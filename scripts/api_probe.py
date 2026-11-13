"""Manual diagnostic: does sort=proven honour `since`?"""
import json, os, requests
API = "https://tgadsspy.com/api/v1"
S = requests.Session(); S.headers["X-Api-Key"] = os.environ["TGADS_API_KEY"]
q = {"geo": "IR", "sort": "proven", "limit": 100, "since": "2026-06-07T10:59:55Z"}
r = S.get(f"{API}/ads", params=q, timeout=40)
j = r.json() if r.ok else {"raw": r.text[:300]}
fs = [x.get("firstSeenAt") for x in j.get("data") or []]
print(json.dumps(q), r.status_code, "meta=", json.dumps(j.get("meta")), "err=", j.get("error"))
print("   firstSeen min/max:", min(fs) if fs else None, max(fs) if fs else None, "count", len(fs))
