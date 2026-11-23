"""
Persian Telegram channel catalog (phase 2: channel suggestions).

A deliberately slow collector: once every CHANNELS_EVERY_HOURS (5 h) it makes
at most 2 /channels requests, 15 s apart, for one keyword from data/channel_keywords.txt,
rotating through the list, and merges the results into data/channels.json.
The panel's "کانال‌ها" tab searches that file locally.

It uses its own key and base URL (CHANNELS_API_KEY / CHANNELS_API_BASE), so the
ads sync key is never spent on it. It is off unless CHANNELS_API_KEY is set.
On the first 429 it stops, records the pause, and makes no further requests.
"""
import datetime as dt
import html
import json
import os
import pathlib
import re
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
KEYWORDS_F = ROOT / "data" / "channel_keywords.txt"
CHANNELS_F = ROOT / "data" / "channels.json"
API = os.environ.get("CHANNELS_API_BASE", "https://tgadsspy.com/api/v1").rstrip("/")
KEY = os.environ.get("CHANNELS_API_KEY", "").strip()
PER_RUN = int(os.environ.get("CHANNELS_PER_RUN", "1"))       # keywords per slice
GAP = float(os.environ.get("CHANNELS_GAP_SEC", "15"))         # pause between requests
EVERY_H = float(os.environ.get("CHANNELS_EVERY_HOURS", "5"))  # one slice every 5 h: ~5 keywords/day, a full pass in ~4 weeks
PAGE = 100
PERSIAN = re.compile("[پچژگکی]")
# compact rows keep the file small for the browser
FIELDS = ["u", "t", "m", "v", "c", "e", "k", "s", "d", "dd", "pd", "lp", "sr", "ia", "ic", "cl", "kd", "co", "lg", "er", "ts", "rg", "tl", "cb", "lz", "tm", "tc", "tse"]   # tse: Telegram Search Engine date. tl/cb/lz/tm/tc: date seen in TGList / Combot / Lyzem / telemetr.io / telegramchannels.me   # kd channel/group, co country, lg language, er engagement, ts TGStat date   # ia/ic: Iranian advertisers / their creatives seen in the channel   # + posts per day, last post date, source (tme = found by our crawler)   # username, title, members, avg views, category, sponsor-eligible, keywords, last snapshot, description, description date
DESC_PER_RUN = int(os.environ.get("CHANNELS_DESC_PER_RUN", "60"))   # public t.me pages read per run
DESC_GAP = float(os.environ.get("CHANNELS_DESC_GAP_SEC", "1.5"))
DESC_MAX_AGE_DAYS = 30
OG_DESC = re.compile(r'<meta property="og:description" content="([^"]*)"')


class Blocked(Exception):
    def __init__(self, wait, shared):
        super().__init__(f"channels API paused for {wait}s")
        self.wait, self.shared = wait, shared


def enabled():
    return bool(KEY)


def due(state, now):
    """True when the last slice was at least EVERY_H hours ago."""
    last = state.get("channels_last")
    if not last:
        return True
    last = dt.datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return now - last >= dt.timedelta(hours=EVERY_H)


def keywords():
    if not KEYWORDS_F.exists():
        return []
    out = []
    for line in KEYWORDS_F.read_text("utf-8").splitlines():
        w = line.split("#", 1)[0].strip()
        if len(w) >= 3 and w not in out:
            out.append(w)
    return out


def load():
    if not CHANNELS_F.exists():
        return {}
    rows = json.loads(CHANNELS_F.read_text("utf-8")).get("rows", [])
    return {r[0]: dict(zip(FIELDS, r)) for r in rows}


def save(chans, at):
    rows = sorted(chans.values(), key=lambda c: -(c.get("v") or 0))
    body = {"at": at, "fields": FIELDS, "rows": [[c.get(f) for f in FIELDS] for c in rows]}
    text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    if not CHANNELS_F.exists() or CHANNELS_F.read_text("utf-8") != text:
        CHANNELS_F.write_text(text, "utf-8")


def is_persian(ch):
    return (ch.get("lang") or "").lower() == "fa" or bool(PERSIAN.search(ch.get("title") or ""))


def _get(session, params):
    r = session.get(f"{API}/channels", params=params, timeout=40)
    if r.status_code == 429:
        try:
            j = r.json()
        except Exception:
            j = {}
        wait = int((j.get("retryAfterMs") or 0) / 1000) or int(r.headers.get("Retry-After", "3600") or 3600)
        # a quarantine is on the identity, so the ads sync must pause too
        raise Blocked(max(wait, 600), shared=j.get("error") == "identity_quarantined")
    if r.status_code != 200:
        raise RuntimeError(f"/channels {r.status_code}: {r.text[:200]}")
    return r.json()


def step(state, log, now_iso):
    """One small slice of work. Returns the catalog size."""
    state["channels_last"] = now_iso()            # counted even if it fails, so a failure never speeds things up
    kws = keywords()
    queries = [""] + kws                       # "" = biggest Persian channels overall
    session = requests.Session()
    session.headers.update({"Accept": "application/json", "User-Agent": "nardbon-archive/1.0", "X-Api-Key": KEY})
    chans = load()
    idx = state.get("channels_idx", 0) % len(queries)
    for _ in range(min(PER_RUN, len(queries))):
        kw = queries[idx]
        base = {"q": kw} if kw else {"lang": "fa"}
        offset = 0
        while True:
            page = _get(session, {**base, "limit": PAGE, "offset": offset})
            items = page.get("data") or []
            for ch in items:
                u = ch.get("username")
                if not u or not is_persian(ch):
                    continue
                c = chans.setdefault(u, {"u": u, "k": []})
                c.update({"t": ch.get("title") or "", "m": ch.get("members"), "v": ch.get("avgViews"),
                          "c": ch.get("category"), "e": bool(ch.get("isSponsoredEligible")),
                          "s": (ch.get("lastSnapshotAt") or "")[:10] or None})
                if kw and kw not in c["k"]:
                    c["k"].append(kw)
            meta = page.get("meta") or {}
            offset += PAGE
            time.sleep(GAP)
            if (len(items) < PAGE or meta.get("hasMore") is False or meta.get("offsetClamped")
                    or (meta.get("maxOffset") is not None and offset > meta["maxOffset"])):
                break
        idx = (idx + 1) % len(queries)
        state["channels_idx"] = idx
        if idx == 0:
            state["channels_at"] = now_iso()     # one full pass over all keywords finished
        log(f"channels: '{kw or 'lang=fa'}' done, next {idx}/{len(queries)}, catalog {len(chans)}")
    save(chans, now_iso())
    return len(chans)


def fill_descriptions(log, now_iso, time_left):
    """Read the public t.me/<username> page of channels that have no (fresh) description.
    Biggest channels first. Uses no API key and no Telegram account."""
    chans = load()
    if not chans:
        return 0
    stale = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=DESC_MAX_AGE_DAYS)).strftime("%Y-%m-%d")
    todo = [c for c in chans.values() if not c.get("dd") or c["dd"] < stale]
    todo.sort(key=lambda c: -(c.get("v") or 0))
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (compatible; nardbon-archive/1.0)"
    done = 0
    for c in todo[:DESC_PER_RUN]:
        if time_left() < 90:
            break
        try:
            r = session.get(f"https://t.me/{c['u']}", timeout=20)
        except requests.RequestException as e:
            log(f"descriptions: network error {e}")
            break
        if r.status_code == 429:
            log("descriptions: t.me asked us to slow down, stopping for this run")
            break
        if r.status_code == 200:
            m = OG_DESC.search(r.text)
            d = html.unescape(m.group(1)).strip() if m else ""
            if d.startswith("You can view and join @") or d.startswith("If you have Telegram"):
                d = ""                                     # t.me's generic text, not the channel's own
            c["d"] = re.sub(r"\s+", " ", d)[:400]
            c["dd"] = now_iso()[:10]
            done += 1
        time.sleep(DESC_GAP)
    if done:
        save(chans, now_iso())
    log(f"descriptions: {done} read this run, {len(todo) - done} waiting")
    return done
