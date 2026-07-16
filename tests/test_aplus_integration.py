#!/usr/bin/env python3
"""
Session 4 — A+ integration proofs: PageAuditor A+ evidence gates, copy-package inclusion, dashboard/
package agreement, determinism, and the T2-safe end-to-end state.
"""
import copy
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("listing", "dashboard", "compliance"):
    _p = os.path.join(ROOT, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import listing_generator as LG            # noqa: E402
import page_auditor as PA                 # noqa: E402
import copy_package as CPKG               # noqa: E402
import aplus_module_registry as REG       # noqa: E402
import app as DASH                        # noqa: E402

SEED = "personalized nurse sweatshirt"

RICH_FACTS = {
    "decoration_method": {"value": "machine embroidery", "status": "VERIFIED"},
    "personalization_fields": {"value": ["name"], "status": "VERIFIED"},
    "size_range": {"value": ["S", "M", "L", "XL"], "status": "VERIFIED"},
    "color_options": {"value": ["black", "navy"], "status": "VERIFIED"},
    "care_instructions": {"value": "Machine wash cold", "status": "VERIFIED"},
}
REAL_ASSETS = [{"proof_purpose": p, "real_or_generated": "REAL",
                "alt_text": "A real product photo showing the detail", "asset_type": "image"}
               for p in ("MACRO_DECORATION_STITCH", "PERSONALIZATION_EXAMPLE", "GARMENT_OR_COLOR")]


def lean_master(folder):
    recs = [{"keyword_exact": SEED, "keyword_normalized": SEED, "tier": "A_CORE", "owner_status": "auto",
             "reason_codes": [], "risk_flags": [], "search_volume": 1200, "best_rank": 5,
             "median_rank": 20, "batch_support": {"B1": 6}, "observation_count": 1},
            {"keyword_exact": "custom nurse gift", "keyword_normalized": "custom nurse gift",
             "tier": "B_SUPPORTING", "owner_status": "auto", "reason_codes": [], "risk_flags": [],
             "search_volume": 500, "best_rank": 8, "median_rank": 22, "batch_support": {"B1": 4},
             "observation_count": 1}]
    with open(os.path.join(folder, "MASTER-KEYWORDS-LEAN.json"), "w", encoding="utf-8") as f:
        json.dump({"run_metadata": {"run_id": "kwrun_s4", "schema_version": "1.0.0"},
                   "target_profile": {"seed": SEED}, "quality_summary": {"counts": {"A_CORE": 1,
                                                                                     "B_SUPPORTING": 1}},
                   "downstream_policy": {"mode": "transition"}, "keywords": recs}, f)


def project(facts=None, brief=None):
    d = tempfile.mkdtemp()
    lean_master(d)
    if facts is not None:
        with open(os.path.join(d, "product-facts.json"), "w", encoding="utf-8") as f:
            json.dump({"schema_version": "1.0.0", "source": "owner", "product": facts}, f)
    if brief is not None:
        with open(os.path.join(d, "creative-brief.json"), "w", encoding="utf-8") as f:
            json.dump(brief, f)
    return d


def gen(facts=None, brief=None):
    return LG.generate(project(facts=facts, brief=brief))["listing"]


READY_BRIEF = {"category": "apparel", "aplus_capability": "BASIC_A_PLUS", "aplus_assets": REAL_ASSETS}


# ============================================================ PageAuditor A+ evidence
class AuditorAplusEvidence(unittest.TestCase):
    def test_legacy_aplus_remains_blocked_without_facts(self):
        L = gen()                                                   # T2-like: no facts
        self.assertEqual(L["aplus_state"], PA.APLUS_BLOCKED_LEGACY_UNVERIFIED)
        self.assertEqual(L["aplus"], [])
        self.assertNotIn("aplus_module_count",
                         [h["category"] for h in L["audit"]["hard_failures"]])

    def test_pageauditor_sees_every_aplus_field(self):
        L = gen()
        er = L["audit"]["aplus_evidence_results"]
        self.assertTrue(er["present"])
        for key in ("capability", "basic_module_count", "module_states", "fallback_positions",
                    "premium_eligible"):
            self.assertIn(key, er)

    def test_correct_module_counts_pass(self):
        L = gen()
        cats = [h["category"] for h in L["audit"]["hard_failures"]]
        self.assertNotIn("aplus_module_count", cats)
        self.assertNotIn("aplus_duplicate_module_key", cats)

    def test_blocked_module_cannot_enter_publishable_payload(self):
        L = gen()
        L2 = copy.deepcopy(L)
        L2["aplus"] = [{"headline": "X", "copy": "y", "module_key": "HERO_EMBROIDERY_PROOF"}]
        L2["aplus_content"]["publishable_modules"] = [
            {"module_key": "HERO_EMBROIDERY_PROOF", "headline": "X", "copy": "y", "position_slot": 1}]
        audit = PA.audit_listing(L2)
        self.assertEqual(audit["publishability"], PA.BLOCKED_UNSAFE)
        self.assertTrue(any(h["category"] == "aplus_draft_leak" for h in audit["hard_failures"]))

    def test_unknown_capability_premium_draft_is_excluded(self):
        L = gen()                                                   # UNKNOWN default
        self.assertEqual(L["aplus_capability"], REG.UNKNOWN_A_PLUS_CAPABILITY)
        prem = L["aplus_content"]["premium"]
        self.assertTrue(prem["never_publishable"])
        self.assertEqual(L["audit"]["publishability"], PA.SAFE_DRAFT_OWNER_FACTS_REQUIRED)   # not blocked

    def test_placeholder_in_ready_module_blocks(self):
        L = gen(facts=RICH_FACTS, brief=READY_BRIEF)
        L2 = copy.deepcopy(L)
        L2["aplus_content"]["basic_modules"][0]["body"] = "Decorated with {material}."
        audit = PA.audit_listing(L2)
        self.assertEqual(audit["publishability"], PA.BLOCKED_UNSAFE)
        self.assertTrue(any(h["category"] == "aplus_placeholder" for h in audit["hard_failures"]))

    def test_missing_claim_lineage_in_ready_module_blocks(self):
        L = gen(facts=RICH_FACTS, brief=READY_BRIEF)
        L2 = copy.deepcopy(L)
        L2["aplus_content"]["basic_modules"][0]["claim_ids"] = []
        audit = PA.audit_listing(L2)
        self.assertTrue(any(h["category"] == "aplus_missing_lineage" for h in audit["hard_failures"]))

    def test_missing_proof_asset_in_ready_module_blocks(self):
        L = gen(facts=RICH_FACTS, brief=READY_BRIEF)
        L2 = copy.deepcopy(L)
        L2["aplus_content"]["basic_modules"][0]["evidence_summary"]["missing_real_assets"] = ["MACRO"]
        audit = PA.audit_listing(L2)
        self.assertTrue(any(h["category"] == "aplus_missing_asset" for h in audit["hard_failures"]))

    def test_unsupported_claim_in_ready_module_blocks(self):
        L = gen(facts=RICH_FACTS, brief=READY_BRIEF)
        L2 = copy.deepcopy(L)
        L2["aplus_content"]["basic_modules"][0]["body"] = "Made to last, shipped from the US."
        audit = PA.audit_listing(L2)
        self.assertEqual(audit["publishability"], PA.BLOCKED_UNSAFE)
        self.assertTrue(any(h["category"] in ("aplus_unsupported_claim", "unsupported_claim")
                            for h in audit["hard_failures"]))

    def test_broken_fallback_positions_block(self):
        L = gen(facts=RICH_FACTS, brief=READY_BRIEF)
        L2 = copy.deepcopy(L)
        L2["aplus_content"]["premium"]["basic_fallback"] = [
            {"module_key": "X", "position_slot": 7}, {"module_key": "Y", "position_slot": 3},
            {"module_key": "Z", "position_slot": 1}, {"module_key": "W", "position_slot": 2},
            {"module_key": "V", "position_slot": 4}]
        audit = PA.audit_listing(L2)
        self.assertTrue(any(h["category"] == "aplus_fallback_positions" for h in audit["hard_failures"]))

    def test_ready_basic_listing_is_publishable(self):
        L = gen(facts=RICH_FACTS, brief=READY_BRIEF)
        self.assertEqual(L["aplus_state"], "READY_BASIC_A_PLUS")
        self.assertEqual(len(L["aplus"]), REG.BASIC_MODULE_COUNT)
        self.assertEqual(L["audit"]["publishability"], PA.PUBLISHABLE)


# ============================================================ copy package
class CopyPackage(unittest.TestCase):
    def test_only_ready_modules_enter_copy_ready(self):
        L = gen(facts=RICH_FACTS, brief=READY_BRIEF)
        plan = CPKG.build_manual_entry_plan(L)
        self.assertIn("aplus", plan["safe_to_paste"])
        self.assertEqual(len(plan["safe_to_paste"]["aplus"]), REG.BASIC_MODULE_COUNT)
        txt = CPKG.build_copy_ready_text(L)
        self.assertIn("paste-ready", txt)

    def test_blocked_modules_absent_from_copy_ready(self):
        L = gen()                                                   # nothing READY
        plan = CPKG.build_manual_entry_plan(L)
        self.assertNotIn("aplus", plan["safe_to_paste"])
        self.assertEqual(plan["do_not_paste"]["aplus"]["missing_requirement"],
                         CPKG.APLUS_OWNER_FACTS_OR_ASSETS_REQUIRED)

    def test_manual_entry_never_instructs_pasting_blocked_content(self):
        L = gen()
        plan = CPKG.build_manual_entry_plan(L)
        self.assertIn("aplus", plan["do_not_paste"])
        # no instruction line tells the owner to paste A+ when it is not READY
        joined = " ".join(plan.get("instructions", [])).lower()
        self.assertNotIn("paste the ready a+", joined)
        txt = CPKG.build_copy_ready_text(L)
        self.assertIn(CPKG.APLUS_OWNER_FACTS_OR_ASSETS_REQUIRED, txt)

    def test_one_authoritative_copy_ready_source(self):
        d = project()
        LG.generate(d)
        self.assertTrue(os.path.exists(os.path.join(d, "LISTING-COPY-READY.txt")))
        self.assertTrue(os.path.exists(os.path.join(d, "BASIC-APLUS-CONTENT.json")))


# ============================================================ integration / determinism / dashboard
class IntegrationDeterminism(unittest.TestCase):
    def test_t2_remains_safe(self):
        L = gen()
        self.assertEqual(L["audit"]["publishability"], PA.SAFE_DRAFT_OWNER_FACTS_REQUIRED)
        self.assertEqual(L["aplus"], [])
        self.assertEqual(L["aplus_capability"], REG.UNKNOWN_A_PLUS_CAPABILITY)
        self.assertEqual(L["aplus_content"]["basic_ready_count"], 0)

    def test_deterministic_aplus_hash_across_runs(self):
        d = project(facts=RICH_FACTS, brief=READY_BRIEF)
        h1 = LG.generate(d)["listing"]["aplus_content"]["content_sha256"]
        h2 = LG.generate(d)["listing"]["aplus_content"]["content_sha256"]
        self.assertEqual(h1, h2)

    def test_dashboard_and_package_show_same_aplus_state(self):
        d = project(facts=RICH_FACTS, brief=READY_BRIEF)
        r = LG.generate(d)
        DASH.safe_write_listing(d, r["listing"])
        ae = DASH.snapshot(d)["listing_copy"]["aplus_evidence"]
        self.assertEqual(ae["capability"], r["listing"]["aplus_capability"])
        self.assertEqual(ae["basic_ready_count"], r["listing"]["aplus_content"]["basic_ready_count"])
        self.assertTrue(ae["copy_ready_included"])

    def test_dashboard_shows_incomplete_aplus_for_t2(self):
        d = project()
        r = LG.generate(d)
        DASH.safe_write_listing(d, r["listing"])
        ae = DASH.snapshot(d)["listing_copy"]["aplus_evidence"]
        self.assertEqual(ae["capability"], REG.UNKNOWN_A_PLUS_CAPABILITY)
        self.assertFalse(ae["copy_ready_included"])
        self.assertEqual(ae["basic_ready_count"], 0)
        self.assertTrue(ae["eligibility_unverified"])

    def test_listing_carries_all_aplus_fields(self):
        L = gen()
        for field in ("aplus", "aplus_state", "aplus_capability", "aplus_content"):
            self.assertIn(field, L)
        ac = L["aplus_content"]
        for key in ("capability", "basic_modules", "premium", "asset_manifest", "compliance",
                    "publishable_modules", "content_sha256"):
            self.assertIn(key, ac)


if __name__ == "__main__":
    unittest.main(verbosity=2)
