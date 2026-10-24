"""A saved channel switch controls each send, including work already in flight."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from news_agent.config import Config
from news_agent.push.dispatcher import Dispatcher


def sample_config():
    return {'push': {
        'enabled': ['qq', 'telegram', 'email'],
        'qq': {'private_qq': ['10001'], 'group_id': '20001', 'access_token': 'old-qq-token'},
        'email': {'smtp_host': 'smtp.example.com', 'username': 'from@example.com',
                  'password': 'old-email-secret', 'to': ['to@example.com']},
        'telegram': {'bot_token': 'old-tg-token', 'chat_id': '30001'},
    }}


class LiveChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'config.json'
        self.data = sample_config()
        self.write(self.data)
        self.cfg = Config(deepcopy(self.data))
        self.cfg.path = str(self.path)
        self.qq_patch = patch('news_agent.push.dispatcher.QQChannel.send', return_value=True)
        self.email_patch = patch('news_agent.push.dispatcher.EmailChannel.send', return_value=True)
        self.telegram_patch = patch('news_agent.push.dispatcher.TelegramChannel.send', return_value=True)
        self.qq = self.qq_patch.start()
        self.email = self.email_patch.start()
        self.telegram = self.telegram_patch.start()
        self.addCleanup(self.qq_patch.stop)
        self.addCleanup(self.email_patch.stop)
        self.addCleanup(self.telegram_patch.stop)
        self.addCleanup(self.temp.cleanup)

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding='utf-8')

    def switch(self, enabled):
        self.data['push']['enabled'] = enabled
        self.write(self.data)

    def assert_no_sends(self):
        self.qq.assert_not_called()
        self.email.assert_not_called()
        self.telegram.assert_not_called()

    def test_email_disabled_after_constructor_is_skipped(self):
        dispatcher = Dispatcher(self.cfg)
        self.switch(['qq'])
        self.assertEqual(dispatcher.send('新闻', '正文'), (['qq'], []))
        self.email.assert_not_called()
        self.telegram.assert_not_called()
        self.assertEqual([channel.name for channel in dispatcher.channels], ['qq'])

    def test_disabling_email_during_qq_prevents_smtp_call(self):
        dispatcher = Dispatcher(self.cfg)
        self.qq.side_effect = lambda *args: self.switch(['qq'])
        self.assertEqual(dispatcher.send('日报', '正文'), (['qq'], []))
        self.qq.assert_called_once()
        self.email.assert_not_called()
        self.telegram.assert_not_called()

    def test_disabled_channel_in_initial_file_overrides_stale_snapshot(self):
        self.switch(['qq'])
        dispatcher = Dispatcher(self.cfg)
        self.assertEqual([channel.name for channel in dispatcher.channels], ['qq'])
        self.assertEqual(dispatcher.send('新闻', '正文'), (['qq'], []))
        self.email.assert_not_called()

    def test_newly_enabled_channel_uses_current_config(self):
        self.switch([])
        dispatcher = Dispatcher(self.cfg)
        self.data['push']['email']['password'] = 'new-email-secret'
        self.switch(['email'])
        self.assertEqual(dispatcher.send('新闻', '正文'), (['email'], []))
        self.assertEqual(dispatcher.channels[0].password, 'new-email-secret')
        self.email.assert_called_once()
        self.qq.assert_not_called()
        self.telegram.assert_not_called()

    def test_disable_then_enable_rebuilds_channel_from_latest(self):
        dispatcher = Dispatcher(self.cfg)
        original = next(channel for channel in dispatcher.channels if channel.name == 'email')
        self.switch([])
        self.assertEqual(dispatcher.send('新闻', '正文'), ([], []))
        self.data['push']['email']['password'] = 'changed-after-disable'
        self.switch(['email'])
        self.assertEqual(dispatcher.send('新闻', '正文'), (['email'], []))
        current = dispatcher.channels[0]
        self.assertIsNot(current, original)
        self.assertEqual(current.password, 'changed-after-disable')

    def test_bad_config_fails_closed_and_can_recover(self):
        dispatcher = Dispatcher(self.cfg)
        self.path.write_text('{broken secret-token', encoding='utf-8')
        with patch('news_agent.push.dispatcher.log.warning') as warning:
            self.assertEqual(dispatcher.send('新闻', '正文'), ([], []))
        self.assert_no_sends()
        warning.assert_called_once()
        self.assertNotIn('secret-token', str(warning.call_args))
        self.switch(['qq'])
        self.assertEqual(dispatcher.send('新闻', '正文'), (['qq'], []))

    def test_read_failure_after_qq_stops_later_channels(self):
        dispatcher = Dispatcher(self.cfg)
        self.qq.side_effect = lambda *args: self.path.write_text('{broken', encoding='utf-8')
        with patch('news_agent.push.dispatcher.log.warning'):
            self.assertEqual(dispatcher.send('新闻', '正文'), (['qq'], []))
        self.email.assert_not_called()
        self.telegram.assert_not_called()

    def test_permission_error_fails_closed_without_echoing_exception(self):
        dispatcher = Dispatcher(self.cfg)
        with patch('news_agent.push.dispatcher.Path.open', side_effect=PermissionError('private-path secret-token')), \
             patch('news_agent.push.dispatcher.log.warning') as warning:
            self.assertEqual(dispatcher.send('新闻', '正文'), ([], []))
        self.assert_no_sends()
        self.assertNotIn('private-path', str(warning.call_args))
        self.assertNotIn('secret-token', str(warning.call_args))

    def test_invalid_enabled_types_fail_closed(self):
        dispatcher = Dispatcher(self.cfg)
        for invalid in (None, 'qq,email', ['qq', {}], False):
            with self.subTest(invalid=invalid):
                self.data['push']['enabled'] = invalid
                self.write(self.data)
                with patch('news_agent.push.dispatcher.log.warning'):
                    self.assertEqual(dispatcher.send('新闻', '正文'), ([], []))
                self.assert_no_sends()

    def test_only_empty_set_sends_nothing_and_does_not_read_file(self):
        dispatcher = Dispatcher(self.cfg)
        with patch('news_agent.push.dispatcher.Path.open') as read:
            self.assertEqual(dispatcher.send('新闻', '正文', only=set()), ([], []))
        self.assert_no_sends()
        read.assert_not_called()

    def test_only_filter_and_live_switches_both_apply(self):
        dispatcher = Dispatcher(self.cfg)
        self.switch(['qq'])
        self.assertEqual(dispatcher.send('新闻', '正文', only={'email'}), ([], []))
        self.assert_no_sends()

    def test_email_cancellation_callback_rechecks_latest_switch(self):
        dispatcher = Dispatcher(self.cfg)
        callbacks = []

        def during_email(title, content, html=None, should_send=None):
            self.assertTrue(should_send())
            self.switch(['qq'])
            callbacks.append(should_send())
            return False

        self.email.side_effect = during_email
        self.assertEqual(dispatcher.send('新闻', '正文', only={'email'}), ([], []))
        self.assertEqual(callbacks, [False])

    def test_email_callback_fails_closed_when_file_becomes_unreadable(self):
        dispatcher = Dispatcher(self.cfg)

        def during_email(title, content, html=None, should_send=None):
            self.path.write_text('{broken', encoding='utf-8')
            return False if not should_send() else True

        self.email.side_effect = during_email
        with patch('news_agent.push.dispatcher.log.warning'):
            self.assertEqual(dispatcher.send('新闻', '正文', only={'email'}), ([], []))

    def test_legacy_none_return_still_counts_as_success(self):
        dispatcher = Dispatcher(self.cfg)
        self.email.return_value = None
        self.assertEqual(dispatcher.send('新闻', '正文', only={'email'}), (['email'], []))

    def test_without_config_path_keeps_loaded_channels(self):
        self.cfg.path = ''
        dispatcher = Dispatcher(self.cfg)
        self.switch([])
        with patch('news_agent.push.dispatcher.Path.open') as read:
            self.assertEqual(dispatcher.send('新闻', '正文', only={'email'}), (['email'], []))
        read.assert_not_called()
        self.email.assert_called_once()
        self.qq.assert_not_called()
        self.telegram.assert_not_called()

    def test_active_qq_applies_new_connection_settings_and_recipients(self):
        dispatcher = Dispatcher(self.cfg)
        qq = next(channel for channel in dispatcher.channels if channel.name == 'qq')
        self.data['push']['qq'].update(private_qq='10002，10003\n10004',
                                       access_token='new-qq-token', group_id='20002')
        self.switch(['qq'])
        self.assertEqual(dispatcher.send('新闻', '正文'), (['qq'], []))
        current = dispatcher.channels[0]
        self.assertIsNot(current, qq)
        self.assertEqual(current.private_qq_list, ['10002', '10003', '10004'])
        self.assertEqual(current.access_token, 'new-qq-token')
        self.assertEqual(current.group_id, '20002')
        self.assertEqual(self.cfg.data, sample_config())

    def test_active_email_uses_new_account_without_disabling_first(self):
        dispatcher=Dispatcher(self.cfg)
        self.data['push']['email'].update(password='new-password',smtp_host='new.example.test')
        self.write(self.data)
        dispatcher.send('news','body',only={'email'})
        current=next(channel for channel in dispatcher.channels if channel.name=='email')
        self.assertEqual(current.password,'new-password')
        self.assertEqual(current.smtp_host,'new.example.test')

    def test_email_inflight_callback_cancels_submission_if_credentials_change(self):
        dispatcher=Dispatcher(self.cfg)
        def email(title,content,html=None,should_send=None):
            self.data['push']['email']['password']='changed-during-login'
            self.write(self.data)
            self.assertFalse(should_send())
            return False
        self.email.side_effect=email
        self.assertEqual(dispatcher.send('news','body',only={'email'}),([],[]))


if __name__ == '__main__':
    unittest.main()
