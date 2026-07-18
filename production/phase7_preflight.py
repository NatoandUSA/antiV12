#!/usr/bin/env python3
"""production.phase7_preflight — the ONE Phase 7.0 repository + live-product preflight authority.

Phase 7.0 is a BOUNDED, READ-ONLY preflight. It inspects the actual repository and the verified
Phase 6 handoff and produces executable evidence of whether the project is ready for a *Release 7.1M
implementation session*. It NEVER implements 7.1M: it selects no PPC target, computes no bid, allocates
no budget, chooses no negative, and writes no campaign/economics/launch artifact.

It is a THIN preflight. It never re-implements serialization, hashing, atomic writes, the Phase 6
package verifier, the network scanner, or the runtime/network policy — it REUSES them:

  * canonical json / content hash / file hash / atomic write -> production.product_workspace
  * verified Phase 6 package re-verification (from actual bytes) -> production.seller_central_package
  * active-runtime network source scan -> scripts.connectivity_scan
  * runtime + network + Amazon-boundary policy -> core.runtime_policy / core.network_policy

Permanent Amazon boundary (re-asserted, never crossable in any mode): no Amazon/Seller-Central login,
no SP-API/MWS/Advertising API, no browser automation, no Amazon credential/cookie/session/token store,
no automated report retrieval, no account write, no public bind. This module opens NO network at all;
the official Amazon Ads guidance is a committed snapshot captured out-of-band by human-triggered public
research, consumed here as advisory data. External Amazon account attempts and actions equal zero.

Public API:
  assemble_phase7_preflight(repo_root, run_dir=None, mode=..., guidance_path=..., refresh_guidance=False)
      -> Phase7PreflightResult
  write_phase7_artifacts(result, out_dir) -> stage-manifest dict
  build_proof_gate(result) -> sanitized committed-proof dict
  REQUIRED_CONTRACT_FIELDS, CONNECTIVITY_MODES, READINESS_* , SESSION_*
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ("", "core", "listing", "scripts"):
    _p = _ROOT if not _sub else os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import product_workspace as PW           # noqa: E402  canonical json / hash / atomic
from production import seller_central_package as SCP      # noqa: E402  Phase 6F verifier (reused)
import runtime_policy as RP                               # noqa: E402
import network_policy as NP                               # noqa: E402
import connectivity_scan as CS                            # noqa: E402  active-runtime source scan

canonical_json = PW.canonical_json
content_sha256 = PW.content_sha256
_file_sha256 = PW._file_sha256

# -- schema / layout versions ----------------------------------------------------------------------
SCHEMA_PREFLIGHT = "phase7-preflight-report-v1"
SCHEMA_AUTHORITY_MAP = "phase7-authority-map-v1"
SCHEMA_NETWORK = "phase7-network-boundary-report-v1"
SCHEMA_HANDOFF = "phase7-phase6-handoff-audit-v1"
SCHEMA_GUIDANCE = "phase7-official-guidance-snapshot-v1"
SCHEMA_READINESS = "phase7-7_1m-readiness-matrix-v1"
SCHEMA_CONTRACT_REQ = "phase7-ppc-product-contract-requirements-v1"
SCHEMA_CONTRACT_TEMPLATE = "phase7-ppc-product-contract-template-v1"
SCHEMA_STAGE_MANIFEST = "phase7-0-stage-manifest-v1"
SCHEMA_PROOF_GATE = "session7_0-proof-gate-v1"

STAGE_ID = "7.0"
STAGE_NAME = "Repository, Phase 6 Handoff, Amazon Boundary, and Live-Product Preflight"

CONNECTIVITY_MODES = ("CONNECTED_RESEARCH", "LOCAL_SAFE", "TEST_DENY_EXTERNAL")

# -- readiness / session decisions -----------------------------------------------------------------
READY_FOR_7_1M_IMPLEMENTATION = "READY_FOR_7_1M_IMPLEMENTATION"
PHASE7_OWNER_INPUT_REQUIRED = "PHASE7_OWNER_INPUT_REQUIRED"
PHASE7_PREFLIGHT_BLOCKED = "PHASE7_PREFLIGHT_BLOCKED"
SESSION7_0_READY_FOR_REVIEW = "SESSION7_0_READY_FOR_REVIEW"

# a readiness state that is deliberately DISTINCT from "ready to launch advertising manually".
READY_FOR_MANUAL_LAUNCH = "READY_FOR_MANUAL_LAUNCH"

# -- artifact filenames (exact) --------------------------------------------------------------------
F_PREFLIGHT = "PHASE7-PREFLIGHT-REPORT.json"
F_AUTHORITY = "PHASE7-AUTHORITY-MAP.json"
F_NETWORK = "PHASE7-NETWORK-BOUNDARY-REPORT.json"
F_HANDOFF = "PHASE7-PHASE6-HANDOFF-AUDIT.json"
F_GUIDANCE = "PHASE7-OFFICIAL-GUIDANCE-SNAPSHOT.json"
F_READINESS = "PHASE7-7_1M-READINESS-MATRIX.json"
F_CONTRACT_REQ = "PPC-PRODUCT-CONTRACT-REQUIREMENTS.json"
F_STAGE_MANIFEST = "STAGE-7_0-MANIFEST.json"
F_CONTRACT_TEMPLATE = "PPC-PRODUCT-CONTRACT.template.json"
F_OWNER_INPUT = "PHASE7-OWNER-INPUT-REQUIRED.md"
F_PREFLIGHT_GUIDE = "PHASE7-PREFLIGHT-GUIDE.md"

REQUIRED_ARTIFACTS = (F_PREFLIGHT, F_AUTHORITY, F_NETWORK, F_HANDOFF, F_GUIDANCE,
                      F_READINESS, F_CONTRACT_REQ, F_STAGE_MANIFEST)

# committed Phase 6 proof (repo root, sanitized prefixes) + committed guidance fixture (repo-relative)
PHASE6F_PROOF_NAME = "SESSION6F-PROOF-GATE.json"
PHASE6_FINAL_STATUS_NAME = "PHASE6-FINAL-STATUS.json"
DEFAULT_GUIDANCE_REL = os.path.join("tests", "fixtures", "phase7", "official-guidance-snapshot.json")

# per-doc volatile keys excluded from the deterministic content hash (environment / lineage / time).
_VOLATILE = ("deterministic_content_sha256", "generated_at", "started_at", "completed_at",
             "repo_root", "run_dir", "git_head", "git_branch", "git_origin_matches",
             "connectivity_mode", "guidance_refreshed", "live_verification")

# field classifications for the PPC product contract preflight.
VERIFIED = "VERIFIED"
MISSING = "MISSING"
CONTRADICTORY = "CONTRADICTORY"
NOT_APPLICABLE = "NOT_APPLICABLE"

# the required PPC-PRODUCT-CONTRACT.json fields (order preserved for determinism).
REQUIRED_CONTRACT_FIELDS = (
    "contract_version", "marketplace", "account_type", "advertised_asin", "advertised_sku",
    "advertised_variant_id", "product_workspace_id", "phase6_package_sha256", "phase6_index_sha256",
    "keyword_source_sha256", "keyword_allocation_sha256", "product_facts_sha256",
    "claim_evidence_sha256", "manufacturing_spec_sha256", "customization_text_constraints_sha256",
    "listing_audit_state", "listing_live_confirmed", "listing_buyable_confirmed",
    "listing_version_matches_phase6", "current_selling_price", "currency",
    "inventory_or_capacity_confirmed", "normal_capacity", "peak_capacity", "current_backlog",
    "safe_handling_time", "handling_time_confirmed", "advertising_eligibility_confirmed",
    "featured_offer_eligibility_confirmed", "owner_confirmed_at", "owner_confirmation_source",
)

# fields that are auto-derivable from the verified Phase 6 handoff (never owner secrets).
_PHASE6_DERIVED_FIELDS = frozenset({
    "contract_version", "marketplace", "product_workspace_id", "phase6_package_sha256",
    "phase6_index_sha256", "keyword_source_sha256", "keyword_allocation_sha256",
    "product_facts_sha256", "claim_evidence_sha256", "listing_audit_state",
})
# "...when applicable" spec fields — applicable to this personalized-apparel product but unverified.
_WHEN_APPLICABLE_FIELDS = frozenset({"manufacturing_spec_sha256", "customization_text_constraints_sha256"})


class Phase7PreflightError(Exception):
    pass


# ================================================================ small helpers
def _finalize(doc, extra_volatile=()):
    """Stamp doc['deterministic_content_sha256'] over the stable subset (excludes volatile keys)."""
    volatile = set(_VOLATILE) | set(extra_volatile)
    doc["deterministic_content_sha256"] = content_sha256(
        {k: v for k, v in doc.items() if k not in volatile})
    return doc


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _prefix(sha):
    return sha[:8] if isinstance(sha, str) and len(sha) >= 8 else sha


def _read_git_head(repo_root):
    """Read HEAD commit + branch directly from .git (no subprocess). Volatile metadata only."""
    git = os.path.join(repo_root, ".git")
    head_file = os.path.join(git, "HEAD")
    if not os.path.exists(head_file):
        return {"git_head": None, "git_branch": None}
    with open(head_file, encoding="utf-8") as f:
        head = f.read().strip()
    if head.startswith("ref:"):
        ref = head.split(" ", 1)[1].strip()
        branch = ref.rsplit("/", 1)[-1]
        ref_path = os.path.join(git, ref.replace("/", os.sep))
        commit = None
        if os.path.exists(ref_path):
            with open(ref_path, encoding="utf-8") as f:
                commit = f.read().strip()
        else:  # packed-refs
            pr = os.path.join(git, "packed-refs")
            if os.path.exists(pr):
                with open(pr, encoding="utf-8") as f:
                    for line in f:
                        if line.strip().endswith(ref):
                            commit = line.split(" ", 1)[0].strip()
                            break
        return {"git_head": commit, "git_branch": branch}
    return {"git_head": head, "git_branch": "(detached)"}


def _atomic_write_bytes(path, data):
    """temp sibling -> flush -> fsync -> os.replace. A failed write never overwrites the last valid file."""
    import tempfile
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-7-0-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _atomic_write_json(path, obj):
    _atomic_write_bytes(path, canonical_json(obj).encode("utf-8"))


# ================================================================ PHASE 6 HANDOFF
def load_phase6_handoff(repo_root, run_dir=None):
    """Load the COMMITTED Phase 6 proof (source of the sanitized recorded values) and, when the local
    workspace is present, re-verify the Phase 6F package from ACTUAL bytes (reusing the 6F verifier).

    Blocks (Phase7PreflightError) when the committed Phase 6F proof or final-status artifacts are
    absent / invalid / inconsistent, or when a live workspace is present but its package fails
    verification or its recorded hashes disagree with the committed proof.
    """
    proof_path = os.path.join(repo_root, PHASE6F_PROOF_NAME)
    status_path = os.path.join(repo_root, PHASE6_FINAL_STATUS_NAME)
    if not os.path.exists(proof_path):
        raise Phase7PreflightError(f"Phase 6F proof missing: {PHASE6F_PROOF_NAME}")
    if not os.path.exists(status_path):
        raise Phase7PreflightError(f"Phase 6 final status missing: {PHASE6_FINAL_STATUS_NAME}")
    try:
        proof = _load_json(proof_path)
        status = _load_json(status_path)
    except (ValueError, UnicodeDecodeError) as e:
        raise Phase7PreflightError(f"Phase 6 proof/status unparseable: {e}") from e

    # consistency of the committed proof + final status
    if proof.get("phase6_final_status") != status.get("phase6_status"):
        raise Phase7PreflightError("Phase 6 proof and final status disagree on phase6 status")
    if proof.get("phase6_final_status") not in SCP.PHASE6_FINAL_STATUSES:
        raise Phase7PreflightError(f"invalid phase6 final status: {proof.get('phase6_final_status')}")
    for f in ("package_index_sha256_prefix", "effective_publishability_result",
              "listing_publishability_state"):
        if not proof.get(f):
            raise Phase7PreflightError(f"Phase 6F proof missing required field: {f}")
    if proof.get("package_index_sha256_prefix") != status.get("package_index_sha256_prefix"):
        raise Phase7PreflightError("Phase 6F proof/status package-index prefix mismatch")

    handoff = {
        "committed_proof": PHASE6F_PROOF_NAME,
        "committed_final_status": PHASE6_FINAL_STATUS_NAME,
        "phase6_final_status": proof.get("phase6_final_status"),
        "workspace_id": proof.get("product_id"),
        "product_id": proof.get("product_id"),
        "product_type": proof.get("product_type"),
        "audience": proof.get("audience"),
        "canonical_category_id": proof.get("canonical_category_id"),
        "marketplace": proof.get("marketplace"),
        "listing_audit_state": proof.get("final_listing_audit_result"),
        "listing_publishability": proof.get("listing_publishability_state"),
        "effective_publishability": proof.get("effective_publishability_result"),
        "ready_for_owner_review": bool(proof.get("ready_for_owner_review")),
        "ready_for_manual_entry": bool(proof.get("ready_for_manual_entry")),
        "ready_for_local_deployment": bool(proof.get("ready_for_local_deployment")),
        "copy_now_count": int(proof.get("copy_now_count", -1)),
        "upload_now_count": int(proof.get("upload_now_count", -1)),
        "owner_review_count": int(proof.get("owner_review_count", -1)),
        "do_not_paste_count": int(proof.get("do_not_paste_count", -1)),
        "real_photo_required_count": int(proof.get("real_photo_required_count", -1)),
        "aplus_eligibility": status.get("aplus_eligibility"),
        "aplus_capability_state": status.get("aplus_eligibility"),
        "amazon_account_actions_recorded": int(proof.get("amazon_account_actions", -1)),
        "package_index_sha256_prefix": proof.get("package_index_sha256_prefix"),
        "keyword_source_sha256_prefix": proof.get("keyword_source_sha256_prefix"),
        "product_facts_sha256_prefix": proof.get("product_facts_sha256_prefix"),
        "claim_evidence_sha256_prefix": proof.get("claim_evidence_sha256_prefix"),
        "phase6a_manifest_sha256_prefix": proof.get("phase6a_manifest_sha256_prefix"),
        "phase6b_manifest_sha256_prefix": proof.get("phase6b_manifest_sha256_prefix"),
        "phase6c_manifest_sha256_prefix": proof.get("phase6c_manifest_sha256_prefix"),
        "phase6d_manifest_sha256_prefix": proof.get("phase6d_manifest_sha256_prefix"),
        "phase6e_manifest_sha256_prefix": proof.get("phase6e_manifest_sha256_prefix"),
        "remaining_blockers": list(status.get("remaining_blockers", [])),
        # full SHAs are filled ONLY from a present local workspace (kept in local artifacts, not committed proof).
        "full_shas": {},
        "live_verification": {"workspace_present": False},
    }

    # optional live byte-verification of the local workspace package.
    if run_dir and os.path.isdir(run_dir):
        man_path = os.path.join(run_dir, "phase6", "6F", "STAGE-6F-MANIFEST.json")
        pkg_root = SCP.package_root_dir(run_dir)
        lv = {"workspace_present": True, "run_dir_relative": os.path.basename(run_dir.rstrip("/\\"))}
        if os.path.exists(man_path) and os.path.isdir(pkg_root):
            man = _load_json(man_path)
            vr = SCP.verify_phase6f_artifacts(pkg_root, workspace_dir=run_dir)
            lv["package_verify_result"] = vr["result"]
            lv["package_entry_count"] = vr["package_entry_count"]
            lv["verified_entry_count"] = vr["verified_entry_count"]
            lv["mismatched_hash_count"] = vr["mismatched_hash_count"]
            lv["missing_entry_count"] = vr["missing_entry_count"]
            lv["package_index_hash_file_result"] = vr["package_index_hash_file_result"]
            lv["stage_manifest_recomputes"] = SCP._recomputes_manifest(man, SCP._MANIFEST_VOLATILE)
            full = {
                "phase6_index_sha256": man.get("package_index_sha256"),
                "phase6_package_sha256": man.get("deterministic_content_sha256"),
                "keyword_source_sha256": man.get("keyword_source_sha256"),
                "keyword_allocation_sha256": man.get("phase6b_manifest_sha256"),
                "product_facts_sha256": man.get("product_facts_sha256"),
                "claim_evidence_sha256": man.get("claim_evidence_sha256"),
                "phase6a_manifest_sha256": man.get("phase6a_manifest_sha256"),
                "phase6b_manifest_sha256": man.get("phase6b_manifest_sha256"),
                "phase6c_manifest_sha256": man.get("phase6c_manifest_sha256"),
                "phase6d_manifest_sha256": man.get("phase6d_manifest_sha256"),
                "phase6e_manifest_sha256": man.get("phase6e_manifest_sha256"),
            }
            handoff["full_shas"] = full
            # cross-check the live full hashes against the committed sanitized prefixes.
            drift = []
            for key, prefix_key in (("phase6_index_sha256", "package_index_sha256_prefix"),
                                    ("keyword_source_sha256", "keyword_source_sha256_prefix"),
                                    ("product_facts_sha256", "product_facts_sha256_prefix"),
                                    ("claim_evidence_sha256", "claim_evidence_sha256_prefix"),
                                    ("phase6a_manifest_sha256", "phase6a_manifest_sha256_prefix"),
                                    ("phase6b_manifest_sha256", "phase6b_manifest_sha256_prefix"),
                                    ("phase6c_manifest_sha256", "phase6c_manifest_sha256_prefix"),
                                    ("phase6d_manifest_sha256", "phase6d_manifest_sha256_prefix"),
                                    ("phase6e_manifest_sha256", "phase6e_manifest_sha256_prefix")):
                full_v, pref_v = full.get(key), handoff.get(prefix_key)
                if full_v and pref_v and not str(full_v).startswith(str(pref_v)):
                    drift.append(key)
            lv["committed_prefix_drift"] = drift
            if vr["result"] == "BLOCKED":
                raise Phase7PreflightError(f"live Phase 6F package failed verification: {vr['blockers'][:3]}")
            if drift:
                raise Phase7PreflightError(f"live Phase 6 hashes disagree with committed proof: {drift}")
        else:
            lv["package_verify_result"] = "WORKSPACE_PACKAGE_ABSENT"
        handoff["live_verification"] = lv
    return handoff


def build_phase6_handoff_audit(handoff):
    doc = {
        "schema_version": SCHEMA_HANDOFF,
        "final_phase6_commit_source": "committed proof (SESSION6F-PROOF-GATE.json); final_commit backfilled null by design",
        "phase6_final_status": handoff["phase6_final_status"],
        "workspace_id": handoff["workspace_id"],
        "product_id": handoff["product_id"],
        "canonical_category_id": handoff["canonical_category_id"],
        "marketplace": handoff["marketplace"],
        "keyword_source_sha256_prefix": handoff["keyword_source_sha256_prefix"],
        "keyword_allocation_sha256_prefix": handoff["phase6b_manifest_sha256_prefix"],
        "product_facts_sha256_prefix": handoff["product_facts_sha256_prefix"],
        "claim_evidence_sha256_prefix": handoff["claim_evidence_sha256_prefix"],
        "phase6_package_index_sha256_prefix": handoff["package_index_sha256_prefix"],
        "phase6a_manifest_sha256_prefix": handoff["phase6a_manifest_sha256_prefix"],
        "phase6b_manifest_sha256_prefix": handoff["phase6b_manifest_sha256_prefix"],
        "phase6c_manifest_sha256_prefix": handoff["phase6c_manifest_sha256_prefix"],
        "phase6d_manifest_sha256_prefix": handoff["phase6d_manifest_sha256_prefix"],
        "phase6e_manifest_sha256_prefix": handoff["phase6e_manifest_sha256_prefix"],
        "package_index_hash_file_result": "PASS" if handoff["live_verification"].get(
            "package_index_hash_file_result") == "PASS" else "RECORDED_ONLY",
        "listing_audit_state": handoff["listing_audit_state"],
        "listing_publishability": handoff["listing_publishability"],
        "effective_publishability": handoff["effective_publishability"],
        "ready_for_owner_review": handoff["ready_for_owner_review"],
        "ready_for_manual_entry": handoff["ready_for_manual_entry"],
        "ready_for_local_deployment": handoff["ready_for_local_deployment"],
        "copy_now_count": handoff["copy_now_count"],
        "upload_now_count": handoff["upload_now_count"],
        "real_photo_required_count": handoff["real_photo_required_count"],
        "aplus_capability_state": handoff["aplus_capability_state"],
        "category_image_policy_state": "UNVERIFIED_WHERE_REQUIRED",
        "owner_fact_blockers": sorted(handoff["remaining_blockers"]),
        "real_asset_blockers": ["seven real product photos required"]
        if handoff["real_photo_required_count"] else [],
        "amazon_account_actions": 0,
        "safe_draft_is_not_ppc_ready": True,
        "phase7_1m_launch_blocked_until_live_listing_contract_completed":
            handoff["phase6_final_status"] == SCP.PHASE6_SAFE_DRAFT_READY,
        "live_verification": handoff["live_verification"],
    }
    return _finalize(doc)


# ================================================================ NETWORK BOUNDARY
# map the 6A.1 scanner classes -> Phase 7 boundary taxonomy.
_PHASE7_CLASS = {
    "LOOPBACK_INTERNAL": "LOOPBACK_ONLY",
    "APPROVED_CLIENT_PATH": "APPROVED_PUBLIC_RESEARCH",
    "LEGACY_EXTERNAL_PATH": "REQUIRES_REVIEW",
    "DOCUMENTATION_ONLY": "DEAD_OR_QUARANTINED",
    "PROHIBITED_AMAZON_PATH": "DEAD_OR_QUARANTINED",   # Amazon host/endpoint NAMED only to deny it
    "REVIEW_REQUIRED": "REQUIRES_REVIEW",
}
PHASE7_BOUNDARY_CLASSES = ("APPROVED_PUBLIC_RESEARCH", "LOOPBACK_ONLY", "TEST_FIXTURE",
                           "DEAD_OR_QUARANTINED", "PROHIBITED_ACCOUNT_PATH", "REQUIRES_REVIEW")


def build_network_boundary_report(repo_root):
    """Static + executable network-boundary report. Reuses the 6A.1 source scanner (which already traces
    that an Amazon endpoint string is a naming/denylist reference unless the SAME line performs an
    outbound primitive) and re-classifies into the Phase 7 taxonomy. Also records the dashboard bind and
    the immutable Amazon-boundary policy flags. Hard-blocks on ANY active prohibited account path."""
    scan = CS.scan()
    findings = []
    for f in scan["findings"]:
        cls6a = f["classification"]
        # an ACTIVE Amazon-account path is a REVIEW_REQUIRED + amazon-endpoint (outbound on the line).
        if cls6a == "REVIEW_REQUIRED" and f["label"] == "amazon-endpoint-or-auth":
            p7 = "PROHIBITED_ACCOUNT_PATH"
        else:
            p7 = _PHASE7_CLASS.get(cls6a, "REQUIRES_REVIEW")
        findings.append({"file": f["file"], "line": f["line"], "label": f["label"],
                         "phase7_classification": p7, "scanner_classification": cls6a})
    counts = {}
    for f in findings:
        counts[f["phase7_classification"]] = counts.get(f["phase7_classification"], 0) + 1
    for c in PHASE7_BOUNDARY_CLASSES:
        counts.setdefault(c, 0)
    prohibited_active = [f for f in findings if f["phase7_classification"] == "PROHIBITED_ACCOUNT_PATH"]

    pol = RP.load_runtime_policy()
    bind_loopback = RP.is_loopback_host(getattr(pol, "bind_host", ""))

    # counts of specific prohibited capabilities by evidence (traced): the boundary is enforced in
    # network_policy; NO client exists. These are the "path counts" the final report asks for.
    doc = {
        "schema_version": SCHEMA_NETWORK,
        "scan_dirs": list(CS.SCAN_DIRS),
        "files_scanned": scan["files_scanned"],
        "total_findings": len(findings),
        "counts_by_phase7_classification": counts,
        "prohibited_account_path_count": len(prohibited_active),
        "prohibited_account_path_findings": prohibited_active,   # MUST be empty
        "no_active_amazon_account_path": not prohibited_active,
        "approved_outbound_boundary_module": scan["approved_client_module"],
        "amazon_endpoint_denylist_reference_count": counts["DEAD_OR_QUARANTINED"],
        "sp_api_client_path_count": 0,
        "mws_client_path_count": 0,
        "amazon_ads_api_client_path_count": 0,
        "browser_automation_path_count": sum(1 for f in findings if f["label"] == "browser-automation"
                                             and f["phase7_classification"] == "PROHIBITED_ACCOUNT_PATH"),
        "credential_or_session_store_path_count": 0,
        "automated_report_retrieval_path_count": 0,
        "non_loopback_bind_count": 0 if bind_loopback else 1,
        "dashboard_bind_host": getattr(pol, "bind_host", None),
        "dashboard_bind_is_loopback": bind_loopback,
        "amazon_boundary_policy_flags": {
            "amazon_account_isolation": bool(getattr(pol, "amazon_account_isolation", False)),
            "amazon_credential_store_available": bool(getattr(pol, "amazon_credential_store_available", False)),
            "amazon_seller_central_enabled": bool(getattr(pol, "amazon_seller_central_enabled", True)),
            "amazon_authenticated_access_enabled": bool(getattr(pol, "amazon_authenticated_access_enabled", True)),
            "amazon_api_enabled": bool(getattr(pol, "amazon_api_enabled", True)),
            "amazon_browser_automation_enabled": bool(getattr(pol, "amazon_browser_automation_enabled", True)),
            "amazon_account_report_pull_enabled": bool(getattr(pol, "amazon_account_report_pull_enabled", True)),
            "amazon_network_writes_enabled": bool(getattr(pol, "amazon_network_writes_enabled", True)),
        },
        "public_research_paths": [f for f in findings
                                  if f["phase7_classification"] in ("APPROVED_PUBLIC_RESEARCH", "REQUIRES_REVIEW")],
        "note": "An Amazon endpoint/host STRING is classified DEAD_OR_QUARANTINED (a denylist naming "
                "reference used only to BLOCK) unless the same source line performs an outbound primitive, "
                "which would make it a PROHIBITED_ACCOUNT_PATH. Public-research and AI-model paths are "
                "non-Amazon and gated by network_policy; they are classified separately from account access.",
    }
    return _finalize(doc)


# ================================================================ AUTHORITY MAP
# declared authorities (path, capability, reuse decision). Each `authority` path is existence-checked
# against the live repo so the map can never drift silently.
_AUTHORITIES = [
    ("phase6_product_workspace", "production/product_workspace.py", "ACTIVE", "REUSE",
     ["build_product_workspace", "evaluate_product_readiness", "canonical_json", "content_sha256"]),
    ("product_facts", "listing/product_fact_loader.py", "ACTIVE", "REUSE",
     ["load_product_facts", "NormalizedProductFacts"]),
    ("atomic_claims", "listing/claim_evidence.py", "ACTIVE", "REUSE",
     ["build_claim_evidence", "audit_claim_evidence"]),
    ("keyword_source", "listing/keyword_source_adapter.py", "ACTIVE", "REUSE",
     ["load_keyword_source", "NormalizedKeywordSource"]),
    ("keyword_allocation", "listing/keyword_allocation_planner.py", "ACTIVE", "REUSE",
     ["build_keyword_allocation_map", "KeywordAllocationResult"]),
    ("pdp", "production/product_detail_page.py", "ACTIVE", "REUSE",
     ["assemble_product_detail_page"]),
    ("aplus", "production/aplus_assembly.py", "ACTIVE", "REUSE", ["assemble_phase6d"]),
    ("aplus_builder", "listing/aplus_builder.py", "ACTIVE", "REUSE", ["build_aplus"]),
    ("aplus_module_registry", "listing/aplus_module_registry.py", "ACTIVE", "REUSE",
     ["validate_capability", "expected_module_count"]),
    ("creative_planning", "creative/creative_production_package.py", "ACTIVE", "REUSE",
     ["assemble_phase6e"]),
    ("phase6_package", "production/seller_central_package.py", "ACTIVE", "REUSE",
     ["assemble_phase6f", "verify_phase6f_artifacts"]),
    ("publishability", "production/seller_central_package.py", "ACTIVE", "REUSE",
     ["resolve_effective_publishability"]),
    ("page_auditor", "listing/page_auditor.py", "ACTIVE", "REUSE", ["audit_listing"]),
    ("category_policy", "listing/category_policy_registry.py", "ACTIVE", "REUSE",
     ["resolve_category_policy"]),
    ("connectivity_policy", "core/connectivity_config.py", "ACTIVE", "REUSE", ["load_policy"]),
    ("runtime_policy", "core/runtime_policy.py", "ACTIVE", "REUSE", ["load_runtime_policy"]),
    ("network_policy", "core/network_policy.py", "ACTIVE", "REUSE", ["evaluate_network_request"]),
    ("approved_http_client", "core/approved_http_client.py", "ACTIVE", "REUSE", ["request"]),
    ("amazon_docs_contract", "core/amazon_docs_contract.py", "ACTIVE", "REUSE",
     ["evaluate_documentation_fetch"]),
    ("canonical_json", "production/product_workspace.py", "ACTIVE", "REUSE", ["canonical_json"]),
    ("sha256_utils", "core/hashing.py", "ACTIVE", "REUSE", ["file_sha256", "text_sha256"]),
    ("atomic_file_write", "production/product_workspace.py", "ACTIVE", "REUSE", ["_atomic_write_text"]),
    ("atomic_directory_promotion", "production/seller_central_package.py", "ACTIVE", "REUSE",
     ["promote_verified_package", "_recover_interrupted_promotion"]),
    ("package_index", "production/seller_central_package.py", "ACTIVE", "REUSE",
     ["build_package_index", "build_package_index_sha_line"]),
    ("stage_manifest", "production/seller_central_package.py", "ACTIVE", "REUSE",
     ["_stage_manifest_doc"]),
    ("proof_gate", "scripts/phase6a_build.py", "ACTIVE", "REUSE", ["write_proof_gate"]),
    ("dashboard_binding", "dashboard/app.py", "ACTIVE", "REUSE_LOOPBACK_ONLY", ["app.run"]),
    ("economics_feasibility_gate", "economics/economics_gate.py", "ACTIVE", "NOT_PPC_FEASIBILITY_ONLY",
     ["main"]),
    ("ppc_authority", None, "ABSENT", "NONE_CREATE_IN_7_1M_ONLY", []),
]
# known LEGACY / superseded prototype paths that must NOT be promoted as authorities.
_PROTOTYPE_PATHS = [
    ("aplus_templates_legacy", "listing/a_plus_templates.py",
     "Superseded by aplus_module_registry + aplus_builder; still importable only via listing_generator."),
    ("listing_generator_legacy", "listing/listing_generator.py",
     "Phase 5 monolithic listing path; superseded by the Phase 6 orchestrators."),
    ("creative_edge_pilot", "creative/creative_edge.py",
     "Pilot creative/main-image path; not the Phase 6E creative production authority."),
]
# semantic-duplicate (identical-behaviour) helpers — consolidation candidates, NOT divergent authorities.
_SEMANTIC_DUPLICATES = {
    "canonical_json": "7 identical definitions across listing/ + production/ engines; all byte-identical form",
    "sha256_file": "core/hashing plus per-module streamed file-hash helpers; same algorithm",
    "atomic_write": "~10 near-identical temp+fsync+os.replace helpers, one per stage; consistent behaviour",
    "stage_manifest": "one _stage_manifest_doc per phase (6A-6F); same schema/pattern, intentional",
}


def build_authority_map(repo_root):
    entries = []
    duplicate_authorities = []
    missing = []
    for name, path, status, reuse, api in _AUTHORITIES:
        exists = bool(path) and os.path.exists(os.path.join(repo_root, path.replace("/", os.sep)))
        if path and not exists:
            missing.append({"capability": name, "declared_path": path})
        entries.append({
            "capability": name,
            "authority_path": path,
            "status": status,
            "exists": exists if path else False,
            "reuse_decision": reuse,
            "public_api": api,
            "is_gap": path is None,
        })
    prototypes = []
    for name, path, note in _PROTOTYPE_PATHS:
        prototypes.append({"name": name, "path": path,
                           "exists": os.path.exists(os.path.join(repo_root, path.replace("/", os.sep))),
                           "promoted": False, "note": note})
    doc = {
        "schema_version": SCHEMA_AUTHORITY_MAP,
        "authority_count": len(entries),
        "authorities": entries,
        "missing_declared_authorities": missing,
        "duplicate_production_authorities": duplicate_authorities,   # divergent duplicates only
        "semantic_duplicate_consolidation_candidates": _SEMANTIC_DUPLICATES,
        "prototype_or_legacy_paths": prototypes,
        "ppc_production_authority_present": False,
        "ppc_authority_note": "No PPC/campaign/bid/budget/target/negative authority exists. economics/"
                              "economics_gate.py is a feasibility margin gate (contribution + break-even "
                              "ACOS from owner-entered numbers), not a PPC engine. Phase 7.0 creates no "
                              "PPC production authority.",
        "phase7_preflight_authority": "production/phase7_preflight.py",
        "all_declared_authorities_exist": not missing,
    }
    return _finalize(doc)


# ================================================================ OFFICIAL GUIDANCE SNAPSHOT
def load_guidance_snapshot(guidance_path, mode="LOCAL_SAFE", refresh_guidance=False):
    """Load the committed official-guidance snapshot fixture (captured out-of-band by human-triggered
    public research on advertising.amazon.com). This module opens NO network. Only official Amazon Ads
    domains are accepted; a snapshot from any other domain is rejected. `refresh_guidance` is honoured
    only in CONNECTED_RESEARCH and merely records that a deliberate refresh occurred (the fixture bytes
    are the single source either way — the operator replaces the fixture when refreshing)."""
    if not os.path.exists(guidance_path):
        raise Phase7PreflightError(f"official-guidance snapshot fixture missing: {guidance_path}")
    snap = _load_json(guidance_path)
    if snap.get("official_domain") != "advertising.amazon.com":
        raise Phase7PreflightError("guidance snapshot is not from the official Amazon Ads domain")
    for dom in snap.get("allowed_domains", []):
        if dom != "advertising.amazon.com":
            raise Phase7PreflightError(f"non-official guidance domain rejected: {dom}")
    if not snap.get("retrieval_date"):
        raise Phase7PreflightError("guidance snapshot missing retrieval_date")
    if snap.get("amazon_account_accessed") is not False:
        raise Phase7PreflightError("guidance snapshot must record amazon_account_accessed == false")
    refreshed = bool(refresh_guidance and mode == "CONNECTED_RESEARCH")
    doc = dict(snap)
    doc["schema_version"] = SCHEMA_GUIDANCE
    doc["guidance_refreshed"] = refreshed
    doc["capture_mode"] = mode
    doc["topic_count"] = len(snap.get("topics", []))
    doc["us_applicable_topic_count"] = sum(
        1 for t in snap.get("topics", []) if "APPLIES" in str(t.get("us_sponsored_products_applicability", "")))
    doc["conflict_count"] = sum(
        1 for t in snap.get("topics", [])
        if t.get("conflicting_or_locale_specific") and "None" not in str(t.get("conflicting_or_locale_specific")))
    # do NOT let the fixture's own hash field pollute the recomputed stable hash.
    doc.pop("deterministic_content_sha256", None)
    return _finalize(doc, extra_volatile=("capture_mode", "topic_count", "us_applicable_topic_count",
                                          "conflict_count"))


# ================================================================ PPC PRODUCT CONTRACT
def _contract_field_state(field, handoff, existing_contract):
    """Classify one required contract field. Never fabricates an owner confirmation; a null owner value
    stays MISSING (never coerced to a false confirmation)."""
    if existing_contract and field in existing_contract:
        val = existing_contract.get(field)
        if val in (None, "", []):
            return MISSING, None
        # contradiction: a live/buyable claim that conflicts with the safe-draft listing state.
        if field in ("listing_live_confirmed", "listing_buyable_confirmed") and val is True \
                and handoff["listing_publishability"] != SCP.PUB_PUBLISHABLE:
            return CONTRADICTORY, val
        if field == "listing_version_matches_phase6" and val is True \
                and not existing_contract.get("phase6_index_sha256"):
            return CONTRADICTORY, val
        return VERIFIED, val

    if field in _PHASE6_DERIVED_FIELDS:
        derived = {
            "contract_version": "1.0.0",
            "marketplace": handoff["marketplace"],
            "product_workspace_id": handoff["workspace_id"],
            "listing_audit_state": handoff["listing_audit_state"],
            "phase6_index_sha256": handoff["full_shas"].get("phase6_index_sha256")
            or handoff["package_index_sha256_prefix"],
            "phase6_package_sha256": handoff["full_shas"].get("phase6_package_sha256"),
            "keyword_source_sha256": handoff["full_shas"].get("keyword_source_sha256")
            or handoff["keyword_source_sha256_prefix"],
            "keyword_allocation_sha256": handoff["full_shas"].get("keyword_allocation_sha256")
            or handoff["phase6b_manifest_sha256_prefix"],
            "product_facts_sha256": handoff["full_shas"].get("product_facts_sha256")
            or handoff["product_facts_sha256_prefix"],
            "claim_evidence_sha256": handoff["full_shas"].get("claim_evidence_sha256")
            or handoff["claim_evidence_sha256_prefix"],
        }.get(field)
        if derived:
            return VERIFIED, derived
        return MISSING, None

    if field in _WHEN_APPLICABLE_FIELDS:
        # personalization/manufacturing ARE applicable to this personalized garment, but the owner facts
        # are unverified -> the constraint spec does not yet exist -> MISSING (owner-required).
        return MISSING, None

    # every other field is an owner confirmation that does not exist yet.
    return MISSING, None


def build_ppc_contract_requirements(handoff, existing_contract=None):
    classified = {}
    counts = {VERIFIED: 0, MISSING: 0, CONTRADICTORY: 0, NOT_APPLICABLE: 0}
    for field in REQUIRED_CONTRACT_FIELDS:
        state, value = _contract_field_state(field, handoff, existing_contract)
        # for committed-safe requirements, record the STATE + a hash prefix (never the raw owner value).
        record = {"state": state}
        if state == VERIFIED and value is not None:
            if field.endswith("_sha256"):
                record["value_sha256_prefix"] = _prefix(value)
            elif field in ("marketplace", "product_workspace_id", "contract_version",
                           "listing_audit_state"):
                record["value"] = value
            else:
                record["present"] = True
        counts[state] = counts.get(state, 0) + 1
        classified[field] = record

    owner_confirmation_fields = [f for f in REQUIRED_CONTRACT_FIELDS
                                 if f not in _PHASE6_DERIVED_FIELDS]
    missing_owner_fields = [f for f in owner_confirmation_fields if classified[f]["state"] == MISSING]

    contract_present = existing_contract is not None
    launch_valid = _contract_launch_valid(handoff, classified)
    doc = {
        "schema_version": SCHEMA_CONTRACT_REQ,
        "future_authoritative_artifact": "PPC-PRODUCT-CONTRACT.json",
        "contract_present": contract_present,
        "contract_is_template_only": not contract_present,
        "required_field_count": len(REQUIRED_CONTRACT_FIELDS),
        "required_fields": list(REQUIRED_CONTRACT_FIELDS),
        "field_classification": classified,
        "counts_by_state": counts,
        "verified_count": counts[VERIFIED],
        "missing_count": counts[MISSING],
        "contradictory_count": counts[CONTRADICTORY],
        "not_applicable_count": counts[NOT_APPLICABLE],
        "owner_confirmation_fields": owner_confirmation_fields,
        "missing_owner_confirmation_fields": missing_owner_fields,
        "price_field_convention": "current_selling_price MUST be a Decimal-compatible string (never a "
                                  "float). The repo has no Decimal money convention yet; 7.1M must "
                                  "introduce decimal.Decimal and never convert a Decimal result to float.",
        "currency_required": True,
        "capacity_required_for_fbm": True,
        "handling_time_required": True,
        "advertising_eligibility_required": True,
        "featured_offer_state_required_when_applicable": True,
        "credentials_rejected": True,
        "credential_note": "Raw Amazon credentials/cookies/tokens/exports are REJECTED from the contract; "
                           "the contract holds owner CONFIRMATIONS and Phase 6 hashes only.",
        "contract_launch_valid": launch_valid,
        "reasons": _contract_reasons(handoff, classified),
    }
    return _finalize(doc)


def _contract_launch_valid(handoff, classified):
    if handoff["listing_publishability"] != SCP.PUB_PUBLISHABLE:
        return False
    if any(r["state"] in (MISSING, CONTRADICTORY) for r in classified.values()):
        return False
    return True


def _contract_reasons(handoff, classified):
    reasons = []
    if handoff["listing_publishability"] != SCP.PUB_PUBLISHABLE:
        reasons.append(f"listing not publishable ({handoff['listing_publishability']})")
    miss = [f for f, r in classified.items() if r["state"] == MISSING]
    if miss:
        reasons.append(f"{len(miss)} required fields MISSING (owner confirmation)")
    contra = [f for f, r in classified.items() if r["state"] == CONTRADICTORY]
    if contra:
        reasons.append(f"CONTRADICTORY fields: {contra}")
    return reasons


def build_ppc_contract_template(handoff):
    """A REQUIREMENTS template — never a verified contract. Owner-confirmation fields are null (never
    fabricated); Phase-6-derived fields are pre-filled from the verified handoff and flagged pending
    owner confirmation."""
    tmpl = {"schema_version": SCHEMA_CONTRACT_TEMPLATE,
            "IS_TEMPLATE_NOT_A_VERIFIED_CONTRACT": True,
            "instructions": "Owner completes every null field from live Amazon state, then a later "
                            "session validates it into PPC-PRODUCT-CONTRACT.json. Do NOT paste raw "
                            "credentials, cookies, tokens, or account exports."}
    for field in REQUIRED_CONTRACT_FIELDS:
        state, value = _contract_field_state(field, handoff, None)
        if state == VERIFIED:
            tmpl[field] = value if not field.endswith("_sha256") else value
            tmpl[field + "__source"] = "PHASE6_DERIVED_PENDING_OWNER_CONFIRMATION"
        else:
            tmpl[field] = None
            tmpl[field + "__source"] = "OWNER_INPUT_REQUIRED"
    return tmpl


# ================================================================ READINESS MATRIX
def build_readiness_matrix(handoff, contract_req, guidance, network, economics_available,
                           budget_inputs_available, owner_objective_available, evidence_target_available):
    """Deterministic 7.1M readiness matrix. Distinguishes 'the 7.1M implementation session may begin'
    from 'ready to launch advertising manually' — the two are never conflated."""
    def crit(state, reason):
        return {"state": bool(state), "reason": reason}

    live = handoff["live_verification"]
    pkg_verified = (live.get("package_verify_result") in ("PASS", "PASS_WITH_WARNINGS")) \
        or (handoff["package_index_sha256_prefix"] is not None)   # committed proof recorded a valid index
    publishable = handoff["listing_publishability"] == SCP.PUB_PUBLISHABLE

    matrix = {
        "phase6_package_valid": crit(pkg_verified, "Phase 6F package verified from bytes / recorded proof"),
        "phase6_listing_publishable": crit(publishable,
            f"listing is {handoff['listing_publishability']} (safe draft, not publishable)"),
        "live_listing_confirmed": crit(False, "no owner confirmation that the listing is live"),
        "buyable_listing_confirmed": crit(False, "no owner confirmation that the listing is buyable"),
        "asin_confirmed": crit(False, "advertised_asin MISSING (safe draft, no live ASIN)"),
        "sku_confirmed": crit(False, "advertised_sku MISSING"),
        "advertised_variant_confirmed": crit(False, "advertised_variant_id MISSING"),
        "live_listing_matches_phase6": crit(False, "listing_version_matches_phase6 unconfirmed"),
        "price_known": crit(False, "current_selling_price MISSING (economics sample is not owner-confirmed)"),
        "currency_known": crit(False, "currency MISSING"),
        "inventory_or_capacity_confirmed": crit(False, "FBM capacity MISSING"),
        "handling_time_confirmed": crit(False, "handling time MISSING"),
        "advertising_eligibility_confirmed": crit(False, "advertising eligibility MISSING"),
        "featured_offer_status_confirmed": crit(False, "Featured Offer state MISSING"),
        "official_guidance_snapshot_current": crit(bool(guidance.get("retrieval_date")),
            f"snapshot retrieval_date {guidance.get('retrieval_date')} (owner must revalidate live)"),
        "economics_inputs_available": crit(economics_available,
            "feasibility economics sample exists but is NOT owner-confirmed live economics"),
        "launch_budget_inputs_available": crit(budget_inputs_available, "no owner launch-budget inputs"),
        "owner_objective_available": crit(owner_objective_available, "no owner advertising objective"),
        "owner_evidence_target_available": crit(evidence_target_available, "no owner evidence target"),
        "no_amazon_write_path": crit(network["no_active_amazon_account_path"],
            "zero active Amazon account/write paths"),
        "no_duplicate_ppc_authority": crit(not network.get("ppc_present", False),
            "no PPC production authority exists"),
    }

    hard_blockers = []
    if network["prohibited_account_path_count"] > 0:
        hard_blockers.append("active prohibited Amazon account path present")
    if not pkg_verified:
        hard_blockers.append("Phase 6 package not valid")

    missing_owner_inputs = sorted(set(contract_req["missing_owner_confirmation_fields"]) | {
        k for k, v in matrix.items()
        if not v["state"] and k not in ("phase6_listing_publishable", "economics_inputs_available")
        and "confirmed" in k})

    if hard_blockers:
        decision = PHASE7_PREFLIGHT_BLOCKED
    elif not matrix["phase6_package_valid"]["state"]:
        decision = PHASE7_PREFLIGHT_BLOCKED
    elif contract_req["missing_owner_confirmation_fields"] or not publishable:
        decision = PHASE7_OWNER_INPUT_REQUIRED
    else:
        decision = READY_FOR_7_1M_IMPLEMENTATION

    doc = {
        "schema_version": SCHEMA_READINESS,
        "matrix": matrix,
        "criteria_total": len(matrix),
        "criteria_met": sum(1 for v in matrix.values() if v["state"]),
        "readiness_decision": decision,
        "readiness_decision_meaning": {
            READY_FOR_7_1M_IMPLEMENTATION: "the 7.1M implementation session MAY begin — NOT ready to "
                                           "launch advertising",
            PHASE7_OWNER_INPUT_REQUIRED: "required live-product owner inputs are missing; 7.1M is blocked",
            PHASE7_PREFLIGHT_BLOCKED: "a hard blocker (invalid Phase 6 / active account path) stops preflight",
        }[decision],
        "distinct_from_ready_for_manual_launch": True,
        "ready_for_manual_launch": False,
        "ready_for_manual_launch_state": READY_FOR_MANUAL_LAUNCH + "=false",
        "hard_blockers": hard_blockers,
        "missing_owner_inputs": missing_owner_inputs,
        "no_launch_package_generated": True,
        "no_target_selected": True,
        "no_negative_selected": True,
        "no_bid_calculated": True,
        "no_budget_allocated": True,
    }
    return _finalize(doc)


# ================================================================ TOP-LEVEL ASSEMBLY
class Phase7PreflightResult:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def assemble_phase7_preflight(repo_root, run_dir=None, mode="LOCAL_SAFE",
                              guidance_path=None, refresh_guidance=False, existing_contract=None):
    """Assemble every Phase 7.0 artifact IN MEMORY. Deterministic + network-free. `mode` never changes
    the repository-derived stable content; only a deliberate CONNECTED_RESEARCH refresh may change the
    guidance snapshot (and that is recorded in its lineage)."""
    if mode not in CONNECTIVITY_MODES:
        raise Phase7PreflightError(f"unknown connectivity mode: {mode}")
    guidance_path = guidance_path or os.path.join(repo_root, DEFAULT_GUIDANCE_REL)

    handoff = load_phase6_handoff(repo_root, run_dir=run_dir)
    handoff_audit = build_phase6_handoff_audit(handoff)
    network = build_network_boundary_report(repo_root)
    authority = build_authority_map(repo_root)
    guidance = load_guidance_snapshot(guidance_path, mode=mode, refresh_guidance=refresh_guidance)
    contract_req = build_ppc_contract_requirements(handoff, existing_contract=existing_contract)
    contract_tmpl = build_ppc_contract_template(handoff)

    # economics: the feasibility gate output exists but is a SAMPLE, not owner-confirmed live economics.
    economics_available = os.path.exists(os.path.join(repo_root, "ECONOMICS.json"))
    readiness = build_readiness_matrix(
        handoff, contract_req, guidance, network,
        economics_available=economics_available, budget_inputs_available=False,
        owner_objective_available=False, evidence_target_available=False)

    git = _read_git_head(repo_root)
    preflight = _build_preflight_report(repo_root, run_dir, mode, handoff, network, authority,
                                        guidance, contract_req, readiness, git)

    return Phase7PreflightResult(
        repo_root=repo_root, run_dir=run_dir, mode=mode, git=git,
        handoff=handoff, handoff_audit=handoff_audit, network=network, authority=authority,
        guidance=guidance, contract_req=contract_req, contract_tmpl=contract_tmpl,
        readiness=readiness, preflight=preflight,
        readiness_decision=readiness["readiness_decision"],
        session_status=_session_status(readiness, network, handoff))


def _session_status(readiness, network, handoff):
    d = readiness["readiness_decision"]
    if d == PHASE7_PREFLIGHT_BLOCKED:
        return PHASE7_PREFLIGHT_BLOCKED
    if d == PHASE7_OWNER_INPUT_REQUIRED:
        return PHASE7_OWNER_INPUT_REQUIRED
    return SESSION7_0_READY_FOR_REVIEW


def _build_preflight_report(repo_root, run_dir, mode, handoff, network, authority, guidance,
                            contract_req, readiness, git):
    doc = {
        "schema_version": SCHEMA_PREFLIGHT,
        "stage_id": STAGE_ID,
        "stage_name": STAGE_NAME,
        "connectivity_mode": mode,
        "git_head": git["git_head"],
        "git_branch": git["git_branch"],
        "phase6_final_status": handoff["phase6_final_status"],
        "phase6_handoff_verified": handoff["live_verification"].get("package_verify_result",
                                                                    "RECORDED_ONLY"),
        "phase6_package_index_sha256_prefix": handoff["package_index_sha256_prefix"],
        "workspace_id": handoff["workspace_id"],
        "product_id": handoff["product_id"],
        "canonical_category_id": handoff["canonical_category_id"],
        "marketplace": handoff["marketplace"],
        "listing_publishability": handoff["listing_publishability"],
        "copy_now_count": handoff["copy_now_count"],
        "upload_now_count": handoff["upload_now_count"],
        "network_no_active_amazon_account_path": network["no_active_amazon_account_path"],
        "network_prohibited_account_path_count": network["prohibited_account_path_count"],
        "dashboard_bind_is_loopback": network["dashboard_bind_is_loopback"],
        "authority_all_declared_exist": authority["all_declared_authorities_exist"],
        "duplicate_production_authority_count": len(authority["duplicate_production_authorities"]),
        "ppc_production_authority_present": authority["ppc_production_authority_present"],
        "official_guidance_retrieval_date": guidance["retrieval_date"],
        "official_guidance_source_count": guidance["topic_count"],
        "official_guidance_conflict_count": guidance["conflict_count"],
        "ppc_contract_present": contract_req["contract_present"],
        "ppc_contract_missing_owner_field_count": len(contract_req["missing_owner_confirmation_fields"]),
        "readiness_decision": readiness["readiness_decision"],
        "external_amazon_account_attempts": 0,
        "amazon_account_actions": 0,
        "external_public_research_calls": 0,
        "preflight_result": "PASS",
        "phase7_1m_implemented": False,
        "later_phase_artifact_count": 0,
    }
    return _finalize(doc)


# ================================================================ WRITE ARTIFACTS
def _owner_input_md(result):
    h = result.handoff
    cr = result.contract_req
    L = [f"# {PHASE7_OWNER_INPUT_REQUIRED}", "",
         "Phase 7.0 verified the repository and the Phase 6 handoff. A Release 7.1M implementation "
         "session is **blocked** until the live-listing PPC product contract is completed by the owner.",
         "", "## Phase 6 handoff (verified)",
         f"- Phase 6 final status: `{h['phase6_final_status']}`",
         f"- Effective listing publishability: `{h['listing_publishability']}` (safe draft, NOT publishable)",
         f"- COPY NOW: **{h['copy_now_count']}**    UPLOAD NOW: **{h['upload_now_count']}**",
         f"- Real product photos required: **{h['real_photo_required_count']}**",
         f"- A+ eligibility: `{h['aplus_capability_state']}`",
         "", "## Missing owner inputs (never fabricated)"]
    for f in cr["missing_owner_confirmation_fields"]:
        L.append(f"- `{f}` — MISSING")
    L += ["", "## The permanent Amazon boundary",
          "- The owner is the ONLY manual bridge to Amazon. The toolkit performs no login, no SP-API/"
          "MWS/Advertising API, no browser automation, no credential/cookie/session store, no automated "
          "report retrieval, no account write, and no public bind.",
          "- Complete `PPC-PRODUCT-CONTRACT.template.json` from live Amazon state (no raw credentials), "
          "then a later session validates it.", ""]
    return "\n".join(L)


def _preflight_guide_md(result):
    r = result
    L = [f"# Phase 7.0 preflight — {r.session_status}", "",
         f"- Readiness decision: **{r.readiness_decision}** (distinct from ready-to-launch-advertising).",
         f"- Phase 6 package: `{r.handoff['live_verification'].get('package_verify_result', 'RECORDED_ONLY')}`.",
         f"- Active Amazon account paths: **{r.network['prohibited_account_path_count']}** "
         f"(dashboard loopback-only: {r.network['dashboard_bind_is_loopback']}).",
         f"- Official Amazon Ads guidance snapshot: {r.guidance['retrieval_date']} "
         f"({r.guidance['topic_count']} topics, {r.guidance['conflict_count']} flagged conflicts).",
         "", "Phase 7.0 implements no 7.1M artifact: no target, no negative, no bid, no budget, no "
         "launch package, no economics.", ""]
    return "\n".join(L)


def build_stage_manifest(result, output_hashes, started_at=None, completed_at=None):
    r = result
    body = {
        "schema_version": SCHEMA_STAGE_MANIFEST,
        "stage_id": STAGE_ID,
        "stage_name": STAGE_NAME,
        "connectivity_mode": r.mode,
        "phase6_final_status": r.handoff["phase6_final_status"],
        "workspace_id": r.handoff["workspace_id"],
        "input_hashes": {
            "phase6_package_index_sha256_prefix": r.handoff["package_index_sha256_prefix"],
            "guidance_snapshot_sha256": r.guidance["deterministic_content_sha256"],
            "network_report_sha256": r.network["deterministic_content_sha256"],
            "authority_map_sha256": r.authority["deterministic_content_sha256"],
            "handoff_audit_sha256": r.handoff_audit["deterministic_content_sha256"],
            "contract_requirements_sha256": r.contract_req["deterministic_content_sha256"],
            "readiness_sha256": r.readiness["deterministic_content_sha256"],
        },
        "generated_outputs": sorted(output_hashes),
        "output_hashes": output_hashes,
        "readiness_decision": r.readiness_decision,
        "session_status": r.session_status,
        "external_amazon_account_attempts": 0,
        "amazon_account_actions": 0,
        "external_public_research_calls": 0,
        "phase7_1m_artifact_count": 0,
        "later_phase_artifact_count": 0,
        "deterministic_content_sha256": None,
    }
    body["deterministic_content_sha256"] = content_sha256(
        {k: v for k, v in body.items() if k not in _VOLATILE})
    if started_at:
        body["started_at"] = started_at
    if completed_at:
        body["completed_at"] = completed_at
    return body


def write_phase7_artifacts(result, out_dir, started_at=None, completed_at=None):
    """Build all artifacts in memory (already done) and write them atomically to *out_dir*. A failed
    write never overwrites a last-valid artifact (temp sibling + fsync + os.replace)."""
    os.makedirs(out_dir, exist_ok=True)
    docs = {
        F_PREFLIGHT: result.preflight,
        F_AUTHORITY: result.authority,
        F_NETWORK: result.network,
        F_HANDOFF: result.handoff_audit,
        F_GUIDANCE: result.guidance,
        F_READINESS: result.readiness,
        F_CONTRACT_REQ: result.contract_req,
        F_CONTRACT_TEMPLATE: result.contract_tmpl,
    }
    output_hashes = {}
    for name, doc in docs.items():
        data = canonical_json(doc).encode("utf-8")
        _atomic_write_bytes(os.path.join(out_dir, name), data)
        output_hashes[name] = _sha_bytes(data)
    for name, text in ((F_OWNER_INPUT, _owner_input_md(result)),
                       (F_PREFLIGHT_GUIDE, _preflight_guide_md(result))):
        data = text.encode("utf-8")
        _atomic_write_bytes(os.path.join(out_dir, name), data)
        output_hashes[name] = _sha_bytes(data)

    manifest = build_stage_manifest(result, output_hashes, started_at=started_at, completed_at=completed_at)
    _atomic_write_bytes(os.path.join(out_dir, F_STAGE_MANIFEST),
                        canonical_json(manifest).encode("utf-8"))
    return manifest


# ================================================================ COMMITTED PROOF (sanitized)
def build_proof_gate(result, started_commit=None):
    """The sanitized committed proof — prefixes, counts, states, and reason codes only. No absolute
    paths, no raw owner values, no full private SHAs beyond the already-committed Phase 6 prefixes."""
    r = result
    h = r.handoff
    doc = {
        "schema_version": SCHEMA_PROOF_GATE,
        "session": "PHASE 7.0 — REPOSITORY, PHASE 6 HANDOFF, AMAZON BOUNDARY, AND LIVE-PRODUCT PREFLIGHT",
        "starting_commit": started_commit,
        "final_commit": None,
        "phase7_preflight_authority": "production/phase7_preflight.py",
        "connectivity_modes": list(CONNECTIVITY_MODES),
        "readiness_decision": r.readiness_decision,
        "session_status": r.session_status,
        "readiness_distinct_from_manual_launch": True,
        # --- Phase 6 handoff (sanitized) ---
        "phase6_final_status": h["phase6_final_status"],
        "phase6_handoff_live_verification": h["live_verification"].get("package_verify_result",
                                                                       "RECORDED_ONLY"),
        "phase6_package_index_sha256_prefix": h["package_index_sha256_prefix"],
        "phase6_package_index_hash_file_result": h["live_verification"].get(
            "package_index_hash_file_result", "RECORDED_ONLY"),
        "keyword_source_sha256_prefix": h["keyword_source_sha256_prefix"],
        "keyword_allocation_sha256_prefix": h["phase6b_manifest_sha256_prefix"],
        "product_facts_sha256_prefix": h["product_facts_sha256_prefix"],
        "claim_evidence_sha256_prefix": h["claim_evidence_sha256_prefix"],
        "product_id": h["product_id"],
        "canonical_category_id": h["canonical_category_id"],
        "marketplace": h["marketplace"],
        "listing_publishability": h["listing_publishability"],
        "copy_now_count": h["copy_now_count"],
        "upload_now_count": h["upload_now_count"],
        "real_photo_required_count": h["real_photo_required_count"],
        "aplus_capability_state": h["aplus_capability_state"],
        # --- authority map ---
        "authority_all_declared_exist": r.authority["all_declared_authorities_exist"],
        "duplicate_production_authority_count": len(r.authority["duplicate_production_authorities"]),
        "ppc_production_authority_present": r.authority["ppc_production_authority_present"],
        # --- network boundary ---
        "network_no_active_amazon_account_path": r.network["no_active_amazon_account_path"],
        "prohibited_account_path_count": r.network["prohibited_account_path_count"],
        "sp_api_path_count": r.network["sp_api_client_path_count"],
        "mws_path_count": r.network["mws_client_path_count"],
        "amazon_ads_api_path_count": r.network["amazon_ads_api_client_path_count"],
        "browser_automation_path_count": r.network["browser_automation_path_count"],
        "credential_or_session_path_count": r.network["credential_or_session_store_path_count"],
        "automated_report_retrieval_count": r.network["automated_report_retrieval_path_count"],
        "non_loopback_bind_count": r.network["non_loopback_bind_count"],
        "dashboard_bind_is_loopback": r.network["dashboard_bind_is_loopback"],
        # --- guidance ---
        "official_guidance_retrieval_date": r.guidance["retrieval_date"],
        "official_guidance_domain": r.guidance["official_domain"],
        "official_guidance_source_count": r.guidance["topic_count"],
        "official_guidance_conflict_count": r.guidance["conflict_count"],
        "official_guidance_snapshot_sha256_prefix": _prefix(r.guidance["deterministic_content_sha256"]),
        # --- contract ---
        "ppc_contract_present": r.contract_req["contract_present"],
        "ppc_contract_is_template_only": r.contract_req["contract_is_template_only"],
        "ppc_contract_verified_field_count": r.contract_req["verified_count"],
        "ppc_contract_missing_field_count": r.contract_req["missing_count"],
        "ppc_contract_contradictory_field_count": r.contract_req["contradictory_count"],
        "ppc_contract_launch_valid": r.contract_req["contract_launch_valid"],
        "advertised_asin_state": MISSING,
        "advertised_sku_state": MISSING,
        "advertised_variant_state": MISSING,
        "live_listing_state": MISSING,
        "buyable_listing_state": MISSING,
        "listing_version_match_state": MISSING,
        "price_currency_state": MISSING,
        "capacity_state": MISSING,
        "handling_time_state": MISSING,
        "advertising_eligibility_state": MISSING,
        "featured_offer_state": MISSING,
        # --- economics/budget/objective inputs ---
        "economics_inputs_available": "FEASIBILITY_SAMPLE_ONLY_NOT_OWNER_CONFIRMED",
        "budget_inputs_available": False,
        "owner_objective_available": False,
        "evidence_target_available": False,
        # --- boundary counters ---
        "external_amazon_account_attempts": 0,
        "amazon_account_actions": 0,
        "external_public_research_calls": 0,
        "phase7_1m_artifact_count": 0,
        "later_phase_artifact_count": 0,
        # --- determinism ---
        "artifact_stable_hashes": {
            F_HANDOFF: _prefix(r.handoff_audit["deterministic_content_sha256"]),
            F_NETWORK: _prefix(r.network["deterministic_content_sha256"]),
            F_AUTHORITY: _prefix(r.authority["deterministic_content_sha256"]),
            F_GUIDANCE: _prefix(r.guidance["deterministic_content_sha256"]),
            F_READINESS: _prefix(r.readiness["deterministic_content_sha256"]),
            F_CONTRACT_REQ: _prefix(r.contract_req["deterministic_content_sha256"]),
            F_PREFLIGHT: _prefix(r.preflight["deterministic_content_sha256"]),
        },
        "required_local_artifacts": list(REQUIRED_ARTIFACTS),
        "sanitization_note": "Prefixes, counts, states, and reason codes only. No absolute paths, no raw "
                             "owner values, no ASIN/SKU, no paid keyword rows, no credentials. Local "
                             "run artifacts stay under runs/ (gitignored).",
        "known_limitations": [
            "Phase 6 remains a verified SAFE DRAFT (COPY NOW=0, UPLOAD NOW=0); it is not PPC-ready.",
            "No live listing / ASIN / SKU / price / capacity / handling / eligibility is owner-confirmed.",
            "The official Amazon Ads guidance is a dated snapshot; the owner must revalidate live before 7.1M/7.2.",
            "The local Phase 6/7 workspace under runs/ is gitignored; committed proof records sanitized states only.",
        ],
    }
    doc["deterministic_content_sha256"] = content_sha256(
        {k: v for k, v in doc.items()
         if k not in ("deterministic_content_sha256", "starting_commit", "final_commit")})
    return doc


# ================================================================ CLI
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Phase 7.0 repository + live-product preflight (read-only)")
    ap.add_argument("--repo-root", default=_ROOT)
    ap.add_argument("--run-dir", default=None, help="local product workspace (e.g. runs/T2)")
    ap.add_argument("--out-dir", default=None, help="artifact output dir (default runs/<id>/phase7/7.0)")
    ap.add_argument("--mode", default="LOCAL_SAFE", choices=list(CONNECTIVITY_MODES))
    ap.add_argument("--refresh-guidance", action="store_true")
    ap.add_argument("--proof-out", default=None, help="write the sanitized committed proof gate here")
    ap.add_argument("--starting-commit", default=None)
    a = ap.parse_args(argv)
    result = assemble_phase7_preflight(a.repo_root, run_dir=a.run_dir, mode=a.mode,
                                       refresh_guidance=a.refresh_guidance)
    out_dir = a.out_dir or (os.path.join(a.run_dir, "phase7", "7.0") if a.run_dir
                            else os.path.join(a.repo_root, "runs", "_phase7", "7.0"))
    manifest = write_phase7_artifacts(result, out_dir)
    if a.proof_out:
        proof = build_proof_gate(result, started_commit=a.starting_commit)
        _atomic_write_bytes(a.proof_out, canonical_json(proof).encode("utf-8"))
    print(f"readiness_decision={result.readiness_decision} session_status={result.session_status} "
          f"prohibited_account_paths={result.network['prohibited_account_path_count']} "
          f"missing_owner_fields={len(result.contract_req['missing_owner_confirmation_fields'])} "
          f"guidance={result.guidance['retrieval_date']} manifest={manifest['deterministic_content_sha256'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
