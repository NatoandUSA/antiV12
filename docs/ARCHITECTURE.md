# Architecture Map — v1 → v2

## Method
Compared four sources: what the SOP claims, what the v1 code does, what the two audits found, and what's still missing for personalized FBM. Below is the map that drove the v2 build.

## v1 modules (reused, proven)
| Module | In | Does | Status in v2 |
|---|---|---|---|
| demand_score.py | research/ | GO/TEST/SKIP/INCOMPLETE cross-check | reused, drives DEMAND gate |
| asin_picker.py | research/ | best-10 ASIN batch (active-sales aware) | reused |
| phaseA_master.py | research/ | keyword scoring, Beatability, reports | reused |
| seed_expand.py | research/ | next-round seeds | reused |
| ip_guard.py + ip_library.json | compliance/ | 450+ term IP screen, exit 0/2/3 | reused as IP engine |
| tm_guard.py | compliance/ | delegates to ip_guard (one engine) | reused |
| listing_validate.py | compliance/ | all-content + claim-checker + category config | reused, drives LISTING_ACCURACY |
| economics_gate.py | economics/ | contribution margin, $8 min, break-even ACOS | reused, drives ECONOMICS |
| run_state.py | core/ | v1 gate/approval log | superseded by manifest (kept for migration) |

## v2 NEW core (the enforcement layer — this turn)
| Module | Purpose |
|---|---|
| core/status.py | ONE set of statuses + exit codes (0–7); BLOCKED can't exit 0 |
| core/gate_engine.py | structured GateResult; 11 hard gates; score never overrides a hard gate |
| core/manifest.py | PROJECT-MANIFEST.json, atomic writes, history, hashing, approval invalidation |
| core/config.py + config.yaml | business rules (min $8, owner-only-publish) in config, not hard-coded |
| core/provenance.py | separates verified fact / Amazon-native / estimate / AI inference / assumption / missing |
| core/hashing.py | source-file + approved-version hashes |
| pipeline.py (v2) | runs gates → manifest → computes PUBLICATION LOCKED |
| migrate_project.py | non-destructive v1→v2 migration |
| tests/ | 17 tests incl. all reproducible audit-failure cases |

## Gap analysis — what the SOP/prompt want that is NOT yet built (Phase 2+)
Honestly not done this turn (scaffolding/folders exist, logic pending):
- feasibility/ (product + FBM validators) — Stages 6–7 gates
- positioning/ (positioning builder) — Stage 8
- listing/ (keyword_mapper, listing_builder, personalization) — Stages 10–13
- creative/ (image_plan_builder, image_validator, a_plus_builder) — Stages 14–16
- analytics/ (report_ingestion, launch_analyzer, diagnostic_engine, experiment_planner) — Stages 20–24
- reports/ (html/markdown/spreadsheet renderers), dashboard, publication package builder
- competitor_gap, catalog_validator, claims_validator (as standalone gates)

## Failure points fixed (from audits, now regression-tested)
IP BLOCK not a gate · dead listing as TOP · stitch=Disney · phaseA crash · missing-evidence=SKIP ·
profit <$8 not enforced · approvals not version-bound · state inferred from filenames.

## Data flow (v2)
seed → demand-input.csv → DEMAND gate → xray → asin_picker → cerebro → phaseA →
economics-input.csv → ECONOMICS gate → listing → IP + LISTING gates →
manifest.evaluate() → PUBLICATION LOCKED? → owner approval → (manual) publish.
