#!/usr/bin/env python3
"""
research.competitor_gap_analyzer — turn an Xray export into ranked differentiation gaps.

Reads the product-level Helium 10 Xray file you exported and finds where the competitor
set is WEAK, so you know how to win: price bands, review beatability, thin image counts,
and — for personalized embroidery — how few competitors actually show personalization
depth or REAL embroidery proof. Every gap comes with a concrete creative action.

Read-only. No Amazon account access. Aligned with the toolkit's honesty rules: it never
invents claims, it only reports what the data shows and what you could do about it.

Usage:
    python competitor_gap_analyzer.py <run_folder>
Outputs:
    COMPETITOR-GAP.json
    COMPETITOR-GAP-REPORT.md
"""
from __future__ import annotations
import os, sys, re, glob, json


def _load(path):
    try:
        import pandas as pd
    except Exception:
        return None
    try:
        if path.lower().endswith((".xlsx", ".xlsm", ".xls")):
            return pd.read_excel(path)
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                df = pd.read_csv(path, encoding=enc)
                if df.shape[1] > 1:
                    return df
            except Exception:
                continue
    except Exception:
        return None
    return None


def _find(df, *names):
    def norm(x):
        return re.sub(r"[^a-z0-9]+", " ", str(x).lower()).strip()
    cols = {norm(c): c for c in df.columns}
    for n in names:
        if norm(n) in cols:
            return cols[norm(n)]
    for n in names:
        nw = set(norm(n).split())
        for cn, orig in cols.items():
            if nw and nw <= set(cn.split()):
                return orig
    return None


def _num(series):
    import pandas as pd
    return pd.to_numeric(series.astype(str).str.replace(r"[^0-9.\-]", "", regex=True).replace("", "0"),
                         errors="coerce").fillna(0)


def find_xray(folder):
    for f in glob.glob(os.path.join(folder, "*")):
        b = os.path.basename(f).lower()
        if "xray" in b and f.lower().endswith((".xlsx", ".xls", ".csv")):
            return f
    # fall back: any xlsx that has a Price + Review Count column
    for f in glob.glob(os.path.join(folder, "*.xls*")):
        df = _load(f)
        if df is not None and _find(df, "price") and _find(df, "review count", "reviews"):
            return f
    return None


PERSONAL = ("personalized", "custom", "customized", "name", "monogram", "your name", "add name")
EMBROID = ("embroidered", "embroidery", "stitched")
PRINT = ("printed", "print", "sublimation", "vinyl", "dtg")


def analyze(folder):
    import pandas as pd
    path = find_xray(folder)
    if not path:
        return {"ok": False, "reason": "no Xray file found in the folder", "gaps": []}
    df = _load(path)
    if df is None:
        return {"ok": False, "reason": "could not read the Xray file", "gaps": []}
    df.columns = [str(c).replace("﻿", "").strip() for c in df.columns]

    price_c = _find(df, "price")
    rev_c = _find(df, "review count", "reviews")
    rat_c = _find(df, "ratings", "rating")
    img_c = _find(df, "images", "image count", "# images")
    title_c = _find(df, "title", "product title", "product details")
    rev_v_c = _find(df, "review velocity", "reviews/month", "review count/month")

    n = len(df)
    titles = df[title_c].astype(str).str.lower().tolist() if title_c else []
    prices = _num(df[price_c]) if price_c else pd.Series([], dtype=float)
    reviews = _num(df[rev_c]) if rev_c else pd.Series([], dtype=float)
    images = _num(df[img_c]) if img_c else pd.Series([], dtype=float)

    # P0-05: report which fields were actually present. A missing column is a WARNING, not a
    # confident zero — we never score a gap from data we don't have.
    missing = [label for label, col in (("price", price_c), ("review count", rev_c),
               ("image count", img_c), ("title", title_c)) if not col]

    gaps = []
    def gap(area, score, source_type, finding, action, manual=False, field=None):
        gaps.append({
            "area": area, "score": score,
            "source_type": source_type,                 # numeric | text-derived | manual-review-required
            "source_field": field,
            "confidence": "HIGH" if source_type == "numeric" and n >= 8 else "MEDIUM" if n >= 4 else "LOW",
            "manual_confirmation_required": bool(manual),
            "finding": finding, "action": action,
        })

    # 1) review beatability (numeric)
    if len(reviews):
        low_rev = int((reviews < 100).sum()); med = float(reviews.median()); share = round(low_rev / n * 100)
        if share >= 40:
            gap("Review beatability", min(95, 50 + share // 2), "numeric",
                f"{share}% of competitors have <100 reviews (median {int(med)}).",
                "Enter now — you can out-review most of the page with a focused launch + review follow-up.",
                field="review count")
        else:
            gap("Review beatability", max(20, 60 - share), "numeric",
                f"Only {share}% are under 100 reviews (median {int(med)}) — established page.",
                "Differentiate on personalization/quality, not on out-reviewing them fast.", field="review count")

    # 2) price gap (numeric)
    if len(prices) and prices.gt(0).any():
        p = prices[prices > 0]; lo, hi, mid = float(p.min()), float(p.max()), float(p.median())
        gap("Price positioning", 60, "numeric",
            f"Competitors span ${lo:.0f}–${hi:.0f} (median ${mid:.0f}).",
            f"Anchor slightly above median (~${mid+3:.0f}) IF your embroidery proof + A+ justify it; "
            f"only go low if your economics still clear $8/order.", field="price")

    # 3) thin images (numeric COUNT only — NOT image quality, which needs manual review)
    if len(images) and images.gt(0).any():
        thin = int((images < 6).sum()); share = round(thin / n * 100)
        if share >= 30:
            gap("Image coverage", min(90, 55 + share // 2), "numeric",
                f"{share}% of competitors use fewer than 6 images (count only — quality not judged from data).",
                "Ship the full 7–9 image set (macro stitch, size chart, how-to-personalize, gift) to win the click.",
                field="image count")

    # 4) personalization depth (TEXT-derived from titles — a signal, not proof)
    if titles:
        pers = sum(1 for t in titles if any(w in t for w in PERSONAL)); share = round(pers / n * 100)
        if share <= 60:
            gap("Personalization depth", min(92, 60 + (60 - share)), "text-derived",
                f"Only {share}% of titles clearly signal personalization/custom name (from title text).",
                "Lead with the personalization promise in title + main-image legibility + image #4 'how to personalize'.",
                field="title")

    # 5) embroidery vs print — TEXT-derived signal; real embroidery PROOF requires a manual/image check
    if titles:
        emb = sum(1 for t in titles if any(w in t for w in EMBROID))
        prn = sum(1 for t in titles if any(w in t for w in PRINT))
        emb_s = round(emb / n * 100); prn_s = round(prn / n * 100)
        if emb_s <= 50:
            gap("Real embroidery proof", min(95, 65 + (50 - emb_s)), "manual-review-required",
                f"{emb_s}% of titles mention embroidery, {prn_s}% look printed (title text only — actual stitching "
                f"cannot be proven from Xray data).",
                "Own the macro-stitch proof image (real photo, hash-bound). Confirm competitors' embroidery claims "
                "by eye before relying on this gap.", manual=True, field="title")

    gaps.sort(key=lambda g: g["score"], reverse=True)
    return {"ok": True, "schema_version": "2.4", "source": os.path.basename(path),
            "n_competitors": n, "missing_fields": missing, "gaps": gaps}


def run(folder):
    data = analyze(folder)
    with open(os.path.join(folder, "COMPETITOR-GAP.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    lines = ["# Competitor Gap Analysis — where the page is weak", ""]
    if not data.get("ok"):
        lines.append(f"_{data.get('reason')}_ — add your Xray export to the folder.")
    else:
        lines.append(f"Source: `{data['source']}` · {data['n_competitors']} competitors")
        if data.get("missing_fields"):
            lines.append(f"\n> ⚠️ Missing columns (not scored): {', '.join(data['missing_fields'])} — add them to the Xray export for full coverage.")
        lines.append("")
        lines.append("| Rank | Gap | Score | Evidence | Confidence | Finding | Best action |")
        lines.append("|---|---|---:|---|---|---|---|")
        for i, g in enumerate(data["gaps"], 1):
            ev = g["source_type"] + (" · manual check" if g.get("manual_confirmation_required") else "")
            lines.append(f"| {i} | {g['area']} | {g['score']} | {ev} | {g['confidence']} | {g['finding']} | {g['action']} |")
    with open(os.path.join(folder, "COMPETITOR-GAP-REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return data


def main():
    if len(sys.argv) < 2:
        print("usage: python competitor_gap_analyzer.py <run_folder>")
        sys.exit(2)
    folder = sys.argv[1]
    if not os.path.isdir(folder):
        print(f"no such folder: {folder}"); sys.exit(2)
    d = run(folder)
    if not d.get("ok"):
        print("skip:", d.get("reason")); sys.exit(2)
    print(f"OK — {len(d['gaps'])} gaps from {d['n_competitors']} competitors. Wrote COMPETITOR-GAP.json + report.")
    sys.exit(0)


if __name__ == "__main__":
    main()
