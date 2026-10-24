"""Real Qt interaction regressions plus non-mutating service button wiring."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from news_agent.desktop.theme import STYLE
from news_agent.desktop.window import ConfigWindow
from news_agent.model_catalog import PROVIDER_PRESETS, PROVIDERS_BY_ID


class DesktopControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'config.json'
        self.data = {
            'push': {'enabled': ['qq'], 'qq': {'http_base': 'http://127.0.0.1:3000'}},
            'llm': {'primary': {'provider': 'agnes', 'model': 'news-test', 'api_key': 'keep-my-secret', 'base_url': 'https://example.com/v1'}},
            'chat': {'primary': {'provider': 'agnes', 'model': 'chat-test', 'base_url': 'https://example.com/v1'}},
        }
        self.path.write_text(json.dumps(self.data), encoding='utf-8')
        self.window = ConfigWindow(self.path, monitor_services=False)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.stop_monitoring()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_model_mode_clicks_switch_content_in_both_directions(self):
        self.window.select_page(2)
        self.app.processEvents()
        QTest.mouseClick(self.window.model_mode_buttons['chat'], Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(self.window.model_stack.currentIndex(), 1)
        self.assertTrue(self.window.fields['chat.primary.model'][0].isVisible())
        self.assertFalse(self.window.fields['llm.primary.model'][0].isVisible())
        QTest.mouseClick(self.window.model_mode_buttons['llm'], Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(self.window.model_stack.currentIndex(), 0)
        self.assertEqual(self.window.changes(), {})

    def test_switch_remains_clickable_after_scrolling_long_forms(self):
        self.window.select_page(2)
        area = self.window.model_stack.widget(0)
        self.app.processEvents()
        area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
        QTest.mouseClick(self.window.model_mode_buttons['chat'], Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.model_stack.currentIndex(), 1)

    def test_major_provider_preset_fills_protocol_and_url_without_overwriting_key(self):
        prefix = 'llm.primary'
        combo = self.window.model_presets[prefix]
        combo.setCurrentIndex(combo.findData('anthropic'))
        self.window.apply_model_preset(prefix)
        self.assertEqual(self.window.editor_value(prefix + '.adapter'), 'anthropic')
        self.assertEqual(self.window.editor_value(prefix + '.base_url'), PROVIDERS_BY_ID['anthropic'].base_url)
        self.assertEqual(self.window.editor_value(prefix + '.api_key'), 'keep-my-secret')
        self.assertEqual(self.window.editor_value('chat.primary.model'), 'chat-test')

    def test_custom_model_and_base_url_round_trip(self):
        prefix = 'llm.primary'
        combo = self.window.model_presets[prefix]
        combo.setCurrentIndex(combo.findData('custom'))
        self.window.apply_model_preset(prefix)
        self.window.fields[prefix + '.model'][0].setCurrentText('small-provider/my-own-model-42')
        self.window.fields[prefix + '.base_url'][0].setText('https://example.com/custom/v1')
        self.assertTrue(self.window.save())
        actual = json.loads(self.path.read_text(encoding='utf-8'))['llm']['primary']
        self.assertEqual(actual['model'], 'small-provider/my-own-model-42')
        self.assertEqual(actual['base_url'], 'https://example.com/custom/v1')
        self.assertEqual(actual['provider'], 'custom')

    def test_local_preset_can_be_saved_without_key(self):
        prefix = 'chat.primary'
        combo = self.window.model_presets[prefix]
        combo.setCurrentIndex(combo.findData('ollama'))
        self.window.apply_model_preset(prefix)
        self.assertFalse(self.window.editor_value(prefix + '.requires_api_key'))
        self.assertTrue(self.window.save())
        actual = json.loads(self.path.read_text(encoding='utf-8'))['chat']['primary']
        self.assertFalse(actual['requires_api_key'])

    def test_service_buttons_wire_to_explicit_background_commands(self):
        scheduled = []
        with patch.object(self.window, 'run_background', side_effect=lambda name, callback: scheduled.append((name, callback)) or True):
            self.window.start_all_services()
            self.window.stop_all_services()
        with patch.object(self.window.service_manager, 'start_all', return_value=['started']) as start:
            self.assertEqual(scheduled[0][1](), ['started'])
            start.assert_called_once()
        with patch.object(self.window.service_manager, 'stop_all', return_value=['stopped']) as stop:
            self.assertEqual(scheduled[1][1](), ['stopped'])
            stop.assert_called_once()

    def test_running_accelerator_does_not_fake_website_connectivity(self):
        self.window.background_finished('status', {
            'backend': {'ok': False, 'message': '后台未运行'},
            'steampp': {'ok': True, 'message': 'Steam++ 正在运行'},
        })
        self.window.background_finished('sites', {
            'github': {'ok': False, 'message': 'HTTP 403', 'elapsed_ms': 150},
            'steam': {'ok': True, 'message': '网站可访问', 'elapsed_ms': 100},
        })
        self.assertIn('正在运行', self.window.service_labels['steampp'].text())
        self.assertIn('403', self.window.site_labels['github'].text())
        self.assertIn('网站可访问', self.window.site_labels['steam'].text())

    def test_catalog_contains_major_platforms_and_custom(self):
        self.assertTrue({'openai', 'anthropic', 'gemini', 'deepseek', 'qwen', 'zhipu', 'moonshot', 'custom', 'ollama'} <= {p.id for p in PROVIDER_PRESETS})

    def test_legacy_provider_aliases_display_the_actual_protocol(self):
        for provider, adapter in [('responses', 'openai-responses'), ('claude', 'anthropic')]:
            self.window.store.data['llm']['primary']['provider'] = provider
            self.window.populate()
            self.assertEqual(self.window.editor_value('llm.primary.adapter'), adapter)
            self.assertEqual(self.window.changes(), {})

    def test_fractional_model_timeouts_survive_unrelated_save(self):
        role = self.window.store.data['llm']['primary']
        role.update(timeout=91.125, circuit_open_seconds=600.25)
        self.path.write_text(json.dumps(self.window.store.data), encoding='utf-8')
        self.window.populate()
        self.assertEqual(self.window.editor_value('llm.primary.timeout'), 91.125)
        self.assertEqual(self.window.editor_value('llm.primary.circuit_open_seconds'), 600.25)
        self.window.fields['llm.primary.model'][0].setCurrentText('changed-model')
        self.assertTrue(self.window.save())
        actual = json.loads(self.path.read_text(encoding='utf-8'))['llm']['primary']
        self.assertEqual(actual['timeout'], 91.125)
        self.assertEqual(actual['circuit_open_seconds'], 600.25)

    def test_unknown_process_inventory_is_not_reported_as_stopped(self):
        self.window.background_finished('status', {'backend': {'ok': False, 'state': 'unknown', 'message': '无法读取进程'}})
        self.assertEqual(self.window.header_status.text(), '后台状态未知')

    def test_malformed_qq_address_is_reported_without_starting_a_request(self):
        for value in ['http://[invalid', 'http://localhost:bad', 'http://localhost:70000', 'http://user:secret@localhost']:
            self.window.fields['push.qq.http_base'][0].setText(value)
            with patch('news_agent.desktop.window.QThreadPool') as pool:
                self.window.check_connection()
                pool.globalInstance.assert_not_called()
            self.assertIsNone(self.window.probe)
            self.assertIn('完整的 HTTP', self.window.probe_status.text())


if __name__ == '__main__':
    unittest.main()
