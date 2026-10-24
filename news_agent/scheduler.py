import threading
import time
from datetime import datetime
from pathlib import Path
import hashlib

from .logger import get_logger
from .orchestrator import retry_pending, run_daily, run_realtime
from .dedupe import Dedupe
from .runtime_config import LiveConfig

log = get_logger()


def _should_run_daily(now, daily_time, tolerance_minutes=5):
    """Due at or after the configured time, including a missed polling window."""
    target_h, target_m = daily_time.split(":")
    target_total = int(target_h) * 60 + int(target_m)
    now_total = now.hour * 60 + now.minute
    return now_total >= target_total


def _daily_identity(cfg_path, now):
    identity = hashlib.sha256(str(Path(cfg_path).resolve()).lower().encode()).hexdigest()[:16]
    return f'daily:{identity}:{now:%Y-%m-%d}'


def _run_due_daily(cfg, cfg_path, now):
    if not _should_run_daily(now, cfg.get('schedule.daily_report_time', '08:00')):
        return False
    state = Dedupe()
    try:
        identity = _daily_identity(cfg_path, now)
        if state.scheduled_done(identity):
            return False
        result = run_daily(cfg)
        if result and result.get('ok'):
            state.mark_scheduled_done(identity)
            if result.get('failed'):
                log.warning('日报部分渠道失败，请检查失败日志；已发送部分不重复群发。')
            return True
        log.warning('日报尚未成功发送，将在后续检查时重试。')
        return False
    finally:
        state.close()


def _qqbot_worker(cfg_path, cfg=None):
    from .qq_bot import run_qq_bot

    try:
        run_qq_bot(cfg_path, cfg=cfg)
    except Exception as e:
        log.error("QQ机器人异常退出: %s", e)


def run_loop(cfg_path, with_qqbot=False):
    with LiveConfig(cfg_path) as cfg:
        _run_live_loop(cfg, cfg_path, with_qqbot)


def _run_live_loop(cfg, cfg_path, with_qqbot=False):
    next_realtime = 0
    next_pending = 0
    last_realtime_finished = None
    interval = int(cfg.get('schedule.realtime_interval_minutes', 30))
    if with_qqbot:
        t = threading.Thread(target=_qqbot_worker, args=(cfg_path, cfg), daemon=True)
        t.start()
        log.info("QQ 交互机器人已启动")
    log.info('调度器启动：配置变更约 1 秒内应用；新请求使用新设置，已开始的请求允许完成。')
    while True:
        if cfg.changed.is_set():
            cfg.changed.clear()
            new_interval = int(cfg.get('schedule.realtime_interval_minutes', 30))
            if new_interval != interval and last_realtime_finished is not None:
                next_realtime = last_realtime_finished + new_interval * 60
            interval = new_interval
            next_pending = 0
        try:
            with Dedupe() as cache:
                cleanup = cache.cache_maintenance(cfg)
            if cleanup['removed']:
                log.info('缓存定时清空完成：删除 %d 条新闻。', cleanup['removed'])
        except Exception as exc:
            log.warning('缓存定时检查未完成：%s', type(exc).__name__)
        if time.monotonic() >= next_pending:
            try:
                retry_pending(cfg)
            except Exception as e:
                log.error('待发检查异常：%s', type(e).__name__)
            next_pending = time.monotonic() + 60
        try:
            _run_due_daily(cfg, cfg_path, datetime.now())
        except Exception as e:
            log.error('日报任务异常：%s', type(e).__name__)
        if time.monotonic() >= next_realtime:
            try:
                run_realtime(cfg)
            except Exception as e:
                log.error('实时任务异常：%s', e)
            last_realtime_finished = time.monotonic()
            next_realtime = last_realtime_finished + int(cfg.get('schedule.realtime_interval_minutes', 30)) * 60
            next_pending = time.monotonic() + 60
        timeout = max(0, min(30, next_realtime-time.monotonic(), next_pending-time.monotonic()))
        cfg.changed.wait(timeout)
