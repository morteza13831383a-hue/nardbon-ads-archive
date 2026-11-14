"""Finite Persian HTML listing slices, then advertiser and profile discovery."""
import hashlib,json,re
from urllib.parse import urlsplit,parse_qs,urlencode
from html_core import Document

RANGES=[(0,100),(100,500),(500,1000),(1000,5000),(5000,20000),(20000,None)]
ALLOWED={'category','niche','sort','order','minMembers','maxMembers','sponsored'}

def ensure(core,db):
    db.executescript('''CREATE TABLE IF NOT EXISTS spy_slices(key TEXT PRIMARY KEY,filters TEXT,status TEXT DEFAULT 'pending',page INTEGER DEFAULT 1,pages INTEGER DEFAULT 0,new_records INTEGER DEFAULT 0,error TEXT);
    CREATE TABLE IF NOT EXISTS spy_slice_pages(key TEXT,page INTEGER,fingerprint TEXT,rows INTEGER,new_records INTEGER,PRIMARY KEY(key,page));
    CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY,url TEXT,status INTEGER,at TEXT);
    CREATE TABLE IF NOT EXISTS spy_controls(name TEXT PRIMARY KEY,verdict TEXT,evidence TEXT);''')
    if 'via'not in {r[1]for r in db.execute('PRAGMA table_info(requests)')}:db.execute("ALTER TABLE requests ADD COLUMN via TEXT DEFAULT 'channel_collection'")
    for sort in('members','ads','new'):
        for order in('desc','asc'):add(db,{'sort':sort,'order':order})
    for lo,hi in RANGES:
        f={'minMembers':str(lo)}
        if hi is not None:f['maxMembers']=str(hi)
        add(db,f)
    for name,value in(('niche','vpn'),('sponsored','eligible')):add(db,{name:value})
    add(db,{'minMembers':'1000000000'})
    __import__('channel_priority').ensure(core,db)
    db.commit()


def add(db,filters):
    f={k:str(v)for k,v in filters.items()if k in ALLOWED and v is not None}
    if f.get('sort')=='members':f.pop('sort')
    if f.get('order')=='desc':f.pop('order')
    raw=json.dumps(f,sort_keys=True);key=hashlib.sha256(raw.encode()).hexdigest()
    db.execute('INSERT OR IGNORE INTO spy_slices(key,filters)VALUES(?,?)',(key,raw));return key


def url(core,filters,page=1):
    params={'lang':'fa',**filters}
    if page>1:params['page']=str(page)
    return core.BASE+'/channels?'+urlencode(params)


def discover(db,doc):
    found={}
    for n in doc.root.nodes():
        if n.tag!='a':continue
        p=urlsplit(n.attrs.get('href',''))
        if p.path!='/channels':continue
        q=parse_qs(p.query)
        for key in('category','niche','sort','sponsored'):
            if key in q:found.setdefault(key,set()).add(q[key][0])
    categories=found.get('category',set())
    sorts={'members','ads','new'}|found.get('sort',set())
    for category in categories:
        for sort in sorts:
            for order in('desc','asc'):add(db,{'category':category,'sort':sort,'order':order})
    for niche in found.get('niche',set()):add(db,{'niche':niche})
    for sponsored in found.get('sponsored',set()):add(db,{'sponsored':sponsored})
    return found


def persist(core,db,rows):
    new=0
    for row in rows:
        previous=db.execute('SELECT payload FROM channels WHERE username=?',(row['username'],)).fetchone()
        if not previous:new+=1;value=row
        else:
            value=json.loads(previous[0]);updates={k:v for k,v in row.items()if v is not None and v not in('',[])and k not in('description','text_sample')}
            if __import__('channel_priority').has_profile(value)and row.get('record_detail')=='listing':
                for key in ('record_detail','source_url','channel_url','classification'):updates.pop(key,None)
            value.update(updates)
        db.execute('INSERT OR REPLACE INTO channels VALUES(?,?)',(row['username'],json.dumps(value,ensure_ascii=False)))
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='priority_profiles'").fetchone():__import__('channel_priority').consider(core,db,value)
        db.execute("UPDATE queue SET status='listing_saved'WHERE url=?AND kind='channel'AND status!='done'",(core.BASE+'/channels/'+row['username'],))
    return new


def inspect_result(core,db,job,doc):
    key,raw,page=job;f=json.loads(raw);u=url(core,f,page);rows=core.extract_listing(doc,u);discover(db,doc)
    signature=hashlib.sha256('\n'.join(r['username']for r in rows).encode()).hexdigest()
    nxt=[]
    for n in doc.root.nodes():
        if n.tag=='a':
            q=parse_qs(urlsplit(n.attrs.get('href','')).query)
            if q.get('lang')==['fa']and q.get('page',[''])[0].isdigit():nxt.append(q)
    # The server's own next links reveal unsupported parameters. Never treat ignored bounds as real coverage.
    ignored=[]
    for name in f:
        if name=='sort'and f[name]=='members' or name=='order'and f[name]=='desc':continue
        omitted=nxt and not any(q.get(name)==[f[name]]for q in nxt)
        values=[r['members']for r in rows if r.get('members')is not None]
        violated=(name=='minMembers'and any(v<int(f[name])for v in values))or(name=='maxMembers'and any(v>int(f[name])for v in values))or(name=='order'and f[name]=='asc'and values!=sorted(values))
        if omitted and(violated or name in('niche','sponsored')):ignored.append(name)
    n=persist(core,db,rows)
    if ignored:
        for name in ignored:db.execute('INSERT OR REPLACE INTO spy_controls VALUES(?,?,?)',(name,'ignored','Parameter omitted from server pagination; '+u))
        status='ignored';error='Server omitted: '+','.join(ignored)
    elif db.execute('SELECT 1 FROM spy_slice_pages WHERE key=?AND fingerprint=?',(key,signature)).fetchone():status='capped';error='Server repeated/clamped the result page'
    elif not rows:status='done';error=None
    elif any(int(q['page'][0])>page for q in nxt):status='pending';error=None
    else:status='capped'if page>=200 else'done';error='Reached the known 200-page HTML ceiling'if status=='capped'else None
    db.execute('INSERT OR IGNORE INTO spy_slice_pages VALUES(?,?,?,?,?)',(key,page,signature,len(rows),n))
    db.execute('UPDATE spy_slices SET status=?,page=?,pages=pages+1,new_records=new_records+?,error=?WHERE key=?',(status,page+1,n,error,key))
    if status=='capped'and 'minMembers'in f and db.execute("SELECT 1 FROM spy_controls WHERE name IN('minMembers','maxMembers')AND verdict='ignored'").fetchone()is None:
        lo=int(f['minMembers']);hi=int(f.get('maxMembers',max([r['members']or 0 for r in rows],default=lo+1)))
        if hi-lo>1:
            mid=(lo+hi)//2
            for left,right in((lo,mid),(mid,hi)):add(db,{**f,'minMembers':str(left),'maxMembers':str(right)})
    # Avoid thousands of identical unsupported combinations once a parameter has been proven ignored.
    for name, in db.execute("SELECT name FROM spy_controls WHERE verdict='ignored'").fetchall():
        for other,params in db.execute("SELECT key,filters FROM spy_slices WHERE status='pending'").fetchall():
            if name in json.loads(params):db.execute("UPDATE spy_slices SET status='unsupported',error=?WHERE key=?",('Unsupported parameter '+name,other))
    db.commit()


class CountOpener:
    def __init__(self,db,opener,core):self.db=db;self.opener=opener;self.core=core;self.via='channel_collection'
    def open(self,request,**kwargs):
        i=self.db.execute('INSERT INTO requests(url,status,at,via)VALUES(?,0,?,?)',(request.full_url,self.core.now(),self.via)).lastrowid;self.db.commit()
        try:r=self.opener.open(request,**kwargs)
        except self.core.HTTPError as exc:
            self.db.execute('UPDATE requests SET status=?WHERE id=?',(exc.code,i));self.db.commit();raise
        self.db.execute('UPDATE requests SET status=?WHERE id=?',(r.status,i));self.db.commit();return r


def step(core,db,client):
    from source_budget import Challenge,HostBlocked
    from urllib.error import URLError
    from http.client import HTTPException
    priority=__import__('channel_priority')
    turns=int((db.execute("SELECT value FROM meta WHERE key='priority_profile_turn'").fetchone()or['0'])[0])
    db.execute("INSERT OR REPLACE INTO meta VALUES('priority_profile_turn',?)",(str(turns+1),));db.commit()
    # One enrichment slot per five jobs prevents profile starvation while lists advance.
    if turns%5==4 and priority.next_job(db):return priority.step(core,db,client)
    # Phase 1: all effective HTML listing slices, respecting their saved cursors.
    job=db.execute("SELECT key,filters,page FROM spy_slices WHERE status='pending'ORDER BY pages,CASE WHEN filters='{}'THEN 0 ELSE 1 END,key LIMIT 1").fetchone()
    if job:
        key,raw,page=job
        try:inspect_result(core,db,job,Document(client.get(url(core,json.loads(raw),page))))
        except HostBlocked:raise
        except core.HTTPError as exc:
            db.rollback()
            if exc.code not in(403,429)and exc.code<500:db.execute("UPDATE spy_slices SET status='failed',error=?WHERE key=?",(str(exc.code),key));db.commit()
        except(Challenge,URLError,TimeoutError,ConnectionError,HTTPException):db.rollback()
        except ValueError as exc:
            db.rollback();db.execute("UPDATE spy_slices SET status=?,error=?WHERE key=?",('robots_disallowed'if str(exc).startswith('robots.txt disallows:')else'failed',str(exc),key));db.commit()
        return True
    if priority.next_job(db):return priority.step(core,db,client)
    # Phase 2: every known Iranian advertiser's placement list, even if no longer in active ads.
    for slug in core.iran_advertisers():core.enqueue(db,core.BASE+'/advertisers/'+slug+'/channels','listing',0)
    db.commit()
    task=db.execute("SELECT url,kind FROM queue WHERE status='pending'AND url LIKE '%/advertisers/%/channels'ORDER BY url LIMIT 1").fetchone()
    if not task:
        # Phase 3: profile graph, but never fetch a username already saved from any phase.
        db.execute("UPDATE queue SET status='listing_saved'WHERE kind='channel'AND status='pending'AND EXISTS(SELECT 1 FROM channels c WHERE queue.url=?||c.username)",(core.BASE+'/channels/',));db.commit()
        task=db.execute("SELECT url,kind FROM queue WHERE status='pending'AND kind='channel'ORDER BY priority,url LIMIT 1").fetchone()
    if not task:return False
    u,kind=task
    try:
        doc=Document(client.get(u));phase_persian=False
        if kind=='channel':
            value=core.extract(doc,u);phase_persian=value['classification']!='unclassified'or'fa'in value.get('source_language_tags',[])
            if phase_persian:persist(core,db,[value])
        # Ad placement lists may carry member/title fields too; keep partial metadata when visible.
        for n in doc.root.nodes():
            if n.tag!='a'or 'href'not in n.attrs:continue
            p=urlsplit(n.attrs['href']).path
            if not core.CHANNEL.fullmatch(p):continue
            persian=bool(core.PERSIAN.search(n.text()))
            if kind=='listing'or persian or phase_persian:core.enqueue(db,n.attrs['href'],'channel',1 if persian else 2)
        db.execute("UPDATE queue SET status='done',error=NULL WHERE url=?",(u,));db.commit()
    except HostBlocked:raise
    except core.HTTPError as exc:
        db.rollback()
        if exc.code not in(403,429)and exc.code<500:db.execute("UPDATE queue SET status='failed',error=?WHERE url=?",(str(exc.code),u));db.commit()
    except(Challenge,URLError,TimeoutError,ConnectionError,HTTPException):db.rollback()
    except ValueError as exc:db.execute("UPDATE queue SET status='failed',error=?WHERE url=?",(str(exc),u));db.commit()
    return True


def coverage(db):return[dict(zip(('filters','status','next_page','pages','new_records','error'),v))for v in db.execute('SELECT filters,status,page,pages,new_records,error FROM spy_slices ORDER BY status,filters')]
