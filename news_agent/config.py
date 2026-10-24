import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


class Config:
    def __init__(self, data):
        self.data = data
        self.path = ""

    def get(self, key, default=None):
        keys = key.split(".")
        node = self.data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def llm_primary(self):
        return self.get("llm.primary", {})

    def llm_fallback(self):
        return self.get("llm.fallback", {})

    def chat_cfg(self):
        """聊天用的 LLM 配置（可独立于推送配置）"""
        return self.get("chat", {})

    def chat_primary(self):
        return self.chat_cfg().get("primary", {}) or self.llm_primary()

    def chat_fallback(self):
        return self.chat_cfg().get("fallback", {}) or self.llm_fallback()

    def proxy(self):
        return self.get("proxy", None)

    def schedule(self):
        return self.get("schedule", {})

    def scoring(self):
        return self.get("scoring", {})

    def sources(self):
        return self.get("sources", {})

    def push(self):
        return self.get("push", {})

    def enabled_pushes(self):
        # Push switches are live even while collection/scoring still holds the
        # previous model and schedule snapshot. Do not revive stale channels.
        if not self.path:
            return self.get('push.enabled', [])
        try:
            with open(self.path, encoding='utf-8-sig') as handle:
                latest = json.load(handle)
            push = latest.get('push', {})
            enabled = push.get('enabled', [])
            if not isinstance(push, dict) or not isinstance(enabled, list) or any(not isinstance(name, str) for name in enabled):
                raise ValueError('invalid channel switches')
            return list(enabled)
        except (OSError, ValueError, TypeError, AttributeError):
            from .logger import get_logger
            get_logger().warning('无法读取最新推送开关，暂不启动推送，请检查配置文件。')
            return []


def load_config(path=None):
    path = path or CONFIG_PATH
    if not os.path.exists(path):
        sys.exit(f"配置文件不存在: {path}（请从 config.example.json 复制并填写）")
    with open(path, "r", encoding="utf-8") as f:
        cfg = Config(json.load(f))
    from .privacy import register_private_values
    register_private_values(cfg.data)
    cfg.path = os.path.abspath(path)
    return cfg
