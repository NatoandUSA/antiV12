#!/usr/bin/env python3
"""
creative.creative_edge — Conversion & Creative Edge module (v2.2).

v2.2 hardening (from the Amazon Creative Edge review):
 - Amazon MAIN IMAGE is a separate, conservative, compliant slot (no text/props/insets).
 - Macro/gift/steps concepts live in SECONDARY images (3/8/4), never Image 1.
 - White background = AMAZON_BASELINE, never a "gap to differentiate away from".
 - Competitor evidence carries a confidence level + effective-unique-sample + limitations.
 - Two scores: Creative PLAN readiness (strategy) and ACTUAL ASSET edge (INCOMPLETE w/o real image).
 - Embroidery proof adds DRAFT_UNVERIFIED (internal AI concept != MISLEADING).
 - Every image headline is claim-checked; misleading "handmade / made to last" removed.
No computer vision. No Seller Central. Human-review friendly.

Usage:
  python creative_edge.py <folder> [--brief creative-brief.json] [--example]
"""
import sys, os, json, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
import edge_lib as L
import main_image_validator as MIV
try:
    import asset_validator as AV
except Exception:
    AV = None

DIMS = [
    ("white_bg",             "White background (main)",             False, True,  True),
    ("flat_lay",             "Flat-lay / folded garment",           False, True,  False),
    ("on_model",             "Worn on a model",                     False, False, False),
    ("name_readable",        "Personalized name clearly readable",  True,  False, False),
    ("embroidery_macro",     "Real embroidery macro / proof (sec.)",True,  False, False),
    ("lifestyle",            "Lifestyle / in-use scene",            False, False, False),
    ("size_chart",           "Size / fit chart",                    True,  False, False),
    ("color_grid",           "Color-options grid",                  False, False, False),
    ("gift_context",         "Gift / occasion context (sec.)",      True,  False, False),
    ("personalization_steps","How-to-personalize steps",            True,  False, False),
    ("trust_production",     "Trust / production / care",           False, False, False),
    ("brand_or_character_art","Brand/character/licensed art",       False, False, False),
]

CLAIM_HARD = {
    "handmade":"machine-embroidered product — 'handmade' is misleading",
    "hand stitched":"machine embroidery, not hand-stitched","hand-stitched":"machine embroidery, not hand-stitched",
    "made to last":"durability claim needs evidence — use 'Real Machine Embroidery'",
    "never fade":"absolute durability claim","never fades":"absolute durability claim",
    "will not fade":"absolute durability claim","will not crack":"absolute durability claim",
    "guaranteed":"unsupported guarantee","lifetime":"unsupported durability claim",
    "made in usa":"origin claim needs evidence","official":"authorization claim",
    "licensed":"authorization claim","premium quality":"vague quality claim",
}
def claim_check_headline(text, packaging_verified=False):
    t = str(text or "").lower()
    problems = [why for term, why in CLAIM_HARD.items() if term in t]
    if ("gift-ready" in t or "gift ready" in t) and not packaging_verified:
        problems.append("'gift-ready' needs verified gift packaging")
    return problems

def load_brief(folder, brief_path=None):
    p = brief_path or os.path.join(folder, "creative-brief.json")
    if not os.path.exists(p):
        sys.exit(f"No creative brief at {p}. Run with --example to create one, then fill it.")
    with open(p, encoding="utf-8") as f:
        brief = json.load(f)
    lj = os.path.join(folder, "listing.json")
    if os.path.exists(lj):
        try:
            with open(lj, encoding="utf-8") as f: listing = json.load(f)
            brief.setdefault("listing_statements", {}).setdefault("title", listing.get("title",""))
        except Exception: pass
    return brief

# ---- 1. competitor matrix ----
def classify(meta, p, top_p, any_present):
    key, label, high_value, sameness, baseline = meta
    if key == "brand_or_character_art":
        return (L.UNSAFE, "competitors use brand/character art — legally unsafe to copy") if any_present \
               else (L.EXPECTED, "no one risks brand/character art — keep it that way")
    if baseline:
        return L.AMAZON_BASELINE, "Amazon main-image baseline — keep it, do NOT differentiate away from white bg"
    if sameness and p >= 0.7:
        return L.OVERUSED, f"{round(p*100)}% use this style — sameness; differentiate elsewhere (not by breaking the baseline)"
    if p >= 0.7:
        return L.EXPECTED, f"{round(p*100)}% do this — table stakes, meet it"
    if top_p >= 0.5 and p < 0.7:
        return L.EFFECTIVE, "top sellers do it, not everyone — worth adopting"
    if high_value and p <= 0.3:
        return (L.MISSING if p == 0 else L.OPPORTUNITY), "high-value and rare — a gap you can own (in a SECONDARY image)"
    if p == 0:
        return L.MISSING, "nobody does it"
    return L.WEAK, f"{round(p*100)}% do it, inconsistently"

def competitor_confidence(comps):
    n = len(comps)
    brands = {c.get("brand","?") for c in comps}
    parents = {c.get("parent_group") or c.get("asin") for c in comps}
    per_brand, eff = {}, 0
    for c in comps:
        b = c.get("brand","?"); per_brand[b] = per_brand.get(b,0)+1
        if per_brand[b] <= 2: eff += 1
    eff = min(eff, len(parents))
    limits = []
    if eff < 8: limits.append(f"fewer than 8 effective unique listings ({eff})")
    if len(brands) < n: limits.append("some listings share a brand (deduplicated to 2 max/brand)")
    if any(v>2 for v in per_brand.values()): limits.append("a brand contributes >2 listings")
    conf = "HIGH" if eff >= 10 and len(brands) >= 6 else "MEDIUM" if eff >= 8 else "LOW"
    return {"raw_sample":n,"effective_unique_sample":eff,"unique_brands":len(brands),
            "unique_parents":len(parents),"confidence":conf,"limitations":limits}

def build_visual_matrix(brief):
    comps = brief.get("competitors", [])
    n = len(comps); tops = [c for c in comps if c.get("top_seller")]
    conf = competitor_confidence(comps); rows = []
    for meta in DIMS:
        key = meta[0]
        present = sum(1 for c in comps if c.get("visual",{}).get(key))
        top_present = sum(1 for c in tops if c.get("visual",{}).get(key))
        p = present/n if n else 0; top_p = top_present/len(tops) if tops else 0
        status, note = classify(meta, p, top_p, present>0)
        if conf["confidence"]=="LOW" and status in (L.MISSING,L.OPPORTUNITY,L.EFFECTIVE):
            note += " · treat as a hypothesis (LOW sample confidence)"
        rows.append({"key":key,"label":meta[1],"prevalence":round(p,2),"top_seller_prevalence":round(top_p,2),
                     "classification":status,"note":note})
    everyone = [r["label"] for r in rows if r["prevalence"]>=0.7]
    winners  = [r["label"] for r in rows if r["top_seller_prevalence"]>=0.5 and r["classification"] in (L.EFFECTIVE,L.EXPECTED)]
    gaps     = [r["label"] for r in rows if r["classification"] in (L.OPPORTUNITY,L.MISSING)]
    unclear  = [r["label"] for r in rows if r["key"] in ("name_readable","personalization_steps","size_chart") and r["prevalence"]<0.5]
    unsafe   = [r["label"] for r in rows if r["classification"]==L.UNSAFE]
    data = {"n_competitors":n,"n_top":len(tops),"confidence":conf,"rows":rows,"everyone_does":everyone,
            "winners_do":winners,"customer_still_unclear":unclear,"gaps_to_own_secondary":gaps,"do_not_copy":unsafe}
    tr = "".join(f"<tr><td>{L.esc(r['label'])}</td><td style='text-align:center'>{round(r['prevalence']*100)}%</td>"
                 f"<td style='text-align:center'>{round(r['top_seller_prevalence']*100)}%</td>"
                 f"<td>{L.badge(r['classification'])}</td><td class='muted'>{L.esc(r['note'])}</td></tr>" for r in rows)
    lim = "".join(f"<li>{L.esc(x)}</li>" for x in conf["limitations"]) or "<li>none</li>"
    body = (f"<div class='card'><b>Sample confidence:</b> {L.badge(conf['confidence'])} · "
            f"{conf['effective_unique_sample']} effective unique of {n} ({conf['unique_brands']} brands)."
            f"<ul class='muted'>{lim}</ul></div>"
            f"<div class='card'><table><tr><th>Visual element</th><th>All</th><th>Top</th><th>Verdict</th><th>Read</th></tr>{tr}</table></div>"
            f"<h2>So what</h2><div class='card'>"
            f"<p><b>Every competitor does:</b> {L.esc(', '.join(everyone) or '—')}.</p>"
            f"<p><b>Top sellers do well:</b> {L.esc(', '.join(winners) or '—')}.</p>"
            f"<p><b>Customers may still not understand:</b> {L.esc(', '.join(unclear) or '—')}.</p>"
            f"<div class='why'><b>Gaps to own — in SECONDARY images (not by breaking the white-bg main):</b> {L.esc(', '.join(gaps) or '—')}.</div>"
            f"<p class='muted'><b>Do NOT copy:</b> {L.esc(', '.join(unsafe) or 'nothing flagged')}. White background is the Amazon baseline — keep it.</p></div>")
    html = L.page("Competitor Visual Matrix","Creative Edge v2.2 · Output 1",
                  f"{n} competitors ({conf['confidence']} confidence). What's the same, what's required, and where you can honestly differ.", body)
    return data, html

# ---- 2. compliant main image + concepts ----
MAIN_CONCEPTS = [
    dict(name="Clean Full-Garment Control",
         hypothesis="A flawless, accurate, product-only white-bg shot — the compliant control every test runs against.",
         detail="Exact garment, product-only, white background, accurate color, clean maximum fill without cropping features, real placement.",
         differentiator="execution quality (sharpness, accurate color, clean silhouette)"),
    dict(name="High-Contrast Valid Color Variant",
         hypothesis="An actually-available garment/thread combo with stronger thumbnail contrast helps it pop — without changing design size.",
         detail="Same exact product; pick a REAL available color with higher contrast to the thread; design dimensions unchanged.",
         differentiator="valid color/thread contrast (only if the color is truly available)"),
    dict(name="Improved Preparation & Crop",
         hypothesis="Better wrinkle control, lighting, cleaner silhouette and higher fill make the name readable via photography, not false enlargement.",
         detail="Same product & color; press, even lighting, higher fill, readable name via contrast — never enlarge embroidery beyond real proportions.",
         differentiator="photography quality + honest readability"),
]
def build_main_image_edge(brief, matrix):
    prod = brief.get("product", {}); colors = prod.get("colors", []); threads = prod.get("thread_colors", [])
    main_img = (brief.get("our_images") or {}).get("main")
    concepts = []
    for c in MAIN_CONCEPTS:
        spec = dict(c); spec.update(slot_type="AMAZON_MAIN_IMAGE", headline=None, supporting_copy=None,
            text_allowed=False, background="white",
            recommended_color=(colors[2] if ("Color" in c["name"] and len(colors)>2) else (colors[0] if colors else "accurate color")),
            recommended_thread=(threads[0] if threads else "high-contrast thread"),
            # v2.3.4-RC1: pass accuracy using the field names the validator reads (…_accuracy),
            # sourced from the owner's explicit evidence in the brief product. Default UNKNOWN
            # (so compliance stays INCOMPLETE) until the owner asserts VERIFIED.
            garment_type_accuracy=str(prod.get("garment_type_accuracy","UNKNOWN")).upper(),
            color_accuracy=str(prod.get("color_accuracy","UNKNOWN")).upper(),
            placement_accuracy=str(prod.get("placement_accuracy","UNKNOWN")).upper(),
            category_rules_verified=bool(brief.get("category_rules_verified", False)), asset_path=main_img,
            project_dir=brief.get("__folder"),   # v2.3.4-RC1: resolve the real file inside the project
            category_review=brief.get("category_review", {}))
        spec["compliance"] = MIV.validate(spec); concepts.append(spec)
    secondary_note = ("Macro-stitch proof → Image 3 · Gift context → Image 8 · How-to steps → Image 4 · "
                      "Trust/production → Image 9. These are NOT main-image concepts.")
    data = {"slot":"AMAZON_MAIN_IMAGE","concepts":concepts,"secondary_concepts_moved":secondary_note,
            "gaps_for_secondary_images":matrix["gaps_to_own_secondary"]}
    cc = ""
    for c in concepts:
        comp = c["compliance"]
        cc += (f"<div class='concept'><h3>{L.esc(c['name'])}</h3><p class='muted'>{L.esc(c['hypothesis'])}</p>"
               f"<p><b>Spec:</b> {L.esc(c['detail'])}<br><b>Color/thread:</b> {L.esc(c['recommended_color'])} / {L.esc(c['recommended_thread'])} · "
               f"<b>Differentiator:</b> {L.esc(c['differentiator'])}<br><b>Compliance:</b> {L.badge(comp['status'])} — "
               f"{L.esc(comp['required_actions'][0] if comp['required_actions'] else '')}</p></div>")
    body = (f"<div class='why'>Amazon <b>Image 1 is conservative and compliant</b>: exact product only, white bg, "
            f"<b>no text / props / insets / gift boxes</b>. Differentiate through execution + a valid color, not gimmicks.<br>"
            f"<b>Secondary concepts moved:</b> {L.esc(secondary_note)}</div>"
            f"<h2>{len(concepts)} compliant main-image concepts</h2><div class='grid'>{cc}</div>"
            f"<p class='muted'>Real gaps ({L.esc(', '.join(matrix['gaps_to_own_secondary']) or '—')}) are exploited in SECONDARY images.</p>")
    html = L.page("Main Image Edge Report","Creative Edge v2.2 · Output 2",
                  "Compliant main-image concepts only. Conversion storytelling lives in images 2–9.", body)
    return data, html

# ---- 3. thumbnail review ----
def build_thumbnail_sim(brief):
    prod = brief.get("product", {}); main = (brief.get("our_images") or {}).get("main")
    sizes = [("Full",320),("Desktop search",186),("Mobile search",110),("Tiny",56)]; cells = ""
    for label, px in sizes:
        inner = (f"<img src='{L.esc(main)}' alt='' style='object-fit:contain'>" if main else
                 f"<span style='font-size:{max(8,px//9)}px'>{L.esc(prod.get('garment_type','product'))}<br>{L.esc(prod.get('design','name'))}</span>")
        cells += (f"<div style='display:inline-block;text-align:center;margin:8px'>"
                  f"<div class='thumb' style='width:{px}px;height:{px}px;background:#f4f6f8'>{inner}</div>"
                  f"<div class='muted'>{L.esc(label)} · {px}px</div></div>")
    fields = ["product_recognizable","personalization_readable","design_understandable","excess_white_space",
              "product_cropped","visual_differentiation","premium_perception","accuracy_concern"]
    rev = {"image_id":"MAIN-A","reviewer":"","reviewed_at":"",
           "sizes":{"desktop":{k:None for k in fields[:3]},"mobile":{k:None for k in fields[:3]}},
           "status":"INCOMPLETE","required_action":"complete the review; do not enlarge embroidery beyond real proportions"}
    # v2.3.4 (P1.2): never overwrite a COMPLETED review. Only write the blank template when the
    # file is missing or still blank (no reviewer). A staff-completed review is the source of truth.
    tr_path = os.path.join(brief["__folder"],"THUMBNAIL-REVIEW.json")
    existing = None
    if os.path.exists(tr_path):
        try: existing = json.load(open(tr_path, encoding="utf-8"))
        except Exception: existing = None
    if existing and existing.get("reviewer"):
        rev = existing   # keep the completed review; do not clobber it
    else:
        L.save_json(tr_path, rev)
    ck = "".join(f"<tr><td>{L.esc(f.replace('_',' '))}</td><td style='text-align:center'>desktop ☐&nbsp;&nbsp;mobile ☐</td></tr>" for f in fields)
    note = ("" if main else "<div class='why'>No image supplied — showing composition spec. Uses <code>object-fit: contain</code> "
            "(never crops the product). Fill <code>THUMBNAIL-REVIEW.json</code> with reviewer + answers.</div>")
    body = (note + f"<div class='card' style='text-align:center'>{cells}</div>"
            f"<div class='card'><table><tr><th>Reviewer check</th><th>Answer per size</th></tr>{ck}</table>"
            f"<p class='muted'>Preview sizes are configurable and illustrative — buyers don't all decide at one exact pixel size.</p></div>")
    return rev, L.page("Thumbnail Review","Creative Edge v2.2 · Output 3",
                       "Preview at search-result sizes (contain, not crop) and record a real reviewer decision.", body)

# ---- 4. storyboard ----
def build_storyboard(brief):
    prod = brief.get("product", {})
    g = prod.get("garment_type","sweatshirt"); design = prod.get("design","the personalized name")
    method = prod.get("decoration_method","machine embroidery"); pkg = bool(prod.get("gift_packaging_verified", False))
    plan = [
      (1,"AMAZON_MAIN_IMAGE","Win the click","What exactly is it?","Sees a generic product", None,
       f"Exact {g}, product-only, white background, accurate color, name at real proportions, product fills frame. NO text/props/insets.",
       ["real/accurate product image","embroidery design"],["product type","exact color","placement"],
       ["no headline","no props","no inset","no gift box"], False),
      (2,"AMAZON_SECONDARY_IMAGE","Explain why this product","Why this one?","Doesn't see value","Personalized, Not Generic",
       "Product + one crisp value line; name front and center.",["main product image"],["personalization value"],[], True),
      (3,"AMAZON_SECONDARY_IMAGE","Prove quality","Is the quality real?","Fears cheap print","See the Stitch Detail",
       f"Macro of real {method}: visible thread, satin edges, raised stitch, fabric texture.",["REAL macro sample photo"],["decoration method"],["AI macro is NOT proof"], True),
      (4,"AMAZON_SECONDARY_IMAGE","Remove ordering confusion","How do I personalize it?","Confused how to order","Personalized in 4 Easy Steps",
       "4 real steps: Choose → Enter name & credentials → We embroider → Ships.",["step icons"],["personalization fields"],["don't invent a proof step"], True),
      (5,"AMAZON_SECONDARY_IMAGE","Reduce sizing anxiety","Will it fit?","Worried about fit","Find Your Fit",
       "Accurate size chart from REAL garment measurements + fit note.",["real measurements"],["available sizes"],["measurements must be real"], True),
      (6,"AMAZON_SECONDARY_IMAGE","Clarify choices","What colors?","Unsure of options","Colors She'll Love",
       "Grid of the REAL available colors with clear labels.",["real color swatches"],["available colors"],["no unavailable colors"], True),
      (7,"AMAZON_SECONDARY_IMAGE","Show fit & audience","Does it look good worn?","Can't picture it worn","Made for the Ones Who Show Up",
       "Realistic worn shot of the exact garment (real photo preferred; AI = INTERNAL_CONCEPT_ONLY until verified).",["worn photo"],["exact garment + placement"],["no occupational misrepresentation"], True),
      (8,"AMAZON_SECONDARY_IMAGE","Strengthen emotional relevance","Is it a good gift?","Not sure it's giftable","A Gift She'll Be Proud to Wear",
       "One focused occasion, warm tone, product dominant. Packaging only if verified.",["lifestyle/gift image"],["occasion"],["one occasion, not all holidays"], True),
      (9,"AMAZON_SECONDARY_IMAGE","Set expectations","When will it arrive & how made?","Worried about delivery/quality","Made to Order",
       f"Made to order + {method} + care + production time + support. Packaging only if real.",["care/production graphic"],["production time","care"],["no guaranteed-delivery claim","no 'handmade'"], True),
    ]
    imgs, prompts = [], ["# IMAGE PROMPTS — 9-image set (Image 1 has NO text)", ""]
    for num,slot,obj,q,objn,head,comp,assets,facts,prohib,txt in plan:
        head_problems = claim_check_headline(head, pkg) if head else []
        item = {"image":num,"slot_type":slot,"objective":obj,"customer_question":q,"objection_removed":objn,
                "headline":head,"supporting_copy":None,"text_allowed":txt,"composition":comp,"source_assets":assets,
                "product_facts_required":facts,"prohibited":prohib,"claim_check":("OK" if not head_problems else head_problems),
                "category_rules_verified":False,"consistency_rules":[f"garment must be {g}",f"design = {design}","same name across all images"]}
        if num in (3,5,7):
            item["photography_prompt"] = f"Shoot a REAL {g}: {comp}"; item["image_generation_prompt"] = None
        elif num == 1:
            item["photography_prompt"] = f"Product-only {g} on pure white, accurate color, name at real proportions. NO text, NO props."
            item["image_generation_prompt"] = None
        else:
            item["image_generation_prompt"] = (f"Studio e-commerce image, {comp} Garment: {g}. "
                f"Embroidered '{design}' in satin-stitch (raised, glossy, hard edges, no gradients). "
                f"Headline text: \"{head}\". Mobile-readable. No brand logos, no characters.")
            item["photography_prompt"] = None
        imgs.append(item)
        prompts.append(f"## Image {num} — {obj}  [{slot}]")
        if num == 1:
            prompts.append("**No text on the main image.**")
            prompts.append("REAL PHOTO / PRODUCT-ONLY: " + item["photography_prompt"] + "\n")
        else:
            prompts.append(f"**Headline:** {head}" + (f"  ⚠️ CLAIM ISSUE: {head_problems}" if head_problems else ""))
            prompts.append((item['image_generation_prompt'] or ("REAL PHOTO NEEDED: " + (item['photography_prompt'] or ''))) + "\n")
    flagged = [i["image"] for i in imgs if i["claim_check"] != "OK"]
    tr = ""
    for it in imgs:
        kind = "📷 real photo" if it["image"] in (3,5,7) else ("🚫 no text" if it["image"]==1 else "🎨 generate")
        head = it["headline"] or "— (no text) —"; cflag = "" if it["claim_check"]=="OK" else " ⚠️"
        tr += (f"<tr><td><b>{it['image']}</b><br><span class='muted'>{L.esc(it['slot_type'].replace('AMAZON_',''))}</span></td>"
               f"<td>{L.esc(it['objective'])}<br><span class='muted'>{L.esc(it['customer_question'])}</span></td>"
               f"<td>{L.esc(head)}{cflag}</td><td class='muted'>{L.esc(it['composition'])}</td><td>{kind}</td></tr>")
    body = (f"<div class='card'><table><tr><th>#</th><th>Objective / question</th><th>Headline</th><th>Composition</th><th>Source</th></tr>{tr}</table></div>"
            f"<div class='why'>Image 1 = compliant, <b>no text</b>. Images 3/5/7 need <b>real photos</b>. Every headline is claim-checked. "
            f"{'⚠️ Claim issues on images: '+str(flagged) if flagged else 'All headlines pass the claim check.'}</div>")
    return {"images":imgs,"headline_claim_flags":flagged}, L.page("Nine-Image Conversion Storyboard","Creative Edge v2.2 · Output 4",
            "Image 1 wins the click (no text); images 2–9 each remove one objection with claim-safe copy.", body), "\n".join(prompts)

# ---- 5. embroidery proof ----
def build_embroidery_proof(brief):
    prod = brief.get("product", {}); F = brief.get("__folder", ".")
    is_emb = "embroider" in str(prod.get("decoration_method","")).lower()
    publication_intent = bool(brief.get("publication_intent", False))
    imgs = brief.get("our_images") or {}
    main_ai = prod.get("main_image_is_ai", True); reasons = []; asset_rec = None
    # v2.3.2 (asset-backed): a boolean is a statement, not evidence. Validate the real files.
    macro_path = imgs.get("macro")            # a real macro photo path
    supplier_path = imgs.get("supplier_photo") or prod.get("supplier_confirmed_photo_path")
    macro_ok = supplier_ok = False
    supplier_rec = None
    if AV is not None:
        if macro_path and isinstance(macro_path, str):
            av = AV.validate_image(macro_path, project_dir=F, source_type="real_macro",
                                   sku=prod.get("supplier_sku",""), design_version=prod.get("design_version",""),
                                   reviewer=brief.get("asset_reviewer",""))
            macro_ok = AV.is_real_image(av)
            if macro_ok: asset_rec = av
            elif macro_path: reasons.append(f"macro not usable: {av.get('reason')}")
        if supplier_path and isinstance(supplier_path, str):
            av2 = AV.validate_image(supplier_path, project_dir=F, source_type="supplier_photo")
            supplier_rec = av2
            supplier_ok = AV.is_real_image(av2)
            if supplier_ok and not asset_rec: asset_rec = av2
    # v2.3.3: a decoded file is an INPUT to proof, not proof. Require a hash-bound content review.
    prev = brief.get("embroidery_proof_review") or {}
    def review_confirms(av_rec):
        if not isinstance(prev, dict): return (False, "no review record")
        need = ["individual_threads_visible","stitch_edges_visible","fabric_weave_visible",
                "image_is_not_ai","image_is_not_blank","supplier_sku_matches","design_version_matches",
                "thread_colors_match","placement_matches"]
        if not prev.get("reviewer") or not prev.get("reviewed_at"): return (False, "review missing reviewer/timestamp")
        if not prev.get("asset_hash"): return (False, "review missing asset_hash")
        if prev.get("asset_hash") != av_rec.get("sha256"): return (False, "review hash does not match current macro (stale)")
        missing = [k for k in need if prev.get(k) is not True]
        if missing: return (False, "review did not confirm: " + ", ".join(missing))
        return (True, "reviewer confirmed visible embroidery on the matching asset")
    # v2.3.4 (P0.1): a supplier reference photo is proof only with its OWN hash-bound review.
    # A boolean supplier_evidence_reviewed on a blank/AI file is not evidence.
    srev = brief.get("supplier_reference_review") or {}
    def supplier_review_confirms(av_rec):
        if not isinstance(srev, dict): return (False, "no supplier review record")
        if not av_rec: return (False, "no decoded supplier photo")
        if not AV.is_quality_acceptable(av_rec): return (False, f"supplier photo below minimum size: {av_rec.get('reason')}")
        if not srev.get("reviewer") or not srev.get("reviewed_at"): return (False, "supplier review missing reviewer/timestamp")
        if not srev.get("asset_hash"): return (False, "supplier review missing asset_hash")
        if srev.get("asset_hash") != av_rec.get("sha256"): return (False, "supplier review hash does not match current supplier photo (stale)")
        need = ["supplier_identity_verified","supplier_sku_matches","design_version_matches",
                "thread_colors_match","placement_matches","image_is_not_ai","image_is_not_blank",
                "visible_embroidery_confirmed"]
        missing = [k for k in need if srev.get(k) is not True]
        if missing: return (False, "supplier review did not confirm: " + ", ".join(missing))
        return (True, "supplier-reference review confirmed on the matching asset")
    if not is_emb:
        status = L.NOT_APPLICABLE; reasons.append("product is not embroidery")
    elif macro_ok and (asset_rec or {}).get("status") == "QUALITY_REVIEW_REQUIRED":
        status = L.DRAFT_UNVERIFIED
        reasons.append(f"macro decoded but below minimum size ({asset_rec['width']}x{asset_rec['height']}) — reshoot larger; a tiny/low-res image cannot prove embroidery")
    elif macro_ok:
        ok, why = review_confirms(asset_rec)
        if ok:
            status = L.PROVEN; reasons.append(why + f" ({asset_rec['width']}x{asset_rec['height']})")
        else:
            status = L.DRAFT_UNVERIFIED
            reasons.append(f"real macro file present but NOT proof yet — {why}. Run the embroidery-proof review.")
    elif supplier_ok:
        ok_s, why_s = supplier_review_confirms(supplier_rec)
        if ok_s:
            status = L.PARTIALLY_PROVEN; reasons.append(why_s)
        else:
            status = L.DRAFT_UNVERIFIED
            reasons.append(f"supplier photo present but NOT proof yet — {why_s}. Complete a supplier_reference_review.")
    elif main_ai and publication_intent:
        status = L.MISLEADING; reasons.append("AI imagery would be published as real proof — not allowed")
    else:
        status = L.DRAFT_UNVERIFIED
        reasons.append("no reviewed real macro/supplier asset — internal draft only; a decoded file alone cannot prove embroidery")
    pub_block = bool(status == L.MISLEADING or (publication_intent and status in (L.DRAFT_UNVERIFIED, L.UNPROVEN)))
    data = {"status":status,"reasons":reasons,"publication_intent":publication_intent,"publication_blocked":pub_block,
            "required_action":("Shoot ONE real macro of a stitched sample and validate it (asset_validator) before publishing." if status in (L.DRAFT_UNVERIFIED,L.MISLEADING,L.UNPROVEN) else "OK"),
            "asset": asset_rec,
            "evidence":{"source_type":("real_macro" if macro_ok else "supplier_photo" if supplier_ok else "none"),
                        "asset_hash":(asset_rec or {}).get("sha256"),
                        "thread_colors":prod.get("thread_colors"),"placement":prod.get("design_placement")}}
    # write EMBROIDERY-PROOF.json so the gate/pipeline can consume it
    try: L.save_json(os.path.join(F, "EMBROIDERY-PROOF.json"), data)
    except Exception: pass
    body = (f"<div class='card'><p style='font-size:20px'>{L.badge(status)}</p>"
            f"<ul class='muted'>{''.join('<li>'+L.esc(r)+'</li>' for r in reasons)}</ul>"
            f"<div class='why'><b>Rule:</b> an internal AI concept is a DRAFT, never proof; AI shown as real proof is MISLEADING. "
            f"Embroidery products need at least a supplier-confirmed reference (PARTIALLY_PROVEN) before the publication package. {L.esc(data['required_action'])}</div></div>")
    return data, L.page("Embroidery Proof Audit","Creative Edge v2.2 · Output 5",
            "Internal AI draft (fine) vs AI-as-proof (blocked) vs real macro (PROVEN).", body)

# ---- 6. consistency ----
def build_consistency_audit(brief):
    prod = brief.get("product", {}); canon_g = prod.get("garment_type","").lower()
    prod_folder = brief.get("__folder", ".")
    specs = brief.get("image_specs", []); mismatches = []
    title = str(brief.get("listing_statements",{}).get("title","")).lower()
    for gword in ["hoodie","t-shirt","tshirt","jacket","tank","mock neck"]:
        if gword in title and gword not in canon_g and canon_g not in gword:
            mismatches.append({"asset":"title","issue":f"title says '{gword}' but product is '{canon_g}'","severity":L.CONVERSION_RISK})
    first_name = specs[0].get("personalization_example") if specs else None
    for sp in specs:
        img = sp.get("image","?"); gt = str(sp.get("garment_type","")).lower()
        if gt and canon_g and gt not in canon_g and canon_g not in gt:
            mismatches.append({"asset":f"image {img}","issue":f"shows '{gt}' but product is '{canon_g}'","severity":L.BLOCKED})
        if sp.get("hood") and not prod.get("hood"):
            mismatches.append({"asset":f"image {img}","issue":"shows a hood the product lacks","severity":L.BLOCKED})
        if sp.get("pockets") and not prod.get("pockets"):
            mismatches.append({"asset":f"image {img}","issue":"shows pockets the product lacks","severity":L.CONVERSION_RISK})
        nm = sp.get("personalization_example")
        if nm and first_name and nm != first_name:
            mismatches.append({"asset":f"image {img}","issue":f"name '{nm}' differs from '{first_name}'","severity":L.CONVERSION_RISK})
    # empty / single spec cannot prove cross-image consistency (but a single BAD spec can still BLOCK)
    # v2.3.4 (P0.4): actual CONSISTENT requires ≥2 specs whose asset_hash matches a REAL current
    # file in the project (invented/stale hashes do not count). A string is not evidence.
    backed = []
    for s in specs:
        ah = s.get("asset_hash"); ap = s.get("asset_path")
        if not (ah and s.get("reviewed_by") and s.get("reviewed_at") and ap and AV is not None):
            continue
        av = AV.validate_image(ap, project_dir=prod_folder)
        if not AV.is_quality_acceptable(av):
            mismatches.append({"asset":f"image {s.get('image','?')}","issue":f"spec asset not usable: {av.get('reason')}","severity":L.CONVERSION_RISK})
            continue
        if av.get("sha256") != ah:
            mismatches.append({"asset":f"image {s.get('image','?')}","issue":"spec asset_hash does not match the real file (stale/invented)","severity":L.CONVERSION_RISK})
            continue
        backed.append(s)
    if any(m["severity"]==L.BLOCKED for m in mismatches):        status = L.BLOCKED
    elif any(m["severity"]==L.CONVERSION_RISK for m in mismatches): status = L.CONVERSION_RISK
    elif len(specs) == 0:                                         status = L.CONSISTENCY_INCOMPLETE
    elif len(specs) == 1:                                         status = L.CONSISTENCY_ONE_SPEC
    elif len(backed) < 2:                                        status = L.PLAN_CONSISTENT   # agree on paper, not asset-backed
    elif mismatches:                                            status = L.MINOR_REVIEW
    else:                                                       status = L.CONSISTENT
    note = "" if len(specs)>=2 else ("<div class='why'>Fewer than 2 reviewed image specs — <b>cannot prove cross-image consistency</b>. "
            "Add <code>image_specs</code> for each real/planned image (garment_type, hood, pockets, color, design dims, personalization_example).</div>")
    rows = "".join(f"<tr><td>{L.esc(m['asset'])}</td><td>{L.esc(m['issue'])}</td><td>{L.badge(m['severity'])}</td></tr>" for m in mismatches) \
           or "<tr><td colspan=3 class='muted'>No contradictions in supplied assets.</td></tr>"
    body = (f"<div class='card'><p style='font-size:20px'>{L.badge(status)}</p>{note}"
            f"<table><tr><th>Asset</th><th>Mismatch</th><th>Severity</th></tr>{rows}</table></div>")
    try:
        import config as _cfg
        _tv = _cfg.tool_version()
    except Exception:
        _tv = None
    data = {"status": status, "mismatches": mismatches,
            "evidence": {"n_specs": len(specs), "n_asset_backed_specs": len(backed),
                         "asset_backed_hashes": [b.get("asset_hash") for b in backed],
                         "reviewers": sorted({b.get("reviewed_by") for b in backed if b.get("reviewed_by")}),
                         "tool_version": _tv}}
    return data, L.page("Visual Consistency Audit","Creative Edge v2.2 · Output 6",
            "Every image and statement must describe the SAME product.", body)

# ---- 7a plan score / 7b actual asset ----
def build_plan_score(brief, matrix, storyboard, main):
    def has(x): return 1 if x else 0
    comps_ok = matrix["confidence"]["confidence"] in ("MEDIUM","HIGH")
    parts = {
        "competitor_evidence": 20 if comps_ok else (10 if matrix["confidence"]["effective_unique_sample"]>=4 else 4),
        "gap_quality": min(15, 5+3*len(matrix["gaps_to_own_secondary"])),
        "storyboard_coverage": 15 if len(storyboard["images"])==9 else 8,
        "objection_coverage": 15 if len({i["objection_removed"] for i in storyboard["images"]})>=8 else 9,
        "experiment_quality": 10,
        "product_fact_completeness": 10*sum(has(brief["product"].get(k)) for k in
            ("garment_type","colors","thread_colors","decoration_method","design_placement","personalization_fields"))//6,
        "claim_safety": 15 if not storyboard["headline_claim_flags"] else 6,
    }
    total = sum(parts.values())
    verdict = "PLAN READY" if total>=85 else "IMPROVE PLAN" if total>=70 else "REBUILD PLAN"
    tr = "".join(f"<tr><td>{L.esc(k.replace('_',' '))}</td><td style='text-align:right'>{v}</td></tr>" for k,v in parts.items())
    body = (f"<div class='card' style='text-align:center'><div class='big'>{total}/100</div><p><b>{verdict}</b></p>"
            f"<p class='muted'>Measures STRATEGY completeness — not the finished images.</p></div>"
            f"<div class='card'><table><tr><th>Component</th><th>Score</th></tr>{tr}</table></div>")
    return {"total":total,"parts":parts,"verdict":verdict}, L.page("Creative Plan Readiness Score","Creative Edge v2.2 · Output 7a",
            "Is the strategy complete and claim-safe? (Separate from the actual images.)", body)

def _resolve_thumbnail_review(brief, folder):
    """v2.3.4-RC1 (P0.2): prefer a completed THUMBNAIL-REVIEW.json over the embedded brief object.
    A file with a reviewer wins; otherwise fall back to brief['thumbnail_review'] (deprecated)."""
    path = os.path.join(folder, "THUMBNAIL-REVIEW.json")
    if os.path.exists(path):
        try:
            filerev = json.load(open(path, encoding="utf-8"))
            if isinstance(filerev, dict) and filerev.get("reviewer"):
                return filerev
        except Exception:
            pass
    return brief.get("thumbnail_review") or {}

def _incomplete_asset(reason):
    body = (f"<div class='card' style='text-align:center'><div class='big'>INCOMPLETE</div>"
            f"<p><b>Cannot score real assets.</b></p><div class='why'>{L.esc(reason)}</div></div>")
    return ({"status":"INCOMPLETE","total":None,"reason":reason},
            L.page("Actual Asset Edge Score","Creative Edge v2.3.1 · Output 7b",
                   "Every point must come from a real image + a hash-matched reviewer record. No booleans, no defaults.", body))

def build_actual_asset_score(brief, proof, consistency):
    prod = brief.get("product", {}); F = brief.get("__folder", ".")
    main = (brief.get("our_images") or {}).get("main")
    # P0.2/P0.3: require a REALLY decoded image (no boolean bypass).
    if not main or AV is None:
        return _incomplete_asset("no actual main image supplied (a decoded image file is required).")
    av = AV.validate_image(main, project_dir=F,
                           source_type=(brief.get("our_images") or {}).get("main_source_type",""),
                           sku=prod.get("supplier_sku",""), design_version=prod.get("design_version",""),
                           reviewer=brief.get("asset_reviewer",""),
                           publication_intent=bool(brief.get("publication_intent")))
    if not AV.is_real_image(av):
        return _incomplete_asset(f"image not usable: {av.get('reason')}")
    # v2.3.4 (P0.3): a decodable-but-tiny image cannot be scored — a human score must never
    # override a file-quality failure. Reject QUALITY_REVIEW_REQUIRED before any scoring.
    if not AV.is_quality_acceptable(av):
        return _incomplete_asset(f"image below minimum size — quality review required, not scorable: {av.get('reason')}")
    # require a structured, hash-matched reviewer record (P0.2) — a boolean cannot replace it.
    # v2.3.4-RC1: THUMBNAIL-REVIEW.json is the source of truth. A completed file (reviewer set) is
    # authoritative; the embedded brief.thumbnail_review is a deprecated fallback for older briefs.
    rev = _resolve_thumbnail_review(brief, F)
    if not isinstance(rev, dict) or not rev.get("reviewer") or not rev.get("reviewed_at"):
        return _incomplete_asset("no completed thumbnail review — fill THUMBNAIL-REVIEW.json (reviewer + reviewed_at + asset_hash + component answers).")
    if not rev.get("asset_hash"):
        return _incomplete_asset("thumbnail review has no asset_hash — cannot tie the review to the current image.")
    if rev.get("asset_hash") != av.get("sha256"):
        return _incomplete_asset("thumbnail review is STALE — its asset_hash does not match the current image.")
    # P0.3: every visual component derives from the review record (0/1/2). Missing → INCOMPLETE.
    def comp(key, weight):
        v = rev.get(key)
        if v is None: return None
        try: v = float(v)
        except Exception: return None
        return round(max(0, min(2, v)) / 2 * weight)
    review_parts = {"main_image_clarity":comp("main_image_clarity",16),
                    "thumbnail_clarity":comp("thumbnail_clarity",12),
                    "product_fill":comp("product_fill",8),
                    "personalization_visibility":comp("personalization_readable",8),
                    "mobile_readability":comp("mobile_readable",8)}
    missing = [k for k,v in review_parts.items() if v is None]
    if missing:
        return _incomplete_asset(f"review missing component(s): {', '.join(missing)} — score stays INCOMPLETE, no optimistic defaults.")
    # evidence-based components
    proof_pts = {L.PROVEN:15,L.PARTIALLY_PROVEN:9,L.DRAFT_UNVERIFIED:0,L.UNPROVEN:0,L.MISLEADING:0,L.NOT_APPLICABLE:12}[proof["status"]]
    cons_pts = {L.CONSISTENT:15,L.MINOR_REVIEW:10,L.CONVERSION_RISK:4,L.BLOCKED:0}.get(consistency["status"], 0)
    if consistency["status"] in (L.CONSISTENCY_INCOMPLETE, L.CONSISTENCY_ONE_SPEC, L.PLAN_CONSISTENT):
        return _incomplete_asset("visual consistency not proven (need ≥2 asset-backed, reviewed image specs).")
    blocked = consistency["status"]==L.BLOCKED or proof["status"]==L.MISLEADING or proof.get("publication_blocked")
    acc = 0 if blocked else comp("accuracy_verified", 10)
    if acc is None:
        return _incomplete_asset("review missing accuracy_verified.")
    parts = {**review_parts, "product_proof":proof_pts, "visual_consistency":cons_pts, "product_accuracy":acc}
    total = sum(parts.values())
    verdict = ("BLOCKED — accuracy/proof overrides score" if blocked else "STRONG TEST CANDIDATE" if total>=90
               else "USABLE — IMPROVE" if total>=80 else "WEAK EDGE" if total>=70 else "REBUILD")
    tr = "".join(f"<tr><td>{L.esc(k.replace('_',' '))}</td><td style='text-align:right'>{v}</td></tr>" for k,v in parts.items())
    body = (f"<div class='card' style='text-align:center'><div class='big'>{total}/100</div><p><b>{L.esc(verdict)}</b></p>"
            f"<p class='muted'>image {av['width']}x{av['height']} · reviewed by {L.esc(rev.get('reviewer'))}</p></div>"
            f"<div class='card'><table><tr><th>Component</th><th>Source</th><th>Score</th></tr>{tr}</table>"
            + ("<div class='why' style='border-color:#c0392b;background:#fdECEC'>⛔ Accuracy/proof issue blocks approval regardless of the number.</div>" if blocked else "") + "</div>")
    return {"status":"SCORED","total":total,"parts":parts,"verdict":verdict,"approval_blocked":bool(blocked),
            "asset":av,"reviewer":rev.get("reviewer")}, \
           L.page("Actual Asset Edge Score","Creative Edge v2.3.1 · Output 7b",
                  "Evidence-derived: real decoded image + hash-matched reviewer answers. No hard-coded points.", body)

# ---- 8. experiments ----
def build_experiments(main, matrix):
    tests = [
      dict(area="Main image (control vs improved prep)", major="prep/lighting/fill + valid color contrast",
           version_a="Clean Full-Garment Control", version_b="High-Contrast Valid Color / Improved Prep", primary="CTR",
           owner="MANUAL: swap main image (verify experiment publish settings first)"),
      dict(area="Personalization readability (photography, not enlargement)", major="contrast/crop for the real name",
           version_a="Name at competitor scale", version_b="Higher contrast + honest crop", primary="CTR",
           owner="MANUAL: update main image"),
      dict(area="Real embroidery proof (Image 3)", major="add real macro proof",
           version_a="No proof image", version_b="Real macro proof at slot 3", primary="unit session %",
           owner="MANUAL: add image 3"),
    ]
    data = {"tests":tests,"eligibility_note":(
        "Manage Your Experiments needs Brand Registry + eligibility + enough traffic, and may auto-publish a winner. "
        "You require MANUAL control — verify experiment publish settings first; if they conflict, run a logged sequential manual test. "
        "No universal '2–4 weeks' claim — use eligibility, traffic and significance.")}
    rows = "".join(f"<tr><td><b>{L.esc(t['area'])}</b></td><td>{L.esc(t['major'])}</td>"
                   f"<td>A: {L.esc(t['version_a'])}<br>B: {L.esc(t['version_b'])}</td><td>{L.esc(t['primary'])}</td>"
                   f"<td class='muted'>{L.esc(t['owner'])}</td></tr>" for t in tests)
    body = (f"<div class='why'>{L.esc(data['eligibility_note'])}</div>"
            f"<div class='card'><table><tr><th>Test</th><th>Major difference</th><th>A vs B</th><th>Metric</th><th>Manual action</th></tr>{rows}</table></div>")
    return data, L.page("Creative Experiment Plan","Creative Edge v2.2 · Output 8",
            "A few strong, measurable tests — eligibility + traffic + significance guidance, all manual.", body)

# ---- example + orchestrator ----
def example_brief(folder):
    def comp(asin,brand,parent,top,v): return {"asin":asin,"brand":brand,"parent_group":parent,"top_seller":top,
        "observed_at":"2026-07-15","observed_by":"staff","visual":v}
    base=lambda **k: {**{d[0]:0 for d in DIMS},"white_bg":1,**k}
    ex = {"project":"Personalized Nurse Sweatshirt","publication_intent":False,"category_rules_verified":False,
      "product":{"garment_type":"crewneck sweatshirt","neckline":"crew","sleeves":"long","hood":False,"drawstrings":False,
        "pockets":False,"fabric":"cotton-blend fleece","colors":["black","navy","teal","maroon","heather grey","blush"],
        "thread_colors":["cream","white","gold"],"design":"embroidered name + credentials (e.g. 'Sarah, RN')",
        "design_placement":"left chest","decoration_method":"machine embroidery","gift_packaging_verified":False,
        "personalization_fields":["name (<=15 chars)","credentials","specialty (optional)"],"price":"44.99",
        "delivery":"10-14 business days","has_real_sample_photo":False,"supplier_confirmed_photo":False,"main_image_is_ai":True},
      "listing_statements":{"title":"Custom Nurse Sweatshirt - Personalized Embroidered Crewneck with Name for Women"},
      "our_images":{"main":None,"macro_real":False},"image_specs":[],
      "competitors":[
        comp("B0F2YPFQKX","GODMERCH","godmerch-nurse",True,base(flat_lay=1,color_grid=1)),
        comp("B0FKYVVQPN","COUPLEHOODIES","ch-nurse",True,base(flat_lay=1,name_readable=1,size_chart=1,color_grid=1)),
        comp("B0GHGCKDMB","Chillever","chill-a",False,base(flat_lay=1,lifestyle=1)),
        comp("B0GWQZX5SM","Sweetee","sweetee-a",False,base(on_model=1,color_grid=1)),
        comp("B0AAA00001","POPPOP","pop-a",False,base(flat_lay=1)),
        comp("B0AAA00002","Giantbighands","gbh-a",False,base(flat_lay=1,name_readable=1,size_chart=1,color_grid=1)),
        comp("B0AAA00003","LovelyPOD","lp-a",False,base(on_model=1,lifestyle=1)),
        comp("B0AAA00004","Noni","noni-a",False,base(flat_lay=1,color_grid=1)),
      ]}
    p = os.path.join(folder,"creative-brief.json"); L.save_json(p, ex); print("Wrote example brief:", p)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder"); ap.add_argument("--brief", default=None); ap.add_argument("--example", action="store_true")
    a = ap.parse_args(); os.makedirs(a.folder, exist_ok=True)
    if a.example: example_brief(a.folder); return
    brief = load_brief(a.folder, a.brief); brief["__folder"] = a.folder; F = a.folder
    m_data,m_html = build_visual_matrix(brief); L.save_json(F+"/COMPETITOR-VISUAL-MATRIX.json",m_data); L.save(F+"/COMPETITOR-VISUAL-MATRIX.html",m_html)
    mi_data,mi_html = build_main_image_edge(brief,m_data); L.save_json(F+"/MAIN-IMAGE-EDGE.json",mi_data); L.save(F+"/MAIN-IMAGE-EDGE.html",mi_html)
    mic = min(mi_data["concepts"], key=lambda c: 0 if c["compliance"]["status"]==L.MI_BLOCKED else 1)["compliance"]
    L.save(F+"/MAIN-IMAGE-COMPLIANCE.html", MIV.render(mic)); L.save_json(F+"/MAIN-IMAGE-COMPLIANCE.json", mic)
    ts_data,ts_html = build_thumbnail_sim(brief); L.save(F+"/THUMBNAIL-REVIEW.html",ts_html)
    sb_data,sb_html,prompts = build_storyboard(brief); L.save_json(F+"/IMAGE-STORYBOARD.json",sb_data); L.save(F+"/IMAGE-STORYBOARD.html",sb_html); L.save(F+"/IMAGE-PROMPTS.md",prompts)
    ep_data,ep_html = build_embroidery_proof(brief); L.save(F+"/EMBROIDERY-PROOF.html",ep_html); L.save_json(F+"/EMBROIDERY-PROOF.json",ep_data)
    ca_data,ca_html = build_consistency_audit(brief); L.save(F+"/VISUAL-CONSISTENCY.html",ca_html); L.save_json(F+"/VISUAL-CONSISTENCY.json",ca_data)   # P0.9
    ps_data,ps_html = build_plan_score(brief,m_data,sb_data,mi_data); L.save(F+"/CREATIVE-PLAN-SCORE.html",ps_html); L.save_json(F+"/CREATIVE-PLAN-SCORE.json",ps_data)
    as_data,as_html = build_actual_asset_score(brief,ep_data,ca_data); L.save(F+"/ACTUAL-ASSET-EDGE-SCORE.html",as_html); L.save_json(F+"/ACTUAL-ASSET-EDGE-SCORE.json",as_data)
    ex_data,ex_html = build_experiments(mi_data,m_data); L.save_json(F+"/CREATIVE-EXPERIMENTS.json",ex_data); L.save(F+"/CREATIVE-EXPERIMENTS.html",ex_html)
    # P0.6: write creative gate results into the manifest so --status/--next reflect real work.
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
        import manifest as _M, gate_engine as _GE
        actual_status = "APPROVED" if (as_data["status"]=="SCORED" and not as_data.get("approval_blocked")) else \
                        ("INCOMPLETE" if as_data["status"]=="INCOMPLETE" else "REVISE")
        import hashing as _H
        def _ev(*names):
            out = []
            for n in names:
                pth = os.path.join(F, n)
                if os.path.exists(pth): out.append({"path": n, "sha256": _H.file_sha256(pth)})
            return out
        _M.set_gate(F, "MAIN_IMAGE_COMPLIANCE", _GE.gate("MAIN_IMAGE_COMPLIANCE", mic["status"], evidence=_ev("MAIN-IMAGE-COMPLIANCE.json")).to_dict())
        _M.set_gate(F, "EMBROIDERY_PROOF", _GE.gate("EMBROIDERY_PROOF", ep_data["status"], evidence=_ev("EMBROIDERY-PROOF.json")).to_dict())
        _M.set_gate(F, "VISUAL_CONSISTENCY", _GE.gate("VISUAL_CONSISTENCY", ca_data["status"], evidence=_ev("VISUAL-CONSISTENCY.json")).to_dict())
        _M.set_gate(F, "ACTUAL_ASSET_EDGE", _GE.gate("ACTUAL_ASSET_EDGE", actual_status, evidence=_ev("ACTUAL-ASSET-EDGE-SCORE.json")).to_dict())
    except Exception as _e:
        print("  (manifest sync skipped:", _e, ")")
    # v2.3.4 (P0.5): Creative Edge reports only the CREATIVE PACKAGE status, derived from the
    # four creative gates it just wrote. It NEVER claims project publication readiness — that
    # verdict belongs only to the central manifest (pipeline --status), which also requires the
    # owner main-image + creative + final approvals that this module cannot grant.
    hard_block = (mic["status"]==L.MI_BLOCKED or ep_data["status"]==L.MISLEADING or ca_data["status"]==L.BLOCKED)
    evidence_incomplete = (as_data["status"]=="INCOMPLETE"
                           or ep_data["status"] in (L.DRAFT_UNVERIFIED,L.UNPROVEN)
                           or ca_data["status"] not in (L.CONSISTENT,)
                           or mic["status"] != "APPROVED")   # APPROVED only via --approve-main-image
    package_status = ("CREATIVE PACKAGE BLOCKED" if hard_block
                      else "CREATIVE PACKAGE REVIEW REQUIRED" if evidence_incomplete
                      else "CREATIVE PACKAGE COMPLETE")
    idx = [("Competitor Visual Matrix","COMPETITOR-VISUAL-MATRIX.html"),("Main Image Edge (compliant)","MAIN-IMAGE-EDGE.html"),
        ("Main Image Compliance","MAIN-IMAGE-COMPLIANCE.html"),("Thumbnail Review","THUMBNAIL-REVIEW.html"),
        ("9-Image Storyboard","IMAGE-STORYBOARD.html"),("Image Prompts","IMAGE-PROMPTS.md"),("Embroidery Proof","EMBROIDERY-PROOF.html"),
        ("Visual Consistency","VISUAL-CONSISTENCY.html"),("Creative PLAN Score","CREATIVE-PLAN-SCORE.html"),
        ("ACTUAL ASSET Score","ACTUAL-ASSET-EDGE-SCORE.html"),("Creative Experiments","CREATIVE-EXPERIMENTS.html")]
    links = "".join(f"<li><a href='{h}'>{L.esc(t)}</a></li>" for t,h in idx)
    summary = (f"<div class='card'><p><b>Plan readiness:</b> {ps_data['total']}/100 ({L.esc(ps_data['verdict'])})</p>"
        f"<p><b>Actual asset edge:</b> {as_data['status'] if as_data['status']=='INCOMPLETE' else str(as_data['total'])+'/100'}</p>"
        f"<p><b>Embroidery proof:</b> {L.badge(ep_data['status'])} · <b>Consistency:</b> {L.badge(ca_data['status'])} · "
        f"<b>Main image:</b> {L.badge(mic['status'])} · <b>Competitor confidence:</b> {L.badge(m_data['confidence']['confidence'])}</p>"
        f"<p style='font-size:16px'><b>Creative package:</b> {L.esc(package_status)}</p>"
        f"<div class='why'>This is the creative package status only. Project publication readiness is decided by "
        f"<code>python pipeline.py {L.esc(F)} --status</code> after the owner runs "
        f"<code>--approve-main-image</code>, <code>--approve-creative</code>, and <code>--approve-final</code>.</div></div>"
        f"<div class='card'><h2>Reports</h2><ul>{links}</ul></div>")
    L.save(F+"/CREATIVE-EDGE-INDEX.html", L.page("Creative Edge — Index (v2.2)","Conversion & Creative Edge",
                  f"{brief.get('project','project')} · plan vs actual-asset scores separated; main image compliant.", summary))
    print(f"✅ Creative Edge for '{brief.get('project')}'")
    print(f"   Plan {ps_data['total']}/100 · Actual asset {as_data['status'] if as_data['status']=='INCOMPLETE' else as_data['total']} · "
          f"proof {ep_data['status']} · consistency {ca_data['status']} · main image {mic['status']}")
    print(f"   {package_status}")
    print(f"   (project publication is decided by: python pipeline.py {F} --status)")
    # a package that is blocked or missing evidence must NOT exit 0 (3 = review/evidence required)
    sys.exit(0 if package_status=="CREATIVE PACKAGE COMPLETE" else 3)

if __name__ == "__main__":
    main()
