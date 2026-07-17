#!/usr/bin/env python3
"""
listing.item_highlights_builder — category-capability & claim-aware item highlights (ACT-010).

Before this module `listing_generator.build_item_highlights()` produced a generic comma-joined string of
secondary keywords with NO category-capability check and NO per-highlight claim lineage — so a field that
may be unavailable for the actual product type, or a phrase with no verified backing, could be generated.

This builder generates an item highlight ONLY when ALL of these hold:
  * the category-policy registry declares the item_highlights catalog field supported for the category;
  * the backing claim/fact is VERIFIED (owner-review and unverified facts never publish);
  * the content is useful and non-duplicative (not a repeat of a visible field or another highlight);
  * every factual phrase is traceable to a claim id (lineage the PageAuditor can verify).

When the field is unsupported by the category, nothing is placed in the publishable payload — the field is
marked UNSUPPORTED_BY_CATEGORY and surfaced only in the manual-review report. When evidence is missing, the
concept is marked OWNER_FACT_REQUIRED (owner-review) or BLOCKED_UNVERIFIED (no evidence) — never filled with
generic copy. The builder invents nothing: it can only restate what an evidence-classed claim already
verified, so "premium material / soft / comfortable / durable / made to last / ships from the US /
tracking included / made to order / measured from real garments" cannot appear unless a VERIFIED claim
carries that exact meaning.

The allowed count and per-highlight character limit come from the shared category_policy_registry — there
is no second limit source.

Public API:
  build_item_highlights_content(claim_evidence, policy, keyword_source=None, product_facts=None,
                                title="", bullets=(), description="") -> ItemHighlightsResult
  write_item_highlights(folder, result, generated_at=None) -> path
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import claim_evidence as CE

ITEM_HIGHLIGHTS_SCHEMA_VERSION = "1.0.0"
ITEM_HIGHLIGHTS_FILENAME = "ITEM-HIGHLIGHTS.json"

# documented fallbacks used ONLY when the category policy leaves the limit unset.
DEFAULT_MAX_COUNT = 5
DEFAULT_CHARACTER_LIMIT = 125

# category-support states
SUPPORTED = "SUPPORTED"
UNSUPPORTED_BY_CATEGORY = "UNSUPPORTED_BY_CATEGORY"

# publishability states
PUBLISHABLE = "PUBLISHABLE"
OWNER_FACT_REQUIRED = "OWNER_FACT_REQUIRED"
BLOCKED_UNVERIFIED = "BLOCKED_UNVERIFIED"
PROHIBITED = "PROHIBITED"
DUPLICATE_CONCEPT = "DUPLICATE_CONCEPT"
DUPLICATE_OF_VISIBLE = "DUPLICATE_OF_VISIBLE"
OVER_CHARACTER_LIMIT = "OVER_CHARACTER_LIMIT"
MAX_COUNT_REACHED = "MAX_COUNT_REACHED"

# inclusion reason for a published highlight.
INCL_VERIFIED_UNIQUE_ATTRIBUTE = "VERIFIED_UNIQUE_ATTRIBUTE"

# The curated, bounded set of claim concepts that read as short catalog "item highlights" (attribute-style),
# in deterministic display order. A concept never in this list is simply not an item-highlight candidate.
HIGHLIGHT_CONCEPTS = (
    "material_composition",
    "real_machine_embroidery",
    "satin_stitch",
    "decoration_method",
    "personalization_fields",
    "fit",
    "size_range",
    "color_options",
    "care",
    "production_location",
    "packaging",
)


def canonical_json(obj):
    """Stable canonical serialization — sorted keys, fixed separators, no ASCII escaping."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2, separators=(",", ": "))


def _norm(s):
    s = str(s or "").lower().replace("-", " ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


# ---------------------------------------------------------------- result
class ItemHighlightsResult:
    """The deterministic, capability- and evidence-aware item-highlights payload for one product."""

    def __init__(self, category_support_state, category_identifier, max_count, character_limit,
                 highlights, source_claim_evidence_sha256):
        self.schema_version = ITEM_HIGHLIGHTS_SCHEMA_VERSION
        self.category_support_state = category_support_state
        self.category_identifier = category_identifier
        self.max_count = max_count
        self.character_limit = character_limit
        self.highlights = highlights                       # every candidate, published or blocked
        self.source_claim_evidence_sha256 = source_claim_evidence_sha256

    # -- views ------------------------------------------------------
    @property
    def category_field_supported(self):
        return self.category_support_state == SUPPORTED

    @property
    def publishable_highlights(self):
        return [h for h in self.highlights if h["publishability_status"] == PUBLISHABLE]

    @property
    def blocked_highlights(self):
        return [h for h in self.highlights if h["publishability_status"] != PUBLISHABLE]

    @property
    def publishable_count(self):
        return len(self.publishable_highlights)

    @property
    def blocked_count(self):
        return len(self.blocked_highlights)

    @property
    def owner_fact_required(self):
        """Fact fields behind the OWNER_FACT_REQUIRED / BLOCKED_UNVERIFIED highlights the owner must supply."""
        out = []
        for h in self.highlights:
            if h["publishability_status"] in (OWNER_FACT_REQUIRED, BLOCKED_UNVERIFIED):
                out.extend(h.get("source_fact_fields") or [])
        return sorted(set(out))

    def publishable_texts(self):
        return [h["text"] for h in self.publishable_highlights if h.get("text")]

    def manual_review_report(self):
        """The concepts held back from the publishable payload, with the reason and the facts they need."""
        return [{"highlight_id": h["highlight_id"], "concept": h["concept"],
                 "publishability_status": h["publishability_status"],
                 "exclusion_reason": h.get("exclusion_reason"),
                 "source_fact_fields": h.get("source_fact_fields") or []}
                for h in self.blocked_highlights]

    # -- serialization ----------------------------------------------
    def canonical_content(self):
        return {
            "schema_version": self.schema_version,
            "category_support_state": self.category_support_state,
            "category_identifier": self.category_identifier,
            "max_count": self.max_count,
            "character_limit": self.character_limit,
            "publishable_count": self.publishable_count,
            "blocked_count": self.blocked_count,
            "highlights": self.highlights,
            "publishable_highlights": self.publishable_highlights,
            "manual_review_report": self.manual_review_report(),
            "owner_fact_required": self.owner_fact_required,
            "source_claim_evidence_sha256": self.source_claim_evidence_sha256,
        }

    def content_sha256(self):
        return hashlib.sha256(canonical_json(self.canonical_content()).encode("utf-8")).hexdigest()

    def to_dict(self, generated_at=None):
        doc = self.canonical_content()
        doc["content_sha256"] = self.content_sha256()
        if generated_at:
            doc["generated_at"] = generated_at             # the only volatile field; excluded from the hash
        return doc

    def listing_block(self):
        """Compact block embedded in the listing so PageAuditor/copy_package can consume it directly."""
        return {
            "category_support_state": self.category_support_state,
            "publishable_count": self.publishable_count,
            "blocked_count": self.blocked_count,
            "max_count": self.max_count,
            "character_limit": self.character_limit,
            "publishable_highlights": self.publishable_highlights,
            "highlights": self.highlights,
            "manual_review_report": self.manual_review_report(),
            "owner_fact_required": self.owner_fact_required,
            "content_sha256": self.content_sha256(),
            "source_claim_evidence_sha256": self.source_claim_evidence_sha256,
        }


# ---------------------------------------------------------------- build
def _highlight(hid, concept, text, source_fields, claim_ids, state, category_state,
               publishability_status, inclusion_reason=None, exclusion_reason=None):
    return {
        "highlight_id": hid,
        "text": text,
        "concept": concept,
        "source_fact_fields": sorted(set(source_fields or [])),
        "claim_ids": list(claim_ids or []),
        "verification_state": state,
        "category_support_state": category_state,
        "publishability_status": publishability_status,
        "inclusion_reason": inclusion_reason,
        "exclusion_reason": exclusion_reason,
    }


def build_item_highlights_content(claim_evidence, policy, keyword_source=None, product_facts=None,
                                  title="", bullets=(), description=""):
    """Build the capability- & evidence-aware item highlights from evidence-classed claims + the policy.

    Generates a publishable highlight ONLY for a VERIFIED claim concept when the category supports the
    field, the text is unique (not a visible-field or concept duplicate) and within the policy character
    limit, and the running count is under the policy maximum. Everything else is recorded — with a reason —
    for the manual-review report and never enters the publishable payload.
    """
    supported = policy.supports_item_highlights()
    category_state = SUPPORTED if supported else UNSUPPORTED_BY_CATEGORY
    max_count = policy.item_highlights_max_count or DEFAULT_MAX_COUNT
    char_limit = policy.item_highlights_character_limit or DEFAULT_CHARACTER_LIMIT
    ce_sha = claim_evidence.content_sha256() if claim_evidence is not None else None

    visible_norm = " " + _norm(" ".join([title, description] + [str(b) for b in (bullets or [])])) + " "

    highlights = []
    seen_concepts = set()
    published = 0

    for concept in HIGHLIGHT_CONCEPTS:
        claim = claim_evidence.claim(concept) if claim_evidence is not None else None
        if not claim:
            continue
        # consume the EFFECTIVE (atomicity-gated) state, never the raw verification_state — a compound /
        # mixed claim must never promote a blocked component into a published highlight (Session 6C.1).
        state = claim.get("effective_evidence_state") or claim["verification_state"]
        text = claim.get("proposed_text")
        hid = "IH-" + concept.upper()
        source_fields = claim.get("source_fact_fields") or []
        claim_ids = [claim["claim_id"]] if (text and state in (CE.VERIFIED, CE.SUPPORTED_OWNER_REVIEW)) else []

        # 1) category gate first — an unsupported field never enters the publishable payload.
        if not supported:
            highlights.append(_highlight(hid, concept, text, source_fields, claim_ids, state,
                                         category_state, UNSUPPORTED_BY_CATEGORY,
                                         exclusion_reason=UNSUPPORTED_BY_CATEGORY))
            continue

        # 2) evidence gate — only VERIFIED claims are candidates to publish.
        if state == CE.PROHIBITED:
            highlights.append(_highlight(hid, concept, None, source_fields, [], state, category_state,
                                         PROHIBITED, exclusion_reason=PROHIBITED))
            continue
        if state == CE.SUPPORTED_OWNER_REVIEW:
            highlights.append(_highlight(hid, concept, None, source_fields, [], state, category_state,
                                         OWNER_FACT_REQUIRED, exclusion_reason=OWNER_FACT_REQUIRED))
            continue
        if state != CE.VERIFIED or not text:
            highlights.append(_highlight(hid, concept, None, source_fields, [], state, category_state,
                                         BLOCKED_UNVERIFIED, exclusion_reason=BLOCKED_UNVERIFIED))
            continue

        # 3) usefulness gates — no duplicate concept, no visible-field repeat, within limits.
        if concept in seen_concepts:
            highlights.append(_highlight(hid, concept, text, source_fields, claim_ids, state,
                                         category_state, DUPLICATE_CONCEPT,
                                         exclusion_reason=DUPLICATE_CONCEPT))
            continue
        if _norm(text) and _norm(text) in visible_norm:
            highlights.append(_highlight(hid, concept, text, source_fields, claim_ids, state,
                                         category_state, DUPLICATE_OF_VISIBLE,
                                         exclusion_reason=DUPLICATE_OF_VISIBLE))
            continue
        if len(text) > char_limit:
            highlights.append(_highlight(hid, concept, text, source_fields, claim_ids, state,
                                         category_state, OVER_CHARACTER_LIMIT,
                                         exclusion_reason=OVER_CHARACTER_LIMIT))
            continue
        if published >= max_count:
            highlights.append(_highlight(hid, concept, text, source_fields, claim_ids, state,
                                         category_state, MAX_COUNT_REACHED,
                                         exclusion_reason=MAX_COUNT_REACHED))
            continue

        # publish — a verified, unique, traceable attribute highlight.
        highlights.append(_highlight(hid, concept, text, source_fields, claim_ids, state, category_state,
                                     PUBLISHABLE, inclusion_reason=INCL_VERIFIED_UNIQUE_ATTRIBUTE))
        seen_concepts.add(concept)
        published += 1

    return ItemHighlightsResult(category_support_state=category_state,
                                category_identifier=policy.category_identifier,
                                max_count=max_count, character_limit=char_limit,
                                highlights=highlights, source_claim_evidence_sha256=ce_sha)


def write_item_highlights(folder, result, generated_at=None, outdir=None):
    """Write the canonical ITEM-HIGHLIGHTS.json for a folder. -> path."""
    path = os.path.join(outdir or folder, ITEM_HIGHLIGHTS_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        f.write(canonical_json(result.to_dict(generated_at=generated_at)))
    return path
