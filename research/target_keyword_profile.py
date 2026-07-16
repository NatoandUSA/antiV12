#!/usr/bin/env python3
"""
research.target_keyword_profile — dynamic, versioned keyword intent profile.

Replaces the old relevance model, which derived intent from the PROJECT NAME:
a project called "T1" produced no tokens over two letters, so every one of 7,512
keywords was silently marked irrelevant.

Nothing niche-specific is hardcoded in the engine. Conflicts are DERIVED from the
same lexicons the target is derived from: every audience that is not ours is a
wrong audience, every product family that is not ours (and not an approved
adjacent) is a wrong product.
"""
import os, json, re

from target_profile import (parse_target_profile, AUDIENCE_LEXICON, FAMILY_LEXICON,
                            PERSONALIZATION_TERMS, DECORATION_LEXICON, RECIPIENT_TERMS, _clean)

PROFILE_VERSION = "1.0.0"

# Holiday / patriotic themes that signal a DIFFERENT product intent unless the
# target explicitly asks for them ("halloween sweatshirt" is not our product).
THEME_CONFLICTS = ["halloween", "christmas", "xmas", "valentine", "valentines", "thanksgiving",
                   "easter", "st patricks", "st patrick", "4th of july", "fourth of july",
                   "independence day", "usa", "u s a", "america", "american flag", "patriotic",
                   "anniversary", "new year", "hanukkah", "birthday"]

# Career/professional occasions that BELONG to an audience rather than competing
# with it — these are supporting intent, never conflicts.
CAREER_OCCASIONS = ["graduation", "grad", "appreciation", "week", "retirement", "new job",
                    "practitioner", "school", "student", "orientation", "preceptor"]

GIFT_TERMS = ["gift", "gifts", "gift for", "present", "gift idea"]


from functools import lru_cache

_PAT_CACHE = {}


@lru_cache(maxsize=8192)
def _clean_cached(s):
    """One keyword is scanned against ~200 term groups; without this it was
    re-cleaned 200 times per keyword (28.6 ms/kw -> 273s on a 9.5k-row folder)."""
    return _clean(s)


def _pattern(terms_key):
    """One compiled alternation per term group, built once per process."""
    p = _PAT_CACHE.get(terms_key)
    if p is None:
        parts = sorted({_clean(w) for w in terms_key if _clean(w)}, key=len, reverse=True)
        p = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(x) for x in parts) + r")(?![a-z0-9])") if parts else False
        _PAT_CACHE[terms_key] = p
    return p


def _hits(text, terms):
    """Whole-word matches of `terms` in `text`. Same semantics as before, cached."""
    p = _pattern(tuple(terms))
    if not p:
        return []
    seen, out = set(), []
    for m in p.findall(_clean_cached(str(text))):
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


class KeywordProfile:
    def __init__(self, base, approved_adjacent=None, required_concepts=None,
                 extra_excluded=None):
        self.version = PROFILE_VERSION
        self.seed = base.seed
        self.marketplace = base.marketplace
        self.audience_key = base.audience_key
        self.audience_terms = base.audience_terms
        self.family_key = base.family_key
        self.product_family = base.product_family
        self.personalization_required = base.personalization_required
        self.personalization_terms = PERSONALIZATION_TERMS
        self.decoration_method = base.decoration_method
        self.decoration_terms = base.decoration_terms
        self.recipient_terms = RECIPIENT_TERMS
        self.gift_terms = GIFT_TERMS
        self.target_occasions = list(base.occasion or [])
        self.career_occasions = CAREER_OCCASIONS
        self.approved_adjacent_families = list(approved_adjacent or [])
        self.required_concepts = [c.lower() for c in (required_concepts or [])]

        # ---- conflicts, derived (never hardcoded to one niche)
        self.conflict_audiences = {k: v for k, v in AUDIENCE_LEXICON.items()
                                   if k != self.audience_key}
        allowed_fams = {self.family_key} | set(self.approved_adjacent_families)
        self.conflict_families = {k: v for k, v in FAMILY_LEXICON.items()
                                  if k not in allowed_fams and k}
        self.conflict_occasions = [t for t in THEME_CONFLICTS
                                   if not _hits(self.seed, [t])]
        self.excluded_terms = [t.lower() for t in (extra_excluded or [])]

    def to_dict(self):
        d = dict(self.__dict__)
        d["conflict_audiences"] = sorted(self.conflict_audiences)
        d["conflict_families"] = sorted(self.conflict_families)
        return d

    def describe(self):
        return (f"v{self.version} seed='{self.seed}' audience={self.audience_key or '—'} "
                f"family={self.family_key or '—'} "
                f"personalization={'required' if self.personalization_required else 'optional'} "
                f"decoration={self.decoration_method or '—'} "
                f"conflict_audiences={len(self.conflict_audiences)} "
                f"conflict_families={len(self.conflict_families)} "
                f"conflict_themes={len(self.conflict_occasions)}")


def load_keyword_profile(folder=None, seed=None, audience=None, family=None,
                         decoration_method=None, approved_adjacent=None,
                         required_concepts=None, excluded_terms=None):
    """Inherit intent from ASIN-BATCHES.json when present (the batch that produced
    the Cerebro data IS the intent), otherwise from the seed. Overrides always win."""
    inherited = {}
    if folder:
        p = os.path.join(folder, "ASIN-BATCHES.json")
        if os.path.exists(p):
            try:
                tp = json.load(open(p, encoding="utf-8")).get("target_profile", {})
                inherited = {"seed": tp.get("seed"), "audience": tp.get("audience_key"),
                             "family": tp.get("family_key"),
                             "decoration_method": tp.get("decoration_method")}
            except Exception:
                pass
    seed = seed or inherited.get("seed")
    if not seed:
        raise ValueError("no seed: pass --seed or run the ASIN batch stage first "
                         "(relevance is decided from the seed, never the project name)")
    base = parse_target_profile(
        seed,
        audience=audience or inherited.get("audience") or None,
        family=family or inherited.get("family") or None,
        decoration_method=decoration_method or inherited.get("decoration_method") or None)
    return KeywordProfile(base, approved_adjacent=approved_adjacent,
                          required_concepts=required_concepts, extra_excluded=excluded_terms)
