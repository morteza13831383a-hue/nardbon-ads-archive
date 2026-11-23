"""Discover Persian channels in Telegram Search Engine's public HTML catalog and sitemap."""
import argparse
import base64
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from archive_collect import gh
from html_core import Client, Document
from lyzem_collect import known_channels
from source_budget import Challenge, status

BASE = "https://telegramsearchengine.com"
NAME = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{3,31}")
SLUG = re.compile(r"[a-z][a-z0-9-]{0,48}")
FA_WORD = re.compile(r"(?<!\w)(?:از|برای|های|هایشان|اخبار|فوری|ایران|ایرانی|فارسی|کانال|آموزش|موسیقی|دانشگاه|پزشکی|ورزش|جدیدترین|خبرهای|رسمی|تخفیف|فروش|قیمت|خرید|عضویت|است|را|ها)(?!\w)")
FA_LETTER = re.compile(r"[پچژگکی]")
OTHER_LANGUAGE = re.compile(r"[ٹڈڑںھہۂے]")

PREFIX = {"directory": ("__SSR_DIRECTORY_DATA__", "__INITIAL_DIRECTORY_DATA__"),
          "category": ("__SSR_CATEGORY_ITEMS__", "__INITIAL_CATEGORY_ITEMS__"),
          "profile": ("__SSR_CHANNEL_PROFILE__", "__INITIAL_CHANNEL_PROFILE__")}


def page_data(body, kind):
    tag_id, var = PREFIX[kind]
    doc = Document(body)
    node = next((n for n in doc.root.nodes() if n.tag == "script" and n.attrs.get("id") == tag_id), None)
    if node is None:
        raise ValueError("Missing public catalog data")
    left, sep, right = node.text().partition("=")
    if not sep or left.strip() != "window." + var:
        raise ValueError("Unexpected catalog assignment")
    return json.loads(right.strip().removesuffix(";"))


def channel(item, at):
    u = item.get("username")
    if item.get("type") != "channel" or not isinstance(u, str) or not NAME.fullmatch(u) or u.lower().endswith("bot"):
        return None
    title, desc = item.get("name") or "", item.get("description") or ""
    if not title or not isinstance(title, str) or not isinstance(desc, str):
        return None
    # Public proxy listings sometimes include connection credentials in their snippets.
    if re.search(r"\b(?:secret|token|password)\s*[:=]|tg://proxy", desc, re.I):
        desc = ""
    text = title + " " + desc
    if OTHER_LANGUAGE.search(text) or not (FA_WORD.search(text) and FA_LETTER.search(text)):
        return None
    members = item.get("subscriberCount", item.get("subscriber_count"))
    members = members if type(members) is int and members >= 0 else item.get("subscribers")
    return {"type": "channel", "username": u.lower(), "title": title[:512],
            "description": desc[:1000], "members": members, "category": item.get("category"),
            "language": "fa", "url": "https://t.me/" + u.lower(), "collected_at": at}


def connect(work):
    db = sqlite3.connect(work / "catalog.sqlite3", timeout=30)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS pages(path TEXT PRIMARY KEY, kind TEXT, priority INTEGER,
          state TEXT DEFAULT 'pending', observed INTEGER DEFAULT 0, new INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS records(username TEXT PRIMARY KEY, payload TEXT NOT NULL, part TEXT);
        CREATE TABLE IF NOT EXISTS observed(username TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
    """)
    db.executemany("INSERT OR IGNORE INTO pages(path,kind,priority) VALUES(?,?,?)",
                   [("/directory","directory",0), ("/sitemap.xml","sitemap",1)])
    db.commit()
    return db


def ingest(db, path, kind, body, known):
    items = []
    jobs = []
    at = datetime.now(timezone.utc).isoformat()
    if kind == "sitemap":
        root = ET.fromstring(body)
        ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
        if root.tag != ns + "urlset":
            raise ValueError("Unexpected sitemap structure")
        for tag in root.findall(ns + "url/" + ns + "loc"):
            url = urlsplit(tag.text or "")
            if url.scheme == "https" and url.netloc == "telegramsearchengine.com" and not url.query and not url.fragment:
                match = re.fullmatch(r"/channel/([A-Za-z0-9_]+)", url.path)
                if match and NAME.fullmatch(match[1]) and match[1].lower() not in known:
                    jobs.append((url.path, "profile", 10))
    else:
        data = page_data(body, kind)
        if kind == "profile":
            if data.get("username", "").lower() != path.split("/")[-1].lower():
                raise ValueError("Profile identity mismatch")
            items = [data]
        else:
            if kind == "category" and data.get("category") != path[1:]:
                raise ValueError("Category mismatch")
            items = data["items"]
            if not isinstance(items, list):
                raise ValueError("Invalid catalog rows")
            if kind == "directory":
                for cat in data["categories"]:
                    slug = cat["id"]
                    if SLUG.fullmatch(slug) and slug not in ("proxy", "bots"):
                        priority = 2 if slug in ("news","jobs","poetry","education","music","business","trading") else 3
                        jobs.append(("/" + slug, "category", priority))
    new = 0
    with db:
        db.executemany("INSERT OR IGNORE INTO pages(path,kind,priority) VALUES(?,?,?)", jobs)
        for item in items:
            row = channel(item, at)
            u = item.get("username", "")
            if NAME.fullmatch(u):
                db.execute("INSERT OR IGNORE INTO observed VALUES(?)", (u.lower(),))
            if row and row["username"] not in known:
                new += db.execute("INSERT OR IGNORE INTO records(username,payload) VALUES(?,?)",
                                  (row["username"], json.dumps(row, ensure_ascii=False))).rowcount
        db.execute("UPDATE pages SET state='done',observed=?,new=? WHERE path=?", (len(items),new,path))
    return len(items), new


def publish(db, repo):
    if not repo:
        return
    while True:
        batch = db.execute("SELECT username,payload FROM records WHERE part IS NULL ORDER BY rowid LIMIT 500").fetchall()
        if not batch:
            return
        raw = ("\n".join(payload for _, payload in batch) + "\n").encode()
        blob = gzip.compress(raw, mtime=0)
        part = "data/telegramsearchengine-import/tse-" + hashlib.sha256(raw).hexdigest() + ".jsonl.gz"
        entry = {"path": part, "type":"blob", "mode":"100644",
                 "sha":gh(repo, "git/blobs", {"content":base64.b64encode(blob).decode(),"encoding":"base64"})["sha"]}
        for attempt in range(4):
            head = gh(repo, "commits/main")
            tree = gh(repo,"git/trees",{"base_tree":head["commit"]["tree"]["sha"],"tree":[entry]})
            if tree["sha"] == head["commit"]["tree"]["sha"]:
                break
            commit = gh(repo,"git/commits",{"message":"Import Persian channels from Telegram Search Engine",
                        "tree":tree["sha"],"parents":[head["sha"]]})
            try:
                gh(repo,"git/refs/heads/main",{"sha":commit["sha"],"force":False})
                break
            except subprocess.CalledProcessError:
                if attempt == 3:
                    raise
                time.sleep(2)
        with db:
            db.executemany("UPDATE records SET part=? WHERE username=?", [(part,u) for u,_ in batch])
        print("Published",len(batch),part,flush=True)


def run(args):
    db = connect(args.work_dir)
    if db.execute("SELECT 1 FROM meta WHERE key='hold'").fetchone():
        return
    if not (args.repo_root / "data/channels.json").is_file():
        raise ValueError("Missing panel catalog")
    known = known_channels(args.repo_root)
    for path in args.known_db:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as other:
            known.update(u.lower() for u, in other.execute("SELECT username FROM records WHERE username IS NOT NULL"))
    client = Client(BASE, 5, resilient=True)
    count = 0
    try:
        publish(db, args.repo)
        budget = status(BASE)
        if budget["halted"] or budget["pause_left_s"]:
            return
        client.initialize()
        while not args.pages or count < args.pages:
            job = db.execute("SELECT path,kind FROM pages WHERE state='pending' ORDER BY priority,rowid LIMIT 1").fetchone()
            if not job:
                break
            path,kind = job
            slug = path.split("/")[-1].lower()
            if kind == "profile" and (slug in known or db.execute("SELECT 1 FROM observed WHERE username=?", (slug,)).fetchone()):
                with db:
                    db.execute("UPDATE pages SET state='known' WHERE path=?", (path,))
                continue
            if not client.robots.allowed(BASE+path):
                with db:
                    db.execute("UPDATE pages SET state='robots' WHERE path=?", (path,))
                continue
            try:
                html = client.get(BASE+path)
            except HTTPError as err:
                if err.code not in (404,410):
                    raise
                with db:
                    db.execute("UPDATE pages SET state='gone' WHERE path=?", (path,))
            else:
                observed,new = ingest(db,path,kind,html,known)
                print(path,observed,new,flush=True)
            count += 1
            if count % 10 == 0:
                publish(db, args.repo)
    except (HTTPError, Challenge) as err:
        if isinstance(err, Challenge) or err.code in (401,403,429):
            with db:
                db.execute("INSERT OR REPLACE INTO meta VALUES('hold',?)", (type(err).__name__ + " " + str(getattr(err,"code","challenge")),))
        raise
    finally:
        publish(db,args.repo)
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--known-db", type=Path, action="append", default=[])
    parser.add_argument("--pages", type=int, default=0)
    parser.add_argument("--repo")
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    with (args.work_dir / "worker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(args)
