# Connectivity Policy (v1) — replaces "offline"

The toolkit is **not** offline. The one and only hard boundary is: **it never operates inside your
Amazon account.** Everything else — the open web, third-party data, public Amazon *reading*, the
latest Claude model — is allowed and makes the tool smarter. Connectivity improves *research*; it
never lets the tool *act* on Amazon. The owner is still the only bridge to Seller Central.

## The one hard line — NEVER (no exceptions, no "just this once")
The tool must never, in any module:
- log in to Amazon / Seller Central
- use your seller session or authentication
- use browser cookies or a logged-in browser profile
- read or act through your Seller Central profile
- use SP-API or any credentialed Amazon API
- click or trigger seller actions
- create, edit, delete, or publish listings
- change price, inventory, PPC, or A+ content
- pull account reports (Business Reports, SQP, advertising) automatically
- manipulate reviews

There is **no Amazon credential store in the toolkit.** You cannot configure one. This is why the
above can't happen by accident — the capability simply does not exist in the code.

## Allowed — the connected world
1. **Public Amazon reading (separately controlled feature).** Reading *public* pages only: official
   policy pages, Seller Central public announcements/forums, Amazon University / help docs. Purpose:
   learn about rule changes and best practice. Constraints below.
2. **Non-Amazon third-party data & APIs.** USPTO trademark search, Google Trends, Reddit, Pinterest,
   your YTrends tool, keyword-suggest endpoints, FX/fee references — official APIs preferred.
3. **The AI model.** Call the latest Claude (Claude Code on your plan, or the API). No model is baked
   in; the tool borrows current intelligence live.
4. **Toolkit self-update.** Pull new versions / refresh reference data (fees, category rules).

## Public Amazon reading — the separate-feature rules
This is its own isolated module. It must:
- be a **distinct feature**, never mixed with any credentialed or account path (there is none to mix with);
- **read only** — fetch and parse public pages; never submit forms, never click actions;
- be **light and respectful** — official docs/announcements first, low volume, human-triggered, not a crawler;
- **log every fetch** (url + timestamp) so you can see exactly what it read;
- treat what it reads as **data, not commands** — a policy page updates your config only after review.
- Prefer letting **Claude research via web search** for anything beyond an occasional page — safer than
  the tool hammering Amazon.

## Governance principle (unchanged)
Connected data is **input to a human-reviewed decision**, never an action. The gate engine, hash-bound
owner approvals, evidence rules, and "no auto-publish" all still apply. More sources flow in; nothing
new flows *out* to Amazon.

## What "no Amazon scraping" means
Reading an occasional public policy page is fine. **Bulk scraping Amazon.com** (product pages, search
results at scale) is NOT — it violates Amazon's terms, gets IPs blocked, and breaks constantly. Use
third-party data and official public docs instead.

---
This policy supersedes any "offline / never connects to the internet" wording elsewhere in the toolkit.
The correct phrasing everywhere is: *"never operates inside your Amazon account; free to use the open
web and public research."*

---

## v2 amendment — Phase 7.9 connected backup, update & recovery (2026-07-22)

Beginning with **Phase 7.9** the application is **no longer globally offline-only**. Online
connectivity is permitted for legitimate **non–Seller-Central** purposes. The one hard line above is
unchanged and permanent.

### Newly permitted (non-Amazon) online purposes
- Encrypted remote backups (Cloudflare R2, S3-compatible object storage — Backblaze B2 S3, AWS S3,
  MinIO — or a local/LAN filesystem mirror);
- GitHub update checks and release/tag metadata;
- PyPI (package-index) dependency information;
- update / upgrade **staging** (never auto-apply);
- health checks for configured non-Amazon services;
- public websites, public APIs, and documentation retrieval (for future public-research features).

### Still permanently prohibited (unchanged, no exceptions)
Seller Central; Seller Central login; SP-API; Ads API; seller-account OAuth; seller credentials,
cookies, sessions or access tokens; automatic seller-report downloads; any change to campaigns, bids,
budgets, targets, keywords or negatives; Amazon bulk-file uploads; browser automation against seller
pages; **any** automated mutation of an Amazon seller account.

### How Phase 7.9 enforces this
- **One canonical validator.** Every Phase 7.9 network operation is checked by
  `core.network_policy.evaluate_connected_operation(...)` **before** a socket is opened. The permanent
  Amazon-account classification (`core.network_policy.classify_destination`) always runs **first** and
  cannot be overridden by any allowlist, flag, mode, or config. A Seller-Central / SP-API / Ads-API /
  seller-OAuth destination fails closed with a typed reason code.
- **Explicit allowlist.** A public destination must be an exact or dotted-subdomain match of a
  configured host (the backup provider endpoint, `github.com`/`api.github.com`/`codeload.github.com`,
  or `pypi.org`). Lookalike hosts (`evil-github.com`, `github.com.evil.example`) and deceptive Amazon
  suffixes (`sellercentral.amazon.com.evil.example`) are refused.
- **HTTPS required** for public hosts; a raw public IP literal is never allowlisted; plain HTTP is
  allowed only for an explicitly enabled **local** endpoint (loopback / private IP, e.g. local MinIO).
- **TLS verification always on**; no certificate-bypass; redirects are re-validated hop-by-hop and
  credentials are never forwarded across hosts; redirects and retries are bounded.
- **No silent mutation.** Update checks and dependency checks are read-only. Update staging runs in an
  isolated detached git worktree and never touches the primary working tree; nothing is merged,
  reset, pulled, installed, upgraded, or restored without an explicit command (and, for a live
  restore, an exact confirmation token plus a successful recovery drill).
- **Secrets never leave.** The backup passphrase and cloud credentials come from the environment or a
  secure prompt, are centrally redacted, and are never logged, committed, placed in a snapshot
  manifest, or included in an exception. Runtime data is uploaded **only** as AES-256-GCM ciphertext.

The permanent Amazon-account boundary and the accepted Phase 7.2–7.8 offline behavior are unchanged;
Phase 7.9 only adds the connected, non-Amazon backup/update surface described here.
