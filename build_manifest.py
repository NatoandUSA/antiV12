#!/usr/bin/env python3
"""
build_manifest.py — write BUILD-MANIFEST.json so a reviewer can verify the archive contents
match what's documented (P0-01). Records tool version, per-file SHA-256, module presence,
test count, and a build timestamp. Run at package time.

    python build_manifest.py [--stamp <ISO8601>]
"""
import os, sys, re, json, hashlib, glob, argparse

ROOT = os.path.dirname(os.path.abspath(__file__))

KEY_MODULES = [
    "pipeline.py", "core/gate_engine.py", "core/manifest.py", "core/asset_validator.py",
    "core/stages.py", "creative/creative_edge.py", "compliance/listing_validate.py",
    "compliance/ip_guard.py", "research/phaseA_master.py", "research/asin_picker.py",
    "research/seed_expand.py", "research/keyword_intelligence.py",
    "research/competitor_gap_analyzer.py", "dashboard/app.py", "dashboard/templates/index.html",
    "listing/listing_generator.py", "listing/a_plus_templates.py",
    "production/product_workspace.py", "production/product_detail_page.py",
    "production/aplus_assembly.py", "creative/creative_production_package.py",
    "production/seller_central_package.py", "production/phase7_minimal_launch_foundation.py",
    "production/phase7_extended_launch_planning.py", "production/phase7_report_ingestion.py",
    "production/phase7_ads_analysis.py", "production/phase7_owner_dashboard.py",
    "production/phase7_unified_owner_console.py", "production/phase7_workflow_stage_model.py",
    "scripts/ci_test_gate.py",
]

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def tool_version():
    try:
        m = re.search(r'tool_version:\s*"([^"]+)"', open(os.path.join(ROOT, "config.yaml"), encoding="utf-8").read())
        return m.group(1) if m else "unknown"
    except Exception:
        return "unknown"

def count_tests():
    total = 0
    for f in glob.glob(os.path.join(ROOT, "tests", "test_*.py")):
        try:
            total += len(re.findall(r"def test_", open(f, encoding="utf-8").read()))
        except Exception:
            pass
    return total

def build(stamp=None):
    modules = {}
    for rel in KEY_MODULES:
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            modules[rel] = {"status": "VERIFIED", "sha256": sha256(p), "bytes": os.path.getsize(p)}
        else:
            modules[rel] = {"status": "UNAVAILABLE", "sha256": None, "bytes": 0}
    doc = {
        "tool_version": tool_version(),
        "built_at": stamp or "",
        "test_count": count_tests(),
        "module_count_verified": sum(1 for m in modules.values() if m["status"] == "VERIFIED"),
        "modules": modules,
        "note": "Compare these hashes against the archive to prove integrity. Modules marked UNAVAILABLE are not in this build.",
    }
    with open(os.path.join(ROOT, "BUILD-MANIFEST.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)
    return doc

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stamp", default="")
    a = ap.parse_args()
    d = build(a.stamp or None)
    print(f"BUILD-MANIFEST.json — v{d['tool_version']} · {d['module_count_verified']}/{len(d['modules'])} modules verified · {d['test_count']} tests")
