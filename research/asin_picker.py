#!/usr/bin/env python3
"""
STAGE 2a  ·  ASIN PICKER  — pick the best 10 ASINs to feed Cerebro (Reverse ASIN).

Input : one or more Helium 10 XRAY exports (.xlsx/.csv) from a seed search,
        plus the seed phrase and (optionally) the anchor + product words.
Output: a ranked best-10 batch WITH a why/how reason per ASIN, a rejected list
        with reasons, a mix check (top vs newbie vs off-target), and a clean
        paste-ready list of 10 ASINs for Cerebro.

Rule it enforces (from SOP Part 2):
  - garbage ASIN in  ->  garbage keywords out.
  - keep only: right product type + real sales + brand-safe + (prefer) MFN.
  - mix 3-4 proven top sellers  +  4-5 newbies-with-sales  +  0 off-target.

Never touches Seller Central. Reads exported files only.
NOT legal advice — brand screen is risk-reduction, verify at tmsearch.uspto.gov.

Usage:
  python asin_picker.py <folder_or_file> --seed "custom nurse sweatshirt"
         [--anchor nurse] [--product "sweatshirt,hoodie,crewneck,pullover"]
         [--exclude "jacket,jean,denim,scrub top,lab coat"] [--out runs/<niche>]
"""
# _sibling shim (v2 folder layout)
import sys as _sys, os as _os
for _d in ('compliance','core','economics','listing','feasibility'):
    _p=_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),'..',_d)
    if _os.path.isdir(_p) and _p not in _sys.path: _sys.path.insert(0,_p)
import sys, os, re, glob, argparse, warnings
warnings.filterwarnings("ignore")
import pandas as pd
pd.options.mode.chained_assignment = None

# --- optional trademark guard (shared with the pipeline) -----------------------
try:                                        # one IP engine for the whole pipeline
    from ip_guard import check as tm_check   # ambiguity-aware (stitch->CAUTION, not HIGH)
except Exception:
    try:
        from tm_guard import check as tm_check
    except Exception:
        def tm_check(kw):                    # graceful fallback if neither is on path
            return ("OK", "ip_guard/tm_guard not loaded")

STOP = set("""a an the and or of to for in on with your you my our we us this that is are be it as at by
from so no not just all more most every any one for women men womens mens ladies unisex custom
personalized embroidered embroidery name gift gifts""".split())

GARMENTS = ["sweatshirt","hoodie","crewneck","pullover","sweater","jumper","tee","tshirt","t-shirt",
            "shirt","tank","longsleeve","long sleeve"]

# A product named in the seed IS the product type to filter on, together with the
# substitutes a buyer would cross-shop. Without this a 'nurse jacket' seed silently
# falls back to GARMENTS and rejects every jacket in the Xray.
FAMILIES = [["sweatshirt","hoodie","crewneck","pullover","sweater","jumper"],
            ["jacket","bomber","windbreaker","coat","vest"],
            ["scrub","scrubs"],
            ["tee","tshirt","t-shirt","shirt","tank","longsleeve","long sleeve"]]

def products_from_seed(seed):
    """'personalized nurse jacket' -> the jacket family. No product word -> GARMENTS."""
    tl = (seed or "").lower()
    for fam in FAMILIES:
        if any(w in tl for w in fam):
            return fam
    return GARMENTS

def num(x):
    """Parse '1,234.5' / '$12' / '27' / nan -> float, else 0.0."""
    if pd.isna(x): return 0.0
    s = re.sub(r"[^0-9.\-]", "", str(x))
    try: return float(s) if s not in ("", "-", ".") else 0.0
    except Exception: return 0.0

def load_xray(path):
    files = []
    if os.path.isdir(path):
        for e in ("*.xlsx","*.xls","*.csv"):
            files += glob.glob(os.path.join(path, e))
    else:
        files = [path]
    frames = []
    for f in sorted(files):
        try:
            df = pd.read_excel(f) if f.lower().endswith((".xlsx","xls")) else pd.read_csv(f)
            df["__src"] = os.path.basename(f); frames.append(df)
        except Exception as ex:
            print(f"  ! skip {os.path.basename(f)}: {ex}")
    if not frames:
        sys.exit("No Xray files found in " + path)
    return pd.concat(frames, ignore_index=True)

def col(df, *names):
    for n in names:
        if n in df.columns: return n
    # loose match
    low = {c.lower().strip(): c for c in df.columns}
    for n in names:
        if n.lower() in low: return low[n.lower()]
    return None

def anchor_from_seed(seed):
    toks = [t for t in re.findall(r"[a-z0-9]+", seed.lower()) if t not in STOP and t not in GARMENTS]
    return toks[0] if toks else (seed.lower().split()[0] if seed else "")

def build(path, seed, anchor, products, excludes, outdir):
    df = load_xray(path)
    C = {
        "asin":  col(df,"ASIN"),
        "brand": col(df,"Brand"),
        "title": col(df,"Product Details","Title"),
        "price": col(df,"Price  $","Price"),
        "rev":   col(df,"ASIN Revenue","Parent Level Revenue","Revenue"),
        "reviews":col(df,"Review Count","Reviews"),
        "rating":col(df,"Ratings","Rating"),
        "ful":   col(df,"Fulfillment"),
        "age":   col(df,"Seller Age (mo)"),
        "imgs":  col(df,"Images"),
        "cat":   col(df,"Category"),
    }
    if not C["asin"] or not C["title"]:
        sys.exit("Xray missing ASIN or Product Details/Title column.")

    # dedup on ASIN, keep the row with the highest revenue
    df["_rev"] = df[C["rev"]].map(num) if C["rev"] else 0
    df = df.sort_values("_rev", ascending=False).drop_duplicates(subset=C["asin"], keep="first")

    prod_words = products or products_from_seed(seed)
    prod_words = [p.strip().lower() for p in prod_words if p.strip()]
    excl = [e.strip().lower() for e in excludes if e.strip()]

    kept, rejected = [], []
    for _, r in df.iterrows():
        asin  = str(r[C["asin"]]).strip()
        title = str(r[C["title"]]).strip()
        tl    = title.lower()
        brand = str(r[C["brand"]]).strip() if C["brand"] else ""
        price = num(r[C["price"]]) if C["price"] else 0
        rev   = num(r["_rev"])
        revw  = int(num(r[C["reviews"]])) if C["reviews"] else 0
        ful   = str(r[C["ful"]]).strip().upper() if C["ful"] else ""
        age   = num(r[C["age"]]) if C["age"] else 0
        imgs  = int(num(r[C["imgs"]])) if C["imgs"] else 0

        reasons = []
        # 1) relevance gate: must contain anchor + a product-type word
        has_anchor = (anchor in tl) if anchor else True
        has_prod   = any(p in tl for p in prod_words)
        hit_excl   = next((e for e in excl if e in tl), None)
        if hit_excl:
            rejected.append((asin, title, brand, rev, revw, ful, f"OFF-TARGET: contains '{hit_excl}'")); continue
        if not has_anchor:
            rejected.append((asin, title, brand, rev, revw, ful, f"OFF-TARGET: no '{anchor}' in title")); continue
        if not has_prod:
            rejected.append((asin, title, brand, rev, revw, ful, "OFF-TARGET: not the product type")); continue

        # 2) brand / trademark screen — at ASIN-input stage only MAJOR brands matter
        #    (their keywords would be branded). CAUTION on uncommon words is ignored
        #    here on purpose; deep brand screening happens at the listing stage.
        tstat, treason = tm_check(brand + " " + title)
        if tstat == "HIGH":
            rejected.append((asin, title, brand, rev, revw, ful, f"TRADEMARK: {treason}")); continue

        # 3) alive? need a real sale signal
        if rev <= 0 and revw <= 0:
            rejected.append((asin, title, brand, rev, revw, ful, "DEAD: 0 revenue & 0 reviews")); continue

        # 4) fulfillment note (prefer MFN — matches our model)
        if ful and ful not in ("MFN","FBM"): reasons.append(f"fulfillment {ful} (not MFN)")

        kept.append(dict(asin=asin,title=title,brand=brand,price=price,rev=rev,
                         reviews=revw,ful=ful or "?",age=age,imgs=imgs,note="; ".join(reasons)))

    if not kept:
        sys.exit("No relevant ASINs survived the filter — loosen --anchor/--product or check the seed.")

    # classify by ACTIVE SALES (revenue), never by review count alone.
    # TOP    = has real revenue AND in the upper third of positive-revenue listings.
    # NEWBIE = has some sale signal but isn't a proven top seller.
    posrev = sorted([k["rev"] for k in kept if k["rev"] > 0], reverse=True)
    top_rev_cut = posrev[max(0, len(posrev)//3 - 1)] if posrev else 0
    for k in kept:
        if k["rev"] > 0 and k["rev"] >= top_rev_cut:
            k["klass"] = "TOP"
        else:
            k["klass"] = "NEWBIE"
        if k["rev"] <= 0:   # alive via reviews but no revenue estimate — flag it, never TOP
            k["klass"] = "NEWBIE"
            k["note"] = (k["note"] + "; " if k["note"] else "") + "⚠ no revenue estimate — weak signal"

    tops    = sorted([k for k in kept if k["klass"]=="TOP"],    key=lambda x:-x["rev"])
    newbies = sorted([k for k in kept if k["klass"]=="NEWBIE"], key=lambda x:-x["rev"])

    # priority order: proven sellers first (highest revenue), then newbies.
    # rank 1 = check first. Batches of 10 = one Cerebro run each.
    ranked = tops + newbies
    for i, k in enumerate(ranked, 1):
        k["rank"] = i
        k["batch"] = (i - 1) // 10 + 1
    batches = [ranked[i:i+10] for i in range(0, len(ranked), 10)]

    short = len(ranked) < 8   # a healthy batch is 8-10 tightly-relevant ASINs
    write_report(seed, anchor, prod_words, excl, kept, rejected, tops, newbies, ranked, batches, outdir, short)
    if short:
        print(f"\n⚠️  SHORT BATCH: only {len(ranked)} relevant ASIN(s). This Xray does not have"
              f"\n    enough on-target listings. Xray a DEDICATED '{seed}' search page on"
              f"\n    amazon.com (not a mixed/adjacent search) to reach 8-10 before running Cerebro.")
    return ranked

def money(x): return f"${x:,.0f}"

def write_report(seed, anchor, prods, excl, kept, rejected, tops, newbies, ranked, batches, outdir, short=False):
    os.makedirs(outdir, exist_ok=True)
    p = os.path.join(outdir, "ASIN-BATCH-REPORT.md")
    L = []
    L.append(f"# ASIN BATCH — reverse-ASIN input for Cerebro")
    if short:
        L.append(f"> ⚠️ **SHORT BATCH — only {len(ranked)} on-target ASIN(s).** This Xray is not a clean "
                 f"`{seed}` pull. Xray a **dedicated '{seed}' search page** on amazon.com (not a mixed or "
                 f"adjacent search) so 8-10 real competitors are present, then re-run. A tiny batch = weak "
                 f"reverse-ASIN keywords.\n")
    L.append(f"**Seed:** `{seed}`  ·  **Anchor:** `{anchor}`  ·  **Product:** {', '.join(prods)}")
    if excl: L.append(f"**Excluded off-target:** {', '.join(excl)}")
    L.append(f"\n**Scanned:** {len(kept)+len(rejected)} ASINs → **{len(kept)} relevant**, {len(rejected)} rejected.  "
             f"**{len(batches)} batch{'es' if len(batches)!=1 else ''}** of ≤10 "
             f"({sum(1 for b in ranked if b['klass']=='TOP')} top + "
             f"{sum(1 for b in ranked if b['klass']=='NEWBIE')} newbie).  Ranked #1 = check first.")
    for bi, b in enumerate(batches, 1):
        L.append(f"\n---\n## ✅ BATCH {bi} of {len(batches)} — PASTE INTO CEREBRO  "
                 f"(priority {b[0]['rank']}–{b[-1]['rank']})\n```")
        L.append("  ".join(x["asin"] for x in b)); L.append("```")
    L.append("_(one Cerebro run per batch · set 'competitors' / # of competing products ≥ 3–4 before export)_")

    L.append("\n---\n## Priority ranking — why each one is in\n")
    L.append("| # | Batch | ASIN | Class | Reviews | Est $/mo | Ful | Brand | Why it's a good input |")
    L.append("|---|--:|---|---|--:|--:|---|---|---|")
    for b in ranked:
        why = ("proven current seller (has revenue) — its keywords convert" if b["klass"]=="TOP"
               else "selling but young/low — shows the realistic way in")
        if b["note"]: why += f" · {b['note']}"
        L.append(f"| {b['rank']} | {b['batch']} | {b['asin']} | {b['klass']} | {b['reviews']} | {money(b['rev'])} | "
                 f"{b['ful']} | {b['brand'][:22]} | {why} |")

    L.append("\n## Mix check (why this ratio)\n")
    L.append("| Group | Relevant | Gives us | Target |")
    L.append("|---|--:|---|---|")
    nt = sum(1 for b in ranked if b['klass']=='TOP'); nn = len(ranked)-nt
    L.append(f"| TOP sellers | {nt} | money keywords (what buyers actually buy) | 3–4 |")
    L.append(f"| NEWBIES w/ sales | {nn} | enterable keywords (realistic for us) | 4–5 |")
    L.append(f"| Off-target | 0 | — (excluded on purpose) | 0 |")

    if rejected:
        L.append("\n---\n## ❌ Rejected (and why) — this is the pollution we keep out\n")
        L.append("| ASIN | Rev | Rev'ws | Brand | Reason |")
        L.append("|---|--:|--:|---|---|")
        for a,t,br,rv,rw,fl,why in sorted(rejected, key=lambda x:-x[3])[:30]:
            L.append(f"| {a} | {money(rv)} | {rw} | {br[:20]} | {why} |")

    L.append("\n---\n## Next step\n"
             "1. Copy **BATCH 1** above (highest priority) → **Helium 10 → Cerebro** → paste → Run. "
             "Repeat per batch; batch 1 alone is enough to start.\n"
             "2. Filter in Cerebro: **Search Volume ≥ 100**, **# competitors ≥ 3–4**, prefer **Title Density 0–5** → Export.\n"
             "3. Drop that Cerebro export into the niche folder and run the keyword pipeline "
             "(`phaseA_master.py`) — it does trademark + relevance + scoring and gives the listing keywords.\n\n"
             "> Guardrail: this picker is a filter, not a decision. The keyword pipeline + your approval decide the listing.")
    open(p,"w",encoding="utf-8").write("\n".join(L))
    print("Wrote", p)
    # console summary
    for bi, b in enumerate(batches, 1):
        print(f"\n=== BATCH {bi}/{len(batches)} ({len(b)}) : priority {b[0]['rank']}-{b[-1]['rank']} ===")
        print("  ".join(x["asin"] for x in b))
    print(f"\nrelevant {len(kept)} | rejected {len(rejected)} | top {len(tops)} newbie {len(newbies)}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="Xray folder or file")
    ap.add_argument("--seed", required=True)
    ap.add_argument("--anchor", default=None, help="required token (default: from seed)")
    ap.add_argument("--product", default=None, help="comma list of product-type words")
    ap.add_argument("--exclude", default="", help="comma list of off-target words")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    anchor = (a.anchor or anchor_from_seed(a.seed)).lower()
    products = [x for x in (a.product.split(",") if a.product else [])]
    excludes = a.exclude.split(",") if a.exclude else []
    outdir = a.out or os.path.join("runs", re.sub(r"[^a-z0-9]+","_",a.seed.lower()).strip("_"))
    build(a.path, a.seed, anchor, products, excludes, outdir)

if __name__ == "__main__":
    main()
