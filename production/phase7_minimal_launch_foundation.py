#!/usr/bin/env python3
"""production.phase7_minimal_launch_foundation — the ONE Phase 7.1M foundation authority.

Phase 7.1M builds ONLY the deterministic foundation that answers a single question:

  Given a verified live product and complete owner-confirmed economics, is this advertised SKU
  economically eligible to proceed to MANUAL campaign planning?

It contains exactly: (1) a Live Product State authority, (2) a PPC Product Contract authority and
validator, (3) a one-SKU Decimal economics engine (money math via ``core.money`` — never a float),
(4) capacity/handling safety, (5) budget-feasibility input validation + click-capacity bounds, and
(6) a most-restrictive readiness resolver. It writes deterministic local artifacts, a sanitized proof,
and a manifest.

It does NOT (and must never): select keywords/targets/negatives, choose match types or campaign roles,
compute starting bids, allocate campaign budgets, emit any PPC-LAUNCH-READY/CSV/plan, ingest reports,
optimize, learn, or touch Amazon in any way. The permanent Amazon boundary from Phase 6/7.0 is
re-asserted: no login, SP-API/MWS/Ads API, browser automation, credential/cookie/token store,
automated report retrieval, or public bind. External Amazon account attempts and actions equal zero.

THIN by construction — it reuses the existing authorities rather than re-implementing them:
  * canonical json / content hash / atomic write  -> production.product_workspace
  * Phase 6 handoff load + Phase 6F package verify -> production.phase7_preflight / seller_central_package
  * Amazon network-boundary report                 -> production.phase7_preflight
  * Decimal money parsing / serialization          -> core.money

Public API:
  build_live_product_state_template / validate_live_product_state
  build_ppc_product_contract        / validate_ppc_product_contract
  compute_ppc_economics
  evaluate_capacity_and_handling
  evaluate_budget_feasibility
  resolve_phase7_1m_readiness
  assemble_phase7_1m
  write_phase7_1m_candidate / verify_phase7_1m_candidate / promote_phase7_1m_candidate
  verify_phase7_1m_artifacts
  build_stage_manifest / build_proof_gate
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ("", "core", "listing", "scripts"):
    _p = _ROOT if not _sub else os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import product_workspace as PW          # noqa: E402  canonical json / hash / atomic
from production import seller_central_package as SCP     # noqa: E402  publishability + package verify
from production import phase7_preflight as P7            # noqa: E402  handoff + boundary + states
from core import money as MONEY                          # noqa: E402  Decimal money authority

canonical_json = PW.canonical_json
content_sha256 = PW.content_sha256

# ================================================================ constants / versions
STAGE_ID = "7.1M"
STAGE_NAME = "Live Product State, PPC Contract, Decimal Economics, Budget Feasibility, and Readiness Foundation"

SCHEMA_LIVE_STATE = "phase7-1m-live-product-state-v1"
SCHEMA_LIVE_STATE_TEMPLATE = "phase7-1m-live-product-state-template-v1"
SCHEMA_LIVE_STATE_VALIDATION = "phase7-1m-live-product-state-validation-v1"
SCHEMA_CONTRACT = "phase7-1m-ppc-product-contract-v1"
SCHEMA_CONTRACT_TEMPLATE = "phase7-1m-ppc-product-contract-template-v1"
SCHEMA_CONTRACT_VALIDATION = "phase7-1m-ppc-product-contract-validation-v1"
SCHEMA_ECONOMICS = "phase7-1m-ppc-economics-v1"
SCHEMA_ECONOMICS_TEMPLATE = "phase7-1m-ppc-economics-template-v1"
SCHEMA_ECONOMICS_VALIDATION = "phase7-1m-ppc-economics-validation-v1"
SCHEMA_CAPACITY = "phase7-1m-capacity-handling-assessment-v1"
SCHEMA_BUDGET = "phase7-1m-budget-feasibility-assessment-v1"
SCHEMA_READINESS = "phase7-1m-readiness-v1"
SCHEMA_VERIFICATION = "phase7-1m-verification-report-v1"
SCHEMA_STAGE_MANIFEST = "phase7-1m-stage-manifest-v1"
SCHEMA_PROOF_GATE = "session7_1m-proof-gate-v1"

CONNECTIVITY_MODES = P7.CONNECTIVITY_MODES

# per-doc volatile keys excluded from the deterministic content hash.
_VOLATILE = ("deterministic_content_sha256", "generated_at", "started_at", "completed_at",
             "repo_root", "run_dir", "git_head", "git_branch", "connectivity_mode")

# -- field states (reuse P7's vocabulary + one addition) -------------------------------------------
VERIFIED = P7.VERIFIED
MISSING = P7.MISSING
CONTRADICTORY = P7.CONTRADICTORY
NOT_APPLICABLE = P7.NOT_APPLICABLE
OWNER_REVIEW_REQUIRED = "OWNER_REVIEW_REQUIRED"
FIELD_STATES = (VERIFIED, MISSING, CONTRADICTORY, NOT_APPLICABLE, OWNER_REVIEW_REQUIRED)

# -- live product state overall states -------------------------------------------------------------
LIVE_STATE_VERIFIED = "LIVE_PRODUCT_STATE_VERIFIED"
LIVE_STATE_OWNER_INPUT = "LIVE_PRODUCT_STATE_OWNER_INPUT_REQUIRED"
LIVE_STATE_CONTRADICTORY = "LIVE_PRODUCT_STATE_CONTRADICTORY"
LIVE_STATE_INVALID = "LIVE_PRODUCT_STATE_INVALID"

# -- PPC product contract overall states -----------------------------------------------------------
CONTRACT_VERIFIED = "PPC_PRODUCT_CONTRACT_VERIFIED"
CONTRACT_OWNER_INPUT = "PPC_PRODUCT_CONTRACT_OWNER_INPUT_REQUIRED"
CONTRACT_PHASE6_BLOCKED = "PPC_PRODUCT_CONTRACT_PHASE6_BLOCKED"
CONTRACT_LIVE_STATE_BLOCKED = "PPC_PRODUCT_CONTRACT_LIVE_STATE_BLOCKED"
CONTRACT_CONTRADICTORY = "PPC_PRODUCT_CONTRACT_CONTRADICTORY"
CONTRACT_INVALID = "PPC_PRODUCT_CONTRACT_INVALID"

# -- economics overall states ----------------------------------------------------------------------
ECON_VIABLE = "PPC_ECONOMICS_VIABLE"
ECON_OWNER_INPUT = "PPC_ECONOMICS_OWNER_INPUT_REQUIRED"
ECON_NOT_VIABLE = "PPC_ECONOMICS_NOT_VIABLE"
ECON_INVALID = "PPC_ECONOMICS_INVALID"

# economics reason codes (hard blocks)
ECON_BLOCKS = ("MISSING_REQUIRED_INPUT", "INVALID_DECIMAL", "CURRENCY_MISMATCH",
               "NON_POSITIVE_NET_SALES", "NON_POSITIVE_CONTRIBUTION_MARGIN",
               "TARGET_AD_COST_NON_POSITIVE", "TARGET_ACOS_NOT_BELOW_BREAK_EVEN",
               "MAXIMUM_CPC_NON_POSITIVE", "CVR_OUT_OF_RANGE", "OWNER_CONFIRMATION_MISSING")

# -- capacity / handling states --------------------------------------------------------------------
CAPACITY_SAFE = "CAPACITY_VERIFIED_SAFE"
CAPACITY_OWNER_INPUT = "CAPACITY_OWNER_INPUT_REQUIRED"
CAPACITY_BLOCKED = "CAPACITY_BLOCKED"
HANDLING_SAFE = "HANDLING_VERIFIED_SAFE"
HANDLING_OWNER_INPUT = "HANDLING_OWNER_INPUT_REQUIRED"
HANDLING_BLOCKED = "HANDLING_BLOCKED"

# -- budget feasibility states ---------------------------------------------------------------------
BUDGET_OWNER_INPUT = "BUDGET_INPUT_OWNER_REQUIRED"
BUDGET_BLOCKED = "BUDGET_FEASIBILITY_BLOCKED"
BUDGET_PASSES = "BUDGET_FEASIBILITY_PASSES_FOR_PLANNING"
BUDGET_OWNER_REVIEW = "BUDGET_FEASIBILITY_OWNER_REVIEW"

# -- readiness (owner-facing) states ---------------------------------------------------------------
READY_PREFLIGHT_BLOCKED = "PHASE7_PREFLIGHT_BLOCKED"
READY_OWNER_INPUT = "PHASE7_OWNER_INPUT_REQUIRED"
READY_ECONOMICS_BLOCKED = "PHASE7_ECONOMICS_BLOCKED"
READY_FOUNDATION_REVIEW = "PHASE7_MINIMAL_FOUNDATION_READY_FOR_REVIEW"
READY_CAMPAIGN_PLANNING = "PHASE7_READY_FOR_CAMPAIGN_PLANNING"
# deliberately NEVER emitted this session:
NOT_THIS_SESSION_MANUAL_LAUNCH = "PHASE7_READY_FOR_MANUAL_LAUNCH"

# most-restrictive rank ladder (lower rank == more restrictive == wins).
_RANK = {
    READY_PREFLIGHT_BLOCKED: 0,
    READY_ECONOMICS_BLOCKED: 1,
    READY_OWNER_INPUT: 2,
    READY_FOUNDATION_REVIEW: 3,
    READY_CAMPAIGN_PLANNING: 4,
}
_RANK_TO_STATE = {v: k for k, v in _RANK.items()}

# -- session (final) statuses ----------------------------------------------------------------------
SESSION_FOUNDATION_READY = "SESSION7_1M_FOUNDATION_READY_FOR_REVIEW"
SESSION_OWNER_INPUT = READY_OWNER_INPUT
SESSION_ECONOMICS_BLOCKED = READY_ECONOMICS_BLOCKED
SESSION_PREFLIGHT_BLOCKED = "PHASE7_1M_PREFLIGHT_BLOCKED"
SESSION_BLOCKED = "SESSION7_1M_BLOCKED"

# the declared, owner-reviewable economics default (the ONLY allowed default; every other cost is
# required and never silently zero).
DEFAULT_MIN_PROFIT = "8.00"
DEFAULT_MIN_PROFIT_CURRENCY = "USD"
DEFAULT_MIN_PROFIT_SOURCE = "PHASE7_1M_DECLARED_DEFAULT_8_00_USD"

NA = "NOT_APPLICABLE"   # explicit not-applicable marker owners may set

# -- artifact filenames (exact) --------------------------------------------------------------------
F_LIVE_TEMPLATE = "LIVE-PRODUCT-STATE.template.json"
F_LIVE_VALIDATION = "LIVE-PRODUCT-STATE-VALIDATION.json"
F_CONTRACT_TEMPLATE = "PPC-PRODUCT-CONTRACT.template.json"
F_CONTRACT_VALIDATION = "PPC-PRODUCT-CONTRACT-VALIDATION.json"
F_ECON_TEMPLATE = "PPC-ECONOMICS.template.json"
F_ECON_VALIDATION = "PPC-ECONOMICS-VALIDATION.json"
F_CAPACITY = "CAPACITY-HANDLING-ASSESSMENT.json"
F_BUDGET = "BUDGET-FEASIBILITY-ASSESSMENT.json"
F_READINESS = "PHASE7-1M-READINESS.json"
F_VERIFICATION = "PHASE7-1M-VERIFICATION-REPORT.json"
F_STAGE_MANIFEST = "STAGE-7_1M-MANIFEST.json"
F_OWNER_INPUT = "PHASE7-1M-OWNER-INPUT-REQUIRED.md"
F_FOUNDATION_GUIDE = "PHASE7-1M-FOUNDATION-GUIDE.md"

REQUIRED_ARTIFACTS = (F_LIVE_TEMPLATE, F_LIVE_VALIDATION, F_CONTRACT_TEMPLATE, F_CONTRACT_VALIDATION,
                      F_ECON_TEMPLATE, F_ECON_VALIDATION, F_CAPACITY, F_BUDGET, F_READINESS,
                      F_VERIFICATION, F_STAGE_MANIFEST)

# credential / session material that must NEVER appear in a live state or contract.
_FORBIDDEN_SUBSTRINGS = ("cookie", "session_id", "sessionid", "token", "password", "passwd",
                         "secret", "credential", "sellercentral", "seller-central", "x-amz",
                         "mws_auth", "refresh_token", "access_token", "set-cookie", "authorization")


class Phase71MError(Exception):
    pass


# ================================================================ small helpers
def _finalize(doc, extra_volatile=()):
    volatile = set(_VOLATILE) | set(extra_volatile)
    doc["deterministic_content_sha256"] = content_sha256(
        {k: v for k, v in doc.items() if k not in volatile})
    return doc


def _sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _prefix(sha):
    return sha[:8] if isinstance(sha, str) and len(sha) >= 8 else sha


def _atomic_write_bytes(path, data):
    """temp sibling -> flush -> fsync -> os.replace. A failed write never overwrites the last valid file."""
    import tempfile
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-7-1m-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _contains_credentials(obj):
    """True if any key or string value in *obj* looks like credential / session / Amazon-account
    material. The contract holds owner CONFIRMATIONS and hashes only — never raw account data."""
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(k, str) and any(s in k.lower() for s in _FORBIDDEN_SUBSTRINGS):
                    return True
                if walk(v):
                    return True
        elif isinstance(o, list):
            for v in o:
                if walk(v):
                    return True
        elif isinstance(o, str):
            low = o.lower()
            # a Seller Central / account URL or an obvious token string is rejected.
            if "sellercentral.amazon" in low or "seller-central" in low:
                return True
            if low.startswith("atza|") or low.startswith("eyj") and len(o) > 40:  # SC token / JWT-ish
                return True
        return False
    return walk(obj)


def _is_tz_aware_timestamp(value):
    """A timezone-aware ISO-8601 timestamp string. No wall-clock is consulted (determinism)."""
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        dt = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return dt.tzinfo is not None and dt.utcoffset() is not None


# ================================================================ LIVE PRODUCT STATE AUTHORITY
# owner-confirmed real-world listing + fulfilment state. DISTINCT from the PPC contract, which binds
# this state to the verified Phase 6 package. Fields are never inferred from Phase 6 safe-draft copy.
LIVE_STATE_DERIVED_FIELDS = ("schema_version", "state_id", "workspace_id", "product_id", "marketplace")

# required owner confirmations (must be explicit True).
_MUST_BE_TRUE = ("listing_live_confirmed", "listing_buyable_confirmed", "listing_version_matches_phase6",
                 "inventory_or_capacity_confirmed", "handling_time_confirmed",
                 "advertising_eligibility_confirmed")
# required owner-supplied identity / value fields.
_LIVE_REQUIRED_VALUES = ("account_type", "advertised_asin", "advertised_sku", "advertised_variant_id",
                         "current_selling_price", "currency", "normal_capacity", "peak_capacity",
                         "current_backlog", "safe_handling_time", "featured_offer_applicability",
                         "owner_confirmed_at", "owner_confirmation_source")

LIVE_STATE_REQUIRED_FIELDS = tuple(sorted(set(_MUST_BE_TRUE) | set(_LIVE_REQUIRED_VALUES) |
                                          {"featured_offer_eligibility_confirmed"}))


def build_live_product_state_template(handoff):
    """A REQUIREMENTS template — never verified state. Derived identity fields are pre-filled from the
    verified Phase 6 handoff; every owner-confirmation field is null (never fabricated)."""
    tmpl = {
        "schema_version": SCHEMA_LIVE_STATE_TEMPLATE,
        "IS_TEMPLATE_NOT_VERIFIED_STATE": True,
        "instructions": "Owner completes every null field from LIVE Amazon state (screen-read by a "
                        "human, not automation). Do NOT paste credentials, cookies, tokens, session "
                        "data, or a Seller Central URL. A null field stays null until you confirm it.",
        "state_id": None,
        "workspace_id": handoff["workspace_id"],
        "product_id": handoff["product_id"],
        "marketplace": handoff["marketplace"],
        "account_type": None,
        "advertised_asin": None,
        "advertised_sku": None,
        "advertised_variant_id": None,
        "listing_live_confirmed": None,
        "listing_buyable_confirmed": None,
        "listing_version_matches_phase6": None,
        "current_selling_price": None,           # canonical decimal string, e.g. "24.99"
        "currency": None,
        "inventory_or_capacity_confirmed": None,
        "current_inventory": None,               # optional; null / NOT_APPLICABLE for made-to-order
        "normal_capacity": None,
        "peak_capacity": None,
        "current_backlog": None,
        "safe_handling_time": None,              # whole days
        "handling_time_confirmed": None,
        "advertising_eligibility_confirmed": None,
        "featured_offer_eligibility_confirmed": None,
        "featured_offer_applicability": None,    # APPLICABLE / NOT_APPLICABLE
        "owner_confirmed_at": None,              # tz-aware ISO-8601
        "owner_confirmation_source": None,       # e.g. "manual Seller Central screen-read 2026-07-18"
        "source_document_sha256": None,          # optional sha256 of a local evidence file
    }
    return tmpl


def _classify_bool_confirmation(val):
    if val is None:
        return MISSING
    if val is True:
        return VERIFIED
    if val is False:
        return CONTRADICTORY   # explicit "no" for advertising — never silently a missing field
    return CONTRADICTORY       # a non-bool for a boolean confirmation is a contradiction


def validate_live_product_state(state, handoff):
    """Validate an owner-supplied LIVE-PRODUCT-STATE. Returns LiveProductStateResult.

    Never fabricates: null stays MISSING, False stays CONTRADICTORY (never MISSING), missing never
    becomes false. Credentials/session data and template markers are rejected. Identity/marketplace
    mismatches against the verified Phase 6 handoff are CONTRADICTORY.
    """
    fields = {}
    blockers = []
    warnings = []

    # a template (or absent state) is owner-input-required, never verified.
    if state is None:
        for f in LIVE_STATE_REQUIRED_FIELDS:
            fields[f] = MISSING
        return _live_result(LIVE_STATE_OWNER_INPUT, fields,
                            ["live product state not supplied (owner input required)"], warnings, handoff)
    if state.get("IS_TEMPLATE_NOT_VERIFIED_STATE"):
        for f in LIVE_STATE_REQUIRED_FIELDS:
            fields[f] = MISSING
        return _live_result(LIVE_STATE_OWNER_INPUT, fields,
                            ["a template is not verified live state"], warnings, handoff)

    # hard reject credential / session material.
    if _contains_credentials(state):
        return _live_result(LIVE_STATE_INVALID, fields,
                            ["credential/session/account material rejected from live product state"],
                            warnings, handoff)

    invalid = []

    # identity + marketplace consistency vs the verified Phase 6 handoff.
    for f, expected in (("workspace_id", handoff["workspace_id"]),
                        ("product_id", handoff["product_id"]),
                        ("marketplace", handoff["marketplace"])):
        val = state.get(f)
        if val in (None, ""):
            fields[f] = MISSING
        elif expected is not None and val != expected:
            fields[f] = CONTRADICTORY
            blockers.append(f"{f} '{val}' contradicts verified Phase 6 handoff '{expected}'")
        else:
            fields[f] = VERIFIED

    # required identity / value fields.
    for f in ("account_type", "advertised_asin", "advertised_sku", "owner_confirmation_source"):
        val = state.get(f)
        fields[f] = MISSING if val in (None, "") else VERIFIED

    # advertised variant: required when applicable; an explicit NOT_APPLICABLE marker is allowed.
    var = state.get("advertised_variant_id")
    if var in (None, ""):
        fields["advertised_variant_id"] = MISSING
    elif var == NA:
        fields["advertised_variant_id"] = NOT_APPLICABLE
    else:
        fields["advertised_variant_id"] = VERIFIED

    # price + currency (Decimal).
    price = state.get("current_selling_price")
    if price in (None, ""):
        fields["current_selling_price"] = MISSING
    else:
        try:
            MONEY.parse_positive_decimal(price, field="current_selling_price")
            fields["current_selling_price"] = VERIFIED
        except MONEY.MoneyError as e:
            fields["current_selling_price"] = CONTRADICTORY
            invalid.append(f"current_selling_price invalid: {e}")
    cur = state.get("currency")
    fields["currency"] = MISSING if cur in (None, "") else VERIFIED

    # capacity / handling integers.
    for f, parser in (("normal_capacity", MONEY.parse_positive_int),
                      ("peak_capacity", MONEY.parse_positive_int),
                      ("current_backlog", MONEY.parse_nonnegative_int),
                      ("safe_handling_time", MONEY.parse_positive_int)):
        val = state.get(f)
        if val is None:
            fields[f] = MISSING
        else:
            try:
                parser(val, field=f)
                fields[f] = VERIFIED
            except MONEY.MoneyError as e:
                fields[f] = CONTRADICTORY
                invalid.append(f"{f} invalid: {e}")

    # boolean confirmations that must be explicit True.
    for f in _MUST_BE_TRUE:
        st = _classify_bool_confirmation(state.get(f))
        fields[f] = st
        if st == CONTRADICTORY and state.get(f) is False:
            blockers.append(f"{f} explicitly not confirmed (False) — cannot advertise")

    # a live/buyable claim contradicts a non-publishable Phase 6 safe draft.
    publishable = handoff["listing_publishability"] == SCP.PUB_PUBLISHABLE
    for f in ("listing_live_confirmed", "listing_buyable_confirmed"):
        if state.get(f) is True and not publishable:
            fields[f] = CONTRADICTORY
            blockers.append(f"{f}=True contradicts Phase 6 listing state "
                            f"'{handoff['listing_publishability']}' (safe draft, not publishable)")

    # version match requires the confirmation AND a phase6 index hash the owner attests to.
    if state.get("listing_version_matches_phase6") is True and not state.get("phase6_index_sha256_attested"):
        # advisory: the binding hash lives in the PPC contract, so this is a warning, not a block here.
        warnings.append("listing_version_matches_phase6=True; the binding Phase 6 hash is attested in "
                        "the PPC contract, not the live state")

    # featured offer: applicability explicit; eligibility required only when applicable.
    appl = state.get("featured_offer_applicability")
    if appl in (None, ""):
        fields["featured_offer_applicability"] = MISSING
        fields["featured_offer_eligibility_confirmed"] = MISSING
    elif appl == NA or appl == "NOT_APPLICABLE":
        fields["featured_offer_applicability"] = VERIFIED
        fields["featured_offer_eligibility_confirmed"] = NOT_APPLICABLE
    elif appl == "APPLICABLE":
        fields["featured_offer_applicability"] = VERIFIED
        st = _classify_bool_confirmation(state.get("featured_offer_eligibility_confirmed"))
        fields["featured_offer_eligibility_confirmed"] = st
        if st == CONTRADICTORY and state.get("featured_offer_eligibility_confirmed") is False:
            # not a hard block: Sponsored Products can be planned even if not the Featured Offer.
            fields["featured_offer_eligibility_confirmed"] = OWNER_REVIEW_REQUIRED
            warnings.append("featured_offer_eligibility_confirmed=False while applicable — owner review")
    else:
        fields["featured_offer_applicability"] = CONTRADICTORY
        fields["featured_offer_eligibility_confirmed"] = MISSING
        blockers.append(f"featured_offer_applicability '{appl}' not in APPLICABLE/NOT_APPLICABLE")

    # owner confirmation timestamp must be timezone-aware.
    ts = state.get("owner_confirmed_at")
    if ts in (None, ""):
        fields["owner_confirmed_at"] = MISSING
    elif _is_tz_aware_timestamp(ts):
        fields["owner_confirmed_at"] = VERIFIED
    else:
        fields["owner_confirmed_at"] = CONTRADICTORY
        invalid.append("owner_confirmed_at is not a timezone-aware ISO-8601 timestamp")

    # optional source document sha.
    sds = state.get("source_document_sha256")
    if sds not in (None, ""):
        if not (isinstance(sds, str) and len(sds.replace("sha256:", "")) == 64):
            invalid.append("source_document_sha256 is not a sha256 hex digest")

    # peak >= normal integrity (only when both parsed).
    try:
        nc = MONEY.parse_positive_int(state.get("normal_capacity"), field="normal_capacity")
        pc = MONEY.parse_positive_int(state.get("peak_capacity"), field="peak_capacity")
        if nc is not None and pc is not None and pc < nc:
            fields["peak_capacity"] = CONTRADICTORY
            blockers.append("peak_capacity < normal_capacity")
    except MONEY.MoneyError:
        pass

    # resolve overall state (most severe wins) — every classified field counts, including identity.
    all_vals = list(fields.values())
    identity_missing = any(fields.get(f) == MISSING for f in ("workspace_id", "product_id", "marketplace"))
    if invalid:
        overall = LIVE_STATE_INVALID
        blockers = invalid + blockers
    elif any(s == CONTRADICTORY for s in all_vals):
        overall = LIVE_STATE_CONTRADICTORY
    elif any(fields[f] == MISSING for f in LIVE_STATE_REQUIRED_FIELDS) or identity_missing:
        overall = LIVE_STATE_OWNER_INPUT
    else:
        overall = LIVE_STATE_VERIFIED
    return _live_result(overall, fields, blockers, warnings, handoff, raw=state)


class LiveProductStateResult:
    def __init__(self, state, fields, blockers, warnings, handoff, raw=None):
        self.state = state
        self.fields = fields
        self.blockers = blockers
        self.warnings = warnings
        self.handoff = handoff
        self.raw = raw or {}

    @property
    def verified(self):
        return self.state == LIVE_STATE_VERIFIED

    def counts(self):
        c = {VERIFIED: 0, MISSING: 0, CONTRADICTORY: 0, NOT_APPLICABLE: 0, OWNER_REVIEW_REQUIRED: 0}
        for f in LIVE_STATE_REQUIRED_FIELDS:
            c[self.fields.get(f, MISSING)] = c.get(self.fields.get(f, MISSING), 0) + 1
        return c

    def to_dict(self):
        c = self.counts()
        doc = {
            "schema_version": SCHEMA_LIVE_STATE_VALIDATION,
            "live_product_state_result": self.state,
            "is_verified": self.verified,
            "workspace_id": self.handoff["workspace_id"],
            "product_id": self.handoff["product_id"],
            "marketplace": self.handoff["marketplace"],
            "required_field_count": len(LIVE_STATE_REQUIRED_FIELDS),
            "field_states": {f: self.fields.get(f, MISSING) for f in LIVE_STATE_REQUIRED_FIELDS},
            "counts_by_state": c,
            "verified_count": c[VERIFIED] + c[NOT_APPLICABLE],
            "missing_count": c[MISSING],
            "contradictory_count": c[CONTRADICTORY],
            "blockers": sorted(self.blockers),
            "warnings": sorted(self.warnings),
            "never_inferred_from_phase6_safe_draft": True,
        }
        return _finalize(doc)


def _live_result(state, fields, blockers, warnings, handoff, raw=None):
    for f in LIVE_STATE_REQUIRED_FIELDS:
        fields.setdefault(f, MISSING)
    return LiveProductStateResult(state, fields, blockers, warnings, handoff, raw=raw)


# ================================================================ PPC PRODUCT CONTRACT AUTHORITY
# binds the owner-confirmed live state to the cryptographically verified Phase 6 package. All Phase 6
# hashes are RECOMPUTED from the handoff; a template or a hash mismatch can never be VERIFIED.
CONTRACT_PHASE6_HASH_FIELDS = ("phase6_package_sha256", "phase6_index_sha256", "keyword_source_sha256",
                               "keyword_allocation_sha256", "product_facts_sha256", "claim_evidence_sha256")
CONTRACT_WHEN_APPLICABLE_HASHES = ("manufacturing_spec_sha256", "customization_text_constraints_sha256")


def _phase6_expected_hashes(handoff):
    full = handoff.get("full_shas", {})
    return {
        "phase6_package_sha256": full.get("phase6_package_sha256"),
        "phase6_index_sha256": full.get("phase6_index_sha256"),
        "keyword_source_sha256": full.get("keyword_source_sha256"),
        "keyword_allocation_sha256": full.get("keyword_allocation_sha256")
        or full.get("phase6b_manifest_sha256"),
        "product_facts_sha256": full.get("product_facts_sha256"),
        "claim_evidence_sha256": full.get("claim_evidence_sha256"),
    }


def build_ppc_product_contract(handoff, live_state_result=None, live_state_sha256=None):
    """A REQUIREMENTS template binding the verified Phase 6 hashes; owner-confirmation fields null."""
    exp = _phase6_expected_hashes(handoff)
    tmpl = {
        "schema_version": SCHEMA_CONTRACT_TEMPLATE,
        "IS_TEMPLATE_NOT_A_VERIFIED_CONTRACT": True,
        "instructions": "Owner links the verified live product state to the verified Phase 6 package. "
                        "Never paste credentials, cookies, tokens, or account exports.",
        "contract_version": "1.0.0",
        "contract_id": None,
        "workspace_id": handoff["workspace_id"],
        "product_id": handoff["product_id"],
        "marketplace": handoff["marketplace"],
        "account_type": None,
        "advertised_asin": None,
        "advertised_sku": None,
        "advertised_variant_id": None,
        "live_product_state_sha256": live_state_sha256,
        "phase6_package_sha256": exp["phase6_package_sha256"],
        "phase6_index_sha256": exp["phase6_index_sha256"],
        "keyword_source_sha256": exp["keyword_source_sha256"],
        "keyword_allocation_sha256": exp["keyword_allocation_sha256"],
        "product_facts_sha256": exp["product_facts_sha256"],
        "claim_evidence_sha256": exp["claim_evidence_sha256"],
        "manufacturing_spec_sha256": None,               # required when applicable
        "customization_text_constraints_sha256": None,   # required when applicable
        "manufacturing_customization_applicable": None,  # owner asserts applicability explicitly
        "listing_audit_state": handoff["listing_audit_state"],
        "listing_publishability": handoff["listing_publishability"],
        "listing_live_confirmed": None,
        "listing_buyable_confirmed": None,
        "listing_version_matches_phase6": None,
        "current_selling_price": None,
        "currency": None,
        "inventory_or_capacity_confirmed": None,
        "normal_capacity": None,
        "peak_capacity": None,
        "current_backlog": None,
        "safe_handling_time": None,
        "handling_time_confirmed": None,
        "advertising_eligibility_confirmed": None,
        "featured_offer_eligibility_confirmed": None,
        "featured_offer_applicability": None,
        "owner_confirmed_at": None,
        "owner_confirmation_source": None,
    }
    return tmpl


def validate_ppc_product_contract(contract, handoff, live_state_result, live_state_sha256):
    """Validate the PPC product contract. Recomputes Phase 6 hashes, verifies the live-state hash and
    identity/marketplace, requires a publishable + live + buyable listing and complete owner lineage,
    and rejects templates / credentials / stale-or-contradictory confirmations. Returns
    PPCProductContractResult."""
    blockers = []
    warnings = []
    checks = {}

    if contract is None:
        return PPCProductContractResult(CONTRACT_OWNER_INPUT, checks,
                                        ["PPC product contract not supplied (owner input required)"],
                                        warnings, handoff)
    if contract.get("IS_TEMPLATE_NOT_A_VERIFIED_CONTRACT"):
        return PPCProductContractResult(CONTRACT_OWNER_INPUT, checks,
                                        ["a template is not a verified contract"], warnings, handoff)
    if _contains_credentials(contract):
        return PPCProductContractResult(CONTRACT_INVALID, checks,
                                        ["credential/session/account material rejected from contract"],
                                        warnings, handoff)

    # 1. recompute + verify Phase 6 hashes.
    exp = _phase6_expected_hashes(handoff)
    phase6_mismatch = []
    for f in CONTRACT_PHASE6_HASH_FIELDS:
        want, got = exp.get(f), contract.get(f)
        if not got:
            checks[f] = MISSING
            phase6_mismatch.append(f"{f} missing")
        elif want and got != want:
            checks[f] = CONTRADICTORY
            phase6_mismatch.append(f"{f} mismatch")
        else:
            checks[f] = VERIFIED

    # manufacturing / customization hashes: required only when the owner asserts applicability.
    applicable = contract.get("manufacturing_customization_applicable")
    for f in CONTRACT_WHEN_APPLICABLE_HASHES:
        got = contract.get(f)
        if applicable is True:
            checks[f] = VERIFIED if got else MISSING
            if not got:
                phase6_mismatch.append(f"{f} required (manufacturing/customization applicable)")
        elif applicable is False:
            checks[f] = NOT_APPLICABLE
        else:
            checks[f] = MISSING
            warnings.append("manufacturing_customization_applicable not asserted")

    # 2. live-state hash binding.
    ls_hash = contract.get("live_product_state_sha256")
    if not ls_hash:
        checks["live_product_state_sha256"] = MISSING
        blockers.append("live_product_state_sha256 missing")
    elif live_state_sha256 and ls_hash != live_state_sha256:
        checks["live_product_state_sha256"] = CONTRADICTORY
        blockers.append("live_product_state_sha256 mismatch (contract confirmed against a different / "
                        "stale live state)")
    else:
        checks["live_product_state_sha256"] = VERIFIED

    # 3-4. identity + marketplace.
    for f, expected in (("workspace_id", handoff["workspace_id"]),
                        ("product_id", handoff["product_id"]),
                        ("marketplace", handoff["marketplace"])):
        val = contract.get(f)
        if val in (None, ""):
            checks[f] = MISSING
        elif expected is not None and val != expected:
            checks[f] = CONTRADICTORY
            blockers.append(f"{f} '{val}' contradicts verified Phase 6 handoff '{expected}'")
        else:
            checks[f] = VERIFIED

    # 3b. ASIN/SKU/variant must match the verified live state (no divergence between the two docs).
    if live_state_result is not None:
        lraw = live_state_result.raw or {}
        for f in ("advertised_asin", "advertised_sku", "advertised_variant_id", "current_selling_price",
                  "currency", "account_type"):
            cv, lv = contract.get(f), lraw.get(f)
            if cv not in (None, "") and lv not in (None, "") and cv != lv:
                checks[f] = CONTRADICTORY
                blockers.append(f"contract {f} '{cv}' differs from live state '{lv}'")

    # 5-6. listing version + publishability.
    checks["listing_version_matches_phase6"] = _classify_bool_confirmation(
        contract.get("listing_version_matches_phase6"))
    pub = contract.get("listing_publishability")
    if pub == SCP.PUB_PUBLISHABLE:
        checks["listing_publishability"] = VERIFIED
    else:
        checks["listing_publishability"] = CONTRADICTORY
        blockers.append(f"listing not publishable ({pub}); Phase 6 remains a safe draft")

    # 7. live + buyable.
    checks["listing_live_confirmed"] = _classify_bool_confirmation(contract.get("listing_live_confirmed"))
    checks["listing_buyable_confirmed"] = _classify_bool_confirmation(
        contract.get("listing_buyable_confirmed"))

    # 8. price + currency present (never overwrite a CONTRADICTORY set by the live-state cross-check).
    for f in ("current_selling_price", "currency"):
        if checks.get(f) != CONTRADICTORY:
            checks[f] = MISSING if contract.get(f) in (None, "") else VERIFIED

    # 9. capacity + handling.
    checks["inventory_or_capacity_confirmed"] = _classify_bool_confirmation(
        contract.get("inventory_or_capacity_confirmed"))
    checks["handling_time_confirmed"] = _classify_bool_confirmation(contract.get("handling_time_confirmed"))

    # 10. advertising eligibility.
    checks["advertising_eligibility_confirmed"] = _classify_bool_confirmation(
        contract.get("advertising_eligibility_confirmed"))

    # 11. featured offer when applicable.
    appl = contract.get("featured_offer_applicability")
    if appl == "NOT_APPLICABLE" or appl == NA:
        checks["featured_offer_eligibility_confirmed"] = NOT_APPLICABLE
    elif appl == "APPLICABLE":
        checks["featured_offer_eligibility_confirmed"] = _classify_bool_confirmation(
            contract.get("featured_offer_eligibility_confirmed"))
    else:
        checks["featured_offer_eligibility_confirmed"] = MISSING

    # 12. owner lineage.
    ts = contract.get("owner_confirmed_at")
    checks["owner_confirmed_at"] = VERIFIED if _is_tz_aware_timestamp(ts) else (
        MISSING if ts in (None, "") else CONTRADICTORY)
    checks["owner_confirmation_source"] = MISSING if contract.get("owner_confirmation_source") in (
        None, "") else VERIFIED

    # live state must itself be verified before a contract can bind it.
    if live_state_result is not None and not live_state_result.verified:
        blockers.append(f"live product state not verified ({live_state_result.state})")

    # resolve.
    if any(v == CONTRADICTORY for v in checks.values()) or any("mismatch" in b for b in blockers):
        state = CONTRACT_CONTRADICTORY
    elif phase6_mismatch and any("mismatch" in m for m in phase6_mismatch):
        state = CONTRACT_PHASE6_BLOCKED
    elif checks.get("listing_publishability") != VERIFIED:
        state = CONTRACT_PHASE6_BLOCKED
    elif live_state_result is not None and not live_state_result.verified:
        state = CONTRACT_LIVE_STATE_BLOCKED
    elif any(v == MISSING for v in checks.values()):
        state = CONTRACT_OWNER_INPUT
    else:
        state = CONTRACT_VERIFIED
    return PPCProductContractResult(state, checks, blockers, warnings, handoff)


class PPCProductContractResult:
    def __init__(self, state, checks, blockers, warnings, handoff):
        self.state = state
        self.checks = checks
        self.blockers = blockers
        self.warnings = warnings
        self.handoff = handoff

    @property
    def verified(self):
        return self.state == CONTRACT_VERIFIED

    def to_dict(self):
        c = {VERIFIED: 0, MISSING: 0, CONTRADICTORY: 0, NOT_APPLICABLE: 0}
        for v in self.checks.values():
            c[v] = c.get(v, 0) + 1
        doc = {
            "schema_version": SCHEMA_CONTRACT_VALIDATION,
            "ppc_product_contract_result": self.state,
            "is_verified": self.verified,
            "workspace_id": self.handoff["workspace_id"],
            "product_id": self.handoff["product_id"],
            "marketplace": self.handoff["marketplace"],
            "checks": dict(sorted(self.checks.items())),
            "counts_by_state": c,
            "phase6_hashes_recomputed_and_compared": True,
            "template_or_credentials_rejected": True,
            "blockers": sorted(self.blockers),
            "warnings": sorted(self.warnings),
        }
        return _finalize(doc)


# ================================================================ ONE-SKU ECONOMICS ENGINE
# every money value is a Decimal (core.money); no float ever enters or leaves. The ONLY default is the
# declared, owner-reviewable $8.00 USD minimum profit — every other cost is required and never zero.
_ECON_COST_FIELDS = ("amazon_referral_fee", "other_amazon_fees", "blank_product_cost", "decoration_cost",
                     "personalization_cost", "packaging_cost", "outbound_shipping_cost", "labor_cost",
                     "transaction_cost", "refund_reserve", "replacement_reserve", "other_variable_costs")


def build_economics_template(handoff):
    tmpl = {
        "schema_version": SCHEMA_ECONOMICS_TEMPLATE,
        "IS_TEMPLATE_NOT_OWNER_CONFIRMED": True,
        "instructions": "Enter every value as a canonical DECIMAL STRING (e.g. \"24.99\"), never a "
                        "number literal. A missing cost stays missing — it is NEVER treated as 0. Only "
                        "minimum_required_profit has a declared default ($8.00 USD).",
        "economics_version": "1.0.0",
        "workspace_id": handoff["workspace_id"],
        "product_id": handoff["product_id"],
        "advertised_asin": None,
        "advertised_sku": None,
        "advertised_variant_id": None,
        "currency": None,
        "selling_price": None,
        "expected_discount": None,
    }
    for f in _ECON_COST_FIELDS:
        tmpl[f] = None
    tmpl.update({
        "minimum_required_profit": None,   # null -> declared $8.00 USD default is applied + recorded
        "expected_ad_conversion_rate": None,
        "normal_capacity": None,
        "peak_capacity": None,
        "current_backlog": None,
        "safe_handling_time": None,
        "owner_total_test_budget": None,
        "average_daily_budget": None,
        "learning_window_days": None,
        "owner_configured_evidence_target": None,
        "owner_launch_objective": None,
        "owner_confirmed_at": None,
        "source_notes": None,
    })
    return tmpl


class PPCEconomicsResult:
    def __init__(self, state, currency, values, derived, blockers, warnings, min_profit_source,
                 input_state, missing, invalid):
        self.state = state
        self.currency = currency
        self.values = values            # dict field -> Decimal (parsed inputs)
        self.derived = derived          # dict name -> Decimal (computed, raw precision)
        self.blockers = blockers
        self.warnings = warnings
        self.min_profit_source = min_profit_source
        self.input_state = input_state
        self.missing = missing
        self.invalid = invalid

    @property
    def viable(self):
        return self.state == ECON_VIABLE

    def _cur(self, name):
        return MONEY.serialize_currency(self.derived.get(name))

    def to_dict(self):
        doc = {
            "schema_version": SCHEMA_ECONOMICS_VALIDATION,
            "ppc_economics_result": self.state,
            "input_state": self.input_state,
            "is_viable": self.viable,
            "currency": self.currency,
            "minimum_required_profit_source": self.min_profit_source,
            "required_input_count": len(_ECON_COST_FIELDS) + 4,   # + price, discount, min_profit, cvr
            "missing_required_inputs": sorted(self.missing),
            "invalid_inputs": sorted(self.invalid),
            "net_sales": self._cur("net_sales"),
            "non_ad_variable_costs": self._cur("non_ad_variable_costs"),
            "contribution_margin_before_ads": self._cur("contribution_margin_before_ads"),
            "break_even_ad_cost_per_order": self._cur("break_even_ad_cost_per_order"),
            "target_ad_cost_per_order": self._cur("target_ad_cost_per_order"),
            "break_even_acos": MONEY.serialize_rate(self.derived.get("break_even_acos")),
            "target_acos": MONEY.serialize_rate(self.derived.get("target_acos")),
            "maximum_cpc": self._cur("maximum_cpc"),
            "expected_ad_conversion_rate": MONEY.serialize_rate(self.values.get("expected_ad_conversion_rate")),
            "no_float_in_canonical_output": True,
            "no_universal_cpc_clamp": True,
            "no_cpc_fallback": True,
            "blockers": sorted(self.blockers),
            "warnings": sorted(self.warnings),
        }
        return _finalize(doc)


def compute_ppc_economics(inputs, *, live_currency=None):
    """Compute one-SKU PPC economics from owner-supplied decimal strings. Never defaults a cost to 0.
    Returns PPCEconomicsResult (all money as Decimal internally, serialized as strings)."""
    blockers, warnings, missing, invalid = [], [], [], []
    if inputs is None or inputs.get("IS_TEMPLATE_NOT_OWNER_CONFIRMED"):
        return PPCEconomicsResult(ECON_OWNER_INPUT, None, {}, {},
                                  ["economics inputs not owner-confirmed (owner input required)"],
                                  [], DEFAULT_MIN_PROFIT_SOURCE, ECON_OWNER_INPUT,
                                  ["<all economics inputs>"], [])
    if _contains_credentials(inputs):
        return PPCEconomicsResult(ECON_INVALID, None, {}, {},
                                  ["credential/session material rejected from economics inputs"],
                                  [], DEFAULT_MIN_PROFIT_SOURCE, ECON_INVALID, [], ["credentials"])

    currency = inputs.get("currency")
    if currency in (None, ""):
        missing.append("currency")
    if live_currency and currency and live_currency != currency:
        invalid.append(f"CURRENCY_MISMATCH: economics {currency} != live {live_currency}")

    values = {}

    def take(field, parser):
        raw = inputs.get(field)
        if raw is None:
            missing.append(field)
            values[field] = None
            return
        try:
            values[field] = parser(raw, field=field)
        except MONEY.MoneyError as e:
            invalid.append(f"INVALID_DECIMAL: {e}")
            values[field] = None

    take("selling_price", MONEY.parse_positive_decimal)
    take("expected_discount", MONEY.parse_nonnegative_decimal)
    for f in _ECON_COST_FIELDS:
        take(f, MONEY.parse_nonnegative_decimal)

    # conversion rate (0, 1].
    cvr_raw = inputs.get("expected_ad_conversion_rate")
    if cvr_raw is None:
        missing.append("expected_ad_conversion_rate")
        values["expected_ad_conversion_rate"] = None
    else:
        try:
            values["expected_ad_conversion_rate"] = MONEY.parse_rate_decimal(
                cvr_raw, field="expected_ad_conversion_rate")
        except MONEY.MoneyError as e:
            invalid.append(f"CVR_OUT_OF_RANGE: {e}")
            values["expected_ad_conversion_rate"] = None

    # minimum required profit — declared $8.00 USD default when the owner omits it (USD only).
    mp_raw = inputs.get("minimum_required_profit")
    if mp_raw in (None, ""):
        if currency in (None, "USD"):
            values["minimum_required_profit"] = MONEY.parse_nonnegative_decimal(DEFAULT_MIN_PROFIT)
            min_profit_source = DEFAULT_MIN_PROFIT_SOURCE
        else:
            missing.append("minimum_required_profit")
            values["minimum_required_profit"] = None
            min_profit_source = "OWNER_REQUIRED_NON_USD"
    else:
        try:
            values["minimum_required_profit"] = MONEY.parse_nonnegative_decimal(
                mp_raw, field="minimum_required_profit")
            min_profit_source = "OWNER_SUPPLIED"
        except MONEY.MoneyError as e:
            invalid.append(f"INVALID_DECIMAL: {e}")
            values["minimum_required_profit"] = None
            min_profit_source = "OWNER_SUPPLIED_INVALID"

    # owner confirmation presence (staleness is enforced by the contract's live-state hash binding).
    if inputs.get("owner_confirmed_at") in (None, ""):
        warnings.append("OWNER_CONFIRMATION_MISSING: economics inputs lack owner_confirmed_at")

    # short-circuit on incomplete / invalid inputs — never compute over a MISSING value.
    if invalid:
        blockers = invalid + blockers
        return PPCEconomicsResult(ECON_INVALID, currency, values, {}, blockers, warnings,
                                  min_profit_source, ECON_INVALID, missing, invalid)
    if missing:
        blockers.append("MISSING_REQUIRED_INPUT: " + ", ".join(sorted(missing)))
        return PPCEconomicsResult(ECON_OWNER_INPUT, currency, values, {}, blockers, warnings,
                                  min_profit_source, ECON_OWNER_INPUT, missing, invalid)

    # ---- formulas (Decimal, full precision; quantize only at serialization) ----
    d = {}
    d["net_sales"] = values["selling_price"] - values["expected_discount"]
    d["non_ad_variable_costs"] = MONEY.sum_required_decimals(
        [values[f] for f in _ECON_COST_FIELDS], field="non_ad_variable_costs")
    d["contribution_margin_before_ads"] = d["net_sales"] - d["non_ad_variable_costs"]
    d["break_even_ad_cost_per_order"] = d["contribution_margin_before_ads"]
    d["target_ad_cost_per_order"] = d["contribution_margin_before_ads"] - values["minimum_required_profit"]
    d["break_even_acos"] = MONEY.safe_divide(d["break_even_ad_cost_per_order"], d["net_sales"])
    d["target_acos"] = MONEY.safe_divide(d["target_ad_cost_per_order"], d["net_sales"])
    d["maximum_cpc"] = d["target_ad_cost_per_order"] * values["expected_ad_conversion_rate"]

    # ---- viability (hard blocks) ----
    if d["net_sales"] <= 0:
        blockers.append("NON_POSITIVE_NET_SALES")
    if d["contribution_margin_before_ads"] <= 0:
        blockers.append("NON_POSITIVE_CONTRIBUTION_MARGIN")
    if d["target_ad_cost_per_order"] <= 0:
        blockers.append("TARGET_AD_COST_NON_POSITIVE")
    if (d["break_even_acos"] is not None and d["target_acos"] is not None
            and d["target_acos"] >= d["break_even_acos"]):
        blockers.append("TARGET_ACOS_NOT_BELOW_BREAK_EVEN")
    if d["maximum_cpc"] <= 0:
        blockers.append("MAXIMUM_CPC_NON_POSITIVE")

    if blockers:
        return PPCEconomicsResult(ECON_NOT_VIABLE, currency, values, d, blockers, warnings,
                                  min_profit_source, "COMPLETE", missing, invalid)
    return PPCEconomicsResult(ECON_VIABLE, currency, values, d, blockers, warnings,
                              min_profit_source, "COMPLETE", missing, invalid)


# ================================================================ CAPACITY & HANDLING SAFETY
def evaluate_capacity_and_handling(inputs):
    """Bounded capacity + handling safety evaluator. Never infers capacity from Phase 6 or history.
    Returns a plain dict (deterministic)."""
    blockers, warnings = [], []
    owner_review = False

    def _int(field, parser):
        raw = inputs.get(field)
        if raw is None:
            return None, True   # missing
        try:
            return parser(raw, field=field), False
        except MONEY.MoneyError as e:
            blockers.append(f"{field} invalid: {e}")
            return None, False

    normal, nmiss = _int("normal_capacity", MONEY.parse_positive_int)
    peak, pmiss = _int("peak_capacity", MONEY.parse_positive_int)
    backlog, bmiss = _int("current_backlog", MONEY.parse_nonnegative_int)
    handling, hmiss = _int("safe_handling_time", MONEY.parse_positive_int)
    cap_conf = inputs.get("inventory_or_capacity_confirmed")
    hnd_conf = inputs.get("handling_time_confirmed")

    # ---- capacity ----
    safe_remaining = None
    backlog_state = None
    if nmiss or bmiss or normal is None or backlog is None or cap_conf is None:
        capacity_state = CAPACITY_OWNER_INPUT
        if cap_conf is None:
            warnings.append("inventory_or_capacity_confirmed missing")
    elif cap_conf is not True:
        capacity_state = CAPACITY_OWNER_INPUT
        warnings.append("inventory_or_capacity_confirmed not explicitly True")
    else:
        safe_remaining = normal - backlog
        if peak is not None and peak < normal:
            capacity_state = CAPACITY_BLOCKED
            blockers.append("peak_capacity < normal_capacity")
            backlog_state = "CONTRADICTORY"
        elif backlog >= normal:
            capacity_state = CAPACITY_BLOCKED
            backlog_state = "BACKLOG_EXCEEDS_CAPACITY"
            blockers.append(f"current_backlog ({backlog}) >= normal_capacity ({normal}); no safe "
                            "remaining capacity to absorb ad-driven orders")
        else:
            capacity_state = CAPACITY_SAFE
            backlog_state = "WITHIN_CAPACITY"

    # ---- handling ----
    if hmiss or handling is None or hnd_conf is None:
        handling_state = HANDLING_OWNER_INPUT
        if hnd_conf is None:
            warnings.append("handling_time_confirmed missing")
    elif hnd_conf is not True:
        handling_state = HANDLING_OWNER_INPUT
        warnings.append("handling_time_confirmed not explicitly True")
    elif handling <= 0:
        handling_state = HANDLING_BLOCKED
        blockers.append("safe_handling_time is non-positive")
    else:
        handling_state = HANDLING_SAFE

    doc = {
        "schema_version": SCHEMA_CAPACITY,
        "capacity_state": capacity_state,
        "handling_state": handling_state,
        "safe_remaining_capacity": safe_remaining,
        "backlog_state": backlog_state,
        "normal_capacity_present": normal is not None,
        "peak_capacity_present": peak is not None,
        "current_backlog_present": backlog is not None,
        "safe_handling_time_present": handling is not None,
        "inventory_or_capacity_confirmed": cap_conf if cap_conf in (True, False) else None,
        "handling_time_confirmed": hnd_conf if hnd_conf in (True, False) else None,
        "owner_review_required": owner_review,
        "inferred_from_phase6_or_history": False,
        "blockers": sorted(blockers),
        "warnings": sorted(warnings),
    }
    return _finalize(doc)


# ================================================================ BUDGET FEASIBILITY FOUNDATION
def evaluate_budget_feasibility(inputs, economics_result, capacity_doc):
    """Budget-feasibility input validation + click-capacity bounds ONLY. Allocates NO roles/budgets,
    selects NO targets, computes NO starting bid. No universal $25 / percentage / click / window
    thresholds — every bound derives from owner inputs. Returns a deterministic dict."""
    blockers, warnings = [], []
    missing = []

    def money(field):
        raw = inputs.get(field)
        if raw is None:
            missing.append(field)
            return None
        try:
            return MONEY.parse_positive_decimal(raw, field=field)
        except MONEY.MoneyError as e:
            blockers.append(f"{field} invalid: {e}")
            return None

    def posint(field):
        raw = inputs.get(field)
        if raw is None:
            missing.append(field)
            return None
        try:
            return MONEY.parse_positive_int(raw, field=field)
        except MONEY.MoneyError as e:
            blockers.append(f"{field} invalid: {e}")
            return None

    total_budget = money("owner_total_test_budget")
    daily_budget = money("average_daily_budget")
    learning_window = posint("learning_window_days")
    evidence_target = posint("owner_configured_evidence_target")
    objective = inputs.get("owner_launch_objective")
    if objective in (None, ""):
        missing.append("owner_launch_objective")

    planned_cpc = None
    pc_raw = inputs.get("planned_cpc_candidate")
    if pc_raw is not None:
        try:
            planned_cpc = MONEY.parse_positive_decimal(pc_raw, field="planned_cpc_candidate")
        except MONEY.MoneyError as e:
            blockers.append(f"planned_cpc_candidate invalid: {e}")

    max_cpc = economics_result.derived.get("maximum_cpc") if economics_result else None
    capacity_state = capacity_doc.get("capacity_state") if capacity_doc else None

    estimated_total_click_capacity = MONEY.safe_divide(total_budget, planned_cpc)
    maximum_safe_click_capacity = MONEY.safe_divide(total_budget, max_cpc)

    # resolve state.
    if missing:
        state = BUDGET_OWNER_INPUT
        blockers.append("MISSING_REQUIRED_INPUT: " + ", ".join(sorted(missing)))
    elif blockers:
        state = BUDGET_BLOCKED
    elif capacity_state == CAPACITY_BLOCKED:
        state = BUDGET_BLOCKED
        blockers.append("capacity blocker propagates to budget feasibility")
    elif max_cpc is None or max_cpc <= 0:
        state = BUDGET_BLOCKED
        blockers.append("MAXIMUM_CPC_NON_POSITIVE: no economic click-capacity bound available")
    elif planned_cpc is None:
        state = BUDGET_OWNER_INPUT
        warnings.append("planned_cpc_candidate not supplied — cannot size estimated click capacity")
    elif estimated_total_click_capacity is not None and estimated_total_click_capacity < evidence_target:
        state = BUDGET_BLOCKED
        blockers.append(f"estimated click capacity ({MONEY.decimal_to_canonical_string(estimated_total_click_capacity)}) "
                        f"< owner evidence target ({evidence_target}) within the total test budget")
    elif planned_cpc > max_cpc:
        state = BUDGET_OWNER_REVIEW
        warnings.append("planned_cpc_candidate exceeds the economic maximum CPC bound — owner must "
                        "review the bid before planning (this is NOT a recommended bid)")
    else:
        state = BUDGET_PASSES

    doc = {
        "schema_version": SCHEMA_BUDGET,
        "budget_feasibility_result": state,
        "owner_total_test_budget_present": total_budget is not None,
        "average_daily_budget_present": daily_budget is not None,
        "learning_window_days_present": learning_window is not None,
        "owner_configured_evidence_target_present": evidence_target is not None,
        "owner_launch_objective_present": objective not in (None, ""),
        "planned_cpc_candidate_present": planned_cpc is not None,
        "maximum_cpc_available": max_cpc is not None and max_cpc > 0,
        "estimated_total_click_capacity": MONEY.decimal_to_canonical_string(
            MONEY.quantize_rate(estimated_total_click_capacity, MONEY.Decimal("0.01"))
            if estimated_total_click_capacity is not None else None),
        "maximum_safe_click_capacity": MONEY.decimal_to_canonical_string(
            MONEY.quantize_rate(maximum_safe_click_capacity, MONEY.Decimal("0.01"))
            if maximum_safe_click_capacity is not None else None),
        "maximum_safe_click_capacity_is_a_bound_not_a_bid": True,
        "no_universal_25_dollar_threshold": True,
        "no_fixed_percentage_allocator": True,
        "no_universal_click_threshold": True,
        "no_universal_learning_window": True,
        "no_campaign_role_selected": True,
        "no_role_budget_allocated": True,
        "missing_required_inputs": sorted(missing),
        "blockers": sorted(blockers),
        "warnings": sorted(warnings),
    }
    return _finalize(doc)


# ================================================================ READINESS RESOLUTION
def _component_rank(*ranks):
    return min(ranks)


def resolve_phase7_1m_readiness(handoff, live_result, contract_result, economics_result,
                                capacity_doc, budget_doc, network, artifacts_verified=True):
    """One most-restrictive resolver over every component. Distinguishes campaign-planning readiness
    from manual-launch readiness (the latter is always false this session)."""
    comp = {}

    # 1. Phase 6 package validity + boundary + artifact integrity -> hard preflight.
    pkg_valid = (handoff["live_verification"].get("package_verify_result") in
                 ("PASS", "PASS_WITH_WARNINGS")) or bool(handoff["package_index_sha256_prefix"])
    boundary_clean = network["no_active_amazon_account_path"] and network["non_loopback_bind_count"] == 0
    comp["phase6_package"] = READY_CAMPAIGN_PLANNING if pkg_valid else READY_PREFLIGHT_BLOCKED
    comp["amazon_boundary"] = READY_CAMPAIGN_PLANNING if boundary_clean else READY_PREFLIGHT_BLOCKED
    comp["artifact_verification"] = READY_CAMPAIGN_PLANNING if artifacts_verified else READY_PREFLIGHT_BLOCKED

    # 2. Phase 6 listing publishability.
    publishable = handoff["listing_publishability"] == SCP.PUB_PUBLISHABLE
    comp["phase6_listing_publishable"] = READY_CAMPAIGN_PLANNING if publishable else READY_OWNER_INPUT

    # 3. live product state.
    comp["live_product_state"] = {
        LIVE_STATE_VERIFIED: READY_CAMPAIGN_PLANNING,
        LIVE_STATE_OWNER_INPUT: READY_OWNER_INPUT,
        LIVE_STATE_CONTRADICTORY: READY_PREFLIGHT_BLOCKED,
        LIVE_STATE_INVALID: READY_PREFLIGHT_BLOCKED,
    }[live_result.state]

    # 4. PPC contract.
    comp["ppc_contract"] = {
        CONTRACT_VERIFIED: READY_CAMPAIGN_PLANNING,
        CONTRACT_OWNER_INPUT: READY_OWNER_INPUT,
        CONTRACT_LIVE_STATE_BLOCKED: READY_OWNER_INPUT,
        CONTRACT_PHASE6_BLOCKED: READY_OWNER_INPUT,
        CONTRACT_CONTRADICTORY: READY_PREFLIGHT_BLOCKED,
        CONTRACT_INVALID: READY_PREFLIGHT_BLOCKED,
    }[contract_result.state]

    # 5-6. economics completeness + viability.
    comp["economics"] = {
        ECON_VIABLE: READY_CAMPAIGN_PLANNING,
        ECON_OWNER_INPUT: READY_OWNER_INPUT,
        ECON_NOT_VIABLE: READY_ECONOMICS_BLOCKED,
        ECON_INVALID: READY_ECONOMICS_BLOCKED,
    }[economics_result.state]

    # 7. capacity.
    comp["capacity"] = {
        CAPACITY_SAFE: READY_CAMPAIGN_PLANNING,
        CAPACITY_OWNER_INPUT: READY_OWNER_INPUT,
        CAPACITY_BLOCKED: READY_ECONOMICS_BLOCKED,
    }[capacity_doc["capacity_state"]]

    # 8. handling.
    comp["handling"] = {
        HANDLING_SAFE: READY_CAMPAIGN_PLANNING,
        HANDLING_OWNER_INPUT: READY_OWNER_INPUT,
        HANDLING_BLOCKED: READY_ECONOMICS_BLOCKED,
    }[capacity_doc["handling_state"]]

    # 9-10. budget completeness + feasibility.
    comp["budget"] = {
        BUDGET_PASSES: READY_CAMPAIGN_PLANNING,
        BUDGET_OWNER_REVIEW: READY_FOUNDATION_REVIEW,
        BUDGET_OWNER_INPUT: READY_OWNER_INPUT,
        BUDGET_BLOCKED: READY_ECONOMICS_BLOCKED,
    }[budget_doc["budget_feasibility_result"]]

    base_rank = _component_rank(*[_RANK[v] for v in comp.values()])
    decision = _RANK_TO_STATE[base_rank]

    ready_for_campaign_planning = decision == READY_CAMPAIGN_PLANNING
    ready_for_economics_review = economics_result.state == ECON_VIABLE
    ready_for_owner_input = decision == READY_OWNER_INPUT

    doc = {
        "schema_version": SCHEMA_READINESS,
        "phase7_1m_readiness": decision,
        "readiness_meaning": {
            READY_PREFLIGHT_BLOCKED: "a hard integrity blocker (invalid Phase 6 / account path / "
                                     "contradiction) stops the foundation",
            READY_ECONOMICS_BLOCKED: "the advertised SKU is not economically/operationally eligible "
                                     "(economics / capacity / handling / budget)",
            READY_OWNER_INPUT: "required live-product / economics / budget owner inputs are missing",
            READY_FOUNDATION_REVIEW: "the foundation is complete and viable and READY FOR OWNER REVIEW; "
                                     "campaign-planning readiness is not yet auto-granted",
            READY_CAMPAIGN_PLANNING: "the verified live product + complete economics are eligible; a "
                                     "later MANUAL campaign-planning session may begin (NOT a launch)",
        }[decision],
        "component_states": dict(sorted(comp.items())),
        "most_restrictive_component": min(comp, key=lambda k: _RANK[comp[k]]),
        "distinct_from_ready_for_manual_launch": True,
        "ready_for_owner_input": ready_for_owner_input,
        "ready_for_economics_review": ready_for_economics_review,
        "ready_for_campaign_planning": ready_for_campaign_planning,
        "ready_for_manual_launch": False,
        "ready_for_amazon_action": False,
        # this session never selects / calculates / allocates anything:
        "target_selection_count": 0,
        "negative_selection_count": 0,
        "bid_calculation_count": 0,
        "campaign_role_count": 0,
        "campaign_budget_allocation_count": 0,
        "launch_package_count": 0,
        "later_phase_artifact_count": 0,
        "blockers": sorted(set(live_result.blockers) | set(contract_result.blockers)
                           | set(economics_result.blockers) | set(capacity_doc["blockers"])
                           | set(budget_doc["blockers"])),
    }
    return _finalize(doc)


# ================================================================ TOP-LEVEL ASSEMBLY
class Phase71MResult:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _load_local_json(path):
    if path and os.path.exists(path):
        import json
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


def assemble_phase7_1m(repo_root, run_dir=None, mode="LOCAL_SAFE",
                       live_state=None, contract=None, economics=None):
    """Assemble every Phase 7.1M artifact IN MEMORY. Deterministic + network-free. Owner-supplied
    inputs (live_state / contract / economics) are optional dicts; when absent, deterministic templates
    are emitted and the readiness truthfully resolves to owner-input-required."""
    if mode not in CONNECTIVITY_MODES:
        raise Phase71MError(f"unknown connectivity mode: {mode}")

    handoff = P7.load_phase6_handoff(repo_root, run_dir=run_dir)
    network = P7.build_network_boundary_report(repo_root)

    # templates (always emitted — a template is never verified data).
    live_template = build_live_product_state_template(handoff)
    econ_template = build_economics_template(handoff)

    # live product state.
    live_result = validate_live_product_state(live_state, handoff)
    live_state_sha256 = content_sha256(live_state) if (
        live_state and not live_state.get("IS_TEMPLATE_NOT_VERIFIED_STATE")) else None

    # ppc product contract.
    contract_template = build_ppc_product_contract(handoff, live_result, live_state_sha256)
    contract_result = validate_ppc_product_contract(contract, handoff, live_result, live_state_sha256)

    # economics (feasibility math; live currency drives the currency-mismatch guard).
    live_currency = (live_state or {}).get("currency") if live_result.verified else None
    economics_result = compute_ppc_economics(economics, live_currency=live_currency)

    # capacity + handling (owner economics doc carries these operational inputs; fall back to live state).
    cap_inputs = dict(economics or {})
    for f in ("normal_capacity", "peak_capacity", "current_backlog", "safe_handling_time",
              "inventory_or_capacity_confirmed", "handling_time_confirmed"):
        if cap_inputs.get(f) is None and live_state:
            cap_inputs[f] = live_state.get(f)
    capacity_doc = evaluate_capacity_and_handling(cap_inputs)

    # budget feasibility.
    budget_doc = evaluate_budget_feasibility(economics or {}, economics_result, capacity_doc)

    # readiness (most restrictive).
    readiness = resolve_phase7_1m_readiness(handoff, live_result, contract_result, economics_result,
                                            capacity_doc, budget_doc, network, artifacts_verified=True)

    session_status = _session_status(readiness["phase7_1m_readiness"])

    return Phase71MResult(
        repo_root=repo_root, run_dir=run_dir, mode=mode, handoff=handoff, network=network,
        live_template=live_template, econ_template=econ_template, contract_template=contract_template,
        live_result=live_result, contract_result=contract_result, economics_result=economics_result,
        capacity_doc=capacity_doc, budget_doc=budget_doc, readiness=readiness,
        live_state_sha256=live_state_sha256,
        readiness_decision=readiness["phase7_1m_readiness"], session_status=session_status)


def _session_status(decision):
    return {
        READY_PREFLIGHT_BLOCKED: SESSION_PREFLIGHT_BLOCKED,
        READY_ECONOMICS_BLOCKED: SESSION_ECONOMICS_BLOCKED,
        READY_OWNER_INPUT: SESSION_OWNER_INPUT,
        READY_FOUNDATION_REVIEW: SESSION_FOUNDATION_READY,
        READY_CAMPAIGN_PLANNING: SESSION_FOUNDATION_READY,
    }[decision]


# ================================================================ INPUT COUNTING (sanitized proof)
def _input_accounting(result):
    lc = result.live_result.counts()
    ec = result.economics_result
    required = len(LIVE_STATE_REQUIRED_FIELDS) + len(_ECON_COST_FIELDS) + 4
    verified = lc[VERIFIED] + lc[NOT_APPLICABLE]
    missing = lc[MISSING] + len(ec.missing)
    contradictory = lc[CONTRADICTORY]
    return {"required_input_count": required, "verified_input_count": verified,
            "missing_input_count": missing, "contradictory_input_count": contradictory}


# ================================================================ MARKDOWN GUIDES
def _owner_input_md(result):
    r = result
    L = [f"# {r.readiness_decision}", "",
         "Phase 7.1M built the deterministic launch foundation. A campaign-planning session stays "
         "**blocked** until the owner supplies the live product state, the PPC contract, and complete "
         "economics for the advertised live SKU.", "",
         "## Phase 6 handoff (verified, unchanged)",
         f"- Phase 6 final status: `{r.handoff['phase6_final_status']}` (safe draft — NOT publishable)",
         f"- Listing publishability: `{r.handoff['listing_publishability']}`", "",
         "## What the owner must complete (never fabricated)",
         f"- Live product state: `{r.live_result.state}`",
         f"- PPC product contract: `{r.contract_result.state}`",
         f"- Economics inputs: `{r.economics_result.state}`",
         f"- Capacity: `{r.capacity_doc['capacity_state']}`   Handling: `{r.capacity_doc['handling_state']}`",
         f"- Budget feasibility: `{r.budget_doc['budget_feasibility_result']}`", "",
         "## The permanent Amazon boundary (re-asserted)",
         "- The owner is the ONLY manual bridge to Amazon. No login, SP-API/MWS/Ads API, browser "
         "automation, credential/cookie/token store, automated report retrieval, or public bind.",
         "- Enter values as canonical DECIMAL STRINGS (e.g. \"24.99\") — never floats. A missing cost "
         "stays missing; it is NEVER treated as 0.", ""]
    return "\n".join(L)


def _foundation_guide_md(result):
    r = result
    L = [f"# Phase 7.1M foundation — {r.session_status}", "",
         "## What this foundation does",
         "It answers ONE question deterministically: *given a verified live product and complete "
         "owner-confirmed economics, is this advertised SKU economically eligible to proceed to MANUAL "
         "campaign planning?* It builds no campaign, selects no target/negative, computes no bid, and "
         "allocates no budget.", "",
         "## Why there is no campaign package",
         "Phase 6 is a cryptographically verified **safe draft**, not a live listing. Campaign planning "
         "(7.1E+) is a separate, later, owner-authorized session. This session only lays the foundation.",
         "",
         "## Live Product State vs PPC Contract (separate on purpose)",
         "- **LIVE-PRODUCT-STATE** = the owner-confirmed real-world listing + fulfilment state.",
         "- **PPC-PRODUCT-CONTRACT** = binds that verified state to the verified Phase 6 package (by "
         "recomputed hashes). A template is never a verified contract; a hash mismatch never verifies.",
         "",
         "## Money is Decimal, never float",
         "Every price/cost/fee is a canonical decimal string parsed with `core.money` (Decimal). Floats, "
         "NaN, Infinity, blanks, and scientific notation are rejected. A missing value stays missing.",
         "",
         "## The economics (all owner-reviewable)",
         "```",
         "net_sales                     = selling_price - expected_discount",
         "non_ad_variable_costs         = sum(all required costs)   # never a silent 0",
         "contribution_margin_before_ads= net_sales - non_ad_variable_costs",
         "break_even_ad_cost_per_order  = contribution_margin_before_ads",
         "target_ad_cost_per_order      = contribution_margin_before_ads - minimum_required_profit",
         "break_even_acos               = break_even_ad_cost_per_order / net_sales",
         "target_acos                   = target_ad_cost_per_order / net_sales",
         "maximum_cpc                   = target_ad_cost_per_order * expected_ad_conversion_rate",
         "```",
         f"- `minimum_required_profit` default: **{DEFAULT_MIN_PROFIT} {DEFAULT_MIN_PROFIT_CURRENCY}** "
         "(declared + source-recorded; the owner may override). No other cost defaults to zero.",
         "- **maximum_cpc is a ceiling, not a recommended bid.** It is the highest CPC at which the "
         "target ACoS still holds. There is NO universal CPC clamp and NO CPC fallback.", "",
         "## Budget feasibility (no role allocation)",
         "```",
         "estimated_total_click_capacity = owner_total_test_budget / planned_cpc_candidate",
         "maximum_safe_click_capacity    = owner_total_test_budget / maximum_cpc   # a bound, not a bid",
         "```",
         "Campaign roles are NOT selected and campaign budgets are NOT allocated here. No $25 threshold, "
         "no fixed percentage, no universal click threshold, no universal learning window.", "",
         "## Readiness (most restrictive wins)",
         f"- Current decision: **{r.readiness_decision}** — {r.readiness['readiness_meaning']}",
         "- `PHASE7_OWNER_INPUT_REQUIRED` → supply the missing owner inputs.",
         "- `PHASE7_ECONOMICS_BLOCKED` → the SKU is not economically/operationally eligible.",
         "- `PHASE7_READY_FOR_CAMPAIGN_PLANNING` → a later MANUAL planning session may begin. This is "
         "**not** ready-for-manual-launch and **not** ready-for-Amazon-action (both always false here).",
         "", "## Exact next steps once the listing is live",
         "1. Complete `LIVE-PRODUCT-STATE.template.json` from a manual Seller Central screen-read.",
         "2. Complete `PPC-ECONOMICS.template.json` with real decimal costs.",
         "3. Bind them in `PPC-PRODUCT-CONTRACT.template.json` (Phase 6 hashes are pre-filled).",
         "4. Re-run this foundation; when everything verifies, readiness advances — then a later "
         "owner-authorized session plans campaigns manually.", ""]
    return "\n".join(L)


# ================================================================ WRITE / VERIFY / PROMOTE
def _stable_docs(result):
    """The stable JSON artifacts (dict form) keyed by filename."""
    return {
        F_LIVE_TEMPLATE: result.live_template,
        F_LIVE_VALIDATION: result.live_result.to_dict(),
        F_CONTRACT_TEMPLATE: result.contract_template,
        F_CONTRACT_VALIDATION: result.contract_result.to_dict(),
        F_ECON_TEMPLATE: result.econ_template,
        F_ECON_VALIDATION: result.economics_result.to_dict(),
        F_CAPACITY: result.capacity_doc,
        F_BUDGET: result.budget_doc,
        F_READINESS: result.readiness,
    }


def write_phase7_1m_candidate(result, candidate_dir, started_at=None, completed_at=None):
    """Build every artifact in memory and write it atomically into a bounded CANDIDATE directory. A
    failed write never overwrites a last-valid file (temp sibling + fsync + os.replace)."""
    os.makedirs(candidate_dir, exist_ok=True)
    output_hashes = {}
    for name, doc in _stable_docs(result).items():
        data = canonical_json(doc).encode("utf-8")
        _atomic_write_bytes(os.path.join(candidate_dir, name), data)
        output_hashes[name] = _sha_bytes(data)
    for name, text in ((F_OWNER_INPUT, _owner_input_md(result)),
                       (F_FOUNDATION_GUIDE, _foundation_guide_md(result))):
        data = text.encode("utf-8")
        _atomic_write_bytes(os.path.join(candidate_dir, name), data)
        output_hashes[name] = _sha_bytes(data)

    verification = _verification_report(result, candidate_dir, output_hashes)
    data = canonical_json(verification).encode("utf-8")
    _atomic_write_bytes(os.path.join(candidate_dir, F_VERIFICATION), data)
    output_hashes[F_VERIFICATION] = _sha_bytes(data)

    manifest = build_stage_manifest(result, output_hashes, started_at=started_at, completed_at=completed_at)
    _atomic_write_bytes(os.path.join(candidate_dir, F_STAGE_MANIFEST),
                        canonical_json(manifest).encode("utf-8"))
    return manifest, output_hashes


def verify_phase7_1m_candidate(candidate_dir, output_hashes):
    """Reopen every candidate file and verify its bytes against the recorded hash. Returns a report
    dict with result PASS / BLOCKED — a candidate is promoted only on PASS."""
    mismatched, missing = [], []
    for name, want in output_hashes.items():
        path = os.path.join(candidate_dir, name)
        if not os.path.exists(path):
            missing.append(name)
            continue
        with open(path, "rb") as f:
            got = _sha_bytes(f.read())
        if got != want:
            mismatched.append(name)
    ok = not mismatched and not missing and all(
        os.path.exists(os.path.join(candidate_dir, n)) for n in REQUIRED_ARTIFACTS)
    return {"result": "PASS" if ok else "BLOCKED", "mismatched": mismatched, "missing": missing,
            "candidate_file_count": len(output_hashes)}


def promote_phase7_1m_candidate(candidate_dir, final_dir, output_hashes):
    """Promote a VERIFIED candidate to *final_dir* (atomic per-file os.replace, manifest last). Refuses
    to promote a candidate that does not verify, so the last valid final artifacts are preserved."""
    report = verify_phase7_1m_candidate(candidate_dir, output_hashes)
    if report["result"] != "PASS":
        report["promoted"] = False
        return report
    os.makedirs(final_dir, exist_ok=True)
    ordered = [n for n in output_hashes if n != F_STAGE_MANIFEST] + [F_STAGE_MANIFEST]
    for name in ordered:
        src = os.path.join(candidate_dir, name)
        if os.path.exists(src):
            os.replace(src, os.path.join(final_dir, name))
    report["promoted"] = True
    report["final_dir_present"] = True
    return report


def verify_phase7_1m_artifacts(final_dir):
    """Re-verify the promoted artifacts from bytes against the stage manifest's recorded hashes."""
    man_path = os.path.join(final_dir, F_STAGE_MANIFEST)
    if not os.path.exists(man_path):
        return {"result": "BLOCKED", "reason": "stage manifest absent"}
    import json
    with open(man_path, encoding="utf-8") as f:
        man = json.load(f)
    return verify_phase7_1m_candidate(final_dir, man.get("output_hashes", {}))


def assemble_and_write_phase7_1m(result, run_dir, started_at=None, completed_at=None):
    """Convenience: write to a candidate dir, verify, and promote to the final 7.1M artifact dir."""
    base = os.path.join(run_dir, "phase7", "7.1M")
    candidate = os.path.join(base, ".candidate")
    manifest, hashes = write_phase7_1m_candidate(result, candidate, started_at=started_at,
                                                 completed_at=completed_at)
    report = promote_phase7_1m_candidate(candidate, base, hashes)
    if report["result"] == "PASS":
        try:
            os.rmdir(candidate)
        except OSError:
            pass
    return manifest, report, base


# ================================================================ STAGE MANIFEST
def build_stage_manifest(result, output_hashes, started_at=None, completed_at=None):
    r = result
    acc = _input_accounting(r)
    exp = _phase6_expected_hashes(r.handoff)
    body = {
        "schema_version": SCHEMA_STAGE_MANIFEST,
        "stage_id": STAGE_ID,
        "stage_name": STAGE_NAME,
        "workspace_id": r.handoff["workspace_id"],
        "product_id": r.handoff["product_id"],
        "phase7_0_proof": P7.PHASE6F_PROOF_NAME and "SESSION7_0-PROOF-GATE.json",
        "phase6_final_status": r.handoff["phase6_final_status"],
        "phase6_package_sha256": exp["phase6_package_sha256"],
        "phase6_index_sha256": exp["phase6_index_sha256"],
        "keyword_source_sha256": exp["keyword_source_sha256"],
        "keyword_allocation_sha256": exp["keyword_allocation_sha256"],
        "product_facts_sha256": exp["product_facts_sha256"],
        "claim_evidence_sha256": exp["claim_evidence_sha256"],
        "live_product_state_status": r.live_result.state,
        "live_product_state_sha256": r.live_state_sha256,
        "ppc_product_contract_status": r.contract_result.state,
        "economics_input_status": r.economics_result.input_state,
        "economics_result_status": r.economics_result.state,
        "capacity_status": r.capacity_doc["capacity_state"],
        "handling_status": r.capacity_doc["handling_state"],
        "budget_status": r.budget_doc["budget_feasibility_result"],
        "readiness_status": r.readiness_decision,
        "session_status": r.session_status,
        "required_input_count": acc["required_input_count"],
        "verified_input_count": acc["verified_input_count"],
        "missing_input_count": acc["missing_input_count"],
        "contradictory_input_count": acc["contradictory_input_count"],
        "blocker_count": len(r.readiness["blockers"]),
        "warning_count": len(r.live_result.warnings) + len(r.contract_result.warnings)
        + len(r.economics_result.warnings) + len(r.capacity_doc["warnings"])
        + len(r.budget_doc["warnings"]),
        "external_amazon_account_attempts": 0,
        "amazon_account_actions": 0,
        "campaign_write_actions": 0,
        "image_generation_calls": 0,
        "target_selection_count": 0,
        "negative_selection_count": 0,
        "bid_calculation_count": 0,
        "launch_package_count": 0,
        "later_phase_artifact_count": 0,
        "generated_outputs": sorted(output_hashes),
        "output_hashes": output_hashes,
        "deterministic_content_sha256": None,
    }
    body["deterministic_content_sha256"] = content_sha256(
        {k: v for k, v in body.items() if k not in _VOLATILE})
    if started_at:
        body["started_at"] = started_at
    if completed_at:
        body["completed_at"] = completed_at
    return body


def _verification_report(result, out_dir, output_hashes):
    r = result
    doc = {
        "schema_version": SCHEMA_VERIFICATION,
        "stage_id": STAGE_ID,
        "readiness_status": r.readiness_decision,
        "session_status": r.session_status,
        "required_artifact_count": len(REQUIRED_ARTIFACTS),
        "required_artifacts": list(REQUIRED_ARTIFACTS),
        "generated_output_count": len(output_hashes),
        "amazon_boundary": {
            "no_active_amazon_account_path": r.network["no_active_amazon_account_path"],
            "prohibited_account_path_count": r.network["prohibited_account_path_count"],
            "dashboard_bind_is_loopback": r.network["dashboard_bind_is_loopback"],
            "external_amazon_account_attempts": 0,
            "amazon_account_actions": 0,
            "campaign_write_actions": 0,
            "image_generation_calls": 0,
        },
        "this_session_never": {
            "selects_keywords": True, "selects_targets": True, "selects_negatives": True,
            "chooses_match_types": True, "creates_campaign_roles": True, "computes_bids": True,
            "allocates_campaign_budget": True, "emits_launch_package": True, "ingests_reports": True,
            "optimizes": True, "learns": True, "touches_amazon": True,
        },
        "target_selection_count": 0,
        "negative_selection_count": 0,
        "bid_calculation_count": 0,
        "launch_package_count": 0,
        "later_phase_artifact_count": 0,
    }
    return _finalize(doc)


# ================================================================ COMMITTED PROOF (sanitized)
def build_proof_gate(result, started_commit=None, final_commit=None,
                     baseline_tests=None, targeted_tests=None):
    r = result
    h = r.handoff
    acc = _input_accounting(r)
    ec = r.economics_result
    doc = {
        "schema_version": SCHEMA_PROOF_GATE,
        "session": "PHASE 7.1M — LIVE PRODUCT STATE, PPC CONTRACT, DECIMAL ECONOMICS, BUDGET "
                   "FEASIBILITY, AND READINESS FOUNDATION",
        "starting_commit": started_commit,
        "final_commit": final_commit,
        "phase7_1m_authority": "production/phase7_minimal_launch_foundation.py",
        "decimal_money_authority": "core/money.py",
        "connectivity_modes": list(CONNECTIVITY_MODES),
        "phase7_0_dependency": "SESSION7_0-PROOF-GATE.json",
        "phase7_0_readiness_dependency": P7.PHASE7_OWNER_INPUT_REQUIRED,
        # --- Phase 6 handoff (sanitized prefixes) ---
        "phase6_final_status": h["phase6_final_status"],
        "phase6_package_index_sha256_prefix": h["package_index_sha256_prefix"],
        "keyword_source_sha256_prefix": h["keyword_source_sha256_prefix"],
        "keyword_allocation_sha256_prefix": h["phase6b_manifest_sha256_prefix"],
        "product_facts_sha256_prefix": h["product_facts_sha256_prefix"],
        "claim_evidence_sha256_prefix": h["claim_evidence_sha256_prefix"],
        "product_id": h["product_id"],
        "canonical_category_id": h["canonical_category_id"],
        "marketplace": h["marketplace"],
        "listing_publishability": h["listing_publishability"],
        # --- authorities ---
        "live_product_state_schema": SCHEMA_LIVE_STATE_VALIDATION,
        "ppc_product_contract_schema": SCHEMA_CONTRACT_VALIDATION,
        "economics_schema": SCHEMA_ECONOMICS_VALIDATION,
        "duplicate_production_authority_count": 0,
        "decimal_convention": "core.money — Decimal only; float/NaN/Inf/blank/exponent rejected; "
                              "serialized as plain strings; ROUND_HALF_UP currency; no float in output.",
        # --- results (sanitized: states + prefixes only, no raw owner values) ---
        "live_product_state_result": r.live_result.state,
        "ppc_product_contract_result": r.contract_result.state,
        "economics_input_result": ec.input_state,
        "economics_result": ec.state,
        "selling_price_state": r.live_result.fields.get("current_selling_price", MISSING),
        "currency_state": r.live_result.fields.get("currency", MISSING),
        "minimum_profit_state": ec.min_profit_source,
        "expected_cvr_state": VERIFIED if ec.values.get("expected_ad_conversion_rate") is not None else MISSING,
        "net_sales_state": "BLOCKED_OR_MISSING" if not ec.viable else "COMPUTED",
        "contribution_margin_state": "BLOCKED_OR_MISSING" if not ec.viable else "COMPUTED",
        "break_even_acos_state": "BLOCKED_OR_MISSING" if not ec.viable else "COMPUTED",
        "target_acos_state": "BLOCKED_OR_MISSING" if not ec.viable else "COMPUTED",
        "maximum_cpc_state": "BLOCKED_OR_MISSING" if not ec.viable else "COMPUTED",
        "capacity_result": r.capacity_doc["capacity_state"],
        "handling_result": r.capacity_doc["handling_state"],
        "budget_input_result": r.budget_doc["budget_feasibility_result"],
        "budget_feasibility_result": r.budget_doc["budget_feasibility_result"],
        "readiness_result": r.readiness_decision,
        "session_status": r.session_status,
        # --- readiness booleans ---
        "ready_for_owner_input": r.readiness["ready_for_owner_input"],
        "ready_for_economics_review": r.readiness["ready_for_economics_review"],
        "ready_for_campaign_planning": r.readiness["ready_for_campaign_planning"],
        "ready_for_manual_launch": False,
        "ready_for_amazon_action": False,
        # --- input accounting ---
        "required_input_count": acc["required_input_count"],
        "verified_input_count": acc["verified_input_count"],
        "missing_input_count": acc["missing_input_count"],
        "contradictory_input_count": acc["contradictory_input_count"],
        # --- boundary + zero counters ---
        "external_amazon_account_attempts": 0,
        "amazon_account_actions": 0,
        "campaign_write_actions": 0,
        "image_generation_calls": 0,
        "target_selection_count": 0,
        "negative_selection_count": 0,
        "bid_calculation_count": 0,
        "launch_package_count": 0,
        "later_phase_artifact_count": 0,
        "network_no_active_amazon_account_path": r.network["no_active_amazon_account_path"],
        "prohibited_account_path_count": r.network["prohibited_account_path_count"],
        "dashboard_bind_is_loopback": r.network["dashboard_bind_is_loopback"],
        # --- determinism ---
        "artifact_stable_hashes": {
            F_LIVE_VALIDATION: _prefix(r.live_result.to_dict()["deterministic_content_sha256"]),
            F_CONTRACT_VALIDATION: _prefix(r.contract_result.to_dict()["deterministic_content_sha256"]),
            F_ECON_VALIDATION: _prefix(r.economics_result.to_dict()["deterministic_content_sha256"]),
            F_CAPACITY: _prefix(r.capacity_doc["deterministic_content_sha256"]),
            F_BUDGET: _prefix(r.budget_doc["deterministic_content_sha256"]),
            F_READINESS: _prefix(r.readiness["deterministic_content_sha256"]),
        },
        "required_local_artifacts": list(REQUIRED_ARTIFACTS),
        "baseline_tests": baseline_tests,
        "targeted_tests": targeted_tests,
        "sanitization_note": "States, prefixes, counts, and reason codes only. No absolute paths, no raw "
                             "owner values, no ASIN/SKU, no live price/capacity, no paid keyword rows, no "
                             "credentials. Local run artifacts stay under runs/ (gitignored).",
        "known_limitations": [
            "Phase 6 remains a verified SAFE DRAFT; no live listing / ASIN / SKU / price / capacity / "
            "handling / eligibility is owner-confirmed, so 7.1M resolves to owner-input-required.",
            "The $8.00 USD minimum-profit floor is a declared default applied only when the owner omits "
            "it (USD only) and is fully owner-reviewable.",
            "maximum_cpc is an economic ceiling, NOT a recommended bid; no bid/target/negative/budget "
            "allocation is performed this session.",
        ],
    }
    doc["deterministic_content_sha256"] = content_sha256(
        {k: v for k, v in doc.items()
         if k not in ("deterministic_content_sha256", "starting_commit", "final_commit",
                      "baseline_tests", "targeted_tests")})
    return doc


# ================================================================ CLI
def main(argv=None):
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Phase 7.1M minimal launch foundation (deterministic, offline)")
    ap.add_argument("--repo-root", default=_ROOT)
    ap.add_argument("--run-dir", default=None, help="local product workspace (e.g. runs/T2)")
    ap.add_argument("--mode", default="LOCAL_SAFE", choices=list(CONNECTIVITY_MODES))
    ap.add_argument("--live-state", default=None, help="owner live-product-state json (optional)")
    ap.add_argument("--contract", default=None, help="owner ppc-product-contract json (optional)")
    ap.add_argument("--economics", default=None, help="owner ppc-economics json (optional)")
    ap.add_argument("--proof-out", default=None)
    ap.add_argument("--starting-commit", default=None)
    a = ap.parse_args(argv)
    result = assemble_phase7_1m(a.repo_root, run_dir=a.run_dir, mode=a.mode,
                                live_state=_load_local_json(a.live_state),
                                contract=_load_local_json(a.contract),
                                economics=_load_local_json(a.economics))
    if a.run_dir:
        manifest, report, base = assemble_and_write_phase7_1m(result, a.run_dir)
        print(f"artifacts={base} promote={report['result']}")
    if a.proof_out:
        proof = build_proof_gate(result, started_commit=a.starting_commit)
        _atomic_write_bytes(a.proof_out, canonical_json(proof).encode("utf-8"))
    print(f"readiness={result.readiness_decision} session_status={result.session_status} "
          f"live={result.live_result.state} contract={result.contract_result.state} "
          f"economics={result.economics_result.state} capacity={result.capacity_doc['capacity_state']} "
          f"budget={result.budget_doc['budget_feasibility_result']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
