#!/usr/bin/env python3
"""Tests for listing.item_highlights_builder (ACT-010) — category-capability & claim-aware highlights."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "listing"))
import category_policy_registry as CPR
import product_fact_loader as PFL
import claim_evidence as CE
import item_highlights_builder as IHB


def facts_with(**fields):
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "product-facts.json"), "w", encoding="utf-8") as f:
        json.dump({"schema_version": "1.0.0", "product": fields}, f)
    return PFL.load_product_facts(folder=d)


def claims_from(facts, prohibited=()):
    return CE.build_claim_evidence(facts, keyword_context={"audience": "nurses"},
                                   prohibited_concepts=prohibited)


def apparel(**override):
    return CPR.resolve_category_policy("apparel", owner_override=override or None)


def fallback_policy():
    return CPR.resolve_category_policy("some-unknown-category-xyz")


VERIFIED_FACTS = dict(
    material_composition={"value": "80% cotton, 20% polyester", "status": "VERIFIED"},
    fit={"value": "relaxed", "status": "VERIFIED"},
    color_options={"value": "black, navy", "status": "VERIFIED"},
)


class CategorySupport(unittest.TestCase):
    def test_unsupported_category_excludes_the_field(self):
        facts = facts_with(**VERIFIED_FACTS)
        r = IHB.build_item_highlights_content(claims_from(facts), fallback_policy())
        self.assertFalse(r.category_field_supported)
        self.assertEqual(r.category_support_state, IHB.UNSUPPORTED_BY_CATEGORY)
        self.assertEqual(r.publishable_highlights, [])
        # every candidate is reported as unsupported (manual-review), never published.
        for h in r.highlights:
            self.assertEqual(h["publishability_status"], IHB.UNSUPPORTED_BY_CATEGORY)

    def test_supported_category_permits_verified_highlight(self):
        facts = facts_with(**VERIFIED_FACTS)
        r = IHB.build_item_highlights_content(claims_from(facts), apparel())
        self.assertTrue(r.category_field_supported)
        concepts = {h["concept"] for h in r.publishable_highlights}
        self.assertIn("material_composition", concepts)
        self.assertIn("fit", concepts)
        self.assertIn("color_options", concepts)


class EvidenceGating(unittest.TestCase):
    def test_unknown_fact_blocks_highlight(self):
        facts = facts_with(**VERIFIED_FACTS)          # size_range absent -> UNKNOWN
        r = IHB.build_item_highlights_content(claims_from(facts), apparel())
        sr = next(h for h in r.highlights if h["concept"] == "size_range")
        self.assertEqual(sr["publishability_status"], IHB.BLOCKED_UNVERIFIED)
        self.assertNotIn("size_range", {h["concept"] for h in r.publishable_highlights})

    def test_owner_review_fact_does_not_publish(self):
        facts = facts_with(decoration_method={"value": "machine embroidery",
                                              "status": "OWNER_REVIEW_REQUIRED"})
        r = IHB.build_item_highlights_content(claims_from(facts), apparel())
        dm = next(h for h in r.highlights if h["concept"] == "decoration_method")
        self.assertEqual(dm["publishability_status"], IHB.OWNER_FACT_REQUIRED)
        self.assertEqual(r.publishable_highlights, [])

    def test_prohibited_claim_blocks(self):
        facts = facts_with(**VERIFIED_FACTS)
        r = IHB.build_item_highlights_content(claims_from(facts, prohibited=("material_composition",)),
                                              apparel())
        mc = next(h for h in r.highlights if h["concept"] == "material_composition")
        self.assertEqual(mc["publishability_status"], IHB.PROHIBITED)
        self.assertNotIn("material_composition", {h["concept"] for h in r.publishable_highlights})

    def test_claim_lineage_required_on_published(self):
        facts = facts_with(**VERIFIED_FACTS)
        r = IHB.build_item_highlights_content(claims_from(facts), apparel())
        self.assertTrue(r.publishable_highlights)
        for h in r.publishable_highlights:
            self.assertTrue(h["claim_ids"])
            self.assertEqual(h["verification_state"], "VERIFIED")


class QualityGates(unittest.TestCase):
    def test_no_generic_filler(self):
        facts = facts_with(**VERIFIED_FACTS)
        claims = claims_from(facts)
        r = IHB.build_item_highlights_content(claims, apparel())
        for h in r.publishable_highlights:
            # published text is EXACTLY the verified claim's proposed text — nothing invented.
            self.assertEqual(h["text"], claims.claim(h["concept"])["proposed_text"])

    def test_no_duplicate_concepts(self):
        facts = facts_with(**VERIFIED_FACTS)
        r = IHB.build_item_highlights_content(claims_from(facts), apparel())
        concepts = [h["concept"] for h in r.publishable_highlights]
        self.assertEqual(len(concepts), len(set(concepts)))

    def test_duplicate_of_visible_excluded(self):
        facts = facts_with(fit={"value": "relaxed", "status": "VERIFIED"})
        claims = claims_from(facts)
        fit_text = claims.claim("fit")["proposed_text"]        # "Relaxed fit."
        r = IHB.build_item_highlights_content(claims, apparel(), title="Nurse Sweatshirt " + fit_text)
        fit = next(h for h in r.highlights if h["concept"] == "fit")
        self.assertEqual(fit["publishability_status"], IHB.DUPLICATE_OF_VISIBLE)

    def test_limits_come_from_category_policy_count(self):
        facts = facts_with(**VERIFIED_FACTS)                    # 3 verified concepts
        r = IHB.build_item_highlights_content(claims_from(facts),
                                              apparel(item_highlights_max_count=1))
        self.assertEqual(r.publishable_count, 1)
        self.assertTrue(any(h["publishability_status"] == IHB.MAX_COUNT_REACHED for h in r.highlights))

    def test_over_character_limit_excluded(self):
        facts = facts_with(**VERIFIED_FACTS)
        r = IHB.build_item_highlights_content(claims_from(facts),
                                              apparel(item_highlights_character_limit=5))
        self.assertEqual(r.publishable_count, 0)
        self.assertTrue(any(h["publishability_status"] == IHB.OVER_CHARACTER_LIMIT for h in r.highlights))


class Determinism(unittest.TestCase):
    def test_deterministic_hash(self):
        facts = facts_with(**VERIFIED_FACTS)
        a = IHB.build_item_highlights_content(claims_from(facts), apparel())
        b = IHB.build_item_highlights_content(claims_from(facts), apparel())
        self.assertEqual(a.content_sha256(), b.content_sha256())

    def test_empty_when_no_verified_facts(self):
        facts = facts_with()                                   # all UNKNOWN
        r = IHB.build_item_highlights_content(claims_from(facts), apparel())
        self.assertEqual(r.publishable_highlights, [])         # no generic filler invented
        self.assertTrue(r.category_field_supported)            # field IS supported; just nothing verified


if __name__ == "__main__":
    unittest.main(verbosity=2)
