# Collection status

2026-11-15T02:37:58.913323+00:00

Targets supplied by the owner: ~547,000 Persian tgadsspy channels; ~127,000 TGStat Iran channels and ~4,500 groups. These are coverage targets, not guaranteed HTML enumeration totals. Afghan and other Persian records remain included; the importer labels region.

| Site / type | Total saved | New / 100 requests since baseline | Conditional remaining time |
|---|---:|---:|---|
| tgadsspy / channel | 38,005 | 638.7 | 165.5 continuous hours at recent yield; unverified coverage |
| tgstat / channel | 186 | 0.0 | paused / review required |
| tgstat / group | 187 | 0.0 | paused / review required |

## Pacing, pauses and remaining work

- **tgadsspy**: delay 2.5 s + jitter; pause 0 s; blocks in 6 h 0; host halted False. Stop reason: none. Slices: {'done': 51, 'ignored': 5, 'pending': 24, 'robots_disallowed': 1656, 'unsupported': 1735}; queue states: {'done': 5547, 'failed': 269, 'listing_saved': 6966, 'pending': 23424, 'robots_disallowed': 22}.
  Known listing-slice page budget upper bound: 1,964; future graph/range partitions are unknown, so this is not an exact pages-left count.
- **tgstat**: delay 8 s + jitter; pause 0 s; blocks in 6 h 0; host halted False. Stop reason: Catalog reported an error; session/account review required. Slices: {'pending': 92}; queue states: {'done': 468, 'failed': 129, 'pending': 7734}.
  Exact pages left are unknown until server hasMore=false for each category/type/sort; account holds are never automatically cleared.

## Filter / failure findings

- tgadsspy `order`: ignored; Parameter omitted from server pagination; https://tgadsspy.com/channels?lang=fa&category=health&order=asc.
- tgadsspy `maxMembers`: ignored; Parameter omitted from server pagination; https://tgadsspy.com/channels?lang=fa&maxMembers=20000&minMembers=5000.
- tgadsspy `sponsored`: ignored; Parameter omitted from server pagination; https://tgadsspy.com/channels?lang=fa&sponsored=eligible.
- tgadsspy `minMembers`: ignored; Parameter omitted from server pagination; https://tgadsspy.com/channels?lang=fa&minMembers=1000000000.
- tgadsspy `niche`: ignored; Parameter omitted from server pagination; https://tgadsspy.com/channels?lang=fa&niche=vpn.
- TGStat prior failures: 129 × Profile heading missing; possible sign-in/challenge page.
- The earlier 129 TGStat failures were missing expected profile headings, not proof of 129 transport blocks. The old full-profile strategy required those headings; new listing rows save metadata directly. Existing failures are preserved for audit; timeouts/5xx are retried, and only rows lacking public usernames may need profile resolution.
- Live probes found category=news changes results. Bounds 0–100 and order=asc returned the same multi-million-member channels as the base page and were omitted from pagination. Such filters cannot be treated as proven independent coverage paths.
- The Persian-language TGStat catalog beyond country Iran is not yet verified; discovery uses anonymous approved pages, and genuine account/session holds are never bypassed.

## Two-source overlap estimate

{"tgadsspy": 38005, "tgstat_public_handles": 186, "intersection": 65, "union_public_handles": 38126, "population_estimate": 108753}

The population estimate assumes independent, representative source samples. Catalog caps and correlated discovery violate that assumption, so it is an estimate, not a completeness certificate. Queue exhaustion with unresolved caps does not mean the owner targets were reached.

## Operation

The supervisor restarts crashed eligible workers from SQLite checkpoints, respects shared cooldowns and terminal account/host holds, and leaves the ad collection logic/cutoff unchanged. It must itself be launched after a full machine shutdown. The scheduled status task attempts to restore that supervisor on this registered environment before updating this file; a stopped/offline environment cannot crawl.
