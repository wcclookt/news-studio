# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

"""Restore durability, live subscription UI, and explicit mocked NapCat sends."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import requests
from PySide6.QtWidgets import QApplication

from news_agent.config_io import atomic_update_json
from news_agent.config import load_config
from news_agent.config_store import ConfigStore, validate_config
from news_agent.desktop.qq_test import TEST_MESSAGE, send_test_message
from news_agent.desktop.window import ConfigWindow
from news_agent.push.base import PushError
from news_agent.push.qq import QQChannel, _parse_qq_list
from news_agent.factory_defaults import fresh_config, public_backup


def config():
    return {'push': {'enabled': ['qq'], 'qq': {
        'http_base': 'http://127.0.0.1:3000', 'access_token': 'fake-key',
        'private_qq': ['123456'], 'ws_port': 3001,
    }}, 'schedule': {'daily_report_time': '08:00'}, '_comment': '初始说明'}


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'config.json'
        self.original = config()
        self.path.write_text(json.dumps(self.original, ensure_ascii=False), encoding='utf-8')
        self.store = ConfigStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_private_snapshot_is_never_created_and_restore_is_always_fresh(self):
        self.store.save({'schedule.daily_report_time': '09:30'})
        store = ConfigStore(self.path)
        store.save({'schedule.daily_report_time': '10:00'})
        preceding = store.data
        store.restore('initial', preserve_subscribers=False)
        self.assertEqual(store.data, fresh_config())
        self.assertFalse(store.initial_path.exists())
        self.assertEqual(json.loads(self.path.with_name('config.json.bak').read_text(encoding='utf-8')), public_backup(preceding))

    def test_restore_never_keeps_historical_subscribers_even_with_legacy_flag(self):
        self.store.ensure_initial()
        self.store.save({'schedule.daily_report_time': '10:00'})
        atomic_update_json(self.path, lambda data: self.replace_users(data, ['654321']))
        self.store.restore('initial', preserve_subscribers=True)
        self.assertEqual(self.store.data['push']['qq']['private_qq'], [])
        self.assertEqual(self.store.data['schedule']['daily_report_time'], '08:00')

    @staticmethod
    def replace_users(data, values):
        data['push']['qq']['private_qq'] = values
        return data

    def test_factory_defaults_clear_keys_and_recipients_and_validate(self):
        self.store.restore('factory', preserve_subscribers=False)
        self.assertEqual(validate_config(self.store.data), [])
        self.assertEqual(self.store.data['push']['qq']['private_qq'], [])
        self.assertEqual(self.store.data['llm']['primary']['api_key'], '')
        self.assertEqual(self.store.data['push']['enabled'], [])
        self.assertFalse(self.store.initial_path.exists())

    def test_private_or_broken_snapshot_is_deleted_and_never_loaded(self):
        self.store.save({'schedule.daily_report_time': '10:00'})
        self.store.initial_path.write_text('{broken', encoding='utf-8')
        self.store.restore()
        self.assertEqual(self.store.data, fresh_config())
        self.assertFalse(self.store.initial_path.exists())

    def test_subscribing_with_legacy_semicolon_list_preserves_existing_recipients(self):
        from news_agent.qq_bot import QQBot
        atomic_update_json(self.path, lambda data: self.replace_users(data, '123456；654321'))
        bot = QQBot(load_config(self.path))
        bot._subscription_command('订阅新闻', '777777')
        self.assertEqual(ConfigStore(self.path).data['push']['qq']['private_qq'], ['123456', '654321', '777777'])
        bot._subscription_command('取消订阅', '654321')
        self.assertEqual(ConfigStore(self.path).data['push']['qq']['private_qq'], ['123456', '777777'])


class QQTestSendTests(unittest.TestCase):
    def test_news_push_uses_same_local_route_and_rejects_unconfirmed_responses(self):
        channel = QQChannel({'http_base': 'http://localhost:3000', 'access_token': 'secret'})
        with patch('news_agent.push.qq.requests.Session') as factory:
            session = factory.return_value.__enter__.return_value
            response = session.post.return_value.__enter__.return_value
            response.status_code = 200
            response.json.return_value = {'status': 'failed', 'wording': 'secret raw response'}
            with self.assertRaises(PushError) as caught:
                channel._call('send_private_msg', {'user_id': '123456', 'message': 'test'})
            self.assertFalse(session.trust_env)
            self.assertFalse(session.post.call_args.kwargs['allow_redirects'])
            self.assertNotIn('secret', str(caught.exception))

    def test_qq_recipient_parser_deduplicates_all_supported_separators(self):
        self.assertEqual(_parse_qq_list('123456\n654321；123456，777777'), ['123456', '654321', '777777'])

    def test_news_push_requires_message_id_before_reporting_success(self):
        channel = QQChannel({'http_base': 'http://localhost:3000'})
        with patch('news_agent.push.qq.requests.Session') as factory:
            response = factory.return_value.__enter__.return_value.post.return_value.__enter__.return_value
            response.status_code = 200
            response.json.return_value = {'status': 'ok', 'retcode': 0, 'data': {}}
            with self.assertRaisesRegex(PushError, '未返回消息编号'):
                channel._call('send_private_msg', {'user_id': '123456', 'message': 'test'})

    def test_single_exact_private_message_with_auth_and_no_ambient_proxy(self):
        with patch('news_agent.desktop.qq_test.requests.Session') as factory:
            session = factory.return_value.__enter__.return_value
            response = session.post.return_value.__enter__.return_value
            response.status_code = 200
            response.json.return_value = {'status': 'ok', 'retcode': 0, 'data': {'message_id': 42}}
            result = send_test_message('http://127.0.0.1:3000', 'secret', '123456')
            session.post.assert_called_once_with('http://127.0.0.1:3000/send_private_msg',
                json={'user_id': '123456', 'message': TEST_MESSAGE},
                headers={'Authorization': 'Bearer secret'}, timeout=(3, 8), allow_redirects=False)
            self.assertFalse(session.trust_env)
            self.assertTrue(result['ok'])

    def test_timeout_has_unknown_outcome_and_is_never_retried(self):
        with patch('news_agent.desktop.qq_test.requests.Session') as factory:
            session = factory.return_value.__enter__.return_value
            session.post.side_effect = requests.Timeout('secret URL')
            result = send_test_message('http://127.0.0.1:3000', 'secret', '123456')
            self.assertEqual(session.post.call_count, 1)
            self.assertFalse(result['ok'])
            self.assertIn('未知', result['message'])
            self.assertNotIn('secret', result['message'])

    def test_non_success_payload_and_redirect_do_not_claim_sent(self):
        for code, payload in [(302, {}), (200, {'status': 'ok', 'data': {}}),
                              (200, {'status': 'failed', 'retcode': 100})]:
            with self.subTest(code=code, payload=payload), patch('news_agent.desktop.qq_test.requests.Session') as factory:
                response = factory.return_value.__enter__.return_value.post.return_value.__enter__.return_value
                response.status_code = code
                response.json.return_value = payload
                self.assertFalse(send_test_message('http://localhost:3000', '', '123456')['ok'])

    def test_bad_address_or_recipient_does_not_make_request(self):
        with patch('news_agent.desktop.qq_test.requests.Session') as factory:
            self.assertFalse(send_test_message('http://[bad', '', '123456')['ok'])
            self.assertFalse(send_test_message('http://localhost:3000', '', 'not-qq')['ok'])
            factory.assert_not_called()


class LiveSubscriberUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'config.json'
        self.path.write_text(json.dumps(config()), encoding='utf-8')
        self.window = ConfigWindow(self.path, monitor_services=False)

    def tearDown(self):
        self.window.stop_monitoring()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_external_subscription_is_visible_without_losing_other_unsaved_edits(self):
        self.window.fields['schedule.daily_report_time'][0].setTime(self.window.fields['schedule.daily_report_time'][0].time().addSecs(3600))
        atomic_update_json(self.path, lambda data: RestoreTests.replace_users(data, ['123456', '654321']))
        self.window.refresh_subscribers(force=True)
        self.assertEqual(self.window.editor_value('push.qq.private_qq'), ['123456', '654321'])
        self.assertEqual(self.window.changes(), {'schedule.daily_report_time': '09:00'})
        self.assertIn('654321', [self.window.test_qq.itemText(index) for index in range(self.window.test_qq.count())])

    def test_external_subscription_does_not_overwrite_manual_recipient_edit(self):
        self.window.fields['push.qq.private_qq'][0].setPlainText('123456\n777777')
        atomic_update_json(self.path, lambda data: RestoreTests.replace_users(data, ['123456', '654321']))
        self.window.refresh_subscribers(force=True)
        self.assertEqual(self.window.editor_value('push.qq.private_qq'), ['123456', '777777'])
        self.assertIn('654321', self.window.subscription_status.text())

    def test_external_subscription_syncs_after_manual_edit_is_undone(self):
        self.window.refresh_subscribers()
        self.window.fields['push.qq.private_qq'][0].setPlainText('123456\n777777')
        atomic_update_json(self.path, lambda data: RestoreTests.replace_users(data, ['123456', '654321']))
        self.window.refresh_subscribers()
        self.window.fields['push.qq.private_qq'][0].setPlainText('123456')
        self.window.refresh_subscribers()
        self.assertEqual(self.window.editor_value('push.qq.private_qq'), ['123456', '654321'])

    def test_test_message_button_only_schedules_one_selected_qq(self):
        scheduled = []
        self.window.test_qq.setCurrentText('654321')
        with patch.object(self.window, 'run_background', side_effect=lambda name, callback: scheduled.append((name, callback)) or True):
            self.window.send_qq_test()
        with patch('news_agent.desktop.window.send_test_message', return_value={'ok': True}) as send:
            scheduled[0][1]()
            send.assert_called_once_with('http://127.0.0.1:3000', 'fake-key', '654321')
        self.assertEqual(scheduled[0][0], 'qq-test')

    def test_restore_only_offers_fresh_config_without_historical_modes(self):
        from PySide6.QtWidgets import QMessageBox
        with patch('news_agent.desktop.window.QMessageBox.question', return_value=QMessageBox.StandardButton.No) as picker:
            self.window.restore_defaults()
            self.assertIn('全新空配置', picker.call_args.args[2])
            self.assertNotIn('保留当前 QQ 订阅者', picker.call_args.args[2])


if __name__ == '__main__':
    unittest.main()
