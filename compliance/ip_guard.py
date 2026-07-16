#!/usr/bin/env python3
"""
IP / LEGAL SAFETY GUARD  ·  Stage 4b screen (and importable everywhere).

Screens any listing text / keyword / design caption against the maintained
IP library (ip_library.json): brands, characters, celebrities, sports, schools,
protected marks, and reported trademark-enforced phrases. Also carries the
copyright / trademark / patent checklists for the human self-audit.

Risk levels:
  BLOCK   -> named brand/franchise/person/team/school/event/registered phrase.
             Never use in title, bullets, backend, PPC, or the design.
  REVIEW  -> an uncommon token not in safe vocab (possible brand/name/place) OR
             a reported-enforced phrase. A human verifies at tmsearch.uspto.gov.
  OK      -> all tokens recognized safe vocabulary.

This is RISK-REDUCTION, not legal clearance. Verify real cases at
tmsearch.uspto.gov (trademark) and copyright.gov (copyright).

Usage:
  python ip_guard.py "Custom Nurse Sweatshirt Personalized Embroidered Crewneck"
  python ip_guard.py --file LISTING-copy-paste-NURSE.md
  python ip_guard.py --checklists
  from ip_guard import screen           # -> dict with verdict + hits
"""
import sys, os, re, json, argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB  = json.load(open(os.path.join(_HERE, "ip_library.json"), encoding="utf-8"))

# reuse the base safe vocabulary from tm_guard if present, then extend it
try:
    from tm_guard import SAFE as _BASE_SAFE
except Exception:
    _BASE_SAFE = set()
SAFE = (set(_BASE_SAFE)
        | {w.lower() for w in _LIB.get("safe_vocab_add", [])}
        | {w.lower() for w in _LIB.get("common_english", [])})

# brand/franchise terms that ALSO collide with everyday words -> downgrade to REVIEW
AMBIG = {k.lower(): v for k, v in _LIB.get("ambiguous_downgrade", {}).items()
         if not k.startswith("_")}

# flatten all BLOCK terms into one lookup {term: category}
BLOCK = {}
for cat, terms in _LIB["block"].items():
    if cat.startswith("_"):
        continue
    for t in terms:
        BLOCK[t.lower()] = cat
REVIEW_PHRASES = {p["term"].lower(): p["note"] for p in _LIB.get("review_phrases", [])}

_WORD = re.compile(r"[a-z0-9&'\-]+")

def _norm(s):
    return re.sub(r"\s+", " ", str(s).lower()).strip()

def screen(text):
    """Return {'verdict','hits':[{term,category,risk,why}], 'unknown':[...]}"""
    t = _norm(text)
    hits, seen = [], set()

    # 1) multiword + single BLOCK terms (substring match on word-ish boundaries)
    for term, cat in BLOCK.items():
        if term in seen:
            continue
        pat = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
        if re.search(pat, t):
            if term in AMBIG:   # collides with an everyday word -> human decides
                hits.append({"term": term, "category": "ambiguous_brand", "risk": "REVIEW",
                             "why": AMBIG[term]})
            else:
                hits.append({"term": term, "category": cat, "risk": "BLOCK",
                             "why": f"named {cat.replace('_',' ')} — never use"})
            seen.add(term)

    # 2) reported trademark-enforced phrases
    for phrase, note in REVIEW_PHRASES.items():
        pat = r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])"
        if re.search(pat, t):
            hits.append({"term": phrase, "category": "reported_enforced_phrase",
                         "risk": "REVIEW", "why": note})

    # 3) unknown single tokens (possible brand / surname / place)
    unknown = []
    for w in _WORD.findall(t):
        if w in SAFE or w in BLOCK or w.isdigit() or len(w) <= 2:
            continue
        if any(w in ph.split() for ph in REVIEW_PHRASES):
            continue
        unknown.append(w)
    # dedup, keep order
    unknown = list(dict.fromkeys(unknown))
    # v2.4.0 pilot fix: an unrecognized common word is NOT a trademark hit. Listing every unknown
    # token as REVIEW made EVERY real listing REVIEW (crying wolf), so owners ignored it. Unknown
    # tokens are now INFORMATIONAL — surfaced for a manual eyeball but they do NOT drive the verdict.
    # The curated 450+ BLOCK library (brands/characters) and the named REVIEW phrases still govern.
    for w in unknown:
        hits.append({"term": w, "category": "unrecognized_token", "risk": "INFO",
                     "why": "uncommon token — optional manual scan for a hidden brand/name/place"})

    if any(h["risk"] == "BLOCK" for h in hits):
        verdict = "BLOCK"
    elif any(h["risk"] == "REVIEW" for h in hits):
        verdict = "REVIEW"
    else:
        verdict = "OK"
    return {"verdict": verdict, "hits": hits, "unknown": unknown}

# --- convenience for the pipeline: is a single keyword safe to USE? -------------
def check(kw):
    """Back-compat with tm_guard.check -> (status, reason). BLOCK->HIGH, REVIEW->CAUTION."""
    r = screen(kw)
    if r["verdict"] == "BLOCK":
        b = next(h for h in r["hits"] if h["risk"] == "BLOCK")
        return ("HIGH", f"{b['term']} ({b['category']})")
    if r["verdict"] == "REVIEW":
        toks = [h["term"] for h in r["hits"] if h["risk"] == "REVIEW"]
        return ("CAUTION", "verify: " + ", ".join(toks[:6]))
    return ("OK", "all tokens safe")

def print_checklists():
    def blk(title, items):
        print(f"\n== {title} ==")
        for i, x in enumerate(items, 1):
            print(f"  {i}. {x}")
    blk("TRADEMARK checklist", _LIB["trademark_checklist"])
    blk("COPYRIGHT checklist", _LIB["copyright_checklist"])
    blk("PATENT (light) checklist", _LIB["patent_checklist_light"])
    print("\nNOT legal advice. Verify at tmsearch.uspto.gov and copyright.gov.")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="*")
    ap.add_argument("--file")
    ap.add_argument("--checklists", action="store_true")
    a = ap.parse_args()
    if a.checklists:
        print_checklists(); return
    text = open(a.file, encoding="utf-8").read() if a.file else " ".join(a.text)
    if not text.strip():
        print("Give text or --file. Use --checklists for the self-audit lists."); return
    r = screen(text)
    icon = {"BLOCK":"⛔","REVIEW":"⚠️","OK":"✅"}[r["verdict"]]
    print(f"\n{icon} VERDICT: {r['verdict']}   (lib v{_LIB['meta']['version']}, {_LIB['meta']['updated']})")
    blocks = [h for h in r["hits"] if h["risk"]=="BLOCK"]
    revs   = [h for h in r["hits"] if h["risk"]=="REVIEW"]
    if blocks:
        print("\n⛔ BLOCK — remove before publishing:")
        for h in blocks: print(f"   • {h['term']}  [{h['category']}] — {h['why']}")
    if revs:
        named = [h for h in revs if h["category"] in ("reported_enforced_phrase","ambiguous_brand")]
        toks  = [h for h in revs if h["category"] == "unrecognized_token"]
        print("\n⚠️  REVIEW — a human verifies at tmsearch.uspto.gov:")
        for h in named: print(f"   • {h['term']} — {h['why']}")
        if toks:
            show = ", ".join(h["term"] for h in toks[:12])
            more = f"  (+{len(toks)-12} more)" if len(toks) > 12 else ""
            print(f"   • uncommon tokens (verify not a brand/name/place): {show}{more}")
    if r["verdict"] == "OK":
        print("   All tokens are recognized safe vocabulary.")
    print("\n(Risk-reduction only, not legal clearance.)")
    # exit code IS the gate: 0=OK, 2=REVIEW, 3=BLOCK — callers must stop on non-zero
    return {"OK": 0, "REVIEW": 2, "BLOCK": 3}[r["verdict"]]

if __name__ == "__main__":
    sys.exit(main() or 0)
