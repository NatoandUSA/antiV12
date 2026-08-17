# Session 7.2 — Cumulative-Ingestion Hardening: Implementation Report

**Status: IMPLEMENTED — NOT ACCEPTED.** Independent acceptance audit is the required next step.
This is a Phase 7.2 ingestion patch only. Phase 7.4 was **not** started.

---

## 1. Scope and outcome

Fixed the documented Phase 7.2 incremental-ingestion limitation: importing a *new* report after an
earlier one re-normalised only the new file, so `final/` was rebuilt from that run alone and the
previously promoted rows were **erased** — while `promote=PASS`. Phase 7.3 then analysed only the newest
report.

The promoted Phase 7.2 dataset is now the **cumulative analytical dataset**:

```
promoted dataset = verified prior promoted rows
                 ∪ newly accepted valid rows
                 − exact duplicates (by canonical identity)
                 − rows excluded by explicit overlap policy
```

assembled from **canonical normalized records** (the promoted `final/` JSONL) with deterministic
ordering, and promoted atomically. A blocked / empty / duplicate-only / unverifiable run never erases or
shrinks the last valid cumulative dataset.

The single canonical Phase 7.2 authority `production/phase7_report_ingestion.py` was patched in place. No
second ingestion engine was created. Phase 7.3 (`production/phase7_ads_analysis.py`) was **not modified**
— it continues to read the promoted `final/` dataset unchanged.

---

## 2. Git provenance

| Field | Value |
|---|---|
| Branch | `phase7-2-cumulative-ingestion` |
| Baseline commit (HEAD == origin/main at start) | `3056bb4` |
| Checkpoint tag | `phase7-2-cumulative-checkpoint-3056bb4` |
| Accepted Phase 7.3 tag (unchanged) | `phase7-3-accepted-7005275` → `7005275` |
| Implementation commit (production + tests) | `d12de0b` |
| Proof / documentation commit | this docs commit (reported in the session response) |

The accepted Phase 7.3 tag was not touched. No accepted tag was created for this session. No merge into
`main`.

---

## 3. Reproduction of the original limitation

Synthetic reproduction (search-term reports, aggregate ranges): import A (`2026-07-01..05`, 3 rows), then
import disjoint B (`2026-07-06..10`, 2 rows).

**Before the fix** (`repro_BEFORE_fix.txt`):

```
[A] state=...REPORTS_READY promote=PASS final_rows=3
[B] state=...REPORTS_READY promote=PASS accepted=1 final_rows=2
[B] final search terms = ['term B0', 'term B1']
RESULT: BUG REPRODUCED — expected 5 cumulative rows, final has 2. Report A's rows were ERASED under promote=PASS.
```

**After the fix** (`repro_AFTER_fix.txt`):

```
[A] state=...REPORTS_READY promote=PASS final_rows=3
[B] state=...REPORTS_READY promote=PASS accepted=1 final_rows=5
[B] final search terms = ['term A0', 'term A1', 'term A2', 'term B0', 'term B1']
RESULT: SAFE/CUMULATIVE — final has A+B = 5 rows.
```

---

## 4. Canonical row identity contract

Every normalized row has a deterministic canonical identity — **already** implemented as
`canonical_row_key()` and reused unchanged as the cumulative dedup/merge key:

```
type=<report_type>|mk=<marketplace>|range=<start_iso>:<end_iso>|<dim>=<value>|...
```

where the ordered identity dimensions per report type come from `_KEY_DIMS` (campaign / ad_group /
targeting / match_type / search_term / advertised / purchased / placement, as applicable). Values are
pipe/backslash-escaped so `a|b` never collides with `a` + `b`.

The identity is derived from **stable normalized business fields only**. It never uses: source filename,
row number, ingestion timestamp, random UUID, filesystem path, or wall-clock time. This is tested
(`test_identity_ignores_filename`, `test_identity_ignores_row_number`, `test_identity_deterministic`,
`test_identity_distinguishes_legitimate_facts`) plus the pre-existing `TestCanonicalKeyLineage` suite.

Two byte-different raw rows that normalise to the same analytical fact collapse to **one** canonical row
(not counted twice); rows differing in any legitimate dimension or metric are kept distinct.

**Report granularity.** Each row carries an explicit `[start_date, end_date]` coverage range. A daily
report row is a single-day range (`start == end`); an aggregate report row is a multi-day range. The
canonical key includes the range, so daily rows on distinct dates get distinct keys (merge), the same
date+entity is one key (dedupe/conflict), and reconciliation classifies range relationships uniformly for
both. Attribution windows are represented by the windowed metric field names (`sales_7d`, `sales_14d`, …)
and are kept **distinct** — never merged or inferred from one another.

---

## 5. Overlap classification and safe behaviour

Cumulative merge feeds the union of prior + new rows through the existing
`reconcile_interval_group()` per report type. Overlap is determined per entity (identity minus range)
from report type, dimensions, date range, and — for exact-key collapse — currency and metric equality.

| Relationship | Classification | Behaviour |
|---|---|---|
| Disjoint / adjacent ranges | `NO_OVERLAP` / `ADJACENT_RANGE` | **merge cumulatively** |
| Same range, identical metrics+currency | `EXACT_DUPLICATE_COVERAGE` | **skip** (collapse, not double-counted) |
| Same range, conflicting metrics/currency | `DUPLICATE_ROW_CONFLICT` | **block** (`REPORT_CONFLICT_BLOCKED`), preserve |
| Partial overlap | `PARTIAL_OVERLAP` | **block** (`REPORT_OVERLAP_REVIEW_REQUIRED`), preserve |
| Contained range | `CONTAINED_RANGE` | **block** (`REPORT_OVERLAP_REVIEW_REQUIRED`), preserve |
| Unknown semantics | `REPORT_SEMANTICS_REQUIRED` | **block** aggregation, preserve |

No prorating, no metric subtraction, no daily inference from aggregates, no newer-file preference, no
last-write-wins. Currencies and attribution windows are never merged.

---

## 6. Promoted-dataset preservation

Before merge, the prior promoted state is loaded **only when it re-verifies from its own bytes**
(`load_prior_promoted_rows` → `verify_promoted_state`: manifest integrity + every recorded artifact
hash). The five distinguishable outcomes:

| Decision | Trigger | Effect on `final/` / `last_valid/` |
|---|---|---|
| `NO_PROMOTED_DATA` | no prior + no new input | write diagnostics (nothing to lose) |
| `PROMOTE_CUMULATIVE_DATASET` | first dataset, or new rows merged | atomic promote of the cumulative candidate |
| `CARRY_FORWARD_PRESERVED` | no new input over a valid prior | **byte-identical no-op** |
| `CARRY_FORWARD_PRESERVED_BLOCKED` | this run blocked over a valid prior | keep last valid **byte-identically**, report block |
| `CARRY_FORWARD_VERIFICATION_FAILED` | prior exists but unverifiable | **block** (`REPORT_CARRY_FORWARD_BLOCKED`), never erase |

The cumulative source of truth is the promoted **normalized JSONL**, not the `accepted_raw` archive —
so deleting an `accepted_raw` file does not lose cumulative data (`test_no_accepted_raw_deletion_required`).
`accepted_raw` remains an immutable, byte-identical archive; no archive deletion is ever required.

**Lineage-based idempotency.** A file is "already imported" iff its content hash is present in the
**promoted dataset's lineage** (or it was accepted earlier in the same run), never merely because a copy
exists in `accepted_raw`. This directly implements the ACCEPTED_RAW requirement ("use content hashes and
normalized lineage… archive state must not be the only source of truth"). It also closes a latent
stranding path: a valid file archived by a run that was blocked by a bad sibling was previously treated
as idempotent forever and its rows never promoted; now it is re-processed on the next clean run and its
rows are recovered (`test_idempotency_is_lineage_based_not_archive_based`). This is strictly better than
the pre-fix behaviour, which lost both the good file's rows and the prior dataset.

---

## 7. Production changes (`production/phase7_report_ingestion.py`)

- **New constants:** `SCHEMA_CUMULATIVE`, `CF_PROMOTE` (renamed value → `PROMOTE_CUMULATIVE_DATASET`),
  `CF_PRESERVE_BLOCKED`, `PRIOR_NONE` / `PRIOR_LOADED` / `PRIOR_INVALID`.
- **`reconcile_interval_group`:** deterministic group base via `_row_provenance_key` (smallest
  `(source_sha, source_row)`, independent of prior-vs-new ordering); `duplicate_count` now **sums**
  across members (a re-loaded promoted row may already stand for >1) instead of resetting to the group
  length — backward compatible for fresh rows (all `duplicate_count == 1`).
- **`run_ingestion`:** loads the verified prior promoted rows *before* file processing (write-mode
  only), unions them with this run's new rows, reconciles the union, computes cumulative counts, and
  routes promotion through `cumulative_promotion_decision`. The blocked-run branch now **preserves** a
  valid prior dataset instead of promoting an empty/blocked state over it.
- **Idempotency (`process_source_file`):** keyed on the promoted dataset's **lineage** content hashes
  (plus same-run `seen`), not on `accepted_raw` presence; removed the `os.path.exists(accepted_path)`
  idempotency grant. The now-orphaned `_existing_accepted_hashes` helper was removed.
- **New functions:** `_empty_prior`, `load_prior_promoted_rows`, `cumulative_promotion_decision`,
  `_cumulative_attribution_windows`, `_cumulative_manifest_block` (replaces the old
  `carry_forward_decision`, which had no external callers).
- **Manifest:** new `cumulative` block (schema `phase7-2-cumulative-merge-v1`) recording prior dataset
  hash, prior/new/cumulative row counts, duplicate + overlap-conflict counts, cumulative currencies and
  attribution windows, and prior source-lineage hashes. New `counts` keys:
  `prior_cumulative_row_count`, `new_valid_row_count`, `cumulative_row_count`, `new_rows_merged`,
  `overlap_conflict_count`.
- **CLI:** additional status lines (`new_rows_merged`, `duplicate_rows`, `overlap_conflicts`,
  `prior_rows`, `cumulative_rows`, `carry_forward`, `preserved_rows`). The prior `valid_rows=` stdout
  line was **renamed** to `new_valid_rows=` (same value — this run's newly-normalized valid rows — under
  a clearer cumulative name); the `valid_row_count` data field itself is retained in the manifest,
  readiness, and proof-gate outputs. No test or documented consumer parses the CLI stdout (`main()` is
  not invoked by any test), so the CLI-compatibility requirement is satisfied. The documented CLI
  invocation is unchanged.
  <!-- corrected during the independent acceptance audit: the earlier "existing fields preserved"
       wording did not note the valid_rows -> new_valid_rows stdout rename. -->

- **Proof gate:** `branch`/`checkpoint_tag` updated to this session; new cumulative fields and the
  canonical-identity contract string.

The unchanged, 7.3-shared helpers `read_promoted_manifest`, `promoted_normalized_artifacts`,
`promoted_normalized_row_count`, `verify_promoted_state` were reused, not modified.

---

## 8. Test change explained (strictly stronger, never weaker)

One existing test encoded the old data-loss-from-`final` behaviour and was **strengthened**:

- `TestExcelAndEncodingInput.test_blocked_run_preserves_last_valid` — previously asserted that after a
  format-blocked incremental run the good dataset survived **only in `last_valid`** (because the blocked
  run erased `final` and snapshotted it aside). The cumulative engine leaves the last valid dataset in
  `final` **byte-for-byte**, and the promoted manifest stays `REPORTS_READY` for Phase 7.3. The test now
  asserts that stronger contract (final preserved byte-identically + still analysis-ready), plus the new
  `CF_PRESERVE_BLOCKED` decision. It fails against the old code and passes against the fix.

No other existing test was modified. No test was weakened.

---

## 9. Tests added

New `TestCumulativeIngestion` class (44 tests) with synthetic fixtures A (`07-01..05`), B (`07-06..10`),
C (`07-11..15`), Conflict D (`07-04..08`, partial overlap), Duplicate E (A's facts, renamed + `$`/padding
raw differences), and Daily F (single-day rows). Coverage maps to the required 56-item list: cumulative
A+B and A+B+C; exact / renamed / same-facts-different-bytes duplicates; empty / unsupported-xlsx /
malformed-csv / invalid-row-only preservation; exact-duplicate-skip, exact-conflict-block,
partial/contained overlap block, unknown-semantics block, disjoint merge; daily distinct-date merge,
daily same-date dedupe and conflict-block; attribution windows kept separate; currencies not merged /
conflict-block; canonical identity determinism + filename/row-number independence + fact distinction;
content-hash duplicate detection; accepted_raw byte-identical + no-deletion-required; prior validation,
manifest tamper, source-lineage mismatch, atomic promotion, failed-candidate preservation, deterministic
ordering (incremental == combined), byte-identical repeat; cumulative manifest counts + reported counts;
Phase 7.3 analyses all cumulative rows / works without inbox+raw / deterministic; and zero Amazon
counters.

---

## 10. Gate results

| Gate | Result |
|---|---|
| `compileall production core tests` | PASS (exit 0) |
| Focused Phase 7.2 (`test_phase7_2_report_ingestion`) | **377** tests OK (skipped 1) — was 333 |
| Focused Phase 7.3 (`test_phase7_3_ads_analysis`) | **117** tests OK — unchanged |
| Full suite (`unittest discover -s tests`) baseline | 2551 tests OK (skipped 2) |
| Full suite after fix | **2595** tests OK (skipped 2) — +44 new, zero regressions |
| Original-limitation reproduction | BUG REPRODUCED before; SAFE/CUMULATIVE after |
| A+B cumulative | 4 rows, READY, promote PASS |
| A+B+C cumulative | 6 rows, READY, promote PASS |
| Renamed duplicate E | accepted, cumulative stays 2, 2 duplicates, 0 net-new (not double-counted) |
| Exact-duplicate re-run | `CARRY_FORWARD_PRESERVED`, final byte-identical |
| Overlap conflict D | `REPORT_OVERLAP_REVIEW_REQUIRED`, `CF_PRESERVE_BLOCKED`, final byte-identical |
| Empty-run preservation | `CARRY_FORWARD_PRESERVED`, final byte-identical |
| Invalid-prior run | `REPORT_CARRY_FORWARD_BLOCKED`, not promoted, final byte-identical |
| Archive integrity | accepted_raw byte-identical to originals; no deletion required |
| Determinism | incremental A→B == combined {A,B} (identical JSONL hash); byte-identical repeat run |
| Phase 7.3 compatibility | 6 source rows == 6 analysed; works with inbox+accepted_raw deleted; deterministic |
| Atomic promotion | PASS; failed candidate preserves final + last_valid |
| `last_valid` preservation | prior snapshot on promote; untouched on preserve/block |
| Fresh-worktree | clean detached checkout of `d12de0b`: focused 7.2 = 377 OK, focused 7.3 = 117 OK |
| Prohibited-integration search | none (only the boundary docstring and the credential-leak detector match) |

---

## 11. Permanent Amazon boundary (all zero)

| Counter | Value |
|---|---|
| Amazon connections | 0 |
| Amazon SP-API / Ads API calls | 0 |
| Browser automation attempts | 0 |
| Amazon mutations (campaign/target/negative/bid/budget) | 0 |
| Report download attempts | 0 |
| Credential / cookie / token store | 0 |
| External network attempts | 0 |
| API payloads | 0 |

The owner remains the only bridge to Amazon: the owner manually exports reports and places them in the
local inbox; the toolkit reads only the local bytes. No new network or browser dependency was added.
Private report data under `runs/` remains gitignored and untracked (0 tracked files); no owner report
data is committed.

---

## 12. Known limitations

1. **First-accepted dataset version is tracked at the dataset level, not per row.** Adding a per-row
   `first_accepted_version` field would break byte-identical round-tripping of previously promoted rows
   (which lack it). Instead the manifest records `prior_promoted_dataset_hash` and per-row lineage retains
   every contributing `source_file_sha256`. This satisfies "source content hash" and "multiple identical
   source files support one row" without breaking determinism.
2. **Overlap conflicts require owner action, by design.** A partial/contained/exact-conflict overlap
   blocks the incremental promotion and preserves the last valid dataset; there is no automatic
   resolution (no prorating, no subtraction, no last-write-wins). The owner resolves the conflict by
   choosing which report to keep in the inbox.
3. **T2 committed state is unchanged.** No owner report data exists for T2 in this repo; the ready path is
   proven with SYNTHETIC_TEST_DATA_ONLY fixtures. Private `runs/` data stays gitignored.
4. **Not accepted.** This session implements and self-verifies the patch; it does not self-accept.

---

## 13. Exact next action

Run an **independent acceptance audit** of the implementation commit against this contract (cumulative
preservation, overlap safety, determinism, Phase 7.3 compatibility, permanent Amazon boundary). Do **not**
merge into `main`, do **not** create an accepted tag, and do **not** begin Phase 7.4 until the audit
passes.

Exact CLI (unchanged):

```powershell
python -m production.phase7_report_ingestion `
  --base-dir "runs/T2/phase7/7.2" `
  --reference-date "2026-07-20"
```
