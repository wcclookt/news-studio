"""Cache retention, duplicate decisions and live deletion on temporary databases."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from news_agent.config import Config
from news_agent.config_store import validate_config, validate_config_structure
from news_agent.dedupe import Dedupe
from news_agent.news_cache import CACHE_DEFAULTS, CacheLibrary, canonical_url
from news_agent.orchestrator import _deliver_pending, run_realtime
from news_agent.sources.base import NewsItem


def article(index=0, **changes):
    return NewsItem(**{'title': f'独立新闻事件 {index} 的完整标题', 'url': f'https://example.test/news/{index}',
                       'source': '测试来源', 'desc': f'独立新闻事件{index}提供新的事实内容。',
                       'score': 9, 'utility': 9, 'credibility': '较可信', **changes})


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'news.db'
        self.db = Dedupe(self.path)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def add(self, index=0, **changes):
        item = article(index, **changes)
        self.db.add_seen(item.url, title=item.title, source=item.source, score=item.score,
                         utility=item.utility, credibility=item.credibility, description=item.desc)
        return item

    def test_tracking_urls_normalize_but_real_query_identifiers_are_preserved(self):
        original = 'http://Example.test/news?id=42&utm_source=rss&fbclid=abc#section'
        self.assertEqual(canonical_url(original), 'https://example.test/news?id=42')
        self.db.add_seen(original, title='新闻原始标题')
        self.assertTrue(self.db.is_seen('https://example.test/news?utm_campaign=feed&id=42'))
        self.assertFalse(self.db.is_seen('https://example.test/news?id=43'))

    def test_same_headline_is_blocked_across_entire_retained_history(self):
        item = self.add()
        self.db.conn.execute('UPDATE seen SET created_at=1')
        self.db.conn.commit()
        repeated = article(2, title=item.title + '！')
        self.assertEqual(self.db.dedup_same_event([repeated]), [])
        self.assertFalse(self.db.add_seen(repeated.url, title=repeated.title))
        self.assertEqual(self.db.cache_page()['count'], 1)

    def test_original_title_deduplicates_even_when_display_has_translation(self):
        self.db.add_seen('https://example.test/old', title='New chip launched\n译文：新芯片发布', original_title='New chip launched')
        self.assertEqual(self.db.dedup_same_event([article(title='New chip launched', url='https://second.test/new')]), [])

    def test_long_duplicate_body_with_new_title_is_not_inserted(self):
        desc = '这是一份有明确数字42的完整新闻事实，包含产品发布、使用条件和上市信息。' * 8
        self.add(desc=desc)
        repeated = article(2, title='另一媒体采用不同标题', desc='<p>' + desc + '</p>')
        self.assertEqual(self.db.dedup_same_event([repeated]), [])
        self.assertFalse(self.db.add_seen(repeated.url, title=repeated.title, description=repeated.desc))

    def test_short_boilerplate_is_not_used_to_merge_different_events(self):
        self.add(desc='点击查看完整内容')
        other = article(2, desc='点击查看完整内容')
        self.assertEqual(self.db.dedup_same_event([other]), [other])

    def test_decimal_versions_do_not_collapse_when_punctuation_is_normalized(self):
        self.add(title='新闻：模型 v1.1 正式发布全新功能')
        other = article(2, title='新闻：模型 v11 正式发布全新功能')
        self.assertEqual(self.db.dedup_same_event([other]), [other])
        self.assertTrue(self.db.add_seen(other.url, title=other.title))

    def test_duplicates_within_one_collection_are_filtered(self):
        first = article()
        duplicate = article(2, title=first.title, url=first.url + '?utm_source=rss')
        self.assertEqual(self.db.dedup_same_event([first, duplicate]), [first])

    def test_final_insert_rechecks_concurrent_collector(self):
        def insert(_):
            with Dedupe(self.path) as db:
                return db.add_seen('https://example.test/same', title='两个采集线程同时遇到的一条新闻')
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(insert, range(4)))
        self.assertEqual(sum(results), 1)

    def test_manual_clear_cancels_inflight_scoring_generation(self):
        generation = self.db.cache_generation()
        self.db.delete_cached(all_matching=True)
        self.assertFalse(self.db.add_seen(article().url, title=article().title, expected_generation=generation))
        self.assertEqual(self.db.cache_page()['count'], 0)

    def test_selective_delete_removes_delivery_record_but_keeps_other_news(self):
        first, second = self.add(), self.add(2)
        self.db.record_realtime_delivery(first.url, ['qq'])
        self.db.mark_scheduled_done('daily:2026-10-24')
        key = next(row['url_hash'] for row in self.db.cache_page()['rows'] if row['url'] == first.url)
        self.assertEqual(self.db.delete_cached([key]), 1)
        self.assertFalse(self.db.is_seen(first.url))
        self.assertTrue(self.db.is_seen(second.url))
        self.assertEqual(self.db.realtime_delivered_channels(first.url), set())
        self.assertTrue(self.db.scheduled_done('daily:2026-10-24'))

    def test_delete_all_matching_uses_selection_snapshot_not_new_arrivals(self):
        self.add(0)
        page = self.db.cache_page('测试来源')
        self.add(2)
        removed = self.db.delete_cached(all_matching=True, search='测试来源', cutoff=page['cutoff'], expected_generation=page['generation'])
        self.assertEqual(removed, 1)
        self.assertTrue(self.db.is_seen(article(2).url))

    def test_stale_selection_after_other_clear_is_rejected(self):
        self.add()
        page = self.db.cache_page()
        self.db.delete_cached(all_matching=True)
        self.add(2)
        with self.assertRaisesRegex(ValueError, '刷新'):
            self.db.delete_cached(all_matching=True, expected_generation=page['generation'])
        self.assertTrue(self.db.is_seen(article(2).url))

    def test_default_schedule_waits_seven_calendar_days_and_cleans_all(self):
        start = datetime(2026, 10, 24, 10).timestamp()
        self.db.conn.execute('UPDATE cache_state SET last_cleared_at=?', (start,))
        self.db.conn.commit()
        self.add()
        due = datetime(2026, 10, 31, 3).timestamp()
        before = self.db.cache_maintenance({}, now=due-1)
        self.assertEqual(before['removed'], 0)
        self.assertEqual(before['next_cleanup_at'], due)
        result = self.db.cache_maintenance({}, now=due)
        self.assertEqual(result['removed'], 1)
        self.assertEqual(self.db.cache_page()['count'], 0)
        self.assertEqual(result['next_cleanup_at'], datetime(2026, 11, 7, 3).timestamp())

    def test_disabled_autoclear_retains_unlimited_rows_without_age_limit(self):
        self.add()
        self.db.conn.execute('UPDATE cache_state SET last_cleared_at=1')
        self.db.conn.commit()
        result = self.db.cache_maintenance({'cache': {'auto_clear': False}}, now=datetime(2026, 10, 24).timestamp())
        self.assertEqual(result['removed'], 0)
        self.assertIsNone(result['next_cleanup_at'])
        self.assertEqual(self.db.cache_page()['total'], 1)

    def test_custom_schedule_catches_up_once_after_offline_period(self):
        self.db.conn.execute('UPDATE cache_state SET last_cleared_at=?', (datetime(2026, 10, 1, 10).timestamp(),))
        self.db.conn.commit()
        self.add()
        policy = {'cache': {'auto_clear': True, 'interval_days': 3, 'clear_time': '12:15'}}
        now = datetime(2026, 10, 24, 14).timestamp()
        self.assertEqual(self.db.cache_maintenance(policy, now)['removed'], 1)
        self.add(2)
        self.assertEqual(self.db.cache_maintenance(policy, now+30)['removed'], 0)
        self.assertEqual(self.db.cache_page()['total'], 1)

    def test_manual_clear_resets_cycle_and_preserves_daily_completion(self):
        self.add()
        self.db.mark_scheduled_done('daily:2026-10-24')
        reset = datetime(2026, 10, 24, 12).timestamp()
        self.assertEqual(self.db.delete_cached(all_matching=True, now=reset), 1)
        self.assertTrue(self.db.scheduled_done('daily:2026-10-24'))
        self.assertEqual(self.db.cache_maintenance({}, now=reset)['next_cleanup_at'], datetime(2026, 10, 31, 3).timestamp())

    def test_delivery_finishing_after_clear_does_not_resurrect_old_markers(self):
        self.add()
        generation = self.db.cache_generation()
        self.db.delete_cached(all_matching=True)
        self.add()
        self.assertFalse(self.db.record_realtime_delivery(article().url, ['qq'], expected_generation=generation))
        self.db.mark_pushed(article().url, expected_generation=generation)
        self.assertEqual(self.db.realtime_delivered_channels(article().url), set())
        self.assertFalse(self.db.cache_page()['rows'][0]['pushed_realtime'])

    def test_pagination_search_and_literal_wildcards(self):
        for index in range(7):
            self.add(index)
        first = self.db.cache_page(page_size=3)
        last = self.db.cache_page(page=2, page_size=3)
        self.assertEqual(first['count'], 7)
        self.assertEqual(len(first['rows']), 3)
        self.assertEqual(len(last['rows']), 1)
        self.assertFalse(set(row['url_hash'] for row in first['rows']) & set(row['url_hash'] for row in last['rows']))
        self.assertEqual(self.db.cache_page('%')['count'], 0)
        self.assertEqual(self.db.cache_page('事件 2')['count'], 1)

    def test_cache_policy_types_and_ranges_are_validated(self):
        for value in [{'auto_clear': 'no'}, {'interval_days': 0}, {'clear_time': '25:00'}]:
            with self.subTest(value=value):
                self.assertTrue(validate_config({'cache': value}))
        self.assertEqual(validate_config({'cache': CACHE_DEFAULTS}), [])

    def test_known_duplicates_never_invoke_ai_or_send(self):
        self.add()
        config = Config({'push': {'enabled': ['qq']}, 'cache': {'auto_clear': False}})
        with patch('news_agent.orchestrator.Dedupe', side_effect=lambda: Dedupe(self.path)), \
                patch('news_agent.orchestrator.collect_all', return_value=[article(2, title=article().title)]), \
                patch('news_agent.orchestrator.LLMClient') as model, \
                patch('news_agent.orchestrator.Dispatcher') as dispatcher:
            # The existing first record was already delivered; its reappearance
            # cannot create a second message or re-enter the scorer.
            self.db.mark_pushed(article().url)
            self.assertEqual(run_realtime(config), {'pushed': []})
        model.assert_not_called()
        dispatcher.assert_not_called()

    def test_deletion_between_queue_snapshot_and_send_prevents_delivery(self):
        item = self.add()
        def removed_queue():
            self.db.delete_cached(all_matching=True)
            return [item]
        dispatcher = Mock()
        with patch.object(self.db, 'pending_realtime', side_effect=removed_queue), \
                patch('news_agent.orchestrator.Dispatcher', return_value=dispatcher):
            _deliver_pending(Config({'push': {'enabled': ['qq']}}), self.db)
        dispatcher.send.assert_not_called()


class CacheUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from news_agent.desktop.window import ConfigWindow
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'config.json'
        self.path.write_text(json.dumps({'push': {'enabled': []}, 'cache': {'auto_clear': False}}), encoding='utf-8')
        self.db_path = Path(self.temp.name) / 'news.db'
        with Dedupe(self.db_path) as db:
            for index in range(3):
                db.add_seen(article(index).url, title=article(index).title, source='来源', summary='摘要')
        self.window = ConfigWindow(self.path, monitor_services=False, cache_path=self.db_path)
        self.page = self.window.cache_page
        self.page.render(self.page.library.snapshot(self.window.store.read()))

    def tearDown(self):
        self.window.stop_monitoring()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_list_selection_multi_select_select_all_and_details(self):
        self.assertEqual(self.window.stack.count(), 8)
        self.assertEqual(self.page.table.rowCount(), 3)
        self.page.select_page()
        self.assertEqual(len(self.page.selected_keys()), 3)
        self.page.clear_selection()
        self.assertEqual(self.page.selected_keys(), [])
        self.page.table.selectRow(0)
        self.assertIn('摘要', self.page.details.toPlainText())
        self.page.select_all_results()
        self.assertEqual(self.page._all_selection['count'], 3)

    def test_delete_selected_keeps_unselected_and_does_not_touch_config(self):
        original = self.path.read_bytes()
        self.page.table.selectRow(0)
        scheduled = []
        with patch.object(self.page, '_submit', side_effect=lambda action, callback: scheduled.append((action, callback))), \
                patch('news_agent.desktop.cache_page.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
            self.page.delete_selected()
        self.assertEqual(scheduled[0][1](), 1)
        self.assertEqual(self.page.library.run('cache_page')['total'], 2)
        self.assertEqual(self.path.read_bytes(), original)

    def test_clear_cancel_changes_nothing(self):
        with patch('news_agent.desktop.cache_page.QMessageBox.question', return_value=QMessageBox.StandardButton.No), \
                patch.object(self.page, '_submit') as work:
            self.page.clear_library()
        work.assert_not_called()
        self.assertEqual(self.page.library.run('cache_page')['total'], 3)

    def test_settings_round_trip_and_do_not_expose_auth_checkbox_label(self):
        self.window.fields['cache.interval_days'][0].setValue(14)
        self.window.fields['cache.clear_time'][0].setTime(self.window.fields['cache.clear_time'][0].time().addSecs(3600))
        self.assertEqual(self.window.fields['cache.auto_clear'][0].text(), '启用自动定时清空')
        self.assertTrue(self.window.save())
        data = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(data['cache']['interval_days'], 14)
        self.assertEqual(data['cache']['clear_time'], '04:00')
        self.assertFalse(data['cache']['auto_clear'])

    def test_cross_page_select_all_deletes_all_results_not_only_loaded_page(self):
        with Dedupe(self.db_path) as db:
            for index in range(3, 112):
                db.add_seen(article(index).url, title=article(index).title)
        self.page.render(self.page.library.snapshot(self.window.store.read()))
        self.assertEqual(self.page.table.rowCount(), 100)
        self.page.select_all_results()
        scheduled = []
        with patch.object(self.page, '_submit', side_effect=lambda action, callback: scheduled.append(callback)), \
                patch('news_agent.desktop.cache_page.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
            self.page.delete_selected()
        self.assertEqual(scheduled[0](), 112)
        self.assertEqual(self.page.library.run('cache_page')['total'], 0)

    def test_refresh_keeps_multi_selection_but_new_page_clears_old_details(self):
        self.page.select_page()
        self.page.render(self.page.library.snapshot(self.window.store.read()))
        self.assertEqual(len(self.page.selected_keys()), 3)
        self.page.search.setText('no-such-news')
        self.page.render(self.page.library.snapshot(self.window.store.read(), 'no-such-news'))
        self.assertEqual(self.page.selected_keys(), [])
        self.assertEqual(self.page.details.toPlainText(), '')


if __name__ == '__main__':
    unittest.main()
