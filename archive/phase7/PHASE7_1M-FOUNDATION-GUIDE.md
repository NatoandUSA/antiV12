# Phase 7.1M foundation — PHASE7_OWNER_INPUT_REQUIRED

## What this foundation does
It answers ONE question deterministically: *given a verified live product and complete owner-confirmed economics, is this advertised SKU economically eligible to proceed to MANUAL campaign planning?* It builds no campaign, selects no target/negative, computes no bid, and allocates no budget.

## Why there is no campaign package
Phase 6 is a cryptographically verified **safe draft**, not a live listing. Campaign planning (7.1E+) is a separate, later, owner-authorized session. This session only lays the foundation.

## Live Product State vs PPC Contract (separate on purpose)
- **LIVE-PRODUCT-STATE** = the owner-confirmed real-world listing + fulfilment state.
- **PPC-PRODUCT-CONTRACT** = binds that verified state to the verified Phase 6 package (by recomputed hashes). A template is never a verified contract; a hash mismatch never verifies.

## Money is Decimal, never float
Every price/cost/fee is a canonical decimal string parsed with `core.money` (Decimal). Floats, NaN, Infinity, blanks, and scientific notation are rejected. A missing value stays missing.

## The economics (all owner-reviewable)
```
net_sales                     = selling_price - expected_discount
non_ad_variable_costs         = sum(all required costs)   # never a silent 0
contribution_margin_before_ads= net_sales - non_ad_variable_costs
break_even_ad_cost_per_order  = contribution_margin_before_ads
target_ad_cost_per_order      = contribution_margin_before_ads - minimum_required_profit
break_even_acos               = break_even_ad_cost_per_order / net_sales
target_acos                   = target_ad_cost_per_order / net_sales
maximum_cpc                   = target_ad_cost_per_order * expected_ad_conversion_rate
```
- `minimum_required_profit` default: **8.00 USD** (declared + source-recorded; the owner may override). No other cost defaults to zero.
- **maximum_cpc is a ceiling, not a recommended bid.** It is the highest CPC at which the target ACoS still holds. There is NO universal CPC clamp and NO CPC fallback.

## Budget feasibility (no role allocation)
```
estimated_total_click_capacity = owner_total_test_budget / planned_cpc_candidate
maximum_safe_click_capacity    = owner_total_test_budget / maximum_cpc   # a bound, not a bid
```
Campaign roles are NOT selected and campaign budgets are NOT allocated here. No $25 threshold, no fixed percentage, no universal click threshold, no universal learning window.

## Readiness (most restrictive wins)
- Current decision: **PHASE7_OWNER_INPUT_REQUIRED** — required live-product / economics / budget owner inputs are missing
- `PHASE7_OWNER_INPUT_REQUIRED` → supply the missing owner inputs.
- `PHASE7_ECONOMICS_BLOCKED` → the SKU is not economically/operationally eligible.
- `PHASE7_READY_FOR_CAMPAIGN_PLANNING` → a later MANUAL planning session may begin. This is **not** ready-for-manual-launch and **not** ready-for-Amazon-action (both always false here).

## Exact next steps once the listing is live
1. Complete `LIVE-PRODUCT-STATE.template.json` from a manual Seller Central screen-read.
2. Complete `PPC-ECONOMICS.template.json` with real decimal costs.
3. Bind them in `PPC-PRODUCT-CONTRACT.template.json` (Phase 6 hashes are pre-filled).
4. Re-run this foundation; when everything verifies, readiness advances — then a later owner-authorized session plans campaigns manually.
