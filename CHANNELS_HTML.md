# One-off HTML channel discovery

Run independently of the existing API channel sync:

```sh
python scripts/channels_html.py \
  --work-dir /persistent/channels-work \
  --ads-checkpoint /persistent/ads-work/checkpoint.sqlite3 \
  --pages-per-slice 25 --max-slices 0 \
  --repo morteza13831383a-hue/nardbon-ads-archive
```

The optional `--repo` uses the existing authenticated `gh` CLI for durable raw-data commits. It does not call the source API. Results go to `data/channel-html-import/` alongside existing panel files. This collector does not yet merge into the live panel or upload to the download host.

Seeds: channel URLs discovered by the ad crawler, public channel directories, and sitemap channel entries. At slice boundaries it imports newly discovered ad channel links read-only. Queue URLs and usernames are deduplicated; completed URLs are not requested again. A separate SQLite checkpoint and worker lock support restart and prevent duplicate workers. Failed pages remain visible; they are not silently counted as completed. HTTP 401/403/429 stops the job without completing the affected page.

Both collectors must use the updated `html_core.py` and `source_budget.py` to share a host-wide request budget: minimum 4 seconds plus 0–2 seconds jitter between request starts, honoring robots crawl delay. `CRAWL_BUDGET_DIR` can select a persistent shared directory. Separate machines must coordinate externally; these locks only coordinate processes on one host. Concurrent collectors divide the existing throughput rather than doubling source traffic. Initialization/redirects/retries also use the shared budget.

Local outputs: `exports/channels.jsonl.gz` and `exports/manifest.json` (counts, queue status, checksum, limitations). GitHub delivery uses 16 stable compressed username-hash buckets, uploading only changed buckets. Public profile fields include title, description, JSON-LD, metadata, text segments, links and media attributes. Unknown source fields remain preserved as raw structured/text data; unavailable fields are not invented.

Classification: explicit channel JSON-LD Iran evidence yields `iran_confirmed`; a Persian title/description without country proof yields `persian_candidate`; others remain `unclassified`. Iran ad placement alone does not prove country. This is public, current-at-fetch profile discovery, not a historical channel snapshot or a guaranteed complete list of all Iranian Telegram channels. Hidden/unindexed channels cannot be promised.

## Listing summaries

Persian `/channels?lang=fa[&page=N]` table rows are saved directly with case-insensitive usernames, title, numeric members/average views, category and listing `source_url`. Columns are matched by heading. A complete summary defers the profile as `listing_saved`; missing numbers or known Iran-buyer evidence (priority-0 discovery or `iran_advertisers`) retain profile work. Later priority-0 rediscovery reopens deferred work. Summaries preserve existing full-profile data. Profile enrichment preserves the original Persian-listing classification and provenance. Bios remain the existing sync's responsibility.
