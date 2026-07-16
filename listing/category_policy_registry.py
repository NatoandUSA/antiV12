#!/usr/bin/env python3
"""
listing.category_policy_registry — the ONE versioned authority for per-category listing limits (ACT-004).

Before this module a universal 75-character title rule was hardcoded in the listing generator, the
compliance validator, the dashboard counters/warnings, the dashboard template, and the tests. That
treated one unverified rule as binding everywhere and could not adapt by category. Every consumer now
resolves title/backend/field limits through this registry, so changing one policy record updates the
generator, the validator, and the UI counters together.

Data source: category_policies.json (next to this module). It is the single authority for the fields on
a category-policy record. compliance/category_config.json keeps only its claim rules and per-category
severity/soft-warning settings — it no longer carries the title/backend hard limits, so there is no
second authority.

Honesty rules baked in:
  * The limits are the toolkit owner's documented working values for Amazon US listing optimization and
    customer experience. They are NOT presented as an official Amazon rule table.
  * When no category-specific policy exists, resolution returns a documented LOCAL_FALLBACK record with
    fallback_used=True and warning_state=POLICY_VERIFICATION_REQUIRED. The fallback is never described
    as an official or universal Amazon limit.

Public API:
  load_policy_registry(path=None)                         -> PolicyRegistry
  resolve_category_policy(category, marketplace="US", owner_override=None, registry=None) -> CategoryPolicy
  validate_policy_record(record)                          -> [error, ...]  (empty == valid)
  get_title_hard_limit(category, marketplace="US", registry=None)        -> int
  get_backend_byte_ceiling(category, marketplace="US", registry=None)    -> int
  get_supported_catalog_fields(category, marketplace="US", registry=None)-> [field, ...]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY_FILENAME = "category_policies.json"
DEFAULT_REGISTRY_PATH = os.path.join(HERE, REGISTRY_FILENAME)

REGISTRY_SCHEMA_VERSION = "1.0.0"
DEFAULT_MARKETPLACE = "US"
FALLBACK_CATEGORY = "__fallback__"

# warning_state values
WARNING_NONE = "OK"
POLICY_VERIFICATION_REQUIRED = "POLICY_VERIFICATION_REQUIRED"

# The fields every category-policy record must carry.
REQUIRED_FIELDS = (
    "schema_version", "marketplace", "product_category", "category_identifier",
    "title_hard_limit", "backend_byte_ceiling", "supported_catalog_fields",
    "unsupported_catalog_fields", "policy_source",
)


# ---------------------------------------------------------------- errors
class CategoryPolicyError(Exception):
    """Base class for every category-policy failure."""


class PolicyRegistryError(CategoryPolicyError):
    """The registry file is missing, unreadable, malformed, or structurally wrong."""


class InvalidPolicyRecordError(CategoryPolicyError):
    """A policy record is present but does not satisfy the record contract."""

    def __init__(self, identifier, errors):
        self.identifier = identifier
        self.errors = list(errors)
        super().__init__(f"invalid category-policy record {identifier!r}: " + "; ".join(self.errors))


# ---------------------------------------------------------------- record
@dataclass
class CategoryPolicy:
    """A resolved, versioned category policy. `to_dict()` is stable for serialization/payloads."""
    schema_version: str
    marketplace: str
    product_category: str
    category_identifier: str
    title_hard_limit: int
    title_recommended_target: object = None
    bullet_count: object = None
    bullet_character_limit: object = None
    description_character_limit: object = None
    backend_byte_ceiling: int = 249
    # Item-highlights (ACT-010) limits come from THIS registry — there is no second limit source.
    item_highlights_max_count: object = None
    item_highlights_character_limit: object = None
    supported_catalog_fields: list = field(default_factory=list)
    unsupported_catalog_fields: list = field(default_factory=list)
    policy_source: str = ""
    policy_verified_at: object = None
    owner_override: object = None
    warning_state: str = WARNING_NONE
    fallback_used: bool = False

    def to_dict(self):
        return asdict(self)

    def supports_field(self, catalog_field):
        return catalog_field in self.supported_catalog_fields

    def supports_item_highlights(self):
        """True only when this category declares the item_highlights catalog field (ACT-010)."""
        return self.supports_field("item_highlights")


# ---------------------------------------------------------------- validation
def validate_policy_record(record):
    """Return a list of human-readable errors for a policy record (empty list == valid).

    Accepts a dict or a CategoryPolicy. Never raises — callers decide whether to raise.
    """
    if isinstance(record, CategoryPolicy):
        record = record.to_dict()
    errors = []
    if not isinstance(record, dict):
        return ["policy record must be an object"]

    for key in REQUIRED_FIELDS:
        if key not in record or record[key] is None:
            errors.append(f"missing required field '{key}'")

    def _pos_int(key, allow_none=False):
        if key not in record or record[key] is None:
            if not allow_none:
                errors.append(f"'{key}' is required")
            return
        v = record[key]
        if isinstance(v, bool) or not isinstance(v, int):
            errors.append(f"'{key}' must be an integer, got {type(v).__name__}")
        elif v <= 0:
            errors.append(f"'{key}' must be positive, got {v}")

    _pos_int("title_hard_limit")
    _pos_int("backend_byte_ceiling")
    _pos_int("title_recommended_target", allow_none=True)
    _pos_int("bullet_count", allow_none=True)
    _pos_int("bullet_character_limit", allow_none=True)
    _pos_int("description_character_limit", allow_none=True)
    _pos_int("item_highlights_max_count", allow_none=True)
    _pos_int("item_highlights_character_limit", allow_none=True)

    for key in ("marketplace", "product_category", "category_identifier", "policy_source"):
        v = record.get(key)
        if key in record and (not isinstance(v, str) or not v.strip()):
            errors.append(f"'{key}' must be a non-empty string")

    for key in ("supported_catalog_fields", "unsupported_catalog_fields"):
        v = record.get(key)
        if v is not None and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            errors.append(f"'{key}' must be a list of strings")

    tgt, hard = record.get("title_recommended_target"), record.get("title_hard_limit")
    if isinstance(tgt, int) and not isinstance(tgt, bool) and isinstance(hard, int) \
            and not isinstance(hard, bool) and tgt > hard:
        errors.append(f"title_recommended_target ({tgt}) must not exceed title_hard_limit ({hard})")
    return errors


def _record_to_policy(record, fallback_used=False, warning_state=WARNING_NONE):
    return CategoryPolicy(
        schema_version=record.get("schema_version", REGISTRY_SCHEMA_VERSION),
        marketplace=record["marketplace"],
        product_category=record["product_category"],
        category_identifier=record["category_identifier"],
        title_hard_limit=record["title_hard_limit"],
        title_recommended_target=record.get("title_recommended_target"),
        bullet_count=record.get("bullet_count"),
        bullet_character_limit=record.get("bullet_character_limit"),
        description_character_limit=record.get("description_character_limit"),
        backend_byte_ceiling=record["backend_byte_ceiling"],
        item_highlights_max_count=record.get("item_highlights_max_count"),
        item_highlights_character_limit=record.get("item_highlights_character_limit"),
        supported_catalog_fields=list(record.get("supported_catalog_fields") or []),
        unsupported_catalog_fields=list(record.get("unsupported_catalog_fields") or []),
        policy_source=record.get("policy_source", ""),
        policy_verified_at=record.get("policy_verified_at"),
        warning_state=warning_state,
        fallback_used=fallback_used,
    )


# ---------------------------------------------------------------- registry
class PolicyRegistry:
    """The loaded set of category policies plus a documented fallback."""

    def __init__(self, schema_version, default_marketplace, records, fallback_record, source_path):
        self.schema_version = schema_version
        self.default_marketplace = default_marketplace
        self.source_path = source_path
        self._by_key = {}
        for rec in records:
            self._by_key[self._key(rec["marketplace"], rec["product_category"])] = rec
        self._fallback = fallback_record

    @staticmethod
    def _key(marketplace, category):
        return (str(marketplace or "").strip().upper(), str(category or "").strip().lower())

    def categories(self):
        return sorted(self._by_key)

    def resolve(self, category, marketplace=None, owner_override=None):
        marketplace = marketplace or self.default_marketplace
        rec = self._by_key.get(self._key(marketplace, category))
        if rec is not None:
            policy = _record_to_policy(rec, fallback_used=False, warning_state=WARNING_NONE)
        else:
            policy = _record_to_policy(self._fallback, fallback_used=True,
                                       warning_state=POLICY_VERIFICATION_REQUIRED)
            # Reflect what the caller asked for, without pretending a verified record exists.
            policy.marketplace = str(marketplace or self.default_marketplace).strip().upper()
            policy.product_category = str(category or "").strip().lower() or FALLBACK_CATEGORY
            policy.category_identifier = f"{policy.marketplace}:{policy.product_category}(fallback)"
        if owner_override:
            _apply_owner_override(policy, owner_override)
        return policy


def _apply_owner_override(policy, owner_override):
    """Merge an owner override dict over a resolved policy and record that it was applied."""
    if not isinstance(owner_override, dict):
        raise CategoryPolicyError("owner_override must be a dict of field -> value")
    applied = {}
    for key, value in owner_override.items():
        if hasattr(policy, key) and key not in ("owner_override", "warning_state", "fallback_used"):
            setattr(policy, key, value)
            applied[key] = value
    policy.owner_override = applied or dict(owner_override)
    return policy


# ---------------------------------------------------------------- loading
def load_policy_registry(path=None):
    """Load the category-policy registry from `path` (default: category_policies.json beside this module).

    Raises PolicyRegistryError for missing/malformed/structurally-wrong files and
    InvalidPolicyRecordError when a record does not satisfy the record contract.
    """
    path = path or DEFAULT_REGISTRY_PATH
    if not os.path.exists(path):
        raise PolicyRegistryError(f"category-policy registry not found: {path}")
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except json.JSONDecodeError as e:
        raise PolicyRegistryError(f"malformed category-policy registry {path} "
                                  f"(line {e.lineno}, column {e.colno}): {e.msg}") from e
    except OSError as e:
        raise PolicyRegistryError(f"cannot read category-policy registry {path}: {e}") from e

    if not isinstance(doc, dict) or not isinstance(doc.get("policies"), list):
        raise PolicyRegistryError(f"{path}: expected an object with a 'policies' list")

    records = doc["policies"]
    for rec in records:
        errs = validate_policy_record(rec)
        if errs:
            raise InvalidPolicyRecordError(
                rec.get("category_identifier") if isinstance(rec, dict) else rec, errs)

    fallback = doc.get("fallback_policy")
    if not isinstance(fallback, dict):
        raise PolicyRegistryError(f"{path}: a 'fallback_policy' object is required")
    fb_errs = validate_policy_record(fallback)
    if fb_errs:
        raise InvalidPolicyRecordError("fallback_policy", fb_errs)

    return PolicyRegistry(
        schema_version=doc.get("schema_version", REGISTRY_SCHEMA_VERSION),
        default_marketplace=doc.get("default_marketplace", DEFAULT_MARKETPLACE),
        records=records, fallback_record=fallback, source_path=path)


_DEFAULT_REGISTRY = None


def default_registry():
    """The process-wide default registry, loaded once from category_policies.json."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = load_policy_registry()
    return _DEFAULT_REGISTRY


# ---------------------------------------------------------------- convenience API
def resolve_category_policy(category, marketplace=DEFAULT_MARKETPLACE, owner_override=None, registry=None):
    """Resolve one category policy. Unknown category -> documented fallback (POLICY_VERIFICATION_REQUIRED)."""
    registry = registry or default_registry()
    return registry.resolve(category, marketplace=marketplace, owner_override=owner_override)


def get_title_hard_limit(category, marketplace=DEFAULT_MARKETPLACE, registry=None):
    return resolve_category_policy(category, marketplace, registry=registry).title_hard_limit


def get_backend_byte_ceiling(category, marketplace=DEFAULT_MARKETPLACE, registry=None):
    return resolve_category_policy(category, marketplace, registry=registry).backend_byte_ceiling


def get_supported_catalog_fields(category, marketplace=DEFAULT_MARKETPLACE, registry=None):
    return resolve_category_policy(category, marketplace, registry=registry).supported_catalog_fields


def get_item_highlights_max_count(category, marketplace=DEFAULT_MARKETPLACE, registry=None):
    """The item-highlights allowed count for a category, from the shared registry (None when unset)."""
    return resolve_category_policy(category, marketplace, registry=registry).item_highlights_max_count


def main():
    ap = argparse.ArgumentParser(description="Resolve a category policy from the registry")
    ap.add_argument("category", nargs="?", default="apparel")
    ap.add_argument("--marketplace", default=DEFAULT_MARKETPLACE)
    ap.add_argument("--path", default=None)
    a = ap.parse_args()
    try:
        reg = load_policy_registry(a.path)
    except CategoryPolicyError as e:
        print(f"FAILED: {e}")
        sys.exit(2)
    p = reg.resolve(a.category, marketplace=a.marketplace)
    print(json.dumps(p.to_dict(), indent=2))
    if p.fallback_used:
        print(f"\n[{p.warning_state}] no verified policy for {a.marketplace}:{a.category} — using fallback")
    sys.exit(0)


if __name__ == "__main__":
    main()
