# Session 7.12 — Owner Notification Delivery, Digest Queue & Audit Trail — Implementation Report

## Identity
- **Branch:** `phase7-12-owner-notification-delivery`
- **Exact baseline:** `e293f4e807c68b7da9f8f276d8bae9024626ec48` (= `origin/main`)
- **Checkpoint tag:** `phase7-12-owner-notification-delivery-checkpoint-e293f4e`
- **Implementation commit (feat):** `a750306936f6c0775ef3c830ebbf8d6669ef5750`
- **Proof commit (docs):** `(the docs commit that adds this file — see git log on branch phase7-12-owner-notification-delivery)`
- **Accepted prior tags verified present:** phase7-2-cumulative-accepted-d5ad841,
  phase7-3-accepted-7005275, phase7-4-owner-dashboard-accepted-eebecc5,
  phase7-5-owner-decision-package-accepted-66d972d, phase7-6-manual-action-tracker-accepted-f1d11d8,
  phase7-7-outcome-followup-accepted-581ae49, phase7-8-owner-operations-dashboard-accepted-80333ec,
  phase7-9-connected-backup-update-recovery-accepted-383569e,
  phase7-10-connected-public-research-accepted-9888e69,
  phase7-11-connected-research-watchlists-accepted-c024367
- **No Phase 7.12 acceptance tag exists** (this is an implementation, not an acceptance).

## Connectivity boundary & Seller-Central prohibition
The permanent Amazon-account boundary is unchanged and permanent. Every webhook endpoint is validated
by `core.network_policy.evaluate_notification_delivery_url`, which runs the accepted
`classify_destination` Amazon-account check **first** — before any notification-endpoint allowlist.
Seller Central, Seller Central login, seller-account OAuth/credentials/cookies/sessions/tokens,
SP-API, the Ads API, automatic report downloads, campaign/bid/budget/keyword/target/negative/listing/
inventory mutation, bulk upload and browser automation remain permanently prohibited and unreachable.
Every Phase 7.12 record carries constant-zero Amazon counters; no code path increments them
(verified: `amazon_counters_all_zero = true`).

## Owner-only notification scope
Phase 7.12 delivers only the owner's own accepted Phase 7.11 alert summaries to owner-approved
destinations. It never sends customer messages, review requests, marketing campaigns, buyer
communication, Amazon messages, purchase/checkout requests or Seller-Central notifications. It runs no
JavaScript, launches no browser, installs no service and registers no OS scheduler.

## Files created
- `production/phase7_owner_notification_delivery.py` — the ONE Phase 7.12 authority.
- `tests/test_phase7_12_owner_notification_delivery.py` — 234 focused tests.
- `docs/PHASE7_12-OWNER-NOTIFICATION-DELIVERY-POLICY.md` — the policy contract.
- `SESSION7_12-OWNER-NOTIFICATION-DELIVERY-IMPLEMENTATION-REPORT.md` — this report.
- `SESSION7_12-OWNER-NOTIFICATION-DELIVERY-PROOF-GATE.json` — the proof gate.

## Files modified
- `core/network_policy.py` — **purely additive** (0 deletions, 119 insertions): a new
  `PURPOSE_OWNER_NOTIFICATION`, `evaluate_notification_delivery_url(...)` and
  `assert_notification_delivery_allowed(...)`. Reuses the accepted Amazon-account classification and
  SSRF/allowlist helpers; changes no existing evaluator.
- `docs/CONNECTIVITY-POLICY.md` — a purely additive v3 amendment permitting owner-approved outbound
  notification delivery (49 insertions, 0 deletions; LF preserved).
- `docs/CONNECTIVITY-POLICY-MANIFEST.json` — bumped to `connectivity-policy-v3`, `policy_sha256`
  rolled to `16695ae2afa71d27096b69f7b79caf0490c62749d2a729774486adaebf3ef9d6`, the prior v2 hash
  preserved as `policy_sha256_v2_history`. The Phase 6C/6F doc-hash tests pass against the new hash.

## Dependencies
None added. Standard library only (`argparse`, `datetime`, `hashlib`, `hmac`, `os`, `re`, `socket`,
`ssl`, `sys`, `urllib`, `zoneinfo`). Reuses `production.phase7_connected_research_watchlists` (7.11),
`production.phase7_connected_public_research` (7.10 helpers), `core.network_policy`,
`core.diagnostics`, `core.money`.

## Phase 7.11 authority reuse
The accepted Phase 7.11 module is the sole authority for watchlist identity, alert identity, alert
status, alert history, owner severity, source lineage, change lineage, state integrity and the
constant-zero Seller-Central counters. Phase 7.12 reads verified alerts through `W.list_watchlists`,
`W.load_watchlist`, `W.verify_state`, `W._load_alert_state` (raises on a corrupt chain) and
`W.list_alerts`. A corrupt/tampered watchlist, alert state or alert history blocks that source
(`SESSION7_12_ALERT_SOURCE_BLOCKED`). Phase 7.12 never modifies Phase 7.11 state, never
acknowledges/dismisses/reopens an alert, never modifies a watchlist, and creates no second alert or
public-research authority.

## Route schema — result
`phase7-12-route-v1`. Strict field allowlist; unknown fields rejected (`FORBIDDEN_FIELD` for a
secret/header/cookie/command/template-shaped unknown field, else `ROUTE_UNKNOWN_FIELD`). No secret
value permitted; secrets are referenced only by environment-variable NAME. Route identity excludes the
endpoint URL/tokens, timestamps, mtime, temp path, pid, uuid, mutable approval state and delivery
history. Verified: reordered JSON keys and annotation-only edits produce the same `route_id`
(`route_id_reordered_equal = true`); sample `route_id = route-818de108682f2ae63994c2b3`,
`route_content_hash = fbf442f98d959479d2292e9d7e08320975da0783ec8709ee9301f0ca91d39b08`. An Amazon
host in the allowlist is rejected (`ENDPOINT_ALLOWLIST_REJECTED` / `SELLER_CENTRAL_POLICY_BLOCKED`).

## Approval chain — result
`phase7-12-approval-state-v1`, append-only hash-chained, one file per route. `approve-route`,
`revoke-route`, `show-approval`, `list-approvals`. A route modification invalidates the previous
approval (the bound content hash no longer matches); a tampered/deleted/reordered/corrupt chain blocks
delivery (`SESSION7_12_APPROVAL_BLOCKED`). Sample aggregate hash
`d2bca473bc3d0b713481f3988aa50b51f443c761c65ea52d5d9001caecdd91da`. Approvals contain no URL or secret.

## Endpoint policy — result
One canonical validator: HTTPS only; no HTTP public; no file/ftp/data/gopher/javascript scheme; no URL
userinfo/password; Amazon-account boundary first; no private/loopback/link-local/metadata; no raw or
encoded IP literal; DNS resolution validated with mixed safe/unsafe blocked; no implicit proxy; TLS
verification never bypassed; bounded timeouts/body. Redirects disabled (any 3xx →
`DELIVERY_BLOCKED_REDIRECT`, never followed). SP-API endpoint denied (`AMAZON_API_PROHIBITED`);
DNS-rebinding to a private address blocked at send (`SESSION7_12_DELIVERY_BLOCKED`).

## Live-delivery gate — result
Local preview/outbox work with no network permission. Live HTTPS delivery requires: valid route,
valid approval, route hash == approved hash, endpoint env available, host on the allowlist, network
policy pass, `PHASE7_12_ALLOW_LIVE_DELIVERY=1`, and `--confirm-send "SEND:<batch-id>"`. `send-due`
additionally requires `auto_send_approved=true`, an approval allowing auto-send, and a route-hash-bound
`PHASE7_12_AUTO_SEND_TOKEN`. A missing env gate or wrong confirmation returns
`SESSION7_12_DELIVERY_CONFIRMATION_REQUIRED`; delivery is never silently enabled.

## Webhook transport — result
One bounded HTTPS `POST` authority: `_NoRedirect` + `ProxyHandler({})` + `HTTPSHandler(CERT_REQUIRED,
check_hostname=True)`, no cookies, no credential persistence, bounded read. Fixed headers only
(`Content-Type: application/json`, `User-Agent`, `Accept`, `Idempotency-Key`, and — by auth mode —
`Authorization: Bearer` or `X-Phase7-Signature`/`X-Phase7-Timestamp-Version`). Payload default 64 KiB,
absolute ceiling 256 KiB, response read limit 64 KiB. Only status + safe headers + a bounded sanitized
summary + body SHA-256 are stored. `POST` only; arbitrary methods/headers are impossible.

## Payload formats — result
Fixed built-in `generic-json`, `slack`, `discord`, `teams` templates; every user-controlled value
escaped; no template engine, no `eval`/`exec`, no invented demand/sales/recommendation. No raw source
HTML, no absolute path, no credentials in any payload.

## Source selection — result
Verified Phase 7.11 alerts only, selected by alert identity/state/history + watchlist/rule/severity/
status/source-type/field-path/owner-label; never mtime. Default eligibility OPEN + enabled + approved +
not-already-sent-through-the-route + quiet-hours + digest-due. ACKNOWLEDGED/DISMISSED excluded unless
explicitly configured.

## Digest — result
`immediate`/`hourly`/`daily`/`weekly`/`manual`, zoneinfo, 1-hour minimum. Deterministic window labels:
`immediate`, `manual`, `hourly:2026-07-23T10`, `daily:2026-07-23`, `weekly:2026-W30` (verified). Digest
"not due" is not an error.

## Quiet hours — result
Timezone + start/end + weekdays + severity-bypass. Same-day, overnight, DST-forward and DST-backward
verified; exact-start inclusive, exact-end exclusive; explicit CRITICAL bypass works; no implicit
bypass. A quiet-hours send returns `SESSION7_12_QUIET_HOURS` (not an error).

## Batch identity — result
`phase7-12-batch-v1`. Derived from route content hash + route identity + digest period + sorted alert
ids + template version + payload format + content-policy version; excludes timestamp/mtime/temp/pid/
uuid/order. Deterministic (`batch_id_deterministic = true`); alert-order-independent; a route or
alert-set change changes the id. Sample `batch-386e8f93566b6bf89ea77021`.

## Delivery identity — result
`dlv-…` from batch id + route id + provider type + destination label + approved endpoint host +
payload hash; excludes the secret path/token. Stable; a payload change changes the id. Sample
`dlv-66a6198d259d0d085a4f49b7`.

## Retry — result
Bounded (`max_attempts` ≤ 5, initial ≥ 1 s, maximum ≤ 1 h; retryable-status list validated). Verified:
408/429/500/503 retryable, 400/401/403/404 not retryable, pre-send connection failure retryable, a
`503` delivery retried to `SENT`. No retry for policy/approval/revoked/3xx/auth/invalid/UNKNOWN-without-
confirmation. Retry schedules are operational metadata and do not affect delivery identity.

## UNKNOWN behavior — result
A post-send timeout is `UNKNOWN` (`SESSION7_12_DELIVERY_UNKNOWN`), is never auto-retried (no retry
state file written), and requires the exact owner confirmation `RETRY-UNKNOWN:<delivery-id>` before a
retry proceeds.

## Rate limiting — result
Per-route and per-destination hourly caps + minimum interval + max alerts per batch + max payload.
Verified: a second send within the same hour is `SESSION7_12_RATE_LIMITED` (`ROUTE_HOURLY_LIMIT`) and
the batch is preserved; nothing discarded.

## Delivery history — result
`phase7-12-delivery-history-v1`, append-only hash-chained per delivery. Events QUEUED/PREVIEWED/
SEND_STARTED/SENT/FAILED/UNKNOWN/RETRY_SCHEDULED/RETRY_STARTED/RATE_LIMITED/BLOCKED/REVOKED/
OWNER_RETRY_APPROVED. Tamper/delete/reorder/truncate detected; a corrupt chain blocks further updates.
Sample head hash `01962d03a2fa3fe04dee92148a5e50ed21dfa6f23438fac9a69577adbd072a54`.

## Locking — result
Route + batch locks under `runs/T2/phase7/7.12/locks/` only; atomic `O_CREAT|O_EXCL`; a held lock is
`LOCK_HELD`; different routes are independent; a stale lock is reported (never auto-removed) and broken
only by explicit `break-stale-lock`; a lock is released only by its owning process.

## Atomicity & idempotency
All records written atomically (temp sibling + fsync + read-back verify + atomic replace); no `.tmp`
leftovers after a full run (`atomic_no_temp_leftover = true`). A previously valid state is preserved on
a failed/gated attempt. A delivery already SENT is never resent (`IDEMPOTENT_REUSE`, same delivery id);
a duplicate outbox write is reported idempotent.

## Exports — result
Deterministic JSON/TSV/Markdown; formula-injection-safe TSV (`=`,`+`,`@` prefixed; `-2.50` preserved;
Vietnamese Unicode preserved); equal TSV columns; no secret. Verified `export_deterministic = true`.

## Readiness states
All required `SESSION7_12_*` states are implemented; policy/approval/integrity blocks exit nonzero,
while `NOT_DUE`, `READY_EMPTY`, `QUIET_HOURS` and `RATE_LIMITED` exit 0.

## Tests
- **Phase 7.12 focused:** 234 passed, 0 failed, 0 skipped (exit 0). (≥ 190 required.)
- **Prior focused suites (unchanged, re-run green):** 7.2 = 377 (skip 1); 7.3 = 117; 7.4 = 94;
  7.5 = 109; 7.6 = 100; 7.7 = 93; 7.8 = 152; 7.9 = 139 (skip 1); 7.10 = 191 (skip 1); 7.11 = 189.
- **Full baseline (pre-change):** 3662 passed, 4 skipped, 0 failures, exit 0.
- **Full suite (with Phase 7.12), in-place (runs/T2 data present):** 3896 passed, 4 skipped, 0
  failures, exit 0 (= 3662 baseline + 234; no regressions). This is the authoritative full-suite run.
- **Fresh detached worktree (runs/ absent) at the implementation commit:** Ran 3894 — the new Phase
  7.12 suite (234) all pass; the remaining 1 failure + 14 errors + 329 skips are **PRE-EXISTING and
  byte-identical to the baseline `e293f4e` fresh worktree** (which is 3660 ran, 1 failure, 14 errors,
  329 skips). All 15 are Phase 5/6 tests (`test_backend_semantic_quality`,
  `test_backend_phrase_integrity`, `test_session5d_certification`) that require the untracked
  `runs/T2/` product data; none are in a Phase 7.12 file. Phase 7.12 therefore adds exactly +234
  passing tests and 0 fresh-worktree regressions, and depends on no untracked T2 data.
- **compileall:** exit 0 (`core`, `production`, `tests`).

## Synthetic validation
Genuine Phase 7.11 alert state (INFO/REVIEW/IMPORTANT/CRITICAL OPEN alerts, plus ACKNOWLEDGED and
DISMISSED via the 7.11 authority, plus corrupted state/history) drives the whole flow: route creation,
approval, local preview, local outbox, immediate/daily/hourly/weekly digest, quiet-hours block,
explicit CRITICAL bypass, and the webhook status matrix (200 SENT, 302 BLOCKED, 400/401/403/404
FAILED-no-retry, 408/429/500/503 FAILED-retryable, pre-send connection failure PROVIDER_UNAVAILABLE,
post-send timeout UNKNOWN), idempotent resend prevention, route revocation, retry, UNKNOWN-retry
confirmation, rate limiting, history corruption, concurrent lock and deterministic exports.

## Optional live-delivery result
`NOT_RUN` — no owner-controlled live webhook was exercised; live delivery is optional and not required
for acceptance. All delivery tests use injected transports and never contact the real Internet.

## Upstream immutability
The Phase 7.11 runtime tree was hashed before and after the full flow and is **byte-identical**
(`upstream_immutable = true`). No Phase 7.12 file appears under any Phase 7.3–7.11 directory; the 7.12
workspace (`runs/T2/phase7/7.12`) is separate from the 7.11 alert workspace.

## runs tracking
`runs/` remains gitignored (`git check-ignore runs/T2/phase7/7.12/x` → ignored). No `runs/` content is
committed.

## Prohibited-integration scan
`scripts/connectivity_scan.py`: 0 active Amazon-account paths (`no_active_amazon_account_path = true`).
An AST scan of the module confirms no `subprocess`/`smtplib`/`selenium`/`playwright`/`webdriver`/
`webbrowser` import, no `eval`/`exec`/`compile`/`os.system`/`os.popen` call, no `shell=True`, and no
scheduler-API call. The only outbound primitive is the one bounded HTTPS `POST` transport; no Amazon
literal appears on any outbound line.

## Seller-Central counters
Constant zero in every record (`seller_central_connections`, `amazon_seller_api_calls`,
`amazon_ads_api_calls`, `amazon_seller_auth_calls`, `amazon_seller_mutations`,
`amazon_report_downloads`, `amazon_bulk_uploads`, `browser_automation_actions`,
`amazon_credential_store_count` = 0).

## Known limitations
- Live delivery is owner-controlled and not exercised in CI (by design); the transport is verified via
  injected doubles and a source/AST contract for the real transport.
- `send-due` is bounded to one batch per due route per invocation (no unbounded catch-up).
- Retry backoff is computed but not slept in-process; the retry schedule is operational metadata and
  the owner (or the read-only scheduler plan) drives the next attempt.
- A route rename changes `route_content_hash` (so it re-requires approval) but keeps `route_id`;
  creating a route whose identity is unchanged but content differs returns
  `DUPLICATE_ROUTE_ID_DIFFERENT_CONTENT` (mirrors the accepted Phase 7.11 create semantics).

## Exact CLI examples
```
# create a route
python -m production.phase7_owner_notification_delivery \
  --base-dir "runs/T2/phase7/7.12" --alert-dir "runs/T2/phase7/7.11" \
  create-route --definition "notification-route.json"

# approve it
python -m production.phase7_owner_notification_delivery \
  --base-dir "runs/T2/phase7/7.12" approve-route \
  --route-id "<route-id>" --actor "OWNER" --note "Approved for owner Slack"

# local preview (no approval, no network)
python -m production.phase7_owner_notification_delivery \
  --base-dir "runs/T2/phase7/7.12" --alert-dir "runs/T2/phase7/7.11" \
  preview --route-id "<route-id>"

# send to an approved HTTPS webhook (gated)
$env:PHASE7_12_ALLOW_LIVE_DELIVERY = "1"
$env:PHASE7_12_WEBHOOK_URL = "<owner-controlled URL>"
python -m production.phase7_owner_notification_delivery \
  --base-dir "runs/T2/phase7/7.12" --alert-dir "runs/T2/phase7/7.11" \
  send-batch --batch-id "<batch-id>" --confirm-send "SEND:<batch-id>"
```

## Exact next action
Recommend an **independent acceptance audit** of commits `a750306936f6c0775ef3c830ebbf8d6669ef5750` (feat) and
`(the docs commit that adds this file — see git log on branch phase7-12-owner-notification-delivery)` (docs) on branch `phase7-12-owner-notification-delivery`. Do **not** merge into
main, do **not** create an acceptance tag, and do **not** begin Phase 7.13 until the audit accepts.
