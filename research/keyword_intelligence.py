#!/usr/bin/env python3
"""
research.keyword_intelligence — cross-platform keyword opportunity scoring.

Merges Helium 10 keyword data (Cerebro/Magnet export, or the master-keywords.xlsx that
phaseA_master.py already produced) with an optional YTrends export (the CSV from your
YTrends Chrome tool) and ranks keywords by the ONE thing that matters at the starting
decision: HIGH Amazon search volume + LOW Amazon competition, then nudged by YTrends
momentum (rank / growth / hidden-gem).

Scoring is transparent — every keyword carries the numbers and a plain-English "why".
No black box, no Amazon account access; read-only analysis of files you exported.

Usage:
    python keyword_intelligence.py <run_folder>
Outputs (in the folder):
    KEYWORD-INTELLIGENCE.json
    KEYWORD-INTELLIGENCE-REPORT.md
"""
from __future__ import annotations
import os, sys, re, glob, json

# ---- tolerant loader (xlsx/csv, BOM, latin-1) ----
def _load(path):
    try:
        import pandas as pd
    except Exception:
        return None
    try:
        if path.lower().endswith((".xlsx", ".xlsm", ".xls")):
            return pd.read_excel(path)
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            for sep in (",", "\t", ";"):
                try:
                    df = pd.read_csv(path, encoding=enc, sep=sep)
                    if df.shape[1] > 1:
                        return df
                except Exception:
                    continue
    except Exception:
        return None
    return None

def _norm_cols(df):
    df.columns = [str(c).replace("﻿", "").strip().strip('"').strip() for c in df.columns]
    return df

def _find(df, *names):
    # normalize underscores/punctuation to spaces so "search_volume" == "search volume"
    def norm(x):
        return re.sub(r"[^a-z0-9]+", " ", str(x).lower()).strip()
    cols = {norm(c): c for c in df.columns}
    for n in names:                                  # exact (normalized) wins
        if norm(n) in cols:
            return cols[norm(n)]
    for n in names:                                  # all words of the name present as whole words
        nw = set(norm(n).split())
        for cn, orig in cols.items():
            if nw and nw <= set(cn.split()):
                return orig
    return None

def _num(series):
    import pandas as pd
    return pd.to_numeric(
        series.astype(str).str.replace(r"[^0-9.\-]", "", regex=True).replace("", "0"),
        errors="coerce").fillna(0)

def _key(s):
    return re.sub(r"\s+", " ", str(s).strip().lower())

# ---- H10 keyword data ----
def load_h10(folder):
    """Return {keyword: {sv, competition, title_density}} from cerebro/magnet or master-keywords."""
    import pandas as pd
    candidates = []
    mk = os.path.join(folder, "master-keywords.xlsx")
    if os.path.exists(mk):
        try:
            xl = pd.ExcelFile(mk)
            sheet = "All Keywords" if "All Keywords" in xl.sheet_names else xl.sheet_names[0]
            df = _norm_cols(xl.parse(sheet))
            # phaseA_master already screened relevance + trademarks; honour that screen.
            # Without it, off-target high-volume terms ('nursing bras' for a nurse sweatshirt,
            # 'clearance' at title-density 0) win on volume alone and front-load the title.
            rc = _find(df, "relevant")
            if rc:
                df = df[~df[rc].astype(str).str.strip().str.lower().isin(("false", "0", "no"))]
            tc = _find(df, "tm status", "trademark status")
            if tc:
                df = df[df[tc].astype(str).str.strip().str.upper() != "HIGH"]
            candidates.append(df)
        except Exception:
            pass
    if not candidates:
        for f in glob.glob(os.path.join(folder, "*")):
            b = os.path.basename(f).lower()
            if any(t in b for t in ("cerebro", "magnet", "keyword")) and f.lower().endswith((".xlsx", ".xls", ".csv")):
                df = _load(f)
                if df is not None:
                    candidates.append(_norm_cols(df))
    out = {}
    for df in candidates:
        kwc = _find(df, "keyword phrase", "keyword", "phrase", "search terms")
        svc = _find(df, "search volume", "search vol", "sv", "amazon_sv", "volume")
        cpc = _find(df, "competing products", "competing", "title density", "titles containing", "competition")
        tdc = _find(df, "title density", "titles containing")
        if not kwc:
            continue
        for _, r in df.iterrows():
            k = _key(r[kwc])
            if not k or len(k) < 3:
                continue
            sv = float(_num(pd.Series([r[svc]]))[0]) if svc else 0.0
            comp = float(_num(pd.Series([r[cpc]]))[0]) if cpc else 0.0
            td = float(_num(pd.Series([r[tdc]]))[0]) if tdc else 0.0
            prev = out.get(k)
            rec = {"sv": sv, "competition": comp, "title_density": td}
            if not prev or sv > prev["sv"]:
                out[k] = rec
    return out

# ---- YTrends data ----
def load_ytrends(folder):
    """Return {keyword: {rank, growth, hidden_gem, yt_volume}} from a YTrends CSV export."""
    import pandas as pd
    out = {}
    for f in glob.glob(os.path.join(folder, "*")):
        b = os.path.basename(f).lower()
        if not (("ytrend" in b or "ytrends" in b or "trend" in b) and f.lower().endswith((".csv", ".xlsx", ".xls"))):
            continue
        df = _load(f)
        if df is None:
            continue
        df = _norm_cols(df)
        kwc = _find(df, "keyword", "term", "query", "title", "search term")
        if not kwc:
            continue
        rnk = _find(df, "rank", "position")
        grw = _find(df, "growth", "trend", "change", "momentum", "% growth")
        gem = _find(df, "hidden gem", "gem score", "hidden gem score", "opportunity")
        vol = _find(df, "volume", "search volume", "searches")
        for _, r in df.iterrows():
            k = _key(r[kwc])
            if not k or len(k) < 3:
                continue
            out[k] = {
                "rank": float(_num(pd.Series([r[rnk]]))[0]) if rnk else 0.0,
                "growth": float(_num(pd.Series([r[grw]]))[0]) if grw else 0.0,
                "hidden_gem": float(_num(pd.Series([r[gem]]))[0]) if gem else 0.0,
                "yt_volume": float(_num(pd.Series([r[vol]]))[0]) if vol else 0.0,
            }
    return out

# ---- scoring ----
def _scale(v, lo, hi):
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))

EXTERNAL_WEIGHT = 0.25   # P0-04: external (YTrends) influence is CAPPED; Amazon dominates the decision

def score(h10, yt, external_weight=EXTERNAL_WEIGHT):
    """Return keyword records with SEPARATE Amazon-opportunity and trend-momentum scores, a
    confidence level, reason codes, and the score decomposition. This is our INTERNAL heuristic —
    a recommendation, not Amazon's private ranking formula. Missing YTrends never penalizes a
    keyword (the external weight is simply dropped and Amazon carries the full decision)."""
    if not h10:
        return []
    svs = [d["sv"] for d in h10.values()]
    comps = [d["competition"] for d in h10.values() if d["competition"] > 0] or [1]
    sv_hi = max(svs) or 1
    comp_hi = max(comps) or 1
    aw = 1.0 - external_weight
    rows = []
    for k, d in h10.items():
        sv_s = _scale(d["sv"], 0, sv_hi)                     # high SV good
        comp_s = 1 - _scale(d["competition"], 0, comp_hi)    # low competition good
        amazon_opp = round((0.55 * sv_s + 0.45 * comp_s) * 100)   # Amazon-only opportunity (0..100)

        y = yt.get(k, {})
        signals = []; mom_norm = None
        if y:
            g = _scale(y.get("growth", 0), 0, 100)
            gem = _scale(y.get("hidden_gem", 0), 0, 100)
            rank = y.get("rank", 0)
            rnk_s = (1 - _scale(rank, 1, 200)) if rank else 0
            mom_norm = max(g, gem, rnk_s)
            if y.get("growth"): signals.append(f"YTrends growth {int(y['growth'])}")
            if y.get("hidden_gem"): signals.append(f"hidden-gem {int(y['hidden_gem'])}")
            if rank: signals.append(f"YTrends rank #{int(rank)}")
        trend_momentum = round((mom_norm or 0) * 100) if y else None

        # combined: if no YTrends, Amazon opportunity IS the score (no penalty for missing external data)
        if y:
            combined = round(aw * amazon_opp + external_weight * (trend_momentum or 0))
        else:
            combined = amazon_opp

        # confidence from data coverage
        cov = sum([d["sv"] > 0, d["competition"] > 0 or d["title_density"] > 0, bool(y)])
        confidence = "HIGH" if cov >= 3 else "MEDIUM" if cov == 2 else "LOW"

        reasons = []
        reasons.append("high_search_volume" if sv_s >= .6 else "moderate_volume" if sv_s >= .3 else "low_volume")
        reasons.append("low_competition" if comp_s >= .6 else "medium_competition" if comp_s >= .3 else "crowded")
        if y and (mom_norm or 0) >= .5: reasons.append("trending_external")
        if not y: reasons.append("no_external_data")
        why = ", ".join(r.replace("_", " ") for r in reasons)

        rows.append({
            "keyword": k,
            "score": combined,                       # back-compat field = combined internal score
            "amazon_opportunity": amazon_opp,
            "trend_momentum": trend_momentum,        # None when no YTrends data
            "combined_internal_score": combined,
            "confidence": confidence,
            "reason_codes": reasons,
            "sv": int(d["sv"]), "competition": int(d["competition"]),
            "title_density": int(d["title_density"]),
            "on_ytrends": bool(y), "yt_signals": signals,
            "why": why,
            "score_components": {
                "amazon_opportunity_weight": round(aw, 2),
                "external_trend_weight": round(external_weight, 2),
                "amazon_opportunity": amazon_opp,
                "trend_momentum": trend_momentum,
                "note": "recommendation_not_amazon_fact",
            },
        })
    # deterministic order: score desc, then keyword asc (stable, reproducible)
    rows.sort(key=lambda r: (-r["score"], r["keyword"]))
    return rows

def _sources(folder):
    src = []
    for f in sorted(glob.glob(os.path.join(folder, "*"))):
        b = os.path.basename(f).lower()
        if b.endswith((".csv", ".xlsx", ".xls")) and any(t in b for t in ("cerebro", "magnet", "keyword", "master-keywords", "ytrend", "trend")):
            try:
                import hashlib
                h = hashlib.sha256(open(f, "rb").read()).hexdigest()[:16]
            except Exception:
                h = ""
            src.append({"file": os.path.basename(f), "sha256_16": h})
    return src

def run(folder):
    h10 = load_h10(folder)
    yt = load_ytrends(folder)
    rows = score(h10, yt)
    data = {
        "schema_version": "2.4",
        "count": len(rows),
        "has_ytrends": bool(yt),
        "external_trend_weight": EXTERNAL_WEIGHT,
        "top": rows[:15],
        "all": rows,
        "provenance": _sources(folder),
        "note": ("INTERNAL heuristic (a recommendation, NOT Amazon's ranking formula). Amazon opportunity "
                 f"(search volume + low competition) carries {int((1-EXTERNAL_WEIGHT)*100)}% of the decision; "
                 f"YTrends momentum is capped at {int(EXTERNAL_WEIGHT*100)}% and never penalizes a keyword when absent."),
    }
    with open(os.path.join(folder, "KEYWORD-INTELLIGENCE.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    lines = ["# Keyword Intelligence — High SV + Low Competition (+ YTrends momentum)", "",
             data["note"], "",
             f"Keywords analyzed: {len(rows)} · YTrends data: {'yes' if yt else 'no'}", "",
             "| # | Keyword | Score | SV | Competition | Signals |",
             "|---|---|---:|---:|---:|---|"]
    for i, r in enumerate(rows[:15], 1):
        lines.append(f"| {i} | {r['keyword']} | {r['score']} | {r['sv']} | {r['competition']} | "
                     f"{r['why']}{' · ' + '; '.join(r['yt_signals']) if r['yt_signals'] else ''} |")
    if not rows:
        lines.append("\n_No H10 keyword data found. Add a Cerebro/Magnet export or run phaseA_master first._")
    with open(os.path.join(folder, "KEYWORD-INTELLIGENCE-REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return data

def main():
    if len(sys.argv) < 2:
        print("usage: python keyword_intelligence.py <run_folder>")
        sys.exit(2)
    folder = sys.argv[1]
    if not os.path.isdir(folder):
        print(f"no such folder: {folder}"); sys.exit(2)
    d = run(folder)
    print(f"OK — {d['count']} keywords scored (YTrends: {'yes' if d['has_ytrends'] else 'no'}). "
          f"Wrote KEYWORD-INTELLIGENCE.json + report to {folder}")
    sys.exit(0 if d["count"] else 2)

if __name__ == "__main__":
    main()
