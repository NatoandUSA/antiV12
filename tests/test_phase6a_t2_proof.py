#!/usr/bin/env python3
"""
Phase 6A — T2 real-workspace proof (requirements 65-75).

Reuses the REAL, immutable T2 inputs (runs/T2) READ-ONLY and writes Phase 6A artifacts to a temp
location. Proves: keyword source stays MASTER-KEYWORDS-LEAN.json (TIER_A=74, TIER_B=378); the
normalized-fact, claim-evidence, owner-input and readiness outputs are twice-deterministic; the
certified claim-evidence hash is reproduced exactly; wrong-audience/product/occasion/brand leakage is
zero; and nothing is written to the T2 workspace or to any Amazon/Seller-Central surface.

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

from production import product_workspace as PW   # noqa: E402

T2 = os.path.join(ROOT, "runs", "T2")
T2_PRESENT = os.path.exists(os.path.join(T2, "MASTER-KEYWORDS-LEAN.json"))
CERTIFIED_CLAIM_SHA = "5e3a69d6644298f7c8c33cd1c2841e12d80c9183d2c31a9213eb3021a575069a"
CERTIFIED_KEYWORD_SHA = "52b02f162e75f70806a8e2893a5327210037135cb8b4b3834fc192b318f8e8e7"


def _run_into_temp(result):
    root = tempfile.mkdtemp(prefix="6a-t2-")
    dest = os.path.join(root, "phase6", "6A")
    man = PW.write_phase6a_artifacts(result, dest_dir=dest, workspace_dir=root,
                                     started_at="T0", completed_at="T1")
    return root, dest, man


@unittest.skipUnless(T2_PRESENT, "runs/T2 (paid, gitignored) not present on this machine")
class T2Proof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r1 = PW.build_product_workspace(T2)
        cls.r2 = PW.build_product_workspace(T2)

    def test_product_identity_stable(self):
        self.assertEqual(self.r1.product_id, "T2")
        self.assertEqual(self.r1.product_type, "sweatshirt")
        self.assertEqual(self.r1.audience, "nurses")
        self.assertEqual(self.r1._policy.category_identifier, "US:apparel")
        self.assertFalse(self.r1._policy.fallback_used)

    def test_65_keyword_source_is_lean(self):
        self.assertEqual(self.r1._src.source_file, "MASTER-KEYWORDS-LEAN.json")
        self.assertEqual(self.r1.keyword_source_sha256, CERTIFIED_KEYWORD_SHA)

    def test_66_tier_a_74(self):
        self.assertEqual(self.r1._src.tier_counts["TIER_A"], 74)

    def test_67_tier_b_378(self):
        self.assertEqual(self.r1._src.tier_counts["TIER_B"], 378)

    def test_68_product_fact_hashes_match_across_two_runs(self):
        self.assertEqual(self.r1.product_facts_sha256, self.r2.product_facts_sha256)

    def test_69_claim_evidence_hashes_match_across_two_runs(self):
        self.assertEqual(self.r1.claim_evidence_sha256, self.r2.claim_evidence_sha256)

    def test_claim_evidence_hash_reproduces_certified(self):
        self.assertEqual(self.r1.claim_evidence_sha256, CERTIFIED_CLAIM_SHA)

    def test_70_owner_input_outputs_match(self):
        a = PW._owner_input_doc(self.r1)["content_sha256"]
        b = PW._owner_input_doc(self.r2)["content_sha256"]
        self.assertEqual(a, b)

    def test_71_readiness_reports_match_canonical(self):
        self.assertEqual(PW._readiness_report_md(self.r1), PW._readiness_report_md(self.r2))

    def test_72_wrong_audience_leakage_zero(self):
        self.assertEqual(self._leak("WRONG_AUDIENCE"), 0)

    def test_73_wrong_product_leakage_zero(self):
        self.assertEqual(self._leak("WRONG_PRODUCT"), 0)

    def test_74_wrong_occasion_leakage_zero(self):
        self.assertEqual(self._leak("WRONG_OCCASION"), 0)

    def test_75_brand_ip_leakage_zero(self):
        self.assertEqual(len(PW._eligible_ip_contamination(self.r1._src)), 0)

    def _leak(self, token):
        n = 0
        for rec in self.r1._src.eligible_keywords():
            flags = {str(f).upper() for f in rec.get("risk_flags", [])} | \
                    {str(f).upper() for f in rec.get("exclusion_reasons", [])}
            if any(token in f for f in flags):
                n += 1
        return n

    def test_state_and_publishability(self):
        self.assertEqual(self.r1.workspace_state, PW.OWNER_FACTS_AND_REAL_ASSETS_REQUIRED)
        self.assertTrue(self.r1.downstream_readiness["ready_for_6b"])
        self.assertEqual(self.r1.current_listing_publishability_state, PW.LISTING_SAFE_DRAFT)

    def test_owner_facts_actionable_and_proof_explicit(self):
        reqs = self.r1.all_owner_requirements
        self.assertTrue(reqs)
        for q in reqs:
            self.assertTrue(q["question"] and q["reason"] and q["accepted_evidence"]
                            and q["downstream_impact"])
        assets = [a for a in self.r1.real_asset_requirements if a["state"] == PW.ASSET_REQUIRED]
        self.assertTrue(assets)
        for a in assets:
            self.assertTrue(a["ai_generated_prohibited_as_proof"])

    def test_unsafe_claims_remain_blocked(self):
        c = self.r1.claim_evidence
        self.assertEqual(c.prohibited_count, 0)          # nothing prohibited...
        self.assertGreater(c.blocked_count, 0)           # ...but physical claims stay blocked
        for rec in c.by_state(PW.CE.UNVERIFIED_BLOCKED):
            self.assertFalse(rec["publishable"])

    def test_two_run_artifacts_byte_identical(self):
        root1, dest1, man1 = _run_into_temp(self.r1)
        root2, dest2, man2 = _run_into_temp(self.r2)
        for name in ("PRODUCT-WORKSPACE.json", "OWNER-INPUT-REQUIRED.json",
                     "NORMALIZED-PRODUCT-FACTS.json", "CLAIM-EVIDENCE.json",
                     "PRODUCT-READINESS-REPORT.md"):
            a = open(os.path.join(dest1, name), "rb").read()
            b = open(os.path.join(dest2, name), "rb").read()
            self.assertEqual(a, b, name)
        self.assertEqual(man1["deterministic_content_sha256"], man2["deterministic_content_sha256"])
        self.assertTrue(PW.verify_phase6a_artifacts(dest1, workspace_dir=root1)["ok"])

    def test_no_write_to_t2_workspace(self):
        # a build+write to a TEMP location must never modify the source T2 workspace contents.
        before = set(os.listdir(T2))
        _run_into_temp(self.r1)                          # writes to a temp dir, never runs/T2
        self.assertEqual(before, set(os.listdir(T2)))

    def test_offline_and_no_amazon_write(self):
        snap = PW.runtime_safety_snapshot()
        self.assertTrue(snap["offline_only"])
        self.assertFalse(snap["external_ai_enabled"])
        self.assertFalse(snap["outbound_network_enabled"])
        self.assertTrue(snap["loopback_only_effective"])


if __name__ == "__main__":
    unittest.main()
