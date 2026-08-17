# Session 7.7 — Offline Outcome Follow-up — Implementation Report

## Summary

Phase 7.7 is an **offline Outcome Follow-up** engine. It documents observed
before-and-after performance **after** an owner-recorded manual action, by
comparing accepted Phase 7.6 tracker records against a later accepted Phase 7.3
offline analysis. It is **measurement and documentation only**. It never claims
the owner-recorded action caused any result, never connects to Amazon, and never
performs an Amazon action. All Amazon and external-network counters remain zero.

Real T2 is expected to be empty (the accepted Phase 7.5 package is empty, so the
Phase 7.6 tracker holds zero records). The engine returns
`SESSION7_7_FOLLOWUP_READY_EMPTY`, exit 0, with all required output files
generated and all counters at zero.

## Git

| Item | Value |
| --- | --- |
| Branch | `phase7-7-offline-outcome-followup` |
| Baseline | `c728f128dd693e923103c5b92a31dd17d2a1ffe0` |
| Checkpoint tag | `phase7-7-outcome-followup-checkpoint-c728f12` (→ `c728f12`) |
| Implementation commit (commit 1, feat) | `e663b6238453ea548740033349c8e42a572129d4` |
| Proof commit (commit 2, docs) | self — this docs commit is the proof commit; its hash is reported in the session summary |
| origin/main | `c728f128dd693e923103c5b92a31dd17d2a1ffe0` (unchanged; NOT merged) |

### Accepted prior tags (all present, unchanged)

- `phase7-2-cumulative-accepted-d5ad841` → `91e2607`
- `phase7-3-accepted-7005275` → `b9d2755`
- `phase7-4-owner-dashboard-accepted-eebecc5` → `7704277`
- `phase7-5-owner-decision-package-accepted-66d972d` → `02b7a81`
- `phase7-6-manual-action-tracker-accepted-f1d11d8` → `af9b7f6`

## Source authorities (inspected as ground truth; not assumed from the prompt)

- `production/phase7_manual_action_tracker.py` — Phase 7.6 authority. Reused
  directly: `load_state` (validates the append-only, hash-chained history + the
  current-state self-hash), `binding_state` / `_STALE_BINDINGS` (package-lineage
  drift), `load_package`, the owner statuses (`S_COMPLETED == "MANUALLY_COMPLETED"`,
  `S_REVERTED`, …), `tracker_record_id`, `state_paths`.
- `production/phase7_owner_decision_package.py` — Phase 7.5 authority (package
  manifest / checklist schema constants; item identity fields).
- `production/phase7_ads_analysis.py` — Phase 7.3 analysis contract. The read
  source is `promoted/analysis.json` → `search_term_analysis` rows (per-row,
  per-date, with `campaign` / `ad_group` / `targeting` / `customer_search_term` /
  `match_type` / `currency`, `start_date` / `end_date`, `canonical_row_key` with
  marketplace, `evidence.metric_states` attribution-window suffixes, and Decimal
  string metrics). Integrity is verified against `promoted/analysis-manifest.json`
  (`phase7-3-analysis-manifest-v1`) `output_hashes`; the manifest
  `deterministic_content_sha256` is recorded as the source identity.
- `production/phase7_owner_dashboard.py` — reused `_tsv_cell` (formula-injection
  rule) and `MAX_ARTIFACT_BYTES`.
- `production/product_workspace.py` — reused `canonical_json` / `content_sha256`.
- `core/money.py` — the single Decimal money authority (`parse_decimal_string`,
  `sum_required_decimals`, `safe_divide`, `serialize_currency` / `serialize_rate`).

## Files created

- `production/phase7_outcome_followup.py` (the ONE Phase 7.7 authority)
- `tests/test_phase7_7_outcome_followup.py` (93 tests)
- `SESSION7_7-OUTCOME-FOLLOWUP-IMPLEMENTATION-REPORT.md`
- `SESSION7_7-OUTCOME-FOLLOWUP-PROOF-GATE.json`

## Files modified

None. Phase 7.2 / 7.3 / 7.4 / 7.5 / 7.6 sources are untouched.

## Model (accepted repository convention)

There is exactly one Phase 7.3 `promoted/` directory and Phase 7.2 ingestion is
**cumulative** (it preserves prior promoted rows). A "later report" therefore
lives in the **same** cumulative analysis as the earlier one. Phase 7.7 splits
that single source into a **before window** and an **after window** using
**explicit, owner-supplied date windows**, aggregates the matched entity's rows
in each window, and documents the observed change. No later analytical snapshot
is stored elsewhere, so none is invented.

## Follow-up schema (`phase7-7-followup-record-v1`)

`followup_record_id`, `tracker_record_id`, `tracker_record_content_sha256`,
`package_id`, `package_item_id`, `source_recommendation_id`, `recommendation_type`,
`entity_type`, `owner_status`, `owner_completed_date`, `entity_identity`
(campaign / ad_group / targeting / customer_search_term / match_type),
`marketplace`, `currency`, `attribution_window`, `before_period`, `after_period`,
`before_row_count`, `after_row_count`, `before_metrics`, `after_metrics`,
`absolute_deltas`, `percentage_deltas`, `outcome_classification`, `outcome_reason`,
`outcome_detail`, `outcome_statement`, `confidence_status`, `evidence_status`,
`binding_state`, `source_identity_sha256`, `duplicate_of_count`,
`duplicate_tracker_record_ids`, plus the per-record `followup_content_sha256`.

## Eligibility model

A record becomes a documented follow-up only when: Phase 7.6 state + history chain
validate (reused from 7.6); the package binding is `CURRENT` (not stale); the owner
status is `MANUALLY_COMPLETED`; `owner_completed_date` is present; the entity
identity is complete and matches exactly one marketplace / currency / attribution
window in the current Phase 7.3; and the after window is ready. Every other status
(`PENDING_MANUAL_CHECK`, `MANUALLY_SKIPPED`, `NO_LONGER_RELEVANT`, `NEEDS_REVIEW`,
`BLOCKED_BY_CURRENT_STATE`, `UNABLE_TO_VERIFY`) is excluded. `REVERTED_MANUALLY`
is handled as a **separate lifecycle event** (its own `reverted_records` list),
never merged with a completion. No owner action is inferred without a valid Phase
7.6 record.

## Entity matching

Match key = `campaign, ad_group, targeting, customer_search_term, match_type`
(display-name-independent; no row number / sort order / temporary id / uuid /
timestamp). Currency, attribution window, and marketplace are validated as
consistency dimensions **on the matched set** (never part of the key), so a
multi-currency / multi-attribution-window / multi-marketplace identity is caught
(`CURRENCY_MISMATCH` / `ATTRIBUTION_WINDOW_MISMATCH` / `AMBIGUOUS_ENTITY_MATCH`)
and never silently collapsed. Ambiguous matches are excluded — no best-guess.

## Window model

Explicit fields: `reference_date`, `before_period_start/end`, `after_period_start/end`,
`minimum_followup_days` (default 0, owner-configurable), `attribution_window`,
`currency`. The reference date must be supplied explicitly and is never defaulted
to the current date. Rejected as `SESSION7_7_WINDOW_NOT_READY`: invalid dates,
reversed windows, overlapping windows (before must end strictly before after), and
an after window ending later than the reference date. Per record, an after window
opening fewer than `minimum_followup_days` after the action is `WINDOW_NOT_READY`.

## Metric model

Aggregated per window with `core.money` Decimal only (no float). Counts
(impressions / clicks / orders / units) and money (spend / sales) are summed only
when every contributing row has the value present; a missing value keeps the
aggregate **missing** (never coerced to zero). Ratios (cpc / ctr / conversion_rate /
acos / roas) are recomputed from the summed bases via `safe_divide` (a zero or
missing denominator yields missing, never a fabricated value and never infinity).
Absolute deltas are exact; percentage deltas are undefined (`null`) when the before
value is zero. Values are serialized as exact strings (currency 2 dp, rates 6 dp)
or integers.

## Classification model (observation only)

`OBSERVED_IMPROVEMENT`, `OBSERVED_DECLINE`, `OBSERVED_MIXED`,
`OBSERVED_NO_MATERIAL_CHANGE`, `INSUFFICIENT_DATA` (documented follow-ups), plus the
exclusion classes `ENTITY_NOT_FOUND`, `AMBIGUOUS_ENTITY_MATCH`, `WINDOW_NOT_READY`,
`SOURCE_CHANGED`, `PACKAGE_CHANGED`, `TRACKER_STATE_CHANGED`, `CURRENCY_MISMATCH`,
`ATTRIBUTION_WINDOW_MISMATCH`, `FOLLOWUP_CONFLICT`, `NOT_ELIGIBLE_STATUS`,
`REVERTED_SEPARATE`, `MISSING_IDENTITY`. Thresholds come from an explicit policy:
the built-in `phase7-7-outcome-policy-v1` is a labelled
`NEUTRAL_DEFAULT_OWNER_CONFIGURABLE` default (material change ratio `0.10`), whose
source is recorded in every output; a policy that sets `material_change_ratio` to
null yields `SESSION7_7_POLICY_REQUIRED` rather than an invented number. Confidence
is a **data-sufficiency** label only (`SUFFICIENT_OBSERVATION` /
`LIMITED_OBSERVATION` / `INSUFFICIENT_OBSERVATION`) — there is no causal-confidence
score.

## Causation disclaimer

Every relevant output prominently states `OFFLINE OBSERVATIONAL FOLLOW-UP`, that no
Amazon connection was made and no Amazon action was performed by this software, and
that the recorded change is observational and does **not** establish that the
owner-recorded action caused the result — listing other factors (seasonality,
pricing, competition, inventory, listing changes, advertising changes made outside
the tracker, reporting delays, attribution timing, marketplace conditions). Outcome
statements use only observational phrasing ("Observed after the owner-recorded
manual action.", "Performance changed during the follow-up window.", "Insufficient
evidence to determine an outcome."). A test asserts none of the banned causal
sentences appear.

## Stable identity

`followup_record_id = "fu-" + SHA256(canonical({tracker_record_id,
tracker_record_content_sha256, entity_identity, before_period, after_period,
currency, attribution_window, source_identity_sha256}))[:32]`.
`followup_package_id = "followup-" + SHA256(canonical({windows, reference_date,
minimum_followup_days, policy, source identity, tracker state hash, sorted
follow-up content hashes + ids, sorted exclusion / reverted ids}))[:16]`.
Canonical JSON + SHA-256; no timestamps, random ids, or paths.

## Duplicate handling

Canonical follow-up identity = entity identity + before/after periods + currency +
attribution window + marketplace. Identical duplicates collapse to one
(`duplicate_identical_count`++, lineage preserved in `duplicate_tracker_record_ids`).
Conflicting duplicates (same canonical identity, different observation content —
e.g. a different owner completion date) exclude **all** members
(`FOLLOWUP_CONFLICT`, `duplicate_conflict_count`++, no last-write-wins). Records are
never collapsed across different currencies, attribution windows, campaigns, ad
groups, targets, or before/after periods.

## Atomic writes

Validate all sources → validate the tracker chain → build the full in-memory model
→ render all artifacts → hash them → write the manifest → write to a temporary
directory → read-back-verify every artifact → fsync (files and directory,
best-effort) → `os.replace` the temp directory to the final directory. On failure
the temp directory is removed and any previous valid output is left intact.

## Idempotency

Content-addressed package ids. A repeated identical run reports `IDEMPOTENT_REUSE`
and does not rewrite. If the same package id exists with different bytes the run is
blocked (`SESSION7_7_FOLLOWUP_BLOCKED`) — never overwritten.

## Determinism

Identical Phase 7.3 source + Phase 7.6 state/history + reference date + windows +
policy produce byte-identical authoritative files. Sorted records, canonical JSON,
UTF-8, explicit `\n`, Decimal, SHA-256; no runtime timestamps, locale, filesystem
ordering, dict-ordering, random ids, temp paths, or float formatting in
authoritative output. Verified on Windows across LF and CRLF checkouts (fresh
worktree).

## Readiness states

`SESSION7_7_FOLLOWUP_READY`, `SESSION7_7_FOLLOWUP_READY_EMPTY` (exit 0);
`SESSION7_7_TRACKER_REQUIRED`, `SESSION7_7_TRACKER_BLOCKED`,
`SESSION7_7_SOURCE_REQUIRED`, `SESSION7_7_SOURCE_BLOCKED`,
`SESSION7_7_WINDOW_NOT_READY`, `SESSION7_7_POLICY_REQUIRED`,
`SESSION7_7_ENTITY_MATCH_REQUIRED`, `SESSION7_7_FOLLOWUP_CONFLICT`,
`SESSION7_7_FOLLOWUP_BLOCKED` (nonzero).

## CLI

```
python -m production.phase7_outcome_followup \
  --base-dir "runs/T2/phase7/7.7" \
  --phase7-3-dir "runs/T2/phase7/7.3" \
  --phase7-6-dir "runs/T2/phase7/7.6" \
  --phase7-5-dir "runs/T2/phase7/7.5" \
  --reference-date "2026-07-22" \
  --before-start "2026-06-01" --before-end "2026-06-30" \
  --after-start "2026-07-01" --after-end "2026-07-21"
```

Options: `--validate-only` (creates no package), `--minimum-followup-days`,
`--policy-file`, `--require-source-lineage-match`, `--followup-id`, `--format`.
`--phase7-5-dir` defaults to a sibling `7.5` of the Phase 7.6 workspace. Default
behaviour is safe.

## Core outputs (`runs/T2/phase7/7.7/followups/followup-<id>/`)

`OWNER_READ_FIRST.md`, `executive_summary.md`, `outcome_details.md`,
`outcome_status.tsv`, `outcome_status.json`, `excluded_followups.tsv`,
`excluded_followups.json`, `source_lineage.json`, `followup_manifest.json`. A tiny
per-package index is written under `manifests/`. No Amazon bulk-upload files, no
Ads/SP-API payloads, no browser automation, no mutation scripts.

## Test counts

- Phase 7.7 focused: **93 tests, OK** (`tests/test_phase7_7_outcome_followup.py`).
- Prior focused (7.2 + 7.3 + 7.4 + 7.5 + 7.6): **797 tests, OK** (skipped 1).
- Baseline full suite (at `c728f12`): **2898 tests, OK** (skipped 2).
- Full suite with Phase 7.7: **2991 tests, OK** (skipped 2, 0 fail) — exactly
  2898 + 93, zero regressions.

## Compile result

`python -m compileall production core tests` → exit 0.

## Synthetic validations (all passing)

Empty tracker → `FOLLOWUP_READY_EMPTY`; populated → `FOLLOWUP_READY` with the
observed classification; ambiguous entity, currency mismatch, attribution mismatch,
duplicate conflict, and insufficient-data cases each excluded/classified correctly;
formula-injection (`= + - @` tab CR LF) neutralized while genuine negative decimals
(`-2.50`) and Vietnamese Unicode are preserved; determinism verified across two runs
and across a different upstream clock; idempotent reuse verified; validate-only
writes nothing.

## Real-T2 result

- Phase 7.3 source: `source_rows=114`, `analyzed_rows=114`.
- Phase 7.5 package: empty (`pkg-3cf372628abc6082`, `SESSION7_5_PACKAGE_READY_EMPTY`).
- Phase 7.6 tracker (generated with the accepted tracker `initialize`): zero records,
  `SESSION7_6_TRACKER_READY_EMPTY`.
- Phase 7.7: `SESSION7_7_FOLLOWUP_READY_EMPTY`, exit 0, follow-up package id
  `followup-ae48aea7a80654ca`; zero eligible follow-ups, zero fake actions, zero
  inferred completions; all required output files generated with headers and
  disclaimers. Deterministic across two runs (byte-identical, same id).

## Source immutability

Phase 7.3 `promoted/`, Phase 7.5 `packages/`, and Phase 7.6 `action_state/` (state
and history) tree hashes are unchanged before and after a real-T2 run. Phase 7.7
writes only under its own workspace (`runs/T2/phase7/7.7/`); no locks, caches, logs,
temp files, or exports are created inside any input directory.

## runs/ tracking

`runs/` is git-ignored (`git check-ignore runs/T2/phase7/7.7` confirms). No runtime
data is committed. `git status` after the run shows only the tracked source/docs.

## Prohibited integrations

The module imports only `argparse, datetime, hashlib, json, os, re, shutil, sys`,
`core.money`, and the accepted Phase 7.x modules. No `requests` / `httpx` / `aiohttp`
/ `urllib` / `socket` / `boto3` / `botocore` / `selenium` / `playwright` /
`webdriver` / `subprocess` / `pickle` / `marshal`; no `os.system` / `eval` / `exec`
/ `__import__`; no `float(`; no credentials / tokens / cookies / sessions / webhooks
/ telemetry / external URLs / Amazon clients. The only strings matching those words
are the boundary counters that PROVE they are zero. AST-based tests assert this.

## Amazon counters / external-network counter

All Amazon counters (connections, SP-API, Ads API, mutations, report downloads,
bulk uploads, API payloads, browser automation, credential/cookie/token/session
stores, subprocess-from-data) and the external-network counter remain **zero** in
every result and in the follow-up manifest. `amazon_action_performed=false`,
`causation_asserted=false`.

## Known limitations

- Both before and after metrics are drawn from the single cumulative Phase 7.3
  `promoted/` analysis, sliced by explicit date windows (the accepted repository
  convention). If a genuinely separate later analytical snapshot convention is ever
  introduced, the source loader would need to point at it.
- `SOURCE_CHANGED` fires only under `--require-source-lineage-match` (off by
  default), because a cumulative source is expected to advance between the package
  and the follow-up; strict owners can opt in to pin the source lineage.
- Attribution windows and marketplaces are derived from Phase 7.3 fields
  (`evidence.metric_states` suffixes; `canonical_row_key` `mk=`); an entity whose
  Phase 7.3 rows omit those fields is treated as window/marketplace `null`.
- The observed classification is intentionally conservative and observational; it
  is never a causal claim.

## Exact next step

Recommend an **independent acceptance audit** of commit
`e663b6238453ea548740033349c8e42a572129d4` and the proof documentation. Do **not**
merge into `main`, do **not** create an acceptance tag, and do **not** begin Phase
7.8 until the audit is complete.
