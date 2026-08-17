# Phase 6F — Verified Safe-Draft Seller Central Manual Package (Owner Guide)

Phase 6F is the **final** Phase 6 step. It takes everything Phases 6A–6E verified and assembles it into
one local folder you can work from by hand: **`runs/<product>/phase6/6F/SELLER-CENTRAL-READY/`**.

It writes **nothing** to Amazon. The toolkit never logs in, never uses SP-API/MWS/Advertising API, never
automates a browser, and never edits a listing, image, A+, price, inventory, or PPC value. **You are the
only bridge to Seller Central.**

## Why the folder says "SELLER-CENTRAL-READY" but the package is a safe draft

The folder name describes the *destination format*, not the *authorization*. For the current T2 product the
package is a **SAFE DRAFT**: it is structurally complete and its hashes verify, but the listing is **not**
publication-ready. `00-READ-ME-FIRST.md` states this on its very first lines:

```
PHASE6_SAFE_DRAFT_READY

DO NOT PASTE OR UPLOAD THIS PACKAGE TO SELLER CENTRAL YET
```

## Why COPY NOW is empty and UPLOAD NOW is empty

A field only reaches **COPY NOW** when the *whole* listing resolves to `PUBLISHABLE`, and an asset only
reaches **UPLOAD NOW** when a real, verified image exists. The most restrictive applicable state always
wins — a "lexically clean" title does **not** make the listing publishable while owner facts, real photos,
and A+ eligibility are still missing. For T2 every field is still a safe draft, so:

- **COPY NOW: 0 items**
- **UPLOAD NOW: 0 items**

That is a correct, successful result — not a failure.

## The six owner-facing labels

| Label | Meaning |
|---|---|
| **COPY NOW** | Field text you may paste into Seller Central right now. (T2: none.) |
| **UPLOAD NOW** | An image/A+ asset you may upload right now. (T2: none.) |
| **OWNER REVIEW** | Safe drafts and plans to read — reference only, do not paste. |
| **DO NOT PASTE** | Current draft copy, advisory backend terms, blocked A+ — never paste. |
| **REAL PHOTO REQUIRED** | A real photograph you must capture. AI images are never accepted. |
| **A+ ELIGIBILITY UNVERIFIED** | A+ access (Basic/Premium) is not confirmed. |

## How to use OWNER REVIEW and DO NOT PASTE

- **OWNER REVIEW**: read `13-SELLER-CENTRAL-MANUAL-ENTRY-PLAN.md` (the authoritative plan),
  `LISTING-COPY-READY.txt`, the image plans, and the shot list. These are drafts and instructions.
- **DO NOT PASTE**: `01-TITLE.txt`, `02-BULLETS.txt`, `03-DESCRIPTION.txt`, `04-BACKEND-SEARCH-TERMS.txt`,
  `05-ITEM-HIGHLIGHTS.txt`, `06-PERSONALIZATION-INSTRUCTIONS.txt`, `PRODUCT-DETAIL-PAGE.json`,
  `BACKEND-SEARCH-TERMS.json`, and the two A+ drafts. They are safe drafts or blocked references — never
  enter them into Seller Central yet.

## How to complete the owner facts

Open `OWNER-INPUT-REQUIRED.json`. Supply each listed fact (garment, material, decoration method, sizes,
colours, fit, care, personalization fields/limits, production, packaging, shipping) with the accepted
evidence. **No fact is ever invented for you** — an unverified fact stays blocked.

## How to complete the real-photo requirements

Open `11-REAL-PHOTO-SHOT-LIST.md`. Capture each of the **seven** real photographs of the actual finished
product. An AI-generated or digitally simulated image is **never** accepted as evidence.

## Why A+ eligibility stays unverified

Neither Basic nor Premium A+ access is confirmed. The Basic A+ draft has zero visible copy and is not
upload-ready; the Premium A+ draft is `DO_NOT_PUBLISH`. Confirm your A+ access before treating either as
usable.

## How PACKAGE-INDEX.json works

`PACKAGE-INDEX.json` is a **verification manifest**, not a copy source. It lists every packaged artifact
with its SHA-256, byte size, media type, and upstream source hashes. It never lists itself or
`PACKAGE-INDEX.sha256`, and it never embeds full titles, bullets, prompts, or private paths.

## How PACKAGE-INDEX.sha256 works

`PACKAGE-INDEX.sha256` holds the lowercase SHA-256 of `PACKAGE-INDEX.json` in the canonical
`<hash>  PACKAGE-INDEX.json` form with a single trailing newline.

## How to run the package verifier

```
python -m production.seller_central_package runs/<product>
```

This rebuilds the package into a candidate, verifies every hash, promotes it rollback-safely, and
re-verifies the promoted package. A safe-draft package verifies as **PASS_WITH_WARNINGS**.

## How to regenerate Phase 6 after facts and assets are supplied

Add the verified owner facts, capture the real photos, confirm A+ access, then re-run
Phase 6A → 6B → 6C → 6D → 6E → 6F. The gates re-evaluate automatically; only genuinely publishable content
can ever reach COPY NOW / UPLOAD NOW.

## Why there is no Amazon automation

By permanent policy (`docs/CONNECTIVITY-POLICY.md`) the toolkit is isolated from your Amazon account. There
is no Amazon credential store and no way to add one. Connected research is allowed; acting inside your
account is not.

## When PHASE6_READY_FOR_LOCAL_DEPLOYMENT may be used

Only when **all** owner facts are verified, **all** required real assets are verified, A+ eligibility is
verified, the category policy is satisfied, the package index passes, and COPY NOW / UPLOAD NOW contain
only genuinely authorized content. Until then the truthful status is **PHASE6_SAFE_DRAFT_READY**.
