import requests
from html import escape

from .base import PushChannel, PushError
from ..recipient_profiles import recipient_ids

API = "https://api.telegram.org"


class TelegramChannel(PushChannel):
    name = "telegram"

    def __init__(self, cfg):
        super().__init__(cfg)
        self.bot_token = cfg.get("bot_token", "")
        self.chat_id = str(cfg.get("chat_id", "") or "").strip()
        self.chat_ids = recipient_ids(cfg, 'telegram')
        self.proxy = cfg.get("proxy")

    def _proxies(self):
        return {"http": self.proxy, "https": self.proxy} if self.proxy else None

    def send(self, title, content, **kwargs):
        if not self.bot_token or not self.chat_ids:
            raise PushError("Telegram 未配置 bot_token 或 chat_id")
        text = f'<b>{escape(title[:200])}</b>\n\n{escape(content[:3600])}'
        url = f"{API}/bot{self.bot_token}/sendMessage"
        failures = 0
        sent = 0
        allowed = getattr(self, 'recipient_allowed', lambda identifier: True)
        for chat_id in self.chat_ids:
            if not allowed(chat_id):
                continue
            body = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }
            resp = None
            try:
                resp = requests.post(url, json=body, timeout=30, proxies=self._proxies())
                result = resp.json() if resp.status_code == 200 else None
                if not isinstance(result, dict) or result.get('ok') is not True:
                    failures += 1
                else:
                    sent += 1
            except (requests.RequestException, ValueError, TypeError):
                failures += 1
            finally:
                if resp is not None:
                    resp.close()
        if failures:
            raise PushError(f'Telegram 有 {failures}/{len(self.chat_ids)} 个接收对象未确认送达，请检查权限和 Chat ID。')
        return bool(sent)
