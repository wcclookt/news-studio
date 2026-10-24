"""Bounded diagnostics and project-scoped desktop service control.

Nothing starts at import time or during a status check. Process commands are
matched by executable/entry point and configuration, never by a broad name
such as ``python.exe`` or ``node.exe``.
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import ctypes
import hashlib
import json
import ntpath
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

import requests

from ..network import proxy_routes, route_get
from .napcat_qr import read_login_qr


def _value(config, path, default=None):
    node = getattr(config, 'data', config)
    for key in path.split('.'):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def _normal(path):
    return ntpath.normcase(ntpath.normpath(str(path or '')))


def _arguments(command):
    """Use the Windows command-line parser, including paths containing spaces."""
    if not command:
        return []
    if isinstance(command, list):
        return command
    if os.name == 'nt':
        from ctypes import wintypes
        argc = ctypes.c_int()
        parser = ctypes.windll.shell32.CommandLineToArgvW
        parser.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        parser.restype = ctypes.POINTER(wintypes.LPWSTR)
        argv = parser(str(command), ctypes.byref(argc))
        if not argv:
            return []
        try:
            return [argv[index] for index in range(argc.value)]
        finally:
            ctypes.windll.kernel32.LocalFree(ctypes.cast(argv, ctypes.c_void_p))
    try:
        return [part.strip('"') for part in shlex.split(str(command), posix=False)]
    except ValueError:
        return []


def _option(args, name, default=None):
    for index, argument in enumerate(args):
        if argument == name:
            return args[index + 1] if index + 1 < len(args) else None
        if argument.startswith(name + '='):
            return argument.split('=', 1)[1]
    return default


def _status(state, message, pids=(), **details):
    return dict(state=state, ok=state in {'running', 'online'},
                message=message, pids=list(pids), **details)


class ServiceManager:
    """Control this project's worker, bundled NapCat and Steam++.

    Public methods accept the current configuration dictionary (or Config).
    ``snapshot`` and ``check_sites`` only read process/network state. Every
    status has ``state``, ``ok``, ``message`` and ``pids``. Steam++ status also
    includes ``path`` and ``configured_path`` for displaying the selected exe.
    """

    SERVICE_NAME = 'news-agent'
    STEAM_NAMES = {'steam++.exe', 'watttoolkit.exe', 'watt toolkit.exe'}

    def __init__(self, project_root, config_path):
        self.project_root = Path(project_root).resolve()
        self.config_path = Path(config_path).resolve()
        self.run_path = self.project_root / 'run.py'
        self.napcat_root = self.project_root / 'NapCat.Shell.Windows.Node'
        identity = hashlib.sha256(_normal(self.config_path).encode()).hexdigest()[:16]
        self.runtime_path = self.project_root / 'logs' / f'desktop-runtime-{identity}.json'
        self._lock = threading.RLock()

    @contextmanager
    def _control_lock(self):
        """Serialise controls across UI windows, CLI wrappers and configurations."""
        with self._lock:
            if os.name != 'nt':
                yield True
                return
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            kernel.CreateMutexW.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            identity = hashlib.sha256(_normal(self.project_root).encode()).hexdigest()[:24]
            handle = kernel.CreateMutexW(None, False, 'Local\\NewsStudio-' + identity)
            if not handle:
                yield False
                return
            acquired = kernel.WaitForSingleObject(handle, 0) in (0, 0x80)
            try:
                yield acquired
            finally:
                if acquired:
                    kernel.ReleaseMutex(handle)
                kernel.CloseHandle(handle)

    @staticmethod
    def _command(command, timeout=10):
        return subprocess.run(command, capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=timeout,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def _inventory(self):
        if os.name != 'nt':
            raise OSError('Windows process inventory unavailable')
        # Registry values are needed for NSSM: Win32_Service.PathName describes
        # nssm.exe, not the Python command that it actually supervises.
        script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$processes = @(Get-CimInstance Win32_Process | ForEach-Object {
    [pscustomobject]@{pid=[int]$_.ProcessId; ppid=[int]$_.ParentProcessId;
        name=$_.Name; exe=$_.ExecutablePath; command=$_.CommandLine;
        created=$(if ($_.CreationDate) {$_.CreationDate.ToUniversalTime().ToFileTimeUtc().ToString()} else {''})}
})
$service = Get-CimInstance Win32_Service -Filter "Name='news-agent'"
$svc = $null
if ($service) {
    $parameters = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\news-agent\Parameters' -ErrorAction SilentlyContinue
    $svc = [pscustomobject]@{name=$service.Name; state=$service.State;
        pid=[int]$service.ProcessId; command=$service.PathName;
        application=$parameters.Application; arguments=$parameters.AppParameters;
        directory=$parameters.AppDirectory}
}
[pscustomobject]@{processes=$processes; service=$svc} | ConvertTo-Json -Depth 4 -Compress
"""
        encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        result = self._command(['powershell.exe', '-NoProfile', '-NonInteractive',
                                '-EncodedCommand', encoded], timeout=8)
        if result.returncode:
            raise OSError('Cannot read process/service state')
        payload = json.loads(result.stdout.lstrip('\ufeff'))
        if not isinstance(payload, dict) or not isinstance(payload.get('processes'), list):
            raise ValueError('Invalid process inventory')
        return payload

    def _resolved_argument(self, argument, directory=None):
        if ntpath.isabs(str(argument)):
            return _normal(argument)
        return _normal(ntpath.join(str(directory), str(argument))) if directory else ''

    def _backend_args(self, args, directory=None):
        """Require run.py to be the entry point, not an unrelated CLI argument."""
        if not args:
            return False
        entry = 1
        # Desktop launch uses -X utf8; also recognise normal Python switches.
        while entry < len(args) and args[entry].startswith('-'):
            if args[entry] in {'-c', '-m'}:
                return False
            entry += 2 if args[entry] in {'-X', '-W'} else 1
        if entry >= len(args) or self._resolved_argument(args[entry], directory) != _normal(self.run_path):
            return False
        tail = args[entry + 1:]
        if _option(tail, '--mode', 'service') not in {'service', 'qqbot'}:
            return False
        config = _option(tail, '--config', 'config.json' if directory else '')
        return bool(config) and self._resolved_argument(config, directory) == _normal(self.config_path)

    def _is_backend(self, process):
        if str(process.get('name', '')).lower() not in {'python.exe', 'pythonw.exe', 'py.exe', 'python', 'python3'}:
            return False
        return self._backend_args(_arguments(process.get('command')))

    def _backend_mode(self, process):
        return _option(_arguments(process.get('command')), '--mode', 'service') if self._is_backend(process) else None

    def _is_napcat(self, process):
        if _normal(process.get('exe')) != _normal(self.napcat_root / 'node.exe'):
            return False
        args = _arguments(process.get('command'))
        if len(args) < 2:
            return False
        # The dedicated bundled binary also supports the shipped node.exe
        # index.js launcher; a system node.exe is never selected.
        entry_matches = self._resolved_argument(args[1]) in {
            _normal(self.napcat_root / 'index.js'),
            _normal(self.napcat_root / 'napcat' / 'napcat.mjs'),
        } or _normal(args[1]) in {'index.js', '.\\index.js'}
        arguments_match = len(args) == 2 or (len(args) == 4 and args[2] in {'-q', '--qq'} and args[3].isdigit())
        return entry_matches and arguments_match

    def _napcat_processes(self, processes):
        matches = [p for p in processes if self._is_napcat(p)]
        # The shipped shell forks napcat.mjs and restarts a worker when it exits.
        # Stop its supervising index.js process first, then the exact worker.
        # Also recognise orphaned napcat.mjs workers after an earlier hard stop.
        return sorted(matches, key=lambda process: ntpath.basename(_arguments(process.get('command'))[1]).lower() != 'index.js')

    def _owns_service(self, service):
        if not service:
            return False
        application = service.get('application')
        if application:
            if ntpath.basename(str(application)).lower() not in {'python.exe', 'pythonw.exe', 'py.exe'}:
                return False
            directory = service.get('directory')
            return bool(directory and _normal(directory) == _normal(self.project_root)
                        and self._backend_args([application] + _arguments(service.get('arguments')), directory))
        args = _arguments(service.get('command'))
        return bool(args and ntpath.basename(args[0]).lower() in {'python.exe', 'pythonw.exe', 'py.exe'}
                    and self._backend_args(args))

    def _steam_candidates(self, config):
        configured = str(_value(config, 'desktop.steampp_path', '') or '').strip().strip('"')
        if configured:
            path = Path(configured)
            return [path if path.is_absolute() else self.project_root / path]
        return [self.project_root / 'steam++' / 'Steam++.exe',
                Path('D:/app/Steam++/Steam++.exe')]

    def _steam_processes(self, processes, config):
        candidates = {_normal(path) for path in self._steam_candidates(config)}
        return [process for process in processes
                if process.get('exe') and ( _normal(process['exe']) in candidates
                    or (str(process.get('name', '')).lower() in self.STEAM_NAMES
                        and ntpath.basename(process['exe']).lower() in self.STEAM_NAMES))]

    @staticmethod
    def _probe_qq(config):
        base = str(_value(config, 'push.qq.http_base', 'http://127.0.0.1:3000') or '')
        try:
            parsed = urlparse(base)
            if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
                return _status('error', 'QQ HTTP 地址无效，请填写不含密码的完整地址。')
            token = _value(config, 'push.qq.access_token', '')
            headers = {'Authorization': f'Bearer {token}'} if token else {}
            # Local bridge probes must not be routed through an ambient proxy.
            with requests.Session() as session:
                session.trust_env = False
                response = session.post(base.rstrip('/') + '/get_login_info', json={},
                                        headers=headers, timeout=(1.5, 2), allow_redirects=False)
                if response.status_code in {401, 403}:
                    return _status('offline', 'QQ 接口鉴权失败，请核对 NapCat Token。')
                if response.status_code != 200:
                    return _status('offline', f'QQ 接口返回 HTTP {response.status_code}。')
                payload = response.json()
            if isinstance(payload, dict) and payload.get('status') == 'ok' and isinstance(payload.get('data'), dict) and payload['data'].get('user_id'):
                return _status('online', 'QQ 已登录，HTTP 接口在线。')
            return _status('offline', 'NapCat 接口可访问，QQ 尚未登录或返回异常。')
        except requests.Timeout:
            return _status('offline', 'QQ HTTP 接口连接超时。')
        except Exception:
            return _status('offline', 'QQ HTTP 接口离线，请检查 NapCat、地址和鉴权。')

    @staticmethod
    def _probe_ws(config):
        try:
            port = int(_value(config, 'push.qq.ws_port', 3001))
            if not 1 <= port <= 65535:
                raise ValueError('invalid port')
            with socket.create_connection(('127.0.0.1', port), timeout=1.5):
                pass
            return _status('online', f'QQ WebSocket 端口 {port} 可连接（TCP 检查）。')
        except (ValueError, TypeError):
            return _status('error', 'QQ WebSocket 端口配置无效。')
        except OSError:
            return _status('offline', 'QQ WebSocket 端口未就绪。')

    def snapshot(self, config):
        try:
            inventory = self._inventory()
            processes = inventory['processes']
            service = inventory.get('service')
            backend = [p for p in processes if self._is_backend(p)]
            news_workers = [p for p in backend if self._backend_mode(p) == 'service']
            napcat = self._napcat_processes(processes)
            steam = self._steam_processes(processes, config)
            backend_status = _status('running' if news_workers else 'stopped',
                                     '新闻后台正在运行。' if news_workers else ('仅 QQ 互动后台运行，新闻后台未启动。' if backend else '新闻后台未运行。'),
                                     [p['pid'] for p in backend])
            if self._owns_service(service):
                state = str(service.get('state', '')).lower()
                if state == 'running':
                    backend_status = _status('running', '新闻后台 Windows 服务正在运行。', [service['pid']] if service.get('pid') else [], mode='windows_service')
                elif state in {'start pending', 'stop pending', 'continue pending', 'pause pending'}:
                    backend_status = _status('starting', 'Windows 服务正在切换状态，请稍后刷新。', mode='windows_service')
            configured = str(_value(config, 'desktop.steampp_path', '') or '')
            executable = next((path for path in self._steam_candidates(config) if path.is_file()), None)
            result = {
                'backend': backend_status,
                'napcat': _status('running' if napcat else 'stopped', 'NapCat 正在运行。' if napcat else 'NapCat 未运行。', [p['pid'] for p in napcat],
                                  processes=[{'pid': p['pid'], 'created': p.get('created', '')} for p in napcat]),
                'steampp': _status('running' if steam else 'stopped', 'Steam++ 正在运行；网站检测可确认实际连通性。' if steam else 'Steam++ 未运行。',
                                   [p['pid'] for p in steam], path=steam[0]['exe'] if steam else str(executable or ''), configured_path=configured),
            }
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            result = {key: _status('unknown', '暂时无法读取进程状态，请刷新重试。') for key in ('backend', 'napcat', 'steampp')}
            result['steampp'].update(path='', configured_path=str(_value(config, 'desktop.steampp_path', '') or ''))
        with ThreadPoolExecutor(max_workers=2) as pool:
            http = pool.submit(self._probe_qq, config)
            ws = pool.submit(self._probe_ws, config)
            result.update(qq_http=http.result(), qq_ws=ws.result())
        return result

    @staticmethod
    def _process_alive(process):
        if os.name != 'nt' or not str(process.get('created', '')).isdigit():
            return None
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000 | 0x00100000, False, int(process['pid']))
        if not handle:
            return None if ctypes.get_last_error() == 5 else False
        try:
            if kernel.WaitForSingleObject(handle, 0) != 0x102:
                return False
            created, exited, cpu, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(cpu), ctypes.byref(user)):
                return None
            actual = (created.dwHighDateTime << 32) | created.dwLowDateTime
            return actual // 10 == int(process['created']) // 10
        finally:
            kernel.CloseHandle(handle)

    def napcat_login_qr(self, napcat_status, qq_status=None):
        return read_login_qr(self.napcat_root, napcat_status,
                             logged_in=bool((qq_status or {}).get('ok')),
                             process_alive=self._process_alive)

    def _clear_napcat_qr(self):
        # Do not delete an externally configured work directory. The shipped
        # project's known cache path is the only cleanup target we own.
        path = self.napcat_root / 'napcat' / 'cache' / 'qrcode.png'
        try:
            if not path.resolve().is_relative_to(self.napcat_root.resolve()):
                return
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _remember(self, kind, process):
        try:
            records = json.loads(self.runtime_path.read_text(encoding='utf-8')) if self.runtime_path.exists() else {}
            if not isinstance(records, dict):
                records = {}
        except (OSError, ValueError):
            records = {}
        records[kind] = {key: process.get(key) for key in ('pid', 'created', 'exe', 'command')}
        self.runtime_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.runtime_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.runtime_path)

    def _launch(self, kind, args, cwd):
        """Spawn hidden workers with absolute entry/config paths and durable logs."""
        directory = self.project_root / 'logs'
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / f'{kind}.desktop.log').open('ab') as output:
            process = subprocess.Popen(args, cwd=str(cwd), stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        # Record WMI's creation identity as well as PID, so stale records are
        # harmless after Windows reuses an ID or the desktop is restarted.
        inventory = self._inventory()
        found = next((p for p in inventory['processes'] if p['pid'] == process.pid), None)
        if found:
            self._remember(kind, found)
        elif process.poll() is not None:
            raise OSError('Worker exited during startup')
        return process.pid

    def start_all(self, config):
        with self._control_lock() as acquired:
            if not acquired:
                return ['另一个窗口正在控制本项目的服务，请稍后刷新状态。']
            try:
                inventory = self._inventory()
            except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                return ['无法确认现有进程，已取消启动；请刷新状态后重试。']
            return [self._start_one(name, config, inventory) for name in ('steampp', 'napcat', 'backend')]

    def _start_one(self, name, config, inventory):
        processes = inventory['processes']
        if name == 'backend':
            return self._start_backend(inventory)
        if name == 'steampp':
            if self._steam_processes(processes, config):
                return 'Steam++ 已在运行。'
            executable = next((path for path in self._steam_candidates(config) if path.is_file()), None)
            if executable is None:
                return '未找到 Steam++，请在运行控制中设置程序路径。'
            try:
                os.startfile(str(executable.resolve()))
                return '已请求启动 Steam++，请在其窗口中确认加速状态。'
            except (OSError, AttributeError):
                return 'Steam++ 启动失败，请检查程序路径或手动启动。'
        if self._napcat_processes(processes):
            return 'NapCat 已在运行。'
        if not all((self.napcat_root / entry).is_file() for entry in ('node.exe', 'index.js')):
            return '未找到 NapCat 的 node.exe 或 index.js，请检查安装目录。'
        try:
            self._clear_napcat_qr()
            self._launch('napcat', [str(self.napcat_root / 'node.exe'), str(self.napcat_root / 'index.js')], self.napcat_root)
            return '已启动 NapCat；QQ 尚未登录时请打开 NapCat 管理页面登录。'
        except (OSError, ValueError, subprocess.SubprocessError):
            return 'NapCat 启动失败，请查看 logs/napcat.desktop.log。'

    def _target_processes(self, name, config, inventory):
        processes = inventory['processes']
        if name == 'backend':
            return [process for process in processes if self._is_backend(process)]
        return self._napcat_processes(processes) if name == 'napcat' else self._steam_processes(processes, config)

    def _target_active(self, name, config, inventory):
        if self._target_processes(name, config, inventory):
            return True
        service = inventory.get('service')
        return name == 'backend' and self._owns_service(service) and str(service.get('state', '')).lower() != 'stopped'

    def _wait_stopped(self, name, config, timeout=8):
        deadline = time.monotonic() + timeout
        while True:
            inventory = self._inventory()
            if not self._target_active(name, config, inventory):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(.25)

    def control_services(self, config, targets, action):
        """Start/stop/restart only explicitly selected services, under one mutex."""
        targets = set(targets)
        if not targets or not targets <= {'backend', 'napcat', 'steampp'} or action not in {'start', 'stop', 'restart'}:
            return ['无法执行：请选择有效的服务和操作。']
        labels = {'backend': '新闻与 QQ 互动后台', 'napcat': 'NapCat', 'steampp': 'Steam++'}
        with self._control_lock() as acquired:
            if not acquired:
                return ['另一个窗口正在控制本项目的服务，请稍后刷新状态。']
            messages, stopped = [], set()
            try:
                inventory = self._inventory()
                if action in {'stop', 'restart'}:
                    for name in ('backend', 'napcat', 'steampp'):
                        if name not in targets:
                            continue
                        was_active = self._target_active(name, config, inventory)
                        stop_notes = []
                        if name == 'backend':
                            stop_notes = self.stop_backend(config)
                        else:
                            processes = self._target_processes(name, config, inventory)
                            for process in processes:
                                self._terminate_process(process)
                        if self._wait_stopped(name, config):
                            stopped.add(name)
                            if name == 'napcat':
                                self._clear_napcat_qr()
                            messages.append(labels[name] + (' 已停止。' if was_active else ' 当前未运行。'))
                        else:
                            messages.extend(stop_notes)
                            messages.append(labels[name] + ' 未能完全停止，已取消对它的重启，避免重复进程。')
                        inventory = self._inventory()
                if action in {'start', 'restart'}:
                    for name in ('steampp', 'napcat', 'backend'):
                        if name in targets and (action == 'start' or name in stopped):
                            inventory = self._inventory()
                            messages.append(self._start_one(name, config, inventory))
                return messages
            except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                return messages + ['无法确认服务状态，操作已停止；请刷新后重试。']

    def _start_backend(self, inventory):
        service = inventory.get('service')
        owns_service = self._owns_service(service)
        if owns_service:
            state = str(service.get('state', '')).lower()
            if state == 'running':
                return '新闻后台 Windows 服务已在运行。当前 run.py 默认启动 QQ 互动；若无回复请检查 QQ 连接与日志。'
            if state != 'stopped':
                return '新闻后台 Windows 服务正在切换状态，请稍后刷新。'
        if any(self._backend_mode(p) == 'service' for p in inventory['processes']):
            # run.py currently calls run_loop(..., with_qqbot=True) regardless
            # of the optional --with-qqbot flag. Do not infer a missing QQ
            # worker from that flag or start a second bot alongside it.
            return '当前配置的新闻后台已在运行。当前 run.py 默认启动 QQ 互动；若无回复请检查 QQ 连接与日志。'
        if any(self._backend_mode(p) == 'qqbot' for p in inventory['processes']):
            return '仅 QQ 互动进程正在运行，无法启动新闻后台；请先停止后台再启动，以免重复回复。'
        if owns_service:
            try:
                response = self._command(['sc.exe', 'start', self.SERVICE_NAME])
                return '已请求启动新闻后台 Windows 服务。' if response.returncode == 0 else 'Windows 服务启动失败，请检查权限和服务日志。'
            except (OSError, subprocess.SubprocessError):
                return 'Windows 服务启动失败，请检查权限和服务日志。'
        if not self.run_path.is_file() or not self.config_path.is_file():
            return '未找到 run.py 或配置文件，无法启动新闻后台。'
        try:
            self._launch('backend', [sys.executable, '-X', 'utf8', str(self.run_path), '--mode', 'service', '--with-qqbot', '--config', str(self.config_path)], self.project_root)
            return '已启动新闻推送与 QQ 互动后台。'
        except (OSError, ValueError, subprocess.SubprocessError):
            return '新闻后台启动失败，请查看 logs/backend.desktop.log。'

    def start_backend(self, config):
        """Start only the worker; useful for scripts and autostart integration."""
        with self._control_lock() as acquired:
            if not acquired:
                return ['另一个窗口正在控制本项目的服务，请稍后刷新状态。']
            try:
                inventory = self._inventory()
            except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                return ['无法确认现有进程，已取消启动；请刷新状态后重试。']
            return [self._start_backend(inventory)]

    @staticmethod
    def _terminate_process(process):
        """Terminate the inspected process handle, never a recycled PID."""
        if os.name != 'nt' or not str(process.get('created', '')).isdigit():
            return False
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateProcess.restype = wintypes.BOOL
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x0001 | 0x1000 | 0x00100000, False, int(process['pid']))
        if not handle:
            return False
        try:
            created, exited, kernel_time, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel_time), ctypes.byref(user)):
                return False
            actual = (created.dwHighDateTime << 32) | created.dwLowDateTime
            # CIM serialises creation times to microsecond precision.
            if actual // 10 != int(process['created']) // 10:
                return False
            if not kernel.TerminateProcess(handle, 0):
                return False
            return kernel.WaitForSingleObject(handle, 3000) == 0
        finally:
            kernel.CloseHandle(handle)

    def stop_all(self, config):
        return self._stop(config, include_companions=True)

    def stop_backend(self, config):
        """Stop only the worker/owned Windows service, leaving QQ and Steam++."""
        return self._stop(config, include_companions=False)

    def _stop(self, config, include_companions):
        with self._control_lock() as acquired:
            if not acquired:
                return ['另一个窗口正在控制本项目的服务，请稍后刷新状态。']
            try:
                inventory = self._inventory()
            except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                return ['无法确认进程身份，已取消停止；请刷新状态后重试。']
            messages = []
            service = inventory.get('service')
            supervised = set()
            if self._owns_service(service) and service.get('pid'):
                supervised.add(service['pid'])
                # Leave NSSM's complete process tree to SCM, avoiding an
                # automatic restart while the stop command is still pending.
                while True:
                    children = {p['pid'] for p in inventory['processes'] if p.get('ppid') in supervised}
                    added = children - supervised
                    if not added:
                        break
                    supervised.update(added)
                try:
                    response = self._command(['sc.exe', 'stop', self.SERVICE_NAME])
                    messages.append('已请求停止新闻后台 Windows 服务。' if response.returncode == 0 else 'Windows 服务停止失败，请检查权限。')
                except (OSError, subprocess.SubprocessError):
                    messages.append('Windows 服务停止失败，请检查权限。')
            groups = [
                ('新闻后台', [p for p in inventory['processes'] if self._is_backend(p) and p['pid'] not in supervised]),
            ]
            if include_companions:
                groups.extend([
                    ('NapCat', self._napcat_processes(inventory['processes'])),
                    ('Steam++', self._steam_processes(inventory['processes'], config)),
                ])
            for name, processes in groups:
                if not processes:
                    if name == 'NapCat':
                        self._clear_napcat_qr()
                    if name != '新闻后台' or not supervised:
                        messages.append(f'{name} 未运行。')
                    continue
                stopped = 0
                for process in processes:
                    try:
                        stopped += bool(self._terminate_process(process))
                    except (OSError, ValueError, TypeError):
                        pass
                messages.append(f'{name} 已停止。' if stopped == len(processes) else f'{name} 部分进程未能停止，请刷新状态并检查权限。')
                if name == 'NapCat' and stopped == len(processes):
                    self._clear_napcat_qr()
            # Stale identities are harmless, but removing them keeps the runtime
            # manifest useful to operators. Failures must not mask stop results.
            if include_companions:
                try:
                    self.runtime_path.unlink(missing_ok=True)
                except OSError:
                    pass
            return messages

    @staticmethod
    def _check_site(url, proxy, expected_json=None):
        """Check exactly one route; an empty proxy means actual direct access.

        Empty values, rather than None, prevent Requests from silently adding
        HTTP_PROXY/ALL_PROXY or Windows registry proxies to a direct probe.
        """
        started = time.monotonic()
        try:
            response = route_get(url, proxy, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'},
                                 timeout=(2, 3), stream=True, allow_redirects=False)
            try:
                code = response.status_code
                valid_json = True
                if 200 <= code < 300 and expected_json:
                    content = bytearray()
                    for chunk in response.iter_content(chunk_size=16384):
                        content.extend(chunk)
                        if len(content) > 1024 * 1024 or time.monotonic() - started > 5:
                            raise requests.Timeout()
                    try:
                        payload = json.loads(bytes(content))
                        valid_json = isinstance(payload, dict) and isinstance(payload.get(expected_json), list)
                    except (ValueError, TypeError, UnicodeError):
                        valid_json = False
            finally:
                response.close()
            elapsed = round((time.monotonic() - started) * 1000)
            if 200 <= code < 300:
                if not valid_json:
                    return _status('offline', '网站可连接，但新闻接口返回的数据格式异常。', status_code=code, elapsed_ms=elapsed)
                return _status('online', f'可访问 · HTTP {code}', status_code=code, elapsed_ms=elapsed)
            return _status('offline', f'网站返回 HTTP {code}，请检查网络或代理。', status_code=code, elapsed_ms=elapsed)
        except requests.exceptions.SSLError:
            message = 'TLS 证书验证失败，请检查系统证书或 Steam++ 证书设置。'
        except requests.exceptions.ProxyError:
            message = '代理连接失败，可能是代理未启动、端口不正确或代理拒绝连接。'
        except requests.Timeout:
            message = '连接超时，请检查网络及代理。'
        except Exception:
            # Request exceptions can contain proxy passwords; never show them.
            message = '连接失败，请检查网络及代理。'
        return _status('offline', message, status_code=None,
                       elapsed_ms=round((time.monotonic() - started) * 1000))

    def _diagnostic_routes(self, url, configured):
        return proxy_routes(url, configured)

    def _diagnose_site(self, url, configured, expected_json=None):
        """Try the configured/default route, then bounded parallel alternatives.

        GitHub/Steam collection shares connection-failure route fallback.
        HTTP denial and invalid data remain failures of the collector route.
        """
        started = time.monotonic()
        routes, pac = self._diagnostic_routes(url, configured)

        def probe(route):
            result = self._check_site(url, route['proxy'], expected_json)
            # Return no raw proxy URLs: they may contain user/password tokens.
            return {**result, 'route': route['route'], 'route_label': route['route_label']}

        attempts = [probe(routes[0])]
        if not attempts[0]['ok'] and attempts[0].get('status_code') is None and len(routes) > 1:
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(probe, route) for route in routes[1:4]]
                attempts.extend(task.result() for task in futures)
        primary = attempts[0]
        chosen = next((attempt for attempt in attempts if attempt['ok']), primary)
        fallback = chosen is not primary
        result = dict(chosen)
        # Collection stops at the first HTTP response (including 403 or a bad
        # payload) instead of rotating routes to bypass a site's restriction.
        collection_attempt = next((attempt for attempt in attempts if attempt.get('status_code') is not None), primary)
        collection_ready = bool(collection_attempt['ok'])
        result.update(configured_ok=primary['ok'] if configured else None,
                      configured_error=primary['message'] if configured and not primary['ok'] else '',
                      used_fallback=fallback, collection_ready=collection_ready,
                      collection_route=collection_attempt['route'],
                      attempts=attempts, pac_detected=pac,
                      elapsed_ms=round((time.monotonic() - started) * 1000))
        if chosen['ok']:
            result['message'] = f"{chosen['route_label']}可访问 · HTTP {chosen['status_code']}"
            if fallback:
                result['message'] += f"；{primary['route_label']}失败：{primary['message']}"
                result['message'] += (' GitHub / Steam 采集会自动使用可用备用路线。' if collection_ready
                                      else ' 当前采集路线被网站拒绝，不会自动切换以绕过 HTTP 限制。')
                result['recommendation'] = ('可清空失效采集代理，避免其他尚未适配自动路线的来源仍连接旧端口。'
                                            if collection_ready else '请核对网站拒绝原因或修正采集代理后重试。')
            else:
                result['recommendation'] = ''
        else:
            result['message'] = '未找到可用路线。' + '；'.join(f"{attempt['route_label']}：{attempt['message']}" for attempt in attempts)
            result['recommendation'] = ('浏览器使用 PAC 自动代理，Python 无法直接执行 PAC；请填写有效的 HTTP/SOCKS 代理或使用系统 VPN 路线。'
                                        if pac else '请检查 VPN、Steam++ 加速和有效代理端口；浏览器可访问不代表所有程序使用同一路线。')
        return result

    def check_sites(self, config):
        # Test the same targets used by collection rather than a generic home page.
        from ..sources.steam import API_URL
        language = str(_value(config, 'sources.github_trending.language', '') or '').strip()
        since = str(_value(config, 'sources.github_trending.since', 'daily') or 'daily')
        from urllib.parse import quote
        github = 'https://github.com/trending' + ('/' + quote(language, safe='') if language else '')
        github += '?since=' + quote(since, safe='')
        proxy = str(_value(config, 'proxy') or '').strip() or None
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = {key: pool.submit(self._diagnose_site, url, proxy, 'items' if key == 'steam' else None)
                     for key, url in {'github': github, 'steam': API_URL}.items()}
            return {key: task.result() for key, task in tasks.items()}
