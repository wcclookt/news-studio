"""Persistent news fingerprints and configurable whole-library maintenance.

The existing SQLite news database remains the single source of truth. AI
results are stored once; duplicate checks themselves need no model requests.
"""

from datetime import datetime, timedelta
import hashlib
import html
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


CACHE_DEFAULTS = {'auto_clear': True, 'interval_days': 7, 'clear_time': '03:00'}
TRACKING_KEYS = {'fbclid', 'gclid', 'msclkid', 'mc_cid', 'mc_eid', 'spm'}


def canonical_url(value):
    value = str(value or '').strip()
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {'http', 'https'} or not parts.hostname:
            return value
        host = parts.hostname.lower()
        if ':' in host:
            host = '[' + host + ']'
        port = parts.port
        default_port = 80 if parts.scheme.lower() == 'http' else 443
        if port and port != default_port:
            host += ':' + str(port)
        query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
                 if not key.lower().startswith('utm_') and key.lower() not in TRACKING_KEYS]
        return urlunsplit(('https', host, parts.path or '/', urlencode(sorted(query)), ''))
    except ValueError:
        return value


def title_key(value):
    # Existing rows may contain a display title followed by an AI translation.
    value = str(value or '').split('\n译文：', 1)[0]
    value = html.unescape(value).casefold()
    normalized = re.sub(r'[\W_]+', '', value, flags=re.UNICODE)
    numbers = re.findall(r'\d+(?:[.\-]\d+)*', value)
    # Do not merge v1.1 with v11, or different dated releases, when punctuation
    # normalisation removes the separators from an otherwise identical title.
    return normalized + ('|' + ','.join(numbers) if numbers else '')


def content_key(value):
    text = html.unescape(re.sub(r'<[^>]+>', ' ', str(value or '')))
    text = re.sub(r'\s+', '', text).casefold()
    # Avoid treating short boilerplate (e.g. "click to read") as an article.
    return hashlib.sha256(text.encode()).hexdigest() if len(text) >= 120 else ''


def cache_policy(config):
    data = getattr(config, 'data', config)
    settings = data.get('cache', {}) if isinstance(data, dict) else {}
    settings = settings if isinstance(settings, dict) else {}
    result = {**CACHE_DEFAULTS, **settings}
    if type(result['auto_clear']) is not bool:
        raise ValueError('缓存自动清理开关必须是布尔值')
    if type(result['interval_days']) is not int or not 1 <= result['interval_days'] <= 36500:
        raise ValueError('缓存清理周期必须是 1～36500 天')
    if not isinstance(result['clear_time'], str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', result['clear_time']):
        raise ValueError('缓存清理时间必须为 HH:MM')
    return result


def next_cleanup(last_reset, policy):
    hour, minute = map(int, policy['clear_time'].split(':'))
    return (datetime.fromtimestamp(last_reset).replace(hour=hour, minute=minute, second=0, microsecond=0)
            + timedelta(days=policy['interval_days'])).timestamp()


class CacheMixin:
    def _init_cache(self):
        self.conn.execute('''CREATE TABLE IF NOT EXISTS cache_state (
            id INTEGER PRIMARY KEY CHECK(id=1), generation INTEGER NOT NULL,
            initialized_at REAL NOT NULL,last_cleared_at REAL NOT NULL,
            last_reason TEXT NOT NULL)''')
        now = time.time()
        self.conn.execute('INSERT OR IGNORE INTO cache_state VALUES (1,0,?,?,?)', (now, now, '初始化'))
        columns = {row[1] for row in self.conn.execute('PRAGMA table_info(seen)')}
        for name in ('original_title', 'url_key', 'title_key', 'title_prefix', 'content_key', 'description', 'image'):
            if name not in columns:
                self.conn.execute(f"ALTER TABLE seen ADD COLUMN {name} TEXT DEFAULT ''")
        for index, names in [('url_key', 'url_key'), ('title_key', 'title_key'),
                             ('content_key', 'content_key'), ('title_prefix', 'title_prefix')]:
            self.conn.execute(f'CREATE INDEX IF NOT EXISTS seen_{index}_idx ON seen({names})')
        # Migration preserves all previous scores and delivery records.
        for row in self.conn.execute("SELECT url_hash,url,title FROM seen WHERE url_key='' OR url_key IS NULL").fetchall():
            key = title_key(row[2])
            self.conn.execute('UPDATE seen SET original_title=?,url_key=?,title_key=?,title_prefix=? WHERE url_hash=?',
                              (str(row[2] or '').split('\n译文：', 1)[0], canonical_url(row[1]), key, key[:8], row[0]))
        self.conn.commit()

    def cache_generation(self):
        return self.conn.execute('SELECT generation FROM cache_state WHERE id=1').fetchone()[0]

    def _cache_match(self, url, title='', description=''):
        urlkey, titlekey, contentkey = canonical_url(url), title_key(title), content_key(description)
        for column, key in [('url_key', urlkey), ('title_key', titlekey), ('content_key', contentkey)]:
            if key:
                found = self.conn.execute(f'SELECT url_hash FROM seen WHERE {column}=? LIMIT 1', (key,)).fetchone()
                if found:
                    return found[0]
        if len(titlekey) >= 12:
            from .dedupe import _same_event_title
            candidates = self.conn.execute('SELECT url_hash,original_title FROM seen WHERE title_prefix=?', (titlekey[:8],))
            for key, stored in candidates:
                if _same_event_title(title, stored):
                    return key
        return None

    def cache_contains(self, url, expected_generation=None):
        if expected_generation is not None and self.cache_generation() != expected_generation:
            return False
        return self.conn.execute('SELECT 1 FROM seen WHERE url_key=?', (canonical_url(url),)).fetchone() is not None

    def cache_page(self, search='', page=0, page_size=100):
        page_size = max(1, min(int(page_size), 500))
        with self.conn:
            self.conn.execute('BEGIN')
            clause, args = self._cache_filter(search)
            count, cutoff = self.conn.execute('SELECT COUNT(*),COALESCE(MAX(rowid),0) FROM seen' + clause, args).fetchone()
            page = min(max(0, int(page)), max(0, (count - 1) // page_size))
            fields = 'url_hash,title,url,source,score,utility,credibility,valuable,pushed_realtime,in_daily,published_at,created_at,summary,comment,description'
            cursor = self.conn.execute('SELECT ' + fields + ' FROM seen' + clause + ' ORDER BY rowid DESC LIMIT ? OFFSET ?',
                                       args + [page_size, page * page_size])
            names = [col[0] for col in cursor.description]
            rows = [dict(zip(names, row)) for row in cursor.fetchall()]
            generation = self.cache_generation()
        return {'rows': rows, 'count': count, 'page': page, 'page_size': page_size,
                'cutoff': cutoff, 'generation': generation, 'total': self.conn.execute('SELECT COUNT(*) FROM seen').fetchone()[0]}

    @staticmethod
    def _cache_filter(search):
        if not search.strip():
            return '', []
        text = '%' + search.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        return " WHERE (title LIKE ? ESCAPE '\\' OR source LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\')", [text] * 3

    def delete_cached(self, keys=None, *, all_matching=False, search='', cutoff=None, expected_generation=None, now=None):
        now = time.time() if now is None else now
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            if expected_generation is not None and expected_generation != self.cache_generation():
                raise ValueError('缓存已被其他窗口清理或删除，请刷新列表后重试。')
            if all_matching:
                clause, args = self._cache_filter(search)
                if cutoff is not None:
                    clause += (' AND ' if clause else ' WHERE ') + 'rowid<=?'
                    args.append(cutoff)
                target = 'SELECT url_hash FROM seen' + clause
                self.conn.execute('DELETE FROM realtime_deliveries WHERE url_hash IN (' + target + ')', args)
                removed = self.conn.execute('DELETE FROM seen' + clause, args).rowcount
            else:
                keys = list(dict.fromkeys(keys or []))
                removed = 0
                for offset in range(0, len(keys), 400):
                    chunk = keys[offset:offset + 400]
                    placeholders = ','.join('?' for _ in chunk)
                    self.conn.execute('DELETE FROM realtime_deliveries WHERE url_hash IN (' + placeholders + ')', chunk)
                    removed += self.conn.execute('DELETE FROM seen WHERE url_hash IN (' + placeholders + ')', chunk).rowcount
            if removed or all_matching and not search and cutoff is None:
                self.conn.execute('UPDATE cache_state SET generation=generation+1 WHERE id=1')
            # A manual whole-library reset restarts its automatic cleanup cycle.
            if all_matching and not search and cutoff is None:
                self.conn.execute("UPDATE cache_state SET last_cleared_at=?,last_reason='手动清空' WHERE id=1", (now,))
        return removed

    def cache_maintenance(self, config, now=None):
        policy = cache_policy(config)
        now = time.time() if now is None else now
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            state = self.conn.execute('SELECT generation,initialized_at,last_cleared_at,last_reason FROM cache_state WHERE id=1').fetchone()
            due = next_cleanup(state[2], policy)
            removed = 0
            if policy['auto_clear'] and now >= due:
                removed = self.conn.execute('SELECT COUNT(*) FROM seen').fetchone()[0]
                self.conn.execute('DELETE FROM realtime_deliveries')
                self.conn.execute('DELETE FROM seen')
                self.conn.execute("UPDATE cache_state SET generation=generation+1,last_cleared_at=?,last_reason='定时清空' WHERE id=1", (now,))
                state = (state[0] + 1, state[1], now, '定时清空')
                due = next_cleanup(now, policy)
        return {'removed': removed, 'generation': state[0], 'last_cleared_at': state[2],
                'last_reason': state[3], 'next_cleanup_at': due if policy['auto_clear'] else None, 'policy': policy}


class CacheLibrary:
    """Short-lived connections keep Qt worker threads and the backend isolated."""
    def __init__(self, path=None):
        self.path = path

    def run(self, method, *args, **kwargs):
        from .dedupe import Dedupe
        with Dedupe(self.path) as db:
            return getattr(db, method)(*args, **kwargs)

    def snapshot(self, config, search='', page=0):
        from .dedupe import Dedupe
        with Dedupe(self.path) as db:
            maintenance = db.cache_maintenance(config)
            return {**db.cache_page(search, page), 'maintenance': maintenance, 'search': search}
