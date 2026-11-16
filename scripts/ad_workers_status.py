#!/usr/bin/env python3
"""Report recent verified Iran-ad throughput and active CUID months for both hosts."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit
from archive_yield import cuid_time
from source_budget import status as pacing


def source_state(path, host):
    if not path.exists():return {'running':False,'reason':'checkpoint missing'}
    db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)
    since=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
    if host=='tgadsspy.com':
        saved=db.execute("SELECT COUNT(*) FROM records WHERE json_extract(payload,'$.collected_at')>=?",(since,)).fetchone()[0]
        last=db.execute("SELECT url,at FROM source_requests WHERE url LIKE 'https://tgadsspy.com/ads/%' ORDER BY id DESC LIMIT 1").fetchone()
        pending=db.execute("SELECT COUNT(*) FROM queue WHERE kind='detail' AND status='pending'").fetchone()[0]
    else:
        saved=db.execute("SELECT COUNT(*) FROM records WHERE json_extract(payload,'$.collected_at')>=?",(since,)).fetchone()[0]
        last=db.execute('SELECT id,at FROM requests ORDER BY rowid DESC LIMIT 1').fetchone()
        pending=db.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
    stop=db.execute("SELECT value FROM meta WHERE key=?",('last_stop_reason' if host=='tgadsspy.com' else 'stop_reason',)).fetchone()
    db.close()
    ad_id=last[0].rsplit('/',1)[-1] if last else None
    created=cuid_time(ad_id) if ad_id else None
    scripts=('archive_collect.py','archive_html.py') if host=='tgadsspy.com' else ('mirror_ads.py',)
    running=False
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():continue
        try:
            args=(proc/'cmdline').read_bytes().split(b'\0')
            if len(args)>1 and Path(args[1].decode()).name in scripts and (proc/'stat').read_text().split()[2]!='Z':running=True;break
        except (OSError,UnicodeError):continue
    return {'running':running,'iran_ads_last_hour':saved,'last_request_at':last[1] if last else None,'last_request_month':created.strftime('%Y-%m') if created else None,'pending_ad_pages':pending,'stop_reason':stop[0] if stop else None,'pacing':pacing('https://'+host)}


def main():
    p=argparse.ArgumentParser();p.add_argument('--spy-checkpoint',type=Path,required=True);p.add_argument('--mirror-checkpoint',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    data={'at':datetime.now(timezone.utc).isoformat(),'tgadsspy':source_state(a.spy_checkpoint,'tgadsspy.com'),'telegramadsarchive':source_state(a.mirror_checkpoint,'telegramadsarchive.com')}
    lines=['# Ad worker status','',data['at'],'','| Worker | Process | Verified Iran ads in last hour | Last request month | Last request (UTC) | Pending ad pages |','|---|---|---:|---|---|---:|']
    for key in ('tgadsspy','telegramadsarchive'):
        s=data[key];lines.append(f"| {key} | {'running' if s.get('running') else 'stopped'} | {s.get('iran_ads_last_hour','—')} | {s.get('last_request_month') or '—'} | {s.get('last_request_at') or '—'} | {s.get('pending_ad_pages','—')} |")
    lines.extend(['','Rates are counts observed over the previous wall-clock hour, including pauses. A mirror ad without an explicit Iran field is not counted as verified.',''])
    for key in ('tgadsspy','telegramadsarchive'):
        s=data[key];lines.append(f"- {key}: stop reason {s.get('stop_reason') or 'none'}; host pacing {json.dumps(s.get('pacing',{}))}.")
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(data,ensure_ascii=False))
if __name__=='__main__':main()
