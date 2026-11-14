"""Persisted, bounded profile enrichment for likely ad-bearing Persian channels."""
import json
from urllib.parse import urlsplit

VIA='persian_channel_priority_profile'
MIN_MEMBERS=20000

def has_profile(row):
    return bool(row.get('profile_fetched_at') or row.get('record_detail')=='profile' or ('links'in row and ('meta'in row or 'jsonld'in row)))

def ensure(core,db):
    db.executescript('''CREATE TABLE IF NOT EXISTS priority_profiles(username TEXT PRIMARY KEY,url TEXT NOT NULL,members INTEGER DEFAULT 0,eligible INTEGER DEFAULT 0,cabinet INTEGER DEFAULT 0,status TEXT DEFAULT 'pending',reason TEXT,error TEXT);
    CREATE TABLE IF NOT EXISTS priority_profile_fetches(id INTEGER PRIMARY KEY,username TEXT,at TEXT,ad_links INTEGER);''')
    if not db.execute("SELECT 1 FROM meta WHERE key='priority_profiles_seeded'").fetchone():
        for raw, in db.execute('SELECT payload FROM channels').fetchall():consider(core,db,json.loads(raw))
        db.execute("INSERT INTO meta VALUES('priority_profiles_seeded',?)",(core.now(),));db.commit()

def consider(core,db,row):
    if row.get('classification')=='unclassified' or has_profile(row):return
    members=core.listing_number(row.get('members')or'')or 0
    eligible=row.get('sponsored_eligible')is True
    cabinet=str(row.get('cabinet_type')or'').lower()in('eur','ton','gram')
    if not eligible and not cabinet and members<MIN_MEMBERS:return
    url=core.BASE+'/channels/'+row['username'].lower()
    db.execute('''INSERT INTO priority_profiles(username,url,members,eligible,cabinet,reason)VALUES(?,?,?,?,?,?)
      ON CONFLICT(username)DO UPDATE SET members=MAX(members,excluded.members),eligible=MAX(eligible,excluded.eligible),cabinet=MAX(cabinet,excluded.cabinet)''',
      (row['username'].lower(),url,members,int(eligible),int(cabinet),'eligible'if eligible else'cabinet'if cabinet else'size'))

def next_job(db):
    return db.execute("SELECT username,url FROM priority_profiles WHERE status='pending'ORDER BY eligible DESC,members DESC,cabinet DESC,username LIMIT 1").fetchone()

def step(core,db,client):
    from html_core import Document
    from source_budget import HostBlocked,Challenge
    from urllib.error import HTTPError,URLError
    from http.client import HTTPException
    from channel_slices import persist
    job=next_job(db)
    if not job:return False
    username,url=job
    old=db.execute('SELECT payload FROM channels WHERE username=?',(username,)).fetchone()
    if old and has_profile(json.loads(old[0])):
        db.execute("UPDATE priority_profiles SET status='already_fetched'WHERE username=?",(username,));db.commit();return True
    opener=getattr(client,'opener',None);previous_via=getattr(opener,'via',None)
    if opener is not None:opener.via=VIA
    try:
        doc=Document(client.get(url));row=core.extract(doc,url)
        # Persian-list provenance remains valid even if the profile omits language tags.
        if old and json.loads(old[0]).get('classification')!='unclassified'and row['classification']=='unclassified':row['classification']='persian_candidate'
        row.update(record_detail='profile',profile_fetched_at=core.now(),ad_discovery_via=VIA)
        persist(core,db,[row])
        links={v['url']for v in row.get('links',[])if urlsplit(v.get('url','')).netloc=='tgadsspy.com'and __import__('re').fullmatch(r'/ads/c[a-z0-9]{24}',urlsplit(v['url']).path)}
        db.execute('INSERT INTO priority_profile_fetches(username,at,ad_links)VALUES(?,?,?)',(username,core.now(),len(links)))
        db.execute("UPDATE priority_profiles SET status='done',error=NULL WHERE username=?",(username,))
        db.execute("UPDATE queue SET status='done',error=NULL WHERE url=?",(url,));db.commit()
    except HostBlocked:raise
    except HTTPError as exc:
        db.rollback()
        if exc.code not in(403,429)and exc.code<500:db.execute("UPDATE priority_profiles SET status='failed',error=?WHERE username=?",(str(exc.code),username));db.commit()
    except(Challenge,URLError,TimeoutError,ConnectionError,HTTPException):db.rollback()
    except ValueError as exc:db.rollback();db.execute("UPDATE priority_profiles SET status='failed',error=?WHERE username=?",(str(exc),username));db.commit()
    finally:
        if opener is not None:opener.via=previous_via
    return True
