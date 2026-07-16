#!/usr/bin/env python3
"""
STAGE 0.5  ·  CROSS-DEMAND VALIDATION  —  GO / TEST / SKIP scorecard.

Triangulates a candidate keyword across Amazon (the anchor) + 5 off-Amazon
sources you check manually: Etsy, Google Trends, Pinterest, eBay, Reddit.
No APIs, no scraping — you eyeball each source (2 min each) and fill one CSV;
this scores it. Amazon volume/competition auto-fills from master-keywords.xlsx
when present. Runs before the listing builder so you never build for a niche
that only looks good on Amazon.

RUBRIC (each 0 / 1 / 2):
  Amazon SV   : >=500 =2 · 150-499 =1 · <150 =0          (real demand)
  Amazon TD   : 0-5 =2 · 6-15 =1 · >15 =0                (low = easy to rank)
  Etsy        : bestseller/ >=500 reviews =2 · 50-499 =1 · <50/none =0
  Google Trend: up/rising =2 · flat =1 · down =0
  Pinterest   : rising =2 · some =1 · none =0
  eBay sold   : >=50 sold =2 · 10-49 =1 · <10 =0
  Reddit      : clear buy/gift intent =2 · general interest =1 · none =0

VERDICT (Amazon is a GATE — if it fails, nothing else saves it):
  SKIP  if Amazon (SV+TD) <= 1   OR total external <= 2
  GO    if Amazon >= 3  AND external >= 6  AND >=2 external sources scored 2
  TEST  everything in between

Usage:
  python demand_score.py <input.csv> [--master master-keywords.xlsx] [--out .]
  (input columns: keyword, amazon_sv, amazon_td, etsy, ebay_sold, trend, pinterest, reddit
   — leave amazon_* blank to auto-fill from the master sheet by keyword match)
"""
import sys, os, re, argparse
import pandas as pd

def n(x):
    try: return float(re.sub(r"[^0-9.\-]","",str(x)) or 0)
    except: return 0.0

def word(x):
    return re.sub(r"[^a-z]","",str(x).lower())

def s_sv(v):   v=n(v); return 2 if v>=500 else 1 if v>=150 else 0
def s_td(v):
    v=n(v); return 2 if v<=5 else 1 if v<=15 else 0
def s_etsy(v):
    w=word(v)
    if w in ("bestseller","proven","strong","yes","high"): return 2
    if w in ("some","medium","mid","maybe"): return 1
    if w in ("none","no","low","weak",""): return 0
    v=n(v); return 2 if v>=500 else 1 if v>=50 else 0          # else treat as review count
def s_dir(v):
    w=word(v)
    if w in ("up","rising","rise","grow","growing","strong","high","2"): return 2
    if w in ("flat","stable","steady","same","mid","1"): return 1
    return 0
def s_pin(v):  return s_dir(v)
def s_ebay(v):
    w=word(v)
    if w in ("high","strong","many"): return 2
    if w in ("some","mid"): return 1
    v=n(v); return 2 if v>=50 else 1 if v>=10 else 0
def s_reddit(v):
    w=word(v)
    if w in ("buy","buying","intent","gift","strong","high","2"): return 2
    if w in ("interest","some","talk","mid","1"): return 1
    return 0

def verdict(am, ext, strong2, checked):
    # checked = how many of the 5 external sources were actually looked at (not blank).
    missing = 5 - checked
    if am <= 1:            return "SKIP", "Amazon anchor fails (low SV / crowded)"
    if checked <= 2:       return "INCOMPLETE", f"only {checked}/5 outside sources checked — gather more before deciding"
    if am >= 3 and ext >= 6 and strong2 >= 2:
        if missing >= 2:   return "TEST", f"looks strong but only {checked}/5 checked — test small / verify the rest"
        return "GO", "Amazon solid + demand confirmed off-Amazon"
    if ext <= 2:           return "SKIP", "checked sources show no real demand"
    return "TEST", "mixed signal — test small before committing"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--master", default=None)
    ap.add_argument("--out", default=".")
    a = ap.parse_args()

    df = pd.read_csv(a.csv)
    df.columns = [c.strip().lower() for c in df.columns]
    for c in ("amazon_sv","amazon_td","etsy","ebay_sold","trend","pinterest","reddit"):
        if c not in df.columns: df[c] = ""

    # auto-fill Amazon SV/TD from a master-keywords sheet if provided / found
    mpath = a.master
    if not mpath:
        cand = os.path.join(os.path.dirname(a.csv) or ".", "master-keywords.xlsx")
        mpath = cand if os.path.exists(cand) else None
    look = {}
    if mpath and os.path.exists(mpath):
        try:
            mk = pd.read_excel(mpath, sheet_name="All Keywords")
            for _, r in mk.iterrows():
                look[str(r["keyword"]).strip().lower()] = (n(r.get("search_volume")), n(r.get("title_density")))
        except Exception as e:
            print("  (could not read master sheet:", e, ")")

    rows = []
    for _, r in df.iterrows():
        kw = str(r["keyword"]).strip()
        sv, td = n(r["amazon_sv"]), n(r["amazon_td"])
        if (not sv) and kw.lower() in look: sv, td = look[kw.lower()]
        def provided(x):   # blank / NaN = NOT checked; "none"/"0" = checked-but-weak
            return str(x).strip().lower() not in ("", "nan")
        ext_src = {"Etsy":"etsy","Trend":"trend","Pin":"pinterest","eBay":"ebay_sold","Reddit":"reddit"}
        checked = sum(1 for col in ext_src.values() if provided(r[col]))
        sc = {
            "SV": s_sv(sv), "TD": (s_td(td) if sv > 0 else 0), "Etsy": s_etsy(r["etsy"]),
            "Trend": s_dir(r["trend"]), "Pin": s_pin(r["pinterest"]),
            "eBay": s_ebay(r["ebay_sold"]), "Reddit": s_reddit(r["reddit"]),
        }
        am = sc["SV"] + sc["TD"]
        ext = sc["Etsy"] + sc["Trend"] + sc["Pin"] + sc["eBay"] + sc["Reddit"]
        strong2 = sum(1 for k in ("Etsy","Trend","Pin","eBay","Reddit") if sc[k] == 2)
        v, why = verdict(am, ext, strong2, checked)
        rows.append(dict(kw=kw, sv=int(sv), td=int(td), sc=sc, am=am, ext=ext, total=am+ext,
                         checked=checked, verdict=v, why=why))

    order = {"GO":0,"TEST":1,"INCOMPLETE":2,"SKIP":3}
    rows.sort(key=lambda x: (order[x["verdict"]], -x["total"]))
    write(rows, a.out)

def bar(v): return {0:"▽",1:"●",2:"▲"}[v]

def write(rows, out):
    os.makedirs(out, exist_ok=True)
    md = os.path.join(out, "DEMAND-SCORECARD.md")
    L = ["# Cross-Demand Validation — GO / TEST / SKIP",
         "Amazon is the anchor (gate). Off-Amazon sources confirm real, buying, rising demand.",
         "▲ strong (2) · ● ok (1) · ▽ weak (0)", "",
         "| Verdict | Keyword | Amz SV | Amz TD | Etsy | Trend | Pin | eBay | Reddit | Total | Read |",
         "|---|---|--:|--:|:--:|:--:|:--:|:--:|:--:|--:|---|"]
    ico = {"GO":"🟢 GO","TEST":"🟡 TEST","INCOMPLETE":"⚪ INCOMPLETE","SKIP":"🔴 SKIP"}
    for r in rows:
        s = r["sc"]
        L.append(f"| {ico[r['verdict']]} | **{r['kw']}** | {r['sv']} {bar(s['SV'])} | {r['td']} {bar(s['TD'])} | "
                 f"{bar(s['Etsy'])} | {bar(s['Trend'])} | {bar(s['Pin'])} | {bar(s['eBay'])} | {bar(s['Reddit'])} | "
                 f"{r['total']}/14 | {r['why']} |")
    go = [r['kw'] for r in rows if r['verdict']=="GO"]
    L += ["", "## What to do", ""]
    if go: L.append(f"**Build these first (GO):** {', '.join(go)} → send to the listing builder.")
    else:  L.append("**No GO yet** — tighten the seed or gather one more strong source before building.")
    L += ["- 🟡 TEST = launch small / low PPC, watch 2–3 weeks before scaling.",
          "- 🔴 SKIP = don't build; the anchor or the demand isn't there.",
          "",
          "> Reminder: this scores YOUR manual observations. Garbage in = garbage out — "
          "check each source honestly (2 min each). Amazon numbers auto-fill from master-keywords.xlsx."]
    open(md,"w",encoding="utf-8").write("\n".join(L))
    print("Wrote", md)
    print("\n=== VERDICTS ===")
    for r in rows:
        print(f" {ico[r['verdict']]:9} {r['kw']:<36} total {r['total']}/14  ({r['why']})")

if __name__ == "__main__":
    main()
