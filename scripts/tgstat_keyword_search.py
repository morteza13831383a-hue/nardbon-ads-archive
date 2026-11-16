#!/usr/bin/env python3
"""Checkpointed Iran keyword search on TGStat; one authenticated read-only worker."""
import argparse,fcntl,gzip,json,os,re,sqlite3,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlencode,urlsplit
from urllib.request import Request,build_opener
from urllib.error import HTTPError
from html_core import Document,Robots
from source_budget import reserve,report as budget_report,status as budget_status,is_challenge
from tgstat_collect import NoRedirect,connect,export,try_publish,Client,AccessStop
from tgstat_session import Session,account_problem,endpoint_allowed

HOST='https://tgstat.com'
PROFILE=re.compile(r'^/(channel|chat)/@([A-Za-z][A-Za-z0-9_]{3,31})/stat/?$')
def keywords(path):
    return list(dict.fromkeys(x.split('#',1)[0].strip() for x in path.read_text().splitlines() if x.split('#',1)[0].strip()))
def parse(html,kind,keyword):
    doc=Document(html);out={}
    for n in doc.root.nodes():
        if n.tag!='a':continue
        p=urlsplit(n.attrs.get('href',''));m=PROFILE.fullmatch(p.path)
        if not m or p.scheme!='https' or p.netloc!='ir.tgstat.com' or m[1]!=kind:continue
        user=m[2].lower();title=' '.join(n.text().split())[:512]
        out[user]={'type':'channel'if kind=='channel'else'group','username':user,'title':title or user,'description':'','members':None,'avg_reach':None,'err':None,'category':None,'country':'Iran','language':None,'text_sample':'','url':'https://ir.tgstat.com/'+kind+'/@'+user,'record_detail':'keyword_search','search_keyword':keyword,'collected_at':datetime.now(timezone.utc).isoformat()}
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--work-dir',type=Path,required=True);ap.add_argument('--repo',required=True);ap.add_argument('--keywords',type=Path,required=True);ap.add_argument('--daily-cap',type=int,default=300);a=ap.parse_args()
    os.environ.pop('TGSTAT_COOKIE',None)
    with(a.work_dir/'worker.lock').open('a+')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);db=connect(a.work_dir)
        db.executescript('CREATE TABLE IF NOT EXISTS keyword_jobs(keyword TEXT,kind TEXT,page INTEGER DEFAULT 0,offset INTEGER DEFAULT 0,status TEXT DEFAULT \'pending\',new_records INTEGER DEFAULT 0,pages INTEGER DEFAULT 0,PRIMARY KEY(keyword,kind)); CREATE TABLE IF NOT EXISTS keyword_requests(day TEXT,keyword TEXT,kind TEXT,page INTEGER,at TEXT);')
        for term in keywords(a.keywords):
            for kind in('channel','chat'):db.execute('INSERT OR IGNORE INTO keyword_jobs(keyword,kind) VALUES(?,?)',(term,kind))
        db.commit()
        if db.execute("SELECT value FROM meta WHERE key='stop_reason' AND value!=''").fetchone():raise SystemExit('Existing source stop retained')
        session=Session.from_environment()
        if not session.csrf_from_cookie():raise SystemExit('Session expired; source stopped')
        client=Client(db,final_plan=True);client.initialize();opener=build_opener(NoRedirect())
        while True:
            job=db.execute("SELECT keyword,kind,page,offset FROM keyword_jobs WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone()
            if not job:break
            term,kind,page,offset=job;path='/channels/search'if kind=='channel'else'/chats/search';url=HOST+path
            if not endpoint_allowed(url,'POST') or not all(rule.allowed(url)for rule in client.rules):raise SystemExit('Search endpoint disallowed')
            day=datetime.now(timezone.utc).date().isoformat()
            if db.execute('SELECT COUNT(*) FROM keyword_requests WHERE day=?',(day,)).fetchone()[0]>=a.daily_cap:
                print('Daily search cap reached; checkpoint retained',flush=True);break
            b=budget_status(HOST)
            if b['halted'] or b['pause_left_s']:
                print('Source cooldown or halt; checkpoint retained',flush=True);break
            data=urlencode({'q':term,'countries[]':'6','page':str(page),'offset':str(offset),'_tgstat_csrk':session.csrf}).encode()
            req=Request(url,data=data,headers={'User-Agent':'IranAdsCollector/1.0','Content-Type':'application/x-www-form-urlencoded','Accept':'application/json','X-Requested-With':'XMLHttpRequest','Referer':url})
            session.attach(req);reserve(HOST,15)
            try:response=opener.open(req,timeout=30)
            except HTTPError as e:
                budget_report(HOST,e.code)
                if e.code in(401,403,429):db.execute("INSERT OR REPLACE INTO meta VALUES('stop_reason',?)",('TGStat search HTTP '+str(e.code),));db.commit();print('Search stopped: access response',e.code,flush=True);break
                db.execute("UPDATE keyword_jobs SET status='failed' WHERE keyword=? AND kind=?",(term,kind));db.commit();continue
            with response:
                body=response.read(10_000_001).decode(response.headers.get_content_charset()or'utf-8','replace');budget_report(HOST,response.status)
            if len(body)>10_000_000 or is_challenge(body):
                budget_report(HOST,'challenge');db.execute("INSERT OR REPLACE INTO meta VALUES('stop_reason','TGStat search challenge')");db.commit();print('Search stopped: challenge',flush=True);break
            try:
                value=json.loads(body)
                if value.get('status')!='ok' or not isinstance(value.get('html'),str):raise ValueError()
                html=value['html'];problem=account_problem(Document(html),fragment=True)
                if problem:raise AccessStop(problem)
                more=value['hasMore'];np=int(value['nextPage']);no=int(value['nextOffset'])
                if type(more)not in(bool,int) or (more and (np,no)==(page,offset)):raise ValueError()
            except (ValueError,KeyError,TypeError,AccessStop) as exc:
                db.execute("INSERT OR REPLACE INTO meta VALUES('stop_reason',?)",('TGStat search response/account review: '+type(exc).__name__,));db.commit();print('Search stopped: response/account review',flush=True);break
            found=parse(html,kind,term);added=0
            for user,row in found.items():
                if db.execute('SELECT 1 FROM records WHERE username=?',(user,)).fetchone():continue
                db.execute('INSERT INTO records(username,payload) VALUES(?,?)',(user,json.dumps(row,ensure_ascii=False)));added+=1
            db.execute('INSERT INTO keyword_requests VALUES(?,?,?,?,?)',(day,term,kind,page,datetime.now(timezone.utc).isoformat()))
            db.execute("UPDATE keyword_jobs SET page=?,offset=?,status=?,new_records=new_records+?,pages=pages+1 WHERE keyword=? AND kind=?",(np,no,'pending'if more else'done',added,term,kind));db.commit()
            print(json.dumps({'keyword':term,'type':kind,'page':page,'new':added,'more':more},ensure_ascii=False),flush=True)
            if added or not more:
                export(db,a.work_dir);try_publish(db,a.work_dir,a.repo)
            if db.execute("SELECT COUNT(*) FROM keyword_jobs WHERE status='done'").fetchone()[0]>=20:
                print('First 10 keywords complete; continuing checkpoint',flush=True)
        db.close()
if __name__=='__main__':main()
