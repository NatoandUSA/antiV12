# Phase 7.10 — Public Research Source Policy

This document narrows the already-adopted connectivity policy
(`docs/CONNECTIVITY-POLICY.md`, unchanged) for the **Connected Public Research &
Evidence Hub** (`production/phase7_connected_public_research.py`). It records the
source types, the request behaviour, and the hard limits. It does **not** create a
new connectivity policy and does **not** modify the accepted connectivity manifest.

## Permanent Amazon-account boundary (unchanged, evaluated first)

Every online request is validated by `core/network_policy.py`, whose Amazon-account
classification always wins **before** any allowlist / SSRF decision. Permanently
prohibited, in every path, by any flag or config:

- Amazon Seller Central, Seller Central login, seller OAuth;
- the Amazon seller API and the Ads API;
- seller-account credentials, cookies, sessions, access tokens;
- automatic Seller Central report downloads and bulk-file uploads;
- browser automation against Seller Central; cart / checkout automation; CAPTCHA bypass;
- any automated mutation of campaigns, bids, budgets, targets, keywords, negatives,
  listings or seller-account settings.

Every capture carries `seller_central_connections = 0` and the seller-API / Ads-API /
seller-auth / seller-mutation counters, all constant zero. No code path can increment them.

## Supported source types

| Source type | Locator | Host | Notes |
|---|---|---|---|
| `public-url` | an explicit HTTPS URL | owner-supplied | one page; no crawl |
| `rss` | an explicit RSS/Atom feed URL | owner-supplied | hardened XML; item links never fetched |
| `github` | `OWNER/REPOSITORY` | `api.github.com` (tool-constructed) | repo + latest release metadata |
| `pypi` | package name | `pypi.org` (tool-constructed) | official JSON metadata endpoint |
| `amazon-public-product` | a `/dp/<ASIN>` or `/gp/product/<ASIN>` URL, or a bare ASIN | `amazon.com` / `www.amazon.com` only | product-detail pages only |
| `local-file` | a relative path under an explicit input root | — | manually saved HTML/JSON/XML/text |
| `research-plan` | a canonical JSON plan of the above descriptors | — | plan ID is canonical-JSON-addressed |

## Amazon US public-product scope

Allowed hosts: `amazon.com`, `www.amazon.com`. Allowed paths: `/dp/<ASIN>`,
`/gp/product/<ASIN>` (ASIN = 10 alphanumeric characters). **Rejected:** Seller Central,
advertising, account/sign-in, cart, checkout, order history, seller-profile, business /
customer-account pages, search-result pages, and any URL carrying credentials or session
material. Customer-review bodies and customer names are never extracted. Robot challenges
are recorded as an honest unavailable/challenged state and never bypassed.

## SSRF & redirect defence

- HTTPS required for public hosts; HTTP only for an explicit local test endpoint (`--allow-local-test-server`).
- No URL userinfo; no `file`/`ftp`/`gopher`/`data`/`javascript` schemes.
- No raw or encoded public IP literal (integer / hex / octal / mixed IPv4, IPv6).
- DNS resolution is validated **before** connecting: every resolved address must be a
  genuinely public (global) address; a single private, loopback, link-local, reserved,
  multicast or cloud-metadata (`169.254.169.254`) address blocks the whole host (covers
  multi-record hosts and DNS rebinding).
- Redirects are never auto-trusted: each hop is re-classified, re-authorized and re-resolved;
  the redirect count is bounded; credentials are never forwarded across hosts (none are sent).
- TLS certificate verification is always on and cannot be disabled. Environment proxies are ignored.

## Request behaviour

- One clear static User-Agent identifying the tool (never a browser profile).
- `robots.txt` is respected for public HTML captures; an unavailable robots file is treated
  as allow and recorded honestly; a disallow is a `ROBOTS_BLOCKED` state.
- No cookies are sent or persisted; `Set-Cookie` is never stored.
- No linked assets (images, scripts, fonts, ads) are fetched; no JavaScript is executed; no
  browser is launched; no form is submitted; no site is crawled; nothing is posted.
- Per-host concurrency is one and per-host rate limiting is supported.

## Content limits

- Default body bound 5 MiB; absolute ceiling 20 MiB.
- Allowed media types: `text/html`, `text/plain`, `application/json`, `application/ld+json`,
  `application/xml`, `text/xml`, `application/rss+xml`, `application/atom+xml`.
- Executables, archives, images, audio, video, fonts, PDFs and unknown binary data are rejected.
- Decompression is bounded (a decompression bomb is refused); an unsafe Content-Length is refused.

## What this phase never does

Evidence collection and provenance only. It never invents facts or demand, estimates sales or
revenue, scores opportunity / competition / viability, makes campaign recommendations, claims
public evidence came from Seller Central, or mutates any external service. Analysis and
recommendation logic remain outside Phase 7.10.

## Determinism

UTF-8, canonical JSON, sorted authoritative keys, SHA-256 identities, Decimal strings for
numeric authority, no NaN / Infinity / authoritative binary float. Capture and evidence
identities depend only on content + provenance — never on a runtime timestamp, file mtime,
temporary path, process ID, random value or filesystem order. Runtime fetch timestamps are
segregated to the operational log and never enter an identity or an export.
