#!/usr/bin/env python3
"""
creative.creative_diagnosis — Output 9: Post-Launch Creative Diagnosis.

Reads MANUALLY exported / entered metrics and diagnoses the likely creative
problem, while clearly separating creative causes from price, reviews, delivery,
keyword relevance, offer, seasonality and ad traffic. Recommends one manual change.

Input: a metrics.json (or --set key=val ...). All optional; missing -> INSUFFICIENT_DATA
for that branch, never assumed.

Usage:
  python creative_diagnosis.py <folder> [--metrics metrics.json]
"""
import sys, os, json, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edge_lib as L

def f(x):
    if x is None: return None
    try: return float(x)
    except Exception: return None

def _cfg(*keys, default=None):
    try:
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
        import config
        return config.get("creative_benchmarks", *keys, default=default)
    except Exception:
        return default

def diagnose(m):
    # zero-safe: a supplied 0 is a real observation, not missing. Only None is missing.
    imp = f(m.get("impressions")); clk = f(m.get("clicks"))
    supplied_ctr = f(m.get("ctr"))
    if supplied_ctr is not None:                 ctr = supplied_ctr
    elif imp is not None and clk is not None and imp > 0: ctr = clk/imp*100
    else:                                        ctr = None
    sess = f(m.get("sessions")); orders = f(m.get("orders"))
    supplied_usp = f(m.get("unit_session_pct"))
    if supplied_usp is not None:                 usp = supplied_usp
    elif sess is not None and orders is not None and sess > 0: usp = orders/sess*100
    else:                                        usp = None
    min_imp = _cfg("minimum_impressions_for_ctr_diagnosis", default=500) or 500
    ctr_low = _cfg("organic_ctr_low", default=None)   # None => no universal threshold; relative only
    conv_low = _cfg("conversion_low", default=None)
    out = []
    # visibility
    if imp is None:
        out.append(("INSUFFICIENT_DATA","impressions not provided","enter impressions/glance views","—","—","low"))
    elif imp < min_imp:
        out.append(("INSUFFICIENT_DATA", f"only {int(imp)} impressions (< {int(min_imp)} minimum for a CTR read)",
            "not enough traffic to diagnose creative yet","brand-new listing / low bids",
            "gather more impressions before judging the image", "low"))
    # CTR — zero-safe. A real 0% CTR with adequate impressions IS a strong signal.
    if ctr is not None and imp is not None and imp >= min_imp:
        if ctr == 0:
            out.append(("LOW_CTR", f"CTR 0% on {int(imp)} impressions (valid zero, not missing)",
                "main image / price / reviews / delivery promise — image is a prime suspect",
                "price or review gap vs competitors",
                "test the compliant improved-prep main image; verify price/reviews", "high"))
        elif ctr_low is not None and ctr < ctr_low:
            out.append(("LOW_CTR", f"CTR {ctr:.2f}% (< {ctr_low}% configured benchmark)",
                "main image / price / reviews / delivery", "price or review gap",
                "test the improved-prep main image", "medium"))
        else:
            out.append(("CTR_OK_CHECK_RELATIVE", f"CTR {ctr:.2f}% — no universal benchmark configured",
                "compare period-over-period / A vs B / vs comparable products (not a fixed threshold)",
                "—", "record baseline; judge against a comparison, not a global cutoff", "low"))
    elif imp is not None and imp >= min_imp and ctr is None:
        out.append(("INSUFFICIENT_DATA","clicks not provided","enter clicks (0 is valid) to compute CTR","—","—","low"))
    # conversion — zero-safe
    if usp is not None and sess is not None and sess >= (_cfg("minimum_sessions_for_conversion_diagnosis", default=100) or 100):
        if usp == 0:
            out.append(("STRONG_CTR_LOW_CONVERSION", f"0% conversion on {int(sess)} sessions (valid zero)",
                "detail-page images 2-9 / size / personalization clarity / proof / price / offer",
                "inaccurate traffic or weak offer",
                "add real proof (img 3) + clearer size (img 5) & personalization (img 4)", "high"))
        elif conv_low is not None and usp < conv_low:
            out.append(("STRONG_CTR_LOW_CONVERSION", f"conversion {usp:.1f}% (< {conv_low}% benchmark)",
                "detail-page images / size / personalization / proof / price", "weak offer",
                "strengthen proof + size + personalization images", "medium"))
    # qualitative signals
    if int(f(m.get("personalization_questions") or 0) or 0) >= 3:
        out.append(("PERSONALIZATION_CONFUSION","buyers asking how personalization works",
            "personalization images/copy unclear","—","strengthen image 4 steps + bullet 3","medium"))
    if int(f(m.get("size_returns") or 0) or 0) >= 2:
        out.append(("SIZE_CONFUSION","size-related returns/questions",
            "size chart weak","—","add accurate measured size chart (image 5)","medium"))
    if int(f(m.get("delivery_complaints") or 0) or 0) >= 2:
        out.append(("DELIVERY_CONCERN","delivery complaints/cancellations",
            "delivery expectation vs FBM reality — NOT a creative fix","late shipment",
            "set image 9 + handling time to match real production","high"))
    if not out:
        out.append(("INSUFFICIENT_DATA","no usable metrics","enter at least impressions + clicks","—","—","low"))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder"); ap.add_argument("--metrics", default=None)
    a = ap.parse_args()
    mp = a.metrics or os.path.join(a.folder,"metrics.json")
    if not os.path.exists(mp):
        # write a template
        tmpl = {"impressions":None,"clicks":None,"ctr":None,"sessions":None,"orders":None,
                "unit_session_pct":None,"ad_ctr":None,"ad_conversion":None,
                "personalization_questions":0,"size_returns":0,"delivery_complaints":0}
        L.save_json(mp, tmpl); print("Wrote metrics template:", mp, "— fill it and re-run."); return
    with open(mp, encoding="utf-8") as fh: m = json.load(fh)
    diags = diagnose(m)
    rows = ""
    for d in diags:
        code, ev, cause, alt, action, conf = d
        rows += (f"<tr><td>{L.badge(code) if code in L._BADGE else '<b>'+L.esc(code)+'</b>'}</td>"
                 f"<td>{L.esc(ev)}</td><td>{L.esc(cause)}</td><td class='muted'>{L.esc(alt)}</td>"
                 f"<td>{L.esc(action)}</td><td style='text-align:center'>{L.esc(conf)}</td></tr>")
    body = (f"<div class='card'><table><tr><th>Diagnosis</th><th>Evidence</th><th>Likely cause</th>"
            f"<th>Alternative</th><th>Recommended manual change</th><th>Confidence</th></tr>{rows}</table>"
            f"<div class='why'>Not every conversion problem is the images. This separates creative causes from "
            f"<b>price, reviews, delivery, keyword relevance, offer, seasonality and ad traffic</b> — fix the real one.</div></div>")
    html = L.page("Post-Launch Creative Diagnosis","Creative Edge · Output 9",
                  "Turns manual CTR/conversion data into one prioritized creative (or non-creative) action.", body)
    out = os.path.join(a.folder,"CREATIVE-DIAGNOSIS.html")
    L.save(out, html); L.save_json(os.path.join(a.folder,"CREATIVE-DIAGNOSIS.json"),
        {"diagnoses":[{"code":d[0],"evidence":d[1],"likely_cause":d[2],"alternative":d[3],
                       "recommended_change":d[4],"confidence":d[5]} for d in diags]})
    print("✅ diagnosis written:", out)
    for d in diags: print("  -", d[0], "→", d[4])

if __name__ == "__main__":
    main()
