# Additional source collection status

2026-11-16 UTC. The collector used the current main branch and each host's independent `source_budget` state. No source returned a captcha or Cloudflare challenge in these probes.

| Source | Requests in this probe | Rows seen | New vs existing catalog | Stop / next step |
|---|---:|---:|---:|---|
| telegramadsarchive.com | 21 (robots + 20 ads) | 20 ad pages; 11 with a country label | 0 verified Iran ads | 20-page probe complete; no block. Oldest April IDs. |
| telegramadsanalytics.com | 21 (robots + 20 ads) | 20 ad pages; 12 with a country label | 0 verified Iran ads | 20-page probe complete; no block. Newest November IDs. |
| tglist.io | 2 (robots + one API attempt) | 0 | 0 | HTTP 403 on `/api/search_channels_v2`; host stopped. Permission ends 2026-12-16 23:59 UTC. |
| lyzem.com | 11 (robots + 10 search pages) | 500 listing rows | 435 new: 212 channels, 223 groups | No block. First term `فروشگاه`, pages 1–5 for each type; full checkpointed run started. |
| telemetr.io | 7 (robots, home, Iran pages 1–3, two field checks) | 99 unique Iran-list handles; pages 2–3 repeated page 1 | 26 new (26.3%) | Probe only; pagination must be resolved before a full run. |
| telegramchannels.me | 8 (robots, home, Persian home, language category, channel pages 1–2, group page 1, one field check) | 45 unique channel and 36 group handles (80 unique overall) | 52 new overall (65.0%) | Probe only. `/fa/` appears localized and has many Persian titles; it is not an Iran-only filter. |

The mirror pages were checked with the same ad ID and Iran `contentLocation` rule as tgadsspy. A missing country remains unverified and is never imported as Iran. `data/ads/*.json` and `data/html-archive-import` IDs were excluded from the ad probes. TGStat full-search restriction remains untouched; its separately checkpointed Quick search has no current Quick-search hold.

The telemetr.io and telegramchannels.me probes stay below the requested 30-request limit per site. Their full collection awaits the requested confirmation. TGList remains stopped after its 403 response; no retry is scheduled.

Telemetr's Iran table exposes a channel username/link, title, rank, member count, engagement statistics and category. Its numbered `?page=N` links changed the page title but repeated the same 99 handles on pages 1–3. telegramchannels.me's `/fa/channels?page=N` changes listing rows; cards show title, member count and description, and `/fa/groups` lists groups. `/fa/` is a site locale rather than a verified country filter.

## Full-run snapshot at 2026-11-16T21:20:49+00:00

| Worker | Checkpointed requests | Rows found / classified | New saved vs initial catalog | Block |
|---|---:|---:|---:|---|
| telegramadsarchive.com (oldest first) | 59 | 39 non-Iran; 20 no country | 0 verified Iran ads | none |
| telegramadsanalytics.com (newest first) | 105 | 87 non-Iran; 18 no country | 0 verified Iran ads | none |
| lyzem.com | 103 | 5051 result rows | 4278 new usernames | none |
| TGStat Quick search | 76 | 994 result rows | 186 new vs `data/channels.json` (426 channels and 17 groups newly saved in its own checkpoint) | none on Quick search |

Both mirror workers, Lyzem and TGStat Quick search are running from separate checkpoints. The older TGStat full-search `status=restricted` hold remains. TGList API remains stopped after HTTP 403 with zero saved rows.

