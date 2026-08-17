# Session 7.3 — Offline Sponsored Products Analysis (Implementation Report)

Phase 7.3 reads **only promoted Phase 7.2 output** and produces owner-review reports.
It connects to nothing. The owner remains the only manual bridge to Amazon Seller Central.

---

## 1. Commits

| Role | Commit | Description |
| --- | --- | --- |
| Baseline | `d35fa17` | accepted Phase 7.2 (`phase7-2-accepted-d35fa17`), 2416 passed / 2 skipped |
| Phase 7.2 bugfix | `eaffc86` | never promote an empty dataset over promoted rows |
| Phase 7.3 feature | `9cc4344` | offline Sponsored Products analysis engine + 117 tests |
| Phase 7.3 proof | *(this commit)* | this report + `SESSION7_3-PROOF-GATE.json` |

Branch: `phase7-3-offline-ads-analysis`. No accepted history was rewritten; the
`phase7-2-accepted-d35fa17` tag is untouched.

---

## 2. Changed files

| File | Change |
| --- | --- |
| `production/phase7_report_ingestion.py` | **bugfix** — carry-forward guard (see §3) |
| `tests/test_phase7_2_report_ingestion.py` | +16 carry-forward regression tests; 1 pre-existing test corrected |
| `production/phase7_ads_analysis.py` | **new** — the one Phase 7.3 authority |
| `tests/test_phase7_3_ads_analysis.py` | **new** — 117 tests across all 24 required areas |
| `SESSION7_3-PROOF-GATE.json` | **new** — sanitized proof gate |
| `SESSION7_3-IMPLEMENTATION-REPORT.md` | **new** — this report |

`runs/` is gitignored, so the live T2 Phase 7.3 package is **not** committed.

---

## 3. Blocker found first: Phase 7.2 destroyed its own promoted rows

Before any Phase 7.3 code was written, inspection of the accepted source showed
`runs/T2/phase7/7.2/final/` contained **no normalized rows at all** — only the 10 metadata
artifacts, with `raw_row_count: 0` and `currency_set: []`, while still reporting
`SESSION7_2_REPORTS_READY_FOR_ANALYSIS` and `promote=PASS`.

**Cause** (`phase7_report_ingestion.py:1741-1748`): normalized JSONL is built only when a run
re-normalizes rows. An idempotent re-run re-normalizes nothing, so its candidate carried no
JSONL — and `promote_candidate` cleared `final/` and moved that empty candidate in. The 114
previously promoted rows were destroyed by a run that reported success.

**Fix** (`eaffc86`): a run with nothing new to report — no accepted source, no normalized row,
no quarantined source, no scan reject — is now a promotion **no-op**. `final/` and `last_valid/`
are left byte-for-byte untouched and the prior manifest, hashes and row counts stand. Before
carrying forward, the prior state is re-verified from its own bytes (manifest deterministic hash,
then every recorded artifact hash); if that fails the run **blocks** with
`PHASE7_REPORT_CARRY_FORWARD_BLOCKED` rather than erasing data. A run that *does* have something
new still promotes normally, so a newly quarantined file is still reported.

One pre-existing test, `test_no_empty_normalized_claimed`, asserted the erasure as intended
behaviour (`assertNotIn(NORMALIZED_FILE[...], os.listdir(final))`). Its real intent was "never
emit an *empty* normalized file", so it now pins that contract instead: the promoted rows survive
a re-run, and a report type that never had rows still gets no artifact.

**T2 restoration.** The fix prevents future loss but could not un-erase what was already gone
(`last_valid/` held the same empty snapshot). With owner approval the archive copy
`accepted_raw/02980a…tsv` — byte-identical to the still-present `inbox/Ads.tsv` — was backed up
to a scratch directory and removed so the inbox original re-imported. Verified afterwards: the
regenerated archive copy is byte-identical to the backup, and `final/` now holds 114 rows. A
subsequent idempotent re-run preserves them byte-for-byte.

---

## 4. Actual Phase 7.2 normalized contract consumed

Phase 7.2 emits **JSONL**, not CSV — one canonical-JSON file per report type
(`PHASE7-SP-SEARCH-TERM-NORMALIZED.jsonl`), schema `phase7-2-normalized-row-v1`:

| Group | Fields |
| --- | --- |
| top level | `schema_version`, `report_type`, `marketplace`, `start_date`, `end_date`, `currency`, `duplicate_count`, `canonical_row_key`, `key_basis` |
| `identity` | `campaign_name`, `ad_group_name`, `targeting_expression`, `search_term`, `match_type` *(present on 42/114 rows only — auto targets omit it)* |
| `metrics` | `impressions`, `clicks` (int) · `spend`, `sales_7d` (**Decimal-as-string**, e.g. `"0.18"`) · `orders_7d`, `units_7d` (int) |
| `metric_states` | per field `ZERO` / `BLANK` / `MISSING` — a real 0 stays distinguishable from an absent value |
| `lineage` | `source_file_sha256`, `source_row_number`, `source_original_headers`, `canonical_row_key`, `contributing[]`, `lineage_hash` |

CTR, CPC, ACoS and ROAS are **not** carried over (those source columns land in `extras`), so
Phase 7.3 derives them. Money arrives as strings, so the Decimal path is unbroken end to end.

---

## 5. Input contract and block states

Phase 7.3 reads only `<phase7-2-dir>/final/`. `assert_source_path_allowed` refuses any path
outside it, and `inbox`, `processing`, `accepted_raw`, `quarantine` and `candidate` are declared
forbidden. Source gating reuses Phase 7.2's own authority (`read_promoted_manifest`,
`verify_promoted_state`) rather than re-implementing verification.

| State | Raised when |
| --- | --- |
| `PHASE7_3_SOURCE_NOT_READY` | promoted dir missing, readiness ≠ `SESSION7_2_REPORTS_READY_FOR_ANALYSIS`, or a declared normalized artifact is absent |
| `PHASE7_3_SOURCE_MANIFEST_INVALID` | manifest missing/unreadable, manifest hash mismatch, artifact hash mismatch, or a non-JSON row |
| `PHASE7_3_UNSUPPORTED_REPORT_TYPE` | no supported (search-term) report type promoted |
| `PHASE7_3_REQUIRED_COLUMNS_MISSING` | a required identity group or metric column is absent |
| `PHASE7_3_CURRENCY_MIX_BLOCKED` | more than one currency present |
| `PHASE7_3_ANALYSIS_BLOCKED` | staging verification failed at promotion |
| `SESSION7_3_ANALYSIS_READY_FOR_OWNER_REVIEW` | success |

A blocked run writes `logs/analysis-blocked.json`, leaves `promoted/` untouched, and exits 1.

---

## 6. Financial rules

- **Decimal only.** All money flows through `core/money.py`. `float(` appears zero times.
- **Malformed never becomes zero.** A float, negative or unparseable value is recorded `INVALID`,
  excluded from totals, and counted as `invalid_numeric_count`.
- **Zero denominator returns null.** Via `MONEY.safe_divide`: zero sales → ACoS null (not 0),
  zero clicks → CPC and conversion rate null, zero impressions → CTR null, zero spend → ROAS null.
  A genuine zero is preserved: zero orders over real clicks is conversion rate `0.000000`.
- **Currency preserved, never combined.** A second currency blocks; aggregates bucket per currency
  so two currencies can never merge into one total.
- **Rounding centralized.** `MONEY.serialize_currency` (2 dp) and `MONEY.serialize_rate` (6 dp).

---

## 7. Classification precedence

Ordered rule table — the **first** match sets the single primary classification; a row may carry
many reason codes.

1. `REQUIRED_METRIC_UNUSABLE` → `INSUFFICIENT_DATA`
2. `NO_IMPRESSIONS` → `INSUFFICIENT_DATA`
3. `NO_CLICKS` → `NO_CLICKS`
4. `OUTCOME_NOT_REPORTED` → `NEEDS_OWNER_REVIEW`
5. `HIGH_SPEND_NO_SALES` → `HIGH_SPEND_NO_SALES`
6. `PROMISING_LOW_DATA` → `PROMISING_LOW_DATA`
7. `HIGH_ACOS` → `HIGH_ACOS`
8. `LOW_ACOS` → `LOW_ACOS`
9. `PROVEN_CONVERTER` → `PROVEN_CONVERTER`
10. `LOW_CONVERSION` → `LOW_CONVERSION`
11. `LOW_CLICK_VOLUME` → `INSUFFICIENT_DATA`

**Why 6 precedes 7-9:** an ACoS or conversion judgment requires the configured minimum clicks
first. Without that gate the live T2 row with 1 click, $0.31 spend and $38.40 sales would be
labelled `LOW_ACOS` → `REVIEW_FOR_MANUAL_EXACT_KEYWORD`, presenting one lucky order as a proven
efficiency signal. It correctly reads `PROMISING_LOW_DATA` → `KEEP_MONITORING`. Lowering
`minimum_clicks_for_conversion_judgment` in the threshold config is the owner-facing way to
surface thinner signals.

---

## 8. Owner review labels only

`PROVEN_CONVERTER`/`LOW_ACOS` → `REVIEW_FOR_MANUAL_EXACT_KEYWORD` · `HIGH_SPEND_NO_SALES` →
`REVIEW_FOR_MANUAL_NEGATIVE` · `HIGH_ACOS`/`LOW_CONVERSION` → `REVIEW_BID_OR_BUDGET_CONTEXT` ·
`PROMISING_LOW_DATA`/`NO_CLICKS` → `KEEP_MONITORING` · `INSUFFICIENT_DATA`/`NEEDS_OWNER_REVIEW` →
`INSUFFICIENT_EVIDENCE`.

These are prompts to look, never instructions to act. A test scans every promoted artifact for 22
imperative phrases ("add this keyword", "pause this", "set bid", "reduce budget", "add negative",
"upload the", "recommended bid", …) and asserts none appear.

---

## 9. Threshold configuration

One versioned document, `config/analysis-thresholds.json` (`phase7-3-analysis-thresholds-v1`),
materialized on first run and honored as-is when the owner edits it. Nothing is hardcoded at a
call site.

| Key | Default |
| --- | --- |
| `minimum_clicks_for_conversion_judgment` | 10 |
| `minimum_spend_for_no_sale_risk` | `"5.00"` |
| `minimum_orders_for_proven_converter` | 1 |
| `target_acos` | `"0.30"` — **`NEUTRAL_DEFAULT_OWNER_CONFIGURABLE`** |
| `high_acos_multiplier` / `low_acos_multiplier` | `"1.25"` / `"0.75"` |
| `minimum_impressions_for_ctr_judgment` | 100 |
| `lookback_days` / `date_rule` | 30 / `SOURCE_REPORTED_RANGE_ONLY_NEVER_INFERRED` |

There is no owner-declared target ACoS. The 0.30 default is explicitly marked neutral and the
owner report states it is owner-configurable.

---

## 10. Exact CLI command used

```
python -m production.phase7_ads_analysis \
  --base-dir "runs/T2/phase7/7.3" \
  --phase7-2-dir "runs/T2/phase7/7.2" \
  --reference-date "2026-07-19"
```

PowerShell form:

```powershell
python -m production.phase7_ads_analysis `
  --base-dir "runs/T2/phase7/7.3" `
  --phase7-2-dir "runs/T2/phase7/7.2" `
  --reference-date "2026-07-19"
```

### Exact readiness result

```
analysis_readiness=SESSION7_3_ANALYSIS_READY_FOR_OWNER_REVIEW
promote=PASS
source_rows=114
analyzed_rows=114
decision_queue_rows=0
blocked_rows=0
```

`decision_queue_rows=0` is the truthful result for this dataset: 113 of 114 rows sit under the
10-click minimum, and the one converting row has a single click. It is a thin-data outcome, not a
failure.

**Live analysis outcome:** `INSUFFICIENT_DATA` 113, `PROMISING_LOW_DATA` 1 →
`INSUFFICIENT_EVIDENCE` 113, `KEEP_MONITORING` 1. 61 campaigns, 61 ad groups, USD only,
2026-06-06 → 2026-07-05 (28 distinct dates), 0 invalid numerics, 0 duplicate keys, ACoS null on
113 zero-sales rows.

---

## 11. Test results

| Suite | Command | Result |
| --- | --- | --- |
| Phase 7.3 focused | `python -m pytest tests/test_phase7_3_ads_analysis.py -q` | **117 passed** |
| Phase 7.2 focused | `python -m pytest tests/test_phase7_2_report_ingestion.py -q` | **332 passed, 1 skipped** |
| Full suite | `python -m pytest -q` | **2549 passed, 2 skipped** (613s) |
| Compile | `python -m compileall production tests` | exit 0 |

Baseline was 2416 passed / 2 skipped. Zero regressions.

All 24 required test areas are covered: accepted source succeeds · raw inbox never read · source
not ready blocks · missing normalized artifact blocks · required columns missing blocks ·
malformed Decimal handling · zero clicks/spend/sales/orders · high spend no sales · proven
converter · high ACoS · low ACoS · insufficient data · deterministic classification precedence ·
deterministic sorting · repeated run stability · mixed currency safety · source lineage · no
prohibited imports · review labels only · atomic promotion rollback · real-schema regression
fixture.

The regression fixture uses the **exact real Amazon search-term export header layout** — trailing
spaces, `(#)` suffixes, `$` money, `%` rates, `Mmm d, yyyy` dates — ingested by the real Phase 7.2
engine, so it pins Phase 7.3 against what Phase 7.2 actually produces in production.

---

## 12. Security check

Zero occurrences in `production/phase7_ads_analysis.py` of: `requests`, `httpx`, `urllib`,
`urllib.request`, `http.client`, `boto3`, `botocore`, `selenium`, `playwright`, `pyppeteer`,
`webdriver`, `socket`, `aiohttp`, `subprocess`, `sp_api`. No browser or network subprocess launch.
No URL literal. Imports are restricted to an allowlist (`csv`, `datetime`, `hashlib`, `io`,
`json`, `os`, `sys`, `tempfile`, `decimal`, `argparse`, plus `production` / `core`), enforced by a
test. All 15 zero-action boundary counters are 0.

---

## 13. Remaining risks

1. **No owner target ACoS.** Every ACoS-dependent judgment is provisional until the owner sets a
   real target. Ranked #1 because it silently shapes `HIGH_ACOS` / `LOW_ACOS`.
2. **Thin dataset.** With 113/114 rows below the click minimum, Phase 7.3 currently produces
   almost no actionable signal. More data — not looser thresholds — is the fix.
3. **Search-term report only.** Campaign, targeting, placement and budget reports are ingested by
   Phase 7.2 but not analysed; they block as unsupported rather than being guessed at.
4. **7-day attribution assumed.** Sources without `7 Day` columns yield `NEEDS_OWNER_REVIEW`.
5. **Phase 7.2 carry-forward fix is unaccepted.** `eaffc86` changes an accepted-baseline module.
   It is covered by 16 new tests, but Phase 7.2 acceptance was granted against `d35fa17`, so the
   carry-forward behaviour should be re-checked as part of accepting 7.3.
6. **Restoration was a manual step.** The T2 promoted rows were restored by removing a redundant
   archive copy. That path is owner-approved and verified byte-identical, but it is not automated
   and would need repeating if the workspace is ever wiped.

---

## 14. Proposed independent acceptance procedure

1. `git log --oneline d35fa17..HEAD` — confirm exactly three commits (bugfix, feature, proof) and
   that `phase7-2-accepted-d35fa17` still points at `d35fa17`.
2. `python -m compileall production tests` — expect exit 0.
3. `python -m pytest tests/test_phase7_3_ads_analysis.py -q` — expect 117 passed.
4. `python -m pytest tests/test_phase7_2_report_ingestion.py -q` — expect 332 passed, 1 skipped.
5. `python -m pytest -q` — expect zero failures and a count ≥ the 2416 baseline.
6. **Carry-forward proof:** in a scratch workspace, ingest a synthetic report, note
   `final/` hashes, re-run, and confirm `accepted=0`, `carry_forward=CARRY_FORWARD_PRESERVED`,
   and byte-identical `final/`.
7. **Live run:** execute the §10 command and confirm the exact readiness block.
8. **Determinism:** run it twice and diff `sha256` over `runs/T2/phase7/7.3/promoted/` — expect
   no difference.
9. **Source immutability:** hash `runs/T2/phase7/7.2/final/` before and after — expect no change.
10. **Inbox independence:** temporarily move `runs/T2/phase7/7.2/inbox` aside, re-run, confirm
    success; restore it.
11. **Block path:** run against an empty directory — expect `PHASE7_3_SOURCE_NOT_READY` or
    `PHASE7_3_SOURCE_MANIFEST_INVALID`, exit 1, and an empty `promoted/`.
12. **Boundary scan:** grep `production/phase7_ads_analysis.py` for the §12 token list — expect
    zero hits.
13. **Label scan:** grep the promoted artifacts for imperative action phrases — expect zero hits.
14. Confirm no Phase 7.4 file exists and no Amazon credential, endpoint or payload appears
    anywhere in the diff.

---

## 15. Final self-audit

| Question | Answer |
| --- | --- |
| Did Phase 7.3 read only promoted Phase 7.2 artifacts? | **Yes.** Only `<7.2>/final/`; `assert_source_path_allowed` refuses everything else; analysis succeeds with the inbox deleted; the manifest records `raw_inbox_read: false`. |
| Are calculations Decimal-safe? | **Yes.** All money via `core/money.py`; zero `float(` calls; floats rejected as `INVALID`. |
| Are classifications deterministic and threshold-driven? | **Yes.** Ordered rule table, first match wins, every bound from the versioned threshold document. |
| Are outputs owner-review labels only? | **Yes.** Five review labels; 22 imperative phrases asserted absent from every promoted artifact. |
| Are mixed currencies handled safely? | **Yes.** A second currency blocks; aggregates bucket per currency so totals never merge. |
| Are repeated runs stable? | **Yes.** Byte-identical promoted output across runs and across workspaces; no row duplication. |
| Is every row traceable? | **Yes.** `source_file`, `source_file_sha256`, `source_row_number`, `source_line_number`, `canonical_row_key`, `lineage_hash` on every row and in the CSV. |
| Do all tests pass? | **Yes.** 117 focused, 332 Phase 7.2 focused, full suite green with zero regressions. |
| Are prohibited integrations zero? | **Yes.** All 15 scanned tokens absent; import allowlist enforced by test. |
| Was Phase 7.4 untouched? | **Yes.** No Phase 7.4 file created, referenced or implemented. |
