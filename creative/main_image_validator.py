#!/usr/bin/env python3
"""
creative.main_image_validator — Amazon MAIN-IMAGE compliance gate (v2.2).

Main image (slot 1) must be conservative: exact product only, no text/badges/insets/
collage/gift props/packaging cues, accurate garment + color + design placement, product
dominant on a white (or category-required) background. Category rules must be verified
MANUALLY — this never declares an image Amazon-compliant from a prompt string alone.
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core"))
import edge_lib as L
try:
    import asset_validator as AV
except Exception:
    AV = None

FORBIDDEN_IN_MAIN = ["headline","text:","badge","inset","split screen","split-screen",
                     "collage","gift box","gift-box","props","comparison","lifestyle",
                     "packaging","sticker","banner","overlay","how to","steps","size chart"]

def validate(spec: dict) -> dict:
    """spec: a main-image concept/spec dict. Returns structured compliance result."""
    reasons, actions = [], []
    slot_type = spec.get("slot_type", "AMAZON_MAIN_IMAGE")
    if slot_type != "AMAZON_MAIN_IMAGE":
        return {"status": L.NOT_APPLICABLE, "reasons": ["not a main-image slot"], "required_actions": []}

    text = " ".join(str(spec.get(k, "")) for k in
                    ("headline","supporting_copy","composition","concept","prompt")).lower()
    hits = [w for w in FORBIDDEN_IN_MAIN if w in text]
    if spec.get("headline") not in (None, "", "null"): hits.append("headline present")
    if spec.get("text_allowed") is True: hits.append("text_allowed=true")
    for h in hits:
        reasons.append(f"main image must not contain: {h}")

    # P0.4: accuracy defaults to UNKNOWN — missing evidence is NOT confirmed accuracy.
    for field, label in (("garment_type_accuracy","garment type"),("color_accuracy","garment color"),
                         ("placement_accuracy","design placement/scale")):
        val = str(spec.get(field, "UNKNOWN")).upper()
        if val == "CONTRADICTED": reasons.append(f"{label} CONTRADICTED by evidence")
        elif val != "VERIFIED":  reasons.append(f"{label} accuracy UNKNOWN — needs explicit evidence")

    bg = str(spec.get("background","white")).lower()
    if bg not in ("white","category_required"):
        reasons.append(f"background '{bg}' — main image should be white or the category-required background")

    # P0.4: a path string cannot establish that an image exists — decode it.
    # v2.3.4 (P0.2): a decodable-but-tiny image (QUALITY_REVIEW_REQUIRED) is NOT usable for
    # a main-image candidate — it must meet the configured minimum size.
    av = AV.validate_image(spec.get("asset_path"), project_dir=spec.get("project_dir")) if (AV and spec.get("asset_path")) else None
    has_image = bool(av and AV.is_quality_acceptable(av))
    if av and not AV.is_real_image(av):
        reasons.append(f"image not usable: {av.get('reason')}")
    elif av and AV.is_real_image(av) and not AV.is_quality_acceptable(av):
        reasons.append(f"image below main-image minimum size: {av.get('reason')}")
    cat_verified = bool(spec.get("category_rules_verified")) and bool(spec.get("category_review", {}).get("reviewer"))

    if any("must not contain" in r for r in reasons) or (av and av.get("status")=="INVALID_FILE"):
        status = L.MI_BLOCKED
    elif not has_image or any(("UNKNOWN" in r or "CONTRADICTED" in r) for r in reasons):
        status = L.MI_INCOMPLETE
        if not has_image: actions.append("supply the actual decoded main-image file")
        actions.append("provide explicit accuracy evidence (garment/color/placement = VERIFIED)")
    elif not cat_verified:
        status = L.MI_READY_REVIEW      # P0.10: pending owner review is NOT a compliant pass
        actions.append("MANUAL OWNER ACTION: verify Amazon category main-image rules with a reviewer+timestamp, then approve the asset hash")
    else:
        status = "COMPLIANT"            # technical checks complete; only APPROVED unlocks publication
    actions.append("Product fill ~85% is guidance, verify per category. Only an owner-APPROVED asset hash passes the final gate.")
    return {"status": status, "slot_type": slot_type, "reasons": reasons or ["no forbidden elements detected"],
            "required_actions": actions, "category_rules_verified": cat_verified,
            "has_actual_image": has_image, "asset": av}

def render(res: dict) -> str:
    rs = "".join(f"<li>{L.esc(r)}</li>" for r in res.get("reasons",[]))
    ac = "".join(f"<li>{L.esc(a)}</li>" for a in res.get("required_actions",[]))
    body = (f"<div class='card'><p style='font-size:20px'>{L.badge(res['status'])}</p>"
            f"<p class='muted'><b>Findings</b></p><ul class='muted'>{rs}</ul>"
            f"<div class='why'><b>Required manual actions</b><ul>{ac}</ul></div>"
            f"<p class='muted'>Category image rules change — this tool flags them for manual verification and never "
            f"declares an image compliant from text alone.</p></div>")
    return L.page("Main Image Compliance","Creative Edge · v2.2",
                  "Amazon Image 1 must be conservative: exact product only, no text/props/insets, category rules verified manually.", body)
