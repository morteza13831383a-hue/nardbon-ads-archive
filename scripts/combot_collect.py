#!/usr/bin/env python3
"""
Persian groups and channels from Combot's public top lists (combot.org; the owner has permission,
and robots.txt allows /top/telegram/* and /api).

  groups:   https://combot.org/api/chart/fa?limit=100&offset=N   (JSON, ~529 rows, ~6 requests)
  channels: https://combot.org/top/telegram/channels/fa          (JSON-LD list in the page, ~33 rows)

Runs at most once a day from sync.py (about 7 requests, paced through source_budget). Writes
data/combot-import/combot-fa.jsonl.gz in the same row format as data/tgstat-import, so
channel_import picks it up (source "combot", date field "cb").
"""
import datetime as dt
import gzip
import html
import json
import pathlib
import re

import requests

import source_budget

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "combot-import"
BASE = "https://combot.org"
UA = "Mozilla/5.0 (compatible; nardbon-archive/1.0)"
EVERY_H = 24
PAGE = 100
MAX_PAGES = 30          # safety: the Persian list is ~6 pages
USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
LD_JSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


def due(state, now):
    last = state.get("combot_last")
    if not last:
        return True
    last = dt.datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return now - last >= dt.timedelta(hours=EVERY_H)


def _get(session, path, params=None):
    source_budget.reserve(BASE)
    try:
        r = session.get(BASE + path, params=params, timeout=30)
    except requests.RequestException:
        source_budget.report(BASE, 0)
        raise
    source_budget.report(BASE, r.status_code)
    if r.status_code in (403, 429) or source_budget.is_challenge(r.text[:4000]):
        raise source_budget.Challenge(f"combot {path}: {r.status_code}")
    return r


def groups(session):
    rows = []
    for page in range(MAX_PAGES):
        r = _get(session, "/api/chart/fa", {"limit": PAGE, "offset": page * PAGE})
        if r.status_code != 200:
            break
        items = r.json()
        rows += items
        if len(items) < PAGE:
            break
    return rows


def channels(session):
    r = _get(session, "/top/telegram/channels/fa")
    if r.status_code != 200:
        return []
    out = []
    for block in LD_JSON.findall(r.text):
        try:
            data = json.loads(html.unescape(block))
        except ValueError:
            continue
        for node in data.get("@graph", []) if isinstance(data, dict) else []:
            for it in ((node.get("mainEntity") or {}).get("itemListElement") or []):
                m = re.match(r"https?://t\.me/([A-Za-z0-9_]+)", it.get("url") or "")
                if m:
                    out.append({"u": m.group(1), "t": it.get("name") or ""})
    return out


def step(state, log, now_iso):
    state["combot_last"] = now_iso()          # counted even on failure, so failures never speed it up
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept": "application/json, text/html"})
    stamp = now_iso()
    rows = {}
    for g in groups(session):
        u = (g.get("u") or "").lstrip("@")
        if USERNAME.match(u):
            rows[u.lower()] = {"type": "group", "username": u, "title": g.get("t") or "",
                               "members": g.get("s") if isinstance(g.get("s"), int) else None,
                               "language": (g.get("l") or "").lower() or None,
                               "url": f"https://t.me/{u}", "collected_at": stamp}
    for c in channels(session):
        if USERNAME.match(c["u"]) and c["u"].lower() not in rows:
            rows[c["u"].lower()] = {"type": "channel", "username": c["u"], "title": c["t"],
                                    "language": "fa", "url": f"https://t.me/{c['u']}", "collected_at": stamp}
    if not rows:
        log("combot: nothing returned")
        return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in sorted(rows.values(), key=lambda r: r["username"].lower()))
    f = OUT_DIR / "combot-fa.jsonl.gz"
    old = gzip.decompress(f.read_bytes()).decode("utf-8") if f.exists() else None
    # compare without the date so an unchanged list does not rewrite the file every day
    strip = lambda s: re.sub(r'"collected_at": "[^"]*"', "", s or "")
    if strip(old) != strip(body):
        f.write_bytes(gzip.compress(body.encode("utf-8"), mtime=0))
    log(f"combot: {sum(r['type'] == 'group' for r in rows.values())} groups, "
        f"{sum(r['type'] == 'channel' for r in rows.values())} channels")
    return len(rows)
