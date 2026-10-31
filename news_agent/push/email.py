# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

import smtplib
import time
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .base import PushChannel, PushError
from ..recipient_profiles import recipient_ids


class EmailChannel(PushChannel):
    name = "email"

    def __init__(self, cfg):
        super().__init__(cfg)
        self.smtp_host = cfg.get("smtp_host", "smtp.163.com")
        self.smtp_port = cfg.get("smtp_port", 465)
        self.username = cfg.get("username", "")
        self.password = cfg.get("password", "")
        self.to = recipient_ids(cfg, 'email')
        self.retries = int(cfg.get("retries", 3))

    @staticmethod
    def _can_send(should_send):
        if should_send is None:
            return True
        try:
            return bool(should_send())
        except Exception:
            # A missing/unreadable setting must never revive an old mail job.
            return False

    def send(self, title, content, html=None, should_send=None, **kwargs):
        if not self._can_send(should_send):
            return False
        if not self.username or not self.to:
            raise PushError("邮件未配置发件人或收件人")
        body = html or f"<pre>{content}</pre>"
        msg = MIMEMultipart("alternative")
        msg["Subject"] = Header(title, "utf-8")
        msg["From"] = f"科技新闻助手 <{self.username}>"
        msg["To"] = ", ".join(self.to)
        part1 = MIMEText(content, "plain", "utf-8")
        part2 = MIMEText(body, "html", "utf-8")
        msg.attach(part1)
        msg.attach(part2)
        last_err = None
        for attempt in range(self.retries):
            if not self._can_send(should_send):
                return False
            try:
                if self._deliver(msg, should_send=should_send) is False:
                    return False
                return True
            except Exception as e:
                last_err = e
                wait = 5 * (attempt + 1)
                if attempt < self.retries - 1:
                    if should_send is None:
                        time.sleep(wait)
                    else:
                        for _ in range(wait):
                            if not self._can_send(should_send):
                                return False
                            time.sleep(1)
        raise PushError(f'邮件发送失败（尝试 {self.retries} 次，{type(last_err).__name__}），请检查 SMTP 服务与账号。')

    def _deliver(self, msg, should_send=None):
        if not self._can_send(should_send):
            return False
        if self.smtp_port == 465:
            server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=30)
        else:
            server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30)
        try:
            if self.smtp_port != 465:
                server.starttls()
            server.login(self.username, self.password)
            # This is the last reversible point before the SMTP submission.
            if not self._can_send(should_send):
                return False
            server.sendmail(self.username, self.to, msg.as_string())
            return True
        finally:
            try:
                server.quit()
            except Exception:
                try:
                    server.close()
                except Exception:
                    pass
