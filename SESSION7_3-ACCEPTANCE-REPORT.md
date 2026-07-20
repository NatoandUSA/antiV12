# Session 7.3 — Independent Combined Acceptance Audit

**Final state: `PHASE7_3_ACCEPTED_WITH_DOCUMENTATION_FIX`**

Independent audit of the Phase 7.2 carry-forward bugfix and the Phase 7.3 offline analysis engine.
Every proof claim was re-run and verified, not trusted. No production defect and no regression were
found. One proof-gate field was mislabeled (a documentation inaccuracy); it is corrected in this
same commit.

---

## 1–7. Identity

| Field | Value |
| --- | --- |
| Audit branch | `phase7-3-offline-ads-analysis` |
| Baseline commit | `d35fa17` (tag `phase7-2-accepted-d35fa17`, annotated, unchanged) |
| Phase 7.2 bugfix commit | `eaffc86` |
| Phase 7.3 feature commit | `9cc4344` |
| Phase 7.3 proof commit | `929bed0` |
| Acceptance commit | *(this commit)* |
| Acceptance tag | `phase7-3-accepted-<short>` (annotated, on the acceptance commit) |

Pre-audit gates: branch correct · tree clean · HEAD `929bed0f6dffd890d02b9024557768a1b47ece1f` ·
origin HEAD matches local · commits in order `eaffc86 → 9cc4344 → 929bed0` · baseline and tag both
resolve to `d35fa17`. All pass.

---

## 8. Phase 7.2 bugfix findings (`d35fa17..eaffc86`)

Diff: `production/phase7_report_ingestion.py` (+115) and its test file (+197/−4). Purely additive to
production: a new state constant, four `CF_*` decision constants, five helper functions
(`read_promoted_manifest`, `promoted_normalized_artifacts`, `promoted_normalized_row_count`,
`verify_promoted_state`, `carry_forward_decision`), and a guard placed **before** the promotion
block in `run_ingestion`. The CF_PROMOTE path falls through to the original, unmodified code.

**Original bug independently reproduced at `d35fa17`** in a fresh worktree: seed 114 rows → final
holds 114; an idempotent re-run leaves `final` with the normalized file **gone** while reporting
`analysis_readiness=SESSION7_2_REPORTS_READY_FOR_ANALYSIS` and `promote=PASS`. Data erased under a
false success — exactly the reported bug.

**Fix verified** across 16 independent scenarios (own harness, not the shipped tests):

| # | Scenario | Result |
| --- | --- | --- |
| 1 | 114 rows, then empty inbox | `CF_PRESERVE`, final byte-identical, 114 kept, not promoted |
| 2 | 114 rows, then unsupported xlsx only | blocked; data preserved in `last_valid` |
| 3 | 114 rows, then malformed CSV | blocked; data preserved in `last_valid`; never falsely READY-with-new-rows |
| 4 | report already in accepted_raw | idempotent; final byte-identical; 114 kept |
| 5 | duplicate import | final byte-identical; 114 kept |
| 6 | zero valid rows (header only) | blocked; data preserved in `last_valid` |
| 7 | no final, no input | `INPUT_REQUIRED`; `CF_NO_PRIOR_DATA` |
| 8 | no final, blocked input | not READY; no phantom rows |
| 9 | last_valid present, blocked run | last_valid not destroyed |
| 10 | final + last_valid valid, blocked run | data still exists |
| 11 | successful new import | promotes; state READY; `CF_PROMOTE` |
| 12 | repeated import (two fresh workspaces) | normalized JSONL byte-identical |
| 13 | archive copy vs inbox original | byte-identical |
| 14 | idempotent re-run | normalized file not emptied; final byte-identical |
| 15 | blocked run | `accepted_source_count=0`, `valid_row_count=0` — no phantom new data |
| 16 | tampered promoted state | `PHASE7_REPORT_CARRY_FORWARD_BLOCKED`; promote BLOCKED; final left as-is, never erased |

Objective **A** (blocked/empty re-run cannot erase promoted data): **met.**
Objective **B** (preserves accepted guarantees): **met** — 332 focused tests pass, the CF_PROMOTE
path is byte-for-byte the pre-fix code, and the `last_valid`-preservation contract holds.

---

## 9. Phase 7.3 feature findings (`eaffc86..9cc4344`) and proof (`9cc4344..929bed0`)

Feature commit adds only `production/phase7_ads_analysis.py` (+1251) and its tests (+1035), all
insertions. Proof commit touches only the two SESSION7_3 docs. Across all three commits only six
files change and **zero `runs/` files are tracked**.

Independently verified (own fixtures + real 7.2 engine, not the shipped tests):

- **Reads only promoted 7.2**: succeeds with `inbox/` and `accepted_raw/` deleted; `assert_source_path_allowed` refuses every forbidden subdir (`inbox`, `processing`, `accepted_raw`, `quarantine`, `candidate`) and allows only `final/`.
- **Source immutability (real T2)**: 11 files under `7.2/final/` + `7.2/manifests/` byte-identical before vs after a 7.3 run. Static analysis: every `open()` on a source path is `"rb"`; there is no append/write/update-mode open anywhere; every write goes through `_atomic_write_bytes` into the 7.3 workspace only. No move/delete/rename touches a 7.2 path.
- **Decimal-only**: `float(` appears zero times; no `float` in the analysis payload; derived CTR/CPC/ACoS/ROAS are exact Decimal strings (`0.025000`, `0.50`, `0.285714`, `3.500000`).
- **Zero denominators → null**: zero clicks → CPC/conversion-rate null; zero sales → ACoS null; zero spend → ROAS null; zero spend + real sales → ACoS a genuine `0.000000` (not null). Never zero-for-null, never null-for-zero.
- **Never merges currencies**: a two-currency source blocks with `PHASE7_3_CURRENCY_MIX_BLOCKED` and writes no output; `aggregate()` keeps one bucket per currency.
- **Never infers attribution**: absent sales → sales null → `NEEDS_OWNER_REVIEW` + `SALES_NOT_REPORTED`, never an inferred window or zero.
- **Deterministic**: two clean workspaces → byte-identical `promoted/` including the manifest; repeated same-workspace run byte-identical with no row duplication.
- **Row traceability**: every row carries `source_file`, `source_file_sha256`, `source_row_number` (=2 for the single data row), `source_line_number`, `canonical_row_key`, `lineage_hash`.
- **Atomic promotion**: a corrupted staged artifact → promotion BLOCKED, prior promoted bytes preserved. (Phase 7.3 uses staging→promoted; the "last_valid on failure" property maps to "prior promoted preserved on failed promotion", which holds.)
- **Review labels only / no Amazon actions**: all 15 boundary counters 0; every `this_session_never` flag true; every label ∈ the five declared review labels; zero imperative action phrases in any promoted artifact; no keyword/negative/bid/budget/campaign mutation; CPC is computed as spend/clicks and labeled `cpc`, never substituted for a bid; no `maximum_cpc`/`max_cpc` recommendation; no title/keyword auto-promotion.
- **Blocked run**: empty source → `PHASE7_3_SOURCE_NOT_READY`, exit 1, no promoted artifacts, log written.

Objectives **C** and **D**: **met.**

---

## 10. Modified accepted-test assessment

`test_no_empty_normalized_claimed` (accepted at `d35fa17`) asserted the normalized file was
**absent** after a re-run — it passed *because of* the bug, encoding data-loss as the contract. The
replacement asserts the file is **present with exactly the original row count** while keeping the
original "no empty artifact for a report type that never had rows" intent. It fails against the old
code and passes against the fix. The change is **justified and strictly stronger.**

---

## 11. Decision-queue precedence assessment

A converting row with 1 click classifies as `PROMISING_LOW_DATA` → `KEEP_MONITORING` and is not
queued. Verified: this follows the explicit `minimum_clicks_for_conversion_judgment` threshold in
`config/analysis-thresholds.json` (10); it is deterministic; it is not a hidden or code-invented
default; it is documented in the report and owner report; and it is safer than emitting a
keyword-promotion review off one click. `decision_queue_rows=0` is **truthful** for the 114-row T2
dataset — independent inspection shows max clicks = 5, one hundred rows at exactly 1 click, and
**zero** rows at ≥10 clicks, so no row can reach a queue label regardless of ACoS. Not a defect.

---

## 12–16. Independent test execution

| Gate | Command | Result |
| --- | --- | --- |
| Compile | `python -m compileall production tests` | exit 0 |
| Focused 7.2 | `pytest tests/test_phase7_2_report_ingestion.py -q` | 332 passed, 1 skipped |
| Focused 7.3 | `pytest tests/test_phase7_3_ads_analysis.py -q` | 117 passed |
| Full suite | `pytest -q` | **2549 passed, 2 skipped** (671s) |
| Targeted independent | own harnesses (16 × 7.2 scenarios, ~40 × 7.3 checks) | all real properties pass |

Baseline `d35fa17` full suite = 2416 passed / 2 skipped. Delta = +16 (7.2 carry-forward) + 117
(7.3) = **+133**, zero regressions.

---

## 17–24. Property results

| Property | Result |
| --- | --- |
| Determinism | PASS — two clean workspaces byte-identical (incl. manifest); on-disk == fresh |
| Source immutability | PASS — 7.2 `final/` + `manifests/` byte-identical before/after (real T2) |
| Archive integrity | PASS — accepted_raw copy byte-identical to inbox original |
| Atomic promotion | PASS — corrupt/missing staged artifact BLOCKS; prior promoted preserved |
| last_valid / prior-promoted preservation | PASS — 7.2 last_valid intact on blocked runs; 7.3 prior promoted intact on failed promotion |
| Fresh-worktree (`929bed0`, no `runs/`) | PASS — compile, 332, 117, blocked run, fixture-backed success (12/12), determinism all green without dev data |
| Prohibited integrations | PASS — 0 executable references (requests/urllib/httpx/aiohttp/boto/socket/selenium/playwright/webdriver/sp_api/subprocess/eval/exec); only `"rb"` reads + one atomic `"wb"`; no dependency manifests changed |
| Amazon action counters | PASS — all 15 zero; all `this_session_never` flags true |

---

## 25. Proof reproduction result

Every reproducible proof claim verified: focused 117, baseline 2416, full 2549, source_rows 114,
analyzed_rows 114, decision_queue_rows 0, blocked_rows 0, raw_inbox_read false, neutral target ACoS,
NULL_NEVER_ZERO, branch/commit hashes, no `runs/` committed, classification/review-label counts.

**One documentation inaccuracy (the acceptance-fix):** the proof field
`deterministic_analysis_content_sha256 = 5e2a4aef…` actually held the **analysis-manifest.json**
hash, not the analysis.json payload hash. Both are independently reproducible and stable
(analysis.json = `97e7a4b1…`, analysis-manifest.json = `5e2a4aef…`). The value was real and
reproducible — only its label was wrong. Corrected in this commit to two clearly-named fields plus a
`deterministic_hash_note`. This is a proof-labeling fix, not a determinism or production defect.

---

## 26. Known limitations

1. **Incremental-add replaces `final` (pre-existing Phase 7.2 behavior, NOT a regression).** Dropping
   a *new* report file when an earlier file is already in `accepted_raw` re-normalizes only the new
   file, so `final` is replaced with just that run's rows and the prior rows move to `last_valid`.
   Independently reproduced identically at `d35fa17` (final=10, last_valid=114), and the fix's
   CF_PROMOTE path is byte-for-byte the pre-fix code, so this is a standing single-batch-importer
   characteristic — not introduced here and outside objective A (blocked/empty re-run). It is an
   operational footgun: an owner who adds files incrementally to the T2 `7.2/inbox` and re-runs would
   shrink `final`, and Phase 7.3 would then analyse only the newest file. Recommended future 7.2
   enhancement: re-normalize `accepted_raw` contents so `final` is cumulative, or block a run whose
   new dataset would drop the promoted row count. **Not acceptance-blocking.**
2. **No owner target ACoS.** 0.30 ships as `NEUTRAL_DEFAULT_OWNER_CONFIGURABLE`; every ACoS-dependent
   judgment is provisional until the owner sets a real target.
3. **Search-term report only.** Other promoted 7.2 types block as `UNSUPPORTED_REPORT_TYPE`.
4. **Proof hash field was mislabeled** — corrected in this commit (see §25).

---

## 27. Final acceptance state

**`PHASE7_3_ACCEPTED_WITH_DOCUMENTATION_FIX`** — all acceptance gates pass; no production defect, no
regression; one proof-gate labeling inaccuracy corrected in this commit.

---

## 28. Exact next action

The branch `phase7-3-offline-ads-analysis` is **ready for manual merge into `main`**. The acceptance
commit and the annotated tag `phase7-3-accepted-<short>` are pushed to `origin`. No automatic merge
was performed. Phase 7.4 was not started.
