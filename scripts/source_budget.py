"""One host request budget shared by local ads and channel collectors (Linux)."""
import fcntl
import hashlib
import os
from pathlib import Path
import random
import tempfile
import time
from urllib.parse import urlsplit


def reserve(base, delay):
    host = urlsplit(base).netloc.lower()
    name = hashlib.sha256(host.encode()).hexdigest()[:16]
    directory = Path(os.environ.get('CRAWL_BUDGET_DIR', str(Path(tempfile.gettempdir()) / 'nardbon-source-budget')))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / (name + '.lock')).open('a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        value = handle.read().strip()
        previous = float(value) if value else 0
        time.sleep(max(0, previous + max(4, delay) + random.uniform(0, 2) - time.monotonic()))
        handle.seek(0)
        handle.truncate()
        handle.write(str(time.monotonic()))
        handle.flush()
