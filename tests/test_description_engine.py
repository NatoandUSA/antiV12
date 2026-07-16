#!/usr/bin/env python3
"""Session 3 — listing.description_engine (ACT-008): supported sections only, claim lineage."""
import json, os, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "listing"))
import product_fact_loader as PFL
import claim_evidence as CE
import description_engine as DE

PRIMARY = "personalized nurse sweatshirt"


def claims_for(product, keyword_context=None):
    d = tempfile.mkdtemp()
    json.dump({"product": product}, open(os.path.join(d, "product-facts.json"), "w"))
    return CE.build_claim_evidence(PFL.load_product_facts(folder=d), keyword_context=keyword_context)


class Sections(unittest.TestCase):
    def test_only_verified_sections_appear(self):
        claims = claims_for({"material": {"value": "80% cotton 20% polyester", "status": "VERIFIED"}},
                            {"audience": "nurses"})
        r = DE.build_description(claims, PRIMARY)
        ids = [s["section_id"] for s in r.sections]
        self.assertIn("product_identity", ids)
        self.assertIn("attributes", ids)
        self.assertIn("80% cotton 20% polyester", r.description_text)

    def test_unsupported_sections_omitted_with_reason(self):
        r = DE.build_description(claims_for({}, {"audience": "nurses"}), PRIMARY)
        omitted = {o["section_id"] for o in r.omitted_sections}
        self.assertIn("decoration", omitted)
        self.assertIn("attributes", omitted)
        self.assertIn("fulfillment", omitted)
        for o in r.omitted_sections:
            self.assertEqual(o["reason"], "no verified evidence")

    def test_no_unsupported_us_shipping_or_material(self):
        text = DE.build_description(claims_for({}, {"audience": "nurses"}), PRIMARY).description_text.lower()
        self.assertNotIn("shipped from the us", text)
        self.assertNotIn("cotton", text)
        self.assertNotIn("made from", text)

    def test_verified_us_shipping_enables_claim(self):
        claims = claims_for({"shipping_method": {"value": "Ships from the US with tracking",
                                                 "status": "VERIFIED"}}, {"audience": "nurses"})
        text = DE.build_description(claims, PRIMARY).description_text.lower()
        self.assertIn("shipped from the us", text)

    def test_claim_lineage_present_on_verified_sections(self):
        claims = claims_for({"material": {"value": "cotton fleece", "status": "VERIFIED"}},
                            {"audience": "nurses"})
        r = DE.build_description(claims, PRIMARY)
        for s in r.sections:
            if s["state"] == "VERIFIED":
                self.assertTrue(s["claim_ids"], s["section_id"])
        self.assertTrue(r.claim_ids)

    def test_no_unresolved_placeholders(self):
        r = DE.build_description(claims_for({"material": {"value": "cotton", "status": "VERIFIED"}},
                                            {"audience": "nurses"}), PRIMARY)
        self.assertNotIn("{", r.description_text)
        self.assertNotIn("}", r.description_text)

    def test_product_identity_always_present(self):
        r = DE.build_description(claims_for({}, {}), PRIMARY)
        self.assertIn("personalized nurse sweatshirt", r.description_text.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
