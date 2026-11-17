#!/usr/bin/env python3
"""Read-only progress report for mirror ads, channel slices, and other workers."""
import argparse,base64,gzip,json,re,sqlite3,sys,time
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
    rollout_path=root/'reports/max-output-baseline.json'
    rollout=json.loads(rollout_path.read_text()) if rollout_path.exists() else None
    for host in HOSTS:
        db=Path('/workspace/work/niche-workers')/host/'checkpoint.sqlite3'
        counts=dict(rows(db,'SELECT status,COUNT(*) FROM niches GROUP BY status'))
        found={r[0]for r in rows(db,'SELECT id FROM records')};niche|=found
        pages=rows(db,"SELECT COUNT(*) FROM requests WHERE kind='listing'")
        hold=rows(db,"SELECT value FROM meta WHERE key='stop_reason'")
        parts.append((host,counts.get('done',0),sum(v for k,v in counts.items() if k!='transferred'),
                      len(found),pages[0][0]if pages else 0,hold[0][0]if hold else ''))
    gapids=set();done_keys={'facet':set(),'keyword':set()};saved_ids={'facet':set(),'keyword':set()}
    gap_paths=[Path('/workspace/work/gap-production/checkpoint.sqlite3'),
               Path('/workspace/work/gap-analytics-production/checkpoint.sqlite3')]
    gap_paths.extend(Path('/workspace/work/gap-workers')/host/'checkpoint.sqlite3' for host in HOSTS[2:])
    for gapdb in gap_paths:
        gapids.update(r[0]for r in rows(gapdb,'SELECT id FROM records'))
        for kind,key in rows(gapdb,"SELECT kind,key FROM jobs WHERE status='done'"):
            done_keys.setdefault(kind,set()).add(key)
        for kind,ad_id in rows(gapdb,"SELECT j.kind,r.id FROM jobs j JOIN candidates c ON c.key=j.key JOIN records r ON r.id=c.id"):
            saved_ids.setdefault(kind,set()).add(ad_id)
    method=[(kind,len(done_keys[kind]),len(saved_ids[kind]))for kind in ('facet','keyword')]
    spy=Path('/workspace/work/tgstat-production/tgstat.sqlite3')
    quick=rows(spy,"SELECT COUNT(*),COALESCE(SUM(channels),0),COALESCE(SUM(groups),0) FROM quick_jobs WHERE status='done'")
    quick_today=rows(spy,"SELECT COUNT(*) FROM quick_requests WHERE day='"+datetime.now(timezone.utc).date().isoformat()+"'")
    quickhold=rows(spy,"SELECT value FROM meta WHERE key='quick_search_stop_reason'")
    tgstat_types=rows(spy,"SELECT json_extract(payload,'$.type'),COUNT(*) FROM records GROUP BY 1")
    tgstat_hold=rows(spy,"SELECT value FROM meta WHERE key='stop_reason'")
    tgstat_slices=dict(rows(spy,'SELECT status,COUNT(*) FROM tgstat_slices GROUP BY status'))
    spy_slices=dict(rows(Path('/workspace/work/channels-production/channels.sqlite3'),
                         'SELECT status,COUNT(*) FROM spy_slices GROUP BY status'))
    spy_channels=rows(Path('/workspace/work/channels-production/channels.sqlite3'),
                      'SELECT COUNT(*) FROM channels')
    old_report=Path('/workspace/work/collection-final/reports/collection-status.md')
    previous=old_report.read_text()if old_report.exists()else''
    rates={name:(match[1]if(match:=re.search(r'\| '+re.escape(name)+r' \| [^|]+ \| ([0-9.]+) \|',previous))else'—')
           for name in ('tgadsspy / channel','tgstat / group','tgstat / channel')}
    lyzem=Path('/workspace/work/lyzem-production/checkpoint.sqlite3')
    lyzem_total=rows(lyzem,'SELECT COUNT(*) FROM records')
    lyzem_hold=rows(lyzem,"SELECT value FROM meta WHERE key='stop_reason'")
    return locals()

def render(s):
    base=s['baseline'];niche=s['niche'];gapids=s['gapids']
    start=len(base);after_niche=len(base|niche);after_gap=len(base|niche|gapids)
    lines=['# Collection status',
       '',datetime.now(timezone.utc).isoformat()+' UTC. Checkpoint counts; the 57,492 Iran listing total can include ads after the archive cutoff.',
       '', 'Ads priority: facet and keyword workers wait until all niche pages and pending ad details finish. See [the 29-niche audit](niche-coverage.md).',
       '', '## A — niche listings', '',
       '| Mirror | Niches done / assigned | Iran ads saved | Listing pages | Hold |',
       '|---|---:|---:|---:|---|']
    for host,done,total,ads,pages,hold in s['parts']:
        lines.append(f'| {host} | {done}/{total} | {ads} | {pages} | {hold or "—"} |')
    lines += ['',f'Unique new niche records beyond local baseline: {after_niche-start:,}. Transferred slices count only under their destination.',
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
       '| Worker | Saved / completed | New / 100 requests since baseline | Hold |', '|---|---:|---:|---|',
       f"| tgadsspy channels | {s['spy_channels'][0][0] if s['spy_channels'] else 0} | {s['rates']['tgadsspy / channel']} | Paused: ad worker owns host budget |",
       f"| Lyzem | {s['lyzem_total'][0][0] if s['lyzem_total'] else 0} records | — | {s['lyzem_hold'][0][0] if s['lyzem_hold'] else '—'} |",
       f"| TGStat all channels | {dict(s['tgstat_types']).get('channel',0)} | {s['rates']['tgstat / channel']} | Catalog: {s['tgstat_hold'][0][0] if s['tgstat_hold'] else '—'} |",
       f"| TGStat all groups | {dict(s['tgstat_types']).get('group',0)} | {s['rates']['tgstat / group']} | Catalog: {s['tgstat_hold'][0][0] if s['tgstat_hold'] else '—'} |",
       f"| TGStat Quick | {s['quick'][0][1] if s['quick'] else 0} channels, {s['quick'][0][2] if s['quick'] else 0} groups; {s['quick'][0][0] if s['quick'] else 0} terms | — | {s['quickhold'][0][0] if s['quickhold'] else '—'} |",
       '| TGList | 0 new requests | — | HTTP 403 hold retained |',
       '',f"Remaining queued listing slices: tgadsspy {s['spy_slices'].get('pending',0)}, TGStat {s['tgstat_slices'].get('pending',0)}. Holds and catalog caps prevent a defensible completion date.",
       '', '## Host holds and pacing', '',
       '| Host | Delay | Pause left | Halt |', '|---|---:|---:|---|']
    for host in (*HOSTS,'lyzem.com','tgstat.com','tgadsspy.com'):
        v=budget_status('https://'+host)
        lines.append(f"| {host} | {v['delay_s']:.1f}s | {v['pause_left_s']:.0f}s | {v['halted']} |")
    if s['rollout']:
        b=s['rollout'];c=b['channels'];types=dict(s['tgstat_types']);q=s['quick'][0]if s['quick']else(0,0,0)
        changes=[('ads',after_gap-b['ads_unique']),
                 ('tgadsspy channels',(s['spy_channels'][0][0]if s['spy_channels']else 0)-c['tgadsspy']),
                 ('Lyzem', (s['lyzem_total'][0][0]if s['lyzem_total']else 0)-c['lyzem']),
                 ('TGStat all channels',types.get('channel',0)-c['tgstat_catalog_channel']),
                 ('TGStat all groups',types.get('group',0)-c['tgstat_catalog_group']),
                 ('TGStat Quick channels',q[1]-c['tgstat_quick_channel']),
                 ('TGStat Quick groups',q[2]-c['tgstat_quick_group'])]
        lines.extend(['','## Since current rollout','',f"Baseline: {b['at']}. "+'; '.join(f'{name} {value:+,}' for name,value in changes)+'.',
                      'TGStat Quick is included in the TGStat all-source totals above.'])
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
