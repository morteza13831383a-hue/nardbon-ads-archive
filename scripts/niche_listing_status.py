#!/usr/bin/env python3
"""Three-line, six-hour status for the listing-first ad sweep."""
import argparse
import base64
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import time
from archive_collect import gh
from niche_listing_sweep import HOSTS
from source_budget import status as budget_status

ROOT=Path(__file__).resolve().parents[1]

def build(work,config_path=None):
    with sqlite3.connect('file:'+str(work/'checkpoint.sqlite3')+'?mode=ro',uri=True)as db:
        known={row[0] for row in db.execute('SELECT id FROM known_ids')}
        collected={row[0] for row in db.execute('SELECT id FROM records')}
        base=int(db.execute("SELECT value FROM meta WHERE key='base_known_count'").fetchone()[0])
        pages=db.execute("SELECT status,COUNT(*) FROM pages GROUP BY status").fetchall()
    config_path=config_path or work.parent/'final-collection/runtime.json'
    gap_pending=gap_review=0
    if config_path.exists():
        config=json.loads(config_path.read_text())
        for worker in config.get('mirror_gap_workers',[]):
            checkpoint=Path(worker['work_dir'])/'checkpoint.sqlite3'
            if not checkpoint.exists():continue
            with sqlite3.connect('file:'+str(checkpoint)+'?mode=ro',uri=True) as db:
                collected.update(row[0] for row in db.execute('SELECT id FROM records'))
                counts=dict(db.execute('SELECT status,COUNT(*) FROM jobs GROUP BY status'))
                gap_pending+=counts.get('pending',0)
                gap_review+=counts.get('review',0)
    ads=len(collected-known)
    prior_path=work/'status-state.json'
    prior=json.loads(prior_path.read_text()) if prior_path.exists() else {}
    new=max(0,ads-int(prior.get('ads',0)))
    paused=[]
    for host in HOSTS:
        budget=budget_status('https://'+host)
        if budget['pause_left_s']:
            paused.append(host+':'+str(round(budget['pause_left_s']/60))+'m')
    review=dict(pages).get('review',0)+gap_review
    stamp=datetime.now(timezone.utc).isoformat()
    body=(f'New Iran ads since prior report: {new:,}; new unique total: {ads:,}; at {stamp}.\n'
          f'Numerical gap to 57,492: {max(0,57492-base-ads):,}; niche pages: '+', '.join(f'{k} {v:,}'for k,v in pages)+f'; gap jobs pending {gap_pending:,}.\n'
          f'Blocks/cooldowns: {", ".join(paused) if paused else "none"}; review jobs/pages: {review}.\n')
    return body,{'ads':ads,'at':stamp}

def publish(path,repo):
    blob=gh(repo,'git/blobs',{'content':base64.b64encode(path.read_bytes()).decode(),'encoding':'base64'})
    entry={'path':'reports/collection-status.md','mode':'100644','type':'blob','sha':blob['sha']}
    for attempt in range(8):
        head=gh(repo,'commits/main')
        tree=gh(repo,'git/trees',{'base_tree':head['commit']['tree']['sha'],'tree':[entry]})
        commit=gh(repo,'git/commits',{'message':'Update six-hour ad collection status','tree':tree['sha'],'parents':[head['sha']]})
        try:gh(repo,'git/refs/heads/main',{'sha':commit['sha'],'force':False});return commit['sha']
        except Exception:
            if attempt==7:raise
            time.sleep(2)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--work-dir',type=Path,required=True)
    p.add_argument('--repo',required=True)
    p.add_argument('--output',type=Path,default=ROOT/'reports/collection-status.md')
    p.add_argument('--publish',action='store_true')
    a=p.parse_args()
    body,state=build(a.work_dir)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(body,encoding='utf-8')
    if a.publish:
        print('published',publish(a.output,a.repo))
        tmp=a.work_dir/'status-state.tmp';tmp.write_text(json.dumps(state));tmp.replace(a.work_dir/'status-state.json')
    else:print(body,end='')
if __name__=='__main__':main()
