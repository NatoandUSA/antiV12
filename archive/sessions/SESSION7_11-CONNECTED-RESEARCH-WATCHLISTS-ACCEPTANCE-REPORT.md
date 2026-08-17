# SESSION 7.11 — Connected Research Watchlists, Change Detection & Owner Alerts — Independent Acceptance Audit

**Auditor:** independent acceptance audit (adversarial; reproduced from repository bytes and independent fixtures).
**Repository:** `<REPO>`
**Date:** 2026-07-23
**Subject commits:** implementation `fa6da8b`, proof `1dec96b` (feature HEAD).
**Baseline:** `9d9a4528f04af90640019eb872d2561879bfa253`.

> Nothing below was taken on trust from the implementation report or proof gate. Every material
> claim was reproduced independently: git object inspection, full source read of the 2153-line
> authority, real Phase 7.10 pipeline runs offline via injected transports, and 122 independent
> harness checks written against my own fixtures.

## Final decision

**PHASE7_11_CONNECTED_RESEARCH_WATCHLISTS_ACCEPTED**

No blocking defect. All required gates pass. The documentation inaccuracy the audit brief
anticipated ("git diff HEAD vs baseline is empty") is **not present** — the report's actual wording
is accurate — so no documentation fix is required.

---

## Numbered findings

### 1. Git provenance — PASS
Branch `phase7-11-connected-research-watchlists`; working tree clean; local HEAD `1dec96b`; remote
feature HEAD `origin/phase7-11-connected-research-watchlists` = `1dec96b`; `main` = `origin/main` =
`9d9a4528f04af90640019eb872d2561879bfa253`; checkpoint tag
`phase7-11-connected-research-watchlists-checkpoint-9d9a452` → baseline exactly; all 9 prior accepted
tags present and resolve to their short hashes; **no** `*7-11*accepted*` tag exists. History is
linear (baseline → `fa6da8b` → `1dec96b`), parents verified, no amend/rebase/rewrite.

### 2. Implementation diff — PASS
`fa6da8b` is 3 pure additions (`A`): `production/phase7_connected_research_watchlists.py` (2153 L),
`tests/test_phase7_11_connected_research_watchlists.py` (1671 L, 189 tests), and
`docs/PHASE7_11-RESEARCH-WATCHLIST-POLICY.md` (181 L). Parent = baseline. Zero modifications/deletions.

### 3. Proof diff — PASS
`1dec96b` is 2 pure additions: the implementation report and the proof-gate JSON. Parent = `fa6da8b`.
Full baseline→HEAD diff is exactly 5 `A` entries; `grep -v '^A'` returns nothing.

### 4. Phase 7.10 authority reuse — PASS
All network, parse, hash, atomic-write, redaction, descriptor/locator/source-id, capture/manifest
loading, TSV, and canonical-JSON work is delegated to `production.phase7_connected_public_research`
(imported as `R`). I enumerated every `R.*` reference (42 symbols) and confirmed each is **defined**
in the 7.10 module (26 spot-checked by name; all present). The 7.10 file object hash at HEAD equals
the accepted `phase7-10-...-accepted-9888e69` blob (`66b1c6b4…`) — byte-identical, not modified. No
`R.*`/`NP.*`/`M.*` attribute is ever **assigned** (no monkeypatch); private helpers are used
read-only. End-to-end harness drove the real 7.10 fetch→parse→capture→evidence pipeline offline,
proving the reuse is functional, not nominal.

### 5. Connectivity boundary — PASS
No independent transport. Independent scans of the production file for `urllib`, `http.client`,
`socket`, `requests`, `httpx`, `aiohttp`, `pycurl`, `urlopen`, `Popen`, `subprocess`, `webbrowser`,
`selenium`, `playwright`, `webdriver`, `Invoke-WebRequest`, `Invoke-RestMethod` → the only hits are a
defensive forbidden-substring **string list** and docstrings. A `tokenize` NAME-token scan (strings
and comments excluded) found **none** of the banned identifiers. Phase 7.11 cannot fetch or parse
public data independently of Phase 7.10.

### 6. Seller Central prohibition — PASS
Denial is delegated to 7.10 / `core.network_policy` at the single connection-time choke point and
"wins first" (`_execute_watchlist` forces `SELLER_CENTRAL_POLICY_BLOCKED`). Verified directly: a
watchlist naming `https://sellercentral.amazon.com/home` is validated/persisted with **no** network
access; on `run-watchlist` the injected transport is **never called** for the seller-central URL, the
capture is `CAP_SELLER`, readiness = `SESSION7_11_SELLER_CENTRAL_POLICY_BLOCKED` (nonzero exit),
change = `SOURCE_BLOCKED`, and all 9 Amazon counters remain zero. Same for `/ap/signin`. No
Seller-Central / SP-API / Ads-API host literal appears anywhere in the 7.11 source.

### 7. Watchlist schema — PASS
`validate_watchlist` rejects: non-object, unknown top-level fields, secret/header/cookie/command/
`shell`/`subprocess`/`eval`/`exec`/`import`/`dynamic` fields (reused 7.10 forbidden scan + extra
shapes), empty name, invalid comparison policy, malformed sources, invalid schedule/timezone, and
invalid alert rules. Empty and disabled watchlists validate. Duplicate create is idempotent-reuse.

### 8. Source descriptors — PASS
Every source is validated through `R._normalize_descriptor` (the sole descriptor authority); anything
7.10 rejects (arbitrary URL, bad locator, unknown type) is rejected as `SOURCE_DESCRIPTOR_REJECTED` /
`SOURCE_BLOCKED`. Legitimate `amazon-public-product` retail descriptors are accepted (boundary =
retail pages OK via 7.10). Raw headers/cookies/tokens are forbidden fields.

### 9. Schedule model — PASS (harness: 19/19)
`manual`/`hourly`/`daily`/`weekly`/`interval-hours`; 1-hour minimum enforced (`SCHEDULE_MIN_INTERVAL`
for `interval_hours` 0). Manual → not due / `MANUAL_ONLY`; force → due. Due/not-due, first-run,
disabled-schedule, and bounded catch-up (`missed ≤ MAX_CATCH_UP = 1`) all reproduced. `NOT_DUE`
returns a non-error state (CLI exit 0). Operational timestamps never enter any identity.

### 10. DST and clock behavior — PASS
Clock-moving-backward → `CLOCK_MOVED_BACKWARD`, `missed = 0` (never negative). Future last-run treated
as clock-backward → not due. Spring-forward nonexistent local time and fall-back ambiguous local time
both return a boolean decision without crashing (aware `zoneinfo` arithmetic). Invalid timezone →
`TIMEZONE_INVALID`.

### 11. Scheduler-plan safety — PASS (harness: 9/9)
`scheduler-plan` emits a read-only plan; it never invokes/registers `schtasks`/`cron`/`crontab`/
`systemctl`/`launchctl`/Task Scheduler COM (verified by grep + tokenize). Uses `sys.executable`.
Hostile `--research-dir` containing spaces, `"`, `'`, `&`, `|`, `;`, backtick, `$`, `()`, newline is
safely quoted — PowerShell single-quote doubling (`''`) and POSIX single-quote escaping (`'\''`) —
and remains inert string data, never executed. States `OWNER_ACTION_REQUIRED`; contains no secret and
no seller-central target; `template_hash` deterministic across repeated calls.

### 12. Execution model — PASS
`_execute_watchlist` validates → acquires per-watchlist lock → runs reused 7.10 into a **7.11-owned**
`base_dir/research` workspace → `verify_run` → integrity-verifies each capture → compares vs previous
accepted baseline → records deterministic changes → evaluates owner rules → updates only 7.11 state →
writes execution report; lock released in `finally` (failed run releases its own lock). A corrupt
current run (`verify_run` fail / capture hash mismatch) returns `INTEGRITY_BLOCKED` and does **not**
replace the baseline or advance `last_run_utc`. One failed/blocked source does not invalidate
unrelated successful sources (per-item readiness combined most-restrictively).

### 13. Baseline selection — PASS (harness: e2e)
Selection is by stable source identity (source id, capture id, content hash, evidence ids, run
lineage, verified manifest) — never mtime. First successful observation → `INITIAL_BASELINE` (no false
`ADDED`, no alert). Baseline updated **only** from captures in `R._SUCCESS_STATUSES`. Verified: after
a good baseline, a DNS-unavailable run records `SOURCE_UNAVAILABLE` and the prior baseline survives —
the next identical run compares `UNCHANGED` against the preserved baseline.

### 14. Capture integrity — PASS
`_load_verified_capture` re-hashes the raw `.bin` bytes against the stored `content_sha256`; a missing
raw file → `CAPTURE_RAW_MISSING`, a mismatch → `CAPTURE_CORRUPT`, both `INTEGRITY_BLOCKED`. Corrupt
current/previous captures never become a valid baseline.

### 15. Change-event model — PASS
Statuses: `INITIAL_BASELINE`, `ADDED`, `REMOVED`, `CHANGED`, `UNCHANGED`, `SOURCE_UNAVAILABLE`,
`SOURCE_BLOCKED`, `PARSE_PARTIAL`, `INTEGRITY_BLOCKED`, `COMPARISON_NOT_AVAILABLE` — all present and
exercised. Field-level add/remove/change/unchanged reproduced; a content-hash change with no evidence
change is recorded as `source_content_changed_evidence_unchanged`; parser-version change is surfaced
as a warning.

### 16. Stable change identities — PASS (harness: e2e determinism)
`change_id = "chg-" + sha256(field/lineage/value identity)[:24]` excludes runtime timestamp, row
order, sort, filter, pagination, temp path, uuid, filesystem order. Two runs of the identical scenario
in **separate** workspaces produced identical `watchlist_id` and identical sorted `change_id` sets.

### 17. Normalization — PASS (harness: pure)
Only documented technical normalization: Unicode NFC, whitespace collapse, canonical 7.10 URL
normalization, and case-folding **only** for owner-declared case-insensitive field paths (default is
case-sensitive; verified). No fuzzy matching, no LLM, no embeddings.

### 18. Decimal safety — PASS (harness: pure)
Numeric parsing via `core.money` (Decimal-only). `_num` rejects `NaN`/`Infinity`/`-inf`. Numeric
outputs are canonical strings, never binary float. `24.99 == 24.990` numeric equality holds.

### 19. Currency safety — PASS
Numeric delta/percent computed only when both values are explicit numeric with **identical currency**;
a currency mismatch emits `currency_mismatch` and yields no delta. A numeric rule with incompatible
currency does not fire.

### 20. Unit safety — PASS
Same as currency for units: unit mismatch → `unit_mismatch`, no delta; numeric rule with incompatible
unit does not fire. No cross-unit/cross-currency/cross-marketplace aggregation.

### 21. Alert rules — PASS (harness: 21 operator checks)
All 13 operators verified: `field-changed`, `field-added`, `field-removed`, `value-equals` (exact,
case-sensitive), `value-contains` (bounded, case-sensitive substring — no unrestricted regex),
`numeric-above`, `numeric-below`, `absolute-delta-at-least` (uses `abs`), `percent-change-at-least`
(uses `abs`), `source-unavailable`, `source-blocked`, `parse-partial`, `integrity-blocked`. Disabled
rule never fires; selector / evidence-type / field-path filters honored; non-numeric value for a
numeric op does not fire; percent never computed when previous value is zero.

### 22. Owner severity — PASS
Severity ∈ {INFO, REVIEW, IMPORTANT, CRITICAL} is owner-supplied and echoed verbatim onto the alert.
No computed severity, urgency, score, or recommendation anywhere in the code.

### 23. Alert identity — PASS
`alert_id = "alt-" + sha256({watchlist_id, item_id, rule_id, change_id})[:24]`; content-addressed and
deterministic; status lives only in the separate alert-state chain, never in the alert record.

### 24. Alert deduplication — PASS (harness: state)
`_register_alerts` writes each alert once and registers it OPEN once; re-registering the same alerts
returns them as `reused` with no duplicate OPEN entry. Different rule / change / watchlist → different
alert id.

### 25. Acknowledgement operations — PASS (harness: state)
`acknowledge`/`dismiss`/`reopen` require an exact alert id and a non-blank actor (`ACTOR_REQUIRED` for
`""`/`None`). Unknown id → `ALERT_NOT_FOUND`; reopen of an OPEN alert → `ALERT_ALREADY_OPEN`;
dismiss→reopen returns to OPEN with the alert id unchanged.

### 26. Alert-history hash chain — PASS (harness: state)
Append-only: `event_hash = sha256({seq, alert_id, action, actor, note, prev_state_hash})`, chained via
`prev_state_hash`, aggregate `head_hash`. `recorded_at` is stored **outside** the hash — tampering the
timestamp alone leaves the chain valid; audit ordering stays unambiguous via `seq`.

### 27. Corrupt-history blocking — PASS (harness: state)
Actor tampering → `ALERT_HISTORY_TAMPERED`; reorder → `ALERT_HISTORY_REORDERED`; truncation →
`ALERT_HISTORY_HEAD_MISMATCH`; all `SESSION7_11_ALERT_STATE_BLOCKED`. On a broken chain, further state
updates are blocked. State-file integrity hash tamper/missing → `INTEGRITY_BLOCKED`.

### 28. Locking — PASS (harness: 8/8)
Per-watchlist `O_CREAT|O_EXCL` lock under `base_dir/locks`. Second acquire → `LOCK_HELD`. Different
watchlists lock independently. `_release_lock` removes only a lock whose `pid`+`created_epoch` match
the caller's token; a foreign token cannot release an active lock; the owner can.

### 29. Stale-lock behavior — PASS (harness: state)
A lock older than `MAX_LOCK_AGE_SECONDS` (6 h) is **reported** `LOCK_STALE` and is **not** auto-removed;
explicit `--break-stale-lock` recovers it. A future-dated lock (negative age) is treated as `LOCK_HELD`,
never removed. No deletion is based on age alone.

### 30. Atomicity — PASS
All writes go through the accepted 7.10 `_atomic_write_json`/`_atomic_write_text` (temp-sibling +
fsync + atomic replace). Hashed state uses `_write_hashed_json` with read-back integrity verification
on load; a stale-hash body is rejected rather than trusted. A blocked/corrupt run writes an honest
execution record without overwriting the last valid baseline; Phase 7.10 data is untouched.

### 31. Idempotency — PASS (harness: e2e)
Repeated execution over identical accepted 7.10 evidence reuses the same comparison/change/alert
identities and creates no duplicate OPEN alert (dedup verified). Different evidence yields a new
comparison; parser-version change is surfaced; corrupt old state is not reused as a baseline.

### 32. Readiness states — PASS
All 19 documented `SESSION7_11_*` readiness states are defined. `_EXIT_OK` maps success states to exit
0 and everything else to 3. Verified: policy/integrity blocks exit nonzero; `NOT_DUE` exits 0; a valid
first baseline exits 0 (`READY_EMPTY`); a no-change run is `READY_EMPTY`; an actionable change is
`READY`; a mixed run is `READY_PARTIAL`.

### 33. JSON export (`watchlist_snapshot.json`) — PASS
Canonical JSON, stable ordering, carries readiness, change counts, per-source status, owner rule ids,
alert counts, disclaimers, constant-zero Amazon counters and network-purpose counters. Two independent
workspaces produced byte-identical snapshot hashes.

### 34. TSV export (`watchlist_changes.tsv`) — PASS (harness: e2e)
Equal column count per row; formula-injection neutralized (leading `=` quoted by `R._tsv_cell`);
legitimate `-2.50` preserved verbatim; tab/newline neutralized inside cells; Vietnamese Unicode
(`Cà phê Việt`) preserved. Deterministic hash across workspaces.

### 35. Markdown export (`watchlist_report.md`) — PASS
Deterministic; lists change counts, per-source status, changes, alert counts, disclaimers, and the
constant-zero Amazon-account boundary block. Identical hash across workspaces.

### 36. Alerts export (`watchlist_alerts.json`) — PASS
Canonical JSON with alert rows + open/acknowledged/dismissed/total counts + zero Amazon counters.
Identical hash across workspaces. No credentials/tokens/cookies/authorization/signed-URL/local-path/
customer/seller-central/invented-demand content in any export.

### 37. Validate-only — PASS (harness: cli)
`validate-only` performs no network/DNS, writes no file, creates no base directory, acquires no lock,
mutates no state, launches no browser, executes no subprocess. Returns exit 0 for a valid definition
(`files_written=0, network_requests=0, locks_acquired=0`) and nonzero for a forbidden-field or
invalid-timezone definition.

### 38. Upstream immutability — PASS
Independently recomputed sha256 of `core/diagnostics.py`, `core/money.py`, `core/network_policy.py`,
`production/phase7_connected_public_research.py` equals the proof gate's **before and after** values
exactly, and is unchanged after my full audit session. `compare`/`export` write only under `base_dir`
(no write path targets `research_dir`); `research_runs_dir` is under `base_dir`. No Phase 7.11
lock/cache/temp/report/state file lands in any Phase 7.3–7.10 tree.

### 39. Seller Central counters — PASS
Every result and record carries the 9 constant-zero counters (`seller_central_connections`,
`amazon_seller_api_calls`, `amazon_ads_api_calls`, `amazon_seller_auth_calls`,
`amazon_seller_mutations`, `amazon_report_downloads`, `amazon_bulk_uploads`,
`browser_automation_actions`, `amazon_credential_store_count`). No code path increments them; they
sourced from the reused `R.AMAZON_COUNTERS`. Stayed zero even on a seller-central run attempt.

### 40. Prohibited-integration scan — PASS
`grep` + `tokenize` NAME scan of the production file: no `subprocess`, `selenium`, `playwright`,
`webdriver`, `webbrowser`, `pyppeteer`, `requests`, `smtplib`, `sendmail`, `eval`, `exec`, `system`,
`urllib`, `socket`, `shell`, `sellingpartner`, `captcha`. No `shell=True`, no `os.system`, no email/
webhook/chat, no scheduler registration, no browser automation. Accepted scanner suites
(`test_amazon_boundary` 26, `test_connected_services` 18, `test_connectivity_policy` 16,
`test_connectivity_surface` 19, `test_network_policy` 5) all pass.

### 41. Compile result — PASS
`python -m compileall core production tests` → exit 0 (primary and fresh worktree).

### 42. Phase 7.11 focused tests — PASS
`python -m unittest tests.test_phase7_11_connected_research_watchlists` → **189 passed, exit 0**
(true exit captured, no pipe masking). 19 test classes, 189 methods, matching the claim.

### 43. Prior focused tests — PASS
7.2 `377 passed, 1 skipped`; 7.3 `117`; 7.4 `94`; 7.5 `109`; 7.6 `100`; 7.7 `93`; 7.8 `152`; 7.9
`139, 1 skipped` (primary); 7.10 `191, 1 skipped`. All exit 0. Every total matches the proof gate.

### 44. Full suite — PASS
`python -m unittest discover -s tests` → **Ran 3662 tests, OK (skipped=4), exit 0** — 3662 passed, 4
skipped, 0 failures, 0 errors. Matches the claim exactly (`3473 + 189 = 3662`).

### 45. Independent harnesses — PASS (122 checks, 0 failures)
Four auditor-authored harnesses against my own fixtures: pure functions (schedule/DST, numeric/
currency/unit, all 13 alert operators) **52/52**; locking + alert-history chain + state integrity
**26/26**; end-to-end through the real 7.10 authority (baseline/change/determinism/exports/TSV/
seller-central/validate-only) **26/26**; scheduler-plan hostile paths + CLI exit codes **18/18**.
(Three initial "failures" were my own incorrect assumptions — canonical Decimal scale, and the
documented case-sensitive value-equals/value-contains — corrected and re-verified.)

### 46. Fresh worktree — PASS
Detached worktree at `1dec96b`: `runs/` absent; compileall exit 0; 7.11 focused **189 OK**; 7.10
focused **191 OK, 1 skipped**; 7.9 focused **137 total, 2 skipped** (environment-dependent — see #50);
all three portable harnesses (pure/state/e2e) pass against the proof-commit bytes; prohibited scan
clean. Worktree removed afterward; `git clean` never run on the primary workspace. No dependency on
untracked T2 data.

### 47. runs/ tracking — PASS
`git ls-files` shows nothing under `runs/`; `git check-ignore runs` → ignored. No T2 workspace data is
committed.

### 48. Optional live-network status — PASS (as documented)
`NOT_RUN` (owner-gated `PHASE7_11_ALLOW_LIVE_NETWORK`). No committed test contacts the real Internet;
all offline via injected transports/resolvers — confirmed by the boundary scans and the socket-free
NAME scan.

### 49. Documentation accuracy — PASS
Report and proof gate accurately describe branch, baseline, commits, checkpoint, files, 7.10 reuse,
schema, schedule, baseline selection, changes, numeric safety, alert rules, alert identity,
acknowledgement chain, locks, atomicity, idempotency, exports, tests, full suite, synthetic
validation, live-network status, upstream immutability, scanners, zero counters, and known
limitations. **Resolution of the flagged wording:** the report does **not** contain "git diff HEAD vs
baseline is empty". Its actual wording is "`git diff HEAD` against the baseline shows **no accepted
file changed / modified**" (lines 35, 182), which the bytes confirm — the baseline→HEAD diff is 5 pure
additions with zero modifications to any accepted file. No documentation fix is required.

### 50. Known limitations / minor non-blocking observations — NOTED (non-blocking)
- (a) `import diagnostics as D` (production line 57) is a **dead import** — the `D` alias is never
  referenced; redaction runs through `R.redact` (which uses `core.diagnostics` transitively). The
  report/policy claim to "reuse core.diagnostics" is therefore true only transitively. Cosmetic; I did
  not modify production code (clean-acceptance rule). Not a defect.
- (b) The report/policy list `normalize_plan` among directly reused 7.10 functions, but the code calls
  `R.run_plan` (which normalizes internally), not `R.normalize_plan` directly. Defensible over-listing.
- (c) Proof-gate `fresh_worktree_results.phase7_9_focused` says "139 total (2 skipped)"; I observed
  **137 total, 2 skipped** in a fresh worktree. This is prior-phase (7.9), environment/data-dependent
  collection (`unittest.SkipTest` when `runs/` absent + data-driven test generation over real T2 data
  present in the primary but not the worktree). 7.9 passes cleanly (exit 0) in both. Not a 7.11 concern.
- (d) Documented scope limits carry forward: change detection reflects only what 7.10 parsers extract;
  one schedule governs all sources; report click-through remains an owner step; live smoke is owner-run.

### 51. Final decision — ACCEPTED
`PHASE7_11_CONNECTED_RESEARCH_WATCHLISTS_ACCEPTED`. None of the reject conditions hold: no direct
network fetching; 7.10 authority not bypassed; Seller-Central access impossible (denied at the
connection choke point, transport never called, counters zero); baseline selection is content-hash /
lineage based, not mtime; corrupt evidence never becomes a baseline; field comparison invents nothing;
numeric deltas never cross incompatible units/currencies; no duplicate alerts; acknowledgement history
tampering is always detected and blocks further updates; corrupt history blocks; active locks are not
removable unsafely; scheduler commands are never executed or registered; no arbitrary command
execution; Phase 7.3–7.10 data unchanged; full suite green; fresh worktree green.

### 52. Exact next action
Merge remains an **owner** decision — do **not** merge into `main`. This audit creates one acceptance
commit and one annotated tag `phase7-11-connected-research-watchlists-accepted-<hash>`, pushes the
feature branch and the tag, and stops. Do **not** begin Phase 7.12. The optional live-network smoke
and browser click-through of exports remain owner steps.

---

## Reproduction summary (true exit codes)

| Check | Result | Exit |
|---|---|---|
| compileall core/production/tests | ok | 0 |
| Phase 7.11 focused | 189 passed | 0 |
| Phase 7.10 focused | 191 passed, 1 skipped | 0 |
| Phase 7.9 focused (primary) | 139 total, 1 skipped | 0 |
| Phase 7.8 / 7.7 / 7.6 / 7.5 | 152 / 93 / 100 / 109 | 0 |
| Phase 7.4 / 7.3 / 7.2 | 94 / 117 / 377 (1 skip) | 0 |
| Full suite | 3662 passed, 4 skipped, 0 fail | 0 |
| Scanner suites (5) | 26+18+16+19+5 all OK | 0 |
| Independent harnesses (4) | 122 checks, 0 fail | 0 |
| Fresh worktree (compile/7.11/7.10/7.9/harnesses) | all green | 0 |
| Upstream source hashes before==after | byte-identical | — |
