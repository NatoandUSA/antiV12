# Session 7.7 — Offline Outcome Follow-up — Independent Acceptance Audit

**Auditor role:** independent acceptance auditor. Every material claim below was
reproduced from repository bytes; the implementation report, proof gate, claimed
test counts, claimed runtime output, claimed immutability, claimed determinism,
and claimed counters were **not** trusted and were re-derived.

**Decision:** `PHASE7_7_OUTCOME_FOLLOWUP_ACCEPTED`

Not merged. Phase 7.8 not started. Acceptance tag created only after all gates passed.

---

## 1. Git provenance

| Item | Expected | Observed | OK |
| --- | --- | --- | --- |
| Branch | `phase7-7-offline-outcome-followup` | same | ✅ |
| Working tree (pre-audit) | clean | clean | ✅ |
| HEAD | `c54f527` | `c54f5273769e6afb3ab1c36f522e92cebc5a9661` | ✅ |
| Remote feature HEAD | `c54f527` | `c54f527…` (origin/phase7-7-offline-outcome-followup) | ✅ |
| main / origin/main | `c728f128` | both `c728f128dd693e923103c5b92a31dd17d2a1ffe0` | ✅ |
| Checkpoint tag | → `c728f128` | `phase7-7-outcome-followup-checkpoint-c728f12` → `c728f128…` | ✅ |
| 7.7 acceptance tag | none | none present (pre-audit) | ✅ |
| Implementation commit | `e663b62` | parent = `c728f128` (baseline) | ✅ |
| Proof commit | `c54f527` | parent = `e663b62` | ✅ |

Prior accepted tags intact (annotated tag-object → commit):
`phase7-2-cumulative-accepted-d5ad841` (91e2607→d5ad841),
`phase7-3-accepted-7005275` (b9d2755→7005275),
`phase7-4-owner-dashboard-accepted-eebecc5` (7704277→eebecc5),
`phase7-5-owner-decision-package-accepted-66d972d` (02b7a81→66d972d),
`phase7-6-manual-action-tracker-accepted-f1d11d8` (af9b7f6→f1d11d8). No history
rewrite, no accepted-tag movement.

## 2. Implementation and proof diffs

Linear chain `c728f128 → e663b62 → c54f527`. Full name-status of
`baseline..HEAD`:

- `A production/phase7_outcome_followup.py`
- `A tests/test_phase7_7_outcome_followup.py`
- `A SESSION7_7-OUTCOME-FOLLOWUP-IMPLEMENTATION-REPORT.md`
- `A SESSION7_7-OUTCOME-FOLLOWUP-PROOF-GATE.json`

Four files **added**, zero modified, zero deleted. Implementation commit `e663b62`
contains only the production module (1577 lines) + tests (986 lines), all
insertions. Proof commit `c54f527` contains only the implementation report + proof
gate, all insertions. No accepted prior production/test file was touched. Committed
blob sha1s match the proof gate exactly:
`production/phase7_outcome_followup.py = 672856461f7afad799e1c115852ae228b9287274`,
`tests/test_phase7_7_outcome_followup.py = 8448294632e38c79d9926798355cb0b477990d64`.

## 3. Phase 7.3 authority

Read path `runs/T2/phase7/7.3/promoted/`. Manifest `analysis-manifest.json` schema
`phase7-3-analysis-manifest-v1`; `output_hashes` present and **re-verified**: the
recorded `analysis.json` hash `cb426e68…` equals the actual bytes hash. Manifest
`deterministic_content_sha256 = 77bbaaa62ff1856bef3d4efce44d2db685e2200ab7905bea2cb1c2ce3233b56f`
is recorded as the source identity. `search_term_analysis` = 114 rows. Each row
carries per-row `start_date`/`end_date` (report-period, e.g. `2026-06-11`),
`canonical_row_key` with `mk=`/`range=`, `currency`, and `evidence.metric_states`
with `_7d` attribution suffixes, plus Decimal-string metrics. The loader rejects
oversize/null-byte/malformed/missing/hash-mismatch sources (SOURCE_* → SOURCE_BLOCKED
/ SOURCE_REQUIRED), reproduced by focused tests 10–12.

## 4. Phase 7.6 authority

Read `runs/T2/phase7/7.6/action_state/current.json` (+ `history.jsonl`). Real T2:
`record_count=0`, `records={}`, `history_event_count=0`,
`state_content_sha256=44136fa3…`. Phase 7.7 validates the state and the append-only
hash chain **through the accepted Phase 7.6 authority** (`TRK.load_state`), and
re-verifies package binding via `TRK.binding_state`. Focused tests 05–08 reproduce
TRACKER_REQUIRED / TRACKER_BLOCKED on missing state, malformed current.json, a
broken chain link, and a current/history mismatch. Manual-completed confirmation
and package-lineage checks are enforced (no package-lineage bypass; test 09/25).

## 5. Cumulative-source window correctness (CRITICAL RISK A)

**Finding: the single cumulative Phase 7.3 source can truthfully support separate
before/after windows, and fails safe when it cannot.**

- Rows carry their own **report-period** `start_date`/`end_date` (not ingestion
  dates). `_row_in_window` is a strict **containment** test on those dates, so a row
  is placed in a window only when its whole report period is inside it.
- Real T2: 114 rows span **39 distinct report ranges** (2026-06-06 … 2026-07-05),
  **114 distinct entity identities**, **0** identities appearing in more than one
  range, all `mk=US`, all `USD`, all attribution window `{7}`. So in the current
  real dataset each entity is reported in exactly one period — but there are 0
  tracker records, so 0 follow-ups are attempted, and the mechanism is exercised by
  synthetic fixtures.
- Independent harness (auditor-owned):
  - A1 — same entity in both windows → `before_row_count=1`, `after_row_count=1`,
    before spend `25.00`, after spend `40.00` (the cumulative total is **not**
    reused for both windows).
  - A2 — a single row whose report period **straddles** the boundary → excluded
    from **both** windows → `INSUFFICIENT_DATA` (partial-period safety).
  - A3 — entity present in only one period → opposite window empty →
    `INSUFFICIENT_DATA` (never fabricated).
  - A4 — two before rows summed exactly (`10.00+15.00=25.00`, `5+7=12` clicks); the
    after aggregate uses **only** after rows (`30.00`).

The claimed model is truthful. **Not rejected on Risk A.**

## 6. Source-lineage default safety (CRITICAL RISK B)

**Finding: the default is safe; `SOURCE_CHANGED` is correctly opt-in.**

The pinned source sha stored in the Phase 7.5 package manifest
(`phase7_3_manifest_sha256 = 77bbaaa6…`) **equals** the Phase 7.3 manifest
`deterministic_content_sha256`, so the lineage comparison is like-for-like. Because
Phase 7.2 ingestion is cumulative, a follow-up must read a source that has advanced
past the package's pinned source; requiring lineage match by default would defeat
the feature. Crucially, the default is **not** a stale carry-forward:

- Every run recomputes the observation from the **current** source bytes (verified
  integrity), never from a cached prior observation.
- The `followup_package_id` incorporates `source.identity_sha256`, so a different
  source yields a different package id (no silent byte reuse under a new source).
- `source_lineage.json` records the current source identity so the owner can see
  exactly what was compared.

Independent harness (this path is **not** covered by the module's own tests — see
§36):
- B1 flag ON + original source → 1 eligible (identity == pinned).
- B2 flag ON + genuinely changed source (different `identity_sha256`, same matched
  observation) → 0 eligible, exclusion `SOURCE_CHANGED`.
- B3 flag OFF (default) + changed source → 1 eligible from the **current** source,
  **different** `package_id` than the original-source run, and
  `source_lineage.json` records the current identity.
- B4 flag ON + source restored → 1 eligible again (matches pinned).

Default behaviour never silently presents stale observations as current. **Not
rejected on Risk B.**

## 7. Attribution and marketplace ambiguity (CRITICAL RISK C)

**Finding: incomparable data is never silently compared.**

Currency, attribution window, and marketplace are validated on the **matched set**
before slicing:
- currency null/absent on matched rows → `CURRENCY_MISMATCH` (block); multiple
  currencies → `CURRENCY_MISMATCH`; record-vs-row currency conflict →
  `CURRENCY_MISMATCH` (tests 32/43 + harness).
- multiple marketplaces → `AMBIGUOUS_ENTITY_MATCH` → readiness
  `ENTITY_MATCH_REQUIRED` (test 27 + harness C3); a single consistent marketplace is
  used; a null marketplace on **both** sides (default key has no `mk=`) is internally
  consistent and produces a valid observation (harness C1b/C4).
- multiple/derivable-conflicting attribution windows → `ATTRIBUTION_WINDOW_MISMATCH`
  (tests 33/44 + harness C2); a later source whose rows report a different window
  than the record → `ATTRIBUTION_WINDOW_MISMATCH` (harness C1c); when the later
  source has **no** derivable window, the tool falls back to the **record's own
  recorded** window (a safe owner value, never fabricated — harness C1).

Since the before/after subsets are sliced from a single validated matched set, they
always share one currency/window/marketplace; a conflict is excluded, never merged.
**Not rejected on Risk C.**

## 8. Eligibility matrix

Only `MANUALLY_COMPLETED` with a present `owner_completed_date`, a `CURRENT`
package binding, and a complete entity identity becomes a documented follow-up.
`PENDING_MANUAL_CHECK` / `MANUALLY_SKIPPED` / `NO_LONGER_RELEVANT` / `NEEDS_REVIEW`
/ `BLOCKED_BY_CURRENT_STATE` / `UNABLE_TO_VERIFY` → `NOT_ELIGIBLE_STATUS` exclusion
(reason + lineage preserved). `REVERTED_MANUALLY` → separate `reverted_records`
list (`REVERTED_SEPARATE`), never merged into a completion. No completion is
inferred. Focused tests 19–25 reproduce every branch; no reviewed record is lost.

## 9. Window validation

Explicit windows only; the reference date is a **required** CLI argument, never
defaulted to the system date. Rejected as `WINDOW_NOT_READY`: invalid dates,
reversed before/after, overlap (before must end strictly before after), and an
after window ending after the reference date. Per-record `minimum_followup_days` is
enforced (after window opening too soon → `WINDOW_NOT_READY`). Tests 13–18b + harness.

## 10. Entity matching

Match key = `campaign, ad_group, targeting, customer_search_term, match_type`
(display-name-independent; never row number / sort order / temp id / uuid /
timestamp). Currency, attribution window, and marketplace are validated on the
matched set, not part of the key. Different campaign/ad_group/target/search term/
match type are never collapsed; ambiguous matches are excluded (no best-guess).
Tests 26–33.

## 11. Decimal and metric safety

`float(` is absent from the module (AST-verified). All authoritative metrics are
exact strings/Decimals via `core.money`. Counts and money are summed only when
present on every contributing row; a missing metric stays missing (never 0). Ratios
are recomputed from summed bases via `safe_divide` (zero/missing denominator →
null, never infinity). No cross-currency/attribution/marketplace aggregation.
Harness EM: `cpc = 25/20 = 1.25`, `acos = 25/120 = 0.208333`, before `acos` null
(sales 0, not fabricated 0), `sales` abs delta `120.00`, `sales` pct delta null
(zero before denominator). Tests 34–44.

## 12. Outcome classifications

`OBSERVED_IMPROVEMENT / OBSERVED_DECLINE / OBSERVED_MIXED /
OBSERVED_NO_MATERIAL_CHANGE / INSUFFICIENT_DATA` plus documented exclusion classes.
Thresholds come only from an explicit policy (`phase7-7-outcome-policy-v1`, a
labelled `NEUTRAL_DEFAULT_OWNER_CONFIGURABLE`, ratio `0.10`); a null
`material_change_ratio` yields `SESSION7_7_POLICY_REQUIRED`, never an invented
number. Mixed directions → `OBSERVED_MIXED` (not improvement). Confidence is a
data-sufficiency label only. Tests 45–50 + harness.

## 13. Causation wording

Independent scan of all nine generated files for `caused the improvement`,
`caused the decline`, `optimized the campaign`, `amazon confirmed`,
`the system improved`, `the action succeeded`, `the software changed seller central`,
`this action caused` → **0 hits**. Disclaimers present in every relevant output
(`OFFLINE OBSERVATIONAL FOLLOW-UP`, "does not establish that the owner-recorded
action caused the result", other-factors list). `causation_asserted=false`,
`amazon_action_performed=false`. Tests 51–52b.

## 14. Duplicate handling

Canonical identity = entity identity + before/after periods + currency +
attribution window + marketplace. Identical duplicates collapse to one
(`duplicate_identical_count`++, lineage kept in `duplicate_tracker_record_ids`);
conflicting duplicates (same identity, different content) exclude **all** members
(`FOLLOWUP_CONFLICT`, `duplicate_conflict_count`++, no last-write-wins). Never
collapsed across currency/window/campaign/ad_group/target/period. Tests 59–60.

## 15. Output artifacts

Exactly nine files (`OWNER_READ_FIRST.md`, `executive_summary.md`,
`outcome_details.md`, `outcome_status.tsv`, `outcome_status.json`,
`excluded_followups.tsv`, `excluded_followups.json`, `source_lineage.json`,
`followup_manifest.json`). JSON schemas valid; disclaimers present; no Amazon upload
template, no API payload, no executable script, no mutation URL, no browser
selectors, no login/auth. Manifest declares a hash for every artifact except itself.

## 16. Empty real-T2 behaviour

Exact documented CLI reproduced (reference 2026-07-22; before 2026-06-01..06-30;
after 2026-07-01..07-21):
`readiness=SESSION7_7_FOLLOWUP_READY_EMPTY`, `followup_package_id=followup-ae48aea7a80654ca`,
`tracked_record_count=0`, `eligible=0`, `excluded=0`, `reverted=0`, all duplicate
counts 0, all Amazon/external counters 0, `amazon_action_performed=false`,
`causation_asserted=false`, exit code 0. All nine files present; empty TSVs contain
6 disclaimer comment lines + header + **zero** data rows; empty JSONs valid
(`followups=[]`, `excluded=[]`, `reverted=[]`); manifest `amazon_boundary` all
zero. Zero fake actions, zero inferred completions. Run twice → `IDEMPOTENT_REUSE`,
identical bytes.

## 17. Populated synthetic behaviour

Harness populated case: exactly one eligible follow-up; exact before/after metrics;
exact deltas; conservative classification (`OBSERVED_IMPROVEMENT` for the improving
fixture); stable `fu-…`/`followup-…` ids; owner-completion lineage preserved;
observational disclaimer present; no causal wording.

## 18. Stable identities

`followup_record_id` matches `^fu-[0-9a-f]{32}$`; `followup_package_id` matches
`^followup-[0-9a-f]{16}$` (test 53b). Both are canonical-JSON+SHA-256 over content
only — no timestamps, random ids, or paths. Identical inputs → identical ids
(harness + test 53/54).

## 19. Determinism

Two identical runs produce byte-identical authoritative outputs (test 55–58).
Independent harness: rebuilding the pipeline from identical inputs in a **different
workspace** yields the same `followup_record_id` and byte-identical authoritative
artifact hashes. Fresh worktree confirms CRLF/LF stability (focused tests + harness
pass there). No runtime timestamp/locale/dict-order/float formatting in
authoritative output.

## 20. Idempotency

Second identical run → `IDEMPOTENT_REUSE`, no rewrite (test 85, real-T2 both runs).
Same package id with altered existing bytes → `SESSION7_7_FOLLOWUP_BLOCKED`, previous
valid package preserved (test 86).

## 21. Atomic writes

Render → hash → temp dir → read-back verify → fsync → `os.replace`. A blocking
condition leaves no partial READY output and preserves the previous valid package;
no temp `.tmp-*` directory remains after success or block (tests 82–84). Inputs are
never modified on failure.

## 22. TSV safety

`_tsv_cell` (reused from the accepted Phase 7.4/7.5/7.6 authority) neutralizes
leading `= + - @`, tab, CR, LF; a genuine `-2.50` and a real `-120.00` delta are
preserved un-prefixed; Vietnamese Unicode survives; equal column counts (tests
72–81). End-to-end `=WEBSERVICE(1)` → `'=WEBSERVICE(1)` in the TSV.

## 23. Input immutability

Tree hashes before/after a real-T2 run — **byte-identical**:
- `runs/T2/phase7/7.3/promoted` (7 files) `3b9b5d27…`
- `runs/T2/phase7/7.5/packages` (9 files) `d58c2a02…`
- `runs/T2/phase7/7.6/action_state` (2 files) `62c771f3…`

No cache/lock/temp/export/log/metadata is written into any input directory (tests
90–93b). Phase 7.7 writes only under `runs/T2/phase7/7.7/`.

## 24. Validate-only

`--validate-only` resolves everything in memory and writes no package
(`output_dir` null; no `followups/` created). Valid-empty → exit 0; blocked → exit
nonzero (tests 87–89).

## 25. Security scan

Module imports are stdlib + internal only: `__future__, argparse, datetime,
hashlib, json, os, re, shutil, sys, core, production`. No `requests / httpx /
aiohttp / socket / urllib / http / boto3 / botocore / selenium / playwright /
webdriver / subprocess / pickle / marshal / shelve`. No `os.system / os.popen /
eval( / exec( / __import__( / float(`. No functional endpoint, credential, token,
cookie, session, webhook, telemetry, or external URL. The only `SP-API`/`Ads API`
strings are the disclaimer docstring and the zero counters that prove the boundary.
Generated outputs contain no API/upload/browser/login/URL strings. AST tests 96–100
+ 107/self reproduce this.

## 26. Amazon counters

All Amazon counters (`amazon_connections`, `amazon_sp_api_calls`,
`amazon_ads_api_calls`, `amazon_mutations`, `amazon_report_downloads`,
`amazon_bulk_uploads`, `amazon_api_payloads`, `browser_automation_attempts`,
`credential/cookie/token/session_store_count`, `subprocess_executions_from_data`)
are **zero** in every result and in the manifest `amazon_boundary`.

## 27. External-network count

`external_network_calls = 0` in every result and manifest. Constant zero; no code
path can increment it.

## 28. Compile result

`python -m compileall -q production core tests` → exit 0 (main tree and fresh
worktree).

## 29. Phase 7.7 focused tests

`python -m unittest tests.test_phase7_7_outcome_followup -v` → **Ran 93, OK**, exit 0.

## 30. Prior focused tests

`python -m unittest tests.test_phase7_2_report_ingestion
tests.test_phase7_3_ads_analysis tests.test_phase7_4_owner_dashboard
tests.test_phase7_5_owner_decision_package
tests.test_phase7_6_manual_action_tracker` → **Ran 797, OK (skipped=1)**, exit 0.

## 31. Full suite

`python -m unittest discover -s tests -p "test_*.py"` → **Ran 2991, OK (skipped=2)**,
Python exit code 0 (reproduced directly, no pipeline masking exit status).

## 32. Independent harness

Auditor-owned `_audit_harness.py` (17 Risk-A + 8 Risk-B + Risk-C + determinism +
exact-Decimal checks) → **42 passed, 0 failed**, exit 0 (main tree and fresh
worktree).

## 33. Fresh worktree

Detached worktree at `c54f527`: `runs/` **absent**; compileall exit 0; focused
7.7+7.6+7.5 = **302 OK (skipped 2)**; auditor harness **42/0**; prohibited-import
scan clean. Phase 7.7 does not depend on untracked real T2 data. Worktree removed
afterward.

## 34. runs/ tracking

`git ls-files runs/` → 0 tracked files. `runs/` is git-ignored. Post-run
`git status` shows only untracked auditor temp files (removed at end).

## 35. Documentation accuracy

The implementation report and proof gate accurately describe branch/commits, source
authorities, the single cumulative-source limitation, source-lineage flag
behaviour, attribution/marketplace null behaviour, entity matching, windows,
classification, duplicate handling, determinism, idempotency, atomic writes, the
real-T2 result (`followup-ae48aea7a80654ca`, 114 rows, READY_EMPTY), tests, source
immutability, prohibited integrations, counters, and known limitations. Two
cosmetic notes (non-blocking, not requiring a fix): (a) the report lists prior
accepted tags by their **annotated tag-object** sha1 (`91e2607`, …) rather than the
dereferenced commit hash — technically correct but easy to misread; (b) the
"proof commit … self" phrasing is a self-reference. No production defect.

## 36. Known limitations

Accurately documented in the report/proof gate: (1) both windows are sliced from
the one cumulative Phase 7.3 `promoted/` analysis; (2) `SOURCE_CHANGED` is opt-in
via `--require-source-lineage-match`; (3) attribution window / marketplace are
derived from Phase 7.3 fields and treated as null when absent; (4) classification is
conservative and observational. Auditor additions (non-blocking):
- The `--require-source-lineage-match` / `SOURCE_CHANGED` path is **not exercised by
  the module's own test suite**; it was verified here by the independent harness and
  behaves correctly. Recommend adding a regression test in a future session.
- When a later source's rows omit derivable attribution-window markers, the tool
  falls back to the record's recorded window. In genuine Phase 7.3 output every row
  carries `_Nd` markers (all 114 real rows do), so this fallback is defensive only;
  a hand-edited source is separately blocked by manifest hash verification.

## 37. Final decision

`PHASE7_7_OUTCOME_FOLLOWUP_ACCEPTED`

No rejection trigger fired: the single cumulative source truthfully provides
separate windows and fails safe to `INSUFFICIENT_DATA`; stale lineage is never
silently carried forward (each run recomputes from current bytes; ids are
source-identity-bound); unknown attribution/marketplace never yields an ambiguous
comparison; no causal claim appears; inputs are byte-immutable; determinism and
idempotency hold; no partial package can be marked READY; the full suite and fresh
worktree pass; all Amazon/offline boundaries are zero.

## 38. Exact next action

Commit this acceptance report (docs-only), create the annotated acceptance tag
`phase7-7-outcome-followup-accepted-<short-hash>`, push the feature branch and tag,
and confirm `main`/`origin/main` remain `c728f128`. Do **not** merge into `main`.
Do **not** begin Phase 7.8. Merge to `main` and Phase 7.8 authorization are separate
owner decisions.
