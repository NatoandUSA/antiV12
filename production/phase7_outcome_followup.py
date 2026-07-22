#!/usr/bin/env python3
"""Phase 7.7 — offline Outcome Follow-up over accepted Phase 7.6 records + later Phase 7.3 reports.

This module is the ONE Phase 7.7 authority. It is a local MEASUREMENT-AND-DOCUMENTATION layer only.

It connects to NOTHING. There is no Amazon integration, no SP-API, no Ads API, no browser
automation, and no network client of any kind. It never changes a bid, budget, keyword, negative,
target, or campaign, never uploads anything, never emits an API payload, and never claims that an
Amazon action happened or that the owner-recorded action CAUSED any result. The owner remains the
only manual bridge to Amazon Seller Central.

What it does:
  * reads (read-only) an accepted Phase 7.6 manual-action tracker state + append-only history (reusing
    the Phase 7.6 authority to validate the chain, records, and package binding);
  * reads (read-only) a later Phase 7.3 offline analysis (its accepted promoted/ output, verified
    against its own manifest) that already contains — because Phase 7.2 ingestion is CUMULATIVE — rows
    spanning both the before window and the after window;
  * for each owner-recorded MANUALLY_COMPLETED action, matches the action's stable entity identity in
    the Phase 7.3 rows, slices those rows into an explicit before window and after window, and
    documents the observed before/after performance and its deltas;
  * classifies the observation CONSERVATIVELY (OBSERVED_IMPROVEMENT / OBSERVED_DECLINE /
    OBSERVED_MIXED / OBSERVED_NO_MATERIAL_CHANGE / INSUFFICIENT_DATA) and NEVER asserts causation;
  * writes only under its own Phase 7.7 workspace, atomically, with content-addressed idempotency.

Model note (accepted repository convention): there is exactly one Phase 7.3 promoted/ directory and
Phase 7.2 ingestion preserves prior promoted rows (cumulative). A "later report" therefore lives in
the SAME promoted analysis as the earlier one; the before/after split is done by EXPLICIT, owner-
supplied date windows over that single cumulative source. No later analytical snapshot is stored
elsewhere.

Financial rule: every authoritative metric is an exact string or a Decimal (via core.money). Nothing
is ever coerced to float; a missing value stays missing and is never turned into zero.
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

from production import phase7_manual_action_tracker as TRK   # noqa: E402  Phase 7.6 authority
from production import phase7_owner_decision_package as PKG   # noqa: E402  Phase 7.5 authority (schema)
from production import phase7_owner_dashboard as DASH         # noqa: E402  formula-safe TSV / limits
from production import product_workspace as PW                # noqa: E402  canonical json / content hash
from core import money as MONEY                               # noqa: E402  Decimal authority (no float)

canonical_json = PW.canonical_json
content_sha256 = PW.content_sha256

# ================================================================ constants / versions
STAGE_ID = "7.7"
STAGE_NAME = "Offline Outcome Follow-up"
GENERATOR_VERSION = "phase7-7-outcome-followup-v1"
FOLLOWUP_SCHEMA = "phase7-7-followup-record-v1"
STATUS_EXPORT_SCHEMA = "phase7-7-outcome-status-v1"
EXCLUSIONS_SCHEMA = "phase7-7-excluded-followups-v1"
LINEAGE_SCHEMA = "phase7-7-source-lineage-v1"
MANIFEST_SCHEMA = "phase7-7-followup-manifest-v1"
POLICY_SCHEMA = "phase7-7-outcome-policy-v1"

# Phase 7.3 analysis contract (never assumed — pinned to the accepted manifest schema).
PHASE7_3_MANIFEST_SCHEMA = "phase7-3-analysis-manifest-v1"
PHASE7_3_ANALYSIS_FILE = "analysis.json"
PHASE7_3_MANIFEST_FILE = "analysis-manifest.json"
PHASE7_3_PROMOTED = "promoted"

# ---------------------------------------------------------------- readiness states
FOLLOWUP_READY = "SESSION7_7_FOLLOWUP_READY"
FOLLOWUP_READY_EMPTY = "SESSION7_7_FOLLOWUP_READY_EMPTY"
TRACKER_REQUIRED = "SESSION7_7_TRACKER_REQUIRED"
TRACKER_BLOCKED = "SESSION7_7_TRACKER_BLOCKED"
SOURCE_REQUIRED = "SESSION7_7_SOURCE_REQUIRED"
SOURCE_BLOCKED = "SESSION7_7_SOURCE_BLOCKED"
WINDOW_NOT_READY = "SESSION7_7_WINDOW_NOT_READY"
POLICY_REQUIRED = "SESSION7_7_POLICY_REQUIRED"
ENTITY_MATCH_REQUIRED = "SESSION7_7_ENTITY_MATCH_REQUIRED"
FOLLOWUP_CONFLICT = "SESSION7_7_FOLLOWUP_CONFLICT"
FOLLOWUP_BLOCKED = "SESSION7_7_FOLLOWUP_BLOCKED"

# Only these three return exit code 0 (a valid, non-blocked result).
_OK_READINESS = (FOLLOWUP_READY, FOLLOWUP_READY_EMPTY)

# ---------------------------------------------------------------- outcome classifications
OBSERVED_IMPROVEMENT = "OBSERVED_IMPROVEMENT"
OBSERVED_DECLINE = "OBSERVED_DECLINE"
OBSERVED_MIXED = "OBSERVED_MIXED"
OBSERVED_NO_MATERIAL_CHANGE = "OBSERVED_NO_MATERIAL_CHANGE"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
POLICY_REQUIRED_CLASS = "POLICY_REQUIRED"
# exclusion classifications (never reach the outcome table)
ENTITY_NOT_FOUND = "ENTITY_NOT_FOUND"
AMBIGUOUS_ENTITY_MATCH = "AMBIGUOUS_ENTITY_MATCH"
WINDOW_NOT_READY_CLASS = "WINDOW_NOT_READY"
SOURCE_CHANGED = "SOURCE_CHANGED"
PACKAGE_CHANGED = "PACKAGE_CHANGED"
TRACKER_STATE_CHANGED = "TRACKER_STATE_CHANGED"
CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
ATTRIBUTION_WINDOW_MISMATCH = "ATTRIBUTION_WINDOW_MISMATCH"
FOLLOWUP_CONFLICT_CLASS = "FOLLOWUP_CONFLICT"
NOT_ELIGIBLE_STATUS = "NOT_ELIGIBLE_STATUS"
REVERTED_SEPARATE = "REVERTED_SEPARATE"
MISSING_IDENTITY = "MISSING_IDENTITY"

# classifications that count as a produced follow-up observation (the outcome table).
_OUTCOME_CLASSES = (OBSERVED_IMPROVEMENT, OBSERVED_DECLINE, OBSERVED_MIXED,
                    OBSERVED_NO_MATERIAL_CHANGE, INSUFFICIENT_DATA)
# exclusions that mean "the owner must resolve an entity identity" (zero-eligible headline).
_ENTITY_EXCLUSIONS = (ENTITY_NOT_FOUND, AMBIGUOUS_ENTITY_MATCH, MISSING_IDENTITY)

CLASSIFICATION_PHRASING = {
    OBSERVED_IMPROVEMENT: "Performance changed during the follow-up window (observed improvement). "
                          "Observed after the owner-recorded manual action.",
    OBSERVED_DECLINE: "Performance changed during the follow-up window (observed decline). "
                      "Observed after the owner-recorded manual action.",
    OBSERVED_MIXED: "Performance changed during the follow-up window (mixed signals). "
                    "Observed after the owner-recorded manual action.",
    OBSERVED_NO_MATERIAL_CHANGE: "No material performance change was observed during the follow-up "
                                 "window. Observed after the owner-recorded manual action.",
    INSUFFICIENT_DATA: "Insufficient evidence to determine an outcome.",
}

# ---------------------------------------------------------------- confidence / evidence (data sufficiency only)
SUFFICIENT_OBSERVATION = "SUFFICIENT_OBSERVATION"
LIMITED_OBSERVATION = "LIMITED_OBSERVATION"
INSUFFICIENT_OBSERVATION = "INSUFFICIENT_OBSERVATION"

# ---------------------------------------------------------------- default classification policy
# A neutral, owner-configurable default (the accepted repository convention — cf. the Phase 7.3
# NEUTRAL_DEFAULT target ACoS). Its source is recorded in every output; it invents no hidden number.
DEFAULT_POLICY = {
    "schema_version": POLICY_SCHEMA,
    "policy_id": "phase7-7-outcome-policy-v1",
    "source": "NEUTRAL_DEFAULT_OWNER_CONFIGURABLE",
    "material_change_ratio": "0.10",       # a >=10% relative change on a directional metric is material
    "minimum_before_clicks": "1",
    "minimum_after_clicks": "1",
    # directional metrics only (spend/impressions/clicks are documented but never drive improve/decline):
    "directional_metrics": {
        "acos": "lower_better",
        "cpc": "lower_better",
        "conversion_rate": "higher_better",
        "roas": "higher_better",
        "orders": "higher_better",
        "sales": "higher_better",
    },
}

# ---------------------------------------------------------------- metric field sets
_COUNT_METRICS = ("impressions", "clicks", "orders", "units")
_MONEY_METRICS = ("spend", "sales")
_DERIVED_METRICS = ("cpc", "ctr", "conversion_rate", "acos", "roas")
_ALL_METRICS = _COUNT_METRICS + _MONEY_METRICS + _DERIVED_METRICS
_CURRENCY_SERIALIZED = ("spend", "sales", "cpc")   # 2dp
_RATE_SERIALIZED = ("ctr", "conversion_rate", "acos", "roas")   # 6dp

# ---------------------------------------------------------------- workspace
_WORKSPACE_SUBDIRS = ("followups", "manifests", "exports", "validation", "runtime", "logs")
MAX_ARTIFACT_BYTES = DASH.MAX_ARTIFACT_BYTES

PACKAGE_FILES = (
    "OWNER_READ_FIRST.md",
    "executive_summary.md",
    "outcome_details.md",
    "outcome_status.tsv",
    "outcome_status.json",
    "excluded_followups.tsv",
    "excluded_followups.json",
    "source_lineage.json",
    "followup_manifest.json",
)

# ---------------------------------------------------------------- permanent Amazon boundary
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
    "performs_the_action": True, "infers_action_without_a_phase7_6_record": True,
    "claims_the_action_caused_the_result": True, "claims_amazon_confirmed_the_result": True,
    "modifies_phase7_3_source": True, "modifies_phase7_5_package": True,
    "modifies_phase7_6_state": True,
}

DISCLAIMER_LINES = (
    "OFFLINE OBSERVATIONAL FOLLOW-UP.",
    "No Amazon connection was made.",
    "No Amazon action was performed by this software.",
    "The recorded performance change is observational and does not establish that the "
    "owner-recorded action caused the result.",
    "Other factors may include seasonality, pricing, competition, inventory, listing changes, "
    "advertising changes made outside the tracker, reporting delays, attribution timing, and "
    "marketplace conditions.",
    "This file is not an Amazon upload template and contains no API payload.",
)
OTHER_FACTORS = (
    "seasonality", "pricing", "competition", "inventory", "listing changes",
    "advertising changes made outside the tracker", "reporting delays", "attribution timing",
    "marketplace conditions",
)

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WINDOW_DAYS_RE = re.compile(r"(\d+)")
_METRIC_STATE_WINDOW_RE = re.compile(r"_(\d+)d$")

_TSV_CELL = DASH._tsv_cell   # reuse the accepted Phase 7.4/7.5/7.6 formula-injection rule verbatim


class FollowupError(Exception):
    """A structural failure that blocks the follow-up run. Carries a machine-readable code."""

    def __init__(self, code, detail=""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


# ================================================================ small helpers
def _valid_date(v):
    if not isinstance(v, str) or not _DATE_RE.match(v):
        return False
    try:
        _dt.date.fromisoformat(v)
        return True
    except ValueError:
        return False


def _date(v):
    return _dt.date.fromisoformat(v)


def _s(v):
    return "" if v is None else str(v)


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def _read_bytes_bounded(path, what):
    size = os.path.getsize(path)
    if size > MAX_ARTIFACT_BYTES:
        raise FollowupError(f"{what}_OVERSIZE", str(size))
    with open(path, "rb") as f:
        data = f.read()
    if b"\x00" in data:
        raise FollowupError(f"{what}_NULL_BYTE", os.path.basename(path))
    return data


def _write_bytes(path, data):
    with open(path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


# ================================================================ windows & policy
def validate_windows(reference_date, before_start, before_end, after_start, after_end):
    """Return a list of reason dicts (empty == the windows are usable). All windows are explicit and
    deterministic; the reference date is never defaulted to the current date."""
    reasons = []
    labelled = (("reference_date", reference_date), ("before_period_start", before_start),
                ("before_period_end", before_end), ("after_period_start", after_start),
                ("after_period_end", after_end))
    for name, val in labelled:
        if not _valid_date(val):
            reasons.append({"code": "INVALID_WINDOW_DATE", "detail": f"{name}={val!r}"})
    if reasons:
        return reasons
    bs, be, as_, ae, ref = (_date(before_start), _date(before_end), _date(after_start),
                            _date(after_end), _date(reference_date))
    if be < bs:
        reasons.append({"code": "WINDOW_REVERSED", "detail": "before_period_end precedes before_period_start"})
    if ae < as_:
        reasons.append({"code": "WINDOW_REVERSED", "detail": "after_period_end precedes after_period_start"})
    if not reasons and be >= as_:
        reasons.append({"code": "WINDOW_OVERLAP",
                        "detail": "before window must end strictly before the after window begins"})
    if ae > ref:
        reasons.append({"code": "WINDOW_END_AFTER_REFERENCE",
                        "detail": "after_period_end is later than the reference date (not yet observable)"})
    return reasons


def load_policy(policy_file):
    """Load the classification policy. Returns (policy_dict, reasons). A null material_change_ratio is
    an explicit owner decision to withhold a threshold -> POLICY_REQUIRED (never invented)."""
    if policy_file is None:
        return dict(DEFAULT_POLICY), []
    if not os.path.isfile(policy_file):
        raise FollowupError("POLICY_FILE_MISSING", policy_file)
    data = _read_bytes_bounded(policy_file, "POLICY")
    try:
        doc = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise FollowupError("POLICY_MALFORMED", str(e)) from e
    if not isinstance(doc, dict):
        raise FollowupError("POLICY_MALFORMED", "root is not an object")
    policy = dict(DEFAULT_POLICY)
    policy.update(doc)
    policy["schema_version"] = POLICY_SCHEMA
    reasons = []
    ratio = policy.get("material_change_ratio")
    if ratio is None:
        reasons.append({"code": "POLICY_THRESHOLD_REQUIRED",
                        "detail": "material_change_ratio is null; owner policy must set it"})
    else:
        try:
            MONEY.parse_nonnegative_decimal(ratio, field="material_change_ratio", allow_missing=False)
        except MONEY.MoneyError as e:
            raise FollowupError("POLICY_INVALID_THRESHOLD", str(e)) from e
    return policy, reasons


# ================================================================ Phase 7.3 source (read-only, verified)
class Source:
    __slots__ = ("promoted_dir", "manifest", "manifest_bytes_sha256", "identity_sha256",
                 "analysis_sha256", "rows")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def _promoted_dir(phase7_3_dir):
    base = os.path.abspath(phase7_3_dir)
    cand = os.path.join(base, PHASE7_3_PROMOTED)
    if os.path.isfile(os.path.join(cand, PHASE7_3_MANIFEST_FILE)):
        return cand
    # allow pointing directly at a promoted/ directory
    if os.path.isfile(os.path.join(base, PHASE7_3_MANIFEST_FILE)):
        return base
    return cand


def load_source(phase7_3_dir):
    """Load + integrity-verify the Phase 7.3 promoted analysis. Raises FollowupError (SOURCE_*)."""
    pdir = _promoted_dir(phase7_3_dir)
    mpath = os.path.join(pdir, PHASE7_3_MANIFEST_FILE)
    apath = os.path.join(pdir, PHASE7_3_ANALYSIS_FILE)
    if not os.path.isfile(mpath) or not os.path.isfile(apath):
        raise FollowupError("SOURCE_NOT_FOUND", pdir)
    mbytes = _read_bytes_bounded(mpath, "SOURCE_MANIFEST")
    try:
        manifest = json.loads(mbytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise FollowupError("SOURCE_MANIFEST_MALFORMED", str(e)) from e
    if not isinstance(manifest, dict) or manifest.get("schema_version") != PHASE7_3_MANIFEST_SCHEMA:
        raise FollowupError("SOURCE_MANIFEST_SCHEMA",
                            str(manifest.get("schema_version") if isinstance(manifest, dict) else type(manifest)))
    output_hashes = manifest.get("output_hashes")
    if not isinstance(output_hashes, dict) or PHASE7_3_ANALYSIS_FILE not in output_hashes:
        raise FollowupError("SOURCE_MANIFEST_NO_HASHES", "output_hashes missing analysis.json")
    # every declared output must hash to its recorded value (detects a tampered source or manifest).
    for name in sorted(output_hashes):
        fpath = os.path.join(pdir, name)
        if not os.path.isfile(fpath):
            raise FollowupError("SOURCE_ARTIFACT_MISSING", name)
        with open(fpath, "rb") as f:
            if _sha256_hex(f.read()) != output_hashes[name]:
                raise FollowupError("SOURCE_HASH_MISMATCH", name)
    abytes = _read_bytes_bounded(apath, "SOURCE_ANALYSIS")
    try:
        analysis = json.loads(abytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise FollowupError("SOURCE_ANALYSIS_MALFORMED", str(e)) from e
    rows = analysis.get("search_term_analysis") if isinstance(analysis, dict) else None
    if not isinstance(rows, list):
        raise FollowupError("SOURCE_ANALYSIS_NO_ROWS", "search_term_analysis is not a list")
    return Source(promoted_dir=pdir, manifest=manifest, manifest_bytes_sha256=_sha256_hex(mbytes),
                  identity_sha256=manifest.get("deterministic_content_sha256"),
                  analysis_sha256=output_hashes[PHASE7_3_ANALYSIS_FILE], rows=rows)


# ================================================================ entity identity & matching
def _row_marketplace(row):
    key = row.get("canonical_row_key") or ""
    m = re.search(r"\|mk=([^|]*)\|", key)
    return m.group(1) if m else None


def _row_attribution_window(row):
    """Derive the attribution window (int days) from a Phase 7.3 row's evidence metric-state suffixes
    (e.g. orders_7d -> 7). Returns a set of ints (usually one)."""
    ev = row.get("evidence")
    states = ev.get("metric_states") if isinstance(ev, dict) else None
    windows = set()
    if isinstance(states, dict):
        for k in states:
            m = _METRIC_STATE_WINDOW_RE.search(k)
            if m:
                windows.add(int(m.group(1)))
    return windows


def _normalize_window(value):
    """Normalize an attribution window to an int number of days ('7 day' -> 7, 7 -> 7)."""
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    m = _WINDOW_DAYS_RE.search(str(value))
    return int(m.group(1)) if m else None


def _match_key_from_snapshot(snap):
    """The stable, name-independent entity identity used to match a tracked action to Phase 7.3 rows.
    Currency, attribution window, and marketplace are deliberately NOT part of the match key — they
    are validated as consistency dimensions on the matched set so a currency/attribution/marketplace
    ambiguity is caught, never silently collapsed. Never uses row number, sort order, temporary id,
    uuid, or timestamp."""
    return {
        "campaign": snap.get("campaign"),
        "ad_group": snap.get("ad_group"),
        "targeting": snap.get("target"),
        "customer_search_term": snap.get("customer_search_term"),
        "match_type": snap.get("match_type"),
    }


def _match_key_from_row(row):
    return {
        "campaign": row.get("campaign"),
        "ad_group": row.get("ad_group"),
        "targeting": row.get("targeting"),
        "customer_search_term": row.get("customer_search_term"),
        "match_type": row.get("match_type"),
    }


def _identity_complete(match_key):
    # a safe match requires the discriminating dimensions; match_type may legitimately be null.
    return bool(match_key.get("campaign")) and bool(match_key.get("customer_search_term"))


def match_rows(match_key, rows):
    """Return rows whose entity identity equals the given match key (exact, None-aware)."""
    return [r for r in rows if _match_key_from_row(r) == match_key]


# ================================================================ metric aggregation (Decimal, no float)
def _aggregate(rows):
    """Aggregate a set of same-entity rows into one metric bundle. A metric that is missing on ANY
    contributing row stays MISSING for the aggregate (never coerced to zero). Ratios are recomputed
    from the summed bases via Decimal safe-division (zero denominator -> missing)."""
    out = {}
    # counts
    for f in _COUNT_METRICS:
        vals = [r.get(f) for r in rows]
        if any(v is None for v in vals):
            out[f] = None
        else:
            out[f] = sum(MONEY.parse_nonnegative_int(v, field=f, allow_missing=False) for v in vals)
    # money
    for f in _MONEY_METRICS:
        vals = [r.get(f) for r in rows]
        if any(v is None for v in vals):
            out[f] = None
        else:
            parsed = [MONEY.parse_decimal_string(v, field=f, allow_missing=False) for v in vals]
            out[f] = MONEY.sum_required_decimals(parsed, field=f)
    # derived (recomputed from summed bases)
    clicks = _as_decimal(out.get("clicks"))
    impressions = _as_decimal(out.get("impressions"))
    orders = _as_decimal(out.get("orders"))
    spend = out.get("spend")
    sales = out.get("sales")
    out["cpc"] = MONEY.safe_divide(spend, clicks)
    out["ctr"] = MONEY.safe_divide(clicks, impressions)
    out["conversion_rate"] = MONEY.safe_divide(orders, clicks)
    out["acos"] = MONEY.safe_divide(spend, sales)
    out["roas"] = MONEY.safe_divide(sales, spend)
    return out


def _as_decimal(v):
    if v is None:
        return None
    if isinstance(v, int) and not isinstance(v, bool):
        return MONEY.parse_decimal_string(v, allow_missing=False)
    return v


def _serialize_metric(field, value):
    if value is None:
        return None
    if field in _COUNT_METRICS:
        return int(value)
    if field in _CURRENCY_SERIALIZED:
        return MONEY.serialize_currency(value)
    if field in _RATE_SERIALIZED:
        return MONEY.serialize_rate(value)
    return MONEY.decimal_to_canonical_string(value)


def _serialize_bundle(bundle):
    return {f: _serialize_metric(f, bundle.get(f)) for f in _ALL_METRICS}


# ================================================================ deltas (Decimal, no float)
def _abs_delta(field, before, after):
    if before is None or after is None:
        return None
    b, a = _as_decimal(before), _as_decimal(after)
    d = a - b
    if field in _COUNT_METRICS:
        return int(d)
    if field in _RATE_SERIALIZED:
        return MONEY.serialize_rate(d)
    return MONEY.serialize_currency(d)


def _pct_delta(before, after):
    if before is None or after is None:
        return None
    b, a = _as_decimal(before), _as_decimal(after)
    if b == 0:
        return None  # zero-denominator: percentage is undefined (never fabricated / never inf)
    ratio = MONEY.safe_divide((a - b) * MONEY.Decimal(100), abs(b))
    if ratio is None:
        return None
    return MONEY.decimal_to_canonical_string(ratio.quantize(MONEY.Decimal("0.0001"),
                                                             rounding=MONEY.ROUND_HALF_UP))


def _deltas(before, after):
    absd, pctd = {}, {}
    for f in _ALL_METRICS:
        absd[f] = _abs_delta(f, before.get(f), after.get(f))
        pctd[f] = _pct_delta(before.get(f), after.get(f))
    return absd, pctd


# ================================================================ classification (observation only)
def _confidence(before, after, policy):
    min_b = int(MONEY.parse_nonnegative_int(policy.get("minimum_before_clicks", "0"),
                                            field="minimum_before_clicks", allow_missing=False) or 0)
    min_a = int(MONEY.parse_nonnegative_int(policy.get("minimum_after_clicks", "0"),
                                            field="minimum_after_clicks", allow_missing=False) or 0)
    bc, ac = before.get("clicks"), after.get("clicks")
    bs, as_ = before.get("spend"), after.get("spend")
    if bc is None or ac is None or bs is None or as_ is None:
        return INSUFFICIENT_OBSERVATION
    if int(bc) >= min_b and int(ac) >= min_a:
        return SUFFICIENT_OBSERVATION
    return LIMITED_OBSERVATION


def _direction_signal(field, direction, before, after, ratio):
    """Return 'improved' | 'declined' | 'flat' | None (not computable) for one directional metric."""
    b, a = before.get(field), after.get(field)
    if b is None or a is None:
        return None
    b, a = _as_decimal(b), _as_decimal(a)
    if b == 0 and a == 0:
        return "flat"
    if b == 0:
        better = a > 0 if direction == "higher_better" else a < 0
        return "improved" if better else "declined"
    rel = MONEY.safe_divide(abs(a - b), abs(b))
    if rel is None or rel < ratio:
        return "flat"
    increased = a > b
    if direction == "higher_better":
        return "improved" if increased else "declined"
    return "improved" if not increased else "declined"


def classify(before, after, policy, *, before_empty, after_empty):
    """Conservative, observation-only classification. Returns (classification, reason, detail)."""
    if before_empty or after_empty:
        which = "before" if before_empty else "after"
        return INSUFFICIENT_DATA, "WINDOW_EMPTY", f"{which} window has no matched rows"
    conf = _confidence(before, after, policy)
    if conf == INSUFFICIENT_OBSERVATION:
        return INSUFFICIENT_DATA, "CORE_METRIC_MISSING", "clicks or spend missing in a window"
    ratio = MONEY.parse_nonnegative_decimal(policy["material_change_ratio"],
                                            field="material_change_ratio", allow_missing=False)
    directional = policy.get("directional_metrics") or {}
    improved, declined, computable = [], [], 0
    for field in sorted(directional):
        sig = _direction_signal(field, directional[field], before, after, ratio)
        if sig is None:
            continue
        computable += 1
        if sig == "improved":
            improved.append(field)
        elif sig == "declined":
            declined.append(field)
    if computable == 0:
        return INSUFFICIENT_DATA, "NO_DIRECTIONAL_METRIC", "no directional metric computable in both windows"
    if improved and declined:
        return OBSERVED_MIXED, "MIXED_SIGNALS", f"improved={improved} declined={declined}"
    if improved:
        return OBSERVED_IMPROVEMENT, "IMPROVED", f"improved={improved}"
    if declined:
        return OBSERVED_DECLINE, "DECLINED", f"declined={declined}"
    return OBSERVED_NO_MATERIAL_CHANGE, "WITHIN_THRESHOLD", "all directional metrics within threshold"


# ================================================================ follow-up record identity
def followup_record_id(tracker_record_id, tracker_content_sha, identity, before_period, after_period,
                       currency, attribution_window, source_identity_sha):
    ident = {
        "kind": "phase7-7-followup-record-id",
        "tracker_record_id": _s(tracker_record_id),
        "tracker_record_content_sha256": _s(tracker_content_sha),
        "entity_identity": identity,
        "before_period": before_period,
        "after_period": after_period,
        "currency": _s(currency),
        "attribution_window": attribution_window,
        "source_identity_sha256": _s(source_identity_sha),
    }
    return "fu-" + content_sha256(ident)[:32]


def _canonical_identity(identity, before_period, after_period, currency, attribution_window, marketplace):
    """The dedup key: two records collapse only when the OBSERVATION is over the same entity, windows,
    currency, attribution window, and marketplace. Never collapses across any of those."""
    ident = {
        "entity_identity": identity, "before_period": before_period, "after_period": after_period,
        "currency": _s(currency), "attribution_window": attribution_window, "marketplace": _s(marketplace),
    }
    return content_sha256(ident)


# runtime/lineage-only fields excluded from a follow-up's observation content hash.
_LINEAGE_FIELDS = ("followup_record_id", "followup_content_sha256", "tracker_record_id",
                   "tracker_record_content_sha256", "package_id", "package_item_id",
                   "source_recommendation_id", "duplicate_of_count", "duplicate_tracker_record_ids")


def _followup_content_hash(record):
    stable = {k: v for k, v in record.items() if k not in _LINEAGE_FIELDS}
    return content_sha256(stable)


# ================================================================ per-record follow-up construction
def _excluded(rec, classification, reason, detail, *, binding_state=None, marketplace=None):
    snap = rec.get("source_snapshot") or {}
    return {
        "tracker_record_id": rec.get("tracker_record_id"),
        "package_id": rec.get("package_id"),
        "package_item_id": rec.get("package_item_id"),
        "recommendation_type": rec.get("recommendation_type"),
        "entity_type": rec.get("entity_type"),
        "owner_status": rec.get("owner_status"),
        "owner_completed_date": rec.get("owner_completed_date"),
        "campaign": snap.get("campaign"),
        "ad_group": snap.get("ad_group"),
        "target": snap.get("target"),
        "customer_search_term": snap.get("customer_search_term"),
        "match_type": snap.get("match_type"),
        "currency": snap.get("currency"),
        "attribution_window": snap.get("attribution_window"),
        "marketplace": marketplace,
        "binding_state": binding_state,
        "outcome_classification": classification,
        "exclusion_reason": reason,
        "exclusion_detail": detail,
    }


def build_followup(rec, source, windows, policy, *, binding_state, source_pinned_sha,
                   require_source_lineage_match, minimum_followup_days):
    """Evaluate one MANUALLY_COMPLETED record. Returns ('followup', dict) or ('excluded', dict)."""
    snap = rec.get("source_snapshot") or {}
    identity = _match_key_from_snapshot(snap)
    if not _identity_complete(identity):
        return "excluded", _excluded(rec, MISSING_IDENTITY, "INCOMPLETE_IDENTITY",
                                     "campaign / customer_search_term missing", binding_state=binding_state)

    matched = match_rows(identity, source.rows)
    if not matched:
        return "excluded", _excluded(rec, ENTITY_NOT_FOUND, "NO_MATCHING_ROWS",
                                     "entity identity is absent from the current Phase 7.3 analysis",
                                     binding_state=binding_state)

    marketplaces = {_row_marketplace(r) for r in matched}
    if len(marketplaces) > 1:
        return "excluded", _excluded(rec, AMBIGUOUS_ENTITY_MATCH, "MULTIPLE_MARKETPLACES",
                                     f"identity matches multiple marketplaces: {sorted(map(_s, marketplaces))}",
                                     binding_state=binding_state)
    marketplace = next(iter(marketplaces))

    # currency must be single and unambiguous, and agree with the record's recorded currency.
    row_currencies = {r.get("currency") for r in matched}
    rec_currency = snap.get("currency")
    if len(row_currencies) > 1:
        return "excluded", _excluded(rec, CURRENCY_MISMATCH, "MULTIPLE_CURRENCIES",
                                     f"identity spans multiple currencies: {sorted(map(_s, row_currencies))}",
                                     binding_state=binding_state, marketplace=marketplace)
    row_currency = next(iter(row_currencies))
    if not row_currency:
        return "excluded", _excluded(rec, CURRENCY_MISMATCH, "AMBIGUOUS_CURRENCY",
                                     "matched rows have no currency", binding_state=binding_state,
                                     marketplace=marketplace)
    if rec_currency is not None and rec_currency != row_currency:
        return "excluded", _excluded(rec, CURRENCY_MISMATCH, "CURRENCY_MISMATCH",
                                     f"row currency {row_currency!r} vs record {rec_currency!r}",
                                     binding_state=binding_state, marketplace=marketplace)
    rec_currency = row_currency

    windows_found = set()
    for r in matched:
        windows_found |= _row_attribution_window(r)
    rec_window = _normalize_window(snap.get("attribution_window"))
    if len(windows_found) > 1:
        return "excluded", _excluded(rec, ATTRIBUTION_WINDOW_MISMATCH, "MULTIPLE_ATTRIBUTION_WINDOWS",
                                     f"rows span attribution windows {sorted(windows_found)}",
                                     binding_state=binding_state, marketplace=marketplace)
    row_window = next(iter(windows_found)) if windows_found else None
    if row_window is not None and rec_window is not None and row_window != rec_window:
        return "excluded", _excluded(rec, ATTRIBUTION_WINDOW_MISMATCH, "ATTRIBUTION_WINDOW_MISMATCH",
                                     f"record window {rec_window} vs source window {row_window}",
                                     binding_state=binding_state, marketplace=marketplace)
    attribution_window = row_window if row_window is not None else rec_window

    if require_source_lineage_match and source_pinned_sha is not None \
            and source.identity_sha256 != source_pinned_sha:
        return "excluded", _excluded(rec, SOURCE_CHANGED, "SOURCE_LINEAGE_CHANGED",
                                     "current Phase 7.3 identity differs from the package's pinned source",
                                     binding_state=binding_state, marketplace=marketplace)

    completed = rec.get("owner_completed_date")
    if not _valid_date(completed):
        return "excluded", _excluded(rec, NOT_ELIGIBLE_STATUS, "MISSING_COMPLETED_DATE",
                                     "MANUALLY_COMPLETED record has no owner_completed_date",
                                     binding_state=binding_state, marketplace=marketplace)
    bs, be, as_, ae = windows
    if minimum_followup_days > 0 and (_date(as_) - _date(completed)).days < minimum_followup_days:
        return "excluded", _excluded(rec, WINDOW_NOT_READY_CLASS, "MINIMUM_FOLLOWUP_NOT_ELAPSED",
                                     f"after window opens < {minimum_followup_days} days after the action",
                                     binding_state=binding_state, marketplace=marketplace)

    before_rows = [r for r in matched if _row_in_window(r, bs, be)]
    after_rows = [r for r in matched if _row_in_window(r, as_, ae)]
    before = _aggregate(before_rows)
    after = _aggregate(after_rows)
    classification, reason, detail = classify(before, after, policy,
                                              before_empty=not before_rows, after_empty=not after_rows)
    confidence = _confidence(before, after, policy) if (before_rows and after_rows) else INSUFFICIENT_OBSERVATION
    absd, pctd = _deltas(before, after)

    before_period = {"start": bs, "end": be}
    after_period = {"start": as_, "end": ae}
    record = {
        "schema_version": FOLLOWUP_SCHEMA,
        "followup_record_id": None,
        "tracker_record_id": rec.get("tracker_record_id"),
        "tracker_record_content_sha256": rec.get("record_content_sha256"),
        "package_id": rec.get("package_id"),
        "package_item_id": rec.get("package_item_id"),
        "source_recommendation_id": rec.get("source_recommendation_id"),
        "recommendation_type": rec.get("recommendation_type"),
        "entity_type": rec.get("entity_type"),
        "owner_status": rec.get("owner_status"),
        "owner_completed_date": completed,
        "entity_identity": identity,
        "marketplace": marketplace,
        "currency": rec_currency,
        "attribution_window": attribution_window,
        "before_period": before_period,
        "after_period": after_period,
        "before_row_count": len(before_rows),
        "after_row_count": len(after_rows),
        "before_metrics": _serialize_bundle(before),
        "after_metrics": _serialize_bundle(after),
        "absolute_deltas": {f: absd[f] for f in _ALL_METRICS},
        "percentage_deltas": {f: pctd[f] for f in _ALL_METRICS},
        "outcome_classification": classification,
        "outcome_reason": reason,
        "outcome_detail": detail,
        "outcome_statement": CLASSIFICATION_PHRASING[classification],
        "confidence_status": confidence,
        "evidence_status": confidence,
        "binding_state": binding_state,
        "source_identity_sha256": source.identity_sha256,
        "duplicate_of_count": 0,
        "duplicate_tracker_record_ids": [],
    }
    record["_canonical_identity"] = _canonical_identity(identity, before_period, after_period,
                                                        rec_currency, attribution_window, marketplace)
    record["followup_record_id"] = followup_record_id(
        rec.get("tracker_record_id"), rec.get("record_content_sha256"), identity, before_period,
        after_period, rec_currency, attribution_window, source.identity_sha256)
    return "followup", record


def _row_in_window(row, start, end):
    sd, ed = row.get("start_date"), row.get("end_date")
    if not _valid_date(sd):
        return False
    ed = ed if _valid_date(ed) else sd
    return _date(start) <= _date(sd) and _date(ed) <= _date(end)


# ================================================================ dedup (canonical follow-up identity)
def dedupe(followups):
    """Collapse byte-identical duplicates; exclude conflicting duplicates entirely (never last-write-
    wins). Returns (kept, excluded, duplicate_identical_count, duplicate_conflict_count)."""
    groups = {}
    for fu in followups:
        groups.setdefault(fu["_canonical_identity"], []).append(fu)
    kept, excluded = [], []
    dup_identical = 0
    dup_conflict = 0
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda f: f["followup_record_id"])
        content_hashes = {_followup_content_hash(f) for f in members}
        if len(content_hashes) > 1:
            dup_conflict += len(members)
            for f in members:
                excluded.append(_excluded_from_followup(f, FOLLOWUP_CONFLICT_CLASS, "FOLLOWUP_CONFLICT",
                                                        f"{len(members)} conflicting follow-ups share one identity"))
            continue
        primary = members[0]
        others = members[1:]
        if others:
            dup_identical += len(others)
            primary = dict(primary)
            primary["duplicate_of_count"] = len(others)
            primary["duplicate_tracker_record_ids"] = sorted(f["tracker_record_id"] for f in others)
        kept.append(primary)
    return kept, excluded, dup_identical, dup_conflict


def _excluded_from_followup(fu, classification, reason, detail):
    identity = fu.get("entity_identity") or {}
    return {
        "tracker_record_id": fu.get("tracker_record_id"),
        "package_id": fu.get("package_id"),
        "package_item_id": fu.get("package_item_id"),
        "recommendation_type": fu.get("recommendation_type"),
        "entity_type": fu.get("entity_type"),
        "owner_status": fu.get("owner_status"),
        "owner_completed_date": fu.get("owner_completed_date"),
        "campaign": identity.get("campaign"),
        "ad_group": identity.get("ad_group"),
        "target": identity.get("targeting"),
        "customer_search_term": identity.get("customer_search_term"),
        "match_type": identity.get("match_type"),
        "currency": fu.get("currency"),
        "attribution_window": fu.get("attribution_window"),
        "marketplace": fu.get("marketplace"),
        "binding_state": fu.get("binding_state"),
        "outcome_classification": classification,
        "exclusion_reason": reason,
        "exclusion_detail": detail,
    }


# ================================================================ model assembly
class Model:
    __slots__ = ("followups", "excluded", "reverted", "counts", "duplicate_identical",
                 "duplicate_conflict", "readiness", "reasons", "package_id", "windows", "policy",
                 "source", "tracker_state_sha256", "reference_date", "minimum_followup_days")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def build_model(base_dir, phase7_3_dir, phase7_6_dir, phase7_5_dir, reference_date, windows, *,
                policy_file=None, minimum_followup_days=0, require_source_lineage_match=False):
    """Validate every input, resolve every follow-up in memory, and decide readiness — WITHOUT writing
    anything. Raises FollowupError for a structural/blocking condition; otherwise returns a Model."""
    # ---- windows -------------------------------------------------------------------------------
    wreasons = validate_windows(reference_date, *windows)
    if wreasons:
        raise FollowupError("WINDOW_NOT_READY", "; ".join(f"{r['code']}: {r['detail']}" for r in wreasons))

    # ---- policy --------------------------------------------------------------------------------
    policy, preasons = load_policy(policy_file)
    if preasons:
        raise FollowupError("POLICY_REQUIRED", "; ".join(f"{r['code']}: {r['detail']}" for r in preasons))

    # ---- tracker (Phase 7.6) -------------------------------------------------------------------
    if not os.path.isdir(os.path.abspath(phase7_6_dir)):
        raise FollowupError("TRACKER_REQUIRED", f"Phase 7.6 workspace not found: {phase7_6_dir}")
    try:
        loaded = TRK.load_state(phase7_6_dir)
    except TRK.TrackerError as e:
        raise FollowupError("TRACKER_BLOCKED", f"{e.code}: {e.detail}") from e
    records = loaded["records"]
    tracker_state_sha = (loaded["state"]["state_content_sha256"] if loaded["exists"] else
                         content_sha256({}))

    # ---- source (Phase 7.3) --------------------------------------------------------------------
    source = load_source(phase7_3_dir)

    followups, excluded, reverted = [], [], []
    for rid in sorted(records):
        rec = records[rid]
        status = rec.get("owner_status")
        if status == TRK.S_REVERTED:
            reverted.append(_excluded(rec, REVERTED_SEPARATE, "REVERTED_MANUALLY",
                                      "owner recorded a manual reversion; tracked as a separate lifecycle event"))
            continue
        if status != TRK.S_COMPLETED:
            excluded.append(_excluded(rec, NOT_ELIGIBLE_STATUS, "OWNER_STATUS_NOT_ELIGIBLE",
                                      f"owner_status={status} is not a completed manual action"))
            continue
        b_state, _b_detail = TRK.binding_state(rec, phase7_5_dir)
        if b_state in TRK._STALE_BINDINGS:
            excluded.append(_excluded(rec, PACKAGE_CHANGED, "PACKAGE_BINDING_STALE",
                                      f"package binding is {b_state}", binding_state=b_state))
            continue
        pinned = _package_pinned_source_sha(rec, phase7_5_dir)
        kind, obj = build_followup(rec, source, windows, policy, binding_state=b_state,
                                   source_pinned_sha=pinned,
                                   require_source_lineage_match=require_source_lineage_match,
                                   minimum_followup_days=minimum_followup_days)
        (followups if kind == "followup" else excluded).append(obj)

    kept, dup_excluded, dup_identical, dup_conflict = dedupe(followups)
    excluded.extend(dup_excluded)

    package_id = _package_id(kept, excluded, reverted, source, tracker_state_sha, reference_date,
                             windows, policy, minimum_followup_days)
    readiness = _decide_readiness(kept, excluded, dup_conflict)
    counts = {
        "tracked_record_count": len(records),
        "eligible_followup_count": len(kept),
        "excluded_count": len(excluded),
        "reverted_count": len(reverted),
        "duplicate_identical_count": dup_identical,
        "duplicate_conflict_count": dup_conflict,
    }
    for cls in _OUTCOME_CLASSES:
        counts[f"classified_{cls.lower()}"] = sum(1 for f in kept if f["outcome_classification"] == cls)
    return Model(followups=kept, excluded=excluded, reverted=reverted, counts=counts,
                 duplicate_identical=dup_identical, duplicate_conflict=dup_conflict,
                 readiness=readiness, reasons=[], package_id=package_id, windows=windows,
                 policy=policy, source=source, tracker_state_sha256=tracker_state_sha,
                 reference_date=reference_date, minimum_followup_days=minimum_followup_days)


def _package_pinned_source_sha(rec, phase7_5_dir):
    pkg = TRK.load_package(phase7_5_dir, package_id=rec.get("package_id"),
                           dir_name=rec.get("package_dir_name"))
    if pkg is None or not isinstance(pkg.manifest, dict):
        return None
    return pkg.manifest.get("phase7_3_manifest_sha256")


def _decide_readiness(kept, excluded, dup_conflict):
    if kept:
        return FOLLOWUP_READY
    if dup_conflict > 0:
        return FOLLOWUP_CONFLICT
    if excluded:
        classes = {e["outcome_classification"] for e in excluded}
        if classes and classes <= set(_ENTITY_EXCLUSIONS):
            return ENTITY_MATCH_REQUIRED
        if classes and classes <= {WINDOW_NOT_READY_CLASS}:
            return WINDOW_NOT_READY
    return FOLLOWUP_READY_EMPTY


def _package_id(kept, excluded, reverted, source, tracker_state_sha, reference_date, windows, policy,
                minimum_followup_days):
    ident = {
        "kind": "phase7-7-followup-package-id",
        "generator_version": GENERATOR_VERSION,
        "reference_date": reference_date,
        "windows": {"before_start": windows[0], "before_end": windows[1],
                    "after_start": windows[2], "after_end": windows[3]},
        "minimum_followup_days": minimum_followup_days,
        "policy_id": policy.get("policy_id"),
        "material_change_ratio": policy.get("material_change_ratio"),
        "source_identity_sha256": source.identity_sha256,
        "tracker_state_sha256": tracker_state_sha,
        "followup_content_hashes": sorted(_followup_content_hash(f) for f in kept),
        "followup_record_ids": sorted(f["followup_record_id"] for f in kept),
        "excluded_record_ids": sorted(_s(e["tracker_record_id"]) + "|" + _s(e["exclusion_reason"])
                                      for e in excluded),
        "reverted_record_ids": sorted(_s(r["tracker_record_id"]) for r in reverted),
    }
    return "followup-" + content_sha256(ident)[:16]


# ================================================================ rendering (deterministic, local)
def _disclaimer_md(prefix="> "):
    return "\n".join(prefix + line for line in DISCLAIMER_LINES)


def _disclaimer_hash_comment():
    return "\n".join("# " + line for line in DISCLAIMER_LINES)


OUTCOME_COLUMNS = ("followup_record_id", "tracker_record_id", "package_id", "package_item_id",
                   "recommendation_type", "entity_type", "campaign", "ad_group", "target",
                   "customer_search_term", "match_type", "currency", "attribution_window",
                   "marketplace", "owner_completed_date", "before_period_start", "before_period_end",
                   "after_period_start", "after_period_end", "outcome_classification",
                   "confidence_status", "before_spend", "after_spend", "before_sales", "after_sales",
                   "before_orders", "after_orders", "before_acos", "after_acos",
                   "spend_abs_delta", "sales_abs_delta", "orders_abs_delta", "acos_abs_delta",
                   "sales_pct_delta", "duplicate_of_count", "followup_content_sha256")

EXCLUDED_COLUMNS = ("tracker_record_id", "package_id", "package_item_id", "recommendation_type",
                    "entity_type", "owner_status", "owner_completed_date", "campaign", "ad_group",
                    "target", "customer_search_term", "match_type", "currency", "attribution_window",
                    "marketplace", "binding_state", "outcome_classification", "exclusion_reason",
                    "exclusion_detail")


def _outcome_row(fu):
    b, a = fu["before_metrics"], fu["after_metrics"]
    absd, pctd = fu["absolute_deltas"], fu["percentage_deltas"]
    return {
        "followup_record_id": fu["followup_record_id"],
        "tracker_record_id": fu["tracker_record_id"],
        "package_id": fu["package_id"],
        "package_item_id": fu["package_item_id"],
        "recommendation_type": fu["recommendation_type"],
        "entity_type": fu["entity_type"],
        "campaign": fu["entity_identity"].get("campaign"),
        "ad_group": fu["entity_identity"].get("ad_group"),
        "target": fu["entity_identity"].get("targeting"),
        "customer_search_term": fu["entity_identity"].get("customer_search_term"),
        "match_type": fu["entity_identity"].get("match_type"),
        "currency": fu["currency"],
        "attribution_window": fu["attribution_window"],
        "marketplace": fu["marketplace"],
        "owner_completed_date": fu["owner_completed_date"],
        "before_period_start": fu["before_period"]["start"],
        "before_period_end": fu["before_period"]["end"],
        "after_period_start": fu["after_period"]["start"],
        "after_period_end": fu["after_period"]["end"],
        "outcome_classification": fu["outcome_classification"],
        "confidence_status": fu["confidence_status"],
        "before_spend": b["spend"], "after_spend": a["spend"],
        "before_sales": b["sales"], "after_sales": a["sales"],
        "before_orders": b["orders"], "after_orders": a["orders"],
        "before_acos": b["acos"], "after_acos": a["acos"],
        "spend_abs_delta": absd["spend"], "sales_abs_delta": absd["sales"],
        "orders_abs_delta": absd["orders"], "acos_abs_delta": absd["acos"],
        "sales_pct_delta": pctd["sales"],
        "duplicate_of_count": fu["duplicate_of_count"],
        "followup_content_sha256": _followup_content_hash(fu),
    }


def _tsv(columns, rows):
    lines = ["\t".join(columns)]
    for r in rows:
        lines.append("\t".join(_TSV_CELL(r.get(c)) for c in columns))
    return "\n".join(lines) + "\n"


def _render_outcome_tsv(followups):
    rows = [_outcome_row(f) for f in followups]
    return _disclaimer_hash_comment() + "\n" + _tsv(OUTCOME_COLUMNS, rows)


def _render_outcome_json(model):
    meta = _meta(model)
    followups = []
    for fu in model.followups:
        clean = {k: v for k, v in fu.items() if not k.startswith("_")}
        clean["followup_content_sha256"] = _followup_content_hash(fu)
        followups.append(clean)
    return canonical_json({"meta": meta, "followups": followups}) + "\n"


def _render_excluded_tsv(excluded):
    return _disclaimer_hash_comment() + "\n" + _tsv(EXCLUDED_COLUMNS, excluded)


def _render_excluded_json(model):
    return canonical_json({
        "meta": {"schema_version": EXCLUSIONS_SCHEMA, "stage_id": STAGE_ID,
                 "generator_version": GENERATOR_VERSION, "reference_date": model.reference_date,
                 "excluded_count": len(model.excluded), "reverted_count": len(model.reverted),
                 "manual_action_disclaimer": list(DISCLAIMER_LINES)},
        "excluded_followups": model.excluded,
        "reverted_records": model.reverted,
    }) + "\n"


def _meta(model):
    bs, be, as_, ae = model.windows
    return {
        "schema_version": STATUS_EXPORT_SCHEMA,
        "stage_id": STAGE_ID,
        "generator_version": GENERATOR_VERSION,
        "followup_package_id": model.package_id,
        "readiness": model.readiness,
        "reference_date": model.reference_date,
        "before_period": {"start": bs, "end": be},
        "after_period": {"start": as_, "end": ae},
        "minimum_followup_days": model.minimum_followup_days,
        "attribution_note": "Before and after windows are compared only within one currency and one "
                            "attribution window; neither is ever combined.",
        "policy_id": model.policy.get("policy_id"),
        "policy_source": model.policy.get("source"),
        "material_change_ratio": model.policy.get("material_change_ratio"),
        "counts": model.counts,
        "amazon_action_performed": False,
        "causation_asserted": False,
        "manual_action_disclaimer": list(DISCLAIMER_LINES),
        "other_factors": list(OTHER_FACTORS),
    }


def _render_source_lineage(model):
    src = model.source
    return canonical_json({
        "schema_version": LINEAGE_SCHEMA,
        "stage_id": STAGE_ID,
        "reference_date": model.reference_date,
        "phase7_3": {
            "promoted_dir_type": PHASE7_3_PROMOTED,
            "analysis_identity_sha256": src.identity_sha256,
            "analysis_manifest_bytes_sha256": src.manifest_bytes_sha256,
            "analysis_json_sha256": src.analysis_sha256,
            "search_term_row_count": len(src.rows),
        },
        "phase7_6": {
            "tracker_state_content_sha256": model.tracker_state_sha256,
            "tracked_record_count": model.counts["tracked_record_count"],
        },
        "windows": {"before_start": model.windows[0], "before_end": model.windows[1],
                    "after_start": model.windows[2], "after_end": model.windows[3]},
        "policy": {"policy_id": model.policy.get("policy_id"), "source": model.policy.get("source"),
                   "material_change_ratio": model.policy.get("material_change_ratio")},
        "manual_action_disclaimer": list(DISCLAIMER_LINES),
    }) + "\n"


def _render_owner_read_first(model):
    c = model.counts
    out = [
        "# Outcome Follow-up — OFFLINE OBSERVATIONAL FOLLOW-UP",
        "", _disclaimer_md(), "",
        "This package documents observed before-and-after performance after an owner-recorded manual "
        "action. It is measurement and documentation only.",
        "",
        "## What this is NOT",
        "",
        "- It does not claim the owner-recorded action caused any change.",
        "- No Amazon connection was made and no Amazon action was performed by this software.",
        "- It is not an Amazon upload template and contains no API payload.",
        "",
        "## Other factors that may explain any observed change",
        "",
    ]
    out += [f"- {f}" for f in OTHER_FACTORS]
    out += [
        "",
        "## Summary",
        "",
        f"- Follow-up package id: `{model.package_id}`",
        f"- Readiness: {model.readiness}",
        f"- Reference date: {model.reference_date}",
        f"- Before window: {model.windows[0]} to {model.windows[1]}",
        f"- After window: {model.windows[2]} to {model.windows[3]}",
        f"- Tracked records: {c['tracked_record_count']}",
        f"- Documented follow-ups: {c['eligible_followup_count']}",
        f"- Excluded: {c['excluded_count']}",
        f"- Reverted (separate lifecycle): {c['reverted_count']}",
        f"- Duplicate (identical) collapsed: {c['duplicate_identical_count']}",
        f"- Duplicate (conflicting) excluded: {c['duplicate_conflict_count']}",
        "",
        "See `outcome_details.md` for each follow-up, `outcome_status.tsv`/`.json` for the table, and "
        "`excluded_followups.tsv`/`.json` for everything not documented.",
        "",
    ]
    return "\n".join(out)


def _render_executive_summary(model):
    c = model.counts
    out = ["# Outcome Follow-up — Executive Summary", "", _disclaimer_md(), "",
           f"- Follow-up package id: `{model.package_id}`",
           f"- Readiness: {model.readiness}",
           f"- Documented follow-ups: {c['eligible_followup_count']}", "",
           "## Observed classifications", ""]
    for cls in _OUTCOME_CLASSES:
        out.append(f"- {cls}: {c.get('classified_' + cls.lower(), 0)}")
    out += ["", "## Excluded and separate lifecycle", "",
            f"- Excluded follow-ups: {c['excluded_count']}",
            f"- Reverted (handled separately, never merged with a completion): {c['reverted_count']}",
            f"- Duplicate identical (collapsed): {c['duplicate_identical_count']}",
            f"- Duplicate conflicting (all excluded): {c['duplicate_conflict_count']}",
            "",
            "Every classification describes an OBSERVATION only. No causation is asserted.", ""]
    return "\n".join(out)


def _fmt(v):
    return "—" if v is None else str(v)


def _render_outcome_details(model):
    out = ["# Outcome Follow-up — Details", "", _disclaimer_md(), ""]
    if not model.followups:
        out += ["No documented follow-ups in this package.", ""]
    for i, fu in enumerate(model.followups, 1):
        ident = fu["entity_identity"]
        b, a = fu["before_metrics"], fu["after_metrics"]
        absd, pctd = fu["absolute_deltas"], fu["percentage_deltas"]
        out += [
            f"## {i}. `{fu['followup_record_id']}`", "",
            f"- {fu['outcome_statement']}",
            f"- Outcome classification: {fu['outcome_classification']} "
            f"({fu['outcome_reason']}: {fu['outcome_detail']})",
            f"- Confidence (data sufficiency only): {fu['confidence_status']}",
            f"- Tracker record: `{fu['tracker_record_id']}`  |  Package item: `{fu['package_item_id']}`",
            f"- Campaign: {_fmt(ident.get('campaign'))}  |  Ad group: {_fmt(ident.get('ad_group'))}",
            f"- Target: {_fmt(ident.get('targeting'))}  |  Search term: {_fmt(ident.get('customer_search_term'))}"
            f"  |  Match type: {_fmt(ident.get('match_type'))}",
            f"- Currency: {_fmt(fu['currency'])}  |  Attribution window: {_fmt(fu['attribution_window'])}"
            f"  |  Marketplace: {_fmt(fu['marketplace'])}",
            f"- Owner-recorded completion date: {_fmt(fu['owner_completed_date'])}",
            f"- Before window {fu['before_period']['start']}..{fu['before_period']['end']} "
            f"({fu['before_row_count']} row(s)); after window {fu['after_period']['start']}.."
            f"{fu['after_period']['end']} ({fu['after_row_count']} row(s))",
            "",
            "| Metric | Before | After | Abs delta | % delta |",
            "| --- | --- | --- | --- | --- |",
        ]
        for f in _ALL_METRICS:
            out.append(f"| {f} | {_fmt(b.get(f))} | {_fmt(a.get(f))} | {_fmt(absd.get(f))} | "
                       f"{_fmt(pctd.get(f))} |")
        if fu["duplicate_of_count"]:
            out.append("")
            out.append(f"- Collapsed {fu['duplicate_of_count']} identical duplicate follow-up(s): "
                       f"{', '.join(fu['duplicate_tracker_record_ids'])}")
        out.append("")
    return "\n".join(out)


def _render_manifest(model, artifact_sha256):
    c = model.counts
    return canonical_json({
        "schema_version": MANIFEST_SCHEMA,
        "stage_id": STAGE_ID,
        "stage_name": STAGE_NAME,
        "generator_version": GENERATOR_VERSION,
        "followup_package_id": model.package_id,
        "readiness": model.readiness,
        "reference_date": model.reference_date,
        "before_period": {"start": model.windows[0], "end": model.windows[1]},
        "after_period": {"start": model.windows[2], "end": model.windows[3]},
        "minimum_followup_days": model.minimum_followup_days,
        "policy_id": model.policy.get("policy_id"),
        "policy_source": model.policy.get("source"),
        "material_change_ratio": model.policy.get("material_change_ratio"),
        "counts": c,
        "source_identity_sha256": model.source.identity_sha256,
        "tracker_state_sha256": model.tracker_state_sha256,
        "artifact_sha256": artifact_sha256,
        "amazon_action_performed": False,
        "causation_asserted": False,
        "amazon_boundary": dict(AMAZON_COUNTERS),
        "this_session_never": dict(THIS_SESSION_NEVER),
        "manual_action_disclaimer": list(DISCLAIMER_LINES),
        "other_factors": list(OTHER_FACTORS),
    }) + "\n"


def render_all(model):
    """Render every artifact except the manifest (whose hashes depend on the others). Deterministic."""
    files = {
        "OWNER_READ_FIRST.md": _render_owner_read_first(model),
        "executive_summary.md": _render_executive_summary(model),
        "outcome_details.md": _render_outcome_details(model),
        "outcome_status.tsv": _render_outcome_tsv(model.followups),
        "outcome_status.json": _render_outcome_json(model),
        "excluded_followups.tsv": _render_excluded_tsv(model.excluded),
        "excluded_followups.json": _render_excluded_json(model),
        "source_lineage.json": _render_source_lineage(model),
    }
    return {name: text.encode("utf-8") for name, text in files.items()}


# ================================================================ atomic write + idempotency
def ensure_workspace(base_dir):
    base = os.path.abspath(base_dir)
    for sub in _WORKSPACE_SUBDIRS:
        os.makedirs(os.path.join(base, sub), exist_ok=True)
    return base


def _final_dir(base_dir, package_id):
    return os.path.join(os.path.abspath(base_dir), "followups", package_id)


def _existing_matches(final_dir, artifacts):
    """True == the on-disk package already equals what we would write (idempotent). Returns
    (matches, present). 'present' is True if the final dir exists at all."""
    if not os.path.isdir(final_dir):
        return False, False
    for name, data in artifacts.items():
        fpath = os.path.join(final_dir, name)
        if not os.path.isfile(fpath):
            return False, True
        with open(fpath, "rb") as f:
            if f.read() != data:
                return False, True
    return True, True


def _fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def commit_package(base_dir, model):
    """Atomically publish the follow-up package. Returns (outcome, final_dir). 'outcome' is one of
    'CREATED' | 'IDEMPOTENT_REUSE'. Raises FollowupError('FOLLOWUP_BLOCKED') if an existing package
    with the same id has different bytes, or if written artifacts fail post-write verification."""
    base = ensure_workspace(base_dir)
    artifacts = render_all(model)
    artifact_sha256 = {name: _sha256_hex(data) for name, data in artifacts.items()}
    manifest_bytes = _render_manifest(model, artifact_sha256).encode("utf-8")
    artifacts["followup_manifest.json"] = manifest_bytes
    artifact_sha256["followup_manifest.json"] = _sha256_hex(manifest_bytes)

    final_dir = _final_dir(base, model.package_id)
    matches, present = _existing_matches(final_dir, artifacts)
    if present and matches:
        _write_index(base, model, artifact_sha256)
        return "IDEMPOTENT_REUSE", final_dir
    if present and not matches:
        raise FollowupError("FOLLOWUP_BLOCKED",
                            f"a different package already exists at {model.package_id}; refusing to overwrite")

    tmp_dir = os.path.join(base, "followups", ".tmp-" + model.package_id)
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir, ignore_errors=True)
    os.makedirs(tmp_dir)
    try:
        for name in sorted(artifacts):
            _write_bytes(os.path.join(tmp_dir, name), artifacts[name])
        # post-write verification (read back every artifact before publishing).
        for name in sorted(artifacts):
            with open(os.path.join(tmp_dir, name), "rb") as f:
                if _sha256_hex(f.read()) != artifact_sha256[name]:
                    raise FollowupError("FOLLOWUP_BLOCKED", f"written artifact {name} failed verification")
        _fsync_dir(tmp_dir)
        os.replace(tmp_dir, final_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    _fsync_dir(os.path.dirname(final_dir))
    _write_index(base, model, artifact_sha256)
    return "CREATED", final_dir


def _write_index(base_dir, model, artifact_sha256):
    """A tiny per-package pointer under manifests/ (index only; never an authoritative artifact)."""
    idx = {
        "schema_version": "phase7-7-followup-index-v1",
        "followup_package_id": model.package_id,
        "readiness": model.readiness,
        "reference_date": model.reference_date,
        "eligible_followup_count": model.counts["eligible_followup_count"],
        "excluded_count": model.counts["excluded_count"],
        "artifact_sha256": artifact_sha256,
    }
    path = os.path.join(os.path.abspath(base_dir), "manifests", model.package_id + ".json")
    tmp = path + ".tmp"
    _write_bytes(tmp, (canonical_json(idx) + "\n").encode("utf-8"))
    os.replace(tmp, path)


# ================================================================ orchestration
def run(base_dir, phase7_3_dir, phase7_6_dir, phase7_5_dir, reference_date, windows, *,
        policy_file=None, minimum_followup_days=0, require_source_lineage_match=False,
        validate_only=False):
    """Full run. Returns a result dict (never raises for a data/validation reason — blocking
    conditions surface as readiness + reasons so the CLI can set the exit code deliberately)."""
    try:
        model = build_model(base_dir, phase7_3_dir, phase7_6_dir, phase7_5_dir, reference_date,
                            windows, policy_file=policy_file,
                            minimum_followup_days=minimum_followup_days,
                            require_source_lineage_match=require_source_lineage_match)
    except FollowupError as e:
        readiness = _readiness_for_error(e.code)
        return _result(readiness, reasons=[{"code": e.code, "detail": e.detail}],
                       package_id=None, output_dir=None, outcome="NONE", validate_only=validate_only)

    if validate_only:
        return _result(model.readiness, package_id=model.package_id, output_dir=None,
                       outcome="VALIDATED", counts=model.counts, validate_only=True)

    try:
        outcome, final_dir = commit_package(base_dir, model)
    except FollowupError as e:
        return _result(FOLLOWUP_BLOCKED, reasons=[{"code": e.code, "detail": e.detail}],
                       package_id=model.package_id, output_dir=None, outcome="NONE",
                       counts=model.counts)
    return _result(model.readiness, package_id=model.package_id, output_dir=final_dir,
                   outcome=outcome, counts=model.counts, output_files=list(PACKAGE_FILES))


def _readiness_for_error(code):
    return {
        "WINDOW_NOT_READY": WINDOW_NOT_READY,
        "POLICY_REQUIRED": POLICY_REQUIRED,
        "POLICY_FILE_MISSING": POLICY_REQUIRED,
        "POLICY_MALFORMED": POLICY_REQUIRED,
        "POLICY_INVALID_THRESHOLD": POLICY_REQUIRED,
        "TRACKER_REQUIRED": TRACKER_REQUIRED,
        "TRACKER_BLOCKED": TRACKER_BLOCKED,
    }.get(code, SOURCE_BLOCKED if code.startswith("SOURCE") and code != "SOURCE_NOT_FOUND"
          else SOURCE_REQUIRED if code == "SOURCE_NOT_FOUND" else FOLLOWUP_BLOCKED)


def _result(readiness, **kw):
    res = {
        "readiness": readiness,
        "reasons": kw.pop("reasons", []),
        "amazon_counters": dict(AMAZON_COUNTERS),
    }
    res.update(kw)
    return res


def exit_code_for(readiness):
    return 0 if readiness in _OK_READINESS else 2


# ================================================================ CLI
def _summary_pairs(result):
    ac = result["amazon_counters"]
    pairs = [
        ("followup_readiness", result.get("readiness")),
        ("followup_package_id", result.get("package_id")),
        ("output_dir", result.get("output_dir")),
        ("outcome", result.get("outcome")),
    ]
    counts = result.get("counts") or {}
    for k in ("tracked_record_count", "eligible_followup_count", "excluded_count", "reverted_count",
              "duplicate_identical_count", "duplicate_conflict_count"):
        if k in counts:
            pairs.append((k, counts[k]))
    for r in result.get("reasons", []):
        pairs.append(("reason", f"{r['code']}: {r['detail']}"))
    pairs += [
        ("amazon_connections", ac["amazon_connections"]),
        ("amazon_mutations", ac["amazon_mutations"]),
        ("amazon_bulk_uploads", ac["amazon_bulk_uploads"]),
        ("external_network_calls", ac["external_network_calls"]),
        ("amazon_action_performed", "false"),
        ("causation_asserted", "false"),
    ]
    return pairs


def format_summary(result, fmt="text"):
    if fmt == "json":
        out = {k: v for k, v in _summary_pairs(result) if k != "reason"}
        out["reasons"] = result.get("reasons", [])
        out["counts"] = result.get("counts", {})
        return canonical_json(out)
    lines = [f"{k}={'' if v is None else v}" for k, v in _summary_pairs(result)]
    return "\n".join(lines)


def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="python -m production.phase7_outcome_followup",
        description="Offline Outcome Follow-up (Phase 7.7). Documents observed before-and-after "
                    "performance after an owner-recorded manual action, by comparing accepted Phase "
                    "7.6 records against a later Phase 7.3 offline analysis. Connects to nothing; "
                    "performs no Amazon action; never claims the action caused any result.")
    p.add_argument("--base-dir", default=os.path.join("runs", "T2", "phase7", "7.7"),
                   help="Phase 7.7 workspace (followups/ manifests/ exports/ validation/ runtime/ logs/).")
    p.add_argument("--phase7-3-dir", default=os.path.join("runs", "T2", "phase7", "7.3"),
                   help="Phase 7.3 workspace whose promoted/ analysis is the later report source.")
    p.add_argument("--phase7-6-dir", default=os.path.join("runs", "T2", "phase7", "7.6"),
                   help="Phase 7.6 workspace holding the manual-action tracker state and history.")
    p.add_argument("--phase7-5-dir", default=None,
                   help="Phase 7.5 workspace (for package-binding re-verification). "
                        "Defaults to a sibling '7.5' of the Phase 7.6 workspace.")
    p.add_argument("--reference-date", required=True,
                   help="Explicit reference date (YYYY-MM-DD); never defaulted to the current date.")
    p.add_argument("--before-start", required=True, help="Before window start (YYYY-MM-DD).")
    p.add_argument("--before-end", required=True, help="Before window end (YYYY-MM-DD).")
    p.add_argument("--after-start", required=True, help="After window start (YYYY-MM-DD).")
    p.add_argument("--after-end", required=True, help="After window end (YYYY-MM-DD).")
    p.add_argument("--minimum-followup-days", type=int, default=0,
                   help="Minimum days between the owner action and the after window (default 0).")
    p.add_argument("--policy-file", default=None,
                   help="Optional JSON classification policy overriding the neutral default.")
    p.add_argument("--require-source-lineage-match", action="store_true",
                   help="Exclude a record if the current Phase 7.3 identity differs from the "
                        "package's pinned source (SOURCE_CHANGED).")
    p.add_argument("--followup-id", default=None,
                   help="Optional expected follow-up package id (asserted after the run).")
    p.add_argument("--validate-only", action="store_true",
                   help="Validate all inputs and resolve follow-ups in memory; write no package.")
    p.add_argument("--format", choices=("text", "json"), default="text", help="Summary format.")
    return p


def _resolve_phase7_5_dir(args):
    if args.phase7_5_dir:
        return args.phase7_5_dir
    return os.path.join(os.path.dirname(os.path.abspath(args.phase7_6_dir)), "7.5")


def run_command(args):
    windows = (args.before_start, args.before_end, args.after_start, args.after_end)
    p75 = _resolve_phase7_5_dir(args)
    result = run(args.base_dir, args.phase7_3_dir, args.phase7_6_dir, p75, args.reference_date,
                 windows, policy_file=args.policy_file,
                 minimum_followup_days=args.minimum_followup_days,
                 require_source_lineage_match=args.require_source_lineage_match,
                 validate_only=args.validate_only)
    if args.followup_id and result.get("package_id") and result["package_id"] != args.followup_id:
        result["reasons"].append({"code": "FOLLOWUP_ID_MISMATCH",
                                  "detail": f"expected {args.followup_id}, got {result['package_id']}"})
        result["readiness"] = FOLLOWUP_BLOCKED
    return result


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        result = run_command(args)
    except FollowupError as e:
        sys.stdout.write(f"followup_readiness={_readiness_for_error(e.code)}\n"
                         f"error_code={e.code}\nerror_detail={e.detail}\n")
        return 2
    sys.stdout.write(format_summary(result, fmt=args.format) + "\n")
    return exit_code_for(result["readiness"])


if __name__ == "__main__":
    raise SystemExit(main())
