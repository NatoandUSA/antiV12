#!/usr/bin/env python3
"""
research.keyword_relevance_lean — conflict gates, semantic classes, competitor
coverage and the four lean tiers.

Decision order is fixed and auditable:
    1 validate  ->  2 preserve evidence  ->  3 conflict gates  ->  4 semantic fit
    ->  5 competitor coverage  ->  6 tier  ->  7 sort inside tier

There is deliberately NO blended relevance score in Phase 1. Search volume, Title
Density, CPR, trend, gift intent, personalization and embroidery are evidence —
none of them can rescue a keyword with the wrong audience, product, occasion or
brand, and none of them can change a tier.
"""
import re

from target_keyword_profile import _hits

# semantic classes, highest intent first
DIRECT_EXACT       = "DIRECT_EXACT"
DIRECT_CORE        = "DIRECT_CORE"
METHOD_SUPPORTING  = "METHOD_SUPPORTING"
AUDIENCE_SUPPORTING = "AUDIENCE_SUPPORTING"
APPROVED_ADJACENT  = "APPROVED_ADJACENT"
GENERIC_PRODUCT    = "GENERIC_PRODUCT"
CONFLICTING        = "CONFLICTING"
OFF_TARGET         = "OFF_TARGET"

CLASS_PRIORITY = {DIRECT_EXACT: 7, DIRECT_CORE: 6, METHOD_SUPPORTING: 5,
                  AUDIENCE_SUPPORTING: 4, APPROVED_ADJACENT: 3, GENERIC_PRODUCT: 2,
                  OFF_TARGET: 1, CONFLICTING: 0}

A_CORE, B_SUPPORTING, REVIEW, REJECTED = "A_CORE", "B_SUPPORTING", "REVIEW", "REJECTED"


def _tm_check():
    try:
        from ip_guard import check as f
        return f
    except Exception:
        try:
            from tm_guard import check as f
            return f
        except Exception:
            return lambda s: ("OK", "")


TM = _tm_check()


def product_strategy_signals(kw, profile):
    """Evidence fields. They never rescue a conflict — that is the point of Phase 1."""
    aud = _hits(kw, profile.audience_terms) if profile.audience_terms else []
    fam = _hits(kw, profile.product_family) if profile.product_family else []
    pers = _hits(kw, profile.personalization_terms)
    dec = _hits(kw, profile.decoration_terms) if profile.decoration_terms else []
    rec = _hits(kw, profile.recipient_terms)
    career = _hits(kw, profile.career_occasions)
    gift = _hits(kw, profile.gift_terms)

    adj = []
    for f in profile.approved_adjacent_families:
        from target_profile import FAMILY_LEXICON
        adj += _hits(kw, FAMILY_LEXICON.get(f, [f]))

    conflicts = []
    for k, terms in profile.conflict_audiences.items():
        for h in _hits(kw, terms):
            conflicts.append({"type": "wrong_audience", "term": h, "detail": k})
    for k, terms in profile.conflict_families.items():
        for h in _hits(kw, terms):
            conflicts.append({"type": "wrong_product_family", "term": h, "detail": k})
    for h in _hits(kw, profile.conflict_occasions):
        conflicts.append({"type": "wrong_occasion_or_theme", "term": h, "detail": h})
    for h in _hits(kw, profile.excluded_terms):
        conflicts.append({"type": "excluded_term", "term": h, "detail": h})

    try:
        st, why = TM(str(kw))
    except Exception:
        st, why = "OK", ""
    if str(st).upper() == "HIGH":
        conflicts.append({"type": "brand_or_ip", "term": str(why)[:60], "detail": "protected brand"})

    return {
        "has_audience": bool(aud), "audience_terms": aud,
        "has_product_family": bool(fam), "product_terms": fam,
        "has_personalization": bool(pers), "personalization_terms": pers,
        "personalization_strength": ("explicit" if pers else "none"),
        "has_decoration_method": bool(dec), "decoration_terms": dec,
        "decoration_method": profile.decoration_method if dec else "",
        "has_recipient_or_occasion": bool(rec or career),
        "recipient_or_occasion_terms": rec + career,
        "commercial_event": bool(_hits(kw, profile.conflict_occasions)),
        "gift_intent": bool(gift),
        "approved_adjacent_match": adj,
        "explicit_conflict_terms": conflicts,
        "trademark_status": str(st).upper(),
    }


def classify(kw, sig, profile):
    """-> (semantic_class, reason_codes). Conflicts win before anything else."""
    codes = []
    if sig["explicit_conflict_terms"]:
        for c in sig["explicit_conflict_terms"]:
            codes.append(f"CONFLICT_{c['type'].upper()}:{c['term']}")
        return CONFLICTING, codes

    if not str(kw).strip() or len(str(kw).strip()) < 2:
        return OFF_TARGET, ["MALFORMED_KEYWORD"]

    aud, fam = sig["has_audience"], sig["has_product_family"]
    method = sig["has_personalization"] or sig["has_decoration_method"]
    if aud: codes.append("AUDIENCE_MATCH")
    if fam: codes.append("PRODUCT_FAMILY_MATCH")
    if sig["has_personalization"]: codes.append("PERSONALIZATION_MATCH")
    if sig["has_decoration_method"]: codes.append("DECORATION_METHOD_MATCH")
    if sig["gift_intent"]: codes.append("GIFT_INTENT")
    if sig["has_recipient_or_occasion"]: codes.append("RECIPIENT_OR_OCCASION_MATCH")

    if aud and fam and method:
        return DIRECT_EXACT, codes + ["EXACT_INTENT"]
    if aud and fam:
        return DIRECT_CORE, codes
    if fam and method:
        return METHOD_SUPPORTING, codes + ["AUDIENCE_MISSING"]
    if aud and (sig["has_recipient_or_occasion"] or sig["gift_intent"]):
        return AUDIENCE_SUPPORTING, codes + ["PRODUCT_LANGUAGE_INCOMPLETE"]
    if sig["approved_adjacent_match"] and aud:
        return APPROVED_ADJACENT, codes + ["APPROVED_ADJACENT_PRODUCT"]
    if fam:
        return GENERIC_PRODUCT, codes + ["NO_NICHE_IDENTITY"]
    return OFF_TARGET, codes + ["INSUFFICIENT_TARGET_CONNECTION"]


def coverage_metrics(ranks, denominator):
    """ranks = {asin: rank or None}. Always exposes the denominator."""
    valid = {a: r for a, r in ranks.items() if r is not None and r > 0}
    n = max(denominator, 0)
    def pct(k):
        return round(100.0 * k / n, 1) if n else 0.0
    ranking = len(valid)
    t20 = sum(1 for r in valid.values() if r <= 20)
    t50 = sum(1 for r in valid.values() if r <= 50)
    t100 = sum(1 for r in valid.values() if r <= 100)
    rs = sorted(valid.values())
    return {
        "valid_asin_denominator": n,
        "ranking_asin_count": ranking,
        "simple_competitor_coverage_pct": pct(ranking),
        "top_20_asin_count": t20, "top_20_coverage_pct": pct(t20),
        "top_50_asin_count": t50, "top_50_coverage_pct": pct(t50),
        "top_100_asin_count": t100, "top_100_coverage_pct": pct(t100),
        "best_rank": rs[0] if rs else None,
        "median_rank": rs[len(rs) // 2] if rs else None,
        "mean_rank": round(sum(rs) / len(rs), 1) if rs else None,
    }


def assign_tier(sclass, cov, batch_support, sig):
    """Rule-driven, lexicographic. Volume/TD/CPR/trend NEVER appear here."""
    reasons = []
    if sclass == CONFLICTING:
        return REJECTED, ["HARD_CONFLICT"]
    if sclass == OFF_TARGET:
        return REJECTED, ["OFF_TARGET"]

    ranking = cov["ranking_asin_count"]
    t50 = cov["top_50_asin_count"]
    b1 = batch_support.get("B1_CORE_MONEY", 0)
    b3_only = (batch_support.get("B3_CONTROLLED_EXPANSION", 0) > 0
               and b1 == 0 and batch_support.get("B2_CORE_OPPORTUNITY", 0) == 0)

    if cov["valid_asin_denominator"] == 0 or ranking == 0:
        return REVIEW, ["NO_COMPETITOR_RANK_EVIDENCE"]
    if b3_only:
        return REVIEW, ["BATCH_3_ONLY_ADJACENT"]

    if sclass in (DIRECT_EXACT, DIRECT_CORE):
        # A_CORE needs credible support, not one lucky ASIN.
        if ranking >= 2 and (t50 >= 1 or cov["best_rank"] is not None and cov["best_rank"] <= 50):
            return A_CORE, reasons + ["DIRECT_INTENT_WITH_COMPETITOR_SUPPORT"]
        if ranking == 1:
            return REVIEW, ["SINGLE_ASIN_SUPPORT_ONLY"]
        return B_SUPPORTING, ["DIRECT_INTENT_WEAK_RANK_DEPTH"]

    if sclass in (METHOD_SUPPORTING, AUDIENCE_SUPPORTING, APPROVED_ADJACENT):
        if ranking >= 1:
            return B_SUPPORTING, [f"{sclass}_WITH_SUPPORT"]
        return REVIEW, ["SUPPORTING_NO_RANK_SUPPORT"]

    if sclass == GENERIC_PRODUCT:
        return REVIEW, ["GENERIC_PRODUCT_NEEDS_OWNER_DECISION"]
    return REVIEW, ["UNCLASSIFIED"]


def sort_key(row):
    """Deterministic in-tier order (spec sorting_inside_tier). Search volume is the
    LAST numeric signal and only breaks ties inside an already-approved tier."""
    c, cov, bs = row["semantic_class"], row["coverage"], row["batch_support"]
    return (-CLASS_PRIORITY.get(c, 0),
            -cov["simple_competitor_coverage_pct"],
            -cov["top_50_asin_count"],
            cov["median_rank"] if cov["median_rank"] is not None else 10 ** 6,
            -bs.get("B1_CORE_MONEY", 0),
            -bs.get("B2_CORE_OPPORTUNITY", 0),
            -(row.get("search_volume") or 0),
            row["keyword_normalized"])


def explain(row):
    sig, cov = row["product_strategy_signals"], row["coverage"]
    if row["tier"] == REJECTED:
        cf = sig["explicit_conflict_terms"]
        if cf:
            c = cf[0]
            return f"Rejected — {c['type'].replace('_',' ')}: '{c['term']}'. No volume or personalization can override a conflict."
        return "Rejected — not connected to the target product."
    bits = []
    if sig["has_audience"]: bits.append(f"audience '{sig['audience_terms'][0]}'")
    if sig["has_product_family"]: bits.append(f"product '{sig['product_terms'][0]}'")
    if sig["has_personalization"]: bits.append("personalization")
    if sig["has_decoration_method"]: bits.append("decoration method")
    what = ", ".join(bits) if bits else "partial target match"
    return (f"{row['semantic_class']} — {what}; "
            f"{cov['ranking_asin_count']}/{cov['valid_asin_denominator']} approved ASINs rank"
            + (f", best #{cov['best_rank']}" if cov["best_rank"] else "")
            + (f", {cov['top_50_asin_count']} in top-50" if cov["top_50_asin_count"] else ""))


def shadow_baseline(kw, profile):
    """Non-authoritative QA only. Cannot influence the tier."""
    return {
        "contains_audience": bool(_hits(kw, profile.audience_terms)) if profile.audience_terms else False,
        "contains_product": bool(_hits(kw, profile.product_family)) if profile.product_family else False,
        "contains_personalization": bool(_hits(kw, profile.personalization_terms)),
        "contains_decoration": bool(_hits(kw, profile.decoration_terms)) if profile.decoration_terms else False,
        "contains_explicit_conflict": bool(_hits(kw, profile.conflict_occasions)),
    }
