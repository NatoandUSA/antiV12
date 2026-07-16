# Changelog

## 2.4.0-RC2 — pilot fixes (3 real bugs found running one project end to end)
Ran a full end-to-end pilot (Personalized Nurse Sweatshirt) through every stage. It completed and
UNLOCKED with no manifest editing, and caught 3 real bugs — all fixed with regression tests:
- **listing_validate.py crashed on A+ modules** shaped as {headline, copy} dicts (AttributeError),
  which would have BLOCKED every generated/AI-built listing. Now normalizes A+ modules to text.
- **listing_generator backend repeated title words** (wasted the 249 bytes). Now excludes title words.
- **ip_guard flagged every unrecognized English word as REVIEW** → no real listing could pass IP. Now
  unrecognized tokens are informational (still listed for a manual eyeball); the curated brand/character
  BLOCK library + named risky-phrase REVIEW list still govern. Clean listing → OK; "Disney Mickey" → BLOCK.
See PILOT-REPORT.md for the stage-by-stage results and the ranked v2.4 punch-list. 143/143 tests pass.


## 2.4.0-RC2 — Structured listing generator (deterministic, 75-char compliant)
Built the listing-generation upgrade from the v2.4.0 enhancement summary. (Its files weren't in this
build — it was written against a parallel session — so they were built for real and made compliant
with the RC1 rules, NOT the summary's outdated "~78 char" title.)
- **listing/listing_generator.py (v2)** — turns KEYWORD-INTELLIGENCE.json + COMPETITOR-GAP.json (+
  optional creative-brief product facts) into a human-review **LISTING-BRIEF.json/.md** and a
  schema-valid **listing.json**. Title is front-loaded and hard-capped at **75 chars** (no promo
  words / ALL-CAPS / symbols / repeats), Item Highlights ≤125, backend ≤249 bytes, claim-safe bullets.
- **listing/a_plus_templates.py** — 6 reusable A+ modules for embroidered apparel; modules are chosen
  dynamically by competitor-gap priority. Embroidery-proof and size-chart modules carry
  **requires_proof** so they can't be published without a real photo / manual check (evidence semantics).
- **Dashboard** — a 4th build mode, **🧩 Generate structured listing (from data)** — deterministic,
  free, no AI. Runs the generator, safe-writes listing.json, renders the preview, and reports which
  A+ modules still need a real photo before publishing.
- capabilities + BUILD-MANIFEST now include the two new modules; sidebar shows them VERIFIED.
137/137 tests pass (10 new). PPC modules named in the summary are intentionally NOT built (a separate
feature set, not part of this listing upgrade) — the dashboard/manifest honestly omit them.


## 2.4.0-RC1 — Discovery-first cockpit: compliance, transparency, honesty (P0 set)
Applied the P0 fixes from the Updated Feedback audit. (The two modules it flagged as "missing"
already existed in this build — it reviewed an older archive — but its underlying asks were valid.)
- **P0-02 Title compliance.** Amazon's non-media title cap of **75 characters** (effective 2026-07-27)
  is now the config default for apparel/POD/jewelry (was 200). Added the new **Item Highlights**
  field (125 chars). listing_validate blocks a 76-char title and a 126-char highlight; the dashboard
  preview shows live title X/75 and highlight X/125 counters. (Confirmed via Amazon's announcement.)
- **P0-03 No fake algorithm claims.** Renamed "A10-aware Opportunity Score" to an **Internal
  Opportunity Heuristic** everywhere, explicitly "our recommendation, not Amazon's ranking formula,"
  with the formula shown. No user-facing text claims knowledge of Amazon's private algorithm.
- **P0-04 Transparent keyword scoring.** keyword_intelligence now shows **separate Amazon-opportunity
  and trend-momentum scores**, external (YTrends) influence **capped at 25%** and never penalizing a
  keyword when absent, plus per-keyword **confidence**, reason codes, score decomposition, and file
  **provenance** (name + hash). Output is deterministic.
- **P0-05 Evidence-classed gaps.** Every competitor gap carries source_type (numeric / text-derived /
  manual-review-required), confidence, and a manual-confirmation flag. Real embroidery proof is
  **never inferred from Xray numbers** — it's marked manual-review-required. Missing columns are
  reported as warnings, not scored as zero.
- **P0-06 Strict listing schema + safe overwrite.** A built or imported listing is validated
  field-by-field before it can replace listing.json; an invalid build **keeps your previous listing**
  and returns the exact errors. schema_version stamped; prior file backed up to listing.prev.json.
- **P0-07 Truthful dashboard.** Removed invented star ratings; shows "Price not entered" when absent;
  the preview is labelled a **STRUCTURAL PREVIEW (not live Amazon data)**; each run stage reports
  SUCCESS / SKIP / REVIEW / FAILED and the toast says so.
- **P0-01 Package integrity.** New BUILD-MANIFEST.json (version, per-file SHA-256, module status,
  test count, build time); CAPABILITIES lists the new modules; the dashboard sidebar shows the
  toolkit version and each module as VERIFIED / FAILED / UNAVAILABLE.
127/127 tests pass (13 new P0 tests). Deferred to the next phase (the reviewer's P1/P2 — a much
larger build): authoritative Next-Action card, editable in-dashboard gate forms, ASIN review/approval,
Keyword Decision Center, four role-based report packages, project history/backup, and the manual
post-launch learning loop. Deployment stays owner-only local on 127.0.0.1 — no public exposure, no auth.


## 2.3.4-RC1 · Claude Code build mode (use your Max plan, no API cost)
Added a third listing-build mode to the cockpit that uses the local **Claude Code CLI** — which the
Pro/Max plan already includes on shared subscription limits — so listings are built with **no API key
and no per-token charge**.
- `/api/build_cc` shells to `claude -p "<prompt>"` (headless), parses the JSON listing, writes
  listing.json, renders the Amazon preview. `/api/claude_code_status` detects whether the CLI is
  installed and shows a green "Max plan · detected" badge.
- Three build modes now: **⌘ Claude Code (Max plan, no API cost)** · ⚡ API key · ✨ copy-paste brief.
- **Deliberately NOT built:** browser automation of claude.ai. Driving the consumer web app
  programmatically violates Anthropic's terms and risks account suspension — same principle as the
  no-Seller-Central rule. Claude Code is the sanctioned way to use the subscription from a script.


## 2.3.4-RC1 · starting-phase add-ons (keyword intelligence + competitor gaps + cockpit)
Built the local dashboard cockpit (dashboard/app.py) and two new starting-phase research tools.
An uploaded "Enhancement Summary" claimed these existed — verified they did NOT in this build, so
they were built for real, tested, and integrated.
- **research/keyword_intelligence.py** — merges Helium 10 keyword data (Cerebro/Magnet or the
  master-keywords.xlsx phaseA already makes) with an optional YTrends CSV, and ranks by
  **45% Amazon search volume + 40% low Amazon competition + 15% YTrends momentum**. Transparent
  score + plain-English "why" per keyword. Writes KEYWORD-INTELLIGENCE.json + report.
- **research/competitor_gap_analyzer.py** — reads the Xray export and ranks differentiation gaps:
  review beatability, price band, thin image counts, personalization depth, and (the core edge)
  real-embroidery-vs-print proof — each with a concrete creative action. Writes COMPETITOR-GAP.json.
- **Dashboard cockpit** — local Flask app: upload H10/YTrends → Run → Amazon-style results
  (10 ASIN, top-5 keywords, 5 seeds, **keyword-intelligence bar chart**, **competitor-gap board**,
  gate board, Amazon listing preview). ⭐ My-products board (revisit finished listings) and an
  optional API auto-build (your own key, in-memory only, never on disk). Never touches Seller Central.
- Robust column matching (underscore-aware) so H10 "search_volume" resolves correctly (was mis-hitting
  "sv_trend"). 114/114 tests pass (7 new for the two tools).


## 2.3.4-RC1 — Alpha-blocker patch: the supported workflow now completes end-to-end
The FINAL ALPHA READINESS review was a NO-GO: v2.3.4-RC's safety was real, but a staff member
could not move one project through all gates without editing PROJECT-MANIFEST.json by hand, and a
few approval-integrity gaps remained. This bounded RC1 closes them. Verified end-to-end: a real
nurse project goes raw-evidence → approved/unlocked with NO manifest editing, and editing any
bound file re-locks it.
- **P0.1 Gate-file ingestion.** New deterministic readers set RELEVANCE, PRODUCT_FEASIBILITY,
  FULFILLMENT, CATALOG_STRUCTURE, PERSONALIZATION, CLAIMS_EVIDENCE from their JSON files (explicit
  `decision`, never inferred from the filename). `--scaffold-gate-files` writes blank templates so
  staff have a supported way to fill each gate.
- **P0.2 Thumbnail source of truth.** Scoring reads a completed THUMBNAIL-REVIEW.json; the embedded
  brief object is a deprecated fallback. A rerun never overwrites a completed review.
- **P0.3 Main-image approval identity.** `--approve-main-image` refuses unless the chosen asset IS
  the reviewed image and its current hash matches the compliance record. (Also fixed the root cause:
  the validator now receives the project dir and the accuracy fields, so a real main image can
  actually reach COMPLIANT.)
- **P0.4 Creative bundle completeness.** `--approve-creative` refuses an empty/incomplete bundle —
  every required creative evidence file and referenced asset must exist. No zero-hash approvals.
- **P0.5 Final evidence completeness.** The final bundle is derived from each passing required
  gate's declared evidence; a passing gate with no current evidence file is a hard refusal.
- **P0.6 IP on listing.json.** IP screening runs on JSON listings (title/bullets/description/backend/
  personalization/A+), setting IP_SAFETY instead of leaving it NOT_RUN.
- **P0.7 Windows-safe.** All child processes use `sys.executable`; docs use `python`.
- **P0.8 Docs regenerated.** Staff QUICKSTART rewritten to the real commands (approval chain, no
  removed `--approve "<keyword>"`); README/CHANGELOG/CAPABILITIES/SOP updated; doc-regression tests
  fail if a removed command reappears.
- **P0.9 One next-action engine.** The pipeline footer uses stages.compute() — the same source as
  `--status`/`--next` — so recommendations always agree.
- **P1.2** requests added to requirements + tested-version note. **P1.6** kw_expand TOS wording softened.
107/107 tests pass (14 new alpha-blocker tests incl. a full end-to-end shadow run).
Alpha posture: owner-only shadow alpha on one trusted machine; do not publish from the first cycle.
Still deferred: role/auth, executable stage orchestration, PUBLICATION_READY rename (P1.3),
richer category-review schema (P1.4), and the dashboard/database.


## 2.3.4-RC — Close the alternative evidence paths + real approval workflow
The independent v2.3.3 audit was right: the strong new macro path was undermined by weaker
alternative paths and a missing approval workflow. This bounded release-candidate closes them.
- **P0.1 Supplier proof now needs its OWN hash-bound review.** A blank/AI supplier photo plus a
  boolean no longer reaches PARTIALLY_PROVEN. `supplier_reference_review` must match the current
  file's SHA-256 and confirm supplier identity, SKU, design version, thread, placement, not-AI,
  not-blank, and visible embroidery. Missing/stale/incomplete → DRAFT_UNVERIFIED.
- **P0.2/P0.3 Quality-incomplete images cannot pass.** A tiny (QUALITY_REVIEW_REQUIRED) image can
  no longer be main-image COMPLIANT nor receive an actual-asset score. A human score never
  overrides a file-quality failure. (New `asset_validator.is_quality_acceptable`.)
- **P0.4 Consistency validates real current files.** Each image spec must carry an asset_path that
  decodes and whose current hash matches its asset_hash; invented/stale hashes are rejected and
  never yield CONSISTENT (they drop to PLAN_CONSISTENT / flagged).
- **P0.5 Unified creative status.** Creative Edge reports only CREATIVE PACKAGE COMPLETE / REVIEW
  REQUIRED / BLOCKED, derived from its four gates. It never prints "publication clear" — project
  publication is decided only by `pipeline --status`.
- **P0.6 Real approval workflow.** New `--approve-main-image --asset <f>` and `--approve-creative`
  CLIs. These are the ONLY way MAIN_IMAGE_COMPLIANCE and CREATIVE_OWNER_APPROVAL reach APPROVED;
  both are hash-bound and auto-invalidate (with the gate falling back) if the bound files change.
- **P0.7 Final approval requires creative approval.** `--approve-final` now refuses unless every
  non-final required gate passes, INCLUDING CREATIVE_OWNER_APPROVAL. Correct order enforced:
  main-image approval → creative approval → final approval.
- **P0.8 Final bundle derives from gate evidence.** The bundle is built from every gate's evidence
  file (feasibility, economics, catalog, personalization, claims, all creative JSONs), the listing,
  economics input, referenced image assets, and a hashed GATE-SNAPSHOT.json — so changing any of
  them after approval auto-invalidates it. (Verified: editing listing.json invalidates final.)
- **P0.9 VISUAL-CONSISTENCY.json** is now written with evidence (spec counts, backed hashes,
  reviewers, tool version).
- **P1.2** A completed THUMBNAIL-REVIEW.json is no longer overwritten by a rerun.
- **P1.5** The pipeline recommends the ACTUAL next approval command in order, not the removed
  `--approve "<keyword>"`.
91/91 tests pass (17 new evidence/approval tests). Honestly deferred to after one real end-to-end
pilot: executable stage orchestration (--run-stage), full IP-on-listing.json + claims/feasibility
gate parsing (P1.1/P1.3/P1.4), and the web dashboard/database.


## 2.0.0 — Enforcement core (Phase 1)
Added
- core/: status (statuses + exit codes 0–7), gate_engine (11 hard gates, structured results),
  manifest (PROJECT-MANIFEST.json, atomic writes, history, hashing, approval invalidation),
  config (config.yaml business rules), provenance, hashing, paths bootstrap.
- pipeline.py v2 — runs gates, records manifest, computes PUBLICATION LOCKED.
- migrate_project.py — non-destructive v1→v2 migration.
- tests/ — 17 tests incl. all reproducible audit-failure regression cases (17/17 pass).
- Folder structure: research/ compliance/ economics/ feasibility/ positioning/ listing/ creative/ analytics/ reports/.

Changed
- Business rules (min $8 profit, owner-only publish) moved to config.yaml.
- Proven v1 modules relocated into category folders (reused, not rewritten).

Enforced (were advisory in v1)
- BLOCKED can no longer exit 0. Score cannot override a hard gate. Missing owner
  approval or any NOT_RUN hard gate keeps a project PUBLICATION LOCKED (fail-safe).

## 1.x — see reference/FIX-REPORT-P0.md and FIX-REPORT-P1.md

## 2.1.0 — Creative & Conversion Edge module
Added creative/ (creative_edge.py, creative_diagnosis.py, edge_lib.py): 9 outputs — visual matrix, main-image concepts, thumbnail sim, 9-image storyboard+prompts, embroidery proof, consistency audit, edge score, experiments, post-launch diagnosis. Reuses project data; no CV; no Seller Central.

## 2.2.0 — Creative Edge hardening
Main-image compliance mode (main_image_validator.py); macro/gift moved to secondary; Image 1 text removed; misleading claims fixed + headline claim-check; split PLAN vs ACTUAL-ASSET score (INCOMPLETE w/o real image); competitor confidence + effective sample; DRAFT_UNVERIFIED proof status; zero-safe metrics + config benchmarks; thumbnail contain + saved review; 15 creative regression tests (32 total pass). Maturity: PILOT_READY.

## 2.3.0 — Phase 0 Evidence Patch
Per-gate pass statuses (READY_FOR_REVIEW no longer passes hard gates); real image validation + hashing (core/asset_validator.py); empty image specs -> INCOMPLETE; missing economics costs -> INCOMPLETE (exit 2); approval bundle-hash auto-invalidation; creative run exits 3 when blocked; generated CAPABILITIES.json (single source of truth); added gate-outcome statuses. 43/43 tests pass. Web-app OS-v3 intentionally NOT built (separate program).

## 2.3.1 — Evidence Integrity Patch
Real Pillow image decoding (no header sniffing); removed thumbnail boolean bypass (structured hash-matched review required); evidence-derived actual-asset scoring (no hard-coded points); main-image validator decodes the file + accuracy defaults UNKNOWN; creative gates are conditional hard gates; hash-bound --approve-final CLI (refuses incomplete bundle, relative paths, auto-invalidation); variant-aware economics; product-fact-aware claim contradictions (hand-stitched blocked for machine embroidery); COMPLIANT_DRAFT no longer passes; READY_FOR_REVIEW exits 3. 60/60 tests pass (16 new adversarial).

## 2.3.2 — Asset-backed proof + stage orchestration
Embroidery proof is now ASSET-BACKED (a boolean can no longer produce PROVEN; only a real decoded macro/supplier photo does) and writes EMBROIDERY-PROOF.json with an asset hash. asset_validator.py gains a CLI (Real-Photo SOP now runnable). New core/stages.py registry + pipeline --status / --next give one clear next action. Corrected the overstated "macro = final gate" claim. 65/65 tests pass.

## 2.3.3 — Evidence semantics: a file is an input to proof, not proof
The independent audit was right that v2.3.2 overclaimed "asset-backed proof": any DECODABLE
image — including a blank white square or an AI render — could reach PROVEN, because
"asset-backed" only checked that a real file existed, not what the file showed. Fixed:
- **Content review now required for PROVEN.** build_embroidery_proof requires an
  `embroidery_proof_review` bound to the macro's SHA-256, with reviewer + reviewed_at and
  every confirmation true (individual_threads_visible, stitch_edges_visible, fabric_weave_visible,
  image_is_not_ai, image_is_not_blank, supplier_sku_matches, design_version_matches,
  thread_colors_match, placement_matches). A blank/AI/undersized/hash-mismatched macro →
  DRAFT_UNVERIFIED. Verified end-to-end: a blank 2000×2000 white PNG no longer proves.
- **Creative Edge writes to the manifest.** creative_edge.py main() now sets MAIN_IMAGE_COMPLIANCE,
  EMBROIDERY_PROOF, VISUAL_CONSISTENCY and ACTUAL_ASSET_EDGE gates in PROJECT-MANIFEST.json,
  closing the "two truths" gap where creative status and the gate ledger could disagree.
- **MAIN_IMAGE_COMPLIANCE passes on APPROVED only** — COMPLIANT is technical-only, not a pass.
- **Consistency needs asset-backed specs** — plan-only specs yield PLAN_CONSISTENT, not ACTUAL.
- **--approve-final refuses** unless every non-owner required gate already passes (final = last step).
- **--init-project** derives required gates from product facts; embroidery projects require EMBROIDERY_PROOF.
- **External absolute paths rejected** by asset_validator unless inside the project bundle.
- **--status runs verify_approvals first** so approval hash drift is caught on every status call.
- **REAL-PHOTO-SOP.md fixed**: the validator path is relative to --project-dir (the old
  doubled `runs/nurse/macro.png` returned MISSING); added the Step 3.5 hash-bound proof review.
74/74 tests pass (9 new evidence-semantics tests). Executable stage orchestration
(--run-stage / --run-all-ready) and the web dashboard/database remain intentionally deferred.
