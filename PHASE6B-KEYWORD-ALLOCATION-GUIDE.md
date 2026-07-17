# Phase 6B — Keyword Allocation Guide (owner-facing)

This explains what Phase 6B does, in plain terms, so you can read the coverage report and know what
happens next.

## What Phase 6B does

Phase 6B is the **planning** stage. It takes the keywords you already researched (the frozen
`MASTER-KEYWORDS-LEAN.json`) and the completed Phase 6A workspace, then:

1. Picks the **most relevant, safe** keywords for your product.
2. Ranks them with an explicit, repeatable scoring policy (no AI guessing, no randomness).
3. **Allocates** them into the exact 13 listing fields as a **plan** — a suggestion for the copy
   engines to use later.

Phase 6B does **not** write your title, bullets, description, backend terms, A+, or images. It writes
a *map* of which keyword belongs in which field, and why. Think of it as the blueprint, not the house.

## "Top relevance" — what it means

Not every eligible keyword is worth putting in your listing. "Top relevance" is the ranked shortlist
of keywords that (a) come from your paid research, (b) are safe to use, and (c) genuinely describe
**this** product. The ranking rewards your core product identity (a nurse sweatshirt), your audience
(nurses and nurse roles), and the verified gift/recipient angle — and it penalises repeats of the same
idea so the listing does not stuff the same word over and over.

## The exact 13 fields

| # | Field | What it is |
|---|-------|------------|
| 1 | title_primary | the single strongest core phrase for the title |
| 2 | title_support | up to 2 extra title concepts that add new meaning |
| 3 | bullet_1 | bullet job: product + personalization |
| 4 | bullet_2 | bullet job: embroidery / decoration proof |
| 5 | bullet_3 | bullet job: fit / size / colour / order accuracy |
| 6 | bullet_4 | bullet job: care / production / packaging / delivery |
| 7 | bullet_5 | bullet job: recipient / occasion / use |
| 8 | description | broader set of safe concepts for the description body |
| 9 | backend_candidates | hidden search-term candidates (the backend optimizer trims later) |
| 10 | item_highlights | category-gated "at a glance" attributes |
| 11 | aplus | enhanced A+ content |
| 12 | listing_image_text | planning note for image overlay text (no image is made) |
| 13 | image_alt_text | planning note for accessibility alt text |

The five bullets map to the **existing** five bullet jobs in the toolkit — Phase 6B did not invent a
new bullet system.

## Why "unallocated eligible" is kept separate

Some safe keywords are good but say the same thing as one already chosen (for example
"nurses sweatshirt" vs "nurse sweatshirt", or "sweatshirt for nurses"). Rather than throw them away or
stuff them in, Phase 6B keeps them in a **separate holding list** with the reason they were not used
(usually "a stronger variant was already selected"). Nothing is hidden.

## Why some fields are empty — and that is correct

For the T2 nurse sweatshirt, most fields that describe a **physical fact** are intentionally **empty**,
because you have not yet supplied verified owner facts:

- **bullets 2, 3, 4** (decoration, fit/size/colour, care/production) → empty, reason
  `CLAIM_EVIDENCE_MISSING`.
- **item_highlights** → empty, reason `CLAIM_EVIDENCE_MISSING` (the apparel category supports it, but no
  verified attribute exists to anchor a highlight yet).
- **A+** → empty, reason `OWNER_FACT_REQUIRED`.

An empty field with a clear reason is a **correct** result, not a gap to be padded.

## Why allocation is only advisory

The keyword allocation is a **recommendation**. When Phase 6C runs, the real title / bullet /
description / backend / item-highlights / A+ engines take this plan as input and re-check every concept
against your product facts and claim evidence. The **PageAuditor** has the final say. Nothing in the
allocation can force a keyword into published copy.

## Why allocation does not make a fact "true"

Your keywords tell us the **market** you are in (a personalized nurse sweatshirt). They do **not** prove
the physical product. "Sweatshirt" and "nurses" can drive relevance, but:

- unknown material stays unknown,
- unknown decoration method stays unknown,
- unknown personalization stays unknown,
- unknown size / colour / care stay unknown.

So a keyword like **"embroidered nurse sweatshirt"** or **"personalized nurse sweatshirt"** is held back
(`OWNER_FACT_REQUIRED`) — it is a fine keyword, but it claims something (embroidery, personalization)
that is not yet verified. It cannot go into visible copy until you supply the fact and the real proof.

## How risky and unsupported terms are excluded

Two layers protect you:

1. **Source layer** — your research already rejected wrong-audience, wrong-product, wrong-occasion,
   brand/IP, and malformed terms. Phase 6B never touches those.
2. **Physical-claim layer** — from the remaining eligible keywords, Phase 6B holds back any term that
   asserts an unverified material, decoration, colour, size, personalization, or occasion, and any term
   for a different garment (e.g. "nurse hoodie" when the product is a sweatshirt).

Every excluded and every held-back term is counted and reasoned in the coverage report.

## How to read the coverage report

`KEYWORD-COVERAGE-REPORT.md` (in your local run folder) walks through: your identity, the source hash,
the 6A dependency, how many keywords were eligible / safe / held back, what landed in each field, which
fields are empty and why, and the safety statement. Start at section 7 (allocation by field) and 8
(empty fields), then read section 16 (physical-claim restrictions) and 21 (safety statement).

## What Phase 6C will do next

Phase 6C takes this safe allocation plan and, using the existing engines, drafts the actual listing
copy — **still** as a `SAFE_DRAFT_OWNER_FACTS_REQUIRED` listing until you supply the missing owner facts
and real photos. The strongest next action for you is to answer the Phase 6A owner questions (material,
decoration, colours, sizes, personalization fields) and provide the real product photos; that unlocks
the currently held-back keywords for safe, honest copy.

## Why Phase 6B does not generate final listing copy

Copy is only trustworthy once facts and evidence are in place and the PageAuditor has checked it.
Phase 6B deliberately stops at the plan so that no unverified claim can slip into a published listing.
The listing is still **`SAFE_DRAFT_OWNER_FACTS_REQUIRED`**, and no copy was written in this stage.
