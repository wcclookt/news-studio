# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

"""Call-count and context regressions; no API requests or QQ sends occur."""

import json
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import MagicMock, Mock, patch

import requests

from news_agent.config import Config
from news_agent.llm import LLMError
from news_agent.orchestrator import run_daily, run_realtime
from news_agent.processor import _build_user_prompt, _process_batch
from news_agent.qq_bot import QQBot
from news_agent.report import _generate_overview
from news_agent.sources.base import NewsItem


def news(index=0, **changes):
    return NewsItem(**{"title": f"测试产品 {index} 发布", "url": f"https://news.example/{index}",
                       "source": "官方公告", "desc": "产品新增离线功能，售价未公布。",
                       "published_at": "2026-10-17 08:00:00", **changes})


def model_row(index=0, **changes):
    return {"index": index, "category": "tech_product", "score": 8,
            "utility": 8, "valuable": True, "credibility": "较可信",
            "summary": "产品推出新功能", "comment": "仍需留意正式售价。", **changes}


def config(channels=("qq",)):
    return Config({"push": {"enabled": list(channels), "qq": {}},
                   "scoring": {"max_realtime_per_cycle": 1},
                   "llm": {"primary": {"provider": "custom", "api_key": "fake-key",
                                         "base_url": "https://model.example/v1", "model": "test"}}})


class BatchEfficiencyTests(unittest.TestCase):
    def test_network_failure_does_not_start_second_round_of_provider_retries(self):
        llm = Mock()
        llm.complete.side_effect = LLMError("HTTP 429")
        items = [news(0), news(1)]
        self.assertEqual(_process_batch(llm, items), items)
        llm.complete.assert_called_once()
        self.assertTrue(all(item.score == 3 for item in items))

    def test_partial_json_repairs_only_missing_item_and_preserves_order(self):
        llm = Mock()
        llm.complete.side_effect = [json.dumps([model_row(1)]), json.dumps([model_row(0)])]
        items = [news(0), news(1)]
        limiter = Mock()
        self.assertEqual(_process_batch(llm, items, limiter), items)
        repair = json.loads(llm.complete.call_args_list[1].args[1])
        self.assertEqual(len(repair), 1)
        self.assertEqual(repair[0]["title"], items[0].title)
        self.assertEqual(repair[0]["index"], 0)
        self.assertEqual([item.score for item in items], [8, 8])
        self.assertEqual(limiter.acquire.call_count, 2)

    def test_bad_indices_duplicates_and_non_objects_never_drop_or_overwrite_news(self):
        llm = Mock()
        llm.complete.side_effect = [json.dumps([
            "bad", model_row(-1), model_row(True), model_row(0), model_row(0, score=1),
            model_row("1"), model_row(100), model_row(1, category=[]),
            model_row(1, credibility={}), model_row(1, summary={"bad": "type"}),
        ]), json.dumps([model_row(0, valuable=False, score=2)])]
        items = [news(0), news(1)]
        actual = _process_batch(llm, items)
        self.assertEqual(actual, items)
        self.assertEqual(items[0].score, 8)
        self.assertEqual(items[1].score, 2)
        self.assertFalse(items[1].is_valuable)

    def test_failed_partial_repair_keeps_good_result_and_falls_back_only_missing(self):
        llm = Mock()
        llm.complete.side_effect = [json.dumps([model_row(0)]), LLMError("unavailable")]
        items = [news(0), news(1)]
        self.assertEqual(_process_batch(llm, items), items)
        self.assertEqual([item.score for item in items], [8, 3])

    def test_false_string_is_not_treated_as_a_valuable_item(self):
        llm = Mock()
        llm.complete.return_value = json.dumps([model_row(0, valuable="false")])
        item = news()
        _process_batch(llm, [item])
        self.assertFalse(item.is_valuable)
        llm.complete.assert_called_once()

    def test_prompt_removes_html_repeated_title_and_paragraphs_but_keeps_evidence(self):
        item = news(desc="<p>测试产品 0 发布</p><p>产品新增离线功能。</p><p>产品新增离线功能。</p>")
        prompt = _build_user_prompt([item])
        row = json.loads(prompt)[0]
        self.assertNotIn("<p>", prompt)
        self.assertEqual(prompt.count(item.title), 1)
        self.assertEqual(row["description"], "产品新增离线功能。")
        self.assertEqual(row["host"], "news.example")
        self.assertEqual(row["published"], item.published_at)

    def test_normal_batch_excerpt_budget_does_not_expand_per_article_input(self):
        rows = json.loads(_build_user_prompt([news(index, desc="事实" * 500) for index in range(8)]))
        self.assertTrue(all(len(row["description"]) <= 201 for row in rows))
        self.assertLessEqual(sum(len(row["description"]) for row in rows), 1608)


class DeliveryEfficiencyTests(unittest.TestCase):
    def test_push_limit_still_remembers_every_scored_item_for_future_cycles_and_daily(self):
        items = [news(i, score=9, utility=9, credibility="较可信") for i in range(5)]
        seen = set()
        dedupe, dispatcher = Mock(), Mock()
        dedupe.cache_maintenance.return_value = {'removed': 0}
        dedupe.cache_generation.return_value = 0
        dedupe.is_seen.side_effect = lambda url: url in seen
        dedupe.add_seen.side_effect = lambda url, **fields: seen.add(url)
        dedupe.dedup_same_event.side_effect = lambda batch, **kwargs: batch
        delivered = set()
        dedupe.pending_realtime.side_effect = lambda: [item for item in items if item.url in seen and item.url not in delivered]
        dedupe.realtime_delivered_channels.return_value = set()
        dedupe.mark_pushed.side_effect = lambda url, **kwargs: delivered.add(url)
        dispatcher.send.return_value = (["qq"], [])
        with patch("news_agent.orchestrator.Dedupe", return_value=dedupe), \
                patch("news_agent.orchestrator.collect_all", return_value=items), \
                patch("news_agent.orchestrator.LLMClient"), \
                patch("news_agent.orchestrator.process_items", side_effect=lambda llm, batch, size: batch) as process, \
                patch("news_agent.orchestrator.Dispatcher", return_value=dispatcher):
            self.assertEqual(len(run_realtime(config())["pushed"]), 1)
            self.assertEqual(len(run_realtime(config())["pushed"]), 1)
        self.assertEqual(seen, {item.url for item in items})
        self.assertEqual(dedupe.add_seen.call_count, 5)
        process.assert_called_once()
        self.assertEqual(dispatcher.send.call_count, 2)

    def test_qq_only_daily_reuses_scored_interpretations_with_zero_model_calls(self):
        entries = [("新闻", "https://news.example/1", 8, "tech_product", "", "较可信", 1, "已有解读", 0)]
        dedupe, dispatcher = Mock(), Mock()
        dedupe.period_entries.return_value = entries
        dispatcher.send.return_value = (["qq"], [])
        with patch("news_agent.orchestrator.Dedupe", return_value=dedupe), \
                patch("news_agent.orchestrator.Dispatcher", return_value=dispatcher), \
                patch("news_agent.orchestrator.LLMClient") as factory, \
                patch("news_agent.orchestrator.generate_daily_report") as report:
            run_daily(config())
        factory.assert_not_called()
        report.assert_not_called()
        self.assertIn("已有解读", dispatcher.send.call_args_list[1].args[1])
        dedupe.mark_in_daily.assert_called_once_with([entries[0][1]])

    def test_daily_item_exception_does_not_resend_already_successful_items(self):
        entries = [(f"新闻{i}", f"https://news.example/{i}", 8, "science", "", "较可信", 1, "解读", 0)
                   for i in range(3)]
        dedupe, dispatcher = Mock(), Mock()
        dedupe.period_entries.return_value = entries
        dispatcher.send.side_effect = [(["qq"], []), (["qq"], []), RuntimeError("temporary"), (["qq"], [])]
        with patch("news_agent.orchestrator.Dedupe", return_value=dedupe), \
                patch("news_agent.orchestrator.Dispatcher", return_value=dispatcher):
            run_daily(config())
        self.assertEqual(dispatcher.send.call_count, 4)
        dedupe.mark_in_daily.assert_called_once_with([entries[0][1], entries[2][1]])

    def test_untrusted_news_does_not_reappear_in_daily(self):
        dedupe, dispatcher = Mock(), Mock()
        dedupe.period_entries.return_value = [("谣言", "https://news.example/1", 9, "world", "", "不可信", 1)]
        dispatcher.send.return_value = (["qq"], [])
        with patch("news_agent.orchestrator.Dedupe", return_value=dedupe), \
                patch("news_agent.orchestrator.Dispatcher", return_value=dispatcher), \
                patch("news_agent.orchestrator.LLMClient") as factory:
            run_daily(config())
        factory.assert_not_called()
        self.assertNotIn("谣言", dispatcher.send.call_args.args[1])

    def test_short_overview_caps_at_twelve_and_preserves_existing_uncertainty(self):
        llm = Mock()
        llm.complete.return_value = "速览"
        entries = [(f"新闻{i}", f"url{i}", 10-i/100, "science", "", "存疑", 1, "仍待确认", 0)
                   for i in range(40)]
        self.assertEqual(_generate_overview(llm, entries), "速览")
        prompt = llm.complete.call_args.args[1]
        self.assertEqual(prompt.count("- [存疑]"), 12)
        self.assertIn("仍待确认", prompt)
        self.assertNotIn("新闻12", prompt)

    def test_single_entry_needs_no_redundant_model_overview(self):
        llm = Mock()
        self.assertEqual(_generate_overview(llm, [("一条新闻", "url", 8, "science")]), "")
        llm.complete.assert_not_called()


class ChatEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.bot = QQBot(config())
        self.send = patch.object(self.bot, "_reply").start()
        self.fetch = patch.object(self.bot, "_fetch_url_content", return_value="网页事实").start()
        self.quote = patch.object(self.bot, "_get_replied_text", return_value="").start()
        self.client = Mock()
        self.client.complete.return_value = "回答内容"
        self.factory = patch("news_agent.qq_bot.LLMClient", return_value=self.client).start()
        self.addCleanup(patch.stopall)

    def message(self, text, identifier=1, user="10001", **changes):
        return {"message_id": identifier, "message": text, "user_id": user,
                "self_id": "99999", "message_type": "private", **changes}

    def test_followup_uses_bounded_history_and_reuses_model_client(self):
        self.bot._handle_message(self.message("介绍这个产品", 1))
        self.bot._handle_message(self.message("它适合谁", 2))
        self.assertIn("介绍这个产品", self.client.complete.call_args.args[1])
        self.assertIn("回答内容", self.client.complete.call_args.args[1])
        self.factory.assert_called_once()
        for identifier in range(3, 8):
            self.bot._handle_message(self.message(f"追问{identifier}", identifier))
        turns = next(iter(self.bot._history.values()))[1]
        self.assertEqual(len(turns), 2)
        self.assertLessEqual(sum(len(question)+len(answer) for question, answer in turns), 1920)

    def test_different_users_never_receive_each_others_history(self):
        self.bot._handle_message(self.message("我的私人问题", 1))
        self.bot._handle_message(self.message("另一个人的问题", 2, user="10002"))
        self.assertNotIn("我的私人问题", self.client.complete.call_args.args[1])

    def test_local_help_and_reset_do_not_call_model_or_fetch_quoted_content(self):
        self.bot._handle_message(self.message("帮助", 1))
        self.bot._handle_message(self.message("清空对话", 2))
        self.factory.assert_not_called()
        self.fetch.assert_not_called()
        self.quote.assert_not_called()

    def test_subscription_command_is_handled_before_quote_or_link_fetching(self):
        with patch.object(self.bot, "_subscription_command", return_value="已订阅"):
            self.bot._handle_message(self.message([{"type": "text", "data": {"text": "订阅新闻"}},
                                                   {"type": "reply", "data": {"id": "quoted"}}]))
        self.send.assert_called_once()
        self.quote.assert_not_called()
        self.factory.assert_not_called()

    def test_self_messages_duplicate_events_and_emoji_skip_model(self):
        self.bot._handle_message(self.message("自己发送的回复", 1, user="99999"))
        self.bot._handle_message(self.message("😀😀", 2))
        self.bot._handle_message(self.message("有效问题", 3))
        self.bot._handle_message(self.message("有效问题", 3))
        self.client.complete.assert_called_once()

    def test_concurrent_redelivery_of_same_message_pays_for_one_model_call(self):
        message = self.message("重复事件只处理一次", 11)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: self.bot._handle_message(message), range(20)))
        self.client.complete.assert_called_once()
        self.send.assert_called_once()

    def test_same_user_in_different_groups_has_separate_history(self):
        self.bot._handle_message(self.message("第一群的上下文", 1, message_type="group", group_id="101"))
        self.bot._handle_message(self.message("第二群的问题", 2, message_type="group", group_id="102"))
        self.assertNotIn("第一群", self.client.complete.call_args.args[1])

    def test_duplicate_link_is_fetched_once_and_short_cache_avoids_refetch(self):
        self.bot._handle_message(self.message("解读 https://example.com/article https://example.com/article", 1))
        self.bot._handle_message(self.message("再看 https://example.com/article", 2))
        self.fetch.assert_called_once_with("https://example.com/article")
        self.assertEqual(self.client.complete.call_args.args[1].count("网页事实"), 1)
        self.assertNotIn("最近对话", self.client.complete.call_args.args[1])

    def test_failed_link_is_explicitly_marked_as_unread_for_model(self):
        self.fetch.return_value = ""
        self.bot._handle_message(self.message("解读 https://example.com/article", 1))
        self.assertIn("本次读取失败", self.client.complete.call_args.args[1])

    def test_link_cache_expires_and_refreshes_recent_content(self):
        with patch("news_agent.qq_bot.time.monotonic", return_value=100):
            self.assertEqual(self.bot._cached_url_content("https://example.com/article"), "网页事实")
        with patch("news_agent.qq_bot.time.monotonic", return_value=200):
            self.assertEqual(self.bot._cached_url_content("https://example.com/article"), "网页事实")
        self.fetch.assert_called_once()
        self.fetch.return_value = "更新后的事实"
        with patch("news_agent.qq_bot.time.monotonic", return_value=401):
            self.assertEqual(self.bot._cached_url_content("https://example.com/article"), "更新后的事实")
        self.assertEqual(self.fetch.call_count, 2)

    def test_expired_history_is_not_sent_again(self):
        key = ("private", "", "10001")
        self.bot._history[key] = (0, [("old", "old answer")])
        self.assertEqual(self.bot._history_prompt(key, "现在呢"), "现在呢")
        self.assertNotIn(key, self.bot._history)

    def test_config_change_rebuilds_client_but_unchanged_key_reuses_it(self):
        first = self.bot._get_chat_client()
        self.assertIs(self.bot._get_chat_client(), first)
        self.bot.cfg.data["llm"]["primary"]["model"] = "new-model"
        self.bot._get_chat_client()
        self.assertEqual(self.factory.call_count, 2)


class BotTransportTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config()
        self.cfg.data["push"]["qq"]["access_token"] = "private-token"
        self.bot = QQBot(self.cfg)
        self.session, self.response = MagicMock(), MagicMock()
        self.session.__enter__.return_value = self.session
        self.response.__enter__.return_value = self.response
        self.session.post.return_value = self.response
        self.response.status_code = 200
        self.response.json.return_value = {"status": "ok", "data": {"message": "text"}}

    def test_bot_napcat_call_bypasses_proxy_and_redirects_and_closes_resources(self):
        with patch("news_agent.qq_bot.requests.Session", return_value=self.session):
            self.assertEqual(self.bot._call_http("get_msg", {"message_id": "1"})["status"], "ok")
        self.assertFalse(self.session.trust_env)
        self.session.post.assert_called_once_with(
            "http://127.0.0.1:3000/get_msg", json={"message_id": "1"},
            headers={"Content-Type": "application/json", "Authorization": "Bearer private-token"},
            timeout=(3, 12), allow_redirects=False,
        )
        self.response.raise_for_status.assert_called_once()
        self.response.__exit__.assert_called_once()
        self.session.__exit__.assert_called_once()

    def test_bot_napcat_redirect_is_rejected_without_parsing_body(self):
        self.response.status_code = 307
        with patch("news_agent.qq_bot.requests.Session", return_value=self.session):
            self.assertIsNone(self.bot._call_http("get_msg", {}))
        self.response.json.assert_not_called()

    def test_bot_napcat_error_log_does_not_expose_token_or_url(self):
        self.response.raise_for_status.side_effect = requests.HTTPError("private-token https://private-host")
        with patch("news_agent.qq_bot.requests.Session", return_value=self.session), \
                patch("news_agent.qq_bot.log.warning") as warning:
            self.assertIsNone(self.bot._call_http("get_msg", {}))
        shown = str(warning.call_args)
        self.assertNotIn("private-token", shown)
        self.assertNotIn("private-host", shown)

    def test_bot_napcat_logical_failure_is_not_treated_as_success(self):
        self.response.json.return_value = {"status": "failed", "retcode": 1400, "message": "private-token"}
        with patch("news_agent.qq_bot.requests.Session", return_value=self.session), \
                patch("news_agent.qq_bot.log.warning") as warning:
            self.assertIsNone(self.bot._call_http("get_msg", {}))
        self.assertNotIn("private-token", str(warning.call_args))

    def test_websocket_receives_configured_authorization_header(self):
        def stop(**kwargs):
            self.bot._running = False
        socket = Mock()
        socket.run_forever.side_effect = stop
        with patch("news_agent.qq_bot.websocket.WebSocketApp", return_value=socket) as factory, \
                patch("news_agent.qq_bot.time.sleep"):
            self.bot.run()
        self.assertEqual(factory.call_args.kwargs["header"], {"Authorization": "Bearer private-token"})
        self.assertEqual(socket.run_forever.call_args.kwargs["http_no_proxy"], ["127.0.0.1", "localhost"])


if __name__ == "__main__":
    unittest.main()
