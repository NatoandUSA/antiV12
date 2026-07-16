#!/usr/bin/env python3
"""Session 3 — listing.title_engine (ACT-005): semantic component titles, no global word deletion."""
import json, os, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "listing"))
import product_fact_loader as PFL
import claim_evidence as CE
import category_policy_registry as CPR
import title_engine as TE

POLICY = CPR.resolve_category_policy("apparel")
PRIMARY = "personalized nurse sweatshirt"


def claims_for(product, keyword_context=None):
    d = tempfile.mkdtemp()
    json.dump({"product": product}, open(os.path.join(d, "product-facts.json"), "w"))
    return CE.build_claim_evidence(PFL.load_product_facts(folder=d), keyword_context=keyword_context)


class Components(unittest.TestCase):
    def test_identity_only_when_no_verified_attributes(self):
        r = TE.build_titles(PRIMARY, claims_for({}, {"audience": "nurses"}), POLICY, audience_hint="nurse")
        self.assertEqual(r.title_recommended, "Personalized Nurse Sweatshirt")
        self.assertEqual(r.status, "OK")

    def test_verified_attributes_add_complete_components(self):
        claims = claims_for({"decoration_method": {"value": "machine embroidery", "status": "VERIFIED"},
                             "verified_differentiator": {"value": "RN Gift", "status": "VERIFIED"}},
                            {"audience": "nurses"})
        r = TE.build_titles(PRIMARY, claims, POLICY, audience_hint="nurse")
        self.assertIn("Embroidered", r.title_expanded)
        self.assertIn("RN Gift", r.title_expanded)
        ids = [c["component_id"] for c in r.components if not c["excluded"]]
        self.assertIn("decoration", ids)

    def test_no_global_word_deletion(self):
        # a natural repeated word required by two distinct components stays; only concepts dedupe.
        r = TE.build_titles(PRIMARY, claims_for({}, {"audience": "nurses"}), POLICY, audience_hint="nurse")
        # "nurse" appears once (identity); the audience component was a duplicate concept and removed
        # as a WHOLE component, not by deleting a token elsewhere.
        self.assertEqual(r.title_recommended.lower().count("nurse"), 1)

    def test_no_duplicate_personalization_concept(self):
        # identity already carries "personalized"; a verified personalization claim must not add another.
        claims = claims_for({"personalization_fields": {"value": ["name"], "status": "VERIFIED"}},
                            {"audience": "nurses"})
        r = TE.build_titles(PRIMARY, claims, POLICY, audience_hint="nurse")
        low = r.title_recommended.lower()
        self.assertEqual(low.count("personalized"), 1)
        excluded = [c["component_id"] for c in r.components if c["excluded"]]
        self.assertIn("personalization", excluded)

    def test_no_duplicate_product_concept(self):
        r = TE.build_titles(PRIMARY, claims_for({}, {"audience": "nurses"}), POLICY, audience_hint="nurse")
        self.assertEqual(r.title_recommended.lower().count("sweatshirt"), 1)

    def test_no_duplicate_audience_concept(self):
        claims = claims_for({}, {"audience": "nurses"})
        r = TE.build_titles(PRIMARY, claims, POLICY, audience_hint="nurse")
        excluded = [c["component_id"] for c in r.components if c["excluded"]]
        self.assertIn("audience", excluded)          # "nurse" already in identity

    def test_product_identity_appears_first(self):
        claims = claims_for({"decoration_method": {"value": "machine embroidery", "status": "VERIFIED"}},
                            {"audience": "nurses"})
        r = TE.build_titles(PRIMARY, claims, POLICY, audience_hint="nurse")
        self.assertTrue(r.title_recommended.lower().startswith("personalized nurse sweatshirt"))


class Scoring(unittest.TestCase):
    def test_concise_and_expanded_scored_independently(self):
        claims = claims_for({"decoration_method": {"value": "machine embroidery", "status": "VERIFIED"},
                             "verified_differentiator": {"value": "RN Gift", "status": "VERIFIED"}},
                            {"audience": "nurses"})
        r = TE.build_titles(PRIMARY, claims, POLICY, audience_hint="nurse")
        self.assertIn("CONCISE", r.scores)
        self.assertIn("EXPANDED", r.scores)
        self.assertNotEqual(r.scores["CONCISE"]["score"], r.scores["EXPANDED"]["score"])

    def test_longer_candidate_does_not_automatically_win(self):
        # with no verified attributes, concise == expanded; the shorter/equal candidate is recommended,
        # never auto-picking the longer text.
        r = TE.build_titles(PRIMARY, claims_for({}, {"audience": "nurses"}), POLICY, audience_hint="nurse")
        self.assertEqual(r.recommended_candidate, "CONCISE")

    def test_unverified_facts_cannot_enter_title(self):
        # owner-review decoration must not appear in any candidate.
        claims = claims_for({"decoration_method": {"value": "heat vinyl", "status": "OWNER_REVIEW_REQUIRED"}},
                            {"audience": "nurses"})
        r = TE.build_titles(PRIMARY, claims, POLICY, audience_hint="nurse")
        self.assertNotIn("embroidered", r.title_expanded.lower())
        self.assertNotIn("vinyl", r.title_expanded.lower())

    def test_category_policy_hard_limit_respected(self):
        r = TE.build_titles(PRIMARY, claims_for({}, {"audience": "nurses"}), POLICY)
        self.assertLessEqual(len(r.title_recommended), POLICY.title_hard_limit)


class MobilePreview(unittest.TestCase):
    def test_word_boundary_and_identity_visibility(self):
        long_primary = "personalized registered nurse practitioner crewneck sweatshirt"
        r = TE.build_titles(long_primary, claims_for({}, {"audience": "nurses"}), POLICY, mobile_cutoff=30)
        mp = r.mobile_preview
        self.assertLessEqual(mp["char_count"], 30)
        self.assertFalse(mp["text"].endswith(" "))
        # truncation must not split a word: the preview is a prefix ending at a space boundary.
        self.assertTrue(r.title_recommended.startswith(mp["text"]))
        self.assertIn("product_identity_visible", mp)

    def test_overlong_component_omitted_or_fails_safely_without_slicing(self):
        tiny = CPR.CategoryPolicy(schema_version="1.0.0", marketplace="US", product_category="apparel",
                                  category_identifier="US:apparel", title_hard_limit=10,
                                  title_recommended_target=8, backend_byte_ceiling=249)
        r = TE.build_titles(PRIMARY, claims_for({}, {"audience": "nurses"}), tiny)
        # never sliced: either a complete shorter approved phrase, or TITLE_COMPONENT_TOO_LONG.
        self.assertIn(r.status, ("OK", "TITLE_COMPONENT_TOO_LONG"))
        if r.status == "OK":
            self.assertLessEqual(len(r.title_recommended), 10)
            self.assertIn(r.title_recommended.lower().rstrip(),
                          {"sweatshirt", "nurse sweatshirt", "personalized nurse sweatshirt"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
