# TGStat keyword search status

The keyword worker is stopped at the site's explicit `status=restricted` response on the pending manual term پوشاک, page 4. This is an account restriction. The checkpoint and all collected records are retained; no search request should resume until the owner resolves the restriction and the session is validated. Shared pacing, captcha/logout holds and the daily cap remain in force.

The first three manual channel terms completed:

| Term | Pages | New channels |
|---|---:|---:|
| فروشگاه | 7 | 80 |
| فروش | 15 | 75 |
| تخفیف | 1 | 7 |

پوشاک is pending at page 4, with 57 new channels from its first four completed pages. The 1,500 auto terms from `data/channel_keywords_auto.txt` are queued after the 131 manual terms; none has been searched yet. Current database totals are 22,920 channels and 1,425 groups. No group keyword search route has been confirmed.

The updated worker refreshes auto terms from main once per UTC day, appends only new terms, preserves finished terms, and ends a term when a page adds fewer than two new usernames. It makes at most 300 successful search requests per UTC day and uses at least 15 seconds between requests on the shared TGStat host budget. The requested per-term report for the first 20 auto terms is unavailable until these terms are reached.
