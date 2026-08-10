#!/usr/bin/env python3
"""
listing.claim_evidence — turn normalized product facts into evidence-classed listing claims (ACT-007/008).

Before this layer the generator wrote fixed customer promises ("embroidered exactly as you enter it",
"raised satin stitching", "shipped from the US with tracking", "comfortable fit", "made to last") that
were not tied to any verified fact. This module converts the ONE normalized product-fact source into a
set of claim records, each carrying its own verification state and evidence lineage, so downstream
copy engines can only publish what is actually supported.

It consumes NormalizedProductFacts from product_fact_loader WITHOUT requiring another fact-schema
rewrite — a fact field maps to a claim concept, and the fact's state decides the claim's state.

Claim states (unattended-generation policy):
  VERIFIED               -> may publish
  SUPPORTED_OWNER_REVIEW -> must NOT publish automatically (owner must confirm first)
  UNVERIFIED_BLOCKED     -> must NOT publish (no supporting evidence)
  PROHIBITED             -> must NEVER publish (the backing fact is BLOCKED/prohibited)

The no-inference rules are enforced STRUCTURALLY, by choosing each concept's evidence field:
  * softness/comfort is NOT read from a material name           -> no material evidence field
  * comfort is NOT read from measurements                        -> no measurement evidence field
  * durability is NOT read from embroidery                       -> no decoration evidence field
  * US shipping is NOT read from a local supplier                -> only an explicit US shipping fact
  * tracking is NOT read from the existence of shipping          -> a dedicated tracking fact
  * exact personalization is NOT read from a name field          -> a dedicated exact-personalization fact
  * made-to-order is NOT read from personalization               -> a dedicated made-to-order fact
Concepts with no verifiable field in the current fact schema therefore stay UNVERIFIED_BLOCKED until an
owner supplies explicit evidence; they never borrow a neighbouring fact.

Public API:
  build_claim_evidence(facts, keyword_context=None, prohibited_concepts=()) -> ClaimEvidence
  write_claim_evidence(folder, evidence=None, facts=None, ...)              -> (path, ClaimEvidence)
  ClaimEvidence.claim(concept) / .publishable / .by_state(...) / .to_dict()
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import unsafe_claim_policy as UCP
import product_fact_loader as PFL

# 1.1.0 (Session 6C.1): every claim now carries ATOMIC semantic components + an effective evidence
# state. A claim record may not receive a stronger effective state than its least-supported material
# component, so a compound text can never inherit one component's verification (see ATOMIC-CLAIM RULE).
CLAIM_EVIDENCE_SCHEMA_VERSION = "1.1.0"
CLAIM_EVIDENCE_FILENAME = "CLAIM-EVIDENCE.json"

# claim states
VERIFIED = "VERIFIED"
SUPPORTED_OWNER_REVIEW = "SUPPORTED_OWNER_REVIEW"
UNVERIFIED_BLOCKED = "UNVERIFIED_BLOCKED"
PROHIBITED = "PROHIBITED"
# a compound claim whose components disagree on evidence — blocked, and NEVER treated as VERIFIED.
MIXED_EVIDENCE_BLOCKED = "MIXED_EVIDENCE_BLOCKED"

# only VERIFIED claims may enter publishable copy in unattended generation.
PUBLISHABLE_STATES = (VERIFIED,)

# every state a stored claim record may legally carry (verification_state stays one of the classic four;
# effective_evidence_state may additionally be MIXED_EVIDENCE_BLOCKED).
CLAIM_STATES = (VERIFIED, SUPPORTED_OWNER_REVIEW, UNVERIFIED_BLOCKED, PROHIBITED)
EFFECTIVE_STATES = CLAIM_STATES + (MIXED_EVIDENCE_BLOCKED,)
# worst-wins ordering when a claim carries several material components (lower = weaker/less supported).
_STATE_RANK = {PROHIBITED: 0, MIXED_EVIDENCE_BLOCKED: 1, UNVERIFIED_BLOCKED: 2,
               SUPPORTED_OWNER_REVIEW: 3, VERIFIED: 4}

# ---------------------------------------------------------------- semantic component vocabulary
# The material semantic components a claim's canonical text may assert. A claim is ATOMIC when its text
# asserts exactly one gated component (or every asserted component is independently VERIFIED). Reuse an
# existing type; do NOT invent free-form component names.
COMP_PRODUCT_IDENTITY = "PRODUCT_IDENTITY"
COMP_RECIPIENT = "RECIPIENT"
COMP_AUDIENCE = "AUDIENCE"
COMP_GIFT_OR_OCCASION = "GIFT_OR_OCCASION"
COMP_PERSONALIZATION = "PERSONALIZATION"
COMP_DECORATION_METHOD = "DECORATION_METHOD"
COMP_MATERIAL = "MATERIAL"
COMP_COLOR = "COLOR"
COMP_SIZE = "SIZE"
COMP_FIT = "FIT"
COMP_CARE = "CARE"
COMP_PACKAGING = "PACKAGING"
COMP_PRODUCTION_TIME = "PRODUCTION_TIME"
COMP_SHIPPING_TIME = "SHIPPING_TIME"
COMP_PHYSICAL_QUALITY = "PHYSICAL_QUALITY"
SEMANTIC_COMPONENTS = (
    COMP_PRODUCT_IDENTITY, COMP_RECIPIENT, COMP_AUDIENCE, COMP_GIFT_OR_OCCASION, COMP_PERSONALIZATION,
    COMP_DECORATION_METHOD, COMP_MATERIAL, COMP_COLOR, COMP_SIZE, COMP_FIT, COMP_CARE, COMP_PACKAGING,
    COMP_PRODUCTION_TIME, COMP_SHIPPING_TIME, COMP_PHYSICAL_QUALITY,
)

# A narrow, secondary guard (NOT the primary authority — the primary authority is a claim's declared
# components + fact/evidence lineage): if a claim's canonical/display text contains one of these
# qualifier words, the mapped component MUST be one of the claim's own VERIFIED components, otherwise the
# text is smuggling an unsupported concept and the claim is blocked. Word-boundary matched, lowercase.
_QUALIFIER_COMPONENTS = {
    COMP_PERSONALIZATION: ("personalized", "personalised", "personalize", "personalization",
                           "custom", "customized", "customised", "customizable", "monogram",
                           "monogrammed", "monogramming", "engraved", "initials"),
    COMP_GIFT_OR_OCCASION: ("gift", "gifts"),
    COMP_DECORATION_METHOD: ("embroidered", "embroidery", "stitched", "satin", "tatami", "printed",
                             "screenprint", "applique"),
    COMP_MATERIAL: ("cotton", "polyester", "fleece", "wool", "cashmere", "linen", "spandex", "nylon",
                    "rayon", "flannel"),
}
# typed reason codes returned by the atomicity validator (documented vocabulary).
R_NOT_ATOMIC = "CLAIM_NOT_ATOMIC"
R_UNSUPPORTED_COMPONENT = "UNSUPPORTED_CLAIM_COMPONENT"
R_MIXED_COMPONENTS = "MIXED_EVIDENCE_COMPONENTS"
R_PERSONALIZATION_MISSING = "PERSONALIZATION_EVIDENCE_MISSING"
R_GIFT_MISSING = "GIFT_EVIDENCE_MISSING"
R_DECORATION_MISSING = "DECORATION_EVIDENCE_MISSING"
R_MATERIAL_MISSING = "MATERIAL_EVIDENCE_MISSING"
_COMPONENT_MISSING_REASON = {
    COMP_PERSONALIZATION: R_PERSONALIZATION_MISSING, COMP_GIFT_OR_OCCASION: R_GIFT_MISSING,
    COMP_DECORATION_METHOD: R_DECORATION_MISSING, COMP_MATERIAL: R_MATERIAL_MISSING,
}


def _joined(value):
    return ", ".join(value) if isinstance(value, list) else value


def _has_us_shipping(value):
    v = str(value or "").lower()
    return any(tok in v for tok in ("united states", "u.s", "u.s.", " us ", " us.", "us ",
                                    "domestic", "usa")) or v.strip() == "us"


def _shipping_text(value):
    """A US shipping statement bound to the verified value: tracking only if the value states it."""
    base = "Shipped from the US"
    return base + (" with tracking." if "track" in str(value or "").lower() else ".")


# ---------------------------------------------------------------- claim concept specs
class ClaimSpec:
    """One claim concept: which fact field(s) back it, the text it proposes, and any extra guard.

    evidence_fields is ordered; the FIRST present publishable field is the evidence. An empty
    evidence_fields means the current fact schema carries no verifiable source for this concept, so it
    is UNVERIFIED_BLOCKED until an owner supplies explicit evidence (this is how the no-inference rules
    are encoded — the concept is never allowed to read a neighbouring fact).
    """

    def __init__(self, concept, claim_type, evidence_fields, text, guard=None, keyword_source=None,
                 components=(), free_text=False):
        self.concept = concept
        self.claim_type = claim_type
        self.evidence_fields = tuple(evidence_fields)
        self._text = text                 # callable(value) -> str
        self._guard = guard               # optional callable(value) -> bool (value must qualify)
        self.keyword_source = keyword_source  # optional keyword_context key that can also verify it
        # the material semantic component(s) this claim's canonical text asserts. A single component ==
        # an atomic claim. Its evidence is the claim's own evidence — never borrowed from a neighbour.
        self.components = tuple(components)
        # free_text: the canonical text IS the owner-verified value (a differentiator the owner attested
        # verbatim), so every component the text asserts is itself owner-backed — the secondary qualifier
        # scan does not treat those words as smuggled. Template claims (recipient/material/…) are False.
        self.free_text = free_text

    def text_for(self, value):
        return self._text(value)

    def qualifies(self, value):
        return True if self._guard is None else bool(self._guard(value))


def _embroider_machine(v):
    s = str(v or "").lower()
    return "embroider" in s and ("machine" in s or "stitch" in s) and "hand" not in s


# negation markers, matched on whitespace-delimited tokens so "no" never matches inside "nothing".
_DENIAL_TOKENS = frozenset((
    "no", "not", "none", "never", "without", "false", "excluded", "unavailable", "n/a", "na",
    "dont", "doesnt", "cannot", "cant", "lacks", "lacking", "off",
))
_AFFIRM_TOKENS = frozenset(("yes", "true", "included", "includes", "include", "provided", "always"))


def _tracking_included(v):
    """Tracking is INCLUDED only when the owner's value actually affirms it.

    The tracking claim's text is a FIXED assertion -- "Order tracking included." -- that ignores the
    value entirely. Without a guard, a fact reading "we do not offer tracking", marked VERIFIED,
    publishes the exact opposite of itself into bullets, description and A+ copy.

    real_machine_embroidery and satin_stitch already carry guards for precisely this reason: a spec
    whose text asserts a fixed sentence MUST qualify the value it is asserting about. tracking was
    the only fixed-assertion spec in CLAIM_SPECS without one.

    Fails CLOSED. A value this cannot read affirmatively does not verify, so an unusual phrasing
    costs the owner a claim rather than costing a customer a false promise.
    """
    tokens = set(str(v or "").lower().replace("-", " ").replace(",", " ").split())
    if not tokens:
        return False
    if tokens & _DENIAL_TOKENS:
        return False
    return bool("tracking" in tokens or "track" in tokens or (tokens & _AFFIRM_TOKENS))


CLAIM_SPECS = [
    ClaimSpec("decoration_method", "decoration", ("decoration_method",),
              lambda v: f"Decorated with {str(v).lower()}.", components=(COMP_DECORATION_METHOD,)),
    ClaimSpec("real_machine_embroidery", "decoration", ("decoration_method",),
              lambda v: "Real machine embroidery, not a printed graphic.", guard=_embroider_machine,
              components=(COMP_DECORATION_METHOD,)),
    ClaimSpec("satin_stitch", "decoration", ("decoration_method",),
              lambda v: "Raised satin-stitch embroidery.",
              guard=lambda v: "satin" in str(v).lower(), components=(COMP_DECORATION_METHOD,)),
    ClaimSpec("tatami_fill", "decoration", ("decoration_method",),
              lambda v: "Tatami-fill embroidery.", guard=lambda v: "tatami" in str(v).lower(),
              components=(COMP_DECORATION_METHOD,)),
    ClaimSpec("personalization_fields", "personalization", ("personalization_fields",),
              lambda v: f"Add {_joined(v)} during checkout.", components=(COMP_PERSONALIZATION,)),
    # exact personalization is a promise, not the presence of a field -> needs its own explicit fact.
    # That fact now exists in the vocabulary, so the concept reads it directly. It is still never
    # inferred from personalization_fields: an owner listing which fields they offer has not
    # promised what the finished stitching will say.
    ClaimSpec("exact_personalization_promise", "personalization",
              ("exact_personalization_promise",),
              lambda v: "Embroidered exactly as you enter it.",
              components=(COMP_PERSONALIZATION, COMP_DECORATION_METHOD)),
    ClaimSpec("material_composition", "material", ("material_composition", "material"),
              lambda v: f"Made from {_joined(v)}.", components=(COMP_MATERIAL,)),
    # softness/comfort is a sensory claim; it is never read off a material name or measurements.
    ClaimSpec("softness_comfort", "comfort", (),
              lambda v: "Soft and comfortable to wear.", components=(COMP_PHYSICAL_QUALITY,)),
    ClaimSpec("fit", "fit", ("fit",), lambda v: f"{str(v).capitalize()} fit.", components=(COMP_FIT,)),
    ClaimSpec("size_range", "size", ("size_range",),
              lambda v: f"Available in sizes {_joined(v)}.", components=(COMP_SIZE,)),
    ClaimSpec("color_options", "color", ("color_options",),
              lambda v: f"Available in {_joined(v)}.", components=(COMP_COLOR,)),
    ClaimSpec("measurements", "size", ("measurements",),
              lambda v: f"Measurements: {_joined(v)}.", components=(COMP_SIZE,)),
    ClaimSpec("care", "care", ("care_instructions",), lambda v: f"Care: {_joined(v)}.",
              components=(COMP_CARE,)),
    ClaimSpec("production_location", "production", ("production_location",),
              lambda v: f"Made in {v}.", components=(COMP_PRODUCTION_TIME,)),
    ClaimSpec("production_time", "production", ("production_time_range",),
              lambda v: f"Production time: {v}.", components=(COMP_PRODUCTION_TIME,)),
    ClaimSpec("handling_time", "fulfillment", ("handling_time",),
              lambda v: f"Handling time: {v}.", components=(COMP_SHIPPING_TIME,)),
    ClaimSpec("shipping_method", "fulfillment", ("shipping_method",),
              lambda v: _shipping_text(v) if _has_us_shipping(v) else f"Shipping: {v}.",
              components=(COMP_SHIPPING_TIME,)),
    # Fulfillment origin gets its own concept and is NEVER inferred from shipping prose. The
    # unsafe-claim policy binds US fulfillment-origin wording to this concept AND to its VALUE.
    ClaimSpec("ship_from_country", "fulfillment", ("ship_from_country",),
              lambda v: f"Ships from {v}.", components=(COMP_SHIPPING_TIME,)),
    # tracking is not implied by "there is shipping" -> a dedicated tracking fact.
    ClaimSpec("tracking", "fulfillment", ("tracking",), lambda v: "Order tracking included.",
              guard=_tracking_included, components=(COMP_SHIPPING_TIME,)),
    ClaimSpec("packaging", "fulfillment", ("packaging",), lambda v: f"Arrives in {v}.",
              components=(COMP_PACKAGING,)),
    # durability is never read off embroidery presence -> needs its own explicit fact.
    ClaimSpec("durability", "durability", (), lambda v: "Made to last.",
              components=(COMP_PHYSICAL_QUALITY,)),
    # made-to-order is never read off personalization -> needs its own explicit fact. That fact
    # now exists in the vocabulary, so the concept reads it directly and nothing else.
    ClaimSpec("made_to_order", "production", ("made_to_order",),
              lambda v: "Made to order after you purchase.",
              components=(COMP_PRODUCTION_TIME,)),
    # ATOMIC recipient (Session 6C.1): the canonical text asserts ONLY the RECIPIENT component. It must
    # never carry personalization / gift language — those are independent claims below with their own
    # evidence. "For nurses." is verified from the approved audience; personalization + gift are not.
    ClaimSpec("recipient", "audience", ("audience",),
              lambda v: f"For {v}.", keyword_source="audience", components=(COMP_RECIPIENT,)),
    ClaimSpec("occasion", "occasion", ("occasion",), lambda v: f"Great for {v}.",
              components=(COMP_GIFT_OR_OCCASION,)),
    # gift suitability is its OWN claim, never inferred from recipient identity -> needs explicit evidence.
    ClaimSpec("gift", "gift", (), lambda v: "Suitable as a gift.",
              components=(COMP_GIFT_OR_OCCASION,)),
    ClaimSpec("differentiator", "differentiator", ("verified_differentiator",), lambda v: str(v),
              components=(COMP_PHYSICAL_QUALITY,), free_text=True),
]
CLAIM_CONCEPTS = tuple(s.concept for s in CLAIM_SPECS)
_SPEC_BY_CONCEPT = {s.concept: s for s in CLAIM_SPECS}


# ---------------------------------------------------------------- claim record
def _claim_id(concept):
    """Deterministic, human-readable claim id (stable across identical runs)."""
    return "CLM-" + concept.upper()


def _evidence_value_tokens(source_evidence):
    """Lowercase word tokens of every backing fact/keyword VALUE. These are the claim's own verified
    evidence (e.g. a 'gift box' packaging value), so a qualifier word inside them is NOT smuggled — only
    the template's fixed framing words ('A personalized gift for …') can smuggle an unsupported concept."""
    import re
    out = set()
    for rec in (source_evidence or {}).values():
        val = rec.get("value") if isinstance(rec, dict) else None
        if val is None:
            continue
        text = " ".join(str(x) for x in val) if isinstance(val, (list, tuple)) else str(val)
        out.update(re.findall(r"[a-z]+", text.lower()))
    return out


def _text_qualifier_components(text, exclude=()):
    """The semantic components a canonical/display text ASSERTS via qualifier words (secondary guard).

    `exclude` are the claim's own verified value tokens, which are skipped so only the template's fixed
    framing words are scanned. e.g. 'A personalized gift for nurses' -> {PERSONALIZATION, GIFT_OR_OCCASION}.
    """
    import re
    toks = set(re.findall(r"[a-z]+", str(text or "").lower())) - set(exclude)
    found = set()
    for comp, words in _QUALIFIER_COMPONENTS.items():
        if toks & set(words):
            found.add(comp)
    return found


def resolve_atomic_components(declared_components, verification_state, proposed_text, free_text=False,
                              value_tokens=()):
    """Resolve a claim into atomic semantic components + an effective evidence state.

    A claim may NOT hold a stronger effective state than its least-supported material component. A
    canonical text that asserts a component the claim does not independently back (a compound / mixed
    claim) is blocked: MIXED_EVIDENCE_BLOCKED when the base claim was VERIFIED, else the worst state.

    free_text=True means the whole canonical text is the owner-verified value, so every component the
    text asserts is itself owner-backed (never smuggled) — used only for owner-attested differentiators.
    value_tokens are the claim's own verified value words, skipped by the qualifier scan.

    Returns (semantic_components, effective_state, reason_codes).
    """
    declared = set(declared_components)
    text_comps = _text_qualifier_components(proposed_text, exclude=value_tokens) if proposed_text else set()
    if free_text:
        declared = declared | text_comps          # owner attested the whole text -> all backed
        foreign = []
    else:
        foreign = sorted(text_comps - declared)
    declared = sorted(declared)

    components = [{"component": c, "evidence_state": verification_state,
                   "supported": verification_state == VERIFIED} for c in declared]
    components += [{"component": c, "evidence_state": UNVERIFIED_BLOCKED, "supported": False}
                  for c in foreign]

    reasons = []
    if not components:
        effective = verification_state
    elif foreign:
        reasons += [R_NOT_ATOMIC, R_UNSUPPORTED_COMPONENT]
        reasons += [_COMPONENT_MISSING_REASON[c] for c in foreign if c in _COMPONENT_MISSING_REASON]
        if verification_state == VERIFIED:
            reasons.append(R_MIXED_COMPONENTS)
            effective = MIXED_EVIDENCE_BLOCKED
        else:
            effective = min((c["evidence_state"] for c in components), key=lambda s: _STATE_RANK[s])
    else:
        # every declared component shares the claim's own evidence -> worst == the claim's state.
        effective = min((c["evidence_state"] for c in components), key=lambda s: _STATE_RANK[s])
    return components, effective, sorted(set(reasons))


def claim_content_sha256(record):
    """Deterministic per-claim content hash (excludes its own hash field) — lineage checkable."""
    body = {k: v for k, v in record.items() if k != "content_sha256"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def validate_claim_record(record):
    """Re-derive a stored claim record's atomicity and return typed violations (empty == safe).

    This is the guard that makes an OLD unsafe compound record (e.g. a VERIFIED
    "A personalized gift for nurses.") FAIL on read — its canonical text asserts PERSONALIZATION +
    GIFT_OR_OCCASION components the RECIPIENT claim never backed, so it can never be trusted as VERIFIED.
    """
    violations = []
    # authoritative declared set comes from the spec when the concept is known; else from the record.
    spec = _SPEC_BY_CONCEPT.get(record.get("normalized_concept"))
    declared = list(spec.components) if spec else [c["component"] for c in
                                                   record.get("semantic_components", [])]
    vstate = record.get("verification_state")
    _comps, effective, reasons = resolve_atomic_components(
        declared, vstate, record.get("proposed_text"), free_text=bool(spec and spec.free_text),
        value_tokens=_evidence_value_tokens(record.get("source_evidence")))
    if record.get("effective_evidence_state", effective) != effective:
        violations.append("effective_evidence_state does not recompute")
    if effective != VERIFIED and record.get("publishable"):
        violations.append(f"{record.get('claim_id')} is publishable but effective state is {effective}")
    if vstate == VERIFIED and effective == MIXED_EVIDENCE_BLOCKED:
        violations.append(f"{record.get('claim_id')} is a MIXED compound VERIFIED claim: {reasons}")
    if "content_sha256" in record and record["content_sha256"] != claim_content_sha256(record):
        violations.append(f"{record.get('claim_id')} content_sha256 does not recompute")
    return violations


def _claim_declared_components(record):
    spec = _SPEC_BY_CONCEPT.get(record.get("normalized_concept"))
    return list(spec.components) if spec else [c["component"] for c in
                                               record.get("semantic_components", [])]


def _evaluate(spec, facts, keyword_context, prohibited):
    """Resolve one concept -> (state, value, source_fact_fields, source_evidence, reasons, warnings,
    owner_status, updated_at)."""
    reasons, warnings = [], []
    source_fields, source_evidence = [], {}
    owner_status = None
    updated_at = None

    if spec.concept in prohibited:
        reasons.append("PROHIBITED_CONCEPT")
        return PROHIBITED, None, source_fields, source_evidence, reasons, warnings, owner_status, updated_at

    # 1. try the product-fact evidence fields, in order. A verified qualifying field wins outright;
    #    the first owner-review qualifying field is remembered as a supported-but-not-publishable
    #    fallback in case no verified field turns up.
    owner_review_value = None
    for field in spec.evidence_fields:
        rec = facts.get(field)
        source_fields.append(field)
        source_evidence[field] = {"value": rec.get("value"), "state": rec.get("state")}
        state = rec.get("state")
        value = rec.get("value")
        if state == PFL.BLOCKED:
            reasons.append(f"{field}:BLOCKED")
            return PROHIBITED, None, source_fields, source_evidence, sorted(set(reasons)), warnings, \
                rec.get("owner_status"), rec.get("updated_at")
        if rec.get("publishable"):
            if not spec.qualifies(value):
                reasons.append(f"{field}:verified but does not qualify for {spec.concept}")
                continue
            return VERIFIED, value, source_fields, source_evidence, sorted(set(reasons)), warnings, \
                rec.get("owner_status"), rec.get("updated_at")
        if state == PFL.OWNER_REVIEW_REQUIRED and value is not None and spec.qualifies(value) \
                and owner_review_value is None:
            owner_review_value = value
            owner_status = rec.get("owner_status")
            updated_at = rec.get("updated_at")
            reasons.append(f"{field}:OWNER_REVIEW_REQUIRED")
        # UNKNOWN / not-qualifying -> keep scanning

    # 2. a keyword-context signal can verify recipient/audience-style concepts (approved keyword data).
    if spec.keyword_source and keyword_context:
        kv = keyword_context.get(spec.keyword_source)
        if kv:
            source_fields.append(f"keyword:{spec.keyword_source}")
            source_evidence[f"keyword:{spec.keyword_source}"] = {"value": kv, "state": "KEYWORD_APPROVED"}
            reasons.append(f"verified from approved keyword {spec.keyword_source}")
            return VERIFIED, kv, source_fields, source_evidence, sorted(set(reasons)), warnings, \
                "keyword_source", None

    # 3. an owner-review field is supported but must not publish automatically.
    if owner_review_value is not None:
        return SUPPORTED_OWNER_REVIEW, owner_review_value, source_fields, source_evidence, \
            sorted(set(reasons)), warnings, owner_status, updated_at

    # 4. nothing verifiable.
    if not spec.evidence_fields:
        reasons.append("no verifiable evidence source in product facts — requires explicit owner "
                       "verification (never inferred from a neighbouring fact)")
    else:
        reasons.append("no verified evidence in product facts")
    return UNVERIFIED_BLOCKED, None, source_fields, source_evidence, sorted(set(reasons)), warnings, \
        owner_status, updated_at


def build_claim_record(spec, facts, keyword_context, prohibited):
    state, value, source_fields, source_evidence, reasons, warnings, owner_status, updated_at = \
        _evaluate(spec, facts, keyword_context, prohibited)
    proposed = (spec.text_for(value) if value is not None else spec.text_for("")) \
        if state in (VERIFIED, SUPPORTED_OWNER_REVIEW) else None

    # ATOMIC CLAIM RULE: derive the semantic components + the effective evidence state. A claim is
    # publishable ONLY when its EFFECTIVE state is VERIFIED, so a compound/mixed text can never inherit
    # one component's verification. For the current spec set every claim is atomic (one gated component),
    # so effective == verification_state unless a foreign qualifier is smuggled into the text.
    components, effective, atom_reasons = resolve_atomic_components(
        spec.components, state, proposed, free_text=spec.free_text,
        value_tokens=_evidence_value_tokens(source_evidence))
    publishable = effective in PUBLISHABLE_STATES

    # INGESTION GUARD. Claim text is spec.text_for(value) -- the owner's RAW FACT VALUE inside a
    # template. Without this, an owner authorises any prohibited phrase simply by typing it into a
    # free-text fact and marking it VERIFIED, because the claim then carries that text as "evidence".
    # Owner-entered text is NOT evidence. The raw value is preserved untouched for audit; only its
    # publishability is refused, and a structured blocker records why.
    unsafe_block = None
    if publishable:
        for candidate in (proposed, value if isinstance(value, str) else None):
            unsafe_block = UCP.screen_owner_value(
                ", ".join(sorted(set(source_fields))) or spec.concept, candidate)
            if unsafe_block:
                break
        if unsafe_block:
            publishable = False
            reasons = list(reasons) + [f"unsafe_owner_fact_value:{unsafe_block['phrase']}"]

    record = {
        "claim_id": _claim_id(spec.concept),
        "claim_type": spec.claim_type,
        "normalized_concept": spec.concept,
        "canonical_claim": proposed,
        "proposed_text": proposed,
        "semantic_components": components,
        "source_fact_fields": sorted(set(source_fields)),
        "source_evidence": source_evidence,
        "verification_state": state,
        "effective_evidence_state": effective,
        "owner_status": owner_status,
        "publishable": publishable,
        "unsafe_claim_block": unsafe_block,
        "reasons": sorted(set(reasons)),
        "atomicity_reason_codes": atom_reasons,
        "warnings": sorted(set(warnings)),
        "updated_at": updated_at,
    }
    record["content_sha256"] = claim_content_sha256(record)
    return record


# ---------------------------------------------------------------- result
class ClaimEvidence:
    """The canonical, deterministic set of evidence-classed claims for one product."""

    def __init__(self, source_product_fact_file, source_product_fact_sha256, claims,
                 keyword_context=None, warnings=None):
        self.schema_version = CLAIM_EVIDENCE_SCHEMA_VERSION
        self.source_product_fact_file = source_product_fact_file
        self.source_product_fact_sha256 = source_product_fact_sha256
        self.claims = claims                      # {concept: record}
        self.keyword_context = dict(keyword_context or {})
        self.warnings = list(warnings or [])

    # -- views ------------------------------------------------------
    def claim(self, concept):
        return self.claims.get(concept)

    def state(self, concept):
        c = self.claims.get(concept)
        return c["verification_state"] if c else None

    def effective_state(self, concept):
        """The gated, atomicity-aware state that decides publishability (never weaker→stronger)."""
        c = self.claims.get(concept)
        return c["effective_evidence_state"] if c else None

    def is_publishable(self, concept):
        c = self.claims.get(concept)
        return bool(c and c["publishable"])

    def publishable_text(self, concept):
        """The proposed text ONLY if the claim may publish (VERIFIED); else None."""
        c = self.claims.get(concept)
        return c["proposed_text"] if (c and c["publishable"]) else None

    def by_state(self, *states):
        return [c for c in self.claims.values() if c["verification_state"] in states]

    @property
    def publishable(self):
        return [c for c in self.claims.values() if c["publishable"]]

    @property
    def verified_count(self):
        return len(self.by_state(VERIFIED))

    @property
    def owner_review_count(self):
        return len(self.by_state(SUPPORTED_OWNER_REVIEW))

    @property
    def blocked_count(self):
        return len(self.by_state(UNVERIFIED_BLOCKED))

    @property
    def prohibited_count(self):
        return len(self.by_state(PROHIBITED))

    @property
    def mixed_evidence_count(self):
        return sum(1 for c in self.claims.values()
                   if c["effective_evidence_state"] == MIXED_EVIDENCE_BLOCKED)

    @property
    def atomicity_ok(self):
        """True when no claim is a mixed/compound VERIFIED claim (the ATOMIC-CLAIM invariant holds)."""
        return not audit_claim_evidence(self)["violations"]

    @property
    def missing_evidence_requirements(self):
        """Concepts that could not be verified and the fact fields that would verify them."""
        out = []
        for c in self.claims.values():
            if c["effective_evidence_state"] in (UNVERIFIED_BLOCKED, SUPPORTED_OWNER_REVIEW,
                                                 MIXED_EVIDENCE_BLOCKED):
                spec = _SPEC_BY_CONCEPT[c["normalized_concept"]]
                out.append({"concept": c["normalized_concept"], "claim_id": c["claim_id"],
                            "state": c["verification_state"],
                            "required_fact_fields": list(spec.evidence_fields),
                            "keyword_source": spec.keyword_source})
        return sorted(out, key=lambda x: x["concept"])

    def claim_id_index(self):
        """concept -> claim_id and claim_id -> concept, for lineage checks by consumers."""
        return {c["claim_id"]: c["normalized_concept"] for c in self.claims.values()}

    # -- serialization ----------------------------------------------
    def canonical_content(self):
        return {
            "schema_version": self.schema_version,
            "source_product_fact_file": self.source_product_fact_file,
            "source_product_fact_sha256": self.source_product_fact_sha256,
            "keyword_context": self.keyword_context,
            "claims": self.claims,
            "verified_count": self.verified_count,
            "owner_review_count": self.owner_review_count,
            "blocked_count": self.blocked_count,
            "prohibited_count": self.prohibited_count,
            "mixed_evidence_count": self.mixed_evidence_count,
            "missing_evidence_requirements": self.missing_evidence_requirements,
            "warnings": list(self.warnings),
        }

    def content_sha256(self):
        return hashlib.sha256(canonical_json(self.canonical_content()).encode("utf-8")).hexdigest()

    def to_dict(self, generated_at=None):
        doc = self.canonical_content()
        doc["content_sha256"] = self.content_sha256()
        if generated_at:
            doc["generated_at"] = generated_at    # the only volatile field; excluded from the hash
        return doc


def canonical_json(obj):
    """Stable canonical serialization — sorted keys, fixed separators, no ASCII escaping."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2, separators=(",", ": "))


# ---------------------------------------------------------------- build
def build_claim_evidence(facts, keyword_context=None, prohibited_concepts=()):
    """Build the evidence-classed claim set from normalized product facts.

    `facts` is a product_fact_loader.NormalizedProductFacts. `keyword_context` optionally carries
    approved keyword signals (e.g. {"audience": "nurses"}) that can verify recipient-style concepts.
    `prohibited_concepts` forces named concepts to PROHIBITED (never publishes).
    """
    if not isinstance(facts, PFL.NormalizedProductFacts):
        raise TypeError("build_claim_evidence expects a product_fact_loader.NormalizedProductFacts")
    prohibited = set(prohibited_concepts)
    claims = {}
    for spec in CLAIM_SPECS:
        claims[spec.concept] = build_claim_record(spec, facts, keyword_context, prohibited)
    warnings = []
    if facts.source_sha256 is None:
        warnings.append("no product-facts.json — every factual claim is UNVERIFIED_BLOCKED until the "
                        "owner supplies verified facts")
    return ClaimEvidence(source_product_fact_file=facts.source_file,
                         source_product_fact_sha256=facts.source_sha256,
                         claims=claims, keyword_context=keyword_context, warnings=warnings)


def audit_claim_evidence(evidence):
    """Audit a built ClaimEvidence (or a loaded claim dict) for the ATOMIC-CLAIM invariant.

    Returns {"ok": bool, "violations": [...], "mixed_evidence_claims": [...]}. A violation means a claim
    holds a stronger effective/publishable state than its least-supported component allows — the exact
    defect Session 6C.1 repairs. Consumers/proof scripts call this to prove no unsafe compound survives.
    """
    claims = evidence.claims if isinstance(evidence, ClaimEvidence) else dict(evidence or {})
    violations, mixed = [], []
    for concept, rec in claims.items():
        for v in validate_claim_record(rec):
            violations.append(f"{concept}: {v}")
        if rec.get("effective_evidence_state") == MIXED_EVIDENCE_BLOCKED:
            mixed.append(rec.get("claim_id"))
    return {"ok": not violations, "violations": sorted(violations),
            "mixed_evidence_claims": sorted(mixed)}


def write_claim_evidence(folder, evidence=None, facts=None, keyword_context=None,
                         prohibited_concepts=(), generated_at=None, outdir=None):
    """Write the canonical CLAIM-EVIDENCE.json for a folder. -> (path, ClaimEvidence)."""
    if evidence is None:
        facts = facts or PFL.load_product_facts(folder=folder)
        evidence = build_claim_evidence(facts, keyword_context, prohibited_concepts)
    path = os.path.join(outdir or folder, CLAIM_EVIDENCE_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        f.write(canonical_json(evidence.to_dict(generated_at=generated_at)))
    return path, evidence


def main():
    ap = argparse.ArgumentParser(description="Build evidence-classed claims from product facts")
    ap.add_argument("folder")
    ap.add_argument("--write", action="store_true", help=f"write {CLAIM_EVIDENCE_FILENAME}")
    a = ap.parse_args()
    facts = PFL.load_product_facts(folder=a.folder)
    ev = build_claim_evidence(facts)
    print(f"product facts: {facts.source_file} sha256 {facts.source_sha256}")
    print(f"claims: verified {ev.verified_count} · owner-review {ev.owner_review_count} · "
          f"blocked {ev.blocked_count} · prohibited {ev.prohibited_count}")
    print(f"content sha256 {ev.content_sha256()}")
    for c in ev.publishable:
        print(f"  [{c['verification_state']}] {c['claim_id']}: {c['proposed_text']}")
    if a.write:
        path, _ = write_claim_evidence(a.folder, evidence=ev)
        print(f"wrote {path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
