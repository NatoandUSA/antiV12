# Audit Response — v2.3.4-RC

Two independent reviews of v2.3.3 landed together. The lighter one confirmed the evidence-semantics
fix was the right call and said the next bottleneck is execution discipline (shoot real photos), not
more code. The critical one accepted v2.3.3 as a strong pilot but reproduced, outside the shipped
tests, a set of alternative paths that were weaker than the hardened macro path. It was right. Every
P0 it raised was reproduced against the actual code before fixing.

## Findings reproduced, then fixed

| # | Finding (reproduced) | Fix in v2.3.4-RC |
|---|---|---|
| P0.1 | Blank supplier PNG + `supplier_evidence_reviewed:true` → PARTIALLY_PROVEN | Supplier proof needs a hash-bound `supplier_reference_review` (identity, SKU, design, thread, placement, not-AI, not-blank, visible embroidery); blank/tiny/stale → DRAFT_UNVERIFIED |
| P0.2 | 1×1 main image → COMPLIANT | Main-image compliance requires a quality-acceptable image; QUALITY_REVIEW_REQUIRED → INCOMPLETE |
| P0.3 | 1×1 main image → 89/100 actual score | Actual-asset score rejects QUALITY_REVIEW_REQUIRED before scoring; a human score can't override a file-quality failure |
| P0.4 | Invented spec hashes → CONSISTENT | Each spec must resolve to a real file whose current hash matches asset_hash; else PLAN_CONSISTENT / flagged |
| P0.5 | Creative Edge printed "Publication clear" while main-image gate INCOMPLETE | Creative Edge reports only CREATIVE PACKAGE COMPLETE/REVIEW REQUIRED/BLOCKED; publication verdict belongs to `pipeline --status` |
| P0.6 | No CLI to reach MAIN_IMAGE_COMPLIANCE=APPROVED or CREATIVE_OWNER_APPROVAL=APPROVED | New `--approve-main-image` and `--approve-creative`, hash-bound, gate auto-falls-back on invalidation |
| P0.7 | `--approve-final` recorded before creative approval | Final refuses unless every non-final required gate passes, including CREATIVE_OWNER_APPROVAL |
| P0.8 | File created/changed after final approval didn't invalidate | Final bundle derived from every gate's evidence file + assets + a hashed GATE-SNAPSHOT; editing any bound file invalidates final (verified) |
| P0.9 | VISUAL-CONSISTENCY.json not written | Written with evidence (spec counts, backed hashes, reviewers, tool version) |
| P1.2 | Rerun overwrote a completed thumbnail review | A completed THUMBNAIL-REVIEW.json is preserved |
| P1.5 | Output recommended the removed `--approve "<keyword>"` | Recommends the actual next approval command, in order |

Tests: 91/91 pass (17 new in `tests/test_evidence_v234.py`), including the audit's regression
targets — blank supplier photo, tiny main, fake/stale consistency hashes, missing creative approval,
changed main image invalidates its approval, and changed evidence invalidates final approval.

## Honestly deferred (not in this RC, on purpose)
Both audits agree these come after one real end-to-end pilot, and that the dashboard waits:
- **Executable stage orchestration** (`--run-stage` / `--run-next` / `--run-all-ready`). Today
  `--status`/`--next` navigate and recommend one action; the owner runs each tool.
- **P1.1** fuller main-image category-review schema (hash + timestamp + rule reference). Category
  review is required for APPROVED today, but its internal record is still light.
- **P1.3 / P1.4** running IP screening on a JSON-only `listing.json` and parsing
  claims/feasibility/fulfillment/catalog/personalization files into their gates from the central
  pipeline. These belong to executable orchestration.
- **Web dashboard / import center / database.** Every review says defer; the backend is the
  priority until a real project has gone through it once.
- **No computer vision.** The tool never “looks” at an image to judge embroidery — that judgment is
  the owner's hash-bound content review. This is a deliberate honesty boundary.

## The real next action (from the lighter review, and true)
Shoot one production main-image candidate and one embroidery macro, validate them, record the
hash-bound reviews, and walk one nurse project through the full chain. The physical assets create
the commercial value; this RC only makes sure they can't be misclassified, approved through a weak
path, or dropped from the final owner approval.
