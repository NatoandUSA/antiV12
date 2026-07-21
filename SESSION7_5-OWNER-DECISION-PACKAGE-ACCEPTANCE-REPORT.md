# Session 7.5 — Offline Owner Decision Package — Independent Acceptance Audit

**Auditor role:** independent acceptance auditor. Evidence reproduced from bytes; the implementation
report and proof gate were not trusted without reproduction. No production code was modified. No merge
into `main`. Phase 7.6 not begun.

**Decision:** `PHASE7_5_OWNER_DECISION_PACKAGE_ACCEPTED`

**Date:** 2026-07-21 · **Platform:** Windows 11, Python 3.12.10 · **Test runner:** `unittest` (project gate)

---

## Numbered findings

**1. Git branch.** `phase7-5-owner-decision-package` (confirmed via `git rev-parse --abbrev-ref HEAD`).

**2. Baseline.** `0d85e03bba5fdc3e63103c02abc78b6ff6b79b4c`; is an ancestor of HEAD; checkpoint tag
`phase7-5-decision-package-checkpoint-0d85e03` → `0d85e03` (verified `^{commit}`).

**3. Implementation commit.** `ae07310` `feat(phase7.5): add offline owner decision package` — adds exactly
`production/phase7_owner_decision_package.py` (+1342) and `tests/test_phase7_5_owner_decision_package.py`
(+1076); 2 files, +2418, no modifications.

**4. Proof commit.** `3ee3d5950ce34ebce298d0fcab0e311b458037e8`
`docs(phase7.5): add owner decision package proof gate` — adds exactly the implementation report (+187)
and proof gate (+162); documentation-only.

**5. Acceptance commit.** Created by this audit (see final response); adds only this acceptance report.

**6. Acceptance tag.** `phase7-5-owner-decision-package-accepted-<short>` (annotated; see final response).
No prior Phase 7.5 acceptance tag existed (`git tag -l "*7-5*accepted*"` → empty).

**7. Local HEAD.** `3ee3d59` before the acceptance commit.

**8. Remote branch HEAD.** `origin/phase7-5-owner-decision-package` = `3ee3d59` (== local, pre-commit).

**9. Main HEAD.** `main` = `origin/main` = `0d85e03` — unchanged; not merged.

**10. Git cleanliness.** Working tree clean before and after the audit (`git status --porcelain` empty).
The real-T2 run wrote only under `runs/T2/phase7/7.5` (gitignored). No history rewrite/amend/rebase/tag
movement. Prior accepted tags intact: `phase7-2-cumulative-accepted-d5ad841` → `d5ad841`,
`phase7-3-accepted-7005275` → `7005275`, `phase7-4-owner-dashboard-accepted-eebecc5` → `eebecc5`.

**11. Implementation diff.** Only the two intended new files (module + tests). No prior accepted code or
test file touched.

**12. Proof diff.** Only the report + proof gate. Documentation-only.

**13. Canonical Phase 7.3 source.** `runs/T2/phase7/7.3/promoted/` used via `DASH.load_source`;
`promoted/` precedes stale `final/` — reproduced in `SourceSelection.test_promoted_precedence_over_final`
(lineage `source_dir_type == "promoted"` even with a `final/` present).

**14. Phase 7.3 integrity.** Producer-exact validation is inherited from the accepted Phase 7.4 authority
and re-exercised: tampered manifest → `SOURCE_BLOCKED`; tampered/appended artifact → `SOURCE_BLOCKED`;
malformed JSON / NaN → `SOURCE_BLOCKED`; missing manifest → `SOURCE_REQUIRED`. Real-T2 integrity result =
producer-exact PASS; all 7 promoted files byte-identical before/after (finding 40).

**15. Phase 7.4 review-state authority.** `runs/T2/phase7/7.4/review_state/review-state.json`, schema
`phase7-4-review-state-v1`. Inspected the actual file: one record, `review_status: DEFERRED`.

**16. Review-state validation.** Strict loader (`load_review_state_strict`) blocks duplicate JSON keys,
non-finite numbers (NaN/Infinity via `parse_constant`), null bytes, malformed JSON, wrong root type, wrong
schema, oversize. Per-record structural issues (invalid status, bad 64-hex shape, mismatched inner id) are
per-record exclusions (`INVALID_REVIEW_STATE`) rather than whole-set blocks. Reproduced in
`ReviewStateLoader` (7 cases) + `Eligibility` structural cases; corrupt real-shape review state raised
`DecisionPackageError`. Phase 7.5 is read-only toward review state (finding 41).

**17. Eligibility matrix.** Independently reproduced all 16 gates with an auditor-authored harness (not the
shipped tests): APPROVED negative/exact/bid-budget(OWNER_DECLARED) → candidate; every other status
(`UNREVIEWED, REJECTED, DEFERRED, NEEDS_MORE_DATA, NEEDS_POLICY, ALREADY_HANDLED, NOT_APPLICABLE`) →
`NOT_APPROVED`; unsupported label → `UNSUPPORTED_RECOMMENDATION_TYPE`; blocked class/reason →
`BLOCKED_RECOMMENDATION`; bid/budget neutral acos → `MISSING_POLICY`; missing metric → `MISSING_EVIDENCE`;
no currency → `AMBIGUOUS_CURRENCY`; 0 or ≥2 windows → `AMBIGUOUS_ATTRIBUTION_WINDOW`; missing
campaign/search-term → `MISSING_REQUIRED_FIELD`. Only `APPROVED_FOR_MANUAL_ACTION` can become eligible.
Every non-eligible reviewed record appears in exclusions — never silently dropped.

**18. Content/source-change matrix.** Independently reproduced: content-mismatch + manifest-match →
`HASH_MISMATCH`; content-mismatch + manifest-mismatch → `SOURCE_CHANGED`; content-match + manifest-mismatch
→ `SOURCE_MANIFEST_MISMATCH`; entity removed → `ENTITY_ABSENT`; unknown prefix → `UNKNOWN_ENTITY`;
end-to-end changed-row → `SOURCE_CHANGED` and dropped-row → `ENTITY_ABSENT` (readiness
`SOURCE_CHANGED_REVIEW_REQUIRED`). No last-write-wins; stale approvals never enter approved output.

**19. Supported recommendation types.** Exactly `REVIEW_FOR_MANUAL_NEGATIVE`,
`REVIEW_FOR_MANUAL_EXACT_KEYWORD`, `REVIEW_BID_OR_BUDGET_CONTEXT` (bound to `AA.RL_*` constants; verified
those symbols exist). No analytical reclassification; recommendation semantics unchanged; action wording is
advisory/manual only.

**20. Real approved count.** `0`.

**21. Real eligible count.** `0`.

**22. Real excluded count.** `1` (`NOT_APPROVED`, `reason_detail=review_status=DEFERRED`,
`source_change_state=CURRENT`, full lineage: campaign/ad_group/target/search-term/currency/owner_note).

**23. Real duplicate-identical count.** `0`.

**24. Real duplicate-conflict count.** `0`.

**25. Real source-changed count.** `0` (blocked `0`, policy_required `0`).

**26. Real package readiness.** `SESSION7_5_PACKAGE_READY_EMPTY`, CLI exit `0`.

**27. Real package ID.** `pkg-3cf372628abc6082`;
`package_content_sha256=3cf372628abc60824a3f6fa3e82c53459f123afbfa1e8aebc2d937007672e552`
(package id == `pkg-` + content-hash[:16], content-addressed — verified).

**28. Package artifacts.** 9 files present: `OWNER_READ_FIRST.md`, `executive_summary.md`,
`manual_action_checklist.tsv`, `manual_action_checklist.json`, `decision_details.md`, `excluded_items.tsv`,
`excluded_items.json`, `source_lineage.json`, `package_manifest.json` (manifest lists the 8 deterministic
artifacts + isolated runtime metadata). No bulk-upload/API-payload/browser-selector/executable/mutation-URL
artifact. Disclaimer present in every relevant file (verified in TSV comment header, MD, JSON meta,
manifest).

**29. Empty-package behavior.** Real-T2: approved 0 / eligible 0 / excluded 1, reason `NOT_APPROVED`,
readiness `READY_EMPTY`, exit 0; all 9 files exist; checklist TSV has the 5-line disclaimer + full 30-column
header and **zero** action rows; `manual_action_checklist.json` items = 0; `OWNER_READ_FIRST.md` states "No
owner-approved, current, unblocked decisions are eligible for manual action." Empty ≠ failure. The real
review record was **not** altered to force population.

**30. Populated synthetic behavior.** Auditor harness: approve one negative → readiness `READY`, eligible
1, checklist item present, evidence traceable, owner note preserved, source/content hash preserved, currency
+ window preserved, no float, no execution claim, deterministic item id + package id.

**31. Package item identity.** `item:` + sha256(canonical action identity + source content hash). Stable
and content-addressed; not derived from row number, sort position, timestamp, UUID, or filesystem order
(verified stable + prefixed).

**32. Package identity.** `pkg-` + content-hash[:16]; content hash = canonical serialization of the
deterministic model + sorted artifact hashes; independent double-run produced identical id/hash.

**33. Duplicate handling.** Identical (two derived views of the same action) → 1 item, `duplicate_identical`
+1, lineage recorded; conflicting content on the same action identity → **all** members excluded
(`DUPLICATE_CONFLICT`), `duplicate_conflicts` +2, no last-write-wins, readiness `CONFLICT_REVIEW_REQUIRED`;
cross-currency same term → **not** collapsed (2 items). Reproduced at unit and end-to-end level.

**34. Exclusion handling.** All 16 reason codes present and exercised
(`NOT_APPROVED, SOURCE_CHANGED, ENTITY_ABSENT, HASH_MISMATCH, SOURCE_MANIFEST_MISMATCH,
BLOCKED_RECOMMENDATION, MISSING_POLICY, MISSING_EVIDENCE, AMBIGUOUS_CURRENCY,
AMBIGUOUS_ATTRIBUTION_WINDOW, DUPLICATE_IDENTICAL, DUPLICATE_CONFLICT, INVALID_REVIEW_STATE,
UNKNOWN_ENTITY, UNSUPPORTED_RECOMMENDATION_TYPE, MISSING_REQUIRED_FIELD`). Exclusion rows carry lineage +
`reason_detail`. No reviewed item silently lost.

**35. Determinism.** Two independent runs (different base dirs, fixed clock) → byte-identical for all 8
deterministic artifacts and identical `package_content_sha256`; `package_manifest.json` content hash
identical though its isolated `runtime_metadata.generated_at` differs — the timestamp never feeds the
content hash. Changing `--reference-date` changes the identity. Reproduced independently.

**36. Idempotency.** Repeated identical run → `IDEMPOTENT_REUSE`; real-T2 rerun left package mtimes
byte-identical (not rewritten); same package name + altered content → `SESSION7_5_PACKAGE_BLOCKED`, last valid
package preserved (integrity conflict, no overwrite).

**37. Atomic writes.** Injected failure in `_build_manifest` → RuntimeError raised, no `packages/<id>` dir
created, no `runtime/.build-*` leftover. Build occurs in a temp dir, artifacts verified from bytes, then
`os.replace`; failure path removes the temp dir. No source directory touched.

**38. Decimal safety.** No `float(` in the module. `spend`/`sales` validated via the Decimal money
authority (`MONEY.parse_decimal_string`, `allow_missing`); exact strings preserved (`"5.00"` stays a string),
missing stays missing (never 0), NaN/Infinity rejected. No cross-currency or cross-window aggregation.

**39. TSV / formula-injection safety.** Reuses accepted `DASH._tsv_cell`: leading `= + - @` (and
tab/CR/LF) neutralized with a leading `'`; legitimate `-2.50` / `-2` preserved as numeric-looking; control
chars stripped; every row emitted from a fixed column tuple → equal column counts; Vietnamese Unicode note
preserved verbatim in JSON. Reproduced (`'=` prefix, equal columns, unicode intact).

**40. Phase 7.3 immutability.** SHA-256 of all 7 promoted files identical before and after: real-T2
generation, idempotent rerun, and synthetic runs. No lock/cache/temp/metadata/export file created inside the
source. File count unchanged (7).

**41. Phase 7.4 immutability.** `review-state.json` SHA-256
`8ee2de416c29dca85102d391859db9cff2b8ffc650351d54e9d884104dbfcbc2` identical before and after. Read-only
confirmed.

**42. Validate-only.** Valid input → correct counts, readiness `READY`/`READY_EMPTY`, **no** package dir
created, exit 0; blocked input (missing source) → `SOURCE_REQUIRED`, exit 2. Amazon/external counters remain
0.

**43. CLI.** `--help` exits 0; missing `--reference-date` exits 2 with an argparse error; the exact real-T2
command exits 0 with the summary in finding 26 (true Python exit code captured directly, not through a
masking pipeline). `--format json`, `--validate-only`, `--include-deferred-summary` all behave as
documented.

**44. Prohibited integrations.** Independent source scan: imports are stdlib
(`argparse, datetime, hashlib, json, os, re, shutil, sys`) + internal modules only. No
requests/httpx/aiohttp/urllib/socket/boto3/selenium/playwright/webdriver, no subprocess/os.system/os.popen,
no eval/exec, no float(, no pickle/marshal/shelve, no keyring/cookie/oauth/token storage, no
`.amazonaws`/`sellercentral`/`advertising.amazon`, no dynamic import/compile of report data. The only
"subprocess" occurrence is the zero counter `subprocess_executions_from_data: 0`. Path writes are bounded to
the workspace; `--package-name` is validated against `^[A-Za-z0-9._-]+$` (path traversal rejected).

**45. Amazon counters.** All 14 constant zero (`amazon_connections, amazon_sp_api_calls,
amazon_ads_api_calls, amazon_mutations, amazon_report_downloads, amazon_bulk_uploads, amazon_api_payloads,
browser_automation_attempts, credential_store_count, cookie_store_count, token_store_count,
session_store_count, external_network_calls, subprocess_executions_from_data`). No code path can increment
them. Manifest `amazon_action_performed=false`, `this_session_never` all true.

**46. External-network count.** `external_network_calls = 0` (constant). No network client exists.

**47. Compile result.** `python -m compileall -q production core tests` → exit 0 (main tree and fresh
worktree).

**48. Phase 7.5 focused tests.** `python -m unittest tests.test_phase7_5_owner_decision_package` →
**109 passed**, exit 0.

**49. Phase 7.2 focused tests.** `tests.test_phase7_2_report_ingestion` → **377 passed, 1 skipped**, exit 0.

**50. Phase 7.3 focused tests.** `tests.test_phase7_3_ads_analysis` → **117 passed**, exit 0.

**51. Phase 7.4 focused tests.** `tests.test_phase7_4_owner_dashboard` → **94 passed**, exit 0.

**52. Full suite.** `python -m unittest discover -s tests -p "test_*.py"` → **2798 passed, 2 skipped,
0 failures, 0 errors**, exit 0 (`OK (skipped=2)`; 0 `FAIL:`/`ERROR:` lines).

**53. Synthetic harnesses.** Auditor-authored harness (43 checks: eligibility, content/source-change,
duplicates incl. cross-currency, determinism, idempotency, atomic, formula-injection, Decimal/Unicode,
validate-only, immutability) → **0 failures**.

**54. Fresh worktree.** Detached worktree at `3ee3d59` with `runs/` absent: compileall exit 0; 7.5 = 109,
7.3 = 117, 7.4 = 94 passed; synthetic populated fixture → `READY` + all 9 files; synthetic empty fixture →
`READY_EMPTY`, eligible 0 / excluded 1, exit 0; prohibited-integration scan clean. Confirms no dependence on
untracked local T2 data. Worktree removed afterward.

**55. runs/ tracking.** `git ls-files runs/` → empty; `git check-ignore runs/T2/phase7/7.5` → ignored. No
owner runtime data tracked.

**56. Documentation accuracy.** Report + proof gate accurately describe branch, baseline, commits,
checkpoint, source/review-state authorities, supported types, eligibility rules, exclusion reasons,
deterministic IDs, artifacts, empty-package behavior, real-T2 counts, focused + full test counts, compile,
immutability, determinism, idempotency, atomic writes, formula-injection, fresh-worktree, runs/ tracking,
prohibited integrations, Amazon/external counters, and limitations — all independently reproduced and
matching. The only imperfection is the deliberate `<PROOF_COMMIT>` placeholder in the report/proof gate,
which explicitly defers the proof-commit hash to "the final response"; it is resolved here (finding 4:
`3ee3d59`). This is self-consistent (a commit cannot embed its own hash) and non-blocking — not a doc fix.

**57. Known limitations (accepted).** (a) Only the three actionable Phase 7.3 labels are checklist-eligible;
other labels/aggregate entities surface as `UNSUPPORTED_RECOMMENDATION_TYPE` — deliberate. (b) Populated
checklist path is covered by synthetic fixtures only (real T2 has no approved record; no owner data
committed). (c) Package dir is content-addressed `pkg-<hash16>` rather than the prompt's illustrative
`decision-package-<id>` — deterministic/collision-safe, honored in spirit. Non-blocking auditor
observations: the display-only `source_change_state` annotation reports "SOURCE_CHANGED" for a
`HASH_MISMATCH` exclusion (the authoritative `reason_code` and the `source_changed` count remain correct — a
hash mismatch is correctly counted under source-changed); a review record with no `source_manifest_sha256`
(None) is treated as manifest-matching (content hash is the primary integrity check); the
`CONFLICT_REVIEW_REQUIRED` / `SOURCE_CHANGED_REVIEW_REQUIRED` / `POLICY_REQUIRED` readiness states exit 0
because a valid package (empty checklist + full exclusions) is still produced. None affect safety or
correctness.

**58. Final decision.** `PHASE7_5_OWNER_DECISION_PACKAGE_ACCEPTED`. Production behavior is safe and correct;
Phase 7.3 source and Phase 7.4 review state are immutable (byte-identical verified); deterministic outputs,
idempotency, and atomic-write safety are independently verified; all focused suites and the full suite pass;
the offline + permanent Amazon boundary holds with every counter at constant zero; documentation is accurate.
No blocking defect.

**59. Exact next action.** Create the single acceptance commit (this report only) and the annotated tag
`phase7-5-owner-decision-package-accepted-<short>`; push the feature branch and the tag; verify local HEAD ==
remote branch HEAD and `main`/`origin/main` remain `0d85e03`. Do **not** merge into `main`. Do **not** begin
Phase 7.6. The owner remains the only manual bridge to Seller Central; a human still verifies each item and
performs any action manually.
