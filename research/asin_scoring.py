#!/usr/bin/env python3
"""
research.asin_scoring — transparent component scoring for ASIN candidates.

Fixes ASIN-001: relevance is now 45% of the score and revenue is one feature
inside the 20% activity component, not the de-facto sort key. A generic listing
with big revenue can no longer outrank an exact personalized+embroidered match.

    predicted_keyword_value_score =
        0.45*relevance + 0.20*current_activity + 0.15*organic_keyword_signal
      + 0.10*attainability + 0.05*business_model_fit + 0.05*data_confidence
      - penalties

Every component, subcomponent and penalty is recorded on the candidate so the
owner can see exactly why anything ranked where it did. Deterministic: no AI
call, no randomness, explicit tie-breaks.

Missing data never silently becomes zero performance. A subcomponent whose input
is absent for the whole pool is dropped and its weight redistributed across the
subcomponents that DO have data; the loss is charged to data_confidence instead.
"""
import re
from difflib import SequenceMatcher

from asin_features import ACTIVE_CONFIRMED, ACTIVE_SUPPORTED, LEGACY_ONLY, INACTIVE
from asin_clusters import normalize_title

WEIGHTS = {"relevance": 0.45, "current_activity": 0.20, "organic_keyword_signal": 0.15,
           "attainability": 0.10, "business_model_fit": 0.05, "data_confidence": 0.05}


# ---------------------------------------------------------------- helpers
def _clean(x):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(x).lower())).strip()


def _has(text, terms):
    t = _clean(text)
    for w in terms:
        wn = _clean(w)
        if wn and re.search(r"(?<![a-z0-9])" + re.escape(wn) + r"(?![a-z0-9])", t):
            return True
    return False


def _pct_ranker(values):
    """Percentile rank within the clean pool, winsorized at p5/p95 (spec).
    Returns a function value -> 0..1, or None when the pool has no data."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    lo = vals[max(0, int(0.05 * (len(vals) - 1)))]
    hi = vals[min(len(vals) - 1, int(0.95 * (len(vals) - 1)))]
    if hi <= lo:
        return lambda v: 0.5 if v is not None else None

    def rank(v):
        if v is None:
            return None
        v = min(max(v, lo), hi)
        return (v - lo) / (hi - lo)
    return rank


def _blend(parts):
    """parts = [(points, value_0_1_or_None)]. Drops unavailable subcomponents and
    redistributes their weight. -> (score_0_100, coverage_0_1)"""
    avail = [(p, v) for p, v in parts if v is not None]
    if not avail:
        return 0.0, 0.0
    tot = sum(p for p, _ in avail)
    score = sum(p * v for p, v in avail) / tot * 100.0
    coverage = tot / sum(p for p, _ in parts) if parts else 0.0
    return round(score, 2), round(coverage, 2)


# ---------------------------------------------------------------- components
def relevance(c, profile):
    """45% — does this ASIN sell the exact intent we plan to sell?"""
    t = c["title"]
    codes, parts = [], []

    aud = _has(t, profile.audience_terms) if profile.audience_terms else None
    if aud is not None:
        parts.append((20, 1.0 if aud else 0.0))
        if aud: codes.append("EXACT_AUDIENCE")

    fam = _has(t, profile.product_family) if profile.product_family else None
    if fam is not None:
        parts.append((20, 1.0 if fam else 0.0))
        if fam: codes.append("EXACT_PRODUCT_FAMILY")

    if profile.personalization_required:
        p = _has(t, profile.personalization_terms)
        parts.append((15, 1.0 if p else 0.0))
        codes.append("PERSONALIZATION_MATCH" if p else "NO_PERSONALIZATION_IN_TITLE")

    if profile.decoration_terms:
        d = _has(t, profile.decoration_terms)
        parts.append((15, 1.0 if d else 0.0))
        codes.append("DECORATION_METHOD_MATCH" if d else "NO_DECORATION_IN_TITLE")

    sim = SequenceMatcher(None, normalize_title(profile.seed), normalize_title(t)).ratio()
    parts.append((15, sim))

    if profile.occasion or profile.recipient:
        m = _has(t, profile.occasion + profile.recipient)
        parts.append((10, 1.0 if m else 0.0))
        if m: codes.append("OCCASION_OR_RECIPIENT_MATCH")

    if profile.target_price and c.get("price"):
        tol = profile.target_price * profile.price_tolerance_percent / 100.0
        ok = abs(c["price"] - profile.target_price) <= tol
        parts.append((5, 1.0 if ok else 0.0))
        codes.append("PRICE_BAND_MATCH" if ok else "PRICE_OUTSIDE_TARGET")

    s, cov = _blend(parts)
    return s, codes, cov


def current_activity(c, rk):
    """20% — is it selling NOW? Review count contributes nothing here."""
    codes, parts = [], []
    if rk["asin_sales"]:
        parts.append((30, rk["asin_sales"](c.get("asin_sales"))))
        if (c.get("asin_sales") or 0) > 0: codes.append("ACTIVE_SALES")
    if rk["revenue"]:
        parts.append((25, rk["revenue"](c.get("revenue"))))
        if (c.get("revenue") or 0) > 0: codes.append("ACTIVE_REVENUE")
    if rk["recent_purchases"]:
        parts.append((15, rk["recent_purchases"](c.get("recent_purchases"))))
        if (c.get("recent_purchases") or 0) > 0: codes.append("RECENT_PURCHASE_SIGNAL")
    if rk["review_velocity"]:
        parts.append((10, rk["review_velocity"](c.get("review_velocity"))))
    if rk["bsr_inv"]:
        parts.append((10, rk["bsr_inv"](-(c["bsr"]) if c.get("bsr") else None)))
    n = len([x for x in (c.get("asin_sales"), c.get("revenue"), c.get("recent_purchases"),
                         c.get("review_velocity")) if (x or 0) > 0])
    parts.append((10, min(1.0, n / 3.0)))
    s, cov = _blend(parts)
    return s, codes, cov


def organic_keyword_signal(c, rk):
    """15% — organic authority. Display Order / ABA / Best Seller are absent from
    this export, so this blends only the evidence that exists (cross-query
    stability, organic placement, Buy Box); data_confidence absorbs the gap."""
    codes, parts = [], []
    if rk["display_order_inv"]:
        parts.append((40, rk["display_order_inv"](-(c["display_order"]) if c.get("display_order") else None)))
    parts.append((25, min(1.0, (c.get("cross_query_count", 1) - 1) / 2.0)))
    if c.get("cross_query_count", 1) > 1:
        codes.append("CROSS_QUERY_STABILITY")
    parts.append((15, 1.0 if c.get("aba") else 0.0) if any(x.get("aba") for x in rk["_pool"]) else (15, None))
    parts.append((10, 1.0 if c.get("best_seller") else 0.0) if any(x.get("best_seller") for x in rk["_pool"]) else (10, None))
    parts.append((10, 0.0 if c.get("sponsored") else 1.0))
    if not c.get("sponsored"):
        codes.append("ORGANIC_PLACEMENT")
    else:
        codes.append("SPONSORED_ONLY_RISK")
    s, cov = _blend(parts)
    return s, codes, cov


def attainability(c, rk, med_reviews):
    """10% — proves demand without an impossible review moat."""
    codes, parts = [], []
    rev_ct = c.get("reviews") or 0
    if rk["sales_per_review"]:
        v = (c["asin_sales"] / rev_ct) if (c.get("asin_sales") and rev_ct > 0) else (
            c.get("asin_sales") if rev_ct == 0 else None)
        parts.append((30, rk["sales_per_review"](v)))
    if rk["rev_per_review"]:
        v = (c["revenue"] / rev_ct) if (c.get("revenue") and rev_ct > 0) else (
            c.get("revenue") if rev_ct == 0 else None)
        parts.append((20, rk["rev_per_review"](v)))
    low_rev_selling = 1.0 if ((c.get("asin_sales") or 0) > 0 and rev_ct < med_reviews) else 0.0
    parts.append((20, low_rev_selling))
    if low_rev_selling:
        codes.append("LOW_REVIEW_CURRENT_SALES")
    if c.get("_age_days") is not None:
        new_selling = 1.0 if (c["_age_days"] <= 540 and (c.get("asin_sales") or 0) > 0) else 0.0
        parts.append((20, new_selling))
    parts.append((10, 1.0 if c.get("fulfillment") in ("MFN", "FBM") else 0.0))
    s, cov = _blend(parts)
    return s, codes, cov


def business_model_fit(c, profile):
    """5% — how much this competitor resembles our FBM/personalized operation."""
    codes, parts = [], []
    ful = c.get("fulfillment") or ""
    if ful:
        m = ful in [f.upper() for f in profile.preferred_fulfillment]
        parts.append((25, 1.0 if m else 0.0))
        if m: codes.append("FBM_MODEL_MATCH")
    parts.append((25, 1.0 if _has(c["title"], profile.personalization_terms) else 0.0))
    if profile.decoration_terms:
        parts.append((25, 1.0 if _has(c["title"], profile.decoration_terms) else 0.0))
    if profile.target_price and c.get("price"):
        tol = profile.target_price * profile.price_tolerance_percent / 100.0
        parts.append((25, 1.0 if abs(c["price"] - profile.target_price) <= tol else 0.0))
    s, cov = _blend(parts)
    return s, codes, cov


def data_confidence(c):
    """5% — how much of this row is real. Absent fields land here, not in perf."""
    parts = [
        (20, 1.0 if (c.get("title") and c.get("brand") and c.get("asin")) else 0.5),
        (20, 1.0 if (c.get("asin_sales") is not None or c.get("revenue") is not None) else 0.0),
        (15, 1.0 if c.get("bsr") else 0.0),
        (10, 1.0 if (c.get("reviews") is not None and c.get("rating") is not None) else 0.0),
        (10, 1.0 if (c.get("creation_date") or c.get("seller_age") is not None) else 0.0),
        (10, 1.0 if c.get("fulfillment") else 0.0),
        (15, 1.0 if c.get("cross_query_count", 1) > 1 else 0.5),
    ]
    s, cov = _blend(parts)
    return s, [], cov


def penalties(c):
    """Explicit, itemized deductions (Phase 1 scope: per-candidate only —
    cross-batch duplicate penalties belong to the Phase 2 optimizer)."""
    out = []
    if c.get("activity_state") == LEGACY_ONLY:
        out.append(("LEGACY_ONLY activity state", 30))
    if c.get("activity_state") == INACTIVE:
        out.append(("INACTIVE - no current or historical evidence", 30))
    if c.get("sponsored") and not (c.get("asin_sales") or c.get("revenue")):
        out.append(("sponsored-only placement with no organic support", 15))
    if c.get("title_cluster_size", 1) > 1:
        out.append((f"near-identical title shared with {c['title_cluster_size']-1} other listing(s)", 25))
    if c.get("rating") is not None and c["rating"] < 3.5:
        out.append(("rating below 3.5", 5))
    return out


# ---------------------------------------------------------------- driver
def score_candidates(cands, profile):
    """Attach component_scores, reason_codes, penalties and the total to every
    candidate. Deterministic ordering with explicit tie-breaks."""
    rk = {
        "asin_sales":      _pct_ranker([c.get("asin_sales") for c in cands]),
        "revenue":         _pct_ranker([c.get("revenue") for c in cands]),
        "recent_purchases": _pct_ranker([c.get("recent_purchases") for c in cands]),
        "review_velocity": _pct_ranker([c.get("review_velocity") for c in cands]),
        "bsr_inv":         _pct_ranker([-(c["bsr"]) for c in cands if c.get("bsr")]),
        "display_order_inv": _pct_ranker([-(c["display_order"]) for c in cands if c.get("display_order")]),
        "sales_per_review": _pct_ranker([(c["asin_sales"] / c["reviews"]) for c in cands
                                         if c.get("asin_sales") and c.get("reviews")]),
        "rev_per_review":  _pct_ranker([(c["revenue"] / c["reviews"]) for c in cands
                                        if c.get("revenue") and c.get("reviews")]),
        "_pool": cands,
    }
    revs = sorted(c["reviews"] for c in cands if c.get("reviews") is not None)
    med_reviews = revs[len(revs) // 2] if revs else 0

    for c in cands:
        rel, rc, rcov = relevance(c, profile)
        act, ac, acov = current_activity(c, rk)
        org, oc, ocov = organic_keyword_signal(c, rk)
        att, tc, tcov = attainability(c, rk, med_reviews)
        bmf, bc, bcov = business_model_fit(c, profile)
        dcf, _, dcov = data_confidence(c)
        pens = penalties(c)
        total = (WEIGHTS["relevance"] * rel + WEIGHTS["current_activity"] * act
                 + WEIGHTS["organic_keyword_signal"] * org + WEIGHTS["attainability"] * att
                 + WEIGHTS["business_model_fit"] * bmf + WEIGHTS["data_confidence"] * dcf
                 - sum(p for _, p in pens))
        c["component_scores"] = {"relevance": rel, "current_activity": act,
                                 "organic_keyword_signal": org, "attainability": att,
                                 "business_model_fit": bmf, "data_confidence": dcf}
        c["component_coverage"] = {"relevance": rcov, "current_activity": acov,
                                   "organic_keyword_signal": ocov, "attainability": tcov,
                                   "business_model_fit": bcov, "data_confidence": dcov}
        c["reason_codes"] = rc + ac + oc + tc + bc
        c["penalties"] = [{"reason": r, "points": p} for r, p in pens]
        c["predicted_keyword_value_score"] = round(max(0.0, min(100.0, total)), 2)

    # deterministic: total, relevance, organic, activity, then ASIN
    cands.sort(key=lambda c: (-c["predicted_keyword_value_score"],
                              -c["component_scores"]["relevance"],
                              -c["component_scores"]["organic_keyword_signal"],
                              -c["component_scores"]["current_activity"],
                              c["asin"]))
    for i, c in enumerate(cands, 1):
        c["global_rank"] = i
    return cands
