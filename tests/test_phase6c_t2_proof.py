#!/usr/bin/env python3
"""
Phase 6C — T2 real-workspace proof.

Reuses the REAL, immutable T2 inputs (runs/T2 + its completed phase6/6A + phase6/6B) READ-ONLY and
writes Phase 6C artifacts to a temp location. Proves: TIER_A=74 / TIER_B=378 and the certified source
SHA are unchanged; the 6A + 6B dependency verifies; the unique-count accounting reconciles to the
exact 56 / 24 / 27 / 1 / 32; only the recipient/nurse claim is verified (gift/personalization
deferred); the safe draft is exactly "Nurse Sweatshirt" + "A nurse sweatshirt." with all five bullets
deferred; the backend is byte-safe; every leakage count is zero; the listing stays SAFE_DRAFT; the run
is twice- and three-mode-deterministic; nothing is written to the T2 workspace or any Amazon surface;
and no A+ / image / later-stage artifact is produced.

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

from production import product_detail_page as PDP           # noqa: E402
import keyword_source_adapter as KSA                         # noqa: E402

T2 = os.path.join(ROOT, "runs", "T2")
T2_6B = os.path.join(T2, "phase6", "6B")
T2_PRESENT = (os.path.exists(os.path.join(T2, "MASTER-KEYWORDS-LEAN.json"))
              and os.path.exists(os.path.join(T2_6B, "STAGE-6B-MANIFEST.json")))
CERTIFIED_KEYWORD_SHA = "52b02f162e75f70806a8e2893a5327210037135cb8b4b3834fc192b318f8e8e7"


@unittest.skipUnless(T2_PRESENT, "runs/T2 (paid, gitignored) not present on this machine")
class T2Proof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deps = PDP.load_phase6c_dependencies(T2)
        cls.r1 = PDP.assemble_product_detail_page(T2)
        cls.r2 = PDP.assemble_product_detail_page(T2)
        cls.acc = PDP.reconcile_phase6b_keyword_counts(cls.deps)

    # -- identity + source ------------------------------------------------------
    def test_product_identity(self):
        self.assertEqual(self.r1.product_id, "T2")
        self.assertEqual(self.r1.product_type, "sweatshirt")
        self.assertEqual(self.r1.audience, "nurses")
        self.assertEqual(self.r1.category, "apparel")

    def test_source_certified_and_tiers(self):
        src = KSA.load_keyword_source(T2)
        self.assertEqual(src.source_file, "MASTER-KEYWORDS-LEAN.json")
        self.assertEqual(src.source_sha256, CERTIFIED_KEYWORD_SHA)
        self.assertEqual(src.tier_counts["TIER_A"], 74)
        self.assertEqual(src.tier_counts["TIER_B"], 378)

    def test_dependency_verifies_and_ready(self):
        self.assertTrue(self.deps.ready_for_6c)
        self.assertEqual(self.deps.claim_evidence_sha256,
                         "5e3a69d6644298f7c8c33cd1c2841e12d80c9183d2c31a9213eb3021a575069a")

    # -- keyword accounting (exact T2 numbers, computed from IDs) ----------------
    def test_accounting_exact(self):
        self.assertEqual(self.acc["selected_unique_keyword_count"], 56)
        self.assertEqual(self.acc["allocated_unique_keyword_count"], 24)
        self.assertEqual(self.acc["assignment_record_count"], 27)
        self.assertEqual(self.acc["reused_keyword_count"], 1)
        self.assertEqual(self.acc["unallocated_unique_keyword_count"], 32)
        self.assertTrue(self.acc["reconciles"])

    def test_equation_reconciles(self):
        self.assertEqual(self.acc["allocated_unique_keyword_count"]
                         + self.acc["unallocated_unique_keyword_count"],
                         self.acc["selected_unique_keyword_count"])

    # -- gift / recipient -------------------------------------------------------
    def test_only_recipient_verified_gift_deferred(self):
        gr = self.r1.gift_recipient_reconciliation
        self.assertEqual(gr["recipient_state"], "VERIFIED")
        self.assertFalse(gr["gift_claim_verified"])
        self.assertEqual(gr["occasion_state"], "UNVERIFIED_BLOCKED")

    # -- assembled copy ---------------------------------------------------------
    def test_recommended_title(self):
        self.assertEqual(self.r1.recommended_title, "Nurse Sweatshirt")
        self.assertLessEqual(sum(1 for c in self.r1.title_options["candidates"] if c["recommended"]), 1)

    def test_all_bullets_deferred_with_reasons(self):
        self.assertEqual(self.r1.bullets["safe_draft_count"], 0)
        for b in self.r1.bullets["bullets"]:
            self.assertEqual(b["text"], "")
            self.assertTrue(b["reason_codes"])
        self.assertEqual([b["bullet_job"] for b in self.r1.bullets["bullets"]],
                         list(PDP.BE.JOBS))

    def test_description_is_product_identity_only(self):
        self.assertEqual(self.r1.description_text, "A nurse sweatshirt.")
        for bad in ("personalized", "gift", "cotton", "embroider"):
            self.assertNotIn(bad, self.r1.description_text.lower())

    def test_backend_byte_safe(self):
        be = self.r1.backend
        self.assertLessEqual(be["utf8_byte_count"], be["byte_limit"])
        self.assertEqual(be["byte_limit"], 249)
        self.assertEqual(set(be["selected_candidate_ids"]) | set(be["rejected_candidate_ids"]),
                         set(be["candidate_assignment_ids"]))
        for bad in ("personal", "cotton", "embroider"):
            self.assertNotIn(bad, be["final_search_terms"].lower())

    def test_item_highlights_empty(self):
        self.assertEqual(self.r1.item_highlights["state"], "CLAIM_EVIDENCE_MISSING")
        self.assertEqual(self.r1.item_highlights["texts"], [])

    def test_deferred_fields(self):
        df = self.r1.deferred_fields
        self.assertEqual(df["aplus"]["state"], "DEFERRED_OWNER_FACT_REQUIRED")
        self.assertEqual(df["listing_image_text"]["state"], "DEFERRED_TO_LATER_STAGE")
        self.assertEqual(df["image_alt_text"]["state"], "DEFERRED_TO_LATER_STAGE")

    # -- audit + state ----------------------------------------------------------
    def test_zero_leakage(self):
        leak = self.r1.page_audit["leakage_summary"]
        for k, v in leak.items():
            self.assertEqual(v, 0, k)

    def test_states(self):
        self.assertEqual(self.r1.listing_publishability_state, "SAFE_DRAFT_OWNER_FACTS_REQUIRED")
        self.assertEqual(self.r1.phase6c_state, "SAFE_PDP_OWNER_FACTS_REQUIRED")
        self.assertTrue(self.r1.ready_for_next_stage)

    def test_all_27_assignments_have_outcomes(self):
        self.assertEqual(len(self.r1.allocation_outcomes), 27)
        self.assertEqual(len({o["assignment_id"] for o in self.r1.allocation_outcomes}), 27)

    # -- determinism ------------------------------------------------------------
    def test_two_run_determinism(self):
        self.assertEqual(self.r1.deterministic_content_sha256(),
                         self.r2.deterministic_content_sha256())

    def test_three_mode_determinism(self):
        shas = {PDP.assemble_product_detail_page(T2, connectivity_mode=m).deterministic_content_sha256()
                for m in ("CONNECTED_RESEARCH", "LOCAL_SAFE", "TEST_DENY_EXTERNAL")}
        self.assertEqual(len(shas), 1)

    # -- write to temp, verify, and never touch the real T2 workspace -----------
    def test_write_verify_temp_and_t2_untouched(self):
        def snapshot():
            out = {}
            for stage in ("6A", "6B"):
                base = os.path.join(T2, "phase6", stage)
                for n in os.listdir(base):
                    with open(os.path.join(base, n), "rb") as f:
                        out[(stage, n)] = f.read()
            return out

        before = snapshot()
        r = PDP.assemble_product_detail_page(T2)
        root = tempfile.mkdtemp(prefix="6c-t2-")
        dest = os.path.join(root, "phase6", "6C")
        PDP.write_phase6c_artifacts(r, dest_dir=dest, workspace_dir=root, started_at="T0", completed_at="T1")
        v = PDP.verify_phase6c_artifacts(dest, workspace_dir=root)
        self.assertTrue(v["ok"], v["errors"])
        self.assertEqual(before, snapshot())                     # T2 6A/6B untouched
        self.assertEqual(set(os.listdir(dest)) & PDP.FORBIDDEN_ARTIFACTS, set())


if __name__ == "__main__":
    unittest.main()
