"""Mail cancellation and immediate UI disable without external messages."""

from copy import deepcopy
import json
import os
from pathlib import Path
import smtplib
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from news_agent.config_io import atomic_update_json
from news_agent.config import Config
from news_agent.desktop.window import ConfigWindow
from news_agent.push.email import EmailChannel
from news_agent.push.base import PushError


def email_settings():
    return {'smtp_host': 'smtp.example.test', 'smtp_port': 465,
            'username': 'sender@example.test', 'password': 'private-secret',
            'to': ['reader@example.test'], 'retries': 3}


class EmailCancellationTests(unittest.TestCase):
    def setUp(self):
        self.channel = EmailChannel(email_settings())

    def test_disabled_mail_never_opens_smtp(self):
        with patch('news_agent.push.email.smtplib.SMTP_SSL') as smtp:
            self.assertFalse(self.channel.send('title', 'body', should_send=lambda: False))
        smtp.assert_not_called()

    def test_disable_during_login_stops_before_submitting_message(self):
        enabled = [True]
        server = Mock()
        server.login.side_effect = lambda *args: enabled.__setitem__(0, False)
        with patch('news_agent.push.email.smtplib.SMTP_SSL', return_value=server):
            self.assertFalse(self.channel.send('title', 'body', should_send=lambda: enabled[0]))
        server.sendmail.assert_not_called()
        server.quit.assert_called_once()

    def test_disable_cancels_wait_and_later_retry(self):
        enabled = [True]
        server = Mock()
        server.login.side_effect = smtplib.SMTPServerDisconnected('private-secret')
        with patch('news_agent.push.email.smtplib.SMTP_SSL', return_value=server) as smtp, \
                patch('news_agent.push.email.time.sleep', side_effect=lambda seconds: enabled.__setitem__(0, False)) as sleep:
            self.assertFalse(self.channel.send('title', 'body', should_send=lambda: enabled[0]))
        smtp.assert_called_once()
        sleep.assert_called_once_with(1)
        server.sendmail.assert_not_called()
        server.quit.assert_called_once()

    def test_callback_read_failure_stops_without_smtp(self):
        def cannot_read():
            raise PermissionError('private-path')
        with patch('news_agent.push.email.smtplib.SMTP_SSL') as smtp:
            self.assertFalse(self.channel.send('title', 'body', should_send=cannot_read))
        smtp.assert_not_called()

    def test_quit_failure_after_send_does_not_retry_successful_message(self):
        server = Mock()
        server.quit.side_effect = smtplib.SMTPServerDisconnected('connection closed')
        with patch('news_agent.push.email.smtplib.SMTP_SSL', return_value=server) as smtp:
            self.assertTrue(self.channel.send('title', 'body', should_send=lambda: True))
        smtp.assert_called_once()
        server.sendmail.assert_called_once()
        server.close.assert_called_once()

    def test_repeated_error_closes_connections_and_does_not_echo_secrets(self):
        server = Mock()
        server.login.side_effect = smtplib.SMTPAuthenticationError(535, b'private-secret')
        with patch('news_agent.push.email.smtplib.SMTP_SSL', return_value=server), \
                patch('news_agent.push.email.time.sleep'):
            with self.assertRaises(PushError) as caught:
                self.channel.send('title', 'body')
        self.assertEqual(server.quit.call_count, 3)
        self.assertNotIn('private-secret', str(caught.exception))


class CurrentChannelConfigTests(unittest.TestCase):
    def test_saved_disabled_channels_override_stale_config_without_changing_it(self):
        original = {'push': {'enabled': ['qq', 'email']}}
        config = Config(deepcopy(original))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            config.path = str(path)
            path.write_text(json.dumps({'push': {'enabled': ['qq']}}), encoding='utf-8-sig')
            self.assertEqual(config.enabled_pushes(), ['qq'])
        self.assertEqual(config.data, original)

    def test_bad_channel_config_fails_closed_without_echoing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config({'push': {'enabled': ['email']}})
            config.path = str(Path(directory) / 'config.json')
            for value in ('{private-secret', '[]', '{"push":[]}', '{"push":{"enabled":"email"}}'):
                Path(config.path).write_text(value, encoding='utf-8')
                with patch('news_agent.logger.get_logger') as logger:
                    self.assertEqual(config.enabled_pushes(), [])
                self.assertNotIn('private-secret', str(logger.return_value.warning.call_args))

    def test_in_memory_config_keeps_original_behavior(self):
        config = Config({'push': {'enabled': ['qq', 'email']}})
        self.assertEqual(config.enabled_pushes(), ['qq', 'email'])


class ImmediateDisableUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'config.json'
        self.original = {'push': {'enabled': ['qq', 'email'],
            'qq': {'http_base': 'http://127.0.0.1:3000', 'private_qq': ['123456']},
            'email': email_settings(),
            'telegram': {'bot_token': 'fake-token', 'chat_id': '654321'}},
            'custom_extension': {'keep': True}}
        self.write(self.original)
        self.window = ConfigWindow(self.path, monitor_services=False)

    def tearDown(self):
        self.window.stop_monitoring()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding='utf-8')

    def read(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def test_disable_is_immediate_but_unrelated_edits_are_not_saved(self):
        self.window.fields['push.qq.group_id'][0].setText('777777')
        self.window.channel_toggles['email'].setChecked(False)
        expected = deepcopy(self.original)
        expected['push']['enabled'] = ['qq']
        self.assertEqual(self.read(), expected)
        self.assertEqual(self.window.changes(), {'push.qq.group_id': '777777'})
        self.assertIn('已停用并保存', self.window.save_status.text())
        from news_agent.factory_defaults import public_backup
        self.assertEqual(json.loads(self.path.with_name('config.json.bak').read_text(encoding='utf-8')), public_backup(self.original))

    def test_disabling_preserves_other_windows_new_channel_and_subscription(self):
        latest = deepcopy(self.original)
        latest['push']['enabled'].append('telegram')
        latest['push']['qq']['private_qq'].append('654321')
        self.write(latest)
        self.window.channel_toggles['email'].setChecked(False)
        self.assertEqual(self.read()['push']['enabled'], ['qq', 'telegram'])
        self.assertEqual(self.read()['push']['qq']['private_qq'], ['123456', '654321'])
        self.assertNotIn('push.enabled', self.window.changes())
        self.window.fields['push.qq.group_id'][0].setText('777777')
        self.assertTrue(self.window.save())
        self.assertEqual(self.read()['push']['enabled'], ['qq', 'telegram'])

    def test_failed_disable_rolls_back_switch_and_preserves_other_edits(self):
        self.window.fields['push.qq.group_id'][0].setText('777777')
        with patch('news_agent.desktop.window.atomic_update_json', side_effect=PermissionError), \
                patch('news_agent.desktop.window.QMessageBox.warning') as warning:
            self.window.channel_toggles['email'].setChecked(False)
        self.assertTrue(self.window.channel_toggles['email'].isChecked())
        self.assertEqual(self.window.channel_toggles['email'].get_position(), 1.0)
        self.assertEqual(self.read(), self.original)
        self.assertEqual(self.window.changes(), {'push.qq.group_id': '777777'})
        warning.assert_called_once()

    def test_loading_does_not_write_and_enabling_still_requires_save(self):
        self.window.channel_toggles['email'].setChecked(False)
        with patch('news_agent.desktop.window.atomic_update_json') as update:
            self.window.populate()
            self.window.channel_toggles['email'].setChecked(True)
        update.assert_not_called()
        self.assertEqual(self.read()['push']['enabled'], ['qq'])
        self.assertEqual(self.window.changes()['push.enabled'], ['qq', 'email'])


if __name__ == '__main__':
    unittest.main()
