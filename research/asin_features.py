#!/usr/bin/env python3
"""
research.asin_features — load product Xray rows, normalize them, and classify
current activity.

Fixes ASIN-004: the old code treated `revenue > 0 OR reviews > 0` as "alive", so
a listing with 900 legacy reviews and zero current sales was a viable NEWBIE.
Review COUNT now earns no activity credit at all; it is history, not demand.
Fixes ASIN-005 at the loader: only product_xray files reach this module.
"""
import os, re
from datetime import datetime

from export_detector import classify_folder, PRODUCT_XRAY

ASIN_RE = re.compile(r"^B0[A-Z0-9]{8}$")

# activity states
ACTIVE_CONFIRMED = "ACTIVE_CONFIRMED"
ACTIVE_SUPPORTED = "ACTIVE_SUPPORTED"
LEGACY_ONLY      = "LEGACY_ONLY"
INACTIVE         = "INACTIVE"

ALIASES = {
    "asin":        ["asin"],
    "title":       ["product details", "title"],
    "brand":       ["brand"],
    "seller":      ["seller"],
    "price":       ["price  $", "price $", "price"],
    "asin_sales":  ["asin sales"],
    "parent_sales": ["parent level sales"],
    "recent_purchases": ["recent purchases"],
    "revenue":     ["asin revenue"],
    "parent_revenue": ["parent level revenue"],
    "bsr":         ["bsr"],
    "rating":      ["ratings", "rating"],
    "reviews":     ["review count", "reviews"],
    "review_velocity": ["review velocity"],
    "fulfillment": ["fulfillment"],
    "creation_date": ["creation date"],
    "sponsored":   ["sponsored"],
    "best_seller": ["best seller"],
    "aba":         ["aba most clicked"],
    "seller_age":  ["seller age (mo)", "seller age mo", "seller age"],
    "buy_box":     ["buy box"],
    "category":    ["category"],
    "images":      ["images"],
    "display_order": ["display order"],
    "active_sellers": ["active sellers"],
}


def _norm(c):
    return re.sub(r"\s+", " ", str(c).replace("﻿", "").strip().lower())


def _num(x):
    """'377,937' / '$12.50' / '' / nan -> float or None. None means unknown, not zero."""
    if x is None:
        return None
    s = re.sub(r"[^0-9.\-]", "", str(x))
    if s in ("", "-", ".", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _text(x):
    s = str(x).strip() if x is not None else ""
    return "" if s.lower() in ("nan", "none", "-") else s


def _colmap(df):
    have = {_norm(c): c for c in df.columns}
    out = {}
    for key, names in ALIASES.items():
        for n in names:
            if n in have:
                out[key] = have[n]
                break
    return out


def load_product_rows(path):
    """Load ONLY product-level Xray exports. -> (rows, provenance)"""
    import pandas as pd
    prov = classify_folder(path)
    rows = []
    for p in prov:
        if p["detected_type"] != PRODUCT_XRAY:
            p["rows_loaded"] = 0
            continue
        try:
            df = pd.read_csv(p["path"]) if p["path"].lower().endswith(".csv") else pd.read_excel(p["path"])
        except Exception as e:
            p["detected_type"], p["reason"], p["rows_loaded"] = "skipped_unreadable", str(e)[:80], 0
            continue
        cm = _colmap(df)
        n = 0
        for _, r in df.iterrows():
            rows.append({"_src": p["file"], "_row": {k: r.get(v) for k, v in cm.items()}})
            n += 1
        p["rows_loaded"] = n
    return rows, prov


def _parse_date(x):
    s = _text(x)
    if not s:
        return None
    for f in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(s[:10], f)
        except ValueError:
            pass
    return None


def normalize_candidates(rows):
    """Validate + normalize; aggregate the same ASIN seen in several exports.

    -> (candidates, rejected) where rejected rows carry an explicit reason code.
    """
    by_asin, rejected = {}, []
    for item in rows:
        r = item["_row"]
        asin = _text(r.get("asin")).upper()
        if not ASIN_RE.match(asin):
            rejected.append({"asin": asin or "(blank)", "source_file": item["_src"],
                             "reason_code": "INVALID_ASIN",
                             "reason": f"not an Amazon ASIN: {asin or 'blank/nan'}"})
            continue
        title = _text(r.get("title"))
        if not title:
            rejected.append({"asin": asin, "source_file": item["_src"],
                             "reason_code": "INVALID_ASIN", "reason": "row has no title"})
            continue
        c = {
            "asin": asin, "title": title,
            "brand": _text(r.get("brand")), "seller": _text(r.get("seller")),
            "price": _num(r.get("price")),
            "asin_sales": _num(r.get("asin_sales")),
            "parent_sales": _num(r.get("parent_sales")),
            "recent_purchases": _num(r.get("recent_purchases")),
            "revenue": _num(r.get("revenue")),
            "parent_revenue": _num(r.get("parent_revenue")),
            "bsr": _num(r.get("bsr")),
            "rating": _num(r.get("rating")),
            "reviews": _num(r.get("reviews")),
            "review_velocity": _num(r.get("review_velocity")),
            "seller_age": _num(r.get("seller_age")),
            "images": _num(r.get("images")),
            "display_order": _num(r.get("display_order")),
            "fulfillment": _text(r.get("fulfillment")).upper(),
            "category": _text(r.get("category")),
            "buy_box": _text(r.get("buy_box")),
            "sponsored": bool(_text(r.get("sponsored"))),
            "best_seller": bool(_text(r.get("best_seller"))),
            "aba": bool(_text(r.get("aba"))),
            "creation_date": _text(r.get("creation_date")),
            "_age_days": None,
            "source_files": [item["_src"]],
        }
        d = _parse_date(r.get("creation_date"))
        if d:
            c["_age_days"] = max(0, (datetime(2026, 7, 16) - d).days)

        prev = by_asin.get(asin)
        if not prev:
            by_asin[asin] = c
        else:
            # Same ASIN in several exports = cross-query evidence. Keep the
            # strongest observation per field; never average away real data.
            for k in ("asin_sales", "revenue", "recent_purchases", "reviews",
                      "review_velocity", "rating", "parent_sales", "parent_revenue"):
                a, b = prev.get(k), c.get(k)
                if b is not None and (a is None or b > a):
                    prev[k] = b
            if c["sponsored"] is False:
                prev["sponsored"] = False           # organic sighting anywhere wins
            if item["_src"] not in prev["source_files"]:
                prev["source_files"].append(item["_src"])
    for c in by_asin.values():
        c["cross_query_count"] = len(c["source_files"])
    return list(by_asin.values()), rejected


def _tm_check():
    """Reuse the toolkit's existing brand/IP screen; degrade safely if absent."""
    try:
        from ip_guard import check as f
        return f
    except Exception:
        try:
            from tm_guard import check as f
            return f
        except Exception:
            return lambda s: ("OK", "")


def _word(text, terms):
    t = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(text).lower())).strip()
    for w in terms:
        wn = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(w).lower())).strip()
        if wn and re.search(r"(?<![a-z0-9])" + re.escape(wn) + r"(?![a-z0-9])", t):
            return wn
    return None


def apply_hard_gates(cands, profile):
    """Spec candidate_hard_gates. -> (qualified, rejected)

    Gates are pass/fail and stated in plain language; they are never traded off
    against a high score (a trademarked listing cannot buy its way in on revenue).
    LEGACY_ONLY/INACTIVE candidates survive here but are marked core_eligible=False
    so Phase 2 can keep them out of B1/B2 while leaving them visible for review.
    """
    tm = _tm_check()
    qualified, rejected = [], []
    for c in cands:
        title = c.get("title", "")
        rej = None

        hit = _word(title, profile.excluded_terms) if profile.excluded_terms else None
        if hit:
            rej = ("EXCLUDED_TERM", f"title contains excluded term '{hit}'")

        if not rej and profile.required_terms:
            missing = [t for t in profile.required_terms if not _word(title, [t])]
            if missing:
                rej = ("INSUFFICIENT_RELEVANCE", f"missing required term(s): {', '.join(missing)}")

        if not rej and profile.audience_terms and not _word(title, profile.audience_terms):
            rej = ("WRONG_AUDIENCE", f"no {profile.audience_key} term in title")

        if not rej and profile.product_family and not _word(title, profile.product_family):
            rej = ("WRONG_PRODUCT_FAMILY", f"not a {profile.family_key or 'target'} product")

        if not rej:
            try:
                st, why = tm(f"{c.get('brand','')} {title}")
            except Exception:
                st, why = "OK", ""
            if str(st).upper() == "HIGH":
                rej = ("TRADEMARK_OR_BRAND_CONTAMINATION", f"protected brand: {why}"[:120])

        if not rej and c.get("activity_state") == INACTIVE:
            rej = ("NO_CURRENT_ACTIVITY", "no current or historical activity evidence")

        if rej:
            rejected.append({**{k: c.get(k) for k in ("asin", "title", "brand", "reviews",
                                                      "revenue", "asin_sales", "activity_state")},
                             "reason_code": rej[0], "reason": rej[1]})
        else:
            # Reviews alone are not demand: keep LEGACY_ONLY out of the core batches.
            c["core_eligible"] = c.get("activity_state") in (ACTIVE_CONFIRMED, ACTIVE_SUPPORTED)
            if not c["core_eligible"]:
                c["core_ineligible_reason"] = "LEGACY_ONLY — reviews are not current-activity proof"
            qualified.append(c)
    return qualified, rejected


def classify_activity(c):
    """-> (state, [signal names]). Review COUNT is never a current-activity signal."""
    primary = []
    if (c.get("asin_sales") or 0) > 0:        primary.append("ASIN_SALES")
    if (c.get("revenue") or 0) > 0:           primary.append("ASIN_REVENUE")
    if (c.get("recent_purchases") or 0) > 0:  primary.append("RECENT_PURCHASES")
    if primary:
        return ACTIVE_CONFIRMED, primary

    secondary = []
    if (c.get("bsr") or 0) > 0:               secondary.append("VALID_BSR")
    if (c.get("review_velocity") or 0) > 0:   secondary.append("REVIEW_VELOCITY")
    if c.get("aba"):                          secondary.append("ABA_MOST_CLICKED")
    if c.get("best_seller"):                  secondary.append("BEST_SELLER_BADGE")
    if (c.get("_age_days") is not None and c["_age_days"] <= 365 and not c.get("sponsored")):
        secondary.append("RECENT_LISTING_ORGANIC")
    if len(secondary) >= 2:                   # "multiple independent" per spec
        return ACTIVE_SUPPORTED, secondary

    if (c.get("reviews") or 0) > 0:
        return LEGACY_ONLY, ["REVIEWS_ONLY_NO_CURRENT_ACTIVITY"]
    return INACTIVE, []
