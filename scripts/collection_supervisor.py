#!/usr/bin/env python3
"""Resume eligible channels workers; account and terminal host holds always win."""
import argparse,fcntl,json,os,signal,sqlite3,subprocess,time
from datetime import datetime
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from source_budget import status

def alive(meta):
    try:
        d=json.loads(meta.read_text());p=Path('/proc')/str(d['pid']);args=(p/'cmdline').read_bytes().split(b'\0')
        return (p/'stat').read_text().split()[2]!='Z'and len(args)>1 and args[1].decode()==d['command'][1]
    except(OSError,ValueError,KeyError):return False

def stop_reason(path):
    try:
        db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True);r=db.execute("SELECT value FROM meta WHERE key='stop_reason'AND value!=''").fetchone();db.close();return r[0]if r else None
    except sqlite3.Error:return None

def tgstat_frontier_exhausted(path):
    """An empty final-plan frontier is idle, not proof of complete coverage."""
    try:
        with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as db:
            tables={r[0]for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'tgstat_slices','tgstat_unresolved','meta'} <= tables:return False
            if not db.execute('SELECT 1 FROM tgstat_slices LIMIT 1').fetchone():return False
            if db.execute("SELECT 1 FROM meta WHERE key='github_delivery_pending' AND value='1'").fetchone():return False
            return not any(db.execute("SELECT 1 FROM "+table+" WHERE status='pending' LIMIT 1").fetchone()
                           for table in ('tgstat_slices','tgstat_unresolved'))
    except sqlite3.Error:return False

def niches_complete(config):
    if config.get('niche_listing_work'):
        path=Path(config['niche_listing_work'])/'checkpoint.sqlite3'
        try:
            with closing(sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)) as db:
                if not db.execute('SELECT 1 FROM pages LIMIT 1').fetchone():return False
                if db.execute("SELECT 1 FROM pages WHERE status!='done' LIMIT 1").fetchone():return False
                return not db.execute('SELECT 1 FROM records WHERE id NOT IN (SELECT id FROM delivered) LIMIT 1').fetchone()
        except (OSError,sqlite3.Error):return False
    active={};pages=defaultdict(set)
    try:
        for item in config.get('mirror_niche_workers',[]):
            path=Path(item['work_dir'])/'checkpoint.sqlite3'
            with closing(sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)) as db:
                if db.execute("SELECT 1 FROM candidates WHERE status='pending' LIMIT 1").fetchone():return False
                for name,count,state in db.execute('SELECT name,site_count,status FROM niches'):
                    if state!='transferred':
                        if name in active:return False
                        active[name]=(count or 0,state)
                for key, in db.execute("SELECT key FROM requests WHERE kind='listing'"):
                    name,sep,page=key.rpartition(':')
                    if sep and page.isdigit():pages[name].add(int(page))
        if len(active)!=29:return False
        return all(state=='done' and len(pages[name])*30>=count-30
                   for name,(count,state) in active.items())
    except (OSError,sqlite3.Error):return False

def ensure(config):
    root=Path(config['code']);state=Path(config['state_dir']);state.mkdir(exist_ok=True,parents=True);python=config['python'];clean_env={k:v for k,v in os.environ.items()if k not in('TGSTAT_COOKIE','TGSTAT_COOKIE_FILE')}
    jobs=[('spy',Path(config['spy_work'])/'process.json','https://tgadsspy.com',Path(config['spy_work'])/'channels.sqlite3',[python,str(root/'scripts/channels_html.py'),'--final-plan','--work-dir',config['spy_work'],'--repo',config['repo'],'--pages-per-slice','25','--max-slices','0']),('tgstat',Path(config['tg_work'])/'process.json','https://tgstat.com',Path(config['tg_work'])/'tgstat.sqlite3',[python,str(root/'scripts/tgstat_collect.py'),'--final-plan','--session','--work-dir',config['tg_work'],'--repo',config['repo'],'--pages-per-slice','25','--max-slices','0'])]
    if config.get('mirror_command'):
        jobs.append(('mirror',Path(config['mirror_process_file']),'https://telegramadsarchive.com',Path(config['mirror_work'])/'checkpoint.sqlite3',config['mirror_command']))
    if config.get('analytics_command'):
        jobs.append(('analytics',Path(config['analytics_process_file']),'https://telegramadsanalytics.com',Path(config['analytics_work'])/'checkpoint.sqlite3',config['analytics_command']))
    if config.get('lyzem_command'):
        jobs.append(('lyzem',Path(config['lyzem_process_file']),'https://lyzem.com',Path(config['lyzem_work'])/'checkpoint.sqlite3',config['lyzem_command']))
    if config.get('quick_command'):
        jobs.append(('quick',Path(config['quick_process_file']),'https://tgstat.com',Path(config['tg_work'])/'tgstat.sqlite3',config['quick_command']))
    if config.get('tgchannelsme_command'):
        jobs.append(('tgchannelsme',Path(config['tgchannelsme_process_file']),'https://telegramchannels.me',Path(config['tgchannelsme_work'])/'checkpoint.sqlite3',config['tgchannelsme_command']))
    for item in config.get('mirror_niche_workers',[]):
        jobs.append(('niche_'+item['host'].replace('.','_'),Path(item['process_file']),
                     'https://'+item['host'],Path(item['work_dir'])/'checkpoint.sqlite3',item['command']))
    if config.get('mirror_gap_command'):
        jobs.append(('mirror_gap',Path(config['mirror_gap_process_file']),'https://'+config.get('mirror_gap_host','telegramadsarchive.com'),
                     Path(config['mirror_gap_work'])/'checkpoint.sqlite3',config['mirror_gap_command']))
    for item in config.get('mirror_gap_workers',[]):
        jobs.append(('gap_'+item['host'].replace('.','_'),Path(item['process_file']),
                     'https://'+item['host'],Path(item['work_dir'])/'checkpoint.sqlite3',item['command']))
    for item in config.get('niche_listing_workers',[]):
        jobs.append(('niche_listing_'+item['host'].replace('.','_'),Path(item['process_file']),
                     'https://'+item['host'],Path(item['work_dir'])/'checkpoint.sqlite3',item['command']))
    events=[];ownership=None
    if config.get('ads_niche_first') and not niches_complete(config):
        jobs=[j for j in jobs if j[0]!='mirror_gap' and not j[0].startswith('gap_')]
        events.append({'worker':'mirror_gap','state':'paused','reason':'Niche pages or ad details remain'})
    if config.get('tgadsspy_budget_policy')=='ads_then_channels':
        from collection_ownership import advance
        ownership=advance(config)
        if ownership['owner']=='ads':
            channel_meta=Path(config['spy_work'])/'process.json'
            if alive(channel_meta):
                data=json.loads(channel_meta.read_text());os.kill(data['pid'],signal.SIGTERM)
                events.append({'worker':'spy','state':'paused','reason':'Exclusive tgadsspy budget belongs to ads'})
            jobs=[j for j in jobs if j[0]!='spy']
            if not ownership['active_ad_processes']and not ownership['archive'].get('error')and(ownership['archive'].get('pending')or ownership['archive'].get('undelivered')):
                jobs.insert(0,('ads',Path(config['ads_process_file']),'https://tgadsspy.com',Path(config['ads_work'])/'checkpoint.sqlite3',config['ads_command']))
        else:
            jobs=[j for j in jobs if j[0]!='ads']
    for name,meta,host,checkpoint,command in jobs:
        if alive(meta):continue
        if name=='quick':
            try:
                with closing(sqlite3.connect('file:'+str(checkpoint)+'?mode=ro',uri=True))as quick_db:
                    value=quick_db.execute("SELECT value FROM meta WHERE key='quick_search_stop_reason' AND value!=''").fetchone()
                    hold=value[0]if value else None
                    today=time.strftime('%Y-%m-%d',time.gmtime())
                    used=quick_db.execute('SELECT COUNT(*) FROM quick_requests WHERE day=?',(today,)).fetchone()[0]+quick_db.execute('SELECT COUNT(*) FROM keyword_requests WHERE day=?',(today,)).fetchone()[0]
                    remaining=quick_db.execute("SELECT 1 FROM quick_jobs WHERE status='pending' LIMIT 1").fetchone()
                if hold:
                    events.append({'worker':name,'state':'held','reason':hold});continue
                if not remaining:
                    events.append({'worker':name,'state':'done','reason':'No pending terms'});continue
            except sqlite3.Error:hold='Quick search checkpoint unreadable'
        else:hold=ownership['archive'].get('hold')if name=='ads'else stop_reason(checkpoint)
        if name.startswith('niche_') and checkpoint.exists():
            if name.startswith('niche_listing_'):
                try:
                    with closing(sqlite3.connect('file:'+str(checkpoint)+'?mode=ro',uri=True)) as listing_db:
                        pending=listing_db.execute("SELECT 1 FROM pages WHERE status='pending' LIMIT 1").fetchone()
                    if not pending:
                        events.append({'worker':name,'state':'done_or_waiting'});continue
                except sqlite3.Error:pass
            else:
                try:
                    with closing(sqlite3.connect('file:'+str(checkpoint)+'?mode=ro',uri=True)) as niche_db:
                        pending=niche_db.execute("SELECT 1 FROM niches WHERE status='pending' LIMIT 1").fetchone()
                        candidates=niche_db.execute("SELECT 1 FROM candidates WHERE status='pending' LIMIT 1").fetchone()
                    if not pending and not candidates:
                        events.append({'worker':name,'state':'done_or_review'});continue
                except sqlite3.Error:pass
        if (name=='mirror_gap' or name.startswith('gap_')) and checkpoint.exists():
            try:
                with closing(sqlite3.connect('file:'+str(checkpoint)+'?mode=ro',uri=True)) as gap_db:
                    pending=gap_db.execute("SELECT 1 FROM jobs WHERE status='pending' LIMIT 1").fetchone()
                    candidates=gap_db.execute("SELECT 1 FROM candidates WHERE status='pending' LIMIT 1").fetchone()
                    undelivered=gap_db.execute('SELECT 1 FROM records WHERE id NOT IN (SELECT id FROM delivered) LIMIT 1').fetchone()
                if not pending and not undelivered and (config.get('niche_listing_work') or not candidates):
                    events.append({'worker':name,'state':'done_or_review'});continue
                if not pending and undelivered and meta.exists() and time.time()-meta.stat().st_mtime<300:
                    events.append({'worker':name,'state':'delivery_retry_wait'});continue
            except sqlite3.Error:pass
        if name in ('lyzem','tgchannelsme'):
            try:
                with closing(sqlite3.connect('file:'+str(checkpoint)+'?mode=ro',uri=True))as lyzem_db:
                    today=time.strftime('%Y-%m-%d',time.gmtime())
                    used=lyzem_db.execute('SELECT COUNT(*) FROM requests WHERE day=?',(today,)).fetchone()[0]
                    remaining=lyzem_db.execute("SELECT 1 FROM jobs WHERE status='pending' LIMIT 1").fetchone()
                if hold:
                    events.append({'worker':name,'state':'held','reason':hold});continue
                if not remaining:
                    events.append({'worker':name,'state':'done','reason':'No pending catalog jobs'});continue
            except sqlite3.Error:hold='Catalog checkpoint unreadable'
        if hold or status(host)['halted']:events.append({'worker':name,'state':'held','reason':hold or'three host blocks'});continue
        if name=='tgstat'and tgstat_frontier_exhausted(checkpoint):
            events.append({'worker':name,'state':'frontier_exhausted','reason':'No pending catalog or username-resolution jobs; failures/caps remain coverage gaps'});continue
        env=dict(clean_env)
        if name in ('tgstat','quick'):
            if not Path(config['cookie_file']).exists():events.append({'worker':name,'state':'missing private cookie file'});continue
            env['TGSTAT_COOKIE_FILE']=config['cookie_file']
        log=state/(name+'-runner.log')
        meta.parent.mkdir(parents=True,exist_ok=True)
        with log.open('a')as f:p=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True,env=env)
        meta.write_text(json.dumps({'pid':p.pid,'command':command,'mode':'final-plan'},indent=2));events.append({'worker':name,'state':'started','pid':p.pid})
    (state/'supervisor-status.json').write_text(json.dumps({'at':time.time(),'events':events,'tgadsspy_budget':ownership},ensure_ascii=False,indent=2));return events

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--once',action='store_true');a=p.parse_args();c=json.loads(a.config.read_text());state=Path(c['state_dir']);state.mkdir(parents=True,exist_ok=True)
    with(state/'supervisor.lock').open('a+')as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        clock_path=state/'publication-clock.json'
        try:clock=json.loads(clock_path.read_text())
        except (OSError,ValueError):
            clock={}
            status_path=Path(c.get('niche_listing_work',''))/'status-state.json'
            if status_path.exists():
                try:
                    stamp=json.loads(status_path.read_text())['at']
                    clock['last_report']=datetime.fromisoformat(stamp).timestamp()
                except (OSError,ValueError,KeyError):pass
            publisher_log=state/'niche-publisher.log'
            if publisher_log.exists():clock['last_niche_publish']=publisher_log.stat().st_mtime
        last_report=float(clock.get('last_report',0));last_niche_publish=float(clock.get('last_niche_publish',0))
        while True:
            print(json.dumps(ensure(c),ensure_ascii=False),flush=True)
            if c.get('niche_listing_work') and time.time()-last_niche_publish>=1800:
                report_env={k:v for k,v in os.environ.items()if k not in('TGSTAT_COOKIE','TGSTAT_COOKIE_FILE')}
                with (state/'niche-publisher.log').open('a') as output:
                    try:
                        result=subprocess.run([c['python'],str(Path(__file__).parent/'niche_listing_sweep.py'),
                            '--work-dir',c['niche_listing_work'],'--repo',c['repo'],'--publish-only'],
                            env=report_env,stdout=output,stderr=subprocess.STDOUT,timeout=300)
                        if result.returncode==0:
                            last_niche_publish=time.time()
                            clock_path.write_text(json.dumps({'last_report':last_report,'last_niche_publish':last_niche_publish}))
                    except subprocess.TimeoutExpired:
                        print('Listing publication timed out; checkpoint retained.',file=output,flush=True)
            if time.time()-last_report>= (21600 if c.get('niche_listing_work') else 10800):
                report_env={k:v for k,v in os.environ.items()if k not in('TGSTAT_COOKIE','TGSTAT_COOKIE_FILE')}
                with(state/'reports.log').open('a')as output:
                    if c.get('niche_listing_work'):
                        try:
                            subprocess.run([c['python'],str(Path(__file__).parent/'niche_listing_status.py'),
                                '--work-dir',c['niche_listing_work'],'--repo',c['repo'],'--publish'],
                                env=report_env,stdout=output,stderr=subprocess.STDOUT,timeout=180)
                        except subprocess.TimeoutExpired:
                            print('Six-hour report timed out; supervision continues.',file=output,flush=True)
                    else:
                        try:subprocess.run([c['python'],str(Path(c['code'])/'scripts/collection_status.py'),'--config',str(a.config),'--publish'],env=report_env,stdout=output,stderr=subprocess.STDOUT,timeout=180)
                        except subprocess.TimeoutExpired:print('Coverage publication timed out; supervision continues.',flush=True)
                        try:subprocess.run([c['python'],str(Path(__file__).parent/'multisource_status.py'),
                            '--repo-root','/workspace/work/multisource-main','--repo',c['repo'],'--publish'],
                            env=report_env,stdout=output,stderr=subprocess.STDOUT,timeout=180)
                        except subprocess.TimeoutExpired:print('Multisource status publication timed out; supervision continues.',flush=True)
                        try:subprocess.run([c['python'],str(Path(__file__).parent/'niche_coverage.py'),
                            '--repo',c['repo'],'--publish'],env=report_env,
                            stdout=output,stderr=subprocess.STDOUT,timeout=180)
                        except subprocess.TimeoutExpired:print('Niche coverage publication timed out; supervision continues.',flush=True)
                last_report=time.time()
                clock_path.write_text(json.dumps({'last_report':last_report,'last_niche_publish':last_niche_publish}))
            if a.once:return
            time.sleep(30)

if __name__=='__main__':main()
