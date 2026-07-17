# Phase 6C — Product Detail Page Assembly (Owner Guide)

Phase 6C turns your verified product facts, the one verified claim, and the advisory Phase 6B keyword
plan into a **truthful Product Detail Page draft** — using the same title, bullet, description,
backend, item‑highlight, and audit engines the toolkit already trusts. It invents nothing. Everything
is written to `runs/<product>/phase6/6C/` for your **manual** review; the toolkit never touches your
Amazon account.

## What Phase 6C does

1. **Verifies its inputs first.** It re‑checks the Phase 6A workspace and the Phase 6B allocation
   (hashes, source integrity, `ready_for_6c`). If anything upstream is missing, tampered, or drifted,
   it stops instead of guessing.
2. **Reconciles the keyword accounting** directly from keyword IDs (not the report numbers):
   `allocated_unique + unallocated_unique = selected_unique`.
3. **Runs the existing engines** on the *safe* market‑identity core and lets each engine produce copy.
4. **Applies one extra evidence gate** on top of the engines, then audits the whole page.
5. **Records an outcome for every allocated keyword** — nothing disappears silently.

## Why the Phase 6B allocations are advisory

The Phase 6B "keyword allocation map" is a **plan**, not a permission slip. It is stamped
`ADVISORY_ONLY`. A keyword being allocated to a field does **not** authorize dropping it into that
field. In Phase 6C every allocated concept must still pass: product‑fact evidence, claim evidence,
product / audience / occasion compatibility, category policy, the field engine's own rules, natural‑
language rules, duplication rules, the PageAuditor, and publishability. Whatever survives all of that
is what you see.

## How the copy is generated (and why some fields are empty)

The engines only publish a factual phrase when a **VERIFIED** claim backs it. For this product the only
verified claim is the **recipient/nurse audience**. Every physical attribute — material, colour, size,
fit, decoration/embroidery, personalization, care, production/shipping, occasion — is **UNKNOWN**, so:

- **Title:** a safe product‑identity draft (e.g. *"Nurse Sweatshirt"*). No material, size, colour,
  embroidery, personalization, or gift wording — none of those are verified.
- **Bullets:** exactly five bullet jobs are preserved. A bullet is filled **only** when its evidence is
  present. With no verified attributes, all five stay **empty, each with an explicit reason** (missing
  owner fact or missing claim evidence). Empty is correct — the toolkit will not invent filler.
- **Description:** natural product‑identity prose only. The engine's recipient sentence
  ("*A personalized gift for nurses.*") is **dropped**, because "personalized" and "gift" are not
  verified — a clean‑looking sentence is not the same as a verified one.
- **Backend search terms:** the existing byte‑safe optimizer builds a hidden‑search string within the
  category byte ceiling, preserving whole phrases and excluding risky / unsupported / duplicate terms.
- **Item highlights:** empty — the category supports the field, but no verified product‑attribute claim
  exists to anchor one (`CLAIM_EVIDENCE_MISSING`).

### The gift / personalization rule (important)

A keyword containing the word *gift* or *personalized* is **not** evidence that you make a gift or offer
personalization. Those are separate claims, and they are **not verified**. So gift / personalization
wording is deferred everywhere in visible copy until you supply the evidence. "Gifts for nurses" as a
hidden **search category** is fine; a visible **claim** that the item is a personalized gift is not.

## Why the listing is still a **safe draft**

A page can read cleanly and still not be publishable, because the facts behind it are missing. Phase 6C
reports two separate things:

- `listing_publishability_state = SAFE_DRAFT_OWNER_FACTS_REQUIRED` — truthful, but not yet publishable.
- `phase6c_state = SAFE_PDP_OWNER_FACTS_REQUIRED`, `ready_for_next_stage = true` — ready for the next
  **stage**, which is **not** the same as ready to **publish**.

A clean lexical audit never overrides this. Missing facts keep the listing a draft.

## How to read the outputs (`runs/<product>/phase6/6C/`)

| File | What it is |
|---|---|
| `PRODUCT-DETAIL-PAGE.json` | The assembled draft + every dependency hash, accounting, field state, allocation outcome, and audit summary. |
| `TITLE-OPTIONS.json` | Deterministic title candidates. Each has character + UTF‑8 byte counts, the concepts it used / rejected, the category‑policy check, and its own audit. At most one is `RECOMMENDED_SAFE_DRAFT`. |
| `BULLETS.json` | Five bullet records. Empty bullets stay present with a reason and the owner facts they need. |
| `DESCRIPTION.txt` | The plain‑text description draft. |
| `BACKEND-SEARCH-TERMS.json` | The final byte‑safe search‑terms string, its byte count vs the limit, and which advisory candidates were selected / rejected. |
| `ITEM-HIGHLIGHTS.json` | The item‑highlights evaluation (empty here, with the reason). |
| `PAGE-AUDIT-REPORT.json` | The full PageAuditor result over the combined page. |
| `FIELD-BLOCKERS.json` | Every blocked / deferred field and exactly what it needs. |
| `PHASE6C-COPY-LINEAGE.json` | The advisory‑to‑final ledger: every Phase 6B assignment's outcome. |
| `STAGE-6C-MANIFEST.json` | Byte hashes + stage state for verification. |

### Title states you may see
`RECOMMENDED_SAFE_DRAFT` (the one to use), `PUBLISHABLE`, `OWNER_FACT_REQUIRED`, `BLOCKED_UNSAFE`,
`REJECTED_DUPLICATION`, `REJECTED_NATURAL_LANGUAGE`, `REJECTED_CATEGORY_POLICY`.

### Bullet states you may see
`SAFE_DRAFT` (filled), or `EMPTY_OWNER_FACT_REQUIRED` / `EMPTY_CLAIM_EVIDENCE_MISSING` (empty, with the
missing facts named).

## What the PageAuditor checks

Every generated field and the combined page are audited for: wrong‑audience, wrong‑product, wrong‑
occasion, and brand/IP leakage; unsupported physical claims; promotion of blocked / owner‑review
claims; and fabricated owner facts. All of these must be **zero**.

## Fields deferred to a later stage

Phase 6C does **not** create A+ content, image‑overlay text, final image alt text, or image prompts.
These are carried forward as `aplus: DEFERRED_OWNER_FACT_REQUIRED`, `listing_image_text` and
`image_alt_text: DEFERRED_TO_LATER_STAGE`.

## What unlocks stronger copy

Supplying **verified** owner facts (with real evidence — a supplier spec, a real product/label photo,
not an AI mockup) turns blocked fields into publishable copy. The highest‑impact facts first:

1. **Garment material / fabric composition** — unlocks the material bullet, description, and A+.
2. **Sizes actually offered** and **real measurements** — unlocks fit/size copy and the size chart.
3. **Available colours** (real swatch) — unlocks colour copy.
4. **Decoration method** (real macro photo) — unlocks any embroidery/decoration claim.
5. **Personalization fields + character limits** (real personalized sample) — unlocks personalization.
6. **Care, production/handling time, shipping, packaging, occasion** — unlock the remaining bullets.

`FIELD-BLOCKERS.json` lists the exact owner facts and real‑asset photos each blocked field needs.

## What the next stage will do

The next stage consumes this **verified** safe draft (plus any owner facts you add) to plan the
creative layer — A+ content and images — under the same evidence gates. It will still never publish for
you: **you remain the only manual bridge to Seller Central.**
