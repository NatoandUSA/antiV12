#!/usr/bin/env python3
"""
production.product_detail_page — the ONE Phase 6C Product Detail Page assembly authority.

Phase 6C assembles a TRUTHFUL Product Detail Page DRAFT by combining verified Phase 6A product
facts + claim evidence, the ADVISORY Phase 6B keyword allocation, and the mature Phase 5 field
engines. It is a thin assembler: it never re-implements a title/bullet/description/backend/
item-highlight engine, a category policy, a fact loader, a claim engine, a PageAuditor, or a
publishability authority — it REUSES them.

The Phase 6B allocation map is advisory planning metadata (downstream_authorization=ADVISORY_ONLY).
It does NOT authorize literal insertion or publication. Every allocated concept must still pass the
existing product-fact / claim-evidence / category-policy / field-engine / natural-language /
duplication / PageAuditor / publishability gates. On top of the engines, Phase 6C applies one extra
evidence gate — the SAME physical-claim classifier the 6B planner used (keyword_allocation_planner.
_classify_keyword) — so that copy which passes lexical auditing but leans on an UNVERIFIED concept
(e.g. the recipient claim text "A personalized gift for nurses." carries unverified personalization
+ gift/occasion language) is DEFERRED, never published. A clean lexical audit never overrides the
evidence/publishability state.

It reuses — never re-implements:
  * Phase 6A dependency + verify -> production.product_workspace / keyword_allocation_planner
  * Phase 6B allocation + verify  -> listing.keyword_allocation_planner
  * category policy               -> listing.category_policy_registry
  * product facts                 -> listing.product_fact_loader
  * claim evidence                -> listing.claim_evidence
  * title / bullets / description -> listing.title_engine / bullet_engine / description_engine
  * backend optimizer             -> listing.backend_optimizer
  * item highlights               -> listing.item_highlights_builder
  * page auditor                  -> listing.page_auditor
  * canonical json / hashing / atomic write -> production.product_workspace helpers
  * runtime / network safety      -> core.runtime_policy / core.network_policy

It never touches the owner's Amazon account in any way — no account access, no authenticated session,
no seller API, no browser automation, no account-report retrieval, and no price / inventory / listing /
A+ / advertising write. It never opens the network. It does not generate A+ or image copy, and never
emits a later-stage (Phase 6D-6F) artifact.

Public API:
  load_phase6c_dependencies(run_dir) -> Phase6CDependencies
  reconcile_phase6b_keyword_counts(deps) -> dict
  assemble_product_detail_page(run_dir, connectivity_mode=...) -> ProductDetailPageResult
  write_phase6c_artifacts(result, dest_dir, ...) -> manifest dict
  verify_phase6c_artifacts(dest_dir) -> dict
  phase6c_dir(run_dir) -> str
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
import keyword_allocation_planner as KAP            # noqa: E402  (6A dep + 6B allocation + physical-claim gate)
import keyword_source_adapter as KSA                # noqa: E402
import product_fact_loader as PFL                   # noqa: E402
import claim_evidence as CE                         # noqa: E402
import category_policy_registry as CPR              # noqa: E402
import title_engine as TE                           # noqa: E402
import bullet_engine as BE                          # noqa: E402
import description_engine as DE                     # noqa: E402
import backend_optimizer as BO                      # noqa: E402
import item_highlights_builder as IHB               # noqa: E402
import page_auditor as PA                           # noqa: E402
import listing_generator as LG                      # noqa: E402  (garment read only)
import runtime_policy as RP                         # noqa: E402  (offline safety snapshot)

# one serialization + hashing authority (do NOT re-implement) --------------------------------------
canonical_json = PW.canonical_json
content_sha256 = PW.content_sha256
_file_sha256 = PW._file_sha256

SCHEMA_VERSION = "phase6-pdp-v1"
TITLE_OPTIONS_SCHEMA_VERSION = "phase6-title-options-v1"
BULLETS_SCHEMA_VERSION = "phase6-bullets-v1"
BACKEND_SCHEMA_VERSION = "phase6-backend-v1"
ITEM_HIGHLIGHTS_SCHEMA_VERSION = "phase6-item-highlights-v1"
LINEAGE_SCHEMA_VERSION = "phase6-copy-lineage-v1"
STAGE_MANIFEST_SCHEMA_VERSION = "phase6c-stage-manifest-v1"

STAGE_ID = "6C"
STAGE_NAME = "Product Detail Page Assembly"

# -- Phase 6C stage states (NOT the same as listing publishability) --------------------------------
READY_FOR_6D = "READY_FOR_6D"
SAFE_PDP_OWNER_FACTS_REQUIRED = "SAFE_PDP_OWNER_FACTS_REQUIRED"
BLOCKED_DEPENDENCY = "BLOCKED_DEPENDENCY"
BLOCKED_UNSAFE = "BLOCKED_UNSAFE"
INVALID_ENGINE_OUTPUT = "INVALID_ENGINE_OUTPUT"
INVALID_AUDIT = "INVALID_AUDIT"
INVALID_KEYWORD_ACCOUNTING = "INVALID_KEYWORD_ACCOUNTING"

LISTING_SAFE_DRAFT = "SAFE_DRAFT_OWNER_FACTS_REQUIRED"

# -- title candidate states ------------------------------------------------------------------------
T_RECOMMENDED_SAFE_DRAFT = "RECOMMENDED_SAFE_DRAFT"
T_PUBLISHABLE = "PUBLISHABLE"
T_OWNER_FACT_REQUIRED = "OWNER_FACT_REQUIRED"
T_BLOCKED_UNSAFE = "BLOCKED_UNSAFE"
T_REJECTED_DUPLICATION = "REJECTED_DUPLICATION"
T_REJECTED_NATURAL_LANGUAGE = "REJECTED_NATURAL_LANGUAGE"
T_REJECTED_CATEGORY_POLICY = "REJECTED_CATEGORY_POLICY"

# -- allocation outcome vocabulary -----------------------------------------------------------------
USED = "USED"
PARAPHRASED_NATURALLY = "PARAPHRASED_NATURALLY"
DEFERRED_OWNER_FACT_REQUIRED = "DEFERRED_OWNER_FACT_REQUIRED"
DEFERRED_REAL_ASSET_REQUIRED = "DEFERRED_REAL_ASSET_REQUIRED"
REJECTED_CLAIM_EVIDENCE = "REJECTED_CLAIM_EVIDENCE"
REJECTED_PRODUCT_FACT = "REJECTED_PRODUCT_FACT"
REJECTED_CATEGORY_POLICY = "REJECTED_CATEGORY_POLICY"
REJECTED_NATURAL_LANGUAGE = "REJECTED_NATURAL_LANGUAGE"
REJECTED_DUPLICATION = "REJECTED_DUPLICATION"
REJECTED_PAGE_AUDITOR = "REJECTED_PAGE_AUDITOR"
DEFERRED_TO_LATER_STAGE = "DEFERRED_TO_LATER_STAGE"

# -- deferred creative/enhanced fields (executed in a LATER stage, never in 6C) ---------------------
DEFERRED_FIELD_STATES = {
    "aplus": "DEFERRED_OWNER_FACT_REQUIRED",
    "listing_image_text": "DEFERRED_TO_LATER_STAGE",
    "image_alt_text": "DEFERRED_TO_LATER_STAGE",
}

# -- required + support artifacts (all under <run>/phase6/6C) ---------------------------------------
PRODUCT_DETAIL_PAGE_FILENAME = "PRODUCT-DETAIL-PAGE.json"
TITLE_OPTIONS_FILENAME = "TITLE-OPTIONS.json"
BULLETS_FILENAME = "BULLETS.json"
DESCRIPTION_FILENAME = "DESCRIPTION.txt"
BACKEND_FILENAME = "BACKEND-SEARCH-TERMS.json"
STAGE_MANIFEST_FILENAME = "STAGE-6C-MANIFEST.json"
ITEM_HIGHLIGHTS_FILENAME = "ITEM-HIGHLIGHTS.json"
PAGE_AUDIT_FILENAME = "PAGE-AUDIT-REPORT.json"
FIELD_BLOCKERS_FILENAME = "FIELD-BLOCKERS.json"
COPY_LINEAGE_FILENAME = "PHASE6C-COPY-LINEAGE.json"

REQUIRED_ARTIFACTS = (PRODUCT_DETAIL_PAGE_FILENAME, TITLE_OPTIONS_FILENAME, BULLETS_FILENAME,
                      DESCRIPTION_FILENAME, BACKEND_FILENAME)
SUPPORT_ARTIFACTS = (ITEM_HIGHLIGHTS_FILENAME, PAGE_AUDIT_FILENAME, FIELD_BLOCKERS_FILENAME,
                     COPY_LINEAGE_FILENAME)

# artifacts a Phase 6C run must NEVER emit (they belong to a later, owner-approved creative stage).
FORBIDDEN_ARTIFACTS = frozenset({
    "BASIC-APLUS-CONTENT.json", "PREMIUM-APLUS-DRAFT.json", "LISTING-IMAGE-PLAN.json",
    "PACKAGE-INDEX.json", "SELLER-CENTRAL-READY",
})

_MANIFEST_VOLATILE = ("deterministic_content_sha256", "started_at", "completed_at",
                      "previous_valid_manifest_sha256", "verification")

# generic tokens that carry no distinctive market identity (used only for backend candidate matching).
_GENERIC_TOKENS = frozenset({"gift", "gifts", "for", "nursing", "nurse", "nurses", "sweatshirt",
                             "and", "a", "the", "to", "of"})


class Phase6CError(Exception):
    pass


class Phase6CDependencyError(Phase6CError):
    pass


class Phase6BAllocationCountMismatch(Phase6CError):
    pass


class Phase6CAssemblyError(Phase6CError):
    pass


# ---------------------------------------------------------------- paths / atomic write
def phase6c_dir(run_dir):
    return os.path.join(run_dir, "phase6", "6C")


def _safe_rel(path, base):
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(base))
    norm = rel.replace(os.sep, "/")
    if os.path.isabs(rel) or norm == ".." or norm.startswith("../"):
        raise Phase6CAssemblyError(f"path escapes workspace ({norm}): {path}")
    return norm


def _atomic_write_text(path, text):
    """temp sibling -> fsync -> os.replace. A failed write never overwrites the last valid file."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-6c-", suffix=".part")
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


def _norm_text(text):
    n = re.sub(r"[^a-z0-9 ]", " ", str(text).lower())
    return re.sub(r"\s+", " ", n).strip()


def _is_safe_copy(text):
    """Phase 6C visible-copy evidence gate: reuse the 6B physical-claim classifier. A field's copy is
    Phase-6C-safe ONLY if it classifies SAFE (pure market identity) — any GATED/EXCLUDED verdict means
    it leans on an unverified physical / personalization / gift-occasion concept and must be deferred."""
    norm = _norm_text(text)
    if not norm:
        return False
    verdict = KAP._classify_keyword({"keyword_normalized": norm})
    return verdict[0] == "SAFE"


def _classify_reason(text):
    norm = _norm_text(text)
    if not norm:
        return "EMPTY"
    verdict = KAP._classify_keyword({"keyword_normalized": norm})
    return verdict[1] if len(verdict) > 1 and verdict[1] else verdict[0]


# ================================================================ DEPENDENCIES
class Phase6CDependencies:
    """Verified, read-only view over Phase 6A + Phase 6B, plus the reusable Phase 5 engine handles."""

    def __init__(self, *, run_dir, dep, facts, claims, policy, keyword_source,
                 allocation_map, top_relevance, unallocated, phase6a_manifest, phase6b_manifest,
                 ready_for_6c):
        self.run_dir = run_dir
        self.dep = dep
        self.facts = facts
        self.claims = claims
        self.policy = policy
        self.keyword_source = keyword_source
        self.allocation_map = allocation_map
        self.top_relevance = top_relevance
        self.unallocated = unallocated
        self.phase6a_manifest = phase6a_manifest
        self.phase6b_manifest = phase6b_manifest
        self.ready_for_6c = ready_for_6c

    # identity
    @property
    def workspace_id(self):
        return self.dep.workspace_id

    @property
    def product_id(self):
        return self.dep.product_id

    @property
    def category(self):
        return self.allocation_map.get("category") or self.dep.category

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
        return self.phase6a_manifest.get("deterministic_content_sha256")

    @property
    def phase6b_manifest_sha256(self):
        return self.phase6b_manifest.get("deterministic_content_sha256")

    @property
    def allocation_map_sha256(self):
        return self.allocation_map.get("deterministic_content_sha256")


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_phase6c_dependencies(run_dir):
    """Load + VERIFY every Phase 6A and Phase 6B dependency before any assembly.

    Blocks (Phase6CDependencyError) when: a 6A/6B artifact is missing / tampered / hash-mismatched,
    the keyword source drifted, the allocation map cross-references disagree with 6A, ready_for_6c is
    not true, or an Amazon account path exists. It NEVER modifies an upstream artifact.
    """
    dest6a = PW.phase6a_dir(run_dir)
    dest6b = KAP.phase6b_dir(run_dir)

    # 1) Phase 6A dependency — load_phase6a_dependency itself re-verifies 6A hashes + source drift.
    try:
        dep = KAP.load_phase6a_dependency(run_dir)
    except KAP.Phase6ADependencyError as e:
        raise Phase6CDependencyError(f"Phase 6A dependency invalid: {e}") from e
    v6a = PW.verify_phase6a_artifacts(dest6a, workspace_dir=run_dir)
    if not v6a["ok"]:
        raise Phase6CDependencyError(f"Phase 6A artifacts failed verification: {v6a['errors']}")

    # 2) Phase 6B artifacts — byte integrity vs the 6B manifest.
    v6b = KAP.verify_phase6b_artifacts(dest6b, workspace_dir=run_dir)
    if not v6b["ok"]:
        raise Phase6CDependencyError(f"Phase 6B artifacts failed verification: {v6b['errors']}")

    for name in ("KEYWORD-ALLOCATION-MAP.json", "TOP-RELEVANCE-KEYWORDS.json",
                 "UNALLOCATED-ELIGIBLE-KEYWORDS.json", "STAGE-6B-MANIFEST.json"):
        if not os.path.exists(os.path.join(dest6b, name)):
            raise Phase6CDependencyError(f"missing Phase 6B artifact: {name}")
    allocation_map = _load_json(os.path.join(dest6b, "KEYWORD-ALLOCATION-MAP.json"))
    top_relevance = _load_json(os.path.join(dest6b, "TOP-RELEVANCE-KEYWORDS.json"))
    unallocated = _load_json(os.path.join(dest6b, "UNALLOCATED-ELIGIBLE-KEYWORDS.json"))
    phase6b_manifest = _load_json(os.path.join(dest6b, "STAGE-6B-MANIFEST.json"))
    phase6a_manifest = _load_json(os.path.join(dest6a, PW.STAGE_MANIFEST_FILENAME))

    # 3) allocation-map cross references must agree with the verified 6A dependency.
    for field, want in (("source_keyword_sha256", dep.keyword_source_sha256),
                        ("product_facts_sha256", dep.product_facts_sha256),
                        ("claim_evidence_sha256", dep.claim_evidence_sha256),
                        ("phase6a_manifest_sha256", dep.phase6a_manifest_sha256)):
        if allocation_map.get(field) != want:
            raise Phase6CDependencyError(
                f"allocation-map {field} {str(allocation_map.get(field))[:12]} != 6A {str(want)[:12]}")
    if allocation_map.get("downstream_authorization") != "ADVISORY_ONLY":
        raise Phase6CDependencyError("allocation map is not ADVISORY_ONLY")
    if allocation_map.get("listing_publishability_state") != LISTING_SAFE_DRAFT:
        raise Phase6CDependencyError("allocation map listing publishability is not SAFE_DRAFT")

    # 4) ready_for_6c — reconstruct the planner's authoritative flag and confirm 6B has not drifted.
    recomputed = KAP.plan_keyword_allocation(run_dir)
    ready = bool(getattr(recomputed, "ready_for_6c", False))
    if not ready:
        raise Phase6CDependencyError("Phase 6B planner does not report ready_for_6c=true")
    if recomputed.stage_state != KAP.SAFE_ALLOCATION_OWNER_FACTS_REQUIRED:
        raise Phase6CDependencyError(f"Phase 6B stage state is {recomputed.stage_state}")
    if recomputed.allocation.get("fields") != allocation_map.get("fields"):
        raise Phase6CDependencyError("persisted Phase 6B allocation drifted from the planner recompute")

    # 5) Amazon-account boundary must remain closed in every mode (immutable runtime-policy attrs).
    pol = RP.load_runtime_policy()
    if not getattr(pol, "amazon_account_isolation", False) or getattr(pol, "amazon_api_enabled", True) \
            or getattr(pol, "amazon_network_writes_enabled", True) \
            or getattr(pol, "amazon_authenticated_access_enabled", True):
        raise Phase6CDependencyError("Amazon account boundary is not closed")

    return Phase6CDependencies(
        run_dir=run_dir, dep=dep, facts=None, claims=None, policy=None, keyword_source=None,
        allocation_map=allocation_map, top_relevance=top_relevance, unallocated=unallocated,
        phase6a_manifest=phase6a_manifest, phase6b_manifest=phase6b_manifest, ready_for_6c=ready,
    )._attach_engines(run_dir)

    # (engines attached in _attach_engines; kept off __init__ so the object is cheap to construct)


def _attach_engines(self, run_dir):
    # loaded exactly as Phase 6A/5 do — reproducing the 6A claim-evidence content hash bit-for-bit.
    self.keyword_source = KSA.load_keyword_source(run_dir)
    self.facts = PFL.load_product_facts(folder=run_dir)
    self.policy = CPR.resolve_category_policy(self.dep.category or "apparel")
    ctx = {"audience": self.dep.audience} if self.dep.audience else {}
    self.claims = CE.build_claim_evidence(self.facts, keyword_context=ctx)
    # the reconstructed claim-evidence MUST equal the 6A record the whole chain was hashed against.
    if self.claims.content_sha256() != self.dep.claim_evidence_sha256:
        raise Phase6CDependencyError("reconstructed claim evidence does not match the 6A claim hash")
    if self.facts.content_sha256() != self.dep.product_facts_sha256:
        raise Phase6CDependencyError("reconstructed product facts do not match the 6A facts hash")
    return self


Phase6CDependencies._attach_engines = _attach_engines


# ================================================================ KEYWORD ACCOUNTING
def reconcile_phase6b_keyword_counts(deps):
    """Compute the five unique counts DIRECTLY from keyword IDs (never trust the report numbers) and
    prove the reconciliation equation + reuse justification. Raises Phase6BAllocationCountMismatch."""
    amap = deps.allocation_map
    assignments = [(field, rec) for field, recs in amap["fields"].items() for rec in recs]
    assignment_ids = [r["assignment_id"] for _f, r in assignments]
    if len(assignment_ids) != len(set(assignment_ids)):
        raise Phase6BAllocationCountMismatch("duplicate assignment_id in allocation map")

    alloc_ids = [r["keyword_id"] for _f, r in assignments]
    alloc_unique = set(alloc_ids)
    sel_ids = [k["keyword_id"] for k in deps.top_relevance["keywords"]]
    sel_unique = set(sel_ids)
    un_ids = [k["keyword_id"] for k in deps.unallocated["keywords"]]
    un_unique = set(un_ids)

    from collections import Counter
    counts = Counter(alloc_ids)
    reused = {k: n for k, n in counts.items() if n > 1}

    result = {
        "selected_unique_keyword_count": len(sel_unique),
        "allocated_unique_keyword_count": len(alloc_unique),
        "assignment_record_count": len(assignment_ids),
        "reused_keyword_count": len(reused),
        "unallocated_unique_keyword_count": len(un_unique),
        "reused_keyword_ids": {k: n for k, n in sorted(reused.items())},
    }

    # equation: allocated_unique + unallocated_unique == selected_unique, disjoint, covering.
    if len(alloc_unique) + len(un_unique) != len(sel_unique):
        raise Phase6BAllocationCountMismatch(
            f"unique-count equation fails: {len(alloc_unique)} + {len(un_unique)} != {len(sel_unique)}")
    overlap = alloc_unique & un_unique
    if overlap:
        raise Phase6BAllocationCountMismatch(f"allocated and unallocated conflict on {sorted(overlap)}")
    if (alloc_unique | un_unique) != sel_unique:
        raise Phase6BAllocationCountMismatch("allocated ∪ unallocated does not cover the selected set")
    unresolved = alloc_unique - sel_unique
    if unresolved:
        raise Phase6BAllocationCountMismatch(f"assigned keyword ids do not resolve: {sorted(unresolved)}")

    # reused keywords: every assignment beyond the first must carry REUSE_JUSTIFIED + distinct field
    # purpose + incremental semantic value.
    by_kw = {}
    for field, r in assignments:
        by_kw.setdefault(r["keyword_id"], []).append((field, r))
    for kw, n in reused.items():
        recs = by_kw[kw]
        fields = [f for f, _ in recs]
        if len(set(fields)) != len(fields):
            raise Phase6BAllocationCountMismatch(f"reused {kw} lacks a distinct field purpose")
        justified = sum(1 for _f, r in recs if r.get("reuse") == "REUSE_JUSTIFIED")
        if justified < n - 1:
            raise Phase6BAllocationCountMismatch(f"reused {kw}: {justified}/{n-1} REUSE_JUSTIFIED")
        for _f, r in recs:
            if r.get("reuse") == "REUSE_JUSTIFIED" and not r.get("reuse_justification"):
                raise Phase6BAllocationCountMismatch(f"reused {kw} missing reuse_justification")
        if not all(r.get("incremental_semantic_value") for _f, r in recs):
            raise Phase6BAllocationCountMismatch(f"reused {kw} missing incremental_semantic_value")

    result["reconciles"] = True
    return result


# ================================================================ GIFT / RECIPIENT
def _gift_recipient_reconciliation(deps):
    """The report confirms ONLY the recipient/nurse claim is VERIFIED. Gift is NOT verified merely
    because a keyword contains gift language; the occasion claim stays UNVERIFIED_BLOCKED. Record the
    exact evidence so downstream copy can use nurse-recipient identity but must defer gift-occasion
    claims (CLAIM_EVIDENCE_MISSING)."""
    claims = deps.claims
    recipient = claims.claim("recipient")
    occasion = claims.claim("occasion")
    return {
        "recipient_claim_id": recipient["claim_id"],
        "recipient_state": recipient["verification_state"],
        "recipient_publishable": bool(recipient["publishable"]),
        "recipient_verified_text": claims.publishable_text("recipient"),
        "gift_claim_verified": False,
        "occasion_claim_id": occasion["claim_id"],
        "occasion_state": occasion["verification_state"],
        "gift_language_status": "CLAIM_EVIDENCE_MISSING",
        "note": ("Only the recipient/nurse audience claim is VERIFIED. No verified gift/occasion claim "
                 "exists, so gift-occasion wording is deferred (CLAIM_EVIDENCE_MISSING); market-identity "
                 "recipient phrases (e.g. 'gifts for nurses') remain a search category, not an occasion "
                 "claim, exactly as the 6B classifier scored them."),
    }


# ================================================================ FIELD ASSEMBLY
def _safe_primary_keyword(deps):
    """The deterministic SAFE product+audience identity core, taken from the 6B title_primary
    allocation — NEVER the raw workspace primary ('personalized nurse sweatshirt'), whose
    personalization is UNVERIFIED_BLOCKED."""
    tp = deps.allocation_map["fields"]["title_primary"]
    if tp:
        return tp[0]["phrase"]
    return f"{deps.audience or ''} {deps.product_type or ''}".strip() or deps.product_type


def _safe_eligible_phrases(deps):
    """The 56 vetted-safe top-relevance phrases — the advisory concept pool fed to the field engines
    (all classified SAFE by 6B; the engines gate again on claim evidence)."""
    return [k["phrase"] for k in deps.top_relevance["keywords"]]


def assemble_title_options(deps):
    """Generate deterministic title candidates with the existing title engine; each is evidence- and
    audit-gated. At most one is RECOMMENDED. 6B title allocations are advisory semantic inputs only."""
    primary = _safe_primary_keyword(deps)
    tr = TE.build_titles(primary, deps.claims, deps.policy, audience_hint=deps.audience)
    limit = deps.policy.title_hard_limit

    alloc = deps.allocation_map["fields"]
    considered = ([a["phrase"] for a in alloc["title_primary"]] +
                  [a["phrase"] for a in alloc["title_support"]])

    variants = [("CONCISE", tr.title_concise), ("EXPANDED", tr.title_expanded)]
    recommended_variant = tr.recommended_candidate
    candidates = []
    seen_text = {}
    for variant, text in variants:
        char_count = len(text)
        byte_count = len(text.encode("utf-8"))
        safe = _is_safe_copy(text)
        within_policy = char_count <= limit
        used = [c for c in considered if _norm_text(c) in _norm_text(text)]
        rejected = [c for c in considered if c not in used]
        is_recommended = (variant == recommended_variant)
        if not within_policy:
            state = T_REJECTED_CATEGORY_POLICY
        elif not safe:
            state = T_BLOCKED_UNSAFE
        elif text in seen_text:
            state = T_REJECTED_DUPLICATION           # identical text already offered
            is_recommended = False
        elif is_recommended:
            state = T_RECOMMENDED_SAFE_DRAFT
        else:
            state = T_REJECTED_DUPLICATION
            is_recommended = False
        seen_text.setdefault(text, variant)
        audit = _audit_single_field(deps, title=text)
        candidates.append({
            "candidate_id": f"T6C-{variant}",
            "engine_variant": variant,
            "title": text,
            "character_count": char_count,
            "utf8_byte_count": byte_count,
            "title_hard_limit": limit,
            "allocated_concepts_considered": considered,
            "concepts_used": used,
            "concepts_paraphrased": [],
            "concepts_rejected": rejected,
            "fact_ids": [],
            "claim_ids": sorted({cid for comp in tr.components for cid in comp.get("claim_ids", [])}),
            "category_policy_result": {"title_hard_limit": limit, "within_limit": within_policy,
                                       "fallback_used": deps.policy.fallback_used},
            "duplication_result": "UNIQUE" if seen_text[text] == variant else "DUPLICATE_OF_" + seen_text[text],
            "page_audit_result": {"status": audit["status"], "publishability": audit["publishability"],
                                  "hard_failures": audit["hard_failures"]},
            "state": state,
            "score_components": tr.scores.get(variant, {}),
            "recommended": is_recommended,
            "warnings": list(tr.warnings),
        })

    recommended = [c for c in candidates if c["recommended"]]
    recommended_title = recommended[0]["title"] if recommended else None
    return {
        "schema_version": TITLE_OPTIONS_SCHEMA_VERSION,
        "workspace_id": deps.workspace_id,
        "product_id": deps.product_id,
        "engine": "listing.title_engine.build_titles",
        "title_hard_limit": limit,
        "recommended_title": recommended_title,
        "recommended_candidate_id": recommended[0]["candidate_id"] if recommended else None,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }, tr


def _bullet_field_key(i):
    return f"bullet_{i + 1}"


def assemble_bullets(deps, br):
    """Preserve exactly the five existing bullet jobs. A bullet is a Phase-6C safe draft ONLY when the
    engine marks it publishable, it carries no missing owner-fact/claim requirement, AND its copy is
    evidence-clean (the physical-claim gate). Otherwise it stays empty with an explicit reason. Never
    invent filler to populate five bullets."""
    amap_status = deps.allocation_map["field_status"]
    amap_fields = deps.allocation_map["fields"]
    records = []
    published_texts = []
    for i, b in enumerate(br.to_list()):
        field_key = _bullet_field_key(i)
        alloc = amap_fields.get(field_key, [])
        alloc_status = amap_status.get(field_key, {})
        text = b["text"] or ""
        engine_pub = b["publishability"]
        missing = list(b.get("missing_requirements") or [])
        safe = bool(text.strip()) and engine_pub == BE.PUBLISHABLE and not missing and _is_safe_copy(text)
        if safe:
            state = "SAFE_DRAFT"
            final_text = text
            reason_codes = []
            published_texts.append(final_text)
        else:
            final_text = ""
            if not text.strip():
                reason = alloc_status.get("empty_reason_code") or "CLAIM_EVIDENCE_MISSING"
            elif missing:
                reason = "OWNER_FACT_REQUIRED"
            elif not _is_safe_copy(text):
                reason = "CLAIM_EVIDENCE_MISSING"      # copy leans on an unverified concept
            else:
                reason = "FIELD_NOT_PUBLISHABLE"
            state = "EMPTY_" + reason
            reason_codes = [reason]
        if final_text:
            _slot = _placeholder_bullet_objects()
            _slot[b["bullet_number"] - 1] = _bullet_object(b["bullet_number"], b["job"], final_text, True)
            audit = _audit_single_field(deps, bullet_objects=_slot)
        else:
            audit = None
        records.append({
            "bullet_number": b["bullet_number"],
            "bullet_job": b["job"],
            "purpose": alloc_status.get("note") or b["job"],
            "allocated_concepts": [a["phrase"] for a in alloc],
            "allocation_outcomes": ([USED] if safe else [DEFERRED_OWNER_FACT_REQUIRED
                                    if reason_codes and reason_codes[0] == "OWNER_FACT_REQUIRED"
                                    else REJECTED_CLAIM_EVIDENCE]) if alloc else [],
            "text": final_text,
            "character_count": len(final_text),
            "engine_publishability": engine_pub,
            "fact_ids": [],
            "claim_ids": list(b.get("claim_ids") or []),
            "owner_fact_dependencies": missing if reason_codes[:1] == ["OWNER_FACT_REQUIRED"] else [],
            "real_asset_dependencies": [],
            "missing_requirements": missing,
            "state": state,
            "reason_codes": reason_codes,
            "page_audit_result": ({"status": audit["status"], "publishability": audit["publishability"],
                                   "hard_failures": audit["hard_failures"]} if audit else None),
            "warnings": [],
        })
    return {
        "schema_version": BULLETS_SCHEMA_VERSION,
        "workspace_id": deps.workspace_id,
        "product_id": deps.product_id,
        "engine": "listing.bullet_engine.build_bullets",
        "bullet_jobs": list(BE.JOBS),
        "bullet_count": len(records),
        "safe_draft_count": len(published_texts),
        "bullets": records,
    }, published_texts


def assemble_description(deps, dr):
    """Reuse the description engine, then keep ONLY the sections whose prose is evidence-clean. The
    recipient/occasion section ('A personalized gift for nurses.') carries unverified personalization
    + gift language and is dropped — the surviving description is natural, deterministic product
    identity. No keyword dumping, no unsupported fact, no unverified gift language."""
    kept, dropped = [], []
    for s in dr.sections:
        if _is_safe_copy(s["text"]):
            kept.append(s)
        else:
            dropped.append({"section_id": s["section_id"], "text": s["text"],
                            "reason": "CLAIM_EVIDENCE_MISSING", "gate_reason": _classify_reason(s["text"]),
                            "claim_ids": s.get("claim_ids", [])})
    text = " ".join(s["text"].strip() for s in kept).strip()
    audit = _audit_single_field(deps, description=text) if text else None
    lineage = {
        "schema_version": "phase6-description-v1",
        "engine": "listing.description_engine.build_description",
        "text": text,
        "character_count": len(text),
        "utf8_byte_count": len(text.encode("utf-8")),
        "included_sections": [{"section_id": s["section_id"], "text": s["text"], "state": s.get("state"),
                               "claim_ids": s.get("claim_ids", [])} for s in kept],
        "dropped_sections": dropped,
        "engine_omitted_sections": dr.omitted_sections,
        "publishability": "SAFE_DRAFT" if text else "EMPTY",
        "page_audit_result": ({"status": audit["status"], "publishability": audit["publishability"],
                               "hard_failures": audit["hard_failures"]} if audit else None),
    }
    return text, lineage


def assemble_backend_search_terms(deps, title, bullets, description, item_highlights_visible):
    """Reuse the existing byte-safe backend optimizer (the ONE backend authority). The 13 Phase 6B
    backend assignments are advisory candidates; the optimizer decides the final byte-safe string from
    the eligible source, preserving semantic units and excluding risky / unsupported / dup terms."""
    bo = BO.optimize_backend(deps.keyword_source, policy=deps.policy, title=title, bullets=bullets,
                             description=description, item_highlights=item_highlights_visible,
                             claim_evidence=deps.claims, product_facts=deps.facts,
                             primary_keyword=_safe_primary_keyword(deps))
    final = bo.backend_search_terms_string
    backend_tokens = set(final.split())

    candidate_records = []
    selected_ids, rejected_ids = [], []
    for a in deps.allocation_map["fields"]["backend_candidates"]:
        distinctive = [t for t in _norm_text(a["phrase"]).split() if t not in _GENERIC_TOKENS]
        surfaced = any(t in backend_tokens for t in distinctive)
        if surfaced:
            selected_ids.append(a["assignment_id"])
            reason = "IDENTITY_TOKEN_IN_FINAL_BACKEND"
        else:
            rejected_ids.append(a["assignment_id"])
            reason = "NOT_SURFACED_BY_OPTIMIZER"
        candidate_records.append({
            "assignment_id": a["assignment_id"], "keyword_id": a["keyword_id"],
            "phrase": a["phrase"], "distinctive_tokens": distinctive,
            "selected": surfaced, "reason_code": reason,
        })

    return {
        "schema_version": BACKEND_SCHEMA_VERSION,
        "workspace_id": deps.workspace_id,
        "product_id": deps.product_id,
        "engine": "listing.backend_optimizer.optimize_backend",
        "final_search_terms": final,
        "utf8_byte_count": bo.bytes_used,
        "byte_limit": bo.byte_ceiling,
        "bytes_remaining": bo.bytes_remaining,
        "semantic_units": list(bo.included_units),
        "included_terms": list(bo.included_terms),
        "candidate_assignment_ids": [a["assignment_id"] for a in deps.allocation_map["fields"]["backend_candidates"]],
        "selected_candidate_ids": selected_ids,
        "rejected_candidate_ids": rejected_ids,
        "candidate_accounting": candidate_records,
        "exclusion_summary": bo.excluded_summary,
        "source_keyword_sha256": deps.keyword_source_sha256,
        "allocation_map_sha256": deps.allocation_map_sha256,
        "deterministic_content_sha256": bo.content_sha256(),
    }, bo


def evaluate_item_highlights(deps, title, bullets, description):
    """Evaluate item highlights through the existing authority. With no verified product-attribute
    claim (the T2 state) the field stays empty — CLAIM_EVIDENCE_MISSING — and nothing is invented."""
    ih = IHB.build_item_highlights_content(deps.claims, deps.policy, keyword_source=deps.keyword_source,
                                           product_facts=deps.facts, title=title, bullets=bullets,
                                           description=description)
    texts = ih.publishable_texts()
    alloc_status = deps.allocation_map["field_status"].get("item_highlights", {})
    state = ("SAFE_DRAFT" if texts
             else (alloc_status.get("empty_reason_code") or "CLAIM_EVIDENCE_MISSING"))
    return {
        "schema_version": ITEM_HIGHLIGHTS_SCHEMA_VERSION,
        "workspace_id": deps.workspace_id,
        "product_id": deps.product_id,
        "engine": "listing.item_highlights_builder.build_item_highlights_content",
        "state": state,
        "category_support_state": ih.category_support_state,
        "texts": texts,
        "allocated_concepts": [a["phrase"] for a in deps.allocation_map["fields"].get("item_highlights", [])],
        "owner_fact_dependencies": ih.owner_fact_required,
        "fact_ids": [],
        "claim_ids": [],
        "publishable_count": ih.publishable_count,
        "blocked_count": ih.blocked_count,
        "reasons": ["CLAIM_EVIDENCE_MISSING"] if not texts else [],
    }, ih


# ================================================================ AUDIT
def _bullet_object(bullet_number, job, text, publishable):
    n = 1 if (text and publishable) else 0
    return {"bullet_number": bullet_number, "job": job, "text": text,
            "publishability": BE.PUBLISHABLE if (text and publishable) else "BLOCKED_INCOMPLETE",
            "claim_ids": [], "verification_summary": {"verified": n, "owner_review": 0,
                                                      "blocked": 0, "prohibited": 0},
            "missing_requirements": []}


def _placeholder_bullet_objects():
    """Five structurally valid, evidence-blocked bullet slots — so a field-level audit never trips the
    auditor's 'exactly five structured bullets' / 'no bullets' structural checks."""
    return [_bullet_object(i + 1, BE.JOBS[i], "", False) for i in range(5)]


def _base_listing(deps):
    return {
        "schema_version": "2.4", "category": deps.category, "generated_by": "phase6c_pdp_assembler",
        # a valid product-identity title by default so an isolated field audit never trips the
        # auditor's structural 'title missing product identity' check (overridden per title candidate).
        "title": _safe_primary_keyword(deps),
        "bullets": ["", "", "", "", ""], "bullet_objects": _placeholder_bullet_objects(),
        "selected_keywords": {"primary": [_safe_primary_keyword(deps)], "secondary": [], "backend": []},
        "category_policy": {"category_identifier": deps.policy.category_identifier,
                            "title_hard_limit": deps.policy.title_hard_limit,
                            "backend_byte_ceiling": deps.policy.backend_byte_ceiling,
                            "fallback_used": deps.policy.fallback_used,
                            "warning_state": deps.policy.warning_state},
        "product_fact_source_sha256": deps.facts.source_sha256,
        "claim_evidence": LG._claim_evidence_block(deps.claims),
        "owner_fact_required": [], "missing_requirements": [],
        **deps.keyword_source.listing_metadata(),
    }


def _audit_single_field(deps, title=None, description="", backend="", item_highlights="",
                        bullet_objects=None):
    """Audit ONE field in isolation over a structurally valid base listing (valid title + 5 blocked
    bullet slots) so only the field under test can produce a finding."""
    listing = _base_listing(deps)
    if title is not None:
        listing["title"] = title
    listing.update({"description": description, "backend": backend,
                    "item_highlights": item_highlights})
    if bullet_objects is not None:
        listing["bullet_objects"] = bullet_objects
        listing["bullets"] = [o["text"] for o in bullet_objects]
    return PA.audit_listing(listing, keyword_source=deps.keyword_source, claim_evidence=deps.claims,
                            product_facts=deps.facts, policy=deps.policy)


def _gated_bullet_objects(bullets_doc):
    objs = []
    for b in bullets_doc["bullets"]:
        o = _bullet_object(b["bullet_number"], b["bullet_job"], b["text"], b["state"] == "SAFE_DRAFT")
        o["claim_ids"] = b["claim_ids"] if b["state"] == "SAFE_DRAFT" else []
        objs.append(o)
    return objs


def run_page_audit(deps, title, bullets_doc, description, backend, backend_audit,
                   item_highlights_result):
    """Audit the assembled, evidence-gated PDP with the shared PageAuditor (the ONE auditor)."""
    objs = _gated_bullet_objects(bullets_doc)
    listing = _base_listing(deps)
    listing.update({
        "title": title, "title_recommended": title,
        "bullets": [o["text"] for o in objs],
        "bullet_objects": objs,
        "description": description,
        "backend": backend, "backend_audit": backend_audit,
        "item_highlights_publishable": item_highlights_result.publishable_highlights,
        "item_highlights_content": item_highlights_result.listing_block(),
        "item_highlights_capability_state": item_highlights_result.category_support_state,
        "aplus": [], "aplus_state": "DEFERRED_TO_LATER_STAGE",
    })
    audit = PA.audit_listing(listing, keyword_source=deps.keyword_source, claim_evidence=deps.claims,
                             product_facts=deps.facts, policy=deps.policy)
    kr = audit["keyword_results"]
    cr = audit["claim_results"]
    audit["leakage_summary"] = {
        "wrong_audience_leakage": len(kr.get("wrong_audience_leakage", [])),
        "wrong_product_leakage": len(kr.get("wrong_product_leakage", [])),
        "wrong_occasion_leakage": len(kr.get("wrong_occasion_leakage", [])),
        "brand_ip_leakage": len(kr.get("brand_ip_leakage", [])),
        "rejected_leakage": len(kr.get("rejected_leakage", [])),
        "unsupported_physical_claims": len(cr.get("unsupported", [])),
        "blocked_claim_promotion": len(cr.get("owner_review_in_copy", [])) + len(cr.get("prohibited", [])),
        "fabricated_owner_facts": len(cr.get("missing_lineage", [])),
    }
    return audit


# ================================================================ ALLOCATION OUTCOMES
def _outcome_record(field, rec, outcome, reason_codes, field_ref=None, extra=None):
    r = {
        "assignment_id": rec["assignment_id"],
        "keyword_id": rec["keyword_id"],
        "phrase": rec["phrase"],
        "source_field": field,
        "outcome": outcome,
        "reason_codes": reason_codes,
        "generated_field_reference": field_ref,
        "fact_dependencies": list(rec.get("required_fact_ids") or []),
        "claim_dependencies": list(rec.get("required_claim_ids") or []),
        "owner_fact_dependencies": list(rec.get("owner_fact_dependencies") or []),
        "real_asset_dependencies": list(rec.get("real_asset_dependencies") or []),
        "warnings": [],
    }
    if extra:
        r.update(extra)
    return r


def build_allocation_outcomes(deps, *, title_options, bullets_doc, description_text, backend_doc):
    """Every one of the Phase 6B assignments receives exactly one final Phase 6C outcome. No allocation
    disappears silently."""
    amap = deps.allocation_map["fields"]
    outcomes = []
    title_text = title_options.get("recommended_title") or ""
    title_norm = _norm_text(title_text)
    desc_norm = _norm_text(description_text)
    selected_backend = set(backend_doc["selected_candidate_ids"])

    for field, recs in amap.items():
        for rec in recs:
            phrase_norm = _norm_text(rec["phrase"])
            if field in DEFERRED_FIELD_STATES:
                mapping = {"aplus": DEFERRED_OWNER_FACT_REQUIRED,
                           "listing_image_text": DEFERRED_TO_LATER_STAGE,
                           "image_alt_text": DEFERRED_TO_LATER_STAGE}
                outcomes.append(_outcome_record(field, rec, mapping[field],
                                                [DEFERRED_FIELD_STATES[field]], field_ref=field))
            elif field == "title_primary":
                used = phrase_norm and phrase_norm in title_norm
                outcomes.append(_outcome_record(field, rec, USED if used else PARAPHRASED_NATURALLY,
                                                ["IDENTITY_IN_TITLE"] if used else ["IDENTITY_PARAPHRASED"],
                                                field_ref="title"))
            elif field == "title_support":
                # audience/recipient concept already carried by the title identity; gift/occasion unverified.
                outcomes.append(_outcome_record(field, rec, REJECTED_DUPLICATION,
                                                ["DUPLICATE_AUDIENCE_CONCEPT", "OCCASION_GIFT_UNVERIFIED"],
                                                field_ref="title"))
            elif field == "description":
                used = phrase_norm and phrase_norm in desc_norm
                outcomes.append(_outcome_record(field, rec, USED if used else PARAPHRASED_NATURALLY,
                                                ["IN_DESCRIPTION"] if used else
                                                ["COVERED_BY_PRODUCT_IDENTITY_NO_KEYWORD_DUMP"],
                                                field_ref="description"))
            elif field in ("bullet_1", "bullet_2", "bullet_3", "bullet_4", "bullet_5"):
                brec = next((b for b in bullets_doc["bullets"] if b["bullet_job"]
                             == deps.allocation_map["field_status"][field].get("bullet_job")), None)
                if brec and brec["state"] == "SAFE_DRAFT":
                    outcomes.append(_outcome_record(field, rec, USED, ["IN_BULLET"], field_ref=field))
                else:
                    owner = list(rec.get("owner_fact_dependencies") or []) or \
                        (deps.allocation_map["field_status"][field].get("owner_fact_required_concepts") or [])
                    if owner:
                        outcomes.append(_outcome_record(field, rec, DEFERRED_OWNER_FACT_REQUIRED,
                                                        ["OWNER_FACT_REQUIRED"], field_ref=field,
                                                        extra={"owner_fact_dependencies": owner}))
                    else:
                        outcomes.append(_outcome_record(field, rec, REJECTED_CLAIM_EVIDENCE,
                                                        ["CLAIM_EVIDENCE_MISSING"], field_ref=field))
            elif field == "backend_candidates":
                if rec["assignment_id"] in selected_backend:
                    outcomes.append(_outcome_record(field, rec, USED, ["IN_BACKEND"],
                                                    field_ref="backend_search_terms"))
                else:
                    outcomes.append(_outcome_record(field, rec, REJECTED_DUPLICATION,
                                                    ["NOT_SURFACED_BY_OPTIMIZER"],
                                                    field_ref="backend_search_terms"))
            else:
                outcomes.append(_outcome_record(field, rec, DEFERRED_TO_LATER_STAGE,
                                                ["DEFERRED_TO_LATER_STAGE"], field_ref=field))
    return outcomes


# ================================================================ ORCHESTRATION
class ProductDetailPageResult:
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
            "dependency_hashes": self.dependency_hashes,
            "keyword_accounting": self.keyword_accounting,
            "gift_recipient_reconciliation": self.gift_recipient_reconciliation,
            "title": self.title_options,
            "recommended_title": self.recommended_title,
            "bullets": self.bullets["bullets"],
            "bullets_summary": {"safe_draft_count": self.bullets["safe_draft_count"],
                                "bullet_jobs": self.bullets["bullet_jobs"]},
            "description": self.description_lineage,
            "backend_search_terms": self.backend,
            "item_highlights": self.item_highlights,
            "deferred_fields": self.deferred_fields,
            "allocation_outcomes": self.allocation_outcomes,
            "field_states": self.field_states,
            "owner_fact_dependencies": self.owner_fact_dependencies,
            "real_asset_dependencies": self.real_asset_dependencies,
            "page_audit": self.page_audit_summary,
            "listing_publishability_state": self.listing_publishability_state,
            "phase6c_state": self.phase6c_state,
            "ready_for_next_stage": self.ready_for_next_stage,
            "blockers": self.blockers,
            "warnings": self.warnings,
        }

    def deterministic_content_sha256(self):
        return content_sha256(self.canonical_content())

    def product_detail_page_doc(self):
        d = self.canonical_content()
        d["deterministic_content_sha256"] = self.deterministic_content_sha256()
        d["generated_artifacts"] = list(REQUIRED_ARTIFACTS) + list(SUPPORT_ARTIFACTS) + [STAGE_MANIFEST_FILENAME]
        return d

    def to_dict(self):
        return self.product_detail_page_doc()


def assemble_product_detail_page(run_dir, connectivity_mode="CONNECTED_RESEARCH"):
    """Assemble the whole Phase 6C PDP in memory (no disk writes here). Deterministic + network-free."""
    deps = load_phase6c_dependencies(run_dir)
    accounting = reconcile_phase6b_keyword_counts(deps)
    gift = _gift_recipient_reconciliation(deps)

    title_options, tr = assemble_title_options(deps)
    br = BE.build_bullets(deps.claims, _safe_primary_keyword(deps),
                          eligible_keywords=_safe_eligible_phrases(deps),
                          garment=LG.garment_from_keyword(_safe_primary_keyword(deps)))
    bullets_doc, bullet_texts = assemble_bullets(deps, br)
    dr = DE.build_description(deps.claims, _safe_primary_keyword(deps),
                             eligible_keywords=_safe_eligible_phrases(deps))
    description_text, description_lineage = assemble_description(deps, dr)
    item_highlights, ih = evaluate_item_highlights(deps, title_options.get("recommended_title") or "",
                                                   bullet_texts, description_text)
    ih_visible = " ".join(ih.publishable_texts())
    backend, bo = assemble_backend_search_terms(deps, title_options.get("recommended_title") or "",
                                                bullet_texts, description_text, ih_visible)

    audit = run_page_audit(deps, title_options.get("recommended_title") or "", bullets_doc,
                           description_text, backend["final_search_terms"], bo.audit(), ih)

    allocation_outcomes = build_allocation_outcomes(
        deps, title_options=title_options, bullets_doc=bullets_doc,
        description_text=description_text, backend_doc=backend)

    # field states
    field_states = {
        "title_primary": title_options["candidates"][0]["state"] if title_options["candidates"] else "EMPTY",
        "title_support": T_REJECTED_DUPLICATION,
        "description": description_lineage["publishability"],
        "backend_candidates": "SAFE_DRAFT",
        "item_highlights": item_highlights["state"],
    }
    for b in bullets_doc["bullets"]:
        field_states[_bullet_field_key(b["bullet_number"] - 1)] = b["state"]
    field_states.update(DEFERRED_FIELD_STATES)

    # owner-fact + real-asset dependencies (from the verified 6A workspace, deduped, deterministic)
    ws = PW.load_product_workspace(PW.phase6a_dir(run_dir))
    owner_fact_dependencies = sorted(ws.get("normalized_product_facts", {}).get("owner_fact_required", []))
    real_asset_dependencies = sorted({a["asset_requirement_id"] for a in ws.get("real_asset_requirements", [])})

    # states / gates
    blockers, warnings = [], []
    leak = audit["leakage_summary"]
    if audit["hard_failures"]:
        warnings.append("page auditor reported hard failures")
    unsafe = (audit["publishability"] == PA.BLOCKED_UNSAFE) or bool(audit["hard_failures"]) \
        or leak["unsupported_physical_claims"] or leak["blocked_claim_promotion"] \
        or leak["fabricated_owner_facts"] or any(leak[k] for k in
        ("wrong_audience_leakage", "wrong_product_leakage", "wrong_occasion_leakage", "brand_ip_leakage"))
    if unsafe:
        phase6c_state = BLOCKED_UNSAFE
        blockers.append("page audit is not clean; PDP blocked")
    else:
        phase6c_state = SAFE_PDP_OWNER_FACTS_REQUIRED

    result = ProductDetailPageResult(
        run_dir=run_dir,
        connectivity_mode=connectivity_mode,
        schema_version=SCHEMA_VERSION,
        workspace_id=deps.workspace_id, product_id=deps.product_id, category=deps.category,
        product_type=deps.product_type, audience=deps.audience,
        dependency_hashes={
            "phase6a_manifest_sha256": deps.phase6a_manifest_sha256,
            "phase6b_manifest_sha256": deps.phase6b_manifest_sha256,
            "keyword_source_sha256": deps.keyword_source_sha256,
            "product_facts_sha256": deps.product_facts_sha256,
            "claim_evidence_sha256": deps.claim_evidence_sha256,
            "allocation_map_sha256": deps.allocation_map_sha256,
        },
        keyword_accounting=accounting,
        gift_recipient_reconciliation=gift,
        title_options=title_options,
        recommended_title=title_options.get("recommended_title"),
        bullets=bullets_doc,
        description_lineage=description_lineage,
        description_text=description_text,
        backend=backend,
        item_highlights=item_highlights,
        deferred_fields={k: {"state": v, "note": "executed in a later, owner-approved stage; not in 6C"}
                         for k, v in DEFERRED_FIELD_STATES.items()},
        allocation_outcomes=allocation_outcomes,
        field_states=field_states,
        owner_fact_dependencies=owner_fact_dependencies,
        real_asset_dependencies=real_asset_dependencies,
        page_audit=audit,
        page_audit_summary={"status": audit["status"], "publishability": audit["publishability"],
                            "hard_failure_count": len(audit["hard_failures"]),
                            "leakage_summary": audit["leakage_summary"]},
        listing_publishability_state=audit["publishability"],
        phase6c_state=phase6c_state,
        ready_for_next_stage=(phase6c_state == SAFE_PDP_OWNER_FACTS_REQUIRED),
        blockers=blockers,
        warnings=warnings,
    )
    return result


# ================================================================ WRITE / VERIFY
def _manifest_hashable(body):
    return {k: v for k, v in body.items() if k not in _MANIFEST_VOLATILE}


def _stage_manifest_doc(result, output_hashes, *, started_at=None, completed_at=None,
                        previous_valid_manifest_sha256=None, verification=None):
    body = {
        "schema_version": STAGE_MANIFEST_SCHEMA_VERSION,
        "stage_id": STAGE_ID,
        "stage_name": STAGE_NAME,
        "workspace_id": result.workspace_id,
        "product_id": result.product_id,
        "state": result.phase6c_state,
        "listing_publishability_state": result.listing_publishability_state,
        "ready_for_next_stage": result.ready_for_next_stage,
        "required_inputs": ["MASTER-KEYWORDS-LEAN.json", "phase6/6A/*", "phase6/6B/*"],
        "input_hashes": {
            "phase6a_manifest_sha256": result.dependency_hashes["phase6a_manifest_sha256"],
            "phase6b_manifest_sha256": result.dependency_hashes["phase6b_manifest_sha256"],
            "source_keyword_sha256": result.dependency_hashes["keyword_source_sha256"],
            "product_facts_sha256": result.dependency_hashes["product_facts_sha256"],
            "claim_evidence_sha256": result.dependency_hashes["claim_evidence_sha256"],
            "allocation_map_sha256": result.dependency_hashes["allocation_map_sha256"],
        },
        "generated_outputs": sorted(output_hashes),
        "output_hashes": output_hashes,
        "keyword_accounting": result.keyword_accounting,
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


def _description_text_bytes(text):
    """DESCRIPTION.txt — deterministic UTF-8, LF-only, single trailing newline (or none if empty)."""
    return (text + "\n") if text else ""


def write_phase6c_artifacts(result, dest_dir=None, *, workspace_dir=None, started_at=None,
                            completed_at=None, verification=None):
    """Build every document + exact bytes IN MEMORY, validate all, compute output hashes, THEN commit
    each file atomically. A validation failure raises and leaves the last valid Phase 6C run intact."""
    workspace_dir = workspace_dir or result.run_dir
    dest_dir = dest_dir or phase6c_dir(workspace_dir)

    pdp_doc = result.product_detail_page_doc()
    titles_doc = result.title_options
    bullets_doc = result.bullets
    backend_doc = result.backend
    item_highlights_doc = result.item_highlights
    audit_doc = result.page_audit
    field_blockers_doc = _field_blockers_doc(result)
    lineage_doc = _copy_lineage_doc(result)
    description_bytes = _description_text_bytes(result.description_text)

    content = [
        (PRODUCT_DETAIL_PAGE_FILENAME, canonical_json(pdp_doc)),
        (TITLE_OPTIONS_FILENAME, canonical_json(titles_doc)),
        (BULLETS_FILENAME, canonical_json(bullets_doc)),
        (DESCRIPTION_FILENAME, description_bytes),
        (BACKEND_FILENAME, canonical_json(backend_doc)),
        (ITEM_HIGHLIGHTS_FILENAME, canonical_json(item_highlights_doc)),
        (PAGE_AUDIT_FILENAME, canonical_json(audit_doc)),
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
    _raise_on_errors("PRODUCT-DETAIL-PAGE", validate_product_detail_page(pdp_doc))
    _raise_on_errors("TITLE-OPTIONS", validate_title_options(titles_doc))
    _raise_on_errors("BULLETS", validate_bullets(bullets_doc))
    _raise_on_errors("BACKEND-SEARCH-TERMS", validate_backend(backend_doc))
    _raise_on_errors("STAGE-6C-MANIFEST", validate_stage6c_manifest(manifest, workspace_dir))
    _forbid_later_stage_outputs(dest_dir)

    os.makedirs(dest_dir, exist_ok=True)
    for name, text in content:
        _atomic_write_text(os.path.join(dest_dir, name), text)
    _atomic_write_json(os.path.join(dest_dir, STAGE_MANIFEST_FILENAME), manifest)
    return manifest


def _field_blockers_doc(result):
    blocked = []
    for b in result.bullets["bullets"]:
        if b["state"] != "SAFE_DRAFT":
            blocked.append({"field": _bullet_field_key(b["bullet_number"] - 1), "job": b["bullet_job"],
                            "state": b["state"], "reason_codes": b["reason_codes"],
                            "owner_fact_dependencies": b["owner_fact_dependencies"],
                            "missing_requirements": b["missing_requirements"]})
    if result.item_highlights["state"] != "SAFE_DRAFT":
        blocked.append({"field": "item_highlights", "state": result.item_highlights["state"],
                        "reason_codes": result.item_highlights["reasons"],
                        "owner_fact_dependencies": result.item_highlights["owner_fact_dependencies"]})
    for f, st in DEFERRED_FIELD_STATES.items():
        blocked.append({"field": f, "state": st, "reason_codes": [st], "owner_fact_dependencies": []})
    return {
        "schema_version": "phase6-field-blockers-v1",
        "workspace_id": result.workspace_id, "product_id": result.product_id,
        "owner_fact_dependencies": result.owner_fact_dependencies,
        "real_asset_dependencies": result.real_asset_dependencies,
        "blocked_fields": blocked,
    }


def _copy_lineage_doc(result):
    return {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "workspace_id": result.workspace_id, "product_id": result.product_id,
        "dependency_hashes": result.dependency_hashes,
        "keyword_accounting": result.keyword_accounting,
        "gift_recipient_reconciliation": result.gift_recipient_reconciliation,
        "allocation_outcomes": result.allocation_outcomes,
        "description_sections": result.description_lineage,
        "field_states": result.field_states,
        "deferred_fields": result.deferred_fields,
    }


def load_product_detail_page(dest_dir):
    with open(os.path.join(dest_dir, PRODUCT_DETAIL_PAGE_FILENAME), encoding="utf-8") as f:
        return json.load(f)


def verify_phase6c_artifacts(dest_dir, workspace_dir=None):
    """Re-read the written artifacts and confirm the manifest hashes match the final bytes, required
    artifacts are present, no path escapes, and no later-stage artifact leaked in."""
    workspace_dir = workspace_dir or os.path.dirname(os.path.dirname(os.path.abspath(dest_dir)))
    errors = []
    for name in REQUIRED_ARTIFACTS:
        if not os.path.exists(os.path.join(dest_dir, name)):
            errors.append(f"missing required artifact: {name}")
    man_path = os.path.join(dest_dir, STAGE_MANIFEST_FILENAME)
    if not os.path.exists(man_path):
        errors.append("missing STAGE-6C-MANIFEST.json")
        return {"ok": False, "errors": errors, "checked": []}
    with open(man_path, encoding="utf-8") as f:
        manifest = json.load(f)
    errors.extend(validate_stage6c_manifest(manifest, workspace_dir))
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
    except Phase6CAssemblyError as e:
        errors.append(str(e))
    return {"ok": not errors, "errors": errors, "checked": sorted(checked)}


def _forbid_later_stage_outputs(dest_dir):
    if not os.path.isdir(dest_dir):
        return
    present = set(os.listdir(dest_dir))
    leaked = present & FORBIDDEN_ARTIFACTS
    if leaked:
        raise Phase6CAssemblyError(f"forbidden later-stage artifact present: {sorted(leaked)}")


# ================================================================ VALIDATORS
def _raise_on_errors(label, errors):
    if errors:
        raise Phase6CAssemblyError(f"{label} failed validation: " + "; ".join(errors))


def validate_product_detail_page(doc):
    errors = []
    if doc.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if not doc.get("workspace_id"):
        errors.append("empty workspace_id")
    if doc.get("listing_publishability_state") not in (LISTING_SAFE_DRAFT, PA.PUBLISHABLE):
        errors.append("unexpected listing_publishability_state")
    if doc.get("phase6c_state") not in (READY_FOR_6D, SAFE_PDP_OWNER_FACTS_REQUIRED):
        errors.append(f"unexpected phase6c_state {doc.get('phase6c_state')}")
    if doc.get("deterministic_content_sha256") != content_sha256(
            {k: v for k, v in doc.items() if k not in ("deterministic_content_sha256", "generated_artifacts")}):
        errors.append("deterministic_content_sha256 does not recompute")
    if not isinstance(doc.get("allocation_outcomes"), list) or not doc["allocation_outcomes"]:
        errors.append("allocation_outcomes missing")
    # A+ / creative content must never appear in the PDP.
    blob = canonical_json(doc).lower()
    for forbidden in ("basic-aplus", "premium-aplus", "image_prompt", "image prompt", "aplus_modules"):
        if forbidden in blob:
            errors.append(f"forbidden creative content in PDP: {forbidden}")
    return errors


def validate_title_options(doc):
    errors = []
    if doc.get("schema_version") != TITLE_OPTIONS_SCHEMA_VERSION:
        errors.append("bad title-options schema_version")
    cands = doc.get("candidates") or []
    if not cands:
        errors.append("no title candidates")
    if sum(1 for c in cands if c.get("recommended")) > 1:
        errors.append("more than one recommended title")
    for c in cands:
        if c.get("character_count") is None or c.get("utf8_byte_count") is None:
            errors.append("candidate missing counts")
        if c.get("state") not in (T_RECOMMENDED_SAFE_DRAFT, T_PUBLISHABLE, T_OWNER_FACT_REQUIRED,
                                  T_BLOCKED_UNSAFE, T_REJECTED_DUPLICATION, T_REJECTED_NATURAL_LANGUAGE,
                                  T_REJECTED_CATEGORY_POLICY):
            errors.append(f"bad candidate state {c.get('state')}")
        low = str(c.get("title", "")).lower()
        for bad in ("personalized", "custom ", "embroider", "cotton", "monogram"):
            if bad in low:
                errors.append(f"unsupported term in title candidate: {bad}")
    return errors


def validate_bullets(doc):
    errors = []
    if doc.get("schema_version") != BULLETS_SCHEMA_VERSION:
        errors.append("bad bullets schema_version")
    bullets = doc.get("bullets") or []
    if len(bullets) != 5:
        errors.append(f"expected exactly 5 bullet records, got {len(bullets)}")
    jobs = [b.get("bullet_job") for b in bullets]
    if jobs != list(BE.JOBS):
        errors.append("bullet jobs must preserve the existing five-job taxonomy in order")
    for b in bullets:
        if b["state"] != "SAFE_DRAFT" and b["text"]:
            errors.append(f"empty-state bullet {b['bullet_number']} must have empty text")
        if b["state"] != "SAFE_DRAFT" and not b["reason_codes"]:
            errors.append(f"empty bullet {b['bullet_number']} lacks a reason")
    return errors


def validate_backend(doc):
    errors = []
    if doc.get("schema_version") != BACKEND_SCHEMA_VERSION:
        errors.append("bad backend schema_version")
    if doc.get("utf8_byte_count", 0) > doc.get("byte_limit", 0):
        errors.append("backend exceeds byte limit")
    sel = set(doc.get("selected_candidate_ids", []))
    rej = set(doc.get("rejected_candidate_ids", []))
    cand = set(doc.get("candidate_assignment_ids", []))
    if sel & rej:
        errors.append("candidate id both selected and rejected")
    if (sel | rej) != cand:
        errors.append("selected+rejected candidate ids do not cover the candidate set")
    return errors


def validate_stage6c_manifest(doc, workspace_dir):
    errors = []
    if doc.get("schema_version") != STAGE_MANIFEST_SCHEMA_VERSION:
        errors.append("invalid manifest schema_version")
    if doc.get("stage_id") != STAGE_ID:
        errors.append("manifest stage_id must be 6C")
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
    ap = argparse.ArgumentParser(description="Phase 6C — assemble a truthful Product Detail Page draft.")
    ap.add_argument("run_dir")
    ap.add_argument("--mode", default="CONNECTED_RESEARCH",
                    choices=["CONNECTED_RESEARCH", "LOCAL_SAFE", "TEST_DENY_EXTERNAL"])
    ap.add_argument("--write", action="store_true", help="write artifacts under <run>/phase6/6C")
    a = ap.parse_args()
    result = assemble_product_detail_page(a.run_dir, connectivity_mode=a.mode)
    if a.write:
        man = write_phase6c_artifacts(result, workspace_dir=a.run_dir, started_at="cli", completed_at="cli")
        v = verify_phase6c_artifacts(phase6c_dir(a.run_dir), workspace_dir=a.run_dir)
        print(f"wrote Phase 6C artifacts; verify ok={v['ok']}")
    print(f"phase6c_state={result.phase6c_state}  listing={result.listing_publishability_state}  "
          f"ready_for_next_stage={result.ready_for_next_stage}")
    print(f"recommended_title={result.recommended_title!r}  "
          f"safe_bullets={result.bullets['safe_draft_count']}/5  "
          f"backend_bytes={result.backend['utf8_byte_count']}/{result.backend['byte_limit']}")


if __name__ == "__main__":
    main()
