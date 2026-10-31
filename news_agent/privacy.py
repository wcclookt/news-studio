"""Log redaction without storing another credential registry on disk."""

import re
import threading
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_values = set()
_lock = threading.Lock()
PRIVATE_KEYS = {'apikey','password','passwd','smtppassword','authorizationcode','accesskey',
                'token','accesstoken','bottoken','secret','clientsecret','privateqq',
                'groupid','chatid','username','email','to','userid','uin','qq','cookies',
                'cookie','authorization','session','sessionkey','refreshtoken','apisecret',
                'chatids','privateqqprofiles','toprofiles','chatprofiles','address'}


def is_private_key(key):
    return re.sub(r'[^a-z0-9]', '', str(key).lower()) in PRIVATE_KEYS


def private_values(data):
    found = set()
    def collect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if is_private_key(key) or key in {'nickname', 'name', 'title', 'note'}:
                    collect(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                collect(child)
        elif isinstance(value, (str, int)) and not isinstance(value, bool):
            text = str(value).strip()
            if len(text) >= 5 and not text.startswith(('你的', 'fake-', 'test-', 'old-', 'new-')):
                found.add(text)
    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).startswith('_comment'):
                    continue
                if is_private_key(key):
                    collect(value)
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(data)
    return found


def register_private_values(data):
    with _lock:
        _values.update(private_values(data))


def redact_text(text, values=None):
    text = str(text)
    if values is None:
        with _lock:
            values = tuple(_values)
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(str(value), '[私密信息已隐藏]')
    text = re.sub(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '[邮箱已隐藏]', text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]{12,}\b', '[API密钥已隐藏]', text)
    text = re.sub(r'\b[0-9a-fA-F]{24,}\.[0-9a-zA-Z]{12,}\b', '[API密钥已隐藏]', text)
    text = re.sub(r'(?i)(bearer\s+)[^\s\x27\"]+', r'\1[已隐藏]', text)
    text = re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|bot[_-]?token|password|secret|authorization[_-]?code)\s*[=:]\s*)[^\s,;]+', r'\1[已隐藏]', text)
    text = re.sub(r'(?<!\d)\d{6,12}(?!\d)', '[编号已隐藏]', text)
    def url(match):
        value = match.group()
        try:
            parsed = urlsplit(value)
            host = parsed.netloc.rsplit('@', 1)[-1]
            query = [(key, '[已隐藏]' if is_private_key(key) or any(part in key.lower() for part in ('token','secret','password','signature','auth')) else val)
                     for key, val in parse_qsl(parsed.query, keep_blank_values=True)]
            return urlunsplit((parsed.scheme, host, parsed.path, urlencode(query), ''))
        except ValueError:
            return '[链接已隐藏]'
    return re.sub(r'https?://[^\s<>\x27\"]+', url, text)


class PrivacyLogFilter:
    def filter(self, record):
        record.msg = redact_text(record.getMessage())
        record.args = ()
        if record.exc_info:
            import traceback
            record.exc_text = redact_text(''.join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return True
