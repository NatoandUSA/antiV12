#!/usr/bin/env python3
"""
listing.description_engine — evidence-backed product description (ACT-008).

Before this engine the description always asserted real machine embroidery, personalization, made-to-
order, and US shipping regardless of the facts. This engine builds the description ONLY from supported
sections: each factual section is emitted only when a VERIFIED claim backs it, links to that claim's id,
and a section with no verified evidence is omitted (with the reason recorded) rather than filled.

Sections, in order:
  product_identity   (always — the approved product keyword)
  personalization    (verified personalization / exact-personalization)
  decoration         (verified embroidery / satin / decoration method)
  attributes         (verified material / fit / size / color / measurements)
  fulfillment        (verified care / production / packaging / shipping)
  recipient_occasion (verified recipient / occasion)

Public API:
  build_description(claim_evidence, primary_keyword, eligible_keywords=()) -> DescriptionResult
"""
from __future__ import annotations

PUBLISHABLE = "PUBLISHABLE"
BLOCKED_INCOMPLETE = "BLOCKED_INCOMPLETE"

# section_id -> ordered claim concepts that may fill it.
SECTIONS = (
    ("personalization", ("personalization_fields", "exact_personalization_promise")),
    ("decoration", ("real_machine_embroidery", "satin_stitch", "tatami_fill", "decoration_method")),
    ("attributes", ("material_composition", "fit", "size_range", "color_options", "measurements")),
    ("fulfillment", ("care", "production_location", "production_time", "handling_time",
                     "shipping_method", "tracking", "packaging", "made_to_order")),
    ("recipient_occasion", ("recipient", "occasion")),
)


def _lower_phrase(s):
    import re
    return re.sub(r"\s+", " ", str(s or "")).strip()


def build_description(claim_evidence, primary_keyword, eligible_keywords=()):
    """Assemble the description from verified sections only. Product identity is always present; every
    other section appears only when a VERIFIED claim supports it."""
    identity_phrase = _lower_phrase(primary_keyword).lower()
    identity_text = f"A {identity_phrase}." if identity_phrase else "A personalized item."

    sections = [{
        "section_id": "product_identity",
        "text": identity_text,
        "claim_ids": [],
        "source": f"keyword:{primary_keyword}",
        "state": "PRODUCT_IDENTITY",
    }]
    omitted = []
    all_claim_ids = []

    for section_id, concepts in SECTIONS:
        verified, missing_fields = [], []
        for concept in concepts:
            c = claim_evidence.claim(concept) if claim_evidence else None
            if not c:
                continue
            if c["publishable"] and c.get("proposed_text"):
                verified.append(c)
            elif c["verification_state"] in ("UNVERIFIED_BLOCKED", "SUPPORTED_OWNER_REVIEW", "PROHIBITED"):
                for f in c.get("source_fact_fields", []):
                    if not f.startswith("keyword:"):
                        missing_fields.append(f)
                if not c.get("source_fact_fields"):
                    missing_fields.append(concept)
        if section_id == "decoration":
            verified = _dedupe_decoration(verified)
        if verified:
            text = " ".join(c["proposed_text"] for c in verified)
            cids = [c["claim_id"] for c in verified]
            all_claim_ids += cids
            sections.append({"section_id": section_id, "text": text, "claim_ids": cids,
                             "source": "claim_evidence", "state": "VERIFIED"})
        else:
            omitted.append({"section_id": section_id,
                            "reason": "no verified evidence",
                            "missing_fact_fields": sorted(set(missing_fields))})

    description_text = " ".join(s["text"] for s in sections)
    # publishable if any factual section is verified; identity-only is a safe minimal draft.
    status = PUBLISHABLE if len(sections) > 1 else BLOCKED_INCOMPLETE
    return DescriptionResult(description_text, sections, omitted, all_claim_ids,
                             list(eligible_keywords)[:8], status)


def _dedupe_decoration(verified):
    concepts = {c["normalized_concept"] for c in verified}
    if concepts & {"real_machine_embroidery", "satin_stitch", "tatami_fill"}:
        return [c for c in verified if c["normalized_concept"] != "decoration_method"]
    return verified


class DescriptionResult:
    def __init__(self, text, sections, omitted, claim_ids, keyword_allocations, status):
        self.description_text = text
        self.sections = sections
        self.omitted_sections = omitted
        self.claim_ids = claim_ids
        self.keyword_allocations = keyword_allocations
        self.publishability = status

    def to_dict(self):
        return {
            "description_text": self.description_text,
            "sections": self.sections,
            "omitted_sections": self.omitted_sections,
            "claim_ids": self.claim_ids,
            "keyword_allocations": self.keyword_allocations,
            "publishability": self.publishability,
        }
