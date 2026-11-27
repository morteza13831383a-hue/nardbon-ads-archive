#!/usr/bin/env python3
"""Shared Iran niche page queue; seven hosts read listing cards without ad-detail requests."""
import argparse
import base64
from datetime import datetime, timezone
import filelock as fcntl
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlsplit
from uuid import uuid4

from archive_collect import gh
from archive_yield import cuid_time
from html_core import Client, Document
from mirror_niche_collect import HOSTS as MIRRORS, NICHES
from source_budget import Challenge, HostBlocked, status as budget_status

HOSTS=(*MIRRORS,'tgadsspy.com')
AD=re.compile(r'^/ads/([a-z0-9]+)$')
ROOT=Path(__file__).resolve().parents[1]
DEFAULT_WORK=Path('/workspace/work/niche-listing-sweep')


def now():return datetime.now(timezone.utc).isoformat()


def db_open(path):
    path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=30)
    db.execute('PRAGMA busy_timeout=30000')
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
      CREATE TABLE IF NOT EXISTS pages(niche TEXT,page INTEGER,status TEXT DEFAULT 'pending',prior_seen INTEGER DEFAULT 0,
        owner TEXT,token TEXT,claimed_at REAL,site_count INTEGER,rows INTEGER,new_ads INTEGER,error TEXT,
        PRIMARY KEY(niche,page));
      CREATE TABLE IF NOT EXISTS known_ids(id TEXT PRIMARY KEY);
      CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,payload TEXT);
      CREATE TABLE IF NOT EXISTS delivered(id TEXT PRIMARY KEY);
      CREATE TABLE IF NOT EXISTS enrichment(id TEXT PRIMARY KEY,reason TEXT);
      CREATE TABLE IF NOT EXISTS requests(host TEXT,niche TEXT,page INTEGER,status INTEGER,at TEXT);''')
    return db


def old_niches(work_root):
    counts={};seen=set()
    for host in MIRRORS:
        path=work_root/host/'checkpoint.sqlite3'
        with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as db:
            for name,count in db.execute('SELECT name,site_count FROM niches'):
                counts[name]=max(counts.get(name,0),count or 0)
            for key, in db.execute("SELECT key FROM requests WHERE kind='listing'"):
                name,sep,page=key.rpartition(':')
                if sep and page.isdigit():seen.add((name,int(page)))
    return counts,seen


def seed(db,work_root,repo_root,spy_checkpoint,cutoff):
    prior=db.execute("SELECT value FROM meta WHERE key='cutoff'").fetchone()
    if prior and prior[0]!=cutoff:raise ValueError('Fixed archive cutoff changed')
    db.execute("INSERT OR IGNORE INTO meta VALUES('cutoff',?)",(cutoff,))
    counts,seen=old_niches(work_root)
    for niche in NICHES:
        count=counts.get(niche,0)
        for page in range(1,max(1,math.ceil(count/30))+1):
            db.execute('INSERT OR IGNORE INTO pages(niche,page,prior_seen,site_count)VALUES(?,?,?,?)',
                       (niche,page,int((niche,page)in seen),count))
    if not db.execute("SELECT 1 FROM meta WHERE key='known_seeded'").fetchone():
        from mirror_ads import archive_ids
        ids=archive_ids(repo_root)
        with sqlite3.connect('file:'+str(spy_checkpoint)+'?mode=ro',uri=True) as spy:
            ids.update(row[0] for row in spy.execute('SELECT id FROM records'))
        for host in MIRRORS:
            path=work_root/host/'checkpoint.sqlite3'
            with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as old:
                ids.update(row[0] for row in old.execute('SELECT id FROM records'))
        for path in (Path('/workspace/work/gap-production/checkpoint.sqlite3'),
                     Path('/workspace/work/gap-analytics-production/checkpoint.sqlite3'),
                     *(Path('/workspace/work/gap-workers')/host/'checkpoint.sqlite3' for host in MIRRORS[2:])):
            if path.exists():
                with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as old:
                    ids.update(row[0] for row in old.execute('SELECT id FROM records'))
        db.executemany('INSERT OR IGNORE INTO known_ids VALUES(?)',((x,)for x in ids))
        db.execute("INSERT OR REPLACE INTO meta VALUES('base_known_count',?)",(str(len(ids)),))
        db.execute("INSERT OR REPLACE INTO meta VALUES('known_seeded','1')")
    db.commit()


def claim(db,host):
    db.execute('BEGIN IMMEDIATE')
    db.execute("UPDATE pages SET status='pending',owner=NULL,token=NULL,claimed_at=NULL "
               "WHERE status='claimed' AND claimed_at<?",(time.time()-180,))
    row=db.execute("SELECT niche,page FROM pages WHERE status='pending' "
                   "ORDER BY prior_seen ASC,page ASC,niche ASC LIMIT 1").fetchone()
    if not row:db.commit();return None
    token=uuid4().hex
    db.execute("UPDATE pages SET status='claimed',owner=?,token=?,claimed_at=? WHERE niche=? AND page=?",
               (host,token,time.time(),*row))
    db.commit();return (*row,token)


def release(db,niche,page,token,error):
    db.execute("UPDATE pages SET status='pending',owner=NULL,token=NULL,claimed_at=NULL,error=? "
               "WHERE niche=? AND page=? AND token=?",(error,niche,page,token))
    db.commit()


def card_rows(doc,url,niche,cutoff):
    item_names={};positions={};count=None
    for item in doc.jsonld():
        if item.get('@type')=='CollectionPage':
            main=item.get('mainEntity') or {}
            if isinstance(main,dict):
                raw=main.get('numberOfItems')
                if str(raw).replace(',','').isdigit():count=int(str(raw).replace(',',''))
                for element in main.get('itemListElement') or []:
                    p=urlsplit(str(element.get('url')or''))
                    match=AD.fullmatch(p.path)
                    if match:
                        item_names[match[1]]=str(element.get('name')or'').strip()
                        positions[match[1]]=element.get('position')
    cutoff_dt=datetime.fromisoformat(cutoff.replace('Z','+00:00'))
    records={};missing=[];future=[]
    for li in (n for n in doc.root.nodes() if n.tag=='li'):
        link=next((n for n in li.nodes() if n.tag=='a' and AD.fullmatch(n.attrs.get('href',''))),None)
        if not link:continue
        ad_id=AD.fullmatch(link.attrs['href'])[1]
        if item_names and ad_id not in item_names:continue
        title=next((n.text().strip() for n in li.nodes() if n.tag=='p' and 'font-bold' in n.attrs.get('class','')),'') or item_names.get(ad_id,'')
        content=next((n.text().strip() for n in li.nodes() if 'line-clamp-4' in n.attrs.get('class','')),'')
        time_node=next((n for n in li.nodes() if n.tag=='time' and n.attrs.get('datetime')),None)
        first=time_node.attrs['datetime'] if time_node else None
        if not first:
            created=cuid_time(ad_id)
            first=created.isoformat() if created else None
        try:first_dt=datetime.fromisoformat(first.replace('Z','+00:00')) if first else None
        except ValueError:first_dt=None
        if not first_dt or not first_dt.tzinfo:
            missing.append(ad_id);continue
        if first_dt>cutoff_dt:
            future.append(ad_id);continue
        image=next((n.attrs.get('src') for n in li.nodes() if n.tag=='img' and n.attrs.get('src')),None)
        record={'id':ad_id,'title':title,'text':content,'niche':niche,'geo':'IR','lang':None,
                'advertiser':'','cta_url':'','cta_text':'','first_seen':first,'last_seen':None,
                'media_src':urljoin(url,image) if image else None,'source':'html-archive',
                'source_url':urljoin(url,'/ads/'+ad_id),'listing_source_url':url,
                'archive_cutoff':cutoff,'collected_at':now(),'record_detail':'listing',
                'details':{'record_detail':'listing','list_position':positions.get(ad_id)}}
        records[ad_id]=record
    return records,count,missing,future


def finish_page(db,host,niche,page,token,records,count,missing,future):
    db.execute('BEGIN IMMEDIATE')
    row=db.execute('SELECT status,token FROM pages WHERE niche=? AND page=?',(niche,page)).fetchone()
    if row!=('claimed',token):db.commit();return 0
    new=0
    for ad_id,record in records.items():
        if db.execute('SELECT 1 FROM known_ids WHERE id=?',(ad_id,)).fetchone():continue
        result=db.execute('INSERT OR IGNORE INTO records VALUES(?,?)',(ad_id,json.dumps(record,ensure_ascii=False)))
        if result.rowcount:new+=1
    for ad_id in missing:db.execute("INSERT OR IGNORE INTO enrichment VALUES(?,'missing_listing_date')",(ad_id,))
    if count is not None:
        for next_page in range(1,max(1,math.ceil(count/30))+1):
            db.execute('INSERT OR IGNORE INTO pages(niche,page,site_count)VALUES(?,?,?)',(niche,next_page,count))
    db.execute("UPDATE pages SET status='done',owner=NULL,token=NULL,claimed_at=NULL,site_count=COALESCE(?,site_count),rows=?,new_ads=?,error=NULL "
               "WHERE niche=? AND page=?",(count,len(records)+len(missing)+len(future),new,niche,page))
    db.execute('INSERT INTO requests VALUES(?,?,?,?,?)',(host,niche,page,200,now()))
    db.commit();return new


def run(host,work,repo_root,spy_checkpoint,max_pages=0):
    with sqlite3.connect('file:'+str(spy_checkpoint)+'?mode=ro',uri=True) as spy:
        cutoff=spy.execute("SELECT value FROM meta WHERE key='cutoff'").fetchone()[0]
    db=db_open(work/'checkpoint.sqlite3')
    seed(db,Path('/workspace/work/niche-workers'),repo_root,spy_checkpoint,cutoff)
    client=Client('https://'+host,4,resilient=True)
    client.initialize();done=0
    try:
        while not max_pages or done<max_pages:
            if budget_status(client.base)['pause_left_s']:break
            job=claim(db,host)
            if not job:break
            niche,page,token=job
            url=client.base+'/ads?'+urlencode({'geo':'IR','niche':niche,'page':page})
            try:
                doc=Document(client.get(url))
                records,count,missing,future=card_rows(doc,url,niche,cutoff)
                if not records and not missing and not future and (count is None or page<=math.ceil(count/30)):
                    raise ValueError('No ad cards on scheduled listing page')
                new=finish_page(db,host,niche,page,token,records,count,missing,future)
                done+=1
                print(json.dumps({'host':host,'niche':niche,'page':page,'cards':len(records)+len(missing)+len(future),'new':new}),flush=True)
            except (HTTPError,Challenge,HostBlocked) as exc:
                code=getattr(exc,'code',0)
                if code in (404,410):
                    db.execute("UPDATE pages SET status='review',owner=NULL,token=NULL,claimed_at=NULL,error=? "
                               "WHERE niche=? AND page=? AND token=?",('HTTP '+str(code),niche,page,token));db.commit()
                else:release(db,niche,page,token,type(exc).__name__)
                db.execute('INSERT INTO requests VALUES(?,?,?,?,?)',(host,niche,page,code,now()));db.commit()
                break
            except Exception as exc:
                db.execute("UPDATE pages SET status='review',owner=NULL,token=NULL,claimed_at=NULL,error=? "
                           "WHERE niche=? AND page=? AND token=?",(type(exc).__name__+': '+str(exc)[:120],niche,page,token))
                db.commit();break
    finally:db.close()


def publish(work,repo):
    lock=work/'publisher.lock';work.mkdir(parents=True,exist_ok=True)
    with lock.open('a+') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        db=db_open(work/'checkpoint.sqlite3')
        try:
            total=0
            while True:
                pending=db.execute('SELECT id,payload FROM records WHERE id NOT IN (SELECT id FROM delivered) ORDER BY id LIMIT 1000').fetchall()
                if not pending:break
                raw=''.join(payload+'\n' for _,payload in pending).encode()
                digest=hashlib.sha256(raw).hexdigest()[:20]
                name=f'ads-niche-listing-{digest}.jsonl.gz'
                out=work/'github-outbox';out.mkdir(exist_ok=True)
                path=out/name
                with path.open('wb') as f:
                    with gzip.GzipFile(fileobj=f,mode='wb',mtime=0,filename='') as gz:gz.write(raw)
                blob=gh(repo,'git/blobs',{'content':base64.b64encode(path.read_bytes()).decode(),'encoding':'base64'})
                entry={'path':'data/html-archive-import/'+name,'mode':'100644','type':'blob','sha':blob['sha']}
                for attempt in range(8):
                    head=gh(repo,'commits/main')
                    tree=gh(repo,'git/trees',{'base_tree':head['commit']['tree']['sha'],'tree':[entry]})
                    commit=gh(repo,'git/commits',{'message':f'Import {len(pending)} Iran listing ads','tree':tree['sha'],'parents':[head['sha']]})
                    try:gh(repo,'git/refs/heads/main',{'sha':commit['sha'],'force':False});break
                    except Exception:
                        if attempt==7:raise
                        time.sleep(2)
                db.executemany('INSERT OR IGNORE INTO delivered VALUES(?)',((ad_id,)for ad_id,_ in pending));db.commit()
                total+=len(pending)
            return total
        finally:db.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--host',choices=HOSTS)
    p.add_argument('--work-dir',type=Path,default=DEFAULT_WORK)
    p.add_argument('--repo-root',type=Path,default=ROOT)
    p.add_argument('--spy-checkpoint',type=Path,default=Path('/workspace/work/archive-production/archive-work/checkpoint.sqlite3'))
    p.add_argument('--repo');p.add_argument('--max-pages',type=int,default=0)
    p.add_argument('--publish-only',action='store_true')
    a=p.parse_args()
    if a.publish_only:
        if not a.repo:p.error('--publish-only requires --repo')
        print('published_rows',publish(a.work_dir,a.repo));return
    if not a.host:p.error('--host required for worker mode')
    run(a.host,a.work_dir,a.repo_root,a.spy_checkpoint,a.max_pages)

if __name__=='__main__':main()
