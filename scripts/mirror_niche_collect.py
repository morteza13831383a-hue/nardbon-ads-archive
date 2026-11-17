#!/usr/bin/env python3
"""Checkpointed, Iran-filtered niche listing collector on approved mirrors."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin

import archive_html
import mirror_ads
from ad_claims import Claims
from html_core import Client, Document
from source_budget import Challenge, HostBlocked

HOSTS = ('telegramadsarchive.com', 'telegramadsanalytics.com',
         'telegramads.it', 'tgads.biz', 'tgads.observer', 'telegramstats.net')
NICHES = ('adult', 'parenting', 'mini-apps-productivity', 'dating-premium',
          'podcasts', 'religion', 'auto', 'wellness', 'real-estate', 'jobs',
          'casino', 'travel', 'news', 'prop-firm', 'dating', 'gaming',
          'marketing', 'gambling', 'health', 'betting', 'finance', 'tech',
          'bots', 'retail', 'fashion', 'crypto', 'education', 'trading', 'vpn')
COUNTS = (0, 6, 9, 14, 15, 56, 91, 145, 228, 260, 278, 281, 337,
          427, 500, 549, 701, 816, 880, 1169, 1370, 1667, 2586,
          2715, 3200, 3319, 4567, 8977, 9185)
AD_LINK = re.compile(r'/ads/([a-z0-9]+)')
COUNT = re.compile(r'Found\s*([0-9,]+\+?)', re.I)


def connect(path, host, cutoff):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
      CREATE TABLE IF NOT EXISTS niches(name TEXT PRIMARY KEY,site_count INTEGER,page INTEGER DEFAULT 1,status TEXT DEFAULT 'pending',last_signature TEXT);
      CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY,niche TEXT,status TEXT DEFAULT 'pending',error TEXT);
      CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,payload TEXT);
      CREATE TABLE IF NOT EXISTS requests(kind TEXT,key TEXT,status INTEGER,at TEXT);
      CREATE TABLE IF NOT EXISTS delivered(id TEXT PRIMARY KEY);''')
    old=db.execute("SELECT value FROM meta WHERE key='cutoff'").fetchone()
    if old and old[0]!=cutoff:raise ValueError('Cutoff changed; checkpoint retained')
    db.execute("INSERT OR IGNORE INTO meta VALUES('cutoff',?)",(cutoff,))
    for i,(n,count) in enumerate(zip(NICHES,COUNTS)):
        if i%len(HOSTS)==HOSTS.index(host):
            db.execute('INSERT OR IGNORE INTO niches(name,site_count)VALUES(?,?)',(n,count))
    db.commit()
    return db


def listing(client,db,niche,page,known):
    query=urlencode({'geo':'IR','niche':niche,'page':page})
    url=client.base+'/ads?'+query
    doc=Document(client.get(url))
    shown=COUNT.findall(doc.root.text())
    if shown and '+' in shown[-1]:raise ValueError('Niche still capped at 10,000')
    expected=db.execute('SELECT site_count FROM niches WHERE name=?',(niche,)).fetchone()[0]
    total=int(shown[-1].replace(',','')) if shown else expected
    ids=set()
    for href in doc.links():
        m=AD_LINK.fullmatch(href)
        if m:ids.add(m[1])
    signature=hashlib.sha256('\n'.join(sorted(ids)).encode()).hexdigest()
    previous=db.execute('SELECT last_signature FROM niches WHERE name=?',(niche,)).fetchone()[0]
    if ids and signature==previous:raise ValueError('Repeated listing page; cursor held for review')
    next_page=page+1
    for ad_id in ids-known:
        db.execute('INSERT OR IGNORE INTO candidates(id,niche)VALUES(?,?)',(ad_id,niche))
    # A missing next-page link is not proof of completion when the site's own
    # count says more result pages remain. Keep the explicit page cursor moving.
    has_next=any(('page='+str(page+1)) in href for href in doc.links() if href.startswith('/ads?'))
    walked={int(key.rsplit(':',1)[1]) for key, in db.execute("SELECT key FROM requests WHERE kind='listing' AND key LIKE ?",(niche+':%',))
            if ':' in key and key.rsplit(':',1)[1].isdigit()}
    capacity=30*len(walked|{page})
    if not ids and capacity<max(total,expected or 0)-30:
        raise ValueError('Listing empty before advertised niche count')
    done=not has_next and capacity>=max(total,expected or 0)-30
    db.execute('UPDATE niches SET site_count=?,page=?,status=?,last_signature=? WHERE name=?',
               (total,next_page,'done'if done else'pending',signature,niche))
    db.execute('INSERT INTO requests VALUES(?,?,?,?)',('listing',niche+':'+str(page),200,datetime.now(timezone.utc).isoformat()))
    db.commit()
    return len(ids),len(ids-known),done


def detail(client,db,claims,known,cutoff,limit=30):
    done=0
    owner='mirror_iran:'+client.base.split('//',1)[1]
    for ad_id,niche in db.execute("SELECT id,niche FROM candidates WHERE status='pending' ORDER BY rowid LIMIT ?",(limit,)).fetchall():
        if ad_id in known:
            db.execute("UPDATE candidates SET status='known' WHERE id=?",(ad_id,));db.commit();continue
        claim=claims.acquire(ad_id,owner)
        if claim!='acquired':
            if claim=='done':db.execute("UPDATE candidates SET status='shared_done' WHERE id=?",(ad_id,));db.commit()
            else:db.execute("UPDATE candidates SET status='shared_pending' WHERE id=?",(ad_id,));db.commit()
            continue
        verified=False
        try:
            url=client.base+'/ads/'+ad_id
            doc=Document(client.get(url))
            record,state=archive_html.extract(doc,url,cutoff,iran_listing=True)
            if state=='iran' and record:
                record['via']='mirror_iran_niche'
                record['listing_source_url']=client.base+'/ads?'+urlencode({'geo':'IR','niche':niche})
                db.execute('INSERT OR IGNORE INTO records VALUES(?,?)',(ad_id,json.dumps(record,ensure_ascii=False)))
                verified=True
            db.execute('UPDATE candidates SET status=? WHERE id=?',(state,ad_id))
            db.execute('INSERT INTO requests VALUES(?,?,?,?)',('detail',ad_id,200,datetime.now(timezone.utc).isoformat()))
            db.commit();done+=1
        except (HTTPError,Challenge,HostBlocked) as exc:
            code=getattr(exc,'code',0)
            db.execute('INSERT INTO requests VALUES(?,?,?,?)',('detail',ad_id,code,datetime.now(timezone.utc).isoformat()))
            db.execute('UPDATE candidates SET error=? WHERE id=?',(type(exc).__name__,ad_id))
            db.commit();break
        except Exception as exc:
            db.execute('UPDATE candidates SET status=?,error=? WHERE id=?',('failed',type(exc).__name__,ad_id));db.commit()
        finally:claims.finish(ad_id,owner,verified)
    return done


def main():
    p=argparse.ArgumentParser();p.add_argument('--host',choices=HOSTS,required=True)
    p.add_argument('--work-dir',type=Path,required=True);p.add_argument('--repo-root',type=Path,required=True)
    p.add_argument('--spy-checkpoint',type=Path,required=True);p.add_argument('--repo',required=True)
    p.add_argument('--max-pages',type=int,default=0);a=p.parse_args()
    with sqlite3.connect('file:'+str(a.spy_checkpoint)+'?mode=ro',uri=True) as spy:
        cutoff=spy.execute("SELECT value FROM meta WHERE key='cutoff'").fetchone()[0]
    db=connect(a.work_dir/'checkpoint.sqlite3',a.host,cutoff)
    known=mirror_ads.archive_ids(a.repo_root)
    known.update(x[0]for x in db.execute('SELECT id FROM records'))
    client=Client('https://'+a.host,4,resilient=True)
    try:client.initialize()
    except (HTTPError,Challenge,HostBlocked):
        raise
    claims=Claims();mirror_ads.BASE=client.base
    pages=0;last_publish=time.monotonic()
    try:
        while True:
            job=db.execute("SELECT name,page FROM niches WHERE status='pending' ORDER BY site_count,name LIMIT 1").fetchone()
            if not job:break
            try:
                ids,new,complete=listing(client,db,*job,known)
            except (HTTPError,Challenge,HostBlocked) as exc:
                break
            except Exception as exc:
                db.execute("UPDATE niches SET status='review' WHERE name=?",(job[0],))
                db.execute("INSERT OR REPLACE INTO meta VALUES('review_reason',?)",(job[0]+': '+type(exc).__name__,));db.commit();break
            pages+=1
            if pages%10==0:detail(client,db,claims,known,cutoff)
            if pages%5==0 or complete or time.monotonic()-last_publish>=1200:
                mirror_ads.publish(db,a.work_dir,a.repo);last_publish=time.monotonic()
            print(json.dumps({'host':a.host,'niche':job[0],'page':job[1],'seen':ids,'new_vs_local':new,'complete':complete}),flush=True)
            if a.max_pages and pages>=a.max_pages:break
        if not a.max_pages:
            while db.execute("SELECT 1 FROM candidates WHERE status='pending' LIMIT 1").fetchone():
                detail(client,db,claims,known,cutoff,30)
                if time.monotonic()-last_publish>=1200:
                    mirror_ads.publish(db,a.work_dir,a.repo);last_publish=time.monotonic()
                from source_budget import status as budget_status
                if budget_status(client.base)['pause_left_s']:break
        mirror_ads.publish(db,a.work_dir,a.repo)
    finally:claims.close();db.close()

if __name__=='__main__':main()
