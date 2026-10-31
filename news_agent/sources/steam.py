# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

import re
import urllib3

import requests

from .base import NewsItem
from ..network import request_get

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

API_URL = "https://store.steampowered.com/search/results/?specials=1&filter=topsellers&cc=cn&l=schinese&json=1"


def _clean(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _appid_from_logo(logo):
    m = re.search(r"/apps/(\d+)/", logo or "")
    return m.group(1) if m else ""


def fetch_steam_specials(proxy=None, max_items=20, timeout=25):
    resp = None
    try:
        resp = request_get(
            API_URL,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0"},
            proxy=proxy,
            verify=False,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []
    finally:
        if resp is not None:
            resp.close()
    items = []
    for entry in data.get("items", [])[:max_items]:
        name = _clean(entry.get("name", ""))
        appid = _appid_from_logo(entry.get("logo", ""))
        if not name or not appid:
            continue
        url = f"https://store.steampowered.com/app/{appid}"
        items.append(
            NewsItem(
                title=f"[Steam特惠] {name}",
                url=url,
                source="Steam特惠",
                desc=f"Steam 特惠中，点击查看折扣与价格",
            )
        )
    return items
