"""Publish immutable channel batches using commit_data.py in a disposable worktree."""
import argparse
import fcntl
import gzip
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def publish(repo, outbox):
    with (outbox/'.publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        parts = sorted(outbox.glob('channels-batch-*.jsonl.gz'))
        if not parts:
            return
        def git(*args):
            return subprocess.check_output(['git', '-C', repo, *args])
        git('fetch', 'origin', 'main')
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp)/'publish'
            git('worktree', 'add', '--detach', str(checkout), 'origin/main')
            try:
                directory = checkout/'data/channel-html-import'
                directory.mkdir(parents=True, exist_ok=True)
                for part in parts:
                    digest = hashlib.sha256(gzip.decompress(part.read_bytes())).hexdigest()
                    if part.name != f'channels-batch-{digest}.jsonl.gz':
                        raise ValueError('Invalid content digest: ' + part.name)
                    target = directory/part.name
                    if target.exists():
                        if gzip.decompress(target.read_bytes()) != gzip.decompress(part.read_bytes()):
                            raise ValueError('Conflicting remote batch')
                    else:
                        shutil.copyfile(part,target)
                subprocess.run([sys.executable,str(repo/'scripts/commit_data.py'),
                                'Import verified channel batches'], cwd=checkout, check=True)
                git('fetch','origin','main')
                for part in parts:
                    remote = git('show','origin/main:data/channel-html-import/'+part.name)
                    if gzip.decompress(remote) != gzip.decompress(part.read_bytes()):
                        raise RuntimeError('Delivery mismatch: ' + part.name)
                    part.unlink()
            finally:
                git('worktree','remove','--force',str(checkout))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--outbox', type=Path, required=True)
    args = parser.parse_args()
    publish(args.repo.resolve(),args.outbox.resolve())
