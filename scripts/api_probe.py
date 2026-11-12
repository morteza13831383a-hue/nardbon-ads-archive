"""Manual diagnostic: paging depth for this API key, with and without filters."""
import json, os, requests
API = "https://tgadsspy.com/api/v1"
S = requests.Session(); S.headers["X-Api-Key"] = os.environ["TGADS_API_KEY"]
for name, q in {"no filter": {}, "geo=IR": {"geo": "IR"}, "niche=crypto": {"niche": "crypto"}}.items():
    for off in (100, 1000):
        r = S.get(f"{API}/ads", params={**q, "limit": 100, "offset": off}, timeout=40)
        m = (r.json() if r.ok else {}).get("meta") or {}
        print(f"{name:13} offset={off:5} http={r.status_code} plan={m.get('plan')} maxOffset={m.get('maxOffset')} "
              f"returnedOffset={m.get('offset')} clamped={m.get('offsetClamped')} reachable={m.get('totalReachable')}")
