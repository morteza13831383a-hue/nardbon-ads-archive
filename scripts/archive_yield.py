"""
Raise the Iran yield of the one-off HTML archive without more requests, without the
source API and without touching blocked paths.

Four ideas, all from public pages the crawler already may read:

1. Ad ids are cuid v1: 'c' + 8 base36 chars of the creation time in ms. In our data the
   id time is close to first_seen (median 0 h). So every sitemap id can be
   dated *without fetching it*. Measured on 4,611 ads: id time - first_seen is between
   -2 h and +71.3 h (5 ads above +24 h), so the margins below are wide:
     - ids created more than 7 days after the cutoff are skipped; within 7 days they only
       go to the back of the queue;
     - ids from two or more months before Iran coverage began are skipped; the month just
       before only goes to the back;
     - the rest are ordered by the expected Iran yield of their month (point 2).
2. /countries/ir/snapshot/YYYY-MM states the exact number of Iran creatives that month and
   per niche ("16,687 Telegram ad creatives targeting Iran during September 2026", "VPN 2,331",
   ...). That is the ground truth for completeness, per month and per niche, and it tells
   which months still have the most missing Iran ads per remaining sitemap id.
3. The advertiser graph is widened through channels: every Iran ad page lists the channels it
   ran in ("Where this ad ran"). /channels/<username>/advertisers lists who bought there, so
   Iranian advertisers outside the current graph surface through Persian channels.
4. Advertiser pages show a limited list; /advertisers/<slug>/niches/<niche> (in the sitemap)
   splits one advertiser by niche, so more of its ads become reachable.

Everything is attributed: queue.via records which strategy found a URL, and the report
gives Iran ads per 100 requests for each strategy, so the order can be tuned on evidence.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

BASE = 'https://tgadsspy.com'
CUID = re.compile(r'^c[a-z0-9]{24}$')
SNAPSHOT_TOTAL = re.compile(r'([\d,]+)\s+Telegram ad creatives targeting Iran during')
CHANNEL_ADVERTISERS = re.compile(r'^/channels/([A-Za-z0-9_]{4,64})/advertisers$')
ADVERTISER_NICHE = re.compile(r'^/advertisers/([a-z0-9_-]+)/niches/([a-z-]+)$')
NICHE_PAIR = re.compile(r'([A-Z][A-Za-z&/ -]{1,30}?)\s+([\d,]+)')


def cuid_time(ad_id):
    """Creation time encoded in a cuid v1 id, or None if the id is not a cuid."""
    if not CUID.fullmatch(ad_id or ''):
        return None
    try:
        return datetime.fromtimestamp(int(ad_id[1:9], 36) / 1000, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def ensure_schema(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS coverage (month TEXT NOT NULL, niche TEXT NOT NULL, site_count INTEGER NOT NULL,
                                         PRIMARY KEY(month, niche));
    ''')
    cols = {r[1] for r in db.execute('PRAGMA table_info(queue)')}
    if 'via' not in cols:
        db.execute('ALTER TABLE queue ADD COLUMN via TEXT')
    if 'month' not in cols:
        db.execute('ALTER TABLE queue ADD COLUMN month TEXT')
    db.execute('CREATE INDEX IF NOT EXISTS queue_month ON queue(kind, status, month)')
    # label rows queued before `via` existed, from the priority their discoverer gave them,
    # so yield is not credited to the wrong strategy
    if not db.execute("SELECT 1 FROM meta WHERE key='via_backfilled_v1'").fetchone():
        for kind, prio, via in (('detail', 1, 'iran_listing'), ('detail', 2, 'same_advertiser'),
                                ('detail', 30, 'sitemap'), ('listing', 3, 'advertiser'),
                                ('listing', 5, 'advertiser'), ('listing', 4, 'listing'),
                                ('listing', 0, 'listing'), ('listing', 2, 'listing'), ('sitemap', 10, 'sitemap')):
            db.execute('UPDATE queue SET via=? WHERE via IS NULL AND kind=? AND priority=?', (via, kind, prio))
        db.execute("INSERT INTO meta VALUES('via_backfilled_v1', datetime('now'))")
    db.commit()


def seed_known(db, ads_dir='data/ads'):
    """Skip IDs already delivered by another source, including later panel imports."""
    import hashlib
    from pathlib import Path

    db.execute("CREATE TABLE IF NOT EXISTS known_ad_shards(name TEXT PRIMARY KEY, digest TEXT NOT NULL)")
    changed = 0
    for path in sorted(Path(ads_dir).glob("*.json")):
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if db.execute("SELECT 1 FROM known_ad_shards WHERE name=? AND digest=?",
                      (path.name, digest)).fetchone():
            continue
        rows = json.loads(raw)
        if not isinstance(rows, list):
            raise ValueError(f"Invalid ad shard {path.name}")
        urls = [(BASE + "/ads/" + row["id"],) for row in rows if CUID.fullmatch(row.get("id") or "")]
        with db:
            result = db.executemany(
                "INSERT INTO queue(url,kind,priority,status,via) VALUES(?, 'detail', 99, 'known', 'archive') "
                "ON CONFLICT(url) DO UPDATE SET status='known' WHERE queue.status='pending'", urls)
            changed += result.rowcount
            db.execute("INSERT OR REPLACE INTO known_ad_shards VALUES(?,?)", (path.name, digest))
    return changed


def seed_from_channel_pages(db, enqueue, html_dir=None):
    """Ads linked on Persian channel profiles the channel collector already fetched
    (data/channel-html-import): ads that ran in Persian channels are the likeliest Iran
    targets. No extra request to find them; each ad is still verified on its own page."""
    import glob
    import gzip
    import pathlib
    html_dir = html_dir or pathlib.Path(__file__).resolve().parents[1] / 'data' / 'channel-html-import'
    n = 0
    files = sorted(glob.glob(str(pathlib.Path(html_dir) / 'channels-*.jsonl.gz')))
    local_export = pathlib.Path(html_dir) / 'channels.jsonl.gz'
    if local_export.is_file():
        files.append(str(local_export))
    # Hash compressed bytes on the same open descriptor we parse: an atomic
    # exporter replacement cannot associate new contents with an old digest.
    import hashlib
    import io
    db.execute('CREATE TABLE IF NOT EXISTS channel_seed_files '
               '(path TEXT PRIMARY KEY, sha256 TEXT NOT NULL)')
    for f in files:
        key = str(pathlib.Path(f).resolve())
        with open(f, 'rb') as raw:
            digest = hashlib.sha256()
            for chunk in iter(lambda: raw.read(1024 * 1024), b''):
                digest.update(chunk)
            fingerprint = digest.hexdigest()
            if db.execute('SELECT 1 FROM channel_seed_files WHERE path=? AND sha256=?',
                          (key, fingerprint)).fetchone():
                continue
            raw.seek(0)
            db.execute('SAVEPOINT channel_seed_file')
            file_count = 0
            try:
                with gzip.GzipFile(fileobj=raw) as compressed, io.TextIOWrapper(compressed, encoding='utf-8') as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        r = json.loads(line)
                        if r.get('classification') == 'unclassified':
                            continue
                        for link in r.get('links') or []:
                            m = re.search(r'/ads/(c[a-z0-9]{24})(?:$|[/?#])', link.get('url') or '')
                            if m:
                                via = ('persian_channel_priority_profile' if
                                       r.get('ad_discovery_via') == 'persian_channel_priority_profile'
                                       else 'persian_channel_page')
                                enqueue(db, f'{BASE}/ads/{m[1]}', 'detail', 1, via=via)
                                file_count += 1
                db.execute('INSERT OR REPLACE INTO channel_seed_files VALUES(?,?)', (key, fingerprint))
                db.execute('RELEASE channel_seed_file')
                n += file_count
            except Exception:
                db.execute('ROLLBACK TO channel_seed_file')
                db.execute('RELEASE channel_seed_file')
                raise
    db.commit()
    return n


def months_between(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield f'{y:04d}-{m:02d}'
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def seed_snapshots(db, cutoff, enqueue, first_month='2026-01'):
    """Queue every Iran monthly snapshot up to the cutoff month (cheap listing pages)."""
    start = datetime.strptime(first_month + '-01', '%Y-%m-%d').replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(cutoff.replace('Z', '+00:00'))
    for month in months_between(start, end):
        enqueue(db, f'{BASE}/countries/ir/snapshot/{month}', 'listing', 0, via='snapshot')


def requeue_missing_snapshots(db):
    """Snapshot pages fetched before totals were parsed (status done, no coverage row) are
    queued once more, so coverage.by_month and month weights get real numbers (~1 page/month)."""
    cur = db.execute("""UPDATE queue SET status='pending', tries=0, error=NULL, priority=0
                        WHERE url LIKE '%/countries/ir/snapshot/____-__' AND status='done'
                        AND substr(url, -7) NOT IN (SELECT month FROM coverage WHERE niche='')""")
    db.commit()
    return cur.rowcount


def parse_snapshot(db, url, visible_text):
    """Store the site's exact Iran totals for one month: overall and per niche."""
    m = re.search(r'/countries/ir/snapshot/(\d{4}-\d{2})$', urlsplit(url).path)
    if not m:
        return None
    month = m[1]
    total = SNAPSHOT_TOTAL.search(visible_text)
    if not total:
        return None
    db.execute('INSERT OR REPLACE INTO coverage VALUES(?,?,?)', (month, '', int(total[1].replace(',', ''))))
    niches = re.search(r'Top niches\s+(.*?)\s+Top advertisers', visible_text)
    if niches:
        for name, count in NICHE_PAIR.findall(niches[1]):
            db.execute('INSERT OR REPLACE INTO coverage VALUES(?,?,?)',
                       (month, name.strip().lower(), int(count.replace(',', ''))))
    return month


def iran_start(db):
    """First month with an Iran snapshot, trusted only when the site answered 404 (status
    'gone') for the month before it; otherwise None and nothing is skipped by age."""
    row = db.execute("SELECT MIN(month) FROM coverage WHERE niche=''").fetchone()
    if not row or not row[0]:
        return None
    y, m = map(int, row[0].split('-'))
    prev = f'{y - 1:04d}-12' if m == 1 else f'{y:04d}-{m - 1:02d}'
    gone = db.execute("SELECT 1 FROM queue WHERE url=? AND status='gone'",
                      (f'{BASE}/countries/ir/snapshot/{prev}',)).fetchone()
    return row[0] if gone else None


def found_by_month(db):
    found = {}
    for (payload,) in db.execute('SELECT payload FROM records'):
        first = (json.loads(payload).get('first_seen') or '')[:7]
        found[first] = found.get(first, 0) + 1
    return found


def reprioritize(db, cutoff):
    """Date pending sitemap details by their id (once per row), drop impossible ones, and
    order months by expected Iran yield (one UPDATE per month). No network."""
    cutoff_dt = datetime.fromisoformat(cutoff.replace('Z', '+00:00'))
    start_month = iran_start(db)
    skipped = {'after_cutoff_by_id': 0, 'before_iran_by_id': 0}
    # 1) date rows not dated yet
    rows = db.execute("SELECT url FROM queue WHERE status='pending' AND kind='detail' AND month IS NULL").fetchall()
    for (url,) in rows:
        t = cuid_time(urlsplit(url).path.rsplit('/', 1)[-1])
        if t is None:
            db.execute("UPDATE queue SET month='?' WHERE url=?", (url,))
            continue
        db.execute('UPDATE queue SET month=? WHERE url=?', (t.strftime('%Y-%m'), url))
        if t > cutoff_dt + timedelta(days=7):            # measured max id-vs-first_seen gap is +71.3 h
            db.execute("UPDATE queue SET status='skipped_time' WHERE url=?", (url,)); skipped['after_cutoff_by_id'] += 1
        elif t > cutoff_dt:
            db.execute("UPDATE queue SET month='~late', priority=29 WHERE url=?", (url,))
    # 2) months before Iran coverage began (known once the first snapshot is stored)
    if start_month:
        y, m = map(int, start_month.split('-'))
        prev = f'{y - 1:04d}-12' if m == 1 else f'{y:04d}-{m - 1:02d}'
        before = db.execute("UPDATE queue SET status='skipped_time' WHERE status='pending' AND kind='detail' "
                            "AND month NOT IN ('?','~late') AND month<?", (prev,))
        skipped['before_iran_by_id'] = before.rowcount
        db.execute("UPDATE queue SET priority=29 WHERE status='pending' AND kind='detail' AND month=?", (prev,))
    # 3) rank months by missing Iran ads per remaining id
    per_month = dict(db.execute("SELECT month, COUNT(*) FROM queue WHERE status='pending' AND kind='detail' "
                                "AND month IS NOT NULL AND month NOT IN ('?','~late') GROUP BY month"))
    site = dict(db.execute("SELECT month, site_count FROM coverage WHERE niche=''"))
    found = found_by_month(db)
    yield_ = {m: max(0, site.get(m, 0) - found.get(m, 0)) / n for m, n in per_month.items() if n}
    order = sorted(yield_, key=lambda m: -yield_[m])
    if start_month:
        order = [m for m in order if m >= start_month]
    for i, m in enumerate(order):
        # only sitemap-level rows (>=20); graph-found candidates keep their better priority
        db.execute("UPDATE queue SET priority=? WHERE status='pending' AND kind='detail' AND month=? AND priority>=20",
                   (20 + min(i, 9), m))
    db.commit()
    return {'pending_dated': sum(per_month.values()), **skipped,
            'month_order': [(m, round(yield_[m], 3), per_month[m]) for m in order]}


def expand_from_record(db, record, enqueue):
    """Channels an Iran ad ran in -> who else bought there."""
    for p in (record.get('details') or {}).get('placements_shown') or []:
        path = urlsplit(p.get('url') or '').path
        m = re.fullmatch(r'/channels/([A-Za-z0-9_]{4,64})', path)
        if m:
            enqueue(db, f'{BASE}/channels/{m[1]}/advertisers', 'listing', 6, via='channel_advertisers')


PERSIAN = re.compile('[\u0600-\u06FF]')
_SWEPT = None


def api_swept_advertisers():
    """Advertisers whose whole Iran history the API sweep in sync.py already collected
    (data/state.json -> adv_sweep.done). The HTML crawler should look for NEW advertisers."""
    global _SWEPT
    if _SWEPT is None:
        try:
            import pathlib
            state = json.loads((pathlib.Path(__file__).resolve().parents[1] / 'data' / 'state.json').read_text('utf-8'))
            _SWEPT = set((state.get('adv_sweep') or {}).get('done') or {})
        except Exception:
            _SWEPT = set()
    return _SWEPT


def classify_listing(source_path, link_path, link_text=''):
    """Extra listing paths worth following, with priority and strategy label, or None.
    Depth-first: an advertiser found on a channel page is visited before more channel pages."""
    m = re.fullmatch(r'/advertisers/([a-z0-9_-]+)', link_path)
    if m and m[1] in api_swept_advertisers():
        return 40, 'advertiser_api_covered'        # history already pulled through the API
    if CHANNEL_ADVERTISERS.fullmatch(link_path):
        return 6, 'channel_advertisers'
    if ADVERTISER_NICHE.fullmatch(link_path) and source_path.startswith('/advertisers/'):
        return 5, 'advertiser_niche'
    if m and CHANNEL_ADVERTISERS.fullmatch(source_path):
        # Persian name -> very likely an Iranian advertiser: before any further channel page
        return (4, 'advertiser_via_channel') if PERSIAN.search(link_text or '') else (5, 'advertiser_via_channel')
    return None


def coverage_report(db):
    site = {}
    for month, niche, count in db.execute('SELECT month, niche, site_count FROM coverage ORDER BY month, niche'):
        site.setdefault(month, {})[niche] = count
    found = found_by_month(db)
    months = []
    for month in sorted(site):
        total = site[month].get('', 0)
        months.append({'month': month, 'site_iran_total': total, 'extracted': found.get(month, 0),
                       'missing': max(0, total - found.get(month, 0)),
                       'coverage_pct': round(100 * found.get(month, 0) / total, 1) if total else None})
    strategies = [dict(via=v or 'original', requests=n, iran=i, iran_per_100=round(100 * i / n, 2) if n else None)
                  for v, n, i in db.execute(
                      "SELECT via, COUNT(*), SUM(status='iran') FROM queue "
                      "WHERE status NOT IN ('pending','skipped_time') GROUP BY via ORDER BY 3 DESC")]
    # Queue outcomes are not HTTP attempts: retries, known rows and robots skips
    # otherwise distort performance comparisons. Keep legacy fields for readers.
    observed = []
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='source_requests'").fetchone():
        observed = [dict(via=v, requests=n, iran=i,
                         iran_per_100=round(100*i/n, 2))
                    for v,n,i in db.execute("""
                        SELECT s.via, COUNT(*),
                               COUNT(DISTINCT CASE WHEN q.status='iran' AND s.status=200 THEN s.url END)
                        FROM source_requests s LEFT JOIN queue q ON q.url=s.url
                        GROUP BY s.via ORDER BY COUNT(*) DESC
                    """)]
    site_sum = sum(m['site_iran_total'] for m in months)
    return {'by_month': months, 'site_sum_of_months': site_sum,
            'extracted_total': sum(found.values()),
            'by_strategy': strategies,
            'by_strategy_basis': 'Legacy completed queue rows, not HTTP requests',
            'observed_http_by_strategy': observed,
            'observed_http_scope': 'Only requests recorded since the request ledger was installed',
            'skipped_by_id_time': db.execute("SELECT COUNT(*) FROM queue WHERE status='skipped_time'").fetchone()[0]}


class RequestLedger:
    """Count actual requests by discovery strategy, without copying headers or credentials."""
    def __init__(self,db,opener):
        self.db=db;self.opener=opener
        db.execute('CREATE TABLE IF NOT EXISTS source_requests(id INTEGER PRIMARY KEY,url TEXT,via TEXT,status INTEGER,at TEXT)');db.commit()
    def open(self,request,**kwargs):
        from urllib.error import HTTPError
        from datetime import datetime,timezone
        row=self.db.execute('SELECT via FROM queue WHERE url=?',(request.full_url,)).fetchone()
        via=(row[0]if row else None)or'overhead'
        key=self.db.execute('INSERT INTO source_requests(url,via,status,at)VALUES(?,?,0,?)',(request.full_url,via,datetime.now(timezone.utc).isoformat())).lastrowid;self.db.commit()
        try:response=self.opener.open(request,**kwargs)
        except HTTPError as exc:
            self.db.execute('UPDATE source_requests SET status=?WHERE id=?',(exc.code,key));self.db.commit();raise
        self.db.execute('UPDATE source_requests SET status=?WHERE id=?',(response.status,key));self.db.commit();return response
