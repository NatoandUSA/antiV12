# v2 Self-Audit Report
Honest status against the master-prompt acceptance criteria. Nothing hidden.

## Test results
`python -m unittest tests.test_regression` → **17/17 PASS** (13 regression + 4 core-unit).

## Acceptance criteria — status

### ✅ MET (built + tested this turn)
| Criterion | Evidence |
|---|---|
| No Seller Central connection anywhere | grep: no SP-API / credentials / browser automation; `config.seller_central_connection_allowed=false` |
| IP BLOCK stops publication readiness | test_02, test_05; manifest locks on IP_SAFETY=BLOCKED |
| BLOCKED never exits 0 | `core/status.py` exit map; test_01 (exit 3), test_07 (exit 5) |
| Missing data = INCOMPLETE, not zero/negative | test_06; `provenance.MISSING`; demand INCOMPLETE |
| Min $8 contribution enforced | test_07; `config.min_profit()`; economics gate |
| Owner approval version-bound + invalidation | test_11, test_12, test_13; manifest.invalidate_approvals |
| Score never overrides a hard gate | CoreUnit.test_score_never_overrides_hard_gate |
| Manifest atomic writes + history | manifest.save temp+replace; history snapshots |
| Central statuses + config (not hard-coded) | core/status.py, core/config.py, config.yaml |
| ASIN current-activity ≠ legacy reviews | test_04 (dead $0-rev/900-review = NEWBIE) |
| Contextual IP ("stitch") | test_03 |
| One IP engine | tm_guard delegates to ip_guard |
| Non-destructive migration | migrate_project.py + backup, invents nothing |

### 🟡 PARTIAL
- Listing accuracy / claims: `listing_validate.py` screens all content + absolute-vs-comparative claims (from P1), wired as LISTING_ACCURACY gate. A standalone `claims_validator` with per-claim evidence provenance is not yet separate.
- Provenance labels exist (`core/provenance.py`) but are not yet threaded through every report.

### 🟢 CREATIVE MODULE — BUILT + TESTED (v2.2, PILOT_READY) — see CREATIVE-V2_2-SELF-AUDIT.md

### 🔴 NOT DONE (Phase 2–4 — folders scaffolded, logic pending)
Feasibility gate, FBM fulfillment gate, positioning builder, catalog/variation validator, keyword-intent mapper, listing-copy builder, personalization validator, 9-image plan + visual-consistency audit, A+ builder, competitor-gap, pre-launch 100-pt scorer, publication-package generator, 48h/7d/14d/30d post-launch analyzers, diagnostic action engine, PPC planner, experiment planner, learning loop, dashboard UI, full doc set.

Regression cases **not yet covered** because their stage isn't built: #9 (hoodie-for-crewneck rejection — needs keyword_mapper/catalog gate), #14 (image contradiction — needs image_validator), #15 (fulfillment block — needs fbm_validator). These are Phase 2 and are listed as known gaps, not silently passed.

## Remaining risks / manual verification still required
- Amazon category rules (title length, variation themes, main-image rules, A+ eligibility) must be **verified manually** — the toolkit marks these, never asserts them.
- The IP library is risk-reduction, not legal clearance.
- Phase-2 gates are NOT_RUN, so the pipeline conservatively keeps every project **PUBLICATION LOCKED** until those stages exist — fail-safe by design.

## Verdict
The **enforceable safety core is done and tested**: gates, exit codes, manifest, approval binding, config, provenance. The system cannot be walked past a failed hard gate or a missing approval. The creative/analytics stages (positioning → post-launch) are the next phases and are honestly marked incomplete rather than stubbed as done.
