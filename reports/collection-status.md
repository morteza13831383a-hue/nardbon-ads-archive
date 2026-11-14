# Collection status

2026-11-14T16:37:11.042986+00:00

Targets supplied by the owner: ~547,000 Persian tgadsspy channels; ~127,000 TGStat Iran channels and ~4,500 groups. These are coverage targets, not guaranteed HTML enumeration totals. Afghan and other Persian records remain included; the importer labels region.

| Site / type | Total saved | New / 100 requests since baseline | Conditional remaining time |
|---|---:|---:|---|
| tgadsspy / channel | 7,194 | not yet measured | not defensible yet |
| tgstat / channel | 186 | not yet measured | not defensible yet |
| tgstat / group | 187 | not yet measured | not defensible yet |

## Pacing, pauses and remaining work

- **tgadsspy**: delay 5 s + jitter; pause 0 s; blocks in 6 h 0; host halted False. Stop reason: none. Slices: {'pending': 15}; queue states: {'done': 4702, 'failed': 269, 'listing_saved': 2519, 'pending': 28716, 'robots_disallowed': 22}.
  Known listing-slice page budget upper bound: 3,000; future graph/range partitions are unknown, so this is not an exact pages-left count.
- **tgstat**: delay 8 s + jitter; pause 1784 s; blocks in 6 h 1; host halted False. Stop reason: none. Slices: {'pending': 92}; queue states: {'done': 468, 'failed': 129, 'pending': 7734}.
  Exact pages left are unknown until server hasMore=false for each category/type/sort; account holds are never automatically cleared.

## Filter / failure findings

- TGStat prior failures: 129 × Profile heading missing; possible sign-in/challenge page.
- The earlier 129 TGStat failures were missing expected profile headings, not proof of 129 transport blocks. The old full-profile strategy required those headings; new listing rows save metadata directly. Existing failures are preserved for audit; timeouts/5xx are retried, and only rows lacking public usernames may need profile resolution.
- Live probes found category=news changes results. Bounds 0–100 and order=asc returned the same multi-million-member channels as the base page and were omitted from pagination. Such filters cannot be treated as proven independent coverage paths.
- The Persian-language TGStat catalog beyond country Iran is not yet verified; discovery uses anonymous approved pages, and genuine account/session holds are never bypassed.

## Two-source overlap estimate

{"tgadsspy": 7194, "tgstat_public_handles": 186, "intersection": 58, "union_public_handles": 7322, "population_estimate": 23070}

The population estimate assumes independent, representative source samples. Catalog caps and correlated discovery violate that assumption, so it is an estimate, not a completeness certificate. Queue exhaustion with unresolved caps does not mean the owner targets were reached.

## Operation

The supervisor restarts crashed eligible workers from SQLite checkpoints, respects shared cooldowns and terminal account/host holds, and leaves the ad collection logic/cutoff unchanged. It must itself be launched after a full machine shutdown. The scheduled status task attempts to restore that supervisor on this registered environment before updating this file; a stopped/offline environment cannot crawl.
