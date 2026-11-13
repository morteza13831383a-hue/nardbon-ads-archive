#!/usr/bin/env python3
"""
Commit data/ and push, merging instead of failing when another job pushed first.

Two workflows write data/ (sync.yml every 30 min and archive-html.yml for the
one-off Iran import). If both changed the same shard, a plain `git pull --rebase`
stops on a conflict and the run turns red. Here every conflict is resolved by
content:
  data/ads/*.json   union of both sides by ad id; for an ad on both sides the more
                    complete record wins (image on host, sent, later last-seen)
  data/state.json   this job's state, plus the other side's upload hashes and
                    timestamps it does not have
  data/channels.json union by username, fresher description wins
  anything else     the other side's version (this job re-creates it next run)
Usage: python scripts/commit_data.py "commit message"
"""
import json
import subprocess
import sys
import time

MSG = sys.argv[1] if len(sys.argv) > 1 else "sync data"


def git(*args, check=True, capture=False):
    r = subprocess.run(["git", *args], text=True, capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{r.stdout}\n{r.stderr}")
    return r.stdout if capture else r


def show(stage, path):
    r = subprocess.run(["git", "show", f":{stage}:{path}"], text=True, capture_output=True)
    return r.stdout if r.returncode == 0 else None


def score(a):
    return (bool(a.get("media_file")), bool(a.get("sent_at")), a.get("last_seen") or "",
            len(a.get("labels") or {}), a.get("found_at") or "")


def merge_ads(upstream, mine):
    ads = {}
    for text in (upstream, mine):
        for a in json.loads(text or "[]"):
            cur = ads.get(a["id"])
            if cur is None:
                ads[a["id"]] = a
                continue
            best, other = (a, cur) if score(a) >= score(cur) else (cur, a)
            m = dict(best)
            # dates and counters: take the widest view from both sides
            m["first_seen"] = min(filter(None, [a.get("first_seen"), cur.get("first_seen")]), default=None)
            m["last_seen"] = max(filter(None, [a.get("last_seen"), cur.get("last_seen")]), default=None)
            m["found_at"] = min(filter(None, [a.get("found_at"), cur.get("found_at")]), default=None)
            if (other.get("last_seen") or "") > (best.get("last_seen") or "") and other.get("placements") is not None:
                m["placements"] = other["placements"]
            m["labels"] = {**(other.get("labels") or {}), **(best.get("labels") or {})}
            ads[a["id"]] = m
    items = sorted(ads.values(), key=lambda a: (a.get("first_seen") or "", a["id"]), reverse=True)
    return "[\n" + ",\n".join(json.dumps(a, ensure_ascii=False, sort_keys=True) for a in items) + "\n]\n"


def merge_state(upstream, mine):
    up, me = json.loads(upstream or "{}"), json.loads(mine or "{}")
    out = dict(me)
    out["uploaded"] = {**(up.get("uploaded") or {}), **(me.get("uploaded") or {})}
    for k in ("last_upload_ok", "html_archive_exported_at", "api_blocked_until", "channels_blocked_until"):
        vals = [v for v in (up.get(k), me.get(k)) if v]
        if vals:
            out[k] = max(vals)
    for k, v in up.items():
        out.setdefault(k, v)
    return json.dumps(out, ensure_ascii=False, indent=1)


def merge_channels(upstream, mine):
    up, me = json.loads(upstream or "{}"), json.loads(mine or "{}")
    fields = me.get("fields") or up.get("fields") or []
    rows = {}
    for body in (up, me):
        f = body.get("fields") or fields
        for r in body.get("rows", []):
            c = dict(zip(f, r))
            cur = rows.get(c.get("u"))
            if cur is None or (c.get("dd") or "") >= (cur.get("dd") or ""):
                if cur:
                    c["k"] = sorted(set((cur.get("k") or []) + (c.get("k") or [])))
                    if not c.get("d"):
                        c["d"], c["dd"] = cur.get("d"), cur.get("dd")
                rows[c["u"]] = c
    ordered = sorted(rows.values(), key=lambda c: -(c.get("v") or 0))
    return json.dumps({"at": max(filter(None, [up.get("at"), me.get("at")]), default=None), "fields": fields,
                       "rows": [[c.get(k) for k in fields] for c in ordered]}, ensure_ascii=False, separators=(",", ":"))


def resolve():
    files = git("diff", "--name-only", "--diff-filter=U", capture=True).split()
    for path in files:
        upstream, mine = show(2, path), show(3, path)    # during a rebase: 2 = remote, 3 = this job's commit
        if path.startswith("data/ads/") and path.endswith(".json"):
            text = merge_ads(upstream, mine)
        elif path == "data/state.json":
            text = merge_state(upstream, mine)
        elif path == "data/channels.json":
            text = merge_channels(upstream, mine)
        else:
            text = upstream if upstream is not None else (mine or "")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        git("add", path)
        print(f"merged {path}")
    return files


def main():
    git("add", "data")
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        print("no data changes")
        return
    git("commit", "-q", "-m", MSG)
    for attempt in range(5):
        git("fetch", "-q", "origin", "main")
        r = git("rebase", "origin/main", check=False)
        rounds = 0
        while r.returncode != 0 and rounds < 20:
            if not resolve():
                git("rebase", "--abort", check=False)
                raise RuntimeError(f"rebase failed without conflicts:\n{r.stdout}\n{r.stderr}")
            r = subprocess.run(["git", "-c", "core.editor=true", "rebase", "--continue"], text=True, capture_output=True)
            rounds += 1
        if git("push", "-q", "origin", "HEAD:main", check=False).returncode == 0:
            print("pushed")
            return
        time.sleep(5 * (attempt + 1))                 # someone pushed in between: fetch and merge again
    raise RuntimeError("could not push after 5 attempts")


if __name__ == "__main__":
    main()
