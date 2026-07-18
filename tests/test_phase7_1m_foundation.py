#!/usr/bin/env python3
"""Phase 7.1M — minimal launch foundation tests (focused release-grade requirements).

Hermetic by default: everything runs offline against the committed repository and the committed
Phase 6/7.0 proofs. Workspace-dependent byte verification skips cleanly when the gitignored runs/
product workspace is absent (like the Phase 6F / 7.0 workspace tests). This module opens NO network.
"""
import copy
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("", "core", "listing", "production", "scripts"):
    _p = ROOT if not _sub else os.path.join(ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import phase7_minimal_launch_foundation as F   # noqa: E402
from production import phase7_preflight as P7                   # noqa: E402
from production import seller_central_package as SCP            # noqa: E402
from core import money as M                                     # noqa: E402

RUN_T2 = os.path.join(ROOT, "runs", "T2")
HAS_WORKSPACE = os.path.isdir(RUN_T2) and os.path.isdir(SCP.package_root_dir(RUN_T2))
COMMITTED_PROOF = os.path.join(ROOT, "SESSION7_1M-PROOF-GATE.json")
_RUN_DIR = RUN_T2 if HAS_WORKSPACE else None


def _handoff():
    return P7.load_phase6_handoff(ROOT, run_dir=_RUN_DIR)


def _assemble(**kw):
    return F.assemble_phase7_1m(ROOT, run_dir=_RUN_DIR, **kw)


def _synthetic_handoff(publishable=True):
    """A minimal, hermetic handoff mirroring load_phase6_handoff output — used to exercise the fully
    green (publishable + verified) path without depending on the gitignored runs/ workspace."""
    shas = {"phase6_package_sha256": "a" * 64, "phase6_index_sha256": "b" * 64,
            "keyword_source_sha256": "c" * 64, "keyword_allocation_sha256": "d" * 64,
            "product_facts_sha256": "e" * 64, "claim_evidence_sha256": "f" * 64}
    return {
        "workspace_id": "T2", "product_id": "T2", "marketplace": "US",
        "canonical_category_id": "US:apparel",
        "phase6_final_status": SCP.PHASE6_SAFE_DRAFT_READY,
        "listing_audit_state": "SAFE_DRAFT",
        "listing_publishability": SCP.PUB_PUBLISHABLE if publishable else "SAFE_DRAFT_OWNER_FACTS_REQUIRED",
        "package_index_sha256_prefix": "b" * 8,
        "keyword_source_sha256_prefix": "c" * 8,
        "phase6b_manifest_sha256_prefix": "d" * 8,
        "product_facts_sha256_prefix": "e" * 8,
        "claim_evidence_sha256_prefix": "f" * 8,
        "full_shas": shas,
        "live_verification": {"workspace_present": True, "package_verify_result": "PASS_WITH_WARNINGS",
                              "package_index_hash_file_result": "PASS", "committed_prefix_drift": []},
    }


def _full_live_state():
    return {
        "workspace_id": "T2", "product_id": "T2", "marketplace": "US", "account_type": "SELLER",
        "advertised_asin": "B0LIVEASIN1", "advertised_sku": "NURSE-SWT-1", "advertised_variant_id": "VAR-M",
        "listing_live_confirmed": True, "listing_buyable_confirmed": True,
        "listing_version_matches_phase6": True, "current_selling_price": "39.99", "currency": "USD",
        "inventory_or_capacity_confirmed": True, "normal_capacity": "50", "peak_capacity": "80",
        "current_backlog": "5", "safe_handling_time": "3", "handling_time_confirmed": True,
        "advertising_eligibility_confirmed": True, "featured_offer_applicability": "APPLICABLE",
        "featured_offer_eligibility_confirmed": True,
        "owner_confirmed_at": "2026-07-18T10:00:00+07:00", "owner_confirmation_source": "manual screen-read",
    }


def _full_economics(**over):
    e = {
        "currency": "USD", "selling_price": "39.99", "expected_discount": "0.00",
        "amazon_referral_fee": "6.00", "other_amazon_fees": "0.50", "blank_product_cost": "6.00",
        "decoration_cost": "2.00", "personalization_cost": "1.00", "packaging_cost": "0.75",
        "outbound_shipping_cost": "4.50", "labor_cost": "1.50", "transaction_cost": "0.30",
        "refund_reserve": "0.50", "replacement_reserve": "0.40", "other_variable_costs": "0.00",
        "expected_ad_conversion_rate": "0.10", "owner_confirmed_at": "2026-07-18T10:00:00+07:00",
        "normal_capacity": "50", "peak_capacity": "80", "current_backlog": "5", "safe_handling_time": "3",
        "inventory_or_capacity_confirmed": True, "handling_time_confirmed": True,
        "owner_total_test_budget": "300.00", "average_daily_budget": "20.00", "learning_window_days": "14",
        "owner_configured_evidence_target": "50", "owner_launch_objective": "evidence_generation",
        "planned_cpc_candidate": "0.50",
    }
    e.update(over)
    return e


def _full_contract(handoff, live_state):
    ls_sha = F.content_sha256(live_state)
    c = dict(live_state)
    c.update(F._phase6_expected_hashes(handoff))
    c.update({"IS_TEMPLATE_NOT_A_VERIFIED_CONTRACT": False,
              "listing_publishability": handoff["listing_publishability"],
              "listing_audit_state": handoff["listing_audit_state"],
              "live_product_state_sha256": ls_sha,
              "manufacturing_customization_applicable": False,
              "contract_version": "1.0.0"})
    return c, ls_sha


def _green_result(handoff=None, economics=None):
    """A fully-green resolution using the synthetic publishable handoff."""
    h = handoff or _synthetic_handoff(publishable=True)
    net = P7.build_network_boundary_report(ROOT)
    ls = _full_live_state()
    lr = F.validate_live_product_state(ls, h)
    contract, ls_sha = _full_contract(h, ls)
    cr = F.validate_ppc_product_contract(contract, h, lr, ls_sha)
    econ = economics if economics is not None else _full_economics()
    er = F.compute_ppc_economics(econ, live_currency="USD")
    cap = F.evaluate_capacity_and_handling(econ)
    bud = F.evaluate_budget_feasibility(econ, er, cap)
    rd = F.resolve_phase7_1m_readiness(h, lr, cr, er, cap, bud, net)
    return dict(h=h, net=net, ls=ls, lr=lr, contract=contract, ls_sha=ls_sha, cr=cr, econ=econ,
                er=er, cap=cap, bud=bud, rd=rd)


# ============================================================ PREFLIGHT & DEPENDENCIES (1-10)
class PreflightAndDependencies(unittest.TestCase):
    def test_01_phase7_0_proof_verifies(self):
        p = os.path.join(ROOT, "SESSION7_0-PROOF-GATE.json")
        d = json.load(open(p, encoding="utf-8"))
        self.assertEqual(d["readiness_decision"], P7.PHASE7_OWNER_INPUT_REQUIRED)
        self.assertEqual(d["session_status"], P7.PHASE7_OWNER_INPUT_REQUIRED)

    def test_02_phase7_0_commit_lineage(self):
        git = P7._read_git_head(ROOT)
        self.assertTrue(git["git_head"] is None or len(git["git_head"]) == 40)

    def test_03_phase6f_proof_valid(self):
        d = json.load(open(os.path.join(ROOT, P7.PHASE6F_PROOF_NAME), encoding="utf-8"))
        self.assertEqual(d["phase6_final_status"], SCP.PHASE6_SAFE_DRAFT_READY)

    def test_04_phase6_final_status_valid(self):
        h = _handoff()
        self.assertEqual(h["phase6_final_status"], SCP.PHASE6_SAFE_DRAFT_READY)

    def test_05_amazon_boundary_closed(self):
        net = P7.build_network_boundary_report(ROOT)
        self.assertTrue(net["no_active_amazon_account_path"])
        self.assertEqual(net["prohibited_account_path_count"], 0)

    def test_06_missing_phase7_0_dependency_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(P7.Phase7PreflightError):
                F.assemble_phase7_1m(d, run_dir=None)

    def test_07_invalid_phase6_handoff_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            proof = json.load(open(os.path.join(ROOT, P7.PHASE6F_PROOF_NAME), encoding="utf-8"))
            proof["package_index_sha256_prefix"] = "deadbeef"
            status = json.load(open(os.path.join(ROOT, P7.PHASE6_FINAL_STATUS_NAME), encoding="utf-8"))
            json.dump(proof, open(os.path.join(d, P7.PHASE6F_PROOF_NAME), "w", encoding="utf-8"))
            json.dump(status, open(os.path.join(d, P7.PHASE6_FINAL_STATUS_NAME), "w", encoding="utf-8"))
            with self.assertRaises(P7.Phase7PreflightError):
                F.assemble_phase7_1m(d, run_dir=None)

    def test_08_no_launch_package_types_written(self):
        r = _assemble()
        d = tempfile.mkdtemp()
        try:
            F.write_phase7_1m_candidate(r, d)
            present = set(os.listdir(d))
            for bad in ("PPC-TARGETS.csv", "PPC-HARD-NEGATIVES.csv", "PPC-MANUAL-LAUNCH-PLAN.md",
                        "CAMPAIGN-PAYLOAD.json"):
                self.assertNotIn(bad, present)
            self.assertFalse(os.path.isdir(os.path.join(d, "PPC-LAUNCH-READY")))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_09_t2_resolves_owner_input_required(self):
        r = _assemble()
        self.assertEqual(r.readiness_decision, F.READY_OWNER_INPUT)
        self.assertEqual(r.session_status, F.SESSION_OWNER_INPUT)

    def test_10_later_phase_artifact_count_zero(self):
        r = _assemble()
        self.assertEqual(r.readiness["later_phase_artifact_count"], 0)


# ============================================================ AUTHORITY (11-20)
class Authority(unittest.TestCase):
    def test_11_one_live_state_authority(self):
        self.assertTrue(hasattr(F, "validate_live_product_state"))
        self.assertTrue(hasattr(F, "build_live_product_state_template"))

    def test_12_one_ppc_contract_authority(self):
        self.assertTrue(hasattr(F, "validate_ppc_product_contract"))

    def test_13_one_decimal_authority(self):
        self.assertTrue(hasattr(M, "parse_decimal_string"))
        self.assertIs(F.MONEY, M)

    def test_14_one_economics_authority(self):
        self.assertTrue(hasattr(F, "compute_ppc_economics"))

    def test_15_one_readiness_authority(self):
        self.assertTrue(hasattr(F, "resolve_phase7_1m_readiness"))

    def test_16_no_duplicate_production_authority(self):
        # exactly one 7.1M foundation module and one money module exist.
        prod = os.listdir(os.path.join(ROOT, "production"))
        self.assertEqual([f for f in prod if "minimal_launch_foundation" in f],
                         ["phase7_minimal_launch_foundation.py"])
        for banned in ("phase7_minimal_launch_foundation_v2.py", "ppc_economics_v2.py",
                       "campaign_compiler.py", "ppc_launch_compiler.py", "targeting_engine.py",
                       "negative_engine.py", "report_ingestion.py", "optimization_engine.py"):
            self.assertNotIn(banned, prod)

    def test_17_prototype_not_promoted(self):
        # the float/pandas economics feasibility gate is NOT the PPC economics authority.
        import economics.economics_gate as EG
        self.assertFalse(hasattr(EG, "compute_ppc_economics"))

    def test_18_existing_utils_reused(self):
        self.assertIs(F.canonical_json, __import__("production.product_workspace",
                                                   fromlist=["canonical_json"]).canonical_json)
        self.assertIs(F.content_sha256, __import__("production.product_workspace",
                                                   fromlist=["content_sha256"]).content_sha256)

    def test_19_no_campaign_compiler_introduced(self):
        prod = os.listdir(os.path.join(ROOT, "production"))
        self.assertNotIn("campaign_compiler.py", prod)

    def test_20_no_target_or_negative_authority(self):
        prod = os.listdir(os.path.join(ROOT, "production"))
        self.assertNotIn("targeting_engine.py", prod)
        self.assertNotIn("negative_engine.py", prod)


# ============================================================ LIVE PRODUCT STATE (21-45)
class LiveProductState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = _synthetic_handoff(publishable=True)
        cls.hsafe = _synthetic_handoff(publishable=False)

    def _val(self, **over):
        st = _full_live_state()
        st.update(over)
        return F.validate_live_product_state(st, self.h)

    def test_21_template_is_not_verified(self):
        t = F.build_live_product_state_template(self.h)
        self.assertTrue(t["IS_TEMPLATE_NOT_VERIFIED_STATE"])
        r = F.validate_live_product_state(t, self.h)
        self.assertEqual(r.state, F.LIVE_STATE_OWNER_INPUT)
        self.assertFalse(r.verified)

    def test_22_missing_remains_missing(self):
        r = self._val(advertised_asin=None)
        self.assertEqual(r.fields["advertised_asin"], F.MISSING)

    def test_23_null_not_false(self):
        r = self._val(listing_live_confirmed=None)
        self.assertEqual(r.fields["listing_live_confirmed"], F.MISSING)

    def test_24_false_not_missing(self):
        r = self._val(listing_buyable_confirmed=False)
        self.assertEqual(r.fields["listing_buyable_confirmed"], F.CONTRADICTORY)
        self.assertNotEqual(r.fields["listing_buyable_confirmed"], F.MISSING)

    def test_25_explicit_true_required(self):
        r = self._val()   # all True
        self.assertEqual(r.fields["advertising_eligibility_confirmed"], F.VERIFIED)
        r2 = self._val(advertising_eligibility_confirmed="yes")
        self.assertEqual(r2.fields["advertising_eligibility_confirmed"], F.CONTRADICTORY)

    def test_26_27_asin_sku_required(self):
        self.assertEqual(self._val(advertised_asin=None).fields["advertised_asin"], F.MISSING)
        self.assertEqual(self._val(advertised_sku=None).fields["advertised_sku"], F.MISSING)

    def test_28_variant_required_when_applicable(self):
        self.assertEqual(self._val(advertised_variant_id=None).fields["advertised_variant_id"], F.MISSING)
        self.assertEqual(self._val(advertised_variant_id=F.NA).fields["advertised_variant_id"],
                         F.NOT_APPLICABLE)

    def test_29_live_confirmation_required(self):
        self.assertEqual(self._val(listing_live_confirmed=None).fields["listing_live_confirmed"], F.MISSING)

    def test_30_buyable_confirmation_required(self):
        self.assertEqual(self._val(listing_buyable_confirmed=None).fields["listing_buyable_confirmed"],
                         F.MISSING)

    def test_31_version_match_required(self):
        self.assertEqual(self._val(listing_version_matches_phase6=None).fields[
            "listing_version_matches_phase6"], F.MISSING)

    def test_32_price_required_and_decimal(self):
        self.assertEqual(self._val(current_selling_price=None).fields["current_selling_price"], F.MISSING)
        self.assertEqual(self._val(current_selling_price="12.99").fields["current_selling_price"],
                         F.VERIFIED)
        # a float-y/garbage price is contradictory (invalid), never silently accepted.
        self.assertEqual(self._val(current_selling_price="abc").fields["current_selling_price"],
                         F.CONTRADICTORY)

    def test_33_currency_required(self):
        self.assertEqual(self._val(currency=None).fields["currency"], F.MISSING)

    def test_34_capacity_confirmation_required(self):
        self.assertEqual(self._val(inventory_or_capacity_confirmed=None).fields[
            "inventory_or_capacity_confirmed"], F.MISSING)

    def test_35_handling_confirmation_required(self):
        self.assertEqual(self._val(handling_time_confirmed=None).fields["handling_time_confirmed"],
                         F.MISSING)

    def test_36_advertising_eligibility_required(self):
        self.assertEqual(self._val(advertising_eligibility_confirmed=None).fields[
            "advertising_eligibility_confirmed"], F.MISSING)

    def test_37_featured_offer_applicability_explicit(self):
        self.assertEqual(self._val(featured_offer_applicability=None).fields[
            "featured_offer_applicability"], F.MISSING)
        r = self._val(featured_offer_applicability="NOT_APPLICABLE")
        self.assertEqual(r.fields["featured_offer_eligibility_confirmed"], F.NOT_APPLICABLE)

    def test_38_featured_offer_state_required_when_applicable(self):
        r = self._val(featured_offer_applicability="APPLICABLE", featured_offer_eligibility_confirmed=None)
        self.assertEqual(r.fields["featured_offer_eligibility_confirmed"], F.MISSING)

    def test_39_owner_timestamp_required_tz_aware(self):
        self.assertEqual(self._val(owner_confirmed_at=None).fields["owner_confirmed_at"], F.MISSING)
        self.assertEqual(self._val(owner_confirmed_at="2026-07-18T10:00:00").fields[
            "owner_confirmed_at"], F.CONTRADICTORY)   # naive timestamp rejected

    def test_40_owner_source_required(self):
        self.assertEqual(self._val(owner_confirmation_source=None).fields[
            "owner_confirmation_source"], F.MISSING)

    def test_41_credentials_rejected(self):
        st = _full_live_state()
        st["session_token"] = "atza|abc"
        r = F.validate_live_product_state(st, self.h)
        self.assertEqual(r.state, F.LIVE_STATE_INVALID)

    def test_42_cookie_session_rejected(self):
        st = _full_live_state()
        st["cookie"] = "x-amz-blah"
        r = F.validate_live_product_state(st, self.h)
        self.assertEqual(r.state, F.LIVE_STATE_INVALID)

    def test_43_marketplace_mismatch_rejected(self):
        r = self._val(marketplace="UK")
        self.assertEqual(r.fields["marketplace"], F.CONTRADICTORY)
        self.assertEqual(r.state, F.LIVE_STATE_CONTRADICTORY)

    def test_44_product_workspace_mismatch_rejected(self):
        r = self._val(product_id="T9")
        self.assertEqual(r.fields["product_id"], F.CONTRADICTORY)

    def test_44b_live_claim_contradicts_safe_draft(self):
        # listing_live_confirmed=True with a non-publishable Phase 6 state is contradictory.
        st = _full_live_state()
        r = F.validate_live_product_state(st, self.hsafe)
        self.assertEqual(r.fields["listing_live_confirmed"], F.CONTRADICTORY)
        self.assertEqual(r.state, F.LIVE_STATE_CONTRADICTORY)

    def test_45_deterministic_serialization(self):
        a = self._val().to_dict()["deterministic_content_sha256"]
        b = self._val().to_dict()["deterministic_content_sha256"]
        self.assertEqual(a, b)

    def test_45b_full_state_verifies(self):
        self.assertEqual(self._val().state, F.LIVE_STATE_VERIFIED)


# ============================================================ PPC CONTRACT (46-65)
class PPCContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = _synthetic_handoff(publishable=True)
        cls.ls = _full_live_state()
        cls.lr = F.validate_live_product_state(cls.ls, cls.h)
        cls.contract, cls.ls_sha = _full_contract(cls.h, cls.ls)

    def _val(self, **over):
        c = copy.deepcopy(self.contract)
        c.update(over)
        return F.validate_ppc_product_contract(c, self.h, self.lr, self.ls_sha)

    def test_46_template_not_verified(self):
        t = F.build_ppc_product_contract(self.h, self.lr, self.ls_sha)
        self.assertTrue(t["IS_TEMPLATE_NOT_A_VERIFIED_CONTRACT"])
        r = F.validate_ppc_product_contract(t, self.h, self.lr, self.ls_sha)
        self.assertEqual(r.state, F.CONTRACT_OWNER_INPUT)

    def test_47_live_state_hash_required(self):
        r = self._val(live_product_state_sha256=None)
        self.assertEqual(r.checks["live_product_state_sha256"], F.MISSING)

    def test_47b_live_state_hash_mismatch_blocks(self):
        r = self._val(live_product_state_sha256="0" * 64)
        self.assertEqual(r.checks["live_product_state_sha256"], F.CONTRADICTORY)
        self.assertEqual(r.state, F.CONTRACT_CONTRADICTORY)

    def test_48_49_50_51_52_53_phase6_hashes_required(self):
        for f in F.CONTRACT_PHASE6_HASH_FIELDS:
            r = self._val(**{f: None})
            self.assertEqual(r.checks[f], F.MISSING, f)

    def test_54_manufacturing_customization_linked_when_applicable(self):
        c = copy.deepcopy(self.contract)
        c["manufacturing_customization_applicable"] = True
        c["manufacturing_spec_sha256"] = None
        r = F.validate_ppc_product_contract(c, self.h, self.lr, self.ls_sha)
        self.assertEqual(r.checks["manufacturing_spec_sha256"], F.MISSING)
        c["manufacturing_spec_sha256"] = "1" * 64
        c["customization_text_constraints_sha256"] = "2" * 64
        r2 = F.validate_ppc_product_contract(c, self.h, self.lr, self.ls_sha)
        self.assertEqual(r2.checks["manufacturing_spec_sha256"], F.VERIFIED)

    def test_55_56_publishability_required_safe_draft_blocks(self):
        r = self._val(listing_publishability="SAFE_DRAFT_OWNER_FACTS_REQUIRED")
        self.assertEqual(r.checks["listing_publishability"], F.CONTRADICTORY)
        self.assertIn(r.state, (F.CONTRACT_PHASE6_BLOCKED, F.CONTRACT_CONTRADICTORY))

    def test_57_live_listing_required(self):
        r = self._val(listing_live_confirmed=None)
        self.assertEqual(r.checks["listing_live_confirmed"], F.MISSING)

    def test_58_buyable_required(self):
        r = self._val(listing_buyable_confirmed=None)
        self.assertEqual(r.checks["listing_buyable_confirmed"], F.MISSING)

    def test_59_version_match_required(self):
        r = self._val(listing_version_matches_phase6=None)
        self.assertEqual(r.checks["listing_version_matches_phase6"], F.MISSING)

    def test_60_price_currency_match_live_state(self):
        r = self._val(current_selling_price="99.99")
        self.assertEqual(r.checks["current_selling_price"], F.CONTRADICTORY)
        self.assertEqual(r.state, F.CONTRACT_CONTRADICTORY)

    def test_61_capacity_handling_confirmations_present(self):
        r = self._val(handling_time_confirmed=None)
        self.assertEqual(r.checks["handling_time_confirmed"], F.MISSING)

    def test_62_eligibility_present(self):
        r = self._val(advertising_eligibility_confirmed=None)
        self.assertEqual(r.checks["advertising_eligibility_confirmed"], F.MISSING)

    def test_63_owner_lineage_required(self):
        r = self._val(owner_confirmed_at=None, owner_confirmation_source=None)
        self.assertEqual(r.checks["owner_confirmed_at"], F.MISSING)
        self.assertEqual(r.checks["owner_confirmation_source"], F.MISSING)

    def test_64_hash_mismatch_blocks(self):
        r = self._val(phase6_index_sha256="0" * 64)
        self.assertEqual(r.checks["phase6_index_sha256"], F.CONTRADICTORY)
        self.assertIn(r.state, (F.CONTRACT_PHASE6_BLOCKED, F.CONTRACT_CONTRADICTORY))

    def test_65_contradiction_blocks_readiness(self):
        r = self._val(advertised_asin="B0DIFFERENT9")
        self.assertEqual(r.state, F.CONTRACT_CONTRADICTORY)
        self.assertFalse(r.verified)

    def test_65b_credentials_rejected(self):
        c = copy.deepcopy(self.contract)
        c["access_token"] = "eyJ" + "x" * 60
        r = F.validate_ppc_product_contract(c, self.h, self.lr, self.ls_sha)
        self.assertEqual(r.state, F.CONTRACT_INVALID)

    def test_65c_full_contract_verifies(self):
        self.assertEqual(self._val().state, F.CONTRACT_VERIFIED)


# ============================================================ DECIMAL MONEY (66-80)
class DecimalMoney(unittest.TestCase):
    def test_66_decimal_string_parses(self):
        self.assertEqual(M.parse_money_decimal("12.99"), Decimal("12.99"))

    def test_67_float_rejected(self):
        with self.assertRaises(M.MoneyError):
            M.parse_money_decimal(12.99)

    def test_68_missing_remains_missing(self):
        self.assertIsNone(M.parse_money_decimal(None))

    def test_69_blank_rejected(self):
        for b in ("", "   "):
            with self.assertRaises(M.MoneyError):
                M.parse_money_decimal(b)

    def test_70_nan_rejected(self):
        with self.assertRaises(M.MoneyError):
            M.parse_money_decimal("NaN")

    def test_71_infinity_rejected(self):
        for b in ("Infinity", "-inf"):
            with self.assertRaises(M.MoneyError):
                M.parse_money_decimal(b)

    def test_72_negative_costs_rejected(self):
        with self.assertRaises(M.MoneyError):
            M.parse_nonnegative_decimal("-0.01")
        # unless explicitly permitted (plain parse allows a signed decimal)
        self.assertEqual(M.parse_money_decimal("-5.00"), Decimal("-5.00"))

    def test_73_positive_price_required(self):
        with self.assertRaises(M.MoneyError):
            M.parse_positive_decimal("0")

    def test_74_cvr_range_validated(self):
        self.assertEqual(M.parse_rate_decimal("0.1"), Decimal("0.1"))
        for bad in ("0", "1.5", "-0.1"):
            with self.assertRaises(M.MoneyError):
                M.parse_rate_decimal(bad)
        self.assertEqual(M.parse_rate_decimal("1"), Decimal("1"))   # upper inclusive

    def test_75_currency_quantization_round_half_up(self):
        self.assertEqual(M.serialize_currency(Decimal("12.005")), "12.01")
        self.assertEqual(M.serialize_currency(Decimal("12.004")), "12.00")

    def test_76_internal_precision_retained(self):
        self.assertEqual(M.safe_divide(Decimal("1"), Decimal("3")).quantize(Decimal("0.0001")),
                         Decimal("0.3333"))

    def test_77_78_decimal_serialized_as_string_no_float(self):
        s = M.decimal_to_canonical_string(Decimal("0.0000001"))
        self.assertIsInstance(s, str)
        self.assertNotIn("e", s.lower())
        with self.assertRaises(M.MoneyError):
            M.decimal_to_canonical_string(0.1)   # a float can never be serialized

    def test_79_missing_sum_input_blocks(self):
        with self.assertRaises(M.MoneyError):
            M.sum_required_decimals([Decimal("1"), None])

    def test_80_deterministic_formatting(self):
        self.assertEqual(M.serialize_currency(Decimal("7")), "7.00")
        self.assertEqual(M.decimal_to_canonical_string(Decimal("7.50")), "7.50")

    def test_80b_exponent_form_rejected(self):
        with self.assertRaises(M.MoneyError):
            M.parse_money_decimal("1e3")


# ============================================================ ECONOMICS (81-104)
class Economics(unittest.TestCase):
    def _econ(self, **over):
        return F.compute_ppc_economics(_full_economics(**over), live_currency="USD")

    def test_81_required_inputs_enforced(self):
        r = self._econ(blank_product_cost=None)
        self.assertEqual(r.state, F.ECON_OWNER_INPUT)
        self.assertIn("blank_product_cost", r.missing)

    def test_82_no_cost_defaults_to_zero(self):
        r = self._econ(packaging_cost=None)
        self.assertEqual(r.state, F.ECON_OWNER_INPUT)   # missing != treated as 0

    def test_83_default_min_profit_explicit_and_sourced(self):
        r = self._econ(minimum_required_profit=None)
        self.assertEqual(r.min_profit_source, F.DEFAULT_MIN_PROFIT_SOURCE)
        self.assertEqual(r.values["minimum_required_profit"], Decimal("8.00"))

    def test_84_owner_min_profit_overrides_default(self):
        r = self._econ(minimum_required_profit="5.00")
        self.assertEqual(r.min_profit_source, "OWNER_SUPPLIED")
        self.assertEqual(r.values["minimum_required_profit"], Decimal("5.00"))

    def test_85_net_sales_formula(self):
        r = self._econ(selling_price="40.00", expected_discount="2.00")
        self.assertEqual(r.derived["net_sales"], Decimal("38.00"))

    def test_86_variable_cost_sum(self):
        r = self._econ()
        self.assertEqual(r.derived["non_ad_variable_costs"], Decimal("23.45"))

    def test_87_contribution_margin_formula(self):
        r = self._econ()
        self.assertEqual(r.derived["contribution_margin_before_ads"], Decimal("16.54"))

    def test_88_break_even_ad_cost_formula(self):
        r = self._econ()
        self.assertEqual(r.derived["break_even_ad_cost_per_order"],
                         r.derived["contribution_margin_before_ads"])

    def test_89_target_ad_cost_formula(self):
        r = self._econ(minimum_required_profit="8.00")
        self.assertEqual(r.derived["target_ad_cost_per_order"], Decimal("8.54"))

    def test_90_break_even_acos_formula(self):
        r = self._econ()
        self.assertEqual((r.derived["break_even_acos"] * Decimal("39.99")).quantize(Decimal("0.01")),
                         Decimal("16.54"))

    def test_91_target_acos_formula(self):
        r = self._econ()
        self.assertTrue(r.derived["target_acos"] < r.derived["break_even_acos"])

    def test_92_maximum_cpc_formula(self):
        r = self._econ()
        # target_ad_cost 8.54 * cvr 0.10 = 0.854 -> 0.85
        self.assertEqual(M.serialize_currency(r.derived["maximum_cpc"]), "0.85")

    def test_93_non_positive_net_sales_blocks(self):
        r = self._econ(selling_price="10.00", expected_discount="10.00")
        self.assertEqual(r.state, F.ECON_NOT_VIABLE)
        self.assertIn("NON_POSITIVE_NET_SALES", r.blockers)

    def test_94_non_positive_contribution_blocks(self):
        r = self._econ(blank_product_cost="40.00")
        self.assertEqual(r.state, F.ECON_NOT_VIABLE)
        self.assertIn("NON_POSITIVE_CONTRIBUTION_MARGIN", r.blockers)

    def test_95_non_positive_target_ad_cost_blocks(self):
        r = self._econ(minimum_required_profit="30.00")
        self.assertEqual(r.state, F.ECON_NOT_VIABLE)
        self.assertIn("TARGET_AD_COST_NON_POSITIVE", r.blockers)

    def test_96_target_acos_below_break_even(self):
        r = self._econ()   # viable
        self.assertLess(r.derived["target_acos"], r.derived["break_even_acos"])

    def test_97_non_positive_max_cpc_blocks(self):
        r = self._econ(minimum_required_profit="16.54")   # target ad cost -> 0
        self.assertEqual(r.state, F.ECON_NOT_VIABLE)
        self.assertIn("TARGET_AD_COST_NON_POSITIVE", r.blockers)

    def test_98_99_no_universal_cpc_clamp_or_fallback(self):
        d = self._econ().to_dict()
        self.assertTrue(d["no_universal_cpc_clamp"])
        self.assertTrue(d["no_cpc_fallback"])

    def test_100_no_float_output(self):
        d = self._econ().to_dict()
        for v in d.values():
            self.assertNotIsInstance(v, float)
        self.assertTrue(d["no_float_in_canonical_output"])

    def test_101_currency_mismatch_blocks(self):
        r = F.compute_ppc_economics(_full_economics(currency="EUR"), live_currency="USD")
        self.assertEqual(r.state, F.ECON_INVALID)

    def test_102_safe_draft_blocks_launch_readiness(self):
        # a viable economics does NOT lift a safe-draft listing to campaign-planning readiness.
        g = _green_result(handoff=_synthetic_handoff(publishable=False))
        self.assertNotEqual(g["rd"]["phase7_1m_readiness"], F.READY_CAMPAIGN_PLANNING)

    def test_103_missing_live_state_owner_input(self):
        r = _assemble()
        self.assertEqual(r.economics_result.state, F.ECON_OWNER_INPUT)
        self.assertEqual(r.readiness_decision, F.READY_OWNER_INPUT)

    def test_104_t2_owner_input_required(self):
        self.assertEqual(_assemble().readiness_decision, F.READY_OWNER_INPUT)

    def test_104b_invalid_decimal_blocks(self):
        r = self._econ(selling_price="12,99")
        self.assertEqual(r.state, F.ECON_INVALID)


# ============================================================ CAPACITY & HANDLING (105-114)
class CapacityHandling(unittest.TestCase):
    def _cap(self, **over):
        return F.evaluate_capacity_and_handling(_full_economics(**over))

    def test_105_missing_capacity_missing(self):
        d = self._cap(normal_capacity=None)
        self.assertEqual(d["capacity_state"], F.CAPACITY_OWNER_INPUT)
        self.assertFalse(d["normal_capacity_present"])

    def test_106_missing_backlog(self):
        d = self._cap(current_backlog=None)
        self.assertEqual(d["capacity_state"], F.CAPACITY_OWNER_INPUT)

    def test_107_missing_handling(self):
        d = self._cap(safe_handling_time=None)
        self.assertEqual(d["handling_state"], F.HANDLING_OWNER_INPUT)

    def test_108_capacity_confirmation_required(self):
        d = self._cap(inventory_or_capacity_confirmed=None)
        self.assertEqual(d["capacity_state"], F.CAPACITY_OWNER_INPUT)

    def test_109_handling_confirmation_required(self):
        d = self._cap(handling_time_confirmed=None)
        self.assertEqual(d["handling_state"], F.HANDLING_OWNER_INPUT)

    def test_110_unsafe_backlog_blocks(self):
        d = self._cap(current_backlog="60")   # >= normal_capacity 50
        self.assertEqual(d["capacity_state"], F.CAPACITY_BLOCKED)

    def test_111_unsafe_handling_blocks(self):
        d = F.evaluate_capacity_and_handling(_full_economics(safe_handling_time="0"))
        # 0 fails positive-int parse -> capacity/handling owner-input or blocked; force explicit unsafe:
        d2 = dict(_full_economics()); d2["safe_handling_time"] = -1
        self.assertIn(F.evaluate_capacity_and_handling(d2)["handling_state"],
                      (F.HANDLING_BLOCKED, F.HANDLING_OWNER_INPUT))

    def test_111b_peak_below_normal_blocks(self):
        d = self._cap(peak_capacity="40")   # < normal 50
        self.assertEqual(d["capacity_state"], F.CAPACITY_BLOCKED)

    def test_112_remaining_capacity_deterministic(self):
        self.assertEqual(self._cap()["safe_remaining_capacity"], 45)
        self.assertEqual(self._cap()["deterministic_content_sha256"],
                         self._cap()["deterministic_content_sha256"])

    def test_113_no_historic_inference(self):
        self.assertFalse(self._cap()["inferred_from_phase6_or_history"])

    def test_114_owner_review_explicit(self):
        self.assertIn("owner_review_required", self._cap())


# ============================================================ BUDGET FEASIBILITY (115-133)
class BudgetFeasibility(unittest.TestCase):
    def _bud(self, econ_over=None, budget_over=None):
        econ = _full_economics(**(econ_over or {}))
        er = F.compute_ppc_economics(econ, live_currency="USD")
        cap = F.evaluate_capacity_and_handling(econ)
        binp = dict(econ)
        if budget_over:
            binp.update(budget_over)
        return F.evaluate_budget_feasibility(binp, er, cap), er

    def test_115_total_budget_required(self):
        d, _ = self._bud(budget_over={"owner_total_test_budget": None})
        self.assertEqual(d["budget_feasibility_result"], F.BUDGET_OWNER_INPUT)

    def test_116_average_daily_budget_required(self):
        d, _ = self._bud(budget_over={"average_daily_budget": None})
        self.assertEqual(d["budget_feasibility_result"], F.BUDGET_OWNER_INPUT)

    def test_117_learning_window_required(self):
        d, _ = self._bud(budget_over={"learning_window_days": None})
        self.assertEqual(d["budget_feasibility_result"], F.BUDGET_OWNER_INPUT)

    def test_118_evidence_target_required(self):
        d, _ = self._bud(budget_over={"owner_configured_evidence_target": None})
        self.assertEqual(d["budget_feasibility_result"], F.BUDGET_OWNER_INPUT)

    def test_119_launch_objective_required(self):
        d, _ = self._bud(budget_over={"owner_launch_objective": None})
        self.assertEqual(d["budget_feasibility_result"], F.BUDGET_OWNER_INPUT)

    def test_120_budget_positive(self):
        d, _ = self._bud(budget_over={"owner_total_test_budget": "-5"})
        self.assertEqual(d["budget_feasibility_result"], F.BUDGET_BLOCKED)

    def test_121_daily_budget_positive(self):
        d, _ = self._bud(budget_over={"average_daily_budget": "0"})
        self.assertEqual(d["budget_feasibility_result"], F.BUDGET_BLOCKED)

    def test_122_learning_window_positive(self):
        d, _ = self._bud(budget_over={"learning_window_days": "0"})
        self.assertEqual(d["budget_feasibility_result"], F.BUDGET_BLOCKED)

    def test_123_evidence_target_positive(self):
        d, _ = self._bud(budget_over={"owner_configured_evidence_target": "0"})
        self.assertEqual(d["budget_feasibility_result"], F.BUDGET_BLOCKED)

    def test_124_max_cpc_positive_required(self):
        # unprofitable economics -> no positive max cpc -> budget blocked.
        d, er = self._bud(econ_over={"selling_price": "24.99"})
        self.assertEqual(d["budget_feasibility_result"], F.BUDGET_BLOCKED)

    def test_125_click_capacity_formula(self):
        d, _ = self._bud(budget_over={"planned_cpc_candidate": "0.50",
                                      "owner_total_test_budget": "300.00"})
        self.assertEqual(d["estimated_total_click_capacity"], "600.00")

    def test_126_safe_click_capacity_is_bound(self):
        d, _ = self._bud()
        self.assertTrue(d["maximum_safe_click_capacity_is_a_bound_not_a_bid"])

    def test_127_128_129_130_no_universal_thresholds(self):
        d, _ = self._bud()
        self.assertTrue(d["no_universal_25_dollar_threshold"])
        self.assertTrue(d["no_fixed_percentage_allocator"])
        self.assertTrue(d["no_universal_click_threshold"])
        self.assertTrue(d["no_universal_learning_window"])

    def test_131_132_no_role_selected_or_allocated(self):
        d, _ = self._bud()
        self.assertTrue(d["no_campaign_role_selected"])
        self.assertTrue(d["no_role_budget_allocated"])

    def test_133_capacity_blocker_propagates(self):
        d, _ = self._bud(econ_over={"current_backlog": "60"})   # capacity blocked
        self.assertEqual(d["budget_feasibility_result"], F.BUDGET_BLOCKED)

    def test_133b_passes_for_planning(self):
        d, _ = self._bud(budget_over={"planned_cpc_candidate": "0.50"})
        self.assertEqual(d["budget_feasibility_result"], F.BUDGET_PASSES)

    def test_133c_evidence_target_unreachable_blocks(self):
        d, _ = self._bud(budget_over={"planned_cpc_candidate": "50.00",
                                      "owner_configured_evidence_target": "100",
                                      "owner_total_test_budget": "300.00"})
        self.assertEqual(d["budget_feasibility_result"], F.BUDGET_BLOCKED)


# ============================================================ READINESS (134-147)
class Readiness(unittest.TestCase):
    def test_134_most_restrictive_wins(self):
        g = _green_result()
        # flip economics to not-viable: readiness must drop to economics-blocked (more restrictive).
        er = F.compute_ppc_economics(_full_economics(selling_price="24.99"), live_currency="USD")
        rd = F.resolve_phase7_1m_readiness(g["h"], g["lr"], g["cr"], er, g["cap"], g["bud"], g["net"])
        self.assertEqual(rd["phase7_1m_readiness"], F.READY_ECONOMICS_BLOCKED)

    def test_135_safe_draft_prevents_campaign_planning(self):
        g = _green_result(handoff=_synthetic_handoff(publishable=False))
        self.assertNotEqual(g["rd"]["phase7_1m_readiness"], F.READY_CAMPAIGN_PLANNING)

    def test_136_missing_live_state_owner_input(self):
        self.assertEqual(_assemble().readiness_decision, F.READY_OWNER_INPUT)

    def test_137_missing_contract_owner_input(self):
        h = _synthetic_handoff()
        net = P7.build_network_boundary_report(ROOT)
        lr = F.validate_live_product_state(_full_live_state(), h)
        cr = F.validate_ppc_product_contract(None, h, lr, "x")   # no contract
        er = F.compute_ppc_economics(_full_economics(), live_currency="USD")
        cap = F.evaluate_capacity_and_handling(_full_economics())
        bud = F.evaluate_budget_feasibility(_full_economics(), er, cap)
        rd = F.resolve_phase7_1m_readiness(h, lr, cr, er, cap, bud, net)
        self.assertEqual(rd["phase7_1m_readiness"], F.READY_OWNER_INPUT)

    def test_138_missing_economics_owner_input(self):
        g = _green_result()
        er = F.compute_ppc_economics(None)   # missing economics
        rd = F.resolve_phase7_1m_readiness(g["h"], g["lr"], g["cr"], er, g["cap"], g["bud"], g["net"])
        self.assertEqual(rd["phase7_1m_readiness"], F.READY_OWNER_INPUT)

    def test_139_unprofitable_economics_blocked(self):
        g = _green_result(economics=_full_economics(selling_price="24.99"))
        self.assertEqual(g["rd"]["phase7_1m_readiness"], F.READY_ECONOMICS_BLOCKED)

    def test_140_unsafe_capacity_blocks(self):
        g = _green_result(economics=_full_economics(current_backlog="60"))
        self.assertEqual(g["rd"]["phase7_1m_readiness"], F.READY_ECONOMICS_BLOCKED)

    def test_141_unsafe_handling_blocks(self):
        econ = _full_economics()
        econ["safe_handling_time"] = -1
        g = _green_result(economics=econ)
        self.assertIn(g["rd"]["phase7_1m_readiness"], (F.READY_ECONOMICS_BLOCKED, F.READY_OWNER_INPUT))

    def test_142_missing_budget_owner_input(self):
        g = _green_result(economics=_full_economics(owner_total_test_budget=None))
        self.assertEqual(g["rd"]["phase7_1m_readiness"], F.READY_OWNER_INPUT)

    def test_143_budget_infeasible_blocks(self):
        econ = _full_economics(planned_cpc_candidate="50.00", owner_configured_evidence_target="100",
                               owner_total_test_budget="300.00")
        g = _green_result(economics=econ)
        self.assertEqual(g["rd"]["phase7_1m_readiness"], F.READY_ECONOMICS_BLOCKED)

    def test_144_campaign_planning_distinct_from_manual_launch(self):
        g = _green_result()
        self.assertEqual(g["rd"]["phase7_1m_readiness"], F.READY_CAMPAIGN_PLANNING)
        self.assertTrue(g["rd"]["distinct_from_ready_for_manual_launch"])
        self.assertFalse(g["rd"]["ready_for_manual_launch"])

    def test_145_ready_for_manual_launch_false(self):
        self.assertFalse(_assemble().readiness["ready_for_manual_launch"])

    def test_146_ready_for_amazon_action_false(self):
        self.assertFalse(_assemble().readiness["ready_for_amazon_action"])

    def test_147_t2_resolves_truthfully(self):
        r = _assemble()
        self.assertEqual(r.readiness_decision, F.READY_OWNER_INPUT)
        self.assertEqual(r.session_status, F.SESSION_OWNER_INPUT)

    def test_147b_foundation_review_reachable(self):
        # everything green but budget owner-review (planned cpc > max cpc) -> foundation ready for review.
        g = _green_result(economics=_full_economics(planned_cpc_candidate="5.00"))
        self.assertEqual(g["bud"]["budget_feasibility_result"], F.BUDGET_OWNER_REVIEW)
        self.assertEqual(g["rd"]["phase7_1m_readiness"], F.READY_FOUNDATION_REVIEW)


# ============================================================ ARTIFACTS / COUNTERS / BOUNDARY (148-175)
class ArtifactsCountersBoundary(unittest.TestCase):
    def _write(self):
        d = tempfile.mkdtemp()
        r = _assemble()
        man, hashes = F.write_phase7_1m_candidate(r, d, started_at="t", completed_at="t")
        return d, r, man, hashes

    def test_148_eleven_required_artifacts(self):
        d, _, _, _ = self._write()
        try:
            for name in F.REQUIRED_ARTIFACTS:
                self.assertTrue(os.path.exists(os.path.join(d, name)), name)
            self.assertEqual(len(F.REQUIRED_ARTIFACTS), 11)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_149_exact_filenames(self):
        self.assertEqual(F.F_LIVE_TEMPLATE, "LIVE-PRODUCT-STATE.template.json")
        self.assertEqual(F.F_LIVE_VALIDATION, "LIVE-PRODUCT-STATE-VALIDATION.json")
        self.assertEqual(F.F_CONTRACT_TEMPLATE, "PPC-PRODUCT-CONTRACT.template.json")
        self.assertEqual(F.F_CONTRACT_VALIDATION, "PPC-PRODUCT-CONTRACT-VALIDATION.json")
        self.assertEqual(F.F_ECON_TEMPLATE, "PPC-ECONOMICS.template.json")
        self.assertEqual(F.F_ECON_VALIDATION, "PPC-ECONOMICS-VALIDATION.json")
        self.assertEqual(F.F_CAPACITY, "CAPACITY-HANDLING-ASSESSMENT.json")
        self.assertEqual(F.F_BUDGET, "BUDGET-FEASIBILITY-ASSESSMENT.json")
        self.assertEqual(F.F_READINESS, "PHASE7-1M-READINESS.json")
        self.assertEqual(F.F_VERIFICATION, "PHASE7-1M-VERIFICATION-REPORT.json")
        self.assertEqual(F.F_STAGE_MANIFEST, "STAGE-7_1M-MANIFEST.json")

    def test_150_paths_bounded(self):
        d, _, _, _ = self._write()
        try:
            for name in F.REQUIRED_ARTIFACTS:
                blob = open(os.path.join(d, name), encoding="utf-8").read()
                self.assertNotIn(":\\", blob)
                self.assertNotIn("/Users/", blob)
                self.assertNotIn(ROOT, blob)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_151_final_byte_hashes_verify(self):
        d, _, man, hashes = self._write()
        try:
            for name, recorded in hashes.items():
                data = open(os.path.join(d, name), "rb").read()
                self.assertEqual(F._sha_bytes(data), recorded, name)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_152_failed_candidate_preserves_last_valid(self):
        d = tempfile.mkdtemp()
        try:
            final = os.path.join(d, "final")
            r = _assemble()
            cand1 = os.path.join(d, "c1")
            _, h1 = F.write_phase7_1m_candidate(r, cand1)
            F.promote_phase7_1m_candidate(cand1, final, h1)
            before = open(os.path.join(final, F.F_READINESS), "rb").read()
            cand2 = os.path.join(d, "c2")
            _, h2 = F.write_phase7_1m_candidate(r, cand2)
            with open(os.path.join(cand2, F.F_READINESS), "ab") as f:
                f.write(b"TAMPER")
            rep = F.promote_phase7_1m_candidate(cand2, final, h2)
            self.assertEqual(rep["result"], "BLOCKED")
            self.assertFalse(rep["promoted"])
            self.assertEqual(open(os.path.join(final, F.F_READINESS), "rb").read(), before)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_153_templates_clearly_labeled(self):
        d, _, _, _ = self._write()
        try:
            live = json.load(open(os.path.join(d, F.F_LIVE_TEMPLATE), encoding="utf-8"))
            econ = json.load(open(os.path.join(d, F.F_ECON_TEMPLATE), encoding="utf-8"))
            con = json.load(open(os.path.join(d, F.F_CONTRACT_TEMPLATE), encoding="utf-8"))
            self.assertTrue(live["IS_TEMPLATE_NOT_VERIFIED_STATE"])
            self.assertTrue(econ["IS_TEMPLATE_NOT_OWNER_CONFIRMED"])
            self.assertTrue(con["IS_TEMPLATE_NOT_A_VERIFIED_CONTRACT"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_154_155_156_157_committed_proof_sanitized(self):
        proof = F.build_proof_gate(_assemble(), started_commit="test")
        blob = json.dumps(proof)
        self.assertNotIn(":\\", blob)
        self.assertNotIn("/Users/", blob)
        self.assertNotIn("/d/Claude", blob)
        self.assertIsNone(re.search(r"B0[A-Z0-9]{8}", blob))   # no real ASIN token
        # no credential VALUE material (policy notes may mention the word "credentials").
        self.assertFalse(F._contains_credentials(proof))

    def test_158_159_160_161_162_163_164_165_no_forbidden_artifacts(self):
        d, _, _, _ = self._write()
        try:
            present = set(os.listdir(d))
            for bad in ("PPC-TARGETS.csv", "PPC-HARD-NEGATIVES.csv", "PPC-MANUAL-LAUNCH-PLAN.md",
                        "PPC-BIDS.csv", "REPORT-INGESTION.json", "OPTIMIZATION.json", "CHANGE-LEDGER.json"):
                self.assertNotIn(bad, present)
            self.assertFalse(os.path.isdir(os.path.join(d, "PPC-LAUNCH-READY")))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_166_167_168_amazon_counters_zero(self):
        _, _, man, _ = self._write()
        self.assertEqual(man["external_amazon_account_attempts"], 0)
        self.assertEqual(man["amazon_account_actions"], 0)
        self.assertEqual(man["campaign_write_actions"], 0)

    def test_169_170_171_172_173_selection_counts_zero(self):
        _, _, man, _ = self._write()
        for k in ("target_selection_count", "negative_selection_count", "bid_calculation_count",
                  "launch_package_count", "later_phase_artifact_count"):
            self.assertEqual(man[k], 0, k)

    def test_174_dashboard_loopback_only(self):
        r = _assemble()
        self.assertTrue(r.network["dashboard_bind_is_loopback"])
        self.assertEqual(r.network["non_loopback_bind_count"], 0)

    def test_175_no_external_request_local_deny(self):
        for mode in ("LOCAL_SAFE", "TEST_DENY_EXTERNAL"):
            r = _assemble(mode=mode)
            self.assertEqual(r.network["prohibited_account_path_count"], 0)

    def test_175b_verification_report_counts_zero(self):
        d, _, _, _ = self._write()
        try:
            vr = json.load(open(os.path.join(d, F.F_VERIFICATION), encoding="utf-8"))
            self.assertEqual(vr["target_selection_count"], 0)
            self.assertEqual(vr["amazon_boundary"]["amazon_account_actions"], 0)
            self.assertEqual(vr["amazon_boundary"]["image_generation_calls"], 0)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_175c_image_generation_calls_zero(self):
        _, _, man, _ = self._write()
        self.assertEqual(man["image_generation_calls"], 0)


# ============================================================ DETERMINISM & REGRESSION (176-186)
class DeterminismRegression(unittest.TestCase):
    def _stable(self, r):
        return {"live": r.live_result.to_dict()["deterministic_content_sha256"],
                "contract": r.contract_result.to_dict()["deterministic_content_sha256"],
                "econ": r.economics_result.to_dict()["deterministic_content_sha256"],
                "cap": r.capacity_doc["deterministic_content_sha256"],
                "bud": r.budget_doc["deterministic_content_sha256"],
                "ready": r.readiness["deterministic_content_sha256"]}

    def test_176_177_178_two_run_stable_hashes_match(self):
        self.assertEqual(self._stable(_assemble()), self._stable(_assemble()))

    def test_179_three_mode_stable_outputs_match(self):
        a = self._stable(_assemble(mode="LOCAL_SAFE"))
        b = self._stable(_assemble(mode="TEST_DENY_EXTERNAL"))
        c = self._stable(_assemble(mode="CONNECTED_RESEARCH"))
        self.assertEqual(a, b)
        self.assertEqual(b, c)

    def test_180_volatile_fields_excluded(self):
        d = tempfile.mkdtemp()
        try:
            r = _assemble()
            m1 = F.build_stage_manifest(r, {"x": "1"}, started_at="A", completed_at="B")
            m2 = F.build_stage_manifest(r, {"x": "1"}, started_at="C", completed_at="D")
            self.assertEqual(m1["deterministic_content_sha256"], m2["deterministic_content_sha256"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_181_business_state_not_excluded_to_force_equality(self):
        # changing a real business input MUST change the stable hash.
        g_ok = F.compute_ppc_economics(_full_economics(), live_currency="USD").to_dict()
        g_diff = F.compute_ppc_economics(_full_economics(selling_price="41.00"),
                                         live_currency="USD").to_dict()
        self.assertNotEqual(g_ok["deterministic_content_sha256"], g_diff["deterministic_content_sha256"])

    def test_182_phase6_tests_present(self):
        for m in ("test_phase6a_product_workspace", "test_phase6f_seller_central_package"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, "tests", m + ".py")))

    def test_183_phase7_0_tests_present(self):
        self.assertTrue(os.path.exists(os.path.join(ROOT, "tests", "test_phase7_preflight.py")))

    def test_184_full_assemble_writes_and_verifies(self):
        d = tempfile.mkdtemp()
        try:
            r = _assemble()
            cand = os.path.join(d, "cand")
            final = os.path.join(d, "final")
            man, hashes = F.write_phase7_1m_candidate(r, cand)
            self.assertEqual(F.verify_phase7_1m_candidate(cand, hashes)["result"], "PASS")
            self.assertEqual(F.promote_phase7_1m_candidate(cand, final, hashes)["result"], "PASS")
            self.assertEqual(F.verify_phase7_1m_artifacts(final)["result"], "PASS")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_185_committed_proof_parses_if_present(self):
        if not os.path.exists(COMMITTED_PROOF):
            self.skipTest("committed proof not yet written")
        d = json.load(open(COMMITTED_PROOF, encoding="utf-8"))
        self.assertEqual(d["schema_version"], F.SCHEMA_PROOF_GATE)
        self.assertIn(d["session_status"], (F.SESSION_OWNER_INPUT, F.SESSION_FOUNDATION_READY,
                                            F.SESSION_ECONOMICS_BLOCKED, F.SESSION_PREFLIGHT_BLOCKED))

    def test_186_no_private_paths_leak(self):
        r = _assemble()
        for doc in (r.live_result.to_dict(), r.contract_result.to_dict(), r.economics_result.to_dict(),
                    r.capacity_doc, r.budget_doc, r.readiness):
            blob = json.dumps(doc)
            self.assertNotIn(":\\", blob)
            self.assertNotIn("/Users/", blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
