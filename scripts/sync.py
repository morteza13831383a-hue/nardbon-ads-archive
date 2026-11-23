#!/usr/bin/env python3
"""
Nardbon Telegram Ads archive — sync job.

Every run (GitHub Actions, every 30 minutes):
  1. incremental: pull ads that became visible since the last run (tgadsspy API),
     keep Iranian / Persian ones, update last-seen dates of known ads
  2. backfill:    walk the historical Iran archive a slice at a time until done
  3. media:       download images, shrink to WebP, put them in the outbox
  4. upload:      push outbox media + changed data shards + panel + status to the
                  download host (HTTP receiver nb-upload.php; FTP as fallback). Anything that fails stays queued and is
                  retried on the next run; nothing is dropped until the host has it.
The source of truth is data/ in this repository (committed after each run).
"""
import datetime as dt
import ftplib
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import sys
import time
import traceback
from urllib.parse import urljoin

import requests
from PIL import Image

import channels
import tme_crawler
import channel_import

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ADS_DIR = DATA / "ads"
STATE_F = DATA / "state.json"
SEEDS_F = DATA / "advertiser_seeds.txt"           # one advertiser per line: tg-slug, @handle or its tgadsspy URL
PANEL = ROOT / "panel"
PANEL_CH = ROOT / "panel_channels"              # separate channels panel
CH_ROOT = os.environ.get("REMOTE_CH_ROOT", "tgads/channels").strip("/")   # nb-upload.php only accepts tgads/*
OUTBOX = ROOT / "outbox"            # persisted between runs with actions/cache
OUT_MEDIA = OUTBOX / "media"

API = os.environ.get("API_BASE", "https://tgadsspy.com/api/v1").rstrip("/")
SITE = os.environ.get("MEDIA_BASE", re.sub(r"(https?://[^/]+).*", r"\1", API))
KEY = os.environ.get("TGADS_API_KEY", "")
REMOTE_ROOT = os.environ.get("REMOTE_ROOT", "tgads").strip("/")
LOCAL_UPLOAD_DIR = os.environ.get("LOCAL_UPLOAD_DIR")  # testing only: copy instead of FTP
UPLOAD_URL = os.environ.get("UPLOAD_URL", "").strip()   # HTTP receiver (nb-upload.php); preferred over FTP
UPLOAD_TOKEN = os.environ.get("UPLOAD_TOKEN", "").strip()

PAGE = 100                                   # FREE plan cap per request
MAX_DEPTH = 5000                             # safety cap; the API reports the real depth in meta.maxOffset
TIME_BUDGET = int(os.environ.get("TIME_BUDGET_SEC", "600"))
BACKFILL_ROWS = int(os.environ.get("BACKFILL_ROWS_PER_RUN", "600"))    # one reachable window per run
BACKFILL_GAP = float(os.environ.get("BACKFILL_GAP_SEC", "5"))           # pause between backfill pages
ADV_PER_RUN = int(os.environ.get("ADVERTISERS_PER_RUN", "15"))        # advertiser history sweep (heavy runs only)
HEAVY_EVERY_MIN = int(os.environ.get("HEAVY_EVERY_MIN", "170"))        # images, advertiser sweep, channel crawler ~every 3 h; other runs stay under a minute
ADS_API_ONLY = os.environ.get("ADS_API_ONLY") == "1"  # temporary owner pause: incremental ad API only
ADV_GAP = float(os.environ.get("ADVERTISERS_GAP_SEC", "4"))     # ~15 requests/min, far under the plan limit
ADV_REVISIT_DAYS = 30
MEDIA_PER_RUN = int(os.environ.get("MEDIA_PER_RUN", "100"))             # images per heavy run (download + upload must fit the time budget)
VIDEO_PER_RUN = int(os.environ.get("VIDEO_PER_RUN", "0"))   # 0: the host receiver rejects .mp4 ("type not allowed"); raise once it accepts video
VIDEO_MAX_BYTES = int(os.environ.get("VIDEO_MAX_BYTES", str(4 * 1024 * 1024)))   # bigger videos stay a source link
MEDIA_GAP = float(os.environ.get("MEDIA_GAP_SEC", "0.2"))
MEDIA_MAX_W = 900

# Persian-only letters (پ چ ژ گ and Persian ک ی) — separates Persian from Arabic text
PERSIAN = re.compile("[پچژگکی]")
STARTED = time.time()
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso():
    return iso(dt.datetime.now(dt.timezone.utc))


def time_left():
    return TIME_BUDGET - (time.time() - STARTED)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# --------------------------------------------------------------------------- state
def load_state():
    s = {}
    if STATE_F.exists():
        s = json.loads(STATE_F.read_text("utf-8"))
    s.setdefault("cursor", None)
    s.setdefault("backfill", {"since": None, "done": False, "rows": 0})
    s.setdefault("uploaded", {})          # remote path -> content hash
    s.setdefault("runs", [])
    s.setdefault("last_upload_ok", None)
    s.setdefault("lang_seen", {})
    return s


def save_state(s):
    s["runs"] = s["runs"][-60:]
    STATE_F.write_text(json.dumps(s, ensure_ascii=False, indent=1), "utf-8")


def load_ads():
    ads = {}
    for f in sorted(ADS_DIR.glob("*.json")):
        for a in json.loads(f.read_text("utf-8")):
            ads[a["id"]] = a
    return ads


def month_of(a):
    return (a.get("first_seen") or a.get("found_at") or "0000-00")[:7]


def write_shards(ads):
    """One JSON file per month, one ad per line (small git diffs). Returns {month: path}."""
    months = {}
    for a in ads.values():
        months.setdefault(month_of(a), []).append(a)
    ADS_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for m, items in months.items():
        items.sort(key=lambda a: (a.get("first_seen") or "", a["id"]), reverse=True)
        body = "[\n" + ",\n".join(json.dumps(a, ensure_ascii=False, sort_keys=True) for a in items) + "\n]\n"
        p = ADS_DIR / f"{m}.json"
        if not p.exists() or p.read_text("utf-8") != body:
            p.write_text(body, "utf-8")
        out[m] = p
    for p in ADS_DIR.glob("*.json"):           # drop shards that no longer have ads
        if p.stem not in out:
            p.unlink()
    return out


# --------------------------------------------------------------------------- API
SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json", "User-Agent": "nardbon-archive/1.0"})
if KEY:
    SESSION.headers["X-Api-Key"] = KEY


class RateLimited(Exception):
    def __init__(self, wait):
        super().__init__(f"rate limited for {wait}s")
        self.wait = wait


API_INFO = {}


def api_get(path, params):
    for attempt in range(6):
        try:
            r = SESSION.get(f"{API}{path}", params=params, timeout=40)
        except requests.RequestException as e:
            log("network error", e)
            time.sleep(5 * (attempt + 1))
            continue
        if not API_INFO:
            API_INFO.update({k: r.headers.get(k) for k in ("X-Plan", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset")})
            log("API plan/limits:", API_INFO)
        if r.status_code == 429:
            try:
                wait = int(r.json().get("retryAfter") or 0)
            except Exception:
                wait = 0
            wait = wait or int(r.headers.get("Retry-After", "15") or 15)
            if wait > 90:
                raise RateLimited(wait)       # don't burn Actions minutes; resume next run
            log(f"rate limited, waiting {wait}s")
            time.sleep(wait)
            continue
        if r.status_code >= 500:
            log("server error", r.status_code)
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code != 200:
            raise RuntimeError(f"API {path} {r.status_code}: {r.text[:300]}")
        return r.json()
    raise RuntimeError(f"API {path}: gave up after retries")


def is_persian(ad):
    if (ad.get("geo") or "").upper() == "IR":
        return True
    if (ad.get("lang") or "").lower() in ("fa", "fas", "per", "persian", "farsi"):
        return True
    return bool(PERSIAN.search(f"{ad.get('title') or ''} {ad.get('text') or ''}"))


def merge(ads, raw, source):
    """Insert or update one API ad. Returns 'new', 'upd' or None."""
    aid = str(raw.get("id") or "")
    if not aid:
        return None
    cur = ads.get(aid)
    if cur:
        changed = False
        for k_src, k_dst in (("lastSeenAt", "last_seen"), ("placementCount", "placements")):
            v = raw.get(k_src)
            if v is not None and v != cur.get(k_dst):
                cur[k_dst] = v
                changed = True
        if raw.get("mediaUrl") and cur.get("media_status") in ("failed", "none") and not cur.get("media_file"):
            cur["media_src"] = raw["mediaUrl"]
            cur["media_status"] = "pending"
            changed = True
        return "upd" if changed else None
    media = raw.get("mediaUrl")
    ads[aid] = {
        "id": aid,
        "title": raw.get("title") or "",
        "text": raw.get("text") or "",
        "niche": raw.get("niche"),
        "category": raw.get("category"),
        "geo": raw.get("geo"),
        "lang": raw.get("lang"),
        "advertiser": raw.get("advertiserName") or "",
        "advertiser_slug": raw.get("advertiserSlug") or "",
        "cta_text": raw.get("ctaButtonText") or "",
        "cta_url": raw.get("ctaUrl") or "",
        "target_title": raw.get("targetTitle"),
        "payment": raw.get("paymentSource"),
        "first_seen": raw.get("firstSeenAt"),
        "last_seen": raw.get("lastSeenAt"),
        "placements": raw.get("placementCount"),
        "media_src": media,
        "media_status": "pending" if media else "none",
        "media_file": None,
        "found_at": now_iso(),      # when our system first got it
        "sent_at": None,            # when it reached the download host
        "source": source,
        "labels": {},               # filled later by the AI classifier
    }
    return "new"


def pull(ads, state, params, source, row_cap, stats, keep_all=False, gap=0.0):
    """Page through /ads with offset. Returns (rows_read, max_first_seen, exhausted)."""
    rows, max_fs, offset = 0, None, 0
    stats.pop("_meta", None)
    while offset < MAX_DEPTH and rows < row_cap and time_left() > 120:
        if offset and gap:
            time.sleep(gap)
        page = api_get("/ads", {**params, "limit": PAGE, "offset": offset})
        meta = page.get("meta") or {}
        if offset == 0:
            stats["_meta"] = meta
        items = page.get("data") or []
        for raw in items:
            lang = (raw.get("lang") or "-")
            state["lang_seen"][lang] = state["lang_seen"].get(lang, 0) + 1
            fs = raw.get("firstSeenAt")
            if fs and (max_fs is None or fs > max_fs):
                max_fs = fs
            if keep_all or is_persian(raw):
                r = merge(ads, raw, source)
                if r:
                    stats[r] += 1
        rows += len(items)
        offset += PAGE
        total = meta.get("total")
        if len(items) < PAGE or (total is not None and offset >= total):
            return rows, max_fs, True
        if meta.get("offsetClamped") or meta.get("hasMore") is False:
            return rows, max_fs, False            # plan depth reached: the API would only repeat rows
        if meta.get("maxOffset") is not None and offset > meta["maxOffset"]:
            return rows, max_fs, False
    return rows, max_fs, False


def run_incremental(ads, state, stats):
    # cursor = start of previous run minus 2h overlap (duplicates are merged, never doubled)
    params = {"sort": "new", "geo": "IR"}
    if state["cursor"]:
        params["visibleSince"] = state["cursor"]
    else:
        params["days"] = 2
    rows, _, _ = pull(ads, state, params, "live", 3000, stats, keep_all=True)
    state["cursor"] = iso(NOW - dt.timedelta(hours=1))   # runs every 30 min, 1h overlap
    meta = stats.pop("_meta", None) or {}
    total, reach = meta.get("total"), meta.get("totalReachable")
    if total is not None and reach is not None and total > reach:
        # more ads in this window than the plan lets us page to: some may be lost
        stats["missed"] = total - reach
        state["missed_total"] = state.get("missed_total", 0) + stats["missed"]
        state["missed_last"] = now_iso()
        state.setdefault("missed_windows", []).append(
            {"from": params.get("visibleSince"), "to": now_iso(), "total": total, "reachable": reach})
        state["missed_windows"] = state["missed_windows"][-500:]
    log(f"incremental: read {rows} rows (window total {total}, reachable {reach})")
    return rows


def run_backfill(ads, state, stats):
    """Long-running ("proven") Iran ads, oldest first. sort=proven pages in ascending firstSeenAt,
    so `since` works as a forward cursor; one reachable window per run."""
    bf = state["backfill"]
    if bf["done"]:
        return 0
    # when the API stops honouring `since` the cursor cannot move; then try once a day only
    if (bf.get("stalled") or "") > iso(NOW - dt.timedelta(hours=24)):
        return 0
    params = {"geo": "IR", "sort": "proven"}
    if bf["since"]:
        params["since"] = bf["since"]
    rows, max_fs, exhausted = pull(ads, state, params, "backfill", BACKFILL_ROWS, stats,
                                   keep_all=True, gap=BACKFILL_GAP)
    meta = stats.pop("_meta", None) or {}
    bf["rows"] = bf.get("rows", 0) + rows
    bf["total"] = meta.get("total", bf.get("total"))
    bf["trust"] = meta.get("trustTier")
    if max_fs:
        t = dt.datetime.strptime(max_fs[:19], "%Y-%m-%dT%H:%M:%S") - dt.timedelta(seconds=1)
        nxt = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        if bf["since"] and nxt <= bf["since"]:
            # rows older than our cursor came back: `since` was ignored. Never move backwards,
            # never call this "done"; wait a day and try again.
            bf["stalled"] = now_iso()
            log(f"backfill: API ignored since={bf['since']} (newest row {max_fs}); paused for 24 h")
        else:
            bf.pop("stalled", None)
            bf["done"] = bool(exhausted) and rows < BACKFILL_ROWS and not meta.get("totalApprox")
            bf["since"] = nxt
    elif not meta.get("totalApprox"):
        bf["done"] = True
    log(f"backfill: read {rows} rows, cursor {bf['since']}, done={bf['done']}, trust={bf.get('trust')}")
    return rows


def advertiser_slug(a):
    s = a.get("advertiser_slug") or ""
    if not s and (a.get("advertiser") or "").startswith("@"):
        s = "tg-" + a["advertiser"][1:].strip().lower()
    return s


def run_advertisers(ads, state, stats):
    """History of every known Iranian advertiser through the documented `advertiser` filter.
    Most advertisers have far fewer Iran ads than the reachable depth, so their whole history
    comes in one or two requests. Biggest advertisers first; each revisited after 30 days."""
    sw = state.setdefault("adv_sweep", {"done": {}, "partial": {}, "ads_found": 0})
    stale = iso(NOW - dt.timedelta(days=ADV_REVISIT_DAYS))
    counts = {}
    for a in ads.values():
        s = advertiser_slug(a)
        if s:
            counts[s] = counts.get(s, 0) + 1
    # advertisers discovered by hand / browser (data/advertiser_seeds.txt) come first
    seeds = []
    if SEEDS_F.exists():
        for line in SEEDS_F.read_text("utf-8").splitlines():
            w = line.split("#", 1)[0].strip().lower()
            if w.startswith("@"):
                w = "tg-" + w[1:]
            elif w.startswith("https://tgadsspy.com/advertisers/"):
                w = w.rsplit("/", 1)[-1]
            if re.fullmatch(r"[a-z0-9_-]{3,80}", w) and w not in seeds:
                seeds.append(w)
    sw["seeds"] = len(seeds)
    order = seeds + [s for s in sorted(counts, key=lambda s: -counts[s]) if s not in seeds]
    todo = [s for s in order if sw["done"].get(s, "") < stale]
    sw["known"], sw["waiting"] = len(set(counts) | set(seeds)), len(todo)
    for i, slug in enumerate(todo[:ADV_PER_RUN]):
        if time_left() < 200:
            break
        if i:
            time.sleep(ADV_GAP)
        before = stats["new"]
        _, _, exhausted = pull(ads, state, {"advertiser": slug, "geo": "IR"}, "advertiser", 600, stats,
                               keep_all=True, gap=ADV_GAP)
        meta = stats.pop("_meta", None) or {}
        sw["done"][slug] = now_iso()
        sw["ads_found"] += stats["new"] - before
        total, reach = meta.get("total"), meta.get("totalReachable")
        if total is not None and reach is not None and total > reach:
            sw["partial"][slug] = {"total": total, "reachable": reach}
    log(f"advertisers: {min(len(todo), ADV_PER_RUN)} checked, {sw['waiting']} waiting of {sw['known']}, "
        f"{sw['ads_found']} old ads found so far")


# --------------------------------------------------------------------------- media
def fetch_media(ads, stats):
    OUT_MEDIA.mkdir(parents=True, exist_ok=True)
    for a in ads.values():                      # prepared in an earlier run but never uploaded (that runner is gone)
        if a["media_status"] == "queued" and not a.get("media_file") and not (OUT_MEDIA / f"{a['id']}.webp").exists():
            a["media_status"] = "pending"
    for a in ads.values():                      # retry images that failed only because of relative URLs
        if a["media_status"] == "failed" and (a.get("media_src") or "").startswith("/") and not a.get("media_rel_retry"):
            a["media_status"], a["media_tries"], a["media_rel_retry"] = "pending", 0, True
    todo = [a for a in ads.values() if a["media_status"] == "pending"]
    todo.sort(key=lambda a: a.get("first_seen") or "", reverse=True)    # newest first
    done = 0
    for a in todo[:MEDIA_PER_RUN]:
        if time_left() < 150:
            break
        dest = OUT_MEDIA / f"{a['id']}.webp"
        if dest.exists():
            a["media_status"] = "queued"
            continue
        url = urljoin(SITE + "/", a["media_src"])     # API gives relative paths like /m/3c/...jpg
        if re.search(r"\.(mp4|mov|webm)(\?|$)", url, re.I):
            a["media_status"] = "video"       # handled by fetch_videos
            continue
        try:
            time.sleep(MEDIA_GAP)
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            if "video" in r.headers.get("Content-Type", ""):
                a["media_status"] = "video"   # handled by fetch_videos
                continue
            im = Image.open(io.BytesIO(r.content))
            im = im.convert("RGB")
            if im.width > MEDIA_MAX_W:
                im = im.resize((MEDIA_MAX_W, round(im.height * MEDIA_MAX_W / im.width)), Image.LANCZOS)
            im.save(dest, "WEBP", quality=80, method=4)
            a["media_status"] = "queued"
            a["media_w"], a["media_h"] = im.width, im.height
            done += 1
        except Exception as e:
            a["media_tries"] = a.get("media_tries", 0) + 1
            if a["media_tries"] >= 3:
                a["media_status"] = "failed"
            log("media failed", a["id"], str(e)[:120])
    stats["media"] = done
    log(f"media: {done} images prepared, {max(len(todo) - done, 0)} still waiting")
    fetch_videos(ads)


def fetch_videos(ads):
    """Store small ad videos (<= VIDEO_MAX_BYTES) next to the images; bigger ones keep the source link.
    Each video is tried once."""
    todo = [a for a in ads.values() if a["media_status"] == "video" and not a.get("media_file") and not a.get("video_tried")]
    todo.sort(key=lambda a: a.get("first_seen") or "", reverse=True)
    done = 0
    for a in todo[:VIDEO_PER_RUN]:
        if time_left() < 180:
            break
        url = urljoin(SITE + "/", a.get("media_src") or "")
        try:
            time.sleep(MEDIA_GAP)
            with requests.get(url, timeout=60, stream=True) as r:
                r.raise_for_status()
                if int(r.headers.get("Content-Length") or 0) > VIDEO_MAX_BYTES:
                    a["video_tried"] = True          # too big: keep the source link for good
                    continue
                data = b""
                for chunk in r.iter_content(65536):
                    data += chunk
                    if len(data) > VIDEO_MAX_BYTES:
                        break
            if len(data) > VIDEO_MAX_BYTES or len(data) < 1000:
                a["video_tried"] = True
                continue
            (OUT_MEDIA / f"{a['id']}.mp4").write_bytes(data)
            done += 1
        except Exception as e:
            a["video_tried"] = True
            log("video failed", a["id"], str(e)[:120])
    log(f"media: {done} videos prepared, {max(len(todo) - VIDEO_PER_RUN, 0)} still waiting")


# --------------------------------------------------------------------------- upload
class Uploader:
    def __init__(self):
        self.ftp = None
        self.http = None
        self.dirs = set()

    def __enter__(self):
        if LOCAL_UPLOAD_DIR:
            return self
        if UPLOAD_URL:
            if not UPLOAD_TOKEN:
                raise RuntimeError("UPLOAD_URL is set but UPLOAD_TOKEN is missing")
            self.http = requests.Session()
            self.http.headers["X-Upload-Token"] = UPLOAD_TOKEN
            last = None
            for _ in range(3):
                try:
                    r = self.http.get(UPLOAD_URL, params={"ping": 1}, timeout=30)
                    if r.status_code == 200 and r.json().get("ok"):
                        log("HTTP receiver reachable:", r.json())
                        return self
                    last = f"{r.status_code} {r.text[:200]}"
                except Exception as e:
                    last = e
                time.sleep(5)
            raise RuntimeError(f"HTTP receiver not reachable: {last}")
        host, user, pw = (os.environ.get(k, "") for k in ("FTP_HOST", "FTP_USER", "FTP_PASS"))
        if not host:
            raise RuntimeError("FTP_HOST is not set")
        last = None
        for cls in (ftplib.FTP_TLS, ftplib.FTP):
            try:
                f = cls(timeout=60)
                f.connect(host, 21)
                f.login(user, pw)
                if isinstance(f, ftplib.FTP_TLS):
                    f.prot_p()
                f.set_pasv(True)
                self.ftp = f
                log(f"FTP connected ({cls.__name__})")
                return self
            except Exception as e:
                last = e
                log(f"FTP {cls.__name__} failed: {e}")
        raise RuntimeError(f"FTP connect failed: {last}")

    def __exit__(self, *exc):
        if self.ftp:
            try:
                self.ftp.quit()
            except Exception:
                pass

    def _mkdirs(self, remote_dir):
        if remote_dir in self.dirs or not remote_dir:
            return
        path = ""
        for part in remote_dir.split("/"):
            path = f"{path}/{part}" if path else part
            if path in self.dirs:
                continue
            try:
                self.ftp.mkd(path)
            except ftplib.error_perm:
                pass
            self.dirs.add(path)

    def put(self, local, remote):
        if UPLOAD_URL and not LOCAL_UPLOAD_DIR:
            raw = pathlib.Path(local).read_bytes()
            headers = {"X-Sha1": hashlib.sha1(raw).hexdigest(), "Content-Type": "application/octet-stream"}
            body = raw
            if remote.endswith((".json", ".html", ".txt", ".css", ".js")):
                import gzip
                body = gzip.compress(raw, 6)
                headers["X-Gzip"] = "1"
            last = None
            for attempt in range(3):
                try:
                    r = self.http.post(UPLOAD_URL, params={"path": remote}, data=body, headers=headers, timeout=120)
                    if r.status_code == 200 and r.json().get("ok"):
                        return
                    last = f"{r.status_code} {r.text[:200]}"
                    if r.status_code in (400, 403, 413):
                        break
                except Exception as e:
                    last = e
                time.sleep(3 * (attempt + 1))
            raise RuntimeError(f"upload {remote} failed: {last}")
        if LOCAL_UPLOAD_DIR:
            dst = pathlib.Path(LOCAL_UPLOAD_DIR) / remote
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local, dst)
            return
        self._mkdirs(remote.rsplit("/", 1)[0] if "/" in remote else "")
        tmp = remote + ".part"
        with open(local, "rb") as fh:
            self.ftp.storbinary(f"STOR {tmp}", fh)
        try:
            self.ftp.delete(remote)
        except ftplib.error_perm:
            pass
        self.ftp.rename(tmp, remote)


def sha(p):
    return hashlib.sha1(pathlib.Path(p).read_bytes()).hexdigest()[:16]


def build_status(ads, state, shards):
    today = iso(NOW - dt.timedelta(hours=24))
    status = {
        "generated_at": now_iso(),
        "last_upload_ok": state["last_upload_ok"],
        "totals": {
            "ads": len(ads),
            "with_image": sum(1 for a in ads.values() if a.get("media_file")),
            "new_24h": sum(1 for a in ads.values() if a.get("source") == "live" and (a.get("found_at") or "") >= today),
            "media_waiting": sum(1 for a in ads.values() if a["media_status"] in ("pending", "queued")),
            "not_sent": sum(1 for a in ads.values() if not a.get("sent_at")),
        },
        "backfill": state["backfill"],
        "missed_total": state.get("missed_total", 0),
        "adv_sweep": {k: v for k, v in (state.get("adv_sweep") or {}).items() if k != "done"},
        "missed_last": state.get("missed_last"),
        "runs": list(reversed(state["runs"][-30:])),
    }
    manifest = {
        "generated_at": now_iso(),
        "months": [{"m": m, "n": sum(1 for a in ads.values() if month_of(a) == m), "h": sha(p)}
                   for m, p in sorted(shards.items(), reverse=True)],
    }
    return status, manifest


CH_PART_ROWS = int(os.environ.get("CHANNELS_PART_ROWS", "20000"))   # ~1.5 MB gzipped each; the host rejects big bodies


def channel_parts():
    """Split data/channels.json into channels-NN.json parts plus a small channels.json index
    {"at", "fields", "parts": [...], "rows": []}, because the host's receiver rejects one big file."""
    import tempfile
    body = json.loads(channels.CHANNELS_F.read_text("utf-8"))
    rows, out = body.get("rows") or [], pathlib.Path(tempfile.mkdtemp(prefix="chparts-"))
    names, files = [], []
    for i in range(0, max(len(rows), 1), CH_PART_ROWS):
        name = f"channels-{i // CH_PART_ROWS:02d}.json"
        f = out / name
        f.write_text(json.dumps({"rows": rows[i:i + CH_PART_ROWS]}, ensure_ascii=False, separators=(",", ":")), "utf-8")
        names.append(name)
        files.append((f, f"{CH_ROOT}/{name}"))
    idx = out / "channels.json"
    idx.write_text(json.dumps({"at": body.get("at"), "fields": body.get("fields"), "parts": names, "rows": []},
                              ensure_ascii=False, separators=(",", ":")), "utf-8")
    return files + [(idx, f"{CH_ROOT}/channels.json")]


def upload(ads, state, run):
    """Push everything pending. Raises on connection failure; partial progress is kept."""
    tmp = OUTBOX / "_gen"
    tmp.mkdir(parents=True, exist_ok=True)
    sent_media = sent_files = 0
    with Uploader() as up:
        # 1) images
        for f in sorted(OUT_MEDIA.glob("*.webp")) + sorted(OUT_MEDIA.glob("*.mp4")):
            if time_left() < 60:
                break
            try:
                up.put(f, f"{REMOTE_ROOT}/media/{f.name}")
            except RuntimeError as e:
                if f.suffix != ".mp4":
                    raise
                log(f"video upload refused, keeping the source link: {e}")   # e.g. the host rejects .mp4
                f.unlink()
                if ads.get(f.stem):
                    ads[f.stem]["video_tried"] = True
                continue
            a = ads.get(f.stem)
            if a:
                a["media_file"] = f"media/{f.name}"
                if f.suffix == ".webp":
                    a["media_status"] = "ok"
            f.unlink()
            sent_media += 1
        # 2) data shards (rewritten so media paths are current)
        sent_now = now_iso()
        pending = [a for a in ads.values() if not a.get("sent_at")]
        for a in pending:
            a["sent_at"] = sent_now
        try:
            shards = write_shards(ads)
            for m, p in shards.items():
                remote = f"{REMOTE_ROOT}/data/{m}.json"
                h = sha(p)
                if state["uploaded"].get(remote) != h:
                    up.put(p, remote)
                    state["uploaded"][remote] = h
                    sent_files += 1
        except Exception:
            for a in pending:            # not confirmed on the host -> still "not sent"
                a["sent_at"] = None
            raise
        # 3) panel + channel catalog (only changed files)
        files = [(f, f"{REMOTE_ROOT}/{f.relative_to(PANEL).as_posix()}") for f in sorted(PANEL.rglob("*")) if f.is_file()]
        if PANEL_CH.exists():
            files += [(f, f"{CH_ROOT}/{f.relative_to(PANEL_CH).as_posix()}") for f in sorted(PANEL_CH.rglob("*")) if f.is_file()]
            files += [(f, f"{CH_ROOT}/fonts/{f.name}") for f in sorted((PANEL / "fonts").glob("*.woff2"))]
        if channels.CHANNELS_F.exists():
            files += channel_parts()             # parts first, the index (channels.json) last
        for f, remote in files:
            h = sha(f)
            if state["uploaded"].get(remote) != h:
                up.put(f, remote)
                state["uploaded"][remote] = h
                sent_files += 1
        # 4) manifest + status last, so the panel never points at missing files
        state["last_upload_ok"] = now_iso()
        run.update({"sent_media": sent_media, "sent_files": sent_files, "ok": not run.get("error"),
                    "secs": round(time.time() - STARTED)})
        state["runs"].append(run)
        status, manifest = build_status(ads, state, shards)
        state["runs"].pop()
        ch_status = {
            "enabled": channels.enabled(), "total": state.get("channels_total", 0),
            "idx": state.get("channels_idx", 0), "queries": len(channels.keywords()) + 1,
            "last_slice": state.get("channels_last"), "full_pass": state.get("channels_at"),
            "blocked_until": max(state.get("channels_blocked_until") or "", state.get("api_blocked_until") or "") or None,
            "every_hours": channels.EVERY_H,
            "coverage": channel_import.coverage(channels.load()),
            "crawler_waiting": len(tme_crawler.load_queue()["pending"]),
            "crawler_found": sum(1 for c in channels.load().values() if c.get("sr") == "tme"),
        }
        for name, obj, root in (("manifest.json", manifest, REMOTE_ROOT), ("status.json", status, REMOTE_ROOT),
                                ("status.json", ch_status, CH_ROOT)):
            p = tmp / f"{root.replace('/', '_')}-{name}"
            p.write_text(json.dumps(obj, ensure_ascii=False), "utf-8")
            up.put(p, f"{root}/{name}")
    log(f"upload: {sent_media} images, {sent_files} files")


# --------------------------------------------------------------------------- main
def main():
    DATA.mkdir(exist_ok=True)
    OUT_MEDIA.mkdir(parents=True, exist_ok=True)
    state = load_state()
    ads = load_ads()
    from ad_images import apply_receipts
    apply_receipts(ads)
    stats = {"new": 0, "upd": 0, "media": 0}
    run = {"at": now_iso(), "ok": False, "error": None}
    errors = []

    # Actions bills every run by the started minute, so the slow work happens about every
    # 2 h and the rest of the runs only pick up new ads (under a minute each)
    heavy = not ADS_API_ONLY and (state.get("heavy_last") or "") < iso(NOW - dt.timedelta(minutes=HEAVY_EVERY_MIN))
    if heavy:
        state["heavy_last"] = now_iso()
    blocked = state.get("api_blocked_until")
    if blocked and blocked > now_iso():
        log(f"API paused until {blocked} (rate limit) — skipping fetch this run")
    else:
        for step in ((run_incremental,) if ADS_API_ONLY else (run_incremental, run_backfill, run_advertisers)):
            if step is not run_incremental and not heavy:
                continue
            try:
                step(ads, state, stats)
            except RateLimited as e:
                state["api_blocked_until"] = iso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=e.wait))
                errors.append(f"api: rate limited, paused until {state['api_blocked_until']}")
                log(errors[-1])
                break
            except Exception as e:
                errors.append(f"{step.__name__}: {e}")
                traceback.print_exc()
    # phase 2: a small slice of the Persian channel catalog each run (own key; off without one)
    ch_block = state.get("channels_blocked_until") or ""
    if not ADS_API_ONLY and channels.enabled() and channels.due(state, dt.datetime.now(dt.timezone.utc)) \
            and not ch_block > now_iso() and not (state.get("api_blocked_until") or "") > now_iso():
        try:
            state["channels_total"] = channels.step(state, log, now_iso)
        except channels.Blocked as e:
            until = iso(dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=e.wait))
            state["channels_blocked_until"] = until
            if e.shared:
                state["api_blocked_until"] = max(state.get("api_blocked_until") or "", until)
            errors.append(f"channels: API paused until {until}" + (" (whole identity)" if e.shared else ""))
        except Exception as e:
            errors.append(f"channels: {e}")
            traceback.print_exc()
    # Persian channel crawler on public t.me pages (no API key, no account); heavy runs only.
    # It also refreshes descriptions, so the separate description step is no longer needed.
    if heavy and os.environ.get("TME_CRAWLER") == "1":      # off: channels come from tgadsspy (see channel_import)
        try:
            state["channels_total"] = tme_crawler.step(log, now_iso, time_left)
        except Exception as e:
            log(f"channels crawler failed: {e}")      # never fails the run
            traceback.print_exc()
    elif heavy:
        # bio only: read the description of channels we already have (from tgadsspy) on their
        # public t.me page. Finds no new channels; biggest channels first, refreshed monthly.
        try:
            channels.fill_descriptions(log, now_iso, time_left)
        except Exception as e:
            log(f"descriptions failed: {e}")      # never fails the run
    # search keywords built from our own database (heavy runs only; a few seconds, no network)
    if heavy:
        try:
            import build_keywords
            build_keywords.main()
        except Exception as e:
            log(f"keyword list failed: {e}")
    # Persian groups/channels from combot.org top lists (once a day, ~7 paced requests)
    if not ADS_API_ONLY:
        try:
            import combot_collect
            if combot_collect.due(state, NOW):
                combot_collect.step(state, log, now_iso)
        except Exception as e:
            log(f"combot failed: {e}")          # never fails the run
    # channel pages collected from tgadsspy by the external crawler (no network here)
    if not ADS_API_ONLY:
        try:
            channel_import.run(log, now_iso)
        except Exception as e:
            errors.append(f"channel import: {e}")
            traceback.print_exc()
    # list of known Iranian advertisers for the external crawler (biggest first)
    if not ADS_API_ONLY:
        try:
            counts = {}
            for a in ads.values():
                s_ = advertiser_slug(a)
                if s_:
                    counts[s_] = counts.get(s_, 0) + 1
            text = "\n".join(sorted(counts, key=lambda k: (-counts[k], k))) + "\n"
            f = DATA / "iran_advertisers.txt"
            if not f.exists() or f.read_text("utf-8") != text:
                f.write_text(text, "utf-8")
        except Exception as e:
            log(f"advertiser list failed: {e}")
    if stats.get("missed"):
        errors.append(f"missed: up to {stats['missed']} ads beyond the API plan's reach in this window")
    if API_INFO:
        state["api_info"] = API_INFO
    if heavy:
        try:
            fetch_media(ads, stats)
        except Exception as e:
            errors.append(f"media: {e}")
            traceback.print_exc()

    write_shards(ads)
    run["error"] = " | ".join(errors)[:500] or None      # so status.json already shows this run's problems
    run.update({"new": stats["new"], "updated": stats["upd"], "media": stats["media"], "missed": stats.get("missed", 0)})
    try:
        upload(ads, state, run)
    except Exception as e:
        errors.append(f"upload: {e}")
        traceback.print_exc()
        log("upload failed — everything stays queued for the next run")
    write_shards(ads)

    run["error"] = " | ".join(errors)[:500] or None
    run["ok"] = bool(run.get("sent_files") is not None) and not errors
    run["secs"] = round(time.time() - STARTED)
    state["runs"].append(run)
    save_state(state)
    log("done", json.dumps(run, ensure_ascii=False))
    # data is committed by the workflow either way; a red run makes GitHub send an alert email.
    # Only running out of API quota is treated as normal.
    hard = [e for e in errors if not e.startswith("api: rate")]
    if hard:
        sys.exit(1)


if __name__ == "__main__":
    main()
