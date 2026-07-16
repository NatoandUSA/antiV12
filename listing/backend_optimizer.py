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

# ---------------------------------------------------------------------------------------------------
# Session 5A.1 — SEMANTIC-QUALITY VOCABULARY (deterministic; documented). The optimizer no longer reduces
# every phrase into independently selectable tokens. Each eligible keyword is decomposed into semantic
# UNITS, and a single token publishes independently only when it has clear searchable meaning on its own.
# The vocabulary below is the apparel / nurse-apparel niche this toolkit actually serves (see runs/). It is
# not a general lexicon and is not tuned to any one desired backend string.
# ---------------------------------------------------------------------------------------------------

# Stopwords carry no independent backend search value (Amazon ignores them). They are NEVER emitted — not
# as a standalone token and not interior to a phrase — so the backend string is a clean, stopword-free bag.
_STOPWORDS = frozenset({
    "a", "an", "and", "the", "for", "of", "to", "in", "on", "with", "by", "or", "at", "as", "is", "are",
    "be", "am", "it", "its", "this", "that", "these", "those", "my", "your", "our", "their", "his", "her",
    "i", "you", "we", "they", "he", "she", "from", "up", "out", "off", "so", "if", "but", "not", "no",
    "yes", "do", "does", "did", "have", "has", "had", "was", "were", "will", "would", "can", "could",
})

# a phrase that carries one of these determiners/pronouns reads as a SLOGAN / sentence, not a product
# descriptor — so any residual token of that phrase that has no independent product meaning is a broken
# fragment ("this nurse prays" -> "prays"; "the nurse face" -> "face"), never a searchable term.
_SLOGAN_MARKERS = frozenset({"a", "an", "the", "this", "that", "these", "those", "my", "your", "our",
                             "their", "his", "her", "i", "you", "we", "they"})

# generic marketing filler with no incremental search value on its own.
_FILLER = frozenset({"cool", "cute", "best", "top", "unique", "idea", "ideas", "awesome", "amazing",
                     "perfect", "great", "nice", "lovely", "adorable", "trendy", "fun", "favorite",
                     "quality", "premium", "must", "have"})

# --- product-type vocabulary (garments + their families) ---
# A garment token publishes only when the verified product facts and/or the approved keyword identity
# confirm the garment family. Missing facts => conservative exclusion, never broad-synonym inclusion.
_GARMENT_FAMILY = {
    # the base "sweatshirt / pullover top" family — a crewneck/pullover accurately describes a standard
    # sweatshirt, so they are compatible synonyms of a sweatshirt identity.
    "sweatshirt": "sweatshirt", "sweatshirts": "sweatshirt", "crewneck": "sweatshirt",
    "crewnecks": "sweatshirt", "crew": "sweatshirt", "neck": "sweatshirt", "pullover": "sweatshirt",
    "pullovers": "sweatshirt",
    # distinct garments — a different silhouette, NOT an automatic synonym of a sweatshirt.
    "hoodie": "hooded", "hoodies": "hooded", "hooded": "hooded",
    "sweater": "knit", "sweaters": "knit", "cardigan": "knit", "cardigans": "knit",
    "shirt": "shirt", "shirts": "shirt", "tshirt": "shirt", "tshirts": "shirt", "tee": "shirt",
    "tees": "shirt",
    "tank": "tank", "tanks": "tank",
    "jacket": "outerwear", "jackets": "outerwear", "vest": "outerwear", "vests": "outerwear",
    "mug": "other", "mugs": "other", "tumbler": "other", "tumblers": "other", "hat": "other",
    "cap": "other", "socks": "other", "blanket": "other", "apron": "other", "pajamas": "other",
    "scrub": "other", "scrubs": "other", "onesie": "other", "legging": "other", "leggings": "other",
    "pants": "other", "dress": "other", "skirt": "other",
}
# closure attributes describe a specific construction that must be verified before publishing.
_CLOSURE_ATTRS = frozenset({"quarter", "zip", "half", "zipup", "quarterzip"})
_GARMENT_TOKENS = frozenset(_GARMENT_FAMILY) | _CLOSURE_ATTRS

# product-type compatibility results (PATCH 3).
PT_VERIFIED = "PRODUCT_TYPE_VERIFIED"
PT_COMPATIBLE_SYNONYM = "PRODUCT_TYPE_COMPATIBLE_SYNONYM"
PT_UNVERIFIED = "PRODUCT_TYPE_UNVERIFIED"
PT_CONFLICT = "PRODUCT_TYPE_CONFLICT"

# --- attribute + audience vocabulary (independently meaningful units) ---
_COLORS = frozenset({"navy", "blue", "black", "pink", "red", "green", "white", "gray", "grey", "maroon",
                     "purple", "teal", "burgundy", "heather", "olive", "tan", "brown", "yellow", "orange"})
_MATERIALS = frozenset({"fleece", "cotton", "polyester", "sherpa", "knit", "terry"})
_PERSONALIZATION = frozenset({"custom", "customized", "personalized", "personalised", "embroidered",
                              "embroidery", "monogram", "monogrammed", "name", "names", "initial",
                              "initials", "personalization"})
_GENDER = frozenset({"women", "womens", "woman", "men", "mens", "man", "female", "male", "ladies",
                     "unisex", "girls", "boys", "kids"})
# nurse-apparel audience / specialty / recipient terms that carry meaning on their own.
_AUDIENCE = frozenset({
    "nurse", "nurses", "nursing", "practitioner", "registered", "postpartum", "oncology", "peds",
    "pediatric", "emergency", "hospice", "dialysis", "cardiac", "psych", "travel", "student", "students",
    "graduate", "graduates", "graduating", "assistant", "midwife", "anesthetist", "aide", "future",
    "neonatal", "maternity", "telemetry", "trauma", "rehab", "surgical", "labor", "delivery", "clinical",
    "practitioners", "specialist", "caregiver", "medical",
})
# generic descriptors that are weak but still searchable (kept, but never treated as a phrase fragment).
_DESCRIPTORS = frozenset({"gift", "gifts", "present", "presents", "new", "work", "set", "bulk", "pack",
                          "essentials", "apparel", "clothing", "wear", "outfit", "mom", "moms", "mother",
                          "mothers", "dad", "size", "plus"})

# occasion terms need eligible keyword evidence AND product-context support (a verified occasion fact or
# an occasion in the approved identity). Without support they are excluded (PATCH 4).
_OCCASION_TERMS = frozenset({"holiday", "holidays", "christmas", "xmas", "halloween", "birthday",
                             "graduation", "valentine", "valentines", "thanksgiving", "easter", "week",
                             "day", "appreciation", "school", "pinning", "ceremony", "season", "festive",
                             "fall", "autumn", "winter", "summer", "spring"})

# every token with a recognized independent meaning — used to decide phrase-fragment exemption.
_RECOGNIZED_MEANING = (_GARMENT_TOKENS | _COLORS | _MATERIALS | _PERSONALIZATION | _GENDER | _AUDIENCE
                       | _DESCRIPTORS | _OCCASION_TERMS | _ABBREVIATIONS)

# approved multi-word specialty / audience phrases whose member tokens must retain their phrase meaning
# and provenance (never reconstructed from unrelated standalone tokens).
_SPECIALTY_PHRASES = (
    "labor and delivery", "labor delivery", "med surg", "nurse practitioner", "registered nurse",
    "family nurse practitioner", "nursing student", "student nurse", "future nurse", "school nurse",
    "travel nurse", "emergency room", "operating room", "intensive care", "neonatal intensive care",
    "post partum", "nurse anesthetist", "er nurse", "icu nurse", "nicu nurse", "labor and delivery nurse",
    "nursing school", "oncology nurse", "hospice nurse", "pediatric nurse", "psych nurse",
)

# vocabulary + inflections used only by the suspicious-concatenation detector (two real words merged with
# no separator, e.g. a data glitch "registerednurse" / "registeredtom"). Legitimate long single words
# ("personalized", "embroidered", "practitioner", "postpartum") are protected by the inflection allowlist
# and by never matching two full known words.
_CONCAT_WORDS = (_AUDIENCE | _GARMENT_TOKENS | _PERSONALIZATION | _GENDER | _COLORS | _MATERIALS
                 | _DESCRIPTORS | frozenset({"nurse", "sweatshirt", "hoodie", "gift"}))
_INFLECTIONS = ("s", "es", "ed", "ing", "er", "ers", "ion", "ions", "al", "y", "ies", "ist", "ists",
                "ment", "ize", "izes", "ized", "izing", "able", "ness")

# --- inclusion reasons (documented vocabulary) ---
INCL_UNIQUE_SYNONYM = "UNIQUE_SYNONYM"
INCL_APPROVED_ABBREVIATION = "APPROVED_ABBREVIATION"
INCL_UNIQUE_LONG_TAIL = "UNIQUE_LONG_TAIL_COMPONENT"
INCL_HIGH_EVIDENCE = "HIGH_EVIDENCE_SUPPORT"
INCL_LOW_VISIBLE_COVERAGE = "LOW_VISIBLE_COVERAGE"
# Session 5A.1 semantic-unit inclusion reasons.
INCL_SPECIALTY_PHRASE = "SPECIALTY_PHRASE_TOKEN"
INCL_AUDIENCE_TERM = "AUDIENCE_TERM"
INCL_PRODUCT_ATTRIBUTE = "PRODUCT_ATTRIBUTE"
INCL_COMPATIBLE_PRODUCT_TYPE = "COMPATIBLE_PRODUCT_TYPE"
INCL_SUPPORTED_OCCASION = "SUPPORTED_OCCASION"
INCLUSION_REASONS = (INCL_UNIQUE_SYNONYM, INCL_APPROVED_ABBREVIATION, INCL_UNIQUE_LONG_TAIL,
                     INCL_HIGH_EVIDENCE, INCL_LOW_VISIBLE_COVERAGE, INCL_SPECIALTY_PHRASE,
                     INCL_AUDIENCE_TERM, INCL_PRODUCT_ATTRIBUTE, INCL_COMPATIBLE_PRODUCT_TYPE,
                     INCL_SUPPORTED_OCCASION)

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
# Session 5A.1 semantic-quality exclusion reasons (PATCH 2/3/4). Nothing is ever silently discarded.
EXCL_ORPHAN_STOPWORD = "ORPHAN_STOPWORD"
EXCL_LOW_INFORMATION = "LOW_INFORMATION_TOKEN"
EXCL_UNEXPLAINED_NUMBER = "UNEXPLAINED_NUMBER"
EXCL_SUSPICIOUS_CONCAT = "SUSPICIOUS_CONCATENATION"
EXCL_BROKEN_FRAGMENT = "BROKEN_PHRASE_FRAGMENT"
EXCL_REDUNDANT_INFLECTION = "REDUNDANT_INFLECTION"
EXCL_UNRECOGNIZED_ABBREVIATION = "UNRECOGNIZED_ABBREVIATION"
EXCL_PRODUCT_TYPE_CONFLICT = "PRODUCT_TYPE_CONFLICT"
EXCL_PRODUCT_TYPE_UNVERIFIED = "PRODUCT_TYPE_UNVERIFIED"
EXCL_UNSUPPORTED_OCCASION = "UNSUPPORTED_OCCASION"
EXCL_LOW_INCREMENTAL_COVERAGE = "LOW_INCREMENTAL_COVERAGE"

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


# ---------------------------------------------------------------- semantic-quality classification
def _has_vowel(token):
    return any(c in "aeiouy" for c in token)


def _is_unrecognized_abbreviation(token):
    """A 3+ char all-consonant alpha token that is NOT an approved credential reads as a broken/typo
    abbreviation (e.g. 'xqz'). Approved no-vowel credentials (lpn, bsn, ltc) are exempt via the set."""
    return (token.isalpha() and len(token) >= 3 and not _has_vowel(token)
            and token not in _ABBREVIATIONS)


def _is_suspicious_concatenation(token):
    """True when a token is two real words merged with no separator (a data glitch), e.g. 'registerednurse'
    or 'registeredtom'. Deterministic and conservative: it fires only when a KNOWN word (>=4 chars) is a
    strict prefix and the remainder is either another known word or a >=3 char non-inflection remnant. A
    legitimate single word ('personalized', 'embroidered') either is not decomposable this way or leaves an
    inflectional suffix, so it is never flagged."""
    if not token.isalpha() or len(token) < 8 or token in _CONCAT_WORDS:
        return False
    for i in range(4, len(token) - 1):
        head, tail = token[:i], token[i:]
        if head not in _CONCAT_WORDS:
            continue
        if tail in _CONCAT_WORDS:                      # two full known words merged
            return True
        if len(tail) >= 3 and tail not in _INFLECTIONS:  # known word + unexplained remnant
            return True
    return False


def _garment_identity(product_facts, title, primary_keyword):
    """The garment family the product actually IS, from (1) VERIFIED product facts and (2) the approved
    keyword identity (title + primary keyword). Missing facts fall back to keyword identity only — never to
    a broad synonym set. Returns (verified_garment_tokens, identity_garment_tokens)."""
    verified = set()
    if product_facts is not None:
        for field in ("garment_type", "product_type"):
            try:
                val = product_facts.publishable_value(field)
            except Exception:
                val = None
            if val:
                verified.update(t for t in tokenize(val) if t in _GARMENT_TOKENS)
    identity = set()
    for t in tokenize(title) + tokenize(primary_keyword or ""):
        if t in _GARMENT_TOKENS:
            identity.add(t)
    return verified, identity


def _product_type_result(token, verified_garments, identity_garments):
    """Classify a garment/closure token against the product's actual family (PATCH 3)."""
    if token in verified_garments:
        return PT_VERIFIED
    if token in identity_garments:
        return PT_VERIFIED
    id_families = {_GARMENT_FAMILY.get(t) for t in (verified_garments | identity_garments)}
    fam = _GARMENT_FAMILY.get(token)
    if fam is not None and fam in id_families:
        # a crewneck/pullover of a verified sweatshirt identity is a compatible synonym.
        return PT_COMPATIBLE_SYNONYM
    if token in _CLOSURE_ATTRS or fam == "sweatshirt":
        # a verifiable neckline/closure style whose support we do not have -> unverified, not a conflict.
        return PT_UNVERIFIED
    return PT_CONFLICT


def _supported_occasions(product_facts, title, primary_keyword):
    """Occasion tokens supported by product context: a VERIFIED occasion fact or an occasion already in the
    approved identity. Everything else is excluded (PATCH 4)."""
    supported = set()
    ident_text = f"{title} {primary_keyword or ''}"
    for t in tokenize(ident_text):
        if t in _OCCASION_TERMS:
            supported.add(t)
    if product_facts is not None:
        try:
            occ = product_facts.publishable_value("occasion")
        except Exception:
            occ = None
        if occ:
            for t in tokenize(occ):
                if t in _OCCASION_TERMS:
                    supported.add(t)
    return supported


def _matched_specialty_phrase(phrase_tokens):
    """The longest approved specialty phrase contained (contiguously) in a keyword's tokens, else None.
    Used to tag member tokens with their phrase provenance so a specialty phrase keeps its meaning and is
    never reconstructed from unrelated standalone tokens."""
    joined = " ".join(phrase_tokens)
    best = None
    for phrase in _SPECIALTY_PHRASES:
        p = f" {phrase} "
        if p in f" {joined} ":
            if best is None or len(phrase) > len(best):
                best = phrase
    return best


def _classify_token(token, is_slogan, has_anchor, ctx):
    """The core semantic-unit decision for ONE token. Returns (emit, reason, meta). `emit` is True to
    publish the token, False to exclude it; `reason` is the documented inclusion/exclusion reason; `meta`
    carries unit provenance (unit_type + product_type_result). Deterministic and side-effect free.

    `has_anchor` is True when the token's keyword carries a product-identity/recognized token (audience,
    garment, abbreviation, covered field word). A residual token with no recognized meaning ALONGSIDE such
    an anchor is a design/slogan modifier ('nurse FACE sweatshirt'), not a standalone search term."""
    meta = {"unit_type": None, "product_type_result": None}
    # 1) hard malformed / low-information filters (PATCH 2).
    if token in _STOPWORDS:
        return False, EXCL_ORPHAN_STOPWORD, meta
    if token.isdigit():
        return False, EXCL_UNEXPLAINED_NUMBER, meta
    if len(token) < MIN_TOKEN_LEN:
        return False, EXCL_LOW_INFORMATION, meta
    if _is_suspicious_concatenation(token):
        return False, EXCL_SUSPICIOUS_CONCAT, meta
    if _is_unrecognized_abbreviation(token):
        return False, EXCL_UNRECOGNIZED_ABBREVIATION, meta
    if token in _FILLER:
        return False, EXCL_LOW_INFORMATION, meta
    # 2) occasion gating (PATCH 4): needs eligible evidence AND product-context support.
    if token in _OCCASION_TERMS:
        if token in ctx["supported_occasions"]:
            meta["unit_type"] = "occasion"
            return True, INCL_SUPPORTED_OCCASION, meta
        return False, EXCL_UNSUPPORTED_OCCASION, meta
    # 3) product-type gating (PATCH 3): garments/closures must be verified or a compatible synonym.
    if token in _GARMENT_TOKENS:
        ptr = _product_type_result(token, ctx["verified_garments"], ctx["identity_garments"])
        meta["product_type_result"] = ptr
        meta["unit_type"] = "product_type"
        if ptr in (PT_VERIFIED, PT_COMPATIBLE_SYNONYM):
            return True, INCL_COMPATIBLE_PRODUCT_TYPE, meta
        return (False, EXCL_PRODUCT_TYPE_CONFLICT if ptr == PT_CONFLICT
                else EXCL_PRODUCT_TYPE_UNVERIFIED, meta)
    # 4) independently meaningful units.
    if token in _ABBREVIATIONS:
        meta["unit_type"] = "abbreviation"
        return True, INCL_APPROVED_ABBREVIATION, meta
    if token in _AUDIENCE:
        meta["unit_type"] = "audience"
        return True, INCL_AUDIENCE_TERM, meta
    if token in _COLORS or token in _MATERIALS or token in _PERSONALIZATION or token in _GENDER:
        meta["unit_type"] = "attribute"
        return True, INCL_PRODUCT_ATTRIBUTE, meta
    if token in _DESCRIPTORS:
        meta["unit_type"] = "descriptor"
        return True, INCL_LOW_VISIBLE_COVERAGE, meta
    # 5) a residual token with no recognized meaning is a broken fragment when it sits inside a slogan
    #    sentence OR alongside a product-identity anchor — it is a design/slogan word, not a search term.
    if is_slogan or has_anchor:
        return False, EXCL_BROKEN_FRAGMENT, meta
    # 6) an unanchored, well-formed content token (the whole keyword is a plain phrase) — publishes as a
    #    long-tail/term. This is what keeps genuine standalone keywords selectable.
    meta["unit_type"] = "term"
    return True, None, meta                            # reason resolved by _inclusion_reason at the call site


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


def _token_entry(rec, source_sha256, token, byte_cost, reason_key, reason_value, unit=None,
                 unit_type=None, product_type_result=None):
    """A full-provenance entry — used for INCLUDED tokens, where every token must carry full provenance
    plus its semantic-unit lineage (the phrase/unit it belongs to, so a phrase keeps its meaning)."""
    e = _provenance(rec, source_sha256)
    e.update({"term": token, "unit": unit or token, "unit_type": unit_type,
              "product_type_result": product_type_result, "concept_root": concept_root(token),
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
                 incremental_coverage, risk_results, source_hashes, max_tokens_per_root,
                 semantic_quality=None, product_type_results=None, audience_occasion_results=None):
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
        # Session 5A.1 semantic-quality sub-results (PATCH 5 / audit surface).
        self.semantic_quality = semantic_quality or {}
        self.product_type_results = product_type_results or {}
        self.audience_occasion_results = audience_occasion_results or {}

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
            "semantic_quality": self.semantic_quality,
            "product_type_results": self.product_type_results,
            "audience_occasion_results": self.audience_occasion_results,
            "source_hashes": self.source_hashes,
        }

    def to_dict(self):
        return {"backend_search_terms_string": self.backend_search_terms_string, "audit": self.audit()}

    def content_sha256(self):
        return hashlib.sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- core
def _semantic_score(unit_type):
    """A token's semantic-information score by its unit type (PATCH 5). Strong meaning (specialty phrase,
    audience, product type, attribute, abbreviation, supported occasion) scores 2; a generic descriptor or
    plain long-tail term scores 1; nothing recognized scores 0."""
    if unit_type in ("specialty_phrase", "audience", "product_type", "attribute", "abbreviation",
                     "occasion"):
        return 2
    if unit_type in ("descriptor", "term"):
        return 1
    return 0


def optimize_backend(keyword_source, policy=None, title="", bullets=(), description="",
                     item_highlights="", claim_evidence=None, brand_terms=(), prohibited_terms=(),
                     max_tokens_per_root=None, product_facts=None, primary_keyword="",
                     min_incremental_coverage=1, min_semantic_score=0):
    """Build the byte-safe, provenance-audited, semantically-coherent backend search-terms string.

    keyword_source : a KSA.NormalizedKeywordSource (the single authoritative keyword source).
    policy         : a category_policy_registry.CategoryPolicy; its backend_byte_ceiling is the ONE ceiling.
    title/bullets/description/item_highlights : the visible fields, used for incremental-coverage decisions.
    claim_evidence : optional ClaimEvidence, used only to add its source hash to the audit (backend terms
                     are keywords, not factual claims — the claim gate governs copy, not search terms).
    brand_terms / prohibited_terms : extra brand/prohibited tokens that must never enter the backend, on top
                     of the adapter's own risk screening.
    product_facts  : optional NormalizedProductFacts. With the approved keyword identity (title + primary
                     keyword) it establishes the product family, so unverified/conflicting garment types and
                     unsupported occasions are excluded rather than pulled in as broad synonyms.
    primary_keyword: the approved primary keyword — part of the product identity for product-type gating.
    min_incremental_coverage : quality-first floor (PATCH 5). A candidate keyword must contribute at least
                     this many NEW, semantically-meaningful tokens to be selected; otherwise it is skipped
                     and its bytes are left unused. The optimizer never pads the ceiling with weak terms.
    min_semantic_score : optional low-quality rejection threshold (PATCH 5). A token whose semantic-
                     information score (see _semantic_score) is below this is not published even when bytes
                     remain — leaving unused bytes is preferred to padding with weak terms. Default 0 keeps
                     every meaningful token; set 2 to publish only strong (specialty/audience/attribute/
                     product-type/abbreviation) tokens.

    Instead of reducing every keyword into independently selectable words, each keyword is decomposed into
    semantic UNITS. A token publishes independently only when it has clear searchable meaning on its own
    (an approved phrase/abbreviation, an audience or specialty term, or a verified product attribute).
    Orphan stopwords, unexplained numbers, malformed/concatenated tokens, broken phrase fragments,
    unverified/conflicting garment types and unsupported occasions are excluded — each with a precise
    reason. Phrase provenance (the unit a token belongs to) is preserved on every included token.
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

    # product family + supported occasions, from VERIFIED facts and the approved keyword identity.
    verified_garments, identity_garments = _garment_identity(product_facts, title, primary_keyword)
    supported_occasions = _supported_occasions(product_facts, title, primary_keyword)
    ctx = {"verified_garments": verified_garments, "identity_garments": identity_garments,
           "supported_occasions": supported_occasions}

    included, excluded = [], []
    visible_overlap, incremental = [], []
    summary = {}                       # exact count per exclusion reason (never truncated)
    pt_seen = {PT_VERIFIED: set(), PT_COMPATIBLE_SYNONYM: set(), PT_UNVERIFIED: set(), PT_CONFLICT: set()}
    occ_supported, occ_rejected, audience_seen = set(), set(), set()
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

    # 2) eligible candidates, ordered deterministically, contribute their semantic units' tokens.
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
        is_slogan = any(t in _SLOGAN_MARKERS for t in tokens)
        # an anchor = a product-identity/recognized token or a token already covered by a visible field; it
        # marks the keyword as a real product phrase, so any unrecognized residual is a design/slogan word.
        has_anchor = any(t in _RECOGNIZED_MEANING or t in visible_tokens for t in tokens)
        specialty = _matched_specialty_phrase(tokens)
        specialty_toks = set(tokenize(specialty)) if specialty else set()

        # 2a) classify each token into its semantic unit BEFORE committing any of them (PATCH 5: know a
        #     keyword's real incremental contribution, so a keyword that adds no meaningful NEW token is
        #     skipped rather than padded in).
        plan = []                                      # (tok, emit, reason, meta) for emittable candidates
        for tok in tokens:
            if tok in brand_token_set:
                plan.append((tok, False, EXCL_BRAND_TERM, {"unit_type": None}))
                continue
            emit, reason, meta = _classify_token(tok, is_slogan, has_anchor, ctx)
            plan.append((tok, emit, reason, meta))
        novel_emittable = sum(1 for tok, emit, _r, _m in plan
                              if emit and tok not in backend_set and tok not in visible_tokens)
        keyword_selected = novel_emittable >= min_incremental_coverage

        for tok, emit, reason, meta in plan:
            byte_cost = (1 if backend_tokens else 0) + len(tok.encode("utf-8"))
            unit = specialty if tok in specialty_toks else None
            unit_type = "specialty_phrase" if unit else meta.get("unit_type")
            ptr = meta.get("product_type_result")
            if ptr in pt_seen:
                pt_seen[ptr].add(tok)
            # 2b) semantic-quality exclusions (malformed / low-info / product-type / occasion / fragment).
            if not emit:
                if reason == EXCL_BRAND_TERM:
                    risk_buckets["brand_ip"].append(rec["keyword_exact"])
                elif reason == EXCL_UNSUPPORTED_OCCASION:
                    occ_rejected.add(tok)
                _drop(_excl_token(rec, tok, byte_cost, reason), reason)
                continue
            # 2c) structural exclusions (dup / visible-overlap / saturation / byte / low-coverage).
            if tok in backend_set:
                _drop(_excl_token(rec, tok, byte_cost, EXCL_DUPLICATE_TOKEN), EXCL_DUPLICATE_TOKEN)
                continue
            if tok in visible_tokens:
                _drop(_excl_token(rec, tok, byte_cost, EXCL_ALREADY_FULLY_COVERED),
                      EXCL_ALREADY_FULLY_COVERED)
                visible_overlap.append(tok)
                continue
            if not keyword_selected:
                # quality-first: this keyword adds no meaningful NEW coverage — leave the bytes unused.
                _drop(_excl_token(rec, tok, byte_cost, EXCL_LOW_INCREMENTAL_COVERAGE),
                      EXCL_LOW_INCREMENTAL_COVERAGE)
                continue
            if _semantic_score(unit_type) < min_semantic_score:
                # low-quality rejection threshold: leave bytes unused rather than pad with a weak token.
                _drop(_excl_token(rec, tok, byte_cost, EXCL_LOW_INCREMENTAL_COVERAGE),
                      EXCL_LOW_INCREMENTAL_COVERAGE)
                continue
            root = concept_root(tok)
            if root_counts.get(root, 0) >= max_per_root:
                _drop(_excl_token(rec, tok, byte_cost, EXCL_ROOT_SATURATED), EXCL_ROOT_SATURATED)
                continue
            if bytes_used + byte_cost > ceiling:
                # never cut a token: it does not fit whole, so it is excluded (a later shorter token may fit).
                _drop(_excl_token(rec, tok, byte_cost, EXCL_BYTE_LIMIT), EXCL_BYTE_LIMIT)
                continue
            # include the whole token; a plain term (reason None) gets the incremental-coverage reason.
            final_reason = reason or _inclusion_reason(rec, tok, phrase_len, visible_roots)
            if unit_type == "specialty_phrase":
                final_reason = INCL_SPECIALTY_PHRASE
            backend_tokens.append(tok)
            backend_set.add(tok)
            root_counts[root] = root_counts.get(root, 0) + 1
            bytes_used += byte_cost
            if unit_type in ("audience", "specialty_phrase"):
                audience_seen.add(tok)
            if unit_type == "occasion":
                occ_supported.add(tok)
            entry = _token_entry(rec, src_sha, tok, byte_cost, "inclusion_reason", final_reason,
                                 unit=unit, unit_type=unit_type, product_type_result=ptr)
            included.append(entry)
            incremental.append({"term": tok, "inclusion_reason": final_reason,
                                "unit": unit or tok, "unit_type": unit_type,
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
    # semantic-quality sub-results (PATCH 5 / auditor surface). bytes_remaining > 0 with a non-empty source
    # is the intended, healthy outcome: coverage stopped when useful candidates were exhausted.
    semantic_quality = {
        "min_incremental_coverage": min_incremental_coverage,
        "min_semantic_score": min_semantic_score,
        "bytes_remaining": ceiling - bytes_used,
        "stopped_before_ceiling": bytes_used < ceiling,
        "excluded_low_information": summary.get(EXCL_LOW_INFORMATION, 0),
        "excluded_orphan_stopwords": summary.get(EXCL_ORPHAN_STOPWORD, 0),
        "excluded_unexplained_numbers": summary.get(EXCL_UNEXPLAINED_NUMBER, 0),
        "excluded_suspicious_concatenations": summary.get(EXCL_SUSPICIOUS_CONCAT, 0),
        "excluded_broken_fragments": summary.get(EXCL_BROKEN_FRAGMENT, 0),
        "excluded_unrecognized_abbreviations": summary.get(EXCL_UNRECOGNIZED_ABBREVIATION, 0),
        "excluded_low_incremental_coverage": summary.get(EXCL_LOW_INCREMENTAL_COVERAGE, 0),
    }
    product_type_results = {
        "verified_garment_identity": sorted(verified_garments | identity_garments),
        "verified": sorted(pt_seen[PT_VERIFIED]),
        "compatible_synonym": sorted(pt_seen[PT_COMPATIBLE_SYNONYM]),
        "unverified_excluded": sorted(pt_seen[PT_UNVERIFIED]),
        "conflict_excluded": sorted(pt_seen[PT_CONFLICT]),
    }
    audience_occasion_results = {
        "supported_occasions": sorted(occ_supported),
        "rejected_occasions": sorted(occ_rejected),
        "audience_terms": sorted(audience_seen),
    }
    return BackendOptimization(
        backend_search_terms_string=backend_string, byte_ceiling=ceiling,
        included_terms=included, excluded_terms=excluded,
        excluded_summary=dict(sorted(summary.items())), excluded_total=excluded_total,
        excluded_truncated=excluded_total > len(excluded),
        visible_field_overlap=sorted(set(visible_overlap)), incremental_coverage=incremental,
        risk_results=risk_results, source_hashes=source_hashes, max_tokens_per_root=max_per_root,
        semantic_quality=semantic_quality, product_type_results=product_type_results,
        audience_occasion_results=audience_occasion_results)


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
