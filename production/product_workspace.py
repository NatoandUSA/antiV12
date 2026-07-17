#!/usr/bin/env python3
"""
production.product_workspace — the ONE Phase 6A product-readiness orchestration authority.

Phase 6A establishes a single trustworthy product workspace BEFORE keyword allocation (6B) or
listing assembly (6C+) begins. It does NOT generate a listing, allocate keywords, create image
prompts, or assemble a Seller Central package.

It reuses — never re-implements — the mature Phase 5 authorities:
  * keyword source          -> listing.keyword_source_adapter.load_keyword_source
  * category policy          -> listing.category_policy_registry.resolve_category_policy
  * normalized product facts -> listing.product_fact_loader.load_product_facts
  * claim evidence           -> listing.claim_evidence.build_claim_evidence
  * audience / garment reads  -> listing.listing_generator._audience_from_keywords / garment_from_keyword
  * listing publishability   -> the recorded Phase 5 verdict (read, not recomputed)
  * runtime / network safety  -> core.runtime_policy / core.network_policy / core.diagnostics

On top of those it derives, without fabricating any fact:
  1. the selected product identity (from explicit repository evidence, never filename similarity);
  2. missing owner facts (actionable OWNER-INPUT requirements);
  3. missing real assets (physical proof that AI mockups can never satisfy);
  4. a Phase 6A workspace-readiness state, kept separate from listing publishability;
  5. four required artifacts + minimal support artifacts, written atomically and deterministically.

Public API:
  build_product_workspace(workspace_dir, ...) -> ProductWorkspaceResult   (alias: create_product_workspace)
  load_product_workspace(dest_dir) -> dict
  evaluate_product_readiness(result) -> dict
  select_product_workspace(runs_root, workspace_id=None) -> workspace_dir
  write_phase6a_artifacts(result, dest_dir, ...) -> manifest dict
  verify_phase6a_artifacts(dest_dir) -> dict
  validate_* (product_workspace / owner_input / normalized_facts / claim_evidence / manifest)
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ("listing", "core"):
    _p = os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import keyword_source_adapter as KSA          # noqa: E402
import product_fact_loader as PFL             # noqa: E402
import claim_evidence as CE                   # noqa: E402
import category_policy_registry as CPR        # noqa: E402
import listing_generator as LG                # noqa: E402  (reused for audience/garment reads only)
import runtime_policy as RP                   # noqa: E402  (offline-only runtime snapshot)

SCHEMA_VERSION = "phase6a-product-workspace-v1"
OWNER_INPUT_SCHEMA_VERSION = "phase6-owner-input-v1"
STAGE_MANIFEST_SCHEMA_VERSION = "phase6a-stage-manifest-v1"

STAGE_ID = "6A"
STAGE_NAME = "Product Readiness and Owner Facts"

# -- Phase 6A workspace-readiness states (NOT the same as listing publishability) --------------
READY_FOR_6B = "READY_FOR_6B"
OWNER_FACTS_REQUIRED = "OWNER_FACTS_REQUIRED"
REAL_ASSETS_REQUIRED = "REAL_ASSETS_REQUIRED"
OWNER_FACTS_AND_REAL_ASSETS_REQUIRED = "OWNER_FACTS_AND_REAL_ASSETS_REQUIRED"
BLOCKED_UNSAFE = "BLOCKED_UNSAFE"
INVALID_SOURCE = "INVALID_SOURCE"
CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
WORKSPACE_STATES = (READY_FOR_6B, OWNER_FACTS_REQUIRED, REAL_ASSETS_REQUIRED,
                    OWNER_FACTS_AND_REAL_ASSETS_REQUIRED, BLOCKED_UNSAFE, INVALID_SOURCE,
                    CONFLICTING_EVIDENCE)

# -- listing publishability states (mirrors listing.page_auditor; a test pins the equality) -----
LISTING_PUBLISHABLE = "PUBLISHABLE"
LISTING_SAFE_DRAFT = "SAFE_DRAFT_OWNER_FACTS_REQUIRED"
LISTING_BLOCKED_UNSAFE = "BLOCKED_UNSAFE"
LISTING_NOT_YET_GENERATED = "NOT_YET_GENERATED"
LISTING_PUBLISHABILITY_STATES = (LISTING_PUBLISHABLE, LISTING_SAFE_DRAFT, LISTING_BLOCKED_UNSAFE,
                                 LISTING_NOT_YET_GENERATED)

# -- artifact filenames (exact) -----------------------------------------------------------------
PRODUCT_WORKSPACE_FILENAME = "PRODUCT-WORKSPACE.json"
OWNER_INPUT_FILENAME = "OWNER-INPUT-REQUIRED.json"
PRODUCT_READINESS_FILENAME = "PRODUCT-READINESS-REPORT.md"
NORMALIZED_FACTS_FILENAME = PFL.NORMALIZED_FILENAME          # "NORMALIZED-PRODUCT-FACTS.json"
CLAIM_EVIDENCE_FILENAME = CE.CLAIM_EVIDENCE_FILENAME         # "CLAIM-EVIDENCE.json"
STAGE_MANIFEST_FILENAME = "STAGE-6A-MANIFEST.json"
REQUIRED_ARTIFACTS = (OWNER_INPUT_FILENAME, PRODUCT_READINESS_FILENAME,
                      NORMALIZED_FACTS_FILENAME, CLAIM_EVIDENCE_FILENAME)

DEFAULT_CATEGORY = "apparel"
PHASE6_SUBPATH = ("phase6", "6A")

PRIORITY_BLOCKING = "BLOCKING"
PRIORITY_IMPORTANT = "IMPORTANT"
PRIORITY_OPTIONAL = "OPTIONAL"
PRIORITY_RANK = {PRIORITY_BLOCKING: 0, PRIORITY_IMPORTANT: 1, PRIORITY_OPTIONAL: 2}

REQ_OPEN = "OPEN"
REQ_RESOLVED = "RESOLVED"
REQ_NOT_APPLICABLE = "NOT_APPLICABLE"
REQ_REJECTED_UNSAFE = "REJECTED_UNSAFE"

ASSET_REQUIRED = "REAL_ASSET_REQUIRED"
ASSET_PRESENT_UNVERIFIED = "ASSET_PRESENT_UNVERIFIED"
ASSET_VERIFIED = "ASSET_VERIFIED"
ASSET_NOT_APPLICABLE = "NOT_APPLICABLE"
ASSET_BLOCKED_UNSAFE = "BLOCKED_UNSAFE"

# evidence an AI image / mockup can NEVER satisfy for a physical claim.
NOT_ACCEPTED_AS_PROOF = ["AI-generated image or mockup", "design mockup / digital rendering",
                         "generic competitor listing", "generic supplier catalog statement not tied "
                         "to this exact product"]


class ProductWorkspaceError(Exception):
    """Base class for every Phase 6A product-workspace failure."""


class AmbiguousProductError(ProductWorkspaceError):
    """More than one candidate product workspace and no explicit selection — never guess."""

    def __init__(self, runs_root, candidates):
        self.runs_root = runs_root
        self.candidates = sorted(candidates)
        super().__init__("OWNER_SELECTION_REQUIRED: multiple candidate product workspaces in "
                         f"{os.path.basename(runs_root)} — {self.candidates}. Pass workspace_id "
                         "explicitly; Phase 6A never combines data from multiple products.")


# ---------------------------------------------------------------- primitives
def canonical_json(obj):
    """Stable canonical serialization — identical to the Phase 5 engines' form."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2, separators=(",", ": "))


def content_sha256(obj):
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_rel(path, base):
    """A normalized, forward-slash relative path inside *base*. Rejects parent traversal / escape."""
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(base))
    norm = rel.replace(os.sep, "/")
    if os.path.isabs(rel) or norm == ".." or norm.startswith("../"):
        raise ProductWorkspaceError(f"path escapes workspace ({norm}): {path}")
    return norm


def _atomic_write_text(path, text):
    """Write text to a temp sibling, fsync, then atomically replace. A failed write never
    overwrites the last valid artifact (the replace only happens on a fully written temp)."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-6a-", suffix=".part")
    try:
        # newline="" => no platform newline translation, so the file bytes equal the canonical
        # UTF-8 bytes exactly (portable, and the manifest output hash always matches the file).
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


def phase6a_dir(workspace_dir):
    return os.path.join(workspace_dir, *PHASE6_SUBPATH)


# ---------------------------------------------------------------- owner-fact / real-asset knowledge
# One entry per underlying physical fact. `also` folds synonym fields into ONE question so the owner is
# never asked twice for the same underlying fact. Priority reflects downstream copy impact, NOT 6B: a
# missing owner fact blocks safe publishable COPY (6C+), it does not block keyword allocation (6B).
_FACT_META = {
    "material_composition": dict(priority=PRIORITY_BLOCKING, also=("material",), copy_stage="6C",
        key_label="garment_material",
        question="What is the verified garment material / fabric composition (e.g. '80% cotton, 20% polyester')?",
        reason="Required before any material or fabric claim can appear in bullets, description, A+, or product details.",
        accepted=["supplier specification sheet", "purchase invoice line specification",
                  "verified garment label photo"]),
    "decoration_method": dict(priority=PRIORITY_BLOCKING, copy_stage="6C", key_label="decoration_method",
        question="What is the verified decoration method (e.g. machine embroidery, satin-stitch, print)?",
        reason="Required before any embroidery/decoration claim (real embroidery, satin stitch, tatami fill) can be used.",
        accepted=["supplier decoration specification", "real embroidery macro photo of the actual product",
                  "production sample photo"]),
    "personalization_fields": dict(priority=PRIORITY_BLOCKING, copy_stage="6C", key_label="personalization_fields",
        question="Which personalization fields do you actually offer (e.g. name, credentials) and their character limits?",
        reason="Required before personalization instructions or an exact-personalization promise can be published.",
        accepted=["owner-confirmed personalization options and character limits", "order-form specification"]),
    "size_range": dict(priority=PRIORITY_BLOCKING, copy_stage="6C", key_label="size_range",
        question="Which sizes are actually offered (e.g. S-3XL)?",
        reason="Required before size availability can be stated in the listing.",
        accepted=["supplier size availability list", "verified size chart"]),
    "color_options": dict(priority=PRIORITY_BLOCKING, copy_stage="6C", key_label="color_options",
        question="Which colors are actually available from the supplier?",
        reason="Required before color options can be listed.",
        accepted=["supplier color list", "real color swatch photo from the actual supplier"]),
    "fit": dict(priority=PRIORITY_IMPORTANT, copy_stage="6C", key_label="fit",
        question="What is the verified fit (e.g. unisex, relaxed, true-to-size)?",
        reason="Required before a fit claim can be published.",
        accepted=["supplier fit specification", "owner-confirmed fit from a real sample"]),
    "care_instructions": dict(priority=PRIORITY_IMPORTANT, copy_stage="6C", key_label="care_instructions",
        question="What are the verified care instructions (wash / dry)?",
        reason="Required before care instructions can be published.",
        accepted=["garment care label photo", "supplier care specification"]),
    "measurements": dict(priority=PRIORITY_IMPORTANT, copy_stage="6C", key_label="measurements",
        question="What are the real garment measurements per size?",
        reason="Required before measurement/fit detail can be published.",
        accepted=["measured garment dimensions", "verified size chart with measurements"]),
    "production_time_range": dict(priority=PRIORITY_IMPORTANT, copy_stage="6C", key_label="production_time",
        question="What is the real production (make) time before shipping?",
        reason="Required before a production-time claim can be published.",
        accepted=["owner-confirmed production lead time", "supplier turnaround specification"]),
    "shipping_method": dict(priority=PRIORITY_IMPORTANT, copy_stage="6C", key_label="shipping_method",
        question="What is the verified shipping method / origin (e.g. shipped from the US, carrier)?",
        reason="Required before any shipping or US-origin claim can be used.",
        accepted=["owner-confirmed shipping method and origin", "carrier account confirmation"]),
    "production_location": dict(priority=PRIORITY_OPTIONAL, copy_stage="6C", key_label="production_location",
        question="Where is the product actually produced / decorated?",
        reason="Required before any 'made in ...' or origin claim can be used.",
        accepted=["supplier production-location statement for THIS product", "invoice showing origin"]),
    "handling_time": dict(priority=PRIORITY_OPTIONAL, copy_stage="6C", key_label="handling_time",
        question="What is your handling time before dispatch?",
        reason="Required before a handling-time claim can be used.",
        accepted=["owner-confirmed handling time"]),
    "tracking": dict(priority=PRIORITY_OPTIONAL, copy_stage="6C", key_label="tracking",
        question="Do you provide order tracking?",
        reason="Required before a tracking claim can be used.",
        accepted=["owner-confirmed tracking provision"]),
    "packaging": dict(priority=PRIORITY_OPTIONAL, copy_stage="6C", key_label="packaging",
        question="How is the product packaged (e.g. gift packaging)?",
        reason="Required before any packaging claim can be used.",
        accepted=["real packaging photo", "owner-confirmed packaging description"]),
    "occasion": dict(priority=PRIORITY_OPTIONAL, copy_stage="6C", key_label="occasion",
        question="Which occasions is this product genuinely intended for?",
        reason="Required before an occasion claim can be published — keyword relevance alone does not verify it.",
        accepted=["owner-confirmed target occasions"]),
    "verified_differentiator": dict(priority=PRIORITY_OPTIONAL, copy_stage="6C", key_label="verified_differentiator",
        question="What is your verified differentiator versus competitors?",
        reason="Required before a differentiation claim can be published.",
        accepted=["owner-confirmed, evidence-backed differentiator"]),
}

# claims the engine will NEVER infer from a neighbouring fact — they need explicit owner attestation and
# carry no fact field. They are OPTIONAL: the owner may simply choose not to make the claim, so a missing
# attestation must NOT push the whole workspace out of readiness — it only means that one claim stays out.
_NOT_INFERRABLE = {
    "durability": dict(priority=PRIORITY_OPTIONAL, label="durability ('made to last')"),
    "softness_comfort": dict(priority=PRIORITY_OPTIONAL, label="softness / comfort"),
    "made_to_order": dict(priority=PRIORITY_OPTIONAL, label="made-to-order"),
    "exact_personalization_promise": dict(priority=PRIORITY_OPTIONAL,
                                          label="exact-personalization promise"),
}

# fact field -> claim concepts it backs, derived from the ONE claim-evidence authority (no duplication).
_FIELD_TO_CONCEPTS = {}
for _spec in CE.CLAIM_SPECS:
    for _f in _spec.evidence_fields:
        _FIELD_TO_CONCEPTS.setdefault(_f, set()).add(_spec.concept)

# real-asset requirements: physical proof for physical claims. Each names the concepts it proves; a
# requirement is only raised when at least one backing concept is still unverified.
_REAL_ASSET_SPECS = [
    dict(rid="REAL-ASSET-001", purpose="Prove real embroidery / decoration quality",
         concepts=("decoration_method", "real_machine_embroidery", "satin_stitch", "tatami_fill"),
         viewpoint="Macro close-up of the actual stitched decoration on the real, finished product",
         priority=PRIORITY_BLOCKING, blocks=("6C", "6E")),
    dict(rid="REAL-ASSET-002", purpose="Prove the real garment (front)",
         concepts=("material_composition", "fit", "size_range"),
         viewpoint="Front photo of the actual garment on a plain, uncluttered background",
         priority=PRIORITY_BLOCKING, blocks=("6C", "6E")),
    dict(rid="REAL-ASSET-003", purpose="Prove real personalization output",
         concepts=("personalization_fields", "exact_personalization_promise"),
         viewpoint="Photo of a real personalized example with the name/credentials legible",
         priority=PRIORITY_BLOCKING, blocks=("6C", "6E")),
    dict(rid="REAL-ASSET-004", purpose="Prove available colors",
         concepts=("color_options",),
         viewpoint="Real color swatch / grid from the actual supplier (no digital recolors)",
         priority=PRIORITY_IMPORTANT, blocks=("6C", "6E")),
    dict(rid="REAL-ASSET-005", purpose="Prove garment label (material + care)",
         concepts=("material_composition", "care_instructions"),
         viewpoint="Photo of the real garment label showing fiber content and care",
         priority=PRIORITY_IMPORTANT, blocks=("6C", "6E")),
    dict(rid="REAL-ASSET-006", purpose="Prove real measurements / size chart",
         concepts=("measurements", "size_range"),
         viewpoint="Size chart built from real measured garments",
         priority=PRIORITY_IMPORTANT, blocks=("6C", "6E")),
    dict(rid="REAL-ASSET-007", purpose="Prove packaging",
         concepts=("packaging",),
         viewpoint="Photo of the real packaging the buyer receives",
         priority=PRIORITY_OPTIONAL, blocks=("6E",)),
]
_ASSET_FILE_TYPES = ["jpg", "jpeg", "png", "tiff", "webp"]


# ---------------------------------------------------------------- result
class ProductWorkspaceResult:
    """The canonical, deterministic Phase 6A view of one selected product workspace."""

    def __init__(self, *, workspace_id, product_id, product_type, category, category_policy,
                 primary_keyword, audience, keyword_source, normalized_facts, claim_evidence,
                 owner_input_requirements, not_inferrable_requirements, real_asset_requirements,
                 blockers, warnings, workspace_state, current_listing_publishability_state,
                 downstream_readiness, source_files, source_hashes, resolved_requirements=()):
        self.schema_version = SCHEMA_VERSION
        self.workspace_id = workspace_id
        self.product_id = product_id
        self.product_type = product_type
        self.category = category
        self._policy = category_policy
        self.primary_keyword = primary_keyword
        self.audience = audience
        self._src = keyword_source
        self.normalized_product_facts = normalized_facts      # PFL.NormalizedProductFacts
        self.claim_evidence = claim_evidence                  # CE.ClaimEvidence
        self.owner_input_requirements = owner_input_requirements
        self.not_inferrable_requirements = not_inferrable_requirements
        self.resolved_requirements = list(resolved_requirements)
        self.real_asset_requirements = real_asset_requirements
        self.blockers = list(blockers)
        self.warnings = list(warnings)
        self.workspace_state = workspace_state
        self.current_listing_publishability_state = current_listing_publishability_state
        self.downstream_readiness = downstream_readiness
        self.source_files = list(source_files)
        self.source_hashes = dict(source_hashes)

    # -- hashes exposed on the result ------------------------------------------------------------
    @property
    def keyword_source_sha256(self):
        return self._src.source_sha256 if self._src else None

    @property
    def product_facts_sha256(self):
        return self.normalized_product_facts.content_sha256()

    @property
    def claim_evidence_sha256(self):
        return self.claim_evidence.content_sha256()

    @property
    def all_owner_requirements(self):
        return list(self.owner_input_requirements) + list(self.not_inferrable_requirements)

    # -- summaries (compact; the full payloads live in their own artifacts) ----------------------
    def _facts_summary(self):
        f = self.normalized_product_facts
        return {"file": "/".join(PHASE6_SUBPATH) + "/" + NORMALIZED_FACTS_FILENAME,
                "content_sha256": f.content_sha256(), "source_file": f.source_file,
                "source_sha256": f.source_sha256, "verified": f.verified_fact_count,
                "owner_review": f.owner_review_count, "unknown": f.unknown_fact_count,
                "blocked": f.blocked_fact_count, "owner_fact_required": f.owner_fact_required}

    def _claims_summary(self):
        c = self.claim_evidence
        return {"file": "/".join(PHASE6_SUBPATH) + "/" + CLAIM_EVIDENCE_FILENAME,
                "content_sha256": c.content_sha256(), "verified": c.verified_count,
                "owner_review": c.owner_review_count, "blocked": c.blocked_count,
                "prohibited": c.prohibited_count,
                "publishable_claim_ids": sorted(x["claim_id"] for x in c.publishable)}

    def _category_summary(self):
        p = self._policy
        return {"category": self.category, "category_identifier": p.category_identifier,
                "title_hard_limit": p.title_hard_limit,
                "backend_byte_ceiling": p.backend_byte_ceiling,
                "fallback_used": p.fallback_used, "warning_state": p.warning_state,
                "policy_source": p.policy_source}

    def _requirement_counts(self):
        # counts cover every requirement recorded in OWNER-INPUT (open + not-inferrable + resolved).
        reqs = self.all_owner_requirements + self.resolved_requirements
        return {"blocking": sum(1 for r in reqs if r["priority"] == PRIORITY_BLOCKING),
                "important": sum(1 for r in reqs if r["priority"] == PRIORITY_IMPORTANT),
                "optional": sum(1 for r in reqs if r["priority"] == PRIORITY_OPTIONAL),
                "resolved": len(self.resolved_requirements),
                "real_assets_required": sum(1 for a in self.real_asset_requirements
                                            if a["state"] == ASSET_REQUIRED)}

    def generated_artifacts(self):
        return ["/".join(PHASE6_SUBPATH) + "/" + n for n in
                (PRODUCT_WORKSPACE_FILENAME, OWNER_INPUT_FILENAME, PRODUCT_READINESS_FILENAME,
                 NORMALIZED_FACTS_FILENAME, CLAIM_EVIDENCE_FILENAME, STAGE_MANIFEST_FILENAME)]

    # -- canonical serialization -----------------------------------------------------------------
    def canonical_content(self):
        """Everything except approved volatile metadata — identical input yields identical content."""
        return {
            "schema_version": self.schema_version,
            "workspace_id": self.workspace_id,
            "product_id": self.product_id,
            "product_type": self.product_type,
            "category": self.category,
            "category_policy": self._category_summary(),
            "primary_keyword": self.primary_keyword,
            "audience": self.audience,
            "workspace_state": self.workspace_state,
            "current_listing_publishability_state": self.current_listing_publishability_state,
            "downstream_readiness": self.downstream_readiness,
            "source_files": sorted(self.source_files),
            "source_hashes": self.source_hashes,
            "keyword_source_sha256": self.keyword_source_sha256,
            "keyword_tier_counts": {k: v for k, v in self._src.tier_counts.items()} if self._src else {},
            "product_facts_sha256": self.product_facts_sha256,
            "product_facts_source_sha256": self.normalized_product_facts.source_sha256,
            "claim_evidence_sha256": self.claim_evidence_sha256,
            "normalized_product_facts": self._facts_summary(),
            "claim_evidence": self._claims_summary(),
            "owner_input_requirements": self.owner_input_requirements,
            "not_inferrable_requirements": self.not_inferrable_requirements,
            "resolved_requirements": self.resolved_requirements,
            "real_asset_requirements": self.real_asset_requirements,
            "requirement_counts": self._requirement_counts(),
            "blockers": sorted(self.blockers),
            "warnings": sorted(self.warnings),
            "generated_artifacts": self.generated_artifacts(),
        }

    def deterministic_content_sha256(self):
        return content_sha256(self.canonical_content())

    def to_dict(self, generated_at=None):
        doc = self.canonical_content()
        doc["deterministic_content_sha256"] = self.deterministic_content_sha256()
        if generated_at:
            doc["generated_at"] = generated_at    # the only volatile field; excluded from the hash
        return doc


# ---------------------------------------------------------------- product identification
def _load_manifest(workspace_dir):
    p = os.path.join(workspace_dir, "PROJECT-MANIFEST.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _has_keyword_source(folder):
    for fname, _schema, mode in KSA.SOURCE_PRIORITY:
        if mode == KSA.MODE_LEGACY_UNSAFE:
            continue                     # legacy alone is never an approved source
        if os.path.exists(os.path.join(folder, fname)):
            return True
    return False


def select_product_workspace(runs_root, workspace_id=None):
    """Resolve the ONE selected product workspace under *runs_root* from explicit evidence.

    With an explicit *workspace_id* the folder must exist. Otherwise a single candidate (a run
    directory that carries an approved keyword source) is selected; multiple candidates raise
    AmbiguousProductError (OWNER_SELECTION_REQUIRED) — Phase 6A never guesses by filename similarity.
    """
    if workspace_id:
        wd = os.path.join(runs_root, workspace_id)
        if not os.path.isdir(wd):
            raise ProductWorkspaceError(f"selected product workspace not found: {workspace_id}")
        return wd
    if not os.path.isdir(runs_root):
        raise ProductWorkspaceError(f"runs root not found: {runs_root}")
    candidates = [name for name in sorted(os.listdir(runs_root))
                  if os.path.isdir(os.path.join(runs_root, name))
                  and not name.startswith((".", "_"))
                  and _has_keyword_source(os.path.join(runs_root, name))]
    if not candidates:
        raise ProductWorkspaceError(f"no product workspace with an approved keyword source in {runs_root}")
    if len(candidates) > 1:
        raise AmbiguousProductError(runs_root, candidates)
    return os.path.join(runs_root, candidates[0])


def _current_listing_publishability(workspace_dir):
    """Reuse the recorded Phase 5 verdict — never recompute a listing here. First present wins."""
    for name in ("LAST-PUBLISHABLE-LISTING-METADATA.json", "LAST-SAFE-DRAFT-METADATA.json",
                 "LAST-VALID-LISTING-METADATA.json", "listing.json"):
        p = os.path.join(workspace_dir, name)
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        state = doc.get("publishability")
        if state in LISTING_PUBLISHABILITY_STATES[:3]:
            return state
    return LISTING_NOT_YET_GENERATED


# ---------------------------------------------------------------- owner-input + real-asset derivation
def _worst_state(facts, fields):
    """The effective state across a canonical field and its synonyms: publishable if ANY is verified."""
    states = [facts.state(f) for f in fields]
    for good in (PFL.VERIFIED, PFL.OWNER_CONFIRMED):
        if good in states:
            return good
    for bad in (PFL.BLOCKED, PFL.OWNER_REVIEW_REQUIRED):
        if bad in states:
            return bad
    return PFL.UNKNOWN


def _build_owner_requirements(facts, claims):
    """Owner-fact requirements consolidated by underlying fact (never duplicated).

    Returns (open_requirements, resolved_requirements). A fact that is already VERIFIED / OWNER_CONFIRMED
    is separated into resolved_requirements (RESOLVED); an unverified fact that backs at least one claim
    concept becomes an OPEN requirement. Synonym fields (material / material_composition) fold into one
    canonical question so the owner is never asked twice for the same underlying fact.
    """
    open_reqs, resolved_reqs = [], []
    idx = 0
    for fld in sorted(_FACT_META):
        meta = _FACT_META[fld]
        fields = sorted({fld, *meta.get("also", ())})
        concepts = sorted({c for f in fields for c in _FIELD_TO_CONCEPTS.get(f, ())})
        if not concepts:
            continue                                  # this fact backs no claim -> not an owner question
        state = _worst_state(facts, fields)
        idx += 1
        base = {
            "priority": meta["priority"],
            "fact_key": meta["key_label"],
            "fact_fields": fields,
            "question": meta["question"],
            "reason": meta["reason"],
            "current_state": state,
            "accepted_evidence": list(meta["accepted"]),
            "not_accepted_as_proof": list(NOT_ACCEPTED_AS_PROOF),
            "downstream_impact": concepts,
            "blocking_stage": meta["copy_stage"],
            "owner_response_type": "TEXT_OR_DOCUMENT",
        }
        if state in PFL.PUBLISHABLE_STATES:
            resolved_reqs.append(dict(base, requirement_id=f"OWNER-FACT-R{idx:03d}", status=REQ_RESOLVED))
        else:
            open_reqs.append(dict(base, requirement_id=f"OWNER-FACT-{idx:03d}", status=REQ_OPEN))
    return open_reqs, resolved_reqs


def _build_not_inferrable_requirements(claims):
    """Claims that are structurally never inferred — explicit owner attestation, no fact field."""
    out = []
    for i, concept in enumerate(sorted(_NOT_INFERRABLE), start=1):
        c = claims.claim(concept)
        if c is None or c["verification_state"] == CE.VERIFIED:
            continue
        meta = _NOT_INFERRABLE[concept]
        out.append({
            "requirement_id": f"OWNER-CLAIM-{i:03d}",
            "priority": meta["priority"],
            "claim_concept": concept,
            "claim_id": c["claim_id"],
            "question": f"Do you want to claim {meta['label']}? If so, provide explicit evidence — "
                        "this claim is never inferred from another fact.",
            "reason": "Structurally blocked from inference; it requires explicit, evidence-backed owner "
                      "attestation before it can be published.",
            "current_state": c["verification_state"],
            "accepted_evidence": ["owner attestation with supporting evidence (spec, test, or real sample)"],
            "not_accepted_as_proof": list(NOT_ACCEPTED_AS_PROOF),
            "downstream_impact": [concept],
            "blocking_stage": "6C",
            "owner_response_type": "TEXT_OR_DOCUMENT",
            "status": REQ_OPEN,
        })
    return out


def _concept_verified(claims, concept):
    c = claims.claim(concept)
    return bool(c and c["verification_state"] == CE.VERIFIED)


def _build_real_asset_requirements(claims, verified_assets=None):
    """Physical-proof requirements. A requirement is raised only when a backing concept is unverified;
    an already-verified real asset (passed in via *verified_assets*) suppresses a duplicate request."""
    verified = {str(a).strip().upper() for a in (verified_assets or [])}
    out = []
    for spec in _REAL_ASSET_SPECS:
        unverified_concepts = [c for c in spec["concepts"] if not _concept_verified(claims, c)]
        primary_proven = _concept_verified(claims, spec["concepts"][0])
        if primary_proven:
            state = ASSET_NOT_APPLICABLE       # the asset's primary purpose is already proven
        elif spec["rid"] in verified:
            state = ASSET_VERIFIED             # owner already supplied a verified real asset
        else:
            state = ASSET_REQUIRED
        out.append({
            "asset_requirement_id": spec["rid"],
            "priority": spec["priority"],
            "purpose": spec["purpose"],
            "supports_concepts": list(spec["concepts"]),
            "unverified_concepts": unverified_concepts,
            "accepted_file_types": list(_ASSET_FILE_TYPES),
            "required_viewpoint": spec["viewpoint"],
            "ai_generated_prohibited_as_proof": True,
            "downstream_stages_blocked": list(spec["blocks"]),
            "state": state,
        })
    return out


# ---------------------------------------------------------------- readiness
def _evaluate_readiness(*, category_policy, product_type, keyword_source, facts, claims,
                        owner_reqs, not_inferrable_reqs, real_assets):
    """Compute (workspace_state, blockers, warnings, downstream_readiness).

    Readiness for 6B (keyword allocation) is separate from listing publishability. 6B is blocked ONLY
    when missing/conflicting information prevents reliable product-relevance decisions. Missing owner
    facts and missing real assets do NOT block 6B — they gate safe publishable COPY at 6C+.

    State mapping (first match wins):
      INVALID_SOURCE       — no authoritative keyword source / no allocatable keywords
      BLOCKED_UNSAFE       — a prohibited claim, or brand/IP contamination in the eligible set
      CONFLICTING_EVIDENCE — unknown category, unknown product type, or a BLOCKED/contradictory fact
      OWNER_FACTS_AND_REAL_ASSETS_REQUIRED / OWNER_FACTS_REQUIRED / REAL_ASSETS_REQUIRED / READY_FOR_6B
    """
    warnings = []
    category_identified = not category_policy.fallback_used
    product_type_known = bool(product_type)
    has_kw = bool(keyword_source) and bool(keyword_source.eligible_keywords())

    invalid_reasons, unsafe_reasons, conflict_reasons = [], [], []

    if not has_kw:
        invalid_reasons.append("INVALID_SOURCE: no authoritative keyword source with allocatable keywords")
    if claims.prohibited_count:
        unsafe_reasons.append(f"BLOCKED_UNSAFE: {claims.prohibited_count} PROHIBITED claim(s)")
    contaminated = _eligible_ip_contamination(keyword_source) if keyword_source else []
    if contaminated:
        unsafe_reasons.append(f"BLOCKED_UNSAFE: {len(contaminated)} eligible keyword(s) carry a brand/IP risk")
    if not category_identified:
        conflict_reasons.append("UNKNOWN_CATEGORY: category policy fell back (POLICY_VERIFICATION_REQUIRED)")
    if not product_type_known:
        conflict_reasons.append("UNKNOWN_PRODUCT_TYPE: no garment noun in the approved primary keyword and "
                                "no verified garment fact")
    if facts.blocked_fact_count:
        conflict_reasons.append(f"CONFLICTING_EVIDENCE: {facts.blocked_fact_count} BLOCKED product fact(s) "
                                "(e.g. a contradictory/blocked decoration method)")

    # Only BLOCKING gaps change the readiness STATE. IMPORTANT/OPTIONAL owner facts and non-blocking
    # assets are recorded but do not push the product out of readiness (they gate specific copy fields,
    # not the workspace). This is what lets a product be READY_FOR_6B while its listing is a safe draft.
    facts_needed = any(r["priority"] == PRIORITY_BLOCKING
                       for r in list(owner_reqs) + list(not_inferrable_reqs))
    assets_needed = any(a["state"] == ASSET_REQUIRED and a["priority"] == PRIORITY_BLOCKING
                        for a in real_assets)

    if invalid_reasons:
        state, ready, blockers = INVALID_SOURCE, False, invalid_reasons
    elif unsafe_reasons:
        state, ready, blockers = BLOCKED_UNSAFE, False, unsafe_reasons
    elif conflict_reasons:
        state, ready, blockers = CONFLICTING_EVIDENCE, False, conflict_reasons
    else:
        ready, blockers = True, []
        if facts_needed and assets_needed:
            state = OWNER_FACTS_AND_REAL_ASSETS_REQUIRED
        elif facts_needed:
            state = OWNER_FACTS_REQUIRED
        elif assets_needed:
            state = REAL_ASSETS_REQUIRED
        else:
            state = READY_FOR_6B

    downstream = {
        "ready_for_6b": ready,
        "category_identified": category_identified,
        "product_type_known": product_type_known,
        "authoritative_keyword_source": has_kw,
        "eligible_keyword_count": len(keyword_source.eligible_keywords()) if keyword_source else 0,
        "blocking_reasons": sorted(invalid_reasons + unsafe_reasons + conflict_reasons),
        "note": ("Keyword allocation (6B) may proceed; missing owner facts and real assets gate safe "
                 "publishable COPY (6C+), not relevance." if ready else
                 "Keyword allocation (6B) is blocked until the blocking reasons are resolved."),
    }
    return state, blockers, warnings, downstream


def _eligible_ip_contamination(keyword_source):
    """Eligible keywords that still carry a brand/IP/trademark risk flag (should be empty)."""
    bad = []
    for r in keyword_source.eligible_keywords():
        flags = {str(f).upper() for f in r.get("risk_flags", [])} | \
                {str(f).upper() for f in r.get("exclusion_reasons", [])}
        if any(tok in f for f in flags for tok in ("TRADEMARK", "BRAND", "IP_RISK")):
            bad.append(r.get("keyword_exact"))
    return bad


# ---------------------------------------------------------------- build
def build_product_workspace(workspace_dir, *, workspace_id=None, category=None,
                            allow_legacy_unsafe=False, verified_assets=None):
    """Assemble the Phase 6A product workspace for ONE selected product, reusing Phase 5 authorities.

    Returns a ProductWorkspaceResult. Raises ProductWorkspaceError on an unusable workspace directory;
    a missing/malformed keyword source is captured as an INVALID_SOURCE state (a typed result), not a
    crash, so blockers can be surfaced safely.
    """
    if not os.path.isdir(workspace_dir):
        raise ProductWorkspaceError(f"workspace directory not found: {workspace_dir}")

    manifest = _load_manifest(workspace_dir)
    product_id = (manifest.get("project_id") or workspace_id
                  or os.path.basename(os.path.normpath(workspace_dir)))
    wid = workspace_id or product_id
    warnings = []

    # -- keyword source (authoritative). A source failure is a typed INVALID_SOURCE result. ---------
    try:
        src = KSA.load_keyword_source(workspace_dir, allow_legacy_unsafe=allow_legacy_unsafe)
    except KSA.KeywordSourceError as e:
        return _invalid_source_result(wid, product_id, workspace_dir, manifest, str(e))

    kws = src.eligible_phrases()
    primary = kws[0] if kws else None
    audience = LG._audience_from_keywords(kws) if kws else ""
    product_type = None
    if primary:
        product_type = LG.garment_from_keyword(primary)

    # -- category policy ---------------------------------------------------------------------------
    cat_in = category or _category_from_workspace(workspace_dir) or DEFAULT_CATEGORY
    policy = CPR.resolve_category_policy(cat_in)
    # a verified garment_type fact overrides the keyword-read product type.
    facts = PFL.load_product_facts(folder=workspace_dir)
    verified_garment = facts.publishable_value("garment_type") or facts.publishable_value("product_type")
    if verified_garment:
        product_type = verified_garment
    if facts.source_sha256 is None:
        warnings.append("no product-facts.json — every factual claim is UNVERIFIED_BLOCKED until the "
                        "owner supplies verified facts")

    # -- claim evidence (reuse the Phase 5 engine; audience keyword-context only) -------------------
    keyword_context = {"audience": audience} if audience else {}
    claims = CE.build_claim_evidence(facts, keyword_context=keyword_context)

    owner_reqs, resolved_reqs = _build_owner_requirements(facts, claims)
    not_inf_reqs = _build_not_inferrable_requirements(claims)
    real_assets = _build_real_asset_requirements(claims, verified_assets=verified_assets)

    state, blockers, more_warnings, downstream = _evaluate_readiness(
        category_policy=policy, product_type=product_type, keyword_source=src, facts=facts,
        claims=claims, owner_reqs=owner_reqs, not_inferrable_reqs=not_inf_reqs,
        real_assets=real_assets)
    warnings.extend(more_warnings)
    warnings.extend(src.warnings)

    listing_pub = _current_listing_publishability(workspace_dir)

    source_files, source_hashes = _source_provenance(workspace_dir, src, facts)

    return ProductWorkspaceResult(
        workspace_id=wid, product_id=product_id, product_type=product_type, category=cat_in,
        category_policy=policy, primary_keyword=primary, audience=audience, keyword_source=src,
        normalized_facts=facts, claim_evidence=claims, owner_input_requirements=owner_reqs,
        not_inferrable_requirements=not_inf_reqs, resolved_requirements=resolved_reqs,
        real_asset_requirements=real_assets,
        blockers=blockers, warnings=warnings, workspace_state=state,
        current_listing_publishability_state=listing_pub, downstream_readiness=downstream,
        source_files=source_files, source_hashes=source_hashes)


create_product_workspace = build_product_workspace


def _category_from_workspace(workspace_dir):
    """Read an explicit category from creative-brief.json if present (never guessed)."""
    p = os.path.join(workspace_dir, "creative-brief.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
        return doc.get("category") if isinstance(doc, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _source_provenance(workspace_dir, src, facts):
    """The immutable inputs Phase 6A consumed, with canonical hashes (relative names only)."""
    files, hashes = [], {}
    if src and src.source_file:
        files.append(src.source_file)
        hashes[src.source_file] = src.source_sha256
    if facts.source_sha256 is not None:
        files.append(facts.source_file)
        hashes[facts.source_file] = facts.source_sha256
    return sorted(set(files)), hashes


def _invalid_source_result(wid, product_id, workspace_dir, manifest, reason):
    facts = PFL.load_product_facts(folder=workspace_dir)
    claims = CE.build_claim_evidence(facts)
    policy = CPR.resolve_category_policy(DEFAULT_CATEGORY)
    downstream = {"ready_for_6b": False, "category_identified": not policy.fallback_used,
                  "product_type_known": False, "authoritative_keyword_source": False,
                  "eligible_keyword_count": 0,
                  "blocking_reasons": ["INVALID_SOURCE: " + reason],
                  "note": "Keyword allocation (6B) is blocked: no usable authoritative keyword source."}
    return ProductWorkspaceResult(
        workspace_id=wid, product_id=product_id, product_type=None, category=DEFAULT_CATEGORY,
        category_policy=policy, primary_keyword=None, audience="", keyword_source=None,
        normalized_facts=facts, claim_evidence=claims, owner_input_requirements=[],
        not_inferrable_requirements=[], real_asset_requirements=[],
        blockers=["INVALID_SOURCE: " + reason], warnings=[], workspace_state=INVALID_SOURCE,
        current_listing_publishability_state=_current_listing_publishability(workspace_dir),
        downstream_readiness=downstream, source_files=[], source_hashes={})


def evaluate_product_readiness(result):
    """Expose the readiness verdict for a built result (already computed during build)."""
    return dict(result.downstream_readiness, workspace_state=result.workspace_state)


# ---------------------------------------------------------------- artifact bodies
def _owner_input_doc(result):
    open_reqs = result.all_owner_requirements                     # open fact + not-inferrable claim reqs
    resolved = result.resolved_requirements
    counts = result._requirement_counts()
    body = {
        "schema_version": OWNER_INPUT_SCHEMA_VERSION,
        "workspace_id": result.workspace_id,
        "product_id": result.product_id,
        "phase6a_workspace_state": result.workspace_state,
        "current_listing_publishability_state": result.current_listing_publishability_state,
        "requirements": sorted(open_reqs, key=lambda r: (PRIORITY_RANK[r["priority"]],
                                                         r["requirement_id"])),
        "real_asset_requirements": result.real_asset_requirements,
        "resolved_requirements": resolved,
        "summary": counts,
        "source_hashes": result.source_hashes,
    }
    body["content_sha256"] = content_sha256(body)
    return body


def _normalized_facts_doc(result):
    return result.normalized_product_facts.to_dict()      # no volatile stamp -> deterministic bytes


def _claim_evidence_doc(result):
    return result.claim_evidence.to_dict()                # no volatile stamp -> deterministic bytes


def _readiness_report_md(result):
    r = result
    f = r.normalized_product_facts
    c = r.claim_evidence
    counts = r._requirement_counts()

    def _fact_line(field):
        rec = f.get(field)
        val = rec["value"]
        return f"- `{field}` — **{rec['state']}**" + (f": {val}" if val else "")

    verified_facts = sorted(fld for fld, rec in f.facts.items()
                            if rec["state"] in PFL.PUBLISHABLE_STATES)
    unknown_facts = f.owner_fact_required
    conflicting_facts = sorted(fld for fld, rec in f.facts.items() if rec["state"] == PFL.BLOCKED)
    supported = sorted(x["claim_id"] + " — " + (x["proposed_text"] or "") for x in c.publishable)
    blocked = sorted(x["claim_id"] for x in c.by_state(CE.UNVERIFIED_BLOCKED))
    prohibited = sorted(x["claim_id"] for x in c.by_state(CE.PROHIBITED))

    lines = [
        f"# Product Readiness Report — {r.product_id}",
        "",
        "> Phase 6A establishes product readiness. It is NOT the same as listing publishability. "
        "A product can be ready for keyword allocation (6B) while its listing is still a safe draft.",
        "",
        "## 1. Product identity",
        f"- Workspace: `{r.workspace_id}`",
        f"- Product id: `{r.product_id}`",
        f"- Product type: `{r.product_type or 'UNKNOWN'}`  (read from the approved primary keyword; not fabricated)",
        f"- Category: `{r._policy.category_identifier}`  (policy fallback used: {r._policy.fallback_used})",
        f"- Primary keyword: `{r.primary_keyword or 'none'}`",
        f"- Audience (from approved keywords): `{r.audience or 'none'}`",
        f"- Keyword source: `{f_or_none(r._src, 'source_file')}` · sha256 `{short(r.keyword_source_sha256)}`",
        f"- Keyword tiers: TIER_A={tier(r._src,'TIER_A')} · TIER_B={tier(r._src,'TIER_B')}",
        "",
        "## 2. Current Phase 6A state",
        f"- **{r.workspace_state}**",
        f"- Ready for 6B (keyword allocation): **{r.downstream_readiness['ready_for_6b']}**",
        "",
        "## 3. Current listing publishability state",
        f"- **{r.current_listing_publishability_state}** (recorded Phase 5 verdict — not recomputed here)",
        "",
        "## 4. Verified facts",
    ] + ([_fact_line(x) for x in verified_facts] or ["- none — no product-facts.json supplied yet"]) + [
        "",
        "## 5. Unknown facts",
    ] + ([f"- `{x}`" for x in unknown_facts] or ["- none"]) + [
        "",
        "## 6. Conflicting or rejected facts",
    ] + ([f"- `{x}` — BLOCKED" for x in conflicting_facts] or ["- none"]) + [
        "",
        "## 7. Supported (publishable) claims",
    ] + ([f"- {x}" for x in supported] or ["- none — no verified physical facts yet"]) + [
        "",
        "## 8. Blocked claims",
        f"- {len(blocked)} unverified-blocked; {len(prohibited)} prohibited",
    ] + [f"  - `{x}` (blocked)" for x in blocked] + [f"  - `{x}` (prohibited)" for x in prohibited] + [
        "",
        "## 9. Owner information required",
        f"- Blocking: {counts['blocking']} · Important: {counts['important']} · Optional: {counts['optional']}",
    ] + [f"  - [{req['priority']}] `{req.get('fact_key', req.get('claim_concept'))}` — {req['question']}"
         for req in sorted(r.all_owner_requirements,
                           key=lambda q: (PRIORITY_RANK[q["priority"]], q["requirement_id"]))] + [
        "",
        "## 10. Real assets required",
        f"- {counts['real_assets_required']} real-asset requirement(s) (AI mockups are NOT accepted as proof)",
    ] + [f"  - [{a['priority']}] `{a['asset_requirement_id']}` — {a['purpose']} ({a['state']})"
         for a in r.real_asset_requirements if a["state"] == ASSET_REQUIRED] + [
        "",
        "## 11. What may proceed now",
        ("- Keyword allocation (Phase 6B) may proceed from the authoritative keyword source; product "
         "relevance is clear." if r.downstream_readiness["ready_for_6b"]
         else "- Nothing downstream may proceed until the blockers below are resolved."),
        "",
        "## 12. What remains blocked",
        ("- Safe publishable COPY (Phase 6C+): every physical claim stays blocked until the owner facts "
         "and real assets below are supplied." if r.downstream_readiness["ready_for_6b"]
         else "- " + "; ".join(sorted(r.blockers)) if r.blockers else "- none"),
        "",
        "## 13. Recommended next owner actions",
    ] + _recommended_actions(r) + [
        "",
        "## 14. Phase 6B readiness",
        f"- ready_for_6b: **{r.downstream_readiness['ready_for_6b']}**",
        f"- {r.downstream_readiness['note']}",
        "",
        "## 15. Safety statement",
        "- Phase 6A ran locally and offline. No Amazon or Seller Central connection was made, no external "
        "AI was used, no outbound network request was performed, and no listing was published. The owner "
        "remains the only manual bridge to Seller Central.",
        "- Distinctions preserved: a FACT is a verified product attribute; a CLAIM is copy backed by a "
        "fact; an ASSUMPTION is never published; missing owner input and missing real proof both keep "
        "affected copy as a SAFE DRAFT, never publishable, until resolved.",
        "",
        f"_workspace content sha256: {r.deterministic_content_sha256()}_",
        f"_facts content sha256: {f.content_sha256()} · claims content sha256: {c.content_sha256()}_",
    ]
    return "\n".join(lines) + "\n"


def _recommended_actions(result):
    blocking = [r for r in result.all_owner_requirements if r["priority"] == PRIORITY_BLOCKING]
    out = []
    for i, req in enumerate(sorted(blocking, key=lambda q: q["requirement_id"])[:6], start=1):
        out.append(f"{i}. Provide **{req.get('fact_key', req.get('claim_concept'))}** — "
                   f"accepted: {', '.join(req['accepted_evidence'][:2])}.")
    assets = [a for a in result.real_asset_requirements
              if a["state"] == ASSET_REQUIRED and a["priority"] == PRIORITY_BLOCKING]
    for a in assets[:3]:
        out.append(f"{len(out)+1}. Supply real asset **{a['asset_requirement_id']}** — {a['required_viewpoint']}.")
    return out or ["1. No blocking owner actions — the product is ready for keyword allocation."]


# tiny report helpers (kept local; no fabricated values)
def f_or_none(obj, attr):
    return getattr(obj, attr, None) if obj else None


def short(h):
    return (h[:16] + "…") if h else "none"


def tier(src, t):
    return src.tier_counts.get(t, 0) if src else 0


# ---------------------------------------------------------------- manifest
_MANIFEST_VOLATILE = ("deterministic_content_sha256", "started_at", "completed_at",
                      "previous_valid_manifest_sha256", "verification")


def _manifest_hashable(body):
    return {k: v for k, v in body.items() if k not in _MANIFEST_VOLATILE}


def _stage_manifest_doc(result, dest_dir, workspace_dir, output_hashes, *, started_at=None,
                        completed_at=None, previous_valid_manifest_sha256=None, verification=None,
                        errors=None):
    required_inputs = sorted(result.source_files)
    counts = result._requirement_counts()
    body = {
        "schema_version": STAGE_MANIFEST_SCHEMA_VERSION,
        "stage_id": STAGE_ID,
        "stage_name": STAGE_NAME,
        "workspace_id": result.workspace_id,
        "product_id": result.product_id,
        "state": result.workspace_state,
        "listing_publishability_state": result.current_listing_publishability_state,
        "required_inputs": required_inputs,
        "input_hashes": result.source_hashes,
        "generated_outputs": sorted(output_hashes),
        "output_hashes": output_hashes,
        "owner_fact_requirement_count": len(result.all_owner_requirements),
        "real_asset_requirement_count": sum(1 for a in result.real_asset_requirements
                                            if a["state"] == ASSET_REQUIRED),
        "blocker_count": len(result.blockers),
        "warning_count": len(result.warnings),
        "requirement_counts": counts,
        "deterministic_content_sha256": None,     # filled below (excludes volatile timestamps)
        "previous_valid_manifest_sha256": previous_valid_manifest_sha256,
        "verification": verification or {},
        "errors": list(errors or []),
    }
    # the deterministic hash covers content EXCLUDING volatile lineage/timestamps and its own self-hash,
    # so identical inputs reproduce it even when the previous-manifest lineage or run timestamps differ.
    body["deterministic_content_sha256"] = content_sha256(_manifest_hashable(body))
    if started_at:
        body["started_at"] = started_at
    if completed_at:
        body["completed_at"] = completed_at
    return body


# ---------------------------------------------------------------- write / verify
def write_phase6a_artifacts(result, dest_dir=None, *, workspace_dir=None, generated_at=None,
                            started_at=None, completed_at=None):
    """Write the four required artifacts + support artifacts atomically and deterministically.

    Every artifact is validated in-memory BEFORE it is written; a validation failure raises and leaves
    the previous valid artifacts untouched (the atomic replace never runs on an invalid document).
    Returns the STAGE-6A manifest dict.
    """
    workspace_dir = workspace_dir or _infer_workspace_dir(result, dest_dir)
    dest_dir = dest_dir or phase6a_dir(workspace_dir)

    # -- phase 1: build every document + its exact final bytes IN MEMORY (write nothing yet) -------
    ws_doc = result.to_dict(generated_at=generated_at)
    owner_doc = _owner_input_doc(result)
    facts_doc = _normalized_facts_doc(result)
    claims_doc = _claim_evidence_doc(result)
    report_md = _readiness_report_md(result)

    # content artifacts in a fixed order: (filename, exact-final-text).
    content = [
        (PRODUCT_WORKSPACE_FILENAME, canonical_json(ws_doc)),
        (OWNER_INPUT_FILENAME, canonical_json(owner_doc)),
        (NORMALIZED_FACTS_FILENAME, canonical_json(facts_doc)),
        (CLAIM_EVIDENCE_FILENAME, canonical_json(claims_doc)),
        (PRODUCT_READINESS_FILENAME, report_md),
    ]
    # output hashes == sha256 of the EXACT bytes we will write (verify re-reads the files to confirm).
    output_hashes = {}
    for name, text in content:
        rel = _safe_rel(os.path.join(dest_dir, name), workspace_dir)
        output_hashes[rel] = hashlib.sha256(text.encode("utf-8")).hexdigest()

    prev = _previous_manifest_hash(dest_dir)
    manifest = _stage_manifest_doc(result, dest_dir, workspace_dir, output_hashes,
                                   started_at=started_at, completed_at=completed_at,
                                   previous_valid_manifest_sha256=prev)

    # -- phase 2: validate EVERYTHING before touching disk. A failure preserves the last valid run. -
    _raise_on_errors("PRODUCT-WORKSPACE", validate_product_workspace(ws_doc))
    _raise_on_errors("OWNER-INPUT", validate_owner_input(owner_doc))
    _raise_on_errors("NORMALIZED-PRODUCT-FACTS", validate_normalized_facts(facts_doc))
    _raise_on_errors("CLAIM-EVIDENCE", validate_claim_evidence(claims_doc))
    _raise_on_errors("STAGE-6A-MANIFEST", validate_manifest(manifest, workspace_dir))

    # -- phase 3: commit. Each file is written atomically (temp + fsync + os.replace). -------------
    os.makedirs(dest_dir, exist_ok=True)
    for name, text in content:
        _atomic_write_text(os.path.join(dest_dir, name), text)
    _atomic_write_json(os.path.join(dest_dir, STAGE_MANIFEST_FILENAME), manifest)
    return manifest


def _infer_workspace_dir(result, dest_dir):
    if dest_dir:
        # dest_dir is <workspace>/phase6/6A -> workspace is two levels up.
        return os.path.dirname(os.path.dirname(os.path.abspath(dest_dir)))
    raise ProductWorkspaceError("write_phase6a_artifacts needs workspace_dir or dest_dir")


def _previous_manifest_hash(dest_dir):
    p = os.path.join(dest_dir, STAGE_MANIFEST_FILENAME)
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f).get("deterministic_content_sha256")
    except (OSError, json.JSONDecodeError):
        return None


def load_product_workspace(dest_dir):
    """Read the written PRODUCT-WORKSPACE.json back."""
    p = os.path.join(dest_dir, PRODUCT_WORKSPACE_FILENAME)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def verify_phase6a_artifacts(dest_dir, workspace_dir=None):
    """Re-read the written artifacts and verify manifest hashes match the final bytes, no absolute
    paths / traversal / duplicates, and every required artifact is present. Returns a verdict dict."""
    workspace_dir = workspace_dir or os.path.dirname(os.path.dirname(os.path.abspath(dest_dir)))
    errors = []
    for name in REQUIRED_ARTIFACTS:
        if not os.path.exists(os.path.join(dest_dir, name)):
            errors.append(f"missing required artifact: {name}")
    man_path = os.path.join(dest_dir, STAGE_MANIFEST_FILENAME)
    if not os.path.exists(man_path):
        errors.append("missing STAGE-6A-MANIFEST.json")
        return {"ok": False, "errors": errors, "checked": []}
    with open(man_path, encoding="utf-8") as f:
        manifest = json.load(f)
    errors.extend(validate_manifest(manifest, workspace_dir))
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
    return {"ok": not errors, "errors": errors, "checked": sorted(checked)}


# ---------------------------------------------------------------- validators
def _raise_on_errors(label, errors):
    if errors:
        raise ProductWorkspaceError(f"{label} failed validation: " + "; ".join(errors))


def validate_product_workspace(doc):
    errors = []
    if doc.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if not doc.get("workspace_id"):
        errors.append("empty workspace_id")
    if not doc.get("product_id"):
        errors.append("ambiguous/empty product_id")
    if doc.get("workspace_state") not in WORKSPACE_STATES:
        errors.append(f"invalid workspace_state {doc.get('workspace_state')!r}")
    if doc.get("current_listing_publishability_state") not in LISTING_PUBLISHABILITY_STATES:
        errors.append("invalid current_listing_publishability_state")
    for name, h in (doc.get("source_hashes") or {}).items():
        if os.path.isabs(name) or name.replace(os.sep, "/").startswith("../") or ".." in name.split("/"):
            errors.append(f"source path not normalized/relative: {name}")
    recomputed = content_sha256({k: v for k, v in doc.items()
                                 if k not in ("deterministic_content_sha256", "generated_at")})
    if doc.get("deterministic_content_sha256") != recomputed:
        errors.append("deterministic_content_sha256 does not recompute")
    return errors


def validate_owner_input(doc):
    errors = []
    if doc.get("schema_version") != OWNER_INPUT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {OWNER_INPUT_SCHEMA_VERSION}")
    ids, fact_keys = set(), {}
    all_reqs = list(doc.get("requirements", [])) + list(doc.get("resolved_requirements", []))
    for req in all_reqs:
        rid = req.get("requirement_id")
        if not rid or rid in ids:
            errors.append(f"duplicate/empty requirement_id: {rid}")
        ids.add(rid)
        if req.get("priority") not in PRIORITY_RANK:
            errors.append(f"invalid priority for {rid}")
        if req.get("status") not in (REQ_OPEN, REQ_RESOLVED, REQ_NOT_APPLICABLE, REQ_REJECTED_UNSAFE):
            errors.append(f"invalid status for {rid}")
        if not req.get("question"):
            errors.append(f"missing question for {rid}")
        if not req.get("reason"):
            errors.append(f"missing reason for {rid}")
        if not req.get("downstream_impact"):
            errors.append(f"missing downstream_impact for {rid}")
        if not req.get("accepted_evidence"):
            errors.append(f"missing accepted_evidence for {rid}")
        key = req.get("fact_key")
        if req.get("status") == REQ_OPEN and key:
            if key in fact_keys:
                errors.append(f"duplicate unresolved fact_key without justification: {key}")
            fact_keys[key] = rid
    counts = doc.get("summary", {})
    open_reqs = doc.get("requirements", [])
    for prio, field in ((PRIORITY_BLOCKING, "blocking"), (PRIORITY_IMPORTANT, "important"),
                        (PRIORITY_OPTIONAL, "optional")):
        actual = sum(1 for r in (open_reqs + doc.get("resolved_requirements", []))
                     if r.get("priority") == prio)
        if counts.get(field) != actual:
            errors.append(f"summary.{field} ({counts.get(field)}) != records ({actual})")
    if doc.get("content_sha256") != content_sha256({k: v for k, v in doc.items()
                                                    if k != "content_sha256"}):
        errors.append("content_sha256 does not recompute")
    return errors


def validate_normalized_facts(doc):
    errors = []
    if doc.get("schema_version") != PFL.NORMALIZED_SCHEMA_VERSION:
        errors.append("normalized facts came from a foreign authority (schema_version mismatch)")
    facts = doc.get("facts", {})
    valid_states = (PFL.VERIFIED, PFL.OWNER_CONFIRMED, PFL.OWNER_REVIEW_REQUIRED, PFL.UNKNOWN,
                    PFL.BLOCKED)
    seen = set()
    for fld, rec in facts.items():
        if fld in seen:
            errors.append(f"duplicate fact field {fld}")
        seen.add(fld)
        if rec.get("state") not in valid_states:
            errors.append(f"invalid fact state for {fld}")
    body = {k: v for k, v in doc.items() if k not in ("content_sha256", "generated_at")}
    if doc.get("content_sha256") != hashlib.sha256(canonical_json(body).encode()).hexdigest():
        errors.append("normalized-facts content_sha256 does not recompute")
    return errors


def validate_claim_evidence(doc):
    errors = []
    if doc.get("schema_version") != CE.CLAIM_EVIDENCE_SCHEMA_VERSION:
        errors.append("claim evidence came from a foreign authority (schema_version mismatch)")
    claims = doc.get("claims", {})
    ids = set()
    valid_states = (CE.VERIFIED, CE.SUPPORTED_OWNER_REVIEW, CE.UNVERIFIED_BLOCKED, CE.PROHIBITED)
    for concept, rec in claims.items():
        cid = rec.get("claim_id")
        if not cid or cid in ids:
            errors.append(f"duplicate/empty claim_id for {concept}")
        ids.add(cid)
        if rec.get("verification_state") not in valid_states:
            errors.append(f"invalid claim state for {concept}")
        # a blocked/prohibited claim must never be labelled publishable.
        if rec.get("verification_state") != CE.VERIFIED and rec.get("publishable"):
            errors.append(f"blocked claim {cid} labelled publishable")
    body = {k: v for k, v in doc.items() if k not in ("content_sha256", "generated_at")}
    if doc.get("content_sha256") != hashlib.sha256(canonical_json(body).encode()).hexdigest():
        errors.append("claim-evidence content_sha256 does not recompute")
    return errors


def validate_manifest(doc, workspace_dir):
    errors = []
    if doc.get("schema_version") != STAGE_MANIFEST_SCHEMA_VERSION:
        errors.append("invalid manifest schema_version")
    if doc.get("stage_id") != STAGE_ID:
        errors.append("manifest stage_id must be 6A")
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
    for name in REQUIRED_ARTIFACTS:
        if name not in required_present:
            errors.append(f"manifest missing required artifact: {name}")
    # the manifest must not self-hash inside itself.
    if STAGE_MANIFEST_FILENAME in {r.split("/")[-1] for r in outputs}:
        errors.append("manifest must not list its own hash in output_hashes")
    if doc.get("deterministic_content_sha256") != content_sha256(_manifest_hashable(doc)):
        errors.append("manifest deterministic_content_sha256 does not recompute")
    return errors


# ---------------------------------------------------------------- runtime safety snapshot
def runtime_safety_snapshot(env=None):
    """A secret-free record that Phase 6A ran under the offline-only Phase 5 runtime policy."""
    policy = RP.load_runtime_policy(env=env)
    return {
        "offline_only": policy.offline_only,
        "external_ai_enabled": policy.external_ai_enabled,
        "outbound_network_enabled": policy.outbound_network_enabled,
        "bind_host": policy.bind_host,
        "loopback_only_effective": policy.loopback_only_effective,
        "policy_sha256": policy.policy_sha256,
        "is_valid": policy.is_valid,
    }
