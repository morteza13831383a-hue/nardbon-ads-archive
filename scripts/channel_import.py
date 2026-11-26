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
from datetime import datetime, timezone
import glob
from pathlib import Path
import gzip
import json
import re

import channels

IMPORT_DIR = channels.ROOT / "data" / "channel-import"
HTML_DIR = channels.ROOT / "data" / "channel-html-import"     # buckets from scripts/channels_html.py
TGSTAT_DIR = channels.ROOT / "data" / "tgstat-import"          # Iran-related channels and groups from tgstat.com
TELEMETR_DIR = channels.ROOT / "data" / "telemetr-import"      # telemetr.io catalog (same row format)
TSE_DIR = channels.ROOT / "data" / "telegramsearchengine-import"
TGCHME_DIR = channels.ROOT / "data" / "tgchannelsme-import"   # telegramchannels.me catalog (same row format)
LYZEM_DIR = channels.ROOT / "data" / "lyzem-import"            # keyword-search results from lyzem.com (same row format)
COMBOT_DIR = channels.ROOT / "data" / "combot-import"          # Persian groups/channels from combot.org top lists
TGLIST_DIR = channels.ROOT / "data" / "tglist-import"          # keyword-search results from tglist.io (same row format)
PLACEMENTS_F = channels.ROOT / "data" / "placements.json"
USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")


def load_placements():
    if PLACEMENTS_F.exists():
        return json.loads(PLACEMENTS_F.read_text("utf-8"))
    return {"done_parts": [], "rows": {}}       # rows: "advertiser|channel" -> {...}


# Iran vs Afghanistan (both write Persian; TGStat's country label is not reliable).
# Concrete signals only: cities, currency, phone codes, domains, everyday Iranian services.
IRAN_SIGNS = re.compile(
    r"ایران|تهران|اصفهان|مشهد|شیراز|تبریز|کرج|اهواز|(?<![\u0600-\u06FF])قم(?![\u0600-\u06FF])|کرمانشاه|رشت|ارومیه|زاهدان|همدان|یزد|اردبیل|"
    r"بندرعباس|کرمان|قزوین|ساری|گرگان|سنندج|خرم\s*آباد|بوشهر|بیرجند|سمنان|ایلام|یاسوج|شهرکرد|زنجان|"
    r"تومان|ریال|کارت\s*به\s*کارت|شماره\s*شبا|دیجی\s*کالا|اسنپ|ایرانسل|همراه\s*اول|کنکور|"
    r"\+98|(?<!\d)0?9[0-3]\d[\s-]?\d{3}[\s-]?\d{4}(?!\d)|\.ir\b|Iran\b|Tehran", re.I)
AFGHAN_SIGNS = re.compile(
    r"افغانستان|افغان|کابل|هرات|مزار\s*شریف|قندهار|جلال\s*آباد|بلخ|بامیان|غزنی|کندز|بدخشان|"
    r"طالبان|امارت\s*اسلامی|افغانی|\+93|\.af\b|Afghan|Kabul|Herat", re.I)


def region(text):
    """'ir', 'af' or None (unknown) from concrete signals in title, bio and post sample."""
    ir, af = len(IRAN_SIGNS.findall(text or "")), len(AFGHAN_SIGNS.findall(text or ""))
    if ir > af:
        return "ir"
    if af > ir:
        return "af"
    return None


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
    catalog_keys = {key.lower(): key for key in chans}
    n = 0
    for path in sorted(glob.glob(str(HTML_DIR / "channels-*.jsonl.gz"))):
        name = path.rsplit("/", 1)[-1]
        raw = Path(path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if seen.get(name) == digest:
            continue
        for line in gzip.decompress(raw).decode("utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            u = (r.get("username") or "").strip().lstrip("@").lower()
            iran_advs = r.get("iran_advertisers") or []
            if not USERNAME.match(u) or (r.get("classification") == "unclassified" and not iran_advs):
                continue
            m = r.get("metrics") or {}
            if r.get("source") == "tme" and u not in chans:
                u = catalog_keys.get(u, u)
            c = chans.get(u) or {"u": u, "k": [], "sr": "tme" if r.get("source") == "tme" else "tgadsspy"}
            for adv in iran_advs:
                key = f"{adv}|{u}"
                pl["rows"].setdefault(key, {"a": adv, "c": u, "n": 0})
            # Immutable batches can arrive out of chronological order.
            stamps = pl.setdefault("html_collected_at", {})
            try:
                instant = datetime.fromisoformat(r.get("collected_at") or "")
                stamp = instant.astimezone(timezone.utc).isoformat(timespec="microseconds") if instant.tzinfo else ""
            except (ValueError, TypeError):
                stamp = ""
            if stamps.get(u, "") > stamp:
                continue
            if stamp:
                stamps[u] = stamp
            if r.get("record_detail") in ("profile", "listing"):
                c["kd"] = "channel"
            if r.get("country"):
                c["co"] = r["country"]
            if r.get("language"):
                c["lg"] = r["language"]
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
            if r.get("source") == "tme":
                if r.get("region_signal") and not c.get("rg"):
                    c["rg"] = r["region_signal"]
            if r.get("source") != "tme":
                c["cl"] = r.get("classification")
            chans[u] = c
            n += 1
        seen[name] = digest
    if n:
        log(f"channel import: {n} channel profiles from channels_html buckets")
    return n


def import_tgstat(chans, pl, log, now_iso, directory=None, seen_key="tgstat_files", source="tgstat", stamp="ts"):
    """tgstat.com (permission granted for Iran-related channels and groups). Parts may be new
    files or rewritten buckets: each is re-read when its digest changes. One row per entity:
    {"type": "channel"|"group", "username", "title", "description", "members", "avg_reach",
     "err", "category", "country", "language", "url", "collected_at"}"""
    import hashlib
    seen = pl.setdefault(seen_key, {})
    n = 0
    entities_f=channels.ROOT/'data'/'tgstat-entities.json'
    entities=json.loads(entities_f.read_text('utf-8'))if entities_f.exists()else{}
    entities_changed=False
    tgme_keys = {key.lower(): key for key in chans} if source == "tgme" else {}
    for path in sorted(glob.glob(str((directory or TGSTAT_DIR) / "*.jsonl.gz"))):
        name = path.rsplit("/", 1)[-1]
        raw = Path(path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if seen.get(name) == digest:
            continue
        for line in gzip.decompress(raw).decode("utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            u = (r.get("username") or "").lstrip("@")
            if r.get('type')not in('channel','group'):continue
            if not USERNAME.match(u):
                if r.get('source_id'):
                    key=r['type']+':'+str(r['source_id'])
                    r['region']=region(' '.join(str(r.get(k)or'')for k in('title','description','text_sample')))
                    entities[key]=r;entities_changed=True;n+=1
                continue
            private_key=r['type']+':'+str(r.get('source_id',''))
            if private_key in entities:entities.pop(private_key);entities_changed=True
            if source == "tgme" and u not in chans:
                u = tgme_keys.get(u.lower(), u)
            c = chans.get(u) or {"u": u, "k": [], "sr": source}
            c["kd"] = r["type"]
            for src, dst in (("title", "t"), ("category", "c"), ("country", "co"), ("language", "lg")):
                if r.get(src) and (source != "tgme" or not c.get(dst)):
                    c[dst] = r[src]
            if r.get("description"):
                c["d"] = str(r["description"])[:400]
                c["dd"] = (r.get("collected_at") or now_iso())[:10]
            members = r.get("members") if isinstance(r.get("members"), int) else metric(r.get("members"))
            if members:
                c["m"] = members
                if c["kd"] == "channel":
                    c.setdefault("e", members >= 1000)
            reach = r.get("avg_reach") if isinstance(r.get("avg_reach"), int) else metric(r.get("avg_reach"))
            if reach and not c.get("v"):
                c["v"] = reach                      # TGStat average post reach ~ views per post
            if r.get("err") not in (None, ""):
                c["er"] = r["err"]
            if stamp:
                c[stamp] = (r.get("collected_at") or now_iso())[:10]
            rg = region(" ".join(str(r.get(k) or "") for k in ("title", "description", "text_sample")))
            if rg:
                c["rg"] = rg
            chans[u] = c
            n += 1
        seen[name] = digest
    if entities_changed:entities_f.write_text(json.dumps(entities,ensure_ascii=False,separators=(',',':')),'utf-8')
    if n:
        log(f"channel import: {n} {source} channels/groups")
    return n


def coverage(chans):
    """Lincoln-Petersen estimate from two independent sources (TGStat and tgadsspy):
    N ~= n1 * n2 / m, where m is the number of channels both found. Reported overall and for
    channels with 1,000+ subscribers (where Telegram Ads can run). An estimate, not a census."""
    out = {}
    for label, keep in (("all", lambda c: True), ("1000plus", lambda c: (c.get("m") or 0) >= 1000)):
        pool = [c for c in chans.values() if (c.get("kd") or "channel") == "channel" and keep(c)]
        a = {c["u"].lower() for c in pool if c.get("ts")}                                   # TGStat
        b = {c["u"].lower() for c in pool if c.get("cl") or c.get("sr") == "tgadsspy" or c.get("s") and not c.get("ts") and c.get("sr") != "tme"}
        m = len(a & b)
        est = round(len(a) * len(b) / m) if m >= 20 else None
        union = len(a | b)
        out[label] = {"tgstat": len(a), "tgadsspy": len(b), "both": m, "union": union,
                      "estimated_total": est,
                      "estimated_coverage_pct": round(100 * union / est, 1) if est else None}
    return out


def run(log, now_iso):
    parts = sorted(glob.glob(str(IMPORT_DIR / "*.jsonl.gz")))
    pl = load_placements()
    todo = [p for p in parts if p.rsplit("/", 1)[-1] not in pl["done_parts"]]
    chans = channels.load()
    n_html = import_html_buckets(chans, pl, log, now_iso)
    n_html += import_tgstat(chans, pl, log, now_iso)
    n_html += import_tgstat(chans, pl, log, now_iso, TGLIST_DIR, "tglist_files", "tglist", "tl")
    n_html += import_tgstat(chans, pl, log, now_iso, COMBOT_DIR, "combot_files", "combot", "cb")
    n_html += import_tgstat(chans, pl, log, now_iso, LYZEM_DIR, "lyzem_files", "lyzem", "lz")
    n_html += import_tgstat(chans, pl, log, now_iso, TELEMETR_DIR, "telemetr_files", "telemetr", "tm")
    n_html += import_tgstat(chans, pl, log, now_iso, TSE_DIR, "tse_files", "telegramsearchengine", "tse")
    n_html += import_tgstat(chans, pl, log, now_iso, channels.ROOT / "data/tgme-import", "tgme_files", "tgme", None)
    n_html += import_tgstat(chans, pl, log, now_iso, TGCHME_DIR, "tgchannelsme_files", "tgchannelsme", "tc")
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
    for c in chans.values():
        if not c.get("rg"):
            rg = region(f"{c.get('t') or ''} {c.get('d') or ''}")
            if rg:
                c["rg"] = rg
    for u, (advs, creatives) in per.items():
        c = chans.setdefault(u, {"u": u, "k": [], "sr": "tgadsspy"})
        c["ia"], c["ic"] = advs, creatives
    channels.save(chans, now_iso())
    PLACEMENTS_F.write_text(json.dumps(pl, ensure_ascii=False, separators=(",", ":")), "utf-8")
    log(f"channel import: {len(todo)} parts, {n_ch} channel rows, {n_pl} placements, {bad} rejected; "
        f"{len(per)} channels with Iranian advertisers")
    return n_ch + n_pl
