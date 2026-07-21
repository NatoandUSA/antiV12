# Session 7.6 — Offline Manual Action Tracker — Implementation Report

## Scope

Phase 7.6 adds an **offline Manual Action Tracker** that records, after the owner has manually
checked Amazon Seller Central, what the **owner** did with the decisions produced by Phase 7.5.

It is a local record-keeping layer only. It never connects to Amazon, never logs in to Seller
Central, never uses an Amazon/Ads API, never automates a browser, never changes a bid/budget,
never creates a keyword/negative/target, never pauses a campaign, never uploads anything, and
never claims an action happened unless the owner explicitly records it. It never infers completion
from a Phase 7.5 approval. The owner remains the only manual bridge to Seller Central.

## Pipeline position

```
Manual Amazon report export
  → Phase 7.2 ingestion
  → Phase 7.3 analysis
  → Phase 7.4 owner review
  → Phase 7.5 decision package
  → owner manually acts in Seller Central
  → Phase 7.6 manual action record   ← THIS SESSION
```

## Git coordinates

| Item | Value |
|------|-------|
| Branch | `phase7-6-manual-action-tracker` |
| Baseline | `9767ec2dc8ff628254184236cfc16f531ffb285d` |
| Checkpoint tag | `phase7-6-manual-action-tracker-checkpoint-9767ec2` |
| origin/main | `9767ec2` (unchanged; **not merged**) |

## Files

| File | Status |
|------|--------|
| `production/phase7_manual_action_tracker.py` | created — the one Phase 7.6 authority |
| `tests/test_phase7_6_manual_action_tracker.py` | created — 100 focused tests |
| `SESSION7_6-MANUAL-ACTION-TRACKER-IMPLEMENTATION-REPORT.md` | created — this report |
| `SESSION7_6-MANUAL-ACTION-TRACKER-PROOF-GATE.json` | created — proof gate |

No other tracked files were modified. All owner runtime data stays under gitignored `runs/`.

## Phase 7.5 authority (inspected, not assumed)

The tracker imports the accepted Phase 7.5 module (`production/phase7_owner_decision_package.py`)
and uses its schema constants directly:

- package manifest schema: `phase7-5-package-manifest-v1` (`PKG.MANIFEST_SCHEMA`)
- checklist schema: `phase7-5-manual-action-checklist-v1` (`PKG.CHECKLIST_SCHEMA`)
- package location: `runs/T2/phase7/7.5/packages/<dir>/`
- per-package files: `package_manifest.json` (with `package_id`, `package_content_sha256`,
  `artifact_sha256`, `readiness`), `manual_action_checklist.json` (`{"meta":…, "items":[…]}`),
  and the 7.5 human-readable artifacts.
- per-item identity: `package_item_id` (`item:<hash>`), plus `content_sha256`,
  `source_recommendation_id`, `recommendation_type`, `entity_type`, and market-identity fields.

A tracker record can only reference a valid `package_item_id` from a valid Phase 7.5 package. An
owner-invented Amazon action with **no Phase 7.5 lineage is refused** (`NO_PACKAGE_LINEAGE`).

The real accepted T2 package `pkg-3cf372628abc6082` is `SESSION7_5_PACKAGE_READY_EMPTY` (0 items).

## Workspace

```
runs/T2/phase7/7.6/
  action_state/current.json     ← latest revision of every record (authoritative)
  action_state/history.jsonl    ← append-only, hash-chained revision log
  exports/                      ← local human-readable exports
  validation/
  runtime/
  logs/
```

The tracker writes only under its own 7.6 workspace and never touches any Phase 7.5 artifact.

## Tracker record model

Each record carries: `tracker_record_id`, `package_id`, `package_dir_name`,
`package_manifest_sha256`, `package_content_sha256`, `package_item_id`, `source_recommendation_id`,
`entity_type`, `recommendation_type`, `item_content_sha256`, `source_snapshot`, `reference_date`,
`owner_status`, `owner_checked_date`, `owner_completed_date`, `owner_note`, `before_value`,
`after_value`, `before_value_currency`, `after_value_currency`, `manually_verified_campaign`,
`manually_verified_ad_group`, `manually_verified_target_or_term`,
`confirmed_manual_seller_central_check`, `confirmed_owner_performed_action`, `evidence_reference`,
`evidence_sha256`, `local_revision`, `previous_record_hash`, `created_at`, `updated_at`,
`record_content_sha256`.

`created_at`/`updated_at` are runtime-only and are **excluded** from `record_content_sha256` and
from the stable identity. No Amazon credentials/cookies/tokens/sessions/URLs are ever stored.

## Owner statuses

`PENDING_MANUAL_CHECK` (default), `MANUALLY_COMPLETED`, `MANUALLY_SKIPPED`, `NO_LONGER_RELEVANT`,
`NEEDS_REVIEW`, `BLOCKED_BY_CURRENT_STATE`, `UNABLE_TO_VERIFY`, `REVERTED_MANUALLY`. Only the owner
sets these. Initializing from an APPROVED package item creates a `PENDING_MANUAL_CHECK` record —
never `MANUALLY_COMPLETED`.

## MANUALLY_COMPLETED confirmation gate

A record may become `MANUALLY_COMPLETED` **only** when the owner supplies, for that update:
`--confirmed-manual-seller-central-check`, `--confirmed-owner-performed-action`, a valid
`owner_checked_date`, a valid `owner_completed_date`, and an owner note or a manually entered
after_value — and the package binding is still `CURRENT`. Any missing element →
`SESSION7_6_CONFIRMATION_REQUIRED` (exit 2) with precise reason codes. Completing against a changed
package → `SESSION7_6_PACKAGE_CHANGED_REVIEW_REQUIRED` (exit 2). Generated text says exactly
**"Owner recorded this action as manually completed."** — never "Amazon confirmed completion".

## Before/after values

Owner-entered observations are preserved verbatim (exact strings; `"-2.50"`, `"8.00"` are kept as
typed, never normalized, never floated). A monetary-looking value requires an explicit currency;
NaN/Infinity is rejected via `core.money`. No bid/budget is ever computed and no recommendation is
issued in Phase 7.6.

## Stable identity

`tracker_record_id = "trk-" + SHA256(canonical({package_id, package_item_id}))[:32]` — a pure
function of package id + package item id. It never depends on row number, timestamp, random UUID,
display order, or filename.

## Append-only hash-chained history

Each update: (1) fully validates the existing current state and history chain; (2) increments
`local_revision`; (3) sets `previous_record_hash` to the prior revision's `record_content_sha256`;
(4) appends a new event to `history.jsonl`; (5) atomically republishes `current.json`. The loader
detects and blocks: broken previous-hash links, missing entries / sequence gaps, duplicate
revisions, revision rollback, rewritten history (content hash re-verified), corrupted current
state, and current↔history mismatch. No last-write-wins.

## Package-change handling

Each record is re-evaluated against its bound package on every read. Detected states:
`PACKAGE_CHANGED`, `PACKAGE_ITEM_CHANGED`, `PACKAGE_ITEM_ABSENT`, `PACKAGE_MANIFEST_MISMATCH`,
`PACKAGE_INTEGRITY_BLOCKED` (integrity via the manifest's `artifact_sha256`). A completed record
tied to a now-changed package **remains in history** and is **visibly marked stale** in `list`,
`validate` (→ `PACKAGE_CHANGED_REVIEW_REQUIRED`), and exports. Historical completions are never
deleted.

## Atomic writes + concurrency

Both temp files (`history.jsonl.tmp`, `current.json.tmp`) are written and fsynced before either
`os.replace`, so a failure before the replaces preserves the previous files intact (no partial
update, no revision gap). Concurrency is a compare-and-swap on the observed
`state_content_sha256`: the update re-reads `current.json` immediately before committing and aborts
with `SESSION7_6_CONCURRENT_MODIFICATION` if it changed; explicit `--expected-state-sha256` /
`--expected-record-hash` guards are also honored. No last-write-wins; both files are preserved on
detection.

## Determinism

Identical package + state + history + owner input + reference date ⇒ identical authoritative next
record and byte-identical exports (verified across two different wall clocks). Canonical JSON
(sorted keys, UTF-8, explicit newlines), SHA-256, Decimal/exact strings; no random UUID, no locale
or filesystem-ordering dependence, no float formatting.

## Exports (local only)

`exports/manual_action_status.tsv`, `manual_action_status.json`, `manual_action_history.md`. Each
prominently states **LOCAL OWNER-ENTERED TRACKER / No Amazon connection was made / No action was
performed by this software / Status values represent owner-entered records only**. Exports are not
Amazon upload files (no bulk template, no API payload). TSV cells reuse the accepted Phase 7.4/7.5
formula-injection rule (neutralizes `= + - @` tab/CR/LF; preserves legitimate negatives like
`-2.50`; equal columns; Vietnamese Unicode preserved).

## Readiness states

`SESSION7_6_TRACKER_READY`, `…_TRACKER_READY_EMPTY`, `…_PACKAGE_REQUIRED`, `…_PACKAGE_BLOCKED`,
`…_STATE_REQUIRED`, `…_STATE_BLOCKED`, `…_HISTORY_BLOCKED`, `…_PACKAGE_CHANGED_REVIEW_REQUIRED`,
`…_CONFIRMATION_REQUIRED`, `…_VALIDATION_READY`, plus `…_UPDATE_REJECTED` and
`…_CONCURRENT_MODIFICATION`. Only READY / READY_EMPTY / VALIDATION_READY exit 0; every blocked
state exits 2.

## CLI

`python -m production.phase7_manual_action_tracker --base-dir … --phase7-5-dir … --reference-date …
{list|show|initialize|update|history|validate|export}`. Missing confirmation flags block
`MANUALLY_COMPLETED`; default behavior is safe.

## Real T2 empty-package result

`initialize --package-id pkg-3cf372628abc6082` → `SESSION7_6_TRACKER_READY_EMPTY`, **0 records**,
exit 0. `validate` → `SESSION7_6_VALIDATION_READY` (exit 0). `export` writes headers + disclaimers
(exit 0). No fake pending actions, no inferred owner action. The real Phase 7.5 package tree is
byte-identical before and after (immutability verified).

## Verification summary

- Phase 7.6 focused: **100 / 100 pass**.
- Prior focused (7.5 / 7.4 / 7.3 / 7.2): pass (see proof gate).
- Full suite: pass (see proof gate).
- `compileall` production + core + tests: exit 0.
- Prohibited-integration AST scan: only stdlib + internal imports; no eval/exec/subprocess/network.
- Amazon counters and external-network counter: all zero.
- Phase 7.5 package tree: immutable (before == after).
- `runs/` remains untracked.
- Determinism: exports byte-identical across two clocks.

## Known limitations

1. The history↔current commit uses two `os.replace` calls (history then current, per spec). A crash
   in the sub-millisecond window between them is **detected** on the next load (current↔history
   mismatch → `HISTORY_BLOCKED`/`STATE_BLOCKED`), never silently accepted; recovery is a manual
   reconcile. No data is lost.
2. Concurrency safety is compare-and-swap (atomic `os.replace` + state-hash re-read), not an OS
   advisory lock — deliberately, to avoid stale-lock deadlock for a solo operator. It prevents
   silent last-write-wins across processes; it does not serialize genuinely simultaneous writers
   beyond CAS retry.
3. `PACKAGE_MANIFEST_MISMATCH` is the weakest drift signal (manifest bytes differ while content and
   items match); it is reported for owner review, not treated as corruption.
4. The browser/visual confirmation of exports remains an owner step (this is a CLI/file tool).

## Exact next action

Recommend an **independent acceptance audit** of commit range on
`phase7-6-manual-action-tracker`. Do not merge to main, do not create an accepted tag, do not begin
Phase 7.7 until the audit passes.
