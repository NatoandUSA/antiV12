#!/usr/bin/env python3
"""Tests for the starting-phase research tools: keyword_intelligence + competitor_gap_analyzer."""
import os, sys, json, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "research"))
import pandas as pd
import keyword_intelligence as KI
import competitor_gap_analyzer as CG


def h10(folder):
    pd.DataFrame([
        {"Keyword Phrase": "personalized nurse sweatshirt", "Search Volume": 1900, "Competing Products": 1200, "Title Density": 3},
        {"Keyword Phrase": "custom nurse gift", "Search Volume": 1400, "Competing Products": 600, "Title Density": 2},
        {"Keyword Phrase": "nurse sweatshirt", "Search Volume": 5000, "Competing Products": 9000, "Title Density": 40},
    ]).to_csv(os.path.join(folder, "cerebro_nurse.csv"), index=False)

def yt(folder):
    pd.DataFrame([
        {"keyword": "custom nurse gift", "rank": 8, "growth": 72, "hidden gem score": 85},
    ]).to_csv(os.path.join(folder, "ytrends_nurse.csv"), index=False)

def xray(folder):
    pd.DataFrame([
        {"ASIN": "B01", "Title": "Nurse Sweatshirt Printed", "Price": 29.99, "Review Count": 40, "Images": 3},
        {"ASIN": "B02", "Title": "Personalized Nurse Crewneck Custom Name", "Price": 44.99, "Review Count": 80, "Images": 7},
        {"ASIN": "B03", "Title": "Cute Nurse Shirt Print", "Price": 24.99, "Review Count": 15, "Images": 4},
        {"ASIN": "B04", "Title": "RN Gift Sweatshirt", "Price": 34.99, "Review Count": 300, "Images": 5},
    ]).to_excel(os.path.join(folder, "xray_nurse.xlsx"), index=False)


class KeywordIntelligence(unittest.TestCase):
    def test_reads_sv_and_competition(self):
        d = tempfile.mkdtemp(); h10(d)
        data = KI.run(d)
        top = {r["keyword"]: r for r in data["top"]}
        self.assertEqual(top["custom nurse gift"]["sv"], 1400)
        self.assertEqual(top["custom nurse gift"]["competition"], 600)

    def test_high_sv_low_comp_beats_crowded(self):
        d = tempfile.mkdtemp(); h10(d)
        data = KI.run(d)
        order = [r["keyword"] for r in data["top"]]
        # the crowded 9000-competition term must not rank first despite highest SV
        self.assertNotEqual(order[0], "nurse sweatshirt")

    def test_ytrends_momentum_boosts(self):
        d1 = tempfile.mkdtemp(); h10(d1)
        base = {r["keyword"]: r["score"] for r in KI.run(d1)["top"]}
        d2 = tempfile.mkdtemp(); h10(d2); yt(d2)
        withyt = {r["keyword"]: r["score"] for r in KI.run(d2)["top"]}
        self.assertGreater(withyt["custom nurse gift"], base["custom nurse gift"])
        self.assertTrue(KI.run(d2)["has_ytrends"])

    def test_no_data_is_empty_not_crash(self):
        d = tempfile.mkdtemp()
        data = KI.run(d)
        self.assertEqual(data["count"], 0)
        self.assertTrue(os.path.exists(os.path.join(d, "KEYWORD-INTELLIGENCE.json")))


class CompetitorGaps(unittest.TestCase):
    def test_finds_embroidery_and_personalization_gaps(self):
        d = tempfile.mkdtemp(); xray(d)
        data = CG.run(d)
        areas = {g["area"] for g in data["gaps"]}
        self.assertIn("Real embroidery proof", areas)
        self.assertIn("Personalization depth", areas)

    def test_review_beatability_flags_weak_page(self):
        d = tempfile.mkdtemp(); xray(d)
        data = CG.run(d)
        rb = [g for g in data["gaps"] if g["area"] == "Review beatability"][0]
        self.assertGreaterEqual(rb["score"], 50)  # 3 of 4 under 100 reviews

    def test_missing_xray_is_graceful(self):
        d = tempfile.mkdtemp()
        data = CG.run(d)
        self.assertFalse(data["ok"])
        self.assertTrue(os.path.exists(os.path.join(d, "COMPETITOR-GAP.json")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
