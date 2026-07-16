#!/usr/bin/env python3
"""Session 3 — listing.bullet_engine (ACT-007): five distinct evidence-led bullet jobs."""
import json, os, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "listing"))
import product_fact_loader as PFL
import claim_evidence as CE
import bullet_engine as BE

PRIMARY = "personalized nurse sweatshirt"


def claims_for(product, keyword_context=None):
    d = tempfile.mkdtemp()
    json.dump({"product": product}, open(os.path.join(d, "product-facts.json"), "w"))
    return CE.build_claim_evidence(PFL.load_product_facts(folder=d), keyword_context=keyword_context)


FULL = {"decoration_method": {"value": "machine embroidery, satin stitch", "status": "VERIFIED"},
        "personalization_fields": {"value": ["name", "credentials"], "status": "VERIFIED"},
        "material": {"value": "80% cotton 20% polyester", "status": "VERIFIED"},
        "fit": {"value": "unisex relaxed", "status": "VERIFIED"},
        "shipping_method": {"value": "Ships from the US with tracking", "status": "VERIFIED"},
        "occasion": {"value": "nurse appreciation", "status": "VERIFIED"}}


class Structure(unittest.TestCase):
    def test_exactly_five_bullets(self):
        r = BE.build_bullets(claims_for({}, {"audience": "nurses"}), PRIMARY)
        self.assertEqual(len(r.bullets), 5)

    def test_all_jobs_unique(self):
        r = BE.build_bullets(claims_for(FULL, {"audience": "nurses"}), PRIMARY)
        jobs = [b["job"] for b in r.bullets]
        self.assertEqual(len(set(jobs)), 5)
        self.assertEqual(jobs, list(BE.JOBS))

    def test_all_jobs_publishable_with_full_facts(self):
        r = BE.build_bullets(claims_for(FULL, {"audience": "nurses"}), PRIMARY)
        self.assertEqual(r.publishable_count, 5)

    def test_missing_essential_evidence_blocks_only_the_affected_job(self):
        # only decoration verified -> embroidery bullet publishes, fit/care bullets block, others by job.
        claims = claims_for({"decoration_method": {"value": "machine embroidery", "status": "VERIFIED"}},
                            {"audience": "nurses"})
        r = BE.build_bullets(claims, PRIMARY)
        by_job = {b["job"]: b for b in r.bullets}
        self.assertEqual(by_job["EMBROIDERY_OR_DECORATION_PROOF"]["publishability"], BE.PUBLISHABLE)
        self.assertEqual(by_job["FIT_SIZE_COLOR_ORDER_ACCURACY"]["publishability"], BE.BLOCKED_INCOMPLETE)
        self.assertEqual(by_job["CARE_PRODUCTION_PACKAGING_DELIVERY"]["publishability"], BE.BLOCKED_INCOMPLETE)


class Evidence(unittest.TestCase):
    def test_every_factual_bullet_carries_claim_ids(self):
        r = BE.build_bullets(claims_for(FULL, {"audience": "nurses"}), PRIMARY)
        for b in r.bullets:
            if b["verification_summary"]["verified"] > 0:
                self.assertTrue(b["claim_ids"], b["job"])

    def test_unsupported_claim_language_is_omitted_not_filled(self):
        # no facts -> embroidery/fit/care bullets are empty, NOT generic marketing filler.
        r = BE.build_bullets(claims_for({}, {"audience": "nurses"}), PRIMARY)
        by_job = {b["job"]: b for b in r.bullets}
        self.assertEqual(by_job["EMBROIDERY_OR_DECORATION_PROOF"]["text"], "")
        self.assertEqual(by_job["FIT_SIZE_COLOR_ORDER_ACCURACY"]["text"], "")
        self.assertTrue(by_job["EMBROIDERY_OR_DECORATION_PROOF"]["missing_requirements"])

    def test_no_unsupported_comfort_durability_shipping_tracking_or_exact_personalization(self):
        # nothing verified -> none of the risky promises appear anywhere in the bullet text.
        r = BE.build_bullets(claims_for({}, {"audience": "nurses"}), PRIMARY)
        blob = " ".join(b["text"] for b in r.bullets).lower()
        for bad in ("comfortable", "made to last", "shipped from the us", "with tracking",
                    "exactly as you enter", "made to order"):
            self.assertNotIn(bad, blob)

    def test_verified_us_shipping_appears_only_when_verified(self):
        r = BE.build_bullets(claims_for(FULL, {"audience": "nurses"}), PRIMARY)
        blob = " ".join(b["text"] for b in r.bullets).lower()
        self.assertIn("shipped from the us", blob)

    def test_no_repeated_product_identity_across_five_bullets(self):
        # with no facts, only bullet 1 (its job) states product identity; blocked bullets do not repeat it.
        r = BE.build_bullets(claims_for({}, {"audience": "nurses"}), PRIMARY)
        stating_identity = [b for b in r.bullets if "sweatshirt" in b["text"].lower()]
        self.assertLessEqual(len(stating_identity), 1)

    def test_keyword_allocations_are_metadata_only(self):
        r = BE.build_bullets(claims_for({}, {"audience": "nurses"}), PRIMARY,
                             eligible_keywords=["custom nurse gift", "embroidered nurse crewneck"])
        self.assertTrue(any(b["keyword_allocations"] for b in r.bullets))


if __name__ == "__main__":
    unittest.main(verbosity=2)
