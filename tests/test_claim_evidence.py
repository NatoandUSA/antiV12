#!/usr/bin/env python3
"""Session 3 — listing.claim_evidence (ACT-007/008): evidence-classed claims + no-inference rules."""
import json, os, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "listing"))
import product_fact_loader as PFL
import claim_evidence as CE


def facts_with(product):
    d = tempfile.mkdtemp()
    json.dump({"schema_version": "1.0.0", "source": "owner", "product": product},
              open(os.path.join(d, "product-facts.json"), "w"))
    return PFL.load_product_facts(folder=d)


def build(product, keyword_context=None):
    return CE.build_claim_evidence(facts_with(product), keyword_context=keyword_context)


class ClaimStates(unittest.TestCase):
    def test_verified_fact_creates_verified_publishable_claim(self):
        ev = build({"material": {"value": "80% cotton 20% polyester", "status": "VERIFIED"}})
        c = ev.claim("material_composition")
        self.assertEqual(c["verification_state"], CE.VERIFIED)
        self.assertTrue(c["publishable"])
        self.assertIn("80% cotton 20% polyester", c["proposed_text"])
        self.assertIn("material", c["source_fact_fields"])

    def test_owner_review_fact_creates_non_publishable_claim(self):
        ev = build({"material": {"value": "mystery fabric", "status": "OWNER_REVIEW_REQUIRED"}})
        c = ev.claim("material_composition")
        self.assertEqual(c["verification_state"], CE.SUPPORTED_OWNER_REVIEW)
        self.assertFalse(c["publishable"])

    def test_unknown_fact_creates_blocked_claim(self):
        ev = build({})                                       # material absent -> UNKNOWN
        c = ev.claim("material_composition")
        self.assertEqual(c["verification_state"], CE.UNVERIFIED_BLOCKED)
        self.assertFalse(c["publishable"])
        self.assertIsNone(c["proposed_text"])

    def test_blocked_fact_creates_prohibited_claim_that_never_publishes(self):
        ev = build({"fit": {"value": "unisex", "status": "BLOCKED"}})
        c = ev.claim("fit")
        self.assertEqual(c["verification_state"], CE.PROHIBITED)
        self.assertFalse(c["publishable"])

    def test_verified_claim_is_the_only_publishable_state(self):
        ev = build({"material": {"value": "cotton", "status": "VERIFIED"},
                    "occasion": {"value": "graduation", "status": "OWNER_REVIEW_REQUIRED"}})
        for c in ev.publishable:
            self.assertEqual(c["verification_state"], CE.VERIFIED)


class NoInference(unittest.TestCase):
    def test_softness_not_inferred_from_material(self):
        ev = build({"material": {"value": "soft cotton fleece", "status": "VERIFIED"}})
        self.assertEqual(ev.state("softness_comfort"), CE.UNVERIFIED_BLOCKED)

    def test_comfort_not_inferred_from_measurements(self):
        ev = build({"measurements": {"value": ["chest 22in", "length 28in"], "status": "VERIFIED"}})
        self.assertEqual(ev.state("softness_comfort"), CE.UNVERIFIED_BLOCKED)

    def test_durability_not_inferred_from_embroidery(self):
        ev = build({"decoration_method": {"value": "machine embroidery", "status": "VERIFIED"}})
        self.assertEqual(ev.state("real_machine_embroidery"), CE.VERIFIED)   # embroidery IS verified
        self.assertEqual(ev.state("durability"), CE.UNVERIFIED_BLOCKED)      # but durability is not

    def test_us_shipping_not_inferred_from_supplier_location(self):
        ev = build({"production_location": {"value": "overseas contract supplier", "status": "VERIFIED"}})
        self.assertEqual(ev.state("shipping_method"), CE.UNVERIFIED_BLOCKED)

    def test_tracking_not_inferred_from_shipping_existence(self):
        # shipping is verified but there is no dedicated tracking fact -> the tracking CLAIM stays blocked.
        ev = build({"shipping_method": {"value": "ships in 5 business days", "status": "VERIFIED"}})
        self.assertEqual(ev.state("shipping_method"), CE.VERIFIED)
        self.assertEqual(ev.state("tracking"), CE.UNVERIFIED_BLOCKED)

    def test_exact_personalization_not_inferred_from_name_field(self):
        ev = build({"personalization_fields": {"value": ["name"], "status": "VERIFIED"}})
        self.assertEqual(ev.state("personalization_fields"), CE.VERIFIED)
        self.assertEqual(ev.state("exact_personalization_promise"), CE.UNVERIFIED_BLOCKED)

    def test_made_to_order_not_inferred_from_personalization(self):
        ev = build({"personalization_fields": {"value": ["name", "title"], "status": "VERIFIED"}})
        self.assertEqual(ev.state("made_to_order"), CE.UNVERIFIED_BLOCKED)

    def test_verified_us_shipping_reads_us_from_the_fact_not_inference(self):
        ev = build({"shipping_method": {"value": "Ships from the US with tracking", "status": "VERIFIED"}})
        c = ev.claim("shipping_method")
        self.assertEqual(c["verification_state"], CE.VERIFIED)
        self.assertIn("Shipped from the US", c["proposed_text"])

    def test_recipient_verified_from_approved_keyword(self):
        ev = build({}, keyword_context={"audience": "nurses"})
        c = ev.claim("recipient")
        self.assertEqual(c["verification_state"], CE.VERIFIED)
        self.assertIn("nurses", c["proposed_text"])


class Serialization(unittest.TestCase):
    def test_deterministic_claim_hash(self):
        product = {"material": {"value": "cotton", "status": "VERIFIED"},
                   "decoration_method": {"value": "machine embroidery", "status": "VERIFIED"}}
        a = CE.build_claim_evidence(facts_with(product), keyword_context={"audience": "nurses"})
        b = CE.build_claim_evidence(facts_with(product), keyword_context={"audience": "nurses"})
        self.assertEqual(a.content_sha256(), b.content_sha256())

    def test_counts_and_missing_requirements(self):
        ev = build({"material": {"value": "cotton", "status": "VERIFIED"}})
        self.assertGreaterEqual(ev.verified_count, 1)
        self.assertGreater(ev.blocked_count, 0)
        concepts = {r["concept"] for r in ev.missing_evidence_requirements}
        self.assertIn("softness_comfort", concepts)

    def test_write_claim_evidence_artifact(self):
        d = tempfile.mkdtemp()
        json.dump({"product": {"material": {"value": "cotton", "status": "VERIFIED"}}},
                  open(os.path.join(d, "product-facts.json"), "w"))
        path, ev = CE.write_claim_evidence(d)
        self.assertTrue(os.path.exists(path))
        doc = json.load(open(path, encoding="utf-8"))
        self.assertEqual(doc["content_sha256"], ev.content_sha256())
        self.assertIn("verified_count", doc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
