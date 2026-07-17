#!/usr/bin/env python3
"""
Session 6C.1 — ATOMIC CLAIM-EVIDENCE REPAIR.

Proves the shared claim-evidence authority (listing.claim_evidence) is now ATOMIC: a claim may never
hold a stronger EFFECTIVE state than its least-supported material component, so a compound text like
"A personalized gift for nurses." can never be VERIFIED / publishable. Also covers the effective-state
consumers, the backend semantic-density audit, and the committed regeneration/proof artifacts.

Pure-logic tests use synthetic product facts and run everywhere; the T2 artifact tests are gated on the
gitignored runs/T2 workspace being present (no paid data is copied into a committed fixture).
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("", "listing", "core"):
    p = os.path.join(ROOT, sub) if sub else ROOT
    if p not in sys.path:
        sys.path.insert(0, p)

import product_fact_loader as PFL          # noqa: E402
import claim_evidence as CE                # noqa: E402
import backend_optimizer as BO             # noqa: E402
import backend_density_audit as BDA        # noqa: E402
import category_policy_registry as CPR     # noqa: E402
import item_highlights_builder as IHB      # noqa: E402
import description_engine as DE            # noqa: E402
import bullet_engine as BE                # noqa: E402
import keyword_source_adapter as KSA       # noqa: E402
from production import product_workspace as PW   # noqa: E402

T2 = os.path.join(ROOT, "runs", "T2")
T2_PRESENT = os.path.exists(os.path.join(T2, "phase6", "6C", "PRODUCT-DETAIL-PAGE.json"))


def facts_with(product):
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "product-facts.json"), "w", encoding="utf-8") as f:
        json.dump({"schema_version": "1.0.0", "source": "owner", "product": product}, f)
    return PFL.load_product_facts(folder=d)


def build(product, keyword_context=None):
    return CE.build_claim_evidence(facts_with(product), keyword_context=keyword_context)


T2_NURSE = {"audience": "nurses"}


# =============================================================== ATOMIC CLAIMS (9-21)
class AtomicClaims(unittest.TestCase):
    def setUp(self):
        self.ev = build({}, T2_NURSE)
        self.rec = self.ev.claim("recipient")

    def test_09_recipient_only_recipient_semantics(self):
        comps = [c["component"] for c in self.rec["semantic_components"]]
        self.assertEqual(comps, [CE.COMP_RECIPIENT])
        self.assertEqual(self.rec["proposed_text"], "For nurses.")
        self.assertNotIn("gift", self.rec["proposed_text"].lower())
        self.assertNotIn("personal", self.rec["proposed_text"].lower())

    def test_10_recipient_does_not_verify_personalization(self):
        self.assertEqual(self.ev.effective_state("personalization_fields"), CE.UNVERIFIED_BLOCKED)
        self.assertEqual(self.ev.effective_state("exact_personalization_promise"), CE.UNVERIFIED_BLOCKED)

    def test_11_recipient_does_not_verify_gift(self):
        self.assertEqual(self.ev.effective_state("gift"), CE.UNVERIFIED_BLOCKED)
        self.assertFalse(self.ev.is_publishable("gift"))

    def test_11b_recipient_does_not_verify_occasion(self):
        self.assertEqual(self.ev.effective_state("occasion"), CE.UNVERIFIED_BLOCKED)

    def test_12_recipient_does_not_verify_decoration(self):
        self.assertEqual(self.ev.effective_state("real_machine_embroidery"), CE.UNVERIFIED_BLOCKED)
        self.assertEqual(self.ev.effective_state("decoration_method"), CE.UNVERIFIED_BLOCKED)

    def test_13_recipient_does_not_verify_material(self):
        self.assertEqual(self.ev.effective_state("material_composition"), CE.UNVERIFIED_BLOCKED)

    def test_14_personalization_has_independent_claim(self):
        c = self.ev.claim("personalization_fields")
        self.assertIsNotNone(c)
        self.assertEqual([x["component"] for x in c["semantic_components"]], [CE.COMP_PERSONALIZATION])

    def test_15_gift_has_independent_claim(self):
        c = self.ev.claim("gift")
        self.assertIsNotNone(c)
        self.assertIn(CE.COMP_GIFT_OR_OCCASION, [x["component"] for x in c["semantic_components"]])

    def test_16_decoration_has_independent_claim(self):
        c = self.ev.claim("decoration_method")
        self.assertEqual([x["component"] for x in c["semantic_components"]], [CE.COMP_DECORATION_METHOD])

    def test_17_material_has_independent_claim(self):
        c = self.ev.claim("material_composition")
        self.assertEqual([x["component"] for x in c["semantic_components"]], [CE.COMP_MATERIAL])

    def test_18_atomic_claim_ids_unique(self):
        ids = [c["claim_id"] for c in self.ev.claims.values()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_19_atomic_claim_lineage_resolves(self):
        idx = self.ev.claim_id_index()
        self.assertEqual(idx[self.rec["claim_id"]], "recipient")

    def test_20_atomic_claim_hashes_recompute(self):
        for c in self.ev.claims.values():
            self.assertEqual(c["content_sha256"], CE.claim_content_sha256(c))

    def test_21_serialization_deterministic(self):
        a = build({}, T2_NURSE)
        b = build({}, T2_NURSE)
        self.assertEqual(a.content_sha256(), b.content_sha256())

    def test_recipient_verified_and_publishable(self):
        self.assertEqual(self.rec["verification_state"], CE.VERIFIED)
        self.assertEqual(self.rec["effective_evidence_state"], CE.VERIFIED)
        self.assertTrue(self.rec["publishable"])


# =============================================================== COMPOUND CLAIMS (22-32)
def _forge_compound(base_ev, text):
    """Forge a claim record carrying a compound canonical text (as an OLD unsafe record would)."""
    rec = dict(base_ev.claim("recipient"))
    rec["proposed_text"] = text
    rec["canonical_claim"] = text
    rec["verification_state"] = CE.VERIFIED
    rec["effective_evidence_state"] = CE.VERIFIED
    rec["publishable"] = True
    rec.pop("content_sha256", None)
    return rec


class CompoundClaims(unittest.TestCase):
    def setUp(self):
        self.ev = build({}, T2_NURSE)

    def test_22_mixed_compound_not_verified(self):
        rec = _forge_compound(self.ev, "A personalized gift for nurses.")
        _c, eff, _r = CE.resolve_atomic_components((CE.COMP_RECIPIENT,), CE.VERIFIED,
                                                   "A personalized gift for nurses.")
        self.assertEqual(eff, CE.MIXED_EVIDENCE_BLOCKED)
        self.assertTrue(CE.validate_claim_record(rec))

    def test_23_least_supported_controls_effective(self):
        _c, eff, _r = CE.resolve_atomic_components((CE.COMP_RECIPIENT,), CE.VERIFIED,
                                                   "gift for nurses")
        self.assertEqual(eff, CE.MIXED_EVIDENCE_BLOCKED)

    def test_24_unsupported_component_typed_blocker(self):
        _c, _eff, reasons = CE.resolve_atomic_components((CE.COMP_RECIPIENT,), CE.VERIFIED,
                                                         "A personalized gift for nurses.")
        self.assertIn(CE.R_UNSUPPORTED_COMPONENT, reasons)
        self.assertIn(CE.R_PERSONALIZATION_MISSING, reasons)
        self.assertIn(CE.R_GIFT_MISSING, reasons)
        self.assertIn(CE.R_NOT_ATOMIC, reasons)

    def test_25_verified_compound_ok_only_when_all_components_verified(self):
        # every component independently VERIFIED -> no foreign component -> VERIFIED allowed.
        _c, eff, _r = CE.resolve_atomic_components(
            (CE.COMP_RECIPIENT, CE.COMP_GIFT_OR_OCCASION, CE.COMP_PERSONALIZATION), CE.VERIFIED,
            "A personalized gift for nurses.")
        self.assertEqual(eff, CE.VERIFIED)

    def test_26_legacy_unsafe_compound_fails_validation(self):
        rec = _forge_compound(self.ev, "A personalized gift for nurses.")
        self.assertTrue(CE.validate_claim_record(rec))
        self.assertFalse(CE.audit_claim_evidence({"recipient": rec})["ok"])

    def test_27_recipient_plus_personalization_blocked(self):
        _c, eff, _r = CE.resolve_atomic_components((CE.COMP_RECIPIENT,), CE.VERIFIED,
                                                   "A personalized item for nurses")
        self.assertEqual(eff, CE.MIXED_EVIDENCE_BLOCKED)

    def test_28_recipient_plus_gift_blocked(self):
        _c, eff, _r = CE.resolve_atomic_components((CE.COMP_RECIPIENT,), CE.VERIFIED, "gift for nurses")
        self.assertEqual(eff, CE.MIXED_EVIDENCE_BLOCKED)

    def test_29_recipient_plus_embroidery_blocked(self):
        _c, eff, r = CE.resolve_atomic_components((CE.COMP_RECIPIENT,), CE.VERIFIED,
                                                  "embroidered for nurses")
        self.assertEqual(eff, CE.MIXED_EVIDENCE_BLOCKED)
        self.assertIn(CE.R_DECORATION_MISSING, r)

    def test_30_product_plus_material_blocked_without_material_evidence(self):
        _c, eff, r = CE.resolve_atomic_components((CE.COMP_PRODUCT_IDENTITY,), CE.VERIFIED,
                                                  "A cotton sweatshirt")
        self.assertEqual(eff, CE.MIXED_EVIDENCE_BLOCKED)
        self.assertIn(CE.R_MATERIAL_MISSING, r)

    def test_31_canonical_text_cannot_add_unsupported_qualifier(self):
        # a build path: if a recipient template smuggled 'gift', the built claim is not publishable.
        _c, eff, _r = CE.resolve_atomic_components((CE.COMP_RECIPIENT,), CE.VERIFIED, "gift for nurses")
        self.assertNotIn(eff, CE.PUBLISHABLE_STATES)

    def test_32_display_not_more_permissive_than_canonical(self):
        rec = _forge_compound(self.ev, "A personalized gift for nurses.")
        # effective recompute blocks it, so publishable must be False (display cannot exceed evidence).
        self.assertIn("is publishable but effective state is", " ".join(CE.validate_claim_record(rec)))

    def test_owner_verified_value_not_smuggled(self):
        # a 'gift box' packaging VALUE is owner-verified evidence, not a smuggled gift claim.
        ev = build({"packaging": {"value": "gift box", "status": "VERIFIED"}})
        pk = ev.claim("packaging")
        self.assertEqual(pk["effective_evidence_state"], CE.VERIFIED)
        self.assertTrue(pk["publishable"])


# =============================================================== CONSUMERS (33-44)
class Consumers(unittest.TestCase):
    def test_36_37_38_engines_cannot_promote_blocked_component(self):
        # a MIXED recipient claim (compound) must not reach description / bullet copy.
        ev = build({}, T2_NURSE)
        # forge the recipient into a compound in-place and confirm engines skip it.
        ev.claims["recipient"] = _forge_compound(ev, "A personalized gift for nurses.")
        # rebuild effective/publishable via the authority so the record is self-consistent
        comps, eff, _r = CE.resolve_atomic_components((CE.COMP_RECIPIENT,), CE.VERIFIED,
                                                      "A personalized gift for nurses.")
        ev.claims["recipient"].update({"semantic_components": comps, "effective_evidence_state": eff,
                                       "publishable": eff in CE.PUBLISHABLE_STATES})
        dr = DE.build_description(ev, "nurse sweatshirt")
        self.assertNotIn("personalized gift", dr.description_text.lower())
        br = BE.build_bullets(ev, "nurse sweatshirt")
        for b in br.to_list():
            self.assertNotIn("personalized gift", (b["text"] or "").lower())

    def test_40_item_highlights_uses_effective_state(self):
        # a MIXED material claim must not publish as a highlight.
        ev = build({"material_composition": {"value": "cotton", "status": "VERIFIED"}})
        ev.claims["material_composition"]["effective_evidence_state"] = CE.MIXED_EVIDENCE_BLOCKED
        ev.claims["material_composition"]["publishable"] = False
        policy = CPR.resolve_category_policy("apparel")
        ih = IHB.build_item_highlights_content(ev, policy)
        for h in ih.publishable_highlights:
            self.assertNotEqual(h["concept"], "material_composition")

    def test_42_publishability_not_upgraded_by_mixed_claim(self):
        rec = _forge_compound(build({}, T2_NURSE), "A personalized gift for nurses.")
        comps, eff, _r = CE.resolve_atomic_components((CE.COMP_RECIPIENT,), CE.VERIFIED, rec["proposed_text"])
        self.assertNotIn(eff, CE.PUBLISHABLE_STATES)

    def test_33_product_workspace_doc_validator_enforces_atomicity(self):
        ev = build({}, T2_NURSE)
        doc = ev.to_dict()
        self.assertEqual(PW.validate_claim_evidence(doc), [])
        # corrupt one claim into an unsafe compound -> the 6A doc validator must reject it.
        doc["claims"]["recipient"]["proposed_text"] = "A personalized gift for nurses."
        doc["claims"]["recipient"]["publishable"] = True
        self.assertTrue(PW.validate_claim_evidence(doc))


# =============================================================== SINGLE AUTHORITY (6-8)
class SingleAuthority(unittest.TestCase):
    def test_06_shared_claim_authority_unique(self):
        # exactly one module defines build_claim_evidence + ClaimSpec (the authority).
        self.assertTrue(hasattr(CE, "build_claim_evidence"))
        self.assertTrue(hasattr(CE, "resolve_atomic_components"))

    def test_08_no_amazon_account_path(self):
        # the claim authority never imports an Amazon SP-API / seller-central client.
        src = open(os.path.join(ROOT, "listing", "claim_evidence.py"), encoding="utf-8").read().lower()
        for bad in ("sp-api", "sellingpartner", "seller_central", "mws"):
            self.assertNotIn(bad, src)


# =============================================================== BACKEND DENSITY (81-94)
def _fake_optimization(units, byte_ceiling=249):
    class _Opt:
        pass
    o = _Opt()
    o.included_units = units
    o.backend_search_terms_string = " ".join(u["text"] for u in units)
    o.byte_ceiling = byte_ceiling
    o.bytes_used = len(o.backend_search_terms_string.encode("utf-8"))
    o.bytes_remaining = byte_ceiling - o.bytes_used
    o.excluded_summary = {}
    return o


def _unit(text, concept, utype="specialty_phrase", inc=1):
    toks = text.split()
    return {"semantic_unit_id": text.replace(" ", "_"), "text": text, "tokens": toks,
            "normalized_concept": concept, "unit_type": utype, "incremental_coverage_score": inc,
            "semantic_score": 2, "byte_cost": len(text) + 1, "inclusion_reason": "SPECIALTY_PHRASE_TOKEN",
            "source_keyword_id": text.replace(" ", "")[:12]}


class BackendDensity(unittest.TestCase):
    def test_82_distinct_units_pass(self):
        opt = _fake_optimization([_unit("icu nurse", "icu"), _unit("er nurse", "er")])
        a = BDA.audit_backend_density(opt, visible_texts=["nurse sweatshirt"])
        self.assertIn(a["result"], (BDA.RESULT_PASS, BDA.RESULT_PASS_WARN))
        self.assertEqual(a["non_incremental_units"], [])

    def test_83_84_85_pure_repetition_flagged(self):
        # 'nurses' after 'nurse' adds no new root -> non-incremental -> OPTIMIZER_REPAIR_REQUIRED.
        opt = _fake_optimization([_unit("nurse", "nurse", "audience"),
                                  _unit("nurses", "nurse", "audience")])
        a = BDA.audit_backend_density(opt, visible_texts=[])
        self.assertEqual(a["result"], BDA.RESULT_REPAIR)
        self.assertTrue(a["non_incremental_units"])

    def test_86_repeated_root_needs_new_modifier(self):
        opt = _fake_optimization([_unit("icu nurse", "icu"), _unit("er nurse", "er")])
        a = BDA.audit_backend_density(opt, visible_texts=[])
        for c in a["per_candidate"]:
            if c["shared_interior_roots"]:
                self.assertTrue(c["new_roots"], c["text"])

    def test_89_90_unused_bytes_allowed_not_target(self):
        opt = _fake_optimization([_unit("icu nurse", "icu")], byte_ceiling=249)
        a = BDA.audit_backend_density(opt)
        self.assertFalse(a["byte_utilization_is_a_target"])
        self.assertTrue(a["unused_bytes_allowed"])
        self.assertGreater(a["bytes_remaining"], 0)

    def test_91_92_93_phrase_units_intact_no_slicing_byte_safe(self):
        opt = _fake_optimization([_unit("labor and delivery nurse", "labor delivery")])
        a = BDA.audit_backend_density(opt)
        self.assertTrue(a["phrase_units_intact"])
        self.assertFalse(a["token_slicing_detected"])
        self.assertLessEqual(a["final_byte_count"], a["byte_limit"])


# =============================================================== T2 ARTIFACTS (gated)
@unittest.skipUnless(T2_PRESENT, "runs/T2 (paid, gitignored) not present")
class T2Artifacts(unittest.TestCase):
    def test_45_t2_recipient_atomic_verified(self):
        ev = CE.build_claim_evidence(PFL.load_product_facts(folder=T2), keyword_context=T2_NURSE)
        r = ev.claim("recipient")
        self.assertEqual(r["effective_evidence_state"], CE.VERIFIED)
        self.assertEqual([c["component"] for c in r["semantic_components"]], [CE.COMP_RECIPIENT])
        self.assertTrue(ev.atomicity_ok)
        self.assertEqual(ev.mixed_evidence_count, 0)

    def test_46_47_48_blocked_concepts(self):
        ev = CE.build_claim_evidence(PFL.load_product_facts(folder=T2), keyword_context=T2_NURSE)
        for concept in ("personalization_fields", "exact_personalization_promise", "gift", "occasion",
                        "decoration_method", "real_machine_embroidery"):
            self.assertFalse(ev.is_publishable(concept), concept)

    def test_50_51_52_source_tiers_unchanged(self):
        src = KSA.load_keyword_source(T2)
        self.assertEqual(src.source_sha256,
                         "52b02f162e75f70806a8e2893a5327210037135cb8b4b3834fc192b318f8e8e7")
        self.assertEqual(src.tier_counts["TIER_A"], 74)
        self.assertEqual(src.tier_counts["TIER_B"], 378)

    def test_committed_claim_hash_matches_regenerated(self):
        ev = CE.build_claim_evidence(PFL.load_product_facts(folder=T2), keyword_context=T2_NURSE)
        disk = json.load(open(os.path.join(T2, "phase6", "6A", "CLAIM-EVIDENCE.json"), encoding="utf-8"))
        self.assertEqual(disk["content_sha256"], ev.content_sha256())

    def test_backend_density_report_committed(self):
        rep = json.load(open(os.path.join(ROOT, "BACKEND-SEMANTIC-DENSITY-REPORT.json"), encoding="utf-8"))
        self.assertIn(rep["result"], (BDA.RESULT_PASS, BDA.RESULT_PASS_WARN))
        self.assertEqual(rep["repeated_concept_count"], 0)
        self.assertEqual(rep["non_incremental_units"], [])
        self.assertTrue(rep["phrase_units_intact"])
        self.assertFalse(rep["token_slicing_detected"])
        self.assertLessEqual(rep["final_byte_count"], rep["byte_limit"])


# =============================================================== REGEN LINEAGE (95-102)
class RegenerationLineage(unittest.TestCase):
    def test_95_96_97_lineage_records_hashes(self):
        p = os.path.join(ROOT, "PHASE6-REGENERATION-LINEAGE.json")
        lin = json.load(open(p, encoding="utf-8"))
        self.assertNotEqual(lin["old_claim_evidence_sha256"], lin["new_claim_evidence_sha256"])
        self.assertEqual(lin["source_keyword_sha256"],
                         "52b02f162e75f70806a8e2893a5327210037135cb8b4b3834fc192b318f8e8e7")
        self.assertTrue(lin["source_keyword_unchanged"])
        self.assertTrue(lin["deterministic_verification"])

    def test_102_no_private_absolute_paths_in_proof(self):
        for name in ("PHASE6-REGENERATION-LINEAGE.json", "BACKEND-SEMANTIC-DENSITY-REPORT.json",
                     "SESSION6C1-ATOMIC-CLAIM-PROOF.json", "ATOMIC-CLAIM-MIGRATION-REPORT.json"):
            blob = open(os.path.join(ROOT, name), encoding="utf-8").read()
            self.assertNotIn(":\\Users\\", blob)
            self.assertNotIn("/Users/", blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
