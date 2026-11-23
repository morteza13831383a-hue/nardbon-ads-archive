"""Refresh the archive worker's monthly known-ID inputs from a pinned GitHub revision."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time


def git_hash(raw):
    return hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()


def refresh(repo, dest):
    def get(route, raw=False):
        cmd = ["gh", "api", f"repos/{repo}/{route}"]
        if raw:
            cmd += ["-H", "Accept: application/vnd.github.raw+json"]
        return subprocess.check_output(cmd, timeout=90)
    ref = json.loads(get("commits/main"))["sha"]
    files = json.loads(get(f"contents/data/ads?ref={ref}"))
    count = 0
    for f in files:
        if not re.fullmatch(r"\d{4}-\d{2}\.json", f["name"]):
            continue
        path = dest / f["name"]
        if path.exists() and git_hash(path.read_bytes()) == f["sha"]:
            continue
        raw = get(f"contents/data/ads/{path.name}?ref={ref}", True)
        if git_hash(raw) != f["sha"] or not isinstance(json.loads(raw), list):
            raise ValueError("Invalid monthly shard response")
        tmp = path.with_suffix(".download")
        tmp.write_bytes(raw)
        tmp.replace(path)
        count += 1
    return ref, count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--ads-dir", type=Path, required=True)
    parser.add_argument("--hourly", action="store_true")
    args = parser.parse_args()
    args.ads_dir.mkdir(parents=True, exist_ok=True)
    with (args.ads_dir / ".refresh.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                ref, count = refresh(args.repo, args.ads_dir)
                print("Updated monthly shards", count, "at", ref, flush=True)
            except (subprocess.SubprocessError, OSError, ValueError):
                if not args.hourly:
                    raise
                print("Refresh failed; retaining verified shards", flush=True)
            if not args.hourly:
                break
            time.sleep(3600)
