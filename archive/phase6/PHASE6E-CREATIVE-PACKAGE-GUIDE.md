# Phase 6E — Creative Production Package (Owner Guide)

## What Phase 6E does
Phase 6E turns the verified Phase 6C product page and Phase 6D A+ draft into an **evidence-gated
creative plan**: ten stable creative "job" records, a listing-image plan, listing and A+ image
**prompt instructions**, a real-photo shot list, a creative asset manifest, cross-image consistency
rules, and a creative audit. It is produced by one authority — `creative/creative_production_package.py`
— which reuses the Phase 5/6 engines (product facts, atomic claims, category policy, the A+ builder,
and the shared PageAuditor). It never re-implements them.

## Why it creates plans and prompts but NOT images
Phase 6E **never** generates an image file, calls an image-generation service, or touches your Amazon
account. It writes planning documents only. Real photographs must be taken by you; AI mockups may be
planned only where they prove nothing physical. The owner remains the only manual bridge to Seller
Central.

## The exact ten creative jobs (fixed order)
1. `MAIN_PRODUCT` — the real garment as the main image
2. `REAL_DECORATION_PROOF` — a macro photo proving the real decoration
3. `PERSONALIZATION_GUIDE` — verified personalization options + a real example
4. `SIZE_AND_FIT` — verified sizes/measurements
5. `COLOR_AND_GARMENT_OPTIONS` — the real offered colours/garments
6. `LIFESTYLE_RECIPIENT` — a safe recipient/use scene
7. `OCCASION_WHEN_SUPPORTED` — an occasion, only if independently verified
8. `PRODUCT_DETAILS_AND_CARE` — verified material/care from a real label
9. `HOW_TO_ORDER` — a verified ordering/personalization workflow
10. `VERIFIED_COMPARISON_OR_VARIATION` — real comparisons/variations

### Why all ten records exist
Every job is always present so the workflow, blockers, requirements, future filenames, lineage, and
status are visible — even when a job cannot yet produce an image. Ten records do **not** mean ten
ready images.

### Why most jobs are blocked for T2
The product facts are still owner-required and no real product photo is verified. So for **T2**:
- **PROMPT_READY: 1** — only `LIFESTYLE_RECIPIENT` (a non-evidentiary nurse scene anchored on the one
  verified claim, "For nurses.").
- **REAL_PHOTO_REQUIRED: 2** — `MAIN_PRODUCT`, `REAL_DECORATION_PROOF` (a real photograph is the deliverable).
- **OWNER_FACT_REQUIRED: 5** — `PERSONALIZATION_GUIDE`, `SIZE_AND_FIT`, `COLOR_AND_GARMENT_OPTIONS`,
  `PRODUCT_DETAILS_AND_CARE`, `HOW_TO_ORDER` (you must declare the facts first).
- **NOT_APPLICABLE: 2** — `OCCASION_WHEN_SUPPORTED`, `VERIFIED_COMPARISON_OR_VARIATION` (no verified
  occasion / no real variations).

## Prompt readiness vs asset readiness (kept separate)
- **Prompt readiness** = can we safely write an AI prompt now?
- **Asset readiness** = does an approved, real (or generated) file exist?

A job can be prompt-ready with **no** asset (the lifestyle scene), and it can need a real photo while
its owner facts are still missing. A prompt-ready plan is never proof and is never upload-ready.

## Generated mockup vs real product proof
A generated / AI image can **never** prove decoration, embroidery, print method, material,
measurements, fit, colour accuracy, garment source, packaging, production, customization, offered
variations, or care. Those require a **real photograph** of your finished product. The package records
`generated assets accepted as real proof = 0`; any other value is a hard failure.

## How to read each file (under `runs/T2/phase6/6E/`)
- **LISTING-IMAGE-PLAN.json** — the ten job records with their state, blockers, and lineage.
- **LISTING-IMAGE-PROMPTS.md** — a full AI prompt appears only for a PROMPT_READY job; real-photo jobs
  point to the shot list; owner-fact / not-applicable jobs say "NO PROMPT GENERATED" with reasons.
- **APLUS-IMAGE-PROMPTS.md** — active A+ asset requirements and, separately, deferred Premium candidate
  assets. It truthfully contains **0** A+ generation prompts for T2.
- **REAL_PHOTO_SHOT_LIST.md** — exactly what real evidence to capture, with acceptance/rejection
  criteria. AI images are rejected as evidence.
- **CREATIVE-ASSET-MANIFEST.json**, **CREATIVE-ASSET-AUDIT.json**, **CROSS-IMAGE-CONSISTENCY.json**,
  **CREATIVE-ASSET-CHECKLIST.json**, **CREATIVE-BRIEF.md** — the asset picture, the audit, the shared
  visual identity, the owner checklist, and the human brief.

## Completing the checklist
Work `CREATIVE-ASSET-CHECKLIST.json` top to bottom: supply each accepted evidence type (supplier
specs, real photos, owner-confirmed facts). **AI mockups are listed as rejected evidence** for every
physical-proof item. Re-run the earlier phases after supplying facts/photos.

## Why category identity stays US:apparel
The canonical category is `US:apparel` (display "apparel", marketplace US). Phase 6D reported the
display text only; the canonical id and marketplace never change silently. The category-policy
authority carries **no** image dimension/background rule, so image policy is marked
`CATEGORY_IMAGE_POLICY_UNVERIFIED` — confirm Amazon's current image rules in Seller Central rather than
trusting an invented value.

## What still remains
- **Owner facts:** garment type, colours, decoration method, design (reference/dimensions/placement),
  material, sizes, measurements, fit, care, packaging, production, personalization fields.
- **Real photos:** main garment, decoration macro, personalization example, colour swatch, size chart,
  garment label (and packaging if depicted).

## What Phase 6F will package
Phase 6F may compile a safe manual package that clearly separates safe draft copy, owner review,
missing facts, real-photo requirements, and do-not-paste content. `ready_for_6f = true` does **not**
mean images are upload-ready, A+ is eligible, or the listing is publishable. **You remain the only
manual bridge to Seller Central.**
