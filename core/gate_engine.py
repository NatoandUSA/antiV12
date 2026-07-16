#!/usr/bin/env python3
"""
core.gate_engine — one reusable gate mechanism instead of scattered if-statements.

A Gate produces a structured result. The engine evaluates a set of gate results
and decides whether the project is PUBLICATION LOCKED. A failed hard gate can
NEVER be overridden by a good score.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any
try:
    from . import status as S
    from . import config
except ImportError:  # flat import (core/ on sys.path)
    import status as S
    import config

# base hard gates (every project) + creative gates (added conditionally by product family)
BASE_HARD_GATES = [
    "DEMAND", "RELEVANCE", "IP_SAFETY", "PRODUCT_FEASIBILITY", "ECONOMICS",
    "FULFILLMENT", "CATALOG_STRUCTURE", "CLAIMS_EVIDENCE",
    "PERSONALIZATION", "LISTING_ACCURACY", "OWNER_APPROVAL",
]
CREATIVE_GATES = ["MAIN_IMAGE_COMPLIANCE", "EMBROIDERY_PROOF", "VISUAL_CONSISTENCY",
                  "ACTUAL_ASSET_EDGE", "CREATIVE_OWNER_APPROVAL"]
HARD_GATES = BASE_HARD_GATES   # back-compat default

def required_gates(project: dict | None = None) -> list:
    """v2.3.1 (P0.5): creative gates are REQUIRED for projects that need listing images.
    Embroidery products additionally require EMBROIDERY_PROOF."""
    gates = list(BASE_HARD_GATES)
    p = project or {}
    needs_images = p.get("requires_listing_images", True)   # apparel/POD default: yes
    if needs_images:
        gates += [g for g in CREATIVE_GATES if g != "EMBROIDERY_PROOF"]
        method = str(p.get("decoration_method", p.get("product_family",""))).lower()
        if "embroider" in method or p.get("requires_embroidery_proof"):
            gates.append("EMBROIDERY_PROOF")
    return gates

# per-gate pass criteria — READY_FOR_REVIEW means a human must still decide; it is
# NOT a pass. A gate only passes on its own explicit success status(es).
GATE_PASS_STATUSES = {
    "DEMAND": {S.GO}, "RELEVANCE": {S.GO}, "IP_SAFETY": {"OK", S.GO},
    "PRODUCT_FEASIBILITY": {S.GO, S.APPROVED}, "ECONOMICS": {S.GO},
    "FULFILLMENT": {S.GO, S.APPROVED}, "CATALOG_STRUCTURE": {S.APPROVED},
    "CLAIMS_EVIDENCE": {"VERIFIED", S.APPROVED}, "PERSONALIZATION": {S.APPROVED},
    "LISTING_ACCURACY": {S.GO, S.APPROVED},
    "MAIN_IMAGE_COMPLIANCE": {S.APPROVED},   # v2.3.3: only owner-APPROVED asset passes (COMPLIANT is technical-only)
    "EMBROIDERY_PROOF": {"PARTIALLY_PROVEN", "PROVEN"},
    "VISUAL_CONSISTENCY": {"CONSISTENT"}, "ACTUAL_ASSET_EDGE": {S.APPROVED},
    "CREATIVE_OWNER_APPROVAL": {S.APPROVED}, "OWNER_APPROVAL": {S.APPROVED},
}
_DEFAULT_PASS = {S.GO, S.APPROVED, "OK", S.PUBLISHED_MANUALLY}   # NOTE: no READY_FOR_REVIEW

def _passes(gate_id: str, status: str) -> bool:
    return status in GATE_PASS_STATUSES.get(gate_id, _DEFAULT_PASS)

@dataclass
class GateResult:
    gate_id: str
    status: str
    blocking: bool = False
    score: float | None = None
    reasons: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    required_actions: list[str] = field(default_factory=list)
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    tool_version: str = None  # type: ignore

    def __post_init__(self):
        if self.tool_version is None:
            self.tool_version = config.tool_version()
        if self.status not in S.ALL_STATUSES and self.status != "OK":
            self.status = S.INCOMPLETE

    def passed(self) -> bool:
        return _passes(self.gate_id, self.status)

    def to_dict(self) -> dict:
        return asdict(self)

def gate(gate_id: str, status: str, *, reasons=None, evidence=None,
         required_actions=None, score=None, blocking: bool | None = None) -> GateResult:
    """Build a GateResult. blocking auto-set for hard gates that didn't pass."""
    g = GateResult(gate_id=gate_id, status=status, score=score,
                   reasons=list(reasons or []), evidence=list(evidence or []),
                   required_actions=list(required_actions or []))
    if blocking is None:
        g.blocking = (gate_id in BASE_HARD_GATES or gate_id in CREATIVE_GATES) and not g.passed() and status != S.INCOMPLETE
    else:
        g.blocking = blocking
    return g

def evaluate(gates: dict[str, dict | GateResult], project: dict | None = None) -> dict:
    """Given {gate_id: GateResult|dict}, decide publication lock against the gates
    REQUIRED for this project (base + conditional creative gates)."""
    norm: dict[str, dict] = {}
    for gid, g in gates.items():
        norm[gid] = g.to_dict() if isinstance(g, GateResult) else dict(g)

    req = required_gates(project)
    lock_reasons: list[str] = []
    for gid in req:
        g = norm.get(gid)
        if g is None:
            lock_reasons.append(f"{gid}=NOT_RUN")
            continue
        st = g.get("status")
        if st in S.HARD_STOP or g.get("blocking"):
            lock_reasons.append(f"{gid}={st}")
        elif st == S.INCOMPLETE:
            lock_reasons.append(f"{gid}=INCOMPLETE")
        elif not _passes(gid, st):
            lock_reasons.append(f"{gid}={st}")

    locked = len(lock_reasons) > 0
    return {
        "publication_locked": locked,
        "publication_lock_reasons": lock_reasons,
        "overall_status": S.BLOCKED if any(
            norm.get(g, {}).get("status") in S.HARD_STOP for g in req
        ) else (S.INCOMPLETE if locked else S.READY_FOR_REVIEW),
        "required_gates": req,
        "gates": norm,
    }
