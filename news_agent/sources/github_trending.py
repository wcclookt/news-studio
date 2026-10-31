# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

from datetime import datetime, timezone

import requests
import urllib3
from bs4 import BeautifulSoup

from .base import NewsItem
from ..network import request_get

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def fetch_github_trending(proxy=None, language="", since="daily", max_items=20, timeout=25):
    url = "https://github.com/trending"
    if language:
        url += "/" + language
    url += f"?since={since}"
    resp = None
    try:
        resp = request_get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=timeout,
            proxy=proxy,
            verify=False,
        )
        resp.raise_for_status()
    except Exception:
        return []
    finally:
        if resp is not None:
            resp.close()
    soup = BeautifulSoup(resp.text, "lxml")
    items = []
    for article in soup.select("article.Box-row"):
        h = article.select_one("h2 a")
        if not h:
            continue
        name = h.get("href", "").strip("/")
        desc_el = article.select_one("p")
        desc = desc_el.get_text(strip=True) if desc_el else ""
        stars_el = article.select_one('a[href*="/stargazers"]')
        stars_text = stars_el.get_text(strip=True) if stars_el else "0"
        stars_text = stars_text.replace(",", "")
        try:
            stars = int(stars_text)
        except ValueError:
            stars = 0
        today_star_el = article.select_one("span.d-inline-block.float-sm-right")
        today_star = today_star_el.get_text(strip=True) if today_star_el else ""
        items.append(
            NewsItem(
                title=name,
                url=f"https://github.com/{name}",
                source="GitHub Trending",
                desc=f"{desc} | ⭐{stars:,} | 今日+{today_star}".strip(" |"),
                # Trending 页面没有项目新闻发布时间；留空，避免把抓取时间伪装成发布时间。
                published_at="",
            )
        )
        if len(items) >= max_items:
            break
    return items
