#!/usr/bin/env python3
"""Phase 7.7 — offline Outcome Follow-up.

Every test runs OFFLINE and asserts the permanent Amazon boundary throughout: Phase 7.7 reads only
an accepted Phase 7.6 tracker state and a later accepted Phase 7.3 analysis, connects to nothing,
performs no Amazon action, never claims the owner-recorded action caused any result, and never claims
Amazon confirmed anything. Synthetic fixtures only — no real owner runtime data is committed. The
whole pipeline (Phase 7.3 -> 7.4 -> 7.5 -> 7.6) is produced with the accepted generators (reusing the
accepted Phase 7.4/7.5/7.6 fixture builders) so every input contract is byte-faithful.
"""
import ast
import hashlib
import io
import json
import os
import py_compile
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from production import phase7_outcome_followup as F        # noqa: E402
from production import phase7_manual_action_tracker as T    # noqa: E402
from production import phase7_owner_decision_package as P5   # noqa: E402
from production import phase7_owner_dashboard as D           # noqa: E402
from production import phase7_ads_analysis as AA             # noqa: E402
from tests import test_phase7_4_owner_dashboard as T4        # noqa: E402  make_row/build_analysis/write_promoted/reseal

REF75 = "2026-07-21"                    # reference date used to build the 7.5 package + 7.6 records
REF77 = "2026-07-31"                    # 7.7 reference date (after the after-window)
NOW = lambda: datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)   # noqa: E731
NOW2 = lambda: datetime(2027, 1, 2, 3, 4, 5, tzinfo=timezone.utc)    # noqa: E731  a different clock
APPROVED = "APPROVED_FOR_MANUAL_ACTION"
WIN = ("2026-06-01", "2026-06-29", "2026-07-01", "2026-07-31")
COMPLETED = "2026-06-30"
MODULE_PATH = F.__file__

# one entity's stable identity (currency-independent match key + currency)
ENT = dict(campaign="C1", ad_group="G1", targeting="wasteful", customer_search_term="wasteful",
           match_type=None, currency="USD")

# a "before the action" row (approved as a manual-negative candidate): spend, no sales
DEF_BEFORE = dict(owner_review_label=AA.RL_NEGATIVE, primary_classification="HIGH_SPEND_NO_SALES",
                  start_date="2026-06-15", end_date="2026-06-15",
                  impressions=800, clicks=20, spend="25.00", sales="0.00", orders=0, units=0)
# an "after the action" row (data only): same spend, now converting -> observed improvement
DEF_AFTER = dict(owner_review_label="INSUFFICIENT_EVIDENCE",
                 start_date="2026-07-15", end_date="2026-07-15",
                 impressions=800, clicks=20, spend="25.00", sales="120.00", orders=6, units=6)


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _evidence(days):
    return {"metric_states": {"clicks": "PRESENT", "impressions": "PRESENT", "spend": "PRESENT",
                              f"orders_{days}d": "PRESENT", f"sales_{days}d": "PRESENT",
                              f"units_{days}d": "PRESENT"},
            "target_acos": "0.300000", "target_acos_source": "NEUTRAL_DEFAULT_OWNER_CONFIGURABLE",
            "high_acos_threshold": "0.375000", "low_acos_threshold": "0.225000",
            "outside_lookback_window": False, "thresholds_policy_id": "phase7-3-analysis-thresholds-v1",
            "minimum_clicks_for_conversion_judgment": 10, "minimum_spend_for_no_sale_risk": "5.00"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p77-")
        self._n = 0

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- workspace helpers ---------------------------------------------------------------------
    def newdir(self, tag):
        self._n += 1
        d = os.path.join(self.tmp, f"{tag}-{self._n}")
        return d

    def promote(self, rows, *, mutate=None):
        analysis = T4.build_analysis(rows)
        if mutate:
            mutate(analysis)
        base = self.newdir("p73")
        return T4.write_promoted(base, analysis)

    def approve(self, p73, lineage_hashes):
        p74 = self.newdir("p74")
        src = D.load_source(p73)
        views = D.build_views(src)
        D.save_review(p74, src, views,
                      [{"entity_id": "st:" + lh, "review_status": APPROVED, "owner_note": "n"}
                       for lh in lineage_hashes], now=NOW)
        return p74

    def package(self, p73, p74, *, now=NOW):
        p75 = self.newdir("p75")
        r5 = P5.run(p75, p73, p74, REF75, now=now)
        return p75, r5

    def item_ids(self, r5):
        items = json.loads(open(os.path.join(r5["package_dir"], "manual_action_checklist.json"),
                                encoding="utf-8").read())["items"]
        return [it["package_item_id"] for it in items]

    def tracker_init(self, p75, pkg_id, *, now=NOW):
        base76 = self.newdir("p76")
        T.run_initialize(base76, p75, REF75, package_id=pkg_id, now=now)
        return base76

    def complete(self, base76, p75, pkg_id, item_id, *, status=None, over=None, now=NOW):
        status = status or T.S_COMPLETED
        if status == T.S_COMPLETED:
            updates = dict(owner_note="done manually", owner_checked_date=COMPLETED,
                           owner_completed_date=COMPLETED)
        else:
            updates = dict(owner_note="owner note")
        if over:
            updates.update(over)
        return T.run_update(base76, p75, REF75, package_id=pkg_id, package_item_id=item_id,
                            status=status, updates=updates, confirm_check=True, confirm_action=True,
                            now=now)

    # ---- one-entity pipeline -------------------------------------------------------------------
    def pipeline(self, *, before=None, after=None, extra=(), status=T.S_COMPLETED, over=None, now=NOW):
        b = dict(ENT); b.update(DEF_BEFORE); b.update(before or {})
        a = dict(ENT); a.update(DEF_AFTER); a.update(after or {})
        r_before = T4.make_row(1, **b)
        r_after = T4.make_row(2, **a)
        rows = [r_before, r_after] + list(extra)
        p73 = self.promote(rows)
        p74 = self.approve(p73, [r_before["lineage_hash"]])
        p75, r5 = self.package(p73, p74, now=now)
        self.assertEqual(r5["readiness"], P5.PKG_READY, r5)
        item_id = self.item_ids(r5)[0]
        base76 = self.tracker_init(p75, r5["package_id"], now=now)
        ru = None
        if status is not None:
            ru = self.complete(base76, p75, r5["package_id"], item_id, status=status, over=over, now=now)
        return SimpleNamespace(p73=p73, p74=p74, p75=p75, base76=base76, pkg_id=r5["package_id"],
                               item_id=item_id, rows=rows, r_before=r_before, r_after=r_after,
                               update=ru, trk_id=T.tracker_record_id(r5["package_id"], item_id))

    # ---- two-entity pipeline (for "not collapsed" tests) ---------------------------------------
    def pipeline_two(self, differ):
        """Two entities identical except for one identity field; both completed."""
        e1 = dict(ENT)
        e2 = dict(ENT)
        e2[differ] = (ENT.get(differ) or "X") + "-2" if differ != "currency" else "EUR"
        specs = []
        rows = []
        for i, e in enumerate((e1, e2), start=0):
            b = dict(e); b.update(DEF_BEFORE)
            a = dict(e); a.update(DEF_AFTER)
            rb = T4.make_row(1 + i * 2, **b)
            ra = T4.make_row(2 + i * 2, **a)
            rows += [rb, ra]
            specs.append(rb)
        p73 = self.promote(rows)
        p74 = self.approve(p73, [s["lineage_hash"] for s in specs])
        p75, r5 = self.package(p73, p74)
        self.assertEqual(r5["readiness"], P5.PKG_READY, r5)
        base76 = self.tracker_init(p75, r5["package_id"])
        item_ids = self.item_ids(r5)
        self.assertEqual(len(item_ids), 2, "expected two package items")
        for iid in item_ids:
            self.complete(base76, p75, r5["package_id"], iid)
        return SimpleNamespace(p73=p73, p75=p75, base76=base76, pkg_id=r5["package_id"], rows=rows)

    # ---- duplicate pipeline (two items -> one 7.7 canonical identity) --------------------------
    def pipeline_dupe(self, *, conflict):
        A = T4.make_row(1, **dict(ENT, **dict(DEF_BEFORE, start_date="2026-06-10", end_date="2026-06-10")))
        B = T4.make_row(2, **dict(ENT, **dict(DEF_BEFORE, start_date="2026-06-20", end_date="2026-06-20")))
        after = T4.make_row(3, **dict(ENT, **DEF_AFTER))
        rows = [A, B, after]
        p73 = self.promote(rows)
        p74 = self.approve(p73, [A["lineage_hash"], B["lineage_hash"]])
        p75, r5 = self.package(p73, p74)
        self.assertEqual(r5["readiness"], P5.PKG_READY, r5)
        item_ids = self.item_ids(r5)
        self.assertEqual(len(item_ids), 2)
        base76 = self.tracker_init(p75, r5["package_id"])
        self.complete(base76, p75, r5["package_id"], item_ids[0], over={"owner_completed_date": "2026-06-25"})
        self.complete(base76, p75, r5["package_id"], item_ids[1],
                      over={"owner_completed_date": "2026-06-26" if conflict else "2026-06-25"})
        return SimpleNamespace(p73=p73, p75=p75, base76=base76, pkg_id=r5["package_id"], rows=rows)

    # ---- run 7.7 -------------------------------------------------------------------------------
    def run77(self, ns, *, source=None, base=None, windows=WIN, ref=REF77, **kw):
        base = base or self.newdir("p77")
        res = F.run(base, source or ns.p73, ns.base76, ns.p75, ref, windows, **kw)
        return res, res.get("output_dir")

    # ---- output readers ------------------------------------------------------------------------
    def read_json(self, out_dir, name):
        return json.loads(open(os.path.join(out_dir, name), encoding="utf-8").read())

    def read_text(self, out_dir, name):
        return open(os.path.join(out_dir, name), encoding="utf-8").read()

    def read_bytes(self, out_dir, name):
        return open(os.path.join(out_dir, name), "rb").read()

    def followups(self, out_dir):
        return self.read_json(out_dir, "outcome_status.json")["followups"]

    def excluded(self, out_dir):
        return self.read_json(out_dir, "excluded_followups.json")["excluded_followups"]

    def only(self, out_dir):
        fu = self.followups(out_dir)
        self.assertEqual(len(fu), 1, fu)
        return fu[0]

    def policy_file(self, **over):
        doc = dict(F.DEFAULT_POLICY)
        doc.update(over)
        path = os.path.join(self.newdir("policy"))
        os.makedirs(path, exist_ok=True)
        p = os.path.join(path, "policy.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps(doc))
        return p

    def tree_hash(self, path):
        h = hashlib.sha256()
        for root, dirs, files in os.walk(path):
            dirs.sort()
            for fn in sorted(files):
                fp = os.path.join(root, fn)
                h.update(os.path.relpath(fp, path).replace("\\", "/").encode())
                with open(fp, "rb") as f:
                    h.update(f.read())
        return h.hexdigest()


# ================================================================ CLI / arguments
class TestCLI(Base):
    def test_01_cli_help(self):
        buf = io.StringIO()
        with self.assertRaises(SystemExit) as cm, redirect_stdout(buf):
            F.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("Outcome Follow-up", buf.getvalue())

    def test_02_missing_arguments(self):
        err = io.StringIO()
        with self.assertRaises(SystemExit) as cm, redirect_stderr(err):
            F.main([])          # missing required --reference-date and windows
        self.assertNotEqual(cm.exception.code, 0)

    def test_88b_cli_end_to_end_exit_zero(self):
        ns = self.pipeline()
        base = self.newdir("p77")
        argv = ["--base-dir", base, "--phase7-3-dir", ns.p73, "--phase7-6-dir", ns.base76,
                "--phase7-5-dir", ns.p75, "--reference-date", REF77,
                "--before-start", WIN[0], "--before-end", WIN[1],
                "--after-start", WIN[2], "--after-end", WIN[3]]
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = F.main(argv)
        self.assertEqual(code, 0)
        self.assertIn("SESSION7_7_FOLLOWUP_READY", buf.getvalue())


# ================================================================ tracker / source input states
class TestInputStates(Base):
    def test_03_valid_empty_tracker(self):
        ns = self.pipeline()
        empty76 = self.newdir("empty76")
        os.makedirs(empty76)
        res, out = F.run(self.newdir("p77"), ns.p73, empty76, ns.p75, REF77, WIN), None
        res = F.run(self.newdir("p77b"), ns.p73, empty76, ns.p75, REF77, WIN)
        self.assertEqual(res["readiness"], F.FOLLOWUP_READY_EMPTY)
        self.assertEqual(F.exit_code_for(res["readiness"]), 0)
        self.assertEqual(res["counts"]["eligible_followup_count"], 0)

    def test_04_valid_populated_tracker(self):
        ns = self.pipeline()
        res, out = self.run77(ns)
        self.assertEqual(res["readiness"], F.FOLLOWUP_READY)
        self.assertEqual(res["counts"]["eligible_followup_count"], 1)

    def test_05_missing_tracker(self):
        ns = self.pipeline()
        missing = os.path.join(self.tmp, "does-not-exist")
        res = F.run(self.newdir("p77"), ns.p73, missing, ns.p75, REF77, WIN)
        self.assertEqual(res["readiness"], F.TRACKER_REQUIRED)
        self.assertNotEqual(F.exit_code_for(res["readiness"]), 0)

    def test_06_malformed_current_state(self):
        ns = self.pipeline()
        spath, _ = T.state_paths(ns.base76)
        with open(spath, "w", encoding="utf-8") as f:
            f.write("{ this is not json")
        res, _ = self.run77(ns)
        self.assertEqual(res["readiness"], F.TRACKER_BLOCKED)
        self.assertNotEqual(F.exit_code_for(res["readiness"]), 0)

    def test_07_broken_history_chain(self):
        ns = self.pipeline()
        _, hpath = T.state_paths(ns.base76)
        lines = open(hpath, encoding="utf-8").read().splitlines()
        ev = json.loads(lines[-1])
        ev["previous_record_hash"] = "0" * 64          # break the chain link
        lines[-1] = json.dumps(ev)
        with open(hpath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        res, _ = self.run77(ns)
        self.assertEqual(res["readiness"], F.TRACKER_BLOCKED)

    def test_08_current_history_mismatch(self):
        ns = self.pipeline()
        _, hpath = T.state_paths(ns.base76)
        os.remove(hpath)                               # current.json without history
        res, _ = self.run77(ns)
        self.assertEqual(res["readiness"], F.TRACKER_BLOCKED)

    def test_09_invalid_package_lineage(self):
        ns = self.pipeline()
        # corrupt a package artifact -> binding is no longer CURRENT -> PACKAGE_CHANGED exclusion
        pkg_dir = os.path.join(ns.p75, "packages", ns.pkg_id)
        cpath = os.path.join(pkg_dir, "manual_action_checklist.json")
        data = json.loads(open(cpath, encoding="utf-8").read())
        data["meta"]["tampered"] = True
        with open(cpath, "w", encoding="utf-8") as f:
            f.write(json.dumps(data))
        res, out = self.run77(ns)
        self.assertEqual(res["counts"]["eligible_followup_count"], 0)
        exc = self.excluded(out)
        self.assertEqual(len(exc), 1)
        self.assertEqual(exc[0]["outcome_classification"], F.PACKAGE_CHANGED)

    def test_10_missing_analytical_source(self):
        ns = self.pipeline()
        empty = self.newdir("nosrc")
        os.makedirs(empty)
        res = F.run(self.newdir("p77"), empty, ns.base76, ns.p75, REF77, WIN)
        self.assertEqual(res["readiness"], F.SOURCE_REQUIRED)
        self.assertNotEqual(F.exit_code_for(res["readiness"]), 0)

    def test_11_tampered_analytical_source(self):
        ns = self.pipeline()
        apath = os.path.join(ns.p73, "promoted", "analysis.json")
        with open(apath, "ab") as f:
            f.write(b"\n ")                             # change bytes, do NOT reseal
        res, _ = self.run77(ns)
        self.assertEqual(res["readiness"], F.SOURCE_BLOCKED)

    def test_12_tampered_manifest(self):
        ns = self.pipeline()
        mpath = os.path.join(ns.p73, "promoted", "analysis-manifest.json")
        m = json.loads(open(mpath, encoding="utf-8").read())
        m["output_hashes"]["analysis.json"] = "0" * 64   # lie about the analysis hash
        with open(mpath, "w", encoding="utf-8") as f:
            f.write(json.dumps(m))
        res, _ = self.run77(ns)
        self.assertEqual(res["readiness"], F.SOURCE_BLOCKED)


# ================================================================ window validation
class TestWindows(Base):
    def _blocked(self, ns, *, ref=REF77, windows=WIN):
        res = F.run(self.newdir("p77"), ns.p73, ns.base76, ns.p75, ref, windows)
        return res

    def test_13_invalid_reference_date(self):
        ns = self.pipeline()
        res = self._blocked(ns, ref="2026-13-40")
        self.assertEqual(res["readiness"], F.WINDOW_NOT_READY)
        self.assertNotEqual(F.exit_code_for(res["readiness"]), 0)

    def test_14_invalid_before_window(self):
        ns = self.pipeline()
        res = self._blocked(ns, windows=("nope", WIN[1], WIN[2], WIN[3]))
        self.assertEqual(res["readiness"], F.WINDOW_NOT_READY)

    def test_15_invalid_after_window(self):
        ns = self.pipeline()
        res = self._blocked(ns, windows=(WIN[0], WIN[1], WIN[2], "2026-99-99"))
        self.assertEqual(res["readiness"], F.WINDOW_NOT_READY)

    def test_16_overlapping_windows(self):
        ns = self.pipeline()
        res = self._blocked(ns, windows=("2026-06-01", "2026-07-10", "2026-07-01", "2026-07-31"))
        self.assertEqual(res["readiness"], F.WINDOW_NOT_READY)
        self.assertTrue(any("OVERLAP" in r["detail"] for r in res["reasons"]), res["reasons"])

    def test_17_reversed_window(self):
        ns = self.pipeline()
        res = self._blocked(ns, windows=("2026-06-29", "2026-06-01", "2026-07-01", "2026-07-31"))
        self.assertEqual(res["readiness"], F.WINDOW_NOT_READY)
        self.assertTrue(any("REVERSED" in r["detail"] for r in res["reasons"]), res["reasons"])

    def test_18_window_not_ready_future(self):
        ns = self.pipeline()
        # after_end later than the reference date -> not yet observable
        res = self._blocked(ns, ref="2026-07-10", windows=("2026-06-01", "2026-06-29", "2026-07-01", "2026-07-31"))
        self.assertEqual(res["readiness"], F.WINDOW_NOT_READY)

    def test_18b_window_not_ready_minimum_followup(self):
        ns = self.pipeline(over={"owner_completed_date": "2026-06-30"})
        res = F.run(self.newdir("p77"), ns.p73, ns.base76, ns.p75, REF77, WIN,
                    minimum_followup_days=60)
        # after window opens 2026-07-01, only 1 day after the action < 60 -> per-record WINDOW_NOT_READY
        self.assertEqual(res["counts"]["eligible_followup_count"], 0)
        self.assertEqual(res["readiness"], F.WINDOW_NOT_READY)


# ================================================================ eligibility by owner status
class TestEligibility(Base):
    def test_19_eligible_manually_completed(self):
        ns = self.pipeline()
        res, out = self.run77(ns)
        self.assertEqual(res["counts"]["eligible_followup_count"], 1)
        self.assertEqual(self.only(out)["owner_status"], T.S_COMPLETED)

    def _excluded_status(self, status):
        ns = self.pipeline(status=status)
        res, out = self.run77(ns)
        self.assertEqual(res["counts"]["eligible_followup_count"], 0)
        return res, out

    def test_20_pending_excluded(self):
        ns = self.pipeline(status=None)   # left PENDING by initialize
        res, out = self.run77(ns)
        self.assertEqual(res["counts"]["eligible_followup_count"], 0)
        self.assertEqual(self.excluded(out)[0]["outcome_classification"], F.NOT_ELIGIBLE_STATUS)
        self.assertEqual(self.excluded(out)[0]["owner_status"], T.S_PENDING)

    def test_21_skipped_excluded(self):
        res, out = self._excluded_status(T.S_SKIPPED)
        self.assertEqual(self.excluded(out)[0]["outcome_classification"], F.NOT_ELIGIBLE_STATUS)

    def test_22_needs_review_excluded(self):
        res, out = self._excluded_status(T.S_NEEDS_REVIEW)
        self.assertEqual(self.excluded(out)[0]["owner_status"], T.S_NEEDS_REVIEW)

    def test_23_unable_to_verify_excluded(self):
        res, out = self._excluded_status(T.S_UNABLE)
        self.assertEqual(self.excluded(out)[0]["owner_status"], T.S_UNABLE)

    def test_24_reverted_handled_separately(self):
        ns = self.pipeline(status=T.S_REVERTED)
        res, out = self.run77(ns)
        self.assertEqual(res["counts"]["eligible_followup_count"], 0)
        self.assertEqual(res["counts"]["reverted_count"], 1)
        exj = self.read_json(out, "excluded_followups.json")
        self.assertEqual(len(exj["reverted_records"]), 1)
        self.assertEqual(exj["reverted_records"][0]["outcome_classification"], F.REVERTED_SEPARATE)
        # a reverted record is NOT merged into the excluded-followups list
        self.assertTrue(all(e["outcome_classification"] != F.REVERTED_SEPARATE
                            for e in exj["excluded_followups"]))

    def test_25_stale_tracker_record_excluded(self):
        # a record whose package binding drifted is stale -> excluded (not silently followed up)
        ns = self.pipeline()
        pkg_dir = os.path.join(ns.p75, "packages", ns.pkg_id)
        os.remove(os.path.join(pkg_dir, "source_lineage.json"))   # break package integrity
        res, out = self.run77(ns)
        self.assertEqual(res["counts"]["eligible_followup_count"], 0)
        self.assertEqual(self.excluded(out)[0]["outcome_classification"], F.PACKAGE_CHANGED)


# ================================================================ entity matching
class TestMatching(Base):
    def test_26_stable_entity_match(self):
        ns = self.pipeline()
        fu = self.only(self.run77(ns)[1])
        self.assertEqual(fu["entity_identity"]["campaign"], "C1")
        self.assertEqual(fu["entity_identity"]["customer_search_term"], "wasteful")
        self.assertEqual(fu["before_row_count"], 1)
        self.assertEqual(fu["after_row_count"], 1)

    def test_27_ambiguous_entity_match(self):
        # same identity present under two marketplaces -> ambiguous, excluded
        ns = self.pipeline(before={"canonical_row_key": "t|mk=US|r"},
                           after={"canonical_row_key": "t|mk=UK|r"})
        res, out = self.run77(ns)
        self.assertEqual(res["counts"]["eligible_followup_count"], 0)
        self.assertEqual(self.excluded(out)[0]["outcome_classification"], F.AMBIGUOUS_ENTITY_MATCH)
        self.assertEqual(res["readiness"], F.ENTITY_MATCH_REQUIRED)

    def test_28_missing_entity(self):
        ns = self.pipeline()
        # a later 7.3 that does not contain the tracked entity at all
        other = self.promote([T4.make_row(9, campaign="OTHER", ad_group="X", targeting="z",
                                          customer_search_term="zzz", currency="USD")])
        res, out = self.run77(ns, source=other)
        self.assertEqual(res["counts"]["eligible_followup_count"], 0)
        self.assertEqual(self.excluded(out)[0]["outcome_classification"], F.ENTITY_NOT_FOUND)

    def test_29_different_campaign_not_collapsed(self):
        ns = self.pipeline_two("campaign")
        res, out = self.run77(ns)
        self.assertEqual(res["counts"]["eligible_followup_count"], 2)
        self.assertEqual(res["counts"]["duplicate_identical_count"], 0)

    def test_30_different_ad_group_not_collapsed(self):
        ns = self.pipeline_two("ad_group")
        res, out = self.run77(ns)
        self.assertEqual(res["counts"]["eligible_followup_count"], 2)

    def test_31_different_target_not_collapsed(self):
        ns = self.pipeline_two("targeting")
        res, out = self.run77(ns)
        self.assertEqual(res["counts"]["eligible_followup_count"], 2)

    def test_32_different_currency_not_collapsed(self):
        # same entity match key but two currencies present -> ambiguous currency, excluded
        ns = self.pipeline(after={"currency": "EUR"})
        # note: after row now has a different currency; before row is USD. match key ignores currency.
        res, out = self.run77(ns)
        self.assertEqual(res["counts"]["eligible_followup_count"], 0)
        self.assertEqual(self.excluded(out)[0]["outcome_classification"], F.CURRENCY_MISMATCH)

    def test_33_different_attribution_window_not_collapsed(self):
        ns = self.pipeline(after={"evidence": _evidence(14)})   # after row reports a 14-day window
        res, out = self.run77(ns)
        self.assertEqual(res["counts"]["eligible_followup_count"], 0)
        self.assertEqual(self.excluded(out)[0]["outcome_classification"], F.ATTRIBUTION_WINDOW_MISMATCH)


# ================================================================ metrics & deltas
class TestMetrics(Base):
    def test_34_before_metrics_preserved(self):
        fu = self.only(self.run77(self.pipeline())[1])
        self.assertEqual(fu["before_metrics"]["spend"], "25.00")
        self.assertEqual(fu["before_metrics"]["sales"], "0.00")
        self.assertEqual(fu["before_metrics"]["clicks"], 20)

    def test_35_after_metrics_preserved(self):
        fu = self.only(self.run77(self.pipeline())[1])
        self.assertEqual(fu["after_metrics"]["spend"], "25.00")
        self.assertEqual(fu["after_metrics"]["sales"], "120.00")
        self.assertEqual(fu["after_metrics"]["orders"], 6)

    def test_36_absolute_delta(self):
        fu = self.only(self.run77(self.pipeline())[1])
        self.assertEqual(fu["absolute_deltas"]["sales"], "120.00")
        self.assertEqual(fu["absolute_deltas"]["orders"], 6)

    def test_37_percentage_delta(self):
        # before spend 25 -> after spend 25 => 0.00% ; sales 0 -> undefined (zero denominator)
        fu = self.only(self.run77(self.pipeline())[1])
        self.assertEqual(fu["percentage_deltas"]["spend"], "0.0000")
        self.assertIsNone(fu["percentage_deltas"]["sales"])

    def test_38_zero_denominator(self):
        fu = self.only(self.run77(self.pipeline())[1])
        self.assertIsNone(fu["before_metrics"]["acos"])   # sales==0 -> acos undefined, not 0
        self.assertIsNone(fu["percentage_deltas"]["sales"])

    def test_39_and_40_missing_metrics_stay_missing(self):
        # a matched after row with a missing orders metric must stay missing (never 0)
        def drop_orders(analysis):
            for r in analysis["search_term_analysis"]:
                if r["start_date"] == "2026-07-15":
                    r["orders"] = None
                    r["conversion_rate"] = None
        ns = self.pipeline()
        p73b = self.promote_from(ns, drop_orders)
        fu = self.only(self.run77(ns, source=p73b)[1])
        self.assertIsNone(fu["after_metrics"]["orders"])
        self.assertIsNone(fu["absolute_deltas"]["orders"])

    def promote_from(self, ns, mutate):
        """Re-promote the same rows as ns.p73 but mutated (for missing-metric / tamper scenarios)."""
        return self.promote(ns.rows, mutate=mutate)

    def test_41_no_authoritative_float(self):
        src = open(MODULE_PATH, encoding="utf-8").read()
        self.assertNotIn("float(", src)

    def test_42_exact_decimal_values(self):
        # cpc = spend/clicks = 25/20 = 1.25 exactly; acos after = 25/120 = 0.208333
        fu = self.only(self.run77(self.pipeline())[1])
        self.assertEqual(fu["before_metrics"]["cpc"], "1.25")
        self.assertEqual(fu["after_metrics"]["acos"], "0.208333")

    def test_43_multi_currency_separation(self):
        ns = self.pipeline(after={"currency": "EUR"})
        res, out = self.run77(ns)
        self.assertEqual(self.excluded(out)[0]["exclusion_reason"], "MULTIPLE_CURRENCIES")

    def test_44_attribution_window_separation(self):
        ns = self.pipeline(after={"evidence": _evidence(30)})
        res, out = self.run77(ns)
        self.assertEqual(self.excluded(out)[0]["outcome_classification"], F.ATTRIBUTION_WINDOW_MISMATCH)


# ================================================================ classification
class TestClassification(Base):
    def test_45_observed_improvement(self):
        fu = self.only(self.run77(self.pipeline())[1])
        self.assertEqual(fu["outcome_classification"], F.OBSERVED_IMPROVEMENT)

    def test_46_observed_decline(self):
        ns = self.pipeline(before={"sales": "120.00", "orders": 6},
                           after={"sales": "0.00", "orders": 0})
        fu = self.only(self.run77(ns)[1])
        self.assertEqual(fu["outcome_classification"], F.OBSERVED_DECLINE)

    def test_47_observed_mixed(self):
        # cvr & orders up (higher_better), but acos & roas worse -> mixed
        ns = self.pipeline(before={"sales": "100.00", "orders": 5, "spend": "25.00", "clicks": 20},
                           after={"sales": "110.00", "orders": 6, "spend": "50.00", "clicks": 20})
        fu = self.only(self.run77(ns)[1])
        self.assertEqual(fu["outcome_classification"], F.OBSERVED_MIXED)

    def test_48_no_material_change(self):
        ns = self.pipeline(before={"sales": "100.00", "orders": 5, "spend": "25.00", "clicks": 20},
                           after={"sales": "100.00", "orders": 5, "spend": "25.00", "clicks": 20})
        fu = self.only(self.run77(ns)[1])
        self.assertEqual(fu["outcome_classification"], F.OBSERVED_NO_MATERIAL_CHANGE)

    def test_49_insufficient_data(self):
        # after window has no matched rows -> insufficient data (still a documented follow-up)
        ns = self.pipeline(after={"start_date": "2026-08-15", "end_date": "2026-08-15"})
        res, out = self.run77(ns, ref="2026-08-31",
                              windows=("2026-06-01", "2026-06-29", "2026-07-01", "2026-07-31"))
        fu = self.only(out)
        self.assertEqual(fu["outcome_classification"], F.INSUFFICIENT_DATA)
        self.assertEqual(res["counts"]["eligible_followup_count"], 1)

    def test_50_policy_required(self):
        ns = self.pipeline()
        pf = self.policy_file(material_change_ratio=None)
        res = F.run(self.newdir("p77"), ns.p73, ns.base76, ns.p75, REF77, WIN, policy_file=pf)
        self.assertEqual(res["readiness"], F.POLICY_REQUIRED)
        self.assertNotEqual(F.exit_code_for(res["readiness"]), 0)


# ================================================================ causation / disclaimer wording
class TestWording(Base):
    # the exact affirmative causal claims that must never appear (a NEGATED disclaimer is fine).
    CAUSAL_BANNED = ("this action caused the improvement", "the system optimized the campaign",
                     "amazon confirmed the result", "the software changed seller central",
                     "this action caused the result")

    def test_51_no_causal_wording(self):
        ns = self.pipeline()
        _, out = self.run77(ns)
        for name in F.PACKAGE_FILES:
            text = self.read_text(out, name).lower()
            for banned in self.CAUSAL_BANNED:
                self.assertNotIn(banned, text, f"{name} contains banned causal wording {banned!r}")

    def test_52_disclaimer_present(self):
        ns = self.pipeline()
        _, out = self.run77(ns)
        for name in ("OWNER_READ_FIRST.md", "executive_summary.md", "outcome_details.md",
                     "outcome_status.json", "followup_manifest.json"):
            text = self.read_text(out, name)
            self.assertIn("OFFLINE OBSERVATIONAL FOLLOW-UP", text)
        # the required observational phrasings appear
        details = self.read_text(out, "outcome_details.md")
        self.assertIn("Observed after the owner-recorded manual action.", details)
        self.assertIn("does not establish that the owner-recorded action caused the result",
                      self.read_text(out, "OWNER_READ_FIRST.md"))

    def test_52b_insufficient_phrase(self):
        ns = self.pipeline(after={"start_date": "2026-08-15", "end_date": "2026-08-15"})
        _, out = self.run77(ns, ref="2026-08-31",
                            windows=("2026-06-01", "2026-06-29", "2026-07-01", "2026-07-31"))
        self.assertEqual(self.only(out)["outcome_statement"], "Insufficient evidence to determine an outcome.")


# ================================================================ identity / determinism
class TestDeterminism(Base):
    def test_53_and_54_stable_ids(self):
        ns = self.pipeline()
        res1, out1 = self.run77(ns)
        # rebuild the whole pipeline again from identical inputs -> ids unchanged (deterministic)
        ns2 = self.pipeline()
        res2, out2 = self.run77(ns2)
        self.assertEqual(self.only(out1)["followup_record_id"], self.only(out2)["followup_record_id"])
        self.assertEqual(res1["package_id"], res2["package_id"])

    def test_55_56_57_58_deterministic_bytes(self):
        ns = self.pipeline()
        _, a = self.run77(ns)
        _, b = self.run77(ns)
        for name in F.PACKAGE_FILES:
            self.assertEqual(self.read_bytes(a, name), self.read_bytes(b, name), name)

    def test_53b_id_shapes(self):
        ns = self.pipeline()
        res, out = self.run77(ns)
        self.assertRegex(res["package_id"], r"^followup-[0-9a-f]{16}$")
        self.assertRegex(self.only(out)["followup_record_id"], r"^fu-[0-9a-f]{32}$")


# ================================================================ duplicate handling
class TestDuplicates(Base):
    def test_59_and_61_identical_dedup(self):
        ns = self.pipeline_dupe(conflict=False)
        res, out = self.run77(ns)
        self.assertEqual(res["counts"]["eligible_followup_count"], 1)
        self.assertEqual(res["counts"]["duplicate_identical_count"], 1)
        fu = self.only(out)
        self.assertEqual(fu["duplicate_of_count"], 1)
        self.assertEqual(len(fu["duplicate_tracker_record_ids"]), 1)   # lineage preserved

    def test_60_conflicting_exclusion(self):
        ns = self.pipeline_dupe(conflict=True)
        res, out = self.run77(ns)
        self.assertEqual(res["counts"]["eligible_followup_count"], 0)
        self.assertEqual(res["counts"]["duplicate_conflict_count"], 2)
        self.assertEqual(res["readiness"], F.FOLLOWUP_CONFLICT)
        exc = self.excluded(out)
        self.assertEqual(len(exc), 2)
        self.assertTrue(all(e["outcome_classification"] == F.FOLLOWUP_CONFLICT_CLASS for e in exc))


# ================================================================ outputs
class TestOutputs(Base):
    def test_62_empty_output_succeeds(self):
        ns = self.pipeline()
        empty = self.newdir("empty76")
        os.makedirs(empty)
        base = self.newdir("p77")
        res = F.run(base, ns.p73, empty, ns.p75, REF77, WIN)
        self.assertEqual(res["readiness"], F.FOLLOWUP_READY_EMPTY)
        for name in F.PACKAGE_FILES:
            self.assertTrue(os.path.isfile(os.path.join(res["output_dir"], name)), name)

    def test_63_populated_output_succeeds(self):
        ns = self.pipeline()
        res, out = self.run77(ns)
        for name in F.PACKAGE_FILES:
            self.assertTrue(os.path.isfile(os.path.join(out, name)), name)

    def test_64_to_71_all_files_generated(self):
        ns = self.pipeline()
        _, out = self.run77(ns)
        expected = {"OWNER_READ_FIRST.md", "executive_summary.md", "outcome_details.md",
                    "outcome_status.tsv", "outcome_status.json", "excluded_followups.tsv",
                    "excluded_followups.json", "source_lineage.json", "followup_manifest.json"}
        self.assertTrue(expected.issubset(set(os.listdir(out))))
        # the manifest declares a hash for every artifact except itself (a manifest never self-hashes)
        man = self.read_json(out, "followup_manifest.json")
        for name in expected - {"followup_manifest.json"}:
            self.assertIn(name, man["artifact_sha256"])
        self.assertNotIn("followup_manifest.json", man["artifact_sha256"])


# ================================================================ TSV safety
class TestTSVSafety(Base):
    def _cellcheck(self, payload):
        return F._TSV_CELL(payload)

    def test_72_formula_injection_equals(self):
        self.assertTrue(self._cellcheck("=SUM(A1)").startswith("'="))

    def test_73_formula_injection_plus(self):
        self.assertTrue(self._cellcheck("+1+1").startswith("'+"))

    def test_74_formula_injection_minus(self):
        self.assertTrue(self._cellcheck("-cmd").startswith("'-"))

    def test_75_formula_injection_at(self):
        self.assertTrue(self._cellcheck("@evil").startswith("'@"))

    def test_76_formula_injection_tab(self):
        self.assertNotIn("\t", self._cellcheck("a\tb"))

    def test_77_formula_injection_cr(self):
        self.assertNotIn("\r", self._cellcheck("a\rb"))

    def test_78_formula_injection_lf(self):
        self.assertNotIn("\n", self._cellcheck("a\nb"))

    def test_79_negative_decimal_preserved(self):
        self.assertEqual(self._cellcheck("-2.50"), "-2.50")   # a genuine negative decimal is untouched

    def test_79b_negative_delta_in_tsv(self):
        ns = self.pipeline(before={"sales": "120.00", "orders": 6}, after={"sales": "0.00", "orders": 0})
        _, out = self.run77(ns)
        tsv = self.read_text(out, "outcome_status.tsv")
        self.assertIn("-120.00", tsv)          # sales abs delta, not corrupted
        self.assertNotIn("'-120.00", tsv)

    def test_80_vietnamese_unicode_preserved(self):
        ns = self.pipeline(before={"campaign": "Chiến dịch Tết"}, after={"campaign": "Chiến dịch Tết"})
        _, out = self.run77(ns)
        self.assertIn("Chiến dịch Tết", self.read_text(out, "outcome_status.json"))
        self.assertIn("Chiến dịch Tết", self.read_text(out, "outcome_status.tsv"))

    def test_80b_injection_end_to_end(self):
        ns = self.pipeline(before={"campaign": "=WEBSERVICE(1)", "customer_search_term": "wasteful"},
                           after={"campaign": "=WEBSERVICE(1)", "customer_search_term": "wasteful"})
        _, out = self.run77(ns)
        tsv = self.read_text(out, "outcome_status.tsv")
        self.assertIn("'=WEBSERVICE(1)", tsv)

    def test_81_equal_tsv_columns(self):
        ns = self.pipeline()
        _, out = self.run77(ns)
        for name in ("outcome_status.tsv", "excluded_followups.tsv"):
            lines = [ln for ln in self.read_text(out, name).splitlines() if not ln.startswith("#")]
            widths = {ln.count("\t") for ln in lines if ln}
            self.assertLessEqual(len(widths), 1, f"{name} has ragged columns: {widths}")


# ================================================================ atomicity & idempotency
class TestAtomicIdempotent(Base):
    def test_82_atomic_generation(self):
        ns = self.pipeline()
        _, out = self.run77(ns)
        # no leftover temp directory after a successful publish
        followups = os.path.join(os.path.dirname(out), os.path.basename(out))  # out is the package dir
        parent = os.path.dirname(out)
        self.assertFalse(any(n.startswith(".tmp-") for n in os.listdir(parent)))

    def test_83_failed_generation_no_partial(self):
        ns = self.pipeline()
        base = self.newdir("p77")
        # first, a clean successful run
        r1 = F.run(base, ns.p73, ns.base76, ns.p75, REF77, WIN)
        good = self.tree_hash(r1["output_dir"])
        # now corrupt the existing package's manifest so a re-run with the SAME id must block
        with open(os.path.join(r1["output_dir"], "followup_manifest.json"), "ab") as f:
            f.write(b" ")
        r2 = F.run(base, ns.p73, ns.base76, ns.p75, REF77, WIN)
        self.assertEqual(r2["readiness"], F.FOLLOWUP_BLOCKED)
        # no temp partial left behind
        parent = os.path.join(base, "followups")
        self.assertFalse(any(n.startswith(".tmp-") for n in os.listdir(parent)))

    def test_84_previous_valid_output_preserved(self):
        ns = self.pipeline()
        base = self.newdir("p77")
        r1 = F.run(base, ns.p73, ns.base76, ns.p75, REF77, WIN)
        # a different package id (different windows) can be produced alongside without harming the first
        h1 = self.tree_hash(r1["output_dir"])
        F.run(base, ns.p73, ns.base76, ns.p75, "2026-08-31",
              ("2026-06-01", "2026-06-29", "2026-07-01", "2026-08-15"))
        self.assertEqual(self.tree_hash(r1["output_dir"]), h1)

    def test_85_idempotent_reuse(self):
        ns = self.pipeline()
        base = self.newdir("p77")
        r1 = F.run(base, ns.p73, ns.base76, ns.p75, REF77, WIN)
        self.assertEqual(r1["outcome"], "CREATED")
        r2 = F.run(base, ns.p73, ns.base76, ns.p75, REF77, WIN)
        self.assertEqual(r2["outcome"], "IDEMPOTENT_REUSE")
        self.assertEqual(r1["package_id"], r2["package_id"])

    def test_86_existing_altered_output_blocks(self):
        ns = self.pipeline()
        base = self.newdir("p77")
        r1 = F.run(base, ns.p73, ns.base76, ns.p75, REF77, WIN)
        with open(os.path.join(r1["output_dir"], "outcome_status.tsv"), "ab") as f:
            f.write(b"tampered\n")
        r2 = F.run(base, ns.p73, ns.base76, ns.p75, REF77, WIN)
        self.assertEqual(r2["readiness"], F.FOLLOWUP_BLOCKED)


# ================================================================ validate-only
class TestValidateOnly(Base):
    def test_87_validate_only_creates_no_output(self):
        ns = self.pipeline()
        base = self.newdir("p77")
        res = F.run(base, ns.p73, ns.base76, ns.p75, REF77, WIN, validate_only=True)
        self.assertIsNone(res["output_dir"])
        self.assertFalse(os.path.isdir(os.path.join(base, "followups")))

    def test_88_validate_only_valid_empty_exit_zero(self):
        ns = self.pipeline()
        empty = self.newdir("empty76")
        os.makedirs(empty)
        res = F.run(self.newdir("p77"), ns.p73, empty, ns.p75, REF77, WIN, validate_only=True)
        self.assertEqual(F.exit_code_for(res["readiness"]), 0)
        self.assertEqual(res["readiness"], F.FOLLOWUP_READY_EMPTY)

    def test_89_validate_only_blocked_exit_nonzero(self):
        ns = self.pipeline()
        res = F.run(self.newdir("p77"), ns.p73, ns.base76, ns.p75, "bad-date", WIN, validate_only=True)
        self.assertNotEqual(F.exit_code_for(res["readiness"]), 0)


# ================================================================ input immutability
class TestImmutability(Base):
    def test_90_to_93_inputs_immutable(self):
        ns = self.pipeline()
        h73 = self.tree_hash(os.path.join(ns.p73, "promoted"))
        h75 = self.tree_hash(os.path.join(ns.p75, "packages"))
        spath, hpath = T.state_paths(ns.base76)
        h76state = _sha(open(spath, "rb").read())
        h76hist = _sha(open(hpath, "rb").read())
        self.run77(ns)
        self.assertEqual(self.tree_hash(os.path.join(ns.p73, "promoted")), h73, "7.3 promoted changed")
        self.assertEqual(self.tree_hash(os.path.join(ns.p75, "packages")), h75, "7.5 package changed")
        self.assertEqual(_sha(open(spath, "rb").read()), h76state, "7.6 state changed")
        self.assertEqual(_sha(open(hpath, "rb").read()), h76hist, "7.6 history changed")

    def test_93b_no_writes_into_input_dirs(self):
        ns = self.pipeline()
        before = {p: self.tree_hash(p) for p in (ns.p73, ns.p75, ns.base76)}
        self.run77(ns)
        for p, h in before.items():
            self.assertEqual(self.tree_hash(p), h, f"{p} was modified")


# ================================================================ boundary & security
class TestBoundary(Base):
    def test_94_amazon_counters_zero(self):
        ns = self.pipeline()
        res, out = self.run77(ns)
        for k, v in res["amazon_counters"].items():
            self.assertEqual(v, 0, k)
        man = self.read_json(out, "followup_manifest.json")
        for k, v in man["amazon_boundary"].items():
            self.assertEqual(v, 0, k)
        self.assertFalse(man["amazon_action_performed"])
        self.assertFalse(man["causation_asserted"])

    def test_95_external_network_counter_zero(self):
        ns = self.pipeline()
        res, _ = self.run77(ns)
        self.assertEqual(res["amazon_counters"]["external_network_calls"], 0)

    def _imports(self):
        tree = ast.parse(open(MODULE_PATH, encoding="utf-8").read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        return names

    def test_96_no_amazon_imports(self):
        names = self._imports()
        for banned in ("boto3", "botocore", "sp_api", "python_amazon_mws", "amazon_advertising_api",
                       "ad_api", "selenium", "playwright"):
            self.assertNotIn(banned, names)

    def test_97_no_network_imports(self):
        names = self._imports()
        for banned in ("requests", "httpx", "aiohttp", "urllib", "http", "socket", "websocket",
                       "websockets"):
            self.assertNotIn(banned, names)

    def test_98_no_browser_automation(self):
        names = self._imports()
        for banned in ("selenium", "playwright", "pyppeteer", "webdriver"):
            self.assertNotIn(banned, names)

    def test_99_no_subprocess_from_data(self):
        names = self._imports()
        for banned in ("subprocess", "pty", "pickle", "marshal"):
            self.assertNotIn(banned, names)
        src = open(MODULE_PATH, encoding="utf-8").read()
        for banned in ("os.system", "eval(", "exec(", "__import__(", "os.popen"):
            self.assertNotIn(banned, src)

    def test_100_no_credentials_tokens_cookies_sessions(self):
        src = open(MODULE_PATH, encoding="utf-8").read().lower()
        # counters exist to PROVE zero; no functional credential/token/cookie/session store
        for banned in ("password", "secret_key", "access_token=", "set_cookie", "requests.session"):
            self.assertNotIn(banned, src)


# ================================================================ compile / self checks
class TestSelf(Base):
    def test_107_compileall(self):
        py_compile.compile(MODULE_PATH, doraise=True)
        py_compile.compile(__file__, doraise=True)

    def test_this_module_no_amazon_endpoint_literals(self):
        src = open(MODULE_PATH, encoding="utf-8").read().lower()
        for banned in ("amazon.com", "sellingpartnerapi", "advertising-api", "sellercentral"):
            self.assertNotIn(banned, src)


if __name__ == "__main__":
    unittest.main()
