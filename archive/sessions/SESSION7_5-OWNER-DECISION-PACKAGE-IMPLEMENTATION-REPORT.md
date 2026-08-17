# Session 7.5 — Offline Owner Decision Package — Implementation Report

## Identity

| Field | Value |
|---|---|
| Phase | 7.5 — Offline Owner Decision Package |
| Branch | `phase7-5-owner-decision-package` |
| Baseline commit | `0d85e03bba5fdc3e63103c02abc78b6ff6b79b4c` |
| Checkpoint tag | `phase7-5-decision-package-checkpoint-0d85e03` |
| Implementation commit | `ae07310` (feat) |
| Proof commit | `<PROOF_COMMIT>` (docs — the commit that adds this report + the proof gate; see final response) |
| Accepted Phase 7.2 tag | `phase7-2-cumulative-accepted-d5ad841` |
| Accepted Phase 7.3 tag | `phase7-3-accepted-7005275` |
| Accepted Phase 7.4 tag | `phase7-4-owner-dashboard-accepted-eebecc5` |

## Files

**Created**
- `production/phase7_owner_decision_package.py` — the ONE Phase 7.5 authority.
- `tests/test_phase7_5_owner_decision_package.py` — 109 focused tests.
- `SESSION7_5-OWNER-DECISION-PACKAGE-IMPLEMENTATION-REPORT.md` — this report.
- `SESSION7_5-OWNER-DECISION-PACKAGE-PROOF-GATE.json` — proof gate.

**Modified**: none. No Phase 7.2 / 7.3 / 7.4 source or test file was changed. No accepted history, tag, or commit was touched.

## Position in the accepted pipeline

Manual Amazon report export → Phase 7.2 cumulative ingestion → Phase 7.3 offline analysis → Phase 7.4 owner review → **Phase 7.5 owner decision package** → owner manually verifies and acts in Seller Central.

Phase 7.5 is a **preparation and documentation layer only**. It never connects to Amazon, never changes Amazon, never uploads, never emits a bulk-upload template or API payload, never automates a browser, never executes an owner decision, and never claims an action was performed. The owner remains the only manual bridge to Seller Central.

## Source authority (Phase 7.3)

Phase 7.5 does **not** re-implement Phase 7.3 loading. It reuses the accepted Phase 7.4 authority `production/phase7_owner_dashboard` (`DASH`):

- `DASH.load_source(phase7_3_dir)` — canonical source selection (`promoted/` precedes stale `final/`), producer-exact integrity (manifest deterministic hash + every `output_hashes` artifact + required-artifact presence), and rejection of malformed JSON / NaN / Infinity / null bytes / oversize / wrong root / wrong schema / duplicate stable IDs / count inconsistency / path traversal.
- `DASH.build_views(source)` — the stable entity identities and per-entity `content_sha256` (search-term `entity_id = st:<lineage_hash>`, `content_sha256 = _row_material(row)`).

Phase 7.5 additionally enforces `analysis_readiness == SESSION7_3_ANALYSIS_READY_FOR_OWNER_REVIEW` before treating the source as usable.

## Review-state authority (Phase 7.4)

- Path: `runs/T2/phase7/7.4/review_state/review-state.json`, schema `phase7-4-review-state-v1`.
- Record fields: `entity_id, entity_type, review_status, owner_note, source_manifest_sha256, content_sha256, revision, created_at, updated_at`.
- `source_manifest_sha256` references the Phase 7.3 **manifest** `deterministic_content_sha256`.
- Phase 7.5 adds a **strict structural loader** (`load_review_state_strict`) that rejects duplicate JSON keys, non-finite numbers, null bytes, malformed JSON, wrong root type, and wrong schema (blocking), and performs per-record validation (invalid status, invalid 64-hex shape, mismatched inner id) as a per-record structural exclusion. It reuses the accepted status vocabulary `DASH.REVIEW_STATUSES`.

Phase 7.5 computes the source-change state itself (rather than depending on `DASH.merge_review_state`, which does not flag a stale manifest hash and raises on non-dict records) so its manifest-hash gate is strict and its evaluation never aborts on a corrupt record.

## Eligibility model

A record enters the actionable checklist only if every condition holds:

1. Phase 7.3 source valid; 2. `analysis_readiness` accepted for owner review; 3. review record structurally valid; 4. entity id present in current Phase 7.3 data; 5. `review_status == APPROVED_FOR_MANUAL_ACTION`; 6. `source_manifest_sha256` matches current source; 7. `content_sha256` matches current evidence; 8. not SOURCE_CHANGED; 9. not ENTITY_ABSENT; 10. not blocked; 11. required owner policy present; 12. currency unambiguous; 13. attribution window unambiguous; 14. evidence sufficient to reproduce; 15. no duplicate action identity.

**Content / manifest matrix** (distinct, testable reasons):
- content mismatch + manifest mismatch → `SOURCE_CHANGED`
- content mismatch + manifest match → `HASH_MISMATCH`
- content match + manifest mismatch → `SOURCE_MANIFEST_MISMATCH`

**Supported recommendation types** = the Phase 7.3 actionable labels `REVIEW_FOR_MANUAL_NEGATIVE`, `REVIEW_FOR_MANUAL_EXACT_KEYWORD`, `REVIEW_BID_OR_BUDGET_CONTEXT`. Any other label (e.g. `INSUFFICIENT_EVIDENCE`, `KEEP_MONITORING`) → `UNSUPPORTED_RECOMMENDATION_TYPE`. `MISSING_POLICY` fires only for a bid/budget recommendation whose `target_acos_source != OWNER_DECLARED`.

Every reviewed record that is not eligible is placed in the exclusions report with an exact reason — never silently dropped.

## Package item identity & duplicate policy

- **Package item id** = `item:` + SHA-256 of the canonical action identity + source content hash. Derived from stable source identity, never row number, sort position, timestamp, or random UUID.
- **Canonical action identity** (prefix-independent) = `(recommendation_type, campaign, ad_group, targeting, match_type, customer_search_term, currency, attribution_window, report_period)`.
- Same identity + identical content → **deduplicate deterministically**, keep the lexicographically-smallest entity id, record duplicate count and lineage.
- Same identity + conflicting content → **exclude all** members (`DUPLICATE_CONFLICT`), record the conflict; never last-write-wins.
- Ordering everywhere is deterministic (sorted).

## Exclusion model

Reason codes: `NOT_APPROVED, SOURCE_CHANGED, ENTITY_ABSENT, HASH_MISMATCH, SOURCE_MANIFEST_MISMATCH, BLOCKED_RECOMMENDATION, MISSING_POLICY, MISSING_EVIDENCE, AMBIGUOUS_CURRENCY, AMBIGUOUS_ATTRIBUTION_WINDOW, DUPLICATE_IDENTICAL, DUPLICATE_CONFLICT, INVALID_REVIEW_STATE, UNKNOWN_ENTITY, UNSUPPORTED_RECOMMENDATION_TYPE, MISSING_REQUIRED_FIELD`.

## Package schemas

Package directory `packages/decision-package/<package-id>` → in this build the content-addressed name `pkg-<hash16>` (or `--package-name`). Files:
- `OWNER_READ_FIRST.md`, `executive_summary.md`, `manual_action_checklist.tsv`, `manual_action_checklist.json`, `decision_details.md`, `excluded_items.tsv`, `excluded_items.json`, `source_lineage.json`, `package_manifest.json`.
- With `--include-deferred-summary`: `deferred_items.tsv`, `policy_requirements.md`.

No Amazon bulk sheet, SP-API JSON, Ads API JSON, browser-automation script, or upload-ready file is ever produced.

**Manifest schema** `phase7-5-package-manifest-v1` includes: package schema version, package id, readiness, deterministic `package_content_sha256`, Phase 7.3 source-dir type + manifest hash, Phase 7.4 review-state aggregate hash, reference date, source/analyzed rows, review-state records, approved records, eligible count, excluded count, duplicate-identical/-conflict counts, source-changed/blocked/policy-required counts, currency list, attribution-window list, output artifact list + SHA-256 hashes, Amazon counters (all 0), manual-action disclaimer, generator version. Runtime `generated_at` is isolated in `runtime_metadata` and never feeds the content hash.

## Deterministic design

`canonical_json` (sorted keys, `ensure_ascii=False`, UTF-8, `indent=2`) + SHA-256 throughout. Every artifact except `package_manifest.json` is fully deterministic (no timestamps, explicit `\n`, sorted records, Decimal metrics preserved as exact strings). The `package_content_sha256` is computed over the analytical model plus the deterministic artifact hashes; the package id is `pkg-<first16>`. Identical (source, review state, reference date, config) ⇒ byte-identical analytical files and identical content hash.

## Atomic-write & idempotency design

Validate → build complete in-memory model → write into `runtime/.build-<name>` → verify every artifact from bytes → best-effort fsync (file + directory) → `os.replace` the temp directory onto the final package directory. On any failure the temp directory is removed and no successful/partial package is left. Idempotency: an existing package with the same content hash and intact artifacts → `IDEMPOTENT_REUSE` (not rewritten); an existing package with the same name but different content → `SESSION7_5_PACKAGE_BLOCKED` integrity conflict (the last valid package is preserved, never overwritten). Content-addressed default names make natural collisions impossible.

## Formula-injection design

TSV cells reuse the accepted Phase 7.4 rule (`DASH._tsv_cell`): a leading `= + - @` (and tab/CR/LF/pipe) neutralized with a leading `'`, except genuine numbers (`-2.50` preserved); tab/CR/LF/null stripped. Every TSV row is emitted from a fixed column tuple, guaranteeing equal column counts; JSON preserves Unicode (Vietnamese owner notes intact).

## Readiness states

`SESSION7_5_PACKAGE_READY`, `SESSION7_5_PACKAGE_READY_EMPTY`, `SESSION7_5_SOURCE_REQUIRED`, `SESSION7_5_SOURCE_BLOCKED`, `SESSION7_5_REVIEW_STATE_REQUIRED`, `SESSION7_5_REVIEW_STATE_BLOCKED`, `SESSION7_5_SOURCE_CHANGED_REVIEW_REQUIRED`, `SESSION7_5_POLICY_REQUIRED`, `SESSION7_5_CONFLICT_REVIEW_REQUIRED`, `SESSION7_5_PACKAGE_BLOCKED`. Blocking/missing-input states (`SOURCE_REQUIRED/BLOCKED`, `REVIEW_STATE_REQUIRED/BLOCKED`, `PACKAGE_BLOCKED`) exit nonzero and write no package; content states (including empty) exit zero and write a package.

## Test results

| Gate | Result |
|---|---|
| Baseline compileall (`production core`) | exit 0 |
| Baseline focused Phase 7.2 / 7.3 / 7.4 | 377 / 117 / 94 = 588 tests, OK (skipped=1) |
| Baseline full suite (at `0d85e03`) | 2689 passed, 2 skipped |
| Phase 7.5 focused | 109 passed |
| Phase 7.2 / 7.3 / 7.4 focused after | 377 / 117 / 94 = 588 tests, OK (skipped=1) — no regressions |
| Full suite after | 2798 passed, 2 skipped |
| compileall (`production core tests`) | exit 0 |
| Fresh-worktree | PASS — detached worktree at `ae07310`: `runs/` absent, compileall exit 0, 109 Phase 7.5 tests pass with synthetic fixtures only |

## Validation

- **Synthetic validation** — PASS. 109 tests cover: no/empty/valid review state; one/multiple approved; rejected/deferred/needs-more-data/needs-policy/already-handled/not-applicable; source-changed; entity-absent; blocked; missing policy; missing evidence; ambiguous currency; multi-currency; multi-window; duplicate identical; duplicate conflict; malformed source; malformed/duplicate-key/NaN/null-byte review state; tampered manifest/artifact; invalid hashes; unknown id; formula-injection; Unicode notes; empty + populated packages; determinism; idempotency; atomic failure; immutability; validate-only; CLI.
- **Empty package** — PASS (`SESSION7_5_PACKAGE_READY_EMPTY`, package + exclusions written, exit 0).
- **Populated package** — PASS (`SESSION7_5_PACKAGE_READY`, checklist populated).
- **Duplicate conflict** — PASS (all conflicting members excluded, `SESSION7_5_CONFLICT_REVIEW_REQUIRED`).
- **Source-change** — PASS (approved-then-changed row excluded `SOURCE_CHANGED`; row removed excluded `ENTITY_ABSENT`).
- **Validate-only** — PASS (no package, correct counts, nonzero on blocked input).

## Real T2 validation

Run against the local real data (never committed; `runs/` gitignored):

| Field | Value |
|---|---|
| package_readiness | `SESSION7_5_PACKAGE_READY_EMPTY` |
| source_readiness | `SESSION7_3_ANALYSIS_READY_FOR_OWNER_REVIEW` |
| source_rows | 114 |
| analyzed_rows | 114 |
| review_state_records | 1 |
| approved_records | 0 |
| eligible_actions | 0 |
| excluded_items | 1 (`NOT_APPROVED` — the single local record is `DEFERRED`) |
| duplicate_identical / duplicate_conflicts / source_changed / blocked | 0 / 0 / 0 / 0 |
| Amazon counters / external network | 0 / 0 |

The one local review record reconciles as CURRENT (content + manifest match) but its status is `DEFERRED`, so the correct, non-forced result is an empty package. Phase 7.3 promoted directory and Phase 7.4 review-state file were **byte-identical (SHA-256) before and after** the run.

## Source immutability

Phase 7.5 is read-only toward both inputs. It creates no locks, caches, metadata, temp files, or exports inside `runs/T2/phase7/7.3/` or `runs/T2/phase7/7.4/review_state/`. Verified by SHA-256 tree hashing before/after (real T2 and synthetic tests).

## Determinism & idempotency

Two runs with identical inputs produce byte-identical analytical artifacts and an identical `package_content_sha256`; a repeated run into the same workspace returns `IDEMPOTENT_REUSE`; changing the reference date changes the package identity; a same-name run with altered content blocks (`SESSION7_5_PACKAGE_BLOCKED`) and preserves the last valid package.

## runs/ tracking

`runs/` is gitignored and untracked (`git check-ignore runs/T2/phase7/7.5` → ignored; `git ls-files runs/` → empty). No owner report data is committed. Working tree after implementation contains only the four intended new files.

## Prohibited-integration search

No functional integration code: no `requests` / `httpx` / `aiohttp` / `socket` / `urllib.request` / `http.client` / `boto3` / `selenium` / `playwright` / `webdriver` / `os.system` / `os.popen` / `subprocess` call / `eval(` / `exec(` / `float(` / `.amazonaws` / `sellercentral` / `advertising.amazon`. The words SP-API / Ads API / upload / browser / credential / cookie / token appear only inside safety disclaimers and constant **zero** boundary counters. Enforced by tests in `NoForbiddenIntegrations`.

## Amazon counters

All Amazon counters are constant zero — the process has no code path that could increment any of them: `amazon_connections=0, amazon_sp_api_calls=0, amazon_ads_api_calls=0, amazon_mutations=0, amazon_report_downloads=0, amazon_bulk_uploads=0, amazon_api_payloads=0, browser_automation_attempts=0, credential_store_count=0, cookie_store_count=0, token_store_count=0, session_store_count=0, external_network_calls=0, subprocess_executions_from_data=0`.

## Known limitations

- Only the three Phase 7.3 actionable recommendation labels are treated as manual-actionable; aggregate entities (campaign/ad-group/target) and non-actionable labels are surfaced in exclusions as `UNSUPPORTED_RECOMMENDATION_TYPE`, not in the checklist. This is deliberate: an actionable checklist item is only built when a concrete, supported manual action exists.
- The real local T2 review state contains no approved record, so the populated checklist path is exercised only by synthetic fixtures (as required — no owner data is committed).
- The package directory uses a content-addressed name (`pkg-<hash16>`); the prompt's illustrative `decision-package-<id>` layout is honored in spirit (deterministic, stable, collision-safe).

## Exact CLI

```
python -m production.phase7_owner_decision_package `
  --base-dir "runs/T2/phase7/7.5" `
  --phase7-3-dir "runs/T2/phase7/7.3" `
  --phase7-4-dir "runs/T2/phase7/7.4" `
  --reference-date "2026-07-21"
```

Optional: `--package-name`, `--format {text,json}`, `--include-deferred-summary`, `--validate-only`. Default behavior is safe; the CLI requires no server, starts no browser, and opens no Seller Central.

## Acceptance status & recommended next step

**NOT ACCEPTED** — implementation complete; an independent acceptance audit is recommended. Do not merge into `main`; do not create an accepted tag; do not begin Phase 7.6.

**Recommended exact next action:** run an independent Phase 7.5 acceptance audit against `ae07310` (reproduce the full suite, the 109 focused tests, the real-T2 empty-package result, source immutability, determinism, idempotency, and the prohibited-integration search), then — only on ACCEPT — create the accepted tag and merge.
