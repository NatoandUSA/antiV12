#!/usr/bin/env python3
"""
STAGE 6 GATE  ·  ECONOMICS / MARGIN  —  is this worth building at all?

A keyword can be high-demand + brand-safe and STILL lose money after Amazon
fees, COGS, returns and ads. This gate computes real contribution margin per
order and returns GO / TEST / SKIP before any listing is built.

  contribution (before ads) = price - referral - COGS - returns reserve
  contribution (after ads)  = contribution before ads - PPC per order
  break-even ACOS           = contribution before ads / price
  test PPC budget           = target clicks x expected CPC   (if CPC given)

VERDICT (min margin default $8, override with --min):
  GO    contribution after ads >= min margin
  TEST  contribution after ads > 0 but < min margin
  SKIP  contribution after ads <= 0

Input: a CSV (one row per product/variant) with columns —
  product, price, blank_cost, embroidery_cost, packaging, ship_out,
  amazon_fee_pct, refund_reserve_pct, ppc_per_order[, expected_cpc, target_clicks]
amazon_fee_pct / refund_reserve_pct accept 17 or 0.17.  Not financial advice.

Usage:
  python economics_gate.py economics-input.csv [--min 8] [--out .]
"""
import sys, os, argparse
import pandas as pd

def num(x):
    try: return float(str(x).replace("$","").replace("%","").replace(",","").strip() or 0)
    except: return 0.0

def pct(x):
    v = num(x); return v/100.0 if v > 1 else v      # 17 -> 0.17, 0.17 -> 0.17

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--min", type=float, default=8.0, help="minimum $ contribution per order to GO")
    ap.add_argument("--out", default=".")
    a = ap.parse_args()
    df = pd.read_csv(a.csv)
    df.columns = [c.strip().lower() for c in df.columns]

    # required cost coverage — a MISSING cost is INCOMPLETE, never silently 0.
    REQUIRED = ["price","blank_cost","embroidery_cost","packaging","ship_out",
                "amazon_fee_pct","refund_reserve_pct","ppc_per_order"]
    rows = []
    for _, r in df.iterrows():
        provided = lambda k: (k in r and pd.notna(r[k]) and str(r[k]).strip() != "")
        g = lambda k: r[k] if provided(k) else 0
        missing = [k for k in REQUIRED if not provided(k)]
        price   = num(g("price"))
        cogs    = num(g("blank_cost")) + num(g("embroidery_cost")) + num(g("packaging")) + num(g("ship_out"))
        referral= price * pct(g("amazon_fee_pct") or 0.17)
        reserve = price * pct(g("refund_reserve_pct") or 0.04)
        ppc     = num(g("ppc_per_order"))
        before  = price - referral - cogs - reserve
        after   = before - ppc
        be_acos = (before / price) if price else 0
        cpc, clicks = num(g("expected_cpc")), num(g("target_clicks"))
        test_budget = cpc * clicks if cpc and clicks else None
        if missing:
            v, why = "INCOMPLETE", f"missing required cost(s): {', '.join(missing)} — cannot certify profit"
        elif price <= 0:
            v, why = "SKIP", "no price given"
        elif after >= a.min:
            v, why = "GO", f"${after:.2f}/order after ads (>= ${a.min:.0f} target)"
        elif after > 0:
            v, why = "TEST", f"only ${after:.2f}/order after ads — thin, test small"
        else:
            v, why = "SKIP", f"loses ${abs(after):.2f}/order after ads"
        rows.append(dict(p=str(g("product") or "?"), price=price, cogs=cogs, referral=referral,
                         reserve=reserve, ppc=ppc, before=before, after=after, be_acos=be_acos,
                         test_budget=test_budget, v=v, why=why))

    order = {"GO":0,"TEST":1,"INCOMPLETE":2,"SKIP":3}
    rows.sort(key=lambda x: (order[x["v"]], -x["after"]))
    ico = {"GO":"🟢 GO","TEST":"🟡 TEST","INCOMPLETE":"⚪ INCOMPLETE","SKIP":"🔴 SKIP"}

    os.makedirs(a.out, exist_ok=True)
    p = os.path.join(a.out, "ECONOMICS-GATE.md")
    L = ["# Economics / Margin Gate", f"Minimum contribution to GO: **${a.min:.0f}/order** after ads. "
         "Not financial advice — enter your real numbers.", "",
         "| Verdict | Product | Price | COGS | Referral | Reserve | PPC | Margin before ads | **After ads** | Break-even ACOS |",
         "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|"]
    for r in rows:
        L.append(f"| {ico[r['v']]} | {r['p']} | ${r['price']:.2f} | ${r['cogs']:.2f} | ${r['referral']:.2f} | "
                 f"${r['reserve']:.2f} | ${r['ppc']:.2f} | ${r['before']:.2f} | **${r['after']:.2f}** | {r['be_acos']*100:.0f}% |")
    L += ["", "## Read"]
    for r in rows:
        extra = f" · suggested test budget ${r['test_budget']:.0f}" if r["test_budget"] else ""
        L.append(f"- {ico[r['v']]} **{r['p']}** — {r['why']}. Keep ads under **{r['be_acos']*100:.0f}% ACOS** to stay profitable{extra}.")
    gos = [r["p"] for r in rows if r["v"]=="GO"]
    L += ["", ("**Build only these (GO):** " + ", ".join(gos)) if gos else
          "**No GO** — raise price, cut COGS, or lower expected PPC before building.",
          "", "> Break-even ACOS = the max % of sales you can spend on ads and still not lose money on that order."]
    open(p,"w",encoding="utf-8").write("\n".join(L))
    print("Wrote", p)
    print("\n=== ECONOMICS VERDICTS ===")
    for r in rows:
        print(f" {ico[r['v']]:9} {r['p']:<28} after-ads ${r['after']:.2f}  break-even ACOS {r['be_acos']*100:.0f}%")
    # P0.8: variant-aware project verdict. A row can be excluded=1 to launch without it.
    def excluded(rr):
        # look up the original df row by product name
        m = df[df.get("product","").astype(str)==rr["p"]] if "product" in df.columns else None
        if m is not None and len(m) and "exclude" in df.columns:
            try: return str(m.iloc[0]["exclude"]).strip() not in ("","0","nan","false","False")
            except Exception: return False
        return False
    eligible   = [r["p"] for r in rows if r["v"]=="GO" and not excluded(r)]
    incomplete = [r["p"] for r in rows if r["v"]=="INCOMPLETE" and not excluded(r)]
    blocked    = [r["p"] for r in rows if r["v"]=="SKIP" and not excluded(r)]
    if eligible and not incomplete and not blocked:
        project, code = "GO", 0
    elif incomplete:
        project, code = "INCOMPLETE", 2      # a non-excluded variant is unpriced → not project GO
    elif eligible:
        project, code = "TEST", 3            # some sellable, some blocked variants
    else:
        project, code = "SKIP", 5
    print(f"\nPROJECT: {project}  · eligible={eligible} · incomplete={incomplete} · blocked={blocked}")
    print("  (mixed GO+INCOMPLETE cannot return project GO — every listing child must map to a priced, eligible row)")
    import json as _json
    _json.dump({"project_status":project,"eligible_variants":eligible,
                "incomplete_variants":incomplete,"blocked_variants":blocked},
               open(os.path.join(a.out,"ECONOMICS.json"),"w"), indent=2)
    sys.exit(code)

if __name__ == "__main__":
    main()
