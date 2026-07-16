#!/usr/bin/env python3
"""
research.asin_candidates — Phase 1 orchestrator.

Xray folder -> classified files -> normalized candidates -> activity states ->
hard gates -> clusters -> component scores -> ASIN-CANDIDATES.json / .csv

This is the ranked candidate POOL only. Building batches from it is Phase 2;
until then the pool is deliberately not chunked into batches.

Usage:
    python research/asin_candidates.py <folder> --seed "personalized nurse sweatshirt"
        [--decoration-method "machine embroidery"] [--audience nurse] [--family sweatshirt]
        [--exclude a,b] [--require a,b] [--target-price 32] [--out DIR]
"""
import sys as _sys, os as _os
for _p in (_os.path.dirname(_os.path.abspath(__file__)),
           _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "compliance")):
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.insert(0, _p)

import os, json, csv, argparse, warnings
warnings.filterwarnings("ignore")

from export_detector import PRODUCT_XRAY
from asin_features import (load_product_rows, normalize_candidates, classify_activity,
                           apply_hard_gates)
from asin_clusters import annotate_clusters
from asin_scoring import score_candidates, WEIGHTS
from target_profile import parse_target_profile

SCHEMA_VERSION = "1.0.0"
TOOL_VERSION = "2.4.0-rc2"


def build_candidates(path, profile):
    """-> dict with provenance, summary, candidates, rejected. Deterministic."""
    rows, prov = load_product_rows(path)
    cands, invalid = normalize_candidates(rows)
    for c in cands:
        c["activity_state"], c["activity_signals"] = classify_activity(c)
    qualified, gated_out = apply_hard_gates(cands, profile)
    annotate_clusters(qualified)
    score_candidates(qualified, profile)

    rejected = invalid + gated_out
    loaded = sum(p.get("rows_loaded", 0) for p in prov)
    return {
        "schema_version": SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "phase": "Phase 1 — individual ASIN ranking",
        "seed": profile.seed,
        "marketplace": profile.marketplace,
        "scoring_weights": WEIGHTS,
        "target_profile": profile.to_dict(),
        "source_files": [{"file": p["file"], "detected_type": p["detected_type"],
                          "rows_loaded": p.get("rows_loaded", 0), "reason": p["reason"]}
                         for p in prov],
        "summary": {
            "files_seen": len(prov),
            "files_loaded_as_product_xray": sum(1 for p in prov if p["detected_type"] == PRODUCT_XRAY),
            "rows_loaded": loaded,
            "unique_valid_asins": len(cands),
            "hard_rejected": len(rejected),
            "qualified_candidates": len(qualified),
            "core_eligible": sum(1 for c in qualified if c.get("core_eligible")),
            "redundant_group_members": sum(1 for c in qualified if c.get("title_cluster_size", 1) > 1),
        },
        "candidates": qualified,
        "rejected_candidates": rejected,
    }


CSV_COLS = ["global_rank", "asin", "predicted_keyword_value_score", "activity_state",
            "core_eligible", "relevance", "current_activity", "organic_keyword_signal",
            "attainability", "business_model_fit", "data_confidence", "penalty_points",
            "brand", "seller", "title_cluster", "variation_family", "title_cluster_size",
            "cross_query_count", "asin_sales", "revenue", "reviews", "rating",
            "fulfillment", "sponsored", "price", "reason_codes", "title"]


def write_outputs(result, outdir):
    os.makedirs(outdir, exist_ok=True)
    jp = os.path.join(outdir, "ASIN-CANDIDATES.json")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)

    cp = os.path.join(outdir, "ASIN-CANDIDATES.csv")
    with open(cp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS + ["selection_status", "reject_reason"])
        w.writeheader()
        for c in result["candidates"]:
            cs = c["component_scores"]
            w.writerow({
                "global_rank": c["global_rank"], "asin": c["asin"],
                "predicted_keyword_value_score": c["predicted_keyword_value_score"],
                "activity_state": c["activity_state"], "core_eligible": c.get("core_eligible"),
                **{k: cs[k] for k in cs},
                "penalty_points": sum(p["points"] for p in c["penalties"]),
                "brand": c.get("brand"), "seller": c.get("seller"),
                "title_cluster": c["title_cluster"], "variation_family": c["variation_family"],
                "title_cluster_size": c["title_cluster_size"],
                "cross_query_count": c.get("cross_query_count"),
                "asin_sales": c.get("asin_sales"), "revenue": c.get("revenue"),
                "reviews": c.get("reviews"), "rating": c.get("rating"),
                "fulfillment": c.get("fulfillment"), "sponsored": c.get("sponsored"),
                "price": c.get("price"), "reason_codes": "|".join(c["reason_codes"]),
                "title": c["title"], "selection_status": "QUALIFIED", "reject_reason": "",
            })
        for r in result["rejected_candidates"]:
            w.writerow({"asin": r.get("asin"), "title": r.get("title"),
                        "brand": r.get("brand"), "reviews": r.get("reviews"),
                        "revenue": r.get("revenue"), "asin_sales": r.get("asin_sales"),
                        "activity_state": r.get("activity_state"),
                        "selection_status": "REJECTED", "reject_reason": r["reason_code"] + ": " + r["reason"]})
    return jp, cp


def main():
    ap = argparse.ArgumentParser(description="Phase 1 — ranked ASIN candidate pool")
    ap.add_argument("path", help="folder containing Helium 10 Xray exports")
    ap.add_argument("--seed", required=True)
    ap.add_argument("--audience", default=None)
    ap.add_argument("--family", default=None)
    ap.add_argument("--decoration-method", default=None)
    ap.add_argument("--personalization", default=None, choices=["true", "false"])
    ap.add_argument("--exclude", default="")
    ap.add_argument("--require", default="")
    ap.add_argument("--target-price", type=float, default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    profile = parse_target_profile(
        a.seed, audience=a.audience, family=a.family, decoration_method=a.decoration_method,
        personalization=(None if a.personalization is None else a.personalization == "true"),
        excluded_terms=[x for x in a.exclude.split(",") if x.strip()],
        required_terms=[x for x in a.require.split(",") if x.strip()],
        target_price=a.target_price)

    res = build_candidates(a.path, profile)
    jp, cp = write_outputs(res, a.out or a.path)

    s = res["summary"]
    print(f"TARGET: {profile.describe()}")
    print(f"\nFILES: {s['files_loaded_as_product_xray']}/{s['files_seen']} loaded as product Xray")
    for f in res["source_files"]:
        mark = "LOAD" if f["detected_type"] == PRODUCT_XRAY else "SKIP"
        print(f"  {mark} {f['file'][:54]:<56} {f['detected_type']} ({f['rows_loaded']} rows)")
    print(f"\nCANDIDATES: {s['rows_loaded']} rows -> {s['unique_valid_asins']} unique ASINs "
          f"-> {s['qualified_candidates']} qualified ({s['hard_rejected']} rejected)")
    print(f"  core-eligible: {s['core_eligible']}   in redundant groups: {s['redundant_group_members']}")
    print(f"\nTOP 10 (relevance-led):")
    for c in res["candidates"][:10]:
        cs = c["component_scores"]
        print(f"  {c['global_rank']:>2}. {c['asin']}  score {c['predicted_keyword_value_score']:>5}  "
              f"rel {cs['relevance']:>3.0f}  act {cs['current_activity']:>3.0f}  "
              f"{c['activity_state']:<17} {c['title'][:44]}")
    print(f"\nWrote {jp}\n      {cp}")
    print("\n(Phase 1 = ranked pool only. Batch construction is Phase 2.)")


if __name__ == "__main__":
    main()
