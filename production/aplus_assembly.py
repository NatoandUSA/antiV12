#!/usr/bin/env python3
"""
production.aplus_assembly — the ONE Phase 6D A+ Content assembly authority.

Phase 6D assembles a TRUTHFUL A+ Content package (a Basic A+ result + a Premium A+ DRAFT + a
module-specific asset-requirement manifest + a compliance report) from the verified Phase 6A product
facts / atomic claim evidence and the mature Phase 5 A+ engine. It is a thin assembler: it never
re-implements the A+ builder, the A+ module registry, the claim engine, the product-fact loader, the
PageAuditor, or a capability resolver — it REUSES them.

Reuses — never re-implements:
  * Phase 6A dependency + verify   -> production.product_workspace / keyword_allocation_planner
  * A+ capability + build + assets  -> listing.aplus_builder / listing.aplus_module_registry
  * product facts                   -> listing.product_fact_loader
  * atomic claim evidence           -> listing.claim_evidence
  * category policy                 -> listing.category_policy_registry
  * page auditor                    -> listing.page_auditor
  * canonical json / hash / atomic write -> production.product_workspace helpers
  * runtime / network safety        -> core.runtime_policy

A+ capability is resolved through the existing registry authority and is NEVER guessed: when the owner
has not stated A+ eligibility the honest state is UNKNOWN_A_PLUS_CAPABILITY (a validated Basic payload
plus an unverified Premium draft), never a silent Basic default. Only a module whose evidence gates all
pass may emit customer-visible copy; a blocked module keeps its requirements in metadata and emits empty
copy (never a placeholder). A generated / AI asset can satisfy ZERO real-proof gates.

It never touches the owner's Amazon account in any way, never opens the network, and never emits a
later-stage (Phase 6E creative production / 6F) artifact — no image plan, no image prompt, no image file.

Public API:
  load_phase6d_dependencies(run_dir) -> Phase6DDependencies
  resolve_aplus_capability(deps) -> (capability, source, verified)
  assemble_phase6d(run_dir, connectivity_mode=...) -> APlusAssemblyResult
  write_phase6d_artifacts(result, dest_dir, ...) -> manifest dict
  verify_phase6d_artifacts(dest_dir) -> dict
  phase6d_dir(run_dir) -> str
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ("listing", "core"):
    _p = os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import product_workspace as PW      # noqa: E402  (canonical json/hash/atomic-write template)
import keyword_allocation_planner as KAP            # noqa: E402  (6A dep + 6B advisory + physical-claim gate)
import keyword_source_adapter as KSA                # noqa: E402
import product_fact_loader as PFL                   # noqa: E402
import claim_evidence as CE                         # noqa: E402
import aplus_module_registry as AREG                # noqa: E402  (the ONE A+ capability/module authority)
import aplus_builder as AB                          # noqa: E402  (the ONE A+ builder authority)
import category_policy_registry as CPR              # noqa: E402
import page_auditor as PA                           # noqa: E402
import runtime_policy as RP                         # noqa: E402  (offline safety snapshot)

# one serialization + hashing authority (do NOT re-implement) --------------------------------------
canonical_json = PW.canonical_json
content_sha256 = PW.content_sha256
_file_sha256 = PW._file_sha256

# -- schema versions -------------------------------------------------------------------------------
SCHEMA_VERSION = "phase6-aplus-assembly-v1"
BASIC_SCHEMA_VERSION = "phase6-basic-aplus-v1"
PREMIUM_SCHEMA_VERSION = "phase6-premium-aplus-v1"
COMPLIANCE_SCHEMA_VERSION = "phase6-aplus-compliance-v1"
ASSET_MANIFEST_SCHEMA_VERSION = "phase6-aplus-asset-manifest-v1"
FIELD_BLOCKERS_SCHEMA_VERSION = "phase6-aplus-field-blockers-v1"
COPY_LINEAGE_SCHEMA_VERSION = "phase6-aplus-copy-lineage-v1"
STAGE_MANIFEST_SCHEMA_VERSION = "phase6d-stage-manifest-v1"

STAGE_ID = "6D"
STAGE_NAME = "A+ Content Assembly"

# -- Phase 6D stage states (NOT the same as listing publishability) --------------------------------
READY_FOR_6E = "READY_FOR_6E"
SAFE_APLUS_OWNER_FACTS_REQUIRED = "SAFE_APLUS_OWNER_FACTS_REQUIRED"
APLUS_ELIGIBILITY_UNVERIFIED = "APLUS_ELIGIBILITY_UNVERIFIED"
NO_A_PLUS_NOT_APPLICABLE = "NO_A_PLUS_NOT_APPLICABLE"
BLOCKED_DEPENDENCY = "BLOCKED_DEPENDENCY"
BLOCKED_UNSAFE = "BLOCKED_UNSAFE"
INVALID_ENGINE_OUTPUT = "INVALID_ENGINE_OUTPUT"
INVALID_ASSET_MANIFEST = "INVALID_ASSET_MANIFEST"
INVALID_AUDIT = "INVALID_AUDIT"
PHASE6D_STAGE_STATES = (READY_FOR_6E, SAFE_APLUS_OWNER_FACTS_REQUIRED, APLUS_ELIGIBILITY_UNVERIFIED,
                        NO_A_PLUS_NOT_APPLICABLE, BLOCKED_DEPENDENCY, BLOCKED_UNSAFE,
                        INVALID_ENGINE_OUTPUT, INVALID_ASSET_MANIFEST, INVALID_AUDIT)

LISTING_SAFE_DRAFT = "SAFE_DRAFT_OWNER_FACTS_REQUIRED"

# -- per-artifact publishability states -------------------------------------------------------------
BASIC_ELIGIBILITY_UNVERIFIED = "ELIGIBILITY_UNVERIFIED"
BASIC_READY = "READY"
PREMIUM_DO_NOT_PUBLISH = "DO_NOT_PUBLISH"

# -- asset states (the only ones an asset requirement may carry) ------------------------------------
ASSET_READY = "ASSET_READY"
ASSET_PROMPT_READY = "PROMPT_READY"
ASSET_REAL_PHOTO_REQUIRED = "REAL_PHOTO_REQUIRED"
ASSET_OWNER_FACT_REQUIRED = "OWNER_FACT_REQUIRED"
ASSET_NOT_APPLICABLE = "NOT_APPLICABLE"
ASSET_BLOCKED_UNSAFE = "BLOCKED_UNSAFE"
ASSET_STATES = (ASSET_READY, ASSET_PROMPT_READY, ASSET_REAL_PHOTO_REQUIRED, ASSET_OWNER_FACT_REQUIRED,
                ASSET_NOT_APPLICABLE, ASSET_BLOCKED_UNSAFE)

# -- prompt/asset readiness (a prompt can be briefable while its real asset is still missing) --------
PROMPT_READY = "PROMPT_READY"
PROMPT_OWNER_FACT_REQUIRED = "OWNER_FACT_REQUIRED"

# -- required + support artifacts (all under <run>/phase6/6D) --------------------------------------
BASIC_FILENAME = "BASIC-APLUS-CONTENT.json"
PREMIUM_FILENAME = "PREMIUM-APLUS-DRAFT.json"
COMPLIANCE_FILENAME = "APLUS-COMPLIANCE-REPORT.json"
ASSET_MANIFEST_FILENAME = "APLUS-ASSET-MANIFEST.json"
STAGE_MANIFEST_FILENAME = "STAGE-6D-MANIFEST.json"
BASIC_BRIEF_FILENAME = "BASIC-APLUS-CONTENT-BRIEF.md"
FIELD_BLOCKERS_FILENAME = "APLUS-FIELD-BLOCKERS.json"
COPY_LINEAGE_FILENAME = "APLUS-COPY-LINEAGE.json"

REQUIRED_ARTIFACTS = (BASIC_FILENAME, PREMIUM_FILENAME, COMPLIANCE_FILENAME, ASSET_MANIFEST_FILENAME)
SUPPORT_ARTIFACTS = (BASIC_BRIEF_FILENAME, FIELD_BLOCKERS_FILENAME, COPY_LINEAGE_FILENAME)

# artifacts a Phase 6D run must NEVER emit (they belong to the Phase 6E creative production package).
FORBIDDEN_ARTIFACTS = frozenset({
    "LISTING-IMAGE-PLAN.json", "LISTING-IMAGE-PROMPTS.md", "APLUS-IMAGE-PROMPTS.md",
    "REAL_PHOTO_SHOT_LIST.md", "CREATIVE-ASSET-CHECKLIST.json", "CREATIVE-ASSET-AUDIT.json",
    "PACKAGE-INDEX.json", "SELLER-CENTRAL-READY",
})

_MANIFEST_VOLATILE = ("deterministic_content_sha256", "started_at", "completed_at",
                      "previous_valid_manifest_sha256", "verification")

# proof purposes whose evidentiary value can ONLY come from a real photograph — an AI / generated mockup
# can never satisfy these gates. Every other proof purpose is illustrative (a generated graphic is OK).
_REAL_PROOF_PURPOSES = frozenset({
    "MACRO_DECORATION_STITCH", "PERSONALIZATION_EXAMPLE", "GARMENT_OR_COLOR", "LIFESTYLE",
    "GARMENT_OPTIONS", "DESIGN_VARIATIONS", "PRODUCTION_PROCESS", "GIFT_PACKAGING",
})


class Phase6DError(Exception):
    pass


class Phase6DDependencyError(Phase6DError):
    pass


class Phase6DAssemblyError(Phase6DError):
    pass


# ---------------------------------------------------------------- paths / atomic write
def phase6d_dir(run_dir):
    return os.path.join(run_dir, "phase6", "6D")


def _safe_rel(path, base):
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(base))
    norm = rel.replace(os.sep, "/")
    if os.path.isabs(rel) or norm == ".." or norm.startswith("../"):
        raise Phase6DAssemblyError(f"path escapes workspace ({norm}): {path}")
    return norm


def _atomic_write_text(path, text):
    """temp sibling -> fsync -> os.replace. A failed write never overwrites the last valid file."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-6d-", suffix=".part")
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


def _norm_text(text):
    n = re.sub(r"[^a-z0-9 ]", " ", str(text).lower())
    return re.sub(r"\s+", " ", n).strip()


def _is_safe_copy(text):
    """Phase 6D visible-copy evidence gate — reuse the 6B physical-claim classifier. Emitted copy is
    safe ONLY if it classifies SAFE (pure market identity). Any GATED verdict means it leans on an
    unverified physical / personalization / gift-occasion concept and must not reach customer copy."""
    norm = _norm_text(text)
    if not norm:
        return True                      # empty copy is trivially safe (nothing asserted)
    return KAP._classify_keyword({"keyword_normalized": norm})[0] == "SAFE"


# ================================================================ DEPENDENCIES
class Phase6DDependencies:
    """Verified, read-only view over Phase 6A (+ optional Phase 6B advisory), plus reusable engines."""

    def __init__(self, *, run_dir, dep, facts, claims, policy, keyword_source, workspace,
                 phase6a_manifest, phase6b_manifest, phase6b_aplus_field, phase6b_aplus_status):
        self.run_dir = run_dir
        self.dep = dep
        self.facts = facts
        self.claims = claims
        self.policy = policy
        self.keyword_source = keyword_source
        self.workspace = workspace
        self.phase6a_manifest = phase6a_manifest
        self.phase6b_manifest = phase6b_manifest
        self.phase6b_aplus_field = phase6b_aplus_field        # advisory A+ allocation records (expected [])
        self.phase6b_aplus_status = phase6b_aplus_status      # advisory A+ field_status dict

    # identity
    @property
    def workspace_id(self):
        return self.dep.workspace_id

    @property
    def product_id(self):
        return self.dep.product_id

    @property
    def category(self):
        return self.dep.category

    @property
    def product_type(self):
        return self.dep.product_type

    @property
    def audience(self):
        return self.dep.audience

    # dependency hashes
    @property
    def keyword_source_sha256(self):
        return self.dep.keyword_source_sha256

    @property
    def product_facts_sha256(self):
        return self.dep.product_facts_sha256

    @property
    def claim_evidence_sha256(self):
        return self.dep.claim_evidence_sha256

    @property
    def phase6a_manifest_sha256(self):
        return self.dep.phase6a_manifest_sha256

    @property
    def phase6b_manifest_sha256(self):
        return (self.phase6b_manifest or {}).get("deterministic_content_sha256")

    @property
    def listing_publishability_state(self):
        return self.dep.listing_publishability_state


def load_phase6d_dependencies(run_dir):
    """Load + VERIFY every Phase 6A dependency (and, when present, the Phase 6B advisory A+ context)
    before any assembly. Phase 6A is the HARD dependency; Phase 6B is optional advisory keyword context.

    Blocks (Phase6DDependencyError) when: a 6A artifact is missing / tampered / hash-mismatched, the
    reconstructed facts/claims do not reproduce the 6A hashes, the atomic-claim invariant is broken, the
    6B advisory (when present) disagrees with 6A, ready_for_6b is not true, or the Amazon account boundary
    is not closed. It NEVER modifies an upstream artifact.
    """
    dest6a = PW.phase6a_dir(run_dir)

    # 1) Phase 6A dependency — load_phase6a_dependency re-verifies 6A hashes + identity + source drift.
    try:
        dep = KAP.load_phase6a_dependency(run_dir)
    except KAP.Phase6ADependencyError as e:
        raise Phase6DDependencyError(f"Phase 6A dependency invalid: {e}") from e
    v6a = PW.verify_phase6a_artifacts(dest6a, workspace_dir=run_dir)
    if not v6a["ok"]:
        raise Phase6DDependencyError(f"Phase 6A artifacts failed verification: {v6a['errors']}")

    workspace = PW.load_product_workspace(dest6a)
    phase6a_manifest = _load_json(os.path.join(dest6a, PW.STAGE_MANIFEST_FILENAME))

    # 2) reconstruct facts + claims EXACTLY as Phase 6A/5 did and reproduce the recorded 6A hashes.
    keyword_source = KSA.load_keyword_source(run_dir)
    facts = PFL.load_product_facts(folder=run_dir)
    if facts.content_sha256() != dep.product_facts_sha256:
        raise Phase6DDependencyError("reconstructed product facts do not match the 6A facts hash")
    ctx = {"audience": dep.audience} if dep.audience else {}
    claims = CE.build_claim_evidence(facts, keyword_context=ctx)
    if claims.content_sha256() != dep.claim_evidence_sha256:
        raise Phase6DDependencyError("reconstructed claim evidence does not match the 6A claim hash")
    if claims.schema_version != CE.CLAIM_EVIDENCE_SCHEMA_VERSION:
        raise Phase6DDependencyError(f"unexpected claim schema {claims.schema_version}")

    # 3) atomic-claim invariant must hold (no mixed/compound VERIFIED claim survives).
    audit = CE.audit_claim_evidence(claims)
    if not audit["ok"]:
        raise Phase6DDependencyError(f"atomic claim invariant broken: {audit['violations']}")

    # 4) Phase 6B advisory context (OPTIONAL) — when present, cross-check it agrees with 6A and read the
    #    advisory A+ allocation field. It NEVER authorizes A+ eligibility.
    phase6b_manifest, aplus_field, aplus_status = None, None, None
    dest6b = KAP.phase6b_dir(run_dir)
    amap_path = os.path.join(dest6b, "KEYWORD-ALLOCATION-MAP.json")
    man6b_path = os.path.join(dest6b, "STAGE-6B-MANIFEST.json")
    if os.path.exists(amap_path) and os.path.exists(man6b_path):
        v6b = KAP.verify_phase6b_artifacts(dest6b, workspace_dir=run_dir)
        if not v6b["ok"]:
            raise Phase6DDependencyError(f"Phase 6B artifacts failed verification: {v6b['errors']}")
        amap = _load_json(amap_path)
        phase6b_manifest = _load_json(man6b_path)
        for field, want in (("source_keyword_sha256", dep.keyword_source_sha256),
                            ("claim_evidence_sha256", dep.claim_evidence_sha256),
                            ("product_facts_sha256", dep.product_facts_sha256)):
            if amap.get(field) != want:
                raise Phase6DDependencyError(
                    f"6B allocation {field} {str(amap.get(field))[:12]} != 6A {str(want)[:12]}")
        if amap.get("downstream_authorization") != "ADVISORY_ONLY":
            raise Phase6DDependencyError("6B allocation map is not ADVISORY_ONLY")
        aplus_field = list((amap.get("fields") or {}).get("aplus") or [])
        aplus_status = dict((amap.get("field_status") or {}).get("aplus") or {})

    # 5) Amazon-account boundary must remain closed in every mode (immutable runtime-policy attrs).
    pol = RP.load_runtime_policy()
    if not getattr(pol, "amazon_account_isolation", False) or getattr(pol, "amazon_api_enabled", True) \
            or getattr(pol, "amazon_network_writes_enabled", True) \
            or getattr(pol, "amazon_authenticated_access_enabled", True):
        raise Phase6DDependencyError("Amazon account boundary is not closed")

    policy = CPR.resolve_category_policy(dep.category or "apparel")
    return Phase6DDependencies(
        run_dir=run_dir, dep=dep, facts=facts, claims=claims, policy=policy,
        keyword_source=keyword_source, workspace=workspace, phase6a_manifest=phase6a_manifest,
        phase6b_manifest=phase6b_manifest, phase6b_aplus_field=aplus_field,
        phase6b_aplus_status=aplus_status)


# ================================================================ CAPABILITY
def resolve_aplus_capability(deps):
    """Resolve A+ capability through the existing registry authority — NEVER guessed, never defaulted to
    Basic. When the owner has not stated an A+ eligibility, the honest state is UNKNOWN_A_PLUS_CAPABILITY
    (a validated Basic payload + an unverified Premium draft). An explicitly declared, invalid capability
    fails loudly. Returns (capability, capability_source, capability_verified)."""
    # look for an owner-declared capability in the verified workspace (none exists for an unstated owner).
    declared = None
    ws = deps.workspace or {}
    for key in ("aplus_capability", "a_plus_capability"):
        if ws.get(key):
            declared = ws.get(key)
            break
    if declared is None:
        f = deps.facts.get("aplus_capability") if hasattr(deps.facts, "get") else None
        if isinstance(f, dict) and f.get("publishable"):
            declared = f.get("value")

    if declared is None:
        # AREG.validate_capability(None) -> UNKNOWN_A_PLUS_CAPABILITY (the registry's honest-unknown
        # state), NOT Basic. The Phase 6B advisory independently records the same UNKNOWN state.
        capability = AREG.validate_capability(None)
        source = ("aplus_module_registry.DEFAULT_CAPABILITY (owner A+ eligibility unstated in the "
                  "verified Phase 6A workspace; confirmed UNKNOWN by the Phase 6B A+ advisory)")
        return capability, source, False

    capability = AREG.validate_capability(declared)   # raises InvalidCapabilityError on a bad value
    return capability, f"owner-declared in Phase 6A workspace: {declared!r}", True


# ================================================================ MODULE ASSEMBLY
def _emitted_copy(module):
    """Customer-visible copy for a module — the Phase 6D module-level evidence gate. Copy is emitted ONLY
    when the module is READY (the builder assembled its body from VERIFIED atomic claims and refused every
    placeholder / unsupported phrase). A blocked module emits EMPTY copy: the builder keeps a template
    headline for every module, but a blocked module's headline is a registry label, not an asserted
    customer claim, so it stays in metadata and is never shown. Returns (copy_dict, emitted_bool).
    The shared, verification-aware PageAuditor is the authority that screens whatever copy IS emitted."""
    if module.get("status") == AREG.READY:
        return {"headline": module.get("headline") or "", "body_copy": module.get("body") or "",
                "supporting_copy": "", "alt_text": ""}, True
    return {"headline": "", "body_copy": "", "supporting_copy": "", "alt_text": ""}, False


def _module_record(module, *, module_type, deps, position):
    """One Phase 6D module record — the builder's authoritative evaluation, enriched with lineage,
    limits, evidence presence and (gated) customer-visible copy."""
    es = module.get("evidence_summary") or {}
    status = module.get("status")
    copy, emitted = _emitted_copy(module)
    missing_facts = sorted(es.get("missing_facts", []))
    missing_real_assets = sorted(es.get("missing_real_assets", []))
    verified_claim_ids = sorted(es.get("verified_claim_ids", []))
    required_claim_ids = sorted(module.get("required_claim_ids", []))
    required_facts = sorted(module.get("required_facts", []))
    required_real_assets = sorted(module.get("required_real_assets", []))
    hl = copy["headline"]
    body = copy["body_copy"]
    return {
        "module_id": f"6D-{module_type}-{position:02d}-{module.get('module_key')}",
        "module_key": module.get("module_key"),
        "position": position,
        "module_type": module_type,
        "purpose": module.get("purpose"),
        "required_fact_ids": required_facts,
        "required_claim_ids": required_claim_ids,
        "required_asset_ids": required_real_assets,
        "facts_present": not missing_facts and status == AREG.READY,
        "claims_present": bool(verified_claim_ids) and not es.get("claim_blockers"),
        "assets_present": not missing_real_assets,
        "headline": hl,
        "body_copy": body,
        "supporting_copy": copy["supporting_copy"],
        "alt_text": copy["alt_text"],
        "module_template_headline": module.get("headline") or "",   # registry label, metadata only
        "keyword_allocations_considered": [a.get("phrase") for a in (deps.phase6b_aplus_field or [])],
        "keyword_allocations_used": [],                             # 6B A+ field is empty / advisory only
        "owner_fact_dependencies": missing_facts,
        "real_asset_dependencies": missing_real_assets,
        "copy_lineage": {"emitted": emitted, "source": "listing.aplus_builder verified-claim assembly",
                         "verified_claim_ids": verified_claim_ids if emitted else [],
                         "gate": "READY + physical-claim SAFE" if emitted else "NON_PUBLISHABLE_EMPTY"},
        "claim_lineage": {"verified_claim_ids": verified_claim_ids,
                          "claim_blockers": sorted(es.get("claim_blockers", []))},
        "asset_lineage": {"required_real_assets": required_real_assets,
                          "missing_real_assets": missing_real_assets,
                          "generated_only_assets": sorted(es.get("generated_only_assets", []))},
        "character_limit_results": {
            "headline_length": len(hl), "headline_max": AREG.HEADLINE_MAX,
            "headline_within_limit": len(hl) <= AREG.HEADLINE_MAX,
            "body_length": len(body), "body_max": AREG.BODY_MAX,
            "body_within_limit": len(body) <= AREG.BODY_MAX,
            "alt_text_max": AREG.ALT_TEXT_MAX},
        "page_audit_result": {"emitted_copy": emitted, "module_state": status,
                              "screened_by": "listing.page_auditor (verification-aware, combined A+ audit)"},
        "publishability": status,
        "publishable": bool(module.get("publishable")),
        "blockers": sorted(module.get("missing_requirements", [])),
        "warnings": [],
    }


def assemble_basic_aplus(deps, result, capability, capability_verified):
    """Truthful Basic A+ content built from the existing builder. Structure completeness is NOT
    publishability: every module record is present, but visible copy is empty while blocked."""
    modules = [_module_record(m, module_type="BASIC", deps=deps, position=m.get("position_slot"))
               for m in sorted(result.basic_modules, key=lambda x: x["position_slot"])]
    owner_facts = sorted({f for m in modules for f in m["owner_fact_dependencies"]})
    real_assets = sorted({a for m in modules for a in m["real_asset_dependencies"]})
    ready = result.basic_ready and capability != AREG.NO_A_PLUS
    nonempty = sum(1 for m in modules if m["headline"] or m["body_copy"])
    doc = {
        "schema_version": BASIC_SCHEMA_VERSION,
        "workspace_id": deps.workspace_id,
        "product_id": deps.product_id,
        "capability": capability,
        "capability_verified": capability_verified,
        "state": BASIC_READY if ready else BASIC_ELIGIBILITY_UNVERIFIED,
        "module_count": len(modules),
        "modules": modules,
        "basic_fallback_role": capability in (AREG.PREMIUM_A_PLUS, AREG.UNKNOWN_A_PLUS_CAPABILITY),
        "basic_ready": result.basic_ready,
        "basic_ready_count": result.basic_ready_count,
        "nonempty_copy_module_count": nonempty,
        "owner_fact_dependencies": owner_facts,
        "real_asset_dependencies": real_assets,
        "compliance_summary": {"ready": ready, "module_count": len(modules),
                               "ready_module_count": result.basic_ready_count},
        "publishability": LISTING_SAFE_DRAFT,
        "blockers": [] if ready else ["A+ capability unverified; owner facts and real assets required"],
        "warnings": [],
    }
    doc["deterministic_content_sha256"] = content_sha256({k: v for k, v in doc.items()
                                                          if k != "deterministic_content_sha256"})
    return doc


def assemble_premium_aplus_draft(deps, result, capability, capability_verified):
    """Truthful Premium A+ DRAFT — always clearly non-publishable unless real Premium eligibility and
    every content/asset gate are verified. Includes the complete Basic fallback payload, evaluates each
    dynamic module independently, and never invents a filler module to reach two."""
    prem = result.to_premium_draft()
    is_draft = capability == AREG.UNKNOWN_A_PLUS_CAPABILITY
    # fixed modules = the reindexed Basic fallback (positions 1..5); dynamic modules occupy 6..7.
    fixed = [_module_record(m, module_type="PREMIUM_FIXED", deps=deps, position=m.get("position_slot"))
             for m in sorted(result.basic_fallback(), key=lambda x: x["position_slot"])]
    dyn_pool = prem.get("dynamic_pool") or []
    candidates, rejected = [], []
    for d in dyn_pool:
        rec = {"module_key": d.get("module_key"), "purpose": d.get("purpose"),
               "eligibility_rule": d.get("eligibility_rule"), "eligible": bool(d.get("eligible")),
               "ineligible_reasons": sorted(d.get("ineligible_reasons", [])),
               "required_real_assets": sorted(d.get("required_real_assets", [])),
               "missing_real_assets": sorted(d.get("missing_real_assets", [])),
               "verified_claim_ids": sorted(d.get("claim_ids", []))}
        candidates.append(rec)
        if not rec["eligible"]:
            rejected.append({"module_key": rec["module_key"], "reasons": rec["ineligible_reasons"]})
    selected = list(prem.get("selected_dynamic_modules") or [])
    fallback_positions = [m["position"] for m in fixed]
    blockers = []
    if len(selected) < AREG.PREMIUM_DYNAMIC_COUNT:
        blockers.append(f"only {len(selected)}/{AREG.PREMIUM_DYNAMIC_COUNT} eligible dynamic modules; "
                        "Premium is a non-publishable draft")
    if is_draft:
        blockers.append("A+ eligibility unverified; Premium draft is never publishable")
    doc = {
        "schema_version": PREMIUM_SCHEMA_VERSION,
        "workspace_id": deps.workspace_id,
        "product_id": deps.product_id,
        "capability": capability,
        "capability_verified": capability_verified,
        "state": APLUS_ELIGIBILITY_UNVERIFIED if is_draft else (
            "READY" if result.premium_eligible else "PREMIUM_INCOMPLETE"),
        "is_draft": True,
        "never_publishable": bool(prem.get("never_publishable", True)),
        "premium_eligible": bool(result.premium_eligible),
        "fixed_modules": fixed,
        "fixed_module_count": len(fixed),
        "dynamic_candidates": candidates,
        "dynamic_candidate_count": len(candidates),
        "eligible_dynamic_modules": [d["module_key"] for d in candidates if d["eligible"]],
        "selected_dynamic_modules": selected,
        "rejected_dynamic_modules": rejected,
        "basic_fallback": fixed,                       # the complete Basic payload, positions 1..5
        "basic_fallback_positions": fallback_positions,
        "owner_fact_dependencies": sorted({f for m in fixed for f in m["owner_fact_dependencies"]}),
        "real_asset_dependencies": sorted(
            {a for m in fixed for a in m["real_asset_dependencies"]}
            | {a for d in candidates for a in d["missing_real_assets"]}),
        "compliance_summary": {"premium_eligible": bool(result.premium_eligible),
                               "selected_dynamic_count": len(selected),
                               "eligible_dynamic_count": len([d for d in candidates if d["eligible"]])},
        "publishability": PREMIUM_DO_NOT_PUBLISH,
        "blockers": blockers,
        "warnings": [],
    }
    doc["deterministic_content_sha256"] = content_sha256({k: v for k, v in doc.items()
                                                          if k != "deterministic_content_sha256"})
    return doc


# ================================================================ ASSET MANIFEST
def _asset_role(proof_purpose):
    return str(proof_purpose or "UNSPECIFIED").upper()


def _asset_status(builder_status, real_photo_required, module_status, real_present):
    """Precedence surfaces the hard real-photo gate a generated asset can NEVER satisfy."""
    if builder_status == AREG.ASSET_GENERATED_NOT_PROOF:
        return ASSET_BLOCKED_UNSAFE
    if real_present and builder_status == AREG.ASSET_READY:
        return ASSET_READY
    if real_photo_required and not real_present:
        return ASSET_REAL_PHOTO_REQUIRED
    if module_status == AREG.OWNER_FACT_REQUIRED:
        return ASSET_OWNER_FACT_REQUIRED
    if builder_status == AREG.ASSET_READY:
        return ASSET_READY
    return ASSET_PROMPT_READY


def build_aplus_asset_manifest(deps, result, capability):
    """Module-specific asset-requirement manifest (NOT the Phase 6E ten-job listing-image plan). Each
    record states what proof an A+ module needs, whether a real photo is mandatory, and prompt-readiness
    metadata — but NEVER a final prompt body. A generated/AI asset can satisfy zero real-proof gates."""
    module_pos = {m["module_key"]: m["position_slot"] for m in result.basic_modules}
    module_status = {m["module_key"]: m["status"] for m in result.basic_modules}
    module_missing_facts = {m["module_key"]: sorted((m.get("evidence_summary") or {}).get("missing_facts", []))
                            for m in result.basic_modules}
    assets = []
    for a in result.to_asset_manifest()["assets"]:
        purpose = a.get("proof_purpose")
        mk = a.get("module_key")
        real_photo_required = a.get("requirements") == "REAL_PHOTO" or purpose in _REAL_PROOF_PURPOSES
        real_present = a.get("real_or_generated") == AREG.REAL
        generated_present = a.get("real_or_generated") == AREG.GENERATED
        status = _asset_status(a.get("status"), real_photo_required, module_status.get(mk), real_present)
        owner_facts = module_missing_facts.get(mk, [])
        prompt_readiness = PROMPT_READY if (module_status.get(mk) == AREG.READY) else PROMPT_OWNER_FACT_REQUIRED
        asset_readiness = status
        missing = []
        if owner_facts:
            missing += [f"owner_fact:{f}" for f in owner_facts]
        if real_photo_required and not real_present:
            missing.append(f"real_photo:{purpose}")
        assets.append({
            "asset_id": a.get("asset_id"),
            "module_key": mk,
            "module_position": module_pos.get(mk),
            "asset_role": _asset_role(purpose),
            "purpose": a.get("requirements"),
            "proof_purpose": purpose,
            "required_fact_ids": owner_facts,
            "required_claim_ids": [],
            "required_real_assets": [purpose] if real_photo_required else [],
            "current_asset_ids": [a["asset_id"]] if real_present else [],
            "current_relative_paths": [],
            "current_asset_sha256": [],
            "real_or_generated": a.get("real_or_generated"),
            "generated_mockup_allowed": not real_photo_required,
            "real_photo_required": real_photo_required,
            "generated_present_but_rejected": generated_present and real_photo_required,
            "prompt_readiness": prompt_readiness,
            "asset_readiness": asset_readiness,
            "missing_requirements": sorted(missing),
            "garment_source": "OWNER_FACT_REQUIRED",
            "color_source": "OWNER_FACT_REQUIRED",
            "design_reference": "OWNER_FACT_REQUIRED",
            "design_placement_requirements": "OWNER_FACT_REQUIRED",
            "design_scale_requirements": "OWNER_FACT_REQUIRED",
            "composition_requirements": "DEFERRED_TO_6E",
            "mobile_crop_requirements": "DEFERRED_TO_6E",
            "desktop_crop_requirements": "DEFERRED_TO_6E",
            "overlay_copy_lineage": [],
            "alt_text_lineage": [],
            "prohibited_visual_claims": _prohibited_visual_claims(purpose, real_photo_required),
            "expected_future_filename": None,
            "status": status,
            "blockers": sorted(missing),
            "warnings": [],
        })
    assets.sort(key=lambda x: (x.get("module_position") or 99, x.get("asset_id") or ""))
    doc = {
        "schema_version": ASSET_MANIFEST_SCHEMA_VERSION,
        "workspace_id": deps.workspace_id,
        "product_id": deps.product_id,
        "capability": capability,
        "note": ("Module-specific A+ asset requirements only. This is NOT a Phase 6E listing-image plan "
                 "or shot list; no final image prompt or image file is produced here. A generated or AI "
                 "asset can satisfy zero real-proof gates."),
        "asset_count": len(assets),
        "assets": assets,
        "asset_state_counts": _count_by(assets, "status"),
        "real_photo_required_count": sum(1 for a in assets if a["real_photo_required"]),
        "generated_accepted_as_real_proof_count": 0,
        "blockers": sorted({b for a in assets for b in a["blockers"]}),
        "warnings": [],
    }
    doc["deterministic_content_sha256"] = content_sha256({k: v for k, v in doc.items()
                                                          if k != "deterministic_content_sha256"})
    return doc


def _prohibited_visual_claims(proof_purpose, real_photo_required):
    """Physical claims a generated/illustrative asset must never depict as proof for this purpose."""
    base = ["presenting a generated/AI mockup as real physical proof",
            "depicting an unverified product fact as if verified"]
    extra = {
        "MACRO_DECORATION_STITCH": ["real stitch/thread texture", "embroidery quality", "decoration method"],
        "PERSONALIZATION_EXAMPLE": ["final personalization accuracy", "exact-as-entered promise"],
        "GARMENT_OR_COLOR": ["material", "actual colour accuracy", "size/measurements"],
        "LIFESTYLE": ["unverified occasion or gift context"],
        "PRODUCTION_PROCESS": ["production process", "made-to-order steps"],
        "GIFT_PACKAGING": ["gift packaging that is not supplied"],
        "GARMENT_OPTIONS": ["garment options that do not exist"],
        "DESIGN_VARIATIONS": ["design/thread variations that are not offered"],
    }.get(proof_purpose, [])
    return base + extra if real_photo_required else base


def _count_by(records, key):
    out = {}
    for r in records:
        out[r[key]] = out.get(r[key], 0) + 1
    return out


# ================================================================ PAGE AUDIT
def run_aplus_page_audit(deps, result, capability):
    """Audit the assembled A+ with the shared PageAuditor (the ONE auditor). Passes the builder's
    authoritative A+ structure via listing['aplus_content'] and the (empty for T2) publishable payload
    via listing['aplus']; the auditor's evidence-aware A+ pass reports per-module states and hard-fails
    only on genuine safety violations."""
    listing = {
        "schema_version": "2.4", "category": deps.category, "generated_by": "phase6d_aplus_assembler",
        "title": f"{deps.audience or ''} {deps.product_type or ''}".strip() or deps.product_type,
        "bullets": ["", "", "", "", ""],
        "selected_keywords": {"primary": [], "secondary": [], "backend": []},
        "category_policy": {"category_identifier": deps.policy.category_identifier,
                            "title_hard_limit": deps.policy.title_hard_limit,
                            "backend_byte_ceiling": deps.policy.backend_byte_ceiling},
        "product_fact_source_sha256": deps.facts.source_sha256,
        "aplus": result.publishable_modules(),
        "aplus_capability": capability,
        "aplus_content": result.listing_block(),
        **deps.keyword_source.listing_metadata(),
    }
    audit = PA.audit_listing(listing, keyword_source=deps.keyword_source, claim_evidence=deps.claims,
                             product_facts=deps.facts, policy=deps.policy)
    kr = audit.get("keyword_results", {})
    cr = audit.get("claim_results", {})
    audit["leakage_summary"] = {
        "wrong_audience_leakage": len(kr.get("wrong_audience_leakage", [])),
        "wrong_product_leakage": len(kr.get("wrong_product_leakage", [])),
        "wrong_occasion_leakage": len(kr.get("wrong_occasion_leakage", [])),
        "brand_ip_leakage": len(kr.get("brand_ip_leakage", [])),
        "unsupported_physical_claims": len(cr.get("unsupported", [])),
        "blocked_claim_promotion": len(cr.get("owner_review_in_copy", [])) + len(cr.get("prohibited", [])),
        "fabricated_owner_facts": len(cr.get("missing_lineage", [])),
    }
    return audit


# ================================================================ COMPLIANCE
def run_aplus_compliance(deps, result, capability, capability_verified, capability_source,
                         basic_doc, premium_doc, asset_doc, audit, phase6d_state,
                         listing_publishability, ready_for_6e, blockers, warnings):
    """The A+ compliance report — capability verification, per-module states, dynamic accounting,
    Basic-fallback verification, atomic-claim / product-fact / keyword / asset results, PageAuditor
    findings, and the truthful stage state. ready_for_6e is NEVER treated as publishability."""
    leak = audit["leakage_summary"]
    basic_states = {m["module_key"]: m["publishability"] for m in basic_doc["modules"]}
    premium_states = {m["module_key"]: m["publishability"] for m in premium_doc["fixed_modules"]}
    fallback_positions = premium_doc["basic_fallback_positions"]
    fallback_ok = fallback_positions == list(range(1, AREG.BASIC_MODULE_COUNT + 1))
    nonempty_visible = sum(1 for m in basic_doc["modules"] if m["headline"] or m["body_copy"])
    asset_counts = asset_doc["asset_state_counts"]
    doc = {
        "schema_version": COMPLIANCE_SCHEMA_VERSION,
        "workspace_id": deps.workspace_id,
        "product_id": deps.product_id,
        "capability": capability,
        "capability_verified": capability_verified,
        "capability_verification_source": capability_source,
        "basic_module_count": basic_doc["module_count"],
        "basic_module_states": basic_states,
        "basic_nonempty_copy_count": nonempty_visible,
        "basic_publishability": basic_doc["publishability"],
        "premium_module_count": premium_doc["fixed_module_count"],
        "premium_fixed_module_states": premium_states,
        "dynamic_candidate_count": premium_doc["dynamic_candidate_count"],
        "eligible_dynamic_count": len(premium_doc["eligible_dynamic_modules"]),
        "selected_dynamic_count": len(premium_doc["selected_dynamic_modules"]),
        "rejected_dynamic_reasons": {r["module_key"]: r["reasons"]
                                     for r in premium_doc["rejected_dynamic_modules"]},
        "basic_fallback_verified": fallback_ok and len(premium_doc["basic_fallback"]) == AREG.BASIC_MODULE_COUNT,
        "basic_fallback_positions": fallback_positions,
        "premium_publishability": premium_doc["publishability"],
        "atomic_claim_result": {"schema_version": deps.claims.schema_version,
                                "atomicity_ok": deps.claims.atomicity_ok,
                                "verified_concepts": sorted(c["normalized_concept"]
                                                            for c in deps.claims.publishable),
                                "claim_evidence_sha256": deps.claim_evidence_sha256},
        "product_fact_result": {"verified_fact_count": deps.facts.verified_fact_count,
                                "unknown_fact_count": deps.facts.unknown_fact_count,
                                "product_facts_sha256": deps.product_facts_sha256},
        "keyword_allocation_result": {"aplus_field_empty": not (deps.phase6b_aplus_field or []),
                                      "aplus_field_reason": (deps.phase6b_aplus_status or {}).get(
                                          "empty_reason_code"),
                                      "backfill_performed": False},
        "asset_requirement_result": {"asset_count": asset_doc["asset_count"],
                                     "state_counts": asset_counts,
                                     "real_photo_required_count": asset_doc["real_photo_required_count"]},
        "real_photo_blocker_count": asset_doc["real_photo_required_count"],
        "owner_fact_blocker_count": len(basic_doc["owner_fact_dependencies"]),
        "unsupported_claim_count": leak["unsupported_physical_claims"] + leak["blocked_claim_promotion"],
        "generated_assets_accepted_as_real_proof": asset_doc["generated_accepted_as_real_proof_count"],
        "page_auditor_result": {"status": audit["status"], "publishability": audit["publishability"],
                                "hard_failure_count": len(audit["hard_failures"]),
                                "leakage_summary": leak,
                                "aplus_evidence_results": audit.get("aplus_evidence_results", {})},
        "page_auditor_module_states": (audit.get("aplus_evidence_results") or {}).get("module_states", {}),
        "combined_basic_audit": {"status": audit["status"], "hard_failures": audit["hard_failures"]},
        "combined_premium_draft_audit": {"never_publishable": premium_doc["never_publishable"]},
        "character_limit_results": {m["module_key"]: m["character_limit_results"]
                                    for m in basic_doc["modules"]},
        "alt_text_results": {a["asset_id"]: a["asset_readiness"] for a in asset_doc["assets"]},
        "duplicate_module_purpose_result": _duplicate_purpose_result(premium_doc),
        "filler_module_result": {"filler_dynamic_selected": False,
                                 "selected_dynamic_count": len(premium_doc["selected_dynamic_modules"])},
        "aplus_copy_readiness": "NOT_READY" if nonempty_visible == 0 else "REVIEW",
        "aplus_asset_readiness": "NOT_READY" if asset_counts.get(ASSET_READY, 0) == 0 else "PARTIAL",
        "listing_publishability_state": listing_publishability,
        "phase6d_state": phase6d_state,
        "ready_for_6e": ready_for_6e,
        "ready_for_6e_note": "creative planning may proceed WITH explicit blockers; NOT publishable",
        "blockers": blockers,
        "warnings": warnings,
    }
    doc["deterministic_content_sha256"] = content_sha256({k: v for k, v in doc.items()
                                                          if k != "deterministic_content_sha256"})
    return doc


def _duplicate_purpose_result(premium_doc):
    purposes = [c["purpose"] for c in premium_doc["dynamic_candidates"]
                if c["module_key"] in premium_doc["selected_dynamic_modules"]]
    return {"selected_purposes": purposes, "duplicate": len(set(purposes)) != len(purposes)}


# ================================================================ ORCHESTRATION
class APlusAssemblyResult:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def canonical_content(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "workspace_id": self.workspace_id,
            "product_id": self.product_id,
            "category": self.category,
            "product_type": self.product_type,
            "audience": self.audience,
            "phase6a_manifest_sha256": self.phase6a_manifest_sha256,
            "optional_phase6b_manifest_sha256": self.optional_phase6b_manifest_sha256,
            "product_facts_sha256": self.product_facts_sha256,
            "claim_evidence_sha256": self.claim_evidence_sha256,
            "keyword_source_sha256": self.keyword_source_sha256,
            "aplus_capability": self.aplus_capability,
            "aplus_capability_source": self.aplus_capability_source,
            "aplus_capability_verified": self.aplus_capability_verified,
            "basic_aplus": self.basic_aplus,
            "premium_aplus_draft": self.premium_aplus_draft,
            "module_results": self.module_results,
            "asset_manifest": self.asset_manifest,
            "compliance": self.compliance,
            "owner_fact_dependencies": self.owner_fact_dependencies,
            "real_asset_dependencies": self.real_asset_dependencies,
            "listing_publishability_state": self.listing_publishability_state,
            "phase6d_state": self.phase6d_state,
            "ready_for_6e": self.ready_for_6e,
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


def assemble_phase6d(run_dir, connectivity_mode="CONNECTED_RESEARCH"):
    """Assemble the whole Phase 6D A+ package in memory (no disk writes here). Deterministic +
    network-free; connectivity_mode never influences the stable content."""
    deps = load_phase6d_dependencies(run_dir)
    capability, capability_source, capability_verified = resolve_aplus_capability(deps)

    result = AB.build_aplus(capability, deps.claims, deps.facts, assets=None,
                            category=deps.policy.product_category)

    basic_doc = assemble_basic_aplus(deps, result, capability, capability_verified)
    premium_doc = assemble_premium_aplus_draft(deps, result, capability, capability_verified)
    asset_doc = build_aplus_asset_manifest(deps, result, capability)
    audit = run_aplus_page_audit(deps, result, capability)

    # states / safety gates ------------------------------------------------------------------------
    leak = audit["leakage_summary"]
    unsafe = (bool(audit["hard_failures"]) or leak["unsupported_physical_claims"]
              or leak["blocked_claim_promotion"] or leak["fabricated_owner_facts"]
              or any(leak[k] for k in ("wrong_audience_leakage", "wrong_product_leakage",
                                       "wrong_occasion_leakage", "brand_ip_leakage")))
    # generated assets must satisfy zero real-proof gates.
    if asset_doc["generated_accepted_as_real_proof_count"]:
        unsafe = True

    blockers, warnings = [], []
    listing_publishability = deps.listing_publishability_state or LISTING_SAFE_DRAFT
    if unsafe:
        phase6d_state = BLOCKED_UNSAFE
        ready_for_6e = False
        blockers.append("A+ page audit is not clean; A+ package blocked")
    elif capability == AREG.NO_A_PLUS:
        phase6d_state = NO_A_PLUS_NOT_APPLICABLE
        ready_for_6e = True
    elif not capability_verified:
        phase6d_state = APLUS_ELIGIBILITY_UNVERIFIED
        ready_for_6e = True
        blockers.append("A+ eligibility is UNVERIFIED; Basic is a safe draft and Premium is a draft only")
    elif not result.basic_ready:
        phase6d_state = SAFE_APLUS_OWNER_FACTS_REQUIRED
        ready_for_6e = True
        blockers.append("A+ modules require owner facts / real assets before publication")
    else:
        phase6d_state = READY_FOR_6E
        ready_for_6e = True
    if basic_doc["owner_fact_dependencies"]:
        blockers.append("owner facts required for A+ modules: "
                        + ", ".join(basic_doc["owner_fact_dependencies"]))
    if basic_doc["real_asset_dependencies"]:
        blockers.append("real assets required for A+ modules: "
                        + ", ".join(basic_doc["real_asset_dependencies"]))

    owner_fact_deps = sorted(set(basic_doc["owner_fact_dependencies"])
                             | set(premium_doc["owner_fact_dependencies"]))
    real_asset_deps = sorted(set(basic_doc["real_asset_dependencies"])
                             | set(premium_doc["real_asset_dependencies"]))

    compliance = run_aplus_compliance(
        deps, result, capability, capability_verified, capability_source, basic_doc, premium_doc,
        asset_doc, audit, phase6d_state, listing_publishability, ready_for_6e, blockers, warnings)

    module_results = {"basic": {m["module_key"]: m["publishability"] for m in basic_doc["modules"]},
                      "premium_fixed": {m["module_key"]: m["publishability"]
                                        for m in premium_doc["fixed_modules"]},
                      "premium_dynamic": {c["module_key"]: ("ELIGIBLE" if c["eligible"] else "REJECTED")
                                          for c in premium_doc["dynamic_candidates"]}}

    return APlusAssemblyResult(
        run_dir=run_dir, connectivity_mode=connectivity_mode,
        schema_version=SCHEMA_VERSION,
        workspace_id=deps.workspace_id, product_id=deps.product_id, category=deps.category,
        product_type=deps.product_type, audience=deps.audience,
        phase6a_manifest_sha256=deps.phase6a_manifest_sha256,
        optional_phase6b_manifest_sha256=deps.phase6b_manifest_sha256,
        product_facts_sha256=deps.product_facts_sha256,
        claim_evidence_sha256=deps.claim_evidence_sha256,
        keyword_source_sha256=deps.keyword_source_sha256,
        aplus_capability=capability, aplus_capability_source=capability_source,
        aplus_capability_verified=capability_verified,
        basic_aplus=basic_doc, premium_aplus_draft=premium_doc, module_results=module_results,
        asset_manifest=asset_doc, compliance=compliance,
        owner_fact_dependencies=owner_fact_deps, real_asset_dependencies=real_asset_deps,
        listing_publishability_state=listing_publishability, phase6d_state=phase6d_state,
        ready_for_6e=ready_for_6e, blockers=blockers, warnings=warnings,
        page_audit=audit, _result=result, _deps=deps)


# ================================================================ WRITE / VERIFY
def _manifest_hashable(body):
    return {k: v for k, v in body.items() if k not in _MANIFEST_VOLATILE}


def _stage_manifest_doc(result, output_hashes, *, started_at=None, completed_at=None,
                        previous_valid_manifest_sha256=None, verification=None):
    asset = result.asset_manifest
    body = {
        "schema_version": STAGE_MANIFEST_SCHEMA_VERSION,
        "stage_id": STAGE_ID,
        "stage_name": STAGE_NAME,
        "workspace_id": result.workspace_id,
        "product_id": result.product_id,
        "state": result.phase6d_state,
        "listing_publishability_state": result.listing_publishability_state,
        "ready_for_6e": result.ready_for_6e,
        "required_inputs": ["MASTER-KEYWORDS-LEAN.json", "phase6/6A/*"],
        "optional_inputs": ["phase6/6B/*"],
        "input_hashes": {
            "phase6a_manifest_sha256": result.phase6a_manifest_sha256,
            "optional_phase6b_manifest_sha256": result.optional_phase6b_manifest_sha256,
            "product_facts_sha256": result.product_facts_sha256,
            "claim_evidence_sha256": result.claim_evidence_sha256,
            "keyword_source_sha256": result.keyword_source_sha256,
        },
        "phase6a_manifest_sha256": result.phase6a_manifest_sha256,
        "optional_phase6b_manifest_sha256": result.optional_phase6b_manifest_sha256,
        "product_facts_sha256": result.product_facts_sha256,
        "claim_evidence_sha256": result.claim_evidence_sha256,
        "keyword_source_sha256": result.keyword_source_sha256,
        "aplus_capability": result.aplus_capability,
        "capability_verified": result.aplus_capability_verified,
        "generated_outputs": sorted(output_hashes),
        "output_hashes": output_hashes,
        "basic_module_count": result.basic_aplus["module_count"],
        "premium_module_count": result.premium_aplus_draft["fixed_module_count"],
        "asset_requirement_count": asset["asset_count"],
        "ready_asset_count": asset["asset_state_counts"].get(ASSET_READY, 0),
        "real_photo_required_count": asset["asset_state_counts"].get(ASSET_REAL_PHOTO_REQUIRED, 0),
        "owner_fact_required_count": asset["asset_state_counts"].get(ASSET_OWNER_FACT_REQUIRED, 0),
        "not_applicable_count": asset["asset_state_counts"].get(ASSET_NOT_APPLICABLE, 0),
        "blocker_count": len(result.blockers),
        "warning_count": len(result.warnings),
        "deterministic_content_sha256": None,
        "previous_valid_manifest_sha256": previous_valid_manifest_sha256,
        "verification": verification or {},
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


def _field_blockers_doc(result):
    blocked = []
    for m in result.basic_aplus["modules"]:
        if m["publishability"] != AREG.READY:
            blocked.append({"module_key": m["module_key"], "position": m["position"],
                            "state": m["publishability"], "blockers": m["blockers"],
                            "owner_fact_dependencies": m["owner_fact_dependencies"],
                            "real_asset_dependencies": m["real_asset_dependencies"]})
    return {
        "schema_version": FIELD_BLOCKERS_SCHEMA_VERSION,
        "workspace_id": result.workspace_id, "product_id": result.product_id,
        "capability": result.aplus_capability,
        "owner_fact_dependencies": result.owner_fact_dependencies,
        "real_asset_dependencies": result.real_asset_dependencies,
        "blocked_modules": blocked,
        "rejected_dynamic_modules": result.premium_aplus_draft["rejected_dynamic_modules"],
    }


def _copy_lineage_doc(result):
    return {
        "schema_version": COPY_LINEAGE_SCHEMA_VERSION,
        "workspace_id": result.workspace_id, "product_id": result.product_id,
        "claim_evidence_sha256": result.claim_evidence_sha256,
        "product_facts_sha256": result.product_facts_sha256,
        "module_copy_lineage": [{"module_key": m["module_key"], "position": m["position"],
                                 "emitted": m["copy_lineage"]["emitted"],
                                 "copy_lineage": m["copy_lineage"], "claim_lineage": m["claim_lineage"],
                                 "asset_lineage": m["asset_lineage"]}
                                for m in result.basic_aplus["modules"]],
    }


def _basic_brief_md(result):
    b = result.basic_aplus
    lines = [f"# Basic A+ Content Brief — {result.product_id}", "",
             f"- Capability: **{result.aplus_capability}** (verified: {result.aplus_capability_verified})",
             f"- Phase 6D state: **{result.phase6d_state}**",
             f"- Listing publishability: **{result.listing_publishability_state}**",
             f"- Ready for Phase 6E creative planning: **{result.ready_for_6e}** "
             "(planning only — NOT publishable)", "",
             "## Modules (all five are held until owner facts + real assets exist)", ""]
    for m in b["modules"]:
        lines.append(f"### {m['position']}. {m['module_key']} — {m['publishability']}")
        lines.append(f"- Purpose: {m['purpose']}")
        lines.append(f"- Customer-visible copy: "
                     + ("(emitted)" if (m['headline'] or m['body_copy']) else "*(empty — blocked)*"))
        if m["owner_fact_dependencies"]:
            lines.append(f"- Owner facts required: {', '.join(m['owner_fact_dependencies'])}")
        if m["real_asset_dependencies"]:
            lines.append(f"- Real assets required: {', '.join(m['real_asset_dependencies'])}")
        lines.append("")
    lines.append("An AI/generated mockup can NEVER satisfy a real-proof gate. The owner remains the only "
                 "manual bridge to Seller Central.")
    return "\n".join(lines) + "\n"


def write_phase6d_artifacts(result, dest_dir=None, *, workspace_dir=None, started_at=None,
                            completed_at=None, verification=None):
    """Build every document + exact bytes IN MEMORY, validate all, compute output hashes, THEN commit
    each file atomically. A validation failure raises and leaves the last valid Phase 6D run intact."""
    workspace_dir = workspace_dir or result.run_dir
    dest_dir = dest_dir or phase6d_dir(workspace_dir)

    basic_doc = result.basic_aplus
    premium_doc = result.premium_aplus_draft
    compliance_doc = result.compliance
    asset_doc = result.asset_manifest
    field_blockers_doc = _field_blockers_doc(result)
    lineage_doc = _copy_lineage_doc(result)
    brief_md = _basic_brief_md(result)

    content = [
        (BASIC_FILENAME, canonical_json(basic_doc)),
        (PREMIUM_FILENAME, canonical_json(premium_doc)),
        (COMPLIANCE_FILENAME, canonical_json(compliance_doc)),
        (ASSET_MANIFEST_FILENAME, canonical_json(asset_doc)),
        (BASIC_BRIEF_FILENAME, brief_md),
        (FIELD_BLOCKERS_FILENAME, canonical_json(field_blockers_doc)),
        (COPY_LINEAGE_FILENAME, canonical_json(lineage_doc)),
    ]
    output_hashes = {}
    for name, text in content:
        rel = _safe_rel(os.path.join(dest_dir, name), workspace_dir)
        output_hashes[rel] = hashlib.sha256(text.encode("utf-8")).hexdigest()

    prev = _previous_manifest_hash(dest_dir)
    manifest = _stage_manifest_doc(result, output_hashes, started_at=started_at,
                                   completed_at=completed_at, previous_valid_manifest_sha256=prev,
                                   verification=verification)

    # validate everything before touching disk
    _raise_on_errors("BASIC-APLUS-CONTENT", validate_basic_aplus(basic_doc))
    _raise_on_errors("PREMIUM-APLUS-DRAFT", validate_premium_draft(premium_doc))
    _raise_on_errors("APLUS-ASSET-MANIFEST", validate_asset_manifest(asset_doc))
    _raise_on_errors("APLUS-COMPLIANCE-REPORT", validate_compliance(compliance_doc, basic_doc,
                                                                    premium_doc, asset_doc))
    _raise_on_errors("STAGE-6D-MANIFEST", validate_stage6d_manifest(manifest, workspace_dir))
    _forbid_later_stage_outputs(dest_dir)

    os.makedirs(dest_dir, exist_ok=True)
    for name, text in content:
        _atomic_write_text(os.path.join(dest_dir, name), text)
    _atomic_write_json(os.path.join(dest_dir, STAGE_MANIFEST_FILENAME), manifest)
    return manifest


def load_basic_aplus(dest_dir):
    return _load_json(os.path.join(dest_dir, BASIC_FILENAME))


def verify_phase6d_artifacts(dest_dir, workspace_dir=None):
    """Re-read the written artifacts and confirm the manifest hashes match the final bytes, required
    artifacts are present, no path escapes, and no later-stage artifact leaked in."""
    workspace_dir = workspace_dir or os.path.dirname(os.path.dirname(os.path.abspath(dest_dir)))
    errors = []
    for name in REQUIRED_ARTIFACTS:
        if not os.path.exists(os.path.join(dest_dir, name)):
            errors.append(f"missing required artifact: {name}")
    man_path = os.path.join(dest_dir, STAGE_MANIFEST_FILENAME)
    if not os.path.exists(man_path):
        errors.append("missing STAGE-6D-MANIFEST.json")
        return {"ok": False, "errors": errors, "checked": []}
    manifest = _load_json(man_path)
    errors.extend(validate_stage6d_manifest(manifest, workspace_dir))
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
        _forbid_later_stage_outputs(dest_dir)
    except Phase6DAssemblyError as e:
        errors.append(str(e))
    return {"ok": not errors, "errors": errors, "checked": sorted(checked)}


def _forbid_later_stage_outputs(dest_dir):
    if not os.path.isdir(dest_dir):
        return
    present = set(os.listdir(dest_dir))
    leaked = present & FORBIDDEN_ARTIFACTS
    if leaked:
        raise Phase6DAssemblyError(f"forbidden later-stage artifact present: {sorted(leaked)}")


# ================================================================ VALIDATORS
def _raise_on_errors(label, errors):
    if errors:
        raise Phase6DAssemblyError(f"{label} failed validation: " + "; ".join(errors))


def _recomputes(doc):
    return doc.get("deterministic_content_sha256") == content_sha256(
        {k: v for k, v in doc.items() if k != "deterministic_content_sha256"})


def validate_basic_aplus(doc):
    errors = []
    if doc.get("schema_version") != BASIC_SCHEMA_VERSION:
        errors.append(f"schema_version must be {BASIC_SCHEMA_VERSION}")
    if not doc.get("workspace_id") or not doc.get("product_id"):
        errors.append("empty workspace/product id")
    if doc.get("capability") not in AREG.CAPABILITY_MODES:
        errors.append(f"invalid capability {doc.get('capability')}")
    mods = doc.get("modules") or []
    expected = 0 if doc.get("capability") == AREG.NO_A_PLUS else AREG.BASIC_MODULE_COUNT
    if len(mods) != expected:
        errors.append(f"{doc.get('capability')} expects {expected} Basic modules, got {len(mods)}")
    if doc.get("module_count") != len(mods):
        errors.append("module_count mismatch")
    ids = [m.get("module_id") for m in mods]
    keys = [m.get("module_key") for m in mods]
    positions = [m.get("position") for m in mods]
    if len(set(ids)) != len(ids):
        errors.append("duplicate module_id")
    if len(set(keys)) != len(keys):
        errors.append("duplicate module_key")
    if len(set(positions)) != len(positions):
        errors.append("duplicate module position")
    for m in mods:
        if m.get("module_key") not in AREG.BASIC_BY_KEY:
            errors.append(f"unknown Basic module key {m.get('module_key')}")
        if m.get("publishability") not in AREG.MODULE_STATES:
            errors.append(f"module {m.get('module_key')} bad state {m.get('publishability')}")
        # a blocked module must not carry customer-visible copy.
        if m.get("publishability") != AREG.READY and (m.get("headline") or m.get("body_copy")):
            errors.append(f"blocked module {m.get('module_key')} must have empty visible copy")
        # a module claiming READY must not be marked here unless truly publishable.
        if m.get("publishability") == AREG.READY and not m.get("publishable"):
            errors.append(f"module {m.get('module_key')} READY but not publishable")
        # emitted (customer-visible) factual copy must carry claim lineage (built from VERIFIED claims).
        if m.get("body_copy") and not (m.get("claim_lineage") or {}).get("verified_claim_ids"):
            errors.append(f"module {m.get('module_key')} emits body copy without claim lineage")
    if doc.get("state") == BASIC_READY and doc.get("capability") not in (
            AREG.BASIC_A_PLUS, AREG.PREMIUM_A_PLUS):
        errors.append("Basic marked READY without a verified Basic/Premium capability")
    if doc.get("publishability") != LISTING_SAFE_DRAFT and doc.get("state") != BASIC_READY:
        errors.append("unexpected publishability for a non-READY Basic result")
    if not _recomputes(doc):
        errors.append("deterministic_content_sha256 does not recompute")
    return errors


def validate_premium_draft(doc):
    errors = []
    if doc.get("schema_version") != PREMIUM_SCHEMA_VERSION:
        errors.append(f"schema_version must be {PREMIUM_SCHEMA_VERSION}")
    if not doc.get("is_draft"):
        errors.append("Premium artifact must be clearly labelled a draft")
    if doc.get("publishability") != PREMIUM_DO_NOT_PUBLISH and not doc.get("premium_eligible"):
        errors.append("non-eligible Premium must be DO_NOT_PUBLISH")
    fixed = doc.get("fixed_modules") or []
    positions = [m.get("position") for m in fixed]
    if len(set(positions)) != len(positions):
        errors.append("duplicate Premium fixed position")
    fb = doc.get("basic_fallback")
    # a boolean-only Basic fallback is rejected: fallback must be the full module payload.
    if not isinstance(fb, list) or not all(isinstance(m, dict) and m.get("module_key") for m in fb):
        errors.append("Basic fallback must be the full module payload (a list of modules), not a boolean")
    else:
        if len(fb) != AREG.BASIC_MODULE_COUNT:
            errors.append(f"Basic fallback must carry {AREG.BASIC_MODULE_COUNT} modules, got {len(fb)}")
        if doc.get("basic_fallback_positions") != list(range(1, AREG.BASIC_MODULE_COUNT + 1)):
            errors.append("Basic fallback positions must be 1..5")
    # no filler dynamic module: every selected dynamic must be genuinely eligible.
    eligible = set(doc.get("eligible_dynamic_modules") or [])
    for k in (doc.get("selected_dynamic_modules") or []):
        if k not in eligible:
            errors.append(f"selected dynamic {k} is not eligible (filler)")
    if len(doc.get("selected_dynamic_modules") or []) > AREG.PREMIUM_DYNAMIC_COUNT:
        errors.append("more than two dynamic modules selected")
    # every dynamic candidate is independently evaluated.
    for c in (doc.get("dynamic_candidates") or []):
        if "eligible" not in c or "ineligible_reasons" not in c:
            errors.append(f"dynamic candidate {c.get('module_key')} lacks independent eligibility")
    if not _recomputes(doc):
        errors.append("deterministic_content_sha256 does not recompute")
    return errors


def validate_asset_manifest(doc):
    errors = []
    if doc.get("schema_version") != ASSET_MANIFEST_SCHEMA_VERSION:
        errors.append(f"schema_version must be {ASSET_MANIFEST_SCHEMA_VERSION}")
    assets = doc.get("assets") or []
    ids = [a.get("asset_id") for a in assets]
    if len(set(ids)) != len(ids):
        errors.append("duplicate asset_id")
    for a in assets:
        if a.get("status") not in ASSET_STATES:
            errors.append(f"asset {a.get('asset_id')} bad status {a.get('status')}")
        # relative-path safety: no absolute path, no parent traversal.
        for p in (a.get("current_relative_paths") or []):
            if os.path.isabs(p) or ".." in str(p).replace(os.sep, "/").split("/"):
                errors.append(f"asset {a.get('asset_id')} unsafe path {p}")
        # a missing file may not be ASSET_READY; ASSET_READY requires a real asset + recorded sha.
        if a.get("status") == ASSET_READY and (a.get("real_or_generated") != AREG.REAL
                                               or not a.get("current_asset_sha256")):
            errors.append(f"asset {a.get('asset_id')} ASSET_READY without a verified real file")
        # a generated asset can never satisfy a real-proof gate.
        if a.get("real_photo_required") and a.get("real_or_generated") == AREG.GENERATED \
                and a.get("status") not in (ASSET_BLOCKED_UNSAFE, ASSET_REAL_PHOTO_REQUIRED):
            errors.append(f"asset {a.get('asset_id')} generated but not blocked for a real-proof gate")
        if a.get("real_photo_required") and a.get("generated_mockup_allowed"):
            errors.append(f"asset {a.get('asset_id')} real-proof gate must not allow a generated mockup")
        # no final prompt body is permitted in this artifact.
        if a.get("expected_future_filename") and str(a["expected_future_filename"]).endswith((".md",)):
            errors.append(f"asset {a.get('asset_id')} must not carry a prompt file")
    if doc.get("generated_accepted_as_real_proof_count", 0) != 0:
        errors.append("a generated asset was accepted as real proof")
    if doc.get("asset_count") != len(assets):
        errors.append("asset_count mismatch")
    if not _recomputes(doc):
        errors.append("deterministic_content_sha256 does not recompute")
    return errors


def validate_compliance(doc, basic_doc, premium_doc, asset_doc):
    errors = []
    if doc.get("schema_version") != COMPLIANCE_SCHEMA_VERSION:
        errors.append(f"schema_version must be {COMPLIANCE_SCHEMA_VERSION}")
    if doc.get("basic_module_count") != basic_doc["module_count"]:
        errors.append("compliance basic_module_count disagrees with artifact")
    if doc.get("dynamic_candidate_count") != premium_doc["dynamic_candidate_count"]:
        errors.append("compliance dynamic_candidate_count disagrees with artifact")
    if doc.get("asset_requirement_result", {}).get("asset_count") != asset_doc["asset_count"]:
        errors.append("compliance asset_count disagrees with artifact")
    if doc.get("phase6d_state") not in PHASE6D_STAGE_STATES:
        errors.append(f"invalid phase6d_state {doc.get('phase6d_state')}")
    if doc.get("generated_assets_accepted_as_real_proof") != 0:
        errors.append("compliance reports a generated asset accepted as real proof")
    if not _recomputes(doc):
        errors.append("deterministic_content_sha256 does not recompute")
    return errors


def validate_stage6d_manifest(doc, workspace_dir):
    errors = []
    if doc.get("schema_version") != STAGE_MANIFEST_SCHEMA_VERSION:
        errors.append("invalid manifest schema_version")
    if doc.get("stage_id") != STAGE_ID:
        errors.append("manifest stage_id must be 6D")
    if doc.get("state") not in PHASE6D_STAGE_STATES:
        errors.append(f"invalid manifest state {doc.get('state')}")
    outputs = doc.get("output_hashes", {})
    seen = set()
    required_present = set()
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
            errors.append(f"manifest lists a forbidden later-stage artifact: {base}")
    for name in REQUIRED_ARTIFACTS:
        if name not in required_present:
            errors.append(f"manifest missing required artifact: {name}")
    if STAGE_MANIFEST_FILENAME in {r.split("/")[-1] for r in outputs}:
        errors.append("manifest must not list its own hash in output_hashes")
    if doc.get("deterministic_content_sha256") != content_sha256(_manifest_hashable(doc)):
        errors.append("manifest deterministic_content_sha256 does not recompute")
    return errors


# ================================================================ CLI
def main():
    import argparse
    ap = argparse.ArgumentParser(description="Phase 6D — assemble a truthful, evidence-gated A+ draft.")
    ap.add_argument("run_dir")
    ap.add_argument("--mode", default="CONNECTED_RESEARCH",
                    choices=["CONNECTED_RESEARCH", "LOCAL_SAFE", "TEST_DENY_EXTERNAL"])
    ap.add_argument("--write", action="store_true", help="write artifacts under <run>/phase6/6D")
    a = ap.parse_args()
    result = assemble_phase6d(a.run_dir, connectivity_mode=a.mode)
    if a.write:
        write_phase6d_artifacts(result, workspace_dir=a.run_dir, started_at="cli", completed_at="cli")
        v = verify_phase6d_artifacts(phase6d_dir(a.run_dir), workspace_dir=a.run_dir)
        print(f"wrote Phase 6D artifacts; verify ok={v['ok']}")
    print(f"capability={result.aplus_capability} verified={result.aplus_capability_verified}  "
          f"phase6d_state={result.phase6d_state}  ready_for_6e={result.ready_for_6e}")
    print(f"basic modules={result.basic_aplus['module_count']} ready={result.basic_aplus['basic_ready_count']}  "
          f"premium eligible={result.premium_aplus_draft['premium_eligible']}  "
          f"assets={result.asset_manifest['asset_count']}")


if __name__ == "__main__":
    main()
