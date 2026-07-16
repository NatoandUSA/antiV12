#!/usr/bin/env python3
"""
core.config — load adjustable business rules from config.yaml (not hard-coded).
Conservative defaults; every value is overridable. Category-specific rules live
in compliance/category_config.json, kept separate on purpose.
"""
from __future__ import annotations
import os, functools
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS: dict[str, Any] = {
    "marketplace": "US",
    "currency": "USD",
    "tool_version": "2.0.0",
    "business": {
        "minimum_contribution_profit": 8.00,
        "owner_only_publish": True,
        "seller_central_connection_allowed": False,
    },
    "demand": {
        "minimum_external_supporting_sources": 2,
        "required_amazon_anchor": True,
    },
    "listing": {
        "review_score_ready": 90,
        "review_score_owner_review": 85,
        "review_score_revise": 75,
    },
    "fulfillment": {
        "require_tracking": True,
        "peak_capacity_buffer_percent": 25,
    },
    "compliance": {
        "ip_block_is_hard_gate": True,
        "unsupported_high_risk_claim_is_hard_gate": True,
    },
}

def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _deep_merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out

@functools.lru_cache(maxsize=1)
def load() -> dict:
    path = os.path.join(_ROOT, "config.yaml")
    data = {}
    if os.path.exists(path):
        try:
            import yaml
            data = yaml.safe_load(open(path, encoding="utf-8")) or {}
        except Exception:
            data = {}
    return _deep_merge(DEFAULTS, data)

def get(*keys: str, default: Any = None) -> Any:
    d: Any = load()
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d

# convenience
def min_profit() -> float: return float(get("business", "minimum_contribution_profit", default=8.0))
def tool_version() -> str: return str(get("tool_version", default="2.0.0"))
def seller_central_allowed() -> bool: return bool(get("business", "seller_central_connection_allowed", default=False))
