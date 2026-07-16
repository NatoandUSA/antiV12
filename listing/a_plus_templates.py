#!/usr/bin/env python3
"""
listing.a_plus_templates — reusable A+ content modules for personalized / embroidered apparel.

Each template is a dict:
  key                  short id
  title                module headline (kept claim-safe)
  copy_template        body copy with {placeholders} filled from product facts
  image_direction      what the module image should show
  requires_proof       True = this module makes a visual claim that needs a REAL photo /
                        manual confirmation before it can be published (evidence-semantics rule)
  gap_signals          gap areas (from competitor_gap_analyzer) that make this module high-priority

These are drafts for human review — no invented certifications, guarantees, or absolute claims.
"""

TEMPLATES = [
    {
        "key": "embroidery_proof",
        "title": "See the Real Stitching",
        "copy_template": "This {garment} is decorated with real machine embroidery — raised satin "
                         "stitches you can see and feel, not a flat printed graphic.",
        "image_direction": "Macro close-up of the embroidered {design} showing individual threads, "
                           "satin edges, and fabric weave. MUST be a real photo of the actual product.",
        "requires_proof": True,
        "gap_signals": ["Real embroidery proof"],
    },
    {
        "key": "personalization",
        "title": "Personalized Just for Them",
        "copy_template": "Add {personalization} to make it one of a kind. Enter the details at checkout "
                         "and we embroider exactly what you choose.",
        "image_direction": "Product with a clearly legible personalized name; small inset of the "
                           "checkout personalization fields.",
        "requires_proof": False,
        "gap_signals": ["Personalization depth"],
    },
    {
        "key": "made_to_order",
        "title": "Made to Order for You",
        "copy_template": "Each {garment} is embroidered after you order. Typical production "
                         "time: {production_time}.",
        "image_direction": "Simple production/shipping graphic; no guaranteed-delivery language.",
        "requires_proof": False,
        "gap_signals": ["Review beatability"],
    },
    {
        "key": "gift",
        "title": "A Gift They'll Actually Wear",
        "copy_template": "A thoughtful, personalized gift for {audience} — for {occasion} or just "
                         "because. Warm, practical, and made to last through everyday wear.",
        "image_direction": "One warm gift/occasion lifestyle scene, product dominant. Packaging only "
                           "if it is real.",
        "requires_proof": False,
        "gap_signals": ["Personalization depth"],
    },
    {
        "key": "fabric_comfort",
        "title": "Soft, Comfortable, Everyday",
        "copy_template": "A {fabric} {garment} built for real-life wear — soft against the skin and "
                         "easy to layer for long days.",
        "image_direction": "Fabric texture close-up + relaxed worn shot of the exact garment.",
        "requires_proof": False,
        "gap_signals": ["Image coverage"],
    },
    {
        "key": "size_fit",
        "title": "Find Your Fit",
        "copy_template": "Check the size chart before ordering. Measurements are taken from the real "
                         "{garment} so you get the fit you expect.",
        "image_direction": "Accurate size chart from REAL garment measurements + a short fit note.",
        "requires_proof": True,   # size chart must reflect real measurements
        "gap_signals": ["Image coverage"],
    },
]

BY_KEY = {t["key"]: t for t in TEMPLATES}


def select_modules(gaps, max_modules=7):
    """Pick A+ modules, prioritizing templates whose gap_signals match high-scoring competitor gaps.
    Always includes embroidery_proof + personalization (the core edge). Returns ordered template dicts."""
    gap_score = {}
    for g in (gaps or []):
        gap_score[g.get("area")] = g.get("score", 0)
    core = ["embroidery_proof", "personalization"]
    rest = [t for t in TEMPLATES if t["key"] not in core]

    def priority(t):
        return max([gap_score.get(sig, 0) for sig in t["gap_signals"]] + [0])

    ordered = [BY_KEY[k] for k in core] + sorted(rest, key=priority, reverse=True)
    return ordered[:max_modules]


def render(template, facts):
    """Fill a template's copy with product facts; leave unknown placeholders as readable blanks."""
    class _Safe(dict):
        def __missing__(self, k): return "{" + k + "}"
    copy = template["copy_template"].format_map(_Safe(facts))
    img = template["image_direction"].format_map(_Safe(facts))
    return {
        "key": template["key"],
        "headline": template["title"],
        "copy": copy,
        "image_direction": img,
        "requires_proof": template["requires_proof"],
    }


if __name__ == "__main__":
    import json
    facts = {"garment": "crewneck sweatshirt", "design": "embroidered name", "fabric": "cotton-blend",
             "personalization": "any name and credentials", "audience": "nurses",
             "occasion": "graduation", "production_time": "10-14 business days"}
    demo = [render(t, facts) for t in select_modules([{"area": "Real embroidery proof", "score": 95}])]
    print(json.dumps(demo, indent=2))
