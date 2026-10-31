# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

"""Config values stay editable, safe to save, and compatible with QQ writes."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from news_agent.config_io import atomic_update_json
from news_agent.config_store import ConfigStore, validate_config, validate_config_structure


def model_config():
    return {
        'push': {'enabled': ['qq'], 'qq': {
            'http_base': 'http://127.0.0.1:3000', 'private_qq': [], 'group_id': '',
        }, 'email': {'username': '', 'password': '', 'to': []},
                 'telegram': {'bot_token': '', 'chat_id': ''}},
        'llm': {'primary': {
            'provider': 'custom', 'adapter': 'openai-compatible',
            'api_key': 'test-key', 'base_url': 'https://example.com/v1',
            'model': 'my-small-model', 'timeout': 2.5, 'retries': 0, 'max_tokens': 200000,
        }},
    }


class ValidationTests(unittest.TestCase):
    def test_qq_only_does_not_require_other_channel_credentials(self):
        self.assertEqual(validate_config(model_config()), [])

    def test_custom_provider_and_extensions_survive(self):
        cfg = model_config()
        cfg['llm']['primary']['provider'] = 'my-niche-service'
        cfg['llm']['primary']['x-extra'] = {'region': 'private'}
        before = deepcopy(cfg)
        self.assertEqual(validate_config(cfg), [])
        self.assertEqual(cfg, before)

    def test_local_model_can_run_without_a_key(self):
        cfg = model_config()
        cfg['llm']['primary'].update(provider='ollama', api_key='', requires_api_key=False)
        self.assertEqual(validate_config(cfg), [])
        cfg['llm']['primary']['model'] = ''
        self.assertTrue(any('模型名称' in error for error in validate_config(cfg)))

    def test_enabled_source_needs_positive_item_limit(self):
        for name in ('steam', 'hackernews', 'arxiv', 'github_trending'):
            with self.subTest(name=name):
                cfg = model_config()
                cfg['sources'] = {name: {'enabled': False, 'max_items': 0}}
                self.assertEqual(validate_config(cfg), [])
                cfg['sources'][name]['enabled'] = True
                self.assertTrue(any(f'{name}.max_items' in error for error in validate_config(cfg)))

    def test_invalid_model_numbers_are_rejected(self):
        for field, value in [('timeout', 0), ('timeout', float('inf')),
                             ('timeout', float('nan')), ('retries', -1), ('retries', 1.5),
                             ('max_tokens', 0), ('max_tokens', True),
                             ('circuit_failures', 0), ('circuit_open_seconds', -1)]:
            with self.subTest(field=field, value=value):
                cfg = model_config()
                cfg['llm']['primary'][field] = value
                self.assertTrue(any(field in error or 'Token' in error or '熔断' in error
                                    or '超时' in error or '重试' in error for error in validate_config(cfg)))

    def test_malformed_urls_are_errors_and_never_crash(self):
        for value in ('http://[broken', 'http://localhost:wrong', 'http://localhost:99999',
                      'http://host with spaces', 'ftp://example.com', 'https://'):
            with self.subTest(value=value):
                cfg = model_config()
                cfg['llm']['primary']['base_url'] = value
                self.assertTrue(any('API 地址' in error for error in validate_config(cfg)))
        cfg = model_config()
        cfg['proxy'] = 'socks5h://127.0.0.1:1080'
        self.assertEqual(validate_config(cfg), [])
        cfg['proxy'] = '127.0.0.1:1080'
        self.assertTrue(any('代理地址' in error for error in validate_config(cfg)))

    def test_structural_errors_do_not_coerce_or_echo_secrets(self):
        cfg = model_config()
        cfg['llm']['primary']['api_key'] = {'secret-token': True}
        cfg['sources'] = {'rss': [7], 'steam': {'enabled': 'false'}}
        errors = validate_config_structure(cfg)
        self.assertGreaterEqual(len(errors), 3)
        self.assertNotIn('secret-token', '\n'.join(errors))
        self.assertEqual(validate_config(cfg), errors)

    def test_structure_checks_dont_prevent_fixing_semantic_errors(self):
        cfg = model_config()
        cfg['llm']['primary']['timeout'] = -1
        cfg['sources'] = {'steam': {'enabled': True, 'max_items': 0}}
        self.assertEqual(validate_config_structure(cfg), [])
        self.assertTrue(validate_config(cfg))

    def test_unrepresentable_numbers_fail_clearly_before_qt_population(self):
        for key, value in [('timeout', float('nan')), ('timeout', float('inf')),
                           ('max_tokens', 2 ** 10000)]:
            with self.subTest(key=key):
                cfg = model_config()
                cfg['llm']['primary'][key] = value
                self.assertTrue(validate_config_structure(cfg))


class AtomicConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'config.json'
        self.cfg = model_config()
        self.path.write_text(json.dumps(self.cfg, ensure_ascii=False), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_editor_merges_latest_qq_subscriptions_and_backs_up_predecessor(self):
        store = ConfigStore(self.path)

        def subscribe(latest):
            latest['push']['qq']['private_qq'].append('123456')
            return latest

        atomic_update_json(self.path, subscribe)
        predecessor = self.path.read_bytes()
        result = store.save({'llm.primary.model': 'new-model'})
        self.assertEqual(result['push']['qq']['private_qq'], ['123456'])
        self.assertEqual(result['llm']['primary']['model'], 'new-model')
        from news_agent.factory_defaults import public_backup
        self.assertEqual(json.loads(self.path.with_name('config.json.bak').read_text(encoding='utf-8')),
                         public_backup(json.loads(predecessor)))
        self.assertEqual(store.data, result)

    def test_conflicts_and_invalid_changes_leave_file_and_backup_untouched(self):
        store = ConfigStore(self.path)
        store.save({'llm.primary.model': 'current-model'})
        stale = ConfigStore(self.path)
        atomic_update_json(self.path, lambda latest: dict(latest, **{'custom_extension': 42}))
        store.save({'llm.primary.model': 'newest-model'})
        original = self.path.read_bytes()
        backup = self.path.with_name('config.json.bak').read_bytes()
        for edits in ({'llm.primary.model': 'stale-model'}, {'llm.primary.retries': -1}):
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                stale.save(edits)
            self.assertEqual(self.path.read_bytes(), original)
            self.assertEqual(self.path.with_name('config.json.bak').read_bytes(), backup)

    def test_parallel_updates_keep_every_subscription(self):
        def subscribe(index):
            def update(latest):
                latest['push']['qq']['private_qq'].append(str(index))
                return latest
            return atomic_update_json(self.path, update)

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(subscribe, range(12)))
        actual = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(set(actual['push']['qq']['private_qq']), set(map(str, range(12))))
        self.assertEqual(actual['llm'], self.cfg['llm'])

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows byte-range locking')
    def test_empty_lock_byte_is_written_only_after_lock_acquisition(self):
        import os,msvcrt
        from unittest.mock import patch
        from news_agent.config_io import _config_lock
        locked=[]
        def locking(descriptor,mode,length):
            if mode==msvcrt.LK_NBLCK:
                self.assertEqual(os.fstat(descriptor).st_size,0)
                locked.append(True)
        with patch('msvcrt.locking',side_effect=locking):
            with _config_lock(self.path):
                self.assertEqual(self.path.with_name('config.json.lock').stat().st_size,1)
        self.assertEqual(locked,[True])

    def test_corrupt_input_is_not_overwritten(self):
        self.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            atomic_update_json(self.path, lambda data: data, backup=True)
        self.assertEqual(self.path.read_text(encoding='utf-8'), '{broken')
        self.assertFalse(self.path.with_name('config.json.bak').exists())

    def test_wrong_types_are_reported_when_opening_config(self):
        self.cfg['llm']['primary']['retries'] = '3'
        self.path.write_text(json.dumps(self.cfg), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'llm.primary.retries'):
            ConfigStore(self.path)


if __name__ == '__main__':
    unittest.main()
