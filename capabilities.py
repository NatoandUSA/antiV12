#!/usr/bin/env python3
"""
capabilities.py — ONE source of truth for what the toolkit can do (P0.7).

Scans for the modules that actually exist and writes CAPABILITIES.json with a
maturity per capability. README / self-audit / any UI must derive status from
this file instead of hand-written (and drifting) claims.

Maturity: BUILT · BUILT_WITH_LIMITATIONS · PILOT · PARTIAL · NOT_BUILT
"""
import os, json, re
ROOT = os.path.dirname(os.path.abspath(__file__))

def _tool_version():
    try:
        with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
            m = re.search(r'tool_version:\s*"([^"]+)"', f.read())
            return m.group(1) if m else "unknown"
    except Exception:
        return "unknown"

# capability -> (representative file, declared maturity, note)
CAPS = {
    "research":              ("research/phaseA_master.py",   "BUILT", "H10 keyword scoring, Beatability, reports"),
    "asin_selection":       ("research/asin_picker.py",     "BUILT", "active-sales aware; short-batch warning"),
    "demand_validation":    ("research/demand_score.py",    "BUILT", "GO/TEST/SKIP/INCOMPLETE cross-market"),
    "seed_expansion":       ("research/seed_expand.py",     "BUILT", "next-round seeds"),
    "keyword_intelligence": ("research/keyword_intelligence.py", "BUILT", "H10 + optional YTrends; separate Amazon-opportunity + trend-momentum scores, confidence, provenance"),
    "competitor_gaps":      ("research/competitor_gap_analyzer.py", "BUILT", "Xray gaps with source_type/confidence/manual-review flags; embroidery proof never inferred from numbers"),
    "dashboard_cockpit":    ("dashboard/app.py",            "BUILT", "local Flask cockpit: upload->run->Amazon-style results; 4 build modes; products board; version/module status"),
    "listing_generator":    ("listing/listing_generator.py","BUILT", "deterministic 75-char-compliant listing.json + review Brief from research data; dynamic A+"),
    "a_plus_templates":     ("listing/a_plus_templates.py", "BUILT", "6 A+ templates for embroidered apparel; proof-required flags on visual-claim modules"),
    "ip_compliance":        ("compliance/ip_guard.py",      "BUILT", "450+ term library; exit 0/2/3"),
    "listing_validation":   ("compliance/listing_validate.py","BUILT","all-content + claim checker + category config"),
    "economics":            ("economics/economics_gate.py", "BUILT_WITH_LIMITATIONS", "missing costs now INCOMPLETE; no scenario UI"),
    "creative_plan":        ("creative/creative_edge.py",   "PILOT", "compliant main image; plan vs actual split; writes gates to manifest"),
    "actual_asset_validation":("core/asset_validator.py",   "PILOT", "real file/hash/dims; PROVEN needs hash-bound owner content review (not just a decodable file); no CV"),
    "post_launch":          ("creative/creative_diagnosis.py","PARTIAL","zero-safe diagnosis; no full query funnel"),
    "gate_engine":          ("core/gate_engine.py",         "BUILT", "per-gate pass statuses; READY_FOR_REVIEW != pass"),
    "manifest_approvals":   ("core/manifest.py",            "BUILT", "atomic writes, history, hash-bound approvals"),
    "dashboard_v1_console": ("production/phase7_unified_owner_console.py", "BUILT", "local unified console & workflow view (accepted Dashboard V1)"),
    "product_truth_prerequisite": ("production/phase7_workflow_stage_model.py", "BUILT", "Phase 6A product truth prerequisite tracking"),
    "dashboard_ui":         ("apps/web",                    "NOT_BUILT", "web app is a separate program (OS-v3)"),
    "web_app_v3":           ("apps/web",                    "NOT_BUILT", "web app is a separate program (OS-v3)"),
    "import_center":        ("services/import",             "NOT_BUILT", "planned OS-v3"),
    "database":             ("storage/db.sqlite",           "NOT_BUILT", "planned OS-v3"),
}

def build():
    caps = {}
    for name, (path, maturity, note) in CAPS.items():
        present = os.path.exists(os.path.join(ROOT, path))
        caps[name] = {"maturity": maturity if present else "NOT_BUILT",
                      "present": present, "path": path, "note": note}
    doc = {"generated_from": "capabilities.py",
           "tool_version": _tool_version(),
           "overall": {
               "backend_decision_engine": "PILOT_READY",
               "creative_planning": "PILOT_READY",
               "actual_asset_validation": "PILOT",
               "dashboard_v1_console": "BUILT",
               "post_launch_learning_loop": "PARTIAL",
               "market_beating_operating_system": "PILOT_READY (backend hardened, dashboard V1 accepted; cloud web app pending)",
           },
           "capabilities": caps}
    with open(os.path.join(ROOT, "CAPABILITIES.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
    return doc

if __name__ == "__main__":
    d = build()
    print("Wrote CAPABILITIES.json")
    for k, v in d["capabilities"].items():
        print(f"  {k:26} {v['maturity']}")
