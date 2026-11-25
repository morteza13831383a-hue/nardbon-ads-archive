"""Resume the Persian listing only. Follow same-route pagination in the returned HTML."""
import argparse
from contextlib import closing
import fcntl
import hashlib
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin

import channels_html as channels
from channel_document import parse
from fast_iran_channels_crawler import prepare, flush, seed_files
from html_core import Client
from source_budget import Challenge, status

FIRST = channels.BASE + '/channels?lang=fa'
PAGE = re.compile(re.escape(FIRST) + r'(?:&page=([1-9][0-9]*))?$')


class ListingOpener:
    def __init__(self, opener, db):
        self.opener = opener
        self.db = db

    def open(self, request, **kwargs):
        url = request.full_url
        if not (PAGE.fullmatch(url) or url == channels.BASE + '/robots.txt'):
            raise ValueError('Listing route changed')
        started = time.time()
        code = 0
        try:
            response = self.opener.open(request, **kwargs)
            code = response.status
            return response
        except HTTPError as exc:
            code = exc.code
            raise
        finally:
            with self.db:
                self.db.execute('INSERT INTO listing_http VALUES(?,?,?)',(url,started,code))


def store(db, page, body, started):
    url = FIRST if page == 1 else FIRST + '&page=' + str(page)
    doc = parse(body)
    rows = channels.extract_listing(doc, url, all_listings=True)
    if not rows:
        raise ValueError('No recognizable channel listing rows')
    ids = sorted(r['username'] for r in rows)
    digest = hashlib.sha256('\n'.join(ids).encode()).hexdigest()
    next_url = FIRST + '&page=' + str(page + 1)
    more = any(urljoin(url, link) == next_url for link in doc.links())
    with db:
        repeated = db.execute('SELECT 1 FROM listing_pages WHERE fingerprint=?',(digest,)).fetchone()
        if repeated:
            more = False
        previous = sum(db.execute('SELECT 1 FROM channels WHERE username=?',(u,)).fetchone() is not None for u in ids)
        channels.save_listing(db, rows)
        channels.enqueue(db,url,'listing')
        db.execute("UPDATE queue SET status='done' WHERE url=?", (url,))
        if more:
            channels.enqueue(db,next_url,'listing')
        db.execute('INSERT INTO listing_pages VALUES(?,?,?,?,?,?,?)',
                   (page,digest,len(ids),len(ids)-previous,sum(bool(r.get('iran_geo_tag')) for r in rows),started,int(more)))
    return len(ids), more


def budgeted_request(request):
    while True:
        try:
            return request()
        except HTTPError as error:
            if error.code not in (403,429,500,502,503,504) or status(channels.BASE)['halted']:
                raise
            if not status(channels.BASE)['pause_left_s']:
                raise
        except (URLError, TimeoutError):
            if not status(channels.BASE)['pause_left_s']:
                raise
        # html_core reported the response. reserve() waits before the next request.


def next_page(db, start=None, sample_pages=0):
    """Prioritize a bounded sample, then resume the earliest uncovered sequential page."""
    pages = dict(db.execute('SELECT page,more FROM listing_pages ORDER BY page'))
    if start is not None:
        for page in range(start,start + sample_pages):
            if page in pages:
                if not pages[page]:
                    break
                continue
            return page
    page = 1
    while page in pages:
        if not pages[page]:
            return None
        page += 1
    return page


def run(args):
    args.work.mkdir(parents=True,exist_ok=True)
    with (args.work/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with closing(channels.connect(args.work)) as db:
            prepare(db)
            db.execute('CREATE TABLE IF NOT EXISTS listing_pages(page INTEGER PRIMARY KEY,fingerprint,rows,new_local,iran,started,more)')
            db.execute('CREATE TABLE IF NOT EXISTS listing_http(url TEXT, started REAL, code INTEGER)')
            flush(db,args.outbox)
            if args.ads_checkpoint and not db.execute("SELECT 1 FROM meta WHERE key='discovery_seed_v1'").fetchone():
                channels.seed(db,args.ads_checkpoint)
                seed_files(db,args.seed,args.advertisers,args.keywords,args.catalog)
                with db:
                    db.execute("INSERT INTO meta VALUES ('discovery_seed_v1','1')")
                    for (done,) in db.execute("SELECT page FROM listing_pages").fetchall():
                        url=FIRST if done==1 else FIRST+'&page='+str(done)
                        channels.enqueue(db,url,'listing')
                        db.execute("UPDATE queue SET status='done' WHERE url=?",(url,))
            page=next_page(db,args.start_page,args.sample_pages)
            if page is None:
                return
            if db.execute("SELECT 1 FROM meta WHERE key IN ('listing_hold','stop_reason','fast_hold') AND value<>''").fetchone():
                raise SystemExit('Recorded hold')
            client=Client(channels.BASE,delay=args.min_interval,resilient=True)
            client.opener=ListingOpener(client.opener, db)
            try:
                if status(channels.BASE)['halted']:
                    raise SystemExit('Host halted')
                budgeted_request(client.initialize)
                while not (args.work/'pause').exists():
                    if status(channels.BASE)['halted']:
                        raise SystemExit('Host halted')
                    url=FIRST if page==1 else FIRST+'&page='+str(page)
                    started=time.time()
                    if page==1 and args.first_page:
                        body=args.first_page.read_text()
                    else:
                        body=budgeted_request(lambda:client.get(url))
                    count, more=store(db,page,body,started)
                    flush(db,args.outbox)
                    print('page',page,'rows',count,'more',more,flush=True)
                    page=next_page(db,args.start_page,args.sample_pages)
                    if page is None:
                        return
            except (Challenge,HTTPError,ValueError) as error:
                with db:
                    db.execute("INSERT OR REPLACE INTO meta VALUES('listing_hold',?)",(str(error),))
                raise


if __name__=='__main__':
    import os
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--outbox',type=Path,required=True)
    parser.add_argument('--ads-checkpoint',type=Path)
    parser.add_argument('--seed',type=Path,action='append',default=[])
    parser.add_argument('--keywords',type=Path,nargs='+',default=[])
    parser.add_argument('--catalog',type=Path)
    parser.add_argument('--advertisers',type=Path)
    parser.add_argument('--start-page',type=int,help='Prioritize a bounded sample beginning at this listing page')
    parser.add_argument('--sample-pages',type=int,default=10,help='Maximum sample width before returning to backlog')
    parser.add_argument('--first-page',type=Path,help='Verified first listing response from the access check')
    parser.add_argument('--min-interval',type=float,default=os.getenv('CHANNEL_MIN_INTERVAL','1.0'))
    args=parser.parse_args()
    if args.sample_pages < 1 or (args.start_page is not None and args.start_page < 1):
        parser.error('page numbers and sample width must be positive')
    if args.keywords and not args.catalog:
        parser.error('keyword lookup requires --catalog')
    if not 1 <= args.min_interval < float('inf'):
        parser.error('interval must be finite and at least one second')
    os.environ["CHANNEL_MIN_INTERVAL"]=str(args.min_interval)
    run(args)
