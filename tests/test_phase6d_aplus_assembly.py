#!/usr/bin/env python3
"""
Phase 6D — A+ Content Assembly component tests (run everywhere; no paid workspace needed).

Covers, WITHOUT requiring the gitignored runs/T2 paid workspace: capability resolution through the
existing registry authority (never guessed), reuse of the ONE A+ builder + module registry, Basic /
Premium draft assembly, per-module fact/claim/asset evidence gates, the atomic-claim gate, A+ keyword
control, the module-specific asset manifest (a generated asset can satisfy zero real-proof gates), the
validators, atomic-write / path-safety primitives, and deterministic hashing. The full pipeline + page
audit + write/verify + T2-specific proof live in test_phase6d_t2_proof.py (skipped without runs/T2).
"""
import os
import sys
import json
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "listing"))
sys.path.insert(0, os.path.join(ROOT, "core"))

from production import aplus_assembly as A            # noqa: E402
import product_fact_loader as PFL                     # noqa: E402
import claim_evidence as CE                           # noqa: E402
import aplus_module_registry as AREG                  # noqa: E402
import aplus_builder as AB                            # noqa: E402


def _facts(fields=None):
    """Build NormalizedProductFacts from an optional dict of verified fields (else all UNKNOWN)."""
    d = tempfile.mkdtemp()
    if fields:
        doc = {"schema_version": "1.0.0", "source": "owner",
               "product": {k: {"value": v, "status": "VERIFIED"} for k, v in fields.items()}}
        with open(os.path.join(d, "product-facts.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f)
    return PFL.load_product_facts(folder=d)


def _claims(facts, audience="nurses"):
    return CE.build_claim_evidence(facts, keyword_context={"audience": audience} if audience else {})


def _fake_deps(facts, claims, aplus_field=None, aplus_status=None):
    """A minimal read-only deps stand-in carrying only the attributes the doc-builders touch."""
    return types.SimpleNamespace(
        workspace_id="TX", product_id="TX", category="apparel", product_type="sweatshirt",
        audience="nurses", facts=facts, claims=claims, workspace={},
        phase6b_aplus_field=aplus_field or [], phase6b_aplus_status=aplus_status or {},
        product_facts_sha256=facts.content_sha256(), claim_evidence_sha256=claims.content_sha256(),
        keyword_source_sha256="k" * 64, phase6a_manifest_sha256="m" * 64,
        listing_publishability_state=A.LISTING_SAFE_DRAFT)


_FULL = {"decoration_method": "machine embroidery", "personalization_fields": ["name"],
         "size_range": ["S", "M", "L"], "color_options": ["black", "navy"],
         "care_instructions": "machine wash cold", "brand": "Acme", "audience": "nurses"}
_REAL_ASSETS = [
    {"asset_id": "A1", "proof_purpose": "MACRO_DECORATION_STITCH", "real_or_generated": "REAL", "alt_text": "stitch"},
    {"asset_id": "A2", "proof_purpose": "PERSONALIZATION_EXAMPLE", "real_or_generated": "REAL", "alt_text": "name"},
    {"asset_id": "A3", "proof_purpose": "GARMENT_OR_COLOR", "real_or_generated": "REAL", "alt_text": "garment"},
]


# ================================================================ CAPABILITY
class Capability(unittest.TestCase):
    def test_unstated_owner_resolves_to_unknown_not_basic(self):
        facts = _facts(); claims = _claims(facts)
        deps = _fake_deps(facts, claims)
        cap, source, verified = A.resolve_aplus_capability(deps)
        self.assertEqual(cap, AREG.UNKNOWN_A_PLUS_CAPABILITY)      # honest unknown, never Basic
        self.assertFalse(verified)
        self.assertIn("unstated", source)

    def test_capability_uses_registry_authority(self):
        # the registry is the sole authority; UNKNOWN is a valid mode, not a Basic default.
        self.assertIn(AREG.UNKNOWN_A_PLUS_CAPABILITY, AREG.CAPABILITY_MODES)
        self.assertEqual(AREG.validate_capability(None), AREG.UNKNOWN_A_PLUS_CAPABILITY)

    def test_owner_declared_capability_is_verified(self):
        facts = _facts(); claims = _claims(facts)
        deps = _fake_deps(facts, claims)
        deps.workspace = {"aplus_capability": "BASIC_A_PLUS"}
        # resolve reads workspace via deps.workspace; patch attribute path used by resolver.
        cap, source, verified = A.resolve_aplus_capability(deps)
        self.assertEqual(cap, AREG.BASIC_A_PLUS)
        self.assertTrue(verified)

    def test_invalid_declared_capability_raises(self):
        facts = _facts(); claims = _claims(facts)
        deps = _fake_deps(facts, claims)
        deps.workspace = {"aplus_capability": "SUPER_A_PLUS"}
        with self.assertRaises(AREG.InvalidCapabilityError):
            A.resolve_aplus_capability(deps)

    def test_no_a_plus_yields_zero_modules(self):
        facts = _facts(); claims = _claims(facts)
        res = AB.build_aplus(AREG.NO_A_PLUS, claims, facts, assets=None)
        deps = _fake_deps(facts, claims)
        basic = A.assemble_basic_aplus(deps, res, AREG.NO_A_PLUS, False)
        self.assertEqual(basic["module_count"], 0)
        self.assertEqual(basic["modules"], [])


# ================================================================ BASIC A+
class BasicAplus(unittest.TestCase):
    def setUp(self):
        self.facts = _facts(); self.claims = _claims(self.facts)
        self.res = AB.build_aplus(AREG.UNKNOWN_A_PLUS_CAPABILITY, self.claims, self.facts, assets=None)
        self.deps = _fake_deps(self.facts, self.claims)
        self.basic = A.assemble_basic_aplus(self.deps, self.res, AREG.UNKNOWN_A_PLUS_CAPABILITY, False)

    def test_reuses_existing_builder_and_registry(self):
        self.assertIs(A.AB, AB)
        self.assertIs(A.AREG, AREG)

    def test_five_modules_unique_ids_and_positions(self):
        mods = self.basic["modules"]
        self.assertEqual(len(mods), AREG.BASIC_MODULE_COUNT)
        self.assertEqual(len({m["module_id"] for m in mods}), 5)
        self.assertEqual(sorted(m["position"] for m in mods), [1, 2, 3, 4, 5])
        self.assertEqual([m["module_key"] for m in mods], list(AREG.BASIC_MODULE_KEYS))

    def test_blocked_modules_present_but_empty_copy(self):
        for m in self.basic["modules"]:
            self.assertEqual(m["publishability"], AREG.OWNER_FACT_REQUIRED)
            self.assertEqual(m["headline"], "")
            self.assertEqual(m["body_copy"], "")
            self.assertTrue(m["blockers"])
            # template headline is preserved in metadata, never shown.
            self.assertTrue(m["module_template_headline"])

    def test_no_placeholder_copy(self):
        import re
        placeholder = re.compile(r"\{[a-zA-Z0-9_]+\}")     # the builder's {token} placeholder shape
        for m in self.basic["modules"]:
            for fld in ("headline", "body_copy", "supporting_copy", "alt_text"):
                text = m[fld]
                self.assertFalse(placeholder.search(text), f"{m['module_key']}.{fld}")
                low = text.lower()
                for ph in ("insert ", "add size chart", "upload photo", "customize your name"):
                    self.assertNotIn(ph, low)

    def test_structure_not_publishability(self):
        self.assertEqual(self.basic["state"], A.BASIC_ELIGIBILITY_UNVERIFIED)
        self.assertEqual(self.basic["publishability"], A.LISTING_SAFE_DRAFT)
        self.assertEqual(self.basic["nonempty_copy_module_count"], 0)

    def test_deterministic(self):
        b2 = A.assemble_basic_aplus(self.deps, AB.build_aplus(
            AREG.UNKNOWN_A_PLUS_CAPABILITY, _claims(_facts()), _facts(), assets=None),
            AREG.UNKNOWN_A_PLUS_CAPABILITY, False)
        self.assertEqual(self.basic["deterministic_content_sha256"], b2["deterministic_content_sha256"])

    def test_ready_scenario_emits_verified_copy(self):
        facts = _facts(_FULL); claims = _claims(facts)
        res = AB.build_aplus(AREG.BASIC_A_PLUS, claims, facts, assets=_REAL_ASSETS)
        deps = _fake_deps(facts, claims)
        basic = A.assemble_basic_aplus(deps, res, AREG.BASIC_A_PLUS, True)
        self.assertTrue(res.basic_ready)
        self.assertEqual(basic["state"], A.BASIC_READY)
        # verified modules emit claim-backed copy (would be wrongly suppressed by a verification-blind gate).
        hero = next(m for m in basic["modules"] if m["module_key"] == "HERO_EMBROIDERY_PROOF")
        self.assertIn("embroidery", hero["body_copy"].lower())
        self.assertTrue(hero["claim_lineage"]["verified_claim_ids"])
        self.assertEqual(basic["nonempty_copy_module_count"], 5)
        self.assertEqual(A.validate_basic_aplus(basic), [])


# ================================================================ MODULE EVIDENCE GATES
class ModuleGates(unittest.TestCase):
    def _module(self, res, key):
        return next(m for m in res.basic_modules if m["module_key"] == key)

    def test_decoration_requires_decoration_fact(self):
        res = AB.build_aplus(AREG.BASIC_A_PLUS, _claims(_facts()), _facts(), assets=None)
        self.assertEqual(self._module(res, "HERO_EMBROIDERY_PROOF")["status"], AREG.OWNER_FACT_REQUIRED)

    def test_decoration_requires_real_photo_when_fact_present(self):
        f = _facts({"decoration_method": "machine embroidery"}); c = _claims(f)
        res = AB.build_aplus(AREG.BASIC_A_PLUS, c, f, assets=None)   # fact present, no real photo
        self.assertEqual(self._module(res, "HERO_EMBROIDERY_PROOF")["status"], AREG.REAL_PHOTO_REQUIRED)

    def test_size_and_color_require_verified_values(self):
        res = AB.build_aplus(AREG.BASIC_A_PLUS, _claims(_facts()), _facts(), assets=None)
        m = self._module(res, "FIT_SIZE_COLOR_DETAILS")
        self.assertEqual(m["status"], AREG.OWNER_FACT_REQUIRED)
        self.assertIn("size_range", m["evidence_summary"]["missing_facts"])
        self.assertIn("color_options", m["evidence_summary"]["missing_facts"])

    def test_unknown_color_does_not_become_standard(self):
        # a colour claim exists ONLY when color_options is verified; nothing is defaulted to "Standard".
        f = _facts(); c = _claims(f)
        self.assertIsNone(c.publishable_text("color_options"))

    def test_unknown_decoration_does_not_become_embroidered(self):
        f = _facts(); c = _claims(f)
        self.assertIsNone(c.publishable_text("decoration_method"))
        self.assertIsNone(c.publishable_text("real_machine_embroidery"))

    def test_personalization_requires_fields(self):
        res = AB.build_aplus(AREG.BASIC_A_PLUS, _claims(_facts()), _facts(), assets=None)
        self.assertEqual(self._module(res, "PERSONALIZATION_GALLERY")["status"], AREG.OWNER_FACT_REQUIRED)
        self.assertEqual(self._module(res, "HOW_TO_CUSTOMIZE")["status"], AREG.OWNER_FACT_REQUIRED)

    def test_care_faq_requires_any_verified_topic(self):
        res = AB.build_aplus(AREG.BASIC_A_PLUS, _claims(_facts()), _facts(), assets=None)
        self.assertEqual(self._module(res, "CARE_PRODUCTION_FAQ")["status"], AREG.OWNER_FACT_REQUIRED)


# ================================================================ ATOMIC CLAIM GATES
class AtomicClaims(unittest.TestCase):
    def test_recipient_permits_neutral_language(self):
        c = _claims(_facts())
        self.assertEqual(c.publishable_text("recipient"), "For nurses.")

    def test_recipient_does_not_verify_personalization_gift_occasion_decoration(self):
        c = _claims(_facts())
        for concept in ("personalization_fields", "gift", "occasion", "decoration_method",
                        "exact_personalization_promise"):
            self.assertIsNone(c.publishable_text(concept), concept)

    def test_atomicity_invariant_holds(self):
        c = _claims(_facts())
        self.assertTrue(c.atomicity_ok)
        self.assertTrue(CE.audit_claim_evidence(c)["ok"])

    def test_only_recipient_verified_for_all_unknown_facts(self):
        c = _claims(_facts())
        self.assertEqual(sorted(x["normalized_concept"] for x in c.publishable), ["recipient"])

    def test_effective_state_used_for_publishability(self):
        c = _claims(_facts())
        self.assertEqual(c.effective_state("recipient"), CE.VERIFIED)
        self.assertEqual(c.effective_state("gift"), CE.UNVERIFIED_BLOCKED)


# ================================================================ PREMIUM A+
class PremiumDraft(unittest.TestCase):
    def setUp(self):
        self.facts = _facts(); self.claims = _claims(self.facts)
        self.res = AB.build_aplus(AREG.UNKNOWN_A_PLUS_CAPABILITY, self.claims, self.facts, assets=None)
        self.deps = _fake_deps(self.facts, self.claims)
        self.prem = A.assemble_premium_aplus_draft(self.deps, self.res, AREG.UNKNOWN_A_PLUS_CAPABILITY, False)

    def test_clearly_non_publishable_draft(self):
        self.assertTrue(self.prem["is_draft"])
        self.assertTrue(self.prem["never_publishable"])
        self.assertEqual(self.prem["publishability"], A.PREMIUM_DO_NOT_PUBLISH)
        self.assertFalse(self.prem["premium_eligible"])

    def test_complete_basic_fallback_payload_positions_1_to_5(self):
        fb = self.prem["basic_fallback"]
        self.assertEqual(len(fb), AREG.BASIC_MODULE_COUNT)
        self.assertEqual(self.prem["basic_fallback_positions"], [1, 2, 3, 4, 5])
        # not a boolean; the full module payload.
        self.assertTrue(all(isinstance(m, dict) and m.get("module_key") for m in fb))

    def test_dynamic_pool_independently_evaluated_no_filler(self):
        self.assertEqual(self.prem["dynamic_candidate_count"], len(AREG.PREMIUM_DYNAMIC_POOL))
        self.assertEqual(self.prem["eligible_dynamic_modules"], [])
        self.assertEqual(self.prem["selected_dynamic_modules"], [])       # never invents a module
        for c in self.prem["dynamic_candidates"]:
            self.assertIn("eligible", c)
            self.assertIn("ineligible_reasons", c)

    def test_fewer_than_two_dynamic_returns_blockers(self):
        self.assertTrue(any("dynamic" in b for b in self.prem["blockers"]))

    def test_dynamic_gates_require_real_evidence(self):
        # with all facts unknown, every dynamic module is ineligible.
        for c in self.prem["dynamic_candidates"]:
            self.assertFalse(c["eligible"], c["module_key"])
            self.assertTrue(c["ineligible_reasons"])

    def test_brand_story_requires_brand_fact(self):
        f = _facts({"brand": "Acme"}); c = _claims(f)
        res = AB.build_aplus(AREG.UNKNOWN_A_PLUS_CAPABILITY, c, f, assets=None)
        deps = _fake_deps(f, c)
        prem = A.assemble_premium_aplus_draft(deps, res, AREG.UNKNOWN_A_PLUS_CAPABILITY, False)
        brand = next(x for x in prem["dynamic_candidates"] if x["module_key"] == "BRAND_STORY")
        self.assertTrue(brand["eligible"])       # brand verified -> eligible

    def test_deterministic(self):
        p2 = A.assemble_premium_aplus_draft(self.deps, AB.build_aplus(
            AREG.UNKNOWN_A_PLUS_CAPABILITY, _claims(_facts()), _facts(), assets=None),
            AREG.UNKNOWN_A_PLUS_CAPABILITY, False)
        self.assertEqual(self.prem["deterministic_content_sha256"], p2["deterministic_content_sha256"])


# ================================================================ KEYWORD CONTROL
class KeywordControl(unittest.TestCase):
    def test_empty_aplus_allocation_respected_no_backfill(self):
        facts = _facts(); claims = _claims(facts)
        res = AB.build_aplus(AREG.UNKNOWN_A_PLUS_CAPABILITY, claims, facts, assets=None)
        deps = _fake_deps(facts, claims, aplus_field=[], aplus_status={"empty_reason_code": "OWNER_FACT_REQUIRED"})
        basic = A.assemble_basic_aplus(deps, res, AREG.UNKNOWN_A_PLUS_CAPABILITY, False)
        for m in basic["modules"]:
            self.assertEqual(m["keyword_allocations_used"], [])          # never backfills keywords


# ================================================================ ASSET MANIFEST
class AssetManifest(unittest.TestCase):
    def setUp(self):
        self.facts = _facts(); self.claims = _claims(self.facts)
        self.res = AB.build_aplus(AREG.UNKNOWN_A_PLUS_CAPABILITY, self.claims, self.facts, assets=None)
        self.deps = _fake_deps(self.facts, self.claims)
        self.doc = A.build_aplus_asset_manifest(self.deps, self.res, AREG.UNKNOWN_A_PLUS_CAPABILITY)

    def test_unique_asset_ids_and_valid_states(self):
        ids = [a["asset_id"] for a in self.doc["assets"]]
        self.assertEqual(len(set(ids)), len(ids))
        for a in self.doc["assets"]:
            self.assertIn(a["status"], A.ASSET_STATES)

    def test_missing_files_not_asset_ready(self):
        for a in self.doc["assets"]:
            self.assertNotEqual(a["status"], A.ASSET_READY)
            self.assertEqual(a["current_asset_ids"], [])
            self.assertEqual(a["current_relative_paths"], [])

    def test_real_proof_requires_real_photo_not_generated(self):
        for a in self.doc["assets"]:
            if a["real_photo_required"]:
                self.assertFalse(a["generated_mockup_allowed"])

    def test_generated_asset_never_satisfies_real_proof(self):
        assets = [{"asset_id": "G1", "proof_purpose": "MACRO_DECORATION_STITCH",
                   "real_or_generated": "GENERATED", "alt_text": "ai mockup"}]
        f = _facts({"decoration_method": "machine embroidery"}); c = _claims(f)
        res = AB.build_aplus(AREG.BASIC_A_PLUS, c, f, assets=assets)
        deps = _fake_deps(f, c)
        doc = A.build_aplus_asset_manifest(deps, res, AREG.BASIC_A_PLUS)
        hero_asset = next(a for a in doc["assets"] if a["proof_purpose"] == "MACRO_DECORATION_STITCH")
        self.assertIn(hero_asset["status"], (A.ASSET_BLOCKED_UNSAFE, A.ASSET_REAL_PHOTO_REQUIRED))
        self.assertNotEqual(hero_asset["status"], A.ASSET_READY)
        self.assertEqual(doc["generated_accepted_as_real_proof_count"], 0)

    def test_real_asset_counts_and_records_sha_only_when_present(self):
        assets = [{"asset_id": "R1", "proof_purpose": "MACRO_DECORATION_STITCH",
                   "real_or_generated": "REAL", "alt_text": "real stitch macro"}]
        f = _facts({"decoration_method": "machine embroidery"}); c = _claims(f)
        res = AB.build_aplus(AREG.BASIC_A_PLUS, c, f, assets=assets)
        deps = _fake_deps(f, c)
        doc = A.build_aplus_asset_manifest(deps, res, AREG.BASIC_A_PLUS)
        hero_asset = next(a for a in doc["assets"] if a["proof_purpose"] == "MACRO_DECORATION_STITCH")
        self.assertEqual(hero_asset["real_or_generated"], "REAL")

    def test_prompt_readiness_differs_from_asset_readiness(self):
        for a in self.doc["assets"]:
            self.assertIn("prompt_readiness", a)
            self.assertIn("asset_readiness", a)
        # they are separate axes: briefing-readiness vs actual-asset presence.
        self.assertTrue(any(a["prompt_readiness"] != a["asset_readiness"] for a in self.doc["assets"]))

    def test_no_final_prompt_body_and_no_ten_job_schema(self):
        # the asset manifest is module-based, not a Phase 6E listing-image plan: no prompt bodies, no
        # per-asset prompt/job structure. (The disclaimer note may name what this is NOT — only per-asset
        # record content is checked here.)
        self.assertEqual(self.doc["schema_version"], A.ASSET_MANIFEST_SCHEMA_VERSION)
        for a in self.doc["assets"]:
            blob = json.dumps(a).lower()
            for forbidden in ("image_prompt", "prompt_body", "prompt_text", "job_id", "shot_list"):
                self.assertNotIn(forbidden, blob)
            self.assertIsNone(a["expected_future_filename"])

    def test_validate_asset_manifest_ok(self):
        self.assertEqual(A.validate_asset_manifest(self.doc), [])


# ================================================================ VALIDATORS
class Validators(unittest.TestCase):
    def _good_docs(self):
        facts = _facts(); claims = _claims(facts)
        res = AB.build_aplus(AREG.UNKNOWN_A_PLUS_CAPABILITY, claims, facts, assets=None)
        deps = _fake_deps(facts, claims)
        basic = A.assemble_basic_aplus(deps, res, AREG.UNKNOWN_A_PLUS_CAPABILITY, False)
        prem = A.assemble_premium_aplus_draft(deps, res, AREG.UNKNOWN_A_PLUS_CAPABILITY, False)
        asset = A.build_aplus_asset_manifest(deps, res, AREG.UNKNOWN_A_PLUS_CAPABILITY)
        return basic, prem, asset

    def test_valid_docs_pass(self):
        basic, prem, asset = self._good_docs()
        self.assertEqual(A.validate_basic_aplus(basic), [])
        self.assertEqual(A.validate_premium_draft(prem), [])
        self.assertEqual(A.validate_asset_manifest(asset), [])

    def test_basic_rejects_visible_copy_on_blocked_module(self):
        basic, _, _ = self._good_docs()
        basic["modules"][0]["headline"] = "See the Real Decoration"
        errs = A.validate_basic_aplus(basic)
        self.assertTrue(any("empty visible copy" in e for e in errs))

    def test_premium_rejects_boolean_fallback(self):
        _, prem, _ = self._good_docs()
        prem["basic_fallback"] = True
        errs = A.validate_premium_draft(prem)
        self.assertTrue(errs)

    def test_premium_rejects_filler_dynamic(self):
        _, prem, _ = self._good_docs()
        prem["selected_dynamic_modules"] = ["BRAND_STORY"]        # not in eligible list
        errs = A.validate_premium_draft(prem)
        self.assertTrue(any("filler" in e for e in errs))

    def test_asset_rejects_generated_as_proof(self):
        _, _, asset = self._good_docs()
        asset["assets"][0]["real_or_generated"] = "GENERATED"
        asset["assets"][0]["real_photo_required"] = True
        asset["assets"][0]["status"] = A.ASSET_READY
        errs = A.validate_asset_manifest(asset)
        self.assertTrue(errs)

    def test_manifest_rejects_absolute_and_traversal_paths(self):
        man = {"schema_version": A.STAGE_MANIFEST_SCHEMA_VERSION, "stage_id": "6D",
               "state": A.APLUS_ELIGIBILITY_UNVERIFIED,
               "output_hashes": {"/abs/BASIC-APLUS-CONTENT.json": "x",
                                 "../PREMIUM-APLUS-DRAFT.json": "y"}}
        man["deterministic_content_sha256"] = A.content_sha256(A._manifest_hashable(man))
        errs = A.validate_stage6d_manifest(man, ROOT)
        self.assertTrue(any("absolute" in e for e in errs))
        self.assertTrue(any("parent traversal" in e for e in errs))

    def test_manifest_rejects_forbidden_and_missing_required(self):
        man = {"schema_version": A.STAGE_MANIFEST_SCHEMA_VERSION, "stage_id": "6D",
               "state": A.APLUS_ELIGIBILITY_UNVERIFIED,
               "output_hashes": {"phase6/6D/LISTING-IMAGE-PLAN.json": "x"}}
        man["deterministic_content_sha256"] = A.content_sha256(A._manifest_hashable(man))
        errs = A.validate_stage6d_manifest(man, ROOT)
        self.assertTrue(any("forbidden" in e for e in errs))
        self.assertTrue(any("missing required" in e for e in errs))


# ================================================================ ATOMIC WRITE / PATH SAFETY
class WritePrimitives(unittest.TestCase):
    def _read(self, p):
        with open(p, encoding="utf-8") as f:
            return f.read()

    def test_atomic_write_and_last_valid_preserved(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "x.json")
        A._atomic_write_text(p, "GOOD")
        self.assertEqual(self._read(p), "GOOD")
        # a failing write (temp cannot be created under a file path) leaves the last valid file intact.
        try:
            A._atomic_write_text(os.path.join(p, "nested"), "BAD")   # p is a file, not a dir
        except OSError:
            pass
        self.assertEqual(self._read(p), "GOOD")

    def test_safe_rel_rejects_escape(self):
        base = tempfile.mkdtemp()
        with self.assertRaises(A.Phase6DAssemblyError):
            A._safe_rel(os.path.join(os.path.dirname(base), "outside.json"), base)

    def test_forbid_later_stage_outputs(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "LISTING-IMAGE-PLAN.json"), "w") as f:
            f.write("{}")
        with self.assertRaises(A.Phase6DAssemblyError):
            A._forbid_later_stage_outputs(d)

    def test_physical_claim_classifier_flags_template_headlines(self):
        # the blocked template headlines WOULD assert unverified concepts — which is why 6D suppresses them.
        self.assertFalse(A._is_safe_copy("Personalized For Them"))
        self.assertFalse(A._is_safe_copy("See the Real Decoration"))
        self.assertTrue(A._is_safe_copy("Nurse Sweatshirt"))


if __name__ == "__main__":
    unittest.main()
