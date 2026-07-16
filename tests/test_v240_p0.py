#!/usr/bin/env python3
"""v2.4.0-RC1 P0 tests: 75-char title + Item Highlights, transparent scoring, evidence-classed
gaps, strict listing schema, honesty. From the Updated Feedback audit."""
import os, sys, json, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("research", "compliance", "dashboard"):
    sys.path.insert(0, os.path.join(ROOT, d))
import pandas as pd
import keyword_intelligence as KI
import competitor_gap_analyzer as CG


class TitleRule(unittest.TestCase):
    def _validate(self, listing):
        import subprocess
        d = tempfile.mkdtemp()
        json.dump(listing, open(os.path.join(d, "listing.json"), "w"))
        p = subprocess.run([sys.executable, os.path.join(ROOT, "compliance", "listing_validate.py"),
                            os.path.join(d, "listing.json")], capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return p.returncode, p.stdout + p.stderr

    def test_76_char_title_fails(self):
        t = "P" * 76
        rc, out = self._validate({"category": "apparel", "title": t,
                                  "bullets": ["a", "b", "c", "d", "e"],
                                  "backend": "nurse embroidered personalized custom gift crewneck name women rn"})
        self.assertNotEqual(rc, 0)
        self.assertIn("> 75", out)

    def test_75_char_title_passes_length(self):
        t = "Personalized Nurse Sweatshirt Embroidered Crewneck Name Gift Women RN LP"  # 71
        self.assertLessEqual(len(t), 75)
        rc, out = self._validate({"category": "apparel", "title": t,
                                  "bullets": ["a", "b", "c", "d", "e"],
                                  "backend": "nurse embroidered personalized custom gift crewneck name women rn icu"})
        self.assertNotIn("> 75", out)

    def test_item_highlights_over_125_fails(self):
        rc, out = self._validate({"category": "apparel", "title": "Nurse Sweatshirt Custom Name",
                                  "item_highlights": "x" * 126,
                                  "bullets": ["a", "b", "c", "d", "e"],
                                  "backend": "nurse embroidered personalized custom gift crewneck name women rn icu"})
        self.assertIn("125", out)


class TransparentScoring(unittest.TestCase):
    def _fixture(self):
        d = tempfile.mkdtemp()
        pd.DataFrame([
            {"Keyword Phrase": "personalized nurse sweatshirt", "Search Volume": 1900, "Competing Products": 1200, "Title Density": 3},
            {"Keyword Phrase": "custom nurse gift", "Search Volume": 1400, "Competing Products": 600, "Title Density": 2},
        ]).to_csv(os.path.join(d, "cerebro_nurse.csv"), index=False)
        return d

    def test_separate_scores_present(self):
        d = self._fixture()
        r = KI.run(d)["top"][0]
        self.assertIn("amazon_opportunity", r)
        self.assertIn("trend_momentum", r)
        self.assertIn("confidence", r)
        self.assertIn("reason_codes", r)

    def test_missing_ytrends_no_penalty(self):
        d = self._fixture()
        r = {x["keyword"]: x for x in KI.run(d)["top"]}
        k = r["custom nurse gift"]
        self.assertIsNone(k["trend_momentum"])            # no YTrends -> None, not 0
        self.assertEqual(k["combined_internal_score"], k["amazon_opportunity"])  # not dragged down

    def test_external_weight_capped(self):
        self.assertLessEqual(KI.EXTERNAL_WEIGHT, 0.25)

    def test_deterministic(self):
        d = self._fixture()
        a = json.dumps(KI.run(d)["all"], sort_keys=True)
        b = json.dumps(KI.run(d)["all"], sort_keys=True)
        self.assertEqual(a, b)

    def test_provenance_recorded(self):
        d = self._fixture()
        prov = KI.run(d)["provenance"]
        self.assertTrue(any(p["file"].endswith(".csv") for p in prov))


class EvidenceGaps(unittest.TestCase):
    def _xray(self):
        d = tempfile.mkdtemp()
        pd.DataFrame([
            {"ASIN": "B01", "Title": "Nurse Sweatshirt Printed", "Price": 29.99, "Review Count": 40, "Images": 3},
            {"ASIN": "B02", "Title": "Personalized Nurse Crewneck Custom Name", "Price": 44.99, "Review Count": 80, "Images": 7},
            {"ASIN": "B03", "Title": "Cute Nurse Shirt Print", "Price": 24.99, "Review Count": 15, "Images": 4},
            {"ASIN": "B04", "Title": "RN Gift Sweatshirt", "Price": 34.99, "Review Count": 300, "Images": 5},
        ]).to_excel(os.path.join(d, "xray_nurse.xlsx"), index=False)
        return d

    def test_embroidery_gap_is_manual_review(self):
        d = self._xray()
        gaps = {g["area"]: g for g in CG.run(d)["gaps"]}
        emb = gaps["Real embroidery proof"]
        self.assertEqual(emb["source_type"], "manual-review-required")
        self.assertTrue(emb["manual_confirmation_required"])

    def test_numeric_gaps_labeled(self):
        d = self._xray()
        gaps = {g["area"]: g for g in CG.run(d)["gaps"]}
        self.assertEqual(gaps["Review beatability"]["source_type"], "numeric")

    def test_missing_columns_reported(self):
        d = tempfile.mkdtemp()
        pd.DataFrame([{"ASIN": "B01", "Title": "Nurse Sweatshirt", "Price": 30, "Review Count": 10}]).to_excel(
            os.path.join(d, "xray_x.xlsx"), index=False)  # no Images column
        data = CG.run(d)
        self.assertIn("image count", data["missing_fields"])


class ListingSchema(unittest.TestCase):
    def test_invalid_does_not_overwrite(self):
        import app as DASH
        d = tempfile.mkdtemp()
        good = {"title": "Nurse Sweatshirt Custom Name", "bullets": ["a", "b", "c", "d", "e"]}
        ok, errs = DASH.safe_write_listing(d, good)
        self.assertTrue(ok)
        bad = {"title": "P" * 90, "bullets": []}     # too long + empty bullets
        ok2, errs2 = DASH.safe_write_listing(d, bad)
        self.assertFalse(ok2)
        self.assertTrue(errs2)
        # the good listing is still on disk
        cur = json.load(open(os.path.join(d, "listing.json")))
        self.assertEqual(cur["title"], good["title"])

    def test_title_over_75_is_field_error(self):
        import app as DASH
        ok, errs = DASH.validate_listing({"title": "P" * 76, "bullets": ["a"]})
        self.assertFalse(ok)
        self.assertTrue(any("75" in e for e in errs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
