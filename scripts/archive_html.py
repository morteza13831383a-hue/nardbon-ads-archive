#!/usr/bin/env python3
"""One-off Iran ad backfill from public HTML and sitemaps, never source APIs."""
import argparse
import csv
import fcntl
from datetime import datetime, timezone
import gzip
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

from html_core import Client, Document, canonical, origin
import archive_yield as ay

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://tgadsspy.com'
MIRROR_BASE = 'https://telegramadsarchive.com'
ANALYTICS_BASE = 'https://telegramadsanalytics.com'
LOG = logging.getLogger('archive-html')
AD_PATH = re.compile(r'^/ads/([A-Za-z0-9_-]+)$')
LIST_PATH = re.compile(r'^/(?:countries/ir(?:/(?:advertisers|niches|snapshot/[0-9-]+|trending/(?:week|month)))?|archive(?:/[0-9-]+)?|niches/[a-z-]+|advertisers/[a-z0-9_-]+)$')


def now():
    return datetime.now(timezone.utc).isoformat()


def connect(directory, cutoff=None, seed_known=False):
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(directory / 'checkpoint.sqlite3')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS queue (
        url TEXT PRIMARY KEY, kind TEXT NOT NULL, priority INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', tries INTEGER NOT NULL DEFAULT 0, error TEXT);
    CREATE INDEX IF NOT EXISTS queue_work ON queue(status,priority,tries,url);
    CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS discovery_edges (parent TEXT, child TEXT, relation TEXT, PRIMARY KEY(parent,child,relation));
    CREATE TABLE IF NOT EXISTS listing_counts (url TEXT PRIMARY KEY, ad_links INTEGER, iran_links INTEGER);
    ''')
    ay.ensure_schema(db)
    if not db.execute("SELECT 1 FROM meta WHERE key='cutoff'").fetchone():
        value = cutoff or now()
        if datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is None:
            raise ValueError('Cutoff must include a timezone')
        db.execute('INSERT INTO meta VALUES(?,?)', ('cutoff',value))
        enqueue(db, BASE+'/countries/ir', 'listing', 0)
        enqueue(db, BASE+'/countries/ir/advertisers', 'listing', 2)
        enqueue(db, BASE+'/sitemap.xml', 'sitemap', 10)
        db.commit()
    if seed_known:                       # real runs only: tests must not read the repo's data
        ay.seed_known(db, ROOT / 'data' / 'ads')
    ay.requeue_missing_snapshots(db)
    if seed_known:                       # real runs: ads linked from Persian channel pages first
        ay.seed_from_channel_pages(db, enqueue)
    if not db.execute("SELECT 1 FROM meta WHERE key='yield_seeded_v1'").fetchone():
        cutoff_value = db.execute("SELECT value FROM meta WHERE key='cutoff'").fetchone()[0]
        ay.seed_snapshots(db, cutoff_value, enqueue)
        for (payload,) in db.execute('SELECT payload FROM records').fetchall():
            ay.expand_from_record(db, json.loads(payload), enqueue)
        db.execute("INSERT INTO meta VALUES('yield_seeded_v1',?)", (now(),))
        db.commit()
    return db


def enqueue(db, url, kind, priority, via=None):
    url = canonical(url)
    if origin(url) != origin(BASE) or '/api/' in urlsplit(url).path:
        return
    # `via` records which discovery strategy found the URL first (for yield measurement)
    # a better (lower) priority also brings its strategy label, so tests credit the finder
    db.execute('INSERT INTO queue(url,kind,priority,via) VALUES(?,?,?,?) ON CONFLICT(url) DO UPDATE SET '
               'via=CASE WHEN excluded.priority<priority AND excluded.via IS NOT NULL THEN excluded.via ELSE via END, '
               'priority=MIN(priority,excluded.priority) WHERE excluded.priority<priority', (url,kind,priority,via))


def compact(node):
    """Keep text boundaries for labels without storing executable HTML."""
    def visible(n):
        if n.tag in ('script','style','svg','button'):
            return ''
        return ' '.join(visible(c) if hasattr(c,'tag') else c for c in n.children)
    return ' '.join(visible(node).split())


def elements(node):
    return [c for c in node.children if hasattr(c, 'tag')]


def page_details(doc, base=BASE):
    article = next((n for n in doc.root.nodes() if n.tag == 'article'), None)
    if not article:
        raise ValueError('Missing ad article; possible challenge or layout change')
    details = {'info':{}, 'metrics':{}, 'daily_impressions':[], 'placements_shown':[], 'sections':[]}
    for n in article.nodes():
        children = elements(n)
        # Named label/value rows used by Ad info and headline statistics.
        if len(children) >= 2:
            label = compact(children[0])
            if label in ('Total impressions', 'Active days', 'Peak day', 'Channels'):
                details['metrics'][label] = [compact(x) for x in children[1:]]
            label_nodes = [x for x in children if 'w-[104px]' in x.attrs.get('class','').split()]
            if label_nodes:
                label = compact(label_nodes[0])
                i = children.index(label_nodes[0])
                if i+1 < len(children):
                    details['info'][label] = compact(children[i+1])
        if n.tag == 'section':
            headings = [x for x in n.nodes() if x.tag == 'h2']
            label = n.attrs.get('aria-label') or (compact(headings[0]) if headings else '')
            if label == 'Impressions by day':
                for cell in n.nodes():
                    tooltip = cell.attrs.get('title','')
                    m = re.fullmatch(r'([\d,]+) impressions · (\d{1,2}/\d{1,2}/\d{4})',tooltip)
                    if m:
                        details['daily_impressions'].append({'date':datetime.strptime(m[2],'%m/%d/%Y').date().isoformat(),'impressions':int(m[1].replace(',','')),'label':tooltip})
            if label:
                texts = []
                # Leaf text segments preserve otherwise unmodelled public fields.
                for x in n.nodes():
                    if x.tag not in ('script','style','svg','path'):
                        text = ' '.join(c for c in x.children if isinstance(c,str)).strip()
                        if text:
                            texts.append(text)
                details['sections'].append({'label':label,'text_segments':texts})
            if headings and compact(headings[0]) == 'Where this ad ran':
                for a in n.nodes():
                    if a.tag == 'a' and a.attrs.get('href','').startswith('/channels/'):
                        details['placements_shown'].append({'url':urljoin(base,a.attrs['href']),'text':compact(a)})
    details['article_text_segments'] = [text for n in article.nodes() if n.tag not in ('script','style','svg','path') for text in [' '.join(c for c in n.children if isinstance(c,str)).strip()] if text]
    details['article_links'] = [dict(href=urljoin(base,n.attrs['href']),text=compact(n)) for n in article.nodes() if n.tag=='a' and 'href' in n.attrs]
    details['article_media'] = [dict(n.attrs) for n in article.nodes() if n.tag in ('img','video','source')]
    details['placements_complete'] = False  # source explicitly shows only top channels
    return details


def extract(doc, url, cutoff, iran_listing=False):
    works = []
    for item in doc.jsonld():
        kinds = item.get('@type',[])
        kinds = [kinds] if isinstance(kinds,str) else kinds
        if 'CreativeWork' in kinds:
            works.append(item)
    if not works:
        raise ValueError('No CreativeWork JSON-LD; do not mark page as successfully processed')
    item = next((w for w in works if str(w.get('identifier','')) == AD_PATH.fullmatch(urlsplit(url).path)[1]), works[0])
    country = item.get('contentLocation',{})
    countries = country if isinstance(country,list) else [country]
    if not iran_listing and not any(isinstance(c,dict) and str(c.get('name','')).upper() in ('IR','IRAN','ایران') for c in countries):
        return None, 'not_iran'
    first = item.get('datePublished')
    if not first:
        raise ValueError('Missing first-seen date; cannot enforce archive cutoff')
    first_dt = datetime.fromisoformat(first.replace('Z','+00:00'))
    cutoff_dt = datetime.fromisoformat(cutoff.replace('Z','+00:00'))
    if first_dt.tzinfo is None or cutoff_dt.tzinfo is None:
        raise ValueError('Timezone required for datePublished and cutoff')
    if first_dt > cutoff_dt:
        return None, 'after_cutoff'
    sponsor = item.get('sponsor') or {}
    media = item.get('associatedMedia') or {}
    sponsor = sponsor if isinstance(sponsor,dict) else {}
    media = media if isinstance(media,dict) else {}
    article = next((n for n in doc.root.nodes() if n.tag == 'article'), None)
    if article is None:
        raise ValueError('Missing ad article')
    cta_url, cta_text = '', ''
    # The creative's CTA is distinguished from links to the promoted entity.
    for n in article.nodes():
        if n.tag == 'a' and 'sponsored' in n.attrs.get('rel','').split() and 'uppercase' in n.attrs.get('class','').split():
            cta_url, cta_text = n.attrs.get('href',''), compact(n)
            break
    return {
        'id': AD_PATH.fullmatch(urlsplit(url).path)[1],
        'title':item.get('name',''), 'text':item.get('description',''),
        'niche':item.get('genre'), 'geo':'IR', 'lang':item.get('inLanguage'),
        'advertiser':sponsor.get('name',''), 'advertiser_url':sponsor.get('sameAs'),
        'cta_url':cta_url or sponsor.get('sameAs',''), 'cta_text':cta_text,
        'first_seen':first, 'last_seen':item.get('dateModified'),
        'media_src':urljoin(url,media['contentUrl']) if media.get('contentUrl') else None, 'source':'html-archive', 'source_url':url,
        'archive_cutoff':cutoff, 'collected_at':now(),
        'jsonld':item, 'details':page_details(doc,url),
    }, 'iran'


def discover(db, doc, url):
    links, iran = 0, 0
    row = db.execute('SELECT via FROM queue WHERE url=?', (canonical(url),)).fetchone()
    parent_via = (row[0] if row else None) or 'listing'
    child_via = parent_via.split('>')[0] + '>ad'
    for n in doc.root.nodes():
        if n.tag != 'a' or 'href' not in n.attrs:
            continue
        link = canonical(urljoin(url, n.attrs['href']))
        if origin(link) != origin(BASE):
            continue
        p = urlsplit(link)
        extra = None if p.query else ay.classify_listing(urlsplit(url).path, p.path, compact(n))
        if extra:
            enqueue(db, link, 'listing', extra[0], via=extra[1])
            continue
        if AD_PATH.fullmatch(p.path) and not p.query:
            links += 1
            is_ir = urlsplit(url).path.startswith('/countries/ir') or any(x.attrs.get('aria-label') == 'Iran' for x in n.nodes())
            iran += int(is_ir)
            # Every detail is still verified independently, including cards from Iran pages.
            enqueue(db,link,'detail',1 if is_ir else (3 if child_via in ('advertiser_via_channel>ad','advertiser_niche>ad') else 30),via=child_via)
        elif LIST_PATH.fullmatch(p.path):
            # Only bounded useful catalogue filters; no search or sort permutations.
            if p.query not in ('','geo=IR','intent=all','intent=strict'):
                continue
            if p.path.startswith('/niches/') and p.query != 'geo=IR':
                continue
            if p.path.startswith('/archive'):
                continue  # daily pages are capped; exhaustive detail discovery uses the sitemap
            if p.path.startswith('/advertisers/'):
                # Follow advertisers only from Iran index, not unrelated ad recommendations.
                if '/countries/ir' not in urlsplit(url).path:
                    continue
                priority = 5
            else:
                priority = 4
            enqueue(db,link,'listing',priority)
    db.execute('INSERT OR REPLACE INTO listing_counts VALUES(?,?,?)',(url,links,iran))
    return links,iran


def discover_related(db,record,doc=None):
    """Follow the advertiser graph of verified Iran ads, not random niche suggestions."""
    parent=record['source_url']
    advertiser=(record.get('advertiser') or '').strip().casefold()
    # history of this advertiser already came through the API sweep: nothing to add here
    if advertiser.startswith('@') and 'tg-'+advertiser[1:] in ay.api_swept_advertisers():
        return
    links=[]
    for link in record.get('details',{}).get('article_links',[]):
        text=(link.get('text') or '').strip().casefold()
        same=bool(advertiser) and (text==advertiser or text.startswith(advertiser+' ') or text.startswith('see all by') and advertiser in text)
        if same:
            links.append(link['href'])
    if doc is not None:
        for section in doc.root.nodes():
            if section.tag!='section':
                continue
            headings=[n for n in section.nodes() if n.tag=='h2']
            if not headings or not compact(headings[0]).startswith('More from this advertiser'):
                continue
            links.extend(n.attrs['href'] for n in section.nodes() if n.tag=='a' and 'href' in n.attrs)
    for href in set(links):
        url=canonical(urljoin(parent,href))
        parsed=urlsplit(url)
        if origin(url)!=origin(BASE) or parsed.query:
            continue
        if AD_PATH.fullmatch(parsed.path):
            kind,priority,relation='detail',2,'same_advertiser'
        elif re.fullmatch(r'/advertisers/[a-z0-9_-]+',parsed.path):
            kind,priority,relation='listing',3,'advertiser'
        else:
            continue
        enqueue(db,url,kind,priority)
        db.execute('INSERT OR IGNORE INTO discovery_edges VALUES(?,?,?)',(parent,url,relation))


def seed_related(db):
    if db.execute("SELECT 1 FROM meta WHERE key='related_graph_seeded_v1'").fetchone():
        return
    for (payload,) in db.execute('SELECT payload FROM records').fetchall():
        discover_related(db,json.loads(payload))
    db.execute("INSERT INTO meta VALUES('related_graph_seeded_v1',?)",(now(),))
    db.commit()


def sitemap(db, body):
    root = ET.fromstring(body)
    kind = root.tag.rsplit('}',1)[-1]
    if kind not in ('sitemapindex','urlset'):
        raise ValueError('Unexpected sitemap document')
    count = 0
    for node in root:
        loc = next((x.text for x in node if x.tag.rsplit('}',1)[-1]=='loc'),None)
        if not loc or origin(loc) != origin(BASE):
            continue
        path = urlsplit(loc).path
        if kind == 'sitemapindex' and re.fullmatch(r'/sitemap/\d+\.xml',path):
            enqueue(db,loc,'sitemap',10)
            count += 1
        elif kind == 'urlset' and AD_PATH.fullmatch(path):
            enqueue(db,loc,'detail',30)
            count += 1
    return count


def crawl(db, client, max_pages, max_seconds, only_via=None, claims=None):
    cutoff = db.execute("SELECT value FROM meta WHERE key='cutoff'").fetchone()[0]
    plan = ay.reprioritize(db, cutoff)
    LOG.info('Yield plan: %s', json.dumps(plan)[:1500])
    started = time.monotonic()
    initial_iran = db.execute('SELECT COUNT(*) FROM records').fetchone()[0]
    db.execute("INSERT OR REPLACE INTO meta VALUES('last_stop_reason','')")
    attempts = 0
    db.execute("CREATE TEMP TABLE IF NOT EXISTS attempted (url TEXT PRIMARY KEY)")
    db.execute("DELETE FROM attempted")
    while attempts < max_pages and time.monotonic()-started < max_seconds:
        # Limit retrying failed jobs to a later invocation; zero-try jobs first at equal priority.
        # only_via: test slices limited to some strategies (prefix match, e.g. 'channel_advertisers')
        via_sql, via_args = '', []
        if only_via:
            via_sql = ' AND (' + ' OR '.join('q.via LIKE ?' for _ in only_via) + ')'
            via_args = [v + '%' for v in only_via]
        job = db.execute("SELECT q.url,q.kind,q.tries FROM queue q WHERE status='pending' AND tries<3 AND NOT EXISTS (SELECT 1 FROM attempted a WHERE a.url=q.url)" + via_sql + " ORDER BY priority,tries,q.url LIMIT 1", via_args).fetchone()
        if not job:
            break
        url,kind,tries = job
        db.execute("INSERT INTO attempted VALUES(?)",(url,))
        ad_id = AD_PATH.fullmatch(urlsplit(url).path)[1] if kind == 'detail' and AD_PATH.fullmatch(urlsplit(url).path) else None
        if claims and ad_id:
            claim = claims.acquire(ad_id, 'tgadsspy')
            if claim != 'acquired':
                if claim == 'done':
                    db.execute("UPDATE queue SET status='mirror_done' WHERE url=?", (url,))
                    db.commit()
                continue
        attempts += 1
        status, error = 'done',None
        stop = False
        try:
            if not client.robots.allowed(url):
                status = 'blocked_robots'
            else:
                body = client.get(url)
                if kind == 'sitemap':
                    found = sitemap(db,body)
                    LOG.info('Sitemap %s: %s useful URLs',url,found)
                else:
                    doc = Document(body)
                    if kind == 'detail':
                        record,status = extract(doc,url,cutoff)
                        if record:
                            record['html_sha256'] = hashlib.sha256(body.encode()).hexdigest()
                            db.execute('INSERT OR REPLACE INTO records VALUES(?,?)',(record['id'],json.dumps(record,ensure_ascii=False)))
                            discover_related(db,record,doc)
                            ay.expand_from_record(db,record,enqueue)
                            LOG.info('Iran ad %s',record['id'])
                    else:
                        found,ir = discover(db,doc,url)
                        LOG.info('Listing %s: %s ads (%s Iran cards)',url,found,ir)
                        if '/countries/ir/snapshot/' in url:
                            month = ay.parse_snapshot(db, url, compact(doc.root))
                            LOG.info('Snapshot %s: site total stored=%s', url, bool(month))
                        if url == BASE+'/countries/ir':
                            visible = ' '.join(x.text() for x in doc.root.nodes() if x.tag in ('h1','p'))
                            m = re.search(r'([\d,]+)\s+creatives targeting Iran',visible)
                            if m:
                                db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('source_reported_iran_total',m[1].replace(',','')))
                            if not found:
                                raise ValueError('Iran catalogue has no ad links; possible layout/challenge')
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'[:500]
            status = 'failed' if tries+1 >= 3 else 'pending'
            LOG.warning('%s %s',url,error)
            if isinstance(exc,HTTPError) and exc.code in (404,410):
                status = 'gone'
            stop = isinstance(exc,HTTPError) and exc.code in (401,403,429)
            stop = stop or isinstance(exc,RuntimeError) and 'cooldown' in str(exc)
        db.execute('UPDATE queue SET status=?,tries=tries+1,error=? WHERE url=?',(status,error,url))
        db.commit()
        if claims and ad_id:
            claims.finish(ad_id, 'tgadsspy', status in ('iran','not_iran','after_cutoff'))
        if stop:
            db.execute("INSERT OR REPLACE INTO meta VALUES('last_stop_reason',?)",(error,))
            db.commit()
            LOG.warning('Stopping source requests after access/rate-limit response')
            break
    stats={'pages_attempted':attempts,'new_iran_ads':db.execute('SELECT COUNT(*) FROM records').fetchone()[0]-initial_iran,'seconds':round(time.monotonic()-started,2)}
    db.execute("INSERT OR REPLACE INTO meta VALUES('last_slice_performance',?)",(json.dumps(stats),))
    db.commit()
    LOG.info('Slice performance: %s',stats)
    return attempts


def eligible_import(record, cutoff_dt):
    url=record.get('source_url','')
    match=AD_PATH.fullmatch(urlsplit(url).path)
    if origin(url) not in {origin('https://' + h) for h in (
        'tgadsspy.com', 'telegramadsarchive.com', 'telegramadsanalytics.com',
        'telegramads.it', 'tgads.biz', 'tgads.observer', 'telegramstats.net')} or not match or match[1]!=record.get('id') or record.get('geo')!='IR':
        raise ValueError('Invalid imported ad identity or country')
    first=datetime.fromisoformat(record['first_seen'].replace('Z','+00:00'))
    if first.tzinfo is None:
        raise ValueError('Imported date requires timezone')
    return first <= cutoff_dt


def import_records(db,paths):
    """Import files collected on an executor with normal public-page access."""
    cutoff=db.execute("SELECT value FROM meta WHERE key='cutoff'").fetchone()[0]
    cutoff_dt=datetime.fromisoformat(cutoff.replace('Z','+00:00'))
    count=0
    for path in paths:
        path=Path(path)
        opener=gzip.open if path.name.endswith('.gz') else open
        with opener(path,'rt',encoding='utf-8') as handle:
            for line in handle:
                if not line.strip():
                    continue
                record=json.loads(line)
                if not eligible_import(record, cutoff_dt):
                    continue
                record['archive_cutoff']=cutoff
                prior=db.execute('SELECT payload FROM records WHERE id=?',(record['id'],)).fetchone()
                if prior:
                    existing=json.loads(prior[0])
                    if record.get('record_detail')=='listing' and existing.get('record_detail')!='listing':
                        # A later listing part must not erase a richer detail page.
                        merged=dict(existing)
                        for key,value in record.items():
                            if merged.get(key) in (None,'',{},[]) and value not in (None,'',{},[]):merged[key]=value
                    else:
                        merged=dict(existing)
                        for key,value in record.items():
                            if value not in (None,'',{},[]):merged[key]=value
                    record=merged
                db.execute('INSERT OR REPLACE INTO records VALUES(?,?)',(record['id'],json.dumps(record,ensure_ascii=False)))
                original_url=BASE+'/ads/'+record['id']
                enqueue(db,original_url,'detail',1)
                db.execute("UPDATE queue SET status='iran',error=NULL WHERE url=?",(original_url,))
                count+=1
        db.commit()
    return count


def report(db):
    statuses = {k:v for k,v in db.execute('SELECT status,COUNT(*) FROM queue GROUP BY status')}
    kinds = {k:v for k,v in db.execute('SELECT kind,COUNT(*) FROM queue GROUP BY kind')}
    total = db.execute("SELECT value FROM meta WHERE key='source_reported_iran_total'").fetchone()
    count = db.execute('SELECT COUNT(*) FROM records').fetchone()[0]
    stopped = db.execute("SELECT value FROM meta WHERE key='last_stop_reason'").fetchone()
    external = db.execute("SELECT value FROM meta WHERE key='external_collection'").fetchone()
    performance = db.execute("SELECT value FROM meta WHERE key='last_slice_performance'").fetchone()
    return {
        'last_slice_performance':json.loads(performance[0]) if performance else None,
        'external_collection':json.loads(external[0]) if external else None,
        'last_stop_reason':stopped[0] if stopped else None,
        'schema_version':1, 'generated_at':now(),
        'cutoff':db.execute("SELECT value FROM meta WHERE key='cutoff'").fetchone()[0],
        'source':'https://tgadsspy.com', 'source_api_used':False,
        'iran_ads_extracted':count, 'source_reported_iran_total':int(total[0]) if total else None,
        'queue_status':statuses,'queue_kinds':kinds,
        'discovered_queue_exhausted':not any(statuses.get(x,0) for x in ('pending','failed','blocked_robots')),
        'complete_iran_archive_verified':False,
        'coverage':ay.coverage_report(db),
        'limitations':[
            'Sitemaps and public listings are discovery sources, not a guaranteed complete Iran export.',
            'The source shows only top placement channels on each ad; those lists are not complete.',
            'Cutoff applies to first-seen dates; other fields are observed at collection time.',
            'Media files and host delivery are tracked separately from text extraction.',
        ],
        'errors':[dict(url=u,status=s,tries=t,error=e) for u,s,t,e in db.execute("SELECT url,status,tries,error FROM queue WHERE error IS NOT NULL OR status='blocked_robots' ORDER BY url LIMIT 200")],
    }


def safe_cell(value):
    if value is None:
        return ''
    if isinstance(value,(dict,list)):
        value = json.dumps(value,ensure_ascii=False,separators=(',',':'))
    value = str(value)
    return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value


def export(db,directory,part_bytes=4_000_000):
    """Small independently readable gzip/CSV parts fit conservative upload limits."""
    out = directory/'exports'
    out.mkdir(exist_ok=True)
    for old in out.glob('ads-*'):
        old.unlink()
    fields=['id','title','text','geo','lang','niche','advertiser','cta_url','cta_text','first_seen','last_seen','media_src','source_url','details']
    files=[]
    def write_part(rows,index):
        gz=out/f'ads-{index:05d}.jsonl.gz'
        with gz.open('wb') as raw, gzip.GzipFile(fileobj=raw,mode='wb',mtime=0,filename='') as handle:
            for row in rows:
                handle.write((json.dumps(row,ensure_ascii=False)+'\n').encode())
        csvpath=out/f'ads-{index:05d}.csv'
        with csvpath.open('w',encoding='utf-8-sig',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=fields)
            writer.writeheader()
            writer.writerows({k:safe_cell(row.get(k)) for k in fields} for row in rows)
        for path in (gz,csvpath):
            files.append({'name':path.name,'records':len(rows),'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    batch,size,index=[],0,1
    for (payload,) in db.execute('SELECT payload FROM records ORDER BY id'):
        row=json.loads(payload)
        # Bound uncompressed JSON and CSV; one oversized record remains explicit in manifest.
        n=max(len(payload.encode()),sum(len(safe_cell(row.get(k)).encode()) for k in fields)+len(fields)*4)
        if batch and size+n>part_bytes:
            write_part(batch,index)
            batch,size,index=[],0,index+1
        batch.append(row)
        size+=n
    if batch:
        write_part(batch,index)
    info=report(db)
    info['files']=files
    (out/'manifest.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    LOG.info('Extracted %s Iran ads; queue: %s',info['iran_ads_extracted'],info['queue_status'])
    return info


def source_lock(directory):
    directory.mkdir(parents=True,exist_ok=True)
    handle=(directory/'source.lock').open('a')
    try:
        fcntl.flock(handle,fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError('Another source collector is already running for this checkpoint')
    return handle


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir',type=Path,default=ROOT/'archive-work')
    parser.add_argument('--max-pages',type=int,default=1500)
    parser.add_argument('--max-seconds',type=int,default=5400)
    parser.add_argument('--delay',type=float,default=4)
    parser.add_argument('--cutoff',help='ISO-8601 UTC cutoff, fixed on first invocation')
    parser.add_argument('--retry-failed',action='store_true')
    parser.add_argument('--export-only',action='store_true')
    parser.add_argument('--only-via',help='Comma-separated strategy prefixes for a test slice, e.g. channel_advertisers,advertiser_niche')
    parser.add_argument('--import-dir',type=Path,help='Import collected ads-*.jsonl.gz parts before export; no network')
    args=parser.parse_args()
    if min(args.max_pages,args.max_seconds)<1 or args.delay<1:
        parser.error('Positive budgets and at least one second request delay required')
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    lock=source_lock(args.work_dir)
    db=connect(args.work_dir,args.cutoff,seed_known=True)
    try:
        if args.import_dir:
            imported=import_records(db,sorted(args.import_dir.glob('ads-*.jsonl.gz')))
            LOG.info('Imported %s externally collected records',imported)
            external_report=args.import_dir/'collection-report.json'
            if external_report.exists():
                data=json.loads(external_report.read_text(encoding='utf-8'))
                db.execute("INSERT OR REPLACE INTO meta VALUES('external_collection',?)",(json.dumps(data,ensure_ascii=False),))
                db.commit()
        if args.retry_failed:
            db.execute("UPDATE queue SET status='pending',tries=0,error=NULL WHERE status='failed'")
            db.commit()
        if not args.export_only:
            seed_related(db)
            client=Client(BASE,args.delay)
            client.opener=ay.RequestLedger(db,client.opener)
            client.initialize()
            from ad_claims import Claims
            claims=Claims()
            try:
                LOG.info('Shared cross-host ad-ID claims enabled')
                crawl(db,client,args.max_pages,args.max_seconds,[v.strip() for v in args.only_via.split(',') if v.strip()] if args.only_via else None,claims)
            finally:
                claims.close()
        info=export(db,args.work_dir)
        if not args.export_only and info.get('last_stop_reason'):
            raise SystemExit(2)
    finally:
        db.close()
        lock.close()

if __name__=='__main__':
    main()
