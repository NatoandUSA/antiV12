#!/usr/bin/env python3
"""Phase 7.6 — offline Manual Action Tracker.

Every test runs OFFLINE and asserts the permanent Amazon boundary throughout: Phase 7.6 reads only
accepted Phase 7.5 decision packages, connects to nothing, performs no Amazon action, never infers
completion from a Phase 7.5 approval, and never claims Amazon confirmed anything. Synthetic fixtures
only — no real owner runtime data is committed. Genuine Phase 7.5 packages are produced with the
accepted Phase 7.5 generator (reusing the Phase 7.4/7.5 fixture builders) so the package contract is
byte-faithful.
"""
import ast
import hashlib
import io
import json
import os
import py_compile
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from production import phase7_manual_action_tracker as T   # noqa: E402
from production import phase7_owner_decision_package as P5  # noqa: E402
from production import phase7_owner_dashboard as D          # noqa: E402
from production import phase7_ads_analysis as AA            # noqa: E402
from tests import test_phase7_5_owner_decision_package as T5  # noqa: E402

REF = "2026-07-21"
NOW = lambda: datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)   # noqa: E731
NOW2 = lambda: datetime(2027, 1, 2, 3, 4, 5, tzinfo=timezone.utc)    # noqa: E731  a different clock
APPROVED = "APPROVED_FOR_MANUAL_ACTION"
MODULE_PATH = T.__file__


def file_sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.loads(f.read())


def write_bytes(path, data):
    with open(path, "wb") as f:
        f.write(data)


def tree_hashes(root):
    out = {}
    for dirpath, _dn, filenames in os.walk(root):
        for name in filenames:
            p = os.path.join(dirpath, name)
            out[os.path.relpath(p, root)] = file_sha(p)
    return out


def _module_ast():
    with open(T.__file__, encoding="utf-8") as f:
        return ast.parse(f.read())


def imported_modules(tree):
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    return mods


def called_names(tree):
    """Bare and attribute call names, e.g. 'eval', 'os.system'."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                names.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                base = fn.value.id if isinstance(fn.value, ast.Name) else ""
                names.add(f"{base}.{fn.attr}" if base else fn.attr)
    return names


# ================================================================ base fixture
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p76-")
        self.base = os.path.join(self.tmp, "7.6")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build_package(self, approvals=None, *, name=None, sub="pkgtree"):
        """Produce a genuine Phase 7.5 package on disk via the accepted Phase 7.5 generator."""
        if approvals is None:
            approvals = [(1, AA.RL_NEGATIVE, APPROVED)]
        tree = os.path.join(self.tmp, sub)
        os.makedirs(tree, exist_ok=True)
        rows = [T5.make_row(idx, owner_review_label=label) for (idx, label, _st) in approvals]
        analysis = T5.build_analysis(rows)
        p73 = T5.write_promoted(tree, analysis)
        p74 = os.path.join(tree, "7.4")
        src = D.load_source(p73)
        views = D.build_views(src)
        review = [{"entity_id": "st:" + rows[i]["lineage_hash"], "review_status": st, "owner_note": ""}
                  for i, (_idx, _label, st) in enumerate(approvals)]
        D.save_review(p74, src, views, review, now=NOW)
        p75 = os.path.join(tree, "7.5")
        r = P5.run(p75, p73, p74, REF, now=NOW, package_name=name)
        pkg = {"p75": p75, "package_id": r["package_id"], "package_dir": r["package_dir"],
               "readiness": r["readiness"], "tree": tree, "p73": p73}
        if r["package_dir"]:
            items = read_json(os.path.join(r["package_dir"], "manual_action_checklist.json"))["items"]
        else:
            items = []
        pkg["items"] = items
        return pkg

    def iid(self, pkg, n=0):
        return pkg["items"][n]["package_item_id"]

    def initialize(self, pkg):
        return T.run_initialize(self.base, pkg["p75"], REF, package_id=pkg["package_id"], now=NOW)

    def update(self, pkg, iid, status, **kw):
        return T.run_update(self.base, pkg["p75"], REF, package_id=pkg["package_id"],
                            package_item_id=iid, status=status, now=NOW, **kw)

    def complete(self, pkg, iid, **over):
        kw = dict(updates={"owner_note": "done manually", "owner_checked_date": REF,
                           "owner_completed_date": REF},
                  confirm_check=True, confirm_action=True)
        kw.update(over)
        return self.update(pkg, iid, T.S_COMPLETED, **kw)

    # ---- package tamper helpers (keep integrity valid unless a break is intended) --------------
    def tamper_artifact(self, pkg, name="OWNER_READ_FIRST.md"):
        p = os.path.join(pkg["package_dir"], name)
        with open(p, "ab") as f:
            f.write(b" tampered")

    def set_manifest_field(self, pkg, key, value):
        mpath = os.path.join(pkg["package_dir"], "package_manifest.json")
        m = read_json(mpath)
        m[key] = value
        write_bytes(mpath, (P5.canonical_json(m) + "\n").encode("utf-8"))

    def bump_manifest_runtime(self, pkg):
        mpath = os.path.join(pkg["package_dir"], "package_manifest.json")
        m = read_json(mpath)
        m.setdefault("runtime_metadata", {})["generated_at"] = "2099-01-01T00:00:00Z"
        write_bytes(mpath, (P5.canonical_json(m) + "\n").encode("utf-8"))

    def mutate_checklist(self, pkg, fn, reseal=True):
        cpath = os.path.join(pkg["package_dir"], "manual_action_checklist.json")
        doc = read_json(cpath)
        fn(doc)
        data = (P5.canonical_json(doc) + "\n").encode("utf-8")
        write_bytes(cpath, data)
        if reseal:
            mpath = os.path.join(pkg["package_dir"], "package_manifest.json")
            m = read_json(mpath)
            m["artifact_sha256"]["manual_action_checklist.json"] = hashlib.sha256(data).hexdigest()
            write_bytes(mpath, (P5.canonical_json(m) + "\n").encode("utf-8"))


# ================================================================ 1-2 CLI surface
class CLISurface(Base):
    def _main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        code = None
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = T.main(argv)
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_01_cli_help(self):
        code, out, _err = self._main(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("Manual Action Tracker", out)
        for cmd in ("list", "show", "initialize", "update", "history", "validate", "export"):
            self.assertIn(cmd, out)

    def test_02_missing_arguments(self):
        code, _o, _e = self._main([])                       # no reference-date, no subcommand
        self.assertEqual(code, 2)
        code2, _o2, _e2 = self._main(["--reference-date", REF, "update"])  # missing required update args
        self.assertEqual(code2, 2)

    def test_02b_invalid_reference_date(self):
        code, out, _e = self._main(["--reference-date", "07/21/2026", "list"])
        self.assertEqual(code, 2)
        self.assertIn("INVALID_REFERENCE_DATE", out)


# ================================================================ 3-10 package + init
class PackageAndInit(Base):
    def test_03_valid_package(self):
        pkg = self.build_package()
        loaded = T.load_package(pkg["p75"], package_id=pkg["package_id"])
        self.assertIsNotNone(loaded)
        self.assertTrue(loaded.integrity_ok)
        self.assertIsNone(loaded.error)
        self.assertEqual(len(loaded.items), 1)

    def test_04_empty_package_initialization(self):
        pkg = self.build_package([(1, AA.RL_NEGATIVE, "DEFERRED")])  # nothing approved -> empty pkg
        self.assertEqual(pkg["readiness"], P5.PKG_READY_EMPTY)
        r = self.initialize(pkg)
        self.assertEqual(r["readiness"], T.TRACKER_READY_EMPTY)
        self.assertEqual(r["record_count"], 0)
        self.assertEqual(T.exit_code_for(r["readiness"]), 0)

    def test_05_populated_initialization(self):
        pkg = self.build_package([(1, AA.RL_NEGATIVE, APPROVED), (2, AA.RL_EXACT, APPROVED)])
        r = self.initialize(pkg)
        self.assertEqual(r["readiness"], T.TRACKER_READY)
        self.assertEqual(r["record_count"], 2)
        self.assertEqual(r["created"], 2)
        # idempotent re-initialize creates nothing new
        r2 = self.initialize(pkg)
        self.assertEqual(r2["created"], 0)
        self.assertEqual(r2["record_count"], 2)

    def test_06_invalid_package_manifest(self):
        pkg = self.build_package()
        mpath = os.path.join(pkg["package_dir"], "package_manifest.json")
        write_bytes(mpath, b"{ this is not valid json ")
        r = self.initialize(pkg)
        self.assertEqual(r["readiness"], T.PACKAGE_BLOCKED)
        self.assertEqual(T.exit_code_for(r["readiness"]), 2)

    def test_07_tampered_package_artifact(self):
        pkg = self.build_package()
        self.tamper_artifact(pkg)
        r = self.initialize(pkg)
        self.assertEqual(r["readiness"], T.PACKAGE_BLOCKED)
        self.assertIn("PACKAGE_HASH_MISMATCH", r["reasons"][0]["detail"])

    def test_08_missing_package_item(self):
        pkg = self.build_package()
        r = self.update(pkg, "item:doesnotexist0000000000000000", T.S_NEEDS_REVIEW)
        self.assertEqual(r["readiness"], T.PACKAGE_BLOCKED)
        self.assertEqual(r["reasons"][0]["code"], "NO_PACKAGE_LINEAGE")

    def test_08b_package_required_when_absent(self):
        r = T.run_initialize(self.base, os.path.join(self.tmp, "nope"), REF, package_id="pkg-x", now=NOW)
        self.assertEqual(r["readiness"], T.PACKAGE_REQUIRED)
        self.assertEqual(T.exit_code_for(r["readiness"]), 2)

    def test_09_stable_tracker_record_id(self):
        pkg = self.build_package()
        iid = self.iid(pkg)
        a = T.tracker_record_id(pkg["package_id"], iid)
        b = T.tracker_record_id(pkg["package_id"], iid)
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("trk-"))
        # identity does not depend on order/timestamp/uuid — different item -> different id
        self.assertNotEqual(a, T.tracker_record_id(pkg["package_id"], iid + "x"))

    def test_10_default_pending(self):
        pkg = self.build_package()
        self.initialize(pkg)
        st = T.load_state(self.base)
        rec = list(st["records"].values())[0]
        self.assertEqual(rec["owner_status"], T.S_PENDING)
        self.assertEqual(rec["local_revision"], 1)


# ================================================================ 11-24 statuses + completion gate
class Statuses(Base):
    def setUp(self):
        super().setUp()
        self.pkg = self.build_package()
        self.i = self.iid(self.pkg)
        self.initialize(self.pkg)

    def test_11_no_automatic_completion(self):
        # initialize from an APPROVED package item must NOT auto-complete it
        st = T.load_state(self.base)
        rec = list(st["records"].values())[0]
        self.assertNotEqual(rec["owner_status"], T.S_COMPLETED)
        self.assertEqual(rec["owner_status"], T.S_PENDING)

    def test_12_valid_manual_completion(self):
        r = self.complete(self.pkg, self.i)
        self.assertEqual(r["readiness"], T.TRACKER_READY)
        self.assertEqual(r["status_phrase"], "Owner recorded this action as manually completed.")
        rec = T.load_state(self.base)["records"][T.tracker_record_id(self.pkg["package_id"], self.i)]
        self.assertEqual(rec["owner_status"], T.S_COMPLETED)
        self.assertTrue(rec["confirmed_manual_seller_central_check"])
        self.assertTrue(rec["confirmed_owner_performed_action"])

    def test_13_completion_missing_check_confirmation_blocks(self):
        r = self.complete(self.pkg, self.i, confirm_check=False)
        self.assertEqual(r["readiness"], T.CONFIRMATION_REQUIRED)
        self.assertIn("MISSING_SELLER_CENTRAL_CHECK_CONFIRMATION", [x["code"] for x in r["reasons"]])
        self.assertEqual(T.exit_code_for(r["readiness"]), 2)

    def test_14_completion_missing_owner_action_confirmation_blocks(self):
        r = self.complete(self.pkg, self.i, confirm_action=False)
        self.assertEqual(r["readiness"], T.CONFIRMATION_REQUIRED)
        self.assertIn("MISSING_OWNER_ACTION_CONFIRMATION", [x["code"] for x in r["reasons"]])

    def test_15_completion_missing_checked_date_blocks(self):
        r = self.complete(self.pkg, self.i,
                          updates={"owner_note": "x", "owner_completed_date": REF})
        self.assertEqual(r["readiness"], T.CONFIRMATION_REQUIRED)
        self.assertIn("MISSING_OWNER_CHECKED_DATE", [x["code"] for x in r["reasons"]])

    def test_16_completion_missing_completed_date_blocks(self):
        r = self.complete(self.pkg, self.i,
                          updates={"owner_note": "x", "owner_checked_date": REF})
        self.assertEqual(r["readiness"], T.CONFIRMATION_REQUIRED)
        self.assertIn("MISSING_OWNER_COMPLETED_DATE", [x["code"] for x in r["reasons"]])

    def test_17_completion_missing_note_or_outcome_blocks(self):
        r = self.complete(self.pkg, self.i,
                          updates={"owner_checked_date": REF, "owner_completed_date": REF})
        self.assertEqual(r["readiness"], T.CONFIRMATION_REQUIRED)
        self.assertIn("MISSING_NOTE_OR_OUTCOME", [x["code"] for x in r["reasons"]])

    def test_17b_completion_after_value_counts_as_outcome(self):
        r = self.complete(self.pkg, self.i,
                          updates={"owner_checked_date": REF, "owner_completed_date": REF,
                                   "after_value": "0.90", "after_value_currency": "USD",
                                   "owner_note": ""})
        self.assertEqual(r["readiness"], T.TRACKER_READY)

    def test_18_manually_skipped(self):
        r = self.update(self.pkg, self.i, T.S_SKIPPED, updates={"owner_note": "not worth it"})
        self.assertEqual(r["readiness"], T.TRACKER_READY)
        self.assertEqual(self._status(), T.S_SKIPPED)

    def test_19_no_longer_relevant(self):
        r = self.update(self.pkg, self.i, T.S_NOT_RELEVANT)
        self.assertEqual(r["readiness"], T.TRACKER_READY)
        self.assertEqual(self._status(), T.S_NOT_RELEVANT)

    def test_20_needs_review(self):
        self.update(self.pkg, self.i, T.S_NEEDS_REVIEW)
        self.assertEqual(self._status(), T.S_NEEDS_REVIEW)

    def test_21_blocked_by_current_state(self):
        self.update(self.pkg, self.i, T.S_BLOCKED_STATE)
        self.assertEqual(self._status(), T.S_BLOCKED_STATE)

    def test_22_unable_to_verify(self):
        self.update(self.pkg, self.i, T.S_UNABLE)
        self.assertEqual(self._status(), T.S_UNABLE)

    def test_23_reverted_manually(self):
        self.update(self.pkg, self.i, T.S_REVERTED, updates={"owner_note": "put bid back"})
        self.assertEqual(self._status(), T.S_REVERTED)

    def test_24_invalid_status(self):
        with self.assertRaises(T.TrackerError) as cm:
            T.build_next_record(self._rec(), status="SEND_TO_AMAZON", updates={},
                                confirm_check=False, confirm_action=False, evidence_sha256=None,
                                binding_state=T.B_CURRENT, now=NOW)
        self.assertEqual(cm.exception.code, "INVALID_STATUS")

    def _rec(self):
        return T.load_state(self.base)["records"][T.tracker_record_id(self.pkg["package_id"], self.i)]

    def _status(self):
        return self._rec()["owner_status"]


# ================================================================ 25-30 before/after values
class Values(Base):
    def setUp(self):
        super().setUp()
        self.pkg = self.build_package()
        self.i = self.iid(self.pkg)
        self.initialize(self.pkg)

    def _rec(self):
        return T.load_state(self.base)["records"][T.tracker_record_id(self.pkg["package_id"], self.i)]

    def test_25_before_value_preserved(self):
        self.update(self.pkg, self.i, T.S_NEEDS_REVIEW,
                    updates={"before_value": "1.20", "before_value_currency": "USD"})
        self.assertEqual(self._rec()["before_value"], "1.20")
        self.assertEqual(self._rec()["before_value_currency"], "USD")

    def test_26_after_value_preserved(self):
        self.update(self.pkg, self.i, T.S_NEEDS_REVIEW,
                    updates={"after_value": "BROAD", "before_value": "EXACT"})
        self.assertEqual(self._rec()["after_value"], "BROAD")
        self.assertEqual(self._rec()["before_value"], "EXACT")

    def test_27_currency_required_for_monetary(self):
        r = self.update(self.pkg, self.i, T.S_NEEDS_REVIEW, updates={"before_value": "2.50"})
        self.assertEqual(r["readiness"], T.UPDATE_REJECTED)
        self.assertEqual(r["reasons"][0]["code"], "CURRENCY_REQUIRED")

    def test_28_missing_values_remain_missing(self):
        self.update(self.pkg, self.i, T.S_NEEDS_REVIEW, updates={"owner_note": "just a note"})
        rec = self._rec()
        self.assertIsNone(rec["before_value"])
        self.assertIsNone(rec["after_value"])
        self.assertIsNone(rec["before_value_currency"])

    def test_29_decimal_exact_strings(self):
        self.update(self.pkg, self.i, T.S_NEEDS_REVIEW,
                    updates={"before_value": "8.00", "before_value_currency": "USD"})
        self.assertEqual(self._rec()["before_value"], "8.00")  # not normalized to "8" or "8.0"

    def test_30_no_authoritative_float(self):
        self.update(self.pkg, self.i, T.S_NEEDS_REVIEW,
                    updates={"before_value": "0.10", "before_value_currency": "USD"})
        raw = open(os.path.join(self.base, "action_state", "current.json"), encoding="utf-8").read()
        self.assertIn('"0.10"', raw)          # stored as a quoted string, never a JSON float
        self.assertNotIn("0.1000000000", raw)  # no float artifact

    def test_30b_non_finite_value_rejected(self):
        r = self.update(self.pkg, self.i, T.S_NEEDS_REVIEW,
                        updates={"before_value": "1.0", "before_value_currency": "USD",
                                 "after_value": "NaN", "after_value_currency": "USD"})
        # "NaN" is not numeric-shaped -> caught by the finite guard
        self.assertEqual(r["readiness"], T.UPDATE_REJECTED)
        self.assertEqual(r["reasons"][0]["code"], "NON_FINITE_VALUE")


# ================================================================ 31-39 append-only history
class History(Base):
    def setUp(self):
        super().setUp()
        self.pkg = self.build_package()
        self.i = self.iid(self.pkg)
        self.initialize(self.pkg)
        self.rid = T.tracker_record_id(self.pkg["package_id"], self.i)
        self.hpath = T.state_paths(self.base)[1]
        self.spath = T.state_paths(self.base)[0]

    def _lines(self):
        return [ln for ln in open(self.hpath, encoding="utf-8").read().splitlines() if ln.strip()]

    def _rewrite(self, lines):
        write_bytes(self.hpath, ("".join(l + "\n" for l in lines)).encode("utf-8"))

    def test_31_append_only_history(self):
        self.update(self.pkg, self.i, T.S_NEEDS_REVIEW)
        self.update(self.pkg, self.i, T.S_SKIPPED)
        self.assertEqual(len(self._lines()), 3)  # init + 2 updates, nothing overwritten

    def test_32_revision_increment(self):
        self.update(self.pkg, self.i, T.S_NEEDS_REVIEW)
        self.update(self.pkg, self.i, T.S_SKIPPED)
        revs = [json.loads(l)["local_revision"] for l in self._lines()]
        self.assertEqual(revs, [1, 2, 3])

    def test_33_previous_record_hash(self):
        self.update(self.pkg, self.i, T.S_NEEDS_REVIEW)
        evs = [json.loads(l) for l in self._lines()]
        self.assertIsNone(evs[0]["previous_record_hash"])
        self.assertEqual(evs[1]["previous_record_hash"], evs[0]["record_content_sha256"])

    def test_34_broken_history_chain_blocks(self):
        self.update(self.pkg, self.i, T.S_NEEDS_REVIEW)
        lines = self._lines()
        ev = json.loads(lines[1])
        ev["previous_record_hash"] = "0" * 64
        lines[1] = json.dumps(ev, sort_keys=True)
        self._rewrite(lines)
        with self.assertRaises(T.TrackerError) as cm:
            T.load_state(self.base)
        self.assertEqual(cm.exception.code, "HISTORY_CHAIN_BROKEN")

    def test_35_missing_history_entry_blocks(self):
        self.update(self.pkg, self.i, T.S_NEEDS_REVIEW)
        self.update(self.pkg, self.i, T.S_SKIPPED)
        lines = self._lines()
        del lines[1]  # remove revision 2, leaving 1 then 3
        self._rewrite(lines)
        with self.assertRaises(T.TrackerError) as cm:
            T.load_state(self.base)
        self.assertIn(cm.exception.code, ("HISTORY_SEQ_GAP", "HISTORY_MISSING_ENTRY"))

    def test_36_duplicate_revision_blocks(self):
        self.update(self.pkg, self.i, T.S_NEEDS_REVIEW)
        lines = self._lines()
        lines.append(lines[1])  # duplicate the last event line
        self._rewrite(lines)
        with self.assertRaises(T.TrackerError) as cm:
            T.load_state(self.base)
        self.assertIn(cm.exception.code, ("HISTORY_SEQ_GAP", "HISTORY_DUP_REVISION"))

    def test_37_revision_rollback_blocks(self):
        self.update(self.pkg, self.i, T.S_NEEDS_REVIEW)
        # current says revision 1 while history has 2 -> rollback/mismatch
        doc = read_json(self.spath)
        rec = doc["records"][self.rid]
        rec["local_revision"] = 1
        rec["record_content_sha256"] = T._record_content_hash(rec)
        doc["state_content_sha256"] = T._state_content_sha256(doc["records"])
        write_bytes(self.spath, (P5.canonical_json(doc) + "\n").encode("utf-8"))
        with self.assertRaises(T.TrackerError):
            T.load_state(self.base)

    def test_38_rewritten_history_detected(self):
        self.update(self.pkg, self.i, T.S_NEEDS_REVIEW)
        lines = self._lines()
        ev = json.loads(lines[1])
        ev["record"]["owner_note"] = "SECRETLY EDITED"   # tamper content, keep the stored hash
        lines[1] = json.dumps(ev, sort_keys=True)
        self._rewrite(lines)
        with self.assertRaises(T.TrackerError) as cm:
            T.load_state(self.base)
        self.assertEqual(cm.exception.code, "HISTORY_REWRITTEN")

    def test_39_current_history_mismatch_blocks(self):
        self.update(self.pkg, self.i, T.S_NEEDS_REVIEW)
        doc = read_json(self.spath)
        doc["records"][self.rid]["owner_status"] = "MANUALLY_COMPLETED"  # diverge from history
        rec = doc["records"][self.rid]
        rec["record_content_sha256"] = T._record_content_hash(rec)
        doc["state_content_sha256"] = T._state_content_sha256(doc["records"])
        write_bytes(self.spath, (P5.canonical_json(doc) + "\n").encode("utf-8"))
        with self.assertRaises(T.TrackerError) as cm:
            T.load_state(self.base)
        self.assertIn(cm.exception.code, ("CURRENT_HISTORY_MISMATCH", "STATE_HASH_MISMATCH"))

    def test_39b_corrupted_current_state_blocks(self):
        write_bytes(self.spath, b"{ not json")
        with self.assertRaises(T.TrackerError) as cm:
            T.load_state(self.base)
        self.assertEqual(cm.exception.code, "STATE_MALFORMED")


# ================================================================ 40-45 package change handling
class PackageChange(Base):
    def setUp(self):
        super().setUp()
        self.pkg = self.build_package()
        self.i = self.iid(self.pkg)
        self.initialize(self.pkg)
        self.rid = T.tracker_record_id(self.pkg["package_id"], self.i)

    def _bind(self):
        rec = T.load_state(self.base)["records"][self.rid]
        return T.binding_state(rec, self.pkg["p75"])[0]

    def test_40_package_changed_detection(self):
        self.set_manifest_field(self.pkg, "package_content_sha256", "a" * 64)
        self.assertEqual(self._bind(), T.B_PACKAGE_CHANGED)

    def test_41_package_item_changed_detection(self):
        self.mutate_checklist(self.pkg, lambda d: d["items"][0].__setitem__("content_sha256", "b" * 64))
        self.assertEqual(self._bind(), T.B_PACKAGE_ITEM_CHANGED)

    def test_42_package_item_absent_detection(self):
        self.mutate_checklist(self.pkg, lambda d: d["items"].clear())
        self.assertEqual(self._bind(), T.B_PACKAGE_ITEM_ABSENT)

    def test_42b_manifest_mismatch_detection(self):
        self.bump_manifest_runtime(self.pkg)
        self.assertEqual(self._bind(), T.B_PACKAGE_MANIFEST_MISMATCH)

    def test_42c_integrity_blocked_detection(self):
        self.tamper_artifact(self.pkg)
        self.assertEqual(self._bind(), T.B_PACKAGE_INTEGRITY_BLOCKED)

    def test_42d_package_absent_detection(self):
        shutil.rmtree(self.pkg["package_dir"])
        self.assertEqual(self._bind(), T.B_PACKAGE_ITEM_ABSENT)

    def test_43_old_completed_record_preserved(self):
        self.complete(self.pkg, self.i)
        self.set_manifest_field(self.pkg, "package_content_sha256", "c" * 64)  # package now changed
        st = T.load_state(self.base)                                            # record survives
        self.assertIn(self.rid, st["records"])
        self.assertEqual(st["records"][self.rid]["owner_status"], T.S_COMPLETED)
        # history still holds init + completion
        self.assertEqual(len([e for e in st["events"] if e["tracker_record_id"] == self.rid]), 2)

    def test_44_stale_record_visibly_marked(self):
        self.complete(self.pkg, self.i)
        self.set_manifest_field(self.pkg, "package_content_sha256", "d" * 64)
        r = T.run_list(self.base, self.pkg["p75"], REF)
        row = r["rows"][0]
        self.assertEqual(row["binding_state"], T.B_PACKAGE_CHANGED)
        self.assertEqual(r["stale_count"], 1)
        v = T.run_validate(self.base, self.pkg["p75"], REF)
        self.assertEqual(v["readiness"], T.PACKAGE_CHANGED_REVIEW_REQUIRED)
        self.assertEqual(T.exit_code_for(v["readiness"]), 2)

    def test_44b_cannot_complete_against_stale_package(self):
        self.set_manifest_field(self.pkg, "package_content_sha256", "e" * 64)
        r = self.complete(self.pkg, self.i)
        self.assertEqual(r["readiness"], T.PACKAGE_CHANGED_REVIEW_REQUIRED)
        self.assertIn("PACKAGE_BINDING_STALE", [x["code"] for x in r["reasons"]])

    def test_45_no_last_write_wins(self):
        s0 = T.load_state(self.base)["state"]["state_content_sha256"]
        self.update(self.pkg, self.i, T.S_NEEDS_REVIEW)                # advances state
        r = self.update(self.pkg, self.i, T.S_SKIPPED, expected_state_sha256=s0)  # stale expectation
        self.assertEqual(r["readiness"], T.CONCURRENT_MODIFICATION)
        self.assertEqual(r["reasons"][0]["code"], "EXPECTED_STATE_SHA_MISMATCH")


# ================================================================ 46-50 atomic + concurrency
class AtomicAndConcurrency(Base):
    def setUp(self):
        super().setUp()
        self.pkg = self.build_package()
        self.i = self.iid(self.pkg)
        self.initialize(self.pkg)
        self.spath, self.hpath = T.state_paths(self.base)

    def test_46_atomic_update_success(self):
        r = self.update(self.pkg, self.i, T.S_NEEDS_REVIEW)
        self.assertEqual(r["readiness"], T.TRACKER_READY)
        T.load_state(self.base)  # loads clean -> consistent

    def test_47_48_atomic_failure_preserves_files(self):
        before_state = open(self.spath, "rb").read()
        before_hist = open(self.hpath, "rb").read()
        with mock.patch.object(T, "_write_bytes", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.update(self.pkg, self.i, T.S_SKIPPED)
        self.assertEqual(open(self.spath, "rb").read(), before_state)   # current preserved
        self.assertEqual(open(self.hpath, "rb").read(), before_hist)    # history preserved
        # no temp files linger
        self.assertFalse(os.path.exists(self.spath + ".tmp"))
        self.assertFalse(os.path.exists(self.hpath + ".tmp"))

    def test_49_concurrent_update_detected(self):
        loaded = T.load_state(self.base)
        s0 = loaded["state"]["state_content_sha256"]
        # another process writes in between
        self.update(self.pkg, self.i, T.S_NEEDS_REVIEW)
        r = self.update(self.pkg, self.i, T.S_SKIPPED, expected_state_sha256=s0)
        self.assertEqual(r["readiness"], T.CONCURRENT_MODIFICATION)

    def test_50_expected_record_hash_mismatch_detected(self):
        r = self.update(self.pkg, self.i, T.S_NEEDS_REVIEW, expected_record_hash="0" * 64)
        self.assertEqual(r["readiness"], T.CONCURRENT_MODIFICATION)
        self.assertEqual(r["reasons"][0]["code"], "EXPECTED_RECORD_HASH_MISMATCH")


# ================================================================ 51-53 determinism
class Determinism(Base):
    def test_51_deterministic_current_state(self):
        pkg = self.build_package()
        b1 = os.path.join(self.tmp, "a")
        b2 = os.path.join(self.tmp, "b")
        r1 = T.run_initialize(b1, pkg["p75"], REF, package_id=pkg["package_id"], now=NOW)
        r2 = T.run_initialize(b2, pkg["p75"], REF, package_id=pkg["package_id"], now=NOW2)
        # different wall clocks, identical authoritative state hash
        self.assertEqual(r1["state_content_sha256"], r2["state_content_sha256"])

    def test_52_deterministic_history_record(self):
        pkg = self.build_package()
        i = self.iid(pkg)
        b1, b2 = os.path.join(self.tmp, "a"), os.path.join(self.tmp, "b")
        for b, clk in ((b1, NOW), (b2, NOW2)):
            T.run_initialize(b, pkg["p75"], REF, package_id=pkg["package_id"], now=clk)
            T.run_update(b, pkg["p75"], REF, package_id=pkg["package_id"], package_item_id=i,
                         status=T.S_NEEDS_REVIEW, updates={"owner_note": "n"}, now=clk)
        rid = T.tracker_record_id(pkg["package_id"], i)
        h1 = T.load_state(b1)["records"][rid]["record_content_sha256"]
        h2 = T.load_state(b2)["records"][rid]["record_content_sha256"]
        self.assertEqual(h1, h2)

    def test_53_deterministic_exports(self):
        pkg = self.build_package()
        i = self.iid(pkg)
        b1, b2 = os.path.join(self.tmp, "a"), os.path.join(self.tmp, "b")
        for b, clk in ((b1, NOW), (b2, NOW2)):
            T.run_initialize(b, pkg["p75"], REF, package_id=pkg["package_id"], now=clk)
            T.run_update(b, pkg["p75"], REF, package_id=pkg["package_id"], package_item_id=i,
                         status=T.S_SKIPPED, updates={"owner_note": "x"}, now=clk)
            T.run_export(b, pkg["p75"], REF)
        for name in ("manual_action_status.tsv", "manual_action_status.json"):
            a = open(os.path.join(b1, "exports", name), "rb").read()
            c = open(os.path.join(b2, "exports", name), "rb").read()
            self.assertEqual(a, c, name)  # exports carry no runtime timestamp


# ================================================================ 54-68 exports + TSV safety
class Exports(Base):
    def setUp(self):
        super().setUp()
        self.pkg = self.build_package()
        self.i = self.iid(self.pkg)
        self.initialize(self.pkg)

    def _export(self):
        r = T.run_export(self.base, self.pkg["p75"], REF)
        d = os.path.join(self.base, "exports")
        return r, d

    def test_54_tsv_generated(self):
        _r, d = self._export()
        self.assertTrue(os.path.isfile(os.path.join(d, "manual_action_status.tsv")))

    def test_55_json_export_generated(self):
        _r, d = self._export()
        doc = read_json(os.path.join(d, "manual_action_status.json"))
        self.assertEqual(doc["meta"]["schema_version"], T.STATUS_EXPORT_SCHEMA)
        self.assertFalse(doc["meta"]["amazon_action_performed"])

    def test_56_markdown_history_generated(self):
        _r, d = self._export()
        self.assertTrue(os.path.isfile(os.path.join(d, "manual_action_history.md")))

    def test_57_export_disclaimer(self):
        _r, d = self._export()
        for name in ("manual_action_status.tsv", "manual_action_history.md"):
            txt = open(os.path.join(d, name), encoding="utf-8").read()
            self.assertIn("LOCAL OWNER-ENTERED TRACKER", txt)
            self.assertIn("No Amazon connection was made", txt)
        doc = read_json(os.path.join(d, "manual_action_status.json"))
        self.assertIn("LOCAL OWNER-ENTERED TRACKER.", doc["meta"]["manual_action_disclaimer"])

    def test_58_export_not_amazon_upload_format(self):
        _r, d = self._export()
        blob = ""
        for name in os.listdir(d):
            with open(os.path.join(d, name), encoding="utf-8") as f:
                blob += f.read().lower()
        # markers that would appear only in a genuine Amazon Sponsored Products bulk upload sheet
        for banned in ("sponsored products campaigns", "campaign id\tad group id", "keyword bid",
                       "bid\tmatch type", "entity\toperation"):
            self.assertNotIn(banned, blob)
        # a JSON export that explicitly records no Amazon action was performed
        doc = read_json(os.path.join(d, "manual_action_status.json"))
        self.assertFalse(doc["meta"]["amazon_action_performed"])

    def _cell(self, note):
        self.update(self.pkg, self.i, T.S_NEEDS_REVIEW, updates={"owner_note": note})
        _r, d = self._export()
        lines = open(os.path.join(d, "manual_action_status.tsv"), encoding="utf-8").read().splitlines()
        header = None
        for ln in lines:
            if ln.startswith("#"):
                continue
            header = ln.split("\t")
            break
        col = header.index("owner_note")
        for ln in lines:
            if ln.startswith("#") or "\t" not in ln or ln.split("\t")[0] == "package_id":
                continue
            return ln.split("\t")[col]
        return None

    def test_59_formula_injection_equals(self):
        self.assertTrue(self._cell("=cmd()").startswith("'"))

    def test_60_formula_injection_plus(self):
        self.assertTrue(self._cell("+cmd").startswith("'"))

    def test_61_formula_injection_minus(self):
        self.assertTrue(self._cell("-cmd").startswith("'"))

    def test_62_formula_injection_at(self):
        self.assertTrue(self._cell("@cmd").startswith("'"))

    def test_63_formula_injection_tab(self):
        self.assertNotIn("\t", self._cell("a\tb"))

    def test_64_formula_injection_cr(self):
        self.assertNotIn("\r", self._cell("a\rb"))

    def test_65_formula_injection_newline(self):
        self.assertNotIn("\n", self._cell("a\nb"))

    def test_66_negative_decimal_preserved(self):
        self.update(self.pkg, self.i, T.S_REVERTED,
                    updates={"before_value": "-2.50", "before_value_currency": "USD",
                             "owner_note": "n"})
        _r, d = self._export()
        lines = open(os.path.join(d, "manual_action_status.tsv"), encoding="utf-8").read().splitlines()
        header = next(ln.split("\t") for ln in lines if not ln.startswith("#"))
        col = header.index("before_value")
        data = [ln for ln in lines if "\t" in ln and ln.split("\t")[0] == self.pkg["package_id"]][0]
        self.assertEqual(data.split("\t")[col], "-2.50")   # not quoted, not mangled

    def test_67_unicode_vietnamese_note_preserved(self):
        note = "Đã đổi giá thủ công sau khi kiểm tra Seller Central."
        self.update(self.pkg, self.i, T.S_SKIPPED, updates={"owner_note": note})
        _r, d = self._export()
        tsv = open(os.path.join(d, "manual_action_status.tsv"), encoding="utf-8").read()
        self.assertIn(note, tsv)
        doc = read_json(os.path.join(d, "manual_action_status.json"))
        self.assertTrue(any(r.get("owner_note") == note for r in doc["records"]))

    def test_68_equal_tsv_columns(self):
        self.update(self.pkg, self.i, T.S_NEEDS_REVIEW, updates={"owner_note": "x\ty"})
        _r, d = self._export()
        lines = open(os.path.join(d, "manual_action_status.tsv"), encoding="utf-8").read().splitlines()
        ncol = len(T.STATUS_COLUMNS)
        for ln in lines:
            if "\t" in ln:
                self.assertEqual(len(ln.split("\t")), ncol)

    def test_99_empty_export_headers(self):
        pkg = self.build_package([(1, AA.RL_NEGATIVE, "DEFERRED")], sub="empty")
        b = os.path.join(self.tmp, "empty76")
        T.run_initialize(b, pkg["p75"], REF, package_id=pkg["package_id"], now=NOW)
        T.run_export(b, pkg["p75"], REF)
        lines = open(os.path.join(b, "exports", "manual_action_status.tsv"), encoding="utf-8").read().splitlines()
        header = next(ln for ln in lines if not ln.startswith("#"))
        self.assertEqual(header.split("\t"), list(T.STATUS_COLUMNS))
        self.assertIn("LOCAL OWNER-ENTERED TRACKER", "\n".join(lines))


# ================================================================ 69-74 immutability + validate
class ImmutabilityAndValidate(Base):
    def test_69_package_source_immutable(self):
        pkg = self.build_package()
        before = tree_hashes(pkg["package_dir"])
        self.initialize(pkg)
        i = self.iid(pkg)
        self.complete(pkg, i)
        T.run_list(self.base, pkg["p75"], REF)
        T.run_validate(self.base, pkg["p75"], REF)
        T.run_export(self.base, pkg["p75"], REF)
        self.assertEqual(tree_hashes(pkg["package_dir"]), before)

    def test_70_validate_command(self):
        pkg = self.build_package()
        self.initialize(pkg)
        r = T.run_validate(self.base, pkg["p75"], REF)
        self.assertEqual(r["readiness"], T.VALIDATION_READY)

    def test_71_empty_tracker_validation(self):
        pkg = self.build_package([(1, AA.RL_NEGATIVE, "DEFERRED")])
        self.initialize(pkg)
        r = T.run_validate(self.base, pkg["p75"], REF)
        self.assertEqual(r["readiness"], T.VALIDATION_READY)
        self.assertEqual(T.exit_code_for(r["readiness"]), 0)

    def test_72_populated_tracker_validation(self):
        pkg = self.build_package([(1, AA.RL_NEGATIVE, APPROVED), (2, AA.RL_EXACT, APPROVED)])
        self.initialize(pkg)
        r = T.run_validate(self.base, pkg["p75"], REF)
        self.assertEqual(r["readiness"], T.VALIDATION_READY)
        self.assertEqual(r["record_count"], 2)

    def test_73_blocked_validation_exit_code(self):
        pkg = self.build_package()
        self.initialize(pkg)
        spath = T.state_paths(self.base)[0]
        write_bytes(spath, b"{ corrupt")
        r = T.run_validate(self.base, pkg["p75"], REF)
        self.assertEqual(r["readiness"], T.STATE_BLOCKED)
        self.assertEqual(T.exit_code_for(r["readiness"]), 2)

    def test_74_valid_validation_exit_code(self):
        pkg = self.build_package()
        self.initialize(pkg)
        r = T.run_validate(self.base, pkg["p75"], REF)
        self.assertEqual(T.exit_code_for(r["readiness"]), 0)

    def test_74b_state_required_before_init(self):
        pkg = self.build_package()
        r = T.run_validate(self.base, pkg["p75"], REF)
        self.assertEqual(r["readiness"], T.STATE_REQUIRED)
        self.assertEqual(T.exit_code_for(r["readiness"]), 2)


# ================================================================ 75-76 real T2 empty package
class RealT2(unittest.TestCase):
    P75 = os.path.join(ROOT, "runs", "T2", "phase7", "7.5")

    def setUp(self):
        if not os.path.isdir(os.path.join(self.P75, "packages")):
            self.skipTest("real T2 Phase 7.5 workspace not present")
        self.tmp = tempfile.mkdtemp(prefix="p76-t2-")
        self.base = os.path.join(self.tmp, "7.6")

    def tearDown(self):
        shutil.rmtree(getattr(self, "tmp", ""), ignore_errors=True)

    def _pkg_id(self):
        pkgs = os.path.join(self.P75, "packages")
        for name in sorted(os.listdir(pkgs)):
            m = os.path.join(pkgs, name, "package_manifest.json")
            if os.path.isfile(m):
                return read_json(m)["package_id"], read_json(m)["readiness"]
        return None, None

    def test_75_real_t2_empty_package(self):
        pid, readiness = self._pkg_id()
        self.assertIsNotNone(pid)
        self.assertEqual(readiness, "SESSION7_5_PACKAGE_READY_EMPTY")
        before = tree_hashes(self.P75)
        r = T.run_initialize(self.base, self.P75, REF, package_id=pid, now=NOW)
        self.assertEqual(r["readiness"], T.TRACKER_READY_EMPTY)
        self.assertEqual(r["record_count"], 0)
        self.assertEqual(T.exit_code_for(r["readiness"]), 0)
        v = T.run_validate(self.base, self.P75, REF)
        self.assertEqual(T.exit_code_for(v["readiness"]), 0)
        e = T.run_export(self.base, self.P75, REF)
        self.assertEqual(T.exit_code_for(e["readiness"]), 0)
        self.assertEqual(tree_hashes(self.P75), before)  # real package untouched

    def test_76_no_fake_records_from_empty_package(self):
        pid, _ = self._pkg_id()
        T.run_initialize(self.base, self.P75, REF, package_id=pid, now=NOW)
        st = T.load_state(self.base)
        self.assertEqual(st["records"], {})
        self.assertEqual(st["events"], [])


# ================================================================ 77-88 security / boundary
class SecurityBoundary(Base):
    def test_77_amazon_counters_zero(self):
        pkg = self.build_package()
        r = self.initialize(pkg)
        for k, v in r["amazon_counters"].items():
            self.assertEqual(v, 0, k)
        for k, v in T.AMAZON_COUNTERS.items():
            self.assertEqual(v, 0, k)

    def test_78_external_network_counter_zero(self):
        self.assertEqual(T.AMAZON_COUNTERS["external_network_calls"], 0)
        self.assertEqual(T.AMAZON_COUNTERS["amazon_connections"], 0)

    # These scans inspect actual imports/calls via AST — never prose. The module legitimately
    # DOCUMENTS what it refuses to do (e.g. "no SP-API"); documentation must not trip a scan.
    def test_79_no_amazon_imports(self):
        mods = imported_modules(_module_ast())
        for banned in ("boto3", "botocore", "amazonpaapi", "sp_api", "python_amazon_mws",
                       "advertising_api", "selling_partner_api"):
            self.assertNotIn(banned, mods)

    def test_80_no_network_imports(self):
        mods = imported_modules(_module_ast())
        for banned in ("requests", "httpx", "aiohttp", "urllib", "socket", "http", "ftplib",
                       "smtplib", "websocket", "websockets"):
            self.assertNotIn(banned, mods)

    def test_81_no_browser_automation(self):
        mods = imported_modules(_module_ast())
        for banned in ("selenium", "playwright", "pyppeteer", "webdriver", "splinter"):
            self.assertNotIn(banned, mods)

    def test_82_no_credentials(self):
        with open(MODULE_PATH, encoding="utf-8") as f:
            src = f.read().lower()
        for banned in ("password", "api_key", "apikey", "secret_key", "access_token", "client_secret"):
            self.assertNotIn(banned, src)

    def test_83_84_85_no_token_cookie_session_storage(self):
        # the module names these only inside the permanent-boundary zero counters, never stores them
        self.assertEqual(T.AMAZON_COUNTERS["token_store_count"], 0)
        self.assertEqual(T.AMAZON_COUNTERS["cookie_store_count"], 0)
        self.assertEqual(T.AMAZON_COUNTERS["session_store_count"], 0)
        with open(MODULE_PATH, encoding="utf-8") as f:
            src = f.read().lower()
        for banned in ("set-cookie", "authorization:", "bearer "):
            self.assertNotIn(banned, src)

    def test_86_no_eval(self):
        self.assertNotIn("eval", called_names(_module_ast()))

    def test_87_no_exec(self):
        self.assertNotIn("exec", called_names(_module_ast()))

    def test_88_no_subprocess_or_dangerous(self):
        mods = imported_modules(_module_ast())
        for banned in ("subprocess", "pickle", "marshal", "ctypes", "multiprocessing"):
            self.assertNotIn(banned, mods)
        calls = called_names(_module_ast())
        for banned in ("os.system", "os.popen", "__import__", "compile", "os.exec", "os.spawn"):
            self.assertNotIn(banned, calls)


# ================================================================ 89, 100 compile + wording
class MetaChecks(Base):
    def test_89_compileall(self):
        self.assertTrue(py_compile.compile(MODULE_PATH, doraise=True))
        self.assertTrue(py_compile.compile(os.path.abspath(__file__), doraise=True))

    def test_100_owner_entered_wording_only(self):
        pkg = self.build_package()
        self.initialize(pkg)
        i = self.iid(pkg)
        r = self.complete(pkg, i)
        self.assertEqual(r["status_phrase"], "Owner recorded this action as manually completed.")
        # the GENERATED text (phrasing + disclaimer) must never claim Amazon confirmed / auto-applied.
        banned = ("amazon confirmed completion", "action was automatically applied",
                  "the system changed seller central", "amazon action succeeded",
                  "amazon confirmed", "automatically applied")
        generated = " ".join(list(T.STATUS_PHRASING.values()) + list(T.DISCLAIMER_LINES)).lower()
        for b in banned:
            self.assertNotIn(b, generated)
        # exports carry only owner-entered phrasing
        T.run_export(self.base, pkg["p75"], REF)
        with open(os.path.join(self.base, "exports", "manual_action_history.md"), encoding="utf-8") as f:
            md = f.read().lower()
        self.assertIn("owner recorded", md)
        for b in banned:
            self.assertNotIn(b, md)

    def test_evidence_reference_and_sha(self):
        pkg = self.build_package()
        self.initialize(pkg)
        i = self.iid(pkg)
        evpath = os.path.join(self.tmp, "screenshot.txt")
        write_bytes(evpath, b"local evidence bytes")
        r = self.update(pkg, i, T.S_NEEDS_REVIEW, updates={"owner_note": "see shot"},
                        evidence_file=evpath)
        self.assertEqual(r["readiness"], T.TRACKER_READY)
        rec = T.load_state(self.base)["records"][T.tracker_record_id(pkg["package_id"], i)]
        self.assertEqual(rec["evidence_sha256"], hashlib.sha256(b"local evidence bytes").hexdigest())
        self.assertEqual(rec["evidence_reference"], "screenshot.txt")

    def test_no_amazon_lineage_arbitrary_record_blocked(self):
        # an owner-invented action id with no Phase 7.5 package lineage must be refused
        pkg = self.build_package()
        r = T.run_update(self.base, pkg["p75"], REF, package_id="pkg-invented",
                         package_item_id="item:invented", status=T.S_COMPLETED, now=NOW)
        self.assertIn(r["readiness"], (T.PACKAGE_BLOCKED,))
        self.assertEqual(r["reasons"][0]["code"], "NO_PACKAGE_LINEAGE")


if __name__ == "__main__":
    unittest.main()
