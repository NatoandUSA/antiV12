# Phase 0 — Evidence Patch (v2.3) — response to the OS-v3 audit
Applied the 8 backend fixes the audit said to do FIRST, before any UI. 43/43 tests pass.

| # | Audit finding | Fix | Test |
|---|---|---|---|
| P0.1 | READY_FOR_REVIEW counted as a hard-gate pass | Per-gate pass statuses; READY_FOR_REVIEW never passes; each gate passes only on its own success status | test_ready_for_review_does_not_pass_hard_gate, test_review_status_locks_publication |
| P0.3 | A filename string counted as an actual image | New `core/asset_validator.py`: file must exist, decode, be an image MIME, have readable dims + hash + metadata; else INCOMPLETE | test_filename_string_is_not_an_image, test_real_png_validates, test_actual_asset_incomplete_without_real_image |
| P0.4 | Empty image specs reported CONSISTENT | 0 specs → INCOMPLETE; 1 → INCOMPLETE_FOR_CROSS_IMAGE; a bad single spec still BLOCKS | test_empty_specs_is_incomplete_not_consistent, test_single_bad_spec_still_blocks |
| P0.5 | Missing economics costs became 0 (false profit) | Required cost coverage; any missing required cost → INCOMPLETE (exit 2), never silently 0 | test_missing_cost_is_incomplete |
| P0.6 | Approvals not auto version-bound | Approvals store an approved_bundle_hash of the exact files; any file change auto-invalidates on next manifest eval | test_approval_bundle_hash_auto_invalidates |
| P0.8 | Blocked creative run exited 0 | Creative module exits 3 when publication is blocked | test_blocked_creative_exits_nonzero |
| P0.7 | Docs drifted from code | `capabilities.py` → CAPABILITIES.json, one source of truth for maturity (README/self-audit derive from it) | test_capability_manifest_generates |
| bug | Gate-outcome statuses (VERIFIED/COMPLIANT/CONSISTENT) were coerced to INCOMPLETE | Added them to the status vocabulary (found by the new tests) | full suite |

## NOT done this cycle (the web-app OS-v3 — a separate multi-month program)
The React/Next.js dashboard, FastAPI service layer, SQLite/Postgres database, Import Center, Command Center, Project Workspace, query-funnel analytics, PPC engine, learning library, and roles/permissions (Parts VI–XXIV) are a full application build — deliberately not faked here. The audit itself sequences them AFTER this evidence patch. CAPABILITIES.json marks them NOT_BUILT honestly.

## Maturity (from CAPABILITIES.json)
Backend decision engine: PILOT_READY · Creative planning: PILOT_READY · Actual-asset validation: PILOT · Dashboard: NOT_BUILT · Post-launch loop: PARTIAL · Market-beating OS: NOT_READY (backend hardened, web app pending).
