#!/usr/bin/env python3
"""
research.target_profile — turn a seed (plus optional overrides) into an explicit,
inspectable product-intent profile.

Fixes ASIN-007: personalization and decoration method are now first-class target
attributes, not incidental title words. Fixes the naive anchor: the old code took
the first non-stopword of the seed, so "personalized nurse jacket" anchored on
"personalized" (a stop word it also claimed to strip elsewhere).

Deterministic dictionaries only — no AI call, so the same seed always yields the
same profile (spec: reproducibility).
"""
import re

STOP = set("""a an the and or of to for in on with your you my our we us this that is are be it as at by
from so no not just all more most any one two gift gifts""".split())

# audience/niche -> the tokens that prove it
AUDIENCE_LEXICON = {
    "nurse":       ["nurse", "nurses", "nursing", "rn", "lpn", "bsn", "cna", "icu", "er nurse", "practitioner"],
    "teacher":     ["teacher", "teachers", "teaching", "educator", "paraprofessional"],
    "mom":         ["mom", "mama", "momma", "mother", "mommy"],
    "dad":         ["dad", "papa", "father", "daddy"],
    "grandma":     ["grandma", "grandmother", "nana", "mimi", "granny"],
    "doctor":      ["doctor", "physician", "md", "surgeon"],
    "vet":         ["vet", "veterinarian", "vet tech"],
    "coach":       ["coach", "coaching"],
    "bride":       ["bride", "bridal", "bridesmaid"],
    "student":     ["student", "graduate", "graduation", "grad"],
    "firefighter": ["firefighter", "fireman"],
    "police":      ["police", "officer", "deputy"],
}

# product family -> substitutes a buyer cross-shops
FAMILY_LEXICON = {
    "sweatshirt": ["sweatshirt", "crewneck", "crew neck", "pullover", "hoodie", "hooded",
                   "quarter zip", "1/4 zip", "sweater", "jumper"],
    "jacket":     ["jacket", "bomber", "windbreaker", "coat", "vest", "puffer"],
    "tee":        ["tee", "t-shirt", "tshirt", "t shirt", "shirt", "tank", "longsleeve", "long sleeve"],
    "scrubs":     ["scrub", "scrubs", "scrub top", "scrub set"],
    "bag":        ["bag", "tote", "backpack", "badge holder"],
    "drinkware":  ["mug", "tumbler", "cup", "bottle"],
    "blanket":    ["blanket", "throw"],
    "hat":        ["hat", "cap", "beanie"],
}

PERSONALIZATION_TERMS = ["personalized", "personalised", "custom", "customized", "customised",
                         "monogram", "monogrammed", "with name", "your name", "initials", "name on"]

DECORATION_LEXICON = {
    "machine embroidery": ["embroidered", "embroidery", "stitched", "stitching", "satin stitch"],
    "print":              ["printed", "graphic print", "sublimation", "screen print", "dtg", "vinyl"],
}

OCCASION_TERMS = ["christmas", "birthday", "graduation", "anniversary", "valentine", "mothers day",
                  "fathers day", "halloween", "thanksgiving", "4th of july", "fourth of july",
                  "appreciation", "week", "retirement", "new job"]

RECIPIENT_TERMS = ["for women", "for men", "for her", "for him", "womens", "mens", "unisex",
                   "for kids", "for girls", "for boys"]


def _clean(x):
    """'T-Shirt' -> 't shirt'. Punctuation becomes a space so terms and titles align."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(x).lower())).strip()


def _hits(text, terms):
    """Whole-word matches only.

    Substring matching is wrong here: 'sweatshirt' contains both 'shirt' and
    'tshirt', which made a sweatshirt seed resolve to the tee family.
    """
    t = _clean(text)
    out = []
    for w in terms:
        wn = _clean(w)
        if wn and re.search(r"(?<![a-z0-9])" + re.escape(wn) + r"(?![a-z0-9])", t):
            out.append(w)
    return out


class TargetProfile:
    """Explicit product intent. Every field is inspectable and appears in output."""

    def __init__(self, seed, marketplace="US", audience_key="", audience_terms=None,
                 family_key="", product_family=None, personalization_required=False,
                 personalization_terms=None, decoration_method="", decoration_terms=None,
                 occasion=None, recipient=None, required_terms=None, excluded_terms=None,
                 target_price=None, price_tolerance_percent=40, preferred_fulfillment=None,
                 adjacent_families=None):
        self.seed = seed
        self.marketplace = marketplace
        self.audience_key = audience_key
        self.audience_terms = audience_terms or []
        self.family_key = family_key
        self.product_family = product_family or []
        self.personalization_required = personalization_required
        self.personalization_terms = personalization_terms or PERSONALIZATION_TERMS
        self.decoration_method = decoration_method
        self.decoration_terms = decoration_terms or []
        self.occasion = occasion or []
        self.recipient = recipient or []
        self.required_terms = required_terms or []
        self.excluded_terms = excluded_terms or []
        self.target_price = target_price
        self.price_tolerance_percent = price_tolerance_percent
        self.preferred_fulfillment = preferred_fulfillment or ["MFN", "FBM"]
        self.adjacent_families = adjacent_families or []

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items()}

    def describe(self):
        bits = [f"audience={self.audience_key or '(none)'}", f"family={self.family_key or '(none)'}"]
        if self.personalization_required: bits.append("personalization=REQUIRED")
        if self.decoration_method: bits.append(f"decoration={self.decoration_method}")
        if self.excluded_terms: bits.append(f"excluded={','.join(self.excluded_terms)}")
        return "  ".join(bits)


def parse_target_profile(seed, audience=None, family=None, decoration_method=None,
                         personalization=None, excluded_terms=None, required_terms=None,
                         target_price=None, preferred_fulfillment=None, marketplace="US"):
    """Deterministically derive intent from the seed; every field can be overridden.

    'personalized nurse sweatshirt' ->
        audience nurse | family sweatshirt | personalization REQUIRED | decoration (unset)
    """
    s = str(seed or "").lower()

    # audience: the lexicon entry with the most seed hits (not "first non-stopword")
    aud_key, aud_terms = audience or "", []
    if aud_key:
        aud_terms = AUDIENCE_LEXICON.get(aud_key, [aud_key])
    else:
        best, n = "", 0
        for k, terms in AUDIENCE_LEXICON.items():
            c = len(_hits(s, terms))
            if c > n:
                best, n = k, c
        if best:
            aud_key, aud_terms = best, AUDIENCE_LEXICON[best]

    # product family
    fam_key, fam_terms = family or "", []
    if fam_key:
        fam_terms = FAMILY_LEXICON.get(fam_key, [fam_key])
    else:
        best, n = "", 0
        for k, terms in FAMILY_LEXICON.items():
            c = len(_hits(s, terms))
            if c > n:
                best, n = k, c
        if best:
            fam_key, fam_terms = best, FAMILY_LEXICON[best]

    # personalization: explicit override wins, else the seed says so
    if personalization is None:
        personalization = bool(_hits(s, PERSONALIZATION_TERMS))

    # decoration: override, else inferred from the seed, else unset (scores redistribute)
    dec_method, dec_terms = decoration_method or "", []
    if not dec_method:
        for k, terms in DECORATION_LEXICON.items():
            if _hits(s, terms):
                dec_method = k
                break
    if dec_method:
        dec_terms = DECORATION_LEXICON.get(dec_method, [dec_method])

    return TargetProfile(
        seed=seed, marketplace=marketplace,
        audience_key=aud_key, audience_terms=aud_terms,
        family_key=fam_key, product_family=fam_terms,
        personalization_required=personalization,
        decoration_method=dec_method, decoration_terms=dec_terms,
        occasion=_hits(s, OCCASION_TERMS), recipient=_hits(s, RECIPIENT_TERMS),
        required_terms=[t.strip().lower() for t in (required_terms or []) if t.strip()],
        excluded_terms=[t.strip().lower() for t in (excluded_terms or []) if t.strip()],
        target_price=target_price,
        preferred_fulfillment=preferred_fulfillment or ["MFN", "FBM"],
    )
