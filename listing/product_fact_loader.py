#!/usr/bin/env python3
"""
listing.product_fact_loader — typed loading of verified product facts (ACT-006).

Before this module the listing generator replaced missing product facts with invented defaults
("cotton-blend", "any name and credentials", "any occasion", "10-14 business days", "embroidered
name"), so unsupported materials, personalization, occasions, lead times, and design claims could be
published. This loader makes unknown facts stay unknown: they normalize to null with an explicit state,
never a fabricated value.

It distinguishes, deliberately and separately:
  1. required file missing        -> MissingRequiredProductFactsError (hard fail)
  2. optional file missing        -> empty facts + a warning (all fields UNKNOWN)
  3. existing file malformed       -> MalformedProductFactsError with path, line, column, parser message
  4. existing file invalid schema  -> InvalidProductFactsSchemaError (hard fail, clear message)
  5. field absent                  -> UNKNOWN (value null)
  6. field explicitly null         -> UNKNOWN (value null)
  7. field present and verified    -> VERIFIED / OWNER_CONFIRMED (publishable)
  8. field present, needs review   -> OWNER_REVIEW_REQUIRED (non-publishable)

Fact states: VERIFIED, OWNER_CONFIRMED, OWNER_REVIEW_REQUIRED, UNKNOWN, BLOCKED.

The normalized output is structured so a future claim-evidence engine can consume it without another
schema rewrite. This module deliberately does NOT implement that engine.

Input file (product-facts.json) shape — every field is optional; absent == UNKNOWN:
  {
    "schema_version": "1.0.0",
    "source": "owner",                       # optional default source label
    "product": {                             # or "facts" / "product_facts", or top-level fields
      "brand": {"value": "Acme", "status": "VERIFIED", "source": "owner",
                "notes": "...", "updated_at": "2026-07-16"},
      "material": null,                      # explicitly unknown
      "color_options": "black, navy, maroon",# bare value -> OWNER_REVIEW_REQUIRED, comma-normalized
      "occasion": {"value": "graduation", "status": "OWNER_REVIEW_REQUIRED"}
    }
  }

Public API:
  load_product_facts(path=None, folder=None, filename="product-facts.json", required=False)
  write_normalized_product_facts(folder, facts=None, generated_at=None)  -> (path, NormalizedProductFacts)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

NORMALIZED_SCHEMA_VERSION = "1.0.0"
NORMALIZED_FILENAME = "NORMALIZED-PRODUCT-FACTS.json"
DEFAULT_FACTS_FILENAME = "product-facts.json"

# fact states
VERIFIED = "VERIFIED"
OWNER_CONFIRMED = "OWNER_CONFIRMED"
OWNER_REVIEW_REQUIRED = "OWNER_REVIEW_REQUIRED"
UNKNOWN = "UNKNOWN"
BLOCKED = "BLOCKED"

PUBLISHABLE_STATES = (VERIFIED, OWNER_CONFIRMED)

# The product-fact vocabulary. Every field defaults to UNKNOWN when absent.
SCALAR_FIELDS = (
    "brand", "product_type", "garment_type", "material", "material_composition", "fit",
    "decoration_method", "care_instructions", "production_location", "production_time_range",
    "handling_time", "shipping_method", "tracking", "packaging", "audience", "occasion",
    # WHERE THE PARCEL SHIPS FROM, as a country the owner states outright. Deliberately separate
    # from shipping_method (a carrier/service in free prose) and from production_location (where
    # the item was MADE). Fulfillment origin and manufacturing origin are different claims with
    # different legal weight, and this owner ships some SKUs domestically and some direct.
    "ship_from_country",
    "verified_differentiator", "placement",
)
LIST_FIELDS = (
    "size_range", "measurements", "color_options", "personalization_fields", "character_limits",
    "thread_colors", "design_dimensions",
)
KNOWN_FIELDS = SCALAR_FIELDS + LIST_FIELDS

# how an owner writes a status -> canonical state
_STATUS_ALIASES = {
    "verified": VERIFIED, "verify": VERIFIED, "confirmed": VERIFIED,
    "owner_confirmed": OWNER_CONFIRMED, "owner-confirmed": OWNER_CONFIRMED,
    "owner_review_required": OWNER_REVIEW_REQUIRED, "owner_review": OWNER_REVIEW_REQUIRED,
    "review": OWNER_REVIEW_REQUIRED, "review_required": OWNER_REVIEW_REQUIRED,
    "needs_review": OWNER_REVIEW_REQUIRED, "pending": OWNER_REVIEW_REQUIRED,
    "unknown": UNKNOWN, "unverified": OWNER_REVIEW_REQUIRED,
    "blocked": BLOCKED, "prohibited": BLOCKED,
}


# ---------------------------------------------------------------- errors
class ProductFactError(Exception):
    """Base class for every product-fact failure."""


class MissingRequiredProductFactsError(ProductFactError):
    """A required product-facts file does not exist."""

    def __init__(self, path):
        self.path = path
        super().__init__(f"required product-facts file not found: {path}")


class MalformedProductFactsError(ProductFactError):
    """The product-facts file exists but is not valid JSON. Reports where it broke; never silently empty."""

    def __init__(self, path, line, column, parser_message):
        self.path, self.line, self.column, self.parser_message = path, line, column, parser_message
        super().__init__(f"malformed product-facts file: {path} (line {line}, column {column}): "
                         f"{parser_message}. Refusing to convert malformed input to empty data.")


class InvalidProductFactsSchemaError(ProductFactError):
    """The product-facts file is valid JSON but does not match the expected schema."""

    def __init__(self, path, message):
        self.path = path
        super().__init__(f"invalid product-facts schema in {path}: {message}")


# ---------------------------------------------------------------- primitives
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(obj):
    """Stable canonical serialization — sorted keys, fixed separators, no ASCII escaping."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2, separators=(",", ": "))


_BLANKS = ("", "none", "null", "nan", "n/a", "na", "-", "—", "unknown", "tbd", "?")


def _is_blank(value):
    return value is None or (isinstance(value, str) and value.strip().lower() in _BLANKS)


def _normalize_list_value(value):
    """A list field -> list. A comma/semicolon/newline string splits safely; blanks drop out."""
    if isinstance(value, list):
        items = [str(x).strip() for x in value if not _is_blank(x)]
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        items = [str(value)]
    else:
        items = [p.strip() for p in re.split(r"[,;\n]+", str(value)) if p.strip()]
    return [x for x in items if not _is_blank(x)]


# ---------------------------------------------------------------- normalization
def _empty_fact(field, source_default):
    return {"field": field, "value": None, "state": UNKNOWN, "publishable": False,
            "source": None, "owner_status": None, "verification_status": None,
            "notes": None, "updated_at": None, "warnings": []}


def _normalize_fact(field, raw, source_default):
    """Turn one raw field entry into a typed, null-safe fact record."""
    fact = _empty_fact(field, source_default)
    is_list = field in LIST_FIELDS

    # cases 5 & 6: absent or explicitly null -> UNKNOWN
    if raw is None:
        return fact

    if isinstance(raw, dict):
        value = raw.get("value")
        raw_status = raw.get("status") or raw.get("state") or raw.get("verification_status")
        fact["source"] = raw.get("source") or source_default
        fact["owner_status"] = raw.get("owner_status")
        fact["verification_status"] = raw_status
        fact["notes"] = raw.get("notes")
        fact["updated_at"] = raw.get("updated_at")
    else:
        value = raw
        raw_status = None
        fact["source"] = source_default

    if _is_blank(value) if not is_list else (not _normalize_list_value(value)):
        # present but empty -> still UNKNOWN (case 6 for lists / blanks)
        return fact

    norm_value = _normalize_list_value(value) if is_list else str(value).strip()
    fact["value"] = norm_value

    state = _STATUS_ALIASES.get(str(raw_status).strip().lower()) if raw_status else None
    if raw_status and state is None:
        fact["warnings"].append(f"unrecognized status {raw_status!r} -> treated as OWNER_REVIEW_REQUIRED")
        state = OWNER_REVIEW_REQUIRED
    if state is None:
        # case 7 vs 8: a value with no explicit verification is NOT auto-trusted.
        state = OWNER_REVIEW_REQUIRED
    if state == UNKNOWN:
        fact["value"] = None
        fact["publishable"] = False
        fact["state"] = UNKNOWN
        return fact

    fact["state"] = state
    fact["publishable"] = state in PUBLISHABLE_STATES
    return fact


def _extract_facts_container(doc, path):
    """Locate the field map: an explicit product/facts/product_facts block, else top-level fields."""
    for key in ("product", "facts", "product_facts"):
        if key in doc:
            block = doc[key]
            if not isinstance(block, dict):
                raise InvalidProductFactsSchemaError(path, f"'{key}' must be an object")
            return block
    # fall back to top-level: keep only recognized fact keys, ignore metadata keys
    return {k: v for k, v in doc.items() if k in KNOWN_FIELDS}


# ---------------------------------------------------------------- result
class NormalizedProductFacts:
    """The canonical, deterministic view of a project's product facts."""

    def __init__(self, source_file, source_sha256, facts, warnings, errors):
        self.schema_version = NORMALIZED_SCHEMA_VERSION
        self.source_file = source_file
        self.source_sha256 = source_sha256
        self.facts = facts                       # {field: fact_record}
        self.warnings = warnings
        self.errors = errors

    # -- views ------------------------------------------------------
    def get(self, field):
        return self.facts.get(field) or _empty_fact(field, None)

    def state(self, field):
        return self.get(field)["state"]

    def publishable_value(self, field):
        """The value only if it is safe to publish (VERIFIED / OWNER_CONFIRMED); else None."""
        f = self.get(field)
        return f["value"] if f["publishable"] else None

    def _fields_in_state(self, *states):
        return sorted(f for f, rec in self.facts.items() if rec["state"] in states)

    @property
    def owner_fact_required(self):
        """Facts that are simply unknown — an owner must provide them before they can be used."""
        return self._fields_in_state(UNKNOWN)

    @property
    def verified_fact_count(self):
        return len(self._fields_in_state(VERIFIED, OWNER_CONFIRMED))

    @property
    def unknown_fact_count(self):
        return len(self._fields_in_state(UNKNOWN))

    @property
    def owner_review_count(self):
        return len(self._fields_in_state(OWNER_REVIEW_REQUIRED))

    @property
    def blocked_fact_count(self):
        return len(self._fields_in_state(BLOCKED))

    # -- serialization ----------------------------------------------
    def canonical_content(self):
        """Everything except approved volatile metadata — identical input yields identical content."""
        return {
            "schema_version": self.schema_version,
            "source_file": self.source_file,
            "source_sha256": self.source_sha256,
            "facts": self.facts,
            "verified_fact_count": self.verified_fact_count,
            "unknown_fact_count": self.unknown_fact_count,
            "owner_review_count": self.owner_review_count,
            "blocked_fact_count": self.blocked_fact_count,
            "owner_fact_required": self.owner_fact_required,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }

    def content_sha256(self):
        return hashlib.sha256(canonical_json(self.canonical_content()).encode("utf-8")).hexdigest()

    def to_dict(self, generated_at=None):
        doc = self.canonical_content()
        doc["content_sha256"] = self.content_sha256()
        if generated_at:
            doc["generated_at"] = generated_at    # the only volatile field; excluded from the hash
        return doc


# ---------------------------------------------------------------- loading
def load_product_facts(path=None, folder=None, filename=DEFAULT_FACTS_FILENAME, required=False):
    """Load and normalize product facts.

    Provide either `path` (a file) or `folder` (+ optional `filename`). When the file is absent:
      required=True  -> MissingRequiredProductFactsError
      required=False -> an all-UNKNOWN result with an 'optional product-facts file absent' warning.
    """
    if path is None:
        if folder is None:
            raise ProductFactError("load_product_facts requires either path or folder")
        path = os.path.join(folder, filename)

    if not os.path.exists(path):
        if required:
            raise MissingRequiredProductFactsError(path)
        facts = {f: _empty_fact(f, None) for f in KNOWN_FIELDS}
        return NormalizedProductFacts(source_file=os.path.basename(path), source_sha256=None,
                                      facts=facts,
                                      warnings=[f"optional product-facts file absent: {os.path.basename(path)} "
                                                f"— all facts are UNKNOWN"], errors=[])

    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except json.JSONDecodeError as e:
        raise MalformedProductFactsError(path, e.lineno, e.colno, e.msg) from e
    except OSError as e:
        raise ProductFactError(f"cannot read product-facts file {path}: {e}") from e

    if not isinstance(doc, dict):
        raise InvalidProductFactsSchemaError(path, "top-level JSON must be an object")

    container = _extract_facts_container(doc, path)
    if not isinstance(container, dict):
        raise InvalidProductFactsSchemaError(path, "product facts must be an object of field -> value")

    source_default = doc.get("source")
    warnings, errors = [], []

    unknown_keys = [k for k in container if k not in KNOWN_FIELDS]
    if unknown_keys:
        warnings.append(f"ignored unrecognized product-fact field(s): {sorted(unknown_keys)}")

    facts = {}
    for fld in KNOWN_FIELDS:
        rec = _normalize_fact(fld, container.get(fld), source_default)
        warnings.extend(f"{fld}: {w}" for w in rec["warnings"])
        facts[fld] = rec

    return NormalizedProductFacts(source_file=os.path.basename(path), source_sha256=sha256_file(path),
                                  facts=facts, warnings=warnings, errors=errors)


def write_normalized_product_facts(folder, facts=None, generated_at=None, outdir=None,
                                   filename=DEFAULT_FACTS_FILENAME):
    """Write the canonical NORMALIZED-PRODUCT-FACTS.json for a folder. -> (path, NormalizedProductFacts)."""
    facts = facts or load_product_facts(folder=folder, filename=filename)
    stamp = generated_at or datetime.now(timezone.utc).isoformat()
    path = os.path.join(outdir or folder, NORMALIZED_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        f.write(canonical_json(facts.to_dict(generated_at=stamp)))
    return path, facts


def main():
    ap = argparse.ArgumentParser(description="Normalize a project's product-facts file")
    ap.add_argument("folder")
    ap.add_argument("--filename", default=DEFAULT_FACTS_FILENAME)
    ap.add_argument("--required", action="store_true")
    ap.add_argument("--write", action="store_true", help=f"write {NORMALIZED_FILENAME}")
    a = ap.parse_args()
    try:
        facts = load_product_facts(folder=a.folder, filename=a.filename, required=a.required)
    except ProductFactError as e:
        print(f"FAILED: {e}")
        sys.exit(2)
    print(f"source   {facts.source_file}  sha256 {facts.source_sha256}")
    print(f"verified {facts.verified_fact_count} · owner-review {facts.owner_review_count} · "
          f"unknown {facts.unknown_fact_count} · blocked {facts.blocked_fact_count}")
    print(f"content  {facts.content_sha256()}")
    if facts.owner_fact_required:
        print(f"OWNER_FACT_REQUIRED ({len(facts.owner_fact_required)}): {', '.join(facts.owner_fact_required)}")
    if a.write:
        path, _ = write_normalized_product_facts(a.folder, facts)
        print(f"wrote    {path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
