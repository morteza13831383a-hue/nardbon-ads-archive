"""Manual diagnostic: per-advertiser Iran totals via the documented `advertiser` filter."""
import json, os, time, requests
API = "https://tgadsspy.com/api/v1"
S = requests.Session(); S.headers["X-Api-Key"] = os.environ["TGADS_API_KEY"]
for slug in ("tg-homapakhshbazar", "tg-tygo_vpn_bot", "tg-sahbazy97"):
    r = S.get(f"{API}/ads", params={"advertiser": slug, "geo": "IR", "limit": 100}, timeout=40)
    j = r.json() if r.ok else {"raw": r.text[:200]}
    m = j.get("meta") or {}
    fs = sorted(x.get("firstSeenAt", "") for x in j.get("data") or [])
    print(slug, r.status_code, "total", m.get("total"), "reachable", m.get("totalReachable"), "rows", len(fs),
          "first", fs[:1], "last", fs[-1:], "trust", m.get("trustTier"), "maxOffset", m.get("maxOffset"))
    time.sleep(10)
