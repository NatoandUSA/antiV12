# Session 7.4 — Offline Owner Review Dashboard — Implementation Report

## 1. Summary

Phase 7.4 delivers a fully offline, read-only **Owner Review Dashboard** over the promoted
Phase 7.3 Sponsored Products analysis. It is a *review and local-export interface only*. It connects
to nothing, performs no Amazon action, and never modifies the Phase 7.3 source. Every Amazon action
counter is a constant zero.

The dashboard is a Python-standard-library HTTP server bound to `127.0.0.1` that serves a
single-page vanilla-JS front end and a small read-only JSON API. Owner review decisions are local
labels (never Amazon actions) persisted under the Phase 7.4 workspace and keyed by stable Phase 7.3
IDs. Exports are local TSV / JSON / Markdown files, each carrying a manual-action disclaimer and
formula-injection-safe cells.

## 2. Provenance

| Field | Value |
|---|---|
| Branch | `phase7-4-owner-review-dashboard` |
| Baseline commit | `9ea8579` |
| Checkpoint tag | `phase7-4-dashboard-checkpoint-9ea8579` |
| Accepted Phase 7.2 tag | `phase7-2-cumulative-accepted-d5ad841` |
| Accepted Phase 7.3 tag | `phase7-3-accepted-7005275` |
| Implementation commit | `c6b0dcc` (feat) |
| Proof commit | docs commit that adds this report (see final response) |
| Fresh-worktree verification | PASS at `c6b0dcc` — `runs/` absent, compileall clean, 94 Phase 7.4 tests pass on synthetic fixtures only |

## 3. Files created

| File | Lines | Purpose |
|---|---:|---|
| `production/phase7_owner_dashboard.py` | 1334 | The ONE Phase 7.4 authority: source adapter + validation, entity/view assembly, local review-state store, local export, HTTP server, CLI. |
| `production/phase7_dashboard_static/index.html` | 50 | Single-page shell: offline banner, status bar, sidebar, content, detail drawer. |
| `production/phase7_dashboard_static/dashboard.css` | 149 | Self-contained theme (light/dark), no external fonts/assets. |
| `production/phase7_dashboard_static/dashboard.js` | 496 | Vanilla-JS app: views, client-side filter/sort, review controls, export. No external code. |
| `tests/test_phase7_4_owner_dashboard.py` | 1009 | 94 tests (synthetic fixtures only). |
| `SESSION7_4-OWNER-DASHBOARD-IMPLEMENTATION-REPORT.md` | — | This report. |
| `SESSION7_4-OWNER-DASHBOARD-PROOF-GATE.json` | — | Machine-readable proof gate. |

**Files modified:** none. Phase 7.4 is a pure addition; no prior-phase production, test, or
documentation file was changed.

## 4. Source adapter design

Phase 7.3's real promoted contract (inspected before implementation — the prompt's guessed artifact
list does **not** match) is a single directory:

```
runs/T2/phase7/7.3/promoted/
    analysis.json              (phase7-3-analysis-v1)   — the canonical model
    analysis-manifest.json     (phase7-3-analysis-manifest-v1)
    campaign-summary.csv  ad-group-summary.csv  search-term-analysis.csv
    owner-decision-queue.csv   owner-report.md
```

`analysis.json` carries every view the dashboard renders: `campaign_summary`,
`ad_group_summary`, `search_term_analysis` (rows with `lineage_hash`, `evidence`, `reason_codes`,
`primary_classification`, `owner_review_label`), `owner_decision_queue`, `data_quality`,
`thresholds`, `amazon_boundary`, and readiness. There are **no** separate observations / blocked /
manual-review / policy artifacts, so the dashboard derives those views read-only from the analysis
without inventing analytics or re-classifying anything:

* **Targets** — a navigational grouping produced by Phase 7.3's own `aggregate()` authority
  (identical Decimal-safe, currency-isolating arithmetic used for its campaign/ad-group summaries).
  Classification is not invented; the set of row-level classifications present is surfaced verbatim.
* **Observations** — a frequency rollup of the `reason_codes` already present on each row.
* **Blocked recommendations** — rows classified `NEEDS_OWNER_REVIEW` or carrying a
  `REQUIRED_METRIC_UNUSABLE` / `INVALID_NUMERIC_VALUE` reason code.
* **Manual review queue** — the actionable decision-queue rows (in queue-priority order) followed by
  the blocked rows.
* **Owner policy requirements** — derived from `thresholds` (e.g. `target_acos_source =
  NEUTRAL_DEFAULT_OWNER_CONFIGURABLE`, source-reported date rule).

**Validation** re-verifies the promoted state from its own bytes using the producer's exact hash
math (`AA._VOLATILE`, `AA._recursive_strip`, `product_workspace.content_sha256`): manifest
deterministic hash, then every `output_hashes` artifact, then required-artifact presence. It then
rejects, before any recommendation data is served: malformed JSON, NaN/Infinity tokens, null bytes,
oversize artifacts, wrong root types, wrong schema versions, duplicate stable IDs
(`lineage_hash`), count inconsistency (`source = analyzed + skipped + blocked`, cross-checked against
the manifest), and unsafe/traversal artifact names. On failure the dashboard still starts in a safe
**`SESSION7_4_SOURCE_BLOCKED`** mode that shows the exact reasons and hides recommendation data.

## 5. Local review-state schema (`review_state/review-state.json`, `phase7-4-review-state-v1`)

Keyed by **stable entity ID** (never row/sort/frontend position). Search-term / recommendation /
blocked / manual-review entities use a type-prefixed `lineage_hash`; campaign / ad-group / target
entities use a stable hash of their identity tuple. Each record:

```
entity_id, entity_type, review_status, owner_note,
source_manifest_sha256, content_sha256, revision, created_at, updated_at
```

Allowed local statuses (none performs an Amazon action): `UNREVIEWED`,
`APPROVED_FOR_MANUAL_ACTION`, `REJECTED`, `DEFERRED`, `NEEDS_MORE_DATA`, `NEEDS_POLICY`,
`ALREADY_HANDLED`, `NOT_APPLICABLE`.

**Source-change handling.** On load, each record is compared to the current source by `content_sha256`
(a hash of the material fields: classification, rule, label, reason codes, evidence, metrics). Unchanged
→ status preserved (`CURRENT`). Materially changed → `SOURCE_CHANGED`; entity gone → `ENTITY_ABSENT`.
A material commitment (`APPROVED_FOR_MANUAL_ACTION` / `ALREADY_HANDLED`) on a changed record is
**never silently carried**: `requires_re_review` is set and `effective_status` downgrades to
`NEEDS_MORE_DATA`, flipping dashboard readiness to `SESSION7_4_SOURCE_CHANGED_REVIEW_REQUIRED`.
Runtime timestamps live only in the local review-state file; analytical payloads stay deterministic.

## 6. Export schema (`exports/…`, `phase7-4-export-v1`)

Formats: **TSV, JSON, Markdown**. Scopes: `all`, `filtered`, `approved_only` (an approval whose
source changed is excluded). Server-generated filenames (`owner-review-export-NNNN.ext`) — no
user-supplied path is ever accepted, so traversal is impossible. Every export includes: export schema
version, source Phase 7.3 manifest hash, export date, filter summary, item count, and per-item review
status / recommendation ID / entity identifiers / evidence / reason / owner note. Every export
carries the disclaimer:

> This file is for manual owner review only.
> No Amazon action has been performed.
> The owner must independently verify all data before making changes in Seller Central.

TSV cells are formula-injection-safe: a non-numeric cell whose first character is one of
`= + - @ | \t \r` is prefixed with `'`; tabs/newlines are collapsed. **No** Amazon bulk-upload
template, API payload, or executable browser instruction is ever generated.

## 7. Security headers (every response)

```
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self';
    img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none';
    base-uri 'none'; frame-ancestors 'none'
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
X-Frame-Options: DENY
Cross-Origin-Resource-Policy: same-origin
Cache-Control: no-store
```

Loopback-only bind (default `127.0.0.1`; a non-loopback host is refused unless
`--allow-nonlocal-bind` is passed explicitly). Static allowlist only (`index.html`, `dashboard.css`,
`dashboard.js`); unknown static path → 404; no directory listing. GET/POST only (others → 405);
POST endpoints require `application/json` (else 415), enforce a 1 MiB body limit (413, with the
oversize body drained so the client never deadlocks), and reject malformed/NaN/null-byte bodies (400).
No CORS, no proxy, no shell/eval/dynamic execution, no arbitrary file read/write.

## 8. Readiness states

`SESSION7_4_DASHBOARD_READY`, `SESSION7_4_SOURCE_REQUIRED`, `SESSION7_4_SOURCE_BLOCKED`,
`SESSION7_4_SOURCE_CHANGED_REVIEW_REQUIRED`, `SESSION7_4_REVIEW_STATE_READY`,
`SESSION7_4_EXPORT_READY`, `SESSION7_4_EXPORT_BLOCKED`. READY is never claimed when source
validation fails.

## 9. Test results

| Suite | Result |
|---|---|
| Baseline full suite (at `9ea8579`) | **2595 tests — OK (skipped=2)** |
| Phase 7.4 focused (`tests.test_phase7_4_owner_dashboard`) | **94 tests — OK** |
| Phase 7.2 focused | **377 tests — OK (skipped=1)** |
| Phase 7.3 focused | **117 tests — OK** |
| Full suite after implementation | **2689 tests — OK (skipped=2)** |
| `compileall production core tests` | **exit 0** |

The 94 Phase 7.4 tests cover the full required matrix: CLI help / default loopback host / custom
port / non-loopback refusal; source required / valid load / missing-or-invalid manifest / tampered
artifact / missing artifact / malformed JSON / duplicate IDs / NaN / Infinity / null byte / unsafe
path / count consistency; empty and populated queues; every view + API endpoint; static serving +
unknown-path + directory-listing + method + body-limit + content-type + all six security headers;
local review save / note / stable-ID persistence / bulk / invalid-status rejection / restart survival
/ manifest+content hash storage / source-change detection / re-review requirement / source
immutability; TSV+JSON+Markdown export / disclaimer / approved-only / filtered / formula-injection /
server-generated filename / no-upload-template; missing-stays-missing / decimals-as-strings /
no-float pipeline; filters; deterministic model, API and export ordering; no external
resources/scripts/fonts/telemetry; no network/Amazon integration; and all Amazon counters zero.

## 10. Synthetic validation

Server started against synthetic fixtures on an ephemeral loopback port. All endpoints return 200;
sortable tables, filters, review controls, bulk apply, and TSV/JSON/Markdown export exercised; empty
decision queue renders a useful empty state (not an error) while the rest of the dashboard stays
populated; blocked-source fixture starts the server in blocked mode and hides recommendation data;
the offline banner and per-format disclaimers are present. No external network request is possible
(strict CSP + loopback-only + no external references in the static assets).

## 11. Manual browser validation

Because this is a headless environment, browser-level behavior was validated at the HTTP layer
(equivalent to browser dev-tools inspection): `GET /` returns the HTML shell containing
`OFFLINE REVIEW MODE`; `dashboard.css` and `dashboard.js` are served with correct content types;
the static assets contain no `http(s)://`, CDN, external-font, `integrity=`, analytics, or telemetry
references; the CSP forbids any external `script-src`/`style-src`/`connect-src`. Recommended local
owner check: run the CLI below, open the URL, confirm sidebar navigation, sorting (missing values
render as `—`), the detail drawer, review-status selection, note save, bulk review, export download,
empty-queue state, the persistent offline banner, and — in dev-tools — zero external network
requests and no console errors.

## 12. Real T2 validation

CLI run against `runs/T2/phase7/7.3`:

```
dashboard_readiness=SESSION7_4_DASHBOARD_READY
source_readiness=SESSION7_3_ANALYSIS_READY_FOR_OWNER_REVIEW
source_rows=114
analyzed_rows=114
review_items=0
blocked_items=0
amazon_connections=0
amazon_api_calls=0
amazon_mutations=0
dashboard_url=http://127.0.0.1:8740
```

Live endpoints against real T2: campaigns=61, ad-groups=61, targets=74, search-terms=114,
observations=10, recommendations=0, blocked=0, manual-review=0 (empty queue handled correctly),
policy-requirements=2. A review save and a TSV export (310 reviewable items, disclaimer present)
were written under `runs/T2/phase7/7.4/`. **The Phase 7.3 promoted directory was byte-identical
before and after** (source immutability verified by SHA-256). Git remained clean except the intended
new production/test files; `runs/` stayed untracked.

## 13. Determinism

`build_model` is byte-stable across runs (no runtime fields in the analytical model); API JSON uses
`sort_keys`; export content is byte-identical across fresh workspaces under a fixed clock; export
ordering is a total sort by `(entity_type, entity_id)`. Verified by dedicated tests.

## 14. Amazon boundary

| Counter | Value |
|---|---|
| Amazon connections | 0 |
| Amazon API calls | 0 |
| Amazon mutations (campaign/bid/budget/keyword/negative/target/upload) | 0 |
| Browser automation attempts | 0 |
| Credential/cookie/session/token storage | 0 |
| Network calls | 0 |

## 15. Known limitations

* Phase 7.3 publishes no target-level or observation artifacts; the Targets and Observations views are
  **derived read-only** from `analysis.json` (Targets via Phase 7.3's own `aggregate()` authority,
  Observations as a reason-code rollup). They add no new classification and cannot disagree with
  Phase 7.3's arithmetic.
* Campaign IDs are not present in the SP search-term report, so the Campaign-ID column renders `—`.
* `confidence` is not emitted by Phase 7.3 and renders `—`.
* Browser validation is performed at the HTTP/asset layer (headless environment); an interactive
  local browser pass is recommended before acceptance.
* The current real T2 dataset has an empty decision queue (0 recommendations / 0 blocked); the
  populated-queue and blocked paths are exercised by synthetic fixtures only.

## 16. Exact CLI

```powershell
python -m production.phase7_owner_dashboard `
  --base-dir "runs/T2/phase7/7.4" `
  --phase7-3-dir "runs/T2/phase7/7.3" `
  --host "127.0.0.1" `
  --port 8740
```

Dashboard URL: `http://127.0.0.1:8740`

## 17. Acceptance status

**NOT ACCEPTED.** Implementation complete and self-verified. An independent acceptance audit is
recommended before merging to `main` and before creating an accepted tag.

## 18. Recommended next action

Independent acceptance audit of this branch. Do not merge to `main`, do not create an accepted tag,
and do not begin Phase 7.5 until the audit passes.
