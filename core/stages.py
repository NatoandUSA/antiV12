#!/usr/bin/env python3
"""
core.stages — the authoritative stage registry (v2.3.2).

One place that declares the research-to-publication stages, which gates each
affects, the input files it needs, who is responsible, and the recommended next
action. Powers `pipeline.py --status` and `--next` so there is always ONE clear
next action. A future dashboard should consume this state, not re-invent it.
"""
from __future__ import annotations
try:
    from . import gate_engine as GE, status as S
except ImportError:
    import gate_engine as GE, status as S

# id, name, gates, required input files (relative to project folder), role, action-if-missing
STAGES = [
    ("DEMAND_VALIDATION", "Demand validation", ["DEMAND"], ["demand-input.csv"], "researcher",
     "Fill demand-input.csv (Etsy/Trends/eBay/Reddit) and run the pipeline."),
    ("RELEVANCE_VALIDATION", "Relevance validation", ["RELEVANCE"], ["RELEVANCE-REVIEW.json"], "researcher",
     "Fill RELEVANCE-REVIEW.json (decision=GO) confirming the seed/keywords describe the exact product. Run --scaffold-gate-files to create it."),
    ("IP_AND_COMPLIANCE", "IP & compliance", ["IP_SAFETY"], ["listing.json"], "manager",
     "Run ip_guard on the listing content; resolve any BLOCK/REVIEW."),
    ("CATALOG_STRUCTURE", "Catalog & variation", ["CATALOG_STRUCTURE"], ["CATALOG-STRUCTURE.json"], "manager",
     "Define the catalog/variation plan in CATALOG-STRUCTURE.json and approve it."),
    ("PERSONALIZATION_WORKFLOW", "Personalization workflow", ["PERSONALIZATION"], ["PERSONALIZATION-RULES.json"], "operations",
     "Define personalization fields/limits/proof workflow in PERSONALIZATION-RULES.json."),
    ("CLAIMS_EVIDENCE", "Claims evidence", ["CLAIMS_EVIDENCE"], ["CLAIMS-EVIDENCE.json"], "manager",
     "Record evidence for every factual claim in CLAIMS-EVIDENCE.json."),
    ("ECONOMICS", "Economics / margin", ["ECONOMICS"], ["economics-input.csv"], "owner",
     "Fill economics-input.csv with real costs (every variant); need ≥$8/order."),
    ("PRODUCT_FEASIBILITY", "Product feasibility", ["PRODUCT_FEASIBILITY"], ["PRODUCT-FEASIBILITY.json"], "operations",
     "Record supplier/product feasibility (sizes, colors, method) in PRODUCT-FEASIBILITY.json."),
    ("FULFILLMENT", "FBM fulfillment", ["FULFILLMENT"], ["FBM-FEASIBILITY.json"], "operations",
     "Record production time, capacity, tracking in FBM-FEASIBILITY.json."),
    ("LISTING_BUILD", "Listing copy", ["LISTING_ACCURACY", "CLAIMS_EVIDENCE"], ["listing.json"], "manager",
     "Build listing.json (title/bullets/backend) and run listing_validate."),
    ("REAL_ASSET_EVIDENCE", "Real asset evidence", ["EMBROIDERY_PROOF", "VISUAL_CONSISTENCY", "ACTUAL_ASSET_EDGE"],
     ["creative-brief.json"], "creative",
     "Shoot the real main image + embroidery macro, validate them (asset_validator), add a thumbnail review + ≥2 image specs, then run creative_edge."),
    ("MAIN_IMAGE_APPROVAL", "Main-image owner approval", ["MAIN_IMAGE_COMPLIANCE"], [], "owner",
     "Owner approves the reviewed main image: pipeline.py <folder> --approve-main-image --asset <main.png> --by owner"),
    ("CREATIVE_OWNER_APPROVAL", "Creative owner approval", ["CREATIVE_OWNER_APPROVAL"], [], "owner",
     "Owner approves the creative package: pipeline.py <folder> --approve-creative --by owner"),
    ("FINAL_OWNER_APPROVAL", "Final owner approval", ["OWNER_APPROVAL"], ["listing.json", "economics-input.csv"], "owner",
     'Run: pipeline.py <folder> --approve-final --by owner (hash-bound bundle).'),
    ("MANUAL_PUBLICATION", "Manual publication", [], [], "owner",
     "Owner publishes MANUALLY in Seller Central using the publication package."),
]

def gate_status(man: dict, gate_id: str) -> str:
    g = man.get("gates", {}).get(gate_id)
    return g.get("status") if g else "NOT_RUN"

def project_traits(man: dict) -> dict:
    return {"product_family": man.get("product_family",""),
            "decoration_method": man.get("decoration_method", man.get("product_family","")),
            "requires_listing_images": man.get("requires_listing_images", True),
            "requires_embroidery_proof": man.get("requires_embroidery_proof",
                "embroider" in str(man.get("product_family","")).lower())}

def compute(man: dict) -> dict:
    """Return passing/missing gates and the first incomplete stage + its next action."""
    req = GE.required_gates(project_traits(man))
    passing, missing = [], []
    for g in req:
        st = gate_status(man, g)
        (passing if GE._passes(g, st) else missing).append((g, st))
    # first stage whose gates aren't all passing
    current = None
    for sid, name, gates, inputs, role, action in STAGES:
        relevant = [g for g in gates if g in req]
        if relevant and not all(GE._passes(g, gate_status(man, g)) for g in relevant):
            current = {"id": sid, "name": name, "gates": relevant, "inputs": inputs,
                       "role": role, "action": action}
            break
    # P0.8 safety: if gates are still missing but no stage matched, that's an orchestration error —
    # never let the CLI claim "all gates pass".
    if current is None and missing:
        mapped = {g for _,_,gs,_,_,_ in STAGES for g in gs}
        unmapped = [g for g,_ in missing if g not in mapped]
        current = {"id": "ORCHESTRATION_ERROR", "name": "Orchestration error",
                   "gates": [g for g,_ in missing], "inputs": [], "role": "engineer",
                   "action": f"These required gates have no stage mapping: {unmapped or [g for g,_ in missing]}. Fix the stage registry."}
    return {"required_gates": req, "passing": passing, "missing": missing,
            "current_stage": current, "publication_locked": man.get("publication_locked", True)}
