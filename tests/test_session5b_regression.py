#!/usr/bin/env python3
"""Session 5B — regression guards (proof-gate tests 58, 59) that must stay green
alongside the full Session 1-5A suite.

The bulk of 51-57/60 is the whole existing test suite continuing to pass; these two
are made first-class here: T2 local generation stays deterministic, and no Amazon /
Seller Central network-write path exists anywhere in the runtime source.
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core", "listing"):
    sys.path.insert(0, os.path.join(ROOT, d))
import listing_generator as LG


def _make_project():
    d = tempfile.mkdtemp()
    records = [{"keyword_exact": p, "keyword_normalized": p.lower(), "tier": "A_CORE",
                "owner_status": "auto", "risk_flags": [], "search_volume": sv,
                "simple_competitor_coverage_pct": cov, "best_rank": 5, "median_rank": 20,
                "batch_support": {"B1": 4}, "observation_count": 1}
               for p, sv, cov in [("personalized nurse sweatshirt", 1900, 90.0),
                                  ("embroidered nurse crewneck", 820, 45.0)]]
    doc = {"run_metadata": {"run_id": "kwrun_s5b_reg", "schema_version": "1.0.0",
                            "source_schema": "MASTER_KEYWORDS_LEAN"},
           "quality_summary": {"counts": {"A_CORE": len(records)}}, "keywords": records}
    with open(os.path.join(d, "MASTER-KEYWORDS-LEAN.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f)
    return d


class T2Determinism(unittest.TestCase):
    def test_58_local_generation_deterministic(self):
        d = _make_project()
        a = LG.generate(d, "personalized nurse sweatshirt")["listing"]
        b = LG.generate(d, "personalized nurse sweatshirt")["listing"]
        for field in ("title_recommended", "title", "backend", "bullets", "description"):
            self.assertEqual(json.dumps(a.get(field), sort_keys=True),
                             json.dumps(b.get(field), sort_keys=True), field)


class NoAmazonWritePath(unittest.TestCase):
    # endpoints/clients that would constitute a live Amazon / Seller Central write path
    FORBIDDEN = ("sellingpartnerapi", "sp-api", "mws.amazonaws", "advertising-api.amazon",
                 "sellercentral.amazon", "amazon.com/ap/", "seller_central_write",
                 "boto3", "spapi")

    def test_59_no_seller_central_or_amazon_api_path(self):
        runtime_files = [os.path.join(ROOT, "dashboard", "app.py")]
        for sub in ("core", "listing"):
            folder = os.path.join(ROOT, sub)
            for fn in os.listdir(folder):
                if fn.endswith(".py"):
                    runtime_files.append(os.path.join(folder, fn))
        for path in runtime_files:
            with open(path, encoding="utf-8") as f:
                src = f.read().lower()
            for token in self.FORBIDDEN:
                self.assertNotIn(token, src, f"{token} found in {os.path.basename(path)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
