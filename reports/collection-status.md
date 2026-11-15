# Collection status

2026-11-15T12:38:08.815294+00:00

Targets supplied by the owner: ~547,000 Persian tgadsspy channels; ~127,000 TGStat Iran channels and ~4,500 groups. These are coverage targets, not guaranteed HTML enumeration totals. Afghan and other Persian records remain included; the importer labels region.

| Site / type | Total saved | New / 100 requests since baseline | Conditional remaining time |
|---|---:|---:|---|
| tgadsspy / channel | 46,816 | 369.54 | unknown: listing frontier ended; profile/discovery yield and reachable coverage unverified |
| tgstat / channel | 3,911 | 3840.21 | unknown: baseline spans the pre-resume hold; requires an active-run measurement window |
| tgstat / group | 1,224 | 1069.07 | unknown: baseline spans the pre-resume hold; requires an active-run measurement window |

## Pacing, pauses and remaining work

- **tgadsspy**: delay 3.5 s + jitter; pause 0 s; blocks in 6 h 0; host halted False. Stop reason: none. Slices: {'capped': 12, 'done': 63, 'ignored': 5, 'robots_disallowed': 1656, 'unsupported': 1735}; queue states: {'done': 9147, 'failed': 269, 'listing_saved': 3968, 'pending': 22822, 'robots_disallowed': 22}.
  Known listing-slice page budget upper bound: 0; future graph/range partitions are unknown, so this is not an exact pages-left count.
  Priority profile states: {'done': 5553, 'pending': 36855}. Listing caps/repeats are unresolved coverage gaps; finishing the listing frontier does not establish complete coverage.
- **tgstat**: delay 9 s + jitter; pause 0 s; blocks in 6 h 0; host halted False. Stop reason: none. Slices: {'done': 1, 'pending': 91}; queue states: {'done': 468, 'failed': 14, 'listing_saved': 4568, 'pending': 3281}.
  Exact pages left are unknown until server hasMore=false for each category/type/sort; account holds are never automatically cleared.

## Filter / failure findings

- tgadsspy `order`: ignored; Parameter omitted from server pagination; https://tgadsspy.com/channels?lang=fa&category=health&order=asc.
- tgadsspy `maxMembers`: ignored; Parameter omitted from server pagination; https://tgadsspy.com/channels?lang=fa&maxMembers=20000&minMembers=5000.
- tgadsspy `sponsored`: ignored; Parameter omitted from server pagination; https://tgadsspy.com/channels?lang=fa&sponsored=eligible.
- tgadsspy `minMembers`: ignored; Parameter omitted from server pagination; https://tgadsspy.com/channels?lang=fa&minMembers=1000000000.
- tgadsspy `niche`: ignored; Parameter omitted from server pagination; https://tgadsspy.com/channels?lang=fa&niche=vpn.
- TGStat prior failures: 14 × Profile heading missing; possible sign-in/challenge page.
- The earlier 129 TGStat failures were missing expected profile headings, not proof of 129 transport blocks. The old full-profile strategy required those headings; new listing rows save metadata directly. Existing failures are preserved for audit; timeouts/5xx are retried, and only rows lacking public usernames may need profile resolution.
- Live probes found category=news changes results. Bounds 0–100 and order=asc returned the same multi-million-member channels as the base page and were omitted from pagination. Such filters cannot be treated as proven independent coverage paths.
- The Persian-language TGStat catalog beyond country Iran is not yet verified; discovery uses anonymous approved pages, and genuine account/session holds are never bypassed.

## Two-source overlap estimate

{"tgadsspy": 46816, "tgstat_public_handles": 3802, "intersection": 706, "union_public_handles": 49912, "population_estimate": 252117}

The population estimate assumes independent, representative source samples. Catalog caps and correlated discovery violate that assumption, so it is an estimate, not a completeness certificate. Queue exhaustion with unresolved caps does not mean the owner targets were reached.

## Operation

The supervisor restarts crashed eligible workers from SQLite checkpoints, respects shared cooldowns and terminal account/host holds, and leaves the ad collection logic/cutoff unchanged. It must itself be launched after a full machine shutdown. The scheduled status task attempts to restore that supervisor on this registered environment before updating this file; a stopped/offline environment cannot crawl.
