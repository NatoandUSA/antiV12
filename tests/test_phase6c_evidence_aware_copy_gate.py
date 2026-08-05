#!/usr/bin/env python3
"""
Phase 6C — the visible-copy gate must read evidence, not vocabulary.

WHY THIS FILE EXISTS. `_is_safe_copy` decides whether generated copy may be published:

    return KAP._classify_keyword({"keyword_normalized": norm})[0] == "SAFE"

`_classify_keyword` returns `(verdict, concept, reason_code)` — it names the exact concept that
gated the text. `_is_safe_copy` keeps `[0]` and discards the concept, so the gate asks "does this
copy mention a physical attribute?" while three separate comments in this module claim it asks
"does this copy lean on an UNVERIFIED concept?". Those are different questions, and they diverge
precisely when the owner supplies facts.

The measured consequence: with every product fact VERIFIED, the bullet engine produces five
complete bullets ("REAL DECORATION: Real machine embroidery, not a printed graphic", "FIT, SIZE
AND COLOR: ...") and Phase 6C blanks all five. One of them has NO missing requirements at all and
is still blanked, because its text mentions fit, size and colour. Supplying real photographs does
not change this — assets are inert on this path.

WHAT THE GATE MUST BECOME. Copy is publishable when every gated concept in it is backed by a
VERIFIED claim. `production.product_workspace._concept_verified` is the existing authority for
that question and must be reused, not reimplemented.

THE HAZARD TO DESIGN AROUND. `_classify_keyword` returns only the MOST policy-relevant concept,
not all of them. A naive fix verifies that one concept and publishes copy whose second, unverified
concept was never examined — the same cross-authorisation trap the title work had to avoid. The
gate must consider every gated concept in the text.
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

from production import product_workspace as PW              # noqa: E402
from production import product_detail_page as PDP           # noqa: E402
import keyword_allocation_planner as KAP                    # noqa: E402

NURSE_KEYWORDS = [
    {"keyword": "nurse sweatshirt", "tier": "A_CORE"},
    {"keyword": "nurses sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "gifts for nurses", "tier": "B_SUPPORTING"},
    {"keyword": "rn sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "cna sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "icu nurse sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "er nurse sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "lpn sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "embroidered nurse sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "personalized nurse sweatshirt", "tier": "A_CORE"},
]

# Deliberately synthetic. These are fixture values, never owner facts, and never a pilot input.
FULL_FACTS = {
    "schema_version": "1.0.0", "source": "fixture",
    "product": {
        "product_type": {"value": "sweatshirt", "status": "VERIFIED"},
        "garment_type": {"value": "crewneck sweatshirt", "status": "VERIFIED"},
        "material": {"value": "cotton-polyester fleece", "status": "VERIFIED"},
        "material_composition": {"value": "50% cotton, 50% polyester", "status": "VERIFIED"},
        "fit": {"value": "unisex classic fit", "status": "VERIFIED"},
        "size_range": {"value": ["S", "M", "L", "XL"], "status": "VERIFIED"},
        "color_options": {"value": ["black", "navy"], "status": "VERIFIED"},
        "decoration_method": {"value": "machine embroidery", "status": "VERIFIED"},
        "personalization_fields": {"value": ["name", "credentials"], "status": "VERIFIED"},
        "care_instructions": {"value": "machine wash cold", "status": "VERIFIED"},
        "production_location": {"value": "Hue, Vietnam", "status": "VERIFIED"},
        "production_time_range": {"value": "2-4 business days", "status": "VERIFIED"},
        "handling_time": {"value": "3 business days", "status": "VERIFIED"},
        "shipping_method": {"value": "tracked standard shipping", "status": "VERIFIED"},
        "tracking": {"value": "tracking provided", "status": "VERIFIED"},
        "packaging": {"value": "poly mailer", "status": "VERIFIED"},
        "occasion": {"value": "everyday", "status": "VERIFIED"},
        # Present so at least one bullet reaches "every requirement satisfied" and is blanked
        # purely by the copy gate. Without these the fixture cannot observe that case at all --
        # test_a_blocked_bullet_names_the_concept_that_blocked_it asserts the fixture still can.
        "measurements": {"value": ["M: 22in chest, 27in length"], "status": "VERIFIED"},
        "character_limits": {"value": ["name: 20 characters"], "status": "VERIFIED"},
        "verified_differentiator": {"value": "embroidered, not printed", "status": "VERIFIED"},
    },
}


def build_chain(facts=None, project_id="TESTWS"):
    """A hermetic workspace with completed 6A + 6B, optionally carrying verified product facts."""
    d = tempfile.mkdtemp(prefix="6c-gate-")
    lean = {"run_metadata": {"run_id": "kwrun_gate", "source_schema": "master_keywords_lean"},
            "target_profile": {"seed": "nurse sweatshirt"}, "keywords": NURSE_KEYWORDS}
    with open(os.path.join(d, "MASTER-KEYWORDS-LEAN.json"), "w", encoding="utf-8") as f:
        json.dump(lean, f)
    with open(os.path.join(d, "PROJECT-MANIFEST.json"), "w", encoding="utf-8") as f:
        json.dump({"project_id": project_id}, f)
    if facts is not None:
        with open(os.path.join(d, "product-facts.json"), "w", encoding="utf-8") as f:
            json.dump(facts, f)
    res = PW.build_product_workspace(d, workspace_id=project_id)
    PW.write_phase6a_artifacts(res, dest_dir=os.path.join(d, "phase6", "6A"), workspace_dir=d,
                               started_at="T0", completed_at="T1")
    kres = KAP.plan_keyword_allocation(d)
    KAP.write_phase6b_artifacts(kres, dest_dir=KAP.phase6b_dir(d), workspace_dir=d,
                                started_at="T0", completed_at="T1")
    return d


def bullets_of(workspace):
    """Assemble only. Writing would also run title validation, whose separate defect would
    confound a bullet result."""
    res = PDP.assemble_product_detail_page(workspace)
    return res.bullets["bullets"], res.bullets["safe_draft_count"]


class EvidenceAwareCopyGate(unittest.TestCase):

    def test_copy_backed_by_verified_claims_is_published(self):
        """The whole point. Verified facts must produce visible bullets."""
        bl, safe_count = bullets_of(build_chain(FULL_FACTS))
        blanked = [b for b in bl if not str(b.get("text", "")).strip()]
        self.assertGreater(
            safe_count, 0,
            "every bullet was blanked even though the owner verified every product fact; "
            "blanked jobs: %s" % [b.get("bullet_job") for b in blanked])

    def test_decoration_bullet_is_published_when_decoration_is_verified(self):
        """The specific bullet the gate blanks today, named so a regression is unambiguous."""
        bl, _ = bullets_of(build_chain(FULL_FACTS))
        dec = [b for b in bl if "DECORATION" in str(b.get("bullet_job", ""))]
        self.assertTrue(dec, "no decoration bullet in the taxonomy")
        self.assertTrue(str(dec[0].get("text", "")).strip(),
                        "decoration copy was blanked while decoration_method is VERIFIED")

    def test_copy_without_facts_stays_blocked(self):
        """Regression guard: the gate must not become permissive. No facts, no visible copy."""
        bl, safe_count = bullets_of(build_chain(None))
        self.assertEqual(safe_count, 0,
                         "copy was published with no verified product facts at all")
        for b in bl:
            self.assertEqual(str(b.get("text", "")).strip(), "")

    def test_a_blocked_bullet_names_the_concept_that_blocked_it(self):
        """An owner cannot act on 'CLAIM_EVIDENCE_MISSING'. The gate knows the concept -- the
        classifier returns it -- so the artifact must record it.

        Asserted under FULL facts on purpose. With no facts every bullet already carries
        `missing_requirements`, so the same assertion passes without exercising anything: the
        first version of this test did exactly that. The case that matters is the bullet whose
        requirements are all satisfied and which is blanked anyway by the copy gate -- today it
        names nothing at all.
        """
        bl, _ = bullets_of(build_chain(FULL_FACTS))
        silent = [b for b in bl
                  if not str(b.get("text", "")).strip()
                  and not (b.get("missing_requirements") or [])]
        self.assertTrue(silent, "fixture no longer produces a requirement-satisfied blocked "
                                "bullet; this test can no longer observe what it exists to check")
        for b in silent:
            named = b.get("blocking_concepts") or []
            self.assertTrue(
                named,
                "bullet %r was blanked with every requirement satisfied and named no concept "
                "the owner could act on" % b.get("bullet_job"))

    def test_one_verified_concept_does_not_authorise_another(self):
        """Cross-authorisation guard.

        `_classify_keyword` returns only the most policy-relevant concept. A gate that verifies
        that one concept and stops would publish copy whose second, unverified concept was never
        examined. Stated against the gate contract directly because no bullet in the shipped
        taxonomy happens to name two gated concepts with only one verified -- the mutation teeth
        prove this assertion bites.
        """
        gate = getattr(PDP, "_copy_gate", None)
        self.assertIsNotNone(
            gate, "no evidence-aware copy gate exists; _is_safe_copy still discards the concept")
        ws = build_chain(FULL_FACTS)
        res = PDP.assemble_product_detail_page(ws)
        claims = res.claims if hasattr(res, "claims") else None
        ok, blocking = gate("Cotton nurse sweatshirt with a monogram", claims)
        self.assertFalse(ok, "copy naming an unverified concept was authorised")
        self.assertTrue(blocking, "the gate refused without naming what it refused on")


if __name__ == "__main__":
    unittest.main()
