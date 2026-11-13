"""
Import channel data collected from tgadsspy's public HTML pages by the external crawler.

The crawler (run where tgadsspy pages are reachable) writes gzip JSON-lines parts to
data/channel-import/ :

  {"type": "placement", "advertiser": "tg-slug", "channel": "username", "creatives": 12,
   "first_seen": "...", "last_seen": "...", "source_url": "https://tgadsspy.com/advertisers/tg-slug/channels",
   "collected_at": "..."}
      one row per channel listed on an Iranian advertiser's /advertisers/<slug>/channels page

  {"type": "channel", "username": "name", "title": "...", "members": 12345, "avg_views": 2100,
   "lang": "fa", "category": "...", "sponsored_eligible": true, "description": "...",
   "source_url": "https://tgadsspy.com/channels/name", "collected_at": "..."}
      details from /channels/<username>

This module upserts them, by username / (advertiser, channel), into
  data/channels.json    (catalog used by the channels panel; source "tgadsspy")
  data/placements.json  (who bought where: the basis for channel suggestions)
Re-importing the same parts never creates duplicates. Runs on every sync; no network.
"""
import glob
import gzip
import json
import re

import channels

IMPORT_DIR = channels.ROOT / "data" / "channel-import"
HTML_DIR = channels.ROOT / "data" / "channel-html-import"     # buckets from scripts/channels_html.py
PLACEMENTS_F = channels.ROOT / "data" / "placements.json"
USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")


def load_placements():
    if PLACEMENTS_F.exists():
        return json.loads(PLACEMENTS_F.read_text("utf-8"))
    return {"done_parts": [], "rows": {}}       # rows: "advertiser|channel" -> {...}


def metric(text):
    """'5,972' -> 5972, '12.3K' -> 12300, '1.2M' -> 1200000, '—' -> None."""
    t = str(text or "").replace(",", "").replace("\u00a0", "").strip()
    m = re.fullmatch(r"([\d.]+)\s*([KkMm]?)", t)
    return int(float(m[1]) * {"": 1, "k": 1_000, "m": 1_000_000}[m[2].lower()]) if m else None


def import_html_buckets(chans, pl, log, now_iso):
    """channels_html.py rewrites 16 hash buckets as it grows; re-read a bucket when its digest
    changes. Only Persian / Iran-evidence channels go into the catalog."""
    import hashlib
    seen = pl.setdefault("html_buckets", {})
    n = 0
    for path in sorted(glob.glob(str(HTML_DIR / "channels-*.jsonl.gz"))):
        name = path.rsplit("/", 1)[-1]
        raw = open(path, "rb").read()
        digest = hashlib.sha256(raw).hexdigest()
        if seen.get(name) == digest:
            continue
        for line in gzip.decompress(raw).decode("utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            u = r.get("username") or ""
            iran_advs = r.get("iran_advertisers") or []
            if not USERNAME.match(u) or (r.get("classification") == "unclassified" and not iran_advs):
                continue
            m = r.get("metrics") or {}
            c = chans.get(u) or {"u": u, "k": [], "sr": "tgadsspy"}
            if r.get("title"):
                c["t"] = r["title"]
            if r.get("description"):
                c["d"] = str(r["description"])[:400]
                c["dd"] = (r.get("collected_at") or now_iso())[:10]
            members = r.get("members") or metric(m.get("Members"))
            if members:
                c["m"] = members = int(members)
                c.setdefault("e", members >= 1000)
            views = r.get("avg_views") or metric(m.get("Avg. views"))
            if views:
                c["v"] = int(views)
            if r.get("category"):
                c["c"] = r["category"]
            c["s"] = (r.get("collected_at") or now_iso())[:10]
            c["cl"] = r.get("classification")
            for adv in iran_advs:                       # Iranian advertisers linked from the channel page
                key = f"{adv}|{u}"
                pl["rows"].setdefault(key, {"a": adv, "c": u, "n": 0})
            chans[u] = c
            n += 1
        seen[name] = digest
    if n:
        log(f"channel import: {n} channel profiles from channels_html buckets")
    return n


def run(log, now_iso):
    parts = sorted(glob.glob(str(IMPORT_DIR / "*.jsonl.gz")))
    pl = load_placements()
    todo = [p for p in parts if p.rsplit("/", 1)[-1] not in pl["done_parts"]]
    chans = channels.load()
    n_html = import_html_buckets(chans, pl, log, now_iso)
    if not todo and not n_html:
        return 0
    n_ch = n_pl = bad = 0
    for path in todo:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                if r.get("type") == "placement":
                    adv, ch = (r.get("advertiser") or "").lower(), r.get("channel") or ""
                    if not re.fullmatch(r"[a-z0-9_-]{3,80}", adv) or not USERNAME.match(ch):
                        bad += 1
                        continue
                    key = f"{adv}|{ch}"
                    cur = pl["rows"].get(key, {})
                    # keep the widest view if the same pair is collected twice
                    cur.update({"a": adv, "c": ch,
                                "n": max(int(r.get("creatives") or 0), cur.get("n", 0)),
                                "f": min(filter(None, [cur.get("f"), r.get("first_seen")]), default=None),
                                "l": max(filter(None, [cur.get("l"), r.get("last_seen")]), default=None)})
                    pl["rows"][key] = cur
                    n_pl += 1
                elif r.get("type") == "channel":
                    u = r.get("username") or ""
                    if not USERNAME.match(u):
                        bad += 1
                        continue
                    c = chans.get(u) or {"u": u, "k": []}
                    for src, dst in (("title", "t"), ("members", "m"), ("avg_views", "v"), ("category", "c"),
                                     ("description", "d")):
                        if r.get(src) not in (None, ""):
                            c[dst] = r[src] if dst != "d" else str(r[src])[:400]
                    if r.get("sponsored_eligible") is not None:
                        c["e"] = bool(r["sponsored_eligible"])
                    c["s"] = (r.get("collected_at") or now_iso())[:10]
                    c.setdefault("sr", "tgadsspy")
                    chans[u] = c
                    n_ch += 1
                else:
                    bad += 1
        pl["done_parts"].append(path.rsplit("/", 1)[-1])
    # per-channel summary for the panel: how many Iranian advertisers / creatives ran there
    per = {}
    for row in pl["rows"].values():
        p = per.setdefault(row["c"], [0, 0])
        p[0] += 1
        p[1] += row.get("n", 0)
    for u, (advs, creatives) in per.items():
        c = chans.setdefault(u, {"u": u, "k": [], "sr": "tgadsspy"})
        c["ia"], c["ic"] = advs, creatives
    channels.save(chans, now_iso())
    PLACEMENTS_F.write_text(json.dumps(pl, ensure_ascii=False, separators=(",", ":")), "utf-8")
    log(f"channel import: {len(todo)} parts, {n_ch} channel rows, {n_pl} placements, {bad} rejected; "
        f"{len(per)} channels with Iranian advertisers")
    return n_ch + n_pl
