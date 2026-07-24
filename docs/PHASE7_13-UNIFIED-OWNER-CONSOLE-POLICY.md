# Phase 7.13 — Unified Owner Console Policy

**Module:** `production/phase7_unified_owner_console.py`
**Static assets:** `production/phase7_unified_owner_console_static/` (`index.html`, `app.js`, `styles.css`, `icons.svg`)
**Workspace:** `runs/T2/phase7/7.13/` (git-ignored; never committed)
**Status:** implemented, not accepted. This document is the governing policy for the console.

The Unified Owner Console is **one** local, loopback-only web interface that unifies the already-accepted
Phase 7 authorities behind a single URL. It is an **aggregation + orchestration** layer only. It reuses the
accepted authorities for every read model and for every state-changing action; it never re-implements or
re-interprets their business logic, never invents a metric, and never weakens an integrity check.

---

## 1. Permanent boundary (never configurable, always first)

The console **never**:

- connects to Amazon Seller Central; uses a seller sign-in, seller login, seller OAuth, seller credentials,
  seller cookies, seller sessions, or seller tokens;
- uses a seller API (SP-API) or an advertising API (Ads API);
- downloads a Seller Central report; performs a Seller Central bulk upload;
- mutates a campaign, bid, budget, keyword, target, negative, listing, or inventory;
- drives a Seller Central browser; messages a buyer; requests a review; bypasses a CAPTCHA.

Every one of the ten `seller_central_counters` is a **constant zero**; no code path can increment them. The
console exposes no hidden or indirect route around these rules. Public-Internet work (Phase 7.9 git update
checks, Phase 7.10 public research, Phase 7.12 webhook delivery) is performed **only** inside the accepted
backend authorities, which enforce `core.network_policy` (the Amazon-account boundary is denied first, before
any allowlist). The browser front-end contacts nothing but this local server.

Amazon host literals never appear in the production source; where the network policy must be exercised the
host is assembled from fragments. `scripts/connectivity_scan.py` reports **0 active Amazon-account paths**.

---

## 2. Authority reuse

| Console section | Accepted authority reused | Nature |
|---|---|---|
| Analysis & Decisions (7.3–7.7) | `phase7_owner_operations_dashboard` (7.8) | read model, read-only |
| Public Research (7.10) | `phase7_connected_public_research` | read model, read-only |
| Watchlists & Alerts (7.11) | `phase7_connected_research_watchlists` | read + `run-watchlist`, `acknowledge/dismiss/reopen-alert` |
| Notifications (7.12) | `phase7_owner_notification_delivery` | read + `preview/build/send` |
| Backup & Recovery (7.9) | `phase7_connected_backup_recovery` | read + `snapshot/verify/update-check/stage/recovery-plan` |
| Network / diagnostics / money | `core.network_policy`, `core.diagnostics`, `core.money` | policy + redaction + Decimal |

The console builds the entire Phase 7.3–7.7 business read model by calling `OPS.build_operations_model(...)`
— it does **not** create a second business-analysis authority, research authority, alert authority,
notification authority, backup authority, or network policy. It never shells out to a Phase CLI when a Python
authority exists, never duplicates validation logic, never reinterprets an accepted business status, and never
bypasses an authority through direct filesystem mutation.

---

## 3. Server model

- Bind host: **`127.0.0.1`** only (IPv6 `::1` and `localhost` also accepted when they resolve to loopback).
  `0.0.0.0`, public interfaces, LAN addresses, and arbitrary hosts are refused.
- Default URL: `http://127.0.0.1:8780`.
- Start command:
  ```
  python -m production.phase7_unified_owner_console `
    --workspace-root "runs/T2/phase7" `
    --host "127.0.0.1" `
    --port 8780 `
    serve
  ```
- The browser is **never** opened automatically in Phase 7.13.
- Strict `Host` header validation, same-origin only, no CORS, no JSONP, no cross-origin control channel.

---

## 4. Action capability model

The front-end cannot call arbitrary backend functions. A **fixed server-side allowlist of exactly 15 actions**
governs every state change. Each action maps to exactly one accepted authority function through a fixed
`if/elif` dispatch — never a dynamic attribute lookup on caller input, never an arbitrary import, never a
subprocess, never an arbitrary URL or HTTP method.

Owner actions: `refresh-overview`, `export-overview`, `verify-system-state`, `run-watchlist`,
`acknowledge-alert`, `dismiss-alert`, `reopen-alert`, `preview-notification`, `build-notification-batch`,
`send-notification-batch`, `create-backup-snapshot`, `verify-backup`, `check-for-update`, `stage-update`,
`create-recovery-plan`.

**Excluded** (absent from the allowlist): arbitrary Python function invocation, arbitrary module import,
arbitrary shell command, arbitrary file path, arbitrary URL, arbitrary HTTP method, **destructive restore
execution**, scheduler registration, service installation, and any Seller Central operation.

### Explicit confirmation (two-stage)

State-changing and network actions require an explicit confirmation through two stages: **prepare-action**
then **execute-action**. The preparation response includes the action token, canonical action name, target
IDs, expected authority, expected effect, network use, local and upstream state changes, expiration, and the
confirmation phrase. The token is bound to the exact action, target IDs, and canonical parameters; it is
short-lived (300 s), single-use, stored only in memory, and excluded from logs and exports.

Confirmation phrases: `ACKNOWLEDGE:<alert-id>`, `DISMISS:<alert-id>`, `REOPEN:<alert-id>`, `RUN:<watchlist-id>`,
`BUILD:<route-id>`, `SEND:<batch-id>`, `BACKUP:<snapshot-id>`, `PLAN:<snapshot-id>`, `CHECK-UPDATE:<branch>`,
`STAGE-UPDATE:<release-id>`. A wrong phrase is rejected **without** consuming the token; a correct phrase
consumes it exactly once.

**Phase 7.12 live send:** the console confirmation is *additional*. It never replaces the accepted Phase 7.12
gates — the `PHASE7_12_ALLOW_LIVE_DELIVERY=1` environment gate and the `SEND:<batch-id>` token are both
preserved and passed through to `NOTIFY.send_batch`. Missing gates surface honestly as a non-completed action.

---

## 5. Session, CSRF, and HTTP security

- Ephemeral in-memory session; a random 32-byte server secret is generated at startup and never persisted
  under `runs/`. `HttpOnly`, `SameSite=Strict` session cookie; bounded lifetime (8 h); sessions are invalid
  after a server restart.
- A per-session CSRF token is required for every state-changing request (`X-CSRF-Token` header), bound to the
  active session and excluded from logs and exports. The session is never a substitute for owner-action
  confirmation.
- GET for reads only; POST (JSON only) for prepared actions and execution; no state change through query
  strings; bounded request size (256 KiB); duplicate JSON keys and NaN/Infinity rejected; safe static-file
  allowlist; no directory traversal, no arbitrary file download, no open redirect.
- Response headers: `Content-Security-Policy` (`default-src 'self'`; `script-src 'self'`; `object-src 'none'`;
  `base-uri 'none'`; `frame-ancestors 'none'`; no `unsafe-eval`, no `unsafe-inline`), `X-Content-Type-Options:
  nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Permissions-Policy`, `Cache-Control`.

---

## 6. Read-model rules

Read models preserve accepted-authority meaning: a `BLOCKED` state is never renamed `READY`; `UNKNOWN` delivery
is never treated as `FAILED`; `READY_EMPTY` is not an error; no new recommendation/priority/urgency/risk score
is invented; no missing metric is inferred; currencies and units are never aggregated; outcome follow-up never
claims causation; records are never selected by filesystem mtime (mtime feeds a display-only freshness signal).
Stale or missing data is displayed honestly with a stale indicator.

---

## 7. Console audit trail

An append-only, hash-chained orchestration audit log lives at `runs/T2/phase7/7.13/audit/console_audit.jsonl`.
Each event records: schema version, console event id, request id, actor, a secret-free session fingerprint,
action, canonical target IDs, canonical parameter hash, accepted authority, preparation result, execution
result, upstream result id, readiness, policy result, previous-event hash, event hash, and aggregate hash.
Operational timestamps live outside the immutable event identity. A corrupt audit chain appears as an integrity
block, blocks further state-changing operations, and leaves read-only views available. The audit log is never
a duplicate business authority.

---

## 8. Workspace and exports

The console reads the standard phase directories `runs/T2/phase7/7.3 … 7.12` and writes only under its own
`runs/T2/phase7/7.13/` workspace (`audit/`, `snapshots/`, `exports/`, `logs/`, `validation/`). It never
directly mutates an upstream runtime record; an accepted authority updates its own runtime directory only after
an explicit owner action. Nothing under `runs/` is committed.

Deterministic consolidated exports: `owner_console_snapshot.json`, `owner_console_status.tsv`,
`owner_console_report.md`. TSV cells are formula-injection-safe (`=`, `+`, `@`, `|` neutralised; CR/LF stripped)
while legitimate negative numbers and Vietnamese Unicode are preserved. Exports exclude secrets, cookies, CSRF
tokens, session tokens, endpoint secrets, Authorization values, local absolute paths, raw source HTML, and
customer personal data.

---

## 9. CLI

```
python -m production.phase7_unified_owner_console --workspace-root "runs/T2/phase7" \
  --base-dir "runs/T2/phase7/7.13" [--host 127.0.0.1] [--port 8780] <command>
```

Commands: `serve`, `snapshot`, `export`, `verify-state`, `validate-only`. `validate-only` performs no DNS, no
HTTP, no secret read, creates no directory, writes no file, creates no lock, creates no session, mutates no
upstream state, executes no action, launches no browser, and executes no subprocess.
