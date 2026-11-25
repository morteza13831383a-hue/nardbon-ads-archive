# Channel collection on a Linux VPS

## 1. Budget and access

The deployed host and archive checkpoint are the authority for source access. The production tgadsspy challenge was still recorded when this upgrade was prepared. Run the new unit only after production access is restored. Use the same channel checkpoint, lock directory, archive checkpoint and CRAWL_BUDGET_DIR as the other collectors on that machine. File locks on independent VPS filesystems cannot coordinate one shared budget.

100,000 distinct profile pages per 24 hours requires at least 1.1574 successful profiles per second, before charging for robots, discovery, retries and duplicates. The existing 2.5-second minimum allows at most 34,560 request starts/day across all workers sharing this host, before jitter and pauses. A four-request concurrent transport would still share that budget. Listing pages can supply many channel records per request. Measure net new normalized usernames, successful profile pages, listing pages and total HTTP requests separately.

## 2. Fetch, process and recover

The VPS entry point reuses channels_html.seed, enqueue, next_job and consume. The normal sitemap index, nested sitemaps, approved Persian listings, archive placements and advertiser discovery all feed the same queue. The worker acquires the existing worker.lock before recovering leases. SQLite WAL allows checkpoint readers during processing. Each short BEGIN IMMEDIATE transaction claims one batch; fetching happens after commit. Metadata, new links, completion and the pending export entry commit together. SIGINT or SIGTERM releases unfinished jobs and flushes the outbox. A hard kill leaves unfinished claims available for recovery under the same exclusive lock. Re-fetching a response interrupted before commit is possible. Old queue status labels remain readable.

The default transport is a synchronous curl_cffi session with the collector's real identity. html_core controls redirects, robots, origin checks, body limits and all reserve/report calls. Set --transport stdlib for urllib. Lexbor supplies the channel document tree; the built-in HTML parser is the fallback. Country evidence and language evidence are independent. Persian/Farsi is a language label. has_persian_script records the requested Unicode range, shared with Arabic and other languages. Stronger language signals or explicit Iran evidence determine catalog inclusion. Unknown fields remain empty. Related usernames are normalized and restricted to same-origin channel URLs.

For an existing Linux service account with a full checkout in /opt/nardbon and Python 3.12:

```sh
cd /opt/nardbon
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests
```

Use a local filesystem for SQLite and the outbox. Create /etc/nardbon/channel-worker.env with your real paths (the paths below are examples):

```ini
CHANNEL_WORK=/var/lib/nardbon/channels
ADS_CHECKPOINT=/var/lib/nardbon/archive/checkpoint.sqlite3
CRAWL_BUDGET_DIR=/var/lib/nardbon/source-budget
CHANNEL_OUTBOX=/var/lib/nardbon/channel-outbox
```

The service account needs read/write access to CHANNEL_WORK, CHANNEL_OUTBOX and the shared budget, and read access to the actual ads checkpoint and any SQLite WAL files. The publisher needs a normal full Git checkout, a configured commit identity and the deployment's existing authorized push access to origin/main. Create CHANNEL_OUTBOX before enabling the timer.

## 3. Seed and start

The default seed sources run at startup. Optional flags support newline-separated handles or approved URLs, advertiser slugs and local catalog matching using keyword files. Words alone are insufficient to construct channel URLs. Keyword matching prioritizes known catalog titles and descriptions, then the fetched pages contribute new related links. To use these files, append the following options to the worker unit's ExecStart in the checkout before installation:

```text
--seed /var/lib/nardbon/custom-seeds.txt --advertisers /opt/nardbon/data/advertiser_seeds.txt --keywords /opt/nardbon/data/channel_keywords.txt /opt/nardbon/data/channel_keywords_auto.txt --catalog /opt/nardbon/data/channels.json
```

At a slice boundary, stop the previous channel network worker for this checkpoint. Retain its database and the shared source budget. When the source is available:

```sh
sudo install -m 0644 deploy/systemd/nardbon-channel-* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nardbon-channel-publish.timer nardbon-channel-worker.service
journalctl -u nardbon-channel-worker.service -u nardbon-channel-publish.service
```

A challenge or recorded terminal hold exits with status 75 and prevents automatic restart. Ordinary 403/429 responses are reported to the shared budget and retried after its pause. An exhausted finite queue exits successfully. Existing GitHub Actions schedules continue to handle imports and panel delivery.

## 4. Publish and verify

The outbox contains immutable gzip JSONL parts. Each filename includes the SHA-256 of the uncompressed bytes. On the first migration existing channel records enter the outbox once. Subsequent exports contain changed records. The timer uses a temporary detached worktree and the repo's scripts/commit_data.py to publish to data/channel-html-import. It compares the remote contents before acknowledging each local file. An interrupted publish can be rerun. The ordinary channel importer merges by username; profile observations retain separate country and language fields. Check the importer workflow and identifiers on the download host separately from a successful Git push.

```sh
sqlite3 -readonly "$CHANNEL_WORK/channels.sqlite3" 'SELECT status,count(*) FROM queue GROUP BY status'
sqlite3 -readonly "$CHANNEL_WORK/channels.sqlite3" 'SELECT count(*) FROM channel_outgoing'
systemctl list-timers nardbon-channel-publish.timer
```
