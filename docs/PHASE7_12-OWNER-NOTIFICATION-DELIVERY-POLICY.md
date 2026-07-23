# Phase 7.12 — Owner Notification Delivery Policy

Phase 7.12 delivers the owner's **own** LOCAL alerts (the accepted Phase 7.11 owner alerts) to a
destination the owner has **explicitly approved**. It is the first toolkit surface that pushes data
*outward*, so it is gated more tightly than any inbound research surface. This document is the policy
contract for `production/phase7_owner_notification_delivery.py`.

## Permanent Amazon-account boundary (never crossable)
Evaluated **before** any notification-endpoint allowlist, in every path, by
`core.network_policy.classify_destination` (which cannot be overridden by any flag, mode, config or
allowlist). Always denied: Seller Central, Seller Central login, seller-account OAuth/credentials/
cookies/sessions/tokens, Amazon SP-API, Amazon Ads API, automatic report downloads, campaign/bid/
budget/keyword/target/negative/listing/inventory mutation, Amazon bulk upload, Seller-Central browser
automation, and any automated Amazon seller-account action. Every Phase 7.12 record carries
constant-zero Amazon counters; no code path can increment them.

## Owner-only notification scope (what it must never send)
Phase 7.12 delivers only the owner's own alert summaries to the owner's own approved endpoints. It
must **never** send: customer messages, review requests, marketing campaigns, buyer communication,
Amazon messages, purchase/checkout requests, or Seller-Central notifications. It runs no JavaScript,
launches no browser, installs no service, and registers no OS scheduler.

## Authority reuse
The accepted Phase 7.11 module (`production.phase7_connected_research_watchlists`) is the **sole**
authority for watchlist identity, alert identity, alert status, alert history, owner severity, source
lineage, change lineage, state integrity and the constant-zero Seller-Central counters. Phase 7.12
reads verified alerts through that authority's Python functions and **never** modifies Phase 7.11
alert state, never acknowledges/dismisses/reopens an alert, never modifies a watchlist, never creates
a second alert or public-research authority, and never invokes the Phase 7.11 CLI through a shell.
Shared helpers (`core.network_policy`, `core.diagnostics`, `core.money`, and the reused Phase 7.10
canonical-JSON / atomic-write / redaction / TSV helpers) are reused, not copied.

## Delivery channels
- **local-outbox** — writes a deterministic local notification preview; performs no network request.
- **https-json-webhook** — a fixed-schema JSON `POST` to an owner-approved endpoint. Compatible with
  Slack / Discord / Microsoft Teams incoming webhooks, Make / Zapier / n8n, or the owner's own HTTPS
  endpoint. Payload formats: `generic-json`, `slack`, `discord`, `teams`. One bounded HTTPS transport
  authority; `POST` only; no SMTP, no arbitrary email, no customer messaging.

## Route schema (`phase7-12-route-v1`)
Fields: `schema_version`, `route_id`, `name`, `description`, `enabled`, `channel`, `payload_format`,
`destination_label`, `endpoint_env`, `endpoint_host_allowlist`, `authentication`, `filter`,
`digest_policy`, `quiet_hours`, `retry_policy`, `rate_limit`, `content_policy`, `tags`, `owner_notes`,
`auto_send_approved`. Unknown fields are rejected; a forbidden-shaped unknown field (secret / header /
cookie / command / template shape) is rejected with `FORBIDDEN_FIELD`.

The route **never contains a secret value**. Secrets are referenced only by environment-variable
NAME, e.g. `PHASE7_12_WEBHOOK_URL`, `PHASE7_12_WEBHOOK_BEARER_TOKEN`, `PHASE7_12_WEBHOOK_HMAC_SECRET`.
Authentication modes: `none`, `bearer-env`, `hmac-sha256-env`. A route string value may never contain
a literal URL (`://`), URL userinfo, a bearer token, or a key-shaped secret (`FORBIDDEN_VALUE`), and
an `endpoint_host_allowlist` host may never be an Amazon destination
(`SESSION7_12_SELLER_CENTRAL_POLICY_BLOCKED`).

### Route identity
`route_id = "route-" + sha256(canonical identity)[:24]` over: channel, payload format, destination
label, endpoint env-name, approved host allowlist, authentication (mode + env NAMES), filter, digest
policy, quiet hours, retry policy, rate limit, content policy, and `auto_send_approved`. It **excludes**
the actual endpoint URL / tokens, timestamps, file mtime, temporary path, pid, random uuid, mutable
approval state and delivery history. Reordered JSON keys and annotation-only edits (name/description/
notes/enabled/tags) give the same `route_id`.

## Route approval (append-only hash chain)
Live network delivery requires an explicit owner approval record. `approve-route`, `revoke-route`,
`show-approval`, `list-approvals`. Each approval event contains: `schema_version`, `approval_id`,
`route_id`, exact `route_content_hash`, owner `actor`, owner `note`, `delivery_mode`, `status`
(`APPROVED`/`REVOKED`), `allow_auto_send`, `automation_token_hash`, `previous_event_hash`
(`prev_state_hash`), `event_hash`, and an aggregate `head_hash`. The chain is append-only; a route
modification invalidates the previous approval (the bound content hash no longer matches); a corrupted/
deleted/reordered/tampered chain blocks delivery (`SESSION7_12_APPROVAL_BLOCKED`). Approvals contain
no webhook URL or secret value.

## Live-delivery gate
Local preview and local outbox work without live-network permission. Live HTTPS delivery requires
**all** of: (1) a valid route; (2) a valid route approval; (3) the route content hash matching the
approved hash; (4) the endpoint value available through the declared environment variable; (5) the
endpoint host matching the approved allowlist; (6) the network policy passing; (7)
`PHASE7_12_ALLOW_LIVE_DELIVERY=1`; and (8) the exact confirmation token `--confirm-send "SEND:<batch-id>"`.
Automatic `send-due` additionally requires: a valid approved route, `PHASE7_12_ALLOW_LIVE_DELIVERY=1`,
route `auto_send_approved=true`, an approval that explicitly allows auto-send, and a non-secret local
automation token bound to the route content hash (`PHASE7_12_AUTO_SEND_TOKEN`). Live delivery is never
silently enabled.

## Endpoint policy (one canonical validator)
Every webhook endpoint passes through `core.network_policy.evaluate_notification_delivery_url` before a
socket is opened: HTTPS only (no public HTTP; no file/ftp/data/gopher/javascript scheme); no URL
userinfo/embedded password; the Amazon-account boundary first; no private/loopback/link-local/metadata
destination; no raw or encoded IP literal (public destinations must be DNS names so TLS pins them);
DNS resolution validated with a mixed safe/unsafe result blocked (defeats rebinding); no implicit
proxy environment; TLS verification never bypassed; bounded connect/read timeouts and body. Webhook
redirects are disabled — any 3xx response is recorded `DELIVERY_BLOCKED_REDIRECT` and never followed.

## Webhook request model
`POST` only. Fixed headers: `Content-Type: application/json`, `User-Agent`, `Accept`,
`Idempotency-Key` (= delivery id), and — only for `bearer-env` — `Authorization: Bearer <secret>`; for
`hmac-sha256-env`, `X-Phase7-Signature: sha256=<hmac>` and `X-Phase7-Timestamp-Version`. Route-provided
header names/values are impossible. Payload default 64 KiB, absolute maximum 256 KiB; response body
read limit 64 KiB. Only the status code, a safe selected-header subset, a bounded sanitized response
summary, and the body SHA-256 are stored — never a full provider response body, never a Set-Cookie.

## Source selection
Batches use verified Phase 7.11 alerts only — selected by valid alert id, valid alert-state aggregate,
valid history chain, watchlist id, rule id, owner severity, status, change lineage and source lineage
(never filesystem mtime). Filters: watchlist id, route, owner severity, alert status, rule id, source
type, field path, exact owner label. Default eligibility: alert status OPEN, route enabled + approved,
not already successfully delivered through the same route, quiet-hours permitting, digest due.
ACKNOWLEDGED and DISMISSED alerts are excluded by default and included only when explicitly configured.

## Digest model
Modes: `immediate`, `hourly`, `daily`, `weekly`, `manual`. `zoneinfo` timezones; minimum automatic
interval 1 hour. Digest windows are explicit and deterministic (`immediate`/`manual` collapse to a
constant; `hourly`/`daily`/`weekly` bucket the reference time into an explicit local window label). No
AI, no LLM, no invented recommendations — fixed built-in templates only.

## Quiet hours
Owner-configured: timezone, start/end local time, weekdays, and an explicit severity-bypass list.
Handles same-day and overnight windows and DST via `zoneinfo`. Owner severity remains owner-defined;
there is no implicit urgency calculation and no implicit bypass.

## Batch, delivery, states and identity
`batch_id` derives from the exact route content hash + approved route identity + digest-period identity
+ sorted eligible alert ids + template version + payload format + content-policy version — never a
runtime timestamp, mtime, temp path, pid, uuid or iteration order. `delivery_id` derives from batch id
+ route id + provider type + destination label + approved endpoint host + payload hash — never the
secret endpoint path or token. Delivery states: `QUEUED`, `PREVIEWED`, `SENT`, `FAILED`, `UNKNOWN`,
`RATE_LIMITED`, `QUIET_HOURS`, `SKIPPED`, `BLOCKED`, `REVOKED`, `CONFIRMATION_REQUIRED`,
`PROVIDER_UNAVAILABLE`. 2xx = SENT only after the local delivery record is committed; 3xx = BLOCKED
(never followed); 400/401/403/404 and most 4xx = FAILED, no auto-retry; 408/429 and 5xx may be
retryable; a pre-send connection failure may be retryable; a post-send timeout is UNKNOWN and is never
auto-retried (owner confirmation required).

## Idempotency, retry, rate limiting
The `Idempotency-Key` header is the stable delivery id; a delivery already SENT is never resent
(`IDEMPOTENT_REUSE`) and reuses the same delivery id. A changed payload produces a new delivery
identity; a changed route approval invalidates stale pending deliveries. Retry is bounded
(`max_attempts` ≤ 5, initial delay ≥ 1 s, maximum delay ≤ 1 h) and never applies to policy/approval/
revoked/3xx/authentication/invalid-payload/UNKNOWN-without-confirmation/Seller-Central blocks; retry
schedules are operational metadata and never affect delivery identity. Rate limiting supports
per-route and per-destination hourly caps, a minimum interval, a maximum alerts-per-batch and a
maximum payload size; a rate-limited batch remains pending and nothing is silently discarded.

## Delivery history (append-only hash chain)
Per delivery, each event stores `schema_version`, `delivery_id`, `attempt_sequence`,
`previous_event_hash`, `event_type`, `delivery_state`, `provider_status`, `payload_sha256`,
`route_hash`, `approval_hash`, `actor`, `sanitized_reason`, `event_hash`. Events: `QUEUED`,
`PREVIEWED`, `SEND_STARTED`, `SENT`, `FAILED`, `UNKNOWN`, `RETRY_SCHEDULED`, `RETRY_STARTED`,
`RATE_LIMITED`, `BLOCKED`, `REVOKED`, `OWNER_RETRY_APPROVED`. A corrupted/deleted/reordered/truncated
chain blocks any further delivery update.

## Locking, atomicity, exports, scheduler
Route and batch locks live only under `runs/T2/phase7/7.12/locks/`, use atomic `O_CREAT|O_EXCL`
creation, report a bounded lock age, are broken only by an explicit owner command, and are released
only by the owning process. All state is written atomically (temp sibling + fsync + read-back verify +
atomic replace); on failure the last valid state remains, no delivery is falsely marked SENT, and no
partial batch/history is accepted. Exports are deterministic JSON / TSV / Markdown with
formula-injection-safe TSV cells and no secret. The scheduler plan for `send-due` is **read-only**
(`OWNER_ACTION_REQUIRED`) and never executes `schtasks` / `cron` / `crontab` / `systemctl` /
`launchctl` / Task Scheduler COM; it references environment-variable NAMES only.

## Readiness states
`SESSION7_12_NOTIFICATION_READY(_EMPTY|_PARTIAL)`, `SESSION7_12_ROUTE_READY|REQUIRED|BLOCKED`,
`SESSION7_12_APPROVAL_READY|REQUIRED|BLOCKED`, `SESSION7_12_BATCH_READY(_EMPTY)`,
`SESSION7_12_DELIVERY_CONFIRMATION_REQUIRED|SENT|FAILED|UNKNOWN`, `SESSION7_12_PROVIDER_UNAVAILABLE`,
`SESSION7_12_RATE_LIMITED`, `SESSION7_12_QUIET_HOURS`, `SESSION7_12_NOT_DUE`,
`SESSION7_12_ALERT_SOURCE_BLOCKED`, `SESSION7_12_DELIVERY_STATE_BLOCKED`,
`SESSION7_12_INTEGRITY_BLOCKED`, `SESSION7_12_SELLER_CENTRAL_POLICY_BLOCKED`, plus operational
LIST/SHOW/VERIFY/EXPORT/SCHEDULER/VALIDATE/PROVIDER_CHECK/LOCK success states. Policy, approval and
integrity blocks exit nonzero; `NOT_DUE`, `READY_EMPTY`, `QUIET_HOURS` and `RATE_LIMITED` are not
errors.

## Workspace
`runs/T2/phase7/7.12/` (subdirs: `routes/`, `approvals/`, `batches/`, `outbox/`, `deliveries/`,
`delivery_history/`, `retries/`, `digests/`, `reports/`, `scheduler_plans/`, `validation/`, `locks/`,
`logs/`). Phase 7.11 alerts are read from `runs/T2/phase7/7.11/`. Phase 7.12 never writes into the
Phase 7.3–7.11 workspaces, and nothing under `runs/` is committed.
