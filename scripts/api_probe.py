"""Manual diagnostic: one request each to /ads and /channels, full rate-limit details."""
import os, requests
API = "https://tgadsspy.com/api/v1"
S = requests.Session(); S.headers["X-Api-Key"] = os.environ["TGADS_API_KEY"]
for path, q in (("/ads", {"geo": "IR", "limit": 1}), ("/channels", {"lang": "fa", "limit": 1})):
    r = S.get(f"{API}{path}", params=q, timeout=40)
    hdr = {k: v for k, v in r.headers.items() if "rate" in k.lower() or "retry" in k.lower() or k.lower().startswith("x-")}
    print(path, r.status_code, hdr, r.text[:300])
