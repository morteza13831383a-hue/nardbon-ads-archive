#!/usr/bin/env python3
"""
Nardbon Telegram Ads archive — sync job.

Every run (GitHub Actions, hourly):
  1. incremental: pull ads that became visible since the last run (tgadsspy API),
     keep Iranian / Persian ones, update last-seen dates of known ads
  2. backfill:    walk the historical Iran archive a slice at a time until done
  3. media:       download images, shrink to WebP, put them in the outbox
  4. upload:      push outbox media + changed data shards + panel + status to the
                  download host over FTP. Anything that fails stays queued and is
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

import requests
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
ADS_DIR = DATA / "ads"
STATE_F = DATA / "state.json"
PANEL = ROOT / "panel"
OUTBOX = ROOT / "outbox"            # persisted between runs with actions/cache
OUT_MEDIA = OUTBOX / "media"

API = os.environ.get("API_BASE", "https://tgadsspy.com/api/v1").rstrip("/")
KEY = os.environ.get("TGADS_API_KEY", "")
REMOTE_ROOT = os.environ.get("REMOTE_ROOT", "tgads").strip("/")
LOCAL_UPLOAD_DIR = os.environ.get("LOCAL_UPLOAD_DIR")  # testing only: copy instead of FTP

PAGE = 100                                   # FREE plan cap per request
MAX_DEPTH = 5000                             # FREE plan paging depth
TIME_BUDGET = int(os.environ.get("TIME_BUDGET_SEC", "600"))
BACKFILL_ROWS = int(os.environ.get("BACKFILL_ROWS_PER_RUN", "1500"))   # ~36k/day, under 50k
MEDIA_PER_RUN = int(os.environ.get("MEDIA_PER_RUN", "500"))
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


def api_get(path, params):
    for attempt in range(6):
        try:
            r = SESSION.get(f"{API}{path}", params=params, timeout=40)
        except requests.RequestException as e:
            log("network error", e)
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 429:
            try:
                wait = int(r.json().get("retryAfter") or 0)
            except Exception:
                wait = 0
            wait = wait or int(r.headers.get("Retry-After", "15") or 15)
            log(f"rate limited, waiting {wait}s")
            time.sleep(min(wait, 120))
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


def pull(ads, state, params, source, row_cap, stats, keep_all=False):
    """Page through /ads with offset. Returns (rows_read, max_first_seen, exhausted)."""
    rows, max_fs, offset = 0, None, 0
    while offset < MAX_DEPTH and rows < row_cap and time_left() > 120:
        page = api_get("/ads", {**params, "limit": PAGE, "offset": offset})
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
        total = (page.get("meta") or {}).get("total")
        if len(items) < PAGE or (total is not None and offset >= total):
            return rows, max_fs, True
    return rows, max_fs, False


def run_incremental(ads, state, stats):
    # cursor = start of previous run minus 2h overlap (duplicates are merged, never doubled)
    params = {"sort": "new"}
    if state["cursor"]:
        params["visibleSince"] = state["cursor"]
    else:
        params["days"] = 2
    rows, _, _ = pull(ads, state, params, "live", 10 ** 9, stats)
    state["cursor"] = iso(NOW - dt.timedelta(hours=2))
    log(f"incremental: read {rows} rows")
    return rows


def run_backfill(ads, state, stats):
    bf = state["backfill"]
    if bf["done"]:
        return 0
    # sort=proven is oldest-first by firstSeenAt, so `since` works as a forward cursor
    params = {"geo": "IR", "sort": "proven", "days": 365}
    if bf["since"]:
        params["since"] = bf["since"]
    rows, max_fs, exhausted = pull(ads, state, params, "backfill", BACKFILL_ROWS, stats, keep_all=True)
    bf["rows"] = bf.get("rows", 0) + rows
    if exhausted or not max_fs or max_fs == bf["since"]:
        bf["done"] = rows < PAGE or not max_fs or max_fs == bf["since"]
    if max_fs:
        bf["since"] = max_fs
    log(f"backfill: read {rows} rows, cursor {bf['since']}, done={bf['done']}")
    return rows


# --------------------------------------------------------------------------- media
def fetch_media(ads, stats):
    OUT_MEDIA.mkdir(parents=True, exist_ok=True)
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
        url = a["media_src"]
        if re.search(r"\.(mp4|mov|webm)(\?|$)", url, re.I):
            a["media_status"] = "video"       # keep the link; videos are not stored
            continue
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            if "video" in r.headers.get("Content-Type", ""):
                a["media_status"] = "video"
                continue
            im = Image.open(io.BytesIO(r.content))
            im = im.convert("RGB")
            if im.width > MEDIA_MAX_W:
                im = im.resize((MEDIA_MAX_W, round(im.height * MEDIA_MAX_W / im.width)), Image.LANCZOS)
            im.save(dest, "WEBP", quality=80, method=6)
            a["media_status"] = "queued"
            a["media_w"], a["media_h"] = im.width, im.height
            done += 1
        except Exception as e:
            a["media_tries"] = a.get("media_tries", 0) + 1
            if a["media_tries"] >= 3:
                a["media_status"] = "failed"
            log("media failed", a["id"], str(e)[:120])
    stats["media"] = done
    log(f"media: {done} images prepared")


# --------------------------------------------------------------------------- upload
class Uploader:
    def __init__(self):
        self.ftp = None
        self.dirs = set()

    def __enter__(self):
        if LOCAL_UPLOAD_DIR:
            return self
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
            "new_24h": sum(1 for a in ads.values() if (a.get("found_at") or "") >= today),
            "media_waiting": sum(1 for a in ads.values() if a["media_status"] in ("pending", "queued")),
            "not_sent": sum(1 for a in ads.values() if not a.get("sent_at")),
        },
        "backfill": state["backfill"],
        "runs": list(reversed(state["runs"][-30:])),
    }
    manifest = {
        "generated_at": now_iso(),
        "months": [{"m": m, "n": sum(1 for a in ads.values() if month_of(a) == m), "h": sha(p)}
                   for m, p in sorted(shards.items(), reverse=True)],
    }
    return status, manifest


def upload(ads, state, run):
    """Push everything pending. Raises on connection failure; partial progress is kept."""
    tmp = OUTBOX / "_gen"
    tmp.mkdir(parents=True, exist_ok=True)
    sent_media = sent_files = 0
    with Uploader() as up:
        # 1) images
        for f in sorted(OUT_MEDIA.glob("*.webp")):
            if time_left() < 60:
                break
            up.put(f, f"{REMOTE_ROOT}/media/{f.name}")
            a = ads.get(f.stem)
            if a:
                a["media_file"] = f"media/{f.name}"
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
        # 3) panel (only changed files)
        for f in sorted(PANEL.rglob("*")):
            if f.is_file():
                remote = f"{REMOTE_ROOT}/{f.relative_to(PANEL).as_posix()}"
                h = sha(f)
                if state["uploaded"].get(remote) != h:
                    up.put(f, remote)
                    state["uploaded"][remote] = h
                    sent_files += 1
        # 4) manifest + status last, so the panel never points at missing files
        state["last_upload_ok"] = now_iso()
        run.update({"sent_media": sent_media, "sent_files": sent_files, "ok": True,
                    "secs": round(time.time() - STARTED)})
        state["runs"].append(run)
        status, manifest = build_status(ads, state, shards)
        state["runs"].pop()
        for name, obj in (("manifest.json", manifest), ("status.json", status)):
            p = tmp / name
            p.write_text(json.dumps(obj, ensure_ascii=False), "utf-8")
            up.put(p, f"{REMOTE_ROOT}/{name}")
    log(f"upload: {sent_media} images, {sent_files} files")


# --------------------------------------------------------------------------- main
def main():
    DATA.mkdir(exist_ok=True)
    OUT_MEDIA.mkdir(parents=True, exist_ok=True)
    state = load_state()
    ads = load_ads()
    stats = {"new": 0, "upd": 0, "media": 0}
    run = {"at": now_iso(), "ok": False, "error": None}
    errors = []

    for step in (run_incremental, run_backfill):
        try:
            step(ads, state, stats)
        except Exception as e:
            errors.append(f"{step.__name__}: {e}")
            traceback.print_exc()
    try:
        fetch_media(ads, stats)
    except Exception as e:
        errors.append(f"media: {e}")
        traceback.print_exc()

    write_shards(ads)
    run.update({"new": stats["new"], "updated": stats["upd"], "media": stats["media"]})
    try:
        upload(ads, state, run)
    except Exception as e:
        errors.append(f"upload: {e}")
        traceback.print_exc()
        log("upload failed — everything stays queued for the next run")
    write_shards(ads)

    run["error"] = " | ".join(errors)[:500] or None
    run["ok"] = run.get("ok", False) and not errors
    run["secs"] = round(time.time() - STARTED)
    state["runs"].append(run)
    save_state(state)
    log("done", json.dumps(run, ensure_ascii=False))
    # a failed upload is not a failed run: data is safe in git and will be retried
    if errors and not any(e.startswith("upload") for e in errors) and stats["new"] == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
