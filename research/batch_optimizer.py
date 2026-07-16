#!/usr/bin/env python3
"""
research.batch_optimizer — Phase 2. Turn a ranked candidate pool into two (rarely
three) strategically distinct batches of ten.

Fixes ASIN-002/003/009. The old picker sorted by revenue and cut the list into
chunks of ten, so Batch 1 could be all-TOP, four ASINs from one brand, and three
title-clone pairs — 6 of 10 Cerebro slots spent on 3 products. Selection here is
constrained greedy maximal-marginal-relevance + local swaps: each slot is chosen
for what it ADDS to the partial batch, and hard concentration limits cannot be
traded away for score.

Never fills a weak slot to reach ten. A short batch with a stated reason is the
correct output when the pool cannot support ten.
"""
from difflib import SequenceMatcher

from asin_features import ACTIVE_CONFIRMED, ACTIVE_SUPPORTED
from asin_clusters import normalize_title

CONSTRAINTS = {
    "batch_size": 10, "minimum_healthy_batch_size": 8,
    "max_same_brand_per_batch": 2, "max_same_brand_all_batches": 4,
    "max_same_seller_per_batch": 2,
    "max_same_title_cluster_per_batch": 1,
    "max_same_variation_family_per_batch": 1,
    "default_batches": 2, "max_batches": 3,
}

BATCH_SPECS = {
    "B1_CORE_MONEY": {
        "priority": 1,
        "purpose": "Closest direct competitors with the strongest current demand and organic evidence.",
        "min_relevance": 82, "target_median_relevance": 86,
        "composition": {"proven": 5, "mid": 3, "emerging": 2},
        "allowed_states": [ACTIVE_CONFIRMED],
        "fallback_states": [ACTIVE_SUPPORTED],
        "min_personalization": 8, "min_decoration": 6,
        "max_sponsored_without_organic": 3,
    },
    "B2_CORE_OPPORTUNITY": {
        "priority": 2,
        "purpose": "Relevant, more attainable, newer or lower-review direct competitors.",
        "min_relevance": 76, "target_median_relevance": 82,
        "composition": {"proven": 3, "emerging": 5, "alt_offer": 2},
        "allowed_states": [ACTIVE_CONFIRMED, ACTIVE_SUPPORTED],
        "fallback_states": [],
        "min_personalization": 7, "min_decoration": 5,
        "max_sponsored_without_organic": 3,
    },
    "B3_CONTROLLED_EXPANSION": {
        "priority": 3,
        "purpose": "One adjacent but coherent micro-cluster.",
        "min_relevance": 65, "target_median_relevance": 78,
        "composition": {},
        "allowed_states": [ACTIVE_CONFIRMED, ACTIVE_SUPPORTED],
        "fallback_states": [],
        "min_personalization": 0, "min_decoration": 0,
        "max_sponsored_without_organic": 3,
    },
}

REDUNDANCY = {"same_brand": 12, "same_seller": 10, "same_title_cluster": 100,
              "same_variation_family": 100, "title_similarity_above_0_88": 20,
              "same_subcluster_overrepresented": 8}

QUALITY_LABELS = [(90, "EXCELLENT"), (82, "STRONG"), (74, "USABLE_WITH_REVIEW"), (0, "REBUILD_BATCH")]

SUBFORMS = ["quarter zip", "1/4 zip", "hoodie", "crewneck", "crew neck", "pullover", "sweater", "sweatshirt"]


# ---------------------------------------------------------------- helpers
def _rel(c):
    return c["component_scores"]["relevance"]


def market_bucket(c, p33, p66):
    """proven / mid / emerging by review moat — deterministic terciles of the pool."""
    r = c.get("reviews") or 0
    if r >= p66:  return "proven"
    if r >= p33:  return "mid"
    return "emerging"


def subform(c):
    t = normalize_title(c.get("title", ""))
    for s in SUBFORMS:
        if s.replace("/", " ") in t or s in t:
            return s
    return "other"


def price_tier(c, p33, p66):
    p = c.get("price")
    if p is None: return "unknown"
    if p >= p66:  return "high"
    if p >= p33:  return "mid"
    return "low"


def _pct(vals, q):
    v = sorted(x for x in vals if x is not None)
    if not v: return 0
    return v[min(len(v) - 1, int(q * (len(v) - 1)))]


def eligible(c, spec, used_asins, allow_fallback=False):
    """Hard eligibility for a batch, independent of what is already selected."""
    if c["asin"] in used_asins:
        return False, "already selected in an earlier batch"
    if _rel(c) < spec["min_relevance"]:
        return False, f"relevance {_rel(c):.0f} below {spec['min_relevance']}"
    states = spec["allowed_states"] + (spec["fallback_states"] if allow_fallback else [])
    if c.get("activity_state") not in states:
        return False, f"activity {c.get('activity_state')} not allowed"
    if not c.get("core_eligible", True) and spec["priority"] in (1, 2):
        return False, "not core-eligible (reviews are not current-activity proof)"
    return True, ""


def blocked_by_batch(c, batch, brand_all_counts):
    """Hard concentration constraints against the partial batch. -> reason or None."""
    brands = [x.get("brand") for x in batch]
    sellers = [x.get("seller") for x in batch]
    if c.get("brand") and brands.count(c["brand"]) >= CONSTRAINTS["max_same_brand_per_batch"]:
        return f"BATCH_BRAND_LIMIT: {c['brand']} already has {CONSTRAINTS['max_same_brand_per_batch']}"
    if c.get("brand") and brand_all_counts.get(c["brand"], 0) >= CONSTRAINTS["max_same_brand_all_batches"]:
        return f"BRAND_LIMIT_ALL_BATCHES: {c['brand']} already has {CONSTRAINTS['max_same_brand_all_batches']}"
    if c.get("seller") and sellers.count(c["seller"]) >= CONSTRAINTS["max_same_seller_per_batch"]:
        return f"BATCH_SELLER_LIMIT: {c['seller']} already has {CONSTRAINTS['max_same_seller_per_batch']}"
    if any(x["title_cluster"] == c["title_cluster"] for x in batch):
        return "DUPLICATE_TITLE_CLUSTER: a near-identical title is already in this batch"
    if any(x["variation_family"] == c["variation_family"] for x in batch):
        return "DUPLICATE_VARIATION_FAMILY: a probable child variation is already in this batch"
    for x in batch:
        if SequenceMatcher(None, normalize_title(x["title"]), normalize_title(c["title"])).ratio() > 0.88:
            return "TITLE_SIMILARITY_ABOVE_0.88: near-duplicate wording already in this batch"
    return None


def marginal_utility(c, batch, spec, need, p33p, p66p):
    """0.70*score + 0.15*diversity + 0.10*missing-bucket + 0.05*model-balance - redundancy."""
    base = c["predicted_keyword_value_score"]

    # controlled diversity: new brand / seller / sub-form / price tier inside one intent
    if batch:
        d = 0.0
        d += 25.0 if c.get("brand") not in [x.get("brand") for x in batch] else 0.0
        d += 25.0 if c.get("seller") not in [x.get("seller") for x in batch] else 0.0
        d += 25.0 if subform(c) not in [subform(x) for x in batch] else 0.0
        d += 25.0 if price_tier(c, p33p, p66p) not in [price_tier(x, p33p, p66p) for x in batch] else 0.0
    else:
        d = 100.0

    # missing composition bucket coverage
    b = c["_bucket"]
    cover = 100.0 if need.get(b, 0) > 0 else 0.0

    bal = c["component_scores"]["business_model_fit"]

    pen = 0.0
    brands = [x.get("brand") for x in batch]
    sellers = [x.get("seller") for x in batch]
    if c.get("brand") and c["brand"] in brands:   pen += REDUNDANCY["same_brand"]
    if c.get("seller") and c["seller"] in sellers: pen += REDUNDANCY["same_seller"]
    if subform(c) in [subform(x) for x in batch] and len(batch) >= 3:
        pen += REDUNDANCY["same_subcluster_overrepresented"]

    return 0.70 * base + 0.15 * d + 0.10 * cover + 0.05 * bal - pen


def batch_quality(batch, spec):
    """0-100 + label + itemized penalties."""
    if not batch:
        return 0.0, "REBUILD_BATCH", [{"reason": "empty batch", "points": 0}]
    n = len(batch)
    rels = [_rel(c) for c in batch]
    mean_rel, min_rel = sum(rels) / n, min(rels)
    activity = sum(c["component_scores"]["current_activity"] for c in batch) / n
    attain = sum(c["component_scores"]["attainability"] for c in batch) / n
    conf = sum(c["component_scores"]["data_confidence"] for c in batch) / n
    diversity = 100.0 * (len({c.get("brand") for c in batch}) / n * 0.4
                         + len({c["title_cluster"] for c in batch}) / n * 0.4
                         + len({subform(c) for c in batch}) / min(n, 4) * 0.2)
    diversity = min(100.0, diversity)

    pens = []
    from collections import Counter
    bc = Counter(c.get("brand") for c in batch if c.get("brand"))
    for b, k in bc.items():
        if k > CONSTRAINTS["max_same_brand_per_batch"]:
            pens.append({"reason": f"brand concentration: {b} x{k}", "points": 10 * (k - 2)})
    tc = Counter(c["title_cluster"] for c in batch)
    for t, k in tc.items():
        if k > 1:
            pens.append({"reason": f"title-clone concentration x{k}", "points": 15 * (k - 1)})
    legacy = sum(1 for c in batch if not c.get("core_eligible", True))
    if legacy:
        pens.append({"reason": f"{legacy} LEGACY_ONLY candidate(s)", "points": 10 * legacy})
    if n < CONSTRAINTS["batch_size"]:
        pens.append({"reason": f"short batch ({n}/{CONSTRAINTS['batch_size']})", "points": 3 * (10 - n)})
    sponsored = sum(1 for c in batch if c.get("sponsored") and not (c.get("asin_sales") or c.get("revenue")))
    if sponsored > spec["max_sponsored_without_organic"]:
        pens.append({"reason": f"{sponsored} sponsored-only candidates", "points": 5 * sponsored})
    if spec["composition"]:
        got = Counter(c["_bucket"] for c in batch)
        for b, want in spec["composition"].items():
            if got.get(b, 0) < want:
                pens.append({"reason": f"composition short: {b} {got.get(b,0)}/{want}", "points": 3})

    score = (0.35 * mean_rel + 0.15 * min_rel + 0.15 * activity + 0.15 * diversity
             + 0.10 * attain + 0.10 * conf - sum(p["points"] for p in pens))
    score = round(max(0.0, min(100.0, score)), 2)
    label = next(l for t, l in QUALITY_LABELS if score >= t)
    return score, label, pens


def build_batch(pool, spec, used_asins, brand_all_counts, p33p, p66p):
    """Constrained greedy MMR, then local swaps. -> (batch, warnings, blocked)"""
    warnings, blocked = [], []
    cands = []
    for c in pool:
        ok, why = eligible(c, spec, used_asins)
        if ok:
            cands.append(c)
        elif why:
            blocked.append({"asin": c["asin"], "reason": why})

    if len(cands) < CONSTRAINTS["minimum_healthy_batch_size"] and spec["fallback_states"]:
        extra = [c for c in pool if eligible(c, spec, used_asins, allow_fallback=True)[0] and c not in cands]
        if extra:
            warnings.append(f"fewer than {CONSTRAINTS['minimum_healthy_batch_size']} "
                            f"{'/'.join(spec['allowed_states'])} candidates — admitting "
                            f"{'/'.join(spec['fallback_states'])} per spec fallback")
            cands += extra

    need = dict(spec["composition"])
    batch = []
    while len(batch) < CONSTRAINTS["batch_size"]:
        best, best_mu = None, None
        for c in cands:
            if c in batch:
                continue
            if blocked_by_batch(c, batch, brand_all_counts):
                continue
            mu = marginal_utility(c, batch, spec, need, p33p, p66p)
            key = (-mu, -c["predicted_keyword_value_score"], c["asin"])   # deterministic
            if best_mu is None or key < best_mu:
                best, best_mu = c, key
        if best is None:
            break
        batch.append(best)
        if need.get(best["_bucket"], 0) > 0:
            need[best["_bucket"]] -= 1

    if len(batch) < CONSTRAINTS["minimum_healthy_batch_size"]:
        reasons = []
        if len(cands) < CONSTRAINTS["batch_size"]:
            reasons.append(f"only {len(cands)} candidates clear relevance >= {spec['min_relevance']} "
                           f"and the activity gate")
        reasons.append("brand/seller/title-cluster/variation-family limits blocked the rest")
        warnings.append("SHORT_BATCH: " + "; ".join(reasons) +
                        ". Not filled with weaker candidates — Xray a cleaner, dedicated search page.")

    # local swaps: keep only strict improvements that preserve every constraint
    q0, _, _ = batch_quality(batch, spec)
    improved = True
    while improved:
        improved = False
        for i, cur in enumerate(list(batch)):
            for cand in cands:
                if cand in batch:
                    continue
                trial = [x for x in batch if x is not cur]
                if blocked_by_batch(cand, trial, brand_all_counts):
                    continue
                trial.append(cand)
                q1, _, _ = batch_quality(trial, spec)
                if q1 > q0 + 1e-9:
                    batch, q0, improved = trial, q1, True
                    break
            if improved:
                break

    batch.sort(key=lambda c: (-c["predicted_keyword_value_score"], c["asin"]))
    return batch, warnings, blocked


def find_expansion_cluster(pool, used_asins, spec):
    """B3 only when >= 8 leftover candidates form ONE coherent sub-form cluster.
    Never leftovers-as-filler (spec forbidden_behavior)."""
    from collections import defaultdict
    groups = defaultdict(list)
    for c in pool:
        if c["asin"] in used_asins or _rel(c) < spec["min_relevance"]:
            continue
        if not c.get("core_eligible", True):
            continue
        groups[subform(c)].append(c)
    best = None
    for k, g in sorted(groups.items()):
        if k == "other" or len(g) < 8:
            continue
        if best is None or len(g) > len(best[1]):
            best = (k, g)
    return best


def optimize_batches(candidates):
    """-> (batches, warnings). Two by default; a third only when coherent."""
    pool = [c for c in candidates]
    revs = [c.get("reviews") for c in pool]
    p33, p66 = _pct(revs, 0.33), _pct(revs, 0.66)
    prices = [c.get("price") for c in pool]
    p33p, p66p = _pct(prices, 0.33), _pct(prices, 0.66)
    for c in pool:
        c["_bucket"] = market_bucket(c, p33, p66)
        c["_subform"] = subform(c)
        c["_price_tier"] = price_tier(c, p33p, p66p)

    # B2's "alternative offer" bucket = a different price tier, judged in-batch
    from collections import Counter
    modal_tier = Counter(c["_price_tier"] for c in pool).most_common(1)[0][0]
    for c in pool:
        if c["_price_tier"] != modal_tier and c["_bucket"] != "proven":
            c["_bucket_b2"] = "alt_offer"
        else:
            c["_bucket_b2"] = c["_bucket"]

    out, warnings, used, brand_all = [], [], set(), Counter()
    for bid in ("B1_CORE_MONEY", "B2_CORE_OPPORTUNITY"):
        spec = BATCH_SPECS[bid]
        if bid == "B2_CORE_OPPORTUNITY":
            for c in pool:
                c["_bucket"] = c["_bucket_b2"]
        batch, w, blocked = build_batch(pool, spec, used, brand_all, p33p, p66p)
        if not batch:
            warnings.append(f"{bid}: no qualified candidate — batch not created")
            continue
        q, label, pens = batch_quality(batch, spec)
        for c in batch:
            used.add(c["asin"])
            # Freeze the bucket this batch actually selected on: B2 re-labels
            # _bucket for its own composition and would otherwise rewrite B1's.
            c["_bucket_final"] = c["_bucket"]
            if c.get("brand"):
                brand_all[c["brand"]] += 1
        out.append({"batch_id": bid, "priority": spec["priority"], "purpose": spec["purpose"],
                    "batch_quality_score": q, "quality_label": label, "batch_penalties": pens,
                    "warnings": w, "members": batch, "spec": spec})
        warnings += [f"{bid}: {x}" for x in w]

    # B3 — only for a coherent adjacent micro-cluster
    spec3 = BATCH_SPECS["B3_CONTROLLED_EXPANSION"]
    found = find_expansion_cluster(pool, used, spec3)
    if found:
        key, group = found
        batch, w, _ = build_batch(group, spec3, used, brand_all, p33p, p66p)
        q, label, pens = batch_quality(batch, spec3) if batch else (0, "REBUILD_BATCH", [])
        if len(batch) < CONSTRAINTS["minimum_healthy_batch_size"]:
            warnings.append("B3 not created: no adjacent cluster reached the minimum healthy size")
        elif label == "REBUILD_BATCH":
            # An optional batch is only worth a Cerebro run if it is actually good.
            warnings.append(f"B3 not created: best adjacent cluster '{key}' scored {q}/100 "
                            f"(REBUILD_BATCH) — an expansion batch must earn its slot")
        else:
            for c in batch:
                used.add(c["asin"])
                c["_bucket_final"] = c["_bucket"]
            out.append({"batch_id": "B3_CONTROLLED_EXPANSION", "priority": 3,
                        "purpose": f"Adjacent coherent micro-cluster: '{key}'",
                        "cluster": key, "batch_quality_score": q, "quality_label": label,
                        "batch_penalties": pens, "warnings": w, "members": batch, "spec": spec3})
    else:
        warnings.append("B3 not created: no coherent adjacent micro-cluster of 8+ candidates "
                        "(leftovers are never used as filler)")
    return out, warnings, used


def validate_constraints(batches):
    """Independent re-check of every hard constraint AFTER selection."""
    from collections import Counter
    results, all_asins, brand_all = [], [], Counter()
    for b in batches:
        m = b["members"]
        bc, sc = Counter(x.get("brand") for x in m if x.get("brand")), Counter(x.get("seller") for x in m if x.get("seller"))
        tc, vf = Counter(x["title_cluster"] for x in m), Counter(x["variation_family"] for x in m)
        all_asins += [x["asin"] for x in m]
        for k, v in bc.items():
            brand_all[k] += v
        results.append({
            "batch_id": b["batch_id"], "size": len(m),
            "max_same_brand": max(bc.values()) if bc else 0,
            "brand_limit_ok": (max(bc.values()) if bc else 0) <= CONSTRAINTS["max_same_brand_per_batch"],
            "max_same_seller": max(sc.values()) if sc else 0,
            "seller_limit_ok": (max(sc.values()) if sc else 0) <= CONSTRAINTS["max_same_seller_per_batch"],
            "max_same_title_cluster": max(tc.values()) if tc else 0,
            "title_cluster_ok": (max(tc.values()) if tc else 0) <= CONSTRAINTS["max_same_title_cluster_per_batch"],
            "max_same_variation_family": max(vf.values()) if vf else 0,
            "variation_family_ok": (max(vf.values()) if vf else 0) <= CONSTRAINTS["max_same_variation_family_per_batch"],
            "min_relevance": round(min((_rel(x) for x in m), default=0), 2),
            "median_relevance": round(sorted(_rel(x) for x in m)[len(m) // 2], 2) if m else 0,
            "min_relevance_ok": all(_rel(x) >= b["spec"]["min_relevance"] for x in m),
            "healthy_size": len(m) >= CONSTRAINTS["minimum_healthy_batch_size"],
        })
    dupes = [a for a, k in Counter(all_asins).items() if k > 1]
    return {
        "per_batch": results,
        "no_duplicate_asin_across_batches": not dupes,
        "duplicate_asins": dupes,
        "max_same_brand_all_batches": max(brand_all.values()) if brand_all else 0,
        "brand_all_batches_ok": (max(brand_all.values()) if brand_all else 0) <= CONSTRAINTS["max_same_brand_all_batches"],
        "batch_count_ok": len(batches) <= CONSTRAINTS["max_batches"],
        "all_constraints_pass": (not dupes
                                 and all(r["brand_limit_ok"] and r["seller_limit_ok"]
                                         and r["title_cluster_ok"] and r["variation_family_ok"]
                                         and r["min_relevance_ok"] for r in results)
                                 and len(batches) <= CONSTRAINTS["max_batches"]
                                 and (max(brand_all.values()) if brand_all else 0) <= CONSTRAINTS["max_same_brand_all_batches"]),
    }
