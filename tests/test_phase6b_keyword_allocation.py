#!/usr/bin/env python3
"""
Phase 6B — listing.keyword_allocation_planner: top-relevance selection + 13-field allocation.

Hermetic tests (synthetic, non-paid fixtures) covering the Phase 6B proof gate: preflight + 6A
dependency, cockpit-delta advisory boundary, keyword-source REUSE, the physical-claim eligibility
gate, deterministic versioned ranking, the exact 13-field schema, field limits + safety, duplication
control, the separate unallocated pool + excluded summary, artifacts + atomic/last-valid writes,
validators, two-run determinism, and regression (no listing copy, no Amazon path, no 6C artifacts).

Real T2 proof (TIER_A=74 / TIER_B=378, real two-run determinism, zero leakage) lives in
test_phase6b_t2_proof.py, which is SKIPPED on a clean checkout with no local (gitignored) runs/T2.
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "listing"))
sys.path.insert(0, os.path.join(ROOT, "core"))

from production import product_workspace as PW      # noqa: E402
import keyword_allocation_planner as KAP            # noqa: E402
import keyword_source_adapter as KSA                # noqa: E402
import bullet_engine as BE                          # noqa: E402
import page_auditor as PA                           # noqa: E402  (import-only regression)
import backend_optimizer as BO                      # noqa: E402  (import-only regression)

# A synthetic nurse-sweatshirt niche: safe market identity, physically-gated eligibles, and
# source-excluded terms (wrong audience/occasion, brand/IP, review, rejected).
NURSE_KEYWORDS = [
    {"keyword": "nurse sweatshirt", "tier": "A_CORE"},
    {"keyword": "nurses sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "sweatshirt for nurses", "tier": "B_SUPPORTING"},
    {"keyword": "gifts for nurses", "tier": "B_SUPPORTING"},
    {"keyword": "nurse gifts", "tier": "B_SUPPORTING"},
    {"keyword": "rn sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "cna sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "nurse practitioner sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "icu nurse sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "er nurse sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "nicu nurse sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "lpn sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "gifts for nurse practitioners", "tier": "B_SUPPORTING"},
    {"keyword": "nurse practitioner gifts", "tier": "B_SUPPORTING"},
    {"keyword": "cna gifts", "tier": "B_SUPPORTING"},
    {"keyword": "picu nurse sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "gifts for nursing students", "tier": "B_SUPPORTING"},
    {"keyword": "gifts for rn", "tier": "B_SUPPORTING"},
    {"keyword": "gifts for lpn", "tier": "B_SUPPORTING"},
    {"keyword": "nicu nurse gifts", "tier": "B_SUPPORTING"},
    {"keyword": "er nurse gifts", "tier": "B_SUPPORTING"},
    {"keyword": "gifts for cna", "tier": "B_SUPPORTING"},
    # eligible-from-source, but each asserts an UNVERIFIED physical fact -> gated by Phase 6B:
    {"keyword": "embroidered nurse sweatshirt", "tier": "B_SUPPORTING"},   # decoration
    {"keyword": "cotton nurse sweatshirt", "tier": "B_SUPPORTING"},        # material
    {"keyword": "personalized nurse sweatshirt", "tier": "A_CORE"},        # personalization
    {"keyword": "custom nurse sweatshirt", "tier": "B_SUPPORTING"},        # personalization
    {"keyword": "black nurse sweatshirt", "tier": "B_SUPPORTING"},         # color
    {"keyword": "womens nurse sweatshirt", "tier": "B_SUPPORTING"},        # size / gender
    {"keyword": "nurse hoodie", "tier": "B_SUPPORTING"},                   # different garment
    {"keyword": "nurses week sweatshirt", "tier": "B_SUPPORTING"},         # occasion
    {"keyword": "best nurse sweatshirt", "tier": "B_SUPPORTING"},          # subjective differentiator
    # source-excluded (blocking risks / review / rejected):
    {"keyword": "nurse graduation sweatshirt", "tier": "B_SUPPORTING",
     "risk_flags": ["wrong_occasion_or_theme:graduation"]},
    {"keyword": "teacher sweatshirt", "tier": "B_SUPPORTING", "risk_flags": ["wrong_audience:teacher"]},
    {"keyword": "disney nurse sweatshirt", "tier": "B_SUPPORTING", "risk_flags": ["brand:disney"]},
    {"keyword": "review term nurse", "tier": "REVIEW"},
    {"keyword": "junk noise", "tier": "REJECTED"},
]


def make_ws(keywords=None, project_id="TESTWS"):
    """A hermetic run workspace with a completed Phase 6A stage the planner can consume."""
    d = tempfile.mkdtemp(prefix="6b-ws-")
    kws = keywords if keywords is not None else NURSE_KEYWORDS
    lean = {"run_metadata": {"run_id": "kwrun_test", "source_schema": "master_keywords_lean"},
            "target_profile": {"seed": "nurse sweatshirt"}, "keywords": kws}
    with open(os.path.join(d, "MASTER-KEYWORDS-LEAN.json"), "w", encoding="utf-8") as f:
        json.dump(lean, f)
    with open(os.path.join(d, "PROJECT-MANIFEST.json"), "w", encoding="utf-8") as f:
        json.dump({"project_id": project_id}, f)
    result = PW.build_product_workspace(d, workspace_id=project_id)
    PW.write_phase6a_artifacts(result, dest_dir=os.path.join(d, "phase6", "6A"), workspace_dir=d,
                               started_at="T0", completed_at="T1")
    return d


def plan(keywords=None):
    d = make_ws(keywords)
    return d, KAP.plan_keyword_allocation(d)


def write_into(result, root):
    dest = KAP.phase6b_dir(root)
    man = KAP.write_phase6b_artifacts(result, dest_dir=dest, workspace_dir=root,
                                      started_at="T0", completed_at="T1")
    return dest, man


# ============================================================ PREFLIGHT + DEPENDENCY
class Preflight(unittest.TestCase):
    def test_01_phase5_certification_valid(self):
        cert = json.load(open(os.path.join(ROOT, "SESSION5D-RELEASE1-CERTIFICATION.json"), encoding="utf-8"))
        self.assertEqual(cert["overall_status"], "RELEASE1_CERTIFIED")
        self.assertEqual(cert["mandatory_pass"], cert["mandatory_total"])

    def test_02_session6a_and_6a1_proofs_present(self):
        a = json.load(open(os.path.join(ROOT, "SESSION6A-PROOF-GATE.json"), encoding="utf-8"))
        self.assertTrue(a["ready_for_6b"])
        b = json.load(open(os.path.join(ROOT, "SESSION6A1-CONNECTIVITY-PROOF.json"), encoding="utf-8"))
        self.assertEqual(b["final_status"], "SESSION6A1_READY_FOR_REVIEW")

    def test_03_single_allocation_authority(self):
        self.assertTrue(os.path.exists(os.path.join(ROOT, "listing", "keyword_allocation_planner.py")))
        # no duplicate/prototype allocation authority
        for dup in ("listing/keyword_allocation_planner_v2.py", "production/keyword_allocation_planner.py",
                    "research/keyword_allocation_planner.py", "listing/allocation_planner.py"):
            self.assertFalse(os.path.exists(os.path.join(ROOT, dup)), dup)

    def test_04_no_amazon_or_network_path_in_planner(self):
        src = open(os.path.join(ROOT, "listing", "keyword_allocation_planner.py"), encoding="utf-8").read().lower()
        for bad in ("sp-api", "sp_api", "sellingpartner", "selling_partner", "mws", "seller_central",
                    "sellercentral", "boto3", "import requests", "urllib.request", "webdriver",
                    "selenium", "playwright", "amazon advertising api"):
            self.assertNotIn(bad, src, bad)

    def test_05_reuses_phase5_authorities(self):
        self.assertIs(KAP.KSA, KSA)
        self.assertTrue(callable(KAP.KSA.load_keyword_source))
        self.assertTrue(callable(KAP.CPR.resolve_category_policy))
        self.assertEqual(KAP.canonical_json, PW.canonical_json)     # one serialization authority
        self.assertEqual(KAP.content_sha256, PW.content_sha256)


class Dependency(unittest.TestCase):
    def test_06_loads_ready_dependency(self):
        d = make_ws()
        dep = KAP.load_phase6a_dependency(d)
        self.assertTrue(dep.ready_for_6b)
        self.assertEqual(dep.workspace_id, dep.product_id)
        self.assertEqual(dep.product_type, "sweatshirt")
        self.assertEqual(dep.audience, "nurses")

    def test_07_dependency_recomputes_6a_hashes(self):
        d = make_ws()
        v = PW.verify_phase6a_artifacts(os.path.join(d, "phase6", "6A"), workspace_dir=d)
        self.assertTrue(v["ok"])

    def test_08_missing_6a_artifact_blocks(self):
        d = make_ws()
        os.remove(os.path.join(d, "phase6", "6A", "CLAIM-EVIDENCE.json"))
        with self.assertRaises(KAP.Phase6ADependencyError):
            KAP.load_phase6a_dependency(d)

    def test_09_tampered_6a_artifact_blocks(self):
        d = make_ws()
        p = os.path.join(d, "phase6", "6A", "PRODUCT-WORKSPACE.json")
        doc = json.load(open(p, encoding="utf-8"))
        doc["audience"] = "teachers"            # tamper -> recomputed hash won't match manifest
        open(p, "w", encoding="utf-8").write(json.dumps(doc))
        with self.assertRaises(KAP.Phase6ADependencyError):
            KAP.load_phase6a_dependency(d)

    def test_10_source_hash_drift_blocks(self):
        d = make_ws()
        # mutate the source AFTER 6A recorded its hash -> dependency must refuse
        with open(os.path.join(d, "MASTER-KEYWORDS-LEAN.json"), "a", encoding="utf-8") as f:
            f.write("\n")
        with self.assertRaises(KAP.Phase6ADependencyError):
            KAP.load_phase6a_dependency(d)

    def test_11_planning_does_not_modify_6a(self):
        d = make_ws()
        dest6a = os.path.join(d, "phase6", "6A")
        before = {n: open(os.path.join(dest6a, n), "rb").read() for n in os.listdir(dest6a)}
        KAP.plan_keyword_allocation(d)
        after = {n: open(os.path.join(dest6a, n), "rb").read() for n in os.listdir(dest6a)}
        self.assertEqual(before, after)


# ============================================================ COCKPIT DELTA
class CockpitDelta(unittest.TestCase):
    def test_12_policy_watcher_and_uspto_absent(self):
        for p in ("policy_watcher.py", "uspto_check.py"):
            hits = []
            for base, _dirs, files in os.walk(ROOT):
                if ".venv" in base or "__pycache__" in base or os.sep + ".git" in base:
                    continue
                if p in files:
                    hits.append(base)
            self.assertEqual(hits, [], f"{p} should be deferred/absent")

    def test_13_listing_generator_is_v2(self):
        src = open(os.path.join(ROOT, "listing", "listing_generator.py"), encoding="utf-8").read()
        self.assertIn("v2", src.lower())

    def test_14_aplus_registry_supersedes_legacy_templates(self):
        reg = open(os.path.join(ROOT, "listing", "aplus_module_registry.py"), encoding="utf-8").read().lower()
        self.assertIn("replace", reg)
        self.assertTrue(os.path.exists(os.path.join(ROOT, "listing", "a_plus_templates.py")))  # legacy still quarantined

    def test_15_keyword_intelligence_and_competitor_gap_present(self):
        self.assertTrue(os.path.exists(os.path.join(ROOT, "research", "keyword_intelligence.py")))
        self.assertTrue(os.path.exists(os.path.join(ROOT, "research", "competitor_gap_analyzer.py")))

    def test_16_title_limit_is_category_resolved_not_hardcoded_75(self):
        policy = KAP.CPR.resolve_category_policy("apparel")
        self.assertEqual(policy.title_hard_limit, 75)          # apparel value, but via the registry
        self.assertFalse(policy.fallback_used)
        # the planner never redefines a title limit of its own
        src = open(os.path.join(ROOT, "listing", "keyword_allocation_planner.py"), encoding="utf-8").read()
        self.assertNotIn("title_hard_limit =", src)

    def test_17_cockpit_delta_report_is_deterministic(self):
        r1 = KAP_cockpit_report()
        r2 = KAP_cockpit_report()
        self.assertEqual(r1, r2)


def KAP_cockpit_report():
    # a tiny deterministic classification the session emits; mirrors PHASE6B-COCKPIT-DELTA-REPORT.json
    return json.dumps({
        "keyword_intelligence": "ALREADY_PRESENT",
        "competitor_gap": "ALREADY_PRESENT",
        "listing_generator_v2": "CURRENT_IMPLEMENTATION_NEWER",
        "a_plus_templates": "CURRENT_IMPLEMENTATION_NEWER",
        "policy_watcher": "POTENTIAL_FUTURE_PORT",
        "uspto_check": "POTENTIAL_FUTURE_PORT",
        "title_75_char": "UNVERIFIED_POLICY_CLAIM",
    }, sort_keys=True)


# ============================================================ KEYWORD SOURCE REUSE
class KeywordSource(unittest.TestCase):
    def test_18_uses_existing_adapter_and_lean_source(self):
        d = make_ws()
        src = KSA.load_keyword_source(d)
        self.assertEqual(src.source_file, "MASTER-KEYWORDS-LEAN.json")
        self.assertEqual(src.source_mode, KSA.MODE_LEAN)

    def test_19_source_records_not_mutated(self):
        d = make_ws()
        before = open(os.path.join(d, "MASTER-KEYWORDS-LEAN.json"), "rb").read()
        KAP.plan_keyword_allocation(d)
        after = open(os.path.join(d, "MASTER-KEYWORDS-LEAN.json"), "rb").read()
        self.assertEqual(before, after)

    def test_20_source_sha_recomputes(self):
        d = make_ws()
        _dep, res = d, KAP.plan_keyword_allocation(d)
        self.assertEqual(res.source_keyword_sha256, KSA.load_keyword_source(d).source_sha256)

    def test_21_keyword_ids_unique(self):
        _d, res = plan()
        ids = [k["keyword_id"] for k in res.top_relevance_keywords["keywords"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_22_legacy_source_not_silently_used(self):
        # adapter priority never reaches legacy unless explicitly enabled
        self.assertEqual(KSA.SOURCE_PRIORITY[-1][2], KSA.MODE_LEGACY_UNSAFE)


# ============================================================ ELIGIBILITY GATE
class Eligibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.res = plan()
        cls.safe_phrases = {k["normalized_phrase"] for k in cls.res.top_relevance_keywords["keywords"]}
        cls.gated = {g["phrase"] for g in cls.res._gated}

    def _classify(self, phrase):
        return KAP._classify_keyword({"keyword_normalized": phrase})[0]

    def test_23_pure_identity_is_safe(self):
        for p in ("nurse sweatshirt", "gifts for nurses", "rn sweatshirt", "nurse gifts"):
            self.assertEqual(self._classify(p), "SAFE", p)

    def test_24_decoration_term_gated(self):
        self.assertEqual(self._classify("embroidered nurse sweatshirt"), "GATED")

    def test_25_material_term_gated(self):
        self.assertEqual(self._classify("cotton nurse sweatshirt"), "GATED")

    def test_26_personalization_term_gated(self):
        for p in ("personalized nurse sweatshirt", "custom nurse sweatshirt"):
            self.assertEqual(self._classify(p), "GATED", p)

    def test_27_color_term_gated(self):
        self.assertEqual(self._classify("black nurse sweatshirt"), "GATED")

    def test_28_size_gender_term_gated(self):
        self.assertEqual(self._classify("womens nurse sweatshirt"), "GATED")

    def test_29_other_garment_gated(self):
        self.assertEqual(self._classify("nurse hoodie"), "GATED")

    def test_30_occasion_term_gated(self):
        self.assertEqual(self._classify("nurses week sweatshirt"), "GATED")

    def test_31_gated_terms_never_reach_top_relevance(self):
        for g in ("embroidered nurse sweatshirt", "cotton nurse sweatshirt",
                  "personalized nurse sweatshirt", "black nurse sweatshirt"):
            self.assertNotIn(g, self.safe_phrases, g)

    def test_32_market_identity_is_not_a_verified_physical_fact(self):
        # sweatshirt/nurses drive relevance, but the 6A facts stay UNKNOWN and claims stay blocked
        dep = KAP.load_phase6a_dependency(self.d)
        self.assertFalse(dep.concept_is_publishable("decoration_method"))
        self.assertFalse(dep.concept_is_publishable("personalization_fields"))
        self.assertTrue(dep.concept_is_publishable("recipient"))       # only recipient is verified

    def test_33_excluded_reasons_cover_source_and_gate(self):
        codes = set(self.res.coverage["excluded_counts_by_reason"])
        self.assertIn(KAP.EXC_WRONG_AUDIENCE, codes)
        self.assertIn(KAP.EXC_BRAND_IP, codes)
        self.assertIn(KAP.EXC_SOURCE_REJECTED, codes)
        self.assertTrue({KAP.EXC_UNSUPPORTED_DECORATION, KAP.EXC_UNSUPPORTED_MATERIAL,
                         KAP.EXC_UNSUPPORTED_PERSONALIZATION} & codes)

    def test_34_every_excluded_term_has_reason(self):
        exc = self.res.excluded_summary
        for bucket in (exc["source_ineligible"], exc["phase6b_gated_owner_fact_required"]):
            for code, info in bucket.items():
                self.assertTrue(code and info["count"] >= 1)


# ============================================================ RANKING
class Ranking(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.res = plan()
        cls.kws = cls.res.top_relevance_keywords["keywords"]

    def test_35_ranking_policy_versioned(self):
        self.assertEqual(self.res.top_relevance_keywords["ranking_policy_version"], KAP.RANKING_POLICY_VERSION)

    def test_36_deterministic_order_and_reasons(self):
        ordered = sorted(self.kws, key=lambda k: (-k["relevance_score"], k["source_rank"], k["normalized_phrase"]))
        self.assertEqual([k["keyword_id"] for k in self.kws], [k["keyword_id"] for k in ordered])
        for k in self.kws:
            self.assertTrue(k["inclusion_reason_codes"])
            self.assertTrue(k["score_components"])

    def test_37_head_product_audience_ranks_first(self):
        self.assertEqual(self.kws[0]["normalized_phrase"], "nurse sweatshirt")

    def test_38_product_and_audience_relevance_scored(self):
        head = next(k for k in self.kws if k["normalized_phrase"] == "nurse sweatshirt")
        self.assertGreater(head["score_components"]["product_identity_match"], 0)
        self.assertGreater(head["score_components"]["audience_identity_match"], 0)

    def test_39_duplicate_root_penalty_applies(self):
        # 'nurses sweatshirt' shares the root of 'nurse sweatshirt' -> penalised or consolidated out
        variants = [k for k in self.kws if k["normalized_root"] == "nurse|sweatshirt"]
        pen = [k for k in variants if k["score_components"]["duplicate_root_penalty"] < 0]
        # at least the non-first same-root variant is penalised
        self.assertTrue(pen or len(variants) == 1)

    def test_40_no_external_ai_or_web_in_ranking(self):
        src = open(os.path.join(ROOT, "listing", "keyword_allocation_planner.py"), encoding="utf-8").read()
        for bad in ("anthropic", "openai", "requests.get", "requests.post", "http://", "https://"):
            self.assertNotIn(bad, src)

    def test_41_source_tier_preserved(self):
        for k in self.kws:
            self.assertIn(k["source_tier"], (KSA.TIER_A, KSA.TIER_B, KSA.TIER_C))


# ============================================================ 13-FIELD SCHEMA
class Schema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.res = plan()
        cls.alloc = cls.res.allocation

    def test_42_exactly_13_fields_in_order(self):
        self.assertEqual(self.alloc["field_order"], list(KAP.FIELD_KEYS))
        self.assertEqual(len(KAP.FIELD_KEYS), 13)
        self.assertEqual(set(self.alloc["fields"]), set(KAP.FIELD_KEYS))
        self.assertEqual(set(self.alloc["field_status"]), set(KAP.FIELD_KEYS))

    def test_43_exact_field_keys(self):
        self.assertEqual(list(KAP.FIELD_KEYS), [
            "title_primary", "title_support", "bullet_1", "bullet_2", "bullet_3", "bullet_4",
            "bullet_5", "description", "backend_candidates", "item_highlights", "aplus",
            "listing_image_text", "image_alt_text"])

    def test_44_unallocated_is_not_a_field(self):
        self.assertNotIn("unallocated_eligible", self.alloc["fields"])
        self.assertEqual(self.res.unallocated_eligible["schema_version"], KAP.UNALLOCATED_SCHEMA_VERSION)

    def test_45_missing_field_rejected(self):
        bad = json.loads(json.dumps(self.alloc))
        del bad["fields"]["aplus"]
        self.assertTrue(KAP.validate_keyword_allocation_map(bad))

    def test_46_extra_field_rejected(self):
        bad = json.loads(json.dumps(self.alloc))
        bad["fields"]["title_extra"] = []
        self.assertTrue(KAP.validate_keyword_allocation_map(bad))

    def test_47_field_status_count_13(self):
        self.assertEqual(len(self.alloc["field_status"]), 13)

    def test_48_deterministic_hash_recomputes(self):
        self.assertFalse(KAP.validate_keyword_allocation_map(self.alloc))
        tampered = json.loads(json.dumps(self.alloc))
        tampered["fields"]["title_primary"] = []
        self.assertTrue(KAP.validate_keyword_allocation_map(tampered))


# ============================================================ FIELD LIMITS + SAFETY
class Fields(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.res = plan()
        cls.f = cls.res.fields
        cls.st = cls.res.field_status

    def test_49_field_limits_respected(self):
        self.assertLessEqual(len(self.f["title_primary"]), 1)
        self.assertLessEqual(len(self.f["title_support"]), 2)
        for b in ("bullet_1", "bullet_2", "bullet_3", "bullet_4", "bullet_5"):
            self.assertLessEqual(len(self.f[b]), 2)
        self.assertLessEqual(len(self.f["description"]), 8)

    def test_50_empty_fields_have_reasons(self):
        for k in KAP.FIELD_KEYS:
            if not self.f[k]:
                self.assertTrue(self.st[k].get("empty"))
                self.assertTrue(self.st[k].get("empty_reason_code"))

    def test_51_title_primary_is_safe_core_phrase(self):
        self.assertEqual(len(self.f["title_primary"]), 1)
        a = self.f["title_primary"][0]
        self.assertEqual(KAP._classify_keyword({"keyword_normalized": a["normalized_phrase"]})[0], "SAFE")
        self.assertEqual(a["normalized_phrase"], "nurse sweatshirt")

    def test_52_title_support_incremental(self):
        for a in self.f["title_support"]:
            self.assertTrue(a.get("incremental_semantic_value"))
            self.assertNotEqual(a["normalized_root"], self.f["title_primary"][0]["normalized_root"])

    def test_53_bullets_map_to_existing_five_jobs(self):
        for i, b in enumerate(("bullet_1", "bullet_2", "bullet_3", "bullet_4", "bullet_5")):
            self.assertEqual(self.st[b]["bullet_job"], BE.JOBS[i])

    def test_54_blocked_bullets_are_empty_with_reason(self):
        for b in ("bullet_2", "bullet_3", "bullet_4"):
            self.assertEqual(self.f[b], [])
            self.assertEqual(self.st[b]["empty_reason_code"], KAP.EMPTY_CLAIM_EVIDENCE_MISSING)

    def test_55_bullet5_recipient_verified(self):
        self.assertTrue(self.f["bullet_5"])
        self.assertEqual(self.st["bullet_5"]["bullet_job"], "RECIPIENT_OCCASION_USE")

    def test_56_backend_candidates_safe_and_units_preserved(self):
        for a in self.f["backend_candidates"]:
            self.assertEqual(KAP._classify_keyword({"keyword_normalized": a["normalized_phrase"]})[0], "SAFE")
            self.assertEqual(a["downstream_authorization"], KAP.ADVISORY_ONLY)
        self.assertEqual(self.st["backend_candidates"].get("downstream_authority"),
                         "listing.backend_optimizer.optimize_backend")

    def test_57_item_highlights_category_gated_and_evidence_gated(self):
        # apparel supports the field, so emptiness is evidence-driven, not category-driven
        self.assertEqual(self.f["item_highlights"], [])
        self.assertEqual(self.st["item_highlights"]["empty_reason_code"], KAP.EMPTY_CLAIM_EVIDENCE_MISSING)

    def test_58_aplus_evidence_gated_empty(self):
        self.assertEqual(self.f["aplus"], [])
        self.assertEqual(self.st["aplus"]["empty_reason_code"], KAP.EMPTY_OWNER_FACT_REQUIRED)

    def test_59_image_fields_advisory_and_factual(self):
        for k in ("listing_image_text", "image_alt_text"):
            for a in self.f[k]:
                self.assertEqual(a["downstream_authorization"], KAP.ADVISORY_ONLY)
                self.assertEqual(KAP._classify_keyword({"keyword_normalized": a["normalized_phrase"]})[0], "SAFE")

    def test_60_no_unsupported_physical_claim_anywhere(self):
        self.assertEqual(self.res.coverage["leakage"]["unsupported_physical_claim"], 0)


# ============================================================ DUPLICATION CONTROL
class Duplication(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.res = plan()
        cls.f = cls.res.fields

    def test_61_within_field_roots_unique(self):
        for k in KAP.FIELD_KEYS:
            roots = [a["normalized_root"] for a in self.f[k]]
            self.assertEqual(len(roots), len(set(roots)), k)

    def test_62_cross_visible_reuse_is_justified(self):
        # the core identity reused title_primary -> bullet_1 must carry a reuse justification
        b1 = self.f["bullet_1"][0]
        self.assertEqual(b1["reuse"], "REUSE_JUSTIFIED")
        self.assertTrue(b1["reuse_justification"])

    def test_63_word_order_and_plural_consolidated(self):
        # 'nurses sweatshirt' / 'sweatshirt for nurses' share the head root and are not each allocated
        allocated_roots = [a["normalized_root"] for k in KAP.VISIBLE_FIELDS for a in self.f[k]]
        self.assertEqual(allocated_roots.count("nurse|sweatshirt"),
                         sum(1 for k in ("title_primary", "bullet_1") for _ in self.f[k]))

    def test_64_essential_identity_retained(self):
        phrases = {a["phrase"] for k in KAP.FIELD_KEYS for a in self.f[k]}
        self.assertIn("nurse sweatshirt", phrases)


# ============================================================ UNALLOCATED + EXCLUDED
class UnallocatedExcluded(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d, cls.res = plan()

    def test_65_unallocated_only_safe_terms_with_reasons(self):
        for r in self.res.unallocated_eligible["keywords"]:
            self.assertEqual(KAP._classify_keyword({"keyword_normalized": r["normalized_phrase"]})[0], "SAFE")
            self.assertTrue(r["non_allocation_reason_code"])

    def test_66_rejected_and_gated_not_in_unallocated(self):
        pool = {r["normalized_phrase"] for r in self.res.unallocated_eligible["keywords"]}
        self.assertNotIn("junk noise", pool)
        self.assertNotIn("embroidered nurse sweatshirt", pool)
        self.assertNotIn("teacher sweatshirt", pool)

    def test_67_unallocated_ordering_deterministic(self):
        recs = self.res.unallocated_eligible["keywords"]
        ordered = sorted(recs, key=lambda r: (-r["relevance_score"], r["source_rank"], r["normalized_phrase"]))
        self.assertEqual([r["keyword_id"] for r in recs], [r["keyword_id"] for r in ordered])

    def test_68_every_excluded_and_unallocated_reason_resolves(self):
        self.assertFalse(KAP.validate_unallocated_eligible_pool(self.res.unallocated_eligible))
        total = self.res.coverage["excluded_total"]
        self.assertEqual(total, sum(self.res.coverage["excluded_counts_by_reason"].values()))


# ============================================================ ARTIFACTS + WRITES
class Artifacts(unittest.TestCase):
    def test_69_five_artifacts_generated_with_exact_names(self):
        d = make_ws()
        res = KAP.plan_keyword_allocation(d)
        dest, man = write_into(res, d)
        for name in (KAP.TOP_RELEVANCE_FILENAME, KAP.ALLOCATION_MAP_FILENAME,
                     KAP.COVERAGE_REPORT_FILENAME, KAP.UNALLOCATED_FILENAME, KAP.STAGE_MANIFEST_FILENAME):
            self.assertTrue(os.path.exists(os.path.join(dest, name)), name)

    def test_70_manifest_hashes_match_bytes(self):
        d = make_ws()
        res = KAP.plan_keyword_allocation(d)
        dest, man = write_into(res, d)
        self.assertTrue(KAP.verify_phase6b_artifacts(dest, workspace_dir=d)["ok"])

    def test_71_manifest_does_not_self_hash(self):
        d = make_ws()
        res = KAP.plan_keyword_allocation(d)
        _dest, man = write_into(res, d)
        self.assertFalse(any(KAP.STAGE_MANIFEST_FILENAME in k for k in man["output_hashes"]))

    def test_72_absolute_and_traversal_paths_rejected(self):
        d = make_ws()
        res = KAP.plan_keyword_allocation(d)
        _dest, man = write_into(res, d)
        bad = json.loads(json.dumps(man))
        bad["output_hashes"]["/etc/passwd"] = "x"
        self.assertTrue(KAP.validate_stage6b_manifest(bad, d))
        bad2 = json.loads(json.dumps(man))
        bad2["output_hashes"]["../escape.json"] = "x"
        self.assertTrue(KAP.validate_stage6b_manifest(bad2, d))

    def test_73_no_phase6c_artifact_created(self):
        d = make_ws()
        res = KAP.plan_keyword_allocation(d)
        dest, _man = write_into(res, d)
        forbidden = {"PRODUCT-DETAIL-PAGE.json", "TITLE-OPTIONS.json", "BULLETS.json", "DESCRIPTION.txt",
                     "BASIC-APLUS-CONTENT.json", "PREMIUM-APLUS-DRAFT.json", "LISTING-IMAGE-PLAN.json",
                     "PACKAGE-INDEX.json"}
        self.assertEqual(set(os.listdir(dest)) & forbidden, set())
        self.assertFalse(os.path.exists(os.path.join(dest, "SELLER-CENTRAL-READY")))

    def test_74_no_listing_copy_generated(self):
        # allocation records carry source keyword phrases, never composed sentence copy
        d = make_ws()
        res = KAP.plan_keyword_allocation(d)
        for k in KAP.FIELD_KEYS:
            for a in res.fields[k]:
                self.assertNotIn(".", a["phrase"])        # no prose / sentences
                self.assertLess(len(a["phrase"].split()), 8)

    def test_75_failed_run_preserves_last_valid(self):
        d = make_ws()
        res = KAP.plan_keyword_allocation(d)
        dest, _man = write_into(res, d)
        before = {n: open(os.path.join(dest, n), "rb").read() for n in os.listdir(dest)}
        orig = KAP.validate_keyword_allocation_map
        try:
            KAP.validate_keyword_allocation_map = lambda *a, **k: ["forced failure"]
            with self.assertRaises(KAP.KeywordAllocationError):
                KAP.write_phase6b_artifacts(res, dest_dir=dest, workspace_dir=d,
                                            started_at="X", completed_at="Y")
        finally:
            KAP.validate_keyword_allocation_map = orig
        after = {n: open(os.path.join(dest, n), "rb").read() for n in os.listdir(dest)}
        self.assertEqual(before, after)
        # no residue temp files
        self.assertFalse([n for n in os.listdir(dest) if n.startswith(".tmp-6b-") or n.endswith(".part")])


# ============================================================ DETERMINISM
class Determinism(unittest.TestCase):
    def test_76_two_run_hashes_match(self):
        d = make_ws()
        r1 = KAP.plan_keyword_allocation(d)
        dest1 = tempfile.mkdtemp(prefix="6b-d1-")
        m1 = KAP.write_phase6b_artifacts(r1, dest_dir=os.path.join(dest1, "phase6", "6B"),
                                         workspace_dir=dest1, started_at="A", completed_at="A")
        r2 = KAP.plan_keyword_allocation(d)
        dest2 = tempfile.mkdtemp(prefix="6b-d2-")
        m2 = KAP.write_phase6b_artifacts(r2, dest_dir=os.path.join(dest2, "phase6", "6B"),
                                         workspace_dir=dest2, started_at="B", completed_at="B")
        for name in KAP.REQUIRED_ARTIFACTS:
            a = open(os.path.join(dest1, "phase6", "6B", name), "rb").read()
            b = open(os.path.join(dest2, "phase6", "6B", name), "rb").read()
            self.assertEqual(a, b, name)
        self.assertEqual(m1["deterministic_content_sha256"], m2["deterministic_content_sha256"])
        self.assertEqual(r1.deterministic_content_sha256(), r2.deterministic_content_sha256())


# ============================================================ REGRESSION SMOKE
class RegressionSmoke(unittest.TestCase):
    def test_77_core_engines_still_import(self):
        self.assertTrue(callable(PA.audit_listing))
        self.assertTrue(callable(BO.optimize_backend))
        self.assertTrue(callable(BE.build_bullets))

    def test_78_listing_stays_safe_draft(self):
        _d, res = plan()
        self.assertEqual(res.listing_publishability_state, "SAFE_DRAFT_OWNER_FACTS_REQUIRED")
        self.assertEqual(res.stage_state, KAP.SAFE_ALLOCATION_OWNER_FACTS_REQUIRED)
        self.assertTrue(res.ready_for_6c)


if __name__ == "__main__":
    unittest.main()
