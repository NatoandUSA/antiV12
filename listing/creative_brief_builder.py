#!/usr/bin/env python3
"""
listing.creative_brief_builder — turn an APlusResult into human creative direction (ACT-011 support).

Produces the owner-facing artifacts that tell a photographer/designer exactly what REAL assets and owner
facts each A+ module still needs before it can go from draft to READY:

  CREATIVE-BRIEF.md              per-module creative direction, capability + status, real-asset shot list
  CREATIVE-ASSET-CHECKLIST.json  structured checklist: every required real asset, its proof purpose,
                                 alt-text spec, and current status (READY / REAL_PHOTO_REQUIRED / …)
  BASIC-APLUS-CONTENT-BRIEF.md   the Basic A+ content brief (per-module headline, body, status, blockers)

Nothing here fabricates copy or assets — it only reports what the evidence-gated builder produced and what
is still missing. Deterministic: identical builds yield identical text.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import aplus_module_registry as REG


def build_asset_checklist(result):
    """Structured checklist of every real/optional asset the A+ payload needs, with proof purpose,
    alt-text spec and current status."""
    items = []
    for a in result.asset_manifest:
        items.append({
            "asset_id": a["asset_id"],
            "module_key": a["module_key"],
            "proof_purpose": a["proof_purpose"],
            "requirements": a["requirements"],
            "real_or_generated_supplied": a["real_or_generated"],
            "status": a["status"],
            "missing_reason": a["missing_reason"],
            "alt_text_supplied": a["alt_text"],
            "alt_text_character_count": a["alt_text_character_count"],
            "alt_text_max": REG.ALT_TEXT_MAX,
        })
    real_photos_needed = [i for i in items if i["requirements"] == "REAL_PHOTO"
                          and i["status"] != REG.ASSET_READY]
    return {
        "schema_version": "1.0.0",
        "capability": result.capability,
        "assets": items,
        "real_photos_still_required": [{"module_key": i["module_key"],
                                        "proof_purpose": i["proof_purpose"]} for i in real_photos_needed],
        "real_photos_required_count": len(real_photos_needed),
        "note": "AI mockups may illustrate intent but cannot prove real stitch texture, garment fit, "
                "measurements, production process, packaging, durability, exact material/colour, or "
                "finished customization accuracy.",
        "source_claim_evidence_sha256": result.source_claim_sha256,
    }


def build_creative_brief_md(result, primary_keyword=None):
    """Human creative direction, one section per module, plus the real-photo shot list."""
    L = [f"# Creative Brief — {primary_keyword or 'A+ content'}", "",
         f"**A+ capability:** `{result.capability}`",
         f"**Basic modules READY:** {result.basic_ready_count}/{len(result.basic_modules)}",
         f"**Publishable A+ modules:** {len(result.publishable_modules())}", ""]
    if result.capability == REG.NO_A_PLUS:
        L += ["This catalog has **no A+ capability**. Use the product description and standard image stack.",
              "", "**Recommended images:**"]
        L += [f"- {img}" for img in (result.fallback_plan or {}).get("recommended_images", [])]
        return "\n".join(L) + "\n"

    L += ["## Basic modules", ""]
    for m in result.basic_modules:
        L += [f"### {m['position_slot']}. {m['module_key']} — {m['status']}",
              f"*Purpose:* {m['purpose']}",
              f"*Headline (claim-safe):* {m['headline']}",
              f"*Body (verified copy):* {m['body'] or '(blocked — no verified copy yet)'}",
              f"*Asset brief:* {m['asset_brief']}"]
        es = m["evidence_summary"]
        if es["missing_facts"]:
            L += [f"*Owner facts still required:* {', '.join(es['missing_facts'])}"]
        if es["missing_real_assets"]:
            L += [f"*Real photos still required:* {', '.join(es['missing_real_assets'])}"]
        if es["generated_only_assets"]:
            L += [f"*Replace AI/generated with REAL:* {', '.join(es['generated_only_assets'])}"]
        L += [""]

    # Premium direction
    prem = result.to_premium_draft()
    if prem.get("premium") is not None or prem.get("modules") is not None or \
            result.capability in (REG.PREMIUM_A_PLUS, REG.UNKNOWN_A_PLUS_CAPABILITY):
        L += ["## Premium dynamic modules", ""]
        if result.capability == REG.UNKNOWN_A_PLUS_CAPABILITY:
            L += ["> Premium eligibility is **UNVERIFIED**. Everything below is a DRAFT and must NOT be "
                  "pasted into Seller Central until Premium A+ access is confirmed.", ""]
        for d in result.dynamic_eval:
            mark = "✅ eligible" if d["eligible"] else "⛔ not eligible"
            L += [f"- **{d['module_key']}** — {mark} · rule: {d['eligibility_rule']}"]
            if not d["eligible"]:
                L += [f"  - needs: {', '.join(d['ineligible_reasons'])}"]
        L += ["", f"*Selected dynamic modules:* "
              f"{', '.join(result.selected_dynamic) or 'none (fewer than two eligible)'}", ""]

    # real-photo shot list
    checklist = build_asset_checklist(result)
    L += ["## Real-photo shot list", ""]
    if checklist["real_photos_still_required"]:
        for i in checklist["real_photos_still_required"]:
            L += [f"- [ ] {i['module_key']}: **{i['proof_purpose']}** (real photo — AI mockup will not do)"]
    else:
        L += ["- All required real photos supplied. ✅"]
    L += ["", "_AI mockups cannot prove real stitch texture, fit, measurements, production, packaging, "
          "durability, exact material/colour, or finished customization accuracy._", ""]
    return "\n".join(L) + "\n"


def build_basic_content_brief_md(result):
    """A compact Basic A+ content brief — the copy each module would carry and what still blocks it."""
    L = [f"# Basic A+ Content Brief", "",
         f"**Capability:** `{result.capability}` · **READY:** {result.basic_ready_count}/"
         f"{len(result.basic_modules)}", ""]
    for m in result.basic_modules:
        icon = "✅" if m["status"] == REG.READY else "⛔"
        L += [f"## {icon} {m['position_slot']}. {m['module_key']} [{m['status']}]",
              f"**{m['headline']}**", "", (m["body"] or "_(blocked — no verified copy yet)_"), ""]
        if m["missing_requirements"]:
            L += [f"_Missing:_ {', '.join(m['missing_requirements'])}", ""]
        if m["claim_ids"]:
            L += [f"_Claim lineage:_ {', '.join(m['claim_ids'])}", ""]
    return "\n".join(L) + "\n"


def write_creative_artifacts(folder, result, primary_keyword=None):
    """Write CREATIVE-BRIEF.md, CREATIVE-ASSET-CHECKLIST.json and BASIC-APLUS-CONTENT-BRIEF.md."""
    written = {}
    brief_path = os.path.join(folder, "CREATIVE-BRIEF.md")
    with open(brief_path, "w", encoding="utf-8") as f:
        f.write(build_creative_brief_md(result, primary_keyword=primary_keyword))
    written["CREATIVE-BRIEF.md"] = brief_path

    checklist_path = os.path.join(folder, "CREATIVE-ASSET-CHECKLIST.json")
    with open(checklist_path, "w", encoding="utf-8") as f:
        json.dump(build_asset_checklist(result), f, indent=2, ensure_ascii=False, sort_keys=True)
    written["CREATIVE-ASSET-CHECKLIST.json"] = checklist_path

    content_brief_path = os.path.join(folder, "BASIC-APLUS-CONTENT-BRIEF.md")
    with open(content_brief_path, "w", encoding="utf-8") as f:
        f.write(build_basic_content_brief_md(result))
    written["BASIC-APLUS-CONTENT-BRIEF.md"] = content_brief_path
    return written
