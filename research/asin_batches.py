#!/usr/bin/env python3
"""
research.asin_batches — Phase 2 orchestrator.

Xray folder -> Phase 1 candidate pool -> constrained batch optimization ->
ASIN-BATCHES.json (authoritative) + ASIN-BATCH-REPORT.md (human) + ASIN-CANDIDATES.csv

Usage:
    python research/asin_batches.py <folder> --seed "personalized nurse sweatshirt"
        [--decoration-method "machine embroidery"] [--exclude a,b] [--out DIR]
"""
import sys as _sys, os as _os
for _p in (_os.path.dirname(_os.path.abspath(__file__)),
           _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "compliance")):
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.insert(0, _p)

import os, json, argparse, hashlib, warnings
warnings.filterwarnings("ignore")

from asin_candidates import build_candidates, write_outputs, SCHEMA_VERSION, TOOL_VERSION
from batch_optimizer import optimize_batches, validate_constraints, CONSTRAINTS
from target_profile import parse_target_profile
from export_detector import PRODUCT_XRAY

REPORT = "ASIN-BATCH-REPORT.md"
BATCHES = "ASIN-BATCHES.json"


def _member(c, i):
    cs = c["component_scores"]
    return {
        "rank_within_batch": i, "global_rank": c["global_rank"], "asin": c["asin"],
        "title": c["title"], "brand": c.get("brand"), "seller": c.get("seller"),
        "cluster": c["title_cluster"], "variation_family": c["variation_family"],
        "market_bucket": c.get("_bucket_final", c.get("_bucket")), "sub_form": c.get("_subform"),
        "price_tier": c.get("_price_tier"),
        "activity_state": c["activity_state"],
        "predicted_keyword_value_score": c["predicted_keyword_value_score"],
        "component_scores": cs,
        "key_metrics": {"asin_sales": c.get("asin_sales"), "asin_revenue": c.get("revenue"),
                        "recent_purchases": c.get("recent_purchases"), "bsr": c.get("bsr"),
                        "reviews": c.get("reviews"), "rating": c.get("rating"),
                        "review_velocity": c.get("review_velocity"),
                        "creation_date": c.get("creation_date") or None,
                        "fulfillment": c.get("fulfillment") or None,
                        "price": c.get("price"), "display_order": c.get("display_order")},
        "reason_codes": c["reason_codes"],
        "risk_flags": [p["reason"] for p in c["penalties"]],
        "why_selected": why_selected(c),
    }


def why_selected(c):
    cs = c["component_scores"]
    bits = []
    if "EXACT_AUDIENCE" in c["reason_codes"] and "EXACT_PRODUCT_FAMILY" in c["reason_codes"]:
        bits.append("exact audience + product match")
    if "PERSONALIZATION_MATCH" in c["reason_codes"]:
        bits.append("sells personalization")
    if "DECORATION_METHOD_MATCH" in c["reason_codes"]:
        bits.append("same decoration method")
    if c["activity_state"] == "ACTIVE_CONFIRMED":
        bits.append("confirmed selling now")
    elif c["activity_state"] == "ACTIVE_SUPPORTED":
        bits.append("current activity supported by secondary signals")
    if "LOW_REVIEW_CURRENT_SALES" in c["reason_codes"]:
        bits.append("sells without a big review moat")
    if "FBM_MODEL_MATCH" in c["reason_codes"]:
        bits.append("same fulfilment model")
    if "CROSS_QUERY_STABILITY" in c["reason_codes"]:
        bits.append("appears across several searches")
    return f"relevance {cs['relevance']:.0f}/100 — " + ("; ".join(bits) if bits else "meets all gates")


def build(path, profile, outdir):
    res = build_candidates(path, profile)
    batches, warnings, used = optimize_batches(res["candidates"])
    validation = validate_constraints(batches)

    unselected = [c for c in res["candidates"] if c["asin"] not in used][:20]
    run_id = "run_" + hashlib.sha1(
        (profile.seed + "|" + os.path.abspath(path)).encode("utf-8")).hexdigest()[:10]

    doc = {
        "schema_version": SCHEMA_VERSION,
        "run_metadata": {
            "run_id": run_id, "tool_version": TOOL_VERSION,
            "marketplace": profile.marketplace, "seed": profile.seed,
            "phase": "Phase 2 — batch portfolio optimization",
            "source_files": res["source_files"],
        },
        "target_profile": res["target_profile"],
        "scoring_weights": res["scoring_weights"],
        "constraints": CONSTRAINTS,
        "summary": {
            **res["summary"],
            "selected_asins": len(used),
            "number_of_batches": len(batches),
            "short_batch_warning": any(len(b["members"]) < CONSTRAINTS["minimum_healthy_batch_size"]
                                       for b in batches),
            "owner_review_required": any(b["quality_label"] in ("USABLE_WITH_REVIEW", "REBUILD_BATCH")
                                         for b in batches),
        },
        "batches": [{
            "batch_id": b["batch_id"], "priority": b["priority"], "purpose": b["purpose"],
            "batch_quality_score": b["batch_quality_score"], "quality_label": b["quality_label"],
            "batch_penalties": b["batch_penalties"],
            "constraint_results": next(r for r in validation["per_batch"] if r["batch_id"] == b["batch_id"]),
            "paste_ready_asins": [c["asin"] for c in b["members"]],
            "asins": [_member(c, i) for i, c in enumerate(b["members"], 1)],
        } for b in batches],
        "constraint_validation": validation,
        "unselected_top_candidates": [
            {"global_rank": c["global_rank"], "asin": c["asin"], "title": c["title"],
             "brand": c.get("brand"), "predicted_keyword_value_score": c["predicted_keyword_value_score"],
             "relevance": c["component_scores"]["relevance"], "activity_state": c["activity_state"],
             "available_as_replacement": True} for c in unselected],
        "rejected_candidates": res["rejected_candidates"],
        "warnings": warnings,
        "next_manual_action": ("Review Batch 1, then paste its ASINs into Helium 10 Cerebro. "
                               "Nothing here touches Seller Central."),
    }
    return doc, res


def write_report(doc, outdir):
    L = ["# ASIN BATCHES — reverse-ASIN input for Cerebro", ""]
    s, tp = doc["summary"], doc["target_profile"]
    L.append(f"**Seed:** `{doc['run_metadata']['seed']}`  ·  **Audience:** `{tp['audience_key'] or '—'}`  ·  "
             f"**Family:** `{tp['family_key'] or '—'}`  ·  "
             f"**Personalization:** {'required' if tp['personalization_required'] else 'optional'}  ·  "
             f"**Decoration:** {tp['decoration_method'] or '—'}")
    L.append(f"\n**Scanned:** {s['rows_loaded']} rows → **{s['unique_valid_asins']} unique ASINs** → "
             f"**{s['qualified_candidates']} qualified** ({s['hard_rejected']} rejected).  "
             f"**{s['number_of_batches']} batch(es)**, {s['selected_asins']} ASINs selected.")
    L.append("\n**Files read**\n")
    L.append("| File | Detected | Rows |")
    L.append("|---|---|--:|")
    for f in doc["run_metadata"]["source_files"]:
        L.append(f"| {f['file']} | {f['detected_type']} | {f['rows_loaded']} |")

    for b in doc["batches"]:
        L.append(f"\n---\n## {b['batch_id']} — {b['quality_label']} ({b['batch_quality_score']}/100)")
        L.append(f"_{b['purpose']}_\n")
        L.append("### ✅ Paste into Cerebro\n```")
        L.append("  ".join(b["paste_ready_asins"]))
        L.append("```")
        cr = b["constraint_results"]
        L.append(f"\n**Constraints** — size {cr['size']}/10 · max same brand {cr['max_same_brand']}/2 · "
                 f"max same seller {cr['max_same_seller']}/2 · title clones {cr['max_same_title_cluster']}/1 · "
                 f"variation families {cr['max_same_variation_family']}/1 · "
                 f"min relevance {cr['min_relevance']} · median {cr['median_relevance']}")
        if b["batch_penalties"]:
            L.append("\n**Penalties:** " + "; ".join(f"{p['reason']} (-{p['points']})" for p in b["batch_penalties"]))
        L.append("\n| # | ASIN | Score | Rel | Act | State | Bucket | Brand | Why selected |")
        L.append("|---|---|--:|--:|--:|---|---|---|---|")
        for m in b["asins"]:
            cs = m["component_scores"]
            L.append(f"| {m['rank_within_batch']} | {m['asin']} | {m['predicted_keyword_value_score']} | "
                     f"{cs['relevance']:.0f} | {cs['current_activity']:.0f} | {m['activity_state']} | "
                     f"{m['market_bucket']} | {(m['brand'] or '?')[:18]} | {m['why_selected'][:88]} |")

    v = doc["constraint_validation"]
    L.append(f"\n---\n## Constraint validation\n")
    L.append(f"- All hard constraints pass: **{v['all_constraints_pass']}**")
    L.append(f"- No duplicate ASIN across batches: **{v['no_duplicate_asin_across_batches']}**")
    L.append(f"- Max same brand across all batches: **{v['max_same_brand_all_batches']}/4** "
             f"({'ok' if v['brand_all_batches_ok'] else 'VIOLATION'})")
    if doc["warnings"]:
        L.append("\n## Warnings\n")
        for w in doc["warnings"]:
            L.append(f"- {w}")
    if doc["unselected_top_candidates"]:
        L.append("\n## Replacement queue (next best, constraint-checked on use)\n")
        L.append("| ASIN | Score | Rel | Brand | Title |")
        L.append("|---|--:|--:|---|---|")
        for c in doc["unselected_top_candidates"][:10]:
            L.append(f"| {c['asin']} | {c['predicted_keyword_value_score']} | {c['relevance']:.0f} | "
                     f"{(c['brand'] or '?')[:16]} | {c['title'][:52]} |")
    L.append(f"\n---\n## Next step\n\n{doc['next_manual_action']}\n")
    L.append("> This picker filters and explains; it never publishes. You decide what enters Cerebro.")

    p = os.path.join(outdir, REPORT)
    open(p, "w", encoding="utf-8").write("\n".join(L))
    return p


def main():
    ap = argparse.ArgumentParser(description="Phase 2 — optimized Cerebro ASIN batches")
    ap.add_argument("path")
    ap.add_argument("--seed", required=True)
    ap.add_argument("--audience", default=None)
    ap.add_argument("--family", default=None)
    ap.add_argument("--decoration-method", default=None)
    ap.add_argument("--exclude", default="")
    ap.add_argument("--require", default="")
    ap.add_argument("--target-price", type=float, default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    profile = parse_target_profile(
        a.seed, audience=a.audience, family=a.family, decoration_method=a.decoration_method,
        excluded_terms=[x for x in a.exclude.split(",") if x.strip()],
        required_terms=[x for x in a.require.split(",") if x.strip()],
        target_price=a.target_price)
    outdir = a.out or a.path
    doc, res = build(a.path, profile, outdir)
    os.makedirs(outdir, exist_ok=True)
    jp = os.path.join(outdir, BATCHES)
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False, default=str)
    write_outputs(res, outdir)
    rp = write_report(doc, outdir)

    print(f"TARGET: {profile.describe()}")
    s = doc["summary"]
    print(f"\n{s['rows_loaded']} rows -> {s['unique_valid_asins']} unique -> {s['qualified_candidates']} qualified")
    for b in doc["batches"]:
        cr = b["constraint_results"]
        print(f"\n=== {b['batch_id']}  {b['quality_label']} ({b['batch_quality_score']}/100)  "
              f"size {cr['size']}  brand<={cr['max_same_brand']}  clones<={cr['max_same_title_cluster']}")
        print("  " + "  ".join(b["paste_ready_asins"]))
    v = doc["constraint_validation"]
    print(f"\nconstraints pass: {v['all_constraints_pass']}   batches: {len(doc['batches'])}")
    for w in doc["warnings"]:
        print("  ! " + w)
    print(f"\nWrote {jp}\n      {rp}")


if __name__ == "__main__":
    main()
