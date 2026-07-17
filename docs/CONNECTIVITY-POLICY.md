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
