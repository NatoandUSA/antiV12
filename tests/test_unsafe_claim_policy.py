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


def phrase_collection_offenders(source):
    """Places in *source* where canonical unsafe phrases are GATHERED into a screening collection.

    Two rules, both keyed on the grouping rather than on any name, so renaming hides nothing:
      (a) one collection literal holding >= 3 canonical phrases, anywhere -- including function
          locals and class attributes;
      (b) >= 3 distinct canonical phrases aggregated across ALL literals in one FUNCTION or CLASS
          scope, which catches a list split in two or concatenated inline to duck rule (a).

    Module scope is excluded from (b) on purpose: bullet_engine and description_engine each scatter
    2-phrase concept maps across the module, and those are copy mappings, not screening lists.
    Anything gathered at module level and actually used is bound to a module attribute, which the
    runtime scan in test_40 already sees however it was constructed.
    """
    import ast
    canonical = {UCP.normalize_text(p) for p in UCP.phrases_for("acrylic")}

    def literal_hits(node):
        return {UCP.normalize_text(e.value) for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
                and UCP.normalize_text(e.value) in canonical}

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):                                              # rule (a)
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            hits = literal_hits(node)
            if len(hits) >= 3:
                out.append(f"{node.lineno}: one literal gathers {len(hits)} canonical phrases")
    for node in ast.walk(tree):                                              # rule (b)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            hits = set()
            for n in ast.walk(node):
                if isinstance(n, (ast.Tuple, ast.List, ast.Set)):
                    hits |= literal_hits(n)
            if len(hits) >= 3:
                out.append(f"{node.lineno}: scope {node.name!r} gathers {len(hits)} across literals")
    return out


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
            UCP.ABSOLUTE_OR_PERMANENCE: 17,
            UCP.GUARANTEE_OR_PROMISE: 22,
            UCP.UNVERIFIABLE_SUPERLATIVE: 16,
            UCP.AMBIGUOUS: 6,
            UCP.FACTUAL_AND_EVIDENCE_VERIFIABLE: 15,
        }, "shared phrase classification changed")
        every = collections.Counter(r["policy_class"] for r in UCP.manifest()["rows"])
        self.assertEqual(sum(every.values()), 123)
        self.assertEqual(every[UCP.FACTUAL_AND_EVIDENCE_VERIFIABLE], 15,
                         "only the shared vocabulary has clearable phrases; acrylic has none")

    def test_01_every_hard_block_phrase_survives_owner_self_authorisation(self):
        self.assertEqual(len(HARD_BLOCK), 55, "hard-block set size changed")
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


class ShipFromCountryValueBinding(unittest.TestCase):
    """Fulfillment origin must bind to a structured country VALUE, not to shipping prose.

    The first version of this branch bound "ships from the us" to the generic `shipping_method`
    concept and authorised on verified-ness alone, so a VERIFIED shipping method of ANY value cleared
    it. claim_evidence._has_us_shipping only substring-scans the owner's free text, which is the same
    "owner typed it, therefore it is true" pattern the whole policy exists to remove.
    """

    ORIGIN_PHRASES = ("ships from the us", "shipped from the us")

    def test_32_us_ship_from_country_authorises_fulfillment_origin_wording(self):
        _f, claims = facts_and_claims(ship_from_country="US")
        for phrase in self.ORIGIN_PHRASES:
            v = UCP.evaluate(phrase, claims, UCP.SURFACE_CLAIMS)
            self.assertTrue(v["authorized"], f"{phrase} must clear on a VERIFIED US ship-from country")
            self.assertEqual(v["required_concept_ids"], ["ship_from_country"])

    def test_33_non_us_ship_from_country_blocks_the_phrase(self):
        _f, claims = facts_and_claims(ship_from_country="Vietnam")
        for phrase in self.ORIGIN_PHRASES:
            v = UCP.evaluate(phrase, claims, UCP.SURFACE_CLAIMS)
            self.assertFalse(v["authorized"],
                             f"{phrase} cleared with a VERIFIED NON-US ship-from country")
            self.assertEqual(v["block"]["reason_code"], UCP.MATCHING_CONCEPT_UNVERIFIED)

    def test_34_absent_ship_from_country_blocks_the_phrase(self):
        _f, claims = facts_and_claims()
        for phrase in self.ORIGIN_PHRASES:
            self.assertFalse(UCP.evaluate(phrase, claims, UCP.SURFACE_CLAIMS)["authorized"],
                             f"{phrase} cleared with no ship-from country at all")

    def test_35_shipping_prose_alone_cannot_authorise_fulfillment_origin(self):
        """The exact regression: owner prose saying it ships from the US is not evidence."""
        _f, claims = facts_and_claims(shipping_method="Ships from the US with tracking")
        self.assertTrue(UCP._concept_verified(claims, "shipping_method"),
                        "fixture guard: the generic shipping concept IS verified here")
        for phrase in self.ORIGIN_PHRASES:
            self.assertFalse(UCP.evaluate(phrase, claims, UCP.SURFACE_CLAIMS)["authorized"],
                             f"{phrase} was authorised by free-text shipping prose")

    def test_36_us_fulfillment_origin_never_authorises_manufacturing_origin(self):
        """Shipping from the US says nothing about where the item was made."""
        _f, claims = facts_and_claims(ship_from_country="US")
        for phrase in ("made in the usa", "made in the us", "printed in the usa"):
            v = UCP.evaluate(phrase, claims, UCP.SURFACE_CLAIMS)
            self.assertFalse(v["authorized"],
                             f"a US ship-from country authorised manufacturing origin {phrase!r}")
            self.assertEqual(v["block"]["reason_code"], UCP.POLICY_DECISION_REQUIRED)

    def test_37_an_unrelated_us_fact_cannot_authorise_fulfillment_origin(self):
        """A US audience or a US-sounding address is not a ship-from country."""
        _f, claims = facts_and_claims(audience="US nurses", production_location="US")
        for phrase in self.ORIGIN_PHRASES:
            self.assertFalse(UCP.evaluate(phrase, claims, UCP.SURFACE_CLAIMS)["authorized"],
                             f"{phrase} was authorised by an unrelated US fact")


class TrackingBinding(unittest.TestCase):
    """Tracking binds to its own dedicated concept, never to generic shipping."""

    TRACKING_PHRASES = ("tracking included", "with tracking", "includes tracking")

    def test_38_dedicated_tracking_concept_authorises_tracking_wording(self):
        _f, claims = facts_and_claims()          # SAFE_FACTS carries a tracking fact
        self.assertTrue(UCP._concept_verified(claims, "tracking"), "fixture guard")
        for phrase in self.TRACKING_PHRASES:
            self.assertTrue(UCP.evaluate(phrase, claims, UCP.SURFACE_CLAIMS)["authorized"], phrase)

    def test_39_generic_shipping_verification_cannot_authorise_tracking(self):
        _f, claims = facts_and_claims(tracking="")
        self.assertTrue(UCP._concept_verified(claims, "shipping_method"),
                        "fixture guard: shipping IS verified, tracking is not")
        self.assertFalse(UCP._concept_verified(claims, "tracking"), "fixture guard")
        for phrase in self.TRACKING_PHRASES:
            v = UCP.evaluate(phrase, claims, UCP.SURFACE_CLAIMS)
            self.assertFalse(v["authorized"],
                             f"{phrase} was authorised by generic shipping verification")
            self.assertEqual(v["block"]["reason_code"], UCP.MATCHING_CONCEPT_UNVERIFIED)


class TrackingClaimMustNotAssertTheOppositeOfItsFact(unittest.TestCase):
    """The root cause of the tracking hole, which is in claim_evidence and PREDATES this branch.

    Three claim specs assert a FIXED sentence that ignores the fact value:
    real_machine_embroidery, satin_stitch and tracking. The first two carry a `guard` that qualifies
    the value -- that is the codebase's own rule for a fixed assertion. tracking was the only one
    without one, so `tracking = "we do not offer tracking"` marked VERIFIED produced the customer-
    facing claim "Order tracking included." at state VERIFIED.

    That is a false fulfillment promise generated by the claim engine itself, before any unsafe-claim
    policy sees it. It reaches bullets, description and A+ copy on ae8d60b today. The unsafe-claim
    policy was doing its job -- it authorised a VERIFIED claim; the claim was the lie.

    Guards fail CLOSED: an unrecognised value does not verify.
    """

    AFFIRMATIVE = ["tracking number provided", "tracking number provided for every order",
                   "USPS Ground Advantage with tracking", "yes", "included"]
    NEGATIVE = ["no tracking provided", "tracking not available", "we do not offer tracking",
                "false", "no", "without tracking", "tracking unavailable"]

    def test_44_an_affirmative_tracking_fact_still_verifies_and_publishes(self):
        for val in self.AFFIRMATIVE:
            _f, claims = facts_and_claims(tracking=val)
            rec = claims.claim("tracking")
            self.assertEqual(rec["verification_state"], "VERIFIED",
                             f"affirmative tracking value {val!r} must still verify")
            self.assertTrue(UCP.evaluate("tracking included", claims,
                                         UCP.SURFACE_CLAIMS)["authorized"], val)

    def test_45_a_negative_tracking_fact_must_not_verify(self):
        """THE DEFECT: the claim engine asserted 'Order tracking included.' for these."""
        leaked = []
        for val in self.NEGATIVE:
            _f, claims = facts_and_claims(tracking=val)
            rec = claims.claim("tracking")
            if rec["verification_state"] == "VERIFIED" or rec["publishable"]:
                leaked.append((val, rec["proposed_text"]))
        self.assertEqual(leaked, [],
                         "a tracking fact that DENIES tracking produced a VERIFIED claim asserting "
                         "tracking is included: "
                         + "; ".join(f"{v!r} -> {t!r}" for v, t in leaked))

    def test_46_a_negative_tracking_fact_cannot_authorise_tracking_wording(self):
        for val in self.NEGATIVE:
            _f, claims = facts_and_claims(tracking=val)
            for phrase in ("tracking included", "with tracking", "includes tracking"):
                self.assertFalse(
                    UCP.evaluate(phrase, claims, UCP.SURFACE_CLAIMS)["authorized"],
                    f"tracking={val!r} authorised {phrase!r}")

    def test_47_every_fixed_assertion_spec_carries_a_guard(self):
        """The invariant behind the fix, so the next fixed-assertion claim cannot skip its guard."""
        import inspect
        offenders = []
        for spec in CE.CLAIM_SPECS:
            if not spec.evidence_fields:
                continue                                  # no backing field -> blocked by design
            try:
                src = inspect.getsource(spec._text)
            except (OSError, TypeError):
                continue
            body = src.split("lambda v:", 1)[-1] if "lambda v:" in src else src
            references_value = any(tok in body for tok in ("{v", "(v", "v)", "v."))
            if not references_value and spec._guard is None:
                offenders.append(spec.concept)
        self.assertEqual(offenders, [],
                         "these specs assert a fixed sentence that ignores the fact value, with no "
                         "guard qualifying that value: " + ", ".join(offenders))


class PersonalizationPromiseAndMadeToOrderMustNotAssertTheOppositeOfTheirFact(unittest.TestCase):
    """The same class of defect as tracking (test_44-47 above), for the two fixed-assertion specs
    6C added on a branch authored before this guard convention existed: exact_personalization_promise
    ("Embroidered exactly as you enter it.") and made_to_order ("Made to order after you purchase.").
    Found only by combining 6C and 6D-unsafe-claim-policy -- neither branch's own suite could see it.

    Unlike tracking, both canonical phrases ("exactly as you enter", "made to order") are classed
    GUARANTEE_OR_PROMISE in unsafe_claim_policy -- ALWAYS_BLOCK, unconditionally, regardless of
    evidence -- so UCP.evaluate() can never authorise them and is not the right assertion here.

    A second consequence, discovered by running this (not assumed): each spec's own FIXED template
    text ("Made to order after you purchase.", "Embroidered exactly as you enter it.") literally
    contains its own now-hard-blocked phrase, so build_claim_record's ingestion guard --
    UCP.screen_owner_value scanning `proposed`, not only the raw owner value -- refuses `publishable`
    unconditionally, for EVERY value, guard or no guard. That is correct and not this fix's to
    change: it is the same protection this whole branch exists to add, now also catching the
    system's own generated text, not only what an owner types. What the guard controls is
    verification_state -- whether the underlying fact bookkeeping is honest -- which matters to any
    consumer that reads claims.claim(concept) directly rather than only the copy-surface audit.
    """

    PROMISE_AFFIRMATIVE = ["yes", "true", "exact", "exactly as entered", "embroidered exactly as submitted"]
    PROMISE_NEGATIVE = ["", "unknown", "maybe", "depends", "not exact", "we may adjust",
                        "artist discretion", "no", "false", "we don't guarantee exact wording"]

    ORDER_AFFIRMATIVE = ["yes", "true", "made to order", "made-to-order", "produced after purchase"]
    ORDER_NEGATIVE = ["", "unknown", "maybe", "no", "false", "ready-made", "ready-made stock only",
                      "premade", "pre-made", "in stock", "we don't make to order"]

    def test_48_affirmative_personalization_promise_verifies(self):
        for val in self.PROMISE_AFFIRMATIVE:
            _f, claims = facts_and_claims(exact_personalization_promise=val)
            rec = claims.claim("exact_personalization_promise")
            self.assertEqual(rec["verification_state"], "VERIFIED", f"{val!r} must verify")
            # NOT publishable: the fixed sentence itself contains "exactly as you enter", a
            # GUARANTEE_OR_PROMISE phrase 6D hard-blocks regardless of evidence. Correct, not a bug
            # in this guard -- see the class docstring.
            self.assertFalse(rec["publishable"], val)
            self.assertEqual(rec.get("unsafe_claim_block", {}).get("phrase"), "exactly as you enter")

    def test_49_denied_personalization_promise_must_not_verify_or_publish(self):
        """THE DEFECT: without the guard, every one of these produced a VERIFIED claim asserting
        'Embroidered exactly as you enter it.' regardless of what the owner actually wrote."""
        leaked = []
        for val in self.PROMISE_NEGATIVE:
            _f, claims = facts_and_claims(exact_personalization_promise=val)
            rec = claims.claim("exact_personalization_promise")
            if rec["verification_state"] == "VERIFIED" or rec["publishable"]:
                leaked.append((val, rec["proposed_text"]))
        self.assertEqual(leaked, [],
                         "a value that does not affirm exact personalization produced a VERIFIED "
                         "claim asserting it does: " + "; ".join(f"{v!r} -> {t!r}" for v, t in leaked))

    def test_50_affirmative_made_to_order_verifies(self):
        for val in self.ORDER_AFFIRMATIVE:
            _f, claims = facts_and_claims(made_to_order=val)
            rec = claims.claim("made_to_order")
            self.assertEqual(rec["verification_state"], "VERIFIED", f"{val!r} must verify")
            # NOT publishable: the fixed sentence itself contains "made to order", a
            # GUARANTEE_OR_PROMISE phrase 6D hard-blocks regardless of evidence. Correct, not a bug
            # in this guard -- see the class docstring.
            self.assertFalse(rec["publishable"], val)
            self.assertEqual(rec.get("unsafe_claim_block", {}).get("phrase"), "made to order")

    def test_51_denied_made_to_order_must_not_verify_or_publish(self):
        """THE DEFECT: without the guard, 'ready-made stock only' marked VERIFIED produced a
        VERIFIED claim asserting 'Made to order after you purchase.'."""
        leaked = []
        for val in self.ORDER_NEGATIVE:
            _f, claims = facts_and_claims(made_to_order=val)
            rec = claims.claim("made_to_order")
            if rec["verification_state"] == "VERIFIED" or rec["publishable"]:
                leaked.append((val, rec["proposed_text"]))
        self.assertEqual(leaked, [],
                         "a value that does not affirm made-to-order production produced a VERIFIED "
                         "claim asserting it does: " + "; ".join(f"{v!r} -> {t!r}" for v, t in leaked))

    def test_52_both_canonical_phrases_stay_always_blocked_regardless_of_evidence(self):
        """Defense in depth, asserted rather than assumed: even a fully VERIFIED claim must not
        authorise these phrases on the copy surface -- GUARANTEE_OR_PROMISE is a second, independent
        backstop behind the guard, not a substitute for it (bookkeeping can still be wrong even when
        this backstop happens to catch the copy-surface consequence)."""
        _f, claims = facts_and_claims(exact_personalization_promise="yes", made_to_order="yes")
        for phrase in ("exactly as you enter", "embroidered exactly", "made to order"):
            self.assertFalse(UCP.evaluate(phrase, claims, UCP.SURFACE_CLAIMS)["authorized"],
                             f"{phrase!r} must never authorise, even fully VERIFIED")


class CanonicalAuthorityIsSole(unittest.TestCase):
    """No second LIVE unsafe-phrase authority may exist anywhere in the tree.

    Value-based, not name-based: it looks for containers that actually hold canonical phrases, so
    renaming a list does not make it invisible. This project has already been bitten twice by
    name-keyed static guards failing open.
    """

    def test_40_no_module_outside_the_authority_holds_a_phrase_collection(self):
        import importlib
        import pkgutil
        canonical = set(UCP.phrases_for("acrylic"))
        allowed = {"unsafe_claim_policy", "page_auditor"}      # authority + its re-export
        offenders = []
        listing_dir = os.path.join(ROOT, "listing")
        for mod in pkgutil.iter_modules([listing_dir]):
            if mod.name in allowed:
                continue
            try:
                m = importlib.import_module(mod.name)
            except Exception:
                continue
            for attr in dir(m):
                try:
                    val = getattr(m, attr)
                except Exception:
                    continue
                if isinstance(val, (tuple, list, set, frozenset)):
                    hits = {x for x in val if isinstance(x, str) and x in canonical}
                    if len(hits) >= 3:
                        offenders.append(f"{mod.name}.{attr} holds {len(hits)} canonical phrases")
        self.assertEqual(offenders, [],
                         "a second live unsafe-phrase authority exists and can diverge from "
                         "listing/unsafe_claim_policy.py: " + "; ".join(offenders))

    def test_40b_no_phrase_collection_anywhere_including_function_locals_and_split_literals(self):
        """The runtime scan above only sees MODULE-level attributes. A screening list declared inside
        a function walks straight past it -- mutant M11 did exactly that and survived.

        Two source-level rules, both keyed on phrases being GROUPED rather than on any name:

          (a) any single collection literal holding >= 3 canonical phrases, anywhere;
          (b) >= 3 distinct canonical phrases aggregated across ALL literals in one FUNCTION or
              CLASS scope -- which catches a list deliberately split in two, or concatenated inline,
              to duck rule (a).

        Module scope is deliberately excluded from (b): bullet_engine and description_engine each
        scatter 2-phrase concept maps across the module, which are copy mappings, not screening
        lists. Anything gathered at module level and actually USED is bound to a module attribute,
        which the runtime scan above already sees however it was constructed.

        Probed and caught: module global, function local, class attribute, split literals, inline
        concatenation, helper-returned collection. Probed and correctly NOT flagged: an alias of the
        canonical authority, and a filtered view derived from it -- neither is a second authority.
        Known residual, disclosed: a phrase list loaded at runtime from an external data file is not
        visible to source scanning, and is caught only if it lands in a module attribute.
        """
        offenders = []
        listing_dir = os.path.join(ROOT, "listing")
        for fn in sorted(os.listdir(listing_dir)):
            if not fn.endswith(".py") or fn == "unsafe_claim_policy.py":
                continue
            with open(os.path.join(listing_dir, fn), encoding="utf-8") as f:
                offenders += [f"{fn}:{o}" for o in phrase_collection_offenders(f.read())]
        self.assertEqual(offenders, [],
                         "a second live unsafe-phrase authority exists as a phrase collection: "
                         + "; ".join(offenders))

    def test_40c_the_guard_rules_actually_fire_on_evasions_and_spare_legitimate_code(self):
        """A clean tree exercises NEITHER rule. Without this, deleting rule (a) or rule (b) is
        invisible -- the same unexercised-backstop hole this project has already been bitten by, and
        the reason mutant M11 survived the first version of this guard. The forbidden candidates are
        therefore constructed HERE rather than waited for.
        """
        must_catch = {
            "module global literal": 'BAD = ("made to last", "built to last", "will not fade")',
            "function local literal":
                'def f(b):\n    x = ("made to last", "built to last", "will not fade")\n    return x',
            "class attribute":
                'class S:\n    P = ["made to last", "built to last", "will not fade"]',
            "split across two literals in one function":
                'def f(b):\n    a = ("made to last", "built to last")\n'
                '    c = ("will not fade", "buttery soft")\n    return a + c',
            "concatenated inline":
                'def f(b):\n    return [p for p in ("made to last", "built to last")'
                ' + ("will not fade",) if p in b]',
            "helper-returned collection":
                'def _p():\n    return ["made to last", "built to last", "will not fade"]',
        }
        must_spare = {
            "alias of the canonical authority":
                'import unsafe_claim_policy as UCP\nMY_VIEW = UCP.UNSAFE_CLAIM_PHRASES',
            "filtered view derived from the authority":
                'import unsafe_claim_policy as UCP\n'
                'def f():\n    return [p for p in UCP.phrases_for(None) if p.startswith("m")]',
            "two unrelated phrases in a copy map (the bullet_engine shape)":
                'JOBS = {"a": ("real machine embroidery", "satin stitch"), "b": ("made to order",)}',
        }
        for name, src in must_catch.items():
            self.assertTrue(phrase_collection_offenders(src),
                            f"guard failed to catch a second authority via: {name}")
        for name, src in must_spare.items():
            self.assertEqual(phrase_collection_offenders(src), [],
                             f"guard wrongly flagged legitimate code: {name}")

    def test_41_aplus_builder_self_check_uses_the_canonical_authority(self):
        import aplus_builder as AB
        self.assertFalse(hasattr(AB, "_UNSAFE_PHRASES"),
                         "aplus_builder still defines its own phrase subset")
        src = open(os.path.join(ROOT, "listing", "aplus_builder.py"), encoding="utf-8").read()
        self.assertIn("unsafe_claim_policy", src,
                      "aplus_builder does not consult the canonical policy authority")


class SurfaceAllowlists(unittest.TestCase):
    def test_42_every_clearable_record_declares_an_explicit_allowlist(self):
        for row in UCP.manifest()["rows"]:
            if row["clearance_rule"] == UCP.REQUIRE_ALL_CONCEPTS:
                self.assertTrue(row["allowed_surfaces"],
                                f"{row['phrase']} is clearable but declares no allowed surfaces")
                for s in row["allowed_surfaces"]:
                    self.assertIn(s, UCP.ALL_SURFACES, f"{row['phrase']} names unknown surface {s}")

    def test_43_a_clearable_phrase_is_refused_on_a_surface_it_does_not_list(self):
        rec = UCP.policy_for("measured from the real garment")
        self.assertNotIn(UCP.SURFACE_ITEM_HIGHLIGHTS, rec["allowed_surfaces"],
                         "fixture guard: this record must genuinely restrict a surface")
        _f, claims = facts_and_claims()
        allowed = UCP.evaluate("measured from the real garment", claims, UCP.SURFACE_CLAIMS)
        refused = UCP.evaluate("measured from the real garment", claims,
                               UCP.SURFACE_ITEM_HIGHLIGHTS)
        self.assertTrue(allowed["authorized"], "permitted surface must still clear")
        self.assertFalse(refused["authorized"], "a surface outside the allowlist must be refused")
        self.assertEqual(refused["block"]["evidence_state"], "SURFACE_NOT_PERMITTED")


class ContractionsMustNotBypassThePolicy(unittest.TestCase):
    """"won't fade" is the way a human actually writes it, and it slipped through completely.

    normalize_text replaced the apostrophe with a SPACE, so "won't fade" became "won t fade" while
    the canonical entry is "wont fade". The informal spelling was blocked and the correct English
    one published. Found by auditing compliance/category_config.json, which carries both spellings.
    """

    BYPASS = ["This design won't fade.", "It won’t fade.", "The acrylic won't crack.",
              "The print won't peel.", "It won't shrink."]

    def test_52_apostrophe_forms_are_blocked_exactly_like_the_plain_forms(self):
        leaked = []
        for copy in self.BYPASS:
            if not UCP.findings_for_text(copy, None, UCP.SURFACE_CLAIMS, category="acrylic"):
                leaked.append(copy)
        self.assertEqual(leaked, [],
                         "contraction spellings bypassed the policy entirely: " + "; ".join(leaked))

    def test_53_normalisation_collapses_the_apostrophe_rather_than_splitting_the_word(self):
        self.assertEqual(UCP.normalize_text("won't fade"), UCP.normalize_text("wont fade"))
        self.assertEqual(UCP.normalize_text("won’t crack"), UCP.normalize_text("wont crack"))


class CanonicalAuthorityMustCoverEveryLiveBlocklist(unittest.TestCase):
    """A second live blocklist exists in DATA, not source: compliance/category_config.json.

    compliance/listing_validate.py loads it and hard-fails on claim_rules.hard_fail and
    claim_rules.durability_absolute. pipeline.py runs that validator as a stage, so it is live.

    It is NOT a safety hole -- it is a pure blocklist with no permit path, so it can only ever block
    MORE than the canonical authority, and it hard-blocks zero canonical FACTUAL phrases. The real
    problem runs the other way: it knows about medical, regulatory and guarantee claims the canonical
    authority had never heard of, and the Phase 6 chain does not run it, so on bullets, item
    highlights and A+ copy those phrases were screened by nothing at all.

    superlative_warn is deliberately NOT required here: those are warnings in that validator, not
    hard failures, and importing them as blocking phrases would change severity without a decision.
    """

    @staticmethod
    def _compliance_hard_blocklist():
        with open(os.path.join(ROOT, "compliance", "category_config.json"), encoding="utf-8") as f:
            rules = json.load(f)["claim_rules"]
        out = set()
        for key in ("hard_fail", "durability_absolute"):
            out |= {UCP.normalize_text(p) for p in rules.get(key, [])}
        # Exclude only DEGENERATE normalisations: a single token under four characters. "#1"
        # normalises to the bare token "1", and this authority matches whole normalised tokens, so
        # importing it would block every listing containing a size, quantity or measurement. The
        # compliance validator substring-matches the raw "#1" and so does not have that problem.
        return {p for p in out if p and not (" " not in p and len(p) < 4)}

    def test_54_every_hard_blocked_compliance_phrase_has_a_canonical_record(self):
        canonical = {UCP.normalize_text(r["phrase"]) for r in UCP.manifest()["rows"]}
        missing = sorted(self._compliance_hard_blocklist() - canonical)
        self.assertEqual(missing, [],
                         "compliance/listing_validate.py hard-fails phrases the canonical authority "
                         "has no record for, so the Phase 6 surfaces do not screen them at all: "
                         + ", ".join(missing))

    def test_55_the_imported_medical_and_guarantee_claims_are_never_clearable(self):
        for phrase in ("clinically proven", "fda approved", "cures", "antibacterial",
                       "money back guarantee", "lifetime warranty"):
            rec = UCP.policy_for(phrase)
            self.assertIsNotNone(rec, f"{phrase!r} has no canonical record")
            self.assertNotEqual(rec["clearance_rule"], UCP.REQUIRE_ALL_CONCEPTS,
                                f"{phrase!r} must never be clearable by ordinary product evidence")

    def test_56_a_medical_claim_is_blocked_on_every_phase6_surface(self):
        _f, claims = facts_and_claims()
        for surface, listing in (("bullets", listing_bullet("Clinically proven to help.")),
                                 ("highlights", listing_highlight("Clinically proven to help.")),
                                 ("aplus", listing_aplus("Clinically proven to help."))):
            au = audit(listing, claims)
            self.assertTrue(blocked(au, "clinically proven"),
                            f"{surface}: a medical claim was not blocked")


class DataFilePhraseAuthoritiesAreRegistered(unittest.TestCase):
    """Closing the CLASS, not just the instance.

    The previous round absorbed compliance/category_config.json's blocking phrases and pinned that
    they stay covered (test_54). It did NOT stop a NEW screening list appearing in some other data
    file, which is the residual that started this whole thread: source scanning cannot see phrases
    that live in JSON.

    Measured across the tree: of 61 tracked JSON files, exactly two carry arrays of phrase-like
    strings. Both are registered below with their purpose, because they answer DIFFERENT questions:

      compliance/category_config.json  unsafe CLAIM blocking -> must stay covered by the canonical
                                       authority (test_54 enforces that)
      compliance/ip_library.json       trademark / IP blocking -> brands, characters, celebrities,
                                       universities. A different question entirely; it is not a
                                       rival unsafe-claim authority and must NOT be absorbed.

    A new phrase-bearing data file fails this test on purpose: someone must classify it rather than
    let it become a silent third authority.
    """

    REGISTERED = {
        "compliance/category_config.json": "unsafe-claim blocking; covered by the canonical authority",
        "compliance/ip_library.json": "trademark/IP blocking; a different question, deliberately separate",
    }

    @staticmethod
    def _phrase_like(s):
        return (isinstance(s, str) and 2 <= len(s.split()) <= 5 and s == s.lower()
                and s.replace(" ", "").replace("-", "").replace("'", "").isalpha())

    @classmethod
    def _has_phrase_array(cls, obj):
        if isinstance(obj, dict):
            return any(cls._has_phrase_array(v) for v in obj.values())
        if isinstance(obj, list):
            if sum(1 for x in obj if cls._phrase_like(x)) >= 5:
                return True
            return any(cls._has_phrase_array(v) for v in obj)
        return False

    def test_57_every_data_file_carrying_a_phrase_list_is_registered(self):
        import subprocess
        tracked = subprocess.run(["git", "ls-files", "*.json"], cwd=ROOT,
                                 capture_output=True, text=True).stdout.split()
        unregistered = []
        for rel in tracked:
            if "PROOF" in rel or "REPORT" in rel:      # generated evidence, not an authority
                continue
            path = os.path.join(ROOT, *rel.split("/"))
            try:
                with open(path, encoding="utf-8") as f:
                    doc = json.load(f)
            except (OSError, ValueError):
                continue
            if self._has_phrase_array(doc) and rel not in self.REGISTERED:
                unregistered.append(rel)
        self.assertEqual(unregistered, [],
                         "a data file carries a phrase list and is not registered, so it could be a "
                         "silent second screening authority that no source scan can see: "
                         + ", ".join(unregistered))

    def test_58_the_ip_library_is_not_absorbed_into_the_claim_authority(self):
        """It answers a different question. Absorbing brand names into the unsafe-CLAIM vocabulary
        would block legitimate copy and confuse two separate compliance concerns."""
        canonical = {UCP.normalize_text(r["phrase"]) for r in UCP.manifest()["rows"]}
        with open(os.path.join(ROOT, "compliance", "ip_library.json"), encoding="utf-8") as f:
            ip = json.load(f)
        names = {UCP.normalize_text(s) for group in (ip.get("block") or {}).values()
                 if isinstance(group, list) for s in group if isinstance(s, str)}
        overlap = sorted(n for n in (names & canonical) if n)
        self.assertEqual(overlap, [],
                         "IP/trademark entries leaked into the unsafe-claim authority: " + ", ".join(overlap))


class ImportedPhrasesCarryTheirProvenance(unittest.TestCase):
    """Where a phrase came from is part of the record, not a comment.

    31 phrases were absorbed from another compliance authority. Without provenance on the record,
    the next reader cannot tell which entries this module owns and which mirror a second source that
    still has to stay in step with it.
    """

    def test_59_every_record_declares_a_source(self):
        missing = [r["phrase"] for r in UCP.manifest()["rows"] if not r.get("source")]
        self.assertEqual(missing, [], "records with no provenance: " + ", ".join(missing[:10]))

    def test_60_the_absorbed_phrases_name_the_file_they_came_from(self):
        for phrase in ("clinically proven", "fda approved", "money back guarantee", "waterproof"):
            rec = UCP.policy_for(phrase)
            self.assertIsNotNone(rec, phrase)
            self.assertIn("category_config.json", rec["source"],
                          f"{phrase!r} was absorbed from the compliance blocklist but does not say so")

    def test_61_phrases_this_module_owns_are_not_mislabelled_as_imported(self):
        for phrase in ("never fades", "buttery soft", "machine embroidery"):
            self.assertNotIn("category_config.json", UCP.policy_for(phrase)["source"],
                             f"{phrase!r} is native to this authority, not imported")


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
