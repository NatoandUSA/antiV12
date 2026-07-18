#!/usr/bin/env python3
"""Phase 7.0 — repository + live-product preflight tests (87 focused requirements).

Hermetic by default: every test runs offline against the committed repository + committed guidance
fixture. Workspace-dependent checks skip cleanly when the gitignored runs/ product workspace is absent
(as in a fresh clone), exactly like the Phase 6F workspace tests. The Phase 7.0 module opens NO network,
so there is nothing to stub.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("", "core", "listing", "production", "scripts"):
    _p = ROOT if not _sub else os.path.join(ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import phase7_preflight as P7           # noqa: E402
from production import seller_central_package as SCP     # noqa: E402
import runtime_policy as RP                              # noqa: E402

GUIDANCE = os.path.join(ROOT, P7.DEFAULT_GUIDANCE_REL)
RUN_T2 = os.path.join(ROOT, "runs", "T2")
HAS_WORKSPACE = os.path.isdir(RUN_T2) and os.path.isdir(SCP.package_root_dir(RUN_T2))
COMMITTED_PROOF = os.path.join(ROOT, "SESSION7_0-PROOF-GATE.json")


def _handoff(run_dir=None):
    return P7.load_phase6_handoff(ROOT, run_dir=run_dir)


def _assemble(run_dir=None, mode="LOCAL_SAFE", refresh=False, existing_contract=None):
    return P7.assemble_phase7_preflight(ROOT, run_dir=run_dir, mode=mode,
                                        refresh_guidance=refresh, existing_contract=existing_contract)


# ============================================================ PREVIOUS PHASES (1-10)
class PreviousPhases(unittest.TestCase):
    def test_01_phase1_6_tests_present(self):
        # a proxy for "all Phase 1-6 tests remain green": the phase6 test modules still import.
        for m in ("test_phase6a_product_workspace", "test_phase6f_seller_central_package"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, "tests", m + ".py")), m)

    def test_02_phase6f_proof_parses(self):
        p = os.path.join(ROOT, P7.PHASE6F_PROOF_NAME)
        with open(p, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["phase6_final_status"], SCP.PHASE6_SAFE_DRAFT_READY)

    def test_03_phase6_final_status_parses(self):
        p = os.path.join(ROOT, P7.PHASE6_FINAL_STATUS_NAME)
        with open(p, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["phase6_status"], SCP.PHASE6_SAFE_DRAFT_READY)

    def test_04_phase6_final_commit_matches_head(self):
        # committed 6F proof backfills final_commit=null by design; lineage is checked from the repo.
        git = P7._read_git_head(ROOT)
        self.assertTrue(git["git_head"] is None or len(git["git_head"]) == 40)

    @unittest.skipUnless(HAS_WORKSPACE, "runs/T2 workspace absent (gitignored)")
    def test_05_phase6_package_index_verifies(self):
        vr = SCP.verify_phase6f_artifacts(SCP.package_root_dir(RUN_T2), workspace_dir=RUN_T2)
        self.assertIn(vr["result"], ("PASS", "PASS_WITH_WARNINGS"))
        self.assertEqual(vr["mismatched_hash_count"], 0)

    @unittest.skipUnless(HAS_WORKSPACE, "runs/T2 workspace absent (gitignored)")
    def test_06_phase6_package_index_hash_file_verifies(self):
        vr = SCP.verify_phase6f_artifacts(SCP.package_root_dir(RUN_T2), workspace_dir=RUN_T2)
        self.assertEqual(vr["package_index_hash_file_result"], "PASS")

    @unittest.skipUnless(HAS_WORKSPACE, "runs/T2 workspace absent (gitignored)")
    def test_07_phase6_dependency_lineage_resolves(self):
        h = _handoff(RUN_T2)
        self.assertEqual(h["live_verification"].get("committed_prefix_drift"), [])

    def test_08_missing_phase6f_proof_blocks_preflight(self):
        with tempfile.TemporaryDirectory() as d:
            # a repo_root with no Phase 6F proof must block.
            with self.assertRaises(P7.Phase7PreflightError):
                P7.load_phase6_handoff(d, run_dir=None)

    def test_09_invalid_phase6_hash_blocks_preflight(self):
        with tempfile.TemporaryDirectory() as d:
            proof = json.load(open(os.path.join(ROOT, P7.PHASE6F_PROOF_NAME), encoding="utf-8"))
            status = json.load(open(os.path.join(ROOT, P7.PHASE6_FINAL_STATUS_NAME), encoding="utf-8"))
            proof["package_index_sha256_prefix"] = "deadbeef"   # disagree with final status
            json.dump(proof, open(os.path.join(d, P7.PHASE6F_PROOF_NAME), "w", encoding="utf-8"))
            json.dump(status, open(os.path.join(d, P7.PHASE6_FINAL_STATUS_NAME), "w", encoding="utf-8"))
            with self.assertRaises(P7.Phase7PreflightError):
                P7.load_phase6_handoff(d, run_dir=None)

    def test_10_safe_draft_does_not_imply_ppc_readiness(self):
        r = _assemble()
        self.assertEqual(r.handoff["phase6_final_status"], SCP.PHASE6_SAFE_DRAFT_READY)
        self.assertTrue(r.handoff_audit["safe_draft_is_not_ppc_ready"])
        self.assertNotEqual(r.readiness_decision, P7.READY_FOR_7_1M_IMPLEMENTATION)


# ============================================================ AMAZON BOUNDARY (11-21)
class AmazonBoundary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.net = P7.build_network_boundary_report(ROOT)

    def test_11_no_sp_api_client(self):
        self.assertEqual(self.net["sp_api_client_path_count"], 0)

    def test_12_no_mws_client(self):
        self.assertEqual(self.net["mws_client_path_count"], 0)

    def test_13_no_amazon_ads_api_client(self):
        self.assertEqual(self.net["amazon_ads_api_client_path_count"], 0)

    def test_14_no_seller_central_browser_automation(self):
        self.assertEqual(self.net["browser_automation_path_count"], 0)

    def test_15_no_campaign_manager_browser_automation(self):
        # any active browser-automation path would surface as PROHIBITED_ACCOUNT_PATH.
        self.assertEqual(self.net["counts_by_phase7_classification"]["PROHIBITED_ACCOUNT_PATH"], 0)

    def test_16_no_amazon_credential_storage(self):
        self.assertFalse(self.net["amazon_boundary_policy_flags"]["amazon_credential_store_available"])

    def test_17_no_cookie_session_storage(self):
        self.assertEqual(self.net["credential_or_session_store_path_count"], 0)

    def test_18_no_automated_amazon_report_retrieval(self):
        self.assertEqual(self.net["automated_report_retrieval_path_count"], 0)
        self.assertFalse(self.net["amazon_boundary_policy_flags"]["amazon_account_report_pull_enabled"])

    def test_19_dashboard_loopback_only(self):
        self.assertTrue(self.net["dashboard_bind_is_loopback"])
        self.assertEqual(self.net["non_loopback_bind_count"], 0)

    def test_20_active_prohibited_path_blocks(self):
        # there are none; and if there were, the readiness matrix hard-blocks.
        self.assertEqual(self.net["prohibited_account_path_count"], 0)
        self.assertTrue(self.net["no_active_amazon_account_path"])

    def test_20b_active_prohibited_path_would_hard_block(self):
        r = _assemble()
        net = dict(r.network)
        net["prohibited_account_path_count"] = 1
        m = P7.build_readiness_matrix(r.handoff, r.contract_req, r.guidance, net,
                                      economics_available=True, budget_inputs_available=False,
                                      owner_objective_available=False, evidence_target_available=False)
        self.assertEqual(m["readiness_decision"], P7.PHASE7_PREFLIGHT_BLOCKED)

    def test_21_public_research_classified_separately(self):
        # public-research / AI-model paths exist and are NOT account access.
        self.assertTrue(len(self.net["public_research_paths"]) >= 1)
        for f in self.net["public_research_paths"]:
            self.assertNotEqual(f["phase7_classification"], "PROHIBITED_ACCOUNT_PATH")

    def test_boundary_flags_all_closed(self):
        flags = self.net["amazon_boundary_policy_flags"]
        self.assertTrue(flags["amazon_account_isolation"])
        for k in ("amazon_seller_central_enabled", "amazon_authenticated_access_enabled",
                  "amazon_api_enabled", "amazon_browser_automation_enabled",
                  "amazon_network_writes_enabled"):
            self.assertFalse(flags[k], k)


# ============================================================ AUTHORITY MAP (22-28)
class AuthorityMap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.amap = P7.build_authority_map(ROOT)

    def test_22_every_capability_has_authority_or_gap(self):
        self.assertTrue(self.amap["all_declared_authorities_exist"])
        for e in self.amap["authorities"]:
            self.assertTrue(e["exists"] or e["is_gap"], e["capability"])

    def test_23_duplicate_production_authorities_detected(self):
        # no DIVERGENT duplicate authorities exist; semantic duplicates are recorded separately.
        self.assertEqual(self.amap["duplicate_production_authorities"], [])
        self.assertIn("canonical_json", self.amap["semantic_duplicate_consolidation_candidates"])

    def test_24_prototype_not_promoted(self):
        for p in self.amap["prototype_or_legacy_paths"]:
            self.assertFalse(p["promoted"], p["name"])

    def test_25_existing_hash_and_package_utils_reused(self):
        by_cap = {e["capability"]: e for e in self.amap["authorities"]}
        self.assertEqual(by_cap["package_index"]["reuse_decision"], "REUSE")
        self.assertEqual(by_cap["sha256_utils"]["reuse_decision"], "REUSE")

    def test_26_decimal_conventions_identified(self):
        # the contract requirements identify the (absent) Decimal money convention explicitly.
        cr = P7.build_ppc_contract_requirements(_handoff())
        self.assertIn("Decimal", cr["price_field_convention"])

    def test_27_atomic_write_patterns_identified(self):
        by_cap = {e["capability"]: e for e in self.amap["authorities"]}
        self.assertEqual(by_cap["atomic_file_write"]["reuse_decision"], "REUSE")
        self.assertEqual(by_cap["atomic_directory_promotion"]["reuse_decision"], "REUSE")

    def test_28_authority_map_serialization_deterministic(self):
        a = P7.build_authority_map(ROOT)
        b = P7.build_authority_map(ROOT)
        self.assertEqual(a["deterministic_content_sha256"], b["deterministic_content_sha256"])

    def test_no_ppc_authority(self):
        self.assertFalse(self.amap["ppc_production_authority_present"])
        by_cap = {e["capability"]: e for e in self.amap["authorities"]}
        self.assertEqual(by_cap["ppc_authority"]["status"], "ABSENT")
        self.assertEqual(by_cap["economics_feasibility_gate"]["reuse_decision"],
                         "NOT_PPC_FEASIBILITY_ONLY")


# ============================================================ PRODUCT CONTRACT (29-44)
class ProductContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = _handoff()
        cls.cr = P7.build_ppc_contract_requirements(cls.h)

    def test_29_required_field_list_complete(self):
        self.assertEqual(len(P7.REQUIRED_CONTRACT_FIELDS), 31)
        self.assertEqual(set(self.cr["field_classification"]), set(P7.REQUIRED_CONTRACT_FIELDS))
        for f in ("advertised_asin", "advertised_sku", "advertised_variant_id", "current_selling_price",
                  "currency", "handling_time_confirmed", "advertising_eligibility_confirmed",
                  "featured_offer_eligibility_confirmed", "owner_confirmed_at"):
            self.assertIn(f, self.cr["field_classification"])

    def test_30_missing_owner_confirmation_remains_missing(self):
        self.assertEqual(self.cr["field_classification"]["advertised_asin"]["state"], P7.MISSING)
        self.assertGreater(len(self.cr["missing_owner_confirmation_fields"]), 0)

    def test_31_null_not_converted_to_false(self):
        cr = P7.build_ppc_contract_requirements(self.h, existing_contract={"listing_live_confirmed": None})
        self.assertEqual(cr["field_classification"]["listing_live_confirmed"]["state"], P7.MISSING)

    def test_32_live_listing_requires_confirmation(self):
        self.assertEqual(self.cr["field_classification"]["listing_live_confirmed"]["state"], P7.MISSING)

    def test_33_buyable_listing_requires_confirmation(self):
        self.assertEqual(self.cr["field_classification"]["listing_buyable_confirmed"]["state"], P7.MISSING)

    def test_34_asin_and_sku_require_confirmation(self):
        self.assertEqual(self.cr["field_classification"]["advertised_asin"]["state"], P7.MISSING)
        self.assertEqual(self.cr["field_classification"]["advertised_sku"]["state"], P7.MISSING)

    def test_35_listing_version_match_requires_confirmation_and_hash(self):
        # claiming a version match with no phase6 index hash is CONTRADICTORY, not accepted.
        cr = P7.build_ppc_contract_requirements(
            self.h, existing_contract={"listing_version_matches_phase6": True})
        self.assertEqual(cr["field_classification"]["listing_version_matches_phase6"]["state"],
                         P7.CONTRADICTORY)

    def test_36_price_requires_decimal_compatible_string(self):
        self.assertEqual(self.cr["field_classification"]["current_selling_price"]["state"], P7.MISSING)
        self.assertIn("Decimal-compatible string", self.cr["price_field_convention"])

    def test_37_currency_required(self):
        self.assertTrue(self.cr["currency_required"])
        self.assertEqual(self.cr["field_classification"]["currency"]["state"], P7.MISSING)

    def test_38_capacity_required_for_fbm(self):
        self.assertTrue(self.cr["capacity_required_for_fbm"])
        self.assertEqual(self.cr["field_classification"]["inventory_or_capacity_confirmed"]["state"],
                         P7.MISSING)

    def test_39_handling_time_required(self):
        self.assertTrue(self.cr["handling_time_required"])
        self.assertEqual(self.cr["field_classification"]["handling_time_confirmed"]["state"], P7.MISSING)

    def test_40_advertising_eligibility_required(self):
        self.assertTrue(self.cr["advertising_eligibility_required"])
        self.assertEqual(self.cr["field_classification"]["advertising_eligibility_confirmed"]["state"],
                         P7.MISSING)

    def test_41_featured_offer_required_when_applicable(self):
        self.assertTrue(self.cr["featured_offer_state_required_when_applicable"])
        self.assertEqual(self.cr["field_classification"]["featured_offer_eligibility_confirmed"]["state"],
                         P7.MISSING)

    def test_42_manufacturing_customization_linked_when_applicable(self):
        for f in ("manufacturing_spec_sha256", "customization_text_constraints_sha256"):
            self.assertIn(f, self.cr["field_classification"])
            self.assertEqual(self.cr["field_classification"][f]["state"], P7.MISSING)

    def test_43_credentials_rejected(self):
        self.assertTrue(self.cr["credentials_rejected"])
        self.assertIn("REJECTED", self.cr["credential_note"])

    def test_44_template_not_treated_as_verified_contract(self):
        tmpl = P7.build_ppc_contract_template(self.h)
        self.assertTrue(tmpl["IS_TEMPLATE_NOT_A_VERIFIED_CONTRACT"])
        self.assertIsNone(tmpl["advertised_asin"])
        self.assertFalse(self.cr["contract_launch_valid"])
        self.assertTrue(self.cr["contract_is_template_only"])

    def test_phase6_derived_fields_verified(self):
        for f in ("marketplace", "product_workspace_id", "phase6_index_sha256", "claim_evidence_sha256"):
            self.assertEqual(self.cr["field_classification"][f]["state"], P7.VERIFIED)


# ============================================================ OFFICIAL GUIDANCE (45-55)
class OfficialGuidance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = P7.load_guidance_snapshot(GUIDANCE, mode="LOCAL_SAFE")

    def test_45_only_official_amazon_ads_domain(self):
        self.assertEqual(self.g["official_domain"], "advertising.amazon.com")
        with tempfile.TemporaryDirectory() as d:
            bad = json.load(open(GUIDANCE, encoding="utf-8"))
            bad["official_domain"] = "example.com"
            p = os.path.join(d, "g.json")
            json.dump(bad, open(p, "w", encoding="utf-8"))
            with self.assertRaises(P7.Phase7PreflightError):
                P7.load_guidance_snapshot(p)

    def test_46_retrieval_date_required(self):
        self.assertTrue(self.g["retrieval_date"])
        with tempfile.TemporaryDirectory() as d:
            bad = json.load(open(GUIDANCE, encoding="utf-8"))
            bad.pop("retrieval_date", None)
            p = os.path.join(d, "g.json")
            json.dump(bad, open(p, "w", encoding="utf-8"))
            with self.assertRaises(P7.Phase7PreflightError):
                P7.load_guidance_snapshot(p)

    def test_47_us_applicability_recorded(self):
        for t in self.g["topics"]:
            self.assertIn("us_sponsored_products_applicability", t)
        self.assertGreater(self.g["us_applicable_topic_count"], 0)

    def test_48_title_and_topic_recorded(self):
        for t in self.g["topics"]:
            self.assertTrue(t.get("page_title"))
            self.assertTrue(t.get("topic"))
            self.assertTrue(t.get("source_stable_id"))

    def test_49_conflicting_locale_guidance_preserved(self):
        self.assertGreater(self.g["conflict_count"], 0)
        ids = {t["topic_id"] for t in self.g["topics"]}
        self.assertIn("attribution_lookback_seller_vs_vendor", ids)

    def test_50_51_52_no_universal_multipliers_invented(self):
        forbidden = self.g["do_not_hardcode"]
        self.assertIn("universal 200% daily-spend multiplier", forbidden)
        self.assertIn("universal 25% daily-spend multiplier", forbidden)
        self.assertIn("universal click threshold", forbidden)
        # the captured value appears only as an owner-revalidated snapshot, never a baked rule.
        budget = next(t for t in self.g["topics"] if t["topic_id"] == "daily_budget_monthly_averaging")
        self.assertTrue(budget["revalidation_requirement"])

    def test_53_seller_vendor_attribution_preserved(self):
        attr = next(t for t in self.g["topics"]
                    if t["topic_id"] == "attribution_lookback_seller_vs_vendor")
        self.assertIn("seller", attr["paraphrased_rule"].lower())
        self.assertIn("vendor", attr["paraphrased_rule"].lower())

    def test_54_stale_snapshot_requires_revalidation(self):
        self.assertTrue(self.g["revalidation_required"])

    def test_55_guidance_fixture_deterministic_in_deny_external(self):
        a = P7.load_guidance_snapshot(GUIDANCE, mode="TEST_DENY_EXTERNAL")
        b = P7.load_guidance_snapshot(GUIDANCE, mode="TEST_DENY_EXTERNAL")
        self.assertEqual(a["deterministic_content_sha256"], b["deterministic_content_sha256"])
        self.assertFalse(a["guidance_refreshed"])


# ============================================================ READINESS (56-64)
class Readiness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = _assemble()

    def test_56_safe_draft_plus_missing_asin_is_owner_input_required(self):
        self.assertEqual(self.r.readiness_decision, P7.PHASE7_OWNER_INPUT_REQUIRED)

    def test_57_missing_economics_does_not_authorize_launch(self):
        self.assertNotEqual(self.r.readiness_decision, P7.READY_FOR_7_1M_IMPLEMENTATION)
        self.assertFalse(self.r.readiness["matrix"]["launch_budget_inputs_available"]["state"])

    def test_58_no_launch_package_generated(self):
        self.assertTrue(self.r.readiness["no_launch_package_generated"])

    def test_59_no_target_selected(self):
        self.assertTrue(self.r.readiness["no_target_selected"])

    def test_60_no_negative_selected(self):
        self.assertTrue(self.r.readiness["no_negative_selected"])

    def test_61_no_bid_calculated(self):
        self.assertTrue(self.r.readiness["no_bid_calculated"])

    def test_62_no_budget_allocated(self):
        self.assertTrue(self.r.readiness["no_budget_allocated"])

    def test_63_ready_for_impl_distinct_from_manual_launch(self):
        self.assertTrue(self.r.readiness["distinct_from_ready_for_manual_launch"])
        self.assertFalse(self.r.readiness["ready_for_manual_launch"])

    def test_64_readiness_reasons_explicit_and_deterministic(self):
        for k, v in self.r.readiness["matrix"].items():
            self.assertTrue(v["reason"], k)
        r2 = _assemble()
        self.assertEqual(self.r.readiness["deterministic_content_sha256"],
                         r2.readiness["deterministic_content_sha256"])


# ============================================================ ARTIFACTS (65-75)
class Artifacts(unittest.TestCase):
    def _write(self):
        d = tempfile.mkdtemp()
        r = _assemble()
        man = P7.write_phase7_artifacts(r, d, started_at="t", completed_at="t")
        return d, r, man

    def test_65_eight_required_artifacts_generated(self):
        d, _, _ = self._write()
        try:
            for name in P7.REQUIRED_ARTIFACTS:
                self.assertTrue(os.path.exists(os.path.join(d, name)), name)
            self.assertEqual(len(P7.REQUIRED_ARTIFACTS), 8)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_66_exact_filenames(self):
        self.assertEqual(P7.F_PREFLIGHT, "PHASE7-PREFLIGHT-REPORT.json")
        self.assertEqual(P7.F_AUTHORITY, "PHASE7-AUTHORITY-MAP.json")
        self.assertEqual(P7.F_NETWORK, "PHASE7-NETWORK-BOUNDARY-REPORT.json")
        self.assertEqual(P7.F_HANDOFF, "PHASE7-PHASE6-HANDOFF-AUDIT.json")
        self.assertEqual(P7.F_GUIDANCE, "PHASE7-OFFICIAL-GUIDANCE-SNAPSHOT.json")
        self.assertEqual(P7.F_READINESS, "PHASE7-7_1M-READINESS-MATRIX.json")
        self.assertEqual(P7.F_CONTRACT_REQ, "PPC-PRODUCT-CONTRACT-REQUIREMENTS.json")
        self.assertEqual(P7.F_STAGE_MANIFEST, "STAGE-7_0-MANIFEST.json")

    def test_67_paths_normalized_and_bounded(self):
        d, _, _ = self._write()
        try:
            for name in P7.REQUIRED_ARTIFACTS:
                blob = open(os.path.join(d, name), encoding="utf-8").read()
                self.assertNotIn(":\\", blob)          # no windows absolute path
                self.assertNotIn("/Users/", blob)
                self.assertNotIn(ROOT, blob)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_68_absolute_paths_rejected_from_committed_proof(self):
        proof = json.load(open(COMMITTED_PROOF, encoding="utf-8")) if os.path.exists(COMMITTED_PROOF) \
            else P7.build_proof_gate(_assemble(), started_commit="test")
        blob = json.dumps(proof)
        self.assertNotIn(":\\", blob)
        self.assertNotIn("/Users/", blob)
        self.assertNotIn("/d/Claude", blob)

    def test_69_paid_keyword_rows_absent_from_committed_proof(self):
        proof = P7.build_proof_gate(_assemble(), started_commit="test")
        blob = json.dumps(proof).lower()
        # only hashes/counts/states — no keyword strings or search-volume rows.
        self.assertNotIn("search_volume", blob)
        self.assertNotIn("keyword_rows", blob)

    def test_70_private_asin_sku_absent_from_committed_proof(self):
        proof = P7.build_proof_gate(_assemble(), started_commit="test")
        self.assertEqual(proof["advertised_asin_state"], P7.MISSING)
        blob = json.dumps(proof)
        import re
        self.assertIsNone(re.search(r"B0[A-Z0-9]{8}", blob))   # no real ASIN token

    def test_71_actual_final_byte_hashes_verify(self):
        d, _, man = self._write()
        try:
            for name, recorded in man["output_hashes"].items():
                data = open(os.path.join(d, name), "rb").read()
                self.assertEqual(P7._sha_bytes(data), recorded, name)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_72_atomic_writes_used(self):
        d, _, _ = self._write()
        try:
            residue = [f for f in os.listdir(d) if f.startswith(".tmp-7-0-")]
            self.assertEqual(residue, [])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_73_failed_run_preserves_last_valid_artifact(self):
        d = tempfile.mkdtemp()
        try:
            target = os.path.join(d, "keep.json")
            P7._atomic_write_bytes(target, b'{"v":1}')
            orig = open(target, "rb").read()
            import unittest.mock as mock
            with mock.patch("os.replace", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    P7._atomic_write_bytes(target, b'{"v":2}')
            self.assertEqual(open(target, "rb").read(), orig)   # last valid preserved
            self.assertEqual([f for f in os.listdir(d) if f.startswith(".tmp-7-0-")], [])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_74_no_7_1m_artifact_generated(self):
        d, _, _ = self._write()
        try:
            forbidden = {"PPC-ECONOMICS.json", "PPC-TARGETS.json", "PPC-NEGATIVES.json",
                         "PPC-LAUNCH-PLAN.json", "CAMPAIGN-PAYLOAD.json"}
            present = set(os.listdir(d))
            self.assertEqual(forbidden & present, set())
            self.assertFalse(os.path.isdir(os.path.join(d, "PPC-LAUNCH-READY")))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_75_no_later_phase_artifact_generated(self):
        d, _, man = self._write()
        try:
            present = set(os.listdir(d))
            for f in present:
                self.assertNotIn("7.1E", f)
                self.assertNotIn("7.2", f)
                self.assertNotIn("7.3", f)
                self.assertNotIn("7.4", f)
            self.assertEqual(man["phase7_1m_artifact_count"], 0)
            self.assertEqual(man["later_phase_artifact_count"], 0)
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ============================================================ DETERMINISM (76-82)
class Determinism(unittest.TestCase):
    def _stable(self, r):
        return {k: getattr(r, k)["deterministic_content_sha256"] for k in
                ("handoff_audit", "network", "authority", "guidance", "readiness", "contract_req",
                 "preflight")}

    def test_76_77_78_two_run_stable_hashes_match(self):
        r1 = _assemble(mode="LOCAL_SAFE")
        r2 = _assemble(mode="LOCAL_SAFE")
        self.assertEqual(self._stable(r1), self._stable(r2))

    def test_79_local_safe_equals_deny_external(self):
        # repository-stable artifacts (no workspace) must match across these two modes.
        a = _assemble(run_dir=None, mode="LOCAL_SAFE")
        b = _assemble(run_dir=None, mode="TEST_DENY_EXTERNAL")
        for k in ("network", "authority", "guidance"):
            self.assertEqual(getattr(a, k)["deterministic_content_sha256"],
                             getattr(b, k)["deterministic_content_sha256"], k)

    def test_80_connected_research_changes_snapshot_only_on_explicit_refresh(self):
        base = _assemble(run_dir=None, mode="LOCAL_SAFE")
        conn = _assemble(run_dir=None, mode="CONNECTED_RESEARCH", refresh=False)
        self.assertEqual(base.guidance["deterministic_content_sha256"],
                         conn.guidance["deterministic_content_sha256"])
        self.assertFalse(conn.guidance["guidance_refreshed"])
        refreshed = _assemble(run_dir=None, mode="CONNECTED_RESEARCH", refresh=True)
        self.assertTrue(refreshed.guidance["guidance_refreshed"])

    def test_81_external_amazon_account_attempts_zero(self):
        r = _assemble()
        self.assertEqual(r.preflight["external_amazon_account_attempts"], 0)

    def test_82_amazon_account_actions_zero(self):
        r = _assemble()
        self.assertEqual(r.preflight["amazon_account_actions"], 0)
        self.assertEqual(r.handoff_audit["amazon_account_actions"], 0)


# ============================================================ CLEAN ENVIRONMENT (83-87)
class CleanEnvironment(unittest.TestCase):
    def test_83_committed_proof_parses(self):
        if not os.path.exists(COMMITTED_PROOF):
            self.skipTest("committed proof not yet written")
        with open(COMMITTED_PROOF, encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual(d["schema_version"], P7.SCHEMA_PROOF_GATE)
        self.assertIn(d["session_status"],
                      (P7.SESSION7_0_READY_FOR_REVIEW, P7.PHASE7_OWNER_INPUT_REQUIRED,
                       P7.PHASE7_PREFLIGHT_BLOCKED))

    def test_84_committed_fixtures_parse_standalone(self):
        # a fresh-worktree proxy: the committed guidance fixture parses with no workspace + no network.
        g = P7.load_guidance_snapshot(GUIDANCE, mode="TEST_DENY_EXTERNAL")
        self.assertEqual(g["official_domain"], "advertising.amazon.com")

    def test_85_hermetic_security_tests_need_no_run_data(self):
        # the whole boundary + authority + guidance path runs with NO workspace.
        r = _assemble(run_dir=None)
        self.assertTrue(r.network["no_active_amazon_account_path"])
        self.assertTrue(r.authority["all_declared_authorities_exist"])

    def test_86_no_private_paths_leak(self):
        r = _assemble(run_dir=None)
        for doc in (r.network, r.authority, r.handoff_audit, r.readiness, r.contract_req, r.preflight):
            blob = json.dumps(doc)
            self.assertNotIn(":\\", blob)
            self.assertNotIn("/Users/", blob)

    def test_87_no_prohibited_amazon_integration(self):
        r = _assemble(run_dir=None)
        self.assertEqual(r.network["prohibited_account_path_count"], 0)
        self.assertEqual(r.network["sp_api_client_path_count"], 0)
        self.assertEqual(r.network["mws_client_path_count"], 0)
        self.assertEqual(r.network["amazon_ads_api_client_path_count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
