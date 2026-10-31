# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

import concurrent.futures

from ..logger import get_logger
from . import arxiv, github_trending, hackernews, rss, steam
from .common import dedup_by_key

log = get_logger()

DEFAULT_RSS = [
    {"name": "IT之家", "url": "https://www.ithome.com/rss/"},
    {"name": "少数派", "url": "https://sspai.com/feed"},
    {"name": "爱范儿", "url": "https://www.ifanr.com/feed"},
    {"name": "CNBeta", "url": "https://rsshub.rssforever.com/cnbeta"},
    {"name": "机核网(游戏)", "url": "https://www.gcores.com/rss"},
    {"name": "Nature", "url": "https://www.nature.com/nature.rss"},
    {"name": "Science", "url": "https://www.science.org/rss/news_current.xml"},
    {"name": "ScienceDaily", "url": "https://www.sciencedaily.com/rss/top/science.xml"},
    {"name": "Phys.org", "url": "https://phys.org/rss-feed/"},
    {"name": "Space.com", "url": "https://www.space.com/feeds/all"},
    {"name": "NewScientist", "url": "https://www.newscientist.com/feed/home"},
    {"name": "ArsTechnica", "url": "https://feeds.arstechnica.com/arstechnica/index"},
]


def _collect_rss(cfg):
    sources = cfg.get("sources.rss", []) or DEFAULT_RSS
    proxy = cfg.proxy()
    max_items = cfg.get("sources.rss_max_per_source", 12)
    all_items = []
    for s in sources:
        try:
            items = rss.fetch_rss_feed(
                s.get("url"),
                s.get("name", "RSS"),
                proxy=proxy,
                max_items=max_items,
                via_proxy=s.get("proxy", False),
            )
            all_items.extend(items)
            log.info("RSS[%s] 抓到 %d 条", s.get("name"), len(items))
        except Exception as e:
            log.warning("RSS[%s] 采集失败: %s", s.get("name"), e)
    return all_items


def _collect_steam(cfg):
    st = cfg.get("sources.steam", {}) or {}
    if not st.get("enabled", True):
        return []
    try:
        items = steam.fetch_steam_specials(proxy=cfg.proxy(), max_items=st.get("max_items", 15))
        log.info("Steam特惠 抓到 %d 条", len(items))
        return items
    except Exception as e:
        log.warning("Steam特惠 采集失败: %s", e)
        return []


def _collect_github(cfg):
    g = cfg.get("sources.github_trending", {}) or {}
    if not g.get("enabled", True):
        return []
    try:
        items = github_trending.fetch_github_trending(
            proxy=cfg.proxy(),
            language=g.get("language", ""),
            since=g.get("since", "daily"),
            max_items=g.get("max_items", 20),
        )
        log.info("GitHub Trending 抓到 %d 条", len(items))
        return items
    except Exception as e:
        log.warning("GitHub Trending 采集失败: %s", e)
        return []


def _collect_hackernews(cfg):
    h = cfg.get("sources.hackernews", {}) or {}
    if not h.get("enabled", True):
        return []
    try:
        items = hackernews.fetch_hackernews(proxy=cfg.proxy(), max_items=h.get("max_items", 25))
        log.info("Hacker News 抓到 %d 条", len(items))
        return items
    except Exception as e:
        log.warning("Hacker News 采集失败: %s", e)
        return []


def _collect_arxiv(cfg):
    a = cfg.get("sources.arxiv", {}) or {}
    if not a.get("enabled", True):
        return []
    try:
        items = arxiv.fetch_arxiv(
            a.get("categories", ["cs.AI", "cs.CL", "cs.LG"]),
            proxy=cfg.proxy(),
            max_items=a.get("max_items", 12),
        )
        log.info("arXiv 抓到 %d 条", len(items))
        return items
    except Exception as e:
        log.warning("arXiv 采集失败: %s", e)
        return []


def collect_all(cfg):
    tasks = [_collect_rss, _collect_steam, _collect_github, _collect_hackernews, _collect_arxiv]
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(t, cfg): t.__name__ for t in tasks}
        items = []
        for f in concurrent.futures.as_completed(futures):
            try:
                items.extend(f.result())
            except Exception as e:
                log.warning("%s 异常: %s", futures[f], e)
    total = dedup_by_key(items, lambda x: x.url)
    log.info("采集完成，共 %d 条去重条目", len(total))
    return total
