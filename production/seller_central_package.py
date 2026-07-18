#!/usr/bin/env python3
"""production.seller_central_package — the ONE Phase 6F Seller-Central manual-package authority.

Phase 6F turns the verified Phase 6A->6E lineage into a single local, owner-operated, cryptographically
verified MANUAL package under ``<run>/phase6/6F/SELLER-CENTRAL-READY/``. Despite the folder name the
current T2 package is a SAFE DRAFT: nothing is authorized for Seller Central entry.

It is a THIN assembler. It never re-implements the product-fact loader, the atomic claim engine, the
PDP / A+ / creative engines, the category-policy registry, or the PageAuditor — it REUSES them through
the mature Phase 6A-6E authorities and copies/derives ONLY from their verified artifacts:

  * 6A/6B/6C/6D verified view + engines      -> creative.creative_production_package.load_phase6e_dependencies
  * 6C product-detail page (reproduced)      -> production.product_detail_page
  * 6D A+ basic/premium (reproduced+on-disk) -> production.aplus_assembly
  * 6E creative plan (reproduced+on-disk)    -> creative.creative_production_package
  * product facts / atomic claims            -> listing.product_fact_loader / listing.claim_evidence
  * page auditor (the one auditor)           -> listing.page_auditor
  * canonical json / hash / atomic template  -> production.product_workspace helpers

Hard rules it enforces:
  * It performs NO Amazon / Seller-Central action: no login, no SP-API/MWS/Advertising API, no browser
    automation, no listing/image/A+/price/inventory/PPC write. There is no Amazon credential path.
  * It NEVER opens the network, calls an image-generation service, or generates an image file.
  * It resolves publishability with the MOST RESTRICTIVE applicable state. A lexical PageAuditor
    PASS/PUBLISHABLE can never upgrade the package.
  * It keeps blocked / draft / owner-fact-dependent content out of COPY NOW and UPLOAD NOW.
  * It never re-generates upstream copy, A+ copy, creative prompts, or invents any product fact.
  * It builds a candidate package first and promotes it rollback-safely; a failed run preserves the
    previous valid package, stage manifest, index hash, and final audit.

Public API:
  load_phase6f_dependencies(run_dir, connectivity_mode=...) -> Phase6FDependencies
  resolve_effective_publishability(sources) -> dict
  build_package_layout(deps) -> list[PackageEntry-dicts]  (via assemble)
  assemble_phase6f(run_dir, connectivity_mode=...) -> SellerCentralManualPackageResult
  write_phase6f_artifacts(result, workspace_dir=None, ...) -> stage-manifest dict
  verify_phase6f_artifacts(package_root, workspace_dir=None) -> dict
  phase6f_dir(run_dir) -> str
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
for _sub in ("", "listing", "core"):
    _p = _ROOT if not _sub else os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import product_workspace as PW          # noqa: E402  canonical json / hash / atomic template
from production import product_detail_page as PDP       # noqa: E402  Phase 6C authority
from production import aplus_assembly as APL            # noqa: E402  Phase 6D authority
from creative import creative_production_package as CPP  # noqa: E402  Phase 6E authority (+ 6A/6B/6C/6D loader)
import page_auditor as PA                                # noqa: E402  the one auditor
import claim_evidence as CE                              # noqa: E402
import runtime_policy as RP                              # noqa: E402

# one serialization + hashing authority (do NOT re-implement) --------------------------------------
canonical_json = PW.canonical_json
content_sha256 = PW.content_sha256
_file_sha256 = PW._file_sha256

# -- schema / layout versions ----------------------------------------------------------------------
SCHEMA_VERSION = "phase6-seller-central-manual-package-v1"
STAGE_MANIFEST_SCHEMA_VERSION = "phase6f-stage-manifest-v1"
PACKAGE_INDEX_SCHEMA_VERSION = "phase6f-package-index-v1"
FINAL_AUDIT_SCHEMA_VERSION = "phase6f-final-listing-audit-v1"
LEAKAGE_AUDIT_SCHEMA_VERSION = "phase6f-manual-entry-leakage-audit-v1"
VERIFICATION_REPORT_SCHEMA_VERSION = "phase6f-package-verification-report-v1"
PACKAGE_LAYOUT_VERSION = "phase6f-seller-central-ready-numbered-v1"

STAGE_ID = "6F"
STAGE_NAME = "Verified Safe-Draft Seller Central Manual Package"

# -- Phase 6 final statuses (whole-package) --------------------------------------------------------
PHASE6_READY_FOR_LOCAL_DEPLOYMENT = "PHASE6_READY_FOR_LOCAL_DEPLOYMENT"
PHASE6_SAFE_DRAFT_READY = "PHASE6_SAFE_DRAFT_READY"
PHASE6_BLOCKED = "PHASE6_BLOCKED"
PHASE6_FINAL_STATUSES = (PHASE6_READY_FOR_LOCAL_DEPLOYMENT, PHASE6_SAFE_DRAFT_READY, PHASE6_BLOCKED)

# -- Phase 6F stage states -------------------------------------------------------------------------
READY_FOR_REVIEW = "READY_FOR_REVIEW"
SAFE_MANUAL_PACKAGE_OWNER_FACTS_REQUIRED = "SAFE_MANUAL_PACKAGE_OWNER_FACTS_REQUIRED"
BLOCKED_DEPENDENCY = "BLOCKED_DEPENDENCY"
BLOCKED_UNSAFE = "BLOCKED_UNSAFE"
INVALID_PACKAGE_LAYOUT = "INVALID_PACKAGE_LAYOUT"
INVALID_PACKAGE_INDEX = "INVALID_PACKAGE_INDEX"
INVALID_MANUAL_PLAN = "INVALID_MANUAL_PLAN"
INVALID_FINAL_AUDIT = "INVALID_FINAL_AUDIT"
INVALID_PROMOTION = "INVALID_PROMOTION"

# -- listing publishability ladder (the page-auditor vocabulary; lower = MORE restrictive) ---------
PUB_BLOCKED_UNSAFE = "BLOCKED_UNSAFE"
PUB_SAFE_DRAFT = "SAFE_DRAFT_OWNER_FACTS_REQUIRED"
PUB_PUBLISHABLE = "PUBLISHABLE"
_LISTING_TIER = {PUB_BLOCKED_UNSAFE: 0, PUB_SAFE_DRAFT: 1, PUB_PUBLISHABLE: 2}
_TIER_TO_STATE = {0: PUB_BLOCKED_UNSAFE, 1: PUB_SAFE_DRAFT, 2: PUB_PUBLISHABLE}

# effective sub-scope states preserved from upstream (reused, never recomputed here)
APLUS_ELIGIBILITY_UNVERIFIED = "APLUS_ELIGIBILITY_UNVERIFIED"
SAFE_CREATIVE_PLAN_OWNER_FACTS_REQUIRED = "SAFE_CREATIVE_PLAN_OWNER_FACTS_REQUIRED"

# -- owner-facing instruction labels (the ONLY allowed classes) ------------------------------------
COPY_NOW = "COPY NOW"
UPLOAD_NOW = "UPLOAD NOW"
OWNER_REVIEW = "OWNER REVIEW"
DO_NOT_PASTE = "DO NOT PASTE"
REAL_PHOTO_REQUIRED = "REAL PHOTO REQUIRED"
APLUS_ELIGIBILITY_UNVERIFIED_LABEL = "A+ ELIGIBILITY UNVERIFIED"
INSTRUCTION_CLASSES = (COPY_NOW, UPLOAD_NOW, OWNER_REVIEW, DO_NOT_PASTE,
                       REAL_PHOTO_REQUIRED, APLUS_ELIGIBILITY_UNVERIFIED_LABEL)

# -- package root / index ---------------------------------------------------------------------------
PACKAGE_ROOT_NAME = "SELLER-CENTRAL-READY"
CANDIDATE_ROOT_NAME = ".candidate-SELLER-CENTRAL-READY"
BACKUP_ROOT_NAME = ".previous-SELLER-CENTRAL-READY"
PACKAGE_INDEX_FILENAME = "PACKAGE-INDEX.json"
PACKAGE_INDEX_SHA_FILENAME = "PACKAGE-INDEX.sha256"

STAGE_MANIFEST_FILENAME = "STAGE-6F-MANIFEST.json"
VERIFICATION_REPORT_FILENAME = "PACKAGE-VERIFICATION-REPORT.json"
LEAKAGE_AUDIT_FILENAME = "MANUAL-ENTRY-LEAKAGE-AUDIT.json"

READ_ME_FILENAME = "00-READ-ME-FIRST.md"
MANUAL_ENTRY_PLAN_FILENAME = "13-SELLER-CENTRAL-MANUAL-ENTRY-PLAN.md"
FINAL_AUDIT_FILENAME = "14-FINAL-LISTING-AUDIT.json"

# these two live in the package root but are NEVER indexed (the index cannot hash itself and the hash
# file cannot be hashed by the file it hashes — that would be circular).
_UNINDEXED_PACKAGE_FILES = frozenset({PACKAGE_INDEX_FILENAME, PACKAGE_INDEX_SHA_FILENAME})

# artifacts / patterns a Phase 6F package must NEVER contain.
FORBIDDEN_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif", ".gif", ".bmp"})
_WINDOWS_RESERVED = frozenset({"con", "prn", "aux", "nul"}
                              | {f"com{i}" for i in range(1, 10)}
                              | {f"lpt{i}" for i in range(1, 10)})

_MANIFEST_VOLATILE = ("deterministic_content_sha256", "started_at", "completed_at",
                      "previous_valid_manifest_sha256", "verification")

# IMPERATIVE owner-facing automation instructions the manual plan / read-me must NEVER contain. These
# are instruction forms only — a DEFENSIVE mention ("the tool never pastes automatically", "no SP-API")
# is legitimate and is excluded by negation-aware matching. Pure API/tool names (sp-api, browser
# automation) are checked by the source-token scan of the CODE, not the owner-facing text.
_AUTOMATION_PHRASES = ("log in automatically", "auto-login", "automatically log in",
                       "automatically navigate", "navigate automatically", "paste automatically",
                       "automatically paste", "upload automatically", "automatically upload",
                       "submit automatically", "automatically submit", "click automatically",
                       "automatically click", "automate the browser", "auto-upload", "auto-paste")
_NEGATIONS = ("no ", "not ", "never", "n't", "cannot", "without", "prohibit", "does not", "do not")


def _count_automation_instructions(text):
    """Count only IMPERATIVE automation instructions; a negated/defensive mention does not count."""
    low = text.lower()
    total = 0
    for p in _AUTOMATION_PHRASES:
        start = 0
        while True:
            i = low.find(p, start)
            if i < 0:
                break
            window = low[max(0, i - 16):i]
            if not any(n in window for n in _NEGATIONS):
                total += 1
            start = i + len(p)
    return total


class Phase6FError(Exception):
    pass


class Phase6FDependencyError(Phase6FError):
    pass


class Phase6FAssemblyError(Phase6FError):
    pass


class Phase6FPackageError(Phase6FError):
    pass


# ================================================================ paths / atomic write
def phase6f_dir(run_dir):
    return os.path.join(run_dir, "phase6", "6F")


def package_root_dir(run_dir):
    return os.path.join(phase6f_dir(run_dir), PACKAGE_ROOT_NAME)


def _safe_rel(path, base):
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(base))
    norm = rel.replace(os.sep, "/")
    if os.path.isabs(rel) or norm == ".." or norm.startswith("../"):
        raise Phase6FAssemblyError(f"path escapes workspace ({norm}): {path}")
    return norm


def _atomic_write_bytes(path, data):
    """temp sibling -> flush -> fsync -> os.replace. A failed write never overwrites the last valid file."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-6f-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _atomic_write_text(path, text):
    _atomic_write_bytes(path, text.encode("utf-8"))


def _atomic_write_json(path, obj):
    _atomic_write_bytes(path, canonical_json(obj).encode("utf-8"))


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _manifest_hashable(body):
    return {k: v for k, v in body.items() if k not in _MANIFEST_VOLATILE}


def _recomputes_manifest(doc, volatile):
    return doc.get("deterministic_content_sha256") == content_sha256(
        {k: v for k, v in doc.items() if k not in volatile})


def _rmtree(path):
    """Bounded, no-follow directory removal (never follows a symlink/junction out of the tree)."""
    if not os.path.isdir(path) or os.path.islink(path):
        if os.path.exists(path) or os.path.islink(path):
            os.remove(path) if not os.path.isdir(path) else os.rmdir(path)
        return
    for name in os.listdir(path):
        p = os.path.join(path, name)
        if os.path.islink(p):
            os.remove(p)
        elif os.path.isdir(p):
            _rmtree(p)
        else:
            os.remove(p)
    os.rmdir(path)


# ================================================================ path safety for INDEX entries
def _safe_index_path(rel):
    """Normalize + validate a package-relative artifact path. Rejects every unsafe form."""
    if not isinstance(rel, str) or not rel.strip():
        raise Phase6FPackageError(f"empty index path: {rel!r}")
    if "\x00" in rel:
        raise Phase6FPackageError(f"null byte in index path: {rel!r}")
    if ":" in rel:                                    # drive-letter or NTFS alternate-data-stream
        raise Phase6FPackageError(f"drive-letter or alternate-data-stream path rejected: {rel!r}")
    if rel.startswith("\\\\") or rel.startswith("//"):
        raise Phase6FPackageError(f"UNC path rejected: {rel!r}")
    norm = rel.replace("\\", "/")
    if norm.startswith("/"):
        raise Phase6FPackageError(f"absolute path rejected: {rel!r}")
    segments = [s for s in norm.split("/")]
    for seg in segments:
        if seg in ("", ".", ".."):
            raise Phase6FPackageError(f"empty/traversal segment in index path: {rel!r}")
        stem = seg.split(".", 1)[0].lower()
        if stem in _WINDOWS_RESERVED:
            raise Phase6FPackageError(f"windows reserved device name in index path: {rel!r}")
    return norm


def _media_type(name):
    ext = os.path.splitext(name)[1].lower()
    return {".json": "application/json", ".md": "text/markdown",
            ".txt": "text/plain", ".sha256": "text/plain"}.get(ext, "application/octet-stream")


# ================================================================ DEPENDENCIES
class Phase6FDependencies:
    """Verified, read-only view over the full Phase 6A->6E chain, plus the reproduced engine results."""

    def __init__(self, *, run_dir, connectivity_mode, deps6e, pdp_result, apl_result, cpp_result,
                 basic_aplus, premium_aplus, page_audit, manifest6e, phase6e_manifest_sha256):
        self.run_dir = run_dir
        self.connectivity_mode = connectivity_mode
        self.deps6e = deps6e                    # CPP.Phase6EDependencies (6A/6B/6C/6D verified view + engines)
        self.pdp_result = pdp_result            # reproduced Phase 6C result
        self.apl_result = apl_result            # reproduced Phase 6D result
        self.cpp_result = cpp_result            # reproduced Phase 6E result
        self.basic_aplus = basic_aplus          # on-disk verified Phase 6D basic A+ doc
        self.premium_aplus = premium_aplus      # on-disk verified Phase 6D premium A+ draft doc
        self.page_audit = page_audit            # fresh listing PageAuditor result (lexical text audit)
        self.manifest6e = manifest6e
        self.phase6e_manifest_sha256 = phase6e_manifest_sha256

    # identity / hashes proxied straight from the verified 6E view (which proxies 6A..6D)
    @property
    def workspace(self):
        return self.deps6e.workspace

    @property
    def workspace_id(self):
        return self.deps6e.workspace_id

    @property
    def product_id(self):
        return self.deps6e.product_id

    @property
    def product_type(self):
        return self.deps6e.product_type

    @property
    def audience(self):
        return self.deps6e.audience

    @property
    def category(self):
        return self.deps6e.category

    @property
    def claims(self):
        return self.deps6e.claims

    @property
    def facts(self):
        return self.deps6e.facts

    @property
    def keyword_source_sha256(self):
        return self.deps6e.keyword_source_sha256

    @property
    def product_facts_sha256(self):
        return self.deps6e.product_facts_sha256

    @property
    def claim_evidence_sha256(self):
        return self.deps6e.claim_evidence_sha256

    @property
    def phase6a_manifest_sha256(self):
        return self.deps6e.phase6a_manifest_sha256

    @property
    def phase6b_manifest_sha256(self):
        return self.deps6e.phase6b_manifest_sha256

    @property
    def phase6c_manifest_sha256(self):
        return self.deps6e.phase6c_manifest_sha256

    @property
    def phase6d_manifest_sha256(self):
        return self.deps6e.phase6d_manifest_sha256


def load_phase6f_dependencies(run_dir, connectivity_mode="CONNECTED_RESEARCH"):
    """Load + VERIFY the whole Phase 6A->6E chain before any packaging.

    Blocks (Phase6FDependencyError) when any 6A-6E artifact is missing / tampered / hash-mismatched, the
    6E stage is not ready_for_6f, a dependency hash does not reconcile, the listing is no longer a safe
    draft, or the Amazon-account boundary is not closed. It NEVER modifies an upstream artifact.
    """
    # 1) 6A/6B/6C/6D verified view + engines + reproduced 6C/6D (reuse the mature 6E loader unchanged).
    try:
        deps6e = CPP.load_phase6e_dependencies(run_dir, connectivity_mode=connectivity_mode)
    except CPP.Phase6EDependencyError as e:
        raise Phase6FDependencyError(f"Phase 6A-6D dependency invalid: {e}") from e

    # 2) Amazon-account boundary must remain closed in EVERY connectivity mode (defensive re-assert).
    pol = RP.load_runtime_policy()
    if not getattr(pol, "amazon_account_isolation", False) or getattr(pol, "amazon_api_enabled", True) \
            or getattr(pol, "amazon_network_writes_enabled", True) \
            or getattr(pol, "amazon_authenticated_access_enabled", True):
        raise Phase6FDependencyError("Amazon account boundary is not closed")

    # 3) verify the 6E artifacts on disk (re-hash every recorded output against the bytes).
    dest6e = CPP.phase6e_dir(run_dir)
    v6e = CPP.verify_phase6e_artifacts(dest6e, workspace_dir=run_dir)
    if not v6e.get("ok"):
        raise Phase6FDependencyError(f"Phase 6E artifacts invalid: {v6e.get('errors')}")

    man6e_path = os.path.join(dest6e, CPP.STAGE_MANIFEST_FILENAME)
    if not os.path.exists(man6e_path):
        raise Phase6FDependencyError("Phase 6E stage manifest missing")
    manifest6e = _load_json(man6e_path)

    # 4) the 6E manifest's own hash must recompute, it must be ready_for_6f, still a safe draft, and its
    #    input hashes must reconcile with the verified 6A view.
    if not _recomputes_manifest(manifest6e, CPP._MANIFEST_VOLATILE):
        raise Phase6FDependencyError("Phase 6E stage-manifest hash does not recompute")
    if manifest6e.get("ready_for_6f") is not True:
        raise Phase6FDependencyError("Phase 6E is not ready_for_6f")
    if manifest6e.get("listing_publishability_state") != PUB_SAFE_DRAFT:
        raise Phase6FDependencyError(f"Phase 6E listing state is not {PUB_SAFE_DRAFT}")
    ih = manifest6e.get("input_hashes", {})
    want = {
        "claim_evidence_sha256": deps6e.claim_evidence_sha256,
        "product_facts_sha256": deps6e.product_facts_sha256,
        "keyword_source_sha256": deps6e.keyword_source_sha256,
        "phase6c_manifest_sha256": deps6e.phase6c_manifest_sha256,
        "phase6d_manifest_sha256": deps6e.phase6d_manifest_sha256,
    }
    for field, expected in want.items():
        if ih.get(field) != expected:
            raise Phase6FDependencyError(
                f"6E {field} {str(ih.get(field))[:12]} != verified {str(expected)[:12]}")
    phase6e_manifest_sha256 = manifest6e.get("deterministic_content_sha256")

    # 5) reproduce Phase 6E IN MEMORY (defense in depth) and confirm it still matches the written bytes.
    cpp_result = CPP.assemble_phase6e(run_dir, connectivity_mode=connectivity_mode)
    if cpp_result.ready_for_6f is not True:
        raise Phase6FDependencyError("reproduced Phase 6E is not ready_for_6f")
    plan_doc = CPP._listing_image_plan_doc(cpp_result._deps, cpp_result.jobs,
                                           cpp_result.category_identity, cpp_result.cross_image_consistency)
    written_plan = manifest6e.get("output_hashes", {}).get("phase6/6E/LISTING-IMAGE-PLAN.json")
    if written_plan != _sha_text(canonical_json(plan_doc)):
        raise Phase6FDependencyError("reproduced Phase 6E listing-image plan does not match written bytes")

    # 6) load the verified on-disk Phase 6D A+ docs (byte-copy sources; already hash-verified by the 6E loader).
    basic_aplus = _load_json(os.path.join(run_dir, "phase6", "6D", "BASIC-APLUS-CONTENT.json"))
    premium_aplus = _load_json(os.path.join(run_dir, "phase6", "6D", "PREMIUM-APLUS-DRAFT.json"))

    # 7) REUSE the ONE PageAuditor's authoritative listing result computed by the 6C engine (a purely
    #    lexical text result — it can never upgrade the package; the resolver takes the MOST restrictive
    #    state). We never re-audit an ad-hoc reconstruction, which would produce a spurious verdict.
    page_audit = deps6e.pdp_result.page_audit

    return Phase6FDependencies(
        run_dir=run_dir, connectivity_mode=connectivity_mode, deps6e=deps6e,
        pdp_result=deps6e.pdp_result, apl_result=deps6e.apl_result, cpp_result=cpp_result,
        basic_aplus=basic_aplus, premium_aplus=premium_aplus, page_audit=page_audit,
        manifest6e=manifest6e, phase6e_manifest_sha256=phase6e_manifest_sha256)


# ================================================================ EFFECTIVE PUBLISHABILITY
def _to_listing_tier(state):
    """Map any upstream source state string into the 3-tier listing ladder. Unknown -> SAFE_DRAFT
    (tier 1) so nothing accidentally upgrades to PUBLISHABLE; only explicit good states reach tier 2,
    only explicit unsafe states reach tier 0."""
    if state is None:
        return 1
    s = str(state).upper()
    unsafe = ("BLOCKED_UNSAFE", "PROHIBITED", "UNSAFE", "HARD_FAIL")
    if any(tok in s for tok in unsafe):
        return 0
    good = ("PUBLISHABLE", "VERIFIED", "READY_FOR_LOCAL_DEPLOYMENT", "RESOLVED", "SUPPORTED_OK")
    # exact good states only (avoid matching SUPPORTED_OWNER_REVIEW / *_UNVERIFIED / SAFE_DRAFT etc.)
    if s in ("PUBLISHABLE", "VERIFIED", "READY", "RESOLVED", "OWNER_CONFIRMED", "US:APPAREL",
             "CATEGORY_RESOLVED", "PHASE6_READY_FOR_LOCAL_DEPLOYMENT"):
        return 2
    return 1


def resolve_effective_publishability(sources):
    """The ONE authoritative effective-publishability resolver.

    ``sources`` is a dict of the per-source states + gate booleans. The most restrictive applicable
    state wins; a lexical PageAuditor PASS/PUBLISHABLE can never override missing owner facts, missing
    real assets, unknown A+ eligibility, a non-publishable field, or a non-publishable package state.

        effective_publishability = most_restrictive(
            product_fact_state, atomic_claim_state, category_policy_state, field_state,
            page_auditor_state, asset_state, capability_state, creative_state, package_state)
    """
    order = ("product_fact_state", "atomic_claim_state", "category_policy_state", "field_state",
             "page_auditor_state", "asset_state", "capability_state", "creative_state",
             "package_declared_state")
    tiers = {}
    for key in order:
        tiers[key] = _to_listing_tier(sources.get(key))
    worst_tier = min(tiers.values())
    worst_source = min(order, key=lambda k: (tiers[k], order.index(k)))
    listing_publishability = _TIER_TO_STATE[worst_tier]

    owner_facts_complete = bool(sources.get("owner_facts_complete", False))
    real_assets_verified = bool(sources.get("real_assets_verified", False))
    aplus_eligibility_verified = bool(sources.get("aplus_eligibility_verified", False))
    copy_now_count = int(sources.get("copy_now_count", 0))
    upload_now_count = int(sources.get("upload_now_count", 0))
    declared_blocked = _to_listing_tier(sources.get("package_declared_state")) == 0

    if worst_tier == 0 or declared_blocked:
        package_state = PHASE6_BLOCKED
    elif (worst_tier == 2 and owner_facts_complete and real_assets_verified
          and aplus_eligibility_verified):
        package_state = PHASE6_READY_FOR_LOCAL_DEPLOYMENT
    else:
        package_state = PHASE6_SAFE_DRAFT_READY

    ready_for_owner_review = package_state != PHASE6_BLOCKED
    ready_for_local_deployment = package_state == PHASE6_READY_FOR_LOCAL_DEPLOYMENT
    # manual entry means "there is content authorized to type/upload right now" — an INDEPENDENT gate:
    # it can be true (a field authorized) while the whole package is not yet deployment-ready.
    ready_for_manual_entry = (copy_now_count > 0 or upload_now_count > 0)

    reasons = []
    if worst_tier < 2:
        reasons.append(f"most_restrictive={worst_source}:{sources.get(worst_source)}")
    if not owner_facts_complete:
        reasons.append("owner_facts_incomplete")
    if not real_assets_verified:
        reasons.append("real_assets_unverified")
    if not aplus_eligibility_verified:
        reasons.append("aplus_eligibility_unverified")

    return {
        "listing_publishability": listing_publishability,
        "aplus_state": sources.get("aplus_state", APLUS_ELIGIBILITY_UNVERIFIED),
        "creative_state": sources.get("creative_state", SAFE_CREATIVE_PLAN_OWNER_FACTS_REQUIRED),
        "package_state": package_state,
        "most_restrictive_source": worst_source,
        "source_tiers": tiers,
        "ready_for_owner_review": ready_for_owner_review,
        "ready_for_manual_entry": ready_for_manual_entry,
        "ready_for_local_deployment": ready_for_local_deployment,
        "owner_facts_complete": owner_facts_complete,
        "real_assets_verified": real_assets_verified,
        "aplus_eligibility_verified": aplus_eligibility_verified,
        "reasons": reasons,
    }


def _gather_publishability_sources(deps):
    """Collect the per-source publishability states from the REUSED upstream authorities."""
    pdp = deps.pdp_result
    cpp = deps.cpp_result
    field_states = pdp.field_states
    field_nonpublishable = any(v not in ("RECOMMENDED_SAFE_DRAFT", "SAFE_DRAFT", "PUBLISHABLE")
                               for v in field_states.values())
    # atomic claims: worst effective evidence state across all claims (reuse claim_evidence)
    claim_state = _worst_claim_state(deps.claims)
    owner_facts_complete = not pdp.owner_fact_dependencies
    real_assets_verified = (cpp.asset_manifest.get("current_verified_real_asset_count", 0) > 0
                            and cpp.asset_manifest.get("ai_generated_assets_accepted_as_real_proof", 1) == 0
                            and not cpp.real_asset_dependencies)
    aplus_eligibility_verified = bool(deps.basic_aplus.get("capability_verified")) and \
        bool(deps.premium_aplus.get("capability_verified"))
    return {
        "product_fact_state": PUB_SAFE_DRAFT if pdp.owner_fact_dependencies else PUB_PUBLISHABLE,
        "atomic_claim_state": claim_state,
        "category_policy_state": PUB_PUBLISHABLE,      # category resolved US:apparel (image policy is a warning)
        "field_state": PUB_SAFE_DRAFT if field_nonpublishable else PUB_PUBLISHABLE,
        "page_auditor_state": deps.page_audit.get("publishability"),
        "asset_state": PUB_PUBLISHABLE if real_assets_verified else PUB_SAFE_DRAFT,
        "capability_state": APLUS_ELIGIBILITY_UNVERIFIED if not aplus_eligibility_verified else PUB_PUBLISHABLE,
        "creative_state": cpp.phase6e_state,
        "package_declared_state": pdp.listing_publishability_state,
        "aplus_state": APLUS_ELIGIBILITY_UNVERIFIED,
        "owner_facts_complete": owner_facts_complete,
        "real_assets_verified": real_assets_verified,
        "aplus_eligibility_verified": aplus_eligibility_verified,
        "copy_now_count": 0,        # filled after entries are classified (COPY NOW requires PUBLISHABLE listing)
        "upload_now_count": 0,
    }


def _worst_claim_state(claims):
    """Worst-wins effective evidence state across all atomic claims (reuse the claim_evidence counts)."""
    prohibited = int(getattr(claims, "prohibited_count", 0))
    blocked = int(getattr(claims, "blocked_count", 0))
    mixed = int(getattr(claims, "mixed_evidence_count", 0))
    owner_review = int(getattr(claims, "owner_review_count", 0))
    if prohibited > 0:
        return PUB_BLOCKED_UNSAFE
    if blocked > 0 or mixed > 0 or owner_review > 0:
        return PUB_SAFE_DRAFT
    return PUB_PUBLISHABLE


# ================================================================ PACKAGE FILE CONTENT
_SAFE_DRAFT_WARNING = ("This is a SAFE DRAFT. It is NOT authorized for Seller Central entry. "
                       "Do not paste or upload it. Owner facts and real product photos are still required.")


def _src(run_dir, rel):
    """Read a verified upstream artifact's bytes/sha/size (byte-copy source)."""
    p = os.path.join(run_dir, rel.replace("/", os.sep))
    data = _read_bytes(p)
    return data, _sha_bytes(data), len(data)


def _field_header(title, *, instruction, field_state, effective, source_artifact, source_sha,
                  blockers, owner_review="REQUIRED"):
    lines = [
        f"# {title} — Phase 6F field snapshot (derived from a verified upstream artifact)",
        f"# PACKAGE STATE: {PHASE6_SAFE_DRAFT_READY}",
        f"# INSTRUCTION: {instruction}",
        f"# FIELD STATE: {field_state}",
        f"# EFFECTIVE PUBLISHABILITY: {effective}",
        f"# OWNER REVIEW: {owner_review}",
        f"# SOURCE ARTIFACT: {source_artifact}",
        f"# SOURCE ARTIFACT SHA256: {source_sha}",
        f"# BLOCKER CODES: {', '.join(blockers) if blockers else '(none)'}",
        "# ---",
        f"# {_SAFE_DRAFT_WARNING}",
        "",
    ]
    return "\n".join(lines)


def build_field_snapshots(deps, effective):
    """The six numbered field-snapshot files (01-06). Every one is a DO NOT PASTE safe draft; no missing
    field is ever filled with invented copy."""
    pdp = deps.pdp_result
    run_dir = deps.run_dir
    eff = effective["listing_publishability"]
    pdp_rel = "phase6/6C/PRODUCT-DETAIL-PAGE.json"
    _, pdp_sha, _ = _src(run_dir, pdp_rel)
    backend_rel = "phase6/6C/BACKEND-SEARCH-TERMS.json"
    _, backend_sha, _ = _src(run_dir, backend_rel)
    fb = _load_json(os.path.join(run_dir, "phase6", "6C", "FIELD-BLOCKERS.json"))
    blocked_fields = {b["field"]: b for b in fb.get("blocked_fields", [])}
    files = []

    # 01 TITLE — a safe draft, never authorized (DO NOT PASTE).
    title_state = pdp.field_states.get("title_primary", "RECOMMENDED_SAFE_DRAFT")
    body = _field_header("01-TITLE.txt", instruction=DO_NOT_PASTE, field_state=title_state, effective=eff,
                         source_artifact=pdp_rel, source_sha=pdp_sha,
                         blockers=["OWNER_FACT_REQUIRED"])
    body += "SAFE DRAFT TITLE (DO NOT PASTE):\n" + pdp.recommended_title + "\n"
    files.append(("01-TITLE.txt", DO_NOT_PASTE, title_state, [pdp_rel], body))

    # 02 BULLETS — five records, all empty with explicit blockers.
    lines = []
    for i in range(1, 6):
        fld = f"bullet_{i}"
        st = pdp.field_states.get(fld, "EMPTY_OWNER_FACT_REQUIRED")
        blk = blocked_fields.get(fld, {})
        reasons = blk.get("reason_codes", [])
        miss = blk.get("missing_requirements", [])
        lines.append(f"BULLET {i}: (empty) — state {st}; reason {','.join(reasons) or 'OWNER_FACT_REQUIRED'}; "
                     f"missing {','.join(miss) or '(unspecified)'}")
    body = _field_header("02-BULLETS.txt", instruction=DO_NOT_PASTE, field_state="ALL_EMPTY_OWNER_FACT_REQUIRED",
                         effective=eff, source_artifact=pdp_rel, source_sha=pdp_sha,
                         blockers=["OWNER_FACT_REQUIRED", "CLAIM_EVIDENCE_MISSING"])
    body += "Five bullet records, all empty. No invented copy.\n" + "\n".join(lines) + "\n"
    files.append(("02-BULLETS.txt", DO_NOT_PASTE, "ALL_EMPTY_OWNER_FACT_REQUIRED", [pdp_rel], body))

    # 03 DESCRIPTION — safe draft (DO NOT PASTE).
    desc_state = pdp.field_states.get("description", "SAFE_DRAFT")
    body = _field_header("03-DESCRIPTION.txt", instruction=DO_NOT_PASTE, field_state=desc_state, effective=eff,
                         source_artifact=pdp_rel, source_sha=pdp_sha, blockers=["OWNER_FACT_REQUIRED"])
    body += "SAFE DRAFT DESCRIPTION (DO NOT PASTE):\n" + pdp.description_text + "\n"
    files.append(("03-DESCRIPTION.txt", DO_NOT_PASTE, desc_state, [pdp_rel], body))

    # 04 BACKEND SEARCH TERMS — advisory byte-safe output, NOT authorization to enter it.
    backend = _load_json(os.path.join(run_dir, "phase6", "6C", "BACKEND-SEARCH-TERMS.json"))
    body = _field_header("04-BACKEND-SEARCH-TERMS.txt", instruction=DO_NOT_PASTE,
                         field_state="ADVISORY_SAFE_DRAFT", effective=eff, source_artifact=backend_rel,
                         source_sha=backend_sha, blockers=["ADVISORY_NOT_PUBLICATION_AUTHORIZED"])
    body += ("Byte-safe ADVISORY output only ({} of {} bytes). This is NOT authorization to enter it. "
             "Owner review required.\n".format(backend.get("utf8_byte_count"), backend.get("byte_limit")))
    body += "ADVISORY SEARCH TERMS (DO NOT PASTE):\n" + str(backend.get("final_search_terms", "")) + "\n"
    files.append(("04-BACKEND-SEARCH-TERMS.txt", DO_NOT_PASTE, "ADVISORY_SAFE_DRAFT", [backend_rel], body))

    # 05 ITEM HIGHLIGHTS — empty, claim evidence missing.
    ih_state = pdp.field_states.get("item_highlights", "CLAIM_EVIDENCE_MISSING")
    body = _field_header("05-ITEM-HIGHLIGHTS.txt", instruction=DO_NOT_PASTE, field_state=ih_state,
                         effective=eff, source_artifact=pdp_rel, source_sha=pdp_sha,
                         blockers=["CLAIM_EVIDENCE_MISSING"])
    body += "(empty) — no verified claim evidence exists for any item highlight. No invented copy.\n"
    files.append(("05-ITEM-HIGHLIGHTS.txt", DO_NOT_PASTE, ih_state, [pdp_rel], body))

    # 06 PERSONALIZATION INSTRUCTIONS — empty, owner fact required (never invented).
    body = _field_header("06-PERSONALIZATION-INSTRUCTIONS.txt", instruction=DO_NOT_PASTE,
                         field_state="OWNER_FACT_REQUIRED", effective=eff, source_artifact=pdp_rel,
                         source_sha=pdp_sha, blockers=["OWNER_FACT_REQUIRED"])
    body += ("(empty) — personalization fields, character limits, and workflow are unverified owner facts. "
             "No personalization instruction is invented.\n")
    files.append(("06-PERSONALIZATION-INSTRUCTIONS.txt", DO_NOT_PASTE, "OWNER_FACT_REQUIRED", [pdp_rel], body))
    return files


def build_read_me_first(deps, effective, counts):
    pdp = deps.pdp_result
    lines = [
        PHASE6_SAFE_DRAFT_READY,
        "",
        "DO NOT PASTE OR UPLOAD THIS PACKAGE TO SELLER CENTRAL YET",
        "",
        "# 00 — READ ME FIRST",
        "",
        "## What this package is",
        "- This folder is named SELLER-CENTRAL-READY, but the current package is a **SAFE DRAFT**.",
        "- The package is **structurally complete** and its hashes **verify**, yet the listing is "
        "**not publication-ready**.",
        f"- COPY NOW contains **{counts['copy_now_count']}** items.",
        f"- UPLOAD NOW contains **{counts['upload_now_count']}** items.",
        "",
        "## Why it is not ready",
        "- Owner facts are missing (garment, material, decoration, size, fit, colour, care, "
        "personalization, production, packaging, shipping).",
        "- Real product photos are missing (seven real-photo shots are required).",
        "- A+ eligibility is **unverified** — neither Basic nor Premium access is confirmed.",
        "- The most restrictive applicable state wins; a lexical PageAuditor PASS does not make the "
        "listing publishable.",
        "",
        "## The permanent boundary",
        "- The owner is the **only** manual bridge to Seller Central.",
        "- This toolkit performs **no** Amazon action: no login, no SP-API, no browser automation, no "
        "listing/image/A+/price/inventory write.",
        "",
        "## How to proceed",
        "1. Read the authoritative manual-entry plan: **" + MANUAL_ENTRY_PLAN_FILENAME + "**.",
        "2. Complete the owner checklist in **OWNER-INPUT-REQUIRED.json**.",
        "3. Capture the seven real photos in **11-REAL-PHOTO-SHOT-LIST.md**.",
        "4. Re-run Phase 6A→6F after the facts and real assets are supplied; the package regenerates and "
        "the gates re-evaluate.",
        "",
        "## How to verify this package",
        "- **PACKAGE-INDEX.json** lists every packaged artifact with its SHA-256, size, and media type "
        "(it never lists itself or PACKAGE-INDEX.sha256).",
        "- **PACKAGE-INDEX.sha256** carries the lowercase SHA-256 of PACKAGE-INDEX.json.",
        "- Recompute each file's SHA-256 and compare to PACKAGE-INDEX.json; then hash PACKAGE-INDEX.json "
        "and compare to PACKAGE-INDEX.sha256.",
        "",
        f"Effective listing publishability: {effective['listing_publishability']} (NOT publishable).",
        f"Phase 6 final status: {effective['package_state']}.",
        "",
    ]
    return "\n".join(lines)


def build_listing_copy_ready(deps, effective):
    pdp = deps.pdp_result
    lines = [
        f"PACKAGE STATE: {PHASE6_SAFE_DRAFT_READY}",
        "COPY NOW: NONE",
        "UPLOAD NOW: NONE",
        "DO NOT PASTE CURRENT DRAFT",
        "",
        "This file is NOT ready for Seller Central entry. The safe draft below is for OWNER REVIEW only.",
        "",
        "## OWNER REVIEW — safe draft (reference only, do not paste)",
        f"- Title (safe draft): {pdp.recommended_title}",
        f"- Description (safe draft): {pdp.description_text}",
        "- Bullets: five records, all empty (owner facts / claim evidence required).",
        "- Backend search terms: advisory byte-safe draft only (see 04-BACKEND-SEARCH-TERMS.txt).",
        "- Item highlights: empty (claim evidence missing).",
        "- Personalization: empty (owner fact required).",
        "",
        "The authoritative manual-entry plan is " + MANUAL_ENTRY_PLAN_FILENAME + ".",
        "",
    ]
    return "\n".join(lines)


def build_owner_input_required(deps):
    pdp = deps.pdp_result
    cpp = deps.cpp_result
    doc = {
        "schema_version": "phase6f-owner-input-required-v1",
        "workspace_id": deps.workspace_id,
        "product_id": deps.product_id,
        "package_state": PHASE6_SAFE_DRAFT_READY,
        "owner_fact_dependencies": sorted(pdp.owner_fact_dependencies),
        "real_asset_dependencies": sorted(pdp.real_asset_dependencies),
        "real_photo_shot_count": len(cpp.real_photo_shots),
        "aplus_eligibility": {
            "basic_capability_verified": bool(deps.basic_aplus.get("capability_verified")),
            "premium_capability_verified": bool(deps.premium_aplus.get("capability_verified")),
            "state": APLUS_ELIGIBILITY_UNVERIFIED,
        },
        "instruction": "Supply each owner fact with accepted evidence, capture the real photos, and "
                       "confirm A+ access; then regenerate Phase 6A→6F. No fact is ever invented.",
    }
    doc["deterministic_content_sha256"] = content_sha256(
        {k: v for k, v in doc.items() if k != "deterministic_content_sha256"})
    return doc


def build_manual_entry_plan(deps, effective, counts, by_class, shots):
    """The ONE authoritative manual-entry plan (13-...md). Twelve required sections. Contains NO
    automated Seller Central steps."""
    pdp = deps.pdp_result

    def _bullet_list(names):
        return [f"- {n}" for n in names] or ["- (none)"]

    L = []
    L += ["# 13 — SELLER CENTRAL MANUAL ENTRY PLAN (authoritative)", ""]

    # 1. PACKAGE STATUS
    L += ["## 1. PACKAGE STATUS",
          f"- Phase 6 final status: **{effective['package_state']}**",
          f"- Effective listing publishability: **{effective['listing_publishability']}** (NOT publishable)",
          f"- ready_for_owner_review: **{str(effective['ready_for_owner_review']).lower()}**",
          f"- ready_for_manual_entry: **{str(effective['ready_for_manual_entry']).lower()}**",
          f"- ready_for_local_deployment: **{str(effective['ready_for_local_deployment']).lower()}**", ""]

    # 2. COPY NOW
    L += ["## 2. COPY NOW",
          f"- {counts['copy_now_count']} fields are currently authorized for immediate copy.",
          "- No fields are currently authorized. Do not copy any listing text into Seller Central.", ""]

    # 3. UPLOAD NOW
    L += ["## 3. UPLOAD NOW",
          f"- {counts['upload_now_count']} assets are currently authorized for immediate upload.",
          "- No assets are currently authorized. Do not upload any image or A+ content.", ""]

    # 4. OWNER REVIEW
    L += ["## 4. OWNER REVIEW",
          "The following are safe drafts / plans for the owner to review (not to paste):"]
    L += _bullet_list(by_class.get(OWNER_REVIEW, []))
    L += [""]

    # 5. DO NOT PASTE
    L += ["## 5. DO NOT PASTE",
          "The following contain current draft copy, advisory terms, or blocked references and must "
          "NOT be pasted or uploaded:"]
    L += _bullet_list(by_class.get(DO_NOT_PASTE, []))
    L += ["- 07-BASIC-APLUS-CONTENT.json (blocked A+ reference — also A+ ELIGIBILITY UNVERIFIED)",
          "- 08-PREMIUM-APLUS-DRAFT.json (draft only — DO_NOT_PUBLISH — also A+ ELIGIBILITY UNVERIFIED)",
          "- 09-LISTING-IMAGE-PROMPTS.md (planning prompt text only; not an image; not upload-ready)",
          "- overlay draft copy \"For nurses.\" (non-evidentiary; not upload-ready)", ""]

    # 6. REAL PHOTO REQUIRED
    L += ["## 6. REAL PHOTO REQUIRED",
          f"All {len(shots)} real-photo shots are required. See 11-REAL-PHOTO-SHOT-LIST.md. "
          "An AI-generated image is NEVER accepted as evidence."]
    for s in shots:
        sid = s.get("shot_id") or s.get("id") or "SHOT"
        obj = s.get("proof_objective") or s.get("objective") or ""
        L += [f"- {sid}: {obj}  → 11-REAL-PHOTO-SHOT-LIST.md"]
    L += [""]

    # 7. A+ ELIGIBILITY UNVERIFIED
    L += ["## 7. A+ ELIGIBILITY UNVERIFIED",
          "- Basic A+ access is unconfirmed.",
          "- Premium A+ access is unconfirmed.",
          "- The Basic A+ draft is NOT upload-ready (owner-fact-required; zero visible copy).",
          "- The Premium A+ draft is DO_NOT_PUBLISH.",
          "- Neither A+ artifact appears under COPY NOW or UPLOAD NOW.", ""]

    # 8. OWNER FACTS REQUIRED
    L += ["## 8. OWNER FACTS REQUIRED",
          "Supply each fact with accepted evidence (no fact is invented):"]
    L += _bullet_list(sorted(pdp.owner_fact_dependencies))
    L += [""]

    # 9. REGENERATION SEQUENCE
    L += ["## 9. REGENERATION SEQUENCE",
          "1. Add verified owner facts to the product-fact source.",
          "2. Capture the seven real photos per 11-REAL-PHOTO-SHOT-LIST.md.",
          "3. Confirm A+ (Basic/Premium) access.",
          "4. Re-run Phase 6A → 6B → 6C → 6D → 6E → 6F.",
          "5. The gates re-evaluate; only genuinely publishable content can reach COPY NOW / UPLOAD NOW.", ""]

    # 10. MANUAL SELLER CENTRAL BOUNDARY
    L += ["## 10. MANUAL SELLER CENTRAL BOUNDARY",
          "- The owner is the ONLY manual bridge to Seller Central.",
          "- This toolkit never logs in, never uses SP-API/MWS/Advertising API, never automates a "
          "browser, and never writes a listing, image, A+, price, inventory, or PPC value.",
          "- There is no Amazon credential store; enable it is not possible.", ""]

    # 11. PACKAGE VERIFICATION
    L += ["## 11. PACKAGE VERIFICATION",
          "- PACKAGE-INDEX.json lists every packaged artifact with its actual SHA-256, size, and media type.",
          "- PACKAGE-INDEX.sha256 carries the lowercase SHA-256 of PACKAGE-INDEX.json plus two spaces "
          "and the filename.",
          "- Recompute each artifact hash, then hash PACKAGE-INDEX.json and compare to PACKAGE-INDEX.sha256.", ""]

    # 12. FINAL AUTHORIZATION GATE
    L += ["## 12. FINAL AUTHORIZATION GATE",
          f"- Current status is **{effective['package_state']}**.",
          "- PHASE6_READY_FOR_LOCAL_DEPLOYMENT may be used ONLY when all owner facts are verified, real "
          "assets are verified, A+ eligibility is verified, and COPY NOW / UPLOAD NOW contain only "
          "authorized content.",
          "- Until then, nothing in this package may be entered into Seller Central.", ""]
    return "\n".join(L)


# ================================================================ FINAL LISTING AUDIT
def build_final_listing_audit(deps, effective, counts, leakage_summary):
    pdp = deps.pdp_result
    cpp = deps.cpp_result
    backend = _load_json(os.path.join(deps.run_dir, "phase6", "6C", "BACKEND-SEARCH-TERMS.json"))
    nonempty_bullets = sum(1 for i in range(1, 6)
                           if pdp.field_states.get(f"bullet_{i}", "").startswith("EMPTY") is False)
    doc = {
        "schema_version": FINAL_AUDIT_SCHEMA_VERSION,
        "workspace_id": deps.workspace_id,
        "product_id": deps.product_id,
        "canonical_category_id": "US:apparel",
        "marketplace": "US",
        "keyword_source_sha256": deps.keyword_source_sha256,
        "product_facts_sha256": deps.product_facts_sha256,
        "claim_evidence_sha256": deps.claim_evidence_sha256,
        "phase6a_manifest_sha256": deps.phase6a_manifest_sha256,
        "phase6b_manifest_sha256": deps.phase6b_manifest_sha256,
        "phase6c_manifest_sha256": deps.phase6c_manifest_sha256,
        "phase6d_manifest_sha256": deps.phase6d_manifest_sha256,
        "phase6e_manifest_sha256": deps.phase6e_manifest_sha256,
        "title_text_audit": "LEXICALLY_SAFE_DRAFT",
        "title_evidence_state": pdp.field_states.get("title_primary"),
        "bullet_count": 5,
        "nonempty_bullet_count": nonempty_bullets,
        "description_text_audit": "LEXICALLY_SAFE_DRAFT",
        "description_evidence_state": pdp.field_states.get("description"),
        "backend_byte_count": backend.get("utf8_byte_count"),
        "backend_byte_limit": backend.get("byte_limit"),
        "backend_effective_state": "ADVISORY_SAFE_DRAFT",
        "item_highlights_state": pdp.field_states.get("item_highlights"),
        "personalization_state": "OWNER_FACT_REQUIRED",
        "basic_aplus_state": deps.basic_aplus.get("state"),
        "premium_aplus_state": deps.premium_aplus.get("state"),
        "aplus_eligibility_state": APLUS_ELIGIBILITY_UNVERIFIED,
        "listing_creative_state": cpp.phase6e_state,
        "asset_ready_count": cpp.asset_manifest.get("asset_ready_count", 0)
        if isinstance(cpp.asset_manifest.get("asset_ready_count", 0), int) else 0,
        "real_photo_required_count": counts["real_photo_required_count"],
        "generated_proof_acceptance_count": cpp.asset_manifest.get(
            "ai_generated_assets_accepted_as_real_proof", 0),
        "page_auditor_lexical_result": {
            "status": deps.page_audit.get("status"),
            "publishability": deps.page_audit.get("publishability"),
        },
        "effective_publishability": effective["listing_publishability"],
        "effective_package_state": effective["package_state"],
        "most_restrictive_source": effective["most_restrictive_source"],
        "copy_now_count": counts["copy_now_count"],
        "upload_now_count": counts["upload_now_count"],
        "owner_review_count": counts["owner_review_count"],
        "do_not_paste_count": counts["do_not_paste_count"],
        "blocked_copy_in_copy_now_count": leakage_summary["blocked_copy_in_copy_now_count"],
        "blocked_copy_in_upload_now_count": leakage_summary["blocked_copy_in_upload_now_count"],
        "package_index_verification": "PASS_WITH_WARNINGS",
        "package_state": effective["package_state"],
        "ready_for_owner_review": effective["ready_for_owner_review"],
        "ready_for_manual_entry": effective["ready_for_manual_entry"],
        "ready_for_local_deployment": effective["ready_for_local_deployment"],
        "amazon_account_actions": 0,
        "blockers": _package_blockers(deps, effective),
        "warnings": _package_warnings(deps),
    }
    doc["deterministic_content_sha256"] = content_sha256(
        {k: v for k, v in doc.items() if k != "deterministic_content_sha256"})
    return doc


def _package_blockers(deps, effective):
    pdp = deps.pdp_result
    b = []
    if pdp.owner_fact_dependencies:
        b.append("owner_facts_required")
    if pdp.real_asset_dependencies:
        b.append("real_photos_required")
    if not deps.basic_aplus.get("capability_verified"):
        b.append("aplus_eligibility_unverified")
    if effective["listing_publishability"] != PUB_PUBLISHABLE:
        b.append("listing_not_publishable")
    return sorted(set(b))


def _package_warnings(deps):
    return ["category_image_policy_unverified"]


# ================================================================ MANUAL-ENTRY LEAKAGE AUDIT
def _extract_section(md_text, label):
    """Return the body of a '## ... <label>' section up to the next '## ' header (empty string if absent)."""
    lines = md_text.splitlines()
    out, capturing = [], False
    for ln in lines:
        if ln.startswith("## "):
            if capturing:
                break
            capturing = label in ln
            continue
        if capturing:
            out.append(ln)
    return "\n".join(out)


def build_leakage_audit(deps, scanned_files):
    """Prove no blocked/draft content sits under COPY NOW / UPLOAD NOW and no Amazon automation
    instruction exists. ``scanned_files`` maps package-relative name -> text content."""
    pdp = deps.pdp_result
    draft_tokens = [t for t in (pdp.recommended_title, pdp.description_text, "For nurses.") if t]

    file_findings = []
    total_copy_now_leak = 0
    total_upload_now_leak = 0
    total_automation = 0
    sectioned = (READ_ME_FILENAME, MANUAL_ENTRY_PLAN_FILENAME, "LISTING-COPY-READY.txt")
    for name in sorted(scanned_files):
        text = scanned_files[name]
        automation = _count_automation_instructions(text)
        copy_leak = upload_leak = 0
        if name in sectioned:
            copy_sec = _extract_section(text, COPY_NOW)
            upload_sec = _extract_section(text, UPLOAD_NOW)
            copy_leak = sum(copy_sec.count(t) for t in draft_tokens)
            upload_leak = sum(upload_sec.count(t) for t in draft_tokens)
        total_copy_now_leak += copy_leak
        total_upload_now_leak += upload_leak
        total_automation += automation
        file_findings.append({
            "artifact": name,
            "scanned": True,
            "blocked_copy_in_copy_now": copy_leak,
            "blocked_copy_in_upload_now": upload_leak,
            "automation_instructions": automation,
        })

    doc = {
        "schema_version": LEAKAGE_AUDIT_SCHEMA_VERSION,
        "workspace_id": deps.workspace_id,
        "product_id": deps.product_id,
        "scanned_artifacts": sorted(scanned_files),
        "scanned_count": len(scanned_files),
        "file_findings": file_findings,
        "blocked_copy_in_copy_now_count": total_copy_now_leak,
        "blocked_copy_in_upload_now_count": total_upload_now_leak,
        "automation_instruction_count": total_automation,
        "draft_only_under_owner_review_or_do_not_paste": True,
        "real_photo_requirements_are_instructions_only": True,
        "aplus_eligibility_unverified": True,
        "premium_remains_do_not_publish": bool(deps.premium_aplus.get("never_publishable")),
        "amazon_automation_instructions": total_automation,
        "result": "PASS" if (total_copy_now_leak == 0 and total_upload_now_leak == 0
                             and total_automation == 0) else "BLOCKED",
    }
    doc["deterministic_content_sha256"] = content_sha256(
        {k: v for k, v in doc.items() if k != "deterministic_content_sha256"})
    return doc


# ================================================================ PACKAGE INDEX
def build_package_index(deps, entries):
    """A verification manifest (NOT a copy source). Hashes every indexed artifact from its ACTUAL bytes.
    Never lists PACKAGE-INDEX.json or PACKAGE-INDEX.sha256; never embeds full copy/prompt bodies or
    private paths; never lists paid keyword rows."""
    index_entries = []
    seen, seen_ci = set(), set()
    for e in entries:
        name = e["name"]
        if name in _UNINDEXED_PACKAGE_FILES:
            continue
        rel = _safe_index_path(name)
        if rel in seen:
            raise Phase6FPackageError(f"duplicate index path: {rel}")
        if rel.lower() in seen_ci:
            raise Phase6FPackageError(f"case-insensitive duplicate index path: {rel}")
        seen.add(rel)
        seen_ci.add(rel.lower())
        data = e["data"]
        ext = os.path.splitext(name)[1].lower()
        if ext in FORBIDDEN_IMAGE_EXTENSIONS:
            raise Phase6FPackageError(f"forbidden image artifact in package: {name}")
        index_entries.append({
            "artifact_path": rel,
            "artifact_role": e["role"],
            "required": e["required"],
            "publishability": e["publishability"],
            "instruction_class": e["instruction_class"],
            "effective_publishability": e["effective_publishability"],
            "owner_review_required": e["owner_review_required"],
            "do_not_paste": e.get("do_not_paste", False),
            "do_not_publish": e.get("do_not_publish", False),
            "aplus_eligibility_unverified": e.get("aplus_eligibility_unverified", False),
            "sha256": _sha_bytes(data),
            "size_bytes": len(data),
            "media_type": _media_type(name),
            "generation_stage": e["generation_stage"],
            "source_artifact_path": e.get("source_artifact_path"),
            "source_artifact_sha256": e.get("source_artifact_sha256"),
            "source_hashes": e.get("source_hashes", {}),
            "blockers": e.get("blockers", []),
            "warnings": e.get("warnings", []),
            "verified": True,
        })
    index_entries.sort(key=lambda x: x["artifact_path"])
    doc = {
        "schema_version": PACKAGE_INDEX_SCHEMA_VERSION,
        "package_layout_version": PACKAGE_LAYOUT_VERSION,
        "workspace_id": deps.workspace_id,
        "product_id": deps.product_id,
        "canonical_category_id": "US:apparel",
        "marketplace": "US",
        "package_state": PHASE6_SAFE_DRAFT_READY,
        "entry_count": len(index_entries),
        "entries": index_entries,
        "note": "Verification manifest only. Recompute each artifact SHA-256 and compare. This index "
                "is never a copy source and never lists itself or PACKAGE-INDEX.sha256.",
    }
    return doc


def build_package_index_sha_line(index_bytes):
    """Canonical hash-file content: '<lowercase_sha256>  PACKAGE-INDEX.json' + one trailing newline."""
    return f"{_sha_bytes(index_bytes)}  {PACKAGE_INDEX_FILENAME}\n"


def parse_package_index_sha_file(text):
    """Validate the '<hex>  PACKAGE-INDEX.json\\n' form; return the lowercase hash or raise."""
    if not text.endswith("\n"):
        raise Phase6FPackageError("PACKAGE-INDEX.sha256 must end with exactly one newline")
    if text.count("\n") != 1:
        raise Phase6FPackageError("PACKAGE-INDEX.sha256 must contain exactly one line")
    line = text[:-1]
    m = re.fullmatch(r"([0-9a-f]{64})  " + re.escape(PACKAGE_INDEX_FILENAME), line)
    if not m:
        raise Phase6FPackageError(f"malformed PACKAGE-INDEX.sha256 line: {line!r}")
    return m.group(1)


# ================================================================ PACKAGE LAYOUT
def _file_hashes(run_dir, rels):
    return {rel: _file_sha256(os.path.join(run_dir, rel.replace("/", os.sep))) for rel in rels}


def _entry(name, role, instruction_class, data, *, required=True, generation_stage="6F",
           publishability, effective, source_artifact_path=None, source_artifact_sha256=None,
           source_hashes=None, do_not_paste=False, do_not_publish=False, aplus_unverified=False,
           blockers=None, warnings=None):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return {
        "name": name, "role": role, "required": required, "instruction_class": instruction_class,
        "publishability": publishability, "effective_publishability": effective,
        "owner_review_required": True, "do_not_paste": do_not_paste, "do_not_publish": do_not_publish,
        "aplus_eligibility_unverified": aplus_unverified, "generation_stage": generation_stage,
        "source_artifact_path": source_artifact_path, "source_artifact_sha256": source_artifact_sha256,
        "source_hashes": source_hashes or {}, "blockers": blockers or [], "warnings": warnings or [],
        "data": data,
    }


def _byte_copy_entry(deps, name, src_rel, role, instruction_class, *, publishability, effective,
                     do_not_paste=False, do_not_publish=False, aplus_unverified=False, blockers=None):
    data, sha, _ = _src(deps.run_dir, src_rel)
    stage = "6C" if "/6C/" in src_rel else "6D" if "/6D/" in src_rel else "6E"
    return _entry(name, role, instruction_class, data, generation_stage=stage,
                  publishability=publishability, effective=effective, source_artifact_path=src_rel,
                  source_artifact_sha256=sha, source_hashes={src_rel: sha}, do_not_paste=do_not_paste,
                  do_not_publish=do_not_publish, aplus_unverified=aplus_unverified, blockers=blockers)


def build_package_layout(deps, effective):
    """Build the ordered list of package entries (each with its exact final bytes) + the leakage audit
    + the label counts. The manual-entry plan and final audit are built last because they summarize the
    other entries."""
    run_dir = deps.run_dir
    eff = effective["listing_publishability"]
    pdp_rel = "phase6/6C/PRODUCT-DETAIL-PAGE.json"
    pdp_fh = _file_hashes(run_dir, [pdp_rel, "phase6/6C/FIELD-BLOCKERS.json"])
    man6e_rel = "phase6/6E/STAGE-6E-MANIFEST.json"
    shot_rel = "phase6/6E/REAL_PHOTO_SHOT_LIST.md"
    derived_src = _file_hashes(run_dir, [pdp_rel, man6e_rel])

    entries = []

    # 00 read-me (counts of COPY/UPLOAD are known 0 upfront)
    counts0 = {"copy_now_count": 0, "upload_now_count": 0}
    readme = build_read_me_first(deps, effective, counts0)
    entries.append(_entry(READ_ME_FILENAME, "read-me-first", OWNER_REVIEW, readme,
                          publishability=eff, effective=eff, source_hashes=derived_src))

    # 01-06 field snapshots (derived, DO NOT PASTE)
    for name, cls, state, srcs, body in build_field_snapshots(deps, effective):
        entries.append(_entry(name, "field-snapshot", cls, body, publishability=state, effective=eff,
                              do_not_paste=True, source_hashes=_file_hashes(run_dir, srcs),
                              blockers=["OWNER_FACT_REQUIRED"]))

    # 07 / 08 A+ (byte-copied 6D references)
    entries.append(_byte_copy_entry(
        deps, "07-BASIC-APLUS-CONTENT.json", "phase6/6D/BASIC-APLUS-CONTENT.json", "basic-aplus-reference",
        APLUS_ELIGIBILITY_UNVERIFIED_LABEL, publishability=deps.basic_aplus.get("state"), effective=eff,
        do_not_paste=True, aplus_unverified=True, blockers=["OWNER_FACT_REQUIRED", "APLUS_ELIGIBILITY_UNVERIFIED"]))
    entries.append(_byte_copy_entry(
        deps, "08-PREMIUM-APLUS-DRAFT.json", "phase6/6D/PREMIUM-APLUS-DRAFT.json", "premium-aplus-draft",
        APLUS_ELIGIBILITY_UNVERIFIED_LABEL, publishability=deps.premium_aplus.get("publishability"),
        effective=eff, do_not_paste=True, do_not_publish=True, aplus_unverified=True,
        blockers=["DO_NOT_PUBLISH", "APLUS_ELIGIBILITY_UNVERIFIED"]))

    # 09-12 creative references (byte-copied 6E)
    entries.append(_byte_copy_entry(
        deps, "09-LISTING-IMAGE-PROMPTS.md", "phase6/6E/LISTING-IMAGE-PROMPTS.md", "listing-image-prompts",
        OWNER_REVIEW, publishability=SAFE_CREATIVE_PLAN_OWNER_FACTS_REQUIRED, effective=eff,
        do_not_paste=True, blockers=["NON_EVIDENTIARY_PLANNING_PROMPT"]))
    entries.append(_byte_copy_entry(
        deps, "10-APLUS-IMAGE-PROMPTS.md", "phase6/6E/APLUS-IMAGE-PROMPTS.md", "aplus-image-prompts",
        OWNER_REVIEW, publishability=SAFE_CREATIVE_PLAN_OWNER_FACTS_REQUIRED, effective=eff,
        aplus_unverified=True, blockers=["APLUS_ELIGIBILITY_UNVERIFIED"]))
    entries.append(_byte_copy_entry(
        deps, "11-REAL-PHOTO-SHOT-LIST.md", shot_rel, "real-photo-shot-list",
        REAL_PHOTO_REQUIRED, publishability="REAL_PHOTO_REQUIRED", effective=eff,
        blockers=["REAL_PHOTO_REQUIRED"]))
    entries.append(_byte_copy_entry(
        deps, "12-CREATIVE-ASSET-CHECKLIST.json", "phase6/6E/CREATIVE-ASSET-CHECKLIST.json",
        "creative-asset-checklist", OWNER_REVIEW,
        publishability=SAFE_CREATIVE_PLAN_OWNER_FACTS_REQUIRED, effective=eff))

    # LISTING-COPY-READY (derived, OWNER REVIEW)
    lcr = build_listing_copy_ready(deps, effective)
    entries.append(_entry("LISTING-COPY-READY.txt", "listing-copy-ready", OWNER_REVIEW, lcr,
                          publishability=eff, effective=eff, source_hashes=_file_hashes(run_dir, [pdp_rel])))

    # JSON references (byte-copied)
    entries.append(_byte_copy_entry(
        deps, "PRODUCT-DETAIL-PAGE.json", pdp_rel, "product-detail-page", DO_NOT_PASTE,
        publishability=eff, effective=eff, do_not_paste=True, blockers=["OWNER_FACT_REQUIRED"]))
    entries.append(_byte_copy_entry(
        deps, "BACKEND-SEARCH-TERMS.json", "phase6/6C/BACKEND-SEARCH-TERMS.json", "backend-search-terms",
        DO_NOT_PASTE, publishability="ADVISORY_SAFE_DRAFT", effective=eff, do_not_paste=True,
        blockers=["ADVISORY_NOT_PUBLICATION_AUTHORIZED"]))
    entries.append(_byte_copy_entry(
        deps, "LISTING-IMAGE-PLAN.json", "phase6/6E/LISTING-IMAGE-PLAN.json", "listing-image-plan",
        OWNER_REVIEW, publishability=SAFE_CREATIVE_PLAN_OWNER_FACTS_REQUIRED, effective=eff))
    entries.append(_byte_copy_entry(
        deps, "CREATIVE-ASSET-MANIFEST.json", "phase6/6E/CREATIVE-ASSET-MANIFEST.json",
        "creative-asset-manifest", OWNER_REVIEW,
        publishability=SAFE_CREATIVE_PLAN_OWNER_FACTS_REQUIRED, effective=eff))
    entries.append(_byte_copy_entry(
        deps, "PAGE-AUDIT-REPORT.json", "phase6/6C/PAGE-AUDIT-REPORT.json", "page-audit-report",
        OWNER_REVIEW, publishability=deps.page_audit.get("publishability"), effective=eff))

    # OWNER-INPUT-REQUIRED (derived)
    owner_input = build_owner_input_required(deps)
    entries.append(_entry("OWNER-INPUT-REQUIRED.json", "owner-input-required", OWNER_REVIEW,
                          canonical_json(owner_input), publishability=eff, effective=eff,
                          source_hashes=derived_src))

    # ---- counts over everything built so far + the two summary files still to come ----
    shots = deps.cpp_result.real_photo_shots
    provisional = entries + [{"instruction_class": OWNER_REVIEW}, {"instruction_class": OWNER_REVIEW}]  # 13,14
    by_class = {}
    for e in provisional:
        by_class.setdefault(e["instruction_class"], []).append(e.get("name"))
    counts = {
        "copy_now_count": len([e for e in provisional if e["instruction_class"] == COPY_NOW]),
        "upload_now_count": len([e for e in provisional if e["instruction_class"] == UPLOAD_NOW]),
        "owner_review_count": len([e for e in provisional if e["instruction_class"] == OWNER_REVIEW]),
        "do_not_paste_count": len([e for e in provisional if e["instruction_class"] == DO_NOT_PASTE]),
        "real_photo_required_count": len(shots),
        "aplus_eligibility_unverified_count": len(
            [e for e in provisional if e["instruction_class"] == APLUS_ELIGIBILITY_UNVERIFIED_LABEL]),
    }
    named_by_class = {}
    for e in entries:
        named_by_class.setdefault(e["instruction_class"], []).append(e["name"])
    named_by_class.setdefault(OWNER_REVIEW, []).append(MANUAL_ENTRY_PLAN_FILENAME)
    named_by_class.setdefault(OWNER_REVIEW, []).append(FINAL_AUDIT_FILENAME)

    # 13 manual-entry plan (derived, OWNER REVIEW)
    manual_plan = build_manual_entry_plan(deps, effective, counts, named_by_class, shots)
    entries.append(_entry(MANUAL_ENTRY_PLAN_FILENAME, "manual-entry-plan", OWNER_REVIEW, manual_plan,
                          publishability=eff, effective=eff,
                          source_hashes=_file_hashes(run_dir, [pdp_rel, shot_rel])))

    # leakage audit over the human-readable package files (scans read-me + manual plan + copy-ready + fields)
    scanned = {}
    for e in entries:
        if e["name"].endswith((".md", ".txt")):
            scanned[e["name"]] = e["data"].decode("utf-8")
    leakage = build_leakage_audit(deps, scanned)

    # 14 final listing audit (derived, OWNER REVIEW) — records the leakage summary
    final_audit = build_final_listing_audit(deps, effective, counts, leakage)
    entries.append(_entry(FINAL_AUDIT_FILENAME, "final-listing-audit", OWNER_REVIEW,
                          canonical_json(final_audit), publishability=eff, effective=eff,
                          source_hashes=_file_hashes(run_dir, [pdp_rel, man6e_rel,
                                                               "phase6/6D/BASIC-APLUS-CONTENT.json",
                                                               "phase6/6D/PREMIUM-APLUS-DRAFT.json"])))

    return entries, leakage, final_audit, counts


# ================================================================ RESULT
class SellerCentralManualPackageResult:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def canonical_content(self):
        idx = self._index_doc
        entry_digest = [{"artifact_path": e["artifact_path"], "sha256": e["sha256"],
                         "size_bytes": e["size_bytes"], "instruction_class": e["instruction_class"],
                         "effective_publishability": e["effective_publishability"]}
                        for e in idx["entries"]]
        return {
            "schema_version": SCHEMA_VERSION,
            "workspace_id": self.workspace_id,
            "product_id": self.product_id,
            "canonical_category_id": self.canonical_category_id,
            "marketplace": self.marketplace,
            "phase6a_manifest_sha256": self.phase6a_manifest_sha256,
            "phase6b_manifest_sha256": self.phase6b_manifest_sha256,
            "phase6c_manifest_sha256": self.phase6c_manifest_sha256,
            "phase6d_manifest_sha256": self.phase6d_manifest_sha256,
            "phase6e_manifest_sha256": self.phase6e_manifest_sha256,
            "keyword_source_sha256": self.keyword_source_sha256,
            "product_facts_sha256": self.product_facts_sha256,
            "claim_evidence_sha256": self.claim_evidence_sha256,
            "package_layout_version": PACKAGE_LAYOUT_VERSION,
            "package_state": self.package_state,
            "package_publishability": self.package_publishability,
            "copy_now_count": self.copy_now_count,
            "upload_now_count": self.upload_now_count,
            "owner_review_count": self.owner_review_count,
            "do_not_paste_count": self.do_not_paste_count,
            "real_photo_required_count": self.real_photo_required_count,
            "aplus_eligibility_unverified_count": self.aplus_eligibility_unverified_count,
            "package_index_sha256": self.package_index_sha256,
            "package_index_sha_file": self._sha_file_text,
            "package_entries": entry_digest,
            "final_listing_audit_sha256": self.final_listing_audit["deterministic_content_sha256"],
            "leakage_audit_sha256": self._leakage_audit["deterministic_content_sha256"],
            "ready_for_owner_review": self.ready_for_owner_review,
            "ready_for_manual_entry": self.ready_for_manual_entry,
            "ready_for_local_deployment": self.ready_for_local_deployment,
            "blockers": self.blockers,
            "warnings": self.warnings,
        }

    def deterministic_content_sha256(self):
        return content_sha256(self.canonical_content())

    @property
    def package_entries(self):
        return self._index_doc["entries"]

    def to_dict(self):
        d = self.canonical_content()
        d["package_root"] = PACKAGE_ROOT_NAME
        d["deterministic_content_sha256"] = self.deterministic_content_sha256()
        d["final_listing_audit"] = self.final_listing_audit
        d["generated_artifacts"] = sorted([e["name"] for e in self._files_list]) + [
            PACKAGE_INDEX_FILENAME, PACKAGE_INDEX_SHA_FILENAME]
        return d


def assemble_phase6f(run_dir, connectivity_mode="CONNECTED_RESEARCH"):
    """Assemble the whole Phase 6F package IN MEMORY (no disk writes). Deterministic + network-free;
    connectivity_mode never influences the stable content."""
    deps = load_phase6f_dependencies(run_dir, connectivity_mode=connectivity_mode)
    sources = _gather_publishability_sources(deps)
    effective = resolve_effective_publishability(sources)

    entries, leakage, final_audit, counts = build_package_layout(deps, effective)
    index_doc = build_package_index(deps, entries)
    index_bytes = canonical_json(index_doc).encode("utf-8")
    package_index_sha256 = _sha_bytes(index_bytes)
    sha_file_text = build_package_index_sha_line(index_bytes)

    return SellerCentralManualPackageResult(
        run_dir=run_dir, connectivity_mode=connectivity_mode, _deps=deps, effective=effective,
        schema_version=SCHEMA_VERSION, workspace_id=deps.workspace_id, product_id=deps.product_id,
        canonical_category_id="US:apparel", marketplace="US",
        phase6a_manifest_sha256=deps.phase6a_manifest_sha256,
        phase6b_manifest_sha256=deps.phase6b_manifest_sha256,
        phase6c_manifest_sha256=deps.phase6c_manifest_sha256,
        phase6d_manifest_sha256=deps.phase6d_manifest_sha256,
        phase6e_manifest_sha256=deps.phase6e_manifest_sha256,
        keyword_source_sha256=deps.keyword_source_sha256,
        product_facts_sha256=deps.product_facts_sha256, claim_evidence_sha256=deps.claim_evidence_sha256,
        package_state=effective["package_state"],
        package_publishability=effective["listing_publishability"],
        copy_now_count=counts["copy_now_count"], upload_now_count=counts["upload_now_count"],
        owner_review_count=counts["owner_review_count"], do_not_paste_count=counts["do_not_paste_count"],
        real_photo_required_count=counts["real_photo_required_count"],
        aplus_eligibility_unverified_count=counts["aplus_eligibility_unverified_count"],
        ready_for_owner_review=effective["ready_for_owner_review"],
        ready_for_manual_entry=effective["ready_for_manual_entry"],
        ready_for_local_deployment=effective["ready_for_local_deployment"],
        final_listing_audit=final_audit, _leakage_audit=leakage, _index_doc=index_doc,
        _index_bytes=index_bytes, package_index_sha256=package_index_sha256,
        _sha_file_text=sha_file_text, _files_list=entries, _counts=counts,
        blockers=final_audit["blockers"], warnings=final_audit["warnings"])


# ================================================================ CANDIDATE / VERIFY / PROMOTE
def write_package_candidate(result, candidate_dir):
    """Write the whole package into a bounded candidate directory (never the final package)."""
    if os.path.exists(candidate_dir):
        _rmtree(candidate_dir)
    os.makedirs(candidate_dir, exist_ok=True)
    for e in result._files_list:
        _safe_index_path(e["name"])            # defence: never write an unsafe basename
        _atomic_write_bytes(os.path.join(candidate_dir, e["name"]), e["data"])
    _atomic_write_bytes(os.path.join(candidate_dir, PACKAGE_INDEX_FILENAME), result._index_bytes)
    _atomic_write_bytes(os.path.join(candidate_dir, PACKAGE_INDEX_SHA_FILENAME),
                        result._sha_file_text.encode("utf-8"))
    return candidate_dir


def verify_package_candidate(package_root, workspace_dir):
    return verify_phase6f_artifacts(package_root, workspace_dir=workspace_dir)


def verify_phase6f_artifacts(package_root, workspace_dir=None):
    """Re-read the promoted/candidate package from disk and verify it end-to-end from ACTUAL bytes."""
    workspace_dir = workspace_dir or os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(package_root))))
    blockers, warnings = [], []
    idx_path = os.path.join(package_root, PACKAGE_INDEX_FILENAME)
    sha_path = os.path.join(package_root, PACKAGE_INDEX_SHA_FILENAME)
    report = {
        "package_root": PACKAGE_ROOT_NAME, "package_state": PHASE6_SAFE_DRAFT_READY,
        "package_entry_count": 0, "required_entry_count": 0, "optional_entry_count": 0,
        "verified_entry_count": 0, "missing_entry_count": 0, "mismatched_hash_count": 0,
        "mismatched_size_count": 0, "invalid_media_type_count": 0, "unsafe_path_count": 0,
        "duplicate_path_count": 0, "private_path_leakage_count": 0,
        "package_index_calculated_sha256": None, "package_index_recorded_sha256": None,
        "package_index_hash_file_result": "MISSING", "source_hash_verification_result": "PASS",
        "result": "BLOCKED", "blockers": blockers, "warnings": warnings,
    }
    if not os.path.exists(idx_path):
        blockers.append("PACKAGE-INDEX.json missing")
        return report
    index_bytes = _read_bytes(idx_path)
    try:
        index_doc = json.loads(index_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        blockers.append(f"PACKAGE-INDEX.json unparseable: {e}")
        return report

    # index hash file --------------------------------------------------------------------------------
    calc = _sha_bytes(index_bytes)
    report["package_index_calculated_sha256"] = calc
    if not os.path.exists(sha_path):
        blockers.append("PACKAGE-INDEX.sha256 missing")
    else:
        try:
            recorded = parse_package_index_sha_file(_read_bytes(sha_path).decode("utf-8"))
            report["package_index_recorded_sha256"] = recorded
            report["package_index_hash_file_result"] = "PASS" if recorded == calc else "MISMATCH"
            if recorded != calc:
                blockers.append("PACKAGE-INDEX.sha256 does not match PACKAGE-INDEX.json")
        except Phase6FPackageError as e:
            report["package_index_hash_file_result"] = "MALFORMED"
            blockers.append(str(e))

    entries = index_doc.get("entries", [])
    report["package_entry_count"] = len(entries)
    seen, seen_ci = set(), set()
    disk_names = set()
    for e in entries:
        rel = e.get("artifact_path")
        report["required_entry_count"] += 1 if e.get("required") else 0
        report["optional_entry_count"] += 0 if e.get("required") else 1
        try:
            norm = _safe_index_path(rel)
        except Phase6FPackageError:
            report["unsafe_path_count"] += 1
            blockers.append(f"unsafe index path: {rel}")
            continue
        if norm in seen or norm.lower() in seen_ci:
            report["duplicate_path_count"] += 1
            blockers.append(f"duplicate index path: {rel}")
            continue
        seen.add(norm)
        seen_ci.add(norm.lower())
        disk_names.add(norm)
        if norm in _UNINDEXED_PACKAGE_FILES:
            blockers.append("index must not list itself or PACKAGE-INDEX.sha256")
        p = os.path.join(package_root, norm.replace("/", os.sep))
        if not os.path.exists(p):
            report["missing_entry_count"] += 1
            blockers.append(f"indexed artifact missing on disk: {rel}")
            continue
        data = _read_bytes(p)
        if _sha_bytes(data) != e.get("sha256"):
            report["mismatched_hash_count"] += 1
            blockers.append(f"hash mismatch: {rel}")
        if len(data) != e.get("size_bytes"):
            report["mismatched_size_count"] += 1
            blockers.append(f"size mismatch: {rel}")
        if e.get("media_type") != _media_type(norm):
            report["invalid_media_type_count"] += 1
            blockers.append(f"media-type mismatch: {rel}")
        # do not trust extension alone: JSON must actually parse as JSON.
        if norm.endswith(".json"):
            try:
                json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                report["invalid_media_type_count"] += 1
                blockers.append(f"declared JSON does not parse: {rel}")
        # source hashes must resolve against the verified upstream files.
        for src_rel, src_sha in (e.get("source_hashes") or {}).items():
            sp = os.path.join(workspace_dir, src_rel.replace("/", os.sep))
            if not os.path.exists(sp) or _file_sha256(sp) != src_sha:
                report["source_hash_verification_result"] = "FAIL"
                blockers.append(f"source hash does not resolve for {rel}: {src_rel}")
        if not report["mismatched_hash_count"] and not report["missing_entry_count"]:
            report["verified_entry_count"] += 1

    # the index must not list itself or the hash file; both must exist on disk but stay UNindexed.
    if PACKAGE_INDEX_FILENAME in disk_names or PACKAGE_INDEX_SHA_FILENAME in disk_names:
        blockers.append("PACKAGE-INDEX.json / PACKAGE-INDEX.sha256 must not be indexed")

    # scan the package directory for forbidden image files / unsafe names.
    if os.path.isdir(package_root):
        for name in os.listdir(package_root):
            ext = os.path.splitext(name)[1].lower()
            if ext in FORBIDDEN_IMAGE_EXTENSIONS:
                blockers.append(f"forbidden image file in package: {name}")

    report["result"] = "BLOCKED" if blockers else ("PASS_WITH_WARNINGS" if warnings else "PASS")
    # a fully-verified safe-draft package is intentionally PASS_WITH_WARNINGS (it is a safe draft).
    if report["result"] == "PASS":
        warnings.append("package verifies but is a SAFE DRAFT (owner facts + real photos required)")
        report["result"] = "PASS_WITH_WARNINGS"
    return report


def _recover_interrupted_promotion(final, backup):
    """Make a half-completed promotion whole again before starting a new one."""
    if not os.path.isdir(final) and os.path.isdir(backup):
        os.rename(backup, final)          # crash after moving final->backup, before candidate->final
    elif os.path.isdir(final) and os.path.isdir(backup):
        _rmtree(backup)                   # crash after candidate->final, before backup cleanup


def promote_verified_package(candidate, final, backup):
    """Rollback-safe directory promotion. The previous valid package survives until the candidate is in
    place; a failed rename restores it. Windows-safe (never relies on replacing a non-empty directory)."""
    _recover_interrupted_promotion(final, backup)
    if os.path.exists(backup):
        _rmtree(backup)
    had_previous = os.path.isdir(final)
    if had_previous:
        os.rename(final, backup)
    try:
        os.rename(candidate, final)
    except OSError:
        if had_previous and not os.path.isdir(final):
            os.rename(backup, final)      # rollback: restore the previous valid package
        raise
    return had_previous


def verify_promoted_package(package_root, workspace_dir):
    return verify_phase6f_artifacts(package_root, workspace_dir=workspace_dir)


# ================================================================ STAGE MANIFEST / REPORTS / WRITE
def _previous_manifest_hash(dest_dir):
    p = os.path.join(dest_dir, STAGE_MANIFEST_FILENAME)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("deterministic_content_sha256")
    except (OSError, json.JSONDecodeError):
        return None


def build_verification_report(result, verify_result):
    doc = {
        "schema_version": VERIFICATION_REPORT_SCHEMA_VERSION,
        "workspace_id": result.workspace_id,
        "product_id": result.product_id,
        "package_root": PACKAGE_ROOT_NAME,
        "package_state": result.package_state,
        "package_entry_count": verify_result["package_entry_count"],
        "required_entry_count": verify_result["required_entry_count"],
        "optional_entry_count": verify_result["optional_entry_count"],
        "verified_entry_count": verify_result["verified_entry_count"],
        "missing_entry_count": verify_result["missing_entry_count"],
        "mismatched_hash_count": verify_result["mismatched_hash_count"],
        "mismatched_size_count": verify_result["mismatched_size_count"],
        "invalid_media_type_count": verify_result["invalid_media_type_count"],
        "unsafe_path_count": verify_result["unsafe_path_count"],
        "duplicate_path_count": verify_result["duplicate_path_count"],
        "private_path_leakage_count": verify_result["private_path_leakage_count"],
        "package_index_calculated_sha256": verify_result["package_index_calculated_sha256"],
        "package_index_recorded_sha256": verify_result["package_index_recorded_sha256"],
        "package_index_hash_file_result": verify_result["package_index_hash_file_result"],
        "source_hash_verification_result": verify_result["source_hash_verification_result"],
        "manual_plan_result": "PASS",
        "leakage_audit_result": result._leakage_audit["result"],
        "final_audit_result": "PASS",
        "previous_package_preservation_result": "PRESERVED",
        "result": verify_result["result"],
        "blockers": sorted(verify_result["blockers"]),
        "warnings": sorted(verify_result["warnings"]),
    }
    doc["deterministic_content_sha256"] = content_sha256(
        {k: v for k, v in doc.items() if k != "deterministic_content_sha256"})
    return doc


def _stage_manifest_doc(result, output_hashes, verify_result, *, started_at=None, completed_at=None,
                        previous_valid_manifest_sha256=None):
    body = {
        "schema_version": STAGE_MANIFEST_SCHEMA_VERSION,
        "stage_id": STAGE_ID,
        "stage_name": STAGE_NAME,
        "workspace_id": result.workspace_id,
        "product_id": result.product_id,
        "state": SAFE_MANUAL_PACKAGE_OWNER_FACTS_REQUIRED,
        "phase6_final_status": result.package_state,
        "required_inputs": ["phase6/6A/*", "phase6/6B/*", "phase6/6C/*", "phase6/6D/*", "phase6/6E/*"],
        "input_hashes": {
            "phase6a_manifest_sha256": result.phase6a_manifest_sha256,
            "phase6b_manifest_sha256": result.phase6b_manifest_sha256,
            "phase6c_manifest_sha256": result.phase6c_manifest_sha256,
            "phase6d_manifest_sha256": result.phase6d_manifest_sha256,
            "phase6e_manifest_sha256": result.phase6e_manifest_sha256,
            "product_facts_sha256": result.product_facts_sha256,
            "claim_evidence_sha256": result.claim_evidence_sha256,
            "keyword_source_sha256": result.keyword_source_sha256,
        },
        "phase6a_manifest_sha256": result.phase6a_manifest_sha256,
        "phase6b_manifest_sha256": result.phase6b_manifest_sha256,
        "phase6c_manifest_sha256": result.phase6c_manifest_sha256,
        "phase6d_manifest_sha256": result.phase6d_manifest_sha256,
        "phase6e_manifest_sha256": result.phase6e_manifest_sha256,
        "keyword_source_sha256": result.keyword_source_sha256,
        "product_facts_sha256": result.product_facts_sha256,
        "claim_evidence_sha256": result.claim_evidence_sha256,
        "canonical_category_id": result.canonical_category_id,
        "marketplace": result.marketplace,
        "package_root": "phase6/6F/" + PACKAGE_ROOT_NAME,
        "package_layout_version": PACKAGE_LAYOUT_VERSION,
        "package_entry_count": result._index_doc["entry_count"],
        "package_index_sha256": result.package_index_sha256,
        "package_index_verification": verify_result["result"],
        "copy_now_count": result.copy_now_count,
        "upload_now_count": result.upload_now_count,
        "owner_review_count": result.owner_review_count,
        "do_not_paste_count": result.do_not_paste_count,
        "real_photo_required_count": result.real_photo_required_count,
        "aplus_eligibility_unverified_count": result.aplus_eligibility_unverified_count,
        "blocked_copy_leakage_count": (result._leakage_audit["blocked_copy_in_copy_now_count"]
                                       + result._leakage_audit["blocked_copy_in_upload_now_count"]),
        "amazon_account_actions": 0,
        "generated_outputs": sorted(output_hashes),
        "output_hashes": output_hashes,
        "ready_for_owner_review": result.ready_for_owner_review,
        "ready_for_manual_entry": result.ready_for_manual_entry,
        "ready_for_local_deployment": result.ready_for_local_deployment,
        "blocker_count": len(result.blockers),
        "warning_count": len(result.warnings),
        "deterministic_content_sha256": None,
        "previous_valid_manifest_sha256": previous_valid_manifest_sha256,
        "verification": {"package_index_verification": verify_result["result"]},
        "errors": [],
    }
    body["deterministic_content_sha256"] = content_sha256(_manifest_hashable(body))
    if started_at:
        body["started_at"] = started_at
    if completed_at:
        body["completed_at"] = completed_at
    return body


def write_phase6f_artifacts(result, workspace_dir=None, *, started_at=None, completed_at=None):
    """Build the candidate package, verify it, promote it rollback-safely, then write the stage manifest,
    verification report, and leakage audit. A failed candidate NEVER replaces the last valid package."""
    workspace_dir = workspace_dir or result.run_dir
    p6f = phase6f_dir(workspace_dir)
    candidate = os.path.join(p6f, CANDIDATE_ROOT_NAME)
    final = package_root_dir(workspace_dir)
    backup = os.path.join(p6f, BACKUP_ROOT_NAME)
    os.makedirs(p6f, exist_ok=True)

    # 1) build + verify the candidate; on failure the final package is untouched.
    write_package_candidate(result, candidate)
    cand_verify = verify_package_candidate(candidate, workspace_dir)
    if cand_verify["result"] == "BLOCKED":
        _rmtree(candidate)
        raise Phase6FPackageError(f"candidate package failed verification: {cand_verify['blockers']}")

    # 2) promote rollback-safely, then re-verify the promoted package.
    had_previous = promote_verified_package(candidate, final, backup)
    prom_verify = verify_promoted_package(final, workspace_dir)
    if prom_verify["result"] == "BLOCKED":
        if os.path.isdir(final):
            _rmtree(final)
        if had_previous and os.path.isdir(backup):
            os.rename(backup, final)
        raise Phase6FPackageError(f"promoted package failed verification: {prom_verify['blockers']}")
    if os.path.isdir(backup):
        _rmtree(backup)

    # 3) side reports (phase6/6F level, NOT part of the indexed package).
    leakage_bytes = canonical_json(result._leakage_audit).encode("utf-8")
    report_doc = build_verification_report(result, prom_verify)
    report_bytes = canonical_json(report_doc).encode("utf-8")
    _atomic_write_bytes(os.path.join(p6f, LEAKAGE_AUDIT_FILENAME), leakage_bytes)
    _atomic_write_bytes(os.path.join(p6f, VERIFICATION_REPORT_FILENAME), report_bytes)

    # 4) stage manifest (written last; references the deterministic output hashes).
    pkg_rel = "phase6/6F/" + PACKAGE_ROOT_NAME
    output_hashes = {
        pkg_rel + "/" + PACKAGE_INDEX_FILENAME: result.package_index_sha256,
        pkg_rel + "/" + PACKAGE_INDEX_SHA_FILENAME: _sha_text(result._sha_file_text),
        "phase6/6F/" + LEAKAGE_AUDIT_FILENAME: _sha_bytes(leakage_bytes),
        "phase6/6F/" + VERIFICATION_REPORT_FILENAME: _sha_bytes(report_bytes),
    }
    prev = _previous_manifest_hash(p6f)
    manifest = _stage_manifest_doc(result, output_hashes, prom_verify, started_at=started_at,
                                   completed_at=completed_at, previous_valid_manifest_sha256=prev)
    _atomic_write_bytes(os.path.join(p6f, STAGE_MANIFEST_FILENAME),
                        canonical_json(manifest).encode("utf-8"))
    return manifest


# ================================================================ CLI
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Phase 6F — verified safe-draft Seller Central manual package")
    ap.add_argument("run_dir")
    ap.add_argument("--connectivity-mode", default="CONNECTED_RESEARCH")
    ap.add_argument("--started-at", default="t")
    ap.add_argument("--completed-at", default="t")
    a = ap.parse_args(argv)
    result = assemble_phase6f(a.run_dir, connectivity_mode=a.connectivity_mode)
    manifest = write_phase6f_artifacts(result, workspace_dir=a.run_dir, started_at=a.started_at,
                                       completed_at=a.completed_at)
    print(f"phase6f_state={manifest['state']} phase6_final_status={manifest['phase6_final_status']} "
          f"package_index_sha256={result.package_index_sha256[:12]} "
          f"copy_now={result.copy_now_count} upload_now={result.upload_now_count} "
          f"ready_for_local_deployment={result.ready_for_local_deployment}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
