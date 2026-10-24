import tempfile
from pathlib import Path
import unittest

from news_agent.dedupe import Dedupe
from news_agent.sources.base import NewsItem


class EventDedupeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Dedupe(str(Path(self.temp.name) / 'news.db'))

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    @staticmethod
    def item(title, number=1, published_at=''):
        return NewsItem(title=title, url=f'https://example.com/{number}', source='测试', published_at=published_at)

    def test_same_platform_different_events_are_kept(self):
        first = self.item('Steam 推出全新的家庭共享功能', 1)
        second = self.item('Steam 公布本月最畅销游戏榜单', 2)
        self.assertEqual(self.db.dedup_same_event([first, second]), [first, second])

    def test_different_model_versions_are_not_merged(self):
        first = self.item('苹果发布 iPhone 16 新机详细参数正式公布', 1)
        second = self.item('苹果发布 iPhone 17 新机详细参数正式公布', 2)
        self.assertEqual(self.db.dedup_same_event([first, second]), [first, second])

    def test_repeated_title_keeps_earlier_report_even_when_it_arrives_second(self):
        later = self.item('Steam 推出全新的家庭共享功能', 1, '2026-10-17T09:00:00')
        earlier = self.item('Steam 推出全新的家庭共享功能！', 2, '2026-10-17T08:00:00')
        self.assertEqual(self.db.dedup_same_event([later, earlier]), [earlier])

    def test_single_item_checks_recent_database_titles(self):
        self.db.add_seen('https://example.com/stored', title='Steam 推出全新的家庭共享功能')
        self.assertEqual(self.db.dedup_same_event([self.item('Steam 推出全新的家庭共享功能！')]), [])
        different = self.item('Steam 公布本月最畅销游戏榜单')
        self.assertEqual(self.db.dedup_same_event([different]), [different])


if __name__ == '__main__':
    unittest.main()
