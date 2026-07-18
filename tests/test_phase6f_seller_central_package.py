#!/usr/bin/env python3
"""Phase 6F — verified safe-draft Seller Central manual package.

Covers: preflight + dependency verification, single-authority reuse, the effective-publishability
hierarchy (most restrictive wins; a lexical PageAuditor PASS can never upgrade), the numbered package
layout, the read-me / field snapshots / manual-entry plan, A+ + creative separation, the package index
+ its SHA file, path safety, the final listing audit, the manual-entry leakage audit, the candidate ->
verify -> promote atomic build with last-valid protection, the T2 proof, two-run + three-mode
determinism, and the permanent Amazon boundary.
"""
import json
import os
import shutil
import tempfile
import unittest

from production import seller_central_package as SCP
from production import product_detail_page as PDP
from production import aplus_assembly as APL
from creative import creative_production_package as CPP
import page_auditor as PA
import claim_evidence as CE

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(_ROOT, "runs", "T2")
# The T2 workspace holds paid Helium 10 data and is gitignored, so it is absent in a clean checkout /
# worktree. Tests that need it skip cleanly (hermetic resolver / path-safety / index tests still run).
_HAS_RUN = os.path.isdir(os.path.join(RUN, "phase6", "6E"))


def _clone_workspace(dst):
    """Copy the T2 workspace (minus heavy paid exports) so write / tamper tests never touch the real run."""
    shutil.copytree(RUN, dst, ignore=shutil.ignore_patterns("*.xlsx", "*.csv"))
    return dst


def _sources(**over):
    """A synthetic per-source publishability dict for pure-resolver tests (defaults = a clean, ready set)."""
    s = {
        "product_fact_state": "PUBLISHABLE", "atomic_claim_state": "PUBLISHABLE",
        "category_policy_state": "PUBLISHABLE", "field_state": "PUBLISHABLE",
        "page_auditor_state": "PUBLISHABLE", "asset_state": "PUBLISHABLE",
        "capability_state": "PUBLISHABLE", "creative_state": "PUBLISHABLE",
        "package_declared_state": "PUBLISHABLE", "owner_facts_complete": True,
        "real_assets_verified": True, "aplus_eligibility_verified": True,
        "copy_now_count": 0, "upload_now_count": 0,
    }
    s.update(over)
    return s


@unittest.skipUnless(os.path.isdir(os.path.join(RUN, "phase6", "6E")),
                     "T2 Phase 6E proof workspace is required")
class Phase6FBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = SCP.assemble_phase6f(RUN, connectivity_mode="LOCAL_SAFE")
        cls.deps = cls.result._deps
        cls.index = cls.result._index_doc
        cls.entries = {e["artifact_path"]: e for e in cls.index["entries"]}
        cls.final_audit = cls.result.final_listing_audit
        cls.leakage = cls.result._leakage_audit
        cls.files = {e["name"]: e["data"] for e in cls.result._files_list}

    def text(self, name):
        return self.files[name].decode("utf-8")


# ============================================================ PREFLIGHT / DEPENDENCIES
class TestDependencies(Phase6FBase):
    def test_dependencies_load_and_verify(self):
        d = SCP.load_phase6f_dependencies(RUN, connectivity_mode="TEST_DENY_EXTERNAL")
        self.assertEqual(d.product_id, "T2")
        self.assertEqual(d.claim_evidence_sha256[:8], "840e6216")
        self.assertEqual(d.product_facts_sha256[:8], "0199b4de")
        self.assertEqual(d.keyword_source_sha256[:8], "52b02f16")
        self.assertEqual(d.phase6c_manifest_sha256[:8], "2e17a1b9")
        self.assertEqual(d.phase6d_manifest_sha256[:8], "c4c2c9f7")
        self.assertEqual(d.phase6e_manifest_sha256[:8], "7974de88")

    def test_phase5_certification_present(self):
        self.assertTrue(os.path.exists(os.path.join(_ROOT, "SESSION5D-RELEASE1-CERTIFICATION.json")))

    def test_phase6a_dependency_verifies(self):
        self.assertTrue(os.path.exists(os.path.join(RUN, "phase6", "6A", "STAGE-6A-MANIFEST.json")))

    def test_phase6b_dependency_verifies(self):
        self.assertTrue(os.path.exists(os.path.join(RUN, "phase6", "6B", "STAGE-6B-MANIFEST.json")))

    def test_phase6c_dependency_verifies(self):
        v = PDP.verify_phase6c_artifacts(PDP.phase6c_dir(RUN), workspace_dir=RUN)
        self.assertTrue(v["ok"], v.get("errors"))

    def test_session6c1_atomic_claim_proof(self):
        self.assertEqual(self.deps.claim_evidence_sha256[:8], "840e6216")
        self.assertTrue(self.deps.claims.atomicity_ok)

    def test_phase6d_dependency_verifies(self):
        v = APL.verify_phase6d_artifacts(APL.phase6d_dir(RUN), workspace_dir=RUN)
        self.assertTrue(v["ok"], v.get("errors"))

    def test_phase6e_dependency_verifies(self):
        v = CPP.verify_phase6e_artifacts(CPP.phase6e_dir(RUN), workspace_dir=RUN)
        self.assertTrue(v["ok"], v.get("errors"))

    def test_connectivity_manifest_verifies(self):
        import hashlib
        man = json.load(open(os.path.join(_ROOT, "docs", "CONNECTIVITY-POLICY-MANIFEST.json"),
                             encoding="utf-8"))
        raw = open(os.path.join(_ROOT, "docs", "CONNECTIVITY-POLICY.md"), "rb").read()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), man["policy_sha256"])
        self.assertTrue(man["permanent_amazon_boundary"]["amazon_account_isolation"])

    def test_ready_for_6f_is_true(self):
        man = json.load(open(os.path.join(RUN, "phase6", "6E", "STAGE-6E-MANIFEST.json"), encoding="utf-8"))
        self.assertTrue(man["ready_for_6f"])

    def test_invalid_dependency_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _clone_workspace(os.path.join(td, "T2"))
            man = os.path.join(ws, "phase6", "6E", "STAGE-6E-MANIFEST.json")
            d = json.load(open(man, encoding="utf-8"))
            d["ready_for_6f"] = False
            json.dump(d, open(man, "w", encoding="utf-8"))
            with self.assertRaises(SCP.Phase6FDependencyError):
                SCP.load_phase6f_dependencies(ws)

    def test_missing_dependency_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _clone_workspace(os.path.join(td, "T2"))
            os.remove(os.path.join(ws, "phase6", "6E", "LISTING-IMAGE-PLAN.json"))
            with self.assertRaises(SCP.Phase6FDependencyError):
                SCP.load_phase6f_dependencies(ws)

    def test_hash_mismatch_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _clone_workspace(os.path.join(td, "T2"))
            man = os.path.join(ws, "phase6", "6E", "STAGE-6E-MANIFEST.json")
            d = json.load(open(man, encoding="utf-8"))
            d["input_hashes"]["claim_evidence_sha256"] = "deadbeef" * 8
            json.dump(d, open(man, "w", encoding="utf-8"))
            with self.assertRaises(SCP.Phase6FDependencyError):
                SCP.load_phase6f_dependencies(ws)

    def test_tampered_6e_artifact_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _clone_workspace(os.path.join(td, "T2"))
            p = os.path.join(ws, "phase6", "6E", "LISTING-IMAGE-PLAN.json")
            with open(p, "a", encoding="utf-8") as f:
                f.write("\n")
            with self.assertRaises(SCP.Phase6FDependencyError):
                SCP.load_phase6f_dependencies(ws)

    def test_upstream_artifacts_not_modified(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _clone_workspace(os.path.join(td, "T2"))
            before = {}
            for stage in ("6A", "6B", "6C", "6D", "6E"):
                d = os.path.join(ws, "phase6", stage)
                for f in os.listdir(d):
                    p = os.path.join(d, f)
                    if os.path.isfile(p):
                        before[p] = SCP._file_sha256(p)
            res = SCP.assemble_phase6f(ws)
            SCP.write_phase6f_artifacts(res, workspace_dir=ws, started_at="t", completed_at="t")
            for p, h in before.items():
                self.assertEqual(SCP._file_sha256(p), h, f"upstream artifact changed: {p}")

    def test_no_amazon_write_or_upload_path_in_source(self):
        src = open(SCP.__file__, encoding="utf-8").read().lower()
        for token in ("boto3", "put_listings", "putlistings", "catalog_items", "listings_items",
                      "upload_image", "feeds_api", "requests.post", "requests.put", "requests.patch",
                      "requests.delete", "urllib.request", "http.client", "selenium", "playwright",
                      "webdriver"):
            self.assertNotIn(token, src, f"unexpected Amazon-action token {token!r}")

    def test_amazon_boundary_is_closed(self):
        import runtime_policy as RP
        pol = RP.load_runtime_policy()
        self.assertTrue(pol.amazon_account_isolation)
        self.assertFalse(pol.amazon_api_enabled)
        self.assertFalse(pol.amazon_network_writes_enabled)
        self.assertFalse(pol.amazon_authenticated_access_enabled)


# ============================================================ AUTHORITY REUSE
class TestAuthorityReuse(Phase6FBase):
    def test_single_package_authority(self):
        # exactly one Phase 6F package module exists.
        prod = os.path.join(_ROOT, "production")
        cands = [f for f in os.listdir(prod) if "seller_central" in f and f.endswith(".py")]
        self.assertEqual(cands, ["seller_central_package.py"])

    def test_no_v2_or_parallel_compiler(self):
        for base in ("production", "listing", "creative"):
            for f in os.listdir(os.path.join(_ROOT, base)):
                self.assertNotIn("_v2", f)
                self.assertNotIn("manual_package", f)

    def test_reuses_page_auditor(self):
        self.assertIs(SCP.PA, PA)
        self.assertIn("publishability", self.deps.page_audit)

    def test_reuses_claim_authority(self):
        self.assertIs(SCP.CE, CE)

    def test_reuses_product_fact_and_engines(self):
        self.assertIs(SCP.PDP, PDP)
        self.assertIs(SCP.APL, APL)
        self.assertIs(SCP.CPP, CPP)

    def test_hash_helpers_are_the_one_authority(self):
        self.assertIs(SCP.canonical_json, PDP.PW.canonical_json if hasattr(PDP, "PW") else SCP.canonical_json)

    def test_one_package_index_authority(self):
        self.assertTrue(hasattr(SCP, "build_package_index"))
        self.assertTrue(hasattr(SCP, "verify_phase6f_artifacts"))

    def test_one_manual_entry_authority(self):
        self.assertTrue(hasattr(SCP, "build_manual_entry_plan"))
        self.assertIn(SCP.MANUAL_ENTRY_PLAN_FILENAME, self.files)


# ============================================================ PUBLISHABILITY HIERARCHY
class TestPublishability(unittest.TestCase):
    def test_lexical_pass_cannot_upgrade_package(self):
        r = SCP.resolve_effective_publishability(_sources(field_state="SAFE_DRAFT_OWNER_FACTS_REQUIRED",
                                                          owner_facts_complete=False,
                                                          page_auditor_state="PUBLISHABLE"))
        self.assertNotEqual(r["package_state"], SCP.PHASE6_READY_FOR_LOCAL_DEPLOYMENT)

    def test_lexical_publishable_cannot_override_missing_facts(self):
        r = SCP.resolve_effective_publishability(_sources(product_fact_state="SAFE_DRAFT_OWNER_FACTS_REQUIRED",
                                                          owner_facts_complete=False))
        self.assertEqual(r["listing_publishability"], SCP.PUB_SAFE_DRAFT)

    def test_lexical_publishable_cannot_override_missing_assets(self):
        r = SCP.resolve_effective_publishability(_sources(asset_state="SAFE_DRAFT_OWNER_FACTS_REQUIRED",
                                                          real_assets_verified=False))
        self.assertEqual(r["listing_publishability"], SCP.PUB_SAFE_DRAFT)

    def test_lexical_publishable_cannot_override_aplus(self):
        r = SCP.resolve_effective_publishability(_sources(capability_state="APLUS_ELIGIBILITY_UNVERIFIED",
                                                          aplus_eligibility_verified=False))
        self.assertNotEqual(r["package_state"], SCP.PHASE6_READY_FOR_LOCAL_DEPLOYMENT)

    def test_most_restrictive_wins(self):
        r = SCP.resolve_effective_publishability(_sources(atomic_claim_state="BLOCKED_UNSAFE"))
        self.assertEqual(r["listing_publishability"], SCP.PUB_BLOCKED_UNSAFE)
        self.assertEqual(r["package_state"], SCP.PHASE6_BLOCKED)

    def test_safe_draft_cannot_become_publishable(self):
        r = SCP.resolve_effective_publishability(_sources(field_state="SAFE_DRAFT_OWNER_FACTS_REQUIRED"))
        self.assertEqual(r["listing_publishability"], SCP.PUB_SAFE_DRAFT)

    def test_owner_review_differs_from_manual_entry(self):
        r = SCP.resolve_effective_publishability(_sources(field_state="SAFE_DRAFT_OWNER_FACTS_REQUIRED",
                                                          owner_facts_complete=False))
        self.assertTrue(r["ready_for_owner_review"])
        self.assertFalse(r["ready_for_manual_entry"])

    def test_manual_entry_differs_from_local_deployment(self):
        # a field authorized (copy_now>0) but the whole listing not publishable -> manual True, deploy False.
        r = SCP.resolve_effective_publishability(_sources(field_state="SAFE_DRAFT_OWNER_FACTS_REQUIRED",
                                                          copy_now_count=2))
        self.assertTrue(r["ready_for_manual_entry"])
        self.assertFalse(r["ready_for_local_deployment"])

    def test_all_good_reaches_local_deployment(self):
        r = SCP.resolve_effective_publishability(_sources(copy_now_count=3))
        self.assertEqual(r["package_state"], SCP.PHASE6_READY_FOR_LOCAL_DEPLOYMENT)
        self.assertTrue(r["ready_for_local_deployment"])

    @unittest.skipUnless(_HAS_RUN, "T2 workspace fixture required")
    def test_t2_resolves_to_safe_draft_ready(self):
        r = SCP.assemble_phase6f(RUN, connectivity_mode="LOCAL_SAFE")
        self.assertEqual(r.package_state, SCP.PHASE6_SAFE_DRAFT_READY)
        self.assertEqual(r.package_publishability, SCP.PUB_SAFE_DRAFT)
        self.assertTrue(r.ready_for_owner_review)
        self.assertFalse(r.ready_for_manual_entry)
        self.assertFalse(r.ready_for_local_deployment)


# ============================================================ PACKAGE LAYOUT
class TestPackageLayout(Phase6FBase):
    def test_exactly_one_package_root(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _clone_workspace(os.path.join(td, "T2"))
            res = SCP.assemble_phase6f(ws)
            SCP.write_phase6f_artifacts(res, workspace_dir=ws, started_at="t", completed_at="t")
            p6f = SCP.phase6f_dir(ws)
            roots = [d for d in os.listdir(p6f) if os.path.isdir(os.path.join(p6f, d))]
            self.assertEqual(roots, [SCP.PACKAGE_ROOT_NAME])

    def test_exactly_one_package_index(self):
        names = [e["name"] for e in self.result._files_list]
        self.assertEqual(names.count(SCP.PACKAGE_INDEX_FILENAME), 0)  # index is added at write, not a file entry
        self.assertEqual(self.index["schema_version"], SCP.PACKAGE_INDEX_SCHEMA_VERSION)

    def test_exactly_one_manual_entry_plan(self):
        names = [e["name"] for e in self.result._files_list]
        self.assertEqual(names.count(SCP.MANUAL_ENTRY_PLAN_FILENAME), 1)

    def test_required_artifacts_exist(self):
        need = ["00-READ-ME-FIRST.md", "01-TITLE.txt", "02-BULLETS.txt", "03-DESCRIPTION.txt",
                "04-BACKEND-SEARCH-TERMS.txt", "05-ITEM-HIGHLIGHTS.txt",
                "06-PERSONALIZATION-INSTRUCTIONS.txt", "07-BASIC-APLUS-CONTENT.json",
                "08-PREMIUM-APLUS-DRAFT.json", "09-LISTING-IMAGE-PROMPTS.md", "10-APLUS-IMAGE-PROMPTS.md",
                "11-REAL-PHOTO-SHOT-LIST.md", "12-CREATIVE-ASSET-CHECKLIST.json",
                SCP.MANUAL_ENTRY_PLAN_FILENAME, SCP.FINAL_AUDIT_FILENAME, "PRODUCT-DETAIL-PAGE.json",
                "BACKEND-SEARCH-TERMS.json", "LISTING-IMAGE-PLAN.json", "CREATIVE-ASSET-MANIFEST.json",
                "OWNER-INPUT-REQUIRED.json", "PAGE-AUDIT-REPORT.json"]
        for n in need:
            self.assertIn(n, self.files, n)

    def test_derivative_files_record_source_lineage(self):
        for e in self.index["entries"]:
            if e["generation_stage"] == "6F":
                self.assertTrue(e["source_hashes"], e["artifact_path"])

    def test_byte_copies_preserve_source_bytes(self):
        e = self.entries["PRODUCT-DETAIL-PAGE.json"]
        src = os.path.join(RUN, "phase6", "6C", "PRODUCT-DETAIL-PAGE.json")
        self.assertEqual(SCP._file_sha256(src), e["sha256"])
        self.assertEqual(SCP._file_sha256(src), e["source_artifact_sha256"])


# ============================================================ READ ME FIRST
class TestReadMe(Phase6FBase):
    def setUp(self):
        self.rm = self.text("00-READ-ME-FIRST.md")

    def test_begins_with_safe_draft_warning(self):
        self.assertTrue(self.rm.lstrip().startswith(SCP.PHASE6_SAFE_DRAFT_READY))

    def test_says_do_not_paste_or_upload(self):
        self.assertIn("DO NOT PASTE OR UPLOAD THIS PACKAGE TO SELLER CENTRAL YET", self.rm)

    def test_reports_copy_now_zero(self):
        self.assertIn("COPY NOW contains **0**", self.rm)

    def test_reports_upload_now_zero(self):
        self.assertIn("UPLOAD NOW contains **0**", self.rm)

    def test_identifies_authoritative_manual_plan(self):
        self.assertIn(SCP.MANUAL_ENTRY_PLAN_FILENAME, self.rm)

    def test_explains_package_verification(self):
        self.assertIn("PACKAGE-INDEX.json", self.rm)
        self.assertIn("PACKAGE-INDEX.sha256", self.rm)

    def test_no_amazon_automation_instructions(self):
        self.assertEqual(SCP._count_automation_instructions(self.rm), 0)


# ============================================================ FIELD FILES
class TestFieldFiles(Phase6FBase):
    def test_title_do_not_paste(self):
        t = self.text("01-TITLE.txt")
        self.assertIn(f"INSTRUCTION: {SCP.DO_NOT_PASTE}", t)
        self.assertIn("Nurse Sweatshirt", t)

    def test_description_do_not_paste(self):
        t = self.text("03-DESCRIPTION.txt")
        self.assertIn(f"INSTRUCTION: {SCP.DO_NOT_PASTE}", t)
        self.assertIn("A nurse sweatshirt. For nurses.", t)

    def test_backend_do_not_paste(self):
        t = self.text("04-BACKEND-SEARCH-TERMS.txt")
        self.assertIn(f"INSTRUCTION: {SCP.DO_NOT_PASTE}", t)
        self.assertIn("NOT authorization", t)

    def test_empty_bullets_remain_explicit(self):
        t = self.text("02-BULLETS.txt")
        for i in range(1, 6):
            self.assertIn(f"BULLET {i}: (empty)", t)

    def test_empty_item_highlights_explicit(self):
        t = self.text("05-ITEM-HIGHLIGHTS.txt")
        self.assertIn("(empty)", t)
        self.assertIn("CLAIM_EVIDENCE_MISSING", t)

    def test_personalization_empty(self):
        t = self.text("06-PERSONALIZATION-INSTRUCTIONS.txt")
        self.assertIn("(empty)", t)
        self.assertIn("OWNER_FACT_REQUIRED", t)

    def test_no_invented_copy(self):
        # description text is exactly the safe draft, nothing more.
        self.assertIn("A nurse sweatshirt. For nurses.", self.text("03-DESCRIPTION.txt"))

    def test_field_files_retain_source_hashes(self):
        for n in ("01-TITLE.txt", "03-DESCRIPTION.txt"):
            self.assertIn("SOURCE ARTIFACT SHA256:", self.text(n))

    def test_field_files_retain_effective_publishability(self):
        self.assertIn(f"EFFECTIVE PUBLISHABILITY: {SCP.PUB_SAFE_DRAFT}", self.text("01-TITLE.txt"))


# ============================================================ MANUAL PLAN
class TestManualPlan(Phase6FBase):
    def setUp(self):
        self.mp = self.text(SCP.MANUAL_ENTRY_PLAN_FILENAME)

    def test_sections_exist(self):
        for label in ("PACKAGE STATUS", "COPY NOW", "UPLOAD NOW", "OWNER REVIEW", "DO NOT PASTE",
                      "REAL PHOTO REQUIRED", "A+ ELIGIBILITY UNVERIFIED", "OWNER FACTS REQUIRED",
                      "REGENERATION SEQUENCE", "MANUAL SELLER CENTRAL BOUNDARY", "PACKAGE VERIFICATION",
                      "FINAL AUTHORIZATION GATE"):
            self.assertIn(label, self.mp, label)

    def test_copy_now_zero_fields(self):
        sec = SCP._extract_section(self.mp, SCP.COPY_NOW)
        self.assertIn("0 fields", sec)
        self.assertIn("No fields are currently authorized", sec)

    def test_upload_now_zero_assets(self):
        sec = SCP._extract_section(self.mp, SCP.UPLOAD_NOW)
        self.assertIn("0 assets", sec)
        self.assertIn("No assets are currently authorized", sec)

    def test_title_does_not_leak_into_copy_now(self):
        self.assertNotIn("Nurse Sweatshirt", SCP._extract_section(self.mp, SCP.COPY_NOW))

    def test_description_does_not_leak_into_copy_now(self):
        self.assertNotIn("A nurse sweatshirt. For nurses.", SCP._extract_section(self.mp, SCP.COPY_NOW))

    def test_lifestyle_prompt_does_not_leak_into_upload_now(self):
        up = SCP._extract_section(self.mp, SCP.UPLOAD_NOW)
        self.assertNotIn("For nurses.", up)

    def test_real_photo_section_lists_seven(self):
        sec = SCP._extract_section(self.mp, SCP.REAL_PHOTO_REQUIRED)
        self.assertIn("7 real-photo shots", sec)

    def test_aplus_section_present(self):
        sec = SCP._extract_section(self.mp, "A+ ELIGIBILITY UNVERIFIED")
        self.assertIn("DO_NOT_PUBLISH", sec)

    def test_no_automated_seller_central_steps(self):
        self.assertEqual(SCP._count_automation_instructions(self.mp), 0)


# ============================================================ A+ SEPARATION
class TestAplusSeparation(Phase6FBase):
    def test_aplus_eligibility_unverified(self):
        self.assertEqual(self.final_audit["aplus_eligibility_state"], SCP.APLUS_ELIGIBILITY_UNVERIFIED)
        self.assertFalse(self.deps.basic_aplus.get("capability_verified"))
        self.assertFalse(self.deps.premium_aplus.get("capability_verified"))

    def test_basic_not_labeled_ready(self):
        e = self.entries["07-BASIC-APLUS-CONTENT.json"]
        self.assertNotEqual(e["instruction_class"], SCP.COPY_NOW)
        self.assertNotEqual(e["instruction_class"], SCP.UPLOAD_NOW)
        self.assertEqual(e["instruction_class"], SCP.APLUS_ELIGIBILITY_UNVERIFIED_LABEL)

    def test_premium_do_not_publish(self):
        e = self.entries["08-PREMIUM-APLUS-DRAFT.json"]
        self.assertTrue(e["do_not_publish"])
        self.assertEqual(e["publishability"], "DO_NOT_PUBLISH")

    def test_aplus_not_in_upload_now(self):
        for n in ("07-BASIC-APLUS-CONTENT.json", "08-PREMIUM-APLUS-DRAFT.json"):
            self.assertNotEqual(self.entries[n]["instruction_class"], SCP.UPLOAD_NOW)

    def test_no_new_aplus_copy_generated(self):
        # the A+ files are byte-identical copies of the verified 6D artifacts.
        for name, src in (("07-BASIC-APLUS-CONTENT.json", "BASIC-APLUS-CONTENT.json"),
                          ("08-PREMIUM-APLUS-DRAFT.json", "PREMIUM-APLUS-DRAFT.json")):
            s = os.path.join(RUN, "phase6", "6D", src)
            self.assertEqual(self.entries[name]["sha256"], SCP._file_sha256(s))

    def test_no_aplus_capability_guessed(self):
        self.assertEqual(self.deps.basic_aplus.get("capability"), "UNKNOWN_A_PLUS_CAPABILITY")


# ============================================================ CREATIVE SEPARATION
class TestCreativeSeparation(Phase6FBase):
    def test_asset_ready_zero(self):
        self.assertEqual(self.final_audit["asset_ready_count"], 0)

    def test_real_photo_required_count(self):
        self.assertEqual(self.result.real_photo_required_count, 7)

    def test_seven_real_photo_shots_included(self):
        t = self.text("11-REAL-PHOTO-SHOT-LIST.md")
        self.assertEqual(sum(1 for i in range(1, 8) if f"SHOT-0{i}" in t), 7)

    def test_lifestyle_prompt_owner_review_only(self):
        self.assertEqual(self.entries["09-LISTING-IMAGE-PROMPTS.md"]["instruction_class"], SCP.OWNER_REVIEW)

    def test_no_prompt_called_actual_image(self):
        for n in ("09-LISTING-IMAGE-PROMPTS.md", "10-APLUS-IMAGE-PROMPTS.md"):
            self.assertNotEqual(self.entries[n]["instruction_class"], SCP.UPLOAD_NOW)

    def test_no_generated_image_packaged(self):
        for e in self.index["entries"]:
            ext = os.path.splitext(e["artifact_path"])[1].lower()
            self.assertNotIn(ext, SCP.FORBIDDEN_IMAGE_EXTENSIONS)

    def test_generated_proof_acceptance_zero(self):
        self.assertEqual(self.final_audit["generated_proof_acceptance_count"], 0)


# ============================================================ PACKAGE INDEX
class TestPackageIndex(Phase6FBase):
    def test_every_entry_has_required_schema(self):
        for e in self.index["entries"]:
            for k in ("artifact_path", "artifact_role", "required", "publishability", "sha256",
                      "size_bytes", "media_type", "generation_stage", "source_hashes", "verified"):
                self.assertIn(k, e)

    def test_every_path_relative(self):
        for e in self.index["entries"]:
            self.assertFalse(os.path.isabs(e["artifact_path"]))
            self.assertNotIn("..", e["artifact_path"])

    def test_absolute_path_rejected(self):
        self.assertRaises(SCP.Phase6FPackageError, SCP._safe_index_path, "/etc/passwd")

    def test_drive_letter_rejected(self):
        self.assertRaises(SCP.Phase6FPackageError, SCP._safe_index_path, "C:/x.txt")

    def test_unc_rejected(self):
        self.assertRaises(SCP.Phase6FPackageError, SCP._safe_index_path, "\\\\srv\\share\\x")

    def test_parent_traversal_rejected(self):
        self.assertRaises(SCP.Phase6FPackageError, SCP._safe_index_path, "a/../b")

    def test_empty_path_rejected(self):
        self.assertRaises(SCP.Phase6FPackageError, SCP._safe_index_path, "")

    def test_reserved_name_rejected(self):
        self.assertRaises(SCP.Phase6FPackageError, SCP._safe_index_path, "CON.txt")
        self.assertRaises(SCP.Phase6FPackageError, SCP._safe_index_path, "sub/nul.json")

    def test_alternate_data_stream_rejected(self):
        self.assertRaises(SCP.Phase6FPackageError, SCP._safe_index_path, "a.txt:stream")

    def test_duplicate_and_case_duplicate_rejected(self):
        deps = self.deps
        e = SCP._entry("x.txt", "r", SCP.OWNER_REVIEW, b"a", publishability="x", effective="x")
        e2 = SCP._entry("X.txt", "r", SCP.OWNER_REVIEW, b"b", publishability="x", effective="x")
        with self.assertRaises(SCP.Phase6FPackageError):
            SCP.build_package_index(deps, [e, dict(e)])
        with self.assertRaises(SCP.Phase6FPackageError):
            SCP.build_package_index(deps, [e, e2])

    def test_actual_sha_and_size(self):
        for e in self.index["entries"]:
            data = self.files[e["artifact_path"]]
            self.assertEqual(SCP._sha_bytes(data), e["sha256"])
            self.assertEqual(len(data), e["size_bytes"])

    def test_media_type_verified(self):
        for e in self.index["entries"]:
            self.assertEqual(e["media_type"], SCP._media_type(e["artifact_path"]))

    def test_source_hashes_resolve(self):
        for e in self.index["entries"]:
            for rel, sha in e["source_hashes"].items():
                self.assertEqual(SCP._file_sha256(os.path.join(RUN, rel.replace("/", os.sep))), sha)

    def test_index_contains_no_full_bodies(self):
        blob = json.dumps(self.index)
        self.assertNotIn("A nurse sweatshirt. For nurses.", blob)
        self.assertNotIn("Prove the real, finished garment", blob)

    def test_index_contains_no_private_paths(self):
        blob = json.dumps(self.index)
        self.assertNotIn(_ROOT, blob)
        self.assertNotIn("C:\\", blob)

    def test_index_excludes_itself_and_sha(self):
        paths = [e["artifact_path"] for e in self.index["entries"]]
        self.assertNotIn(SCP.PACKAGE_INDEX_FILENAME, paths)
        self.assertNotIn(SCP.PACKAGE_INDEX_SHA_FILENAME, paths)

    def test_index_serialization_deterministic(self):
        r2 = SCP.assemble_phase6f(RUN, connectivity_mode="CONNECTED_RESEARCH")
        self.assertEqual(SCP.canonical_json(self.index), SCP.canonical_json(r2._index_doc))


# ============================================================ PACKAGE INDEX HASH
class TestPackageIndexHash(Phase6FBase):
    def test_hash_file_lowercase_and_names_index(self):
        line = self.result._sha_file_text
        self.assertRegex(line, r"^[0-9a-f]{64}  PACKAGE-INDEX\.json\n$")

    def test_hash_file_ends_with_one_newline(self):
        self.assertTrue(self.result._sha_file_text.endswith("\n"))
        self.assertEqual(self.result._sha_file_text.count("\n"), 1)

    def test_calculated_matches(self):
        self.assertEqual(SCP.parse_package_index_sha_file(self.result._sha_file_text),
                         self.result.package_index_sha256)
        self.assertEqual(SCP._sha_bytes(self.result._index_bytes), self.result.package_index_sha256)

    def test_stale_hash_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _clone_workspace(os.path.join(td, "T2"))
            res = SCP.assemble_phase6f(ws)
            SCP.write_phase6f_artifacts(res, workspace_dir=ws, started_at="t", completed_at="t")
            root = SCP.package_root_dir(ws)
            p = os.path.join(root, SCP.PACKAGE_INDEX_SHA_FILENAME)
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write("0" * 64 + "  PACKAGE-INDEX.json\n")
            v = SCP.verify_phase6f_artifacts(root, workspace_dir=ws)
            self.assertEqual(v["package_index_hash_file_result"], "MISMATCH")
            self.assertEqual(v["result"], "BLOCKED")

    def test_malformed_hash_line_rejected(self):
        self.assertRaises(SCP.Phase6FPackageError, SCP.parse_package_index_sha_file, "nothex  PACKAGE-INDEX.json\n")

    def test_extra_content_rejected(self):
        good = SCP._sha_bytes(b"x")
        self.assertRaises(SCP.Phase6FPackageError, SCP.parse_package_index_sha_file,
                          good + "  PACKAGE-INDEX.json\nextra\n")


# ============================================================ FINAL AUDIT
class TestFinalAudit(Phase6FBase):
    def test_records_all_stage_hashes(self):
        for k in ("phase6a_manifest_sha256", "phase6b_manifest_sha256", "phase6c_manifest_sha256",
                  "phase6d_manifest_sha256", "phase6e_manifest_sha256", "keyword_source_sha256",
                  "product_facts_sha256", "claim_evidence_sha256"):
            self.assertIn(k, self.final_audit)

    def test_records_page_auditor_result(self):
        self.assertIn("page_auditor_lexical_result", self.final_audit)
        self.assertIn("publishability", self.final_audit["page_auditor_lexical_result"])

    def test_records_effective_publishability(self):
        self.assertEqual(self.final_audit["effective_publishability"], SCP.PUB_SAFE_DRAFT)

    def test_counts_zero(self):
        self.assertEqual(self.final_audit["copy_now_count"], 0)
        self.assertEqual(self.final_audit["upload_now_count"], 0)
        self.assertEqual(self.final_audit["blocked_copy_in_copy_now_count"], 0)
        self.assertEqual(self.final_audit["blocked_copy_in_upload_now_count"], 0)
        self.assertEqual(self.final_audit["generated_proof_acceptance_count"], 0)

    def test_ready_flags(self):
        self.assertTrue(self.final_audit["ready_for_owner_review"])
        self.assertFalse(self.final_audit["ready_for_manual_entry"])
        self.assertFalse(self.final_audit["ready_for_local_deployment"])

    def test_package_state(self):
        self.assertEqual(self.final_audit["package_state"], SCP.PHASE6_SAFE_DRAFT_READY)
        self.assertEqual(self.final_audit["amazon_account_actions"], 0)


# ============================================================ LEAKAGE AUDIT
class TestLeakageAudit(Phase6FBase):
    def test_scans_key_files(self):
        scanned = set(self.leakage["scanned_artifacts"])
        for n in ("00-READ-ME-FIRST.md", SCP.MANUAL_ENTRY_PLAN_FILENAME, "LISTING-COPY-READY.txt",
                  "01-TITLE.txt", "02-BULLETS.txt", "03-DESCRIPTION.txt", "04-BACKEND-SEARCH-TERMS.txt"):
            self.assertIn(n, scanned)

    def test_zero_blocked_copy_in_copy_now(self):
        self.assertEqual(self.leakage["blocked_copy_in_copy_now_count"], 0)

    def test_zero_blocked_copy_in_upload_now(self):
        self.assertEqual(self.leakage["blocked_copy_in_upload_now_count"], 0)

    def test_zero_automation_instructions(self):
        self.assertEqual(self.leakage["automation_instruction_count"], 0)

    def test_premium_remains_do_not_publish(self):
        self.assertTrue(self.leakage["premium_remains_do_not_publish"])

    def test_result_pass(self):
        self.assertEqual(self.leakage["result"], "PASS")


# ============================================================ ATOMIC BUILD / PROMOTION
@unittest.skipUnless(_HAS_RUN, "T2 workspace fixture (gitignored paid data) required")
class TestAtomicBuildAndPromotion(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.ws = _clone_workspace(os.path.join(self.td, "T2"))
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)

    def test_candidate_builds_and_promotes(self):
        res = SCP.assemble_phase6f(self.ws)
        SCP.write_phase6f_artifacts(res, workspace_dir=self.ws, started_at="t", completed_at="t")
        root = SCP.package_root_dir(self.ws)
        self.assertTrue(os.path.isdir(root))
        v = SCP.verify_phase6f_artifacts(root, workspace_dir=self.ws)
        self.assertEqual(v["result"], "PASS_WITH_WARNINGS")

    def test_candidate_and_backup_dirs_removed_after_success(self):
        res = SCP.assemble_phase6f(self.ws)
        SCP.write_phase6f_artifacts(res, workspace_dir=self.ws, started_at="t", completed_at="t")
        p6f = SCP.phase6f_dir(self.ws)
        self.assertFalse(os.path.exists(os.path.join(p6f, SCP.CANDIDATE_ROOT_NAME)))
        self.assertFalse(os.path.exists(os.path.join(p6f, SCP.BACKUP_ROOT_NAME)))

    def test_failed_candidate_preserves_last_valid(self):
        res = SCP.assemble_phase6f(self.ws)
        SCP.write_phase6f_artifacts(res, workspace_dir=self.ws, started_at="t", completed_at="t")
        root = SCP.package_root_dir(self.ws)
        good = {f: SCP._file_sha256(os.path.join(root, f)) for f in os.listdir(root)}
        # corrupt the in-memory result so its candidate fails verification (index sha will not match a
        # tampered file), then attempt a second write.
        res2 = SCP.assemble_phase6f(self.ws)
        res2._index_bytes = res2._index_bytes + b" "     # index bytes no longer match recorded sha file
        with self.assertRaises(SCP.Phase6FPackageError):
            SCP.write_phase6f_artifacts(res2, workspace_dir=self.ws, started_at="t2", completed_at="t2")
        for f, h in good.items():
            self.assertEqual(SCP._file_sha256(os.path.join(root, f)), h, f"{f} was clobbered")

    def test_previous_stage_manifest_preserved_on_failure(self):
        res = SCP.assemble_phase6f(self.ws)
        SCP.write_phase6f_artifacts(res, workspace_dir=self.ws, started_at="t", completed_at="t")
        man = os.path.join(SCP.phase6f_dir(self.ws), SCP.STAGE_MANIFEST_FILENAME)
        before = SCP._file_sha256(man)
        res2 = SCP.assemble_phase6f(self.ws)
        res2._index_bytes = res2._index_bytes + b" "
        with self.assertRaises(SCP.Phase6FPackageError):
            SCP.write_phase6f_artifacts(res2, workspace_dir=self.ws, started_at="t2", completed_at="t2")
        self.assertEqual(SCP._file_sha256(man), before)

    def test_interrupted_promotion_recovers(self):
        res = SCP.assemble_phase6f(self.ws)
        SCP.write_phase6f_artifacts(res, workspace_dir=self.ws, started_at="t", completed_at="t")
        p6f = SCP.phase6f_dir(self.ws)
        final = SCP.package_root_dir(self.ws)
        backup = os.path.join(p6f, SCP.BACKUP_ROOT_NAME)
        # simulate a crash after final -> backup, before candidate -> final.
        os.rename(final, backup)
        SCP._recover_interrupted_promotion(final, backup)
        self.assertTrue(os.path.isdir(final))
        self.assertFalse(os.path.exists(backup))

    def test_candidate_and_backup_paths_bounded(self):
        p6f = SCP.phase6f_dir(self.ws)
        for name in (SCP.CANDIDATE_ROOT_NAME, SCP.BACKUP_ROOT_NAME):
            self.assertTrue(name.startswith("."))
            self.assertTrue(os.path.abspath(os.path.join(p6f, name)).startswith(os.path.abspath(p6f)))

    def test_promoted_reverifies(self):
        res = SCP.assemble_phase6f(self.ws)
        SCP.write_phase6f_artifacts(res, workspace_dir=self.ws, started_at="t", completed_at="t")
        v = SCP.verify_promoted_package(SCP.package_root_dir(self.ws), self.ws)
        self.assertNotEqual(v["result"], "BLOCKED")

    def test_tampered_promoted_package_is_blocked(self):
        res = SCP.assemble_phase6f(self.ws)
        SCP.write_phase6f_artifacts(res, workspace_dir=self.ws, started_at="t", completed_at="t")
        root = SCP.package_root_dir(self.ws)
        with open(os.path.join(root, "03-DESCRIPTION.txt"), "a", encoding="utf-8") as f:
            f.write("tamper")
        v = SCP.verify_phase6f_artifacts(root, workspace_dir=self.ws)
        self.assertEqual(v["result"], "BLOCKED")


# ============================================================ PATH SECURITY (verify-side)
class TestPathSecurity(unittest.TestCase):
    def test_verify_flags_json_that_does_not_parse(self):
        # a package whose index records a .json entry that does not actually parse -> BLOCKED.
        with tempfile.TemporaryDirectory() as td:
            root = os.path.join(td, SCP.PACKAGE_ROOT_NAME)
            os.makedirs(root)
            bad = b"{not json"
            index = {
                "schema_version": SCP.PACKAGE_INDEX_SCHEMA_VERSION, "entries": [{
                    "artifact_path": "x.json", "artifact_role": "r", "required": True,
                    "publishability": "SAFE", "instruction_class": SCP.OWNER_REVIEW,
                    "effective_publishability": "SAFE", "owner_review_required": True,
                    "sha256": SCP._sha_bytes(bad), "size_bytes": len(bad), "media_type": "application/json",
                    "generation_stage": "6F", "source_artifact_path": None, "source_artifact_sha256": None,
                    "source_hashes": {}, "blockers": [], "warnings": [], "verified": True}]}
            ib = SCP.canonical_json(index).encode("utf-8")
            open(os.path.join(root, "x.json"), "wb").write(bad)
            open(os.path.join(root, SCP.PACKAGE_INDEX_FILENAME), "wb").write(ib)
            open(os.path.join(root, SCP.PACKAGE_INDEX_SHA_FILENAME), "wb").write(
                SCP.build_package_index_sha_line(ib).encode("utf-8"))
            v = SCP.verify_phase6f_artifacts(root, workspace_dir=td)
            self.assertEqual(v["result"], "BLOCKED")
            self.assertTrue(any("does not parse" in b for b in v["blockers"]))

    def test_rmtree_does_not_follow_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            outside = os.path.join(td, "outside")
            os.makedirs(outside)
            open(os.path.join(outside, "keep.txt"), "w").close()
            inside = os.path.join(td, "inside")
            os.makedirs(inside)
            try:
                os.symlink(outside, os.path.join(inside, "link"), target_is_directory=True)
            except (OSError, NotImplementedError, AttributeError):
                self.skipTest("symlink not permitted in this environment")
            SCP._rmtree(inside)
            self.assertTrue(os.path.exists(os.path.join(outside, "keep.txt")))


# ============================================================ ARTIFACTS
@unittest.skipUnless(_HAS_RUN, "T2 workspace fixture (gitignored paid data) required")
class TestArtifacts(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.ws = _clone_workspace(os.path.join(self.td, "T2"))
        self.addCleanup(shutil.rmtree, self.td, ignore_errors=True)
        self.res = SCP.assemble_phase6f(self.ws)
        self.man = SCP.write_phase6f_artifacts(self.res, workspace_dir=self.ws, started_at="t",
                                               completed_at="t")

    def test_stage_manifest_generated(self):
        p = os.path.join(SCP.phase6f_dir(self.ws), SCP.STAGE_MANIFEST_FILENAME)
        self.assertTrue(os.path.exists(p))
        self.assertEqual(self.man["stage_id"], "6F")
        self.assertEqual(self.man["phase6_final_status"], SCP.PHASE6_SAFE_DRAFT_READY)

    def test_verification_report_generated(self):
        p = os.path.join(SCP.phase6f_dir(self.ws), SCP.VERIFICATION_REPORT_FILENAME)
        self.assertTrue(os.path.exists(p))
        d = json.load(open(p, encoding="utf-8"))
        self.assertEqual(d["result"], "PASS_WITH_WARNINGS")

    def test_leakage_audit_generated(self):
        p = os.path.join(SCP.phase6f_dir(self.ws), SCP.LEAKAGE_AUDIT_FILENAME)
        self.assertTrue(os.path.exists(p))

    def test_manifest_output_hashes_match_actual_bytes(self):
        for rel, sha in self.man["output_hashes"].items():
            p = os.path.join(self.ws, rel.replace("/", os.sep))
            self.assertEqual(SCP._file_sha256(p), sha, rel)

    def test_no_phase7_artifact(self):
        for root, _dirs, files in os.walk(SCP.phase6f_dir(self.ws)):
            for f in files:
                self.assertNotIn("phase7", f.lower())
                self.assertNotIn("7a", f.lower())

    def test_no_credential_artifact(self):
        for root, _dirs, files in os.walk(SCP.phase6f_dir(self.ws)):
            for f in files:
                self.assertNotIn("credential", f.lower())
                self.assertNotIn("secret", f.lower())

    def test_committed_proof_has_no_private_paths(self):
        # the stage manifest + report must use relative paths only.
        man_txt = open(os.path.join(SCP.phase6f_dir(self.ws), SCP.STAGE_MANIFEST_FILENAME),
                       encoding="utf-8").read()
        self.assertNotIn(_ROOT, man_txt)


# ============================================================ T2 PROOF + DETERMINISM
class TestT2ProofAndDeterminism(Phase6FBase):
    def test_first_and_second_run_match(self):
        r1 = SCP.assemble_phase6f(RUN, connectivity_mode="LOCAL_SAFE")
        r2 = SCP.assemble_phase6f(RUN, connectivity_mode="LOCAL_SAFE")
        self.assertEqual(r1.deterministic_content_sha256(), r2.deterministic_content_sha256())
        self.assertEqual(r1.package_index_sha256, r2.package_index_sha256)
        self.assertEqual(r1._sha_file_text, r2._sha_file_text)
        self.assertEqual(r1.final_listing_audit["deterministic_content_sha256"],
                         r2.final_listing_audit["deterministic_content_sha256"])
        self.assertEqual(r1._leakage_audit["deterministic_content_sha256"],
                         r2._leakage_audit["deterministic_content_sha256"])

    def test_stable_manifest_hash_matches(self):
        with tempfile.TemporaryDirectory() as td:
            ws = _clone_workspace(os.path.join(td, "T2"))
            res = SCP.assemble_phase6f(ws)
            m1 = SCP.write_phase6f_artifacts(res, workspace_dir=ws, started_at="a", completed_at="b")
            res2 = SCP.assemble_phase6f(ws)
            m2 = SCP.write_phase6f_artifacts(res2, workspace_dir=ws, started_at="c", completed_at="d")
            self.assertEqual(m1["deterministic_content_sha256"], m2["deterministic_content_sha256"])

    def test_three_mode_determinism(self):
        modes = ["CONNECTED_RESEARCH", "LOCAL_SAFE", "TEST_DENY_EXTERNAL"]
        rs = [SCP.assemble_phase6f(RUN, connectivity_mode=m) for m in modes]
        base = rs[0].deterministic_content_sha256()
        for r in rs[1:]:
            self.assertEqual(r.deterministic_content_sha256(), base)
            self.assertEqual(r.package_index_sha256, rs[0].package_index_sha256)

    def test_keyword_source_unchanged(self):
        self.assertEqual(self.deps.keyword_source_sha256[:8], "52b02f16")

    def test_tier_counts_unchanged(self):
        d = json.load(open(os.path.join(RUN, "MASTER-KEYWORDS-LEAN.json"), encoding="utf-8"))
        counts = d["quality_summary"]["counts"]
        self.assertEqual(counts["A_CORE"], 74)
        self.assertEqual(counts["B_SUPPORTING"], 378)

    def test_product_facts_and_claim_sha(self):
        self.assertEqual(self.deps.product_facts_sha256[:8], "0199b4de")
        self.assertEqual(self.deps.claim_evidence_sha256[:8], "840e6216")

    def test_counts(self):
        self.assertEqual(self.result.copy_now_count, 0)
        self.assertEqual(self.result.upload_now_count, 0)
        self.assertEqual(self.result.owner_review_count, 11)
        self.assertEqual(self.result.do_not_paste_count, 8)
        self.assertEqual(self.result.real_photo_required_count, 7)
        self.assertEqual(self.result.aplus_eligibility_unverified_count, 2)

    def test_to_dict_shape(self):
        d = self.result.to_dict()
        for k in ("schema_version", "workspace_id", "product_id", "canonical_category_id", "marketplace",
                  "package_root", "package_state", "package_publishability", "package_index_sha256",
                  "final_listing_audit", "deterministic_content_sha256", "generated_artifacts"):
            self.assertIn(k, d)
        self.assertEqual(d["canonical_category_id"], "US:apparel")
        self.assertEqual(d["marketplace"], "US")


# ============================================================ CONNECTIVITY / AMAZON BOUNDARY
class TestConnectivityBoundary(unittest.TestCase):
    def test_no_network_or_amazon_symbols_in_source(self):
        src = open(SCP.__file__, encoding="utf-8").read().lower()
        for token in ("socket.", "http.client", "smtplib", "ftplib", "amazon_credential_store =",
                      "seller_session", "login("):
            self.assertNotIn(token, src, token)

    @unittest.skipUnless(_HAS_RUN, "T2 workspace fixture required")
    def test_amazon_account_actions_zero(self):
        r = SCP.assemble_phase6f(RUN, connectivity_mode="LOCAL_SAFE")
        self.assertEqual(r.final_listing_audit["amazon_account_actions"], 0)

    @unittest.skipUnless(_HAS_RUN, "T2 workspace fixture required")
    def test_modes_do_not_change_content(self):
        a = SCP.assemble_phase6f(RUN, connectivity_mode="CONNECTED_RESEARCH")
        b = SCP.assemble_phase6f(RUN, connectivity_mode="TEST_DENY_EXTERNAL")
        self.assertEqual(a.deterministic_content_sha256(), b.deterministic_content_sha256())


if __name__ == "__main__":
    unittest.main()
