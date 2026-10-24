import html as html_mod
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import time

from ..logger import get_logger
from .base import PushError
from .email import EmailChannel
from .qq import QQChannel, _parse_qq_list
from .telegram import TelegramChannel

log = get_logger()

CHANNELS = {
    "email": EmailChannel,
    "telegram": TelegramChannel,
    "qq": QQChannel,
}


class Dispatcher:
    # 推送优先级：QQ 最快，email 最后（email 慢且可能风控，不能阻塞）
    PRIORITY = {"qq": 0, "telegram": 1, "email": 2}

    def __init__(self, cfg):
        self.cfg = cfg
        self.channels = []
        # Each actual send refreshes switches AND endpoint/credential settings.
        self._sync_channels(deepcopy(cfg.push()))
        if getattr(cfg, 'path', ''):
            latest_push = self._read_live_push()
            if latest_push is not None:
                self._sync_channels(latest_push)
                self._refresh_qq_subscribers(latest_push)

    def _read_live_push(self):
        """Return current switches, or no authorization to send on read errors."""
        path = getattr(self.cfg, 'path', '')
        try:
            with Path(path).open(encoding='utf-8-sig') as handle:
                latest = json.load(handle)
            push = latest.get('push', {})
            if not isinstance(push, dict):
                raise ValueError('invalid push object')
            enabled = push.get('enabled', [])
            if not isinstance(enabled, list) or any(not isinstance(name, str) for name in enabled):
                raise ValueError('invalid channel switches')
            return push
        except (OSError, ValueError, TypeError, AttributeError):
            # Never echo the path, raw configuration, or exception details.
            log.warning('无法读取最新推送开关，本次暂停推送，请检查配置文件。')
            return None

    def _sync_channels(self, push):
        """Rebuild changed channels so new credentials/addresses apply next send."""
        enabled = push.get('enabled', []) if isinstance(push, dict) else None
        if not isinstance(enabled, list) or any(not isinstance(name, str) for name in enabled):
            self.channels = []
            log.warning('推送渠道开关格式异常，已暂停推送。')
            return
        existing = {channel.name: channel for channel in self.channels}
        channels = []
        for name in dict.fromkeys(enabled):
            cls = CHANNELS.get(name)
            if cls is None:
                continue
            channel = existing.get(name)
            config = push.get(name, {})
            previous = dict(channel.cfg) if channel is not None else None
            compare = dict(config) if isinstance(config, dict) else config
            if name == 'qq' and previous is not None and isinstance(compare, dict):
                previous.pop('private_qq', None)
                compare.pop('private_qq', None)
            if channel is None or previous != compare:
                if not isinstance(config, dict):
                    log.warning('推送渠道配置格式异常，已跳过该渠道。')
                    continue
                try:
                    channel = cls(deepcopy(config))
                except (ValueError, TypeError, AttributeError):
                    log.warning('推送渠道配置无法加载，已跳过该渠道。')
                    continue
            channels.append(channel)
        self.channels = sorted(channels, key=lambda channel: self.PRIORITY.get(channel.name, 9))

    def _channel_still_enabled(self, name, expected_config=None):
        """Called by a slow channel again before retries or network delivery."""
        push = self._read_live_push()
        return (push is not None and name in push.get('enabled', [])
                and (expected_config is None or push.get(name, {}) == expected_config))

    def _refresh_qq_subscribers(self, push):
        """Refresh recipients without replacing the active QQ connection settings."""
        qq_channels = [channel for channel in self.channels if channel.name == 'qq']
        if not qq_channels:
            return True
        try:
            qq = push.get('qq', {})
            if not isinstance(qq, dict):
                raise ValueError('invalid QQ object')
            recipients = qq.get('private_qq', [])
            if isinstance(recipients, str):
                recipients = re.split(r'[,，;；\s]+', recipients)
            if not isinstance(recipients, list):
                raise ValueError('invalid recipient list')
            users = _parse_qq_list(recipients)
            for channel in qq_channels:
                channel.private_qq_list = list(users)
            return True
        except (OSError, ValueError, TypeError, AttributeError):
            log.warning('QQ 订阅列表格式异常，本次暂停 QQ 推送。')
            return False

    def send(self, title, content, html=None, kind="realtime", only=None):
        ok = []
        failed = []
        allowed = None if only is None else set(only)
        if allowed is not None and not allowed:
            return ok, failed
        live = bool(getattr(self.cfg, 'path', ''))
        names = (sorted(CHANNELS, key=lambda name: self.PRIORITY.get(name, 9)) if live
                 else [channel.name for channel in self.channels])
        for name in names:
            if allowed is not None and name not in allowed:
                continue
            if live:
                # Re-read before EACH actual channel: QQ can take time, and an
                # email switch turned off during QQ must prevent opening SMTP.
                push = self._read_live_push()
                if push is None:
                    return ok, failed
                self._sync_channels(push)
                if name == 'qq' and not self._refresh_qq_subscribers(push):
                    continue
            ch = next((channel for channel in self.channels if channel.name == name), None)
            if ch is None:
                continue
            try:
                if ch.name == "telegram":
                    result = ch.send(title, _html_to_tg(html or content))
                elif ch.name == "email":
                    options = {'html': html}
                    if live:
                        options['should_send'] = lambda config=deepcopy(ch.cfg): self._channel_still_enabled('email', config)
                    result = ch.send(title, _html_to_plain(html or content), **options)
                else:
                    result = ch.send(title, content)
                if result is False:
                    continue
                ok.append(ch.name)
                log.info("推送[%s] 成功: %s", ch.name, title[:40])
            except PushError as e:
                failed.append(ch.name)
                log.warning("推送[%s] 失败: %s", ch.name, e)
                _dump_failed(title, content, html, ch.name, str(e))
        return ok, failed


def _dump_failed(title, content, html, channel, err):
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "logs", "failed")
    base = os.path.normpath(base)
    os.makedirs(base, exist_ok=True)
    fn = os.path.join(base, f"{channel}_{time.strftime('%Y%m%d_%H%M%S')}.txt")
    try:
        with open(fn, "w", encoding="utf-8") as f:
            f.write(f"标题: {title}\n错误: {err}\n\n--- 纯文本 ---\n{content}\n\n--- HTML ---\n{html or ''}\n")
        log.info("失败内容已转存: %s", fn)
    except Exception:
        pass


def _html_to_plain(html_str):
    import re

    s = re.sub(r"<br\s*/?>", "\n", html_str)
    s = re.sub(r"<li[^>]*>", "\n- ", s)
    s = re.sub(r"</li>", "", s)
    s = re.sub(r"</?(p|div|ul|ol|h[1-6]|blockquote)[^>]*>", "\n", s)
    s = re.sub(r"<img[^>]*src=\"([^\"]*)\"[^>]*>", r"[图] \1\n", s)
    s = re.sub(r'<a href="([^"]*)"[^>]*>(.*?)</a>', r"\2 (\1)", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_mod.unescape(s)
    lines = [ln.strip() for ln in s.splitlines()]
    return "\n".join([ln for ln in lines if ln])


def _html_to_tg(html_str):
    import re

    s = re.sub(r"<br\s*/?>", "\n", html_str)
    s = re.sub(r"<li[^>]*>", "\n• ", s)
    s = re.sub(r"</li>", "", s)
    s = re.sub(r"</?(p|div|ul|ol|h[1-6]|blockquote)[^>]*>", "\n", s)
    s = re.sub(r"<img[^>]*>", "", s)
    s = re.sub(r'<a href="([^"]*)"[^>]*>(.*?)</a>', r'<a href="\1">\2</a>', s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_mod.unescape(s)
    lines = [ln.strip() for ln in s.splitlines()]
    return "\n".join([ln for ln in lines if ln])
