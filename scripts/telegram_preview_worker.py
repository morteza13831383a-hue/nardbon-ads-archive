"""Checkpointed public Telegram preview validation, sharing the channel batch outbox."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import filelock as fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from threading import Event, local
from urllib.parse import urljoin

import requests
from selectolax.lexbor import LexborHTMLParser

import channels_html as channels
from channel_import import region
from fast_iran_channels_crawler import flush, prepare
from html_core import Robots, UA
from source_budget import is_challenge, report, reserve, retry_after_seconds, status as budget_status
from tme_crawler import ARABIC_SCRIPT, LINKS, MENTIONS, NOT_CHANNEL, USERNAME, is_persian, number

BASE = 'https://t.me'


def seed(db, catalog_file, source_db):
    catalog = json.loads(catalog_file.read_bytes())
    rows = {}
    excluded = set()
    for values in catalog['rows']:
        c = dict(zip(catalog['fields'], values))
        u = (c.get('u') or '').lower()
        if c.get('kd') == 'group' or (c.get('kd') == 'channel' and all(c.get(k) for k in ('t','m','d'))):
            excluded.add(u)
            continue
        evidence = c.get('lg') == 'fa' or c.get('co') == 'IR' or c.get('rg') == 'ir'
        priority = 2 if c.get('kd') == 'channel' else 0 if evidence else 1
        candidate = (priority, int(c.get('m') or 0), 1)
        old = rows.get(u)
        if old is None or priority == 2 or (old[0] != 2 and (priority,-candidate[1]) < (old[0],-old[1])):
            rows[u] = candidate

    with closing(sqlite3.connect(source_db.resolve().as_uri()+'?mode=ro', uri=True)) as src:
        for url, source in src.execute("""SELECT url, source FROM queue JOIN discovery_jobs USING(url)
                WHERE kind='channel' AND status IN ('pending','challenge_deferred')
                AND source IN ('keyword','sitemap','placement','advertiser')"""):
            u = url.rsplit('/',1)[-1].lower()
            if u not in excluded:
                rows.setdefault(u,(0 if source == "placement" else 1,0,0))
    with db:
        db.executemany("UPDATE jobs SET status='already_known' WHERE username=? AND status='pending'",
                       ((u,) for u in excluded))
        db.executemany("""INSERT INTO jobs(username,priority,members,existing) VALUES(?,?,?,?)
                          ON CONFLICT(username) DO UPDATE SET existing=excluded.existing,
                          priority=excluded.priority,members=excluded.members""",
                       ((u,*v) for u,v in rows.items() if u not in excluded and USERNAME.fullmatch(u)))


def reconcile_listings(db, source_db):
    """Use previously parsed channel rows for type, retaining incomplete rows for enrichment."""
    with closing(sqlite3.connect(source_db.resolve().as_uri()+'?mode=ro', uri=True)) as src:
        rows = src.execute("""SELECT lower(username), payload FROM channels
            WHERE json_extract(payload,'$.record_detail') IN ('listing','profile')
            AND json_extract(payload,'$.classification') IN ('persian_candidate','iran_confirmed')
            AND lower(json_extract(payload,'$.username'))=lower(username)""")
        def updates():
            for u, payload in rows:
                row = json.loads(payload)
                yield all(row.get(k) for k in ('title','members','description')), u

        with db:
            db.executemany("""UPDATE jobs SET listing_verified=1, priority=2,
                status=CASE WHEN ? THEN 'already_known' ELSE status END
                WHERE username=? AND status='pending'""", updates())


def inspect_page(body, u, existing):
    doc = LexborHTMLParser(body)
    header = doc.css_first('.tgme_channel_info')
    def txt(node):
        return node.text(separator=' ',strip=True) if node else ''
    if header is None:
        extra = txt(doc.css_first('.tgme_page_extra')).lower()
        if re.search(r'\bmembers\b', extra):
            return 'group', None
        return 'no_public_preview', None
    title = txt(header.css_first('.tgme_channel_info_header_title'))
    handle = txt(header.css_first('.tgme_channel_info_header_username')).lstrip('@').lower()
    posts = [n.attributes.get('data-post','').split('/')[0].lower() for n in doc.css('.tgme_widget_message[data-post]')]
    if not title or (handle and handle != u) or (not handle and u not in posts):
        return 'identity_unverified', None
    metrics = {txt(n.css_first('.counter_type')).lower():txt(n.css_first('.counter_value'))
               for n in header.css('.tgme_channel_info_counter')}
    if 'members' in metrics or 'participants' in metrics:
        return 'group', None
    if 'subscribers' not in metrics and u not in posts:
        return 'type_unverified', None
    bio = txt(header.css_first('.tgme_channel_info_description'))
    text = ' '.join([title,bio]+[txt(n) for n in doc.css('.tgme_widget_message_text')])[:6000]
    fa, geo = bool(is_persian(text)), region(text)
    # Preserve the catalog's scope. Existing entries can receive verified type without a language guess.
    if not (fa or geo=='ir' or existing):
        return 'channel_outside_scope', None
    row = dict(username=u,title=title,description=bio,members=number(metrics.get('subscribers')),
               record_detail='profile',source='tme',source_url=f'{BASE}/s/{u}',collected_at=channels.now(),
               has_persian_script=bool(ARABIC_SCRIPT.search(text)),region_signal=geo,
               language='fa' if fa else None,classification='persian_candidate' if fa else
               'iran_candidate' if geo=='ir' else 'catalog_verified')
    return 'channel', row


def extract_links(body, self_username):
    """Extract candidate channel usernames from t.me links and @mentions for snowball discovery."""
    found = set()
    for candidate in LINKS.findall(body) + MENTIONS.findall(body):
        u = candidate.lower()
        if u == self_username or u in NOT_CHANNEL or u.endswith('bot'):
            continue
        if USERNAME.fullmatch(u):
            found.add(u)
    return sorted(found)


class PreviewClient:
    def __init__(self, maximum, pause):
        self.adapter = requests.adapters.HTTPAdapter(pool_maxsize=maximum, pool_block=True)
        self.sessions = local()
        self.blocked = Event()
        self.pause = pause

    def fetch(self, url, delay, events):
        tme_delay = float(os.environ['TME_MIN_INTERVAL']) if 'TME_MIN_INTERVAL' in os.environ else max(2.5, delay)
        if not reserve(BASE, tme_delay, cancelled=lambda: self.blocked.is_set() or self.pause.exists()):
            return 'cancelled', '', ''
        if not hasattr(self.sessions, 'session'):
            session = requests.Session()
            session.mount(BASE+'/', self.adapter)
            session.headers['User-Agent'] = UA
            self.sessions.session = session
        try:
            with self.sessions.session.get(url,allow_redirects=False,timeout=(10,25)) as response:
                code, body = response.status_code, response.content.decode("utf-8", "replace")
                challenge = response.headers.get('cf-mitigated','').lower() == 'challenge' or (
                    code in (200,403) and is_challenge(body))
                outcome = 'challenge' if challenge else str(code)
                if outcome in ('challenge','401','403','429') or code >= 500:
                    self.blocked.set()
                report(BASE, 'challenge' if challenge else code, retry_after_seconds(response.headers))
                location = response.headers.get('Location','')
        except requests.RequestException as exc:
            self.blocked.set()
            report(BASE,0)
            outcome = 'timeout' if isinstance(exc,requests.Timeout) else 'network_error'
            body, location = '', ''
        events.append((time.time(),url,outcome))
        return outcome,body,location

    def check(self, job, rules):
        username, existing, _ = job
        url = f'{BASE}/s/{username}'
        events = []
        if not rules.allowed(url):
            return 'robots_skipped', None, events
        code, body, location = self.fetch(url,rules.delay,events)
        fallback = f'{BASE}/{username}'
        if code in ('301','302','303','307','308') and urljoin(url,location).lower() == fallback and rules.allowed(fallback):
            code, body, _ = self.fetch(fallback,rules.delay,events)
        discovered = []
        if code == '200':
            result,row = inspect_page(body,username,existing)
            if result == 'channel' and row and (row.get('language') == 'fa' or row.get('region_signal') == 'ir'):
                discovered = extract_links(body, username)
        else:
            result,row = code,None
        return result,row,events,discovered


class Concurrency:
    def __init__(self, maximum):
        self.maximum = maximum
        self.limit = min(3, maximum)
        self.clean = 0
        self.errors = 0

    def observe(self, events):
        codes = [event[2] for event in events]
        if any(code in ('403','429','challenge','401') for code in codes):
            self.limit = 1
            self.errors = self.clean = 0
            return
        failures = sum(code in ('timeout','network_error') or code.startswith('5') for code in codes)
        if failures:
            self.errors += failures
            self.clean = 0
            if self.errors >= 2:
                self.limit = max(1, self.limit // 2)
                self.errors = 0
        else:
            self.clean += len(codes)
            if self.clean >= 400:
                self.limit = min(self.maximum,self.limit+1)
                self.errors = self.clean = 0


def save_batch(db, jobs, results):
    events = [event for item in results for event in item[2]]
    now = time.time()
    with db:
        db.executemany('INSERT INTO events VALUES(?,?,?)',events)
        hold = next((event[2] for event in events if event[2] in ('challenge','401','403')),None)
        if hold:
            db.execute("INSERT OR REPLACE INTO meta VALUES('preview_hold',?)",(hold,))
        updates, records, discovered_rows = [], [], []
        for (username,_,attempts), item in zip(jobs,results):
            result, row = item[0], item[1]
            discovered = item[3] if len(item) > 3 else []
            if result in ('cancelled','challenge','401','403'):
                continue
            ready = 0
            if result in ('429','timeout','network_error') or result.startswith('5'):
                result = 'pending' if attempts < 4 else 'retry_exhausted'
                ready = now + min(3600,120*2**min(attempts,5))
            elif result.isdecimal():
                result = 'unavailable' if result in ('404','410') else 'http_'+result
            updates.append((result,ready,username))
            if row:
                records.append((username,json.dumps(row,ensure_ascii=False)))
            for target in discovered:
                discovered_rows.append((target,))
        db.executemany('UPDATE jobs SET status=?,ready=?,attempts=attempts+1 WHERE username=?',updates)
        db.executemany('INSERT OR REPLACE INTO channels VALUES(?,?)',records)
        if discovered_rows:
            db.executemany('INSERT OR IGNORE INTO jobs(username,priority,members,existing) VALUES(?,1,0,0)',discovered_rows)
    return events


def run(args):
    args.work.mkdir(parents=True,exist_ok=True)
    with (args.work/'preview.lock').open('a') as lock, closing(channels.connect(args.work)) as db:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        prepare(db)
        db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs(username TEXT PRIMARY KEY,priority INTEGER,members INTEGER,existing INTEGER,
            status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0, ready REAL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS preview_pending ON jobs(priority,members DESC,username) WHERE status='pending';
        CREATE TABLE IF NOT EXISTS events(at REAL,url TEXT,outcome TEXT);
        """)
        if 'listing_verified' not in {c[1] for c in db.execute('PRAGMA table_info(jobs)')}:
            db.execute('ALTER TABLE jobs ADD COLUMN listing_verified INTEGER DEFAULT 0')
        if getattr(args, 'min_interval', None) is not None:
            os.environ['TME_MIN_INTERVAL'] = str(args.min_interval)
        if getattr(args, 'seed', None) and args.seed.exists():
            seed_rows = []
            for raw in args.seed.read_text(encoding='utf-8').splitlines():
                u = raw.split('#', 1)[0].strip().lstrip('@').lower()
                if USERNAME.fullmatch(u):
                    seed_rows.append((u,))
            with db:
                db.executemany('INSERT OR IGNORE INTO jobs(username,priority,members,existing) VALUES(?,0,0,0)', seed_rows)
        client = PreviewClient(args.concurrency,args.work/'pause')
        concurrency = Concurrency(args.concurrency)
        rules = None
        catalog_mtime = None
        next_reconcile = last_flush = 0
        batches_done = 0
        try:
            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                while not client.pause.exists():
                    if getattr(args, 'max_batches', 0) and batches_done >= args.max_batches:
                        break
                    if budget_status(BASE)['halted'] or db.execute("SELECT 1 FROM meta WHERE key='preview_hold'").fetchone():
                        break
                    if time.monotonic() >= next_reconcile:
                        if args.catalog and args.catalog.exists() and args.source_db and args.source_db.exists():
                            mtime = args.catalog.stat().st_mtime_ns
                            if mtime != catalog_mtime:
                                seed(db,args.catalog,args.source_db)
                                catalog_mtime = mtime
                            reconcile_listings(db,args.source_db)
                        next_reconcile = time.monotonic()+1800
                    # Delayed classification work still takes precedence over enrichment.
                    unresolved = db.execute("SELECT 1 FROM jobs WHERE status='pending' AND priority<2 LIMIT 1").fetchone()
                    jobs = db.execute("""SELECT username,existing,attempts FROM jobs WHERE status='pending'
                        AND ready<=? AND priority<? ORDER BY priority,members DESC,username LIMIT ?""",
                        (time.time(),2 if unresolved else 3,concurrency.limit)).fetchall()
                    if not jobs:
                        if not db.execute("SELECT 1 FROM jobs WHERE status='pending' LIMIT 1").fetchone():
                            break
                        time.sleep(5)
                        continue
                    client.blocked.clear()
                    if rules is None:
                        events = []
                        code,body,_ = client.fetch(BASE+'/robots.txt',4,events)
                        save_batch(db,[],[(code,None,events)])
                        concurrency.observe(events)
                        if code in ('404','410','200'):
                            rules = Robots(body if code=='200' else '')
                        elif code not in ('cancelled','429','timeout','network_error') and not code.startswith('5'):
                            break
                        continue
                    results = list(pool.map(lambda job: client.check(job,rules),jobs))
                    events = save_batch(db,jobs,results)
                    batches_done += 1
                    concurrency.observe(events)
                    if time.monotonic()-last_flush>=60:
                        flush(db,args.outbox)
                        last_flush=time.monotonic()
        finally:
            client.adapter.close()
            flush(db,args.outbox)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work',required=True,type=Path)
    parser.add_argument('--outbox',required=True,type=Path)
    parser.add_argument('--source-db',type=Path,default=None)
    parser.add_argument('--catalog',type=Path,default=None)
    parser.add_argument('--seed',type=Path,default=None)
    parser.add_argument('--min-interval',type=float,default=None)
    parser.add_argument('--max-batches',type=int,default=0)
    parser.add_argument('--concurrency',type=int,choices=range(1,17),default=5)
    run(parser.parse_args())
