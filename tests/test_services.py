# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

"""Mocked service controls: never start or stop the machine's real services."""

import json
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from contextlib import contextmanager, redirect_stdout

import requests

from news_agent.desktop.services import ServiceManager, _arguments, _status


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config_path = self.root / 'config.json'
        self.config_path.write_text('{}', encoding='utf-8')
        (self.root / 'run.py').write_text('', encoding='utf-8')
        self.napcat = self.root / 'NapCat.Shell.Windows.Node'
        self.napcat.mkdir()
        for name in ('node.exe', 'index.js'):
            (self.napcat / name).write_bytes(b'')
        self.steam = self.root / 'steam++' / 'Steam++.exe'
        self.steam.parent.mkdir()
        self.steam.write_bytes(b'')
        self.manager = ServiceManager(self.root, self.config_path)
        self.config = {'push': {'qq': {'http_base': 'http://127.0.0.1:3000', 'ws_port': 3001}}}

    def tearDown(self):
        self.temporary.cleanup()

    def process(self, pid, name='python.exe', args=None, exe=None, ppid=1):
        return {'pid': pid, 'ppid': ppid, 'name': name, 'created': '133000000000000000',
                'exe': str(exe or self.root / name),
                'command': subprocess.list2cmdline(args or [str(self.root / name), str(self.root / 'run.py'), '--mode', 'service', '--config', str(self.config_path)])}

    def inventory(self, processes=(), service=None):
        return {'processes': list(processes), 'service': service}

    def service(self, **changes):
        value = {'pid': 99, 'state': 'Running', 'command': 'nssm.exe',
                 'directory': str(self.root), 'application': 'C:/Python311/python.exe',
                 'arguments': subprocess.list2cmdline(['run.py', '--mode', 'service', '--with-qqbot', '--config', str(self.config_path)])}
        value.update(changes)
        return value

    def test_exact_backend_identity_rejects_gui_other_config_and_code_commands(self):
        self.assertTrue(self.manager._is_backend(self.process(11)))
        for args in (
            ['python.exe', str(self.root / 'config_ui.py'), '--config', str(self.config_path)],
            ['python.exe', str(self.root / 'run.py'), '--mode', 'daily', '--config', str(self.config_path)],
            ['python.exe', str(self.root / 'run.py'), '--config', str(self.root / 'other.json')],
            ['python.exe', '-c', 'print(1)', str(self.root / 'run.py'), '--config', str(self.config_path)],
            ['python.exe', 'unrelated.py', str(self.root / 'run.py'), '--config', str(self.config_path)],
        ):
            self.assertFalse(self.manager._is_backend(self.process(12, args=args)), args)

    def test_python_utf8_flags_and_paths_with_spaces_are_recognized(self):
        args = ['C:/Program Files/Python311/python.exe', '-X', 'utf8', str(self.root / 'run.py'), '--config=' + str(self.config_path)]
        self.assertEqual(_arguments(subprocess.list2cmdline(args)), args)
        self.assertTrue(self.manager._is_backend(self.process(11, args=args)))

    def test_napcat_requires_its_own_node_and_entry_point(self):
        valid = self.process(11, name='node.exe', exe=self.napcat / 'node.exe', args=[str(self.napcat / 'node.exe'), 'index.js'])
        self.assertTrue(self.manager._is_napcat(valid))
        valid['exe'] = 'C:/Program Files/nodejs/node.exe'
        self.assertFalse(self.manager._is_napcat(valid))
        valid['exe'] = str(self.napcat / 'node.exe')
        valid['command'] = subprocess.list2cmdline([valid['exe'], 'other.js', str(self.napcat / 'index.js')])
        self.assertFalse(self.manager._is_napcat(valid))

    def test_nssm_service_requires_matching_project_and_configuration(self):
        self.assertTrue(self.manager._owns_service(self.service()))
        self.assertFalse(self.manager._owns_service(self.service(directory=str(self.root / 'elsewhere'))))
        self.assertFalse(self.manager._owns_service(self.service(arguments='other.py')))
        self.assertFalse(self.manager._owns_service(self.service(application='arbitrary.exe')))

    def test_stop_is_scoped_and_never_targets_config_ui_or_unrelated_processes(self):
        backend = self.process(10)
        gui = self.process(11, args=['python.exe', str(self.root / 'config_ui.py')])
        other = self.process(12, args=['python.exe', str(self.root / 'run.py'), '--config', 'C:/other/config.json'])
        napcat = self.process(13, name='node.exe', exe=self.napcat / 'node.exe', args=[str(self.napcat / 'node.exe'), 'index.js'])
        steam = self.process(14, name='Steam++.exe', exe=self.steam, args=[str(self.steam)])
        with patch.object(self.manager, '_inventory', return_value=self.inventory([backend, gui, other, napcat, steam])), patch.object(self.manager, '_terminate_process', return_value=True) as terminate:
            messages = self.manager.stop_all(self.config)
        self.assertEqual({call.args[0]['pid'] for call in terminate.call_args_list}, {10, 13, 14})
        self.assertTrue(all('已停止' in message for message in messages))

    def test_windows_service_children_are_left_to_scm(self):
        backend = self.process(10, ppid=99)
        with patch.object(self.manager, '_inventory', return_value=self.inventory([backend], self.service())), patch.object(self.manager, '_command', return_value=MagicMock(returncode=0)) as command, patch.object(self.manager, '_terminate_process') as terminate:
            self.manager.stop_all(self.config)
        command.assert_called_once_with(['sc.exe', 'stop', 'news-agent'])
        terminate.assert_not_called()

    def test_napcat_stops_parent_before_forked_worker_and_recognizes_orphan(self):
        parent = self.process(13, name='node.exe', exe=self.napcat / 'node.exe', args=[str(self.napcat / 'node.exe'), 'index.js'])
        worker = self.process(14, name='node.exe', exe=self.napcat / 'node.exe', args=[str(self.napcat / 'node.exe'), str(self.napcat / 'napcat' / 'napcat.mjs')], ppid=13)
        self.assertTrue(self.manager._is_napcat(worker))
        with patch.object(self.manager, '_inventory', return_value=self.inventory([worker, parent])), patch.object(self.manager, '_terminate_process', return_value=True) as terminate:
            self.manager.stop_all(self.config)
        self.assertEqual([call.args[0]['pid'] for call in terminate.call_args_list], [13, 14])
        self.assertEqual(self.manager._napcat_processes([worker]), [worker])

    def test_backend_only_stop_keeps_companion_processes_alive(self):
        backend = self.process(10)
        napcat = self.process(13, name='node.exe', exe=self.napcat / 'node.exe', args=[str(self.napcat / 'node.exe'), 'index.js'])
        steam = self.process(14, name='Steam++.exe', exe=self.steam, args=[str(self.steam)])
        with patch.object(self.manager, '_inventory', return_value=self.inventory([backend, napcat, steam])), patch.object(self.manager, '_terminate_process', return_value=True) as terminate:
            messages = self.manager.stop_backend(self.config)
        self.assertEqual([call.args[0]['pid'] for call in terminate.call_args_list], [10])
        self.assertEqual(messages, ['新闻后台 已停止。'])

    def test_backend_only_start_does_not_launch_companions(self):
        with patch.object(self.manager, '_inventory', return_value=self.inventory()), patch.object(self.manager, '_launch', return_value=10) as launch, patch('os.startfile', create=True) as startfile:
            self.manager.start_backend(self.config)
        self.assertEqual([call.args[0] for call in launch.call_args_list], ['backend'])
        startfile.assert_not_called()

    def test_service_stopping_does_not_launch_fallback_worker(self):
        with patch.object(self.manager, '_inventory', return_value=self.inventory(service=self.service(state='Stop Pending'))), patch.object(self.manager, '_launch') as launch:
            messages = self.manager.start_backend(self.config)
        launch.assert_not_called()
        self.assertIn('正在切换', messages[0])

    def test_stopped_service_does_not_duplicate_existing_manual_worker(self):
        with patch.object(self.manager, '_inventory', return_value=self.inventory([self.process(10)], self.service(state='Stopped', pid=0))), patch.object(self.manager, '_command') as command, patch.object(self.manager, '_launch') as launch:
            messages = self.manager.start_backend(self.config)
        command.assert_not_called()
        launch.assert_not_called()
        self.assertIn('已在运行', messages[0])

    def test_cross_window_control_contention_never_mutates_processes(self):
        @contextmanager
        def busy():
            yield False
        with patch.object(self.manager, '_control_lock', side_effect=busy), patch.object(self.manager, '_inventory') as inventory:
            self.assertIn('另一个窗口', self.manager.start_all(self.config)[0])
            self.assertIn('另一个窗口', self.manager.stop_all(self.config)[0])
        inventory.assert_not_called()

    def test_unknown_inventory_cancels_actions(self):
        with patch.object(self.manager, '_inventory', side_effect=OSError('secret details')), patch.object(self.manager, '_launch') as launch, patch.object(self.manager, '_terminate_process') as terminate:
            self.assertIn('取消启动', self.manager.start_all(self.config)[0])
            self.assertIn('取消停止', self.manager.stop_all(self.config)[0])
        launch.assert_not_called()
        terminate.assert_not_called()

    def test_start_existing_workers_does_not_create_duplicates(self):
        backend = self.process(10)
        napcat = self.process(13, name='node.exe', exe=self.napcat / 'node.exe', args=[str(self.napcat / 'node.exe'), 'index.js'])
        steam = self.process(14, name='Steam++.exe', exe=self.steam, args=[str(self.steam)])
        with patch.object(self.manager, '_inventory', return_value=self.inventory([backend, napcat, steam])), patch.object(self.manager, '_launch') as launch, patch('os.startfile', create=True) as startfile:
            messages = self.manager.start_all(self.config)
        self.assertTrue(all('已在运行' in message for message in messages))
        launch.assert_not_called()
        startfile.assert_not_called()
        self.assertIn('默认启动 QQ 互动', messages[-1])

    def test_news_worker_status_does_not_claim_qq_thread_is_healthy(self):
        backend = self.process(10)
        self.assertNotIn('--with-qqbot', _arguments(backend['command']))
        with patch.object(self.manager, '_inventory', return_value=self.inventory([backend])), patch.object(self.manager, '_probe_qq', return_value=_status('offline', 'offline')), patch.object(self.manager, '_probe_ws', return_value=_status('offline', 'offline')):
            result = self.manager.snapshot(self.config)
        self.assertTrue(result['backend']['ok'])
        self.assertEqual(result['backend']['message'], '新闻后台正在运行。')
        self.assertFalse(result['qq_http']['ok'])

    def test_steam_launch_error_is_safe_and_other_services_can_start(self):
        with patch.object(self.manager, '_inventory', return_value=self.inventory()), patch.object(self.manager, '_launch', return_value=10), patch('os.startfile', create=True, side_effect=OSError('elevation required')):
            messages = self.manager.start_all(self.config)
        self.assertIn('Steam++ 启动失败', messages[0])
        self.assertIn('已启动新闻', messages[-1])

    def test_matching_service_failure_does_not_launch_second_backend(self):
        with patch.object(self.manager, '_inventory', return_value=self.inventory(service=self.service(state='Stopped', pid=0))), patch.object(self.manager, '_command', return_value=MagicMock(returncode=5)), patch.object(self.manager, '_launch') as launch, patch('os.startfile', create=True):
            messages = self.manager.start_all(self.config)
        self.assertIn('Windows 服务启动失败', ' '.join(messages))
        self.assertEqual([call.args[0] for call in launch.call_args_list], ['napcat'])

    def test_fallback_launch_uses_absolute_project_and_config_paths(self):
        with patch.object(self.manager, '_inventory', return_value=self.inventory()), patch.object(self.manager, '_launch', return_value=10) as launch, patch('os.startfile', create=True) as startfile:
            self.manager.start_all(self.config)
        self.assertEqual([call.args[0] for call in launch.call_args_list], ['napcat', 'backend'])
        args = launch.call_args_list[1].args[1]
        self.assertIn(str(self.root / 'run.py'), args)
        self.assertEqual(args[args.index('--config') + 1], str(self.config_path))
        startfile.assert_called_once_with(str(self.steam.resolve()))

    def test_runtime_record_is_per_configuration_and_has_creation_identity(self):
        process = self.process(10)
        self.manager._remember('backend', process)
        data = json.loads(self.manager.runtime_path.read_text(encoding='utf-8'))
        self.assertEqual(data['backend']['created'], process['created'])
        self.assertNotEqual(self.manager.runtime_path, ServiceManager(self.root, self.root / 'other.json').runtime_path)

    def test_snapshot_reports_steam_process_without_claiming_acceleration(self):
        steam = self.process(14, name='Steam++.exe', exe='C:/Apps/Steam++/Steam++.exe', args=['C:/Apps/Steam++/Steam++.exe'])
        with patch.object(self.manager, '_inventory', return_value=self.inventory([steam])), patch.object(self.manager, '_probe_qq', return_value=_status('online', 'ok')), patch.object(self.manager, '_probe_ws', return_value=_status('offline', 'not ready')):
            result = self.manager.snapshot(self.config)
        self.assertEqual(result['steampp']['state'], 'running')
        self.assertEqual(result['steampp']['path'], steam['exe'])
        self.assertIn('连通性', result['steampp']['message'])
        self.assertFalse(result['qq_ws']['ok'])

    def test_qqbot_only_process_does_not_claim_news_running_or_spawn_duplicate_bot(self):
        qqbot = self.process(10, args=['python.exe', str(self.root / 'run.py'), '--mode', 'qqbot', '--config', str(self.config_path)])
        with patch.object(self.manager, '_inventory', return_value=self.inventory([qqbot])), patch.object(self.manager, '_probe_qq', return_value=_status('online', 'ok')), patch.object(self.manager, '_probe_ws', return_value=_status('online', 'ok')), patch.object(self.manager, '_launch') as launch:
            status = self.manager.snapshot(self.config)
            messages = self.manager.start_backend(self.config)
        self.assertFalse(status['backend']['ok'])
        self.assertEqual(status['backend']['state'], 'stopped')
        self.assertIn('仅 QQ 互动', status['backend']['message'])
        self.assertEqual(status['backend']['pids'], [10])
        self.assertIn('重复回复', messages[0])
        launch.assert_not_called()

    def test_qq_probe_uses_readonly_login_action_and_no_environment_proxy(self):
        self.config['push']['qq']['access_token'] = 'secret-token'
        response = MagicMock(status_code=200)
        response.json.return_value = {'status': 'ok', 'data': {'user_id': 123456}}
        with patch('news_agent.desktop.services.requests.Session') as factory:
            session = factory.return_value.__enter__.return_value
            session.post.return_value = response
            self.assertTrue(self.manager._probe_qq(self.config)['ok'])
        self.assertFalse(session.trust_env)
        self.assertEqual(session.post.call_args.args, ('http://127.0.0.1:3000/get_login_info',))
        self.assertEqual(session.post.call_args.kwargs['headers'], {'Authorization': 'Bearer secret-token'})
        self.assertFalse(session.post.call_args.kwargs['allow_redirects'])

    def test_ws_probe_closes_socket_and_distinguishes_invalid_port(self):
        with patch('news_agent.desktop.services.socket.create_connection') as connect:
            self.assertTrue(self.manager._probe_ws(self.config)['ok'])
        connect.assert_called_once_with(('127.0.0.1', 3001), timeout=1.5)
        connect.return_value.__exit__.assert_called_once()
        self.config['push']['qq']['ws_port'] = 99999
        self.assertEqual(self.manager._probe_ws(self.config)['state'], 'error')

    @unittest.skipUnless(os.name == 'nt', 'Windows process identity guard')
    def test_termination_rejects_recycled_pid_before_terminating(self):
        api = MagicMock()
        api.OpenProcess.return_value = 5

        def times(handle, created, exited, kernel, user):
            created._obj.dwHighDateTime = 0
            created._obj.dwLowDateTime = 123
            return 1

        api.GetProcessTimes.side_effect = times
        with patch('news_agent.desktop.services.ctypes.WinDLL', return_value=api):
            self.assertFalse(self.manager._terminate_process(self.process(10)))
        api.TerminateProcess.assert_not_called()
        api.CloseHandle.assert_called_once_with(5)

    def test_site_http_403_is_offline_and_response_is_closed(self):
        response = MagicMock(status_code=403)
        with patch('news_agent.desktop.services.route_get', return_value=response):
            result = self.manager._check_site('https://github.com/trending', None)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status_code'], 403)
        response.close.assert_called_once()

    def test_website_probe_matches_collector_urls_proxy_and_json(self):
        from news_agent.sources.steam import API_URL
        self.config.update(proxy='http://user:secret@127.0.0.1:26561', sources={'github_trending': {'language': 'python', 'since': 'weekly'}})

        def response(url, proxy, **kwargs):
            result = MagicMock(status_code=200)
            result.iter_content.return_value = [b'{"items": []}']
            return result

        with patch('news_agent.desktop.services.route_get', side_effect=response) as get:
            statuses = self.manager.check_sites(self.config)
        self.assertTrue(all(status['ok'] for status in statuses.values()))
        self.assertEqual({call.args[0] for call in get.call_args_list}, {'https://github.com/trending/python?since=weekly', API_URL})
        for call in get.call_args_list:
            self.assertEqual(call.args[1], self.config['proxy'])
            self.assertEqual(call.kwargs['timeout'], (2, 3))
            self.assertFalse(call.kwargs['allow_redirects'])

    def test_steam_html_with_http_200_is_not_reported_as_success(self):
        response = MagicMock(status_code=200)
        response.iter_content.return_value = [b'<html>blocked</html>']
        with patch('news_agent.desktop.services.route_get', return_value=response):
            self.assertFalse(self.manager._check_site('https://store.steampowered.com', None, 'items')['ok'])
        response.close.assert_called_once()

    def test_proxy_failure_never_leaks_credentials(self):
        with patch('news_agent.desktop.services.route_get', side_effect=requests.exceptions.ProxyError('http://user:secret@proxy')):
            result = self.manager._check_site('https://github.com/trending', 'http://user:secret@proxy')
        self.assertFalse(result['ok'])
        self.assertNotIn('secret', result['message'])

    def test_failed_configured_proxy_reports_actual_successful_direct_route(self):
        routes = [
            {'route': 'configured', 'route_label': '配置代理', 'proxy': 'http://user:secret@127.0.0.1:26561'},
            {'route': 'direct', 'route_label': '直连（含系统 VPN / DNS 加速路线）', 'proxy': None},
        ]
        response = MagicMock(status_code=200)
        with patch.object(self.manager, '_diagnostic_routes', return_value=(routes, False)), patch('news_agent.desktop.services.route_get', side_effect=[requests.exceptions.ProxyError('user:secret'), response]):
            status = self.manager._diagnose_site('https://github.com/trending', routes[0]['proxy'])
        self.assertTrue(status['ok'])
        self.assertTrue(status['collection_ready'])
        self.assertFalse(status['configured_ok'])
        self.assertTrue(status['used_fallback'])
        self.assertEqual(status['route'], 'direct')
        self.assertIn('代理连接失败', status['configured_error'])
        self.assertIn('自动使用可用备用路线', status['message'])
        self.assertNotIn('secret', json.dumps(status))
        response.close.assert_called_once()

    def test_system_proxy_success_is_named_and_not_misreported_as_direct(self):
        routes = [
            {'route': 'configured', 'route_label': '配置代理', 'proxy': 'http://bad'},
            {'route': 'system', 'route_label': 'Windows 系统代理', 'proxy': 'http://good'},
            {'route': 'direct', 'route_label': '直连（含系统 VPN / DNS 加速路线）', 'proxy': None},
        ]

        def route(url, proxy, **kwargs):
            if proxy == 'http://good':
                return MagicMock(status_code=200)
            raise requests.ConnectionError('failure')

        with patch.object(self.manager, '_diagnostic_routes', return_value=(routes, False)), patch('news_agent.desktop.services.route_get', side_effect=route):
            status = self.manager._diagnose_site('https://github.com/trending', 'http://bad')
        self.assertTrue(status['ok'])
        self.assertTrue(status['collection_ready'])
        self.assertEqual(status['route'], 'system')
        self.assertIn('Windows 系统代理可访问', status['message'])

    def test_all_routes_fail_with_pac_note_without_false_success(self):
        routes = [
            {'route': 'configured', 'route_label': '配置代理', 'proxy': 'http://bad'},
            {'route': 'direct', 'route_label': '直连（含系统 VPN / DNS 加速路线）', 'proxy': None},
        ]
        with patch.object(self.manager, '_diagnostic_routes', return_value=(routes, True)), patch('news_agent.desktop.services.route_get', side_effect=requests.Timeout('secret proxy token')):
            status = self.manager._diagnose_site('https://github.com/trending', 'http://bad')
        self.assertFalse(status['ok'])
        self.assertFalse(status['collection_ready'])
        self.assertTrue(status['pac_detected'])
        self.assertEqual(len(status['attempts']), 2)
        self.assertIn('PAC', status['recommendation'])
        self.assertNotIn('secret', json.dumps(status))

    def test_http_denial_does_not_cycle_proxy_routes(self):
        routes = [
            {'route': 'configured', 'route_label': '配置代理', 'proxy': 'http://bad'},
            {'route': 'direct', 'route_label': '直连', 'proxy': None},
        ]
        with patch.object(self.manager, '_diagnostic_routes', return_value=(routes, False)), patch('news_agent.desktop.services.route_get', return_value=MagicMock(status_code=403)) as get:
            status = self.manager._diagnose_site('https://github.com/trending', 'http://bad')
        self.assertFalse(status['ok'])
        self.assertFalse(status['collection_ready'])
        self.assertEqual(status['status_code'], 403)
        get.assert_called_once()

    def test_alternate_direct_success_does_not_hide_earlier_http_denial_from_collection(self):
        routes = [
            {'route': 'configured', 'route_label': '配置代理', 'proxy': 'http://bad'},
            {'route': 'system', 'route_label': 'Windows 系统代理', 'proxy': 'http://denied'},
            {'route': 'direct', 'route_label': '直连', 'proxy': None},
        ]

        def route(url, proxy, **kwargs):
            if proxy == 'http://bad':
                raise requests.ConnectionError()
            return MagicMock(status_code=403 if proxy == 'http://denied' else 200)

        with patch.object(self.manager, '_diagnostic_routes', return_value=(routes, False)), patch('news_agent.desktop.services.route_get', side_effect=route):
            status = self.manager._diagnose_site('https://github.com/trending', 'http://bad')
        self.assertTrue(status['ok'])
        self.assertFalse(status['collection_ready'])
        self.assertEqual(status['route'], 'direct')
        self.assertEqual(status['collection_route'], 'system')
        self.assertIn('不会自动切换', status['message'])


class ServiceCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[1]
        specification = importlib.util.spec_from_file_location('manage_services_test', cls.root / 'scripts' / 'manage_services.py')
        cls.cli = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(cls.cli)

    def test_stop_backend_still_works_with_missing_or_broken_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'missing.json'
            with patch.object(self.cli, 'ServiceManager') as manager, redirect_stdout(io.StringIO()):
                manager.return_value.stop_backend.return_value = ['新闻后台已停止。']
                self.assertEqual(self.cli.main(['--action', 'stop-backend', '--config', str(config)]), 0)
                manager.return_value.stop_backend.assert_called_once_with({})
                manager.return_value.stop_all.assert_not_called()

    def test_cli_validates_before_starting_and_never_prints_secret_values(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.json'
            config.write_text(json.dumps({'push': {'enabled': ['telegram'], 'telegram': {'token': 'secret'}}}), encoding='utf-8')
            output = io.StringIO()
            with patch.object(self.cli, 'ServiceManager') as manager, redirect_stdout(output):
                self.assertEqual(self.cli.main(['--action', 'start', '--config', str(config)]), 1)
                manager.assert_not_called()
            self.assertNotIn('secret', output.getvalue())

    def test_cli_rejects_relative_config(self):
        with redirect_stdout(io.StringIO()), patch('sys.stderr', new=io.StringIO()):
            with self.assertRaises(SystemExit):
                self.cli.main(['--action', 'status', '--config', 'config.json'])

    def test_cli_qqbot_only_refusal_returns_failure_without_launching_news(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.json'
            config.write_text('{}', encoding='utf-8')
            real_manager = ServiceManager(self.root, config)
            qqbot = {'pid': 10, 'ppid': 1, 'name': 'python.exe',
                     'created': '133000000000000000', 'exe': 'C:/Python311/python.exe',
                     'command': subprocess.list2cmdline(['python.exe', str(self.root / 'run.py'),
                                                       '--mode', 'qqbot', '--config', str(config)])}
            with patch.object(self.cli, 'ServiceManager', return_value=real_manager), patch.object(real_manager, '_inventory', return_value={'processes': [qqbot], 'service': None}), patch.object(real_manager, '_launch') as launch, redirect_stdout(io.StringIO()) as output:
                result = self.cli.main(['--action', 'start-backend', '--config', str(config)])
            self.assertEqual(result, 1)
            self.assertIn('无法启动新闻后台', output.getvalue())
            launch.assert_not_called()

    def test_wrappers_select_scoped_cli_actions_and_ui_python_discovery(self):
        actions = {'start_all.bat': 'start', 'stop_all.bat': 'stop',
                   'start_service.bat': 'start-backend', 'stop_service.bat': 'stop-backend'}
        for filename, action in actions.items():
            contents = (self.root / 'scripts' / filename).read_text(encoding='ascii')
            self.assertIn(f'manage_services.py --action {action} --config "%CD%\\config.json"', contents)
            self.assertIn('.venv\\Scripts\\python.exe', contents)
            self.assertIn('py -3 -X utf8', contents)
            self.assertIn('--no-pause', contents)
            self.assertNotIn('taskkill', contents.lower())
            self.assertNotIn('Stop-Process', contents)


if __name__ == '__main__':
    unittest.main()
