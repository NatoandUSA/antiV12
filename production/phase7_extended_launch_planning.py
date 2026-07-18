#!/usr/bin/env python3
"""production.phase7_extended_launch_planning — the ONE Phase 7.1E planning authority.

Phase 7.1E is the OWNER-GATED, offline, MANUAL Sponsored Products *planning* engine. Given the
accepted Phase 7.1M foundation (a verified live product + complete owner-confirmed economics) AND a
confirmed owner policy, it produces deterministic local PLANNING recommendations: eligible target
candidates, an owner-selected target set, optional negative candidates, campaign-role planning labels,
a starting-bid plan, a budget plan, and a manual-entry worksheet for the OWNER to review and type into
Seller Central by hand.

It NEVER touches Amazon. The owner is the only manual bridge. This module does not log in, use SP-API /
MWS / Ads API / browser automation / cookies / credentials / sessions / tokens / automated report
retrieval; it creates/updates NO campaign, ad group, target, negative, bid, budget, listing, price, or
inventory; it uploads nothing; it generates no Amazon API payload; it binds no public server. Every
Amazon-action counter is zero.

THIN by construction — it reuses the existing authorities and never re-implements them:
  * the whole launch foundation (live state / contract / economics / capacity / budget / readiness)
      -> production.phase7_minimal_launch_foundation  (F)
  * canonical json / content hash / atomic write / credential guard / tz check
      -> production.phase7_minimal_launch_foundation  (re-exported from product_workspace / preflight)
  * Decimal money parsing / serialization / division / rounding
      -> core.money  (MONEY)
There is no campaign_compiler / bid_engine / negative_engine / targeting_engine / _v2 duplicate here.

Public API:
  build_owner_policy_template / validate_owner_policy
  normalize_term / build_target_candidates / select_targets
  build_negative_candidates / select_negatives
  build_campaign_role_plan / build_bid_plan / build_budget_plan
  build_input_manifest / build_manual_entry_worksheet_csv / build_manual_entry_guide_md
  resolve_phase7_1e_readiness / assemble_phase7_1e
  write_phase7_1e_candidate / verify_phase7_1e_candidate / promote_phase7_1e_candidate
  build_stage_manifest / build_verification_report / build_proof_gate
"""
from __future__ import annotations

import os
import sys
import unicodedata
from decimal import ROUND_FLOOR

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ("", "core", "listing", "scripts"):
    _p = _ROOT if not _sub else os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import phase7_minimal_launch_foundation as F   # noqa: E402  the whole 7.1M foundation
from core import money as MONEY                                # noqa: E402  Decimal money authority

canonical_json = F.canonical_json
content_sha256 = F.content_sha256
_atomic_write_bytes = F._atomic_write_bytes
_sha_bytes = F._sha_bytes
_prefix = F._prefix
_contains_credentials = F._contains_credentials
_is_tz_aware_timestamp = F._is_tz_aware_timestamp

# ================================================================ constants / versions
STAGE_ID = "7.1E"
STAGE_NAME = "Owner-Gated Offline Manual Sponsored Products Planning Engine"

SCHEMA_OWNER_POLICY = "phase7-1e-owner-policy-v1"
SCHEMA_OWNER_POLICY_TEMPLATE = "phase7-1e-owner-policy-template-v1"
SCHEMA_OWNER_POLICY_VALIDATION = "phase7-1e-owner-policy-validation-v1"
SCHEMA_INPUT_MANIFEST = "phase7-1e-input-manifest-v1"
SCHEMA_TARGET_CANDIDATES = "phase7-1e-target-candidates-v1"
SCHEMA_SELECTED_TARGETS = "phase7-1e-selected-targets-v1"
SCHEMA_NEGATIVE_CANDIDATES = "phase7-1e-negative-candidates-v1"
SCHEMA_SELECTED_NEGATIVES = "phase7-1e-selected-negatives-v1"
SCHEMA_ROLE_PLAN = "phase7-1e-campaign-role-plan-v1"
SCHEMA_BID_PLAN = "phase7-1e-bid-plan-v1"
SCHEMA_BUDGET_PLAN = "phase7-1e-budget-plan-v1"
SCHEMA_READINESS = "phase7-1e-readiness-v1"
SCHEMA_VERIFICATION = "phase7-1e-verification-report-v1"
SCHEMA_STAGE_MANIFEST = "phase7-1e-stage-manifest-v1"
SCHEMA_PROOF_GATE = "session7_1e-proof-gate-v1"

CONNECTIVITY_MODES = F.CONNECTIVITY_MODES

# per-doc volatile keys excluded from the deterministic content hash.
_VOLATILE = F._VOLATILE

# -- field states (reuse F's vocabulary) -----------------------------------------------------------
VERIFIED = F.VERIFIED
MISSING = F.MISSING
CONTRADICTORY = F.CONTRADICTORY
NOT_APPLICABLE = F.NOT_APPLICABLE

# ================================================================ policy vocabularies (no defaults)
# campaign roles — only the three MANUAL roles are ever planned. AUTO_RESEARCH / PRODUCT_TARGETING are
# recognized but stay DISABLED unless the owner explicitly authorizes advanced targeting.
ROLE_MANUAL_EXACT = "MANUAL_EXACT"
ROLE_MANUAL_PHRASE = "MANUAL_PHRASE"
ROLE_MANUAL_BROAD = "MANUAL_BROAD"
MANUAL_ROLES = (ROLE_MANUAL_EXACT, ROLE_MANUAL_PHRASE, ROLE_MANUAL_BROAD)
ADVANCED_ROLES = ("AUTO_RESEARCH", "PRODUCT_TARGETING")   # recognized, disabled by default
ALL_KNOWN_ROLES = MANUAL_ROLES + ADVANCED_ROLES

# match types (planning only) — none enabled by default.
MATCH_EXACT = "EXACT"
MATCH_PHRASE = "PHRASE"
MATCH_BROAD = "BROAD"
MATCH_TYPES = (MATCH_EXACT, MATCH_PHRASE, MATCH_BROAD)
_MATCH_ROLE = {MATCH_EXACT: ROLE_MANUAL_EXACT, MATCH_PHRASE: ROLE_MANUAL_PHRASE,
               MATCH_BROAD: ROLE_MANUAL_BROAD}

# negative match types — no negative Broad; disabled by default.
NEG_EXACT = "NEGATIVE_EXACT"
NEG_PHRASE = "NEGATIVE_PHRASE"
NEGATIVE_MATCH_TYPES = (NEG_EXACT, NEG_PHRASE)

# bid methods — no method by default.
BID_OWNER_FIXED = "OWNER_FIXED"
BID_CEILING_SHARE = "CEILING_SHARE"
BID_TIERED_EVIDENCE = "TIERED_EVIDENCE"
BID_METHODS = (BID_OWNER_FIXED, BID_CEILING_SHARE, BID_TIERED_EVIDENCE)

# budget allocation methods.
BUDGET_FIXED_AMOUNTS = "FIXED_AMOUNTS"
BUDGET_SHARES = "SHARES"
BUDGET_METHODS = (BUDGET_FIXED_AMOUNTS, BUDGET_SHARES)

# evidence grade ladder (ascending) — an owner floor may only tighten it, never loosen.
EVIDENCE_GRADES = ("NONE", "WEAK", "MODERATE", "STRONG", "VERIFIED")
_GRADE_RANK = {g: i for i, g in enumerate(EVIDENCE_GRADES)}

# relevance tiers (ascending strength). TITLE_ONLY is never sufficient on its own.
TIER_TITLE_ONLY = "TITLE_ONLY"
TIER_RELATED = "RELATED"
TIER_DIRECT = "DIRECT"
_TIER_RANK = {TIER_TITLE_ONLY: 0, TIER_RELATED: 1, TIER_DIRECT: 2}

# source authority ordering (for deterministic selection; lower == more authoritative).
_SOURCE_AUTHORITY_RANK = {"PHASE6_ALLOCATION": 0, "PHASE6_SOURCE": 1, "OWNER": 2, "UNKNOWN": 3}

# allocation-state priority (for deterministic selection; lower == higher priority).
ALLOC_MARKET_IDENTITY = "MARKET_IDENTITY"
ALLOC_ADVERTISABLE = "ALLOCATED_ADVERTISABLE"
ALLOC_UNALLOCATED = "UNALLOCATED"
ALLOC_OWNER_FACT_REQUIRED = "OWNER_FACT_REQUIRED"
ALLOC_UNSUPPORTED = "UNSUPPORTED_PHYSICAL_CLAIM"
_ADVERTISABLE_ALLOC_STATES = (ALLOC_MARKET_IDENTITY, ALLOC_ADVERTISABLE)
_ALLOC_PRIORITY_RANK = {ALLOC_MARKET_IDENTITY: 0, ALLOC_ADVERTISABLE: 1, ALLOC_UNALLOCATED: 2}

# structural per-match-type gates (fixed rules that only RESTRICT — never a silent business default).
# EXACT demands the strongest direct relevance + evidence; BROAD additionally needs explicit owner
# permission, low ambiguity, and an owner-approved exploratory (broad) budget.
_MATCH_MIN_TIER = {MATCH_EXACT: TIER_DIRECT, MATCH_PHRASE: TIER_RELATED, MATCH_BROAD: TIER_RELATED}
_MATCH_MIN_GRADE = {MATCH_EXACT: "STRONG", MATCH_PHRASE: "MODERATE", MATCH_BROAD: "STRONG"}

# candidate not-selected reason (limit reached) — NEVER converted to a negative.
NOT_SELECTED_LIMIT = "ELIGIBLE_NOT_SELECTED_LIMIT"

# ================================================================ readiness (owner-facing) states
READY_PREFLIGHT_BLOCKED = "PHASE7_1E_PREFLIGHT_BLOCKED"
READY_OWNER_INPUT = "PHASE7_OWNER_INPUT_REQUIRED"
READY_ECONOMICS_BLOCKED = "PHASE7_ECONOMICS_BLOCKED"
READY_CAPACITY_BLOCKED = "PHASE7_CAPACITY_BLOCKED"
READY_HANDLING_BLOCKED = "PHASE7_HANDLING_BLOCKED"
READY_BUDGET_BLOCKED = "PHASE7_BUDGET_BLOCKED"
READY_OWNER_POLICY = "PHASE7_OWNER_POLICY_REQUIRED"
READY_TARGET_EVIDENCE_BLOCKED = "PHASE7_TARGET_EVIDENCE_BLOCKED"
READY_PLAN_VERIFICATION_BLOCKED = "PHASE7_PLAN_VERIFICATION_BLOCKED"
READY_PLAN_FOR_OWNER_REVIEW = "SESSION7_1E_PLAN_READY_FOR_OWNER_REVIEW"
SESSION_BLOCKED = "SESSION7_1E_BLOCKED"

# most-restrictive rank ladder (lower rank == more restrictive == wins).
_RANK = {
    READY_PREFLIGHT_BLOCKED: 0,
    READY_ECONOMICS_BLOCKED: 1,
    READY_CAPACITY_BLOCKED: 2,
    READY_HANDLING_BLOCKED: 3,
    READY_BUDGET_BLOCKED: 4,
    READY_OWNER_INPUT: 5,
    READY_OWNER_POLICY: 6,
    READY_TARGET_EVIDENCE_BLOCKED: 7,
    READY_PLAN_VERIFICATION_BLOCKED: 8,
    READY_PLAN_FOR_OWNER_REVIEW: 9,
}
_RANK_TO_STATE = {v: k for k, v in _RANK.items()}

# ================================================================ artifact filenames (exact)
F_OWNER_POLICY_VALIDATION = "OWNER-POLICY-VALIDATION.json"
F_OWNER_POLICY_TEMPLATE = "OWNER-POLICY.template.json"
F_INPUT_MANIFEST = "INPUT-MANIFEST.json"
F_TARGET_CANDIDATES = "TARGET-CANDIDATES.json"
F_SELECTED_TARGETS = "SELECTED-TARGETS.json"
F_NEGATIVE_CANDIDATES = "NEGATIVE-CANDIDATES.json"
F_SELECTED_NEGATIVES = "SELECTED-NEGATIVES.json"
F_ROLE_PLAN = "CAMPAIGN-ROLE-PLAN.json"
F_BID_PLAN = "BID-PLAN.json"
F_BUDGET_PLAN = "BUDGET-PLAN.json"
F_WORKSHEET = "MANUAL-ENTRY-WORKSHEET.csv"
F_ENTRY_GUIDE = "MANUAL-ENTRY-GUIDE.md"
F_READINESS = "PHASE7-1E-READINESS.json"
F_VERIFICATION = "PHASE7-1E-VERIFICATION-REPORT.json"
F_STAGE_MANIFEST = "STAGE-7_1E-MANIFEST.json"
F_OWNER_INPUT = "PHASE7-1E-OWNER-INPUT-REQUIRED.md"

# the full ready-plan artifact set (owner-policy template is always emitted alongside).
READY_ARTIFACTS = (F_OWNER_POLICY_VALIDATION, F_INPUT_MANIFEST, F_TARGET_CANDIDATES, F_SELECTED_TARGETS,
                   F_NEGATIVE_CANDIDATES, F_SELECTED_NEGATIVES, F_ROLE_PLAN, F_BID_PLAN, F_BUDGET_PLAN,
                   F_WORKSHEET, F_ENTRY_GUIDE, F_READINESS, F_VERIFICATION, F_STAGE_MANIFEST)
# when blocked, only these are emitted (never a misleading ready worksheet).
BLOCKED_ARTIFACTS = (F_OWNER_INPUT, F_READINESS, F_VERIFICATION, F_STAGE_MANIFEST)

WORKSHEET_BANNER = ("LOCAL PLANNING WORKSHEET ONLY — NOT UPLOADED TO AMAZON — "
                    "OWNER REVIEW AND MANUAL ENTRY REQUIRED")


class Phase71EError(Exception):
    pass


# ================================================================ small helpers
def _finalize(doc, extra_volatile=()):
    volatile = set(_VOLATILE) | set(extra_volatile)
    doc["deterministic_content_sha256"] = content_sha256(
        {k: v for k, v in doc.items() if k not in volatile})
    return doc


def _grade_ok(grade, floor):
    """True if *grade* is at least *floor* on the evidence ladder (unknown grade never passes)."""
    if grade not in _GRADE_RANK or floor not in _GRADE_RANK:
        return False
    return _GRADE_RANK[grade] >= _GRADE_RANK[floor]


# ---- normalization -------------------------------------------------------------------------------
def normalize_term(text):
    """Deterministic normalization of a raw term.

    Returns ``(display, canonical)`` or ``None`` when the term is empty or contains control
    characters. Applies Unicode NFKC, trims, and collapses internal whitespace; ``display`` preserves
    case, ``canonical`` is the lowercased comparison key. No wall-clock, no randomness.
    """
    if not isinstance(text, str):
        return None
    nfkc = unicodedata.normalize("NFKC", text)
    # reject control characters (category Cc/Cf) other than plain spaces we are about to collapse.
    for ch in nfkc:
        if ch in ("\t", "\n", "\r"):
            continue   # whitespace we will collapse
        if unicodedata.category(ch) in ("Cc", "Cf"):
            return None
    display = " ".join(nfkc.split())   # trim + collapse any whitespace run to a single space
    if display == "":
        return None
    canonical = display.lower()
    return display, canonical


def _csv_cell(value):
    """One deterministic, formula-injection-safe CSV cell.

    A cell whose text begins with ``= + - @`` (or a tab / CR that some importers treat specially) is
    prefixed with a single quote so a spreadsheet never evaluates it as a formula. Quoting follows RFC
    4180 (double-quote wrap + doubled inner quotes) when the value contains a comma, quote, or newline.
    """
    s = "" if value is None else str(value)
    if s[:1] in ("=", "+", "-", "@") or s[:1] in ("\t", "\r"):
        s = "'" + s
    if any(c in s for c in (",", '"', "\n", "\r")):
        s = '"' + s.replace('"', '""') + '"'
    return s


def _csv_row(cells):
    return ",".join(_csv_cell(c) for c in cells)


# ================================================================ OWNER POLICY AUTHORITY
# The owner policy is the single source of truth for what may be planned. Nothing is silently enabled
# or defaulted (only schema-safe, non-business metadata like schema_version is fixed). A template is
# never a confirmed policy.
_POLICY_REQUIRED_SECTIONS = (
    "allowed_campaign_roles", "allowed_match_types", "allowed_negative_match_types",
    "starting_bid_method", "total_daily_budget", "budget_allocation_method", "role_allocations",
    "reserve", "minimum_campaign_budget", "target_limits", "evidence_thresholds", "negative_policy",
)


def build_owner_policy_template():
    """A REQUIREMENTS template — blocked and unconfirmed. Every business field is null/empty; the owner
    fills them and sets ``owner_confirmed`` to true. A template is never a confirmed policy."""
    return {
        "schema_version": SCHEMA_OWNER_POLICY_TEMPLATE,
        "IS_TEMPLATE_NOT_CONFIRMED_POLICY": True,
        "instructions": "Complete every field, then set owner_confirmed=true with a tz-aware "
                        "owner_confirmed_at and owner_confirmation_source. Enter every money/rate value "
                        "as a canonical DECIMAL STRING (e.g. \"0.35\"), never a number literal or bool. "
                        "No role, match type, negative, bid method, or budget is enabled until you set "
                        "it here. Never paste credentials, cookies, tokens, or a Seller Central URL.",
        "policy_version": "1.0.0",
        "owner_confirmed": None,
        "owner_confirmed_at": None,
        "owner_confirmation_source": None,
        "allowed_campaign_roles": [],
        "authorize_advanced_targeting": None,   # AUTO_RESEARCH / PRODUCT_TARGETING stay off unless true
        "allowed_match_types": [],
        "authorize_broad": None,
        "allowed_negative_match_types": [],
        "starting_bid_method": None,
        "owner_fixed_bid": None,                # OWNER_FIXED
        "ceiling_share": None,                  # CEILING_SHARE (0 < share <= 1)
        "tier_shares": {},                      # TIERED_EVIDENCE: grade -> share
        "owner_minimum_bid": None,
        "owner_maximum_bid": None,
        "total_daily_budget": None,
        "budget_allocation_method": None,
        "role_allocations": {},                 # role -> share (SHARES) or amount (FIXED_AMOUNTS)
        "reserve": None,
        "minimum_campaign_budget": None,
        "target_limits": {},                    # max_targets_total / max_targets_per_match_type
        "evidence_thresholds": {},              # minimum_evidence_grade (+ optional per-match floors)
        "negative_policy": {"enabled": None, "require_documented_reason": True, "allowed_reasons": []},
        "inclusions": [],                       # explicit owner include terms (priority order)
        "exclusions": [],                       # explicit owner exclude terms
    }


class OwnerPolicyResult:
    def __init__(self, state, policy, blockers, warnings, resolved):
        self.state = state                # OWNER_POLICY_CONFIRMED / OWNER_POLICY_REQUIRED / OWNER_POLICY_INVALID
        self.policy = policy or {}
        self.blockers = blockers
        self.warnings = warnings
        self.resolved = resolved          # dict of parsed/normalized policy values (Decimals kept)

    @property
    def confirmed(self):
        return self.state == "OWNER_POLICY_CONFIRMED"

    def to_dict(self):
        r = self.resolved
        doc = {
            "schema_version": SCHEMA_OWNER_POLICY_VALIDATION,
            "owner_policy_result": self.state,
            "is_confirmed": self.confirmed,
            "owner_confirmed": bool(self.policy.get("owner_confirmed") is True),
            "allowed_campaign_roles": sorted(r.get("roles", [])),
            "allowed_match_types": sorted(r.get("match_types", [])),
            "allowed_negative_match_types": sorted(r.get("negative_match_types", [])),
            "advanced_targeting_authorized": bool(r.get("authorize_advanced_targeting")),
            "broad_authorized": bool(r.get("authorize_broad")),
            "starting_bid_method": r.get("bid_method"),
            "budget_allocation_method": r.get("budget_method"),
            "minimum_evidence_grade": r.get("min_grade"),
            "total_daily_budget": MONEY.serialize_currency(r.get("total_daily_budget")),
            "reserve": MONEY.serialize_currency(r.get("reserve")),
            "minimum_campaign_budget": MONEY.serialize_currency(r.get("minimum_campaign_budget")),
            "negatives_enabled": bool(r.get("negatives_enabled")),
            "inclusion_count": len(r.get("inclusions", [])),
            "exclusion_count": len(r.get("exclusions", [])),
            "no_role_default": True,
            "no_match_type_default": True,
            "no_bid_method_default": True,
            "negatives_disabled_by_default": True,
            "blockers": sorted(self.blockers),
            "warnings": sorted(self.warnings),
        }
        return _finalize(doc)


def _canon_list(values):
    """Normalize a list of terms to a de-duplicated, order-preserving list of (display, canonical)."""
    out, seen = [], set()
    for v in values or []:
        nz = normalize_term(v if isinstance(v, str) else (v or {}).get("term") if isinstance(v, dict) else v)
        if nz is None:
            continue
        if nz[1] in seen:
            continue
        seen.add(nz[1])
        out.append(nz)
    return out


def validate_owner_policy(policy):
    """Validate an owner policy. Returns OwnerPolicyResult. A template / absent policy / unconfirmed
    policy is OWNER_POLICY_REQUIRED; a present-but-malformed policy is OWNER_POLICY_INVALID."""
    blockers, warnings = [], []
    resolved = {"roles": [], "match_types": [], "negative_match_types": [], "inclusions": [],
                "exclusions": [], "tier_shares": {}, "role_allocations": {}, "negatives_enabled": False}

    if policy is None or policy.get("IS_TEMPLATE_NOT_CONFIRMED_POLICY"):
        return OwnerPolicyResult("OWNER_POLICY_REQUIRED", policy,
                                 ["owner policy not supplied / still a template (owner input required)"],
                                 warnings, resolved)
    if _contains_credentials(policy):
        return OwnerPolicyResult("OWNER_POLICY_INVALID", policy,
                                 ["credential/session/account material rejected from owner policy"],
                                 warnings, resolved)

    invalid = []

    # confirmation metadata (must be explicit).
    if policy.get("owner_confirmed") is not True:
        blockers.append("owner_confirmed is not True")
    if not _is_tz_aware_timestamp(policy.get("owner_confirmed_at")):
        blockers.append("owner_confirmed_at missing or not tz-aware ISO-8601")
    if policy.get("owner_confirmation_source") in (None, ""):
        blockers.append("owner_confirmation_source missing")

    # roles.
    adv_auth = policy.get("authorize_advanced_targeting") is True
    resolved["authorize_advanced_targeting"] = adv_auth
    for role in policy.get("allowed_campaign_roles") or []:
        if role in MANUAL_ROLES:
            resolved["roles"].append(role)
        elif role in ADVANCED_ROLES:
            if adv_auth:
                resolved["roles"].append(role)
            else:
                warnings.append(f"advanced role {role} recognized but stays DISABLED (not authorized)")
        else:
            invalid.append(f"unknown campaign role: {role!r}")
    resolved["roles"] = sorted(set(resolved["roles"]))
    if not any(rr in MANUAL_ROLES for rr in resolved["roles"]):
        blockers.append("no MANUAL campaign role enabled")

    # match types.
    auth_broad = policy.get("authorize_broad") is True
    resolved["authorize_broad"] = auth_broad
    for mt in policy.get("allowed_match_types") or []:
        if mt in MATCH_TYPES:
            if mt == MATCH_BROAD and not auth_broad:
                warnings.append("BROAD listed but not authorized (authorize_broad!=true) — disabled")
                continue
            resolved["match_types"].append(mt)
        else:
            invalid.append(f"unknown match type: {mt!r}")
    resolved["match_types"] = sorted(set(resolved["match_types"]))
    if not resolved["match_types"]:
        blockers.append("no match type enabled")

    # negatives (disabled by default).
    negpol = policy.get("negative_policy") or {}
    resolved["negatives_enabled"] = negpol.get("enabled") is True
    for nmt in policy.get("allowed_negative_match_types") or []:
        if nmt in NEGATIVE_MATCH_TYPES:
            resolved["negative_match_types"].append(nmt)
        else:
            invalid.append(f"unknown / prohibited negative match type: {nmt!r}")   # no negative Broad
    resolved["negative_match_types"] = sorted(set(resolved["negative_match_types"]))
    if resolved["negatives_enabled"] and not resolved["negative_match_types"]:
        blockers.append("negatives enabled but no allowed negative match type")

    # bid method + values.
    method = policy.get("starting_bid_method")
    resolved["bid_method"] = method
    if method not in BID_METHODS:
        blockers.append("starting_bid_method missing or not one of OWNER_FIXED/CEILING_SHARE/TIERED_EVIDENCE")
    else:
        _validate_bid_values(method, policy, resolved, invalid, blockers)

    # evidence thresholds.
    thr = policy.get("evidence_thresholds") or {}
    min_grade = thr.get("minimum_evidence_grade")
    resolved["min_grade"] = min_grade
    if min_grade not in _GRADE_RANK:
        blockers.append("evidence_thresholds.minimum_evidence_grade missing or not a valid grade")

    # budget.
    _validate_budget_values(policy, resolved, invalid, blockers)

    # limits.
    limits = policy.get("target_limits") or {}
    resolved["target_limits"] = {}
    for k in ("max_targets_total", "max_targets_per_match_type", "max_targets_per_role"):
        v = limits.get(k)
        if v is None:
            continue
        try:
            resolved["target_limits"][k] = MONEY.parse_positive_int(v, field=k)
        except MONEY.MoneyError as e:
            invalid.append(f"target_limits.{k} invalid: {e}")
    if "max_targets_total" not in resolved["target_limits"]:
        blockers.append("target_limits.max_targets_total missing")

    # explicit inclusions / exclusions.
    resolved["inclusions"] = _canon_list(policy.get("inclusions"))
    resolved["exclusions"] = _canon_list(policy.get("exclusions"))

    if invalid:
        return OwnerPolicyResult("OWNER_POLICY_INVALID", policy, sorted(invalid + blockers), warnings,
                                 resolved)
    if blockers:
        return OwnerPolicyResult("OWNER_POLICY_REQUIRED", policy, blockers, warnings, resolved)
    return OwnerPolicyResult("OWNER_POLICY_CONFIRMED", policy, blockers, warnings, resolved)


def _dec(value, field, invalid, *, parser=None):
    parser = parser or MONEY.parse_positive_decimal
    try:
        return parser(value, field=field, allow_missing=False)
    except MONEY.MoneyError as e:
        invalid.append(f"{field} invalid: {e}")
        return None


def _validate_bid_values(method, policy, resolved, invalid, blockers):
    # optional min/max constraints (both nonnegative, min<=max).
    lo = policy.get("owner_minimum_bid")
    hi = policy.get("owner_maximum_bid")
    resolved["owner_minimum_bid"] = _dec(lo, "owner_minimum_bid", invalid,
                                         parser=MONEY.parse_nonnegative_decimal) if lo is not None else None
    resolved["owner_maximum_bid"] = _dec(hi, "owner_maximum_bid", invalid) if hi is not None else None
    if (resolved.get("owner_minimum_bid") is not None and resolved.get("owner_maximum_bid") is not None
            and resolved["owner_minimum_bid"] > resolved["owner_maximum_bid"]):
        blockers.append("owner_minimum_bid > owner_maximum_bid")

    if method == BID_OWNER_FIXED:
        v = policy.get("owner_fixed_bid")
        if v is None:
            blockers.append("OWNER_FIXED requires owner_fixed_bid")
        else:
            resolved["owner_fixed_bid"] = _dec(v, "owner_fixed_bid", invalid)
    elif method == BID_CEILING_SHARE:
        v = policy.get("ceiling_share")
        if v is None:
            blockers.append("CEILING_SHARE requires ceiling_share")
        else:
            resolved["ceiling_share"] = _dec(v, "ceiling_share", invalid, parser=MONEY.parse_rate_decimal)
    elif method == BID_TIERED_EVIDENCE:
        shares = policy.get("tier_shares") or {}
        if not shares:
            blockers.append("TIERED_EVIDENCE requires tier_shares (grade -> share)")
        parsed = {}
        for grade, share in sorted(shares.items()):
            if grade not in _GRADE_RANK:
                invalid.append(f"tier_shares has unknown grade: {grade!r}")
                continue
            d = _dec(share, f"tier_shares.{grade}", invalid, parser=MONEY.parse_rate_decimal)
            if d is not None:
                parsed[grade] = d
        resolved["tier_shares"] = parsed


def _validate_budget_values(policy, resolved, invalid, blockers):
    method = policy.get("budget_allocation_method")
    resolved["budget_method"] = method
    total = policy.get("total_daily_budget")
    reserve = policy.get("reserve")
    min_camp = policy.get("minimum_campaign_budget")
    resolved["total_daily_budget"] = _dec(total, "total_daily_budget", invalid) if total is not None else None
    resolved["reserve"] = (_dec(reserve, "reserve", invalid, parser=MONEY.parse_nonnegative_decimal)
                           if reserve is not None else None)
    resolved["minimum_campaign_budget"] = (_dec(min_camp, "minimum_campaign_budget", invalid)
                                           if min_camp is not None else None)
    if resolved["total_daily_budget"] is None:
        blockers.append("total_daily_budget missing")
    if resolved["reserve"] is None:
        blockers.append("reserve missing")
    if resolved["minimum_campaign_budget"] is None:
        blockers.append("minimum_campaign_budget missing")
    if method not in BUDGET_METHODS:
        blockers.append("budget_allocation_method missing or not FIXED_AMOUNTS/SHARES")

    allocs = policy.get("role_allocations") or {}
    parsed = {}
    for role, val in sorted(allocs.items()):
        if role not in MANUAL_ROLES:
            invalid.append(f"role_allocations has non-manual role: {role!r}")
            continue
        if method == BUDGET_SHARES:
            parsed[role] = _dec(val, f"role_allocations.{role}", invalid, parser=MONEY.parse_rate_decimal)
        else:
            parsed[role] = _dec(val, f"role_allocations.{role}", invalid)
    resolved["role_allocations"] = {k: v for k, v in parsed.items() if v is not None}
    if not resolved["role_allocations"]:
        blockers.append("role_allocations missing")


# ================================================================ TARGET CANDIDATES + ELIGIBILITY
class TargetCandidate:
    __slots__ = ("display_term", "canonical_term", "provenance", "source_authority", "allocation_state",
                 "advertising_permitted", "evidence_grade", "relevance_tier", "relevance_reasons",
                 "compliance_flags", "in_title", "ambiguity", "allowed_match_types", "eligible",
                 "blocking_reasons", "source_hash")

    def to_dict(self):
        return {
            "display_term": self.display_term,
            "canonical_term": self.canonical_term,
            "provenance": sorted(self.provenance),
            "source_authority": self.source_authority,
            "allocation_state": self.allocation_state,
            "advertising_permitted": self.advertising_permitted,
            "evidence_grade": self.evidence_grade,
            "relevance_tier": self.relevance_tier,
            "relevance_reasons": sorted(self.relevance_reasons),
            "compliance_flags": sorted(self.compliance_flags),
            "in_title": self.in_title,
            "ambiguity": self.ambiguity,
            "allowed_match_types": sorted(self.allowed_match_types),
            "eligible": self.eligible,
            "blocking_reasons": sorted(self.blocking_reasons),
            "source_hash": self.source_hash,
        }


def _qualifies_match_type(mt, cand, resolved):
    """Structural per-match-type gate: only ever restricts. Returns (bool, reason_or_None)."""
    if _TIER_RANK.get(cand.relevance_tier, -1) < _TIER_RANK[_MATCH_MIN_TIER[mt]]:
        return False, f"{mt}_requires_tier_>={_MATCH_MIN_TIER[mt]}"
    if not _grade_ok(cand.evidence_grade, _MATCH_MIN_GRADE[mt]):
        return False, f"{mt}_requires_grade_>={_MATCH_MIN_GRADE[mt]}"
    if mt == MATCH_BROAD:
        if not resolved.get("authorize_broad"):
            return False, "broad_not_authorized"
        if cand.ambiguity != "LOW":
            return False, "broad_requires_low_ambiguity"
        if ROLE_MANUAL_BROAD not in resolved.get("roles", []):
            return False, "broad_role_not_enabled"
    return True, None


def build_target_candidates(keyword_allocation, keyword_source, product_facts, claim_evidence, policy_result):
    """Build the deterministic target-candidate set from the Phase 6 keyword allocation, cross-checked
    against the keyword source, product facts, and claim evidence, under the owner policy.

    Returns a list of TargetCandidate. A candidate is eligible ONLY when every gate passes (see the
    session contract). Title placement or volume ALONE never qualifies a term; an unallocated or
    OWNER_FACT_REQUIRED / unsupported-claim term is never eligible unless the owner policy explicitly
    allows unallocated terms. Deterministic normalization + de-duplication (reasons/provenance unioned).
    """
    resolved = policy_result.resolved if policy_result else {}
    allowed_mt = set(resolved.get("match_types", []))
    exclusions = {c for _d, c in resolved.get("exclusions", [])}
    min_grade = resolved.get("min_grade")
    allow_unallocated = bool((policy_result.policy if policy_result else {}).get("allow_unallocated_terms")
                             is True)

    source_terms = _source_canonical_set(keyword_source)
    facts_terms = _facts_relevance_set(product_facts)
    blocked_claim_terms = _claim_blocked_set(claim_evidence)

    by_canon = {}
    for raw in (keyword_allocation or {}).get("advertising_candidates", []) or []:
        nz = normalize_term(raw.get("term"))
        if nz is None:
            continue
        display, canonical = nz
        cand = by_canon.get(canonical)
        if cand is None:
            cand = TargetCandidate()
            cand.display_term = display
            cand.canonical_term = canonical
            cand.provenance = set()
            cand.relevance_reasons = set()
            cand.compliance_flags = set()
            cand.source_authority = "UNKNOWN"
            cand.allocation_state = raw.get("allocation_state") or ALLOC_UNALLOCATED
            cand.advertising_permitted = bool(raw.get("advertising_permitted"))
            cand.evidence_grade = raw.get("evidence_grade") or "NONE"
            cand.relevance_tier = raw.get("relevance_tier") or TIER_TITLE_ONLY
            cand.in_title = bool(raw.get("in_title"))
            cand.ambiguity = raw.get("ambiguity") or "UNKNOWN"
            cand.allowed_match_types = set()
            cand.eligible = False
            cand.blocking_reasons = set()
            by_canon[canonical] = cand
        # deterministic union / most-authoritative merge.
        for p in raw.get("provenance", []) or ([raw.get("source_authority")] if raw.get("source_authority") else []):
            if p:
                cand.provenance.add(p)
        for rr in raw.get("relevance_reasons", []) or []:
            cand.relevance_reasons.add(rr)
        for cf in raw.get("compliance_flags", []) or []:
            cand.compliance_flags.add(cf)
        sa = raw.get("source_authority") or ("PHASE6_ALLOCATION" if "PHASE6_ALLOCATION" in cand.provenance else "UNKNOWN")
        if _SOURCE_AUTHORITY_RANK.get(sa, 9) < _SOURCE_AUTHORITY_RANK.get(cand.source_authority, 9):
            cand.source_authority = sa
        # strongest evidence / relevance wins the merge (deterministic).
        if _GRADE_RANK.get(raw.get("evidence_grade"), -1) > _GRADE_RANK.get(cand.evidence_grade, -1):
            cand.evidence_grade = raw.get("evidence_grade")
        if _TIER_RANK.get(raw.get("relevance_tier"), -1) > _TIER_RANK.get(cand.relevance_tier, -1):
            cand.relevance_tier = raw.get("relevance_tier")
        if raw.get("advertising_permitted"):
            cand.advertising_permitted = True
        if raw.get("in_title"):
            cand.in_title = True

    for canonical, cand in by_canon.items():
        reasons = cand.blocking_reasons
        # 1. valid normalized term — guaranteed (built from normalize_term).
        # 2. source provenance exists.
        if not cand.provenance:
            reasons.add("no_provenance")
        if source_terms is not None and canonical not in source_terms:
            reasons.add("term_absent_from_keyword_source")
        # 3. Phase 6 allocation permits advertising.
        advertisable_states = set(_ADVERTISABLE_ALLOC_STATES) | ({ALLOC_UNALLOCATED} if allow_unallocated else set())
        if cand.allocation_state not in advertisable_states:
            reasons.add(f"allocation_state_{cand.allocation_state}_not_advertisable")
        if not cand.advertising_permitted:
            reasons.add("advertising_not_permitted_by_allocation")
        if cand.allocation_state == ALLOC_UNALLOCATED and not allow_unallocated:
            reasons.add("unallocated_term_not_permitted")
        # 4. evidence grade meets owner threshold.
        if not (min_grade and _grade_ok(cand.evidence_grade, min_grade)):
            reasons.add("evidence_grade_below_owner_threshold")
        # 5. product facts support relevance (title-only / volume-only never suffice).
        if cand.relevance_tier == TIER_TITLE_ONLY:
            reasons.add("title_only_relevance_rejected")
        if not cand.relevance_reasons:
            reasons.add("no_relevance_reason")
        if facts_terms is not None and canonical not in facts_terms and not (cand.relevance_reasons & {"market_identity_match"}):
            reasons.add("relevance_not_supported_by_product_facts")
        # 6. claim evidence compatible.
        if canonical in blocked_claim_terms:
            reasons.add("claim_evidence_blocked_or_mixed")
        # 7. compliance flags pass.
        if cand.compliance_flags:
            reasons.add("compliance_flag_present")
        # 8. owner exclusion does not apply.
        if canonical in exclusions:
            reasons.add("owner_excluded")
        # 9. at least one allowed match type applies.
        for mt in sorted(allowed_mt):
            ok, _why = _qualifies_match_type(mt, cand, resolved)
            if ok:
                cand.allowed_match_types.add(mt)
        if not cand.allowed_match_types:
            reasons.add("no_allowed_match_type_qualifies")
        cand.source_hash = _prefix(content_sha256({
            "canonical_term": canonical, "allocation_state": cand.allocation_state,
            "evidence_grade": cand.evidence_grade, "relevance_tier": cand.relevance_tier}))
        cand.eligible = not reasons

    return sorted(by_canon.values(), key=lambda c: c.canonical_term)


def _source_canonical_set(keyword_source):
    if keyword_source is None:
        return None
    terms = keyword_source.get("terms") if isinstance(keyword_source, dict) else keyword_source
    out = set()
    for t in terms or []:
        nz = normalize_term(t if isinstance(t, str) else (t or {}).get("term"))
        if nz is not None:
            out.add(nz[1])
    return out


def _facts_relevance_set(product_facts):
    if product_facts is None:
        return None
    out = set()
    for t in (product_facts.get("market_identity_terms") if isinstance(product_facts, dict) else []) or []:
        nz = normalize_term(t)
        if nz is not None:
            out.add(nz[1])
    return out


def _claim_blocked_set(claim_evidence):
    if claim_evidence is None:
        return set()
    out = set()
    for t in (claim_evidence.get("blocked_terms") if isinstance(claim_evidence, dict) else []) or []:
        nz = normalize_term(t)
        if nz is not None:
            out.add(nz[1])
    return out


# ================================================================ DETERMINISTIC SELECTION
def _inclusion_priority(canonical, resolved):
    for i, (_d, c) in enumerate(resolved.get("inclusions", [])):
        if c == canonical:
            return i
    return len(resolved.get("inclusions", [])) + 1_000_000


def _selection_sort_key(cand, mt, resolved):
    return (
        _inclusion_priority(cand.canonical_term, resolved),               # 1 explicit owner inclusion
        -_GRADE_RANK.get(cand.evidence_grade, -1),                        # 2 evidence grade (desc)
        -_TIER_RANK.get(cand.relevance_tier, -1),                         # 3 direct relevance tier (desc)
        _SOURCE_AUTHORITY_RANK.get(cand.source_authority, 9),             # 4 source authority
        _ALLOC_PRIORITY_RANK.get(cand.allocation_state, 9),              # 5 allocation priority
        cand.canonical_term,                                              # 6 canonical term
        mt,                                                               # stable per match type
    )


def select_targets(candidates, policy_result):
    """Deterministically select (canonical_term, match_type) target rows under the owner limits. Each
    eligible candidate yields one row per allowed+qualifying match type. Overflow beyond a limit is
    marked ELIGIBLE_NOT_SELECTED_LIMIT and is NEVER converted to a negative. Returns (selected, deferred).
    """
    resolved = policy_result.resolved
    limits = resolved.get("target_limits", {})
    max_total = limits.get("max_targets_total")
    max_per_mt = limits.get("max_targets_per_match_type")
    max_per_role = limits.get("max_targets_per_role")

    rows = []
    for cand in candidates:
        if not cand.eligible:
            continue
        for mt in sorted(cand.allowed_match_types):
            rows.append((cand, mt))
    rows.sort(key=lambda cm: _selection_sort_key(cm[0], cm[1], resolved))

    selected, deferred = [], []
    per_mt, per_role, total = {}, {}, 0
    for cand, mt in rows:
        role = _MATCH_ROLE[mt]
        reason = None
        if max_total is not None and total >= max_total:
            reason = "max_targets_total"
        elif max_per_mt is not None and per_mt.get(mt, 0) >= max_per_mt:
            reason = "max_targets_per_match_type"
        elif max_per_role is not None and per_role.get(role, 0) >= max_per_role:
            reason = "max_targets_per_role"
        entry = {"canonical_term": cand.canonical_term, "display_term": cand.display_term,
                 "match_type": mt, "campaign_role": role, "evidence_grade": cand.evidence_grade,
                 "relevance_tier": cand.relevance_tier, "source_authority": cand.source_authority,
                 "allocation_state": cand.allocation_state}
        if reason is None:
            selected.append(entry)
            per_mt[mt] = per_mt.get(mt, 0) + 1
            per_role[role] = per_role.get(role, 0) + 1
            total += 1
        else:
            deferred.append({**entry, "status": NOT_SELECTED_LIMIT, "limit_reason": reason})
    selected.sort(key=lambda e: (e["match_type"], e["canonical_term"]))
    deferred.sort(key=lambda e: (e["match_type"], e["canonical_term"]))
    return selected, deferred


# ================================================================ NEGATIVES (owner-gated, never auto)
def build_negative_candidates(policy_result, selected_targets):
    """Build negative candidates ONLY from explicit owner exclusion entries carrying a documented reason
    and an allowed negative match type. Never derived from unselected/low-priority/low-volume/absent
    terms. Enforces the positive/negative conflict check against selected targets. Returns a list."""
    resolved = policy_result.resolved
    if not resolved.get("negatives_enabled"):
        return []
    allowed = set(resolved.get("negative_match_types", []))
    require_reason = (policy_result.policy.get("negative_policy") or {}).get("require_documented_reason", True)
    selected_canon = {e["canonical_term"] for e in selected_targets}

    out = []
    for entry in policy_result.policy.get("negative_exclusions", []) or []:
        nz = normalize_term(entry.get("term"))
        if nz is None:
            continue
        display, canonical = nz
        reasons = []
        mt = entry.get("negative_match_type")
        if mt not in NEGATIVE_MATCH_TYPES:
            reasons.append("negative_match_type_invalid_or_broad_prohibited")
        elif mt not in allowed:
            reasons.append("negative_match_type_not_owner_enabled")
        documented = entry.get("exclusion_reason")
        if require_reason and (documented in (None, "")):
            reasons.append("missing_documented_exclusion_reason")
        if not entry.get("evidence"):
            reasons.append("missing_evidence_of_wrong_product_or_audience")
        if canonical in selected_canon:
            reasons.append("conflicts_with_selected_positive_target")
        out.append({"canonical_term": canonical, "display_term": display, "negative_match_type": mt,
                    "exclusion_reason": documented, "evidence": entry.get("evidence"),
                    "eligible": not reasons, "blocking_reasons": sorted(reasons)})
    out.sort(key=lambda e: (e["negative_match_type"] or "", e["canonical_term"]))
    return out


def select_negatives(negative_candidates):
    sel = [n for n in negative_candidates if n.get("eligible")]
    sel.sort(key=lambda e: (e["negative_match_type"], e["canonical_term"]))
    return sel


# ================================================================ CAMPAIGN ROLE PLANNING (labels only)
def build_campaign_role_plan(handoff, policy_result, selected_targets):
    """Local planning labels only (e.g. T2_US_SP_MANUAL_EXACT_V1). No Amazon IDs, no claim a campaign
    exists. The owner may rename manually. A role plan entry appears only for a role the owner enabled
    that also has at least one selected target."""
    resolved = policy_result.resolved
    marketplace = handoff["marketplace"]
    product = handoff["product_id"]
    by_role = {}
    for e in selected_targets:
        by_role.setdefault(e["campaign_role"], []).append(e)
    roles = []
    for role in sorted(resolved.get("roles", [])):
        if role not in MANUAL_ROLES:
            continue
        members = by_role.get(role, [])
        roles.append({
            "campaign_role": role,
            "planning_label": f"{product}_{marketplace}_SP_{role}_V1",
            "match_type": {ROLE_MANUAL_EXACT: MATCH_EXACT, ROLE_MANUAL_PHRASE: MATCH_PHRASE,
                           ROLE_MANUAL_BROAD: MATCH_BROAD}[role],
            "selected_target_count": len(members),
            "is_amazon_campaign": False,
            "owner_may_rename": True,
        })
    roles.sort(key=lambda r: r["campaign_role"])
    return {"schema_version": SCHEMA_ROLE_PLAN, "roles": roles, "role_count": len(roles),
            "no_amazon_ids": True, "labels_are_local_planning_only": True}


# ================================================================ BID PLANNING (ceiling-aware)
def build_bid_plan(economics_result, policy_result, selected_targets):
    """Compute a starting-bid plan row per selected target. The accepted 7.1M maximum_cpc is the
    economic CEILING, never an automatic bid. No zero / fixed-CPC fallback. Records the method, policy
    value, raw bid, rounded bid, min/max constraints, ceiling check, and status per row."""
    resolved = policy_result.resolved
    method = resolved.get("bid_method")
    max_cpc = economics_result.derived.get("maximum_cpc") if economics_result else None
    lo = resolved.get("owner_minimum_bid")
    hi = resolved.get("owner_maximum_bid")

    rows, blockers = [], []
    if max_cpc is None or max_cpc <= 0:
        blockers.append("MAXIMUM_CPC_NON_POSITIVE: no economic ceiling available for bidding")
    for e in selected_targets:
        row = {"canonical_term": e["canonical_term"], "match_type": e["match_type"],
               "campaign_role": e["campaign_role"], "evidence_grade": e["evidence_grade"],
               "economic_maximum_cpc": MONEY.serialize_currency(max_cpc), "bid_method": method,
               "policy_value": None, "raw_starting_bid": None, "rounded_starting_bid": None,
               "owner_minimum_bid": MONEY.serialize_currency(lo) if lo is not None else None,
               "owner_maximum_bid": MONEY.serialize_currency(hi) if hi is not None else None,
               "within_ceiling": None, "status": "BID_BLOCKED"}
        if max_cpc is None or max_cpc <= 0:
            row["status"] = "BID_BLOCKED_NO_CEILING"
            rows.append(row)
            continue
        raw, policy_value = _raw_bid(method, resolved, max_cpc, e["evidence_grade"])
        row["policy_value"] = policy_value
        if raw is None:
            row["status"] = "BID_BLOCKED_NO_POLICY_VALUE"
            rows.append(row)
            continue
        rounded = MONEY.quantize_currency(raw)
        row["raw_starting_bid"] = MONEY.decimal_to_canonical_string(raw)
        row["rounded_starting_bid"] = MONEY.serialize_currency(rounded)
        within = rounded <= max_cpc
        row["within_ceiling"] = within
        problems = []
        if not within:
            problems.append("exceeds_economic_ceiling")
        if lo is not None and rounded < lo:
            problems.append("below_owner_minimum")
        if hi is not None and rounded > hi:
            problems.append("above_owner_maximum")
        if rounded <= 0:
            problems.append("non_positive_bid")
        row["status"] = "BID_OK" if not problems else "BID_BLOCKED"
        row["bid_problems"] = sorted(problems)
        rows.append(row)
    rows.sort(key=lambda r: (r["match_type"], r["canonical_term"]))
    blocked = [r for r in rows if r["status"] != "BID_OK"]
    return {"schema_version": SCHEMA_BID_PLAN, "bid_method": method,
            "economic_maximum_cpc": MONEY.serialize_currency(max_cpc),
            "maximum_cpc_is_a_ceiling_not_a_bid": True, "no_zero_or_fixed_cpc_fallback": True,
            "rows": rows, "row_count": len(rows), "blocked_row_count": len(blocked),
            "blockers": sorted(blockers)}


def _raw_bid(method, resolved, max_cpc, grade):
    """Return (raw_decimal_or_None, policy_value_string). No fallback to zero / fixed CPC."""
    with MONEY.localcontext() as ctx:  # type: ignore[attr-defined]
        ctx.prec = 50
        if method == BID_OWNER_FIXED:
            v = resolved.get("owner_fixed_bid")
            return (v, MONEY.decimal_to_canonical_string(v)) if v is not None else (None, None)
        if method == BID_CEILING_SHARE:
            share = resolved.get("ceiling_share")
            if share is None:
                return None, None
            return max_cpc * share, MONEY.decimal_to_canonical_string(share)
        if method == BID_TIERED_EVIDENCE:
            share = resolved.get("tier_shares", {}).get(grade)
            if share is None:
                return None, None
            return max_cpc * share, MONEY.decimal_to_canonical_string(share)
    return None, None


# ================================================================ BUDGET PLANNING (exact reconciliation)
def build_budget_plan(policy_result, role_plan):
    """Allocate the owner total daily budget across the enabled MANUAL roles. SHARES sum to exactly 1.00
    over allocatable = total - reserve, with deterministic largest-remainder rounding so the rounded
    role budgets sum EXACTLY to allocatable. FIXED_AMOUNTS: role amounts + reserve equal total exactly.
    Blocks invalid / non-positive / minimum-violating values. No fixed percentages / universal $25."""
    resolved = policy_result.resolved
    method = resolved.get("budget_method")
    total = resolved.get("total_daily_budget")
    reserve = resolved.get("reserve")
    min_camp = resolved.get("minimum_campaign_budget")
    allocs = resolved.get("role_allocations", {})
    plan_roles = [r["campaign_role"] for r in role_plan.get("roles", [])]

    blockers = []
    rows = []
    if total is None or reserve is None or min_camp is None or method not in BUDGET_METHODS:
        return _budget_doc(method, total, reserve, None, rows, ["budget inputs incomplete"])
    if reserve >= total:
        blockers.append("reserve >= total_daily_budget")
    allocatable = total - reserve
    if allocatable <= 0:
        blockers.append("allocatable (total - reserve) is non-positive")

    # only allocate to roles that are BOTH enabled and have a plan entry.
    roles = sorted(r for r in plan_roles if r in allocs)
    missing_alloc = sorted(r for r in plan_roles if r not in allocs)
    if missing_alloc:
        blockers.append(f"role_allocations missing for planned roles: {missing_alloc}")
    if not roles:
        blockers.append("no role to allocate budget to")

    if blockers:
        return _budget_doc(method, total, reserve, allocatable if allocatable > 0 else None, rows, blockers)

    if method == BUDGET_SHARES:
        share_sum = sum((allocs[r] for r in roles), MONEY.Decimal("0"))
        if share_sum != MONEY.Decimal("1"):
            blockers.append(f"role shares sum to {MONEY.decimal_to_canonical_string(share_sum)}, not exactly 1.00")
            return _budget_doc(method, total, reserve, allocatable, rows, blockers)
        rows = _largest_remainder(roles, allocs, allocatable)
    else:  # FIXED_AMOUNTS
        amount_sum = sum((allocs[r] for r in roles), MONEY.Decimal("0"))
        if amount_sum + reserve != total:
            blockers.append("role amounts + reserve do not equal total_daily_budget exactly")
            return _budget_doc(method, total, reserve, allocatable, rows, blockers)
        rows = [{"campaign_role": r, "daily_budget": MONEY.serialize_currency(allocs[r])} for r in roles]

    # minimum campaign budget check + exact reconciliation.
    rounded_sum = sum((MONEY.parse_positive_decimal(r["daily_budget"]) for r in rows), MONEY.Decimal("0"))
    for r in rows:
        amt = MONEY.parse_positive_decimal(r["daily_budget"])
        if amt < min_camp:
            blockers.append(f"role {r['campaign_role']} budget {r['daily_budget']} < minimum_campaign_budget")
    if method == BUDGET_SHARES and rounded_sum != MONEY.quantize_currency(allocatable):
        blockers.append("rounded role budgets do not sum exactly to allocatable")
    for r in rows:
        r.setdefault("meets_minimum", MONEY.parse_positive_decimal(r["daily_budget"]) >= min_camp)
    return _budget_doc(method, total, reserve, allocatable, rows, blockers)


def _largest_remainder(roles, shares, allocatable):
    """Deterministic largest-remainder allocation to cents. Rounded parts sum EXACTLY to allocatable."""
    hundred = MONEY.Decimal("100")
    target_cents = int((MONEY.quantize_currency(allocatable) * hundred).to_integral_value())
    raw = {}
    with MONEY.localcontext() as ctx:  # type: ignore[attr-defined]
        ctx.prec = 50
        for r in roles:
            raw[r] = (allocatable * shares[r]) * hundred   # value in cents (fractional)
    floor_cents = {r: int(raw[r].to_integral_value(rounding=ROUND_FLOOR)) for r in roles}
    assigned = sum(floor_cents.values())
    remainder = target_cents - assigned
    # distribute the leftover cents by largest fractional remainder, ties broken by role name.
    order = sorted(roles, key=lambda r: (-(raw[r] - floor_cents[r]), r))
    cents = dict(floor_cents)
    for i in range(max(0, remainder)):
        cents[order[i % len(order)]] += 1
    return [{"campaign_role": r, "daily_budget": MONEY.serialize_currency(cents[r] * MONEY.CENTS)}
            for r in roles]


def _budget_doc(method, total, reserve, allocatable, rows, blockers):
    return {
        "schema_version": SCHEMA_BUDGET_PLAN,
        "budget_allocation_method": method,
        "total_daily_budget": MONEY.serialize_currency(total) if total is not None else None,
        "reserve": MONEY.serialize_currency(reserve) if reserve is not None else None,
        "allocatable": MONEY.serialize_currency(allocatable) if allocatable is not None else None,
        "rows": sorted(rows, key=lambda r: r["campaign_role"]),
        "role_count": len(rows),
        "reconciles_exactly": not blockers,
        "no_fixed_percentages": True,
        "no_universal_25_dollar_budget": True,
        "blockers": sorted(blockers),
    }


# ================================================================ INPUT MANIFEST (hash-bound)
def _hash_or_missing(obj):
    if obj is None:
        return "MISSING"
    if isinstance(obj, dict) and (obj.get("IS_TEMPLATE_NOT_CONFIRMED_POLICY")
                                  or obj.get("IS_TEMPLATE_NOT_OWNER_CONFIRMED")
                                  or obj.get("IS_TEMPLATE_NOT_VERIFIED_STATE")
                                  or obj.get("IS_TEMPLATE_NOT_A_VERIFIED_CONTRACT")):
        return "TEMPLATE_NOT_CONFIRMED"
    return content_sha256(obj)


def build_input_manifest(foundation, owner_policy, keyword_source, keyword_allocation, product_facts,
                         claim_evidence, inclusions, exclusions, phase7_1m_proof_sha256):
    h = foundation.handoff
    inputs = {
        "phase7_1m_proof_sha256": _prefix(phase7_1m_proof_sha256) if phase7_1m_proof_sha256 else "MISSING",
        "live_product_state": _hash_or_missing(getattr(foundation, "_live_state_raw", None)),
        "ppc_product_contract": _hash_or_missing(getattr(foundation, "_contract_raw", None)),
        "ppc_economics": _hash_or_missing(getattr(foundation, "_economics_raw", None)),
        "phase6_keyword_source": _hash_or_missing(keyword_source),
        "phase6_keyword_allocation": _hash_or_missing(keyword_allocation),
        "product_facts": _hash_or_missing(product_facts),
        "claim_evidence": _hash_or_missing(claim_evidence),
        "owner_policy": _hash_or_missing(owner_policy),
        "owner_inclusions": _hash_or_missing(inclusions),
        "owner_exclusions": _hash_or_missing(exclusions),
    }
    # sanitize to prefixes only (never raw 64-hex leakage beyond a prefix; states stay literal).
    sanitized = {k: (v if v in ("MISSING", "TEMPLATE_NOT_CONFIRMED") else _prefix(v)) for k, v in inputs.items()}
    doc = {
        "schema_version": SCHEMA_INPUT_MANIFEST,
        "workspace_id": h["workspace_id"],
        "product_id": h["product_id"],
        "marketplace": h["marketplace"],
        "phase6_final_status": h["phase6_final_status"],
        "phase6_package_index_sha256_prefix": h["package_index_sha256_prefix"],
        "input_hash_prefixes": dict(sorted(sanitized.items())),
        "all_required_inputs_present": all(
            sanitized[k] not in ("MISSING", "TEMPLATE_NOT_CONFIRMED")
            for k in ("phase6_keyword_source", "phase6_keyword_allocation", "product_facts",
                      "claim_evidence", "owner_policy")),
    }
    return _finalize(doc)


# ================================================================ WORKSHEET + GUIDE
def build_manual_entry_worksheet_csv(handoff, role_plan, selected_targets, bid_plan, budget_plan,
                                     selected_negatives):
    """Deterministic, formula-injection-safe manual-entry worksheet CSV. Rows are for the OWNER to
    review and TYPE into Seller Central by hand. It is never uploaded and never owner-approved by
    default. UTF-8 with '\\n' newlines and stable ordering."""
    bid_by_key = {(r["canonical_term"], r["match_type"]): r for r in bid_plan.get("rows", [])}
    budget_by_role = {r["campaign_role"]: r for r in budget_plan.get("rows", [])}
    lines = [_csv_row([WORKSHEET_BANNER])]
    lines.append(_csv_row(["row_type", "planning_label", "campaign_role", "match_type", "display_term",
                           "starting_bid", "role_daily_budget", "evidence_grade", "relevance_tier",
                           "owner_reviewed", "owner_note"]))
    label_by_role = {r["campaign_role"]: r["planning_label"] for r in role_plan.get("roles", [])}
    for e in sorted(selected_targets, key=lambda x: (x["match_type"], x["canonical_term"])):
        bid = bid_by_key.get((e["canonical_term"], e["match_type"]), {})
        bud = budget_by_role.get(e["campaign_role"], {})
        lines.append(_csv_row([
            "TARGET", label_by_role.get(e["campaign_role"], ""), e["campaign_role"], e["match_type"],
            e["display_term"], bid.get("rounded_starting_bid", ""), bud.get("daily_budget", ""),
            e["evidence_grade"], e["relevance_tier"], "NO", ""]))
    for n in sorted(selected_negatives, key=lambda x: (x["negative_match_type"], x["canonical_term"])):
        lines.append(_csv_row([
            "NEGATIVE", "", "", n["negative_match_type"], n["display_term"], "", "", "", "", "NO",
            n.get("exclusion_reason") or ""]))
    return "\n".join(lines) + "\n"


def build_manual_entry_guide_md(handoff, readiness, selected_targets, selected_negatives, role_plan,
                                budget_plan):
    L = [f"# Phase 7.1E manual-entry guide — {readiness['phase7_1e_readiness']}", "",
         f"> {WORKSHEET_BANNER}", "",
         "This worksheet is a LOCAL PLANNING recommendation. The owner is the ONLY bridge to Amazon: "
         "review every row, then type the approved rows into Seller Central by hand. Nothing here is "
         "uploaded, and no row is owner-approved by default (owner_reviewed=NO).", "",
         "## Summary",
         f"- Marketplace: `{handoff['marketplace']}`  Product: `{handoff['product_id']}`",
         f"- Campaign-role plans (labels only): {role_plan['role_count']}",
         f"- Selected targets: {len(selected_targets)}",
         f"- Selected negatives: {len(selected_negatives)}", "",
         "## How to use",
         "1. Open each planning label as a NEW manual Sponsored Products campaign (you may rename it).",
         "2. Create the ad group and add the targets for that role at the listed starting bid.",
         "3. Set the role daily budget. These are recommendations — adjust to your judgement.",
         "4. Add any approved negatives. Mark owner_reviewed=YES as you go.", "",
         "## Permanent Amazon boundary",
         "- No login, SP-API/MWS/Ads API, browser automation, credential/cookie/token store, automated "
         "report retrieval, or public bind. The starting bids are bounded by the economic maximum CPC "
         "ceiling; they are recommendations, never automatic bids.", ""]
    return "\n".join(L)


def _owner_input_md(readiness, missing):
    L = [f"# {readiness['phase7_1e_readiness']}", "",
         "Phase 7.1E stayed **blocked**: the owner-controlled inputs required to plan campaigns are not "
         "all present and confirmed. No target, negative, bid, budget, or worksheet was generated.", "",
         "## What the owner must complete (never fabricated)"]
    for m in missing:
        L.append(f"- {m}")
    L += ["", "## The permanent Amazon boundary (re-asserted)",
          "- The owner is the ONLY manual bridge to Amazon. No login, SP-API/MWS/Ads API, browser "
          "automation, credential/cookie/token store, automated report retrieval, or public bind.",
          "- Enter every money/rate value as a canonical DECIMAL STRING (never a float or bool). A "
          "missing input stays missing — it is NEVER invented.", ""]
    return "\n".join(L)


# ================================================================ READINESS RESOLUTION
def resolve_phase7_1e_readiness(foundation, policy_result, eligible_target_count, selected_target_count,
                                plan_verification_result):
    """One most-restrictive resolver layering the owner policy, target evidence, and plan verification
    on top of the accepted 7.1M foundation. Phase 7.1E NEVER sets Amazon-action readiness."""
    comp = {}
    net = foundation.network
    lr, cr = foundation.live_result, foundation.contract_result
    er = foundation.economics_result
    cap, bud = foundation.capacity_doc, foundation.budget_doc

    # 1. hard integrity (preflight): boundary + Phase 6 package + hard contradictions.
    boundary_clean = net["no_active_amazon_account_path"] and net["non_loopback_bind_count"] == 0
    comp["amazon_boundary"] = READY_PLAN_FOR_OWNER_REVIEW if boundary_clean else READY_PREFLIGHT_BLOCKED
    comp["live_product_state"] = {
        F.LIVE_STATE_VERIFIED: READY_PLAN_FOR_OWNER_REVIEW,
        F.LIVE_STATE_OWNER_INPUT: READY_OWNER_INPUT,
        F.LIVE_STATE_CONTRADICTORY: READY_PREFLIGHT_BLOCKED,
        F.LIVE_STATE_INVALID: READY_PREFLIGHT_BLOCKED,
    }[lr.state]
    comp["ppc_contract"] = {
        F.CONTRACT_VERIFIED: READY_PLAN_FOR_OWNER_REVIEW,
        F.CONTRACT_OWNER_INPUT: READY_OWNER_INPUT,
        F.CONTRACT_LIVE_STATE_BLOCKED: READY_OWNER_INPUT,
        F.CONTRACT_PHASE6_BLOCKED: READY_OWNER_INPUT,
        F.CONTRACT_CONTRADICTORY: READY_PREFLIGHT_BLOCKED,
        F.CONTRACT_INVALID: READY_PREFLIGHT_BLOCKED,
    }[cr.state]

    # 2. economics / capacity / handling / budget (distinct hard blocks).
    comp["economics"] = {
        F.ECON_VIABLE: READY_PLAN_FOR_OWNER_REVIEW,
        F.ECON_OWNER_INPUT: READY_OWNER_INPUT,
        F.ECON_NOT_VIABLE: READY_ECONOMICS_BLOCKED,
        F.ECON_INVALID: READY_ECONOMICS_BLOCKED,
    }[er.state]
    comp["capacity"] = {
        F.CAPACITY_SAFE: READY_PLAN_FOR_OWNER_REVIEW,
        F.CAPACITY_OWNER_INPUT: READY_OWNER_INPUT,
        F.CAPACITY_BLOCKED: READY_CAPACITY_BLOCKED,
    }[cap["capacity_state"]]
    comp["handling"] = {
        F.HANDLING_SAFE: READY_PLAN_FOR_OWNER_REVIEW,
        F.HANDLING_OWNER_INPUT: READY_OWNER_INPUT,
        F.HANDLING_BLOCKED: READY_HANDLING_BLOCKED,
    }[cap["handling_state"]]
    comp["budget_feasibility"] = {
        F.BUDGET_PASSES: READY_PLAN_FOR_OWNER_REVIEW,
        F.BUDGET_OWNER_REVIEW: READY_PLAN_FOR_OWNER_REVIEW,
        F.BUDGET_OWNER_INPUT: READY_OWNER_INPUT,
        F.BUDGET_BLOCKED: READY_BUDGET_BLOCKED,
    }[bud["budget_feasibility_result"]]

    # 3. owner policy.
    comp["owner_policy"] = {
        "OWNER_POLICY_CONFIRMED": READY_PLAN_FOR_OWNER_REVIEW,
        "OWNER_POLICY_REQUIRED": READY_OWNER_POLICY,
        "OWNER_POLICY_INVALID": READY_OWNER_POLICY,
    }[policy_result.state]

    # 4. target evidence — only meaningful once the foundation + policy are otherwise green.
    upstream_ok = all(_RANK[v] == _RANK[READY_PLAN_FOR_OWNER_REVIEW] for v in comp.values())
    if upstream_ok:
        comp["target_evidence"] = (READY_PLAN_FOR_OWNER_REVIEW if eligible_target_count > 0
                                   else READY_TARGET_EVIDENCE_BLOCKED)
    else:
        comp["target_evidence"] = READY_PLAN_FOR_OWNER_REVIEW

    # 5. plan verification — only when a plan was actually built.
    if plan_verification_result is None:
        comp["plan_verification"] = READY_PLAN_FOR_OWNER_REVIEW
    else:
        comp["plan_verification"] = (READY_PLAN_FOR_OWNER_REVIEW if plan_verification_result == "PASS"
                                     else READY_PLAN_VERIFICATION_BLOCKED)

    base_rank = min(_RANK[v] for v in comp.values())
    decision = _RANK_TO_STATE[base_rank]
    plan_ready = decision == READY_PLAN_FOR_OWNER_REVIEW

    doc = {
        "schema_version": SCHEMA_READINESS,
        "phase7_1e_readiness": decision,
        "component_states": dict(sorted(comp.items())),
        "most_restrictive_component": min(comp, key=lambda k: _RANK[comp[k]]),
        "eligible_target_count": eligible_target_count,
        "selected_target_count": selected_target_count,
        "ready_for_campaign_planning": plan_ready,
        "ready_for_owner_plan_review": plan_ready,
        "ready_for_manual_launch": False,
        "ready_for_amazon_action": False,
        # Phase 7.1E performs zero Amazon actions:
        "amazon_account_attempts": 0,
        "amazon_account_actions": 0,
        "campaign_write_actions": 0,
        "target_write_actions": 0,
        "negative_write_actions": 0,
        "bid_write_actions": 0,
        "budget_write_actions": 0,
        "external_network_attempts": 0,
        "browser_automation_attempts": 0,
        "api_payload_count": 0,
    }
    return _finalize(doc)


# ================================================================ TOP-LEVEL ASSEMBLY
class Phase71EResult:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _load_phase7_1m_proof_sha(repo_root):
    path = os.path.join(repo_root, "SESSION7_1M-PROOF-GATE.json")
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return _sha_bytes(f.read())


def assemble_phase7_1e(repo_root, run_dir=None, mode="LOCAL_SAFE", live_state=None, contract=None,
                       economics=None, owner_policy=None, keyword_source=None, keyword_allocation=None,
                       product_facts=None, claim_evidence=None, inclusions=None, exclusions=None,
                       foundation=None):
    """Assemble every Phase 7.1E planning artifact IN MEMORY. Deterministic + network-free. Reuses the
    whole 7.1M foundation. When any owner-controlled input is missing/unconfirmed, the readiness
    truthfully resolves to a blocked state and only blocked-safe artifacts are emitted downstream.

    ``foundation`` is an optional, already-built 7.1M Phase71MResult (a composition seam used by tests
    to exercise the fully-green ready path against a synthetic publishable handoff, exactly as the 7.1M
    suite does). It never relaxes any gate — the injected foundation itself enforces every check. By
    default the foundation is built from the repository via the real Phase 6 handoff."""
    if mode not in CONNECTIVITY_MODES:
        raise Phase71EError(f"unknown connectivity mode: {mode}")

    if foundation is None:
        foundation = F.assemble_phase7_1m(repo_root, run_dir=run_dir, mode=mode, live_state=live_state,
                                          contract=contract, economics=economics)
    # remember the raw owner inputs for the input manifest (never re-hashed from disk).
    foundation._live_state_raw = live_state
    foundation._contract_raw = contract
    foundation._economics_raw = economics

    policy_result = validate_owner_policy(owner_policy)

    candidates = build_target_candidates(keyword_allocation, keyword_source, product_facts,
                                         claim_evidence, policy_result)
    eligible = [c for c in candidates if c.eligible]

    selected, deferred, negatives, selected_negs = [], [], [], []
    role_plan = {"schema_version": SCHEMA_ROLE_PLAN, "roles": [], "role_count": 0,
                 "no_amazon_ids": True, "labels_are_local_planning_only": True}
    bid_plan = {"schema_version": SCHEMA_BID_PLAN, "rows": [], "row_count": 0, "blocked_row_count": 0,
                "bid_method": policy_result.resolved.get("bid_method"), "blockers": []}
    budget_plan = _budget_doc(policy_result.resolved.get("budget_method"), None, None, None, [], [])

    # a plan is built ONLY when the foundation is viable + the policy is confirmed + there is a target.
    foundation_green = _foundation_green(foundation)
    plan_verification = None
    if foundation_green and policy_result.confirmed and eligible:
        selected, deferred = select_targets(candidates, policy_result)
        negatives = build_negative_candidates(policy_result, selected)
        selected_negs = select_negatives(negatives)
        role_plan = build_campaign_role_plan(foundation.handoff, policy_result, selected)
        bid_plan = build_bid_plan(foundation.economics_result, policy_result, selected)
        budget_plan = build_budget_plan(policy_result, role_plan)
        plan_verification = _plan_self_check(selected, bid_plan, budget_plan, foundation.economics_result,
                                             policy_result)

    readiness = resolve_phase7_1e_readiness(foundation, policy_result, len(eligible), len(selected),
                                            plan_verification)
    phase7_1m_proof_sha = _load_phase7_1m_proof_sha(repo_root)
    input_manifest = build_input_manifest(foundation, owner_policy, keyword_source, keyword_allocation,
                                          product_facts, claim_evidence, inclusions, exclusions,
                                          phase7_1m_proof_sha)

    decision = readiness["phase7_1e_readiness"]
    session_status = decision if decision in _RANK else SESSION_BLOCKED

    return Phase71EResult(
        repo_root=repo_root, run_dir=run_dir, mode=mode, foundation=foundation,
        policy_result=policy_result, candidates=candidates, eligible=eligible, selected=selected,
        deferred=deferred, negatives=negatives, selected_negatives=selected_negs, role_plan=role_plan,
        bid_plan=bid_plan, budget_plan=budget_plan, input_manifest=input_manifest, readiness=readiness,
        plan_verification=plan_verification, readiness_decision=decision, session_status=session_status,
        plan_ready=(decision == READY_PLAN_FOR_OWNER_REVIEW))


def _foundation_green(foundation):
    return (foundation.live_result.verified and foundation.contract_result.verified
            and foundation.economics_result.viable
            and foundation.capacity_doc["capacity_state"] == F.CAPACITY_SAFE
            and foundation.capacity_doc["handling_state"] == F.HANDLING_SAFE
            and foundation.budget_doc["budget_feasibility_result"] in (F.BUDGET_PASSES, F.BUDGET_OWNER_REVIEW))


def _plan_self_check(selected, bid_plan, budget_plan, economics_result, policy_result):
    """Internal plan validation. Returns 'PASS' or 'BLOCKED'. Verifies every selected target has a valid
    within-ceiling bid, the budget reconciles, and no counts violate limits."""
    if not selected:
        return "BLOCKED"
    if bid_plan.get("blocked_row_count", 0) != 0 or bid_plan.get("blockers"):
        return "BLOCKED"
    if budget_plan.get("blockers") or not budget_plan.get("reconciles_exactly"):
        return "BLOCKED"
    max_cpc = economics_result.derived.get("maximum_cpc")
    for r in bid_plan.get("rows", []):
        if r.get("within_ceiling") is not True or r.get("status") != "BID_OK":
            return "BLOCKED"
        if max_cpc is not None and MONEY.parse_positive_decimal(r["rounded_starting_bid"]) > max_cpc:
            return "BLOCKED"
    return "PASS"


# ================================================================ STABLE DOCS + WRITE / VERIFY / PROMOTE
def _selected_targets_doc(result):
    return {"schema_version": SCHEMA_SELECTED_TARGETS,
            "selected": sorted(result.selected, key=lambda e: (e["match_type"], e["canonical_term"])),
            "selected_count": len(result.selected),
            "deferred_limit": sorted(result.deferred, key=lambda e: (e["match_type"], e["canonical_term"])),
            "deferred_count": len(result.deferred),
            "never_converts_deferred_to_negatives": True}


def _target_candidates_doc(result):
    return {"schema_version": SCHEMA_TARGET_CANDIDATES,
            "candidates": [c.to_dict() for c in result.candidates],
            "candidate_count": len(result.candidates),
            "eligible_count": len(result.eligible)}


def _negative_docs(result):
    cand = {"schema_version": SCHEMA_NEGATIVE_CANDIDATES, "candidates": result.negatives,
            "candidate_count": len(result.negatives), "negatives_never_auto_derived": True}
    sel = {"schema_version": SCHEMA_SELECTED_NEGATIVES, "selected": result.selected_negatives,
           "selected_count": len(result.selected_negatives)}
    return cand, sel


def _ready_docs(result):
    """The full stable JSON/text artifact map when the plan is ready for owner review."""
    neg_cand, neg_sel = _negative_docs(result)
    worksheet = build_manual_entry_worksheet_csv(result.foundation.handoff, result.role_plan,
                                                 result.selected, result.bid_plan, result.budget_plan,
                                                 result.selected_negatives)
    guide = build_manual_entry_guide_md(result.foundation.handoff, result.readiness, result.selected,
                                        result.selected_negatives, result.role_plan, result.budget_plan)
    return {
        F_OWNER_POLICY_VALIDATION: result.policy_result.to_dict(),
        F_INPUT_MANIFEST: result.input_manifest,
        F_TARGET_CANDIDATES: _target_candidates_doc(result),
        F_SELECTED_TARGETS: _selected_targets_doc(result),
        F_NEGATIVE_CANDIDATES: neg_cand,
        F_SELECTED_NEGATIVES: neg_sel,
        F_ROLE_PLAN: result.role_plan,
        F_BID_PLAN: result.bid_plan,
        F_BUDGET_PLAN: result.budget_plan,
        F_READINESS: result.readiness,
    }, {F_WORKSHEET: worksheet, F_ENTRY_GUIDE: guide}


def _blocked_missing_list(result):
    missing = []
    f = result.foundation
    if not f.live_result.verified:
        missing.append(f"Live product state: `{f.live_result.state}`")
    if not f.contract_result.verified:
        missing.append(f"PPC product contract: `{f.contract_result.state}`")
    if not f.economics_result.viable:
        missing.append(f"PPC economics: `{f.economics_result.state}`")
    missing.append(f"Capacity: `{f.capacity_doc['capacity_state']}`  "
                   f"Handling: `{f.capacity_doc['handling_state']}`")
    missing.append(f"Budget feasibility: `{f.budget_doc['budget_feasibility_result']}`")
    if not result.policy_result.confirmed:
        missing.append(f"Owner policy: `{result.policy_result.state}`")
    if result.readiness_decision == READY_TARGET_EVIDENCE_BLOCKED:
        missing.append("No target candidate meets the owner evidence threshold + allocation gate.")
    return missing


def write_phase7_1e_candidate(result, candidate_dir, started_at=None, completed_at=None):
    """Write the correct artifact set (ready OR blocked-safe) atomically into a bounded CANDIDATE dir.
    A failed write never overwrites a last-valid file. Returns (manifest, output_hashes)."""
    os.makedirs(candidate_dir, exist_ok=True)
    output_hashes = {}

    def _emit(name, data_bytes):
        _atomic_write_bytes(os.path.join(candidate_dir, name), data_bytes)
        output_hashes[name] = _sha_bytes(data_bytes)

    # owner-policy template is always available for the owner to complete.
    _emit(F_OWNER_POLICY_TEMPLATE, canonical_json(build_owner_policy_template()).encode("utf-8"))

    if result.plan_ready:
        json_docs, text_docs = _ready_docs(result)
        for name, doc in json_docs.items():
            _emit(name, canonical_json(doc).encode("utf-8"))
        for name, text in text_docs.items():
            _emit(name, text.encode("utf-8"))
    else:
        _emit(F_OWNER_INPUT, _owner_input_md(result.readiness, _blocked_missing_list(result)).encode("utf-8"))
        _emit(F_READINESS, canonical_json(result.readiness).encode("utf-8"))

    verification = build_verification_report(result, output_hashes)
    _emit(F_VERIFICATION, canonical_json(verification).encode("utf-8"))

    manifest = build_stage_manifest(result, output_hashes, started_at=started_at, completed_at=completed_at)
    _atomic_write_bytes(os.path.join(candidate_dir, F_STAGE_MANIFEST),
                        canonical_json(manifest).encode("utf-8"))
    return manifest, output_hashes


def _required_for(result):
    return READY_ARTIFACTS if result.plan_ready else BLOCKED_ARTIFACTS


def verify_phase7_1e_candidate(candidate_dir, output_hashes, required=()):
    """Reopen every candidate file and verify its bytes against the recorded hash. Also checks the
    required artifact set is present. Returns a report dict with result PASS / BLOCKED."""
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
    required_missing = [n for n in required if not os.path.exists(os.path.join(candidate_dir, n))]
    ok = not mismatched and not missing and not required_missing
    return {"result": "PASS" if ok else "BLOCKED", "mismatched": sorted(mismatched),
            "missing": sorted(missing), "required_missing": sorted(required_missing),
            "candidate_file_count": len(output_hashes)}


def promote_phase7_1e_candidate(candidate_dir, final_dir, last_valid_dir, output_hashes, required=()):
    """Atomically promote a VERIFIED candidate to *final_dir*, preserving the prior final bytes into
    *last_valid_dir* first. Refuses to promote on ANY defect, so the last valid final bytes are
    preserved exactly (tamper-safe)."""
    report = verify_phase7_1e_candidate(candidate_dir, output_hashes, required=required)
    if report["result"] != "PASS":
        report["promoted"] = False
        return report
    # 1. preserve the current final into last_valid (snapshot the previous good set).
    if os.path.isdir(final_dir) and os.listdir(final_dir):
        os.makedirs(last_valid_dir, exist_ok=True)
        for name in os.listdir(final_dir):
            src = os.path.join(final_dir, name)
            if os.path.isfile(src):
                with open(src, "rb") as f:
                    _atomic_write_bytes(os.path.join(last_valid_dir, name), f.read())
    # 2. move the candidate into final (manifest last).
    os.makedirs(final_dir, exist_ok=True)
    ordered = [n for n in output_hashes if n != F_STAGE_MANIFEST] + [F_STAGE_MANIFEST]
    for name in ordered:
        src = os.path.join(candidate_dir, name)
        if os.path.exists(src):
            os.replace(src, os.path.join(final_dir, name))
    report["promoted"] = True
    report["final_dir_present"] = True
    return report


def assemble_and_write_phase7_1e(result, run_dir, started_at=None, completed_at=None):
    """Convenience: write to candidate/, verify, and promote to final/ (preserving last_valid/)."""
    base = os.path.join(run_dir, "phase7", "7.1E")
    candidate = os.path.join(base, "candidate")
    final = os.path.join(base, "final")
    last_valid = os.path.join(base, "last_valid")
    manifest, hashes = write_phase7_1e_candidate(result, candidate, started_at=started_at,
                                                 completed_at=completed_at)
    report = promote_phase7_1e_candidate(candidate, final, last_valid, hashes, required=_required_for(result))
    return manifest, report, base


# ================================================================ VERIFICATION REPORT
def build_verification_report(result, output_hashes):
    r = result
    doc = {
        "schema_version": SCHEMA_VERIFICATION,
        "stage_id": STAGE_ID,
        "readiness_status": r.readiness_decision,
        "session_status": r.session_status,
        "plan_ready": r.plan_ready,
        "required_artifacts": list(_required_for(r)),
        "required_artifact_count": len(_required_for(r)),
        "generated_output_count": len(output_hashes),
        "plan_self_check": r.plan_verification,
        "eligible_target_count": len(r.eligible),
        "selected_target_count": len(r.selected),
        "selected_negative_count": len(r.selected_negatives),
        "campaign_role_count": r.role_plan["role_count"],
        "bid_row_count": r.bid_plan.get("row_count", 0),
        "bid_blocked_row_count": r.bid_plan.get("blocked_row_count", 0),
        "budget_reconciles_exactly": r.budget_plan.get("reconciles_exactly", False),
        "amazon_boundary": {
            "no_active_amazon_account_path": r.foundation.network["no_active_amazon_account_path"],
            "prohibited_account_path_count": r.foundation.network["prohibited_account_path_count"],
            "dashboard_bind_is_loopback": r.foundation.network["dashboard_bind_is_loopback"],
        },
        "this_session_never": {
            "logs_into_amazon": True, "uses_sp_api_mws_ads_api": True, "browser_automates": True,
            "creates_campaigns": True, "writes_targets": True, "writes_negatives": True,
            "writes_bids": True, "writes_budgets": True, "uploads_to_amazon": True,
            "emits_api_payload": True, "binds_public_server": True,
        },
        # required zero counters.
        "amazon_account_attempts": 0,
        "amazon_account_actions": 0,
        "campaign_write_actions": 0,
        "target_write_actions": 0,
        "negative_write_actions": 0,
        "bid_write_actions": 0,
        "budget_write_actions": 0,
        "external_network_attempts": 0,
        "browser_automation_attempts": 0,
        "api_payload_count": 0,
    }
    return _finalize(doc)


# ================================================================ STAGE MANIFEST
def build_stage_manifest(result, output_hashes, started_at=None, completed_at=None):
    r = result
    h = r.foundation.handoff
    body = {
        "schema_version": SCHEMA_STAGE_MANIFEST,
        "stage_id": STAGE_ID,
        "stage_name": STAGE_NAME,
        "workspace_id": h["workspace_id"],
        "product_id": h["product_id"],
        "marketplace": h["marketplace"],
        "phase7_1m_readiness": r.foundation.readiness_decision,
        "phase6_final_status": h["phase6_final_status"],
        "phase6_package_index_sha256_prefix": h["package_index_sha256_prefix"],
        "owner_policy_status": r.policy_result.state,
        "readiness_status": r.readiness_decision,
        "session_status": r.session_status,
        "plan_ready": r.plan_ready,
        "eligible_target_count": len(r.eligible),
        "selected_target_count": len(r.selected),
        "selected_negative_count": len(r.selected_negatives),
        "campaign_role_count": r.role_plan["role_count"],
        "bid_row_count": r.bid_plan.get("row_count", 0),
        "budget_role_count": r.budget_plan.get("role_count", 0),
        "amazon_account_attempts": 0,
        "amazon_account_actions": 0,
        "campaign_write_actions": 0,
        "target_write_actions": 0,
        "negative_write_actions": 0,
        "bid_write_actions": 0,
        "budget_write_actions": 0,
        "external_network_attempts": 0,
        "browser_automation_attempts": 0,
        "api_payload_count": 0,
        "generated_outputs": sorted(output_hashes),
        "output_hashes": dict(sorted(output_hashes.items())),
        "deterministic_content_sha256": None,
    }
    body["deterministic_content_sha256"] = content_sha256(
        {k: v for k, v in body.items() if k not in _VOLATILE})
    if started_at:
        body["started_at"] = started_at
    if completed_at:
        body["completed_at"] = completed_at
    return body


# ================================================================ COMMITTED PROOF (sanitized)
def _match_type_counts(entries, key):
    out = {}
    for e in entries:
        out[e[key]] = out.get(e[key], 0) + 1
    return dict(sorted(out.items()))


def build_proof_gate(result, started_commit=None, final_commit=None, baseline_tests=None,
                     targeted_tests=None, phase7_1m_proof_sha256=None, clean_worktree=None):
    r = result
    h = r.foundation.handoff
    er = r.foundation.economics_result
    doc = {
        "schema_version": SCHEMA_PROOF_GATE,
        "session": "PHASE 7.1E — OWNER-GATED OFFLINE MANUAL SPONSORED PRODUCTS PLANNING ENGINE",
        "starting_commit": started_commit,
        "final_commit": final_commit,
        "branch": "main",
        "accepted_phase7_1m_tag": "phase7-1m-accepted-f7b6253",
        "accepted_phase7_1m_commit": "f7b6253",
        "phase7_1e_authority": "production/phase7_extended_launch_planning.py",
        "phase7_1m_authority": "production/phase7_minimal_launch_foundation.py",
        "money_authority": "core/money.py",
        "owner_policy_schema": SCHEMA_OWNER_POLICY,
        "duplicate_production_authority_count": 0,
        "connectivity_modes": list(CONNECTIVITY_MODES),
        "phase7_1m_proof_sha256_prefix": _prefix(phase7_1m_proof_sha256) if phase7_1m_proof_sha256 else None,
        # --- Phase 6 handoff (sanitized) ---
        "phase6_final_status": h["phase6_final_status"],
        "phase6_package_index_sha256_prefix": h["package_index_sha256_prefix"],
        "keyword_source_sha256_prefix": h["keyword_source_sha256_prefix"],
        "product_facts_sha256_prefix": h["product_facts_sha256_prefix"],
        "claim_evidence_sha256_prefix": h["claim_evidence_sha256_prefix"],
        "product_id": h["product_id"],
        "canonical_category_id": h["canonical_category_id"],
        "marketplace": h["marketplace"],
        "listing_publishability": h["listing_publishability"],
        # --- foundation dependency states ---
        "phase7_1m_readiness": r.foundation.readiness_decision,
        "live_product_state_result": r.foundation.live_result.state,
        "ppc_product_contract_result": r.foundation.contract_result.state,
        "economics_result": er.state,
        "capacity_result": r.foundation.capacity_doc["capacity_state"],
        "handling_result": r.foundation.capacity_doc["handling_state"],
        "budget_feasibility_result": r.foundation.budget_doc["budget_feasibility_result"],
        "maximum_cpc_state": "BLOCKED_OR_MISSING" if not er.viable else "COMPUTED",
        # --- owner policy + planning states ---
        "owner_policy_result": r.policy_result.state,
        "starting_bid_method": r.policy_result.resolved.get("bid_method"),
        "budget_allocation_method": r.policy_result.resolved.get("budget_method"),
        "minimum_evidence_grade": r.policy_result.resolved.get("min_grade"),
        "negatives_enabled": bool(r.policy_result.resolved.get("negatives_enabled")),
        # --- planning counts ---
        "candidate_target_count": len(r.candidates),
        "eligible_target_count": len(r.eligible),
        "selected_target_count": len(r.selected),
        "selected_targets_by_match_type": _match_type_counts(r.selected, "match_type"),
        "deferred_limit_count": len(r.deferred),
        "negative_candidate_count": len(r.negatives),
        "selected_negative_count": len(r.selected_negatives),
        "selected_negatives_by_match_type": _match_type_counts(r.selected_negatives, "negative_match_type"),
        "campaign_role_count": r.role_plan["role_count"],
        "bid_row_count": r.bid_plan.get("row_count", 0),
        "bid_blocked_row_count": r.bid_plan.get("blocked_row_count", 0),
        "budget_role_count": r.budget_plan.get("role_count", 0),
        "budget_reconciles_exactly": r.budget_plan.get("reconciles_exactly", False),
        "worksheet_generated": r.plan_ready,
        "plan_self_check": r.plan_verification,
        # --- readiness booleans ---
        "readiness_result": r.readiness_decision,
        "session_status": r.session_status,
        "ready_for_campaign_planning": r.readiness["ready_for_campaign_planning"],
        "ready_for_owner_plan_review": r.readiness["ready_for_owner_plan_review"],
        "ready_for_manual_launch": False,
        "ready_for_amazon_action": False,
        # --- required zero counters ---
        "amazon_account_attempts": 0,
        "amazon_account_actions": 0,
        "campaign_write_actions": 0,
        "target_write_actions": 0,
        "negative_write_actions": 0,
        "bid_write_actions": 0,
        "budget_write_actions": 0,
        "external_network_attempts": 0,
        "browser_automation_attempts": 0,
        "api_payload_count": 0,
        "network_no_active_amazon_account_path": r.foundation.network["no_active_amazon_account_path"],
        "dashboard_bind_is_loopback": r.foundation.network["dashboard_bind_is_loopback"],
        # --- determinism / promotion ---
        "clean_worktree_result": clean_worktree,
        "required_ready_artifacts": list(READY_ARTIFACTS),
        "required_blocked_artifacts": list(BLOCKED_ARTIFACTS),
        "baseline_tests": baseline_tests,
        "targeted_tests": targeted_tests,
        "sanitization_note": "States, prefixes, counts, and reason codes only. No absolute paths, no raw "
                             "owner values, no ASIN/SKU, no live price/capacity/bid/budget, no paid "
                             "keyword rows, no credentials. Local run artifacts stay under runs/ (gitignored).",
        "known_limitations": [
            "Phase 6 remains a verified SAFE DRAFT and no owner live-state / economics / contract / "
            "policy is confirmed for T2, so Phase 7.1E truthfully resolves to owner-input / owner-policy "
            "required and emits only blocked-safe artifacts.",
            "maximum_cpc is an economic CEILING, never an automatic bid; every starting bid is bounded by "
            "it and derived only from an explicit owner bid method.",
            "Negatives are disabled by default and are never auto-derived from unselected / low-priority "
            "/ low-volume / absent terms.",
            "Phase 7.1E is planning only: it never logs into Amazon, creates a campaign, or emits an API "
            "payload; the owner types every approved row into Seller Central by hand.",
        ],
    }
    doc["deterministic_content_sha256"] = content_sha256(
        {k: v for k, v in doc.items()
         if k not in ("deterministic_content_sha256", "starting_commit", "final_commit",
                      "baseline_tests", "targeted_tests", "clean_worktree_result")})
    return doc


# ================================================================ CLI
def main(argv=None):
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Phase 7.1E owner-gated manual campaign planning (offline)")
    ap.add_argument("--repo-root", default=_ROOT)
    ap.add_argument("--run-dir", default=None, help="local product workspace (e.g. runs/T2)")
    ap.add_argument("--mode", default="LOCAL_SAFE", choices=list(CONNECTIVITY_MODES))
    ap.add_argument("--live-state", default=None)
    ap.add_argument("--contract", default=None)
    ap.add_argument("--economics", default=None)
    ap.add_argument("--owner-policy", default=None)
    ap.add_argument("--keyword-source", default=None)
    ap.add_argument("--keyword-allocation", default=None)
    ap.add_argument("--product-facts", default=None)
    ap.add_argument("--claim-evidence", default=None)
    ap.add_argument("--proof-out", default=None)
    ap.add_argument("--starting-commit", default=None)
    a = ap.parse_args(argv)

    def _load(p):
        if p and os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        return None

    result = assemble_phase7_1e(
        a.repo_root, run_dir=a.run_dir, mode=a.mode, live_state=_load(a.live_state),
        contract=_load(a.contract), economics=_load(a.economics), owner_policy=_load(a.owner_policy),
        keyword_source=_load(a.keyword_source), keyword_allocation=_load(a.keyword_allocation),
        product_facts=_load(a.product_facts), claim_evidence=_load(a.claim_evidence))
    if a.run_dir:
        manifest, report, base = assemble_and_write_phase7_1e(result, a.run_dir)
        print(f"artifacts={base} promote={report['result']}")
    if a.proof_out:
        proof = build_proof_gate(result, started_commit=a.starting_commit,
                                 phase7_1m_proof_sha256=_load_phase7_1m_proof_sha(a.repo_root))
        _atomic_write_bytes(a.proof_out, canonical_json(proof).encode("utf-8"))
    print(f"readiness={result.readiness_decision} session_status={result.session_status} "
          f"policy={result.policy_result.state} eligible={len(result.eligible)} "
          f"selected={len(result.selected)} plan_ready={result.plan_ready}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
