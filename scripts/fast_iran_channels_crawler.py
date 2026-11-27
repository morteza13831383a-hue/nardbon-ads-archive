"""Checkpointed listing-first collector. Network access uses the shared HTML client."""
import argparse
import filelock as fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from xml.etree.ElementTree import ParseError

import channels_html as channels
from channel_document import parse
from html_core import Client
from source_budget import Challenge, HostBlocked, status


def prepare(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS channel_outgoing(username TEXT PRIMARY KEY COLLATE NOCASE);
        CREATE TRIGGER IF NOT EXISTS channel_outgoing_insert AFTER INSERT ON channels
        BEGIN INSERT OR IGNORE INTO channel_outgoing VALUES (new.username); END;
        CREATE TRIGGER IF NOT EXISTS channel_outgoing_update AFTER UPDATE OF payload ON channels
        BEGIN INSERT OR IGNORE INTO channel_outgoing VALUES (new.username); END;
    """)
    with db:
        if not db.execute("SELECT 1 FROM meta WHERE key='incremental_export_v1'").fetchone():
            db.execute("INSERT OR IGNORE INTO channel_outgoing SELECT username FROM channels")
            db.execute("INSERT INTO meta VALUES ('incremental_export_v1','1')")
        release(db)


def release(db):
    # Caller owns channels_html.py's worker.lock, including after a crash.
    db.execute("UPDATE queue SET status='pending' WHERE status='in_progress'")


def claim(db, size, critical_profiles=False, listings_only=False):
    db.execute('BEGIN IMMEDIATE')
    with db:
        row = db.execute("SELECT value FROM meta WHERE key='rotation_turn'").fetchone()
        turn = int(row[0]) if row else 0
        jobs = []
        for _ in range(size):
            job = channels.next_job(db, turn, critical_profiles)
            if not job:
                break
            if listings_only and job[1] == 'channel':
                listing_job = db.execute(
                    "SELECT url,kind FROM queue WHERE status='pending' AND kind!='channel' ORDER BY priority, url LIMIT 1"
                ).fetchone()
                if not listing_job:
                    break
                job = listing_job
            db.execute("UPDATE queue SET status='in_progress' WHERE url=?", (job[0],))
            jobs.append(job)
            turn = (turn + 1) % len(channels.ROTATION)
        db.execute("INSERT OR REPLACE INTO meta VALUES('rotation_turn',?)", (str(turn),))
    return jobs


def flush(db, directory, size=500):
    directory.mkdir(parents=True, exist_ok=True)
    while True:
        db.execute('BEGIN IMMEDIATE')
        with db:
            rows = db.execute("""SELECT username,payload FROM channel_outgoing
                JOIN channels USING(username) ORDER BY username LIMIT ?""", (size,)).fetchall()
            if not rows:
                return
            data = ('\n'.join(payload for _, payload in rows) + '\n').encode()
            name = 'channels-batch-' + hashlib.sha256(data).hexdigest() + '.jsonl.gz'
            target = directory / name
            tmp = target.with_suffix('.tmp')
            with tmp.open('wb') as output:
                output.write(gzip.compress(data, compresslevel=3, mtime=0))
                output.flush()
                os.fsync(output.fileno())
            tmp.replace(target)
            if hasattr(os, 'O_DIRECTORY'):
                directory_fd = os.open(directory, os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            db.executemany('DELETE FROM channel_outgoing WHERE username=?', [(u,) for u,_ in rows])


def seed_files(db, paths, advertiser_file, keywords, catalog):
    for path in paths:
        for line in path.read_text().splitlines():
            item = line.split('#',1)[0].strip()
            if not item:
                continue
            if re.fullmatch(r'@?[a-zA-Z0-9_]{4,64}', item):
                item = '/channels/' + item.lstrip('@')
            channels.enqueue(db, item)
    if advertiser_file:
        for line in advertiser_file.read_text().splitlines():
            slug = line.split('#',1)[0].strip()
            if slug.startswith(channels.BASE + '/advertisers/'):
                slug = urlsplit(slug).path.split('/')[2]
            elif slug.startswith('@'):
                slug = 'tg-' + slug[1:].lower()
            if re.fullmatch('[a-z0-9_-]{3,80}', slug):
                channels.enqueue(db, f'/advertisers/{slug}/channels', 'listing')
    if keywords:
        terms = {s.split('#',1)[0].strip().casefold() for p in keywords for s in p.read_text().splitlines()}
        terms.discard('')
        index = json.loads(catalog.read_bytes())
        fields = index['fields']
        for row in index['rows']:
            info = dict(zip(fields,row))
            text = (str(info.get('t') or '') + ' ' + str(info.get('d') or '')).casefold()
            if any(term in text for term in terms) and info.get('u'):
                channels.enqueue(db, '/channels/' + info['u'], priority=channels.P_PERSIAN_LINK)
    db.commit()


def held(db, archive):
    row = db.execute("SELECT value FROM meta WHERE key IN ('fast_hold','stop_reason') AND value!='' LIMIT 1").fetchone()
    if row or status(channels.BASE)['halted']:
        return True
    with sqlite3.connect(archive.resolve().as_uri()+'?mode=ro', uri=True) as other:
        row = other.execute("SELECT value FROM meta WHERE key='last_stop_reason'").fetchone()
    return bool(row and re.search(r'challenge|captcha|blocked|halt|403|429|account',row[0],re.I))


def collect(args):
    work = args.work_dir
    work.mkdir(parents=True, exist_ok=True)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True
        # Interrupt long budget sleeps as well as reads. Transactions roll back on unwind.
        raise KeyboardInterrupt

    with (work/'worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Channel checkpoint already has an owner', file=sys.stderr)
            return 75
        db = channels.connect(work)
        transport = None
        try:
            prepare(db)
            flush(db, args.outbox)
            if held(db, args.ads_checkpoint):
                print("Existing source hold; no requests sent", file=sys.stderr)
                return 75
            channels.seed(db, args.ads_checkpoint)
            seed_files(db, args.seed, args.advertisers, args.keywords, args.catalog)
            client = Client(channels.BASE, delay=getattr(args,"min_interval",1.0), resilient=True)
            if not db.execute("SELECT 1 FROM queue WHERE status='pending' LIMIT 1").fetchone():
                return 0
            if args.transport == 'curl':
                from channel_transport import CurlOpener
                transport = CurlOpener()
                client.opener = transport
            for sig in (signal.SIGTERM,signal.SIGINT):
                signal.signal(sig,stop)
            while not stopping:
                try:
                    client.initialize()
                    break
                except HostBlocked:
                    raise
                except HTTPError as error:
                    if error.code == 401:
                        raise HostBlocked('Authentication required') from error
                    if error.code not in (403,429) and error.code < 500:
                        raise
                    # The next request must pass reserve(), including for robots.txt.
                except (TimeoutError,URLError):
                    continue
            while not stopping:
                if held(db,args.ads_checkpoint):
                    return 75
                jobs = claim(db,args.batch,critical_profiles=True,listings_only=getattr(args,'listings_only',False))
                if not jobs:
                    break
                for url,kind in jobs:
                    state = db.execute('SELECT status FROM queue WHERE url=?',(url,)).fetchone()
                    if state[0] != 'in_progress':
                        continue
                    if not client.robots.allowed(url):
                        with db:
                            db.execute("UPDATE queue SET status='skipped', error='robots' WHERE url=?", (url,))
                        continue
                    try:
                        body = client.get(url)
                        with db:
                            channels.consume(db,url,kind,body,document=parse)
                        if kind != "channel":
                            flush(db,args.outbox)
                    except HostBlocked:
                        raise
                    except HTTPError as error:
                        if error.code in (403,429) or error.code >= 500:
                            # html_core has already reported this response to the shared budget.
                            break
                        if error.code == 401:
                            raise HostBlocked('Authentication required') from error
                        with db:
                            db.execute("UPDATE queue SET status='failed',error=? WHERE url=?", (str(error.code),url))
                    except (ValueError,ParseError) as error:
                        with db:
                            db.execute("UPDATE queue SET status='failed',error=? WHERE url=?", (str(error),url))
                    except (TimeoutError,URLError):
                        break
                with db:
                    release(db)
                flush(db,args.outbox)
        except (Challenge,HostBlocked) as error:
            with db:
                db.execute("INSERT OR REPLACE INTO meta VALUES('fast_hold',?)",(type(error).__name__,))
            return 75
        except KeyboardInterrupt:
            pass
        finally:
            if transport is not None:
                transport.close()
            db.rollback()
            with db:
                release(db)
            flush(db,args.outbox)
            db.close()
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--ads-checkpoint', type=Path, required=True)
    parser.add_argument('--outbox', type=Path, required=True)
    parser.add_argument('--seed',type=Path,action='append',default=[])
    parser.add_argument('--advertisers',type=Path)
    parser.add_argument('--keywords',type=Path,nargs='+',default=[])
    parser.add_argument('--catalog',type=Path)
    parser.add_argument('--min-interval',type=float,default=os.getenv('CHANNEL_MIN_INTERVAL','1.0'))
    parser.add_argument('--transport',choices=('curl','stdlib'),default='curl')
    parser.add_argument('--batch',type=int,default=16)
    parser.add_argument('--listings-only',action='store_true',help='Skip single-channel profile GETs; dedicate budget to listings and advertiser pages')
    args = parser.parse_args()
    if args.batch <= 0 or (args.keywords and not args.catalog):
        parser.error('Use a positive batch size and supply --catalog with --keywords')
    if not 1 <= args.min_interval < float('inf'):
        parser.error('interval must be finite and at least one second')
    os.environ['CHANNEL_MIN_INTERVAL']=str(args.min_interval)
    sys.exit(collect(args))
