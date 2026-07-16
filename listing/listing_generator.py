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
- Title/backend limits come from the versioned category_policy_registry (ACT-004), NOT a hardcoded
  universal 75-character rule. The generator resolves the limit for the listing's category.
- No promotional words, no ALL-CAPS, no special symbols, no repeated words in the title.
- Item Highlights field (<=125 chars) built from secondary keywords.
- A+ modules chosen dynamically from a_plus_templates by competitor-gap priority.
- Product facts come from product_fact_loader (ACT-006): unknown facts stay null, are omitted from copy,
  and raise OWNER_FACT_REQUIRED — the generator never invents a material, personalization value,
  occasion, production time, or shipping claim to keep a field populated.
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
import category_policy_registry as CPR
import product_fact_loader as PFL

ITEM_HIGHLIGHTS_MAX = 125
DEFAULT_CATEGORY = "apparel"
PROMO = {"best", "cheap", "sale", "free", "guarantee", "guaranteed", "bestseller", "#1", "top",
         "premium", "perfect", "amazing", "luxury", "hot", "deal", "discount"}

# garment nouns we can safely read off the (real, approved) primary keyword — never fabricated.
GARMENT_NOUNS = ("crewneck sweatshirt", "sweatshirt", "hoodie", "crewneck", "pullover", "sweater",
                 "long sleeve shirt", "long sleeve", "t-shirt", "tshirt", "tee", "tank top", "tank",
                 "jacket", "cardigan", "shirt")

# A+ template placeholder -> product-fact field, so a blocked module names the fact it still needs.
APLUS_PLACEHOLDER_TO_FACT = {"garment": "garment_type", "design": "decoration_method",
                             "fabric": "material", "personalization": "personalization_fields",
                             "audience": "audience", "occasion": "occasion",
                             "production_time": "production_time_range"}


def garment_from_keyword(primary):
    """Read a garment noun off the approved primary keyword (real data). None if none is present."""
    blob = f" {str(primary).lower()} "
    for noun in GARMENT_NOUNS:
        if f" {noun} " in blob or blob.strip().endswith(noun):
            return noun
    return None


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


def build_title(primary, product, title_limit):
    """Front-load the primary keyword, add product type + one differentiator, capped at the category
    policy's title hard limit, no promo words, no repeats, no symbols."""
    garment = product.get("garment_type") or ""
    audience = product.get("audience") or ""
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
        if len(cand) > title_limit:
            break
        title = cand
    return _titlecase(title)


def build_item_highlights(keywords, product):
    """Comma-separated secondary phrases within 125 chars (secondary keywords + verified material)."""
    parts = []
    for k in keywords[1:6]:
        if len(", ".join(parts + [k])) <= ITEM_HIGHLIGHTS_MAX:
            parts.append(k)
    material = product.get("material")           # verified fact or None — never a fabricated default
    if material and len(", ".join(parts + [material])) <= ITEM_HIGHLIGHTS_MAX:
        parts.append(material)
    return ", ".join(parts)[:ITEM_HIGHLIGHTS_MAX]


def build_bullets(keywords, gaps, product):
    """Five benefit bullets. Facts that are unknown are omitted, never fabricated: no material default,
    no personalization value, no shipping-origin claim unless the fact is verified (ACT-006)."""
    g = product.get("garment_type") or "garment"
    audience = product.get("audience")
    who = f"for {audience} " if audience else ""
    pers = product.get("personalization")        # verified personalization value or None
    material = product.get("material")           # verified material or None
    shipping_verified = bool(product.get("shipping_origin_verified"))

    b1 = (f"PERSONALIZED FOR THEM: Add {pers} — embroidered as you enter it at checkout." if pers
          else "PERSONALIZED FOR THEM: Add your personalization at checkout — embroidered as you enter it.")
    b2 = f"REAL MACHINE EMBROIDERY: Raised satin stitching on the {g}, not a flat printed graphic."
    b3 = ("MADE TO ORDER: Each piece is embroidered after you order and shipped from the US with tracking."
          if shipping_verified else "MADE TO ORDER: Each piece is embroidered after you order.")
    b4 = (f"COMFORTABLE {material.upper()} FIT: Built for everyday wear and easy to layer." if material
          else "COMFORTABLE FIT: Built for everyday wear and easy to layer.")
    b5 = f"THOUGHTFUL GIFT: A personalized {g} {who}to actually wear and keep."
    return [b[:490] for b in (b1, b2, b3, b4, b5)]


def build_backend(keywords, product, title="", byte_ceiling=249):
    """<= the category policy's backend byte ceiling, lowercase, space-separated, and NOT repeating
    words already in the title (Amazon ignores title words in backend — repeating them wastes bytes)."""
    title_words = set(re.sub(r"[^a-z0-9 ]", " ", title.lower()).split())
    words, seen, out = [], set(title_words), []
    for k in keywords:
        for w in re.sub(r"[^a-z0-9 ]", " ", k.lower()).split():
            if w in seen or len(w) < 2:
                continue
            seen.add(w)
            cand = " ".join(out + [w])
            if len(cand.encode("utf-8")) > byte_ceiling:
                return " ".join(out)
            out.append(w)
    return " ".join(out)


def _verified_product(facts_src, primary):
    """Assemble ONLY publishable (VERIFIED / OWNER_CONFIRMED) product facts into the copy dict.

    Unknown facts stay absent — the builders omit them, they never become a fabricated default.
    Returns (product_dict, owner_fact_required) where owner_fact_required names the copy facts that are
    not verified so the reviewer knows exactly what to supply.
    """
    owner_fact_required = []

    def pf(field):
        v = facts_src.publishable_value(field)
        if v is None and field not in owner_fact_required:
            owner_fact_required.append(field)
        return v

    def joined(field):
        v = pf(field)
        return ", ".join(v) if isinstance(v, list) else v

    # garment: verified fact first, then the (real, approved) primary keyword, else generic "garment".
    garment = facts_src.publishable_value("garment_type") or garment_from_keyword(primary)
    if not garment:
        if "garment_type" not in owner_fact_required:
            owner_fact_required.append("garment_type")

    product = {
        "garment_type": garment,
        "material": pf("material"),
        "personalization": joined("personalization_fields"),
        "occasion": pf("occasion"),
        "production_time": pf("production_time_range"),
        "design": pf("decoration_method"),
        "shipping_origin_verified": _is_us_shipping_verified(facts_src),
    }
    return product, owner_fact_required


def _is_us_shipping_verified(facts_src):
    """True only if a verified fact states US production/shipping — otherwise no US-origin claim is made."""
    for field in ("shipping_method", "production_location"):
        v = facts_src.publishable_value(field)
        if isinstance(v, str) and ("us" in v.lower() or "united states" in v.lower()
                                   or "u.s" in v.lower() or "domestic" in v.lower()):
            return True
    return False


def _audience_from_keywords(kws):
    AUD = ["nurse", "teacher", "mom", "mama", "dad", "nurse practitioner", "rn", "grandma",
           "coach", "bride", "student", "vet", "doctor", "firefighter", "police"]
    blob = " ".join(kws[:5]).lower()
    found = next((a for a in AUD if a in blob), "")
    return (found + "s") if found and not found.endswith("s") else found


def generate(folder, seed="", allow_legacy_unsafe=False, policy_registry=None):
    """Build the listing draft from the authoritative keyword source and verified product facts.

    Legacy KEYWORD-INTELLIGENCE.json is UNVERIFIED_SOURCE and is never read by default: a legacy-only
    project fails with an actionable error rather than quietly producing a listing from unverified data.
    Pass allow_legacy_unsafe=True to authorize reading it — that authorizes the SOURCE only, and its
    REVIEW/untiered/rejected records still will not allocate.

    Title/backend limits are resolved through category_policy_registry; product facts through
    product_fact_loader. Unknown facts are omitted from copy and reported as OWNER_FACT_REQUIRED — the
    generator never invents a material, personalization value, occasion, production time, or US-shipping
    claim to keep a field populated (ACT-006).
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
    category = brief_in.get("category", DEFAULT_CATEGORY)
    policy = CPR.resolve_category_policy(category, registry=policy_registry)
    title_limit = policy.title_hard_limit

    facts_src = PFL.load_product_facts(folder=folder)      # absent file -> all facts UNKNOWN (no fabrication)
    primary = kws[0]
    gaps = gapd.get("gaps", [])
    product, owner_fact_required = _verified_product(facts_src, primary)
    product["audience"] = _audience_from_keywords(kws)
    product["price"] = (brief_in.get("product") or {}).get("price")

    title = build_title(primary, product, title_limit)
    highlights = build_item_highlights(kws, product)
    bullets = build_bullets(kws, gaps, product)
    backend = build_backend(kws, product, title=title, byte_ceiling=policy.backend_byte_ceiling)

    # A+ facts: only verified values, so an unresolved {placeholder} marks a module non-publishable
    # instead of printing a fabricated value or a literal "{fabric}" (ACT-006 / ACT-014-safe).
    aplus_facts = {k: v for k, v in {"garment": product["garment_type"], "design": product["design"],
                                     "fabric": product["material"], "personalization": product["personalization"],
                                     "audience": product["audience"], "occasion": product["occasion"],
                                     "production_time": product["production_time"]}.items() if v}
    modules = [APT.render(t, aplus_facts) for t in APT.select_modules(gaps, max_modules=7)]
    proof_needed = [m["headline"] for m in modules if m["requires_proof"]]
    publishable_modules, blocked_modules = [], []
    for m in modules:
        missing = re.findall(r"\{(\w+)\}", m["copy"])
        if missing:
            m = dict(m, publishable=False,
                     missing_facts=sorted({APLUS_PLACEHOLDER_TO_FACT.get(p, p) for p in missing}))
            for fld in m["missing_facts"]:
                if fld not in owner_fact_required:
                    owner_fact_required.append(fld)
            blocked_modules.append(m)
        else:
            publishable_modules.append(dict(m, publishable=True))

    description = _build_description(product)

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
        "category": category,
        "generated_by": "listing_generator_v2",
        "title": title,
        "item_highlights": highlights,
        "bullets": bullets,
        "description": description,
        "backend": backend,
        "aplus": [{"headline": m["headline"], "copy": m["copy"]} for m in publishable_modules],
        "image_plan": image_plan,
        "price": product.get("price"),
        "selected_keywords": {"primary": kws[:1], "secondary": kws[1:5], "backend": kws},
        "category_policy": {"category_identifier": policy.category_identifier,
                            "title_hard_limit": policy.title_hard_limit,
                            "title_recommended_target": policy.title_recommended_target,
                            "backend_byte_ceiling": policy.backend_byte_ceiling,
                            "fallback_used": policy.fallback_used,
                            "warning_state": policy.warning_state,
                            "policy_source": policy.policy_source},
        "product_fact_source": facts_src.source_file,
        "product_fact_source_sha256": facts_src.source_sha256,
        "owner_fact_required": sorted(owner_fact_required),
        **src.listing_metadata(),
    }

    brief = {
        "schema_version": "2.4",
        "primary_keyword": primary,
        **src.listing_metadata(),
        "keyword_tier_counts": src.tier_counts,
        "keyword_eligibility_counts": src.eligibility_counts,
        "category_policy": listing["category_policy"],
        "title": title, "title_len": len(title), "title_limit": title_limit,
        "item_highlights": highlights, "item_highlights_len": len(highlights),
        "keywords_used": kws[:8],
        "product_fact_source": facts_src.source_file,
        "product_fact_source_sha256": facts_src.source_sha256,
        "owner_fact_required": sorted(owner_fact_required),
        "aplus_blocked_for_missing_facts": [{"headline": m["headline"], "missing_facts": m["missing_facts"]}
                                            for m in blocked_modules],
        "gaps_addressed": [{"area": g["area"], "score": g.get("score"),
                            "source_type": g.get("source_type"),
                            "manual_confirmation_required": g.get("manual_confirmation_required")} for g in gaps[:6]],
        "aplus_modules": [{"headline": m["headline"], "requires_proof": m["requires_proof"]} for m in modules],
        "photo_proof_required": proof_needed,
        "image_plan": image_plan,
        "note": ("DRAFT generated from research heuristics. A human reviews this Brief; the gate engine "
                 "still governs publication. Unknown product facts are omitted from copy and listed under "
                 "OWNER_FACT_REQUIRED — supply and verify them before publishing. Modules marked "
                 "requires_proof need a REAL photo / manual check (embroidery proof, size chart)."),
    }

    # write outputs (listing.json validated by the caller / dashboard safe-write; here we write brief + a candidate)
    with open(os.path.join(folder, "LISTING-BRIEF.json"), "w", encoding="utf-8") as f:
        json.dump(brief, f, indent=2, ensure_ascii=False)
    fallback_note = "  ⚠️ POLICY_VERIFICATION_REQUIRED (fallback)" if policy.fallback_used else ""
    md = [f"# Listing Brief — {primary}", "", brief["note"], "",
          f"**Keyword source:** `{src.source_file}` · {src.source_schema} · run `{src.source_run_id}` · "
          f"sha256 `{src.source_sha256[:16]}…`" + ("  ⚠️ **LEGACY_UNSAFE**" if src.legacy_unsafe else ""), "",
          f"**Category policy:** `{policy.category_identifier}` · title hard limit {title_limit}"
          f"{fallback_note}", "",
          f"**Product facts:** `{facts_src.source_file}` · verified {facts_src.verified_fact_count} · "
          f"owner-review {facts_src.owner_review_count} · unknown {facts_src.unknown_fact_count}", "",
          f"**OWNER_FACT_REQUIRED:** " + (", ".join(sorted(owner_fact_required)) or "none"), "",
          f"**Title ({len(title)}/{title_limit}):** {title}", "",
          f"**Item Highlights ({len(highlights)}/{ITEM_HIGHLIGHTS_MAX}):** {highlights}", "",
          "**Bullets:**"] + [f"- {b}" for b in bullets] + ["", "**A+ modules (publishable):**"] + \
         [f"- {m['headline']}" + ("  ⚠️ needs real photo" if m["requires_proof"] else "") for m in publishable_modules] + \
         ["", "**A+ modules blocked (missing verified facts):**"] + \
         ([f"- {m['headline']} — needs {', '.join(m['missing_facts'])}" for m in blocked_modules] or ["- none"]) + \
         ["", "**Photo proof required before publish:** " + (", ".join(proof_needed) or "none"), "",
          "**10-photo plan:**"] + [f"{i+1}. {p}" for i, p in enumerate(image_plan)]
    with open(os.path.join(folder, "LISTING-BRIEF.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    return {"ok": True, "listing": listing, "brief": brief, "keyword_source": src,
            "product_facts": facts_src, "category_policy": policy,
            "title_len": len(title), "title_limit": title_limit,
            "owner_fact_required": sorted(owner_fact_required), "proof_required": proof_needed}


def _build_description(product):
    """Description from verified facts only. No production-origin or material claim unless verified."""
    g = product.get("garment_type") or "garment"
    parts = [f"A personalized {g}."]
    pers = product.get("personalization")
    material = product.get("material")
    design = product.get("design")
    if pers:
        parts.append(f"Personalize it with {pers}.")
    if design and "embroider" in str(design).lower():
        parts.append("Decorated with real machine embroidery.")
    if material:
        parts.append(f"Made from {material}.")
    if product.get("shipping_origin_verified"):
        parts.append("Made to order and shipped from the US with tracking.")
    return " ".join(parts)


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
    print(f"OK — title {r['title_len']}/{r['title_limit']} chars · {len(r['listing']['aplus'])} A+ modules · "
          f"proof required: {', '.join(r['proof_required']) or 'none'}")
    if r["owner_fact_required"]:
        print(f"OWNER_FACT_REQUIRED: {', '.join(r['owner_fact_required'])}")
    print("Wrote LISTING-BRIEF.json + .md" + (" + listing.json" if a.write_listing else ""))
    sys.exit(0)


if __name__ == "__main__":
    main()
