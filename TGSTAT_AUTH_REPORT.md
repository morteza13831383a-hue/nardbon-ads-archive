# TGStat authenticated collection: first measured report

Code `3172e26942827c7a555d8823feed0a1d0b87a60b`; 49 tests pass. Account session accepted. One TGStat worker, the same checkpoint, 4–6 seconds between request starts. Both robots files remain enforced. No account settings, joining, following or content posting actions are performed.

## Saved records per 100 requests

Profile benchmark: **24 new unique records in 33 source requests = 72.73 saved records per 100 requests**. By type: {'group': 12, 'channel': 12}; per 100 requests: {'group': 36.36, 'channel': 36.36}.

Including initial session validation and catalog-depth probes: **53 requests**, **24 new unique records**, **45.28 per 100 requests**. HTTP status counts: {200: 42, 302: 11}. The denominator includes robots, approved session validation, POST pagination, profile GETs and redirects; excludes GitHub delivery and the prior public-mode crawl.

Records at measurement boundary: 214. New means new to the TGStat checkpoint; the importer deduplicates the combined catalog by username.

## Category depth observed

This is an initial depth probe, not completed enumeration of every category. The UI reload starts at page 0/offset 0 for each peer type. The collector follows server nextPage/nextOffset and considers an endpoint reached only after hasMore=false. Row counts below are observed eligible profile-link occurrences, including repeated/promoted cards; an empty terminal response counts as a fetched page but adds no data.

| Category | Type | Responses fetched | Rows per response | Distinct public handles | End confirmed |
|---|---|---:|---|---:|---|
| adult | channel | 1 | [102] | 97 | no; lower bound only |
| adult | group | 1 | [71] | 60 | no; lower bound only |
| apps | channel | 8 | [102, 102, 102, 102, 102, 102, 102, 102] | 807 | no; lower bound only |
| apps | group | 2 | [37, 0] | 37 | yes |

Other category/type tracks not yet fetched in authenticated mode: 88. Their depth remains unknown; all cursor states are in the JSON report. The background worker continues them fairly from saved positions.

## Credential protection and runtime

- Session export is stored only in an owner-only external secret file, outside the repository and report/artifact directories. Its contents are not reproduced here.
- Cookies attach only to approved known Iran profile GETs and known Iran category items POSTs on ir.tgstat.com. Anonymous catalog/robots/static requests and GitHub delivery receive no supplied account cookie.
- Actual-cookie, rotated-cookie and CSRF scans passed against exported JSON/gzip, checkpoint data and measured logs before delivery. Tests also exercise reflected credentials and debug logging.
- No logout/account-warning/limit response was observed in the measurement. Such responses, unsafe redirects, or HTTP 401/403/429 stop collection without automatic retry.
- A persisted monotonic timestamp from the pre-restart machine caused an initial local wait; the limiter now detects a reset clock. No accelerated request pace was introduced.
- Normal authenticated 25-job slices continue on the same checkpoint. Existing tgadsspy ad/channel workers were resumed after the environment restart, without TGStat credential environment variables.
