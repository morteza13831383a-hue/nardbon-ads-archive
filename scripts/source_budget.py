"""One request budget per source host, shared by every collector on this machine (Linux),
with adaptive pacing so long unattended runs stay polite without manual tuning.

  start      4 s between request starts (+0-1.5 s jitter)
  speed up   after every 400 clean responses in a row: -0.25 s, never below 2.5 s
  slow down  429 / 403 / challenge: pause 30 min, then the delay doubles (max 20 s)
             5xx / timeouts: pause 2 min, delay +1 s
  cool off   the pause is shared: every collector on the host waits it out

A crawl-delay from robots.txt (passed as `delay`) is always respected as a floor.
State lives next to the lock (CRAWL_BUDGET_DIR) and survives process restarts.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import random
import tempfile
import time
from urllib.parse import urlsplit
from urllib.error import HTTPError

START, MIN_DELAY, MAX_DELAY = 4.0, 2.5, 20.0
# hosts reached with the user's own logged-in account stay slower: an account ban costs more
MIN_BY_HOST = {'tgstat.com': 4.0, 'ir.tgstat.com': 4.0}
SPEEDUP_EVERY, SPEEDUP_STEP = 400, 0.25
BLOCK_PAUSE, ERROR_PAUSE = 30 * 60, 2 * 60


def _paths(base):
    host = urlsplit(base).hostname.lower()
    if host=='ir.tgstat.com':host='tgstat.com'
    name = hashlib.sha256(host.encode()).hexdigest()[:16]
    directory = Path(os.environ.get('CRAWL_BUDGET_DIR', str(Path(tempfile.gettempdir()) / 'nardbon-source-budget')))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory / (name + '.lock'), directory / (name + '.json')


def _load(path):
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        state = {}
    state.setdefault('delay', START)
    state.setdefault('streak', 0)
    state.setdefault('last', 0.0)          # wall clock of the last request start
    state.setdefault('blocks', [])
    state.setdefault('halted', False)
    state.setdefault('reported_requests', 0)
    state.setdefault('pause_until', 0.0)   # wall clock
    return state


def _save(path, state):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state))
    tmp.replace(path)


class HostBlocked(HTTPError):
    """A terminal 429-shaped signal also understood by the unchanged ad runner."""
    def __init__(self,message):super().__init__('budget://host',429,message,{},None)

class Challenge(RuntimeError):
    """A challenge was reported to the shared budget; retry after its pause."""

def is_challenge(body):
    value=body[:250000].lower()
    return any(v in value for v in ('<title>just a moment','cf-chl-','/cdn-cgi/challenge-platform','challenge-form','checking your browser'))

def reserve(base, delay=0):
    """Wait in short, unlocked intervals so other workers can report an in-flight failure."""
    lock,path=_paths(base)
    while True:
        with lock.open('a+') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX)
            state=_load(path);now=time.time()
            if state['halted']:raise HostBlocked('Host halted after three blocks within six hours')
            if state['last']>now+60:state['last']=0
            gap=max(state['delay'],delay or 0)+random.uniform(0,1.5)
            wait=max(state['pause_until']-now,state['last']+gap-now,0)
            if wait<=0:
                state['last']=now;_save(path,state);return
        time.sleep(min(wait,30))


def report(base, status):
    """Tell the budget how the last request went: an HTTP status, or 0 for a network error."""
    lock, path = _paths(base)
    with lock.open('a+') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        state = _load(path)
        now = time.time()
        state['reported_requests']+=1
        state['blocks']=[v for v in state['blocks']if v>=now-6*3600]
        if status in (403, 429) or status == 'challenge':
            state['blocks'].append(now)
            state['halted']=len(state['blocks'])>=3
            state['delay'] = min(MAX_DELAY, state['delay'] * 2)
            state['pause_until'] = max(state['pause_until'], now + BLOCK_PAUSE)
            state['streak'] = 0
        elif status == 0 or (isinstance(status, int) and status >= 500):
            state['delay'] = min(MAX_DELAY, state['delay'] + 1)
            state['pause_until'] = max(state['pause_until'], now + ERROR_PAUSE)
            state['streak'] = 0
        elif isinstance(status, int) and status < 400:
            state['streak'] += 1
            if state['streak'] >= SPEEDUP_EVERY:
                floor = MIN_BY_HOST.get(urlsplit(base).netloc.lower(), MIN_DELAY)
                state['delay'] = max(floor, state['delay'] - SPEEDUP_STEP)
                state['streak'] = 0
        else:
            state['streak']=0
        _save(path, state)
        if state['halted']:raise HostBlocked('Host halted after three blocks within six hours')


def status(base):
    """Current pacing state for reports: delay, clean streak, seconds of pause left."""
    _, path = _paths(base)
    state = _load(path)
    return {'delay_s': round(state['delay'], 2), 'clean_streak': state['streak'],
            'pause_left_s': max(0, round(state['pause_until'] - time.time())),
            'blocks_6h':sum(v>=time.time()-6*3600 for v in state['blocks']),
            'halted':state['halted'],'reported_requests':state['reported_requests']}
