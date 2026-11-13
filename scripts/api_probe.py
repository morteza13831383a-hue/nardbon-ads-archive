"""Manual diagnostic: ordering and paging of sort=proven for Iran ads."""
import json, os, time, requests
API = "https://tgadsspy.com/api/v1"
S = requests.Session(); S.headers["X-Api-Key"] = os.environ["TGADS_API_KEY"]
for q in ({"geo": "IR", "sort": "proven", "limit": 100, "offset": 0},
          {"geo": "IR", "sort": "proven", "limit": 100, "offset": 100},
          {"geo": "IR", "sort": "proven", "limit": 100, "since": "2026-01-01T00:00:00Z"}):
    r = S.get(f"{API}/ads", params=q, timeout=40)
    j = r.json() if r.ok else {}
    d = j.get("data") or []
    fs = [x.get("firstSeenAt") for x in d]
    dur = [round((time.mktime(time.strptime(x["lastSeenAt"][:19], "%Y-%m-%dT%H:%M:%S")) - time.mktime(time.strptime(x["firstSeenAt"][:19], "%Y-%m-%dT%H:%M:%S"))) / 86400, 1)
           for x in d if x.get("firstSeenAt") and x.get("lastSeenAt")]
    print(json.dumps(q), r.status_code, "meta=", json.dumps(j.get("meta")))
    print("   firstSeen first/last:", fs[:3], "...", fs[-3:])
    print("   ascending firstSeen:", fs == sorted(fs), "| descending:", fs == sorted(fs, reverse=True))
    print("   run-days first 8:", dur[:8], "| last 4:", dur[-4:])
    time.sleep(5)
