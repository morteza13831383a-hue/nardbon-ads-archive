# Collection status

2026-11-17T10:41:44.148710+00:00

Targets supplied by the owner: ~547,000 Persian tgadsspy channels; ~127,000 TGStat Iran channels and ~4,500 groups. These are coverage targets, not guaranteed HTML enumeration totals. Afghan and other Persian records remain included; the importer labels region.

| Site / type | Total saved | New / 100 requests since baseline | Conditional remaining time |
|---|---:|---:|---|
| tgadsspy / channel | 46,816 | 310.06 | paused: the ad worker owns the entire tgadsspy request budget |
| tgstat / group | 1,579 | 77.64 | paused / review required |
| tgstat / channel | 25,487 | 1411.1 | paused / review required |

## Exclusive tgadsspy budget

Current owner: **ads**. Ad queue has not drained.
Exclusive ownership began: 2026-11-15T21:33:46.615166+00:00.

| Ad yield window | New Iran ads saved / hour | Window |
|---|---:|---|
| Before exclusive ad ownership | 29 | 1.00 h before the switch |
| After exclusive ad ownership | 68.19 | 37.13 h since the switch |

New Iran ads since switch: 2,532. Remaining ad jobs by kind (including sitemap): {'detail': 730943, 'listing': 26787, 'sitemap': 0}. Unresolved ad rows: {}. Undelivered Iran ads: 0.
Rates count newly saved unique Iran ads per wall-clock hour, including pauses. The before window is the preceding hour; the after window starts when channels were paused. Different discovery mixes can change yield independently of request speed.
Channels resume automatically only after the entire ad queue drains, source holds are absent, all saved ads have been delivered and the final archive process exits. Failed/robots-blocked rows remain reported as coverage gaps; this handoff does not claim full source coverage. TGStat remains on its own independent host budget.

## Pacing, pauses and remaining work

- **tgadsspy**: delay 15.0 s + jitter; pause 0 s; blocks in 6 h 0; host halted False. Stop reason: none. Slices: {'capped': 12, 'done': 63, 'ignored': 5, 'robots_disallowed': 1656, 'unsupported': 1735}; queue states: {'done': 9871, 'failed': 269, 'listing_saved': 3244, 'pending': 22822, 'robots_disallowed': 22}.
  Known listing-slice page budget upper bound: 0; future graph/range partitions are unknown, so this is not an exact pages-left count.
  Priority profile states: {'done': 7611, 'pending': 34797}. Listing caps/repeats are unresolved coverage gaps; finishing the listing frontier does not establish complete coverage.
- **tgstat**: delay 19.75 s + jitter; pause 0 s; blocks in 6 h 0; host halted False. Stop reason: TGStat search account restricted (site status=restricted). Slices: {'done': 92}; queue states: {'done': 468, 'failed': 10, 'listing_saved': 7736, 'pending': 117}.
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

{"tgadsspy": 46816, "tgstat_public_handles": 24998, "intersection": 2694, "union_public_handles": 69120, "population_estimate": 434412}

The population estimate assumes independent, representative source samples. Catalog caps and correlated discovery violate that assumption, so it is an estimate, not a completeness certificate. Queue exhaustion with unresolved caps does not mean the owner targets were reached.

## Operation

The supervisor restarts crashed eligible workers from SQLite checkpoints, respects shared cooldowns and terminal account/host holds, and leaves the ad collection logic/cutoff unchanged. It must itself be launched after a full machine shutdown. The scheduled status task attempts to restore that supervisor on this registered environment before updating this file; a stopped/offline environment cannot crawl.


## TGStat username-resolution frontier

States: {'failed': 517, 'no_public_username': 75, 'resolved': 66}. Known frontier exhausted: True.
When this frontier is empty and delivery has succeeded, the supervisor leaves the worker idle instead of repeatedly restarting it. Failed profiles and listing caps remain unresolved coverage gaps; this is not complete coverage.
