#!/usr/bin/env python3
"""Resume eligible channels workers; account and terminal host holds always win."""
import argparse,fcntl,json,os,signal,sqlite3,subprocess,time
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

def ensure(config):
    root=Path(config['code']);state=Path(config['state_dir']);state.mkdir(exist_ok=True,parents=True);python=config['python'];clean_env={k:v for k,v in os.environ.items()if k not in('TGSTAT_COOKIE','TGSTAT_COOKIE_FILE')}
    jobs=[('spy',Path(config['spy_work'])/'process.json','https://tgadsspy.com',Path(config['spy_work'])/'channels.sqlite3',[python,str(root/'scripts/channels_html.py'),'--final-plan','--work-dir',config['spy_work'],'--repo',config['repo'],'--pages-per-slice','25','--max-slices','0']),('tgstat',Path(config['tg_work'])/'process.json','https://tgstat.com',Path(config['tg_work'])/'tgstat.sqlite3',[python,str(root/'scripts/tgstat_collect.py'),'--final-plan','--session','--work-dir',config['tg_work'],'--repo',config['repo'],'--pages-per-slice','25','--max-slices','0'])]
    if config.get('mirror_command'):
        jobs.append(('mirror',Path(config['mirror_process_file']),'https://telegramadsarchive.com',Path(config['mirror_work'])/'checkpoint.sqlite3',config['mirror_command']))
    if config.get('analytics_command'):
        jobs.append(('analytics',Path(config['analytics_process_file']),'https://telegramadsanalytics.com',Path(config['analytics_work'])/'checkpoint.sqlite3',config['analytics_command']))
    events=[];ownership=None
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
        hold=ownership['archive'].get('hold')if name=='ads'else stop_reason(checkpoint)
        if hold or status(host)['halted']:events.append({'worker':name,'state':'held','reason':hold or'three host blocks'});continue
        if name=='tgstat'and tgstat_frontier_exhausted(checkpoint):
            events.append({'worker':name,'state':'frontier_exhausted','reason':'No pending catalog or username-resolution jobs; failures/caps remain coverage gaps'});continue
        env=dict(clean_env)
        if name=='tgstat':
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
        last_report=0
        while True:
            print(json.dumps(ensure(c),ensure_ascii=False),flush=True)
            if time.time()-last_report>=7200:
                report_env={k:v for k,v in os.environ.items()if k not in('TGSTAT_COOKIE','TGSTAT_COOKIE_FILE')}
                with(state/'reports.log').open('a')as output:
                    try:subprocess.run([c['python'],str(Path(c['code'])/'scripts/collection_status.py'),'--config',str(a.config),'--publish'],env=report_env,stdout=output,stderr=subprocess.STDOUT,timeout=180)
                    except subprocess.TimeoutExpired:print('Coverage publication timed out; supervision continues.',flush=True)
                last_report=time.time()
            if a.once:return
            time.sleep(30)

if __name__=='__main__':main()
