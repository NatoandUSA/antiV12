#!/usr/bin/env python3
"""
PHASE A — Helium 10 -> Master Keyword Sheet + Manager & Opportunity Reports.
Usage:  python phaseA_master.py <folder_with_H10_csvs> <niche_name>
Reads Cerebro/Magnet (keyword-level) + Xray (product-level) exports, merges,
scores each keyword with an INTERNAL Opportunity Heuristic (our recommendation, not a known Amazon ranking formula), classifies intent,
recommends placement, and writes:
  <out>/master-keywords.xlsx      (All Keywords + Top Picks + Competitor Context)
  <out>/MANAGER-REPORT.md
  <out>/OPPORTUNITY-REPORT.md
No Amazon account access. Read-only analysis of files you export yourself.
"""
# _sibling shim (v2 folder layout)
import sys as _sys, os as _os
for _d in ('compliance','core','economics','listing','feasibility'):
    _p=_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),'..',_d)
    if _os.path.isdir(_p) and _p not in _sys.path: _sys.path.insert(0,_p)
import sys, os, glob, re
import pandas as pd, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:                                        # prefer the upgraded IP library if present
    from ip_guard import check as tm_check   # BLOCK->HIGH / REVIEW->CAUTION, 441-term library
except Exception:
    from tm_guard import check as tm_check   # fallback: base trademark guard

# ---------- robust H10 loader (handles BOM, comma/tab, latin-1) ----------
def load(path):
    if path.lower().endswith((".xlsx", ".xlsm", ".xls")):
        try:
            df = pd.read_excel(path)
        except Exception:
            return None
    else:
        for sep in [",", "\t"]:
            try:
                df = pd.read_csv(path, encoding="latin-1", sep=sep, engine="python", on_bad_lines="skip")
                if df.shape[1] >= 3:
                    break
            except Exception:
                df = None
    if df is None: return None
    df.columns = [str(c).replace('﻿','').replace('ï»¿','').strip().strip('"').strip()
                  for c in df.columns]
    return df

def numcol(s):
    return pd.to_numeric(s.astype(str).str.replace(r'[,$%]', '', regex=True).str.strip(),
                         errors="coerce")

# ---------- intent classification (bucket = placement; weight = our conversion-intent proxy) ----------
PRODUCT_NOUNS = {"shirt","tshirt","t-shirt","tee","sweatshirt","crewneck","hoodie","sweater",
 "pullover","jumper","mug","necklace","bracelet","ring","ornament","hat","cap","bag","tote","blanket"}
GIFT = {"gift","gifts"}
OCCASION = {"christmas","halloween","valentine","valentines","mother","mothers","father","fathers",
 "birthday","anniversary","wedding","graduation","thanksgiving","xmas"}
PERS = {"custom","personalized","personalised","monogram","monogrammed","name","photo","portrait","embroidered"}
AUDIENCE = {"mom","mum","mama","dad","papa","grandma","grandpa","nana","wife","husband","women","womens",
 "men","mens","girl","boy","nurse","teacher","bride","couple","couples","dog","cat","pet"}

def bucket(kw):
    w = set(re.findall(r"[a-z]+", kw.lower())); wc = len(kw.split())
    if w & GIFT: return "gift"
    if w & OCCASION: return "occasion"
    if w & PERS: return "personalization"
    if (w & PRODUCT_NOUNS) and (w & AUDIENCE): return "buyer-intent"
    if w & AUDIENCE: return "audience"
    if wc >= 4: return "long-tail"
    if w & PRODUCT_NOUNS: return "main"
    return "generic"

def intent_weight(kw):
    w = set(re.findall(r"[a-z]+", kw.lower()))
    if (w & GIFT) or (w & OCCASION) or ((w & PRODUCT_NOUNS) and (w & PERS)):
        return 1.30          # strong buyer/gift intent -> we weight higher (internal assumption, not Amazon fact)
    if w & PERS: return 1.20 # personalization intent
    return 1.00

SYN = {"sweatshirt":{"sweatshirt","crewneck","sweater","pullover","jumper"},
       "crewneck":{"sweatshirt","crewneck","sweater","pullover","jumper"},
       "shirt":{"shirt","tshirt","tee","tees"}, "tshirt":{"shirt","tshirt","tee","tees"},
       "tee":{"shirt","tshirt","tee","tees"},
       "hoodie":{"hoodie","hoody","hoodies"}, "sweater":{"sweater","sweatshirt","crewneck"},
       "necklace":{"necklace","pendant","chain"}, "bracelet":{"bracelet","bangle"},
       "mug":{"mug","cup","tumbler"}, "ornament":{"ornament","bauble"},
       "blanket":{"blanket","throw"}, "bag":{"bag","tote"}, "hat":{"hat","cap","beanie"}}

def build_anchors(niche):
    toks = [t for t in re.findall(r"[a-z]+", niche.lower()) if len(t) > 2]
    anchors = set()
    for t in toks:
        if t in SYN: anchors |= SYN[t]
        elif t in PRODUCT_NOUNS: anchors.add(t)
    return anchors  # empty -> fall back to all meaningful niche tokens

def placement(bkt):
    return {"main":"TITLE","buyer-intent":"TITLE + PPC exact","personalization":"TITLE/bullets",
            "gift":"bullets + PPC phrase","occasion":"description/A+ + backend",
            "audience":"bullets + backend","long-tail":"backend + PPC auto",
            "generic":"backend"}.get(bkt,"backend")

# ---------- load everything ----------
folder, niche = sys.argv[1], (sys.argv[2] if len(sys.argv)>2 else "niche")
files = (glob.glob(os.path.join(folder, "*.csv")) +
         glob.glob(os.path.join(folder, "*.xlsx")) +
         glob.glob(os.path.join(folder, "*.xls")))
kw_frames, xray_frames = [], []
for f in files:
    d = load(f)
    if d is None: continue
    cols = set(d.columns)
    if "Keyword Phrase" in cols and "Search Volume" in cols:
        kw_frames.append(d)
    elif "ASIN" in cols:
        xray_frames.append(d)

if not kw_frames:
    print("No Cerebro/Magnet keyword files found."); sys.exit(1)

# ---------- merge keyword files ----------
rows = []
for d in kw_frames:
    r = pd.DataFrame()
    r["keyword"] = d["Keyword Phrase"].astype(str).str.lower().str.strip()
    r["search_volume"] = numcol(d["Search Volume"])
    r["title_density"] = numcol(d["Title Density"]) if "Title Density" in d else np.nan
    r["competing"] = numcol(d["Competing Products"]) if "Competing Products" in d else np.nan
    r["kw_sales"] = numcol(d["Keyword Sales"]) if "Keyword Sales" in d else np.nan
    r["sv_trend"] = numcol(d["Search Volume Trend"]) if "Search Volume Trend" in d else np.nan
    r["cpr"] = numcol(d["CPR"]) if "CPR" in d else np.nan
    rows.append(r)
m = pd.concat(rows, ignore_index=True).dropna(subset=["keyword","search_volume"])
m = (m.groupby("keyword")
       .agg(search_volume=("search_volume","max"), title_density=("title_density","min"),
            competing=("competing","min"), kw_sales=("kw_sales","max"),
            sv_trend=("sv_trend","mean"), cpr=("cpr","min"))
       .reset_index())

# ---------- classify + INTERNAL Opportunity Heuristic (not Amazon's algorithm) ----------
med_td = m["title_density"].median(skipna=True)
m["title_density"] = m["title_density"].fillna(med_td)      # missing TD -> niche median (flagged)
m["tm_status"] = m["keyword"].map(lambda k: tm_check(k)[0])   # HIGH / CAUTION / OK
m["trademark"] = m["tm_status"].eq("HIGH")                    # HIGH = exclude from scoring
m["bucket"] = m["keyword"].apply(bucket)
m["placement"] = m["bucket"].apply(placement)

# --- data hygiene: drop malformed run-on rows (real search terms are short) ---
m = m[m["keyword"].str.split().str.len().between(1, 6)].reset_index(drop=True)

# --- RELEVANCE GATE: a keyword must be about OUR product (our hard filter) ---
anchors = build_anchors(niche)
niche_toks = {t for t in re.findall(r"[a-z]+", niche.lower()) if len(t) > 2}
if not niche_toks:
    # Relevance is decided by these tokens. A niche like "T1" or "p2" yields none,
    # which would silently mark EVERY keyword irrelevant and write empty Top Picks.
    sys.exit(f"Niche '{niche}' has no usable word (need a word of 3+ letters). "
             f"Pass the seed phrase instead, e.g. \"personalized nurse sweatshirt\" — "
             f"relevance is decided from these words, so a code-name matches nothing.")
# generic gender/age words are NOT a niche signal on their own (a bare "sweatshirt women"
# is generic apparel, not our custom product) — exclude them from what "qualifies" relevance.
GENERIC = {"women","womens","womans","men","mens","girl","girls","boy","boys","ladies","kids","adult","unisex"}
QUALIFY = (PERS | AUDIENCE | GIFT | OCCASION) - GENERIC
# a "customization" niche (name has custom/embroidered/personalized) needs the garment
# QUALIFIED by intent/audience/gift — a bare "white sweater" is generic retail, not us.
custom_niche = bool(niche_toks & PERS)
def relevant(kw):
    kt = set(re.findall(r"[a-z]+", kw.lower()))
    if anchors:
        if not (kt & anchors): return False              # must contain a product-anchor token
        if custom_niche and not (kt & QUALIFY): return False  # ...and be qualified, for custom niches
        return True
    return bool(kt & niche_toks)                          # fallback: share a niche token
m["relevant"] = m["keyword"].apply(relevant)

# 1) demand per unit competition (rankability)
base = m["search_volume"] / (m["title_density"] + 1)
# 2) buyer-intent (conversion tendency)
intent = m["keyword"].apply(intent_weight)
# 3) momentum — from H10 Search Volume Trend (we assume trend helps; not a proven Amazon weight)
trend = np.where(m["sv_trend"] > 0, 1.10, np.where(m["sv_trend"] < 0, 0.85, 1.00))
# 4) proven conversion signal: keyword sales / search volume (real velocity per keyword)
cr = (m["kw_sales"] / m["search_volume"]).replace([np.inf,-np.inf], np.nan).fillna(0)
conv = np.clip(0.9 + 5.0*cr, 0.9, 1.3)
m["opportunity"] = (base * intent * trend * conv).round(0)
m.loc[m["trademark"], "opportunity"] = 0                    # exclude branded terms
m.loc[~m["relevant"], "opportunity"] = 0                    # exclude off-product terms

m["intent_w"] = intent.round(2)
m = m.sort_values("opportunity", ascending=False).reset_index(drop=True)

# ---------- competitor context + BEATABILITY from Xray ----------
def tier(v, cuts, scores):
    for c,s in zip(cuts,scores):
        if v <= c: return s
    return scores[-1]

ctx = {}
if xray_frames:
    xr = pd.concat(xray_frames, ignore_index=True)
    def col(*keys):
        for c in xr.columns:
            if any(k in c.lower() for k in keys): return numcol(xr[c])
        return pd.Series(dtype=float)
    price = col("price"); rev = col("revenue")
    reviews = numcol(xr[[c for c in xr.columns if c.lower().strip()=="review count"][0]]) if any(c.lower().strip()=="review count" for c in xr.columns) else col("review count")
    images = col("images"); sellers = col("active sellers"); age = col("seller age")
    ful = [c for c in xr.columns if c.lower().strip()=="fulfillment"]
    ctx["n"] = len(xr)
    ctx["med_price"]   = round(price.median(),2) if len(price.dropna()) else None
    ctx["med_reviews"] = int(reviews.median()) if len(reviews.dropna()) else None
    ctx["med_images"]  = int(images.median()) if len(images.dropna()) else None
    ctx["med_sellers"] = round(sellers.median(),1) if len(sellers.dropna()) else None
    ctx["med_age_mo"]  = int(age.median()) if len(age.dropna()) else None
    mfn = fba = None
    if ful:
        fv = xr[ful[0]].astype(str).str.upper()
        mfn = int(fv.str.contains("MFN|MERCHANT|FBM").sum()); fba = int(fv.str.contains("FBA").sum())
        ctx["mfn"], ctx["fba"] = mfn, fba
    # --- Beatability 0-100 (higher = easier to win) ---
    parts = {}
    if ctx["med_reviews"] is not None:
        parts["reviews (low=beatable)"] = tier(ctx["med_reviews"],[50,100,200,400,800],[90,80,62,45,30,15])
    if mfn is not None and (mfn+fba)>0:
        parts["FBM lane open"] = round(100*mfn/(mfn+fba))
    if ctx["med_images"] is not None:
        parts["thin galleries"] = tier(ctx["med_images"],[4,6,8],[85,70,50,35])
    if ctx["med_age_mo"] is not None:
        parts["market still movable"] = tier(ctx["med_age_mo"],[12,24,48],[85,70,50,35])
    if ctx["med_sellers"] is not None:
        parts["low seller concentration"] = tier(ctx["med_sellers"],[1,3],[85,65,45])
    if parts:
        w = {"reviews (low=beatable)":.35,"FBM lane open":.20,"thin galleries":.20,
             "market still movable":.15,"low seller concentration":.10}
        num_ = sum(parts[k]*w.get(k,0) for k in parts); den = sum(w.get(k,0) for k in parts)
        ctx["beatability"] = round(num_/den) if den else None
        ctx["beat_parts"] = parts

# ---------- write master xlsx ----------
out = folder
xlsx = os.path.join(out, "master-keywords.xlsx")
top = m[(~m["trademark"]) & (m["relevant"]) & (m["opportunity"]>0)].head(15)
with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
    m.to_excel(xw, sheet_name="All Keywords", index=False)
    top.to_excel(xw, sheet_name="Top Picks", index=False)
    if ctx:
        flat = {k:v for k,v in ctx.items() if k != "beat_parts"}
        if ctx.get("beat_parts"): flat.update({f"beat: {k}":v for k,v in ctx["beat_parts"].items()})
        pd.DataFrame([flat]).to_excel(xw, sheet_name="Competitor Context", index=False)

# ---------- reports ----------
def md_row(r):
    return f"| {r.keyword} | {int(r.search_volume)} | {int(r.title_density)} | {int(r.opportunity)} | {r.bucket} | {r.placement} |"

topN = top.head(10)
opp = ["# Opportunity Report — "+niche, "",
 "Ranked by our **Internal Opportunity Heuristic = (SV / (Title Density+1)) × intent × trend × conversion**. This is OUR recommendation, not Amazon's private ranking formula.",
 "Trademark terms excluded. Full data in `master-keywords.xlsx`.", "",
 "## Top 10 keywords", "",
 "| Keyword | SV | Title Density | Opportunity | Bucket | Use in |","|---|---|---|---|---|---|"]
opp += [md_row(r) for r in topN.itertuples()]
opp += ["", "## Keyword groups (for placement)"]
for b in ["main","buyer-intent","personalization","gift","occasion","audience","long-tail"]:
    kws = m[(m["bucket"]==b)&(~m["trademark"])&(m["relevant"])&(m["opportunity"]>0)].head(6)["keyword"].tolist()
    if kws: opp += [f"- **{b}** → {placement(b)}: " + " · ".join(kws)]
open(os.path.join(out,"OPPORTUNITY-REPORT.md"),"w",encoding="utf-8").write("\n".join(opp))

# guard: nothing safe + relevant survived — write a clean blocked report, don't crash
if len(topN) == 0:
    n_tm  = int(m["trademark"].sum())
    n_off = int((~m["relevant"]).sum())
    blocked = ["# Manager Report — "+niche, "", "**Read this first.**", "",
        "## ⛔ No viable keyword found",
        f"- Analyzed **{len(m)}** keywords, but **0** survived the safe + relevant + scored filter.",
        f"- Excluded: **{n_tm}** as trademark/brand, **{n_off}** as off-target/off-product.",
        "", "## Do next",
        "1. Check the seed — is it the right product angle?",
        "2. Re-run the ASIN picker on a **dedicated** search (SHORT BATCH = weak reverse keywords).",
        "3. If the niche is genuinely branded/off-target, pick a different lane (see NEW-SEEDS).",
        "", "_No listing should be built from this run._"]
    open(os.path.join(out,"MANAGER-REPORT.md"),"w",encoding="utf-8").write("\n".join(blocked))
    print(f"phaseA: no viable keyword (analyzed {len(m)}, all excluded). Wrote blocked Manager Report.")
    sys.exit(4)   # documented: 4 = no viable keyword survived filtering

best = topN.iloc[0]
n_scored = int(((~m["trademark"]) & (m["relevant"]) & (m["opportunity"] > 0)).sum())
mgr = ["# Manager Report — "+niche, "", "**Read this first.**", "",
 f"- **Keywords analyzed:** {len(m)}  ·  **relevant + clean scored:** {n_scored}",
 f"- **#1 opportunity:** `{best.keyword}` (SV {int(best.search_volume)}, Title Density {int(best.title_density)}, score {int(best.opportunity)})",
 f"- **Why it wins:** high demand vs. low ranking difficulty" + (", rising trend" if best.sv_trend>0 else "") + (", proven keyword sales" if best.kw_sales>0 else "") + ".",""]
if ctx:
    line = f"- **Competitor context:** {ctx.get('n','?')} products"
    if ctx.get("med_price"): line += f" · median price ${ctx['med_price']}"
    if ctx.get("med_reviews") is not None: line += f" · median reviews {ctx['med_reviews']}"
    if ctx.get("med_images") is not None: line += f" · median images {ctx['med_images']}"
    if ctx.get("mfn") is not None: line += f" · {ctx['mfn']} MFN / {ctx['fba']} FBA"
    mgr += [line]
    if ctx.get("beatability") is not None:
        b = ctx["beatability"]
        verdict = ("HIGHLY BEATABLE — attack" if b>=70 else "BEATABLE with effort" if b>=50
                   else "TOUGH — need a real edge" if b>=35 else "AVOID — entrenched")
        mgr += [f"- **Beatability: {b}/100 → {verdict}**",
                "  - " + " · ".join(f"{k} {v}" for k,v in ctx["beat_parts"].items())]
    mgr += [""]
mgr += ["## Do next (ranked)",
 f"1. **Build around `{best.keyword}`** and the top buyer-intent terms — lowest rank difficulty for the demand.",
 "2. **Verify** the top 10 exact phrases before writing the title (never target a 0-search phrase).",
 "3. **Pick the angle** in the Opportunity Report, then run Stage 3 (listing build).",
 "", "## Risk watch",
 f"- **Trademark:** {int(m['trademark'].sum())} keyword(s) auto-excluded as known brands. Never target brands/characters.",
 "- Title Density shown as niche-median where H10 didn't report it (verify those before relying on them)."]
caution = m[(m["tm_status"]=="CAUTION")&(m["relevant"])&(m["opportunity"]>0)].head(8)["keyword"].tolist()
if caution:
    mgr += [f"- **⚠️ Verify before use (unknown token — possible brand/name):** {' · '.join(caution)} — check tmsearch.uspto.gov."]
open(os.path.join(out,"MANAGER-REPORT.md"),"w",encoding="utf-8").write("\n".join(mgr))

print(f"OK — {len(m)} keywords. Wrote master-keywords.xlsx + 2 reports to {out}")
print(f"#1: {best.keyword} | SV {int(best.search_volume)} TD {int(best.title_density)} score {int(best.opportunity)}")
