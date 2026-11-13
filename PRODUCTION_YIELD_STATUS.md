# Production yield after fbb37a5

Measured three consecutive 100-page production slices on the same checkpoint, after a safe slice-boundary restart. Code: `ed0925aebd8ea403d40da0b77ceff7ad736beae5` (includes fbb37a5). All 17 tests passed. Cutoff remains `2026-11-13T08:53:18.687804+00:00`.

## Measured transport and yield

- New unique Iranian HTML ads: **282** (90, 100, 92 by slice).
- Elapsed controller time: **26.67 minutes**, including publication; **634.34 ads/hour**.
- Actual source HTTP requests: **303** (301 HTTP 200; 2 HTTP 404); no 401/403/429. Delay remains 4–6 seconds, serial requests.
- Actual scheduling: 190 priority-1 Iran ad pages yielded 190; 100 priority-2 advertiser-related ad pages yielded 92; 10 listing/metadata requests and 3 robots requests yielded zero.
- This sample did not reach global sitemap fallback. Historical via labels are not a reliable measure of the current scheduling tier: 45 pages labelled sitemap were actually priority-2 rediscoveries.

## coverage.by_month (native HTML records)

| Month | Site Iran total | Extracted | Missing | Coverage % |
|---|---:|---:|---:|---:|
| 2026-04 | 1147 | 256 | 891 | 22.3 |
| 2026-05 | 2315 | 326 | 1989 | 14.1 |
| 2026-06 | 7107 | 667 | 6440 | 9.4 |
| 2026-07 | 8355 | 615 | 7740 | 7.4 |
| 2026-08 | 10472 | 883 | 9589 | 8.4 |
| 2026-09 | 16687 | 1646 | 15041 | 9.9 |
| 2026-10 | 7203 | 1039 | 6164 | 14.4 |
| 2026-11 | 3310 | 642 | 2668 | 19.4 |

HTML total: **6,074**. Current monthly site totals sum to **56,596**, 113 above the original 56,483 target. These live totals can include records after the fixed cutoff.

## coverage.by_strategy (native cumulative counters)

| via | requests | Iran | Iran / 100 |
|---|---:|---:|---:|
| iran_listing | 5038 | 5035 | 99.94 |
| same_advertiser | 1012 | 749 | 74.01 |
| listing>ad | 224 | 192 | 85.71 |
| original | 55 | 55 | 100.0 |
| sitemap | 5305 | 43 | 0.81 |
| snapshot | 3 | 0 | 0.0 |
| listing | 24 | 0 | 0.0 |
| archive | 152 | 0 | 0.0 |
| advertiser | 318 | 0 | 0.0 |

These native request counters include processed queue rows marked known without HTTP, and mutable via labels can reclassify historical rows. They are reported unchanged; use the 303 observed HTTP requests for this measurement. Some legacy original labels remain.

## Deduplicated archive and ETA

Union of eligible Iranian HTML records and the existing API archive, deduplicated by source ID; existing archive data revision `b28ecbd960973cfc7cdad7d884ac264d99001ecf`. No source API was used by the crawler.

| Month | Unique records including existing API archive |
|---|---:|
| 2026-04 | 371 |
| 2026-05 | 554 |
| 2026-06 | 988 |
| 2026-07 | 889 |
| 2026-08 | 1088 |
| 2026-09 | 1827 |
| 2026-10 | 1058 |
| 2026-11 | 851 |

**7,626 unique eligible records**, leaving **48,857** against the original 56,483 target. This measurement gained 282 HTML records; the refreshed API archive also changed independently, so the union difference is not the crawler yield.

At the measured 634.34/hour, remaining / rate is **77 hours (3.21 continuous days)**. This is a conditional linear projection, not a completion promise: the high-yield frontier can run out and the sparse tail may take substantially longer. Queue exhaustion alone does not prove every site record was discovered.

## Continuation

The production controller resumed automatically with unlimited finite slices (`--pages-per-slice 100 --max-slices 0`) on the same checkpoint. It is a detached background process and does not need chat messages or an open browser page. It still depends on this managed environment remaining alive and credentials remaining valid; this is not guaranteed permanent hosting. A persistent worker is needed for a dependable multi-day unattended run. Blocking/rate-limit responses stop the crawl intentionally; checkpoint data supports resuming.
