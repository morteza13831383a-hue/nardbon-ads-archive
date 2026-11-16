# Collection status

2026-11-16T00:33:13.942029+00:00

Targets supplied by the owner: ~547,000 Persian tgadsspy channels; ~127,000 TGStat Iran channels and ~4,500 groups. These are coverage targets, not guaranteed HTML enumeration totals. Afghan and other Persian records remain included; the importer labels region.

| Site / type | Total saved | New / 100 requests since baseline | Conditional remaining time |
|---|---:|---:|---|
| tgadsspy / channel | 46,816 | 310.06 | paused: the ad worker owns the entire tgadsspy request budget |
| tgstat / group | 1,425 | 133.41 | unknown: baseline spans the pre-resume hold; requires an active-run measurement window |
| tgstat / channel | 22,701 | 2426.19 | unknown: baseline spans the pre-resume hold; requires an active-run measurement window |

## Exclusive tgadsspy budget

Current owner: **ads**. Ad queue has not drained.
Exclusive ownership began: 2026-11-15T21:33:46.615166+00:00.

| Ad yield window | New Iran ads saved / hour | Window |
|---|---:|---|
| Before exclusive ad ownership | 29 | 1.00 h before the switch |
| After exclusive ad ownership | 6.35 | 2.99 h since the switch |

New Iran ads since switch: 19. Remaining ad jobs by kind (including sitemap): {'detail': 737470, 'listing': 26565, 'sitemap': 0}. Unresolved ad rows: {}. Undelivered Iran ads: 0.
Rates count newly saved unique Iran ads per wall-clock hour, including pauses. The before window is the preceding hour; the after window starts when channels were paused. Different discovery mixes can change yield independently of request speed.
Channels resume automatically only after the entire ad queue drains, source holds are absent, all saved ads have been delivered and the final archive process exits. Failed/robots-blocked rows remain reported as coverage gaps; this handoff does not claim full source coverage. TGStat remains on its own independent host budget.

## Pacing, pauses and remaining work

- **tgadsspy**: delay 5.25 s + jitter; pause 0 s; blocks in 6 h 0; host halted False. Stop reason: none. Slices: {'capped': 12, 'done': 63, 'ignored': 5, 'robots_disallowed': 1656, 'unsupported': 1735}; queue states: {'done': 9871, 'failed': 269, 'listing_saved': 3244, 'pending': 22822, 'robots_disallowed': 22}.
  Known listing-slice page budget upper bound: 0; future graph/range partitions are unknown, so this is not an exact pages-left count.
  Priority profile states: {'done': 7611, 'pending': 34797}. Listing caps/repeats are unresolved coverage gaps; finishing the listing frontier does not establish complete coverage.
- **tgstat**: delay 19 s + jitter; pause 0 s; blocks in 6 h 0; host halted False. Stop reason: none. Slices: {'done': 92}; queue states: {'done': 468, 'failed': 10, 'listing_saved': 7736, 'pending': 117}.
  Exact pages left are unknown until server hasMore=false for each category/type/sort; account holds are never automatically cleared.

## Filter / failure findings

- tgadsspy `order`: ignored; Parameter omitted from server pagination; https://tgadsspy.com/channels?lang=fa&category=health&order=asc.
- tgadsspy `maxMembers`: ignored; Parameter omitted from server pagination; https://tgadsspy.com/channels?lang=fa&maxMembers=20000&minMembers=5000.
- tgadsspy `sponsored`: ignored; Parameter omitted from server pagination; https://tgadsspy.com/channels?lang=fa&sponsored=eligible.
- tgadsspy `minMembers`: ignored; Parameter omitted from server pagination; https://tgadsspy.com/channels?lang=fa&minMembers=1000000000.
- tgadsspy `niche`: ignored; Parameter omitted from server pagination; https://tgadsspy.com/channels?lang=fa&niche=vpn.
- TGStat prior failures: 10 × Profile heading missing; possible sign-in/challenge page.
- The earlier 129 TGStat failures were missing expected profile headings, not proof of 129 transport blocks. The old full-profile strategy required those headings; new listing rows save metadata directly. Existing failures are preserved for audit; timeouts/5xx are retried, and only rows lacking public usernames may need profile resolution.
- Live probes found category=news changes results. Bounds 0–100 and order=asc returned the same multi-million-member channels as the base page and were omitted from pagination. Such filters cannot be treated as proven independent coverage paths.
- The Persian-language TGStat catalog beyond country Iran is not yet verified; discovery uses anonymous approved pages, and genuine account/session holds are never bypassed.

## Two-source overlap estimate

{"tgadsspy": 46816, "tgstat_public_handles": 22212, "intersection": 2446, "union_public_handles": 66582, "population_estimate": 425134}

The population estimate assumes independent, representative source samples. Catalog caps and correlated discovery violate that assumption, so it is an estimate, not a completeness certificate. Queue exhaustion with unresolved caps does not mean the owner targets were reached.

## Operation

The supervisor restarts crashed eligible workers from SQLite checkpoints, respects shared cooldowns and terminal account/host holds, and leaves the ad collection logic/cutoff unchanged. It must itself be launched after a full machine shutdown. The scheduled status task attempts to restore that supervisor on this registered environment before updating this file; a stopped/offline environment cannot crawl.

## Current-run audit — 2026-11-16 00:33 UTC

- TGStat unresolved-profile states: 66 resolved, 75 without a public username, 352 failed, 165 pending. These failures are separate from the 10 legacy profile failures above; their exact causes are not retained in this status table. They are unresolved coverage gaps, not evidence of account blocking. Recent 400 requests: 392 HTTP 200 and 8 status 0 (no completed HTTP status recorded).
- Since the verified resume at 2026-11-15T22:15:13Z: 441 requests; total records changed from 24,127 to 24,126 (net -1, or -0.23 records per 100 requests). Net change is not gross new discovery; private-to-public identity resolution can merge records. The older baseline ratios above must not be interpreted as current discovery speed.
- Latest ad slice: 100 attempted pages, zero new Iran ads, 629.2 seconds. Latest 100 HTTP requests: 99 channel-advertiser discovery pages and one overhead request. This explains the lack of immediate ad output, but does not establish that discovery will yield new ads later. Ad logic, cutoff and queue ordering remain unchanged.
- No defensible full-coverage finish date is available. TGStat's 92 completed catalog slices and the shrinking username-resolution queue do not demonstrate complete catalog enumeration.
