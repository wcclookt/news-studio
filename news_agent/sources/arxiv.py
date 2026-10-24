import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import time
import urllib.parse

import requests

from .base import NewsItem

BASE = "http://export.arxiv.org/api/query"


def fetch_arxiv(categories, proxy=None, max_items=12, timeout=30, sort="submittedDate"):
    query = ' OR '.join(f"cat:{c}" for c in categories)
    params = {
        "search_query": f"({query})",
        "start": 0,
        "max_results": max_items * 2,
        "sortBy": sort,
        "sortOrder": "descending",
    }
    try:
        resp = requests.get(BASE, params=params, timeout=timeout,
                            verify=False, proxies={"http": proxy, "https": proxy} if proxy else None)
        resp.raise_for_status()
    except Exception:
        return []
    import xml.etree.ElementTree as ET
    ns = {"a": "http://www.w3.org/2005/Atom"}
    items = []
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError:
        return []
    for entry in root.findall("a:entry", ns):
        title = " ".join((entry.findtext("a:title", "", ns) or "").split())
        link_el = entry.find("a:id", ns)
        url = link_el.text if link_el is not None else ""
        summary = " ".join((entry.findtext("a:summary", "", ns) or "").split())[:400]
        pub = entry.findtext("a:published", "", ns)
        if pub:
            pub = pub.replace("Z", "+00:00")
        author_el = entry.find("a:author/a:name", ns)
        author = author_el.text if author_el is not None else ""
        if not title or not url:
            continue
        items.append(
            NewsItem(
                title=title[:200],
                url=url,
                source="arXiv",
                desc=f"{author} | {summary}",
                published_at=pub,
            )
        )
        if len(items) >= max_items:
            break
    return items
