# Public Iran TGStat backfill

```sh
python scripts/tgstat_collect.py --work-dir /persistent/tgstat-work \
  --pages-per-slice 25 --max-slices 0 \
  --repo morteza13831383a-hue/nardbon-ads-archive
```

One detached worker per checkpoint; SQLite resume, immutable username identities, no periodic recrawl. The optional `--inventory` mode visits pending public listing pages only. Without it, listing / channel / listing / group turns keep both entity types moving. Both catalog and category group rankings are seeded from the Iran ranking navigation; only Iran-host public profiles discovered on those pages are eligible. Profiles explicitly outside both Iran and Persian are excluded. The importer's `region()` handles Iran/Afghanistan evidence; the collector preserves labels and up to 1,000 characters of public recent post text without assuming Persian means Iran.

Only public HTML GETs are used: no API, login, statistics pages, quotes, shares, related-channel endpoints, or post pagination. Both `tgstat.com/robots.txt` and `ir.tgstat.com/robots.txt` are consulted. A single TGStat-wide 4–6 second request-start budget is independent of the tgadsspy budget. Robots crawl delay takes precedence. HTTP 401/403/429 stops immediately without retries. Redirects remain within the allowed scope. Anonymous cookies are accepted, but no login or credentials are used or sent. Failed pages remain visible in the queue.

Country-category `Show more` posts to `/<category>/items` with `peer_type`, `page`, and `offset`; the live form test returned a sign-in page. This collector deliberately does not submit those forms. Public pagination anchors, if present, are followed; missing pagination is not treated as full directory coverage. Public ranking pages can be capped top lists.

`data/tgstat-import/tgstat-00.jsonl.gz` through `tgstat-0f.jsonl.gz` are stable username-hash buckets, uploaded only when their digest changes. `manifest.json` records request counts, field availability, per-listing public depth observations, checksums, and incomplete-coverage notes. JSON lines match `scripts/channel_import.py`: type, username, title, description, members, avg_reach, err, category, country, language, text_sample, url, collected_at. Unknown profile metrics stay null. No list statistic is silently presented as a profile statistic.

The existing sync imports digest changes into the panel. Source checkpoints/logs remain on the execution worker; raw data is committed to GitHub at slice boundaries. The execution environment must remain alive for unattended collection.
