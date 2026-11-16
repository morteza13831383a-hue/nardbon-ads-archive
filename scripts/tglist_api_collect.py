#!/usr/bin/env python3
"""Time-limited TGList Persian API catalog and keyword collector."""
import argparse,base64,fcntl,gzip,hashlib,json,re,sqlite3,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlencode,urlsplit
from urllib.request import Request,build_opener
from urllib.error import HTTPError
from html_core import NoRedirect,Robots,UA
from source_budget import reserve,report,is_challenge,status as budget_status
from archive_collect import gh
from lyzem_collect import terms,known_channels

BASE='https://tglist.io'
DEADLINE=datetime(2026,12,16,23,59,59,999999,tzinfo=timezone.utc)
USERNAME=re.compile(r'^[A-Za-z][A-Za-z0-9_]{3,31}$')
ROOT=Path(__file__).resolve().parents[1]

def permitted(now=None):return (now or datetime.now(timezone.utc))<=DEADLINE

def parse(value,at=None):
 if not isinstance(value,dict) or not isinstance(value.get('records'),list):raise ValueError('TGList response lacks records list')
 at=at or datetime.now(timezone.utc).isoformat();rows={}
 for item in value['records']:
  if not isinstance(item,dict):continue
  username=str(item.get('username')or'').lstrip('@')
  if not USERNAME.fullmatch(username):continue
  marker=str(item.get('peer_type')or item.get('type')or item.get('kind')or'').lower()
  kind='group' if marker in ('group','chat','supergroup') or item.get('is_group') is True else 'channel'
  label=str(item.get('label')or item.get('title')or item.get('name')or'').strip()
  if not label:continue
  members=item.get('subscribers',item.get('members'))
  rows[username.lower()]={'type':kind,'username':username.lower(),'title':label[:512],'description':str(item.get('description')or'')[:1000],
    'members':members,'category':item.get('category_slug')or item.get('category'),
    'language':item.get('language_code')or item.get('language')or'fa',
    'url':'https://t.me/'+username,'collected_at':at}
 return rows

def connect(work,root):
 work.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(work/'checkpoint.sqlite3')
 db.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
 CREATE TABLE IF NOT EXISTS jobs(term TEXT PRIMARY KEY,page INTEGER DEFAULT 1,status TEXT DEFAULT 'pending',new_rows INTEGER DEFAULT 0);
 CREATE TABLE IF NOT EXISTS records(username TEXT PRIMARY KEY,payload TEXT,day TEXT);
 CREATE TABLE IF NOT EXISTS requests(day TEXT,term TEXT,page INTEGER,status INTEGER,at TEXT);''')
 db.execute("INSERT OR IGNORE INTO jobs(term)VALUES('')")
 for term in terms(root):db.execute('INSERT OR IGNORE INTO jobs(term)VALUES(?)',(term,))
 db.commit();return db

def export(db,root):
 directory=root/'data/tglist-import';directory.mkdir(exist_ok=True);files=[]
 for day, in db.execute('SELECT DISTINCT day FROM records ORDER BY day'):
  rows=db.execute('SELECT payload FROM records WHERE day=? ORDER BY username',(day,)).fetchall()
  for index in range(0,len(rows),1000):
   path=directory/f'tglist-{day.replace("-","")}-{index//1000+1:02d}.jsonl.gz';tmp=path.with_suffix('.tmp')
   with tmp.open('wb')as raw,gzip.GzipFile(fileobj=raw,mode='wb',mtime=0,filename='')as f:
    for payload, in rows[index:index+1000]:f.write((payload+'\n').encode('utf-8'))
   tmp.replace(path);files.append(path)
 return files

def publish(db,files,repo):
 if not repo:return 0
 current=db.execute("SELECT value FROM meta WHERE key='published_hashes'").fetchone();prior=json.loads(current[0])if current else{}
 changed=[path for path in files if prior.get(path.name)!=hashlib.sha256(path.read_bytes()).hexdigest()]
 if not changed:return 0
 entries=[]
 for path in changed:
  blob=gh(repo,'git/blobs',{'content':base64.b64encode(path.read_bytes()).decode(),'encoding':'base64'})
  entries.append({'path':'data/tglist-import/'+path.name,'mode':'100644','type':'blob','sha':blob['sha']})
 for attempt in range(4):
  head=gh(repo,'commits/main');tree=gh(repo,'git/trees',{'base_tree':head['commit']['tree']['sha'],'tree':entries})
  commit=gh(repo,'git/commits',{'message':f'Import {len(changed)} TGList data parts','tree':tree['sha'],'parents':[head['sha']]})
  try:gh(repo,'git/refs/heads/main',{'sha':commit['sha'],'force':False});break
  except Exception:
   if attempt==3:raise
   time.sleep(2)
 for path in changed:prior[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
 db.execute("INSERT OR REPLACE INTO meta VALUES('published_hashes',?)",(json.dumps(prior),));db.commit();return len(changed)

def get(opener,url,robots):
 if not permitted():raise RuntimeError('TGList permission expired')
 if urlsplit(url).hostname!='tglist.io' or not robots.allowed(url):raise ValueError('TGList URL outside approved scope')
 reserve(BASE,robots.delay)
 if not permitted():raise RuntimeError('TGList permission expired')
 try:
  with opener.open(Request(url,headers={'User-Agent':UA,'Accept':'application/json,text/plain'}),timeout=30)as response:
   raw=response.read(2_000_001);report(BASE,response.status)
   body=raw.decode(response.headers.get_content_charset()or'utf-8','replace')
 except HTTPError as exc:report(BASE,exc.code);raise
 if len(raw)>2_000_000 or is_challenge(body) or 'captcha' in body.lower():
  report(BASE,'challenge');raise RuntimeError('TGList challenge/captcha')
 return body

def main():
 p=argparse.ArgumentParser();p.add_argument('--work-dir',type=Path,required=True);p.add_argument('--repo-root',type=Path,default=ROOT);p.add_argument('--repo');p.add_argument('--daily-cap',type=int,default=1500);p.add_argument('--max-requests',type=int,default=0);a=p.parse_args()
 if not permitted():raise SystemExit('TGList permission ended 2026-12-16 23:59 UTC; no request sent')
 a.work_dir.mkdir(parents=True,exist_ok=True)
 with (a.work_dir/'worker.lock').open('a+')as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  db=connect(a.work_dir,a.repo_root)
  if db.execute("SELECT 1 FROM meta WHERE key='stop_reason' AND value!=''").fetchone():raise SystemExit('TGList source hold retained; no request sent')
  day=datetime.now(timezone.utc).date().isoformat()
  if db.execute('SELECT COUNT(*) FROM requests WHERE day=?',(day,)).fetchone()[0]>=a.daily_cap:raise SystemExit('TGList daily request cap reached')
  opener=build_opener(NoRedirect())
  try:
   try:body=get(opener,BASE+'/robots.txt',Robots('User-agent: *\nAllow: /'))
   except (HTTPError,RuntimeError)as exc:
    code=exc.code if isinstance(exc,HTTPError)else 0
    db.execute('INSERT INTO requests VALUES(?,?,?,?,?)',(day,'',0,code,datetime.now(timezone.utc).isoformat()))
    if code in(403,429) or isinstance(exc,RuntimeError):db.execute("INSERT OR REPLACE INTO meta VALUES('stop_reason',?)",(str(exc)[:100],))
    db.commit();raise
   db.execute('INSERT INTO requests VALUES(?,?,?,?,?)',(datetime.now(timezone.utc).date().isoformat(),'',0,200,datetime.now(timezone.utc).isoformat()));db.commit()
   robots=Robots(body)
   if not robots.allowed(BASE+'/api/search_channels_v2?page=1&page_size=50&lang=fa&value=&category_slug=&sort='):
    raise SystemExit('TGList API disallowed by robots')
   known=known_channels(a.repo_root);known.update(row[0]for row in db.execute('SELECT username FROM records'))
   made=0
   while not a.max_requests or made<a.max_requests:
    if not permitted() or budget_status(BASE)['halted']:break
    day=datetime.now(timezone.utc).date().isoformat()
    if db.execute('SELECT COUNT(*) FROM requests WHERE day=?',(day,)).fetchone()[0]>=a.daily_cap:break
    job=db.execute("SELECT term,page FROM jobs WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone()
    if not job:break
    term,page=job
    url=BASE+'/api/search_channels_v2?'+urlencode({'page':page,'page_size':50,'lang':'fa','value':term,'category_slug':'','sort':''})
    try:
     raw=get(opener,url,robots);value=json.loads(raw)
     if str(value.get('status','')).lower()in('restricted','limited','warning','logged_out'):raise RuntimeError('TGList account/site limit')
     rows=parse(value)
    except (HTTPError,RuntimeError,ValueError)as exc:
     code=exc.code if isinstance(exc,HTTPError)else 0
     db.execute('INSERT INTO requests VALUES(?,?,?,?,?)',(day,term,page,code,datetime.now(timezone.utc).isoformat()))
     db.execute("INSERT OR REPLACE INTO meta VALUES('stop_reason',?)",(f'{type(exc).__name__}: {str(exc)[:100]}',));db.commit();break
    new=0
    for username,row in rows.items():
     if username in known:continue
     known.add(username);new+=1;db.execute('INSERT INTO records VALUES(?,?,?)',(username,json.dumps(row,ensure_ascii=False),day))
    more=value.get('hasMore',value.get('has_more'))
    total=value.get('total') or value.get('total_count')
    if more is None:more=len(value['records'])>=50 and (not isinstance(total,int) or page*50<total)
    status='pending' if more else 'done'
    db.execute('UPDATE jobs SET page=?,status=?,new_rows=new_rows+? WHERE term=?',(page+1,status,new,term))
    db.execute('INSERT INTO requests VALUES(?,?,?,200,?)',(day,term,page,datetime.now(timezone.utc).isoformat()))
    db.commit();made+=1
    print(json.dumps({'term':term,'page':page,'rows':len(rows),'new':new,'status':status},ensure_ascii=False),flush=True)
    if made%20==0:publish(db,export(db,a.repo_root),a.repo)
   publish(db,export(db,a.repo_root),a.repo)
  finally:db.close()
if __name__=='__main__':main()
