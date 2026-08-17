# Evidence Semantics Report — v2.3.3

## The finding the audit was right about
v2.3.2 called embroidery proof "asset-backed" and treated that as PROVEN. But "asset-backed"
only checked that a **real, decodable image file** existed. It never checked **what the file
showed**. Reproduced directly: a blank 2000×2000 white PNG — no stitches, no thread, no
garment — decoded fine and reached `proof PROVEN`. An AI-rendered mockup would have too. That
is exactly the failure mode the whole toolkit exists to prevent (AI/blank images counting as
product proof). The overclaim was mine; the fix is below.

## The corrected principle
A decoded file is an **INPUT** to proof, not proof. Proof requires a human content review of
the actual pixels, bound to that specific file's hash so it can't be reused for a different image.

## What changed
1. **PROVEN now requires a hash-bound content review.** `build_embroidery_proof` requires
   `embroidery_proof_review` with `reviewer`, `reviewed_at`, `asset_hash` == the macro's
   SHA-256, and every confirmation true: individual_threads_visible, stitch_edges_visible,
   fabric_weave_visible, image_is_not_ai, image_is_not_blank, supplier_sku_matches,
   design_version_matches, thread_colors_match, placement_matches. Any missing/false flag,
   an undersized image, or a hash mismatch → **DRAFT_UNVERIFIED**. Verified end-to-end: the
   blank white PNG now returns DRAFT_UNVERIFIED, not PROVEN.
2. **Creative Edge writes to the manifest.** `creative_edge.py` main() now sets
   MAIN_IMAGE_COMPLIANCE, EMBROIDERY_PROOF, VISUAL_CONSISTENCY, ACTUAL_ASSET_EDGE in
   PROJECT-MANIFEST.json and exits 3 when publication is blocked. Closes the "two truths" gap
   where the creative report and the gate ledger could disagree.
3. **MAIN_IMAGE_COMPLIANCE passes on APPROVED only.** COMPLIANT is technical-only (no text/props,
   file decodes) — it is not owner sign-off, so it does not pass the gate.
4. **Consistency needs asset-backed specs.** Two or more image specs must each carry an
   `asset_hash` + `reviewed_by`; plan-only specs yield PLAN_CONSISTENT, never ACTUAL consistency.
5. **--approve-final refuses early approval.** Final approval is rejected unless every non-owner
   required gate already passes — it must be the last step, and it stays hash-bound (auto-invalidates
   if any approved file changes).
6. **--init-project derives gates from facts.** Product family / decoration method set which gates
   are required; embroidery projects automatically require EMBROIDERY_PROOF.
7. **External path containment.** asset_validator rejects absolute paths that escape the project
   bundle unless explicitly allowed.
8. **--status verifies approvals first**, so approval hash drift is caught on every status call.
9. **REAL-PHOTO-SOP.md fixed.** The old command doubled the path (`runs/nurse/macro.png` with
   `--project-dir runs/nurse` → MISSING). Corrected to a filename relative to --project-dir, and
   added Step 3.5, the hash-bound proof review.

## Tests
74/74 pass, including 9 new evidence-semantics tests (blank/tiny/AI image not proven, hash-mismatch
rejected, creative_edge updates the manifest, orchestration-error fallback, init-project requires
embroidery proof, plan specs are not actual consistency).

## Honestly still deferred (not built, on purpose)
- **Executable stage orchestration** (`--run-stage` / `--run-next` / `--run-all-ready` with real
  stage executors). Today `--status` / `--next` navigate stages and recommend one action; the owner
  runs each tool. Turning that into a one-command runner is a larger build.
- **Web dashboard / import center / database.** Every audit says defer these until the backend
  evidence layer is trustworthy. It now is, but the UI remains a separate program.
- **No computer vision.** The tool never "looks" at an image to judge embroidery — that judgment is
  the owner's content review. This is a deliberate honesty boundary, not a missing feature.

## What this does NOT claim
It does not claim to detect a fake or AI image automatically. It forces a human to look and sign off,
binds that sign-off to the exact file, and invalidates it if the file changes. That is the safe,
realistic version — not automated proof.
