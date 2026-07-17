#!/usr/bin/env python3
"""Session 5A.2 — atomic semantic units, concept dedup, verified-attribute + audience/occasion gating,
non-zero quality defaults, and PageAuditor enforcement for the backend optimizer (ACT-009 hardening).

Proves the backend is assembled from complete semantic UNITS (a phrase publishes wholly and contiguously or
not at all), that aliases/inflections dedup to one concept, that factual attributes require verified
evidence, that audiences/recipients/occasions publish only as supported units, that the production quality
floor is non-zero, and that the PageAuditor blocks every one of these violations. The regenerated T2 carries
none of the identified unsupported terms and is deterministic; item highlights are unaffected.
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("listing", "research", "dashboard"):
    sys.path.insert(0, os.path.join(ROOT, _d))
import keyword_source_adapter as KSA
import category_policy_registry as CPR
import product_fact_loader as PFL
import backend_optimizer as BO
import page_auditor as PA
import listing_generator as LG

T2 = os.path.join(ROOT, "runs", "T2")
T2_TITLE = "Personalized Nurse Sweatshirt"
T2_PRIMARY = "personalized nurse sweatshirt"


def _prod(kw, tier="TIER_A_EXACT_MONEY", risk=None, owner=None, sv=100, cov=50.0, rank=5, norm=None):
    r = {"keyword_exact": kw, "tier": tier, "risk_flags": risk or [], "search_volume": sv,
         "simple_competitor_coverage_pct": cov, "best_rank": rank}
    if owner:
        r["owner_status"] = owner
    if norm:
        r["keyword_normalized"] = norm
    return r


def make_source(records, schema="production", run_id="run_pi"):
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


def _units_of_type(o, ut):
    return [u for u in o.included_units if u["unit_type"] == ut]


def _listing(backend, audit=None, title="Nurse Sweatshirt"):
    L = {"category": "apparel", "title": title, "bullets": ["b"], "description": "d", "backend": backend}
    if audit is not None:
        L["backend_audit"] = audit
        L["keyword_source_sha256"] = (audit.get("source_hashes") or {}).get("keyword_source_sha256")
    return L


# ---------------------------------------------------------------- 1-3 phrase integrity + connector
class PhraseIntegrityAndConnector(unittest.TestCase):
    def test_1_labor_and_delivery_nurse_stays_atomic_and_contiguous(self):
        o = opt([_prod("labor and delivery nurse sweatshirt")], title="Nurse Sweatshirt")
        self.assertIn("labor and delivery nurse", o.backend_search_terms_string)
        units = [u for u in o.included_units if "labor" in u["tokens"]]
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["text"], "labor and delivery nurse")
        self.assertEqual(units[0]["tokens"], ["labor", "and", "delivery", "nurse"])
        self.assertEqual(units[0]["unit_type"], "specialty_phrase")

    def test_2_phrase_interior_and_is_preserved(self):
        o = opt([_prod("labor and delivery nurse sweatshirt")], title="Nurse Sweatshirt")
        toks = o.backend_search_terms_string.split()
        self.assertIn("and", toks)
        # the "and" is tied to its phrase unit, never a standalone term.
        and_terms = [t for t in o.included_terms if t["term"] == "and"]
        self.assertTrue(and_terms)
        for t in and_terms:
            self.assertEqual(t["unit_type"], "specialty_phrase")

    def test_3_orphan_and_is_rejected(self):
        # an "and" that is NOT interior to an approved phrase is an orphan stopword, never published.
        o = opt([_prod("nurse and gift shop tee")], title="Nurse Sweatshirt")
        self.assertNotIn("and", o.backend_search_terms_string.split())
        self.assertIn(BO.EXCL_ORPHAN_STOPWORD, o.excluded_summary)


# ---------------------------------------------------------------- 4-6 no broken fragments
class NoBrokenFragments(unittest.TestCase):
    def test_4_registered_nurse_cannot_become_standalone_registered(self):
        o = opt([_prod("registered nurse crewneck")], title="Nurse Sweatshirt")
        for t in o.included_terms:
            if t["term"] == "registered":
                self.assertEqual(t["unit_type"], "specialty_phrase")
                self.assertIn("registered nurse", t["unit"])
        self.assertIn("registered nurse", o.backend_search_terms_string)
        # a bare "registered" with no nurse is a broken fragment.
        o2 = opt([_prod("registered pullover gift")], title="Nurse Sweatshirt")
        self.assertNotIn("registered", o2.backend_search_terms_string.split())
        self.assertIn(BO.EXCL_BROKEN_FRAGMENT, _excl_reasons(o2, "registered"))

    def test_5_future_nurse_cannot_become_standalone_future(self):
        o = opt([_prod("future nurse sweatshirt")], title="Nurse Sweatshirt")
        self.assertIn("future nurse", o.backend_search_terms_string)
        for t in o.included_terms:
            if t["term"] == "future":
                self.assertEqual(t["unit_type"], "specialty_phrase")
                self.assertIn("future nurse", t["unit"])
        o2 = opt([_prod("future crewneck gift")], title="Nurse Sweatshirt")
        self.assertNotIn("future", o2.backend_search_terms_string.split())

    def test_6_emergency_room_nurse_cannot_become_standalone_emergency(self):
        o = opt([_prod("emergency room nurse sweatshirt")], title="Nurse Sweatshirt")
        self.assertIn("emergency room nurse", o.backend_search_terms_string)
        units = [u for u in o.included_units if "emergency" in u["tokens"]]
        self.assertTrue(units)
        for u in units:
            self.assertEqual(u["unit_type"], "specialty_phrase")
            self.assertEqual(u["text"], "emergency room nurse")


# ---------------------------------------------------------------- 7-8 concept dedup
class ConceptDeduplication(unittest.TestCase):
    def test_7_crewneck_and_crew_neck_dedupe_to_one_concept(self):
        o = opt([_prod("nurse crewneck"), _prod("nurse crew neck", tier="TIER_B_CORE_NICHE")],
                title="Nurse Sweatshirt")
        concepts = [u["normalized_concept"] for u in o.included_units]
        self.assertEqual(concepts.count("crewneck"), 1)
        self.assertIn(BO.EXCL_DUPLICATE_CONCEPT, o.excluded_summary)

    def test_8_women_and_womens_dedupe_to_one_concept(self):
        o = opt([_prod("women nurse gift"), _prod("womens nurse gift", tier="TIER_B_CORE_NICHE")],
                title="Nurse Sweatshirt", primary_keyword="nurse sweatshirt",
                product_facts=facts(audience="women"))
        concepts = [u["normalized_concept"] for u in o.included_units]
        self.assertEqual(concepts.count("women"), 1)
        self.assertIn(BO.EXCL_DUPLICATE_CONCEPT, o.excluded_summary)


# ---------------------------------------------------------------- 9-15 verified-attribute gating
class VerifiedAttributeGating(unittest.TestCase):
    def test_9_unverified_color_excluded(self):
        o = opt([_prod("navy nurse crewneck")], title="Nurse Sweatshirt")
        self.assertNotIn("navy", o.backend_search_terms_string.split())
        self.assertIn(BO.EXCL_COLOR_UNVERIFIED, _excl_reasons(o, "navy"))

    def test_10_verified_color_may_publish(self):
        o = opt([_prod("navy nurse crewneck")], title="Nurse Sweatshirt",
                primary_keyword="nurse sweatshirt", product_facts=facts(color_options="navy, black"))
        self.assertIn("navy", o.backend_search_terms_string.split())
        u = [u for u in o.included_units if u["tokens"] == ["navy"]][0]
        self.assertEqual(u["verification_state"], "VERIFIED_FACT")
        self.assertIn("color_options", u["product_fact_fields"])

    def test_11_unverified_material_excluded(self):
        o = opt([_prod("fleece nurse crewneck")], title="Nurse Sweatshirt")
        self.assertNotIn("fleece", o.backend_search_terms_string.split())
        self.assertIn(BO.EXCL_MATERIAL_UNVERIFIED, _excl_reasons(o, "fleece"))

    def test_12_verified_material_may_publish(self):
        o = opt([_prod("fleece nurse crewneck")], title="Nurse Sweatshirt",
                primary_keyword="nurse sweatshirt", product_facts=facts(material="fleece"))
        self.assertIn("fleece", o.backend_search_terms_string.split())

    def test_13_embroidery_requires_verified_decoration(self):
        o_no = opt([_prod("embroidered nurse crewneck")], title="Nurse Sweatshirt")
        self.assertNotIn("embroidered", o_no.backend_search_terms_string.split())
        self.assertIn(BO.EXCL_DECORATION_METHOD_UNVERIFIED, _excl_reasons(o_no, "embroidered"))
        o_ok = opt([_prod("embroidered nurse crewneck")], title="Nurse Sweatshirt",
                   primary_keyword="nurse sweatshirt", product_facts=facts(decoration_method="embroidery"))
        self.assertIn("embroidered", o_ok.backend_search_terms_string.split())

    def test_14_custom_requires_verified_personalization(self):
        o_no = opt([_prod("custom nurse crewneck")], title="Nurse Sweatshirt")
        self.assertNotIn("custom", o_no.backend_search_terms_string.split())
        self.assertIn(BO.EXCL_PERSONALIZATION_UNVERIFIED, _excl_reasons(o_no, "custom"))
        o_ok = opt([_prod("custom nurse crewneck")], title="Nurse Sweatshirt",
                   primary_keyword="nurse sweatshirt", product_facts=facts(personalization_fields="name"))
        self.assertIn("custom", o_ok.backend_search_terms_string.split())

    def test_15_bulk_requires_verified_quantity(self):
        o_no = opt([_prod("bulk nurse crewneck set")], title="Nurse Sweatshirt")
        self.assertNotIn("bulk", o_no.backend_search_terms_string.split())
        self.assertIn(BO.EXCL_QUANTITY_OFFER_UNVERIFIED, _excl_reasons(o_no, "bulk"))
        o_ok = opt([_prod("bulk nurse crewneck")], title="Nurse Sweatshirt",
                   primary_keyword="nurse sweatshirt", product_facts=facts(packaging="bulk pack"))
        self.assertIn("bulk", o_ok.backend_search_terms_string.split())


# ---------------------------------------------------------------- 16-18 audience / recipient / gift gating
class AudienceRecipientOccasionGating(unittest.TestCase):
    def test_16_men_conflicts_with_verified_women_only_product(self):
        o = opt([_prod("nurse crewneck for men")], title="Nurse Sweatshirt",
                primary_keyword="nurse sweatshirt", product_facts=facts(audience="women"))
        self.assertNotIn("men", o.backend_search_terms_string.split())
        self.assertIn(BO.EXCL_AUDIENCE_CONFLICT, _excl_reasons(o, "men"))

    def test_17_graduates_require_supported_context(self):
        o_no = opt([_prod("nursing graduate crewneck")], title="Nurse Sweatshirt",
                   primary_keyword="nurse sweatshirt")
        self.assertNotIn("graduate", o_no.backend_search_terms_string.split())
        self.assertIn(BO.EXCL_RECIPIENT_CONTEXT_REQUIRED, _excl_reasons(o_no, "graduate"))
        o_ok = opt([_prod("nursing graduate crewneck")], title="Nurse Sweatshirt",
                   primary_keyword="nurse sweatshirt", product_facts=facts(occasion="graduation"))
        self.assertIn("graduate", o_ok.backend_search_terms_string.split())

    def test_18_gifts_require_a_complete_gift_intent_unit(self):
        o_no = opt([_prod("cheap gifts wholesale")], title="Nurse Sweatshirt")
        self.assertNotIn("gifts", o_no.backend_search_terms_string.split())
        self.assertIn(BO.EXCL_RECIPIENT_CONTEXT_REQUIRED, _excl_reasons(o_no, "gifts"))
        o_ok = opt([_prod("nurse gift crewneck")], title="Nurse Sweatshirt")
        self.assertIn("nurse gift", o_ok.backend_search_terms_string)
        self.assertTrue(_units_of_type(o_ok, "gift"))


# ---------------------------------------------------------------- 19-21 quality-first + reconstruction
class QualityFirstAndReconstruction(unittest.TestCase):
    def test_19_default_semantic_threshold_is_non_zero(self):
        import inspect
        sig = inspect.signature(BO.optimize_backend)
        self.assertGreater(sig.parameters["min_semantic_score"].default, 0)
        self.assertGreater(BO.PRODUCTION_MIN_SEMANTIC_SCORE, 0)
        self.assertGreater(BO.PRODUCTION_MIN_INCREMENTAL_COVERAGE, 0)

    def test_20_useful_units_may_leave_unused_bytes(self):
        o = opt([_prod("rn nurse crewneck")], title="Nurse Sweatshirt")   # few useful units, huge ceiling
        self.assertLess(o.bytes_used, o.byte_ceiling)
        self.assertGreater(o.bytes_remaining, 0)
        self.assertTrue(o.semantic_quality["stopped_before_ceiling"])

    def test_21_final_string_reconstructs_exactly_from_units(self):
        o = opt([_prod("labor and delivery nurse sweatshirt"), _prod("rn crewneck"),
                 _prod("nurse practitioner gift")], title="Nurse Sweatshirt",
                primary_keyword="nurse sweatshirt", product_facts=facts(garment_type="sweatshirt"))
        self.assertEqual(o.backend_search_terms_string,
                         " ".join(u["text"] for u in o.included_units))
        # included_terms is derived from the units: string tokens equal the term list, in order.
        self.assertEqual(o.backend_search_terms_string.split(),
                         [t["term"] for t in o.included_terms])


# ---------------------------------------------------------------- 22-24 PageAuditor enforcement
class PageAuditorEnforcement(unittest.TestCase):
    def _audit(self, backend, units, terms):
        audit = {"bytes_used": len(backend.encode()), "byte_ceiling": 249,
                 "included_count": len(terms), "included_terms": terms, "included_units": units,
                 "excluded_count": 0, "source_hashes": {"keyword_source_sha256": "abc"}}
        return PA.audit_listing(_listing(backend, audit))

    def test_22_partial_unit_emission_blocks_publication(self):
        # the audit claims the atomic unit "labor and delivery nurse" but the string only carries a broken
        # "labor delivery nurse" — the string cannot be reconstructed from the unit, so it is blocked.
        terms = [{"term": t, "inclusion_reason": "X", "source_keyword": "k", "source_sha256": "abc",
                  "blocking_risks": [], "unit": "labor and delivery nurse", "unit_type": "specialty_phrase"}
                 for t in ("labor", "delivery", "nurse")]
        units = [{"text": "labor and delivery nurse", "tokens": ["labor", "and", "delivery", "nurse"],
                  "normalized_concept": "labor delivery", "unit_type": "specialty_phrase",
                  "verification_state": "NOT_REQUIRED", "product_fact_fields": [], "claim_ids": [],
                  "semantic_score": 2, "incremental_coverage_score": 2}]
        res = self._audit("labor delivery nurse", units, terms)
        self.assertEqual(res["publishability"], PA.BLOCKED_UNSAFE)
        cats = {h["category"] for h in res["hard_failures"]}
        self.assertTrue({"backend_unit_reconstruction", "backend_broken_phrase"} & cats)

    def test_23_duplicate_semantic_concepts_block_publication(self):
        terms = [{"term": t, "inclusion_reason": "X", "source_keyword": "k", "source_sha256": "abc",
                  "blocking_risks": [], "unit": u, "unit_type": "product_type"}
                 for t, u in (("crewneck", "crewneck"), ("crew", "crew neck"), ("neck", "crew neck"))]
        units = [{"text": "crewneck", "tokens": ["crewneck"], "normalized_concept": "crewneck",
                  "unit_type": "product_type", "verification_state": "NOT_REQUIRED",
                  "product_fact_fields": [], "claim_ids": [], "semantic_score": 2,
                  "incremental_coverage_score": 1},
                 {"text": "crew neck", "tokens": ["crew", "neck"], "normalized_concept": "crewneck",
                  "unit_type": "product_type", "verification_state": "NOT_REQUIRED",
                  "product_fact_fields": [], "claim_ids": [], "semantic_score": 2,
                  "incremental_coverage_score": 2}]
        res = self._audit("crewneck crew neck", units, terms)
        self.assertEqual(res["publishability"], PA.BLOCKED_UNSAFE)
        self.assertIn("backend_duplicate_concept", {h["category"] for h in res["hard_failures"]})
        self.assertEqual(res["backend_results"]["concept_dedup"], "FAIL")

    def test_24_pageauditor_blocks_unsupported_factual_attribute(self):
        terms = [{"term": "navy", "inclusion_reason": "X", "source_keyword": "k", "source_sha256": "abc",
                  "blocking_risks": [], "unit": "navy", "unit_type": "attribute"}]
        units = [{"text": "navy", "tokens": ["navy"], "normalized_concept": "navy",
                  "unit_type": "attribute", "verification_state": "UNVERIFIED", "product_fact_fields": [],
                  "claim_ids": [], "semantic_score": 2, "incremental_coverage_score": 1}]
        res = self._audit("navy", units, terms)
        self.assertEqual(res["publishability"], PA.BLOCKED_UNSAFE)
        self.assertIn("backend_unverified_attribute", {h["category"] for h in res["hard_failures"]})
        self.assertEqual(res["backend_results"]["attribute_evidence"], "FAIL")


# ---------------------------------------------------------------- 25-26 T2 regeneration
class T2Regeneration(unittest.TestCase):
    UNSUPPORTED = ("fleece", "navy", "blue", "pink", "custom", "embroidered", "bulk", "men", "graduates",
                   "women", "womens", "gifts", "gift")

    def _t2(self, **kw):
        src = KSA.load_keyword_source(T2)
        return BO.optimize_backend(src, policy=apparel(), title=T2_TITLE, primary_keyword=T2_PRIMARY,
                                   product_facts=PFL.load_product_facts(folder=T2), **kw)

    def test_25_t2_contains_none_of_the_unsupported_terms(self):
        o = self._t2()
        toks = o.backend_search_terms_string.split()
        for bad in self.UNSUPPORTED:
            self.assertNotIn(bad, toks, f"unsupported term '{bad}' leaked into T2")
        # broken specialty fragments and unverified garments never survive either.
        for i in range(len(toks) - 1):
            self.assertFalse(toks[i] == "labor" and toks[i + 1] == "delivery")
        for bad in ("hoodie", "sweater", "quarter", "zip", "holiday", "school", "week"):
            self.assertNotIn(bad, toks)
        # and it is byte-safe + auditor-clean.
        self.assertLessEqual(o.bytes_used, o.byte_ceiling)
        L = {"category": "apparel", "title": T2_TITLE, "bullets": ["b"], "description": "d",
             "backend": o.backend_search_terms_string, "backend_audit": o.audit(),
             "keyword_source_sha256": o.source_hashes["keyword_source_sha256"]}
        res = PA.audit_listing(L)
        self.assertEqual(res["hard_failures"], [])
        for k in ("semantic_quality", "phrase_integrity", "product_compatibility", "attribute_evidence",
                  "concept_dedup", "unit_reconstruction", "quality_thresholds"):
            self.assertEqual(res["backend_results"][k], "PASS", f"{k} should PASS for T2")

    def test_25b_t2_preserves_complete_specialty_phrases(self):
        s = self._t2().backend_search_terms_string
        for kept in ("labor and delivery nurse", "registered nurse", "nurse practitioner", "future nurse"):
            self.assertIn(kept, s)
        for tok in ("postpartum", "rn", "cna", "lpn"):
            self.assertIn(tok, s.split())

    def test_26_t2_is_deterministic(self):
        a, b = self._t2(), self._t2()
        self.assertEqual(a.backend_search_terms_string, b.backend_search_terms_string)
        self.assertEqual(a.content_sha256(), b.content_sha256())


# ---------------------------------------------------------------- 27-28 item highlights + regression smoke
class NoCollateralRegression(unittest.TestCase):
    def _project(self):
        d = tempfile.mkdtemp()
        records = [{"keyword_exact": p, "keyword_normalized": p.lower(), "tier": "A_CORE",
                    "owner_status": "auto", "risk_flags": [], "search_volume": sv,
                    "simple_competitor_coverage_pct": cov, "best_rank": 5, "median_rank": 20,
                    "batch_support": {"B1": 4}, "observation_count": 1}
                   for p, sv, cov in [("personalized nurse sweatshirt", 1900, 90.0),
                                      ("embroidered nurse crewneck", 820, 45.0),
                                      ("rn gift pullover", 400, 30.0)]]
        doc = {"run_metadata": {"run_id": "kwrun_pi", "schema_version": "1.0.0",
                                "source_schema": "MASTER_KEYWORDS_LEAN"},
               "quality_summary": {"counts": {"A_CORE": len(records)}}, "keywords": records}
        with open(os.path.join(d, "MASTER-KEYWORDS-LEAN.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f)
        return d

    def test_27_item_highlights_outputs_remain_unchanged(self):
        d = self._project()
        r1 = LG.generate(d, "personalized nurse sweatshirt")
        r2 = LG.generate(d, "personalized nurse sweatshirt")
        # backend hardening does not touch item-highlight generation: deterministic and still empty (no facts).
        self.assertEqual(r1["item_highlights_result"].content_sha256(),
                         r2["item_highlights_result"].content_sha256())
        self.assertEqual(r1["listing"]["item_highlights_publishable"], [])
        self.assertEqual(r1["listing"]["item_highlights_capability_state"], "SUPPORTED")

    def test_28_generated_backend_has_no_hard_failures_and_reconstructs(self):
        # end-to-end: the generator's audited backend clears every backend gate and reconstructs from units,
        # with no unverified attribute leaking from a facts-free project (embroidered / personalized dropped).
        d = self._project()
        L = LG.generate(d, "personalized nurse sweatshirt")["listing"]
        audit = L["audit"]
        backend_cats = [h["category"] for h in audit["hard_failures"] if h["category"].startswith("backend")]
        self.assertEqual(backend_cats, [])
        units = L["backend_audit"]["included_units"]
        self.assertEqual(L["backend"], " ".join(u["text"] for u in units))
        toks = L["backend"].split()
        for bad in ("embroidered", "personalized", "custom"):
            self.assertNotIn(bad, toks)


if __name__ == "__main__":
    unittest.main(verbosity=2)
