"""Verify observed sitemap IDs on approved ad mirrors with disjoint, persistent work."""
import argparse
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import re
from urllib.error import HTTPError
from urllib.parse import urlsplit

import archive_html as archive
from ad_claims import Claims
from html_core import Client,Document
from source_budget import status as budget_status, Challenge, HostBlocked

HOSTS = {'telegramads.it','tgads.biz','tgads.observer','telegramstats.net','telegramadsarchive.com','telegramadsanalytics.com'}
BASE = 'https://tgadsspy.com/ads/'
CUID = re.compile(r'c[a-z0-9]{24}')

def connect(path):
    db=sqlite3.connect(path,timeout=45)
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript("""
    CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,band INTEGER NOT NULL,sort TEXT NOT NULL,
        state TEXT DEFAULT 'ready',host TEXT,at REAL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS work ON jobs(state,band,sort);
    CREATE TABLE IF NOT EXISTS events(host TEXT,id TEXT,band INTEGER,status TEXT,at REAL);
    CREATE TABLE IF NOT EXISTS hosts(host TEXT PRIMARY KEY,hold TEXT);
    """)
    return db

def seed(db,primary,neighbors):
    # Only existing sitemap URLs qualify. Distance is used for ordering, never for an import.
    count=0
    with db:
        for dist,identifier,*_ in neighbors:
            if dist>5 or not CUID.fullmatch(identifier):
                continue
            row=primary.execute("SELECT status,via FROM queue WHERE url=? AND kind='detail'",(BASE+identifier,)).fetchone()
            if not row or row[0]!='pending' or row[1] not in ('sitemap','sitemap_same_time'):
                continue
            order=hashlib.sha256(identifier.encode()).hexdigest()
            count+=db.execute("INSERT OR IGNORE INTO jobs(id,band,sort) VALUES(?,?,?)",(identifier,dist,order)).rowcount
    return count

def refresh_pool(db, primary, shard_dir, max_ms):
    """Rank only published sitemap IDs using nearby verified Iran records on the same creator."""
    cutoff=datetime.fromisoformat(primary.execute("SELECT value FROM meta WHERE key='cutoff'").fetchone()[0])
    times=defaultdict(set)
    def learn(aid):
        if CUID.fullmatch(aid):
            times[aid[13:17]].add(int(aid[1:9],36))
    for path in Path(shard_dir).glob("*.json"):
        for row in json.loads(path.read_bytes()):
            if row.get("geo") != "IR" or not row.get("first_seen"):
                continue
            seen=datetime.fromisoformat(row["first_seen"].replace("Z","+00:00"))
            if seen.tzinfo and seen <= cutoff:
                learn(row["id"])
    for (aid,) in primary.execute("SELECT id FROM records WHERE json_extract(payload,'$.geo')='IR'"):
        learn(aid)
    keys={key:sorted(values) for key,values in times.items()}
    batch=[]
    sql=("INSERT INTO jobs(id,band,sort) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET band=excluded.band "
         "WHERE jobs.state='ready' AND excluded.band<jobs.band")
    def flush():
        with db:
            db.executemany(sql,batch)
        batch.clear()
    # A cursor on the live checkpoint would hold a read lock throughout the ranking pass.
    # Copy only URL strings, then release the cursor before working on the separate database.
    rows=primary.execute("SELECT url FROM queue WHERE kind='detail' AND status='pending' "
                         "AND via IN ('sitemap','sitemap_same_time')").fetchall()
    for (url,) in rows:
        aid=url.rsplit('/',1)[-1]
        if not CUID.fullmatch(aid):
            continue
        ts=int(aid[1:9],36)
        values=keys.get(aid[13:17])
        if not values:
            continue
        k=bisect_left(values,ts)
        distance=min(abs(values[j]-ts) for j in (k-1,k) if 0<=j<len(values))
        if distance <= max_ms:
            band=next(v for v in (0,1,5,25,100,500,2000) if distance<=v)
            batch.append((aid,band,hashlib.sha256(aid.encode()).hexdigest()))
            if len(batch)>=500:flush()
    if batch:flush()

def check_cutoff(db, primary):
    cutoff=primary.execute("SELECT value FROM meta WHERE key='cutoff'").fetchone()[0]
    if datetime.fromisoformat(cutoff).tzinfo is None:raise ValueError("Cutoff must have a timezone")
    with db:
        db.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execute("INSERT OR IGNORE INTO settings VALUES('cutoff',?)",(cutoff,))
        if db.execute("SELECT value FROM settings WHERE key='cutoff'").fetchone()[0]!=cutoff:
            raise ValueError("Mirror checkpoint belongs to a different cutoff")
    return cutoff

def claim(db,host):
    with db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE jobs SET state='ready' WHERE state='working' AND at<?",(time.time()-7300,))
        row=db.execute("SELECT id,band FROM jobs WHERE state='ready' ORDER BY band,sort LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE jobs SET state='working',host=?,at=? WHERE id=?",(host,time.time(),row[0]))
    return row

def verified(html,url,cutoff):
    identifier=urlsplit(url).path.split('/')[-1]
    doc=Document(html)
    match=[w for w in doc.jsonld() if w.get('identifier')==identifier and
           ('CreativeWork' in (w.get('@type') if isinstance(w.get('@type'),list) else [w.get('@type')]))]
    if len(match)!=1:raise ValueError('Missing exact creative identity')
    row,state=archive.extract(doc,url,cutoff)
    if row and not (row.get('id')==identifier and row['jsonld'].get('identifier')==identifier and row['geo']=='IR'):
        raise ValueError('Creative mismatch')
    if row:row['html_sha256']=hashlib.sha256(html.encode()).hexdigest()
    return row,state

def save(primary,row,host,band):
    with primary:
        primary.execute('BEGIN IMMEDIATE')
        identifier=row['id']
        eligible=primary.execute("SELECT 1 FROM queue WHERE url=? AND status='pending'",(BASE+identifier,)).fetchone()
        if not eligible:return False
        created=primary.execute('INSERT OR IGNORE INTO records(id,payload) VALUES(?,?)',
                             (identifier,json.dumps(row,ensure_ascii=False))).rowcount
        primary.execute("UPDATE queue SET status='iran',via=? WHERE url=? AND status='pending'",
                        ('verified_mirror_'+host+'_d'+str(band),BASE+identifier))
    return bool(created)

def run(args,db,primary):
    host=args.host
    if db.execute('SELECT hold FROM hosts WHERE host=? AND hold IS NOT NULL',(host,)).fetchone():return
    base='https://'+host
    budget=budget_status(base)
    if budget['halted'] or budget['pause_left_s']:return
    cutoff=primary.execute("SELECT value FROM meta WHERE key='cutoff'").fetchone()[0]
    claims=Claims()
    owner='verified_sitemap_'+host
    since=time.time()
    client=Client(base,4,resilient=True)
    try:
        client.initialize()
        requests=0
        while not args.pages or requests<args.pages:
            job=claim(db,host)
            if not job:break
            identifier,band=job
            state='shared'
            request=False
            acquired=False
            done=False
            try:
                pending=primary.execute("SELECT 1 FROM queue WHERE url=? AND status='pending'",(BASE+identifier,)).fetchone()
                if not pending:continue
                if claims.acquire(identifier,owner)!='acquired':continue
                acquired=True
                url=base+'/ads/'+identifier
                if not client.robots.allowed(url):
                    raise RuntimeError('Path disallowed')
                request=True
                requests+=1
                row,state=verified(client.get(url),url,cutoff)
                if row:
                    done=save(primary,row,host,band)
                    state='saved' if done else 'known'
                print(host,'band',band,state,flush=True)
            except HTTPError as exc:
                state='http_'+str(exc.code)
                if exc.code not in (404,410):raise
            except (Challenge,HostBlocked,ValueError,RuntimeError,OSError) as exc:
                state=type(exc).__name__;raise
            finally:
                if acquired:claims.finish(identifier,owner,done)
                with db:
                    db.execute('UPDATE jobs SET state=?,at=? WHERE id=?',(state,time.time(),identifier))
                    if request:db.execute('INSERT INTO events VALUES(?,?,?,?,?)',(host,identifier,band,state,time.time()))
            if request:
                recent=db.execute("SELECT status FROM events WHERE host=? AND band=? AND at>=? ORDER BY rowid DESC LIMIT 16",
                                  (host,band,since)).fetchall()
                if len(recent)>=8 and sum(v[0]=='saved' for v in recent)*4<len(recent):
                    with db:db.execute('INSERT OR REPLACE INTO hosts VALUES(?,?)',(host,'Low yield in band '+str(band)))
                    print(host,'Low observed yield; stopping this worker',flush=True);break
    except (Challenge,HostBlocked,HTTPError,ValueError,RuntimeError,OSError) as exc:
        reason=type(exc).__name__+' '+str(getattr(exc,'code',''))
        with db:db.execute('INSERT OR REPLACE INTO hosts VALUES(?,?)',(host,reason))
        print(host,'stopped',reason,flush=True)
    finally:claims.close()

if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--work-dir',type=Path,required=True)
    ap.add_argument('--primary',type=Path,required=True)
    ap.add_argument('--seed',type=Path)
    ap.add_argument('--refresh-from',type=Path)
    ap.add_argument('--max-ms',type=int,choices=(0,1,5,25,100,500,2000),default=25)
    ap.add_argument('--host',choices=sorted(HOSTS))
    ap.add_argument('--pages',type=int,default=0)
    a=ap.parse_args();a.work_dir.mkdir(parents=True,exist_ok=True)
    with connect(a.work_dir/'mirror-jobs.sqlite3') as db,sqlite3.connect(a.primary.resolve().as_uri()+'?mode=rw',uri=True,timeout=60) as primary:
        check_cutoff(db,primary)
        if a.seed:print('newly queued',seed(db,primary,json.loads(a.seed.read_bytes())),flush=True)
        if a.refresh_from:refresh_pool(db,primary,a.refresh_from,a.max_ms)
        if a.host:
            with (a.work_dir/(a.host+'.lock')).open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);run(a,db,primary)
