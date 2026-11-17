#!/usr/bin/env python3
"""Checkpointed Iran ad discovery via uncategorized facets and Persian search."""
import argparse
from datetime import datetime,timezone
import hashlib
from itertools import product
import json
from pathlib import Path
import re
import sqlite3
from urllib.error import HTTPError
from urllib.parse import urlencode

import archive_html
import mirror_ads
from ad_claims import Claims
from html_core import Client,Document
from source_budget import Challenge,HostBlocked

SURFACES=('channel','bot')
CURRENCIES=('ton','eur')
FORMATS=('banner','botMiniApp','channelPic','giveaway','invoice','text')
CREATIVES=('photo','text','video')
# These values come from the site's own Iran listing links. No Premium value or
# second chronological sort was exposed, so neither is guessed here.
FACETS=tuple(dict.fromkeys(
    ('surface='+v for v in SURFACES)
    ))+tuple('source='+v for v in CURRENCIES)+tuple('type='+v for v in FORMATS)+tuple('media='+v for v in CREATIVES)+('emoji=only',)+tuple(
    urlencode({'surface':surface,'source':currency}) for surface,currency in product(SURFACES,CURRENCIES)
)+tuple(
    urlencode({'surface':surface,'source':currency,'type':fmt,'media':creative,**({'emoji':'only'}if emoji else {})})
    for surface,currency,fmt,creative,emoji in product(SURFACES,CURRENCIES,FORMATS,CREATIVES,(False,True))
)
COUNT=re.compile(r'Found\s*([0-9,]+\+?)',re.I)
AD=re.compile(r'/ads/([a-z0-9]+)')

def now():return datetime.now(timezone.utc).isoformat()

def terms(path):
    if not path.exists():return []
    return [v for line in path.read_text('utf-8').splitlines()
            if (v:=line.split('#',1)[0].strip()) and len(v)>=2]

def owned(kind,key,shard_index,shard_count):
    return int.from_bytes(hashlib.sha256((kind+':'+key).encode()).digest()[:8],'big')%shard_count==shard_index

def connect(path,cutoff,root,shard_index=0,shard_count=1):
    path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=30)
    db.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
      CREATE TABLE IF NOT EXISTS jobs(key TEXT PRIMARY KEY,kind TEXT,page INTEGER DEFAULT 1,status TEXT DEFAULT 'pending',site_count TEXT,new_ids INTEGER DEFAULT 0,last_signature TEXT);
      CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY,key TEXT,listing_url TEXT,status TEXT DEFAULT 'pending',error TEXT);
      CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,payload TEXT);
      CREATE TABLE IF NOT EXISTS requests(kind TEXT,key TEXT,status INTEGER,at TEXT);
      CREATE TABLE IF NOT EXISTS delivered(id TEXT PRIMARY KEY);''')
    old=db.execute("SELECT value FROM meta WHERE key='cutoff'").fetchone()
    if old and old[0]!=cutoff:raise ValueError('Fixed cutoff changed')
    db.execute("INSERT OR IGNORE INTO meta VALUES('cutoff',?)",(cutoff,))
    for x in FACETS:
        if owned('facet',x,shard_index,shard_count):db.execute("INSERT OR IGNORE INTO jobs(key,kind)VALUES(?,'facet')",(x,))
    for file in ('channel_keywords.txt','channel_keywords_auto.txt'):
        for term in terms(root/'data'/file):
            if owned('keyword',term,shard_index,shard_count):
                db.execute("INSERT OR IGNORE INTO jobs(key,kind)VALUES(?,'keyword')",(term,))
    for key,kind in db.execute("SELECT key,kind FROM jobs WHERE status='pending'").fetchall():
        if not owned(kind,key,shard_index,shard_count):
            db.execute("UPDATE jobs SET status='other_shard' WHERE key=?",(key,))
    db.commit();return db

def query(key,kind,page):
    if kind=='facet':
        from urllib.parse import parse_qs
        args={k:v[0] for k,v in parse_qs(key).items()}
    else:args={'q':key}
    args={'geo':'IR',**args,'page':page}
    return urlencode(args)

def listing(db,client,key,kind,page,known):
    url=client.base+'/ads?'+query(key,kind,page)
    doc=Document(client.get(url))
    counts=COUNT.findall(doc.root.text())
    ids={m[1]for link in doc.links()if(m:=AD.fullmatch(link))}
    if not counts and not ids:
        raise ValueError('No result count or ad links; listing layout needs review')
    signature=hashlib.sha256('\n'.join(sorted(ids)).encode()).hexdigest()
    previous=db.execute('SELECT last_signature FROM jobs WHERE key=?',(key,)).fetchone()[0]
    if ids and signature==previous:raise ValueError('Repeated result page')
    seen={r[0]for r in db.execute('SELECT id FROM candidates')}
    new=ids-known-seen
    for ad_id in new:db.execute('INSERT OR IGNORE INTO candidates(id,key,listing_url)VALUES(?,?,?)',(ad_id,key,url))
    has_next=any('page='+str(page+1)in x for x in doc.links()if x.startswith('/ads?'))
    complete=not has_next or(kind=='keyword' and not new)
    db.execute('UPDATE jobs SET page=?,status=?,site_count=?,new_ids=new_ids+?,last_signature=? WHERE key=?',
               (page+1,'done'if complete else'pending',counts[-1]if counts else None,len(new),signature,key))
    db.execute('INSERT INTO requests VALUES(?,?,?,?)',('listing',key+':'+str(page),200,now()))
    db.commit();return len(ids),len(new),complete

def details(db,client,claims,known,cutoff,limit=30):
    owner='mirror_iran:'+client.base.split('//',1)[1]
    for ad_id,key,source in db.execute("SELECT id,key,listing_url FROM candidates WHERE status='pending' ORDER BY rowid LIMIT ?",(limit,)).fetchall():
        if ad_id in known:
            db.execute("UPDATE candidates SET status='known' WHERE id=?",(ad_id,));db.commit();continue
        claim=claims.acquire(ad_id,owner)
        if claim!='acquired':
            db.execute('UPDATE candidates SET status=? WHERE id=?',('shared_'+claim,ad_id));db.commit();continue
        verified=False
        try:
            url=client.base+'/ads/'+ad_id
            doc=Document(client.get(url))
            row,state=archive_html.extract(doc,url,cutoff,iran_listing=True)
            if row:
                row['via']='mirror_iran_gap'
                row['listing_source_url']=source
                db.execute('INSERT OR IGNORE INTO records VALUES(?,?)',(ad_id,json.dumps(row,ensure_ascii=False)))
                verified=True
            db.execute('UPDATE candidates SET status=? WHERE id=?',(state,ad_id))
            db.execute('INSERT INTO requests VALUES(?,?,?,?)',('detail',ad_id,200,now()))
            db.commit()
        except (HTTPError,Challenge,HostBlocked)as exc:
            code=getattr(exc,'code',0)
            db.execute('INSERT INTO requests VALUES(?,?,?,?)',('detail',ad_id,code,now()))
            db.commit();break
        except Exception as exc:
            db.execute('UPDATE candidates SET status=?,error=? WHERE id=?',('failed',type(exc).__name__,ad_id));db.commit()
        finally:claims.finish(ad_id,owner,verified)

def main():
    p=argparse.ArgumentParser();p.add_argument('--host',default='telegramadsarchive.com')
    p.add_argument('--work-dir',type=Path,required=True);p.add_argument('--repo-root',type=Path,required=True)
    p.add_argument('--spy-checkpoint',type=Path,required=True);p.add_argument('--repo',required=True)
    p.add_argument('--max-pages',type=int,default=0)
    p.add_argument('--shard-index',type=int,default=0);p.add_argument('--shard-count',type=int,default=1);a=p.parse_args()
    if a.shard_count<1 or not 0<=a.shard_index<a.shard_count:p.error('Invalid shard')
    with sqlite3.connect('file:'+str(a.spy_checkpoint)+'?mode=ro',uri=True)as spy:
        cutoff=spy.execute("SELECT value FROM meta WHERE key='cutoff'").fetchone()[0]
    db=connect(a.work_dir/'checkpoint.sqlite3',cutoff,a.repo_root,a.shard_index,a.shard_count)
    known=mirror_ads.archive_ids(a.repo_root)
    known.update(r[0]for r in db.execute('SELECT id FROM records'))
    client=Client('https://'+a.host,4,resilient=True)
    try:client.initialize()
    except (HTTPError,Challenge,HostBlocked):
        raise
    mirror_ads.BASE=client.base;claims=Claims();pages=0
    try:
        details(db,client,claims,known,cutoff,100)
        while True:
            turn=db.execute("SELECT value FROM meta WHERE key='job_turn'").fetchone()
            preferred=turn[0]if turn else'facet'
            job=db.execute("SELECT key,kind,page FROM jobs WHERE status='pending' "
                "ORDER BY CASE WHEN kind=? THEN 0 ELSE 1 END,rowid LIMIT 1",(preferred,)).fetchone()
            if not job:break
            try:seen,new,complete=listing(db,client,*job,known)
            except (HTTPError,Challenge,HostBlocked)as exc:
                break
            except Exception as exc:
                db.execute("UPDATE jobs SET status='review' WHERE key=?",(job[0],))
                db.execute("INSERT OR REPLACE INTO meta VALUES('review_reason',?)",(job[0]+': '+type(exc).__name__+': '+str(exc)[:160],));db.commit();break
            pages+=1;details(db,client,claims,known,cutoff)
            db.execute("INSERT OR REPLACE INTO meta VALUES('job_turn',?)",('keyword'if job[1]=='facet'else'facet',));db.commit()
            if pages%5==0 or complete:mirror_ads.publish(db,a.work_dir,a.repo)
            print(json.dumps({'key':job[0],'page':job[2],'seen':seen,'new':new,'done':complete},ensure_ascii=False),flush=True)
            if a.max_pages and pages>=a.max_pages:break
        mirror_ads.publish(db,a.work_dir,a.repo)
    finally:claims.close();db.close()

if __name__=='__main__':main()
