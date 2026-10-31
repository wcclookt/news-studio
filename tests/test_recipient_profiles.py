"""Recipient IDs remain canonical while display profiles stay optional."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from news_agent.config import Config, load_config
from news_agent.config_store import validate_config, validate_config_structure
from news_agent.factory_defaults import fresh_config
from news_agent.push.email import EmailChannel
from news_agent.push.qq import QQChannel
from news_agent.push.telegram import TelegramChannel
from news_agent.push.dispatcher import Dispatcher
from news_agent.qq_bot import QQBot


class ProfileSchemaTests(unittest.TestCase):
    def test_factory_defaults_are_empty_and_profile_only_recipients_validate(self):
        defaults = fresh_config()
        self.assertEqual(defaults['push']['qq']['private_qq_profiles'], [])
        self.assertEqual(defaults['push']['email']['to_profiles'], [])
        self.assertEqual(defaults['push']['telegram']['chat_profiles'], [])
        data = deepcopy(defaults)
        data['push']['enabled'] = ['qq', 'email', 'telegram']
        data['push']['qq']['private_qq_profiles'] = [{'user_id': '10001', 'nickname': '张三', 'note': '朋友'}]
        del data['push']['qq']['private_qq']
        data['push']['email']['username'] = 'sender@example.test'
        data['push']['email']['password'] = 'secret'
        data['push']['email']['to_profiles'] = [{'address': 'receiver@example.test', 'note': '日报'}]
        del data['push']['email']['to']
        data['push']['telegram']['bot_token'] = 'token'
        data['push']['telegram']['chat_profiles'] = [{'chat_id': '-1001', 'title': '新闻群'}]
        del data['push']['telegram']['chat_ids']
        del data['push']['telegram']['chat_id']
        self.assertEqual(validate_config_structure(data), [])
        self.assertEqual(validate_config(data), [])

    def test_duplicate_or_malformed_profiles_are_rejected_without_exposing_values(self):
        data = fresh_config()
        data['push']['qq']['private_qq_profiles'] = [
            {'user_id': '10001', 'nickname': 'a'}, {'user_id': '10001', 'nickname': 'b'}]
        data['push']['email']['to_profiles'] = [{'address': 'not-an-email', 'note': 1}]
        errors = validate_config_structure(data)
        self.assertTrue(any('private_qq_profiles' in error for error in errors))
        self.assertTrue(any('to_profiles' in error for error in errors))
        self.assertNotIn('not-an-email', '\n'.join(errors))


class SubscriptionProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'config.json'
        data = fresh_config()
        data['push']['qq']['private_qq'] = ['10001']
        data['push']['qq']['private_qq_profiles'] = [{'user_id': '10001', 'nickname': '旧昵称'}]
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        self.bot = QQBot(load_config(self.path))

    def tearDown(self):
        self.temp.cleanup()

    def test_subscribe_stores_sender_nickname_and_card_without_network(self):
        reply = self.bot._subscription_command('订阅新闻', '10002', {
            'user_id': '10002', 'nickname': '小明', 'note': '群名片'})
        self.assertIn('加入', reply)
        saved = json.loads(self.path.read_text(encoding='utf-8'))['push']['qq']
        self.assertEqual(saved['private_qq'], ['10001', '10002'])
        self.assertEqual(saved['private_qq_profiles'][-1], {
            'user_id': '10002', 'nickname': '小明', 'note': '群名片'})

    def test_existing_subscriber_refreshes_profile_without_duplicate_id(self):
        reply = self.bot._subscription_command('订阅新闻', '10001', {
            'user_id': '10001', 'nickname': '新昵称', 'note': ''})
        self.assertIn('已经', reply)
        saved = json.loads(self.path.read_text(encoding='utf-8'))['push']['qq']
        self.assertEqual(saved['private_qq'], ['10001'])
        self.assertEqual(saved['private_qq_profiles'][0]['nickname'], '新昵称')

    def test_repeat_subscription_never_overwrites_operator_note(self):
        data = json.loads(self.path.read_text(encoding='utf-8'))
        data['push']['qq']['private_qq_profiles'][0]['note'] = '管理员备注'
        self.path.write_text(json.dumps(data), encoding='utf-8')
        self.bot._subscription_command('订阅', '10001', {'nickname': '新昵称', 'note': '新名片'})
        saved = json.loads(self.path.read_text(encoding='utf-8'))['push']['qq']
        self.assertEqual(saved['private_qq_profiles'][0]['note'], '管理员备注')

    def test_cancel_short_command_stops_later_fragments_in_current_send(self):
        data = json.loads(self.path.read_text(encoding='utf-8'))
        data['push']['enabled'] = ['qq']
        self.path.write_text(json.dumps(data), encoding='utf-8')
        dispatcher = Dispatcher(load_config(self.path))
        def accept_first_fragment(*args):
            self.bot._subscription_command('取消', '10001')
        with patch('news_agent.push.qq.QQChannel._call', side_effect=accept_first_fragment) as call:
            self.assertEqual(dispatcher.send('新闻', '正文' * 1600), (['qq'], []))
        self.assertEqual(call.call_count, 1)
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['push']['qq']['private_qq'], [])

    def test_cancel_before_next_recipient_skips_that_recipient(self):
        data = json.loads(self.path.read_text(encoding='utf-8'))
        data['push']['enabled'] = ['qq']
        data['push']['qq']['private_qq'] = ['10001', '10002']
        self.path.write_text(json.dumps(data), encoding='utf-8')
        dispatcher = Dispatcher(load_config(self.path))
        with patch('news_agent.push.qq.QQChannel._call',
                   side_effect=lambda *args: self.bot._subscription_command('取消', '10002')) as call:
            dispatcher.send('新闻', '正文')
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.args[1]['user_id'], '10001')

    def test_unsubscribe_removes_id_and_profile(self):
        self.bot._subscription_command('取消订阅', '10001')
        saved = json.loads(self.path.read_text(encoding='utf-8'))['push']['qq']
        self.assertEqual(saved['private_qq'], [])
        self.assertEqual(saved['private_qq_profiles'], [])

    def test_sender_profile_prefers_nickname_and_falls_back_to_card(self):
        self.assertEqual(self.bot.sender_profile({'user_id': 1, 'sender': {'nickname': '昵称', 'card': '名片'}}),
                         {'user_id': '1', 'nickname': '昵称', 'note': '名片'})
        self.assertEqual(self.bot.sender_profile({'user_id': 2, 'sender': {'card': '名片'}})['nickname'], '名片')


class ProfileOnlyChannelTests(unittest.TestCase):
    def test_qq_email_and_telegram_use_profiles_when_legacy_targets_are_empty(self):
        qq = QQChannel({'private_qq_profiles': [{'user_id': '10001', 'nickname': '朋友'}]})
        email = EmailChannel({'to_profiles': [{'address': 'receiver@example.test'}]})
        telegram = TelegramChannel({'bot_token': 'token',
                                    'chat_profiles': [{'chat_id': '-1001', 'title': '群'}]})
        self.assertEqual(qq.private_qq_list, ['10001'])
        self.assertEqual(email.to, ['receiver@example.test'])
        self.assertEqual(telegram.chat_ids, ['-1001'])

    def test_telegram_sends_to_all_profile_chat_ids(self):
        channel = TelegramChannel({'bot_token': 'token', 'chat_id': '1001',
                                   'chat_ids': ['1002'], 'chat_profiles': []})
        response = MagicMock(status_code=200)
        response.json.return_value = {'ok': True}
        with patch('news_agent.push.telegram.requests.post', return_value=response) as post:
            self.assertTrue(channel.send('标题', '内容'))
        self.assertEqual([call.kwargs['json']['chat_id'] for call in post.call_args_list], ['1002'])

    def test_explicit_empty_targets_never_resurrect_profile_or_legacy_recipients(self):
        qq = QQChannel({'private_qq': [], 'private_qq_profiles': [{'user_id': '10001'}]})
        email = EmailChannel({'to': [], 'to_profiles': [{'address': 'receiver@example.test'}]})
        tg = TelegramChannel({'chat_ids': [], 'chat_id': '1001', 'chat_profiles': [{'chat_id': '1002'}]})
        self.assertEqual(qq.private_qq_list, [])
        self.assertEqual(email.to, [])
        self.assertEqual(tg.chat_ids, [])

    def test_multiple_telegram_targets_continue_after_failure_and_mask_token(self):
        channel = TelegramChannel({'bot_token': 'secret-token', 'chat_ids': ['1001', '1002', '1001']})
        from news_agent.push.base import PushError
        bad, good = MagicMock(status_code=200), MagicMock(status_code=200)
        bad.json.return_value = {'ok': False, 'description': 'secret-token'}
        good.json.return_value = {'ok': True}
        with patch('news_agent.push.telegram.requests.post', side_effect=[bad, good]) as post:
            with self.assertRaises(PushError) as error:
                channel.send('<Title>', 'a < b & c')
        self.assertEqual(post.call_count, 2)
        self.assertNotIn('secret-token', str(error.exception))
        self.assertIn('&lt;Title&gt;', post.call_args.kwargs['json']['text'])
        bad.close.assert_called_once()
        good.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
