#!/usr/bin/env python3
"""Run a finite, resumable historical collection on an executor with public-page access.

Uses gh only to deliver immutable collected-data parts to the existing Actions pipeline.
No source API, upload-host secret, schedule, or repeated completed-page crawl.
"""
import argparse
import base64
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

import archive_html as archive
import archive_yield


def gh(repo,path,payload=None):
    command=['gh','api',f'repos/{repo}/{path}']
    file=None
    try:
        if payload is not None:
            file=tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',suffix='.json',delete=False)
            json.dump(payload,file,ensure_ascii=False)
            file.close()
            command+=['--method','PATCH' if path.startswith('git/refs/') else 'POST','--input',file.name]
        result=subprocess.run(command,capture_output=True,text=True,check=True)
        return json.loads(result.stdout)
    finally:
        if file:
            Path(file.name).unlink(missing_ok=True)


def publish(db,work,repo,seed_dir):
    db.execute('CREATE TABLE IF NOT EXISTS github_delivered(id TEXT PRIMARY KEY)')
    # A previously committed initial batch must not be sent a second time.
    if not db.execute('SELECT 1 FROM github_delivered LIMIT 1').fetchone():
        for path in seed_dir.glob('ads-*.jsonl.gz'):
            with gzip.open(path,'rt',encoding='utf-8') as handle:
                for line in handle:
                    row=json.loads(line)
                    db.execute('INSERT OR IGNORE INTO github_delivered VALUES(?)',(row['id'],))
        db.commit()
    pending=db.execute('SELECT r.id,r.payload FROM records r WHERE NOT EXISTS (SELECT 1 FROM github_delivered d WHERE d.id=r.id) ORDER BY r.id').fetchall()
    if not pending:
        return 0
    batch_dir=work/'github-outbox'
    batch_dir.mkdir(exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    parts=[]
    rows,size=[],0
    def part():
        if not rows:
            return
        path=batch_dir/f'ads-cloud-{stamp}-{len(parts)+1:04d}.jsonl.gz'
        with path.open('wb') as raw, gzip.GzipFile(fileobj=raw,mode='wb',mtime=0,filename='') as handle:
            for _,payload in rows:
                handle.write((payload+'\n').encode())
        parts.append(path)
    for aid,payload in pending:
        n=len(payload.encode())
        if rows and size+n>4_000_000:
            part();rows,size=[],0
        rows.append((aid,payload));size+=n
    part()
    entries=[]
    for path in parts:
        blob=gh(repo,'git/blobs',{'content':base64.b64encode(path.read_bytes()).decode(),'encoding':'base64'})
        entries.append({'path':'data/html-archive-import/'+path.name,'mode':'100644','type':'blob','sha':blob['sha']})
    report=archive.report(db)
    entries.append({'path':'data/html-archive-import/collection-report.json','mode':'100644','type':'blob','content':json.dumps(report,ensure_ascii=False,indent=2)})
    for attempt in range(4):
        head=gh(repo,'commits/main')
        tree=gh(repo,'git/trees',{'base_tree':head['commit']['tree']['sha'],'tree':entries})
        commit=gh(repo,'git/commits',{'message':f'Import {len(pending)} more historical Iran HTML ads','tree':tree['sha'],'parents':[head['sha']]})
        try:
            gh(repo,'git/refs/heads/main',{'sha':commit['sha'],'force':False})
            break
        except subprocess.CalledProcessError:
            if attempt==3:
                raise
            time.sleep(2)
    db.executemany('INSERT OR IGNORE INTO github_delivered VALUES(?)',[(aid,) for aid,_ in pending])
    db.commit()
    print(f'Published {len(pending)} newly collected ads to GitHub; commit {commit["sha"]}',flush=True)
    return len(pending)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir',type=Path,default=archive.ROOT/'archive-work')
    parser.add_argument('--repo',required=True,help='Authorized owner/repository; gh must already be authenticated')
    parser.add_argument('--pages-per-slice',type=int,default=1000)
    parser.add_argument('--max-slices',type=int,default=1,help='Zero continues the same fixed-cutoff job until its discovered queue is exhausted or access fails')
    parser.add_argument('--channel-import-dir',type=Path,action='append',default=[],help='Additional live channel export directories; read at each slice boundary')
    parser.add_argument('--seed-dir',type=Path,default=archive.ROOT/'data/html-archive-import')
    args=parser.parse_args()
    if args.pages_per_slice<1 or args.max_slices<0:
        parser.error('Invalid budgets')
    # Do not pass an API key or upload-host token to the source collector.
    db=archive.connect(args.work_dir,seed_known=True)
    try:
        publish(db,args.work_dir,args.repo,args.seed_dir)
        index=0
        while args.max_slices==0 or index<args.max_slices:
            archive_yield.seed_from_channel_pages(db,archive.enqueue)
            for directory in args.channel_import_dir:
                archive_yield.seed_from_channel_pages(db,archive.enqueue,directory)
            result=subprocess.run([sys.executable,str(Path(__file__).with_name('archive_html.py')),
                                   '--work-dir',str(args.work_dir),'--max-pages',str(args.pages_per_slice),'--max-seconds','5400'])
            publish(db,args.work_dir,args.repo,args.seed_dir)
            index+=1
            info=archive.report(db)
            strategy=next((v for v in archive_yield.coverage_report(db)['by_strategy']if v['via']=='persian_channel_page'),None)
            yield_report={'slice':index,'at':datetime.now(timezone.utc).isoformat(),'strategy':strategy}
            yield_report['related_strategies']=[v for v in archive_yield.coverage_report(db)['by_strategy']if v['via'].startswith('persian_channel_')]
            print('Channel-page yield: '+json.dumps(yield_report),flush=True)
            target=args.work_dir/'persian-channel-yield.json'
            tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(yield_report,indent=2));tmp.replace(target)
            print(json.dumps({'slice':index,'iran_ads':info['iran_ads_extracted'],'queue':info['queue_status']},ensure_ascii=False),flush=True)
            if result.returncode:
                raise SystemExit(result.returncode)
            if not info['queue_status'].get('pending',0):
                print('Discovered queue exhausted. Review coverage and failed/blocked pages; completeness is not assumed.',flush=True)
                break
    finally:
        db.close()

if __name__=='__main__':
    main()
