"""Checkpointed public Telegram preview validation, sharing the channel batch outbox."""
import argparse
from contextlib import closing
import fcntl
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urljoin

import requests
from selectolax.lexbor import LexborHTMLParser

import channels_html as channels
from channel_import import region
from fast_iran_channels_crawler import flush, prepare
from html_core import Robots, UA
from source_budget import is_challenge, report, reserve, retry_after_seconds
from tme_crawler import ARABIC_SCRIPT, USERNAME, is_persian, number

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
        else:
            rows[u] = (0 if not c.get('kd') else 2, int(c.get('m') or 0), 1)
    with closing(sqlite3.connect(source_db.resolve().as_uri()+'?mode=ro', uri=True)) as src:
        for url, in src.execute("""SELECT url FROM queue JOIN discovery_jobs USING(url)
                WHERE kind='channel' AND status IN ('pending','challenge_deferred')
                AND source IN ('keyword','sitemap','placement','advertiser')"""):
            u = url.rsplit('/',1)[-1].lower()
            if u not in excluded:
                rows.setdefault(u,(1,0,0))
    with db:
        db.executemany("UPDATE jobs SET status='already_known' WHERE username=? AND status='pending'",
                       ((u,) for u in excluded))
        db.executemany("""INSERT INTO jobs(username,priority,members,existing) VALUES(?,?,?,?)
                          ON CONFLICT(username) DO UPDATE SET existing=excluded.existing,
                          priority=excluded.priority,members=excluded.members""",
                       ((u,*v) for u,v in rows.items() if USERNAME.fullmatch(u)))


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


def fetch(session, db, url, delay):
    reserve(BASE, max(4, delay))
    try:
        with session.get(url,allow_redirects=False,timeout=(10,25)) as response:
            code, body = response.status_code, response.content.decode('utf-8','replace')
            challenge = is_challenge(body) if code in (200,403) else False
            report(BASE,'challenge' if challenge else code,retry_after_seconds(response.headers))
            outcome = 'challenge' if challenge else str(code)
            location = response.headers.get('Location','')
    except requests.RequestException as exc:
        report(BASE,0)
        outcome = 'timeout' if isinstance(exc,requests.Timeout) else 'network_error'
        body, location = '', ''
    with db:
        db.execute('INSERT INTO events VALUES(?,?,?)',(time.time(),url,outcome))
    return outcome,body,location


def run(args):
    args.work.mkdir(parents=True,exist_ok=True)
    with (args.work/'preview.lock').open('a') as lock, closing(channels.connect(args.work)) as db, requests.Session() as session:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        prepare(db)
        db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs(username TEXT PRIMARY KEY,priority INTEGER,members INTEGER,existing INTEGER,
            status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0, ready REAL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS preview_pending ON jobs(priority,members DESC,username) WHERE status='pending';
        CREATE TABLE IF NOT EXISTS events(at REAL,url TEXT,outcome TEXT);
        """)
        seed(db,args.catalog,args.source_db)
        session.headers['User-Agent'] = UA
        rules = None
        last_flush = 0
        while not (args.work/'pause').exists():
            if db.execute("SELECT 1 FROM meta WHERE key='preview_hold'").fetchone():
                break
            job = db.execute("""SELECT username,existing,attempts FROM jobs WHERE status='pending' AND ready<=?
                                ORDER BY priority,members DESC,username LIMIT 1""",(time.time(),)).fetchone()
            if not job:
                if not db.execute("SELECT 1 FROM jobs WHERE status='pending' LIMIT 1").fetchone():
                    break
                time.sleep(10)
                continue
            u,existing,attempts = job
            url = f'{BASE}/s/{u}'
            if rules is None:
                code,body,_ = fetch(session,db,BASE+'/robots.txt',4)
                if code in ('404','410','200'):
                    rules = Robots(body if code=='200' else '')
                    continue
                if code in ('429','timeout','network_error') or code.startswith('5'):
                    continue
                with db: db.execute("INSERT INTO meta VALUES('preview_hold',?)",('robots '+code,))
                break
            result, row = 'robots_skipped', None
            if rules.allowed(url):
                code,body,location = fetch(session,db,url,rules.delay)
                if code in ('301','302','303','307','308') and urljoin(url,location).lower()==f'{BASE}/{u}' and rules.allowed(f'{BASE}/{u}'):
                    code,body,location = fetch(session,db,f'{BASE}/{u}',rules.delay)
                if code in ('challenge','401','403'):
                    with db: db.execute("INSERT INTO meta VALUES('preview_hold',?)",(code,))
                    break
                if code=='429' or code.startswith('5') or code in ('timeout','network_error'):
                    with db:
                        db.execute('UPDATE jobs SET attempts=attempts+1,ready=?,status=? WHERE username=?',
                                   (time.time()+min(3600,120*2**min(attempts,5)),
                                    'pending' if attempts<4 else 'retry_exhausted',u))
                    continue
                if code=='200':
                    result,row = inspect_page(body,u,existing)
                else:
                    result = 'unavailable' if code in ('404','410') else 'http_'+code
            with db:
                db.execute('UPDATE jobs SET status=?,attempts=attempts+1 WHERE username=?',(result,u))
                if row:
                    db.execute('INSERT OR REPLACE INTO channels VALUES(?,?)',(u,json.dumps(row,ensure_ascii=False)))
            if time.monotonic()-last_flush>=60:
                flush(db,args.outbox)
                last_flush=time.monotonic()
        flush(db,args.outbox)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('work','outbox','source-db','catalog'):
        parser.add_argument('--'+name,required=True,type=Path)
    run(parser.parse_args())
