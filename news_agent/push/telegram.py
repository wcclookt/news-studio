import requests

from .base import PushChannel, PushError

API = "https://api.telegram.org"


class TelegramChannel(PushChannel):
    name = "telegram"

    def __init__(self, cfg):
        super().__init__(cfg)
        self.bot_token = cfg.get("bot_token", "")
        self.chat_id = cfg.get("chat_id", "")
        self.proxy = cfg.get("proxy")

    def _proxies(self):
        return {"http": self.proxy, "https": self.proxy} if self.proxy else None

    def send(self, title, content, **kwargs):
        if not self.bot_token or not self.chat_id:
            raise PushError("Telegram 未配置 bot_token 或 chat_id")
        text = f"<b>{title}</b>\n\n{content}"
        if len(text) > 4000:
            text = text[:4000]
        url = f"{API}/bot{self.bot_token}/sendMessage"
        body = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        try:
            resp = requests.post(url, json=body, timeout=30, proxies=self._proxies())
        except Exception as e:
            raise PushError(f"Telegram 请求失败: {e}")
        if resp.status_code != 200:
            raise PushError(f"Telegram HTTP {resp.status_code}: {resp.text[:300]}")
        return True
