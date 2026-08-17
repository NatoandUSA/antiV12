# Phase 7.1M — Acceptance Review

**Status:** `PHASE7_1M_ACCEPTED_WITH_REPORTING_FIX`
**Audited commit:** `90843a0` · **Accepted baseline:** `f7b6253` · **Branch:** `main`
**Accepted tag:** `phase7-1m-accepted-f7b6253`
**Truthful product state (unchanged):** `PHASE7_OWNER_INPUT_REQUIRED`

## What was audited
An independent audit of the completed Phase 7.1M minimal launch foundation — money authority, economics formulas, capacity/budget, readiness, determinism, the permanent Amazon boundary, and a clean-worktree reproduction. The prior report was **not** trusted; every gate was re-run against the live repository.

## Verdict
**No material defect.** Money math, all eight economics formulas, the maximum-CPC ceiling semantics, capacity/handling/budget safety, the most-restrictive readiness resolver, determinism, and the Amazon boundary are all correct and truthful.

**One reporting-only defect found and corrected** (Category B): the owner-input accounting did not reconcile.

## The reporting defect
The proof published `required_input_count = 36`, `verified_input_count = 0`, `missing_input_count = 21`, `contradictory_input_count = 0`. Those do not close: `0 + 21 + 0 = 21 ≠ 36`.

Root cause: `_input_accounting` used an **asymmetric denominator**. `verified` counted only live-product-state fields, while `missing` counted the 20 live fields **plus a single aggregate placeholder** (`"<all economics inputs>"`) for the entire 16-field economics document. Economics inputs could therefore add to *missing* but never to *verified*, and when the economics document was absent they collapsed to one item — leaving a 15-field gap that never reconciled. The `owner_action_count = 21` framing did not rescue it, because the equally-not-started live-state document was counted as 20 field-actions while economics was counted as 1 — the same condition at two different granularities.

## The fix (reporting-only)
`_input_accounting` now counts **both** required documents at the same per-field granularity via a new `_ECON_REQUIRED_FIELDS` set (12 costs + selling price, expected discount, minimum profit, conversion rate = 16). The identity `required == verified + missing + contradictory` now holds in every state. For the truthful T2 safe-draft state it reports:

| count | before | after |
|---|---|---|
| required | 36 | 36 |
| verified | 0 | 0 |
| missing | 21 | **36** |
| contradictory | 0 | 0 |

Economics fields with a usable parsed value — including the declared `$8.00 USD` minimum-profit default — now count toward *verified* (e.g. a complete economics document reports `36 / 16 / 20 / 0`).

Nothing else changed: no change to money parsing/serialization, economics formulas, the `maximum_cpc` ceiling, capacity/handling/budget logic, the Amazon boundary, or the readiness state — which stays `PHASE7_OWNER_INPUT_REQUIRED`. `SESSION7_1M-PROOF-GATE.json` was regenerated (reproduces deterministically) and two reconciliation tests were added.

## Gates (all green)
- **Compile:** PASS (`compileall` over all packages).
- **Targeted:** `tests.test_phase7_1m_foundation` — 170 ran / 0 fail / 0 err / 0 skip.
- **Full suite:** `unittest discover -s tests` — **1832 ran / 0 fail / 0 err / 1 skip** (554.846 s as-delivered at `90843a0` = 1830; +2 accounting tests after the fix).
- **Determinism:** two-run and three-mode (`CONNECTED_RESEARCH` / `LOCAL_SAFE` / `TEST_DENY_EXTERNAL`) identical for stable artifacts and the regenerated proof.
- **Clean worktree** at `f7b6253` (hermetic, no `runs/`): compile PASS; 7.1M 170/0/0/0; 7.0 preflight 87/0/0 with 3 designed workspace-dependent skips; proof parses standalone and the accounting closes; no private paths/credentials; no Amazon integration in the 7.1M authority; no campaign/target/negative/bid/launch/report/optimization artifacts; loopback-only bind.

## Permanent boundary (re-asserted, all zero)
`external_amazon_account_attempts`, `amazon_account_actions`, `campaign_write_actions`, `target_selection_count`, `negative_selection_count`, `bid_calculation_count`, `launch_package_count`, `later_phase_artifact_count`, `image_generation_calls` — all `0`. The owner remains the sole manual bridge to Amazon.

## Exact owner completion sequence (once the listing is live)
1. Complete `LIVE-PRODUCT-STATE.template.json` from a manual Seller Central screen-read (no credentials/cookies/tokens/URLs pasted).
2. Complete `PPC-ECONOMICS.template.json` with real decimal-string costs.
3. Bind them in `PPC-PRODUCT-CONTRACT.template.json` (Phase 6 hashes pre-filled).
4. Re-run the foundation; when everything verifies, readiness advances — then a later **owner-authorized** session plans campaigns manually.

## Conditions before Phase 7.1E
Phase 7.1E (campaign planning) must **not** begin until the owner supplies the verified live product state, the bound PPC contract, and complete economics, and the foundation resolves above owner-input-required. No target/negative/bid/campaign-role/budget/launch work is authorized until then.

## Known limitations
- Phase 6 is a verified **safe draft**; no live listing/ASIN/SKU/price/capacity/handling/eligibility is owner-confirmed.
- `required_input_count = 36` sums two documents; `currency` and `price` are required in both by design and counted per-document (not de-duplicated).
- An invalid economics input is folded into *missing* in the accounting; the economics validation artifact reports invalid inputs separately.
- `$8.00 USD` minimum profit is a declared, owner-reviewable default (USD only).
- `maximum_cpc` is an economic ceiling, not a recommended bid.

**Recommended next session:** Phase 7.1E is owner-gated — hold until the owner completes the live product state, PPC contract, and economics. No code work is required to accept this baseline.
