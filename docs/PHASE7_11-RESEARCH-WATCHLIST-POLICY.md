# Phase 7.11 — Connected Research Watchlists, Change Detection & Owner Alerts — Policy

This document is the authoritative policy for the Phase 7.11 authority
`production/phase7_connected_research_watchlists.py`. It is additive to — and never weakens —
the accepted Phase 7.2–7.10 behavior.

## 1. Permanent Amazon-account boundary (never crossable)

Phase 7.11 performs **no** network work of its own. Every fetch, parse, redirect, robots
decision, SSRF guard and — above all — the **Amazon-account denial** is delegated to the
accepted Phase 7.10 authority (`production.phase7_connected_public_research`) and
`core.network_policy`, whose Seller-Central / seller-account classification always wins **first**,
before any allowlist decision.

The following remain permanently prohibited and are unreachable from any Phase 7.11 code path,
flag or configuration:

- Amazon Seller Central; Seller Central login; seller-account OAuth;
- seller-account credentials, cookies, sessions or access tokens;
- Amazon SP-API; Amazon Ads API; automatic Seller Central report downloads;
- campaign / bid / budget / keyword / target / negative-target mutation;
- listing / inventory mutation; Amazon bulk-file upload;
- Seller Central browser automation; CAPTCHA bypass;
- any automated Amazon seller-account mutation.

Every Phase 7.11 record and command result carries the constant-zero Amazon counters
(`seller_central_connections`, `amazon_seller_api_calls`, `amazon_ads_api_calls`,
`amazon_seller_auth_calls`, `amazon_seller_mutations`, `amazon_report_downloads`,
`amazon_bulk_uploads`, `browser_automation_actions`, `amazon_credential_store_count`). No code
path can increment them. Public Amazon US **retail product pages** remain permitted **only** through
the accepted Phase 7.10 public-research authority.

## 2. What Phase 7.11 must never do

It never: opens a second HTTP transport; implements a second public-page parser; bypasses the 7.10
network policy; scrapes a URL directly; launches a browser; executes JavaScript; crawls linked
pages; invents demand; estimates sales / revenue / conversion / search volume / inventory;
calculates opportunity / competitor / viability scores; makes campaign / bid / budget
recommendations; automatically mutates any external service; automatically registers a Windows
scheduled task or cron job; installs a background service; or sends any email, webhook or chat
notification. **Alerts are local owner alerts only.**

## 3. Authority reuse

The one canonical authority for source descriptors, research-plan validation, public-network
policy, Seller-Central denial, public-Amazon boundaries, redirects, SSRF protection, robots
handling, content limits, capture records, evidence records, cache validation, source parsing,
secret redaction, evidence identity and source-content hashes is
`production.phase7_connected_public_research`. Phase 7.11 imports and orchestrates it through its
documented Python functions (`Config`, `run_plan`, `verify_run`, `normalize_plan`,
`_normalize_descriptor`, `_normalize_locator`, `_source_id`, `_load_manifest`, `_load_capture`,
`_tsv_cell`, `canonical_json`, `content_sha256`, `redact`, the atomic writers). It also reuses
`core.network_policy`, `core.diagnostics` and `core.money` (Decimal-only numeric safety). No 7.10
fetch, parser or network logic is copied. **No accepted file was modified.**

## 4. Workspace separation & upstream immutability

- Phase 7.11 writes **only** under `runs/T2/phase7/7.11/`.
- New watchlist fetches run the reused 7.10 authority into a **7.11-owned** 7.10-format workspace
  at `runs/T2/phase7/7.11/research/`. The accepted `runs/T2/phase7/7.10/` tree is **read-only** to
  Phase 7.11 (used only by `compare`) and is never modified.
- The Phase 7.3–7.10 runtime trees are never written. `runs/` remains git-ignored.

## 5. Watchlist identity

`watchlist_id = "wl-" + sha256(canonical_json(identity))[:24]` over the stable identity fields
`{name, timezone, schedule, sources (7.10 descriptor + comparison policy), comparison_policy,
alert_rules (operator/selector/value/unit/currency/severity), tags}`. Reordered JSON keys give the
same id. Identity never depends on a creation timestamp, file mtime, temporary directory, process
id, random uuid or filesystem-enumeration order, and excludes mutable annotations (`description`,
`owner_notes`, `created_by`, `enabled`) and all acknowledgement state. Unknown fields, and any
secret / header / cookie / command / shell / eval / dynamic-import field, are rejected.

## 6. Schedules (operational only)

`manual`, `hourly`, `daily`, `weekly`, `interval-hours`. Minimum automatic interval is **1 hour**.
Timezones use `zoneinfo`; unknown timezone names are rejected. Due/next-due, missed-run handling,
bounded catch-up (at most one run per invocation), clock-moving-backward and DST forward/backward
transitions are all handled. Schedule timestamps are **operational metadata** and never affect
watchlist, source, evidence, change or alert identity. Phase 7.11 never runs continuously, never
starts a daemon, and never registers Task Scheduler or cron.

## 7. Scheduler plan (read-only, owner registers)

`scheduler-plan` emits a deterministic, read-only plan with a Windows PowerShell example, a cron
example and a manual example. It never executes or registers `schtasks`, `cron`, `crontab`,
`systemctl`, `launchctl` or Task Scheduler COM APIs. It uses the current Python executable safely,
quotes paths safely (PowerShell single-quote doubling / POSIX single-quote escaping), contains no
secret and no Seller-Central target, and states clearly that registration is `OWNER_ACTION_REQUIRED`.

## 8. Baseline selection & change detection

Comparison uses accepted, verified evidence only. For each watchlist source, the current accepted
capture is compared with the immediately preceding accepted capture for the same **stable source
identity** (source id, capture id, content hash, evidence ids, run lineage, manifest integrity) —
never by filesystem mtime. A first successful observation is `INITIAL_BASELINE`, not a false
business change. A blocked / unavailable / corrupt current capture **never overwrites** the last
valid baseline.

Change statuses: `INITIAL_BASELINE`, `ADDED`, `REMOVED`, `CHANGED`, `UNCHANGED`,
`SOURCE_UNAVAILABLE`, `SOURCE_BLOCKED`, `PARSE_PARTIAL`, `INTEGRITY_BLOCKED`,
`COMPARISON_NOT_AVAILABLE`. Change identity is content-addressed
(`chg-` + sha256 of the field/lineage/value identity) and never depends on event timestamp, row
position, sort, filter or pagination.

Comparison is **exact** field-level by stable evidence field path and type. Permitted technical
normalization: Unicode NFC, normalized whitespace, canonical 7.10 URL normalization, `Decimal`
parsing for explicit numeric values, and case normalization **only** for field paths the owner
documents as case-insensitive. No fuzzy matching, no LLM comparison, no semantic embeddings. A page
content-hash change with no accepted evidence change is recorded as **source-content changed but
evidence unchanged**.

Numeric delta / percent are computed **only** when both old and new values are explicit numeric
evidence with **identical unit and identical currency**; percent change is never computed when the
previous value is zero. No aggregation across currencies, marketplaces, incompatible units,
incompatible parser definitions or incompatible source identities.

## 9. Owner alert rules & alerts

Alerts are driven **only** by explicit owner-configured rules. Operators: `field-changed`,
`field-added`, `field-removed`, `value-equals`, `value-contains`, `numeric-above`, `numeric-below`,
`absolute-delta-at-least`, `percent-change-at-least`, `source-unavailable`, `source-blocked`,
`parse-partial`, `integrity-blocked`. Matching is exact / bounded substring — **no unrestricted
regular expressions**. A numeric rule whose unit or currency is incompatible with the evidence does
not fire.

Owner severity is one of `INFO`, `REVIEW`, `IMPORTANT`, `CRITICAL` and is **owner-defined**; the
system never calculates a severity, urgency or score, and never emits a recommendation.

Alert identity is content-addressed (`alt-` + sha256 of `{watchlist_id, item_id, rule_id,
change_id}`) and remains stable when acknowledged, dismissed or reopened. Mutable status is stored
separately from the immutable alert identity. Duplicate OPEN alerts for the same rule + same change
are never recreated.

## 10. Acknowledgement audit trail

`acknowledge-alert`, `dismiss-alert`, `reopen-alert` require an exact alert id and an explicit owner
actor label (optional note). Each action appends an event to a per-watchlist **append-only,
hash-chained** history: `event_hash = sha256({seq, alert_id, action, actor, note,
prev_state_hash})`, chained through `prev_state_hash`, with an aggregate `head_hash`. The operational
timestamp is stored **outside** the hash identity. A corrupted, deleted or reordered history chain
blocks all further state updates (`SESSION7_11_ALERT_STATE_BLOCKED`).

## 11. Concurrency, atomicity, idempotency

Locks live only under `runs/T2/phase7/7.11/locks/`, are created atomically (`O_CREAT|O_EXCL`), carry
owner process metadata, and are released only by the owning process. A lock older than the bound is
**reported** stale but never silently removed; explicit recovery requires `--break-stale-lock`.
Different watchlists run independently. All state (watchlists, executions, comparisons, changes,
alerts, alert-state, history, reports, scheduler plans, validation) is written via temp-sibling +
fsync + read-back + atomic replace, so a failed write never overwrites the last valid artefact.
Repeated execution over identical accepted 7.10 evidence reuses the same comparison, change and
alert identities and never duplicates alerts.

## 12. Exports

Deterministic `watchlist_snapshot.json`, `watchlist_changes.tsv`, `watchlist_report.md`,
`watchlist_alerts.json`. TSV formula-injection is neutralized (leading `= + - @` etc. quoted) while
legitimate negative numbers such as `-2.50` are preserved; tabs/newlines are neutralized; Vietnamese
Unicode is preserved. Exports never contain credentials, tokens, cookies, Authorization headers,
signed URLs, proxy credentials, local absolute paths, customer data, Seller-Central upload
structures, invented demand/sales, recommendations or executable content, and always carry the
constant-zero Amazon counters.

## 13. Readiness states

`SESSION7_11_WATCHLIST_READY`, `_READY_EMPTY`, `_READY_PARTIAL`, `_WATCHLIST_REQUIRED`,
`_WATCHLIST_BLOCKED`, `_NOT_DUE`, `_SOURCE_REQUIRED`, `_SOURCE_BLOCKED`, `_NETWORK_UNAVAILABLE`,
`_RESEARCH_RUN_BLOCKED`, `_BASELINE_REQUIRED`, `_COMPARISON_READY`, `_COMPARISON_READY_EMPTY`,
`_COMPARISON_BLOCKED`, `_ALERTS_READY`, `_ALERTS_READY_EMPTY`, `_ALERT_STATE_BLOCKED`,
`_INTEGRITY_BLOCKED`, `_SELLER_CENTRAL_POLICY_BLOCKED` (plus read/utility success states). Policy and
integrity blocks return a nonzero process exit. `NOT_DUE` is not an error; a valid first baseline is
not an error; a valid run with no changes is `READY_EMPTY`.

## 14. CLI

`create-watchlist`, `validate-watchlist`, `show-watchlist`, `list-watchlists`, `run-watchlist`,
`run-due`, `compare`, `list-executions`, `list-changes`, `list-alerts`, `acknowledge-alert`,
`dismiss-alert`, `reopen-alert`, `verify-state`, `export`, `scheduler-plan`, `validate-only`.
`validate-only` performs no network request, no DNS resolution, writes no file, creates no base
directory, acquires no lock, mutates no alert state, launches no browser and executes no subprocess.
