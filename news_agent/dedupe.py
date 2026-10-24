import hashlib
import os
import re
import sqlite3
import time
from difflib import SequenceMatcher

from .logger import get_logger
from .news_cache import CacheMixin, canonical_url, title_key, content_key

log = get_logger()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

def _hash_url(url):
    return hashlib.sha256((url or "").encode("utf-8")).hexdigest()


def _norm_title(title):
    if not title:
        return ""
    t = re.sub(r"[\s\u3000]+", "", title)
    t = re.sub(r"[，。！？、；：""''（）()\[\]【】\-—_*|/\\#@]", "", t)
    t = t.lower()
    return t.strip()


def _same_event_title(first, second):
    """Sharing a platform/product alone does not make two reports one event."""
    left, right = _norm_title(first), _norm_title(second)
    if not left or not right:
        return False
    if left == right:
        return True
    if min(len(left), len(right)) < 12:
        return False
    if set(re.findall(r'\d+(?:\.\d+)*', left)) != set(re.findall(r'\d+(?:\.\d+)*', right)):
        return False
    return SequenceMatcher(None, left, right, autojunk=False).ratio() >= .90


class Dedupe(CacheMixin):
    def __init__(self, db_path=None):
        db_path = db_path or os.path.join(DATA_DIR, "news.db")
        db_path = os.fspath(db_path)
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=10)
        self.conn.execute('PRAGMA journal_mode=WAL')
        self._init_tables()
        self._init_cache()

    def _init_tables(self):
        c = self.conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS seen (
                url_hash TEXT PRIMARY KEY,
                title TEXT,
                url TEXT,
                score REAL DEFAULT 0,
                category TEXT DEFAULT '',
                pushed_realtime INTEGER DEFAULT 0,
                in_daily INTEGER DEFAULT 0,
                published_at TEXT DEFAULT '',
                credibility TEXT DEFAULT '',
                valuable INTEGER DEFAULT 1,
                comment TEXT DEFAULT '',
                created_at REAL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS sent_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT,
                kind TEXT,
                title TEXT,
                status TEXT,
                created_at REAL
            )
            """
        )
        for col in (
            "published_at TEXT DEFAULT ''",
            "credibility TEXT DEFAULT ''",
            "valuable INTEGER DEFAULT 1",
            "comment TEXT DEFAULT ''",
            "utility REAL DEFAULT 5",
            "summary TEXT DEFAULT ''",
            "reason TEXT DEFAULT ''",
            "source TEXT DEFAULT ''",
        ):
            try:
                c.execute(f"ALTER TABLE seen ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
        c.execute('CREATE INDEX IF NOT EXISTS seen_created_at_idx ON seen(created_at)')
        c.execute('''CREATE TABLE IF NOT EXISTS realtime_deliveries (
            url_hash TEXT NOT NULL, channel TEXT NOT NULL, created_at REAL NOT NULL,
            PRIMARY KEY(url_hash, channel))''')
        c.execute('''CREATE TABLE IF NOT EXISTS scheduled_runs (
            identity TEXT PRIMARY KEY, finished_at REAL NOT NULL)''')
        self.conn.commit()

    def is_seen(self, url):
        return self.conn.execute('SELECT 1 FROM seen WHERE url_key=? LIMIT 1', (canonical_url(url),)).fetchone() is not None

    def is_similar_title(self, title, threshold=0.85, since=None):
        t = _norm_title(title)
        if not t:
            return False
        since = since or (time.time() - 24 * 3600)
        c = self.conn.cursor()
        c.execute("SELECT title FROM seen WHERE created_at>=?", (since,))
        rows = c.fetchall()
        for (stored,) in rows:
            s = _norm_title(stored)
            if not s:
                continue
            if t == s or s in t or t in s:
                return True
            if len(t) > 8 and len(s) > 8:
                if SequenceMatcher(None, t, s).ratio() > threshold:
                    return True
        return False

    def _seen(self, url):
        c = self.conn.cursor()
        c.execute("SELECT * FROM seen WHERE url_hash=?", (_hash_url(url),))
        return c.fetchone()

    def add_seen(self, url, title="", score=0.0, category="", pushed_realtime=0, in_daily=0,
                 published_at="", credibility="", valuable=1, comment="", utility=5.0,
                 summary="", reason="", source="", original_title='', description='', image='',
                 expected_generation=None):
        raw_title = original_title or title.split('\n译文：', 1)[0]
        key = title_key(raw_title)
        # Serialize comparison+insertion: two collectors cannot both add the
        # same canonical URL/headline. A clear during AI work cancels that batch.
        with self.conn:
            if not self.conn.in_transaction:
                self.conn.execute('BEGIN IMMEDIATE')
            if expected_generation is not None and expected_generation != self.cache_generation():
                return False
            if self._cache_match(url, raw_title, description):
                return False
            result = self.conn.execute(
            """
            INSERT OR IGNORE INTO seen
            (url_hash, title, url, score, category, pushed_realtime, in_daily, published_at,
             credibility, valuable, comment, utility, created_at, summary, reason, source,
             original_title,url_key,title_key,title_prefix,content_key,description,image)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (_hash_url(url), title, url, score, category, pushed_realtime, in_daily, published_at,
             credibility, 1 if valuable else 0, comment, utility, time.time(), summary, reason, source,
             raw_title,canonical_url(url),key,key[:8],content_key(description),description,image),
            )
            return result.rowcount == 1

    def pending_realtime(self, hours=24):
        """Previously scored items remain deliverable after a failed push.

        Do not re-score or replay old history. Existing databases can recover
        stored title/comment/score even if earlier code omitted the summary.
        """
        from .sources.base import NewsItem
        from urllib.parse import urlparse

        rows = self.conn.execute('''SELECT title,url,score,category,published_at,
                credibility,valuable,comment,utility,summary,reason,source,image
            FROM seen WHERE pushed_realtime=0 AND in_daily=0 AND valuable=1
                AND created_at>=? ORDER BY score*.6+utility*.4 DESC,created_at ASC''',
            (time.time() - hours * 3600,)).fetchall()
        return [NewsItem(title=row[0], url=row[1], score=row[2], category=row[3],
                        published_at=row[4], credibility=row[5], is_valuable=bool(row[6]),
                        comment=row[7], utility=row[8], summary=row[9], reason=row[10],
                        source=row[11] or urlparse(row[1]).hostname or '已采集新闻', image=row[12]) for row in rows]

    def realtime_delivered_channels(self, url):
        return {row[0] for row in self.conn.execute(
            'SELECT channel FROM realtime_deliveries WHERE url_hash=?', (_hash_url(url),))}

    def record_realtime_delivery(self, url, channels, expected_generation=None):
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            if expected_generation is not None and not self.cache_contains(url, expected_generation):
                return False
            self.conn.executemany('INSERT OR IGNORE INTO realtime_deliveries VALUES (?,?,?)',
                                  [(_hash_url(url), channel, time.time()) for channel in channels])
        return True

    def scheduled_done(self, identity):
        return self.conn.execute('SELECT 1 FROM scheduled_runs WHERE identity=?', (identity,)).fetchone() is not None

    def mark_scheduled_done(self, identity):
        self.conn.execute('INSERT OR REPLACE INTO scheduled_runs VALUES (?,?)', (identity, time.time()))
        self.conn.commit()

    def mark_pushed(self, url, expected_generation=None):
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            if expected_generation is not None and not self.cache_contains(url, expected_generation):
                return
            self.conn.execute('UPDATE seen SET pushed_realtime=1 WHERE url_hash=?', (_hash_url(url),))

    def mark_in_daily(self, urls):
        c = self.conn.cursor()
        for url in urls:
            c.execute("UPDATE seen SET in_daily=1 WHERE url_hash=?", (_hash_url(url),))
        self.conn.commit()

    def today_entries(self, min_score=0, since=None, exclude_pushed=True):
        since = since or (time.time() - 24 * 3600)
        c = self.conn.cursor()
        if exclude_pushed:
            c.execute(
                "SELECT title, url, score, category, published_at, credibility, valuable, comment FROM seen WHERE created_at>=? AND score>=? AND pushed_realtime=0 AND valuable=1 ORDER BY score DESC",
                (since, min_score),
            )
        else:
            c.execute(
                "SELECT title, url, score, category, published_at, credibility, valuable, comment FROM seen WHERE created_at>=? AND score>=? AND valuable=1 ORDER BY score DESC",
                (since, min_score),
            )
        return c.fetchall()

    def period_entries(self, start_text, end_text, start_epoch=None, end_epoch=None,
                       min_score=5, max_items=40):
        """按新闻发布时间查询一个自然日；缺少发布时间的条目按入库时间兜底。"""
        start_epoch = start_epoch if start_epoch is not None else time.time() - 24 * 3600
        end_epoch = end_epoch if end_epoch is not None else time.time()
        c = self.conn.cursor()
        c.execute(
            """SELECT title, url, score, category, published_at, credibility, valuable,
                      comment, pushed_realtime
               FROM seen
               WHERE score>=? AND valuable=1 AND
                     ((published_at>=? AND published_at<?) OR
                      (published_at='' AND created_at>=? AND created_at<?))
               ORDER BY COALESCE(NULLIF(published_at, ''), '9999') ASC, score DESC""",
            (min_score, start_text, end_text, start_epoch, end_epoch),
        )
        rows = c.fetchall()
        return rows[:max_items]

    def today_all_entries(self, min_score=5, since=None, max_items=40):
        """兼容旧调用：查询最近 24 小时的重要条目。"""
        since = since or (time.time() - 24 * 3600)
        c = self.conn.cursor()
        c.execute(
            "SELECT title, url, score, category, published_at, credibility, valuable, comment, pushed_realtime FROM seen WHERE created_at>=? AND score>=? AND valuable=1 ORDER BY score DESC",
            (since, min_score),
        )
        return c.fetchall()[:max_items]

    def dedup_same_event(self, items, window_hours=6):
        """Compare against the entire retained cache, then this collection batch.

        window_hours stays accepted for old callers; retained cache no longer
        expires duplicate protection after six hours.
        """
        if not items:
            return items
        group = []
        urls, contents = set(), set()
        for it in items:
            if self._cache_match(it.url, it.title, it.desc):
                log.info('缓存重复跳过: %s', it.title[:40])
                continue
            urlkey, contentkey = canonical_url(it.url), content_key(it.desc)
            if urlkey in urls or contentkey and contentkey in contents:
                continue
            matched = next((index for index, other in enumerate(group)
                            if _same_event_title(it.title, other.title)), None)
            if matched is None:
                group.append(it)
            elif it.published_at and (not group[matched].published_at or it.published_at < group[matched].published_at):
                group[matched] = it
            urls.add(urlkey)
            if contentkey:
                contents.add(contentkey)
        return group

    def log_send(self, channel, kind, title, status):
        c = self.conn.cursor()
        c.execute(
            "INSERT INTO sent_log (channel, kind, title, status, created_at) VALUES (?,?,?,?,?)",
            (channel, kind, title, status, time.time()),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
