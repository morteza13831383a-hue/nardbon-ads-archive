"""Copy ad creatives to the download host and record confirmed uploads for the panel."""
import collections
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

import requests
from PIL import Image

HOSTS = {'tgadsspy.com', 'telegramadsarchive.com', 'telegramadsanalytics.com',
         'telegramads.it', 'tgads.biz', 'tgads.observer', 'telegramstats.net', 'telegium.com'}
RECEIPTS = Path('data/ad-image-import')
LIMIT = 16 * 1024 * 1024


def source(ad):
    return urljoin('https://tgadsspy.com/', ad.get('media_src') or '')


def url_key(url):
    return hashlib.sha256(url.encode('utf-8')).hexdigest()


def load_receipts():
    rows = {}
    for part in sorted(RECEIPTS.glob('*.jsonl')):
        for line in part.read_text().splitlines():
            row = json.loads(line)
            if row['key'] not in rows or row['at'] >= rows[row['key']]['at']:
                rows[row['key']] = row
    return rows


def transient(row):
    reason = row.get('reason', '')
    return (reason in {'ConnectionError', 'ChunkedEncodingError', 'ContentDecodingError',
                       'ReadTimeout', 'ConnectTimeout', 'Timeout', 'SSLError', 'ProxyError'}
            or (reason.startswith('HTTP ') and reason[5:] in {'408', '425', '500', '502', '503', '504'}))


def ready_for_retry(row, now):
    if row.get('status') != 'review':
        return False
    attempt = row.get('attempt', 1)
    backoff = min(86400, 3600 * 2 ** min(attempt - 1, 5))
    return now - row['at'] >= backoff and (transient(row) or attempt < 3)


def apply_receipts(ads):
    rows = load_receipts()
    for ad in ads.values():
        if ad.get('media_status') not in ('pending', 'queued', 'failed'):
            continue
        row = rows.get(url_key(source(ad)), {})
        if row.get('status') == 'review' and row.get('attempt', 0) >= 3 and not transient(row):
            ad['media_status'] = 'failed'
            ad['media_error'] = row.get('reason')
        elif row.get('status') == 'ok' and re.fullmatch(r'media/[a-zA-Z0-9_-]+\.webp', row['file']):
            ad.pop('media_error', None)
            ad.update(media_file=row['file'], media_status='ok')
        elif row.get('status') in ('video', 'none'):
            ad['media_status'] = row['status']


def copy_one(url, session, uploader, tmp, remote_root):
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.netloc not in HOSTS or parts.username or parts.password:
        return {'status': 'review', 'reason': 'unapproved source'}
    if parts.path.endswith(('.mp4', '.webm', '.mov')):
        return {'status': 'video'}
    if parts.path == '/media/work_photos/nophoto.svg':
        return {'status': 'none'}
    with session.get(url, timeout=(8, 25), stream=True, allow_redirects=False) as response:
        if response.status_code in (401, 403, 429):
            return {'status': 'hold', 'reason': str(response.status_code)}
        if response.status_code != 200:
            return {'status': 'review', 'reason': 'HTTP ' + str(response.status_code)}
        if 'video' in response.headers.get('Content-Type', ''):
            return {'status': 'video'}
        if 'html' in response.headers.get('Content-Type', '').lower():
            return {'status': 'hold', 'reason': 'HTML instead of image'}
        body = bytearray()
        for chunk in response.iter_content(65536):
            body.extend(chunk)
            if len(body) > LIMIT:
                return {'status': 'review', 'reason': 'image size'}
    with Image.open(io.BytesIO(body)) as image:
        if image.width * image.height > 40_000_000:
            return {'status': 'review', 'reason': 'image dimensions'}
        image = image.convert('RGB')
        if image.width > 900:
            image = image.resize((900, max(1, round(image.height * 900 / image.width))), Image.Resampling.LANCZOS)
        webp = io.BytesIO()
        image.save(webp, 'WEBP', quality=80, method=4)
    data = webp.getvalue()
    name = hashlib.sha256(data).hexdigest() + '.webp'
    tmp.write_bytes(data)
    uploader.put(tmp, f'{remote_root}/media/{name}')
    return {'status': 'ok', 'file': 'media/' + name}


def commit_part(path):
    subprocess.run(['git', 'add', '--', str(path)], check=True)
    subprocess.run(['git', 'commit', '-qm', 'Record ad image delivery receipts'], check=True)
    for attempt in range(5):
        subprocess.run(['git', 'pull', '--rebase', 'origin', 'main'], check=True)
        if subprocess.run(['git', 'push', 'origin', 'HEAD:main']).returncode == 0:
            return
        time.sleep(2 + attempt)
    raise RuntimeError('Unable to publish delivery receipts')


def drain():
    import queue
    import tempfile
    import sync
    ads = sync.load_ads()
    known = load_receipts()
    mapped = {source(a): a['media_file'] for a in ads.values() if a.get('media_status') == 'ok' and a.get('media_file')}
    todo = collections.defaultdict(set)
    stopped = {row['host'] for row in known.values() if row['status'] == 'hold'}
    now = time.time()
    for ad in ads.values():
        if ad.get('media_src') and ad.get('media_status') in ('failed', 'pending', 'queued'):
            url = source(ad)
            prev = known.get(url_key(url), {})
            if not prev or ready_for_retry(prev, now):
                todo[urlsplit(url).hostname or ''].add(url)
    cutoff = time.monotonic() + int(os.environ.get('DRAIN_SECONDS', '2200'))
    results = queue.Queue()
    def worker(host, urls, temp):
        with requests.Session() as client, sync.Uploader() as up:
            client.headers['User-Agent'] = 'Nardboon-Ad-Image-Sync/1.0'
            for url in sorted(urls, key=lambda u: (u not in mapped, bool(known.get(url_key(u))), u)):
                if time.monotonic() >= cutoff:
                    return
                if url in mapped:
                    result = {'status': 'ok', 'file': mapped[url]}
                elif host in stopped:
                    continue
                else:
                    try:
                        result = copy_one(url, client, up, temp, sync.REMOTE_ROOT)
                    except (requests.RequestException, OSError, Image.DecompressionBombError) as exc:
                        result = {'status': 'review', 'reason': type(exc).__name__}
                    if result['status'] == 'hold':
                        stopped.add(host)
                    time.sleep(4)
                key = url_key(url)
                results.put(dict(key=key, host=host, at=int(time.time()),
                                 attempt=known.get(key, {}).get('attempt', 0) + 1, **result))
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    stamp = os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT']
    counts = collections.Counter()
    with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker, host, urls, Path(tmp)/str(i)) for i,(host,urls) in enumerate(todo.items())]
        part = 0
        while True:
            batch = []
            deadline = time.monotonic() + 300
            while len(batch) < 500 and time.monotonic() < deadline:
                if all(f.done() for f in futures) and results.empty():
                    break
                try: batch.append(results.get(timeout=2))
                except queue.Empty: pass
            if batch:
                path = RECEIPTS / f'{stamp}-{part:05d}.jsonl'
                path.write_text(''.join(json.dumps(row)+'\n' for row in batch))
                commit_part(path)
                part += 1
                counts.update(row['status'] for row in batch)
                print('Delivery receipts:', dict(counts), flush=True)
            if all(f.done() for f in futures) and results.empty():
                break
        for future in futures:
            future.result()
    print('Hold hosts:', sorted(stopped), 'Review and delivery counts:', dict(counts))


if __name__ == '__main__':
    drain()
