#!/usr/bin/env python3
"""
Phase 6B — T2 real-workspace proof.

Reuses the REAL, immutable T2 inputs (runs/T2 + its completed phase6/6A) READ-ONLY and writes Phase 6B
artifacts to a temp location. Proves: keyword source stays MASTER-KEYWORDS-LEAN.json (TIER_A=74,
TIER_B=378, certified SHA); the 6A dependency verifies; the exact 13-field schema; top-relevance /
allocation / unallocated / coverage / manifest are twice-deterministic; wrong-audience / wrong-product /
wrong-occasion / brand-IP / unsupported-physical-claim leakage into fields is zero; and nothing is
written to the T2 workspace or to any Amazon / Seller-Central surface.

runs/ holds paid Helium 10 exports and is gitignored, so this proof is SKIPPED on a clean checkout
that has no local T2 workspace (no paid data is ever copied into a committed fixture).
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "listing"))
sys.path.insert(0, os.path.join(ROOT, "core"))

import keyword_allocation_planner as KAP           # noqa: E402
import keyword_source_adapter as KSA               # noqa: E402

T2 = os.path.join(ROOT, "runs", "T2")
T2_6A = os.path.join(T2, "phase6", "6A")
T2_PRESENT = (os.path.exists(os.path.join(T2, "MASTER-KEYWORDS-LEAN.json"))
              and os.path.exists(os.path.join(T2_6A, "STAGE-6A-MANIFEST.json")))
CERTIFIED_KEYWORD_SHA = "52b02f162e75f70806a8e2893a5327210037135cb8b4b3834fc192b318f8e8e7"


def _run_into_temp(result, tag="a"):
    root = tempfile.mkdtemp(prefix=f"6b-t2-{tag}-")
    dest = os.path.join(root, "phase6", "6B")
    man = KAP.write_phase6b_artifacts(result, dest_dir=dest, workspace_dir=root,
                                      started_at="T0", completed_at="T1")
    return root, dest, man


@unittest.skipUnless(T2_PRESENT, "runs/T2 (paid, gitignored) not present on this machine")
class T2Proof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r1 = KAP.plan_keyword_allocation(T2)
        cls.r2 = KAP.plan_keyword_allocation(T2)
        cls.dep = KAP.load_phase6a_dependency(T2)

    # -- identity + source ------------------------------------------------------
    def test_product_identity_stable(self):
        self.assertEqual(self.r1.product_id, "T2")
        self.assertEqual(self.r1.product_type, "sweatshirt")
        self.assertEqual(self.r1.audience, "nurses")
        self.assertEqual(self.r1.category, "apparel")

    def test_source_is_lean_and_certified(self):
        src = KSA.load_keyword_source(T2)
        self.assertEqual(src.source_file, "MASTER-KEYWORDS-LEAN.json")
        self.assertEqual(src.source_sha256, CERTIFIED_KEYWORD_SHA)
        self.assertEqual(self.r1.source_keyword_sha256, CERTIFIED_KEYWORD_SHA)

    def test_tier_a_74(self):
        self.assertEqual(KSA.load_keyword_source(T2).tier_counts["TIER_A"], 74)

    def test_tier_b_378(self):
        self.assertEqual(KSA.load_keyword_source(T2).tier_counts["TIER_B"], 378)

    def test_source_eligible_452(self):
        self.assertEqual(self.r1.coverage["source_eligible_count"], 452)

    # -- 6A dependency ----------------------------------------------------------
    def test_dependency_ready_and_verified(self):
        self.assertTrue(self.dep.ready_for_6b)
        self.assertEqual(self.dep.listing_publishability_state, "SAFE_DRAFT_OWNER_FACTS_REQUIRED")
        self.assertTrue(self.dep.concept_is_publishable("recipient"))
        for blocked in ("decoration_method", "material_composition", "personalization_fields",
                        "color_options", "size_range", "occasion"):
            self.assertFalse(self.dep.concept_is_publishable(blocked), blocked)

    def test_state_is_safe_allocation(self):
        self.assertEqual(self.r1.stage_state, KAP.SAFE_ALLOCATION_OWNER_FACTS_REQUIRED)
        self.assertEqual(self.r1.listing_publishability_state, "SAFE_DRAFT_OWNER_FACTS_REQUIRED")
        self.assertTrue(self.r1.ready_for_6c)

    # -- exact 13-field schema --------------------------------------------------
    def test_exact_13_fields(self):
        self.assertEqual(self.r1.allocation["field_order"], list(KAP.FIELD_KEYS))
        self.assertEqual(len(KAP.FIELD_KEYS), 13)
        self.assertEqual(set(self.r1.fields), set(KAP.FIELD_KEYS))
        self.assertNotIn("unallocated_eligible", self.r1.fields)

    def test_title_primary_is_head_identity(self):
        self.assertEqual([a["phrase"] for a in self.r1.fields["title_primary"]], ["nurse sweatshirt"])

    # -- leakage zero -----------------------------------------------------------
    def test_no_unsupported_physical_claim_allocated(self):
        self.assertEqual(self.r1.coverage["leakage"]["unsupported_physical_claim"], 0)

    def test_wrong_audience_product_occasion_brand_zero_in_fields(self):
        # every allocated term is pure market identity (safe), so no excluded-class term is assigned
        for k in KAP.FIELD_KEYS:
            for a in self.r1.fields[k]:
                self.assertEqual(KAP._classify_keyword({"keyword_normalized": a["normalized_phrase"]})[0],
                                 "SAFE", a["phrase"])

    # -- determinism ------------------------------------------------------------
    def test_two_run_artifacts_byte_identical(self):
        root1, dest1, man1 = _run_into_temp(self.r1, "1")
        root2, dest2, man2 = _run_into_temp(self.r2, "2")
        for name in KAP.REQUIRED_ARTIFACTS:
            a = open(os.path.join(dest1, name), "rb").read()
            b = open(os.path.join(dest2, name), "rb").read()
            self.assertEqual(a, b, name)
        self.assertEqual(man1["deterministic_content_sha256"], man2["deterministic_content_sha256"])
        self.assertEqual(self.r1.deterministic_content_sha256(), self.r2.deterministic_content_sha256())
        self.assertTrue(KAP.verify_phase6b_artifacts(dest1, workspace_dir=root1)["ok"])

    # -- isolation --------------------------------------------------------------
    def test_no_write_to_t2_workspace(self):
        before = set(os.listdir(T2))
        before6a = {n: open(os.path.join(T2_6A, n), "rb").read() for n in os.listdir(T2_6A)}
        _run_into_temp(self.r1, "iso")               # writes to a temp dir, never runs/T2
        self.assertEqual(before, set(os.listdir(T2)))
        after6a = {n: open(os.path.join(T2_6A, n), "rb").read() for n in os.listdir(T2_6A)}
        self.assertEqual(before6a, after6a)          # the 6A dependency is never mutated

    def test_no_phase6c_artifacts(self):
        _root, dest, _man = _run_into_temp(self.r1, "c")
        self.assertEqual(set(os.listdir(dest)), set(KAP.REQUIRED_ARTIFACTS) | {KAP.STAGE_MANIFEST_FILENAME})


if __name__ == "__main__":
    unittest.main()
