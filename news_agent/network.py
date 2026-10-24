"""Explicit, ambient and direct routes shared by diagnostics and collection.

Only connection failures trigger another route. An HTTP denial is returned to
the caller unchanged. Proxy addresses are never written to logs or UI results.
"""

from __future__ import annotations

import os
import threading
import time
from urllib.parse import urlparse
from urllib.request import getproxies_environment

import requests


_ROUTE_CACHE = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 120


def clear_route_cache():
    with _CACHE_LOCK:
        _ROUTE_CACHE.clear()


def windows_proxies():
    """Read current-user WinINET static proxies and PAC presence without edits."""
    if os.name != 'nt':
        return {}, False
    proxies, pac = {}, False
    try:
        from urllib.request import getproxies_registry
        proxies = getproxies_registry()
    except (ImportError, OSError, ValueError):
        pass
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as settings:
            pac = bool(winreg.QueryValueEx(settings, 'AutoConfigURL')[0])
    except (ImportError, OSError, ValueError):
        pass
    return proxies, pac


def _proxy_for(proxies, url):
    return proxies.get(urlparse(url).scheme) or proxies.get('all') or None


def proxy_routes(url, proxy=None):
    """Return distinct configured, environment/WinINET and real direct routes.

    The first route mirrors Requests' default environment/NO_PROXY/WinINET
    selection when no proxy is explicitly configured. Other routes are named
    alternatives so diagnostics can explain browser versus Python differences.
    Raw proxy values are internal request arguments and must never be displayed.
    """
    configured = str(proxy or '').strip() or None
    environment = getproxies_environment()
    windows, pac = windows_proxies()
    routes = []

    def append(name, label, address):
        if address and not any(route['proxy'] == address for route in routes):
            routes.append({'route': name, 'route_label': label, 'proxy': address})

    if configured:
        append('configured', '配置代理', configured)
    try:
        ambient = requests.utils.get_environ_proxies(url)
    except (OSError, ValueError):
        ambient = {}
    ambient_proxy = _proxy_for(ambient, url)
    environment_proxy = _proxy_for(environment, url)
    windows_proxy = _proxy_for(windows, url)
    if not configured and ambient_proxy:
        is_environment = environment_proxy == ambient_proxy
        append('environment' if is_environment else 'system',
               '环境代理' if is_environment else 'Windows 系统代理', ambient_proxy)
    if not routes:
        routes.append({'route': 'direct', 'route_label': '直连（含系统 VPN / DNS 加速路线）', 'proxy': None})
    append('environment', '环境代理', environment_proxy)
    append('system', 'Windows 系统代理', windows_proxy)
    if not any(route['route'] == 'direct' for route in routes):
        routes.append({'route': 'direct', 'route_label': '直连（含系统 VPN / DNS 加速路线）', 'proxy': None})
    return routes, pac


def _request_timeout(timeout, remaining):
    if isinstance(timeout, tuple):
        connect, read = timeout
    else:
        connect = read = timeout
    connect = float(connect or 3)
    read = float(read or 8)
    # Total route budget is 15 seconds; a stalled route must leave time for a
    # working alternative. Never increase a caller's shorter timeout.
    return (max(.05, min(connect, 3, remaining / 2)),
            max(.05, min(read, 6, remaining / 2)))


def route_get(url, proxy=None, **kwargs):
    """Execute one exact route, with no environment proxy or netrc blending."""
    with requests.Session() as session:
        session.trust_env = False
        return session.get(url,
                           proxies={'http': proxy or '', 'https': proxy or '', 'all': ''},
                           **kwargs)


def request_get(url, proxy=None, **kwargs):
    """GET via bounded automatic routes, retaining the caller's TLS policy.

    A successful alternate route is preferred for 120 seconds per host and
    proxy settings, avoiding repeated failures on a stale localhost port.
    No fallback follows HTTP errors such as authentication/rate-limit denials.
    """
    routes, _ = proxy_routes(url, proxy)
    parsed = urlparse(url)
    key = (parsed.scheme, parsed.netloc, tuple(route['proxy'] for route in routes))
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _ROUTE_CACHE.get(key)
        if cached and cached[1] > now:
            preferred = cached[0]
            routes = sorted(routes, key=lambda route: route['route'] != preferred)
        elif cached:
            _ROUTE_CACHE.pop(key, None)
    arguments = dict(kwargs)
    requested_timeout = arguments.pop('timeout', 15)
    # Route selection belongs here. Avoid caller/environment proxy blending.
    arguments.pop('proxies', None)
    deadline = now + 15
    last_error = None
    for route in routes[:4]:
        remaining = deadline - time.monotonic()
        if remaining <= .1:
            break
        try:
            response = route_get(url, route['proxy'],
                                 timeout=_request_timeout(requested_timeout, remaining), **arguments)
            # Response bodies are already consumed unless stream=True. Session
            # cleanup mirrors requests.get, and caller owns response.close().
            if 200 <= response.status_code < 400:
                with _CACHE_LOCK:
                    # Keep the small transient cache bounded in long-lived bots.
                    if len(_ROUTE_CACHE) > 128:
                        _ROUTE_CACHE.clear()
                    _ROUTE_CACHE[key] = (route['route'], time.monotonic() + _CACHE_TTL)
            return response
        except (requests.ConnectionError, requests.Timeout, requests.exceptions.InvalidProxyURL,
                requests.exceptions.InvalidSchema, requests.exceptions.MissingSchema) as exc:
            last_error = exc
            with _CACHE_LOCK:
                _ROUTE_CACHE.pop(key, None)
    if last_error is not None:
        raise last_error
    raise requests.Timeout('Network route time budget exhausted')
