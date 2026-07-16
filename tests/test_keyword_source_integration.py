#!/usr/bin/env python3
"""Integration tests — the listing generator and the dashboard must agree on one keyword source.

ACT-001: the listing generator read KEYWORD-INTELLIGENCE.json only, so the lean engine could not
         reach titles/bullets/backend.
ACT-002: the dashboard read master-keywords.xlsx "Top Picks" independently, so the two could disagree.

Also covers the runs/T2 fixture invariants: a duplicate Cerebro export must not inflate the evidence
that decides a tier, and an approved batch with no matching export must produce a clear diagnostic.
"""
import json, os, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("listing", "research", "dashboard", "compliance"):
    p = os.path.join(ROOT, d)
    if p not in sys.path:
        sys.path.insert(0, p)

import pandas as pd
import keyword_source_adapter as KSA
import listing_generator as LG
import app as DASH

SEED = "personalized nurse sweatshirt"
# Phrases the fixture must never allocate — the V6 fixture_exclusions list.
REJECTED_PHRASES = ["teacher sweatshirt", "bride sweatshirt", "halloween nurse sweatshirt",
                    "nurse mom sweatshirt", "nursing bra"]


def lean_kw(keyword, tier="A_CORE", **extra):
    r = {"keyword_exact": keyword, "keyword_normalized": keyword.lower(), "tier": tier,
         "owner_status": "auto", "reason_codes": [], "risk_flags": [], "search_volume": 500,
         "simple_competitor_coverage_pct": 60.0, "best_rank": 8, "median_rank": 20,
         "batch_support": {"B1_CORE_MONEY": 6}, "observation_count": 1}
    r.update(extra)
    return r


def lean_doc(records, run_id="kwrun_integration"):
    counts = {}
    for r in records:
        counts[r["tier"]] = counts.get(r["tier"], 0) + 1
    return {"run_metadata": {"run_id": run_id, "schema_version": "1.0.0", "seed": SEED},
            "target_profile": {"seed": SEED},
            "quality_summary": {"counts": counts, "total_keywords": len(records)},
            "downstream_policy": {"mode": "transition", "lean_master_status": "available"},
            "keywords": records}


def fixture_records():
    """A realistic lean spread: strong A_CORE, supporting B, owner-review, and rejected conflicts."""
    records = [
        lean_kw(SEED, "A_CORE", simple_competitor_coverage_pct=90.0, best_rank=2, search_volume=330),
        lean_kw("custom nurse sweatshirt", "A_CORE", simple_competitor_coverage_pct=85.0, best_rank=4),
        lean_kw("embroidered nurse sweatshirt", "A_CORE", simple_competitor_coverage_pct=75.0, best_rank=6),
        lean_kw("nurse crewneck", "B_SUPPORTING", simple_competitor_coverage_pct=30.0, best_rank=25),
        lean_kw("rn gift idea", "REVIEW", simple_competitor_coverage_pct=10.0),
    ]
    for phrase in REJECTED_PHRASES:
        risk = ("wrong_audience:teacher" if "teacher" in phrase else
                "wrong_audience:bride" if "bride" in phrase else
                "wrong_occasion_or_theme:halloween" if "halloween" in phrase else
                "wrong_audience:mom" if "mom" in phrase else "wrong_product_family:bra")
        records.append(lean_kw(phrase, "REJECTED", risk_flags=[risk],
                               search_volume=9999, simple_competitor_coverage_pct=99.0))
    return records


def write_json(folder, name, doc):
    with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)


def write_gaps(folder):
    write_json(folder, "COMPETITOR-GAP.json",
               {"gaps": [{"area": "Image coverage", "score": 90, "source_type": "observed",
                          "manual_confirmation_required": False}]})


class LeanProject(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        write_json(self.d, "MASTER-KEYWORDS-LEAN.json", lean_doc(fixture_records()))
        write_gaps(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    # -- ACT-001 -----------------------------------------------------
    def test_listing_uses_the_lean_source(self):
        r = LG.generate(self.d)
        self.assertTrue(r["ok"], r.get("reason"))
        L = r["listing"]
        self.assertEqual(L["keyword_source_file"], "MASTER-KEYWORDS-LEAN.json")
        self.assertEqual(L["keyword_source_schema"], KSA.SCHEMA_LEAN)
        self.assertEqual(L["keyword_source_mode"], KSA.MODE_LEAN)
        self.assertEqual(L["keyword_source_run_id"], "kwrun_integration")
        self.assertFalse(L["legacy_unsafe"])

    def test_listing_carries_every_required_source_metadata_field(self):
        L = LG.generate(self.d)["listing"]
        for field in ("keyword_source_file", "keyword_source_schema", "keyword_source_run_id",
                      "keyword_source_mode", "keyword_source_sha256", "legacy_unsafe",
                      "keyword_source_warnings"):
            self.assertIn(field, L)

    def test_lean_A_CORE_keyword_reaches_title_allocation(self):
        r = LG.generate(self.d)
        self.assertEqual(r["listing"]["selected_keywords"]["primary"], [SEED])
        self.assertIn("nurse", r["listing"]["title"].lower())

    def test_listing_prefers_lean_over_legacy_when_both_exist(self):
        write_json(self.d, "KEYWORD-INTELLIGENCE.json",
                   {"schema_version": "2.4", "top": [{"keyword": "legacy only phrase", "sv": 10}]})
        L = LG.generate(self.d)["listing"]
        self.assertEqual(L["keyword_source_file"], "MASTER-KEYWORDS-LEAN.json")
        self.assertNotIn("legacy only phrase", json.dumps(L["selected_keywords"]))

    def test_listing_writes_the_normalized_artifact(self):
        r = LG.generate(self.d)
        p = os.path.join(self.d, KSA.NORMALIZED_FILENAME)
        self.assertTrue(os.path.exists(p))
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(doc["source_sha256"], r["listing"]["keyword_source_sha256"])
        self.assertEqual(doc["source_schema"], KSA.SCHEMA_LEAN)

    # -- ACT-002 -----------------------------------------------------
    def test_dashboard_uses_the_same_source(self):
        summary = DASH.keyword_source_summary(self.d)
        self.assertEqual(summary["file"], "MASTER-KEYWORDS-LEAN.json")
        self.assertEqual(summary["schema"], KSA.SCHEMA_LEAN)
        self.assertEqual(summary["run_id"], "kwrun_integration")
        self.assertEqual(summary["mode"], KSA.MODE_LEAN)

    def test_dashboard_does_not_use_master_keywords_xlsx_when_lean_exists(self):
        """A decoy Top Picks sheet must be ignored while an authoritative source exists."""
        pd.DataFrame([{"Keyword": "decoy top pick", "Search Volume": 9999}]).to_excel(
            os.path.join(self.d, "master-keywords.xlsx"), sheet_name="Top Picks", index=False)
        rows = DASH.parse_keywords(self.d)
        self.assertNotIn("decoy top pick", [r["keyword"] for r in rows])
        self.assertEqual(rows[0]["keyword"], SEED)

    def test_dashboard_exposes_every_required_field(self):
        s = DASH.keyword_source_summary(self.d)
        for field in ("file", "schema", "run_id", "mode", "sha256", "tier_a", "tier_b", "review",
                      "rejected", "excluded", "warnings"):
            self.assertIn(field, s)
        self.assertEqual(s["tier_a"], 3)
        self.assertEqual(s["tier_b"], 1)
        self.assertEqual(s["review"], 1)
        self.assertEqual(s["rejected"], len(REJECTED_PHRASES))

    def test_dashboard_surfaces_a_malformed_source_instead_of_falling_back(self):
        with open(os.path.join(self.d, "MASTER-KEYWORDS-LEAN.json"), "w", encoding="utf-8") as f:
            f.write("{broken json")
        write_json(self.d, "KEYWORD-INTELLIGENCE.json", {"top": [{"keyword": "legacy", "sv": 1}]})
        s = DASH.keyword_source_summary(self.d)
        self.assertIn("error", s)
        self.assertIn("MASTER-KEYWORDS-LEAN.json", s["error"])
        self.assertEqual(DASH.parse_keywords(self.d), [])

    def test_dashboard_snapshot_includes_the_keyword_source(self):
        snap = DASH.snapshot(self.d)
        self.assertEqual(snap["keyword_source"]["file"], "MASTER-KEYWORDS-LEAN.json")
        self.assertEqual(snap["keywords"][0]["keyword"], SEED)

    # -- the two must agree ------------------------------------------
    def test_listing_and_dashboard_source_hashes_match(self):
        L = LG.generate(self.d)["listing"]
        s = DASH.keyword_source_summary(self.d)
        self.assertEqual(L["keyword_source_sha256"], s["sha256"])
        self.assertEqual(L["keyword_source_file"], s["file"])
        self.assertEqual(L["keyword_source_run_id"], s["run_id"])
        self.assertEqual(L["keyword_source_schema"], s["schema"])

    def test_listing_and_dashboard_agree_on_the_keyword_order(self):
        L = LG.generate(self.d)["listing"]
        dash = [r["keyword"] for r in DASH.parse_keywords(self.d)]
        listing_kws = L["selected_keywords"]["backend"]
        self.assertEqual(dash, listing_kws[:len(dash)])

    # -- zero leakage ------------------------------------------------
    def test_rejected_phrases_reach_neither_listing_nor_dashboard(self):
        L = LG.generate(self.d)["listing"]
        dash_kws = [r["keyword"] for r in DASH.parse_keywords(self.d)]
        allocated = json.dumps(L["selected_keywords"]).lower()
        for phrase in REJECTED_PHRASES:
            self.assertNotIn(phrase, allocated)
            self.assertNotIn(phrase, dash_kws)
            self.assertNotIn(phrase, L["title"].lower())
        # the distinctive conflict words must not survive into the backend string either
        for word in ("teacher", "bride", "halloween", "bra"):
            self.assertNotIn(word, L["backend"].lower())

    def test_review_tier_is_not_auto_allocated(self):
        L = LG.generate(self.d)["listing"]
        self.assertNotIn("rn gift idea", json.dumps(L["selected_keywords"]).lower())

    def test_high_volume_rejected_keyword_cannot_win_on_volume(self):
        """The rejected fixtures carry SV 9999 / 99% coverage — tier safety must still exclude them."""
        src = KSA.load_keyword_source(self.d)
        self.assertEqual(src.eligible_phrases()[0], SEED)
        for phrase in REJECTED_PHRASES:
            self.assertNotIn(phrase, src.eligible_phrases())


class LegacyProject(unittest.TestCase):
    """A project that only ever had KEYWORD-INTELLIGENCE.json still works — but is marked unsafe."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        write_json(self.d, "KEYWORD-INTELLIGENCE.json",
                   {"schema_version": "2.4", "count": 2,
                    "top": [{"keyword": "nurse sweatshirt", "score": 99, "sv": 4663,
                             "confidence": "MEDIUM"},
                            {"keyword": "nurse crewneck", "score": 80, "sv": 900,
                             "confidence": "LOW"}]})
        write_gaps(self.d)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_legacy_listing_is_marked_legacy_unsafe(self):
        L = LG.generate(self.d)["listing"]
        self.assertEqual(L["keyword_source_file"], "KEYWORD-INTELLIGENCE.json")
        self.assertEqual(L["keyword_source_mode"], KSA.MODE_LEGACY_UNSAFE)
        self.assertTrue(L["legacy_unsafe"])
        self.assertTrue(any(KSA.WARN_LEGACY_UNVERIFIED in w for w in L["keyword_source_warnings"]))

    def test_listing_can_require_an_authoritative_source(self):
        r = LG.generate(self.d, allow_legacy_unsafe=False)
        self.assertFalse(r["ok"])
        self.assertIn("blocked", r["reason"])

    def test_legacy_listing_and_dashboard_still_agree(self):
        L = LG.generate(self.d)["listing"]
        s = DASH.keyword_source_summary(self.d)
        self.assertEqual(L["keyword_source_sha256"], s["sha256"])
        self.assertTrue(s["legacy_unsafe"])

    def test_legacy_preserves_the_engine_ranking_order(self):
        self.assertEqual([r["keyword"] for r in DASH.parse_keywords(self.d)],
                         ["nurse sweatshirt", "nurse crewneck"])


class MalformedProject(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_malformed_lean_stops_the_listing_instead_of_inventing_defaults(self):
        with open(os.path.join(self.d, "MASTER-KEYWORDS-LEAN.json"), "w", encoding="utf-8") as f:
            f.write('{"keywords": [')
        write_json(self.d, "KEYWORD-INTELLIGENCE.json", {"top": [{"keyword": "legacy", "sv": 1}]})
        with self.assertRaises(KSA.MalformedKeywordSourceError):
            LG.generate(self.d)

    def test_malformed_optional_json_is_not_swallowed(self):
        """ACT-003: an existing-but-corrupt optional file must not silently become 'no data'."""
        write_json(self.d, "MASTER-KEYWORDS-LEAN.json", lean_doc([lean_kw(SEED)]))
        with open(os.path.join(self.d, "COMPETITOR-GAP.json"), "w", encoding="utf-8") as f:
            f.write("{ corrupt")
        with self.assertRaises(KSA.MalformedKeywordSourceError):
            LG.generate(self.d)

    def test_absent_optional_json_is_still_fine(self):
        write_json(self.d, "MASTER-KEYWORDS-LEAN.json", lean_doc([lean_kw(SEED)]))
        self.assertTrue(LG.generate(self.d)["ok"])          # no COMPETITOR-GAP.json at all

    def test_no_source_at_all_is_graceful(self):
        r = LG.generate(self.d, "")
        self.assertFalse(r["ok"])


# ---------------------------------------------------------------- fixture integrity (runs/T2)
B1 = [f"B0AAA0000{i}" for i in range(1, 10)] + ["B0AAA00010"]
B2 = [f"B0BBB0000{i}" for i in range(1, 6)]


def write_batches(folder, batches=("B1_CORE_MONEY", "B2_CORE_OPPORTUNITY")):
    doc = {"schema_version": "1.0.0", "run_metadata": {"seed": SEED, "marketplace": "US"},
           "target_profile": {"seed": SEED, "audience_key": "nurse", "family_key": "sweatshirt",
                              "decoration_method": "machine embroidery",
                              "personalization_required": True},
           "batches": []}
    if "B1_CORE_MONEY" in batches:
        doc["batches"].append({"batch_id": "B1_CORE_MONEY", "paste_ready_asins": B1})
    if "B2_CORE_OPPORTUNITY" in batches:
        doc["batches"].append({"batch_id": "B2_CORE_OPPORTUNITY", "paste_ready_asins": B2})
    write_json(folder, "ASIN-BATCHES.json", doc)


def write_cerebro(folder, name, asins, rows):
    """rows = [(keyword, sv, title_density, {asin: rank})]"""
    data = {"Keyword Phrase": [], "Keyword Sales": [], "Cerebro IQ Score": [], "Search Volume": [],
            "Search Volume Trend": [], "Competing Products": [], "CPR": [], "Title Density": [],
            "Competitor Rank (avg)": [], "Ranking Competitors (count)": []}
    for a in asins:
        data[a] = []
    for kw, sv, td, ranks in rows:
        data["Keyword Phrase"].append(kw); data["Keyword Sales"].append(10)
        data["Cerebro IQ Score"].append(100); data["Search Volume"].append(sv)
        data["Search Volume Trend"].append("+5%"); data["Competing Products"].append(500)
        data["CPR"].append(8); data["Title Density"].append(td)
        data["Competitor Rank (avg)"].append(20)
        data["Ranking Competitors (count)"].append(len(ranks))
        for a in asins:
            data[a].append(ranks.get(a))
    p = os.path.join(folder, name)
    pd.DataFrame(data).to_excel(p, index=False)
    return p


CEREBRO_ROWS = [(SEED, 330, 3, {B1[i]: 2 + i for i in range(9)}),
                ("nurse sweatshirt", 4663, 19, {B1[i]: 5 + i for i in range(8)})]


class FixtureIntegrity(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _matrix(self):
        from target_keyword_profile import load_keyword_profile
        from master_keyword_builder import build
        profile = load_keyword_profile(folder=self.d)
        matrix, rows = build(self.d, profile)
        return matrix, {r["keyword_normalized"]: r for r in rows}

    def test_duplicate_cerebro_export_cannot_inflate_the_decision_evidence(self):
        """A byte-identical duplicate export must not change coverage, batch support, rank or tier.

        Raw observation_count is provenance and legitimately counts both copies; what must never move
        is the evidence a tier is decided on.
        """
        write_batches(self.d, batches=("B1_CORE_MONEY",))
        write_cerebro(self.d, "US_AMAZON_cerebro_B0AAA00001_2026-07-15.xlsx", B1, CEREBRO_ROWS)
        _, single = self._matrix()

        shutil.copyfile(os.path.join(self.d, "US_AMAZON_cerebro_B0AAA00001_2026-07-15.xlsx"),
                        os.path.join(self.d, "dupe-US_AMAZON_cerebro_B0AAA00001_2026-07-15.xlsx"))
        _, doubled = self._matrix()

        for kw in single:
            with self.subTest(keyword=kw):
                for field in ("simple_competitor_coverage_pct", "top_20_coverage_pct", "best_rank",
                              "median_rank", "tier", "search_volume", "batch_support"):
                    self.assertEqual(single[kw][field], doubled[kw][field],
                                     f"{field} moved when a duplicate export was added")

    def test_duplicate_export_does_not_add_a_second_batch(self):
        write_batches(self.d, batches=("B1_CORE_MONEY",))
        write_cerebro(self.d, "US_AMAZON_cerebro_B0AAA00001_2026-07-15.xlsx", B1, CEREBRO_ROWS)
        shutil.copyfile(os.path.join(self.d, "US_AMAZON_cerebro_B0AAA00001_2026-07-15.xlsx"),
                        os.path.join(self.d, "copy-US_AMAZON_cerebro_B0AAA00001_2026-07-15.xlsx"))
        matrix, rows = self._matrix()
        self.assertEqual(matrix["source_validation"]["matched_batches"], ["B1_CORE_MONEY"])
        self.assertEqual(list(rows[SEED]["batch_support"]), ["B1_CORE_MONEY"])

    def test_missing_B2_export_produces_a_clear_batch_diagnostic(self):
        write_batches(self.d)                      # both batches approved...
        write_cerebro(self.d, "US_AMAZON_cerebro_B0AAA00001_2026-07-15.xlsx", B1, CEREBRO_ROWS)
        matrix, _ = self._matrix()                 # ...but only the B1 export is present
        sv = matrix["source_validation"]
        self.assertEqual(sv["matched_batches"], ["B1_CORE_MONEY"])
        unmatched = sv["unmatched_batches"]
        self.assertEqual([u["batch_id"] for u in unmatched], ["B2_CORE_OPPORTUNITY"])
        self.assertIn("B2_CORE_OPPORTUNITY", unmatched[0]["reason"])
        self.assertIn("no Cerebro export", unmatched[0]["reason"])

    def test_both_batches_matched_reports_no_unmatched_diagnostic(self):
        write_batches(self.d)
        write_cerebro(self.d, "US_AMAZON_cerebro_B0AAA00001_2026-07-15.xlsx", B1, CEREBRO_ROWS)
        write_cerebro(self.d, "US_AMAZON_cerebro_B0BBB00001_2026-07-15.xlsx", B2,
                      [(SEED, 330, 3, {B2[i]: 3 + i for i in range(5)})])
        sv, _ = self._matrix()
        sv = sv["source_validation"]
        self.assertEqual(sv["matched_batches"], ["B1_CORE_MONEY", "B2_CORE_OPPORTUNITY"])
        self.assertEqual(sv["unmatched_batches"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
