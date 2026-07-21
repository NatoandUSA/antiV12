#!/usr/bin/env python3
"""Phase 7.4 — offline Owner Review Dashboard.

Every test runs OFFLINE and asserts the permanent Amazon boundary throughout: the dashboard reads
only promoted Phase 7.3 output, connects to nothing, performs no Amazon action, and exports locally.
Synthetic fixtures only — no real T2 report data is committed. The one live HTTP server used for the
security tests binds to 127.0.0.1 on an ephemeral port and is torn down in tearDown.
"""
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from production import phase7_owner_dashboard as D          # noqa: E402
from production import phase7_ads_analysis as AA            # noqa: E402

FIXED_NOW = lambda: datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)  # noqa: E731

PLACEHOLDER_CSVS = ("campaign-summary.csv", "ad-group-summary.csv", "search-term-analysis.csv",
                    "owner-decision-queue.csv")
PLACEHOLDER_MD = "owner-report.md"


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def make_row(idx, **kw):
    """One phase7-3-search-term-analysis-v1 row with a unique lineage_hash."""
    ev = kw.get("evidence", {
        "metric_states": {"clicks": "PRESENT", "impressions": "PRESENT", "spend": "PRESENT",
                          "orders_7d": "PRESENT", "sales_7d": "PRESENT", "units_7d": "PRESENT"},
        "target_acos": "0.300000", "target_acos_source": "NEUTRAL_DEFAULT_OWNER_CONFIGURABLE",
        "high_acos_threshold": "0.375000", "low_acos_threshold": "0.225000",
        "outside_lookback_window": False, "thresholds_policy_id": "phase7-3-analysis-thresholds-v1",
        "minimum_clicks_for_conversion_judgment": 10, "minimum_spend_for_no_sale_risk": "5.00"})
    row = {
        "schema_version": "phase7-3-search-term-analysis-v1",
        "campaign": kw.get("campaign", "Camp A"),
        "ad_group": kw.get("ad_group", "AG 1"),
        "targeting": kw.get("targeting", "exact-kw"),
        "match_type": kw.get("match_type", None),
        "customer_search_term": kw.get("customer_search_term", "term%02d" % idx),
        "currency": kw.get("currency", "USD"),
        "start_date": kw.get("start_date", "2026-06-10"),
        "end_date": kw.get("end_date", "2026-06-12"),
        "impressions": kw.get("impressions", 100),
        "clicks": kw.get("clicks", 10),
        "spend": kw.get("spend", "5.00"),
        "orders": kw.get("orders", 1),
        "sales": kw.get("sales", "20.00"),
        "units": kw.get("units", 1),
        "ctr": kw.get("ctr", "0.100000"),
        "cpc": kw.get("cpc", "0.50"),
        "conversion_rate": kw.get("conversion_rate", "0.100000"),
        "acos": kw.get("acos", "0.250000"),
        "roas": kw.get("roas", "4.000000"),
        "primary_classification": kw.get("primary_classification", "INSUFFICIENT_DATA"),
        "classification_rule": kw.get("classification_rule", "LOW_CLICK_VOLUME"),
        "reason_codes": kw.get("reason_codes", ["ZERO_SALES", "CLICKS_BELOW_CONVERSION_JUDGMENT_MINIMUM"]),
        "owner_review_label": kw.get("owner_review_label", "INSUFFICIENT_EVIDENCE"),
        "evidence": ev,
        "source_file": "PHASE7-SP-SEARCH-TERM-NORMALIZED.jsonl",
        "source_file_sha256": "0" * 64,
        "source_row_number": idx,
        "source_line_number": idx + 1,
        "canonical_row_key": kw.get("canonical_row_key", "key-%03d" % idx),
        "lineage_hash": kw.get("lineage_hash", _sha(("lin-%03d" % idx).encode())),
    }
    return row


def build_analysis(rows, *, readiness=None, currencies=("USD",), decision_queue=None,
                   dq_overrides=None):
    readiness = readiness or AA.ANALYSIS_READY
    queue = decision_queue if decision_queue is not None else \
        [r for r in rows if r["owner_review_label"] in AA.DECISION_QUEUE_LABELS]
    dq = {
        "schema_version": "phase7-3-data-quality-v1",
        "source_row_count": len(rows), "analyzed_row_count": len(rows),
        "skipped_row_count": 0, "blocked_row_count": 0,
        "currencies_encountered": list(currencies),
        "missing_field_counts": {},
        "invalid_numeric_count": 0,
        "zero_denominator_counts": {"ctr": 0, "cpc": 0, "conversion_rate": 0, "acos": 0, "roas": 0},
        "duplicate_handling": {"policy": "P", "distinct_canonical_row_keys": len({r["canonical_row_key"] for r in rows}),
                               "duplicate_canonical_row_key_count": 0},
        "source_file_count": 1,
        "source_files": [{"artifact": "PHASE7-SP-SEARCH-TERM-NORMALIZED.jsonl", "byte_length": 1,
                          "report_type": "SP_SEARCH_TERM", "sha256": "0" * 64}],
        "source_manifest_identity": {"stage_id": "7.2", "schema_version": "phase7-2-report-manifest-v1",
                                     "analysis_readiness": "SESSION7_2_REPORTS_READY_FOR_ANALYSIS",
                                     "deterministic_content_sha256": "abc"},
        "date_coverage": {"earliest": "2026-06-10", "latest": "2026-06-12", "distinct_dates": 3,
                          "lookback_days": 30, "lookback_start": "2026-06-21",
                          "reference_date": "2026-07-21", "rows_outside_lookback_window": 0},
    }
    if dq_overrides:
        dq.update(dq_overrides)
    a = {
        "schema_version": "phase7-3-analysis-v1", "stage_id": "7.3", "stage_name": AA.STAGE_NAME,
        "analysis_readiness": readiness,
        "supported_report_types": ["SP_SEARCH_TERM"], "analysed_report_types": ["SP_SEARCH_TERM"],
        "owner_review_labels_only": True,
        "classification_precedence": list(AA.CLASSIFICATIONS),
        "campaign_summary": AA.aggregate(rows, ["campaign"]),
        "ad_group_summary": AA.aggregate(rows, ["campaign", "ad_group"]),
        "search_term_analysis": AA.sort_search_terms(list(rows)),
        "owner_decision_queue": queue,
        "classification_counts": dict(Counter(r["primary_classification"] for r in rows)),
        "review_label_counts": dict(Counter(r["owner_review_label"] for r in rows)),
        "thresholds": dict(AA.DEFAULT_THRESHOLDS),
        "data_quality": dq,
        "amazon_boundary": dict(AA._ZERO_COUNTERS),
        "this_session_never": dict(AA._THIS_SESSION_NEVER),
    }
    AA._finalize(a)
    return a


def _manifest(analysis, docs, output_hashes):
    dq = analysis["data_quality"]
    body = {
        "schema_version": "phase7-3-analysis-manifest-v1", "stage_id": "7.3",
        "stage_name": AA.STAGE_NAME, "analysis_readiness": analysis["analysis_readiness"],
        "source": {"phase7_2_final_dir": "final", "phase7_2_readiness": "SESSION7_2_REPORTS_READY_FOR_ANALYSIS",
                   "phase7_2_manifest_sha256": "deadbeef", "raw_inbox_read": False,
                   "source_files": dq["source_files"]},
        "counts": {"source_row_count": dq["source_row_count"], "analyzed_row_count": dq["analyzed_row_count"],
                   "skipped_row_count": dq["skipped_row_count"], "blocked_row_count": dq["blocked_row_count"],
                   "decision_queue_row_count": len(analysis["owner_decision_queue"]),
                   "campaign_count": len(analysis["campaign_summary"]),
                   "ad_group_count": len(analysis["ad_group_summary"])},
        "classification_counts": analysis["classification_counts"],
        "review_label_counts": analysis["review_label_counts"], "thresholds": analysis["thresholds"],
        "required_artifacts": sorted(list(docs) + [D.F_MANIFEST]),
        "generated_outputs": sorted(docs),
        "stable_content_hashes": dict(output_hashes),
        "amazon_boundary": dict(AA._ZERO_COUNTERS), "this_session_never": dict(AA._THIS_SESSION_NEVER),
        "output_hashes": output_hashes,
    }
    AA._finalize(body, extra_volatile=("output_hashes",))
    return body


def write_promoted(base, analysis=None, *, analysis_bytes=None):
    """Write a valid promoted Phase 7.3 directory (analysis + CSV/md placeholders + manifest)."""
    promoted = os.path.join(base, "7.3", "promoted")
    os.makedirs(promoted, exist_ok=True)
    if analysis_bytes is None:
        analysis_bytes = D.canonical_json(analysis).encode("utf-8")
    docs = {"analysis.json": analysis_bytes}
    for name in PLACEHOLDER_CSVS:
        docs[name] = ("col\nvalue\n").encode("utf-8")
    docs[PLACEHOLDER_MD] = b"# Owner report\n"
    output_hashes = {n: _sha(b) for n, b in docs.items()}
    manifest = _manifest(analysis or {"data_quality": {"source_row_count": 0, "analyzed_row_count": 0,
                                                        "skipped_row_count": 0, "blocked_row_count": 0,
                                                        "source_files": []},
                                      "campaign_summary": [], "ad_group_summary": [],
                                      "owner_decision_queue": [], "classification_counts": {},
                                      "review_label_counts": {}, "thresholds": {},
                                      "analysis_readiness": AA.ANALYSIS_READY}, docs, output_hashes)
    for name, b in docs.items():
        with open(os.path.join(promoted, name), "wb") as f:
            f.write(b)
    with open(os.path.join(promoted, D.F_MANIFEST), "wb") as f:
        f.write(D.canonical_json(manifest).encode("utf-8"))
    return os.path.join(base, "7.3")


def reseal(promoted):
    """Recompute output hashes from the files on disk and re-finalize the manifest. Used to keep a
    deliberately edited artifact internally consistent so a CONTENT validator (not the hash) fires."""
    mpath = os.path.join(promoted, D.F_MANIFEST)
    manifest = json.loads(open(mpath, encoding="utf-8").read())
    docs = {}
    for name in list(manifest["output_hashes"]):
        with open(os.path.join(promoted, name), "rb") as f:
            docs[name] = f.read()
    manifest["output_hashes"] = {n: _sha(b) for n, b in sorted(docs.items())}
    manifest["stable_content_hashes"] = dict(manifest["output_hashes"])
    AA._finalize(manifest, extra_volatile=("output_hashes",))
    with open(mpath, "wb") as f:
        f.write(D.canonical_json(manifest).encode("utf-8"))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p74-")
        self.base_dir = os.path.join(self.tmp, "7.4")
        self._servers = []

    def tearDown(self):
        for s in self._servers:
            try:
                s.shutdown()
                s.server_close()
            except Exception:
                pass

    def rows(self, n=3, **kw):
        return [make_row(i, **kw) for i in range(n)]

    def valid_source(self, rows=None, **kw):
        rows = self.rows() if rows is None else rows
        analysis = build_analysis(rows, **kw)
        return write_promoted(self.tmp, analysis)

    def model(self, phase7_3_dir):
        src = D.load_source(phase7_3_dir)
        return src, D.build_model(self.base_dir, src, now=FIXED_NOW)

    def serve(self, phase7_3_dir):
        srv = D.build_server(self.base_dir, phase7_3_dir, host="127.0.0.1", port=0, now=FIXED_NOW)
        self._servers.append(srv)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return "http://127.0.0.1:%d" % srv.server_address[1]


def http(url, *, method="GET", data=None, ctype="application/json", headers=None):
    req = urllib.request.Request(url, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    if data is not None:
        req.add_header("Content-Type", ctype)
        req.data = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


# ================================================================ source loading & validation
class SourceLoading(Base):
    def test_valid_source_loads(self):
        src, model = self.model(self.valid_source())
        self.assertTrue(src.ok)
        self.assertEqual(src.readiness, D.DASH_READY)
        self.assertEqual(src.verification["result"], "PASS")
        self.assertTrue(model["ok"])

    def test_source_directory_required(self):
        src = D.load_source(os.path.join(self.tmp, "nope", "7.3"))
        self.assertFalse(src.ok)
        self.assertEqual(src.readiness, D.SOURCE_REQUIRED)

    def test_missing_manifest_blocks(self):
        s = self.valid_source()
        os.remove(os.path.join(s, "promoted", D.F_MANIFEST))
        src = D.load_source(s)
        self.assertFalse(src.ok)
        self.assertEqual(src.readiness, D.SOURCE_REQUIRED)

    def test_invalid_manifest_schema_blocks(self):
        s = self.valid_source()
        mp = os.path.join(s, "promoted", D.F_MANIFEST)
        m = json.loads(open(mp, encoding="utf-8").read())
        m["schema_version"] = "wrong"
        open(mp, "w", encoding="utf-8").write(D.canonical_json(m))
        src = D.load_source(s)
        self.assertFalse(src.ok)
        self.assertEqual(src.readiness, D.SOURCE_BLOCKED)
        self.assertEqual(src.reasons[0]["code"], "MANIFEST_SCHEMA_UNEXPECTED")

    def test_tampered_manifest_blocks(self):
        s = self.valid_source()
        mp = os.path.join(s, "promoted", D.F_MANIFEST)
        m = json.loads(open(mp, encoding="utf-8").read())
        m["counts"]["source_row_count"] = 999
        open(mp, "w", encoding="utf-8").write(D.canonical_json(m))
        src = D.load_source(s)
        self.assertFalse(src.ok)
        self.assertEqual(src.readiness, D.SOURCE_BLOCKED)
        self.assertEqual(src.reasons[0]["code"], "SOURCE_INTEGRITY_FAILED")

    def test_tampered_artifact_blocks(self):
        s = self.valid_source()
        with open(os.path.join(s, "promoted", "campaign-summary.csv"), "ab") as f:
            f.write(b"TAMPER")
        src = D.load_source(s)
        self.assertFalse(src.ok)
        self.assertIn("campaign-summary.csv", src.verification["mismatched"])

    def test_missing_artifact_blocks(self):
        s = self.valid_source()
        os.remove(os.path.join(s, "promoted", "search-term-analysis.csv"))
        src = D.load_source(s)
        self.assertFalse(src.ok)
        self.assertIn("search-term-analysis.csv", src.verification["missing"])

    def test_malformed_json_blocks(self):
        s = self.valid_source()
        with open(os.path.join(s, "promoted", "analysis.json"), "wb") as f:
            f.write(b"{ this is not valid json ]")
        reseal(os.path.join(s, "promoted"))
        src = D.load_source(s)
        self.assertFalse(src.ok)
        self.assertEqual(src.reasons[0]["code"], "MALFORMED_JSON")

    def test_duplicate_ids_block(self):
        rows = self.rows(2)
        rows[1]["lineage_hash"] = rows[0]["lineage_hash"]
        s = self.valid_source(rows=rows)
        src = D.load_source(s)
        self.assertFalse(src.ok)
        self.assertEqual(src.reasons[0]["code"], "DUPLICATE_STABLE_ID")

    def test_nan_blocks(self):
        rows = self.rows()
        analysis = build_analysis(rows)
        analysis["search_term_analysis"][0]["roas"] = float("nan")
        raw = json.dumps(analysis, allow_nan=True).encode("utf-8")
        s = write_promoted(self.tmp, analysis, analysis_bytes=raw)
        reseal(os.path.join(s, "promoted"))
        src = D.load_source(s)
        self.assertFalse(src.ok)
        self.assertEqual(src.reasons[0]["code"], "NON_FINITE_NUMBER_REJECTED")

    def test_infinity_blocks(self):
        rows = self.rows()
        analysis = build_analysis(rows)
        analysis["search_term_analysis"][0]["acos"] = float("inf")
        raw = json.dumps(analysis, allow_nan=True).encode("utf-8")
        s = write_promoted(self.tmp, analysis, analysis_bytes=raw)
        reseal(os.path.join(s, "promoted"))
        src = D.load_source(s)
        self.assertFalse(src.ok)
        self.assertEqual(src.reasons[0]["code"], "NON_FINITE_NUMBER_REJECTED")

    def test_null_byte_blocks(self):
        s = self.valid_source()
        with open(os.path.join(s, "promoted", "analysis.json"), "wb") as f:
            f.write(b'{"schema_version":"phase7-3-analysis-v1\x00"}')
        reseal(os.path.join(s, "promoted"))
        src = D.load_source(s)
        self.assertFalse(src.ok)
        self.assertEqual(src.reasons[0]["code"], "NULL_BYTE_REJECTED")

    def test_unsafe_artifact_path_rejected(self):
        with self.assertRaises(D.SourceError):
            D._assert_inside(os.path.join(self.tmp, "promoted"), "../secret")

    def test_source_analyzed_consistency_enforced(self):
        rows = self.rows(3)
        analysis = build_analysis(rows, dq_overrides={"source_row_count": 5})
        s = write_promoted(self.tmp, analysis)
        reseal(os.path.join(s, "promoted"))
        src = D.load_source(s)
        self.assertFalse(src.ok)
        self.assertEqual(src.reasons[0]["code"], "COUNT_INCONSISTENT")

    def test_analyzed_rows_match_row_list(self):
        rows = self.rows(3)
        analysis = build_analysis(rows, dq_overrides={"analyzed_row_count": 2, "source_row_count": 2})
        s = write_promoted(self.tmp, analysis)
        reseal(os.path.join(s, "promoted"))
        src = D.load_source(s)
        self.assertFalse(src.ok)
        self.assertIn(src.reasons[0]["code"], ("ANALYZED_COUNT_MISMATCH", "MANIFEST_ANALYSIS_COUNT_MISMATCH"))

    def test_wrong_analysis_schema_blocks(self):
        rows = self.rows()
        analysis = build_analysis(rows)
        analysis["schema_version"] = "phase7-9-bogus"
        s = write_promoted(self.tmp, analysis)
        reseal(os.path.join(s, "promoted"))
        src = D.load_source(s)
        self.assertFalse(src.ok)
        self.assertEqual(src.reasons[0]["code"], "ANALYSIS_SCHEMA_UNEXPECTED")

    def test_final_dir_naming_supported(self):
        # a directory that contains the manifest directly is accepted (fixtures / alt layouts).
        s = self.valid_source()
        resolved = D.resolve_source_dir(os.path.join(s, "promoted"))
        self.assertTrue(os.path.isfile(os.path.join(resolved, D.F_MANIFEST)))


# ================================================================ views & empty state
class Views(Base):
    def test_empty_queue_dashboard_ready(self):
        src, model = self.model(self.valid_source())  # default rows -> no actionable labels
        self.assertTrue(model["ok"])
        self.assertEqual(model["overview"]["decision_queue_row_count"], 0)
        self.assertEqual(model["recommendations"], [])
        self.assertTrue(model["campaigns"])           # rest of the dashboard is populated
        self.assertTrue(model["search_terms"])

    def test_populated_queue(self):
        rows = self.rows(2) + [make_row(9, owner_review_label="REVIEW_FOR_MANUAL_NEGATIVE",
                                        primary_classification="HIGH_SPEND_NO_SALES",
                                        customer_search_term="wasteful")]
        src, model = self.model(self.valid_source(rows=rows))
        self.assertEqual(len(model["recommendations"]), 1)
        self.assertEqual(model["recommendations"][0]["recommendation_type"], "REVIEW_FOR_MANUAL_NEGATIVE")

    def test_blocked_recommendations_view(self):
        rows = self.rows(2) + [make_row(9, primary_classification="NEEDS_OWNER_REVIEW",
                                        reason_codes=["REQUIRED_METRIC_UNUSABLE"],
                                        customer_search_term="broken")]
        src, model = self.model(self.valid_source(rows=rows))
        self.assertEqual(len(model["blocked_recommendations"]), 1)
        self.assertEqual(model["blocked_recommendations"][0]["blocked_item"], "broken")

    def test_campaign_ad_group_target_counts(self):
        rows = [make_row(0, campaign="C1", ad_group="G1"),
                make_row(1, campaign="C1", ad_group="G2"),
                make_row(2, campaign="C2", ad_group="G3")]
        src, model = self.model(self.valid_source(rows=rows))
        self.assertEqual(model["overview"]["campaign_count"], 2)
        self.assertEqual(model["overview"]["ad_group_count"], 3)
        self.assertEqual(model["overview"]["targeting_count"], len(model["targets"]))

    def test_targets_use_phase7_3_authority(self):
        rows = [make_row(0, targeting="kw-x", spend="1.00", clicks=2, impressions=10),
                make_row(1, targeting="kw-x", spend="2.00", clicks=3, impressions=20)]
        src, model = self.model(self.valid_source(rows=rows))
        tgt = [t for t in model["targets"] if t["targeting"] == "kw-x"][0]
        self.assertEqual(tgt["spend"], "3.00")     # Decimal sum, exact
        self.assertEqual(tgt["clicks"], 5)

    def test_observations_rollup(self):
        src, model = self.model(self.valid_source())
        codes = {o["reason_code"] for o in model["observations"]}
        self.assertIn("ZERO_SALES", codes)

    def test_policy_requirements_present(self):
        src, model = self.model(self.valid_source())
        reqs = {p["requirement"] for p in model["policy_requirements"]}
        self.assertTrue(any("target ACoS" in r for r in reqs))

    def test_missing_values_stay_missing(self):
        rows = [make_row(0, orders=None, sales=None, units=None, acos=None)]
        src, model = self.model(self.valid_source(rows=rows))
        st = model["search_terms"][0]
        self.assertIsNone(st["orders"])
        self.assertIsNone(st["acos"])

    def test_decimal_values_remain_strings(self):
        src, model = self.model(self.valid_source())
        self.assertIsInstance(model["campaigns"][0]["spend"], str)
        self.assertIsInstance(model["search_terms"][0]["spend"], str)

    def test_no_float_in_analytical_pipeline(self):
        src, model = self.model(self.valid_source())
        for view in ("campaigns", "ad_groups", "targets", "search_terms"):
            for row in model[view]:
                for field in ("spend", "sales", "cpc", "acos", "roas", "ctr", "conversion_rate"):
                    self.assertNotIsInstance(row.get(field), float, f"{view}.{field} is a float")

    def test_multiple_campaigns_and_ad_groups(self):
        rows = [make_row(i, campaign="C%d" % (i % 3), ad_group="G%d" % i) for i in range(6)]
        src, model = self.model(self.valid_source(rows=rows))
        self.assertEqual(model["overview"]["campaign_count"], 3)

    def test_multiple_currencies_surface_without_summing(self):
        # a ready analysis is single-currency; the health view still surfaces whatever it declares.
        rows = self.rows()
        analysis = build_analysis(rows, currencies=("USD", "CAD"))
        s = write_promoted(self.tmp, analysis)
        reseal(os.path.join(s, "promoted"))
        src, model = self.model(s)
        self.assertEqual(model["data_health"]["currencies"], ["USD", "CAD"])

    def test_attribution_window_derived(self):
        src, model = self.model(self.valid_source())
        self.assertEqual(model["overview"]["attribution_windows"], ["7 day"])


# ================================================================ deterministic ordering
class Determinism(Base):
    def test_model_is_deterministic(self):
        s = self.valid_source(rows=self.rows(5))
        _, m1 = self.model(s)
        _, m2 = self.model(s)
        self.assertEqual(D.canonical_json(m1), D.canonical_json(m2))

    def test_export_content_deterministic(self):
        rows = self.rows(4)
        s = self.valid_source(rows=rows)
        src = D.load_source(s)
        views = D.build_views(src)
        rec = D.merge_review_state(self.base_dir, src, views)
        base2 = os.path.join(self.tmp, "7.4b")
        e1 = D.build_export(self.base_dir, src, views, rec, fmt="json", now=FIXED_NOW)
        e2 = D.build_export(base2, src, views, rec, fmt="json", now=FIXED_NOW)
        self.assertEqual(e1["content"], e2["content"])

    def test_api_json_ordering_stable(self):
        base = self.serve(self.valid_source(rows=self.rows(5)))
        b1 = http(base + "/api/campaigns")[2]
        b2 = http(base + "/api/campaigns")[2]
        self.assertEqual(b1, b2)


# ================================================================ review state
class ReviewState(Base):
    def _one_entity(self, model, view="search_terms"):
        return model[view][0]["entity_id"]

    def test_save_and_reload_status(self):
        s = self.valid_source()
        src, model = self.model(s)
        eid = self._one_entity(model)
        views = D.build_views(src)
        D.save_review(self.base_dir, src, views,
                      [{"entity_id": eid, "review_status": "APPROVED_FOR_MANUAL_ACTION",
                        "owner_note": "looks good"}], now=FIXED_NOW)
        rec = D.merge_review_state(self.base_dir, src, views)[eid]
        self.assertEqual(rec["review_status"], "APPROVED_FOR_MANUAL_ACTION")
        self.assertEqual(rec["owner_note"], "looks good")

    def test_review_state_survives_restart(self):
        s = self.valid_source()
        src, model = self.model(s)
        eid = self._one_entity(model)
        views = D.build_views(src)
        D.save_review(self.base_dir, src, views, [{"entity_id": eid, "review_status": "DEFERRED"}], now=FIXED_NOW)
        # a fresh load from disk
        src2 = D.load_source(s)
        rec = D.merge_review_state(self.base_dir, src2, D.build_views(src2))[eid]
        self.assertEqual(rec["review_status"], "DEFERRED")

    def test_bulk_save(self):
        s = self.valid_source(rows=self.rows(4))
        src, model = self.model(s)
        views = D.build_views(src)
        eids = [r["entity_id"] for r in model["search_terms"][:3]]
        applied = D.save_review(self.base_dir, src, views,
                                [{"entity_id": e, "review_status": "REJECTED"} for e in eids], now=FIXED_NOW)
        self.assertEqual(len(applied), 3)

    def test_invalid_status_rejected(self):
        s = self.valid_source()
        src, model = self.model(s)
        views = D.build_views(src)
        with self.assertRaises(D.DashboardError):
            D.save_review(self.base_dir, src, views,
                          [{"entity_id": self._one_entity(model), "review_status": "SEND_TO_AMAZON"}],
                          now=FIXED_NOW)

    def test_unknown_entity_rejected(self):
        s = self.valid_source()
        src, _ = self.model(s)
        views = D.build_views(src)
        with self.assertRaises(D.DashboardError):
            D.save_review(self.base_dir, src, views,
                          [{"entity_id": "st:nonexistent", "review_status": "REJECTED"}], now=FIXED_NOW)

    def test_stable_id_and_hashes_stored(self):
        s = self.valid_source()
        src, model = self.model(s)
        eid = self._one_entity(model)
        views = D.build_views(src)
        rec = D.save_review(self.base_dir, src, views,
                            [{"entity_id": eid, "review_status": "APPROVED_FOR_MANUAL_ACTION"}], now=FIXED_NOW)[0]
        self.assertEqual(rec["entity_id"], eid)
        self.assertEqual(rec["source_manifest_sha256"], src.manifest_hash)
        self.assertTrue(rec["content_sha256"])
        self.assertEqual(rec["revision"], 1)

    def test_unchanged_recommendation_preserves_review(self):
        s = self.valid_source(rows=self.rows(3))
        src, model = self.model(s)
        eid = self._one_entity(model)
        views = D.build_views(src)
        D.save_review(self.base_dir, src, views, [{"entity_id": eid, "review_status": "APPROVED_FOR_MANUAL_ACTION"}], now=FIXED_NOW)
        # rebuild source identically; content hash unchanged
        src2 = D.load_source(s)
        rec = D.merge_review_state(self.base_dir, src2, D.build_views(src2))[eid]
        self.assertEqual(rec["source_change_state"], "CURRENT")
        self.assertFalse(rec["requires_re_review"])

    def test_materially_changed_requires_re_review(self):
        rows = self.rows(3)
        s = self.valid_source(rows=rows)
        src, model = self.model(s)
        eid = model["search_terms"][0]["entity_id"]
        lin = model["search_terms"][0]["lineage_hash"]
        views = D.build_views(src)
        D.save_review(self.base_dir, src, views, [{"entity_id": eid, "review_status": "APPROVED_FOR_MANUAL_ACTION"}], now=FIXED_NOW)
        # change the evidence/classification of that same lineage row, re-promote
        rows2 = [dict(r) for r in rows]
        for r in rows2:
            if r["lineage_hash"] == lin:
                r["primary_classification"] = "HIGH_SPEND_NO_SALES"
                r["spend"] = "99.00"
        analysis2 = build_analysis(rows2)
        write_promoted(self.tmp, analysis2)
        src2 = D.load_source(s)
        rec = D.merge_review_state(self.base_dir, src2, D.build_views(src2))[eid]
        self.assertEqual(rec["source_change_state"], "SOURCE_CHANGED")
        self.assertTrue(rec["requires_re_review"])
        self.assertEqual(rec["effective_status"], "NEEDS_MORE_DATA")

    def test_source_change_flips_readiness(self):
        rows = self.rows(2)
        s = self.valid_source(rows=rows)
        src, model = self.model(s)
        eid = model["search_terms"][0]["entity_id"]
        lin = model["search_terms"][0]["lineage_hash"]
        views = D.build_views(src)
        D.save_review(self.base_dir, src, views, [{"entity_id": eid, "review_status": "APPROVED_FOR_MANUAL_ACTION"}], now=FIXED_NOW)
        rows2 = [dict(r) for r in rows]
        for r in rows2:
            if r["lineage_hash"] == lin:
                r["spend"] = "77.00"
        write_promoted(self.tmp, build_analysis(rows2))
        _, model2 = self.model(s)
        self.assertEqual(model2["readiness"], D.SOURCE_CHANGED)

    def test_review_state_never_writes_into_source(self):
        s = self.valid_source()
        src, model = self.model(s)
        promoted = os.path.join(s, "promoted")
        before = {n: os.path.getmtime(os.path.join(promoted, n)) for n in os.listdir(promoted)}
        views = D.build_views(src)
        D.save_review(self.base_dir, src, views,
                      [{"entity_id": self._one_entity(model), "review_status": "REJECTED"}], now=FIXED_NOW)
        after = {n: os.path.getmtime(os.path.join(promoted, n)) for n in os.listdir(promoted)}
        self.assertEqual(before, after)
        # review state lives under the 7.4 workspace only
        self.assertTrue(os.path.isfile(D.review_state_path(self.base_dir)))

    def test_reset_review(self):
        s = self.valid_source()
        src, model = self.model(s)
        eid = self._one_entity(model)
        views = D.build_views(src)
        D.save_review(self.base_dir, src, views, [{"entity_id": eid, "review_status": "REJECTED"}], now=FIXED_NOW)
        D.reset_review(self.base_dir, [eid])
        self.assertNotIn(eid, D.merge_review_state(self.base_dir, src, views))


# ================================================================ export
class Export(Base):
    def _prep(self, rows=None):
        s = self.valid_source(rows=rows or self.rows(4))
        src = D.load_source(s)
        views = D.build_views(src)
        rec = D.merge_review_state(self.base_dir, src, views)
        return s, src, views, rec

    def test_export_tsv(self):
        _, src, views, rec = self._prep()
        res = D.build_export(self.base_dir, src, views, rec, fmt="tsv", now=FIXED_NOW)
        self.assertTrue(res["name"].endswith(".tsv"))
        self.assertTrue(os.path.isfile(res["path"]))
        self.assertIn("\t".join(D.EXPORT_COLUMNS), res["content"])

    def test_export_json(self):
        _, src, views, rec = self._prep()
        res = D.build_export(self.base_dir, src, views, rec, fmt="json", now=FIXED_NOW)
        doc = json.loads(res["content"])
        self.assertEqual(doc["meta"]["export_schema_version"], D.EXPORT_SCHEMA)
        self.assertIn("records", doc)

    def test_export_markdown(self):
        _, src, views, rec = self._prep()
        res = D.build_export(self.base_dir, src, views, rec, fmt="markdown", now=FIXED_NOW)
        self.assertTrue(res["name"].endswith(".md"))
        self.assertIn("Owner Review Export", res["content"])

    def test_export_disclaimer_present_all_formats(self):
        _, src, views, rec = self._prep()
        for fmt in ("tsv", "json", "markdown"):
            res = D.build_export(self.base_dir, src, views, rec, fmt=fmt, now=FIXED_NOW)
            for line in D.DISCLAIMER_LINES:
                self.assertIn(line, res["content"], f"{fmt} missing disclaimer")

    def test_export_metadata_fields(self):
        _, src, views, rec = self._prep()
        res = D.build_export(self.base_dir, src, views, rec, fmt="json", now=FIXED_NOW)
        meta = json.loads(res["content"])["meta"]
        self.assertEqual(meta["source_phase7_3_manifest_sha256"], src.manifest_hash)
        self.assertFalse(meta["amazon_action_performed"])
        self.assertIn("export_generated_at", meta)

    def test_approved_only_export(self):
        s, src, views, rec = self._prep()
        eid = views["search_terms"][0]["entity_id"]
        D.save_review(self.base_dir, src, views, [{"entity_id": eid, "review_status": "APPROVED_FOR_MANUAL_ACTION"}], now=FIXED_NOW)
        rec = D.merge_review_state(self.base_dir, src, views)
        res = D.build_export(self.base_dir, src, views, rec, fmt="json", scope="approved_only", now=FIXED_NOW)
        records = json.loads(res["content"])["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["owner_review_status"], "APPROVED_FOR_MANUAL_ACTION")

    def test_approved_only_excludes_source_changed(self):
        rows = self.rows(2)
        s = self.valid_source(rows=rows)
        src = D.load_source(s)
        views = D.build_views(src)
        eid = views["search_terms"][0]["entity_id"]
        lin = views["search_terms"][0]["lineage_hash"]
        D.save_review(self.base_dir, src, views, [{"entity_id": eid, "review_status": "APPROVED_FOR_MANUAL_ACTION"}], now=FIXED_NOW)
        rows2 = [dict(r) for r in rows]
        for r in rows2:
            if r["lineage_hash"] == lin:
                r["spend"] = "88.00"
        write_promoted(self.tmp, build_analysis(rows2))
        src2 = D.load_source(s)
        views2 = D.build_views(src2)
        rec2 = D.merge_review_state(self.base_dir, src2, views2)
        res = D.build_export(self.base_dir, src2, views2, rec2, fmt="json", scope="approved_only", now=FIXED_NOW)
        self.assertEqual(json.loads(res["content"])["records"], [])

    def test_filtered_export(self):
        _, src, views, rec = self._prep()
        ids = [views["search_terms"][0]["entity_id"]]
        res = D.build_export(self.base_dir, src, views, rec, fmt="json", scope="filtered",
                             entity_ids=ids, now=FIXED_NOW)
        self.assertEqual(json.loads(res["content"])["meta"]["item_count"], 1)

    def test_tsv_formula_injection_escaped(self):
        rows = [make_row(0, customer_search_term="=cmd|'/c calc'!A1", campaign="+SUM(1)",
                         ad_group="@evil", targeting="-2+3")]
        _, src, views, rec = self._prep(rows=rows)
        res = D.build_export(self.base_dir, src, views, rec, fmt="tsv", now=FIXED_NOW)
        for cell in ("'=cmd", "'+SUM(1)", "'@evil", "'-2+3"):
            self.assertIn(cell, res["content"])

    def test_tsv_numbers_not_escaped(self):
        self.assertEqual(D._tsv_cell("-5.00"), "-5.00")
        self.assertEqual(D._tsv_cell("100"), "100")
        self.assertEqual(D._tsv_cell("=danger"), "'=danger")

    def test_export_does_not_create_amazon_upload(self):
        _, src, views, rec = self._prep()
        res = D.build_export(self.base_dir, src, views, rec, fmt="tsv", now=FIXED_NOW)
        low = res["content"].lower()
        for banned in ("bulk operation", "sp-api", "amazon ads api", "campaign_id_for_upload",
                       "sponsored products bulk"):
            self.assertNotIn(banned, low)

    def test_unsupported_export_format_rejected(self):
        _, src, views, rec = self._prep()
        with self.assertRaises(D.DashboardError):
            D.build_export(self.base_dir, src, views, rec, fmt="xlsx", now=FIXED_NOW)

    def test_export_filename_is_server_generated(self):
        _, src, views, rec = self._prep()
        e1 = D.build_export(self.base_dir, src, views, rec, fmt="tsv", now=FIXED_NOW)
        e2 = D.build_export(self.base_dir, src, views, rec, fmt="tsv", now=FIXED_NOW)
        self.assertNotEqual(e1["name"], e2["name"])          # counter increments; no user path
        self.assertTrue(e2["name"].startswith("owner-review-export-"))


# ================================================================ HTTP server & security
class HttpSecurity(Base):
    def setUp(self):
        super().setUp()
        self.base = self.serve(self.valid_source(rows=self.rows(4)))

    def test_health_endpoint(self):
        st, h, b = http(self.base + "/api/health")
        self.assertEqual(st, 200)
        doc = json.loads(b)
        self.assertEqual(doc["dashboard_readiness"], D.DASH_READY)
        self.assertEqual(doc["amazon_counters"]["amazon_api_calls"], 0)

    def test_security_headers(self):
        st, h, b = http(self.base + "/api/health")
        self.assertIn("default-src 'self'", h["Content-Security-Policy"])
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(h["Referrer-Policy"], "no-referrer")
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertEqual(h["Cache-Control"], "no-store")

    def test_index_served(self):
        st, h, b = http(self.base + "/")
        self.assertEqual(st, 200)
        self.assertIn("text/html", h["Content-Type"])
        self.assertIn(b"OFFLINE REVIEW MODE", b)

    def test_css_served(self):
        st, h, b = http(self.base + "/dashboard.css")
        self.assertEqual(st, 200)
        self.assertIn("text/css", h["Content-Type"])

    def test_js_served(self):
        st, h, b = http(self.base + "/dashboard.js")
        self.assertEqual(st, 200)
        self.assertIn("javascript", h["Content-Type"])

    def test_unknown_static_rejected(self):
        self.assertEqual(http(self.base + "/secret.txt")[0], 404)

    def test_directory_listing_disabled(self):
        self.assertEqual(http(self.base + "/phase7_dashboard_static/")[0], 404)
        self.assertEqual(http(self.base + "/api/")[0], 404)
        self.assertEqual(http(self.base + "/phase7_dashboard_static/index.html")[0], 404)

    def test_path_traversal_rejected(self):
        self.assertEqual(http(self.base + "/..%2f..%2fetc%2fpasswd")[0], 404)

    def test_post_to_unknown_endpoint_rejected(self):
        st, h, b = http(self.base + "/api/nope", method="POST", data={"x": 1})
        self.assertEqual(st, 404)

    def test_unsupported_method_rejected(self):
        st, h, b = http(self.base + "/api/health", method="DELETE")
        self.assertEqual(st, 405)

    def test_put_rejected(self):
        self.assertEqual(http(self.base + "/api/health", method="PUT", data={})[0], 405)

    def test_request_body_limit(self):
        big = b'{"updates": "' + b"a" * (D.MAX_BODY_BYTES + 10) + b'"}'
        st, h, b = http(self.base + "/api/review/save", method="POST", data=big)
        self.assertEqual(st, 413)

    def test_json_content_type_required(self):
        st, h, b = http(self.base + "/api/review/save", method="POST",
                        data=b"{}", ctype="text/plain")
        self.assertEqual(st, 415)

    def test_malformed_body_rejected(self):
        st, h, b = http(self.base + "/api/review/save", method="POST", data=b"{bad")
        self.assertEqual(st, 400)

    def test_nan_body_rejected(self):
        st, h, b = http(self.base + "/api/review/save", method="POST", data=b'{"x": NaN}')
        self.assertEqual(st, 400)

    def test_all_view_endpoints(self):
        for path in ("/api/campaigns", "/api/ad-groups", "/api/targets", "/api/search-terms",
                     "/api/observations", "/api/recommendations", "/api/blocked-recommendations",
                     "/api/manual-review-queue", "/api/policy-requirements"):
            st, h, b = http(self.base + path)
            self.assertEqual(st, 200, path)
            self.assertIn("data", json.loads(b))

    def test_session_and_manifest_endpoints(self):
        self.assertEqual(http(self.base + "/api/session")[0], 200)
        st, h, b = http(self.base + "/api/manifest")
        self.assertEqual(st, 200)
        self.assertIn("manifest", json.loads(b))

    def test_filters_endpoint(self):
        st, h, b = http(self.base + "/api/filters")
        self.assertEqual(st, 200)
        self.assertIn("classifications", json.loads(b))

    def test_review_save_via_http(self):
        model = json.loads(http(self.base + "/api/model")[2])
        eid = model["search_terms"][0]["entity_id"]
        st, h, b = http(self.base + "/api/review/save", method="POST",
                        data={"updates": [{"entity_id": eid, "review_status": "DEFERRED"}]})
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(b)["count"], 1)

    def test_export_via_http_returns_content(self):
        st, h, b = http(self.base + "/api/review/export", method="POST",
                        data={"format": "json", "scope": "all"})
        self.assertEqual(st, 200)
        ex = json.loads(b)["export"]
        self.assertIn("content", ex)
        self.assertNotIn("path", ex)          # server does not leak absolute paths

    def test_no_amazon_endpoints_exist(self):
        for path in ("/api/upload", "/api/campaigns/create", "/api/bids", "/api/negatives"):
            self.assertIn(http(self.base + path, method="POST", data={})[0], (404, 405))


# ================================================================ blocked-mode server
class BlockedMode(Base):
    def test_server_starts_in_blocked_mode(self):
        s = self.valid_source()
        with open(os.path.join(s, "promoted", "campaign-summary.csv"), "ab") as f:
            f.write(b"X")   # break integrity
        base = self.serve(s)
        st, h, b = http(base + "/api/health")
        self.assertEqual(st, 200)
        self.assertEqual(json.loads(b)["dashboard_readiness"], D.SOURCE_BLOCKED)

    def test_blocked_hides_recommendation_data(self):
        s = self.valid_source()
        with open(os.path.join(s, "promoted", "campaign-summary.csv"), "ab") as f:
            f.write(b"X")
        base = self.serve(s)
        st, h, b = http(base + "/api/recommendations")
        self.assertTrue(json.loads(b).get("blocked"))

    def test_export_blocked_when_source_blocked(self):
        s = self.valid_source()
        with open(os.path.join(s, "promoted", "campaign-summary.csv"), "ab") as f:
            f.write(b"X")
        base = self.serve(s)
        st, h, b = http(base + "/api/review/export", method="POST", data={"format": "tsv"})
        self.assertEqual(st, 409)


# ================================================================ CLI & host binding
class Cli(Base):
    def test_help(self):
        with self.assertRaises(SystemExit) as cm:
            D.main(["--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_default_host_is_loopback(self):
        # built but not served; close the socket directly (shutdown() would block with no serve loop).
        ap = D.build_server(self.base_dir, self.valid_source(), host="127.0.0.1", port=0)
        try:
            self.assertEqual(ap.server_address[0], "127.0.0.1")
        finally:
            ap.server_close()

    def test_custom_local_port(self):
        srv = D.build_server(self.base_dir, self.valid_source(), host="127.0.0.1", port=0)
        try:
            self.assertGreater(srv.server_address[1], 0)
        finally:
            srv.server_close()

    def test_reject_nonlocal_host(self):
        with self.assertRaises(D.DashboardError):
            D.validate_host("0.0.0.0")
        self.assertEqual(D.validate_host("0.0.0.0", allow_nonlocal=True), "0.0.0.0")

    def test_main_prints_zero_amazon_counters(self):
        s = self.valid_source()
        out = io.StringIO()
        old = sys.stdout
        sys.stdout = out
        try:
            rc = D.main(["--base-dir", self.base_dir, "--phase7-3-dir", s], serve=False)
        finally:
            sys.stdout = old
        text = out.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("amazon_connections=0", text)
        self.assertIn("amazon_api_calls=0", text)
        self.assertIn("amazon_mutations=0", text)
        self.assertIn("dashboard_readiness=SESSION7_4_DASHBOARD_READY", text)

    def test_main_source_required(self):
        rc = D.main(["--base-dir", self.base_dir,
                     "--phase7-3-dir", os.path.join(self.tmp, "absent")], serve=False)
        self.assertEqual(rc, 1)

    def test_main_rejects_nonlocal_host(self):
        s = self.valid_source()
        rc = D.main(["--base-dir", self.base_dir, "--phase7-3-dir", s, "--host", "0.0.0.0"], serve=False)
        self.assertEqual(rc, 2)


# ================================================================ offline / no-external-resources
class Offline(Base):
    def _static(self, name):
        return open(os.path.join(D.STATIC_DIR, name), encoding="utf-8").read()

    def test_no_external_resources_in_html(self):
        html = self._static("index.html")
        for token in ("http://", "https://", "//cdn", "googleapis", "unpkg", "jsdelivr", "integrity="):
            self.assertNotIn(token, html)

    def test_no_external_scripts_or_fonts(self):
        for name in ("index.html", "dashboard.css", "dashboard.js"):
            txt = self._static(name)
            for token in ("@import url(http", "src=\"http", "fonts.gstatic", "fonts.googleapis"):
                self.assertNotIn(token, txt)

    def test_no_telemetry_or_analytics(self):
        js = self._static("dashboard.js")
        for token in ("google-analytics", "gtag(", "mixpanel", "segment.io", "sentry", "navigator.sendBeacon"):
            self.assertNotIn(token, js)

    def test_backend_imports_no_network_client(self):
        src = open(os.path.join(ROOT, "production", "phase7_owner_dashboard.py"), encoding="utf-8").read()
        for token in ("import requests", "import socket", "urllib.request", "http.client",
                      "boto3", "selenium", "playwright", "webbrowser"):
            self.assertNotIn(token, src)

    def test_no_amazon_integration_tokens(self):
        # Genuine integration artifacts must be absent. Boundary DISCLAIMERS ("no SP-API") are allowed
        # — they document the guarantee — so we scan for real client/credential tokens, not the words.
        src = open(os.path.join(ROOT, "production", "phase7_owner_dashboard.py"), encoding="utf-8").read()
        low = src.lower()
        for token in ("sellingpartnerapi", "python-amazon-sp-api", "amazon_cookie", "amazon_token",
                      "amazon_session", "aws_access_key", "aws_secret", "import boto3",
                      "lwa_refresh_token", "advertising.amazon.com"):
            self.assertNotIn(token, low)

    def test_amazon_counters_all_zero(self):
        for v in D.AMAZON_COUNTERS.values():
            self.assertEqual(v, 0)
        src, model = self.model(self.valid_source())
        for v in model["amazon_counters"].values():
            self.assertEqual(v, 0)


if __name__ == "__main__":
    unittest.main()
