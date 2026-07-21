#!/usr/bin/env python3
"""Phase 7.5 — offline Owner Decision Package generator over accepted Phase 7.3 + Phase 7.4 state.

This module is the ONE Phase 7.5 authority. It is a PREPARATION and DOCUMENTATION layer only.

It connects to NOTHING. There is no Amazon integration, no SP-API, no Ads API, no browser
automation, and no network client of any kind. It never creates, pauses, or changes a campaign,
bid, budget, keyword, negative, or target. It never uploads anything, never emits an Amazon bulk
upload template or API payload, and never claims that any action has been performed. The owner
remains the only person who manually performs any action in Amazon Seller Central.

What it does:
  * reuses the Phase 7.4 authority (``phase7_owner_dashboard``) to load and re-verify the promoted
    Phase 7.3 analytical output (promoted/ precedes stale final/) and to reconcile the local Phase
    7.4 review state against the current source;
  * selects ONLY records the owner explicitly marked APPROVED_FOR_MANUAL_ACTION whose evidence is
    current, unambiguous, and reproducible;
  * writes a deterministic, human-readable decision package (Markdown / JSON / TSV) plus an explicit
    exclusions report so no reviewed record is ever silently dropped;
  * is strictly read-only toward Phase 7.3 and Phase 7.4 — it writes only under its own Phase 7.5
    workspace, atomically, with a content-addressed / idempotent package identity.

Financial rules: Decimal only (never float) for any authoritative value. Values that arrive from
Phase 7.3 as canonical strings are preserved verbatim and never re-computed; missing values stay
missing, never silently zero. Currencies and attribution windows are never aggregated.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ("", "core", "production"):
    _p = _ROOT if not _sub else os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import phase7_owner_dashboard as DASH     # noqa: E402  Phase 7.4 authority (source + review)
from production import phase7_ads_analysis as AA          # noqa: E402  Phase 7.3 authority (labels/constants)
from production import product_workspace as PW            # noqa: E402  canonical json / content hash
from core import money as MONEY                            # noqa: E402  Decimal / integer authority

canonical_json = PW.canonical_json
content_sha256 = PW.content_sha256

# ================================================================ constants / versions
STAGE_ID = "7.5"
STAGE_NAME = "Offline Owner Decision Package"
PACKAGE_SCHEMA = "phase7-5-decision-package-v1"
CHECKLIST_SCHEMA = "phase7-5-manual-action-checklist-v1"
EXCLUSIONS_SCHEMA = "phase7-5-excluded-items-v1"
LINEAGE_SCHEMA = "phase7-5-source-lineage-v1"
MANIFEST_SCHEMA = "phase7-5-package-manifest-v1"
GENERATOR_VERSION = "phase7-5-owner-decision-package-v1"

# ---------------------------------------------------------------- readiness states
PKG_READY = "SESSION7_5_PACKAGE_READY"
PKG_READY_EMPTY = "SESSION7_5_PACKAGE_READY_EMPTY"
SOURCE_REQUIRED = "SESSION7_5_SOURCE_REQUIRED"
SOURCE_BLOCKED = "SESSION7_5_SOURCE_BLOCKED"
REVIEW_STATE_REQUIRED = "SESSION7_5_REVIEW_STATE_REQUIRED"
REVIEW_STATE_BLOCKED = "SESSION7_5_REVIEW_STATE_BLOCKED"
SOURCE_CHANGED_REVIEW_REQUIRED = "SESSION7_5_SOURCE_CHANGED_REVIEW_REQUIRED"
POLICY_REQUIRED = "SESSION7_5_POLICY_REQUIRED"
CONFLICT_REVIEW_REQUIRED = "SESSION7_5_CONFLICT_REVIEW_REQUIRED"
PACKAGE_BLOCKED = "SESSION7_5_PACKAGE_BLOCKED"

# readiness states that mean "no package produced / bad or missing input" -> nonzero exit.
_BLOCKING_READINESS = (SOURCE_REQUIRED, SOURCE_BLOCKED, REVIEW_STATE_REQUIRED,
                       REVIEW_STATE_BLOCKED, PACKAGE_BLOCKED)

# ---------------------------------------------------------------- exclusion reason codes
R_NOT_APPROVED = "NOT_APPROVED"
R_SOURCE_CHANGED = "SOURCE_CHANGED"
R_ENTITY_ABSENT = "ENTITY_ABSENT"
R_HASH_MISMATCH = "HASH_MISMATCH"
R_SOURCE_MANIFEST_MISMATCH = "SOURCE_MANIFEST_MISMATCH"
R_BLOCKED_RECOMMENDATION = "BLOCKED_RECOMMENDATION"
R_MISSING_POLICY = "MISSING_POLICY"
R_MISSING_EVIDENCE = "MISSING_EVIDENCE"
R_AMBIGUOUS_CURRENCY = "AMBIGUOUS_CURRENCY"
R_AMBIGUOUS_ATTRIBUTION_WINDOW = "AMBIGUOUS_ATTRIBUTION_WINDOW"
R_DUPLICATE_IDENTICAL = "DUPLICATE_IDENTICAL"
R_DUPLICATE_CONFLICT = "DUPLICATE_CONFLICT"
R_INVALID_REVIEW_STATE = "INVALID_REVIEW_STATE"
R_UNKNOWN_ENTITY = "UNKNOWN_ENTITY"
R_UNSUPPORTED_RECOMMENDATION_TYPE = "UNSUPPORTED_RECOMMENDATION_TYPE"
R_MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"

# ---------------------------------------------------------------- recommendation types (Phase 7.3 labels)
RT_NEGATIVE = AA.RL_NEGATIVE          # REVIEW_FOR_MANUAL_NEGATIVE
RT_EXACT = AA.RL_EXACT                # REVIEW_FOR_MANUAL_EXACT_KEYWORD
RT_BID_BUDGET = AA.RL_BID_BUDGET      # REVIEW_BID_OR_BUDGET_CONTEXT
SUPPORTED_RECOMMENDATION_TYPES = (RT_NEGATIVE, RT_EXACT, RT_BID_BUDGET)

# Neutral, review-only action descriptions. These are OWNER REVIEW INSTRUCTIONS — never commands
# executed by software, never Amazon syntax, never an automatic mutation URL.
ACTION_DESCRIPTIONS = {
    RT_NEGATIVE: ("Suggested manual review: consider whether this customer search term should be "
                  "added as a negative keyword. Owner-approved for manual verification only."),
    RT_EXACT: ("Suggested manual review: consider whether this converting customer search term "
               "should be added as a manual exact-match keyword. Owner-approved for manual "
               "verification only."),
    RT_BID_BUDGET: ("Suggested manual review: consider whether this target's bid or its campaign's "
                    "budget should be reviewed and adjusted manually. Owner-approved for manual "
                    "verification only."),
}

# entity_id prefixes produced by the Phase 7.4 view builder.
KNOWN_ENTITY_PREFIXES = ("cmp", "adg", "tgt", "st", "rec", "blk", "mrq", "obs", "pol")

# ---------------------------------------------------------------- workspace
_WORKSPACE_SUBDIRS = ("packages", "manifests", "validation", "runtime", "logs")
MAX_ARTIFACT_BYTES = DASH.MAX_ARTIFACT_BYTES

# ---------------------------------------------------------------- permanent Amazon boundary
# Every counter is a constant zero — this process has no code path that could increment any of them.
AMAZON_COUNTERS = {
    "amazon_connections": 0,
    "amazon_sp_api_calls": 0,
    "amazon_ads_api_calls": 0,
    "amazon_mutations": 0,
    "amazon_report_downloads": 0,
    "amazon_bulk_uploads": 0,
    "amazon_api_payloads": 0,
    "browser_automation_attempts": 0,
    "credential_store_count": 0,
    "cookie_store_count": 0,
    "token_store_count": 0,
    "session_store_count": 0,
    "external_network_calls": 0,
    "subprocess_executions_from_data": 0,
}
THIS_SESSION_NEVER = {
    "connects_to_amazon": True, "uses_sp_api": True, "uses_ads_api": True,
    "downloads_reports": True, "automates_a_browser": True, "changes_bids": True,
    "changes_budgets": True, "creates_or_pauses_campaigns": True, "adds_keywords": True,
    "adds_or_removes_targets": True, "creates_negatives": True, "uploads_anything": True,
    "emits_bulk_upload_template": True, "emits_api_payload": True,
    "stores_amazon_credentials": True, "makes_network_calls": True,
    "executes_owner_decisions": True, "claims_completion": True,
    "modifies_phase7_3_source": True, "modifies_phase7_4_review_state": True,
}

DISCLAIMER_LINES = (
    "OFFLINE MANUAL DECISION PACKAGE.",
    "No Amazon connection has been made.",
    "No Amazon action has been performed.",
    "This package is not an Amazon upload template and contains no API payload.",
    "The owner must independently verify every recommendation in Seller Central before making any "
    "manual change.",
)

# Owner verification steps carried on every actionable item (rule / documentation only).
MANUAL_VERIFICATION_STEPS = (
    "Confirm the campaign in Seller Central.",
    "Confirm the ad group.",
    "Confirm the targeting or customer search term.",
    "Confirm the marketplace.",
    "Confirm the currency.",
    "Confirm the attribution window.",
    "Confirm the current Seller Central state for this entity.",
    "Confirm the current bid or budget where applicable.",
    "Confirm the business context (promotions, seasonality, strategy).",
    "Confirm inventory and margin considerations.",
    "Confirm the recommendation is still relevant before acting.",
)


class DecisionPackageError(Exception):
    """A structural failure that blocks package generation. Carries a machine-readable code."""

    def __init__(self, code, detail=""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


# ================================================================ small helpers
def _now_utc():
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt):
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _s(v):
    """Deterministic string projection for identity tuples. None -> empty string."""
    return "" if v is None else str(v)


_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_WINDOW_RE = re.compile(r"_(\d+)d$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_hex64(v):
    return isinstance(v, str) and bool(_HEX64_RE.match(v))


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


# ================================================================ review-state validation (strict)
class _DupKeyGuard:
    """object_pairs_hook that rejects duplicate keys anywhere in the review-state JSON."""

    def __call__(self, pairs):
        seen = set()
        for k, _v in pairs:
            if k in seen:
                raise DecisionPackageError("DUPLICATE_REVIEW_RECORD", k)
            seen.add(k)
        return dict(pairs)


def load_review_state_strict(phase7_4_dir):
    """Load and structurally validate the local Phase 7.4 review state. Read-only.

    Returns ``(status, doc)`` where status is one of MISSING / OK. Raises DecisionPackageError with a
    precise code for structural corruption (duplicate keys, non-finite numbers, null bytes, bad root
    type, wrong schema). Per-record validity (status/hash shape) is evaluated later so a single bad
    record excludes rather than blocks the whole review set."""
    path = DASH.review_state_path(phase7_4_dir)
    if not os.path.isfile(path):
        return "MISSING", {"schema_version": DASH.REVIEW_STATE_SCHEMA, "records": {}}
    size = os.path.getsize(path)
    if size > MAX_ARTIFACT_BYTES:
        raise DecisionPackageError("REVIEW_STATE_OVERSIZE", str(size))
    with open(path, "rb") as f:
        data = f.read()
    if b"\x00" in data:
        raise DecisionPackageError("REVIEW_STATE_NULL_BYTE", "review-state.json")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise DecisionPackageError("REVIEW_STATE_ENCODING", str(e)) from e

    def _reject_const(tok):
        raise DecisionPackageError("REVIEW_STATE_NON_FINITE", tok)

    try:
        doc = json.loads(text, parse_constant=_reject_const, object_pairs_hook=_DupKeyGuard())
    except DecisionPackageError:
        raise
    except ValueError as e:
        raise DecisionPackageError("REVIEW_STATE_MALFORMED_JSON", str(e)) from e
    if not isinstance(doc, dict):
        raise DecisionPackageError("REVIEW_STATE_ROOT_NOT_OBJECT", type(doc).__name__)
    records = doc.get("records")
    if not isinstance(records, dict):
        raise DecisionPackageError("REVIEW_STATE_RECORDS_NOT_OBJECT", type(records).__name__)
    if doc.get("schema_version") != DASH.REVIEW_STATE_SCHEMA:
        raise DecisionPackageError("REVIEW_STATE_SCHEMA_UNEXPECTED", str(doc.get("schema_version")))
    return "OK", doc


def _record_structural_error(entity_id, rec):
    """Return a reason detail if a single review record is structurally unusable, else None."""
    if not isinstance(rec, dict):
        return "record is not an object"
    inner = rec.get("entity_id")
    if inner is not None and inner != entity_id:
        return f"inner entity_id {inner!r} does not match key"
    status = rec.get("review_status")
    if not isinstance(status, str) or status not in DASH.REVIEW_STATUSES:
        return f"invalid review_status {status!r}"
    for hkey in ("content_sha256", "source_manifest_sha256"):
        hv = rec.get(hkey)
        if hv is not None and not _is_hex64(hv):
            return f"invalid {hkey} shape"
    note = rec.get("owner_note", "")
    if note is not None and not isinstance(note, str):
        return "owner_note is not a string"
    return None


def review_state_aggregate_sha256(records):
    """Deterministic hash over the raw review records (sorted, canonical)."""
    return content_sha256({k: records[k] for k in sorted(records)})


# ================================================================ derivation helpers
def _full_index(views):
    """entity_id -> full Phase 7.4 view row, across every reviewable view."""
    idx = {}
    for key in ("campaigns", "ad_groups", "targets", "search_terms",
                "recommendations", "blocked", "manual_review"):
        for row in views.get(key, []):
            idx[row["entity_id"]] = row
    return idx


def _recommendation_type(row):
    et = row.get("entity_type")
    if et == "recommendation":
        return row.get("recommendation_type") or row.get("owner_review_label")
    if et == "search_term":
        return row.get("owner_review_label")
    if et == "manual_review":
        return row.get("suggested_review")
    return None


def _row_is_blocked(row):
    if row.get("entity_type") == "blocked":
        return True
    cls = row.get("primary_classification") or row.get("classification")
    if cls == DASH.CL_NEEDS_OWNER_REVIEW:
        return True
    codes = set(row.get("reason_codes") or [])
    return bool(codes & set(DASH.BLOCKING_REASON_CODES))


def _attribution_windows(evidence_detail):
    windows = set()
    states = {}
    if isinstance(evidence_detail, dict):
        st = evidence_detail.get("metric_states")
        if isinstance(st, dict):
            states = st
    for field in states:
        m = _WINDOW_RE.search(field)
        if m:
            windows.add(int(m.group(1)))
    return [f"{d} day" for d in sorted(windows)]


_CORE_METRIC_STATES = ("clicks", "impressions", "spend")
_BAD_METRIC_STATES = ("MISSING", "BLANK", "INVALID")


def _evidence_sufficient(row):
    ev = row.get("evidence_detail")
    if not isinstance(ev, dict):
        return False
    states = ev.get("metric_states")
    if not isinstance(states, dict) or not states:
        return False
    for k in _CORE_METRIC_STATES:
        if states.get(k) in _BAD_METRIC_STATES:
            return False
    if row.get("spend") is None or row.get("sales") is None:
        return False
    return True


def _policy_dependency(row):
    ev = row.get("evidence_detail")
    src = ev.get("target_acos_source") if isinstance(ev, dict) else None
    if src and src != "OWNER_DECLARED":
        return f"target_acos_source={src}"
    return None


def _report_period(row):
    sd, ed = row.get("start_date"), row.get("end_date")
    if not sd or not ed:
        return None
    return f"{sd} to {ed}"


def _validate_money_fields(row):
    """Preserve decimal metrics as exact strings; reject NaN/Infinity via the money authority.
    Never converts to float, never aggregates. Returns None if usable, else a reason detail."""
    for field in ("spend", "sales"):
        v = row.get(field)
        try:
            MONEY.parse_decimal_string(v, field=field, allow_missing=True)
        except Exception as e:  # noqa: BLE001 - money authority raises ValueError for non-finite
            return f"{field}: {e}"
    return None


def _action_key(rec_type, row, currency, window, report_period):
    """Canonical, prefix-independent manual-action identity (drives duplicate detection)."""
    return (
        _s(rec_type), _s(row.get("campaign")), _s(row.get("ad_group")),
        _s(row.get("targeting")), _s(row.get("match_type")),
        _s(row.get("customer_search_term")), _s(currency), _s(window), _s(report_period),
    )


def _package_item_id(action_key, content_hash):
    ident = {"action_key": list(action_key), "source_content_sha256": _s(content_hash)}
    return "item:" + content_sha256(ident)[:32]


# ================================================================ eligibility evaluation
class _Candidate:
    __slots__ = ("entity_id", "row", "rec", "rec_type", "currency", "window",
                 "report_period", "content_hash", "action_key", "item_id")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def _change_state(row, rec, source_manifest_hash):
    if row is None:
        return "ENTITY_ABSENT"
    if rec.get("content_sha256") != row.get("content_sha256"):
        return "SOURCE_CHANGED"
    if rec.get("source_manifest_sha256") not in (None, source_manifest_hash):
        return "SOURCE_MANIFEST_MISMATCH"
    return "CURRENT"


def _make_excluded(entity_id, rec, row, reason_code, reason_detail, change_state=None):
    row = row or {}
    rec = rec or {}
    return {
        "entity_id": entity_id,
        "source_recommendation_id": (row.get("recommendation_id") or row.get("lineage_hash")
                                     or rec.get("entity_id")),
        "entity_type": row.get("entity_type") or rec.get("entity_type"),
        "review_status": rec.get("review_status"),
        "reason_code": reason_code,
        "reason_detail": reason_detail,
        "source_change_state": change_state,
        "campaign": row.get("campaign"),
        "ad_group": row.get("ad_group"),
        "target": row.get("target") or row.get("targeting"),
        "customer_search_term": row.get("customer_search_term"),
        "currency": row.get("currency"),
        "owner_note": rec.get("owner_note", ""),
    }


def _evaluate_record(entity_id, rec, full_index, source_manifest_hash, struct_detail):
    """Evaluate one review record. Returns ('candidate', _Candidate) for a passing approved record,
    or ('excluded', dict) otherwise. Never raises for data reasons — a bad record is excluded."""
    row = full_index.get(entity_id)
    change_state = _change_state(row, rec if isinstance(rec, dict) else {}, source_manifest_hash)

    # Structural validity first (invalid status / hash shape / mismatched inner id).
    if struct_detail is not None:
        return "excluded", _make_excluded(entity_id, rec, None, R_INVALID_REVIEW_STATE,
                                          struct_detail, change_state)

    status = rec.get("review_status")
    if status != "APPROVED_FOR_MANUAL_ACTION":
        return "excluded", _make_excluded(entity_id, rec, row, R_NOT_APPROVED,
                                          f"review_status={status}", change_state)

    # Entity identity: unknown prefix -> UNKNOWN_ENTITY; valid prefix but gone -> ENTITY_ABSENT.
    prefix = entity_id.split(":", 1)[0] if ":" in entity_id else ""
    if row is None:
        if prefix not in KNOWN_ENTITY_PREFIXES:
            return "excluded", _make_excluded(entity_id, rec, None, R_UNKNOWN_ENTITY,
                                              f"unknown entity id prefix {prefix!r}", change_state)
        return "excluded", _make_excluded(entity_id, rec, None, R_ENTITY_ABSENT,
                                          "entity no longer present in current Phase 7.3 source",
                                          change_state)

    cur_content = row.get("content_sha256")
    content_match = rec.get("content_sha256") == cur_content
    manifest_match = rec.get("source_manifest_sha256") in (None, source_manifest_hash)

    # Content / manifest matrix (see module notes): content drift with a newer manifest is a genuine
    # SOURCE_CHANGED; content drift with the same claimed manifest is a HASH_MISMATCH (tamper/stale);
    # identical content under a changed manifest is a strict SOURCE_MANIFEST_MISMATCH.
    if not content_match:
        code = R_HASH_MISMATCH if manifest_match else R_SOURCE_CHANGED
        return "excluded", _make_excluded(entity_id, rec, row, code,
                                          "approved evidence no longer matches current source",
                                          change_state)
    if not manifest_match:
        return "excluded", _make_excluded(entity_id, rec, row, R_SOURCE_MANIFEST_MISMATCH,
                                          "source manifest hash differs from current source",
                                          change_state)

    if _row_is_blocked(row):
        return "excluded", _make_excluded(entity_id, rec, row, R_BLOCKED_RECOMMENDATION,
                                          "recommendation is blocked / needs owner review", change_state)

    rec_type = _recommendation_type(row)
    if rec_type not in SUPPORTED_RECOMMENDATION_TYPES:
        return "excluded", _make_excluded(entity_id, rec, row, R_UNSUPPORTED_RECOMMENDATION_TYPE,
                                          f"recommendation type {rec_type!r} is not manual-actionable",
                                          change_state)

    if not row.get("campaign") or not row.get("customer_search_term"):
        return "excluded", _make_excluded(entity_id, rec, row, R_MISSING_REQUIRED_FIELD,
                                          "campaign or customer_search_term missing", change_state)

    currency = row.get("currency")
    if not currency:
        return "excluded", _make_excluded(entity_id, rec, row, R_AMBIGUOUS_CURRENCY,
                                          "currency missing or ambiguous", change_state)

    windows = _attribution_windows(row.get("evidence_detail"))
    if len(windows) != 1:
        return "excluded", _make_excluded(entity_id, rec, row, R_AMBIGUOUS_ATTRIBUTION_WINDOW,
                                          f"attribution window is ambiguous: {windows}", change_state)
    window = windows[0]

    money_detail = _validate_money_fields(row)
    if money_detail is not None:
        return "excluded", _make_excluded(entity_id, rec, row, R_MISSING_REQUIRED_FIELD,
                                          money_detail, change_state)

    if not _evidence_sufficient(row):
        return "excluded", _make_excluded(entity_id, rec, row, R_MISSING_EVIDENCE,
                                          "evidence insufficient to reproduce the recommendation",
                                          change_state)

    if rec_type == RT_BID_BUDGET:
        policy = _policy_dependency(row)
        if policy is not None:
            return "excluded", _make_excluded(entity_id, rec, row, R_MISSING_POLICY,
                                              f"required owner policy absent ({policy})", change_state)

    report_period = _report_period(row)
    action_key = _action_key(rec_type, row, currency, window, report_period)
    item_id = _package_item_id(action_key, cur_content)
    return "candidate", _Candidate(
        entity_id=entity_id, row=row, rec=rec, rec_type=rec_type, currency=currency,
        window=window, report_period=report_period, content_hash=cur_content,
        action_key=action_key, item_id=item_id)


# ================================================================ model assembly
def _checklist_item(cand, duplicate_source_ids):
    row, rec = cand.row, cand.rec
    return {
        "package_item_id": cand.item_id,
        "source_recommendation_id": row.get("recommendation_id") or row.get("lineage_hash"),
        "entity_id": cand.entity_id,
        "entity_type": row.get("entity_type"),
        "recommendation_type": cand.rec_type,
        "action_description": ACTION_DESCRIPTIONS.get(cand.rec_type),
        "classification": row.get("primary_classification") or row.get("classification"),
        "reason": row.get("reason"),
        "evidence": row.get("evidence"),
        "campaign": row.get("campaign"),
        "ad_group": row.get("ad_group"),
        "target": row.get("target") or row.get("targeting"),
        "customer_search_term": row.get("customer_search_term"),
        "match_type": row.get("match_type"),
        "currency": cand.currency,
        "attribution_window": cand.window,
        "report_period": cand.report_period,
        "spend": row.get("spend"),
        "sales": row.get("sales"),
        "orders": row.get("orders"),
        "clicks": row.get("clicks"),
        "impressions": row.get("impressions"),
        "acos": row.get("acos"),
        "roas": row.get("roas"),
        "owner_note": rec.get("owner_note", ""),
        "review_status": "APPROVED_FOR_MANUAL_ACTION",
        "source_manifest_sha256": rec.get("source_manifest_sha256"),
        "content_sha256": cand.content_hash,
        "source_change_state": "CURRENT",
        "duplicate_of_count": len(duplicate_source_ids),
        "duplicate_source_ids": sorted(duplicate_source_ids),
        "manual_verification_checklist": list(MANUAL_VERIFICATION_STEPS),
        "owner_completion": "",
        "note": "Manual action candidate. Owner-approved for manual verification only.",
    }


def _dedupe(candidates):
    """Group passing candidates by canonical action identity.

    Identical content collapses to ONE item (duplicate lineage recorded). Conflicting content on the
    same action identity excludes ALL members — never last-write-wins. Deterministic throughout."""
    groups = {}
    for c in candidates:
        groups.setdefault(c.action_key, []).append(c)

    items, excluded = [], []
    dup_identical = 0
    dup_conflict = 0
    duplicate_report = []
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda c: c.entity_id)
        content_hashes = {c.content_hash for c in members}
        if len(content_hashes) > 1:
            dup_conflict += len(members)
            duplicate_report.append({
                "action_key": list(key),
                "resolution": R_DUPLICATE_CONFLICT,
                "member_count": len(members),
                "distinct_content_hashes": sorted(content_hashes),
                "entity_ids": [c.entity_id for c in members],
            })
            for c in members:
                excluded.append(_make_excluded(c.entity_id, c.rec, c.row, R_DUPLICATE_CONFLICT,
                                               "conflicting approved recommendations for the same "
                                               "manual action identity"))
            continue
        keep = members[0]
        dupes = members[1:]
        dup_identical += len(dupes)
        dup_ids = [d.entity_id for d in dupes]
        if dupes:
            duplicate_report.append({
                "action_key": list(key),
                "resolution": R_DUPLICATE_IDENTICAL,
                "kept_entity_id": keep.entity_id,
                "duplicate_entity_ids": dup_ids,
                "duplicate_count": len(dupes),
                "content_sha256": keep.content_hash,
            })
        items.append(_checklist_item(keep, dup_ids))

    items.sort(key=lambda it: (it["recommendation_type"], _s(it["campaign"]), _s(it["ad_group"]),
                               _s(it["target"]), _s(it["match_type"]), _s(it["customer_search_term"]),
                               _s(it["currency"]), _s(it["attribution_window"]),
                               it["package_item_id"]))
    return items, excluded, dup_identical, dup_conflict, duplicate_report


def _resolve_readiness(items, approved_excluded_reasons):
    if items:
        return PKG_READY
    reasons = set(approved_excluded_reasons)
    if R_DUPLICATE_CONFLICT in reasons:
        return CONFLICT_REVIEW_REQUIRED
    if reasons & {R_SOURCE_CHANGED, R_ENTITY_ABSENT, R_HASH_MISMATCH, R_SOURCE_MANIFEST_MISMATCH}:
        return SOURCE_CHANGED_REVIEW_REQUIRED
    if R_MISSING_POLICY in reasons:
        return POLICY_REQUIRED
    return PKG_READY_EMPTY


def build_model(base_dir, phase7_3_dir, phase7_4_dir, reference_date, *,
                include_deferred_summary=False, package_name=None):
    """Validate inputs and build the complete in-memory package model. Never writes. Never touches
    the Phase 7.3 source or the Phase 7.4 review state (read-only)."""
    if not _DATE_RE.match(reference_date or ""):
        raise DecisionPackageError("INVALID_REFERENCE_DATE", str(reference_date))

    # ---- Phase 7.3 source ------------------------------------------------------------------
    source = DASH.load_source(phase7_3_dir)
    if not source.ok:
        readiness = SOURCE_REQUIRED if source.readiness == DASH.SOURCE_REQUIRED else SOURCE_BLOCKED
        return {"readiness": readiness, "source_readiness": source.readiness, "source": source,
                "reasons": source.reasons, "model": None}
    if source.analysis.get("analysis_readiness") != AA.ANALYSIS_READY:
        return {"readiness": SOURCE_BLOCKED,
                "source_readiness": source.analysis.get("analysis_readiness"), "source": source,
                "reasons": [{"code": "ANALYSIS_NOT_READY",
                             "detail": source.analysis.get("analysis_readiness")}], "model": None}

    views = DASH.build_views(source)
    full_index = _full_index(views)
    dq = source.analysis.get("data_quality", {})

    # ---- Phase 7.4 review state ------------------------------------------------------------
    status, doc = load_review_state_strict(phase7_4_dir)
    if status == "MISSING":
        return {"readiness": REVIEW_STATE_REQUIRED,
                "source_readiness": source.analysis.get("analysis_readiness"), "source": source,
                "reasons": [{"code": "REVIEW_STATE_FILE_MISSING",
                             "detail": DASH.review_state_path(phase7_4_dir)}], "model": None}
    raw_records = doc.get("records", {})

    # ---- eligibility -----------------------------------------------------------------------
    approved_total = sum(1 for r in raw_records.values()
                         if isinstance(r, dict) and r.get("review_status") == "APPROVED_FOR_MANUAL_ACTION")
    candidates, excluded = [], []
    approved_excluded_reasons = []
    for entity_id in sorted(raw_records):
        raw = raw_records[entity_id]
        struct_detail = _record_structural_error(entity_id, raw)
        rec = raw if isinstance(raw, dict) else {}
        kind, payload = _evaluate_record(entity_id, rec, full_index,
                                         source.manifest_hash, struct_detail)
        if kind == "candidate":
            candidates.append(payload)
        else:
            excluded.append(payload)
            was_approved = isinstance(raw, dict) and raw.get("review_status") == "APPROVED_FOR_MANUAL_ACTION"
            if was_approved and struct_detail is None:
                approved_excluded_reasons.append(payload["reason_code"])

    items, dup_excluded, dup_identical, dup_conflict, duplicate_report = _dedupe(candidates)
    for ex in dup_excluded:
        excluded.append(ex)
        approved_excluded_reasons.append(ex["reason_code"])

    excluded.sort(key=lambda e: (e["reason_code"], _s(e["entity_id"])))
    readiness = _resolve_readiness(items, approved_excluded_reasons)

    currencies = sorted({it["currency"] for it in items if it["currency"]})
    windows = sorted({it["attribution_window"] for it in items if it["attribution_window"]})
    source_changed = sum(1 for e in excluded
                         if e["reason_code"] in (R_SOURCE_CHANGED, R_ENTITY_ABSENT, R_HASH_MISMATCH,
                                                 R_SOURCE_MANIFEST_MISMATCH)
                         and e["review_status"] == "APPROVED_FOR_MANUAL_ACTION")
    blocked_items = sum(1 for e in excluded if e["reason_code"] == R_BLOCKED_RECOMMENDATION)
    policy_required = sum(1 for e in excluded if e["reason_code"] == R_MISSING_POLICY)

    dates = dq.get("date_coverage", {})
    package_report_period = None
    if dates.get("earliest") and dates.get("latest"):
        package_report_period = f"{dates.get('earliest')} to {dates.get('latest')}"

    review_aggregate = review_state_aggregate_sha256(raw_records)

    counts = {
        "source_rows": dq.get("source_row_count"),
        "analyzed_rows": dq.get("analyzed_row_count"),
        "review_state_records": len(raw_records),
        "approved_records": approved_total,
        "eligible_actions": len(items),
        "excluded_items": len(excluded),
        "duplicate_identical": dup_identical,
        "duplicate_conflicts": dup_conflict,
        "source_changed": source_changed,
        "blocked_items": blocked_items,
        "policy_required": policy_required,
    }

    lineage = {
        "schema_version": LINEAGE_SCHEMA,
        "reference_date": reference_date,
        "phase7_3": {
            "source_dir_type": os.path.basename(os.path.normpath(source.source_dir)),
            "manifest_sha256": source.manifest_hash,
            "analysis_readiness": source.analysis.get("analysis_readiness"),
            "source_row_count": dq.get("source_row_count"),
            "analyzed_row_count": dq.get("analyzed_row_count"),
            "report_period": package_report_period,
            "required_artifacts": source.manifest.get("required_artifacts", []),
            "output_hashes": source.manifest.get("output_hashes", {}),
            "phase7_2_manifest_sha256": (source.manifest.get("source", {}) or {}).get("phase7_2_manifest_sha256"),
            "integrity_result": source.verification.get("result"),
        },
        "phase7_4": {
            "review_state_file": DASH.F_REVIEW_STATE,
            "review_state_schema": doc.get("schema_version"),
            "review_state_aggregate_sha256": review_aggregate,
            "total_records": len(raw_records),
            "approved_records": approved_total,
        },
    }

    # Deterministic analytical content (NO runtime timestamps). The package content hash and package
    # id are derived from exactly this object plus the deterministic artifact hashes.
    deterministic = {
        "schema_version": PACKAGE_SCHEMA,
        "stage_id": STAGE_ID,
        "generator_version": GENERATOR_VERSION,
        "reference_date": reference_date,
        "config": {"include_deferred_summary": bool(include_deferred_summary)},
        "readiness": readiness,
        "counts": counts,
        "currencies": currencies,
        "attribution_windows": windows,
        "report_period": package_report_period,
        "eligible_items": items,
        "excluded_items": excluded,
        "duplicate_report": duplicate_report,
        "source_lineage": lineage,
        "amazon_boundary": dict(AMAZON_COUNTERS),
        "this_session_never": dict(THIS_SESSION_NEVER),
        "manual_action_disclaimer": list(DISCLAIMER_LINES),
    }

    model = {
        "deterministic": deterministic,
        "readiness": readiness,
        "source_readiness": source.analysis.get("analysis_readiness"),
        "counts": counts,
        "items": items,
        "excluded": excluded,
        "duplicate_report": duplicate_report,
        "lineage": lineage,
        "review_aggregate_sha256": review_aggregate,
        "include_deferred_summary": bool(include_deferred_summary),
        "package_name": package_name,
        "raw_records": raw_records,
    }
    return {"readiness": readiness, "source_readiness": model["source_readiness"],
            "source": source, "model": model, "reasons": []}


# ================================================================ artifact rendering (deterministic)
_TSV_CELL = DASH._tsv_cell  # reuse the accepted Phase 7.4 formula-injection rule


def _tsv(columns, rows):
    """Render a TSV whose every row has exactly len(columns) cells, formula-injection-safe."""
    lines = ["\t".join(columns)]
    for r in rows:
        lines.append("\t".join(_TSV_CELL(r.get(c)) for c in columns))
    return "\n".join(lines) + "\n"


def _disclaimer_md(prefix="> "):
    return "\n".join(prefix + line for line in DISCLAIMER_LINES)


def _disclaimer_hash_comment():
    return "\n".join("# " + line for line in DISCLAIMER_LINES)


CHECKLIST_COLUMNS = (
    "package_item_id", "source_recommendation_id", "entity_type", "recommendation_type",
    "action_description", "classification", "reason", "evidence", "campaign", "ad_group",
    "target", "customer_search_term", "match_type", "currency", "attribution_window",
    "report_period", "spend", "sales", "orders", "clicks", "impressions", "acos", "roas",
    "owner_note", "review_status", "source_manifest_sha256", "content_sha256",
    "duplicate_of_count", "manual_verification", "owner_completion",
)

EXCLUDED_COLUMNS = (
    "entity_id", "source_recommendation_id", "entity_type", "review_status", "reason_code",
    "reason_detail", "source_change_state", "campaign", "ad_group", "target",
    "customer_search_term", "currency", "owner_note",
)


def _owner_read_first_md(model):
    d = model["deterministic"]
    counts = d["counts"]
    lines = [
        "# OWNER READ FIRST — Offline Manual Decision Package",
        "",
        "**OFFLINE MANUAL DECISION PACKAGE**",
        "",
        "- No Amazon connection has been made.",
        "- No Amazon action has been performed.",
        "- This package is not an Amazon upload template. It contains no bulk-upload file and no API "
        "payload.",
        "- This tool has made no change in Amazon and has transmitted nothing to Amazon. Every item "
        "is a suggestion for the owner to review manually.",
        "",
        "The owner must independently verify every recommendation in Seller Central before making any "
        "manual change. The owner is responsible for confirming:",
        "",
    ]
    lines += [f"- {step[len('Confirm '):].rstrip('.') if step.startswith('Confirm ') else step}"
              for step in (
        "Confirm campaign.", "Confirm ad group.", "Confirm targeting or search term.",
        "Confirm marketplace.", "Confirm currency.", "Confirm attribution window.",
        "Confirm current Seller Central state.", "Confirm current bid or budget where applicable.",
        "Confirm business context.", "Confirm inventory and margin considerations.",
        "Confirm whether the recommendation is still relevant.")]
    lines += [
        "",
        "Every item below is a **suggested manual review** — an owner-approved manual action "
        "candidate, not a completed action.",
        "",
        f"- Package readiness: `{d['readiness']}`",
        f"- Owner-approved review records: {counts['approved_records']}",
        f"- Manual action candidates (eligible): {counts['eligible_actions']}",
        f"- Excluded / attention items: {counts['excluded_items']}",
        f"- Reference date: `{d['reference_date']}`",
        "",
    ]
    if counts["eligible_actions"] == 0:
        lines.append("No owner-approved, current, unblocked decisions are eligible for manual "
                     "action. See `excluded_items.tsv` for every reviewed record and the exact "
                     "reason it is not in the checklist.")
        lines.append("")
    return "\n".join(lines)


def _executive_summary_md(model):
    d = model["deterministic"]
    counts = d["counts"]
    out = ["# Executive Summary — Owner Decision Package", "", _disclaimer_md(), "",
           f"- Readiness: `{d['readiness']}`",
           f"- Phase 7.3 source readiness: `{model['source_readiness']}`",
           f"- Source rows: {counts['source_rows']}",
           f"- Analyzed rows: {counts['analyzed_rows']}",
           f"- Review-state records: {counts['review_state_records']}",
           f"- Owner-approved records: {counts['approved_records']}",
           f"- Eligible manual action candidates: {counts['eligible_actions']}",
           f"- Excluded / attention items: {counts['excluded_items']}",
           f"- Duplicate (identical, deduplicated): {counts['duplicate_identical']}",
           f"- Duplicate (conflicting, excluded): {counts['duplicate_conflicts']}",
           f"- Source-changed approvals: {counts['source_changed']}",
           f"- Blocked recommendations: {counts['blocked_items']}",
           f"- Missing-policy approvals: {counts['policy_required']}",
           f"- Currencies present: {', '.join(d['currencies']) if d['currencies'] else '—'} "
           "(never aggregated across currencies)",
           f"- Attribution windows present: "
           f"{', '.join(d['attribution_windows']) if d['attribution_windows'] else '—'} "
           "(never aggregated across windows)",
           ""]
    if counts["eligible_actions"] == 0:
        out.append("No owner-approved, current, unblocked decisions are eligible for manual action.")
        out.append("")
    out.append("This is a preparation and documentation layer only. The owner performs every action "
               "manually in Amazon Seller Central.")
    out.append("")
    return "\n".join(out)


def _checklist_tsv(model):
    rows = []
    for it in model["items"]:
        r = dict(it)
        r["manual_verification"] = "See OWNER_READ_FIRST.md and decision_details.md"
        rows.append(r)
    return _disclaimer_hash_comment() + "\n" + _tsv(CHECKLIST_COLUMNS, rows)


def _checklist_json(model):
    d = model["deterministic"]
    meta = {
        "schema_version": CHECKLIST_SCHEMA,
        "stage_id": STAGE_ID,
        "generator_version": GENERATOR_VERSION,
        "readiness": d["readiness"],
        "reference_date": d["reference_date"],
        "source_manifest_sha256": d["source_lineage"]["phase7_3"]["manifest_sha256"],
        "review_state_aggregate_sha256": d["source_lineage"]["phase7_4"]["review_state_aggregate_sha256"],
        "eligible_action_count": len(model["items"]),
        "currencies": d["currencies"],
        "attribution_windows": d["attribution_windows"],
        "manual_action_disclaimer": list(DISCLAIMER_LINES),
        "amazon_action_performed": False,
    }
    return canonical_json({"meta": meta, "items": model["items"]}) + "\n"


def _decision_details_md(model):
    out = ["# Decision Details — Manual Action Candidates", "", _disclaimer_md(), ""]
    if not model["items"]:
        out += ["No owner-approved, current, unblocked decisions are eligible for manual action.", ""]
        return "\n".join(out)
    for i, it in enumerate(model["items"], 1):
        out += [
            f"## {i}. {it['recommendation_type']} — {it.get('customer_search_term') or '—'}",
            "",
            f"- Package item id: `{it['package_item_id']}`",
            f"- Source recommendation id: `{it['source_recommendation_id']}`",
            f"- Action (review only): {it['action_description']}",
            f"- Campaign: {it.get('campaign') or '—'}",
            f"- Ad group: {it.get('ad_group') or '—'}",
            f"- Target / search term: {it.get('target') or '—'} / {it.get('customer_search_term') or '—'}",
            f"- Match type: {it.get('match_type') or '—'}",
            f"- Currency: {it.get('currency') or '—'} | Attribution window: "
            f"{it.get('attribution_window') or '—'} | Report period: {it.get('report_period') or '—'}",
            f"- Metrics — spend: {it.get('spend') or '—'}, sales: {it.get('sales') or '—'}, "
            f"orders: {it.get('orders') if it.get('orders') is not None else '—'}, "
            f"clicks: {it.get('clicks') if it.get('clicks') is not None else '—'}, "
            f"impressions: {it.get('impressions') if it.get('impressions') is not None else '—'}, "
            f"ACoS: {it.get('acos') or '—'}, ROAS: {it.get('roas') or '—'}",
            f"- Classification: {it.get('classification') or '—'}",
            f"- Reason: {it.get('reason') or '—'}",
            f"- Evidence: {it.get('evidence') or '—'}",
            f"- Owner note: {it.get('owner_note') or '—'}",
            f"- Phase 7.4 review status: {it['review_status']} (owner-approved for manual "
            "verification only)",
            f"- Source manifest sha256: `{it.get('source_manifest_sha256')}`",
            f"- Content sha256: `{it.get('content_sha256')}`",
            f"- Duplicate identical folded in: {it.get('duplicate_of_count', 0)}",
            "",
            "Manual verification checklist (owner completes in Seller Central):",
            "",
        ]
        out += [f"- [ ] {step}" for step in it["manual_verification_checklist"]]
        out += ["- [ ] Owner completion (leave blank until performed manually in Seller Central): "
                "______", ""]
    return "\n".join(out)


def _excluded_tsv(model):
    return _disclaimer_hash_comment() + "\n" + _tsv(EXCLUDED_COLUMNS, model["excluded"])


def _excluded_json(model):
    meta = {
        "schema_version": EXCLUSIONS_SCHEMA,
        "stage_id": STAGE_ID,
        "excluded_item_count": len(model["excluded"]),
        "manual_action_disclaimer": list(DISCLAIMER_LINES),
    }
    return canonical_json({"meta": meta, "excluded_items": model["excluded"],
                           "duplicate_report": model["duplicate_report"]}) + "\n"


def _source_lineage_json(model):
    return canonical_json(model["lineage"]) + "\n"


def _deferred_items_tsv(model):
    rows = [e for e in model["excluded"] if e.get("review_status") == "DEFERRED"]
    return (_disclaimer_hash_comment() + "\n" + _tsv(EXCLUDED_COLUMNS, rows))


def _policy_requirements_md(model):
    rows = [e for e in model["excluded"] if e["reason_code"] == R_MISSING_POLICY]
    out = ["# Policy Requirements", "", _disclaimer_md(), ""]
    if not rows:
        out += ["No owner-policy prerequisites are outstanding for the approved records.", ""]
    else:
        out += ["The following approved records require an owner policy decision before a manual "
                "action can be prepared:", ""]
        for e in rows:
            out.append(f"- `{e['entity_id']}` — {e['reason_detail']}")
        out.append("")
    return "\n".join(out)


def _deterministic_artifacts(model):
    """Ordered mapping of deterministic package files -> UTF-8 text (no runtime timestamps)."""
    arts = {
        "OWNER_READ_FIRST.md": _owner_read_first_md(model),
        "executive_summary.md": _executive_summary_md(model),
        "manual_action_checklist.tsv": _checklist_tsv(model),
        "manual_action_checklist.json": _checklist_json(model),
        "decision_details.md": _decision_details_md(model),
        "excluded_items.tsv": _excluded_tsv(model),
        "excluded_items.json": _excluded_json(model),
        "source_lineage.json": _source_lineage_json(model),
    }
    if model["include_deferred_summary"]:
        arts["deferred_items.tsv"] = _deferred_items_tsv(model)
        arts["policy_requirements.md"] = _policy_requirements_md(model)
    return arts


# ================================================================ package identity & manifest
def _package_content_sha256(model, artifact_hashes):
    """The single deterministic content hash: analytical model + deterministic artifact hashes."""
    payload = {
        "deterministic": model["deterministic"],
        "artifact_sha256": {k: artifact_hashes[k] for k in sorted(artifact_hashes)},
    }
    return content_sha256(payload)


def _package_id(content_hash):
    return "pkg-" + content_hash[:16]


_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _package_dir_name(model, content_hash):
    name = model.get("package_name")
    if name is None:
        return _package_id(content_hash)
    if not _SAFE_NAME_RE.match(name) or name in (".", ".."):
        raise DecisionPackageError("UNSAFE_PACKAGE_NAME", name)
    return name


def _build_manifest(model, content_hash, package_id, dir_name, artifact_hashes, *, now):
    d = model["deterministic"]
    counts = d["counts"]
    lineage = d["source_lineage"]
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "stage_id": STAGE_ID,
        "package_id": package_id,
        "package_dir_name": dir_name,
        "readiness": d["readiness"],
        "package_content_sha256": content_hash,
        "generator_version": GENERATOR_VERSION,
        "reference_date": d["reference_date"],
        "config": d["config"],
        "phase7_3_source_dir_type": lineage["phase7_3"]["source_dir_type"],
        "phase7_3_manifest_sha256": lineage["phase7_3"]["manifest_sha256"],
        "phase7_4_review_state_aggregate_sha256": lineage["phase7_4"]["review_state_aggregate_sha256"],
        "source_rows": counts["source_rows"],
        "analyzed_rows": counts["analyzed_rows"],
        "review_state_records": counts["review_state_records"],
        "approved_records": counts["approved_records"],
        "eligible_action_count": counts["eligible_actions"],
        "excluded_item_count": counts["excluded_items"],
        "duplicate_identical_count": counts["duplicate_identical"],
        "duplicate_conflict_count": counts["duplicate_conflicts"],
        "source_changed_count": counts["source_changed"],
        "blocked_count": counts["blocked_items"],
        "policy_required_count": counts["policy_required"],
        "currencies": d["currencies"],
        "attribution_windows": d["attribution_windows"],
        "report_period": d["report_period"],
        "output_artifacts": sorted(artifact_hashes),
        "artifact_sha256": {k: artifact_hashes[k] for k in sorted(artifact_hashes)},
        "amazon_boundary": dict(AMAZON_COUNTERS),
        "this_session_never": dict(THIS_SESSION_NEVER),
        "manual_action_disclaimer": list(DISCLAIMER_LINES),
        "amazon_action_performed": False,
        # Runtime metadata is isolated here and NEVER feeds package_content_sha256.
        "runtime_metadata": {
            "generated_at": _iso(now()),
            "generator": GENERATOR_VERSION,
        },
    }
    return manifest


# ================================================================ atomic write
def ensure_workspace(base_dir):
    base = os.path.abspath(base_dir)
    for sub in _WORKSPACE_SUBDIRS:
        os.makedirs(os.path.join(base, sub), exist_ok=True)
    return base


def _write_bytes(path, data):
    with open(path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _write_atomic_file(path, data):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    _write_bytes(tmp, data)
    os.replace(tmp, path)


def _package_dir(base_dir, dir_name):
    return os.path.join(os.path.abspath(base_dir), "packages", dir_name)


def _read_manifest(pkg_dir):
    path = os.path.join(pkg_dir, "package_manifest.json")
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def _verify_existing_package(pkg_dir, content_hash, artifact_hashes):
    """Return True if the existing package on disk is byte-identical to what we would write."""
    manifest = _read_manifest(pkg_dir)
    if not manifest or manifest.get("package_content_sha256") != content_hash:
        return False
    stored = manifest.get("artifact_sha256", {})
    if stored != {k: artifact_hashes[k] for k in sorted(artifact_hashes)}:
        return False
    for name, want in artifact_hashes.items():
        p = os.path.join(pkg_dir, name)
        if not os.path.isfile(p):
            return False
        with open(p, "rb") as f:
            if _sha256_hex(f.read()) != want:
                return False
    return True


def _generate_package(base_dir, model, *, now):
    """Write the package atomically. Returns a dict describing the outcome. Never overwrites an
    existing package whose content differs (integrity conflict -> PACKAGE_BLOCKED)."""
    base = ensure_workspace(base_dir)
    artifacts = _deterministic_artifacts(model)
    artifact_bytes = {name: text.encode("utf-8") for name, text in artifacts.items()}
    artifact_hashes = {name: _sha256_hex(b) for name, b in artifact_bytes.items()}
    content_hash = _package_content_sha256(model, artifact_hashes)
    dir_name = _package_dir_name(model, content_hash)
    package_id = _package_id(content_hash)
    pkg_dir = _package_dir(base, dir_name)

    # ---- idempotency / integrity ----------------------------------------------------------
    if os.path.isdir(pkg_dir):
        if _verify_existing_package(pkg_dir, content_hash, artifact_hashes):
            return {"outcome": "IDEMPOTENT_REUSE", "package_id": package_id,
                    "package_dir": pkg_dir, "content_hash": content_hash, "readiness": model["readiness"]}
        return {"outcome": "INTEGRITY_CONFLICT", "package_id": package_id, "package_dir": pkg_dir,
                "content_hash": content_hash, "readiness": PACKAGE_BLOCKED,
                "detail": "an existing package with this identity has different content"}

    # ---- atomic build ---------------------------------------------------------------------
    tmp_dir = os.path.join(base, "runtime", ".build-" + dir_name)
    if os.path.isdir(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.makedirs(tmp_dir, exist_ok=True)
    try:
        for name, b in sorted(artifact_bytes.items()):
            _write_bytes(os.path.join(tmp_dir, name), b)
        manifest = _build_manifest(model, content_hash, package_id, dir_name, artifact_hashes, now=now)
        _write_bytes(os.path.join(tmp_dir, "package_manifest.json"),
                     (canonical_json(manifest) + "\n").encode("utf-8"))
        # ---- validate generated artifacts from bytes before publishing --------------------
        for name, want in artifact_hashes.items():
            with open(os.path.join(tmp_dir, name), "rb") as f:
                if _sha256_hex(f.read()) != want:
                    raise DecisionPackageError("ARTIFACT_VERIFY_FAILED", name)
        try:
            dirfd = os.open(tmp_dir, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        except (OSError, AttributeError):
            pass  # directory fsync is best-effort (not supported on all platforms)
        os.replace(tmp_dir, pkg_dir)
    except BaseException:
        if os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    # ---- workspace copies (outside the package) -------------------------------------------
    _write_atomic_file(os.path.join(base, "manifests", package_id + ".json"),
                       (canonical_json(_read_manifest(pkg_dir)) + "\n").encode("utf-8"))
    return {"outcome": "CREATED", "package_id": package_id, "package_dir": pkg_dir,
            "content_hash": content_hash, "readiness": model["readiness"]}


# ================================================================ orchestration
def run(base_dir, phase7_3_dir, phase7_4_dir, reference_date, *,
        include_deferred_summary=False, package_name=None, validate_only=False, now=None):
    """Full Phase 7.5 run. Returns a result dict with readiness, counts, and (when a package is
    generated) package id/dir. Read-only toward Phase 7.3 and Phase 7.4."""
    now = now or _now_utc
    built = build_model(base_dir, phase7_3_dir, phase7_4_dir, reference_date,
                        include_deferred_summary=include_deferred_summary, package_name=package_name)
    readiness = built["readiness"]
    result = {
        "readiness": readiness,
        "source_readiness": built["source_readiness"],
        "counts": built["model"]["counts"] if built["model"] else _empty_counts(built["source"]),
        "reasons": built.get("reasons", []),
        "package_id": None,
        "package_dir": None,
        "content_sha256": None,
        "outcome": None,
        "validate_only": bool(validate_only),
        "amazon_counters": dict(AMAZON_COUNTERS),
    }
    if built["model"] is None or readiness in _BLOCKING_READINESS:
        return result
    if validate_only:
        result["outcome"] = "VALIDATED"
        return result

    gen = _generate_package(base_dir, built["model"], now=now)
    result["package_id"] = gen["package_id"]
    result["package_dir"] = gen["package_dir"]
    result["content_sha256"] = gen["content_hash"]
    result["outcome"] = gen["outcome"]
    if gen["outcome"] == "INTEGRITY_CONFLICT":
        result["readiness"] = PACKAGE_BLOCKED
        result["reasons"] = [{"code": "PACKAGE_INTEGRITY_CONFLICT", "detail": gen.get("detail")}]
    return result


def _empty_counts(source):
    dq = (source.analysis.get("data_quality", {}) if source and source.ok else {})
    return {"source_rows": dq.get("source_row_count"), "analyzed_rows": dq.get("analyzed_row_count"),
            "review_state_records": 0, "approved_records": 0, "eligible_actions": 0,
            "excluded_items": 0, "duplicate_identical": 0, "duplicate_conflicts": 0,
            "source_changed": 0, "blocked_items": 0, "policy_required": 0}


# ================================================================ CLI
def _summary_pairs(result):
    c = result["counts"]
    ac = result["amazon_counters"]
    return [
        ("package_readiness", result["readiness"]),
        ("source_readiness", result["source_readiness"]),
        ("source_rows", c.get("source_rows")),
        ("analyzed_rows", c.get("analyzed_rows")),
        ("review_state_records", c.get("review_state_records")),
        ("approved_records", c.get("approved_records")),
        ("eligible_actions", c.get("eligible_actions")),
        ("excluded_items", c.get("excluded_items")),
        ("duplicate_identical", c.get("duplicate_identical")),
        ("duplicate_conflicts", c.get("duplicate_conflicts")),
        ("source_changed", c.get("source_changed")),
        ("blocked_items", c.get("blocked_items")),
        ("policy_required", c.get("policy_required")),
        ("amazon_connections", ac["amazon_connections"]),
        ("amazon_sp_api_calls", ac["amazon_sp_api_calls"]),
        ("amazon_ads_api_calls", ac["amazon_ads_api_calls"]),
        ("amazon_mutations", ac["amazon_mutations"]),
        ("external_network_calls", ac["external_network_calls"]),
        ("idempotent_reuse", "true" if result.get("outcome") == "IDEMPOTENT_REUSE" else "false"),
        ("validate_only", "true" if result.get("validate_only") else "false"),
        ("package_id", result.get("package_id")),
        ("package_dir", result.get("package_dir")),
    ]


def format_summary(result, fmt="text"):
    if fmt == "json":
        return canonical_json({k: v for k, v in _summary_pairs(result)})
    return "\n".join(f"{k}={'' if v is None else v}" for k, v in _summary_pairs(result))


def exit_code_for(readiness):
    return 2 if readiness in _BLOCKING_READINESS else 0


def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="python -m production.phase7_owner_decision_package",
        description="Offline Owner Decision Package generator (Phase 7.5). Reads accepted Phase 7.3 "
                    "promoted output and local Phase 7.4 review state; writes a deterministic, "
                    "human-readable manual-review package. Connects to nothing; performs no Amazon "
                    "action; is not an upload template.")
    p.add_argument("--base-dir", default=os.path.join("runs", "T2", "phase7", "7.5"),
                   help="Phase 7.5 workspace (packages/ manifests/ validation/ runtime/ logs/).")
    p.add_argument("--phase7-3-dir", default=os.path.join("runs", "T2", "phase7", "7.3"),
                   help="Phase 7.3 workspace root (its promoted/ takes precedence over stale final/).")
    p.add_argument("--phase7-4-dir", default=os.path.join("runs", "T2", "phase7", "7.4"),
                   help="Phase 7.4 workspace root (review_state/review-state.json).")
    p.add_argument("--reference-date", required=True,
                   help="Reference date (YYYY-MM-DD) recorded in the deterministic package content.")
    p.add_argument("--package-name", default=None,
                   help="Optional fixed package directory name (default: content-addressed pkg-<hash>).")
    p.add_argument("--format", choices=("text", "json"), default="text",
                   help="CLI summary format (default: text).")
    p.add_argument("--include-deferred-summary", action="store_true",
                   help="Also emit deferred_items.tsv and policy_requirements.md.")
    p.add_argument("--validate-only", action="store_true",
                   help="Validate inputs and report counts without creating any package or files.")
    return p


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        result = run(args.base_dir, args.phase7_3_dir, args.phase7_4_dir, args.reference_date,
                     include_deferred_summary=args.include_deferred_summary,
                     package_name=args.package_name, validate_only=args.validate_only)
    except DecisionPackageError as e:
        sys.stdout.write(f"package_readiness={PACKAGE_BLOCKED}\nerror_code={e.code}\n"
                         f"error_detail={e.detail}\n")
        return 2
    sys.stdout.write(format_summary(result, fmt=args.format) + "\n")
    return exit_code_for(result["readiness"])


if __name__ == "__main__":
    raise SystemExit(main())
