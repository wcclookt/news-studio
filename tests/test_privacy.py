"""Fresh resets and privacy cleanup use dummy values in temporary directories."""

from copy import deepcopy
import io
import json
import logging
from pathlib import Path
import tempfile
import unittest

from news_agent.config_io import atomic_update_json
from news_agent.config_store import ConfigStore,validate_config
from news_agent.factory_defaults import fresh_config,public_backup
from news_agent.privacy import PrivacyLogFilter,redact_text,register_private_values
from scripts.privacy_audit import audit,clean,collect_private


class PrivacyTests(unittest.TestCase):
    def test_fresh_defaults_are_independent_and_have_no_accounts(self):
        first=fresh_config()
        self.assertEqual(validate_config(first),[])
        self.assertEqual(first['push']['enabled'],[])
        for section in ('llm','chat'):
            for role in ('primary','fallback'):
                self.assertEqual(first[section][role]['api_key'],'')
                self.assertEqual(first[section][role]['base_url'],'')
        first['push']['qq']['private_qq'].append('123456')
        self.assertEqual(fresh_config()['push']['qq']['private_qq'],[])

    def test_backup_retains_only_safe_preferences_and_no_private_extensions(self):
        data=fresh_config()
        data['push']['email'].update(username='private@example.test',password='mail-authorization-code',to=['recipient@example.test'])
        data['push']['qq']['private_qq']=['123456789']
        data['llm']['primary']['api_key']='real-looking-private-api-key'
        data['custom_extension']={'personal_note':'private@example.test'}
        data['scoring']['major_threshold']=7.0
        data['schedule']['daily_report_time']='09:30'
        result=public_backup(data)
        self.assertEqual(result['scoring']['major_threshold'],7.0)
        self.assertEqual(result['schedule']['daily_report_time'],'09:30')
        for value in ('private@example.test','mail-authorization-code','123456789','real-looking-private-api-key','personal_note'):
            self.assertNotIn(value,json.dumps(result))

    def test_save_backup_and_factory_reset_never_copy_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json'
            data=fresh_config()
            data['push']['qq']['private_qq']=['123456789']
            data['llm']['primary'].update(api_key='real-looking-private-key',model='model',base_url='https://example.test/v1')
            path.write_text(json.dumps(data),encoding='utf-8')
            old=path.with_name('config.json.initial.json')
            old.write_text(json.dumps(data),encoding='utf-8')
            store=ConfigStore(path)
            store.save({'scoring.major_threshold':7.0})
            self.assertFalse(old.exists())
            backup=path.with_name('config.json.bak').read_text(encoding='utf-8')
            self.assertNotIn('real-looking-private-key',backup)
            self.assertNotIn('123456789',backup)
            store.restore('initial',preserve_subscribers=True)
            self.assertEqual(store.data,fresh_config())
            self.assertNotIn('real-looking-private-key',path.read_text(encoding='utf-8'))

    def test_log_filter_masks_registered_values_identifiers_mail_and_url_tokens(self):
        register_private_values({'api_key':'registered-secret-key','password':'mail-auth-private'})
        original='QQ 123456789, private@example.test, registered-secret-key mail-auth-private https://example.test/news?token=privatequery&x=ok'
        stream=io.StringIO()
        handler=logging.StreamHandler(stream)
        handler.addFilter(PrivacyLogFilter())
        logger=logging.getLogger('privacy-tests')
        logger.handlers=[handler]
        logger.setLevel(logging.INFO)
        logger.propagate=False
        logger.info('%s',original)
        result=stream.getvalue()
        for value in ('123456789','private@example.test','registered-secret-key','mail-auth-private','privatequery'):
            self.assertNotIn(value,result)
        self.assertIn('x=ok',result)

    def test_cleanup_removes_profiles_logs_and_history_without_creating_private_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'data').mkdir()
            config=fresh_config()
            config['llm']['primary']['api_key']='actual-private-key-to-remove'
            config['push']['qq']['private_qq']=['123456789']
            (root/'config.json').write_text(json.dumps(config),encoding='utf-8')
            (root/'config.json.initial.json').write_text(json.dumps(config),encoding='utf-8')
            (root/'config.json.bak').write_text(json.dumps(config),encoding='utf-8')
            (root/'logs').mkdir()
            (root/'logs'/'worker.log').write_text('123456789 actual-private-key-to-remove',encoding='utf-8')
            for relative in ('steam++/AppData','steam++/Cache','NapCat.Shell.Windows.Node/napcat/config'):
                folder=root/relative
                folder.mkdir(parents=True)
                (folder/'account.json').write_text('{"token":"actual-private-key-to-remove"}',encoding='utf-8')
            values=collect_private(root)
            self.assertTrue(audit(root,values))
            clean(root,values)
            self.assertEqual(audit(root,values),[])
            self.assertEqual(json.loads((root/'config.json').read_text(encoding='utf-8')),fresh_config())
            self.assertFalse((root/'config.json.bak').exists())
            self.assertFalse((root/'config.json.initial.json').exists())
            self.assertEqual(list((root/'logs').iterdir()),[])


if __name__=='__main__':
    unittest.main()
