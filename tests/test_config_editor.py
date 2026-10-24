"""Regression coverage for preserving real configuration data while editing."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from news_agent.config_store import ConfigStore, get_value, merge_changes, validate_config
from news_agent.factory_defaults import public_backup


def sample_config():
    return {
        '_comment': '中文说明',
        'push': {'enabled': ['qq'], 'qq': {
            '_comment_access_token': '保留鉴权说明',
            'http_base': 'http://127.0.0.1:3000', 'access_token': 'secret-token',
            'private_qq': ['123456'], 'group_id': '', 'ws_port': 3001,
        }},
        'schedule': {'daily_report_time': '08:00', 'realtime_interval_minutes': 10},
        'sources': {'rss': [{'_comment_url': '中文地址说明', 'name': '测试', 'url': 'https://example.com/feed', 'proxy': True}]},
        'custom_extension': {'leave_alone': 42},
    }


class MergeTests(unittest.TestCase):
    def test_merge_retains_concurrent_subscription_and_unknown_metadata(self):
        original = sample_config()
        latest = deepcopy(original)
        latest['push']['qq']['private_qq'].append('654321')
        merged = merge_changes(original, latest, {'schedule.daily_report_time': '09:30'})
        self.assertEqual(merged['push']['qq']['private_qq'], ['123456', '654321'])
        self.assertEqual(merged['custom_extension'], original['custom_extension'])
        self.assertEqual(merged['_comment'], '中文说明')
        self.assertEqual(original['schedule']['daily_report_time'], '08:00')

    def test_conflicting_subscription_edit_is_rejected(self):
        original = sample_config()
        latest = deepcopy(original)
        latest['push']['qq']['private_qq'].append('654321')
        with self.assertRaisesRegex(ValueError, 'push.qq.private_qq'):
            merge_changes(original, latest, {'push.qq.private_qq': ['987654']})

    def test_disabled_email_and_telegram_require_no_credentials(self):
        self.assertEqual(validate_config(sample_config()), [])
        cfg = sample_config()
        cfg['push']['enabled'] = ['email', 'telegram']
        self.assertGreaterEqual(len(validate_config(cfg)), 5)

    def test_invalid_schedule_or_qq_number_is_rejected(self):
        cfg = sample_config()
        cfg['schedule']['daily_report_time'] = '29:88'
        cfg['push']['qq']['private_qq'] = ['not-a-number']
        self.assertEqual(len(validate_config(cfg)), 2)


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from news_agent.desktop.window import ConfigWindow
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'config.json'
        self.original = sample_config()
        self.path.write_text(json.dumps(self.original, ensure_ascii=False), encoding='utf-8')
        self.window = ConfigWindow(self.path, monitor_services=False)

    def tearDown(self):
        self.window.loading = True
        self.window.log_timer.stop()
        self.window.stop_monitoring()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_open_and_visit_every_page_does_not_change_config(self):
        for index in range(7):
            self.window.select_page(index)
            self.app.processEvents()
        self.assertEqual(self.window.changes(), {})
        self.assertFalse(self.window.save_button.isEnabled())
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')), self.original)

    def test_save_preserves_comments_unknown_data_and_external_changes(self):
        self.window.fields['push.qq.group_id'][0].setText('987654')
        latest = deepcopy(self.original)
        latest['push']['qq']['private_qq'].append('333333')
        self.path.write_text(json.dumps(latest, ensure_ascii=False), encoding='utf-8')
        self.assertTrue(self.window.save())
        actual = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(actual['push']['qq']['group_id'], '987654')
        self.assertEqual(actual['push']['qq']['private_qq'], ['123456', '333333'])
        self.assertEqual(actual['push']['qq']['_comment_access_token'], '保留鉴权说明')
        self.assertEqual(actual['custom_extension'], self.original['custom_extension'])
        self.assertEqual(self.window.changes(), {})
        self.assertEqual(json.loads(self.path.with_name('config.json.bak').read_text(encoding='utf-8')), public_backup(latest))

    def test_rss_edit_keeps_source_comments_and_proxy_flag(self):
        self.window.rss_table.item(0, 0).setText('新的名称')
        self.assertTrue(self.window.save())
        actual = json.loads(self.path.read_text(encoding='utf-8'))['sources']['rss'][0]
        self.assertEqual(actual['name'], '新的名称')
        self.assertEqual(actual['_comment_url'], '中文地址说明')
        self.assertTrue(actual['proxy'])

    def test_secret_hidden_and_disabled_channel_form_collapsed(self):
        from PySide6.QtWidgets import QLineEdit
        self.assertEqual(self.window.fields['push.qq.access_token'][0].echoMode(), QLineEdit.EchoMode.Password)
        self.assertTrue(self.window.channel_forms['email'].isHidden())
        self.window.channel_toggles['email'].setChecked(True)
        self.assertFalse(self.window.channel_forms['email'].isHidden())
        self.assertEqual(self.window.changes()['push.enabled'], ['qq', 'email'])

    def test_conflict_does_not_write_or_create_backup(self):
        self.window.fields['push.qq.group_id'][0].setText('111111')
        latest = deepcopy(self.original)
        latest['push']['qq']['group_id'] = '222222'
        text = json.dumps(latest, ensure_ascii=False)
        self.path.write_text(text, encoding='utf-8')
        with patch('news_agent.desktop.window.QMessageBox.warning') as warning:
            self.assertFalse(self.window.save())
            warning.assert_called_once()
        self.assertEqual(self.path.read_text(encoding='utf-8'), text)
        self.assertFalse(self.path.with_name('config.json.bak').exists())

    def test_discard_prompt_cancel_keeps_unsaved_edits(self):
        from PySide6.QtWidgets import QMessageBox
        self.window.fields['push.qq.group_id'][0].setText('111111')
        with patch('news_agent.desktop.window.QMessageBox.question', return_value=QMessageBox.StandardButton.Cancel):
            self.assertFalse(self.window.allow_discard())
        self.assertEqual(self.window.changes()['push.qq.group_id'], '111111')
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')), self.original)

    def test_connection_check_reads_login_info_and_never_sends_message(self):
        from news_agent.desktop.window import ConnectionProbe
        worker = ConnectionProbe('http://127.0.0.1:3000', 'test-token')
        outcomes = []
        worker.signals.done.connect(lambda ok, message: outcomes.append(ok))
        with patch('requests.Session') as factory:
            session = factory.return_value.__enter__.return_value
            request = session.post
            response = request.return_value.__enter__.return_value
            response.status_code = 200
            response.json.return_value = {'status': 'ok', 'data': {'user_id': 123456}}
            worker.run()
        self.assertEqual(outcomes, [True])
        self.assertFalse(session.trust_env)
        request.assert_called_once_with(
            'http://127.0.0.1:3000/get_login_info', json={},
            headers={'Authorization': 'Bearer test-token'}, timeout=(2, 4), allow_redirects=False,
        )

    def test_reloading_config_resets_secret_visibility(self):
        from PySide6.QtWidgets import QLineEdit
        editor = self.window.fields['push.qq.access_token'][0]
        editor.reveal_button.setChecked(True)
        self.assertEqual(editor.echoMode(), QLineEdit.EchoMode.Normal)
        self.window.populate()
        self.assertEqual(editor.echoMode(), QLineEdit.EchoMode.Password)
        self.assertFalse(editor.reveal_button.isChecked())


if __name__ == '__main__':
    unittest.main()
