"""Manual diagnostic: how deep can this API key page through Iran ads?"""
import json, os, requests
API = "https://tgadsspy.com/api/v1"
S = requests.Session(); S.headers["X-Api-Key"] = os.environ["TGADS_API_KEY"]
for off in (0, 100, 200, 1000, 4900, 5000):
    r = S.get(f"{API}/ads", params={"geo": "IR", "sort": "new", "limit": 100, "offset": off}, timeout=40)
    try: j = r.json()
    except Exception: j = {}
    d = j.get("data") or []
    print(f"offset={off:5} http={r.status_code} rows={len(d)} first_id={d[0].get('id') if d else None} "
          f"meta={json.dumps(j.get('meta'))} plan={r.headers.get('X-Plan')} err={str(j.get('error') or '')[:150]}")
