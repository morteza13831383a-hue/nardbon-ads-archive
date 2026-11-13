# crawler-yield review and live pilot

Tested source: `6da9d8cf615c1692e0f0a0f985da6ff377051bec`. The original slice used `40b0d694f36700ccaa769a097f5da23f55e1704c`. Later branch commits are not included in this merge candidate.

## Method

- Finished the original 200-page slice on a copy: 163 new Iran ads, through the existing advertiser graph.
- Ran the three requested 100-page slices sequentially on independent SQLite backups of one production snapshot: 4,307 existing records, cutoff `2026-11-13T08:53:18.687804+00:00`.
- Kept the source pace at 4 seconds plus 0–2 seconds of jitter. Paused production source processes during the tests and resumed them afterward. No source API, media downloads, host uploads or production checkpoint writes were performed by the tests.
- Instrumented HTTP separately without changing the tested branch. The HTTP denominator below includes robots and redirects; native coverage counters count queue rows and are cumulative.

## Per-slice results

| Filter | Queue pages | Actual HTTP requests | New unique Iran ads | Iran / 100 HTTP |
|---|---:|---:|---:|---:|
| `channel_advertisers,advertiser_via_channel` | 100 | 103 | 0 | 0.0 |
| `advertiser_niche,advertiser` | 100 | 101 | 27 | 26.73 |
| `sitemap` | 100 | 101 | 0 | 0.0 |

**Merge gate: passed by the advertiser family (26.73 versus 0.00 Iran ads per 100 HTTP requests).** No 403 or 429 responses occurred in the three filtered pilots. All 27 new advertiser records have distinct IDs, matching JSON-LD identifiers, country IR, and first-seen timestamps within the cutoff.

## coverage.by_strategy: per-slice deltas

Inherited history is excluded from this table. A missing strategy was not exercised, not proven ineffective.

| Slice | via | Queue rows completed | Iran ads | Native Iran / 100 rows |
|---|---|---:|---:|---:|
| channels | `channel_advertisers` | 100 | 0 | 0.0 |
| advertisers | `advertiser>ad` | 27 | 27 | 100.0 |
| advertisers | `advertiser` | 73 | 0 | 0.0 |
| sitemap | `sitemap` | 100 | 0 | 0.0 |

`advertiser_via_channel` and `advertiser_niche` were not fetched in these pilots. The channel run spent its budget on channel listings (priority 6), ahead of their advertiser pages (priority 7). The second pilot reached ordinary advertiser pages and their new ads, not niche-specific pages.

## Monthly coverage and remaining limitations

`coverage.by_month` is `[]` in all three runs; `site_sum_of_months` is 0, which means missing site metadata, not zero Iran coverage. The resumed checkpoint already marked April–November snapshots done before the new parser existed. Those snapshots were not revisited, and the filtered runs do not select snapshot jobs. Consequently all month-yield weights were zero: the control does not validate yield-weighted month ordering or completeness.

Existing queued URLs retain their existing `via` on rediscovery. A sitemap URL promoted by an advertiser page can therefore remain excluded from `--only-via advertiser`. Newly enqueued ad URLs inherited the parent strategy correctly, producing the measured 27 ads. The default unfiltered crawler still promotes existing URLs by priority, but filtered attribution needs further work. New sitemap rows queued after the one-time label migration can also remain unlabelled.

No IDs were skipped by the time heuristics in these pilots. The widened margin keeps the observed +71.3-hour outlier; these pilots do not establish a general pruning benefit or prove complete archive coverage.

## Validation

The unmodified branch suite had 13 passing tests and one outdated resume mock: it did not provide the newly discovered channel-advertiser page. The merge candidate updates that fixture and adds tests for filtered child-ad selection and deferring the observed near-cutoff CUID outlier. Full candidate suite: **16 passed**. `git diff --check` passed. Source behavior used by the live pilots is unchanged by these test-only updates.

## Original 200-page Yield plan log

```text
Yield plan: {"pending_dated": 745242, "after_cutoff_by_id": 0, "before_iran_by_id": 0, "month_order": [["2026-04", 0.0, 23848], ["2026-05", 0.0, 43584], ["2026-06", 0.0, 117197], ["2026-07", 0.0, 117300], ["2026-08", 0.0, 122785], ["2026-09", 0.0, 191627], ["2026-10", 0.0, 93569], ["2026-11", 0.0, 35332]]}
```

## Full native coverage and Yield plan output

### channels

```json
{
  "coverage": {
    "by_month": [],
    "site_sum_of_months": 0,
    "extracted_total": 4307,
    "by_strategy": [
      {
        "via": "iran_listing",
        "requests": 3820,
        "iran": 3818,
        "iran_per_100": 99.95
      },
      {
        "via": "same_advertiser",
        "requests": 580,
        "iran": 476,
        "iran_per_100": 82.07
      },
      {
        "via": "sitemap",
        "requests": 3820,
        "iran": 13,
        "iran_per_100": 0.34
      },
      {
        "via": "listing",
        "requests": 25,
        "iran": 0,
        "iran_per_100": 0.0
      },
      {
        "via": "channel_advertisers",
        "requests": 100,
        "iran": 0,
        "iran_per_100": 0.0
      },
      {
        "via": "advertiser",
        "requests": 126,
        "iran": 0,
        "iran_per_100": 0.0
      }
    ],
    "skipped_by_id_time": 0
  },
  "yield_plan": {
    "pending_dated": 744510,
    "after_cutoff_by_id": 0,
    "before_iran_by_id": 0,
    "month_order": [
      [
        "2026-04",
        0.0,
        23847
      ],
      [
        "2026-05",
        0.0,
        43583
      ],
      [
        "2026-06",
        0.0,
        117078
      ],
      [
        "2026-07",
        0.0,
        117165
      ],
      [
        "2026-08",
        0.0,
        122667
      ],
      [
        "2026-09",
        0.0,
        191627
      ],
      [
        "2026-10",
        0.0,
        93569
      ],
      [
        "2026-11",
        0.0,
        34974
      ]
    ]
  }
}
```

### advertisers

```json
{
  "coverage": {
    "by_month": [],
    "site_sum_of_months": 0,
    "extracted_total": 4334,
    "by_strategy": [
      {
        "via": "iran_listing",
        "requests": 3820,
        "iran": 3818,
        "iran_per_100": 99.95
      },
      {
        "via": "same_advertiser",
        "requests": 580,
        "iran": 476,
        "iran_per_100": 82.07
      },
      {
        "via": "advertiser>ad",
        "requests": 27,
        "iran": 27,
        "iran_per_100": 100.0
      },
      {
        "via": "sitemap",
        "requests": 3820,
        "iran": 13,
        "iran_per_100": 0.34
      },
      {
        "via": "listing",
        "requests": 25,
        "iran": 0,
        "iran_per_100": 0.0
      },
      {
        "via": "advertiser",
        "requests": 199,
        "iran": 0,
        "iran_per_100": 0.0
      }
    ],
    "skipped_by_id_time": 0
  },
  "yield_plan": {
    "pending_dated": 744510,
    "after_cutoff_by_id": 0,
    "before_iran_by_id": 0,
    "month_order": [
      [
        "2026-04",
        0.0,
        23847
      ],
      [
        "2026-05",
        0.0,
        43583
      ],
      [
        "2026-06",
        0.0,
        117078
      ],
      [
        "2026-07",
        0.0,
        117165
      ],
      [
        "2026-08",
        0.0,
        122667
      ],
      [
        "2026-09",
        0.0,
        191627
      ],
      [
        "2026-10",
        0.0,
        93569
      ],
      [
        "2026-11",
        0.0,
        34974
      ]
    ]
  }
}
```

### sitemap

```json
{
  "coverage": {
    "by_month": [],
    "site_sum_of_months": 0,
    "extracted_total": 4307,
    "by_strategy": [
      {
        "via": "iran_listing",
        "requests": 3820,
        "iran": 3818,
        "iran_per_100": 99.95
      },
      {
        "via": "same_advertiser",
        "requests": 580,
        "iran": 476,
        "iran_per_100": 82.07
      },
      {
        "via": "sitemap",
        "requests": 3920,
        "iran": 13,
        "iran_per_100": 0.33
      },
      {
        "via": "listing",
        "requests": 25,
        "iran": 0,
        "iran_per_100": 0.0
      },
      {
        "via": "advertiser",
        "requests": 126,
        "iran": 0,
        "iran_per_100": 0.0
      }
    ],
    "skipped_by_id_time": 0
  },
  "yield_plan": {
    "pending_dated": 744510,
    "after_cutoff_by_id": 0,
    "before_iran_by_id": 0,
    "month_order": [
      [
        "2026-04",
        0.0,
        23847
      ],
      [
        "2026-05",
        0.0,
        43583
      ],
      [
        "2026-06",
        0.0,
        117078
      ],
      [
        "2026-07",
        0.0,
        117165
      ],
      [
        "2026-08",
        0.0,
        122667
      ],
      [
        "2026-09",
        0.0,
        191627
      ],
      [
        "2026-10",
        0.0,
        93569
      ],
      [
        "2026-11",
        0.0,
        34974
      ]
    ]
  }
}
```
