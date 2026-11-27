#!/usr/bin/env python3
"""One-off public Iran TGStat catalog collector. public HTML and optional environment-only session Show more."""
import argparse
import sys
from datetime import datetime, timezone
import filelock as fcntl
import gzip
import hashlib
from http.cookiejar import CookieJar
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import time
from urllib.error import HTTPError, URLError
from http.client import HTTPException
from urllib.parse import urljoin, urlsplit, urlunsplit, unquote, urlencode
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPRedirectHandler

from html_core import Document, Robots
from source_budget import reserve, report as budget_report, Challenge, HostBlocked, is_challenge, retry_after_seconds

BASE='https://ir.tgstat.com'
ROOT=Path(__file__).resolve().parents[1]
PROFILE=re.compile(r'^/(channel|chat)/@([A-Za-z][A-Za-z0-9_]{3,31})/?$')
LINK_PROFILE=re.compile(r'^/(channel|chat)/@([A-Za-z][A-Za-z0-9_]{3,31})(?:/stat)?/?$')
RANK=re.compile(r'^/ratings/(channels|chats)(?:/([a-z-]+))?/?$')
BLOCKED=re.compile(r'/(?:stat|quotes|share)(?:/|$)')
UA='IranAdsCollector/1.0'
CATALOG_ITEMS=re.compile(r'^/([a-z-]+)/items$')
_SECRET_FILTER=None

def safe_json(value):
    raw=json.dumps(value,ensure_ascii=False)
    return _SECRET_FILTER.clean(raw)if _SECRET_FILTER else raw

def safe_error(exc):
    value=type(exc).__name__+': '+str(exc)
    return _SECRET_FILTER.clean(value)if _SECRET_FILTER else value


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
    def __init__(self,db,session=None,final_plan=False):
        global _SECRET_FILTER
        self.final_plan=final_plan
        self.db=db;self.rules=[];self.delay=4;self.session=session
        if session:_SECRET_FILTER=session.filter
        self.opener=build_opener(NoRedirect(),HTTPCookieProcessor(CookieJar()))
        # Session cookies are never installed in the anonymous opener's cookie jar.
        self.session_opener=build_opener(NoRedirect())
    def get(self,url,robots=False):return self.request(url,robots=robots)
    def known_profile(self,url):
        m=PROFILE.fullmatch(urlsplit(url).path)
        if not m:
            return bool(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='tgstat_unresolved'").fetchone()and self.db.execute('SELECT 1 FROM tgstat_unresolved WHERE url=?',(url,)).fetchone())
        key=BASE+'/'+m[1]+'/@'+m[2].lower()
        return bool(self.db.execute("SELECT 1 FROM queue WHERE url=? AND kind='profile'",(key,)).fetchone()or self.db.execute('SELECT 1 FROM records WHERE username=?',(m[2].lower(),)).fetchone())
    def request(self,url,robots=False,data=None,public_discovery=False):
        from tgstat_session import endpoint_allowed,account_problem
        method='POST'if data is not None else'GET'
        profile_alias=None  # Proven canonical destination, confined to this request chain.
        for _ in range(6):
            if _SECRET_FILTER and _SECRET_FILTER.clean(url)!=url:raise AccessStop('Credential reflection in a URL; source paused')
            authenticated=bool(self.session and not robots and endpoint_allowed(url,method))
            if robots:
                if url not in(BASE+'/robots.txt','https://tgstat.com/robots.txt'):raise ValueError('Unexpected robots redirect')
            elif method=='POST':
                match=CATALOG_ITEMS.fullmatch(urlsplit(url).path)
                if not authenticated or not match or not self.db.execute('SELECT 1 FROM categories WHERE slug=?',(match[1],)).fetchone():raise ValueError('Unapproved catalog request')
                if urlsplit(url).netloc!='ir.tgstat.com' or not self.rules or not all(r.allowed(url)for r in self.rules)or BLOCKED.search(urlsplit(url).path):raise ValueError('Catalog request is out of scope or robots-disallowed')
            elif not(scoped(url)or(public_discovery and url in('https://tgstat.com/','https://tgstat.com/channels/search')))or not self.rules or not all(r.allowed(url)for r in self.rules):raise ValueError('Out of scope or robots-disallowed URL')
            if authenticated and method=='GET'and not(self.known_profile(url)or url==profile_alias):raise ValueError('Profile has no Iran-list provenance')
            reserve('https://tgstat.com',self.delay)
            headers={'User-Agent':UA,'Accept':'text/html,application/json'if method=='POST'else'text/html,text/plain'}
            if method=='POST':headers.update({'Content-Type':'application/x-www-form-urlencoded','X-Requested-With':'XMLHttpRequest','Referer':BASE+'/'+match[1]})
            request=Request(url,data=data,headers=headers)
            if authenticated:
                try:self.session.attach(request)
                except ValueError:raise AccessStop('TGStat session cookies are unavailable or expired')from None
            attempt=self.db.execute('INSERT INTO requests(url,status,at)VALUES(?,?,?)',(url,0,now())).lastrowid;self.db.commit()
            from source_budget import report as budget_report
            try:response=(self.session_opener if authenticated else self.opener).open(request,timeout=30)
            except HTTPError as exc:
                budget_report('https://tgstat.com',exc.code,retry_after=retry_after_seconds(exc.headers))
                self.db.execute('UPDATE requests SET status=?WHERE id=?',(exc.code,attempt));self.db.commit()
                if self.final_plan and exc.code in(403,429):
                    try:error_body=exc.read(100000).decode('utf-8','replace')
                    except Exception:error_body=''
                    if authenticated and not is_challenge(error_body):
                        from tgstat_session import account_problem
                        problem=account_problem(Document(error_body),fragment=True)
                        if problem:raise AccessStop(problem)from None
                    continue
                if exc.code==401:raise AccessStop('HTTP 401: account logged out')from None
                if exc.code in(403,429):raise
                if exc.code in(301,302,303,307,308):
                    target=canonical(urljoin(url,exc.headers.get('Location','')))
                    if authenticated:
                        # Iran catalog private IDs may canonicalize to a public handle.
                        # Grant provenance only for that exact same-host, same-type GET.
                        public=PROFILE.fullmatch(urlsplit(target).path)
                        source_path=urlsplit(url).path
                        unresolved=(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='tgstat_unresolved'").fetchone()
                                    and self.db.execute('SELECT 1 FROM tgstat_unresolved WHERE url=?',(url,)).fetchone())
                        canonical_profile=(method=='GET' and endpoint_allowed(target,'GET') and public
                                           and not PROFILE.fullmatch(source_path) and unresolved
                                           and source_path.startswith('/'+public[1]+'/'))
                        if canonical_profile:profile_alias=target
                        if method=='POST'or not endpoint_allowed(target,'GET')or not(self.known_profile(target)or target==profile_alias):
                            raise AccessStop('Unexpected authenticated redirect; source paused')from None
                    url=target;continue
                if robots and exc.code in(404,410):return ''
                if authenticated:
                    try:problem=account_problem(Document(exc.read(100000).decode('utf-8','replace')),fragment=True)
                    except Exception:problem=None
                    if problem:raise AccessStop(problem)from None
                raise
            except(OSError,TimeoutError):
                budget_report('https://tgstat.com',0);raise
            with response:
                self.db.execute('UPDATE requests SET status=?WHERE id=?',(response.status,attempt));self.db.commit()
                content_type=response.headers.get('Content-Type','').lower()
                if not robots and not any(t in content_type for t in('text/html','application/xhtml+xml','application/json')if method=='POST'or t!='application/json'):raise ValueError('Unexpected response content type')
                try:raw=response.read(10_000_001)
                except(OSError,TimeoutError,HTTPException):budget_report('https://tgstat.com',0);raise
                if len(raw)>10_000_000:raise ValueError('Page over 10 MB')
                body=raw.decode(response.headers.get_content_charset()or'utf-8',errors='replace')
                if is_challenge(body):
                    budget_report('https://tgstat.com','challenge')
                    if self.final_plan:raise Challenge('Challenge response; waiting for host pause')
                    raise AccessStop('Cloudflare challenge; source paused')
                if authenticated:
                    content=body
                    if method=='POST'and(body.lstrip().startswith('{')or'application/json'in content_type):
                        try:
                            envelope=json.loads(body)
                            if isinstance(envelope,dict)and envelope.get('status')!='ok'and any('captcha'in str(envelope.get(k,'')).lower()for k in('error','code','type')):
                                budget_report('https://tgstat.com','challenge')
                                if self.final_plan:raise Challenge('TGStat CAPTCHA challenge; shared cooldown active')
                                raise AccessStop('TGStat CAPTCHA challenge')
                            if isinstance(envelope,dict)and isinstance(envelope.get('html'),str):content=envelope['html']
                        except ValueError:pass
                    doc=Document(content);self.session.receive(response,request,doc)
                    problem=account_problem(doc,fragment=method=='POST')
                    if problem:
                        budget_report('https://tgstat.com',response.status);raise AccessStop(problem)
                    if method=='POST'and not('application/json'in content_type or body.lstrip().startswith('{')):raise AccessStop('Catalog returned an unexpected page; session check required')
                budget_report('https://tgstat.com',response.status)
                return body
        raise ValueError('Redirect limit exceeded')
    def initialize(self):
        for u in('https://tgstat.com/robots.txt',BASE+'/robots.txt'):
            while True:
                try:r=Robots(self.get(u,robots=True));break
                except HostBlocked:raise
                except(URLError,TimeoutError,ConnectionError,HTTPException,Challenge):
                    if not self.final_plan:raise
            self.rules.append(r);self.delay=max(self.delay,r.delay)
    def post_catalog(self,category,peer_type,page,offset,sort='members'):
        if not self.session:raise AccessStop('TGSTAT_COOKIE is required for Show more')
        if self.final_plan and not self.session.csrf:
            if not self.session.csrf_from_cookie():raise AccessStop('Session CSRF cookie is unavailable; no profile bootstrap is permitted')
        if not self.session.csrf or not self.session.csrf_param:
            candidates=self.db.execute("SELECT url FROM queue WHERE kind='profile'ORDER BY status='done' DESC,url LIMIT 1").fetchone()
            if not candidates:raise AccessStop('No approved Iran profile is available for session validation')
            self.get(candidates[0])
        if not self.session.csrf or self.session.csrf_param!='_tgstat_csrk':raise AccessStop('Session CSRF token could not be verified')
        if peer_type not in('channel','chat')or page<0 or offset<0:raise ValueError('Invalid catalog cursor')
        fields={'peer_type':peer_type,'page':str(page),'offset':str(offset),'sort_channel':sort if peer_type=='channel'else'members','sort_chat':sort if peer_type=='chat'else'members',self.session.csrf_param:self.session.csrf}
        return self.request(BASE+'/'+category+'/items',data=urlencode(fields).encode())


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
    CREATE TABLE IF NOT EXISTS catalog_pages(category TEXT,peer_type TEXT,page INTEGER,offset INTEGER,status TEXT DEFAULT 'pending',rows INTEGER,has_more INTEGER,next_page INTEGER,next_offset INTEGER,error TEXT,fingerprint TEXT,PRIMARY KEY(category,peer_type,page,offset));
    CREATE TABLE IF NOT EXISTS catalog_seen(category TEXT,peer_type TEXT,username TEXT,PRIMARY KEY(category,peer_type,username));
    CREATE INDEX IF NOT EXISTS queue_order ON queue(status,kind,entity_type,url);
    ''')
    if 'fingerprint'not in{v[1]for v in db.execute('PRAGMA table_info(catalog_pages)')}:db.execute('ALTER TABLE catalog_pages ADD COLUMN fingerprint TEXT')
    db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',('started_at',now()))
    for u in ('/', '/ratings/channels', '/ratings/chats'): enqueue(db,u,'listing')
    db.commit();return db


def enqueue(db,url,kind=None,category=None):
    url=canonical(url);p=urlsplit(url)
    if _SECRET_FILTER and _SECRET_FILTER.clean(url)!=url:return
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
    continuation='form_show_more_not_followed' if forms else 'no_public_pagination_links'
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
    description=next((text(n)for n in doc.root.nodes()if n.tag=='p'and 'card-text'in classes(n)and text(n)),None)or metadata.get('og:description')or metadata.get('description')or''
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


def seed_catalog_pages(db):
    for(category,)in db.execute('SELECT slug FROM categories').fetchall():
        if BLOCKED.search('/'+category+'/items'):continue
        for peer in('channel','chat'):
            db.execute('INSERT OR IGNORE INTO catalog_pages(category,peer_type,page,offset)VALUES(?,?,0,0)',(category,peer))
    db.commit()


def catalog_payload(body):
    try:value=json.loads(body)
    except(ValueError,TypeError):raise AccessStop('Catalog response is not the expected pagination envelope')from None
    if not isinstance(value,dict)or value.get('status')!='ok':raise AccessStop('Catalog reported an error; session/account review required')
    if any(value.get(k)for k in('warning','accountWarning','limited','isLimited','blocked')):raise AccessStop('Account warning or limit detected')
    if not isinstance(value.get('html'),str)or type(value.get('hasMore'))not in(bool,int)or value['hasMore']not in(True,False,0,1):raise AccessStop('Malformed catalog response; source paused')
    def cursor(k):
        v=value.get(k)
        if type(v)is int and v>=0:return v
        if isinstance(v,str)and re.fullmatch(r'\d{1,12}',v):return int(v)
        raise AccessStop('Catalog cursor is missing or invalid; source paused')
    return value['html'],bool(value['hasMore']),cursor('nextPage'),cursor('nextOffset')


def run_catalog_page(db,client,category=None):
    sql="SELECT category,peer_type,page,offset FROM catalog_pages WHERE status='pending'"
    args=[]
    if category is not None:sql+='AND category=?';args.append(category)
    job=db.execute(sql+' ORDER BY page,offset,category,peer_type LIMIT 1',args).fetchone()
    if not job:return False
    category,peer,page,offset=job
    try:
        body=client.post_catalog(category,peer,page,offset)
        html,more,next_page,next_offset=catalog_payload(body)
        from tgstat_session import account_problem
        doc=Document(html);problem=account_problem(doc,fragment=True)
        if problem:raise AccessStop(problem)
        if more and(next_page,next_offset)==(page,offset):raise AccessStop('Catalog cursor repeated; source paused')
        if more and db.execute('SELECT 1 FROM catalog_pages WHERE category=?AND peer_type=?AND page=?AND offset=?',(category,peer,next_page,next_offset)).fetchone():raise AccessStop('Catalog cursor cycle detected; source paused')
        fingerprint=hashlib.sha256((_SECRET_FILTER.clean(text(doc.root))if _SECRET_FILTER else text(doc.root)).encode()).hexdigest()
        if more and db.execute("SELECT 1 FROM catalog_pages WHERE category=?AND peer_type=?AND status='done'AND fingerprint=?",(category,peer,fingerprint)).fetchone():raise AccessStop('Catalog page content repeated; source paused')
        if more and not html.strip():raise AccessStop('Catalog returned an empty continuation; source paused')
        handles=set();rows=0
        for n in doc.root.nodes():
            if n.tag!='a'or 'href'not in n.attrs:continue
            u=canonical(n.attrs['href']);p=urlsplit(u);m=LINK_PROFILE.fullmatch(p.path)
            if _SECRET_FILTER and _SECRET_FILTER.clean(u)!=u:continue
            if p.netloc=='ir.tgstat.com'and re.fullmatch('/'+peer+r'/[^/]+(?:/stat)?/?',p.path):rows+=1
            if p.netloc=='ir.tgstat.com'and m and m[1]==peer:
                handles.add(m[2].lower());enqueue(db,u,category=category)
        old=db.execute('SELECT COUNT(*)FROM catalog_seen WHERE category=?AND peer_type=?',(category,peer)).fetchone()[0]
        db.executemany('INSERT OR IGNORE INTO catalog_seen VALUES(?,?,?)',[(category,peer,u)for u in handles])
        current=db.execute('SELECT COUNT(*)FROM catalog_seen WHERE category=?AND peer_type=?',(category,peer)).fetchone()[0]
        db.execute("UPDATE catalog_pages SET status='done',rows=?,has_more=?,next_page=?,next_offset=?,error=NULL,fingerprint=? WHERE category=?AND peer_type=?AND page=?AND offset=?",(rows,int(more),next_page,next_offset,fingerprint,*job))
        if more:db.execute('INSERT INTO catalog_pages(category,peer_type,page,offset)VALUES(?,?,?,?)',(category,peer,next_page,next_offset))
        db.commit();return True
    except AccessStop:
        db.rollback();raise
    except(HTTPError,ValueError,URLError,TimeoutError,ConnectionError)as exc:
        db.rollback();db.execute("UPDATE catalog_pages SET status='failed',error=?WHERE category=?AND peer_type=?AND page=?AND offset=?",(safe_error(exc),*job));db.commit();return True


def catalog_coverage(db):
    result=[]
    for category,peer in db.execute('SELECT DISTINCT category,peer_type FROM catalog_pages ORDER BY category,peer_type').fetchall():
        pages=db.execute('SELECT page,offset,status,rows,has_more,next_page,next_offset FROM catalog_pages WHERE category=?AND peer_type=?ORDER BY page,offset',(category,peer)).fetchall()
        result.append({'category':category,'type':'group'if peer=='chat'else'channel','pages_fetched':sum(v[2]=='done'for v in pages),'rows_per_page':[v[3]for v in pages if v[2]=='done'],'unique_handles':db.execute('SELECT COUNT(*)FROM catalog_seen WHERE category=?AND peer_type=?',(category,peer)).fetchone()[0],'end_reached':any(v[2]=='done'and v[4]==0 for v in pages),'pending':sum(v[2]=='pending'for v in pages),'failed':sum(v[2]=='failed'for v in pages)})
    return result


def export(db,work):
    out=work/'exports';out.mkdir(exist_ok=True);files=[]
    for i in range(16):
        rows=[_SECRET_FILTER.clean(p)if _SECRET_FILTER else p for u,p in db.execute('SELECT username,payload FROM records ORDER BY username')if int(hashlib.sha256(u.encode()).hexdigest()[0],16)==i]
        raw=gzip.compress(('\n'.join(rows)+('\n'if rows else'')).encode(),mtime=0);name=f'tgstat-{i:02x}.jsonl.gz';temp=out/(name+'.tmp');temp.write_bytes(raw);temp.replace(out/name)
        files.append({'name':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    types={}
    fields={}
    for p, in db.execute('SELECT payload FROM records'):
        row=json.loads(p);types[row['type']]=types.get(row['type'],0)+1
        for k,v in row.items():
            if v is not None and v!='':fields[k]=fields.get(k,0)+1
    total=db.execute('SELECT COUNT(*)FROM requests').fetchone()[0]
    report={'schema_version':1,'updated_at':now(),'source':BASE,'records':types,'requests':total,'per_100_requests':{k:round(100*v/total,2)for k,v in types.items()}if total else{},'fields_present':fields,'queue':dict(db.execute('SELECT status,COUNT(*)FROM queue GROUP BY status')),'listings':[dict(zip(('url','category','type','link_occurrences','unique_handles','public_next_links','continuation'),v))for v in db.execute('SELECT * FROM listings ORDER BY category,entity_type')],'files':files,'catalog_coverage':catalog_coverage(db),'listing_first_coverage':__import__('tgstat_listing').coverage(db)if db.execute("SELECT 1 FROM sqlite_master WHERE name='tgstat_slices'").fetchone()else[],'stop_reason':(db.execute("SELECT value FROM meta WHERE key='stop_reason'").fetchone()or[None])[0],'complete':False,'limitations':['TGStat Iran/Persian labels include Dari groups; region() uses title, description, text_sample.','Public mode does not follow Show more; --session uses the environment session for the approved form only.','No full-directory coverage guarantee; public ranking/category snapshots may be capped.']}
    for item in report['listings']:
        if item['continuation']=='login_gated_show_more': item['continuation']='form_show_more_not_followed'
    temp=out/'manifest.json.tmp';temp.write_text(safe_json(report));temp.replace(out/'manifest.json');return json.loads(safe_json(report))


def publish(db,work,repo):
    from tgstat_session import delivery_gh as gh
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


def run(db,client,pages,inventory=False,authenticated=False):
    turn=int((db.execute("SELECT value FROM meta WHERE key='turn'").fetchone()or['0'])[0])
    for _ in range(pages):
        kinds=[('listing',None),('profile','channel'),('listing',None),('profile','group')]
        if inventory:kinds=[('listing',None)]
        if authenticated and(inventory or turn%2==0):
            if run_catalog_page(db,client):
                turn+=1;db.execute("INSERT OR REPLACE INTO meta VALUES('turn',?)",(str(turn%4),));db.commit();continue
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
                if value:
                    old=db.execute('SELECT payload FROM records WHERE username=?',(value['username'],)).fetchone()
                    if old:
                        previous=json.loads(old[0]);previous.update({k:v for k,v in value.items()if v is not None and v!=''});value=previous
                    db.execute('INSERT OR REPLACE INTO records VALUES(?,?)',(value['username'],safe_json(value)))
            db.execute("UPDATE queue SET status='done',error=NULL WHERE url=?",(url,))
        except AccessStop:
            db.rollback();raise
        except (HTTPError,ValueError,URLError,TimeoutError,ConnectionError)as exc:
            db.execute("UPDATE queue SET status='failed',error=?WHERE url=?",(safe_error(exc),url))
        turn+=1;db.execute("INSERT OR REPLACE INTO meta VALUES('turn',?)",(str(turn%4),));db.commit()



def try_publish(db,work,repo):
    """Delivery outages retain the SQLite outbox and must not become account holds."""
    try:
        publish(db,work,repo)
    except subprocess.CalledProcessError:
        db.execute("INSERT OR REPLACE INTO meta VALUES('github_delivery_pending','1')");db.commit()
        print('GitHub delivery failed; checkpoint retained; retrying after the next slice.',flush=True)
        return False
    db.execute("DELETE FROM meta WHERE key='github_delivery_pending'");db.commit()
    return True


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--final-plan',action='store_true',help='Listing-first complete catalog mode with adaptive retries');p.add_argument('--session',action='store_true',help='Use TGSTAT_COOKIE from environment for approved profiles and Show more only');p.add_argument('--work-dir',type=Path,required=True);p.add_argument('--repo');p.add_argument('--pages-per-slice',type=int,default=25);p.add_argument('--max-slices',type=int,default=1);p.add_argument('--inventory',action='store_true',help='Only public listing inventory; no profile fetching')
    a=p.parse_args()
    if a.pages_per_slice<1 or a.max_slices<0:p.error('Invalid budgets')
    from tgstat_session import Session
    session=Session.from_environment()if a.session else None
    a.work_dir.mkdir(parents=True,exist_ok=True)
    with(a.work_dir/'worker.lock').open('a+')as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);db=connect(a.work_dir)
        try:
            if a.final_plan:
                stop=db.execute("SELECT value FROM meta WHERE key='stop_reason'").fetchone()
                if stop and any(v in stop[0].lower()for v in('account','logged out','cookie','expired','three blocks')):raise AccessStop('Existing account/session stop requires review: '+stop[0])
                if not session:raise AccessStop('Session is required for listing-first TGStat mode')
                __import__('tgstat_listing').ensure(db)
            client=Client(db,session=session,final_plan=a.final_plan);client.initialize();i=0
            if a.final_plan:__import__('tgstat_listing').probe_languages(db,client)
            if session:seed_catalog_pages(db)
            while a.max_slices==0 or i<a.max_slices:
                if session:seed_catalog_pages(db)
                if a.final_plan:
                    for _ in range(a.pages_per_slice):
                        if not __import__('tgstat_listing').step(sys.modules[__name__],db,client):break
                else:run(db,client,a.pages_per_slice,a.inventory,authenticated=a.session)
                report=export(db,a.work_dir);print(json.dumps({'slice':i+1,'records':report['records'],'requests':report['requests'],'per_100_requests':report['per_100_requests'],'queue':report['queue']},ensure_ascii=False),flush=True)
                delivered=try_publish(db,a.work_dir,a.repo)if a.repo else True
                i+=1
                if a.final_plan:
                    if not db.execute("SELECT 1 FROM tgstat_slices WHERE status='pending'LIMIT 1").fetchone()and not db.execute("SELECT 1 FROM tgstat_unresolved WHERE status='pending'LIMIT 1").fetchone():
                        if not delivered and a.max_slices==0:
                            time.sleep(30);continue
                        break
                    continue
                if not db.execute("SELECT 1 FROM queue WHERE status='pending'"+('AND kind=\'listing\''if a.inventory else'')+'LIMIT 1').fetchone()and not(a.session and db.execute("SELECT 1 FROM catalog_pages WHERE status='pending'LIMIT 1").fetchone()):break
        except AccessStop as exc:
            db.execute("INSERT OR REPLACE INTO meta VALUES('stop_reason',?)",(str(exc),));db.commit();raise
        except HTTPError as exc:
            if exc.code in(403,429):
                db.rollback()
                raise SystemExit('TGStat host cooldown') from None
            raise
        except Exception as exc:
            if session:
                reason='Authenticated collection paused due to '+type(exc).__name__
                db.rollback();db.execute("INSERT OR REPLACE INTO meta VALUES('stop_reason',?)",(reason,));db.commit()
                raise RuntimeError(reason)from None
            raise
        finally:db.rollback();export(db,a.work_dir);db.close()

if __name__=='__main__':main()
