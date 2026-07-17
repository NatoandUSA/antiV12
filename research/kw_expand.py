#!/usr/bin/env python3
"""
kw_expand.py — free long-tail keyword expander (Google Autocomplete).
Learned from amz-trend-hunter. Pulls the phrases real shoppers type, trademark-
screens them (tm_guard), and outputs a clean list to validate in Helium 10 /
feed into backend search terms. Uses a public suggestion endpoint; availability and terms
may change — verify current permitted use. Optional and outside the critical alpha path.
of Amazon, no account.

Usage:
  python kw_expand.py "custom dog sweatshirt"
  python kw_expand.py "custom dog sweatshirt" --az        # deeper (append a–z)
  python kw_expand.py seeds.txt --out expanded.csv        # file of seeds

Needs network (runs on your machine/VPS). Output columns: keyword, seed, tm_status.
"""
import sys, os, json, time, csv, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from tm_guard import check as tm_check
except Exception:
    def tm_check(k): return ("OK", "")

def suggest(q):
    import requests
    url = "https://suggestqueries.google.com/complete/search"
    # Session 5B (ACT-016): this optional research helper is the only repo-owned
    # outbound call outside the dashboard. Route it through the shared guard so it
    # respects OFFLINE_ONLY — offline it raises before opening any connection.
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))
        import network_policy as NP
        NP.assert_outbound_allowed("google-suggest", url)
    except ImportError:
        pass
    r = requests.get(url, params={"client": "firefox", "gl": "us", "q": q},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
    r.raise_for_status()
    return r.json()[1]

def expand(seed, az=False):
    out = set()
    queries = [seed] + ([f"{seed} {c}" for c in "abcdefghijklmnopqrstuvwxyz"] if az else [])
    for q in queries:
        try:
            for s in suggest(q):
                s = s.strip().lower()
                if s and s != seed.lower():
                    out.add(s)
            time.sleep(0.2)   # be gentle on the endpoint
        except Exception as e:
            print(f"  ! fetch failed for '{q}': {str(e)[:80]}", file=sys.stderr)
    return sorted(out)

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    az = "--az" in sys.argv
    out_path = None
    if "--out" in sys.argv:
        out_path = sys.argv[sys.argv.index("--out")+1]
    if not args:
        print(__doc__); sys.exit(2)

    src = args[0]
    seeds = ([l.strip() for l in open(src) if l.strip()]
             if os.path.isfile(src) else [src])

    rows = []
    for seed in seeds:
        print(f"\n# {seed}")
        for kw in expand(seed, az):
            st, _ = tm_check(kw)
            if st == "HIGH":                      # never surface known brands
                continue
            tag = " ⚠️CAUTION" if st == "CAUTION" else ""
            rows.append({"keyword": kw, "seed": seed, "tm_status": st})
            print(f"  {kw}{tag}")

    if out_path and rows:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["keyword", "seed", "tm_status"])
            w.writeheader(); w.writerows(rows)
        print(f"\nSaved {len(rows)} terms → {out_path}")
    print(f"\n{len(rows)} clean suggestions. Next: verify their real volume in Helium 10 "
          f"(or run sqp_crosscheck once you have SQP), then use the winners in backend/title.")

if __name__ == "__main__":
    main()
