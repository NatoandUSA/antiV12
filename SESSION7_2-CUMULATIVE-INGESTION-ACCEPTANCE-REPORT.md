# Session 7.2 — Cumulative-Ingestion: Independent Acceptance Audit

**Decision: `PHASE7_2_CUMULATIVE_ACCEPTED_WITH_DOCUMENTATION_FIX`.**

Every material claim was reproduced independently (fresh audit harness, old-vs-new code, fresh worktree)
— the implementation report was not trusted. One minor documentation inaccuracy was found and corrected
in the same commit; it is not acceptance-blocking. No production code was changed by this audit.

Do **not** merge into `main`. Do **not** begin Phase 7.4.

---

## 1. Git state (verified)

| Item | Expected | Observed | OK |
|---|---|---|---|
| Branch | phase7-2-cumulative-ingestion | phase7-2-cumulative-ingestion | ✅ |
| HEAD (before this commit) | aad5073 | aad5073 | ✅ |
| origin branch HEAD | aad5073 | aad5073 (in sync) | ✅ |
| Worktree | clean | clean | ✅ |
| Baseline (d12de0b parent) | 3056bb4 | 3056bb4 | ✅ |
| Checkpoint tag | phase7-2-cumulative-checkpoint-3056bb4 | → 3056bb4 | ✅ |
| Phase 7.3 accepted tag | phase7-3-accepted-7005275 | → 7005275 (unchanged) | ✅ |
| main | 3056bb4 | 3056bb4 (unchanged) | ✅ |
| Commits on branch | 2 | d12de0b (impl) + aad5073 (docs) | ✅ |

---

## 2. Diff review (independent)

- **`d12de0b..aad5073` is docs-only** (the two SESSION7_2 files, +430 lines, zero code).
- **`3056bb4..d12de0b`** touches only `production/phase7_report_ingestion.py` and
  `tests/test_phase7_2_report_ingestion.py`.
- **`production/phase7_ads_analysis.py` (Phase 7.3) is BYTE-IDENTICAL to baseline** — no 7.3 code change.
- Production logic read in full and judged sound: prior loaded before processing and unioned with new
  rows; `reconcile_interval_group` gains deterministic base selection (`_row_provenance_key`) and a
  summed `duplicate_count` (backward compatible for fresh rows, dc=1); `cumulative_promotion_decision`
  routes the five outcomes; idempotency moved onto promoted lineage hashes; manifest `cumulative` block
  added; `_existing_accepted_hashes` orphan removed. No stray edits, no dead code, no defects.
- **Documentation finding (non-blocking):** the CLI stdout line `valid_rows=` was renamed to
  `new_valid_rows=`; the implementation report's "existing fields preserved" wording did not note this.
  No test or documented consumer parses the CLI stdout (`main()` is invoked by no test), and the
  `valid_row_count` data field remains in the manifest/readiness/proof outputs, so the change is safe.
  The report's CLI bullet was corrected in this acceptance commit.

---

## 3. Original bug reproduced (old vs new, independently)

Fresh harness, distinct fixtures (entity "Audit Nurse SP"), Dataset A then disjoint Dataset B:

| Code | after A | after B | result |
|---|---|---|---|
| **Old** (baseline `3056bb4`, run in a detached worktree) | 2 rows | **2 rows** | A **replaced** by B (bug present) |
| **New** (`d12de0b` / HEAD) | 2 rows | **4 rows** | **cumulative** A+B (fixed) |

---

## 4. Required scenarios (30) — all reproduced independently

38/38 checks passed in a from-scratch harness (`audit_harness.py`, fixtures independent of the shipped
tests). Highlights:

| # | Scenario | Result |
|---|---|---|
| 1–3 | A / A+B / A+B+C | 2 / 4 / 6 cumulative rows, READY |
| 4 | duplicate renamed file (identical bytes) | idempotent, 2 rows (no growth) |
| 5 | exact duplicate re-run | CF_PRESERVE, byte-identical final |
| 6 | duplicate raw file (same run) | 1 accepted + 1 idempotent, 2 rows |
| 7 | duplicate normalized row (diff bytes, same facts) | 2 rows, duplicate_row_count=2, net-new=0 |
| 8 | disjoint ranges | merge → 4 rows, 0 conflicts |
| 9 | aggregate overlap | OVERLAP_REVIEW_REQUIRED, preserved |
| 10 | partial overlap | OVERLAP_REVIEW_REQUIRED (2 conflicts), preserved |
| 11 | full overlap (contained) | OVERLAP_REVIEW_REQUIRED, preserved |
| 12 | unknown semantics | REPORT_SEMANTICS_REQUIRED, no rows |
| 13 | empty inbox | CF_PRESERVE, byte-identical |
| 14 | duplicate-only inbox | 0 accepted, byte-identical |
| 15 | invalid CSV | blocked, final preserved |
| 16 | unsupported XLSX | FORMAT_BLOCKED, final preserved |
| 17 | blocked run | CF_PRESERVE_BLOCKED, final byte-identical |
| 18 | candidate failure | not promoted, final + last_valid preserved |
| 19 | promotion failure (missing artifact) | not promoted, final preserved |
| 20 | manifest mismatch | CARRY_FORWARD_BLOCKED, preserved |
| 21 | lineage mismatch (row bytes tampered) | CARRY_FORWARD_BLOCKED, mismatched=[JSONL], preserved |
| 22 | currency mismatch (same key) | CONFLICT_BLOCKED, preserved |
| 23 | different attribution windows | {7d,14d} kept separate, per-row window intact |
| 24 | same attribution window | merges → 4 rows |
| 25 | deterministic repeat | incremental A→B **==** combined {A,B} JSONL; re-run byte-identical |
| 26 | fresh worktree | focused 7.2 = 377 OK, 7.3 = 117 OK, harness 38/38 |
| 27 | accepted_raw removed | still cumulative (4 rows) |
| 28 | inbox removed | Phase 7.3 still analyses (source=4) |
| 29 | Phase 7.3 reads cumulative | source_row_count=4 == cumulative rows, READY |
| 30 | Phase 7.3 analyses every row | analyzed=4 == source=4 |

---

## 5. Core contract (verified)

`existing promoted rows UNION new accepted rows − exact duplicates − overlap-excluded rows`, confirmed:

- **No silent deletion / no shrink:** every blocked/empty/duplicate-only/unverifiable run left `final`
  byte-identical.
- **No overwrite / no last-write-wins:** a conflicting same-key import (`spend 4.00` then `9.99`) blocked
  with `REPORT_CONFLICT_BLOCKED` and the promoted value **stayed 4.00** — the newer value never won.
- **No accepted_raw dependency:** deleting the archive and re-importing still produced the cumulative
  dataset (rows loaded from the promoted JSONL, the source of truth).
- **No filename dependency:** two differently-named files with the same fact collapse to one row.

---

## 6. Canonical row identity (verified)

Deterministic; independent of filename, row number, and ingestion timestamp (byte-identical JSONL across
different `now`); stable across imports; distinguishes real business facts (a new search term yields a
new row). The key string carries no filename or filesystem path
(`type=SP_SEARCH_TERM|mk=US|range=start:end|<ordered dims>`).

---

## 7. Determinism

Incremental import (A then B) produced a **byte-identical** normalized JSONL to importing {A, B}
together; re-running an unchanged inbox left `final` byte-identical (CF_PRESERVE).

---

## 8. Atomic promotion & preservation

Candidate is written then verified; a tampered/incomplete candidate is refused and both `final` and
`last_valid` are preserved byte-for-byte. A blocked run over a valid prior dataset keeps `final`
byte-identical; an unverifiable prior state blocks (`REPORT_CARRY_FORWARD_BLOCKED`) and never erases.

---

## 9. Archive integrity & lineage

`accepted_raw` copies are byte-identical to the inbox originals and named by their own content SHA-256.
Each normalized row's lineage carries source hash, report type, date range, normalization schema version,
canonical key, lineage hash, and contributing source rows; no absolute path leaks into any deterministic
output.

---

## 10. Manifest

Promoted manifest includes the `cumulative` block (schema `phase7-2-cumulative-merge-v1`) with prior
dataset hash, prior/new/cumulative row counts, duplicate + overlap-conflict counts, cumulative currencies
and attribution windows, and prior source-lineage hashes. The manifest re-verifies from its own bytes
(`verify_promoted_state` → PASS), which is exactly the gate Phase 7.3 uses.

---

## 11. Phase 7.3 compatibility

`phase7_ads_analysis.py` is byte-identical to baseline (no code change required). Over an A+B cumulative
7.2 dataset it analyses all rows (source=4, analyzed=4), works with the inbox and `accepted_raw` deleted,
and its decision hash is deterministic across runs.

---

## 12. Security / permanent Amazon boundary

Production module imports only stdlib + internal modules + local `openpyxl` (deferred). No
network/browser/SP-API/Ads-API/credential/token/cookie/session integration. All 13 Amazon-action /
network counters remain zero. `runs/` is gitignored with 0 tracked files; no owner report data is
committed.

---

## 13. Test gates

| Gate | Result | Runtime |
|---|---|---|
| `compileall production core tests` | exit 0 | — |
| Focused Phase 7.2 | 377 tests OK (skipped 1) | ~14 s |
| Focused Phase 7.3 | 117 tests OK | ~5 s |
| Full suite (`unittest discover -s tests`) | **2595 tests OK (skipped 2)** | ~672 s |
| Fresh worktree (aad5073) focused 7.2 + 7.3 | 377 OK + 117 OK | — |

---

## 14. Known limitations (carried, accepted)

1. First-accepted dataset version tracked at the dataset level (`prior_promoted_dataset_hash`), not per
   row, to preserve byte-identical round-tripping of prior rows; per-row lineage still records every
   contributing source hash.
2. Overlap conflicts require owner action by design (no prorating/subtraction/last-write-wins).
3. T2 committed state unchanged; the ready path is proven with synthetic fixtures only; `runs/`
   gitignored.

---

## 15. Acceptance decision

**`PHASE7_2_CUMULATIVE_ACCEPTED_WITH_DOCUMENTATION_FIX`.** The cumulative-ingestion implementation
satisfies its documented contract — cumulative preservation, canonical identity, overlap safety, atomic
promotion, determinism, Phase 7.3 compatibility, and the permanent Amazon boundary — with no regressions
across 2595 tests. The single finding (an imprecise CLI-field wording in the implementation report) was
corrected in this acceptance commit and is not acceptance-blocking.

## 16. Exact next action

Owner may keep this branch for review or open a PR. Do **not** merge into `main` outside the owner's
decision, and do **not** begin Phase 7.4 until explicitly authorized. This audit created one acceptance
commit and one annotated acceptance tag `phase7-2-cumulative-accepted-<short-hash>`, both pushed to the
feature branch; nothing was merged.
