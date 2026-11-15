"""One-way tgadsspy budget handoff: finish the ad checkpoint, then resume channels."""
import json,sqlite3,time
from contextlib import closing
from pathlib import Path
from source_budget import status

POLICY='ads_then_channels'

def state_path(config):return Path(config['state_dir'])/'tgadsspy-ownership.json'

def load_state(config):
    try:return json.loads(state_path(config).read_text())
    except(OSError,ValueError):return {'policy':POLICY,'owner':'ads'}

def save_state(config,state):
    p=state_path(config);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(state,indent=2));tmp.replace(p)

def active_ads(config):
    """Recognize both the archive parent and an orphaned slice before allowing a handoff."""
    result=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            if(p/'stat').read_text().split()[2]=='Z':continue
            args=[v.decode()for v in(p/'cmdline').read_bytes().split(b'\0')if v]
            if len(args)<2 or Path(args[1]).name not in('archive_collect.py','archive_html.py'):continue
            i=args.index('--work-dir')
            if Path(args[i+1]).resolve()==Path(config['ads_work']).resolve():result.append(int(p.name))
        except(OSError,ValueError,IndexError,UnicodeError):continue
    return result

def archive_state(config):
    p=Path(config['ads_work'])/'checkpoint.sqlite3'
    if not p.exists():return {'error':'Existing ad checkpoint is missing'}
    try:
        with closing(sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True))as db:
            db.execute('BEGIN')
            pending=dict(db.execute("SELECT kind,COUNT(*)FROM queue WHERE status='pending'GROUP BY kind"))
            problems=dict(db.execute("SELECT status,COUNT(*)FROM queue WHERE status IN('failed','blocked_robots')GROUP BY status"))
            hold=db.execute("SELECT value FROM meta WHERE key='last_stop_reason'").fetchone()
            total=db.execute('SELECT COUNT(*)FROM queue').fetchone()[0]
            saved=db.execute('SELECT COUNT(*)FROM records').fetchone()[0]
            delivery_table=db.execute("SELECT 1 FROM sqlite_master WHERE name='github_delivered'").fetchone()
            undelivered=db.execute('SELECT COUNT(*)FROM records r WHERE NOT EXISTS(SELECT 1 FROM github_delivered d WHERE d.id=r.id)').fetchone()[0]if delivery_table else saved
        return {'pending':pending,'unresolved':problems,'queue_rows':total,'saved':saved,'undelivered':undelivered,'hold':hold[0]if hold else''}
    except sqlite3.Error:return {'error':'Ad checkpoint could not be read; handoff deferred'}

def describe(config):
    state=load_state(config);ad=archive_state(config);budget=status('https://tgadsspy.com');active=active_ads(config)
    owner=state.get('owner','ads');reason='Ad queue has not drained'
    complete=not ad.get('error')and ad.get('queue_rows',0)>0 and not ad.get('pending')and not ad.get('unresolved')and not ad.get('undelivered')and not ad.get('hold')and not active and not budget['halted']and not budget['pause_left_s']
    if owner=='channels':reason='Ad checkpoint drained; channel phase owns the host budget'
    elif ad.get('error'):reason=ad['error']
    elif ad.get('hold')or budget['halted']:reason='Ad/source hold preserved; channels remain paused'
    elif ad.get('unresolved')and not ad.get('pending'):reason='Ad errors require review; channels remain paused'
    elif ad.get('undelivered')and not ad.get('pending'):reason='Waiting for collected ads to be delivered to GitHub'
    elif complete:reason='Ad checkpoint drained and archive processes exited'
    elif not ad.get('pending')and active:reason='Waiting for the final archive slice/delivery to exit'
    end=state.get('handed_off_at',time.time())
    elapsed=max(0,end-state.get('changed_at',end))
    after=max(0,state.get('ads_saved_at_handoff',ad.get('saved',0))-state.get('ads_saved_at_change',ad.get('saved',0)))
    return {'policy':POLICY,'owner':owner,'reason':reason,'ad_complete':bool(complete),'archive':ad,'active_ad_processes':active,'pacing':budget,'changed_at':state.get('changed_at'),'before_ads_per_hour':state.get('before_ads_per_hour'),'before_window_seconds':state.get('before_window_seconds'),'after_ads_saved':after,'after_window_seconds':round(elapsed,1),'after_ads_per_hour':round(3600*after/elapsed,2)if elapsed>=60 else None}

def advance(config):
    result=describe(config)
    if result['owner']=='ads'and result['ad_complete']:
        state=load_state(config);state.update(owner='channels',handed_off_at=time.time(),ads_saved_at_handoff=result['archive']['saved']);save_state(config,state);result=describe(config)
    return result
