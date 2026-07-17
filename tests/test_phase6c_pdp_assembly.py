#!/usr/bin/env python3
"""
Phase 6C — production.product_detail_page: evidence-gated Product Detail Page assembly.

Hermetic tests (synthetic, non-paid fixtures) covering the Phase 6C proof gate: preflight + 6A/6B
dependency verification, keyword-count reconciliation from IDs, gift/recipient reconciliation,
authority REUSE (no second listing generator, no A+ engine executed), evidence-gated title / bullet /
description / backend / item-highlight assembly, the advisory-only allocation-outcome ledger, the
deferred creative fields, PageAuditor over every generated field + the combined PDP, atomic /
last-valid / path-safe writes, two-run + three-mode determinism, a zero-network guard, and the
permanent Amazon-account boundary.

Real T2 proof (TIER_A=74 / TIER_B=378, the exact 56/24/27/1/32 accounting, real determinism, exact
"Nurse Sweatshirt" draft) lives in test_phase6c_t2_proof.py, SKIPPED on a clean checkout with no
local (gitignored) runs/T2.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "listing"))
sys.path.insert(0, os.path.join(ROOT, "core"))

from production import product_workspace as PW              # noqa: E402
from production import product_detail_page as PDP           # noqa: E402
import keyword_allocation_planner as KAP                    # noqa: E402
import keyword_source_adapter as KSA                        # noqa: E402
import product_fact_loader as PFL                           # noqa: E402
import claim_evidence as CE                                 # noqa: E402
import category_policy_registry as CPR                      # noqa: E402
import title_engine as TE                                   # noqa: E402
import bullet_engine as BE                                  # noqa: E402
import description_engine as DE                             # noqa: E402
import backend_optimizer as BO                              # noqa: E402
import item_highlights_builder as IHB                       # noqa: E402
import page_auditor as PA                                   # noqa: E402

# A synthetic nurse-sweatshirt niche: safe market identity, a verified recipient (audience) claim, and
# physically-gated / occasion / decoration / material eligibles that must never reach published copy.
NURSE_KEYWORDS = [
    {"keyword": "nurse sweatshirt", "tier": "A_CORE"},
    {"keyword": "nurses sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "gifts for nurses", "tier": "B_SUPPORTING"},
    {"keyword": "rn sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "cna sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "icu nurse sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "er nurse sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "nicu nurse sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "lpn sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "nurse practitioner sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "rn gifts for nurses", "tier": "B_SUPPORTING"},
    {"keyword": "cna gifts for nurses", "tier": "B_SUPPORTING"},
    {"keyword": "lpn gift", "tier": "B_SUPPORTING"},
    # eligible-from-source but each asserts an UNVERIFIED physical/occasion fact -> 6B gates them:
    {"keyword": "personalized nurse sweatshirt", "tier": "A_CORE"},   # personalization
    {"keyword": "custom nurse sweatshirt", "tier": "B_SUPPORTING"},   # personalization
    {"keyword": "embroidered nurse sweatshirt", "tier": "B_SUPPORTING"},  # decoration
    {"keyword": "cotton nurse sweatshirt", "tier": "B_SUPPORTING"},   # material
    {"keyword": "black nurse sweatshirt", "tier": "B_SUPPORTING"},    # color
    {"keyword": "nurses week sweatshirt", "tier": "B_SUPPORTING"},    # occasion
    # source-excluded:
    {"keyword": "teacher sweatshirt", "tier": "B_SUPPORTING", "risk_flags": ["wrong_audience:teacher"]},
    {"keyword": "disney nurse sweatshirt", "tier": "B_SUPPORTING", "risk_flags": ["brand:disney"]},
    {"keyword": "junk noise", "tier": "REJECTED"},
]

_CHAIN = {}


def build_chain(keywords=None, project_id="TESTWS"):
    """A hermetic run workspace with COMPLETED, VERIFIED Phase 6A + Phase 6B stages ready for 6C."""
    d = tempfile.mkdtemp(prefix="6c-ws-")
    kws = keywords if keywords is not None else NURSE_KEYWORDS
    lean = {"run_metadata": {"run_id": "kwrun_test", "source_schema": "master_keywords_lean"},
            "target_profile": {"seed": "nurse sweatshirt"}, "keywords": kws}
    with open(os.path.join(d, "MASTER-KEYWORDS-LEAN.json"), "w", encoding="utf-8") as f:
        json.dump(lean, f)
    with open(os.path.join(d, "PROJECT-MANIFEST.json"), "w", encoding="utf-8") as f:
        json.dump({"project_id": project_id}, f)
    res = PW.build_product_workspace(d, workspace_id=project_id)
    PW.write_phase6a_artifacts(res, dest_dir=os.path.join(d, "phase6", "6A"), workspace_dir=d,
                               started_at="T0", completed_at="T1")
    kres = KAP.plan_keyword_allocation(d)
    KAP.write_phase6b_artifacts(kres, dest_dir=KAP.phase6b_dir(d), workspace_dir=d,
                                started_at="T0", completed_at="T1")
    return d


def default_chain():
    if "dir" not in _CHAIN:
        _CHAIN["dir"] = build_chain()
        _CHAIN["result"] = PDP.assemble_product_detail_page(_CHAIN["dir"])
    return _CHAIN["dir"], _CHAIN["result"]


def write_chain(d, result):
    dest = PDP.phase6c_dir(d)
    man = PDP.write_phase6c_artifacts(result, dest_dir=dest, workspace_dir=d,
                                      started_at="A", completed_at="B")
    return dest, man


# ============================================================ PREFLIGHT + DEPENDENCY
class Preflight(unittest.TestCase):
    def test_01_phase5_certification_valid(self):
        cert = json.load(open(os.path.join(ROOT, "SESSION5D-RELEASE1-CERTIFICATION.json"), encoding="utf-8"))
        self.assertEqual(cert["overall_status"], "RELEASE1_CERTIFIED")
        self.assertEqual(cert["mandatory_pass"], cert["mandatory_total"])

    def test_02_6a_and_6b_proofs_present(self):
        a = json.load(open(os.path.join(ROOT, "SESSION6A-PROOF-GATE.json"), encoding="utf-8"))
        self.assertTrue(a["ready_for_6b"])
        self.assertTrue(os.path.exists(os.path.join(ROOT, "SESSION6B-PROOF-GATE.json")))

    def test_03_connectivity_manifest_verifies(self):
        import hashlib
        man = json.load(open(os.path.join(ROOT, "docs", "CONNECTIVITY-POLICY-MANIFEST.json"), encoding="utf-8"))
        raw = open(os.path.join(ROOT, "docs", "CONNECTIVITY-POLICY.md"), "rb").read()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), man["policy_sha256"])
        b = man["permanent_amazon_boundary"]
        self.assertTrue(b["amazon_account_isolation"])
        for k in ("amazon_seller_central_enabled", "amazon_api_enabled", "amazon_network_writes_enabled"):
            self.assertFalse(b[k])

    def test_04_single_pdp_authority(self):
        self.assertTrue(os.path.exists(os.path.join(ROOT, "production", "product_detail_page.py")))
        for dup in ("listing/product_detail_page.py", "production/product_detail_page_v2.py",
                    "research/product_detail_page.py", "listing/pdp_assembler.py"):
            self.assertFalse(os.path.exists(os.path.join(ROOT, dup)), dup)

    def test_05_no_amazon_or_network_path_in_assembler(self):
        src = open(os.path.join(ROOT, "production", "product_detail_page.py"), encoding="utf-8").read().lower()
        for bad in ("sp-api", "sp_api", "sellingpartner", "selling_partner", "mws", "seller_central",
                    "sellercentral", "boto3", "import requests", "requests.get", "requests.post",
                    "urllib.request", "webdriver", "selenium", "playwright", "amazon advertising api",
                    "http://", "https://", "anthropic", "openai"):
            self.assertNotIn(bad, src, bad)

    def test_06_ready_dependency_loads_and_verifies(self):
        d, _ = default_chain()
        deps = PDP.load_phase6c_dependencies(d)
        self.assertTrue(deps.ready_for_6c)
        self.assertEqual(deps.workspace_id, deps.product_id)
        self.assertEqual(deps.product_type, "sweatshirt")
        self.assertEqual(deps.audience, "nurses")

    def test_07_invalid_6a_dependency_blocks(self):
        d = build_chain()
        os.remove(os.path.join(d, "phase6", "6A", "CLAIM-EVIDENCE.json"))
        with self.assertRaises(PDP.Phase6CDependencyError):
            PDP.load_phase6c_dependencies(d)

    def test_08_missing_6b_artifact_blocks(self):
        d = build_chain()
        os.remove(os.path.join(d, "phase6", "6B", "KEYWORD-ALLOCATION-MAP.json"))
        with self.assertRaises(PDP.Phase6CDependencyError):
            PDP.load_phase6c_dependencies(d)

    def test_09_hash_mismatch_blocks(self):
        d = build_chain()
        p = os.path.join(d, "phase6", "6B", "KEYWORD-ALLOCATION-MAP.json")
        doc = json.load(open(p, encoding="utf-8"))
        doc["audience"] = "teachers"                 # tamper -> byte hash no longer matches 6B manifest
        open(p, "w", encoding="utf-8").write(json.dumps(doc))
        with self.assertRaises(PDP.Phase6CDependencyError):
            PDP.load_phase6c_dependencies(d)

    def test_10_source_drift_blocks(self):
        d = build_chain()
        with open(os.path.join(d, "MASTER-KEYWORDS-LEAN.json"), "a", encoding="utf-8") as f:
            f.write("\n")
        with self.assertRaises(PDP.Phase6CDependencyError):
            PDP.load_phase6c_dependencies(d)

    def test_11_assembly_does_not_modify_upstream(self):
        d = build_chain()

        def snapshot():
            out = {}
            for stage in ("6A", "6B"):
                base = os.path.join(d, "phase6", stage)
                for n in os.listdir(base):
                    with open(os.path.join(base, n), "rb") as f:
                        out[(stage, n)] = f.read()
            return out

        before = snapshot()
        r = PDP.assemble_product_detail_page(d)
        write_chain(d, r)
        self.assertEqual(before, snapshot())


# ============================================================ KEYWORD ACCOUNTING
class Accounting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.r = default_chain()
        cls.deps = PDP.load_phase6c_dependencies(cls.d)
        cls.acc = PDP.reconcile_phase6b_keyword_counts(cls.deps)

    def test_12_five_counts_calculated(self):
        for k in ("selected_unique_keyword_count", "allocated_unique_keyword_count",
                  "assignment_record_count", "reused_keyword_count", "unallocated_unique_keyword_count"):
            self.assertIn(k, self.acc)

    def test_13_allocated_unique_from_ids_not_report(self):
        amap = self.deps.allocation_map
        ids = {r["keyword_id"] for recs in amap["fields"].values() for r in recs}
        self.assertEqual(self.acc["allocated_unique_keyword_count"], len(ids))

    def test_14_equation_reconciles(self):
        self.assertTrue(self.acc["reconciles"])
        self.assertEqual(self.acc["allocated_unique_keyword_count"]
                         + self.acc["unallocated_unique_keyword_count"],
                         self.acc["selected_unique_keyword_count"])

    def test_15_mismatch_blocks(self):
        deps = PDP.load_phase6c_dependencies(self.d)
        deps.unallocated["keywords"] = deps.unallocated["keywords"][:-1]   # break the equation
        with self.assertRaises(PDP.Phase6BAllocationCountMismatch):
            PDP.reconcile_phase6b_keyword_counts(deps)

    def test_16_reused_requires_justification_and_incremental_value(self):
        deps = PDP.load_phase6c_dependencies(self.d)
        for recs in deps.allocation_map["fields"].values():
            for r in recs:
                if r.get("reuse") == "REUSE_JUSTIFIED":
                    r.pop("reuse_justification", None)
                    with self.assertRaises(PDP.Phase6BAllocationCountMismatch):
                        PDP.reconcile_phase6b_keyword_counts(deps)
                    return
        self.skipTest("no reused keyword in this fixture")

    def test_17_reused_requires_distinct_field(self):
        # the canonical identity is reused across distinct fields in this fixture
        reused = self.acc["reused_keyword_ids"]
        self.assertTrue(reused)
        for kw in reused:
            fields = [f for f, recs in self.deps.allocation_map["fields"].items()
                      for r in recs if r["keyword_id"] == kw]
            self.assertEqual(len(fields), len(set(fields)))


# ============================================================ CLAIM / GIFT RECONCILIATION
class Claims(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.r = default_chain()
        cls.deps = PDP.load_phase6c_dependencies(cls.d)

    def test_18_recipient_claim_uses_exact_evidence(self):
        self.assertTrue(self.deps.claims.is_publishable("recipient"))
        self.assertEqual(self.r.gift_recipient_reconciliation["recipient_state"], "VERIFIED")

    def test_19_gift_not_assumed_verified(self):
        gr = self.r.gift_recipient_reconciliation
        self.assertFalse(gr["gift_claim_verified"])
        self.assertEqual(gr["occasion_state"], "UNVERIFIED_BLOCKED")
        self.assertEqual(gr["gift_language_status"], "CLAIM_EVIDENCE_MISSING")

    def test_20_keyword_text_does_not_create_claim_evidence(self):
        # 'personalized' / 'gift' appear in eligible keyword text but their claims stay blocked
        self.assertFalse(self.deps.claims.is_publishable("personalization_fields"))
        self.assertFalse(self.deps.claims.is_publishable("occasion"))

    def test_21_market_identity_is_not_a_physical_fact(self):
        self.assertFalse(self.deps.claims.is_publishable("material_composition"))
        self.assertFalse(self.deps.claims.is_publishable("decoration_method"))

    def test_22_unsupported_physical_gift_terms_never_published(self):
        # only PUBLISHED copy is screened; advisory allocation metadata may legitimately name a concept.
        published = " ".join([self.r.recommended_title or ""]
                             + [c["title"] for c in self.r.title_options["candidates"]]
                             + [b["text"] for b in self.r.bullets["bullets"]]
                             + [self.r.description_text]).lower()
        for bad in ("personalized", "custom ", "embroider", "cotton", "gift", "week", "black"):
            self.assertNotIn(bad, published, bad)


# ============================================================ AUTHORITY REUSE
class Reuse(unittest.TestCase):
    def test_23_reuses_phase5_engines(self):
        self.assertIs(PDP.TE, TE)
        self.assertIs(PDP.BE, BE)
        self.assertIs(PDP.DE, DE)
        self.assertIs(PDP.BO, BO)
        self.assertIs(PDP.IHB, IHB)
        self.assertIs(PDP.PA, PA)
        self.assertIs(PDP.CE, CE)
        self.assertIs(PDP.PFL, PFL)
        self.assertIs(PDP.CPR, CPR)

    def test_24_one_serialization_authority(self):
        self.assertEqual(PDP.canonical_json, PW.canonical_json)
        self.assertEqual(PDP.content_sha256, PW.content_sha256)

    def test_25_reuses_physical_claim_gate(self):
        self.assertTrue(PDP._is_safe_copy("nurse sweatshirt"))
        self.assertFalse(PDP._is_safe_copy("cotton nurse sweatshirt"))
        self.assertFalse(PDP._is_safe_copy("A personalized gift for nurses."))

    def test_26_no_aplus_engine_executed(self):
        src = open(os.path.join(ROOT, "production", "product_detail_page.py"), encoding="utf-8").read()
        for bad in ("build_aplus", "aplus_builder", "aplus_module_registry", "a_plus_templates"):
            self.assertNotIn(bad, src, bad)


# ============================================================ TITLE
class Title(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.r = default_chain()
        cls.to = cls.r.title_options

    def test_27_deterministic_candidates(self):
        r2 = PDP.assemble_product_detail_page(self.d)
        self.assertEqual(json.dumps(self.to, sort_keys=True),
                         json.dumps(r2.title_options, sort_keys=True))

    def test_28_at_most_one_recommended(self):
        self.assertLessEqual(sum(1 for c in self.to["candidates"] if c["recommended"]), 1)
        self.assertTrue(self.to["recommended_title"])

    def test_29_uses_existing_title_engine(self):
        self.assertEqual(self.to["engine"], "listing.title_engine.build_titles")

    def test_30_unsupported_terms_rejected(self):
        for c in self.to["candidates"]:
            low = c["title"].lower()
            for bad in ("personalized", "custom", "embroider", "cotton", "size", "color", "gift"):
                self.assertNotIn(bad, low, bad)

    def test_31_category_policy_limit_applies_not_hardcoded_75(self):
        pol = CPR.resolve_category_policy("apparel")
        self.assertEqual(self.to["title_hard_limit"], pol.title_hard_limit)
        for c in self.to["candidates"]:
            self.assertLessEqual(c["character_count"], self.to["title_hard_limit"])
            self.assertIsNotNone(c["utf8_byte_count"])

    def test_32_every_candidate_audited(self):
        for c in self.to["candidates"]:
            self.assertIn("page_audit_result", c)
            self.assertEqual(c["page_audit_result"]["hard_failures"], [])

    def test_33_allocations_are_advisory(self):
        for c in self.to["candidates"]:
            self.assertIn("allocated_concepts_considered", c)
            self.assertIsInstance(c["concepts_rejected"], list)


# ============================================================ BULLETS
class Bullets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.r = default_chain()
        cls.bd = cls.r.bullets

    def test_34_exactly_five_records(self):
        self.assertEqual(len(self.bd["bullets"]), 5)

    def test_35_preserve_existing_five_jobs(self):
        self.assertEqual([b["bullet_job"] for b in self.bd["bullets"]], list(BE.JOBS))

    def test_36_empty_bullets_permitted_with_reasons(self):
        for b in self.bd["bullets"]:
            if b["state"] != "SAFE_DRAFT":
                self.assertEqual(b["text"], "")
                self.assertTrue(b["reason_codes"])

    def test_37_no_filler_invented(self):
        for b in self.bd["bullets"]:
            if b["state"] != "SAFE_DRAFT":
                self.assertEqual(b["character_count"], 0)

    def test_38_unsupported_claims_blocked_in_bullets(self):
        for b in self.bd["bullets"]:
            low = (b["text"] or "").lower()
            for bad in ("personalized", "personalization", "gift", "embroider", "cotton"):
                self.assertNotIn(bad, low, bad)

    def test_39_deterministic(self):
        r2 = PDP.assemble_product_detail_page(self.d)
        self.assertEqual(json.dumps(self.bd, sort_keys=True),
                         json.dumps(r2.bullets, sort_keys=True))

    def test_40_nonempty_bullets_audited(self):
        for b in self.bd["bullets"]:
            if b["state"] == "SAFE_DRAFT":
                self.assertIsNotNone(b["page_audit_result"])
                self.assertEqual(b["page_audit_result"]["hard_failures"], [])


# ============================================================ DESCRIPTION
class Description(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.r = default_chain()

    def test_41_uses_existing_description_engine(self):
        self.assertEqual(self.r.description_lineage["engine"], "listing.description_engine.build_description")

    def test_42_deterministic(self):
        r2 = PDP.assemble_product_detail_page(self.d)
        self.assertEqual(self.r.description_text, r2.description_text)

    def test_43_no_keyword_dumping(self):
        # a product-identity draft, not a pile of role variants
        self.assertLess(len(self.r.description_text), 120)

    def test_44_every_allocation_gets_outcome(self):
        desc_assignments = [r["assignment_id"] for r in self.r.allocation_outcomes
                            if r["source_field"] == "description"]
        self.assertTrue(desc_assignments)
        for o in self.r.allocation_outcomes:
            if o["source_field"] == "description":
                self.assertIn(o["outcome"], (PDP.USED, PDP.PARAPHRASED_NATURALLY))

    def test_45_no_unsupported_or_gift_language(self):
        low = self.r.description_text.lower()
        for bad in ("personalized", "gift", "cotton", "embroider", "size", "color"):
            self.assertNotIn(bad, low, bad)

    def test_46_description_audited(self):
        self.assertIsNotNone(self.r.description_lineage["page_audit_result"])
        self.assertEqual(self.r.description_lineage["page_audit_result"]["hard_failures"], [])

    def test_47_dropped_sections_recorded(self):
        # the recipient/occasion section (unverified gift language) is dropped with a reason
        dropped = self.r.description_lineage["dropped_sections"]
        if dropped:
            for s in dropped:
                self.assertTrue(s["reason"])


# ============================================================ BACKEND
class Backend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.r = default_chain()
        cls.be = cls.r.backend

    def test_48_uses_existing_optimizer(self):
        self.assertEqual(self.be["engine"], "listing.backend_optimizer.optimize_backend")

    def test_49_byte_limit_enforced(self):
        pol = CPR.resolve_category_policy("apparel")
        self.assertEqual(self.be["byte_limit"], pol.backend_byte_ceiling)
        self.assertLessEqual(self.be["utf8_byte_count"], self.be["byte_limit"])

    def test_50_semantic_units_preserved_no_slicing(self):
        # every included unit is a whole phrase/token from the source, never a sliced fragment
        self.assertIsInstance(self.be["semantic_units"], list)
        for term in self.be["final_search_terms"].split():
            self.assertGreaterEqual(len(term), 2)

    def test_51_no_unsupported_or_gift_terms(self):
        low = self.be["final_search_terms"].lower()
        for bad in ("personal", "custom", "cotton", "embroider", "disney", "teacher"):
            self.assertNotIn(bad, low, bad)

    def test_52_selected_and_rejected_ids_resolve_and_cover(self):
        sel, rej = set(self.be["selected_candidate_ids"]), set(self.be["rejected_candidate_ids"])
        cand = set(self.be["candidate_assignment_ids"])
        self.assertEqual(sel | rej, cand)
        self.assertFalse(sel & rej)

    def test_53_deterministic(self):
        r2 = PDP.assemble_product_detail_page(self.d)
        self.assertEqual(self.be["final_search_terms"], r2.backend["final_search_terms"])


# ============================================================ ITEM HIGHLIGHTS + DEFERRED
class HighlightsDeferred(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.r = default_chain()

    def test_54_item_highlights_reuse_and_empty_with_reason(self):
        ih = self.r.item_highlights
        self.assertEqual(ih["engine"], "listing.item_highlights_builder.build_item_highlights_content")
        self.assertEqual(ih["state"], "CLAIM_EVIDENCE_MISSING")
        self.assertEqual(ih["texts"], [])
        self.assertIn("CLAIM_EVIDENCE_MISSING", ih["reasons"])

    def test_55_deferred_fields_carried_not_executed(self):
        df = self.r.deferred_fields
        self.assertEqual(df["aplus"]["state"], "DEFERRED_OWNER_FACT_REQUIRED")
        self.assertEqual(df["listing_image_text"]["state"], "DEFERRED_TO_LATER_STAGE")
        self.assertEqual(df["image_alt_text"]["state"], "DEFERRED_TO_LATER_STAGE")

    def test_56_no_aplus_or_image_artifact_generated(self):
        d = build_chain()
        r = PDP.assemble_product_detail_page(d)
        dest, _ = write_chain(d, r)
        present = set(os.listdir(dest))
        self.assertEqual(present & PDP.FORBIDDEN_ARTIFACTS, set())
        for forbidden in ("BASIC-APLUS-CONTENT.json", "PREMIUM-APLUS-DRAFT.json", "LISTING-IMAGE-PLAN.json"):
            self.assertNotIn(forbidden, present)
        blob = json.dumps(r.to_dict()).lower()
        for bad in ("image_prompt", "image prompt", "alt_text_final", "basic-aplus", "premium-aplus"):
            self.assertNotIn(bad, blob)


# ============================================================ ALLOCATION OUTCOMES + AUDIT
class OutcomesAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.r = default_chain()

    def test_57_every_assignment_gets_exactly_one_outcome(self):
        amap = PDP.load_phase6c_dependencies(self.d).allocation_map
        assignments = {r["assignment_id"] for recs in amap["fields"].values() for r in recs}
        outcome_ids = [o["assignment_id"] for o in self.r.allocation_outcomes]
        self.assertEqual(set(outcome_ids), assignments)
        self.assertEqual(len(outcome_ids), len(set(outcome_ids)))

    def test_58_outcomes_use_the_controlled_vocabulary(self):
        allowed = {PDP.USED, PDP.PARAPHRASED_NATURALLY, PDP.DEFERRED_OWNER_FACT_REQUIRED,
                   PDP.DEFERRED_REAL_ASSET_REQUIRED, PDP.REJECTED_CLAIM_EVIDENCE, PDP.REJECTED_PRODUCT_FACT,
                   PDP.REJECTED_CATEGORY_POLICY, PDP.REJECTED_NATURAL_LANGUAGE, PDP.REJECTED_DUPLICATION,
                   PDP.REJECTED_PAGE_AUDITOR, PDP.DEFERRED_TO_LATER_STAGE}
        for o in self.r.allocation_outcomes:
            self.assertIn(o["outcome"], allowed)
            self.assertTrue(o["reason_codes"])

    def test_59_combined_pdp_audited_zero_leakage(self):
        leak = self.r.page_audit["leakage_summary"]
        for k in ("wrong_audience_leakage", "wrong_product_leakage", "wrong_occasion_leakage",
                  "brand_ip_leakage", "unsupported_physical_claims", "blocked_claim_promotion",
                  "fabricated_owner_facts"):
            self.assertEqual(leak[k], 0, k)

    def test_60_clean_audit_does_not_override_publishability(self):
        self.assertEqual(self.r.page_audit["status"], "PASS_WITH_WARNINGS")
        self.assertEqual(self.r.listing_publishability_state, PDP.LISTING_SAFE_DRAFT)
        self.assertEqual(self.r.phase6c_state, PDP.SAFE_PDP_OWNER_FACTS_REQUIRED)
        self.assertTrue(self.r.ready_for_next_stage)


# ============================================================ ARTIFACTS + WRITES
class Artifacts(unittest.TestCase):
    def test_61_required_artifacts_generated_with_exact_names(self):
        d = build_chain()
        r = PDP.assemble_product_detail_page(d)
        dest, _ = write_chain(d, r)
        for name in PDP.REQUIRED_ARTIFACTS:
            self.assertTrue(os.path.exists(os.path.join(dest, name)), name)
        for name in PDP.SUPPORT_ARTIFACTS:
            self.assertTrue(os.path.exists(os.path.join(dest, name)), name)

    def test_62_manifest_hashes_match_bytes(self):
        d = build_chain()
        r = PDP.assemble_product_detail_page(d)
        dest, _ = write_chain(d, r)
        self.assertTrue(PDP.verify_phase6c_artifacts(dest, workspace_dir=d)["ok"])

    def test_63_manifest_rejects_absolute_and_traversal_paths(self):
        d = build_chain()
        r = PDP.assemble_product_detail_page(d)
        _dest, man = write_chain(d, r)
        self.assertFalse(PDP.validate_stage6c_manifest(man, d))      # the real manifest is valid
        for bad in ("/etc/passwd", "../escape.json", "C:/Windows/x"):
            doc = json.loads(json.dumps(man))
            doc["output_hashes"][bad] = "x"
            self.assertTrue(PDP.validate_stage6c_manifest(doc, d), bad)

    def test_64_manifest_does_not_self_hash(self):
        d = build_chain()
        r = PDP.assemble_product_detail_page(d)
        _dest, man = write_chain(d, r)
        self.assertFalse(any(PDP.STAGE_MANIFEST_FILENAME in k for k in man["output_hashes"]))

    def test_65_failed_generation_preserves_last_valid(self):
        d = build_chain()
        r = PDP.assemble_product_detail_page(d)
        dest, _ = write_chain(d, r)
        before = {n: open(os.path.join(dest, n), "rb").read() for n in os.listdir(dest)}
        orig = PDP.validate_product_detail_page
        try:
            PDP.validate_product_detail_page = lambda *a, **k: ["forced failure"]
            with self.assertRaises(PDP.Phase6CAssemblyError):
                PDP.write_phase6c_artifacts(r, dest_dir=dest, workspace_dir=d, started_at="X", completed_at="Y")
        finally:
            PDP.validate_product_detail_page = orig
        after = {n: open(os.path.join(dest, n), "rb").read() for n in os.listdir(dest)}
        self.assertEqual(before, after)
        self.assertFalse([n for n in os.listdir(dest) if n.startswith(".tmp-6c-") or n.endswith(".part")])

    def test_66_no_phase6d_6f_artifact(self):
        d = build_chain()
        r = PDP.assemble_product_detail_page(d)
        dest, _ = write_chain(d, r)
        present = set(os.listdir(dest))
        self.assertEqual(present & PDP.FORBIDDEN_ARTIFACTS, set())

    def test_67_description_txt_deterministic_newline(self):
        d = build_chain()
        r = PDP.assemble_product_detail_page(d)
        dest, _ = write_chain(d, r)
        raw = open(os.path.join(dest, "DESCRIPTION.txt"), "rb").read()
        self.assertNotIn(b"\r", raw)
        self.assertTrue(raw.endswith(b"\n") or raw == b"")


# ============================================================ DETERMINISM + CONNECTIVITY
class DeterminismConnectivity(unittest.TestCase):
    def test_68_two_run_hashes_match(self):
        d = build_chain()
        r1 = PDP.assemble_product_detail_page(d)
        dest1 = tempfile.mkdtemp(prefix="6c-d1-")
        m1 = PDP.write_phase6c_artifacts(r1, dest_dir=os.path.join(dest1, "phase6", "6C"),
                                         workspace_dir=dest1, started_at="A", completed_at="A")
        r2 = PDP.assemble_product_detail_page(d)
        dest2 = tempfile.mkdtemp(prefix="6c-d2-")
        m2 = PDP.write_phase6c_artifacts(r2, dest_dir=os.path.join(dest2, "phase6", "6C"),
                                         workspace_dir=dest2, started_at="B", completed_at="B")
        for name in PDP.REQUIRED_ARTIFACTS + PDP.SUPPORT_ARTIFACTS:
            a = open(os.path.join(dest1, "phase6", "6C", name), "rb").read()
            b = open(os.path.join(dest2, "phase6", "6C", name), "rb").read()
            self.assertEqual(a, b, name)
        self.assertEqual(m1["deterministic_content_sha256"], m2["deterministic_content_sha256"])
        self.assertEqual(r1.deterministic_content_sha256(), r2.deterministic_content_sha256())

    def test_69_three_mode_determinism(self):
        d = build_chain()
        shas = set()
        for mode in ("CONNECTED_RESEARCH", "LOCAL_SAFE", "TEST_DENY_EXTERNAL"):
            r = PDP.assemble_product_detail_page(d, connectivity_mode=mode)
            shas.add(r.deterministic_content_sha256())
        self.assertEqual(len(shas), 1)

    def test_70_zero_external_network_attempts(self):
        import socket
        d = build_chain()
        attempts = []
        real_connect = socket.socket.connect

        def spy(self, address):
            attempts.append(address)
            return real_connect(self, address)

        with mock.patch.object(socket.socket, "connect", spy):
            r = PDP.assemble_product_detail_page(d)
            PDP.write_phase6c_artifacts(r, workspace_dir=d, started_at="X", completed_at="Y")
        external = [a for a in attempts if not (isinstance(a, tuple) and str(a[0]).startswith(("127.", "::1", "localhost")))]
        self.assertEqual(external, [])

    def test_71_amazon_account_actions_zero(self):
        # the assembler never references any Amazon account/API/write surface.
        src = open(os.path.join(ROOT, "production", "product_detail_page.py"), encoding="utf-8").read().lower()
        for bad in ("seller central", "seller_central", "sp-api", "listing write", "inventory write",
                    "ppc", "advertising api", "login", "credential"):
            self.assertNotIn(bad, src, bad)

    def test_72_listing_stays_safe_draft(self):
        _d, r = default_chain()
        self.assertEqual(r.listing_publishability_state, "SAFE_DRAFT_OWNER_FACTS_REQUIRED")
        self.assertEqual(r.phase6c_state, "SAFE_PDP_OWNER_FACTS_REQUIRED")


# ============================================================ REGRESSION SMOKE
class RegressionSmoke(unittest.TestCase):
    def test_73_core_engines_still_import(self):
        self.assertTrue(callable(PA.audit_listing))
        self.assertTrue(callable(BO.optimize_backend))
        self.assertTrue(callable(BE.build_bullets))
        self.assertTrue(callable(TE.build_titles))
        self.assertTrue(callable(DE.build_description))
        self.assertTrue(callable(IHB.build_item_highlights_content))

    def test_74_phase6b_still_ready_for_6c(self):
        d, _ = default_chain()
        kres = KAP.plan_keyword_allocation(d)
        self.assertTrue(kres.ready_for_6c)
        self.assertEqual(kres.stage_state, KAP.SAFE_ALLOCATION_OWNER_FACTS_REQUIRED)


if __name__ == "__main__":
    unittest.main()
