#!/usr/bin/env python3
"""Tests for listing.backend_optimizer (ACT-009) — evidence-aware, byte-safe, audited backend terms."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "listing"))
import keyword_source_adapter as KSA
import category_policy_registry as CPR
import backend_optimizer as BO


def _prod(kw, tier="TIER_A_EXACT_MONEY", risk=None, owner=None, sv=100, cov=50.0, rank=5, norm=None):
    r = {"keyword_exact": kw, "tier": tier, "risk_flags": risk or [], "search_volume": sv,
         "simple_competitor_coverage_pct": cov, "best_rank": rank}
    if owner:
        r["owner_status"] = owner
    if norm:
        r["keyword_normalized"] = norm
    return r


def make_source(records, schema="production", run_id="run_test"):
    d = tempfile.mkdtemp()
    if schema == "production":
        fname, declared = "MASTER-KEYWORDS.json", "MASTER_KEYWORDS_PRODUCTION"
    else:
        fname, declared = "MASTER-KEYWORDS-LEAN.json", "MASTER_KEYWORDS_LEAN"
    doc = {"run_metadata": {"run_id": run_id, "schema_version": "1.0.0", "source_schema": declared},
           "keywords": records}
    with open(os.path.join(d, fname), "w", encoding="utf-8") as f:
        json.dump(doc, f)
    return KSA.load_keyword_source(d)


def apparel(ceiling=None):
    ov = {"backend_byte_ceiling": ceiling} if ceiling is not None else None
    return CPR.resolve_category_policy("apparel", owner_override=ov)


class ByteSafety(unittest.TestCase):
    def test_never_exceeds_category_policy_byte_ceiling(self):
        src = make_source([_prod("alpha bravo charlie delta echo foxtrot")])
        opt = BO.optimize_backend(src, policy=apparel(11))
        self.assertLessEqual(opt.bytes_used, 11)
        self.assertLessEqual(len(opt.backend_search_terms_string.encode("utf-8")), 11)

    def test_byte_limit_exclusions_recorded(self):
        src = make_source([_prod("alpha bravo charlie delta")])
        opt = BO.optimize_backend(src, policy=apparel(11))
        self.assertIn(BO.EXCL_BYTE_LIMIT, opt.excluded_summary)
        self.assertGreater(opt.excluded_summary[BO.EXCL_BYTE_LIMIT], 0)

    def test_category_policy_byte_limit_is_the_ceiling_used(self):
        src = make_source([_prod("alpha bravo")])
        opt = BO.optimize_backend(src, policy=apparel(200))
        self.assertEqual(opt.byte_ceiling, 200)
        self.assertEqual(opt.audit()["byte_ceiling"], 200)

    def test_default_ceiling_is_documented_fallback_when_no_policy(self):
        src = make_source([_prod("alpha bravo")])
        opt = BO.optimize_backend(src)
        self.assertEqual(opt.byte_ceiling, BO.DEFAULT_BACKEND_BYTE_CEILING)

    def test_no_token_is_sliced(self):
        src = make_source([_prod("alpha bravo charlie delta")])
        opt = BO.optimize_backend(src, policy=apparel(11))
        # every token in the string is a whole token from a source keyword — never a fragment.
        source_tokens = set()
        for rec in src.keywords:
            source_tokens.update(BO.tokenize(rec["keyword_normalized"]))
        for tok in opt.backend_search_terms_string.split():
            self.assertIn(tok, source_tokens)
        # and the string tokens equal the included_terms in order (no hidden/cut/reordered token).
        self.assertEqual(opt.backend_search_terms_string.split(),
                         [t["term"] for t in opt.included_terms])


class JsonAndDeterminism(unittest.TestCase):
    def test_output_is_a_json_string_never_bytes(self):
        src = make_source([_prod("alpha bravo")])
        payload = BO.optimize_backend(src, policy=apparel()).to_dict()
        self.assertIsInstance(payload["backend_search_terms_string"], str)
        json.dumps(payload)                          # must not raise (no bytes anywhere)

    def test_deterministic_string_and_hash(self):
        recs = [_prod("alpha bravo"), _prod("charlie delta", tier="TIER_B_CORE_NICHE"),
                _prod("echo foxtrot", tier="TIER_C_SUPPORTING_LONG_TAIL")]
        a = BO.optimize_backend(make_source(recs), policy=apparel())
        b = BO.optimize_backend(make_source(recs), policy=apparel())
        self.assertEqual(a.backend_search_terms_string, b.backend_search_terms_string)
        self.assertEqual(a.content_sha256(), b.content_sha256())


class RiskExclusion(unittest.TestCase):
    def setUp(self):
        self.src = make_source([
            _prod("nurse crewneck"),
            _prod("teacher sweatshirt", tier="REJECTED_NOISE"),
            _prod("teacher appreciation tee", risk=["wrong_audience"]),
            _prod("nurse coffee mug", risk=["wrong_product"]),
            _prod("nurse halloween costume", risk=["wrong_occasion"]),
            _prod("disney nurse shirt", risk=["trademark"]),
            _prod("stanley nurse cup", risk=["brand_term"]),
        ])
        self.opt = BO.optimize_backend(self.src, policy=apparel())

    def test_rejected_keyword_excluded(self):
        self.assertNotIn("teacher", self.opt.backend_search_terms_string.split())
        self.assertGreater(self.opt.risk_results["rejected"]["count"], 0)

    def test_wrong_audience_excluded(self):
        self.assertGreater(self.opt.risk_results["wrong_audience"]["count"], 0)
        self.assertNotIn("appreciation", self.opt.backend_search_terms_string.split())

    def test_wrong_product_excluded(self):
        self.assertGreater(self.opt.risk_results["wrong_product"]["count"], 0)
        self.assertNotIn("mug", self.opt.backend_search_terms_string.split())

    def test_wrong_occasion_excluded(self):
        self.assertGreater(self.opt.risk_results["wrong_occasion"]["count"], 0)
        self.assertNotIn("halloween", self.opt.backend_search_terms_string.split())

    def test_brand_trademark_ip_excluded(self):
        risky = self.opt.risk_results
        self.assertGreater(risky["trademark_or_ip"]["count"] + risky["brand_ip"]["count"], 0)
        for w in ("disney", "stanley"):
            self.assertNotIn(w, self.opt.backend_search_terms_string.split())

    def test_no_risky_term_ever_included(self):
        for t in self.opt.included_terms:
            self.assertEqual(t["blocking_risks"], [])


class OwnerApproval(unittest.TestCase):
    def test_unapproved_review_excluded_and_owner_approved_review_eligible(self):
        src = make_source([
            _prod("reviewonly widget", tier="REVIEW", norm="reviewonly widget"),
            _prod("approved gadget", tier="REVIEW", owner="approved", norm="approved gadget"),
        ], schema="lean")
        # NOTE: lean REVIEW maps to REVIEW; only an owner-approved REVIEW record is eligible.
        opt = BO.optimize_backend(src, policy=apparel())
        toks = opt.backend_search_terms_string.split()
        self.assertNotIn("reviewonly", toks)                    # unapproved REVIEW never allocates
        self.assertNotIn("widget", toks)
        self.assertIn("gadget", toks)                           # owner-approved REVIEW is eligible
        self.assertGreater(opt.risk_results["owner_approval_required"]["count"], 0)


class TierPriority(unittest.TestCase):
    def test_tier_a_selected_before_tier_c_under_byte_pressure(self):
        # "aa bb" (5 bytes) fits; "cc dd" does not. TIER_A must win the limited budget over TIER_C.
        src = make_source([_prod("cc dd", tier="TIER_C_SUPPORTING_LONG_TAIL"),
                           _prod("aa bb", tier="TIER_A_EXACT_MONEY")])
        opt = BO.optimize_backend(src, policy=apparel(5))
        toks = opt.backend_search_terms_string.split()
        self.assertIn("aa", toks)
        self.assertNotIn("cc", toks)

    def test_tier_b_between_a_and_c(self):
        src = make_source([_prod("cc", tier="TIER_C_SUPPORTING_LONG_TAIL"),
                           _prod("bb", tier="TIER_B_CORE_NICHE"),
                           _prod("aa", tier="TIER_A_EXACT_MONEY")])
        opt = BO.optimize_backend(src, policy=apparel())
        self.assertEqual(opt.backend_search_terms_string.split(), ["aa", "bb", "cc"])


class IncrementalCoverage(unittest.TestCase):
    def test_full_visible_overlap_excluded(self):
        src = make_source([_prod("nurse sweatshirt")])
        opt = BO.optimize_backend(src, policy=apparel(), title="Nurse Sweatshirt")
        self.assertEqual(opt.backend_search_terms_string, "")
        self.assertIn(BO.EXCL_ALREADY_FULLY_COVERED, opt.excluded_summary)

    def test_partial_overlap_with_new_meaning_allowed(self):
        src = make_source([_prod("nurse crewneck")])
        opt = BO.optimize_backend(src, policy=apparel(), title="Nurse Sweatshirt")
        toks = opt.backend_search_terms_string.split()
        self.assertIn("crewneck", toks)                         # novel token kept
        self.assertNotIn("nurse", toks)                         # title token dropped (already covered)
        self.assertIn("nurse", opt.visible_field_overlap)

    def test_duplicate_tokens_excluded(self):
        src = make_source([_prod("rn gift"), _prod("rn present", tier="TIER_B_CORE_NICHE")])
        opt = BO.optimize_backend(src, policy=apparel())
        toks = opt.backend_search_terms_string.split()
        self.assertEqual(toks.count("rn"), 1)
        self.assertIn(BO.EXCL_DUPLICATE_TOKEN, opt.excluded_summary)

    def test_incremental_coverage_recorded_with_reason(self):
        src = make_source([_prod("nurse crewneck")])
        opt = BO.optimize_backend(src, policy=apparel(), title="Nurse Sweatshirt")
        self.assertTrue(opt.incremental_coverage)
        for ic in opt.incremental_coverage:
            self.assertIn(ic["inclusion_reason"], BO.INCLUSION_REASONS)


class RootSaturation(unittest.TestCase):
    def test_root_saturation_controlled(self):
        src = make_source([_prod("dog walk"), _prod("dogs park", tier="TIER_B_CORE_NICHE")])
        opt = BO.optimize_backend(src, policy=apparel(), max_tokens_per_root=1)
        toks = opt.backend_search_terms_string.split()
        self.assertIn("dog", toks)
        self.assertNotIn("dogs", toks)                          # same root — saturated
        self.assertIn(BO.EXCL_ROOT_SATURATED, opt.excluded_summary)


class ProvenanceAndReasons(unittest.TestCase):
    def setUp(self):
        self.src = make_source([_prod("nurse crewneck rn"), _prod("teacher gift", tier="REJECTED_NOISE")])
        self.opt = BO.optimize_backend(self.src, policy=apparel())

    def test_every_included_term_has_provenance(self):
        self.assertTrue(self.opt.included_terms)
        for t in self.opt.included_terms:
            self.assertTrue(t["source_keyword"])
            self.assertTrue(t["normalized_keyword"])
            self.assertIn(t["normalized_tier"], KSA.TIER_STRENGTH)
            self.assertEqual(t["source_sha256"], self.src.source_sha256)
            self.assertIn("inclusion_reason", t)
            self.assertIn(t["inclusion_reason"], BO.INCLUSION_REASONS)

    def test_every_excluded_term_has_a_reason(self):
        for t in self.opt.excluded_terms:
            self.assertTrue(t.get("exclusion_reason"))
        # the summary is complete: it accounts for every excluded candidate.
        self.assertEqual(sum(self.opt.excluded_summary.values()), self.opt.excluded_total)

    def test_source_hash_consistency(self):
        self.assertEqual(self.opt.source_hashes["keyword_source_sha256"], self.src.source_sha256)
        self.assertEqual(self.opt.source_hashes["keyword_source_file"], self.src.source_file)


if __name__ == "__main__":
    unittest.main(verbosity=2)
