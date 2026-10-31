# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import time
from datetime import datetime, timezone

import requests

from .base import NewsItem

API = "https://hacker-news.firebaseio.com/v0"


def _fetch_item(item_id, proxy=None, timeout=20):
    try:
        resp = requests.get(f"{API}/item/{item_id}.json", timeout=timeout,
                            verify=False, proxies={"http": proxy, "https": proxy} if proxy else None)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def fetch_hackernews(proxy=None, max_items=25, timeout=25):
    try:
        resp = requests.get(f"{API}/topstories.json", timeout=timeout,
                            verify=False, proxies={"http": proxy, "https": proxy} if proxy else None)
        resp.raise_for_status()
        ids = resp.json()[:max_items * 2]
    except Exception:
        return []
    items = []
    for i in ids:
        data = _fetch_item(i, proxy, timeout)
        if not data or data.get("type") != "story" or not data.get("title"):
            continue
        url = data.get("url") or f"https://news.ycombinator.com/item?id={data.get('id')}"
        ts = data.get("time", 0)
        pub = datetime.fromtimestamp(ts, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S") if ts else ""
        items.append(
            NewsItem(
                title=data["title"],
                url=url,
                source="Hacker News",
                desc=f"HN #{data.get('score', 0)}分 | {data.get('type', '')} | by {data.get('by', '')}",
                published_at=pub,
            )
        )
        if len(items) >= max_items:
            break
        time.sleep(0.1)
    return items
