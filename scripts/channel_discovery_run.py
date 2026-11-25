"""Non-pagination channel discovery using the existing channel checkpoint and outbox."""
import argparse
from contextlib import closing
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urljoin
import xml.etree.ElementTree as ET

import channels_html as ch
from channel_document import parse
from fast_iran_channels_crawler import prepare, flush, seed_files
from html_core import Client
from source_budget import Challenge, HostBlocked, status

SOURCES = ('sitemap','advertiser','placement','related','keyword','country')
MAP = re.compile(r'/sitemap(?:-index)?\.xml|/sitemap/[0-9]+\.xml')
ROOT = Path(__file__).resolve().parents[1]


def kind(url):
    u = urlsplit(url)
    if ch.origin(url) != ch.origin(ch.BASE) or u.fragment:
        return None
    if MAP.fullmatch(u.path) and not u.query:
        return 'sitemap'
    if ch.CHANNEL.fullmatch(u.path) and not u.query:
        return 'channel'
    if (ch.ADV_CHANNELS.fullmatch(u.path) or u.path.rstrip('/') == '/countries/ir/channels') and (
            not u.query or re.fullmatch(r'page=[1-9][0-9]*',u.query)):
        return 'listing'
    return None


class Restricted:
    def __init__(self, opener, db):
        self.opener, self.db = opener, db

    def open(self, req, **kwargs):
        if req.full_url != ch.BASE+'/robots.txt' and not kind(req.full_url):
            raise ValueError('Off-scope request or redirect')
        code = 0
        try:
            res = self.opener.open(req,**kwargs)
            code = res.status
            return res
        except HTTPError as err:
            code = err.code
            raise
        finally:
            with self.db:
                self.db.execute('INSERT INTO discovery_http VALUES(?,?,?)',(req.full_url,time.time(),code))


def enqueue(db, url, source, priority):
    url = urljoin(ch.BASE,url)
    job = kind(url)
    if not job:
        return
    if job=='channel':
        url=ch.BASE+'/channels/'+ch.CHANNEL.fullmatch(urlsplit(url).path)[1].lower()
    ch.enqueue(db,url,job,priority)
    db.execute('INSERT OR IGNORE INTO discovery_jobs VALUES(?,?)',(url,source))


def init(db,args):
    prepare(db)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS discovery_jobs(url TEXT PRIMARY KEY,source TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS discovery_origin ON discovery_jobs(source,url);
        CREATE TABLE IF NOT EXISTS discovery_http(url TEXT,started REAL,code INTEGER);
        CREATE TABLE IF NOT EXISTS discovery_found(username TEXT PRIMARY KEY,source TEXT,net INTEGER);
        CREATE TABLE IF NOT EXISTS discovery_seen(username TEXT PRIMARY KEY,source TEXT,net INTEGER);
    """)
    if db.execute("SELECT 1 FROM meta WHERE key='discovery_sources_ready'").fetchone():
        return
    # Existing keyword and placement jobs share the listing worker's checkpoint.
    # Reproduce explicit priority groups; unrelated legacy rows have no source attribution.
    with db:
        enqueue(db,'/sitemap.xml','sitemap',0)
        enqueue(db,'/countries/ir/channels','country',0)
        seed_files(db,[],ROOT/'data/advertiser_seeds.txt',[],None)
        db.execute("""INSERT OR IGNORE INTO discovery_jobs SELECT url,'advertiser' FROM queue
                   WHERE kind='listing' AND url LIKE 'https://tgadsspy.com/advertisers/%/channels%'""")
        db.execute("""INSERT OR IGNORE INTO discovery_jobs SELECT url,'placement' FROM queue
                   WHERE kind='channel' AND priority=0""")
        catalog=json.loads((ROOT/'data/channels.json').read_bytes())
        terms={line.split('#',1)[0].strip().casefold() for f in (ROOT/'data').glob('channel_keywords*.txt') for line in f.read_text().splitlines()}
        terms.discard('')
        for row in catalog['rows']:
            item=dict(zip(catalog['fields'],row))
            text=(str(item.get('t') or '')+' '+str(item.get('d') or '')).casefold()
            if item.get('u') and any(term in text for term in terms):
                enqueue(db,'/channels/'+item['u'],'keyword',2)
        with closing(sqlite3.connect(args.profiles.resolve().as_uri()+'?mode=ro',uri=True)) as old:
            for payload, in old.execute("SELECT payload FROM channels WHERE json_extract(payload,'$.record_detail')='profile'"):
                r=json.loads(payload)
                for link in r.get('links',[]):
                    if isinstance(link,dict) and kind(link.get('url',''))=='channel':
                        enqueue(db,link['url'],'related',2)
        with closing(sqlite3.connect(args.archive.resolve().as_uri()+'?mode=ro',uri=True)) as ads:
            for payload, in ads.execute("SELECT payload FROM records WHERE json_extract(payload,'$.geo')='IR'"):
                for link in json.loads(payload).get('details',{}).get('placements_shown',[]):
                    if isinstance(link,dict) and kind(link.get('url',''))=='channel':
                        enqueue(db,link['url'],'placement',0)
        db.execute("INSERT INTO meta VALUES('discovery_sources_ready','1')")


def save(db,row,source,known):
    u=row['username']
    db.execute('INSERT OR IGNORE INTO discovery_seen VALUES(?,?,?)',(u,source,int(u not in known)))
    if row.get('classification')=='unclassified' and not row.get('iran_advertisers'):
        return
    old=db.execute('SELECT payload FROM channels WHERE username=?',(u,)).fetchone()
    if old:
        previous=json.loads(old[0])
        if previous.get('record_detail')=='profile' and row.get('record_detail')!='profile':
            return
        if previous.get('iran_geo_tag'):
            row.update(country='IR',iran_geo_tag=True,classification='iran_confirmed',iran_evidence=previous.get('iran_evidence',[]))
        for key in ('members','description','category','language','avg_views'):
            if row.get(key) is None and previous.get(key) is not None:
                row[key]=previous[key]
    for field in ('jsonld','meta','text_segments','media','links'):
        row.pop(field,None)
    row['discovery_source']=source
    db.execute('INSERT OR IGNORE INTO discovery_found VALUES(?,?,?)',(u,source,int(u not in known)))
    db.execute('INSERT OR REPLACE INTO channels VALUES(?,?)',(u,json.dumps(row,ensure_ascii=False)))


def ingest(db,url,source,body,known):
    job=kind(url)
    if job=='sitemap':
        tree=ET.fromstring(body)
        name=tree.tag.split('}')[-1]
        if name not in ('sitemapindex','urlset'):
            raise ValueError('Unexpected sitemap layout')
        for loc in tree.findall('{*}sitemap/{*}loc' if name=='sitemapindex' else '{*}url/{*}loc'):
            target=loc.text or ''
            k=kind(target)
            if k=='sitemap' or k=='channel':
                enqueue(db,target,'sitemap',3 if k=='channel' else 0)
                if k=='channel':
                    u=ch.listing_handle(target,url)
                    if u:
                        db.execute('INSERT OR IGNORE INTO discovery_seen VALUES(?,?,?)',(u,'sitemap',int(u not in known)))
        return
    doc=parse(body)
    if job=='channel':
        u=ch.listing_handle(url,url)
        canonical=any(n.tag=='link' and n.attrs.get('rel')=='canonical' and
                      ch.listing_handle(n.attrs.get('href',''),url)==u for n in doc.root.nodes())
        if not canonical:
            raise ValueError('Unverified profile identity')
        row=ch.extract(doc,url)
        save(db,row,source,known)
        for name in row['related_channels']:
            enqueue(db,'/channels/'+name,'related',2)
    else:
        for row in ch.extract_listing(doc,url,all_listings=True):
            save(db,row,source,known)
            enqueue(db,row['channel_url'],source,1)
        for link in doc.links():
            target=urljoin(url,link)
            if urlsplit(target).path==urlsplit(url).path and kind(target)=='listing':
                enqueue(db,target,source,1)


def run(args):
    with (args.work/'worker.lock').open('a') as lock, closing(ch.connect(args.work)) as db:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        init(db,args)
        known=set(json.loads(args.baseline.read_bytes()))
        client=Client(ch.BASE,1,resilient=True)
        client.opener=Restricted(client.opener,db)
        with db:
            db.execute("UPDATE queue SET status='pending' WHERE status='in_progress' AND url IN (SELECT url FROM discovery_jobs)")
        flush(db,args.outbox)
        last_flush=time.monotonic()
        while not (args.work/'discovery-pause').exists():
            if status(ch.BASE)['halted'] or db.execute("SELECT 1 FROM meta WHERE key IN ('discovery_hold','listing_hold','fast_hold','stop_reason') AND value!=''").fetchone():
                return
            url=None
            try:
                if client.robots is None:
                    client.initialize()
                turn=int((db.execute("SELECT value FROM meta WHERE key='discovery_turn'").fetchone() or (0,))[0])
                for i in range(len(SOURCES)):
                    index=(turn+i)%len(SOURCES)
                    source=SOURCES[index]
                    job=db.execute("""SELECT q.url FROM discovery_jobs j JOIN queue q USING(url)
                        WHERE j.source=? AND q.status='pending' ORDER BY q.priority,q.url LIMIT 1""",(source,)).fetchone()
                    if job:
                        url=job[0]
                        break
                if url is None:
                    flush(db,args.outbox)
                    time.sleep(30)
                    continue
                with db:
                    db.execute("INSERT OR REPLACE INTO meta VALUES('discovery_turn',?)",(str((index+1)%len(SOURCES)),))
                    db.execute("UPDATE queue SET status='in_progress' WHERE url=?",(url,))
                if not client.robots.allowed(url):
                    with db: db.execute("UPDATE queue SET status='skipped',error='robots' WHERE url=?",(url,))
                    continue
                body=client.get(url)
                with db:
                    ingest(db,url,source,body,known)
                    db.execute("UPDATE queue SET status='done',error=NULL WHERE url=?",(url,))
                outgoing=db.execute('SELECT count(*) FROM channel_outgoing').fetchone()[0]
                if outgoing>=100 or time.monotonic()-last_flush>=300:
                    flush(db,args.outbox)
                    last_flush=time.monotonic()
                print(source,'done',flush=True)
            except (Challenge,HostBlocked) as exc:
                with db:
                    db.execute("INSERT OR REPLACE INTO meta VALUES('discovery_hold',?)",(type(exc).__name__,))
                flush(db,args.outbox)
                return
            except HTTPError as exc:
                if exc.code==401:
                    with db: db.execute("INSERT OR REPLACE INTO meta VALUES('discovery_hold','401')")
                    flush(db,args.outbox)
                    return
                if exc.code not in (403,429) and exc.code<500:
                    if url is None: raise
                    with db: db.execute("UPDATE queue SET status='failed',error=? WHERE url=?",(str(exc.code),url))
            except (URLError,TimeoutError):
                if not status(ch.BASE)['pause_left_s']: raise
            except (ValueError,ET.ParseError) as exc:
                if url is None: raise
                with db: db.execute("UPDATE queue SET status='failed',error=? WHERE url=?",(str(exc),url))
            finally:
                if url:
                    with db: db.execute("UPDATE queue SET status='pending' WHERE url=? AND status='in_progress'",(url,))

        flush(db,args.outbox)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for field in ('work','outbox','baseline','profiles','archive'):
        parser.add_argument('--'+field,type=Path,required=True)
    os.environ.setdefault('CHANNEL_MIN_INTERVAL','1.0')
    run(parser.parse_args())
