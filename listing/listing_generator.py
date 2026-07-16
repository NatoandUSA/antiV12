#!/usr/bin/env python3
"""
listing.listing_generator (v2) — turn research data into a review Brief + a schema-valid listing.json.

Reads what the research tools already produced in the project folder:
  the authoritative keyword source via keyword_source_adapter (MASTER-KEYWORDS.json >
                              MASTER-KEYWORDS-LEAN.json > KEYWORD-INTELLIGENCE.json opt-in only)
  COMPETITOR-GAP.json         (evidence-classed differentiation gaps)
  creative-brief.json         (product facts: garment, colors, personalization, price, supplier) [optional]

and writes:
  LISTING-BRIEF.json / .md    (HUMAN review — the reasoning, keyword placement, photo plan)
  listing.json                (MACHINE output — title/bullets/backend/A+/item_highlights)

Rules baked in (v2.4.0):
- Title is capped at 75 characters (Amazon non-media rule, 2026-07-27) — NOT ~78.
- No promotional words, no ALL-CAPS, no special symbols, no repeated words in the title.
- Item Highlights field (<=125 chars) built from secondary keywords.
- A+ modules chosen dynamically from a_plus_templates by competitor-gap priority.
- Claim-safe: no invented certifications, guarantees, or absolute durability claims.
- Embroidery-proof and size modules are flagged requires_proof (need a real photo / manual check).
This is a DRAFT generated from heuristics — a human reviews the Brief and the gate engine still governs.

Usage:  python listing_generator.py <run_folder> [--seed "..."]
"""
from __future__ import annotations
import os, sys, re, json, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import a_plus_templates as APT
import keyword_source_adapter as KSA

TITLE_MAX = 75
ITEM_HIGHLIGHTS_MAX = 125
BACKEND_MAX_BYTES = 249
PROMO = {"best", "cheap", "sale", "free", "guarantee", "guaranteed", "bestseller", "#1", "top",
         "premium", "perfect", "amazing", "luxury", "hot", "deal", "discount"}


def _load(folder, name):
    """Optional JSON: absent -> None, but malformed -> hard error (ACT-003).

    Swallowing a parse error turned a corrupt file into "no data", so a broken input silently became
    invented defaults. A file that exists must parse.
    """
    p = os.path.join(folder, name)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise KSA.MalformedKeywordSourceError(p, e.lineno, e.colno, e.msg) from e


def _titlecase(s):
    small = {"for", "and", "with", "the", "a", "an", "of", "to", "in"}
    words = s.split()
    out = []
    for i, w in enumerate(words):
        out.append(w if (w.islower() and w in small and i != 0) else w[:1].upper() + w[1:])
    return " ".join(out)


def build_title(primary, product):
    """Front-load the primary keyword, add product type + one differentiator, capped at 75 chars,
    no promo words, no repeats, no symbols."""
    garment = product.get("garment_type", "")
    audience = product.get("audience", "")
    base_words = []
    seen = set()
    for chunk in (primary, garment, "Embroidered", "Personalized", audience):
        for w in re.sub(r"[^A-Za-z0-9 ]", " ", str(chunk)).split():
            lw = w.lower()
            if lw in PROMO or lw in seen:
                continue
            seen.add(lw)
            base_words.append(w)
    title = ""
    for w in base_words:
        cand = (title + " " + w).strip()
        if len(cand) > TITLE_MAX:
            break
        title = cand
    return _titlecase(title)


def build_item_highlights(keywords, product):
    """Comma-separated secondary phrases within 125 chars (materials, use, audience)."""
    parts = []
    for k in keywords[1:6]:
        if len(", ".join(parts + [k])) <= ITEM_HIGHLIGHTS_MAX:
            parts.append(k)
    fabric = product.get("fabric")
    if fabric and len(", ".join(parts + [fabric])) <= ITEM_HIGHLIGHTS_MAX:
        parts.append(fabric)
    return ", ".join(parts)[:ITEM_HIGHLIGHTS_MAX]


def build_bullets(keywords, gaps, product):
    g = product.get("garment_type", "garment")
    pers = product.get("personalization", "a name")
    fabric = product.get("fabric", "soft")
    audience = product.get("audience") or "someone special"
    who = f"for {audience} " if audience else ""
    bullets = [
        f"PERSONALIZED FOR THEM: Add {pers} — embroidered exactly as you enter it at checkout.",
        f"REAL MACHINE EMBROIDERY: Raised satin stitching on the {g}, not a flat printed graphic.",
        f"MADE TO ORDER: Each piece is embroidered after you order and shipped from the US with tracking.",
        f"COMFORTABLE {fabric.upper()} FIT: Built for everyday wear and easy to layer.",
        f"THOUGHTFUL GIFT: A personalized {g} {who}to actually wear and keep.",
    ]
    return [b[:490] for b in bullets]


def build_backend(keywords, product, title=""):
    """<=249 bytes, lowercase, space-separated, and NOT repeating words already in the title
    (Amazon ignores title words in backend — repeating them wastes the 249 bytes)."""
    title_words = set(re.sub(r"[^a-z0-9 ]", " ", title.lower()).split())
    words, seen, out = [], set(title_words), []
    for k in keywords:
        for w in re.sub(r"[^a-z0-9 ]", " ", k.lower()).split():
            if w in seen or len(w) < 2:
                continue
            seen.add(w)
            cand = " ".join(out + [w])
            if len(cand.encode("utf-8")) > BACKEND_MAX_BYTES:
                return " ".join(out)
            out.append(w)
    return " ".join(out)


def generate(folder, seed="", allow_legacy_unsafe=False):
    """Build the listing draft from the authoritative keyword source.

    Legacy KEYWORD-INTELLIGENCE.json is UNVERIFIED_SOURCE and is never read by default: a legacy-only
    project fails with an actionable error rather than quietly producing a listing from unverified data.
    Pass allow_legacy_unsafe=True to authorize reading it — that authorizes the SOURCE only, and its
    REVIEW/untiered/rejected records still will not allocate.
    """
    try:
        src = KSA.load_keyword_source(folder, allow_legacy_unsafe=allow_legacy_unsafe)
    except KSA.NoKeywordSourceError as e:
        return {"ok": False, "reason": str(e)}
    kw_records = src.eligible_keywords()
    kws = [r["keyword_exact"] for r in kw_records] or ([seed] if seed else [])
    if not kws:
        return {"ok": False, "reason": f"no eligible keywords in {src.source_file} "
                                       f"({src.source_schema}); tiers={src.tier_counts}"}
    KSA.write_normalized_source(folder, src)

    gapd = _load(folder, "COMPETITOR-GAP.json") or {}
    brief_in = _load(folder, "creative-brief.json") or {}
    product = dict(brief_in.get("product", {}))
    product.setdefault("garment_type", "crewneck sweatshirt")
    product.setdefault("fabric", "cotton-blend")
    product.setdefault("personalization", "any name and credentials")
    product.setdefault("occasion", "any occasion")
    product.setdefault("production_time", "10-14 business days")
    product.setdefault("design", "embroidered name")

    primary = kws[0]
    gaps = gapd.get("gaps", [])
    # derive an audience noun from the keywords if the product doesn't specify one (avoids "them")
    if not product.get("audience"):
        AUD = ["nurse", "teacher", "mom", "mama", "dad", "nurse practitioner", "rn", "grandma",
               "coach", "bride", "student", "vet", "doctor", "firefighter", "police"]
        blob = " ".join(kws[:5]).lower()
        found = next((a for a in AUD if a in blob), "")
        product["audience"] = (found + "s") if found and not found.endswith("s") else found

    title = build_title(primary, product)
    highlights = build_item_highlights(kws, product)
    bullets = build_bullets(kws, gaps, product)
    backend = build_backend(kws, product, title=title)

    facts = {"garment": product["garment_type"], "design": product["design"], "fabric": product["fabric"],
             "personalization": product["personalization"], "audience": product["audience"],
             "occasion": product["occasion"], "production_time": product["production_time"]}
    modules = [APT.render(t, facts) for t in APT.select_modules(gaps, max_modules=7)]
    proof_needed = [m["headline"] for m in modules if m["requires_proof"]]

    image_plan = [
        "Main: real product, pure white bg, >=85% frame, no text/props, >=2000px",
        "Macro stitch close-up (REAL photo — embroidery proof)",
        "Personalized name legible",
        "How to personalize (checkout fields)",
        "Size chart from real measurements",
        "Color options grid (real colors only)",
        "Lifestyle / worn shot of the exact garment",
        "Gift / occasion scene (packaging only if real)",
        "Fabric / care detail",
        "Trust / made-to-order graphic",
    ]

    listing = {
        "schema_version": "2.4",
        "category": brief_in.get("category", "apparel"),
        "generated_by": "listing_generator_v2",
        "title": title,
        "item_highlights": highlights,
        "bullets": bullets,
        "description": (f"A personalized {product['garment_type']} with real machine embroidery of "
                        f"{product['personalization']}. Made to order and shipped from the US."),
        "backend": backend,
        "aplus": [{"headline": m["headline"], "copy": m["copy"]} for m in modules],
        "image_plan": image_plan,
        "price": product.get("price"),
        "selected_keywords": {"primary": kws[:1], "secondary": kws[1:5], "backend": kws},
        **src.listing_metadata(),
    }

    brief = {
        "schema_version": "2.4",
        "primary_keyword": primary,
        **src.listing_metadata(),
        "keyword_tier_counts": src.tier_counts,
        "keyword_eligibility_counts": src.eligibility_counts,
        "title": title, "title_len": len(title), "title_limit": TITLE_MAX,
        "item_highlights": highlights, "item_highlights_len": len(highlights),
        "keywords_used": kws[:8],
        "gaps_addressed": [{"area": g["area"], "score": g.get("score"),
                            "source_type": g.get("source_type"),
                            "manual_confirmation_required": g.get("manual_confirmation_required")} for g in gaps[:6]],
        "aplus_modules": [{"headline": m["headline"], "requires_proof": m["requires_proof"]} for m in modules],
        "photo_proof_required": proof_needed,
        "image_plan": image_plan,
        "note": ("DRAFT generated from research heuristics. A human reviews this Brief; the gate engine "
                 "still governs publication. Modules marked requires_proof need a REAL photo / manual check "
                 "before publishing (embroidery proof, size chart)."),
    }

    # write outputs (listing.json validated by the caller / dashboard safe-write; here we write brief + a candidate)
    with open(os.path.join(folder, "LISTING-BRIEF.json"), "w", encoding="utf-8") as f:
        json.dump(brief, f, indent=2, ensure_ascii=False)
    md = [f"# Listing Brief — {primary}", "", brief["note"], "",
          f"**Keyword source:** `{src.source_file}` · {src.source_schema} · run `{src.source_run_id}` · "
          f"sha256 `{src.source_sha256[:16]}…`" + ("  ⚠️ **LEGACY_UNSAFE**" if src.legacy_unsafe else ""), "",
          f"**Title ({len(title)}/{TITLE_MAX}):** {title}", "",
          f"**Item Highlights ({len(highlights)}/{ITEM_HIGHLIGHTS_MAX}):** {highlights}", "",
          "**Bullets:**"] + [f"- {b}" for b in bullets] + ["", "**A+ modules:**"] + \
         [f"- {m['headline']}" + ("  ⚠️ needs real photo" if m["requires_proof"] else "") for m in modules] + \
         ["", "**Photo proof required before publish:** " + (", ".join(proof_needed) or "none"), "",
          "**10-photo plan:**"] + [f"{i+1}. {p}" for i, p in enumerate(image_plan)]
    with open(os.path.join(folder, "LISTING-BRIEF.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    return {"ok": True, "listing": listing, "brief": brief, "keyword_source": src,
            "title_len": len(title), "proof_required": proof_needed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder"); ap.add_argument("--seed", default="")
    ap.add_argument("--write-listing", action="store_true",
                    help="also write listing.json directly (dashboard uses safe-write instead)")
    a = ap.parse_args()
    if not os.path.isdir(a.folder):
        print(f"no such folder: {a.folder}"); sys.exit(2)
    try:
        r = generate(a.folder, a.seed)
    except KSA.KeywordSourceError as e:
        print(f"FAILED: {e}"); sys.exit(2)
    if not r.get("ok"):
        print("skip:", r.get("reason")); sys.exit(2)
    if a.write_listing:
        with open(os.path.join(a.folder, "listing.json"), "w", encoding="utf-8") as f:
            json.dump(r["listing"], f, indent=2, ensure_ascii=False)
    src = r["keyword_source"]
    print(f"keyword source: {src.source_file} [{src.source_schema}/{src.source_mode}] "
          f"sha256 {src.source_sha256[:16]}…" + ("  LEGACY_UNSAFE" if src.legacy_unsafe else ""))
    print(f"OK — title {r['title_len']}/{TITLE_MAX} chars · {len(r['listing']['aplus'])} A+ modules · "
          f"proof required: {', '.join(r['proof_required']) or 'none'}")
    print("Wrote LISTING-BRIEF.json + .md" + (" + listing.json" if a.write_listing else ""))
    sys.exit(0)


if __name__ == "__main__":
    main()
