"""Listing-first TGStat state. The account client/secret boundary stays in tgstat_collect."""
import hashlib,json,re
from urllib.parse import urlsplit
from html_core import Document

PEER=re.compile(r'^/(channel|chat)/([^/]+)(?:/stat)?/?$')
USER=re.compile(r'^@([A-Za-z][A-Za-z0-9_]{3,31})$')

def compact(n):
    if n.tag in('script','style','svg','template'):return ''
    return ' '.join(' '.join(compact(c)if hasattr(c,'tag')else c for c in n.children).split())

def parse_rows(doc,category,peer,base='https://ir.tgstat.com'):
    boxes=[n for n in doc.root.nodes()if 'peer-item-box'in n.attrs.get('class','').split()]
    if not boxes:boxes=[n for n in doc.root.nodes()if n.tag=='a'and PEER.fullmatch(urlsplit(n.attrs.get('href','')).path)]
    result={}
    for box in boxes:
        anchor=next((n for n in box.nodes()if n.tag=='a'and PEER.fullmatch(urlsplit(n.attrs.get('href','')).path)),None)
        if anchor is None:continue
        p=urlsplit(anchor.attrs['href']);m=PEER.fullmatch(p.path)
        if m[1]!=peer or p.netloc and p.netloc!=urlsplit(base).netloc:continue
        username=USER.fullmatch(m[2]);username=username[1].lower()if username else None
        title=next((compact(n)for n in anchor.nodes()if 'font-16'in n.attrs.get('class','').split()),compact(anchor))
        bio=next((compact(n)for n in anchor.nodes()if any(c.startswith('line-clamp-')for c in n.attrs.get('class','').split())),'')
        members=None
        for n in anchor.nodes():
            value=compact(n)
            if re.search(r'\b(subscribers|participants|members)\b',value,re.I):
                b=next((compact(c)for c in n.nodes()if c.tag=='b'),None)
                if b:
                    digits=re.sub(r'[\s,٬]','',b);members=int(digits)if digits.isdigit()else b;break
        url=base+'/'+peer+'/'+m[2]
        key=username or 'tgstat:'+peer+':'+hashlib.sha256(url.encode()).hexdigest()
        result[key]={'type':'channel'if peer=='channel'else'group','username':username,'source_id':m[2],'title':title[:512],'description':bio,'members':members,'avg_reach':None,'err':None,'category':category,'country':'Iran'if urlsplit(base).netloc=='ir.tgstat.com'else None,'language':None,'text_sample':'','url':url,'record_detail':'listing'}
    return result


def ensure(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS tgstat_slices(id INTEGER PRIMARY KEY,category TEXT,peer TEXT,sort TEXT,lang TEXT DEFAULT '',status TEXT DEFAULT 'pending',page INTEGER DEFAULT 0,offset INTEGER DEFAULT 0,pages INTEGER DEFAULT 0,rows INTEGER DEFAULT 0,expected INTEGER,error TEXT,UNIQUE(category,peer,sort,lang));
    CREATE TABLE IF NOT EXISTS tgstat_slice_pages(slice_id INTEGER,page INTEGER,offset INTEGER,fingerprint TEXT,rows INTEGER,new_records INTEGER,PRIMARY KEY(slice_id,page,offset));
    CREATE TABLE IF NOT EXISTS tgstat_unresolved(url TEXT PRIMARY KEY,payload TEXT,status TEXT DEFAULT 'pending');
    CREATE TABLE IF NOT EXISTS tgstat_languages(url TEXT PRIMARY KEY,status TEXT DEFAULT 'pending',evidence TEXT);''')
    for cat, in db.execute('SELECT slug FROM categories').fetchall():
        if cat in('quotes','stat','share'):continue
        for peer in('channel','chat'):db.execute('INSERT OR IGNORE INTO tgstat_slices(category,peer,sort)VALUES(?,?,?)',(cat,peer,'members'))
    # Preserve old profile failures for audit; only retry genuine transient failures.
    db.execute("UPDATE queue SET status='pending',error=NULL WHERE status='failed'AND(error LIKE '%Timeout%'OR error LIKE '%URLError%'OR error LIKE '%HTTP Error 5%')")
    db.commit()


def save_rows(core,db,rows):
    saved=0
    for key,row in rows.items():
        if core._SECRET_FILTER and core._SECRET_FILTER.clean(core.safe_json(row))!=core.safe_json(row):continue
        # Reject any reflected credential URL before it can enter persistent state.
        if core._SECRET_FILTER and core._SECRET_FILTER.clean(row['url'])!=row['url']:continue
        row['collected_at']=core.now()
        old=db.execute('SELECT payload FROM records WHERE username=?',(key,)).fetchone()
        if old:
            previous=json.loads(old[0]);merged=dict(previous)
            merged.update({k:v for k,v in row.items()if v is not None and v!=''})
            if previous.get('description'):merged['description']=previous['description']
            if previous.get('text_sample'):merged['text_sample']=previous['text_sample']
            row=merged
        else:saved+=1
        db.execute('INSERT OR REPLACE INTO records VALUES(?,?)',(key,core.safe_json(row)))
        if not row['username']:db.execute('INSERT OR IGNORE INTO tgstat_unresolved(url,payload)VALUES(?,?)',(row['url'],core.safe_json(row)))
        else:
            m=PEER.fullmatch(urlsplit(row['url']).path)
            if m:db.execute("UPDATE queue SET status='listing_saved'WHERE url=?AND status IN('pending','failed')",(core.BASE+'/'+m[1]+'/@'+row['username'],))
    return saved


def next_slice(db):return db.execute("SELECT id,category,peer,sort,page,offset FROM tgstat_slices WHERE status='pending'ORDER BY pages,category,peer,sort LIMIT 1").fetchone()

def capped(db,job,message):
    sid,cat,peer,sort,_,_=job
    db.execute("UPDATE tgstat_slices SET status='capped',error=?WHERE id=?",(message,sid))
    if sort=='members':db.execute('INSERT OR IGNORE INTO tgstat_slices(category,peer,sort)VALUES(?,?,?)',(cat,peer,'ci'if peer=='channel'else'mau'))
    db.commit()


def step(core,db,client):
    from source_budget import Challenge,HostBlocked
    job=next_slice(db)
    if not job:return resolve_missing(core,db,client)
    sid,cat,peer,sort,page,offset=job
    try:
        body=client.post_catalog(cat,peer,page,offset,sort=sort)
        html,more,np,no=core.catalog_payload(body)
        from tgstat_session import account_problem
        doc=Document(html);problem=account_problem(doc,fragment=True)
        if problem:raise core.AccessStop(problem)
        fingerprint=hashlib.sha256((core._SECRET_FILTER.clean(compact(doc.root))if core._SECRET_FILTER else compact(doc.root)).encode()).hexdigest()
        repeated=db.execute('SELECT 1 FROM tgstat_slice_pages WHERE slice_id=?AND(fingerprint=?OR(page=?AND offset=?))',(sid,fingerprint,np,no)).fetchone()
        if more and((np,no)==(page,offset)or repeated):capped(db,job,'Server repeated a cursor or result page');return True
        rows=parse_rows(doc,cat,peer);n=save_rows(core,db,rows)
        value=json.loads(body);expected=value.get('totalCount')
        expected=int(expected)if str(expected).isdigit()else None
        db.execute('INSERT INTO tgstat_slice_pages VALUES(?,?,?,?,?,?)',(sid,page,offset,fingerprint,len(rows),n))
        db.execute("UPDATE tgstat_slices SET page=?,offset=?,pages=pages+1,rows=rows+?,expected=COALESCE(?,expected),status=?WHERE id=?",(np,no,len(rows),expected,'pending'if more else'done',sid))
        if not more and expected and expected>db.execute('SELECT rows FROM tgstat_slices WHERE id=?',(sid,)).fetchone()[0]:capped(db,job,'End reached before stated total')
        db.commit();return True
    except HostBlocked:raise
    except Challenge:
        db.rollback();return True
    except core.AccessStop as exc:
        db.rollback()
        if any(x in str(exc).lower()for x in('cursor','content repeated','empty continuation')):capped(db,job,str(exc));return True
        raise
    except __import__('source_budget').HostBlocked:raise
    except(core.HTTPError,core.URLError,TimeoutError,ConnectionError,core.HTTPException)as exc:
        db.rollback()
        if isinstance(exc,core.HTTPError)and exc.code not in(403,429)and exc.code<500:db.execute("UPDATE tgstat_slices SET status='failed',error=?WHERE id=?",(core.safe_error(exc),sid));db.commit()
        return True


def resolve_missing(core,db,client):
    job=db.execute("SELECT url,payload FROM tgstat_unresolved WHERE status='pending'ORDER BY url LIMIT 1").fetchone()
    if not job:return False
    url,payload=job;original=json.loads(payload)
    try:
        doc=Document(client.get(url));parents={id(c):p for p in doc.root.nodes()for c in p.children if hasattr(c,'tag')}
        heading=next((n for n in doc.root.nodes()if n.tag=='h1'),None)
        if heading is None:raise ValueError('Missing profile heading')
        scope=parents.get(id(heading),heading);username=None
        for n in scope.nodes():
            if n.tag=='a':
                p=urlsplit(n.attrs.get('href',''))
                if p.netloc in('t.me','telegram.me')and re.fullmatch(r'/[A-Za-z][A-Za-z0-9_]{3,31}',p.path):username=p.path[1:].lower();break
        if username:
            peer='channel'if original['type']=='channel'else'chat'
            value=core.extract(doc,core.BASE+'/'+peer+'/@'+username)
            if value:
                value.update(source_id=original['source_id'],url=url,record_detail='listing_resolution')
                db.execute("DELETE FROM records WHERE json_extract(payload,'$.url')=?AND json_extract(payload,'$.username')IS NULL",(url,))
                save_rows(core,db,{username:value})
        db.execute("UPDATE tgstat_unresolved SET status=?WHERE url=?",('resolved'if username else'no_public_username',url));db.commit()
    except core.AccessStop:db.rollback();raise
    except __import__('source_budget').HostBlocked:raise
    except core.HTTPError as exc:
        db.rollback()
        if exc.code not in(403,429)and exc.code<500:db.execute("UPDATE tgstat_unresolved SET status='failed'WHERE url=?",(url,));db.commit()
    except(core.URLError,TimeoutError,ConnectionError,core.HTTPException,__import__('source_budget').Challenge):db.rollback()
    except ValueError:db.execute("UPDATE tgstat_unresolved SET status='failed'WHERE url=?",(url,));db.commit()
    return True


def coverage(db):
    return [dict(zip(('category','type','sort','status','pages','rows','expected','page','offset'),v))for v in db.execute('SELECT category,peer,sort,status,pages,rows,expected,page,offset FROM tgstat_slices ORDER BY category,peer,sort')]


def probe_languages(db,client):
    """Anonymous discovery only. A discovered link is evidence to validate, not permission to send cookies to search."""
    for url in('https://tgstat.com/','https://tgstat.com/channels/search'):
        if db.execute('SELECT 1 FROM tgstat_languages WHERE url=?',(url,)).fetchone():continue
        try:doc=Document(client.request(url,public_discovery=True))
        except __import__('source_budget').HostBlocked:raise
        except __import__('tgstat_collect').AccessStop:raise
        except (OSError,TimeoutError,ConnectionError,__import__('http.client',fromlist=['HTTPException']).HTTPException,__import__('source_budget').Challenge):return
        found=[]
        from urllib.parse import parse_qs,urljoin
        for n in doc.root.nodes():
            if n.tag=='a':
                candidate=urljoin(url,n.attrs.get('href',''));p=urlsplit(candidate);q=parse_qs(p.query)
                if p.hostname in('tgstat.com','ir.tgstat.com')and any(q.get(k)in(['fa'],['persian'])for k in('lang','language')):found.append(candidate)
            if n.tag=='select'and n.attrs.get('name')in('lang','language'):
                for option in n.nodes():
                    if option.tag=='option'and(compact(option).lower()in('persian','فارسی')or option.attrs.get('value')=='fa'):found.append('Persian selector in '+n.attrs['name'])
        db.execute('INSERT INTO tgstat_languages VALUES(?,?,?)',(url,'candidate_requires_validation'if found else'no_verified_persian_catalog',json.dumps(found)));db.commit()
