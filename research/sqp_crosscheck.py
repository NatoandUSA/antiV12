#!/usr/bin/env python3
"""
H1 — Cross-validate Helium 10 ESTIMATES against Amazon-native TRUTH (Search Query
Performance). Where H10 and Amazon disagree, Amazon wins.

Usage:  python sqp_crosscheck.py <sqp_export.csv|xlsx> <master-keywords.xlsx>

Flags:
  A) H10 INFLATED   — H10 search volume >> Amazon query volume (don't over-target)
  B) H10 MISSED     — Amazon shows real volume H10 under-ranked (add it)
  C) PROVEN CONVERTER — SQP shows purchases but the term isn't in your Top Picks (harvest)
  D) MIRAGE         — a term you plan to use shows ~0 real Amazon volume (drop)
Read-only. SQP is exported by the owner from Seller Central (Brand Analytics).
"""
import sys, os, re
import pandas as pd, numpy as np

def load(p):
    if p.lower().endswith((".xlsx","xlsm")):
        return pd.read_excel(p)
    for sep in [",","\t"]:
        try:
            d=pd.read_csv(p,encoding="latin-1",sep=sep,engine="python",on_bad_lines="skip")
            if d.shape[1]>=3: break
        except Exception: d=None
    d.columns=[str(c).replace("﻿","").replace("ï»¿","").strip().strip('"') for c in d.columns]
    return d

def numcol(s): return pd.to_numeric(s.astype(str).str.replace(r"[,%$]","",regex=True),errors="coerce")
def find(cols,*keys):
    for c in cols:
        cl=c.lower()
        if all(k in cl for k in keys): return c
    return None

sqp_path, master_path = sys.argv[1], sys.argv[2]
sqp = load(sqp_path)
qcol = find(sqp.columns,"search","query") or find(sqp.columns,"query") or sqp.columns[0]
vcol = find(sqp.columns,"query","volume") or find(sqp.columns,"search","volume") or find(sqp.columns,"volume")
pcol = find(sqp.columns,"purchase","count") or find(sqp.columns,"purchases") or find(sqp.columns,"purchase")
ccol = find(sqp.columns,"click","count") or find(sqp.columns,"clicks")
if vcol is None:
    print("Could not find a Search Query Volume column in the SQP export. Columns:", list(sqp.columns)); sys.exit(1)

s = pd.DataFrame({"kw": sqp[qcol].astype(str).str.lower().str.strip(),
                  "sqp_vol": numcol(sqp[vcol])})
s["sqp_purch"] = numcol(sqp[pcol]) if pcol else np.nan
s = s.dropna(subset=["kw"]).groupby("kw").agg(sqp_vol=("sqp_vol","max"),
        sqp_purch=("sqp_purch","max")).reset_index()

m = pd.read_excel(master_path, sheet_name="All Keywords")
m["keyword"]=m["keyword"].astype(str).str.lower().str.strip()
j = m.merge(s, left_on="keyword", right_on="kw", how="outer")

A,B,C,D = [],[],[],[]
for _,r in j.iterrows():
    h10 = r.get("search_volume"); sv = r.get("sqp_vol"); pu = r.get("sqp_purch")
    kw = r.get("keyword") if isinstance(r.get("keyword"),str) else r.get("kw")
    opp = r.get("opportunity",0)
    if pd.notna(h10) and pd.notna(sv) and sv>0 and h10>0:
        ratio = h10/sv
        if ratio >= 2:   A.append((kw,int(h10),int(sv),round(ratio,1)))
        elif ratio <= 0.5: B.append((kw,int(h10),int(sv),round(ratio,1)))
    if pd.notna(pu) and pu and pu>0 and (pd.isna(opp) or opp==0):
        C.append((kw,int(sv) if pd.notna(sv) else "?",int(pu)))
    # MIRAGE only if Amazon SHOWS the query with ~0 volume — not merely absent from the export
    if pd.notna(h10) and h10>0 and pd.notna(sv) and sv==0 and pd.notna(opp) and opp>0:
        D.append((kw,int(h10)))

def show(title, rows, cols):
    print(f"\n## {title} ({len(rows)})")
    if not rows: print("  none"); return
    print("  " + " | ".join(cols))
    for r in sorted(rows, key=lambda x:-(x[1] if isinstance(x[1],int) else 0))[:15]:
        print("  " + " | ".join(str(x) for x in r))

print("="*60); print("  SQP CROSS-CHECK — Amazon truth vs Helium 10 estimate"); print("="*60)
print(f"SQP query column: '{qcol}' · volume: '{vcol}' · purchases: '{pcol}'")
show("A) H10 INFLATED — trust Amazon, don't over-target", A, ["keyword","H10_sv","SQP_vol","x"])
show("B) H10 MISSED — real Amazon demand H10 under-ranked → add", B, ["keyword","H10_sv","SQP_vol","x"])
show("C) PROVEN CONVERTER not in Top Picks → harvest", C, ["keyword","SQP_vol","SQP_purchases"])
show("D) MIRAGE — you plan to use it but ~0 real Amazon volume → drop", D, ["keyword","H10_sv"])
print("\nRule: SQP (Amazon-native) is TRUTH; H10 is an estimate. On conflict, Amazon wins.\n")
