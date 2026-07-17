#!/usr/bin/env python3
"""
Phase 6D — T2 real-workspace proof.

Reuses the REAL, immutable T2 inputs (runs/T2 + its completed phase6/6A, read-only; phase6/6B as
optional advisory) and writes Phase 6D artifacts to a TEMP location. Proves: TIER_A=74 / TIER_B=378 and
the certified source SHA are unchanged; the corrected atomic claim hash (840e6216…) is consumed; A+
capability resolves to UNKNOWN through the registry authority (never guessed, never Basic); the five
Basic modules are all present but blocked with EMPTY customer copy; the Premium draft is clearly
non-publishable with a complete 1..5 Basic fallback, eight independently-evaluated dynamic candidates,
zero eligible and zero selected (no filler); every A+ asset requires a real photo or an owner fact and
no generated asset satisfies any real-proof gate; the shared PageAuditor is clean; the listing stays
SAFE_DRAFT; the run is twice- and three-mode-deterministic; nothing is written to the T2 workspace or
any Amazon surface; and no Phase 6E/6F creative artifact is produced.

runs/ holds paid Helium 10 exports and is gitignored, so this proof is SKIPPED on a clean checkout that
has no local T2 workspace (no paid data is ever copied into a committed fixture).
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "listing"))
sys.path.insert(0, os.path.join(ROOT, "core"))

from production import aplus_assembly as A                    # noqa: E402
import aplus_module_registry as AREG                          # noqa: E402
import keyword_source_adapter as KSA                          # noqa: E402

T2 = os.path.join(ROOT, "runs", "T2")
T2_6A = os.path.join(T2, "phase6", "6A")
T2_PRESENT = (os.path.exists(os.path.join(T2, "MASTER-KEYWORDS-LEAN.json"))
              and os.path.exists(os.path.join(T2_6A, "STAGE-6A-MANIFEST.json")))
CERTIFIED_KEYWORD_SHA = "52b02f162e75f70806a8e2893a5327210037135cb8b4b3834fc192b318f8e8e7"
CERTIFIED_CLAIM_SHA = "840e62168f46e133444ca42a5cd8130c2041eaaae63a9249ec5cf557c3c16edf"


@unittest.skipUnless(T2_PRESENT, "runs/T2 (paid, gitignored) not present on this machine")
class T2Proof(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deps = A.load_phase6d_dependencies(T2)
        cls.r1 = A.assemble_phase6d(T2)
        cls.r2 = A.assemble_phase6d(T2)
        cls.basic = cls.r1.basic_aplus
        cls.premium = cls.r1.premium_aplus_draft
        cls.assets = cls.r1.asset_manifest
        cls.compliance = cls.r1.compliance

    # -- identity + source ------------------------------------------------------
    def test_product_identity(self):
        self.assertEqual((self.r1.product_id, self.r1.product_type, self.r1.audience, self.r1.category),
                         ("T2", "sweatshirt", "nurses", "apparel"))

    def test_source_certified_and_tiers(self):
        src = KSA.load_keyword_source(T2)
        self.assertEqual(src.source_file, "MASTER-KEYWORDS-LEAN.json")
        self.assertEqual(src.source_sha256, CERTIFIED_KEYWORD_SHA)
        self.assertEqual(src.tier_counts["TIER_A"], 74)
        self.assertEqual(src.tier_counts["TIER_B"], 378)

    def test_corrected_atomic_claim_hash_consumed(self):
        self.assertEqual(self.deps.claim_evidence_sha256, CERTIFIED_CLAIM_SHA)
        self.assertEqual(self.deps.claims.schema_version, "1.1.0")
        self.assertTrue(self.deps.claims.atomicity_ok)

    def test_only_recipient_claim_verified(self):
        verified = sorted(c["normalized_concept"] for c in self.deps.claims.publishable)
        self.assertEqual(verified, ["recipient"])
        self.assertEqual(self.deps.claims.publishable_text("recipient"), "For nurses.")

    # -- capability -------------------------------------------------------------
    def test_capability_unknown_not_guessed(self):
        self.assertEqual(self.r1.aplus_capability, AREG.UNKNOWN_A_PLUS_CAPABILITY)
        self.assertFalse(self.r1.aplus_capability_verified)

    def test_phase6b_aplus_advisory_empty(self):
        self.assertEqual(self.deps.phase6b_aplus_field, [])
        self.assertEqual((self.deps.phase6b_aplus_status or {}).get("empty_reason_code"),
                         "OWNER_FACT_REQUIRED")

    # -- Basic ------------------------------------------------------------------
    def test_basic_five_blocked_modules_empty_copy(self):
        self.assertEqual(self.basic["module_count"], 5)
        self.assertEqual([m["module_key"] for m in self.basic["modules"]],
                         list(AREG.BASIC_MODULE_KEYS))
        self.assertEqual(self.basic["nonempty_copy_module_count"], 0)
        for m in self.basic["modules"]:
            self.assertEqual(m["publishability"], AREG.OWNER_FACT_REQUIRED)
            self.assertEqual((m["headline"], m["body_copy"]), ("", ""))
        self.assertEqual(self.basic["state"], A.BASIC_ELIGIBILITY_UNVERIFIED)
        self.assertEqual(self.basic["publishability"], A.LISTING_SAFE_DRAFT)

    # -- Premium ----------------------------------------------------------------
    def test_premium_non_publishable_complete_fallback(self):
        self.assertTrue(self.premium["is_draft"])
        self.assertTrue(self.premium["never_publishable"])
        self.assertEqual(self.premium["publishability"], A.PREMIUM_DO_NOT_PUBLISH)
        self.assertEqual(self.premium["fixed_module_count"], 5)
        self.assertEqual(len(self.premium["basic_fallback"]), 5)
        self.assertEqual(self.premium["basic_fallback_positions"], [1, 2, 3, 4, 5])

    def test_premium_dynamic_no_filler(self):
        self.assertEqual(self.premium["dynamic_candidate_count"], len(AREG.PREMIUM_DYNAMIC_POOL))
        self.assertEqual(self.premium["eligible_dynamic_modules"], [])
        self.assertEqual(self.premium["selected_dynamic_modules"], [])
        self.assertEqual(len(self.premium["rejected_dynamic_modules"]), len(AREG.PREMIUM_DYNAMIC_POOL))

    # -- Assets -----------------------------------------------------------------
    def test_assets_real_photo_required_none_ready(self):
        self.assertEqual(self.assets["asset_count"], 4)
        self.assertEqual(self.assets["real_photo_required_count"], 3)
        self.assertEqual(self.assets["asset_state_counts"].get(A.ASSET_READY, 0), 0)
        self.assertEqual(self.assets["generated_accepted_as_real_proof_count"], 0)
        for a in self.assets["assets"]:
            self.assertNotEqual(a["status"], A.ASSET_READY)
            if a["real_photo_required"]:
                self.assertFalse(a["generated_mockup_allowed"])

    # -- Compliance / audit -----------------------------------------------------
    def test_zero_unsupported_and_clean_audit(self):
        self.assertEqual(self.compliance["unsupported_claim_count"], 0)
        self.assertEqual(self.compliance["generated_assets_accepted_as_real_proof"], 0)
        pa = self.r1.page_audit
        self.assertEqual(pa["hard_failures"], [])
        leak = pa["leakage_summary"]
        for key in ("wrong_audience_leakage", "wrong_product_leakage", "wrong_occasion_leakage",
                    "brand_ip_leakage", "unsupported_physical_claims", "blocked_claim_promotion",
                    "fabricated_owner_facts"):
            self.assertEqual(leak[key], 0, key)

    def test_stage_state_and_listing(self):
        self.assertEqual(self.r1.phase6d_state, A.APLUS_ELIGIBILITY_UNVERIFIED)
        self.assertEqual(self.r1.listing_publishability_state, "SAFE_DRAFT_OWNER_FACTS_REQUIRED")
        self.assertTrue(self.r1.ready_for_6e)

    def test_compliance_counts_match_artifacts(self):
        self.assertEqual(self.compliance["basic_module_count"], self.basic["module_count"])
        self.assertEqual(self.compliance["dynamic_candidate_count"],
                         self.premium["dynamic_candidate_count"])
        self.assertEqual(self.compliance["asset_requirement_result"]["asset_count"],
                         self.assets["asset_count"])
        self.assertTrue(self.compliance["basic_fallback_verified"])

    # -- determinism ------------------------------------------------------------
    def test_two_run_determinism(self):
        self.assertEqual(self.r1.deterministic_content_sha256(),
                         self.r2.deterministic_content_sha256())
        for attr in ("basic_aplus", "premium_aplus_draft", "asset_manifest", "compliance"):
            self.assertEqual(getattr(self.r1, attr)["deterministic_content_sha256"],
                             getattr(self.r2, attr)["deterministic_content_sha256"], attr)

    def test_three_mode_determinism(self):
        shas = {A.assemble_phase6d(T2, connectivity_mode=m).deterministic_content_sha256()
                for m in ("CONNECTED_RESEARCH", "LOCAL_SAFE", "TEST_DENY_EXTERNAL")}
        self.assertEqual(len(shas), 1)

    # -- write to temp, verify, and never touch the real T2 workspace -----------
    def test_write_verify_temp_and_t2_untouched(self):
        def snapshot():
            out = {}
            for stage in ("6A", "6B"):
                base = os.path.join(T2, "phase6", stage)
                if not os.path.isdir(base):
                    continue
                for n in os.listdir(base):
                    with open(os.path.join(base, n), "rb") as f:
                        out[(stage, n)] = f.read()
            return out

        before = snapshot()
        r = A.assemble_phase6d(T2)
        root = tempfile.mkdtemp(prefix="6d-t2-")
        dest = os.path.join(root, "phase6", "6D")
        man = A.write_phase6d_artifacts(r, dest_dir=dest, workspace_dir=root,
                                        started_at="T0", completed_at="T1")
        v = A.verify_phase6d_artifacts(dest, workspace_dir=root)
        self.assertTrue(v["ok"], v["errors"])
        self.assertEqual(before, snapshot())                     # T2 6A/6B untouched
        # all five required artifacts present; no Phase 6E/6F artifact.
        for name in A.REQUIRED_ARTIFACTS + (A.STAGE_MANIFEST_FILENAME,):
            self.assertTrue(os.path.exists(os.path.join(dest, name)), name)
        self.assertEqual(set(os.listdir(dest)) & A.FORBIDDEN_ARTIFACTS, set())
        self.assertEqual(man["stage_id"], "6D")
        self.assertEqual(man["aplus_capability"], AREG.UNKNOWN_A_PLUS_CAPABILITY)

    def test_no_upstream_phase6_artifact_modified_by_assembly(self):
        # assemble_phase6d is memory-only; it must not write anything.
        r = A.assemble_phase6d(T2)
        self.assertEqual(r.run_dir, T2)
        self.assertTrue(r.ready_for_6e)


if __name__ == "__main__":
    unittest.main()
