#!/usr/bin/env python3
"""creative.creative_production_package — the ONE Phase 6E creative production planning authority.

Phase 6E turns the verified Phase 6C product-detail page and Phase 6D A+ draft (and their Phase 6A/6B
lineage) into an EVIDENCE-GATED creative planning package: exactly ten stable creative job records, a
listing-image plan, listing + A+ image *prompt instructions* (planning text only), a real-photo shot
list, a creative asset manifest, cross-image consistency requirements, and a creative asset audit.

It is a thin assembler. It never re-implements the product-fact loader, the atomic claim engine, the
A+ builder / asset manifest, the category-policy registry, or the PageAuditor — it REUSES them via the
mature Phase 6A/6C/6D authorities:

  * 6A/6B verified view + engines + Amazon boundary -> production.aplus_assembly.load_phase6d_dependencies
  * 6C dependency verify + reproduce            -> production.product_detail_page
  * 6D dependency verify + reproduce            -> production.aplus_assembly
  * product facts / atomic claims               -> listing.product_fact_loader / listing.claim_evidence
  * category policy                             -> listing.category_policy_registry
  * page auditor                                -> listing.page_auditor
  * canonical json / hash / atomic write        -> production.product_workspace helpers
  * runtime / network safety                    -> core.runtime_policy

Hard rules it enforces:
  * It NEVER generates an image file, calls an image-generation API, or opens the network.
  * A generated / AI mockup can satisfy ZERO real-proof gates (real_or_generated proof is real-only).
  * It invents no product colour, garment source, decoration, size, fit or care; unknown truth stays
    null with explicit blockers.
  * It writes a full AI prompt body ONLY for a PROMPT_READY job; a real-photo job gets photography
    instructions, an owner-fact / not-applicable / blocked job gets NO prompt body.
  * It never touches the owner's Amazon account and never emits a Phase 6F / Seller-Central artifact.

Public API:
  load_phase6e_dependencies(run_dir, connectivity_mode=...) -> Phase6EDependencies
  reconcile_category_identity(deps) -> dict
  reconcile_phase6d_asset_requirements(deps) -> dict
  assemble_phase6e(run_dir, connectivity_mode=...) -> CreativeProductionPackageResult
  write_phase6e_artifacts(result, dest_dir=None, ...) -> manifest dict
  verify_phase6e_artifacts(dest_dir, workspace_dir=None) -> dict
  phase6e_dir(run_dir) -> str
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ("", "listing", "core"):
    _p = _ROOT if not _sub else os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import product_workspace as PW          # noqa: E402  canonical json / hash / atomic template
from production import product_detail_page as PDP       # noqa: E402  Phase 6C authority
from production import aplus_assembly as APL            # noqa: E402  Phase 6D authority (+ 6A/6B loader)
import claim_evidence as CE                             # noqa: E402
import category_policy_registry as CPR                  # noqa: E402
import page_auditor as PA                               # noqa: E402
import runtime_policy as RP                             # noqa: E402

# one serialization + hashing authority (do NOT re-implement) --------------------------------------
canonical_json = PW.canonical_json
content_sha256 = PW.content_sha256
_file_sha256 = PW._file_sha256

# -- schema versions -------------------------------------------------------------------------------
SCHEMA_VERSION = "phase6-creative-production-package-v1"
LISTING_IMAGE_PLAN_SCHEMA_VERSION = "phase6e-listing-image-plan-v1"
ASSET_MANIFEST_SCHEMA_VERSION = "phase6e-creative-asset-manifest-v1"
CREATIVE_AUDIT_SCHEMA_VERSION = "phase6e-creative-asset-audit-v1"
CROSS_IMAGE_SCHEMA_VERSION = "phase6e-cross-image-consistency-v1"
CHECKLIST_SCHEMA_VERSION = "phase6e-creative-asset-checklist-v1"
STAGE_MANIFEST_SCHEMA_VERSION = "phase6e-stage-manifest-v1"
TEN_JOB_CONTRACT_VERSION = "phase6e-ten-job-v1"

STAGE_ID = "6E"
STAGE_NAME = "Evidence-Gated Creative Production Package"

LISTING_SAFE_DRAFT = "SAFE_DRAFT_OWNER_FACTS_REQUIRED"

# -- Phase 6E stage states -------------------------------------------------------------------------
READY_FOR_6F = "READY_FOR_6F"
SAFE_CREATIVE_PLAN_OWNER_FACTS_REQUIRED = "SAFE_CREATIVE_PLAN_OWNER_FACTS_REQUIRED"
CREATIVE_ASSETS_REQUIRED = "CREATIVE_ASSETS_REQUIRED"
BLOCKED_DEPENDENCY = "BLOCKED_DEPENDENCY"
BLOCKED_UNSAFE = "BLOCKED_UNSAFE"
INVALID_JOB_SCHEMA = "INVALID_JOB_SCHEMA"
INVALID_ASSET_MANIFEST = "INVALID_ASSET_MANIFEST"
INVALID_PROMPT_PACKAGE = "INVALID_PROMPT_PACKAGE"
INVALID_AUDIT = "INVALID_AUDIT"
PHASE6E_STATES = (READY_FOR_6F, SAFE_CREATIVE_PLAN_OWNER_FACTS_REQUIRED, CREATIVE_ASSETS_REQUIRED,
                  BLOCKED_DEPENDENCY, BLOCKED_UNSAFE, INVALID_JOB_SCHEMA, INVALID_ASSET_MANIFEST,
                  INVALID_PROMPT_PACKAGE, INVALID_AUDIT)

# -- job states (the only states a creative job record may carry) ----------------------------------
ASSET_READY = "ASSET_READY"
PROMPT_READY = "PROMPT_READY"
REAL_PHOTO_REQUIRED = "REAL_PHOTO_REQUIRED"
OWNER_FACT_REQUIRED = "OWNER_FACT_REQUIRED"
NOT_APPLICABLE = "NOT_APPLICABLE"
BLOCKED_UNSAFE_JOB = "BLOCKED_UNSAFE"
JOB_STATES = (ASSET_READY, PROMPT_READY, REAL_PHOTO_REQUIRED, OWNER_FACT_REQUIRED, NOT_APPLICABLE,
              BLOCKED_UNSAFE_JOB)

# -- prompt / asset readiness (kept as SEPARATE axes) ----------------------------------------------
PR_READY = "READY"
PR_BLOCKED = "BLOCKED"
PR_OWNER_FACT_REQUIRED = "OWNER_FACT_REQUIRED"
PR_NOT_APPLICABLE = "NOT_APPLICABLE"

AR_MISSING = "MISSING"
AR_REAL_PHOTO_REQUIRED = "REAL_PHOTO_REQUIRED"
AR_OWNER_FACT_REQUIRED = "OWNER_FACT_REQUIRED"
AR_NOT_APPLICABLE = "NOT_APPLICABLE"
AR_READY = "READY"

# -- artifacts (all under <run>/phase6/6E) ---------------------------------------------------------
LISTING_IMAGE_PLAN_FILENAME = "LISTING-IMAGE-PLAN.json"
LISTING_IMAGE_PROMPTS_FILENAME = "LISTING-IMAGE-PROMPTS.md"
APLUS_IMAGE_PROMPTS_FILENAME = "APLUS-IMAGE-PROMPTS.md"
REAL_PHOTO_SHOT_LIST_FILENAME = "REAL_PHOTO_SHOT_LIST.md"
CREATIVE_ASSET_MANIFEST_FILENAME = "CREATIVE-ASSET-MANIFEST.json"
CREATIVE_ASSET_AUDIT_FILENAME = "CREATIVE-ASSET-AUDIT.json"
STAGE_MANIFEST_FILENAME = "STAGE-6E-MANIFEST.json"
CREATIVE_ASSET_CHECKLIST_FILENAME = "CREATIVE-ASSET-CHECKLIST.json"
CREATIVE_BRIEF_FILENAME = "CREATIVE-BRIEF.md"
CROSS_IMAGE_CONSISTENCY_FILENAME = "CROSS-IMAGE-CONSISTENCY.json"

REQUIRED_ARTIFACTS = (LISTING_IMAGE_PLAN_FILENAME, LISTING_IMAGE_PROMPTS_FILENAME,
                      APLUS_IMAGE_PROMPTS_FILENAME, REAL_PHOTO_SHOT_LIST_FILENAME,
                      CREATIVE_ASSET_MANIFEST_FILENAME, CREATIVE_ASSET_AUDIT_FILENAME)
SUPPORT_ARTIFACTS = (CREATIVE_ASSET_CHECKLIST_FILENAME, CREATIVE_BRIEF_FILENAME,
                     CROSS_IMAGE_CONSISTENCY_FILENAME)

# artifacts / files a Phase 6E run must NEVER emit (they belong to Phase 6F or Seller Central).
FORBIDDEN_ARTIFACTS = frozenset({
    "PACKAGE-INDEX.json", "PACKAGE-INDEX.sha256", "SELLER-CENTRAL-READY",
    "SELLER-CENTRAL-MANUAL-ENTRY-PLAN.json",
})
FORBIDDEN_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".gif", ".bmp"})

_MANIFEST_VOLATILE = ("deterministic_content_sha256", "started_at", "completed_at",
                      "previous_valid_manifest_sha256", "verification")

# file formats an accepted real photograph may use (from the verified Phase 6A real-asset authority).
_ACCEPTED_PHOTO_FORMATS = ("jpg", "jpeg", "png", "tiff", "webp")

# universal prohibitions every creative asset / prompt must obey (a generated mockup can prove none).
_UNIVERSAL_NEGATIVE_CONSTRAINTS = (
    "no invented or assumed garment colour",
    "no invented fabric or material claim",
    "no invented decoration method (embroidery, print, vinyl, etc.) shown as if real",
    "no invented personalization text or exact-as-entered promise",
    "no invented size, fit, or measurement",
    "no invented packaging, gift, or occasion context",
    "no brand, logo, character, or trademark",
    "no invented accessory or included item",
    "no invented quality/superiority claim",
    "no visual inconsistency with the other planned images",
    "must never be presented as real photographic proof of a physical attribute",
)


class Phase6EError(Exception):
    pass


class Phase6EDependencyError(Phase6EError):
    pass


class Phase6EAssemblyError(Phase6EError):
    pass


# ---------------------------------------------------------------- paths / atomic write
def phase6e_dir(run_dir):
    return os.path.join(run_dir, "phase6", "6E")


def _category_policy_path():
    return os.path.join(_ROOT, "listing", "category_policies.json")


def _safe_rel(path, base):
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(base))
    norm = rel.replace(os.sep, "/")
    if os.path.isabs(rel) or norm == ".." or norm.startswith("../"):
        raise Phase6EAssemblyError(f"path escapes workspace ({norm}): {path}")
    return norm


def _atomic_write_text(path, text):
    """temp sibling -> flush -> fsync -> os.replace. A failed write never overwrites the last valid file."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-6e-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _atomic_write_json(path, obj):
    _atomic_write_text(path, canonical_json(obj))


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _manifest_hashable(body):
    return {k: v for k, v in body.items() if k not in _MANIFEST_VOLATILE}


def _recomputes_manifest(doc, volatile):
    return doc.get("deterministic_content_sha256") == content_sha256(
        {k: v for k, v in doc.items() if k not in volatile})


# ================================================================ DEPENDENCIES
class Phase6EDependencies:
    """Verified, read-only view over Phase 6A/6B/6C/6D, plus the reusable engines."""

    def __init__(self, *, run_dir, connectivity_mode, deps6d, pdp_result, apl_result,
                 manifest6c, manifest6d, phase6c_manifest_sha256, phase6d_manifest_sha256):
        self.run_dir = run_dir
        self.connectivity_mode = connectivity_mode
        self.deps6d = deps6d                      # aplus_assembly.Phase6DDependencies (6A/6B view + engines)
        self.pdp_result = pdp_result              # reproduced Phase 6C result
        self.apl_result = apl_result              # reproduced Phase 6D result
        self.manifest6c = manifest6c
        self.manifest6d = manifest6d
        self.phase6c_manifest_sha256 = phase6c_manifest_sha256
        self.phase6d_manifest_sha256 = phase6d_manifest_sha256

    # identity / hashes proxied from the verified 6A view
    @property
    def workspace(self):
        return self.deps6d.workspace

    @property
    def facts(self):
        return self.deps6d.facts

    @property
    def claims(self):
        return self.deps6d.claims

    @property
    def keyword_source(self):
        return self.deps6d.keyword_source

    @property
    def policy(self):
        return self.deps6d.policy

    @property
    def workspace_id(self):
        return self.deps6d.workspace_id

    @property
    def product_id(self):
        return self.deps6d.product_id

    @property
    def category(self):
        return self.deps6d.category

    @property
    def product_type(self):
        return self.deps6d.product_type

    @property
    def audience(self):
        return self.deps6d.audience

    @property
    def keyword_source_sha256(self):
        return self.deps6d.keyword_source_sha256

    @property
    def product_facts_sha256(self):
        return self.deps6d.product_facts_sha256

    @property
    def claim_evidence_sha256(self):
        return self.deps6d.claim_evidence_sha256

    @property
    def phase6a_manifest_sha256(self):
        return self.deps6d.phase6a_manifest_sha256

    @property
    def phase6b_manifest_sha256(self):
        return self.deps6d.phase6b_manifest_sha256


def load_phase6e_dependencies(run_dir, connectivity_mode="CONNECTED_RESEARCH"):
    """Load + VERIFY the full Phase 6A→6D chain before any planning.

    Blocks (Phase6EDependencyError) when a 6C/6D artifact is missing / tampered / hash-mismatched, the
    6C/6D input hashes disagree with the verified 6A view, a stage is not ready for the next, the listing
    is no longer a safe draft, or the Amazon account boundary is not closed. It NEVER modifies an upstream
    artifact.
    """
    # 1) 6A/6B verified view + engines + Amazon boundary (reuse the mature 6D loader unchanged).
    try:
        deps6d = APL.load_phase6d_dependencies(run_dir)
    except APL.Phase6DDependencyError as e:
        raise Phase6EDependencyError(f"Phase 6A/6B/6D dependency invalid: {e}") from e

    # 2) Amazon-account boundary must remain closed in every mode (defensive re-assert).
    pol = RP.load_runtime_policy()
    if not getattr(pol, "amazon_account_isolation", False) or getattr(pol, "amazon_api_enabled", True) \
            or getattr(pol, "amazon_network_writes_enabled", True) \
            or getattr(pol, "amazon_authenticated_access_enabled", True):
        raise Phase6EDependencyError("Amazon account boundary is not closed")

    # 3) verify the Phase 6C artifacts on disk (present + manifest hashes match final bytes).
    dest6c = PDP.phase6c_dir(run_dir)
    if not os.path.isdir(dest6c):
        raise Phase6EDependencyError("Phase 6C output directory is missing")
    v6c = PDP.verify_phase6c_artifacts(dest6c, workspace_dir=run_dir)
    if not v6c["ok"]:
        raise Phase6EDependencyError(f"Phase 6C artifacts failed verification: {v6c['errors']}")
    manifest6c = _load_json(os.path.join(dest6c, PDP.STAGE_MANIFEST_FILENAME))

    # 4) verify the Phase 6D artifacts on disk.
    dest6d = APL.phase6d_dir(run_dir)
    if not os.path.isdir(dest6d):
        raise Phase6EDependencyError("Phase 6D output directory is missing")
    v6d = APL.verify_phase6d_artifacts(dest6d, workspace_dir=run_dir)
    if not v6d["ok"]:
        raise Phase6EDependencyError(f"Phase 6D artifacts failed verification: {v6d['errors']}")
    manifest6d = _load_json(os.path.join(dest6d, APL.STAGE_MANIFEST_FILENAME))

    # 5) cross-check every dependency hash agrees with the verified 6A view.
    want = {
        "claim_evidence_sha256": deps6d.claim_evidence_sha256,
        "product_facts_sha256": deps6d.product_facts_sha256,
        "phase6a_manifest_sha256": deps6d.phase6a_manifest_sha256,
    }
    for label, man in (("6C", manifest6c), ("6D", manifest6d)):
        ih = man.get("input_hashes", {})
        for field, expected in want.items():
            if ih.get(field) != expected:
                raise Phase6EDependencyError(
                    f"{label} {field} {str(ih.get(field))[:12]} != 6A {str(expected)[:12]}")
        kw = ih.get("source_keyword_sha256", ih.get("keyword_source_sha256"))
        if kw != deps6d.keyword_source_sha256:
            raise Phase6EDependencyError(f"{label} keyword-source hash != 6A")

    # 6) stage readiness + listing safety must still hold.
    if manifest6c.get("ready_for_next_stage") is not True:
        raise Phase6EDependencyError("Phase 6C is not ready_for_next_stage")
    if manifest6d.get("ready_for_6e") is not True:
        raise Phase6EDependencyError("Phase 6D is not ready_for_6e")
    for label, man in (("6C", manifest6c), ("6D", manifest6d)):
        if man.get("listing_publishability_state") != LISTING_SAFE_DRAFT:
            raise Phase6EDependencyError(f"{label} listing state is not {LISTING_SAFE_DRAFT}")

    # 7) confirm each stage manifest's own recorded hash recomputes.
    if not _recomputes_manifest(manifest6c, PDP._MANIFEST_VOLATILE):
        raise Phase6EDependencyError("Phase 6C stage-manifest hash does not recompute")
    if not _recomputes_manifest(manifest6d, APL._MANIFEST_VOLATILE):
        raise Phase6EDependencyError("Phase 6D stage-manifest hash does not recompute")
    phase6c_manifest_sha256 = manifest6c.get("deterministic_content_sha256")
    phase6d_manifest_sha256 = manifest6d.get("deterministic_content_sha256")

    # 8) reproduce 6C + 6D IN MEMORY (defense in depth) and confirm they still match the written bytes.
    pdp_result = PDP.assemble_product_detail_page(run_dir, connectivity_mode=connectivity_mode)
    if pdp_result.ready_for_next_stage is not True:
        raise Phase6EDependencyError("reproduced Phase 6C is not ready_for_next_stage")
    apl_result = APL.assemble_phase6d(run_dir, connectivity_mode=connectivity_mode)
    if apl_result.ready_for_6e is not True:
        raise Phase6EDependencyError("reproduced Phase 6D is not ready_for_6e")
    written_asset_hash = manifest6d.get("output_hashes", {}).get("phase6/6D/APLUS-ASSET-MANIFEST.json")
    reproduced_asset_hash = _sha_text(canonical_json(apl_result.asset_manifest))
    if written_asset_hash != reproduced_asset_hash:
        raise Phase6EDependencyError("reproduced Phase 6D asset manifest does not match the written bytes")

    return Phase6EDependencies(
        run_dir=run_dir, connectivity_mode=connectivity_mode, deps6d=deps6d, pdp_result=pdp_result,
        apl_result=apl_result, manifest6c=manifest6c, manifest6d=manifest6d,
        phase6c_manifest_sha256=phase6c_manifest_sha256, phase6d_manifest_sha256=phase6d_manifest_sha256)


# ================================================================ CATEGORY RECONCILIATION
def reconcile_category_identity(deps):
    """Reconcile the canonical category identity across authorities. Phase 6D reported the DISPLAY text
    ``apparel``; earlier authorities carry the canonical ``US:apparel``. The canonical id and marketplace
    NEVER change silently, and the display text alone never selects the image policy. Because the category
    policy registry carries NO image rules, the category image policy is explicitly UNVERIFIED — no image
    dimension, background, or aspect ratio is invented."""
    ws = deps.workspace or {}
    cp = ws.get("category_policy", {}) or {}
    display_category = ws.get("category") or deps.category or "apparel"
    canonical_category_id = cp.get("category_identifier") or "US:apparel"
    marketplace = canonical_category_id.split(":", 1)[0] if ":" in canonical_category_id else "US"
    category = canonical_category_id.split(":", 1)[1] if ":" in canonical_category_id else display_category

    # resolve through the actual registry authority using the DISPLAY category + marketplace (never the
    # composite string as a key) and confirm it reproduces the same canonical identity.
    resolved = CPR.resolve_category_policy(display_category, marketplace=marketplace)
    reconciled = (resolved.category_identifier == canonical_category_id
                  and not getattr(resolved, "fallback_used", False))
    policy_path = _category_policy_path()
    category_policy_sha256 = _file_sha256(policy_path) if os.path.exists(policy_path) else None

    return {
        "canonical_category_id": canonical_category_id,
        "display_category": display_category,
        "marketplace": marketplace,
        "category": category,
        "category_policy_source": "listing/category_policies.json",
        "category_policy_sha256": category_policy_sha256,
        "category_policy_note": ("Text-field limits only. The category-policy authority carries NO image "
                                 "dimension / background / aspect-ratio / resolution rule; those remain "
                                 "owner-verified in Seller Central."),
        "registry_reconciled": bool(reconciled),
        "registry_category_identifier": resolved.category_identifier,
        "registry_fallback_used": bool(getattr(resolved, "fallback_used", False)),
        "canonical_unchanged": canonical_category_id == "US:apparel",
        "category_image_policy_state": "CATEGORY_IMAGE_POLICY_UNVERIFIED",
        "accepted_photo_formats": list(_ACCEPTED_PHOTO_FORMATS),
        "image_dimensions": None,
        "background_rule": None,
        "aspect_ratio": None,
        "minimum_resolution": None,
    }


# ================================================================ 6D ASSET RECONCILIATION
def reconcile_phase6d_asset_requirements(deps):
    """Reconcile the Phase 6D A+ asset picture separately from the ten-job creative plan. Active module
    asset requirements (currently structured Basic/Premium fixed content) are counted apart from deferred
    Premium *candidate* dependencies (tied to rejected/unavailable dynamic modules). Deferred candidates
    are never promoted to active production approvals; shared assets are counted once."""
    apl = deps.apl_result
    asset_doc = apl.asset_manifest
    active_assets = list(asset_doc.get("assets", []))
    active_ids = [a.get("asset_id") for a in active_assets]
    active_proof_purposes = sorted({a.get("proof_purpose") for a in active_assets if a.get("proof_purpose")})

    prem = apl.premium_aplus_draft
    dyn_candidates = list(prem.get("dynamic_candidates", []))
    rejected = [c for c in dyn_candidates if not c.get("eligible")]
    deferred_real_assets = sorted({ra for c in dyn_candidates for ra in c.get("required_real_assets", [])})
    deferred_no_asset_modules = sorted({c.get("module_key") for c in dyn_candidates
                                        if not c.get("required_real_assets")})

    active_real_photo = [a for a in active_assets if a.get("real_photo_required")]
    active_owner_fact = [a for a in active_assets
                         if not a.get("real_photo_required") and a.get("status") == "OWNER_FACT_REQUIRED"]

    result = {
        "active_asset_requirement_count": len(active_assets),
        "active_asset_ids": sorted([i for i in active_ids if i]),
        "active_real_photo_required_count": len(active_real_photo),
        "active_owner_fact_required_count": len(active_owner_fact),
        "unique_required_asset_count": len(active_proof_purposes),
        "unique_active_proof_purposes": active_proof_purposes,
        "deferred_candidate_module_count": len(rejected),
        "deferred_candidate_asset_requirement_count": len(deferred_real_assets),
        "deferred_candidate_real_assets": deferred_real_assets,
        "deferred_candidate_modules": sorted({c.get("module_key") for c in rejected}),
        "deferred_no_asset_module_count": len(deferred_no_asset_modules),
        "not_applicable_asset_requirement_count": sum(
            1 for a in active_assets if a.get("status") == "NOT_APPLICABLE"),
        "current_verified_asset_count": sum(
            1 for a in active_assets if a.get("current_asset_sha256")),
        "missing_real_asset_count": len(active_real_photo),
        "generated_accepted_as_real_proof_count": int(
            asset_doc.get("generated_accepted_as_real_proof_count", 0)),
        "reconciles": True,
        "blockers": [],
    }
    # hard reconciliation checks -------------------------------------------------------------------
    if result["active_asset_requirement_count"] != int(asset_doc.get("asset_count", -1)):
        result["reconciles"] = False
        result["blockers"].append("active asset count disagrees with the Phase 6D asset manifest")
    if result["generated_accepted_as_real_proof_count"] != 0:
        result["reconciles"] = False
        result["blockers"].append("a generated asset was accepted as real proof upstream")
    if set(result["deferred_candidate_real_assets"]) & set(result["active_asset_ids"]):
        result["reconciles"] = False
        result["blockers"].append("a deferred candidate asset collides with an active asset id")
    return result


# ================================================================ TEN-JOB CONTRACT
# The exact ten creative jobs, in the exact required order. Each spec declares the NATURE of the job
# (which gate is fundamental); the state is then DERIVED from the verified evidence, never hardcoded.
#   kind: EVIDENTIARY (needs verified facts/claims/real proof) | GENERATED_NEUTRAL (a non-evidentiary
#         generated image anchored only on verified concepts) | NOT_APPLICABLE (not supported/relevant)
#   owner_fact_first: for an evidentiary job, whether a missing owner FACT is the earliest gate (the
#         owner must declare an option set before any photograph can even be specified). When False the
#         real photograph itself is the fundamental deliverable (it reveals the attribute).
JOB_SPECS = (
    {
        "job_id": "MAIN_PRODUCT", "position": 1,
        "purpose": "Primary product presentation — the real, finished garment showing the real design.",
        "output_role": "LISTING_MAIN_IMAGE", "usage": "listing",
        "kind": "EVIDENTIARY", "owner_fact_first": False, "real_proof_required": True,
        "generated_mockup_allowed": False, "proof_purpose": "MAIN_PRODUCT_PRIMARY",
        "primary_asset_id": "MAIN_PRODUCT_PRIMARY", "aplus_module_key": None,
        "required_fact_ids": ["garment_type", "color_options", "decoration_method", "design_dimensions",
                              "placement"],
        "required_claim_ids": [], "maps_real_asset_requirement": "REAL-ASSET-002",
        "not_applicable_reason": None,
    },
    {
        "job_id": "REAL_DECORATION_PROOF", "position": 2,
        "purpose": "Prove the real decoration method and physical execution with a macro photograph.",
        "output_role": "LISTING_SUPPORT_IMAGE_AND_APLUS_MODULE", "usage": "both",
        "kind": "EVIDENTIARY", "owner_fact_first": False, "real_proof_required": True,
        "generated_mockup_allowed": False, "proof_purpose": "MACRO_DECORATION_STITCH",
        "primary_asset_id": "MACRO_DECORATION_STITCH", "aplus_module_key": "HERO_EMBROIDERY_PROOF",
        "required_fact_ids": ["decoration_method"],
        "required_claim_ids": ["CLM-DECORATION_METHOD"], "maps_real_asset_requirement": "REAL-ASSET-001",
        "not_applicable_reason": None,
    },
    {
        "job_id": "PERSONALIZATION_GUIDE", "position": 3,
        "purpose": "Explain the verified personalization options and show a real, legible example.",
        "output_role": "LISTING_SUPPORT_IMAGE_AND_APLUS_MODULE", "usage": "both",
        "kind": "EVIDENTIARY", "owner_fact_first": True, "real_proof_required": True,
        "generated_mockup_allowed": False, "proof_purpose": "PERSONALIZATION_EXAMPLE",
        "primary_asset_id": "PERSONALIZATION_EXAMPLE", "aplus_module_key": "PERSONALIZATION_GALLERY",
        "required_fact_ids": ["personalization_fields"],
        "required_claim_ids": ["CLM-PERSONALIZATION_FIELDS"], "maps_real_asset_requirement": "REAL-ASSET-003",
        "not_applicable_reason": None,
    },
    {
        "job_id": "SIZE_AND_FIT", "position": 4,
        "purpose": "Communicate the verified size range and fit from real measured garments.",
        "output_role": "LISTING_SUPPORT_IMAGE_AND_APLUS_MODULE", "usage": "both",
        "kind": "EVIDENTIARY", "owner_fact_first": True, "real_proof_required": True,
        "generated_mockup_allowed": False, "proof_purpose": "GARMENT_OR_COLOR",
        "primary_asset_id": "GARMENT_OR_COLOR", "aplus_module_key": "FIT_SIZE_COLOR_DETAILS",
        "required_fact_ids": ["size_range", "measurements", "fit"],
        "required_claim_ids": ["CLM-SIZE_RANGE", "CLM-MEASUREMENTS", "CLM-FIT"],
        "maps_real_asset_requirement": "REAL-ASSET-006", "not_applicable_reason": None,
    },
    {
        "job_id": "COLOR_AND_GARMENT_OPTIONS", "position": 5,
        "purpose": "Show the actual offered garment and colour options from real supplier evidence.",
        "output_role": "LISTING_SUPPORT_IMAGE_AND_APLUS_MODULE", "usage": "both",
        "kind": "EVIDENTIARY", "owner_fact_first": True, "real_proof_required": True,
        "generated_mockup_allowed": False, "proof_purpose": "GARMENT_OR_COLOR",
        "primary_asset_id": "GARMENT_OR_COLOR", "aplus_module_key": "FIT_SIZE_COLOR_DETAILS",
        "required_fact_ids": ["color_options", "garment_type"],
        "required_claim_ids": ["CLM-COLOR_OPTIONS"], "maps_real_asset_requirement": "REAL-ASSET-004",
        "not_applicable_reason": None,
    },
    {
        "job_id": "LIFESTYLE_RECIPIENT", "position": 6,
        "purpose": "Show a safe recipient / use context anchored only on the verified nurse-recipient concept.",
        "output_role": "LISTING_SUPPORT_IMAGE", "usage": "listing",
        "kind": "GENERATED_NEUTRAL", "owner_fact_first": False, "real_proof_required": False,
        "generated_mockup_allowed": True, "proof_purpose": "LIFESTYLE_NONEVIDENTIARY",
        "primary_asset_id": "LIFESTYLE_GENERATED", "aplus_module_key": None,
        "required_fact_ids": [], "required_claim_ids": ["CLM-RECIPIENT"],
        "maps_real_asset_requirement": None, "not_applicable_reason": None,
    },
    {
        "job_id": "OCCASION_WHEN_SUPPORTED", "position": 7,
        "purpose": "Show an approved occasion — ONLY when an occasion claim is independently verified.",
        "output_role": "LISTING_SUPPORT_IMAGE", "usage": "listing",
        "kind": "NOT_APPLICABLE", "owner_fact_first": True, "real_proof_required": False,
        "generated_mockup_allowed": False, "proof_purpose": "OCCASION",
        "primary_asset_id": None, "aplus_module_key": None,
        "required_fact_ids": ["occasion"], "required_claim_ids": ["CLM-OCCASION"],
        "maps_real_asset_requirement": None,
        "not_applicable_reason": ("No independently verified occasion claim exists. Recipient evidence "
                                  "does not prove an occasion, and gift/occasion claims are blocked, so "
                                  "no occasion image is authorized."),
    },
    {
        "job_id": "PRODUCT_DETAILS_AND_CARE", "position": 8,
        "purpose": "Explain verified physical details and care from real garment-label evidence.",
        "output_role": "LISTING_SUPPORT_IMAGE_AND_APLUS_MODULE", "usage": "both",
        "kind": "EVIDENTIARY", "owner_fact_first": True, "real_proof_required": True,
        "generated_mockup_allowed": False, "proof_purpose": "GARMENT_LABEL_MATERIAL_CARE",
        "primary_asset_id": "GARMENT_LABEL_MATERIAL_CARE", "aplus_module_key": "CARE_PRODUCTION_FAQ",
        "required_fact_ids": ["material_composition", "material", "care_instructions", "packaging",
                              "production_location", "production_time_range"],
        "required_claim_ids": ["CLM-MATERIAL_COMPOSITION", "CLM-CARE"],
        "maps_real_asset_requirement": "REAL-ASSET-005", "not_applicable_reason": None,
    },
    {
        "job_id": "HOW_TO_ORDER", "position": 9,
        "purpose": "Explain a verified ordering / personalization workflow as an instructional graphic.",
        "output_role": "LISTING_SUPPORT_IMAGE_AND_APLUS_MODULE", "usage": "both",
        "kind": "EVIDENTIARY", "owner_fact_first": True, "real_proof_required": False,
        "generated_mockup_allowed": True, "proof_purpose": "ORDER_STEPS_GRAPHIC",
        "primary_asset_id": "ORDER_STEPS_GRAPHIC", "aplus_module_key": "HOW_TO_CUSTOMIZE",
        "required_fact_ids": ["personalization_fields"],
        "required_claim_ids": ["CLM-PERSONALIZATION_FIELDS"], "maps_real_asset_requirement": None,
        "not_applicable_reason": None,
    },
    {
        "job_id": "VERIFIED_COMPARISON_OR_VARIATION", "position": 10,
        "purpose": "Show verified product comparisons or actual offered variations.",
        "output_role": "LISTING_SUPPORT_IMAGE_AND_APLUS_MODULE", "usage": "both",
        "kind": "NOT_APPLICABLE", "owner_fact_first": True, "real_proof_required": False,
        "generated_mockup_allowed": False, "proof_purpose": "VERIFIED_COMPARISON_OR_VARIATION",
        "primary_asset_id": None, "aplus_module_key": None,
        "required_fact_ids": [], "required_claim_ids": [],
        "maps_real_asset_requirement": None,
        "not_applicable_reason": ("No verified variations or comparison products exist. A single "
                                  "unverified product cannot be compared, and generated variations can "
                                  "never prove offered choices."),
    },
)

TEN_JOB_ORDER = tuple(s["job_id"] for s in JOB_SPECS)

# the one safe, non-evidentiary lifestyle scene (positive customer-relevant depiction only). It anchors
# solely on the VERIFIED recipient concept and depicts no unverified product attribute.
_LIFESTYLE_POSITIVE_PROMPT = (
    "A warm, natural, editorial-style lifestyle photograph of a nurse in an everyday, supportive "
    "workplace moment. Soft, even natural light; authentic and respectful human tone. The nurse is the "
    "subject and the focus is the person, not a product; the actual item is not depicted."
)


def _fact_publishable(deps, fid):
    try:
        rec = deps.facts.get(fid)
    except Exception:
        return False
    return bool(rec and rec.get("publishable"))


def _claim_record_by_id(deps, claim_id):
    """Resolve a CLM-* id to its atomic claim record (claim_id_index maps id -> concept)."""
    concept = deps.claims.claim_id_index().get(claim_id)
    if not concept:
        return None
    return deps.claims.claim(concept)


def _claim_publishable(deps, claim_id):
    rec = _claim_record_by_id(deps, claim_id)
    return bool(rec and rec.get("publishable"))


def _resolve_job_state(spec, missing_facts, missing_claims, real_present):
    """Derive the job state from evidence (never hardcoded)."""
    if spec["kind"] == "NOT_APPLICABLE":
        return NOT_APPLICABLE
    if spec["kind"] == "GENERATED_NEUTRAL":
        # a non-evidentiary generated image is prompt-ready ONLY when its anchoring claims are all
        # verified (so nothing unverified is depicted); otherwise the owner facts come first.
        return PROMPT_READY if not missing_claims else OWNER_FACT_REQUIRED
    # evidentiary job -------------------------------------------------------------------------------
    if spec["real_proof_required"]:
        if real_present and not missing_facts and not missing_claims:
            return ASSET_READY
        if spec["owner_fact_first"] and missing_facts:
            return OWNER_FACT_REQUIRED
        return REAL_PHOTO_REQUIRED
    # non-real-proof evidentiary (e.g. an instructional graphic gated on an owner fact)
    if missing_facts:
        return OWNER_FACT_REQUIRED
    if missing_claims:
        return OWNER_FACT_REQUIRED
    return PROMPT_READY


def _overlay_record(deps, text, claim_id, audit_hint):
    """Overlay copy lineage — customer-visible text MUST carry fact/claim lineage. Empty overlay is the
    safe default (NO_SAFE_OVERLAY_COPY)."""
    if not text:
        return {"overlay_copy": "", "state": "NO_SAFE_OVERLAY_COPY",
                "source_field": None, "source_artifact": None, "source_sha256": None,
                "atomic_claim_ids": [], "product_fact_ids": [], "character_count": 0,
                "mobile_readability": "NOT_APPLICABLE", "page_auditor_result": None}
    rec = _claim_record_by_id(deps, claim_id) or {}
    return {
        "overlay_copy": text, "state": "SAFE_VERIFIED_OVERLAY",
        "source_field": "claim_evidence.canonical_claim",
        "source_artifact": "phase6/6A/CLAIM-EVIDENCE.json",
        "source_sha256": deps.claim_evidence_sha256,
        "atomic_claim_ids": [claim_id] if rec else [],
        "product_fact_ids": sorted(rec.get("source_fact_fields", [])) if rec else [],
        "character_count": len(text),
        "mobile_readability": "OK" if len(text) <= 30 else "REVIEW",
        "page_auditor_result": audit_hint,
    }


def _build_job(spec, deps, ctx):
    fid_missing = sorted([f for f in spec["required_fact_ids"] if not _fact_publishable(deps, f)])
    claim_missing = sorted([c for c in spec["required_claim_ids"] if not _claim_publishable(deps, c)])
    real_present = spec.get("primary_asset_id") in ctx["verified_real_assets"]
    state = _resolve_job_state(spec, fid_missing, claim_missing, real_present)

    is_prompt_ready = state == PROMPT_READY
    is_real_photo = state == REAL_PHOTO_REQUIRED
    is_owner_fact = state == OWNER_FACT_REQUIRED
    is_na = state == NOT_APPLICABLE

    # prompt readiness / asset readiness are SEPARATE axes ------------------------------------------
    if is_na:
        prompt_readiness = PR_NOT_APPLICABLE
        asset_readiness = AR_NOT_APPLICABLE
    elif is_prompt_ready:
        prompt_readiness = PR_READY
        asset_readiness = AR_MISSING          # a prompt-ready plan does NOT mean an asset exists
    elif is_real_photo:
        prompt_readiness = PR_BLOCKED         # no AI prompt: a real photograph is required
        asset_readiness = AR_REAL_PHOTO_REQUIRED
    elif is_owner_fact:
        prompt_readiness = PR_OWNER_FACT_REQUIRED
        asset_readiness = (AR_REAL_PHOTO_REQUIRED if spec["real_proof_required"] else AR_OWNER_FACT_REQUIRED)
    else:  # ASSET_READY
        prompt_readiness = PR_READY
        asset_readiness = AR_READY

    missing = [f"owner_fact:{f}" for f in fid_missing]
    missing += [f"blocked_claim:{c}" for c in claim_missing]
    if spec["real_proof_required"] and not real_present:
        missing.append(f"real_photo:{spec['proof_purpose']}")
    if spec.get("maps_real_asset_requirement"):
        missing.append(f"real_asset:{spec['maps_real_asset_requirement']}")
    missing = sorted(set(missing))

    # prompt body: ONLY a PROMPT_READY job receives an AI prompt body ------------------------------
    prompt = None
    if is_prompt_ready and spec["kind"] == "GENERATED_NEUTRAL":
        prompt = _LIFESTYLE_POSITIVE_PROMPT
        prompt_status = "GENERATED_PLANNING_ONLY_NON_EVIDENTIARY"
    elif is_real_photo:
        prompt_status = "NO_PROMPT_REAL_PHOTOGRAPH_REQUIRED"
    elif is_owner_fact:
        prompt_status = "NO_PROMPT_OWNER_FACT_REQUIRED"
    elif is_na:
        prompt_status = "NO_PROMPT_NOT_APPLICABLE"
    else:
        prompt_status = "NO_PROMPT"

    # overlay copy: one genuinely-verified overlay is allowed on the recipient lifestyle image only.
    overlay_text, overlay_claim = "", None
    if spec["job_id"] == "LIFESTYLE_RECIPIENT" and _claim_publishable(deps, "CLM-RECIPIENT"):
        overlay_text = (_claim_record_by_id(deps, "CLM-RECIPIENT") or {}).get("canonical_claim") or ""
        overlay_claim = "CLM-RECIPIENT"
    overlay = _overlay_record(deps, overlay_text, overlay_claim, ctx.get("overlay_audit_hint"))

    blockers = []
    if is_na:
        blockers.append(spec["not_applicable_reason"])
    else:
        blockers.extend(missing)
    blockers = [b for b in blockers if b]

    required_asset_ids = [spec["primary_asset_id"]] if spec.get("primary_asset_id") else []
    expected_filename = (f"{deps.product_id}-L{spec['position']:02d}-{spec['job_id'].lower()}"
                         if not is_na else None)

    return {
        "job_id": spec["job_id"],
        "position": spec["position"],
        "purpose": spec["purpose"],
        "output_role": spec["output_role"],
        "listing_or_aplus_usage": spec["usage"],
        "required_fact_ids": sorted(spec["required_fact_ids"]),
        "required_claim_ids": sorted(spec["required_claim_ids"]),
        "required_asset_ids": required_asset_ids,
        "current_asset_ids": [],
        "current_relative_paths": [],
        "asset_source_hashes": [],
        "real_or_generated": None,
        "generated_mockup_allowed": bool(spec["generated_mockup_allowed"]),
        "real_proof_required": bool(spec["real_proof_required"]),
        "proof_purpose": spec["proof_purpose"],
        "prompt_readiness": prompt_readiness,
        "asset_readiness": asset_readiness,
        "missing_requirements": missing,
        "prompt": prompt,
        "prompt_status": prompt_status,
        "negative_constraints": list(_UNIVERSAL_NEGATIVE_CONSTRAINTS),
        "design_reference": None,
        "design_reference_sha256": None,
        "design_placement": None,
        "design_scale": None,
        "garment_source": None,
        "garment_source_sha256": None,
        "product_color_source": None,
        "composition": None,
        "background_requirements": "CATEGORY_IMAGE_POLICY_UNVERIFIED",
        "lighting_requirements": ("even, true-to-colour light with no colour cast (photography guidance "
                                  "for real evidence)" if spec["real_proof_required"] else None),
        "model_or_hand_requirements": None,
        "props_allowed": [],
        "props_forbidden": ["any brand/logo/trademark", "any invented accessory or included item",
                            "any gift or occasion prop"],
        "image_dimensions": None,
        "aspect_ratio": None,
        "overlay_safe_zone": None,
        "mobile_crop_guidance": None,
        "desktop_crop_guidance": None,
        "overlay_copy": overlay["overlay_copy"],
        "overlay_copy_lineage": overlay,
        "alt_text": "",
        "alt_text_lineage": {"state": "PENDING_ASSET_AND_OWNER_REVIEW",
                             "planned_source_claim_ids": sorted(spec["required_claim_ids"]),
                             "note": "final alt text is authorized only once a verified asset exists and "
                                     "the owner approves it"},
        "cross_image_consistency_ids": ctx["consistency_ids"],
        "expected_filename": expected_filename,
        "output_relative_path": None,
        "aplus_module_key": spec.get("aplus_module_key"),
        "maps_real_asset_requirement": spec.get("maps_real_asset_requirement"),
        "not_applicable_reason": spec.get("not_applicable_reason"),
        "status": state,
        "blockers": blockers,
        "warnings": [],
    }


def build_ten_job_visual_plan(deps, consistency_ids, verified_real_assets, overlay_audit_hint=None):
    ctx = {"consistency_ids": consistency_ids, "verified_real_assets": set(verified_real_assets),
           "overlay_audit_hint": overlay_audit_hint}
    return [_build_job(spec, deps, ctx) for spec in JOB_SPECS]


def _state_summary(jobs):
    out = {s: 0 for s in JOB_STATES}
    for j in jobs:
        out[j["status"]] = out.get(j["status"], 0) + 1
    return out


# ================================================================ CROSS-IMAGE CONSISTENCY
_CONSISTENCY_IDS = ("CIC-GARMENT", "CIC-COLOR", "CIC-DESIGN", "CIC-DECORATION", "CIC-PERSONALIZATION",
                    "CIC-TYPOGRAPHY", "CIC-MODEL", "CIC-LIGHTING", "CIC-BACKGROUND", "CIC-CROP",
                    "CIC-OVERLAY")


def build_cross_image_consistency(deps, category_identity):
    """Shared visual-identity requirements. Unknown values stay null with explicit blockers — never a
    generic default."""
    verified_recipient = (_claim_record_by_id(deps, "CLM-RECIPIENT") or {}).get("canonical_claim") \
        if _claim_publishable(deps, "CLM-RECIPIENT") else None
    return {
        "schema_version": CROSS_IMAGE_SCHEMA_VERSION,
        "workspace_id": deps.workspace_id,
        "product_id": deps.product_id,
        "canonical_product_id": deps.product_id,
        "canonical_category_id": category_identity["canonical_category_id"],
        "marketplace": category_identity["marketplace"],
        "consistency_ids": list(_CONSISTENCY_IDS),
        "canonical_garment_source": None,
        "canonical_garment_type": None,
        "canonical_color_source": None,
        "design_reference_id": None,
        "design_reference_sha256": None,
        "design_dimensions": None,
        "design_placement": None,
        "design_scale": None,
        "typography_source": None,
        "thread_or_decoration_color_source": None,
        "personalization_example_source": None,
        "verified_recipient_concept": verified_recipient,
        "model_use_rules": ("a model/hand may appear only in a non-evidentiary lifestyle image and must "
                            "not assert any unverified product attribute"),
        "lighting_consistency": "even, true-to-colour, no colour cast across all real photographs",
        "background_consistency": "CATEGORY_IMAGE_POLICY_UNVERIFIED",
        "crop_consistency": "CATEGORY_IMAGE_POLICY_UNVERIFIED",
        "overlay_text_style_source": None,
        "forbidden_inconsistencies": [
            "different garment colours across images without a verified colour option",
            "changing the design size across images",
            "changing the design placement across images",
            "changing the personalization text without a stated purpose",
            "depicting embroidery in one image and printing in another",
            "changing the garment style, neckline, or sleeve form",
            "adding a product accessory or included item that is not verified",
            "inconsistent packaging",
            "inconsistent claims across images",
        ],
        "current_readiness": "BLOCKED_OWNER_FACTS_AND_REAL_ASSETS_REQUIRED",
        "blockers": [
            "owner_fact:garment_type", "owner_fact:color_options", "owner_fact:decoration_method",
            "owner_fact:design_dimensions", "owner_fact:placement", "owner_fact:personalization_fields",
            "real_asset:REAL-ASSET-001", "real_asset:REAL-ASSET-002", "real_asset:REAL-ASSET-003",
        ],
        "warnings": [],
    }


# ================================================================ CREATIVE ASSET MANIFEST
# active creative assets (listing + A+ + shared), each represented ONCE. Deferred Premium candidate
# assets are classified separately and are never active production approvals.
def _asset_records(deps, jobs, category_identity, asset_recon):
    by_job = {j["job_id"]: j for j in jobs}
    # map each asset id -> the jobs / A+ modules that reference it
    ref_jobs, ref_modules, roles, real_proof = {}, {}, {}, {}
    for spec in JOB_SPECS:
        aid = spec.get("primary_asset_id")
        if not aid:
            continue
        ref_jobs.setdefault(aid, []).append(spec["job_id"])
        if spec.get("aplus_module_key"):
            ref_modules.setdefault(aid, set()).add(spec["aplus_module_key"])
        roles[aid] = spec["proof_purpose"]
        real_proof[aid] = real_proof.get(aid, False) or spec["real_proof_required"]

    # which real-asset requirement / fact ids each asset needs
    fact_needs = {
        "MAIN_PRODUCT_PRIMARY": ["garment_type", "color_options", "decoration_method"],
        "MACRO_DECORATION_STITCH": ["decoration_method"],
        "PERSONALIZATION_EXAMPLE": ["personalization_fields"],
        "GARMENT_OR_COLOR": ["color_options", "size_range"],
        "GARMENT_LABEL_MATERIAL_CARE": ["material_composition", "care_instructions"],
        "ORDER_STEPS_GRAPHIC": ["personalization_fields"],
        "LIFESTYLE_GENERATED": [],
    }
    generated_allowed = {"LIFESTYLE_GENERATED", "ORDER_STEPS_GRAPHIC"}

    records = []
    for aid in sorted(ref_jobs):
        jobs_for = sorted(ref_jobs[aid])
        modules_for = sorted(ref_modules.get(aid, set()))
        is_shared = (len(jobs_for) > 1) or bool(modules_for)
        scope = "SHARED" if is_shared else "LISTING"
        rp = bool(real_proof.get(aid))
        gen_ok = aid in generated_allowed and not rp
        owner_facts = sorted([f for f in fact_needs.get(aid, []) if not _fact_publishable(deps, f)])
        # asset state: mirror the earliest gate the linked jobs carry, but keep a real-proof asset at
        # REAL_PHOTO_REQUIRED so the manifest never understates the real-photo obligation.
        if rp:
            state = AR_REAL_PHOTO_REQUIRED
        elif owner_facts:
            state = AR_OWNER_FACT_REQUIRED
        else:
            state = AR_MISSING
        missing = [f"owner_fact:{f}" for f in owner_facts]
        if rp:
            missing.append(f"real_photo:{roles[aid]}")
        records.append({
            "asset_id": aid,
            "asset_scope": scope,
            "linked_job_ids": jobs_for,
            "linked_module_keys": modules_for,
            "asset_role": roles[aid],
            "proof_purpose": roles[aid],
            "required_fact_ids": owner_facts,
            "required_claim_ids": sorted({c for j in jobs_for for c in by_job[j]["required_claim_ids"]}),
            "current_relative_path": None,
            "source_type": "GENERATED_PLAN" if gen_ok else "REAL_PHOTOGRAPH",
            "real_or_generated": None,
            "source_sha256": None,
            "valid_bytes": False,
            "width": None,
            "height": None,
            "media_type": None,
            "category_policy_result": category_identity["category_image_policy_state"],
            "generated_mockup_allowed": gen_ok,
            "real_proof_required": rp,
            "prompt_readiness": (PR_READY if aid == "LIFESTYLE_GENERATED" else
                                 (PR_OWNER_FACT_REQUIRED if owner_facts and not rp else PR_BLOCKED)),
            "asset_readiness": state,
            "owner_fact_dependencies": owner_facts,
            "real_asset_dependencies": [f"real_photo:{roles[aid]}"] if rp else [],
            "missing_requirements": sorted(set(missing)),
            "expected_filename": f"{deps.product_id}-asset-{aid.lower()}",
            "state": state,
            "blockers": sorted(set(missing)),
            "warnings": [],
        })

    # deferred Premium candidate assets — classified separately, never active production approvals.
    deferred = []
    for ra in asset_recon["deferred_candidate_real_assets"]:
        deferred.append({
            "asset_id": f"DEFERRED::{ra}",
            "asset_scope": "APLUS",
            "linked_job_ids": [],
            "linked_module_keys": sorted(_deferred_module_for_asset(deps, ra)),
            "asset_role": ra,
            "proof_purpose": ra,
            "classification": "DEFERRED_PREMIUM_CANDIDATE",
            "real_proof_required": True,
            "generated_mockup_allowed": False,
            "real_or_generated": None,
            "source_sha256": None,
            "valid_bytes": False,
            "state": "DEFERRED_NOT_ACTIVE",
            "blockers": ["premium_module_ineligible", f"real_photo:{ra}"],
            "warnings": [],
        })
    return records, deferred


def _deferred_module_for_asset(deps, real_asset):
    out = []
    for c in deps.apl_result.premium_aplus_draft.get("dynamic_candidates", []):
        if real_asset in c.get("required_real_assets", []):
            out.append(c.get("module_key"))
    return out


def build_creative_asset_manifest(deps, jobs, category_identity, asset_recon):
    active, deferred = _asset_records(deps, jobs, category_identity, asset_recon)
    scope_counts = {}
    for a in active:
        scope_counts[a["asset_scope"]] = scope_counts.get(a["asset_scope"], 0) + 1
    doc = {
        "schema_version": ASSET_MANIFEST_SCHEMA_VERSION,
        "workspace_id": deps.workspace_id,
        "product_id": deps.product_id,
        "canonical_category_id": category_identity["canonical_category_id"],
        "marketplace": category_identity["marketplace"],
        "note": ("Listing + A+ creative requirements. NOT the Phase 6F package index. A generated / AI "
                 "asset can satisfy zero real-proof roles; no image file is produced by Phase 6E."),
        "active_asset_count": len(active),
        "active_assets": active,
        "shared_asset_count": sum(1 for a in active if a["asset_scope"] == "SHARED"),
        "listing_asset_count": scope_counts.get("LISTING", 0),
        "aplus_shared_asset_count": scope_counts.get("SHARED", 0),
        "deferred_candidate_asset_count": len(deferred),
        "deferred_candidate_assets": deferred,
        "unique_asset_count": len(active) + len(deferred),
        "current_verified_real_asset_count": sum(1 for a in active if a.get("source_sha256")),
        "generated_asset_count": sum(1 for a in active if a.get("source_sha256")
                                     and a.get("real_or_generated") == "GENERATED"),
        "ai_generated_assets_accepted_as_real_proof": 0,
        "real_proof_required_count": sum(1 for a in active if a["real_proof_required"]),
        "blockers": sorted({b for a in active for b in a["blockers"]}),
        "warnings": [],
    }
    doc["deterministic_content_sha256"] = content_sha256(
        {k: v for k, v in doc.items() if k != "deterministic_content_sha256"})
    return doc


# ================================================================ REAL-PHOTO SHOT LIST
# each shot maps to jobs + A+ assets and reuses a verified Phase 6A real-asset requirement.
_SHOT_SPECS = (
    {"shot_id": "SHOT-01", "real_asset_requirement": "REAL-ASSET-002", "proof_purpose": "MAIN_PRODUCT_PRIMARY",
     "jobs": ["MAIN_PRODUCT"], "assets": ["MAIN_PRODUCT_PRIMARY", "GARMENT_OR_COLOR"], "aplus_assets": [],
     "objective": "Prove the real, finished garment (front) with its true colour and the real design in place.",
     "subject": "the actual finished garment, front, on a plain uncluttered background",
     "distance": "full-garment framing", "angle": "straight-on front",
     "must_show": "the whole real garment and the real design exactly as produced",
     "must_not_conceal": "the true garment colour, the design, or the neckline/sleeve form",
     "facts": ["garment_type", "color_options", "decoration_method"], "claims": [], "classification": "REAL_PHOTOGRAPH_REQUIRED"},
    {"shot_id": "SHOT-02", "real_asset_requirement": "REAL-ASSET-001", "proof_purpose": "MACRO_DECORATION_STITCH",
     "jobs": ["REAL_DECORATION_PROOF"], "assets": ["MACRO_DECORATION_STITCH"], "aplus_assets": ["HERO_EMBROIDERY_PROOF"],
     "objective": "Prove the real decoration method and quality, captured exactly as it appears — never staged or simulated.",
     "subject": "a macro close-up of the actual decoration on the finished product",
     "distance": "macro close-up", "angle": "perpendicular to the decorated area",
     "must_show": "the true texture of the real decoration so the actual method is evident",
     "must_not_conceal": "the real decoration method (do not enhance, retouch, or simulate)",
     "facts": ["decoration_method"], "claims": ["CLM-DECORATION_METHOD"], "classification": "REAL_PHOTOGRAPH_REQUIRED"},
    {"shot_id": "SHOT-03", "real_asset_requirement": "REAL-ASSET-003", "proof_purpose": "PERSONALIZATION_EXAMPLE",
     "jobs": ["PERSONALIZATION_GUIDE", "HOW_TO_ORDER"], "assets": ["PERSONALIZATION_EXAMPLE"], "aplus_assets": ["PERSONALIZATION_GALLERY"],
     "objective": "Prove a real personalized output (only after the owner declares the personalization fields).",
     "subject": "a real personalized example with the name/credentials legible on the finished product",
     "distance": "close, legible framing", "angle": "straight-on to the personalized area",
     "must_show": "the real personalized text exactly as produced",
     "must_not_conceal": "the real legibility and finish of the personalization",
     "facts": ["personalization_fields"], "claims": ["CLM-PERSONALIZATION_FIELDS"], "classification": "REAL_PHOTOGRAPH_REQUIRED"},
    {"shot_id": "SHOT-04", "real_asset_requirement": "REAL-ASSET-004", "proof_purpose": "GARMENT_OR_COLOR",
     "jobs": ["COLOR_AND_GARMENT_OPTIONS"], "assets": ["GARMENT_OR_COLOR"], "aplus_assets": ["FIT_SIZE_COLOR_DETAILS"],
     "objective": "Prove the actual offered colours from real supplier evidence (no digital recolours).",
     "subject": "a real colour swatch / grid from the actual supplier",
     "distance": "swatch/grid framing", "angle": "flat, even",
     "must_show": "every colour that is genuinely offered",
     "must_not_conceal": "the true colour of each option",
     "facts": ["color_options"], "claims": ["CLM-COLOR_OPTIONS"], "classification": "REAL_PHOTOGRAPH_REQUIRED"},
    {"shot_id": "SHOT-05", "real_asset_requirement": "REAL-ASSET-006", "proof_purpose": "GARMENT_OR_COLOR",
     "jobs": ["SIZE_AND_FIT"], "assets": ["GARMENT_OR_COLOR"], "aplus_assets": ["FIT_SIZE_COLOR_DETAILS"],
     "objective": "Prove real measurements / size chart built from real measured garments.",
     "subject": "a size chart built from real measured garments",
     "distance": "chart framing", "angle": "flat, legible",
     "must_show": "the real measured values per size",
     "must_not_conceal": "any measurement or size that is offered",
     "facts": ["size_range", "measurements"], "claims": ["CLM-SIZE_RANGE", "CLM-MEASUREMENTS"], "classification": "REAL_PHOTOGRAPH_REQUIRED"},
    {"shot_id": "SHOT-06", "real_asset_requirement": "REAL-ASSET-005", "proof_purpose": "GARMENT_LABEL_MATERIAL_CARE",
     "jobs": ["PRODUCT_DETAILS_AND_CARE"], "assets": ["GARMENT_LABEL_MATERIAL_CARE"], "aplus_assets": ["CARE_PRODUCTION_FAQ"],
     "objective": "Prove the real garment label showing fibre content and care.",
     "subject": "the real garment label showing fiber content and care",
     "distance": "close, legible framing", "angle": "flat to the label",
     "must_show": "the printed fibre content and care instructions on the real label",
     "must_not_conceal": "any part of the material or care text",
     "facts": ["material_composition", "care_instructions"], "claims": ["CLM-MATERIAL_COMPOSITION", "CLM-CARE"], "classification": "REAL_PHOTOGRAPH_REQUIRED"},
    {"shot_id": "SHOT-07", "real_asset_requirement": "REAL-ASSET-007", "proof_purpose": "PACKAGING",
     "jobs": ["PRODUCT_DETAILS_AND_CARE"], "assets": [], "aplus_assets": ["GIFT_PRESENTATION"],
     "objective": "Prove the real packaging the buyer receives (only if packaging is depicted / claimed).",
     "subject": "the real packaging the buyer receives",
     "distance": "packaging framing", "angle": "representative",
     "must_show": "the real packaging as shipped",
     "must_not_conceal": "the true packaging contents",
     "facts": ["packaging"], "claims": ["CLM-PACKAGING"], "classification": "DEFERRED_OPTIONAL_REAL_PHOTOGRAPH"},
)


def build_real_photo_shot_list_records(deps):
    shots = []
    for s in _SHOT_SPECS:
        shots.append({
            "shot_id": s["shot_id"],
            "proof_purpose": s["proof_purpose"],
            "mapped_job_ids": s["jobs"],
            "mapped_asset_ids": s["assets"],
            "mapped_aplus_asset_ids": s["aplus_assets"],
            "maps_real_asset_requirement": s["real_asset_requirement"],
            "proof_objective": s["objective"],
            "required_product_facts": sorted(s["facts"]),
            "required_claim_ids": sorted(s["claims"]),
            "subject": s["subject"],
            "required_setup": "the real, finished product only — no staging, simulation, or AI generation",
            "camera_distance": s["distance"],
            "camera_angle": s["angle"],
            "lighting": "even, true-to-colour light with no colour cast",
            "background": "plain, uncluttered (owner to confirm the exact Seller Central background rule)",
            "composition": "the real subject fills the frame; nothing misleading added",
            "must_clearly_show": s["must_show"],
            "must_not_conceal": s["must_not_conceal"],
            "scale_or_measurement_reference": ("a real scale/measurement reference"
                                               if "measurements" in s["facts"] or "size_range" in s["facts"]
                                               else None),
            "color_accuracy_requirements": "true-to-life colour; no digital recolour or filter",
            "file_format_requirements": list(_ACCEPTED_PHOTO_FORMATS),
            "minimum_resolution": "UNVERIFIED — confirm Amazon's current image resolution rule in Seller Central",
            "mobile_crop_considerations": "keep the proof subject central and legible on a small screen",
            "expected_filename": f"{deps.product_id}-{s['shot_id']}-{s['proof_purpose'].lower()}",
            "acceptance_criteria": ["a real photograph of THIS finished product",
                                    "the proof subject is clearly and honestly shown",
                                    f"an accepted file format ({', '.join(_ACCEPTED_PHOTO_FORMATS)})",
                                    "the owner confirms it matches the verified facts"],
            "rejection_criteria": ["any AI-generated or digitally simulated image",
                                   "a stock, competitor, or generic supplier image not of THIS product",
                                   "any retouch that changes a physical attribute",
                                   "a missing or illegible proof subject"],
            "owner_review": "REQUIRED",
            "evidence_classification": s["classification"],
            "ai_generated_accepted_as_evidence": False,
        })
    return shots


# ================================================================ PAGE AUDIT (customer-visible creative text)
def run_creative_page_audit(deps, jobs):
    """Screen the customer-visible creative text (prompt-ready scene copy + non-empty overlays) with the
    shared, verification-aware PageAuditor. The internal negative-constraint lists are prohibitions, not
    customer copy, and are NOT audited as claims."""
    visible = []
    for j in jobs:
        if j["prompt_readiness"] == PR_READY and j["prompt"]:
            visible.append(j["prompt"])
        if j["overlay_copy"]:
            visible.append(j["overlay_copy"])
    listing = {
        "schema_version": "2.4", "category": deps.category, "generated_by": "phase6e_creative_planner",
        "title": (f"{deps.audience or ''} {deps.product_type or ''}".strip() or deps.product_type),
        "bullets": ["", "", "", "", ""],
        "description": "\n".join(visible),
        "selected_keywords": {"primary": [], "secondary": [], "backend": []},
        "category_policy": {"category_identifier": deps.policy.category_identifier,
                            "title_hard_limit": deps.policy.title_hard_limit,
                            "backend_byte_ceiling": deps.policy.backend_byte_ceiling},
        "product_fact_source_sha256": deps.facts.source_sha256,
        "aplus": [],
        **deps.keyword_source.listing_metadata(),
    }
    audit = PA.audit_listing(listing, keyword_source=deps.keyword_source, claim_evidence=deps.claims,
                             product_facts=deps.facts, policy=deps.policy)
    kr = audit.get("keyword_results", {})
    cr = audit.get("claim_results", {})
    leak = {
        "wrong_audience_leakage": len(kr.get("wrong_audience_leakage", [])),
        "wrong_product_leakage": len(kr.get("wrong_product_leakage", [])),
        "wrong_occasion_leakage": len(kr.get("wrong_occasion_leakage", [])),
        "brand_ip_leakage": len(kr.get("brand_ip_leakage", [])),
        "unsupported_physical_claims": len(cr.get("unsupported", [])),
        "blocked_claim_promotion": len(cr.get("owner_review_in_copy", [])) + len(cr.get("prohibited", [])),
        "fabricated_owner_facts": len(cr.get("missing_lineage", [])),
    }
    audit["leakage_summary"] = leak
    audit["audited_visible_text_count"] = len(visible)
    return audit


def _leak_total(leak):
    return sum(int(v) for v in leak.values())


# ================================================================ CREATIVE AUDIT
def run_creative_asset_audit(deps, jobs, asset_doc, shots, cross_image, category_identity, asset_recon,
                             page_audit, phase6e_state, ready_for_6f, blockers, warnings):
    state_summary = _state_summary(jobs)
    leak = page_audit["leakage_summary"]
    order_ok = [j["job_id"] for j in jobs] == list(TEN_JOB_ORDER)
    overlay_states = sorted({j["overlay_copy_lineage"]["state"] for j in jobs})
    unsupported_visual_claims = (leak["unsupported_physical_claims"] + leak["blocked_claim_promotion"]
                                 + leak["fabricated_owner_facts"]
                                 + leak["wrong_audience_leakage"] + leak["wrong_product_leakage"]
                                 + leak["wrong_occasion_leakage"] + leak["brand_ip_leakage"])
    listing_prompt_count = sum(1 for j in jobs if j["prompt_readiness"] == PR_READY and j["prompt"]
                               and j["listing_or_aplus_usage"] in ("listing", "both"))
    doc = {
        "schema_version": CREATIVE_AUDIT_SCHEMA_VERSION,
        "workspace_id": deps.workspace_id,
        "product_id": deps.product_id,
        "canonical_category_id": category_identity["canonical_category_id"],
        "display_category": category_identity["display_category"],
        "marketplace": category_identity["marketplace"],
        "category_policy_result": category_identity["category_image_policy_state"],
        "category_reconciliation_result": {
            "canonical_unchanged": category_identity["canonical_unchanged"],
            "registry_reconciled": category_identity["registry_reconciled"],
            "display_category": category_identity["display_category"]},
        "job_count": len(jobs),
        "exact_job_order_result": order_ok,
        "job_order": [j["job_id"] for j in jobs],
        "prompt_ready_count": state_summary[PROMPT_READY],
        "asset_ready_count": state_summary[ASSET_READY],
        "real_photo_required_count": state_summary[REAL_PHOTO_REQUIRED],
        "owner_fact_required_count": state_summary[OWNER_FACT_REQUIRED],
        "not_applicable_count": state_summary[NOT_APPLICABLE],
        "blocked_unsafe_count": state_summary[BLOCKED_UNSAFE_JOB],
        "active_aplus_asset_requirement_count": asset_recon["active_asset_requirement_count"],
        "deferred_aplus_candidate_requirement_count": asset_recon["deferred_candidate_asset_requirement_count"],
        "unique_asset_count": asset_doc["unique_asset_count"],
        "shared_asset_count": asset_doc["shared_asset_count"],
        "current_verified_real_asset_count": asset_doc["current_verified_real_asset_count"],
        "generated_asset_count": asset_doc["generated_asset_count"],
        "ai_generated_assets_accepted_as_real_proof_count": asset_doc["ai_generated_assets_accepted_as_real_proof"],
        "listing_generation_prompt_count": listing_prompt_count,
        "aplus_generation_prompt_count": 0,
        "real_photo_shot_count": len(shots),
        "prompt_safety_result": {"status": page_audit["status"], "leakage_summary": leak,
                                 "audited_visible_text_count": page_audit["audited_visible_text_count"]},
        "overlay_copy_result": {"states": overlay_states,
                                "nonempty_overlays": sum(1 for j in jobs if j["overlay_copy"])},
        "alt_text_result": {"final_alt_text_count": sum(1 for j in jobs if j["alt_text"]),
                            "all_pending_owner_review": all(not j["alt_text"] for j in jobs)},
        "design_reference_result": "OWNER_FACT_REQUIRED",
        "placement_and_scale_result": "OWNER_FACT_REQUIRED",
        "garment_source_result": "OWNER_FACT_REQUIRED",
        "color_source_result": "OWNER_FACT_REQUIRED",
        "image_policy_result": category_identity["category_image_policy_state"],
        "cross_image_consistency_result": cross_image["current_readiness"],
        "unsupported_visual_claim_count": unsupported_visual_claims,
        "page_auditor_result": {"status": page_audit["status"],
                                "publishability": page_audit["publishability"],
                                "hard_failure_count": len(page_audit.get("hard_failures", [])),
                                "leakage_summary": leak},
        "creative_readiness": phase6e_state,
        "listing_publishability": LISTING_SAFE_DRAFT,
        "phase6e_state": phase6e_state,
        "ready_for_6f": ready_for_6f,
        "ready_for_6f_note": ("Phase 6F may compile a safe manual planning package; it does NOT mean "
                              "images are upload-ready, A+ is eligible, or the listing is publishable."),
        "owner_fact_dependencies": sorted({b.split(":", 1)[1] for j in jobs for b in j["blockers"]
                                           if b.startswith("owner_fact:")}),
        "real_asset_dependencies": sorted({b.split(":", 1)[1] for j in jobs for b in j["blockers"]
                                           if b.startswith("real_asset:") or b.startswith("real_photo:")}),
        "blockers": blockers,
        "warnings": warnings,
    }
    doc["deterministic_content_sha256"] = content_sha256(
        {k: v for k, v in doc.items() if k != "deterministic_content_sha256"})
    return doc


# ================================================================ MARKDOWN DOCUMENTS
def _md_listing_image_prompts(deps, jobs):
    L = [f"# Listing Image Prompts — {deps.product_id}", "",
         "Planning document only. This is NOT an image-output folder and no image file is produced.",
         "A complete AI prompt body appears ONLY for a PROMPT_READY job. A real-photo job receives "
         "photography instructions (see `REAL_PHOTO_SHOT_LIST.md`); an owner-fact / not-applicable / "
         "blocked job receives NO prompt.", ""]
    for j in jobs:
        L += [f"## {j['position']}. {j['job_id']} — {j['status']}",
              f"- Purpose: {j['purpose']}",
              f"- Prompt readiness: **{j['prompt_readiness']}**  |  Asset readiness: **{j['asset_readiness']}**",
              f"- Usage: {j['listing_or_aplus_usage']}  |  Real proof required: {j['real_proof_required']}"
              f"  |  Generated mockup allowed: {j['generated_mockup_allowed']}"]
        if j["required_asset_ids"]:
            L.append(f"- Required asset(s): {', '.join(j['required_asset_ids'])}")
        if j["status"] == PROMPT_READY and j["prompt"]:
            L += ["", "**AI PROMPT (planning only, non-evidentiary, owner review required):**", "",
                  "```", j["prompt"], "```", "",
                  "Negative constraints:"]
            L += [f"- {c}" for c in j["negative_constraints"]]
            if j["overlay_copy"]:
                L += ["", f"Permitted overlay (verified claim only): \"{j['overlay_copy']}\" "
                      f"(source: {', '.join(j['overlay_copy_lineage']['atomic_claim_ids'])})"]
        elif j["status"] == REAL_PHOTO_REQUIRED:
            L += ["", "**REAL PHOTOGRAPH REQUIRED — no AI prompt.** See the shot list for capture "
                  f"instructions (maps {j.get('maps_real_asset_requirement')}).",
                  "Reasons:"]
            L += [f"- {b}" for b in j["blockers"]]
        else:
            L += ["", "**NO PROMPT GENERATED**", "Reasons:"]
            if j["status"] == NOT_APPLICABLE:
                L.append(f"- NOT_APPLICABLE: {j['not_applicable_reason']}")
            else:
                L += [f"- {b}" for b in j["blockers"]]
        L.append("")
    L += ["---", "The owner remains the only manual bridge to Seller Central. No image was generated."]
    return "\n".join(L) + "\n"


def _md_aplus_image_prompts(deps, asset_recon):
    apl = deps.apl_result
    active = apl.asset_manifest["assets"]
    L = [f"# A+ Image Prompts — {deps.product_id}", "",
         "Derived from `phase6/6D/APLUS-ASSET-MANIFEST.json`. A final A+ generation prompt appears ONLY "
         "for a PROMPT_READY A+ asset. Phase 6D reported 0 PROMPT_READY A+ assets, so this document "
         "truthfully contains **0 final A+ generation prompts** — with complete blocker and photography "
         "sections. No filler prompt is created.", "",
         f"- Active A+ asset requirements: {asset_recon['active_asset_requirement_count']}",
         f"- A+ generation prompts: 0", "",
         "## ACTIVE A+ ASSET REQUIREMENTS", ""]
    for a in sorted(active, key=lambda x: (x.get("module_position") or 99, x.get("asset_id") or "")):
        L += [f"### {a['asset_id']} — {a['status']}",
              f"- Module: {a['module_key']} (position {a.get('module_position')})",
              f"- Purpose / proof: {a.get('proof_purpose')}",
              f"- Prompt readiness: **{a.get('prompt_readiness')}**  |  Asset readiness: **{a.get('asset_readiness')}**",
              f"- Real photo required: {a.get('real_photo_required')}  |  Generated mockup allowed: {a.get('generated_mockup_allowed')}"]
        if a.get("real_photo_required"):
            L.append("- **REAL PHOTOGRAPH REQUIRED — no AI prompt.** See `REAL_PHOTO_SHOT_LIST.md`.")
        else:
            L.append("- **OWNER FACT REQUIRED — no AI prompt** until the owner supplies the facts below.")
        if a.get("required_fact_ids"):
            L.append(f"- Owner facts required: {', '.join(a['required_fact_ids'])}")
        L.append(f"- Blockers: {', '.join(a.get('blockers', []))}")
        L.append("- Negative constraints: " + "; ".join(a.get("prohibited_visual_claims", [])))
        L.append("")
    L += ["## DEFERRED PREMIUM CANDIDATE ASSETS",
          "These are tied to rejected / unavailable Premium dynamic modules. They are NOT active "
          "production approvals and receive no prompt.", ""]
    for ra in asset_recon["deferred_candidate_real_assets"]:
        mods = ", ".join(sorted(_deferred_module_for_asset(deps, ra)))
        L.append(f"- **{ra}** — deferred (modules: {mods}); real photograph required if ever activated.")
    L += ["", "---", "No image was generated. A generated / AI asset can satisfy zero real-proof gates."]
    return "\n".join(L) + "\n"


def _md_real_photo_shot_list(deps, shots):
    L = [f"# Real Photo Shot List — {deps.product_id}", "",
         "What real photographic evidence the owner must capture. Detailed even where facts are still "
         "missing, because its purpose is to tell the owner what real proof is required. An AI-generated "
         "image is NEVER accepted as evidence here.", ""]
    for s in shots:
        L += [f"## {s['shot_id']} — {s['evidence_classification']}",
              f"- Maps: jobs {s['mapped_job_ids']}; A+ assets {s['mapped_aplus_asset_ids']}; "
              f"real-asset requirement {s['maps_real_asset_requirement']}",
              f"- Proof objective: {s['proof_objective']}",
              f"- Required facts: {', '.join(s['required_product_facts']) or '(none)'}"
              f"  |  Required claims: {', '.join(s['required_claim_ids']) or '(none)'}",
              f"- Subject: {s['subject']}",
              f"- Setup: {s['required_setup']}",
              f"- Camera: {s['camera_distance']}, {s['camera_angle']}; lighting: {s['lighting']}",
              f"- Background: {s['background']}",
              f"- Must clearly show: {s['must_clearly_show']}",
              f"- Must not conceal: {s['must_not_conceal']}",
              f"- Colour accuracy: {s['color_accuracy_requirements']}",
              f"- File format: {', '.join(s['file_format_requirements'])}  |  Min resolution: {s['minimum_resolution']}",
              f"- Acceptance: {'; '.join(s['acceptance_criteria'])}",
              f"- Rejection: {'; '.join(s['rejection_criteria'])}",
              f"- Owner review: {s['owner_review']}  |  AI accepted as evidence: {s['ai_generated_accepted_as_evidence']}",
              ""]
    L += ["---", "AI-generated images do not belong in this list as evidence."]
    return "\n".join(L) + "\n"


def _md_creative_brief(deps, jobs, category_identity, asset_recon, phase6e_state):
    ss = _state_summary(jobs)
    L = [f"# Creative Brief — {deps.product_id}", "",
         f"- Product identity: **{deps.product_type}** for **{deps.audience}**",
         f"- Canonical category: **{category_identity['canonical_category_id']}** "
         f"(display: {category_identity['display_category']}, marketplace: {category_identity['marketplace']})",
         f"- Phase 6E state: **{phase6e_state}**  |  Listing: **{LISTING_SAFE_DRAFT}**  |  ready_for_6f: true",
         "", "## Verified vs blocked",
         "- Verified: the neutral nurse-recipient concept (\"For nurses.\").",
         "- Blocked / unverified: personalization, gift, occasion, decoration method, material, sizes, "
         "measurements, colours, fit, care, packaging, production, and all physical-quality claims. No "
         "real product photograph is verified.", "",
         "## Current safe copy (from Phase 6C)",
         "- Title: Nurse Sweatshirt", "- Description: A nurse sweatshirt. For nurses.",
         "- Bullets: five records, all empty with explicit blockers.", "",
         "## Ten creative jobs", ""]
    for j in jobs:
        L.append(f"{j['position']}. **{j['job_id']}** — {j['status']} "
                 f"(prompt: {j['prompt_readiness']}, asset: {j['asset_readiness']})")
    L += ["",
          f"- PROMPT_READY: {ss[PROMPT_READY]}  |  REAL_PHOTO_REQUIRED: {ss[REAL_PHOTO_REQUIRED]}  |  "
          f"OWNER_FACT_REQUIRED: {ss[OWNER_FACT_REQUIRED]}  |  NOT_APPLICABLE: {ss[NOT_APPLICABLE]}  |  "
          f"ASSET_READY: {ss[ASSET_READY]}  |  BLOCKED_UNSAFE: {ss[BLOCKED_UNSAFE_JOB]}",
          f"- Active A+ asset requirements: {asset_recon['active_asset_requirement_count']}  |  "
          f"deferred Premium candidates: {asset_recon['deferred_candidate_asset_requirement_count']}", "",
          "## Prohibited claims",
          "No invented colour, garment source, decoration, material, size, fit, care, packaging, gift, "
          "occasion, personalization, or quality claim. A generated mockup can prove none of these.", "",
          "## Visual consistency",
          "Garment, colour, design reference, placement, scale, and decoration method must stay identical "
          "across every image — all currently null pending owner facts and real assets.", "",
          "## Asset naming",
          f"Planned base names use `{deps.product_id}-L<NN>-<job>` (listing) and "
          f"`{deps.product_id}-<SHOT-ID>-<purpose>` (real photos). No file exists yet.", "",
          "## Owner review workflow",
          "1) Supply the blocking owner facts. 2) Capture the real photographs in the shot list. "
          "3) Re-run the evidence gates. 4) Only then may any image be approved. ", "",
          "## Phase 6F handoff",
          "Phase 6F may compile a safe manual package that separates safe draft copy, owner review, "
          "missing facts, real-photo requirements, and do-not-paste content. It does not publish, and "
          "the owner remains the only manual bridge to Seller Central.", ""]
    return "\n".join(L) + "\n"


# ================================================================ CHECKLIST
def build_creative_asset_checklist(deps, jobs, asset_recon):
    ai_rejected = ["AI-generated image or mockup", "digital rendering / design mockup",
                   "stock or competitor image", "generic supplier catalog image not of THIS product"]
    items = [
        ("CHK-001", "Category image policy verification", ["MAIN_PRODUCT"], [],
         "CATEGORY_IMAGE_POLICY_UNVERIFIED", ["owner Seller Central confirmation"]),
        ("CHK-002", "A+ eligibility confirmation", [], [], "UNVERIFIED", ["owner Amazon eligibility confirmation"]),
        ("CHK-003", "Garment source", ["MAIN_PRODUCT", "COLOR_AND_GARMENT_OPTIONS"], ["FIT_SIZE_COLOR_DETAILS"],
         "OWNER_FACT_REQUIRED", ["supplier specification", "real garment photo"]),
        ("CHK-004", "Material / fabric composition", ["PRODUCT_DETAILS_AND_CARE"], ["CARE_PRODUCTION_FAQ"],
         "OWNER_FACT_REQUIRED", ["supplier spec sheet", "real garment label photo"]),
        ("CHK-005", "Sizes offered", ["SIZE_AND_FIT"], ["FIT_SIZE_COLOR_DETAILS"], "OWNER_FACT_REQUIRED",
         ["supplier size list", "verified size chart"]),
        ("CHK-006", "Measurements", ["SIZE_AND_FIT"], ["FIT_SIZE_COLOR_DETAILS"], "OWNER_FACT_REQUIRED",
         ["measured garment dimensions"]),
        ("CHK-007", "Colour list", ["COLOR_AND_GARMENT_OPTIONS"], ["FIT_SIZE_COLOR_DETAILS"],
         "OWNER_FACT_REQUIRED", ["supplier colour list", "real colour swatch"]),
        ("CHK-008", "Decoration method", ["REAL_DECORATION_PROOF"], ["HERO_EMBROIDERY_PROOF"],
         "OWNER_FACT_REQUIRED", ["supplier decoration spec", "real decoration macro photo"]),
        ("CHK-009", "Real decoration macro", ["REAL_DECORATION_PROOF"], ["HERO_EMBROIDERY_PROOF"],
         "REAL_PHOTO_REQUIRED", ["real macro photo of the finished product"]),
        ("CHK-010", "Design reference", ["MAIN_PRODUCT"], [], "OWNER_FACT_REQUIRED",
         ["owner-supplied design file / reference"]),
        ("CHK-011", "Design dimensions", ["MAIN_PRODUCT"], [], "OWNER_FACT_REQUIRED", ["owner-stated design dimensions"]),
        ("CHK-012", "Design placement", ["MAIN_PRODUCT"], [], "OWNER_FACT_REQUIRED", ["owner-stated placement"]),
        ("CHK-013", "Personalization fields", ["PERSONALIZATION_GUIDE", "HOW_TO_ORDER"], ["PERSONALIZATION_GALLERY", "HOW_TO_CUSTOMIZE"],
         "OWNER_FACT_REQUIRED", ["owner-stated fields and character limits"]),
        ("CHK-014", "Personalization example", ["PERSONALIZATION_GUIDE"], ["PERSONALIZATION_GALLERY"],
         "REAL_PHOTO_REQUIRED", ["real personalized example photo"]),
        ("CHK-015", "Fit evidence", ["SIZE_AND_FIT"], ["FIT_SIZE_COLOR_DETAILS"], "OWNER_FACT_REQUIRED",
         ["supplier fit spec", "owner-confirmed fit from a real sample"]),
        ("CHK-016", "Care evidence", ["PRODUCT_DETAILS_AND_CARE"], ["CARE_PRODUCTION_FAQ"], "OWNER_FACT_REQUIRED",
         ["care label photo", "supplier care spec"]),
        ("CHK-017", "Production evidence", ["PRODUCT_DETAILS_AND_CARE"], ["CARE_PRODUCTION_FAQ"], "OWNER_FACT_REQUIRED",
         ["supplier production statement for THIS product"]),
        ("CHK-018", "Packaging evidence", ["PRODUCT_DETAILS_AND_CARE"], ["GIFT_PRESENTATION"], "OWNER_FACT_REQUIRED",
         ["real packaging photo"]),
        ("CHK-019", "Shipping evidence (if depicted)", ["PRODUCT_DETAILS_AND_CARE"], [], "OWNER_FACT_REQUIRED",
         ["owner-confirmed shipping method"]),
        ("CHK-020", "Actual variations", ["VERIFIED_COMPARISON_OR_VARIATION"], [], "NOT_APPLICABLE",
         ["real offered variations"]),
        ("CHK-021", "Real comparison products", ["VERIFIED_COMPARISON_OR_VARIATION"], [], "NOT_APPLICABLE",
         ["≥2 real, named comparison products with verified attributes"]),
        ("CHK-022", "Owner creative approval", [j["job_id"] for j in jobs], [], "OWNER_FACT_REQUIRED",
         ["explicit owner sign-off on the final creative"]),
    ]
    checklist = []
    for cid, req, jobs_for, aplus_for, state, accepted in items:
        checklist.append({
            "checklist_id": cid,
            "requirement": req,
            "linked_job_ids": jobs_for,
            "linked_aplus_assets": aplus_for,
            "current_state": state,
            "accepted_evidence_types": accepted,
            "rejected_evidence_types": ai_rejected,
            "owner_action": "supply the accepted evidence, then re-run the evidence gates",
            "blocker_severity": ("BLOCKING" if state in ("OWNER_FACT_REQUIRED", "REAL_PHOTO_REQUIRED")
                                 else "INFO"),
        })
    return {
        "schema_version": CHECKLIST_SCHEMA_VERSION,
        "workspace_id": deps.workspace_id, "product_id": deps.product_id,
        "note": "AI mockups are rejected evidence for every physical-proof item.",
        "item_count": len(checklist),
        "checklist": checklist,
    }


# ================================================================ ORCHESTRATION
class CreativeProductionPackageResult:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def canonical_content(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "workspace_id": self.workspace_id,
            "product_id": self.product_id,
            "canonical_category_id": self.canonical_category_id,
            "display_category": self.display_category,
            "marketplace": self.marketplace,
            "phase6c_manifest_sha256": self.phase6c_manifest_sha256,
            "phase6d_manifest_sha256": self.phase6d_manifest_sha256,
            "product_facts_sha256": self.product_facts_sha256,
            "claim_evidence_sha256": self.claim_evidence_sha256,
            "keyword_source_sha256": self.keyword_source_sha256,
            "ten_job_contract_version": self.ten_job_contract_version,
            "jobs": self.jobs,
            "listing_prompt_records": self.listing_prompt_records,
            "aplus_prompt_records": self.aplus_prompt_records,
            "real_photo_shots": self.real_photo_shots,
            "asset_manifest": self.asset_manifest,
            "cross_image_consistency": self.cross_image_consistency,
            "creative_audit": self.creative_audit,
            "owner_fact_dependencies": self.owner_fact_dependencies,
            "real_asset_dependencies": self.real_asset_dependencies,
            "listing_publishability_state": self.listing_publishability_state,
            "phase6e_state": self.phase6e_state,
            "ready_for_6f": self.ready_for_6f,
            "blockers": self.blockers,
            "warnings": self.warnings,
        }

    def deterministic_content_sha256(self):
        return content_sha256(self.canonical_content())

    def to_dict(self):
        d = self.canonical_content()
        d["deterministic_content_sha256"] = self.deterministic_content_sha256()
        d["generated_artifacts"] = list(REQUIRED_ARTIFACTS) + list(SUPPORT_ARTIFACTS) + [STAGE_MANIFEST_FILENAME]
        return d


def _listing_image_plan_doc(deps, jobs, category_identity, cross_image):
    ss = _state_summary(jobs)
    owner_fact_deps = sorted({b.split(":", 1)[1] for j in jobs for b in j["blockers"]
                              if b.startswith("owner_fact:")})
    real_asset_deps = sorted({b.split(":", 1)[1] for j in jobs for b in j["blockers"]
                              if b.startswith("real_asset:") or b.startswith("real_photo:")})
    doc = {
        "schema_version": LISTING_IMAGE_PLAN_SCHEMA_VERSION,
        "workspace_id": deps.workspace_id,
        "product_id": deps.product_id,
        "canonical_category_id": category_identity["canonical_category_id"],
        "display_category": category_identity["display_category"],
        "marketplace": category_identity["marketplace"],
        "category_policy_source": category_identity["category_policy_source"],
        "category_policy_sha256": category_identity["category_policy_sha256"],
        "category_image_policy_state": category_identity["category_image_policy_state"],
        "ten_job_contract_version": TEN_JOB_CONTRACT_VERSION,
        "job_count": len(jobs),
        "jobs": jobs,
        "state_summary": ss,
        "prompt_ready_count": ss[PROMPT_READY],
        "asset_ready_count": ss[ASSET_READY],
        "real_photo_required_count": ss[REAL_PHOTO_REQUIRED],
        "owner_fact_required_count": ss[OWNER_FACT_REQUIRED],
        "not_applicable_count": ss[NOT_APPLICABLE],
        "blocked_unsafe_count": ss[BLOCKED_UNSAFE_JOB],
        "owner_fact_dependencies": owner_fact_deps,
        "real_asset_dependencies": real_asset_deps,
        "listing_publishability_state": LISTING_SAFE_DRAFT,
        "creative_readiness_state": SAFE_CREATIVE_PLAN_OWNER_FACTS_REQUIRED,
        "ready_for_6f": True,
        "blockers": deps_plan_blockers(jobs),
        "warnings": [],
    }
    doc["deterministic_content_sha256"] = content_sha256(
        {k: v for k, v in doc.items() if k != "deterministic_content_sha256"})
    return doc


def deps_plan_blockers(jobs):
    return sorted({b for j in jobs for b in j["blockers"] if ":" in b})


def _aplus_prompt_records(deps, asset_recon):
    """Structured A+ prompt records (mirrors the .md). 0 final prompts for T2."""
    active = deps.apl_result.asset_manifest["assets"]
    records = []
    for a in active:
        records.append({
            "asset_id": a["asset_id"], "module_key": a.get("module_key"),
            "module_state": a.get("status"), "purpose": a.get("proof_purpose"),
            "prompt_readiness": a.get("prompt_readiness"), "asset_readiness": a.get("asset_readiness"),
            "real_photo_required": a.get("real_photo_required"),
            "required_evidence": a.get("required_fact_ids", []),
            "prompt": None, "prompt_status": ("NO_PROMPT_REAL_PHOTOGRAPH_REQUIRED"
                                              if a.get("real_photo_required")
                                              else "NO_PROMPT_OWNER_FACT_REQUIRED"),
            "negative_constraints": a.get("prohibited_visual_claims", []),
            "expected_filename": None,
            "blockers": a.get("blockers", []),
        })
    return {
        "active_prompt_records": records,
        "aplus_generation_prompt_count": 0,
        "deferred_premium_candidate_assets": asset_recon["deferred_candidate_real_assets"],
    }


def assemble_phase6e(run_dir, connectivity_mode="CONNECTED_RESEARCH"):
    """Assemble the whole Phase 6E creative planning package IN MEMORY (no disk writes here).
    Deterministic + network-free; connectivity_mode never influences the stable content."""
    deps = load_phase6e_dependencies(run_dir, connectivity_mode=connectivity_mode)
    category_identity = reconcile_category_identity(deps)
    asset_recon = reconcile_phase6d_asset_requirements(deps)
    if not asset_recon["reconciles"]:
        raise Phase6EAssemblyError(f"Phase 6D asset reconciliation failed: {asset_recon['blockers']}")

    # verified real assets (none exist for a workspace still awaiting real photos).
    verified_real_assets = {a["asset_requirement_id"] for a in deps.workspace.get("real_asset_requirements", [])
                            if a.get("state") == "RESOLVED"}

    cross_image = build_cross_image_consistency(deps, category_identity)
    jobs = build_ten_job_visual_plan(deps, list(_CONSISTENCY_IDS), verified_real_assets)

    # audit the customer-visible creative text, then re-stamp overlay audit hints from the same result.
    page_audit = run_creative_page_audit(deps, jobs)
    hint = {"status": page_audit["status"], "leakage_total": _leak_total(page_audit["leakage_summary"])}
    jobs = build_ten_job_visual_plan(deps, list(_CONSISTENCY_IDS), verified_real_assets,
                                     overlay_audit_hint=hint)

    asset_doc = build_creative_asset_manifest(deps, jobs, category_identity, asset_recon)
    shots = build_real_photo_shot_list_records(deps)
    aplus_prompts = _aplus_prompt_records(deps, asset_recon)

    # safety gates ---------------------------------------------------------------------------------
    leak = page_audit["leakage_summary"]
    unsafe = (bool(page_audit.get("hard_failures")) or _leak_total(leak) > 0
              or asset_doc["ai_generated_assets_accepted_as_real_proof"] != 0
              or asset_recon["generated_accepted_as_real_proof_count"] != 0)

    blockers, warnings = [], []
    if unsafe:
        phase6e_state = BLOCKED_UNSAFE
        ready_for_6f = False
        blockers.append("creative page audit is not clean; creative package blocked")
    else:
        phase6e_state = SAFE_CREATIVE_PLAN_OWNER_FACTS_REQUIRED
        ready_for_6f = True
    owner_fact_deps = sorted({b.split(":", 1)[1] for j in jobs for b in j["blockers"]
                              if b.startswith("owner_fact:")})
    real_asset_deps = sorted({b.split(":", 1)[1] for j in jobs for b in j["blockers"]
                              if b.startswith("real_asset:") or b.startswith("real_photo:")})
    if owner_fact_deps:
        blockers.append("owner facts required: " + ", ".join(owner_fact_deps))
    if real_asset_deps:
        blockers.append("real photographs required: " + ", ".join(real_asset_deps))

    creative_audit = run_creative_asset_audit(deps, jobs, asset_doc, shots, cross_image,
                                              category_identity, asset_recon, page_audit,
                                              phase6e_state, ready_for_6f, blockers, warnings)
    listing_prompt_records = [{"job_id": j["job_id"], "status": j["status"],
                               "prompt_readiness": j["prompt_readiness"], "prompt": j["prompt"],
                               "prompt_status": j["prompt_status"]} for j in jobs]

    return CreativeProductionPackageResult(
        run_dir=run_dir, connectivity_mode=connectivity_mode, schema_version=SCHEMA_VERSION,
        workspace_id=deps.workspace_id, product_id=deps.product_id,
        canonical_category_id=category_identity["canonical_category_id"],
        display_category=category_identity["display_category"], marketplace=category_identity["marketplace"],
        phase6c_manifest_sha256=deps.phase6c_manifest_sha256,
        phase6d_manifest_sha256=deps.phase6d_manifest_sha256,
        product_facts_sha256=deps.product_facts_sha256, claim_evidence_sha256=deps.claim_evidence_sha256,
        keyword_source_sha256=deps.keyword_source_sha256, ten_job_contract_version=TEN_JOB_CONTRACT_VERSION,
        jobs=jobs, listing_prompt_records=listing_prompt_records, aplus_prompt_records=aplus_prompts,
        real_photo_shots=shots, asset_manifest=asset_doc, cross_image_consistency=cross_image,
        creative_audit=creative_audit, category_identity=category_identity, asset_recon=asset_recon,
        owner_fact_dependencies=owner_fact_deps, real_asset_dependencies=real_asset_deps,
        listing_publishability_state=LISTING_SAFE_DRAFT, phase6e_state=phase6e_state,
        ready_for_6f=ready_for_6f, blockers=blockers, warnings=warnings, page_audit=page_audit, _deps=deps)


# ================================================================ WRITE / VERIFY
def _stage_manifest_doc(result, output_hashes, *, started_at=None, completed_at=None,
                        previous_valid_manifest_sha256=None, verification=None):
    ss = _state_summary(result.jobs)
    ar = result.asset_recon
    body = {
        "schema_version": STAGE_MANIFEST_SCHEMA_VERSION,
        "stage_id": STAGE_ID,
        "stage_name": STAGE_NAME,
        "workspace_id": result.workspace_id,
        "product_id": result.product_id,
        "state": result.phase6e_state,
        "required_inputs": ["phase6/6A/*", "phase6/6B/*", "phase6/6C/*", "phase6/6D/*"],
        "input_hashes": {
            "phase6c_manifest_sha256": result.phase6c_manifest_sha256,
            "phase6d_manifest_sha256": result.phase6d_manifest_sha256,
            "product_facts_sha256": result.product_facts_sha256,
            "claim_evidence_sha256": result.claim_evidence_sha256,
            "keyword_source_sha256": result.keyword_source_sha256,
        },
        "phase6c_manifest_sha256": result.phase6c_manifest_sha256,
        "phase6d_manifest_sha256": result.phase6d_manifest_sha256,
        "product_facts_sha256": result.product_facts_sha256,
        "claim_evidence_sha256": result.claim_evidence_sha256,
        "keyword_source_sha256": result.keyword_source_sha256,
        "canonical_category_id": result.canonical_category_id,
        "marketplace": result.marketplace,
        "ten_job_contract_version": result.ten_job_contract_version,
        "generated_outputs": sorted(output_hashes),
        "output_hashes": output_hashes,
        "job_count": len(result.jobs),
        "prompt_ready_count": ss[PROMPT_READY],
        "asset_ready_count": ss[ASSET_READY],
        "real_photo_required_count": ss[REAL_PHOTO_REQUIRED],
        "owner_fact_required_count": ss[OWNER_FACT_REQUIRED],
        "not_applicable_count": ss[NOT_APPLICABLE],
        "blocked_unsafe_count": ss[BLOCKED_UNSAFE_JOB],
        "active_aplus_asset_count": ar["active_asset_requirement_count"],
        "deferred_aplus_candidate_asset_count": ar["deferred_candidate_asset_requirement_count"],
        "unique_asset_count": result.asset_manifest["unique_asset_count"],
        "verified_real_asset_count": result.asset_manifest["current_verified_real_asset_count"],
        "AI_generated_proof_acceptance_count": result.asset_manifest["ai_generated_assets_accepted_as_real_proof"],
        "blocker_count": len(result.blockers),
        "warning_count": len(result.warnings),
        "listing_publishability_state": result.listing_publishability_state,
        "ready_for_6f": result.ready_for_6f,
        "deterministic_content_sha256": None,
        "previous_valid_manifest_sha256": previous_valid_manifest_sha256,
        "verification": verification or {},
        "errors": [],
    }
    body["deterministic_content_sha256"] = content_sha256(_manifest_hashable(body))
    if started_at:
        body["started_at"] = started_at
    if completed_at:
        body["completed_at"] = completed_at
    return body


def _previous_manifest_hash(dest_dir):
    p = os.path.join(dest_dir, STAGE_MANIFEST_FILENAME)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("deterministic_content_sha256")
    except (OSError, json.JSONDecodeError):
        return None


def write_phase6e_artifacts(result, dest_dir=None, *, workspace_dir=None, started_at=None,
                            completed_at=None, verification=None):
    """Build every document + exact bytes IN MEMORY, validate all, compute output hashes, THEN commit
    each file atomically. A validation failure raises and leaves the last valid Phase 6E run intact."""
    workspace_dir = workspace_dir or result.run_dir
    dest_dir = dest_dir or phase6e_dir(workspace_dir)
    deps = result._deps

    plan_doc = _listing_image_plan_doc(deps, result.jobs, result.category_identity,
                                       result.cross_image_consistency)
    checklist_doc = build_creative_asset_checklist(deps, result.jobs, result.asset_recon)
    content = [
        (LISTING_IMAGE_PLAN_FILENAME, canonical_json(plan_doc)),
        (LISTING_IMAGE_PROMPTS_FILENAME, _md_listing_image_prompts(deps, result.jobs)),
        (APLUS_IMAGE_PROMPTS_FILENAME, _md_aplus_image_prompts(deps, result.asset_recon)),
        (REAL_PHOTO_SHOT_LIST_FILENAME, _md_real_photo_shot_list(deps, result.real_photo_shots)),
        (CREATIVE_ASSET_MANIFEST_FILENAME, canonical_json(result.asset_manifest)),
        (CREATIVE_ASSET_AUDIT_FILENAME, canonical_json(result.creative_audit)),
        (CREATIVE_ASSET_CHECKLIST_FILENAME, canonical_json(checklist_doc)),
        (CREATIVE_BRIEF_FILENAME, _md_creative_brief(deps, result.jobs, result.category_identity,
                                                     result.asset_recon, result.phase6e_state)),
        (CROSS_IMAGE_CONSISTENCY_FILENAME, canonical_json(result.cross_image_consistency)),
    ]
    output_hashes = {}
    for name, text in content:
        rel = _safe_rel(os.path.join(dest_dir, name), workspace_dir)
        output_hashes[rel] = _sha_text(text)

    prev = _previous_manifest_hash(dest_dir)
    manifest = _stage_manifest_doc(result, output_hashes, started_at=started_at, completed_at=completed_at,
                                   previous_valid_manifest_sha256=prev, verification=verification)

    # validate everything before touching disk -----------------------------------------------------
    _raise_on_errors("LISTING-IMAGE-PLAN", validate_listing_image_plan(plan_doc))
    _raise_on_errors("CREATIVE-ASSET-MANIFEST", validate_creative_asset_manifest(result.asset_manifest))
    _raise_on_errors("CREATIVE-ASSET-AUDIT", validate_creative_audit(result.creative_audit, plan_doc,
                                                                     result.asset_manifest))
    _raise_on_errors("REAL_PHOTO_SHOT_LIST", validate_shot_list(result.real_photo_shots, result.jobs))
    _raise_on_errors("PROMPT-PACKAGE", validate_prompt_package(result.jobs, aplus_generation_prompt_count=0))
    _raise_on_errors("STAGE-6E-MANIFEST", validate_stage6e_manifest(manifest, workspace_dir))
    _forbid_forbidden_outputs(dest_dir)

    os.makedirs(dest_dir, exist_ok=True)
    for name, text in content:
        _atomic_write_text(os.path.join(dest_dir, name), text)
    _atomic_write_json(os.path.join(dest_dir, STAGE_MANIFEST_FILENAME), manifest)
    return manifest


def verify_phase6e_artifacts(dest_dir, workspace_dir=None):
    workspace_dir = workspace_dir or os.path.dirname(os.path.dirname(os.path.abspath(dest_dir)))
    errors = []
    for name in REQUIRED_ARTIFACTS:
        if not os.path.exists(os.path.join(dest_dir, name)):
            errors.append(f"missing required artifact: {name}")
    man_path = os.path.join(dest_dir, STAGE_MANIFEST_FILENAME)
    if not os.path.exists(man_path):
        errors.append("missing STAGE-6E-MANIFEST.json")
        return {"ok": False, "errors": errors, "checked": []}
    manifest = _load_json(man_path)
    errors.extend(validate_stage6e_manifest(manifest, workspace_dir))
    checked = []
    for rel, expected in manifest.get("output_hashes", {}).items():
        p = os.path.join(workspace_dir, rel)
        if not os.path.exists(p):
            errors.append(f"manifest references missing output: {rel}")
            continue
        actual = _file_sha256(p)
        checked.append(rel)
        if actual != expected:
            errors.append(f"hash mismatch for {rel}: manifest {expected[:12]} != actual {actual[:12]}")
    try:
        _forbid_forbidden_outputs(dest_dir)
    except Phase6EAssemblyError as e:
        errors.append(str(e))
    return {"ok": not errors, "errors": errors, "checked": sorted(checked)}


def _forbid_forbidden_outputs(dest_dir):
    if not os.path.isdir(dest_dir):
        return
    for name in os.listdir(dest_dir):
        if name in FORBIDDEN_ARTIFACTS:
            raise Phase6EAssemblyError(f"forbidden later-stage artifact present: {name}")
        ext = os.path.splitext(name)[1].lower()
        if ext in FORBIDDEN_IMAGE_EXTENSIONS:
            raise Phase6EAssemblyError(f"forbidden image file present: {name}")


# ================================================================ VALIDATORS
def _raise_on_errors(label, errors):
    if errors:
        raise Phase6EAssemblyError(f"{label} failed validation: " + "; ".join(errors))


def _recomputes(doc):
    return doc.get("deterministic_content_sha256") == content_sha256(
        {k: v for k, v in doc.items() if k != "deterministic_content_sha256"})


def validate_job_record(j):
    errors = []
    required = ("job_id", "position", "purpose", "output_role", "listing_or_aplus_usage",
                "required_fact_ids", "required_claim_ids", "required_asset_ids", "prompt_readiness",
                "asset_readiness", "missing_requirements", "prompt", "prompt_status",
                "negative_constraints", "overlay_copy", "overlay_copy_lineage", "alt_text",
                "cross_image_consistency_ids", "status", "blockers", "warnings")
    for k in required:
        if k not in j:
            errors.append(f"job {j.get('job_id')} missing field {k}")
    if j.get("status") not in JOB_STATES:
        errors.append(f"job {j.get('job_id')} bad state {j.get('status')}")
    # a prompt body may exist ONLY for a PROMPT_READY job.
    if j.get("prompt") and j.get("status") != PROMPT_READY:
        errors.append(f"job {j.get('job_id')} carries a prompt body but is not PROMPT_READY")
    if j.get("status") == PROMPT_READY and not j.get("prompt"):
        errors.append(f"PROMPT_READY job {j.get('job_id')} has no prompt body")
    if j.get("status") == NOT_APPLICABLE and not j.get("not_applicable_reason"):
        errors.append(f"NOT_APPLICABLE job {j.get('job_id')} lacks a reason")
    if j.get("status") in (REAL_PHOTO_REQUIRED, OWNER_FACT_REQUIRED, BLOCKED_UNSAFE_JOB) and j.get("prompt"):
        errors.append(f"blocked job {j.get('job_id')} must not carry a prompt body")
    if j.get("status") in (OWNER_FACT_REQUIRED, REAL_PHOTO_REQUIRED, BLOCKED_UNSAFE_JOB) and not j.get("blockers"):
        errors.append(f"blocked job {j.get('job_id')} must record blockers")
    # a real-proof job can never accept a generated asset.
    if j.get("real_proof_required") and j.get("generated_mockup_allowed"):
        errors.append(f"job {j.get('job_id')} real-proof gate must not allow a generated mockup")
    # non-empty overlay must carry claim + fact lineage.
    if j.get("overlay_copy"):
        lin = j.get("overlay_copy_lineage") or {}
        if not lin.get("atomic_claim_ids"):
            errors.append(f"job {j.get('job_id')} overlay copy without claim lineage")
    # final alt text is never authorized for a job without a verified asset.
    if j.get("alt_text"):
        errors.append(f"job {j.get('job_id')} must not carry final alt text before a verified asset exists")
    # relative-path safety
    for p in (j.get("current_relative_paths") or []):
        if os.path.isabs(p) or ".." in str(p).replace(os.sep, "/").split("/"):
            errors.append(f"job {j.get('job_id')} unsafe path {p}")
    return errors


def validate_listing_image_plan(doc):
    errors = []
    if doc.get("schema_version") != LISTING_IMAGE_PLAN_SCHEMA_VERSION:
        errors.append(f"schema_version must be {LISTING_IMAGE_PLAN_SCHEMA_VERSION}")
    jobs = doc.get("jobs") or []
    ids = [j.get("job_id") for j in jobs]
    positions = [j.get("position") for j in jobs]
    if len(jobs) != len(TEN_JOB_ORDER):
        errors.append(f"expected {len(TEN_JOB_ORDER)} jobs, got {len(jobs)}")
    if ids != list(TEN_JOB_ORDER):
        errors.append(f"job order/id mismatch: {ids}")
    if len(set(ids)) != len(ids):
        errors.append("duplicate job_id")
    if positions != list(range(1, len(TEN_JOB_ORDER) + 1)):
        errors.append("job positions must be 1..10 in order")
    if doc.get("job_count") != len(jobs):
        errors.append("job_count mismatch")
    for j in jobs:
        errors.extend(validate_job_record(j))
    ss = doc.get("state_summary") or {}
    if sum(ss.values()) != len(jobs):
        errors.append("state_summary does not sum to the job count")
    for key, state in (("prompt_ready_count", PROMPT_READY), ("asset_ready_count", ASSET_READY),
                       ("real_photo_required_count", REAL_PHOTO_REQUIRED),
                       ("owner_fact_required_count", OWNER_FACT_REQUIRED),
                       ("not_applicable_count", NOT_APPLICABLE), ("blocked_unsafe_count", BLOCKED_UNSAFE_JOB)):
        if doc.get(key) != ss.get(state):
            errors.append(f"{key} disagrees with state_summary")
    if doc.get("listing_publishability_state") != LISTING_SAFE_DRAFT:
        errors.append("listing must remain SAFE_DRAFT_OWNER_FACTS_REQUIRED")
    if not _recomputes(doc):
        errors.append("deterministic_content_sha256 does not recompute")
    return errors


def validate_creative_asset_manifest(doc):
    errors = []
    if doc.get("schema_version") != ASSET_MANIFEST_SCHEMA_VERSION:
        errors.append(f"schema_version must be {ASSET_MANIFEST_SCHEMA_VERSION}")
    active = doc.get("active_assets") or []
    ids = [a.get("asset_id") for a in active]
    if len(set(ids)) != len(ids):
        errors.append("duplicate active asset_id")
    if doc.get("active_asset_count") != len(active):
        errors.append("active_asset_count mismatch")
    for a in active:
        for p in ([a.get("current_relative_path")] if a.get("current_relative_path") else []):
            if os.path.isabs(p) or ".." in str(p).replace(os.sep, "/").split("/"):
                errors.append(f"asset {a.get('asset_id')} unsafe path {p}")
        # a missing file cannot be ASSET_READY / READY.
        if a.get("state") == AR_READY and (not a.get("source_sha256") or a.get("real_or_generated") != "REAL"):
            errors.append(f"asset {a.get('asset_id')} READY without a verified real file")
        # a real-proof asset must not allow a generated mockup.
        if a.get("real_proof_required") and a.get("generated_mockup_allowed"):
            errors.append(f"asset {a.get('asset_id')} real-proof gate must not allow a generated mockup")
        # a generated asset can never sit in a real-proof state.
        if a.get("real_or_generated") == "GENERATED" and a.get("real_proof_required"):
            errors.append(f"asset {a.get('asset_id')} generated but marked real-proof")
    # shared assets represented once (already unique by id); confirm SHARED assets reference >1 entity.
    for a in active:
        if a.get("asset_scope") == "SHARED":
            refs = len(a.get("linked_job_ids", [])) + len(a.get("linked_module_keys", []))
            if refs < 2:
                errors.append(f"shared asset {a.get('asset_id')} references fewer than two entities")
    if doc.get("ai_generated_assets_accepted_as_real_proof", 0) != 0:
        errors.append("a generated asset was accepted as real proof")
    # deferred candidates classified separately and never active.
    for d in (doc.get("deferred_candidate_assets") or []):
        if d.get("classification") != "DEFERRED_PREMIUM_CANDIDATE":
            errors.append(f"deferred asset {d.get('asset_id')} lost its deferred classification")
        if d.get("state") != "DEFERRED_NOT_ACTIVE":
            errors.append(f"deferred asset {d.get('asset_id')} is not DEFERRED_NOT_ACTIVE")
    if not _recomputes(doc):
        errors.append("deterministic_content_sha256 does not recompute")
    return errors


def validate_shot_list(shots, jobs):
    errors = []
    # every real-proof asset referenced by a real-photo / owner-fact job must map to at least one shot.
    covered_assets = {aid for s in shots for aid in s.get("mapped_asset_ids", [])}
    covered_purposes = {s.get("proof_purpose") for s in shots}
    for j in jobs:
        if j["real_proof_required"] and j.get("required_asset_ids"):
            for aid in j["required_asset_ids"]:
                if aid not in covered_assets and j["proof_purpose"] not in covered_purposes:
                    errors.append(f"real-proof job {j['job_id']} asset {aid} has no shot")
    for s in shots:
        if not s.get("proof_objective"):
            errors.append(f"shot {s.get('shot_id')} missing proof objective")
        if not s.get("acceptance_criteria"):
            errors.append(f"shot {s.get('shot_id')} missing acceptance criteria")
        if not s.get("rejection_criteria"):
            errors.append(f"shot {s.get('shot_id')} missing rejection criteria")
        if not (s.get("mapped_job_ids") or s.get("mapped_aplus_asset_ids")):
            errors.append(f"shot {s.get('shot_id')} maps to no job or A+ asset")
        if s.get("ai_generated_accepted_as_evidence"):
            errors.append(f"shot {s.get('shot_id')} accepts AI as evidence")
    return errors


def validate_prompt_package(jobs, *, aplus_generation_prompt_count):
    errors = []
    for j in jobs:
        if j["status"] == PROMPT_READY:
            if not j.get("prompt"):
                errors.append(f"PROMPT_READY job {j['job_id']} lacks a prompt body")
            if not j.get("negative_constraints"):
                errors.append(f"prompt for {j['job_id']} lacks negative constraints")
        else:
            if j.get("prompt"):
                errors.append(f"non-PROMPT_READY job {j['job_id']} carries a prompt body")
    if aplus_generation_prompt_count != 0:
        errors.append("A+ generation prompt count must be 0 for T2 (no filler prompts)")
    return errors


def validate_creative_audit(doc, plan_doc, asset_doc):
    errors = []
    if doc.get("schema_version") != CREATIVE_AUDIT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {CREATIVE_AUDIT_SCHEMA_VERSION}")
    if doc.get("job_count") != plan_doc.get("job_count"):
        errors.append("audit job_count disagrees with the plan")
    for key in ("prompt_ready_count", "asset_ready_count", "real_photo_required_count",
                "owner_fact_required_count", "not_applicable_count", "blocked_unsafe_count"):
        if doc.get(key) != plan_doc.get(key):
            errors.append(f"audit {key} disagrees with the plan")
    if not doc.get("exact_job_order_result"):
        errors.append("audit reports a wrong job order")
    if doc.get("unsupported_visual_claim_count") != 0:
        errors.append("audit reports unsupported visual claims")
    if doc.get("ai_generated_assets_accepted_as_real_proof_count") != 0:
        errors.append("audit reports a generated asset accepted as real proof")
    if doc.get("unique_asset_count") != asset_doc.get("unique_asset_count"):
        errors.append("audit unique_asset_count disagrees with the manifest")
    if doc.get("listing_publishability") != LISTING_SAFE_DRAFT:
        errors.append("audit must keep the listing a safe draft")
    if doc.get("phase6e_state") not in PHASE6E_STATES:
        errors.append(f"invalid phase6e_state {doc.get('phase6e_state')}")
    if not _recomputes(doc):
        errors.append("deterministic_content_sha256 does not recompute")
    return errors


def validate_stage6e_manifest(doc, workspace_dir):
    errors = []
    if doc.get("schema_version") != STAGE_MANIFEST_SCHEMA_VERSION:
        errors.append("invalid manifest schema_version")
    if doc.get("stage_id") != STAGE_ID:
        errors.append("manifest stage_id must be 6E")
    if doc.get("state") not in PHASE6E_STATES:
        errors.append(f"invalid manifest state {doc.get('state')}")
    outputs = doc.get("output_hashes", {})
    seen, required_present = set(), set()
    for rel in outputs:
        if os.path.isabs(rel):
            errors.append(f"manifest output path is absolute: {rel}")
        parts = rel.replace(os.sep, "/").split("/")
        if ".." in parts:
            errors.append(f"manifest output path has parent traversal: {rel}")
        if rel in seen:
            errors.append(f"duplicate manifest output path: {rel}")
        seen.add(rel)
        base = parts[-1]
        if base in REQUIRED_ARTIFACTS:
            required_present.add(base)
        if base in FORBIDDEN_ARTIFACTS:
            errors.append(f"manifest lists a forbidden artifact: {base}")
        if os.path.splitext(base)[1].lower() in FORBIDDEN_IMAGE_EXTENSIONS:
            errors.append(f"manifest lists an image file: {base}")
    for name in REQUIRED_ARTIFACTS:
        if name not in required_present:
            errors.append(f"manifest missing required artifact: {name}")
    if STAGE_MANIFEST_FILENAME in {r.split("/")[-1] for r in outputs}:
        errors.append("manifest must not list its own hash in output_hashes")
    if doc.get("AI_generated_proof_acceptance_count", 0) != 0:
        errors.append("manifest reports a generated asset accepted as real proof")
    if doc.get("deterministic_content_sha256") != content_sha256(_manifest_hashable(doc)):
        errors.append("manifest deterministic_content_sha256 does not recompute")
    return errors


# ================================================================ CLI
def main():
    import argparse
    ap = argparse.ArgumentParser(description="Phase 6E — evidence-gated creative production package.")
    ap.add_argument("run_dir")
    ap.add_argument("--mode", default="CONNECTED_RESEARCH",
                    choices=["CONNECTED_RESEARCH", "LOCAL_SAFE", "TEST_DENY_EXTERNAL"])
    ap.add_argument("--write", action="store_true", help="write artifacts under <run>/phase6/6E")
    a = ap.parse_args()
    result = assemble_phase6e(a.run_dir, connectivity_mode=a.mode)
    if a.write:
        write_phase6e_artifacts(result, workspace_dir=a.run_dir, started_at="cli", completed_at="cli")
        v = verify_phase6e_artifacts(phase6e_dir(a.run_dir), workspace_dir=a.run_dir)
        print(f"wrote Phase 6E artifacts; verify ok={v['ok']} errors={v['errors']}")
    ss = _state_summary(result.jobs)
    print(f"phase6e_state={result.phase6e_state}  ready_for_6f={result.ready_for_6f}  jobs={len(result.jobs)}")
    print(f"PROMPT_READY={ss[PROMPT_READY]} REAL_PHOTO_REQUIRED={ss[REAL_PHOTO_REQUIRED]} "
          f"OWNER_FACT_REQUIRED={ss[OWNER_FACT_REQUIRED]} NOT_APPLICABLE={ss[NOT_APPLICABLE]} "
          f"ASSET_READY={ss[ASSET_READY]} BLOCKED_UNSAFE={ss[BLOCKED_UNSAFE_JOB]}")
    print(f"active_aplus_assets={result.asset_recon['active_asset_requirement_count']} "
          f"deferred={result.asset_recon['deferred_candidate_asset_requirement_count']} "
          f"unique_assets={result.asset_manifest['unique_asset_count']} "
          f"generated_proof_accepted={result.asset_manifest['ai_generated_assets_accepted_as_real_proof']}")


if __name__ == "__main__":
    main()
