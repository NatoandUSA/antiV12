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
