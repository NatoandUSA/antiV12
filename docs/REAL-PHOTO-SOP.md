# Real Photo → Asset Validation → Creative Proof SOP (v2.3.4-RC)
Moves embroidery proof from DRAFT_UNVERIFIED → PROVEN using a REAL, validated photo
**plus a hash-bound content review**. In v2.3.3 a decodable file is only an INPUT to
proof — a blank or AI image decodes fine but is NOT proof. Proof requires an owner
content review, bound to the file's SHA-256, confirming visible stitches/threads/fabric.

## Step 1 — Shoot the real macro (+ main image)
One session, two shots (per the orchestration advice):
- **Main-image candidate**: exact garment/SKU/color/design, product-only, pure white bg, no text/props, ≥2000×2000, sharp, accurate color.
- **Embroidery macro**: same garment/design/threads, fill frame with stitches, satin edges + fabric weave visible, no artificial texture, ≥2000×2000.
Save into the project folder, e.g. `runs/nurse/macro.png` and `runs/nurse/main.png`.

## Step 2 — Validate the asset (path is RELATIVE to --project-dir)
Pass the filename relative to the project folder, not the doubled path:

    python3 core/asset_validator.py macro.png \
      --project-dir runs/nurse --source-type real_macro \
      --sku NS-001 --design-version v3 --reviewer owner

Good output → `"status": "PUBLICATION_READY"` (or VALID_FILE if metadata missing).
Bad: MISSING (check path — pass `macro.png`, not `runs/nurse/macro.png`) ·
INVALID_FILE (reshoot) · QUALITY_REVIEW_REQUIRED (<1000px, reshoot bigger).

## Step 3 — Point the creative brief at the real assets
In `runs/nurse/creative-brief.json`:

    "our_images": { "main": "main.png", "macro": "macro.png" }

## Step 3.5 — Record the hash-bound embroidery proof review (NEW in v2.3.3, REQUIRED)
A real file alone stays **DRAFT_UNVERIFIED**. The owner must review the actual pixels and
record it, bound to the macro's SHA-256. Get the hash:

    python3 core/asset_validator.py macro.png --project-dir runs/nurse | grep sha256

Then add to `creative-brief.json` (every flag must be a truthful `true`):

    "embroidery_proof_review": {
      "reviewer": "owner",
      "reviewed_at": "2026-07-15",
      "asset_hash": "sha256:<the hash from above>",
      "individual_threads_visible": true,
      "stitch_edges_visible": true,
      "fabric_weave_visible": true,
      "image_is_not_ai": true,
      "image_is_not_blank": true,
      "supplier_sku_matches": true,
      "design_version_matches": true,
      "thread_colors_match": true,
      "placement_matches": true
    }

If any of these is not actually true when you look at the photo, leave it `false` — proof
stays DRAFT_UNVERIFIED by design. A blank/AI/mismatched image must NOT be signed off.

## Step 4 — Build the creative proof
    python3 creative/creative_edge.py runs/nurse
`EMBROIDERY-PROOF.json` is written and the gate is set in the manifest. With a real,
adequately-sized macro **and** a matching hash-bound review, proof moves to **PROVEN**.
If the hash no longer matches the file (photo re-saved), proof drops back to DRAFT_UNVERIFIED.

## Alternative — supplier reference photo (v2.3.4)
If you can't shoot your own macro yet, a supplier's real photo can reach **PARTIALLY_PROVEN** —
but only with its OWN hash-bound review. Point `our_images.supplier_photo` at the file and add:

    "supplier_reference_review": {
      "reviewer": "owner", "reviewed_at": "2026-07-15",
      "asset_hash": "sha256:<hash of the supplier photo>",
      "supplier_identity_verified": true, "supplier_sku_matches": true,
      "design_version_matches": true, "thread_colors_match": true,
      "placement_matches": true, "image_is_not_ai": true,
      "image_is_not_blank": true, "visible_embroidery_confirmed": true
    }

A blank/AI/tiny/stale-hash supplier photo stays DRAFT_UNVERIFIED. A supplier reference is never
easier to approve than your own macro.

## Step 5 — Walk the gates and the owner approval chain
    python3 pipeline.py runs/nurse --status
    python3 pipeline.py runs/nurse --next     # single clear next action
Once the four creative gates pass, approve in order (each is hash-bound and self-invalidating):

    python3 pipeline.py runs/nurse --approve-main-image --asset main.png --by owner
    python3 pipeline.py runs/nurse --approve-creative --by owner
    python3 pipeline.py runs/nurse --approve-final --by owner

`--approve-final` refuses until CREATIVE_OWNER_APPROVAL passes. If you edit any approved file
later, the approval auto-invalidates and the project re-locks.

## Reality check (honest)
A validated + reviewed macro clears **EMBROIDERY_PROOF only**. To reach publication-ready you
still need: main-image compliance owner-APPROVED (not just COMPLIANT), thumbnail review, ≥2
image specs whose hashes match real files (consistency), actual-asset score, feasibility,
fulfillment, claims, personalization, the creative-package approval, and the final approval.
Use `--next` to walk them in order.
