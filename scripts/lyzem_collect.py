#!/usr/bin/env python3
"""Checkpointed Persian Lyzem search for channels and groups."""
import argparse,base64,hashlib,gzip,json,re,sqlite3,time
from datetime import datetime,timedelta,time as dt_time,timezone
from pathlib import Path
from urllib.parse import urlencode,urlsplit
from urllib.error import HTTPError
from html_core import Client,Document
from source_budget import Challenge,HostBlocked,status as budget_status
from archive_collect import gh

BASE='https://lyzem.com'
PERSIAN=re.compile(r'[\u0600-\u06ff]')
USERNAME=re.compile(r'^[A-Za-z][A-Za-z0-9_]{3,31}$')
BOTS={'lyzembot','mlyzembot','lyzemcom','editorpost_bot'}
ROOT=Path(__file__).resolve().parents[1]

def terms(root):
 out=[];seen=set()
 for path in (root/'data/channel_keywords.txt',root/'data/channel_keywords_auto.txt'):
  for raw in path.read_text(encoding='utf-8').splitlines():
   word=raw.split('#',1)[0].strip()
   if word and word not in seen:seen.add(word);out.append(word)
 return out

def parse(html,kind,at=None):
 doc=Document(html);rows={};at=at or datetime.now(timezone.utc).isoformat()
 for item in doc.root.nodes():
  if item.tag!='li' or 'search-result' not in item.attrs.get('class','').split():continue
  children=list(item.nodes())
  marker=next((n for n in children if 'search-result-type-wrapper' in n.attrs.get('class','').split()),None)
  title=next((n for n in children if 'search-result-title' in n.attrs.get('class','').split()),None)
  descr=next((n for n in children if 'search-result-descr' in n.attrs.get('class','').split()),None)
  if not marker or not title:continue
  row_kind={'channel':'channel','group':'group'}.get(marker.attrs.get('title','').lower())
  if row_kind!=kind:continue
  anchor=next((n for n in title.nodes() if n.tag=='a'),None)
  if not anchor:continue
  p=urlsplit(anchor.attrs.get('href',''))
  if p.scheme!='https' or p.hostname not in ('t.me','www.t.me'):continue
  username=p.path.strip('/')
  if not USERNAME.fullmatch(username) or username.lower() in BOTS:continue
  name=' '.join(title.text().split())[:512]
  description=' '.join(descr.text().split())[:1000] if descr else ''
  if not PERSIAN.search(name+' '+description):continue
  rows[username.lower()]={'type':kind,'username':username.lower(),'title':name,'description':description,'members':None,'category':None,'language':'fa','url':'https://t.me/'+username,'collected_at':at}
 return rows

def known_channels(root):
 path=root/'data/channels.json'
 if not path.exists():return set()
 value=json.loads(path.read_text(encoding='utf-8'))
 if isinstance(value,dict) and 'fields' in value and 'rows' in value:
  position=value['fields'].index('u');return {str(row[position]).lower() for row in value['rows'] if row[position]}
 return {str(u).lower()for u in value}

def connect(work,root):
 work.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(work/'checkpoint.sqlite3')
 db.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
 CREATE TABLE IF NOT EXISTS jobs(term TEXT,kind TEXT,page INTEGER DEFAULT 1,status TEXT DEFAULT 'pending',new_rows INTEGER DEFAULT 0,PRIMARY KEY(term,kind));
 CREATE TABLE IF NOT EXISTS records(username TEXT PRIMARY KEY,payload TEXT,day TEXT);
 CREATE TABLE IF NOT EXISTS requests(day TEXT,term TEXT,kind TEXT,page INTEGER,status INTEGER,at TEXT);''')
 for term in terms(root):
  for kind in ('channel','group'):db.execute('INSERT OR IGNORE INTO jobs(term,kind) VALUES(?,?)',(term,kind))
 db.commit();return db

def export(db,root):
 directory=root/'data/lyzem-import';directory.mkdir(exist_ok=True)
 files=[]
 for day, in db.execute('SELECT DISTINCT day FROM records ORDER BY day'):
  rows=db.execute('SELECT payload FROM records WHERE day=? ORDER BY username',(day,)).fetchall()
  for index in range(0,len(rows),1000):
   path=directory/f'lyzem-{day.replace("-","")}-{index//1000+1:02d}.jsonl.gz'
   tmp=path.with_suffix('.tmp')
   with tmp.open('wb') as raw, gzip.GzipFile(fileobj=raw,mode='wb',mtime=0,filename='') as f:
    for payload, in rows[index:index+1000]:f.write((payload+'\n').encode('utf-8'))
   tmp.replace(path);files.append(path)
 return files

def publish(db,files,repo):
 if not repo:return 0
 prior=json.loads(db.execute("SELECT value FROM meta WHERE key='published_hashes'").fetchone()[0])if db.execute("SELECT 1 FROM meta WHERE key='published_hashes'").fetchone()else{}
 changed=[p for p in files if prior.get(p.name)!=hashlib.sha256(p.read_bytes()).hexdigest()]
 if not changed:return 0
 entries=[]
 for path in changed:
  blob=gh(repo,'git/blobs',{'content':base64.b64encode(path.read_bytes()).decode(),'encoding':'base64'})
  entries.append({'path':'data/lyzem-import/'+path.name,'mode':'100644','type':'blob','sha':blob['sha']})
 for attempt in range(4):
  head=gh(repo,'commits/main')
  tree=gh(repo,'git/trees',{'base_tree':head['commit']['tree']['sha'],'tree':entries})
  commit=gh(repo,'git/commits',{'message':f'Import {len(changed)} Lyzem data parts','tree':tree['sha'],'parents':[head['sha']]})
  try:gh(repo,'git/refs/heads/main',{'sha':commit['sha'],'force':False});break
  except Exception:
   if attempt==3:raise
   time.sleep(2)
 for path in changed:prior[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
 db.execute("INSERT OR REPLACE INTO meta VALUES('published_hashes',?)",(json.dumps(prior),));db.commit()
 print('Published Lyzem parts',len(changed),'commit',commit['sha'],flush=True)
 return len(changed)

def main():
 p=argparse.ArgumentParser();p.add_argument('--work-dir',type=Path,required=True);p.add_argument('--repo-root',type=Path,default=ROOT);p.add_argument('--repo');p.add_argument('--max-requests',type=int,default=0);p.add_argument('--daily-cap',type=int,default=0);a=p.parse_args()
 if a.max_requests<0 or a.daily_cap<0:p.error('Invalid limits')
 lock=a.work_dir/'worker.lock';a.work_dir.mkdir(parents=True,exist_ok=True)
 with lock.open('a+')as handle:
  import filelock as fcntl
  fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
  db=connect(a.work_dir,a.repo_root)
  if db.execute("SELECT 1 FROM meta WHERE key='stop_reason' AND value!=''").fetchone():raise SystemExit('Lyzem source hold retained')
  known=known_channels(a.repo_root);known.update(row[0]for row in db.execute('SELECT username FROM records'))
  day=datetime.now(timezone.utc).date().isoformat()
  if a.daily_cap and db.execute('SELECT COUNT(*) FROM requests WHERE day=?',(day,)).fetchone()[0]>=a.daily_cap:raise SystemExit('Lyzem daily request cap reached')
  client=Client(BASE,3,resilient=True)
  try:client.initialize()
  except (HTTPError,Challenge,HostBlocked)as exc:
   code=exc.code if isinstance(exc,HTTPError)else'challenge'
   raise
  db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?)',(day,'','robots',0,0,datetime.now(timezone.utc).isoformat()));db.commit()
  made=0
  try:
   while not a.max_requests or made<a.max_requests:
    if budget_status(BASE)['halted']:break
    day=datetime.now(timezone.utc).date().isoformat()
    if a.daily_cap and db.execute('SELECT COUNT(*) FROM requests WHERE day=?',(day,)).fetchone()[0]>=a.daily_cap:
     if a.max_requests:break
     next_day=datetime.combine(datetime.now(timezone.utc).date()+timedelta(days=1),dt_time(0,0),timezone.utc)
     wait=max(1,(next_day-datetime.now(timezone.utc)).total_seconds())
     print('Lyzem daily cap reached; next UTC day in',round(wait),'seconds',flush=True)
     time.sleep(min(wait,60));continue
    job=db.execute("SELECT term,kind,page FROM jobs WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone()
    if not job:break
    term,kind,page=job
    url=BASE+'/search?'+urlencode({'q':term,'f':kind+'s','per-page':50,'p':page})
    try:
     body=client.get(url)
    except (HTTPError,Challenge,HostBlocked,OSError,TimeoutError)as exc:
     code=exc.code if isinstance(exc,HTTPError)else'challenge' if isinstance(exc,Challenge)else 0
     db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?)',(day,term,kind,page,code if isinstance(code,int)else 0,datetime.now(timezone.utc).isoformat()))
     db.commit();print('Lyzem response',code,flush=True)
     if code in(403,429,'challenge'):break
     continue
    rows=parse(body,kind);new=0
    for username,row in rows.items():
     if username in known:continue
     known.add(username);new+=1
     db.execute('INSERT INTO records VALUES(?,?,?)',(username,json.dumps(row,ensure_ascii=False),day))
    status='done'if page>=5 or new==0 or not rows else'pending'
    db.execute('UPDATE jobs SET page=?,status=?,new_rows=new_rows+? WHERE term=? AND kind=?',(page+1,status,new,term,kind))
    db.execute('INSERT INTO requests VALUES(?,?,?,?,200,?)',(day,term,kind,page,datetime.now(timezone.utc).isoformat()))
    db.commit();made+=1
    print(json.dumps({'term':term,'kind':kind,'page':page,'rows':len(rows),'new':new,'status':status},ensure_ascii=False),flush=True)
    if made%20==0:publish(db,export(db,a.repo_root),a.repo)
   publish(db,export(db,a.repo_root),a.repo)
  finally:db.close()
if __name__=='__main__':main()
