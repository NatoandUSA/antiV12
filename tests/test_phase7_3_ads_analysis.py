#!/usr/bin/env python3
"""Phase 7.3 — offline Sponsored Products analysis tests (release-grade).

Hermetic + offline by construction. Every report here is SYNTHETIC_TEST_DATA_ONLY built in a temp
workspace — never a real owner export, never mixed with any T2 dataset. This module opens NO
network and asserts the permanent Amazon boundary throughout: Phase 7.3 reads only promoted Phase
7.2 output, emits owner REVIEW LABELS only, and never takes an action against Amazon.
"""
import json
import os
import random
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("", "core", "listing", "production", "scripts"):
    _p = ROOT if not _sub else os.path.join(ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import phase7_ads_analysis as AA        # noqa: E402
from production import phase7_report_ingestion as RI    # noqa: E402
from core import money as MONEY                         # noqa: E402

REF = "2026-07-19"
NOW = "2026-07-19T10:00:00+07:00"
SHA = "a" * 64

# The real Amazon Sponsored Products search-term export header layout (trailing spaces, "(#)"
# suffixes and parenthesised metrics preserved exactly as Amazon emits them). Values are synthetic.
REAL_SEARCH_TERM_HEADERS = [
    "Start Date", "End Date", "Portfolio name", "Currency", "Campaign Name", "Ad Group Name",
    "Retailer", "Country", "Targeting", "Match Type", "Customer Search Term", "Impressions",
    "Clicks", "Click-Thru Rate (CTR)", "Cost Per Click (CPC)", "Spend", "7 Day Total Sales ",
    "Total Advertising Cost of Sales (ACOS) ", "Total Return on Advertising Spend (ROAS)",
    "7 Day Total Orders (#)", "7 Day Total Units (#)", "7 Day Conversion Rate",
    "7 Day Advertised SKU Units (#)", "7 Day Other SKU Units (#)", "7 Day Advertised SKU Sales ",
    "7 Day Other SKU Sales ",
]


def real_search_term_tsv(rows):
    """A TSV in the exact real Amazon export layout. rows: list of dicts of overrides."""
    out = ["\t".join(REAL_SEARCH_TERM_HEADERS)]
    for r in rows:
        cells = [
            r.get("start", "2026-07-01"), r.get("end", "2026-07-01"), "No Portfolio",
            r.get("currency", "USD"), r.get("campaign", "Auto Nurse - 1/1/2026 00:00:00.000"),
            r.get("ad_group", "Auto Nurse AG"), "Amazon", "United States",
            r.get("targeting", "close-match"), r.get("match_type", "-"),
            r.get("search_term", "synthetic term"), r.get("impressions", 100), r.get("clicks", 10),
            "10.0000%", "$0.50", r.get("spend", "$5.00"), r.get("sales", "$0.00"), "",
            "0.00", r.get("orders", 0), r.get("units", 0), "0.0000%", 0, 0, "$0.00", "$0.00",
        ]
        out.append("\t".join(str(c) for c in cells))
    return "\n".join(out) + "\n"


def norm_row(**kw):
    """One normalized row in the EXACT Phase 7.2 `phase7-2-normalized-row-v1` shape."""
    identity = {
        "campaign_name": kw.get("campaign", "Campaign A"),
        "ad_group_name": kw.get("ad_group", "Ad Group A"),
        "targeting_expression": kw.get("targeting", "close-match"),
        "search_term": kw.get("search_term", "synthetic term"),
    }
    if kw.get("match_type"):
        identity["match_type"] = kw["match_type"]
    metrics, states = {}, {}
    for field, key in (("impressions", "impressions"), ("clicks", "clicks"),
                       ("orders_7d", "orders"), ("units_7d", "units")):
        if key in kw and kw[key] is None:
            states[field] = "MISSING"
            continue
        value = kw.get(key, {"impressions": 100, "clicks": 10, "orders": 0, "units": 0}[key])
        metrics[field] = value
        if value == 0:
            states[field] = "ZERO"
    for field, key in (("spend", "spend"), ("sales_7d", "sales")):
        if key in kw and kw[key] is None:
            states[field] = "MISSING"
            continue
        value = kw.get(key, {"spend": "5.00", "sales": "0.00"}[key])
        metrics[field] = value
        if value in ("0.00", "0"):
            states[field] = "ZERO"
    start, end = kw.get("start", "2026-07-01"), kw.get("end", "2026-07-01")
    key = RI.canonical_row_key(RI.SP_SEARCH_TERM, "US", start, end, {
        "campaign": identity["campaign_name"], "ad_group": identity["ad_group_name"],
        "targeting": identity["targeting_expression"], "search_term": identity["search_term"],
        "match_type": kw.get("match_type") or ""})
    sha = kw.get("sha", SHA)
    row_number = kw.get("row_number", 2)
    return {
        "schema_version": RI.SCHEMA_NORMALIZED_ROW,
        "report_type": RI.SP_SEARCH_TERM,
        "marketplace": "US",
        "start_date": start, "end_date": end,
        "currency": kw.get("currency", "USD"),
        "identity": identity,
        "key_basis": {"campaign": "campaign_name", "ad_group": "ad_group_name",
                      "targeting": "targeting_expression", "search_term": "search_term"},
        "metrics": metrics, "metric_states": states,
        "duplicate_count": 1, "canonical_row_key": key,
        "lineage": RI.lineage_for(sha, row_number, RI.SP_SEARCH_TERM, start, end,
                                  REAL_SEARCH_TERM_HEADERS, key),
    }


class Base(unittest.TestCase):
    def mkbase(self):
        d = tempfile.mkdtemp(prefix="p73_")
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    # ---- a hand-built promoted Phase 7.2 state (full control over the normalized rows) ----
    def fake_phase7_2(self, rows, *, readiness=None, report_type=RI.SP_SEARCH_TERM,
                      include_rows=True):
        base = self.mkbase()
        final = os.path.join(base, "final")
        os.makedirs(final, exist_ok=True)
        name = RI.NORMALIZED_FILE[report_type]
        output_hashes, required = {}, []
        if include_rows:
            body = "".join(json.dumps(r, sort_keys=True, ensure_ascii=False,
                                      separators=(",", ":")) + "\n" for r in rows)
            data = body.encode("utf-8")
            with open(os.path.join(final, name), "wb") as f:
                f.write(data)
            output_hashes[name] = AA._sha_bytes(data)
            required.append(name)
        manifest = RI._finalize({
            "schema_version": RI.SCHEMA_MANIFEST, "stage_id": "7.2",
            "analysis_readiness": readiness or RI.REPORTS_READY,
            "counts": {"valid_row_count": len(rows)},
            "required_artifacts": sorted(required),
            "generated_outputs": sorted(output_hashes),
            "output_hashes": output_hashes,
        }, extra_volatile=("output_hashes",))
        with open(os.path.join(final, RI.F_MANIFEST), "wb") as f:
            f.write(AA.canonical_json(manifest).encode("utf-8"))
        return base

    # ---- a REAL promoted Phase 7.2 state, produced by the Phase 7.2 engine itself ----
    def real_phase7_2(self, rows):
        base = self.mkbase()
        dirs = RI.ensure_workspace(base)
        with open(os.path.join(dirs["inbox"], "search_terms.tsv"), "wb") as f:
            f.write(real_search_term_tsv(rows).encode("utf-8"))
        result = RI.run_ingestion(base, reference_date=REF, now=NOW, run_id="t")
        self.assertEqual(result.state, RI.REPORTS_READY)
        return base

    def run73(self, phase7_2_base, **kw):
        kw.setdefault("reference_date", REF)
        return AA.run_analysis(self.mkbase(), phase7_2_base, **kw)

    @staticmethod
    def snapshot(directory):
        out = {}
        for name in sorted(os.listdir(directory)):
            p = os.path.join(directory, name)
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    out[name] = f.read()
        return out

    @staticmethod
    def thresholds(**overrides):
        doc = dict(AA.DEFAULT_THRESHOLDS)
        doc.update(overrides)
        return AA.Thresholds(doc)

    def analyze(self, row, **overrides):
        return AA.analyze_row(row, self.thresholds(**overrides))


# ================================================================ 1. ACCEPTED SOURCE SUCCEEDS
class TestAcceptedSourceSucceeds(Base):
    def test_accepted_phase7_2_source_succeeds(self):
        base = self.real_phase7_2([{"search_term": f"term {i}"} for i in range(5)])
        r = self.run73(base)
        self.assertEqual(r.state, AA.ANALYSIS_READY)
        self.assertEqual(r.promote_report["result"], "PASS")
        self.assertEqual(r.analysis["data_quality"]["source_row_count"], 5)
        self.assertEqual(r.analysis["data_quality"]["analyzed_row_count"], 5)
        self.assertEqual(r.analysis["data_quality"]["blocked_row_count"], 0)

    def test_all_promoted_artifacts_written(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        r = self.run73(base)
        promoted = os.listdir(r.dirs[AA.D_PROMOTED])
        for name in AA.PROMOTED_ARTIFACTS:
            self.assertIn(name, promoted)

    def test_workspace_layout_created(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        r = self.run73(base)
        for sub in (AA.D_CONFIG, AA.D_STAGING, AA.D_PROMOTED, AA.D_LOGS, AA.D_QUARANTINE):
            self.assertTrue(os.path.isdir(r.dirs[sub]), sub)

    def test_threshold_config_materialized(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        r = self.run73(base)
        path = os.path.join(r.dirs[AA.D_CONFIG], AA.F_THRESHOLDS)
        self.assertTrue(os.path.isfile(path))
        doc = json.load(open(path, encoding="utf-8"))
        self.assertEqual(doc["schema_version"], AA.SCHEMA_THRESHOLDS)

    def test_phase7_2_source_is_not_modified(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        final = os.path.join(base, "final")
        before = self.snapshot(final)
        self.run73(base)
        self.assertEqual(self.snapshot(final), before)


# ================================================================ 2. RAW INBOX IS NEVER READ
class TestRawInboxNeverRead(Base):
    def test_analysis_succeeds_without_any_inbox(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        shutil.rmtree(os.path.join(base, "inbox"))
        shutil.rmtree(os.path.join(base, "accepted_raw"))
        r = self.run73(base)
        self.assertEqual(r.state, AA.ANALYSIS_READY)

    def test_inbox_path_is_refused(self):
        base = self.mkbase()
        with self.assertRaises(AA.AdsAnalysisError):
            AA.assert_source_path_allowed(os.path.join(base, "inbox", "Ads.tsv"), base)

    def test_every_forbidden_subdir_is_refused(self):
        base = self.mkbase()
        for sub in AA.FORBIDDEN_SOURCE_SUBDIRS:
            with self.assertRaises(AA.AdsAnalysisError):
                AA.assert_source_path_allowed(os.path.join(base, sub, "x.jsonl"), base)

    def test_promoted_path_is_allowed(self):
        base = self.mkbase()
        path = os.path.join(base, "final", RI.NORMALIZED_FILE[RI.SP_SEARCH_TERM])
        self.assertEqual(AA.assert_source_path_allowed(path, base), os.path.abspath(path))

    def test_manifest_declares_no_raw_inbox_read(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        r = self.run73(base)
        self.assertFalse(r.manifest["source"]["raw_inbox_read"])
        self.assertEqual(r.analysis["amazon_boundary"]["raw_inbox_reads"], 0)

    def test_only_final_files_are_listed_as_sources(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        r = self.run73(base)
        for entry in r.source["source_files"]:
            self.assertIn(entry["artifact"], set(RI.NORMALIZED_FILE.values()))


# ================================================================ 3. SOURCE NOT READY BLOCKS
class TestSourceNotReady(Base):
    def test_input_required_source_blocks(self):
        base = self.mkbase()
        RI.ensure_workspace(base)
        RI.run_ingestion(base, reference_date=REF, now=NOW, run_id="t")   # empty inbox
        r = self.run73(base)
        self.assertEqual(r.state, AA.SOURCE_NOT_READY)

    def test_missing_promoted_directory_blocks(self):
        r = self.run73(self.mkbase())
        self.assertEqual(r.state, AA.SOURCE_NOT_READY)

    def test_not_ready_readiness_blocks(self):
        base = self.fake_phase7_2([norm_row()], readiness="PHASE7_REPORT_VALIDATION_BLOCKED")
        r = self.run73(base)
        self.assertEqual(r.state, AA.SOURCE_NOT_READY)
        self.assertEqual(r.analysis["block_detail"]["reason"], "READINESS_NOT_READY")

    def test_blocked_run_writes_no_promoted_artifacts(self):
        r = self.run73(self.mkbase())
        self.assertEqual(os.listdir(r.dirs[AA.D_PROMOTED]), [])

    def test_blocked_run_records_a_log(self):
        r = self.run73(self.mkbase())
        self.assertIn("analysis-blocked.json", os.listdir(r.dirs[AA.D_LOGS]))

    def test_blocked_run_exit_code_is_nonzero(self):
        base = self.mkbase()
        code = AA.main(["--base-dir", self.mkbase(), "--phase7-2-dir", base])
        self.assertEqual(code, 1)


# ================================================================ 4. MISSING NORMALIZED ARTIFACT
class TestMissingNormalizedArtifact(Base):
    def test_missing_manifest_blocks(self):
        base = self.mkbase()
        os.makedirs(os.path.join(base, "final"), exist_ok=True)
        r = self.run73(base)
        self.assertEqual(r.state, AA.SOURCE_MANIFEST_INVALID)

    def test_deleted_normalized_file_blocks(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        os.remove(os.path.join(base, "final", RI.NORMALIZED_FILE[RI.SP_SEARCH_TERM]))
        r = self.run73(base)
        self.assertIn(r.state, AA.BLOCKED_STATES)
        self.assertEqual(r.state, AA.SOURCE_MANIFEST_INVALID)

    def test_no_normalized_artifact_at_all_blocks_as_unsupported(self):
        base = self.fake_phase7_2([], include_rows=False)
        r = self.run73(base)
        self.assertEqual(r.state, AA.UNSUPPORTED_REPORT_TYPE)

    def test_tampered_normalized_file_blocks(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        path = os.path.join(base, "final", RI.NORMALIZED_FILE[RI.SP_SEARCH_TERM])
        with open(path, "ab") as f:
            f.write(b'{"tampered":true}\n')
        r = self.run73(base)
        self.assertEqual(r.state, AA.SOURCE_MANIFEST_INVALID)

    def test_unsupported_report_type_blocks(self):
        base = self.fake_phase7_2([norm_row()], report_type=RI.SP_BUDGET)
        r = self.run73(base)
        self.assertEqual(r.state, AA.UNSUPPORTED_REPORT_TYPE)


# ================================================================ 5. REQUIRED COLUMNS MISSING
class TestRequiredColumnsMissing(Base):
    def test_missing_clicks_metric_blocks(self):
        row = norm_row()
        del row["metrics"]["clicks"]
        base = self.fake_phase7_2([row])
        r = self.run73(base)
        self.assertEqual(r.state, AA.REQUIRED_COLUMNS_MISSING)
        self.assertIn("metric:clicks", r.analysis["block_detail"]["missing_fields"])

    def test_missing_spend_metric_blocks(self):
        row = norm_row()
        del row["metrics"]["spend"]
        base = self.fake_phase7_2([row])
        self.assertEqual(self.run73(base).state, AA.REQUIRED_COLUMNS_MISSING)

    def test_missing_identity_group_blocks(self):
        row = norm_row()
        row["identity"].pop("search_term")
        row["key_basis"].pop("search_term")
        base = self.fake_phase7_2([row])
        r = self.run73(base)
        self.assertEqual(r.state, AA.REQUIRED_COLUMNS_MISSING)
        self.assertIn("identity:search_term", r.analysis["block_detail"]["missing_fields"])

    def test_blank_state_column_still_counts_as_present(self):
        # the column EXISTS but the cell was empty -> present-but-blank, never "missing column"
        row = norm_row()
        del row["metrics"]["clicks"]
        row["metric_states"]["clicks"] = "BLANK"
        self.assertEqual(AA.required_columns_missing([row]), [])

    def test_required_columns_missing_is_sorted(self):
        row = norm_row()
        del row["metrics"]["clicks"]
        del row["metrics"]["spend"]
        out = AA.required_columns_missing([row])
        self.assertEqual(out, sorted(out))


# ================================================================ 6. MALFORMED DECIMAL HANDLING
class TestMalformedDecimal(Base):
    def test_malformed_money_is_invalid_not_zero(self):
        value, state = AA.read_money_metric({"spend": "not-a-number"}, {}, "spend")
        self.assertIsNone(value)
        self.assertEqual(state, "INVALID")

    def test_float_money_is_invalid_not_zero(self):
        value, state = AA.read_money_metric({"spend": 5.25}, {}, "spend")
        self.assertIsNone(value)
        self.assertEqual(state, "INVALID")

    def test_negative_money_is_invalid(self):
        value, state = AA.read_money_metric({"spend": "-1.00"}, {}, "spend")
        self.assertIsNone(value)
        self.assertEqual(state, "INVALID")

    def test_float_int_metric_is_invalid_not_truncated(self):
        value, state = AA.read_int_metric({"clicks": 3.7}, {}, "clicks")
        self.assertIsNone(value)
        self.assertEqual(state, "INVALID")

    def test_bool_is_never_accepted_as_int(self):
        value, state = AA.read_int_metric({"clicks": True}, {}, "clicks")
        self.assertIsNone(value)
        self.assertEqual(state, "INVALID")

    def test_malformed_row_is_insufficient_data_not_zero_metrics(self):
        row = norm_row()
        row["metrics"]["spend"] = "oops"
        a = self.analyze(row)
        self.assertIsNone(a["spend"])
        self.assertEqual(a["primary_classification"], AA.CL_INSUFFICIENT_DATA)
        self.assertIn(AA.RC_INVALID_NUMERIC, a["reason_codes"])

    def test_malformed_row_is_excluded_from_totals(self):
        good, bad = norm_row(search_term="good", spend="4.00"), norm_row(search_term="bad")
        bad["metrics"]["spend"] = "oops"
        base = self.fake_phase7_2([good, bad])
        r = self.run73(base)
        summary = r.analysis["campaign_summary"][0]
        self.assertEqual(summary["spend"], "4.00")     # the malformed row never became 0.00
        self.assertEqual(summary["excluded_rows"], 1)
        self.assertEqual(summary["analyzed_rows"], 1)

    def test_invalid_numeric_is_counted_in_data_quality(self):
        bad = norm_row()
        bad["metrics"]["spend"] = "oops"
        r = self.run73(self.fake_phase7_2([bad]))
        self.assertEqual(r.analysis["data_quality"]["invalid_numeric_count"], 1)

    def test_money_never_becomes_float(self):
        r = self.run73(self.fake_phase7_2([norm_row()]))
        for a in r.analysis["search_term_analysis"]:
            for field in ("spend", "sales", "cpc"):
                self.assertNotIsInstance(a[field], float)


# ================================================================ 7-10. ZERO METRIC HANDLING
class TestZeroMetrics(Base):
    def test_zero_clicks_yields_no_clicks(self):
        a = self.analyze(norm_row(clicks=0, spend="0.00"))
        self.assertEqual(a["primary_classification"], AA.CL_NO_CLICKS)
        self.assertIn(AA.RC_ZERO_CLICKS, a["reason_codes"])

    def test_zero_clicks_makes_cpc_and_cvr_null(self):
        a = self.analyze(norm_row(clicks=0, spend="0.00"))
        self.assertIsNone(a["cpc"])
        self.assertIsNone(a["conversion_rate"])
        self.assertIn(AA.RC_CPC_UNDEFINED, a["reason_codes"])

    def test_zero_impressions_is_insufficient_data(self):
        a = self.analyze(norm_row(impressions=0, clicks=0, spend="0.00"))
        self.assertEqual(a["primary_classification"], AA.CL_INSUFFICIENT_DATA)
        self.assertIsNone(a["ctr"])
        self.assertIn(AA.RC_CTR_UNDEFINED, a["reason_codes"])

    def test_zero_spend_makes_roas_null_not_zero(self):
        a = self.analyze(norm_row(spend="0.00", sales="10.00", orders=1))
        self.assertIsNone(a["roas"])
        self.assertIn(AA.RC_ROAS_UNDEFINED, a["reason_codes"])

    def test_zero_spend_keeps_acos_zero_not_null(self):
        # spend 0 with real sales is a genuine 0 ACoS, not an undefined one
        a = self.analyze(norm_row(spend="0.00", sales="10.00", orders=1))
        self.assertEqual(a["acos"], "0.000000")

    def test_zero_sales_makes_acos_null_not_zero(self):
        a = self.analyze(norm_row(sales="0.00"))
        self.assertIsNone(a["acos"])
        self.assertIn(AA.RC_ACOS_UNDEFINED, a["reason_codes"])
        self.assertIn(AA.RC_ZERO_SALES, a["reason_codes"])

    def test_zero_orders_makes_conversion_rate_zero_not_null(self):
        a = self.analyze(norm_row(clicks=10, orders=0))
        self.assertEqual(a["conversion_rate"], "0.000000")
        self.assertIn(AA.RC_ZERO_ORDERS, a["reason_codes"])

    def test_zero_denominator_counts_are_reported(self):
        r = self.run73(self.fake_phase7_2([norm_row(sales="0.00")]))
        self.assertEqual(r.analysis["data_quality"]["zero_denominator_counts"]["acos"], 1)

    def test_absent_sales_is_null_not_zero(self):
        a = self.analyze(norm_row(sales=None, orders=None))
        self.assertIsNone(a["sales"])
        self.assertEqual(a["primary_classification"], AA.CL_NEEDS_OWNER_REVIEW)
        self.assertIn(AA.RC_SALES_NOT_REPORTED, a["reason_codes"])


# ================================================================ 11-15. CLASSIFICATIONS
class TestClassifications(Base):
    def test_high_spend_no_sales(self):
        a = self.analyze(norm_row(clicks=20, spend="25.00", sales="0.00", orders=0))
        self.assertEqual(a["primary_classification"], AA.CL_HIGH_SPEND_NO_SALES)
        self.assertIn(AA.RC_SPEND_AT_OR_ABOVE_NO_SALE_MIN, a["reason_codes"])

    def test_high_spend_no_sales_label_is_review_for_manual_negative(self):
        a = self.analyze(norm_row(clicks=20, spend="25.00", sales="0.00", orders=0))
        self.assertEqual(a["owner_review_label"], AA.RL_NEGATIVE)

    def test_spend_below_threshold_is_not_high_spend_no_sales(self):
        a = self.analyze(norm_row(clicks=20, spend="1.00", sales="0.00", orders=0))
        self.assertEqual(a["primary_classification"], AA.CL_LOW_CONVERSION)

    def test_proven_converter(self):
        a = self.analyze(norm_row(clicks=20, spend="10.00", sales="35.00", orders=3))
        self.assertEqual(a["primary_classification"], AA.CL_PROVEN_CONVERTER)
        self.assertEqual(a["owner_review_label"], AA.RL_EXACT)

    def test_high_acos(self):
        a = self.analyze(norm_row(clicks=20, spend="30.00", sales="40.00", orders=2))
        self.assertEqual(a["primary_classification"], AA.CL_HIGH_ACOS)
        self.assertEqual(a["owner_review_label"], AA.RL_BID_BUDGET)
        self.assertIn(AA.RC_ACOS_ABOVE_HIGH, a["reason_codes"])

    def test_low_acos(self):
        a = self.analyze(norm_row(clicks=20, spend="4.00", sales="100.00", orders=4))
        self.assertEqual(a["primary_classification"], AA.CL_LOW_ACOS)
        self.assertEqual(a["owner_review_label"], AA.RL_EXACT)
        self.assertIn(AA.RC_ACOS_AT_OR_BELOW_LOW, a["reason_codes"])

    def test_insufficient_data_on_thin_clicks(self):
        a = self.analyze(norm_row(clicks=2, spend="1.00", sales="0.00", orders=0))
        self.assertEqual(a["primary_classification"], AA.CL_INSUFFICIENT_DATA)
        self.assertEqual(a["owner_review_label"], AA.RL_INSUFFICIENT)

    def test_promising_low_data_converts_on_thin_clicks(self):
        a = self.analyze(norm_row(clicks=1, spend="0.31", sales="38.40", orders=1))
        self.assertEqual(a["primary_classification"], AA.CL_PROMISING_LOW_DATA)
        self.assertEqual(a["owner_review_label"], AA.RL_MONITOR)

    def test_low_conversion(self):
        a = self.analyze(norm_row(clicks=30, spend="2.00", sales="0.00", orders=0))
        self.assertEqual(a["primary_classification"], AA.CL_LOW_CONVERSION)

    def test_acos_within_band_is_proven_converter(self):
        a = self.analyze(norm_row(clicks=20, spend="10.00", sales="33.00", orders=2))
        self.assertIn(AA.RC_ACOS_WITHIN_TARGET_BAND, a["reason_codes"])
        self.assertEqual(a["primary_classification"], AA.CL_PROVEN_CONVERTER)

    def test_every_classification_maps_to_a_review_label(self):
        for name in AA.CLASSIFICATIONS:
            self.assertIn(AA.REVIEW_LABEL_BY_CLASSIFICATION[name], AA.REVIEW_LABELS)

    def test_thresholds_drive_the_classification(self):
        row = norm_row(clicks=5, spend="1.00", sales="0.00", orders=0)
        self.assertEqual(self.analyze(row)["primary_classification"], AA.CL_INSUFFICIENT_DATA)
        loosened = self.analyze(row, minimum_clicks_for_conversion_judgment=3)
        self.assertEqual(loosened["primary_classification"], AA.CL_LOW_CONVERSION)

    def test_owner_target_acos_changes_the_acos_band(self):
        row = norm_row(clicks=20, spend="10.00", sales="33.00", orders=2)
        self.assertEqual(self.analyze(row)["primary_classification"], AA.CL_PROVEN_CONVERTER)
        strict = self.analyze(row, target_acos="0.10")
        self.assertEqual(strict["primary_classification"], AA.CL_HIGH_ACOS)


# ================================================================ 16. CLASSIFICATION PRECEDENCE
class TestClassificationPrecedence(Base):
    def test_precedence_order_is_declared(self):
        self.assertEqual(AA.CLASSIFICATION_PRECEDENCE, tuple(r[0] for r in AA.CLASSIFICATION_RULES))

    def test_exactly_one_primary_classification(self):
        a = self.analyze(norm_row(clicks=20, spend="25.00", sales="0.00", orders=0))
        self.assertIsInstance(a["primary_classification"], str)
        self.assertIn(a["primary_classification"], AA.CLASSIFICATIONS)

    def test_many_reason_codes_but_one_classification(self):
        a = self.analyze(norm_row(clicks=20, spend="25.00", sales="0.00", orders=0))
        self.assertGreater(len(a["reason_codes"]), 1)
        self.assertIn(a["primary_classification"], AA.CLASSIFICATIONS)

    def test_high_spend_no_sales_outranks_low_conversion(self):
        # this row satisfies BOTH rules; the earlier rule must win
        row = norm_row(clicks=50, spend="25.00", sales="0.00", orders=0)
        ev = self._evidence(row)
        self.assertTrue(AA._rule_high_spend_no_sales(ev, self.thresholds()))
        self.assertTrue(AA._rule_low_conversion(ev, self.thresholds()))
        self.assertEqual(self.analyze(row)["primary_classification"], AA.CL_HIGH_SPEND_NO_SALES)

    def test_no_clicks_outranks_high_spend_no_sales(self):
        row = norm_row(clicks=0, spend="25.00", sales="0.00", orders=0)
        self.assertEqual(self.analyze(row)["primary_classification"], AA.CL_NO_CLICKS)

    def test_promising_low_data_outranks_acos_judgment(self):
        # converted on 1 click: too little data to call the ACoS low
        row = norm_row(clicks=1, spend="0.31", sales="38.40", orders=1)
        self.assertEqual(self.analyze(row)["primary_classification"], AA.CL_PROMISING_LOW_DATA)

    def test_required_metric_unusable_outranks_everything(self):
        row = norm_row(clicks=0, spend="25.00", sales="0.00", orders=0)
        row["metrics"]["clicks"] = "bad"
        self.assertEqual(self.analyze(row)["primary_classification"], AA.CL_INSUFFICIENT_DATA)
        self.assertEqual(self.analyze(row)["classification_rule"], "REQUIRED_METRIC_UNUSABLE")

    def test_classification_is_repeatable(self):
        row = norm_row(clicks=20, spend="10.00", sales="35.00", orders=3)
        first = [self.analyze(row)["primary_classification"] for _ in range(20)]
        self.assertEqual(len(set(first)), 1)

    def test_rule_id_is_recorded_on_every_row(self):
        r = self.run73(self.fake_phase7_2([norm_row(), norm_row(search_term="b", clicks=0)]))
        for a in r.analysis["search_term_analysis"]:
            self.assertIn(a["classification_rule"], AA.CLASSIFICATION_PRECEDENCE)

    def _evidence(self, row):
        th = self.thresholds()
        a = AA.analyze_row(row, th)
        return {
            "impressions": a["impressions"], "clicks": a["clicks"],
            "spend": MONEY.parse_decimal_string(a["spend"], allow_missing=False),
            "sales": MONEY.parse_decimal_string(a["sales"], allow_missing=False),
            "orders": a["orders"],
            "acos": MONEY.parse_decimal_string(a["acos"]) if a["acos"] else None,
        }


# ================================================================ 17. DETERMINISTIC SORTING
class TestDeterministicSorting(Base):
    def _rows(self):
        return [norm_row(campaign=c, ad_group=g, search_term=t)
                for c in ("C2", "C1") for g in ("G2", "G1") for t in ("t2", "t1")]

    def test_search_term_sort_is_stable_under_shuffle(self):
        rows = self._rows()
        a = AA.sort_search_terms([self.analyze(r) for r in rows])
        shuffled = list(rows)
        random.Random(7).shuffle(shuffled)
        b = AA.sort_search_terms([self.analyze(r) for r in shuffled])
        self.assertEqual([x["canonical_row_key"] for x in a], [x["canonical_row_key"] for x in b])

    def test_search_term_sort_is_by_campaign_then_ad_group_then_term(self):
        out = AA.sort_search_terms([self.analyze(r) for r in self._rows()])
        keys = [(x["campaign"], x["ad_group"], x["customer_search_term"]) for x in out]
        self.assertEqual(keys, sorted(keys))

    def test_summary_sort_is_deterministic(self):
        rows = self._rows()
        first = AA.aggregate([self.analyze(r) for r in rows], ("campaign",))
        shuffled = list(rows)
        random.Random(11).shuffle(shuffled)
        second = AA.aggregate([self.analyze(r) for r in shuffled], ("campaign",))
        self.assertEqual(first, second)

    def test_decision_queue_order_is_deterministic(self):
        rows = [norm_row(search_term="neg", clicks=20, spend="25.00", sales="0.00", orders=0),
                norm_row(search_term="win", clicks=20, spend="4.00", sales="100.00", orders=4),
                norm_row(search_term="bid", clicks=20, spend="30.00", sales="40.00", orders=2)]
        analyzed = [self.analyze(r) for r in rows]
        queue = AA.sort_decision_queue([a for a in analyzed
                                        if a["owner_review_label"] in AA.DECISION_QUEUE_LABELS])
        labels = [q["owner_review_label"] for q in queue]
        self.assertEqual(labels, [AA.RL_NEGATIVE, AA.RL_EXACT, AA.RL_BID_BUDGET])
        shuffled = list(analyzed)
        random.Random(3).shuffle(shuffled)
        again = AA.sort_decision_queue([a for a in shuffled
                                        if a["owner_review_label"] in AA.DECISION_QUEUE_LABELS])
        self.assertEqual([q["canonical_row_key"] for q in queue],
                         [q["canonical_row_key"] for q in again])

    def test_null_fields_sort_without_raising(self):
        row = norm_row()
        row["identity"].pop("targeting_expression")
        row["key_basis"].pop("targeting")
        AA.sort_search_terms([self.analyze(row), self.analyze(norm_row())])


# ================================================================ 18. REPEATED RUN STABILITY
class TestRepeatedRunStability(Base):
    def test_repeated_run_is_byte_identical(self):
        base = self.real_phase7_2([{"search_term": f"t{i}"} for i in range(4)])
        work = self.mkbase()
        AA.run_analysis(work, base, reference_date=REF)
        first = self.snapshot(os.path.join(work, AA.D_PROMOTED))
        AA.run_analysis(work, base, reference_date=REF)
        self.assertEqual(self.snapshot(os.path.join(work, AA.D_PROMOTED)), first)

    def test_repeated_run_does_not_duplicate_rows(self):
        base = self.real_phase7_2([{"search_term": f"t{i}"} for i in range(4)])
        work = self.mkbase()
        AA.run_analysis(work, base, reference_date=REF)
        r2 = AA.run_analysis(work, base, reference_date=REF)
        self.assertEqual(len(r2.analysis["search_term_analysis"]), 4)
        self.assertEqual(r2.analysis["data_quality"]["analyzed_row_count"], 4)

    def test_content_hash_is_stable_across_workspaces(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        a = AA.run_analysis(self.mkbase(), base, reference_date=REF)
        b = AA.run_analysis(self.mkbase(), base, reference_date=REF)
        self.assertEqual(a.analysis["deterministic_content_sha256"],
                         b.analysis["deterministic_content_sha256"])

    def test_staging_is_emptied_after_promotion(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        r = self.run73(base)
        self.assertEqual([n for n in os.listdir(r.dirs[AA.D_STAGING])
                          if os.path.isfile(os.path.join(r.dirs[AA.D_STAGING], n))], [])

    def test_promoted_row_count_matches_source_row_count(self):
        base = self.real_phase7_2([{"search_term": f"t{i}"} for i in range(9)])
        r = self.run73(base)
        with open(os.path.join(r.dirs[AA.D_PROMOTED], AA.F_SEARCH_TERM_CSV), encoding="utf-8") as f:
            lines = [line for line in f.read().splitlines() if line.strip()]
        self.assertEqual(len(lines) - 1, 9)      # minus the header


# ================================================================ 19. MIXED CURRENCY SAFETY
class TestMixedCurrency(Base):
    def test_mixed_currency_blocks(self):
        base = self.fake_phase7_2([norm_row(search_term="a", currency="USD"),
                                   norm_row(search_term="b", currency="EUR")])
        r = self.run73(base)
        self.assertEqual(r.state, AA.CURRENCY_MIX_BLOCKED)

    def test_mixed_currency_writes_no_totals(self):
        base = self.fake_phase7_2([norm_row(search_term="a", currency="USD"),
                                   norm_row(search_term="b", currency="EUR")])
        r = self.run73(base)
        self.assertEqual(r.analysis["campaign_summary"], [])
        self.assertEqual(os.listdir(r.dirs[AA.D_PROMOTED]), [])

    def test_mixed_currency_names_both_currencies(self):
        base = self.fake_phase7_2([norm_row(search_term="a", currency="USD"),
                                   norm_row(search_term="b", currency="EUR")])
        r = self.run73(base)
        self.assertEqual(r.analysis["block_detail"]["currencies"], ["EUR", "USD"])

    def test_single_currency_is_preserved_on_every_row(self):
        r = self.run73(self.fake_phase7_2([norm_row(currency="USD")]))
        for a in r.analysis["search_term_analysis"]:
            self.assertEqual(a["currency"], "USD")

    def test_summaries_carry_the_currency(self):
        r = self.run73(self.fake_phase7_2([norm_row(currency="USD")]))
        for s in r.analysis["campaign_summary"] + r.analysis["ad_group_summary"]:
            self.assertEqual(s["currency"], "USD")

    def test_aggregate_never_merges_two_currencies(self):
        rows = [self.analyze(norm_row(search_term="a", currency="USD")),
                self.analyze(norm_row(search_term="b", currency="EUR"))]
        out = AA.aggregate(rows, ("campaign",))
        self.assertEqual(len(out), 2)     # same campaign name, two currency buckets
        self.assertEqual(sorted(s["currency"] for s in out), ["EUR", "USD"])


# ================================================================ 20. SOURCE LINEAGE
class TestSourceLineage(Base):
    def test_every_row_carries_full_lineage(self):
        base = self.real_phase7_2([{"search_term": f"t{i}"} for i in range(3)])
        r = self.run73(base)
        for a in r.analysis["search_term_analysis"]:
            self.assertTrue(a["source_file"])
            self.assertRegex(a["source_file_sha256"], r"^[0-9a-f]{64}$")
            self.assertIsInstance(a["source_row_number"], int)
            self.assertIsInstance(a["source_line_number"], int)
            self.assertTrue(a["canonical_row_key"])
            self.assertRegex(a["lineage_hash"], r"^[0-9a-f]{64}$")

    def test_lineage_is_written_to_the_csv(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        r = self.run73(base)
        with open(os.path.join(r.dirs[AA.D_PROMOTED], AA.F_SEARCH_TERM_CSV), encoding="utf-8") as f:
            text = f.read()
        for column in ("source_file", "source_file_sha256", "source_row_number",
                       "canonical_row_key", "lineage_hash"):
            self.assertIn(column, text.splitlines()[0])

    def test_manifest_records_the_source_identity(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        r = self.run73(base)
        src = r.manifest["source"]
        self.assertEqual(src["phase7_2_readiness"], RI.REPORTS_READY)
        self.assertRegex(src["phase7_2_manifest_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(src["phase7_2_verification"]["result"], "PASS")

    def test_source_row_number_points_at_the_original_export_row(self):
        base = self.real_phase7_2([{"search_term": "only"}])
        r = self.run73(base)
        # row 1 is the header, so the single data row is source row 2
        self.assertEqual(r.analysis["search_term_analysis"][0]["source_row_number"], 2)

    def test_source_file_sha_matches_the_promoted_artifact(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        r = self.run73(base)
        artifacts = {f["artifact"] for f in r.source["source_files"]}
        for a in r.analysis["search_term_analysis"]:
            self.assertIn(a["source_file"], artifacts)


# ================================================================ 21. NO PROHIBITED IMPORTS
class TestSecurity(Base):
    with open(os.path.join(ROOT, "production", "phase7_ads_analysis.py"), encoding="utf-8") as _f:
        MODULE_SRC = _f.read()

    PROHIBITED = ("requests", "httpx", "urllib", "http.client", "boto3", "botocore", "selenium",
                  "playwright", "pyppeteer", "puppeteer", "webdriver", "socket", "aiohttp",
                  "paramiko", "ftplib", "telnetlib", "smtplib", "xmlrpc", "sp_api",
                  "advertising-api", "amazon-ads", "seller-central", "sellercentral")

    def test_no_prohibited_imports(self):
        low = self.MODULE_SRC.lower()
        for banned in self.PROHIBITED:
            self.assertNotIn(banned, low, f"prohibited integration referenced: {banned}")

    def test_no_import_statement_outside_the_allowlist(self):
        allowed = {"csv", "datetime", "hashlib", "io", "json", "os", "sys", "tempfile", "decimal",
                   "argparse", "production", "core", "__future__"}
        for line in self.MODULE_SRC.splitlines():
            line = line.strip()
            if line.startswith("import ") or line.startswith("from "):
                root = line.split()[1].split(".")[0]
                self.assertIn(root, allowed, f"unexpected import: {line}")

    def test_no_subprocess_or_eval(self):
        low = self.MODULE_SRC.lower()
        for banned in ("subprocess", "os.system", "popen", "eval(", "exec(", "__import__"):
            self.assertNotIn(banned, low)

    def test_zero_action_counters_are_all_zero(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        r = self.run73(base)
        for key, value in r.analysis["amazon_boundary"].items():
            self.assertEqual(value, 0, key)

    def test_this_session_never_flags_all_true(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        r = self.run73(base)
        for key, value in r.analysis["this_session_never"].items():
            self.assertTrue(value, key)

    def test_no_amazon_endpoint_literal(self):
        low = self.MODULE_SRC.lower()
        for banned in ("https://", "http://", "www.", ".amazon.", "amazon.com"):
            self.assertNotIn(banned, low)


# ================================================================ 22. REVIEW LABELS ONLY
class TestReviewLabelsOnly(Base):
    # imperative instructions Phase 7.3 must NEVER emit
    FORBIDDEN_PHRASES = (
        "add this keyword", "add the keyword", "pause this", "pause the", "set bid",
        "set the bid", "reduce budget", "reduce the budget", "increase bid", "increase the bid",
        "lower the bid", "raise the bid", "add negative", "add a negative", "upload this",
        "upload the", "apply automatically", "automatically apply", "change the bid",
        "change the budget", "you should add", "you should pause", "recommended bid",
    )

    def _promoted_text(self):
        base = self.real_phase7_2([
            {"search_term": "neg", "clicks": 20, "spend": "$25.00", "sales": "$0.00"},
            {"search_term": "win", "clicks": 20, "spend": "$4.00", "sales": "$100.00", "orders": 4},
            {"search_term": "bid", "clicks": 20, "spend": "$30.00", "sales": "$40.00", "orders": 2},
        ])
        r = self.run73(base)
        text = ""
        for name in sorted(os.listdir(r.dirs[AA.D_PROMOTED])):
            with open(os.path.join(r.dirs[AA.D_PROMOTED], name), encoding="utf-8") as f:
                text += f.read().lower() + "\n"
        return r, text

    def test_no_action_instruction_in_any_output(self):
        _r, text = self._promoted_text()
        for phrase in self.FORBIDDEN_PHRASES:
            self.assertNotIn(phrase, text, f"output contains an action instruction: {phrase}")

    def test_every_label_is_a_declared_review_label(self):
        r, _text = self._promoted_text()
        for a in r.analysis["search_term_analysis"]:
            self.assertIn(a["owner_review_label"], AA.REVIEW_LABELS)

    def test_decision_queue_only_carries_review_labels(self):
        r, _text = self._promoted_text()
        for a in r.analysis["owner_decision_queue"]:
            self.assertIn(a["owner_review_label"], AA.DECISION_QUEUE_LABELS)

    def test_all_three_review_labels_are_reachable(self):
        r, _text = self._promoted_text()
        labels = {a["owner_review_label"] for a in r.analysis["owner_decision_queue"]}
        self.assertEqual(labels, set(AA.DECISION_QUEUE_LABELS))

    def test_analysis_declares_review_labels_only(self):
        r, _text = self._promoted_text()
        self.assertTrue(r.analysis["owner_review_labels_only"])

    def test_owner_report_states_the_owner_is_the_only_bridge(self):
        r, _text = self._promoted_text()
        with open(os.path.join(r.dirs[AA.D_PROMOTED], AA.F_OWNER_REPORT_MD), encoding="utf-8") as f:
            md = f.read()
        self.assertIn("owner remains the only manual bridge", md.lower())

    def test_no_upload_or_api_artifact_is_produced(self):
        r, _text = self._promoted_text()
        for name in os.listdir(r.dirs[AA.D_PROMOTED]):
            low = name.lower()
            for banned in ("upload", "payload", "api", "request", "amazon"):
                self.assertNotIn(banned, low)

    def test_neutral_default_target_acos_is_marked_owner_configurable(self):
        r, _text = self._promoted_text()
        self.assertEqual(r.thresholds.target_acos_source, "NEUTRAL_DEFAULT_OWNER_CONFIGURABLE")
        self.assertFalse(r.thresholds.owner_configured_target())
        with open(os.path.join(r.dirs[AA.D_PROMOTED], AA.F_OWNER_REPORT_MD), encoding="utf-8") as f:
            self.assertIn("owner-configurable", f.read().lower())


# ================================================================ 23. ATOMIC PROMOTION ROLLBACK
class TestAtomicPromotion(Base):
    def test_corrupted_staging_blocks_promotion(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        work = self.mkbase()
        r = AA.run_analysis(work, base, reference_date=REF)
        good = self.snapshot(os.path.join(work, AA.D_PROMOTED))
        # stage a second time, then corrupt one staged artifact before promoting
        docs = AA.build_documents(r)
        _m, hashes = AA.write_staging(docs, r.dirs[AA.D_STAGING], r)
        with open(os.path.join(r.dirs[AA.D_STAGING], AA.F_ANALYSIS_JSON), "ab") as f:
            f.write(b"corrupt")
        report = AA.promote_staging(r.dirs[AA.D_STAGING], r.dirs[AA.D_PROMOTED], hashes)
        self.assertEqual(report["result"], "BLOCKED")
        self.assertFalse(report["promoted"])
        self.assertIn(AA.F_ANALYSIS_JSON, report["mismatched"])
        self.assertEqual(self.snapshot(os.path.join(work, AA.D_PROMOTED)), good)

    def test_missing_staged_artifact_blocks_promotion(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        work = self.mkbase()
        r = AA.run_analysis(work, base, reference_date=REF)
        good = self.snapshot(os.path.join(work, AA.D_PROMOTED))
        docs = AA.build_documents(r)
        _m, hashes = AA.write_staging(docs, r.dirs[AA.D_STAGING], r)
        os.remove(os.path.join(r.dirs[AA.D_STAGING], AA.F_CAMPAIGN_CSV))
        report = AA.promote_staging(r.dirs[AA.D_STAGING], r.dirs[AA.D_PROMOTED], hashes)
        self.assertEqual(report["result"], "BLOCKED")
        self.assertEqual(self.snapshot(os.path.join(work, AA.D_PROMOTED)), good)

    def test_verify_staging_passes_on_a_clean_stage(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        work = self.mkbase()
        r = AA.run_analysis(work, base, reference_date=REF)
        docs = AA.build_documents(r)
        _m, hashes = AA.write_staging(docs, r.dirs[AA.D_STAGING], r)
        self.assertEqual(AA.verify_staging(r.dirs[AA.D_STAGING], hashes)["result"], "PASS")

    def test_promotion_reports_pass_and_promoted(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        r = self.run73(base)
        self.assertEqual(r.promote_report["result"], "PASS")
        self.assertTrue(r.promote_report["promoted"])

    def test_manifest_is_promoted_last(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        r = self.run73(base)
        self.assertIn(AA.F_MANIFEST, os.listdir(r.dirs[AA.D_PROMOTED]))


# ================================================================ 24. REAL-SCHEMA REGRESSION
class TestRealSchemaRegression(Base):
    """The fixture uses the EXACT real Amazon search-term export header layout — trailing spaces,
    '(#)' suffixes, '$' money, '%' rates, 'Mmm d, yyyy' dates — ingested by the real Phase 7.2
    engine, so this pins Phase 7.3 against what Phase 7.2 actually produces in production."""

    def test_real_header_layout_is_classified_as_search_term(self):
        base = self.real_phase7_2([{"search_term": "nurse sweatshirt"}])
        manifest = RI.read_promoted_manifest(os.path.join(base, "final"))
        self.assertIn(RI.NORMALIZED_FILE[RI.SP_SEARCH_TERM], manifest["required_artifacts"])

    def test_real_schema_row_analyses_end_to_end(self):
        base = self.real_phase7_2([
            {"search_term": "nurse sweatshirt", "impressions": 800, "clicks": 20,
             "spend": "$10.00", "sales": "$35.00", "orders": 3, "units": 3}])
        r = self.run73(base)
        self.assertEqual(r.state, AA.ANALYSIS_READY)
        a = r.analysis["search_term_analysis"][0]
        self.assertEqual(a["impressions"], 800)
        self.assertEqual(a["clicks"], 20)
        self.assertEqual(a["spend"], "10.00")
        self.assertEqual(a["sales"], "35.00")
        self.assertEqual(a["orders"], 3)
        self.assertEqual(a["units"], 3)
        self.assertEqual(a["currency"], "USD")
        self.assertEqual(a["primary_classification"], AA.CL_PROVEN_CONVERTER)

    def test_real_schema_derived_metrics_are_decimal_exact(self):
        base = self.real_phase7_2([
            {"search_term": "t", "impressions": 800, "clicks": 20, "spend": "$10.00",
             "sales": "$35.00", "orders": 3}])
        a = self.run73(base).analysis["search_term_analysis"][0]
        self.assertEqual(a["ctr"], "0.025000")            # 20 / 800
        self.assertEqual(a["cpc"], "0.50")                # 10.00 / 20
        self.assertEqual(a["conversion_rate"], "0.150000")  # 3 / 20
        self.assertEqual(a["acos"], "0.285714")           # 10.00 / 35.00
        self.assertEqual(a["roas"], "3.500000")           # 35.00 / 10.00

    def test_real_schema_dash_match_type_is_not_applicable(self):
        base = self.real_phase7_2([{"search_term": "t", "match_type": "-"}])
        a = self.run73(base).analysis["search_term_analysis"][0]
        self.assertIsNone(a["match_type"])
        self.assertIn(AA.RC_MATCH_TYPE_NOT_APPLICABLE, a["reason_codes"])

    def test_real_schema_explicit_match_type_survives(self):
        base = self.real_phase7_2([{"search_term": "t", "match_type": "exact"}])
        a = self.run73(base).analysis["search_term_analysis"][0]
        self.assertEqual(a["match_type"], "EXACT")

    def test_real_schema_textual_dates_normalize(self):
        base = self.real_phase7_2([{"search_term": "t", "start": "Jun 11, 2026",
                                    "end": "Jun 11, 2026"}])
        a = self.run73(base).analysis["search_term_analysis"][0]
        self.assertEqual(a["start_date"], "2026-06-11")
        self.assertEqual(a["end_date"], "2026-06-11")

    def test_real_schema_zero_sales_row_keeps_acos_null(self):
        base = self.real_phase7_2([{"search_term": "t", "clicks": 1, "spend": "$0.18",
                                    "sales": "$0.00"}])
        a = self.run73(base).analysis["search_term_analysis"][0]
        self.assertIsNone(a["acos"])
        self.assertEqual(a["spend"], "0.18")

    def test_real_schema_multi_row_export_totals(self):
        base = self.real_phase7_2([
            {"search_term": "a", "impressions": 10, "clicks": 1, "spend": "$0.10"},
            {"search_term": "b", "impressions": 20, "clicks": 2, "spend": "$0.20"},
            {"search_term": "c", "impressions": 30, "clicks": 3, "spend": "$0.30"}])
        r = self.run73(base)
        summary = r.analysis["campaign_summary"][0]
        self.assertEqual(summary["impressions"], 60)
        self.assertEqual(summary["clicks"], 6)
        self.assertEqual(summary["spend"], "0.60")
        self.assertEqual(summary["analyzed_rows"], 3)

    def test_real_schema_lookback_window_is_reported(self):
        base = self.real_phase7_2([{"search_term": "old", "start": "2026-06-01",
                                    "end": "2026-06-01"}])
        r = self.run73(base)
        dq = r.analysis["data_quality"]
        self.assertEqual(dq["date_coverage"]["rows_outside_lookback_window"], 1)
        self.assertEqual(dq["date_coverage"]["lookback_start"], "2026-06-19")

    def test_real_schema_data_quality_is_complete(self):
        base = self.real_phase7_2([{"search_term": "a"}])
        dq = self.run73(base).analysis["data_quality"]
        for field in ("source_manifest_identity", "source_file_count", "source_row_count",
                      "analyzed_row_count", "skipped_row_count", "missing_field_counts",
                      "duplicate_handling", "zero_denominator_counts", "invalid_numeric_count",
                      "date_coverage", "currencies_encountered"):
            self.assertIn(field, dq)


if __name__ == "__main__":
    unittest.main()
