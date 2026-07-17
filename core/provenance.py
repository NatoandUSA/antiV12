#!/usr/bin/env python3
"""
core.provenance — label where every fact/score came from, so reports never blur
observed data, estimates, human input, and AI inference.
"""
from __future__ import annotations

VERIFIED_FACT      = "VERIFIED_FACT"
AMAZON_NATIVE      = "AMAZON_NATIVE_DATA"
THIRD_PARTY        = "THIRD_PARTY_ESTIMATE"
SUPPLIER_PROVIDED  = "SUPPLIER_PROVIDED_DATA"
HUMAN_OBSERVATION  = "HUMAN_OBSERVATION"
AI_INFERENCE       = "AI_INFERENCE"
ASSUMPTION         = "ASSUMPTION"
MISSING            = "MISSING"

ALL = {VERIFIED_FACT, AMAZON_NATIVE, THIRD_PARTY, SUPPLIER_PROVIDED,
       HUMAN_OBSERVATION, AI_INFERENCE, ASSUMPTION, MISSING}

# things that can NEVER count as product-fact evidence
NOT_EVIDENCE = {AI_INFERENCE, ASSUMPTION, MISSING}

def item(value, source: str, note: str = "") -> dict:
    """Wrap a value with its provenance."""
    if source not in ALL:
        source = ASSUMPTION
    return {"value": value, "source": source, "note": note}

def is_evidence(source: str) -> bool:
    return source in ALL and source not in NOT_EVIDENCE


# ---- connected-research provenance (Session 6A.1) ----------------------------
# The standard record attached to anything fetched from a connected research source.
# Its defaults enforce the governance principle: connected data is INPUT to a
# human-reviewed decision, never an action, and never an automatic product fact.
REVIEW_REQUIRED = "OWNER_REVIEW_REQUIRED"


def research_provenance_record(*, source_type, provider, source_url_display=None,
                               retrieved_at="<unset>", content_sha256=None,
                               capability=None, destination_class=None,
                               connectivity_mode=None, data_classification="PUBLIC",
                               owner_approved=False, human_triggered=False,
                               expiration_or_review_date=None, warnings=None) -> dict:
    """Build the standard provenance record for a connected-research result.

    ``advisory_only`` and ``verified_as_product_fact`` are NOT caller-settable: research
    is always advisory and never a verified product fact until an owner reviews it.
    """
    return {
        "source_type": source_type,
        "provider": provider,
        "source_url_display": source_url_display,
        "retrieved_at": retrieved_at,
        "content_sha256": content_sha256,
        "capability": capability,
        "destination_class": destination_class,
        "connectivity_mode": connectivity_mode,
        "data_classification": data_classification,
        "owner_approved": bool(owner_approved),
        "human_triggered": bool(human_triggered),
        "advisory_only": True,
        "verified_as_product_fact": False,
        "review_status": REVIEW_REQUIRED,
        "expiration_or_review_date": expiration_or_review_date,
        "warnings": list(warnings or []),
    }
