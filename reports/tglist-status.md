# TGList collection status

2026-11-16 UTC

- Permission deadline: **2026-12-16 23:59 UTC**. The collector refuses all TGList requests after this time, including the robots request.
- Source: the site's public search form navigates to `/search?q=<term>`. The first live search request returned **HTTP 403**. The collector checkpoint retains this source hold and makes no further TGList requests.
- First 10 keyword terms: **not tested**; no TGList channels or groups were saved. Per-term yields are unavailable. The result-page HTML and its pagination controls cannot be validated under the current 403 response.
- Intended order: `data/channel_keywords.txt`, then `data/channel_keywords_auto.txt`. Rows are saved from listings only, excluding usernames already known to the local collectors. A term ends when a page adds fewer than two new usernames.
- Pacing when access works: one TGList host budget, at least 10 seconds between requests, separate from TGStat and tgadsspy. HTTP 403, 429, or captcha retains a stop reason in the SQLite checkpoint.

TGList support needs to resolve the 403 for the approved search path before the first 10-term live test can proceed. A browser session working for the owner does not prove that the collector's request is permitted.
