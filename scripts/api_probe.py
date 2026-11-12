"""Manual diagnostic: how many Iran ads does the tgadsspy API report for different queries?"""
import json, os, requests
API = "https://tgadsspy.com/api/v1"
S = requests.Session(); S.headers["X-Api-Key"] = os.environ["TGADS_API_KEY"]
QUERIES = {
    "IR all (default sort)": {"geo": "IR"},
    "IR new": {"geo": "IR", "sort": "new"},
    "IR proven": {"geo": "IR", "sort": "proven"},
    "IR proven days=365": {"geo": "IR", "sort": "proven", "days": 365},
    "IR days=30": {"geo": "IR", "days": 30},
    "IR days=365": {"geo": "IR", "days": 365},
    "IR active": {"geo": "IR", "status": "active"},
    "lang fa": {"lang": "fa"},
}
for name, q in QUERIES.items():
    r = S.get(f"{API}/ads", params={**q, "limit": 3}, timeout=40)
    try: j = r.json()
    except Exception: j = {}
    d = j.get("data") or []
    print(f"{name:28} http={r.status_code} meta={json.dumps(j.get('meta'), ensure_ascii=False)} rows={len(d)} "
          f"firstSeen={[x.get('firstSeenAt') for x in d]} err={str(j.get('error') or '')[:150]}")
