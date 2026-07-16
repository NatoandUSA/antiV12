#!/usr/bin/env python3
"""
Session 3.1 safety-hardening proofs.

PATCH 1  legacy A+ is quarantined (BLOCKED_LEGACY_UNVERIFIED), kept only as a separate DRAFT_ONLY draft,
         and never enters the copy-ready / manual-entry publishable payload.
PATCH 2  three distinct publishability states — PUBLISHABLE, SAFE_DRAFT_OWNER_FACTS_REQUIRED,
         BLOCKED_UNSAFE — with the last safe draft stored separately from the last publishable listing.
PATCH 3  missing claim evidence is NON-PUBLISHABLE (never advisory) for every text field, and every
         generated text field is audited / draft-only / excluded — none may silently bypass the auditor.
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("listing", "research", "dashboard", "compliance"):
    _p = os.path.join(ROOT, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import keyword_source_adapter as KSA          # noqa: E402
import listing_generator as LG                # noqa: E402
import page_auditor as PA                     # noqa: E402
import copy_package as CPKG                   # noqa: E402
import app as DASH                            # noqa: E402

SEED = "personalized nurse sweatshirt"

# a rich, fully-verified fact set — one verified fact per factual bullet job, so all five bullets publish.
RICH_FACTS = {
    "decoration_method": {"value": "machine embroidery", "status": "VERIFIED"},
    "material": {"value": "80% cotton 20% polyester", "status": "VERIFIED"},
    "personalization_fields": {"value": ["name"], "status": "VERIFIED"},
    "size_range": {"value": ["S", "M", "L", "XL"], "status": "VERIFIED"},
    "care_instructions": {"value": "Machine wash cold", "status": "VERIFIED"},
}


def lean_master(folder, phrases=((SEED, "A_CORE"), ("custom nurse gift", "B_SUPPORTING"))):
    recs, counts = [], {}
    for phrase, tier in phrases:
        recs.append({"keyword_exact": phrase, "keyword_normalized": phrase.lower(), "tier": tier,
                     "owner_status": "auto", "reason_codes": [], "risk_flags": [],
                     "search_volume": 1200, "best_rank": 5, "median_rank": 20,
                     "batch_support": {"B1": 6}, "observation_count": 1})
        counts[tier] = counts.get(tier, 0) + 1
    with open(os.path.join(folder, "MASTER-KEYWORDS-LEAN.json"), "w", encoding="utf-8") as f:
        json.dump({"run_metadata": {"run_id": "kwrun_s31", "schema_version": "1.0.0"},
                   "target_profile": {"seed": SEED}, "quality_summary": {"counts": counts},
                   "downstream_policy": {"mode": "transition"}, "keywords": recs}, f)


def project(facts=None):
    d = tempfile.mkdtemp()
    lean_master(d)
    if facts is not None:
        with open(os.path.join(d, "product-facts.json"), "w", encoding="utf-8") as f:
            json.dump({"schema_version": "1.0.0", "source": "owner", "product": facts}, f)
    return d


def safe_base():
    """A minimal, clean, PUBLISHABLE listing with no unsafe claim and no claim_evidence lineage."""
    return {"category": "apparel", "title": "Personalized Nurse Sweatshirt",
            "bullets": ["A personalized nurse sweatshirt.", "", "", "", "A personalized gift for nurses."],
            "description": "A personalized nurse sweatshirt.",
            "backend": "custom nurse gift crewneck rn"}


# ============================================================ PATCH 1 — legacy A+ quarantine
class LegacyAplusQuarantine(unittest.TestCase):
    def setUp(self):
        self.d = project()

    def test_legacy_aplus_is_blocked_legacy_unverified(self):                       # proof 1
        L = LG.generate(self.d)["listing"]
        self.assertEqual(L["aplus_state"], PA.APLUS_BLOCKED_LEGACY_UNVERIFIED)
        self.assertEqual(L["aplus"], [])                                            # nothing publishable
        self.assertIn(PA.APLUS_EVIDENCE_REBUILD_REQUIRED, L["missing_requirements"])
        self.assertEqual(L["audit"]["aplus_results"]["state"], PA.APLUS_BLOCKED_LEGACY_UNVERIFIED)

    def test_legacy_aplus_kept_only_as_separate_draft(self):                        # proof 2
        L = LG.generate(self.d)["listing"]
        draft = L["aplus_draft"]
        self.assertEqual(draft["state"], "DRAFT_ONLY")
        self.assertTrue(draft["modules"])                       # legacy templates preserved, not deleted
        self.assertEqual(L["aplus"], [])                        # but never in the publishable field

    def test_blocked_aplus_absent_from_copy_ready_and_manual_entry(self):           # proof 3
        L = LG.generate(self.d)["listing"]
        txt = CPKG.build_copy_ready_text(L)
        plan = CPKG.build_manual_entry_plan(L)
        # the paste-ready copy + safe_to_paste must contain NONE of the legacy A+ module copy.
        paste_blob = txt.split("--- NOT READY TO PUBLISH ---")[0] + json.dumps(plan["safe_to_paste"])
        for module in L["aplus_draft"]["modules"]:
            self.assertNotIn(module["copy"], paste_blob)
        # and the manual-entry plan explicitly tells the owner NOT to paste A+.
        self.assertEqual(plan["do_not_paste"]["aplus"]["state"], PA.APLUS_BLOCKED_LEGACY_UNVERIFIED)
        self.assertNotIn("aplus", plan["safe_to_paste"])
        self.assertIn(PA.APLUS_BLOCKED_LEGACY_UNVERIFIED, txt)

    def test_dashboard_shows_aplus_blocked_and_draft_only(self):
        r = LG.generate(self.d)
        DASH.safe_write_listing(self.d, r["listing"])
        lc = DASH.snapshot(self.d)["listing_copy"]
        self.assertEqual(lc["aplus_state"], PA.APLUS_BLOCKED_LEGACY_UNVERIFIED)
        self.assertTrue(lc["aplus_blocked_draft_only"])


# ============================================================ PATCH 2 — publishability states
class PublishabilityStates(unittest.TestCase):
    def test_two_publishable_three_blocked_bullets_is_safe_draft(self):             # proof 4
        L = LG.generate(project())["listing"]                   # no owner facts
        pub = [b for b in L["bullet_objects"] if b["publishability"] == "PUBLISHABLE"]
        blocked = [b for b in L["bullet_objects"] if b["publishability"] == "BLOCKED_INCOMPLETE"]
        self.assertEqual(len(pub), 2)
        self.assertEqual(len(blocked), 3)
        self.assertEqual(L["audit"]["publishability"], PA.SAFE_DRAFT_OWNER_FACTS_REQUIRED)
        self.assertFalse(L["publishable"])

    def test_fully_backed_five_bullets_is_publishable(self):                        # proof 7
        L = LG.generate(project(facts=RICH_FACTS))["listing"]
        self.assertTrue(all(b["publishability"] == "PUBLISHABLE" for b in L["bullet_objects"]))
        self.assertEqual(L["audit"]["publishability"], PA.PUBLISHABLE)
        self.assertTrue(L["publishable"])


# ============================================================ last-safe-draft vs last-publishable
class LastValidSeparation(unittest.TestCase):
    def _read(self, folder, name):
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            return f.read()

    def test_safe_draft_does_not_overwrite_last_publishable(self):                  # proof 5
        tgt = tempfile.mkdtemp()
        publishable = LG.generate(project(facts=RICH_FACTS))["listing"]
        draft = LG.generate(project())["listing"]
        r1 = PA.promote_if_safe(tgt, publishable)
        self.assertEqual(r1["publishability"], PA.PUBLISHABLE)
        before = self._read(tgt, "LAST-PUBLISHABLE-LISTING-METADATA.json")
        r2 = PA.promote_if_safe(tgt, draft)
        self.assertEqual(r2["publishability"], PA.SAFE_DRAFT_OWNER_FACTS_REQUIRED)
        self.assertEqual(self._read(tgt, "LAST-PUBLISHABLE-LISTING-METADATA.json"), before)

    def test_safe_draft_updates_last_safe_draft(self):                              # proof 6
        tgt = tempfile.mkdtemp()
        draft = LG.generate(project())["listing"]
        r = PA.promote_if_safe(tgt, draft)
        self.assertEqual(r["publishability"], PA.SAFE_DRAFT_OWNER_FACTS_REQUIRED)
        meta = json.load(open(os.path.join(tgt, "LAST-SAFE-DRAFT-METADATA.json"), encoding="utf-8"))
        self.assertEqual(meta["artifact_kind"], "safe_draft")
        # a safe draft alone must NOT mint a last-publishable-listing artifact.
        self.assertFalse(os.path.exists(os.path.join(tgt, "LAST-PUBLISHABLE-LISTING-METADATA.json")))

    def test_publishable_updates_both_artifacts(self):
        tgt = tempfile.mkdtemp()
        r = PA.promote_if_safe(tgt, LG.generate(project(facts=RICH_FACTS))["listing"])
        self.assertEqual(r["publishability"], PA.PUBLISHABLE)
        self.assertTrue(os.path.exists(os.path.join(tgt, "LAST-SAFE-DRAFT-METADATA.json")))
        self.assertTrue(os.path.exists(os.path.join(tgt, "LAST-PUBLISHABLE-LISTING-METADATA.json")))

    def test_blocked_unsafe_updates_neither_stored_state(self):                     # proof 8
        tgt = tempfile.mkdtemp()
        publishable = LG.generate(project(facts=RICH_FACTS))["listing"]
        PA.promote_if_safe(tgt, publishable)
        PA.promote_if_safe(tgt, LG.generate(project())["listing"])                  # a safe draft too
        pub_before = self._read(tgt, "LAST-PUBLISHABLE-LISTING-METADATA.json")
        safe_before = self._read(tgt, "LAST-SAFE-DRAFT-METADATA.json")
        unsafe = dict(publishable)
        unsafe["bullets"] = list(publishable["bullets"])
        unsafe["bullets"][0] = "Made to last, shipped from the US with tracking."
        unsafe.pop("claim_evidence", None)                      # strip lineage → the claim is unbacked
        res = PA.promote_if_safe(tgt, unsafe)
        self.assertEqual(res["publishability"], PA.BLOCKED_UNSAFE)
        self.assertFalse(res["promoted"])
        self.assertEqual(self._read(tgt, "LAST-PUBLISHABLE-LISTING-METADATA.json"), pub_before)
        self.assertEqual(self._read(tgt, "LAST-SAFE-DRAFT-METADATA.json"), safe_before)


# ============================================================ PATCH 3 — no-evidence is non-publishable
class MissingEvidenceBlocks(unittest.TestCase):
    def test_base_is_clean(self):
        audit = PA.audit_listing(safe_base())
        self.assertEqual(audit["publishability"], PA.PUBLISHABLE)
        self.assertEqual(audit["hard_failures"], [])

    def test_missing_evidence_blocks_title(self):                                   # proof 9
        L = safe_base()
        L["title"] = "Soft Cotton Blend Nurse Sweatshirt"
        audit = PA.audit_listing(L)
        self.assertEqual(audit["publishability"], PA.BLOCKED_UNSAFE)
        self.assertIn("cotton blend", audit["claim_results"]["unsupported"])

    def test_missing_evidence_blocks_bullet(self):                                  # proof 10
        L = safe_base()
        L["bullets"][1] = "Made to last through every 12-hour shift."
        audit = PA.audit_listing(L)
        self.assertEqual(audit["publishability"], PA.BLOCKED_UNSAFE)
        self.assertIn("made to last", audit["claim_results"]["unsupported"])

    def test_missing_evidence_blocks_description(self):                             # proof 11
        L = safe_base()
        L["description"] = "Soft and comfortable, shipped from the US."
        audit = PA.audit_listing(L)
        self.assertEqual(audit["publishability"], PA.BLOCKED_UNSAFE)
        self.assertTrue(audit["claim_results"]["unsupported"])

    def test_missing_evidence_blocks_item_highlights(self):                         # proof 12
        L = safe_base()
        L["item_highlights"] = "cotton blend, made to last"
        audit = PA.audit_listing(L)
        self.assertEqual(audit["publishability"], PA.BLOCKED_UNSAFE)
        self.assertIn("cotton blend", audit["claim_results"]["unsupported"])

    def test_missing_evidence_blocks_aplus_text(self):                              # proof 13
        L = safe_base()
        L["aplus"] = [{"headline": "Stitching", "copy": "Real machine embroidery, made to last."}]
        audit = PA.audit_listing(L)
        self.assertEqual(audit["publishability"], PA.BLOCKED_UNSAFE)
        self.assertIn("real machine embroidery", audit["claim_results"]["unsupported"])


# ============================================================ PATCH 3 — audited-field coverage
class AuditedFieldCoverage(unittest.TestCase):
    def test_every_generated_text_field_is_in_the_registry(self):                   # proof 14
        L = LG.generate(project())["listing"]
        reg = L["audit"]["audited_text_fields"]["registry"]
        dispo = {r["field"]: r["disposition"] for r in reg}
        for field in ("title", "bullets", "description", "backend", "aplus", "item_highlights",
                      "aplus_draft"):
            self.assertIn(field, dispo)
        self.assertEqual(dispo["item_highlights"], "draft_only")
        self.assertEqual(dispo["aplus_draft"], "excluded")

    def test_unknown_generated_text_field_cannot_bypass(self):                      # proof 15
        L = safe_base()
        L["catalog_description"] = "Made to last, shipped from the US."
        audit = PA.audit_listing(L)
        self.assertEqual(audit["publishability"], PA.BLOCKED_UNSAFE)
        self.assertIn("catalog_description", audit["audited_text_fields"]["unregistered"])
        self.assertTrue(any(h["category"] == "unaudited_text_field" for h in audit["hard_failures"]))

    def test_promote_writes_audited_text_fields_artifact(self):
        d = project()
        L = LG.generate(d)["listing"]
        PA.promote_if_safe(d, L)
        self.assertTrue(os.path.exists(os.path.join(d, "AUDITED-TEXT-FIELDS.json")))


# ============================================================ Sessions 1 & 2 preserved
class EarlierSessionsPreserved(unittest.TestCase):
    def test_session1_keyword_source_and_hash_unchanged(self):                      # proof 16
        d = project()
        L = LG.generate(d)["listing"]
        self.assertEqual(L["keyword_source_file"], "MASTER-KEYWORDS-LEAN.json")
        self.assertEqual(L["keyword_source_schema"], KSA.SCHEMA_LEAN)
        self.assertFalse(L["legacy_unsafe"])
        self.assertEqual(L["keyword_source_sha256"], DASH.keyword_source_summary(d)["sha256"])
        before = L["keyword_source_sha256"]
        with open(os.path.join(d, "product-facts.json"), "w", encoding="utf-8") as f:
            json.dump({"schema_version": "1.0.0", "source": "owner", "product": RICH_FACTS}, f)
        self.assertEqual(LG.generate(d)["listing"]["keyword_source_sha256"], before)

    def test_session2_policy_and_product_facts_unchanged(self):                     # proof 17
        d = project()
        L = LG.generate(d)["listing"]
        self.assertEqual(L["category_policy"]["title_hard_limit"],
                         DASH.snapshot(d)["category_policy"]["title_hard_limit"])
        r = LG.generate(d)
        for fld in ("material", "personalization_fields"):
            self.assertIn(fld, r["owner_fact_required"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
