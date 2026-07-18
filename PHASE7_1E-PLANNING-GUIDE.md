# Phase 7.1E — owner-gated offline manual Sponsored Products planning

Current committed decision for T2: **`PHASE7_OWNER_INPUT_REQUIRED`** (no owner live-state / economics /
contract / policy is confirmed yet, so nothing is planned).

## What this engine does
Given the **accepted Phase 7.1M foundation** (a verified live product + complete owner-confirmed
economics) **and a confirmed owner policy**, Phase 7.1E produces deterministic *local planning
recommendations* for a MANUAL Sponsored Products launch: eligible target candidates, an owner-selected
target set, optional negative candidates, campaign-role planning labels, a starting-bid plan, a budget
plan, and a manual-entry worksheet. **The owner reviews every row and types the approved ones into
Seller Central by hand.** Nothing is ever uploaded.

## Permanent Amazon boundary (re-asserted)
The owner is the ONLY manual bridge to Amazon. This engine never: logs in; uses SP-API / MWS / the
Advertising API; uses browser automation, cookies, credentials, sessions, or tokens; retrieves reports;
creates or updates any campaign / ad group / target / negative / bid / budget / listing / price /
inventory; uploads anything; emits an Amazon API payload; or binds a public server. Every Amazon-action
counter is **0**.

## Thin by construction (no duplicate authorities)
It reuses the existing authorities and adds no `campaign_compiler` / `bid_engine` / `negative_engine` /
`targeting_engine` / `_v2` duplicate:
- the whole launch foundation (live state / contract / economics / capacity / budget / readiness) →
  `production/phase7_minimal_launch_foundation.py`;
- Decimal money parsing / serialization / division / rounding → `core/money.py`;
- canonical JSON / content hash / atomic write / credential guard → the same reused helpers.

## Owner policy (`phase7-1e-owner-policy-v1`)
Nothing is silently enabled or defaulted (only schema-safe, non-business metadata is fixed). A template
is never a confirmed policy. Every money/rate field is a canonical **Decimal string** (float and bool
are rejected). The owner controls: campaign roles, match types, negative match types, the starting-bid
method and values, the total daily budget, the budget-allocation method and role allocations, the
reserve, the minimum campaign budget, target limits, evidence thresholds, the negative policy, and
explicit inclusions/exclusions.

- **Roles** — only `MANUAL_EXACT` / `MANUAL_PHRASE` / `MANUAL_BROAD` are planned. `AUTO_RESEARCH` and
  `PRODUCT_TARGETING` are recognized but stay **disabled** unless the owner explicitly authorizes them.
- **Match types** — `EXACT` / `PHRASE` / `BROAD`, none enabled by default. Exact demands the strongest
  direct relevance + evidence; Broad additionally needs explicit permission, low ambiguity, and an
  owner-approved exploratory budget.
- **Negatives** — `NEGATIVE_EXACT` / `NEGATIVE_PHRASE` only (no negative Broad), disabled by default,
  and **never auto-derived** from unselected / low-priority / low-volume / absent terms.
- **Bid methods** — `OWNER_FIXED`, `CEILING_SHARE`, or `TIERED_EVIDENCE`; no method by default and no
  zero / fixed-CPC fallback.

## Target eligibility (all gates must pass)
A candidate is eligible only when: it normalizes to a valid term; it has source provenance; the Phase 6
allocation permits advertising; its evidence grade meets the owner threshold; product facts support the
relevance; claim evidence is compatible; compliance flags pass; no owner exclusion applies; and at least
one allowed match type qualifies. **Title placement or search volume alone never qualifies a term**, and
an unallocated / `OWNER_FACT_REQUIRED` / unsupported-claim term is never eligible unless the owner policy
explicitly allows unallocated terms.

## Bids are bounded by the economic ceiling
The accepted 7.1M `maximum_cpc` is an **economic CEILING, never an automatic bid**. `OWNER_FIXED`
validates the explicit bid against the ceiling and the owner min/max; `CEILING_SHARE` uses
`maximum_cpc * owner_share`; `TIERED_EVIDENCE` uses `maximum_cpc * owner_share_for_tier`. There is no
hardcoded share, no default tier, and no fallback.

## Budget reconciles exactly
`SHARES` — role shares sum to exactly 1.00; `allocatable = total − reserve`; role budgets use
deterministic largest-remainder rounding so the rounded role budgets sum **exactly** to allocatable.
`FIXED_AMOUNTS` — role amounts + reserve equal the total exactly. Invalid / non-positive /
minimum-violating values block. No fixed percentages, no universal $25.

## Readiness (most restrictive wins)
`PHASE7_1E_PREFLIGHT_BLOCKED` → `PHASE7_ECONOMICS_BLOCKED` → `PHASE7_CAPACITY_BLOCKED` →
`PHASE7_HANDLING_BLOCKED` → `PHASE7_BUDGET_BLOCKED` → `PHASE7_OWNER_INPUT_REQUIRED` →
`PHASE7_OWNER_POLICY_REQUIRED` → `PHASE7_TARGET_EVIDENCE_BLOCKED` → `PHASE7_PLAN_VERIFICATION_BLOCKED` →
`SESSION7_1E_PLAN_READY_FOR_OWNER_REVIEW`. Phase 7.1E **never** sets `ready_for_manual_launch` or
`ready_for_amazon_action` (both always false).

## Local artifacts (gitignored, under `runs/T2/phase7/7.1E/`)
- **When ready**: owner-policy validation, input manifest, target candidates, selected targets, negative
  candidates, selected negatives, campaign-role plan, bid plan, budget plan, manual-entry worksheet CSV,
  manual-entry guide, readiness, verification, and manifest — written to `candidate/`, verified, then
  atomically promoted to `final/` (the previous `final/` is preserved in `last_valid/`).
- **When blocked**: only the owner-input-required guide, readiness, verification, and manifest. A
  misleading "ready" worksheet is never generated when inputs are absent.

The worksheet always states:
`LOCAL PLANNING WORKSHEET ONLY — NOT UPLOADED TO AMAZON — OWNER REVIEW AND MANUAL ENTRY REQUIRED`, and no
row is owner-approved by default.

## Exact next steps to reach a plan
1. Bring Phase 7.1M to a verified, viable foundation (complete the live product state, PPC contract, and
   economics for the advertised live SKU — the listing must be genuinely live and publishable first).
2. Complete `OWNER-POLICY.template.json` and set `owner_confirmed=true` with a tz-aware timestamp.
3. Supply the Phase 6 keyword source + allocation advertising feed, product facts, and claim evidence.
4. Re-run this engine; when everything verifies, it emits the plan **for owner review** — then the owner
   manually enters the approved rows into Seller Central.
