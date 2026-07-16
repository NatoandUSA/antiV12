#!/usr/bin/env python3
"""
listing.keyword_source_adapter — the ONE authoritative keyword source for every downstream consumer.

Before this module the listing generator read KEYWORD-INTELLIGENCE.json (ACT-001) while the dashboard
independently read master-keywords.xlsx "Top Picks" (ACT-002), so the clean lean keyword engine could not
reliably control either, and the two could show different keyword truths. Malformed authoritative JSON was
swallowed into None and silently became "no data" (ACT-003).

Everything downstream now loads through load_keyword_source() and reports the same source SHA-256.

Source priority (first present wins):
  1. MASTER-KEYWORDS.json        -> PRODUCTION
  2. MASTER-KEYWORDS-LEAN.json   -> LEAN
  3. KEYWORD-INTELLIGENCE.json   -> LEGACY_UNSAFE, only when allow_legacy_unsafe=True

A malformed authoritative source is a hard error carrying path, line, column and the parser message.
It NEVER falls back to a lower-priority source — silently degrading to legacy data is the exact failure
this module exists to prevent.

Eligibility:
  * REJECTED is never eligible, in any mode.
  * A blocking risk flag is never eligible.
  * REVIEW / OUTLIER / RESIDUE need an explicitly approved owner_status to enter automatic allocation,
    in EVERY mode including LEGACY_UNSAFE.
  * allow_legacy_unsafe=True authorizes READING the legacy source. It is not record-level approval: a
    legacy record that is REVIEW, untiered or of an unknown tier normalizes to REVIEW and stays
    ineligible until an owner approves that record. Legacy A/B/C map to TIER_A/B/C and may allocate.
    Every legacy record keeps an UNVERIFIED_SOURCE warning.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

NORMALIZED_SCHEMA_VERSION = "1.0.0"
NORMALIZED_FILENAME = "NORMALIZED-KEYWORD-SOURCE.json"

SCHEMA_PRODUCTION = "MASTER_KEYWORDS_PRODUCTION"
SCHEMA_LEAN = "MASTER_KEYWORDS_LEAN"
SCHEMA_LEGACY = "KEYWORD_INTELLIGENCE_LEGACY"

MODE_PRODUCTION = "PRODUCTION"
MODE_LEAN = "LEAN"
MODE_LEGACY_UNSAFE = "LEGACY_UNSAFE"

TIER_A, TIER_B, TIER_C = "TIER_A", "TIER_B", "TIER_C"
OUTLIER, RESIDUE, REVIEW, REJECTED = "OUTLIER", "RESIDUE", "REVIEW", "REJECTED"

LEAN_TIER_MAP = {"A_CORE": TIER_A, "B_SUPPORTING": TIER_B, "REVIEW": REVIEW, "REJECTED": REJECTED}
PRODUCTION_TIER_MAP = {"TIER_A_EXACT_MONEY": TIER_A, "TIER_B_CORE_NICHE": TIER_B,
                       "TIER_C_SUPPORTING_LONG_TAIL": TIER_C, "OUTLIER_OPPORTUNITY": OUTLIER,
                       "RESIDUE_RELEVANT": RESIDUE, "REJECTED_NOISE": REJECTED}
# Legacy files are hand-made and inconsistent, so the legacy map is matched case-insensitively.
LEGACY_TIER_MAP = {"A": TIER_A, "B": TIER_B, "C": TIER_C, "REVIEW": REVIEW, "REJECTED": REJECTED}
LEGACY_UNKNOWN_TIER = REVIEW

TIER_STRENGTH = {TIER_A: 6, TIER_B: 5, TIER_C: 4, OUTLIER: 3, RESIDUE: 2, REVIEW: 1, REJECTED: 0}
# Tiers that allocate on their own; every other tier needs an approved owner_status (or never allocates).
ALLOCATABLE_TIERS = (TIER_A, TIER_B, TIER_C)
OWNER_APPROVAL_TIERS = (REVIEW, OUTLIER, RESIDUE)
APPROVED_OWNER_STATUS = ("approved", "owner_approved", "approved_by_owner", "owner_confirmed")

BLOCKING_RISKS = ("TRADEMARK", "BRAND_TERM", "IP_RISK", "WRONG_AUDIENCE", "WRONG_PRODUCT",
                  "WRONG_OCCASION", "EXPLICIT_CONFLICT", "MALFORMED", "DATA_CONTAMINATION")
# The lean engine emits namespaced lowercase flags ("wrong_audience:graduation"); map the namespace
# onto the canonical blocking vocabulary so both spellings block.
RISK_NAMESPACE_MAP = {
    "trademark": "TRADEMARK", "brand": "BRAND_TERM", "brand_term": "BRAND_TERM",
    "brand_contamination": "BRAND_TERM", "ip": "IP_RISK", "ip_risk": "IP_RISK",
    "wrong_audience": "WRONG_AUDIENCE", "wrong_product": "WRONG_PRODUCT",
    "wrong_product_family": "WRONG_PRODUCT", "wrong_occasion": "WRONG_OCCASION",
    "wrong_occasion_or_theme": "WRONG_OCCASION", "wrong_theme": "WRONG_OCCASION",
    "explicit_conflict": "EXPLICIT_CONFLICT", "malformed": "MALFORMED",
    "data_contamination": "DATA_CONTAMINATION",
}

WARN_LEGACY_UNVERIFIED = "UNVERIFIED_SOURCE"

# (filename, schema, mode) in priority order.
SOURCE_PRIORITY = (
    ("MASTER-KEYWORDS.json", SCHEMA_PRODUCTION, MODE_PRODUCTION),
    ("MASTER-KEYWORDS-LEAN.json", SCHEMA_LEAN, MODE_LEAN),
    ("KEYWORD-INTELLIGENCE.json", SCHEMA_LEGACY, MODE_LEGACY_UNSAFE),
)
_SCHEMA_RANK = {SCHEMA_PRODUCTION: 0, SCHEMA_LEAN: 1, SCHEMA_LEGACY: 2}

_DECLARED_ALIASES = {
    "master_keywords_production": SCHEMA_PRODUCTION, "production": SCHEMA_PRODUCTION,
    "master_keywords": SCHEMA_PRODUCTION,
    "master_keywords_lean": SCHEMA_LEAN, "lean": SCHEMA_LEAN,
    "keyword_intelligence_legacy": SCHEMA_LEGACY, "legacy": SCHEMA_LEGACY,
    "keyword_intelligence": SCHEMA_LEGACY,
}

_FIELD_ALIASES = {
    "search_volume": ("search_volume", "sv", "search volume"),
    "competitor_coverage": ("simple_competitor_coverage_pct", "competitor_coverage_pct",
                            "competitor_coverage"),
    "top_20_coverage": ("top_20_coverage_pct", "top_20_coverage"),
    "top_50_coverage": ("top_50_coverage_pct", "top_50_coverage"),
    "top_100_coverage": ("top_100_coverage_pct", "top_100_coverage"),
    "best_rank": ("best_rank",),
    "median_rank": ("median_rank",),
    "observation_count": ("observation_count",),
}
_EVIDENCE_FIELDS = ("search_volume", "competitor_coverage", "top_20_coverage", "top_50_coverage",
                    "top_100_coverage", "best_rank", "median_rank")
_CONFIDENCE_STRENGTH = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


# ---------------------------------------------------------------- errors
class KeywordSourceError(Exception):
    """Base class for every keyword-source failure."""


class MalformedKeywordSourceError(KeywordSourceError):
    """An authoritative source exists but cannot be parsed. Never falls back — reports where it broke."""

    def __init__(self, path, line, column, parser_message):
        self.path, self.line, self.column, self.parser_message = path, line, column, parser_message
        super().__init__(f"malformed keyword source: {path} (line {line}, column {column}): "
                         f"{parser_message}. Refusing to fall back to a lower-priority source — fix the "
                         f"file or pass allow_legacy_unsafe=True deliberately.")


class SchemaConflictError(KeywordSourceError):
    """The declared, observed and expected schemas do not agree — refuse rather than guess."""

    def __init__(self, path, message):
        self.path = path
        super().__init__(f"incompatible keyword schema in {path}: {message}")


class NoKeywordSourceError(KeywordSourceError):
    """No usable source.

    blocked_legacy_files distinguishes "nothing researched yet" (empty) from "legacy data exists but
    was not explicitly enabled" — the caller must be able to say which, instead of showing an empty
    keyword list for both.
    """

    def __init__(self, folder, message, blocked_legacy_files=()):
        self.folder = folder
        self.blocked_legacy_files = list(blocked_legacy_files)
        self.legacy_blocked = bool(blocked_legacy_files)
        super().__init__(message)


# ---------------------------------------------------------------- primitives
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_phrase(raw):
    """NFKC, trim, collapse internal whitespace. Preserves the exact display phrase (and its case)."""
    s = unicodedata.normalize("NFKC", "" if raw is None else str(raw))
    return re.sub(r"\s+", " ", s).strip()


def comparison_phrase(display):
    """Lowercase comparison form of a display phrase."""
    return normalize_phrase(display).lower()


_BLANKS = ("", "none", "null", "nan", "n/a", "na", "-", "—")


def parse_number(value, field="value"):
    """Tolerant numeric parse -> (number|None, warning|None). Never raises.

    Handles ints, floats, "1,200", "45%", blanks, None, NaN and invalid text.
    """
    if value is None or isinstance(value, bool):
        return (None, None) if value is None else (None, f"{field}_not_numeric")
    if isinstance(value, (int, float)):
        v = float(value)
        return (None, f"{field}_not_a_number") if (math.isnan(v) or math.isinf(v)) else (v, None)
    s = str(value).strip()
    if s.lower() in _BLANKS:
        return None, None
    s = s[:-1].strip() if s.endswith("%") else s
    s = s.replace(",", "").replace("$", "").strip()
    try:
        v = float(s)
    except ValueError:
        return None, f"{field}_unparsable:{str(value)[:40]}"
    return (None, f"{field}_not_a_number") if (math.isnan(v) or math.isinf(v)) else (v, None)


def canonical_risk_flags(flags):
    """Map source risk flags (any spelling) onto the canonical blocking vocabulary."""
    out = set()
    for f in flags or []:
        token = str(f).strip()
        if not token:
            continue
        upper = token.upper()
        if upper in BLOCKING_RISKS:
            out.add(upper)
            continue
        namespace = re.split(r"[:=]", token, 1)[0].strip().lower()
        mapped = RISK_NAMESPACE_MAP.get(namespace)
        if mapped:
            out.add(mapped)
    return sorted(out)


def _read_json(path):
    """Read JSON or raise MalformedKeywordSourceError with path, line, column and parser message."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise KeywordSourceError(f"cannot read keyword source {path}: {e}") from e
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise MalformedKeywordSourceError(path, e.lineno, e.colno, e.msg) from e


def _first(record, names):
    for n in names:
        if n in record and record[n] is not None:
            return record[n]
    return None


def _as_list(v):
    if v is None:
        return []
    return [v] if isinstance(v, str) else list(v)


# ---------------------------------------------------------------- schema detection
def _declared_schema(doc):
    containers = [doc, doc.get("run_metadata") if isinstance(doc.get("run_metadata"), dict) else {}]
    for c in containers:
        for key in ("source_schema", "schema", "keyword_schema"):
            v = c.get(key)
            if isinstance(v, str) and v.strip():
                alias = _DECLARED_ALIASES.get(re.sub(r"[^a-z0-9]+", "_", v.strip().lower()).strip("_"))
                if alias:
                    return alias
    return None


def observed_tier_vocabulary(doc):
    """The COMPLETE set of tier tokens across every record — never a single-record guess."""
    tiers = set()
    for r in doc.get("keywords") or []:
        if isinstance(r, dict) and r.get("tier") is not None:
            t = str(r["tier"]).strip()
            if t:
                tiers.add(t)
    return tiers


def _structural_schema(doc):
    qs = doc.get("quality_summary")
    if isinstance(qs, dict) and isinstance(qs.get("counts"), dict) and qs["counts"]:
        counts = set(qs["counts"])
        if counts <= set(LEAN_TIER_MAP):
            return SCHEMA_LEAN
        if counts <= set(PRODUCTION_TIER_MAP):
            return SCHEMA_PRODUCTION
    if isinstance(doc.get("keywords"), list) and any(k in doc for k in ("downstream_policy", "target_profile")):
        return SCHEMA_LEAN
    if isinstance(doc.get("top"), list) or isinstance(doc.get("all"), list):
        return SCHEMA_LEGACY
    return None


def detect_schema(doc, path="", expected=None):
    """Detect the source schema from declared metadata, keyword fields, the complete tier vocabulary
    and compatibility validation — in that order. Raises on incompatible/ambiguous input."""
    if not isinstance(doc, dict):
        raise SchemaConflictError(path, "top-level JSON must be an object")

    declared = _declared_schema(doc)
    tiers = observed_tier_vocabulary(doc)
    has_authoritative_records = isinstance(doc.get("keywords"), list)
    has_legacy_records = isinstance(doc.get("top"), list) or isinstance(doc.get("all"), list)

    if tiers:
        # LEAN and PRODUCTION vocabularies are disjoint, so a complete-vocabulary subset test is decisive.
        lean_ok, prod_ok = tiers <= set(LEAN_TIER_MAP), tiers <= set(PRODUCTION_TIER_MAP)
        if lean_ok:
            detected = SCHEMA_LEAN
        elif prod_ok:
            detected = SCHEMA_PRODUCTION
        elif declared == SCHEMA_LEGACY or (has_legacy_records and not has_authoritative_records):
            detected = SCHEMA_LEGACY
        else:
            lean_hits = sorted(tiers & set(LEAN_TIER_MAP))
            prod_hits = sorted(tiers & set(PRODUCTION_TIER_MAP))
            unknown = sorted(tiers - set(LEAN_TIER_MAP) - set(PRODUCTION_TIER_MAP))
            raise SchemaConflictError(
                path, f"mixed or unknown tier vocabulary {sorted(tiers)} "
                      f"(lean tokens={lean_hits}, production tokens={prod_hits}, unknown={unknown}); "
                      f"a source must use exactly one tier vocabulary")
    else:
        detected = declared or _structural_schema(doc)

    if detected is None:
        raise SchemaConflictError(path, "cannot identify schema: no declared metadata, no tier "
                                        "vocabulary and no recognisable keyword records")

    # 4. compatibility validation
    if declared and declared != detected:
        raise SchemaConflictError(path, f"declared schema {declared} conflicts with detected {detected}")
    if expected and expected != detected:
        raise SchemaConflictError(path, f"file occupies the {expected} slot but its content is {detected}")
    if detected in (SCHEMA_LEAN, SCHEMA_PRODUCTION):
        if not has_authoritative_records:
            raise SchemaConflictError(path, f"{detected} source must contain a 'keywords' list")
        for r in doc["keywords"]:
            if not isinstance(r, dict):
                raise SchemaConflictError(path, f"{detected} keyword records must be objects")
            if _first(r, ("keyword_exact", "keyword_normalized", "keyword")) is None:
                raise SchemaConflictError(path, f"{detected} keyword record is missing a keyword field")
    elif not has_legacy_records:
        raise SchemaConflictError(path, "legacy source must contain a 'top' or 'all' list")
    return detected


def map_tier(raw_tier, schema):
    """Map a source tier onto the normalized vocabulary. Unknown legacy tiers become REVIEW."""
    token = "" if raw_tier is None else str(raw_tier).strip()
    if schema == SCHEMA_LEAN:
        if token not in LEAN_TIER_MAP:
            raise SchemaConflictError("", f"unknown lean tier {token!r}")
        return LEAN_TIER_MAP[token]
    if schema == SCHEMA_PRODUCTION:
        if token not in PRODUCTION_TIER_MAP:
            raise SchemaConflictError("", f"unknown production tier {token!r}")
        return PRODUCTION_TIER_MAP[token]
    return LEGACY_TIER_MAP.get(token.upper(), LEGACY_UNKNOWN_TIER)


# ---------------------------------------------------------------- normalization
def _owner_approved(owner_status):
    return str(owner_status or "").strip().lower() in APPROVED_OWNER_STATUS


def _normalize_record(raw, schema, mode, source_file, run_id, order):
    warnings = []
    display = normalize_phrase(_first(raw, ("keyword_exact", "keyword", "keyword_normalized")))
    source_norm = raw.get("keyword_normalized")
    normalized = comparison_phrase(source_norm) if source_norm else comparison_phrase(display)

    rec = {
        "keyword_exact": display,
        "keyword_normalized": normalized,
        "source_tier": raw.get("tier"),
        "normalized_tier": map_tier(raw.get("tier"), schema),
        "source_schema": schema,
        "source_file": source_file,
        "source_run_id": run_id,
        "owner_status": raw.get("owner_status"),
        "owner_approved": _owner_approved(raw.get("owner_status")),
        "reason_codes": _as_list(raw.get("reason_codes")),
        "risk_flags": _as_list(raw.get("risk_flags")),
        "blocking_risks": canonical_risk_flags(raw.get("risk_flags")),
        "batch_support": dict(raw.get("batch_support") or {}),
        "source_files": _as_list(raw.get("source_files")),
        "source_batches": _as_list(raw.get("source_batches")),
        "confidence": raw.get("confidence"),
        "why": raw.get("why"),
        "source_order": order,
        "duplicate_provenance": [],
    }
    for field, aliases in _FIELD_ALIASES.items():
        value, warn = parse_number(_first(raw, aliases), field)
        rec[field] = value
        if warn:
            warnings.append(f"{display}: {warn}")
    if rec["observation_count"] is not None:
        rec["observation_count"] = int(rec["observation_count"])

    if not display:
        rec["blocking_risks"] = sorted(set(rec["blocking_risks"]) | {"MALFORMED"})
        warnings.append(f"record {order}: empty keyword phrase -> MALFORMED")
    if mode == MODE_LEGACY_UNSAFE:
        warnings.append(WARN_LEGACY_UNVERIFIED)
    rec["warnings"] = sorted(set(warnings))
    _evaluate_eligibility(rec)
    return rec


def _evaluate_eligibility(rec):
    """REJECTED never becomes eligible; blocking risks never become eligible; REVIEW/OUTLIER/RESIDUE
    need approved owner status before automatic allocation.

    Mode does not soften this. Authorizing an unverified SOURCE (allow_legacy_unsafe=True) is not
    approval of an ambiguous RECORD, so a legacy REVIEW/untiered/unknown-tier record stays ineligible
    exactly like an authoritative one.
    """
    reasons = []
    if rec["normalized_tier"] == REJECTED:
        reasons.append("REJECTED_TIER")
    reasons += [f"BLOCKING_RISK:{r}" for r in rec["blocking_risks"]]
    if rec["normalized_tier"] in OWNER_APPROVAL_TIERS and not rec["owner_approved"]:
        reasons.append("OWNER_APPROVAL_REQUIRED")
    rec["exclusion_reasons"] = sorted(set(reasons))
    rec["eligible"] = not reasons
    return rec


def _evidence_completeness(rec):
    n = sum(1 for f in _EVIDENCE_FIELDS if rec.get(f) is not None)
    return n + len(rec["batch_support"]) + (rec["observation_count"] or 0)


def _rank_strength(rec):
    return -rec["best_rank"] if rec["best_rank"] is not None else -1e9


def _strength_key(rec):
    """Strongest complete safe record wins an exact-normalized duplicate."""
    return (
        1 if rec["eligible"] else 0,                                   # 1. eligible over ineligible
        TIER_STRENGTH.get(rec["normalized_tier"], 0),                  # 2. safer/stronger tier
        1 if rec["owner_approved"] else 0,                             # 3. owner approved over auto
        _evidence_completeness(rec),                                   # 4. more complete evidence
        rec["competitor_coverage"] if rec["competitor_coverage"] is not None else -1.0,  # 5.
        _rank_strength(rec),                                           # 6. organic rank evidence
        _CONFIDENCE_STRENGTH.get(str(rec["confidence"] or "").upper(), 0),               # 7.
        1 if rec["search_volume"] is not None else 0,                  # 8. valid search volume
        -_SCHEMA_RANK.get(rec["source_schema"], 9),                    # 9. authoritative source order
    )


def _provenance(rec, selected):
    return {"keyword_exact": rec["keyword_exact"], "source_file": rec["source_file"],
            "source_schema": rec["source_schema"], "source_run_id": rec["source_run_id"],
            "source_tier": rec["source_tier"], "normalized_tier": rec["normalized_tier"],
            "owner_status": rec["owner_status"], "search_volume": rec["search_volume"],
            "competitor_coverage": rec["competitor_coverage"], "best_rank": rec["best_rank"],
            "eligible": rec["eligible"], "source_order": rec["source_order"], "selected": selected}


def resolve_duplicates(records):
    """Collapse exact normalized duplicates to the strongest complete safe record.

    The winning record is selected whole — a weaker record is never patched with a stronger tier or
    volume. Every duplicate source record is preserved in duplicate_provenance.
    """
    groups = {}
    for r in records:
        groups.setdefault(r["keyword_normalized"], []).append(r)
    out = []
    for group in groups.values():
        if len(group) == 1:
            out.append(group[0])
            continue
        winner = max(group, key=_strength_key)
        winner["duplicate_provenance"] = [_provenance(r, r is winner) for r in
                                          sorted(group, key=lambda r: r["source_order"])]
        winner["warnings"] = sorted(set(winner["warnings"]) | {f"resolved_{len(group)}_duplicate_records"})
        out.append(winner)
    return out


def _sort_key(schema):
    if schema == SCHEMA_LEGACY:
        # The legacy engine already ranked its own list; preserve that order.
        return lambda r: (r["source_order"],)
    return lambda r: (-TIER_STRENGTH.get(r["normalized_tier"], 0),
                      -(r["competitor_coverage"] if r["competitor_coverage"] is not None else -1.0),
                      r["best_rank"] if r["best_rank"] is not None else 1e9,
                      -(r["search_volume"] if r["search_volume"] is not None else -1.0),
                      r["keyword_normalized"])


# ---------------------------------------------------------------- result
class NormalizedKeywordSource:
    """The canonical normalized view of one keyword source."""

    def __init__(self, source_file, source_schema, source_run_id, source_mode, source_sha256,
                 keywords, warnings):
        self.source_file = source_file
        self.source_schema = source_schema
        self.source_run_id = source_run_id
        self.source_mode = source_mode
        self.source_sha256 = source_sha256
        self.normalized_schema_version = NORMALIZED_SCHEMA_VERSION
        self.keywords = keywords
        self.warnings = warnings
        self.legacy_unsafe = source_mode == MODE_LEGACY_UNSAFE

    # -- views ------------------------------------------------------
    def eligible_keywords(self, tiers=None):
        """Records safe for automatic listing allocation, strongest first.

        Eligibility alone decides this, in every mode: TIER_A/B/C allocate; REJECTED and blocking risks
        never do; REVIEW/OUTLIER/RESIDUE allocate only once an owner approves the record. Pass `tiers`
        to narrow further.
        """
        recs = [r for r in self.keywords if r["eligible"]]
        return recs if tiers is None else [r for r in recs if r["normalized_tier"] in tiers]

    def eligible_phrases(self, tiers=None):
        return [r["keyword_exact"] for r in self.eligible_keywords(tiers)]

    def by_tier(self, tier):
        return [r for r in self.keywords if r["normalized_tier"] == tier]

    @property
    def tier_counts(self):
        counts = {t: 0 for t in TIER_STRENGTH}
        for r in self.keywords:
            counts[r["normalized_tier"]] = counts.get(r["normalized_tier"], 0) + 1
        return counts

    @property
    def eligibility_counts(self):
        eligible = sum(1 for r in self.keywords if r["eligible"])
        return {"eligible": eligible, "ineligible": len(self.keywords) - eligible,
                "total": len(self.keywords)}

    @property
    def exclusions(self):
        """Excluded keywords grouped by reason."""
        grouped = {}
        for r in self.keywords:
            for reason in r["exclusion_reasons"]:
                grouped.setdefault(reason, []).append(r["keyword_exact"])
        return {k: {"count": len(v), "keywords": sorted(v)} for k, v in sorted(grouped.items())}

    def listing_metadata(self):
        """The source-identity block every consumer stamps onto its output."""
        return {"keyword_source_file": self.source_file,
                "keyword_source_schema": self.source_schema,
                "keyword_source_run_id": self.source_run_id,
                "keyword_source_mode": self.source_mode,
                "keyword_source_sha256": self.source_sha256,
                "legacy_unsafe": self.legacy_unsafe,
                "keyword_source_warnings": list(self.warnings)}

    # -- serialization ----------------------------------------------
    def canonical_content(self):
        """Everything except approved volatile fields — identical input yields identical content."""
        return {"source_file": self.source_file, "source_schema": self.source_schema,
                "source_run_id": self.source_run_id, "source_mode": self.source_mode,
                "source_sha256": self.source_sha256, "legacy_unsafe": self.legacy_unsafe,
                "normalized_schema_version": self.normalized_schema_version,
                "warnings": list(self.warnings), "tier_counts": self.tier_counts,
                "eligibility_counts": self.eligibility_counts, "exclusions": self.exclusions,
                "keywords": self.keywords}

    def content_sha256(self):
        return hashlib.sha256(canonical_json(self.canonical_content()).encode("utf-8")).hexdigest()

    def to_dict(self, generated_at=None):
        doc = self.canonical_content()
        doc["content_sha256"] = self.content_sha256()
        if generated_at:
            doc["generated_at"] = generated_at          # the only volatile field; excluded from the hash
        return doc


def canonical_json(obj):
    """Stable canonical serialization — sorted keys, fixed separators, no ASCII escaping."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2, separators=(",", ": "))


# ---------------------------------------------------------------- loading
def find_source(folder, allow_legacy_unsafe=False):
    """-> (path, filename, schema, mode) for the highest-priority available source."""
    blocked = []
    for filename, schema, mode in SOURCE_PRIORITY:
        path = os.path.join(folder, filename)
        if not os.path.exists(path):
            continue
        if schema == SCHEMA_LEGACY and not allow_legacy_unsafe:
            blocked.append(filename)
            continue
        return path, filename, schema, mode
    if blocked:
        raise NoKeywordSourceError(
            folder,
            f"legacy keyword data ({', '.join(blocked)}) is the only source in {folder} and legacy mode "
            f"is not enabled. Legacy data is UNVERIFIED_SOURCE and is never used by default. To proceed, "
            f"either build an approved authoritative source (MASTER-KEYWORDS-LEAN.json via "
            f"research/master_keyword_builder.py, or MASTER-KEYWORDS.json) — recommended — or opt in "
            f"explicitly with allow_legacy_unsafe=True, which authorizes reading the legacy file but "
            f"still will not allocate its REVIEW/untiered/rejected records.",
            blocked_legacy_files=blocked)
    raise NoKeywordSourceError(
        folder, f"no keyword source in {folder}: expected one of "
                f"{[f for f, _, _ in SOURCE_PRIORITY]}. Run the keyword research step first.")


def load_keyword_source(folder, allow_legacy_unsafe=False):
    """Load the authoritative keyword source for a project folder.

    Priority: MASTER-KEYWORDS.json > MASTER-KEYWORDS-LEAN.json > KEYWORD-INTELLIGENCE.json (opt-in only).
    A malformed authoritative source raises MalformedKeywordSourceError and NEVER falls back.
    """
    path, filename, expected_schema, mode = find_source(folder, allow_legacy_unsafe)
    doc = _read_json(path)
    schema = detect_schema(doc, path, expected=expected_schema)

    run_meta = doc.get("run_metadata") if isinstance(doc.get("run_metadata"), dict) else {}
    run_id = run_meta.get("run_id") or doc.get("run_id")

    warnings = []
    if mode == MODE_LEGACY_UNSAFE:
        warnings += [WARN_LEGACY_UNVERIFIED,
                     "LEGACY_UNSAFE: KEYWORD-INTELLIGENCE.json is not an approved authoritative source; "
                     "its records are unverified and were enabled explicitly."]

    if schema == SCHEMA_LEGACY:
        raw_records = doc.get("top") or doc.get("all") or []
    else:
        raw_records = doc["keywords"]

    records = [_normalize_record(r, schema, mode, filename, run_id, i)
               for i, r in enumerate(raw_records)]
    if mode == MODE_LEGACY_UNSAFE:
        held = sum(1 for r in records if "OWNER_APPROVAL_REQUIRED" in r["exclusion_reasons"])
        if held:
            warnings.append(f"LEGACY_UNSAFE: {held} legacy record(s) are REVIEW/untiered and stay "
                            f"ineligible — enabling legacy mode authorizes reading the source, not "
                            f"approving its ambiguous records")
    records = resolve_duplicates(records)
    records.sort(key=_sort_key(schema))
    for r in records:
        warnings += [w for w in r["warnings"] if w != WARN_LEGACY_UNVERIFIED]

    src = NormalizedKeywordSource(source_file=filename, source_schema=schema, source_run_id=run_id,
                                  source_mode=mode, source_sha256=sha256_file(path),
                                  keywords=records, warnings=sorted(set(warnings)))
    return src


def write_normalized_source(folder, source=None, outdir=None, allow_legacy_unsafe=False,
                            generated_at=None):
    """Write the canonical NORMALIZED-KEYWORD-SOURCE.json. -> (path, source)."""
    src = source or load_keyword_source(folder, allow_legacy_unsafe)
    stamp = generated_at or datetime.now(timezone.utc).isoformat()
    path = os.path.join(outdir or folder, NORMALIZED_FILENAME)
    with open(path, "w", encoding="utf-8") as f:
        f.write(canonical_json(src.to_dict(generated_at=stamp)))
    return path, src


def main():
    ap = argparse.ArgumentParser(description="Normalize a project's authoritative keyword source")
    ap.add_argument("folder")
    ap.add_argument("--allow-legacy-unsafe", action="store_true")
    ap.add_argument("--write", action="store_true", help=f"write {NORMALIZED_FILENAME}")
    a = ap.parse_args()
    try:
        src = load_keyword_source(a.folder, a.allow_legacy_unsafe)
    except KeywordSourceError as e:
        print(f"FAILED: {e}")
        sys.exit(2)
    print(f"source   {src.source_file}  [{src.source_schema} / {src.source_mode}]")
    print(f"run id   {src.source_run_id}")
    print(f"sha256   {src.source_sha256}")
    print(f"tiers    {src.tier_counts}")
    print(f"eligible {src.eligibility_counts}")
    if src.warnings:
        print(f"warnings {len(src.warnings)}: {src.warnings[0]}")
    if a.write:
        path, _ = write_normalized_source(a.folder, src)
        print(f"wrote    {path}")
    sys.exit(0)


if __name__ == "__main__":
    main()
