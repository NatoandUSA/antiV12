#!/usr/bin/env python3
"""
listing.title_engine — semantic component title builder (ACT-005).

Before this engine build_title() removed duplicate words globally and assembled the title one token at
a time, which damaged natural phrases, could half-represent the strongest keyword, and let concepts
repeat ("Personalized ... personalized"). This engine instead builds titles from COMPLETE semantic
components and detects overlap by concept and phrase containment — never by deleting individual words
and never by slicing a component.

Components, in priority order:
  1. the primary approved product-identifying phrase (the money keyword)  — always
  2. a verified personalization or decoration attribute not already present
  3. a verified audience / recipient not already present
  4. one verified differentiator
  5. brand, only when required

Only the approved product-identity keyword and VERIFIED claims may enter a title; an unverified fact can
never appear. Candidates (CONCISE / EXPANDED) are scored independently and the higher score is
RECOMMENDED — the longer candidate never wins automatically. When a component will not fit, a shorter
safe component is tried; otherwise it is omitted (or TITLE_COMPONENT_TOO_LONG is reported). A word is
never cut. MOBILE_PREVIEW ends on a word boundary and reports whether product identity stays visible.

Public API:
  build_titles(primary_keyword, claim_evidence, policy, ...) -> TitleResult
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- config: concept roots / aliases
# Promotional / symbol hygiene mirrors the listing generator's existing rules.
PROMO = {"best", "cheap", "sale", "free", "guarantee", "guaranteed", "bestseller", "#1", "top",
         "premium", "perfect", "amazing", "luxury", "hot", "deal", "discount"}

GARMENT_NOUNS = ("crewneck sweatshirt", "sweatshirt", "hoodie", "crewneck", "pullover", "sweater",
                 "long sleeve shirt", "long sleeve", "t-shirt", "tshirt", "tee", "tank top", "tank",
                 "jacket", "cardigan", "shirt")

# concept-token roots. Each maps a token onto a concept key used for overlap detection.
PERSONALIZATION_TOKENS = ("personalized", "personalised", "personalize", "personal", "custom",
                          "customized", "customised", "monogram", "monogrammed", "name", "names",
                          "initial", "initials")
DECORATION_TOKENS = ("embroidered", "embroidery", "stitched", "stitch", "satin", "tatami",
                     "printed", "print", "engraved", "applique")
MATERIAL_TOKENS = ("cotton", "polyester", "fleece", "wool", "blend")
OCCASION_TOKENS = ("christmas", "birthday", "graduation", "valentine", "halloween", "mothers",
                   "fathers", "anniversary", "thanksgiving", "easter")
AUDIENCE_TOKENS = ("nurse", "nurses", "rn", "cna", "lpn", "teacher", "teachers", "mom", "mama",
                   "mother", "dad", "papa", "father", "grandma", "grandpa", "coach", "doctor",
                   "vet", "student", "bride", "groom", "firefighter", "police", "practitioner")

# concept keys
C_PRODUCT = "PRODUCT"
C_PERSONALIZATION = "PERSONALIZATION"
C_DECORATION = "DECORATION"
C_MATERIAL = "MATERIAL"
C_OCCASION = "OCCASION"
C_DIFFERENTIATOR = "DIFFERENTIATOR"
C_BRAND = "BRAND"


def _audience_key(token):
    t = token.lower().rstrip("s")
    return f"AUDIENCE:{t}"


def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", str(s).lower())).strip()


def concepts_in_phrase(phrase):
    """The set of concept keys a phrase already represents (product, personalization, decoration,
    audience:<x>, material, occasion). This is how overlap is detected — by concept, not by token."""
    blob = " " + _norm(phrase) + " "
    found = set()
    for noun in GARMENT_NOUNS:
        if f" {noun} " in blob:
            found.add(C_PRODUCT)
            break
    toks = blob.split()
    if any(t in PERSONALIZATION_TOKENS for t in toks):
        found.add(C_PERSONALIZATION)
    if any(t in DECORATION_TOKENS for t in toks):
        found.add(C_DECORATION)
    if any(t in MATERIAL_TOKENS for t in toks):
        found.add(C_MATERIAL)
    if any(t in OCCASION_TOKENS for t in toks):
        found.add(C_OCCASION)
    for t in toks:
        if t in AUDIENCE_TOKENS:
            found.add(_audience_key(t))
    return found


def _titlecase(s):
    small = {"for", "and", "with", "the", "a", "an", "of", "to", "in"}
    out = []
    for i, w in enumerate(s.split()):
        out.append(w if (w.islower() and w in small and i != 0) else w[:1].upper() + w[1:])
    return " ".join(out)


def _sanitize_phrase(phrase):
    """Drop promo words and symbols from a phrase but keep it a COMPLETE phrase (never slice a word)."""
    words = [w for w in re.sub(r"[^A-Za-z0-9 ]", " ", str(phrase)).split() if w.lower() not in PROMO]
    return " ".join(words)


# ---------------------------------------------------------------- component
def _component(component_id, text, source, tier, claim_ids, state, priority, concept_keys):
    return {
        "component_id": component_id,
        "text": _titlecase(text),
        "source": source,
        "keyword_tier": tier,
        "claim_ids": list(claim_ids),
        "verification_state": state,
        "priority": priority,
        "concept_keys": sorted(concept_keys),
        "inclusion_reason": None,
        "excluded": False,
    }


def _candidate_components(primary_keyword, claims, brand_required=False, brand_value=None,
                          audience_hint=None):
    """The ordered candidate components. Identity is always first; the rest are VERIFIED claims whose
    concept is not already carried by the identity phrase."""
    primary = _sanitize_phrase(primary_keyword)
    identity = _component("identity", primary, f"keyword:{primary_keyword}", "PRIMARY", [],
                          "PRODUCT_IDENTITY", 100, concepts_in_phrase(primary) | {C_PRODUCT})
    out = [identity]

    def verified(concept):
        c = claims.claim(concept) if claims else None
        return c if (c and c["publishable"]) else None

    # 2. decoration attribute (embroidered) — a verified decoration/embroidery claim.
    dec = verified("real_machine_embroidery") or verified("decoration_method")
    if dec:
        out.append(_component("decoration", "Embroidered", f"claim:{dec['claim_id']}", None,
                              [dec["claim_id"]], "VERIFIED", 80, {C_DECORATION}))
    # 2b. personalization attribute — only if a verified personalization claim exists.
    pers = verified("personalization_fields")
    if pers:
        out.append(_component("personalization", "Personalized", f"claim:{pers['claim_id']}", None,
                              [pers["claim_id"]], "VERIFIED", 70, {C_PERSONALIZATION}))
    # 3. audience / recipient — a verified recipient claim (may be keyword-approved).
    rec = verified("recipient")
    aud_word = None
    if rec:
        # use the audience noun, not the full sentence, as the title component.
        aud_word = (audience_hint or "").strip() or _audience_noun(rec)
    if aud_word:
        out.append(_component("audience", aud_word, f"claim:{rec['claim_id']}", None,
                              [rec["claim_id"]], "VERIFIED", 60, {_audience_key(aud_word)}))
    # 4. one verified differentiator.
    diff = verified("differentiator")
    if diff and diff.get("proposed_text"):
        dtext = _sanitize_phrase(diff["proposed_text"]).strip(". ")
        if dtext:
            out.append(_component("differentiator", dtext, f"claim:{diff['claim_id']}", None,
                                  [diff["claim_id"]], "VERIFIED", 50, {C_DIFFERENTIATOR}))
    # 5. brand, only when required.
    if brand_required and brand_value:
        out.append(_component("brand", _sanitize_phrase(brand_value), "fact:brand", None, [],
                              "VERIFIED", 40, {C_BRAND}))
    return out


def _audience_noun(recipient_claim):
    for f, ev in (recipient_claim.get("source_evidence") or {}).items():
        v = ev.get("value")
        if v:
            return str(v).split(",")[0].strip()
    return ""


# ---------------------------------------------------------------- assembly + overlap
def _assemble(components, title_limit, allow_extra=True):
    """Assemble a title from complete components, deduping by concept and phrase containment, capped at
    the hard limit. Components are added or omitted whole — a word is never cut."""
    included, excluded = [], []
    represented = set()
    text = ""
    for comp in components:
        keys = set(comp["concept_keys"])
        norm_comp = _norm(comp["text"])
        is_identity = comp["component_id"] == "identity"
        # overlap: concept already represented, or the phrase is already contained in the title.
        if not is_identity:
            if keys & represented:
                excluded.append({**comp, "excluded": True,
                                 "inclusion_reason": f"duplicate concept {sorted(keys & represented)}"})
                continue
            if norm_comp and norm_comp in _norm(text):
                excluded.append({**comp, "excluded": True,
                                 "inclusion_reason": "phrase already contained in title"})
                continue
            if not allow_extra:
                excluded.append({**comp, "excluded": True,
                                 "inclusion_reason": "concise candidate keeps identity only"})
                continue
        cand = (text + " " + comp["text"]).strip()
        if len(cand) > title_limit:
            # never slice — omit the whole component (identity that alone overflows is reported).
            excluded.append({**comp, "excluded": True,
                             "inclusion_reason": f"omitted: would exceed {title_limit}-char hard limit "
                                                 f"(component kept whole, never sliced)"})
            continue
        text = cand
        represented |= keys
        included.append({**comp, "excluded": False, "inclusion_reason": "included"})
    return text, included, excluded


# ---------------------------------------------------------------- scoring
def _score(title_text, included, primary_norm, policy, mobile_cutoff):
    """Score a candidate. Returns (score, breakdown, hard_reject_reason|None)."""
    limit = policy.title_hard_limit
    target = policy.title_recommended_target or limit
    # hard rejects
    if len(title_text) > limit:
        return 0, {}, f"over category title hard limit ({len(title_text)} > {limit})"
    low = title_text.lower()
    promo_hit = sorted({w for w in re.findall(r"[a-z0-9#]+", low) if w in PROMO})
    if promo_hit:
        return 0, {}, f"promotional term(s) in title: {promo_hit}"
    if re.search(r"[^A-Za-z0-9 ]", title_text):
        return 0, {}, "special symbol in title"
    bad = [c for c in included if c["verification_state"] not in ("PRODUCT_IDENTITY", "VERIFIED")]
    if bad:
        return 0, {}, f"unverified component in title: {[c['component_id'] for c in bad]}"

    breakdown = {}
    # mobile product-identity clarity (30): identity component fully visible within the mobile cutoff.
    identity = next((c for c in included if c["component_id"] == "identity"), None)
    id_end = len(identity["text"]) if identity else 0
    breakdown["mobile_identity_clarity"] = 30 if (identity and id_end <= mobile_cutoff) else \
        (18 if identity else 0)
    # approved primary keyword coverage (25): the full normalized primary phrase is present.
    breakdown["primary_keyword_coverage"] = 25 if primary_norm and primary_norm in _norm(title_text) \
        else (10 if primary_norm and primary_norm.split()[0] in _norm(title_text).split() else 0)
    # verified information gain (20): distinct verified concept components beyond identity.
    gain = sum(1 for c in included if c["component_id"] != "identity")
    breakdown["verified_information_gain"] = min(20, gain * 10)
    # natural readability (15): full credit up to the recommended target, decaying toward the limit.
    if len(title_text) <= target:
        breakdown["natural_readability"] = 15
    else:
        over = (len(title_text) - target) / max(1, (limit - target))
        breakdown["natural_readability"] = max(0, round(15 * (1 - over)))
    # duplicate-concept penalty (-10): guard — assembly excludes duplicates, but score it if any slip.
    seen, dup = set(), False
    for c in included:
        if c["component_id"] == "identity":
            continue
        if set(c["concept_keys"]) & seen:
            dup = True
        seen |= set(c["concept_keys"])
    breakdown["duplicate_concept_penalty"] = -10 if dup else 0
    return sum(breakdown.values()), breakdown, None


# ---------------------------------------------------------------- mobile preview
def mobile_preview(title_text, identity_text, cutoff):
    """Truncate at the last word boundary <= cutoff. Reports whether product identity stays visible."""
    if len(title_text) <= cutoff:
        preview = title_text
    else:
        cut = title_text[:cutoff]
        sp = cut.rfind(" ")
        preview = cut[:sp].rstrip() if sp > 0 else cut.rstrip()
    identity_visible = bool(identity_text) and _norm(identity_text) in _norm(preview)
    return {"text": preview, "char_count": len(preview),
            "product_identity_visible": identity_visible,
            "truncated": len(title_text) > cutoff}


# ---------------------------------------------------------------- result
class TitleResult:
    def __init__(self, concise, expanded, recommended, recommended_key, components, excluded,
                 preview, scores, warnings, status):
        self.title_concise = concise
        self.title_expanded = expanded
        self.title_recommended = recommended
        self.recommended_candidate = recommended_key
        self.components = components
        self.excluded_components = excluded
        self.mobile_preview = preview
        self.scores = scores
        self.warnings = warnings
        self.status = status                 # OK | TITLE_COMPONENT_TOO_LONG

    def to_dict(self):
        return {
            "TITLE_CONCISE": self.title_concise,
            "TITLE_EXPANDED": self.title_expanded,
            "TITLE_RECOMMENDED": self.title_recommended,
            "recommended_candidate": self.recommended_candidate,
            "MOBILE_PREVIEW": self.mobile_preview,
            "components": self.components,
            "excluded_components": self.excluded_components,
            "scores": self.scores,
            "status": self.status,
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------- entry point
def build_titles(primary_keyword, claim_evidence, policy, mobile_cutoff=60,
                 brand_required=False, brand_value=None, audience_hint=None,
                 allowed_component_ids=None):
    """Build CONCISE / EXPANDED / RECOMMENDED titles + a word-boundary MOBILE_PREVIEW.

    `claim_evidence` is a claim_evidence.ClaimEvidence (or None); only VERIFIED claims may contribute.
    `policy` is a category_policy_registry.CategoryPolicy (title_hard_limit / title_recommended_target).

    `allowed_component_ids` lets a CALLER restrict which components its own title surface accepts.
    None (the default) keeps every verified component, which is what Phase 5 has always done and
    must keep doing. A caller that passes a set is expressing a surface POLICY -- "this field
    carries market identity only" -- which is a separate question from whether the evidence
    exists, and one this engine does not get to decide on the caller's behalf.

    IDENTITY IS STRUCTURAL, NOT OPTIONAL. It is the product itself, `comps[0]`, and the rest of
    this function indexes it unconditionally. A caller restricting the surface is choosing which
    ATTRIBUTES may appear, never whether the product may be named. So identity survives any
    filter, an empty set means "identity only" rather than "no title", and a set that happens to
    omit "identity" cannot produce an IndexError. Unknown component ids simply match nothing.
    """
    warnings = []
    limit = policy.title_hard_limit
    comps = _candidate_components(primary_keyword, claim_evidence, brand_required, brand_value,
                                  audience_hint)
    if allowed_component_ids is not None:
        comps = comps[:1] + [c for c in comps[1:]
                             if c["component_id"] in allowed_component_ids]
    primary_norm = _norm(_sanitize_phrase(primary_keyword))

    # CONCISE: identity + the single highest-priority extra verified component that fits.
    concise_text, concise_inc, concise_exc = _assemble(comps[:2], limit, allow_extra=True)
    # EXPANDED: identity + every non-overlapping verified component that fits.
    expanded_text, expanded_inc, expanded_exc = _assemble(comps, limit, allow_extra=True)

    status = "OK"
    identity_text = comps[0]["text"]
    if len(identity_text) > limit:
        # the required product-identity component alone will not fit; try a shorter safe form.
        shorter = _shorten_identity(primary_keyword, limit)
        if shorter:
            warnings.append(f"identity shortened to a safe form within {limit} chars")
            identity_text = shorter
            concise_text = expanded_text = shorter
            concise_inc = expanded_inc = [{**comps[0], "text": shorter, "inclusion_reason":
                                           "included (shortened safe identity)"}]
        else:
            status = "TITLE_COMPONENT_TOO_LONG"
            warnings.append(f"TITLE_COMPONENT_TOO_LONG: product identity '{identity_text}' exceeds the "
                            f"{limit}-char hard limit and cannot be shortened safely; not sliced")

    concise_score, concise_bd, concise_rej = _score(concise_text, concise_inc, primary_norm, policy,
                                                    mobile_cutoff)
    expanded_score, expanded_bd, expanded_rej = _score(expanded_text, expanded_inc, primary_norm,
                                                        policy, mobile_cutoff)
    scores = {
        "CONCISE": {"text": concise_text, "length": len(concise_text), "score": concise_score,
                    "breakdown": concise_bd, "hard_reject": concise_rej},
        "EXPANDED": {"text": expanded_text, "length": len(expanded_text), "score": expanded_score,
                     "breakdown": expanded_bd, "hard_reject": expanded_rej},
    }

    # RECOMMENDED: the higher score wins. Ties break toward the SHORTER title (never auto-pick longer).
    candidates = []
    if concise_rej is None:
        candidates.append(("CONCISE", concise_text, concise_score))
    if expanded_rej is None:
        candidates.append(("EXPANDED", expanded_text, expanded_score))
    if candidates:
        recommended_key, recommended_text, _ = max(
            candidates, key=lambda c: (c[2], -len(c[1])))
    else:
        recommended_key, recommended_text = "CONCISE", concise_text
        status = status if status != "OK" else "TITLE_COMPONENT_TOO_LONG"
        warnings.append("no candidate passed hard-reject scoring")

    preview = mobile_preview(recommended_text, identity_text, mobile_cutoff)
    if not preview["product_identity_visible"]:
        warnings.append("product identity is not fully visible in the mobile preview")

    all_components = expanded_inc + [e for e in expanded_exc]
    return TitleResult(concise_text, expanded_text, recommended_text, recommended_key,
                       all_components, expanded_exc, preview, scores, warnings, status)


def _shorten_identity(primary_keyword, limit):
    """Try to reduce the identity to a complete shorter approved phrase (garment noun + one modifier)
    without slicing a word. Returns None if no safe short form fits."""
    primary = _sanitize_phrase(primary_keyword)
    words = primary.split()
    # progressively drop leading words but keep the garment noun; never cut inside a word.
    for start in range(len(words)):
        cand = " ".join(words[start:])
        if C_PRODUCT in concepts_in_phrase(cand) and len(_titlecase(cand)) <= limit:
            return _titlecase(cand)
    return None
