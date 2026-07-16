#!/usr/bin/env python3
"""
research.keyword_evidence_matrix — keyword x ASIN evidence, nothing thrown away.

The old pipeline concatenated Cerebro files, kept max(search volume) / min(title
density), and discarded the individual ASIN rank columns entirely. Those rank
columns are the only direct evidence that an approved competitor actually ranks
for a keyword, and Phase 0 found 9 of them per export sitting unused.

Rules honoured here:
  * every raw observation is retained with its source file and batch
  * never auto-pick max SV or min TD — most-recent-valid, else median
  * missing stays missing (explicit flags), never silently zero
"""
import os, re
from datetime import datetime

from cerebro_export_detector import (classify_keyword_folder, match_export_to_batch,
                                     load_approved_batches, CEREBRO_WITH_ASIN_RANKS,
                                     CEREBRO_PLAIN)

NUM_FIELDS = {"search_volume": ["search volume"], "keyword_sales": ["keyword sales"],
              "title_density": ["title density"], "competing_products": ["competing products"],
              "cpr": ["cpr"], "cerebro_iq": ["cerebro iq score"],
              "competitor_rank_avg": ["competitor rank (avg)"],
              "ranking_competitors": ["ranking competitors (count)"]}
TEXT_FIELDS = {"trend": ["search volume trend"]}
DATE_RE = re.compile(r"(20\d{2})[-_](\d{2})[-_](\d{2})")


def _norm_col(c):
    return re.sub(r"\s+", " ", str(c).replace("﻿", "").strip().lower())


def _num(x):
    if x is None:
        return None
    s = re.sub(r"[^0-9.\-]", "", str(x))
    if s in ("", "-", ".", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def normalize_keyword(kw):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(kw).lower())).strip()


def file_date(name):
    m = DATE_RE.search(name)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _median(vals):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else round((v[n // 2 - 1] + v[n // 2]) / 2.0, 2)


def aggregate(observations, field):
    """Most recent valid observation when dates are reliable, else median.
    -> (value, rule, missing_flag)"""
    vals = [(o.get("_date"), o.get(field)) for o in observations]
    present = [(d, v) for d, v in vals if v is not None]
    if not present:
        return None, "missing_in_all_observations", True
    dated = [(d, v) for d, v in present if d is not None]
    if dated and len({d for d, _ in dated}) > 1:
        newest = max(d for d, _ in dated)
        return [v for d, v in dated if d == newest][0], "most_recent_valid_observation", False
    if len(present) == 1:
        return present[0][1], "single_observation", False
    return _median([v for _, v in present]), "median_of_comparable_observations", False


def build_evidence_matrix(folder, batches=None, min_overlap=0.5):
    """-> dict(source_validation, keywords[...]) — one row per exact keyword phrase."""
    import pandas as pd
    batches = batches if batches is not None else load_approved_batches(folder)
    files = classify_keyword_folder(folder)
    sources, obs_id = [], 0
    keywords = {}
    # Union of every approved ASIN actually submitted across the matched exports.
    # Per-file denominators are per-batch (10); once B1 and B2 both match, merged
    # ranks span both batches and a per-batch denominator yields coverage > 100%.
    submitted = set()

    for f in files:
        rec = {"file": f["file"], "detected_type": f["kind"], "reason": f["reason"],
               "rows_loaded": 0, "asin_columns": f.get("asin_columns", []),
               "export_date": None, "batch_match": None}
        if f["kind"] not in (CEREBRO_WITH_ASIN_RANKS, CEREBRO_PLAIN):
            sources.append(rec)
            continue

        d = file_date(f["file"])
        rec["export_date"] = d.strftime("%Y-%m-%d") if d else None
        match = (match_export_to_batch(f["asin_columns"], batches, min_overlap)
                 if (f["kind"] == CEREBRO_WITH_ASIN_RANKS and batches) else None)
        rec["batch_match"] = match
        if match and not match["matched"]:
            rec["reason"] = "not merged: " + match["reason"]
            sources.append(rec)
            continue

        try:
            df = pd.read_csv(f["path"]) if f["path"].lower().endswith(".csv") else pd.read_excel(f["path"])
        except Exception as e:
            rec["detected_type"], rec["reason"] = "unsupported_or_malformed", str(e)[:80]
            sources.append(rec)
            continue

        cmap = {_norm_col(c): c for c in df.columns}
        asin_cols = [c for c in df.columns if re.fullmatch(r"B0[A-Z0-9]{8}", str(c).strip())]
        batch_id = match["batch_id"] if match else None
        if match and match.get("matched"):
            submitted.update(match.get("expected") or [])
        denom = match["valid_asin_denominator"] if match else len(asin_cols)

        # to_dict("records") instead of iterrows(): iterrows builds a Series per row and
        # made a 9.5k-row folder take minutes. Column lookups below are unchanged.
        kw_col = cmap.get("keyword phrase") or cmap.get("keyword")
        num_cols = {name: next((cmap[a] for a in aliases if a in cmap), None)
                    for name, aliases in NUM_FIELDS.items()}
        txt_cols = {name: next((cmap[a] for a in aliases if a in cmap), None)
                    for name, aliases in TEXT_FIELDS.items()}
        for r in df.to_dict("records"):
            kw = str(r[kw_col]).strip() if kw_col else ""
            if not kw or kw.lower() == "nan":
                continue
            obs_id += 1
            o = {"source_observation_id": f"obs_{obs_id}", "source_file": f["file"],
                 "source_batch": batch_id, "_date": d,
                 "export_date": rec["export_date"], "keyword_exact": kw}
            flags = []
            for name, col in num_cols.items():
                v = _num(r[col]) if col else None
                o[name] = v
                if col is None:
                    flags.append(f"{name}_column_absent")
                elif v is None:
                    flags.append(f"{name}_missing")
            for name, col in txt_cols.items():
                o[name] = (str(r[col]).strip() if col and str(r[col]).strip().lower() != "nan" else None)
            o["missing_value_flags"] = flags
            ranks = {}
            for c in asin_cols:
                ranks[str(c).strip()] = _num(r[c])
            o["individual_asin_organic_ranks"] = ranks
            o["valid_asin_denominator"] = denom

            key = normalize_keyword(kw)
            e = keywords.setdefault(key, {"keyword_exact": kw, "keyword_normalized": key,
                                          "source_observations": []})
            e["source_observations"].append(o)
            rec["rows_loaded"] += 1
        sources.append(rec)

    # aggregate per keyword, preserving all observations
    out = []
    for key, e in keywords.items():
        obs = e["source_observations"]
        agg, rules, flags = {}, {}, set()
        for field in list(NUM_FIELDS):
            v, rule, missing = aggregate(obs, field)
            agg[field] = v
            rules[field] = rule
            if missing:
                flags.add(f"{field}_missing_in_all_sources")
        for o in obs:
            flags.update(o["missing_value_flags"])

        merged_ranks = {}
        denom = len(submitted) if submitted else max(
            (o.get("valid_asin_denominator") or 0) for o in obs)
        for o in obs:
            for a, r in o["individual_asin_organic_ranks"].items():
                if r is None:
                    continue
                if a not in merged_ranks or r < merged_ranks[a]:
                    merged_ranks[a] = r          # best observed rank per ASIN
        batch_support = {}
        for o in obs:
            b = o.get("source_batch")
            if b and any(v is not None for v in o["individual_asin_organic_ranks"].values()):
                n = sum(1 for v in o["individual_asin_organic_ranks"].values() if v is not None)
                batch_support[b] = max(batch_support.get(b, 0), n)

        out.append({
            "keyword_exact": e["keyword_exact"], "keyword_normalized": key,
            **{k: agg[k] for k in NUM_FIELDS},
            "aggregation_rules": rules,
            "missing_value_flags": sorted(flags),
            "individual_asin_organic_ranks": merged_ranks,
            "valid_asin_denominator": denom,
            "batch_support": batch_support,
            "observation_count": len(obs),
            "source_files": sorted({o["source_file"] for o in obs}),
            "source_batches": sorted({o["source_batch"] for o in obs if o["source_batch"]}),
            "source_observations": [{k: v for k, v in o.items() if k != "_date"} for o in obs],
        })
    out.sort(key=lambda x: x["keyword_normalized"])
    return {"source_validation": {"files": sources,
                                  "approved_batches": {k: len(v) for k, v in (batches or {}).items()},
                                  "total_keywords": len(out)},
            "keywords": out}
