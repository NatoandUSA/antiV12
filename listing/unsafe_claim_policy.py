#!/usr/bin/env python3
"""listing.unsafe_claim_policy — the ONE authority on unsafe customer-facing claim phrases.

WHY THIS EXISTS

The auditor used to authorise an unsafe phrase by concatenating every publishable claim's text into
one normalised bag of words and asking whether the phrase appeared in it. Claim text is built as
``spec.text_for(value)``, which substitutes the owner's RAW FACT VALUE into a template. The result:
an owner could authorise ANY phrase by typing it into a free-text fact field and marking it VERIFIED.
Measured before this module existed: 45 of 45 phrases were self-authorising, 26 of them in classes
that must never clear. ``care_instructions = "never fades"`` put a permanence claim into A+ copy.

Two further consequences of bag-of-words matching, both removed here:
  * no concept binding at all — a CARE fact cleared a DURABILITY claim;
  * a phrase could in principle straddle two adjacent claim texts that neither asserts.

THE MODEL

Every phrase has exactly one canonical record: phrase, policy_class, required_concept_ids,
allowed_surfaces, clearance_rule. Authorisation is evaluated per phrase hit against STRUCTURED claim
evidence — never against raw text. Unknown phrases fail closed.

A phrase may clear only when it asserts the CONCEPT itself. A phrase that encodes a specific VALUE of
a concept ("made in the usa", "10 14 business days") cannot be authorised by concept verification
alone, because a VERIFIED production_location of "Hue, Vietnam" would otherwise clear "made in the
usa". Those are classified AMBIGUOUS and fail closed pending an owner decision rather than guessed.
"""
import re

SCHEMA_VERSION = "unsafe-claim-policy-v1"

# ---------------------------------------------------------------- policy classes
FACTUAL_AND_EVIDENCE_VERIFIABLE = "FACTUAL_AND_EVIDENCE_VERIFIABLE"
ABSOLUTE_OR_PERMANENCE = "ABSOLUTE_OR_PERMANENCE"
GUARANTEE_OR_PROMISE = "GUARANTEE_OR_PROMISE"
UNVERIFIABLE_SUPERLATIVE = "UNVERIFIABLE_SUPERLATIVE"
AMBIGUOUS = "AMBIGUOUS"

POLICY_CLASSES = (FACTUAL_AND_EVIDENCE_VERIFIABLE, ABSOLUTE_OR_PERMANENCE, GUARANTEE_OR_PROMISE,
                  UNVERIFIABLE_SUPERLATIVE, AMBIGUOUS)

# ---------------------------------------------------------------- clearance rules
REQUIRE_ALL_CONCEPTS = "REQUIRE_ALL_CONCEPTS"
ALWAYS_BLOCK = "ALWAYS_BLOCK"
BLOCK_PENDING_POLICY = "BLOCK_PENDING_POLICY"

CLEARANCE_BY_CLASS = {
    FACTUAL_AND_EVIDENCE_VERIFIABLE: REQUIRE_ALL_CONCEPTS,
    ABSOLUTE_OR_PERMANENCE: ALWAYS_BLOCK,
    GUARANTEE_OR_PROMISE: ALWAYS_BLOCK,
    UNVERIFIABLE_SUPERLATIVE: ALWAYS_BLOCK,
    AMBIGUOUS: BLOCK_PENDING_POLICY,
}

# ---------------------------------------------------------------- reason codes
ABSOLUTE_CLAIM_PROHIBITED = "ABSOLUTE_CLAIM_PROHIBITED"
GUARANTEE_CLAIM_PROHIBITED = "GUARANTEE_CLAIM_PROHIBITED"
UNVERIFIABLE_CLAIM_PROHIBITED = "UNVERIFIABLE_CLAIM_PROHIBITED"
POLICY_DECISION_REQUIRED = "POLICY_DECISION_REQUIRED"
MATCHING_CONCEPT_UNVERIFIED = "MATCHING_CONCEPT_UNVERIFIED"
UNSAFE_OWNER_FACT_VALUE = "UNSAFE_OWNER_FACT_VALUE"
UNKNOWN_UNSAFE_PHRASE = "UNKNOWN_UNSAFE_PHRASE"

REASON_BY_CLASS = {
    ABSOLUTE_OR_PERMANENCE: ABSOLUTE_CLAIM_PROHIBITED,
    GUARANTEE_OR_PROMISE: GUARANTEE_CLAIM_PROHIBITED,
    UNVERIFIABLE_SUPERLATIVE: UNVERIFIABLE_CLAIM_PROHIBITED,
    AMBIGUOUS: POLICY_DECISION_REQUIRED,
}

NEXT_ACTION_BY_REASON = {
    ABSOLUTE_CLAIM_PROHIBITED: "Remove the absolute/permanence wording from the copy. No product fact "
                               "and no single photograph can establish permanence.",
    GUARANTEE_CLAIM_PROHIBITED: "Remove the guarantee/promise wording. It needs a distinct guarantee "
                                "authority and an owner-approved policy, neither of which is modelled.",
    UNVERIFIABLE_CLAIM_PROHIBITED: "Remove the subjective wording. It cannot be verified by evidence.",
    POLICY_DECISION_REQUIRED: "Owner policy decision required: this phrase asserts a specific VALUE, "
                              "which verifying the concept alone cannot establish.",
    MATCHING_CONCEPT_UNVERIFIED: "Verify the exact mapped concept through structured evidence, or "
                                 "remove the phrase from the copy.",
    UNSAFE_OWNER_FACT_VALUE: "Rewrite the owner fact value without the prohibited phrase. The raw "
                             "value is preserved for audit and the fact is not publishable.",
    UNKNOWN_UNSAFE_PHRASE: "Add a canonical policy record for this phrase. Unmapped phrases fail closed.",
}

# ---------------------------------------------------------------- surfaces
SURFACE_CLAIMS = "claims"                  # title / description / bullets / publishable payload text
SURFACE_ITEM_HIGHLIGHTS = "item_highlights"
SURFACE_APLUS = "aplus"
ALL_SURFACES = (SURFACE_CLAIMS, SURFACE_ITEM_HIGHLIGHTS, SURFACE_APLUS)

# Accepted spellings for a US fulfillment origin. Compared against the NORMALISED structured
# fact value, so "U.S.A." and "united states" land here too.
_US = ("us", "usa", "u s a", "united states", "united states of america")

_F, _A, _G, _U, _M = (FACTUAL_AND_EVIDENCE_VERIFIABLE, ABSOLUTE_OR_PERMANENCE, GUARANTEE_OR_PROMISE,
                      UNVERIFIABLE_SUPERLATIVE, AMBIGUOUS)

# ---------------------------------------------------------------- the canonical table
# (phrase, policy_class, required_concept_ids, allowed_surfaces)
# required_concept_ids on a blocked class records which concept WOULD be relevant. It is documentation
# only: the clearance rule blocks regardless, which is the whole point of the hard-block classes.
_SHARED_RECORDS = (
    # -- comfort / softness: subjective, never inferable from material or measurements -------------
    ("soft and comfortable", _U, ("softness_comfort",), ()),
    ("comfortable fit", _U, ("softness_comfort", "fit"), ()),
    ("soft against the skin", _U, ("softness_comfort",), ()),
    ("buttery soft", _U, ("softness_comfort",), ()),
    ("cozy and comfortable", _U, ("softness_comfort",), ()),
    ("all day comfort", _U, ("softness_comfort",), ()),
    ("perfect for long shifts", _U, ("softness_comfort",), ()),
    ("for long shifts", _U, ("softness_comfort",), ()),
    ("12 hour shifts", _U, ("softness_comfort",), ()),
    ("12-hour shifts", _U, ("softness_comfort",), ()),
    ("any occasion", _U, ("occasion",), ()),
    ("premium material", _U, ("material_composition",), ()),
    ("premium fabric", _U, ("material_composition",), ()),
    # -- durability / permanence: no fact and no photograph establishes permanence -----------------
    ("made to last", _A, ("durability",), ()),
    ("built to last", _A, ("durability",), ()),
    ("will not fade", _A, ("durability",), ()),
    ("wont fade", _A, ("durability",), ()),
    ("never fades", _A, ("durability",), ()),
    ("lasts forever", _A, ("durability",), ()),
    # -- guarantees / unbounded promises -----------------------------------------------------------
    ("exactly as you enter", _G, ("exact_personalization_promise",), ()),
    ("exactly as entered", _G, ("exact_personalization_promise",), ()),
    ("exactly what you enter", _G, ("exact_personalization_promise",), ()),
    ("embroidered exactly", _G, ("exact_personalization_promise",), ()),
    ("any name and credentials", _G, ("personalization_fields",), ()),
    ("made to order", _G, ("made_to_order",), ()),
    # -- tracking: DEVIATION from the classification this branch was commissioned with -------------
    # The commissioning document carried GUARANTEE_OR_PROMISE = 9, taken from an earlier self-audit
    # that put the three tracking phrases in that class. Implementing it that way is wrong, and the
    # existing suite proved it: the shipping fixture's own value is "Ships from the US with tracking",
    # so the ingestion guard voided the whole shipping_method claim and four accepted tests failed.
    # Whether an order carries tracking is an operational fact the owner controls and can verify -- it
    # is not a warranty, not regulated, and not unprovable, unlike every other member of that class.
    # Classed FACTUAL against the `tracking` concept, which the claim engine already derives.
    ("tracking included", _F, ("tracking",), ALL_SURFACES),
    ("with tracking", _F, ("tracking",), ALL_SURFACES),
    ("includes tracking", _F, ("tracking",), ALL_SURFACES),
    # -- value-encoding: verifying the concept cannot establish the specific value ------------------
    # FULFILLMENT ORIGIN clears only on a dedicated ship_from_country concept whose VALUE is the US.
    # An earlier revision bound these to the generic shipping_method concept and authorised on
    # verified-ness alone, so any VERIFIED shipping method cleared them; claim_evidence._has_us_shipping
    # only substring-scans the owner's free prose, which is not evidence. MANUFACTURING origin is a
    # different claim with different legal weight -- "Made in USA" is FTC-regulated -- so it is not
    # reachable from this concept and stays blocked pending its own compliance model.
    ("shipped from the us", _F, ("ship_from_country",), ALL_SURFACES, {"ship_from_country": _US}),
    ("ships from the us", _F, ("ship_from_country",), ALL_SURFACES, {"ship_from_country": _US}),
    ("made in the usa", _M, ("production_location",), ()),
    ("made in the us", _M, ("production_location",), ()),
    ("printed in the usa", _M, ("production_location", "decoration_method"), ()),
    ("10 14 business days", _M, ("production_time",), ()),
    ("mock neck option", _M, ("fit",), ()),
    # -- factual: the phrase asserts the CONCEPT, and the concept is evidence-verifiable ------------
    ("machine embroidery", _F, ("decoration_method",), ALL_SURFACES),
    ("real machine embroidery", _F, ("real_machine_embroidery",), ALL_SURFACES),
    ("satin stitch", _F, ("satin_stitch",), ALL_SURFACES),
    ("raised satin", _F, ("satin_stitch",), ALL_SURFACES),
    ("raised stitching", _F, ("satin_stitch",), ALL_SURFACES),
    ("embroidered name", _F, ("decoration_method", "personalization_fields"), ALL_SURFACES),
    ("cotton blend", _F, ("material_composition",), ALL_SURFACES),
    # A measurement METHODOLOGY statement belongs in long-form copy where it can be qualified.
    # An item highlight is a stripped one-line assertion with no room for that context, so this
    # phrase is deliberately not allowed there. This is the surface allowlist doing real work
    # rather than every record trivially listing all three.
    ("measured from the real garment", _F, ("measurements",),
     (SURFACE_CLAIMS, SURFACE_APLUS)),
    ("measurements are taken from the real garment", _F, ("measurements",), ALL_SURFACES),
    ("measured from real garments", _F, ("measurements",), ALL_SURFACES),
)

# Acrylic extends the shared vocabulary; it never replaces it. Nearly every entry is a safety,
# regulatory or permanence claim, which is exactly the class an ordinary product fact must not clear.
_ACRYLIC_RECORDS = (
    ("shatterproof", _A, (), ()), ("shatter proof", _A, (), ()), ("shatter resistant", _A, (), ()),
    ("unbreakable", _A, (), ()), ("break proof", _A, (), ()), ("break resistant", _A, (), ()),
    ("impact resistant", _A, (), ()), ("virtually indestructible", _A, (), ()),
    ("indestructible", _A, (), ()), ("will not crack", _A, (), ()), ("wont crack", _A, (), ()),
    ("never cracks", _A, (), ()), ("crack proof", _A, (), ()),
    ("will not yellow", _A, (), ()), ("wont yellow", _A, (), ()), ("never yellows", _A, (), ()),
    ("yellow resistant", _A, (), ()), ("non yellowing", _A, (), ()), ("uv proof", _A, (), ()),
    ("uv resistant", _A, (), ()), ("fade proof", _A, (), ()), ("will not warp", _A, (), ()),
    ("wont warp", _A, (), ()),
    ("scratch proof", _A, (), ()), ("scratch resistant", _A, (), ()), ("scratch free", _A, (), ()),
    ("dishwasher safe", _G, (), ()), ("microwave safe", _G, (), ()), ("oven safe", _G, (), ()),
    ("heat resistant", _G, (), ()), ("heat proof", _G, (), ()), ("top rack safe", _G, (), ()),
    ("food safe", _G, (), ()), ("food grade", _G, (), ()), ("bpa free", _G, (), ()),
    ("non toxic", _G, (), ()), ("child safe", _G, (), ()), ("baby safe", _G, (), ()),
    ("toy safe", _G, (), ()), ("medical grade", _G, (), ()),
    ("crystal clear", _U, (), ()), ("optically clear", _U, (), ()),
    ("glass like clarity", _U, (), ()), ("clearer than glass", _U, (), ()),
    ("real glass", _M, (), ()), ("genuine glass", _M, (), ()), ("tempered", _M, (), ()),
)

_CATEGORY_RECORDS = {"acrylic": _ACRYLIC_RECORDS}


def _record(row):
    phrase, cls, concepts, surfaces = row[:4]
    values = row[4] if len(row) > 4 else {}
    return {"phrase": phrase, "policy_class": cls, "required_concept_ids": tuple(concepts),
            "allowed_surfaces": tuple(surfaces), "clearance_rule": CLEARANCE_BY_CLASS[cls],
            "required_concept_values": {k: tuple(v) for k, v in (values or {}).items()}}


POLICY = {r["phrase"]: r for r in (_record(x) for x in _SHARED_RECORDS)}
POLICY_BY_CATEGORY = {
    cat: {r["phrase"]: r for r in (_record(x) for x in rows)}
    for cat, rows in _CATEGORY_RECORDS.items()
}

# The phrase vocabularies. page_auditor re-exports these so there is exactly one list in the tree.
UNSAFE_CLAIM_PHRASES = tuple(r[0] for r in _SHARED_RECORDS)
UNSAFE_CLAIM_PHRASES_BY_CATEGORY = {c: tuple(r[0] for r in rows) for c, rows in _CATEGORY_RECORDS.items()}


def phrases_for(category=None):
    """Shared phrases plus anything specific to this category. Additive on purpose: an acrylic
    listing must still not claim "made to last"; it must ALSO not claim "shatterproof"."""
    extra = UNSAFE_CLAIM_PHRASES_BY_CATEGORY.get((category or "").strip().lower(), ())
    return UNSAFE_CLAIM_PHRASES + tuple(extra)


def policy_for(phrase, category=None):
    """The canonical record for a phrase, or None when the phrase has no record (fails closed)."""
    cat = (category or "").strip().lower()
    if cat and phrase in POLICY_BY_CATEGORY.get(cat, {}):
        return POLICY_BY_CATEGORY[cat][phrase]
    if phrase in POLICY:
        return POLICY[phrase]
    for table in POLICY_BY_CATEGORY.values():          # a category phrase seen without its category
        if phrase in table:
            return table[phrase]
    return None


# ---------------------------------------------------------------- text helpers (own normaliser so the
# authority stands alone and detection cannot drift from authorisation)
def normalize_text(text):
    s = str(text or "").lower().replace("-", " ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


def contains_phrase(tokens, phrase_tokens):
    """Whole-token contiguous match, so 'us' never matches inside 'usa'."""
    if not phrase_tokens or len(phrase_tokens) > len(tokens):
        return False
    n = len(phrase_tokens)
    return any(tokens[i:i + n] == phrase_tokens for i in range(len(tokens) - n + 1))


def verified_concepts(claim_evidence):
    """The set of VERIFIED, publishable concept ids.

    Accepts either a ClaimEvidence object or a listing's embedded ``claim_evidence`` block. The
    embedded block is read by its declared ``concept`` field ONLY -- never by its text. That is the
    whole point: authorisation comes from a structured concept a claim engine derived from verified
    facts, not from words an owner typed.
    """
    if claim_evidence is None:
        return frozenset()
    pub = getattr(claim_evidence, "publishable", None)
    if pub is not None:
        out = set()
        for c in pub:
            if c.get("verification_state") == "VERIFIED":
                cid = c.get("normalized_concept") or c.get("concept")
                if cid:
                    out.add(cid)
        return frozenset(out)
    if isinstance(claim_evidence, dict):                 # embedded listing block
        return frozenset(
            e["concept"] for e in (claim_evidence.get("verified") or [])
            if isinstance(e, dict) and e.get("concept"))
    return frozenset()


def _concept_values(claims, concept):
    """The normalised structured VALUES backing a verified concept, read from the claim's own
    source_evidence. Never parsed out of the claim's prose."""
    if claims is None:
        return frozenset()
    try:
        rec = claims.claim(concept)
    except Exception:
        return frozenset()
    if not rec:
        return frozenset()
    out = set()
    for entry in (rec.get("source_evidence") or {}).values():
        if isinstance(entry, dict) and entry.get("state") == "VERIFIED":
            val = entry.get("value")
            for item in (val if isinstance(val, list) else [val]):
                norm = normalize_text(item)
                if norm:
                    out.add(norm)
    return frozenset(out)


def _concept_verified(claims, concept):
    """VERIFIED through STRUCTURED evidence. Never raw text, never substring presence."""
    if claims is None:
        return False
    try:
        rec = claims.claim(concept)
    except AttributeError:
        return concept in verified_concepts(claims)
    except Exception:
        return False
    if not rec:
        return False
    return bool(rec.get("publishable")) and rec.get("verification_state") == "VERIFIED"


def evaluate(phrase, claims, surface, *, category=None, owner_fact_field=None):
    """Authorise ONE unsafe phrase hit on ONE surface. Returns a structured verdict.

    Never consults concatenated claim text, so neither an unrelated claim nor a phrase straddling two
    adjacent claim texts can authorise anything.
    """
    rec = policy_for(phrase, category)
    if rec is None:
        return _blocked(phrase, None, (), surface, UNKNOWN_UNSAFE_PHRASE, owner_fact_field,
                        evidence_state="NO_POLICY_RECORD")

    cls, rule, concepts = rec["policy_class"], rec["clearance_rule"], rec["required_concept_ids"]

    if rule in (ALWAYS_BLOCK, BLOCK_PENDING_POLICY):
        return _blocked(phrase, cls, concepts, surface, REASON_BY_CLASS[cls], owner_fact_field,
                        evidence_state="NOT_CONSULTED_CLASS_IS_NEVER_CLEARABLE")

    if surface not in rec["allowed_surfaces"]:          # surface policy is independent of evidence
        return _blocked(phrase, cls, concepts, surface, POLICY_DECISION_REQUIRED, owner_fact_field,
                        evidence_state="SURFACE_NOT_PERMITTED")

    unverified = [c for c in concepts if not _concept_verified(claims, c)]
    if unverified or not concepts:
        return _blocked(phrase, cls, concepts, surface, MATCHING_CONCEPT_UNVERIFIED, owner_fact_field,
                        evidence_state="UNVERIFIED_CONCEPTS:" + ",".join(unverified or ("<none mapped>",)))

    # VALUE binding. Verifying a concept says the owner stated something and stands behind it; it does
    # NOT say what they stated. A phrase that asserts a specific value of a concept must match that
    # value, or a VERIFIED ship_from_country of "Vietnam" would clear "ships from the us".
    for concept, accepted in (rec["required_concept_values"] or {}).items():
        actual = _concept_values(claims, concept)
        if not actual:
            return _blocked(phrase, cls, concepts, surface, MATCHING_CONCEPT_UNVERIFIED,
                            owner_fact_field,
                            evidence_state=f"CONCEPT_VALUE_UNREADABLE:{concept}")
        if not (actual & {normalize_text(a) for a in accepted}):
            return _blocked(phrase, cls, concepts, surface, MATCHING_CONCEPT_UNVERIFIED,
                            owner_fact_field,
                            evidence_state=f"CONCEPT_VALUE_MISMATCH:{concept}="
                                           + "|".join(sorted(actual)))

    return {"authorized": True, "phrase": phrase, "policy_class": cls,
            "required_concept_ids": list(concepts), "surface": surface,
            "evidence_state": "ALL_REQUIRED_CONCEPTS_VERIFIED", "block": None}


def _blocked(phrase, cls, concepts, surface, reason, owner_fact_field, evidence_state):
    return {
        "authorized": False, "phrase": phrase, "policy_class": cls,
        "required_concept_ids": list(concepts), "surface": surface,
        "evidence_state": evidence_state,
        "block": {
            "status": "BLOCKED", "surface": surface, "phrase": phrase, "policy_class": cls,
            "required_concept_ids": list(concepts), "evidence_state": evidence_state,
            "owner_fact_field": owner_fact_field, "reason_code": reason,
            "next_action": NEXT_ACTION_BY_REASON[reason],
        },
    }


def findings_for_text(text, claims, surface, *, category=None):
    """Every unsafe phrase present in *text* that this surface may not carry. One independent
    evaluation per hit — no concatenation, no bag of words."""
    tokens = normalize_text(text).split()
    out = []
    for phrase in phrases_for(category):
        if contains_phrase(tokens, normalize_text(phrase).split()):
            verdict = evaluate(phrase, claims, surface, category=category)
            if not verdict["authorized"]:
                out.append(verdict["block"])
    return out


# ---------------------------------------------------------------- ingestion guard
NEVER_CLEARABLE_RULES = (ALWAYS_BLOCK, BLOCK_PENDING_POLICY)


def screen_owner_value(field, value, *, category=None):
    """Screen a RAW owner fact value before it can become publishable VERIFIED evidence.

    Only never-clearable classes are screened. A factual phrase in a fact value is fine — it still has
    to clear its own concept at the audit surface. The raw value is never rewritten; the caller keeps
    it for audit and simply must not treat it as publishable.
    """
    tokens = normalize_text(value).split()
    if not tokens:
        return None
    for phrase in phrases_for(category):
        rec = policy_for(phrase, category)
        if not rec or rec["clearance_rule"] not in NEVER_CLEARABLE_RULES:
            continue
        if contains_phrase(tokens, normalize_text(phrase).split()):
            cls = rec["policy_class"]
            reason = POLICY_DECISION_REQUIRED if cls is AMBIGUOUS else UNSAFE_OWNER_FACT_VALUE
            return {
                "status": "BLOCKED", "surface": "owner_fact_ingestion", "phrase": phrase,
                "policy_class": cls, "required_concept_ids": list(rec["required_concept_ids"]),
                "evidence_state": "OWNER_ENTERED_TEXT_IS_NOT_EVIDENCE",
                "owner_fact_field": field, "reason_code": reason,
                "next_action": NEXT_ACTION_BY_REASON[reason],
            }
    return None


def manifest():
    """The whole policy table as data — the single source for the proof packet."""
    rows = []
    for scope, table in [("shared", POLICY)] + [(c, t) for c, t in POLICY_BY_CATEGORY.items()]:
        for phrase, r in table.items():
            rows.append({"scope": scope, "phrase": phrase, "policy_class": r["policy_class"],
                         "required_concept_ids": list(r["required_concept_ids"]),
                         "allowed_surfaces": list(r["allowed_surfaces"]),
                         "clearance_rule": r["clearance_rule"]})
    return {"schema_version": SCHEMA_VERSION, "phrase_count": len(rows), "rows": rows}
