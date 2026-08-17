# Phase 7.2 — Offline Amazon Ads Report Ingestion Guide

**Authority:** `production/phase7_report_ingestion.py` (the ONE Phase 7.2 authority)
**Money/int authority:** `core/money.py` (Decimal only — never a float)
**Status of T2 today:** `PHASE7_REPORT_INPUT_REQUIRED` (no owner-exported report files exist yet)
**Upstream T2 product state (separate, never conflated):** `PHASE7_OWNER_INPUT_REQUIRED`

Phase 7.2 accepts Amazon Ads report files that **the owner has manually exported and copied into a
local inbox**, then validates, normalizes, reconciles, hashes, and stores sanitized local outputs —
**without connecting to Amazon**. It builds an analysis-ready foundation. It never optimizes.

---

## The permanent Amazon boundary (re-asserted)

The owner is the **only** bridge to Amazon. The toolkit **never**:

- logs into Seller Central / Amazon Ads; uses SP-API / MWS / the Advertising API; runs a browser;
- downloads or retrieves a report; stores a credential, cookie, session, or token;
- creates or updates a campaign, ad group, target, negative, bid, or budget;
- emits an Amazon API payload; binds a public server; sends report rows to an external service.

Every action/network counter is **zero** and is asserted in the proof, the verification report, and the
tests:

```
external_amazon_account_attempts = amazon_account_actions = campaign_write_actions = 0
target_write_actions = negative_write_actions = bid_write_actions = budget_write_actions = 0
report_download_attempts = external_network_attempts = browser_automation_attempts = 0
credential_store_count = api_payload_count = automated_optimization_actions = 0
```

**Out of scope for Phase 7.2 (belongs to a later, separately-authorized phase):** bid/budget/negative
recommendations, search-term harvesting, campaign restructuring, placement/daypart tuning, any
optimization or action queue, and Phase 7.3 / 7.4.

---

## What the owner does (the only manual bridge)

1. In Seller Central, **manually export** a Sponsored Products report (campaign, targeting,
   search-term, advertised-product, purchased-product, placement, or budget).
2. **Copy** the exported `.csv` / `.tsv` file into the local inbox:
   `runs/<product>/phase7/7.2/inbox/` (this whole tree is gitignored — reports never leave the machine).
3. Re-run the ingestion. Everything below happens **offline and deterministically**.

---

## The local workspace (gitignored)

```
runs/<product>/phase7/7.2/
  inbox/          owner drops report files here (toolkit never downloads into it)
  processing/     transient atomic workspace
  accepted_raw/   immutable accepted source copies, named by content SHA-256 (never edited)
  quarantine/     invalid / unsupported files + machine-readable reasons
  candidate/      a complete normalized candidate dataset (verified before promotion)
  final/          the current verified normalized dataset
  last_valid/     the prior verified dataset (preserved on every promotion)
  manifests/  fixtures/
```

---

## Pipeline (deterministic, offline)

1. **Scan inbox** — regular files only; symlinks, subdirectories, null-byte names, over-count are
   rejected and never followed.
2. **Fingerprint** — content SHA-256 is the source identity (never the filename). Re-importing the
   exact same bytes is idempotent (`IDEMPOTENT_ALREADY_IMPORTED`) — never double-counted.
3. **Detect format** — extension **and** content signature. CSV / CSV+BOM / TSV (UTF-8 only). XLSX /
   OLE / zip / binary → `UNSUPPORTED_REPORT_FORMAT` / `EXTENSION_CONTENT_MISMATCH` (no heavy
   dependency added). Declared limits: 64 MB/file, 256 files, 2M rows, 512 cols, 32 KB/cell.
4. **Normalize headers** — NFKC, trim, collapse whitespace, lowercase comparison key, punctuation →
   space; map known Amazon aliases to canonical fields; unknown columns kept as declared *extras*
   (never silently dropped); duplicate canonical columns detected (conflict → row error).
5. **Classify** — by declared **required + distinguishing headers only**; the filename is
   non-authoritative supporting evidence. `UNKNOWN` / `AMBIGUOUS` → quarantine.
6. **Parse values** — money & rates via `core.money` (Decimal; float / NaN / Infinity / blank /
   scientific / malformed grouping rejected; canonical decimal **strings** out, never floats). Counts
   are non-negative ints (fractions/negatives rejected). Dates → ISO `YYYY-MM-DD`; `end < start` →
   `INVALID_DATE_RANGE`; a future end date (vs an owner-declared reference date) → `FUTURE_DATE`; no
   wall-clock is ever consulted (determinism).
7. **Metric semantics** — attribution windows (`sales/orders/units` × 1/7/14/30-day) are kept
   **distinct** and never inferred from one another; a window-less `Sales` header is preserved as an
   extra, never mapped to a window. `MISSING` / `BLANK` / `ZERO` / `NOT_APPLICABLE` / `INVALID` /
   `PRESENT` are distinguished.
8. **Validate rows** — per report type, with deterministic reason codes (`MISSING_REQUIRED_FIELD`,
   `INVALID_DECIMAL`, `NEGATIVE_METRIC`, `INVALID_DATE`, `CURRENCY_MISSING`, `CURRENCY_CONFLICT`,
   `DUPLICATE_CANONICAL_COLUMN`, `UNSUPPORTED_MATCH_TYPE`, `UNSAFE_FORMULA_CELL`, `OVERSIZED_CELL`, …).
9. **Lineage** — every normalized row retains `source_file_sha256`, `source_row_number`, report type,
   source range, original headers, `canonical_row_key`, and a `lineage_hash`; reconciled rows keep all
   contributing references. Accepted raw bytes stay immutable.
10. **Reconcile** — per report type, using a declared interval-vs-snapshot registry. Exact-duplicate
    rows collapse (never double-counted); conflicting duplicates block. Interval overlaps are
    classified (`NO_OVERLAP` / `EXACT_DUPLICATE_COVERAGE` / `EXACT_COVERAGE_CONFLICT` / `PARTIAL_OVERLAP`
    / `CONTAINED_RANGE` / `ADJACENT_RANGE`); a partial/contained/conflicting overlap →
    `PHASE7_REPORT_OVERLAP_REVIEW_REQUIRED`. Snapshots are **never summed** (latest kept, older
    retained for lineage). Unknown semantics → `REPORT_SEMANTICS_REQUIRED` (aggregation blocked). A
    range total is **never** divided across days.
11. **Write candidate → verify bytes → promote atomically** — the prior `final` is snapshotted into
    `last_valid` first; a failed verification never overwrites `final`.

---

## CSV / spreadsheet safety

All cells are treated as **data** — the importer never evaluates a formula. A text cell that begins
with `=`, `+`, `-`, `@`, tab, or CR is flagged `UNSAFE_FORMULA_CELL` and quarantined; `csv_safe_cell()`
neutralizes any formula-leading value in human exports by prefixing a single quote.

---

## Analysis-readiness states

`PHASE7_2_PREFLIGHT_BLOCKED · PHASE7_REPORT_INPUT_REQUIRED · PHASE7_REPORT_FORMAT_BLOCKED ·
PHASE7_REPORT_CLASSIFICATION_BLOCKED · PHASE7_REPORT_VALIDATION_BLOCKED · PHASE7_REPORT_CURRENCY_BLOCKED
· PHASE7_REPORT_DATE_BLOCKED · PHASE7_REPORT_OVERLAP_REVIEW_REQUIRED · PHASE7_REPORT_CONFLICT_BLOCKED ·
PHASE7_REPORT_VERIFICATION_BLOCKED · SESSION7_2_REPORTS_READY_FOR_ANALYSIS · SESSION7_2_BLOCKED`

The most-restrictive component wins. Readiness booleans `ready_for_phase7_3_decision_support`,
`ready_for_automated_optimization`, and `ready_for_amazon_action` are **always false** in Phase 7.2.

---

## Privacy

Owner reports are private. Committed proof carries **states, counts, schema versions, and reason codes
only** — never a campaign name, search term, ASIN/SKU, spend, sale, absolute path, or credential. All
report data stays under `runs/` (gitignored). Test fixtures are `SYNTHETIC_TEST_DATA_ONLY` and are
never mixed with a T2 owner dataset.

---

## CLI

```
python -m production.phase7_report_ingestion --base-dir runs/T2/phase7/7.2 \
    --mode LOCAL_SAFE --reference-date 2026-07-19
```

With an empty inbox this truthfully resolves to `PHASE7_REPORT_INPUT_REQUIRED` and writes only the
blocked-safe artifacts (`PHASE7-REPORT-INPUT-REQUIRED.md`, readiness, verification, manifest).
