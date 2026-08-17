# v2.3.1 — Evidence Integrity Patch (response to the independent adversarial audit)
Every bypass the audit reproduced is now closed. **60/60 tests pass** (incl. 16 new adversarial tests).

| # | Audit bypass | Fix | Test |
|---|---|---|---|
| P0.1 | Header sniffing accepted a fake PNG | Real Pillow decode (verify + load pixels); fake/truncated → INVALID_FILE; tiny → QUALITY_REVIEW_REQUIRED | test_fake_png_header_is_rejected, test_one_pixel_not_publication_ready |
| P0.2 | `thumbnail_review_complete=true` boolean bypassed image evidence | Removed boolean trust; requires a real decoded image + a structured, hash-matched reviewer record | test_boolean_cannot_replace_review, test_stale_review_hash_rejected |
| P0.3 | Actual Asset Edge points were hard-coded | Every visual component now derives from the reviewer record (0/1/2); any missing component → INCOMPLETE (no optimistic defaults) | test_missing_review_component_is_incomplete |
| P0.4 | Main-image validator trusted a nonexistent path; accuracy defaulted true | Validator calls the asset validator (real file); accuracy defaults UNKNOWN and needs explicit VERIFIED evidence | test_main_validator_rejects_missing_file, test_main_validator_accuracy_unknown_is_incomplete |
| P0.5 | Creative gates weren't hard gates | `required_gates(project)` adds creative gates for apparel and EMBROIDERY_PROOF for embroidery; manifest evaluates against them | test_creative_gates_required_for_apparel |
| P0.6 | CLI `--approve` wasn't hash-bound | Replaced with `--approve-final`: auto-gathers the file bundle, refuses if required files missing, hash-binds, auto-invalidates on change | test_approval_without_files_rejected, test_cli_approval_binds_and_invalidates |
| P0.7 | Approval hashing used basename (collisions) | Stores normalized relative paths; path traversal rejected | test_path_traversal_rejected |
| P0.8 | One GO row made a mixed project GO | Variant-aware: mixed GO+INCOMPLETE → project INCOMPLETE (exit 2); writes ECONOMICS.json with eligible/incomplete/blocked variants | test_mixed_economics_not_project_go |
| P0.9 | "Hand stitched" passed for machine embroidery | Product-fact-aware contradiction check (method/material/packaging/origin/production) blocks it | test_hand_stitched_blocked_for_machine_embroidery |
| P0.10 | COMPLIANT_DRAFT passed publication | Only COMPLIANT/APPROVED pass; pending owner review → READY_FOR_REVIEW | test_compliant_draft_does_not_pass |
| P0.11 | READY_FOR_REVIEW → exit 0 | READY_FOR_REVIEW → exit 3 (review-required is not success) | test_ready_for_review_exit_is_three |

## Migration from v2.3
- `pip install -r requirements.txt` now includes **Pillow** (required for real image decoding).
- Owner approval command changed: `--approve "kw"` → **`--approve-final --by owner`** (hash-bound bundle).
- `thumbnail_review_complete` boolean is ignored — supply a structured `thumbnail_review` record instead.
- Apparel/embroidery projects now require the creative gates to unlock publication (fail-safe if not run).

## Honest remaining limitations
- Image QUALITY is still human-reviewed (no CV grading of composition/crop) — by design.
- Capability manifest checks file presence + declared maturity; not yet fully test-gated (audit P1.1 partial).
- The web-app OS (dashboard/DB/Import Center) remains NOT_BUILT — correctly sequenced after this patch.
