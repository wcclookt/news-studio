"""Configuration editing without losing comments or concurrent subscription changes."""

from copy import deepcopy
import json
import math
from pathlib import Path
import re
from urllib.parse import urlparse


MISSING = object()


def get_value(data, path, default=None):
    node = data
    for key in path.split('.'):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def set_value(data, path, value):
    keys = path.split('.')
    node = data
    for key in keys[:-1]:
        if key not in node:
            node[key] = {}
        if not isinstance(node[key], dict):
            raise ValueError(f'{path} 的上级配置必须是对象')
        node = node[key]
    node[keys[-1]] = deepcopy(value)


def merge_changes(original, latest, changes):
    """Merge only edited fields; refuse to overwrite simultaneous edits."""
    merged = deepcopy(latest)
    conflicts = []
    for path, value in changes.items():
        old = get_value(original, path, MISSING)
        current = get_value(latest, path, MISSING)
        if current != old and current != value:
            conflicts.append(path)
        else:
            set_value(merged, path, value)
    if conflicts:
        raise ValueError('这些配置已被其他程序修改，请重新载入后再编辑：\n' + '\n'.join(conflicts))
    return merged


def validate_config_structure(data):
    """Reject values the editor cannot represent without silently changing them.

    Missing fields and unknown extensions remain valid. Ranges and credentials
    belong to save-time validation, so a structurally valid file can be opened
    to correct those mistakes.
    """
    errors = []
    if not isinstance(data, dict):
        return ['配置文件最外层必须是 JSON 对象']

    def check(path, types, description):
        value = get_value(data, path, MISSING)
        if value is not MISSING and type(value) not in types:
            errors.append(f'{path}：必须是{description}')

    objects = ['schedule', 'scoring', 'push', 'sources', 'desktop', 'cache']
    objects.extend(f'push.{name}' for name in ('qq', 'email', 'telegram'))
    objects.extend(f'sources.{name}' for name in ('github_trending', 'arxiv', 'steam', 'hackernews'))
    for section in ('chat', 'llm'):
        objects.append(section)
        objects.extend(f'{section}.{role}' for role in ('primary', 'fallback'))
    for path in objects:
        check(path, (dict,), 'JSON 对象')

    strings = ['schedule.daily_report_time', 'push.qq.http_base', 'push.qq.access_token',
               'push.email.smtp_host', 'push.email.username', 'push.email.password',
               'push.telegram.bot_token', 'sources.github_trending.language',
               'sources.github_trending.since', 'desktop.steampp_path', 'cache.clear_time']
    integers = ['schedule.realtime_interval_minutes', 'scoring.max_realtime_per_cycle',
                'scoring.max_daily_items', 'scoring.llm_batch_size', 'sources.rss_max_per_source',
                'push.qq.ws_port', 'push.email.smtp_port', 'cache.interval_days']
    numbers = [f'scoring.{key}' for key in ('realtime_threshold', 'major_threshold',
                                           'min_for_daily', 'doubt_threshold')]
    proxies = ['proxy', 'push.telegram.proxy']
    booleans = ['cache.auto_clear']
    for section in ('chat', 'llm'):
        for role in ('primary', 'fallback'):
            prefix = f'{section}.{role}'
            strings.extend(f'{prefix}.{key}' for key in ('provider', 'adapter', 'api_key', 'model', 'base_url'))
            proxies.append(f'{prefix}.proxy')
            integers.extend(f'{prefix}.{key}' for key in ('retries', 'max_tokens', 'circuit_failures'))
            numbers.extend(f'{prefix}.{key}' for key in ('timeout', 'circuit_open_seconds'))
            booleans.append(f'{prefix}.requires_api_key')
    for name in ('github_trending', 'arxiv', 'steam', 'hackernews'):
        integers.append(f'sources.{name}.max_items')
        booleans.append(f'sources.{name}.enabled')
    for path in strings:
        check(path, (str,), '字符串')
    for path in proxies:
        check(path, (str, type(None)), '代理地址字符串或 null')
    for path in integers:
        check(path, (int,), '整数')
        value = get_value(data, path, MISSING)
        if type(value) is int and not -(2 ** 31) <= value <= 2 ** 31 - 1:
            errors.append(f'{path}：超出配置界面支持的整数范围')
    for path in numbers:
        check(path, (int, float), '数字')
        value = get_value(data, path, MISSING)
        if type(value) in (int, float):
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                errors.append(f'{path}：必须是界面可表示的有限数字')
    for path in booleans:
        check(path, (bool,), 'true 或 false')
    for path in ('push.qq.group_id', 'push.telegram.chat_id'):
        check(path, (str, int), '字符串或整数')
    for path in ('push.enabled', 'push.email.to', 'sources.arxiv.categories'):
        value = get_value(data, path, MISSING)
        if value is not MISSING and (not isinstance(value, list) or any(type(item) is not str for item in value)):
            errors.append(f'{path}：必须是字符串列表')
    recipients = get_value(data, 'push.qq.private_qq', MISSING)
    if recipients is not MISSING and not (
        isinstance(recipients, str)
        or isinstance(recipients, list) and all(type(item) in (str, int) for item in recipients)
    ):
        errors.append('push.qq.private_qq：必须是 QQ 号字符串或列表')
    rss = get_value(data, 'sources.rss', MISSING)
    if rss is not MISSING:
        if not isinstance(rss, list):
            errors.append('sources.rss：必须是列表')
        else:
            for index, item in enumerate(rss):
                prefix = f'sources.rss[{index + 1}]'
                if not isinstance(item, dict):
                    errors.append(f'{prefix}：必须是 JSON 对象')
                    continue
                for key in ('name', 'url'):
                    if key in item and not isinstance(item[key], str):
                        errors.append(f'{prefix}.{key}：必须是字符串')
                if 'proxy' in item and type(item['proxy']) is not bool:
                    errors.append(f'{prefix}.proxy：必须是 true 或 false')
    return errors


def _valid_url(value, schemes):
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = urlparse(value.strip())
        # Reading port also catches non-numeric and out-of-range ports.
        port = parsed.port
        return (parsed.scheme in schemes and bool(parsed.hostname)
                and not any(character.isspace() for character in parsed.netloc)
                and (port is None or 1 <= port <= 65535))
    except (ValueError, TypeError):
        return False


def validate_config(data):
    """Validate editable values and credentials for enabled channels locally."""
    errors = validate_config_structure(data)
    if errors:
        return errors

    from .news_cache import cache_policy
    try:
        cache_policy(data)
    except ValueError as exc:
        errors.append(str(exc))

    def http_url(path, label, required=False):
        value = get_value(data, path, '')
        if not value and not required:
            return
        if not _valid_url(value, ('http', 'https')):
            errors.append(f'{label}：请填写完整的 http:// 或 https:// 地址')

    def number(path, label, minimum, integer=True, inclusive=True):
        value = get_value(data, path, MISSING)
        if value is MISSING:
            return
        valid_type = type(value) is int if integer else type(value) in (int, float)
        finite = type(value) is int or type(value) is float and math.isfinite(value)
        valid = valid_type and finite and (value >= minimum if inclusive else value > minimum)
        if not valid:
            requirement = '正整数' if integer and minimum == 1 else '非负整数' if integer else '大于 0 的有限数字'
            errors.append(f'{label}：必须是{requirement}')

    for path, label in [('schedule.realtime_interval_minutes', '采集间隔'),
                        ('scoring.max_realtime_per_cycle', '每轮推送上限'),
                        ('scoring.max_daily_items', '日报条数上限'),
                        ('scoring.llm_batch_size', '模型批处理数量'),
                        ('sources.rss_max_per_source', '每个 RSS 源的条数上限')]:
        number(path, label, 1)
    daily = get_value(data, 'schedule.daily_report_time', '08:00')
    if not isinstance(daily, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', daily):
        errors.append('日报时间：请使用 24 小时制 HH:MM')
    for key in ('realtime_threshold', 'major_threshold', 'min_for_daily', 'doubt_threshold'):
        value = get_value(data, f'scoring.{key}', 0)
        if type(value) not in (int, float) or not 0 <= value <= 10:
            errors.append(f'scoring.{key}：分数应在 0～10 之间')
    enabled = get_value(data, 'push.enabled', [])
    if 'qq' in enabled:
        http_url('push.qq.http_base', 'NapCat HTTP 地址', required=True)
        port = get_value(data, 'push.qq.ws_port', 3001)
        if type(port) is not int or not 1 <= port <= 65535:
            errors.append('QQ WebSocket 端口：必须在 1～65535 之间')
        recipients = get_value(data, 'push.qq.private_qq', [])
        if isinstance(recipients, str):
            recipients = [x.strip() for x in re.split(r'[,，;；\n]+', recipients) if x.strip()]
        if not isinstance(recipients, list) or any(not str(x).isdigit() for x in recipients):
            errors.append('接收 QQ：请填写数字 QQ 号，以逗号或换行分隔')
        group = get_value(data, 'push.qq.group_id', '')
        if group and not str(group).isdigit():
            errors.append('接收群号：请填写数字群号或留空')
    if 'email' in enabled:
        for key, label in [('smtp_host', 'SMTP 服务器'), ('username', '发件邮箱'), ('password', '邮箱授权码')]:
            if not get_value(data, f'push.email.{key}', '').strip():
                errors.append(f'邮箱已启用：请填写{label}')
        recipients = get_value(data, 'push.email.to', [])
        if not isinstance(recipients, list) or not recipients or any('@' not in str(x) for x in recipients):
            errors.append('邮箱已启用：请填写有效的收件邮箱列表')
        port = get_value(data, 'push.email.smtp_port', 465)
        if type(port) is not int or not 1 <= port <= 65535:
            errors.append('SMTP 端口：必须在 1～65535 之间')
    if 'telegram' in enabled:
        for key, label in [('bot_token', 'Bot Token'), ('chat_id', 'Chat ID')]:
            if not str(get_value(data, f'push.telegram.{key}', '')).strip():
                errors.append(f'Telegram 已启用：请填写 {label}')
    from .model_catalog import provider_requires_api_key, resolve_adapter
    for section in ('chat', 'llm'):
        for role in ('primary', 'fallback'):
            prefix = f'{section}.{role}'
            cfg = get_value(data, prefix, {})
            if cfg:
                try:
                    resolve_adapter(cfg)
                except ValueError:
                    errors.append(f'{prefix}：请选择受支持的接口适配器；自定义服务可使用 OpenAI 兼容接口')
            configured = bool(cfg.get('api_key', '').strip()) or not provider_requires_api_key(cfg)
            if configured and not cfg.get('model', '').strip():
                errors.append(f'{prefix}：请填写模型名称')
            http_url(f'{prefix}.base_url', f'{prefix} API 地址', required=configured)
            number(f'{prefix}.timeout', f'{prefix} 请求超时', 0, integer=False, inclusive=False)
            number(f'{prefix}.retries', f'{prefix} 额外重试次数', 0)
            number(f'{prefix}.max_tokens', f'{prefix} Token 上限', 1)
            number(f'{prefix}.circuit_open_seconds', f'{prefix} 熔断等待时间', 0, integer=False, inclusive=False)
            number(f'{prefix}.circuit_failures', f'{prefix} 熔断失败次数', 1)
    proxy_paths = ['proxy', 'push.telegram.proxy']
    proxy_paths.extend(f'{section}.{role}.proxy' for section in ('chat', 'llm') for role in ('primary', 'fallback'))
    for path in proxy_paths:
        value = get_value(data, path, '')
        if value and not _valid_url(value, ('http', 'https', 'socks4', 'socks4a', 'socks5', 'socks5h')):
            errors.append(f'{path}：请填写完整的 HTTP、HTTPS 或 SOCKS 代理地址，或留空')
    for name in ('github_trending', 'arxiv', 'steam', 'hackernews'):
        prefix = f'sources.{name}'
        value = get_value(data, f'{prefix}.max_items', MISSING)
        minimum = 1 if get_value(data, f'{prefix}.enabled', True) else 0
        if value is not MISSING and value < minimum:
            errors.append(f'{prefix}.max_items：开启来源时必须是正整数，关闭时可设为 0')
    rss = get_value(data, 'sources.rss', [])
    if not isinstance(rss, list):
        errors.append('RSS 订阅源必须是列表')
    else:
        for index, item in enumerate(rss):
            if not isinstance(item, dict):
                errors.append(f'第 {index + 1} 个 RSS 源必须是对象')
                continue
            if not item.get('name', '').strip() or not _valid_url(item.get('url', ''), ('http', 'https')):
                errors.append(f'第 {index + 1} 个 RSS 源：请填写名称和完整订阅地址')
    return errors


class ConfigStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.initial_path = self.path.with_name(self.path.name + '.initial.json')
        self.data = self.read()

    def ensure_initial(self):
        """Legacy entry point: remove the old private snapshot, never create it."""
        from .config_io import _config_lock
        with _config_lock(self.path):
            self.initial_path.unlink(missing_ok=True)
        return None

    def restore(self, source='factory', preserve_subscribers=False):
        """Always reset to account-free defaults, including legacy call forms."""
        from .config_io import atomic_update_json
        from .factory_defaults import fresh_config

        self.ensure_initial()
        if source not in {'factory', 'initial'}:
            raise ValueError('未知恢复来源')
        self.data = atomic_update_json(self.path, lambda latest: fresh_config(), backup=True)
        return self.data

    def read(self):
        with self.path.open(encoding='utf-8-sig') as handle:
            data = json.load(handle)
        errors = validate_config_structure(data)
        if errors:
            raise ValueError('\n'.join(errors))
        from .privacy import register_private_values
        register_private_values(data)
        return data

    def prepare(self, changes):
        data = merge_changes(self.data, self.read(), changes)
        errors = validate_config(data)
        if errors:
            raise ValueError('\n'.join(errors))
        return data

    def save(self, changes):
        """Merge against the latest file under the QQ writer's shared lock."""
        from .config_io import atomic_update_json

        self.ensure_initial()

        def update(latest):
            data = merge_changes(self.data, latest, changes)
            errors = validate_config(data)
            if errors:
                raise ValueError('\n'.join(errors))
            return data

        data = atomic_update_json(self.path, update, backup=True)
        self.data = data
        return data
