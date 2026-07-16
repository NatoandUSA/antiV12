#!/usr/bin/env python3
"""
core.manifest — the central PROJECT-MANIFEST.json per project (atomic + history).

Records stage results, gate verdicts, owner approvals, source-file hashes,
assumptions, warnings and manual actions. Writes atomically (temp+rename) so a
crash never corrupts the file, and appends prior states to history rather than
silently overwriting decisions.
"""
from __future__ import annotations
import os, json, tempfile
from datetime import datetime
try:
    from . import config, hashing, status as S
    from . import gate_engine
except ImportError:  # flat import (core/ on sys.path)
    import config, hashing, status as S
    import gate_engine

MANIFEST_NAME = "PROJECT-MANIFEST.json"

def _now() -> str:
    try: return datetime.now().astimezone().isoformat(timespec="seconds")
    except Exception: return datetime.now().isoformat(timespec="seconds")

def path(folder: str) -> str:
    return os.path.join(folder, MANIFEST_NAME)

def new(project_id: str, project_name: str, **kw) -> dict:
    return {
        "project_id": project_id,
        "project_name": project_name,
        "created_at": _now(),
        "updated_at": _now(),
        "marketplace": config.get("marketplace", default="US"),
        "fulfillment": kw.get("fulfillment", "FBM"),
        "product_family": kw.get("product_family", ""),
        "owner": kw.get("owner", ""),
        "staff_assignee": kw.get("staff_assignee", ""),
        "current_stage": kw.get("current_stage", "Stage 0"),
        "overall_status": S.NOT_STARTED,
        "publication_locked": True,
        "publication_lock_reasons": ["not yet evaluated"],
        "source_files": [],
        "source_file_hashes": {},
        "stage_results": {},
        "gates": {},
        "owner_approvals": [],
        "tool_versions": {"toolkit": config.tool_version()},
        "assumptions": [],
        "warnings": [],
        "manual_actions": [],
        "history": [],
    }

def load(folder: str, project_id: str = "", project_name: str = "") -> dict:
    p = path(folder)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return new(project_id or os.path.basename(folder.rstrip("/")) or "project",
               project_name or os.path.basename(folder.rstrip("/")))

def save(folder: str, man: dict) -> dict:
    """Atomic write: temp file + os.replace. Snapshots prior gate/lock state to history."""
    os.makedirs(folder, exist_ok=True)
    man["updated_at"] = _now()
    p = path(folder)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".manifest-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(man, f, indent=2, ensure_ascii=False)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return man

def _snapshot(man: dict, note: str) -> None:
    man.setdefault("history", []).append({
        "at": _now(), "note": note,
        "overall_status": man.get("overall_status"),
        "publication_locked": man.get("publication_locked"),
        "lock_reasons": list(man.get("publication_lock_reasons", [])),
    })

def register_sources(folder: str, files: list[str]) -> dict:
    man = load(folder)
    man["source_files"] = sorted(set(man.get("source_files", []) + [os.path.basename(f) for f in files]))
    man["source_file_hashes"].update(hashing.hash_many(files))
    return save(folder, man)

def set_stage(folder: str, stage: str, result: dict) -> dict:
    man = load(folder)
    man["stage_results"][stage] = {**result, "at": _now()}
    man["current_stage"] = stage
    return save(folder, man)

def set_gate(folder: str, gate_id: str, gate_result: dict) -> dict:
    man = load(folder)
    if hasattr(gate_result, "to_dict"):
        gate_result = gate_result.to_dict()
    entry = {**gate_result, "at": _now()}
    # v2.3.4 (P0.6): remember the technical status of owner-gated creative gates so an
    # owner approval can upgrade the gate to APPROVED and an invalidation can fall back to it.
    if gate_id == "MAIN_IMAGE_COMPLIANCE":
        entry["technical_status"] = gate_result.get("status")
    man["gates"][gate_id] = entry
    return _reevaluate(folder, man)

def add_approval(folder: str, approval: dict) -> dict:
    man = load(folder)
    ap = {**approval, "at": approval.get("approved_at") or _now()}
    # P0.6: bind the approval to a content hash of the exact approved files
    files = approval.get("approved_files")
    if files:
        paths = files if isinstance(files, list) else list(files.values())
        hashes = {}
        for p in paths:
            # P0.7: preserve the relative path as the identity (no basename collisions)
            rel = os.path.relpath(p, folder) if os.path.isabs(p) else p
            hashes[rel] = hashing.file_sha256(p if os.path.isabs(p) else os.path.join(folder, p))
        ap["approved_file_hashes"] = hashes
        ap["approved_bundle_hash"] = hashing.text_sha256("|".join(sorted(f"{k}:{v}" for k,v in hashes.items())))
    man.setdefault("owner_approvals", []).append(ap)
    return _reevaluate(folder, man)

def verify_approvals(folder: str) -> dict:
    """Recompute approved-file hashes; auto-invalidate any approval whose bundle changed."""
    man = load(folder); changed_any = False
    for a in man.get("owner_approvals", []):
        if a.get("decision") != "APPROVED" or not a.get("approved_file_hashes"):
            continue
        drift = []
        for fname, old in a["approved_file_hashes"].items():
            new = hashing.file_sha256(os.path.join(folder, fname))
            if new != old:
                drift.append(fname)
        if drift:
            a["decision"] = "INVALIDATED"; a["invalidated_reason"] = "files changed since approval: " + ", ".join(drift)
            a["invalidated_at"] = _now(); changed_any = True
            man.setdefault("warnings", []).append(f"Approval '{a.get('approval_type')}' auto-invalidated: {a['invalidated_reason']}")
    if changed_any:
        return _reevaluate(folder, man)
    return man

def invalidate_approvals(folder: str, approval_type: str, reason: str) -> dict:
    """When an upstream input changes, mark matching approvals invalid (never delete history)."""
    man = load(folder)
    for a in man.get("owner_approvals", []):
        if a.get("approval_type") == approval_type and a.get("decision") == "APPROVED":
            a["decision"] = "INVALIDATED"
            a["invalidated_reason"] = reason
            a["invalidated_at"] = _now()
    man.setdefault("warnings", []).append(f"Approvals '{approval_type}' invalidated: {reason}")
    return _reevaluate(folder, man)

def _reevaluate(folder: str, man: dict) -> dict:
    """Recompute publication lock from current gates + owner approval presence."""
    # auto-invalidate approvals whose bound files changed (hash drift)
    for a in man.get("owner_approvals", []):
        if a.get("decision") == "APPROVED" and a.get("approved_file_hashes"):
            drift = [f for f, old in a["approved_file_hashes"].items()
                     if hashing.file_sha256(os.path.join(folder, f)) != old]
            if drift:
                a["decision"] = "INVALIDATED"
                a["invalidated_reason"] = "files changed since approval: " + ", ".join(drift)
                a["invalidated_at"] = _now()
    gates = dict(man.get("gates", {}))
    # owner approval gate derived from approvals list
    def _has_valid(kind):
        return any(a.get("approval_type") == kind and a.get("decision") == "APPROVED"
                   for a in man.get("owner_approvals", []))
    has_final = _has_valid("FINAL_LISTING")
    gates["OWNER_APPROVAL"] = {
        "gate_id": "OWNER_APPROVAL",
        "status": S.APPROVED if has_final else S.INCOMPLETE,
        "blocking": not has_final,
        "reasons": [] if has_final else ["no final owner approval on record"],
    }
    # v2.3.4 (P0.6): the owner main-image + creative approvals are the ONLY way these two gates
    # reach APPROVED. Derived from a valid (non-invalidated) approval so hash-drift invalidation
    # flows straight to the gate. This override runs AFTER creative_edge's technical statuses,
    # so a valid approval upgrades the gate and an invalidated one lets it fall back.
    mic = gates.get("MAIN_IMAGE_COMPLIANCE")
    if mic is not None:
        # technical baseline (never lost): the status set by creative_edge, not a prior derivation
        tech = mic.get("technical_status")
        if tech is None:
            tech = mic.get("status") if mic.get("status") != S.APPROVED else "COMPLIANT"
        if _has_valid("MAIN_IMAGE_APPROVAL"):
            gates["MAIN_IMAGE_COMPLIANCE"] = {"gate_id": "MAIN_IMAGE_COMPLIANCE", "status": S.APPROVED,
                "technical_status": tech, "blocking": False, "reasons": ["owner-approved main image (hash-bound)"]}
        else:
            gates["MAIN_IMAGE_COMPLIANCE"] = {"gate_id": "MAIN_IMAGE_COMPLIANCE", "status": tech,
                "technical_status": tech, "blocking": tech not in (S.APPROVED,),
                "reasons": ["main image technically " + str(tech) + "; owner approval pending"]}
    gates["CREATIVE_OWNER_APPROVAL"] = {"gate_id": "CREATIVE_OWNER_APPROVAL",
        "status": S.APPROVED if _has_valid("CREATIVE_PACKAGE") else S.INCOMPLETE,
        "blocking": not _has_valid("CREATIVE_PACKAGE"),
        "reasons": ["owner-approved creative package (hash-bound)"] if _has_valid("CREATIVE_PACKAGE")
                   else ["no creative package approval on record"]}
    # pass project traits so conditional creative gates are required for apparel/embroidery
    project = {"product_family": man.get("product_family",""),
               "decoration_method": man.get("decoration_method", man.get("product_family","")),
               "requires_listing_images": man.get("requires_listing_images", True),
               "requires_embroidery_proof": man.get("requires_embroidery_proof",
                   "embroider" in str(man.get("product_family","")).lower())}
    verdict = gate_engine.evaluate(gates, project=project)
    prev_lock = man.get("publication_locked")
    man["gates"] = verdict["gates"]
    man["publication_locked"] = verdict["publication_locked"]
    man["publication_lock_reasons"] = verdict["publication_lock_reasons"]
    man["overall_status"] = verdict["overall_status"]
    if prev_lock != man["publication_locked"]:
        _snapshot(man, "publication_locked changed")
    return save(folder, man)

def summary(folder: str) -> str:
    man = load(folder)
    lock = "🔒 PUBLICATION LOCKED" if man.get("publication_locked") else "🔓 unlocked (owner may proceed)"
    lines = [f"# {man.get('project_name')}  [{man.get('overall_status')}]",
             f"{lock}"]
    if man.get("publication_lock_reasons"):
        lines.append("  reasons: " + ", ".join(man["publication_lock_reasons"]))
    return "\n".join(lines)
