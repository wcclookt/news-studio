# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

"""Validated hot configuration shared by the scheduler, model calls and QQ bot.

Only a whole valid JSON snapshot is published. The watcher does not restart
applications or write credentials/status copies to disk.
"""

from copy import deepcopy
import json
from pathlib import Path
import threading

from .config import Config
from .config_store import validate_config
from .privacy import register_private_values
from .logger import get_logger

log = get_logger()


class LiveConfig(Config):
    def __init__(self, path, poll_seconds=0.5, watch=True):
        super().__init__({})
        self.path = str(Path(path).resolve())
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.changed = threading.Event()
        self._callbacks = set()
        self._signature = None
        self._thread = None
        self.revision = 0
        self.last_error = ''
        self.refresh(initial=True)
        self.changed.clear()
        if watch:
            self._thread = threading.Thread(target=self._watch, args=(poll_seconds,), name='config-watch', daemon=True)
            self._thread.start()

    def refresh(self, initial=False):
        try:
            path = Path(self.path)
            stamp = path.stat()
            signature = (stamp.st_mtime_ns, stamp.st_size)
            with self._lock:
                if signature == self._signature:
                    return False
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            if validate_config(data):
                raise ValueError('Invalid runtime configuration')
            register_private_values(data)
            with self._lock:
                self.data = data
                self._signature = signature
                self.revision += 1
                self.last_error = ''
                callbacks = list(self._callbacks)
            self.changed.set()
            for callback in callbacks:
                try:
                    callback()
                except Exception:
                    log.warning('某项配置更新暂未完成，将在下次操作时重试。')
            if not initial:
                log.info('已应用最新运行配置：版本 %d。', self.revision)
            return True
        except (OSError, ValueError, TypeError):
            if initial:
                raise ValueError('配置文件无法读取或有无效设置，请在界面修正后启动。') from None
            if not self.last_error:
                log.warning('新的配置文件暂时无效，保留上一次有效运行设置；推送开关仍按最新文件检查。')
            self.last_error = '配置未通过验证'
            # Keep the previous valid non-channel settings. Dispatcher continues
            # to read channel switches independently and fails closed.
            return False

    def snapshot(self):
        with self._lock:
            result = Config(deepcopy(self.data))
            result.path = self.path
            return result

    def subscribe(self, callback):
        with self._lock:
            self._callbacks.add(callback)
        def unsubscribe():
            with self._lock:
                self._callbacks.discard(callback)
        return unsubscribe

    def _watch(self, seconds):
        while not self._stop.wait(max(.1, seconds)):
            self.refresh()

    def close(self):
        self._stop.set()
        self.changed.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
