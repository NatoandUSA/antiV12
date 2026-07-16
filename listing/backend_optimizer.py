#!/usr/bin/env python3
"""
listing.backend_optimizer — the evidence-aware, byte-safe backend search-term optimizer (ACT-009).

Before this module `listing_generator.build_backend()` flattened keyword words, blindly removed every
token already in the title, and returned a bare string with NO audit — so useful synonyms/long-tails were
lost while redundant or unexplained tokens could enter with no provenance and no reason. This module
replaces that with a deterministic optimizer that:

  * draws candidates ONLY from the authoritative keyword source's ELIGIBLE records (TIER_A/B/C and
    owner-approved REVIEW/OUTLIER/RESIDUE). REJECTED, ineligible, and every blocking-risk class
    (trademark, brand, IP, wrong audience/product/occasion, explicit conflict, malformed, contaminated,
    unapproved legacy REVIEW) are NEVER selected — they are recorded as excluded with a reason;
  * counts UTF-8 bytes and never exceeds the category-policy backend byte ceiling;
  * never cuts a word or token — a token is included whole or not at all;
  * decides INCREMENTAL searchable coverage instead of blindly repeating or removing visible-field words:
    a token already fully covered by the title/bullets/description/item-highlights is dropped, but a token
    that adds a distinct synonym, abbreviation, or long-tail component is kept;
  * records provenance + an inclusion reason for every included token and an exclusion reason for every
    excluded candidate, plus a per-risk exclusion summary and the source hashes;
  * is deterministic: identical immutable inputs produce an identical backend string and identical audit
    (there is no volatile field in the audit — the source SHA-256 is stable).

The category-policy registry is the ONE source of the byte ceiling (`policy.backend_byte_ceiling`). 249 is
only ever used as an explicit documented fallback when no policy is supplied.

Public API:
  optimize_backend(keyword_source, policy=None, title="", bullets=(), description="",
                   item_highlights="", claim_evidence=None, brand_terms=(), prohibited_terms=(),
                   max_tokens_per_root=None) -> BackendOptimization
  build_backend_payload(...) -> {"backend_search_terms_string": str, "audit": {...}}
  write_backend_outputs(folder, optimization, generated_at=None) -> {path: ...}
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import keyword_source_adapter as KSA

BACKEND_SCHEMA_VERSION = "1.0.0"
BACKEND_TERMS_FILENAME = "BACKEND-SEARCH-TERMS.json"
BACKEND_AUDIT_FILENAME = "BACKEND-SEARCH-TERMS-AUDIT.json"

# The ONLY hardcoded ceiling in this module: a documented fallback used when no category policy is passed.
# In every real path the ceiling comes from the shared category_policy_registry (policy.backend_byte_ceiling).
DEFAULT_BACKEND_BYTE_CEILING = 249

# A single character carries no search meaning; drop it (documented). 2+ char tokens — including numeric
# sizes ("3xl") and audience abbreviations ("rn", "cna", "lpn", "np") — are preserved.
MIN_TOKEN_LEN = 2

# Default root/concept saturation: at most this many tokens sharing one concept root may enter the backend,
# so a single duplicated root ("nurse nurses nursing …") cannot consume most of the byte budget.
DEFAULT_MAX_TOKENS_PER_ROOT = 4

# A real authoritative source can carry thousands of ineligible/redundant candidates. The audit stays
# COMPLETE via exact counts (excluded_summary, risk_results counts) while the per-term lists are bounded,
# deterministically ordered samples — so the artifact is auditable without being unbounded. Truncation is
# always flagged and the counts are never truncated.
EXCLUDED_TERMS_CAP = 200
RISK_SAMPLE_CAP = 50

# tier selection priority (eligibility is already guaranteed by the adapter before a record gets here).
_TIER_PRIORITY = {KSA.TIER_A: 6, KSA.TIER_B: 5, KSA.TIER_C: 4, KSA.OUTLIER: 3, KSA.RESIDUE: 2,
                  KSA.REVIEW: 1}
_SCHEMA_RANK = {KSA.SCHEMA_PRODUCTION: 0, KSA.SCHEMA_LEAN: 1, KSA.SCHEMA_LEGACY: 2}

# audience/credential abbreviations that are meaningful backend tokens (documented; never stripped).
_ABBREVIATIONS = frozenset({"rn", "lpn", "cna", "np", "bsn", "icu", "er", "ob", "gyn", "pcu", "picu",
                            "nicu", "cvicu", "med", "surg", "pacu", "ltc", "aprn", "crna"})

# --- inclusion reasons (documented vocabulary) ---
INCL_UNIQUE_SYNONYM = "UNIQUE_SYNONYM"
INCL_APPROVED_ABBREVIATION = "APPROVED_ABBREVIATION"
INCL_UNIQUE_LONG_TAIL = "UNIQUE_LONG_TAIL_COMPONENT"
INCL_HIGH_EVIDENCE = "HIGH_EVIDENCE_SUPPORT"
INCL_LOW_VISIBLE_COVERAGE = "LOW_VISIBLE_COVERAGE"
INCLUSION_REASONS = (INCL_UNIQUE_SYNONYM, INCL_APPROVED_ABBREVIATION, INCL_UNIQUE_LONG_TAIL,
                     INCL_HIGH_EVIDENCE, INCL_LOW_VISIBLE_COVERAGE)

# --- exclusion reasons (documented vocabulary) ---
EXCL_ALREADY_FULLY_COVERED = "ALREADY_FULLY_COVERED"
EXCL_DUPLICATE_TOKEN = "DUPLICATE_TOKEN"
EXCL_BRAND_TERM = "BRAND_TERM"
EXCL_TRADEMARK_OR_IP = "TRADEMARK_OR_IP_RISK"
EXCL_WRONG_AUDIENCE = "WRONG_AUDIENCE"
EXCL_WRONG_PRODUCT = "WRONG_PRODUCT"
EXCL_WRONG_OCCASION = "WRONG_OCCASION"
EXCL_EXPLICIT_CONFLICT = "EXPLICIT_CONFLICT"
EXCL_INELIGIBLE_TIER = "INELIGIBLE_TIER"
EXCL_OWNER_APPROVAL = "OWNER_APPROVAL_REQUIRED"
EXCL_BYTE_LIMIT = "BYTE_LIMIT"
EXCL_ROOT_SATURATED = "ROOT_SATURATED"
EXCL_MALFORMED = "MALFORMED"
EXCL_EMPTY_AFTER_NORMALIZATION = "EMPTY_AFTER_NORMALIZATION"

# maps a canonical adapter blocking-risk onto the optimizer's exclusion vocabulary + a risk_results bucket.
_RISK_TO_EXCLUSION = {
    "TRADEMARK": (EXCL_TRADEMARK_OR_IP, "trademark_or_ip"),
    "IP_RISK": (EXCL_TRADEMARK_OR_IP, "trademark_or_ip"),
    "BRAND_TERM": (EXCL_BRAND_TERM, "brand_ip"),
    "WRONG_AUDIENCE": (EXCL_WRONG_AUDIENCE, "wrong_audience"),
    "WRONG_PRODUCT": (EXCL_WRONG_PRODUCT, "wrong_product"),
    "WRONG_OCCASION": (EXCL_WRONG_OCCASION, "wrong_occasion"),
    "EXPLICIT_CONFLICT": (EXCL_EXPLICIT_CONFLICT, "explicit_conflict"),
    "MALFORMED": (EXCL_MALFORMED, "malformed"),
    "DATA_CONTAMINATION": (EXCL_MALFORMED, "malformed"),
}


# ---------------------------------------------------------------- normalization
def canonical_json(obj):
    """Stable canonical serialization — sorted keys, fixed separators, no ASCII escaping."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2, separators=(",", ": "))


def _normalize(text):
    """NFKC, lowercase, punctuation -> space, collapse whitespace. Preserves alphanumerics (incl. numbers)."""
    s = unicodedata.normalize("NFKC", "" if text is None else str(text)).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokenize(text):
    """Deterministic safe alphanumeric tokens (order preserved, whole words only — never cut)."""
    return _normalize(text).split()


def concept_root(token):
    """A small deterministic stem so plural/variant tokens share a root (for synonym + saturation logic).

    nurses -> nurse, sweatshirts -> sweatshirt, nursing -> nurs. Imperfect by design; it only has to be
    stable and group obvious morphological variants, never to be a real lemmatizer.
    """
    t = token
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 4 and t.endswith("ing"):
        return t[:-3]
    if len(t) > 4 and t.endswith("ed"):
        return t[:-2]
    if len(t) > 4 and t.endswith("es"):
        return t[:-2]
    if len(t) > 3 and t.endswith("s"):
        return t[:-1]
    return t


def _is_abbreviation(token):
    if token in _ABBREVIATIONS:
        return True
    # a short all-consonant alpha token (e.g. "rn") reads as a credential/abbreviation, not a word.
    return token.isalpha() and 2 <= len(token) <= 4 and not any(c in "aeiou" for c in token)


def _evidence_strength(rec):
    n = 0
    for f in ("search_volume", "competitor_coverage", "top_20_coverage", "top_50_coverage",
              "top_100_coverage", "best_rank", "median_rank"):
        if rec.get(f) is not None:
            n += 1
    return n + (rec.get("observation_count") or 0) + len(rec.get("batch_support") or {})


def _has_high_evidence(rec):
    cov = rec.get("competitor_coverage")
    sv = rec.get("search_volume")
    return (cov is not None and cov > 0) or (sv is not None and sv >= 100) or rec.get("best_rank") is not None


# ---------------------------------------------------------------- provenance
def _provenance(rec, source_sha256):
    """Everything downstream needs to trust one backend token: which keyword, tier, risk, evidence, and
    the source file/run/hash it came from."""
    return {
        "source_keyword": rec["keyword_exact"],
        "normalized_keyword": rec["keyword_normalized"],
        "normalized_tier": rec["normalized_tier"],
        "owner_status": rec["owner_status"],
        "owner_approved": rec["owner_approved"],
        "risk_flags": list(rec["risk_flags"]),
        "blocking_risks": list(rec["blocking_risks"]),
        "search_volume": rec["search_volume"],
        "competitor_coverage": rec["competitor_coverage"],
        "top_20_coverage": rec["top_20_coverage"],
        "top_50_coverage": rec["top_50_coverage"],
        "top_100_coverage": rec["top_100_coverage"],
        "best_rank": rec["best_rank"],
        "median_rank": rec["median_rank"],
        "source_file": rec["source_file"],
        "source_run_id": rec["source_run_id"],
        "source_schema": rec["source_schema"],
        "source_sha256": source_sha256,
    }


def _token_entry(rec, source_sha256, token, byte_cost, reason_key, reason_value):
    """A full-provenance entry — used for INCLUDED tokens, where every token must carry full provenance."""
    e = _provenance(rec, source_sha256)
    e.update({"term": token, "unit": "token", "concept_root": concept_root(token),
              "byte_cost": byte_cost, reason_key: reason_value})
    return e


def _excluded_entry(rec, term, unit, byte_cost, reason):
    """A compact excluded-candidate entry: core provenance (source keyword, normalized, tier, owner status)
    + the reason. Full provenance is preserved on INCLUDED terms; a real source drops thousands of tokens,
    so an excluded sample stays lean and auditable — the exact per-reason counts are in excluded_summary."""
    return {"term": term, "unit": unit, "source_keyword": rec["keyword_exact"],
            "normalized_keyword": rec["keyword_normalized"], "normalized_tier": rec["normalized_tier"],
            "owner_status": rec["owner_status"], "risk_flags": list(rec["risk_flags"]),
            "byte_cost": byte_cost, "exclusion_reason": reason}


def _excl_token(rec, token, byte_cost, reason):
    return _excluded_entry(rec, token, "token", byte_cost, reason)


def _excl_keyword(rec, reason):
    return _excluded_entry(rec, rec["keyword_exact"], "keyword", None, reason)


# ---------------------------------------------------------------- result
class BackendOptimization:
    """The deterministic backend optimization result: the string plus the full inclusion/exclusion audit."""

    def __init__(self, backend_search_terms_string, byte_ceiling, included_terms, excluded_terms,
                 excluded_summary, excluded_total, excluded_truncated, visible_field_overlap,
                 incremental_coverage, risk_results, source_hashes, max_tokens_per_root):
        self.backend_search_terms_string = backend_search_terms_string
        self.byte_ceiling = byte_ceiling
        self.bytes_used = len(backend_search_terms_string.encode("utf-8"))
        self.bytes_remaining = byte_ceiling - self.bytes_used
        self.included_terms = included_terms
        self.excluded_terms = excluded_terms                # bounded, deterministically ordered sample
        self.excluded_summary = excluded_summary            # exact count per exclusion reason (complete)
        self.excluded_total = excluded_total                # exact total excluded (complete)
        self.excluded_truncated = excluded_truncated
        self.visible_field_overlap = visible_field_overlap
        self.incremental_coverage = incremental_coverage
        self.risk_results = risk_results
        self.source_hashes = source_hashes
        self.max_tokens_per_root = max_tokens_per_root

    def audit(self):
        return {
            "schema_version": BACKEND_SCHEMA_VERSION,
            "byte_ceiling": self.byte_ceiling,
            "bytes_used": self.bytes_used,
            "bytes_remaining": self.bytes_remaining,
            "max_tokens_per_root": self.max_tokens_per_root,
            "included_count": len(self.included_terms),
            "excluded_count": self.excluded_total,
            "excluded_terms_listed": len(self.excluded_terms),
            "excluded_terms_truncated": self.excluded_truncated,
            "included_terms": self.included_terms,
            "excluded_terms": self.excluded_terms,
            "excluded_summary": self.excluded_summary,
            "visible_field_overlap": self.visible_field_overlap,
            "incremental_coverage": self.incremental_coverage,
            "risk_results": self.risk_results,
            "source_hashes": self.source_hashes,
        }

    def to_dict(self):
        return {"backend_search_terms_string": self.backend_search_terms_string, "audit": self.audit()}

    def content_sha256(self):
        return hashlib.sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- core
def optimize_backend(keyword_source, policy=None, title="", bullets=(), description="",
                     item_highlights="", claim_evidence=None, brand_terms=(), prohibited_terms=(),
                     max_tokens_per_root=None):
    """Build the byte-safe, provenance-audited backend search-terms string from the authoritative source.

    keyword_source : a KSA.NormalizedKeywordSource (the single authoritative keyword source).
    policy         : a category_policy_registry.CategoryPolicy; its backend_byte_ceiling is the ONE ceiling.
    title/bullets/description/item_highlights : the visible fields, used for incremental-coverage decisions.
    claim_evidence : optional ClaimEvidence, used only to add its source hash to the audit (backend terms
                     are keywords, not factual claims — the claim gate governs copy, not search terms).
    brand_terms / prohibited_terms : extra brand/prohibited tokens that must never enter the backend, on top
                     of the adapter's own risk screening.
    """
    ceiling = policy.backend_byte_ceiling if policy is not None else DEFAULT_BACKEND_BYTE_CEILING
    max_per_root = DEFAULT_MAX_TOKENS_PER_ROOT if max_tokens_per_root is None else max_tokens_per_root
    src_sha = keyword_source.source_sha256

    # visible coverage: tokens already present in any visible field, and their concept roots.
    visible_parts = [title, description, item_highlights] + [str(b) for b in (bullets or [])]
    visible_tokens = set()
    for part in visible_parts:
        visible_tokens.update(tokenize(part))
    visible_roots = {concept_root(t) for t in visible_tokens}

    brand_token_set = set()
    for t in list(brand_terms) + list(prohibited_terms):
        brand_token_set.update(tokenize(t))

    included, excluded = [], []
    visible_overlap, incremental = [], []
    summary = {}                       # exact count per exclusion reason (never truncated)
    risk_buckets = {b: [] for b in ("rejected", "wrong_audience", "wrong_product", "wrong_occasion",
                                    "brand_ip", "trademark_or_ip", "explicit_conflict", "malformed",
                                    "owner_approval_required")}

    def _drop(entry, reason):
        summary[reason] = summary.get(reason, 0) + 1
        if len(excluded) < EXCLUDED_TERMS_CAP:
            excluded.append(entry)

    # 1) ineligible candidates -> risk_results (exact counts + a bounded sample), NOT the token list. A real
    #    source has thousands of these; they are the adapter's risk screen, summarized here per class.
    ineligible = [r for r in keyword_source.keywords if not r["eligible"]]
    for rec in sorted(ineligible, key=lambda r: r["keyword_normalized"]):
        reason, bucket = _ineligible_reason(rec)
        summary[reason] = summary.get(reason, 0) + 1
        risk_buckets[bucket].append(rec["keyword_exact"])

    # 2) eligible candidates, ordered deterministically, contribute their incremental tokens.
    eligible = keyword_source.eligible_keywords()
    novel = {id(r): _novel_count(r, visible_tokens) for r in eligible}
    ordered = sorted(eligible, key=lambda r: _selection_key(r, novel[id(r)]))

    backend_tokens, backend_set, root_counts = [], set(), {}
    bytes_used = 0
    for rec in ordered:
        tokens = tokenize(rec["keyword_normalized"] or rec["keyword_exact"])
        phrase_len = len(tokens)
        if not tokens:
            _drop(_excl_keyword(rec, EXCL_EMPTY_AFTER_NORMALIZATION), EXCL_EMPTY_AFTER_NORMALIZATION)
            continue
        for tok in tokens:
            byte_cost = (1 if backend_tokens else 0) + len(tok.encode("utf-8"))
            if len(tok) < MIN_TOKEN_LEN and not tok.isdigit():
                _drop(_excl_token(rec, tok, byte_cost, EXCL_MALFORMED), EXCL_MALFORMED)
                continue
            if tok in brand_token_set:
                _drop(_excl_token(rec, tok, byte_cost, EXCL_BRAND_TERM), EXCL_BRAND_TERM)
                risk_buckets["brand_ip"].append(rec["keyword_exact"])
                continue
            if tok in backend_set:
                _drop(_excl_token(rec, tok, byte_cost, EXCL_DUPLICATE_TOKEN), EXCL_DUPLICATE_TOKEN)
                continue
            if tok in visible_tokens:
                # already fully covered by a visible field — repeating it in backend wastes bytes.
                _drop(_excl_token(rec, tok, byte_cost, EXCL_ALREADY_FULLY_COVERED),
                      EXCL_ALREADY_FULLY_COVERED)
                visible_overlap.append(tok)
                continue
            root = concept_root(tok)
            if root_counts.get(root, 0) >= max_per_root:
                _drop(_excl_token(rec, tok, byte_cost, EXCL_ROOT_SATURATED), EXCL_ROOT_SATURATED)
                continue
            if bytes_used + byte_cost > ceiling:
                # never cut a token: it does not fit whole, so it is excluded (a later shorter token may fit).
                _drop(_excl_token(rec, tok, byte_cost, EXCL_BYTE_LIMIT), EXCL_BYTE_LIMIT)
                continue
            # include the whole token.
            reason = _inclusion_reason(rec, tok, phrase_len, visible_roots)
            backend_tokens.append(tok)
            backend_set.add(tok)
            root_counts[root] = root_counts.get(root, 0) + 1
            bytes_used += byte_cost
            entry = _token_entry(rec, src_sha, tok, byte_cost, "inclusion_reason", reason)
            included.append(entry)
            incremental.append({"term": tok, "inclusion_reason": reason,
                                "source_keyword": rec["keyword_exact"],
                                "root_shared_with_visible": root in visible_roots})

    backend_string = " ".join(backend_tokens)
    excluded_total = sum(summary.values())
    risk_results = {}
    for bucket, kws in risk_buckets.items():
        uniq = sorted(set(kws))
        risk_results[bucket] = {"count": len(uniq), "keywords": uniq[:RISK_SAMPLE_CAP],
                                "truncated": len(uniq) > RISK_SAMPLE_CAP}
    source_hashes = {
        "keyword_source_file": keyword_source.source_file,
        "keyword_source_schema": keyword_source.source_schema,
        "keyword_source_run_id": keyword_source.source_run_id,
        "keyword_source_sha256": src_sha,
        "claim_evidence_sha256": claim_evidence.content_sha256() if claim_evidence is not None else None,
        "policy_source": policy.category_identifier if policy is not None else None,
    }
    return BackendOptimization(
        backend_search_terms_string=backend_string, byte_ceiling=ceiling,
        included_terms=included, excluded_terms=excluded,
        excluded_summary=dict(sorted(summary.items())), excluded_total=excluded_total,
        excluded_truncated=excluded_total > len(excluded),
        visible_field_overlap=sorted(set(visible_overlap)), incremental_coverage=incremental,
        risk_results=risk_results, source_hashes=source_hashes, max_tokens_per_root=max_per_root)


def _novel_count(rec, visible_tokens):
    toks = tokenize(rec["keyword_normalized"] or rec["keyword_exact"])
    return sum(1 for t in toks if t not in visible_tokens)


def _selection_key(rec, novel_count):
    """Deterministic scoring order (best first): eligibility (already filtered) -> tier priority ->
    incremental semantic coverage -> evidence strength -> competitor coverage -> rank -> search volume ->
    byte efficiency -> source priority -> stable lexical tie-break. Never selects purely by search volume."""
    return (
        -_TIER_PRIORITY.get(rec["normalized_tier"], 0),
        -novel_count,
        -_evidence_strength(rec),
        -(rec["competitor_coverage"] if rec["competitor_coverage"] is not None else -1.0),
        (rec["best_rank"] if rec["best_rank"] is not None else 1e9),
        -(rec["search_volume"] if rec["search_volume"] is not None else -1.0),
        len((rec["keyword_normalized"] or "").encode("utf-8")),
        _SCHEMA_RANK.get(rec["source_schema"], 9),
        rec["keyword_normalized"] or "",
    )


def _inclusion_reason(rec, token, phrase_len, visible_roots):
    """A deterministic, testable reason for why a novel token adds incremental searchable meaning."""
    if concept_root(token) in visible_roots:
        return INCL_UNIQUE_SYNONYM                 # variant of a visible concept (root matches, token doesn't)
    if _is_abbreviation(token):
        return INCL_APPROVED_ABBREVIATION
    if _has_high_evidence(rec):
        return INCL_HIGH_EVIDENCE
    if phrase_len >= 2:
        return INCL_UNIQUE_LONG_TAIL
    return INCL_LOW_VISIBLE_COVERAGE


def _ineligible_reason(rec):
    """Map an adapter-ineligible record onto (exclusion_reason, risk_results_bucket)."""
    for r in rec["blocking_risks"]:
        if r in _RISK_TO_EXCLUSION:
            return _RISK_TO_EXCLUSION[r]
    if "REJECTED_TIER" in rec["exclusion_reasons"] or rec["normalized_tier"] == KSA.REJECTED:
        return EXCL_INELIGIBLE_TIER, "rejected"
    if "OWNER_APPROVAL_REQUIRED" in rec["exclusion_reasons"]:
        return EXCL_OWNER_APPROVAL, "owner_approval_required"
    return EXCL_INELIGIBLE_TIER, "rejected"


# ---------------------------------------------------------------- convenience
def build_backend_payload(keyword_source, **kwargs):
    """The Product-Page contract payload: {'backend_search_terms_string', 'audit'}. Never returns bytes."""
    return optimize_backend(keyword_source, **kwargs).to_dict()


def write_backend_outputs(folder, optimization, generated_at=None, outdir=None):
    """Write BACKEND-SEARCH-TERMS.json and BACKEND-SEARCH-TERMS-AUDIT.json (deterministic canonical JSON)."""
    out = outdir or folder
    terms_path = os.path.join(out, BACKEND_TERMS_FILENAME)
    audit_path = os.path.join(out, BACKEND_AUDIT_FILENAME)
    terms_doc = {"schema_version": BACKEND_SCHEMA_VERSION,
                 "backend_search_terms_string": optimization.backend_search_terms_string,
                 "bytes_used": optimization.bytes_used, "byte_ceiling": optimization.byte_ceiling,
                 "bytes_remaining": optimization.bytes_remaining,
                 "content_sha256": optimization.content_sha256()}
    audit_doc = {"schema_version": BACKEND_SCHEMA_VERSION,
                 "content_sha256": optimization.content_sha256(), **optimization.to_dict()}
    if generated_at:
        terms_doc["generated_at"] = generated_at
        audit_doc["generated_at"] = generated_at
    with open(terms_path, "w", encoding="utf-8") as f:
        f.write(canonical_json(terms_doc))
    with open(audit_path, "w", encoding="utf-8") as f:
        f.write(canonical_json(audit_doc))
    return {"backend_terms": terms_path, "backend_audit": audit_path}
