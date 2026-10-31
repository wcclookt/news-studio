# Copyright (c) 2026 西新 (wcclookt). All rights reserved.
# First-party portions only; see LICENSE and THIRD_PARTY_NOTICES.md.

"""QQ summary boundaries use deterministic messages, never model calls."""

import unittest
from unittest.mock import Mock

from news_agent.orchestrator import _send_qq_daily
from news_agent.push.qq import QQChannel


class DailyAnnouncementTests(unittest.TestCase):
    def setUp(self):
        self.dispatcher = Mock()
        self.dispatcher.send.return_value = (['qq'], [])
        self.entries = [(f'新闻{i}', f'https://example.com/{i}', 8, 'science',
                         '', '较可信', 1, '已有解读', 0) for i in range(2)]

    def test_intro_items_and_outro_are_separate_and_ordered(self):
        ok, failed, delivered = _send_qq_daily(self.dispatcher, self.entries)
        calls = self.dispatcher.send.call_args_list
        self.assertEqual([call.kwargs['kind'] for call in calls],
                         ['daily_start', 'daily_item', 'daily_item', 'daily_end'])
        self.assertEqual(calls[0].args, ('', '接下来是昨天的新闻总结'))
        self.assertEqual(calls[-1].args, ('', '昨天的总结到此结束'))
        self.assertEqual(delivered, {row[1] for row in self.entries})
        self.assertEqual(failed, [])
        self.assertTrue(all(call.kwargs['only'] == {'qq'} for call in calls))

    def test_empty_report_still_has_both_boundaries(self):
        _send_qq_daily(self.dispatcher, [])
        calls = self.dispatcher.send.call_args_list
        self.assertEqual([call.kwargs['kind'] for call in calls],
                         ['daily_start', 'daily_empty', 'daily_end'])
        self.assertIn('昨天暂无', calls[1].args[1])

    def test_failed_intro_does_not_send_items_or_outro(self):
        self.dispatcher.send.return_value = ([], ['qq'])
        self.assertEqual(_send_qq_daily(self.dispatcher, self.entries), ([], ['qq'], set()))
        self.dispatcher.send.assert_called_once()

    def test_partial_failure_keeps_successes_without_claiming_complete(self):
        self.dispatcher.send.side_effect = [(['qq'], []), ([], ['qq']), (['qq'], [])]
        ok, failed, delivered = _send_qq_daily(self.dispatcher, self.entries)
        self.assertEqual(delivered, {self.entries[1][1]})
        self.assertEqual(failed, ['qq'])
        self.assertNotIn('daily_end', [call.kwargs['kind'] for call in self.dispatcher.send.call_args_list])

    def test_outro_failure_is_reported(self):
        self.dispatcher.send.side_effect = [(['qq'], []), (['qq'], []), ([], ['qq'])]
        ok, failed, delivered = _send_qq_daily(self.dispatcher, self.entries[:1])
        self.assertEqual(failed, ['qq'])
        self.assertEqual(delivered, {self.entries[0][1]})

    def test_qq_standalone_phrase_has_no_extra_title_or_blank_lines(self):
        channel = QQChannel({})
        self.assertEqual(channel._split('', '接下来是昨天的新闻总结'), ['接下来是昨天的新闻总结'])
        self.assertEqual(channel._split('标题', '新闻内容'), ['标题\n\n新闻内容'])

    def test_cache_deleted_items_are_not_sent_from_old_daily_snapshot(self):
        ok, failed, delivered = _send_qq_daily(self.dispatcher, self.entries,
                                              is_current=lambda url: url == self.entries[1][1])
        items = [call for call in self.dispatcher.send.call_args_list if call.kwargs['kind'] == 'daily_item']
        self.assertEqual(len(items), 1)
        self.assertIn(self.entries[1][0], items[0].args[0])
        self.assertEqual(delivered, {self.entries[1][1]})


if __name__ == '__main__':
    unittest.main()
