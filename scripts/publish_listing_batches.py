"""Publish complete gzip batches with a fast-forward-only ref update and local receipts."""
import argparse
import base64
import fcntl
import gzip
import hashlib
from pathlib import Path
import sqlite3
import time

from archive_collect import gh


def publish(repo, directory, db):
    db.execute('CREATE TABLE IF NOT EXISTS sent(name TEXT PRIMARY KEY, sha TEXT)')
    parts = []
    for part in sorted(directory.glob('channels-batch-*.jsonl.gz')):
        if db.execute('SELECT 1 FROM sent WHERE name=?',(part.name,)).fetchone():
            continue
        data = part.read_bytes()
        digest = hashlib.sha256(gzip.decompress(data)).hexdigest()
        if part.name != f'channels-batch-{digest}.jsonl.gz':
            raise ValueError('Batch digest mismatch')
        blob = gh(repo,'git/blobs',dict(encoding='base64',content=base64.b64encode(data).decode()))
        parts.append(dict(path='data/channel-html-import/'+part.name,mode='100644',type='blob',sha=blob['sha']))
        if len(parts)==100:
            break
    if not parts:
        return
    head = gh(repo,'commits/main')
    tree = gh(repo,'git/trees',dict(base_tree=head['commit']['tree']['sha'],tree=parts))
    if tree['sha'] != head['commit']['tree']['sha']:
        commit = gh(repo,'git/commits',dict(message='Import Persian channel listing batches',tree=tree['sha'],parents=[head['sha']]))
        gh(repo,'git/refs/heads/main',dict(sha=commit['sha'],force=False))
        print('published',commit['sha'],len(parts),flush=True)
    with db:
        db.executemany('INSERT OR IGNORE INTO sent VALUES(?,?)',[(Path(p['path']).name,p['sha']) for p in parts])


if __name__=='__main__':
    import subprocess
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',required=True)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--outbox',type=Path,required=True)
    parser.add_argument('--interval',type=int,default=1800)
    args=parser.parse_args()
    with (args.work/'publish.lock').open('a') as lock, sqlite3.connect(args.work/'publish.sqlite3') as db:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while not (args.work/'pause-publisher').exists():
            try:
                publish(args.repo,args.outbox,db)
            except subprocess.CalledProcessError as exc:
                print('Publication deferred; exit code',exc.returncode,flush=True)
            time.sleep(args.interval)
