#!/usr/bin/env python3
"""Credential-free snapshots for reports/collection-status.md."""
import argparse,collections,json,sqlite3,time
from datetime import datetime,timezone
from pathlib import Path
from source_budget import status as pacing

TARGETS={'tgadsspy':{'channel':547000},'tgstat':{'channel':127000,'group':4500}}
def db(path):return sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)
def exists(c,name):return bool(c.execute('SELECT 1 FROM sqlite_master WHERE name=?',(name,)).fetchone())
def snapshot(spy_work,tg_work):
    out={'at':time.time(),'sites':{}}
    for site,work,filename,table in(('tgadsspy',spy_work,'channels.sqlite3','channels'),('tgstat',tg_work,'tgstat.sqlite3','records')):
        p=Path(work)/filename
        if not p.exists():continue
        c=db(p);counts=collections.Counter();names=set()
        for key,raw in c.execute('SELECT '+('username'if site=='tgstat'else'username')+',payload FROM '+table):
            value=json.loads(raw);kind=value.get('type','channel');counts[kind]+=1
            u=value.get('username')or(key if site=='tgadsspy'else None)
            if u and kind=='channel':names.add(u.lower())
        stop=c.execute("SELECT value FROM meta WHERE key IN('stop_reason','last_stop_reason')AND value!=''LIMIT 1").fetchone()
        item={'saved':dict(counts),'requests':c.execute('SELECT COUNT(*)FROM requests').fetchone()[0]if exists(c,'requests')else None,'pacing':pacing('https://tgstat.com'if site=='tgstat'else'https://tgadsspy.com'),'stop_reason':stop[0]if stop else None,'pending_jobs':dict(c.execute('SELECT status,COUNT(*)FROM queue GROUP BY status')),'channel_handles':sorted(names)}
        if site=='tgadsspy'and exists(c,'spy_slices'):
            item['slices']=dict(c.execute('SELECT status,COUNT(*)FROM spy_slices GROUP BY status'));item['known_page_budget_upper_bound']=c.execute("SELECT COALESCE(SUM(MAX(0,201-page)),0)FROM spy_slices WHERE status='pending'").fetchone()[0];item['controls']=list(c.execute('SELECT name,verdict,evidence FROM spy_controls'))
        if site=='tgadsspy'and exists(c,'priority_profiles'):
            item['priority_profiles']=dict(c.execute('SELECT status,COUNT(*)FROM priority_profiles GROUP BY status'))
        if site=='tgstat'and exists(c,'tgstat_slices'):
            item['slices']=dict(c.execute('SELECT status,COUNT(*)FROM tgstat_slices GROUP BY status'));item['pages_fetched']=c.execute('SELECT COALESCE(SUM(pages),0)FROM tgstat_slices').fetchone()[0]
        if site=='tgstat'and exists(c,'tgstat_unresolved'):
            item['username_resolution']=dict(c.execute('SELECT status,COUNT(*)FROM tgstat_unresolved GROUP BY status'))
            item['frontier_exhausted']=bool(item.get('slices'))and not item['slices'].get('pending')and not item['username_resolution'].get('pending')
        if site=='tgstat'and exists(c,'tgstat_languages'):item['persian_catalog_discovery']=list(c.execute('SELECT url,status,evidence FROM tgstat_languages'))
        if site=='tgstat':
            resumed=c.execute("SELECT value FROM meta WHERE key='resume_verified_at'").fetchone()
            item['resume_verified_at']=resumed[0]if resumed else None
        if site=='tgstat':item['legacy_failures']=list(c.execute("SELECT error,COUNT(*)FROM queue WHERE status='failed'GROUP BY error"))
        out['sites'][site]=item;c.close()
    a=set(out['sites'].get('tgadsspy',{}).get('channel_handles',[]));b=set(out['sites'].get('tgstat',{}).get('channel_handles',[]))
    out['overlap']={'tgadsspy':len(a),'tgstat_public_handles':len(b),'intersection':len(a&b),'union_public_handles':len(a|b),'population_estimate':round(len(a)*len(b)/len(a&b))if a&b else None}
    return out

def render(current,baseline):
    elapsed=max(1,current['at']-baseline['at']);lines=['# Collection status','',datetime.fromtimestamp(current['at'],timezone.utc).isoformat(),'','Targets supplied by the owner: ~547,000 Persian tgadsspy channels; ~127,000 TGStat Iran channels and ~4,500 groups. These are coverage targets, not guaranteed HTML enumeration totals. Afghan and other Persian records remain included; the importer labels region.','','| Site / type | Total saved | New / 100 requests since baseline | Conditional remaining time |','|---|---:|---:|---|']
    for site,s in current['sites'].items():
        prior=baseline['sites'].get(site,{});q=s.get('requests');pq=prior.get('requests');delta=q-pq if q is not None and pq is not None else None
        for kind,n in s['saved'].items():
            gained=n-prior.get('saved',{}).get(kind,n);rate=round(100*gained/delta,2)if delta and delta>0 else 'not yet measured'
            hours=gained/elapsed*3600;remaining=max(0,TARGETS.get(site,{}).get(kind,n)-n)
            eta='paused / review required'if s['stop_reason']or s['pacing']['halted']else (f'{remaining/hours:.1f} continuous hours at recent yield; unverified coverage'if gained>=10 and elapsed>=60 and hours else'not defensible yet')
            if not(s['stop_reason']or s['pacing']['halted'])and site=='tgadsspy'and s.get('slices')and not s['slices'].get('pending'):
                eta='unknown: listing frontier ended; profile/discovery yield and reachable coverage unverified'
            if site=='tgstat'and s.get('resume_verified_at')and not(s['stop_reason']or s['pacing']['halted']):
                if datetime.fromisoformat(s['resume_verified_at']).timestamp()>baseline['at']:
                    eta='unknown: baseline spans the pre-resume hold; requires an active-run measurement window'
            if site=='tgadsspy'and current.get('tgadsspy_budget',{}).get('owner')=='ads':
                eta='paused: the ad worker owns the entire tgadsspy request budget'
            lines.append(f'| {site} / {kind} | {n:,} | {rate} | {eta} |')
    if current.get('tgadsspy_budget'):
        lease=current['tgadsspy_budget'];pending=dict(lease['archive'].get('pending',{}));pending.setdefault('sitemap',0);before=lease.get('before_ads_per_hour');after=lease.get('after_ads_per_hour')
        lines+=['','## Exclusive tgadsspy budget','',f"Current owner: **{lease['owner']}**. {lease['reason']}.",f"Exclusive ownership began: {datetime.fromtimestamp(lease['changed_at'],timezone.utc).isoformat()if lease.get('changed_at')else'unknown'}.",'',
            '| Ad yield window | New Iran ads saved / hour | Window |','|---|---:|---|',
            f"| Before exclusive ad ownership | {before if before is not None else 'unmeasured'} | {(lease.get('before_window_seconds')or 0)/3600:.2f} h before the switch |",
            f"| After exclusive ad ownership | {after if after is not None else 'warming up'} | {lease['after_window_seconds']/3600:.2f} h since the switch |",
            '',f"New Iran ads since switch: {lease['after_ads_saved']:,}. Remaining ad jobs by kind (including sitemap): {pending}. Unresolved ad rows: {lease['archive'].get('unresolved',{})}. Undelivered Iran ads: {lease['archive'].get('undelivered','unknown')}.",
            'Rates count newly saved unique Iran ads per wall-clock hour, including pauses. The before window is the preceding hour; the after window starts when channels were paused. Different discovery mixes can change yield independently of request speed.',
            'Channels resume automatically only after the entire ad queue drains, source holds are absent, all saved ads have been delivered and the final archive process exits. Failed/robots-blocked rows remain reported as coverage gaps; this handoff does not claim full source coverage. TGStat remains on its own independent host budget.']
    lines+=['','## Pacing, pauses and remaining work','']
    for site,s in current['sites'].items():
        p=s['pacing'];lines.append(f"- **{site}**: delay {p['delay_s']} s + jitter; pause {p['pause_left_s']} s; blocks in 6 h {p['blocks_6h']}; host halted {p['halted']}. Stop reason: {s['stop_reason']or'none'}. Slices: {s.get('slices','not initialized')}; queue states: {s['pending_jobs']}.")
        if 'known_page_budget_upper_bound'in s:lines.append(f"  Known listing-slice page budget upper bound: {s['known_page_budget_upper_bound']:,}; future graph/range partitions are unknown, so this is not an exact pages-left count.")
        if 'priority_profiles'in s:
            lines.append(f"  Priority profile states: {s['priority_profiles']}. Listing caps/repeats are unresolved coverage gaps; finishing the listing frontier does not establish complete coverage.")
        if site=='tgstat':lines.append('  Exact pages left are unknown until server hasMore=false for each category/type/sort; account holds are never automatically cleared.')
    lines+=['','## Filter / failure findings','']
    for row in current['sites'].get('tgadsspy',{}).get('controls',[]):lines.append(f'- tgadsspy `{row[0]}`: {row[1]}; {row[2]}.')
    for reason,n in current['sites'].get('tgstat',{}).get('legacy_failures',[]):lines.append(f'- TGStat prior failures: {n} × {reason}.')
    lines+=['- The earlier 129 TGStat failures were missing expected profile headings, not proof of 129 transport blocks. The old full-profile strategy required those headings; new listing rows save metadata directly. Existing failures are preserved for audit; timeouts/5xx are retried, and only rows lacking public usernames may need profile resolution.','- Live probes found category=news changes results. Bounds 0–100 and order=asc returned the same multi-million-member channels as the base page and were omitted from pagination. Such filters cannot be treated as proven independent coverage paths.','- The Persian-language TGStat catalog beyond country Iran is not yet verified; discovery uses anonymous approved pages, and genuine account/session holds are never bypassed.','', '## Two-source overlap estimate','',json.dumps(current['overlap'],ensure_ascii=False),'','The population estimate assumes independent, representative source samples. Catalog caps and correlated discovery violate that assumption, so it is an estimate, not a completeness certificate. Queue exhaustion with unresolved caps does not mean the owner targets were reached.','','## Operation','', 'The supervisor restarts crashed eligible workers from SQLite checkpoints, respects shared cooldowns and terminal account/host holds, and leaves the ad collection logic/cutoff unchanged. It must itself be launched after a full machine shutdown. The scheduled status task attempts to restore that supervisor on this registered environment before updating this file; a stopped/offline environment cannot crawl.','']
    tg=current['sites'].get('tgstat',{})
    if 'username_resolution'in tg:
        lines+=['','## TGStat username-resolution frontier','',f"States: {tg['username_resolution']}. Known frontier exhausted: {tg['frontier_exhausted']}.",'When this frontier is empty and delivery has succeeded, the supervisor leaves the worker idle instead of repeatedly restarting it. Failed profiles and listing caps remain unresolved coverage gaps; this is not complete coverage.','']
    return '\n'.join(lines)

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--publish',action='store_true');a=p.parse_args();config=json.loads(a.config.read_text());state=Path(config['state_dir']);state.mkdir(parents=True,exist_ok=True)
    current=snapshot(config['spy_work'],config['tg_work'])
    if config.get('tgadsspy_budget_policy')=='ads_then_channels':
        current['tgadsspy_budget']=__import__('collection_ownership').describe(config)
    basefile=state/'report-baseline.json'
    if not basefile.exists():basefile.write_text(json.dumps(current))
    baseline=json.loads(basefile.read_text());body=render(current,baseline)
    path=Path(config['code'])/'reports'/'collection-status.md';path.parent.mkdir(exist_ok=True);path.write_text(body)
    (state/'last-status.json').write_text(json.dumps({**current,'sites':{k:{n:v for n,v in s.items()if n!='channel_handles'}for k,s in current['sites'].items()}},ensure_ascii=False,indent=2))
    if a.publish:
        from tgstat_session import delivery_gh as gh
        import subprocess
        for attempt in range(4):
            h=gh(config['repo'],'commits/main');tree=gh(config['repo'],'git/trees',{'base_tree':h['commit']['tree']['sha'],'tree':[{'path':'reports/collection-status.md','mode':'100644','type':'blob','content':body}]});commit=gh(config['repo'],'git/commits',{'message':'Update collection status','tree':tree['sha'],'parents':[h['sha']]})
            try:gh(config['repo'],'git/refs/heads/main',{'sha':commit['sha'],'force':False});break
            except subprocess.CalledProcessError:
                if attempt==3:raise
        print('Published reports/collection-status.md',commit['sha'])
    else:print('Updated reports/collection-status.md')

if __name__=='__main__':main()
