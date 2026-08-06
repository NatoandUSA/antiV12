#!/usr/bin/env python3
"""Unsafe-claim policy: an owner must not be able to authorise a prohibited phrase by typing it.

THE DEFECT THESE TESTS PIN

The auditor authorised an unsafe phrase by concatenating every publishable claim's text into one
normalised bag of words and asking whether the phrase appeared in it. Claim text is built as
spec.text_for(value), which substitutes the owner's RAW FACT VALUE into a template. So the owner
could authorise ANY phrase by typing it into a free-text fact and marking it VERIFIED:

    care_instructions = "never fades"  (VERIFIED)  ->  a permanence claim cleared in A+ copy

Measured before the policy authority existed: 45 of 45 shared phrases were self-authorising, 26 of
them in classes that must never clear. There was also no concept binding whatsoever -- a CARE fact
cleared a DURABILITY claim -- and nothing prevented a phrase straddling two adjacent claim texts.

WHAT IS ASSERTED HERE

  * hard-block classes stay blocked even when the owner types the phrase and verifies it;
  * a factual phrase clears ONLY through its own exact mapped concept, and needs ALL of them;
  * unrelated verified claims cannot cross-authorise;
  * concatenation across claim texts cannot authorise;
  * unknown and ambiguous phrases fail closed;
  * every live surface reaches the same authority;
  * the owner-fact ingestion guard refuses to make such a value publishable, while preserving it.

Every surface test carries a fixture guard proving the dangerous copy actually REACHED the audited
path, so none of these can pass merely because the copy was never audited.
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

import category_policy_registry as CPR      # noqa: E402
import claim_evidence as CE                 # noqa: E402
import page_auditor as PA                   # noqa: E402
import product_fact_loader as PFL           # noqa: E402
import unsafe_claim_policy as UCP           # noqa: E402

V = "VERIFIED"

SAFE_FACTS = {
    "brand": "Acme", "product_type": "sweatshirt", "garment_type": "sweatshirt",
    "material": "80% cotton, 20% polyester", "material_composition": "80% cotton, 20% polyester",
    "fit": "unisex classic fit", "decoration_method": "machine embroidery",
    "care_instructions": "machine wash cold, tumble dry low", "production_location": "Hue, Vietnam",
    "production_time_range": "3-5 business days", "handling_time": "2 business days",
    "shipping_method": "USPS Ground Advantage", "tracking": "tracking number provided",
    "packaging": "polybag", "audience": "nurses", "occasion": "nurse graduation",
    "verified_differentiator": "embroidered, not printed", "placement": "left chest",
    "size_range": ["S", "M", "L"], "measurements": ["chest 20in"], "color_options": ["black"],
    "personalization_fields": ["name"], "character_limits": ["name 20"],
    "thread_colors": ["white"], "design_dimensions": ["4in x 3in"],
}


def facts_and_claims(**overrides):
    d = tempfile.mkdtemp(prefix="ucp-")
    merged = dict(SAFE_FACTS)
    merged.update(overrides)
    with open(os.path.join(d, "product-facts.json"), "w", encoding="utf-8") as f:
        json.dump({"schema_version": "1.0.0", "source": "owner",
                   "product": {k: {"value": v, "status": V} for k, v in merged.items()}}, f)
    facts = PFL.load_product_facts(folder=d)
    return facts, CE.build_claim_evidence(facts, keyword_context={"audience": "nurses"})


POLICY = CPR.resolve_category_policy("apparel")


def _base_listing():
    return {"schema_version": "2.4", "category": "apparel", "title": "Nurse Sweatshirt",
            "bullets": ["", "", "", "", ""], "description": "",
            "selected_keywords": {"primary": [], "secondary": [], "backend": []},
            "category_policy": {"category_identifier": POLICY.category_identifier,
                                "title_hard_limit": POLICY.title_hard_limit,
                                "backend_byte_ceiling": POLICY.backend_byte_ceiling}}


def listing_bullet(text):
    L = _base_listing()
    L["bullets"] = [f"FEATURE: {text}", "", "", "", ""]
    return L


def listing_description(text):
    L = _base_listing()
    L["description"] = f"About this product. {text}"
    return L


def listing_highlight(text):
    L = _base_listing()
    L["item_highlights_publishable"] = [
        {"highlight_id": "IH-1", "text": text, "claim_ids": ["CLM-DECORATION_METHOD"],
         "publishability_status": "PUBLISHABLE", "verification_state": "VERIFIED",
         "concept": "decoration_method"}]
    L["item_highlights_content"] = {"category_support_state": None, "blocked_count": 0}
    return L


def _modules(body):
    keys = ["HERO_EMBROIDERY_PROOF", "PERSONALIZATION_GALLERY", "HOW_TO_CUSTOMIZE",
            "FIT_SIZE_COLOR_DETAILS", "CARE_PRODUCTION_FAQ"]
    out = []
    for i, k in enumerate(keys, start=1):
        ready = (i == 1)
        out.append({"module_key": k, "position_slot": i,
                    "status": "READY" if ready else "OWNER_FACT_REQUIRED",
                    "headline": "Headline" if ready else "", "body": body if ready else "",
                    "claim_ids": ["CLM-DECORATION_METHOD"] if ready else [],
                    "evidence_summary": {"missing_facts": [], "missing_real_assets": []}})
    return out


def listing_aplus(body):
    L = _base_listing()
    L["aplus"] = []
    L["aplus_capability"] = "BASIC_A_PLUS"
    L["aplus_content"] = {"capability": "BASIC_A_PLUS", "basic_module_count": 5,
                          "basic_ready_count": 1, "basic_ready": False,
                          "basic_modules": _modules(body), "premium": {},
                          "publishable_modules": [], "fallback_plan": {}}
    return L


def audit(listing, claims, category="apparel"):
    return PA.audit_listing(listing, keyword_source=None, claim_evidence=claims,
                            product_facts=None, policy=CPR.resolve_category_policy(category))


def unsupported_for(au, prefix):
    return [h for h in (au.get("hard_failures") or [])
            if str(h.get("category", "")).endswith("unsupported_claim")
            and (prefix is None or str(h.get("category", "")).startswith(prefix))]


def blocked(au, phrase):
    return any(phrase in str(h.get("message", "")) for h in (au.get("hard_failures") or []))


SHARED = [r for r in UCP.manifest()["rows"] if r["scope"] == "shared"]
HARD_BLOCK_CLASSES = (UCP.ABSOLUTE_OR_PERMANENCE, UCP.GUARANTEE_OR_PROMISE,
                      UCP.UNVERIFIABLE_SUPERLATIVE)
HARD_BLOCK = [r for r in SHARED if r["policy_class"] in HARD_BLOCK_CLASSES]
FACTUAL = [r for r in SHARED if r["policy_class"] == UCP.FACTUAL_AND_EVIDENCE_VERIFIABLE]
AMBIG = [r for r in SHARED if r["policy_class"] == UCP.AMBIGUOUS]

# a free-text scalar fact the owner can type anything into
SELF_TYPE_FIELD = "care_instructions"


# ================================================================ hard-block matrix
class HardBlockMatrix(unittest.TestCase):
    """Every never-clearable phrase stays blocked even when the owner types it and verifies it."""

    def test_00_class_counts_are_pinned_so_a_silent_reclassification_is_caught(self):
        """Moving a phrase between classes changes what may reach a customer. Pin the shape."""
        import collections
        shared = collections.Counter(r["policy_class"] for r in SHARED)
        self.assertEqual(dict(shared), {
            UCP.ABSOLUTE_OR_PERMANENCE: 6,
            UCP.GUARANTEE_OR_PROMISE: 6,
            UCP.UNVERIFIABLE_SUPERLATIVE: 13,
            UCP.AMBIGUOUS: 5,
            UCP.FACTUAL_AND_EVIDENCE_VERIFIABLE: 15,
        }, "shared phrase classification changed")
        every = collections.Counter(r["policy_class"] for r in UCP.manifest()["rows"])
        self.assertEqual(sum(every.values()), 92)
        self.assertEqual(every[UCP.FACTUAL_AND_EVIDENCE_VERIFIABLE], 15,
                         "only the shared vocabulary has clearable phrases; acrylic has none")

    def test_01_every_hard_block_phrase_survives_owner_self_authorisation(self):
        self.assertEqual(len(HARD_BLOCK), 25, "hard-block set size changed")
        leaked = []
        for r in HARD_BLOCK:
            phrase = r["phrase"]
            _f, claims = facts_and_claims(**{SELF_TYPE_FIELD: phrase})
            au = audit(listing_bullet(f"This item {phrase}."), claims)
            if not blocked(au, phrase):
                leaked.append((phrase, r["policy_class"]))
        self.assertEqual(
            leaked, [],
            "owner-typed VERIFIED facts authorised phrases that must never clear: "
            + ", ".join(f"{p} [{c}]" for p, c in leaked))

    def test_01b_hard_block_phrases_are_refused_for_the_RIGHT_reason(self):
        """Blocked-ness alone is too weak an assertion. Hard-block rows also carry an empty
        allowed_surfaces, so a bug that let a verified concept clear the class would STILL be caught
        by the surface check -- and would report POLICY_DECISION_REQUIRED instead of naming the
        prohibition. The owner acts on the reason code, so pin the reason, not just the refusal.

        Four of these phrases (any occasion, premium material, premium fabric, any name and
        credentials) map to concepts the fixture genuinely verifies, which is exactly the condition
        under which a class-check bug would surface.
        """
        _f, claims = facts_and_claims()
        wrong = []
        for r in HARD_BLOCK:
            v = UCP.evaluate(r["phrase"], claims, UCP.SURFACE_CLAIMS)
            expected = UCP.REASON_BY_CLASS[r["policy_class"]]
            if v["authorized"] or v["block"]["reason_code"] != expected:
                wrong.append((r["phrase"], expected,
                              "AUTHORIZED" if v["authorized"] else v["block"]["reason_code"]))
        self.assertEqual(wrong, [],
                         "hard-block phrases must never clear and must name the prohibition: "
                         + "; ".join(f"{p} expected {e} got {g}" for p, e, g in wrong))

    def test_02_never_fades_in_care_instructions_cannot_authorise_permanence(self):
        _f, claims = facts_and_claims(care_instructions="never fades")
        au = audit(listing_bullet("This design never fades."), claims)
        self.assertTrue(blocked(au, "never fades"),
                        "a permanence claim was authorised by an owner-typed care fact")
        v = UCP.evaluate("never fades", claims, UCP.SURFACE_CLAIMS)
        self.assertFalse(v["authorized"])
        self.assertEqual(v["block"]["reason_code"], UCP.ABSOLUTE_CLAIM_PROHIBITED)

    def test_03_lasts_forever_not_clearable_by_ordinary_claim_or_photo_marker(self):
        _f, claims = facts_and_claims(care_instructions="lasts forever",
                                      verified_differentiator="lasts forever")
        v = UCP.evaluate("lasts forever", claims, UCP.SURFACE_CLAIMS)
        self.assertFalse(v["authorized"])
        self.assertEqual(v["block"]["reason_code"], UCP.ABSOLUTE_CLAIM_PROHIBITED)
        self.assertEqual(v["block"]["evidence_state"],
                         "NOT_CONSULTED_CLASS_IS_NEVER_CLEARABLE",
                         "permanence must not even consult evidence; a photo cannot prove it either")

    def test_04_hard_block_classes_are_never_clearable_by_rule(self):
        for r in HARD_BLOCK:
            self.assertEqual(UCP.policy_for(r["phrase"])["clearance_rule"], UCP.ALWAYS_BLOCK,
                             f"{r['phrase']} is not ALWAYS_BLOCK")


# ================================================================ authorization model
class AuthorizationModel(unittest.TestCase):
    def test_05_factual_phrase_clears_only_through_its_exact_mapped_concept(self):
        _f, claims = facts_and_claims()                       # decoration_method VERIFIED
        v = UCP.evaluate("machine embroidery", claims, UCP.SURFACE_CLAIMS)
        self.assertTrue(v["authorized"], "a factual phrase with its concept VERIFIED must clear")
        self.assertEqual(v["required_concept_ids"], ["decoration_method"])

    def test_06_factual_phrase_blocked_when_its_own_concept_is_unverified(self):
        _f, claims = facts_and_claims(decoration_method="")    # concept no longer verified
        v = UCP.evaluate("machine embroidery", claims, UCP.SURFACE_CLAIMS)
        self.assertFalse(v["authorized"])
        self.assertEqual(v["block"]["reason_code"], UCP.MATCHING_CONCEPT_UNVERIFIED)

    def test_07_unrelated_verified_claims_cannot_cross_authorise(self):
        """Everything else verified, the phrase's OWN concept not."""
        _f, claims = facts_and_claims(decoration_method="")
        au = audit(listing_bullet("Made with machine embroidery."), claims)
        self.assertTrue(blocked(au, "machine embroidery"),
                        "an unrelated verified claim authorised a phrase it does not assert")

    def test_08_multi_concept_phrase_requires_all_concepts_not_any(self):
        rec = UCP.policy_for("embroidered name")
        self.assertEqual(len(rec["required_concept_ids"]), 2, "fixture needs a 2-concept phrase")
        _f, both = facts_and_claims()
        self.assertTrue(UCP.evaluate("embroidered name", both, UCP.SURFACE_CLAIMS)["authorized"])
        _f2, one = facts_and_claims(personalization_fields=[])   # drop ONE of the two
        v = UCP.evaluate("embroidered name", one, UCP.SURFACE_CLAIMS)
        self.assertFalse(v["authorized"],
                         "ALL mapped concepts must be verified; ANY-of is not the rule")
        self.assertEqual(v["block"]["reason_code"], UCP.MATCHING_CONCEPT_UNVERIFIED)

    def test_09_raw_fact_text_alone_cannot_self_authorise_a_factual_phrase(self):
        """Typing 'satin stitch' into a care fact must not clear it: satin_stitch is not verified."""
        _f, claims = facts_and_claims(care_instructions="satin stitch")
        v = UCP.evaluate("satin stitch", claims, UCP.SURFACE_CLAIMS)
        self.assertFalse(v["authorized"],
                         "raw owner text authorised a factual phrase without its concept")
        self.assertEqual(v["block"]["reason_code"], UCP.MATCHING_CONCEPT_UNVERIFIED)

    def test_10_words_scattered_across_claims_cannot_assemble_an_authorisation(self):
        """'cotton' lives in one publishable claim and 'blend' in another, while the phrase's own
        concept (material_composition) is UNVERIFIED. Any authorisation that looks at claim TEXT --
        concatenated, or word-by-word across claims -- clears 'cotton blend' here. Concept-bound
        authorisation does not."""
        _f, claims = facts_and_claims(material_composition="", material="",
                                      care_instructions="rinse cotton items cold",
                                      packaging="blend safe mailer")
        texts = " ".join(c["proposed_text"] for c in claims.publishable if c.get("proposed_text"))
        words = set(UCP.normalize_text(texts).split())
        self.assertIn("cotton", words, "fixture guard: 'cotton' must be present in claim text")
        self.assertIn("blend", words, "fixture guard: 'blend' must be present in claim text")
        self.assertFalse(UCP._concept_verified(claims, "material_composition"),
                         "fixture guard: the phrase's own concept must be unverified")
        v = UCP.evaluate("cotton blend", claims, UCP.SURFACE_CLAIMS)
        self.assertFalse(v["authorized"],
                         "words scattered across claim texts assembled an authorisation")
        self.assertEqual(v["block"]["reason_code"], UCP.MATCHING_CONCEPT_UNVERIFIED)

    def test_11_unknown_phrase_fails_closed(self):
        _f, claims = facts_and_claims()
        v = UCP.evaluate("totally unmapped marketing phrase", claims, UCP.SURFACE_CLAIMS)
        self.assertFalse(v["authorized"],
                         "an unmapped phrase was authorised; it must fail closed with "
                         "UNKNOWN_UNSAFE_PHRASE")
        self.assertEqual(v["block"]["reason_code"], UCP.UNKNOWN_UNSAFE_PHRASE)

    def test_12_ambiguous_phrase_returns_policy_decision_blocker(self):
        self.assertTrue(AMBIG, "expected ambiguous phrases in the shared table")
        _f, claims = facts_and_claims()
        for r in AMBIG:
            v = UCP.evaluate(r["phrase"], claims, UCP.SURFACE_CLAIMS)
            self.assertFalse(v["authorized"], f"{r['phrase']} must fail closed")
            self.assertEqual(v["block"]["reason_code"], UCP.POLICY_DECISION_REQUIRED)

    def test_13_unknown_surface_fails_closed_for_a_factual_phrase(self):
        _f, claims = facts_and_claims()
        v = UCP.evaluate("machine embroidery", claims, "some_new_surface")
        self.assertFalse(v["authorized"], "an unrecognised surface must not be permitted")
        self.assertEqual(v["block"]["evidence_state"], "SURFACE_NOT_PERMITTED")

    def test_14_made_in_the_usa_not_cleared_by_a_verified_vietnam_location(self):
        """The concrete reason value-encoding phrases are AMBIGUOUS rather than factual."""
        _f, claims = facts_and_claims(production_location="Hue, Vietnam")
        self.assertTrue(UCP._concept_verified(claims, "production_location"))
        v = UCP.evaluate("made in the usa", claims, UCP.SURFACE_CLAIMS)
        self.assertFalse(v["authorized"],
                         "a VERIFIED Vietnamese production location cleared 'made in the usa'")


# ================================================================ surface parity
class SurfaceParity(unittest.TestCase):
    """Every live surface consults the same authority, with a guard proving the copy was audited."""

    def test_15_bullets_surface_blocks_and_actually_audited_the_copy(self):
        _f, claims = facts_and_claims(care_instructions="never fades")
        L = listing_bullet("This design never fades.")
        au = audit(L, claims)
        self.assertIn("never fades", json.dumps(L["bullets"]), "fixture guard: copy present")
        self.assertTrue(blocked(au, "never fades"))

    def test_16_description_surface_blocks(self):
        _f, claims = facts_and_claims(care_instructions="never fades")
        au = audit(listing_description("This design never fades."), claims)
        self.assertTrue(blocked(au, "never fades"))

    def test_17_item_highlights_surface_blocks_and_reached_the_path(self):
        _f, claims = facts_and_claims(care_instructions="never fades")
        au = audit(listing_highlight("This design never fades."), claims)
        ih = au.get("item_highlights_results") or {}
        self.assertEqual(ih.get("publishable_count"), 1,
                         "fixture guard: the highlight must have been audited")
        self.assertTrue(unsupported_for(au, "item_highlights"),
                        "item highlights surface did not block a permanence claim")

    def test_18_aplus_surface_blocks_and_reached_the_path(self):
        _f, claims = facts_and_claims(care_instructions="never fades")
        au = audit(listing_aplus("This design never fades."), claims)
        er = au.get("aplus_evidence_results") or {}
        self.assertTrue(er.get("present"), "fixture guard: A+ content must have been audited")
        self.assertEqual(er.get("module_states", {}).get("HERO_EMBROIDERY_PROOF"), "READY",
                         "fixture guard: only READY modules are claim-checked")
        self.assertTrue(unsupported_for(au, "aplus"),
                        "A+ surface did not block a permanence claim")

    def test_19_acrylic_vocabulary_reaches_the_specialised_surfaces(self):
        """Pre-existing gap: the two specialised surfaces used the BASE list only, so the acrylic
        safety vocabulary was unchecked there."""
        _f, claims = facts_and_claims()
        au = audit(listing_aplus("This piece is shatterproof."), claims, category="acrylic")
        self.assertTrue(blocked(au, "shatterproof"),
                        "acrylic safety vocabulary is not screened on the A+ surface")

    def test_20_a_factual_phrase_still_publishes_on_every_surface(self):
        """The gate must not simply close: verified factual copy still passes."""
        _f, claims = facts_and_claims()
        for name, L in (("bullets", listing_bullet("Made with machine embroidery.")),
                        ("highlights", listing_highlight("Made with machine embroidery.")),
                        ("aplus", listing_aplus("Made with machine embroidery."))):
            au = audit(L, claims)
            self.assertFalse(blocked(au, "machine embroidery"),
                             f"{name}: a VERIFIED factual phrase was wrongly blocked")


# ================================================================ ingestion guard
class OwnerFactIngestionGuard(unittest.TestCase):
    def test_21_unsafe_owner_value_cannot_become_publishable_verified_evidence(self):
        _f, claims = facts_and_claims(care_instructions="never fades")
        rec = claims.claim("care")
        self.assertIsNotNone(rec)
        self.assertFalse(rec.get("publishable"),
                         "an owner-typed permanence claim became publishable VERIFIED evidence")

    def test_22_raw_owner_value_is_preserved_for_audit(self):
        facts, _c = facts_and_claims(care_instructions="never fades")
        self.assertEqual(facts.get("care_instructions")["value"], "never fades",
                         "the raw owner value must be preserved, never silently rewritten")

    def test_23_structured_blocker_has_every_required_field(self):
        b = UCP.screen_owner_value("care_instructions", "never fades")
        self.assertIsNotNone(b, "the ingestion guard did not screen a prohibited value")
        for field in ("status", "surface", "phrase", "policy_class", "required_concept_ids",
                      "evidence_state", "owner_fact_field", "reason_code", "next_action"):
            self.assertIn(field, b)
        self.assertEqual(b["reason_code"], UCP.UNSAFE_OWNER_FACT_VALUE)
        self.assertEqual(b["owner_fact_field"], "care_instructions")

    def test_24_ambiguous_owner_value_returns_policy_decision_required(self):
        b = UCP.screen_owner_value("production_location", "made in the usa")
        self.assertIsNotNone(b)
        self.assertEqual(b["reason_code"], UCP.POLICY_DECISION_REQUIRED)

    def test_25_safe_ordinary_value_follows_existing_behaviour(self):
        _f, claims = facts_and_claims()
        rec = claims.claim("care")
        self.assertTrue(rec.get("publishable"),
                        "an ordinary safe care value must still become publishable evidence")
        self.assertIsNone(UCP.screen_owner_value("care_instructions",
                                                 "machine wash cold, tumble dry low"))

    def test_26_factual_phrase_in_a_fact_value_is_not_blocked_at_ingestion(self):
        """A factual phrase still has to clear its concept at the surface; it is not an unsafe value."""
        self.assertIsNone(UCP.screen_owner_value("decoration_method", "machine embroidery"))


# ================================================================ authority invariants
class AuthorityInvariants(unittest.TestCase):
    def test_27_one_list_only_page_auditor_reexports_the_authority(self):
        self.assertEqual(tuple(PA.UNSAFE_CLAIM_PHRASES), tuple(UCP.UNSAFE_CLAIM_PHRASES))
        self.assertEqual(PA.UNSAFE_CLAIM_PHRASES_BY_CATEGORY, UCP.UNSAFE_CLAIM_PHRASES_BY_CATEGORY)

    def test_28_every_phrase_has_exactly_one_canonical_record(self):
        for phrase in UCP.phrases_for("acrylic"):
            self.assertIsNotNone(UCP.policy_for(phrase), f"{phrase} has no canonical record")

    def test_29_every_mapped_concept_is_a_real_claim_concept(self):
        real = {s.concept for s in CE.CLAIM_SPECS}
        for row in UCP.manifest()["rows"]:
            for c in row["required_concept_ids"]:
                self.assertIn(c, real, f"{row['phrase']} maps to unknown concept {c}")

    def test_30_only_factual_phrases_are_ever_clearable(self):
        for row in UCP.manifest()["rows"]:
            if row["policy_class"] != UCP.FACTUAL_AND_EVIDENCE_VERIFIABLE:
                self.assertNotEqual(row["clearance_rule"], UCP.REQUIRE_ALL_CONCEPTS,
                                    f"{row['phrase']} is clearable but is not a factual class")
            else:
                self.assertTrue(row["required_concept_ids"],
                                f"{row['phrase']} is clearable but maps to no concept")


class SourceLineEndingCharacterization(unittest.TestCase):
    """Why any source-matching gate must normalise newlines before it matches.

    .gitattributes pins eol=lf, but a Windows checkout materialises CRLF. A mutation harness whose
    anchors are joined with "\\n" therefore matches NOTHING on a fresh Windows clone: every mutant
    reports ANCHOR_NOT_FOUND and the gate silently measures zero coverage. That happened once
    already. This test characterises the trap so a replay cannot rediscover it the hard way.
    """

    TARGETS = ("listing/page_auditor.py", "listing/claim_evidence.py",
               "listing/unsafe_claim_policy.py")

    def test_31_lf_joined_anchors_miss_a_crlf_checkout_but_normalised_ones_do_not(self):
        checked = 0
        for rel in self.TARGETS:
            path = os.path.join(ROOT, *rel.split("/"))
            if not os.path.exists(path):
                continue
            with open(path, "rb") as f:
                raw = f.read()
            text = raw.decode("utf-8")
            logical = text.replace("\r\n", "\n")
            first = logical.split("\n", 1)[0]
            two_line_anchor = "\n".join(logical.split("\n")[:2])
            if b"\r\n" in raw:
                checked += 1
                self.assertNotIn(two_line_anchor, text,
                                 f"{rel}: an LF-joined anchor unexpectedly matched a CRLF file")
            self.assertIn(two_line_anchor, logical,
                          f"{rel}: the normalised anchor must always match")
            self.assertTrue(first is not None)
        self.assertGreater(checked, 0,
                           "no CRLF source found: this platform cannot characterise the trap, so a "
                           "harness that matches raw bytes must not be assumed portable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
