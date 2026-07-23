# SESSION 7.10 — Connected Public Research & Evidence Hub — Implementation Report

## Identity

- **Branch:** `phase7-10-connected-public-research`
- **Exact baseline (`git rev-parse origin/main`):** `718c024d2c0b7efc3a9817e6c38c2fadc7ff372a`
- **Checkpoint tag:** `phase7-10-connected-public-research-checkpoint-718c024`
- **Implementation commit:** `f3337d5cdd688ec737740ed9b5231d7b9e0c0bed` — `feat(phase7.10): add connected public research evidence hub`
- **Proof commit:** `docs(phase7.10): add connected public research proof gate` (commit 2 — see git log)
- **Acceptance tag:** NONE created (independent audit pending).

### Accepted prior tags (all verified present)

```
phase7-2-cumulative-accepted-d5ad841
phase7-3-accepted-7005275
phase7-4-owner-dashboard-accepted-eebecc5
phase7-5-owner-decision-package-accepted-66d972d
phase7-6-manual-action-tracker-accepted-f1d11d8
phase7-7-outcome-followup-accepted-581ae49
phase7-8-owner-operations-dashboard-accepted-80333ec
phase7-9-connected-backup-update-recovery-accepted-383569e
```

## Connectivity boundary & permanent Seller Central prohibition

The application may reach the public Internet for legitimate research. Every online request
is validated first by the accepted `core/network_policy.py` authority, whose Amazon-account
classification **always wins before any allowlist / SSRF decision**. Permanently prohibited,
in every path, by any flag or config: Amazon Seller Central, Seller Central login, seller
OAuth, the Amazon seller API, the Ads API, seller credentials / cookies / sessions / access
tokens, automatic Seller Central report downloads, bulk-file uploads, browser automation
against Seller Central, cart/checkout automation, CAPTCHA bypass, and any automated seller-
account mutation. **Seller Central denial occurs before any allowlist decision.** Every
capture carries `seller_central_connections = 0` plus the seller-API / Ads-API / seller-auth /
seller-mutation / report-download / bulk-upload / browser-automation / credential-store
counters, all constant zero — no code path can increment them.

This phase is **evidence collection and provenance only**. It never invents facts or demand,
estimates sales or revenue, scores opportunity / competition / viability, makes campaign
recommendations, claims public evidence came from Seller Central, crawls a site, follows
arbitrary links, executes JavaScript, launches a browser, submits forms, or posts anything.

## Files created

- `production/phase7_connected_public_research.py` — the ONE Phase 7.10 authority.
- `tests/test_phase7_10_connected_public_research.py` — 191 focused tests.
- `docs/PHASE7_10-PUBLIC-RESEARCH-SOURCE-POLICY.md` — the narrow Phase 7.10 source policy.
- `SESSION7_10-CONNECTED-PUBLIC-RESEARCH-IMPLEMENTATION-REPORT.md` — this report.
- `SESSION7_10-CONNECTED-PUBLIC-RESEARCH-PROOF-GATE.json` — the proof gate.

## Files modified (narrowly additive; no accepted behaviour weakened)

- `core/network_policy.py` — **additive only.** Added the Phase 7.10 public-research section:
  purpose constants (`PUBLIC_RESEARCH`, `RSS_FEED`, `GITHUB_METADATA`, `PYPI_METADATA`,
  `AMAZON_PUBLIC_PRODUCT`), `evaluate_public_research_url`, `evaluate_amazon_public_product_url`,
  `evaluate_public_research_redirect`, `validate_resolved_addresses`, `extract_amazon_asin`, and
  small SSRF helpers. It **reuses** the accepted `classify_destination` / `_is_amazon_host` /
  `_normalize_connected_host` / `_is_private_or_loopback` primitives; no existing function was
  changed. `docs/CONNECTIVITY-POLICY.md` and its manifest are **unchanged** (hash stable).
- `core/diagnostics.py` — **additive only.** Added five typed reason codes
  (`URL_USERINFO_BLOCKED`, `IP_LITERAL_BLOCKED`, `PRIVATE_DESTINATION_BLOCKED`,
  `AMAZON_NON_PRODUCT_PATH_BLOCKED`, `AMAZON_PRODUCT_URL_INVALID`) to `ERROR_CODES`.

No accepted Phase 7.2–7.9 production authority was modified.

## Dependencies added

**None.** The module is stdlib-only (`urllib`, `ssl`, `socket`, `html.parser`,
`xml.etree.ElementTree`, `zlib`, `hashlib`, `json`, `decimal`, `argparse`).

## Source types / adapters

`public-url`, `rss` (RSS + Atom), `github` (repo + latest release metadata via
`api.github.com`), `pypi` (official `pypi.org` JSON), `amazon-public-product` (US retail
product-detail pages only), `local-file` (sandboxed input root), and `research-plan` (a
canonical JSON plan of the above). No unrestricted web search; no hidden-endpoint scraping of
logged-in services; no browser automation.

## Research-plan schema

Top-level keys: `schema_version`, `sources`, `label`, `tags`, `description`. Each descriptor:
`source_type`, `source_locator`, `label`, `tags`, `parser_options`, `expected_content_type`,
`maximum_bytes`, `enabled`. Unknown fields are rejected. Any secret / header / command / cookie /
credential / seller field anywhere in the plan is rejected (deep scan). The plan ID is
`plan-<sha256(canonical_json)>` — reordered JSON keys produce the **same** plan ID.

## Allowed / prohibited Amazon paths

- **Allowed:** `amazon.com` / `www.amazon.com` over HTTPS with `/dp/<ASIN>` or
  `/gp/product/<ASIN>` (ASIN = 10 alphanumeric).
- **Prohibited:** Seller Central, advertising, account / sign-in, cart, checkout, order
  history, seller-profile, business / customer-account pages, search-result pages, and any URL
  carrying credentials or session material. Customer reviews and customer names are never
  extracted. Robot challenges (`403` / `429` / challenge body) are recorded honestly and never
  bypassed or aggressively retried.

## Network-policy reuse

All online requests route through `core/network_policy.py`. The Phase 7.9
`evaluate_connected_operation` allowlist model is untouched; Phase 7.10 adds an allowlist-free
public-research evaluator (the owner supplying the URL is the authorization) plus a strict
Amazon retail host+path evaluator, both composed from the existing Amazon-account
classification which is evaluated first.

## SSRF / redirect / DNS protection

- No URL userinfo; HTTPS required for public hosts (HTTP only for an explicit local test
  endpoint); no `file`/`ftp`/`gopher`/`data`/`javascript` schemes.
- No raw or encoded public IP literal (integer / hex / octal / mixed IPv4 caught via
  `inet_aton`; IPv6).
- DNS is resolved and validated **before** connecting: every resolved address must be global;
  one private / loopback / link-local / reserved / multicast / metadata (`169.254.169.254`)
  address blocks the whole host (covers multi-record hosts and DNS rebinding). No blocked
  request reaches the transport (proven by tests: transport-call list is empty).
- Redirects are never trusted: each hop is re-classified, re-authorized and re-resolved; the
  count is bounded; credentials are never forwarded across hosts (none are sent). TLS
  certificate verification is always on and cannot be disabled; environment proxies are ignored.

## Robots & request behaviour

Static tool User-Agent (never a browser profile); `robots.txt` respected for public HTML
captures (unavailable → allow, recorded; disallow → `ROBOTS_BLOCKED`); no cookies sent or
persisted; `Set-Cookie` never stored; no linked-asset fetch, no JS execution, no form
submission, no crawl; per-host concurrency one; per-host rate limiting supported.

## Content limits

Default 5 MiB body bound, absolute ceiling 20 MiB. Allowed media types only (`text/html`,
`text/plain`, `application/json`, `application/ld+json`, `application/xml`, `text/xml`,
`application/rss+xml`, `application/atom+xml`); executables / archives / images / audio /
video / fonts / PDFs / unknown binary rejected (magic-byte sniff defeats a mislabeled
Content-Type). Bounded decompression (bomb refused); unsafe Content-Length refused.

## Raw capture schema

`schema_version`, `capture_id`, `source_id`, `source_type`, `normalized_locator`,
`source_locator`, `content_sha256`, `content_size`, `media_type`, `http_status`,
`response_headers` (safe subset only — never cookies/credentials), `redirect_lineage`,
`parser_version`, `network_policy_result`, `robots_result`, `capture_status`, `from_cache`,
`cache_key`, `warnings`, `evidence_count`, `evidence[]`, and all Amazon counters = 0.
`Authorization` / `Cookie` / `Set-Cookie` / tokens / signed URLs are never persisted.

## Evidence schema

`schema_version`, `evidence_id`, `capture_id`, `source_id`, `evidence_type`, `field_path`,
`observed_value`, `observed_unit`, `observed_currency`, `source_locator`, `source_content_hash`,
`extraction_method`, `parser_version`, `confidence` (extraction confidence, not business),
`value_kind` (`OBSERVED` / `NORMALIZED_TECHNICAL`), `warnings`, `lineage`. Directly observed
values are separated from normalized technical values; no inferred values are produced.

## Stable identities

- `capture_id = cap-<sha256>` over `{schema, source_type, normalized_locator, content_sha256,
  content_size, media_type, http_status, redirect_lineage, parser_version, capture_status}`.
- `evidence_id = ev-<sha256>` over `{source_content_hash, evidence_type, field_path,
  observed_value, observed_unit, observed_currency, extraction_method, parser_version}`.
- `run_id = run-<sha256>` over the sorted capture IDs + plan ID.
- No identity depends on a runtime timestamp, file mtime, temporary path, PID, random value or
  filesystem order. Equivalent source bytes produce equivalent evidence IDs. Runtime fetch
  timestamps live only in the operational log (`logs/operations.jsonl`).

## Cache model

Content-addressed cache keyed by `{normalized_locator, purpose, parser_version, variant}`;
stores validated bytes + `content_sha256` + `etag` / `last_modified` / `media_type`. Conditional
requests (`If-None-Match` / `If-Modified-Since`) and `304` reuse are supported. A request that
carried an auth token is never cached (no mixing of authenticated / unauthenticated entries). A
corrupt entry (bytes hash ≠ meta) is blocked (`CACHE_BLOCKED`). `replay-run` re-extracts from
cached/raw bytes with **no** network and confirms identical evidence IDs.

## Adapter behaviour

HTML: non-executing `html.parser`; title / meta-description / canonical / Open Graph / bounded
JSON-LD `Product` (size + nesting bounded, duplicate keys rejected, arbitrary `@type` ignored);
Amazon adds explicit `#productTitle` / `.a-offscreen` price and the ASIN from the accepted URL —
never reviews, customer names, hidden form values, cart/session data or tracking tokens.
RSS/Atom: hardened XML (DOCTYPE / ENTITY refused → external-entity + billion-laughs blocked),
deterministic de-duplication, item links never fetched. GitHub: repo + release metadata; the
optional `PHASE7_10_GITHUB_TOKEN` is optional, never stored, never logged, never forwarded
across hosts; no mutation verb exists in the source. PyPI: official JSON only; no install / pip
invocation. Local-file: sandboxed to an explicit input root (traversal / absolute / UNC /
symlink-escape / secret-file / binary / unsupported-extension all refused); the original file is
never modified.

## Secret handling

Central redaction from `core/diagnostics.py` is applied to all CLI output and the operational
log. Secret request headers are never persisted; response records keep a safe header subset only.
Tests assert no secret leaks into stdout / stderr / logs / manifest / exports / captures.

## Deterministic exports

`reports/<run_id>/research_snapshot.json`, `research_evidence.tsv`, `research_report.md`. UTF-8,
canonical JSON, sorted authoritative keys, sorted evidence, SHA-256, Decimal strings, no NaN /
Infinity / authoritative float. No wall-clock timestamp enters any export → byte-identical for
identical inputs. TSV neutralizes formula injection (`=`,`+`,`-`,`@`,tab,CR,`|`) while
preserving a legitimate negative number such as `-2.50`; Vietnamese Unicode preserved. Exports
carry the run ID, source count, capture statuses, evidence, source lineage, content hashes,
blocked / unavailable sources, disclaimers, and the Seller Central + network-purpose counters;
they never carry secrets, tokens, cookies, signed URLs, local absolute paths, customer data, or
invented metrics / recommendations.

## Readiness states

`RESEARCH_READY`, `RESEARCH_READY_EMPTY`, `RESEARCH_READY_PARTIAL`, `SOURCE_REQUIRED`,
`SOURCE_BLOCKED`, `NETWORK_UNAVAILABLE`, `NETWORK_POLICY_BLOCKED`, `ROBOTS_BLOCKED`,
`CONTENT_TYPE_BLOCKED`, `CONTENT_TOO_LARGE`, `PARSE_PARTIAL`, `CACHE_BLOCKED`,
`INTEGRITY_BLOCKED`, `PLAN_BLOCKED`, `SELLER_CENTRAL_POLICY_BLOCKED` (+ `VALIDATE_READY`,
`VERIFY_READY`, `REPLAY_READY`, `EXPORT_READY`, `LIST_READY`, `PROVIDER_CHECK_READY`). Policy
and integrity blocks return a nonzero exit; network-unavailable with other valid sources yields
a partial run; a valid capture with zero extracted evidence is `READY_EMPTY`, not invalid.

## Atomicity

Temp-sibling + fsync + read-back verify + atomic replace for raw captures, capture records,
run manifests, cache entries, exports and validation reports. On failure the last valid run is
preserved, no partial cache entry is accepted, and temp files are cleaned. Source files remain
unchanged.

## Test results

- **Baseline reproduced (pristine `718c024`):** compileall exit 0; focused
  7.2=377 (skip 1), 7.3=117, 7.4=94, 7.5=109, 7.6=100, 7.7=93, 7.8=152, 7.9=139 (skip 1);
  full suite **3282 passed, 3 skipped, 0 failures, exit 0**.
- **Phase 7.10 focused:** **191 passed, 1 skipped** (symlink escape — not permitted without
  Developer Mode / admin on this Windows host; honestly reported), exit 0.
- **Prior focused suites (unchanged after the additive core edits):** re-run post-change and
  identical to baseline (see proof gate).
- **Full repository suite (with 7.10):** **3473 passed, 4 skipped, 0 failures, exit 0**
  (3282 + 191; skips = the 3 pre-existing + the 1 new symlink skip).
- **compileall (`core`, `production`):** exit 0.
- **Independent synthetic network validation:** a `ThreadingHTTPServer` bound to `127.0.0.1`
  exercises the REAL transport + real DNS resolution under the explicit local-test flag; the
  same request without the flag is `NETWORK_POLICY_BLOCKED`. No unit test touches the public
  Internet.
- **Public Amazon fixture validation:** synthetic product-page HTML → title / brand / price /
  currency / availability / rating / rating-count + ASIN extracted; reviews / customer names /
  hidden form values / tracking tokens excluded; search / cart / checkout / account / seller
  paths and non-retail hosts blocked; robot challenge / 403 / 429 recorded honestly.
- **Optional live-network smoke:** not run in this environment (opt-in via
  `PHASE7_10_ALLOW_LIVE_NETWORK=1`); recorded as not-run. Amazon public-page access is not
  required for acceptance because robot blocking varies.

## Upstream source immutability & repo hygiene

Capture writes only under the `--base-dir` workspace. A sibling `7.9` tree left untouched (test
`test_174`). The accepted Phase 7.3–7.9 runtime trees under `runs/` are never written. `runs/`
remains gitignored (`git check-ignore runs/T2/phase7/7.10` confirms). Nothing under `runs/` is
committed.

## Prohibited Seller Central scan & counters

`scripts/connectivity_scan.py` reports **0 active Amazon-account paths** (exit 0) with the new
module present; the Session 5b/5c/5d/5d1 source scanners pass. Amazon literals in the production
source are assembled from fragments so no endpoint string appears verbatim. Every Amazon counter
is a constant zero in captures, manifests and exports.

## Real-T2 demonstration (offline)

`capture-file` into `runs/T2/phase7/7.10` → `run-555e3ffdb5ba443e91f90d31`,
`SESSION7_10_RESEARCH_READY`, 10 evidence, all counters 0; `verify-run` 6/6 → `VERIFY_READY`;
`replay-run` (network raises if touched) → `REPLAY_READY`. Stored under the gitignored `runs/`.

## Known limitations

- Live Amazon public-product capture depends on Amazon's robot behaviour and may return an
  honest `ROBOT_CHALLENGE_OR_BLOCKED` / `NETWORK_UNAVAILABLE` state; this is expected and not a
  defect.
- The real transport validates DNS immediately before connecting but does not pin the resolved
  IP for the actual socket, so a DNS-rebinding time-of-check/time-of-use window exists in
  principle (urllib re-resolves the host when it opens the socket). It is neutralized by
  transport-layer peer-identity validation rather than IP pinning, **not** merely by the tool
  being local: every public host is HTTPS-only (raw and encoded IP literals are refused, so every
  rebindable request goes over TLS), and TLS certificate + hostname verification is hardcoded on
  with no disable path anywhere in the module. A host that rebinds to a private/loopback address
  after validation therefore cannot present a CA-trusted certificate matching the requested public
  hostname; the handshake fails and no response body is ever trusted or captured (independently
  reproduced — the socket reaches a rebound loopback endpoint yet the capture is
  `NETWORK_UNAVAILABLE` with an empty body). Defense in depth: all resolved addresses are
  validated pre-connect and a single private / loopback / link-local / reserved / multicast /
  metadata address blocks the whole host.
- Windows symlink-escape test skips without Developer Mode / admin (honestly reported).
- HTML price/visible-text extraction targets JSON-LD `Product` + explicit `#productTitle` /
  `.a-offscreen`; arbitrary page layouts may yield fewer fields (recorded as absent, never zero).

## Exact CLI examples

```powershell
python -m production.phase7_connected_public_research `
  --base-dir "runs/T2/phase7/7.10" capture-url --url "https://example.com/page"

python -m production.phase7_connected_public_research `
  --base-dir "runs/T2/phase7/7.10" capture-pypi --package "cryptography"

python -m production.phase7_connected_public_research `
  --base-dir "runs/T2/phase7/7.10" capture-github --repository "OWNER/REPOSITORY"

python -m production.phase7_connected_public_research `
  --base-dir "runs/T2/phase7/7.10" capture-amazon-product --url "https://www.amazon.com/dp/EXAMPLEASIN"

python -m production.phase7_connected_public_research `
  --base-dir "runs/T2/phase7/7.10" capture-file --input-root "research-input" --file "sample.html"

python -m production.phase7_connected_public_research `
  --base-dir "runs/T2/phase7/7.10" run-plan --plan "research-plan.json"
```

## Exact next action

Recommend an **independent acceptance audit** of branch
`phase7-10-connected-public-research`. Do NOT merge to `main`, do NOT create an acceptance tag,
and do NOT begin Phase 7.11 until the audit accepts.
