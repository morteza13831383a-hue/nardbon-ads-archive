#!/usr/bin/env python3
"""Audit every Iran niche against its advertised page count and checkpoint cursor."""
import argparse
import base64
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import time

from archive_collect import gh
from mirror_niche_collect import HOSTS, NICHES


def audit(work_root):
    states=defaultdict(list)
    pages=defaultdict(set)
    for host in HOSTS:
        path=work_root/host/'checkpoint.sqlite3'
        with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as db:
            for name,count,cursor,status in db.execute('SELECT name,site_count,page,status FROM niches'):
                states[name].append((host,count or 0,cursor,status))
            for key, in db.execute("SELECT key FROM requests WHERE kind='listing'"):
                name,sep,page=key.rpartition(':')
                if sep and page.isdigit():pages[name].add(int(page))
    out=[]
    for name in NICHES:
        active=[r for r in states[name] if r[3]!='transferred']
        count=max((r[1] for r in states[name]),default=0)
        walked=len(pages[name]);last=max(pages[name],default=0)
        early=walked*30<count-30
        if len(active)!=1:
            status='review: ownership mismatch';host='—';cursor='—'
        else:
            host,_,cursor,status=active[0]
        out.append({'name':name,'site_count':count,'pages_walked':walked,'capacity':walked*30,
                    'last_page':last,'cursor':cursor,'status':status,'host':host,'early':early})
    return out


def render(rows):
    done=sum(r['status']=='done' and not r['early'] for r in rows)
    early_done=sum(r['status']=='done' and r['early'] for r in rows)
    lines=['# Iran niche listing coverage','',
           datetime.now(timezone.utc).isoformat()+' UTC. Distinct successful listing pages across all six mirrors.',
           '',f"Site counts sum: {sum(r['site_count'] for r in rows):,}; pages walked: {sum(r['pages_walked'] for r in rows):,}; "
              f"page capacity: {sum(r['capacity'] for r in rows):,}; verified done: {done}/29; premature done: {early_done}.",
           'Counts can change while crawling. Page capacity is a coverage check, not a count of unique ad IDs.',
           '', '| Niche | Site count | Pages × 30 | Last page | Next cursor | Status | Host |',
           '|---|---:|---:|---:|---:|---|---|']
    for r in rows:
        mark=(' **EARLY DONE**' if r['status']=='done' else ' **MORE PAGES**') if r['early'] else ''
        lines.append(f"| {r['name']} | {r['site_count']:,} | {r['pages_walked']} × 30 = {r['capacity']:,} | "
                     f"{r['last_page']} | {r['cursor']} | {r['status']}{mark} | {r['host']} |")
    return '\n'.join(lines)+'\n'


def publish(path,repo):
    blob=gh(repo,'git/blobs',{'content':base64.b64encode(path.read_bytes()).decode(),'encoding':'base64'})
    entry={'path':'reports/niche-coverage.md','mode':'100644','type':'blob','sha':blob['sha']}
    for attempt in range(8):
        head=gh(repo,'commits/main')
        tree=gh(repo,'git/trees',{'base_tree':head['commit']['tree']['sha'],'tree':[entry]})
        commit=gh(repo,'git/commits',{'message':'Update Iran niche listing coverage','tree':tree['sha'],'parents':[head['sha']]})
        try:
            gh(repo,'git/refs/heads/main',{'sha':commit['sha'],'force':False})
            return commit['sha']
        except Exception:
            if attempt==7:raise
            time.sleep(2)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--work-root',type=Path,default=Path('/workspace/work/niche-workers'))
    p.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'reports/niche-coverage.md')
    p.add_argument('--repo')
    p.add_argument('--publish',action='store_true')
    a=p.parse_args()
    rows=audit(a.work_root)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(render(rows),encoding='utf-8')
    if a.publish:
        if not a.repo:p.error('--publish requires --repo')
        print('published',publish(a.output,a.repo))
    else:print(a.output)

if __name__=='__main__':main()
