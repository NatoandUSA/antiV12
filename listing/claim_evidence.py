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
import product_fact_loader as PFL

CLAIM_EVIDENCE_SCHEMA_VERSION = "1.0.0"
CLAIM_EVIDENCE_FILENAME = "CLAIM-EVIDENCE.json"

# claim states
VERIFIED = "VERIFIED"
SUPPORTED_OWNER_REVIEW = "SUPPORTED_OWNER_REVIEW"
UNVERIFIED_BLOCKED = "UNVERIFIED_BLOCKED"
PROHIBITED = "PROHIBITED"

# only VERIFIED claims may enter publishable copy in unattended generation.
PUBLISHABLE_STATES = (VERIFIED,)


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

    def __init__(self, concept, claim_type, evidence_fields, text, guard=None, keyword_source=None):
        self.concept = concept
        self.claim_type = claim_type
        self.evidence_fields = tuple(evidence_fields)
        self._text = text                 # callable(value) -> str
        self._guard = guard               # optional callable(value) -> bool (value must qualify)
        self.keyword_source = keyword_source  # optional keyword_context key that can also verify it

    def text_for(self, value):
        return self._text(value)

    def qualifies(self, value):
        return True if self._guard is None else bool(self._guard(value))


def _embroider_machine(v):
    s = str(v or "").lower()
    return "embroider" in s and ("machine" in s or "stitch" in s) and "hand" not in s


CLAIM_SPECS = [
    ClaimSpec("decoration_method", "decoration", ("decoration_method",),
              lambda v: f"Decorated with {str(v).lower()}."),
    ClaimSpec("real_machine_embroidery", "decoration", ("decoration_method",),
              lambda v: "Real machine embroidery, not a printed graphic.", guard=_embroider_machine),
    ClaimSpec("satin_stitch", "decoration", ("decoration_method",),
              lambda v: "Raised satin-stitch embroidery.",
              guard=lambda v: "satin" in str(v).lower()),
    ClaimSpec("tatami_fill", "decoration", ("decoration_method",),
              lambda v: "Tatami-fill embroidery.", guard=lambda v: "tatami" in str(v).lower()),
    ClaimSpec("personalization_fields", "personalization", ("personalization_fields",),
              lambda v: f"Add {_joined(v)} during checkout."),
    # exact personalization is a promise, not the presence of a field -> needs its own explicit fact.
    ClaimSpec("exact_personalization_promise", "personalization", (),
              lambda v: "Embroidered exactly as you enter it."),
    ClaimSpec("material_composition", "material", ("material_composition", "material"),
              lambda v: f"Made from {_joined(v)}."),
    # softness/comfort is a sensory claim; it is never read off a material name or measurements.
    ClaimSpec("softness_comfort", "comfort", (),
              lambda v: "Soft and comfortable to wear."),
    ClaimSpec("fit", "fit", ("fit",), lambda v: f"{str(v).capitalize()} fit."),
    ClaimSpec("size_range", "size", ("size_range",),
              lambda v: f"Available in sizes {_joined(v)}."),
    ClaimSpec("color_options", "color", ("color_options",),
              lambda v: f"Available in {_joined(v)}."),
    ClaimSpec("measurements", "size", ("measurements",),
              lambda v: f"Measurements: {_joined(v)}."),
    ClaimSpec("care", "care", ("care_instructions",), lambda v: f"Care: {_joined(v)}."),
    ClaimSpec("production_location", "production", ("production_location",),
              lambda v: f"Made in {v}."),
    ClaimSpec("production_time", "production", ("production_time_range",),
              lambda v: f"Production time: {v}."),
    ClaimSpec("handling_time", "fulfillment", ("handling_time",),
              lambda v: f"Handling time: {v}."),
    ClaimSpec("shipping_method", "fulfillment", ("shipping_method",),
              lambda v: _shipping_text(v) if _has_us_shipping(v) else f"Shipping: {v}."),
    # tracking is not implied by "there is shipping" -> a dedicated tracking fact.
    ClaimSpec("tracking", "fulfillment", ("tracking",), lambda v: "Order tracking included."),
    ClaimSpec("packaging", "fulfillment", ("packaging",), lambda v: f"Arrives in {v}."),
    # durability is never read off embroidery presence -> needs its own explicit fact.
    ClaimSpec("durability", "durability", (), lambda v: "Made to last."),
    # made-to-order is never read off personalization -> needs its own explicit fact.
    ClaimSpec("made_to_order", "production", (), lambda v: "Made to order after you purchase."),
    ClaimSpec("recipient", "audience", ("audience",),
              lambda v: f"A personalized gift for {v}.", keyword_source="audience"),
    ClaimSpec("occasion", "occasion", ("occasion",), lambda v: f"Great for {v}."),
    ClaimSpec("differentiator", "differentiator", ("verified_differentiator",), lambda v: str(v)),
]
CLAIM_CONCEPTS = tuple(s.concept for s in CLAIM_SPECS)
_SPEC_BY_CONCEPT = {s.concept: s for s in CLAIM_SPECS}


# ---------------------------------------------------------------- claim record
def _claim_id(concept):
    """Deterministic, human-readable claim id (stable across identical runs)."""
    return "CLM-" + concept.upper()


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
    proposed = spec.text_for(value) if value is not None else spec.text_for("")
    publishable = state in PUBLISHABLE_STATES
    return {
        "claim_id": _claim_id(spec.concept),
        "claim_type": spec.claim_type,
        "normalized_concept": spec.concept,
        "proposed_text": proposed if state in (VERIFIED, SUPPORTED_OWNER_REVIEW) else None,
        "source_fact_fields": sorted(set(source_fields)),
        "source_evidence": source_evidence,
        "verification_state": state,
        "owner_status": owner_status,
        "publishable": publishable,
        "reasons": sorted(set(reasons)),
        "warnings": sorted(set(warnings)),
        "updated_at": updated_at,
    }


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
    def missing_evidence_requirements(self):
        """Concepts that could not be verified and the fact fields that would verify them."""
        out = []
        for c in self.claims.values():
            if c["verification_state"] in (UNVERIFIED_BLOCKED, SUPPORTED_OWNER_REVIEW):
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
