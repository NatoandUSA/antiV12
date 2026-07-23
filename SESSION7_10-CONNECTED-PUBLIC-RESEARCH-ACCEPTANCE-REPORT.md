# SESSION 7.10 — Connected Public Research & Evidence Hub — Independent Acceptance Audit

**Auditor role:** independent acceptance auditor. Every material claim was reproduced from
repository bytes and independent fixtures; the implementation report, proof gate, and self-claimed
totals were not trusted. No production code was modified for acceptance. Not merged to `main`.

**Decision:** `PHASE7_10_CONNECTED_PUBLIC_RESEARCH_ACCEPTED_WITH_DOCUMENTATION_FIX`

- Branch: `phase7-10-connected-public-research`
- Baseline (`main`/`origin/main`): `718c024d2c0b7efc3a9817e6c38c2fadc7ff372a` (unchanged)
- Implementation commit: `f3337d5cdd688ec737740ed9b5231d7b9e0c0bed`
- Proof commit / feature HEAD (pre-acceptance): `329c219474e95a57b5f304d646e108ebcab066f5`
- Environment: Windows 11, Python 3.12.10, OpenSSL 3.0.16, cryptography 49.0.0.

The single documentation correction (below, finding 11/51) touches only the implementation report;
no production/policy/test byte was changed.

---

## 1. Git provenance
- Current branch `phase7-10-connected-public-research`; working tree clean before and after audit.
- Local HEAD `329c219…`; `origin/phase7-10-connected-public-research` HEAD `329c219…` (equal).
- `main` and `origin/main` both `718c024…` (baseline, unchanged).
- Checkpoint tag `phase7-10-connected-public-research-checkpoint-718c024` → `718c024…` (points exactly at baseline).
- All eight prior accepted tags resolve to their expected commits (d5ad841, 7005275, eebecc5,
  66d972d, f1d11d8, 581ae49, 80333ec, 383569e) — none moved.
- No Phase 7.10 acceptance tag existed before this audit. No history rewrite/amend/rebase:
  `f3337d5`'s parent is `718c024`; `329c219`'s parent is `f3337d5`.
- **PASS.**

## 2. Implementation diff (`718c024 → f3337d5`)
- 5 files, **4050 insertions, 0 deletions** (purely additive): `core/diagnostics.py` (+12),
  `core/network_policy.py` (+276), `docs/PHASE7_10-PUBLIC-RESEARCH-SOURCE-POLICY.md` (+91, new),
  `production/phase7_connected_public_research.py` (+2232, new),
  `tests/test_phase7_10_connected_public_research.py` (+1439, new).
- No accepted Phase 7.2–7.9 business authority modified. Recorded source SHA-256 hashes in the
  proof gate match the actual bytes for all five source files. **PASS.**

## 3. Proof diff (`f3337d5 → 329c219`)
- 2 new files only: the implementation report and the proof gate JSON (394 insertions). **PASS.**

## 4. Shared network-policy changes (`core/network_policy.py`)
- Additive only: new purpose constants, `evaluate_public_research_url`,
  `evaluate_amazon_public_product_url`, `evaluate_public_research_redirect`,
  `validate_resolved_addresses`, `extract_amazon_asin`, and small SSRF helpers. No existing
  Phase 7.2–7.9 evaluator changed; no duplicate/competing authority created. Both new evaluators
  call the accepted `classify_destination` **first**, so the Amazon-account boundary wins before
  any allowlist/SSRF decision. No existing denial rule weakened. **PASS.**

## 5. Shared diagnostics changes (`core/diagnostics.py`)
- Five new typed reason codes appended to `ERROR_CODES` (`URL_USERINFO_BLOCKED`,
  `IP_LITERAL_BLOCKED`, `PRIVATE_DESTINATION_BLOCKED`, `AMAZON_NON_PRODUCT_PATH_BLOCKED`,
  `AMAZON_PRODUCT_URL_INVALID`). Redaction (`redact_secrets`) is unchanged — deliberately
  conservative (env-set secret values + key-shaped tokens only); ordinary words such as "token",
  "key", "cookie" are **not** over-redacted (independently verified). **PASS.**

## 6. Connectivity boundary
- `docs/CONNECTIVITY-POLICY.md` and its manifest are unchanged (not in the diff). The narrow
  source policy layers on top; the permanent Amazon-account boundary is stated and enforced. **PASS.**

## 7. Seller Central deny-first behavior
- Independent matrix (harness): `sellercentral.amazon.com`, regional `.co.uk`/`.de`, subdomains,
  mixed-case, trailing-dot, deceptive prefix/suffix, seller API, Ads API, seller OAuth, sign-in,
  account, cart, checkout — **all denied**; the true Seller-Central/API/OAuth hosts return the
  Amazon-account reason codes. `_fetch` on a Seller Central URL returns `SELLER_CENTRAL_POLICY_BLOCKED`
  with **zero transport calls** (transport never reached). Denial precedes scheme/allowlist checks. **PASS.**

## 8. Amazon public-product boundary
- Allowed hosts strictly `amazon.com` / `www.amazon.com`; allowed paths strictly `/dp/<ASIN>` and
  `/gp/product/<ASIN>` (10-char ASIN, upper-cased). Rejected: search (`/s?k=`), cart, checkout,
  order-history, sign-in, account, seller profile (`/sp`), product-reviews, short/long ASIN,
  `/dp/…/../ap/signin`, non-retail hosts (`amazon.co.uk`, `smile.`). Seller Central classified
  first even through the product evaluator. Extraction pulls title/ASIN/brand/price/availability/
  rating only — **no customer-review body or customer name** (verified with a review-laden fixture:
  `JohnCustomerName` and review body excluded). No auth/cookies; 403/429/robot-challenge recorded
  honestly (`ROBOT_CHALLENGE_OR_BLOCKED`), no retry, no crawl. **PASS.**

## 9. General network policy
- HTTPS required for public hosts; `http`/`file`/`ftp`/`gopher`/`data`/`javascript` all denied;
  URL userinfo denied. Environment proxies ignored (`ProxyHandler({})`); TLS verification always on
  and not disableable; redirects/retries/timeout/body all bounded (5 redirects, 2 retries on
  timeout only, 20 s timeout, 5 MiB default / 20 MiB ceiling). Every online adapter routes through
  `_fetch` → `core/network_policy`. **PASS.**

## 10. SSRF protection
- Hostnames resolving to loopback (v4/v6), private (10/172/192, IPv6 ULA), link-local, cloud
  metadata `169.254.169.254`, multicast, and unspecified are all blocked at DNS validation with
  **no transport call**. Raw/encoded IP literals — integer (`2130706433`), hex (`0x7f000001`),
  octal (`017700000001`), short (`127.1`), dotted loopback, public IP literal, IPv6 `[::1]` — all
  refused at policy. Unicode/IDNA-confusable host refused. `validate_resolved_addresses` blocks the
  whole host when any one of multiple records is disallowed; `allow_local` permits loopback only,
  still blocking link-local/metadata. **PASS.**

## 11. DNS rebinding and TOCTOU analysis (critical gate)
- The residual TOCTOU is real and disclosed: `_fetch` validates DNS via `config.resolver`, then
  `_real_transport` hands the **hostname URL** to urllib, which re-resolves independently — the IP
  is not pinned. I reproduced the rebind end-to-end: a stateful validation resolver returned a
  public address (policy passed) while the socket layer's `socket.getaddrinfo` was patched to
  rebind `evil.example` to a loopback HTTPS server presenting a self-signed cert for `evil.example`.
  Result: the socket **physically connected to the rebound loopback endpoint** (getaddrinfo called
  twice at the socket layer, validation resolver called once) **yet the capture was
  `NETWORK_UNAVAILABLE` with an empty body and the server's `SECRET-INTERNAL-DATA` never appeared** —
  because TLS certificate + hostname verification (`check_hostname=True`, `CERT_REQUIRED`, default
  trust store) rejected the untrusted/mismatched peer. `_real_transport` exposes no
  context/cafile/verify parameter; the module contains no `CERT_NONE`, `_create_unverified_context`,
  or `verify=False`.
- Because every public host is HTTPS-only and IP literals are refused, **every** rebindable request
  is forced through TLS; a private/internal destination cannot present a CA-trusted cert for the
  attacker's public hostname, so no rebound response is ever trusted. This satisfies the acceptance
  criterion "the connected peer address is independently validated before response data is trusted
  / equivalent protection demonstrably prevents rebinding." **Not a practical bypass — PASS.**
- **Documentation fix (applied):** the implementation report's Known-Limitations bullet previously
  justified this safety mainly as "not practically exploitable for a local single-operator tool"
  (the reasoning this audit is told not to accept) and attributed rebinding coverage to DNS
  validation. I corrected it to state the true mechanism (mandatory TLS peer-identity verification;
  HTTPS-only + IP-literal ban forces TLS on every rebindable request). Doc-only; no code changed.

## 12. Redirect protection
- Each hop re-classified, re-authorized and re-resolved. Redirect to Seller Central or a
  private-resolving host is blocked and the target is **never fetched**; a public→public redirect is
  followed; the redirect count is bounded (`too_many_redirects`); a redirect without `Location` is
  handled. Amazon-product captures cannot be redirected off the retail host. **PASS.**

## 13. Credential forwarding
- The tool sends no cookies. The optional GitHub bearer token is present on the first-hop request
  but **dropped on a cross-host redirect** (verified: absent from the second-hop headers, present on
  the same-host first hop). No credential can cross a host. **PASS.**

## 14. TLS behavior
- `ssl.create_default_context()` with `check_hostname=True`, `verify_mode=CERT_REQUIRED`; no
  arbitrary/unverified SSL context, no disable switch, no `verify=False` anywhere in the module. **PASS.**

## 15. Proxy behavior
- `ProxyHandler({})` disables implicit environment-proxy inheritance; no explicit proxy is
  configured. **PASS.**

## 16. Robots behavior
- `robots.txt` consulted for public HTML surfaces (public-url, rss, amazon-product) via the same
  policy-validated `_fetch`; disallow → `ROBOTS_BLOCKED` and the page is **not** fetched; a 404/
  unavailable robots file → allow, recorded as `ROBOTS_UNAVAILABLE_ALLOWED`; a disallow of a
  different path leaves our path allowed. GitHub/PyPI JSON APIs legitimately skip robots. **PASS.**

## 17. Rate limiting
- `HostGate` serializes per host (concurrency == 1 observed) and, with an injected clock+sleeper,
  spaces successive same-host requests by `min_interval` (2.0 s spacing observed). **PASS.**

## 18. Response-size limits
- Default 5 MiB, absolute ceiling 20 MiB (`Config` clamps `max_bytes` to the ceiling). Oversized
  raw body → `CONTENT_TOO_LARGE`; a declared Content-Length materially smaller than the body →
  `content_length_mismatch`. **PASS.**

## 19. Decompression safety
- Bounded gzip/deflate; a ~2 MiB→zero-fill bomb against a 1 KiB limit → `CONTENT_TOO_LARGE`; a
  corrupt gzip stream → handled (`TransportError`, no capture); a valid small gzip decodes. **PASS.**

## 20. Research-plan schema
- Canonical plan → stable `plan-<sha256>`; reordered keys and reordered tag lists produce the same
  ID. Unknown top-level/descriptor fields rejected; any secret/header/command/cookie/credential/
  auth/proxy/seller field anywhere rejected by deep scan; `maximum_bytes` above the 20 MiB ceiling
  rejected; unknown source type rejected. **PASS.**

## 21. Raw capture model
- `capture_id = cap-<sha256>` over `{schema, source_type, normalized_locator, content_sha256,
  content_size, media_type, http_status, redirect_lineage, parser_version, capture_status}` — no
  operational timestamp/mtime/path/PID/random. Only a safe response-header subset is stored (no
  `authorization`/`cookie`/`set-cookie`, verified absent). Constant-zero Amazon counters present. **PASS.**

## 22. Evidence model
- `evidence_id = ev-<sha256>` over value + provenance (no order/timestamp). `value_kind`
  separates `OBSERVED` from `NORMALIZED_TECHNICAL`. No demand/sales/revenue/conversion/search-volume/
  inventory/opportunity/competitor/viability/bid/budget/keyword/target/negative value is computed;
  an absent field stays absent (a price-less product yields **no** price evidence, never zero). **PASS.**

## 23. HTML parser
- Non-executing `html.parser`; scripts read inert. Title, meta description, canonical, Open Graph,
  and bounded JSON-LD Product hooks only; no form values / tracking / customer data extracted. **PASS.**

## 24. JSON-LD parser
- Bounded blocks (24), bytes (256 KiB), depth (20); oversized/deep blocks skipped; duplicate JSON
  keys rejected; arbitrary `@type` ignored (only `Product`); a real Product's
  name/brand/price/currency/availability/rating/count read. **PASS.**

## 25. RSS/Atom parser
- A `<!DOCTYPE>`/`<!ENTITY>` declaration is refused before parsing → external-entity (XXE) and
  billion-laughs payloads blocked (both reproduced → `None`/PARSE_PARTIAL). Deterministic item
  de-duplication (duplicate GUID collapses to one). Item links are recorded as evidence only, never
  auto-fetched. **PASS.**

## 26. GitHub adapter
- Public `api.github.com` repo + latest-release metadata only. Optional token used on the request
  but **never persisted/logged/cached and dropped cross-host** (token value absent from every
  artifact under the workspace; authed request produced zero cache files). No mutation verb exists. **PASS.**

## 27. PyPI adapter
- Official `pypi.org/pypi/<pkg>/json` only; package name validated/normalized; project URLs are
  evidence, not fetched; release SHA-256/upload-time/yanked read. No pip/subprocess/install. **PASS.**

## 28. Amazon product adapter
- Covered in finding 8: strict host+path, product metadata only, no reviews/customer names, honest
  challenge handling, no auth/cookies/JS/browser. **PASS.**

## 29. Local-file adapter
- Sandboxed to an explicit `input_root` via `realpath` + `commonpath`. Blocked: `../` traversal,
  absolute paths, UNC (`\\`, `//`), drive (`C:\`), URL-encoded traversal, forbidden names
  (`.env`), forbidden substrings (`session`), unsupported extensions, binary content in an allowed
  extension. The original file is byte-identical after read. **PASS.**

## 30. Cache integrity
- Content-addressed key includes normalized locator + purpose + parser version. Corrupt bytes
  (hash≠meta) → `CACHE_BLOCKED`; corrupt meta JSON → `CACHE_BLOCKED`; a request carrying an auth
  header is never cached; ETag/Last-Modified/304 conditional reuse supported; a valid roundtrip
  restores exact bytes. No corrupt cache is silently accepted. **PASS.**

## 31. Offline replay
- `replay-run` re-extracts evidence from stored raw bytes with a resolver **and** transport that
  raise if touched → `REPLAY_READY` with identical evidence IDs; proves offline reproducibility. **PASS.**

## 32. Stable identities
- `plan_id`/`source_id`/`capture_id`/`evidence_id`/`run_id` are all content-addressed and exclude
  timestamp/mtime/tmp-path/PID/random/JSON-key-order. Equivalent bytes → equivalent IDs. **PASS.**

## 33. Determinism
- Two full local-file runs under different fake clocks (2001 vs 2099), different file mtimes, and
  different base/tmp directories produced an identical `run_id` and **byte-identical**
  `research_snapshot.json` / `research_evidence.tsv` / `research_report.md`. Canonical JSON rejects
  NaN/Infinity; numeric authority uses Decimal strings. **PASS.**

## 34. JSON export
- `research_snapshot.json` deterministic, sorted, lineage + hashes + capture states + disclaimers +
  network-purpose counters + zero Amazon counters; no secrets/paths/invented metrics. **PASS.**

## 35. TSV export
- Formula injection neutralized for leading `=`,`+`,`-`(non-numeric),`@`,`|`; a legitimate negative
  number `-2.50` and positive `19.99` preserved; tab/CR/LF stripped to keep columns aligned;
  Vietnamese Unicode (`Xuất khẩu`) preserved. **PASS.**

## 36. Markdown export
- `research_report.md` deterministic; carries run/plan IDs, capture statuses, blocked/unavailable
  captures, evidence, disclaimers, and the Amazon-account counter block. **PASS.**

## 37. Secret redaction
- CLI output and the operational log route through `redact()`/`redact_secrets`. `Bearer …` and
  `sk-ant-…` shapes redacted; ordinary text containing "token"/"key"/"cookie" is **not**
  over-redacted. The GitHub token never appears in any capture/manifest/export/log. **PASS.**

## 38. Atomicity
- Temp-sibling + fsync + read-back-verify + `os.replace`. After runs, no `.tmp-710-*.part`
  leftovers; a forced readback failure preserves the last valid artifact; upstream inputs unchanged. **PASS.**

## 39. Validate-only
- `validate-only` performs 0 network requests, writes 0 files, and does **not** create the base
  directory; returns `VALIDATE_READY` for a valid plan and raises `PLAN_BLOCKED` for a forbidden/
  out-of-range plan. **PASS.**

## 40. Upstream immutability
- Writes are scoped under `--base-dir`; a sibling file and the read-only `input_root` file are
  byte-identical before/after captures. The tool never writes to the Phase 7.3–7.9 trees. The
  primary `runs/` tree was not modified by the audit (all harness runs used temp dirs). **PASS.**

## 41. Seller Central counters
- `seller_central_connections` and the seller-API/Ads-API/seller-auth/seller-mutation/report-
  download/bulk-upload/browser-automation/credential-store counters are constant zero in captures,
  manifests, and snapshots. No code path increments them. **PASS.**

## 42. Prohibited integration scan
- `scripts/connectivity_scan.py`: 91 files, **0 active Amazon-account paths, exit 0**; the 7.10
  module's 8 findings are all `REVIEW_REQUIRED` (outbound primitives), none prohibited. Session
  5b/5c/5d regression/certification modules pass (2/13/47, exit 0). Independent grep of the 7.10
  module found no `eval`/`exec`/`subprocess`/`os.system`/`shell=True`/`selenium`/`playwright`/
  `webdriver`/`webbrowser`/`pip`; the only verbatim `amazon.com` is CLI help text, not a functional
  endpoint (real hosts are fragment-assembled). *(Note: `CONNECTED-RESEARCH-NETWORK-SCAN.json` is a
  pre-existing tracked artifact last written at `ac26e9a`; re-running the scan regenerates it — I
  restored it, tree remains clean. Its staleness is out of scope for 7.10.)* **PASS.**

## 43. Compile result
- `python -m compileall core production tests` → exit 0. **PASS.**

## 44. Phase 7.10 focused tests
- `python -m unittest tests.test_phase7_10_connected_public_research` → **Ran 191, skipped 1
  (Windows symlink escape, honestly reported), OK, exit 0.** Matches the claim. **PASS.**

## 45. Prior focused tests
- 7.2 = 377 (skip 1); 7.3 = 117; 7.4 = 94; 7.5 = 109; 7.6 = 100; 7.7 = 93; 7.8 = 152;
  7.9 = 139 (skip 1). All exit 0, all matching the proof gate. **PASS.**

## 46. Full suite
- `python -m unittest` over all 80 test modules → **Ran 3473, skipped 4, OK, exit 0** (0 FAIL/ERROR
  markers). Matches the claim exactly. **PASS.**

## 47. Independent harnesses (author-written, not the project's tests)
- SSRF/deny + Amazon-boundary: 68 pass (4 discarded checks were my own robots-consumption harness
  bugs, superseded by the redirect harness). Redirect: 8/8. TOCTOU/TLS: 16/16. Parser/content/cache/
  local-file/determinism/redaction/TSV: 56/56. Integration (token/Amazon/validate-only/counters/
  verify-replay/atomicity/upstream): 26/26. Robots/media/rate-limit: 23/23. **Total 197 independent
  checks, 0 genuine failures.** **PASS.**

## 48. Fresh worktree
- Detached worktree at `329c219`; `runs/` absent; compileall exit 0; 7.10 focused = 191 (skip 1),
  exit 0; 7.8 = 152 and 7.9 = 137 (skip 2 — the honest RealT2 skip when `runs/` is absent) pass.
  The SSRF/redirect/TOCTOU/data/integration harnesses reproduce identically from the worktree bytes.
  No dependency on local `runs/` data. Worktree removed; no `git clean` run against the primary. **PASS.**

## 49. runs/ tracking
- `runs/` is gitignored; `git ls-files runs/` is empty; nothing under `runs/` is tracked or
  committed on this branch. **PASS.**

## 50. Optional live-network result
- **NOT_RUN.** No opt-in (`PHASE7_10_ALLOW_LIVE_NETWORK`) was set; no public target was contacted;
  no Seller Central or Amazon retail request was made. Honestly recorded. **PASS.**

## 51. Documentation accuracy
- The report/proof-gate/source-policy accurately describe branch, baseline, commits, checkpoint,
  files created/modified, zero dependencies, adapters, plan schema, network-policy reuse, deny-first
  behavior, the Amazon public-product boundary, redirects, robots, rate limiting, content limits,
  cache, parser behavior, identities, exports, redaction, atomicity, validate-only, test totals,
  upstream immutability, runs tracking, scanner results, and zero counters — **all reproduced.**
- One correction applied (finding 11): the residual DNS-TOCTOU safety rationale was materially
  incomplete (led with "local single-operator tool"; attributed rebinding coverage to DNS
  validation). Corrected to name the true mechanism (mandatory TLS peer-identity verification).
  The proof-gate flags `dns_rebinding_blocked: true` and the source-policy "covers … DNS rebinding"
  are true in outcome; the mechanism attribution is clarified in the report. Doc-only.

## 52. Known limitations (accepted, non-blocking)
- Residual DNS-TOCTOU (IP not pinned) — neutralized by mandatory TLS peer verification (finding 11);
  not practically exploitable.
- Live Amazon public-product capture depends on Amazon's robot behaviour (honest
  `ROBOT_CHALLENGE_OR_BLOCKED`/`NETWORK_UNAVAILABLE`).
- Windows symlink-escape test skips without Developer Mode/admin (honestly reported).
- HTML extraction targets JSON-LD Product + explicit hooks; sparse layouts yield fewer fields
  (recorded absent, never zero).
- Pre-existing stale tracked scanner artifact `CONNECTED-RESEARCH-NETWORK-SCAN.json` (out of 7.10
  scope; regenerable; restored).

## 53. Final decision
`PHASE7_10_CONNECTED_PUBLIC_RESEARCH_ACCEPTED_WITH_DOCUMENTATION_FIX`. No blocking defect: Seller
Central and account-scoped Amazon are unreachable (deny-first, transport never reached); the
public-product allowance is narrow and cannot bypass deny-first; redirects are re-authorized and
credential-free; the residual DNS-rebinding TOCTOU is not a practical bypass (TLS peer validation);
private/metadata destinations are unreachable; no cookies/tokens are persisted; robots are honoured;
oversized/decompression-bomb content is refused; XML entities and scripts do not execute; local-file
extraction cannot escape its root; corrupt caches are rejected; evidence/exports invent no business
facts; no secrets appear in output; Phase 7.3–7.9 data is unchanged; the full suite and fresh
worktree pass. The only change made for acceptance is a doc-only clarification plus this report.

## 54. Exact next action
Create the single acceptance commit (this report + the doc-only report correction) and the annotated
tag `phase7-10-connected-public-research-accepted-<hash>`; push the feature branch and tag. Do **not**
merge to `main`. Do **not** begin Phase 7.11. Phase 7.10 then awaits the owner's decision to merge.
