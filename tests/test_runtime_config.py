"""Live settings update atomically; no model/network/process side effects."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from news_agent.factory_defaults import fresh_config
from news_agent.runtime_config import LiveConfig
from news_agent.llm import LLMClient
from news_agent.qq_bot import QQBot


class RuntimeConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'config.json'
        self.data=fresh_config()
        self.data['llm']['primary'].update(model='old-model',base_url='https://example.test/v1',api_key='fake-key')
        self.path.write_text(json.dumps(self.data),encoding='utf-8')
        self.live=LiveConfig(self.path,watch=False)

    def tearDown(self):
        self.live.close()
        self.temp.cleanup()

    def update(self,callback):
        callback(self.data)
        self.path.write_text(json.dumps(self.data),encoding='utf-8')
        return self.live.refresh()

    def test_whole_snapshot_callbacks_and_revision_change_after_save(self):
        callback=Mock()
        self.live.subscribe(callback)
        old=self.live.snapshot()
        self.assertTrue(self.update(lambda data:data['scoring'].update(major_threshold=7.0)))
        self.assertEqual(self.live.get('scoring.major_threshold'),7.0)
        self.assertEqual(old.get('scoring.major_threshold'),6.5)
        self.assertTrue(self.live.changed.is_set())
        callback.assert_called_once()
        self.assertFalse(self.live.refresh())

    def test_invalid_edit_keeps_last_valid_settings_without_callback(self):
        callback=Mock()
        self.live.subscribe(callback)
        self.path.write_text('{"api_key":"private-value",',encoding='utf-8')
        self.assertFalse(self.live.refresh())
        self.assertEqual(self.live.get('llm.primary.model'),'old-model')
        callback.assert_not_called()
        self.data['schedule']['realtime_interval_minutes']=0
        self.path.write_text(json.dumps(self.data),encoding='utf-8')
        self.assertFalse(self.live.refresh())

    def test_watcher_notices_disk_edit_without_waiting_for_scheduler_task(self):
        watcher=LiveConfig(self.path,poll_seconds=.1)
        try:
            self.data['llm']['primary']['model']='changed-while-scoring'
            self.path.write_text(json.dumps(self.data),encoding='utf-8')
            self.assertTrue(watcher.changed.wait(2))
            self.assertEqual(watcher.get('llm.primary.model'),'changed-while-scoring')
        finally:
            watcher.close()
        self.assertFalse(watcher._thread.is_alive())

    def test_existing_model_client_uses_new_model_key_and_endpoint_next_call(self):
        client=LLMClient(self.live)
        responses=[]
        def provider(config):
            responses.append(deepcopy(config))
            return Mock(last_usage={},complete=Mock(return_value='response'))
        with patch.object(client,'_build_provider',side_effect=provider):
            client.complete('system','first')
            self.update(lambda data:data['llm']['primary'].update(model='new-model',api_key='new-fake-key',base_url='https://new.example.test/v1'))
            client._circuit_open_until=float('inf')
            client.complete('system','second')
        self.assertEqual([config['model'] for config in responses],['old-model','new-model'])
        self.assertEqual(responses[1]['api_key'],'new-fake-key')
        self.assertEqual(responses[1]['base_url'],'https://new.example.test/v1')

    def test_qq_transport_reconnects_and_http_uses_updated_address_auth(self):
        bot=QQBot(self.live)
        socket=Mock()
        bot.ws=socket
        self.update(lambda data:data['push']['qq'].update(ws_port=3999,http_base='http://127.0.0.1:3888',access_token='changed-token'))
        socket.close.assert_called_once()
        self.assertEqual(bot.ws_url,'ws://127.0.0.1:3999')
        with patch('news_agent.qq_bot.requests.Session') as factory:
            response=factory.return_value.__enter__.return_value.post.return_value.__enter__.return_value
            response.status_code=200
            response.json.return_value={'status':'ok','data':{}}
            bot._call_http('get_login_info',{})
            post=factory.return_value.__enter__.return_value.post
            self.assertEqual(post.call_args.args[0],'http://127.0.0.1:3888/get_login_info')
            self.assertEqual(post.call_args.kwargs['headers']['Authorization'],'Bearer changed-token')
        bot.stop()

    def test_removed_subscriber_does_not_receive_config_callback(self):
        callback=Mock()
        unsubscribe=self.live.subscribe(callback)
        unsubscribe()
        self.update(lambda data:data['scoring'].update(major_threshold=7.0))
        callback.assert_not_called()

    def test_invalid_startup_fails_with_safe_message(self):
        self.path.write_text('{private-secret',encoding='utf-8')
        with self.assertRaises(ValueError) as error:
            LiveConfig(self.path,watch=False)
        self.assertNotIn('private-secret',str(error.exception))

    def test_inflight_model_request_keeps_snapshot_new_request_switches(self):
        client=LLMClient(self.live)
        settings=[]
        def provider(config):
            settings.append(deepcopy(config))
            def complete(system,user):
                if user=='first':
                    self.update(lambda data:data['llm']['primary'].update(model='changed-mid-request',api_key='another-fake-key'))
                return config['model']
            return Mock(last_usage={},complete=Mock(side_effect=complete))
        with patch.object(client,'_build_provider',side_effect=provider):
            self.assertEqual(client.complete('system','first'),'old-model')
            self.assertEqual(client.complete('system','second'),'changed-mid-request')
        self.assertEqual(settings[0]['api_key'],'fake-key')
        self.assertEqual(settings[1]['api_key'],'another-fake-key')


if __name__=='__main__':
    unittest.main()
