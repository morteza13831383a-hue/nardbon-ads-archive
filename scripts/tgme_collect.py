"""Collect public ranked channel cards from TG.me's Iran directory."""
import argparse
from datetime import datetime, timezone
import filelock as fcntl
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.error import HTTPError
from urllib.parse import parse_qs, urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser
from html_core import Client
from source_budget import Challenge
from tme_crawler import USERNAME

BASE = 'https://tg.me'
START = BASE + '/top/ir'


def iran_listing(url):
    p = urlsplit(url)
    if p.scheme != 'https' or p.netloc != 'tg.me' or p.fragment:
        return False
    q = parse_qs(p.query, keep_blank_values=True)
    if 'page' in q and (len(q['page']) != 1 or not re.fullmatch('[1-9][0-9]*', q.pop('page')[0])):
        return False
    return (p.path == '/top/ir' and not q) or (
        bool(re.fullmatch('/top/(?!bots$)[a-z0-9-]+', p.path)) and q == {'geo':['ir']})


def parse(html, url):
    doc = LexborHTMLParser(html)
    heading = doc.css_first('h1')
    if not iran_listing(url) or not heading or not re.search(r'Iran|ایران|Иран',heading.text(),re.I):
        raise ValueError('Iran listing scope not confirmed')
    links = {urljoin(url,a.attributes['href']) for a in doc.css('a[href]')}
    rows = {}
    for a in doc.css('a.rl-row[href]'):
        username = a.attributes['href'].removeprefix('/').lower()
        title = a.css_first('.rl-name-txt')
        sub = a.css_first('.rl-sub')
        if not USERNAME.fullmatch(username) or not title or not sub:
            continue
        if not re.match('@'+re.escape(username)+r'(?![\w])',sub.text(strip=True),re.I):
            continue
        title_text = title.text(separator=' ',strip=True)
        if not title_text:
            continue
        metric = a.css_first('.rl-metric')
        rows[username] = dict(type='channel',username=username,title=title_text,country='IR',
                              members_display=metric.text(strip=True) if metric else None,
                              url=BASE+'/'+username,source_url=url,
                              collected_at=datetime.now(timezone.utc).isoformat())
    return rows, sorted(link for link in links if iran_listing(link))


def run(work, root):
    work.mkdir(parents=True,exist_ok=True)
    with (work/'worker.lock').open('a') as lock, sqlite3.connect(work/'tgme.sqlite3') as db:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        db.executescript("""CREATE TABLE IF NOT EXISTS pages(url TEXT PRIMARY KEY,status TEXT DEFAULT 'pending',rows INTEGER);
                        CREATE TABLE IF NOT EXISTS records(username TEXT PRIMARY KEY,payload TEXT);
                        CREATE TABLE IF NOT EXISTS holds(reason TEXT);""")
        db.execute('INSERT OR IGNORE INTO pages(url) VALUES(?)',(START,))
        db.commit()
        if db.execute('SELECT 1 FROM holds').fetchone():
            raise RuntimeError('TG.me hold in checkpoint')
        client = Client(BASE,10,resilient=True)
        try:
            if db.execute("SELECT 1 FROM pages WHERE status='pending'").fetchone():
                client.initialize()
            while job := db.execute("SELECT url FROM pages WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone():
                url = job[0]
                body = client.get(url)
                rows, links = parse(body,url)
                with db:
                    db.executemany('INSERT OR IGNORE INTO records VALUES(?,?)',
                                   [(u,json.dumps(r,ensure_ascii=False)) for u,r in rows.items()])
                    db.executemany('INSERT OR IGNORE INTO pages(url) VALUES(?)',[(v,) for v in links])
                    db.execute("UPDATE pages SET status=?,rows=? WHERE url=?",('done' if rows else 'empty',len(rows),url))
        except (Challenge,HTTPError) as exc:
            if isinstance(exc,Challenge) or exc.code in (401,403,429):
                with db:
                    db.execute('INSERT INTO holds VALUES(?)',(str(exc),))
            raise
        finally:
            rows = db.execute('SELECT payload FROM records ORDER BY username').fetchall()
            if rows:
                raw = ''.join(r+'\n' for r, in rows).encode()
                directory = root/'data/tgme-import'
                directory.mkdir(parents=True,exist_ok=True)
                path = directory/('tgme-'+hashlib.sha256(raw).hexdigest()[:20]+'.jsonl.gz')
                tmp = path.with_suffix('.tmp')
                tmp.write_bytes(gzip.compress(raw,mtime=0))
                tmp.replace(path)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    args=parser.parse_args()
    run(args.work,args.root)
