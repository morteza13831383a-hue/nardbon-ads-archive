"""Append new published channel discoveries to the panel without contacting any crawl source."""
import argparse
import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time

from archive_collect import gh
from channel_import import import_tgstat

SOURCES = (("telegramsearchengine", "tse"), ("tgchannelsme", "tc"), ("lyzem", "lz"))


def raw(repo, path, ref, sha):
    if path.endswith(".gz"):
        obj = gh(repo, "git/blobs/" + sha)
        if obj.get("encoding") != "base64" or obj.get("sha") != sha:
            raise ValueError("Invalid source blob")
        body = base64.b64decode(obj["content"])
    else:
        body = subprocess.check_output(["gh","api",f"repos/{repo}/contents/{path}?ref={ref}",
                                       "-H","Accept: application/vnd.github.raw+json"], timeout=120)
    actual = hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest()
    if actual != sha:
        raise ValueError("Remote object digest mismatch")
    return body


def append_new(panel, new):
    fields = panel["fields"]
    idx = fields.index("u")
    known = {r[idx].lower() for r in panel["rows"]}
    additions = [r for u, r in new.items() if u.lower() not in known and r.get("kd") == "channel"]
    if not additions:
        return 0
    if any(r.get("tse") for r in additions) and "tse" not in fields:
        fields.append("tse")
        for row in panel["rows"]:
            row.extend([None] * (len(fields) - len(row)))
    panel["rows"].extend([[r.get(f) for f in fields] for r in additions])
    panel["at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return len(additions)


def update(repo, db):
    head = gh(repo,"commits/main")
    ref = head["sha"]
    data = {f["name"]:f for f in gh(repo,f"contents/data?ref={ref}")}
    pending = []
    new = {}
    for src, stamp in SOURCES:
        directory = src + "-import"
        if directory not in data:
            continue
        items = gh(repo,"git/trees/" + data[directory]["sha"])
        if items.get("truncated"):
            raise ValueError("Incomplete source directory")
        with tempfile.TemporaryDirectory() as tmp:
            for f in items["tree"]:
                name = f["path"]
                if f["type"] != "blob" or not name.endswith(".jsonl.gz") or Path(name).name != name:
                    continue
                key = directory + "/" + name
                if db.execute("SELECT 1 FROM parts WHERE name=? AND sha=?", (key,f["sha"])).fetchone():
                    continue
                (Path(tmp)/name).write_bytes(raw(repo,"data/"+key,ref,f["sha"]))
                pending.append((key,f["sha"]))
            found = {}
            import_tgstat(found, {}, lambda msg: None, lambda: datetime.now(timezone.utc).isoformat(),
                          Path(tmp), src, src, stamp)
            for u,r in found.items():
                if r.get("kd") == "channel":
                    new.setdefault(u.lower(),r)
    if not pending:
        return
    count = 0
    if new:
        panel = json.loads(raw(repo,"data/channels.json",ref,data["channels.json"]["sha"]))
        count = append_new(panel,new)
        if count:
            body = json.dumps(panel,ensure_ascii=False,separators=(",",":")).encode()
            blob = gh(repo,"git/blobs",{"content":base64.b64encode(body).decode(),"encoding":"base64"})
            entry = {"path":"data/channels.json","mode":"100644","type":"blob","sha":blob["sha"]}
            # A concurrent panel update must be re-read and merged before acknowledging any inputs.
            for attempt in range(4):
                current = gh(repo,"commits/main")
                if current["sha"] != ref:
                    current_data = gh(repo,"contents/data?ref="+current["sha"])
                    if next(f["sha"] for f in current_data if f["name"]=="channels.json") != data["channels.json"]["sha"]:
                        raise RuntimeError("Panel changed concurrently")
                tree = gh(repo,"git/trees",{"base_tree":current["commit"]["tree"]["sha"],"tree":[entry]})
                commit = gh(repo,"git/commits",{"message":f"Add {count} newly discovered channels to the panel catalog",
                                                "tree":tree["sha"],"parents":[current["sha"]]})
                try:
                    gh(repo,"git/refs/heads/main",{"sha":commit["sha"],"force":False})
                    print("Catalog commit",commit["sha"],flush=True)
                    break
                except subprocess.CalledProcessError:
                    if attempt == 3:
                        raise
                    time.sleep(2)
    with db:
        db.executemany("INSERT OR REPLACE INTO parts VALUES(?,?)",pending)
    print("Processed source parts",len(pending),"new panel channels",count,flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir",type=Path,required=True)
    parser.add_argument("--repo",required=True)
    parser.add_argument("--watch",action="store_true")
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    with (args.work_dir/"panel.lock").open("a") as lock, sqlite3.connect(args.work_dir/"panel-parts.sqlite3") as db:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        db.execute("CREATE TABLE IF NOT EXISTS parts(name TEXT PRIMARY KEY,sha TEXT NOT NULL)")
        while True:
            try:
                update(args.repo,db)
            except (subprocess.SubprocessError, OSError, RuntimeError) as err:
                if not args.watch:
                    raise
                print("Publication postponed",type(err).__name__,flush=True)
                time.sleep(60)
                continue
            if not args.watch:
                break
            time.sleep(1800)
