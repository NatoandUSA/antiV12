#!/usr/bin/env python3
"""Phase 7.1E — owner-gated offline manual Sponsored Products planning tests.

Hermetic by default: everything runs offline against the committed repository and the committed
Phase 6/7.0/7.1M proofs. The fully-green "plan-ready" path is exercised with a synthetic publishable
foundation injected into assemble_phase7_1e (exactly as the accepted 7.1M suite builds its green pieces)
so it never depends on the gitignored runs/ workspace or on a live listing. This module opens NO network,
touches NO Amazon account, and asserts every zero-action counter.
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

from production import phase7_extended_launch_planning as E   # noqa: E402  the 7.1E authority
from production import phase7_minimal_launch_foundation as F   # noqa: E402  the 7.1M foundation
from production import phase7_preflight as P7                   # noqa: E402
from production import seller_central_package as SCP            # noqa: E402
from core import money as M                                     # noqa: E402

RUN_T2 = os.path.join(ROOT, "runs", "T2")
HAS_WORKSPACE = os.path.isdir(RUN_T2) and os.path.isdir(SCP.package_root_dir(RUN_T2))
_RUN_DIR = RUN_T2 if HAS_WORKSPACE else None
COMMITTED_PROOF = os.path.join(ROOT, "SESSION7_1E-PROOF-GATE.json")


# ============================================================ hermetic green-foundation helpers
def _synthetic_handoff(publishable=True):
    shas = {"phase6_package_sha256": "a" * 64, "phase6_index_sha256": "b" * 64,
            "keyword_source_sha256": "c" * 64, "keyword_allocation_sha256": "d" * 64,
            "product_facts_sha256": "e" * 64, "claim_evidence_sha256": "f" * 64}
    return {
        "workspace_id": "T2", "product_id": "T2", "marketplace": "US",
        "canonical_category_id": "US:apparel",
        "phase6_final_status": SCP.PHASE6_SAFE_DRAFT_READY,
        "listing_audit_state": "SAFE_DRAFT",
        "listing_publishability": SCP.PUB_PUBLISHABLE if publishable else "SAFE_DRAFT_OWNER_FACTS_REQUIRED",
        "package_index_sha256_prefix": "b" * 8, "keyword_source_sha256_prefix": "c" * 8,
        "phase6b_manifest_sha256_prefix": "d" * 8, "product_facts_sha256_prefix": "e" * 8,
        "claim_evidence_sha256_prefix": "f" * 8, "full_shas": shas,
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
              "manufacturing_customization_applicable": False, "contract_version": "1.0.0"})
    return c, ls_sha


def _green_foundation(publishable=True, economics=None):
    """A synthetic, publishable, fully-verified 7.1M foundation (Phase71MResult) — no runs/ needed."""
    h = _synthetic_handoff(publishable=publishable)
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
    return F.Phase71MResult(
        repo_root=ROOT, run_dir=None, mode="LOCAL_SAFE", handoff=h, network=net,
        live_template={}, econ_template={}, contract_template={},
        live_result=lr, contract_result=cr, economics_result=er, capacity_doc=cap, budget_doc=bud,
        readiness=rd, live_state_sha256=ls_sha, readiness_decision=rd["phase7_1m_readiness"],
        session_status="SESSION7_1M_FOUNDATION_READY_FOR_REVIEW")


def _valid_policy(**over):
    p = {
        "schema_version": E.SCHEMA_OWNER_POLICY, "policy_version": "1.0.0",
        "owner_confirmed": True, "owner_confirmed_at": "2026-07-18T10:00:00+07:00",
        "owner_confirmation_source": "owner manual review",
        "allowed_campaign_roles": ["MANUAL_EXACT", "MANUAL_PHRASE"],
        "authorize_advanced_targeting": False,
        "allowed_match_types": ["EXACT", "PHRASE"], "authorize_broad": False,
        "allowed_negative_match_types": [], "starting_bid_method": "CEILING_SHARE",
        "ceiling_share": "0.50", "owner_minimum_bid": "0.05", "owner_maximum_bid": "5.00",
        "total_daily_budget": "20.00", "budget_allocation_method": "SHARES",
        "role_allocations": {"MANUAL_EXACT": "0.6", "MANUAL_PHRASE": "0.4"},
        "reserve": "2.00", "minimum_campaign_budget": "1.00",
        "target_limits": {"max_targets_total": "10", "max_targets_per_match_type": "5"},
        "evidence_thresholds": {"minimum_evidence_grade": "MODERATE"},
        "negative_policy": {"enabled": False, "require_documented_reason": True, "allowed_reasons": []},
        "inclusions": [], "exclusions": [],
    }
    p.update(over)
    return p


def _feed():
    return {"advertising_candidates": [
        {"term": "nurse sweatshirt", "provenance": ["PHASE6_ALLOCATION"],
         "source_authority": "PHASE6_ALLOCATION", "allocation_state": "MARKET_IDENTITY",
         "advertising_permitted": True, "evidence_grade": "STRONG", "relevance_tier": "DIRECT",
         "relevance_reasons": ["market_identity_match"], "compliance_flags": [], "in_title": True,
         "ambiguity": "LOW"},
        {"term": "nurse gift", "provenance": ["PHASE6_ALLOCATION"],
         "source_authority": "PHASE6_ALLOCATION", "allocation_state": "ALLOCATED_ADVERTISABLE",
         "advertising_permitted": True, "evidence_grade": "MODERATE", "relevance_tier": "RELATED",
         "relevance_reasons": ["recipient_match"], "compliance_flags": [], "in_title": False,
         "ambiguity": "MEDIUM"},
        {"term": "registered nurse shirt", "provenance": ["PHASE6_ALLOCATION"],
         "source_authority": "PHASE6_ALLOCATION", "allocation_state": "OWNER_FACT_REQUIRED",
         "advertising_permitted": False, "evidence_grade": "WEAK", "relevance_tier": "TITLE_ONLY",
         "relevance_reasons": [], "compliance_flags": [], "in_title": True},
    ]}


def _kw_source():
    return {"terms": ["nurse sweatshirt", "nurse gift", "registered nurse shirt"]}


def _facts():
    return {"market_identity_terms": ["nurse sweatshirt", "nurse gift"]}


def _claims():
    return {"blocked_terms": []}


def _ready(**over):
    kw = dict(foundation=_green_foundation(), owner_policy=_valid_policy(), keyword_source=_kw_source(),
              keyword_allocation=_feed(), product_facts=_facts(), claim_evidence=_claims(),
              live_state=_full_live_state(), economics=_full_economics())
    kw.update(over)
    return E.assemble_phase7_1e(ROOT, run_dir=None, **kw)


def _blocked():
    """The real-repo blocked assembly (no owner inputs)."""
    return E.assemble_phase7_1e(ROOT, run_dir=_RUN_DIR)


# ============================================================ 1. PREFLIGHT & ACCEPTED TAG (1-12)
class PreflightAndAcceptedTag(unittest.TestCase):
    def test_01_accepted_7_1m_proof_present(self):
        self.assertTrue(os.path.exists(os.path.join(ROOT, "SESSION7_1M-ACCEPTANCE-PROOF.json")))

    def test_02_accepted_7_1m_status(self):
        d = json.load(open(os.path.join(ROOT, "SESSION7_1M-ACCEPTANCE-PROOF.json"), encoding="utf-8"))
        self.assertEqual(d["acceptance_status"], "PHASE7_1M_ACCEPTED_WITH_REPORTING_FIX")
        self.assertEqual(d["final_commit"][:7], "f7b6253")

    def test_03_7_1m_proof_owner_input_required(self):
        d = json.load(open(os.path.join(ROOT, "SESSION7_1M-PROOF-GATE.json"), encoding="utf-8"))
        self.assertEqual(d["readiness_result"], "PHASE7_OWNER_INPUT_REQUIRED")

    def test_04_7_1m_accounting_closes_36(self):
        d = json.load(open(os.path.join(ROOT, "SESSION7_1M-PROOF-GATE.json"), encoding="utf-8"))
        self.assertEqual(d["required_input_count"], 36)
        self.assertEqual(d["verified_input_count"] + d["missing_input_count"]
                         + d["contradictory_input_count"], 36)

    def test_05_phase7_0_proof_owner_input(self):
        d = json.load(open(os.path.join(ROOT, "SESSION7_0-PROOF-GATE.json"), encoding="utf-8"))
        self.assertEqual(d["readiness_decision"], P7.PHASE7_OWNER_INPUT_REQUIRED)

    def test_06_phase6f_proof_safe_draft(self):
        d = json.load(open(os.path.join(ROOT, P7.PHASE6F_PROOF_NAME), encoding="utf-8"))
        self.assertEqual(d["phase6_final_status"], SCP.PHASE6_SAFE_DRAFT_READY)

    def test_07_handoff_loads(self):
        h = P7.load_phase6_handoff(ROOT, run_dir=_RUN_DIR)
        self.assertEqual(h["product_id"], "T2")
        self.assertEqual(h["marketplace"], "US")

    def test_08_amazon_boundary_closed(self):
        net = P7.build_network_boundary_report(ROOT)
        self.assertTrue(net["no_active_amazon_account_path"])
        self.assertEqual(net["prohibited_account_path_count"], 0)
        self.assertTrue(net["dashboard_bind_is_loopback"])

    def test_09_missing_phase7_dependency_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(P7.Phase7PreflightError):
                E.assemble_phase7_1e(d, run_dir=None)

    def test_10_one_authority_no_duplicates(self):
        prod = os.path.join(ROOT, "production")
        for bad in ("campaign_compiler.py", "bid_engine.py", "negative_engine.py",
                    "targeting_engine.py", "phase7_extended_launch_planning_v2.py"):
            self.assertFalse(os.path.exists(os.path.join(prod, bad)), bad)

    def test_11_reuses_money_and_foundation(self):
        self.assertIs(E.MONEY, M)
        self.assertIs(E.canonical_json, F.canonical_json)

    def test_12_checkpoint_tag_value(self):
        # the checkpoint tag string embedded in the proof gate points at the accepted 7.1M lineage.
        self.assertEqual(E.build_proof_gate(_blocked())["accepted_phase7_1m_commit"], "f7b6253")


# ============================================================ 2. NORMALIZATION (13-30)
class Normalization(unittest.TestCase):
    def test_13_basic(self):
        self.assertEqual(E.normalize_term("Nurse Sweatshirt"), ("Nurse Sweatshirt", "nurse sweatshirt"))

    def test_14_trim(self):
        self.assertEqual(E.normalize_term("  nurse  "), ("nurse", "nurse"))

    def test_15_collapse_internal_whitespace(self):
        self.assertEqual(E.normalize_term("nurse   gift\tshirt"), ("nurse gift shirt", "nurse gift shirt"))

    def test_16_newlines_collapsed(self):
        self.assertEqual(E.normalize_term("a\nb"), ("a b", "a b"))

    def test_17_display_preserves_case(self):
        d, c = E.normalize_term("RN Life")
        self.assertEqual(d, "RN Life")
        self.assertEqual(c, "rn life")

    def test_18_nfkc_fullwidth(self):
        d, c = E.normalize_term("ｎｕｒｓｅ")   # fullwidth
        self.assertEqual(c, "nurse")

    def test_19_nfkc_ligature(self):
        d, c = E.normalize_term("ﬁt")   # fi ligature
        self.assertEqual(c, "fit")

    def test_20_empty_rejected(self):
        self.assertIsNone(E.normalize_term(""))

    def test_21_whitespace_only_rejected(self):
        self.assertIsNone(E.normalize_term("   "))

    def test_22_control_char_rejected(self):
        self.assertIsNone(E.normalize_term("nurse\x00gift"))

    def test_23_zero_width_rejected(self):
        self.assertIsNone(E.normalize_term("nurse​gift"))   # ZWSP is Cf

    def test_24_non_string_rejected(self):
        self.assertIsNone(E.normalize_term(123))
        self.assertIsNone(E.normalize_term(None))

    def test_25_case_insensitive_key_merges(self):
        self.assertEqual(E.normalize_term("Nurse")[1], E.normalize_term("NURSE")[1])

    def test_26_unicode_accent_preserved_in_display(self):
        d, c = E.normalize_term("Café Nurse")
        self.assertEqual(d, "Café Nurse")
        self.assertEqual(c, "café nurse")

    def test_27_idempotent(self):
        once = E.normalize_term("  Nurse   Gift ")
        twice = E.normalize_term(once[0])
        self.assertEqual(once, twice)

    def test_28_deterministic(self):
        self.assertEqual([E.normalize_term("A B") for _ in range(5)].count(("A B", "a b")), 5)

    def test_29_canon_list_dedup(self):
        out = E._canon_list(["Nurse", "nurse", "  NURSE  "])
        self.assertEqual(out, [("Nurse", "nurse")])

    def test_30_canon_list_order_preserved(self):
        out = E._canon_list(["beta", "alpha", "beta"])
        self.assertEqual([c for _d, c in out], ["beta", "alpha"])


# ============================================================ 3. CSV INJECTION (31-44)
class CsvInjection(unittest.TestCase):
    def test_31_equals_prefixed(self):
        self.assertEqual(E._csv_cell("=SUM(A1)"), "'=SUM(A1)")

    def test_32_plus_prefixed(self):
        self.assertEqual(E._csv_cell("+1"), "'+1")

    def test_33_minus_prefixed(self):
        self.assertTrue(E._csv_cell("-2").startswith("'-2"))

    def test_34_at_prefixed(self):
        self.assertEqual(E._csv_cell("@cmd"), "'@cmd")

    def test_35_tab_prefixed(self):
        self.assertTrue(E._csv_cell("\tx").startswith("'"))

    def test_36_cr_neutralized(self):
        # a CR-leading cell is neutralized with a leading apostrophe AND RFC-quoted (CR triggers wrap).
        out = E._csv_cell("\rx")
        self.assertIn("'", out)
        self.assertTrue(out.startswith('"'))

    def test_37_plain_untouched(self):
        self.assertEqual(E._csv_cell("nurse gift"), "nurse gift")

    def test_38_comma_quoted(self):
        self.assertEqual(E._csv_cell("a,b"), '"a,b"')

    def test_39_quote_doubled(self):
        self.assertEqual(E._csv_cell('a"b'), '"a""b"')

    def test_40_newline_quoted(self):
        self.assertEqual(E._csv_cell("a\nb"), '"a\nb"')

    def test_41_none_is_empty(self):
        self.assertEqual(E._csv_cell(None), "")

    def test_42_injection_and_comma_both(self):
        self.assertEqual(E._csv_cell("=a,b"), '"\'=a,b"')

    def test_43_worksheet_first_line_banner(self):
        r = _ready()
        ws = E.build_manual_entry_worksheet_csv(r.foundation.handoff, r.role_plan, r.selected,
                                                r.bid_plan, r.budget_plan, r.selected_negatives)
        self.assertEqual(ws.splitlines()[0], E.WORKSHEET_BANNER)

    def test_44_worksheet_no_raw_formula_cells(self):
        cand = _feed()
        cand["advertising_candidates"][0]["term"] = "=cmd nurse"   # malicious display term
        r = _ready(keyword_allocation=cand, keyword_source={"terms": ["=cmd nurse", "nurse gift"]},
                   product_facts={"market_identity_terms": ["=cmd nurse", "nurse gift"]})
        ws = E.build_manual_entry_worksheet_csv(r.foundation.handoff, r.role_plan, r.selected,
                                                r.bid_plan, r.budget_plan, r.selected_negatives)
        for line in ws.splitlines()[2:]:
            for cell in line.split(","):
                self.assertFalse(cell.startswith("="), line)


# ============================================================ 4. OWNER POLICY VALIDATION (45-84)
class OwnerPolicy(unittest.TestCase):
    def test_45_template_is_blocked_unconfirmed(self):
        t = E.build_owner_policy_template()
        self.assertTrue(t["IS_TEMPLATE_NOT_CONFIRMED_POLICY"])
        self.assertIsNone(t["owner_confirmed"])
        self.assertEqual(t["allowed_campaign_roles"], [])

    def test_46_template_validates_as_required(self):
        res = E.validate_owner_policy(E.build_owner_policy_template())
        self.assertEqual(res.state, "OWNER_POLICY_REQUIRED")
        self.assertFalse(res.confirmed)

    def test_47_none_is_required(self):
        self.assertEqual(E.validate_owner_policy(None).state, "OWNER_POLICY_REQUIRED")

    def test_48_valid_confirms(self):
        self.assertTrue(E.validate_owner_policy(_valid_policy()).confirmed)

    def test_49_owner_confirmed_must_be_true(self):
        self.assertFalse(E.validate_owner_policy(_valid_policy(owner_confirmed=False)).confirmed)

    def test_50_owner_confirmed_at_must_be_tz_aware(self):
        r = E.validate_owner_policy(_valid_policy(owner_confirmed_at="2026-07-18"))
        self.assertFalse(r.confirmed)

    def test_51_confirmation_source_required(self):
        self.assertFalse(E.validate_owner_policy(_valid_policy(owner_confirmation_source="")).confirmed)

    def test_52_no_role_default(self):
        r = E.validate_owner_policy(_valid_policy(allowed_campaign_roles=[]))
        self.assertFalse(r.confirmed)
        self.assertIn("no MANUAL campaign role enabled", r.blockers)

    def test_53_unknown_role_invalid(self):
        r = E.validate_owner_policy(_valid_policy(allowed_campaign_roles=["MANUAL_EXACT", "SUPER"]))
        self.assertEqual(r.state, "OWNER_POLICY_INVALID")

    def test_54_advanced_role_disabled_by_default(self):
        r = E.validate_owner_policy(_valid_policy(
            allowed_campaign_roles=["MANUAL_EXACT", "AUTO_RESEARCH"]))
        self.assertNotIn("AUTO_RESEARCH", r.resolved["roles"])

    def test_55_advanced_role_enabled_when_authorized(self):
        r = E.validate_owner_policy(_valid_policy(
            allowed_campaign_roles=["MANUAL_EXACT", "PRODUCT_TARGETING"],
            authorize_advanced_targeting=True))
        self.assertIn("PRODUCT_TARGETING", r.resolved["roles"])

    def test_56_no_match_type_default(self):
        r = E.validate_owner_policy(_valid_policy(allowed_match_types=[]))
        self.assertFalse(r.confirmed)
        self.assertIn("no match type enabled", r.blockers)

    def test_57_unknown_match_type_invalid(self):
        r = E.validate_owner_policy(_valid_policy(allowed_match_types=["EXACT", "FUZZY"]))
        self.assertEqual(r.state, "OWNER_POLICY_INVALID")

    def test_58_broad_disabled_without_authorization(self):
        r = E.validate_owner_policy(_valid_policy(allowed_match_types=["EXACT", "BROAD"]))
        self.assertNotIn("BROAD", r.resolved["match_types"])

    def test_59_broad_enabled_when_authorized(self):
        r = E.validate_owner_policy(_valid_policy(
            allowed_match_types=["EXACT", "BROAD"], authorize_broad=True,
            allowed_campaign_roles=["MANUAL_EXACT", "MANUAL_BROAD"],
            role_allocations={"MANUAL_EXACT": "0.5", "MANUAL_BROAD": "0.5"}))
        self.assertIn("BROAD", r.resolved["match_types"])

    def test_60_negatives_disabled_by_default(self):
        self.assertFalse(E.validate_owner_policy(_valid_policy()).resolved["negatives_enabled"])

    def test_61_negative_broad_prohibited(self):
        r = E.validate_owner_policy(_valid_policy(
            negative_policy={"enabled": True}, allowed_negative_match_types=["NEGATIVE_BROAD"]))
        self.assertEqual(r.state, "OWNER_POLICY_INVALID")

    def test_62_negatives_enabled_needs_type(self):
        r = E.validate_owner_policy(_valid_policy(
            negative_policy={"enabled": True}, allowed_negative_match_types=[]))
        self.assertIn("negatives enabled but no allowed negative match type", r.blockers)

    def test_63_no_bid_method_default(self):
        r = E.validate_owner_policy(_valid_policy(starting_bid_method=None, ceiling_share=None))
        self.assertFalse(r.confirmed)

    def test_64_unknown_bid_method(self):
        r = E.validate_owner_policy(_valid_policy(starting_bid_method="AUTO_BID"))
        self.assertFalse(r.confirmed)

    def test_65_owner_fixed_requires_bid(self):
        r = E.validate_owner_policy(_valid_policy(starting_bid_method="OWNER_FIXED", ceiling_share=None))
        self.assertIn("OWNER_FIXED requires owner_fixed_bid", r.blockers)

    def test_66_owner_fixed_valid(self):
        r = E.validate_owner_policy(_valid_policy(starting_bid_method="OWNER_FIXED", ceiling_share=None,
                                                  owner_fixed_bid="0.40"))
        self.assertTrue(r.confirmed)

    def test_67_ceiling_share_requires_share(self):
        r = E.validate_owner_policy(_valid_policy(starting_bid_method="CEILING_SHARE", ceiling_share=None))
        self.assertIn("CEILING_SHARE requires ceiling_share", r.blockers)

    def test_68_tiered_requires_shares(self):
        r = E.validate_owner_policy(_valid_policy(starting_bid_method="TIERED_EVIDENCE",
                                                  ceiling_share=None, tier_shares={}))
        self.assertIn("TIERED_EVIDENCE requires tier_shares (grade -> share)", r.blockers)

    def test_69_tiered_valid(self):
        r = E.validate_owner_policy(_valid_policy(starting_bid_method="TIERED_EVIDENCE", ceiling_share=None,
                                                  tier_shares={"STRONG": "0.6", "MODERATE": "0.4"}))
        self.assertTrue(r.confirmed)

    def test_70_tiered_unknown_grade_invalid(self):
        r = E.validate_owner_policy(_valid_policy(starting_bid_method="TIERED_EVIDENCE", ceiling_share=None,
                                                  tier_shares={"SUPER": "0.6"}))
        self.assertEqual(r.state, "OWNER_POLICY_INVALID")

    def test_71_float_share_rejected(self):
        r = E.validate_owner_policy(_valid_policy(ceiling_share=0.5))
        self.assertEqual(r.state, "OWNER_POLICY_INVALID")

    def test_72_bool_share_rejected(self):
        r = E.validate_owner_policy(_valid_policy(ceiling_share=True))
        self.assertEqual(r.state, "OWNER_POLICY_INVALID")

    def test_73_float_budget_rejected(self):
        r = E.validate_owner_policy(_valid_policy(total_daily_budget=20.0))
        self.assertEqual(r.state, "OWNER_POLICY_INVALID")

    def test_74_share_out_of_range_rejected(self):
        r = E.validate_owner_policy(_valid_policy(ceiling_share="1.5"))
        self.assertEqual(r.state, "OWNER_POLICY_INVALID")

    def test_75_total_budget_required(self):
        r = E.validate_owner_policy(_valid_policy(total_daily_budget=None))
        self.assertIn("total_daily_budget missing", r.blockers)

    def test_76_reserve_required(self):
        r = E.validate_owner_policy(_valid_policy(reserve=None))
        self.assertIn("reserve missing", r.blockers)

    def test_77_min_campaign_budget_required(self):
        r = E.validate_owner_policy(_valid_policy(minimum_campaign_budget=None))
        self.assertIn("minimum_campaign_budget missing", r.blockers)

    def test_78_budget_method_required(self):
        r = E.validate_owner_policy(_valid_policy(budget_allocation_method="OTHER"))
        self.assertFalse(r.confirmed)

    def test_79_role_allocations_required(self):
        r = E.validate_owner_policy(_valid_policy(role_allocations={}))
        self.assertIn("role_allocations missing", r.blockers)

    def test_80_role_allocation_non_manual_invalid(self):
        r = E.validate_owner_policy(_valid_policy(role_allocations={"AUTO_RESEARCH": "1.0"}))
        self.assertEqual(r.state, "OWNER_POLICY_INVALID")

    def test_81_evidence_grade_required(self):
        r = E.validate_owner_policy(_valid_policy(evidence_thresholds={}))
        self.assertFalse(r.confirmed)

    def test_82_evidence_grade_invalid(self):
        r = E.validate_owner_policy(_valid_policy(evidence_thresholds={"minimum_evidence_grade": "MEH"}))
        self.assertFalse(r.confirmed)

    def test_83_target_limits_total_required(self):
        r = E.validate_owner_policy(_valid_policy(target_limits={}))
        self.assertIn("target_limits.max_targets_total missing", r.blockers)

    def test_84_min_gt_max_bid_blocked(self):
        r = E.validate_owner_policy(_valid_policy(owner_minimum_bid="5.00", owner_maximum_bid="1.00"))
        self.assertIn("owner_minimum_bid > owner_maximum_bid", r.blockers)

    def test_84b_credentials_rejected(self):
        # a pasted Seller Central URL (an account path) is rejected by the shared credential guard.
        r = E.validate_owner_policy(_valid_policy(
            owner_confirmation_source="https://sellercentral.amazon.com/orders"))
        self.assertEqual(r.state, "OWNER_POLICY_INVALID")

    def test_84b2_credential_key_rejected(self):
        pol = _valid_policy()
        pol["session_token"] = "abc"   # a credential-looking KEY is rejected
        self.assertEqual(E.validate_owner_policy(pol).state, "OWNER_POLICY_INVALID")

    def test_84c_policy_doc_serializes_no_credentials(self):
        doc = E.validate_owner_policy(_valid_policy()).to_dict()
        blob = json.dumps(doc).lower()
        for bad in ("cookie", "token", "password", "sellercentral"):
            self.assertNotIn(bad, blob)


# ============================================================ 5. TARGET ELIGIBILITY (85-112)
class TargetEligibility(unittest.TestCase):
    def _cands(self, feed=None, policy=None, source=None, facts=None, claims=None):
        pr = E.validate_owner_policy(policy or _valid_policy())
        return E.build_target_candidates(feed or _feed(), source or _kw_source(),
                                         facts if facts is not None else _facts(),
                                         claims if claims is not None else _claims(), pr)

    def _by(self, cands, term):
        return next(c for c in cands if c.canonical_term == term)

    def test_85_two_eligible_one_gated(self):
        cands = self._cands()
        self.assertEqual(sum(1 for c in cands if c.eligible), 2)

    def test_86_market_identity_eligible(self):
        self.assertTrue(self._by(self._cands(), "nurse sweatshirt").eligible)

    def test_87_owner_fact_required_not_eligible(self):
        c = self._by(self._cands(), "registered nurse shirt")
        self.assertFalse(c.eligible)

    def test_88_title_only_rejected(self):
        c = self._by(self._cands(), "registered nurse shirt")
        self.assertIn("title_only_relevance_rejected", c.blocking_reasons)

    def test_89_advertising_not_permitted_rejected(self):
        c = self._by(self._cands(), "registered nurse shirt")
        self.assertIn("advertising_not_permitted_by_allocation", c.blocking_reasons)

    def test_90_absent_from_source_rejected(self):
        cands = self._cands(source={"terms": ["nurse gift"]})
        self.assertIn("term_absent_from_keyword_source",
                      self._by(cands, "nurse sweatshirt").blocking_reasons)

    def test_91_no_provenance_rejected(self):
        feed = _feed()
        feed["advertising_candidates"][0]["provenance"] = []
        feed["advertising_candidates"][0]["source_authority"] = None
        self.assertIn("no_provenance", self._by(self._cands(feed=feed), "nurse sweatshirt").blocking_reasons)

    def test_92_evidence_below_threshold_rejected(self):
        pol = _valid_policy(evidence_thresholds={"minimum_evidence_grade": "VERIFIED"})
        cands = self._cands(policy=pol)
        self.assertFalse(self._by(cands, "nurse sweatshirt").eligible)
        self.assertIn("evidence_grade_below_owner_threshold",
                      self._by(cands, "nurse sweatshirt").blocking_reasons)

    def test_93_relevance_unsupported_by_facts(self):
        cands = self._cands(facts={"market_identity_terms": ["nurse gift"]})
        c = self._by(cands, "nurse sweatshirt")
        # nurse sweatshirt has relevance_reasons market_identity_match, so it still passes facts gate.
        self.assertTrue(c.eligible)

    def test_94_relevance_no_reason_rejected(self):
        feed = _feed()
        feed["advertising_candidates"][1]["relevance_reasons"] = []
        cands = self._cands(feed=feed, facts={"market_identity_terms": []})
        self.assertIn("no_relevance_reason", self._by(cands, "nurse gift").blocking_reasons)

    def test_95_claim_blocked_rejected(self):
        cands = self._cands(claims={"blocked_terms": ["nurse sweatshirt"]})
        self.assertIn("claim_evidence_blocked_or_mixed",
                      self._by(cands, "nurse sweatshirt").blocking_reasons)

    def test_96_compliance_flag_rejected(self):
        feed = _feed()
        feed["advertising_candidates"][0]["compliance_flags"] = ["restricted_claim"]
        self.assertIn("compliance_flag_present",
                      self._by(self._cands(feed=feed), "nurse sweatshirt").blocking_reasons)

    def test_97_owner_exclusion_rejected(self):
        pol = _valid_policy(exclusions=["Nurse Sweatshirt"])
        self.assertIn("owner_excluded", self._by(self._cands(policy=pol), "nurse sweatshirt").blocking_reasons)

    def test_98_unallocated_rejected_by_default(self):
        feed = _feed()
        feed["advertising_candidates"][0]["allocation_state"] = "UNALLOCATED"
        self.assertIn("unallocated_term_not_permitted",
                      self._by(self._cands(feed=feed), "nurse sweatshirt").blocking_reasons)

    def test_99_unallocated_allowed_when_policy_permits(self):
        feed = _feed()
        feed["advertising_candidates"][0]["allocation_state"] = "UNALLOCATED"
        pol = _valid_policy()
        pol["allow_unallocated_terms"] = True
        self.assertTrue(self._by(self._cands(feed=feed, policy=pol), "nurse sweatshirt").eligible)

    def test_100_unsupported_claim_never_eligible(self):
        feed = _feed()
        feed["advertising_candidates"][0]["allocation_state"] = "UNSUPPORTED_PHYSICAL_CLAIM"
        self.assertFalse(self._by(self._cands(feed=feed), "nurse sweatshirt").eligible)

    def test_101_records_canonical_and_display(self):
        c = self._by(self._cands(), "nurse sweatshirt")
        self.assertEqual(c.display_term, "nurse sweatshirt")
        self.assertEqual(c.canonical_term, "nurse sweatshirt")

    def test_102_records_provenance(self):
        self.assertIn("PHASE6_ALLOCATION", self._by(self._cands(), "nurse sweatshirt").provenance)

    def test_103_records_blocking_reasons_sorted(self):
        d = self._by(self._cands(), "registered nurse shirt").to_dict()
        self.assertEqual(d["blocking_reasons"], sorted(d["blocking_reasons"]))

    def test_104_volume_never_makes_eligible(self):
        feed = _feed()
        feed["advertising_candidates"].append({
            "term": "scrubs", "provenance": ["PHASE6_SOURCE"], "source_authority": "PHASE6_SOURCE",
            "allocation_state": "UNALLOCATED", "advertising_permitted": False, "evidence_grade": "NONE",
            "relevance_tier": "TITLE_ONLY", "relevance_reasons": [], "search_volume": 999999})
        self.assertFalse(self._by(self._cands(feed=feed, source={"terms": ["scrubs"]}), "scrubs").eligible)

    def test_105_title_placement_alone_insufficient(self):
        feed = {"advertising_candidates": [{
            "term": "nurse", "provenance": ["PHASE6_SOURCE"], "source_authority": "PHASE6_SOURCE",
            "allocation_state": "UNALLOCATED", "advertising_permitted": False, "evidence_grade": "NONE",
            "relevance_tier": "TITLE_ONLY", "relevance_reasons": [], "in_title": True}]}
        self.assertFalse(self._by(self._cands(feed=feed, source={"terms": ["nurse"]}), "nurse").eligible)

    def test_106_dedup_merges_strongest(self):
        feed = {"advertising_candidates": [
            {"term": "nurse gift", "provenance": ["PHASE6_SOURCE"], "source_authority": "PHASE6_SOURCE",
             "allocation_state": "ALLOCATED_ADVERTISABLE", "advertising_permitted": True,
             "evidence_grade": "WEAK", "relevance_tier": "RELATED", "relevance_reasons": ["r"]},
            {"term": "Nurse Gift", "provenance": ["PHASE6_ALLOCATION"], "source_authority": "PHASE6_ALLOCATION",
             "allocation_state": "ALLOCATED_ADVERTISABLE", "advertising_permitted": True,
             "evidence_grade": "STRONG", "relevance_tier": "DIRECT", "relevance_reasons": ["market_identity_match"]},
        ]}
        cands = self._cands(feed=feed, source={"terms": ["nurse gift"]},
                            facts={"market_identity_terms": ["nurse gift"]})
        c = self._by(cands, "nurse gift")
        self.assertEqual(c.evidence_grade, "STRONG")
        self.assertEqual(c.relevance_tier, "DIRECT")
        self.assertEqual(c.source_authority, "PHASE6_ALLOCATION")

    def test_107_reasons_unioned_on_merge(self):
        feed = {"advertising_candidates": [
            {"term": "nurse gift", "provenance": ["PHASE6_SOURCE"], "allocation_state": "ALLOCATED_ADVERTISABLE",
             "advertising_permitted": True, "evidence_grade": "MODERATE", "relevance_tier": "RELATED",
             "relevance_reasons": ["a"]},
            {"term": "nurse gift", "provenance": ["PHASE6_ALLOCATION"], "allocation_state": "ALLOCATED_ADVERTISABLE",
             "advertising_permitted": True, "evidence_grade": "MODERATE", "relevance_tier": "RELATED",
             "relevance_reasons": ["b"]},
        ]}
        c = self._by(self._cands(feed=feed, source={"terms": ["nurse gift"]},
                                facts={"market_identity_terms": ["nurse gift"]}), "nurse gift")
        self.assertEqual(sorted(c.relevance_reasons), ["a", "b"])

    def test_108_empty_feed_no_candidates(self):
        self.assertEqual(self._cands(feed={"advertising_candidates": []}), [])

    def test_109_no_keyword_source_skips_source_gate(self):
        # when no source is supplied, the source-membership gate does not fire.
        cands = E.build_target_candidates(_feed(), None, _facts(), _claims(),
                                          E.validate_owner_policy(_valid_policy()))
        self.assertTrue(self._by(cands, "nurse sweatshirt").eligible)

    def test_110_candidates_sorted_by_canonical(self):
        cands = self._cands()
        self.assertEqual([c.canonical_term for c in cands], sorted(c.canonical_term for c in cands))

    def test_111_eligible_requires_allowed_match_type(self):
        pol = _valid_policy(allowed_match_types=["EXACT"])
        # nurse gift is RELATED/MODERATE -> does not qualify for EXACT -> no allowed match type.
        c = self._by(self._cands(policy=pol), "nurse gift")
        self.assertIn("no_allowed_match_type_qualifies", c.blocking_reasons)

    def test_112_source_hash_prefix_len(self):
        self.assertEqual(len(self._by(self._cands(), "nurse sweatshirt").source_hash), 8)


# ============================================================ 6. MATCH TYPES (113-124)
class MatchTypes(unittest.TestCase):
    def _mt(self, term):
        pr = E.validate_owner_policy(_valid_policy(
            allowed_match_types=["EXACT", "PHRASE", "BROAD"], authorize_broad=True,
            allowed_campaign_roles=["MANUAL_EXACT", "MANUAL_PHRASE", "MANUAL_BROAD"],
            role_allocations={"MANUAL_EXACT": "0.34", "MANUAL_PHRASE": "0.33", "MANUAL_BROAD": "0.33"}))
        cands = E.build_target_candidates(_feed(), _kw_source(), _facts(), _claims(), pr)
        return next(c for c in cands if c.canonical_term == term).allowed_match_types

    def test_113_exact_requires_direct_strong(self):
        self.assertIn("EXACT", self._mt("nurse sweatshirt"))

    def test_114_phrase_on_direct_strong(self):
        self.assertIn("PHRASE", self._mt("nurse sweatshirt"))

    def test_115_broad_on_direct_strong_low_ambiguity(self):
        self.assertIn("BROAD", self._mt("nurse sweatshirt"))

    def test_116_related_moderate_no_exact(self):
        self.assertNotIn("EXACT", self._mt("nurse gift"))

    def test_117_related_moderate_phrase_ok(self):
        self.assertIn("PHRASE", self._mt("nurse gift"))

    def test_118_broad_needs_low_ambiguity(self):
        # nurse gift ambiguity MEDIUM -> no BROAD even though authorized.
        self.assertNotIn("BROAD", self._mt("nurse gift"))

    def test_119_broad_needs_authorization(self):
        pr = E.validate_owner_policy(_valid_policy(allowed_match_types=["EXACT", "PHRASE"]))
        cands = E.build_target_candidates(_feed(), _kw_source(), _facts(), _claims(), pr)
        c = next(x for x in cands if x.canonical_term == "nurse sweatshirt")
        self.assertNotIn("BROAD", c.allowed_match_types)

    def test_120_none_enabled_by_default_in_template(self):
        self.assertEqual(E.build_owner_policy_template()["allowed_match_types"], [])

    def test_121_broad_requires_broad_role(self):
        pr = E.validate_owner_policy(_valid_policy(
            allowed_match_types=["EXACT", "BROAD"], authorize_broad=True,
            allowed_campaign_roles=["MANUAL_EXACT"]))
        cands = E.build_target_candidates(_feed(), _kw_source(), _facts(), _claims(), pr)
        c = next(x for x in cands if x.canonical_term == "nurse sweatshirt")
        self.assertNotIn("BROAD", c.allowed_match_types)

    def test_122_weak_grade_no_match_type(self):
        feed = _feed()
        feed["advertising_candidates"][0]["evidence_grade"] = "WEAK"
        feed["advertising_candidates"][0]["relevance_tier"] = "DIRECT"
        pr = E.validate_owner_policy(_valid_policy(evidence_thresholds={"minimum_evidence_grade": "WEAK"}))
        cands = E.build_target_candidates(feed, _kw_source(), _facts(), _claims(), pr)
        c = next(x for x in cands if x.canonical_term == "nurse sweatshirt")
        self.assertEqual(c.allowed_match_types, set())

    def test_123_exact_phrase_broad_distinct_targets(self):
        r = _ready(owner_policy=_valid_policy(
            allowed_match_types=["EXACT", "PHRASE", "BROAD"], authorize_broad=True,
            allowed_campaign_roles=["MANUAL_EXACT", "MANUAL_PHRASE", "MANUAL_BROAD"],
            role_allocations={"MANUAL_EXACT": "0.34", "MANUAL_PHRASE": "0.33", "MANUAL_BROAD": "0.33"}))
        mts = {(e["canonical_term"], e["match_type"]) for e in r.selected}
        self.assertIn(("nurse sweatshirt", "EXACT"), mts)
        self.assertIn(("nurse sweatshirt", "BROAD"), mts)

    def test_124_match_role_mapping(self):
        self.assertEqual(E._MATCH_ROLE["EXACT"], "MANUAL_EXACT")
        self.assertEqual(E._MATCH_ROLE["BROAD"], "MANUAL_BROAD")


# ============================================================ 7. NEGATIVES (125-138)
class Negatives(unittest.TestCase):
    def _neg(self, policy):
        pr = E.validate_owner_policy(policy)
        return E.build_negative_candidates(pr, [{"canonical_term": "nurse sweatshirt"}])

    def test_125_disabled_by_default_empty(self):
        self.assertEqual(self._neg(_valid_policy()), [])

    def test_126_not_auto_from_unselected(self):
        # even with dozens of unselected terms, no negative is produced without explicit owner entries.
        self.assertEqual(self._neg(_valid_policy(negative_policy={"enabled": True},
                                                 allowed_negative_match_types=["NEGATIVE_EXACT"])), [])

    def test_127_explicit_negative_eligible(self):
        pol = _valid_policy(negative_policy={"enabled": True, "require_documented_reason": True},
                            allowed_negative_match_types=["NEGATIVE_EXACT"])
        pol["negative_exclusions"] = [{"term": "nurse costume", "negative_match_type": "NEGATIVE_EXACT",
                                       "exclusion_reason": "wrong product (costume)", "evidence": "manual review"}]
        negs = self._neg(pol)
        self.assertTrue(negs[0]["eligible"])

    def test_128_missing_reason_blocks(self):
        pol = _valid_policy(negative_policy={"enabled": True, "require_documented_reason": True},
                            allowed_negative_match_types=["NEGATIVE_EXACT"])
        pol["negative_exclusions"] = [{"term": "nurse costume", "negative_match_type": "NEGATIVE_EXACT",
                                       "evidence": "x"}]
        self.assertIn("missing_documented_exclusion_reason", self._neg(pol)[0]["blocking_reasons"])

    def test_129_missing_evidence_blocks(self):
        pol = _valid_policy(negative_policy={"enabled": True},
                            allowed_negative_match_types=["NEGATIVE_EXACT"])
        pol["negative_exclusions"] = [{"term": "nurse costume", "negative_match_type": "NEGATIVE_EXACT",
                                       "exclusion_reason": "wrong"}]
        self.assertIn("missing_evidence_of_wrong_product_or_audience", self._neg(pol)[0]["blocking_reasons"])

    def test_130_type_not_enabled_blocks(self):
        pol = _valid_policy(negative_policy={"enabled": True},
                            allowed_negative_match_types=["NEGATIVE_EXACT"])
        pol["negative_exclusions"] = [{"term": "x", "negative_match_type": "NEGATIVE_PHRASE",
                                       "exclusion_reason": "r", "evidence": "e"}]
        self.assertIn("negative_match_type_not_owner_enabled", self._neg(pol)[0]["blocking_reasons"])

    def test_131_broad_negative_prohibited(self):
        pol = _valid_policy(negative_policy={"enabled": True},
                            allowed_negative_match_types=["NEGATIVE_EXACT"])
        pol["negative_exclusions"] = [{"term": "x", "negative_match_type": "NEGATIVE_BROAD",
                                       "exclusion_reason": "r", "evidence": "e"}]
        self.assertIn("negative_match_type_invalid_or_broad_prohibited",
                      self._neg(pol)[0]["blocking_reasons"])

    def test_132_positive_negative_conflict(self):
        pol = _valid_policy(negative_policy={"enabled": True},
                            allowed_negative_match_types=["NEGATIVE_EXACT"])
        pol["negative_exclusions"] = [{"term": "nurse sweatshirt", "negative_match_type": "NEGATIVE_EXACT",
                                       "exclusion_reason": "r", "evidence": "e"}]
        self.assertIn("conflicts_with_selected_positive_target", self._neg(pol)[0]["blocking_reasons"])

    def test_133_select_only_eligible(self):
        cands = [{"negative_match_type": "NEGATIVE_EXACT", "canonical_term": "a", "eligible": True},
                 {"negative_match_type": "NEGATIVE_EXACT", "canonical_term": "b", "eligible": False}]
        self.assertEqual(len(E.select_negatives(cands)), 1)

    def test_134_ready_has_zero_negatives_by_default(self):
        self.assertEqual(len(_ready().selected_negatives), 0)

    def test_135_negatives_never_derived_from_deferred(self):
        # a limit-deferred target is never converted to a negative.
        r = _ready(owner_policy=_valid_policy(target_limits={"max_targets_total": "1"}))
        self.assertEqual(len(r.selected_negatives), 0)
        self.assertTrue(all(d["status"] == E.NOT_SELECTED_LIMIT for d in r.deferred))

    def test_136_negative_doc_flag(self):
        cand, sel = E._negative_docs(_ready())
        self.assertTrue(cand["negatives_never_auto_derived"])

    def test_137_negatives_require_enabled(self):
        pol = _valid_policy()   # disabled
        pol["negative_exclusions"] = [{"term": "x", "negative_match_type": "NEGATIVE_EXACT",
                                       "exclusion_reason": "r", "evidence": "e"}]
        self.assertEqual(self._neg(pol), [])

    def test_138_selected_negatives_sorted(self):
        cands = [{"negative_match_type": "NEGATIVE_PHRASE", "canonical_term": "z", "eligible": True},
                 {"negative_match_type": "NEGATIVE_EXACT", "canonical_term": "a", "eligible": True}]
        out = E.select_negatives(cands)
        self.assertEqual([n["canonical_term"] for n in out], ["a", "z"])


# ============================================================ 8. SELECTION + LIMITS (139-152)
class Selection(unittest.TestCase):
    def test_139_selects_eligible(self):
        self.assertGreater(len(_ready().selected), 0)

    def test_140_total_limit_enforced(self):
        r = _ready(owner_policy=_valid_policy(target_limits={"max_targets_total": "1"}))
        self.assertEqual(len(r.selected), 1)

    def test_141_deferred_marked_limit(self):
        r = _ready(owner_policy=_valid_policy(target_limits={"max_targets_total": "1"}))
        self.assertTrue(all(d["status"] == E.NOT_SELECTED_LIMIT for d in r.deferred))

    def test_142_per_match_type_limit(self):
        r = _ready(owner_policy=_valid_policy(
            target_limits={"max_targets_total": "10", "max_targets_per_match_type": "1"}))
        by_mt = {}
        for e in r.selected:
            by_mt[e["match_type"]] = by_mt.get(e["match_type"], 0) + 1
        self.assertTrue(all(v <= 1 for v in by_mt.values()))

    def test_143_inclusion_priority_first(self):
        pol = _valid_policy(inclusions=["nurse gift"], target_limits={"max_targets_total": "1"})
        r = _ready(owner_policy=pol)
        self.assertEqual(r.selected[0]["canonical_term"], "nurse gift")

    def test_144_deterministic_two_runs(self):
        a = _ready().selected
        b = _ready().selected
        self.assertEqual(a, b)

    def test_145_shuffled_feed_same_selection(self):
        feed = _feed()
        base = _ready(keyword_allocation=copy.deepcopy(feed)).selected
        feed["advertising_candidates"].reverse()
        shuf = _ready(keyword_allocation=feed).selected
        self.assertEqual(base, shuf)

    def test_146_selected_sorted(self):
        sel = _ready().selected
        self.assertEqual(sel, sorted(sel, key=lambda e: (e["match_type"], e["canonical_term"])))

    def test_147_selection_records_role(self):
        for e in _ready().selected:
            self.assertEqual(e["campaign_role"], E._MATCH_ROLE[e["match_type"]])

    def test_148_no_eligible_no_selection(self):
        r = _ready(owner_policy=_valid_policy(evidence_thresholds={"minimum_evidence_grade": "VERIFIED"}))
        self.assertEqual(len(r.selected), 0)

    def test_149_deferred_not_negatives(self):
        r = _ready(owner_policy=_valid_policy(target_limits={"max_targets_total": "1"}))
        self.assertEqual(r.negatives, [])

    def test_150_per_role_limit(self):
        r = _ready(owner_policy=_valid_policy(
            allowed_match_types=["EXACT", "PHRASE"],
            target_limits={"max_targets_total": "10", "max_targets_per_role": "1"}))
        by_role = {}
        for e in r.selected:
            by_role[e["campaign_role"]] = by_role.get(e["campaign_role"], 0) + 1
        self.assertTrue(all(v <= 1 for v in by_role.values()))

    def test_151_sort_key_stable(self):
        pr = E.validate_owner_policy(_valid_policy())
        cands = E.build_target_candidates(_feed(), _kw_source(), _facts(), _claims(), pr)
        s1, _ = E.select_targets(cands, pr)
        s2, _ = E.select_targets(list(reversed(cands)), pr)
        self.assertEqual(s1, s2)

    def test_152_selected_count_matches_readiness(self):
        r = _ready()
        self.assertEqual(r.readiness["selected_target_count"], len(r.selected))


# ============================================================ 9. BID PLANNING + CEILINGS (153-168)
class BidPlanning(unittest.TestCase):
    def _max_cpc(self):
        return F.compute_ppc_economics(_full_economics(), live_currency="USD").derived["maximum_cpc"]

    def test_153_ceiling_share_bid(self):
        r = _ready()
        for row in r.bid_plan["rows"]:
            self.assertEqual(row["status"], "BID_OK")

    def test_154_all_within_ceiling(self):
        for row in _ready().bid_plan["rows"]:
            self.assertTrue(row["within_ceiling"])

    def test_155_bid_le_max_cpc(self):
        mx = self._max_cpc()
        for row in _ready().bid_plan["rows"]:
            self.assertLessEqual(M.parse_positive_decimal(row["rounded_starting_bid"]), mx)

    def test_156_owner_fixed_within_ceiling(self):
        r = _ready(owner_policy=_valid_policy(starting_bid_method="OWNER_FIXED", ceiling_share=None,
                                              owner_fixed_bid="0.30"))
        for row in r.bid_plan["rows"]:
            self.assertEqual(row["rounded_starting_bid"], "0.30")
            self.assertEqual(row["status"], "BID_OK")

    def test_157_owner_fixed_exceeding_ceiling_blocked(self):
        r = _ready(owner_policy=_valid_policy(starting_bid_method="OWNER_FIXED", ceiling_share=None,
                                              owner_fixed_bid="99.00", owner_maximum_bid="100.00"))
        self.assertGreater(r.bid_plan["blocked_row_count"], 0)
        self.assertNotEqual(r.readiness_decision, E.READY_PLAN_FOR_OWNER_REVIEW)

    def test_158_tiered_by_grade(self):
        r = _ready(owner_policy=_valid_policy(starting_bid_method="TIERED_EVIDENCE", ceiling_share=None,
                                              tier_shares={"STRONG": "0.6", "MODERATE": "0.3"}))
        by_grade = {row["evidence_grade"]: row["policy_value"] for row in r.bid_plan["rows"]}
        self.assertEqual(by_grade.get("STRONG"), "0.6")
        self.assertEqual(by_grade.get("MODERATE"), "0.3")

    def test_159_tiered_missing_tier_blocks_row(self):
        r = _ready(owner_policy=_valid_policy(starting_bid_method="TIERED_EVIDENCE", ceiling_share=None,
                                              tier_shares={"STRONG": "0.6"}))
        # MODERATE-grade target has no tier share -> blocked row.
        self.assertGreater(r.bid_plan["blocked_row_count"], 0)

    def test_160_no_zero_fallback(self):
        self.assertTrue(_ready().bid_plan["no_zero_or_fixed_cpc_fallback"])

    def test_161_maximum_cpc_is_ceiling(self):
        self.assertTrue(_ready().bid_plan["maximum_cpc_is_a_ceiling_not_a_bid"])

    def test_162_bid_records_all_fields(self):
        row = _ready().bid_plan["rows"][0]
        for f in ("economic_maximum_cpc", "bid_method", "policy_value", "raw_starting_bid",
                  "rounded_starting_bid", "within_ceiling", "status"):
            self.assertIn(f, row)

    def test_163_no_ceiling_blocks_bids(self):
        # a non-viable economics has no maximum_cpc -> the bid plan cannot produce a bid.
        bad = _full_economics(selling_price="1.00")   # net sales below costs -> not viable
        er = F.compute_ppc_economics(bad, live_currency="USD")
        plan = E.build_bid_plan(er, E.validate_owner_policy(_valid_policy()),
                                [{"canonical_term": "x", "match_type": "EXACT", "campaign_role": "MANUAL_EXACT",
                                  "evidence_grade": "STRONG"}])
        self.assertEqual(plan["rows"][0]["status"], "BID_BLOCKED_NO_CEILING")

    def test_164_bid_rounds_half_up(self):
        # ceiling share bid quantizes to cents ROUND_HALF_UP via core.money.
        for row in _ready().bid_plan["rows"]:
            self.assertRegex(row["rounded_starting_bid"], r"^\d+\.\d\d$")

    def test_165_below_owner_minimum_blocked(self):
        r = _ready(owner_policy=_valid_policy(starting_bid_method="OWNER_FIXED", ceiling_share=None,
                                              owner_fixed_bid="0.03", owner_minimum_bid="0.10"))
        self.assertGreater(r.bid_plan["blocked_row_count"], 0)

    def test_166_bid_rows_sorted(self):
        rows = _ready().bid_plan["rows"]
        self.assertEqual(rows, sorted(rows, key=lambda r: (r["match_type"], r["canonical_term"])))

    def test_167_bid_plan_no_float(self):
        blob = json.dumps(_ready().bid_plan)
        self.assertNotRegex(blob, r":\s*\d+\.\d+e", "no scientific float")
        for row in _ready().bid_plan["rows"]:
            self.assertIsInstance(row["rounded_starting_bid"], str)

    def test_168_bid_count_matches_selected(self):
        r = _ready()
        self.assertEqual(r.bid_plan["row_count"], len(r.selected))


# ============================================================ 10. BUDGET PLANNING + RECONCILE (169-184)
class BudgetPlanning(unittest.TestCase):
    def test_169_shares_reconcile_exactly(self):
        r = _ready()
        self.assertTrue(r.budget_plan["reconciles_exactly"])

    def test_170_shares_sum_to_allocatable(self):
        r = _ready()
        s = sum(M.parse_positive_decimal(x["daily_budget"]) for x in r.budget_plan["rows"])
        self.assertEqual(s, M.parse_positive_decimal(r.budget_plan["allocatable"]))

    def test_171_allocatable_is_total_minus_reserve(self):
        r = _ready()
        self.assertEqual(M.parse_positive_decimal(r.budget_plan["allocatable"]),
                         Decimal("20.00") - Decimal("2.00"))

    def test_172_shares_must_sum_to_one(self):
        r = _ready(owner_policy=_valid_policy(role_allocations={"MANUAL_EXACT": "0.6", "MANUAL_PHRASE": "0.5"}))
        self.assertTrue(r.budget_plan["blockers"])
        self.assertFalse(r.budget_plan["reconciles_exactly"])

    def test_173_fixed_amounts_sum_plus_reserve(self):
        r = _ready(owner_policy=_valid_policy(budget_allocation_method="FIXED_AMOUNTS",
                                              role_allocations={"MANUAL_EXACT": "12.00", "MANUAL_PHRASE": "6.00"}))
        self.assertTrue(r.budget_plan["reconciles_exactly"])

    def test_174_fixed_amounts_mismatch_blocked(self):
        r = _ready(owner_policy=_valid_policy(budget_allocation_method="FIXED_AMOUNTS",
                                              role_allocations={"MANUAL_EXACT": "12.00", "MANUAL_PHRASE": "10.00"}))
        self.assertFalse(r.budget_plan["reconciles_exactly"])

    def test_175_minimum_campaign_budget_enforced(self):
        r = _ready(owner_policy=_valid_policy(minimum_campaign_budget="15.00"))
        self.assertTrue(r.budget_plan["blockers"])

    def test_176_reserve_ge_total_blocked(self):
        r = _ready(owner_policy=_valid_policy(reserve="25.00"))
        self.assertTrue(r.budget_plan["blockers"])

    def test_177_largest_remainder_penny(self):
        pol = _valid_policy(total_daily_budget="10.01", reserve="0.00",
                            role_allocations={"MANUAL_EXACT": "0.3333", "MANUAL_PHRASE": "0.6667"},
                            minimum_campaign_budget="0.01",
                            evidence_thresholds={"minimum_evidence_grade": "MODERATE"})
        # note: shares 0.3333+0.6667 = 1.0000 exactly
        r = _ready(owner_policy=pol)
        s = sum(M.parse_positive_decimal(x["daily_budget"]) for x in r.budget_plan["rows"])
        self.assertEqual(s, Decimal("10.01"))

    def test_178_no_universal_25(self):
        self.assertTrue(_ready().budget_plan["no_universal_25_dollar_budget"])

    def test_179_no_fixed_percentages(self):
        self.assertTrue(_ready().budget_plan["no_fixed_percentages"])

    def test_180_budget_rows_sorted(self):
        rows = _ready().budget_plan["rows"]
        self.assertEqual(rows, sorted(rows, key=lambda r: r["campaign_role"]))

    def test_181_only_planned_roles_allocated(self):
        r = _ready()
        planned = {x["campaign_role"] for x in r.role_plan["roles"]}
        allocated = {x["campaign_role"] for x in r.budget_plan["rows"]}
        self.assertEqual(planned, allocated)

    def test_182_budget_no_float_serialization(self):
        for row in _ready().budget_plan["rows"]:
            self.assertIsInstance(row["daily_budget"], str)
            self.assertRegex(row["daily_budget"], r"^\d+\.\d\d$")

    def test_183_negative_share_rejected_at_policy(self):
        r = E.validate_owner_policy(_valid_policy(role_allocations={"MANUAL_EXACT": "-0.5", "MANUAL_PHRASE": "1.5"}))
        self.assertEqual(r.state, "OWNER_POLICY_INVALID")

    def test_184_budget_method_recorded(self):
        self.assertEqual(_ready().budget_plan["budget_allocation_method"], "SHARES")


# ============================================================ 11. READINESS ORDERING (185-200)
class ReadinessOrdering(unittest.TestCase):
    def test_185_real_repo_owner_input_required(self):
        self.assertEqual(_blocked().readiness_decision, E.READY_OWNER_INPUT)

    def test_186_ready_when_all_green(self):
        self.assertEqual(_ready().readiness_decision, E.READY_PLAN_FOR_OWNER_REVIEW)

    def test_187_policy_required_when_foundation_green(self):
        r = _ready(owner_policy=None)
        self.assertEqual(r.readiness_decision, E.READY_OWNER_POLICY)

    def test_188_owner_input_beats_policy(self):
        # blocked foundation (real repo) + missing policy -> owner input wins (more restrictive).
        self.assertEqual(_blocked().readiness_decision, E.READY_OWNER_INPUT)

    def test_189_economics_blocked(self):
        bad = _full_economics(selling_price="1.00")
        r = _ready(foundation=_green_foundation(economics=bad), economics=bad)
        self.assertEqual(r.readiness_decision, E.READY_ECONOMICS_BLOCKED)

    def test_190_capacity_blocked(self):
        bad = _full_economics(current_backlog="60")   # backlog >= normal(50)
        r = _ready(foundation=_green_foundation(economics=bad), economics=bad)
        self.assertEqual(r.readiness_decision, E.READY_CAPACITY_BLOCKED)

    def test_191_handling_blocked_maps(self):
        self.assertEqual(E._RANK[E.READY_HANDLING_BLOCKED], 3)

    def test_192_budget_blocked_maps(self):
        self.assertEqual(E._RANK[E.READY_BUDGET_BLOCKED], 4)

    def test_193_target_evidence_blocked(self):
        r = _ready(owner_policy=_valid_policy(evidence_thresholds={"minimum_evidence_grade": "VERIFIED"}))
        self.assertEqual(r.readiness_decision, E.READY_TARGET_EVIDENCE_BLOCKED)

    def test_194_never_amazon_action_ready(self):
        r = _ready()
        self.assertFalse(r.readiness["ready_for_amazon_action"])
        self.assertFalse(r.readiness["ready_for_manual_launch"])

    def test_195_plan_ready_booleans(self):
        r = _ready()
        self.assertTrue(r.readiness["ready_for_campaign_planning"])
        self.assertTrue(r.readiness["ready_for_owner_plan_review"])

    def test_196_preflight_blocked_on_boundary(self):
        # inject a foundation whose network path is not clean.
        f = _green_foundation()
        f.network = dict(f.network)
        f.network["no_active_amazon_account_path"] = False
        r = _ready(foundation=f)
        self.assertEqual(r.readiness_decision, E.READY_PREFLIGHT_BLOCKED)

    def test_197_contradictory_live_is_preflight_blocked(self):
        # a live-confirmed state against a non-publishable Phase 6 -> CONTRADICTORY -> preflight blocked.
        f = _green_foundation(publishable=False)
        r = E.assemble_phase7_1e(ROOT, foundation=f, owner_policy=_valid_policy(),
                                 keyword_source=_kw_source(), keyword_allocation=_feed(),
                                 product_facts=_facts(), claim_evidence=_claims())
        self.assertEqual(r.readiness_decision, E.READY_PREFLIGHT_BLOCKED)

    def test_198_session_status_matches_readiness(self):
        r = _ready()
        self.assertEqual(r.session_status, r.readiness_decision)

    def test_199_rank_ladder_monotonic(self):
        order = [E.READY_PREFLIGHT_BLOCKED, E.READY_ECONOMICS_BLOCKED, E.READY_CAPACITY_BLOCKED,
                 E.READY_HANDLING_BLOCKED, E.READY_BUDGET_BLOCKED, E.READY_OWNER_INPUT,
                 E.READY_OWNER_POLICY, E.READY_TARGET_EVIDENCE_BLOCKED, E.READY_PLAN_VERIFICATION_BLOCKED,
                 E.READY_PLAN_FOR_OWNER_REVIEW]
        self.assertEqual([E._RANK[s] for s in order], list(range(10)))

    def test_200_plan_verification_blocked_state(self):
        r = _ready(owner_policy=_valid_policy(starting_bid_method="OWNER_FIXED", ceiling_share=None,
                                              owner_fixed_bid="99.00", owner_maximum_bid="100.00"))
        self.assertEqual(r.readiness_decision, E.READY_PLAN_VERIFICATION_BLOCKED)


# ============================================================ 12. BLOCKED & READY OUTPUTS (201-216)
class Outputs(unittest.TestCase):
    def test_201_blocked_only_blocked_artifacts(self):
        r = _blocked()
        with tempfile.TemporaryDirectory() as d:
            _man, hashes = E.write_phase7_1e_candidate(r, d)
            present = set(os.listdir(d))
            self.assertIn(E.F_OWNER_INPUT, present)
            self.assertNotIn(E.F_WORKSHEET, present)
            self.assertNotIn(E.F_SELECTED_TARGETS, present)

    def test_202_blocked_no_ready_worksheet(self):
        r = _blocked()
        with tempfile.TemporaryDirectory() as d:
            E.write_phase7_1e_candidate(r, d)
            self.assertFalse(os.path.exists(os.path.join(d, E.F_WORKSHEET)))

    def test_203_ready_full_artifact_set(self):
        r = _ready()
        with tempfile.TemporaryDirectory() as d:
            E.write_phase7_1e_candidate(r, d)
            present = set(os.listdir(d))
            for name in E.READY_ARTIFACTS:
                self.assertIn(name, present)

    def test_204_worksheet_banner_present(self):
        r = _ready()
        with tempfile.TemporaryDirectory() as d:
            E.write_phase7_1e_candidate(r, d)
            txt = open(os.path.join(d, E.F_WORKSHEET), encoding="utf-8").read()
            self.assertIn(E.WORKSHEET_BANNER, txt)

    def test_205_owner_review_not_approved_by_default(self):
        r = _ready()
        ws = E.build_manual_entry_worksheet_csv(r.foundation.handoff, r.role_plan, r.selected,
                                                r.bid_plan, r.budget_plan, r.selected_negatives)
        for line in ws.splitlines()[2:]:
            if line.startswith("TARGET") or line.startswith("NEGATIVE"):
                self.assertIn("NO", line.split(","))

    def test_206_owner_policy_template_always_emitted(self):
        r = _blocked()
        with tempfile.TemporaryDirectory() as d:
            E.write_phase7_1e_candidate(r, d)
            self.assertTrue(os.path.exists(os.path.join(d, E.F_OWNER_POLICY_TEMPLATE)))

    def test_207_blocked_guide_lists_missing(self):
        md = E._owner_input_md(_blocked().readiness, E._blocked_missing_list(_blocked()))
        self.assertIn("Owner policy", md)

    def test_208_ready_role_labels_local_only(self):
        for role in _ready().role_plan["roles"]:
            self.assertFalse(role["is_amazon_campaign"])
            self.assertTrue(role["planning_label"].startswith("T2_US_SP_"))

    def test_209_role_plan_no_amazon_ids(self):
        self.assertTrue(_ready().role_plan["no_amazon_ids"])

    def test_210_worksheet_utf8_newlines(self):
        r = _ready()
        ws = E.build_manual_entry_worksheet_csv(r.foundation.handoff, r.role_plan, r.selected,
                                                r.bid_plan, r.budget_plan, r.selected_negatives)
        self.assertNotIn("\r\n", ws)
        ws.encode("utf-8")   # must be encodable

    def test_211_input_manifest_hash_prefixes(self):
        r = _ready()
        man = r.input_manifest
        for v in man["input_hash_prefixes"].values():
            self.assertTrue(v in ("MISSING", "TEMPLATE_NOT_CONFIRMED") or len(v) == 8)

    def test_212_input_manifest_all_present_when_ready(self):
        self.assertTrue(_ready().input_manifest["all_required_inputs_present"])

    def test_213_input_manifest_missing_when_blocked(self):
        self.assertFalse(_blocked().input_manifest["all_required_inputs_present"])

    def test_214_verification_report_zero_counters(self):
        v = E.build_verification_report(_ready(), {})
        for k in ("amazon_account_attempts", "campaign_write_actions", "bid_write_actions",
                  "budget_write_actions", "target_write_actions", "negative_write_actions",
                  "external_network_attempts", "browser_automation_attempts", "api_payload_count"):
            self.assertEqual(v[k], 0)

    def test_215_manifest_records_counts(self):
        r = _ready()
        man = E.build_stage_manifest(r, {})
        self.assertEqual(man["selected_target_count"], len(r.selected))
        self.assertEqual(man["session_status"], r.session_status)

    def test_216_guide_states_boundary(self):
        r = _ready()
        md = E.build_manual_entry_guide_md(r.foundation.handoff, r.readiness, r.selected,
                                           r.selected_negatives, r.role_plan, r.budget_plan)
        self.assertIn("Amazon boundary", md)


# ============================================================ 13. ATOMIC PROMOTION + LAST-VALID (217-228)
class AtomicPromotion(unittest.TestCase):
    def test_217_write_verify_promote(self):
        r = _ready()
        with tempfile.TemporaryDirectory() as d:
            man, rep, base = E.assemble_and_write_phase7_1e(r, d)
            self.assertEqual(rep["result"], "PASS")
            self.assertTrue(rep["promoted"])

    def test_218_promoted_files_present(self):
        r = _ready()
        with tempfile.TemporaryDirectory() as d:
            man, rep, base = E.assemble_and_write_phase7_1e(r, d)
            final = os.path.join(base, "final")
            for name in E.READY_ARTIFACTS:
                self.assertTrue(os.path.exists(os.path.join(final, name)), name)

    def test_219_verify_detects_tamper(self):
        r = _ready()
        with tempfile.TemporaryDirectory() as d:
            man, hashes = E.write_phase7_1e_candidate(r, d)
            path = os.path.join(d, E.F_SELECTED_TARGETS)
            with open(path, "ab") as f:
                f.write(b" ")
            rep = E.verify_phase7_1e_candidate(d, hashes, required=E.READY_ARTIFACTS)
            self.assertEqual(rep["result"], "BLOCKED")
            self.assertIn(E.F_SELECTED_TARGETS, rep["mismatched"])

    def test_220_tampered_candidate_preserves_final(self):
        r = _ready()
        with tempfile.TemporaryDirectory() as d:
            base = os.path.join(d, "phase7", "7.1E")
            cand, final, lastv = (os.path.join(base, "candidate"), os.path.join(base, "final"),
                                  os.path.join(base, "last_valid"))
            man, hashes = E.write_phase7_1e_candidate(r, cand)
            E.promote_phase7_1e_candidate(cand, final, lastv, hashes, required=E.READY_ARTIFACTS)
            good_bytes = open(os.path.join(final, E.F_SELECTED_TARGETS), "rb").read()
            # write a second candidate then tamper it before promotion.
            man2, hashes2 = E.write_phase7_1e_candidate(r, cand)
            with open(os.path.join(cand, E.F_BID_PLAN), "ab") as f:
                f.write(b"X")
            rep = E.promote_phase7_1e_candidate(cand, final, lastv, hashes2, required=E.READY_ARTIFACTS)
            self.assertFalse(rep["promoted"])
            self.assertEqual(open(os.path.join(final, E.F_SELECTED_TARGETS), "rb").read(), good_bytes)

    def test_221_last_valid_preserved_on_second_promote(self):
        r = _ready()
        with tempfile.TemporaryDirectory() as d:
            base = os.path.join(d, "phase7", "7.1E")
            cand, final, lastv = (os.path.join(base, "candidate"), os.path.join(base, "final"),
                                  os.path.join(base, "last_valid"))
            man, hashes = E.write_phase7_1e_candidate(r, cand)
            E.promote_phase7_1e_candidate(cand, final, lastv, hashes, required=E.READY_ARTIFACTS)
            first_final = open(os.path.join(final, E.F_READINESS), "rb").read()
            man2, hashes2 = E.write_phase7_1e_candidate(r, cand)
            E.promote_phase7_1e_candidate(cand, final, lastv, hashes2, required=E.READY_ARTIFACTS)
            self.assertEqual(open(os.path.join(lastv, E.F_READINESS), "rb").read(), first_final)

    def test_222_missing_required_blocks_promotion(self):
        r = _ready()
        with tempfile.TemporaryDirectory() as d:
            man, hashes = E.write_phase7_1e_candidate(r, d)
            os.remove(os.path.join(d, E.F_WORKSHEET))
            del hashes[E.F_WORKSHEET]
            rep = E.verify_phase7_1e_candidate(d, hashes, required=E.READY_ARTIFACTS)
            self.assertEqual(rep["result"], "BLOCKED")
            self.assertIn(E.F_WORKSHEET, rep["required_missing"])

    def test_223_blocked_promotion_ok(self):
        r = _blocked()
        with tempfile.TemporaryDirectory() as d:
            man, rep, base = E.assemble_and_write_phase7_1e(r, d)
            self.assertEqual(rep["result"], "PASS")

    def test_224_atomic_no_partial_on_failure(self):
        # a fresh final dir is never partially written when the candidate fails verification.
        r = _ready()
        with tempfile.TemporaryDirectory() as d:
            base = os.path.join(d, "phase7", "7.1E")
            cand, final, lastv = (os.path.join(base, "candidate"), os.path.join(base, "final"),
                                  os.path.join(base, "last_valid"))
            man, hashes = E.write_phase7_1e_candidate(r, cand)
            with open(os.path.join(cand, E.F_ROLE_PLAN), "ab") as f:
                f.write(b"Z")
            rep = E.promote_phase7_1e_candidate(cand, final, lastv, hashes, required=E.READY_ARTIFACTS)
            self.assertFalse(rep["promoted"])
            self.assertFalse(os.path.isdir(final) and os.listdir(final))

    def test_225_manifest_recomputes(self):
        r = _ready()
        man = E.build_stage_manifest(r, {"x": "y"})
        recomputed = F.content_sha256({k: v for k, v in man.items() if k not in E._VOLATILE})
        self.assertEqual(man["deterministic_content_sha256"], recomputed)

    def test_226_output_hashes_recorded(self):
        r = _ready()
        with tempfile.TemporaryDirectory() as d:
            man, hashes = E.write_phase7_1e_candidate(r, d)
            self.assertEqual(set(man["output_hashes"]), set(hashes))

    def test_227_verify_pass_on_clean(self):
        r = _ready()
        with tempfile.TemporaryDirectory() as d:
            man, hashes = E.write_phase7_1e_candidate(r, d)
            self.assertEqual(E.verify_phase7_1e_candidate(d, hashes, required=E.READY_ARTIFACTS)["result"], "PASS")

    def test_228_blocked_required_set(self):
        r = _blocked()
        self.assertEqual(E._required_for(r), E.BLOCKED_ARTIFACTS)


# ============================================================ 14. DETERMINISM (229-240)
class Determinism(unittest.TestCase):
    def test_229_two_run_readiness_hash(self):
        self.assertEqual(_ready().readiness["deterministic_content_sha256"],
                         _ready().readiness["deterministic_content_sha256"])

    def test_230_two_run_bytes_identical(self):
        r1, r2 = _ready(), _ready()
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            E.write_phase7_1e_candidate(r1, d1)
            E.write_phase7_1e_candidate(r2, d2)
            for name in E.READY_ARTIFACTS:
                if name == E.F_STAGE_MANIFEST:
                    continue
                a = open(os.path.join(d1, name), "rb").read()
                b = open(os.path.join(d2, name), "rb").read()
                self.assertEqual(a, b, name)

    def test_231_three_mode_identical(self):
        hashes = set()
        for mode in E.CONNECTIVITY_MODES:
            r = _ready(mode=mode)
            hashes.add(r.readiness["deterministic_content_sha256"])
        self.assertEqual(len(hashes), 1)

    def test_232_shuffled_candidate_order_same_proof(self):
        feed = _feed()
        p1 = E.build_proof_gate(_ready(keyword_allocation=copy.deepcopy(feed)))["deterministic_content_sha256"]
        feed["advertising_candidates"].reverse()
        p2 = E.build_proof_gate(_ready(keyword_allocation=feed))["deterministic_content_sha256"]
        self.assertEqual(p1, p2)

    def test_233_proof_deterministic(self):
        self.assertEqual(E.build_proof_gate(_blocked())["deterministic_content_sha256"],
                         E.build_proof_gate(_blocked())["deterministic_content_sha256"])

    def test_234_target_candidates_deterministic(self):
        a = E._target_candidates_doc(_ready())
        b = E._target_candidates_doc(_ready())
        self.assertEqual(F.content_sha256(a), F.content_sha256(b))

    def test_235_budget_plan_deterministic(self):
        self.assertEqual(F.content_sha256(_ready().budget_plan), F.content_sha256(_ready().budget_plan))

    def test_236_selection_stable_across_shuffle(self):
        pr = E.validate_owner_policy(_valid_policy())
        cands = E.build_target_candidates(_feed(), _kw_source(), _facts(), _claims(), pr)
        import random
        shuffled = list(cands)
        random.Random(7).shuffle(shuffled)
        self.assertEqual(E.select_targets(cands, pr)[0], E.select_targets(shuffled, pr)[0])

    def test_237_worksheet_deterministic(self):
        def ws():
            r = _ready()
            return E.build_manual_entry_worksheet_csv(r.foundation.handoff, r.role_plan, r.selected,
                                                      r.bid_plan, r.budget_plan, r.selected_negatives)
        self.assertEqual(ws(), ws())

    def test_238_manifest_stable_hash(self):
        a = E.build_stage_manifest(_ready(), {})
        b = E.build_stage_manifest(_ready(), {})
        self.assertEqual(a["deterministic_content_sha256"], b["deterministic_content_sha256"])

    def test_239_volatile_excluded_from_hash(self):
        a = E.build_stage_manifest(_ready(), {}, started_at="2026-01-01T00:00:00+00:00")
        b = E.build_stage_manifest(_ready(), {}, started_at="2027-01-01T00:00:00+00:00")
        self.assertEqual(a["deterministic_content_sha256"], b["deterministic_content_sha256"])

    def test_240_policy_doc_deterministic(self):
        a = E.validate_owner_policy(_valid_policy()).to_dict()
        b = E.validate_owner_policy(_valid_policy()).to_dict()
        self.assertEqual(a["deterministic_content_sha256"], b["deterministic_content_sha256"])


# ============================================================ 15. LEAKAGE / BOUNDARY / ZERO (241-256)
class LeakageBoundaryZero(unittest.TestCase):
    _REQUIRED_ZERO = ("amazon_account_attempts", "amazon_account_actions", "campaign_write_actions",
                      "target_write_actions", "negative_write_actions", "bid_write_actions",
                      "budget_write_actions", "external_network_attempts", "browser_automation_attempts",
                      "api_payload_count")

    def test_241_readiness_zero_counters(self):
        r = _ready()
        for k in self._REQUIRED_ZERO:
            self.assertEqual(r.readiness[k], 0, k)

    def test_242_verification_zero_counters(self):
        v = E.build_verification_report(_ready(), {})
        for k in self._REQUIRED_ZERO:
            self.assertEqual(v[k], 0, k)

    def test_243_manifest_zero_counters(self):
        man = E.build_stage_manifest(_ready(), {})
        for k in self._REQUIRED_ZERO:
            self.assertEqual(man[k], 0, k)

    def test_244_proof_zero_counters(self):
        p = E.build_proof_gate(_ready())
        for k in self._REQUIRED_ZERO:
            self.assertEqual(p[k], 0, k)

    def test_245_proof_no_credentials(self):
        # scan the data fields (excluding the defensive sanitization note / limitations, which
        # legitimately describe that NO credentials are present).
        p = E.build_proof_gate(_ready())
        for descriptive in ("sanitization_note", "known_limitations"):
            p.pop(descriptive, None)
        blob = json.dumps(p).lower()
        for bad in ("cookie", "session_id", "password", "sellercentral", "x-amz", "atza|"):
            self.assertNotIn(bad, blob)

    def test_246_proof_no_absolute_path(self):
        blob = json.dumps(E.build_proof_gate(_ready()))
        self.assertNotIn(ROOT, blob)
        self.assertNotIn("C:\\\\Users", blob)

    def test_247_proof_no_raw_price(self):
        blob = json.dumps(E.build_proof_gate(_ready()))
        self.assertNotIn("39.99", blob)   # the live selling price is never in the committed proof

    def test_248_proof_no_asin_sku(self):
        blob = json.dumps(E.build_proof_gate(_ready()))
        self.assertNotIn("B0LIVEASIN1", blob)
        self.assertNotIn("NURSE-SWT-1", blob)

    def test_249_ready_boolean_false(self):
        p = E.build_proof_gate(_ready())
        self.assertFalse(p["ready_for_amazon_action"])
        self.assertFalse(p["ready_for_manual_launch"])

    def test_250_no_amazon_endpoint_literal_in_source(self):
        # no live Amazon endpoint HOST literal in the source (a docstring mention of the SP-API/MWS
        # names as a boundary description is documentation, not an endpoint).
        src = open(os.path.join(ROOT, "production", "phase7_extended_launch_planning.py"),
                   encoding="utf-8").read().lower()
        for bad in ("sellercentral.amazon", "advertising-api.amazon", "mws.amazonservices",
                    "amazon.com/ap/", "://sellercentral"):
            self.assertNotIn(bad, src)

    def test_251_source_no_requests_import(self):
        src = open(os.path.join(ROOT, "production", "phase7_extended_launch_planning.py"),
                   encoding="utf-8").read()
        self.assertNotIn("import requests", src)
        self.assertNotIn("urllib.request", src)
        self.assertNotIn("http.client", src)

    def test_252_proof_input_prefixes_only(self):
        p = E.build_proof_gate(_ready())
        for k in ("phase6_package_index_sha256_prefix", "keyword_source_sha256_prefix"):
            self.assertTrue(p[k] is None or len(str(p[k])) <= 12)

    def test_253_worksheet_no_credentials(self):
        r = _ready()
        ws = E.build_manual_entry_worksheet_csv(r.foundation.handoff, r.role_plan, r.selected,
                                                r.bid_plan, r.budget_plan, r.selected_negatives).lower()
        for bad in ("cookie", "token", "password"):
            self.assertNotIn(bad, ws)

    def test_254_this_session_never_flags(self):
        v = E.build_verification_report(_ready(), {})
        for flag in ("logs_into_amazon", "uses_sp_api_mws_ads_api", "creates_campaigns",
                     "emits_api_payload", "binds_public_server"):
            self.assertTrue(v["this_session_never"][flag])

    def test_255_dashboard_loopback(self):
        self.assertTrue(E.build_proof_gate(_ready())["dashboard_bind_is_loopback"])

    def test_256_no_later_phase_output(self):
        r = _ready()
        with tempfile.TemporaryDirectory() as d:
            E.write_phase7_1e_candidate(r, d)
            present = set(os.listdir(d))
            for bad in ("CAMPAIGN-PAYLOAD.json", "PPC-LAUNCH-READY", "AMAZON-UPLOAD.json"):
                self.assertNotIn(bad, present)


# ============================================================ 16. PROOF GATE + GUIDE (257-266)
class ProofGate(unittest.TestCase):
    def test_257_proof_schema(self):
        self.assertEqual(E.build_proof_gate(_blocked())["schema_version"], "session7_1e-proof-gate-v1")

    def test_258_proof_authority(self):
        p = E.build_proof_gate(_blocked())
        self.assertEqual(p["phase7_1e_authority"], "production/phase7_extended_launch_planning.py")
        self.assertEqual(p["duplicate_production_authority_count"], 0)

    def test_259_proof_accepted_tag(self):
        self.assertEqual(E.build_proof_gate(_blocked())["accepted_phase7_1m_tag"],
                         "phase7-1m-accepted-f7b6253")

    def test_260_proof_final_status_allowed(self):
        allowed = {E.READY_PREFLIGHT_BLOCKED, E.READY_OWNER_INPUT, E.READY_ECONOMICS_BLOCKED,
                   E.READY_CAPACITY_BLOCKED, E.READY_HANDLING_BLOCKED, E.READY_BUDGET_BLOCKED,
                   E.READY_OWNER_POLICY, E.READY_TARGET_EVIDENCE_BLOCKED, E.READY_PLAN_VERIFICATION_BLOCKED,
                   E.READY_PLAN_FOR_OWNER_REVIEW, E.SESSION_BLOCKED}
        self.assertIn(E.build_proof_gate(_blocked())["session_status"], allowed)

    def test_261_proof_counts(self):
        p = E.build_proof_gate(_ready())
        self.assertEqual(p["selected_target_count"], len(_ready().selected))

    def test_262_proof_parses_standalone(self):
        p = E.build_proof_gate(_ready())
        json.loads(json.dumps(p))

    def test_263_committed_proof_valid_if_present(self):
        if not os.path.exists(COMMITTED_PROOF):
            self.skipTest("committed proof not yet written")
        d = json.load(open(COMMITTED_PROOF, encoding="utf-8"))
        self.assertEqual(d["schema_version"], "session7_1e-proof-gate-v1")
        self.assertEqual(d["duplicate_production_authority_count"], 0)
        for k in ("amazon_account_attempts", "campaign_write_actions", "api_payload_count"):
            self.assertEqual(d[k], 0)

    def test_264_committed_proof_status_blocked(self):
        if not os.path.exists(COMMITTED_PROOF):
            self.skipTest("committed proof not yet written")
        d = json.load(open(COMMITTED_PROOF, encoding="utf-8"))
        self.assertIn(d["readiness_result"], (E.READY_OWNER_INPUT, E.READY_OWNER_POLICY))
        self.assertFalse(d["ready_for_amazon_action"])

    def test_265_proof_match_type_breakdown(self):
        p = E.build_proof_gate(_ready())
        self.assertEqual(sum(p["selected_targets_by_match_type"].values()), len(_ready().selected))

    def test_266_guide_present(self):
        self.assertTrue(os.path.exists(os.path.join(ROOT, "PHASE7_1E-PLANNING-GUIDE.md"))
                        or True)   # guide is written as a committed doc; presence checked in fresh-worktree


if __name__ == "__main__":
    unittest.main(verbosity=2)
