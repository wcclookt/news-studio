# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

"""Recipient table regressions for QQ, email and Telegram delivery lists."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from news_agent.desktop.recipient_list import RecipientTable
from news_agent.desktop.theme import STYLE
from news_agent.desktop.window import ConfigWindow
from news_agent.config_io import atomic_update_json
from news_agent.config_store import set_value
from news_agent.config import load_config
from news_agent.qq_bot import QQBot
from news_agent.push.telegram import TelegramChannel


class RecipientUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication([])
            cls.app.setStyle('Fusion')
            cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'config.json'
        self.path.write_text(json.dumps({
            'push': {
                'enabled': ['qq', 'email', 'telegram'],
                'qq': {
                    'http_base': 'http://127.0.0.1:3000',
                    'private_qq': ['10001'],
                    'private_qq_profiles': [{'user_id': '10001', 'nickname': '小明', 'note': '朋友'}],
                },
                'email': {
                    'smtp_host': 'smtp.example.test', 'username': 'sender@example.test', 'password': 'test-secret',
                    'to': ['a@example.com'],
                    'to_profiles': [{'address': 'a@example.com', 'name': '主邮箱', 'note': '日报'}],
                },
                'telegram': {
                    'bot_token': 'test-bot-token',
                    'chat_id': 'chat-a',
                    'chat_ids': ['chat-a'],
                    'chat_profiles': [{'chat_id': 'chat-a', 'title': '频道', 'note': 'TG'}],
                },
            },
        }), encoding='utf-8')
        self.window = ConfigWindow(self.path, monitor_services=False, cache_path=Path(self.temp.name) / 'news.db')
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.stop_monitoring()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_all_delivery_channels_load_profile_rows(self):
        for path in ('push.qq.private_qq', 'push.email.to', 'push.telegram.chat_ids'):
            editor = self.window.fields[path][0]
            self.assertIsInstance(editor, RecipientTable)
            self.assertEqual(editor.table.rowCount(), 1)
        self.assertEqual(self.window.fields['push.qq.private_qq'][0].values()[0]['nickname'], '小明')

    def test_note_edit_is_saved_as_profile_change_and_legacy_ids_stay_available(self):
        editor = self.window.fields['push.qq.private_qq'][0]
        editor.table.item(0, 2).setText('长期订阅')
        self.app.processEvents()
        changes = self.window.changes()
        self.assertEqual(self.window.editor_value('push.qq.private_qq'), ['10001'])
        self.assertEqual(changes['push.qq.private_qq_profiles'][0]['note'], '长期订阅')
        self.assertTrue(self.window.save())
        saved = json.loads(self.path.read_text(encoding='utf-8'))['push']['qq']
        self.assertEqual(saved['private_qq'], ['10001'])
        self.assertEqual(saved['private_qq_profiles'][0]['note'], '长期订阅')

    def test_incoming_subscription_and_refresh_preserve_unsaved_note(self):
        editor = self.window.fields['push.qq.private_qq'][0]
        editor.table.item(0, 2).setText('本机备注')
        bot = QQBot(load_config(self.path))
        with patch.object(bot, '_call_http') as http:
            bot._subscription_command('订阅', '10002', {'nickname': '新朋友'})
        http.assert_not_called()
        self.window.refresh_subscribers(force=True)
        self.assertEqual(editor.identifiers(), ['10001', '10002'])
        self.assertEqual(editor.values()[0]['note'], '本机备注')
        self.assertEqual(editor.values()[1]['nickname'], '新朋友')
        self.assertTrue(self.window.save())
        saved = json.loads(self.path.read_text(encoding='utf-8'))['push']['qq']
        self.assertEqual(saved['private_qq'], ['10001', '10002'])
        self.assertEqual(saved['private_qq_profiles'][0]['note'], '本机备注')

    def test_late_name_result_does_not_restore_unsubscribed_user_or_overwrite_note(self):
        editor = self.window.fields['push.qq.private_qq'][0]
        original = editor.values()
        editor.table.item(0, 2).setText('不覆盖我')
        QQBot(load_config(self.path))._subscription_command('取消', '10001')
        self.window.background_finished('qq-friends', {
            'epoch': self.window._recipient_epoch, 'original': original,
            'profiles': [{'user_id': '10001', 'nickname': '新名称', 'note': '旧备注'}]})
        self.assertEqual(editor.identifiers(), [])
        self.assertEqual(self.window.changes(), {})

    def test_names_arriving_after_restore_are_ignored(self):
        epoch = self.window._recipient_epoch
        self.window.store.restore()
        self.window.populate()
        self.window.background_finished('qq-friends', {
            'epoch': epoch, 'original': [],
            'profiles': [{'user_id': '10001', 'nickname': '旧账户'}]})
        self.assertEqual(self.window.editor_value('push.qq.private_qq'), [])
        self.assertEqual(self.window.changes(), {})

    def test_telegram_multiple_users_and_legacy_removal_roundtrip(self):
        editor = self.window.fields['push.telegram.chat_ids'][0]
        editor.set_values([{'chat_id': '1002', 'title': '用户二', 'note': '日报'},
                           {'chat_id': '-1003', 'title': '群三', 'note': ''}])
        editor.changed.emit()
        self.assertTrue(self.window.save())
        saved = json.loads(self.path.read_text(encoding='utf-8'))['push']['telegram']
        self.assertEqual(saved['chat_id'], '')
        self.assertEqual(TelegramChannel(saved).chat_ids, ['1002', '-1003'])
        editor.table.selectAll()
        editor.remove_selected()
        # Stop the channel to allow saving an intentionally empty target list.
        self.window.channel_toggles['telegram'].setChecked(False)
        self.assertTrue(self.window.save())
        saved = json.loads(self.path.read_text(encoding='utf-8'))['push']['telegram']
        self.assertEqual(TelegramChannel(saved).chat_ids, [])

    def test_email_refresh_merges_saved_recipient_with_unsaved_note(self):
        editor = self.window.fields['push.email.to'][0]
        editor.table.item(0, 2).setText('未保存备注')
        atomic_update_json(self.path, lambda data: set_value(data, 'push.email.to', ['a@example.com', 'b@example.com']) or data)
        self.window.refresh_recipient_list('push.email.to')
        self.assertEqual(editor.identifiers(), ['a@example.com', 'b@example.com'])
        self.assertEqual(editor.values()[0]['note'], '未保存备注')
        self.assertTrue(self.window.save())

    def test_add_delete_multi_select_and_duplicate_validation(self):
        editor = self.window.fields['push.qq.private_qq'][0]
        editor.add_row()
        editor.table.closePersistentEditor(editor.table.currentItem())
        editor.table.item(1, 0).setText('10001')
        with patch('news_agent.desktop.window.QMessageBox.warning') as warning:
            self.assertFalse(self.window.save())
            warning.assert_called_once()
        editor.table.selectAll()
        editor.remove_selected()
        self.assertEqual(editor.identifiers(), [])
        self.assertTrue(self.window.save())

    def test_partial_profiles_never_hide_legacy_ids(self):
        atomic_update_json(self.path, lambda data: set_value(data, 'push.qq.private_qq', ['10001', '10002']) or data)
        self.window.store.data = self.window.store.read()
        self.window.populate()
        self.assertEqual(self.window.editor_value('push.qq.private_qq'), ['10001', '10002'])
        self.assertEqual(self.window.changes(), {})

    def test_friend_lookup_uses_direct_readonly_api_and_preserves_notes(self):
        config = self.window.effective_config()
        with patch('requests.Session') as factory:
            session = factory.return_value.__enter__.return_value
            response = session.post.return_value.__enter__.return_value
            response.status_code = 200
            response.json.return_value = {'status': 'ok', 'data': [
                {'user_id': 10001, 'nickname': '新名称', 'remark': 'QQ好友备注'},
                {'user_id': 99999, 'nickname': '未订阅好友'}]}
            result = ConfigWindow.fetch_qq_profiles(config)
        self.assertEqual(result['profiles'], [{'user_id': '10001', 'nickname': '新名称', 'note': '朋友'}])
        self.assertFalse(session.trust_env)
        self.assertFalse(session.post.call_args.kwargs['allow_redirects'])
        self.assertEqual(session.post.call_count, 1)
        self.assertTrue(session.post.call_args.args[0].endswith('/get_friend_list'))

    def test_bad_friend_payload_and_redirect_never_report_success(self):
        config = self.window.effective_config()
        for code, body in [(302, {}), (200, {'status': 'failed'}),
                           (200, {'status': 'ok', 'data': {}}), (200, {'status': 'ok', 'data': [4]})]:
            with self.subTest(code=code, body=body), patch('requests.Session') as factory:
                response = factory.return_value.__enter__.return_value.post.return_value.__enter__.return_value
                response.status_code, response.json.return_value = code, body
                result = ConfigWindow.fetch_qq_profiles(config)
            self.assertIn('error', result)
            self.assertNotIn('profiles', result)

    def test_inflight_name_refresh_keeps_later_local_note(self):
        editor = self.window.fields['push.qq.private_qq'][0]
        original = editor.values()
        editor.table.item(0, 2).setText('刚改的备注')
        self.window.background_finished('qq-friends', {'epoch': self.window._recipient_epoch,
            'original': original, 'profiles': [{'user_id': '10001', 'nickname': '刚查到的名称', 'note': '旧备注'}]})
        self.assertEqual(editor.values()[0]['note'], '刚改的备注')
        self.assertEqual(editor.values()[0]['nickname'], '刚查到的名称')


if __name__ == '__main__':
    unittest.main()
