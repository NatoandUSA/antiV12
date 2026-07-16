#!/usr/bin/env python3
"""
Stage 8 (mechanical) — validate a drafted Amazon listing before the reviewer skill.
P1: screens ALL content, checks claims, checks variation consistency, and uses
per-category rules from category_config.json.

Usage:  python listing_validate.py listing.json [master-keywords.xlsx]

listing.json shape (extra fields optional):
{
  "category": "apparel|jewelry|pod",        # optional -> config default
  "title": "...",
  "bullets": ["...", x5],
  "description": "...",
  "backend": "space separated terms",
  "ppc_exact": ["..."], "ppc_phrase": ["..."],
  "aplus": ["module text", ...],            # optional A+ copy
  "image_text": ["text baked into an image", ...],   # optional
  "personalization": "instructions to buyer",         # optional
  "variations": ["crewneck","hoodie","mock neck"]     # optional variation theme
}

Exit: 0 = pass (maybe warns) · 1 = at least one FAIL · 2 = bad input/file.
"""
import sys, json, re, os
from collections import Counter
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from ip_guard import check as tm_check
except Exception:
    from tm_guard import check as tm_check

GARMENTS = ["crewneck","hoodie","sweatshirt","mock neck","mockneck","pullover","sweater",
            "t-shirt","tshirt","tee","tank","long sleeve","longsleeve","jacket","cardigan"]

def toks(s): return re.findall(r"[a-z]+", s.lower())
def bl(s): return len(s.encode("utf-8"))
def sentences(s): return re.split(r"(?<=[.!?])\s+|\n+", s)

def load_cfg():
    try:
        cfg = json.load(open(os.path.join(HERE,"category_config.json"), encoding="utf-8"))
        return cfg
    except Exception:
        return {"default":"apparel","categories":{"apparel":{}}, "claim_rules":{}}

def sev_add(sev, fails, warns, msg):
    if sev == "block": fails.append(msg)
    elif sev == "warn": warns.append(msg)

# P0.9: canonical-fact contradictions — a claim that contradicts the real product is BLOCKED.
def fact_contradictions(text, facts):
    t = " " + re.sub(r"[^a-z0-9 ]", " ", str(text).lower()) + " "
    out = []
    method = str(facts.get("decoration_method", "")).lower()
    if "embroider" in method and "machine" in method or "machine embroider" in method or ("embroider" in method and "hand" not in method):
        for bad in ("hand stitched", "hand-stitched", "hand embroidered", "hand-embroidered", "handmade embroidery", "handmade"):
            if f" {bad} " in t: out.append(f"'{bad}' contradicts machine embroidery")
    mat = str(facts.get("material", "")).lower()
    if "blend" in mat or "poly" in mat:
        if "100% cotton" in t or "100 percent cotton" in t: out.append("'100% cotton' contradicts a blend material")
    if not facts.get("gift_packaging_verified"):
        if "gift ready" in t or "gift-ready" in t: out.append("'gift ready' but gift packaging not verified")
    if facts.get("supplier") and not facts.get("own_factory"):
        if "own factory" in t or "our factory" in t or "our own workshop" in t:
            out.append("'own factory/workshop' but product is supplier-made")
    prod_days = str(facts.get("production_time","")).lower()
    if any(x in prod_days for x in ("3","4","5","day")) and ("same day" in t or "ships today" in t):
        out.append("'same day' contradicts multi-day production")
    return out

def claim_check(text, rules):
    """Return (fails, warns) for claim problems in one text block."""
    f, w = [], []
    low = text.lower()
    for c in rules.get("hard_fail", []):
        if c in low: f.append(f"unsupported claim '{c}'")
    markers = rules.get("comparative_markers", [])
    for sent in sentences(text):
        sl = sent.lower()
        for d in rules.get("durability_absolute", []):
            if d in sl:
                if any(m in sl for m in markers):
                    pass   # comparative ("won't peel like printed vinyl") = allowed
                else:
                    f.append(f"absolute durability claim '{d}' (make it comparative, e.g. '…like printed vinyl')")
    for s in rules.get("superlative_warn", []):
        if re.search(r"(?<![a-z])"+re.escape(s)+r"(?![a-z])", low):
            w.append(f"superlative '{s}' (style/compliance — soften or substantiate)")
    return f, w

def main():
    # ---- robust input handling ----
    if len(sys.argv) < 2:
        print("Usage: python listing_validate.py listing.json [master-keywords.xlsx]"); sys.exit(2)
    path = sys.argv[1]
    try:
        listing = json.load(open(path, encoding="utf-8"))
    except FileNotFoundError:
        print(f"⛔ file not found: {path}"); sys.exit(2)
    except json.JSONDecodeError as e:
        print(f"⛔ invalid JSON in {path}: {e}"); sys.exit(2)
    if not isinstance(listing, dict):
        print("⛔ listing.json must be a JSON object"); sys.exit(2)
    master = sys.argv[2] if len(sys.argv) > 2 else None

    cfg = load_cfg()
    cat = str(listing.get("category") or cfg.get("default","apparel")).lower()
    R = cfg.get("categories",{}).get(cat) or cfg.get("categories",{}).get(cfg.get("default"),{}) or {}
    CR = cfg.get("claim_rules",{})

    fails, warns = [], []
    title = listing.get("title","") or ""
    bullets = listing.get("bullets",[]) or []
    backend = listing.get("backend","") or ""
    desc = listing.get("description","") or ""
    # A+ modules may be strings OR {headline, copy} dicts — normalize to text for screening.
    def _mod_text(a):
        if isinstance(a, dict):
            return " ".join(str(a.get(k,"")) for k in ("headline","copy","text") if a.get(k))
        return str(a)
    aplus = [_mod_text(a) for a in (listing.get("aplus") or listing.get("aplus_modules") or listing.get("a_plus") or [])]
    image_text = listing.get("image_text",[]) or []
    personalization = listing.get("personalization","") or ""
    ppc = (listing.get("ppc_exact",[]) or []) + (listing.get("ppc_phrase",[]) or [])

    # ---- TITLE ----
    # P0-02: Amazon's non-media title rule (effective 2026-07-27) caps titles at 75 chars incl. spaces.
    tmax = R.get("title_max",75)
    if len(title) > tmax:
        fails.append(f"Title {len(title)} chars > {tmax} (Amazon non-media limit effective {R.get('title_rule_effective','2026-07-27')})")
    ttmax = R.get("title_target_max")
    if ttmax and tmax >= len(title) > ttmax:
        warns.append(f"Title {len(title)} chars — over the {ttmax}-char sweet spot; front-load the key phrase in the first ~60.")
    # ---- ITEM HIGHLIGHTS (new field, 125 chars) ----
    ih = listing.get("item_highlights") or listing.get("product_highlights") or []
    if isinstance(ih, str): ih_text = ih
    else: ih_text = ", ".join(str(x) for x in ih)
    ihmax = R.get("item_highlights_max",125)
    if ih_text and len(ih_text) > ihmax:
        fails.append(f"Item Highlights {len(ih_text)} chars > {ihmax}")
    repmax = R.get("title_word_repeat_max",2)
    rep = {w:n for w,n in Counter(toks(title)).items() if n > repmax}
    if rep: fails.append(f"Title repeats a word >{repmax}x: {rep}")
    low = title.lower()
    if "&" in title: sev_add(R.get("ampersand_in_title","warn"), fails, warns, "Title contains '&' (use 'and')")
    if "gift" in low: sev_add(R.get("gift_in_title","warn"), fails, warns, "Title contains 'gift'")
    OCC = ["mother","mothers","father","fathers","christmas","valentine","halloween",
           "birthday","holiday","xmas","easter","thanksgiving","nurses week","graduation"]
    occ = [w for w in OCC if w in low]
    if occ: sev_add(R.get("occasion_in_title","warn"), fails, warns, f"Title occasion word(s): {occ}")

    # ---- VARIATION CONSISTENCY ----
    if R.get("garment_variation_check"):
        in_title = [g for g in GARMENTS if g in low]
        declared = [str(v).lower() for v in (listing.get("variations") or [])]
        if len(set(in_title)) >= 2 and not declared:
            warns.append(f"Title lists multiple garment types {sorted(set(in_title))} but no \"variations\" declared "
                         "— confirm these are real variations of ONE listing, or tighten the title to the main garment.")

    # ---- BULLETS ----
    exp = R.get("bullets_expected",5)
    if len(bullets) != exp: warns.append(f"{len(bullets)} bullets ({exp} recommended)")
    for i,b in enumerate(bullets,1):
        if len(b) > R.get("bullet_hard_max",500): fails.append(f"Bullet {i} {len(b)} chars > {R.get('bullet_hard_max',500)}")
        elif len(b) > R.get("bullet_soft_max",250): warns.append(f"Bullet {i} {len(b)} chars > {R.get('bullet_soft_max',250)} (readability)")

    # ---- BACKEND ----
    nb = bl(backend)
    if nb > R.get("backend_max_bytes",249): fails.append(f"Backend {nb} bytes > {R.get('backend_max_bytes',249)}")
    elif nb < R.get("backend_min_bytes",230): warns.append(f"Backend only {nb} bytes (aim {R.get('backend_min_bytes',230)}–{R.get('backend_max_bytes',249)})")
    if "," in backend: fails.append("Backend contains commas (use spaces)")
    bw = toks(backend)
    dup = [w for w,n in Counter(bw).items() if n > 1]
    if dup: warns.append(f"Backend repeats word(s) internally: {dup}")
    front = set(toks(title)) | {w for b in bullets for w in toks(b)}
    overlap = sorted(set(bw) & front)
    if overlap: warns.append(f"Backend repeats title/bullet word(s) (wasted): {overlap[:12]}")

    # ---- IP SCREEN on ALL content ----
    ip_fields = [("title", title), ("description", desc), ("personalization", personalization)] \
                + [(f"bullet {i}", b) for i,b in enumerate(bullets,1)] \
                + [(f"A+ {i}", a) for i,a in enumerate(aplus,1)] \
                + [(f"image-text {i}", t) for i,t in enumerate(image_text,1)] \
                + [(f"PPC '{k}'", k) for k in ppc]
    # HIGH (named brand/character) fails on ANY field. CAUTION (unknown token) is
    # noisy on prose, so only surface it for short high-visibility fields (title / PPC / image text).
    for label, text in ip_fields:
        if not str(text).strip(): continue
        st, why = tm_check(text)
        if st == "HIGH":
            fails.append(f"Trademark/brand in {label}: {why}")
        elif st == "CAUTION" and (label == "title" or label.startswith("PPC") or label.startswith("image-text")):
            warns.append(f"Verify term in {label}: {why}")

    # ---- CLAIM CHECK on customer-facing copy ----
    for label, text in [("title", title), ("description", desc)] \
                       + [(f"bullet {i}", b) for i,b in enumerate(bullets,1)] \
                       + [(f"A+ {i}", a) for i,a in enumerate(aplus,1)] \
                       + [(f"image-text {i}", t) for i,t in enumerate(image_text,1)]:
        if not str(text).strip(): continue
        cf, cw = claim_check(text, CR)
        for m in cf: fails.append(f"{label}: {m}")
        for m in cw: warns.append(f"{label}: {m}")
        # P0.9: contradictions vs canonical product facts (machine embroidery vs 'hand stitched', etc.)
        facts = listing.get("product") or listing.get("product_facts") or {}
        for m in fact_contradictions(text, facts):
            fails.append(f"{label}: {m}")

    # ---- KEYWORD VERIFY vs master sheet ----
    if master and os.path.exists(master):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(master, read_only=True)
            ws = wb["All Keywords"]
            hdr = [c.value for c in next(ws.rows)]
            ki, si = hdr.index("keyword"), hdr.index("search_volume")
            have = {}
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row[ki] is not None: have[str(row[ki]).lower().strip()] = row[si] or 0
            for kw in ppc:
                sv = have.get(kw.lower().strip())
                if sv is None: fails.append(f"PPC keyword not in master sheet (unverified): '{kw}'")
                elif sv <= 0: fails.append(f"PPC keyword has 0 search volume: '{kw}'")
        except Exception as e:
            warns.append(f"Could not verify keywords against master sheet: {e}")
    else:
        warns.append("No master sheet given — keyword search volume NOT verified")

    # ---- REPORT ----
    print("="*58)
    print(f"  LISTING VALIDATION  [{cat}]  —  title {len(title)}c · backend {nb}B")
    print("="*58)
    if fails:
        print(f"\n❌ FAIL ({len(fails)}):")
        for f in fails: print("  -", f)
    if warns:
        print(f"\n🟡 WARN ({len(warns)}):")
        for w in warns: print("  -", w)
    if not fails and not warns: print("\n✅ ALL CHECKS PASSED")
    elif not fails: print("\n✅ PASS (warnings only) — ready for amazon-fbm-listing-reviewer")
    else: print("\n⛔ BLOCKED — fix FAILs, then re-run")
    print()
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
