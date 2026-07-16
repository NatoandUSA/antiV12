#!/usr/bin/env python3
"""Session 2 integration — the listing generator consumes normalized product facts and the shared
category policy, without regressing the Session 1 authoritative keyword source.

Missing facts must NOT crash generation and must NOT create fabricated copy (no cotton-blend, no
10-14 business-day promise, no invented personalization value, no invented US-shipping claim). The
generator, validator, and dashboard must resolve the same category policy; the listing must keep the
Session 1 keyword-source SHA-256 and the dashboard must report the same hash.
"""
import json, os, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("listing", "research", "dashboard", "compliance"):
    p = os.path.join(ROOT, d)
    if p not in sys.path:
        sys.path.insert(0, p)

import pandas as pd
import keyword_intelligence as KI
import competitor_gap_analyzer as CG
import keyword_source_adapter as KSA
import product_fact_loader as PFL
import category_policy_registry as CPR
import listing_generator as LG
import listing_validate as LV
import app as DASH

SEED = "personalized nurse sweatshirt"


def base_project():
    d = tempfile.mkdtemp()
    pd.DataFrame([
        {"Keyword Phrase": SEED, "Search Volume": 1900, "Competing Products": 1200, "Title Density": 3},
        {"Keyword Phrase": "custom nurse gift", "Search Volume": 1400, "Competing Products": 600, "Title Density": 2},
    ]).to_csv(os.path.join(d, "cerebro_nurse.csv"), index=False)
    pd.DataFrame([{"ASIN": "B01", "Title": "Nurse Sweatshirt", "Price": 29.99,
                   "Review Count": 40, "Images": 3}]).to_excel(os.path.join(d, "xray_nurse.xlsx"), index=False)
    KI.run(d); CG.run(d)
    recs = [{"keyword_exact": p, "keyword_normalized": p.lower(), "tier": "A_CORE",
             "owner_status": "auto", "reason_codes": [], "risk_flags": [], "search_volume": sv,
             "simple_competitor_coverage_pct": cov, "best_rank": 5, "median_rank": 20,
             "batch_support": {"B1": 6}, "observation_count": 1}
            for p, sv, cov in [(SEED, 1900, 90.0), ("custom nurse gift", 1400, 60.0)]]
    json.dump({"run_metadata": {"run_id": "kwrun_s2", "schema_version": "1.0.0"},
               "target_profile": {"seed": SEED}, "quality_summary": {"counts": {"A_CORE": 2}},
               "downstream_policy": {"mode": "transition"}, "keywords": recs},
              open(os.path.join(d, "MASTER-KEYWORDS-LEAN.json"), "w"))
    return d


def write_facts(folder, product):
    json.dump({"schema_version": "1.0.0", "source": "owner", "product": product},
              open(os.path.join(folder, "product-facts.json"), "w"))


def publishable_copy(listing):
    return " ".join([listing["title"], listing["description"]]
                    + [str(b) for b in listing["bullets"]]
                    + [m["copy"] for m in listing["aplus"]]).lower()


class NoFabricatedDefaults(unittest.TestCase):
    def setUp(self):
        self.d = base_project()                              # NO product-facts.json -> facts UNKNOWN

    def test_missing_facts_do_not_crash_generation(self):
        r = LG.generate(self.d)
        self.assertTrue(r["ok"], r.get("reason"))

    def test_no_cotton_blend_default_appears(self):
        copy = publishable_copy(LG.generate(self.d)["listing"])
        self.assertNotIn("cotton-blend", copy)
        self.assertNotIn("cotton blend", copy)

    def test_no_10_14_business_day_default_appears(self):
        copy = publishable_copy(LG.generate(self.d)["listing"])
        self.assertNotIn("10-14 business days", copy)
        self.assertNotIn("10–14 business days", copy)

    def test_no_invented_personalization_default_appears(self):
        copy = publishable_copy(LG.generate(self.d)["listing"])
        self.assertNotIn("any name and credentials", copy)

    def test_no_invented_us_shipping_claim_appears(self):
        copy = publishable_copy(LG.generate(self.d)["listing"])
        self.assertNotIn("shipped from the us", copy)

    def test_no_brace_placeholder_in_publishable_aplus(self):
        for m in LG.generate(self.d)["listing"]["aplus"]:
            self.assertNotIn("{", m["copy"])
            self.assertNotIn("}", m["copy"])

    def test_missing_facts_are_reported_as_owner_fact_required(self):
        r = LG.generate(self.d)
        for fld in ("material", "production_time_range", "personalization_fields"):
            self.assertIn(fld, r["owner_fact_required"])
        self.assertIn("material", r["listing"]["owner_fact_required"])

    def test_generated_listing_still_validates(self):
        r = LG.generate(self.d)
        ok, errs = DASH.validate_listing(r["listing"])
        self.assertTrue(ok, errs)


class VerifiedFactsFlowThrough(unittest.TestCase):
    def setUp(self):
        self.d = base_project()

    def test_verified_material_reaches_copy(self):
        write_facts(self.d, {"material": {"value": "80% cotton 20% polyester", "status": "VERIFIED"}})
        copy = publishable_copy(LG.generate(self.d)["listing"])
        self.assertIn("80% cotton 20% polyester", copy)

    def test_owner_review_material_stays_out_of_copy(self):
        write_facts(self.d, {"material": {"value": "mystery fabric", "status": "OWNER_REVIEW_REQUIRED"}})
        r = LG.generate(self.d)
        self.assertNotIn("mystery fabric", publishable_copy(r["listing"]))
        self.assertIn("material", r["owner_fact_required"])

    def test_verified_us_shipping_enables_the_claim(self):
        write_facts(self.d, {"shipping_method": {"value": "Ships from the US with tracking",
                                                 "status": "VERIFIED"}})
        copy = publishable_copy(LG.generate(self.d)["listing"])
        self.assertIn("shipped from the us", copy)

    def test_listing_records_product_fact_source_hash(self):
        write_facts(self.d, {"brand": {"value": "Acme", "status": "VERIFIED"}})
        r = LG.generate(self.d)
        facts = PFL.load_product_facts(folder=self.d)
        self.assertEqual(r["listing"]["product_fact_source_sha256"], facts.source_sha256)


class Session1KeywordSourcePreserved(unittest.TestCase):
    def setUp(self):
        self.d = base_project()

    def test_listing_still_uses_the_authoritative_lean_source(self):
        L = LG.generate(self.d)["listing"]
        self.assertEqual(L["keyword_source_file"], "MASTER-KEYWORDS-LEAN.json")
        self.assertEqual(L["keyword_source_schema"], KSA.SCHEMA_LEAN)
        self.assertFalse(L["legacy_unsafe"])
        self.assertEqual(L["selected_keywords"]["primary"], [SEED])

    def test_listing_and_dashboard_keyword_hash_still_match(self):
        L = LG.generate(self.d)["listing"]
        s = DASH.keyword_source_summary(self.d)
        self.assertEqual(L["keyword_source_sha256"], s["sha256"])
        self.assertEqual(L["keyword_source_file"], s["file"])

    def test_adding_product_facts_does_not_change_the_keyword_hash(self):
        before = LG.generate(self.d)["listing"]["keyword_source_sha256"]
        write_facts(self.d, {"material": {"value": "cotton", "status": "VERIFIED"}})
        after = LG.generate(self.d)["listing"]["keyword_source_sha256"]
        self.assertEqual(before, after)


class ValidatorDashboardSharePolicy(unittest.TestCase):
    def test_validator_and_dashboard_use_the_same_category_policy(self):
        d = base_project()
        listing = {"category": "apparel"}
        val_limit = LV.resolve_listing_policy(listing).title_hard_limit
        dash_limit = DASH.snapshot(d)["category_policy"]["title_hard_limit"]
        self.assertEqual(val_limit, dash_limit)
        self.assertEqual(val_limit, CPR.get_title_hard_limit("apparel"))

    def test_dashboard_snapshot_surfaces_product_fact_summary(self):
        d = base_project()
        write_facts(d, {"brand": {"value": "Acme", "status": "VERIFIED"}})
        pf = DASH.snapshot(d)["product_facts"]
        self.assertTrue(pf["present"])
        self.assertEqual(pf["verified"], 1)
        self.assertIn("material", pf["owner_fact_required"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
