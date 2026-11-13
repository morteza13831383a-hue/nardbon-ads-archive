# TGStat first production report

Revision `de040d67411dee49d8204073b53e24d44c1fa2fe`. All 38 tests passed. One worker, separate TGStat request-start budget of 4–6 seconds (or longer robots delay); no source API, login or statistics/quotes/share requests.

## Yield

Total production source HTTP requests: **166**; records: **{'channel': 22, 'group': 22}**. Whole-run saved entities per 100 source requests: **{'channel': 13.25, 'group': 13.25}**. This includes the category inventory and robots requests; standalone diagnostic probes are excluded.

After inventory, a separate 40-job profile sample used **60 source HTTP requests**, saving **{'group': 20, 'channel': 20}** new records: **{'group': 33.33, 'channel': 33.33} per 100 requests**. Counters are net saved username-unique records in the TGStat checkpoint, not discovered links or guaranteed additions to the combined panel. The existing importer deduplicates across sources by username. Status 0 denotes a counted pre-response transport timeout, not an HTTP response; one initial inventory timeout was resumed on the same checkpoint. HTTP status counts: {0: 1, 200: 147, 302: 18}.

## Public depth by category

The Iran taxonomy exposes 47 categories. The table shows every category’s observed public footprint. Catalogs use POST `/<category>/items` (`peer_type`, `page`, `offset`) for Show more; the tested News form returned a sign-in page. No form continuation is submitted by this GET-only collector. There are no public page anchors in the measured snapshots unless noted below. Thus **1 is the observed public HTML page count, not a claim that the entire category has only one page**. Full catalog depth is unknown behind that gate. Ranking snapshots are capped lists; no full-directory completeness claim is made.

| Category | Channel catalog: public pages / links / unique handles | Group ranking: public pages / links / unique handles |
|---|---|---|
| adult | 1 / 97 / 97 | 1 / 66 / 66 |
| apps | 1 / 99 / 99 | 1 / 40 / 40 |
| art | 1 / 102 / 102 | 1 / 2 / 2 |
| babies | 1 / 62 / 62 | 1 / 5 / 5 |
| beauty | 1 / 93 / 93 | 1 / 8 / 8 |
| blogs | 1 / 101 / 101 | 1 / 19 / 19 |
| books | 1 / 93 / 93 | 1 / 24 / 24 |
| business | 1 / 97 / 97 | 1 / 45 / 45 |
| career | 1 / 101 / 101 | 1 / 7 / 7 |
| construction | 1 / 33 / 33 | 1 / 5 / 5 |
| courses | 1 / 39 / 39 | 1 / 4 / 4 |
| crypto | 1 / 101 / 101 | 1 / 98 / 98 |
| darknet | 1 / 69 / 69 | 1 / 9 / 9 |
| design | 1 / 100 / 100 | 1 / 14 / 14 |
| economics | 1 / 102 / 102 | 1 / 68 / 68 |
| education | 1 / 93 / 93 | 1 / 98 / 98 |
| edutainment | 1 / 97 / 97 | 1 / 1 / 1 |
| entertainment | 1 / 96 / 96 | 1 / 14 / 14 |
| erotica | 1 / 87 / 87 | 1 / 7 / 7 |
| esoterics | 1 / 86 / 86 | 1 / 4 / 4 |
| food | 1 / 75 / 75 | 1 / 8 / 8 |
| gambling | 1 / 97 / 97 | 1 / 18 / 18 |
| games | 1 / 101 / 101 | 1 / 82 / 82 |
| handmade | 1 / 45 / 45 | 1 / 0 / 0 |
| health | 1 / 102 / 102 | 1 / 10 / 10 |
| instagram | 1 / 36 / 36 | 1 / 4 / 4 |
| language | 1 / 102 / 102 | 1 / 16 / 16 |
| law | 1 / 52 / 52 | 1 / 9 / 9 |
| marketing | 1 / 101 / 101 | 1 / 21 / 21 |
| medicine | 1 / 99 / 99 | 1 / 21 / 21 |
| music | 1 / 101 / 101 | 1 / 17 / 17 |
| nature | 1 / 44 / 44 | 1 / 6 / 6 |
| news | 1 / 101 / 101 | 1 / 44 / 44 |
| other | 1 / 100 / 100 | 1 / 97 / 97 |
| pics | 1 / 99 / 99 | 1 / 1 / 1 |
| politics | 1 / 102 / 102 | 1 / 43 / 43 |
| psychology | 1 / 98 / 98 | 1 / 3 / 3 |
| quotes | not fetched | not fetched |
| religion | 1 / 101 / 101 | 1 / 44 / 44 |
| sales | 1 / 102 / 102 | 1 / 52 / 52 |
| shock | 1 / 21 / 21 | 1 / 3 / 3 |
| sport | 1 / 101 / 101 | 1 / 15 / 15 |
| tech | 1 / 102 / 102 | 1 / 45 / 45 |
| telegram | 1 / 99 / 99 | 1 / 78 / 78 |
| transport | 1 / 101 / 101 | 1 / 8 / 8 |
| travels | 1 / 86 / 86 | 1 / 8 / 8 |
| video | 1 / 96 / 96 | 1 / 28 / 28 |

Quotes category URLs are excluded by the no-quotes policy. Counts are eligible profile-link occurrences and case-normalized handles; promoted entries and aliases can make counts differ from an apparent top-100 label. Private or unhandled entities without public usernames cannot be imported by the current username-based schema.

Public listing snapshots fetched: 95; public next links observed: 0. Failed jobs: [].

## Fields actually available

| Field | Channels with value | Groups with value |
|---|---:|---:|
| title | 22 / 22 | 22 / 22 |
| description | 22 / 22 | 20 / 22 |
| members | 22 / 22 | 0 / 22 |
| avg_reach | 0 / 22 | 0 / 22 |
| err | 0 / 22 | 0 / 22 |
| category | 22 / 22 | 22 / 22 |
| country | 22 / 22 | 22 / 22 |
| language | 22 / 22 | 22 / 22 |
| text_sample | 22 / 22 | 0 / 22 |

Missing values remain null or empty; subscriber counts, average reach and ERR are not inferred from individual posts or list counts. Full visible bios are preferred over metadata summaries. Only public recent post text is sampled, up to 1,000 characters; no post pagination is fetched.

The existing importer’s region() classified these records as {'ir': 9, 'unknown': 33, 'af': 2} using title, description and text_sample. These are heuristic results, not verified residence. TGStat Iran/Persian labels alone are not used to assert Iranian nationality.

## Delivery and continuation

Changed stable gzip JSONL buckets and a checksum/coverage manifest were committed under `data/tgstat-import/`; the existing digest-aware importer can read them. Normal 25-job slices continue on the same SQLite checkpoint after the measurement. The tgadsspy ad/channel workers retain their separate source budget.
