#!/usr/bin/env python3
"""
scripts.phase6a_build — generate the Phase 6A product-readiness workspace for a selected product.

Local and offline only. Reuses the Phase 5 authorities via production.product_workspace; it never
touches Amazon or Seller Central, never opens a network connection, and writes ONLY inside the selected
run workspace (<run>/phase6/6A/). It also emits a sanitized proof gate at the repository root.

Usage:
  python scripts/phase6a_build.py                 # default proof product: runs/T2
  python scripts/phase6a_build.py <run_folder>    # any run workspace
  python scripts/phase6a_build.py --proof         # also (re)write SESSION6A-PROOF-GATE.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "core"))

from production import product_workspace as PW      # noqa: E402
import diagnostics as D                             # noqa: E402

DEFAULT_WORKSPACE = os.path.join(ROOT, "runs", "T2")
PROOF_GATE_NAME = "SESSION6A-PROOF-GATE.json"


def _sanitize(text):
    """Never emit a private absolute path or a machine user name into committed proof."""
    return D.redact_secrets(str(text)).replace(ROOT, "<REPO>").replace(os.path.expanduser("~"), "<HOME>")


def generate(workspace_dir, *, verify=True):
    """Build + write the Phase 6A artifacts twice and confirm determinism. Returns a proof dict."""
    D.clear_events()
    r1 = PW.build_product_workspace(workspace_dir)
    dest = PW.phase6a_dir(workspace_dir)
    man1 = PW.write_phase6a_artifacts(r1, dest_dir=dest, workspace_dir=workspace_dir,
                                      started_at="run-1", completed_at="run-1")
    # second run into the SAME location must reproduce byte-identical content artifacts.
    r2 = PW.build_product_workspace(workspace_dir)
    man2 = PW.write_phase6a_artifacts(r2, dest_dir=dest, workspace_dir=workspace_dir,
                                      started_at="run-2", completed_at="run-2")
    ver = PW.verify_phase6a_artifacts(dest, workspace_dir=workspace_dir) if verify else {"ok": None}

    identical = man1["deterministic_content_sha256"] == man2["deterministic_content_sha256"] \
        and r1.deterministic_content_sha256() == r2.deterministic_content_sha256() \
        and PW._owner_input_doc(r1)["content_sha256"] == PW._owner_input_doc(r2)["content_sha256"]

    outbound_attempts = sum(1 for e in D.recent_events(100)
                            if e.code == D.OUTBOUND_NETWORK_DISABLED)
    counts = r1._requirement_counts()
    snap = PW.runtime_safety_snapshot()
    proof = {
        "workspace_id": r1.workspace_id,
        "product_id": r1.product_id,
        "product_type": r1.product_type,
        "category_identifier": r1._policy.category_identifier,
        "primary_keyword": r1.primary_keyword,
        "audience": r1.audience,
        "keyword_source_file": r1._src.source_file,
        "keyword_source_sha256": r1.keyword_source_sha256,
        "tier_a": r1._src.tier_counts["TIER_A"],
        "tier_b": r1._src.tier_counts["TIER_B"],
        "product_facts_sha256": r1.product_facts_sha256,
        "product_facts_source_sha256": r1.normalized_product_facts.source_sha256,
        "claim_evidence_sha256": r1.claim_evidence_sha256,
        "owner_requirement_counts": {k: counts[k] for k in ("blocking", "important", "optional",
                                                            "resolved")},
        "real_asset_requirements_required": counts["real_assets_required"],
        "blocker_count": len(r1.blockers),
        "warning_count": len(r1.warnings),
        "phase6a_workspace_state": r1.workspace_state,
        "current_listing_publishability_state": r1.current_listing_publishability_state,
        "ready_for_6b": r1.downstream_readiness["ready_for_6b"],
        "two_run_deterministic": identical,
        "artifact_verification_ok": ver["ok"],
        "workspace_content_sha256": r1.deterministic_content_sha256(),
        "manifest_deterministic_content_sha256": man1["deterministic_content_sha256"],
        "offline_network_attempts": outbound_attempts,
        "runtime_offline_only": snap["offline_only"],
        "external_ai_enabled": snap["external_ai_enabled"],
        "outbound_network_enabled": snap["outbound_network_enabled"],
        "loopback_only_effective": snap["loopback_only_effective"],
        "generated_artifacts": r1.generated_artifacts(),
        "artifact_location": "/".join(["<run>", *PW.PHASE6_SUBPATH]),
    }
    return r1, dest, proof


def write_proof_gate(proof, starting_commit="366e035"):
    doc = {
        "session": "6A",
        "phase": "Phase 6A — Product Readiness and Owner Facts",
        "starting_commit": starting_commit,
        "final_commit": None,
        "phase5_certification_status": "RELEASE1_CERTIFIED",
        "proof_workspace": proof["workspace_id"],
        "product_identity": {"product_id": proof["product_id"], "product_type": proof["product_type"],
                             "category_identifier": proof["category_identifier"],
                             "primary_keyword": proof["primary_keyword"], "audience": proof["audience"]},
        "keyword_source_filename": proof["keyword_source_file"],
        "keyword_source_sha256": proof["keyword_source_sha256"],
        "tier_a_count": proof["tier_a"],
        "tier_b_count": proof["tier_b"],
        "product_fact_hash": proof["product_facts_sha256"],
        "claim_evidence_hash": proof["claim_evidence_sha256"],
        "owner_requirement_counts": proof["owner_requirement_counts"],
        "real_asset_requirement_count": proof["real_asset_requirements_required"],
        "phase6a_workspace_state": proof["phase6a_workspace_state"],
        "current_listing_publishability_state": proof["current_listing_publishability_state"],
        "ready_for_6b": proof["ready_for_6b"],
        "two_run_determinism": proof["two_run_deterministic"],
        "required_artifact_verification_ok": proof["artifact_verification_ok"],
        "artifact_location": proof["artifact_location"],
        "offline_network_attempts": proof["offline_network_attempts"],
        "external_ai_result": "EXTERNAL_AI_DISABLED" if not proof["external_ai_enabled"] else "ENABLED",
        "amazon_or_seller_central_write": "NONE",
        "runtime": {"offline_only": proof["runtime_offline_only"],
                    "outbound_network_enabled": proof["outbound_network_enabled"],
                    "loopback_only_effective": proof["loopback_only_effective"]},
        "known_limitations": [
            "Two Phase 6 spec documents named in the handoff (AMZ_FBM_Phase6_Final_Deployment_Gates_v6_4, "
            "AMZ_FBM_Phase6_Final_Critical_Audit_and_Deployment_Prompt_v6_4) are not present in the "
            "repository; the Phase 6A specification was taken from the inline handoff.",
            "The four required artifacts and PRODUCT-WORKSPACE/STAGE-6A-MANIFEST are written under the "
            "gitignored runs/<id>/phase6/6A/ (paid H10 data root), so they are local proof, not committed.",
        ],
    }
    path = os.path.join(ROOT, PROOF_GATE_NAME)
    PW._atomic_write_json(path, doc)
    return path, doc


def main():
    ap = argparse.ArgumentParser(description="Generate the Phase 6A product-readiness workspace")
    ap.add_argument("workspace", nargs="?", default=DEFAULT_WORKSPACE)
    ap.add_argument("--proof", action="store_true", help=f"also (re)write {PROOF_GATE_NAME} at repo root")
    a = ap.parse_args()
    if not os.path.isdir(a.workspace):
        print(_sanitize(f"no such workspace: {a.workspace}"))
        sys.exit(2)
    r, dest, proof = generate(a.workspace)
    print(_sanitize(f"workspace   {proof['workspace_id']}  state {proof['phase6a_workspace_state']}"))
    print(_sanitize(f"keyword     {proof['keyword_source_file']} sha {proof['keyword_source_sha256'][:16]}… "
                    f"A={proof['tier_a']} B={proof['tier_b']}"))
    print(_sanitize(f"claims sha  {proof['claim_evidence_sha256']}"))
    print(_sanitize(f"facts sha   {proof['product_facts_sha256']}"))
    print(_sanitize(f"ready_6b    {proof['ready_for_6b']}  listing {proof['current_listing_publishability_state']}"))
    print(_sanitize(f"determinism {proof['two_run_deterministic']}  verify {proof['artifact_verification_ok']}  "
                    f"offline_attempts {proof['offline_network_attempts']}"))
    print(_sanitize(f"artifacts   {dest}"))
    if a.proof:
        path, _ = write_proof_gate(proof)
        print(_sanitize(f"proof gate  {path}"))
    sys.exit(0)


if __name__ == "__main__":
    main()
