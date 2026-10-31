# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

"""HTML/embedded text extraction and bounded transport; no real HTTP/model calls."""

import json
import unittest
from unittest.mock import MagicMock, Mock, patch

import requests

from news_agent.config import Config
from news_agent.link_reader import (
    LinkResult, _decode_js_literal, extract_article, extract_urls, read_link,
)
from news_agent.qq_bot import QQBot


def body_text():
    return '这是文章的实际正文，保留事件细节和适用条件。' * 12


class ArticleExtractionTests(unittest.TestCase):
    def test_cctv_contentdate_is_read_before_scripts_are_removed(self):
        body = '<p>' + body_text() + '</p><p>结尾三个方法：方法甲、方法乙、方法丙。</p>'
        markup = '<html><title>测试新闻</title><div id="content_area">正在加载</div><script>var contentdate = ' + json.dumps(body, ensure_ascii=False) + ';</script></html>'
        result = extract_article(markup, 'https://news.cctv.com/2026/article.shtml')
        self.assertEqual(result.state, 'body')
        self.assertEqual(result.method, '央视内嵌正文')
        self.assertIn('方法丙', result.text)
        self.assertNotIn('正在加载', result.text)

    def test_cctv_escaped_quotes_unicode_and_newlines_are_decoded_without_eval(self):
        literal = r"'<p>It\'s a report. \u65b0\u95fb\n'quoted'\x21</p>'"
        self.assertEqual(_decode_js_literal(literal), "<p>It's a report. 新闻\n'quoted'!</p>")
        text = "var contentdate = '<p>It\\'s official.</p><p>" + body_text() + "</p>';"
        result = extract_article('<title>T</title><script>' + text + '</script>', 'https://news.cctv.com/a')
        self.assertIn("It's official.", result.text)

    def test_arbitrary_page_script_is_never_executed_or_claimed_as_article(self):
        markup = '<title>T</title><script>var contentdate="' + body_text() + '"; dangerous();</script>'
        self.assertEqual(extract_article(markup, 'https://example.com/a').state, 'title_only')

    def test_cctv_dynamic_expression_is_not_evaluated(self):
        markup = '<title>T</title><script>var contentdate="first" + dangerous();</script>'
        self.assertEqual(extract_article(markup, 'https://news.cctv.com/a').state, 'title_only')

    def test_jsonld_article_body_and_next_data_are_supported(self):
        for attrs in ('type="application/ld+json"', 'id="__NEXT_DATA__" type="application/json"'):
            value = {'props': {'pageProps': {'article': {'headline': '测试新闻', 'articleBody': body_text()}}}}
            markup = '<title>测试新闻</title><script ' + attrs + '>' + json.dumps(value) + '</script>'
            result = extract_article(markup, 'https://example.com/a')
            self.assertEqual(result.state, 'body')
            self.assertEqual(result.text, body_text())

    def test_related_story_jsonld_is_not_substituted_for_requested_article(self):
        value = {'headline': '完全不同的一条推荐新闻', 'url': 'https://example.com/other', 'articleBody': body_text()}
        markup = '<title>当前新闻</title><script type="application/ld+json">' + json.dumps(value) + '</script>'
        self.assertEqual(extract_article(markup, 'https://example.com/current').state, 'title_only')

    def test_article_container_ignores_menu_footer_scripts_and_loading(self):
        markup = '<title>新闻</title><nav>' + body_text()*3 + '</nav><article><p>' + body_text() + '</p><p>正在加载</p></article><footer>隐私声明</footer>'
        result = extract_article(markup)
        self.assertEqual(result.text, body_text())
        self.assertNotIn('隐私声明', result.text)

    def test_metadata_is_labeled_partial_not_full_article(self):
        result = extract_article('<title>新闻</title><meta name="description" content="这只是页面的简介。"><main>正在加载</main>')
        self.assertEqual(result.state, 'partial')
        self.assertIn('仅取得简介', result.for_model())
        self.assertIn('不能声称已读全文', result.for_model())

    def test_title_only_page_does_not_treat_loading_as_body(self):
        result = extract_article('<title>新闻</title><div>正在加载</div>')
        self.assertEqual(result.state, 'title_only')
        self.assertEqual(result.text, '')

    def test_login_preview_is_not_reported_as_a_complete_read(self):
        result = extract_article('<title>新闻</title><article>' + body_text() + '登录后阅读全文</article>')
        self.assertEqual(result.state, 'partial')
        self.assertIn('登录', result.message)

    def test_js_unicode_surrogate_pairs_produce_real_characters(self):
        self.assertEqual(_decode_js_literal(r"'\uD83D\uDE0A'"), '😊')

    def test_long_body_has_explicit_truncation_and_default_is_not_1200(self):
        result = LinkResult('body', text='正' * 7000)
        content = result.for_model()
        self.assertTrue(content.truncated)
        self.assertIn('7000 字符，本次提供 6000', content)
        self.assertIn('不是全文', content)
        self.assertEqual(result.for_model(1000).count('正'), 1000 + 2)

    def test_urls_decode_ampersands_strip_prose_and_deduplicate(self):
        self.assertEqual(extract_urls('看（https://example.com/a?id=1&amp;x=2）。再看 https://example.com/a?id=1&x=2'),
                         ['https://example.com/a?id=1&x=2'])

    def test_balanced_parentheses_inside_url_are_preserved(self):
        self.assertEqual(extract_urls('读 https://example.com/Foo_(bar) 和（https://example.com/next）'),
                         ['https://example.com/Foo_(bar)', 'https://example.com/next'])


class LinkTransportTests(unittest.TestCase):
    def response(self, code=200, media='text/html', text=None, **headers):
        response = MagicMock()
        response.status_code = code
        response.headers = {'Content-Type': media, **headers}
        response.iter_content.return_value = [(text or '<article>' + body_text() + '</article>').encode()]
        return response

    def test_configured_proxy_fallback_shared_transport_and_response_close(self):
        response = self.response()
        with patch('news_agent.link_reader._public_url', return_value=True), \
                patch('news_agent.link_reader.request_get', return_value=response) as request:
            result = read_link('https://example.com/a', proxy='http://localhost:7890')
        self.assertEqual(result.state, 'body')
        self.assertEqual(request.call_args.kwargs['proxy'], 'http://localhost:7890')
        self.assertFalse(request.call_args.kwargs['allow_redirects'])
        self.assertTrue(request.call_args.kwargs['stream'])
        response.close.assert_called_once()

    def test_short_link_redirect_is_followed_and_every_target_is_checked(self):
        first = self.response(302, Location='/article')
        second = self.response()
        with patch('news_agent.link_reader._public_url', return_value=True) as check, \
                patch('news_agent.link_reader.request_get', side_effect=[first, second]) as get:
            self.assertEqual(read_link('https://example.com/short').state, 'body')
        self.assertEqual([call.args[0] for call in check.call_args_list], ['https://example.com/short', 'https://example.com/article'])
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_redirect_to_local_address_is_rejected(self):
        response = self.response(302, Location='http://127.0.0.1/private')
        with patch('news_agent.link_reader._public_url', side_effect=[True, False]), \
                patch('news_agent.link_reader.request_get', return_value=response) as request:
            self.assertEqual(read_link('https://example.com/a').state, 'error')
        request.assert_called_once()

    def test_public_url_rejects_loopback_private_credentials_and_invalid_ports(self):
        from news_agent.link_reader import _public_url
        with patch('news_agent.link_reader.socket.getaddrinfo', return_value=[(2,1,6,'',('127.0.0.1',443))]):
            self.assertFalse(_public_url('https://public-name.test/a'))
        for value in ('file:///x', 'http://user:key@example.com', 'https://example.com:70000'):
            self.assertFalse(_public_url(value))

    def test_http_access_denial_returns_reason_without_guessing_content(self):
        for code in (401,403,429):
            response = self.response(code)
            with patch('news_agent.link_reader._public_url', return_value=True), \
                    patch('news_agent.link_reader.request_get', return_value=response):
                result = read_link('https://example.com/a')
            self.assertEqual(result.state, 'blocked')
            self.assertIn(str(code), result.message)
            response.iter_content.assert_not_called()

    def test_unsupported_file_and_oversized_download_are_not_treated_as_text(self):
        for response in (self.response(media='application/pdf'), self.response(text='x'*1000)):
            with patch('news_agent.link_reader._public_url', return_value=True), \
                    patch('news_agent.link_reader.request_get', return_value=response), \
                    patch('news_agent.link_reader.MAX_BYTES', 500):
                result = read_link('https://example.com/a')
            self.assertIn(result.state, {'unsupported','error'})
            response.close.assert_called_once()

    def test_network_exception_does_not_echo_token_or_private_url(self):
        with patch('news_agent.link_reader._public_url', return_value=True), \
                patch('news_agent.link_reader.request_get', side_effect=requests.ConnectionError('private-token private-url')):
            result = read_link('https://example.com/a')
        self.assertNotIn('private-token', result.message)
        self.assertNotIn('private-url', result.message)


class LinkBotIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.bot = QQBot(Config({'push': {'qq': {}}, 'proxy': 'http://localhost:7890'}))

    def test_fetch_provides_read_status_and_uses_configured_network_route(self):
        with patch('news_agent.qq_bot.read_link', return_value=LinkResult('body', text=body_text())) as read:
            content = self.bot._fetch_url_content('https://example.com/a')
        read.assert_called_once_with('https://example.com/a', proxy='http://localhost:7890')
        self.assertEqual(content.state, 'body')
        self.assertIn('已提取正文', content)

    def test_failed_and_partial_links_are_cached_for_only_thirty_seconds(self):
        content = LinkResult('partial', title='新闻', text='片段').for_model()
        with patch.object(self.bot, '_fetch_url_content', return_value=content) as fetch, \
                patch('news_agent.qq_bot.time.monotonic', return_value=100):
            self.bot._cached_url_content('https://example.com/a')
        with patch.object(self.bot, '_fetch_url_content', return_value=content) as again, \
                patch('news_agent.qq_bot.time.monotonic', return_value=131):
            self.bot._cached_url_content('https://example.com/a')
        again.assert_called_once()

    def test_reply_gets_deterministic_partial_read_disclosure(self):
        model = Mock()
        model.complete.return_value = '根据片段做解读。'
        with patch.object(self.bot, '_cached_url_content', return_value=LinkResult('title_only', title='新闻').for_model()), \
                patch.object(self.bot, '_get_chat_client', return_value=model), \
                patch.object(self.bot, '_reply') as reply:
            self.bot._handle_message({'message_id': 1, 'user_id': '10001', 'message': '解读 https://example.com/a'})
        self.assertIn('未能读取正文', reply.call_args.args[-1])
        self.assertIn('仅取得标题', model.complete.call_args.args[1])

    def test_qq_json_share_card_extracts_article_url_not_preview_image(self):
        card = {'meta': {'detail_1': {'title': '新闻', 'preview': 'https://image.test/a.jpg', 'qqdocurl': 'https://news.test/article'}}}
        text, quote = self.bot._extract_text([{'type':'json','data':{'data':json.dumps(card)}}])
        self.assertEqual(text, 'https://news.test/article')
        self.assertIsNone(quote)


if __name__ == '__main__':
    unittest.main()
