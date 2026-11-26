"""One request budget per source host, shared by every collector on this machine (Linux),
with adaptive pacing so long unattended runs stay polite without manual tuning.

  start      4 s between request starts (+0-1.5 s jitter)
  speed up   after every 400 clean responses in a row: -0.25 s, never below 2.5 s
  slow down  429 / 403 / challenge: pause 30 min, then the delay doubles (max 20 s)
             5xx / timeouts: pause 2 min, delay +1 s
  cool off   the pause is shared: every collector on the host waits it out
  blocks pause only this host; workers resume from their checkpoints

A crawl-delay from robots.txt (passed as `delay`) is always respected as a floor.
State lives next to the lock (CRAWL_BUDGET_DIR) and survives process restarts.
"""
import fcntl
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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
MIN_BY_HOST = {'tgstat.com': 10.0, 'ir.tgstat.com': 10.0}
SPEEDUP_EVERY, SPEEDUP_STEP = 400, 0.25
BLOCK_PAUSE, ERROR_PAUSE = 30 * 60, 2 * 60
CHALLENGE_PAUSE, MAX_BLOCK_PAUSE = 60 * 60, 4 * 60 * 60

def retry_after_seconds(headers):
    value=headers.get('Retry-After','') if headers else ''
    if value.isdigit():return int(value)
    try:return max(0,(parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds())
    except (TypeError,ValueError,OverflowError):return None


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
    state.setdefault('day', '')            # UTC date of day_count
    state.setdefault('day_count', 0)
    state.setdefault('block_streak', 0)
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
    return any(v in value for v in ('<title>just a moment','cf-chl-','/cdn-cgi/challenge-platform','challenge-form','checking your browser',
                                    'captcha','g-recaptcha','hcaptcha','cf-turnstile','smartcaptcha'))

def reserve(base, delay=0, *, cancelled=None):
    """Wait in short, unlocked intervals so other workers can report an in-flight failure."""
    lock,path=_paths(base)
    while True:
        with lock.open('a+') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX)
            if cancelled is not None and cancelled():
                return False
            state=_load(path);now=time.time()
            if state['last']>now+60:state['last']=0
            host=urlsplit(base).netloc.lower()
            today=time.strftime('%Y-%m-%d',time.gmtime(now))
            if state['day']!=today:state['day'],state['day_count']=today,0
            configured = host == 'tgadsspy.com' and 'CHANNEL_MIN_INTERVAL' in os.environ
            floor = max(1.0,float(os.environ['CHANNEL_MIN_INTERVAL'])) if configured else MIN_BY_HOST.get(host,MIN_DELAY)
            jitter = random.uniform(0.7,1.4) if configured else random.uniform(0,1.5)
            gap=max(state['delay'],delay or 0,floor)+jitter
            wait=max(state['pause_until']-now,state['last']+gap-now,0)
            if wait<=0:
                state['last']=now;state['day_count']+=1;_save(path,state);return True
        time.sleep(min(wait, 1 if cancelled is not None else 30))


def report(base, status, retry_after=None):
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
            state['halted']=False
            state['block_streak']+=1
            state['delay'] = min(MAX_DELAY, state['delay'] * 2)
            pause=min(MAX_BLOCK_PAUSE,(CHALLENGE_PAUSE if status=='challenge' else BLOCK_PAUSE)*2**min(state['block_streak']-1,4))
            if retry_after is not None:
                pause=max(pause,max(0,float(retry_after)))
            state['pause_until'] = max(state['pause_until'], now + pause)
            state['streak'] = 0
        elif status == 0 or (isinstance(status, int) and status >= 500):
            state['delay'] = min(MAX_DELAY, state['delay'] + 1)
            state['pause_until'] = max(state['pause_until'], now + ERROR_PAUSE)
            state['streak'] = 0
        elif isinstance(status, int) and status < 400:
            state['streak'] += 1
            if state['streak'] >= SPEEDUP_EVERY:
                floor = MIN_BY_HOST.get(urlsplit(base).netloc.lower(), MIN_DELAY)
                if urlsplit(base).hostname == 'tgadsspy.com' and 'CHANNEL_MIN_INTERVAL' in os.environ:
                    floor = max(1.0,float(os.environ['CHANNEL_MIN_INTERVAL']))
                state['delay'] = max(floor, state['delay'] - SPEEDUP_STEP)
                state['streak'] = 0
                state['block_streak'] = 0
        else:
            state['streak']=0
        _save(path, state)


def status(base):
    """Current pacing state for reports: delay, clean streak, seconds of pause left."""
    _, path = _paths(base)
    state = _load(path)
    return {'delay_s': round(state['delay'], 2), 'clean_streak': state['streak'],
            'pause_left_s': max(0, round(state['pause_until'] - time.time())),
            'blocks_6h':sum(v>=time.time()-6*3600 for v in state['blocks']),
            'halted':state['halted'],'reported_requests':state['reported_requests']}
