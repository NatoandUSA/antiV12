#!/usr/bin/env python3
"""
listing.keyword_allocation_planner — the ONE Phase 6B keyword allocation authority.

Phase 6B selects the most relevant ELIGIBLE keywords deterministically and allocates them into the
exact frozen 13-field schema as ADVISORY planning metadata for the existing Phase 5 engines. It does
NOT generate listing copy, and allocation is never publication authorization.

It reuses — never re-implements — the mature Phase 5 / Phase 6A authorities:
  * keyword source + normalization + eligibility -> listing.keyword_source_adapter.load_keyword_source
  * category policy                              -> listing.category_policy_registry.resolve_category_policy
  * claim evidence / product facts (READ)        -> the completed Phase 6A CLAIM-EVIDENCE / NORMALIZED-FACTS
  * bullet 5-job taxonomy                         -> listing.bullet_engine.JOBS / JOB_CONCEPTS
  * canonical serialization + hashing            -> production.product_workspace.canonical_json / content_sha256
  * 6A dependency verification                    -> production.product_workspace.verify_phase6a_artifacts

The allocation is planning metadata only. No allocated keyword may bypass normalized product facts,
claim evidence, category policy, the field-specific Phase 5 engines, PageAuditor, or the safe-draft /
publishability controls. Missing owner facts still gate safe publishable COPY (6C+).

Deterministic proof: the ranking uses only the frozen local authoritative source. No connected
service, external AI, web, policy-watcher, USPTO, or Amazon-account path is ever called.

Public API:
  load_phase6a_dependency(run_dir) -> Phase6ADependency
  build_top_relevance_keywords(dep, keyword_source) -> dict
  build_keyword_allocation_map(dep, top_relevance) -> dict
  build_unallocated_eligible_pool(...) -> dict
  build_keyword_coverage_report(result) -> str
  plan_keyword_allocation(run_dir, ...) -> KeywordAllocationResult
  validate_top_relevance_keywords / validate_keyword_allocation_map /
  validate_unallocated_eligible_pool / validate_stage6b_manifest
  write_phase6b_artifacts(result, dest_dir, ...) -> manifest dict
  verify_phase6b_artifacts(dest_dir, workspace_dir=None) -> verdict dict
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../listing
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE, os.path.join(_ROOT, "core")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import keyword_source_adapter as KSA          # noqa: E402
import category_policy_registry as CPR        # noqa: E402
import claim_evidence as CE                   # noqa: E402
import bullet_engine as BE                    # noqa: E402  (reused for the 5-job taxonomy only)
from production import product_workspace as PW  # noqa: E402  (6A authority + serialization/atomic reuse)

# ---------------------------------------------------------------- schema versions / identity
SCHEMA_VERSION = "phase6b-keyword-allocation-v1"
TOP_RELEVANCE_SCHEMA_VERSION = "phase6-top-relevance-v1"
ALLOCATION_MAP_SCHEMA_VERSION = "phase6-allocation-v1"
UNALLOCATED_SCHEMA_VERSION = "phase6-unallocated-eligible-v1"
STAGE_MANIFEST_SCHEMA_VERSION = "phase6b-stage-manifest-v1"

RANKING_POLICY_VERSION = "phase6b-ranking-v1"
ALLOCATION_SCHEMA_VERSION = "phase6b-13field-v1"

STAGE_ID = "6B"
STAGE_NAME = "Top-Relevance Keyword Selection and 13-Field Allocation"
PHASE6B_SUBPATH = ("phase6", "6B")

# ---------------------------------------------------------------- stage states
READY_FOR_6C = "READY_FOR_6C"
SAFE_ALLOCATION_OWNER_FACTS_REQUIRED = "SAFE_ALLOCATION_OWNER_FACTS_REQUIRED"
BLOCKED_DEPENDENCY = "BLOCKED_DEPENDENCY"
BLOCKED_UNSAFE = "BLOCKED_UNSAFE"
INVALID_SOURCE = "INVALID_SOURCE"
INVALID_ALLOCATION = "INVALID_ALLOCATION"
STAGE_STATES = (READY_FOR_6C, SAFE_ALLOCATION_OWNER_FACTS_REQUIRED, BLOCKED_DEPENDENCY,
                BLOCKED_UNSAFE, INVALID_SOURCE, INVALID_ALLOCATION)

LISTING_SAFE_DRAFT = "SAFE_DRAFT_OWNER_FACTS_REQUIRED"

# ---------------------------------------------------------------- artifact filenames (exact)
TOP_RELEVANCE_FILENAME = "TOP-RELEVANCE-KEYWORDS.json"
ALLOCATION_MAP_FILENAME = "KEYWORD-ALLOCATION-MAP.json"
COVERAGE_REPORT_FILENAME = "KEYWORD-COVERAGE-REPORT.md"
UNALLOCATED_FILENAME = "UNALLOCATED-ELIGIBLE-KEYWORDS.json"
STAGE_MANIFEST_FILENAME = "STAGE-6B-MANIFEST.json"
# the manifest is written last and is NOT in its own output_hashes (never self-hash).
REQUIRED_ARTIFACTS = (TOP_RELEVANCE_FILENAME, ALLOCATION_MAP_FILENAME, COVERAGE_REPORT_FILENAME,
                      UNALLOCATED_FILENAME)

# ---------------------------------------------------------------- the exact 13-field schema
FIELD_VISIBLE = "VISIBLE_COPY"
FIELD_HIDDEN = "HIDDEN_SEARCH"
FIELD_CATEGORY_GATED = "CATEGORY_GATED_COPY"
FIELD_ENHANCED = "ENHANCED_CONTENT"
FIELD_CREATIVE = "CREATIVE_COPY"
FIELD_CREATIVE_ALT = "CREATIVE_ALT_TEXT"

# ordered field key -> (type, max_assignments). None max = unbounded (still deterministic).
FIELD_SPECS = (
    ("title_primary", FIELD_VISIBLE, 1),
    ("title_support", FIELD_VISIBLE, 2),
    ("bullet_1", FIELD_VISIBLE, 2),
    ("bullet_2", FIELD_VISIBLE, 2),
    ("bullet_3", FIELD_VISIBLE, 2),
    ("bullet_4", FIELD_VISIBLE, 2),
    ("bullet_5", FIELD_VISIBLE, 2),
    ("description", FIELD_VISIBLE, 8),
    ("backend_candidates", FIELD_HIDDEN, None),
    ("item_highlights", FIELD_CATEGORY_GATED, None),
    ("aplus", FIELD_ENHANCED, None),
    ("listing_image_text", FIELD_CREATIVE, None),
    ("image_alt_text", FIELD_CREATIVE_ALT, None),
)
FIELD_KEYS = tuple(k for k, _t, _m in FIELD_SPECS)
FIELD_TYPE = {k: t for k, t, _m in FIELD_SPECS}
FIELD_MAX = {k: m for k, _t, m in FIELD_SPECS}
VISIBLE_FIELDS = tuple(k for k, t, _m in FIELD_SPECS if t == FIELD_VISIBLE)

# bullet field -> its existing bullet-engine job + the claim concepts that job draws from.
BULLET_FIELDS = ("bullet_1", "bullet_2", "bullet_3", "bullet_4", "bullet_5")
BULLET_JOB = {f"bullet_{i + 1}": BE.JOBS[i] for i in range(5)}
BULLET_JOB_CONCEPTS = {f"bullet_{i + 1}": tuple(BE.JOB_CONCEPTS.get(BE.JOBS[i], ())) for i in range(5)}

# empty-field reason vocabulary
EMPTY_CATEGORY_UNSUPPORTED = "CATEGORY_UNSUPPORTED"
EMPTY_OWNER_FACT_REQUIRED = "OWNER_FACT_REQUIRED"
EMPTY_REAL_ASSET_REQUIRED = "REAL_ASSET_REQUIRED"
EMPTY_CLAIM_EVIDENCE_MISSING = "CLAIM_EVIDENCE_MISSING"
EMPTY_NO_INCREMENTAL = "NO_INCREMENTAL_KEYWORD_VALUE"
EMPTY_NO_SAFE_TERM = "NO_SAFE_ELIGIBLE_TERM"
EMPTY_NOT_PUBLISHABLE = "FIELD_NOT_PUBLISHABLE"
EMPTY_NOT_APPLICABLE = "NOT_APPLICABLE"
EMPTY_RESERVED = "RESERVED_FOR_LATER_STAGE"

# non-allocation reason vocabulary (unallocated eligible pool)
NA_LOWER_VALUE = "LOWER_INCREMENTAL_VALUE"
NA_DUPLICATE_CONCEPT = "DUPLICATE_CONCEPT"
NA_DUPLICATE_ROOT = "DUPLICATE_ROOT"
NA_CAPACITY = "FIELD_CAPACITY_REACHED"
NA_BETTER_VARIANT = "BETTER_VARIANT_SELECTED"
NA_NATURAL_LANGUAGE = "NATURAL_LANGUAGE_CONCERN"
NA_FUTURE_REVIEW = "RESERVED_FOR_FUTURE_REVIEW"
NA_NO_PURPOSE = "NO_DISTINCT_FIELD_PURPOSE"

# exclusion reason vocabulary (stable)
EXC_SOURCE_REJECTED = "SOURCE_REJECTED"
EXC_SOURCE_RISKY = "SOURCE_RISKY"
EXC_SOURCE_OWNER_APPROVAL = "SOURCE_OWNER_APPROVAL_REQUIRED"
EXC_BRAND_IP = "BRAND_IP_BLOCKED"
EXC_WRONG_PRODUCT = "WRONG_PRODUCT"
EXC_WRONG_AUDIENCE = "WRONG_AUDIENCE"
EXC_WRONG_OCCASION = "WRONG_OCCASION"
EXC_MALFORMED = "MALFORMED_TERM"
EXC_CATEGORY_UNSUPPORTED = "CATEGORY_UNSUPPORTED"
EXC_PRODUCT_FACT_UNKNOWN = "PRODUCT_FACT_UNKNOWN"
EXC_OWNER_FACT_REQUIRED = "OWNER_FACT_REQUIRED"
EXC_CLAIM_EVIDENCE_MISSING = "CLAIM_EVIDENCE_MISSING"
EXC_CLAIM_BLOCKED = "CLAIM_BLOCKED"
EXC_UNSUPPORTED_DECORATION = "UNSUPPORTED_DECORATION"
EXC_UNSUPPORTED_MATERIAL = "UNSUPPORTED_MATERIAL"
EXC_UNSUPPORTED_COLOR = "UNSUPPORTED_COLOR"
EXC_UNSUPPORTED_SIZE = "UNSUPPORTED_SIZE"
EXC_UNSUPPORTED_PERSONALIZATION = "UNSUPPORTED_PERSONALIZATION"
EXC_UNSAFE = "UNSAFE_OR_MISLEADING"

ADVISORY_ONLY = "ADVISORY_ONLY"

# max sanitized example phrases retained per group in local artifacts (never paid metric rows).
_MAX_EXAMPLES = 5

# ---------------------------------------------------------------- deterministic serialization reuse
# One serialization authority for the whole toolkit — byte-identical to Phase 5 / Phase 6A.
canonical_json = PW.canonical_json
content_sha256 = PW.content_sha256
_file_sha256 = PW._file_sha256
_safe_rel = PW._safe_rel


def _atomic_write_text(path, text):
    """Temp sibling -> flush -> fsync -> os.replace. A failed write never overwrites the last valid
    artifact (mirrors the 6A authority; distinct .tmp-6b- prefix so residue is attributable)."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-6b-", suffix=".part")
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


def phase6b_dir(workspace_dir):
    return os.path.join(workspace_dir, *PHASE6B_SUBPATH)


# ---------------------------------------------------------------- errors
class KeywordAllocationError(Exception):
    """Base class for every Phase 6B allocation failure."""


class Phase6ADependencyError(KeywordAllocationError):
    """The completed Phase 6A workspace is missing, altered, or not ready for 6B."""


# ================================================================ market-identity concept lexicon
# The ONLY concepts safe for publishable allocation with zero verified physical facts are the
# keyword-derived MARKET IDENTITY (product family + audience) and the VERIFIED recipient/gift concept.
# Everything else asserts an unverified physical fact and is gated to OWNER_FACT_REQUIRED / UNSUPPORTED.
#
# This lexicon maps normalized tokens onto the SHARED claim-concept vocabulary (listing.claim_evidence
# CLAIM_CONCEPTS); it never re-tiers or re-evaluates eligibility (the adapter is that authority) and it
# never verifies a fact — it decides which claim concepts a phrase ASSERTS so the loaded 6A claim
# evidence can gate it. It is versioned as part of the ranking/allocation policy.

# product family classified for T2 = sweatshirt (keyword-derived market identity, not a physical fact).
PRODUCT_FAMILY = {"sweatshirt", "sweatshirts"}
# nurse-role audience family — backs the VERIFIED recipient concept (keyword audience = nurses).
AUDIENCE = {
    "nurse", "nurses", "nursing", "rn", "rns", "lpn", "lpns", "cna", "cnas", "np", "nps",
    "nicu", "icu", "picu", "ccu", "er", "practitioner", "practitioners", "midwife", "midwives",
    "student", "students", "grad", "grads", "graduate", "graduates",
}
# the generic head of the audience family (not a specialised subtype like rn / icu / practitioner).
# A head term makes the strongest, broadest title identity; subtypes belong in description / backend.
AUDIENCE_HEAD = {"nurse", "nurses", "nursing"}
# gift / recipient framing — backs the VERIFIED recipient concept.
GIFT = {"gift", "gifts"}
CONNECTORS = {"for", "a", "an", "the", "to", "of", "and"}
SAFE_TOKENS = PRODUCT_FAMILY | AUDIENCE | GIFT | CONNECTORS

# gating lexicon: token -> (excluded reason code, asserted claim concept). Priority: first match wins.
_GATE_LEXICON = (
    (EXC_UNSUPPORTED_DECORATION, "decoration_method", {
        "embroidered", "embroidery", "embroider", "stitched", "stitch", "satin", "tatami",
        "monogram", "monogrammed", "monogramming", "printed", "print", "screenprint",
        "vinyl", "applique", "patch", "engraved", "graphic"}),
    (EXC_UNSUPPORTED_MATERIAL, "material_composition", {
        "cotton", "polyester", "poly", "fleece", "wool", "cashmere", "blend", "organic",
        "linen", "spandex", "nylon", "rayon", "terry", "jersey", "flannel"}),
    (EXC_UNSUPPORTED_PERSONALIZATION, "personalization_fields", {
        "personalized", "personalised", "personalize", "personalization", "custom", "customized",
        "customised", "customize", "customizable", "customizing", "name", "names", "named",
        "initial", "initials", "monogrammed"}),
    (EXC_UNSUPPORTED_COLOR, "color_options", {
        "black", "white", "pink", "blue", "navy", "red", "green", "purple", "grey", "gray",
        "teal", "maroon", "burgundy", "yellow", "orange", "brown", "beige", "tan", "cream",
        "ivory", "gold", "silver", "colors", "colours", "colorful", "color", "colour"}),
    (EXC_UNSUPPORTED_SIZE, "fit", {
        "women", "womens", "woman", "men", "mens", "man", "ladies", "lady", "unisex", "female",
        "male", "plus", "oversized", "oversize", "slim", "relaxed", "fitted", "xs", "xl", "xxl",
        "xxxl", "2xl", "3xl", "4xl", "5xl", "small", "medium", "large", "size", "sizes", "big",
        "tall", "petite", "crop", "cropped"}),
)
# other garment forms (different product than the classified sweatshirt) -> garment_type owner fact.
_GARMENT_OTHER = {
    "hoodie", "hoodies", "sweater", "sweaters", "shirt", "shirts", "tshirt", "tshirts", "tee",
    "tees", "jacket", "jackets", "cardigan", "vest", "pullover", "crewneck", "quarter", "zip",
    "coat", "tank", "top", "tops", "apparel", "clothing", "outfit", "outfits", "uniform",
    "scrub", "scrubs", "jumper", "hooded",
}
# occasion / theme tokens -> occasion owner fact (keyword relevance never verifies a real occasion).
_OCCASION = {
    "week", "day", "appreciation", "christmas", "xmas", "birthday", "graduation", "valentine",
    "valentines", "halloween", "thanksgiving", "mothers", "fathers", "holiday", "holidays",
    "season", "seasonal", "easter", "hanukkah", "anniversary", "retirement", "2024", "2025",
    "2026", "2027", "week's", "national",
}
# subjective / differentiator / comfort tokens -> owner-verified differentiator/comfort/durability.
_DIFFERENTIATOR = {
    "best", "cool", "cute", "cutest", "coolest", "funny", "favorite", "premium", "quality",
    "comfortable", "comfy", "soft", "cozy", "warm", "durable", "lightweight", "heavyweight",
    "aesthetic", "trendy", "stylish", "vintage", "retro", "new", "amazing", "perfect",
}


def _tokens(normalized_phrase):
    return re.findall(r"[a-z0-9]+", normalized_phrase or "")


def _content_tokens(normalized_phrase):
    """Significant tokens for root/concept identity (drop pure connectors)."""
    return [t for t in _tokens(normalized_phrase) if t not in CONNECTORS]


def _stem(tok):
    """Crude singular stem so plural / word-order / synonym variants collapse to one root."""
    if tok in ("nurses", "nurse", "nursing"):   # one audience concept -> one root
        return "nurse"
    if len(tok) > 3 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def _normalized_root(normalized_phrase):
    """A stable concept root: gift-framing + connectors dropped, remaining tokens stemmed & sorted.

    So 'nurse sweatshirt', 'sweatshirt nurse', 'nurses sweatshirt' and 'sweatshirt for nurses' share
    the root 'nurse|sweatshirt' — word-order and plural variants consolidate to one concept.
    """
    toks = [_stem(t) for t in _content_tokens(normalized_phrase) if t not in GIFT]
    toks = sorted(set(toks))
    return "|".join(toks) if toks else "|".join(sorted(set(_stem(t) for t in _content_tokens(normalized_phrase))))


def _profile(normalized_phrase):
    """Which market-identity categories a phrase covers: P(roduct), A(udience), R(ecipient/gift)."""
    toks = set(_tokens(normalized_phrase))
    prof = set()
    if toks & PRODUCT_FAMILY:
        prof.add("P")
    if toks & AUDIENCE:
        prof.add("A")
    if toks & GIFT:
        prof.add("R")
    return prof


def _classify_keyword(rec):
    """Classify one SOURCE-ELIGIBLE keyword record for Phase 6B allocation.

    Returns (verdict, concept, reason_code) where verdict is 'SAFE' (pure market identity) or
    'GATED' (asserts an unverified physical fact / different garment / occasion / differentiator).
    """
    toks = _content_tokens(rec["keyword_normalized"])
    extra = [t for t in toks if t not in SAFE_TOKENS]
    if not extra:
        return "SAFE", None, None
    # assign the most policy-relevant gating reason from the extra (unsafe) tokens.
    extra_set = set(extra)
    for code, concept, vocab in _GATE_LEXICON:
        if extra_set & vocab:
            return "GATED", concept, code
    if extra_set & _GARMENT_OTHER:
        return "GATED", "garment_type", EXC_OWNER_FACT_REQUIRED
    if extra_set & _OCCASION:
        return "GATED", "occasion", EXC_OWNER_FACT_REQUIRED
    if extra_set & _DIFFERENTIATOR:
        return "GATED", "differentiator", EXC_OWNER_FACT_REQUIRED
    return "GATED", None, EXC_PRODUCT_FACT_UNKNOWN


# ================================================================ Phase 6A dependency
class Phase6ADependency:
    """The verified, read-only completed Phase 6A workspace that Phase 6B consumes."""

    def __init__(self, *, run_dir, dest_dir, workspace_id, product_id, category, product_type,
                 audience, primary_keyword, keyword_source_sha256, product_facts_sha256,
                 claim_evidence_sha256, phase6a_manifest_sha256, listing_publishability_state,
                 ready_for_6b, claim_states, claim_records, category_policy, source_files):
        self.run_dir = run_dir
        self.dest_dir = dest_dir
        self.workspace_id = workspace_id
        self.product_id = product_id
        self.category = category
        self.product_type = product_type
        self.audience = audience
        self.primary_keyword = primary_keyword
        self.keyword_source_sha256 = keyword_source_sha256
        self.product_facts_sha256 = product_facts_sha256
        self.claim_evidence_sha256 = claim_evidence_sha256
        self.phase6a_manifest_sha256 = phase6a_manifest_sha256
        self.listing_publishability_state = listing_publishability_state
        self.ready_for_6b = ready_for_6b
        self.claim_states = claim_states          # {concept: verification_state}
        self.claim_records = claim_records        # {concept: full record}
        self.category_policy = category_policy
        self.source_files = source_files

    def concept_is_publishable(self, concept):
        rec = self.claim_records.get(concept)
        return bool(rec and rec.get("publishable"))

    def claim_id(self, concept):
        rec = self.claim_records.get(concept)
        return rec.get("claim_id") if rec else "CLM-" + str(concept).upper()

    def required_fact_fields(self, concept):
        rec = self.claim_records.get(concept)
        if not rec:
            return []
        return sorted(set(rec.get("source_fact_fields", [])) - {f"keyword:{k}" for k in ("audience",)})


def _load_json(path):
    import json
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_phase6a_dependency(run_dir):
    """Load and VERIFY the completed Phase 6A workspace. Never recomputes or overwrites 6A artifacts.

    Raises Phase6ADependencyError when any required 6A file is missing, a recomputed hash disagrees,
    identities disagree, the keyword-source hash drifts, or ready_for_6b is not true.
    """
    dest = PW.phase6a_dir(run_dir)
    required = (PW.PRODUCT_WORKSPACE_FILENAME, PW.OWNER_INPUT_FILENAME, PW.NORMALIZED_FACTS_FILENAME,
                PW.CLAIM_EVIDENCE_FILENAME, PW.STAGE_MANIFEST_FILENAME)
    missing = [n for n in required if not os.path.exists(os.path.join(dest, n))]
    if missing:
        raise Phase6ADependencyError("PHASE6A_DEPENDENCY_INVALID: missing 6A artifacts: "
                                     + ", ".join(sorted(missing)))

    # 1. recompute every 6A artifact hash against the recorded manifest (the 6A verify authority).
    verdict = PW.verify_phase6a_artifacts(dest, workspace_dir=run_dir)
    if not verdict.get("ok"):
        raise Phase6ADependencyError("PHASE6A_DEPENDENCY_INVALID: 6A artifact verification failed: "
                                     + "; ".join(verdict.get("errors", [])))

    workspace = _load_json(os.path.join(dest, PW.PRODUCT_WORKSPACE_FILENAME))
    manifest = _load_json(os.path.join(dest, PW.STAGE_MANIFEST_FILENAME))
    claims_doc = _load_json(os.path.join(dest, PW.CLAIM_EVIDENCE_FILENAME))

    # 2. identity agreement: workspace_id == product_id, and manifest agrees.
    ws_id = workspace.get("workspace_id")
    prod_id = workspace.get("product_id")
    if not ws_id or not prod_id or ws_id != prod_id:
        raise Phase6ADependencyError(
            f"PHASE6A_DEPENDENCY_INVALID: ambiguous product identity (workspace_id={ws_id!r}, "
            f"product_id={prod_id!r})")
    if manifest.get("workspace_id") != ws_id or manifest.get("product_id") != prod_id:
        raise Phase6ADependencyError("PHASE6A_DEPENDENCY_INVALID: manifest/workspace identity disagree")

    # 3. keyword-source hash agreement (workspace vs manifest vs the actual on-disk source).
    ws_src_sha = workspace.get("keyword_source_sha256")
    man_src = (manifest.get("input_hashes") or {})
    if ws_src_sha not in man_src.values():
        raise Phase6ADependencyError("PHASE6A_DEPENDENCY_INVALID: keyword source hash disagrees "
                                     "between workspace and manifest")
    src = KSA.load_keyword_source(run_dir)
    if src.source_sha256 != ws_src_sha:
        raise Phase6ADependencyError(
            "PHASE6A_DEPENDENCY_INVALID: on-disk keyword source hash "
            f"{src.source_sha256[:12]} != 6A-recorded {str(ws_src_sha)[:12]} — source changed")

    # 4. readiness verdict.
    ready = bool((workspace.get("downstream_readiness") or {}).get("ready_for_6b"))
    if not ready:
        raise Phase6ADependencyError("PHASE6A_DEPENDENCY_INVALID: ready_for_6b is not true")

    # 5. claim evidence states (READ ONLY).
    claim_records = dict(claims_doc.get("claims") or {})
    claim_states = {c: r.get("verification_state") for c, r in claim_records.items()}

    policy = CPR.resolve_category_policy(workspace.get("category") or "apparel")
    return Phase6ADependency(
        run_dir=run_dir, dest_dir=dest, workspace_id=ws_id, product_id=prod_id,
        category=workspace.get("category"), product_type=workspace.get("product_type"),
        audience=workspace.get("audience"), primary_keyword=workspace.get("primary_keyword"),
        keyword_source_sha256=ws_src_sha,
        product_facts_sha256=workspace.get("product_facts_sha256"),
        claim_evidence_sha256=workspace.get("claim_evidence_sha256"),
        phase6a_manifest_sha256=manifest.get("deterministic_content_sha256"),
        listing_publishability_state=workspace.get("current_listing_publishability_state"),
        ready_for_6b=ready, claim_states=claim_states, claim_records=claim_records,
        category_policy=policy, source_files=list(workspace.get("source_files") or []))


# ================================================================ working records + ranking
def _build_working_records(dep, keyword_source):
    """Partition the SOURCE-eligible pool into Phase-6B safe records and gated (owner-fact) records,
    plus the source-ineligible summary. Returns (safe, gated, source_ineligible_counts)."""
    eligible = keyword_source.eligible_keywords()
    order_index = {id(r): i for i, r in enumerate(eligible)}  # adapter's strongest-first order
    safe, gated = [], []
    for r in eligible:
        verdict, concept, reason = _classify_keyword(r)
        rank = order_index[id(r)]
        base = {
            "keyword_id": _keyword_id(r["keyword_normalized"]),
            "phrase": r["keyword_exact"],
            "normalized_phrase": r["keyword_normalized"],
            "source_tier": r["normalized_tier"],
            "source_rank": rank,
            "source_record_id": r["keyword_normalized"],
            "concept_id": _normalized_root(r["keyword_normalized"]),
            "normalized_root": _normalized_root(r["keyword_normalized"]),
            "source_keyword_sha256": keyword_source.source_sha256,
            "profile": sorted(_profile(r["keyword_normalized"])),
            "_evidence_completeness": _evidence_completeness(r),
        }
        if verdict == "SAFE":
            safe.append(base)
        else:
            g = dict(base)
            g["gate_reason_code"] = reason
            g["unsupported_concept"] = concept
            g["required_claim_ids"] = [dep.claim_id(concept)] if concept else []
            g["required_fact_ids"] = dep.required_fact_fields(concept) if concept else []
            gated.append(g)

    ineligible = {}
    for r in keyword_source.keywords:
        if r["eligible"]:
            continue
        code = _source_exclusion_code(r)
        slot = ineligible.setdefault(code, {"count": 0, "examples": []})
        slot["count"] += 1
        if len(slot["examples"]) < _MAX_EXAMPLES:
            slot["examples"].append(r["keyword_exact"])
    return safe, gated, ineligible


def _keyword_id(normalized_phrase):
    return "KW-" + hashlib.sha1(normalized_phrase.encode("utf-8")).hexdigest()[:12]


def _evidence_completeness(rec):
    fields = ("search_volume", "competitor_coverage", "top_20_coverage", "top_50_coverage",
              "top_100_coverage", "best_rank", "median_rank")
    return sum(1 for f in fields if rec.get(f) is not None) + (1 if rec.get("batch_support") else 0)


def _source_exclusion_code(rec):
    risks = set(rec.get("blocking_risks") or [])
    if risks & {"TRADEMARK", "BRAND_TERM", "IP_RISK"}:
        return EXC_BRAND_IP
    if "WRONG_AUDIENCE" in risks:
        return EXC_WRONG_AUDIENCE
    if "WRONG_PRODUCT" in risks:
        return EXC_WRONG_PRODUCT
    if "WRONG_OCCASION" in risks:
        return EXC_WRONG_OCCASION
    if "MALFORMED" in risks:
        return EXC_MALFORMED
    if risks & {"EXPLICIT_CONFLICT", "DATA_CONTAMINATION"}:
        return EXC_SOURCE_RISKY
    if rec.get("normalized_tier") == KSA.REJECTED:
        return EXC_SOURCE_REJECTED
    return EXC_SOURCE_OWNER_APPROVAL


_TIER_SCORE = {KSA.TIER_A: 100, KSA.TIER_B: 80, KSA.TIER_C: 60}


def _score_record(rec, root_seen_count):
    """Deterministic, explicit relevance score with exposed component reasons. No hidden decision,
    no fabricated commercial metric, no reward for repetition (duplicate-root is penalised).

    The head term (generic nurse + product, concise) beats a narrow subtype for the strongest slots;
    the adapter's own strongest-first order (source_rank) is the dominant commercial signal.
    """
    prof = set(rec["profile"])
    ntoks = _content_tokens(rec["normalized_phrase"])
    aud_toks = set(ntoks) & AUDIENCE
    head = bool(aud_toks) and aud_toks <= AUDIENCE_HEAD
    comp = {
        "tier_strength": _TIER_SCORE.get(rec["source_tier"], 40),
        "product_identity_match": 40 if "P" in prof else 0,
        "audience_identity_match": 30 if "A" in prof else 0,
        "recipient_gift_match": 20 if "R" in prof else 0,
        "audience_head_bonus": 15 if head else 0,
        "concept_coverage": 10 * len(prof),
        "conciseness_bonus": 6 - min(len(ntoks), 6),
        "source_strength": max(0, 40 - rec["source_rank"] // 5),
        "evidence_completeness": min(rec["_evidence_completeness"], 5),
        "duplicate_root_penalty": -12 * min(root_seen_count, 3),
    }
    reasons = []
    if comp["product_identity_match"]:
        reasons.append("PRODUCT_IDENTITY_MATCH")
    if comp["audience_identity_match"]:
        reasons.append("AUDIENCE_IDENTITY_MATCH")
    if comp["recipient_gift_match"]:
        reasons.append("RECIPIENT_GIFT_MATCH")
    if head:
        reasons.append("AUDIENCE_HEAD_TERM")
    reasons.append(f"SOURCE_TIER_{rec['source_tier']}")
    if comp["duplicate_root_penalty"]:
        reasons.append("DUPLICATE_ROOT_PENALTY")
    return sum(comp.values()), comp, reasons


def build_top_relevance_keywords(dep, keyword_source, safe=None):
    """Rank the Phase-6B eligible SAFE pool deterministically with explicit component reasons."""
    if safe is None:
        safe, _gated, _inelig = _build_working_records(dep, keyword_source)

    # first pass: base scores (root not yet seen) to establish a stable order.
    prelim = []
    for rec in safe:
        base, _c, _r = _score_record(rec, 0)
        prelim.append((rec, base))
    prelim.sort(key=lambda x: (-x[1], x[0]["source_rank"], x[0]["normalized_phrase"]))

    # second pass: apply duplicate-root penalty in that stable order, then re-rank.
    root_counts = {}
    scored = []
    for rec, _base in prelim:
        seen = root_counts.get(rec["normalized_root"], 0)
        total, comp, reasons = _score_record(rec, seen)
        root_counts[rec["normalized_root"]] = seen + 1
        item = {
            "keyword_id": rec["keyword_id"],
            "phrase": rec["phrase"],
            "normalized_phrase": rec["normalized_phrase"],
            "source_tier": rec["source_tier"],
            "source_rank": rec["source_rank"],
            "concept_id": rec["concept_id"],
            "normalized_root": rec["normalized_root"],
            "relevance_score": total,
            "score_components": comp,
            "product_compatibility": "COMPATIBLE" if "P" in rec["profile"] else "IDENTITY_NEUTRAL",
            "audience_compatibility": "COMPATIBLE" if "A" in rec["profile"] else "IDENTITY_NEUTRAL",
            "occasion_compatibility": "NO_OCCASION_ASSERTED",
            "claim_sensitivity": "MARKET_IDENTITY_ONLY",
            "required_fact_ids": [],
            "required_claim_ids": ([dep.claim_id("recipient")] if "R" in rec["profile"] else []),
            "inclusion_reason_codes": reasons,
            "warnings": [],
            "source_keyword_sha256": rec["source_keyword_sha256"],
            "_profile": rec["profile"],
        }
        scored.append((item, total))
    scored.sort(key=lambda x: (-x[1], x[0]["source_rank"], x[0]["normalized_phrase"]))
    keywords = [it for it, _t in scored]

    doc = {
        "schema_version": TOP_RELEVANCE_SCHEMA_VERSION,
        "workspace_id": dep.workspace_id,
        "product_id": dep.product_id,
        "product_type": dep.product_type,
        "audience": dep.audience,
        "source_keyword_sha256": keyword_source.source_sha256,
        "ranking_policy_version": RANKING_POLICY_VERSION,
        "selection_summary": {
            "source_eligible_count": len(keyword_source.eligible_keywords()),
            "selected_top_relevance_count": len(keywords),
            "tier_a_count": keyword_source.tier_counts.get(KSA.TIER_A, 0),
            "tier_b_count": keyword_source.tier_counts.get(KSA.TIER_B, 0),
            "ranking_components": ["tier_strength", "product_identity_match", "audience_identity_match",
                                   "recipient_gift_match", "audience_head_bonus", "concept_coverage",
                                   "conciseness_bonus", "source_strength", "evidence_completeness",
                                   "duplicate_root_penalty"],
        },
        "keywords": keywords,
    }
    return doc


# ================================================================ 13-field allocation
def _empty_field_status(reason, note, **extra):
    st = {"assignment_count": 0, "empty": True, "empty_reason_code": reason, "note": note}
    st.update(extra)
    return st


def _assignment(item, field, purpose, dep, *, reuse=None, incremental=None, extra_reasons=None):
    prof = set(item.get("_profile", []))
    required_claim_ids = [dep.claim_id("recipient")] if "R" in prof else []
    reasons = list(extra_reasons or [])
    reasons += list(item["inclusion_reason_codes"])
    aid = "A6B-" + hashlib.sha1((field + "|" + item["normalized_phrase"]).encode("utf-8")).hexdigest()[:12]
    rec = {
        "assignment_id": aid,
        "keyword_id": item["keyword_id"],
        "phrase": item["phrase"],
        "normalized_phrase": item["normalized_phrase"],
        "source_tier": item["source_tier"],
        "source_rank": item["source_rank"],
        "source_keyword_sha256": item["source_keyword_sha256"],
        "concept_id": item["concept_id"],
        "normalized_root": item["normalized_root"],
        "target_field": field,
        "field_type": FIELD_TYPE[field],
        "purpose": purpose,
        "relevance_score": item["relevance_score"],
        "inclusion_reason_codes": sorted(set(reasons)),
        "product_compatibility": item["product_compatibility"],
        "audience_compatibility": item["audience_compatibility"],
        "occasion_compatibility": item["occasion_compatibility"],
        "claim_sensitivity": item["claim_sensitivity"],
        "required_fact_ids": [],
        "required_claim_ids": required_claim_ids,
        "owner_fact_dependencies": [],
        "real_asset_dependencies": [],
        "constraints": [],
        "downstream_authorization": ADVISORY_ONLY,
        "warnings": [],
    }
    if reuse:
        rec["reuse"] = "REUSE_JUSTIFIED"
        rec["reuse_justification"] = reuse
    if incremental:
        rec["incremental_semantic_value"] = incremental
    return rec


def build_keyword_allocation_map(dep, top_relevance, keyword_source, gated=None):
    """Allocate the ranked SAFE pool into the exact 13-field schema as advisory metadata.

    Rules: within-field roots are unique; a root repeats across VISIBLE fields only for the ONE core
    identity phrase (title_primary -> bullet_1), tagged REUSE_JUSTIFIED. No gated/unsupported term is
    ever assigned. Empty fields are valid and carry an explicit reason.
    """
    items = list(top_relevance["keywords"])
    by_root_first = {}
    canonical, dup_variants = [], []
    for it in items:
        root = it["normalized_root"]
        if root in by_root_first:
            dup_variants.append((it, by_root_first[root]))
        else:
            by_root_first[root] = it
            canonical.append(it)

    fields = {k: [] for k in FIELD_KEYS}
    field_status = {}
    used_visible_roots = set()
    assigned_roots = set()
    assigned_ids = set()

    def take(it, field, purpose, **kw):
        fields[field].append(_assignment(it, field, purpose, dep, **kw))
        assigned_roots.add(it["normalized_root"])
        assigned_ids.add(it["keyword_id"])
        if FIELD_TYPE[field] == FIELD_VISIBLE:
            used_visible_roots.add(it["normalized_root"])

    def first(pred, exclude_visible=True):
        for it in canonical:
            if it["keyword_id"] in assigned_ids:
                continue
            if exclude_visible and it["normalized_root"] in used_visible_roots:
                continue
            if pred(it):
                return it
        return None

    prof = lambda it: set(it.get("_profile", []))

    # --- title_primary: strongest safe product+audience identity (fallback product, then any) ------
    primary = first(lambda it: {"P", "A"} <= prof(it)) or first(lambda it: "P" in prof(it)) \
        or (canonical[0] if canonical else None)
    if primary:
        take(primary, "title_primary",
             "Strongest safe core product+audience identity phrase.",
             incremental="Anchors the listing on the verified market identity (product family + nurse audience).")
        field_status["title_primary"] = {"assignment_count": 1, "empty": False,
                                          "note": "Single strongest safe core phrase; no unsupported physical claim."}
        # --- bullet_1 (PRODUCT_AND_PERSONALIZATION): reuse the core identity; personalization is gated
        take(primary, "bullet_1",
             "Bullet 1 product-identity statement (job PRODUCT_AND_PERSONALIZATION).",
             reuse="Distinct field purpose: the title names the product; bullet 1 opens the product "
                   "statement. Personalization half is OWNER_FACT_REQUIRED.",
             incremental="Restates verified product identity in the bullet-1 job context.",
             extra_reasons=["BULLET_JOB_PRODUCT_HALF"])
        field_status["bullet_1"] = {
            "assignment_count": 1, "empty": False, "bullet_job": BULLET_JOB["bullet_1"],
            "note": "Product half only; personalization concepts are OWNER_FACT_REQUIRED.",
            "owner_fact_required_concepts": ["personalization_fields", "exact_personalization_promise"]}
    else:
        field_status["title_primary"] = _empty_field_status(EMPTY_NO_SAFE_TERM,
                                                            "No safe eligible core product phrase.")
        field_status["bullet_1"] = _empty_field_status(EMPTY_NO_SAFE_TERM, "No safe product term.",
                                                       bullet_job=BULLET_JOB["bullet_1"])

    # --- title_support: up to 2 incremental head-level concepts (recipient/gift angle preferred) ---
    # Kept head-level and distinct-root so the title never saturates on a subtype or word-order variant.
    def _head_level(it):
        return (set(_content_tokens(it["normalized_phrase"])) & AUDIENCE) <= AUDIENCE_HEAD
    support = []
    support_roots = set()
    for it in canonical:
        if len(support) >= FIELD_MAX["title_support"]:
            break
        if it["keyword_id"] in assigned_ids or it["normalized_root"] in used_visible_roots:
            continue
        if it["normalized_root"] in support_roots or not _head_level(it):
            continue
        if "R" in prof(it):
            support.append((it, "Adds the verified recipient/gift angle beyond the product identity."))
            support_roots.add(it["normalized_root"])
    for it, why in support:
        take(it, "title_support", "Incremental supporting identity concept.",
             incremental=why, extra_reasons=["TITLE_SUPPORT_INCREMENTAL"])
    if fields["title_support"]:
        field_status["title_support"] = {"assignment_count": len(fields["title_support"]), "empty": False,
                                         "note": "Each adds meaning beyond the primary; distinct roots, no saturation."}
    else:
        field_status["title_support"] = _empty_field_status(EMPTY_NO_INCREMENTAL,
                                                            "No incremental safe concept beyond the primary phrase.")

    # --- bullets 2..4: their jobs draw only from BLOCKED claim concepts -> empty with reasons -------
    for f in ("bullet_2", "bullet_3", "bullet_4"):
        concepts = BULLET_JOB_CONCEPTS[f]
        publishable = [c for c in concepts if dep.concept_is_publishable(c)]
        field_status[f] = _empty_field_status(
            EMPTY_CLAIM_EVIDENCE_MISSING if not publishable else EMPTY_NO_INCREMENTAL,
            f"Job {BULLET_JOB[f]} draws from concepts {list(concepts)}; none are verified for this product.",
            bullet_job=BULLET_JOB[f],
            owner_fact_required_concepts=list(concepts),
            required_claim_ids=[dep.claim_id(c) for c in concepts])

    # --- bullet_5 (RECIPIENT_OCCASION_USE): recipient concept IS verified -> allocate a recipient term
    b5 = first(lambda it: "R" in prof(it)) or first(lambda it: "A" in prof(it))
    if b5:
        take(b5, "bullet_5", "Bullet 5 recipient/use statement (job RECIPIENT_OCCASION_USE).",
             incremental="Recipient concept is VERIFIED (keyword audience = nurses); occasion remains "
                         "OWNER_FACT_REQUIRED.",
             extra_reasons=["BULLET_JOB_RECIPIENT_VERIFIED"])
        field_status["bullet_5"] = {"assignment_count": len(fields["bullet_5"]), "empty": False,
                                   "bullet_job": BULLET_JOB["bullet_5"],
                                   "note": "Recipient verified; occasion concept is OWNER_FACT_REQUIRED.",
                                   "owner_fact_required_concepts": ["occasion"]}
    else:
        field_status["bullet_5"] = _empty_field_status(EMPTY_NO_SAFE_TERM, "No safe recipient term.",
                                                      bullet_job=BULLET_JOB["bullet_5"])

    # --- description: broader safe set, distinct roots, capped ------------------------------------
    desc_cap = FIELD_MAX["description"]
    for it in canonical:
        if len(fields["description"]) >= desc_cap:
            break
        if it["keyword_id"] in assigned_ids or it["normalized_root"] in used_visible_roots:
            continue
        take(it, "description", "Safe incremental concept for the description body.",
             incremental="Distinct market-identity concept; adds description coverage without unsupported claims.",
             extra_reasons=["DESCRIPTION_INCREMENTAL"])
    if fields["description"]:
        field_status["description"] = {"assignment_count": len(fields["description"]), "empty": False,
                                      "note": "Distinct safe concepts only; not a keyword dump; respects the 8-concept limit."}
    else:
        field_status["description"] = _empty_field_status(EMPTY_NO_INCREMENTAL,
                                                         "No distinct safe concept left for the description.")

    # --- backend_candidates: safe distinct-root terms NOT surfaced in any visible field ------------
    for it in canonical:
        if it["keyword_id"] in assigned_ids:
            continue
        if it["normalized_root"] in used_visible_roots:
            continue
        take(it, "backend_candidates",
             "Hidden-search candidate (final backend authority remains the Phase 5 backend optimizer).",
             incremental="Safe market-identity concept not surfaced in visible copy; adds discoverability.",
             extra_reasons=["BACKEND_INCREMENTAL_HIDDEN"])
    if fields["backend_candidates"]:
        field_status["backend_candidates"] = {
            "assignment_count": len(fields["backend_candidates"]), "empty": False,
            "note": "Candidates only; byte ceiling + phrase-unit integrity enforced later by the backend optimizer.",
            "downstream_authority": "listing.backend_optimizer.optimize_backend"}
    else:
        field_status["backend_candidates"] = _empty_field_status(EMPTY_NO_INCREMENTAL,
                                                                "Every safe concept already surfaced in visible copy.")

    # --- item_highlights: category-supported for apparel but evidence-gated ------------------------
    if not dep.category_policy.supports_item_highlights():
        field_status["item_highlights"] = _empty_field_status(
            EMPTY_CATEGORY_UNSUPPORTED, "Category does not expose the item_highlights catalog field.")
    else:
        field_status["item_highlights"] = _empty_field_status(
            EMPTY_CLAIM_EVIDENCE_MISSING,
            "Category supports item highlights, but no verified product-attribute claim exists to anchor one.",
            downstream_authority="listing.item_highlights_builder.build_item_highlights_content")

    # --- aplus: capability + evidence gated -> empty ----------------------------------------------
    field_status["aplus"] = _empty_field_status(
        EMPTY_OWNER_FACT_REQUIRED,
        "A+ capability is UNKNOWN and no verified module evidence exists; A+ remains for a later stage.",
        downstream_authority="listing.aplus_builder.build_aplus")

    # --- listing_image_text: advisory overlay planning metadata (safe identity only) --------------
    if primary:
        take(primary, "listing_image_text",
             "Advisory overlay-text planning metadata (no image is produced in 6B).",
             reuse="Distinct creative field type: overlay copy, not visible listing copy.",
             incremental="Safe product identity overlay; no unsupported physical/overlay claim.",
             extra_reasons=["CREATIVE_ADVISORY"])
        field_status["listing_image_text"] = {"assignment_count": 1, "empty": False,
                                              "note": "Advisory only; no unsupported overlay claim; no image prompt created."}
    else:
        field_status["listing_image_text"] = _empty_field_status(EMPTY_NO_SAFE_TERM,
                                                                "No safe identity term for overlay planning.")

    # --- image_alt_text: factual advisory ---------------------------------------------------------
    if primary:
        take(primary, "image_alt_text",
             "Advisory alt-text planning metadata (factual product identity).",
             reuse="Distinct creative field type: accessibility alt text.",
             incremental="Factual product identity; no keyword stuffing; no unsupported physical fact.",
             extra_reasons=["CREATIVE_ALT_ADVISORY"])
        field_status["image_alt_text"] = {"assignment_count": 1, "empty": False,
                                         "note": "Factual identity only; no stuffing; final alt text produced later."}
    else:
        field_status["image_alt_text"] = _empty_field_status(EMPTY_NO_SAFE_TERM,
                                                            "No safe identity term for alt-text planning.")

    # ensure every field has a status, then present fields/status in the exact canonical field order.
    for k in FIELD_KEYS:
        field_status.setdefault(k, _empty_field_status(EMPTY_NOT_APPLICABLE, "No allocation."))
    fields = {k: fields[k] for k in FIELD_KEYS}
    field_status = {k: field_status[k] for k in FIELD_KEYS}

    empty_fields = {k: field_status[k]["empty_reason_code"] for k in FIELD_KEYS if field_status[k].get("empty")}

    doc = {
        "schema_version": ALLOCATION_MAP_SCHEMA_VERSION,
        "workspace_id": dep.workspace_id,
        "product_id": dep.product_id,
        "product_type": dep.product_type,
        "audience": dep.audience,
        "category": dep.category,
        "source_keyword_sha256": dep.keyword_source_sha256,
        "phase6a_manifest_sha256": dep.phase6a_manifest_sha256,
        "product_facts_sha256": dep.product_facts_sha256,
        "claim_evidence_sha256": dep.claim_evidence_sha256,
        "ranking_policy_version": RANKING_POLICY_VERSION,
        "allocation_schema_version": ALLOCATION_SCHEMA_VERSION,
        "listing_publishability_state": LISTING_SAFE_DRAFT,
        "downstream_authorization": ADVISORY_ONLY,
        # JSON canonicalization sorts object keys, so field_order is the AUTHORITY for the exact 13
        # ordered field keys (the `fields`/`field_status` objects carry the values).
        "field_order": list(FIELD_KEYS),
        "field_type": {k: FIELD_TYPE[k] for k in FIELD_KEYS},
        "field_max_assignments": {k: FIELD_MAX[k] for k in FIELD_KEYS},
        "fields": fields,
        "field_status": field_status,
        "empty_fields": empty_fields,
    }
    # canonical hash over the allocation content (deterministic).
    doc["deterministic_content_sha256"] = content_sha256(_alloc_hashable(doc))
    return doc, {"canonical": canonical, "dup_variants": dup_variants, "assigned_ids": assigned_ids,
                 "assigned_roots": assigned_roots}


def _alloc_hashable(doc):
    return {k: v for k, v in doc.items() if k != "deterministic_content_sha256"}


# ================================================================ unallocated eligible pool
def build_unallocated_eligible_pool(dep, top_relevance, alloc_ctx):
    """Eligible SAFE terms that were NOT assigned to any field, each with an explicit reason.

    Never contains rejected / risky / wrong-product / wrong-audience / brand-IP / gated terms — those
    belong to the excluded summary.
    """
    assigned_ids = alloc_ctx["assigned_ids"]
    by_id = {it["keyword_id"]: it for it in top_relevance["keywords"]}
    records = []
    for it, canonical_it in alloc_ctx["dup_variants"]:
        if it["keyword_id"] in assigned_ids:
            continue
        records.append(_unalloc_record(it, NA_BETTER_VARIANT,
                                       f"A stronger same-root variant '{canonical_it['phrase']}' was selected "
                                       "(word-order / plural consolidation).",
                                       competing=[canonical_it["keyword_id"]]))
    # any canonical term left unassigned (defensive; normally backend absorbs all distinct roots).
    for it in top_relevance["keywords"]:
        if it["keyword_id"] in assigned_ids:
            continue
        if any(v["keyword_id"] == it["keyword_id"] for v in records):
            continue
        records.append(_unalloc_record(it, NA_NO_PURPOSE,
                                       "Eligible and safe, but no field offered a distinct purpose for it."))
    records.sort(key=lambda r: (-r["relevance_score"], r["source_rank"], r["normalized_phrase"]))
    doc = {
        "schema_version": UNALLOCATED_SCHEMA_VERSION,
        "workspace_id": dep.workspace_id,
        "product_id": dep.product_id,
        "source_keyword_sha256": dep.keyword_source_sha256,
        "count": len(records),
        "keywords": records,
    }
    return doc


def _unalloc_record(it, reason_code, reason, competing=None):
    return {
        "keyword_id": it["keyword_id"],
        "phrase": it["phrase"],
        "normalized_phrase": it["normalized_phrase"],
        "source_tier": it["source_tier"],
        "source_rank": it["source_rank"],
        "relevance_score": it["relevance_score"],
        "concept_id": it["concept_id"],
        "normalized_root": it["normalized_root"],
        "eligible_fields": ["backend_candidates", "description"],
        "non_allocation_reason_code": reason_code,
        "non_allocation_reason": reason,
        "competing_assignment_ids": competing or [],
        "source_keyword_sha256": it["source_keyword_sha256"],
    }


# ================================================================ excluded summary + coverage
def build_excluded_summary(dep, gated, source_ineligible):
    by_reason = {}
    for g in gated:
        code = g["gate_reason_code"]
        slot = by_reason.setdefault(code, {"count": 0, "examples": [], "concepts": set()})
        slot["count"] += 1
        if g.get("unsupported_concept"):
            slot["concepts"].add(g["unsupported_concept"])
        if len(slot["examples"]) < _MAX_EXAMPLES:
            slot["examples"].append(g["phrase"])
    phase6b_gated = {c: {"count": v["count"], "example_phrases": v["examples"],
                         "asserted_concepts": sorted(v["concepts"])} for c, v in sorted(by_reason.items())}
    source_ineligible_out = {c: {"count": v["count"], "example_phrases": v["examples"]}
                             for c, v in sorted(source_ineligible.items())}
    total = sum(v["count"] for v in phase6b_gated.values()) + \
        sum(v["count"] for v in source_ineligible_out.values())
    return {
        "phase6b_gated_owner_fact_required": phase6b_gated,
        "source_ineligible": source_ineligible_out,
        "total_excluded": total,
        "note": "Gated terms are eligible-from-source but assert an unverified physical fact; they are "
                "held (not hidden as publishable) until the owner supplies verified facts.",
    }


def build_coverage(dep, keyword_source, top_relevance, allocation, unallocated, excluded, safe, gated):
    fields = allocation["fields"]
    by_field = {k: len(v) for k, v in fields.items()}
    empty_fields = allocation["empty_fields"]
    root_all = [it["normalized_root"] for it in top_relevance["keywords"]]
    distinct_roots = sorted(set(root_all))
    # leakage: any allocated term asserting a gated token = leakage (must be zero).
    leakage = _leakage_counts(fields)
    return {
        "source_eligible_count": len(keyword_source.eligible_keywords()),
        "phase6b_eligible_safe_count": len(safe),
        "gated_owner_fact_required_count": len(gated),
        "selected_top_relevance_count": len(top_relevance["keywords"]),
        "allocated_assignment_count": sum(by_field.values()),
        "assignment_count_by_field": by_field,
        "empty_fields": empty_fields,
        "unallocated_eligible_count": unallocated["count"],
        "excluded_total": excluded["total_excluded"],
        "excluded_counts_by_reason": {**{k: v["count"] for k, v in excluded["source_ineligible"].items()},
                                      **{k: v["count"] for k, v in
                                         excluded["phase6b_gated_owner_fact_required"].items()}},
        "concept_coverage": {"distinct_concept_roots": len(distinct_roots),
                             "total_safe_terms": len(safe)},
        "root_duplication": {"distinct_roots": len(distinct_roots),
                            "safe_terms": len(safe),
                            "duplicate_variants": len(safe) - len(distinct_roots)},
        "product_identity_coverage": sum(1 for it in top_relevance["keywords"] if "P" in it["_profile"]),
        "audience_coverage": sum(1 for it in top_relevance["keywords"] if "A" in it["_profile"]),
        "recipient_coverage": sum(1 for it in top_relevance["keywords"] if "R" in it["_profile"]),
        "occasion_handling": "No occasion asserted in any allocated term; occasion concept is OWNER_FACT_REQUIRED.",
        "leakage": leakage,
        "tier_a_count": keyword_source.tier_counts.get(KSA.TIER_A, 0),
        "tier_b_count": keyword_source.tier_counts.get(KSA.TIER_B, 0),
    }


def _leakage_counts(fields):
    """Every allocated term must be pure market identity; any gated token is a leak (must be zero)."""
    counts = {"audience": 0, "product": 0, "occasion": 0, "brand_ip": 0, "unsupported_physical_claim": 0}
    for _field, assigns in fields.items():
        for a in assigns:
            verdict, _concept, _reason = _classify_keyword({"keyword_normalized": a["normalized_phrase"]})
            if verdict == "GATED":
                counts["unsupported_physical_claim"] += 1
    return counts


# ================================================================ result object
class KeywordAllocationResult:
    """The single Phase 6B allocation result — advisory planning metadata, never publication."""

    def __init__(self, *, dep, keyword_source, top_relevance, allocation, unallocated, excluded,
                 coverage, safe, gated, warnings=None, blockers=None):
        self.schema_version = SCHEMA_VERSION
        self.workspace_id = dep.workspace_id
        self.product_id = dep.product_id
        self.category = dep.category
        self.product_type = dep.product_type
        self.audience = dep.audience
        self.source_keyword_sha256 = dep.keyword_source_sha256
        self.phase6a_manifest_sha256 = dep.phase6a_manifest_sha256
        self.product_facts_sha256 = dep.product_facts_sha256
        self.claim_evidence_sha256 = dep.claim_evidence_sha256
        self.ranking_policy_version = RANKING_POLICY_VERSION
        self.allocation_schema_version = ALLOCATION_SCHEMA_VERSION
        self.listing_publishability_state = LISTING_SAFE_DRAFT
        self.top_relevance_keywords = top_relevance
        self.allocation = allocation
        self.fields = allocation["fields"]
        self.field_status = allocation["field_status"]
        self.unallocated_eligible = unallocated
        self.excluded_summary = excluded
        self.coverage = coverage
        self.blockers = list(blockers or [])
        self.warnings = list(warnings or [])
        self.source_files = list(dep.source_files or ["MASTER-KEYWORDS-LEAN.json"])
        self._safe = safe
        self._gated = gated
        self.stage_state = SAFE_ALLOCATION_OWNER_FACTS_REQUIRED
        self.ready_for_6c = True
        self.generated_artifacts = list(REQUIRED_ARTIFACTS) + [STAGE_MANIFEST_FILENAME]

    def canonical_content(self):
        return {
            "schema_version": self.schema_version,
            "workspace_id": self.workspace_id,
            "product_id": self.product_id,
            "category": self.category,
            "product_type": self.product_type,
            "audience": self.audience,
            "source_keyword_sha256": self.source_keyword_sha256,
            "phase6a_manifest_sha256": self.phase6a_manifest_sha256,
            "product_facts_sha256": self.product_facts_sha256,
            "claim_evidence_sha256": self.claim_evidence_sha256,
            "ranking_policy_version": self.ranking_policy_version,
            "allocation_schema_version": self.allocation_schema_version,
            "listing_publishability_state": self.listing_publishability_state,
            "stage_state": self.stage_state,
            "ready_for_6c": self.ready_for_6c,
            "top_relevance_keywords": _strip_private(self.top_relevance_keywords),
            "fields": self.fields,
            "field_status": self.field_status,
            "unallocated_eligible": self.unallocated_eligible,
            "excluded_summary": self.excluded_summary,
            "coverage": self.coverage,
            "blockers": self.blockers,
            "warnings": self.warnings,
        }

    def deterministic_content_sha256(self):
        return content_sha256(self.canonical_content())

    def to_dict(self):
        d = self.canonical_content()
        d["deterministic_content_sha256"] = self.deterministic_content_sha256()
        d["generated_artifacts"] = list(self.generated_artifacts)
        return d


def _strip_private(top_relevance):
    """Drop internal helper keys (leading underscore) from the persisted top-relevance doc."""
    doc = dict(top_relevance)
    doc["keywords"] = [{k: v for k, v in it.items() if not k.startswith("_")} for it in top_relevance["keywords"]]
    return doc


# ================================================================ orchestration
def plan_keyword_allocation(run_dir):
    """Full Phase 6B plan for a completed 6A workspace. Deterministic; frozen local source only."""
    dep = load_phase6a_dependency(run_dir)
    src = KSA.load_keyword_source(run_dir)
    safe, gated, source_ineligible = _build_working_records(dep, src)
    top = build_top_relevance_keywords(dep, src, safe=safe)
    allocation, ctx = build_keyword_allocation_map(dep, top, src, gated=gated)
    unallocated = build_unallocated_eligible_pool(dep, top, ctx)
    excluded = build_excluded_summary(dep, gated, source_ineligible)
    coverage = build_coverage(dep, src, top, allocation, unallocated, excluded, safe, gated)
    warnings = []
    if dep.listing_publishability_state != LISTING_SAFE_DRAFT:
        warnings.append(f"6A listing publishability was {dep.listing_publishability_state!r}")
    return KeywordAllocationResult(dep=dep, keyword_source=src, top_relevance=top, allocation=allocation,
                                   unallocated=unallocated, excluded=excluded, coverage=coverage,
                                   safe=safe, gated=gated, warnings=warnings)


# ================================================================ coverage report (markdown)
def build_keyword_coverage_report(result):
    c = result.coverage
    tr = result.top_relevance_keywords
    L = []
    A = L.append
    A("# Keyword Coverage Report — Phase 6B")
    A("")
    A("## 1. Workspace identity")
    A(f"- Workspace / product id: `{result.workspace_id}`")
    A(f"- Category: `{result.category}`  ·  Product type: `{result.product_type}`  ·  Audience: `{result.audience}`")
    A(f"- Stage state: `{result.stage_state}`")
    A("")
    A("## 2. Source keyword file and hash")
    A(f"- Source SHA-256: `{result.source_keyword_sha256}`")
    A(f"- Tier A: {c['tier_a_count']}  ·  Tier B: {c['tier_b_count']}  ·  source-eligible: {c['source_eligible_count']}")
    A("")
    A("## 3. Phase 6A dependency status")
    A(f"- 6A manifest SHA-256: `{result.phase6a_manifest_sha256}`")
    A(f"- Product facts SHA-256: `{result.product_facts_sha256}`")
    A(f"- Claim evidence SHA-256: `{result.claim_evidence_sha256}`")
    A(f"- Listing publishability: `{result.listing_publishability_state}` (unchanged by 6B)")
    A("")
    A("## 4. Ranking-policy version")
    A(f"- `{result.ranking_policy_version}` / allocation schema `{result.allocation_schema_version}`")
    A("")
    A("## 5. Eligible keyword count")
    A(f"- Source-eligible (Tier A+B): {c['source_eligible_count']}")
    A(f"- Phase 6B eligible SAFE (market identity): {c['phase6b_eligible_safe_count']}")
    A(f"- Gated OWNER_FACT_REQUIRED (assert unverified facts): {c['gated_owner_fact_required_count']}")
    A("")
    A("## 6. Selected top-relevance count")
    A(f"- {c['selected_top_relevance_count']} ranked safe keywords")
    A("")
    A("## 7. Allocation count by field")
    for k in FIELD_KEYS:
        A(f"- `{k}` ({FIELD_TYPE[k]}): {c['assignment_count_by_field'][k]}")
    A("")
    A("## 8. Empty fields and reasons")
    if c["empty_fields"]:
        for k, reason in c["empty_fields"].items():
            A(f"- `{k}`: {reason}")
    else:
        A("- (none)")
    A("")
    A("## 9. Unallocated eligible count")
    A(f"- {c['unallocated_eligible_count']} safe terms held (word-order/plural consolidation etc.)")
    A("")
    A("## 10. Excluded counts by reason")
    for reason, n in sorted(c["excluded_counts_by_reason"].items()):
        A(f"- `{reason}`: {n}")
    A(f"- total excluded: {c['excluded_total']}")
    A("")
    A("## 11. Concept coverage")
    A(f"- distinct concept roots: {c['concept_coverage']['distinct_concept_roots']} "
      f"across {c['concept_coverage']['total_safe_terms']} safe terms")
    A("")
    A("## 12. Root duplication analysis")
    A(f"- distinct roots {c['root_duplication']['distinct_roots']} · duplicate variants "
      f"{c['root_duplication']['duplicate_variants']} (consolidated to strongest)")
    A("")
    A("## 13. Product identity coverage")
    A(f"- {c['product_identity_coverage']} safe terms carry the product family")
    A("")
    A("## 14. Audience coverage")
    A(f"- {c['audience_coverage']} safe terms carry the nurse audience · recipient {c['recipient_coverage']}")
    A("")
    A("## 15. Occasion handling")
    A(f"- {c['occasion_handling']}")
    A("")
    A("## 16. Physical-claim restrictions")
    A("- No allocated term asserts material, decoration, color, size, fit, personalization, care, or "
      "occasion. Every such keyword is held as OWNER_FACT_REQUIRED.")
    A("")
    A("## 17. Owner-fact dependencies")
    A(f"- {c['gated_owner_fact_required_count']} eligible keywords require owner facts before they may "
      "reach visible copy (see excluded summary).")
    A("")
    A("## 18. Real-asset dependencies")
    A("- Decoration / material / personalization / measurement claims additionally require REAL photo "
      "proof (Phase 6A REAL-ASSET requirements); AI mockups are never accepted.")
    A("")
    A("## 19. Risk and IP exclusions")
    ip = c["excluded_counts_by_reason"].get("BRAND_IP_BLOCKED", 0)
    A(f"- Brand/IP-blocked source terms: {ip}. Zero brand/IP terms are allocated.")
    A("")
    A("## 20. Phase 6C handoff guidance")
    A("- This allocation is ADVISORY planning metadata. Phase 6C must feed it through the existing "
      "title / bullet / description / backend / item-highlights / A+ engines, then PageAuditor.")
    A("- The engines remain authoritative; they will re-gate every concept on claim evidence.")
    A("")
    A("## 21. Safety statement")
    A("- Allocations are advisory; Phase 6C engines remain authoritative; product facts remain "
      "authoritative; claim evidence remains authoritative; PageAuditor remains final.")
    A(f"- The listing is still `{result.listing_publishability_state}`. No copy was generated in Phase 6B.")
    A("- No external request, connected service, or Amazon-account action occurred.")
    A("")
    return "\n".join(L)


# ================================================================ stage manifest
_MANIFEST_VOLATILE = ("started_at", "completed_at", "previous_valid_manifest_sha256", "verification",
                      "errors")


def _manifest_hashable(body):
    return {k: v for k, v in body.items() if k not in _MANIFEST_VOLATILE}


def _stage_manifest_doc(result, dest_dir, workspace_dir, output_hashes, *, started_at=None,
                        completed_at=None, previous_valid_manifest_sha256=None, verification=None,
                        errors=None):
    c = result.coverage
    body = {
        "schema_version": STAGE_MANIFEST_SCHEMA_VERSION,
        "stage_id": STAGE_ID,
        "stage_name": STAGE_NAME,
        "workspace_id": result.workspace_id,
        "product_id": result.product_id,
        "state": result.stage_state,
        "listing_publishability_state": result.listing_publishability_state,
        "required_inputs": sorted(os.path.basename(f) for f in result.source_files),
        "input_hashes": {
            "source_keyword_sha256": result.source_keyword_sha256,
            "phase6a_manifest_sha256": result.phase6a_manifest_sha256,
            "product_facts_sha256": result.product_facts_sha256,
            "claim_evidence_sha256": result.claim_evidence_sha256,
        },
        "phase6a_manifest_sha256": result.phase6a_manifest_sha256,
        "source_keyword_sha256": result.source_keyword_sha256,
        "ranking_policy_version": result.ranking_policy_version,
        "allocation_schema_version": result.allocation_schema_version,
        "field_count": len(FIELD_KEYS),
        "field_order": list(FIELD_KEYS),
        "generated_outputs": sorted(output_hashes.keys()),
        "output_hashes": output_hashes,
        "eligible_count": c["phase6b_eligible_safe_count"],
        "selected_count": c["selected_top_relevance_count"],
        "allocated_assignment_count": c["allocated_assignment_count"],
        "unallocated_eligible_count": c["unallocated_eligible_count"],
        "excluded_count": c["excluded_total"],
        "empty_field_count": len(c["empty_fields"]),
        "blocker_count": len(result.blockers),
        "warning_count": len(result.warnings),
    }
    body["deterministic_content_sha256"] = content_sha256(_manifest_hashable(body))
    body["previous_valid_manifest_sha256"] = previous_valid_manifest_sha256
    body["verification"] = verification or {}
    body["errors"] = errors or []
    if started_at is not None:
        body["started_at"] = started_at
    if completed_at is not None:
        body["completed_at"] = completed_at
    return body


def _previous_manifest_hash(dest_dir):
    p = os.path.join(dest_dir, STAGE_MANIFEST_FILENAME)
    if not os.path.exists(p):
        return None
    try:
        return _load_json(p).get("deterministic_content_sha256")
    except Exception:
        return None


# ================================================================ validators
def validate_top_relevance_keywords(doc):
    errors = []
    if doc.get("schema_version") != TOP_RELEVANCE_SCHEMA_VERSION:
        errors.append("bad top-relevance schema_version")
    if not doc.get("source_keyword_sha256"):
        errors.append("missing source_keyword_sha256")
    kws = doc.get("keywords", [])
    ids = [k.get("keyword_id") for k in kws]
    if len(ids) != len(set(ids)):
        errors.append("duplicate keyword ids in top-relevance")
    ordered = sorted(kws, key=lambda k: (-k["relevance_score"], k["source_rank"], k["normalized_phrase"]))
    if [k["keyword_id"] for k in kws] != [k["keyword_id"] for k in ordered]:
        errors.append("top-relevance not in deterministic order")
    for k in kws:
        if not k.get("score_components"):
            errors.append(f"missing score_components for {k.get('keyword_id')}")
        if not k.get("inclusion_reason_codes"):
            errors.append(f"missing inclusion reasons for {k.get('keyword_id')}")
        v, _c, _r = _classify_keyword({"keyword_normalized": k["normalized_phrase"]})
        if v != "SAFE":
            errors.append(f"non-safe term selected into top-relevance: {k.get('keyword_id')}")
    return errors


def validate_keyword_allocation_map(doc, dep=None):
    errors = []
    if doc.get("schema_version") != ALLOCATION_MAP_SCHEMA_VERSION:
        errors.append("bad allocation schema_version")
    fields = doc.get("fields") or {}
    status = doc.get("field_status") or {}
    # field_order is the canonical authority for the exact 13 ordered keys (JSON sorts object keys).
    if doc.get("field_order") != list(FIELD_KEYS):
        errors.append("field_order is not the exact 13 field keys in order")
    if set(fields.keys()) != set(FIELD_KEYS):
        errors.append("fields keys are not exactly the 13 field keys (missing/extra/unknown)")
    if set(status.keys()) != set(FIELD_KEYS):
        errors.append("field_status keys are not exactly the 13 field keys (missing/extra/unknown)")
    if len(fields) != len(FIELD_KEYS) or len(status) != len(FIELD_KEYS):
        errors.append("field / field_status count is not exactly 13")
    if "unallocated_eligible" in fields:
        errors.append("unallocated_eligible must not be a field")
    seen_aids = set()
    for k in FIELD_KEYS:
        assigns = fields.get(k, [])
        mx = FIELD_MAX[k]
        if mx is not None and len(assigns) > mx:
            errors.append(f"field {k} exceeds max_assignments {mx}")
        roots = {}
        for a in assigns:
            if a["assignment_id"] in seen_aids:
                errors.append(f"duplicate assignment_id {a['assignment_id']}")
            seen_aids.add(a["assignment_id"])
            if a["target_field"] != k:
                errors.append(f"assignment target_field {a['target_field']} != container {k}")
            if not a.get("inclusion_reason_codes"):
                errors.append(f"assignment missing inclusion reasons in {k}")
            v, _c, _r = _classify_keyword({"keyword_normalized": a["normalized_phrase"]})
            if v != "SAFE":
                errors.append(f"blocked/unsupported term assigned to {k}: {a['phrase']}")
            root = a["normalized_root"]
            roots[root] = roots.get(root, 0) + 1
            if roots[root] > 1:
                errors.append(f"duplicate root within field {k}: {root}")
        st = status.get(k, {})
        if not assigns and not st.get("empty"):
            errors.append(f"empty field {k} not marked empty")
        if not assigns and not st.get("empty_reason_code"):
            errors.append(f"empty field {k} missing reason")
    recomputed = content_sha256(_alloc_hashable(doc))
    if recomputed != doc.get("deterministic_content_sha256"):
        errors.append("allocation deterministic hash does not recompute")
    return errors


def validate_unallocated_eligible_pool(doc, top_relevance=None):
    errors = []
    if doc.get("schema_version") != UNALLOCATED_SCHEMA_VERSION:
        errors.append("bad unallocated schema_version")
    recs = doc.get("keywords", [])
    for r in recs:
        if not r.get("non_allocation_reason_code"):
            errors.append(f"unallocated missing reason: {r.get('keyword_id')}")
        v, _c, _r = _classify_keyword({"keyword_normalized": r["normalized_phrase"]})
        if v != "SAFE":
            errors.append(f"non-eligible term in unallocated pool: {r.get('phrase')}")
    ordered = sorted(recs, key=lambda r: (-r["relevance_score"], r["source_rank"], r["normalized_phrase"]))
    if [r["keyword_id"] for r in recs] != [r["keyword_id"] for r in ordered]:
        errors.append("unallocated pool not in deterministic order")
    if doc.get("count") != len(recs):
        errors.append("unallocated count mismatch")
    return errors


def validate_stage6b_manifest(doc, workspace_dir):
    errors = []
    if doc.get("schema_version") != STAGE_MANIFEST_SCHEMA_VERSION:
        errors.append("bad manifest schema_version")
    if doc.get("field_order") != list(FIELD_KEYS):
        errors.append("manifest field_order not exact 13")
    if doc.get("field_count") != len(FIELD_KEYS):
        errors.append("manifest field_count != 13")
    seen = set()
    for rel, expected in (doc.get("output_hashes") or {}).items():
        if os.path.isabs(rel) or rel.startswith("../") or ".." in rel.split("/"):
            errors.append(f"unsafe manifest path: {rel}")
        if rel in seen:
            errors.append(f"duplicate manifest path: {rel}")
        seen.add(rel)
        if STAGE_MANIFEST_FILENAME in rel:
            errors.append("manifest must not hash itself")
    for bad in ("PRODUCT-DETAIL-PAGE.json", "TITLE-OPTIONS.json", "BULLETS.json", "DESCRIPTION.txt",
                "BASIC-APLUS-CONTENT.json", "PREMIUM-APLUS-DRAFT.json", "LISTING-IMAGE-PLAN.json",
                "PACKAGE-INDEX.json"):
        if any(bad in rel for rel in (doc.get("output_hashes") or {})):
            errors.append(f"forbidden downstream artifact present: {bad}")
    return errors


# ================================================================ write / verify artifacts
def write_phase6b_artifacts(result, dest_dir=None, *, workspace_dir=None, started_at=None,
                            completed_at=None):
    """Build every document + exact bytes in memory, validate all, THEN commit atomically.

    A failed run never overwrites the last valid Phase 6B package (validate-before-disk + atomic
    replace). Returns the manifest dict.
    """
    workspace_dir = workspace_dir or os.path.join(_ROOT, "runs", result.workspace_id)
    dest_dir = dest_dir or phase6b_dir(workspace_dir)

    top_doc = _strip_private(result.top_relevance_keywords)
    alloc_doc = result.allocation
    unalloc_doc = result.unallocated_eligible
    report_md = build_keyword_coverage_report(result)

    # phase 1 — exact final bytes in memory (fixed order).
    content = [
        (TOP_RELEVANCE_FILENAME, canonical_json(top_doc)),
        (ALLOCATION_MAP_FILENAME, canonical_json(alloc_doc)),
        (COVERAGE_REPORT_FILENAME, report_md),
        (UNALLOCATED_FILENAME, canonical_json(unalloc_doc)),
    ]
    output_hashes = {}
    for name, text in content:
        rel = _safe_rel(os.path.join(dest_dir, name), workspace_dir)
        output_hashes[rel] = hashlib.sha256(text.encode("utf-8")).hexdigest()

    prev = _previous_manifest_hash(dest_dir)
    manifest = _stage_manifest_doc(result, dest_dir, workspace_dir, output_hashes,
                                   started_at=started_at, completed_at=completed_at,
                                   previous_valid_manifest_sha256=prev)

    # phase 2 — validate everything BEFORE touching disk (preserves last valid on failure).
    _raise_on_errors("top-relevance", validate_top_relevance_keywords(top_doc))
    _raise_on_errors("allocation-map", validate_keyword_allocation_map(alloc_doc))
    _raise_on_errors("unallocated-pool", validate_unallocated_eligible_pool(unalloc_doc))
    _raise_on_errors("stage-manifest", validate_stage6b_manifest(manifest, workspace_dir))

    # phase 3 — commit atomically.
    os.makedirs(dest_dir, exist_ok=True)
    for name, text in content:
        _atomic_write_text(os.path.join(dest_dir, name), text)
    _atomic_write_json(os.path.join(dest_dir, STAGE_MANIFEST_FILENAME), manifest)
    return manifest


def _raise_on_errors(label, errors):
    if errors:
        raise KeywordAllocationError(f"{label} failed validation: " + "; ".join(errors))


def verify_phase6b_artifacts(dest_dir, workspace_dir=None):
    """Re-read written artifacts and verify manifest hashes match final bytes; no absolute paths /
    traversal / duplicates; every required artifact present."""
    workspace_dir = workspace_dir or os.path.dirname(os.path.dirname(os.path.abspath(dest_dir)))
    errors = []
    for name in REQUIRED_ARTIFACTS:
        if not os.path.exists(os.path.join(dest_dir, name)):
            errors.append(f"missing required artifact: {name}")
    man_path = os.path.join(dest_dir, STAGE_MANIFEST_FILENAME)
    if not os.path.exists(man_path):
        errors.append("missing STAGE-6B-MANIFEST.json")
        return {"ok": False, "errors": errors, "checked": []}
    manifest = _load_json(man_path)
    errors.extend(validate_stage6b_manifest(manifest, workspace_dir))
    checked = []
    for rel, expected in (manifest.get("output_hashes") or {}).items():
        p = os.path.join(workspace_dir, rel)
        if not os.path.exists(p):
            errors.append(f"manifest references missing output: {rel}")
            continue
        actual = _file_sha256(p)
        checked.append(rel)
        if actual != expected:
            errors.append(f"hash mismatch for {rel}: manifest {expected[:12]} != actual {actual[:12]}")
    return {"ok": not errors, "errors": errors, "checked": sorted(checked)}


# ================================================================ CLI
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Phase 6B — deterministic keyword allocation planner")
    ap.add_argument("run_dir", nargs="?", default=os.path.join(_ROOT, "runs", "T2"))
    ap.add_argument("--started-at", default=None)
    ap.add_argument("--completed-at", default=None)
    args = ap.parse_args(argv)
    result = plan_keyword_allocation(args.run_dir)
    manifest = write_phase6b_artifacts(result, started_at=args.started_at, completed_at=args.completed_at)
    verdict = verify_phase6b_artifacts(phase6b_dir(args.run_dir), workspace_dir=args.run_dir)
    print(f"state={result.stage_state} eligible_safe={result.coverage['phase6b_eligible_safe_count']} "
          f"allocated={result.coverage['allocated_assignment_count']} verify_ok={verdict['ok']}")
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
