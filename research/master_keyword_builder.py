#!/usr/bin/env python3
"""
research.master_keyword_builder — Phase 1 orchestrator (lean master keyword list).

Cerebro exports (+ approved ASIN-BATCHES.json) -> evidence matrix -> conflict gates
-> semantic classes -> competitor coverage -> lean tiers -> outputs.

Outputs:
    CEREBRO-EVIDENCE-MATRIX.json / .csv
    MASTER-KEYWORDS-LEAN.json / .xlsx / .md

Usage:
    python research/master_keyword_builder.py <folder> [--seed "..."] [--adjacent hoodie]
        [--exclude a,b] [--out DIR]
    (seed is inherited from ASIN-BATCHES.json when present)
"""
import sys as _sys, os as _os
for _p in (_os.path.dirname(_os.path.abspath(__file__)),
           _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "compliance")):
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.insert(0, _p)

import os, json, csv, argparse, hashlib, warnings
warnings.filterwarnings("ignore")

from cerebro_export_detector import load_approved_batches, CEREBRO_WITH_ASIN_RANKS
from keyword_evidence_matrix import build_evidence_matrix
from target_keyword_profile import load_keyword_profile, PROFILE_VERSION
from keyword_relevance_lean import (product_strategy_signals, classify, coverage_metrics,
                                    assign_tier, sort_key, explain, shadow_baseline,
                                    A_CORE, B_SUPPORTING, REVIEW, REJECTED, CONFLICTING)

SCHEMA_VERSION = "1.0.0"
TOOL_VERSION = "2.4.0-rc2"


def build(folder, profile, min_overlap=0.5):
    batches = load_approved_batches(folder)
    matrix = build_evidence_matrix(folder, batches, min_overlap)

    rows = []
    for e in matrix["keywords"]:
        kw = e["keyword_exact"]
        sig = product_strategy_signals(kw, profile)
        sclass, codes = classify(kw, sig, profile)
        cov = coverage_metrics(e["individual_asin_organic_ranks"], e["valid_asin_denominator"])
        tier, treasons = assign_tier(sclass, cov, e["batch_support"], sig)
        row = {
            "keyword_exact": kw, "keyword_normalized": e["keyword_normalized"],
            "tier": tier, "semantic_class": sclass,
            "product_strategy_signals": sig,
            "coverage": cov,
            **{k: cov[k] for k in ("simple_competitor_coverage_pct", "top_20_coverage_pct",
                                   "top_50_coverage_pct", "top_100_coverage_pct",
                                   "best_rank", "median_rank")},
            "batch_support": e["batch_support"],
            "search_volume": e.get("search_volume"),
            "title_density": e.get("title_density"),
            "competing_products": e.get("competing_products"),
            "cpr": e.get("cpr"), "trend": None,
            "missing_value_flags": e["missing_value_flags"],
            "aggregation_rules": e["aggregation_rules"],
            "individual_asin_organic_ranks": e["individual_asin_organic_ranks"],
            "observation_count": e["observation_count"],
            "source_files": e["source_files"], "source_batches": e["source_batches"],
            "source_observations": e["source_observations"],
            "reason_codes": codes + treasons,
            "risk_flags": [f"{c['type']}:{c['term']}" for c in sig["explicit_conflict_terms"]],
            "owner_status": "review" if tier == REVIEW else "auto",
            "shadow_baseline": shadow_baseline(kw, profile),
        }
        row["why"] = explain(row)
        rows.append(row)

    rows.sort(key=sort_key)
    for r in rows:
        b, lean = r["shadow_baseline"], r["tier"]
        strong_base = b["contains_audience"] and b["contains_product"]
        if strong_base and lean in (REVIEW, REJECTED):
            r["qa_flag"] = "BASELINE_HIGH_LEAN_LOW"
        elif not strong_base and lean == A_CORE:
            r["qa_flag"] = "LEAN_HIGH_BASELINE_LOW"
    return matrix, rows


def quality_summary(rows):
    core = [r for r in rows if r["tier"] == A_CORE]
    n = len(core) or 1
    def pct(k):
        return round(100.0 * k / n, 1)
    leak_aud = sum(1 for r in core if any(c["type"] == "wrong_audience"
                                          for c in r["product_strategy_signals"]["explicit_conflict_terms"]))
    leak_occ = sum(1 for r in core if any(c["type"] == "wrong_occasion_or_theme"
                                          for c in r["product_strategy_signals"]["explicit_conflict_terms"]))
    leak_brand = sum(1 for r in core if r["product_strategy_signals"]["trademark_status"] == "HIGH")
    exact = sum(1 for r in core if r["product_strategy_signals"]["has_audience"]
                and r["product_strategy_signals"]["has_product_family"])
    pers = sum(1 for r in core if r["product_strategy_signals"]["has_personalization"])
    dec = sum(1 for r in core if r["product_strategy_signals"]["has_decoration_method"])
    generic = sum(1 for r in rows if r["semantic_class"] == "GENERIC_PRODUCT")
    cross = sum(1 for r in rows if len(r["batch_support"]) > 1)
    counts = {t: sum(1 for r in rows if r["tier"] == t) for t in (A_CORE, B_SUPPORTING, REVIEW, REJECTED)}
    s = {
        "counts": counts, "total_keywords": len(rows),
        "A_CORE_exact_product_identity_pct": pct(exact),
        "A_CORE_personalization_coverage_pct": pct(pers),
        "A_CORE_decoration_method_coverage_pct": pct(dec),
        "A_CORE_wrong_audience_leakage_pct": pct(leak_aud),
        "A_CORE_wrong_occasion_leakage_pct": pct(leak_occ),
        "A_CORE_brand_contamination_pct": pct(leak_brand),
        "generic_keyword_ratio_pct": round(100.0 * generic / (len(rows) or 1), 1),
        "owner_review_ratio_pct": round(100.0 * counts[REVIEW] / (len(rows) or 1), 1),
        "cross_batch_supported_pct": round(100.0 * cross / (len(rows) or 1), 1),
    }
    s["hard_gates_pass"] = (s["A_CORE_wrong_audience_leakage_pct"] == 0
                            and s["A_CORE_wrong_occasion_leakage_pct"] == 0
                            and s["A_CORE_brand_contamination_pct"] == 0)
    s["recommended_targets_met"] = (s["A_CORE_exact_product_identity_pct"] >= 70
                                    and s["generic_keyword_ratio_pct"] <= 30)
    return s


def downstream_policy(qs, rows):
    """Transition mode — the lean list does not hard-block listing generation yet."""
    if not qs["hard_gates_pass"]:
        return {"mode": "transition", "lean_master_status": "failed_validation",
                "automatic_listing_generation": "BLOCKED",
                "note": "leakage guardrails failed — fix before any downstream use"}
    if qs["counts"][A_CORE] == 0:
        return {"mode": "transition", "lean_master_status": "empty",
                "automatic_listing_generation": "legacy_allowed_with_warning",
                "legacy_unsafe_warning": True,
                "note": "no A_CORE keywords; legacy generation allowed only with owner confirmation"}
    return {"mode": "transition", "lean_master_status": "available",
            "automatic_listing_generation": "owner_confirmation_required",
            "legacy_unsafe_warning": False,
            "note": "approved lean list available; owner confirms before downstream use"}


CSV_COLS = ["keyword_exact", "tier", "semantic_class", "simple_competitor_coverage_pct",
            "top_20_coverage_pct", "top_50_coverage_pct", "top_100_coverage_pct",
            "best_rank", "median_rank", "ranking_asin_count", "valid_asin_denominator",
            "search_volume", "title_density", "competing_products", "cpr",
            "observation_count", "source_files", "source_batches", "reason_codes",
            "risk_flags", "missing_value_flags", "why"]


def write_outputs(folder, profile, matrix, rows, qs, policy, outdir):
    os.makedirs(outdir, exist_ok=True)
    run_id = "kwrun_" + hashlib.sha1((profile.seed + "|" + os.path.abspath(folder)).encode()).hexdigest()[:10]
    meta = {"run_id": run_id, "tool_version": TOOL_VERSION, "schema_version": SCHEMA_VERSION,
            "profile_version": PROFILE_VERSION, "phase": "Phase 1 — lean relevance engine",
            "marketplace": profile.marketplace, "seed": profile.seed}

    # evidence matrix
    ep = os.path.join(outdir, "CEREBRO-EVIDENCE-MATRIX.json")
    json.dump({"run_metadata": meta, **matrix}, open(ep, "w", encoding="utf-8"),
              indent=2, ensure_ascii=False, default=str)
    ecp = os.path.join(outdir, "CEREBRO-EVIDENCE-MATRIX.csv")
    with open(ecp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["keyword_exact", "keyword_normalized", "search_volume", "title_density",
                    "competing_products", "cpr", "observation_count", "valid_asin_denominator",
                    "ranking_asins", "asin_ranks", "source_files", "source_batches",
                    "missing_value_flags"])
        for e in matrix["keywords"]:
            ranks = e["individual_asin_organic_ranks"]
            w.writerow([e["keyword_exact"], e["keyword_normalized"], e.get("search_volume"),
                        e.get("title_density"), e.get("competing_products"), e.get("cpr"),
                        e["observation_count"], e["valid_asin_denominator"],
                        sum(1 for v in ranks.values() if v is not None),
                        ";".join(f"{a}={int(r)}" for a, r in sorted(ranks.items()) if r is not None),
                        "|".join(e["source_files"]), "|".join(e["source_batches"]),
                        "|".join(e["missing_value_flags"])])

    # lean master
    doc = {"run_metadata": meta, "target_profile": profile.to_dict(),
           "source_validation": matrix["source_validation"], "quality_summary": qs,
           "downstream_policy": policy,
           # source_observations live in CEREBRO-EVIDENCE-MATRIX.json; duplicating them
           # here made the lean file huge on real folders. Provenance is kept via
           # source_files / source_batches / observation_count.
           "keywords": [{k: v for k, v in r.items()
                         if k not in ("coverage", "source_observations")} for r in rows],
           "next_manual_action": ("Review A_CORE and REVIEW tiers, then approve. "
                                  "Nothing here touches Seller Central.")}
    jp = os.path.join(outdir, "MASTER-KEYWORDS-LEAN.json")
    json.dump(doc, open(jp, "w", encoding="utf-8"), indent=2, ensure_ascii=False, default=str)

    # xlsx
    xp = os.path.join(outdir, "MASTER-KEYWORDS-LEAN.xlsx")
    try:
        import pandas as pd
        def flat(r):
            return {"keyword_exact": r["keyword_exact"], "tier": r["tier"],
                    "semantic_class": r["semantic_class"],
                    "coverage_pct": r["simple_competitor_coverage_pct"],
                    "top_20_pct": r["top_20_coverage_pct"], "top_50_pct": r["top_50_coverage_pct"],
                    "best_rank": r["best_rank"], "median_rank": r["median_rank"],
                    "ranking_asins": r["coverage"]["ranking_asin_count"],
                    "denominator": r["coverage"]["valid_asin_denominator"],
                    "search_volume": r["search_volume"], "title_density": r["title_density"],
                    "batch_support": json.dumps(r["batch_support"]),
                    "reason_codes": "|".join(r["reason_codes"]),
                    "risk_flags": "|".join(r["risk_flags"]),
                    "missing_flags": "|".join(r["missing_value_flags"]), "why": r["why"]}
        with pd.ExcelWriter(xp, engine="openpyxl") as xw:
            for t in (A_CORE, B_SUPPORTING, REVIEW, REJECTED):
                sub = [flat(r) for r in rows if r["tier"] == t]
                pd.DataFrame(sub or [{"keyword_exact": "(none)"}]).to_excel(xw, sheet_name=t[:31], index=False)
            pd.DataFrame([qs["counts"]]).to_excel(xw, sheet_name="Quality", index=False)
    except Exception as e:
        xp = f"(xlsx skipped: {e})"

    # markdown
    mp = os.path.join(outdir, "MASTER-KEYWORDS-LEAN.md")
    L = ["# MASTER KEYWORDS — lean relevance list (Phase 1)", ""]
    L.append(f"**Seed:** `{profile.seed}` · **Profile:** v{PROFILE_VERSION} · "
             f"audience `{profile.audience_key or '—'}` · family `{profile.family_key or '—'}`")
    c = qs["counts"]
    L.append(f"\n**Tiers:** A_CORE **{c[A_CORE]}** · B_SUPPORTING **{c[B_SUPPORTING]}** · "
             f"REVIEW **{c[REVIEW]}** · REJECTED **{c[REJECTED]}**  (total {qs['total_keywords']})")
    L.append(f"\n**Guardrails** — wrong-audience leakage {qs['A_CORE_wrong_audience_leakage_pct']}% · "
             f"wrong-occasion {qs['A_CORE_wrong_occasion_leakage_pct']}% · "
             f"brand {qs['A_CORE_brand_contamination_pct']}% · "
             f"exact identity {qs['A_CORE_exact_product_identity_pct']}% · "
             f"hard gates **{'PASS' if qs['hard_gates_pass'] else 'FAIL'}**")
    L.append("\n**Files read**\n\n| File | Type | Rows | Batch |")
    L.append("|---|---|--:|---|")
    for f in matrix["source_validation"]["files"]:
        bm = f.get("batch_match") or {}
        L.append(f"| {f['file'][:44]} | {f['detected_type']} | {f['rows_loaded']} | "
                 f"{bm.get('batch_id') or '—'} |")
    for t in (A_CORE, B_SUPPORTING):
        sub = [r for r in rows if r["tier"] == t][:40]
        L.append(f"\n---\n## {t} ({sum(1 for r in rows if r['tier']==t)})\n")
        if not sub:
            L.append("_(none)_")
            continue
        L.append("| Keyword | Class | Coverage | Top-50 | Best | SV | Why |")
        L.append("|---|---|--:|--:|--:|--:|---|")
        for r in sub:
            L.append(f"| {r['keyword_exact'][:40]} | {r['semantic_class']} | "
                     f"{r['simple_competitor_coverage_pct']}% | {r['coverage']['top_50_asin_count']} | "
                     f"{r['best_rank'] or '—'} | {int(r['search_volume']) if r['search_volume'] else '—'} | "
                     f"{r['why'][:64]} |")
    rej = [r for r in rows if r["tier"] == REJECTED][:25]
    if rej:
        L.append(f"\n---\n## REJECTED — why (first 25 of {sum(1 for r in rows if r['tier']==REJECTED)})\n")
        L.append("| Keyword | Reason |")
        L.append("|---|---|")
        for r in rej:
            L.append(f"| {r['keyword_exact'][:44]} | {r['why'][:86]} |")
    L.append(f"\n---\n**Downstream policy:** {policy['mode']} — {policy['note']}")
    L.append("\n> Search volume, Title Density, CPR and trend never change a tier. "
             "Nothing here connects to Seller Central.")
    open(mp, "w", encoding="utf-8").write("\n".join(L))
    return ep, ecp, jp, xp, mp


def main():
    ap = argparse.ArgumentParser(description="Phase 1 — lean master keyword list")
    ap.add_argument("path")
    ap.add_argument("--seed", default=None, help="inherited from ASIN-BATCHES.json when omitted")
    ap.add_argument("--audience", default=None)
    ap.add_argument("--family", default=None)
    ap.add_argument("--decoration-method", default=None)
    ap.add_argument("--adjacent", default="", help="approved adjacent families, comma list")
    ap.add_argument("--exclude", default="")
    ap.add_argument("--require", default="")
    ap.add_argument("--min-overlap", type=float, default=0.5)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    profile = load_keyword_profile(
        folder=a.path, seed=a.seed, audience=a.audience, family=a.family,
        decoration_method=a.decoration_method,
        approved_adjacent=[x.strip() for x in a.adjacent.split(",") if x.strip()],
        required_concepts=[x.strip() for x in a.require.split(",") if x.strip()],
        excluded_terms=[x.strip() for x in a.exclude.split(",") if x.strip()])
    outdir = a.out or a.path
    matrix, rows = build(a.path, profile, a.min_overlap)
    qs = quality_summary(rows)
    policy = downstream_policy(qs, rows)
    paths = write_outputs(a.path, profile, matrix, rows, qs, policy, outdir)

    print("PROFILE:", profile.describe())
    print("\nFILES:")
    for f in matrix["source_validation"]["files"]:
        bm = f.get("batch_match") or {}
        tag = "USE " if f["detected_type"] == CEREBRO_WITH_ASIN_RANKS and f["rows_loaded"] else "skip"
        print(f"  {tag} {f['file'][:48]:<50}{f['detected_type']:<26}rows={f['rows_loaded']}"
              + (f"  -> {bm.get('batch_id')}" if bm.get("batch_id") else ""))
        if bm.get("missing_expected_asins"):
            print(f"       missing expected ASINs: {bm['missing_expected_asins']}")
    c = qs["counts"]
    print(f"\nTIERS  A_CORE {c[A_CORE]} | B_SUPPORTING {c[B_SUPPORTING]} | "
          f"REVIEW {c[REVIEW]} | REJECTED {c[REJECTED]}   (total {qs['total_keywords']})")
    print(f"GUARDRAILS  wrong-audience {qs['A_CORE_wrong_audience_leakage_pct']}% | "
          f"wrong-occasion {qs['A_CORE_wrong_occasion_leakage_pct']}% | "
          f"brand {qs['A_CORE_brand_contamination_pct']}% | "
          f"exact identity {qs['A_CORE_exact_product_identity_pct']}% | "
          f"hard gates {'PASS' if qs['hard_gates_pass'] else 'FAIL'}")
    print(f"\nTOP A_CORE:")
    for r in [x for x in rows if x["tier"] == A_CORE][:10]:
        print(f"  {r['keyword_exact'][:42]:<44}{r['semantic_class']:<14}"
              f"cov {r['simple_competitor_coverage_pct']}%  best #{r['best_rank']}")
    print("\nWrote:")
    for p in paths:
        print("  ", p)


if __name__ == "__main__":
    main()
