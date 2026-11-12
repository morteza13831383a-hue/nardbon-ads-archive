"""Manual diagnostic: what does the channel catalog offer for Persian channels?"""
import json, os, requests
API = "https://tgadsspy.com/api/v1"
S = requests.Session(); S.headers["X-Api-Key"] = os.environ["TGADS_API_KEY"]
def get(path, **q):
    r = S.get(f"{API}{path}", params=q, timeout=40)
    try: return r.status_code, r.json()
    except Exception: return r.status_code, {"raw": r.text[:200]}
for q in ({"lang": "fa", "limit": 5}, {"lang": "fa", "category": "commerce", "limit": 5}, {"q": "فروشگاه", "limit": 5}):
    code, j = get("/channels", **q)
    print("\n/channels", q, code, "meta=", json.dumps(j.get("meta")))
    for c in (j.get("data") or [])[:5]:
        print("  ", {k: c.get(k) for k in ("username", "title", "lang", "category", "members", "avgViews", "estimatedCpm", "isSponsoredEligible", "lastSnapshotAt")})
code, j = get("/niches"); print("\n/niches", code, json.dumps(j, ensure_ascii=False)[:700])
code, j = get("/analytics/niche-pricing", geo="IR"); print("\n/niche-pricing IR", code, json.dumps(j, ensure_ascii=False)[:700])
code, j = get("/search", q="آموزش زبان", region="IR", limit=5); print("\n/search IR", code, json.dumps(j, ensure_ascii=False)[:900])
