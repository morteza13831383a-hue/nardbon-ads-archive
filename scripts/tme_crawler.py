"""
Persian channel crawler on Telegram's own public preview pages (t.me/s/<username>).

No tgadsspy, no Telegram account, no API key: t.me pages are public and reachable from
GitHub Actions. One request per channel gives title, description, subscribers, the latest
~20 posts with their views and dates, and links/mentions of other channels.

  seeds      channels already in data/channels.json, the advertisers' own channels in the
             ad archive (@advertiser, t.me links in CTAs) and the channels ads ran in
  discovery  every t.me/<name> link or @mention on a Persian channel's page joins the queue
  filter     only Persian channels are stored; others are remembered as seen
  refresh    stored channels are revisited after 30 days

Runs inside sync.py on the "heavy" runs (about every 2 h), a few dozen pages each, and stops
for the run as soon as t.me answers 429.
"""
import datetime as dt
import glob
import html
import json
import os
import re
import statistics
import time

import requests

import channels

QUEUE_F = channels.ROOT / "data" / "channel_queue.json"
PER_RUN = int(os.environ.get("TME_PER_RUN", "40"))
GAP = float(os.environ.get("TME_GAP_SEC", "1.2"))
REFRESH_DAYS = 30
UA = "Mozilla/5.0 (compatible; nardbon-archive/1.0)"

USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
NOT_CHANNEL = {"joinchat", "addstickers", "share", "proxy", "socks", "iv", "s", "c", "addemoji",
               "setlanguage", "login", "contact", "telegram", "durov", "blog", "premium"}
PERSIAN_ONLY = re.compile("[پچژگکی]")
ARABIC_SCRIPT = re.compile("[؀-ۿ]")
LATIN = re.compile("[A-Za-z]")

TITLE = re.compile(r'tgme_channel_info_header_title"><span dir="auto">(.*?)</span>', re.S)
DESC = re.compile(r'<div class="tgme_channel_info_description">(.*?)</div>', re.S)
COUNTER = re.compile(r'counter_value">([^<]+)</span> <span class="counter_type">([^<]+)<')
VIEWS = re.compile(r'tgme_widget_message_views">([^<]+)<')
TIMES = re.compile(r'<time datetime="([^"]+)"')
TEXTS = re.compile(r'tgme_widget_message_text[^>]*>(.*?)</div>', re.S)
LINKS = re.compile(r'href="https?://t\.me/([A-Za-z0-9_]{4,32})(?:[/?"])')
MENTIONS = re.compile(r'(?<![\w@])@([A-Za-z][A-Za-z0-9_]{3,31})\b')
OG_TITLE = re.compile(r'<meta property="og:title" content="([^"]*)"')
OG_DESC = re.compile(r'<meta property="og:description" content="([^"]*)"')
PAGE_EXTRA = re.compile(r'tgme_page_extra">\s*([\d  ,.]+)\s*(subscribers|members)')


def number(text):
    """'1.76M' -> 1760000, '3.94K' -> 3940, '1 773 066' -> 1773066."""
    t = (text or "").strip().replace(" ", " ").replace(",", "")
    m = re.fullmatch(r"([\d.]+)\s*([KkMm]?)", t.replace(" ", ""))
    if not m:
        return None
    return int(float(m[1]) * {"": 1, "k": 1_000, "m": 1_000_000}[m[2].lower()])


def strip(fragment):
    return html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).strip()


def is_persian(text):
    letters = len(ARABIC_SCRIPT.findall(text)) + len(LATIN.findall(text))
    return bool(PERSIAN_ONLY.search(text)) and letters and len(ARABIC_SCRIPT.findall(text)) / letters >= 0.3


def parse(body, final_url):
    """Channel facts from a t.me page; preview page (/s/) or the plain page as fallback."""
    out = {"links": set()}
    if "/s/" in final_url and TITLE.search(body):
        out["title"] = strip(TITLE.search(body)[1])
        d = DESC.search(body)
        out["desc"] = re.sub(r"\s+", " ", strip(d[1])) if d else ""
        for value, kind in COUNTER.findall(body):
            if kind.strip() in ("subscribers", "members"):
                out["members"] = number(value)
        views = [number(v) for v in VIEWS.findall(body)]
        views = [v for v in views if v is not None]
        times = sorted(TIMES.findall(body))
        if len(views) >= 3:
            out["avg_views"] = int(statistics.median(views[:-1] or views))  # newest post is still growing
        if times:
            out["last_post"] = times[-1][:10]
            first = dt.datetime.fromisoformat(times[0])
            last = dt.datetime.fromisoformat(times[-1])
            days = max((last - first).total_seconds() / 86400, 0.5)
            out["posts_per_day"] = round(len(times) / days, 1) if len(times) > 1 else None
        texts = " ".join(strip(t) for t in TEXTS.findall(body))
        out["text"] = f'{out["title"]} {out["desc"]} {texts}'
        out["links"] = set(LINKS.findall(body)) | set(MENTIONS.findall(texts))
    else:
        t, d = OG_TITLE.search(body), OG_DESC.search(body)
        extra = PAGE_EXTRA.search(body)
        if not t or not extra:          # no channel here (user, bot, removed or private)
            return None
        out["title"] = html.unescape(t[1])
        out["desc"] = html.unescape(d[1]) if d else ""
        out["members"] = number(extra[1])
        out["text"] = f'{out["title"]} {out["desc"]}'
    return out


def load_queue():
    if QUEUE_F.exists():
        q = json.loads(QUEUE_F.read_text("utf-8"))
    else:
        q = {}
    q.setdefault("pending", [])      # usernames to visit, best first
    q.setdefault("seen", {})         # username -> "YYYY-MM-DD:status" for non-stored outcomes
    q.setdefault("seeded", False)
    return q


def save_queue(q):
    QUEUE_F.write_text(json.dumps(q, ensure_ascii=False, separators=(",", ":")), "utf-8")


def candidate(u):
    """A username worth one request: valid, not a Telegram service path, not a bot."""
    return bool(USERNAME.match(u)) and u.lower() not in NOT_CHANNEL and not u.lower().endswith("bot")


def seed(q, chans):
    """Advertisers' own channels first (they buy ads: the market this archive is about)."""
    found = []
    for f in glob.glob(str(channels.ROOT / "data" / "ads" / "*.json")):
        for a in json.load(open(f, encoding="utf-8")):
            adv = (a.get("advertiser") or "").strip()
            if adv.startswith("@"):
                found.append(adv[1:])
            m = re.match(r"https?://t\.me/([A-Za-z0-9_]{4,32})", a.get("cta_url") or "")
            if m:
                found.append(m[1])
            for p in ((a.get("details") or {}).get("placements_shown") or []):
                m = re.search(r"/channels/([A-Za-z0-9_]{4,32})$", p.get("url") or "")
                if m:
                    found.append(m[1])
    added = 0
    for u in found:
        if candidate(u) and u not in chans and u not in q["seen"] and u not in q["pending"]:
            q["pending"].append(u)
            added += 1
    q["seeded"] = True
    return added


def step(log, now_iso, time_left):
    chans = channels.load()
    q = load_queue()
    if not q["seeded"]:
        log(f"channels crawler: seeded {seed(q, chans)} channels from the ad archive")
    today = now_iso()[:10]
    stale = (dt.date.fromisoformat(today) - dt.timedelta(days=REFRESH_DAYS)).isoformat()
    # stale ones, and older catalog entries that never had posts/day and last-post read
    refresh = sorted((c for c in chans.values() if (c.get("dd") or "") < stale or not c.get("lp")),
                     key=lambda c: (bool(c.get("lp")), -(c.get("v") or 0)))
    session = requests.Session()
    session.headers["User-Agent"] = UA
    visited = new = 0
    while visited < PER_RUN and time_left() > 90:
        if q["pending"]:
            u = q["pending"].pop(0)
        elif refresh:
            u = refresh.pop(0)["u"]
        else:
            break
        if visited:
            time.sleep(GAP)
        visited += 1
        try:
            r = session.get(f"https://t.me/s/{u}", timeout=20)
        except requests.RequestException as e:
            log(f"channels crawler: network error {e}; stopping this run")
            q["pending"].insert(0, u)
            break
        if r.status_code == 429:
            log("channels crawler: t.me asked to slow down (429); stopping this run")
            q["pending"].insert(0, u)
            break
        info = parse(r.text, r.url) if r.status_code == 200 else None
        if not info:
            q["seen"][u] = f"{today}:none"
            continue
        if not is_persian(info["text"]):
            q["seen"][u] = f"{today}:other_language"
            continue
        c = chans.get(u) or {"u": u, "k": [], "sr": "tme"}
        if u not in chans:
            new += 1
        c.update({"t": info["title"], "d": info["desc"][:400], "dd": today, "s": today})
        if info.get("members") is not None:
            c["m"] = info["members"]
            if c.get("e") is None:
                c["e"] = info["members"] >= 1000       # Telegram shows sponsored messages from 1,000 subscribers
        for key, field in (("avg_views", "v"), ("posts_per_day", "pd"), ("last_post", "lp")):
            if info.get(key) is not None:
                c[field] = info[key]
        chans[u] = c
        for link in info["links"]:
            if candidate(link) and link != u and link not in chans and link not in q["seen"] \
                    and link not in q["pending"]:
                q["pending"].append(link)
    channels.save(chans, now_iso())
    save_queue(q)
    log(f"channels crawler: {visited} pages, {new} new Persian channels, {len(chans)} in catalog, "
        f"{len(q['pending'])} waiting")
    return len(chans)
