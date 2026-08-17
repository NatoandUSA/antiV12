# Session 7.4 — Offline Owner Review Dashboard — Independent Acceptance Audit

**Decision: `PHASE7_4_OWNER_DASHBOARD_ACCEPTED`**

Independent audit of the Phase 7.4 feature branch. The implementation report and proof gate were
**not** trusted; every material claim was independently inspected, reproduced, and tested. No
production code was modified during this audit. Not merged into `main`. Phase 7.5 not started.

---

## 1. Provenance and git identity (independently verified)

| Field | Value | Status |
|---|---|---|
| Branch | `phase7-4-owner-review-dashboard` | ✅ |
| Local HEAD | `bb52a5c96359d3f78cd4b38e0996c977877e878f` | ✅ = expected |
| Remote feature HEAD | `bb52a5c` (`origin/phase7-4-owner-review-dashboard`) | ✅ in sync |
| Baseline commit | `9ea8579` (= `main` = `origin/main`) | ✅ |
| Implementation commit | `c6b0dcc` feat | ✅ |
| Proof commit | `bb52a5c` docs | ✅ documentation-only |
| Checkpoint tag | `phase7-4-dashboard-checkpoint-9ea8579` → `9ea8579` (baseline) | ✅ |
| Phase 7.2 accepted tag | `phase7-2-cumulative-accepted-d5ad841` → `d5ad841` | ✅ unchanged |
| Phase 7.3 accepted tag | `phase7-3-accepted-7005275` → `7005275` | ✅ unchanged |
| `main` / `origin/main` | `9ea8579` | ✅ unchanged |
| Working tree | clean | ✅ |
| Tracked `runs/` data | none (`runs/` gitignored) | ✅ |
| Commits `main..HEAD` | exactly 2: `c6b0dcc`, `bb52a5c` | ✅ no unexpected commits |
| Prior accepted history amended | no | ✅ |
| Existing Phase 7.4 acceptance commit/tag | none prior to this report | ✅ |

**`git diff 9ea8579..c6b0dcc`** — 5 files, `+3038 / −0`, **all newly created**; **zero modifications**
to any existing production, core, test, or docs file. Pure addition (no regression surface in prior
phases from source changes).

- `production/phase7_owner_dashboard.py` (1334)
- `production/phase7_dashboard_static/index.html` (50)
- `production/phase7_dashboard_static/dashboard.css` (149)
- `production/phase7_dashboard_static/dashboard.js` (496)
- `tests/test_phase7_4_owner_dashboard.py` (1009)

**`git diff c6b0dcc..bb52a5c`** — 2 files, `+410 / −0`: `SESSION7_4-OWNER-DASHBOARD-IMPLEMENTATION-REPORT.md`
and `SESSION7_4-OWNER-DASHBOARD-PROOF-GATE.json`. **Documentation-only** — confirmed.

---

## 2. Canonical Phase 7.3 source contract

Canonical source path: **`runs/T2/phase7/7.3/promoted/`** (real data present; **no `final/` exists**).
Manifest `analysis-manifest.json` (`phase7-3-analysis-manifest-v1`) + `analysis.json`
(`phase7-3-analysis-v1`), plus 4 CSV + 1 MD artifact. Manifest `counts`: source=114, analyzed=114,
skipped=0, blocked=0, campaign=61, ad_group=61, decision_queue=0.

**Source adapter (`resolve_source_dir` / `load_source`).** Independently verified:

- Resolves the manifest under the workspace root, then `promoted/`, then `final/`. **`promoted/`
  strictly precedes `final/`** — verified with a synthetic root holding both (chose `promoted/`).
- Re-verifies integrity from bytes using the **producer's exact hash math** (`AA._VOLATILE`,
  `AA._recursive_strip`, `product_workspace.content_sha256`): manifest deterministic hash, then every
  `output_hashes` artifact, then required-artifact presence. Mirrors `phase7_ads_analysis` exactly.
- Rejects, before serving any recommendation data: malformed JSON, NaN/Infinity, null bytes, oversize
  (>256 MiB), wrong root types, wrong schema versions, duplicate stable IDs (`lineage_hash`), count
  inconsistency, manifest/analysis count mismatch, unsafe/traversal artifact names. Each independently
  reproduced against tampered temp copies → `SESSION7_4_SOURCE_BLOCKED` (source untouched).

The `final/` candidate is a documented secondary that the **current** Phase 7.3 contract never
produces (`promoted/` always wins, and the manifest schema gate blocks a Phase 7.2 `final/`). No
undocumented fallback to stale/non-promoted data was found.

---

## 3. Direct vs derived views

**Direct** (surfaced verbatim from `analysis.json`): campaign summary, ad-group summary, search-term
rows, decision-queue rows, data-quality, thresholds, readiness, Amazon boundary. Decimal metrics are
preserved as **strings**; integer counts as ints; missing values as `None` (em dash). Verified: **0
float values** anywhere in `analysis.json` and **0 floats** in the assembled model.

**Derived read-only** (Phase 7.3 publishes no dedicated artifact) — each audited; none invents
analytics or re-classifies:

| View | Authoritative source | Transform | Verdict |
|---|---|---|---|
| Targets | `phase7_ads_analysis.aggregate()` (7.3's own currency-isolating Decimal math) | grouping by (currency, campaign, ad_group, targeting, match_type); row classifications surfaced verbatim | ✅ no invented classification |
| Observations | row `reason_codes` | frequency rollup | ✅ counts only |
| Blocked | `primary_classification == NEEDS_OWNER_REVIEW` or `REQUIRED_METRIC_UNUSABLE`/`INVALID_NUMERIC_VALUE` reason code | filter | ✅ deterministic, no new logic |
| Manual review | decision-queue rows (queue priority) then blocked rows | ordering | ✅ no re-ranking of 7.3 logic |
| Policy requirements | `thresholds` (`target_acos_source`, date rule) | conditional surfacing | ✅ owner-declared suppresses target-ACoS req |

Derived **stable IDs** are deterministic SHA-256 of prefixed identity tuples (`tgt:`/`obs:`/`pol:`
…) — verified unique within every view over real data; Decimal values remain strings; missing values
remain missing; records are neither duplicated nor omitted. Multi-currency isolation verified (no
cross-currency metric merge; distinct targets/filters per currency).

---

## 4. Source immutability

Real `runs/T2/phase7/7.3/promoted/` hashed (SHA-256, all 7 files) **before and after** the complete
battery — startup, every GET API, review saves, bulk review, source reload, TSV/JSON/Markdown
exports, reset, server shutdown/restart, integrity-failure probes, source-change simulations:
**byte-identical**. No lock file, cache, temp, export, or review-state written inside any Phase 7.3
directory. Review state and exports land only under the Phase 7.4 base dir.

---

## 5. Source-change / re-review behavior

"Material change" = change to `content_sha256` of the material fields (metrics + classification +
rule + label + reason codes + evidence + currency). Verified:

- Material change → `SOURCE_CHANGED`; a material commitment (`APPROVED_FOR_MANUAL_ACTION` /
  `ALREADY_HANDLED`) sets `requires_re_review`, downgrades `effective_status` to `NEEDS_MORE_DATA`,
  and flips readiness to `SESSION7_4_SOURCE_CHANGED_REVIEW_REQUIRED`. **Approval is never silently
  carried.**
- Non-material change (e.g. `source_line_number`) → `CURRENT`; approval retained.
- Removed entity → `ENTITY_ABSENT`; never resurfaced as an active recommendation.
- `approved_only` export **excludes** materially-changed rows.

---

## 6. Review-state behavior

Keyed by **stable Phase 7.3 entity ID** (type-prefixed `lineage_hash` / identity-tuple hash) — never
row/sort/frontend position. Survives restart; revision increments; `created_at` preserved; stores
`source_manifest_sha256` + `content_sha256`. All eight allowed statuses accepted
(`UNREVIEWED`, `APPROVED_FOR_MANUAL_ACTION`, `REJECTED`, `DEFERRED`, `NEEDS_MORE_DATA`,
`NEEDS_POLICY`, `ALREADY_HANDLED`, `NOT_APPLICABLE`). Rejected: `APPLIED`, `EXECUTED`,
`SENT_TO_AMAZON`, `BID_CHANGED`, case-confused, empty/arbitrary strings, unknown entity IDs,
empty updates, oversize notes (>4000 chars). Bulk with one bad entry is atomic — nothing persists
(verified the "good half" did not leak to disk). Notes (`<script>`, `=HYPERLINK`, `+SUM`, `@cmd`,
pipes, tabs/newlines, Vietnamese Unicode, long strings) stored safely and rendered via `textContent`
/ input `value` (never `innerHTML`) — no execution path.

---

## 7. Export behavior + formula injection

TSV / JSON / Markdown; scopes `all` / `filtered` / `approved_only`. Server-generated filenames
(`owner-review-export-NNNN.ext`); no user-supplied path is accepted → traversal impossible; output
confined to the base dir. Deterministic ordering `(entity_type, entity_id)`. Every export carries the
exact disclaimer in all three formats:

> This file is for manual owner review only.
> No Amazon action has been performed.
> The owner must independently verify all data before making changes in Seller Central.

**TSV formula-injection safe**: a non-numeric cell whose first character is `= + - @ |` (or a
control char) is prefixed with `'`; tabs/CR/LF/null are neutralized. Verified `=HYPERLINK(...)`,
`+SUM`, `@cmd`, `|pipe`, `-2+3` are all quoted; a plain `-2.50` keeps its sign. No ragged rows. No
export resembles an Amazon bulk-upload template, SP-API/Ads-API payload, or an executable
shell/PowerShell/browser instruction. `amazon_action_performed: false` in every export.

---

## 8. HTTP security + loopback + network

- **Default host `127.0.0.1`**; server binds to `127.0.0.1`. Non-loopback (`0.0.0.0`, `::`, external
  IPs, hostnames, malformed) **refused** (CLI exit 2) unless `--allow-nonlocal-bind`; loopback
  aliases `127.0.0.1`/`::1`/`localhost` accepted.
- **Security headers on every response**: strict CSP (`default-src 'self'`; `object-src 'none'`;
  `base-uri 'none'`; `frame-ancestors 'none'`; no wildcards, no external hosts), `X-Content-Type-
  Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`,
  `Cross-Origin-Resource-Policy: same-origin`, `Cache-Control: no-store`. **No** `Access-Control-
  Allow-Origin` (no CORS).
- **Static allowlist** of exactly 3 files (dict membership); 16 traversal variants
  (`../`, encoded `%2e%2e`, double-encoded, backslash, absolute, null-byte, `//etc/passwd`,
  `dashboard.js/../analysis.json`, …) → **404, no data leak**. No directory listing.
- **Methods**: GET/POST/HEAD served; PUT/DELETE/PATCH/OPTIONS → 405; TRACE/CONNECT → 501 (no
  reflection). HEAD returns headers + correct `Content-Length`, no body.
- **POST validation**: non/wrong `Content-Type` → 415; unparseable length → 411; oversize (>1 MiB) →
  413 (body drained); null-byte → 400; malformed/NaN JSON → 400; non-object root → 400; unknown POST
  path → 404. Error bodies leak **no** repo path and **no** stack trace, and always carry
  `amazon_action_performed: false`.
- **No arbitrary file read or write.** No `urllib.request`/`socket` client/`requests`/`httpx`/
  `subprocess`/`eval`/`exec`; imports are stdlib only (`urllib.parse.urlsplit` is path parsing, not a
  network client). Frontend uses only same-origin `fetch`; `index.html`/CSS load only same-origin
  assets — no CDN/font/analytics/telemetry/WebSocket/EventSource/sendBeacon. Prohibited-integration
  search: only boundary **disclaimers** matched.

---

## 9. Permanent Amazon boundary

All counters are constant zeros with no code path to increment them:
`amazon_connections`, `amazon_api_calls`, `amazon_mutations` (campaign/bid/budget/keyword/negative/
target/upload), `browser_automation_attempts`, `credential/cookie/session/token storage`,
`report_download_attempts`, `network_calls`, `external_network_calls`, `telemetry_events` = **0**.
The local loopback HTTP server is not an Amazon connection or external network call.

---

## 10. Real T2 validation (independently reproduced; no data committed)

| Metric | Value | Method |
|---|---:|---|
| Dashboard readiness | `SESSION7_4_DASHBOARD_READY` | CLI + model |
| Phase 7.3 readiness | `SESSION7_3_ANALYSIS_READY_FOR_OWNER_REVIEW` | source |
| Source rows | 114 | direct `data_quality` |
| Analyzed rows | 114 | direct `data_quality` |
| Decision-queue rows | 0 | direct |
| Blocked rows | 0 | direct |
| Campaigns | 61 | direct `campaign_summary` |
| Ad groups | 61 | direct `ad_group_summary` |
| Targets | 74 | derived (unique campaign/ad_group/targeting/match_type × currency) |
| Search terms | 114 | direct rows |
| Observations | 10 | derived (distinct `reason_codes`) |
| Policy requirements | 2 | derived (target-ACoS OWNER_CONFIGURABLE + source-reported date rule) |
| Attribution windows | `7 day` | derived from `*_7d` metric states |
| Currency | USD (single) | direct |
| Reviewable items (all scope) | 310 (114+61+61+74) | export index |

Every reported count independently reproduced. Empty decision queue is handled as a useful empty
state, not an error; the rest of the dashboard stays populated. No count is misleading. Real source
byte-identical before/after; `runs/` remained untracked and clean.

---

## 11. Determinism, compile, and tests

- **Determinism**: two independent `build_model` builds of the real source are byte-identical
  (`canonical_json`); export ordering is a total sort; API JSON uses sorted keys.
- **`python -m compileall production core tests`** → **exit 0** (also in fresh worktree).
- **Phase 7.4 focused** (`tests.test_phase7_4_owner_dashboard`) → **94 passed** (13.97 s).
- **Phase 7.2 focused** → **377 passed, 1 skipped**.
- **Phase 7.3 focused** → **117 passed**.
- **Full repository suite** (`python -m unittest discover -s tests -p "test_*.py"`) →
  **2689 passed, 2 skipped, 0 failures** (python exit 0; 645 s). Matches the implementation report's
  claim; independently reproduced. (One benign `ResourceWarning: unclosed socket` from a server test's
  teardown — not a failure.)
- **Fresh worktree** (detached at `bb52a5c`, `runs/` absent): compileall exit 0; Phase 7.4 = 94
  passed; Phase 7.3 = 117 passed — self-contained on synthetic fixtures.

### Independent audit harnesses (outside tracked production files) — 210 checks, 0 failures

- **Offline logic/immutability** — 115/115: model+counts+determinism, decimals-as-strings/no-float,
  review-state validation (accept/reject matrix, atomic bulk, revision, persistence), material vs
  non-material change, `ENTITY_ABSENT`, approved-only exclusion, export formula-injection, host
  validation, source-resolution precedence, integrity-failure detection (tampered artifact/manifest,
  missing artifact, wrong schema, NaN, null byte, duplicate ID), real-source immutability.
- **Live loopback HTTP server** — 74/74: security headers, static allowlist + 16 traversal variants,
  method rejection, HEAD, all 14 GET endpoints, POST body validation, review roundtrip (writes to
  base not 7.3), export disclaimer + formula-safety, error hygiene, post-op immutability.
- **Synthetic scenarios** — 21/21: multi-currency isolation, multi-attribution-window derivation,
  populated decision queue, blocked queue, missing-optional-metrics, owner-declared policy
  suppression.

---

## 12. Frontend / browser validation (method + limitation)

Validated at the **HTTP/asset + static-analysis layer** (headless environment — no interactive
browser available):

- `GET /` serves the shell containing `OFFLINE REVIEW MODE`; CSS/JS served with correct content
  types; assets contain no `http(s)://`, CDN, external-font, `integrity=`, analytics, or telemetry
  references; CSP forbids external `script-/style-/connect-src`.
- `dashboard.js` passes `node --check` (syntactically valid → no parse-time console errors).
- Owner notes and all data cells render via `textContent`/input `value`, not `innerHTML` — HTML/note
  injection cannot execute.

**Acceptance limitation (already disclosed by the implementation report, carried forward):**
interactive in-browser validation — click-through of sidebar nav, sorting, filters, drawer, note
save, bulk review, export download, empty-queue state, focus/keyboard, and dev-tools "zero external
requests / zero console errors" — was **not** performed and remains an **owner verification step**
before routine use. This does not affect the safety, correctness, offline, immutability, or
Amazon-boundary guarantees, all of which were verified server-side.

---

## 13. Documentation review

The implementation report and proof gate are **accurate**: canonical `promoted/` path, direct-vs-
derived views, producer-exact integrity, stable-ID contract, source-change rules, export disclaimer,
security boundaries, readiness states, real T2 counts (all reproduced, incl. 310 reviewable items),
Amazon counters, and the browser-validation limitation are all correctly stated. The absence of
`campaign_id`/`confidence` in the Phase 7.3 output (→ em dash) is confirmed and honestly disclosed.
No inaccuracy requiring a documentation correction was found.

---

## 14. Non-blocking observations (no fix required)

None affect safety, correctness, determinism, offline operation, source immutability, or the Amazon
boundary. Recorded for the owner; no production change was made.

1. **Keep-alive body-drain gap.** A POST with a wrong `Content-Type` (415) or unparseable
   `Content-Length` (411) does not drain its request body; on a reused HTTP/1.1 keep-alive
   connection the leftover bytes cause the *next* pipelined request to mis-parse (server replies
   `501`, does **not** hang/crash, touches no source, leaks nothing cross-client). The real frontend
   always sends `application/json`, so it never triggers this; loopback single-owner makes impact
   negligible. Optional future hardening: drain or close on these two error paths.
2. **`final/` secondary candidate** is never produced by the current Phase 7.3 contract; `promoted/`
   always precedes it and the manifest schema gate blocks a Phase 7.2 `final/`. Behavior is safe and
   documented; no stale-data path was reachable.
3. **`_attribution_windows` scans only the first 200 rows** (silent bound; fine for 114 rows;
   `_build_observations` scans all rows). Latent only if a window first appears past row 200.
4. **Cosmetic**: a redundant `elif/else` in `merge_review_state` (both branches yield `CURRENT`).

---

## 15. Decision and next action

**`PHASE7_4_OWNER_DASHBOARD_ACCEPTED`.** Phase 7.4 is safe, correct, deterministic, genuinely
offline, loopback-only, read-only toward and byte-immutable of the accepted Phase 7.3 source,
incapable of Amazon action or network access, local-review/local-export only, and compatible with
the accepted Phase 7.2 and 7.3 contracts (their focused suites and accepted tags are unchanged). No
production defect was found; no production code was modified.

**Acceptance artifacts**: this report + one acceptance commit + one annotated tag
`phase7-4-owner-dashboard-accepted-<hash>`. Push the feature branch and the tag.

**Do not** merge into `main`. **Do not** begin Phase 7.5. Recommended owner step before routine use:
the one-time interactive in-browser pass described in §12.
