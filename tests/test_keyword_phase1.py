#!/usr/bin/env python3
"""Phase 1 acceptance tests — AMZ_Master_Keyword_List_Final_Lean_Phased_Spec.

One test per LEAN-KW-001..015.
"""
import os, sys, json, shutil, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(ROOT, "research"), os.path.join(ROOT, "compliance")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pandas as pd
from target_keyword_profile import load_keyword_profile
from master_keyword_builder import build, quality_summary
from keyword_relevance_lean import A_CORE, B_SUPPORTING, REVIEW, REJECTED

SEED = "personalized nurse sweatshirt"
B1 = ["B0AAA00001", "B0AAA00002", "B0AAA00003", "B0AAA00004", "B0AAA00005",
      "B0AAA00006", "B0AAA00007", "B0AAA00008", "B0AAA00009", "B0AAA00010"]
B2 = ["B0BBB00001", "B0BBB00002", "B0BBB00003", "B0BBB00004", "B0BBB00005"]
B3 = ["B0CCC00001", "B0CCC00002"]


def write_batches(folder, extra=None):
    doc = {"schema_version": "1.0.0",
           "run_metadata": {"seed": SEED, "marketplace": "US"},
           "target_profile": {"seed": SEED, "audience_key": "nurse", "family_key": "sweatshirt",
                              "decoration_method": "machine embroidery",
                              "personalization_required": True},
           "batches": [{"batch_id": "B1_CORE_MONEY", "paste_ready_asins": B1},
                       {"batch_id": "B2_CORE_OPPORTUNITY", "paste_ready_asins": B2}]}
    if extra:
        doc["batches"].append(extra)
    json.dump(doc, open(os.path.join(folder, "ASIN-BATCHES.json"), "w", encoding="utf-8"), indent=2)


def write_cerebro(folder, rows, asins=B1, name="1-US_AMAZON_cerebro_B0AAA00001_2026-07-15.xlsx",
                  drop_td=False):
    """rows = [(keyword, sv, td, {asin: rank})]"""
    data = {"Keyword Phrase": [], "Keyword Sales": [], "Cerebro IQ Score": [],
            "Search Volume": [], "Search Volume Trend": [], "Competing Products": [],
            "CPR": [], "Title Density": [], "Competitor Rank (avg)": [],
            "Ranking Competitors (count)": []}
    for a in asins:
        data[a] = []
    for kw, sv, td, ranks in rows:
        data["Keyword Phrase"].append(kw); data["Keyword Sales"].append(10)
        data["Cerebro IQ Score"].append(100); data["Search Volume"].append(sv)
        data["Search Volume Trend"].append("+5%"); data["Competing Products"].append(500)
        data["CPR"].append(8); data["Title Density"].append(td)
        data["Competitor Rank (avg)"].append(20); data["Ranking Competitors (count)"].append(len(ranks))
        for a in asins:
            data[a].append(ranks.get(a))
    df = pd.DataFrame(data)
    if drop_td:
        df = df.drop(columns=["Title Density"])
    p = os.path.join(folder, name)
    df.to_excel(p, index=False)
    return p


def strong(n=8):
    """ranks for n B1 ASINs, all in top-50"""
    return {B1[i]: 5 + i for i in range(n)}


def run(folder, **kw):
    prof = load_keyword_profile(folder=folder, **kw)
    matrix, rows = build(folder, prof)
    return {r["keyword_normalized"]: r for r in rows}, rows, matrix


class KeywordPhase1(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        write_batches(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _std(self, extra=()):
        rows = [("personalized nurse sweatshirt", 3000, 5, strong()),
                ("nurse sweatshirt", 5000, 12, strong()),
                ("embroidered nurse sweatshirt", 900, 3, strong(6)),
                ("teacher sweatshirt", 8000, 4, strong()),
                ("bride sweatshirt", 4000, 2, strong()),
                ("mom sweatshirt", 9000, 3, strong()),
                ("mama sweatshirt", 7000, 3, strong()),
                ("halloween sweatshirt", 12000, 2, strong()),
                ("usa anniversary sweatshirt", 6000, 1, strong())] + list(extra)
        write_cerebro(self.d, rows)

    def test_LEAN_KW_001_personalized_nurse_sweatshirt_is_A_CORE(self):
        self._std()
        m, _, _ = run(self.d)
        self.assertEqual(m["personalized nurse sweatshirt"]["tier"], A_CORE)
        self.assertEqual(m["personalized nurse sweatshirt"]["semantic_class"], "DIRECT_EXACT")

    def test_LEAN_KW_002_nurse_sweatshirt_is_A_CORE(self):
        self._std()
        m, _, _ = run(self.d)
        self.assertEqual(m["nurse sweatshirt"]["tier"], A_CORE)

    def test_LEAN_KW_003_embroidered_nurse_sweatshirt_is_core_or_supporting(self):
        self._std()
        m, _, _ = run(self.d)
        self.assertIn(m["embroidered nurse sweatshirt"]["tier"], (A_CORE, B_SUPPORTING))

    def test_LEAN_KW_004_teacher_sweatshirt_rejected(self):
        self._std()
        m, _, _ = run(self.d)
        r = m["teacher sweatshirt"]
        self.assertEqual(r["tier"], REJECTED)
        self.assertEqual(r["semantic_class"], "CONFLICTING")
        self.assertTrue(any("wrong_audience" in f for f in r["risk_flags"]))

    def test_LEAN_KW_005_bride_sweatshirt_rejected(self):
        self._std()
        m, _, _ = run(self.d)
        self.assertEqual(m["bride sweatshirt"]["tier"], REJECTED)

    def test_LEAN_KW_006_halloween_sweatshirt_rejected(self):
        self._std()
        m, _, _ = run(self.d)
        r = m["halloween sweatshirt"]
        self.assertEqual(r["tier"], REJECTED)
        self.assertTrue(any("wrong_occasion" in f for f in r["risk_flags"]))

    def test_LEAN_KW_007_brand_term_rejected_despite_volume(self):
        self._std(extra=[("nike nurse sweatshirt", 99000, 1, strong())])
        m, _, _ = run(self.d)
        r = m["nike nurse sweatshirt"]
        self.assertEqual(r["tier"], REJECTED, "a protected brand must not reach a relevant tier")
        self.assertNotIn(r["tier"], (A_CORE, B_SUPPORTING))

    def test_LEAN_KW_008_asin_rank_columns_survive(self):
        self._std()
        _, rows, matrix = run(self.d)
        e = next(k for k in matrix["keywords"] if k["keyword_normalized"] == "nurse sweatshirt")
        self.assertTrue(e["individual_asin_organic_ranks"], "ASIN rank columns must survive")
        self.assertEqual(len([v for v in e["individual_asin_organic_ranks"].values() if v]), 8)
        self.assertEqual(e["valid_asin_denominator"], 10, "denominator must be the approved batch size")

    def test_LEAN_KW_009_same_keyword_two_files_keeps_both_observations(self):
        write_cerebro(self.d, [("nurse sweatshirt", 5000, 12, strong())],
                      name="1-US_AMAZON_cerebro_B0AAA00001_2026-07-15.xlsx")
        write_cerebro(self.d, [("nurse sweatshirt", 7000, 9, strong())],
                      name="1-US_AMAZON_cerebro_B0AAA00002_2026-07-16.xlsx")
        _, rows, matrix = run(self.d)
        e = next(k for k in matrix["keywords"] if k["keyword_normalized"] == "nurse sweatshirt")
        self.assertEqual(e["observation_count"], 2, "both raw observations must be retained")
        self.assertEqual(len(e["source_observations"]), 2)
        # documented rule applied: newest wins, NOT max(SV)
        self.assertEqual(e["aggregation_rules"]["search_volume"], "most_recent_valid_observation")
        self.assertEqual(e["search_volume"], 7000)

    def test_LEAN_KW_010_missing_title_density_stays_missing(self):
        write_cerebro(self.d, [("nurse sweatshirt", 5000, None, strong())], drop_td=True)
        m, _, matrix = run(self.d)
        e = next(k for k in matrix["keywords"] if k["keyword_normalized"] == "nurse sweatshirt")
        self.assertIsNone(e["title_density"], "missing TD must stay None, never 0")
        self.assertTrue(any("title_density" in f for f in e["missing_value_flags"]))
        self.assertEqual(m["nurse sweatshirt"]["tier"], A_CORE, "missing TD must not change the tier")

    def test_LEAN_KW_011_single_asin_support_is_not_auto_A_CORE(self):
        write_cerebro(self.d, [("nurse sweatshirt", 5000, 12, {B1[0]: 3})])
        m, _, _ = run(self.d)
        self.assertIn(m["nurse sweatshirt"]["tier"], (REVIEW, B_SUPPORTING))
        self.assertNotEqual(m["nurse sweatshirt"]["tier"], A_CORE,
                            "one strong ASIN is not enough for A_CORE")

    def test_LEAN_KW_012_batch_support_visible_and_sorts(self):
        write_cerebro(self.d, [("nurse sweatshirt", 5000, 12, strong())], asins=B1,
                      name="1-US_AMAZON_cerebro_B0AAA00001_2026-07-15.xlsx")
        write_cerebro(self.d, [("nurse sweatshirt", 5000, 12, {B2[0]: 4, B2[1]: 9})], asins=B2,
                      name="1-US_AMAZON_cerebro_B0BBB00001_2026-07-15.xlsx")
        m, _, _ = run(self.d)
        bs = m["nurse sweatshirt"]["batch_support"]
        self.assertIn("B1_CORE_MONEY", bs)
        self.assertIn("B2_CORE_OPPORTUNITY", bs, "cross-batch support must be visible")

    def test_LEAN_KW_013_batch3_only_adjacent_never_auto_A_CORE(self):
        write_batches(self.d, extra={"batch_id": "B3_CONTROLLED_EXPANSION", "paste_ready_asins": B3})
        write_cerebro(self.d, [("nurse sweatshirt", 5000, 12, {B3[0]: 4, B3[1]: 8})], asins=B3,
                      name="1-US_AMAZON_cerebro_B0CCC00001_2026-07-15.xlsx")
        m, _, _ = run(self.d)
        r = m["nurse sweatshirt"]
        self.assertNotEqual(r["tier"], A_CORE, "B3-only support must never be automatic A_CORE")
        self.assertIn(r["tier"], (REVIEW, B_SUPPORTING))

    def test_LEAN_KW_014_deterministic(self):
        self._std()
        _, r1, _ = run(self.d)
        _, r2, _ = run(self.d)
        self.assertEqual([(r["keyword_exact"], r["tier"], r["semantic_class"]) for r in r1],
                         [(r["keyword_exact"], r["tier"], r["semantic_class"]) for r in r2])
        self.assertEqual([r["reason_codes"] for r in r1], [r["reason_codes"] for r in r2])

    def test_LEAN_KW_015_no_conflicts_in_relevant_tiers(self):
        self._std(extra=[("nurse valentine sweatshirt", 4000, 2, strong()),
                         ("nurse tote bag", 3000, 2, strong())])
        m, rows, _ = run(self.d)
        banned = ["teacher sweatshirt", "bride sweatshirt", "mom sweatshirt", "mama sweatshirt",
                  "halloween sweatshirt", "usa anniversary sweatshirt", "nurse valentine sweatshirt"]
        relevant = {r["keyword_normalized"] for r in rows if r["tier"] in (A_CORE, B_SUPPORTING)}
        for b in banned:
            self.assertNotIn(b, relevant, f"'{b}' must never reach A_CORE or B_SUPPORTING")
        qs = quality_summary(rows)
        self.assertEqual(qs["A_CORE_wrong_audience_leakage_pct"], 0)
        self.assertEqual(qs["A_CORE_wrong_occasion_leakage_pct"], 0)
        self.assertEqual(qs["A_CORE_brand_contamination_pct"], 0)
        self.assertTrue(qs["hard_gates_pass"])

    def test_16_volume_and_title_density_cannot_change_a_tier(self):
        """Spec no-go: SV/TD must not influence Phase 1 relevance."""
        write_cerebro(self.d, [("teacher sweatshirt", 999999, 0, strong())])
        m, _, _ = run(self.d)
        self.assertEqual(m["teacher sweatshirt"]["tier"], REJECTED,
                         "huge volume + zero title density must not rescue a wrong audience")


if __name__ == "__main__":
    unittest.main()
