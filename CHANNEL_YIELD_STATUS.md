# Iranian-first channel crawler: production check after 4cc6e06

Code revision: `bd390a36a9e99d4ade95d2d851c1d473e5bbca61` (includes 4e357e0 and 4cc6e06). All 25 tests passed. Restarted at a completed publication/slice boundary on the same `/workspace/work/channels-production/channels.sqlite3`, with a SQLite backup before migration. The old robots-rejected Persian listing was requeued once under the new owner approval.

## Pagination

- Exact URL: `https://tgadsspy.com/channels?lang=fa`; subsequent pages: `https://tgadsspy.com/channels?lang=fa&page=N`. Plain HTML anchors; no load-more request needed. The existing OWNER_APPROVED exception matches; no change required.
- Default: 20 listing rows per page. There are often 40 link occurrences due to duplicated rendered links. Case variants can reduce distinct Telegram handles further (19 on page 1 and page 200; 20 on checked pages 2, 5 and 199).
- Pagination ends at page **200**. Page 199 links to 200; 200 has no Next. Requests for 201, 500 and 501 returned the exact same normalized handle set as 200. Do not enumerate beyond 200.
- This exposes up to **4,000 rows** at the default page size, before duplicate removal. The page/JSON-LD advertises 10,000 directory items; that is not proof the filtered listing exposes 10,000 distinct Persian channels.
- Diagnostic probes checked pages 1, 2, 5, 199, 200, 201, 500 and 501: 98 distinct handles observed. These probes are separate from the production yield denominator. No API calls or unapproved query variants were used.

## Three production slices

Three 10-page slices, same serial 4–6 second shared source budget as the ads crawler. Elapsed: 6.53 minutes, including exports/publication.

| Metric | Result |
|---|---:|
| Queue pages attempted | 30 |
| Actual source HTTP requests, including robots and redirects | 34 |
| HTTP responses | {'200': 28, '404': 5, '307': 1} |
| New saved channel profiles | 0 |
| New saved Persian/Iran-evidence profiles per 100 HTTP requests | 0.0 |
| Distinct channel handles linked by successful advertiser pages | 1275 |
| Linked candidate handles new to checkpoint | 874 |
| New Iran-advertiser-linked candidates per 100 HTTP requests | 2570.59 |
| New candidates with Persian link text per 100 HTTP requests | 2067.65 |

Profile yield and link-discovery yield are different metrics. An Iranian advertiser having bought in a channel is useful evidence for discovery, not proof of the channel’s country. Persian link text likewise does not prove Iran residence.

Saved profiles stayed at 78 → 78. All sampled queue jobs were priority-0 advertiser listings, so profile requests and the priority-1 Persian listing were not reached. Sorting by `(priority, url)` puts `/advertisers/...` ahead of `/channels/...` at equal priority. There is a substantial priority-0 backlog. Thus the current queue can postpone both profile enrichment and the Persian listing; making that listing priority -1 or interleaving job kinds would be needed to sample it promptly. This report does not change the requested queue policy.

## Advertiser channel pages

25 successful advertiser pages; 25 showed channel links. Some generated slugs returned 404; these remain visible as failed jobs. No 401/403/429 responses occurred.

- `https://tgadsspy.com/advertisers/tg-abitahrir1/channels`: 65 channel links; heading: ['Telegram channels where @abitahrir1 runs ads'].
- `https://tgadsspy.com/advertisers/tg-ablosptp/channels`: 25 channel links; heading: ['Telegram channels where @ablosptp runs ads'].
- `https://tgadsspy.com/advertisers/tg-abzar-rayan/channels`: 4 channel links; heading: ['Telegram channels where @abzar_rayan runs ads'].
- `https://tgadsspy.com/advertisers/tg-abzarforoushweb/channels`: 6 channel links; heading: ['Telegram channels where @abzarforoushweb runs ads'].

## Deployment note

`data/iran_advertisers.txt` was absent from the fetched main. Generated 1,232 advertiser slugs locally using the existing sync.advertiser_slug rule over existing IR archive records before the restart. No source API request was made. The file remains in the worker checkout; ensure the sync-generated file is committed for reproducible clean deployments.

After measurement, the same worker/checkpoint automatically resumes normal 25-page slices, publishing changed raw buckets for the existing panel importer. The ad crawler continues using the shared source budget.
