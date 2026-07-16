#!/usr/bin/env python3
"""Session 3 integration — the generator, validator and dashboard share the new engines and the one
PageAuditor, without regressing the Session 1 keyword source or the Session 2 policy/facts behavior."""
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
import listing_generator as LG
import page_auditor as PA
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
    json.dump({"run_metadata": {"run_id": "kwrun_s3", "schema_version": "1.0.0"},
               "target_profile": {"seed": SEED}, "quality_summary": {"counts": {"A_CORE": 2}},
               "downstream_policy": {"mode": "transition"}, "keywords": recs},
              open(os.path.join(d, "MASTER-KEYWORDS-LEAN.json"), "w"))
    return d


def write_facts(folder, product):
    json.dump({"schema_version": "1.0.0", "source": "owner", "product": product},
              open(os.path.join(folder, "product-facts.json"), "w"))


class GeneratorUsesEngines(unittest.TestCase):
    def setUp(self):
        self.d = base_project()

    def test_listing_carries_all_shared_engine_outputs(self):
        L = LG.generate(self.d)["listing"]
        for key in ("title_candidates", "title_recommended", "mobile_preview", "bullet_objects",
                    "description_meta", "claim_evidence", "audit", "owner_fact_required",
                    "keyword_source_sha256", "product_fact_source_sha256"):
            self.assertIn(key, L)
        self.assertEqual(len(L["bullet_objects"]), 5)
        self.assertEqual(len({b["job"] for b in L["bullet_objects"]}), 5)

    def test_generated_listing_passes_shared_page_auditor(self):
        r = LG.generate(self.d)
        self.assertNotEqual(r["listing"]["audit"]["status"], PA.BLOCKED)
        ok, errs = DASH.validate_listing(r["listing"])
        self.assertTrue(ok, errs)

    def test_no_facts_produces_a_safe_blocked_draft_with_owner_fact_required(self):
        r = LG.generate(self.d)                                 # no product-facts.json
        L = r["listing"]
        blocked = [b for b in L["bullet_objects"] if b["publishability"] == "BLOCKED_INCOMPLETE"]
        self.assertTrue(blocked)                                # some jobs cannot be stated safely
        self.assertTrue(L["owner_fact_required"])
        # zero unsupported claims survive into publishable copy
        self.assertEqual(L["audit"]["claim_results"]["unsupported"], [])

    def test_verified_facts_flow_into_title_bullets_description(self):
        write_facts(self.d, {"decoration_method": {"value": "machine embroidery", "status": "VERIFIED"},
                             "material": {"value": "80% cotton 20% polyester", "status": "VERIFIED"}})
        L = LG.generate(self.d)["listing"]
        blob = " ".join([L["title"], L["description"]] + L["bullets"]).lower()
        self.assertIn("embroidered", L["title"].lower())
        self.assertIn("80% cotton 20% polyester", blob)
        self.assertNotEqual(L["audit"]["status"], PA.BLOCKED)

    def test_deterministic_output_across_identical_runs(self):
        a = LG.generate(self.d)["listing"]
        b = LG.generate(self.d)["listing"]
        def stable(x):
            x = {k: v for k, v in x.items() if k != "audit"}
            return json.dumps(x, sort_keys=True, ensure_ascii=False)
        self.assertEqual(stable(a), stable(b))
        self.assertEqual(a["claim_evidence"]["source_content_sha256"],
                         b["claim_evidence"]["source_content_sha256"])


class ValidatorAndDashboardSharePageAuditor(unittest.TestCase):
    def test_dashboard_safe_write_blocks_unsafe_and_preserves_last_valid(self):
        d = base_project()
        good = LG.generate(d)["listing"]
        ok, _ = DASH.safe_write_listing(d, good)
        self.assertTrue(ok)
        title_before = json.load(open(os.path.join(d, "listing.json"), encoding="utf-8"))["title"]
        unsafe = dict(good)
        unsafe["bullets"] = list(good["bullets"])
        unsafe["bullets"][2] = "Made to last, shipped from the US with tracking."
        unsafe.pop("claim_evidence", None)          # strip lineage so the fabricated claim is unbacked
        ok2, errs2 = DASH.safe_write_listing(d, unsafe)
        self.assertFalse(ok2)
        self.assertTrue(errs2)
        after = json.load(open(os.path.join(d, "listing.json"), encoding="utf-8"))
        self.assertEqual(after["title"], title_before)

    def test_dashboard_snapshot_surfaces_listing_copy_and_page_audit(self):
        d = base_project()
        good = LG.generate(d)["listing"]
        DASH.safe_write_listing(d, good)
        lc = DASH.snapshot(d)["listing_copy"]
        self.assertIn("page_audit", lc)
        self.assertIn(lc["page_audit"]["status"], (PA.PASS, PA.PASS_WITH_WARNINGS))
        self.assertEqual(len(lc["bullet_jobs"]), 5)
        self.assertIsNotNone(lc["claim_counts"])

    def test_validator_verdict_matches_dashboard_verdict(self):
        d = base_project()
        L = LG.generate(d)["listing"]
        dash_ok, _ = DASH.validate_listing(L)
        audit_ok, _ = PA.audit_verdict(L)
        self.assertEqual(dash_ok, audit_ok)


class SessionOneAndTwoPreserved(unittest.TestCase):
    def setUp(self):
        self.d = base_project()

    def test_session1_keyword_source_still_authoritative(self):
        L = LG.generate(self.d)["listing"]
        self.assertEqual(L["keyword_source_file"], "MASTER-KEYWORDS-LEAN.json")
        self.assertEqual(L["keyword_source_schema"], KSA.SCHEMA_LEAN)
        self.assertFalse(L["legacy_unsafe"])

    def test_session1_listing_and_dashboard_hash_still_match(self):
        L = LG.generate(self.d)["listing"]
        s = DASH.keyword_source_summary(self.d)
        self.assertEqual(L["keyword_source_sha256"], s["sha256"])

    def test_session2_product_facts_still_normalized_and_missing_stays_missing(self):
        r = LG.generate(self.d)
        for fld in ("material", "personalization_fields"):
            self.assertIn(fld, r["owner_fact_required"])

    def test_session2_category_policy_still_shared(self):
        L = LG.generate(self.d)["listing"]
        self.assertEqual(L["category_policy"]["title_hard_limit"],
                         DASH.snapshot(self.d)["category_policy"]["title_hard_limit"])

    def test_adding_facts_does_not_change_keyword_hash(self):
        before = LG.generate(self.d)["listing"]["keyword_source_sha256"]
        write_facts(self.d, {"material": {"value": "cotton", "status": "VERIFIED"}})
        after = LG.generate(self.d)["listing"]["keyword_source_sha256"]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
