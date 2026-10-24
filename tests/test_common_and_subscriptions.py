"""Timeouts and live subscriptions work without network or actual QQ messages."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import requests

from news_agent.config import Config
from news_agent.push.dispatcher import Dispatcher
from news_agent.qq_bot import QQBot
from news_agent.sources import common
from scripts import manage_qq


def sample_config():
    return {
        '_comment': '保留说明', 'custom_extension': {'keep': 42},
        'push': {'enabled': ['qq'], 'qq': {
            'http_base': 'http://127.0.0.1:3000', 'access_token': 'old-token',
            'private_qq': ['10001'], 'group_id': '20001',
            '_comment_private_qq': '保留接收列表说明',
        }},
        'llm': {'primary': {'model': 'old-model', 'api_key': 'old-api-key'}},
    }


class CommonRequestsTests(unittest.TestCase):
    def test_json_passes_real_request_timeout_and_closes_resources(self):
        client, response = MagicMock(), MagicMock()
        client.__enter__.return_value = client
        response.__enter__.return_value = response
        client.get.return_value = response
        response.json.return_value = {'result': True}
        with patch.object(common, 'session', return_value=client) as factory:
            self.assertEqual(common.get_json('https://example.com', proxy='http://localhost:8888', timeout=4),
                             {'result': True})
        factory.assert_called_once_with('http://localhost:8888', 4)
        client.get.assert_called_once_with('https://example.com', timeout=4)
        response.raise_for_status.assert_called_once()
        response.__exit__.assert_called_once()
        client.__exit__.assert_called_once()

    def test_http_error_still_closes_response_and_session(self):
        client, response = MagicMock(), MagicMock()
        client.__enter__.return_value = client
        response.__enter__.return_value = response
        client.get.return_value = response
        response.raise_for_status.side_effect = requests.HTTPError('failed')
        with patch.object(common, 'session', return_value=client):
            with self.assertRaises(requests.HTTPError):
                common.get_json('https://example.com', timeout=5)
        response.__exit__.assert_called_once()
        client.__exit__.assert_called_once()

    def test_text_timeout_and_encoding_stay_inside_session(self):
        client, response = MagicMock(), MagicMock()
        client.__enter__.return_value = client
        response.__enter__.return_value = response
        client.get.return_value = response
        response.apparent_encoding = 'utf-8'
        response.text = '科技新闻'
        with patch.object(common, 'session', return_value=client):
            self.assertEqual(common.get_text('https://example.com', timeout=7), '科技新闻')
        client.get.assert_called_once_with('https://example.com', timeout=7)
        self.assertEqual(response.encoding, 'utf-8')
        response.__exit__.assert_called_once()
        client.__exit__.assert_called_once()


class ManageFriendsTests(unittest.TestCase):
    def mock_session(self):
        client, response = MagicMock(), MagicMock()
        client.__enter__.return_value = client
        response.__enter__.return_value = response
        client.post.return_value = response
        response.status_code = 200
        response.json.return_value = {'status': 'ok', 'data': [{'user_id': 10001, 'nickname': '朋友'}]}
        return client, response

    def test_friend_request_uses_token_and_bypasses_proxy_and_redirects(self):
        client, response = self.mock_session()
        with patch.object(manage_qq.requests, 'Session', return_value=client):
            result = manage_qq.get_friends(sample_config())
        self.assertEqual(result, [{'user_id': 10001, 'nickname': '朋友'}])
        client.post.assert_called_once_with('http://127.0.0.1:3000/get_friend_list', json={},
                                           headers={'Authorization': 'Bearer old-token'},
                                           timeout=(3, 10), allow_redirects=False)
        self.assertFalse(client.trust_env)
        response.raise_for_status.assert_called_once()
        response.__exit__.assert_called_once()
        client.__exit__.assert_called_once()

    def test_no_token_keeps_optional_auth_behavior(self):
        client, _ = self.mock_session()
        cfg = sample_config()
        cfg['push']['qq']['access_token'] = ''
        with patch.object(manage_qq.requests, 'Session', return_value=client):
            self.assertTrue(manage_qq.get_friends(cfg))
        self.assertEqual(client.post.call_args.kwargs['headers'], {})

    def test_http_error_does_not_print_sensitive_exception(self):
        client, response = self.mock_session()
        response.raise_for_status.side_effect = requests.HTTPError('secret-token https://private-endpoint')
        with patch.object(manage_qq.requests, 'Session', return_value=client), patch('builtins.print') as output:
            self.assertIsNone(manage_qq.get_friends(sample_config()))
        shown = ' '.join(str(call) for call in output.call_args_list)
        self.assertNotIn('secret-token', shown)
        self.assertNotIn('private-endpoint', shown)
        response.json.assert_not_called()
        response.__exit__.assert_called_once()
        client.__exit__.assert_called_once()

    def test_redirect_is_rejected_before_parsing_body(self):
        client, response = self.mock_session()
        response.status_code = 307
        with patch.object(manage_qq.requests, 'Session', return_value=client), patch('builtins.print'):
            self.assertIsNone(manage_qq.get_friends(sample_config()))
        response.json.assert_not_called()

    def test_bad_friend_list_returns_none(self):
        for body in ([], {'status': 'ok', 'data': None}, {'status': 'ok', 'data': [7]},
                     {'status': 'failed', 'data': []}):
            with self.subTest(body=body):
                client, response = self.mock_session()
                response.json.return_value = body
                with patch.object(manage_qq.requests, 'Session', return_value=client), patch('builtins.print'):
                    self.assertIsNone(manage_qq.get_friends(sample_config()))


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'config.json'
        self.initial = sample_config()
        self.write(self.initial)
        self.cfg = Config(deepcopy(self.initial))
        self.cfg.path = str(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, data):
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def read(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def concurrently_changed(self, recipients):
        latest = deepcopy(self.initial)
        latest['push']['qq'].update(private_qq=recipients, group_id='20002', access_token='new-token')
        latest['llm']['primary']['model'] = 'new-model'
        latest['custom_extension']['new'] = 'untouched'
        self.write(latest)
        return latest

    def assert_only_recipients_changed(self, previous, expected):
        actual = self.read()
        expected_data = deepcopy(previous)
        expected_data['push']['qq']['private_qq'] = expected
        self.assertEqual(actual, expected_data)

    def test_manage_addition_preserves_concurrent_subscribers_and_config(self):
        edited = deepcopy(self.initial)
        manage_qq.set_qq_list(edited, ['10001', '10002'])
        previous = self.concurrently_changed(['10001', '10003'])
        with patch.object(manage_qq, 'CONFIG_PATH', str(self.path)):
            result = manage_qq.save_config(edited, ['10001'])
        self.assert_only_recipients_changed(previous, ['10001', '10003', '10002'])
        self.assertEqual(result, self.read())

    def test_manage_removal_preserves_concurrent_subscribers_and_config(self):
        edited = deepcopy(self.initial)
        manage_qq.set_qq_list(edited, [])
        previous = self.concurrently_changed('10001，10003\n10004')
        with patch.object(manage_qq, 'CONFIG_PATH', str(self.path)):
            manage_qq.save_config(edited, ['10001'])
        self.assert_only_recipients_changed(previous, ['10003', '10004'])

    def test_bot_subscription_uses_latest_fields_and_string_recipients(self):
        bot = QQBot(self.cfg)
        previous = self.concurrently_changed('10001，10003\n10004')
        with patch.object(bot, '_call_http') as network:
            answer = bot._subscription_command('订阅新闻', '10002')
        self.assertIn('加入', answer)
        network.assert_not_called()
        self.assert_only_recipients_changed(previous, ['10001', '10003', '10004', '10002'])

    def test_bot_unsubscription_keeps_concurrent_settings_and_other_recipients(self):
        bot = QQBot(self.cfg)
        previous = self.concurrently_changed('10001，10003\n10004')
        with patch.object(bot, '_call_http') as network:
            answer = bot._subscription_command('取消订阅', '10001')
        self.assertIn('停止', answer)
        network.assert_not_called()
        self.assert_only_recipients_changed(previous, ['10003', '10004'])

    def test_bot_subscription_list_counts_string_numbers(self):
        bot = QQBot(self.cfg)
        self.concurrently_changed('10001，10003\n10004')
        with patch.object(bot, '_call_http') as network:
            answer = bot._subscription_command('订阅列表', '10001')
        self.assertEqual(answer, '当前推送人数：3')
        network.assert_not_called()

    def test_dispatcher_refreshes_connection_settings_and_recipients(self):
        self.concurrently_changed(['10001', '10003'])
        dispatcher = Dispatcher(self.cfg)
        channel = dispatcher.channels[0]
        self.assertEqual(channel.private_qq_list, ['10001', '10003'])
        self.assertEqual(channel.access_token, 'new-token')
        self.assertEqual(channel.group_id, '20002')

        self.concurrently_changed('10003，10004\n10005')
        observed = []
        with patch.object(channel, 'send', side_effect=lambda *args: observed.append(list(channel.private_qq_list))):
            self.assertEqual(dispatcher.send('title', 'content'), (['qq'], []))
        self.assertEqual(observed, [['10003', '10004', '10005']])
        self.assertEqual(self.cfg.data, self.initial)
        self.assertEqual(channel.group_id, '20002')
        self.assertEqual(channel.access_token, 'new-token')

    def test_dispatcher_bad_file_keeps_latest_valid_subscribers(self):
        self.concurrently_changed(['10003'])
        dispatcher = Dispatcher(self.cfg)
        self.path.write_text('{broken', encoding='utf-8')
        channel = dispatcher.channels[0]
        with patch.object(channel, 'send') as send, patch('news_agent.push.dispatcher.log.warning') as warning:
            self.assertEqual(dispatcher.send('title', 'content'), ([], []))
        self.assertEqual(channel.private_qq_list, ['10003'])
        send.assert_not_called()
        warning.assert_called_once()

    def test_dispatcher_without_config_path_uses_in_memory_subscriptions(self):
        self.cfg.path = ''
        with patch('news_agent.push.dispatcher.Path.open') as read:
            dispatcher = Dispatcher(self.cfg)
        read.assert_not_called()
        self.assertEqual(dispatcher.channels[0].private_qq_list, ['10001'])


if __name__ == '__main__':
    unittest.main()
