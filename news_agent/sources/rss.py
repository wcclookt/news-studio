import re
from datetime import datetime
from email.utils import parsedate_to_datetime

import feedparser
import requests

from .base import NewsItem


def _clean_html(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_time(value):
    if not value:
        return ""
    from datetime import datetime

    s = str(value).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    try:
        if isinstance(value, str):
            dt = parsedate_to_datetime(value)
        else:
            dt = value
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _extract_image(entry):
    for key in ("media_content", "media_thumbnail"):
        media = entry.get(key)
        if media:
            for m in media:
                url = m.get("url")
                if url:
                    return url
    enc = entry.get("enclosures") or []
    for e in enc:
        t = (e.get("type") or "")
        if t.startswith("image"):
            return e.get("href") or e.get("url") or ""
    return ""


def _parse_entries(parsed, source_name, max_items):
    items = []
    for entry in parsed.entries[:max_items]:
        title = _clean_html(entry.get("title", ""))
        link = entry.get("link", "") or entry.get("id", "")
        if not title or not link:
            continue
        desc = _clean_html(entry.get("summary", entry.get("description", "")))
        pub = entry.get("published") or entry.get("updated") or ""
        items.append(
            NewsItem(
                title=title,
                url=link,
                source=source_name,
                desc=desc[:500],
                published_at=_parse_time(pub),
                image=_extract_image(entry),
            )
        )
    return items


def fetch_rss_feed(url, source_name, proxy=None, max_items=15, timeout=25, via_proxy=False):
    try:
        if via_proxy and proxy:
            resp = requests.get(url, timeout=timeout, proxies={"http": proxy, "https": proxy},
                                headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)
        else:
            parsed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0"})
    except Exception:
        return []
    if parsed.bozo and not parsed.entries:
        return []
    return _parse_entries(parsed, source_name, max_items)
