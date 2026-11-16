#!/usr/bin/env python3
"""Checkpointed /fa/ channel and group catalog collector for telegramchannels.me."""
import argparse,base64,fcntl,gzip,hashlib,json,re,sqlite3,time
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from urllib.error import HTTPError
from html_core import Client,Document
from source_budget import Challenge,HostBlocked,status as budget_status
from archive_collect import gh
from lyzem_collect import known_channels

BASE='https://telegramchannels.me'
ROOT=Path(__file__).resolve().parents[1]
USERNAME=re.compile(r'^[A-Za-z][A-Za-z0-9_]{3,31}$')
PERSIAN=re.compile('[\u0600-\u06ff]')

def parse(html,kind,at=None):
 doc=Document(html);at=at or datetime.now(timezone.utc).isoformat();rows={};max_page=1
 for url in doc.links():
  parsed=urlsplit(url)
  if parsed.scheme=='https' and parsed.hostname=='telegramchannels.me' and parsed.path=='/fa/'+kind+'s':
   value=parse_qs(parsed.query).get('page',['1'])[0]
   if value.isdigit():max_page=max(max_page,min(int(value),1000))
 for card in doc.root.nodes():
  if card.tag!='div' or 'media-card' not in card.attrs.get('class','').split():continue
  links=[n for n in card.nodes()if n.tag=='a']
  anchor=next((n for n in links if re.fullmatch(r'https://telegramchannels\.me/fa/'+kind+r's/[A-Za-z][A-Za-z0-9_]{3,31}',n.attrs.get('href',''))),None)
  if not anchor:continue
  username=urlsplit(anchor.attrs['href']).path.rsplit('/',1)[-1].lower()
  if not USERNAME.fullmatch(username):continue
  title=' '.join(anchor.text().split())[:512]
  desc=next((n for n in card.nodes()if 'card-description' in n.attrs.get('class','').split()),None)
  description=' '.join(desc.text().split())[:1000]if desc else''
  subtitle=next((n for n in card.nodes()if 'subtitle' in n.attrs.get('class','').split()),None)
  member_text=' '.join(subtitle.text().split())if subtitle else''
  m=re.search(r'([\d,.]+)\s*([KkMm]?)',member_text)
  members=int(float(m[1].replace(',',''))*{'':1,'k':1000,'m':1000000}[m[2].lower()])if m else None
  rows[username]={'type':kind,'username':username,'title':title,'description':description,'members':members,'category':None,
   'language':'fa'if PERSIAN.search(title+' '+description)else None,'url':anchor.attrs['href'],'collected_at':at}
 return rows,max_page

def connect(work):
 work.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(work/'checkpoint.sqlite3')
 db.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
 CREATE TABLE IF NOT EXISTS jobs(kind TEXT PRIMARY KEY,page INTEGER DEFAULT 1,max_page INTEGER DEFAULT 1,status TEXT DEFAULT 'pending');
 CREATE TABLE IF NOT EXISTS records(username TEXT PRIMARY KEY,payload TEXT,day TEXT);
 CREATE TABLE IF NOT EXISTS requests(day TEXT,kind TEXT,page INTEGER,status INTEGER,rows_found INTEGER,new_rows INTEGER,at TEXT);''')
 for kind in ('channel','group'):db.execute('INSERT OR IGNORE INTO jobs(kind)VALUES(?)',(kind,))
 db.commit();return db

def export(db,root):
 directory=root/'data/tgchannelsme-import';directory.mkdir(exist_ok=True);files=[]
 for day, in db.execute('SELECT DISTINCT day FROM records ORDER BY day'):
  values=db.execute('SELECT payload FROM records WHERE day=? ORDER BY username',(day,)).fetchall()
  for index in range(0,len(values),1000):
   path=directory/f'tgchannelsme-{day.replace("-","")}-{index//1000+1:02d}.jsonl.gz';tmp=path.with_suffix('.tmp')
   with tmp.open('wb')as raw,gzip.GzipFile(fileobj=raw,mode='wb',mtime=0,filename='')as out:
    for payload, in values[index:index+1000]:out.write((payload+'\n').encode('utf-8'))
   tmp.replace(path);files.append(path)
 return files

def publish(db,files,repo):
 if not repo:return 0
 row=db.execute("SELECT value FROM meta WHERE key='published_hashes'").fetchone();prior=json.loads(row[0])if row else{}
 changed=[p for p in files if prior.get(p.name)!=hashlib.sha256(p.read_bytes()).hexdigest()]
 if not changed:return 0
 entries=[]
 for path in changed:
  blob=gh(repo,'git/blobs',{'content':base64.b64encode(path.read_bytes()).decode(),'encoding':'base64'})
  entries.append({'path':'data/tgchannelsme-import/'+path.name,'mode':'100644','type':'blob','sha':blob['sha']})
 for attempt in range(4):
  head=gh(repo,'commits/main');tree=gh(repo,'git/trees',{'base_tree':head['commit']['tree']['sha'],'tree':entries})
  commit=gh(repo,'git/commits',{'message':f'Import {len(changed)} telegramchannels.me data parts','tree':tree['sha'],'parents':[head['sha']]})
  try:gh(repo,'git/refs/heads/main',{'sha':commit['sha'],'force':False});break
  except Exception:
   if attempt==3:raise
   time.sleep(2)
 for path in changed:prior[path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
 db.execute("INSERT OR REPLACE INTO meta VALUES('published_hashes',?)",(json.dumps(prior),));db.commit();return len(changed)

def main():
 p=argparse.ArgumentParser();p.add_argument('--work-dir',type=Path,required=True);p.add_argument('--repo-root',type=Path,default=ROOT);p.add_argument('--repo');p.add_argument('--daily-cap',type=int,default=1500);p.add_argument('--max-requests',type=int,default=0);a=p.parse_args()
 a.work_dir.mkdir(parents=True,exist_ok=True)
 with(a.work_dir/'worker.lock').open('a+')as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);db=connect(a.work_dir)
  if db.execute("SELECT value FROM meta WHERE key='stop_reason' AND value!=''").fetchone():raise SystemExit('telegramchannels.me host hold retained')
  client=Client(BASE,4,resilient=True)
  try:client.initialize()
  except (HTTPError,Challenge,HostBlocked)as exc:
   code=exc.code if isinstance(exc,HTTPError)else'challenge'
   if code in(403,429,'challenge'):
    db.execute("INSERT OR REPLACE INTO meta VALUES('stop_reason',?)",(str(code),));db.commit()
   raise
  day=datetime.now(timezone.utc).date().isoformat()
  db.execute('INSERT INTO requests VALUES(?,?,?,0,0,0,?)',(day,'robots',0,datetime.now(timezone.utc).isoformat()));db.commit()
  known=known_channels(a.repo_root);known.update(u for u, in db.execute('SELECT username FROM records'))
  made=0
  try:
   while not a.max_requests or made<a.max_requests:
    if budget_status(BASE)['halted']:break
    day=datetime.now(timezone.utc).date().isoformat()
    if db.execute('SELECT COUNT(*) FROM requests WHERE day=?',(day,)).fetchone()[0]>=a.daily_cap:break
    job=db.execute("SELECT kind,page,max_page FROM jobs WHERE status='pending' ORDER BY rowid LIMIT 1").fetchone()
    if not job:break
    kind,page,limit=job;url=BASE+'/fa/'+kind+'s'+('?page='+str(page)if page>1 else'')
    try:body=client.get(url)
    except (HTTPError,Challenge,HostBlocked)as exc:
     code=exc.code if isinstance(exc,HTTPError)else'challenge'
     db.execute('INSERT INTO requests VALUES(?,?,?,?,0,0,?)',(day,kind,page,code if isinstance(code,int)else 0,datetime.now(timezone.utc).isoformat()))
     if code in(403,429,'challenge'):db.execute("INSERT OR REPLACE INTO meta VALUES('stop_reason',?)",(str(code),))
     db.commit();break
    rows,max_page=parse(body,kind);new=0
    for username,row in rows.items():
     if username in known:continue
     known.add(username);new+=1;db.execute('INSERT INTO records VALUES(?,?,?)',(username,json.dumps(row,ensure_ascii=False),day))
    limit=max(limit,max_page)
    state='done'if page>=limit or not rows else'pending'
    db.execute('UPDATE jobs SET page=?,max_page=?,status=? WHERE kind=?',(page+1,limit,state,kind))
    db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?)',(day,kind,page,200,len(rows),new,datetime.now(timezone.utc).isoformat()))
    db.commit();made+=1
    print(json.dumps({'kind':kind,'page':page,'max_page':limit,'rows':len(rows),'new':new,'state':state}),flush=True)
    if made%20==0:publish(db,export(db,a.repo_root),a.repo)
   publish(db,export(db,a.repo_root),a.repo)
  finally:db.close()
if __name__=='__main__':main()
