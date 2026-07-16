#!/usr/bin/env python3
"""Session 5A integration — the generator/auditor/copy-package/dashboard all consume the audited backend
optimizer (ACT-009) and the capability/evidence-aware item highlights (ACT-010)."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("listing", "research", "dashboard"):
    sys.path.insert(0, os.path.join(ROOT, d))
import listing_generator as LG
import page_auditor as PA
import copy_package as CPKG
import category_policy_registry as CPR
import app as DASH


def make_project(with_facts=None):
    """A minimal project: an approved lean keyword source (+ optional product facts). No pandas/KI needed."""
    d = tempfile.mkdtemp()
    records = [{"keyword_exact": p, "keyword_normalized": p.lower(), "tier": "A_CORE",
                "owner_status": "auto", "risk_flags": [], "search_volume": sv,
                "simple_competitor_coverage_pct": cov, "best_rank": 5, "median_rank": 20,
                "batch_support": {"B1": 4}, "observation_count": 1}
               for p, sv, cov in [("personalized nurse sweatshirt", 1900, 90.0),
                                  ("embroidered nurse crewneck", 820, 45.0),
                                  ("rn gift pullover", 400, 30.0)]]
    doc = {"run_metadata": {"run_id": "kwrun_s5a", "schema_version": "1.0.0",
                            "source_schema": "MASTER_KEYWORDS_LEAN"},
           "quality_summary": {"counts": {"A_CORE": len(records)}}, "keywords": records}
    with open(os.path.join(d, "MASTER-KEYWORDS-LEAN.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f)
    if with_facts:
        with open(os.path.join(d, "product-facts.json"), "w", encoding="utf-8") as f:
            json.dump({"schema_version": "1.0.0", "product": with_facts}, f)
    return d


class GeneratorIntegration(unittest.TestCase):
    def setUp(self):
        self.d = make_project()
        self.r = LG.generate(self.d, "personalized nurse sweatshirt")
        self.L = self.r["listing"]

    def test_generator_uses_backend_optimizer(self):
        self.assertIn("backend_audit", self.L)
        audit = self.L["backend_audit"]
        self.assertEqual(self.L["backend"], " ".join(t["term"] for t in audit["included_terms"]))
        self.assertLessEqual(audit["bytes_used"], audit["byte_ceiling"])
        self.assertEqual(audit["source_hashes"]["keyword_source_sha256"],
                         self.L["keyword_source_sha256"])

    def test_generator_uses_item_highlights_builder(self):
        self.assertIn("item_highlights_content", self.L)
        self.assertEqual(self.L["item_highlights_capability_state"], "SUPPORTED")   # apparel default
        # no product facts -> nothing verified -> no publishable highlights, no generic filler.
        self.assertEqual(self.L["item_highlights_publishable"], [])

    def test_backend_writes_artifacts(self):
        for name in ("BACKEND-SEARCH-TERMS.json", "BACKEND-SEARCH-TERMS-AUDIT.json",
                     "ITEM-HIGHLIGHTS.json"):
            self.assertTrue(os.path.exists(os.path.join(self.d, name)), name)

    def test_pageauditor_sees_both_fields(self):
        audit = self.L["audit"]
        self.assertTrue(audit["backend_results"]["audit_present"])
        self.assertEqual(audit["backend_results"]["source_hash"], "OK")
        self.assertTrue(audit["backend_results"]["deterministic_order"])
        self.assertTrue(audit["backend_results"]["no_risky_terms"])
        self.assertIn("item_highlights_results", audit)
        self.assertTrue(audit["item_highlights_results"]["supported"])

    def test_backend_field_is_registered_and_byte_safe(self):
        # the auditor's text-field coverage registry must cover the new item-highlights fields.
        reg = {r["field"]: r["disposition"] for r in
               self.L["audit"]["audited_text_fields"]["registry"]}
        self.assertEqual(reg.get("item_highlights_publishable"), "audited")
        self.assertEqual(reg.get("item_highlights_content"), "excluded")

    def test_no_hard_failures_and_safe_draft(self):
        self.assertEqual(self.L["audit"]["hard_failures"], [])
        self.assertEqual(self.L["publishability"], PA.SAFE_DRAFT_OWNER_FACTS_REQUIRED)


class CopyPackageIntegration(unittest.TestCase):
    def test_manual_entry_plan_shows_backend_audit(self):
        d = make_project()
        L = LG.generate(d, "personalized nurse sweatshirt")["listing"]
        plan = CPKG.build_manual_entry_plan(L)
        self.assertIn("backend_audit", plan)
        self.assertEqual(plan["backend_audit"]["bytes_used"], L["backend_audit"]["bytes_used"])
        self.assertIn("item_highlights_state", plan)
        self.assertEqual(plan["item_highlights_state"]["publishable_count"], 0)

    def test_blocked_item_highlights_excluded_from_copy_ready(self):
        d = make_project()
        L = LG.generate(d, "personalized nurse sweatshirt")["listing"]
        txt = CPKG.build_copy_ready_text(L)
        self.assertNotIn("ITEM HIGHLIGHTS (verified, paste-ready)", txt)
        plan = CPKG.build_manual_entry_plan(L)
        self.assertNotIn("item_highlights", plan["safe_to_paste"])          # nothing verified to paste
        self.assertIn("item_highlights", plan["do_not_paste"])

    def test_publishable_item_highlights_reach_copy_when_page_publishable(self):
        # a hand-built PUBLISHABLE listing with a verified item highlight -> it is pasteable.
        L = {
            "category": "apparel", "title": "Personalized Nurse Sweatshirt",
            "bullets": ["b"], "description": "d", "backend": "rn crewneck",
            "publishability": "PUBLISHABLE",
            "item_highlights_capability_state": "SUPPORTED",
            "item_highlights_publishable": [{"highlight_id": "IH-FIT", "concept": "fit",
                                             "text": "Relaxed fit.", "claim_ids": ["CLM-FIT"],
                                             "verification_state": "VERIFIED",
                                             "publishability_status": "PUBLISHABLE"}],
            "item_highlights_content": {"category_support_state": "SUPPORTED", "blocked_count": 0},
        }
        txt = CPKG.build_copy_ready_text(L)
        self.assertIn("ITEM HIGHLIGHTS (verified, paste-ready)", txt)
        self.assertIn("Relaxed fit.", txt)
        plan = CPKG.build_manual_entry_plan(L)
        self.assertIn("item_highlights", plan["safe_to_paste"])
        self.assertEqual(plan["safe_to_paste"]["item_highlights"][0]["concept"], "fit")


class AuditorGating(unittest.TestCase):
    def test_unsafe_publishable_item_highlight_blocks_publication(self):
        L = {
            "category": "apparel", "title": "Nurse Sweatshirt", "bullets": ["b1"], "description": "d",
            "backend": "rn crewneck",
            "item_highlights_capability_state": "SUPPORTED",
            # a comfort claim with NO verified backing must block.
            "item_highlights_publishable": [{"highlight_id": "IH-X", "concept": "comfort",
                                             "text": "soft and comfortable", "claim_ids": ["CLM-X"],
                                             "verification_state": "VERIFIED",
                                             "publishability_status": "PUBLISHABLE"}],
            "item_highlights_content": {"category_support_state": "SUPPORTED"},
        }
        audit = PA.audit_listing(L)
        self.assertEqual(audit["publishability"], PA.BLOCKED_UNSAFE)
        cats = [h["category"] for h in audit["hard_failures"]]
        self.assertIn("item_highlights_unsupported_claim", cats)

    def test_publishable_highlight_on_unsupported_category_blocks(self):
        L = {
            "category": "some-unknown-category-xyz", "title": "T", "bullets": ["b"], "description": "d",
            "backend": "rn",
            "item_highlights_capability_state": "UNSUPPORTED_BY_CATEGORY",
            "item_highlights_publishable": [{"highlight_id": "IH-FIT", "concept": "fit",
                                             "text": "Relaxed fit.", "claim_ids": ["CLM-FIT"],
                                             "verification_state": "VERIFIED",
                                             "publishability_status": "PUBLISHABLE"}],
        }
        audit = PA.audit_listing(L)
        cats = [h["category"] for h in audit["hard_failures"]]
        self.assertIn("item_highlights_unsupported", cats)

    def test_empty_item_highlights_do_not_demote_a_safe_draft(self):
        d = make_project()
        L = LG.generate(d, "personalized nurse sweatshirt")["listing"]
        # empty publishable highlights: no item_highlights hard failure, publishability driven by facts only.
        cats = [h["category"] for h in L["audit"]["hard_failures"]]
        self.assertFalse(any(c.startswith("item_highlights") for c in cats))
        self.assertEqual(L["publishability"], PA.SAFE_DRAFT_OWNER_FACTS_REQUIRED)


class BackendAuditorGating(unittest.TestCase):
    def _base(self, backend, audit):
        return {"category": "apparel", "title": "Nurse Sweatshirt", "bullets": ["b"], "description": "d",
                "backend": backend, "backend_audit": audit, "keyword_source_sha256": "abc"}

    def test_backend_token_not_in_included_terms_blocks(self):
        # a hidden/unaudited backend token (not in included_terms) is a hard failure.
        audit = {"bytes_used": len("rn crewneck".encode()), "byte_ceiling": 249, "included_count": 1,
                 "included_terms": [{"term": "rn", "inclusion_reason": "X", "source_keyword": "k",
                                     "source_sha256": "abc", "blocking_risks": []}],
                 "excluded_count": 0, "source_hashes": {"keyword_source_sha256": "abc"}}
        audit_res = PA.audit_listing(self._base("rn crewneck", audit))
        cats = [h["category"] for h in audit_res["hard_failures"]]
        self.assertIn("backend_provenance_order", cats)

    def test_backend_risky_included_term_blocks(self):
        audit = {"bytes_used": len("teacher".encode()), "byte_ceiling": 249, "included_count": 1,
                 "included_terms": [{"term": "teacher", "inclusion_reason": "X", "source_keyword": "k",
                                     "source_sha256": "abc", "blocking_risks": ["WRONG_AUDIENCE"]}],
                 "excluded_count": 0, "source_hashes": {"keyword_source_sha256": "abc"}}
        audit_res = PA.audit_listing(self._base("teacher", audit))
        cats = [h["category"] for h in audit_res["hard_failures"]]
        self.assertIn("backend_risky_term", cats)

    def test_backend_source_hash_mismatch_blocks(self):
        audit = {"bytes_used": len("rn".encode()), "byte_ceiling": 249, "included_count": 1,
                 "included_terms": [{"term": "rn", "inclusion_reason": "X", "source_keyword": "k",
                                     "source_sha256": "zzz", "blocking_risks": []}],
                 "excluded_count": 0, "source_hashes": {"keyword_source_sha256": "zzz"}}
        audit_res = PA.audit_listing(self._base("rn", audit))
        cats = [h["category"] for h in audit_res["hard_failures"]]
        self.assertIn("backend_source_hash", cats)

    def test_backend_without_audit_still_checks_byte_ceiling(self):
        # a plain backend string (no backend_audit) still fails on overflow, and skips provenance checks.
        L = {"category": "apparel", "title": "T", "bullets": ["b"], "description": "d",
             "backend": "x " * 200}
        audit_res = PA.audit_listing(L)
        cats = [h["category"] for h in audit_res["hard_failures"]]
        self.assertIn("backend_overflow", cats)


class DashboardIntegration(unittest.TestCase):
    def test_dashboard_displays_optimizer_and_highlights(self):
        d = make_project()
        L = LG.generate(d, "personalized nurse sweatshirt")["listing"]
        summary = DASH.listing_copy_summary(d, listing=L)
        bo = summary["backend_optimizer"]
        self.assertTrue(bo["audit_present"])
        self.assertEqual(bo["bytes_used"], L["backend_audit"]["bytes_used"])
        self.assertIn("risk_results", bo)
        ic = summary["item_highlights_capability"]
        self.assertEqual(ic["category_support_state"], "SUPPORTED")
        self.assertEqual(ic["publishable_count"], 0)

    def test_category_policy_summary_exposes_item_highlights_limits(self):
        d = make_project()
        cp = DASH.category_policy_summary(d)
        self.assertTrue(cp["item_highlights_supported"])
        self.assertEqual(cp["item_highlights_max_count"], 5)


class Determinism(unittest.TestCase):
    def test_backend_and_item_highlights_are_deterministic(self):
        d = make_project()
        r1 = LG.generate(d, "personalized nurse sweatshirt")
        r2 = LG.generate(d, "personalized nurse sweatshirt")
        self.assertEqual(r1["listing"]["backend"], r2["listing"]["backend"])
        self.assertEqual(r1["backend_optimization"].content_sha256(),
                         r2["backend_optimization"].content_sha256())
        self.assertEqual(r1["item_highlights_result"].content_sha256(),
                         r2["item_highlights_result"].content_sha256())


if __name__ == "__main__":
    unittest.main(verbosity=2)
