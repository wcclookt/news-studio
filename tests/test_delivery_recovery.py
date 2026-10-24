"""Persistent delivery recovery, with all network and model calls mocked."""

from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from news_agent.config import Config
from news_agent.dedupe import Dedupe
from news_agent.orchestrator import _deliver_pending, retry_pending, run_realtime
from news_agent.scheduler import _run_due_daily, _should_run_daily


def cfg(channels=('qq',)):
    return Config({'push': {'enabled': list(channels), 'qq': {}},
                   'scoring': {'major_threshold': 8, 'realtime_threshold': 8,
                               'max_realtime_per_cycle': 2}})


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'news.db')
        self.db = Dedupe(self.path)
        self.url = 'https://example.com/article'
        self.db.add_seen(self.url, title='已评分待发新闻', score=8, utility=9,
                         category='tech_product', credibility='较可信',
                         summary='重要摘要', source='官方来源', comment='已有解读')

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_missing_recipient_failure_retries_without_new_news_or_model(self):
        failed = Mock()
        failed.send.return_value = ([], ['qq'])
        with patch('news_agent.orchestrator.Dispatcher', return_value=failed):
            self.assertEqual(_deliver_pending(cfg(), self.db), [])
        self.assertEqual(len(self.db.pending_realtime()), 1)
        online = Mock()
        online.send.return_value = (['qq'], [])
        def open_db():
            return Dedupe(self.path)
        with patch('news_agent.orchestrator.Dedupe', side_effect=open_db), \
                patch('news_agent.orchestrator.collect_all', return_value=[]), \
                patch('news_agent.orchestrator.Dispatcher', return_value=online), \
                patch('news_agent.orchestrator.LLMClient') as model:
            self.assertEqual(run_realtime(cfg())['pushed'], [self.url])
            self.assertEqual(run_realtime(cfg())['pushed'], [])
        model.assert_not_called()
        online.send.assert_called_once()
        self.assertIn('重要摘要', online.send.call_args.args[1])
        self.assertEqual(self.db.pending_realtime(), [])

    def test_only_failed_channels_are_retried_across_process_restarts(self):
        first = Mock()
        first.send.return_value = (['qq'], ['email'])
        with patch('news_agent.orchestrator.Dispatcher', return_value=first):
            self.assertEqual(_deliver_pending(cfg(('qq', 'email')), self.db), [self.url])
        self.db.close()
        self.db = Dedupe(self.path)
        second = Mock()
        second.send.return_value = (['email'], [])
        with patch('news_agent.orchestrator.Dispatcher', return_value=second):
            _deliver_pending(cfg(('qq', 'email')), self.db)
        self.assertEqual(second.send.call_args.kwargs['only'], {'email'})
        self.assertEqual(self.db.pending_realtime(), [])

    def test_low_score_untrusted_old_and_daily_delivered_do_not_replay(self):
        self.db.conn.execute('UPDATE seen SET created_at=0 WHERE url=?', (self.url,))
        self.db.add_seen('https://example.com/low', score=7, utility=7)
        self.db.add_seen('https://example.com/fake', score=10, utility=10, credibility='不可信')
        self.db.add_seen('https://example.com/daily', score=10, utility=10, in_daily=1)
        self.db.conn.commit()
        with patch('news_agent.orchestrator.Dispatcher') as dispatcher:
            self.assertEqual(_deliver_pending(cfg(), self.db), [])
        dispatcher.assert_not_called()

    def test_existing_schema_migrates_without_losing_scored_rows(self):
        import sqlite3
        legacy = str(Path(self.temp.name) / 'legacy.db')
        conn = sqlite3.connect(legacy)
        conn.execute('''CREATE TABLE seen (url_hash TEXT PRIMARY KEY,title TEXT,url TEXT,
            score REAL,category TEXT,pushed_realtime INTEGER,in_daily INTEGER,
            published_at TEXT,credibility TEXT,valuable INTEGER,comment TEXT,created_at REAL)''')
        import time
        conn.execute('INSERT INTO seen VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                     ('abc', '旧新闻', self.url, 8, 'other', 0, 0, '', '较可信', 1, '解读', time.time()))
        conn.commit()
        conn.close()
        migrated = Dedupe(legacy)
        try:
            self.assertEqual(migrated.pending_realtime()[0].title, '旧新闻')
            self.assertEqual(migrated.pending_realtime()[0].comment, '解读')
        finally:
            migrated.close()

    def test_daily_due_runs_after_missed_window_and_only_once_after_restart(self):
        config = cfg()
        config.data['schedule'] = {'daily_report_time': '08:00'}
        self.assertFalse(_should_run_daily(datetime(2026, 10, 17, 7, 59), '08:00'))
        self.assertTrue(_should_run_daily(datetime(2026, 10, 17, 8, 11), '08:00'))
        with patch('news_agent.scheduler.Dedupe', side_effect=lambda: Dedupe(self.path)), \
                patch('news_agent.scheduler.run_daily', return_value={'ok': ['qq'], 'failed': []}) as run:
            self.assertTrue(_run_due_daily(config, 'config.json', datetime(2026, 10, 17, 8, 11)))
            self.assertFalse(_run_due_daily(config, 'config.json', datetime(2026, 10, 17, 9)))
        run.assert_called_once()

    def test_failed_daily_does_not_mark_the_day_complete(self):
        with patch('news_agent.scheduler.Dedupe', side_effect=lambda: Dedupe(self.path)), \
                patch('news_agent.scheduler.run_daily', side_effect=[{'ok': [], 'failed': ['qq']}, {'ok': ['qq'], 'failed': []}]) as run:
            self.assertFalse(_run_due_daily(cfg(), 'config.json', datetime(2026, 10, 17, 8, 11)))
            self.assertTrue(_run_due_daily(cfg(), 'config.json', datetime(2026, 10, 17, 8, 12)))
        self.assertEqual(run.call_count, 2)


if __name__ == '__main__':
    unittest.main()
