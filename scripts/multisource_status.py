#!/usr/bin/env python3
"""Read-only progress report for mirror ads, channel slices, and other workers."""
import argparse,base64,gzip,json,sqlite3,sys,time
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from source_budget import status as budget_status
from archive_collect import gh

HOSTS=('telegramadsarchive.com','telegramadsanalytics.com','telegramads.it',
       'tgads.biz','tgads.observer','telegramstats.net')

def rows(path,sql):
    try:
        with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)as db:return db.execute(sql).fetchall()
    except (sqlite3.Error,OSError):return []

def ids_ads(root):
    out=set()
    for p in (root/'data/ads').glob('*.json'):
        try:out.update(x['id']for x in json.loads(p.read_text())if x.get('id'))
        except (OSError,ValueError):pass
    for p in (root/'data/html-archive-import').glob('ads-*.jsonl.gz'):
        try:
            with gzip.open(p,'rt')as f:out.update(x['id']for line in f if line.strip()for x in [json.loads(line)]if x.get('id'))
        except (OSError,ValueError):pass
    return out

def snapshot(root):
    baseline=ids_ads(root);niche=set();parts=[]
    for host in HOSTS:
        db=Path('/workspace/work/niche-workers')/host/'checkpoint.sqlite3'
        counts=dict(rows(db,'SELECT status,COUNT(*) FROM niches GROUP BY status'))
        found={r[0]for r in rows(db,'SELECT id FROM records')};niche|=found
        pages=rows(db,"SELECT COUNT(*) FROM requests WHERE kind='listing'")
        hold=rows(db,"SELECT value FROM meta WHERE key='stop_reason'")
        parts.append((host,counts.get('done',0),sum(counts.values()),len(found),pages[0][0]if pages else 0,hold[0][0]if hold else ''))
    gapdb=Path('/workspace/work/gap-production/checkpoint.sqlite3')
    gapids={r[0]for r in rows(gapdb,'SELECT id FROM records')}
    method=rows(gapdb,"SELECT j.kind,COUNT(DISTINCT CASE WHEN j.status='done' THEN j.key END),COUNT(r.id) FROM jobs j LEFT JOIN candidates c ON c.key=j.key LEFT JOIN records r ON r.id=c.id GROUP BY j.kind")
    spy=Path('/workspace/work/tgstat-production/tgstat.sqlite3')
    quick=rows(spy,"SELECT COUNT(*),COALESCE(SUM(channels),0),COALESCE(SUM(groups),0) FROM quick_jobs WHERE status='done'")
    quick_today=rows(spy,"SELECT COUNT(*) FROM quick_requests WHERE day='"+datetime.now(timezone.utc).date().isoformat()+"'")
    quickhold=rows(spy,"SELECT value FROM meta WHERE key='quick_search_stop_reason'")
    tgstat_types=rows(spy,"SELECT json_extract(payload,'$.type'),COUNT(*) FROM records GROUP BY 1")
    tgstat_hold=rows(spy,"SELECT value FROM meta WHERE key='stop_reason'")
    lyzem=Path('/workspace/work/lyzem-production/checkpoint.sqlite3')
    lyzem_total=rows(lyzem,'SELECT COUNT(*) FROM records')
    lyzem_hold=rows(lyzem,"SELECT value FROM meta WHERE key='stop_reason'")
    return locals()

def render(s):
    base=s['baseline'];niche=s['niche'];gapids=s['gapids']
    start=len(base);after_niche=len(base|niche);after_gap=len(base|niche|gapids)
    lines=['# Collection status',
       '',datetime.now(timezone.utc).isoformat()+' UTC. Checkpoint counts; the 57,492 Iran listing total can include ads after the archive cutoff.',
       '', '## A — niche listings', '',
       '| Mirror | Niches done / assigned | Iran ads saved | Listing pages | Hold |',
       '|---|---:|---:|---:|---|']
    for host,done,total,ads,pages,hold in s['parts']:
        lines.append(f'| {host} | {done}/{total} | {ads} | {pages} | {hold or "—"} |')
    lines += ['',f'Unique new niche records beyond local baseline: {after_niche-start:,}.',
       '', '## B — other filters and Persian text search', '',
       '| Method | Jobs done | Iran ads saved (attributed) | Gap to 57,492 after method |',
       '|---|---:|---:|---:|']
    for kind,done,saved in s['method']:
        lines.append(f'| {kind} | {done} | {saved} | {max(0,57492-after_gap):,} after all current methods |')
    lines += ['',f'Baseline unique IDs: {start:,}; after A: {after_niche:,}; after A+B: {after_gap:,}. Gap is a comparison, not a completeness proof.',
       '', '## C — Persian channel member ranges', '',
       '| Slices done | Sum of proven slice counts / 547,983 | New usernames | Status |',
       '|---:|---:|---:|---|',
       '| 0 | 0 / 547,983 | 0 | Held: mirror pages ignore minMembers/maxMembers; tgadsspy is reserved for ads. |',
       '', '## D — existing channel searches', '',
       '| Worker | Saved / completed | Hold |', '|---|---:|---|',
       f"| Lyzem | {s['lyzem_total'][0][0] if s['lyzem_total'] else 0} records | {s['lyzem_hold'][0][0] if s['lyzem_hold'] else '—'} |",
       f"| TGStat catalog | {dict(s['tgstat_types']).get('channel',0)} channels, {dict(s['tgstat_types']).get('group',0)} groups | {s['tgstat_hold'][0][0] if s['tgstat_hold'] else '—'} |",
       f"| TGStat Quick | {s['quick'][0][1] if s['quick'] else 0} channels, {s['quick'][0][2] if s['quick'] else 0} groups; {s['quick'][0][0] if s['quick'] else 0} terms | {s['quickhold'][0][0] if s['quickhold'] else ('UTC daily cap 300/300' if s['quick_today'] and s['quick_today'][0][0]>=300 else '—')} |",
       '| TGList | 0 new requests | HTTP 403 hold retained |',
       '', '## Host holds and pacing', '',
       '| Host | Delay | Pause left | Halt |', '|---|---:|---:|---|']
    for host in (*HOSTS,'lyzem.com','tgstat.com','tgadsspy.com'):
        v=budget_status('https://'+host)
        lines.append(f"| {host} | {v['delay_s']:.1f}s | {v['pause_left_s']:.0f}s | {v['halted']} |")
    return '\n'.join(lines)+'\n'

def publish(path,repo):
    data=path.read_bytes();blob=gh(repo,'git/blobs',{'content':base64.b64encode(data).decode(),'encoding':'base64'})
    entry={'path':'reports/collection-status.md','mode':'100644','type':'blob','sha':blob['sha']}
    for attempt in range(8):
        head=gh(repo,'commits/main')
        tree=gh(repo,'git/trees',{'base_tree':head['commit']['tree']['sha'],'tree':[entry]})
        commit=gh(repo,'git/commits',{'message':'Update collection status','tree':tree['sha'],'parents':[head['sha']]})
        try:gh(repo,'git/refs/heads/main',{'sha':commit['sha'],'force':False});return commit['sha']
        except Exception:
            if attempt==7:raise
            time.sleep(2)

def main():
    p=argparse.ArgumentParser();p.add_argument('--repo-root',type=Path,required=True)
    p.add_argument('--repo',required=True);p.add_argument('--publish',action='store_true');a=p.parse_args()
    target=a.repo_root/'reports/collection-status.md';target.parent.mkdir(exist_ok=True)
    target.write_text(render(snapshot(a.repo_root)))
    if a.publish:print('published',publish(target,a.repo))
    else:print(target)

if __name__=='__main__':main()
