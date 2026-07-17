# Phase 6A — Owner Facts Guide

Phase 6A builds **one trustworthy product workspace** before any keyword allocation or listing copy
is written. It is a **deterministic local computation** that needs no network and requests no
enrichment — its outputs are byte-identical whether the toolkit is in `CONNECTED_RESEARCH`,
`LOCAL_SAFE`, or `TEST_DENY_EXTERNAL` (verified in `tests/test_phase6a_three_modes.py`). It never
operates inside your Amazon account, never uses external AI, and never publishes anything. Any
connected research is advisory only and never becomes a verified product fact automatically —
product facts, claim evidence, and the PageAuditor remain authoritative. You remain the only manual
bridge to Seller Central.

## What Phase 6A does

For a selected product (a `runs/<id>` folder), Phase 6A reuses the Phase 5 engines to:

1. identify the product (from `PROJECT-MANIFEST.json` and the approved keyword source — never a guess);
2. load the authoritative keyword source (`MASTER-KEYWORDS-LEAN.json`);
3. normalize your product facts (unknown facts stay **unknown** — nothing is invented);
4. build evidence-classed claims (only verified facts can ever become publishable copy);
5. list the **owner facts** still needed;
6. list the **real assets** (photos/specs) still needed to prove physical claims;
7. decide whether the product is **ready for keyword allocation (Phase 6B)**.

It does **not** write a listing, allocate keywords, or create image prompts. Those are later stages.

## What you must provide

Phase 6A tells you exactly what is missing. Two kinds of input:

- **Owner facts** — verified attributes of your product (material, decoration method, sizes, colors,
  personalization options, care, shipping, etc.). Read them in `OWNER-INPUT-REQUIRED.json`.
- **Real assets** — actual photos or specifications that prove a physical claim (a real embroidery
  macro photo, a real garment photo, a real size chart). Read them under `real_asset_requirements`.

### What counts as evidence

Accepted, for example: a supplier specification sheet, a purchase invoice line, a real garment label
photo, a real embroidery macro photo, a measured size chart, an owner confirmation with a real sample.

**Not accepted as proof** (this is deliberate and cannot be overridden):

- an **AI-generated image or mockup**;
- a **design mockup / digital rendering**;
- a **generic competitor listing**;
- a **generic supplier catalog statement** not tied to your exact product.

An AI image can look like embroidery, but it cannot prove your real product's stitch quality, material,
fit, or personalization. Only a real photo or a real specification can.

## How to read `OWNER-INPUT-REQUIRED.json`

Each entry under `requirements` has:

- `priority` — `BLOCKING` (needed before that copy field can be published), `IMPORTANT`, or `OPTIONAL`;
- `fact_key` and `question` — what to provide;
- `reason` — why it matters;
- `accepted_evidence` / `not_accepted_as_proof` — what proves it and what never does;
- `downstream_impact` — which claims/fields it unblocks;
- `blocking_stage` — the later stage it gates (usually copy assembly, 6C);
- `status` — `OPEN` until you resolve it.

`resolved_requirements` lists facts you have **already verified** — you do not need to re-supply them.
`summary` gives the counts (blocking / important / optional / resolved / real assets required).

### Resolving a requirement

Put your verified facts into a `product-facts.json` file in the product's run folder, with a `status`
of `VERIFIED` (or `OWNER_CONFIRMED`) for each fact you can back with evidence. Re-run Phase 6A — the
resolved facts move out of the open list, their claims become supported, and the matching real-asset
requests are suppressed once the primary proof is present.

## Readiness: `READY_FOR_6B` is not "publishable"

Phase 6A records **two separate states**:

- `phase6a_workspace_state` — is the product ready to start **keyword allocation**?
- `current_listing_publishability_state` — is the **listing** safe to publish? (from Phase 5)

A product can be **ready for 6B while its listing is still a safe draft**. `READY_FOR_6B` means only
that product relevance is clear enough to allocate keywords — it does **not** mean the listing is
`PUBLISHABLE`. Missing owner facts and missing real assets do **not** block keyword allocation; they
gate the safe publishable **copy** in later stages.

Workspace states you may see:

| State | Meaning |
| --- | --- |
| `READY_FOR_6B` | Enough trustworthy info to begin keyword allocation. |
| `OWNER_FACTS_REQUIRED` | Ready for 6B, but owner facts are needed before safe copy. |
| `REAL_ASSETS_REQUIRED` | Ready for 6B, but real proof photos are needed before safe copy. |
| `OWNER_FACTS_AND_REAL_ASSETS_REQUIRED` | Ready for 6B; both owner facts and real assets are needed. |
| `BLOCKED_UNSAFE` | Unsafe/contradictory data — nothing proceeds until resolved. |
| `INVALID_SOURCE` | No usable authoritative keyword source. |
| `CONFLICTING_EVIDENCE` | Unknown product type/category or contradictory facts — 6B blocked. |

## How missing facts affect the later listing

Every physical claim (material, embroidery, fit, sizes, colors, care, shipping, personalization) stays
**blocked** until you supply the fact **and**, where physical, the real proof. Until then the affected
bullet, description section, A+ module, or product-detail field is a **safe draft** — never published.
Supplying facts and proof is what turns those safe drafts into publishable copy in later stages.

## Where the artifacts are

For a product `runs/<id>`, Phase 6A writes to `runs/<id>/phase6/6A/`:

- `OWNER-INPUT-REQUIRED.json` — what you must provide;
- `PRODUCT-READINESS-REPORT.md` — the plain-language summary;
- `NORMALIZED-PRODUCT-FACTS.json` — your facts, with each fact's state;
- `CLAIM-EVIDENCE.json` — each claim and whether it is supported;
- `PRODUCT-WORKSPACE.json` and `STAGE-6A-MANIFEST.json` — the workspace record and file manifest.

The `runs/` folder holds your paid research data and is kept local (never committed).

## Running it

```
python scripts/phase6a_build.py                 # default proof product: runs/T2
python scripts/phase6a_build.py runs/<id>       # your product
```

Re-running with identical inputs reproduces byte-identical artifacts (deterministic).
