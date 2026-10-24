"""Network route tests use mocked HTTP only, never live proxies or websites."""

import unittest
from unittest.mock import MagicMock, patch

import requests

from news_agent import network


def routes():
    return [
        {'route': 'configured', 'route_label': '配置代理', 'proxy': 'http://bad:26561'},
        {'route': 'direct', 'route_label': '直连', 'proxy': None},
    ], False


class RouteTests(unittest.TestCase):
    def setUp(self):
        network.clear_route_cache()

    def tearDown(self):
        network.clear_route_cache()

    def test_bad_proxy_then_direct_success_and_success_route_cache(self):
        response = MagicMock(status_code=200)
        with patch.object(network, 'proxy_routes', return_value=routes()), patch.object(network, 'route_get', side_effect=[requests.exceptions.ProxyError('secret'), response, response]) as get:
            self.assertIs(network.request_get('https://github.com/trending', proxy='http://bad:26561', timeout=25), response)
            self.assertIs(network.request_get('https://github.com/trending?since=weekly', proxy='http://bad:26561', timeout=25), response)
        self.assertEqual([call.args[1] for call in get.call_args_list], ['http://bad:26561', None, None])
        self.assertTrue(all(call.kwargs['timeout'][0] <= 3 for call in get.call_args_list))

    def test_cache_expires_and_returns_to_configured_first(self):
        response = MagicMock(status_code=200)
        with patch.object(network, 'proxy_routes', return_value=routes()), patch.object(network, 'route_get', side_effect=[requests.ConnectionError(), response, requests.ConnectionError(), response]) as get, patch.object(network, '_CACHE_TTL', -1):
            network.request_get('https://github.com/trending', proxy='http://bad:26561')
            network.request_get('https://github.com/trending', proxy='http://bad:26561')
        self.assertEqual([call.args[1] for call in get.call_args_list], ['http://bad:26561', None, 'http://bad:26561', None])

    def test_http_errors_do_not_fallback(self):
        for code in (401, 403, 429, 500):
            with self.subTest(code=code), patch.object(network, 'proxy_routes', return_value=routes()), patch.object(network, 'route_get', return_value=MagicMock(status_code=code)) as get:
                self.assertEqual(network.request_get('https://github.com/trending', proxy='http://bad:26561').status_code, code)
                get.assert_called_once()

    def test_direct_request_disables_environment_and_closes_session(self):
        with patch.object(network.requests, 'Session') as factory:
            session = factory.return_value.__enter__.return_value
            session.get.return_value = MagicMock(status_code=200)
            network.route_get('https://github.com/trending', None, timeout=(2, 3), verify=True)
        self.assertFalse(session.trust_env)
        self.assertEqual(session.get.call_args.kwargs['proxies'], {'http': '', 'https': '', 'all': ''})
        self.assertTrue(session.get.call_args.kwargs['verify'])
        factory.return_value.__exit__.assert_called_once()

    def test_ambient_route_order_deduplication_and_windows_proxy(self):
        with patch.object(network, 'getproxies_environment', return_value={'https': 'http://env'}), patch.object(network, 'windows_proxies', return_value=({'https': 'http://windows'}, True)), patch.object(network.requests.utils, 'get_environ_proxies', return_value={'https': 'http://env'}):
            configured, pac = network.proxy_routes('https://github.com/trending', 'http://bad')
            ambient, _ = network.proxy_routes('https://github.com/trending')
            same, _ = network.proxy_routes('https://github.com/trending', 'http://env')
        self.assertTrue(pac)
        self.assertEqual([route['route'] for route in configured], ['configured', 'environment', 'system', 'direct'])
        self.assertEqual([route['route'] for route in ambient], ['environment', 'system', 'direct'])
        self.assertEqual([route['route'] for route in same], ['configured', 'system', 'direct'])

    def test_no_proxy_default_direct_is_preserved(self):
        with patch.object(network, 'getproxies_environment', return_value={'https': 'http://env', 'no': 'github.com'}), patch.object(network, 'windows_proxies', return_value=({}, False)), patch.object(network.requests.utils, 'get_environ_proxies', return_value={}):
            candidates, _ = network.proxy_routes('https://github.com/trending')
        self.assertEqual(candidates[0]['route'], 'direct')

    def test_failed_cached_route_can_recover_to_configured(self):
        response = MagicMock(status_code=200)
        with patch.object(network, 'proxy_routes', return_value=routes()), patch.object(network, 'route_get', side_effect=[requests.ConnectionError(), response, requests.Timeout(), response]) as get:
            network.request_get('https://github.com/trending', proxy='http://bad:26561')
            network.request_get('https://github.com/trending', proxy='http://bad:26561')
        self.assertEqual([call.args[1] for call in get.call_args_list], ['http://bad:26561', None, None, 'http://bad:26561'])

    def test_github_source_recovers_from_bad_proxy_with_real_shared_route_logic(self):
        from news_agent.sources.github_trending import fetch_github_trending
        response = MagicMock(status_code=200)
        response.text = '<article class="Box-row"><h2><a href="/openai/project">project</a></h2></article>'
        with patch.object(network, 'proxy_routes', return_value=routes()), patch.object(network, 'route_get', side_effect=[requests.exceptions.ProxyError('bad'), response]) as get:
            items = fetch_github_trending(proxy='http://bad:26561')
        self.assertEqual(items[0].title, 'openai/project')
        self.assertEqual([call.args[1] for call in get.call_args_list], ['http://bad:26561', None])
        self.assertFalse(get.call_args.kwargs['verify'])
        response.close.assert_called_once()

    def test_steam_source_recovers_from_bad_proxy_with_real_shared_route_logic(self):
        from news_agent.sources.steam import fetch_steam_specials
        response = MagicMock(status_code=200)
        response.json.return_value = {'items': [{'name': 'Portal', 'logo': 'https://cdn.example/apps/400/logo.jpg'}]}
        with patch.object(network, 'proxy_routes', return_value=routes()), patch.object(network, 'route_get', side_effect=[requests.exceptions.ProxyError('bad'), response]) as get:
            items = fetch_steam_specials(proxy='http://bad:26561')
        self.assertEqual(items[0].url, 'https://store.steampowered.com/app/400')
        self.assertEqual([call.args[1] for call in get.call_args_list], ['http://bad:26561', None])
        self.assertFalse(get.call_args.kwargs['verify'])
        response.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
