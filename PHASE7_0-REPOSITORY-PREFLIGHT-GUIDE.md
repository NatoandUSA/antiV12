# Phase 7.0 — Repository, Phase 6 Handoff, Amazon Boundary & Live-Product Preflight

**Session status:** `PHASE7_OWNER_INPUT_REQUIRED` · **7.1M readiness:** `PHASE7_OWNER_INPUT_REQUIRED`

Phase 7.0 is a **bounded, read-only preflight**. It inspected the actual repository and the verified
Phase 6 handoff and produced executable evidence about readiness for a *Release 7.1M implementation
session*. It **did not** implement 7.1M: no PPC target, negative, bid, budget, economics, or launch
package was created. The single authority is `production/phase7_preflight.py` (thin — it reuses the
existing serialization, hashing, atomic-write, Phase 6F verifier, and network-scan authorities).

## Result in one line
The Phase 6 package is a **cryptographically verified SAFE DRAFT**, not a PPC-ready live product. A
7.1M launch stays blocked until the owner completes the live-listing PPC product contract.

## What was verified (green)
- **Phase 6F proof + final status** parse and agree; `phase6_final_status = PHASE6_SAFE_DRAFT_READY`.
- **Phase 6 package** re-verified from actual bytes: `PASS_WITH_WARNINGS`, 22/22 entries, index hash
  file `PASS`; the five 6A–6E dependency manifest hashes recompute and match; `PACKAGE-INDEX.sha256`
  verifies. `COPY NOW = 0`, `UPLOAD NOW = 0`.
- **Permanent Amazon boundary:** 0 active prohibited account paths; no SP-API / MWS / Advertising-API
  client, no browser automation, no credential/cookie/session/token store, no automated report
  retrieval. Dashboard binds **loopback-only** (`127.0.0.1`). Every immutable Amazon-boundary policy
  flag is closed in every connectivity mode.
- **Authority map:** every declared capability resolves to exactly one active authority (or an explicit
  gap); no divergent duplicate production authority; **no PPC authority exists** (economics_gate is a
  feasibility margin gate, not a PPC engine); legacy/prototype paths are not promoted.
- **Official Amazon Ads guidance snapshot:** dated `2026-07-18`, official domain `advertising.amazon.com`
  only, 12 topics, conflicts/locale caveats preserved. Values (e.g. the daily-budget 25% intraday
  overage) are **not hardcoded** — the owner must revalidate them live before 7.1M/7.2.

## Why 7.1M is blocked — missing owner inputs
The PPC product contract is **template-only**. Ten fields are Phase-6-derived (`VERIFIED`); the
remaining owner-confirmation fields are `MISSING` and were never fabricated:
advertised ASIN / SKU / variant, live + buyable confirmation, listing-version-matches-Phase-6, price +
currency, FBM capacity, handling time, advertising eligibility, Featured Offer state, and the dated
owner confirmation. `current_selling_price` must be supplied as a **Decimal-compatible string** (the
repo has no Decimal money convention yet — 7.1M must introduce it and never convert a Decimal to float).

## Boundary counters
External Amazon account attempts: **0** · Amazon account actions: **0** · 7.1M artifacts: **0** ·
later-phase (7.1E/7.2/7.3/7.4) artifacts: **0**. The official-guidance research was performed by the
operator's human-triggered public web research (advertising.amazon.com only); the toolkit itself opened
no network (`core/amazon_docs_contract.py` ships an empty verified-official allowlist, so it denies
every Amazon URL).

## Artifacts
- **Local** (under `runs/<id>/phase7/7.0/`, gitignored): the 8 required artifacts + owner-input and
  preflight guides + the `PPC-PRODUCT-CONTRACT.template.json`.
- **Committed** (sanitized — prefixes, counts, states, reason codes only): `SESSION7_0-PROOF-GATE.json`,
  this guide, `production/phase7_preflight.py`, its tests, and the committed guidance snapshot fixture
  `tests/fixtures/phase7/official-guidance-snapshot.json`.

## Determinism
Two runs with identical local inputs produce identical stable content hashes; `LOCAL_SAFE` and
`TEST_DENY_EXTERNAL` repository outputs match; `CONNECTED_RESEARCH` changes the guidance snapshot only
on an explicit, recorded refresh.

## Next action for the owner
Complete `PPC-PRODUCT-CONTRACT.template.json` from live Amazon state (no raw credentials/cookies/tokens/
exports). Once the listing is live, buyable, ASIN/SKU-confirmed, version-matched to the verified Phase 6
package, and priced — with capacity, handling time, advertising eligibility, and Featured Offer state
confirmed — a later session can validate the contract and the readiness decision can advance. Until
then, Release 7.1M does not begin.
