#!/usr/bin/env python3
"""Phase 1 acceptance tests — AMZ_ASIN_Highest_Leverage_Phased_Master_Spec_v3.

One test per phase_1_acceptance_tests entry, plus the TEST-001/003/004/007
scenarios from the scoring spec. Fixtures are synthetic so the expected
behaviour is unambiguous.
"""
import os, sys, json, shutil, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(ROOT, "research"), os.path.join(ROOT, "compliance")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pandas as pd
from target_profile import parse_target_profile
from asin_features import (load_product_rows, normalize_candidates, classify_activity,
                           apply_hard_gates, LEGACY_ONLY, ACTIVE_CONFIRMED)
from asin_clusters import annotate_clusters
from asin_scoring import score_candidates
from asin_candidates import build_candidates
from export_detector import detect_export_type, PRODUCT_XRAY

SEED = "personalized nurse sweatshirt"


def xray(rows):
    """Minimal but realistic product-Xray frame."""
    base = {"ASIN": [], "Product Details": [], "Brand": [], "Price  $": [], "ASIN Sales": [],
            "ASIN Revenue": [], "BSR": [], "Ratings": [], "Review Count": [],
            "Review velocity": [], "Fulfillment": [], "Sponsored": [], "Seller": [],
            "Creation Date": [], "Recent Purchases": []}
    for r in rows:
        for k in base:
            base[k].append(r.get(k))
    return pd.DataFrame(base)


def write_xray(folder, rows, name="test-Helium_10_Xray_.xlsx"):
    p = os.path.join(folder, name)
    xray(rows).to_excel(p, index=False)
    return p


def profile():
    return parse_target_profile(SEED, decoration_method="machine embroidery")


def run(folder):
    return build_candidates(folder, profile())


class Phase1(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    # ---- TEST-001 / acceptance #1
    def test_01_exact_personalized_outranks_generic_high_revenue(self):
        write_xray(self.d, [
            {"ASIN": "B0GENERIC1", "Product Details": "Leopard Nurse Sweatshirt RN Crewneck Graphic Print",
             "Brand": "BigCo", "Price  $": 26, "ASIN Sales": 900, "ASIN Revenue": 30000,
             "BSR": 4000, "Ratings": 4.6, "Review Count": 800, "Review velocity": 20,
             "Fulfillment": "FBA", "Seller": "BigCo", "Creation Date": "2023-01-01"},
            {"ASIN": "B0EXACT001", "Product Details": "Personalized Nurse Sweatshirt Custom Embroidered Name Crewneck",
             "Brand": "SmallCo", "Price  $": 34, "ASIN Sales": 40, "ASIN Revenue": 1400,
             "BSR": 90000, "Ratings": 4.8, "Review Count": 30, "Review velocity": 3,
             "Fulfillment": "MFN", "Seller": "SmallCo", "Creation Date": "2025-06-01"},
        ])
        res = run(self.d)
        order = [c["asin"] for c in res["candidates"]]
        self.assertIn("B0EXACT001", order)
        self.assertIn("B0GENERIC1", order)
        self.assertLess(order.index("B0EXACT001"), order.index("B0GENERIC1"),
                        "exact personalized+embroidered must outrank generic high-revenue print")

    # ---- TEST-004 / acceptance #2
    def test_02_reviews_without_activity_are_legacy_only(self):
        c = {"asin": "B0LEGACY01", "title": "Personalized Nurse Sweatshirt Embroidered",
             "reviews": 900, "asin_sales": None, "revenue": None, "recent_purchases": None,
             "review_velocity": 0, "bsr": None, "_age_days": 2000, "sponsored": False}
        state, sig = classify_activity(c)
        self.assertEqual(state, LEGACY_ONLY, "900 reviews + no current signal must be LEGACY_ONLY")

    def test_02b_legacy_only_is_not_core_eligible(self):
        write_xray(self.d, [
            {"ASIN": "B0LEGACY01", "Product Details": "Personalized Nurse Sweatshirt Custom Embroidered",
             "Brand": "OldCo", "Price  $": 30, "ASIN Sales": None, "ASIN Revenue": None,
             "BSR": None, "Ratings": 4.1, "Review Count": 900, "Review velocity": 0,
             "Fulfillment": "MFN", "Seller": "OldCo", "Creation Date": "2019-01-01"},
        ])
        res = run(self.d)
        got = [c for c in res["candidates"] if c["asin"] == "B0LEGACY01"]
        self.assertTrue(got, "LEGACY_ONLY stays visible for manual review")
        self.assertFalse(got[0]["core_eligible"], "LEGACY_ONLY must not be core-batch eligible")

    # ---- TEST-003 / acceptance #3
    def test_03_cerebro_export_never_loaded_as_product_data(self):
        pd.DataFrame({"Keyword Phrase": ["nurse sweatshirt", "custom nurse gift"],
                      "Search Volume": [5000, 1200], "Title Density": [10, 4],
                      "Competing Products": [900, 120]}).to_excel(
            os.path.join(self.d, "cerebro_export.xlsx"), index=False)
        write_xray(self.d, [
            {"ASIN": "B0REAL0001", "Product Details": "Personalized Nurse Sweatshirt Embroidered Custom",
             "Brand": "Co", "Price  $": 30, "ASIN Sales": 10, "ASIN Revenue": 300,
             "BSR": 50000, "Ratings": 4.5, "Review Count": 5, "Review velocity": 1,
             "Fulfillment": "MFN", "Seller": "Co", "Creation Date": "2025-01-01"},
        ])
        t, _, _ = detect_export_type(os.path.join(self.d, "cerebro_export.xlsx"))
        self.assertNotEqual(t, PRODUCT_XRAY, "a Cerebro export must never classify as product Xray")
        res = run(self.d)
        loaded = {f["file"]: f["detected_type"] for f in res["source_files"]}
        self.assertNotEqual(loaded["cerebro_export.xlsx"], PRODUCT_XRAY)
        self.assertEqual(res["summary"]["unique_valid_asins"], 1,
                         "keyword rows must not become product candidates")

    # ---- acceptance #4
    def test_04_malformed_and_nan_asins_rejected(self):
        write_xray(self.d, [
            {"ASIN": "nan", "Product Details": "Personalized Nurse Sweatshirt Embroidered",
             "Brand": "X", "ASIN Sales": 5, "Review Count": 1},
            {"ASIN": "", "Product Details": "Personalized Nurse Sweatshirt Embroidered",
             "Brand": "X", "ASIN Sales": 5, "Review Count": 1},
            {"ASIN": "NOTANASIN", "Product Details": "Personalized Nurse Sweatshirt Embroidered",
             "Brand": "X", "ASIN Sales": 5, "Review Count": 1},
            {"ASIN": "B0VALID001", "Product Details": "Personalized Nurse Sweatshirt Custom Embroidered",
             "Brand": "X", "Price  $": 30, "ASIN Sales": 5, "ASIN Revenue": 150, "BSR": 1000,
             "Ratings": 4.5, "Review Count": 2, "Review velocity": 1, "Fulfillment": "MFN",
             "Seller": "X", "Creation Date": "2025-02-02"},
        ])
        res = run(self.d)
        self.assertEqual(res["summary"]["unique_valid_asins"], 1)
        codes = {r["reason_code"] for r in res["rejected_candidates"]}
        self.assertIn("INVALID_ASIN", codes)
        for c in res["candidates"]:
            self.assertRegex(c["asin"], r"^B0[A-Z0-9]{8}$")

    # ---- acceptance #5 / TEST-007
    def test_05_deterministic_across_runs(self):
        write_xray(self.d, [
            {"ASIN": f"B0DET{i:05d}", "Product Details": f"Personalized Nurse Sweatshirt Embroidered Custom {i}",
             "Brand": f"B{i%3}", "Price  $": 30 + i, "ASIN Sales": 10 * i, "ASIN Revenue": 100 * i,
             "BSR": 1000 * i, "Ratings": 4.0, "Review Count": i, "Review velocity": 1,
             "Fulfillment": "MFN", "Seller": f"S{i%3}", "Creation Date": "2025-01-01"}
            for i in range(1, 9)
        ])
        a, b = run(self.d), run(self.d)
        self.assertEqual([c["asin"] for c in a["candidates"]], [c["asin"] for c in b["candidates"]])
        self.assertEqual([c["predicted_keyword_value_score"] for c in a["candidates"]],
                         [c["predicted_keyword_value_score"] for c in b["candidates"]])
        self.assertEqual([c["title_cluster"] for c in a["candidates"]],
                         [c["title_cluster"] for c in b["candidates"]])

    # ---- acceptance #6
    def test_06_every_candidate_and_rejection_has_reason_codes(self):
        write_xray(self.d, [
            {"ASIN": "B0KEEP0001", "Product Details": "Personalized Nurse Sweatshirt Custom Embroidered",
             "Brand": "A", "Price  $": 30, "ASIN Sales": 9, "ASIN Revenue": 270, "BSR": 900,
             "Ratings": 4.5, "Review Count": 3, "Review velocity": 1, "Fulfillment": "MFN",
             "Seller": "A", "Creation Date": "2025-03-01"},
            {"ASIN": "B0WRONG001", "Product Details": "Teacher Coffee Mug Ceramic",
             "Brand": "B", "Price  $": 12, "ASIN Sales": 50, "ASIN Revenue": 600, "BSR": 500,
             "Ratings": 4.2, "Review Count": 10, "Review velocity": 2, "Fulfillment": "FBA",
             "Seller": "B", "Creation Date": "2024-01-01"},
        ])
        res = run(self.d)
        for c in res["candidates"]:
            self.assertTrue(c["reason_codes"], f"{c['asin']} has no reason codes")
            self.assertIn("relevance", c["component_scores"])
            self.assertIsInstance(c["penalties"], list)
        self.assertTrue(res["rejected_candidates"], "the mug must be rejected")
        for r in res["rejected_candidates"]:
            self.assertTrue(r.get("reason_code") and r.get("reason"))

    # ---- acceptance #7 / TEST-007
    def test_07_missing_data_lowers_confidence_not_performance(self):
        write_xray(self.d, [
            {"ASIN": "B0FULL0001", "Product Details": "Personalized Nurse Sweatshirt Custom Embroidered Name",
             "Brand": "A", "Price  $": 30, "ASIN Sales": 20, "ASIN Revenue": 600, "BSR": 900,
             "Ratings": 4.5, "Review Count": 10, "Review velocity": 2, "Fulfillment": "MFN",
             "Seller": "A", "Creation Date": "2025-03-01"},
            {"ASIN": "B0SPARSE01", "Product Details": "Personalized Nurse Sweatshirt Custom Embroidered Name",
             "Brand": "A", "ASIN Sales": 20, "ASIN Revenue": 600},
        ])
        res = run(self.d)
        m = {c["asin"]: c for c in res["candidates"]}
        self.assertIn("B0SPARSE01", m, "a sparse row must survive, not be zeroed out")
        full, sparse = m["B0FULL0001"], m["B0SPARSE01"]
        self.assertLess(sparse["component_scores"]["data_confidence"],
                        full["component_scores"]["data_confidence"],
                        "missing fields must reduce data_confidence")
        self.assertGreater(sparse["component_scores"]["relevance"], 50,
                           "missing market data must not destroy a real relevance match")
        self.assertGreater(sparse["predicted_keyword_value_score"], 0)

    # ---- clustering data required by Phase 2
    def test_08_variation_family_detected_for_title_clones(self):
        write_xray(self.d, [
            {"ASIN": "B0CLONE001", "Product Details": "Nurse Graphic Crewneck Sweatshirt Personalized Embroidered Floral Black",
             "Brand": "SameCo", "Price  $": 30, "ASIN Sales": 10, "ASIN Revenue": 300, "BSR": 900,
             "Ratings": 4.5, "Review Count": 12, "Review velocity": 1, "Fulfillment": "MFN",
             "Seller": "SameCo", "Creation Date": "2025-01-01"},
            {"ASIN": "B0CLONE002", "Product Details": "Nurse Graphic Crewneck Sweatshirt Personalized Embroidered Floral Pink",
             "Brand": "SameCo", "Price  $": 30, "ASIN Sales": 9, "ASIN Revenue": 280, "BSR": 900,
             "Ratings": 4.5, "Review Count": 12, "Review velocity": 1, "Fulfillment": "MFN",
             "Seller": "SameCo", "Creation Date": "2025-01-01"},
        ])
        res = run(self.d)
        m = {c["asin"]: c for c in res["candidates"]}
        self.assertEqual(m["B0CLONE001"]["variation_family"], m["B0CLONE002"]["variation_family"],
                         "colour children of one parent must share a variation family")
        self.assertEqual(m["B0CLONE001"]["title_cluster"], m["B0CLONE002"]["title_cluster"])
        self.assertTrue(any("near-identical title" in p["reason"] for p in m["B0CLONE001"]["penalties"]))

    # ---- outputs
    def test_09_outputs_are_valid_and_complete(self):
        write_xray(self.d, [
            {"ASIN": "B0OUT00001", "Product Details": "Personalized Nurse Sweatshirt Custom Embroidered",
             "Brand": "A", "Price  $": 30, "ASIN Sales": 9, "ASIN Revenue": 270, "BSR": 900,
             "Ratings": 4.5, "Review Count": 3, "Review velocity": 1, "Fulfillment": "MFN",
             "Seller": "A", "Creation Date": "2025-03-01"},
        ])
        from asin_candidates import write_outputs
        res = run(self.d)
        jp, cp = write_outputs(res, self.d)
        self.assertTrue(os.path.exists(jp) and os.path.exists(cp))
        data = json.load(open(jp, encoding="utf-8"))
        for k in ("schema_version", "target_profile", "source_files", "summary",
                  "candidates", "rejected_candidates", "scoring_weights"):
            self.assertIn(k, data)
        self.assertAlmostEqual(sum(data["scoring_weights"].values()), 1.0, places=6)
        self.assertEqual(data["scoring_weights"]["relevance"], 0.45)


if __name__ == "__main__":
    unittest.main()
