# SESSION 7.11 — Connected Research Watchlists, Change Detection & Owner Alerts — Implementation Report

## Identity
- **Branch:** `phase7-11-connected-research-watchlists`
- **Exact baseline:** `9d9a4528f04af90640019eb872d2561879bfa253` (== `origin/main` == `main` at session start)
- **Checkpoint tag:** `phase7-11-connected-research-watchlists-checkpoint-9d9a452`
- **Implementation commit (feat):** `fa6da8b7923a5c1f8efa493f735d0fc37661d149`
- **Proof commit (docs):** see final response (committed after this report)
- **Accepted prior tags (all present, unmoved):** `phase7-2-cumulative-accepted-d5ad841`,
  `phase7-3-accepted-7005275`, `phase7-4-owner-dashboard-accepted-eebecc5`,
  `phase7-5-owner-decision-package-accepted-66d972d`, `phase7-6-manual-action-tracker-accepted-f1d11d8`,
  `phase7-7-outcome-followup-accepted-581ae49`, `phase7-8-owner-operations-dashboard-accepted-80333ec`,
  `phase7-9-connected-backup-update-recovery-accepted-383569e`,
  `phase7-10-connected-public-research-accepted-9888e69`
- **Phase 7.11 acceptance tag:** NOT created (per instructions).

## Connectivity boundary & Seller-Central prohibition
Phase 7.11 performs **no** network work of its own. Every fetch, parse, redirect, robots decision,
SSRF guard and the **Amazon-account denial** are delegated to the accepted Phase 7.10 authority and
`core.network_policy`, whose Seller-Central / seller-account classification always wins **first**.
Seller Central, Seller Central login, seller OAuth, seller credentials/cookies/sessions/tokens,
SP-API, Ads API, automatic report downloads, campaign/bid/budget/keyword/target/negative mutation,
listing/inventory mutation, bulk upload, browser automation and CAPTCHA bypass are permanently
prohibited and unreachable from any Phase 7.11 code path, flag or config. Every record carries the
constant-zero Amazon counters; no code path can increment them. Public Amazon US retail product
pages are reachable **only** through the accepted Phase 7.10 authority.

## Files
- **Created:**
  - `production/phase7_connected_research_watchlists.py` — the ONE Phase 7.11 authority.
  - `tests/test_phase7_11_connected_research_watchlists.py` — 189 focused tests.
  - `docs/PHASE7_11-RESEARCH-WATCHLIST-POLICY.md` — policy.
  - `SESSION7_11-CONNECTED-RESEARCH-WATCHLISTS-IMPLEMENTATION-REPORT.md` — this report.
  - `SESSION7_11-CONNECTED-RESEARCH-WATCHLISTS-PROOF-GATE.json` — proof gate.
- **Modified:** none. `git diff HEAD` against the baseline shows **no accepted file changed**.
- **Dependencies:** none added. Standard library only (`argparse`, `datetime`, `os`, `re`, `sys`,
  `time`, `unicodedata`, `decimal`, `zoneinfo`) plus the reused in-repo authorities
  (`production.phase7_connected_public_research`, `core.network_policy`, `core.diagnostics`,
  `core.money`).

## Phase 7.10 authority reuse
Imports and orchestrates the accepted 7.10 authority via its documented Python functions: `Config`,
`run_plan`, `verify_run`, `normalize_plan`, `_normalize_descriptor`, `_normalize_locator`,
`_source_id`, `_load_manifest`, `_load_capture`, `_tsv_cell`, `canonical_json`, `content_sha256`,
`redact`, the atomic writers, and the readiness/capture-status/`AMAZON_COUNTERS`/`_SUCCESS_STATUSES`
constants. No 7.10 fetch, parser or network logic is copied. No narrowly-additive 7.10 helper was
required — the accepted public surface was sufficient, so **no accepted file was modified**.

## Workspace
Phase 7.11 writes only under `runs/T2/phase7/7.11/` (`watchlists/`, `state/`, `executions/`,
`comparisons/`, `changes/`, `alerts/`, `alert_history/`, `reports/`, `scheduler_plans/`,
`validation/`, `locks/`, `logs/`, and a 7.11-owned `research/` 7.10-format workspace for new
fetches). The accepted `runs/T2/phase7/7.10/` tree is read-only (used only by `compare`) and never
modified; the Phase 7.3–7.9 trees are never touched. `runs/` remains git-ignored; nothing under
`runs/` is committed.

## Watchlist schema
Canonical JSON. `watchlist_id = "wl-" + sha256(canonical_json(identity))[:24]` over
`{name, timezone, schedule, sources (7.10 descriptor + comparison policy), comparison_policy,
alert_rules (selector/type/field/operator/value/unit/currency/severity), tags}`. Reordered JSON keys
give the same id; the id never depends on timestamp, mtime, temp path, pid, uuid or enumeration order
and excludes mutable annotations (`description`, `owner_notes`, `created_by`, `enabled`) and all
acknowledgement state. Unknown fields and secret/header/cookie/command/shell/eval/dynamic-import
fields are rejected (reused 7.10 forbidden-field scan + extra shapes).

## Source descriptors
Each source reuses the accepted Phase 7.10 descriptor schema (`public-url`, `rss`, `github`, `pypi`,
`amazon-public-product`, `local-file`, research-plan-compatible) validated through
`R._normalize_descriptor`; anything 7.10 would reject (arbitrary URL, bad locator, unknown type) is
rejected. No raw request headers, cookies or API tokens are permitted. Each watchlist item has a
stable item id derived from `{watchlist_id, 7.10 descriptor, owner label, comparison policy}`.

## Schedule model
`manual`, `hourly`, `daily`, `weekly`, `interval-hours`; **1-hour minimum** automatic interval;
`zoneinfo` timezones (unknown rejected). Handles next-due, due/not-due, missed runs, bounded catch-up
(≤1 run/invocation), clock-moving-backward and DST forward/backward. Schedule timestamps are
operational only and never affect any identity. No daemon, no continuous run, no auto-registration.

## Scheduler plan
`scheduler-plan` emits a deterministic read-only plan (Windows PowerShell / cron / manual examples),
using the current Python executable, safe path quoting, no secret and no Seller-Central target. It
never executes or registers `schtasks`/`cron`/`crontab`/`systemctl`/`launchctl`/Task Scheduler COM,
and states `OWNER_ACTION_REQUIRED`. Deterministic apart from the operational path metadata; a stable
`template_hash` covers the non-path template.

## Baseline selection
Comparison uses accepted, verified evidence only. Current accepted capture vs the immediately
preceding accepted capture for the same stable source identity (source id, capture id, content hash,
evidence ids, run lineage, manifest integrity), never by mtime. First successful observation is
`INITIAL_BASELINE`. A blocked/unavailable/corrupt current capture never overwrites the last valid
baseline. Corrupt captures are detected by re-hashing raw bytes (`INTEGRITY_BLOCKED`).

## Change-event model
Content-addressed `change_id = "chg-" + sha256(field/lineage/value identity)[:24]`, independent of
event timestamp, row order, sort, filter or pagination. Statuses: `INITIAL_BASELINE`, `ADDED`,
`REMOVED`, `CHANGED`, `UNCHANGED`, `SOURCE_UNAVAILABLE`, `SOURCE_BLOCKED`, `PARSE_PARTIAL`,
`INTEGRITY_BLOCKED`, `COMPARISON_NOT_AVAILABLE`. Each event carries schema version, watchlist/item/
source ids, normalized locator, previous/current capture ids + content hashes, evidence type, field
path, previous/current value, unit, currency, change type, numeric delta/percent where safe,
warnings, comparison method, parser lineage, source lineage and owner-rule matches.

## Comparison rules
Exact field-level comparison by stable evidence field path + type. Permitted normalization: Unicode
NFC, whitespace collapse, canonical 7.10 URL normalization, `Decimal` parsing for explicit numbers,
and case-folding **only** for owner-documented case-insensitive field paths. No fuzzy matching, no
LLM comparison, no semantic embeddings. A page-content-hash change with no accepted evidence change
is recorded as source-content-changed / evidence-unchanged.

## Numeric / currency / unit safety
Numeric delta/percent computed only when both values are explicit numeric evidence (via
`core.money`, Decimal-only, no float) with **identical unit and identical currency**; percent is
never computed when the previous value is zero. No aggregation across currencies, marketplaces,
incompatible units, incompatible parser definitions or incompatible source identities. No NaN, no
Infinity, no authoritative float in any record or export.

## Owner alert-rule model & severity
Explicit owner rules only. Operators: `field-changed`, `field-added`, `field-removed`,
`value-equals`, `value-contains`, `numeric-above`, `numeric-below`, `absolute-delta-at-least`,
`percent-change-at-least`, `source-unavailable`, `source-blocked`, `parse-partial`,
`integrity-blocked`. Exact / bounded-substring matching — no unrestricted regex, no fuzzy match. A
numeric rule with incompatible unit/currency does not fire. Owner severity ∈ {INFO, REVIEW,
IMPORTANT, CRITICAL} is owner-defined; the system never computes a severity, urgency, score or
recommendation.

## Alert identity & acknowledgement history
`alert_id = "alt-" + sha256({watchlist_id, item_id, rule_id, change_id})[:24]`, stable across
acknowledge/dismiss/reopen; mutable status is stored separately. Duplicate OPEN alerts for the same
rule+change are never recreated. `acknowledge-alert`/`dismiss-alert`/`reopen-alert` require an exact
alert id + explicit owner actor (optional note) and append to a per-watchlist **append-only
hash-chained** history (`event_hash = sha256({seq, alert_id, action, actor, note, prev_state_hash})`,
chained via `prev_state_hash`, aggregate `head_hash`); operational timestamps live outside the hash.
A corrupted, deleted or reordered chain blocks further state updates (`ALERT_STATE_BLOCKED`).

## Locking / atomicity / idempotency
Per-watchlist `O_CREAT|O_EXCL` locks under `runs/T2/phase7/7.11/locks/` with owner process metadata,
released only by the owning process; a lock older than the bound is reported stale but never silently
removed, with explicit `--break-stale-lock` recovery. Different watchlists run independently. All
state is written via temp-sibling + fsync + read-back + atomic replace, so a failed write never
overwrites the last valid artefact. Repeated execution over identical accepted 7.10 evidence reuses
the same comparison/change/alert identities and never duplicates alerts.

## Exports
Deterministic `watchlist_snapshot.json`, `watchlist_changes.tsv`, `watchlist_report.md`,
`watchlist_alerts.json`. TSV formula-injection neutralized (leading `= + - @` etc. quoted) while
`-2.50` is preserved; tabs/newlines neutralized; Vietnamese Unicode preserved; equal columns per row.
No credentials, tokens, cookies, Authorization headers, signed URLs, local absolute paths, customer
data, invented demand/sales or recommendations; constant-zero Amazon counters included.

## Readiness states
`SESSION7_11_WATCHLIST_READY / _READY_EMPTY / _READY_PARTIAL / _WATCHLIST_REQUIRED / _WATCHLIST_BLOCKED
/ _NOT_DUE / _SOURCE_REQUIRED / _SOURCE_BLOCKED / _NETWORK_UNAVAILABLE / _RESEARCH_RUN_BLOCKED /
_BASELINE_REQUIRED / _COMPARISON_READY / _COMPARISON_READY_EMPTY / _COMPARISON_BLOCKED / _ALERTS_READY
/ _ALERTS_READY_EMPTY / _ALERT_STATE_BLOCKED / _INTEGRITY_BLOCKED / _SELLER_CENTRAL_POLICY_BLOCKED`
(plus read/utility success states). Policy and integrity blocks exit nonzero; NOT_DUE is not an
error; a valid first baseline is not an error; a no-change run is READY_EMPTY.

## Test results (true Python exit codes captured)
- **Baseline (before, HEAD `9d9a452`):** full suite `3473 passed, 4 skipped, 0 failures`, exit `0`.
- **Phase 7.11 focused:** `189 passed`, exit `0`.
- **Full suite (with 7.11):** `3662 passed, 4 skipped, 0 failures, 0 errors`, exit `0`
  (`3473 + 189 = 3662`, zero regressions).
- **Prior focused suites (unchanged, all match baseline expectations):** 7.2 `377 (1 skip)`,
  7.3 `117`, 7.4 `94`, 7.5 `109`, 7.6 `100`, 7.7 `93`, 7.8 `152`, 7.9 `139 (1 skip)`,
  7.10 `191 (1 skip)`.
- **compileall (`core production tests`):** exit `0`.

## Synthetic validation
Synthetic Phase 7.10 evidence (Amazon public product, PyPI, GitHub, RSS, public HTML, unavailable/
blocked/partial) drives: first baseline; unchanged second run; title change; price text change;
availability change; rating-count change; currency mismatch (no delta); source-content change with no
evidence change; source unavailable; corrupt current/previous capture; alert creation + dedup;
acknowledgement; history corruption/deletion/reorder detection; due/not-due scheduling; concurrent
lock behavior; deterministic exports. No committed test contacts the real Internet.

## Optional live-network result
`NOT_RUN` (owner-controlled `PHASE7_11_ALLOW_LIVE_NETWORK`; committed tests never contact the real
Internet).

## Upstream immutability
The accepted Phase 7.10 module and `core/network_policy.py`, `core/diagnostics.py`, `core/money.py`
are byte-identical before and after all Phase 7.11 operations (recorded sha256 before == after in the
proof gate). `git diff HEAD` against the baseline shows no accepted file modified. A functional check
confirms Phase 7.11 reads but never writes the 7.10 research tree (tree hash unchanged after
`compare`/`export`). No Phase 7.11 file appears under any Phase 7.3–7.10 directory.

## runs tracking
`runs/` remains in `.gitignore`; nothing under `runs/` is staged or committed.

## Prohibited-integration scan
Code-token scan (strings/comments excluded, since safety strings are not integrations): no
`subprocess`, `selenium`, `playwright`, `webdriver`, `webbrowser`, `pyppeteer`, `requests`,
`smtplib`, `urllib`, `socket`, `shell`, `eval`, `exec`, `system`, `sellingpartner` or `captcha`
identifiers. Imports are only stdlib + the reused authorities. No Seller-Central / SP-API / Ads-API
literal present. No browser automation, no shell execution, no arbitrary subprocess, no scheduler
registration, no outbound email/webhook/chat.

## Seller-Central counters
All constant zero (`seller_central_connections`, `amazon_seller_api_calls`, `amazon_ads_api_calls`,
`amazon_seller_auth_calls`, `amazon_seller_mutations`, `amazon_report_downloads`,
`amazon_bulk_uploads`, `browser_automation_actions`, `amazon_credential_store_count`).

## Fresh-worktree verification
Detached worktree at `fa6da8b`: `runs/` absent; compileall exit `0`; 7.11 focused `189 OK`;
7.10 focused `191 OK (1 skip)`; 7.9 focused `139 total` (2 skips when `runs/` absent, accurate skip
reporting); prohibited scan clean. Worktree removed afterward; `git clean` never run on the primary
workspace.

## Known limitations
- Change detection reflects only what the accepted Phase 7.10 parsers extract; a page whose visible
  change is not surfaced as 7.10 evidence is recorded as source-content-changed / evidence-unchanged.
- A watchlist-level schedule governs all of its sources (no per-source schedule in this phase).
- Browser click-through of the exported reports remains an owner step (no UI server in this phase).
- The optional live-network smoke is owner-run and not part of the committed suite.

## Exact CLI examples
```
python -m production.phase7_connected_research_watchlists --base-dir "runs/T2/phase7/7.11" \
  --research-dir "runs/T2/phase7/7.10" validate-only --definition "watchlist.json"

python -m production.phase7_connected_research_watchlists --base-dir "runs/T2/phase7/7.11" \
  --research-dir "runs/T2/phase7/7.10" create-watchlist --definition "watchlist.json"

python -m production.phase7_connected_research_watchlists --base-dir "runs/T2/phase7/7.11" \
  --research-dir "runs/T2/phase7/7.10" run-watchlist --watchlist-id "<watchlist-id>"

python -m production.phase7_connected_research_watchlists --base-dir "runs/T2/phase7/7.11" \
  --research-dir "runs/T2/phase7/7.10" run-due --reference-time "2026-07-23T10:00:00+07:00"

python -m production.phase7_connected_research_watchlists --base-dir "runs/T2/phase7/7.11" \
  acknowledge-alert --alert-id "<alert-id>" --actor "OWNER" --note "Reviewed"
```

## Exact next action
Recommend an **independent acceptance audit** of commit `fa6da8b` (+ the proof-gate docs commit).
Do **not** merge into `main`, do **not** create a Phase 7.11 acceptance tag, and do **not** begin
Phase 7.12 until the audit completes.
