# Channel records saved directly from Persian listings

Production revision `c3ab9d7517220159e716c409d447748b61cb891d` includes a6b6bef job rotation and the listing fast path. All **31 tests** passed. Same checkpoint; persisted rotation turn and prior records retained. The corrected live parser was measured in three 10-page slices at the existing shared serial source pace.

## Behavior

- `/channels?lang=fa[&page=N]` rows save title, normalized username, members, average views, category and listing source URL. New rows are `persian_candidate`; this does not prove country.
- Parser matches column headings, handles Persian digits and K/M counts, desktop streamed trailing columns, and mobile channel links without a role=cell. Case variants and duplicated desktop/mobile links collapse to one username.
- Complete summaries are deferred as `listing_saved`. Missing numbers or existing Iran-buyer evidence (priority-0 discovery / iran_advertisers) retain profile work. Later stronger discovery reopens deferred work. Existing full profiles and bios are preserved; a Persian listing can strengthen their classification.
- Robots-disallowed filter URLs are excluded from rotation slots. The exact approved Persian-listing exception and other access/rate rules remain in force.
- An initial live check exposed streamed-row differences; its results are excluded from the figures below. The first listing was requeued once after correcting its parser; this is not a periodic refresh.

## Measured production yield

| Metric | Result |
|---|---:|
| Queue page attempts | 30 |
| Actual source HTTP requests, including robots/redirects | 33 |
| Source requests by type | {'robots': 3, 'advertiser': 8, 'profile': 15, 'persian_listing': 7} |
| HTTP responses | {'200': 33} |
| Saved records before → after | 93 → 243 |
| Net new unique channel records | 150 |
| **New unique channels saved per 100 source HTTP requests** | **454.55** |
| New Persian / Iran-evidence channel records | 150 |
| New Persian / Iran-evidence records per 100 source HTTP requests | 454.55 |
| Detail types of net new records at measurement end | {'profile': 15, 'listing': 135} |
| Elapsed including exports and GitHub publication | 6.23 minutes |

These are net new unique records, not discovered links or repeated saves of the same channel. A listing request can save many records, so the per-100-request figure can exceed 100. The denominator includes only this channel worker’s source HTTP requests, including robots and redirects; parallel ad-worker requests and GitHub delivery requests are excluded. Some existing records may have been enriched without increasing the unique count.

61 complete summary profiles were deferred (`listing_saved`) at the measurement boundary. All 150 new records have Persian or Iranian-advertiser evidence: 148 are classified persian_candidate; two are unclassified but link Iranian advertisers.

## Sample listing records

- `tofan_trade`: members 6330765, average views 114670; source `https://tgadsspy.com/channels?lang=fa`.
- `ne_wg`: members 5235656, average views 57603; source `https://tgadsspy.com/channels?lang=fa`.
- `kh_mx`: members 5071154, average views 13210; source `https://tgadsspy.com/channels?lang=fa`.

The measured data was published in the existing hash buckets. After measurement the detached worker resumes normal 25-page slices on the same checkpoint. The ads collector continues with the same shared request budget.
