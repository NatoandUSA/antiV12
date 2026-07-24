# Session 7.13 — Unified Owner Console — Implementation Report

> Status: **IMPLEMENTED, NOT ACCEPTED.** Not merged. No acceptance tag created. Phase 7.14 not begun.
> Recommended next step: an independent acceptance audit.

## 1. Branch / baseline / commits

| Item | Value |
|---|---|
| Branch | `phase7-13-unified-owner-console` |
| Baseline (== origin/main) | `a5df2b1237553a2699740cef98b2ba056d440fd4` |
| Checkpoint tag | `phase7-13-unified-owner-console-checkpoint-a5df2b1` |
| Implementation commit (feat) | `5eb13ed85f09fbfa4555f9bbe7681c2658a8af9e` |
| Proof commit (docs) | `this docs(phase7.13) commit (created immediately after the feat commit; exact hash in the session hand-off)` |
| Local feature HEAD | `5eb13ed85f09fbfa4555f9bbe7681c2658a8af9e (feat); this docs commit becomes local HEAD after it lands` |
| Remote feature HEAD | `origin/phase7-13-unified-owner-console == local HEAD after push` |
| main / origin/main | `a5df2b1237553a2699740cef98b2ba056d440fd4` (unchanged, NOT merged) |

## 2. Files created / modified

**Created**
- `production/phase7_unified_owner_console.py` — the one Phase 7.13 authority (console server + read
  models + action allowlist + session/CSRF + confirmation tokens + audit chain + exports + CLI).
- `production/phase7_unified_owner_console_static/{index.html,app.js,styles.css,icons.svg}` — self-contained
  front-end (no CDN, no external script/style/font/image/analytics).
- `tests/test_phase7_13_unified_owner_console.py` — 266 focused tests.
- `docs/PHASE7_13-UNIFIED-OWNER-CONSOLE-POLICY.md` — governing policy.
- `SESSION7_13-UNIFIED-OWNER-CONSOLE-IMPLEMENTATION-REPORT.md` (this file).
- `SESSION7_13-UNIFIED-OWNER-CONSOLE-PROOF-GATE.json` — proof gate.

**Modified (repo config only; no accepted authority touched)**
- `.gitattributes` — added `eol=lf` pins for the new console source + static assets + policy doc so their
  SHA-256 hashes reproduce identically in every checkout under `core.autocrlf=true`. This is the same
  precedent set in Session 7.9 for the connectivity policy doc.

**No accepted Phase 7.3–7.12 production or core source byte was changed.** `git diff --stat HEAD -- production/
core/` is empty for all pre-existing files (verified before and after; see §16).

## 3. Dependencies

None added. Stdlib-only (`http.server`, `hashlib`, `hmac`, `ipaddress`, `secrets`, `json`, `socket`,
`argparse`, `datetime`, `time`). The console reuses the accepted authorities and `core.network_policy /
diagnostics / money`, which already carry their own (lazy) dependencies. No frontend framework, no build tool.

## 4. Authority reuse

Read model and every state-changing action delegate to an accepted authority (no duplicated logic):

- `production.phase7_owner_operations_dashboard` (7.8) → the accepted 7.3–7.7 business read model
  (`build_operations_model`), which itself reuses `phase7_ads_analysis` (7.3), `phase7_owner_dashboard` (7.4),
  `phase7_owner_decision_package` (7.5), `phase7_manual_action_tracker` (7.6), `phase7_outcome_followup` (7.7).
- `production.phase7_connected_backup_recovery` (7.9) → `create_snapshot`, `verify_snapshot`, `update_check`,
  `update_stage`, `restore_plan`, `list_snapshots`, `load_snapshot`.
- `production.phase7_connected_public_research` (7.10) → `list_runs`, `_load_manifest`, `_load_capture`.
- `production.phase7_connected_research_watchlists` (7.11) → `list_watchlists`, `load_watchlist`, `_load_state`,
  `compute_due`, `list_alerts`, `run_watchlist`, `_alert_action`.
- `production.phase7_owner_notification_delivery` (7.12) → `list_routes`, `list_batches`, `list_deliveries`,
  `load_route`, `load_batch`, `build_batch`, `write_outbox`, `send_batch`.
- `core.network_policy` (`classify_destination`, `_AMAZON_ACCOUNT_CLASSES`), `core.diagnostics`
  (`redact_secrets`, `recent_events`), `core.money` (parity import).

The console builds **no** second business-analysis, research, alert, notification, backup authority, or network
policy. It never shells out to a Phase CLI, never duplicates validation, never reinterprets an accepted status.

## 5. Console architecture

Thin, stdlib `ThreadingHTTPServer` bound to a validated loopback address. A read-only model is assembled by
`build_console_model()` from five section builders (each reusing one accepted authority) plus a system/health
section and a display-only freshness signal. State changes flow through a **fixed 15-action allowlist** with a
two-stage prepare → execute confirmation, an ephemeral in-memory session + CSRF, and an append-only,
hash-chained orchestration audit trail. A bounded, short-lived in-memory read-model cache serves reads.

## 6. Pages implemented (8 sections)

Overview; Analysis & Decisions (7.3–7.7 sub-views: analysis / owner review / decision packages / manual
actions / outcome follow-up / attention); Research (7.10); Watchlists & Alerts (7.11); Notifications (7.12);
Backup & Recovery (7.9); System Health; Activity (the 7.13 audit log).

## 7. Actions implemented (15) / intentionally excluded

**Implemented:** refresh-overview, export-overview, verify-system-state, run-watchlist, acknowledge-alert,
dismiss-alert, reopen-alert, preview-notification, build-notification-batch, send-notification-batch,
create-backup-snapshot, verify-backup, check-for-update, stage-update, create-recovery-plan.

**Excluded (absent from the allowlist):** arbitrary Python function invocation, arbitrary module import,
arbitrary shell command, arbitrary file path, arbitrary URL, arbitrary HTTP method, **destructive restore
execution**, scheduler registration, service installation, any Seller Central operation, Amazon Ads action,
buyer messaging, review requests.

## 8. Session / CSRF / confirmation-token model

- Random 32-byte server secret generated at startup, never persisted. `HttpOnly`, `SameSite=Strict` session
  cookie; 8 h bounded lifetime; invalid after restart (in-memory only).
- Per-session CSRF token required on every POST (`X-CSRF-Token`), session-bound, excluded from logs/exports.
- Prepare issues a single-use, 300 s, in-memory action token bound to the exact action, canonical params, and
  target IDs. Confirmation phrase is checked before the token is consumed (a wrong phrase never burns it). For
  a Phase 7.12 live send, the console `SEND:<batch-id>` phrase is additional and the accepted 7.12 gates
  (`PHASE7_12_ALLOW_LIVE_DELIVERY=1` + `SEND:<batch-id>` authority token) are preserved.

## 9. HTTP security

Loopback bind + strict Host validation; GET reads only, POST (JSON only) mutations; no state via query string;
bounded 256 KiB body; duplicate-JSON-key and NaN/Infinity rejected; static allowlist, no traversal, no
arbitrary download, no open redirect; CSP (`default-src 'self'`, no `unsafe-eval`/`unsafe-inline`), nosniff,
`Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Permissions-Policy`, `Cache-Control`. No CORS.

## 10. API / read models / cache / audit

Versioned `/api/v1/*`; every response carries `schema_version, request_id, readiness, generated_at, data,
warnings, errors, source_authorities, seller_central_counters` (constant zero). Read models preserve accepted
meaning (no BLOCKED→READY rename, UNKNOWN≠FAILED, READY_EMPTY not an error, no invented metric, no causation,
no mtime record selection). Bounded in-memory cache keyed with a state hash, short max age, explicit refresh,
stale indicator. Audit chain: schema/event-id/request-id/actor/fingerprint/action/targets/param-hash/
authority/prep+exec result/upstream id/readiness/policy/prev-hash/event-hash/aggregate-hash; corrupt chain
blocks mutations and allows reads.

## 11. UI / accessibility

Left sidebar, fixed top status bar, breadcrumbs, readiness badges, blocked-state explanations, freshness
indicators, search/sort/bounded pagination, copy-ID buttons, confirmation modal, loading/empty/error states,
skip link, visible `:focus-visible`, `aria-live` result area, status conveyed by text + shape (never colour
alone). Self-contained assets; no secret in the DOM or browser storage.

## 12. Deterministic exports

`owner_console_snapshot.json`, `owner_console_status.tsv`, `owner_console_report.md`. TSV formula-injection
safe (`=`,`+`,`@`,`|` neutralised, CR/LF stripped) while `-2.50` and Vietnamese Unicode are preserved. No
secrets, cookies, tokens, endpoint secrets, Authorization, absolute paths, raw HTML, or customer data.

## 13. Readiness states

`SESSION7_13_CONSOLE_READY / _READY_PARTIAL / _READY_EMPTY / _REQUIRED / _BLOCKED`,
`_MODULE_UNAVAILABLE`, `_DATA_STALE`, `_ACTION_CONFIRMATION_REQUIRED / _ACTION_READY / _ACTION_COMPLETED /
_ACTION_FAILED / _ACTION_BLOCKED`, `_SESSION_REQUIRED / _SESSION_EXPIRED`, `_CSRF_BLOCKED`,
`_AUDIT_STATE_BLOCKED`, `_INTEGRITY_BLOCKED`, `_SELLER_CENTRAL_POLICY_BLOCKED`.

## 14. Test results

- **Baseline focused suites (in-place, a5df2b1):** 7.2 = 377 (1 skip), 7.3 = 117, 7.4 = 94, 7.5 = 109,
  7.6 = 100, 7.7 = 93, 7.8 = 152, 7.9 = 139 (1 skip), 7.10 = 191 (1 skip), 7.11 = 189, 7.12 = 234;
  connectivity_policy = 16, connectivity_surface = 19, network_policy = 5. compileall exit 0.
- **Phase 7.13 focused:** 266 passed, 0 failed (in-place). 266 passed, 0 failed, exit 0 (fresh feature worktree).
- **Prior focused suites (in-place, with 7.13 present):** unchanged from baseline — the full in-place suite (below) exercises every prior focused suite with 0 failures; 7.13 adds a new file and changes no prior test.
- **Full in-place suite:** 4162 passed, 4 skipped, 0 failures, exit 0 (== baseline in-place 3896 + 266 new).
- **Differential fresh-worktree audit:** baseline fresh worktree (a5df2b1) = 3894 ran, 1 failure + 14 errors + 329 skipped, exit 1; feature fresh worktree (5eb13ed) = 4160 ran, the IDENTICAL 1 failure + 14 errors + 329 skipped set, exit 1, plus 266 passing 7.13 tests (4160-3894=266). Failsets are byte-identical (15==15); 0 new failures, 0 lost passes, 0 broadened skips, and 0 Phase 7.13 tests in the failset. The 15 pre-existing fail/errors are all runs/T2-data-dependent tests (test_backend_semantic_quality, test_backend_phrase_integrity, test_session5d_certification) that fail identically in both worktrees because runs/ is git-ignored and absent. Result: BASELINE_EQUIVALENT_NONZERO — the feature is no worse than baseline. Fresh feature 7.13 focused = 266 passed, exit 0..
- **compileall:** exit 0 in-place and in both fresh worktrees.

## 15. Synthetic validation

Synthetic accepted state was built for every integrated module by chaining the real upstream authorities
through injected fake transports/resolvers (no real Internet): analysis READY / READY_EMPTY, owner decision,
decision package, manual action, follow-up, backup snapshot, research run, watchlist with OPEN/ACK/DISMISSED
alerts, notification route + batch + SENT/UNKNOWN-gated delivery, integrity blocks, and a missing optional
module. Validated: overview, every page, readiness translation, freshness, action preparation, exact
confirmation, CSRF, action audit, audit corruption, cache invalidation, deterministic exports, loopback-only
server, and external-request denial.

## 16. Source immutability / runs tracking / prohibited-integration scan

- Accepted Phase 7.3–7.12 production tree SHA-256 (14 files) **before** implementation:
  `254882a1e8b707cc036edeed708b55b834300ce43eb9f143dee746fa85e2ca2c`. Recomputed **after**: identical
  (see proof gate `source_hashes`). No accepted byte changed.
- `runs/` remains git-ignored; nothing under `runs/` is committed; no Phase 7.13 file appears under any prior
  phase runtime directory.
- Prohibited-integration scan (AST of the module): no `subprocess`, `selenium`, `playwright`, `webdriver`,
  `pyppeteer`, `webbrowser`, `smtplib`, `boto3`, `anthropic`, `openai`, `requests`; no `eval`/`exec`/`compile`,
  no `os.system`/`os.popen`, no `shell=True`; no `schtasks`/`crontab`/`systemctl`/service registration; no
  `sellercentral`/`sellingpartnerapi`/`advertising-api.amazon`/`/ap/signin` literal.
  `scripts/connectivity_scan.py` → `no_active_amazon_account_path = true` (0 active Amazon-account paths).

## 17. Seller Central counters

All ten constant zero: `seller_central_connections, seller_api_calls, advertising_api_calls,
seller_account_mutations, seller_report_downloads, seller_bulk_uploads, seller_browser_automation_actions,
seller_credential_store_count, buyer_messages_sent, review_requests_sent`.

## 18. Known limitations

- The console never opens a browser; the owner opens `http://127.0.0.1:8780` manually (Phase 7.14 first-run
  wizard is out of scope and not begun).
- A live Phase 7.12 send and a git `check-for-update`/`stage-update` require the owner's real environment
  (webhook env + git remote); tests exercise these through injected transports / an offline non-repo, so they
  make no real network call.
- Browser click-through QA remains an owner step; automated tests cover the served HTML/JS statically and the
  API/security contract dynamically.
- Real-T2 behaviour depends on the untracked `runs/T2` tree; in a fresh worktree (no `runs/`) the console
  reports `SESSION7_13_CONSOLE_REQUIRED` honestly.

## 19. Exact launch command

```
python -m production.phase7_unified_owner_console `
  --workspace-root "runs/T2/phase7" `
  --host "127.0.0.1" `
  --port 8780 `
  serve
```

## 20. Exact next action

Run an **independent acceptance audit** of commit `5eb13ed85f09fbfa4555f9bbe7681c2658a8af9e` (+ proof docs `this docs(phase7.13) commit (created immediately after the feat commit; exact hash in the session hand-off)`). Do not
merge, do not create an acceptance tag, and do not begin Phase 7.14 until the audit accepts.
