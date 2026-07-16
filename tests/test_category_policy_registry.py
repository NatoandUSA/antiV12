#!/usr/bin/env python3
"""ACT-004 — the versioned category-policy registry replaces the hardcoded universal 75-char title rule.

Proves one shared registry drives the generator, the validator, and the dashboard, that changing one
policy record updates all three consistently, and that no active consumer still hardcodes a 75 rule.
"""
import json, os, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("listing", "research", "dashboard", "compliance"):
    p = os.path.join(ROOT, d)
    if p not in sys.path:
        sys.path.insert(0, p)

import pandas as pd
import keyword_intelligence as KI
import competitor_gap_analyzer as CG
import category_policy_registry as CPR
import listing_generator as LG
import listing_validate as LV
import app as DASH


def lean_project(title_facts=True):
    """A minimal lean project the generator can build from (no product-facts.json -> facts UNKNOWN)."""
    d = tempfile.mkdtemp()
    pd.DataFrame([
        {"Keyword Phrase": "personalized nurse sweatshirt", "Search Volume": 1900,
         "Competing Products": 1200, "Title Density": 3},
    ]).to_csv(os.path.join(d, "cerebro_nurse.csv"), index=False)
    pd.DataFrame([{"ASIN": "B01", "Title": "Nurse Sweatshirt", "Price": 29.99,
                   "Review Count": 40, "Images": 3}]).to_excel(os.path.join(d, "xray_nurse.xlsx"), index=False)
    KI.run(d); CG.run(d)
    recs = [{"keyword_exact": "personalized nurse sweatshirt", "keyword_normalized": "personalized nurse sweatshirt",
             "tier": "A_CORE", "owner_status": "auto", "reason_codes": [], "risk_flags": [],
             "search_volume": 1900, "simple_competitor_coverage_pct": 90.0, "best_rank": 5,
             "median_rank": 20, "batch_support": {"B1": 6}, "observation_count": 1}]
    json.dump({"run_metadata": {"run_id": "cat_policy", "schema_version": "1.0.0"},
               "target_profile": {"seed": "x"}, "quality_summary": {"counts": {"A_CORE": 1}},
               "downstream_policy": {"mode": "transition"}, "keywords": recs},
              open(os.path.join(d, "MASTER-KEYWORDS-LEAN.json"), "w"))
    return d


def registry_with_apparel_limit(limit):
    """Write a fixture registry that differs from the default (apparel title_hard_limit=limit)."""
    d = tempfile.mkdtemp()
    base = {"schema_version": "1.0.0", "marketplace": "US", "product_category": "apparel",
            "category_identifier": "US:apparel", "title_hard_limit": limit,
            "title_recommended_target": min(60, limit - 5), "bullet_count": 5,
            "bullet_character_limit": 500, "description_character_limit": 2000,
            "backend_byte_ceiling": 249, "supported_catalog_fields": ["title", "bullet_points"],
            "unsupported_catalog_fields": [], "policy_source": "fixture",
            "policy_verified_at": "2026-07-16"}
    fb = dict(base, product_category="__fallback__", category_identifier="US:__fallback__",
              policy_source="LOCAL_FALLBACK fixture", policy_verified_at=None)
    p = os.path.join(d, "category_policies.json")
    json.dump({"schema_version": "1.0.0", "default_marketplace": "US",
               "policies": [base], "fallback_policy": fb}, open(p, "w"))
    return p


class RegistryBasics(unittest.TestCase):
    def test_known_category_resolves_configured_policy(self):
        p = CPR.resolve_category_policy("apparel")
        self.assertFalse(p.fallback_used)
        self.assertEqual(p.warning_state, CPR.WARNING_NONE)
        self.assertEqual(p.category_identifier, "US:apparel")
        self.assertIsInstance(p.title_hard_limit, int)

    def test_unknown_category_uses_documented_fallback(self):
        p = CPR.resolve_category_policy("no-such-category")
        self.assertTrue(p.fallback_used)
        self.assertIn("fallback", p.category_identifier.lower())
        # the fallback must be documented as such and must not positively claim officialness
        self.assertIn("fallback", p.policy_source.lower())
        self.assertIn("not an official", p.policy_source.lower())

    def test_fallback_emits_policy_verification_required(self):
        p = CPR.resolve_category_policy("no-such-category")
        self.assertEqual(p.warning_state, CPR.POLICY_VERIFICATION_REQUIRED)

    def test_owner_override_works(self):
        p = CPR.resolve_category_policy("apparel", owner_override={"title_hard_limit": 60})
        self.assertEqual(p.title_hard_limit, 60)
        self.assertEqual(p.owner_override, {"title_hard_limit": 60})

    def test_invalid_policy_record_fails_clearly(self):
        errs = CPR.validate_policy_record({"product_category": "x"})
        self.assertTrue(errs)
        self.assertTrue(any("title_hard_limit" in e for e in errs))
        # a registry file that carries an invalid record must not load silently
        d = tempfile.mkdtemp()
        bad = os.path.join(d, "category_policies.json")
        json.dump({"policies": [{"product_category": "broken"}],
                   "fallback_policy": {}}, open(bad, "w"))
        with self.assertRaises(CPR.InvalidPolicyRecordError):
            CPR.load_policy_registry(bad)

    def test_supported_catalog_fields_come_from_registry(self):
        fields = CPR.get_supported_catalog_fields("apparel")
        self.assertIn("title", fields)
        self.assertEqual(fields, CPR.resolve_category_policy("apparel").supported_catalog_fields)

    def test_backend_byte_ceiling_comes_from_registry(self):
        self.assertEqual(CPR.get_backend_byte_ceiling("apparel"),
                         CPR.resolve_category_policy("apparel").backend_byte_ceiling)

    def test_title_hard_limit_helper_matches_record(self):
        self.assertEqual(CPR.get_title_hard_limit("apparel"),
                         CPR.resolve_category_policy("apparel").title_hard_limit)


class SharedAcrossConsumers(unittest.TestCase):
    def test_generator_resolves_title_limit_from_registry(self):
        d = lean_project()
        r = LG.generate(d)
        self.assertEqual(r["title_limit"], CPR.get_title_hard_limit("apparel"))
        self.assertEqual(r["listing"]["category_policy"]["title_hard_limit"],
                         CPR.get_title_hard_limit("apparel"))

    def test_validator_resolves_the_same_limit(self):
        limit = CPR.get_title_hard_limit("apparel")
        pol = LV.resolve_listing_policy({"category": "apparel"})
        self.assertEqual(pol.title_hard_limit, limit)

    def test_dashboard_receives_the_same_limit(self):
        d = lean_project()
        snap = DASH.snapshot(d)
        self.assertEqual(snap["category_policy"]["title_hard_limit"], CPR.get_title_hard_limit("apparel"))

    def test_ui_data_and_backend_validation_limits_match(self):
        d = lean_project()
        snap = DASH.snapshot(d)
        ui_limit = snap["category_policy"]["title_hard_limit"]           # what the UI counter uses
        ok, errs = DASH.validate_listing({"category": "apparel", "title": "P" * (ui_limit + 1),
                                          "bullets": ["a"]})              # what backend validation uses
        self.assertFalse(ok)
        self.assertTrue(any(str(ui_limit) in e for e in errs))

    def test_changing_one_policy_updates_generator_validator_and_ui(self):
        """The ACT-004 acceptance test: one fixture change must move all three consumers together."""
        reg = CPR.load_policy_registry(registry_with_apparel_limit(80))
        # generator
        d = lean_project()
        r = LG.generate(d, policy_registry=reg)
        self.assertEqual(r["title_limit"], 80)
        # validator (same shared API, injected registry)
        self.assertEqual(LV.resolve_listing_policy({"category": "apparel"}, registry=reg).title_hard_limit, 80)
        # UI counter / dashboard validation
        self.assertEqual(CPR.resolve_category_policy("apparel", registry=reg).title_hard_limit, 80)
        ok, errs = DASH.validate_listing({"category": "apparel", "title": "P" * 79, "bullets": ["a"]},
                                         registry=reg)
        self.assertTrue(ok, errs)                                        # 79 <= 80 now passes


class NoHardcodedRule(unittest.TestCase):
    """No active consumer may still contain a hardcoded universal 75-character title rule (ACT-004/021)."""

    def _src(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_generator_has_no_hardcoded_title_or_backend_constant(self):
        s = self._src("listing", "listing_generator.py")
        self.assertNotIn("TITLE_MAX = 75", s)
        self.assertNotIn("TITLE_MAX=75", s)
        self.assertNotIn("BACKEND_MAX_BYTES = 249", s)

    def test_dashboard_app_has_no_hardcoded_title_constant(self):
        s = self._src("dashboard", "app.py")
        self.assertNotIn("TITLE_MAX = 75", s)
        self.assertNotIn("TITLE_MAX=75", s)

    def test_validator_does_not_read_a_hardcoded_title_max(self):
        s = self._src("compliance", "listing_validate.py")
        self.assertNotIn('title_max",75', s)
        self.assertNotIn('title_max", 75', s)

    def test_template_reads_limits_from_payload_not_hardcoded(self):
        s = self._src("dashboard", "templates", "index.html")
        self.assertNotIn("tlen>75", s)
        self.assertNotIn("/75 chars", s)
        self.assertNotIn("length>125", s)
        self.assertNotIn("/125", s)

    def test_category_config_no_longer_holds_the_title_limit(self):
        cfg = json.load(open(os.path.join(ROOT, "compliance", "category_config.json"), encoding="utf-8"))
        for cat, rules in cfg.get("categories", {}).items():
            self.assertNotIn("title_max", rules, f"{cat} still carries a second title authority")
            self.assertNotIn("backend_max_bytes", rules)


if __name__ == "__main__":
    unittest.main(verbosity=2)
