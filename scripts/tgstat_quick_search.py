#!/usr/bin/env python3
"""Read-only TGStat header Quick search with a separate resumable checkpoint."""
import argparse,fcntl,json,os,re,sqlite3,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlencode,urlsplit
from urllib.request import Request,build_opener
from urllib.error import HTTPError
from html_core import Document
from source_budget import reserve,report as budget_report,status as budget_status,is_challenge
from tgstat_collect import Client,NoRedirect,connect,export,try_publish
from tgstat_session import Session,account_problem,endpoint_allowed
from tgstat_keyword_search import keywords,refresh_auto
HOST='https://tgstat.com';URL=HOST+'/channels/global-search'
PUBLIC=re.compile(r'^/(channel|chat)/@([A-Za-z][A-Za-z0-9_]{3,31})/?$')
COUNT=re.compile(r'([\d.,]+\s*[kKmM]?)\s+(subscribers|participants|members)\b',re.I)
def parse(html,term,secret_filter=None):
    out={}
    for n in Document(html).root.nodes():
        if n.tag!='a':continue
        p=urlsplit(n.attrs.get('href',''));m=PUBLIC.fullmatch(p.path)
        if not m or p.scheme!='https' or p.hostname not in('tgstat.com','ir.tgstat.com'):continue
        visible=' '.join(n.text().split());match=COUNT.search(visible)
        if not match:continue
        user=m[2].lower();title=visible[:match.start()].strip()[:512]
        if not title:continue
        count=match[1].replace(',','').strip();members=int(count)if count.isdigit()else count
        kind='channel'if m[1]=='channel'else'group'
        row={'type':kind,'username':user,'title':title,'description':'','members':members,'avg_reach':None,'err':None,'category':None,'country':'Iran'if p.hostname=='ir.tgstat.com'else None,'language':None,'text_sample':'','url':f'https://{p.hostname}/{m[1]}/@{user}','record_detail':'quick_search','search_keyword':term,'collected_at':datetime.now(timezone.utc).isoformat()}
        if secret_filter and secret_filter.clean(json.dumps(row,ensure_ascii=False))!=json.dumps(row,ensure_ascii=False):continue
        out[user]=row
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--work-dir',type=Path,required=True);ap.add_argument('--repo',required=True);ap.add_argument('--keywords',type=Path,required=True);ap.add_argument('--auto-keywords',type=Path,required=True);ap.add_argument('--daily-cap',type=int,default=300);ap.add_argument('--limit-terms',type=int,default=0);a=ap.parse_args()
    os.environ.pop('TGSTAT_COOKIE',None)
    with(a.work_dir/'worker.lock').open('a+')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);db=connect(a.work_dir)
        db.execute("CREATE TABLE IF NOT EXISTS quick_jobs(term TEXT PRIMARY KEY,status TEXT DEFAULT 'pending',channels INTEGER DEFAULT 0,groups INTEGER DEFAULT 0,rows INTEGER DEFAULT 0)")
        db.execute('CREATE TABLE IF NOT EXISTS quick_requests(day TEXT,term TEXT,at TEXT)')
        for term in keywords(a.keywords):db.execute('INSERT OR IGNORE INTO quick_jobs(term) VALUES(?)',(term,))
        db.commit();refresh_auto(db,a.auto_keywords,a.repo)
        for term in keywords(a.auto_keywords):db.execute('INSERT OR IGNORE INTO quick_jobs(term) VALUES(?)',(term,))
        db.commit()
        if db.execute("SELECT 1 FROM meta WHERE key='quick_search_stop_reason' AND value!=''").fetchone():raise SystemExit('Quick search account hold retained')
        sess=Session.from_environment();client=Client(db,final_plan=True);client.initialize();opener=build_opener(NoRedirect())
        if not endpoint_allowed(URL,'POST')or not all(rule.allowed(URL)for rule in client.rules):raise SystemExit('Quick search disallowed by robots/scope')
        while True:
            job=db.execute("SELECT term FROM quick_jobs WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone()
            if not job:break
            if a.limit_terms and db.execute("SELECT COUNT(*) FROM quick_jobs WHERE status='done'").fetchone()[0]>=a.limit_terms:break
            term=job[0];day=datetime.now(timezone.utc).date().isoformat()
            count=db.execute('SELECT COUNT(*) FROM quick_requests WHERE day=?',(day,)).fetchone()[0]+db.execute('SELECT COUNT(*) FROM keyword_requests WHERE day=?',(day,)).fetchone()[0]
            if count>=a.daily_cap:print('Daily cap reached',flush=True);break
            b=budget_status(HOST)
            if b['halted']or b['pause_left_s']:print('Host cooldown/halt; checkpoint retained',flush=True);break
            req=Request(URL,data=urlencode({'query':term}).encode(),headers={'User-Agent':'IranAdsCollector/1.0','Content-Type':'application/x-www-form-urlencoded','Accept':'application/json','X-Requested-With':'XMLHttpRequest','Referer':HOST+'/'})
            sess.attach(req);reserve(HOST,15)
            try:r=opener.open(req,timeout=30)
            except HTTPError as exc:
                budget_report(HOST,exc.code)
                if exc.code in(401,403,429):db.execute("INSERT OR REPLACE INTO meta VALUES('quick_search_stop_reason',?)",('HTTP '+str(exc.code),));db.commit();print('Quick search access hold',exc.code,flush=True);break
                print('Quick search HTTP error',exc.code,flush=True);break
            with r:body=r.read(2_000_001).decode(r.headers.get_content_charset()or'utf-8','replace');budget_report(HOST,r.status)
            if len(body)>2_000_000 or is_challenge(body):
                budget_report(HOST,'challenge');db.execute("INSERT OR REPLACE INTO meta VALUES('quick_search_stop_reason','challenge')");db.commit();print('Quick search challenge',flush=True);break
            try:
                value=json.loads(body);state=str(value.get('status',''))
                if state!='ok' or not isinstance(value.get('html'),str) or any(value.get(k)for k in('warning','accountWarning','limited','isLimited','blocked')):raise ValueError('status/limit '+state[:40])
                problem=account_problem(Document(value['html']),fragment=True)
                if problem:raise ValueError(problem)
            except (ValueError,TypeError,AttributeError) as exc:
                db.execute("INSERT OR REPLACE INTO meta VALUES('quick_search_stop_reason',?)",('Quick search response/account: '+str(exc)[:80],));db.commit();print('Quick search response/account hold',flush=True);break
            found=parse(value['html'],term,sess.filter);ch=gr=0
            for user,row in found.items():
                if db.execute('SELECT 1 FROM records WHERE username=?',(user,)).fetchone():continue
                db.execute('INSERT INTO records(username,payload) VALUES(?,?)',(user,json.dumps(row,ensure_ascii=False)))
                if row['type']=='channel':ch+=1
                else:gr+=1
            db.execute('INSERT INTO quick_requests VALUES(?,?,?)',(day,term,datetime.now(timezone.utc).isoformat()))
            db.execute("UPDATE quick_jobs SET status='done',channels=?,groups=?,rows=? WHERE term=?",(ch,gr,len(found),term));db.commit()
            print(json.dumps({'term':term,'channels':ch,'groups':gr,'rows':len(found)},ensure_ascii=False),flush=True)
            export(db,a.work_dir);try_publish(db,a.work_dir,a.repo)
        db.close()
if __name__=='__main__':main()
