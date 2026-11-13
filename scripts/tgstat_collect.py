#!/usr/bin/env python3
"""One-off public Iran TGStat catalog collector. GET-only; no API or login."""
import argparse
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
from http.cookiejar import CookieJar
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit, urlunsplit, unquote
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPRedirectHandler

from html_core import Document, Robots
from source_budget import reserve

BASE='https://ir.tgstat.com'
ROOT=Path(__file__).resolve().parents[1]
PROFILE=re.compile(r'^/(channel|chat)/@([A-Za-z][A-Za-z0-9_]{3,31})/?$')
LINK_PROFILE=re.compile(r'^/(channel|chat)/@([A-Za-z][A-Za-z0-9_]{3,31})(?:/stat)?/?$')
RANK=re.compile(r'^/ratings/(channels|chats)(?:/([a-z-]+))?/?$')
BLOCKED=re.compile(r'/(?:stat|quotes|share)(?:/|$)')
UA='IranAdsCollector/1.0'


def now(): return datetime.now(timezone.utc).isoformat()
def text(node):
    if node.tag in ('script','style','svg','template'): return ''
    return ' '.join(' '.join(text(c) if hasattr(c,'tag') else c for c in node.children).split())
def classes(node): return set(node.attrs.get('class','').split())
def canonical(url):
    p=urlsplit(urljoin(BASE,url))
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),unquote(p.path) or '/',p.query,''))
def scoped(url):
    p=urlsplit(canonical(url))
    return p.scheme=='https' and p.netloc=='ir.tgstat.com' and not BLOCKED.search(p.path) and not any(x in p.path for x in ('/api','/login','/items'))
def number(value):
    value=str(value).translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩','01234567890123456789'))
    value=re.sub(r'[\s,٬]','',value)
    return int(value) if value.isdigit() else None


class AccessStop(RuntimeError): pass
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


class Client:
    def __init__(self,db):
        self.db=db;self.rules=[];self.delay=4
        self.opener=build_opener(NoRedirect(),HTTPCookieProcessor(CookieJar()))
    def get(self,url,robots=False):
        for _ in range(6):
            if robots:
                if url not in (BASE+'/robots.txt','https://tgstat.com/robots.txt'): raise ValueError('Unexpected robots redirect')
            elif not scoped(url) or not self.rules or not all(r.allowed(url) for r in self.rules):
                raise ValueError('Out of scope or robots-disallowed URL')
            reserve('https://tgstat.com',self.delay)
            try:
                response=self.opener.open(Request(url,headers={'User-Agent':UA,'Accept':'text/html,text/plain'}),timeout=30)
            except HTTPError as exc:
                self.db.execute('INSERT INTO requests(url,status,at) VALUES(?,?,?)',(url,exc.code,now()));self.db.commit()
                if exc.code in (401,403,429): raise AccessStop(f'HTTP {exc.code}: source paused') from exc
                if exc.code in (301,302,303,307,308):
                    url=canonical(urljoin(url,exc.headers['Location']));continue
                if robots and exc.code in (404,410): return ''
                raise
            with response:
                self.db.execute('INSERT INTO requests(url,status,at) VALUES(?,?,?)',(url,response.status,now()));self.db.commit()
                kind=response.headers.get('Content-Type','').lower()
                if not robots and not any(t in kind for t in ('text/html','application/xhtml+xml')): raise ValueError('Non-HTML response rejected')
                data=response.read(10_000_001)
                if len(data)>10_000_000: raise ValueError('Page over 10 MB')
                return data.decode(response.headers.get_content_charset() or 'utf-8',errors='replace')
        raise ValueError('Redirect limit exceeded')
    def initialize(self):
        for u in ('https://tgstat.com/robots.txt',BASE+'/robots.txt'):
            r=Robots(self.get(u,robots=True));self.rules.append(r);self.delay=max(self.delay,r.delay)


def connect(work):
    work.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(work/'tgstat.sqlite3')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS queue(url TEXT PRIMARY KEY,kind TEXT,entity_type TEXT,category TEXT,status TEXT DEFAULT 'pending',error TEXT);
    CREATE TABLE IF NOT EXISTS records(username TEXT PRIMARY KEY COLLATE NOCASE,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS categories(slug TEXT PRIMARY KEY,title TEXT);
    CREATE TABLE IF NOT EXISTS listings(url TEXT PRIMARY KEY,category TEXT,entity_type TEXT,rows INTEGER,unique_handles INTEGER,public_next INTEGER,continuation TEXT);
    CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY,url TEXT,status INTEGER,at TEXT);
    CREATE INDEX IF NOT EXISTS queue_order ON queue(status,kind,entity_type,url);
    ''')
    db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',('started_at',now()))
    for u in ('/', '/ratings/channels', '/ratings/chats'): enqueue(db,u,'listing')
    db.commit();return db


def enqueue(db,url,kind=None,category=None):
    url=canonical(url);p=urlsplit(url)
    # /stat links may identify a profile, but statistics URLs themselves are never requested.
    profile=LINK_PROFILE.fullmatch(p.path)
    if profile and p.netloc=='ir.tgstat.com' and p.scheme=='https':
        url=BASE+'/'+profile[1]+'/@'+profile[2].lower();kind='profile';entity='channel' if profile[1]=='channel' else 'group'
    else:
        if not scoped(url): return
        rank=RANK.fullmatch(p.path)
        if p.path=='/':kind='listing';entity=None
        elif rank:
            if rank[2] and not db.execute('SELECT 1 FROM categories WHERE slug=?',(rank[2],)).fetchone(): return
            url=BASE+p.path.rstrip('/');kind='listing';entity='channel' if rank[1]=='channels' else 'group';category=rank[2]
        elif re.fullmatch('/[a-z-]+',p.path) and db.execute('SELECT 1 FROM categories WHERE slug=?',(p.path[1:],)).fetchone():
            url=BASE+p.path;kind='listing';entity='channel';category=p.path[1:]
        else:return
        # Only actual page links are followed; never invent offset/filter URLs or use AJAX APIs.
        if p.query:
            if not re.fullmatch(r'(?:page=\d+|sort=(?:members|mau))',p.query):return
            if p.query.startswith('page='):url+='?'+p.query
    if not scoped(url):return
    db.execute('INSERT OR IGNORE INTO queue(url,kind,entity_type,category) VALUES(?,?,?,?)',(url,kind,entity,category))


def listing(db,doc,url):
    # Category taxonomy comes from Iran ranking navigation, not arbitrary outbound links.
    for n in doc.root.nodes():
        if n.tag!='a' or 'href'not in n.attrs:continue
        candidate=canonical(n.attrs['href']);p=urlsplit(candidate);m=RANK.fullmatch(p.path)
        if p.netloc=='ir.tgstat.com' and m and m[2] and m[2] not in ('public','private'):
            db.execute('INSERT OR IGNORE INTO categories VALUES(?,?)',(m[2],text(n)))
    for slug, in db.execute('SELECT slug FROM categories').fetchall():
        for path in ('/'+slug,'/ratings/chats/'+slug): enqueue(db,path,category=slug)
    seen=set();raw=0;next_pages=0
    for n in doc.root.nodes():
        if n.tag!='a' or 'href'not in n.attrs:continue
        candidate=canonical(n.attrs['href']);p=urlsplit(candidate);m=LINK_PROFILE.fullmatch(p.path)
        if p.netloc=='ir.tgstat.com' and m:
            raw+=1;seen.add((m[1],m[2].lower()));enqueue(db,candidate)
        elif p.netloc=='ir.tgstat.com' and 'page='in p.query and p.path==urlsplit(url).path:
            enqueue(db,candidate);next_pages+=1
    forms=[n for n in doc.root.nodes()if n.tag=='form' and n.attrs.get('method','').upper()=='POST' and n.attrs.get('action','').endswith('/items')]
    continuation='login_gated_show_more' if forms else 'no_public_pagination_links'
    rank=RANK.fullmatch(urlsplit(url).path);category=rank[2] if rank else urlsplit(url).path.strip('/') or 'all'
    entity='group' if rank and rank[1]=='chats' else 'channel'
    db.execute('INSERT OR REPLACE INTO listings VALUES(?,?,?,?,?,?,?)',(url,category or 'all',entity,raw,len(seen),next_pages,continuation))
    if not raw and not any(n.tag=='h1'for n in doc.root.nodes()): raise ValueError('Listing layout missing; possible sign-in/challenge page')


def extract(doc,url):
    match=PROFILE.fullmatch(urlsplit(url).path)
    if not match:raise ValueError('Invalid profile URL')
    title=next((text(n)for n in doc.root.nodes()if n.tag=='h1'),'')
    if not title:raise ValueError('Profile heading missing; possible sign-in/challenge page')
    metadata={n.attrs.get('property',n.attrs.get('name','')):n.attrs.get('content','')for n in doc.root.nodes()if n.tag=='meta'}
    description=metadata.get('og:description')or metadata.get('description')or''
    parents={id(c):n for n in doc.root.nodes()for c in n.children if hasattr(c,'tag')}
    country=language=category=None;metrics={};samples=[]
    for n in doc.root.nodes():
        label=text(n).strip().rstrip(':');parent=parents.get(id(n))
        if parent is None:continue
        if n.tag in ('h5','b') and 'geo and language' in label.lower():
            value=text(parent).replace(text(n),'',1).strip();parts=[p.strip()for p in value.split(',')]
            if len(parts)>=2:country,language=parts[0],parts[1]
        elif n.tag in ('h5','b')and label.lower()=='category':category=text(parent).replace(text(n),'',1).strip()or None
        if label.lower()in ('subscribers','participants','members','average post reach','average reach','err','engagement rate by reach'):
            values=[text(c)for c in parent.children if hasattr(c,'tag')and c is not n and c.tag in('h2','h3','h4','span','div')]
            if values:metrics[label.lower()]=values[0]
        if 'post-text'in classes(n):
            value=text(n)
            if value and value not in samples:samples.append(value)
    if country and country.lower()not in('iran','ir','ایران')and (language or'').lower()not in('persian','farsi','fa','فارسی'):return None
    members=next((metrics[k]for k in('subscribers','participants','members')if k in metrics),None)
    return {'type':'channel'if match[1]=='channel'else'group','username':match[2].lower(),'title':title,'description':description,
            'members':number(members) if number(members)is not None else members,
            'avg_reach':metrics.get('average post reach',metrics.get('average reach')),
            'err':metrics.get('err',metrics.get('engagement rate by reach')),'category':category,'country':country,'language':language,
            'text_sample':'\n'.join(samples)[:1000],'url':url,'collected_at':now()}


def export(db,work):
    out=work/'exports';out.mkdir(exist_ok=True);files=[]
    for i in range(16):
        rows=[p for u,p in db.execute('SELECT username,payload FROM records ORDER BY username')if int(hashlib.sha256(u.encode()).hexdigest()[0],16)==i]
        raw=gzip.compress(('\n'.join(rows)+('\n'if rows else'')).encode(),mtime=0);name=f'tgstat-{i:02x}.jsonl.gz';temp=out/(name+'.tmp');temp.write_bytes(raw);temp.replace(out/name)
        files.append({'name':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    types={}
    fields={}
    for p, in db.execute('SELECT payload FROM records'):
        row=json.loads(p);types[row['type']]=types.get(row['type'],0)+1
        for k,v in row.items():
            if v is not None and v!='':fields[k]=fields.get(k,0)+1
    total=db.execute('SELECT COUNT(*)FROM requests').fetchone()[0]
    report={'schema_version':1,'updated_at':now(),'source':BASE,'records':types,'requests':total,'per_100_requests':{k:round(100*v/total,2)for k,v in types.items()}if total else{},'fields_present':fields,'queue':dict(db.execute('SELECT status,COUNT(*)FROM queue GROUP BY status')),'listings':[dict(zip(('url','category','type','link_occurrences','unique_handles','public_next_links','continuation'),v))for v in db.execute('SELECT * FROM listings ORDER BY category,entity_type')],'files':files,'complete':False,'limitations':['TGStat Iran/Persian labels include Dari groups; region() uses title, description, text_sample.','Catalog Show more requires sign-in in live test; not accessed by this GET-only worker.','No full-directory coverage guarantee; public ranking/category snapshots may be capped.']}
    temp=out/'manifest.json.tmp';temp.write_text(json.dumps(report,ensure_ascii=False,indent=2));temp.replace(out/'manifest.json');return report


def publish(db,work,repo):
    from archive_collect import gh
    import base64,subprocess
    out=work/'exports';manifest=json.loads((out/'manifest.json').read_text());entries=[];saved=[]
    for item in manifest['files']:
        key='published_'+item['name'];old=db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        if old and old[0]==item['sha256']:continue
        raw=(out/item['name']).read_bytes();blob=gh(repo,'git/blobs',{'content':base64.b64encode(raw).decode(),'encoding':'base64'})
        entries.append({'path':'data/tgstat-import/'+item['name'],'mode':'100644','type':'blob','sha':blob['sha']});saved.append((key,item['sha256']))
    entries.append({'path':'data/tgstat-import/manifest.json','mode':'100644','type':'blob','content':json.dumps(manifest,ensure_ascii=False,indent=2)})
    for attempt in range(4):
        head=gh(repo,'commits/main');tree=gh(repo,'git/trees',{'base_tree':head['commit']['tree']['sha'],'tree':entries});commit=gh(repo,'git/commits',{'message':'Import public Iran TGStat channels and groups','tree':tree['sha'],'parents':[head['sha']]})
        try:gh(repo,'git/refs/heads/main',{'sha':commit['sha'],'force':False});break
        except subprocess.CalledProcessError:
            if attempt==3:raise
            time.sleep(2)
    db.executemany('INSERT OR REPLACE INTO meta VALUES(?,?)',saved);db.commit();print('Published TGStat: '+commit['sha'],flush=True)


def run(db,client,pages,inventory=False):
    turn=int((db.execute("SELECT value FROM meta WHERE key='turn'").fetchone()or['0'])[0])
    for _ in range(pages):
        kinds=[('listing',None),('profile','channel'),('listing',None),('profile','group')]
        if inventory:kinds=[('listing',None)]
        job=None
        for i in range(len(kinds)):
            kind,entity=kinds[(turn+i)%len(kinds)]
            sql="SELECT url,kind FROM queue WHERE status='pending'AND kind=?";args=[kind]
            if entity:sql+='AND entity_type=?';args.append(entity)
            job=db.execute(sql+' ORDER BY url LIMIT 1',args).fetchone()
            if job:break
        if not job:return
        url,kind=job
        try:
            body=client.get(url);doc=Document(body)
            if kind=='listing':listing(db,doc,url)
            else:
                value=extract(doc,url)
                if value:db.execute('INSERT OR IGNORE INTO records VALUES(?,?)',(value['username'],json.dumps(value,ensure_ascii=False)))
            db.execute("UPDATE queue SET status='done',error=NULL WHERE url=?",(url,))
        except AccessStop:
            db.rollback();raise
        except (HTTPError,ValueError)as exc:
            db.execute("UPDATE queue SET status='failed',error=?WHERE url=?",(str(exc),url))
        turn+=1;db.execute("INSERT OR REPLACE INTO meta VALUES('turn',?)",(str(turn%4),));db.commit()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--work-dir',type=Path,required=True);p.add_argument('--repo');p.add_argument('--pages-per-slice',type=int,default=25);p.add_argument('--max-slices',type=int,default=1);p.add_argument('--inventory',action='store_true',help='Only public listing inventory; no profile fetching')
    a=p.parse_args()
    if a.pages_per_slice<1 or a.max_slices<0:p.error('Invalid budgets')
    a.work_dir.mkdir(parents=True,exist_ok=True)
    with(a.work_dir/'worker.lock').open('a+')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);db=connect(a.work_dir)
        try:
            client=Client(db);client.initialize();i=0
            while a.max_slices==0 or i<a.max_slices:
                run(db,client,a.pages_per_slice,a.inventory);report=export(db,a.work_dir);print(json.dumps({'slice':i+1,'records':report['records'],'requests':report['requests'],'per_100_requests':report['per_100_requests'],'queue':report['queue']},ensure_ascii=False),flush=True)
                if a.repo:publish(db,a.work_dir,a.repo)
                i+=1
                if not db.execute("SELECT 1 FROM queue WHERE status='pending'"+('AND kind=\'listing\''if a.inventory else'')+'LIMIT 1').fetchone():break
        except AccessStop as exc:
            db.execute("INSERT OR REPLACE INTO meta VALUES('stop_reason',?)",(str(exc),));db.commit();raise
        finally:db.rollback();export(db,a.work_dir);db.close()

if __name__=='__main__':main()
