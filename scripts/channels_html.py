#!/usr/bin/env python3
"""Resumable one-off public HTML channel discovery; no source APIs.

Unverified Persian candidates are kept separately from explicitly Iran-tagged channels.
"""
import argparse
import sys
import base64
from datetime import datetime, timezone
import filelock as fcntl
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit, parse_qs
import xml.etree.ElementTree as ET

from html_core import Client, Document, canonical, origin, OWNER_APPROVED

BASE = 'https://tgadsspy.com'
CHANNEL = re.compile(r'^/channels/([A-Za-z0-9_]{4,64})/?$')
PERSIAN = re.compile('[پچژگکیی]')
ADV_CHANNELS = re.compile(r'^/advertisers/([a-z0-9_-]{3,80})/channels/?$')
ADVERTISER = re.compile(r'^/advertisers/([a-z0-9_-]{3,80})/?$')
IRAN_ADVERTISERS_F = Path(__file__).resolve().parents[1] / 'data' / 'iran_advertisers.txt'

# Lower = sooner. Iranian evidence first, broad snowball and sitemap last.
P_IRAN_ADV_PAGE = 0      # /advertisers/<Iranian advertiser>/channels
P_IRAN_PLACEMENT = 0     # channel listed on such a page, or shown under an Iranian ad
P_PERSIAN_LINK = 1       # channel link whose visible text is Persian
P_FROM_PERSIAN = 2       # any channel linked from a Persian / Iran-bought channel page
P_LISTING = 3
P_OTHER = 5              # channel linked from a non-Persian page
P_SITEMAP = 8


def iran_advertisers():
    try:
        return {l.strip() for l in IRAN_ADVERTISERS_F.read_text('utf-8').splitlines() if l.strip()}
    except OSError:
        return set()


def now():
    return datetime.now(timezone.utc).isoformat()


def connect(work):
    work.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(work / 'channels.sqlite3', timeout=30)
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS queue(url TEXT PRIMARY KEY, kind TEXT, status TEXT DEFAULT 'pending', error TEXT);
    CREATE TABLE IF NOT EXISTS channels(username TEXT PRIMARY KEY COLLATE NOCASE, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
    ''')
    if 'priority' not in {r[1] for r in db.execute('PRAGMA table_info(queue)')}:
        db.execute('ALTER TABLE queue ADD COLUMN priority INTEGER NOT NULL DEFAULT 4')
        db.execute('CREATE INDEX IF NOT EXISTS queue_order ON queue(status, priority, url)')
    db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)', ('started_at', now()))
    return db


def enqueue(db, url, kind=None, priority=None):
    url = canonical(urljoin(BASE, url))
    path = urlsplit(url).path
    if origin(url) != origin(BASE) or '/api/' in path:
        return
    if path == '/channels' and urlsplit(url).query and not any(rx.fullmatch(path + '?' + urlsplit(url).query) for rx in OWNER_APPROVED['tgadsspy.com']):
        return
    channel = CHANNEL.fullmatch(path)
    if channel:
        url = BASE + '/channels/' + channel[1].lower()
    if not kind:
        if CHANNEL.fullmatch(path):
            kind = 'channel'
        elif ((path.rstrip('/') == '/channels' and parse_qs(urlsplit(url).query).get('lang') == ['fa'])
              or path.startswith('/countries/ir/channels')
              or re.fullmatch(r'/(?:niches|categories)/[a-z0-9_-]+/?|/search/?',path)):
            kind = 'listing'
        elif ADV_CHANNELS.fullmatch(path) and ADV_CHANNELS.fullmatch(path)[1] in iran_advertisers():
            kind = 'listing'
        else:
            return
    if priority is None:
        priority = {'listing': P_LISTING, 'channel': P_OTHER, 'sitemap': P_SITEMAP}.get(kind, P_OTHER)
    # a URL found again with stronger Iranian evidence moves forward in the queue
    db.execute('INSERT INTO queue(url,kind,priority) VALUES(?,?,?) '
               'ON CONFLICT(url) DO UPDATE SET priority=MIN(priority, excluded.priority), '
               "status=CASE WHEN status='listing_saved' AND excluded.priority=0 THEN 'pending' ELSE status END", (url, kind, priority))


def seed(db, archive):
    # Persian channel listing: robots.txt disallows /channels?*, but the owner approved crawling;
    # html_core.OWNER_APPROVED allows exactly /channels?lang=fa and its &page=N pages.
    enqueue(db, '/channels?lang=fa', 'listing', P_PERSIAN_LINK)
    enqueue(db, '/countries/ir/channels', 'listing')
    enqueue(db, '/sitemap.xml', 'sitemap')
    # where Iranian advertisers bought: the strongest public signal for Iranian channels
    for slug in iran_advertisers():
        enqueue(db, f'/advertisers/{slug}/channels', 'listing', P_IRAN_ADV_PAGE)
    if archive:
        src = sqlite3.connect(f'file:{archive}?mode=ro', uri=True)
        try:
            for (url,) in src.execute("SELECT url FROM queue WHERE url LIKE '%/channels/%'"):
                match = re.match(r'^https://tgadsspy.com(/channels/[A-Za-z0-9_]+)(?:/|$)', url)
                if match:
                    enqueue(db, match[1])
            for (payload,) in src.execute('SELECT payload FROM records'):
                value = json.loads(payload)
                for link in value.get('details', {}).get('placements_shown', []):
                    enqueue(db, link['url'], priority=P_IRAN_PLACEMENT)   # shown under a verified Iran ad
        finally:
            src.close()
    db.commit()


def extract(doc, url):
    match = CHANNEL.fullmatch(urlsplit(url).path)
    if not match:
        raise ValueError('Not a channel URL')
    title = next((n.text().strip() for n in doc.root.nodes() if n.tag == 'h1'), '')
    if not title:
        raise ValueError('Missing channel heading; possible challenge/layout change')
    structured = list(doc.jsonld())
    meta = {n.attrs.get('property', n.attrs.get('name', '')): n.attrs.get('content', '')
            for n in doc.root.nodes() if n.tag == 'meta'}
    nodes = [n for n in doc.root.nodes() if n.tag not in ('script','style','svg','path')]
    segments = [' '.join(c for c in n.children if isinstance(c, str)).strip() for n in nodes]
    segments = [s for s in segments if s]
    # Country evidence must be channel-specific; a Persian title or an Iran ad placement is not proof.
    explicit = []
    countries = set()
    entities = []
    for item in structured:
        entity = item.get('mainEntity')
        if isinstance(entity, dict) and (item.get('url') == url or str(entity.get('identifier', '')).lower() == '@' + match[1].lower()):
            entities.append(entity)
    for item in entities:
        for key in ('address', 'contentLocation', 'areaServed'):
            values = item.get(key, [])
            for value in values if isinstance(values, list) else [values]:
                country = value.get('addressCountry', value.get('name', '')) if isinstance(value, dict) else value
                if isinstance(country, dict):
                    country = country.get('name', '')
                if isinstance(country, str):
                    code = country.strip().upper()
                    if code in ('IR', 'IRAN', 'ایران'):
                        countries.add('IR')
                        explicit.append({'field': key, 'value': value})
                    elif re.fullmatch('[A-Z]{2}', code):
                        countries.add(code)
    metrics = {}
    for label in ('Members', 'Avg. views', 'CPM (est.)', 'Ads indexed'):
        if label in segments and segments.index(label) + 1 < len(segments):
            metrics[label] = segments[segments.index(label) + 1]
    languages = [parse_qs(urlsplit(n.attrs['href']).query).get('lang', [None])[0]
                 for n in doc.root.nodes() if n.tag == 'a' and origin(urljoin(url,n.attrs.get('href',''))) == origin(BASE) and urlsplit(n.attrs.get('href', '')).path == '/channels']
    iran = iran_advertisers()
    iran_on_page = sorted({ADVERTISER.fullmatch(urlsplit(urljoin(BASE, n.attrs['href'])).path)[1]
                           for n in doc.root.nodes() if n.tag == 'a' and 'href' in n.attrs
                           and ADVERTISER.fullmatch(urlsplit(urljoin(BASE, n.attrs['href'])).path)
                           and ADVERTISER.fullmatch(urlsplit(urljoin(BASE, n.attrs['href'])).path)[1] in iran})
    description = meta.get('og:description') or meta.get('description', '')
    script = bool(re.search(r'[\u0600-\u06ff]', title + ' ' + description))
    for entity in entities:
        tag = entity.get('inLanguage')
        if isinstance(tag, str):
            languages.append(tag.lower())
    fa = any(str(tag).lower().split('-')[0] in ('fa', 'persian', 'farsi') for tag in languages)
    related = set()
    for href in doc.links():
        link = urlsplit(urljoin(url, href))
        handle = CHANNEL.fullmatch(link.path)
        if origin(link.geturl()) == origin(BASE) and handle and handle[1].lower() != match[1].lower():
            related.add(handle[1].lower())
    category = next((e['category'] for e in entities if isinstance(e.get('category'), str)), None)
    members = next((e['interactionStatistic'].get('userInteractionCount') for e in entities
                    if isinstance(e.get('interactionStatistic'), dict)), None)
    members = listing_number(members) if members is not None else listing_number(metrics.get('Members', ''))
    return {'username': match[1].lower(), 'source_url': url, 'title': title,
            'iran_advertisers': iran_on_page,
            'description': description, 'has_persian_script': script, 'iran_geo_tag': bool(explicit),
            'url': url, 'country': next(iter(countries)) if len(countries) == 1 else None, 'language': 'fa' if fa else None,
            'related_channels': sorted(related), 'category': category,
            'classification': 'iran_confirmed' if explicit else 'persian_candidate' if fa or PERSIAN.search(title + ' ' + description) else 'unclassified',
            'iran_evidence': explicit, 'source_language_tags': sorted({x for x in languages if x}),
            'members': members, 'subscribers': members,
            'record_detail':'profile','profile_fetched_at':now(),
            'metrics': metrics, 'collected_at': now(), 'jsonld': structured,
            'meta': meta, 'text_segments': segments,
            'links': [{'url': urljoin(BASE, n.attrs['href']), 'text': n.text().strip()}
                      for n in doc.root.nodes() if n.tag == 'a' and 'href' in n.attrs],
            'media': [dict(n.attrs) for n in doc.root.nodes() if n.tag in ('img','video','source')]}


def listing_number(text):
    text = str(text).translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789'))
    text = text.replace(',', '').replace('٬', '').replace('\u00a0', '').strip()
    m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*([KkMm]?)', text)
    return int(float(m[1]) * {'': 1, 'k': 1000, 'm': 1000000}[m[2].lower()]) if m else None


def listing_eligibility(fields):
    for label in ('Sponsored eligible','Ad eligibility','Sponsored'):
        if label in fields:
            value=fields[label].text().strip().lower()
            if value in ('yes','eligible','enabled','true'):return True
            if value in ('no','ineligible','disabled','false'):return False
    return None


def listing_handle(href, url):
    link = urlsplit(urljoin(url, href))
    if origin(link.geturl()) != origin(BASE):
        return None
    match = CHANNEL.fullmatch(link.path)
    if match and match[1].lower() != 'most-advertised':
        return match[1].lower()
    return None


def direct_row(username, title, url, fields):
    desc = fields.get('Description')
    language = fields.get('Language') or fields.get('Lang') or ''
    fa = (parse_qs(urlsplit(url).query).get('lang') == ['fa'] or
          language.casefold() in ('fa', 'persian', 'farsi', 'فارسی'))
    country = fields.get('Country') or fields.get('Geo')
    iran = str(country).casefold() in ('ir','iran','ایران')
    script = (title or '') + ' ' + (desc or '')
    members = listing_number(fields.get('Subscribers') or fields.get('Members',''))
    return dict(username=username, title=title, description=desc, members=members, subscribers=members,
                avg_views=listing_number(fields.get('Avg. views','')), category=fields.get('Category') or fields.get('Niche'),
                language='fa' if fa else language or None, source_language_tags=['fa'] if fa else [],
                country='IR' if iran else country, iran_geo_tag=iran, iran_advertisers=[],
                iran_evidence=[{'field':'Country','value':country}] if iran else [],
                classification='iran_confirmed' if iran else 'persian_candidate' if fa or PERSIAN.search(script) else 'unclassified',
                has_persian_script=bool(re.search(r'[\u0600-\u06ff]',script)), metrics={},
                channel_url=BASE+'/channels/'+username, source_url=url, record_detail='listing', collected_at=now())


def extract_listing(doc, url, all_listings=False):
    """Read public Persian listing table rows by heading, never infer columns by position."""
    if not all_listings and (urlsplit(url).path != '/channels' or parse_qs(urlsplit(url).query).get('lang') != ['fa']):
        return []
    if CHANNEL.fullmatch(urlsplit(url).path):
        return []
    result = {}
    for table in doc.root.nodes():
        if table.tag != 'table' and table.attrs.get('role') != 'table':
            continue
        headers = []
        for row in table.nodes():
            if row.tag != 'tr' and row.attrs.get('role') != 'row':
                continue
            heads = [n.text().strip() for n in row.nodes() if n.tag == 'th' or n.attrs.get('role') == 'columnheader']
            if heads:
                headers = heads
                continue
            cells = [n for n in row.nodes() if n.tag == 'td' or n.attrs.get('role') == 'cell']
            # The mobile table's channel link has no role=cell, unlike its numeric columns.
            nodes = list(row.nodes())
            leading = next((n for n in nodes if n.tag == 'a' and CHANNEL.fullmatch(urlsplit(n.attrs.get('href', '')).path)), None)
            if headers and headers[0] == 'Channel' and cells and leading is not None and nodes.index(leading) < nodes.index(cells[0]):
                cells.insert(0, leading)
            if not cells or len(cells) > len(headers) or 'Channel' not in headers:
                continue
            if len(cells) < len(headers):
                # Next.js can stream trailing Lang/Cabinet cells after the initial row.
                # Accept a contiguous prefix only; a template gap between cells could shift columns.
                nodes = list(row.nodes())
                last_cell = max(nodes.index(c) for c in cells)
                if any(n.tag == 'template' for n in nodes[:last_cell]):
                    continue
            fields = dict(zip(headers, cells))
            anchor = next((n for n in fields['Channel'].nodes() if n.tag == 'a' and CHANNEL.fullmatch(urlsplit(n.attrs.get('href', '')).path)), None)
            if anchor is None:
                continue
            username = listing_handle(anchor.attrs['href'], url)
            if not username:
                continue
            title = re.sub(r'\s*@' + re.escape(username) + r'\s*$', '', anchor.text().strip(), flags=re.I).strip()
            if not title:
                continue
            previous=result.get(username,{})
            metrics = {key: fields[key].text().strip() for key in ('Members', 'Avg. views') if key in fields}
            row = direct_row(username,title,url,{k:v.text().strip() for k,v in fields.items()})
            row.update(metrics=metrics, sponsored_eligible=listing_eligibility(fields),
                       cabinet_type=fields['Cabinet type'].text().strip() if 'Cabinet type' in fields else None)
            result[username] = row
            if previous:
                for key,value in previous.items():
                    if value is not None:result[username][key]=value
    if not all_listings:
        return list(result.values())
    main = next((n for n in doc.root.nodes() if n.tag == 'main'), doc.root)
    for card in main.nodes():
        if card.tag not in ('article', 'li') and not card.attrs.get('data-channel'):
            continue
        nodes = list(card.nodes())
        anchors = [n for n in nodes if n.tag == 'a' and listing_handle(n.attrs.get('href',''),url)]
        names = {listing_handle(n.attrs['href'],url) for n in anchors}
        if len(names) != 1:
            continue
        username = names.pop()
        if username in result:
            continue
        fields = {}
        for label in ('Members','Subscribers','Niche','Category','Country','Geo','Language','Description'):
            attr = 'data-' + label.lower()
            value = next((n.attrs[attr] for n in nodes if n.attrs.get(attr)), None)
            if value:
                fields[label] = value
        desc = next((n.text().strip() for n in nodes if n.attrs.get('itemprop') == 'description'),None)
        if desc:
            fields.setdefault('Description',desc)
        title = next((n.text().strip() for n in nodes if n.tag in ('h2','h3','h4')),anchors[0].text().strip())
        result[username] = direct_row(username,title or None,url,fields)

    for node in main.nodes():
        if node.tag != 'a':
            continue
        username = listing_handle(node.attrs.get('href',''), url)
        if not username or username in result:
            continue
        text = node.text().strip()
        title = re.sub(r'\s*@'+re.escape(username)+r'\s*$', '', text, flags=re.I).strip() or None
        result[username] = direct_row(username,title,url,{})
    return list(result.values())


def save_listing(db, rows):
    for row in rows:
        old = db.execute('SELECT payload FROM channels WHERE username=?', (row['username'],)).fetchone()
        current = json.loads(old[0]) if old else {}
        # A summary never overwrites a fetched profile, explicit Iran evidence or a known value with null.
        if current and current.get('record_detail') != 'listing':
            classification = row.get('classification', 'persian_candidate' if parse_qs(urlsplit(row['source_url']).query).get('lang') == ['fa'] else 'unclassified')
            if current.get('classification') == 'unclassified' and classification != 'unclassified':
                current['classification'] = classification
                current['listing_source_url'] = row['source_url']
                db.execute('UPDATE channels SET payload=? WHERE username=?', (json.dumps(current, ensure_ascii=False), row['username']))
            continue
        merged = dict(current)
        for key, value in row.items():
            if value is not None and (value != [] or key not in merged):
                merged[key] = value
        if current.get('iran_geo_tag') or current.get('classification') == 'iran_confirmed':
            merged.update(iran_geo_tag=True, country='IR', classification='iran_confirmed')
        elif current.get('classification') == 'persian_candidate' and merged['classification'] == 'unclassified':
            merged['classification'] = 'persian_candidate'
        merged['metrics'] = dict(current.get('metrics', {}), **row['metrics'])
        db.execute('INSERT OR REPLACE INTO channels VALUES(?,?)', (row['username'], json.dumps(merged, ensure_ascii=False)))
        enqueue(db, row['channel_url'], priority=P_OTHER if merged['classification']=='unclassified' else P_PERSIAN_LINK)
        queued = db.execute('SELECT priority FROM queue WHERE url=?', (row['channel_url'],)).fetchone()
        needs_profile = bool(merged.get('iran_advertisers')) or queued[0] == P_IRAN_PLACEMENT or any(merged.get(k) is None for k in ('members','avg_views'))
        if not needs_profile or (merged['classification'] == 'unclassified' and not merged.get('iran_advertisers') and queued[0] > P_IRAN_PLACEMENT):
            db.execute("UPDATE queue SET status='listing_saved',error=NULL WHERE url=? AND status IN ('pending','in_progress')", (row['channel_url'],))


def export(db, work):
    out = work / 'exports'; out.mkdir(exist_ok=True)
    counts = dict(db.execute('SELECT status,COUNT(*) FROM queue GROUP BY status'))
    classes = {}
    tmp = out / 'channels.jsonl.gz.tmp'
    with gzip.open(tmp, 'wt', encoding='utf-8') as handle:
        for (payload,) in db.execute('SELECT payload FROM channels ORDER BY username'):
            value = json.loads(payload)
            classes[value['classification']] = classes.get(value['classification'], 0) + 1
            handle.write(payload + '\n')
    target = out / 'channels.jsonl.gz'; tmp.replace(target)
    report = {'schema_version': 1, 'started_at': db.execute("SELECT value FROM meta WHERE key='started_at'").fetchone()[0],
              'updated_at': now(), 'channels': sum(classes.values()), 'classifications': classes,
              'queue': counts, 'complete': False,
              'request_count':db.execute('SELECT COUNT(*)FROM requests').fetchone()[0]if db.execute("SELECT 1 FROM sqlite_master WHERE name='requests'").fetchone()else None,
              'slices':__import__('channel_slices').coverage(db)if db.execute("SELECT 1 FROM sqlite_master WHERE name='spy_slices'").fetchone()else [],
              'limitations': ['Only discoverable public pages are included; queue exhaustion does not prove full coverage.',
                             'Persian language and Iran ad placement do not prove a channel is Iranian.',
                             'Channel statistics are current at fetch time; this is not historical reconstruction.'],
              'files': [{'name': target.name, 'bytes': target.stat().st_size, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}]}
    tmp = out / 'manifest.json.tmp'; tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2)); tmp.replace(out / 'manifest.json')
    return report


# Rotation between queue types, so one big backlog (e.g. 1,240 advertiser pages) cannot starve
# the others. Per cycle: Persian listing page, channel profile, Iranian advertiser page, profile.
ROTATION = (
    ("listing",    "kind='listing' AND url NOT LIKE '%/advertisers/%'"),
    ("profile",    "kind='channel'"),
    ("advertiser", "url LIKE '%/advertisers/%/channels'"),
    ("profile",    "kind='channel'"),
)


def next_job(db, turn, critical_profiles=False):
    """Best pending job of this turn's type; if that type is empty, the next type; then any."""
    eligibility = ""
    if critical_profiles:
        eligibility = """ AND (kind!='channel' OR (priority<=2 AND NOT EXISTS (
            SELECT 1 FROM channels c WHERE c.username=substr(queue.url,length('https://tgadsspy.com/channels/')+1)
            AND length(trim(json_extract(c.payload,'$.title')))>0
            AND json_type(c.payload,'$.members') IN ('integer','real') )))"""
    for i in range(len(ROTATION)):
        _, where = ROTATION[(turn + i) % len(ROTATION)]
        job = db.execute(f"SELECT url,kind FROM queue WHERE status='pending' AND {where} {eligibility} "
                         "ORDER BY priority, url LIMIT 1").fetchone()
        if job:
            return job
    return db.execute(f"SELECT url,kind FROM queue WHERE status='pending' {eligibility} ORDER BY priority, url LIMIT 1").fetchone()


def consume(db, url, kind, body, document=Document):
    if kind == 'sitemap':
        tree = ET.fromstring(body)
        index = tree.tag.endswith('sitemapindex')
        for node in tree.findall('{*}sitemap/{*}loc' if index else '{*}url/{*}loc'):
            location = node.text or ''
            username = listing_handle(location,url) if not index else None
            if username:
                save_listing(db,[direct_row(username,None,url,{})])
            enqueue(db, location, 'sitemap' if index else None)
    else:
        doc = document(body)
        save_listing(db, extract_listing(doc, url, all_listings=(kind=='listing')))
        page_priority = P_OTHER
        if kind == 'channel':
            value = extract(doc, url)
            previous = db.execute('SELECT payload FROM channels WHERE username=?', (value['username'],)).fetchone()
            if previous:
                summary = json.loads(previous[0])
                if summary.get('record_detail') == 'listing':
                    value['listing_source_url'] = summary['source_url']
                    if value['classification'] == 'unclassified':
                        value['classification'] = summary['classification']
                    for key in ('members', 'avg_views'):
                        if value.get(key) is None and summary.get(key) is not None:
                            value[key] = summary[key]
            value['record_detail'] = 'profile'
            db.execute('INSERT OR REPLACE INTO channels VALUES(?,?)', (value['username'], json.dumps(value, ensure_ascii=False)))
            if value['classification'] != 'unclassified' or value.get('iran_advertisers'):
                page_priority = P_FROM_PERSIAN
        elif ADV_CHANNELS.fullmatch(urlsplit(url).path):
            page_priority = P_IRAN_PLACEMENT          # this page lists where an Iranian advertiser bought
        elif kind == 'listing':
            page_priority = P_LISTING
        for n in doc.root.nodes():
            if n.tag != 'a' or 'href' not in n.attrs:
                continue
            link = urljoin(url, n.attrs['href'])
            persian_text = bool(PERSIAN.search(n.text() or ''))
            pr = min(page_priority, P_PERSIAN_LINK) if persian_text else page_priority
            enqueue(db, link, priority=pr)
    db.execute("UPDATE queue SET status='done',error=NULL WHERE url=?", (url,))


def run(db, client, pages):
    client.initialize()
    if getattr(client, 'robots', None):
        for (candidate,) in db.execute("SELECT url FROM queue WHERE kind='listing' AND status='pending'").fetchall():
            if not client.robots.allowed(candidate):
                db.execute("UPDATE queue SET status='robots_disallowed',error='robots.txt disallows' WHERE url=?", (candidate,))
        db.commit()
    turn = int((db.execute("SELECT value FROM meta WHERE key='rotation_turn'").fetchone() or [0])[0])
    for _ in range(pages):
        job = next_job(db, turn)
        turn += 1
        db.execute("INSERT OR REPLACE INTO meta VALUES('rotation_turn', ?)", (str(turn % len(ROTATION)),))
        if not job:
            return
        url, kind = job
        try:
            body = client.get(url)
            consume(db, url, kind, body)
        except HTTPError as exc:
            if exc.code in (401,403,429):
                raise
            db.execute("UPDATE queue SET status='failed',error=? WHERE url=?", (str(exc),url))
        except (ValueError, ET.ParseError) as exc:
            db.execute("UPDATE queue SET status='failed',error=? WHERE url=?", (str(exc),url))
        db.commit()


def publish(db, work, repo):
    # Keep raw channel data alongside the current panel format, not in its place.
    from archive_collect import gh
    entries = []
    saved = []
    files = []
    buckets = [[] for _ in range(16)]
    for username, payload in db.execute('SELECT username,payload FROM channels ORDER BY username'):
        buckets[int(hashlib.sha256(username.encode()).hexdigest()[0], 16)].append(payload)
    for i, rows in enumerate(buckets):
        raw = gzip.compress(('\n'.join(rows) + ('\n' if rows else '')).encode(), mtime=0)
        digest = hashlib.sha256(raw).hexdigest()
        files.append({'name': f'channels-{i:02x}.jsonl.gz', 'bytes': len(raw), 'sha256': digest})
        key = f'published_bucket_{i}'
        old = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        if old and old[0] == digest:
            continue
        blob = gh(repo, 'git/blobs', {'content': base64.b64encode(raw).decode(), 'encoding': 'base64'})
        entries.append({'path': f'data/channel-html-import/channels-{i:02x}.jsonl.gz', 'mode': '100644', 'type': 'blob', 'sha': blob['sha']})
        saved.append((key, digest))
    if not entries:
        return
    manifest = json.loads((work / 'exports' / 'manifest.json').read_text())
    manifest['files'] = files
    entries.append({'path': 'data/channel-html-import/manifest.json', 'mode': '100644', 'type': 'blob',
                    'content': json.dumps(manifest, ensure_ascii=False, indent=2)})
    import subprocess
    for attempt in range(4):
        head = gh(repo, 'commits/main')
        tree = gh(repo, 'git/trees', {'base_tree': head['commit']['tree']['sha'], 'tree': entries})
        commit = gh(repo, 'git/commits', {'message': 'Import discovered public channel profiles', 'tree': tree['sha'], 'parents': [head['sha']]})
        try:
            gh(repo, 'git/refs/heads/main', {'sha': commit['sha'], 'force': False})
            break
        except subprocess.CalledProcessError:
            if attempt == 3:
                raise
            time.sleep(2)
    db.executemany('INSERT OR REPLACE INTO meta VALUES(?,?)', saved)
    db.commit()
    print('Published channel data: ' + commit['sha'], flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--final-plan',action='store_true',help='Listing slices first, advertiser placements second, unsaved Persian graph last')
    p.add_argument('--work-dir', type=Path, required=True)
    p.add_argument('--ads-checkpoint', type=Path)
    p.add_argument('--repo', help='Optional authorized GitHub repository for durable raw exports')
    p.add_argument('--pages-per-slice', type=int, default=25)
    p.add_argument('--max-slices', type=int, default=1, help='0: continue until discovered queue exhausted')
    a = p.parse_args()
    if a.pages_per_slice < 1 or a.max_slices < 0:
        p.error('Invalid budgets')
    a.work_dir.mkdir(parents=True, exist_ok=True)
    with (a.work_dir / 'worker.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        db = connect(a.work_dir)
        try:
            if a.final_plan:__import__('channel_slices').ensure(sys.modules[__name__],db)
            else:seed(db,a.ads_checkpoint)
            client = Client(BASE, delay=0 if a.final_plan else 4,resilient=a.final_plan)
            if a.final_plan:
                from source_budget import Challenge
                from urllib.error import URLError
                from http.client import HTTPException
                client.opener=__import__('channel_slices').CountOpener(db,client.opener,sys.modules[__name__])
                while True:
                    try:client.initialize();break
                    except __import__('source_budget').HostBlocked:raise
                    except HTTPError as exc:
                        if exc.code not in(403,429)and exc.code<500:raise
                    except(Challenge,URLError,TimeoutError,HTTPException):pass
            i = 0
            while a.max_slices == 0 or i < a.max_slices:
                active=True
                if a.final_plan:
                    for _ in range(a.pages_per_slice):
                        active=__import__('channel_slices').step(sys.modules[__name__],db,client)
                        if not active:break
                else:run(db,client,a.pages_per_slice)
                print(json.dumps(export(db, a.work_dir), ensure_ascii=False), flush=True)
                if a.repo:
                    publish(db, a.work_dir, a.repo)
                i += 1
                if a.final_plan:
                    if not active:break
                    continue
                if not db.execute("SELECT 1 FROM queue WHERE status='pending' LIMIT 1").fetchone():
                    break
                seed(db, a.ads_checkpoint)
        except __import__('source_budget').HostBlocked as exc:
            db.rollback();db.execute("INSERT OR REPLACE INTO meta VALUES('stop_reason',?)",(str(exc),));db.commit();raise
        finally:
            db.rollback()
            export(db, a.work_dir)
            db.close()

if __name__ == '__main__':
    main()
