#!/usr/bin/env python3
"""Phase 7.5 — offline Owner Decision Package.

Every test runs OFFLINE and asserts the permanent Amazon boundary throughout: Phase 7.5 reads only
accepted Phase 7.3 promoted output and local Phase 7.4 review state, connects to nothing, performs
no Amazon action, is not an upload template, and never claims an action was performed. Synthetic
fixtures only — no real T2 owner data is committed. The Phase 7.3 fixture builders are reused from
the accepted Phase 7.4 test module so the source contract is byte-faithful.
"""
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from production import phase7_owner_decision_package as P     # noqa: E402
from production import phase7_owner_dashboard as D            # noqa: E402
from production import phase7_ads_analysis as AA              # noqa: E402
from tests import test_phase7_4_owner_dashboard as T4         # noqa: E402

make_row = T4.make_row
build_analysis = T4.build_analysis
write_promoted = T4.write_promoted
reseal = T4.reseal

FIXED_NOW = lambda: datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)  # noqa: E731
REF = "2026-07-21"
MH = "a" * 64          # a valid-shaped manifest hash for unit tests
OTHER = "b" * 64       # a different valid-shaped hash
ZERO = "0" * 64        # a different valid-shaped content hash


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.loads(f.read())


def file_sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def ev_evidence(states=None, target_acos_source="NEUTRAL_DEFAULT_OWNER_CONFIGURABLE"):
    states = states or {"clicks": "PRESENT", "impressions": "PRESENT", "spend": "PRESENT",
                        "orders_7d": "PRESENT", "sales_7d": "PRESENT", "units_7d": "PRESENT"}
    return {"metric_states": states, "target_acos": "0.300000",
            "target_acos_source": target_acos_source, "outside_lookback_window": False}


def strow(idx=0, **kw):
    """A Phase 7.4 search-term VIEW row (with correct entity_id + content_sha256) for unit tests."""
    return D._search_term_view(make_row(idx, **kw), prefix="st", entity_type="search_term")


def rec_for(row, *, status="APPROVED_FOR_MANUAL_ACTION", content=None, manifest=MH, note=""):
    return {
        "entity_id": row["entity_id"], "entity_type": row["entity_type"],
        "review_status": status, "owner_note": note,
        "content_sha256": content if content is not None else row["content_sha256"],
        "source_manifest_sha256": manifest, "revision": 1,
        "created_at": "2026-07-21T00:00:00Z", "updated_at": "2026-07-21T00:00:00Z",
    }


def evaluate(entity_id, rec, index, manifest_hash=MH):
    struct = P._record_structural_error(entity_id, rec)
    return P._evaluate_record(entity_id, rec, index, manifest_hash, struct)


# ================================================================ unit: eligibility gates
class Eligibility(unittest.TestCase):
    def _eval_row(self, row, **rec_kw):
        return evaluate(row["entity_id"], rec_for(row, **rec_kw), {row["entity_id"]: row})

    def test_approved_negative_is_eligible(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE)
        kind, payload = self._eval_row(row)
        self.assertEqual(kind, "candidate")
        self.assertEqual(payload.rec_type, AA.RL_NEGATIVE)

    def test_approved_exact_is_eligible(self):
        row = strow(owner_review_label=AA.RL_EXACT)
        self.assertEqual(self._eval_row(row)[0], "candidate")

    def test_approved_bid_budget_owner_declared_is_eligible(self):
        row = strow(owner_review_label=AA.RL_BID_BUDGET,
                    evidence=ev_evidence(target_acos_source="OWNER_DECLARED"))
        self.assertEqual(self._eval_row(row)[0], "candidate")

    def test_unreviewed_excluded(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE)
        kind, ex = self._eval_row(row, status="UNREVIEWED")
        self.assertEqual((kind, ex["reason_code"]), ("excluded", P.R_NOT_APPROVED))

    def test_rejected_excluded(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE)
        self.assertEqual(self._eval_row(row, status="REJECTED")[1]["reason_code"], P.R_NOT_APPROVED)

    def test_deferred_excluded(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE)
        self.assertEqual(self._eval_row(row, status="DEFERRED")[1]["reason_code"], P.R_NOT_APPROVED)

    def test_needs_more_data_excluded(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE)
        self.assertEqual(self._eval_row(row, status="NEEDS_MORE_DATA")[1]["reason_code"], P.R_NOT_APPROVED)

    def test_needs_policy_excluded(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE)
        self.assertEqual(self._eval_row(row, status="NEEDS_POLICY")[1]["reason_code"], P.R_NOT_APPROVED)

    def test_already_handled_excluded(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE)
        self.assertEqual(self._eval_row(row, status="ALREADY_HANDLED")[1]["reason_code"], P.R_NOT_APPROVED)

    def test_not_applicable_excluded(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE)
        self.assertEqual(self._eval_row(row, status="NOT_APPLICABLE")[1]["reason_code"], P.R_NOT_APPROVED)

    def test_unsupported_recommendation_type_excluded(self):
        row = strow(owner_review_label=AA.RL_INSUFFICIENT)  # INSUFFICIENT_EVIDENCE
        self.assertEqual(self._eval_row(row)[1]["reason_code"], P.R_UNSUPPORTED_RECOMMENDATION_TYPE)

    def test_keep_monitoring_unsupported(self):
        row = strow(owner_review_label=AA.RL_MONITOR)  # KEEP_MONITORING
        self.assertEqual(self._eval_row(row)[1]["reason_code"], P.R_UNSUPPORTED_RECOMMENDATION_TYPE)

    def test_blocked_recommendation_excluded(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE, primary_classification="NEEDS_OWNER_REVIEW")
        self.assertEqual(self._eval_row(row)[1]["reason_code"], P.R_BLOCKED_RECOMMENDATION)

    def test_blocked_by_reason_code_excluded(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE, reason_codes=["REQUIRED_METRIC_UNUSABLE"])
        self.assertEqual(self._eval_row(row)[1]["reason_code"], P.R_BLOCKED_RECOMMENDATION)

    def test_missing_policy_excluded(self):
        row = strow(owner_review_label=AA.RL_BID_BUDGET,
                    evidence=ev_evidence(target_acos_source="NEUTRAL_DEFAULT_OWNER_CONFIGURABLE"))
        self.assertEqual(self._eval_row(row)[1]["reason_code"], P.R_MISSING_POLICY)

    def test_missing_evidence_excluded(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE,
                    evidence=ev_evidence(states={"clicks": "PRESENT", "impressions": "PRESENT",
                                                 "spend": "MISSING", "orders_7d": "PRESENT"}))
        self.assertEqual(self._eval_row(row)[1]["reason_code"], P.R_MISSING_EVIDENCE)

    def test_ambiguous_currency_excluded(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE, currency=None)
        self.assertEqual(self._eval_row(row)[1]["reason_code"], P.R_AMBIGUOUS_CURRENCY)

    def test_ambiguous_attribution_window_multiple(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE,
                    evidence=ev_evidence(states={"clicks": "PRESENT", "impressions": "PRESENT",
                                                 "spend": "PRESENT", "orders_7d": "PRESENT",
                                                 "orders_14d": "PRESENT"}))
        self.assertEqual(self._eval_row(row)[1]["reason_code"], P.R_AMBIGUOUS_ATTRIBUTION_WINDOW)

    def test_ambiguous_attribution_window_none(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE,
                    evidence=ev_evidence(states={"clicks": "PRESENT", "impressions": "PRESENT",
                                                 "spend": "PRESENT"}))
        self.assertEqual(self._eval_row(row)[1]["reason_code"], P.R_AMBIGUOUS_ATTRIBUTION_WINDOW)

    def test_missing_required_field_excluded(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE, customer_search_term=None)
        self.assertEqual(self._eval_row(row)[1]["reason_code"], P.R_MISSING_REQUIRED_FIELD)

    def test_entity_absent_excluded(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE)
        kind, ex = evaluate(row["entity_id"], rec_for(row), {})  # empty index
        self.assertEqual((kind, ex["reason_code"]), ("excluded", P.R_ENTITY_ABSENT))

    def test_unknown_entity_excluded(self):
        eid = "weird:" + ZERO
        rec = {"entity_id": eid, "review_status": "APPROVED_FOR_MANUAL_ACTION",
               "content_sha256": ZERO, "source_manifest_sha256": MH, "owner_note": ""}
        kind, ex = evaluate(eid, rec, {})
        self.assertEqual((kind, ex["reason_code"]), ("excluded", P.R_UNKNOWN_ENTITY))

    def test_hash_mismatch_excluded(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE)
        kind, ex = self._eval_row(row, content=ZERO, manifest=MH)  # content differs, manifest matches
        self.assertEqual((kind, ex["reason_code"]), ("excluded", P.R_HASH_MISMATCH))

    def test_source_changed_excluded(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE)
        kind, ex = self._eval_row(row, content=ZERO, manifest=OTHER)  # content + manifest differ
        self.assertEqual((kind, ex["reason_code"]), ("excluded", P.R_SOURCE_CHANGED))

    def test_source_manifest_mismatch_excluded(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE)
        kind, ex = self._eval_row(row, content=None, manifest=OTHER)  # content matches, manifest differs
        self.assertEqual((kind, ex["reason_code"]), ("excluded", P.R_SOURCE_MANIFEST_MISMATCH))

    def test_invalid_status_is_structural_exclusion(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE)
        rec = rec_for(row, status="SEND_TO_AMAZON")
        kind, ex = evaluate(row["entity_id"], rec, {row["entity_id"]: row})
        self.assertEqual((kind, ex["reason_code"]), ("excluded", P.R_INVALID_REVIEW_STATE))

    def test_invalid_hash_shape_is_structural_exclusion(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE)
        rec = rec_for(row)
        rec["content_sha256"] = "not-a-hash"
        kind, ex = evaluate(row["entity_id"], rec, {row["entity_id"]: row})
        self.assertEqual((kind, ex["reason_code"]), ("excluded", P.R_INVALID_REVIEW_STATE))

    def test_mismatched_inner_entity_id_is_structural(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE)
        rec = rec_for(row)
        rec["entity_id"] = "st:" + ZERO
        self.assertIsNotNone(P._record_structural_error(row["entity_id"], rec))


# ================================================================ unit: duplicate handling
class Duplicates(unittest.TestCase):
    def _cands(self, rows):
        out = []
        for r in rows:
            kind, payload = evaluate(r["entity_id"], rec_for(r), {r["entity_id"]: r})
            self.assertEqual(kind, "candidate")
            out.append(payload)
        return out

    def test_identical_duplicates_deduplicate(self):
        a = strow(owner_review_label=AA.RL_NEGATIVE, customer_search_term="kw", lineage_hash=sha("A"),
                  canonical_row_key="kA")
        b = strow(owner_review_label=AA.RL_NEGATIVE, customer_search_term="kw", lineage_hash=sha("B"),
                  canonical_row_key="kB")
        self.assertEqual(a["content_sha256"], b["content_sha256"])
        items, excluded, dup_i, dup_c, report = P._dedupe(self._cands([a, b]))
        self.assertEqual((len(items), dup_i, dup_c), (1, 1, 0))
        self.assertEqual(items[0]["duplicate_of_count"], 1)
        self.assertTrue(any(e["resolution"] == P.R_DUPLICATE_IDENTICAL for e in report))

    def test_conflicting_duplicates_exclude_all(self):
        a = strow(owner_review_label=AA.RL_NEGATIVE, customer_search_term="kw", spend="5.00",
                  lineage_hash=sha("A"), canonical_row_key="kA")
        b = strow(owner_review_label=AA.RL_NEGATIVE, customer_search_term="kw", spend="9.00",
                  lineage_hash=sha("B"), canonical_row_key="kB")
        self.assertNotEqual(a["content_sha256"], b["content_sha256"])
        items, excluded, dup_i, dup_c, report = P._dedupe(self._cands([a, b]))
        self.assertEqual((len(items), dup_i, dup_c), (0, 0, 2))
        self.assertTrue(all(e["reason_code"] == P.R_DUPLICATE_CONFLICT for e in excluded))
        self.assertTrue(any(r["resolution"] == P.R_DUPLICATE_CONFLICT for r in report))

    def test_duplicate_lineage_recorded(self):
        a = strow(owner_review_label=AA.RL_NEGATIVE, customer_search_term="kw", lineage_hash=sha("A"),
                  canonical_row_key="kA")
        b = strow(owner_review_label=AA.RL_NEGATIVE, customer_search_term="kw", lineage_hash=sha("B"),
                  canonical_row_key="kB")
        items, _, _, _, report = P._dedupe(self._cands([a, b]))
        entry = [r for r in report if r["resolution"] == P.R_DUPLICATE_IDENTICAL][0]
        self.assertEqual(entry["duplicate_count"], 1)
        self.assertEqual(len(items[0]["duplicate_source_ids"]), 1)


# ================================================================ unit: helpers
class Helpers(unittest.TestCase):
    def test_attribution_windows_single(self):
        self.assertEqual(P._attribution_windows(ev_evidence()), ["7 day"])

    def test_attribution_windows_multiple_sorted(self):
        ev = ev_evidence(states={"orders_7d": "PRESENT", "orders_14d": "PRESENT"})
        self.assertEqual(P._attribution_windows(ev), ["7 day", "14 day"])

    def test_recommendation_type_projection(self):
        self.assertEqual(P._recommendation_type({"entity_type": "search_term",
                                                 "owner_review_label": AA.RL_NEGATIVE}), AA.RL_NEGATIVE)
        self.assertEqual(P._recommendation_type({"entity_type": "recommendation",
                                                 "recommendation_type": AA.RL_EXACT}), AA.RL_EXACT)
        self.assertIsNone(P._recommendation_type({"entity_type": "campaign"}))

    def test_package_item_id_stable_and_prefixed(self):
        k = ("t", "c", "g", "x", "", "kw", "USD", "7 day", "d1 to d2")
        i1 = P._package_item_id(k, "h")
        i2 = P._package_item_id(k, "h")
        self.assertEqual(i1, i2)
        self.assertTrue(i1.startswith("item:"))

    def test_missing_values_stay_missing(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE, orders=None)
        kind, cand = evaluate(row["entity_id"], rec_for(row), {row["entity_id"]: row})
        items, *_ = P._dedupe([cand])
        self.assertIsNone(items[0]["orders"])

    def test_decimal_values_remain_strings(self):
        row = strow(owner_review_label=AA.RL_NEGATIVE, spend="5.00", sales="20.00")
        _, cand = evaluate(row["entity_id"], rec_for(row), {row["entity_id"]: row})
        items, *_ = P._dedupe([cand])
        self.assertIsInstance(items[0]["spend"], str)
        self.assertEqual(items[0]["spend"], "5.00")


# ================================================================ unit: review-state strict loader
class ReviewStateLoader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p75rs-")
        self.dir = os.path.join(self.tmp, "7.4")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, text):
        path = D.review_state_path(self.dir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(text.encode("utf-8") if isinstance(text, str) else text)

    def test_missing_returns_missing(self):
        status, doc = P.load_review_state_strict(self.dir)
        self.assertEqual(status, "MISSING")
        self.assertEqual(doc["records"], {})

    def test_valid_loads(self):
        self._write(json.dumps({"schema_version": D.REVIEW_STATE_SCHEMA, "records": {}}))
        self.assertEqual(P.load_review_state_strict(self.dir)[0], "OK")

    def test_duplicate_key_blocks(self):
        self._write('{"schema_version": "%s", "records": {"a": 1, "a": 2}}' % D.REVIEW_STATE_SCHEMA)
        with self.assertRaises(P.DecisionPackageError) as cm:
            P.load_review_state_strict(self.dir)
        self.assertEqual(cm.exception.code, "DUPLICATE_REVIEW_RECORD")

    def test_malformed_json_blocks(self):
        self._write("{not json")
        with self.assertRaises(P.DecisionPackageError):
            P.load_review_state_strict(self.dir)

    def test_non_finite_blocks(self):
        self._write('{"schema_version": "%s", "records": {"a": NaN}}' % D.REVIEW_STATE_SCHEMA)
        with self.assertRaises(P.DecisionPackageError):
            P.load_review_state_strict(self.dir)

    def test_null_byte_blocks(self):
        self._write(b'{"schema_version": "x", "records": {}}\x00')
        with self.assertRaises(P.DecisionPackageError):
            P.load_review_state_strict(self.dir)

    def test_wrong_root_blocks(self):
        self._write("[]")
        with self.assertRaises(P.DecisionPackageError):
            P.load_review_state_strict(self.dir)

    def test_wrong_schema_blocks(self):
        self._write(json.dumps({"schema_version": "nope", "records": {}}))
        with self.assertRaises(P.DecisionPackageError):
            P.load_review_state_strict(self.dir)


# ================================================================ unit: TSV safety
class TsvSafety(unittest.TestCase):
    def test_formula_injection_prefixes(self):
        for ch in ("=", "+", "-", "@"):
            self.assertTrue(P._TSV_CELL(ch + "cmd").startswith("'" + ch),
                            f"leading {ch!r} not neutralized")

    def test_formula_injection_tab_cr_lf_stripped(self):
        for ch in ("\t", "\r", "\n"):
            cell = P._TSV_CELL(ch + "=danger")
            self.assertNotIn("\t", cell)
            self.assertNotIn("\r", cell)
            self.assertNotIn("\n", cell)

    def test_at_sign_prefixed(self):
        self.assertEqual(P._TSV_CELL("@x")[0], "'")

    def test_legitimate_negative_decimal_preserved(self):
        self.assertEqual(P._TSV_CELL("-2.50"), "-2.50")
        self.assertEqual(P._TSV_CELL("-2"), "-2")

    def test_tsv_rows_equal_columns(self):
        cols = ("a", "b", "c")
        text = P._tsv(cols, [{"a": "1", "b": "=x", "c": None}, {"a": "2", "b": "y", "c": "z"}])
        lines = [ln for ln in text.split("\n") if ln]
        for ln in lines:
            self.assertEqual(len(ln.split("\t")), len(cols))


# ================================================================ integration base
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p75-")
        self.p73 = None
        self.p74 = os.path.join(self.tmp, "7.4")
        self.p75 = os.path.join(self.tmp, "7.5")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_source(self, rows, **kw):
        analysis = build_analysis(rows, **kw)
        self.p73 = write_promoted(self.tmp, analysis)
        return D.load_source(self.p73)

    def approve(self, src, entity_ids, status="APPROVED_FOR_MANUAL_ACTION", note=""):
        views = D.build_views(src)
        D.save_review(self.p74, src, views,
                      [{"entity_id": e, "review_status": status, "owner_note": note}
                       for e in entity_ids], now=FIXED_NOW)

    def st_id(self, row):
        return "st:" + row["lineage_hash"]

    def rec_id(self, row):
        return "rec:" + row["lineage_hash"]

    def run75(self, **kw):
        ref = kw.pop("reference_date", REF)
        base = kw.pop("base_dir", self.p75)
        return P.run(base, self.p73, self.p74, ref, now=FIXED_NOW, **kw)

    def read(self, pkg_dir, name):
        with open(os.path.join(pkg_dir, name), "rb") as f:
            return f.read().decode("utf-8")

    def files(self, pkg_dir):
        return sorted(os.listdir(pkg_dir))


# ================================================================ integration: empty & populated
class EmptyAndPopulated(Base):
    def test_no_approved_empty_package_succeeds(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])], status="DEFERRED", note="later")
        r = self.run75()
        self.assertEqual(r["readiness"], P.PKG_READY_EMPTY)
        self.assertEqual(r["counts"]["eligible_actions"], 0)
        self.assertEqual(r["counts"]["excluded_items"], 1)
        self.assertEqual(P.exit_code_for(r["readiness"]), 0)
        self.assertTrue(os.path.isdir(r["package_dir"]))

    def test_empty_review_state_ready_empty(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        self.make_source(rows)
        os.makedirs(os.path.dirname(D.review_state_path(self.p74)), exist_ok=True)
        with open(D.review_state_path(self.p74), "wb") as f:
            f.write(json.dumps({"schema_version": D.REVIEW_STATE_SCHEMA, "records": {}}).encode())
        r = self.run75()
        self.assertEqual(r["readiness"], P.PKG_READY_EMPTY)
        self.assertEqual(r["counts"]["review_state_records"], 0)

    def test_populated_package_succeeds(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])], note="please review")
        r = self.run75()
        self.assertEqual(r["readiness"], P.PKG_READY)
        self.assertEqual(r["counts"]["eligible_actions"], 1)
        pkg = r["package_dir"]
        for name in ("OWNER_READ_FIRST.md", "executive_summary.md", "manual_action_checklist.tsv",
                     "manual_action_checklist.json", "decision_details.md", "excluded_items.tsv",
                     "excluded_items.json", "source_lineage.json", "package_manifest.json"):
            self.assertTrue(os.path.isfile(os.path.join(pkg, name)), name)
        items = json.loads(self.read(pkg, "manual_action_checklist.json"))["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["recommendation_type"], AA.RL_NEGATIVE)
        self.assertEqual(items[0]["review_status"], "APPROVED_FOR_MANUAL_ACTION")
        self.assertIn("negative keyword", items[0]["action_description"])

    def test_multiple_approved(self):
        rows = [make_row(i, owner_review_label=AA.RL_NEGATIVE) for i in range(3)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(r) for r in rows])
        r = self.run75()
        self.assertEqual(r["counts"]["eligible_actions"], 3)

    def test_owner_read_first_contract(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])])
        pkg = self.run75()["package_dir"]
        text = self.read(pkg, "OWNER_READ_FIRST.md").lower()
        self.assertIn("offline manual decision package", text)
        self.assertIn("no amazon connection has been made", text)
        self.assertIn("no amazon action has been performed", text)
        self.assertIn("not an amazon upload template", text)
        self.assertIn("independently verify", text)

    def test_excluded_report_records_reason(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])], status="REJECTED")
        pkg = self.run75()["package_dir"]
        ex = json.loads(self.read(pkg, "excluded_items.json"))["excluded_items"]
        self.assertEqual(ex[0]["reason_code"], P.R_NOT_APPROVED)


# ================================================================ integration: source-change & absent
class SourceChange(Base):
    def test_source_changed_flags_and_excludes(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])])
        rows2 = [make_row(1, owner_review_label=AA.RL_NEGATIVE, spend="99.00")]
        write_promoted(self.tmp, build_analysis(rows2))
        r = self.run75()
        self.assertEqual(r["counts"]["eligible_actions"], 0)
        self.assertEqual(r["counts"]["source_changed"], 1)
        self.assertEqual(r["readiness"], P.SOURCE_CHANGED_REVIEW_REQUIRED)
        pkg = r["package_dir"]
        ex = json.loads(self.read(pkg, "excluded_items.json"))["excluded_items"]
        self.assertEqual(ex[0]["reason_code"], P.R_SOURCE_CHANGED)

    def test_entity_absent_excludes(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE),
                make_row(2, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])])
        write_promoted(self.tmp, build_analysis([rows[1]]))   # drop approved row
        r = self.run75()
        pkg = r["package_dir"]
        ex = json.loads(self.read(pkg, "excluded_items.json"))["excluded_items"]
        self.assertEqual(ex[0]["reason_code"], P.R_ENTITY_ABSENT)

    def test_source_manifest_mismatch_excludes(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE),
                make_row(2, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])])
        # change a DIFFERENT row -> manifest hash changes, approved row content unchanged.
        rows2 = [make_row(1, owner_review_label=AA.RL_NEGATIVE),
                 make_row(2, owner_review_label=AA.RL_NEGATIVE, spend="77.00")]
        write_promoted(self.tmp, build_analysis(rows2))
        r = self.run75()
        pkg = r["package_dir"]
        ex = json.loads(self.read(pkg, "excluded_items.json"))["excluded_items"]
        self.assertEqual(ex[0]["reason_code"], P.R_SOURCE_MANIFEST_MISMATCH)

    def test_hash_mismatch_excludes(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])])
        # tamper the review record content hash but keep the manifest hash current.
        path = D.review_state_path(self.p74)
        doc = read_json(path)
        eid = self.st_id(rows[0])
        doc["records"][eid]["content_sha256"] = ZERO
        with open(path, "wb") as f:
            f.write(json.dumps(doc).encode("utf-8"))
        r = self.run75()
        pkg = r["package_dir"]
        ex = json.loads(self.read(pkg, "excluded_items.json"))["excluded_items"]
        self.assertEqual(ex[0]["reason_code"], P.R_HASH_MISMATCH)


# ================================================================ integration: duplicates end-to-end
class DuplicatesE2E(Base):
    def test_same_row_in_search_term_and_recommendation_dedup(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0]), self.rec_id(rows[0])])
        r = self.run75()
        self.assertEqual(r["counts"]["eligible_actions"], 1)
        self.assertEqual(r["counts"]["duplicate_identical"], 1)

    def test_conflicting_actions_exclude_all(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE, customer_search_term="kw",
                         spend="5.00", canonical_row_key="k1"),
                make_row(2, owner_review_label=AA.RL_NEGATIVE, customer_search_term="kw",
                         spend="9.00", canonical_row_key="k2")]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0]), self.st_id(rows[1])])
        r = self.run75()
        self.assertEqual(r["counts"]["eligible_actions"], 0)
        self.assertEqual(r["counts"]["duplicate_conflicts"], 2)
        self.assertEqual(r["readiness"], P.CONFLICT_REVIEW_REQUIRED)


# ================================================================ integration: separation & policy
class Separation(Base):
    def test_multi_currency_separation(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE, currency="USD"),
                make_row(2, owner_review_label=AA.RL_NEGATIVE, currency="EUR")]
        src = self.make_source(rows, currencies=("USD", "EUR"))
        self.approve(src, [self.st_id(r) for r in rows])
        r = self.run75()
        self.assertEqual(r["counts"]["eligible_actions"], 2)
        man = json.loads(self.read(r["package_dir"], "package_manifest.json"))
        self.assertEqual(man["currencies"], ["EUR", "USD"])

    def test_attribution_window_separation(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE),
                make_row(2, owner_review_label=AA.RL_NEGATIVE,
                         evidence=ev_evidence(states={"clicks": "PRESENT", "impressions": "PRESENT",
                                                      "spend": "PRESENT", "orders_14d": "PRESENT"}))]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(r) for r in rows])
        r = self.run75()
        man = json.loads(self.read(r["package_dir"], "package_manifest.json"))
        self.assertEqual(sorted(man["attribution_windows"]), ["14 day", "7 day"])

    def test_missing_policy_readiness(self):
        rows = [make_row(1, owner_review_label=AA.RL_BID_BUDGET)]  # default neutral target acos
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])])
        r = self.run75()
        self.assertEqual(r["counts"]["eligible_actions"], 0)
        self.assertEqual(r["counts"]["policy_required"], 1)
        self.assertEqual(r["readiness"], P.POLICY_REQUIRED)


# ================================================================ integration: determinism & idempotency
class Determinism(Base):
    def _populated(self):
        rows = [make_row(i, owner_review_label=AA.RL_NEGATIVE) for i in range(2)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(r) for r in rows], note="Cần kiểm tra tồn kho")
        return rows

    def test_deterministic_content_hash_and_files(self):
        self._populated()
        b1 = os.path.join(self.tmp, "out1")
        b2 = os.path.join(self.tmp, "out2")
        r1 = self.run75(base_dir=b1)
        r2 = self.run75(base_dir=b2)
        self.assertEqual(r1["content_sha256"], r2["content_sha256"])
        self.assertEqual(r1["package_id"], r2["package_id"])
        det = ("OWNER_READ_FIRST.md", "executive_summary.md", "manual_action_checklist.tsv",
               "manual_action_checklist.json", "decision_details.md", "excluded_items.tsv",
               "excluded_items.json", "source_lineage.json")
        for name in det:
            self.assertEqual(self.read(r1["package_dir"], name), self.read(r2["package_dir"], name),
                             f"{name} not byte-identical")

    def test_manifest_content_hash_deterministic(self):
        self._populated()
        r1 = self.run75(base_dir=os.path.join(self.tmp, "a"))
        r2 = self.run75(base_dir=os.path.join(self.tmp, "b"))
        m1 = json.loads(self.read(r1["package_dir"], "package_manifest.json"))
        m2 = json.loads(self.read(r2["package_dir"], "package_manifest.json"))
        self.assertEqual(m1["package_content_sha256"], m2["package_content_sha256"])

    def test_reference_date_changes_identity(self):
        self._populated()
        r1 = self.run75(base_dir=os.path.join(self.tmp, "a"), reference_date="2026-07-21")
        r2 = self.run75(base_dir=os.path.join(self.tmp, "b"), reference_date="2026-07-22")
        self.assertNotEqual(r1["content_sha256"], r2["content_sha256"])

    def test_repeated_run_idempotent(self):
        self._populated()
        r1 = self.run75()
        r2 = self.run75()
        self.assertEqual(r1["package_id"], r2["package_id"])
        self.assertEqual(r2["outcome"], "IDEMPOTENT_REUSE")

    def test_same_name_altered_content_blocks(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])])
        r1 = self.run75(package_name="fixed")
        self.assertEqual(r1["outcome"], "CREATED")
        before = self.read(r1["package_dir"], "package_manifest.json")
        # alter content: approve a second row, same package name
        rows2 = [make_row(1, owner_review_label=AA.RL_NEGATIVE),
                 make_row(2, owner_review_label=AA.RL_NEGATIVE)]
        src2 = self.make_source(rows2)
        self.approve(src2, [self.st_id(rows2[0]), self.st_id(rows2[1])])
        r2 = self.run75(package_name="fixed")
        self.assertEqual(r2["readiness"], P.PACKAGE_BLOCKED)
        self.assertEqual(P.exit_code_for(r2["readiness"]), 2)
        self.assertEqual(self.read(r1["package_dir"], "package_manifest.json"), before)

    def test_json_canonical_ordering(self):
        self._populated()
        pkg = self.run75()["package_dir"]
        raw = self.read(pkg, "manual_action_checklist.json")
        parsed = json.loads(raw)
        self.assertEqual(raw, P.canonical_json(parsed) + "\n")

    def test_tsv_deterministic(self):
        self._populated()
        r1 = self.run75(base_dir=os.path.join(self.tmp, "a"))
        r2 = self.run75(base_dir=os.path.join(self.tmp, "b"))
        self.assertEqual(self.read(r1["package_dir"], "manual_action_checklist.tsv"),
                         self.read(r2["package_dir"], "manual_action_checklist.tsv"))


# ================================================================ integration: atomic writes
class AtomicWrites(Base):
    def _populated(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])])

    def test_failure_leaves_no_partial_package(self):
        self._populated()
        built = P.build_model(self.p75, self.p73, self.p74, REF)
        orig = P._build_manifest

        def boom(*a, **k):
            raise RuntimeError("injected failure")

        P._build_manifest = boom
        try:
            with self.assertRaises(RuntimeError):
                P._generate_package(self.p75, built["model"], now=FIXED_NOW)
        finally:
            P._build_manifest = orig
        pkgs = os.path.join(self.p75, "packages")
        self.assertEqual(os.listdir(pkgs) if os.path.isdir(pkgs) else [], [])
        runtime = os.path.join(self.p75, "runtime")
        leftovers = [n for n in (os.listdir(runtime) if os.path.isdir(runtime) else [])
                     if n.startswith(".build-")]
        self.assertEqual(leftovers, [])

    def test_last_valid_package_preserved_on_conflict(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])])
        r1 = self.run75(package_name="p")
        content_before = self.read(r1["package_dir"], "manual_action_checklist.json")
        rows2 = [make_row(1, owner_review_label=AA.RL_NEGATIVE),
                 make_row(2, owner_review_label=AA.RL_NEGATIVE)]
        src2 = self.make_source(rows2)
        self.approve(src2, [self.st_id(rows2[0]), self.st_id(rows2[1])])
        self.run75(package_name="p")
        self.assertEqual(self.read(r1["package_dir"], "manual_action_checklist.json"), content_before)


# ================================================================ integration: immutability
class Immutability(Base):
    def _hash_tree(self, root):
        h = hashlib.sha256()
        for dirpath, _dirs, names in sorted(os.walk(root)):
            for n in sorted(names):
                p = os.path.join(dirpath, n)
                h.update(os.path.relpath(p, root).encode("utf-8"))
                with open(p, "rb") as f:
                    h.update(f.read())
        return h.hexdigest()

    def test_source_and_review_state_immutable(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])])
        promoted = os.path.join(self.p73, "promoted")
        rs = D.review_state_path(self.p74)
        h3 = self._hash_tree(promoted)
        h4 = file_sha(rs)
        self.run75()
        self.run75(base_dir=os.path.join(self.tmp, "again"))
        self.assertEqual(self._hash_tree(promoted), h3)
        self.assertEqual(file_sha(rs), h4)

    def test_no_writes_into_source_dirs(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])])
        promoted = os.path.join(self.p73, "promoted")
        before = set(os.listdir(promoted))
        self.run75()
        self.assertEqual(set(os.listdir(promoted)), before)


# ================================================================ integration: validate-only & source blocks
class ValidateAndBlocks(Base):
    def test_validate_only_creates_no_package(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])])
        r = self.run75(validate_only=True)
        self.assertEqual(r["readiness"], P.PKG_READY)
        self.assertIsNone(r["package_dir"])
        self.assertFalse(os.path.isdir(os.path.join(self.p75, "packages")) and
                         os.listdir(os.path.join(self.p75, "packages")))
        self.assertEqual(P.exit_code_for(r["readiness"]), 0)

    def test_validate_only_blocked_exit(self):
        self.p73 = os.path.join(self.tmp, "empty73")
        os.makedirs(self.p73, exist_ok=True)
        os.makedirs(os.path.dirname(D.review_state_path(self.p74)), exist_ok=True)
        with open(D.review_state_path(self.p74), "wb") as f:
            f.write(json.dumps({"schema_version": D.REVIEW_STATE_SCHEMA, "records": {}}).encode())
        r = self.run75(validate_only=True)
        self.assertEqual(r["readiness"], P.SOURCE_REQUIRED)
        self.assertEqual(P.exit_code_for(r["readiness"]), 2)

    def test_missing_source_blocks(self):
        self.p73 = os.path.join(self.tmp, "empty73")
        os.makedirs(self.p73, exist_ok=True)
        r = self.run75()
        self.assertEqual(r["readiness"], P.SOURCE_REQUIRED)
        self.assertIsNone(r["package_dir"])

    def test_missing_manifest_blocks(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        self.make_source(rows)
        os.remove(os.path.join(self.p73, "promoted", D.F_MANIFEST))
        r = self.run75()
        self.assertEqual(r["readiness"], P.SOURCE_REQUIRED)

    def test_tampered_manifest_blocks(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        self.make_source(rows)
        mpath = os.path.join(self.p73, "promoted", D.F_MANIFEST)
        doc = read_json(mpath)
        doc["deterministic_content_sha256"] = ZERO
        with open(mpath, "wb") as f:
            f.write(json.dumps(doc).encode("utf-8"))
        r = self.run75()
        self.assertEqual(r["readiness"], P.SOURCE_BLOCKED)

    def test_tampered_artifact_blocks(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        self.make_source(rows)
        apath = os.path.join(self.p73, "promoted", D.F_ANALYSIS)
        with open(apath, "ab") as f:
            f.write(b" ")
        r = self.run75()
        self.assertEqual(r["readiness"], P.SOURCE_BLOCKED)

    def test_malformed_source_blocks(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        self.make_source(rows, )
        # rewrite promoted with malformed analysis bytes that still match the manifest output hash
        write_promoted(self.tmp, build_analysis(rows), analysis_bytes=b"{not json")
        r = self.run75()
        self.assertEqual(r["readiness"], P.SOURCE_BLOCKED)

    def test_nan_source_blocks(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        write_promoted(self.tmp, build_analysis(rows),
                       analysis_bytes=b'{"schema_version":"phase7-3-analysis-v1","x":NaN}')
        self.p73 = os.path.join(self.tmp, "7.3")
        r = self.run75()
        self.assertEqual(r["readiness"], P.SOURCE_BLOCKED)

    def test_missing_review_state_required(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        self.make_source(rows)  # no review state written
        r = self.run75()
        self.assertEqual(r["readiness"], P.REVIEW_STATE_REQUIRED)
        self.assertEqual(P.exit_code_for(r["readiness"]), 2)

    def test_corrupt_review_state_blocks(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        self.make_source(rows)
        os.makedirs(os.path.dirname(D.review_state_path(self.p74)), exist_ok=True)
        with open(D.review_state_path(self.p74), "wb") as f:
            f.write(b"{not json")
        with self.assertRaises(P.DecisionPackageError):
            self.run75()

    def test_invalid_reference_date_raises(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])])
        with self.assertRaises(P.DecisionPackageError):
            self.run75(reference_date="July 21")


# ================================================================ integration: source selection & counts
class SourceSelection(Base):
    def test_promoted_precedence_over_final(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])])
        # add a stale final/ dir alongside promoted/
        promoted = os.path.join(self.p73, "promoted")
        final = os.path.join(self.p73, "final")
        shutil.copytree(promoted, final)
        r = self.run75()
        lineage = json.loads(self.read(r["package_dir"], "source_lineage.json"))
        self.assertEqual(lineage["phase7_3"]["source_dir_type"], "promoted")

    def test_row_and_analyzed_counts(self):
        rows = [make_row(i, owner_review_label=AA.RL_NEGATIVE) for i in range(4)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])])
        r = self.run75()
        man = json.loads(self.read(r["package_dir"], "package_manifest.json"))
        self.assertEqual(man["source_rows"], 4)
        self.assertEqual(man["analyzed_rows"], 4)

    def test_lineage_records_hashes(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])])
        r = self.run75()
        lineage = json.loads(self.read(r["package_dir"], "source_lineage.json"))
        self.assertEqual(lineage["phase7_3"]["manifest_sha256"], src.manifest_hash)
        self.assertTrue(P._is_hex64(lineage["phase7_4"]["review_state_aggregate_sha256"]))


# ================================================================ safety: content & wording
class SafetyContent(Base):
    def _pkg(self, note="=HYPERLINK(\"http://x\")"):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])], note=note)
        return self.run75()["package_dir"]

    def test_disclaimer_in_every_relevant_file(self):
        pkg = self._pkg()
        for name in ("OWNER_READ_FIRST.md", "executive_summary.md", "decision_details.md",
                     "manual_action_checklist.tsv", "excluded_items.tsv",
                     "manual_action_checklist.json", "excluded_items.json", "package_manifest.json"):
            text = self.read(pkg, name).lower()
            self.assertIn("no amazon action has been performed", text, name)

    def test_no_execution_wording(self):
        pkg = self._pkg()
        forbidden = ("has been executed", "has been applied", "has been uploaded", "sent to amazon",
                     "created in amazon", "action completed", "successfully changed")
        for name in os.listdir(pkg):
            text = self.read(pkg, name).lower()
            for phrase in forbidden:
                self.assertNotIn(phrase, text, f"{phrase!r} in {name}")

    def test_formula_injection_neutralized_in_tsv(self):
        pkg = self._pkg(note="=SUM(A1:A9)")
        tsv = self.read(pkg, "manual_action_checklist.tsv")
        data_lines = [ln for ln in tsv.split("\n") if ln and not ln.startswith("#")]
        header = data_lines[0].split("\t")
        note_idx = header.index("owner_note")
        row = data_lines[1].split("\t")
        self.assertTrue(row[note_idx].startswith("'="))

    def test_unicode_owner_note_preserved(self):
        pkg = self._pkg(note="Cần kiểm tra tồn kho — lợi nhuận thấp")
        items = json.loads(self.read(pkg, "manual_action_checklist.json"))["items"]
        self.assertEqual(items[0]["owner_note"], "Cần kiểm tra tồn kho — lợi nhuận thấp")

    def test_tsv_rows_equal_columns(self):
        rows = [make_row(i, owner_review_label=AA.RL_NEGATIVE) for i in range(2)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(r) for r in rows])
        pkg = self.run75()["package_dir"]
        for name in ("manual_action_checklist.tsv", "excluded_items.tsv"):
            lines = [ln for ln in self.read(pkg, name).split("\n") if ln and not ln.startswith("#")]
            ncol = len(lines[0].split("\t"))
            for ln in lines:
                self.assertEqual(len(ln.split("\t")), ncol, name)

    def test_no_amazon_upload_or_payload_files(self):
        pkg = self._pkg()
        man = json.loads(self.read(pkg, "package_manifest.json"))
        self.assertFalse(man["amazon_action_performed"])
        for k, v in man["amazon_boundary"].items():
            self.assertEqual(v, 0, k)
        names = " ".join(os.listdir(pkg)).lower()
        for bad in ("bulk", "sp-api", "sp_api", "ads-api", "payload", ".xlsm", "campaign-upload"):
            self.assertNotIn(bad, names)

    def test_manifest_counters_and_flags(self):
        pkg = self._pkg()
        man = json.loads(self.read(pkg, "package_manifest.json"))
        self.assertTrue(all(man["this_session_never"].values()))
        self.assertEqual(man["amazon_boundary"]["amazon_connections"], 0)
        self.assertEqual(man["amazon_boundary"]["external_network_calls"], 0)


# ================================================================ safety: no forbidden integrations in source
class NoForbiddenIntegrations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(P.__file__, encoding="utf-8") as f:
            cls.src = f.read()

    def test_no_network_or_client_imports(self):
        # Functional integration patterns only — the module docstring legitimately names SP-API / Ads
        # API inside its safety disclaimer, which is not integration code.
        for pat in (r"^\s*import\s+requests", r"^\s*import\s+httpx", r"^\s*import\s+aiohttp",
                    r"^\s*import\s+selenium", r"^\s*import\s+playwright", r"\bwebdriver\b",
                    r"^\s*import\s+socket", r"^\s*from\s+http\.client", r"urllib\.request",
                    r"\bboto3\b", r"\.amazonaws\.com", r"sellercentral", r"advertising\.amazon"):
            self.assertIsNone(re.search(pat, self.src, re.MULTILINE | re.IGNORECASE),
                              f"forbidden pattern present: {pat}")

    def test_no_subprocess_or_shell(self):
        for pat in (r"\bsubprocess\b", r"\bos\.system\b", r"\bos\.popen\b", r"\bpty\b"):
            self.assertIsNone(re.search(pat, self.src), pat)

    def test_no_eval_or_exec(self):
        self.assertIsNone(re.search(r"\beval\s*\(", self.src))
        self.assertIsNone(re.search(r"\bexec\s*\(", self.src))

    def test_no_float_in_authoritative_model(self):
        self.assertIsNone(re.search(r"\bfloat\s*\(", self.src))

    def test_no_credential_or_token_storage(self):
        # only harmless zero-counters may mention these words; no functional storage APIs.
        for pat in (r"keyring", r"set_cookie", r"cookiejar", r"oauth", r"access_token\s*="):
            self.assertIsNone(re.search(pat, self.src, re.IGNORECASE), pat)


# ================================================================ CLI
class Cli(Base):
    def _args(self, base, extra=None):
        a = ["--base-dir", base, "--phase7-3-dir", self.p73, "--phase7-4-dir", self.p74,
             "--reference-date", REF]
        return a + (extra or [])

    def test_help_exits_zero(self):
        with self.assertRaises(SystemExit) as cm, redirect_stdout(io.StringIO()):
            P.main(["-h"])
        self.assertEqual(cm.exception.code, 0)

    def test_reference_date_required(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        self.make_source(rows)
        with self.assertRaises(SystemExit) as cm, redirect_stderr(io.StringIO()):
            P.main(["--phase7-3-dir", self.p73, "--phase7-4-dir", self.p74])
        self.assertNotEqual(cm.exception.code, 0)

    def test_cli_summary_and_counters(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])])
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = P.main(self._args(self.p75))
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("package_readiness=SESSION7_5_PACKAGE_READY", out)
        self.assertIn("eligible_actions=1", out)
        for line in ("amazon_connections=0", "amazon_sp_api_calls=0", "amazon_ads_api_calls=0",
                     "amazon_mutations=0", "external_network_calls=0"):
            self.assertIn(line, out)
        self.assertRegex(out, r"package_id=pkg-[0-9a-f]{16}")

    def test_cli_validate_only_no_package(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])])
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = P.main(self._args(self.p75, ["--validate-only"]))
        self.assertEqual(code, 0)
        self.assertIn("validate_only=true", buf.getvalue())
        self.assertFalse(os.path.isdir(os.path.join(self.p75, "packages")))

    def test_cli_blocked_returns_nonzero(self):
        self.p73 = os.path.join(self.tmp, "empty73")
        os.makedirs(self.p73, exist_ok=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = P.main(self._args(self.p75))
        self.assertEqual(code, 2)

    def test_cli_json_format(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])])
        buf = io.StringIO()
        with redirect_stdout(buf):
            P.main(self._args(self.p75, ["--format", "json"]))
        data = json.loads(buf.getvalue())
        self.assertEqual(data["package_readiness"], "SESSION7_5_PACKAGE_READY")

    def test_include_deferred_summary_adds_files(self):
        rows = [make_row(1, owner_review_label=AA.RL_NEGATIVE)]
        src = self.make_source(rows)
        self.approve(src, [self.st_id(rows[0])], status="DEFERRED")
        r = self.run75(include_deferred_summary=True)
        self.assertTrue(os.path.isfile(os.path.join(r["package_dir"], "deferred_items.tsv")))
        self.assertTrue(os.path.isfile(os.path.join(r["package_dir"], "policy_requirements.md")))


if __name__ == "__main__":
    unittest.main()
