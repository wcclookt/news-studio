"""Account-free defaults; never derive a reset from historical user files."""

from copy import deepcopy
import re


def fresh_config():
    model = {'provider': 'custom', 'adapter': 'openai-compatible', 'api_key': '',
             'model': '', 'base_url': '', 'timeout': 90, 'retries': 1, 'max_tokens': 8192}
    return {
        '_comment': '全新配置，不包含历史账号、密钥、QQ 号或订阅者。填写完成后手动启用推送渠道。',
        'cache': {'auto_clear': True, 'interval_days': 7, 'clear_time': '03:00'},
        'chat': {'primary': deepcopy(model), 'fallback': deepcopy(model)},
        'llm': {'primary': {**model, 'circuit_open_seconds': 600, 'circuit_failures': 3},
                'fallback': deepcopy(model)},
        'proxy': '',
        'schedule': {'realtime_interval_minutes': 10, 'daily_report_time': '08:00'},
        'scoring': {'realtime_threshold': 7.5, 'major_threshold': 6.5, 'min_for_daily': 6,
                    'doubt_threshold': 8.5, 'max_realtime_per_cycle': 30,
                    'max_daily_items': 40, 'llm_batch_size': 8},
        'sources': {'rss': [
            {'name': 'IT之家', 'url': 'https://www.ithome.com/rss/'},
            {'name': '少数派', 'url': 'https://sspai.com/feed'},
            {'name': '爱范儿', 'url': 'https://www.ifanr.com/feed'},
            {'name': 'Nature', 'url': 'https://www.nature.com/nature.rss'},
            {'name': 'ScienceDaily', 'url': 'https://www.sciencedaily.com/rss/top/science.xml'},
            {'name': 'ArsTechnica', 'url': 'https://feeds.arstechnica.com/arstechnica/index'},
        ], 'rss_max_per_source': 12,
            'github_trending': {'enabled': True, 'language': '', 'since': 'daily', 'max_items': 20},
            'steam': {'enabled': True, 'max_items': 15},
            'arxiv': {'enabled': True, 'categories': ['cs.AI', 'cs.CL', 'cs.LG'], 'max_items': 12},
            'hackernews': {'enabled': False, 'max_items': 0}},
        'push': {'enabled': [],
                 'qq': {'http_base': 'http://127.0.0.1:3000', 'ws_port': 3001,
                        'access_token': '', 'private_qq': [], 'group_id': ''},
                 'email': {'smtp_host': 'smtp.163.com', 'smtp_port': 465,
                           'username': '', 'password': '', 'to': []},
                 'telegram': {'bot_token': '', 'chat_id': '', 'proxy': ''}},
        'desktop': {'steampp_path': ''},
    }


def public_backup(data):
    """Back up only explicitly harmless numeric/scheduling preferences.

    Unknown extensions, addresses, identifiers and credentials must not become
    another copy of private configuration. This is not a credential recovery.
    """
    result = fresh_config()
    for section in ('scoring', 'schedule', 'cache'):
        if not isinstance(data.get(section), dict):
            continue
        for key, default in result[section].items():
            value = data[section].get(key, default)
            if type(value) is type(default) or type(default) is float and type(value) in (int, float):
                if not isinstance(default, str) or key.endswith('time') and re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
                    result[section][key] = value
    return result
