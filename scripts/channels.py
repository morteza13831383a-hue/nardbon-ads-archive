"""
Persian Telegram channel catalog (phase 2: channel suggestions).

A slow, steady collector: every sync run (every 30 min) it makes only a few
/channels requests, one keyword at a time from data/channel_keywords.txt,
rotating through the list, and merges the results into data/channels.json.
The panel's "کانال‌ها" tab searches that file locally.

It uses its own key and base URL (CHANNELS_API_KEY / CHANNELS_API_BASE), so the
ads sync key is never spent on it. It is off unless CHANNELS_API_KEY is set.
On the first 429 it stops, records the pause, and makes no further requests.
"""
import datetime as dt
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
PER_RUN = int(os.environ.get("CHANNELS_PER_RUN", "1"))       # keywords per run
GAP = float(os.environ.get("CHANNELS_GAP_SEC", "4"))          # pause between requests
PAGE = 100
PERSIAN = re.compile("[پچژگکی]")
# compact rows keep the file small for the browser
FIELDS = ["u", "t", "m", "v", "c", "e", "k", "s"]   # username, title, members, avg views, category, sponsor-eligible, keywords, last snapshot


class Blocked(Exception):
    def __init__(self, wait, shared):
        super().__init__(f"channels API paused for {wait}s")
        self.wait, self.shared = wait, shared


def enabled():
    return bool(KEY)


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
