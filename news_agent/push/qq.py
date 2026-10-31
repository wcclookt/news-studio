import requests
import re

from ..logger import get_logger
from .base import PushChannel, PushError
from ..recipient_profiles import recipient_ids

log = get_logger()

MAX_MSG = 1500


def _parse_qq_list(val):
    values = re.split(r'[,，;；\s]+', val) if isinstance(val, str) else val
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip().isdigit())) if isinstance(values, list) else []


class QQChannel(PushChannel):
    name = "qq"

    def __init__(self, cfg):
        super().__init__(cfg)
        self.http_base = cfg.get("http_base", "http://127.0.0.1:3000").rstrip("/")
        self.access_token = cfg.get("access_token", "")
        self.private_qq_list = recipient_ids(cfg, 'qq')
        self.group_id = str(cfg.get("group_id", "") or "")

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        return h

    def _call(self, action, params):
        url = f"{self.http_base}/{action}"
        try:
            with requests.Session() as session:
                session.trust_env = False
                with session.post(url, json=params, headers=self._headers(),
                                  timeout=(3, 12), allow_redirects=False) as resp:
                    if resp.status_code != 200:
                        raise PushError(f'NapCat HTTP {resp.status_code}，请检查接口地址和鉴权。')
                    data = resp.json()
        except PushError:
            raise
        except requests.Timeout:
            raise PushError('NapCat 请求超时，发送结果未知；请确认接收端，避免重复发送。') from None
        except Exception:
            raise PushError('NapCat 请求失败，请检查服务地址、QQ 登录状态和鉴权。') from None
        if not isinstance(data, dict) or data.get('status') != 'ok' or data.get('retcode', 0) != 0:
            raise PushError(f'NapCat {action} 未确认成功，请检查好友关系和服务日志。')
        if action in {'send_private_msg', 'send_group_msg'} and (
                not isinstance(data.get('data'), dict) or data['data'].get('message_id') is None):
            raise PushError('NapCat 未返回消息编号，发送结果未确认；请在接收端查看。')
        return data

    def _split(self, title, content):
        header = f"{title}\n\n" if title else ''
        body = content
        if len(header) + len(body) <= MAX_MSG:
            return [header + body]
        # 需要分片：第一片带头部，后续带"续"
        parts = []
        remaining = body
        first = True
        while remaining:
            budget = MAX_MSG - len(header) if first else MAX_MSG - len("[续]\n")
            chunk, remaining = remaining[:budget], remaining[budget:]
            if first:
                parts.append(header + chunk)
                first = False
            else:
                parts.append("[续]\n" + chunk)
        return parts

    def send(self, title, content, **kwargs):
        messages = self._split(title, content)
        sent = 0
        errors = []
        allowed = getattr(self, 'recipient_allowed', lambda identifier, group=False: True)
        for qq in self.private_qq_list:
            try:
                for text in messages:
                    if not allowed(qq):
                        break
                    self._call("send_private_msg", {"user_id": qq, "message": text})
                    sent += 1
            except PushError as e:
                errors.append(f"{qq}: {e}")
                log.warning("推送到 %s 失败: %s", qq, e)
        if self.group_id and self.group_id.isdigit():
            try:
                for text in messages:
                    if not allowed(self.group_id, group=True):
                        break
                    self._call("send_group_msg", {"group_id": self.group_id, "message": text})
                    sent += 1
            except PushError as e:
                errors.append(f"群{self.group_id}: {e}")
                log.warning("推送到群 %s 失败: %s", self.group_id, e)
        if not sent:
            if not errors and (self.private_qq_list or self.group_id):
                return False
            raise PushError("QQ 推送全部失败: " + "; ".join(errors) if errors else "未配置接收对象")
        return True
