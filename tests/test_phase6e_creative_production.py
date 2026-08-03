#!/usr/bin/env python3
"""Phase 6E — evidence-gated ten-job creative production package.

Covers: preflight + dependency verification, category reconciliation, Phase 6D asset reconciliation,
the exact ten-job contract and per-job evidence gates, prompt-vs-asset readiness separation, listing +
A+ prompt documents, the real-photo shot list, the creative asset manifest, overlay / alt-text safety,
cross-image consistency, the creative audit, atomic writes + last-valid protection, the T2 proof,
two-run + three-mode determinism, and the permanent Amazon boundary.
"""
import json
import os
import shutil
import tempfile
import unittest

from creative import creative_production_package as CPP
from production import product_detail_page as PDP
from production import aplus_assembly as APL
import page_auditor as PA
import claim_evidence as CE
import category_policy_registry as CPR

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(_ROOT, "runs", "T2")


def _clone_workspace(dst):
    """Copy the T2 workspace (minus heavy paid exports) so write / tamper tests never touch the real run."""
    shutil.copytree(RUN, dst, ignore=shutil.ignore_patterns("*.xlsx", "*.csv"))
    return dst


@unittest.skipUnless(os.path.isdir(os.path.join(RUN, "phase6", "6D")),
                     "T2 Phase 6D proof workspace is required")
class Phase6EBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = CPP.assemble_phase6e(RUN, connectivity_mode="LOCAL_SAFE")
        cls.jobs = cls.result.jobs
        cls.by_id = {j["job_id"]: j for j in cls.jobs}
        cls.plan = CPP._listing_image_plan_doc(cls.result._deps, cls.jobs, cls.result.category_identity,
                                               cls.result.cross_image_consistency)
        cls.audit = cls.result.creative_audit
        cls.manifest = cls.result.asset_manifest


# ============================================================ PREFLIGHT / DEPENDENCIES
class TestDependencies(Phase6EBase):
    def test_dependencies_load_and_verify(self):
        deps = CPP.load_phase6e_dependencies(RUN, connectivity_mode="TEST_DENY_EXTERNAL")
        self.assertEqual(deps.product_id, "T2")
        self.assertEqual(deps.claim_evidence_sha256[:8], "840e6216")
        self.assertEqual(deps.product_facts_sha256[:8], "0199b4de")
        self.assertEqual(deps.keyword_source_sha256[:8], "52b02f16")
        self.assertEqual(deps.phase6c_manifest_sha256[:8], "2e17a1b9")
        self.assertEqual(deps.phase6d_manifest_sha256[:8], "c4c2c9f7")

    def test_amazon_boundary_is_closed(self):
        import runtime_policy as RP
        pol = RP.load_runtime_policy()
        self.assertTrue(pol.amazon_account_isolation)
        self.assertFalse(pol.amazon_api_enabled)
        self.assertFalse(pol.amazon_network_writes_enabled)
        self.assertFalse(pol.amazon_authenticated_access_enabled)

    def test_no_amazon_upload_or_write_path_in_source(self):
        # no Amazon *action* / upload / SP-API client exists (boundary READS like
        # amazon_authenticated_access_enabled are legitimate assertions and excluded).
        src = open(CPP.__file__, encoding="utf-8").read().lower()
        for token in ("boto3", "sp-api", "sp_api", "putlistings", "put_listings", "catalog_items",
                      "upload_image", "requests.post", "requests.put", "urllib.request"):
            self.assertNotIn(token, src, f"unexpected Amazon-action token {token!r}")

    def test_missing_6d_dependency_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _clone_workspace(os.path.join(td, "T2"))
            os.remove(os.path.join(ws, "phase6", "6D", "APLUS-ASSET-MANIFEST.json"))
            with self.assertRaises(CPP.Phase6EDependencyError):
                CPP.load_phase6e_dependencies(ws)

    def test_missing_6c_dependency_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _clone_workspace(os.path.join(td, "T2"))
            os.remove(os.path.join(ws, "phase6", "6C", "PRODUCT-DETAIL-PAGE.json"))
            with self.assertRaises(CPP.Phase6EDependencyError):
                CPP.load_phase6e_dependencies(ws)

    def test_tampered_6d_artifact_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _clone_workspace(os.path.join(td, "T2"))
            p = os.path.join(ws, "phase6", "6D", "APLUS-ASSET-MANIFEST.json")
            with open(p, "a", encoding="utf-8") as f:
                f.write("\n")
            with self.assertRaises(CPP.Phase6EDependencyError):
                CPP.load_phase6e_dependencies(ws)

    def test_dependency_hash_mismatch_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _clone_workspace(os.path.join(td, "T2"))
            man = os.path.join(ws, "phase6", "6C", "STAGE-6C-MANIFEST.json")
            d = json.load(open(man, encoding="utf-8"))
            d["input_hashes"]["claim_evidence_sha256"] = "deadbeef" * 8
            json.dump(d, open(man, "w", encoding="utf-8"))
            with self.assertRaises(CPP.Phase6EDependencyError):
                CPP.load_phase6e_dependencies(ws)

    def test_upstream_artifacts_not_modified(self):
        """Writes 6E and proves 6A-6D are untouched -- in a CLONE, never the real run.

        This test used to pass `workspace_dir=RUN`, so proving that 6E generation leaves upstream
        alone was done by generating 6E *into the owner's real workspace*. It guarded 6A-6D and
        clobbered 6E, the one stage it did not check, with `completed_at="t"`.

        That is exactly the `"t"` found in the real STAGE-6E-MANIFEST.json on 2026-08-02. The
        clone helper directly above already existed and says "so write / tamper tests never touch
        the real run"; this test simply did not use it.
        """
        parent = tempfile.mkdtemp(prefix="6e-upstream-")
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        ws = _clone_workspace(os.path.join(parent, "T2"))
        before = {}
        for stage in ("6A", "6B", "6C", "6D"):
            d = os.path.join(ws, "phase6", stage)
            for f in os.listdir(d):
                p = os.path.join(d, f)
                if os.path.isfile(p):
                    before[p] = CPP._file_sha256(p)
        CPP.write_phase6e_artifacts(CPP.assemble_phase6e(ws), workspace_dir=ws,
                                    started_at="t", completed_at="t")
        for p, h in before.items():
            self.assertEqual(CPP._file_sha256(p), h, f"upstream artifact changed: {p}")


# ============================================================ CATEGORY RECONCILIATION
class TestCategoryReconciliation(Phase6EBase):
    def setUp(self):
        self.ci = CPP.reconcile_category_identity(self.result._deps)

    def test_canonical_is_us_apparel(self):
        self.assertEqual(self.ci["canonical_category_id"], "US:apparel")
        self.assertTrue(self.ci["canonical_unchanged"])

    def test_display_and_marketplace(self):
        self.assertEqual(self.ci["display_category"], "apparel")
        self.assertEqual(self.ci["marketplace"], "US")

    def test_display_text_does_not_replace_canonical(self):
        self.assertNotEqual(self.ci["canonical_category_id"], self.ci["display_category"])
        self.assertTrue(self.ci["registry_reconciled"])

    def test_registry_resolves_via_display_not_composite(self):
        # the composite string is an OUTPUT identifier, never a registry key.
        pol = CPR.resolve_category_policy("apparel", marketplace="US")
        self.assertEqual(pol.category_identifier, "US:apparel")
        self.assertFalse(pol.fallback_used)

    def test_image_policy_unverified_no_invented_rules(self):
        self.assertEqual(self.ci["category_image_policy_state"], "CATEGORY_IMAGE_POLICY_UNVERIFIED")
        for k in ("image_dimensions", "background_rule", "aspect_ratio", "minimum_resolution"):
            self.assertIsNone(self.ci[k])

    def test_category_reconciliation_is_deterministic(self):
        a = CPP.reconcile_category_identity(self.result._deps)
        b = CPP.reconcile_category_identity(self.result._deps)
        self.assertEqual(a, b)


# ============================================================ 6D ASSET RECONCILIATION
class TestAssetReconciliation(Phase6EBase):
    def setUp(self):
        self.r = CPP.reconcile_phase6d_asset_requirements(self.result._deps)

    def test_active_count(self):
        self.assertEqual(self.r["active_asset_requirement_count"], 4)

    def test_deferred_candidate_counts(self):
        self.assertEqual(self.r["deferred_candidate_asset_requirement_count"], 5)
        self.assertEqual(sorted(self.r["deferred_candidate_real_assets"]),
                         ["DESIGN_VARIATIONS", "GARMENT_OPTIONS", "GIFT_PACKAGING", "LIFESTYLE",
                          "PRODUCTION_PROCESS"])

    def test_unique_and_missing_counts(self):
        self.assertEqual(self.r["unique_required_asset_count"], 4)
        self.assertEqual(self.r["missing_real_asset_count"], 3)
        self.assertEqual(self.r["current_verified_asset_count"], 0)

    def test_generated_proof_zero(self):
        self.assertEqual(self.r["generated_accepted_as_real_proof_count"], 0)

    def test_deferred_not_promoted_and_separate(self):
        self.assertFalse(set(self.r["deferred_candidate_real_assets"]) & set(self.r["active_asset_ids"]))
        self.assertTrue(self.r["reconciles"])

    def test_reconciliation_deterministic(self):
        a = CPP.reconcile_phase6d_asset_requirements(self.result._deps)
        b = CPP.reconcile_phase6d_asset_requirements(self.result._deps)
        self.assertEqual(a, b)


# ============================================================ AUTHORITY
class TestAuthority(Phase6EBase):
    def test_single_creative_authority_no_duplicate_planner(self):
        cdir = os.path.join(_ROOT, "creative")
        self.assertFalse(os.path.exists(os.path.join(cdir, "listing_image_planner.py")))
        self.assertFalse(os.path.exists(os.path.join(cdir, "creative_asset_audit.py")))
        self.assertTrue(os.path.exists(os.path.join(cdir, "creative_production_package.py")))

    def test_reuses_shared_engines(self):
        self.assertIs(CPP.PA, PA)
        self.assertIs(CPP.CE, CE)
        self.assertIs(CPP.CPR, CPR)
        self.assertIs(CPP.PDP, PDP)
        self.assertIs(CPP.APL, APL)

    def test_reuses_pageauditor_on_creative_text(self):
        audit = CPP.run_creative_page_audit(self.result._deps, self.jobs)
        self.assertIn("leakage_summary", audit)
        self.assertEqual(audit["status"], PA.PASS)


# ============================================================ TEN-JOB CONTRACT
class TestTenJobContract(Phase6EBase):
    EXPECTED = ("MAIN_PRODUCT", "REAL_DECORATION_PROOF", "PERSONALIZATION_GUIDE", "SIZE_AND_FIT",
                "COLOR_AND_GARMENT_OPTIONS", "LIFESTYLE_RECIPIENT", "OCCASION_WHEN_SUPPORTED",
                "PRODUCT_DETAILS_AND_CARE", "HOW_TO_ORDER", "VERIFIED_COMPARISON_OR_VARIATION")

    def test_exactly_ten_jobs(self):
        self.assertEqual(len(self.jobs), 10)

    def test_exact_order_and_positions(self):
        self.assertEqual(tuple(j["job_id"] for j in self.jobs), self.EXPECTED)
        self.assertEqual([j["position"] for j in self.jobs], list(range(1, 11)))

    def test_each_job_at_its_position(self):
        for i, jid in enumerate(self.EXPECTED, start=1):
            self.assertEqual(self.by_id[jid]["position"], i)

    def test_unique_ids_and_positions(self):
        ids = [j["job_id"] for j in self.jobs]
        pos = [j["position"] for j in self.jobs]
        self.assertEqual(len(set(ids)), 10)
        self.assertEqual(len(set(pos)), 10)

    def test_plan_validates(self):
        self.assertEqual(CPP.validate_listing_image_plan(self.plan), [])

    def test_missing_job_rejected(self):
        bad = dict(self.plan)
        bad["jobs"] = self.plan["jobs"][:9]
        bad["job_count"] = 9
        self.assertTrue(CPP.validate_listing_image_plan(bad))

    def test_extra_job_rejected(self):
        bad = dict(self.plan)
        bad["jobs"] = self.plan["jobs"] + [dict(self.plan["jobs"][0])]
        bad["job_count"] = 11
        self.assertTrue(CPP.validate_listing_image_plan(bad))

    def test_duplicate_job_rejected(self):
        bad = dict(self.plan)
        jobs = list(self.plan["jobs"])
        jobs[1] = dict(jobs[0])
        bad["jobs"] = jobs
        self.assertTrue(CPP.validate_listing_image_plan(bad))

    def test_ten_records_do_not_imply_ten_ready_images(self):
        ss = CPP._state_summary(self.jobs)
        self.assertEqual(ss[CPP.ASSET_READY], 0)
        self.assertGreater(ss[CPP.OWNER_FACT_REQUIRED] + ss[CPP.REAL_PHOTO_REQUIRED], 0)

    def test_no_filler_prompts(self):
        prompts = [j for j in self.jobs if j["prompt"]]
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0]["job_id"], "LIFESTYLE_RECIPIENT")


# ============================================================ JOB STATES
class TestJobStates(Phase6EBase):
    def test_expected_states(self):
        want = {
            "MAIN_PRODUCT": CPP.REAL_PHOTO_REQUIRED,
            "REAL_DECORATION_PROOF": CPP.REAL_PHOTO_REQUIRED,
            "PERSONALIZATION_GUIDE": CPP.OWNER_FACT_REQUIRED,
            "SIZE_AND_FIT": CPP.OWNER_FACT_REQUIRED,
            "COLOR_AND_GARMENT_OPTIONS": CPP.OWNER_FACT_REQUIRED,
            "LIFESTYLE_RECIPIENT": CPP.PROMPT_READY,
            "OCCASION_WHEN_SUPPORTED": CPP.NOT_APPLICABLE,
            "PRODUCT_DETAILS_AND_CARE": CPP.OWNER_FACT_REQUIRED,
            "HOW_TO_ORDER": CPP.OWNER_FACT_REQUIRED,
            "VERIFIED_COMPARISON_OR_VARIATION": CPP.NOT_APPLICABLE,
        }
        for jid, st in want.items():
            self.assertEqual(self.by_id[jid]["status"], st, jid)

    def test_all_states_valid(self):
        for j in self.jobs:
            self.assertIn(j["status"], CPP.JOB_STATES)

    def test_prompt_ready_has_safe_prompt(self):
        j = self.by_id["LIFESTYLE_RECIPIENT"]
        self.assertEqual(j["prompt_readiness"], CPP.PR_READY)
        self.assertTrue(j["prompt"])
        self.assertTrue(j["negative_constraints"])

    def test_real_photo_rejects_generated(self):
        for jid in ("MAIN_PRODUCT", "REAL_DECORATION_PROOF"):
            j = self.by_id[jid]
            self.assertTrue(j["real_proof_required"])
            self.assertFalse(j["generated_mockup_allowed"])
            self.assertIsNone(j["prompt"])

    def test_owner_fact_has_missing_facts(self):
        j = self.by_id["PERSONALIZATION_GUIDE"]
        self.assertTrue(any(b.startswith("owner_fact:") for b in j["blockers"]))

    def test_not_applicable_has_reason(self):
        for jid in ("OCCASION_WHEN_SUPPORTED", "VERIFIED_COMPARISON_OR_VARIATION"):
            self.assertTrue(self.by_id[jid]["not_applicable_reason"])

    def test_prompt_readiness_differs_from_asset_readiness(self):
        j = self.by_id["LIFESTYLE_RECIPIENT"]
        self.assertEqual(j["prompt_readiness"], CPP.PR_READY)
        self.assertEqual(j["asset_readiness"], CPP.AR_MISSING)   # prompt-ready != asset exists
        p = self.by_id["PERSONALIZATION_GUIDE"]
        self.assertEqual(p["prompt_readiness"], CPP.PR_OWNER_FACT_REQUIRED)
        self.assertEqual(p["asset_readiness"], CPP.AR_REAL_PHOTO_REQUIRED)

    def test_ready_for_6f_is_not_publishability(self):
        self.assertTrue(self.result.ready_for_6f)
        self.assertEqual(self.result.listing_publishability_state, "SAFE_DRAFT_OWNER_FACTS_REQUIRED")

    def test_state_flips_when_a_fact_is_verified(self):
        # if the owner verifies personalization_fields, the guide job leaves OWNER_FACT_REQUIRED.
        spec = next(s for s in CPP.JOB_SPECS if s["job_id"] == "PERSONALIZATION_GUIDE")
        st = CPP._resolve_job_state(spec, missing_facts=[], missing_claims=[], real_present=False)
        self.assertEqual(st, CPP.REAL_PHOTO_REQUIRED)
        st2 = CPP._resolve_job_state(spec, missing_facts=[], missing_claims=[], real_present=True)
        self.assertEqual(st2, CPP.ASSET_READY)


# ============================================================ PER-JOB SEMANTICS
class TestJobSemantics(Phase6EBase):
    def test_main_product_requires_real_photo_and_facts(self):
        j = self.by_id["MAIN_PRODUCT"]
        self.assertIn("garment_type", j["required_fact_ids"])
        self.assertIn("color_options", j["required_fact_ids"])
        self.assertIsNone(j["design_reference"])
        self.assertIsNone(j["garment_source"])
        self.assertIsNone(j["product_color_source"])

    def test_main_product_invents_no_universal_rules(self):
        j = self.by_id["MAIN_PRODUCT"]
        self.assertIsNone(j["image_dimensions"])
        self.assertIsNone(j["aspect_ratio"])
        self.assertEqual(j["background_requirements"], "CATEGORY_IMAGE_POLICY_UNVERIFIED")

    def test_decoration_proof_needs_real_and_no_method_word(self):
        j = self.by_id["REAL_DECORATION_PROOF"]
        self.assertTrue(j["real_proof_required"])
        self.assertIsNone(j["prompt"])
        # no invented decoration wording is asserted anywhere in the job's customer-visible fields.
        self.assertEqual(j["overlay_copy"], "")

    def test_personalization_requires_fields(self):
        j = self.by_id["PERSONALIZATION_GUIDE"]
        self.assertIn("personalization_fields", j["required_fact_ids"])
        self.assertIsNone(j["prompt"])

    def test_size_job_no_invented_measurements(self):
        j = self.by_id["SIZE_AND_FIT"]
        self.assertIn("size_range", j["required_fact_ids"])
        self.assertIn("measurements", j["required_fact_ids"])
        self.assertIsNone(j["prompt"])

    def test_color_not_defaulted_to_standard(self):
        j = self.by_id["COLOR_AND_GARMENT_OPTIONS"]
        self.assertIsNone(j["product_color_source"])
        self.assertIn("color_options", j["required_fact_ids"])

    def test_lifestyle_prompt_is_non_evidentiary_and_owner_reviewed(self):
        j = self.by_id["LIFESTYLE_RECIPIENT"]
        self.assertFalse(j["real_proof_required"])
        self.assertTrue(j["generated_mockup_allowed"])
        self.assertIn("non-evidentiary", j["prompt_status"].lower().replace("_", "-"))
        # the anchoring claim is the verified recipient only.
        self.assertEqual(j["required_claim_ids"], ["CLM-RECIPIENT"])

    def test_lifestyle_prompt_depicts_no_unsupported_attribute(self):
        j = self.by_id["LIFESTYLE_RECIPIENT"]
        low = j["prompt"].lower()
        for banned in ("embroider", "cotton", "gift", "size", "colour", "color of", "personalized name"):
            self.assertNotIn(banned, low)

    def test_occasion_not_applicable_no_gift(self):
        # the occasion job emits no prompt / overlay and its reason states gift/occasion are BLOCKED
        # (explaining the block is fine; promoting gift as usable copy is not).
        j = self.by_id["OCCASION_WHEN_SUPPORTED"]
        self.assertEqual(j["status"], CPP.NOT_APPLICABLE)
        self.assertIsNone(j["prompt"])
        self.assertEqual(j["overlay_copy"], "")
        self.assertIn("blocked", (j["not_applicable_reason"] or "").lower())

    def test_details_and_care_requires_evidence(self):
        j = self.by_id["PRODUCT_DETAILS_AND_CARE"]
        for f in ("material_composition", "care_instructions"):
            self.assertIn(f, j["required_fact_ids"])
        self.assertIsNone(j["prompt"])

    def test_how_to_order_requires_workflow_fact(self):
        j = self.by_id["HOW_TO_ORDER"]
        self.assertIn("personalization_fields", j["required_fact_ids"])
        self.assertIsNone(j["prompt"])

    def test_comparison_not_applicable(self):
        j = self.by_id["VERIFIED_COMPARISON_OR_VARIATION"]
        self.assertEqual(j["status"], CPP.NOT_APPLICABLE)
        self.assertIsNone(j["prompt"])


# ============================================================ PROMPT DOCUMENTS
class TestPromptDocuments(Phase6EBase):
    def test_only_prompt_ready_has_body(self):
        md = CPP._md_listing_image_prompts(self.result._deps, self.jobs)
        self.assertEqual(md.count("AI PROMPT (planning only"), 1)
        self.assertIn("NO PROMPT GENERATED", md)
        self.assertIn("REAL PHOTOGRAPH REQUIRED", md)

    def test_prompt_package_validates(self):
        self.assertEqual(CPP.validate_prompt_package(self.jobs, aplus_generation_prompt_count=0), [])

    def test_prompt_package_rejects_body_on_blocked_job(self):
        jobs = [dict(j) for j in self.jobs]
        jobs[0]["prompt"] = "some body"    # MAIN_PRODUCT is REAL_PHOTO_REQUIRED
        self.assertTrue(CPP.validate_prompt_package(jobs, aplus_generation_prompt_count=0))

    def test_aplus_prompts_zero_and_deferred_separated(self):
        md = CPP._md_aplus_image_prompts(self.result._deps, self.result.asset_recon)
        self.assertIn("0 final A+ generation prompts", md)
        self.assertIn("DEFERRED PREMIUM CANDIDATE ASSETS", md)
        self.assertEqual(self.result.aplus_prompt_records["aplus_generation_prompt_count"], 0)

    def test_every_prompt_has_negative_constraints(self):
        for j in self.jobs:
            self.assertTrue(j["negative_constraints"])

    def test_documents_are_deterministic(self):
        a = CPP._md_listing_image_prompts(self.result._deps, self.jobs)
        b = CPP._md_listing_image_prompts(self.result._deps, self.jobs)
        self.assertEqual(a, b)


# ============================================================ SHOT LIST
class TestShotList(Phase6EBase):
    def test_shot_list_validates(self):
        self.assertEqual(CPP.validate_shot_list(self.result.real_photo_shots, self.jobs), [])

    def test_every_real_proof_asset_has_a_shot(self):
        purposes = {s["proof_purpose"] for s in self.result.real_photo_shots}
        for jid in ("MAIN_PRODUCT", "REAL_DECORATION_PROOF", "PERSONALIZATION_GUIDE",
                    "PRODUCT_DETAILS_AND_CARE"):
            self.assertIn(self.by_id[jid]["proof_purpose"], purposes)

    def test_shots_have_acceptance_and_rejection(self):
        for s in self.result.real_photo_shots:
            self.assertTrue(s["acceptance_criteria"])
            self.assertTrue(s["rejection_criteria"])
            self.assertTrue(s["mapped_job_ids"] or s["mapped_aplus_asset_ids"])

    def test_ai_not_accepted_as_evidence(self):
        for s in self.result.real_photo_shots:
            self.assertFalse(s["ai_generated_accepted_as_evidence"])
            self.assertTrue(any("AI" in r for r in s["rejection_criteria"]))

    def test_unknown_specs_remain_unverified(self):
        for s in self.result.real_photo_shots:
            self.assertIn("UNVERIFIED", s["minimum_resolution"])

    def test_shot_list_deterministic(self):
        a = CPP.build_real_photo_shot_list_records(self.result._deps)
        b = CPP.build_real_photo_shot_list_records(self.result._deps)
        self.assertEqual(a, b)


# ============================================================ ASSET MANIFEST
class TestAssetManifest(Phase6EBase):
    def test_manifest_validates(self):
        self.assertEqual(CPP.validate_creative_asset_manifest(self.manifest), [])

    def test_unique_asset_ids(self):
        ids = [a["asset_id"] for a in self.manifest["active_assets"]]
        self.assertEqual(len(set(ids)), len(ids))

    def test_shared_assets_represented_once(self):
        shared = [a for a in self.manifest["active_assets"] if a["asset_scope"] == "SHARED"]
        for a in shared:
            self.assertGreaterEqual(len(a["linked_job_ids"]) + len(a["linked_module_keys"]), 2)
        # GARMENT_OR_COLOR is referenced by two listing jobs but appears once.
        gc = [a for a in self.manifest["active_assets"] if a["asset_id"] == "GARMENT_OR_COLOR"]
        self.assertEqual(len(gc), 1)
        self.assertEqual(sorted(gc[0]["linked_job_ids"]), ["COLOR_AND_GARMENT_OPTIONS", "SIZE_AND_FIT"])

    def test_missing_file_cannot_be_ready(self):
        for a in self.manifest["active_assets"]:
            self.assertNotEqual(a["state"], CPP.AR_READY)
            self.assertFalse(a["valid_bytes"])

    def test_generated_cannot_satisfy_real_proof(self):
        bad = json.loads(json.dumps(self.manifest))
        a = bad["active_assets"][0]
        a["real_or_generated"] = "GENERATED"
        a["real_proof_required"] = True
        self.assertTrue(CPP.validate_creative_asset_manifest(bad))

    def test_absolute_path_rejected(self):
        bad = json.loads(json.dumps(self.manifest))
        bad["active_assets"][0]["current_relative_path"] = "C:/x.jpg" if os.name == "nt" else "/x.jpg"
        self.assertTrue(CPP.validate_creative_asset_manifest(bad))

    def test_parent_traversal_rejected(self):
        bad = json.loads(json.dumps(self.manifest))
        bad["active_assets"][0]["current_relative_path"] = "../secret.jpg"
        self.assertTrue(CPP.validate_creative_asset_manifest(bad))

    def test_generated_proof_acceptance_zero(self):
        self.assertEqual(self.manifest["ai_generated_assets_accepted_as_real_proof"], 0)

    def test_deferred_candidates_preserved(self):
        self.assertEqual(len(self.manifest["deferred_candidate_assets"]), 5)
        for d in self.manifest["deferred_candidate_assets"]:
            self.assertEqual(d["classification"], "DEFERRED_PREMIUM_CANDIDATE")
            self.assertEqual(d["state"], "DEFERRED_NOT_ACTIVE")


# ============================================================ OVERLAY / ALT TEXT
class TestOverlayAndAltText(Phase6EBase):
    def test_only_verified_overlay_present(self):
        overlays = [j for j in self.jobs if j["overlay_copy"]]
        self.assertEqual(len(overlays), 1)
        j = overlays[0]
        self.assertEqual(j["job_id"], "LIFESTYLE_RECIPIENT")
        self.assertEqual(j["overlay_copy_lineage"]["atomic_claim_ids"], ["CLM-RECIPIENT"])
        self.assertEqual(j["overlay_copy_lineage"]["source_sha256"][:8], "840e6216")

    def test_unsafe_overlay_stays_empty(self):
        for j in self.jobs:
            if j["job_id"] != "LIFESTYLE_RECIPIENT":
                self.assertEqual(j["overlay_copy"], "")
                self.assertEqual(j["overlay_copy_lineage"]["state"], "NO_SAFE_OVERLAY_COPY")

    def test_no_final_alt_text_before_asset(self):
        for j in self.jobs:
            self.assertEqual(j["alt_text"], "")
            self.assertEqual(j["alt_text_lineage"]["state"], "PENDING_ASSET_AND_OWNER_REVIEW")

    def test_overlay_and_scene_pass_pageauditor(self):
        audit = CPP.run_creative_page_audit(self.result._deps, self.jobs)
        self.assertEqual(CPP._leak_total(audit["leakage_summary"]), 0)
        self.assertFalse(audit.get("hard_failures"))


# ============================================================ CROSS-IMAGE CONSISTENCY
class TestCrossImageConsistency(Phase6EBase):
    def test_unknown_values_are_null_not_defaults(self):
        c = self.result.cross_image_consistency
        for k in ("canonical_garment_source", "canonical_garment_type", "canonical_color_source",
                  "design_reference_id", "design_dimensions", "design_placement", "design_scale",
                  "typography_source", "thread_or_decoration_color_source"):
            self.assertIsNone(c[k], k)

    def test_forbidden_inconsistencies_listed(self):
        c = self.result.cross_image_consistency
        joined = " ".join(c["forbidden_inconsistencies"]).lower()
        self.assertIn("colour", joined)
        self.assertIn("placement", joined)
        self.assertIn("embroidery in one image", joined)
        self.assertEqual(c["current_readiness"], "BLOCKED_OWNER_FACTS_AND_REAL_ASSETS_REQUIRED")

    def test_verified_recipient_preserved(self):
        self.assertEqual(self.result.cross_image_consistency["verified_recipient_concept"], "For nurses.")


# ============================================================ CREATIVE AUDIT
class TestCreativeAudit(Phase6EBase):
    def test_audit_validates(self):
        self.assertEqual(CPP.validate_creative_audit(self.audit, self.plan, self.manifest), [])

    def test_counts_match_plan(self):
        for key in ("prompt_ready_count", "asset_ready_count", "real_photo_required_count",
                    "owner_fact_required_count", "not_applicable_count", "blocked_unsafe_count",
                    "job_count"):
            self.assertEqual(self.audit[key], self.plan[key])

    def test_generated_proof_and_unsupported_zero(self):
        self.assertEqual(self.audit["ai_generated_assets_accepted_as_real_proof_count"], 0)
        self.assertEqual(self.audit["unsupported_visual_claim_count"], 0)

    def test_pageauditor_reference_resolves(self):
        self.assertEqual(self.audit["page_auditor_result"]["status"], PA.PASS)
        self.assertEqual(self.audit["page_auditor_result"]["hard_failure_count"], 0)

    def test_ready_for_6f_not_publishability(self):
        self.assertTrue(self.audit["ready_for_6f"])
        self.assertEqual(self.audit["listing_publishability"], "SAFE_DRAFT_OWNER_FACTS_REQUIRED")
        self.assertIn("does NOT mean", self.audit["ready_for_6f_note"])

    def test_prompt_and_shot_counts(self):
        self.assertEqual(self.audit["listing_generation_prompt_count"], 1)
        self.assertEqual(self.audit["aplus_generation_prompt_count"], 0)
        self.assertEqual(self.audit["real_photo_shot_count"], 7)


# ============================================================ ARTIFACTS / ATOMIC WRITES
class TestArtifacts(Phase6EBase):
    def test_write_and_verify(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _clone_workspace(os.path.join(td, "T2"))
            res = CPP.assemble_phase6e(ws)
            CPP.write_phase6e_artifacts(res, workspace_dir=ws, started_at="t", completed_at="t")
            dest = CPP.phase6e_dir(ws)
            for name in CPP.REQUIRED_ARTIFACTS + CPP.SUPPORT_ARTIFACTS + (CPP.STAGE_MANIFEST_FILENAME,):
                self.assertTrue(os.path.exists(os.path.join(dest, name)), name)
            v = CPP.verify_phase6e_artifacts(dest, workspace_dir=ws)
            self.assertTrue(v["ok"], v["errors"])

    def test_no_image_or_phase6f_files_written(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _clone_workspace(os.path.join(td, "T2"))
            res = CPP.assemble_phase6e(ws)
            CPP.write_phase6e_artifacts(res, workspace_dir=ws, started_at="t", completed_at="t")
            dest = CPP.phase6e_dir(ws)
            for f in os.listdir(dest):
                ext = os.path.splitext(f)[1].lower()
                self.assertNotIn(ext, CPP.FORBIDDEN_IMAGE_EXTENSIONS)
                self.assertNotIn(f, CPP.FORBIDDEN_ARTIFACTS)

    def test_failed_write_preserves_last_valid(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _clone_workspace(os.path.join(td, "T2"))
            res = CPP.assemble_phase6e(ws)
            CPP.write_phase6e_artifacts(res, workspace_dir=ws, started_at="t", completed_at="t")
            dest = CPP.phase6e_dir(ws)
            good = {f: CPP._file_sha256(os.path.join(dest, f)) for f in CPP.REQUIRED_ARTIFACTS}
            res.jobs = res.jobs[:9]      # corrupt: only nine jobs
            with self.assertRaises(CPP.Phase6EAssemblyError):
                CPP.write_phase6e_artifacts(res, workspace_dir=ws, started_at="t2", completed_at="t2")
            for f, h in good.items():
                self.assertEqual(CPP._file_sha256(os.path.join(dest, f)), h, f"{f} was clobbered")

    def test_manifest_rejects_absolute_path(self):
        man = CPP._stage_manifest_doc(self.result, {"/abs/x.json": "0" * 64})
        self.assertTrue(any("absolute" in e for e in CPP.validate_stage6e_manifest(man, RUN)))

    def test_manifest_rejects_parent_traversal(self):
        man = CPP._stage_manifest_doc(self.result, {"../x.json": "0" * 64})
        self.assertTrue(any("parent traversal" in e for e in CPP.validate_stage6e_manifest(man, RUN)))

    def test_manifest_rejects_image_output(self):
        outs = {f"phase6/6E/{n}": "0" * 64 for n in CPP.REQUIRED_ARTIFACTS}
        outs["phase6/6E/main.jpg"] = "0" * 64
        man = CPP._stage_manifest_doc(self.result, outs)
        self.assertTrue(any("image file" in e for e in CPP.validate_stage6e_manifest(man, RUN)))

    def test_seven_required_artifacts_named(self):
        self.assertEqual(CPP.REQUIRED_ARTIFACTS, (
            "LISTING-IMAGE-PLAN.json", "LISTING-IMAGE-PROMPTS.md", "APLUS-IMAGE-PROMPTS.md",
            "REAL_PHOTO_SHOT_LIST.md", "CREATIVE-ASSET-MANIFEST.json", "CREATIVE-ASSET-AUDIT.json"))


# ============================================================ T2 PROOF + DETERMINISM
class TestT2ProofAndDeterminism(Phase6EBase):
    def test_identity_and_hashes_unchanged(self):
        d = self.result._deps
        self.assertEqual(d.product_id, "T2")
        self.assertEqual(d.product_type, "sweatshirt")
        self.assertEqual(d.audience, "nurses")
        self.assertEqual(self.result.canonical_category_id, "US:apparel")
        self.assertEqual(self.result.display_category, "apparel")
        self.assertEqual(self.result.marketplace, "US")
        self.assertEqual(d.keyword_source_sha256[:8], "52b02f16")
        self.assertEqual(d.claim_evidence_sha256[:8], "840e6216")

    def test_tier_counts_unchanged(self):
        tiers = self.result._deps.workspace["keyword_tier_counts"]
        self.assertEqual(tiers["TIER_A"], 74)
        self.assertEqual(tiers["TIER_B"], 378)

    def test_two_run_determinism(self):
        r2 = CPP.assemble_phase6e(RUN, connectivity_mode="LOCAL_SAFE")
        self.assertEqual(self.result.deterministic_content_sha256(),
                         r2.deterministic_content_sha256())
        self.assertEqual(self.result.asset_manifest["deterministic_content_sha256"],
                         r2.asset_manifest["deterministic_content_sha256"])
        self.assertEqual(self.result.creative_audit["deterministic_content_sha256"],
                         r2.creative_audit["deterministic_content_sha256"])

    def test_three_mode_determinism(self):
        modes = ["CONNECTED_RESEARCH", "LOCAL_SAFE", "TEST_DENY_EXTERNAL"]
        results = [CPP.assemble_phase6e(RUN, connectivity_mode=m) for m in modes]
        base = results[0].deterministic_content_sha256()
        for r in results[1:]:
            self.assertEqual(r.deterministic_content_sha256(), base)
            self.assertEqual(r.asset_manifest["deterministic_content_sha256"],
                             results[0].asset_manifest["deterministic_content_sha256"])

    def test_written_artifacts_two_run_stable(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _clone_workspace(os.path.join(td, "T2"))
            CPP.write_phase6e_artifacts(CPP.assemble_phase6e(ws), workspace_dir=ws,
                                        started_at="t", completed_at="t")
            dest = CPP.phase6e_dir(ws)
            first = {f: CPP._file_sha256(os.path.join(dest, f))
                     for f in CPP.REQUIRED_ARTIFACTS + CPP.SUPPORT_ARTIFACTS}
            CPP.write_phase6e_artifacts(CPP.assemble_phase6e(ws), workspace_dir=ws,
                                        started_at="t", completed_at="t")
            for f, h in first.items():
                self.assertEqual(CPP._file_sha256(os.path.join(dest, f)), h, f)

    def test_state_is_truthful(self):
        self.assertEqual(self.result.phase6e_state, "SAFE_CREATIVE_PLAN_OWNER_FACTS_REQUIRED")
        self.assertTrue(self.result.ready_for_6f)


if __name__ == "__main__":
    unittest.main()
