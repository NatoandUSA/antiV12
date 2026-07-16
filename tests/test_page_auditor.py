#!/usr/bin/env python3
"""Session 3 — listing.page_auditor (ACT-015): shared auditor + last-valid-listing protection."""
import json, os, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "listing"))
import keyword_source_adapter as KSA
import page_auditor as PA


def kw_source(folder):
    """A tiny lean source carrying a wrong-audience and a trademark risk record."""
    recs = [
        {"keyword_exact": "personalized nurse sweatshirt", "keyword_normalized": "personalized nurse sweatshirt",
         "tier": "A_CORE", "owner_status": "auto", "risk_flags": []},
        {"keyword_exact": "custom nurse gift", "keyword_normalized": "custom nurse gift",
         "tier": "B_SUPPORTING", "owner_status": "auto", "risk_flags": []},
        {"keyword_exact": "student nurse sweatshirt", "keyword_normalized": "student nurse sweatshirt",
         "tier": "REJECTED", "owner_status": "auto", "risk_flags": ["wrong_audience:student"]},
        {"keyword_exact": "disney nurse shirt", "keyword_normalized": "disney nurse shirt",
         "tier": "REJECTED", "owner_status": "auto", "risk_flags": ["trademark:disney"]},
    ]
    json.dump({"run_metadata": {"run_id": "kw_pa", "schema_version": "1.0.0"},
               "quality_summary": {"counts": {"A_CORE": 1, "B_SUPPORTING": 1, "REJECTED": 2}},
               "downstream_policy": {"mode": "transition"}, "keywords": recs},
              open(os.path.join(folder, "MASTER-KEYWORDS-LEAN.json"), "w"))
    return KSA.load_keyword_source(folder)


def safe_listing(src=None):
    L = {"category": "apparel", "title": "Personalized Nurse Sweatshirt",
         "bullets": ["PERSONALIZED FOR YOU: A personalized nurse sweatshirt.", "", "", "",
                     "A THOUGHTFUL GIFT: A personalized gift for nurses."],
         "bullet_objects": [{"bullet_number": i, "job": j, "text": "", "claim_ids": [],
                             "publishability": "PUBLISHABLE",
                             "verification_summary": {"verified": 0}}
                            for i, j in enumerate(
                                ["PRODUCT_AND_PERSONALIZATION", "EMBROIDERY_OR_DECORATION_PROOF",
                                 "FIT_SIZE_COLOR_ORDER_ACCURACY", "CARE_PRODUCTION_PACKAGING_DELIVERY",
                                 "RECIPIENT_OCCASION_USE"], 1)],
         "description": "A personalized nurse sweatshirt.", "backend": "custom nurse gift crewneck rn"}
    if src is not None:
        L["keyword_source_sha256"] = src.source_sha256
    return L


class ContentBlocks(unittest.TestCase):
    def test_safe_listing_passes(self):
        d = tempfile.mkdtemp()
        src = kw_source(d)
        audit = PA.audit_listing(safe_listing(src), keyword_source=src)
        self.assertIn(audit["status"], (PA.PASS, PA.PASS_WITH_WARNINGS))
        self.assertEqual(audit["hard_failures"], [])

    def test_wrong_audience_leakage_blocks(self):
        d = tempfile.mkdtemp()
        src = kw_source(d)
        L = safe_listing(src)
        L["bullets"][1] = "Great student nurse sweatshirt for clinicals"
        audit = PA.audit_listing(L, keyword_source=src)
        self.assertEqual(audit["status"], PA.BLOCKED)
        self.assertTrue(audit["keyword_results"]["wrong_audience_leakage"])

    def test_trademark_leakage_blocks(self):
        d = tempfile.mkdtemp()
        src = kw_source(d)
        L = safe_listing(src)
        L["description"] = "A personalized disney nurse shirt for fans."
        audit = PA.audit_listing(L, keyword_source=src)
        self.assertEqual(audit["status"], PA.BLOCKED)
        self.assertTrue(audit["keyword_results"]["brand_ip_leakage"])

    def test_unsupported_claim_blocks(self):
        L = safe_listing()
        L["bullets"][2] = "Soft and comfortable, made to last."
        audit = PA.audit_listing(L)
        self.assertEqual(audit["status"], PA.BLOCKED)
        self.assertTrue(audit["claim_results"]["unsupported"])

    def test_owner_review_claim_in_publishable_text_blocks(self):
        L = safe_listing()
        L["description"] = "A personalized nurse sweatshirt. Great for graduation."
        L["claim_evidence"] = {"verified": [],
                               "owner_review": [{"claim_id": "CLM-OCCASION", "concept": "occasion",
                                                 "text": "Great for graduation."}]}
        audit = PA.audit_listing(L)
        self.assertEqual(audit["status"], PA.BLOCKED)
        self.assertTrue(audit["claim_results"]["owner_review_in_copy"])

    def test_missing_claim_id_blocks(self):
        L = safe_listing()
        L["bullet_objects"][1] = {"bullet_number": 2, "job": "EMBROIDERY_OR_DECORATION_PROOF",
                                  "text": "REAL DECORATION: Raised satin-stitch embroidery.",
                                  "claim_ids": [], "publishability": "PUBLISHABLE",
                                  "verification_summary": {"verified": 1}}
        audit = PA.audit_listing(L)
        self.assertEqual(audit["status"], PA.BLOCKED)
        self.assertTrue(audit["claim_results"]["missing_lineage"])

    def test_unresolved_placeholder_blocks(self):
        L = safe_listing()
        L["description"] = "A personalized {garment} for {audience}."
        audit = PA.audit_listing(L)
        self.assertEqual(audit["status"], PA.BLOCKED)
        self.assertTrue(audit["claim_results"]["unresolved_placeholders"])


class StructureBlocks(unittest.TestCase):
    def test_title_hard_limit_blocks(self):
        L = safe_listing()
        L["title"] = "P" * 80
        audit = PA.audit_listing(L)
        self.assertEqual(audit["status"], PA.BLOCKED)
        self.assertTrue(any(h["category"] == "title_hard_limit" for h in audit["hard_failures"]))

    def test_duplicate_product_concept_blocks(self):
        L = safe_listing()
        L["title"] = "Nurse Sweatshirt Custom Sweatshirt Gift"
        audit = PA.audit_listing(L)
        self.assertTrue(any(h["category"] == "duplicate_product_concept" for h in audit["hard_failures"]))

    def test_duplicate_personalization_concept_blocks(self):
        L = safe_listing()
        L["title"] = "Personalized Personalized Nurse Sweatshirt"
        audit = PA.audit_listing(L)
        self.assertTrue(any(h["category"] == "duplicate_personalization_concept"
                            for h in audit["hard_failures"]))

    def test_compound_garment_is_not_a_duplicate(self):
        L = safe_listing()
        L["title"] = "Personalized Nurse Crewneck Sweatshirt Embroidered"
        audit = PA.audit_listing(L)
        self.assertFalse(any(h["category"] == "duplicate_product_concept" for h in audit["hard_failures"]))

    def test_bullet_count_failure_blocks(self):
        L = safe_listing()
        L["bullet_objects"] = L["bullet_objects"][:4]
        audit = PA.audit_listing(L)
        self.assertTrue(any(h["category"] == "bullet_count" for h in audit["hard_failures"]))

    def test_duplicate_bullet_jobs_block(self):
        L = safe_listing()
        L["bullet_objects"][1]["job"] = "PRODUCT_AND_PERSONALIZATION"
        audit = PA.audit_listing(L)
        self.assertTrue(any(h["category"] == "duplicate_bullet_jobs" for h in audit["hard_failures"]))

    def test_backend_overflow_blocks(self):
        L = safe_listing()
        L["backend"] = "x " * 200
        audit = PA.audit_listing(L)
        self.assertTrue(any(h["category"] == "backend_overflow" for h in audit["hard_failures"]))

    def test_source_hash_mismatch_blocks(self):
        d = tempfile.mkdtemp()
        src = kw_source(d)
        L = safe_listing(src)
        L["keyword_source_sha256"] = "deadbeef"
        audit = PA.audit_listing(L, keyword_source=src)
        self.assertTrue(any(h["category"] == "keyword_source_hash" for h in audit["hard_failures"]))


class LastValidProtection(unittest.TestCase):
    def test_unsafe_candidate_cannot_overwrite_last_valid_listing(self):
        d = tempfile.mkdtemp()
        good = safe_listing()
        r1 = PA.promote_if_safe(d, good)
        self.assertTrue(r1["promoted"])
        listing_path = os.path.join(d, "listing.json")
        self.assertTrue(os.path.exists(listing_path))

        unsafe = safe_listing()
        unsafe["bullets"][2] = "Made to last, shipped from the US with tracking."
        r2 = PA.promote_if_safe(d, unsafe)
        self.assertFalse(r2["promoted"])
        self.assertTrue(r2["last_valid_preserved"])
        # the good listing is still the one on disk
        cur = json.load(open(listing_path, encoding="utf-8"))
        self.assertEqual(cur["title"], good["title"])
        # the audit artifacts exist
        self.assertTrue(os.path.exists(os.path.join(d, "PAGE-AUDIT-REPORT.json")))
        self.assertTrue(os.path.exists(os.path.join(d, "FAILED-LISTING-CANDIDATE.json")))
        self.assertTrue(os.path.exists(os.path.join(d, "LAST-VALID-LISTING-METADATA.json")))

    def test_verdict_maps_status_to_ok(self):
        ok, errs = PA.audit_verdict(safe_listing())
        self.assertTrue(ok, errs)
        bad = safe_listing(); bad["title"] = "P" * 90
        ok2, errs2 = PA.audit_verdict(bad)
        self.assertFalse(ok2)
        self.assertTrue(errs2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
