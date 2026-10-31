# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

"""Chinese CLI for the same safe service controls used by the desktop UI."""

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from news_agent.config_store import validate_config
from news_agent.desktop.services import ServiceManager


def absolute_config(value):
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError('配置文件必须使用绝对路径')
    return path.resolve()


def main(argv=None):
    parser = argparse.ArgumentParser(description='安全管理本项目的新闻后台、NapCat 与 Steam++')
    parser.add_argument('--action', required=True,
                        choices=('status', 'start', 'stop', 'start-backend', 'stop-backend', 'sites',
                                 'start-selected', 'stop-selected', 'restart-selected'))
    parser.add_argument('--services', nargs='+', choices=('backend', 'napcat', 'steampp'), default=['backend'],
                        help='所选服务；仅用于 *-selected 操作')
    parser.add_argument('--config', type=absolute_config, default=PROJECT_ROOT / 'config.json')
    # The .bat wrappers consume this preference after Python returns.
    parser.add_argument('--no-pause', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--with-qqbot', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError('配置必须是 JSON 对象')
    except (OSError, ValueError, UnicodeError):
        if args.action not in {'stop', 'stop-backend', 'stop-selected'}:
            print('无法读取配置文件，请检查文件路径与 JSON 格式。')
            return 1
        # Even a broken config must remain stoppable. Worker identity still
        # requires this exact absolute config path and the project's run.py.
        config = {}
        print('配置读取失败，继续按本项目及配置路径核对进程身份后停止。')
    if args.action in {'start', 'start-backend'} or args.action in {'start-selected', 'restart-selected'} and 'backend' in args.services:
        errors = validate_config(config)
        if errors:
            print('启动前请修正配置：')
            for error in errors:
                print('  ' + error)
            return 1
    manager = ServiceManager(PROJECT_ROOT, args.config)
    if args.action.endswith('-selected'):
        messages = manager.control_services(config, args.services, args.action.split('-')[0])
        for message in messages:
            print(message)
        return int(any(word in message for message in messages for word in ('失败', '无法', '未能', '未找到', '取消', '另一个窗口')))
    if args.action in {'status', 'sites'}:
        statuses = manager.snapshot(config) if args.action == 'status' else manager.check_sites(config)
        names = {'backend': '新闻后台', 'napcat': 'NapCat', 'steampp': 'Steam++',
                 'qq_http': 'QQ HTTP 接口', 'qq_ws': 'QQ WebSocket 端口',
                 'github': 'GitHub Trending', 'steam': 'Steam 新闻接口'}
        for key, status in statuses.items():
            message = status['message']
            if status.get('elapsed_ms') is not None:
                message += f" · {status['elapsed_ms']} ms"
            print(f"{names.get(key, key)}：{message}")
        return 0
    action = {'start': manager.start_all, 'stop': manager.stop_all,
              'start-backend': manager.start_backend, 'stop-backend': manager.stop_backend}[args.action]
    messages = action(config)
    for message in messages:
        print(message)
    return int(any(word in message for message in messages
                   for word in ('失败', '无法', '未能', '未找到', '取消', '另一个窗口')))


if __name__ == '__main__':
    raise SystemExit(main())
