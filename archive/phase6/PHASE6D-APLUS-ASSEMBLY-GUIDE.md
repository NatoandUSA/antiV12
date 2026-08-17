# Phase 6D — A+ Content Assembly (owner guide)

Plain-language guide to what Phase 6D produces for product **T2** (a nurse sweatshirt), why almost
everything is still held, and what you need to supply next. Nothing here is published to Amazon — you
remain the only manual bridge to Seller Central.

## What Phase 6D does

Phase 6D takes the verified Phase 6A product facts and atomic claim evidence and assembles a **truthful
A+ Content package**: a Basic A+ result, a Premium A+ *draft*, a module-by-module asset-requirement
manifest, and a compliance report. It reuses the existing A+ builder and module registry — it does not
invent a second A+ engine, and it never guesses.

It writes five files under `runs/T2/phase6/6D/`:

1. `BASIC-APLUS-CONTENT.json` — the five Basic A+ modules and their evidence state.
2. `PREMIUM-APLUS-DRAFT.json` — the Premium journey as a clearly-labelled, non-publishable draft.
3. `APLUS-ASSET-MANIFEST.json` — what each module needs (real photo? owner fact?) before it can be built.
4. `APLUS-COMPLIANCE-REPORT.json` — the safety scorecard.
5. `STAGE-6D-MANIFEST.json` — hashes + state for the whole stage.

## Basic vs Premium A+

- **Basic A+** is the five-module story every eligible seller can use (hero, personalization, how-to,
  fit/size/colour, care/FAQ).
- **Premium A+** adds up to two extra "dynamic" modules (lifestyle, comparison, garment options, etc.)
  and needs a higher eligibility tier. Premium always carries the complete Basic content as a fallback.

## Why A+ eligibility must be verified

Amazon grants Basic or Premium A+ per account/brand. We have **not** been told your A+ eligibility, so
the honest state is **UNKNOWN** — not "Basic". The tool never silently assumes you have A+ access.
Because of that, the Basic result is a **safe draft** and the Premium result is a **draft only**.

## Why a draft is not publishable

A structurally complete A+ object is *not* the same as publishable content. Every module still has to
pass its own evidence gates. For T2 those gates are not met yet, so:

- Basic A+ state: **ELIGIBILITY_UNVERIFIED**
- Premium A+: **DO_NOT_PUBLISH** (never-publishable draft)
- Listing: **SAFE_DRAFT_OWNER_FACTS_REQUIRED**

## Why each module has separate fact and asset gates

Every module is judged **on its own**, not by a single global switch:

- **Hero / decoration** needs the verified decoration method **and** a *real* macro photo of the stitching.
- **Personalization gallery** needs the verified personalization fields **and** a *real* example photo.
- **How to customize** needs the verified personalization workflow.
- **Fit / size / colour** needs verified sizes and colours **and** a *real* garment/colour photo.
- **Care / production FAQ** needs at least one independently verified care/production fact.

Because none of these facts are verified for T2 yet, all five modules are **OWNER_FACT_REQUIRED** and
their customer-visible copy is intentionally **empty**. The module headline you see in the file (e.g.
"See the Real Decoration") is a template label kept in metadata — it is never shown to a customer while
the module is blocked.

## Why current T2 modules stay empty or blocked

The only thing verified for T2 is the **recipient** ("For nurses."). Everything physical — decoration,
material, size, colour, fit, care, production, packaging — is still UNKNOWN, and personalization, gift
and occasion are blocked. Empty-but-present modules are the correct, honest result: the structure is
ready, the evidence is not.

## Why AI mockups do not prove physical product facts

An AI or generated image can help you *plan* a layout, but it can **never** prove a real physical fact.
A generated mockup cannot prove stitch quality, material, measurements, colour accuracy, packaging, the
production process, or that a personalization comes out exactly as typed. Those require a **real photo**.
In this package, generated assets satisfy **zero** real-proof gates (count: 0).

## Why recipient evidence does not prove personalization or gift claims

"For nurses." is a verified *audience/recipient* statement. It does **not** verify that the product is
personalized, that it is a gift, or that it suits any occasion — each of those is a separate claim that
needs its own evidence. So the tool will not turn "for nurses" into "personalized nurse gift".

## How to read the files

- `BASIC-APLUS-CONTENT.json` → `modules[]`: each has `publishability` (OWNER_FACT_REQUIRED here),
  `owner_fact_dependencies`, `real_asset_dependencies`, and empty `headline`/`body_copy` while blocked.
- `PREMIUM-APLUS-DRAFT.json` → `never_publishable: true`, the complete `basic_fallback` (positions 1–5),
  and `rejected_dynamic_modules` explaining why each of the eight dynamic modules is not eligible.
- `APLUS-ASSET-MANIFEST.json` → `assets[]`: `status` (REAL_PHOTO_REQUIRED / OWNER_FACT_REQUIRED),
  `real_photo_required`, `generated_mockup_allowed`, and `prohibited_visual_claims`.

## What you still need to supply

**Owner facts** (verify these in your product-facts data): care instructions, colour options, decoration
method, handling time, packaging, personalization fields, production location, production time, shipping
method, size range, tracking.

**Real photos** (real product photography, not AI): a macro decoration/stitch shot, a personalization
example, a garment/colour reference — plus, for Premium, lifestyle, garment-option, design-variation,
production-process and gift-packaging photos if you want those dynamic modules.

## What Phase 6E will create

Phase 6E is the **creative production** stage. It will turn these module requirements into the actual
image plan and creative prompts (the ten-job listing-image contract) and the real-photo shot list.
Phase 6D deliberately stops before any prompt or image is generated.

## The bottom line

`ready_for_6e = true` means creative *planning* can proceed with the blockers spelled out. It does **not**
mean A+ can be published, that assets are ready, or that Premium eligibility exists. When the facts and
real photos are in hand, the same engines will lift these modules from draft to publishable — and you
still make the final call inside Seller Central yourself.
