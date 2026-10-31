# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

"""Read public article text without executing page scripts or bypassing gates."""

from dataclasses import dataclass
from difflib import SequenceMatcher
import html
import ipaddress
import json
import re
import socket
import time
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
import requests

from .network import request_get


MAX_BYTES = 2 * 1024 * 1024
MAX_SECONDS = 22
BODY_SELECTORS = (
    '#js_content', '#artibody', '#article_content', '#content_area', '#text_area',
    '[itemprop="articleBody"]', 'article', '.article-content', '.article_content',
    '.articleContent', '.rich_media_content', '.news-content', '.newsContent',
    '.post-content', '.entry-content', '#content_body', '.text_area', 'main',
)
JS_LITERAL = r'''(?P<literal>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')'''
PLACEHOLDER = re.compile(r'^(?:正在加载[.。…\s]*|加载中[.。…\s]*|loading[.\s]*|点击收起全文|展开全文|加载更多)$', re.I)
ACCESS_GATE = re.compile(r'登录后(?:查看|阅读)|订阅后(?:查看|阅读)|购买后(?:查看|阅读)|sign in to (?:read|continue)|subscribe to (?:read|continue)|enable javascript|请开启\s*javascript', re.I)


class LinkContent(str):
    def __new__(cls, value, *, state='body', truncated=False):
        instance = super().__new__(cls, value)
        instance.state = state
        instance.truncated = truncated
        return instance


@dataclass
class LinkResult:
    state: str
    title: str = ''
    text: str = ''
    method: str = ''
    message: str = ''
    url: str = ''

    def for_model(self, max_chars=6000):
        max_chars = max(500, min(int(max_chars), 24000))
        truncated = len(self.text) > max_chars
        body = self.text[:max_chars]
        states = {'body': '已提取正文', 'partial': '仅取得简介或正文片段',
                  'title_only': '仅取得标题，没有取得正文', 'blocked': '页面限制访问，没有取得正文',
                  'unsupported': '当前内容格式不支持', 'error': '本次读取失败，没有取得正文'}
        parts = ['读取状态：' + states[self.state]]
        if self.message:
            parts.append('说明：' + self.message)
        if self.title:
            parts.append('标题：' + self.title[:400])
        if self.text:
            parts.append(f'已提取 {len(self.text)} 字符，本次提供 {len(body)} 字符' + ('（已截断，不是全文）' if truncated else ''))
            parts.append(('正文：' if self.state == 'body' else '可见片段：') + '\n' + body)
        if self.method:
            parts.append('提取方式：' + self.method)
        parts.append('仅据实际提供的内容解读，不把标题、简介、网页主张或来源权威性当作独立事实核验。')
        return LinkContent('\n'.join(parts), state=self.state, truncated=truncated)


def _clean_html(fragment):
    tree = BeautifulSoup(str(fragment or ''), 'lxml')
    for node in tree(['script', 'style', 'nav', 'header', 'footer', 'aside', 'form', 'noscript']):
        node.decompose()
    lines, seen = [], set()
    for value in tree.get_text('\n', strip=True).splitlines():
        value = re.sub(r'\s+', ' ', html.unescape(value)).strip()
        if value and value not in seen and not PLACEHOLDER.fullmatch(value):
            lines.append(value)
            seen.add(value)
    return '\n'.join(lines)


def _decode_js_literal(literal):
    # Decode string escapes only. Never eval/exec JavaScript from a page.
    text = literal[1:-1]
    escapes = {'n': '\n', 'r': '\r', 't': '\t', 'b': '\b', 'f': '\f', 'v': '\v', '0': '\0'}
    def replace(match):
        value = match.group(1)
        if value[:1] in {'u', 'x'} and len(value) > 1:
            return chr(int(value[1:], 16))
        if value in {'\n', '\r\n'}:
            return ''
        return escapes.get(value, value)
    decoded = re.sub(r'\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|\r?\n|.)', replace, text, flags=re.S)
    return decoded.encode('utf-16', errors='surrogatepass').decode('utf-16', errors='replace')


def _json_articles(value, depth=0, title='', url=''):
    if depth > 16:
        return
    if isinstance(value, dict):
        headline = value.get('headline') or value.get('title')
        location = value.get('url') or value.get('@id')
        matches = True
        if isinstance(location, str) and location.startswith(('http://', 'https://')) and url:
            actual, wanted = urlsplit(location), urlsplit(url)
            matches = (actual.hostname, actual.path.rstrip('/')) == (wanted.hostname, wanted.path.rstrip('/'))
        if isinstance(headline, str) and title:
            matches = matches and (headline in title or title in headline or SequenceMatcher(None, title, headline).ratio() >= .65)
        for key in ('articleBody', 'articleContent', 'article_content', 'bodyHtml', 'contentHtml'):
            if matches and isinstance(value.get(key), str):
                yield value[key]
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from _json_articles(child, depth+1, title, url)
    elif isinstance(value, list):
        for child in value[:200]:
            yield from _json_articles(child, depth+1, title, url)


def extract_article(markup, url=''):
    soup = BeautifulSoup(markup, 'lxml')
    title_node = soup.select_one('meta[property="og:title"]')
    title = title_node.get('content', '') if title_node else soup.title.get_text(' ', strip=True) if soup.title else ''
    title = re.sub(r'\s+', ' ', title).strip()
    candidates = []
    host = (urlsplit(url).hostname or '').lower()
    # CCTV injects the article into an initially empty #content_area at runtime.
    # The public HTML already contains a plain string literal called contentdate.
    if host == 'cctv.com' or host.endswith('.cctv.com'):
        expression = re.compile(r'\b(?:var|let|const)\s+contentdate\s*=\s*' + JS_LITERAL, re.S)
        for script in soup.find_all('script', src=False):
            for match in expression.finditer(script.get_text()):
                tail = script.get_text()[match.end():].lstrip()
                if tail.startswith('+'):
                    continue
                body = _clean_html(_decode_js_literal(match.group('literal')))
                if body:
                    candidates.append((body, '央视内嵌正文'))
    for script in soup.find_all('script'):
        if script.get('type', '').lower() not in {'application/ld+json', 'application/json'} and script.get('id') != '__NEXT_DATA__':
            continue
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        for fragment in _json_articles(data, title=title, url=url):
            body = _clean_html(fragment)
            if body:
                candidates.append((body, '页面结构化正文'))
    for node in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'form']):
        node.decompose()
    for selector in BODY_SELECTORS:
        for node in soup.select(selector):
            body = _clean_html(str(node))
            if body and body != title:
                link_text = len(''.join(link.get_text() for link in node.find_all('a')))
                if link_text <= len(body) * .5:
                    candidates.append((body, '文章正文节点'))
    if candidates:
        body, method = max(candidates, key=lambda entry: len(entry[0]))
        if ACCESS_GATE.search(body):
            return LinkResult('partial', title, body, method, '可见页面包含登录、订阅或脚本要求，不能确认已取得完整正文。', url)
        if len(body) >= 100:
            return LinkResult('body', title, body, method, url=url)
        if len(body) >= 20:
            return LinkResult('partial', title, body, method, '页面可见正文较短，可能尚未加载完整。', url)
    # Generic paragraphs are a fallback, not the entire navigation/footer text.
    paragraphs = [_clean_html(str(node)) for node in soup.select('body p')]
    paragraphs = list(dict.fromkeys(value for value in paragraphs if len(value) >= 35))
    if len(paragraphs) >= 2 and len('\n'.join(paragraphs)) >= 200:
        return LinkResult('partial', title, '\n'.join(paragraphs), '可见段落',
                          '未识别到明确文章容器，仅提供实际取得的段落。', url)
    description = soup.select_one('meta[name="description"], meta[property="og:description"]')
    if description and description.get('content', '').strip():
        return LinkResult('partial', title, description['content'].strip(), '页面简介',
                          '仅取得页面简介，不能声称已读全文。', url)
    return LinkResult('title_only' if title else 'error', title=title, url=url,
                      message='页面可能依赖 JavaScript、登录或验证；可粘贴正文让我解读。')


def _public_url(url):
    try:
        parts = urlsplit(url)
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
            return False
        if parts.port is not None and not 1 <= parts.port <= 65535:
            return False
        addresses = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80), type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(entry[4][0]).is_global for entry in addresses)
    except (OSError, ValueError):
        return False


def read_link(url, proxy=None):
    started = time.monotonic()
    current = url
    for _ in range(4):
        if not _public_url(current):
            return LinkResult('error', message='链接无效、无法解析，或不是公开互联网地址。', url=url)
        try:
            response = request_get(current, proxy=proxy, timeout=(3, 6), stream=True,
                                   allow_redirects=False, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36'})
            try:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get('Location')
                    if not location:
                        return LinkResult('error', message='页面跳转缺少目标地址。', url=url)
                    current = urljoin(current, location)
                    if time.monotonic() - started > MAX_SECONDS:
                        break
                    continue
                if response.status_code in {401, 403, 429}:
                    return LinkResult('blocked', message=f'网站返回 HTTP {response.status_code}；可能需要登录、验证或稍后重试。', url=url)
                if response.status_code != 200:
                    return LinkResult('error', message=f'网站返回 HTTP {response.status_code}。', url=url)
                media = response.headers.get('Content-Type', '').split(';', 1)[0].strip().lower()
                if media and media not in {'text/html', 'application/xhtml+xml', 'text/plain'}:
                    return LinkResult('unsupported', message='暂不直接解读 PDF、图片、视频或下载文件，请粘贴文字内容。', url=url)
                content = bytearray()
                for chunk in response.iter_content(16384):
                    content.extend(chunk)
                    if len(content) > MAX_BYTES:
                        return LinkResult('error', message='页面超过读取大小上限，未把不完整下载当成全文。', url=url)
                    if time.monotonic() - started > MAX_SECONDS:
                        return LinkResult('error', message='页面读取超时，可稍后重试或粘贴正文。', url=url)
                if media == 'text/plain':
                    text = bytes(content).decode(response.encoding or 'utf-8', errors='replace').strip()
                    return LinkResult('body' if len(text) >= 100 else 'partial', text=text, method='纯文本', url=current)
                return extract_article(bytes(content), current)
            finally:
                response.close()
        except requests.exceptions.SSLError:
            return LinkResult('error', message='网站证书验证失败，请检查网络和加速器证书设置。', url=url)
        except requests.Timeout:
            return LinkResult('error', message='网络读取超时，可稍后重试。', url=url)
        except Exception:
            return LinkResult('error', message='网络请求未完成，请检查代理或稍后重试。', url=url)
    return LinkResult('error', message='跳转过多或读取超过时间限制。', url=url)


def extract_urls(text):
    # Keep query strings, decode QQ/HTML escaped ampersands, remove prose endings.
    urls = re.findall(r'https?://[^\s<>"\u3000，。；！？、]+', html.unescape(text))
    cleaned = []
    pairs = {')': '(', ']': '[', '}': '{', '）': '（', '】': '【', '》': '《'}
    for value in urls:
        value = value.rstrip(".,;:!?’'")
        while value and value[-1] in pairs and value.count(value[-1]) > value.count(pairs[value[-1]]):
            value = value[:-1]
        cleaned.append(value)
    return list(dict.fromkeys(cleaned))
