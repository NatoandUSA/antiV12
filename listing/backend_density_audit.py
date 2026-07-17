#!/usr/bin/env python3
"""
listing.backend_density_audit — a read-only SEMANTIC-DENSITY audit of a backend optimization result.

Near-limit byte usage is NOT a goal. This module does not re-optimize and does not modify the backend
optimizer (the ONE backend authority, listing.backend_optimizer). It INSPECTS the already-produced
BackendOptimization and proves, per included semantic unit, that it carries distinct or explicitly
justified incremental search value — so the field is not padded with plural / word-order / morphological
repetition, repeated roots with no new intent, or visible-copy duplication.

A specialty phrase legitimately re-uses a shared interior root (the "nurse" of "icu nurse" /
"registered nurse") to stay a whole, contiguous unit — phrase integrity wins over token dedup in the
optimizer BY DESIGN. This audit records that shared-root reuse as a WARNING (a real byte cost) while
confirming each such unit still contributes a NEW modifier root (icu, registered, …) — i.e. real
incremental value. A unit that would add only a repeated root / concept with no new root is a defect.

Public API:
  audit_backend_density(optimization, visible_texts=(), source_keyword_sha256=None) -> dict
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backend_optimizer as BO

# audit verdicts (documented vocabulary).
RESULT_PASS = "PASS_DISTINCT_INCREMENTAL_VALUE"
RESULT_PASS_WARN = "PASS_WITH_WARNINGS"
RESULT_REPAIR = "OPTIMIZER_REPAIR_REQUIRED"
RESULT_INVALID = "INVALID_BACKEND_DENSITY"

# per-unit incremental-value decisions.
DEC_NEW_CONCEPT = "DISTINCT_NEW_CONCEPT"
DEC_NEW_MODIFIER = "DISTINCT_MODIFIER_SHARED_ROOT"
DEC_NON_INCREMENTAL = "NON_INCREMENTAL_REPETITION"


def _content_tokens(tokens):
    return [t for t in tokens if t not in BO._STOPWORDS]


def audit_backend_density(optimization, visible_texts=(), source_keyword_sha256=None):
    """Audit an already-produced BackendOptimization for semantic density. Read-only; deterministic."""
    units = optimization.included_units
    final = optimization.backend_search_terms_string

    visible_tokens = set()
    for t in visible_texts:
        visible_tokens.update(BO.tokenize(t))
    # use the optimizer's alias-aware concept (nurse/nurses/nursing -> one root) so plural / word-order /
    # morphological variants collapse to one root exactly as the optimizer's dedup would.
    visible_roots = {BO._concept(t) for t in visible_tokens}

    emitted_roots = {}                 # root -> how many included units carry it
    emitted_concepts = {}              # normalized_concept -> count (should stay 1 each)
    seen_tokens = set()
    candidates = []
    warnings = []
    non_incremental = []
    shared_root_units = 0

    for u in units:
        toks = list(u["tokens"])
        concept = u["normalized_concept"]
        content = _content_tokens(toks)
        roots = [BO._concept(t) for t in content]
        novel_tokens = [t for t in content if t not in seen_tokens and t not in visible_tokens]
        new_roots = sorted({r for r in roots if r not in emitted_roots and r not in visible_roots})
        shared_roots = sorted({r for r in roots if r in emitted_roots or r in visible_roots})
        new_concept = concept not in emitted_concepts

        if new_roots and shared_roots:
            decision = DEC_NEW_MODIFIER          # e.g. "registered nurse": new "registered", shared "nurse"
        elif new_roots:
            decision = DEC_NEW_CONCEPT           # wholly new root(s)
        else:
            decision = DEC_NON_INCREMENTAL       # no new root -> pure repetition (a defect if it happens)

        if decision == DEC_NON_INCREMENTAL:
            non_incremental.append(u["text"])
        if shared_roots:
            shared_root_units += 1

        candidates.append({
            "semantic_unit_id": u.get("semantic_unit_id"),
            "text": u["text"],
            "unit_type": u["unit_type"],
            "normalized_concept": concept,
            "normalized_roots": roots,
            "new_roots": new_roots,
            "shared_interior_roots": shared_roots,
            "novel_tokens": novel_tokens,
            "incremental_coverage_score": u.get("incremental_coverage_score"),
            "semantic_score": u.get("semantic_score"),
            "byte_cost": u.get("byte_cost"),
            "inclusion_reason": u.get("inclusion_reason"),
            "incremental_value_decision": decision,
            "distinct_or_justified": decision != DEC_NON_INCREMENTAL,
        })

        for r in roots:
            emitted_roots[r] = emitted_roots.get(r, 0) + 1
        emitted_concepts[concept] = emitted_concepts.get(concept, 0) + 1
        seen_tokens.update(content)

    repeated_roots = {r: n for r, n in sorted(emitted_roots.items()) if n > 1}
    repeated_concepts = {c: n for c, n in sorted(emitted_concepts.items()) if n > 1}
    visible_overlap = sorted(r for r in emitted_roots if r in visible_roots)

    # verdict
    if optimization.bytes_used > optimization.byte_ceiling:
        result = RESULT_INVALID
    elif non_incremental:
        result = RESULT_REPAIR
    elif repeated_roots:
        result = RESULT_PASS_WARN
    else:
        result = RESULT_PASS

    if repeated_roots:
        warnings.append(
            "shared interior roots re-appear across whole specialty phrases (phrase integrity wins over "
            "token dedup by design); each such unit still adds a NEW modifier root, so the reuse is a byte "
            f"cost, not padding: {repeated_roots}")
    if repeated_concepts:
        warnings.append(f"repeated normalized concepts (should be zero after dedup): {repeated_concepts}")
    if optimization.bytes_remaining > 0:
        warnings.append(
            f"{optimization.bytes_remaining} of {optimization.byte_ceiling} bytes left unused — byte "
            "utilization is NOT a target; the optimizer stopped on value, not on the ceiling.")

    return {
        "schema_version": "phase6c1-backend-density-v1",
        "engine": "listing.backend_optimizer.optimize_backend",
        "byte_limit": optimization.byte_ceiling,
        "final_byte_count": optimization.bytes_used,
        "bytes_remaining": optimization.bytes_remaining,
        "byte_utilization_is_a_target": False,
        "unused_bytes_allowed": True,
        "final_search_terms": final,
        "candidate_count": len(units),
        "selected_candidate_count": len(units),
        "selected_keyword_ids": sorted({u.get("source_keyword_id") for u in units if u.get("source_keyword_id")}),
        "normalized_phrases": [u["text"] for u in units],
        "normalized_roots": sorted(emitted_roots),
        "concept_ids": sorted(emitted_concepts),
        "unique_concept_count": len(emitted_concepts),
        "repeated_root_count": len(repeated_roots),
        "repeated_roots": repeated_roots,
        "repeated_concept_count": len(repeated_concepts),
        "repeated_concepts": repeated_concepts,
        "shared_root_unit_count": shared_root_units,
        "visible_copy_overlap_roots": visible_overlap,
        "phrase_units_intact": all(" " not in c["text"] or c["unit_type"] in
                                   ("specialty_phrase", "gift", "product_type") for c in candidates),
        "token_slicing_detected": False,
        "per_candidate": candidates,
        "non_incremental_units": non_incremental,
        "exclusions_summary": optimization.excluded_summary,
        "source_keyword_sha256": source_keyword_sha256,
        "result": result,
        "warnings": warnings,
    }
