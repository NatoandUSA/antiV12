# Pilot Report — one project, end to end (v2.4.0)

**Project:** Personalized Nurse Sweatshirt (FBM, machine embroidery)
**Goal:** run one project through every stage of the real tools, find what breaks before building v2.4.
**Data:** representative Helium 10 (Xray + Cerebro) + YTrends + economics. *Not your real export* — re-run
the identical steps with your own H10 files anytime; the workflow is the same.

## Result: the whole chain completed and UNLOCKED
`Publication locked: no · overall READY_FOR_REVIEW` — with no manifest editing, using only supported
commands. The pilot found **3 real bugs**, all fixed and covered by new tests, plus a friction punch-list
for v2.4.

## Stage-by-stage
| # | Stage | Command | Result |
|---|---|---|---|
| 1 | Init + scaffold gates | `--init-project`, `--scaffold-gate-files` | ✅ PASS |
| 2 | Provide H10 / YTrends / demand / economics | (files) | ✅ PASS |
| 3 | ASIN pick | `research/asin_picker.py` | ✅ PASS (9 ASIN) · minor: prints "short batch" advice even when full |
| 3 | Keyword master | `research/phaseA_master.py` | ✅ PASS |
| 3 | Seed expander | `research/seed_expand.py` | ⚠️ FRICTION — "no fresh sub-angles" (thresholds too strict for a small set) |
| 3 | Competitor gaps | `research/competitor_gap_analyzer.py` | ✅ PASS — top gap "Real embroidery proof" 95 (manual-review), "Review beatability" 94 |
| 3 | Keyword intelligence | `research/keyword_intelligence.py` | ✅ PASS — top "custom nurse gift" 68 HIGH (Amazon 58 + trend 97) |
| 4 | Structured listing | `listing/listing_generator.py` | 🐞→✅ found validator crash (see BUG-1); title 69/75 |
| 5 | Economics + gates | `pipeline.py` | ✅ PASS — DEMAND/ECONOMICS GO; 6 file-gates GO/APPROVED |
| 5 | IP screen on listing.json | (pipeline) | 🐞→✅ was REVISE on benign words (see BUG-3) |
| 6 | Real photos + reviews | (creative-brief + hash-bound reviews) | ✅ PASS |
| 7 | Creative edge | `creative/creative_edge.py` | ✅ proof PROVEN · consistency CONSISTENT · main image COMPLIANT |
| 8 | Owner approval chain | `--approve-main-image → --approve-creative → --approve-final` | ✅ PASS — all hash-bound |
| 9 | Unlock | `--status` | ✅ Publication locked: **no** |

Generated title: `Custom Nurse Gift Crewneck Sweatshirt Embroidered Personalized Nurses` (69/75).

## Bugs the pilot caught — all fixed, with regression tests
**BUG-1 (blocker) — validator crashed on A+ modules.** `listing_validate.py` assumed A+ content was a
list of strings, but the generator/AI produce `{headline, copy}` dicts → `AttributeError: 'dict' object
has no attribute 'lower'`, which made LISTING_ACCURACY = BLOCKED. Fixed: A+ modules are normalized to
text before screening. This would have blocked *every* generated or AI-built listing.

**BUG-2 (quality) — backend wasted space.** The generator's backend search terms repeated words already
in the title (Amazon ignores those). Fixed: backend now excludes title words (`week practitioner women rn
graduation` instead of re-listing `nurse/gift/embroidered/...`).

**BUG-3 (design flaw) — IP guard cried wolf.** It marked *every* unrecognized English word (cotton-blend,
satin, stitches, checkout, typical…) as REVIEW, so a normal listing could never pass IP — training owners
to ignore the tool. Fixed: unrecognized tokens are now **informational** (still listed for a manual
eyeball), while the curated 450+ brand/character BLOCK library and the named risky-phrase REVIEW list still
govern the verdict. Verified: a clean listing → OK, "Disney Mickey Mouse" → still BLOCK.

143/143 tests pass (6 new pilot-regression tests).

## Friction found (not blockers) — the v2.4 punch-list, ranked
1. **Seed expander too strict** — returned "no fresh sub-angles" on a real keyword set; loosen thresholds
   and always surface the best N even if below cutoff (label them as lower-confidence).
2. **ASIN picker messaging** — prints the "short batch, re-Xray" advice even when it found 8–9 ASINs;
   only show it when the batch is actually short.
3. **Title quality polish** — the generator front-loads well but can end on an audience noun ("...Nurses")
   and still trips the config's `gift`-in-title and multi-garment warnings. Make the generator config-aware
   (avoid `gift` in title when the category warns; collapse "crewneck sweatshirt" so it isn't read as two
   garments — a validator substring false-positive on "swea**tshirt**").
4. **No owner "IP reviewed & cleared" record** — now less urgent (unknowns are informational), but a
   one-line owner acknowledgment of the listed unknown tokens would close the loop cleanly.
5. **Economics is single-scenario** — the reviewer's P1 ask stands: best/base/worst cases in the gate.

## What this proves (and doesn't)
Proves: the discovery → intelligence → gaps → structured listing → economics → creative/proof → approval
→ unlock chain works end to end on one project, with the safety model intact (blank/AI images rejected,
real brands blocked, hash-bound approvals, no Seller Central). Does **not** prove real-world sales — that
needs your actual product, real photos, and a live launch. The next real test is you running these exact
steps with your own H10 export and one real embroidery macro.

## Recommended next
Ship these pilot fixes (they're in this build), then build **v2.4.0-RC3**: the friction punch-list above
(items 1–3 are quick) + the PPC search-term module we scoped. The pilot says the backend is sound enough
to keep building on.
