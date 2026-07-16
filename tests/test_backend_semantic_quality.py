#!/usr/bin/env python3
"""Session 5A.1 — phrase-aware semantic quality for the backend optimizer (ACT-009 hardening).

Proves the optimizer decomposes each keyword into semantic UNITS and publishes a token independently only
when it has clear searchable meaning: malformed/merged tokens, orphan stopwords, unexplained numbers,
broken phrase fragments, unverified/conflicting garment types and unsupported occasions are excluded (each
with a reason), while approved abbreviations and specialty phrases keep their meaning and provenance. Also
proves the PageAuditor blocks semantic token soup, and that the regenerated T2 backend carries no junk and
is deterministic.
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "listing"))
import keyword_source_adapter as KSA
import category_policy_registry as CPR
import product_fact_loader as PFL
import backend_optimizer as BO
import page_auditor as PA

T2 = os.path.join(ROOT, "runs", "T2")
# the identity the generator uses for runs/T2 (title / primary keyword; no product-facts.json present).
T2_TITLE = "Personalized Nurse Sweatshirt"
T2_PRIMARY = "personalized nurse sweatshirt"
# every junk token the Session 5A.1 patch must keep out of a copy-ready backend.
T2_JUNK = ("registeredtom", "this", "the", "for", "up", "5", "prays", "face")


def _prod(kw, tier="TIER_A_EXACT_MONEY", risk=None, owner=None, sv=100, cov=50.0, rank=5, norm=None):
    r = {"keyword_exact": kw, "tier": tier, "risk_flags": risk or [], "search_volume": sv,
         "simple_competitor_coverage_pct": cov, "best_rank": rank}
    if owner:
        r["owner_status"] = owner
    if norm:
        r["keyword_normalized"] = norm
    return r


def make_source(records, schema="production", run_id="run_sq"):
    d = tempfile.mkdtemp()
    fname, declared = (("MASTER-KEYWORDS.json", "MASTER_KEYWORDS_PRODUCTION") if schema == "production"
                       else ("MASTER-KEYWORDS-LEAN.json", "MASTER_KEYWORDS_LEAN"))
    doc = {"run_metadata": {"run_id": run_id, "schema_version": "1.0.0", "source_schema": declared},
           "keywords": records}
    with open(os.path.join(d, fname), "w", encoding="utf-8") as f:
        json.dump(doc, f)
    return KSA.load_keyword_source(d)


def facts(**fields):
    """A NormalizedProductFacts with the given VERIFIED fields (value + status=verified)."""
    d = tempfile.mkdtemp()
    product = {k: {"value": v, "status": "verified"} for k, v in fields.items()}
    with open(os.path.join(d, "product-facts.json"), "w", encoding="utf-8") as f:
        json.dump({"schema_version": "1.0.0", "product": product}, f)
    return PFL.load_product_facts(folder=d)


def apparel(ceiling=None):
    ov = {"backend_byte_ceiling": ceiling} if ceiling is not None else None
    return CPR.resolve_category_policy("apparel", owner_override=ov)


def opt(records, **kw):
    return BO.optimize_backend(make_source(records), policy=apparel(kw.pop("ceiling", None)), **kw)


def _excl_reasons(o, term):
    return {e["exclusion_reason"] for e in o.excluded_terms if e["term"] == term}


# ---------------------------------------------------------------- PATCH 2: malformed / low-info
class MalformedFilters(unittest.TestCase):
    def test_1_registeredtom_is_suspicious_concatenation(self):
        o = opt([_prod("registeredtom nurse gift")], title="Nurse Sweatshirt")
        self.assertNotIn("registeredtom", o.backend_search_terms_string.split())
        self.assertIn(BO.EXCL_SUSPICIOUS_CONCAT, _excl_reasons(o, "registeredtom"))

    def test_2_orphan_stopwords_rejected(self):
        o = opt([_prod("rn sweatshirt for women"), _prod("nurse sweater zip up")], title="Nurse Sweatshirt")
        toks = o.backend_search_terms_string.split()
        for sw in ("for", "up", "and", "the", "this"):
            self.assertNotIn(sw, toks)
        self.assertIn(BO.EXCL_ORPHAN_STOPWORD, o.excluded_summary)

    def test_3_standalone_numbers_rejected(self):
        o = opt([_prod("5 gifts for nurses"), _prod("2026 nurse sweatshirt")], title="Nurse Sweatshirt")
        toks = o.backend_search_terms_string.split()
        self.assertNotIn("5", toks)
        self.assertNotIn("2026", toks)
        self.assertIn(BO.EXCL_UNEXPLAINED_NUMBER, o.excluded_summary)

    def test_4_recognized_abbreviations_remain_eligible(self):
        o = opt([_prod("rn cna lpn np icu nicu er nurse")])
        toks = o.backend_search_terms_string.split()
        for ab in ("rn", "cna", "lpn", "np", "icu", "nicu", "er"):
            self.assertIn(ab, toks)

    def test_5_unknown_abbreviations_rejected(self):
        o = opt([_prod("xqz nurse gift"), _prod("zzt rn present")])
        toks = o.backend_search_terms_string.split()
        self.assertNotIn("xqz", toks)
        self.assertNotIn("zzt", toks)
        self.assertIn(BO.EXCL_UNRECOGNIZED_ABBREVIATION, o.excluded_summary)


# ---------------------------------------------------------------- PATCH 1/4: phrase integrity
class PhraseIntegrity(unittest.TestCase):
    def test_6_complete_specialty_phrase_retains_meaning(self):
        o = opt([_prod("labor and delivery nurse sweatshirt")], title="Nurse Sweatshirt")
        by_term = {t["term"]: t for t in o.included_terms}
        self.assertIn("labor", by_term)
        self.assertIn("delivery", by_term)
        for term in ("labor", "delivery"):
            self.assertEqual(by_term[term]["unit_type"], "specialty_phrase")
            self.assertIn("labor and delivery", by_term[term]["unit"])
            self.assertEqual(by_term[term]["inclusion_reason"], BO.INCL_SPECIALTY_PHRASE)
        # the interior stopword never publishes as a standalone token.
        self.assertNotIn("and", o.backend_search_terms_string.split())

    def test_7_broken_phrase_fragments_rejected(self):
        o = opt([_prod("the nurse face sweatshirt"), _prod("this nurse prays sweatshirt"),
                 _prod("nurse face crewneck")], title="Nurse Sweatshirt")
        toks = o.backend_search_terms_string.split()
        self.assertNotIn("face", toks)
        self.assertNotIn("prays", toks)
        self.assertIn(BO.EXCL_BROKEN_FRAGMENT, o.excluded_summary)

    def test_12_partial_extraction_cannot_change_audience_meaning(self):
        # "delivery" only ever publishes tied to its labor-and-delivery phrase — never re-attributed to an
        # unrelated keyword, so a partial extraction cannot invent a different audience meaning.
        o = opt([_prod("labor and delivery nurse sweatshirt"),
                 _prod("food delivery driver shirt", risk=["wrong_audience"])], title="Nurse Sweatshirt")
        by_term = {t["term"]: t for t in o.included_terms}
        self.assertIn("delivery", by_term)
        self.assertEqual(by_term["delivery"]["source_keyword"], "labor and delivery nurse sweatshirt")
        self.assertEqual(by_term["delivery"]["unit_type"], "specialty_phrase")


# ---------------------------------------------------------------- PATCH 3: product-type conflicts
class ProductTypeConflicts(unittest.TestCase):
    def test_8_hoodie_rejected_for_non_hooded_product(self):
        o = opt([_prod("nurse hoodie gift")], title="Nurse Sweatshirt", primary_keyword="nurse sweatshirt",
                product_facts=facts(garment_type="sweatshirt"))
        self.assertNotIn("hoodie", o.backend_search_terms_string.split())
        self.assertIn(BO.EXCL_PRODUCT_TYPE_CONFLICT, _excl_reasons(o, "hoodie"))
        self.assertIn("hoodie", o.product_type_results["conflict_excluded"])

    def test_8b_hoodie_publishes_when_offered_as_hoodie(self):
        # identity from verified facts + primary keyword (not the title, so it is not "already covered").
        o = opt([_prod("nurse hoodie gift")], title="Nurse Gift", primary_keyword="nurse hoodie",
                product_facts=facts(garment_type="hoodie"))
        self.assertIn("hoodie", o.backend_search_terms_string.split())

    def test_9_crewneck_requires_verified_neckline_or_compatible_identity(self):
        # compatible identity (sweatshirt) -> crewneck publishes.
        o_ok = opt([_prod("nurse crewneck gift")], title="Nurse Sweatshirt")
        self.assertIn("crewneck", o_ok.backend_search_terms_string.split())
        # a non-compatible identity with no verified neckline -> crewneck excluded as unverified.
        o_no = opt([_prod("nurse crewneck gift")], title="Nurse Tank Top",
                   primary_keyword="nurse tank top", product_facts=facts(garment_type="tank top"))
        self.assertNotIn("crewneck", o_no.backend_search_terms_string.split())
        self.assertIn(BO.EXCL_PRODUCT_TYPE_UNVERIFIED, _excl_reasons(o_no, "crewneck"))

    def test_10_quarter_zip_requires_verified_support(self):
        o_no = opt([_prod("nurse quarter zip gift")], title="Nurse Sweatshirt",
                   primary_keyword="nurse sweatshirt", product_facts=facts(garment_type="sweatshirt"))
        toks = o_no.backend_search_terms_string.split()
        self.assertNotIn("quarter", toks)
        self.assertNotIn("zip", toks)
        self.assertIn(BO.EXCL_PRODUCT_TYPE_UNVERIFIED, _excl_reasons(o_no, "quarter"))
        # verified quarter-zip garment (identity from facts + primary keyword) -> the attribute publishes.
        o_ok = opt([_prod("nurse quarter zip gift")], title="Nurse Gift",
                   primary_keyword="nurse quarter zip", product_facts=facts(garment_type="quarter zip"))
        self.assertIn("quarter", o_ok.backend_search_terms_string.split())


# ---------------------------------------------------------------- PATCH 4: occasion validation
class OccasionValidation(unittest.TestCase):
    def test_11_unsupported_holiday_and_school_rejected(self):
        o = opt([_prod("nurse holiday sweatshirt"), _prod("nursing school sweatshirt")],
                title="Nurse Sweatshirt", primary_keyword="nurse sweatshirt",
                product_facts=facts(garment_type="sweatshirt"))
        toks = o.backend_search_terms_string.split()
        self.assertNotIn("holiday", toks)
        self.assertNotIn("school", toks)
        self.assertIn(BO.EXCL_UNSUPPORTED_OCCASION, o.excluded_summary)
        for term in ("holiday", "school"):
            self.assertIn(term, o.audience_occasion_results["rejected_occasions"])

    def test_11b_supported_occasion_publishes(self):
        o = opt([_prod("nurse holiday sweatshirt")], title="Nurse Sweatshirt",
                primary_keyword="nurse sweatshirt",
                product_facts=facts(garment_type="sweatshirt", occasion="holiday"))
        self.assertIn("holiday", o.backend_search_terms_string.split())


# ---------------------------------------------------------------- PATCH 5: quality-first byte use
class QualityFirstByteUse(unittest.TestCase):
    def test_13_optimizer_may_leave_unused_bytes(self):
        o = opt([_prod("rn nurse gift")], title="Nurse Sweatshirt")   # few useful tokens, huge ceiling
        self.assertLess(o.bytes_used, o.byte_ceiling)
        self.assertGreater(o.bytes_remaining, 0)
        self.assertTrue(o.semantic_quality["stopped_before_ceiling"])

    def test_14_low_quality_terms_not_added_to_fill_ceiling(self):
        # with the low-quality threshold on, a generic descriptor is not published even though bytes remain.
        o = opt([_prod("rn nurse"), _prod("nurse bulk gift set", tier="TIER_B_CORE_NICHE")],
                title="Nurse Sweatshirt", min_semantic_score=2)
        toks = o.backend_search_terms_string.split()
        for weak in ("bulk", "gift", "set"):
            self.assertNotIn(weak, toks)
        self.assertGreater(o.bytes_remaining, 0)
        self.assertIn(BO.EXCL_LOW_INCREMENTAL_COVERAGE, o.excluded_summary)


# ---------------------------------------------------------------- provenance / reasons completeness
class ProvenanceCompleteness(unittest.TestCase):
    def setUp(self):
        self.o = opt([_prod("labor and delivery nurse sweatshirt"), _prod("nurse hoodie"),
                      _prod("5 gifts for nurses"), _prod("the nurse face sweatshirt")],
                     title="Nurse Sweatshirt", primary_keyword="nurse sweatshirt",
                     product_facts=facts(garment_type="sweatshirt"))

    def test_15_phrase_provenance_is_complete(self):
        self.assertTrue(self.o.included_terms)
        for t in self.o.included_terms:
            for field in ("source_keyword", "unit", "unit_type", "source_sha256", "inclusion_reason"):
                self.assertTrue(t.get(field), f"included term missing {field}: {t}")

    def test_16_all_exclusions_have_reasons(self):
        for e in self.o.excluded_terms:
            self.assertTrue(e.get("exclusion_reason"))
        self.assertEqual(sum(self.o.excluded_summary.values()), self.o.excluded_total)


# ---------------------------------------------------------------- PATCH 6: auditor hardening
class AuditorSemanticGating(unittest.TestCase):
    def _listing(self, backend, audit=None):
        L = {"category": "apparel", "title": "Nurse Sweatshirt", "bullets": ["b"], "description": "d",
             "backend": backend}
        if audit is not None:
            L["backend_audit"] = audit
            L["keyword_source_sha256"] = "abc"
        return L

    def test_17_pageauditor_blocks_semantic_token_soup(self):
        soup = "nurse this the for up 5 registerednurse rn"
        audit = PA.audit_listing(self._listing(soup))
        self.assertEqual(audit["publishability"], PA.BLOCKED_UNSAFE)
        cats = {h["category"] for h in audit["hard_failures"]}
        self.assertIn("backend_orphan_stopword", cats)
        self.assertIn("backend_unexplained_number", cats)
        self.assertIn("backend_suspicious_concatenation", cats)
        self.assertEqual(audit["backend_results"]["semantic_quality"], "FAIL")

    def test_17b_conflicting_garment_in_audit_blocks(self):
        audit = {"bytes_used": len("hoodie".encode()), "byte_ceiling": 249, "included_count": 1,
                 "included_terms": [{"term": "hoodie", "inclusion_reason": "X", "source_keyword": "k",
                                     "source_sha256": "abc", "blocking_risks": [], "unit": "hoodie",
                                     "unit_type": "product_type",
                                     "product_type_result": BO.PT_CONFLICT}],
                 "excluded_count": 0, "source_hashes": {"keyword_source_sha256": "abc"}}
        res = PA.audit_listing(self._listing("hoodie", audit))
        cats = {h["category"] for h in res["hard_failures"]}
        self.assertIn("backend_product_type_conflict", cats)
        self.assertEqual(res["backend_results"]["product_compatibility"], "FAIL")

    def test_17c_clean_backend_reports_all_subresults_pass(self):
        o = opt([_prod("registered nurse crewneck rn")], title="Nurse Sweatshirt",
                primary_keyword="nurse sweatshirt", product_facts=facts(garment_type="sweatshirt"))
        L = {"category": "apparel", "title": "Nurse Sweatshirt", "bullets": ["b"], "description": "d",
             "backend": o.backend_search_terms_string, "backend_audit": o.audit(),
             "keyword_source_sha256": o.source_hashes["keyword_source_sha256"]}
        res = PA.audit_listing(L)
        br = res["backend_results"]
        for k in ("byte_safety", "semantic_quality", "phrase_integrity", "product_compatibility",
                  "audience_occasion_compatibility", "risk_safety"):
            self.assertEqual(br[k], "PASS", f"{k} should PASS for a clean backend")


# ---------------------------------------------------------------- T2 regeneration (derived, not hardcoded)
class T2Regeneration(unittest.TestCase):
    def _t2(self, **kw):
        src = KSA.load_keyword_source(T2)
        return BO.optimize_backend(src, policy=apparel(), title=T2_TITLE, primary_keyword=T2_PRIMARY,
                                   product_facts=PFL.load_product_facts(folder=T2), **kw)

    def test_18_t2_contains_no_identified_junk_terms(self):
        toks = self._t2().backend_search_terms_string.split()
        for junk in T2_JUNK:
            self.assertNotIn(junk, toks)
        # no unsupported garment type or occasion survived either.
        for bad in ("hoodie", "sweater", "quarter", "zip", "holiday", "school", "week"):
            self.assertNotIn(bad, toks)

    def test_18b_t2_is_byte_safe_and_semantically_clean(self):
        o = self._t2()
        self.assertLessEqual(o.bytes_used, o.byte_ceiling)
        L = {"category": "apparel", "title": T2_TITLE, "bullets": ["b"], "description": "d",
             "backend": o.backend_search_terms_string, "backend_audit": o.audit(),
             "keyword_source_sha256": o.source_hashes["keyword_source_sha256"]}
        res = PA.audit_listing(L)
        self.assertEqual(res["backend_results"]["semantic_quality"], "PASS")
        self.assertEqual(res["backend_results"]["product_compatibility"], "PASS")

    def test_19_t2_is_deterministic(self):
        a, b = self._t2(), self._t2()
        self.assertEqual(a.backend_search_terms_string, b.backend_search_terms_string)
        self.assertEqual(a.content_sha256(), b.content_sha256())

    def test_19b_t2_preserves_specialty_phrases(self):
        toks = self._t2().backend_search_terms_string.split()
        # meaningful nurse specialty tokens survive.
        for kept in ("labor", "delivery", "registered", "practitioner", "postpartum"):
            self.assertIn(kept, toks)


if __name__ == "__main__":
    unittest.main(verbosity=2)
