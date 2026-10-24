import requests


def session(proxy=None, timeout=20):
    s = requests.Session()
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        }
    )
    return s


def get_json(url, proxy=None, timeout=20):
    with session(proxy, timeout) as client:
        with client.get(url, timeout=timeout) as response:
            response.raise_for_status()
            return response.json()


def get_text(url, proxy=None, timeout=20):
    with session(proxy, timeout) as client:
        with client.get(url, timeout=timeout) as response:
            response.raise_for_status()
            response.encoding = response.apparent_encoding or response.encoding
            return response.text


def dedup_by_key(items, keyfunc):
    seen = set()
    result = []
    for item in items:
        k = keyfunc(item)
        if k in seen:
            continue
        seen.add(k)
        result.append(item)
    return result
