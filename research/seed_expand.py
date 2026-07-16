#!/usr/bin/env python3
"""
SEED EXPANDER  ·  closes the loop.  master-keywords.xlsx  ->  NEW SEED candidates.

After a Cerebro run is scored by phaseA_master.py, this finds the fresh sub-angles
hiding in the cleaned keyword set — new audiences, occasions, products, or
personalization angles you have NOT mined yet — and proposes them as the seeds
for the NEXT round (Step 1: Xray + Cerebro again).

It only uses relevant, brand-SAFE keywords (trademark terms already excluded by
phaseA + ip_guard). Output ranks candidates by real demand and rankability.

Usage:
  python seed_expand.py <run_folder> [--anchor nurse] [--minsv 150] [--top 12]
  (reads <run_folder>/master-keywords.xlsx -> writes <run_folder>/NEW-SEEDS.md)
"""
import sys, os, re, argparse
import pandas as pd

STOP = set("""a an the and or of to for in on with your you my our we us this that is are be it as at by from
so no not just all more most any one two 2 3 for gift gifts custom personalized personalised embroidered
embroidery name with add""".split())
GENERIC = {"women","womens","womans","men","mens","man","woman","girl","girls","boy","boys","ladies","kids",
           "kid","adult","adults","unisex","female","male","her","him","his"}
GARMENTS = {"sweatshirt","sweatshirts","hoodie","hoodies","crewneck","crewnecks","pullover","pullovers",
            "sweater","sweaters","jumper","tee","tees","tshirt","tshirts","shirt","shirts","tank","sweat",
            "apparel","clothing","outfit","top","tops"}
ANGLE = {"audience":"who it's for","occasion":"when it's bought","product":"new garment/form",
         "personalization":"how it's made","buyer-intent":"strong intent","gift":"gift angle","other":"angle"}

def num(s):
    try: return float(re.sub(r"[^0-9.\-]","",str(s)) or 0)
    except: return 0.0

def infer_anchor(df):
    from collections import Counter
    c = Counter()
    for kw in df["keyword"].astype(str):
        for w in re.findall(r"[a-z0-9]+", kw.lower()):
            if w not in STOP and w not in GENERIC and w not in GARMENTS and len(w) > 2:
                c[w] += 1
    return c.most_common(1)[0][0] if c else ""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--anchor", default=None)
    ap.add_argument("--minsv", type=float, default=150)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--onanchor", action="store_true",
                    help="only suggest seeds that still contain the anchor (stay in the same niche)")
    a = ap.parse_args()

    xlsx = os.path.join(a.folder, "master-keywords.xlsx")
    if not os.path.exists(xlsx):
        sys.exit("No master-keywords.xlsx in " + a.folder + " — run phaseA_master.py first.")
    df = pd.read_excel(xlsx, sheet_name="All Keywords")

    # keep only relevant, brand-safe, real-demand keywords
    if "relevant" in df: df = df[df["relevant"] == True]
    if "tm_status" in df: df = df[df["tm_status"].astype(str).str.upper() != "HIGH"]
    df = df.copy()
    df["sv"]  = df["search_volume"].map(num)
    df["td"]  = df["title_density"].map(num)
    df["opp"] = df["opportunity"].map(num) if "opportunity" in df else df["sv"]
    df = df[df["sv"] >= a.minsv]
    if df.empty:
        sys.exit("No keywords above SV floor — lower --minsv.")

    anchor = (a.anchor or infer_anchor(df)).lower()

    # cluster by each 'modifier' token (not the anchor, not generic, not a garment)
    clusters = {}
    for _, r in df.iterrows():
        kw = str(r["keyword"]).lower()
        toks = [w for w in re.findall(r"[a-z0-9]+", kw)
                if w not in STOP and w not in GENERIC and w not in GARMENTS and w != anchor and len(w) > 2]
        for w in set(toks):
            c = clusters.setdefault(w, {"kws": [], "sv": 0.0, "opp": [], "td": [], "buckets": []})
            c["kws"].append((r["keyword"], r["sv"], r["td"], r["opp"]))
            c["sv"] += r["sv"]; c["opp"].append(r["opp"]); c["td"].append(r["td"])
            if "bucket" in r and pd.notna(r["bucket"]): c["buckets"].append(str(r["bucket"]))

    def med(x):
        x = sorted(x); return x[len(x)//2] if x else 0

    rows = []
    for mod, c in clusters.items():
        if len(c["kws"]) < 2:          # need a real cluster, not a one-off
            continue
        best = max(c["kws"], key=lambda k: k[3])   # highest-opportunity real phrase = the seed
        seed_phrase = str(best[0])
        # skip clusters that ARE basically the current seed / anchor-only
        core = [w for w in re.findall(r"[a-z0-9]+", seed_phrase.lower()) if w not in STOP]
        if set(core) <= ({anchor} | GENERIC | GARMENTS):
            continue
        avg_td = med(c["td"])
        # seed score: demand, rankability (low TD), and cluster breadth
        score = (c["sv"] / (avg_td + 1)) * (1 + 0.15 * len(c["kws"]))
        angle = "other"
        if c["buckets"]:
            from collections import Counter
            angle = Counter(c["buckets"]).most_common(1)[0][0]
        keeps = anchor in re.findall(r"[a-z0-9]+", seed_phrase.lower())
        rows.append(dict(mod=mod, seed=seed_phrase, n=len(c["kws"]), sv=int(c["sv"]),
                         td=round(avg_td,1), score=int(score), angle=angle, keeps=keeps,
                         examples=", ".join(k[0] for k in sorted(c["kws"], key=lambda x:-x[3])[:3])))

    if a.onanchor:
        rows = [r for r in rows if r["keeps"]]
    rows = sorted(rows, key=lambda r: -r["score"])[:a.top]
    if not rows:
        sys.exit("No fresh sub-angles found above thresholds.")

    out = os.path.join(a.folder, "NEW-SEEDS.md")
    L = [f"# NEW SEED candidates — next-round angles",
         f"Anchor detected: **{anchor}**  ·  from `master-keywords.xlsx`  ·  SV floor {int(a.minsv)}",
         "",
         "Each row is a **fresh sub-angle** already showing demand in your cleaned data — "
         "use it as the seed for the next round (Step 1: Xray that search + Cerebro new ASINs).",
         "Ranked by demand ÷ rankability × breadth. Brand terms already excluded.",
         "",
         f"| # | Use as next seed | Angle | Same niche? | Cluster | Total SV | Avg TD | Score | Example keywords |",
         "|---|---|---|:--:|--:|--:|--:|--:|---|"]
    for i, r in enumerate(rows, 1):
        same = f"✅ {anchor}" if r["keeps"] else "↗ adjacent"
        L.append(f"| {i} | **{r['seed']}** | {r['angle']} | {same} | {r['n']} kws | {r['sv']:,} | {r['td']} | "
                 f"{r['score']:,} | {r['examples']} |")
    L += ["",
          "## How to read this",
          "- **Angle** = why it's a distinct lane (audience / occasion / new product / personalization).",
          "- **Avg TD** low (0–5) = easy to rank; high = crowded titles.",
          "- Pick 1–3 seeds with the best Score + low TD → those are your next Cerebro rounds.",
          "- Stop expanding when new rounds stop producing fresh seeds (the niche is mapped).",
          "",
          "> This is a suggestion engine, not a decision. Verify each seed's head terms in H10 "
          "(some clusters are big because of one fluke keyword). Your approval picks the lane."]
    open(out, "w", encoding="utf-8").write("\n".join(L))
    print("Wrote", out)
    print(f"\n=== TOP NEW SEEDS (anchor: {anchor}) ===")
    for i, r in enumerate(rows[:8], 1):
        print(f" {i}. {r['seed']:<42} {r['angle']:<13} SV {r['sv']:>6,}  TD {r['td']:<4}  score {r['score']:,}")

if __name__ == "__main__":
    main()
