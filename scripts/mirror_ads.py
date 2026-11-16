#!/usr/bin/env python3
"""Checkpointed oldest-first mirror ad collector; no source API."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import glob
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from urllib.error import HTTPError

import archive_html as archive
from archive_collect import gh
from archive_yield import cuid_time
from ad_claims import Claims
from html_core import Client, Document
from source_budget import Challenge, HostBlocked, status as budget_status

BASE = archive.MIRROR_BASE
ORDER = 'ASC'


def archive_ids(root):
    out = set()
    for path in glob.glob(str(root / 'data/ads/*.json')):
        out.update(row['id'] for row in json.loads(Path(path).read_text(encoding='utf-8')) if row.get('id'))
    for path in (root/'data/html-archive-import').glob('ads-*.jsonl.gz'):
        with gzip.open(path,'rt',encoding='utf-8') as handle:
            out.update(row['id'] for line in handle if line.strip() for row in [json.loads(line)] if row.get('id'))
    return out


@contextmanager
def open_spy(path):
    db=sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True)
    try:yield db
    finally:db.close()


def connect(work, cutoff):
    work.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(work / 'checkpoint.sqlite3')
    db.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,month TEXT,status TEXT DEFAULT 'pending',error TEXT);
    CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,payload TEXT);
    CREATE TABLE IF NOT EXISTS requests(id TEXT,at TEXT,status INTEGER);''')
    previous = db.execute("SELECT value FROM meta WHERE key='cutoff'").fetchone()
    if previous and previous[0] != cutoff:
        raise ValueError('Mirror and tgadsspy cutoffs differ')
    if not previous:
        db.execute("INSERT INTO meta VALUES('cutoff',?)", (cutoff,))
        db.commit()
    return db


def seed(db, spy_path, root):
    """Refresh candidates from the live spy checkpoint read-only at every slice."""
    with open_spy(spy_path) as spy:
        excluded = archive_ids(root)
        excluded.update(row[0] for row in spy.execute('SELECT id FROM records'))
        excluded.update(row[0].rsplit('/',1)[-1] for row in spy.execute("SELECT url FROM queue WHERE kind='detail' AND status!='pending'"))
        cursor_row=db.execute("SELECT value FROM meta WHERE key='spy_queue_rowid'").fetchone()
        cursor=int(cursor_row[0]) if cursor_row else 0
        for rowid,url in spy.execute("SELECT rowid,url FROM queue WHERE rowid>? AND kind='detail' AND status='pending' AND month>='2026-04' ORDER BY rowid",(cursor,)):
            ad_id = url.rsplit('/',1)[-1]
            created = cuid_time(ad_id)
            if ad_id not in excluded and created and created.strftime('%Y-%m') >= '2026-04':
                db.execute('INSERT OR IGNORE INTO jobs(id,month) VALUES(?,?)', (ad_id,created.strftime('%Y-%m')))
        db.execute("INSERT OR REPLACE INTO meta VALUES('spy_queue_rowid',?)", (str(spy.execute('SELECT COALESCE(MAX(rowid),0) FROM queue').fetchone()[0]),))
        db.executemany("UPDATE jobs SET status='spy_done' WHERE id=? AND status='pending'", ((ad_id,) for ad_id in excluded))
        db.commit()


def spy_busy(spy_path, ad_id):
    with open_spy(spy_path) as spy:
        url = archive.BASE + '/ads/' + ad_id
        row = spy.execute('SELECT status FROM queue WHERE url=?', (url,)).fetchone()
        active = spy.execute('SELECT 1 FROM source_requests WHERE url=? AND status=0 AND at>=? LIMIT 1', (url,(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat())).fetchone()
        done = spy.execute('SELECT 1 FROM records WHERE id=?', (ad_id,)).fetchone()
    return not row or row[0] != 'pending' or bool(active) or bool(done)


def crawl_slice(db, client, claims, spy_path, root, pages):
    seed(db, spy_path, root)
    saved_ids=archive_ids(root)
    cutoff = db.execute("SELECT value FROM meta WHERE key='cutoff'").fetchone()[0]
    fetched = 0
    for ad_id, month in db.execute("SELECT id,month FROM jobs WHERE status='pending' ORDER BY id " + ORDER + " LIMIT ?", (pages,)).fetchall():
        if spy_busy(spy_path, ad_id) or ad_id in saved_ids:
            db.execute("UPDATE jobs SET status='spy_done' WHERE id=?", (ad_id,));db.commit();continue
        if claims.acquire(ad_id, client.base) != 'acquired':
            continue
        url = BASE + '/ads/' + ad_id
        verified = False
        try:
            body = client.get(url)
            fetched += 1
            db.execute('INSERT INTO requests VALUES(?,?,200)', (ad_id,datetime.now(timezone.utc).isoformat()))
            doc = Document(body)
            works = [x for x in doc.jsonld() if x.get('@type') == 'CreativeWork' and x.get('identifier') == ad_id]
            if not works:
                raise ValueError('Mirror JSON-LD lacks matching ad ID')
            if not works[0].get('contentLocation'):
                db.execute("UPDATE jobs SET status='unverified',error='Missing contentLocation' WHERE id=?", (ad_id,))
                db.commit()
                verified = 'unverified'
                continue
            record, result = archive.extract(doc,url,cutoff)
            verified = result in ('iran','not_iran','after_cutoff')
            if record:
                record['html_sha256'] = hashlib.sha256(body.encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO records VALUES(?,?)', (ad_id,json.dumps(record,ensure_ascii=False)))
            db.execute('UPDATE jobs SET status=?,error=NULL WHERE id=?', (result,ad_id));db.commit()
        except (HTTPError,Challenge,HostBlocked) as exc:
            code = exc.code if isinstance(exc,HTTPError) else 0
            db.execute('INSERT INTO requests VALUES(?,?,?)', (ad_id,datetime.now(timezone.utc).isoformat(),code))
            db.execute('UPDATE jobs SET error=? WHERE id=?', (f'{type(exc).__name__}: {str(exc)[:100]}',ad_id))
            if code in (403,429) or isinstance(exc,(Challenge,HostBlocked)):
                db.execute("INSERT OR REPLACE INTO meta VALUES('stop_reason',?)", (f'Access hold: {type(exc).__name__} {code}',))
            db.commit();break
        except Exception as exc:
            db.execute('UPDATE jobs SET status=?,error=? WHERE id=?', ('failed',f'{type(exc).__name__}: {str(exc)[:100]}',ad_id))
            db.commit();break
        finally:
            claims.finish(ad_id,client.base,verified)
    return fetched


def export(db, work):
    output = work / 'exports' / ('ads-'+BASE.split('//')[1]+'-00001.jsonl.gz')
    output.parent.mkdir(parents=True,exist_ok=True)
    tmp = output.with_suffix('.tmp')
    with gzip.open(tmp,'wt',encoding='utf-8') as handle:
        for payload, in db.execute('SELECT payload FROM records ORDER BY id'):
            handle.write(payload + '\n')
    tmp.replace(output)


def publish(db, work, repo):
    """Deliver only new verified Iran rows through the existing import directory."""
    db.execute('CREATE TABLE IF NOT EXISTS delivered(id TEXT PRIMARY KEY)')
    pending=db.execute('SELECT id,payload FROM records WHERE id NOT IN (SELECT id FROM delivered) ORDER BY id').fetchall()
    if not pending:
        return 0
    out=work/'github-outbox';out.mkdir(exist_ok=True)
    name='ads-'+BASE.split('//')[1]+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.jsonl.gz'
    path=out/name
    with gzip.open(path,'wt',encoding='utf-8') as handle:
        for _,payload in pending:handle.write(payload+'\n')
    blob=gh(repo,'git/blobs',{'content':base64.b64encode(path.read_bytes()).decode(),'encoding':'base64'})
    entries=[{'path':'data/html-archive-import/'+name,'mode':'100644','type':'blob','sha':blob['sha']}]
    for attempt in range(4):
        head=gh(repo,'commits/main')
        tree=gh(repo,'git/trees',{'base_tree':head['commit']['tree']['sha'],'tree':entries})
        commit=gh(repo,'git/commits',{'message':f'Import {len(pending)} Iran HTML ads from mirror','tree':tree['sha'],'parents':[head['sha']]})
        try:
            gh(repo,'git/refs/heads/main',{'sha':commit['sha'],'force':False})
            break
        except Exception:
            if attempt==3:raise
            time.sleep(2)
    db.executemany('INSERT OR IGNORE INTO delivered VALUES(?)',((ad_id,) for ad_id,_ in pending))
    db.commit()
    print('Published mirror Iran ads',len(pending),'commit',commit['sha'],flush=True)
    return len(pending)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work-dir',type=Path,required=True)
    p.add_argument('--spy-checkpoint',type=Path,required=True)
    p.add_argument('--repo-root',type=Path,default=archive.ROOT)
    p.add_argument('--repo',required=True)
    p.add_argument('--pages-per-slice',type=int,default=100)
    p.add_argument('--max-slices',type=int,default=1)
    p.add_argument('--host',choices=('telegramadsarchive.com','telegramadsanalytics.com'),default='telegramadsarchive.com')
    a=p.parse_args()
    global BASE,ORDER
    BASE='https://'+a.host
    ORDER='ASC' if a.host=='telegramadsarchive.com' else 'DESC'
    if a.pages_per_slice<1 or a.max_slices<0:p.error('Invalid slice size')
    if not a.spy_checkpoint.exists():p.error('tgadsspy checkpoint missing')
    lock=archive.source_lock(a.work_dir)
    with open_spy(a.spy_checkpoint) as spy:
        cutoff=spy.execute("SELECT value FROM meta WHERE key='cutoff'").fetchone()[0]
    db=connect(a.work_dir,cutoff)
    claims=Claims()
    try:
        old=db.execute("SELECT value FROM meta WHERE key='stop_reason' AND value!=''").fetchone()
        if old and old[0]=='Mirror JSON-LD omits contentLocation; Iran cannot be verified':
            db.execute("DELETE FROM meta WHERE key='stop_reason'");db.commit()
            old=None
        if old:
            raise SystemExit('Mirror stop reason retained; no request sent')
        client=Client(BASE,4,resilient=True)
        client.initialize()
        for index in range(a.max_slices or 10**12):
            if budget_status(BASE)['halted']:break
            count=crawl_slice(db,client,claims,a.spy_checkpoint,a.repo_root,a.pages_per_slice)
            export(db,a.work_dir)
            publish(db,a.work_dir,a.repo)
            state=dict(db.execute('SELECT status,COUNT(*) FROM jobs GROUP BY status'))
            print(json.dumps({'slice':index+1,'requests':count,'jobs':state},ensure_ascii=False),flush=True)
            if not count or db.execute("SELECT value FROM meta WHERE key='stop_reason' AND value!=''").fetchone():break
    finally:
        claims.close();db.close();lock.close()

if __name__=='__main__':main()
