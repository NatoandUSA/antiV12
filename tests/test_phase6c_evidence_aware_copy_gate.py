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
import title_engine as TE                                   # noqa: E402

_UNSET = object()   # "parameter not passed at all", distinct from an explicit None

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


# Everything verified EXCEPT the decoration method. The verified differentiator still reads
# "embroidered, not printed", so copy carries a DECORATION concept backed only by a
# DIFFERENTIATOR claim -- the cross-authorisation case, and the only fixture that produces a
# bullet whose requirements are satisfied while its copy is still blocked.
FACTS_NO_DECORATION = json.loads(json.dumps(FULL_FACTS))
FACTS_NO_DECORATION["product"].pop("decoration_method")


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

    def test_the_copy_gate_no_longer_blocks_the_decoration_bullet(self):
        """Scope boundary, asserted rather than assumed.

        With decoration_method VERIFIED the copy gate must not be what stops this bullet. It is
        still empty, because `bullet_engine.JOB_CONCEPTS` draws EMBROIDERY_OR_DECORATION_PROOF
        from ("real_machine_embroidery", "satin_stitch", "tatami_fill", "decoration_method") and
        the engine reports decoration_method unsatisfied -- a claim-mapping question inside the
        bullet engine, not a Phase 6C filtering question.

        This test pins the boundary so a future reader does not mistake the remaining emptiness
        for the defect this branch fixed, and fails loudly if the copy gate starts blocking it
        again.
        """
        bl, _ = bullets_of(build_chain(FULL_FACTS))
        dec = [b for b in bl if "DECORATION" in str(b.get("bullet_job", ""))]
        self.assertTrue(dec, "no decoration bullet in the taxonomy")
        self.assertEqual(
            dec[0].get("blocking_concepts") or [], [],
            "the copy gate is blocking decoration copy although decoration_method is VERIFIED")
        self.assertIn("decoration_method", dec[0].get("missing_requirements") or [],
                      "the engine-side blocker changed shape; re-check the scope boundary")

    def test_copy_without_facts_stays_blocked(self):
        """Regression guard: the gate must not become permissive. No facts, no visible copy."""
        bl, safe_count = bullets_of(build_chain(None))
        self.assertEqual(safe_count, 0,
                         "copy was published with no verified product facts at all")
        for b in bl:
            self.assertEqual(str(b.get("text", "")).strip(), "")

    def test_a_verified_material_claim_does_not_authorise_decoration_wording(self):
        """Cross-authorisation: a verified claim authorises its own concept and nothing else.

        The copy names TWO gated concepts. Under FACTS_NO_DECORATION material_composition is
        VERIFIED and decoration_method is not, so a gate that stops at "is anything here
        verified?" publishes decoration wording on the strength of a material claim.

        The fixture matters and was got wrong once: "embroidered, not printed" enumerates only
        decoration_method, so it carries no second verified concept and cannot distinguish the
        mutant at all. The mutation gate is what exposed that -- the assertion passed while the
        mutant lived. Any replacement copy must keep at least one verified AND one unverified
        concept, or this test silently stops testing anything.
        """
        ws = build_chain(FACTS_NO_DECORATION)
        claims = PDP.load_phase6c_dependencies(ws).claims

        hits = dict((c, PW._concept_verified(claims, c)) for c, _ in
                    PDP.gated_concepts("cotton nurse sweatshirt with embroidery") if c)
        self.assertTrue(any(hits.values()) and not all(hits.values()),
                        "fixture no longer holds one verified and one unverified concept, so "
                        "cross-authorisation cannot be observed: %s" % hits)

        ok, blocking = PDP._copy_gate("cotton nurse sweatshirt with embroidery", claims,
                                      PDP.COPY_SURFACE_BODY)
        self.assertFalse(ok, "decoration wording was authorised by a material claim; "
                             "one verified concept authorised another")
        self.assertIn("decoration_method", [b["concept"] for b in blocking],
                      "one verified concept authorised another; decoration_method was not named")

        bl, _ = bullets_of(ws)
        rec = [b for b in bl if "RECIPIENT" in str(b.get("bullet_job", ""))][0]
        self.assertEqual(str(rec.get("text", "")).strip(), "",
                         "decoration wording reached a published bullet")

    def test_a_blocked_bullet_names_the_concept_that_blocked_it(self):
        """An owner cannot act on 'CLAIM_EVIDENCE_MISSING'. The gate knows the concept -- the
        classifier returns it -- so the artifact must record it.

        Asserted under FULL facts on purpose. With no facts every bullet already carries
        `missing_requirements`, so the same assertion passes without exercising anything: the
        first version of this test did exactly that. The case that matters is the bullet whose
        requirements are all satisfied and which is blanked anyway by the copy gate -- today it
        names nothing at all.
        """
        bl, _ = bullets_of(build_chain(FACTS_NO_DECORATION))
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
            for entry in named:
                self.assertTrue(entry.get("next_action"),
                                "block reason names a concept but nothing the owner can do")

    def test_every_gated_concept_is_examined_not_only_the_first(self):
        """`_classify_keyword` returns ONE concept -- the most policy-relevant hit. A gate built
        on it examines that concept and stops, publishing copy whose second, unverified concept
        was never looked at.

        Asserted on the enumeration itself because no bullet in the shipped taxonomy happens to
        carry two gated concepts. The mutation teeth prove this assertion bites.
        """
        hits = PDP.gated_concepts("cotton nurse sweatshirt with an embroidered monogram")
        concepts = [c for c, _code in hits]
        self.assertGreater(len(concepts), 1,
                           "only one concept was enumerated; a second unverified concept in the "
                           "same copy would never be examined. got: %s" % concepts)
        self.assertIn("material_composition", concepts,
                      "only one concept was enumerated; material was not examined. got: %s"
                      % concepts)
        self.assertIn("decoration_method", concepts,
                      "only one concept was enumerated; decoration was not examined. got: %s"
                      % concepts)

    def test_unrecognised_copy_fails_closed(self):
        """"No concept matched" and "nothing to check" must not be the same answer."""
        hits = PDP.gated_concepts("zzqx wibbleflange nurse sweatshirt")
        self.assertTrue(hits, "unrecognised content tokens produced an empty hit list, which "
                              "would publish the copy unchecked")


class ConceptEnumerationContract(unittest.TestCase):
    """What `gated_concepts` must mean, pinned before anything relies on it.

    The fallthrough is deliberately IDENTICAL to `_classify_keyword`'s own: any content token
    that is neither SAFE_TOKENS nor in a gate lexicon yields (None, PRODUCT_FACT_UNKNOWN).
    Narrowing it here would make Phase 6C strictly more permissive than the Phase 6B authority
    it borrows the vocabulary from, which is a divergence, not a fix. These tests exist to show
    what that rule actually does to real copy rather than to argue about it.
    """

    def concepts(self, text):
        return [c for c, _code in PDP.gated_concepts(text)]

    def test_pure_market_identity_produces_no_gated_concepts(self):
        """The control. If this ever blocks, the gate is unusable regardless of evidence."""
        self.assertEqual(PDP.gated_concepts("nurse sweatshirt"), [])
        self.assertEqual(PDP.gated_concepts("sweatshirt for nurses"), [])

    def test_connectors_alone_do_not_create_a_block(self):
        """Ordinary grammar must not manufacture a concept."""
        self.assertEqual(PDP.gated_concepts("sweatshirt for the nurse"), [])

    def test_an_unknown_claim_like_term_fails_closed(self):
        """Unrecognised vocabulary blocks, and is distinguishable from a known-but-unverified
        concept: concept is None, reason is PRODUCT_FACT_UNKNOWN."""
        hits = PDP.gated_concepts("nurse sweatshirt with quantumweave nanofibre")
        self.assertTrue(hits, "unrecognised vocabulary did not fail closed; it produced no gate "
                              "entry at all, which publishes the copy unchecked")
        unknown = [h for h in hits if h[0] is None]
        self.assertTrue(unknown, "unrecognised claim-like vocabulary did not fail closed")
        self.assertEqual(unknown[0][1], KAP.EXC_PRODUCT_FACT_UNKNOWN)

    def test_a_token_in_two_lexicons_yields_both_concepts(self):
        """'monogram' is decoration vocabulary AND personalization vocabulary. Both claims must
        be verified before such copy publishes -- one of them is not enough."""
        got = self.concepts("monogrammed nurse sweatshirt")
        self.assertIn("decoration_method", got)
        self.assertIn("personalization_fields", got)

    def test_concepts_are_deduplicated_and_deterministically_ordered(self):
        """Repeated hits for one concept must not multiply block entries, and the order must be
        stable so block reasons are reproducible."""
        got = self.concepts("embroidered embroidery stitched nurse sweatshirt")
        self.assertEqual(len(got), len(set(got)), "the same concept was reported more than once")
        self.assertEqual(got, self.concepts("embroidered embroidery stitched nurse sweatshirt"))

    def test_quality_adjectives_resolve_to_differentiator_not_to_unknown(self):
        """The load-bearing question about the unknown fallthrough, answered by measurement.

        The concern was that ordinary adjectives would collapse into PRODUCT_FACT_UNKNOWN and
        block with a reason nobody can act on. They do not: the differentiator vocabulary claims
        them first, so "comfortable" yields ('differentiator', OWNER_FACT_REQUIRED) -- named and
        actionable. Blocking it is correct; "comfortable" is an unsubstantiated product claim,
        which is the entire premise of the evidence model.

        Only genuinely unrecognised vocabulary reaches the unknown fallthrough.
        """
        for phrase in ("comfortable nurse sweatshirt", "soft cozy nurse sweatshirt",
                       "best nurse sweatshirt ever", "premium quality nurse sweatshirt"):
            hits = PDP.gated_concepts(phrase)
            self.assertTrue(hits, phrase)
            self.assertNotIn(None, [c for c, _ in hits],
                             "%r collapsed into the unknown fallthrough instead of naming a "
                             "concept the owner can act on" % phrase)

    def test_unknown_and_unverified_remain_distinguishable(self):
        """An owner can act on 'verify decoration_method'. Nobody can act on 'something in this
        sentence is unrecognised'. The two must never collapse into one reason."""
        known = PDP.gated_concepts("embroidered nurse sweatshirt")
        unknown = PDP.gated_concepts("nurse sweatshirt with quantumweave nanofibre")
        self.assertTrue(all(c is not None for c, _ in known))
        self.assertTrue(any(c is None for c, _ in unknown))


class SurfacePolicy(unittest.TestCase):
    """The TITLE surface carries market identity only, whatever the evidence says."""

    def claims_for(self, facts):
        return PDP.load_phase6c_dependencies(build_chain(facts)).claims

    def test_a_verified_physical_concept_is_still_blocked_on_the_title_surface(self):
        """Killer for the `title_evidence_aware` mutant.

        decoration_method is VERIFIED here. On BODY that authorises the wording; on TITLE it
        must not, because the surface rule is a policy about the field, not a judgement about
        the evidence. Flipping TITLE to evidence-aware must fail this.
        """
        claims = self.claims_for(FULL_FACTS)
        ok_body, _ = PDP._copy_gate("embroidered nurse sweatshirt", claims, PDP.COPY_SURFACE_BODY)
        self.assertTrue(ok_body, "verified decoration wording was blocked on the BODY surface")

        ok_title, blocking = PDP._copy_gate("embroidered nurse sweatshirt", claims,
                                            PDP.COPY_SURFACE_TITLE)
        self.assertFalse(ok_title,
                         "a verified physical concept reached the TITLE surface; the "
                         "pure-identity title policy has been reversed")
        self.assertEqual([b["evidence_state"] for b in blocking],
                         ["NOT_PERMITTED_ON_SURFACE"] * len(blocking))

    def test_monogram_needs_both_of_its_concepts_verified(self):
        """Killer for the `overlap_any_instead_of_all` mutant.

        'monogram' is decoration vocabulary AND personalization vocabulary. Enumerating both is
        not enough -- publication must require BOTH claims. With only one verified the copy
        stays blocked, and the block names the one that is missing.
        """
        no_pers = json.loads(json.dumps(FULL_FACTS))
        no_pers["product"].pop("personalization_fields")
        claims = self.claims_for(no_pers)

        ok, blocking = PDP._copy_gate("monogrammed nurse sweatshirt", claims,
                                      PDP.COPY_SURFACE_BODY)
        self.assertFalse(ok, "monogram copy published with only one of its two concepts verified")
        self.assertIn("personalization_fields", [b["concept"] for b in blocking])

    def test_the_term_blacklist_backstop_still_fires_when_reached(self):
        """Killer for the `restore_traceback` / blacklist-disabling mutants.

        Under the pure-identity filter, production title assembly can never put a controlled
        term into a candidate -- which means the blacklist is unreachable in normal operation
        and a mutant that disables it would survive every other test in this file. A backstop
        nobody exercises is indistinguishable from dead code, which is the defect this project
        already found in Phase 6D.

        So the forbidden candidate is constructed HERE, in the test, without weakening
        production filtering. The validator must still catch it, and must report it as a
        structured error list rather than raising at the caller.
        """
        doc = {"schema_version": PDP.TITLE_OPTIONS_SCHEMA_VERSION,
               "candidates": [{"candidate_id": "T-TEST", "title": "Embroidered Nurse Sweatshirt",
                               "character_count": 29, "utf8_byte_count": 29,
                               "state": PDP.T_RECOMMENDED_SAFE_DRAFT, "recommended": True}]}
        errors = PDP.validate_title_options(doc)
        self.assertIsInstance(errors, list, "the backstop raised instead of reporting")
        self.assertTrue([e for e in errors if "embroider" in str(e)],
                        "the controlled-term backstop did not fire on a forbidden candidate")

    def test_production_titles_never_reach_the_backstop(self):
        """The other half: the backstop must stay unreachable in normal assembly, or the
        pure-identity filter is not doing its job."""
        res = PDP.assemble_product_detail_page(build_chain(FULL_FACTS))
        self.assertEqual(PDP.validate_title_options(res.title_options), [],
                         "production title assembly produced a candidate the backstop rejects")


class SharedTitleEngineContract(unittest.TestCase):
    """`allowed_component_ids` is an additive parameter on an engine Phase 5 also uses."""

    def build(self, allowed=_UNSET):
        d = build_chain(FULL_FACTS)
        deps = PDP.load_phase6c_dependencies(d)
        kw = {} if allowed is _UNSET else {"allowed_component_ids": allowed}
        return TE.build_titles("nurse sweatshirt", deps.claims, deps.policy,
                               audience_hint=deps.audience, **kw)

    def test_default_is_byte_identical_to_not_passing_the_parameter(self):
        """Phase 5 must not move. None means exactly what omitting it meant."""
        self.assertEqual(self.build().title_expanded, self.build(allowed=None).title_expanded)
        self.assertEqual(self.build().title_concise, self.build(allowed=None).title_concise)

    def test_an_empty_set_means_identity_only_not_no_title(self):
        """Identity is structural. An empty set restricts attributes to none; it cannot leave
        the product unnamed, and it must not silently fall back to legacy behaviour."""
        tr = self.build(allowed=frozenset())
        self.assertTrue(tr.title_concise.strip())
        self.assertNotEqual(tr.title_expanded, self.build().title_expanded,
                            "an empty set fell back to legacy component selection")

    def test_the_phase6c_set_excludes_the_physical_components(self):
        tr = self.build(allowed=PDP.TITLE_SURFACE_COMPONENTS)
        low = tr.title_expanded.lower()
        for banned in ("embroider", "personalized", "not printed"):
            self.assertNotIn(banned, low)

    def test_unknown_component_ids_match_nothing_and_do_not_raise(self):
        tr = self.build(allowed=frozenset({"identity", "no_such_component"}))
        self.assertTrue(tr.title_concise.strip())

    def test_filtering_is_deterministic(self):
        a = self.build(allowed=PDP.TITLE_SURFACE_COMPONENTS).title_expanded
        b = self.build(allowed=PDP.TITLE_SURFACE_COMPONENTS).title_expanded
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
