# Collection performance review — 2026-11-15 UTC

Reviewed active scheduling, shared pacing, discovery, checkpoint seeding, queue writes, request attribution, publication and supervisor holds. No extra source requests were made for this review.

## Applied changes

- Persist SHA-256 fingerprints of channel input files in the archive checkpoint. Only changed files are decompressed and seeded. Hash and parse use the same open descriptor, preserving atomic-export consistency. Failed files roll back their queue changes and remain retryable. New/changed files still contribute links at slice boundaries; completed ads stay completed.
- Duplicate queue discoveries with equal/weaker priority no longer perform SQLite updates. Stronger evidence still updates priority and attribution.
- Coverage now additionally reports `observed_http_by_strategy`: actual ledger requests (including retries) and distinct verified Iran ads. Legacy `by_strategy` remains compatible and is explicitly labelled as completed queue rows, not actual HTTP requests. Observed history begins at ledger installation; it is not lifetime coverage.

## Offline measurement and validation

Used a SQLite backup of the production archive, with both repository channel buckets and live exports. No source traffic or production queue modifications during benchmark.

| Seed pass | Seconds | Ad links processed |
|---|---:|---:|
| Previous full scan | 2.3731 | 4,465 |
| Initial fingerprint pass | 2.3308 | 4,465 |
| Subsequent unchanged pass | 0.0231 | 0 |

The unchanged-file step is about 103 times faster, saving about 2.35 seconds per boundary. This is NOT a 103-times crawl speedup: the last production 100-page slice took 664.7 seconds and saved 19 Iran ads. Site pacing dominates runtime. The before measurement already uses the no-op queue optimization, so it isolates the file-cache benefit.

93 tests pass, including persistent-cache restart, changed exports, corrupt-file rollback/retry, no reopening completed ads, stronger-evidence priority, and HTTP retries versus unrequested queue rows.

## Operational findings

- tgadsspy budget remains exclusively with ads; channel checkpoint stays paused. Fixed cutoff remains `2026-11-13T08:53:18.687804+00:00`.
- Last observed tgadsspy delay: 5.5 seconds plus jitter; no cooldown, no recent block, no terminal halt.
- TGStat is held: `Unexpected authenticated redirect; source paused`. Recent ledger: 49 HTTP 200 responses followed by one HTTP 302. Existing logging does not retain the redirect target, so the evidence does not distinguish login expiry, account hold, or benign canonical redirect. The hold has NOT been cleared; no authenticated probe was added. A fresh login alone does not establish that this redirect is safe.
- TGStat's 92 listing slices are marked done, but listing caps and incomplete exposure prevent treating that as complete coverage. tgadsspy's ignored filters and capped listings likewise cannot be counted as independent coverage.
- Do not optimize strategy order using legacy queue-row ratios alone: discovery pages cost requests and may produce downstream ads. The new actual-request metric improves auditability; a controlled production window is still needed to claim higher end-to-end yield.

## Rollout

Updated runtime files are installed. The old archive parent was stopped while its current source child continues undisturbed. The supervisor recognizes the orphan slice and starts the updated parent only after it exits; that parent publishes any undelivered records and resumes the same checkpoint. No duplicate source worker or budget reset is introduced. Production after-change throughput remains to be measured.

Further major throughput gains require more useful records per permitted response or a verified broader listing route; this review does not increase request rate, add accounts/workers, bypass holds or claim complete enumeration.
