#!/usr/bin/env python3
"""Tests for listing_generator v2 + a_plus_templates (v2.4.0 enhancement)."""
import os, sys, json, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("listing", "research", "dashboard"):
    sys.path.insert(0, os.path.join(ROOT, d))
import pandas as pd
import keyword_intelligence as KI
import competitor_gap_analyzer as CG
import listing_generator as LG
import a_plus_templates as APT
import app as DASH


def project():
    d = tempfile.mkdtemp()
    pd.DataFrame([
        {"Keyword Phrase": "custom nurse gift", "Search Volume": 1400, "Competing Products": 600, "Title Density": 2},
        {"Keyword Phrase": "personalized nurse sweatshirt", "Search Volume": 1900, "Competing Products": 1200, "Title Density": 3},
        {"Keyword Phrase": "embroidered nurse crewneck", "Search Volume": 820, "Competing Products": 300, "Title Density": 1},
    ]).to_csv(os.path.join(d, "cerebro_nurse.csv"), index=False)
    pd.DataFrame([
        {"ASIN": "B01", "Title": "Nurse Sweatshirt Printed", "Price": 29.99, "Review Count": 40, "Images": 3},
        {"ASIN": "B02", "Title": "Cute Nurse Shirt Print", "Price": 24.99, "Review Count": 15, "Images": 4},
    ]).to_excel(os.path.join(d, "xray_nurse.xlsx"), index=False)
    KI.run(d); CG.run(d)
    return d


class Generator(unittest.TestCase):
    def test_generates_and_is_schema_valid(self):
        d = project()
        r = LG.generate(d, "custom nurse sweatshirt")
        self.assertTrue(r["ok"])
        ok, errs = DASH.validate_listing(r["listing"])
        self.assertTrue(ok, errs)

    def test_title_within_75(self):
        d = project()
        r = LG.generate(d, "custom nurse sweatshirt")
        self.assertLessEqual(len(r["listing"]["title"]), 75)

    def test_title_has_no_promo_or_symbols(self):
        d = project()
        t = LG.generate(d, "custom nurse sweatshirt")["listing"]["title"].lower()
        for bad in ("best", "cheap", "sale", "guarantee", "#1", "premium"):
            self.assertNotIn(bad, t)
        self.assertNotIn("&", t)

    def test_item_highlights_within_125(self):
        d = project()
        ih = LG.generate(d, "custom nurse sweatshirt")["listing"]["item_highlights"]
        self.assertLessEqual(len(ih), 125)

    def test_embroidery_module_requires_proof(self):
        d = project()
        r = LG.generate(d, "custom nurse sweatshirt")
        self.assertIn("See the Real Stitching", r["proof_required"])

    def test_writes_brief_files(self):
        d = project()
        LG.generate(d, "custom nurse sweatshirt")
        self.assertTrue(os.path.exists(os.path.join(d, "LISTING-BRIEF.json")))
        self.assertTrue(os.path.exists(os.path.join(d, "LISTING-BRIEF.md")))

    def test_no_keywords_is_graceful(self):
        d = tempfile.mkdtemp()
        r = LG.generate(d, "")
        self.assertFalse(r["ok"])

    def test_backend_within_249_bytes(self):
        d = project()
        bk = LG.generate(d, "custom nurse sweatshirt")["listing"]["backend"]
        self.assertLessEqual(len(bk.encode("utf-8")), 249)


class Templates(unittest.TestCase):
    def test_select_prioritizes_high_gap(self):
        mods = APT.select_modules([{"area": "Image coverage", "score": 90}])
        keys = [m["key"] for m in mods]
        self.assertEqual(keys[:2], ["embroidery_proof", "personalization"])  # core always first

    def test_render_fills_facts(self):
        m = APT.render(APT.BY_KEY["personalization"], {"personalization": "any name"})
        self.assertIn("any name", m["copy"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
