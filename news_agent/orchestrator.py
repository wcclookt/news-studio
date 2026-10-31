# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

import html as html_mod
import re
import time
from datetime import datetime, timedelta

from .dedupe import Dedupe, _norm_title
from .llm import LLMClient, CATEGORY_NAMES
from .logger import get_logger
from .processor import process_items
from .push.dispatcher import Dispatcher
from .report import _pick_entries, format_time, generate_daily_report
from .sources.collector import collect_all

log = get_logger()

NIGHT_START = 23  # 晚间23点
NIGHT_END = 6     # 早上6点


def is_night_hour(now=None):
    now = now or datetime.now()
    return now.hour >= NIGHT_START or now.hour < NIGHT_END


def _item_html(it):
    img = ""
    if it.image:
        img = f'<br><img src="{html_mod.escape(it.image)}" style="max-width:300px;border-radius:6px;">'
    t = format_time(it.published_at)
    time_str = f"<span style='color:#aaa'>[{t}]</span> " if t else ""
    title_zh = html_mod.escape(it.display_title())
    parts = [f"<b>{time_str}<a href=\"{html_mod.escape(it.url)}\">{title_zh}</a></b><br>"]
    parts.append(f"{html_mod.escape(it.summary)}<br>")
    if it.credibility:
        parts.append(f"<span style='color:#888'>可信度:{html_mod.escape(it.credibility)} | {html_mod.escape(it.reason)} | 来源:{html_mod.escape(it.source)}</span><br>")
    else:
        parts.append(f"<span style='color:#888'>{html_mod.escape(it.reason)} | 来源:{html_mod.escape(it.source)}</span><br>")
    if it.comment:
        parts.append(f"<span style='color:#556'>AI解读：{html_mod.escape(it.comment)}</span>")
    if img:
        parts.append(img)
    return "<br>".join(parts)


def _item_plain(it):
    t = format_time(it.published_at)
    time_str = f"[{t}] " if t else ""
    s = f"{time_str}{it.display_title()}\n{it.summary}\n{it.reason}"
    if it.credibility:
        s += f"\n可信度：{it.credibility}"
    s += f"\n来源：{it.source}"
    if it.comment:
        s += f"\nAI解读：{it.comment}"
    s += f"\n{it.url}"
    if it.image:
        s += f"\n[图] {it.image}"
    return s


def _push_score(it):
    return round(it.score * 0.6 + it.utility * 0.4, 2)


def _should_send(it, threshold, cfg):
    if not it.is_valuable:
        return False
    if it.credibility == "不可信":
        return False
    if it.credibility == "存疑" and it.score < cfg.get("scoring.doubt_threshold", 9):
        return False
    return _push_score(it) >= threshold


def _deliver_pending(cfg, dedupe, max_push=None):
    """Retry scored news, including the no-new-news case, without model calls."""
    night = is_night_hour()
    threshold = cfg.get('scoring.realtime_threshold', 7) if night else cfg.get('scoring.major_threshold', 9)
    limit = int(cfg.get('scoring.max_realtime_per_cycle', 30)) if max_push is None else max_push
    channels = set(cfg.enabled_pushes()) & {'qq', 'email', 'telegram'}
    if not channels or limit <= 0:
        return []
    generation = dedupe.cache_generation()
    pending = [item for item in dedupe.pending_realtime() if _should_send(item, threshold, cfg)]
    if not pending:
        log.info('待发检查：没有达到当前阈值 %s 的未发送新闻。', threshold)
        return []
    dispatcher = Dispatcher(cfg)
    pushed = []
    log.info('待发检查：%d 条已评分新闻达到阈值 %s，尝试发送。', len(pending), threshold)
    for item in pending[:limit]:
        if not dedupe.cache_contains(item.url, expected_generation=generation):
            # A UI/automatic deletion must also cancel an already-read queue.
            continue
        delivered = dedupe.realtime_delivered_channels(item.url)
        remaining = channels - delivered
        if not remaining:
            dedupe.mark_pushed(item.url, expected_generation=generation)
            continue
        title = f'{CATEGORY_NAMES.get(item.category, item.category)} | {item.display_title()}'
        ok, failed = dispatcher.send(title, _item_plain(item), html=_item_html(item),
                                     kind='realtime', only=remaining)
        if ok:
            dedupe.record_realtime_delivery(item.url, ok, expected_generation=generation)
            pushed.append(item.url)
        if channels <= delivered | set(ok):
            dedupe.mark_pushed(item.url, expected_generation=generation)
        if failed:
            log.warning('新闻发送未完成，保留待发，下轮仅重试未成功渠道：%s', ','.join(failed))
    return pushed


def retry_pending(cfg):
    """Service startup recovery does not collect news or consume model tokens."""
    dedupe = Dedupe()
    try:
        return {'pushed': _deliver_pending(cfg, dedupe)}
    finally:
        dedupe.close()


def run_realtime(cfg):
    dedupe = Dedupe()
    try:
        return _run_realtime(cfg, dedupe)
    finally:
        dedupe.close()


def _run_realtime(cfg, dedupe):
    maintenance = dedupe.cache_maintenance(cfg)
    if maintenance['removed']:
        log.info('缓存定时清空完成：删除 %d 条新闻。', maintenance['removed'])
    generation = dedupe.cache_generation()
    items = collect_all(cfg)
    if not items:
        log.info('没有采集条目，仍检查已评分待发新闻。')
        return {'pushed': _deliver_pending(cfg, dedupe)}
    fresh = [it for it in items if not dedupe.is_seen(it.url)]
    log.info("去重后新增 %d 条", len(fresh))
    title_map = {}
    for it in fresh:
        norm = _norm_title(it.title)
        if not norm:
            continue
        if norm in title_map:
            prev = title_map[norm]
            if it.published_at and (not prev.published_at or it.published_at < prev.published_at):
                log.info("同事件选更早时间: %s (%s -> %s)", it.title[:40], prev.published_at or "无", it.published_at)
                title_map[norm] = it
            else:
                log.info("标题重复跳过: %s", it.title[:40])
        else:
            title_map[norm] = it
    fresh = list(title_map.values())
    log.info("标题去重后 %d 条", len(fresh))
    fresh = dedupe.dedup_same_event(fresh, window_hours=6)
    log.info("实体去重后 %d 条", len(fresh))
    if not fresh:
        return {'pushed': _deliver_pending(cfg, dedupe)}
    llm = LLMClient(cfg)
    batch_size = int(cfg.get("scoring.llm_batch_size", 8))
    processed = process_items(llm, fresh, batch_size)
    night = is_night_hour()
    if night:
        threshold = cfg.get("scoring.realtime_threshold", 7)
    else:
        threshold = cfg.get("scoring.major_threshold", 9)
    max_push = int(cfg.get("scoring.max_realtime_per_cycle", 30))
    skipped = {"novalue": 0, "untrust": 0, "lowscore": 0}
    # Save the whole scored batch before applying the notification limit. A
    # limit stops sending, not remembering; otherwise the next cycle pays to
    # score the remaining items again and the daily report never sees them.
    for it in processed:
        dedupe.add_seen(it.url, title=it.display_title(), score=it.score, category=it.category,
                        published_at=it.published_at, credibility=it.credibility, valuable=1 if it.is_valuable else 0,
                        comment=it.comment, utility=it.utility, summary=it.summary,
                        reason=it.reason, source=it.source, original_title=it.title,
                        description=it.desc, image=it.image, expected_generation=generation)
    for it in sorted(processed, key=lambda x: -_push_score(x)):
        if not it.is_valuable:
            skipped["novalue"] += 1
            continue
        if it.credibility == "不可信":
            skipped["untrust"] += 1
            log.info("不可信过滤: %s (%s)", it.title[:40], it.credibility)
            continue
        if it.credibility == "存疑" and it.score < cfg.get("scoring.doubt_threshold", 9):
            skipped["untrust"] += 1
            log.info("存疑低分过滤: %s", it.title[:40])
            continue
        ps = _push_score(it)
        if ps < threshold:
            skipped["lowscore"] += 1
            continue
    pushed = _deliver_pending(cfg, dedupe, max_push)
    log.info("实时推送完成，共推送 %d 条 (夜间=%s, 阈值=%s, 过滤: %s)",
             len(pushed), night, threshold, skipped)
    return {"pushed": pushed}


def _fallback_daily_plain(entries, report_date=None):
    """LLM失败时的降级日报：纯文本条目列表"""
    lines = ["科技新闻日报 " + (report_date or time.strftime("%m-%d")), ""]
    for row in entries:
        title = row[0]
        url = row[1]
        score = row[2]
        category = row[3]
        published_at = row[4] if len(row) > 4 else ""
        credibility = row[5] if len(row) > 5 else ""
        pushed = row[8] if len(row) > 8 else 0
        tag = " [已推送]" if pushed else ""
        cat_name = CATEGORY_NAMES.get(category, category)
        t = format_time(published_at)
        time_str = f"[{t}] " if t else ""
        cred_str = f"[{credibility}] " if credibility else ""
        lines.append(f"- {time_str}{cred_str}[{cat_name}]{title}{tag}（{score:.0f}分）")
        lines.append(f"  {url}")
        lines.append("")
    return "\n".join(lines)


def _entry_row_plain(row):
    title, url, score, category = row[0], row[1], row[2], row[3]
    published_at = row[4] if len(row) > 4 else ""
    credibility = row[5] if len(row) > 5 else ""
    comment = row[7] if len(row) > 7 else ""
    cat_name = CATEGORY_NAMES.get(category, category)
    lines = [f"[{format_time(published_at) or '时间未知'}] {cat_name} | {title}",
             f"重要度：{score:.0f}分", f"可信度：{credibility or '未判断'}"]
    if comment:
        lines.append(f"AI评价与建议：{comment}")
    lines.append(url)
    return "\n".join(lines)


def run_daily(cfg):
    dedupe = Dedupe()
    try:
        return _run_daily(cfg, dedupe)
    finally:
        dedupe.close()


def _send_qq_daily(dispatcher, entries, is_current=None):
    """Frame the QQ daily report with separate, ordered plain messages."""
    ok, failed, delivered = [], [], set()

    def send(title, content, kind):
        try:
            return dispatcher.send(title, content, kind=kind, only={'qq'})
        except Exception as exc:
            log.error('QQ 日报发送异常（%s）：%s', kind, type(exc).__name__)
            return [], ['qq']

    opening_ok, opening_failed = send('', '接下来是昨天的新闻总结', 'daily_start')
    if 'qq' not in opening_ok or opening_failed:
        return [], ['qq'], delivered
    if not entries:
        body_ok, body_failed = send('', '昨天暂无达到筛选标准的重要新闻。', 'daily_empty')
        ok.extend(body_ok)
        failed.extend(body_failed or ([] if 'qq' in body_ok else ['qq']))
    for row in entries:
        if is_current is not None and not is_current(row[1]):
            continue
        item_ok, item_failed = send(
            f'{CATEGORY_NAMES.get(row[3], row[3])} | {row[0]}',
            _entry_row_plain(row), 'daily_item')
        ok.extend(item_ok)
        failed.extend(item_failed or ([] if 'qq' in item_ok else ['qq']))
        if 'qq' in item_ok:
            delivered.add(row[1])
    if not failed:
        closing_ok, closing_failed = send('', '昨天的总结到此结束', 'daily_end')
        failed.extend(closing_failed or ([] if 'qq' in closing_ok else ['qq']))
    else:
        log.warning('QQ 日报有内容未发送成功，未发送结束播报。')
    return ok, failed, delivered


def _run_daily(cfg, dedupe):
    # 08:00 日报回顾昨天完整自然日，优先使用新闻自身发布时间。
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    generation = dedupe.cache_generation()
    entries = dedupe.period_entries(
        yesterday.strftime("%Y-%m-%d 00:00:00"), today.strftime("%Y-%m-%d 00:00:00"),
        yesterday.timestamp(), today.timestamp(),
        min_score=cfg.get("scoring.min_for_daily", 5),
        max_items=int(cfg.get("scoring.max_daily_items", 40)),
    )
    max_daily = int(cfg.get("scoring.max_daily_items", 40))
    entries = [row for row in entries if len(row) <= 5 or row[5] != "不可信"][:max_daily]
    dispatcher = Dispatcher(cfg)
    enabled = set(cfg.enabled_pushes())
    title = "科技新闻日报 " + yesterday.strftime("%m-%d")
    if not entries:
        log.info("今日无足够条目，发送暂无消息")
        ok, failed = [], []
        if 'qq' in enabled:
            qq_ok, qq_failed, _ = _send_qq_daily(dispatcher, [])
            ok.extend(qq_ok)
            failed.extend(qq_failed)
        if enabled & {'email', 'telegram'}:
            try:
                report_ok, report_failed = dispatcher.send(title, '昨天暂无达到筛选标准的重要新闻。',
                                                           kind='daily', only={'email', 'telegram'})
                ok.extend(report_ok)
                failed.extend(report_failed)
            except Exception as exc:
                log.error('日报暂无消息发送失败：%s', type(exc).__name__)
                failed.extend(enabled & {'email', 'telegram'})
        log.info('日报推送完成: ok=%s failed=%s', ok, failed)
        return {'ok': ok, 'failed': failed}
    ok, failed, delivered = [], [], set()
    if enabled & {"email", "telegram"}:
        # QQ uses the already-scored per-item interpretation below, so a QQ-only
        # deployment has no use for a second model call that writes an overview.
        report_entries = _pick_entries(entries, max_daily)
        html_body, plain_body = None, None
        try:
            html_body, plain_body = generate_daily_report(LLMClient(cfg), report_entries)
        except Exception as exc:
            log.warning("日报生成失败，使用已评分条目列表：%s", type(exc).__name__)
        if not plain_body:
            plain_body = _fallback_daily_plain(report_entries, yesterday.strftime("%m-%d"))
        if dedupe.cache_generation() != generation:
            log.info('日报生成期间缓存已被清理，取消旧快照，后续按最新缓存重新检查。')
            return {'ok': [], 'failed': []}
        try:
            report_ok, report_failed = dispatcher.send(title, plain_body, html=html_body,
                                                       kind="daily", only={"email", "telegram"})
            ok.extend(report_ok)
            failed.extend(report_failed)
            if report_ok:
                delivered.update(row[1] for row in report_entries)
        except Exception as exc:
            log.error("日报整份推送异常：%s", type(exc).__name__)
    if "qq" in enabled:
        qq_ok, qq_failed, qq_delivered = _send_qq_daily(dispatcher, entries,
            is_current=lambda url: dedupe.cache_contains(url, expected_generation=generation))
        ok.extend(qq_ok)
        failed.extend(qq_failed)
        delivered.update(qq_delivered)
    if delivered and dedupe.cache_generation() == generation:
        dedupe.mark_in_daily(sorted(delivered))
    log.info("日报推送完成: ok=%s failed=%s", ok, failed)
    return {'ok': ok, 'failed': failed}
