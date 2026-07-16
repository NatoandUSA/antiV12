#!/usr/bin/env python3
"""Phase 2 acceptance tests — batch portfolio optimization.

One test per phase_2_acceptance_tests entry in
AMZ_ASIN_Highest_Leverage_Phased_Master_Spec_v3.json.
"""
import os, sys, shutil, tempfile, unittest
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(ROOT, "research"), os.path.join(ROOT, "compliance")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pandas as pd
from target_profile import parse_target_profile
from asin_batches import build
from batch_optimizer import CONSTRAINTS

SEED = "personalized nurse sweatshirt"
COLS = ["ASIN", "Product Details", "Brand", "Price  $", "ASIN Sales", "ASIN Revenue", "BSR",
        "Ratings", "Review Count", "Review velocity", "Fulfillment", "Sponsored", "Seller",
        "Creation Date", "Recent Purchases"]


def row(asin, title, brand, seller=None, sales=20, revenue=600, reviews=25, price=32,
        velocity=2, bsr=5000, rating=4.6, created="2025-02-01"):
    return {"ASIN": asin, "Product Details": title, "Brand": brand, "Price  $": price,
            "ASIN Sales": sales, "ASIN Revenue": revenue, "BSR": bsr, "Ratings": rating,
            "Review Count": reviews, "Review velocity": velocity, "Fulfillment": "MFN",
            "Sponsored": None, "Seller": seller or brand, "Creation Date": created,
            "Recent Purchases": None}


def write(folder, rows):
    pd.DataFrame([{c: r.get(c) for c in COLS} for r in rows]).to_excel(
        os.path.join(folder, "fx-Helium_10_Xray_.xlsx"), index=False)


def run(folder):
    return build(folder, parse_target_profile(SEED, decoration_method="machine embroidery"), folder)[0]


def wide_pool(n=40):
    """n distinct, genuinely different personalized+embroidered nurse sweatshirts."""
    themes = ["stethoscope", "heartbeat", "caduceus", "scrub life", "night shift", "icu",
              "er", "pediatric", "oncology", "labor delivery", "flight", "school",
              "practitioner", "midwife", "hospice", "dialysis", "surgical", "trauma",
              "cardiac", "neonatal"]
    out = []
    for i in range(n):
        th = themes[i % len(themes)]
        out.append(row(f"B0W{i:07d}",
                       f"Personalized Nurse Sweatshirt Custom Embroidered {th} Name Crewneck {i}",
                       brand=f"Brand{i:02d}", seller=f"Seller{i:02d}",
                       sales=5 + i * 3, revenue=200 + i * 40, reviews=(i * 7) % 90,
                       price=28 + (i % 3) * 6))
    return out


class Phase2(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_01_brand_limit_two_per_batch(self):
        rows = wide_pool(30)
        for i in range(6):   # one brand floods the pool with high revenue
            rows.append(row(f"B0FLOOD{i:03d}",
                            f"Personalized Nurse Sweatshirt Custom Embroidered Design {i} Unique{i}",
                            brand="Migxsaf", seller="Migxsaf", sales=500, revenue=20000,
                            reviews=200 + i))
        write(self.d, rows)
        doc = run(self.d)
        self.assertTrue(doc["batches"], "expected at least one batch")
        for b in doc["batches"]:
            c = Counter(x["brand"] for x in b["asins"] if x["brand"])
            self.assertLessEqual(max(c.values()), CONSTRAINTS["max_same_brand_per_batch"],
                                 f"{b['batch_id']} exceeds brand limit: {c.most_common(2)}")

    def test_02_seller_limit_two_per_batch(self):
        rows = wide_pool(30)
        for i in range(6):
            rows.append(row(f"B0SELL{i:04d}",
                            f"Personalized Nurse Sweatshirt Custom Embroidered Variant {i} Zeta{i}",
                            brand=f"Alias{i}", seller="OneSeller", sales=300, revenue=9000))
        write(self.d, rows)
        doc = run(self.d)
        for b in doc["batches"]:
            c = Counter(x["seller"] for x in b["asins"] if x["seller"])
            self.assertLessEqual(max(c.values()), CONSTRAINTS["max_same_seller_per_batch"],
                                 f"{b['batch_id']} exceeds seller limit: {c.most_common(2)}")

    def test_03_no_two_from_same_title_cluster(self):
        rows = wide_pool(30)
        rows += [row("B0CLONEA01", "Nurse Graphic Crewneck Personalized Embroidered Floral Black",
                     "CloneCo", sales=400, revenue=12000, reviews=50),
                 row("B0CLONEA02", "Nurse Graphic Crewneck Personalized Embroidered Floral Pink",
                     "CloneCo", sales=390, revenue=11800, reviews=50)]
        write(self.d, rows)
        doc = run(self.d)
        for b in doc["batches"]:
            c = Counter(x["cluster"] for x in b["asins"])
            self.assertLessEqual(max(c.values()), CONSTRAINTS["max_same_title_cluster_per_batch"],
                                 f"{b['batch_id']} contains a title clone pair")

    def test_04_no_two_from_same_variation_family(self):
        rows = wide_pool(30)
        rows += [row(f"B0VARF{i:04d}",
                     "Personalized Nurse Sweatshirt Custom Embroidered Stethoscope Name",
                     "FamCo", sales=300, revenue=9000, reviews=44, bsr=1234)
                 for i in range(3)]
        write(self.d, rows)
        doc = run(self.d)
        for b in doc["batches"]:
            c = Counter(x["variation_family"] for x in b["asins"])
            self.assertLessEqual(max(c.values()), CONSTRAINTS["max_same_variation_family_per_batch"],
                                 f"{b['batch_id']} contains two of one variation family")

    def test_05_no_asin_repeated_across_batches(self):
        write(self.d, wide_pool(40))
        doc = run(self.d)
        all_asins = [x["asin"] for b in doc["batches"] for x in b["asins"]]
        self.assertEqual(len(all_asins), len(set(all_asins)), "an ASIN appears in two batches")
        self.assertTrue(doc["constraint_validation"]["no_duplicate_asin_across_batches"])

    def test_06_b1_and_b2_have_distinct_composition(self):
        write(self.d, wide_pool(40))
        doc = run(self.d)
        ids = [b["batch_id"] for b in doc["batches"]]
        self.assertIn("B1_CORE_MONEY", ids)
        self.assertIn("B2_CORE_OPPORTUNITY", ids)
        b1 = next(b for b in doc["batches"] if b["batch_id"] == "B1_CORE_MONEY")
        b2 = next(b for b in doc["batches"] if b["batch_id"] == "B2_CORE_OPPORTUNITY")
        self.assertFalse(set(b1["paste_ready_asins"]) & set(b2["paste_ready_asins"]),
                         "batches must not share ASINs")
        self.assertNotEqual(b1["purpose"], b2["purpose"])
        # Each batch clears its OWN relevance floor (spec: B1 >= 82, B2 >= 76).
        self.assertGreaterEqual(b1["constraint_results"]["min_relevance"], 82)
        self.assertGreaterEqual(b2["constraint_results"]["min_relevance"], 76)
        # Strategic distinction is composition, not relevance order: B1 is the money
        # batch (5 proven target), B2 the opportunity batch (3 proven / 5 emerging).
        p1 = sum(1 for x in b1["asins"] if x["market_bucket"] == "proven")
        p2 = sum(1 for x in b2["asins"] if x["market_bucket"] == "proven")
        self.assertGreaterEqual(p1, p2, "B1 must lean more proven than B2")
        self.assertGreaterEqual(b1["constraint_results"]["median_relevance"], 86,
                                "B1 target median relevance per spec")

    def test_07_no_batch3_without_coherent_cluster(self):
        write(self.d, wide_pool(24))
        doc = run(self.d)
        self.assertLessEqual(len(doc["batches"]), CONSTRAINTS["max_batches"])
        if len(doc["batches"]) < 3:
            self.assertTrue(any("B3 not created" in w for w in doc["warnings"]),
                            "declining B3 must be explained")

    def test_08_short_batch_is_not_padded(self):
        rows = [row(f"B0SHORT{i:03d}",
                    f"Personalized Nurse Sweatshirt Custom Embroidered Theme{i} Name Crew",
                    brand=f"S{i}", seller=f"S{i}", sales=10 + i, revenue=300 + i * 10,
                    reviews=i * 5) for i in range(7)]
        # plus clearly off-target rows that must never be used as filler
        rows += [row(f"B0OFFT{i:04d}", f"Teacher Coffee Mug Ceramic {i}", brand=f"T{i}",
                     sales=900, revenue=30000, reviews=500) for i in range(10)]
        write(self.d, rows)
        doc = run(self.d)
        b1 = doc["batches"][0]
        self.assertLessEqual(len(b1["asins"]), 7, "must not pad past the qualified pool")
        for x in b1["asins"]:
            self.assertNotIn("Mug", x["title"], "off-target filler must never enter a batch")
        self.assertTrue(doc["summary"]["short_batch_warning"])
        self.assertTrue(any("SHORT_BATCH" in w for w in doc["warnings"]),
                        "a short batch must state why")

    def test_09_deterministic_batches(self):
        write(self.d, wide_pool(40))
        a, b = run(self.d), run(self.d)
        self.assertEqual([x["paste_ready_asins"] for x in a["batches"]],
                         [x["paste_ready_asins"] for x in b["batches"]],
                         "same inputs must produce identical batch membership AND order")
        self.assertEqual([x["batch_quality_score"] for x in a["batches"]],
                         [x["batch_quality_score"] for x in b["batches"]])

    def test_10_constraint_validation_reports_truthfully(self):
        write(self.d, wide_pool(40))
        doc = run(self.d)
        v = doc["constraint_validation"]
        self.assertTrue(v["all_constraints_pass"])
        self.assertTrue(v["batch_count_ok"])
        self.assertTrue(v["brand_all_batches_ok"])
        for r in v["per_batch"]:
            self.assertTrue(r["brand_limit_ok"] and r["seller_limit_ok"])
            self.assertTrue(r["title_cluster_ok"] and r["variation_family_ok"])
            self.assertTrue(r["min_relevance_ok"])

    def test_11_output_schema_is_complete(self):
        write(self.d, wide_pool(40))
        doc = run(self.d)
        for k in ("schema_version", "run_metadata", "target_profile", "scoring_weights",
                  "constraints", "summary", "batches", "constraint_validation",
                  "unselected_top_candidates", "rejected_candidates", "warnings",
                  "next_manual_action"):
            self.assertIn(k, doc)
        for b in doc["batches"]:
            self.assertTrue(b["paste_ready_asins"])
            self.assertIn(b["quality_label"],
                          ("EXCELLENT", "STRONG", "USABLE_WITH_REVIEW", "REBUILD_BATCH"))
            for x in b["asins"]:
                self.assertTrue(x["why_selected"])
                self.assertIn("relevance", x["component_scores"])
        self.assertTrue(doc["unselected_top_candidates"], "a replacement queue must exist")

    def test_12_generated_outputs_are_not_reingested(self):
        """Our own ASIN-CANDIDATES.csv must never load as a product Xray."""
        write(self.d, wide_pool(20))
        run(self.d)                      # writes ASIN-CANDIDATES.csv into the folder
        from asin_candidates import write_outputs, build_candidates
        res = build_candidates(self.d, parse_target_profile(SEED))
        write_outputs(res, self.d)
        doc = run(self.d)                # second pass sees its own outputs
        types = {f["file"]: f["detected_type"] for f in doc["run_metadata"]["source_files"]}
        for name in ("ASIN-CANDIDATES.csv", "ASIN-CANDIDATES.json", "ASIN-BATCHES.json"):
            if name in types:
                self.assertNotEqual(types[name], "product_xray",
                                    f"{name} must not be re-ingested as input")
        self.assertEqual(doc["summary"]["rows_loaded"], 20,
                         "row count must not inflate from our own outputs")


if __name__ == "__main__":
    unittest.main()
