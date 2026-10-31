# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

"""Atomic JSON updates shared by the editor and automatic QQ subscriptions."""

from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import time


@contextmanager
def _config_lock(path, timeout=2.0):
    # Keep this file in place: removing it could let two callers lock different
    # files under the same name while one still holds the old file open.
    lock_path = path.with_name(path.name + '.lock')
    with lock_path.open('a+b') as handle:
        if os.name == 'nt':
            import msvcrt

            def acquire():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

            def release():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire():
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release():
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

        deadline = time.monotonic() + timeout
        while True:
            try:
                acquire()
                break
            except (BlockingIOError, OSError) as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError('配置正由其他程序保存，请稍后再试') from exc
                time.sleep(.04)
        try:
            # Windows permits locking a range beyond EOF. Initialise its byte
            # only AFTER acquiring that range: another writer may already hold
            # the initially empty file, making a pre-lock flush fail EACCES.
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'\0')
                handle.flush()
            yield
        finally:
            release()


def _replace_bytes(path, content):
    """Stage beside the target, then atomically replace it on the same volume."""
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_update_json(path, updater, backup=False):
    """Read, modify and atomically save JSON while holding a shared file lock.

    ``updater`` receives the latest JSON object and must return the new object.
    Validation and merge errors raised by it leave both files unchanged. With
    ``backup=True`` only public preferences are backed up, never credentials or
    recipient lists from the predecessor.
    """
    path = Path(path).resolve()
    with _config_lock(path):
        original = path.read_bytes()
        latest = json.loads(original.decode('utf-8-sig'))
        if not isinstance(latest, dict):
            raise ValueError('配置文件最外层必须是 JSON 对象')
        data = updater(deepcopy(latest))
        if not isinstance(data, dict):
            raise ValueError('更新后的配置必须是 JSON 对象')
        serialized = (json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
        if backup:
            from .factory_defaults import public_backup
            safe = json.dumps(public_backup(latest), ensure_ascii=False, indent=2, allow_nan=False) + '\n'
            _replace_bytes(path.with_name(path.name + '.bak'), safe.encode('utf-8'))
        _replace_bytes(path, serialized)
    return data
