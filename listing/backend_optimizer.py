#!/usr/bin/env python3
"""
listing.backend_optimizer — the evidence-aware, byte-safe backend search-term optimizer (ACT-009).

Session 5A.2 makes the backend an assembly of ATOMIC SEMANTIC UNITS, not independently selected tokens.
Each eligible keyword is decomposed into complete semantic units — a multi-token specialty/gift phrase, a
verified product attribute, an audience term, a compatible product type, or a plain long-tail token — and a
unit publishes WHOLLY and contiguously or not at all. The emitted string equals the ordered concatenation
of the included units' `text`, so a specialty phrase can never be reduced to a broken fragment
("labor and delivery nurse" never becomes "labor delivery"; "registered nurse" never becomes "registered").

On top of that this module:

  * draws candidates ONLY from the authoritative keyword source's ELIGIBLE records (TIER_A/B/C and
    owner-approved REVIEW/OUTLIER/RESIDUE). REJECTED, ineligible, and every blocking-risk class
    (trademark, brand, IP, wrong audience/product/occasion, explicit conflict, malformed, contaminated,
    unapproved legacy REVIEW) are NEVER selected — they are recorded as excluded with a reason;
  * counts UTF-8 bytes and never exceeds the category-policy backend byte ceiling; a unit is included whole
    or not at all (never cut);
  * DEDUPLICATES at the CONCEPT level: aliases/inflections that add no distinct searchable meaning collapse
    to one concept (crewneck / "crew neck"; women / womens; nurse / nursing; "med surg" / "med surg nurse")
    and the redundant unit is excluded as DUPLICATE_SEMANTIC_CONCEPT naming the retained unit;
  * GATES product attributes on evidence: a color / material / decoration / personalization / quantity /
    fit / neckline / closure term publishes only with a VERIFIED product fact or explicit approved identity
    evidence — it is never inferred from the niche name or from keyword demand;
  * GATES audience / recipient / occasion concepts: they publish only as complete supported units; a bare
    "men" that conflicts with a verified women-only product, an unsupported "graduates", a context-free
    "gifts", or a broken phrase fragment is excluded with a precise reason;
  * is QUALITY-FIRST: a candidate unit must clear BOTH a non-zero minimum semantic-information score and a
    minimum incremental-coverage score. Low-value units are never added just because bytes remain, so the
    optimizer may stop with unused bytes;
  * records provenance + an inclusion reason for every included unit/token and an exclusion reason for every
    excluded candidate, plus a per-risk exclusion summary and the source hashes;
  * is deterministic: identical immutable inputs produce an identical backend string and identical audit.

The category-policy registry is the ONE source of the byte ceiling (`policy.backend_byte_ceiling`). 249 is
only ever used as an explicit documented fallback when no policy is supplied.

Public API:
  optimize_backend(keyword_source, policy=None, title="", bullets=(), description="",
                   item_highlights="", claim_evidence=None, brand_terms=(), prohibited_terms=(),
                   max_tokens_per_root=None, product_facts=None, primary_keyword="",
                   min_incremental_coverage=1, min_semantic_score=1) -> BackendOptimization
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

BACKEND_SCHEMA_VERSION = "1.1.0"
BACKEND_TERMS_FILENAME = "BACKEND-SEARCH-TERMS.json"
BACKEND_AUDIT_FILENAME = "BACKEND-SEARCH-TERMS-AUDIT.json"

# The ONLY hardcoded ceiling in this module: a documented fallback used when no category policy is passed.
# In every real path the ceiling comes from the shared category_policy_registry (policy.backend_byte_ceiling).
DEFAULT_BACKEND_BYTE_CEILING = 249

# A single character carries no search meaning; drop it (documented). 2+ char tokens — including numeric
# sizes ("3xl") and audience abbreviations ("rn", "cna", "lpn", "np") — are preserved.
MIN_TOKEN_LEN = 2

# Default root/concept saturation: at most this many standalone tokens sharing one concept root may enter
# the backend, so a single duplicated root ("dog dogs …") cannot consume most of the byte budget. Phrase-
# interior tokens (the shared "nurse" of distinct specialty phrases) are exempt — phrase integrity wins.
DEFAULT_MAX_TOKENS_PER_ROOT = 4

# Session 5A.2 quality-first production defaults (both NON-ZERO). A unit must clear both to publish.
PRODUCTION_MIN_SEMANTIC_SCORE = 1
PRODUCTION_MIN_INCREMENTAL_COVERAGE = 1

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
# SEMANTIC-QUALITY VOCABULARY (deterministic; documented). The optimizer decomposes each keyword into
# semantic UNITS. This vocabulary is the apparel / nurse-apparel niche this toolkit actually serves (see
# runs/). It is not a general lexicon and is not tuned to any one desired backend string.
# ---------------------------------------------------------------------------------------------------

# Stopwords carry no independent backend search value (Amazon ignores them). They are NEVER emitted as a
# standalone token; they survive ONLY interior to an approved phrase (the "and" of "labor and delivery").
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
# closure/neckline attributes describe a specific construction that must be verified before publishing.
_CLOSURE_ATTRS = frozenset({"quarter", "zip", "half", "zipup", "quarterzip"})
_GARMENT_TOKENS = frozenset(_GARMENT_FAMILY) | _CLOSURE_ATTRS

# product-type compatibility results (PATCH 3).
PT_VERIFIED = "PRODUCT_TYPE_VERIFIED"
PT_COMPATIBLE_SYNONYM = "PRODUCT_TYPE_COMPATIBLE_SYNONYM"
PT_UNVERIFIED = "PRODUCT_TYPE_UNVERIFIED"
PT_CONFLICT = "PRODUCT_TYPE_CONFLICT"

# --- verified-attribute vocabulary (each requires evidence before publishing) ---
_COLORS = frozenset({"navy", "blue", "black", "pink", "red", "green", "white", "gray", "grey", "maroon",
                     "purple", "teal", "burgundy", "heather", "olive", "tan", "brown", "yellow", "orange"})
_MATERIALS = frozenset({"fleece", "cotton", "polyester", "sherpa", "knit", "terry"})
_DECORATION = frozenset({"embroidered", "embroidery", "embroider", "printed", "print", "screenprint",
                         "screenprinted", "vinyl", "htv", "sublimation", "sublimated", "stitched",
                         "applique"})
_PERSONALIZATION = frozenset({"custom", "customized", "customizable", "personalized", "personalised",
                              "personalize", "personalization", "monogram", "monogrammed", "name",
                              "names", "initial", "initials"})
_QUANTITY = frozenset({"bulk", "wholesale", "multipack", "dozen", "bundle", "bundles"})
_FIT = frozenset({"slim", "relaxed", "oversized", "loose", "fitted", "cropped", "boxy", "fit"})

# --- audience / gender / recipient vocabulary ---
_GENDER_FEMALE = frozenset({"women", "womens", "woman", "female", "ladies", "girl", "girls"})
_GENDER_MALE = frozenset({"men", "mens", "man", "male", "boy", "boys"})
_GENDER_NEUTRAL = frozenset({"unisex"})
_GENDER = _GENDER_FEMALE | _GENDER_MALE | _GENDER_NEUTRAL | frozenset({"kids"})

# audience / specialty tokens that carry meaning on their OWN (a buyer searches them alone as a nurse type).
# Phrase-only fragments (registered, practitioner, future, labor, delivery, emergency, travel, graduate)
# are deliberately NOT here — they publish only inside an approved phrase or via context gating.
_AUDIENCE_STANDALONE = frozenset({
    "nurse", "nurses", "nursing", "postpartum", "oncology", "hospice", "pediatric", "peds", "cardiac",
    "dialysis", "psych", "telemetry", "trauma", "neonatal", "maternity", "surgical", "clinical",
    "midwife", "anesthetist", "caregiver", "medical", "specialist", "aide",
})
# recipient tokens that need an approved graduation/occasion context to publish.
_RECIPIENT_CONTEXT = frozenset({"graduate", "graduates", "graduating", "grad"})
# gift tokens that publish only inside a complete gift-intent phrase (never as a context-free token).
_GIFT_TOKENS = frozenset({"gift", "gifts"})

# generic descriptors that are weak but still searchable (kept, but never treated as a phrase fragment).
_DESCRIPTORS = frozenset({"present", "presents", "new", "work", "set", "pack", "essentials", "apparel",
                          "clothing", "wear", "outfit", "mom", "moms", "mother", "mothers", "dad", "size",
                          "plus"})

# occasion terms need eligible keyword evidence AND product-context support (a verified occasion fact or an
# occasion in the approved identity). Without support they are excluded (PATCH 4).
_OCCASION_TERMS = frozenset({"holiday", "holidays", "christmas", "xmas", "halloween", "birthday",
                             "graduation", "valentine", "valentines", "thanksgiving", "easter", "week",
                             "day", "appreciation", "school", "pinning", "ceremony", "season", "festive",
                             "fall", "autumn", "winter", "summer", "spring"})

# --- approved multi-token phrases (each publishes wholly & contiguously; interior stopwords preserved) ---
# specialty / audience phrases. Longest matches win (greedy), so "med surg nurse" beats "med surg".
_SPECIALTY_PHRASES = (
    "labor and delivery nurse", "labor and delivery",
    "registered nurse", "registered nurses",
    "nurse practitioner", "nurse practitioners", "family nurse practitioner",
    "future nurse", "future nurses",
    "med surg nurse", "med surg",
    "emergency room nurse", "emergency room", "emergency nurse",
    "er nurse", "icu nurse", "nicu nurse", "picu nurse", "cvicu nurse", "pacu nurse",
    "oncology nurse", "hospice nurse", "pediatric nurse", "peds nurse", "psych nurse", "cardiac nurse",
    "dialysis nurse", "travel nurse", "trauma nurse", "telemetry nurse", "surgical nurse",
    "rehab nurse", "clinical nurse", "postpartum nurse", "charge nurse", "flight nurse",
    "nursing student", "student nurse", "nurse anesthetist", "nurse midwife",
    "operating room", "intensive care", "neonatal intensive care",
)
# complete gift-intent phrases (a bare "gift"/"gifts" never publishes on its own).
_GIFT_PHRASES = (
    "nurse gift", "nurse gifts", "nursing gift", "nursing gifts", "rn gift", "rn gifts",
    "gift for nurse", "gift for nurses", "gifts for nurse", "gifts for nurses",
    "nurse practitioner gift", "nurse practitioner gifts",
)
# garment phrases. "crew neck" is a two-token spelling of the crewneck concept, so it must dedup against the
# "crewneck" token. Closure/neckline tokens (quarter, zip, half) stay single tokens — their product-type
# gating already runs per token, and a "quarter zip" keyword excludes each unverified token by name.
_GARMENT_PHRASES = ("crew neck", "crew necks")
# representative single token used to run product-type gating for a garment phrase.
_GARMENT_PHRASE_REP = {"crew neck": "crewneck", "crew necks": "crewneck"}

_SPECIALTY_PHRASE_SET = frozenset(_SPECIALTY_PHRASES)
_GIFT_PHRASE_SET = frozenset(_GIFT_PHRASES)
_GARMENT_PHRASE_SET = frozenset(_GARMENT_PHRASES)
_MAX_PHRASE_TOKENS = max(len(p.split()) for p in
                         _SPECIALTY_PHRASES + _GIFT_PHRASES + _GARMENT_PHRASES)
# approved phrases that legitimately carry an interior stopword — used by the auditor to allow the phrase
# connector ("labor and delivery") while still rejecting an orphan stopword anywhere else.
_CONNECTOR_PHRASES = tuple(p for p in _SPECIALTY_PHRASES + _GIFT_PHRASES + _GARMENT_PHRASES
                           if any(t in _STOPWORDS for t in p.split()))

# every token flowing out of an approved phrase — used so a residual token alongside such an anchor reads
# as a design/slogan modifier, not a standalone search term.
_PHRASE_TOKENS = frozenset(t for p in (_SPECIALTY_PHRASES + _GIFT_PHRASES + _GARMENT_PHRASES)
                           for t in p.split())

# every token with a recognized independent meaning — used to decide phrase-fragment exemption.
_RECOGNIZED_MEANING = (_GARMENT_TOKENS | _COLORS | _MATERIALS | _DECORATION | _PERSONALIZATION | _GENDER
                       | _AUDIENCE_STANDALONE | _RECIPIENT_CONTEXT | _GIFT_TOKENS | _DESCRIPTORS
                       | _OCCASION_TERMS | _ABBREVIATIONS | _QUANTITY | _FIT | _PHRASE_TOKENS)

# concept aliases: inflections/synonyms that share one searchable concept (for concept-level dedup).
_CONCEPT_ALIASES = {
    "women": "women", "womens": "women", "woman": "women", "female": "women", "ladies": "women",
    "girl": "women", "girls": "women",
    "men": "men", "mens": "men", "man": "men", "male": "men", "boy": "men", "boys": "men",
    "nurse": "nurse", "nurses": "nurse", "nursing": "nurse",
    "crewneck": "crewneck", "crewnecks": "crewneck",
    "gift": "gift", "gifts": "gift",
    "sweatshirt": "sweatshirt", "sweatshirts": "sweatshirt",
    "pullover": "pullover", "pullovers": "pullover",
    "hoodie": "hoodie", "hoodies": "hoodie",
}

# vocabulary + inflections used only by the suspicious-concatenation detector (two real words merged with
# no separator, e.g. a data glitch "registerednurse" / "registeredtom"). Legitimate long single words
# ("personalized", "embroidered", "practitioner", "postpartum") are protected by the inflection allowlist
# and by never matching two full known words.
_CONCAT_WORDS = (_AUDIENCE_STANDALONE | _GARMENT_TOKENS | _PERSONALIZATION | _DECORATION | _GENDER
                 | _COLORS | _MATERIALS | _DESCRIPTORS
                 | frozenset({"nurse", "sweatshirt", "hoodie", "gift", "registered", "practitioner"}))
_INFLECTIONS = ("s", "es", "ed", "ing", "er", "ers", "ion", "ions", "al", "y", "ies", "ist", "ists",
                "ment", "ize", "izes", "ized", "izing", "able", "ness")

# --- inclusion reasons (documented vocabulary) ---
INCL_UNIQUE_SYNONYM = "UNIQUE_SYNONYM"
INCL_APPROVED_ABBREVIATION = "APPROVED_ABBREVIATION"
INCL_UNIQUE_LONG_TAIL = "UNIQUE_LONG_TAIL_COMPONENT"
INCL_HIGH_EVIDENCE = "HIGH_EVIDENCE_SUPPORT"
INCL_LOW_VISIBLE_COVERAGE = "LOW_VISIBLE_COVERAGE"
INCL_SPECIALTY_PHRASE = "SPECIALTY_PHRASE_TOKEN"
INCL_AUDIENCE_TERM = "AUDIENCE_TERM"
INCL_PRODUCT_ATTRIBUTE = "VERIFIED_PRODUCT_ATTRIBUTE"
INCL_COMPATIBLE_PRODUCT_TYPE = "COMPATIBLE_PRODUCT_TYPE"
INCL_SUPPORTED_OCCASION = "SUPPORTED_OCCASION"
INCL_GIFT_INTENT = "GIFT_INTENT_PHRASE"
INCL_RECIPIENT_TERM = "SUPPORTED_RECIPIENT_TERM"
INCLUSION_REASONS = (INCL_UNIQUE_SYNONYM, INCL_APPROVED_ABBREVIATION, INCL_UNIQUE_LONG_TAIL,
                     INCL_HIGH_EVIDENCE, INCL_LOW_VISIBLE_COVERAGE, INCL_SPECIALTY_PHRASE,
                     INCL_AUDIENCE_TERM, INCL_PRODUCT_ATTRIBUTE, INCL_COMPATIBLE_PRODUCT_TYPE,
                     INCL_SUPPORTED_OCCASION, INCL_GIFT_INTENT, INCL_RECIPIENT_TERM)

# --- exclusion reasons (documented vocabulary) ---
EXCL_ALREADY_FULLY_COVERED = "ALREADY_FULLY_COVERED"
EXCL_DUPLICATE_TOKEN = "DUPLICATE_TOKEN"
EXCL_DUPLICATE_CONCEPT = "DUPLICATE_SEMANTIC_CONCEPT"
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
# Session 5A.2 verified-attribute + audience/occasion gating.
EXCL_PRODUCT_ATTRIBUTE_UNVERIFIED = "PRODUCT_ATTRIBUTE_UNVERIFIED"
EXCL_COLOR_UNVERIFIED = "COLOR_UNVERIFIED"
EXCL_MATERIAL_UNVERIFIED = "MATERIAL_UNVERIFIED"
EXCL_DECORATION_METHOD_UNVERIFIED = "DECORATION_METHOD_UNVERIFIED"
EXCL_PERSONALIZATION_UNVERIFIED = "PERSONALIZATION_UNVERIFIED"
EXCL_QUANTITY_OFFER_UNVERIFIED = "QUANTITY_OFFER_UNVERIFIED"
EXCL_AUDIENCE_CONTEXT_REQUIRED = "AUDIENCE_CONTEXT_REQUIRED"
EXCL_RECIPIENT_CONTEXT_REQUIRED = "RECIPIENT_CONTEXT_REQUIRED"
EXCL_OCCASION_CONTEXT_REQUIRED = "OCCASION_CONTEXT_REQUIRED"
EXCL_AUDIENCE_CONFLICT = "AUDIENCE_CONFLICT"
EXCL_BROKEN_AUDIENCE_PHRASE = "BROKEN_AUDIENCE_PHRASE"

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
    """A small deterministic stem so plural/variant tokens share a root (for synonym + saturation logic)."""
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


def _concept(token):
    """The dedup concept of a single token: an explicit alias, else the deterministic root."""
    return _CONCEPT_ALIASES.get(token) or concept_root(token)


def _phrase_concept(tokens, override=None):
    """The dedup concept of a phrase: alias-normalized, stopword-free, with a redundant trailing/leading
    'nurse' dropped so 'med surg' and 'med surg nurse' (or 'nurse gift' and 'gifts for nurses') collapse to
    one concept. Distinct specialties keep distinct concepts."""
    if override:
        return override
    core = [_concept(t) for t in tokens if t not in _STOPWORDS]
    stripped = [c for c in core if c != "nurse"]
    return " ".join(stripped) if stripped else " ".join(core)


def _is_abbreviation(token):
    if token in _ABBREVIATIONS:
        return True
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
    or 'registeredtom'."""
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


# ---------------------------------------------------------------- product identity / evidence context
def _publishable_tokens(product_facts, *fields):
    """The tokenized VERIFIED value(s) of the given product-fact fields (empty if none is publishable)."""
    out = set()
    if product_facts is None:
        return out
    for field in fields:
        try:
            val = product_facts.publishable_value(field)
        except Exception:
            val = None
        if not val:
            continue
        text = " ".join(str(x) for x in val) if isinstance(val, (list, tuple)) else str(val)
        out.update(tokenize(text))
    return out


def _garment_identity(product_facts, title, primary_keyword):
    """The garment family the product actually IS, from (1) VERIFIED product facts and (2) the approved
    keyword identity (title + primary keyword)."""
    verified = {t for t in _publishable_tokens(product_facts, "garment_type", "product_type")
                if t in _GARMENT_TOKENS}
    identity = {t for t in tokenize(title) + tokenize(primary_keyword or "") if t in _GARMENT_TOKENS}
    return verified, identity


def _product_type_result(token, verified_garments, identity_garments):
    """Classify a garment/closure token against the product's actual family (PATCH 3)."""
    if token in verified_garments or token in identity_garments:
        return PT_VERIFIED
    id_families = {_GARMENT_FAMILY.get(t) for t in (verified_garments | identity_garments)}
    fam = _GARMENT_FAMILY.get(token)
    if fam is not None and fam in id_families:
        return PT_COMPATIBLE_SYNONYM
    if token in _CLOSURE_ATTRS or fam == "sweatshirt":
        return PT_UNVERIFIED
    return PT_CONFLICT


def _supported_occasions(product_facts, title, primary_keyword):
    """Occasion tokens supported by product context: a VERIFIED occasion fact or an occasion already in the
    approved identity."""
    supported = {t for t in tokenize(f"{title} {primary_keyword or ''}") if t in _OCCASION_TERMS}
    supported.update(t for t in _publishable_tokens(product_facts, "occasion") if t in _OCCASION_TERMS)
    return supported


def _gender_context(product_facts, title, primary_keyword):
    """(approved_female, approved_male, has_gender_evidence) from a VERIFIED audience fact and the approved
    identity. A verified audience/identity that is one gender establishes a conflict for the other."""
    aud = _publishable_tokens(product_facts, "audience")
    ident = set(tokenize(title) + tokenize(primary_keyword or ""))
    female = bool((aud | ident) & _GENDER_FEMALE)
    male = bool((aud | ident) & _GENDER_MALE)
    neutral = bool((aud | ident) & _GENDER_NEUTRAL)
    approved_female = female or neutral
    approved_male = male or neutral
    return approved_female, approved_male, (female or male or neutral)


# ---------------------------------------------------------------- attribute evidence gating
def _attribute_decision(token, ctx):
    """Gate a product-attribute token on VERIFIED facts / approved identity. Returns
    (kind, emit, verification_state, product_fact_fields, reason) or None if the token is not an attribute.

    A color / material / decoration / personalization / quantity / fit term publishes only with a verified
    product fact or explicit approved-identity evidence — never inferred from the niche name or demand."""
    ident = ctx["identity_tokens"]

    def decide(kind, fields, verified_set, unverified_reason, identity_ok=True):
        if token in verified_set:
            return (kind, True, "VERIFIED_FACT", fields, INCL_PRODUCT_ATTRIBUTE)
        if identity_ok and token in ident:
            return (kind, True, "APPROVED_IDENTITY", fields, INCL_PRODUCT_ATTRIBUTE)
        return (kind, False, "UNVERIFIED", fields, unverified_reason)

    if token in _COLORS:
        return decide("color", ["color_options"], ctx["verified_colors"], EXCL_COLOR_UNVERIFIED)
    if token in _MATERIALS:
        return decide("material", ["material", "material_composition"], ctx["verified_materials"],
                      EXCL_MATERIAL_UNVERIFIED)
    if token in _DECORATION:
        return decide("decoration", ["decoration_method"], ctx["verified_decoration"],
                      EXCL_DECORATION_METHOD_UNVERIFIED)
    if token in _PERSONALIZATION:
        return decide("personalization", ["personalization_fields", "decoration_method"],
                      ctx["verified_personalization"], EXCL_PERSONALIZATION_UNVERIFIED)
    if token in _QUANTITY:
        # a quantity/bulk offer is never established by the approved identity — it needs a packaging fact.
        return decide("quantity", ["packaging"], ctx["verified_quantity"], EXCL_QUANTITY_OFFER_UNVERIFIED,
                      identity_ok=False)
    if token in _FIT:
        return decide("fit", ["fit"], ctx["verified_fit"], EXCL_PRODUCT_ATTRIBUTE_UNVERIFIED)
    return None


def _gender_decision(token, ctx):
    """Gate a gender token: allowed when the approved identity/verified audience covers it; a CONFLICT when
    the verified/approved gender is the opposite; otherwise it needs audience context."""
    female = token in _GENDER_FEMALE
    male = token in _GENDER_MALE
    neutral = token in _GENDER_NEUTRAL
    af, am, has_evidence = ctx["approved_female"], ctx["approved_male"], ctx["gender_evidence"]
    if neutral:
        return (True, "APPROVED_IDENTITY") if am and af else (
            (True, "APPROVED_IDENTITY") if has_evidence else (False, EXCL_AUDIENCE_CONTEXT_REQUIRED))
    if female:
        if af:
            return True, "APPROVED_IDENTITY"
        if am:                                         # verified/approved male-only product
            return False, EXCL_AUDIENCE_CONFLICT
        return False, EXCL_AUDIENCE_CONTEXT_REQUIRED
    if male:
        if am:
            return True, "APPROVED_IDENTITY"
        if af:                                         # verified/approved women-only product
            return False, EXCL_AUDIENCE_CONFLICT
        return False, EXCL_AUDIENCE_CONTEXT_REQUIRED
    # "kids" and any other gender/audience token: needs verified audience context.
    return False, EXCL_AUDIENCE_CONTEXT_REQUIRED


# ---------------------------------------------------------------- semantic units
def _semantic_score(unit_type):
    """A unit's semantic-information score by type. Strong meaning (specialty phrase, audience, product
    type, attribute, abbreviation, supported occasion, gender, recipient) scores 2; a generic descriptor,
    gift-intent phrase, or plain long-tail term scores 1; nothing recognized scores 0."""
    if unit_type in ("specialty_phrase", "audience", "product_type", "attribute", "abbreviation",
                     "occasion", "gender", "recipient"):
        return 2
    if unit_type in ("descriptor", "gift", "term"):
        return 1
    return 0


def _new_unit(tokens, unit_type, concept, emit, reason, verification_state="NOT_REQUIRED",
              compatibility_state="NOT_APPLICABLE", product_fact_fields=None, product_type_result=None):
    return {"tokens": list(tokens), "text": " ".join(tokens), "unit_type": unit_type,
            "normalized_concept": concept, "emit": emit, "reason": reason,
            "verification_state": verification_state, "compatibility_state": compatibility_state,
            "product_fact_fields": list(product_fact_fields or []),
            "product_type_result": product_type_result, "semantic_score": _semantic_score(unit_type)}


def _classify_single(token, is_slogan, has_anchor, ctx):
    """Decide the semantic unit for ONE standalone token."""
    # 1) hard malformed / low-information filters.
    if token in _STOPWORDS:
        return _new_unit([token], None, _concept(token), False, EXCL_ORPHAN_STOPWORD)
    if token.isdigit():
        return _new_unit([token], None, token, False, EXCL_UNEXPLAINED_NUMBER)
    if len(token) < MIN_TOKEN_LEN:
        return _new_unit([token], None, token, False, EXCL_LOW_INFORMATION)
    if _is_suspicious_concatenation(token):
        return _new_unit([token], None, token, False, EXCL_SUSPICIOUS_CONCAT)
    if _is_unrecognized_abbreviation(token):
        return _new_unit([token], None, token, False, EXCL_UNRECOGNIZED_ABBREVIATION)
    if token in _FILLER:
        return _new_unit([token], None, token, False, EXCL_LOW_INFORMATION)
    # 2) occasion gating — needs eligible evidence AND product-context support.
    if token in _OCCASION_TERMS:
        if token in ctx["supported_occasions"]:
            return _new_unit([token], "occasion", _concept(token), True, INCL_SUPPORTED_OCCASION,
                             verification_state="SUPPORTED_OCCASION")
        return _new_unit([token], "occasion", _concept(token), False, EXCL_UNSUPPORTED_OCCASION)
    # 3) recipient (graduate) gating — needs an approved graduation occasion.
    if token in _RECIPIENT_CONTEXT:
        if "graduation" in ctx["supported_occasions"]:
            return _new_unit([token], "recipient", _concept(token), True, INCL_RECIPIENT_TERM,
                             verification_state="SUPPORTED_OCCASION")
        return _new_unit([token], "recipient", _concept(token), False, EXCL_RECIPIENT_CONTEXT_REQUIRED)
    # 4) a bare gift token never publishes — it needs a complete gift-intent phrase.
    if token in _GIFT_TOKENS:
        return _new_unit([token], "gift", _concept(token), False, EXCL_RECIPIENT_CONTEXT_REQUIRED)
    # 5) product-type gating (garments/closures must be verified or a compatible synonym).
    if token in _GARMENT_TOKENS:
        ptr = _product_type_result(token, ctx["verified_garments"], ctx["identity_garments"])
        if ptr in (PT_VERIFIED, PT_COMPATIBLE_SYNONYM):
            return _new_unit([token], "product_type", _concept(token), True, INCL_COMPATIBLE_PRODUCT_TYPE,
                             compatibility_state=ptr, product_type_result=ptr)
        reason = EXCL_PRODUCT_TYPE_CONFLICT if ptr == PT_CONFLICT else EXCL_PRODUCT_TYPE_UNVERIFIED
        return _new_unit([token], "product_type", _concept(token), False, reason,
                         compatibility_state=ptr, product_type_result=ptr)
    # 6) approved credential abbreviation.
    if token in _ABBREVIATIONS:
        return _new_unit([token], "abbreviation", _concept(token), True, INCL_APPROVED_ABBREVIATION)
    # 7) gender gating.
    if token in _GENDER:
        ok, res = _gender_decision(token, ctx)
        if ok:
            return _new_unit([token], "gender", _concept(token), True, INCL_AUDIENCE_TERM,
                             verification_state=res)
        return _new_unit([token], "gender", _concept(token), False, res)
    # 8) verified-attribute gating (color / material / decoration / personalization / quantity / fit).
    attr = _attribute_decision(token, ctx)
    if attr is not None:
        _kind, emit, vstate, fields, reason = attr
        return _new_unit([token], "attribute", _concept(token), emit, reason,
                         verification_state=vstate, product_fact_fields=fields)
    # 9) independently meaningful audience term.
    if token in _AUDIENCE_STANDALONE:
        return _new_unit([token], "audience", _concept(token), True, INCL_AUDIENCE_TERM)
    # 10) generic descriptor (weak but searchable).
    if token in _DESCRIPTORS:
        return _new_unit([token], "descriptor", _concept(token), True, INCL_LOW_VISIBLE_COVERAGE)
    # 11) a residual token with no recognized meaning is a broken fragment when it sits inside a slogan
    #     sentence OR alongside a product-identity anchor.
    if is_slogan or has_anchor:
        return _new_unit([token], None, _concept(token), False, EXCL_BROKEN_FRAGMENT)
    # 12) an unanchored, well-formed content token — publishes as a plain long-tail term.
    return _new_unit([token], "term", _concept(token), True, None)


def _phrase_unit(span, kind, ctx):
    """Build a phrase unit (specialty / gift-intent / garment). A specialty phrase is always meaningful; a
    gift phrase is a supported gift-intent unit; a garment phrase is gated by product-type compatibility."""
    if kind == "specialty":
        return _new_unit(span, "specialty_phrase", _phrase_concept(span), True, INCL_SPECIALTY_PHRASE,
                         compatibility_state="COMPATIBLE")
    if kind == "gift":
        return _new_unit(span, "gift", _phrase_concept(span), True, INCL_GIFT_INTENT)
    # garment phrase (crew neck / quarter zip / …) — run product-type gating on its representative token.
    phrase = " ".join(span)
    rep = _GARMENT_PHRASE_REP.get(phrase, span[0])
    concept = "crewneck" if rep == "crewneck" else _phrase_concept(span)
    ptr = _product_type_result(rep, ctx["verified_garments"], ctx["identity_garments"])
    if ptr in (PT_VERIFIED, PT_COMPATIBLE_SYNONYM):
        return _new_unit(span, "product_type", concept, True, INCL_COMPATIBLE_PRODUCT_TYPE,
                         compatibility_state=ptr, product_type_result=ptr)
    reason = EXCL_PRODUCT_TYPE_CONFLICT if ptr == PT_CONFLICT else EXCL_PRODUCT_TYPE_UNVERIFIED
    return _new_unit(span, "product_type", concept, False, reason, compatibility_state=ptr,
                     product_type_result=ptr)


def _build_units(tokens, ctx):
    """Decompose a keyword's tokens into ordered semantic units (greedy longest-phrase match, else single
    token). Every unit publishes wholly and contiguously or not at all."""
    is_slogan = any(t in _SLOGAN_MARKERS for t in tokens)
    has_anchor = any(t in _RECOGNIZED_MEANING or t in ctx["visible_tokens"] for t in tokens)
    units, i, n = [], 0, len(tokens)
    while i < n:
        matched = None
        for length in range(min(_MAX_PHRASE_TOKENS, n - i), 1, -1):
            phrase = " ".join(tokens[i:i + length])
            if phrase in _SPECIALTY_PHRASE_SET:
                matched = (tokens[i:i + length], "specialty"); break
            if phrase in _GIFT_PHRASE_SET:
                matched = (tokens[i:i + length], "gift"); break
            if phrase in _GARMENT_PHRASE_SET:
                matched = (tokens[i:i + length], "garment"); break
        if matched:
            units.append(_phrase_unit(matched[0], matched[1], ctx))
            i += len(matched[0])
        else:
            units.append(_classify_single(tokens[i], is_slogan, has_anchor, ctx))
            i += 1
    return units


# ---------------------------------------------------------------- provenance
def _keyword_id(rec):
    """A deterministic, stable id for a source keyword (12-hex sha of its normalized text)."""
    norm = rec["keyword_normalized"] or rec["keyword_exact"] or ""
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]


def _provenance(rec, source_sha256):
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


def _token_entry(rec, source_sha256, token, byte_cost, reason_value, unit_text, unit_type,
                 normalized_concept, product_type_result=None):
    """A full-provenance INCLUDED-token entry carrying its semantic-unit lineage."""
    e = _provenance(rec, source_sha256)
    e.update({"term": token, "unit": unit_text, "unit_type": unit_type,
              "normalized_concept": normalized_concept, "product_type_result": product_type_result,
              "concept_root": concept_root(token), "byte_cost": byte_cost,
              "inclusion_reason": reason_value})
    return e


def _excluded_entry(rec, term, unit, byte_cost, reason):
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

    def __init__(self, backend_search_terms_string, byte_ceiling, included_terms, included_units,
                 excluded_terms, excluded_summary, excluded_total, excluded_truncated,
                 visible_field_overlap, incremental_coverage, risk_results, source_hashes,
                 max_tokens_per_root, semantic_quality=None, product_type_results=None,
                 audience_occasion_results=None):
        self.backend_search_terms_string = backend_search_terms_string
        self.byte_ceiling = byte_ceiling
        self.bytes_used = len(backend_search_terms_string.encode("utf-8"))
        self.bytes_remaining = byte_ceiling - self.bytes_used
        self.included_terms = included_terms                # flat per-token list, derived from units
        self.included_units = included_units                # the production authority: atomic units
        self.excluded_terms = excluded_terms
        self.excluded_summary = excluded_summary
        self.excluded_total = excluded_total
        self.excluded_truncated = excluded_truncated
        self.visible_field_overlap = visible_field_overlap
        self.incremental_coverage = incremental_coverage
        self.risk_results = risk_results
        self.source_hashes = source_hashes
        self.max_tokens_per_root = max_tokens_per_root
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
            "included_unit_count": len(self.included_units),
            "excluded_count": self.excluded_total,
            "excluded_terms_listed": len(self.excluded_terms),
            "excluded_terms_truncated": self.excluded_truncated,
            "included_units": self.included_units,
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
def optimize_backend(keyword_source, policy=None, title="", bullets=(), description="",
                     item_highlights="", claim_evidence=None, brand_terms=(), prohibited_terms=(),
                     max_tokens_per_root=None, product_facts=None, primary_keyword="",
                     min_incremental_coverage=PRODUCTION_MIN_INCREMENTAL_COVERAGE,
                     min_semantic_score=PRODUCTION_MIN_SEMANTIC_SCORE):
    """Build the byte-safe, provenance-audited, semantically-coherent backend search-terms string as an
    assembly of atomic semantic units.

    min_incremental_coverage : quality-first floor. A unit must contribute at least this many NEW,
                     meaningful (stopword-free, not already covered) tokens to be selected.
    min_semantic_score : non-zero low-quality rejection threshold. A unit whose semantic-information score
                     is below this is not published even when bytes remain. Default 1 keeps every
                     meaningful unit; set 2 to publish only strong units.
    """
    ceiling = policy.backend_byte_ceiling if policy is not None else DEFAULT_BACKEND_BYTE_CEILING
    max_per_root = DEFAULT_MAX_TOKENS_PER_ROOT if max_tokens_per_root is None else max_tokens_per_root
    src_sha = keyword_source.source_sha256

    # visible coverage: tokens/concepts already present in any visible field.
    visible_parts = [title, description, item_highlights] + [str(b) for b in (bullets or [])]
    visible_tokens = set()
    for part in visible_parts:
        visible_tokens.update(tokenize(part))
    visible_roots = {concept_root(t) for t in visible_tokens}
    visible_concepts = {_concept(t) for t in visible_tokens}

    brand_token_set = set()
    for t in list(brand_terms) + list(prohibited_terms):
        brand_token_set.update(tokenize(t))

    # evidence context: verified attributes, product family, supported occasions, gender.
    verified_garments, identity_garments = _garment_identity(product_facts, title, primary_keyword)
    approved_female, approved_male, gender_evidence = _gender_context(product_facts, title, primary_keyword)
    packaging_tokens = _publishable_tokens(product_facts, "packaging")
    ctx = {
        "visible_tokens": visible_tokens,
        "identity_tokens": set(tokenize(title) + tokenize(primary_keyword or "")),
        "verified_garments": verified_garments, "identity_garments": identity_garments,
        "supported_occasions": _supported_occasions(product_facts, title, primary_keyword),
        "verified_colors": {t for t in _publishable_tokens(product_facts, "color_options") if t in _COLORS},
        "verified_materials": {t for t in _publishable_tokens(product_facts, "material",
                                                              "material_composition") if t in _MATERIALS},
        # a verified decoration method / personalization capability approves the whole class (the product
        # IS decorated / IS personalizable) — a specific color/material still needs its own verified value.
        "verified_decoration": (set(_DECORATION)
                                if _publishable_tokens(product_facts, "decoration_method") else set()),
        "verified_personalization": (set(_PERSONALIZATION)
                                     if _publishable_tokens(product_facts, "personalization_fields")
                                     else set()),
        "verified_quantity": set(_QUANTITY) if (packaging_tokens & (_QUANTITY | {"pack", "set", "case"}))
        else set(),
        "verified_fit": {t for t in _publishable_tokens(product_facts, "fit") if t in _FIT},
        "approved_female": approved_female, "approved_male": approved_male,
        "gender_evidence": gender_evidence,
    }

    included_terms, included_units = [], []
    excluded = []
    visible_overlap, incremental = [], []
    summary = {}
    pt_seen = {PT_VERIFIED: set(), PT_COMPATIBLE_SYNONYM: set(), PT_UNVERIFIED: set(), PT_CONFLICT: set()}
    occ_supported, occ_rejected, audience_seen = set(), set(), set()
    risk_buckets = {b: [] for b in ("rejected", "wrong_audience", "wrong_product", "wrong_occasion",
                                    "brand_ip", "trademark_or_ip", "explicit_conflict", "malformed",
                                    "owner_approval_required")}

    def _drop(entry, reason):
        summary[reason] = summary.get(reason, 0) + 1
        if len(excluded) < EXCLUDED_TERMS_CAP:
            excluded.append(entry)

    # 1) ineligible candidates -> risk_results (exact counts + a bounded sample), NOT the token list.
    ineligible = [r for r in keyword_source.keywords if not r["eligible"]]
    for rec in sorted(ineligible, key=lambda r: r["keyword_normalized"]):
        reason, bucket = _ineligible_reason(rec)
        summary[reason] = summary.get(reason, 0) + 1
        risk_buckets[bucket].append(rec["keyword_exact"])

    # 2) eligible candidates, ordered deterministically, contribute their semantic units.
    eligible = keyword_source.eligible_keywords()
    novel = {id(r): _novel_count(r, visible_tokens) for r in eligible}
    ordered = sorted(eligible, key=lambda r: _selection_key(r, novel[id(r)]))

    backend_tokens, backend_set = [], set()
    emitted_concepts = {}                              # normalized_concept -> retained unit text
    root_counts = {}
    bytes_used = 0

    for rec in ordered:
        tokens = tokenize(rec["keyword_normalized"] or rec["keyword_exact"])
        if not tokens:
            _drop(_excl_keyword(rec, EXCL_EMPTY_AFTER_NORMALIZATION), EXCL_EMPTY_AFTER_NORMALIZATION)
            continue
        kwid = _keyword_id(rec)
        units = _build_units(tokens, ctx)
        for uidx, u in enumerate(units):
            ptr = u.get("product_type_result")
            if ptr in pt_seen:
                pt_seen[ptr].add(u["tokens"][0] if len(u["tokens"]) == 1 else u["text"])
            is_single = len(u["tokens"]) == 1
            tok0 = u["tokens"][0]
            unit_cost = (1 if backend_tokens else 0) + len(u["text"].encode("utf-8"))

            # 2a) brand/prohibited screen — never publishes.
            if any(t in brand_token_set for t in u["tokens"]):
                risk_buckets["brand_ip"].append(rec["keyword_exact"])
                _drop(_excl_token(rec, u["text"], unit_cost, EXCL_BRAND_TERM), EXCL_BRAND_TERM)
                continue
            # 2b) classification excluded the unit (malformed / unverified / conflict / fragment / …).
            if not u["emit"]:
                if u["reason"] == EXCL_UNSUPPORTED_OCCASION:
                    occ_rejected.add(tok0)
                _drop(_excl_token(rec, u["text"], unit_cost, u["reason"]), u["reason"])
                continue
            # 2c) concept-level deduplication (aliases/multi-token concepts that add no distinct meaning).
            #     A plain single token whose concept is only its stem stays under DUPLICATE_TOKEN /
            #     ROOT_SATURATED so a shared stem (dog/dogs) is a saturation decision, not an alias merge.
            concept = u["normalized_concept"]
            dedupable = (not is_single) or (tok0 in _CONCEPT_ALIASES)
            if dedupable and concept in emitted_concepts:
                _drop(_excl_token(rec, u["text"], unit_cost, EXCL_DUPLICATE_CONCEPT), EXCL_DUPLICATE_CONCEPT)
                continue
            # 2d) coverage: a unit whose meaning is already fully on the page adds nothing.
            if is_single:
                if tok0 in visible_tokens or _concept(tok0) in visible_concepts:
                    _drop(_excl_token(rec, tok0, unit_cost, EXCL_ALREADY_FULLY_COVERED),
                          EXCL_ALREADY_FULLY_COVERED)
                    visible_overlap.append(tok0)
                    continue
                if tok0 in backend_set:
                    _drop(_excl_token(rec, tok0, unit_cost, EXCL_DUPLICATE_TOKEN), EXCL_DUPLICATE_TOKEN)
                    continue
            # incremental coverage: NEW, meaningful (stopword-free, not already covered) tokens.
            novel_tokens = [t for t in u["tokens"] if t not in _STOPWORDS
                            and t not in visible_tokens and t not in backend_set]
            inc_score = len(novel_tokens)
            if inc_score < min_incremental_coverage:
                _drop(_excl_token(rec, u["text"], unit_cost, EXCL_ALREADY_FULLY_COVERED
                                  if inc_score == 0 else EXCL_LOW_INCREMENTAL_COVERAGE),
                      EXCL_ALREADY_FULLY_COVERED if inc_score == 0 else EXCL_LOW_INCREMENTAL_COVERAGE)
                continue
            # 2e) quality-first: leave bytes unused rather than pad with a weak unit.
            if u["semantic_score"] < min_semantic_score:
                _drop(_excl_token(rec, u["text"], unit_cost, EXCL_LOW_INCREMENTAL_COVERAGE),
                      EXCL_LOW_INCREMENTAL_COVERAGE)
                continue
            # 2f) standalone-root saturation (phrase-interior shared tokens are exempt — integrity wins).
            if is_single:
                root = concept_root(tok0)
                if root_counts.get(root, 0) >= max_per_root:
                    _drop(_excl_token(rec, tok0, unit_cost, EXCL_ROOT_SATURATED), EXCL_ROOT_SATURATED)
                    continue
            # 2g) byte ceiling — the whole unit fits or it does not (never cut a unit).
            if bytes_used + unit_cost > ceiling:
                _drop(_excl_token(rec, u["text"], unit_cost, EXCL_BYTE_LIMIT), EXCL_BYTE_LIMIT)
                continue

            # ---- EMIT the whole unit ----
            final_reason = u["reason"]
            if final_reason is None:                   # a plain term resolves its incremental reason.
                final_reason = _inclusion_reason(rec, tok0, len(u["tokens"]), visible_roots)
            first = not backend_tokens
            for j, t in enumerate(u["tokens"]):
                tcost = (0 if first and j == 0 else 1) + len(t.encode("utf-8"))
                included_terms.append(_token_entry(rec, src_sha, t, tcost, final_reason, u["text"],
                                                   u["unit_type"], concept, product_type_result=ptr))
                incremental.append({"term": t, "inclusion_reason": final_reason, "unit": u["text"],
                                    "unit_type": u["unit_type"], "source_keyword": rec["keyword_exact"],
                                    "root_shared_with_visible": concept_root(t) in visible_roots})
            backend_tokens.extend(u["tokens"])
            backend_set.update(u["tokens"])
            emitted_concepts[concept] = u["text"]
            bytes_used += unit_cost
            if is_single:
                root_counts[concept_root(tok0)] = root_counts.get(concept_root(tok0), 0) + 1
            if u["unit_type"] in ("audience", "specialty_phrase", "gender", "recipient"):
                audience_seen.add(u["text"])
            if u["unit_type"] == "occasion":
                occ_supported.add(tok0)
            included_units.append({
                "semantic_unit_id": f"{kwid}#{uidx}",
                "text": u["text"], "tokens": u["tokens"], "normalized_concept": concept,
                "unit_type": u["unit_type"], "source_keyword": rec["keyword_exact"],
                "source_keyword_id": kwid, "source_sha256": src_sha, "claim_ids": [],
                "product_fact_fields": u["product_fact_fields"],
                "verification_state": u["verification_state"],
                "compatibility_state": u["compatibility_state"], "inclusion_reason": final_reason,
                "semantic_score": u["semantic_score"], "incremental_coverage_score": inc_score,
                "byte_cost": unit_cost,
            })

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
    semantic_quality = {
        "min_incremental_coverage": min_incremental_coverage,
        "min_semantic_score": min_semantic_score,
        "production_min_semantic_score": PRODUCTION_MIN_SEMANTIC_SCORE,
        "production_min_incremental_coverage": PRODUCTION_MIN_INCREMENTAL_COVERAGE,
        "bytes_remaining": ceiling - bytes_used,
        "stopped_before_ceiling": bytes_used < ceiling,
        "excluded_low_information": summary.get(EXCL_LOW_INFORMATION, 0),
        "excluded_orphan_stopwords": summary.get(EXCL_ORPHAN_STOPWORD, 0),
        "excluded_unexplained_numbers": summary.get(EXCL_UNEXPLAINED_NUMBER, 0),
        "excluded_suspicious_concatenations": summary.get(EXCL_SUSPICIOUS_CONCAT, 0),
        "excluded_broken_fragments": summary.get(EXCL_BROKEN_FRAGMENT, 0),
        "excluded_unrecognized_abbreviations": summary.get(EXCL_UNRECOGNIZED_ABBREVIATION, 0),
        "excluded_low_incremental_coverage": summary.get(EXCL_LOW_INCREMENTAL_COVERAGE, 0),
        "excluded_duplicate_concepts": summary.get(EXCL_DUPLICATE_CONCEPT, 0),
        "excluded_unverified_attributes": (summary.get(EXCL_COLOR_UNVERIFIED, 0)
                                           + summary.get(EXCL_MATERIAL_UNVERIFIED, 0)
                                           + summary.get(EXCL_DECORATION_METHOD_UNVERIFIED, 0)
                                           + summary.get(EXCL_PERSONALIZATION_UNVERIFIED, 0)
                                           + summary.get(EXCL_QUANTITY_OFFER_UNVERIFIED, 0)
                                           + summary.get(EXCL_PRODUCT_ATTRIBUTE_UNVERIFIED, 0)),
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
        "gender_evidence": gender_evidence,
        "approved_female": approved_female, "approved_male": approved_male,
    }
    return BackendOptimization(
        backend_search_terms_string=backend_string, byte_ceiling=ceiling,
        included_terms=included_terms, included_units=included_units, excluded_terms=excluded,
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
    """Deterministic scoring order (best first): tier priority -> incremental coverage -> evidence
    strength -> competitor coverage -> rank -> search volume -> byte efficiency -> source priority ->
    stable lexical tie-break. Never selects purely by search volume."""
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
    """A deterministic, testable reason for why a novel plain token adds incremental searchable meaning."""
    if concept_root(token) in visible_roots:
        return INCL_UNIQUE_SYNONYM
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
