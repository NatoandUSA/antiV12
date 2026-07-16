#!/usr/bin/env python3
"""
Session 4 — evidence-aware, capability-based A+ builder unit proofs (ACT-011/012/013/014).

Covers the four capability modes, the exact five-module Basic journey and its module-specific evidence
gates, the "no filler" Premium dynamic eligibility rules, and the Basic-fallback reindexing.
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("listing",):
    _p = os.path.join(ROOT, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import product_fact_loader as PFL          # noqa: E402
import claim_evidence as CE                # noqa: E402
import aplus_builder as AB                 # noqa: E402
import aplus_module_registry as REG        # noqa: E402

# a full, verified fact set — enough to take every Basic module to READY (given real assets).
FULL_FACTS = {
    "decoration_method": {"value": "machine embroidery", "status": "VERIFIED"},
    "personalization_fields": {"value": ["name"], "status": "VERIFIED"},
    "size_range": {"value": ["S", "M", "L", "XL"], "status": "VERIFIED"},
    "color_options": {"value": ["black", "navy"], "status": "VERIFIED"},
    "care_instructions": {"value": "Machine wash cold", "status": "VERIFIED"},
    "occasion": {"value": "graduation", "status": "VERIFIED"},
    "packaging": {"value": "gift box", "status": "VERIFIED"},
    "brand": {"value": "Acme", "status": "VERIFIED"},
}
REAL_ASSETS = [{"proof_purpose": p, "real_or_generated": "REAL",
                "alt_text": "A real product photo showing the detail", "asset_type": "image"}
               for p in ("MACRO_DECORATION_STITCH", "PERSONALIZATION_EXAMPLE", "GARMENT_OR_COLOR",
                         "LIFESTYLE", "GIFT_PACKAGING", "PRODUCTION_PROCESS", "DESIGN_VARIATIONS",
                         "GARMENT_OPTIONS")]


def make(facts_dict=None, keyword_context=None):
    d = tempfile.mkdtemp()
    if facts_dict is not None:
        with open(os.path.join(d, "product-facts.json"), "w", encoding="utf-8") as f:
            json.dump({"schema_version": "1.0.0", "source": "owner", "product": facts_dict}, f)
    facts = PFL.load_product_facts(folder=d)
    claims = CE.build_claim_evidence(facts, keyword_context=keyword_context or {"audience": "nurses"})
    return claims, facts


# ============================================================ capability modes
class CapabilityModes(unittest.TestCase):
    def test_no_a_plus_zero_modules(self):
        claims, facts = make(FULL_FACTS)
        r = AB.build_aplus(REG.NO_A_PLUS, claims, facts, assets=REAL_ASSETS)
        self.assertEqual(len(r.basic_modules), 0)
        self.assertEqual(r.publishable_modules(), [])
        self.assertIsNotNone(r.fallback_plan)
        self.assertEqual(r.to_premium_draft()["premium"], None)

    def test_basic_a_plus_five_module_slots(self):
        claims, facts = make(FULL_FACTS)
        r = AB.build_aplus(REG.BASIC_A_PLUS, claims, facts, assets=REAL_ASSETS)
        self.assertEqual(len(r.basic_modules), REG.BASIC_MODULE_COUNT)
        self.assertEqual([m["position_slot"] for m in r.basic_modules], [1, 2, 3, 4, 5])
        self.assertEqual(r.to_premium_draft()["premium"], None)     # Basic capability carries no Premium

    def test_premium_seven_only_when_eligible_and_confirmed(self):
        claims, facts = make(FULL_FACTS)
        r = AB.build_aplus(REG.PREMIUM_A_PLUS, claims, facts, assets=REAL_ASSETS, premium_confirmed=True)
        self.assertTrue(r.basic_ready)
        self.assertTrue(r.premium_eligible)
        self.assertEqual(len(r.premium_modules()), REG.PREMIUM_MODULE_COUNT)
        self.assertEqual(len(r.publishable_modules()), REG.PREMIUM_MODULE_COUNT)

    def test_premium_not_confirmed_falls_back_to_basic_five(self):
        claims, facts = make(FULL_FACTS)
        r = AB.build_aplus(REG.PREMIUM_A_PLUS, claims, facts, assets=REAL_ASSETS, premium_confirmed=False)
        self.assertFalse(r.premium_eligible)
        self.assertEqual(len(r.publishable_modules()), REG.BASIC_MODULE_COUNT)

    def test_premium_incomplete_when_fewer_than_two_dynamic(self):
        # only LIFESTYLE can be eligible (occasion + asset); no packaging/brand/etc -> one eligible only.
        facts_one = {**{k: FULL_FACTS[k] for k in ("decoration_method", "personalization_fields",
                                                   "size_range", "color_options", "care_instructions",
                                                   "occasion")}}
        claims, facts = make(facts_one)
        assets = [a for a in REAL_ASSETS if a["proof_purpose"] in
                  ("MACRO_DECORATION_STITCH", "PERSONALIZATION_EXAMPLE", "GARMENT_OR_COLOR", "LIFESTYLE")]
        r = AB.build_aplus(REG.PREMIUM_A_PLUS, claims, facts, assets=assets, premium_confirmed=True)
        self.assertTrue(r.basic_ready)
        self.assertEqual(len(r.eligible_dynamic), 1)
        self.assertFalse(r.premium_eligible)                        # one eligible dynamic is not enough
        self.assertEqual(len(r.publishable_modules()), REG.BASIC_MODULE_COUNT)   # Basic still usable

    def test_unknown_returns_basic_plus_unverified_premium_draft(self):
        claims, facts = make(FULL_FACTS)
        r = AB.build_aplus(REG.UNKNOWN_A_PLUS_CAPABILITY, claims, facts, assets=REAL_ASSETS,
                           premium_confirmed=True)
        self.assertEqual(len(r.basic_modules), REG.BASIC_MODULE_COUNT)
        self.assertEqual(len(r.publishable_modules()), REG.BASIC_MODULE_COUNT)   # Basic publishes
        draft = r.to_premium_draft()
        self.assertTrue(draft["eligibility_unverified"])
        self.assertTrue(draft["never_publishable"])
        self.assertEqual(draft["premium_status"], REG.ELIGIBILITY_UNVERIFIED)
        # every premium draft module is ELIGIBILITY_UNVERIFIED and non-publishable
        self.assertTrue(all(m["status"] == REG.ELIGIBILITY_UNVERIFIED for m in draft["modules"]))

    def test_invalid_capability_fails_and_is_not_defaulted_to_basic(self):
        claims, facts = make(FULL_FACTS)
        with self.assertRaises(REG.InvalidCapabilityError):
            AB.build_aplus("SILVER_A_PLUS", claims, facts)

    def test_absent_capability_defaults_to_unknown_not_basic(self):
        claims, facts = make(FULL_FACTS)
        r = AB.build_aplus(None, claims, facts)
        self.assertEqual(r.capability, REG.UNKNOWN_A_PLUS_CAPABILITY)


# ============================================================ Basic modules
class BasicModules(unittest.TestCase):
    def test_correct_five_module_order(self):
        claims, facts = make(FULL_FACTS)
        r = AB.build_aplus(REG.BASIC_A_PLUS, claims, facts, assets=REAL_ASSETS)
        self.assertEqual([m["module_key"] for m in r.basic_modules],
                         ["HERO_EMBROIDERY_PROOF", "PERSONALIZATION_GALLERY", "HOW_TO_CUSTOMIZE",
                          "FIT_SIZE_COLOR_DETAILS", "CARE_PRODUCTION_FAQ"])

    def test_module_specific_required_facts(self):
        claims, facts = make(FULL_FACTS)
        r = AB.build_aplus(REG.BASIC_A_PLUS, claims, facts, assets=REAL_ASSETS)
        by = {m["module_key"]: m for m in r.basic_modules}
        self.assertIn("decoration_method", by["HERO_EMBROIDERY_PROOF"]["required_facts"])
        self.assertIn("personalization_fields", by["PERSONALIZATION_GALLERY"]["required_facts"])
        self.assertIn("size_range", by["FIT_SIZE_COLOR_DETAILS"]["required_facts"])
        self.assertIn("color_options", by["FIT_SIZE_COLOR_DETAILS"]["required_facts"])

    def test_missing_fact_blocks_only_relevant_module(self):
        facts_no_deco = {k: v for k, v in FULL_FACTS.items() if k != "decoration_method"}
        claims, facts = make(facts_no_deco)
        r = AB.build_aplus(REG.BASIC_A_PLUS, claims, facts, assets=REAL_ASSETS)
        by = {m["module_key"]: m for m in r.basic_modules}
        self.assertEqual(by["HERO_EMBROIDERY_PROOF"]["status"], REG.OWNER_FACT_REQUIRED)
        self.assertIn("decoration_method", by["HERO_EMBROIDERY_PROOF"]["evidence_summary"]["missing_facts"])
        # the other four, fully backed with real assets, stay READY
        for k in ("PERSONALIZATION_GALLERY", "HOW_TO_CUSTOMIZE", "FIT_SIZE_COLOR_DETAILS",
                  "CARE_PRODUCTION_FAQ"):
            self.assertEqual(by[k]["status"], REG.READY)

    def test_missing_real_photo_produces_real_photo_required(self):
        claims, facts = make(FULL_FACTS)          # facts complete, but NO assets supplied
        r = AB.build_aplus(REG.BASIC_A_PLUS, claims, facts, assets=[])
        by = {m["module_key"]: m for m in r.basic_modules}
        self.assertEqual(by["HERO_EMBROIDERY_PROOF"]["status"], REG.REAL_PHOTO_REQUIRED)
        self.assertIn("MACRO_DECORATION_STITCH",
                      by["HERO_EMBROIDERY_PROOF"]["evidence_summary"]["missing_real_assets"])

    def test_generated_asset_cannot_prove_real_claim(self):
        claims, facts = make(FULL_FACTS)
        gen = [dict(a) for a in REAL_ASSETS]
        for a in gen:
            if a["proof_purpose"] == "MACRO_DECORATION_STITCH":
                a["real_or_generated"] = "GENERATED"
        r = AB.build_aplus(REG.BASIC_A_PLUS, claims, facts, assets=gen)
        by = {m["module_key"]: m for m in r.basic_modules}
        self.assertEqual(by["HERO_EMBROIDERY_PROOF"]["status"], REG.BLOCKED_MISSING_ASSET)

    def test_no_unresolved_placeholder_in_any_ready_module(self):
        claims, facts = make(FULL_FACTS)
        r = AB.build_aplus(REG.BASIC_A_PLUS, claims, facts, assets=REAL_ASSETS)
        for m in r.basic_modules:
            if m["status"] == REG.READY:
                self.assertNotIn("{", m["body"])

    def test_claim_lineage_present_on_ready_modules(self):
        claims, facts = make(FULL_FACTS)
        r = AB.build_aplus(REG.BASIC_A_PLUS, claims, facts, assets=REAL_ASSETS)
        for m in r.basic_modules:
            if m["status"] == REG.READY and m["body"]:
                self.assertTrue(m["claim_ids"], f"{m['module_key']} READY but no claim lineage")

    def test_text_limit_blocks_ready(self):
        long_care = {**FULL_FACTS, "care_instructions": {"value": "x" * (REG.BODY_MAX + 50),
                                                         "status": "VERIFIED"}}
        claims, facts = make(long_care)
        r = AB.build_aplus(REG.BASIC_A_PLUS, claims, facts, assets=REAL_ASSETS)
        by = {m["module_key"]: m for m in r.basic_modules}
        self.assertNotEqual(by["CARE_PRODUCTION_FAQ"]["status"], REG.READY)
        self.assertTrue(any("body_exceeds" in x for x in
                            by["CARE_PRODUCTION_FAQ"]["missing_requirements"]))

    def test_alt_text_limit_flagged_in_asset_manifest(self):
        claims, facts = make(FULL_FACTS)
        assets = [dict(a) for a in REAL_ASSETS]
        for a in assets:
            if a["proof_purpose"] == "MACRO_DECORATION_STITCH":
                a["alt_text"] = "y" * (REG.ALT_TEXT_MAX + 20)
        r = AB.build_aplus(REG.BASIC_A_PLUS, claims, facts, assets=assets)
        rec = next(x for x in r.asset_manifest if x["proof_purpose"] == "MACRO_DECORATION_STITCH")
        self.assertEqual(rec["status"], REG.ASSET_ALT_TEXT_REQUIRED)
        self.assertGreater(rec["alt_text_character_count"], REG.ALT_TEXT_MAX)

    def test_faq_omits_unverified_topics(self):
        # only care verified -> CARE body carries the care topic only, no invented answers.
        claims, facts = make({**{k: FULL_FACTS[k] for k in
                                 ("decoration_method", "personalization_fields", "size_range",
                                  "color_options", "care_instructions")}})
        r = AB.build_aplus(REG.BASIC_A_PLUS, claims, facts, assets=REAL_ASSETS)
        care = next(m for m in r.basic_modules if m["module_key"] == "CARE_PRODUCTION_FAQ")
        self.assertEqual(care["status"], REG.READY)
        self.assertIn("Care:", care["body"])
        self.assertNotIn("Made in", care["body"])          # production_location was never verified


# ============================================================ Premium dynamic pool
class DynamicPool(unittest.TestCase):
    def _dyn(self, facts_dict, assets, keyword_context=None):
        claims, facts = make(facts_dict, keyword_context=keyword_context)
        r = AB.build_aplus(REG.PREMIUM_A_PLUS, claims, facts, assets=assets, premium_confirmed=True)
        return {d["module_key"]: d for d in r.dynamic_eval}, r

    def test_no_filler_selection_when_nothing_eligible(self):
        claims, facts = make({"decoration_method": {"value": "machine embroidery", "status": "VERIFIED"}})
        r = AB.build_aplus(REG.PREMIUM_A_PLUS, claims, facts, assets=[], premium_confirmed=True)
        self.assertEqual(r.selected_dynamic, [])
        self.assertFalse(r.premium_eligible)

    def test_two_selected_modules_are_distinct(self):
        claims, facts = make(FULL_FACTS)
        r = AB.build_aplus(REG.PREMIUM_A_PLUS, claims, facts, assets=REAL_ASSETS, premium_confirmed=True)
        self.assertEqual(len(r.selected_dynamic), 2)
        self.assertEqual(len(set(r.selected_dynamic)), 2)

    def test_comfort_not_inferred_from_measurements(self):
        by, _ = self._dyn({"measurements": {"value": ["chest 20in"], "status": "VERIFIED"},
                           "material": {"value": "cotton", "status": "VERIFIED"}}, REAL_ASSETS)
        self.assertFalse(by["COMFORT_AND_DAILY_USE"]["eligible"])

    def test_gift_presentation_not_inferred_without_packaging(self):
        by, _ = self._dyn({"decoration_method": {"value": "machine embroidery", "status": "VERIFIED"}},
                          REAL_ASSETS)
        self.assertFalse(by["GIFT_PRESENTATION"]["eligible"])
        by2, _ = self._dyn({"packaging": {"value": "gift box", "status": "VERIFIED"}}, REAL_ASSETS)
        self.assertTrue(by2["GIFT_PRESENTATION"]["eligible"])

    def test_brand_story_not_generated_without_brand_facts(self):
        by, _ = self._dyn({"occasion": {"value": "graduation", "status": "VERIFIED"}}, REAL_ASSETS)
        self.assertFalse(by["BRAND_STORY"]["eligible"])
        by2, _ = self._dyn({"brand": {"value": "Acme", "status": "VERIFIED"}}, REAL_ASSETS)
        self.assertTrue(by2["BRAND_STORY"]["eligible"])

    def test_design_variations_require_real_variations_and_assets(self):
        # verified color variations but NO design asset -> ineligible
        by, _ = self._dyn({"color_options": {"value": ["black", "navy"], "status": "VERIFIED"}},
                          [a for a in REAL_ASSETS if a["proof_purpose"] != "DESIGN_VARIATIONS"])
        self.assertFalse(by["DESIGN_VARIATIONS"]["eligible"])
        by2, _ = self._dyn({"color_options": {"value": ["black", "navy"], "status": "VERIFIED"}},
                           REAL_ASSETS)
        self.assertTrue(by2["DESIGN_VARIATIONS"]["eligible"])

    def test_comparison_requires_real_comparison_facts(self):
        by, _ = self._dyn(FULL_FACTS, REAL_ASSETS)
        self.assertFalse(by["PRODUCT_COMPARISON"]["eligible"])   # no comparison data in the fact schema

    def test_production_process_requires_real_process_evidence(self):
        # verified production location but no REAL process asset -> ineligible (no AI-as-proof)
        by, _ = self._dyn({"production_location": {"value": "USA", "status": "VERIFIED"}},
                          [a for a in REAL_ASSETS if a["proof_purpose"] != "PRODUCTION_PROCESS"])
        self.assertFalse(by["PRODUCTION_PROCESS"]["eligible"])
        by2, _ = self._dyn({"production_location": {"value": "USA", "status": "VERIFIED"}}, REAL_ASSETS)
        self.assertTrue(by2["PRODUCTION_PROCESS"]["eligible"])


# ============================================================ Basic fallback
class BasicFallback(unittest.TestCase):
    def test_premium_contains_complete_basic_fallback(self):
        claims, facts = make(FULL_FACTS)
        r = AB.build_aplus(REG.PREMIUM_A_PLUS, claims, facts, assets=REAL_ASSETS, premium_confirmed=True)
        fb = r.basic_fallback()
        self.assertEqual(len(fb), REG.BASIC_MODULE_COUNT)
        self.assertEqual([m["module_key"] for m in fb], list(REG.BASIC_MODULE_KEYS))

    def test_fallback_positions_are_exactly_one_to_five(self):
        claims, facts = make(FULL_FACTS)
        r = AB.build_aplus(REG.PREMIUM_A_PLUS, claims, facts, assets=REAL_ASSETS, premium_confirmed=True)
        self.assertEqual([m["position_slot"] for m in r.basic_fallback()], [1, 2, 3, 4, 5])
        # the 7-module premium journey keeps 6/7 for dynamic, but the fallback never keeps slot 6/7
        self.assertNotIn(7, [m["position_slot"] for m in r.basic_fallback()])

    def test_fallback_independently_validated(self):
        # a fallback built from an incomplete Basic still reindexes 1..5 (positions independent of status)
        claims, facts = make({"decoration_method": {"value": "machine embroidery", "status": "VERIFIED"}})
        r = AB.build_aplus(REG.UNKNOWN_A_PLUS_CAPABILITY, claims, facts, assets=[])
        self.assertEqual([m["position_slot"] for m in r.basic_fallback()], [1, 2, 3, 4, 5])


# ============================================================ determinism
class Determinism(unittest.TestCase):
    def test_identical_inputs_identical_hash(self):
        claims, facts = make(FULL_FACTS)
        h1 = AB.build_aplus(REG.PREMIUM_A_PLUS, claims, facts, assets=REAL_ASSETS,
                            premium_confirmed=True).content_sha256()
        h2 = AB.build_aplus(REG.PREMIUM_A_PLUS, claims, facts, assets=REAL_ASSETS,
                            premium_confirmed=True).content_sha256()
        self.assertEqual(h1, h2)

    def test_empty_facts_deterministic(self):
        claims, facts = make(None)
        h1 = AB.build_aplus(None, claims, facts).content_sha256()
        h2 = AB.build_aplus(None, claims, facts).content_sha256()
        self.assertEqual(h1, h2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
