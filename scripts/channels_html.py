#!/usr/bin/env python3
"""Resumable one-off public HTML channel discovery; no source APIs.

Unverified Persian candidates are kept separately from explicitly Iran-tagged channels.
"""
import argparse
import base64
from datetime import datetime, timezone
import fcntl
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

from html_core import Client, Document, canonical, origin

BASE = 'https://tgadsspy.com'
CHANNEL = re.compile(r'^/channels/([A-Za-z0-9_]{4,64})/?$')
PERSIAN = re.compile('[پچژگکیی]')


def now():
    return datetime.now(timezone.utc).isoformat()


def connect(work):
    work.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(work / 'channels.sqlite3')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS queue(url TEXT PRIMARY KEY, kind TEXT, status TEXT DEFAULT 'pending', error TEXT);
    CREATE TABLE IF NOT EXISTS channels(username TEXT PRIMARY KEY COLLATE NOCASE, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
    ''')
    db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)', ('started_at', now()))
    return db


def enqueue(db, url, kind=None):
    url = canonical(urljoin(BASE, url))
    path = urlsplit(url).path
    if origin(url) != origin(BASE) or '/api/' in path:
        return
    channel = CHANNEL.fullmatch(path)
    if channel:
        url = BASE + '/channels/' + channel[1].lower()
    if not kind:
        if CHANNEL.fullmatch(path):
            kind = 'channel'
        elif ((path.rstrip('/') == '/channels' and parse_qs(urlsplit(url).query).get('lang') == ['fa'])
              or path.startswith('/countries/ir/channels')):
            kind = 'listing'
        else:
            return
    db.execute('INSERT OR IGNORE INTO queue(url,kind) VALUES(?,?)', (url, kind))


def seed(db, archive):
    enqueue(db, '/channels?lang=fa', 'listing')
    enqueue(db, '/countries/ir/channels', 'listing')
    enqueue(db, '/sitemap.xml', 'sitemap')
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
                    enqueue(db, link['url'])
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
    entities = []
    for item in structured:
        entity = item.get('mainEntity')
        if isinstance(entity, dict) and (item.get('url') == url or str(entity.get('identifier', '')).lower() == '@' + match[1].lower()):
            entities.append(entity)
    for item in entities:
        for key in ('address', 'contentLocation', 'areaServed'):
            values = item.get(key, [])
            for value in values if isinstance(values, list) else [values]:
                if isinstance(value, dict):
                    country = value.get('addressCountry', value.get('name', ''))
                    if isinstance(country, dict):
                        country = country.get('name', '')
                    if str(country).upper() in ('IR', 'IRAN', 'ایران'):
                        explicit.append({'field': key, 'value': value})
    metrics = {}
    for label in ('Members', 'Avg. views', 'CPM (est.)', 'Ads indexed'):
        if label in segments and segments.index(label) + 1 < len(segments):
            metrics[label] = segments[segments.index(label) + 1]
    languages = [parse_qs(urlsplit(n.attrs['href']).query).get('lang', [None])[0]
                 for n in doc.root.nodes() if n.tag == 'a' and urlsplit(n.attrs.get('href', '')).path == '/channels']
    return {'username': match[1].lower(), 'source_url': url, 'title': title,
            'description': meta.get('og:description', meta.get('description', '')),
            'classification': 'iran_confirmed' if explicit else 'persian_candidate' if 'fa' in languages or PERSIAN.search(title + ' ' + meta.get('description','')) else 'unclassified',
            'iran_evidence': explicit, 'source_language_tags': sorted({x for x in languages if x}),
            'members': next((e.get('interactionStatistic', {}).get('userInteractionCount') for e in entities if isinstance(e.get('interactionStatistic'), dict)), None),
            'metrics': metrics, 'collected_at': now(), 'jsonld': structured,
            'meta': meta, 'text_segments': segments,
            'links': [{'url': urljoin(BASE, n.attrs['href']), 'text': n.text().strip()}
                      for n in doc.root.nodes() if n.tag == 'a' and 'href' in n.attrs],
            'media': [dict(n.attrs) for n in doc.root.nodes() if n.tag in ('img','video','source')]}


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
              'limitations': ['Only discoverable public pages are included; queue exhaustion does not prove full coverage.',
                             'Persian language and Iran ad placement do not prove a channel is Iranian.',
                             'Channel statistics are current at fetch time; this is not historical reconstruction.'],
              'files': [{'name': target.name, 'bytes': target.stat().st_size, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}]}
    tmp = out / 'manifest.json.tmp'; tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2)); tmp.replace(out / 'manifest.json')
    return report


def run(db, client, pages):
    client.initialize()
    for _ in range(pages):
        job = db.execute("SELECT url,kind FROM queue WHERE status='pending' ORDER BY CASE WHEN url LIKE '%lang=fa%' THEN 0 WHEN kind='channel' THEN 1 WHEN kind='listing' THEN 2 ELSE 3 END,url LIMIT 1").fetchone()
        if not job:
            return
        url, kind = job
        try:
            body = client.get(url)
            if kind == 'sitemap':
                tree = ET.fromstring(body)
                index = tree.tag.endswith('sitemapindex')
                for node in tree.iter():
                    if node.tag.endswith('}loc') or node.tag == 'loc':
                        enqueue(db, node.text or '', 'sitemap' if index else None)
            else:
                doc = Document(body)
                if kind == 'channel':
                    value = extract(doc, url)
                    db.execute('INSERT OR REPLACE INTO channels VALUES(?,?)', (value['username'], json.dumps(value, ensure_ascii=False)))
                for link in doc.links():
                    enqueue(db, link)
            db.execute("UPDATE queue SET status='done',error=NULL WHERE url=?", (url,))
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
            seed(db, a.ads_checkpoint)
            client = Client(BASE, delay=4)
            i = 0
            while a.max_slices == 0 or i < a.max_slices:
                run(db, client, a.pages_per_slice)
                print(json.dumps(export(db, a.work_dir), ensure_ascii=False), flush=True)
                if a.repo:
                    publish(db, a.work_dir, a.repo)
                i += 1
                if not db.execute("SELECT 1 FROM queue WHERE status='pending' LIMIT 1").fetchone():
                    break
                seed(db, a.ads_checkpoint)
        finally:
            db.rollback()
            export(db, a.work_dir)
            db.close()

if __name__ == '__main__':
    main()
