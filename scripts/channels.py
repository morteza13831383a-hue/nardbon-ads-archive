"""
Persian Telegram channel catalog (phase 2: channel suggestions).

Once a day, sync.py calls refresh(): it asks the tgadsspy /channels endpoint for
Persian channels, once for the biggest overall and once per keyword in
data/channel_keywords.txt, and merges everything into data/channels.json.
The panel's "کانال‌ها" tab searches that file locally.

Each keyword query reaches only as deep as the API plan allows (meta.maxOffset),
so coverage comes from many keywords rather than deep paging.
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
KEYWORDS_F = ROOT / "data" / "channel_keywords.txt"
CHANNELS_F = ROOT / "data" / "channels.json"
PERSIAN = re.compile("[پچژگکی]")
PAGE = 100
# compact rows keep the file small for the browser
FIELDS = ["u", "t", "m", "v", "c", "e", "k", "s"]   # username, title, members, avg views, category, sponsor-eligible, keywords, last snapshot


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
    body = {"at": at, "fields": FIELDS,
            "rows": [[c.get(f) for f in FIELDS] for c in rows]}
    text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    if not CHANNELS_F.exists() or CHANNELS_F.read_text("utf-8") != text:
        CHANNELS_F.write_text(text, "utf-8")


def is_persian(ch):
    return (ch.get("lang") or "").lower() == "fa" or bool(PERSIAN.search(ch.get("title") or ""))


def fetch(api_get, params, time_left, log):
    """All rows the plan lets us page to for one query."""
    rows, offset = [], 0
    while time_left() > 180:
        page = api_get("/channels", {**params, "limit": PAGE, "offset": offset})
        items = page.get("data") or []
        rows += items
        meta = page.get("meta") or {}
        offset += PAGE
        if (len(items) < PAGE or meta.get("hasMore") is False or meta.get("offsetClamped")
                or (meta.get("maxOffset") is not None and offset > meta["maxOffset"])):
            break
    return rows


def refresh(api_get, time_left, log, now_iso):
    chans = load()
    seen_now = set()
    queries = [("", {"lang": "fa"})] + [(w, {"q": w}) for w in keywords()]
    done = 0
    for kw, params in queries:
        if time_left() < 240:
            log(f"channels: out of time after {done}/{len(queries)} queries")
            break
        try:
            items = fetch(api_get, params, time_left, log)
        except Exception as e:
            if type(e).__name__ == "RateLimited":
                save(chans, now_iso())
                raise                      # stop at once: every further request extends the block
            log(f"channels: query {params} failed: {e}")
            continue
        done += 1
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
            seen_now.add(u)
    save(chans, now_iso())
    log(f"channels: {done}/{len(queries)} queries, {len(seen_now)} seen this run, {len(chans)} in catalog")
    return done == len(queries), len(chans)
