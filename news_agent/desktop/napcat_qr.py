# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

"""Read this NapCat session's locally generated PNG, without a QR web service.

The image is returned in memory only. An old file from a previous process or an
unverified/stopped process must never appear as a current login challenge.
"""

import os
from pathlib import Path
import struct
import time


PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
MAX_QR_BYTES = 256 * 1024
MAX_QR_AGE = 120


def started_at(process):
    try:
        value = int(process.get('created', ''))
        return (value - 116444736000000000) / 10000000 if value > 116444736000000000 else None
    except (ValueError, TypeError):
        return None


def qr_path(napcat_root):
    workdir = str(os.environ.get('NAPCAT_WORKDIR') or '').strip()
    return (Path(workdir).expanduser() if workdir else Path(napcat_root) / 'napcat') / 'cache' / 'qrcode.png'


def read_login_qr(napcat_root, status, *, logged_in=False, process_alive=None, now=None):
    state = status.get('state', 'unknown')
    if state == 'stopped':
        return {'state': 'stopped', 'message': '暂未启动 NAPCAT 服务'}
    if state != 'running':
        return {'state': 'unknown', 'message': '正在确认 NAPCAT 服务状态，请稍后刷新。'}
    processes = status.get('processes', [])
    if not processes or not process_alive:
        return {'state': 'waiting', 'message': 'NAPCAT 已启动，正在确认当前登录会话…'}
    states = [(process, process_alive(process)) for process in processes]
    alive = [process for process, value in states if value is True]
    if not alive:
        if any(value is None for _, value in states):
            return {'state': 'unknown', 'message': '暂时无法确认 NAPCAT 进程权限，已隐藏旧二维码。'}
        return {'state': 'stopped', 'message': '暂未启动 NAPCAT 服务'}
    if logged_in:
        return {'state': 'logged_in', 'message': 'QQ 已登录，无需扫码。'}
    start_times = [value for process in alive if (value := started_at(process)) is not None]
    if not start_times:
        return {'state': 'waiting', 'message': '正在等待本次 NAPCAT 启动后生成的二维码…'}
    path = qr_path(napcat_root)
    now = time.time() if now is None else now
    try:
        if path.is_symlink():
            return {'state': 'waiting', 'message': '二维码文件路径异常，未读取。'}
        before = path.stat()
        if before.st_mtime < max(start_times):
            return {'state': 'waiting', 'message': '已忽略上次运行的旧二维码，等待本次生成…'}
        if now - before.st_mtime > MAX_QR_AGE or before.st_mtime > now + 5:
            return {'state': 'stale', 'message': '暂不显示超过 2 分钟的旧码，等待 NAPCAT 更新二维码。'}
        if not 24 <= before.st_size <= MAX_QR_BYTES:
            return {'state': 'waiting', 'message': '二维码正在生成，稍后自动重新读取…'}
        with path.open('rb') as handle:
            data = handle.read(MAX_QR_BYTES + 1)
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            return {'state': 'waiting', 'message': '二维码正在更新，稍后自动重新读取…'}
        if len(data) != after.st_size or not data.startswith(PNG_SIGNATURE) or data[12:16] != b'IHDR':
            return {'state': 'waiting', 'message': '二维码图片尚未完整生成…'}
        width, height = struct.unpack('>II', data[16:24])
        if not 16 <= width <= 2048 or not 16 <= height <= 2048 or width * height > 2048 * 2048:
            return {'state': 'error', 'message': '二维码图片尺寸异常，未显示。'}
        identity = tuple(sorted((process.get('pid'), process.get('created')) for process in alive))
        return {'state': 'qr', 'message': '请使用手机 QQ 扫码，并在手机上确认登录。',
                'image': data, 'version': (identity, after.st_mtime_ns, after.st_size),
                'updated_at': after.st_mtime}
    except FileNotFoundError:
        return {'state': 'waiting', 'message': 'NAPCAT 已启动，正在等待登录二维码…'}
    except (OSError, ValueError, struct.error):
        return {'state': 'waiting', 'message': '二维码暂时无法读取，稍后自动重试。'}
