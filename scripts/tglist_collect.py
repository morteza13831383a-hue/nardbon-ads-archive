#!/usr/bin/env python3
"""One-off, checkpointed TGList keyword search under the owner's time-limited grant."""
import argparse,datetime,gzip,json,re,sqlite3
from pathlib import Path
from urllib.parse import urlencode,urljoin,urlsplit,parse_qs
from urllib.request import Request,build_opener
from urllib.error import HTTPError
from html_core import Document,Robots
from source_budget import reserve,report as budget_report,is_challenge,status as budget_status
from tgstat_collect import NoRedirect

HOST='https://tglist.io'
HARD_STOP=datetime.datetime(2026,12,16,23,59,59,999999,tzinfo=datetime.timezone.utc)
PROFILE=re.compile(r'^/(?:channel|channels|group|groups)/@?([A-Za-z][A-Za-z0-9_]{3,31})/?$')
MEMBERS=re.compile(r'([\d,.]+\s*[kKmM]?)\s+(subscribers|participants|members)\b',re.I)
def allowed_time(now=None):return (now or datetime.datetime.now(datetime.timezone.utc))<=HARD_STOP
def terms(path):return list(dict.fromkeys(x.split('#',1)[0].strip()for x in path.read_text().splitlines()if x.split('#',1)[0].strip()))
def parse(html,base):
    doc=Document(html);rows={};next_url=None
    current=parse_qs(urlsplit(base).query).get('page',['1'])[0]
    current=int(current)if current.isdigit()else 1
    for node in doc.root.nodes():
        if node.tag!='a':continue
        url=urljoin(base,node.attrs.get('href',''));p=urlsplit(url)
        if p.scheme!='https' or p.hostname!='tglist.io':continue
        if p.path=='/search':
            query=parse_qs(p.query);page=query.get('page',[''])[0]
            if query.get('q')==parse_qs(urlsplit(base).query).get('q') and page.isdigit() and int(page)==current+1:
                next_url=url
        m=PROFILE.fullmatch(p.path)
        if not m:continue
        text=' '.join(node.text().split());match=MEMBERS.search(text)
        if not match:continue
        username=m[1].lower();title=text[:match.start()].strip()[:512]
        if not title:continue
        value=match[1].replace(',','').strip();members=int(value)if value.isdigit()else value
        kind='group'if p.path.startswith(('/group/','/groups/'))else'channel'
        rows[username]={'type':kind,'username':username,'title':title,'description':'','members':members,'category':None,'language':None,'url':url.split('?',1)[0],'collected_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    return rows,next_url

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--work-dir',type=Path,required=True);ap.add_argument('--repo-root',type=Path,required=True);ap.add_argument('--limit-terms',type=int,default=0);ap.add_argument('--daily-cap',type=int,default=300);a=ap.parse_args()
    if not allowed_time():raise SystemExit('TGList permission ended at 2026-12-16 23:59 UTC; no request sent')
    a.work_dir.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(a.work_dir/'checkpoint.sqlite3')
    db.executescript('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE IF NOT EXISTS jobs(term TEXT PRIMARY KEY,page INTEGER DEFAULT 1,status TEXT DEFAULT \'pending\',channels INTEGER DEFAULT 0,groups INTEGER DEFAULT 0);CREATE TABLE IF NOT EXISTS records(username TEXT PRIMARY KEY,payload TEXT);CREATE TABLE IF NOT EXISTS requests(day TEXT,url TEXT,status INTEGER,at TEXT);')
    for path in(a.repo_root/'data/channel_keywords.txt',a.repo_root/'data/channel_keywords_auto.txt'):
        for term in terms(path):db.execute('INSERT OR IGNORE INTO jobs(term)VALUES(?)',(term,))
    db.commit()
    if db.execute("SELECT 1 FROM meta WHERE key='stop_reason' AND value!=''").fetchone():raise SystemExit('TGList source hold retained')
    known=set()
    for path,table in((Path('/workspace/work/tgstat-production/tgstat.sqlite3'),'records'),(Path('/workspace/work/channels-production/channels.sqlite3'),'channels')):
        if path.exists():
            source=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)
            try:known.update((x[0]or'').lower()for x in source.execute('SELECT username FROM '+table))
            except sqlite3.Error:pass
            source.close()
    known.update(x[0]for x in db.execute('SELECT username FROM records'))
    opener=build_opener(NoRedirect())
    def get(url,robots=None):
        if not allowed_time():raise RuntimeError('TGList permission expired')
        if robots and not robots.allowed(url):raise RuntimeError('robots disallows request')
        reserve(HOST,max(10,robots.delay if robots else 10))
        if not allowed_time():raise RuntimeError('TGList permission expired')
        try:r=opener.open(Request(url,headers={'User-Agent':'IranAdsCollector/1.0','Accept':'text/html'}),timeout=30)
        except HTTPError as e:budget_report(HOST,e.code);raise
        with r:body=r.read(2_000_001).decode(r.headers.get_content_charset()or'utf-8','replace');budget_report(HOST,r.status)
        if len(body)>2_000_000 or is_challenge(body) or re.search(r'captcha|verify you are human',body,re.I):
            budget_report(HOST,'challenge');raise RuntimeError('captcha/challenge')
        return body
    try:robots=Robots(get(HOST+'/robots.txt'))
    except HTTPError as e:
        db.execute("INSERT OR REPLACE INTO meta VALUES('stop_reason',?)",('robots HTTP '+str(e.code),));db.commit();raise SystemExit('TGList stopped on robots HTTP response')
    if not robots.allowed(HOST+'/search'):raise SystemExit('TGList search disallowed by robots')
    while allowed_time():
        if a.limit_terms and db.execute("SELECT COUNT(*) FROM jobs WHERE status IN('done','low_yield')").fetchone()[0]>=a.limit_terms:break
        job=db.execute("SELECT term,page FROM jobs WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone()
        if not job:break
        term,page=job;day=datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        if db.execute('SELECT COUNT(*) FROM requests WHERE day=?',(day,)).fetchone()[0]>=a.daily_cap:break
        budget=budget_status(HOST)
        if budget['halted']or budget['pause_left_s']:break
        url=HOST+'/search?'+urlencode({'q':term,**({'page':page}if page>1 else{})})
        try:body=get(url,robots)
        except HTTPError as e:
            if e.code in(403,429):db.execute("INSERT OR REPLACE INTO meta VALUES('stop_reason',?)",('HTTP '+str(e.code),));db.commit();break
            raise
        except RuntimeError as e:
            db.execute("INSERT OR REPLACE INTO meta VALUES('stop_reason',?)",(str(e),));db.commit();break
        rows,next_url=parse(body,url);new={'channel':0,'group':0}
        for username,row in rows.items():
            if username in known:continue
            known.add(username);new[row['type']]+=1;db.execute('INSERT INTO records VALUES(?,?)',(username,json.dumps(row,ensure_ascii=False)))
        total=sum(new.values());next_page=page+1
        if next_url:
            q=parse_qs(urlsplit(next_url).query)
            if q.get('q')==[term] and q.get('page',[''])[0].isdigit():next_page=int(q['page'][0])
        status='low_yield'if total<2 else ('pending'if next_url and next_page>page else'done')
        db.execute('INSERT INTO requests VALUES(?,?,?,?)',(day,url,200,datetime.datetime.now(datetime.timezone.utc).isoformat()))
        db.execute('UPDATE jobs SET page=?,status=?,channels=channels+?,groups=groups+? WHERE term=?',(next_page,status,new['channel'],new['group'],term));db.commit()
        output=a.repo_root/'data/tglist-import/tglist-00.jsonl.gz';output.parent.mkdir(parents=True,exist_ok=True)
        temp=output.with_suffix('.tmp')
        with gzip.open(temp,'wt',encoding='utf-8')as f:
            for raw, in db.execute('SELECT payload FROM records ORDER BY username'):f.write(raw+'\n')
        temp.replace(output)
        print(json.dumps({'term':term,'page':page,'new':new,'status':status},ensure_ascii=False),flush=True)
    db.close()
if __name__=='__main__':main()
