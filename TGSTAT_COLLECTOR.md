# Public Iran TGStat backfill

```sh
python scripts/tgstat_collect.py --work-dir /persistent/tgstat-work \
  --pages-per-slice 25 --max-slices 0 \
  --repo morteza13831383a-hue/nardbon-ads-archive
```

One detached worker per checkpoint; SQLite resume, immutable username identities, no periodic recrawl. The optional `--inventory` mode visits pending public listing pages; with `--session` it also follows approved Show more cursors. Without it, listing / channel / listing / group turns keep both entity types moving. Both catalog and category group rankings are seeded from the Iran ranking navigation; only Iran-host public profiles discovered on those pages are eligible. Profiles explicitly outside both Iran and Persian are excluded. The importer's `region()` handles Iran/Afghanistan evidence; the collector preserves labels and up to 1,000 characters of public recent post text without assuming Persian means Iran.

In default public mode, only HTML GETs are used: no API, login, statistics pages, quotes, shares, related-channel endpoints, or post pagination. Both `tgstat.com/robots.txt` and `ir.tgstat.com/robots.txt` are consulted. A single TGStat-wide 4–6 second request-start budget is independent of the tgadsspy budget. Robots crawl delay takes precedence. HTTP 401/403/429 stops immediately without retries. Redirects remain within the allowed scope. Anonymous cookies are accepted, but no login or credentials are used or sent. Failed pages remain visible in the queue.

Country-category `Show more` posts to `/<category>/items` with `peer_type`, `page`, and `offset`; the live form test returned a sign-in page. Default public mode does not submit those forms; the optional session mode below supports the approved read-only form. Public pagination anchors, if present, are followed; missing pagination is not treated as full directory coverage. Public ranking pages can be capped top lists.

`data/tgstat-import/tgstat-00.jsonl.gz` through `tgstat-0f.jsonl.gz` are stable username-hash buckets, uploaded only when their digest changes. `manifest.json` records request counts, field availability, per-listing public depth observations, checksums, and incomplete-coverage notes. JSON lines match `scripts/channel_import.py`: type, username, title, description, members, avg_reach, err, category, country, language, text_sample, url, collected_at. Unknown profile metrics stay null. No list statistic is silently presented as a profile statistic.

The existing sync imports digest changes into the panel. Source checkpoints/logs remain on the execution worker; raw data is committed to GitHub at slice boundaries. The execution environment must remain alive for unattended collection.

## Optional account session for approved Show more

Configure `TGSTAT_COOKIE` in the execution environment's secret settings using a Cookie-Editor JSON export. Do not paste it into chat, commands, files, GitHub content, or reports. The program reads it only from the environment; there is no cookie command-line argument or file fallback.

```sh
python scripts/tgstat_collect.py --session --work-dir /persistent/tgstat-work \
  --pages-per-slice 25 --max-slices 0 \
  --repo morteza13831383a-hue/nardbon-ads-archive
```

Stop the previous worker at a slice boundary before switching modes on the same checkpoint. The worker lock prevents concurrent use of that checkpoint. Public mode remains the default and does not use the supplied account cookies.

Session cookies are attached only to HTTPS `ir.tgstat.com` profile GETs already known from the Iran catalog, and POST `/<known-category>/items`. They are never attached to catalog GETs, robots files, static assets, other TGStat endpoints, tgadsspy, GitHub, or redirects outside these boundaries. Session and anonymous cookie jars are separate. GitHub delivery subprocesses do not inherit TGSTAT_COOKIE. Cookie and CSRF values stay in memory; records, errors, exports and manifests pass a credential filter. Cookie-jar debug logging is disabled. Tests use dummy credentials only.

The first approved profile GET validates the session and obtains its CSRF token in memory; no login, account modification, joining, following, or posting is performed. The only authenticated POST is the site's read-only catalog form. Its website JSON wrapper is parsed as `status`, `html`, `hasMore`, `nextPage`, `nextOffset`; this does not use the TGStat API service. Cursors start at the UI's reload position `page=0, offset=0` separately for `peer_type=channel` and `peer_type=chat`, with member sorting. Subsequent cursors come from the response rather than guessed page arithmetic.

Cursor state and per-category/type coverage persist in SQLite. `catalog_coverage` in the manifest reports fetched pages, observed profile-link rows per page, distinct eligible usernames, pending/failed states, and whether `hasMore=false` was reached. Private groups without public usernames count as visible rows but are not assigned invented usernames. Repeated cursors/content or invalid responses pause rather than declare completeness.

HTTP 401/403/429, logged-out pages, account warning/limit notices, unsafe authenticated redirects and session-validation errors stop collection immediately. The affected cursor remains pending. The stop reason contains no response excerpts or credentials. A stopped session is not automatically retried. After revalidating/replacing the environment secret, explicitly restart the worker and review the saved stop reason.
