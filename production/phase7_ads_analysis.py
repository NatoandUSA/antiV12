#!/usr/bin/env python3
"""Phase 7.3 — offline Sponsored Products analysis over promoted Phase 7.2 output.

This module is the ONE Phase 7.3 authority. It reads ONLY the promoted (``final/``) Phase 7.2
artifacts — never the raw inbox, never a processing/accepted_raw/quarantine copy — and writes
owner-review reports into a local workspace.

It connects to NOTHING. There is no Amazon integration, no SP-API, no Ads API, no browser
automation, no network client of any kind. It changes no bid, budget, campaign, keyword or
negative, and it emits no upload action. Every output is a REVIEW LABEL for the owner, who
remains the only manual bridge to Amazon Seller Central.

Financial rules: Decimal only (never float), source currency preserved and never combined across
currencies, a zero denominator yields null (never zero), a malformed numeric never silently
becomes zero, and all rounding/quantization is centralized in ``core.money``.
"""
from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import io
import json
import os
import sys
import tempfile
from decimal import Decimal

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ("", "core", "listing", "scripts"):
    _p = _ROOT if not _sub else os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import product_workspace as PW           # noqa: E402  canonical json / content hash
from production import phase7_report_ingestion as RI     # noqa: E402  the Phase 7.2 authority
from core import money as MONEY                          # noqa: E402  Decimal / integer authority

canonical_json = PW.canonical_json
content_sha256 = PW.content_sha256

# ================================================================ constants / versions
STAGE_ID = "7.3"
STAGE_NAME = "Offline Sponsored Products Analysis and Owner Review Reporting"

SCHEMA_THRESHOLDS = "phase7-3-analysis-thresholds-v1"
SCHEMA_ANALYSIS = "phase7-3-analysis-v1"
SCHEMA_MANIFEST = "phase7-3-analysis-manifest-v1"
SCHEMA_ROW = "phase7-3-search-term-analysis-v1"
SCHEMA_DATA_QUALITY = "phase7-3-data-quality-v1"

_VOLATILE = ("deterministic_content_sha256", "generated_at", "started_at", "completed_at",
             "analyzed_at", "run_id", "repo_root", "run_dir", "base_dir", "phase7_2_dir",
             "git_head", "git_branch", "output_hashes")

# ---------------------------------------------------------------- analysis states
SOURCE_NOT_READY = "PHASE7_3_SOURCE_NOT_READY"
SOURCE_MANIFEST_INVALID = "PHASE7_3_SOURCE_MANIFEST_INVALID"
UNSUPPORTED_REPORT_TYPE = "PHASE7_3_UNSUPPORTED_REPORT_TYPE"
REQUIRED_COLUMNS_MISSING = "PHASE7_3_REQUIRED_COLUMNS_MISSING"
CURRENCY_MIX_BLOCKED = "PHASE7_3_CURRENCY_MIX_BLOCKED"
ANALYSIS_BLOCKED = "PHASE7_3_ANALYSIS_BLOCKED"
ANALYSIS_READY = "SESSION7_3_ANALYSIS_READY_FOR_OWNER_REVIEW"

BLOCKED_STATES = (SOURCE_NOT_READY, SOURCE_MANIFEST_INVALID, UNSUPPORTED_REPORT_TYPE,
                  REQUIRED_COLUMNS_MISSING, CURRENCY_MIX_BLOCKED, ANALYSIS_BLOCKED)

# ---------------------------------------------------------------- supported input
# Phase 7.3 analyses the search-term report only. Any other promoted type is reported, never guessed.
SUPPORTED_REPORT_TYPES = (RI.SP_SEARCH_TERM,)

# canonical identity groups + metric fields this analysis requires from a normalized row.
REQUIRED_IDENTITY_GROUPS = ("campaign", "ad_group", "search_term")
REQUIRED_METRIC_FIELDS = ("impressions", "clicks", "spend")
# optional metrics: absent means UNKNOWN (null), never zero.
OPTIONAL_METRIC_FIELDS = ("sales_7d", "orders_7d", "units_7d")

# the promoted directory is the ONLY legal source. These siblings are raw/intermediate and are
# never opened by this module.
FORBIDDEN_SOURCE_SUBDIRS = ("inbox", "processing", "accepted_raw", "quarantine", "candidate")

# ---------------------------------------------------------------- classifications
CL_PROVEN_CONVERTER = "PROVEN_CONVERTER"
CL_PROMISING_LOW_DATA = "PROMISING_LOW_DATA"
CL_HIGH_SPEND_NO_SALES = "HIGH_SPEND_NO_SALES"
CL_LOW_CONVERSION = "LOW_CONVERSION"
CL_HIGH_ACOS = "HIGH_ACOS"
CL_LOW_ACOS = "LOW_ACOS"
CL_NO_CLICKS = "NO_CLICKS"
CL_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
CL_NEEDS_OWNER_REVIEW = "NEEDS_OWNER_REVIEW"

CLASSIFICATIONS = (CL_PROVEN_CONVERTER, CL_PROMISING_LOW_DATA, CL_HIGH_SPEND_NO_SALES,
                   CL_LOW_CONVERSION, CL_HIGH_ACOS, CL_LOW_ACOS, CL_NO_CLICKS,
                   CL_INSUFFICIENT_DATA, CL_NEEDS_OWNER_REVIEW)

# ---------------------------------------------------------------- owner review labels
# These are REVIEW LABELS, never actions. Phase 7.3 never instructs a change and never uploads.
RL_EXACT = "REVIEW_FOR_MANUAL_EXACT_KEYWORD"
RL_NEGATIVE = "REVIEW_FOR_MANUAL_NEGATIVE"
RL_BID_BUDGET = "REVIEW_BID_OR_BUDGET_CONTEXT"
RL_MONITOR = "KEEP_MONITORING"
RL_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

REVIEW_LABELS = (RL_EXACT, RL_NEGATIVE, RL_BID_BUDGET, RL_MONITOR, RL_INSUFFICIENT)

REVIEW_LABEL_BY_CLASSIFICATION = {
    CL_PROVEN_CONVERTER: RL_EXACT,
    CL_LOW_ACOS: RL_EXACT,
    CL_HIGH_SPEND_NO_SALES: RL_NEGATIVE,
    CL_HIGH_ACOS: RL_BID_BUDGET,
    CL_LOW_CONVERSION: RL_BID_BUDGET,
    CL_PROMISING_LOW_DATA: RL_MONITOR,
    CL_NO_CLICKS: RL_MONITOR,
    CL_INSUFFICIENT_DATA: RL_INSUFFICIENT,
    CL_NEEDS_OWNER_REVIEW: RL_INSUFFICIENT,
}

# labels that place a row on the owner decision queue, in queue order.
DECISION_QUEUE_LABELS = (RL_NEGATIVE, RL_EXACT, RL_BID_BUDGET)

# ---------------------------------------------------------------- reason codes (evidence)
RC_ZERO_IMPRESSIONS = "ZERO_IMPRESSIONS"
RC_ZERO_CLICKS = "ZERO_CLICKS"
RC_ZERO_SPEND = "ZERO_SPEND"
RC_ZERO_SALES = "ZERO_SALES"
RC_ZERO_ORDERS = "ZERO_ORDERS"
RC_SPEND_AT_OR_ABOVE_NO_SALE_MIN = "SPEND_AT_OR_ABOVE_NO_SALE_MINIMUM"
RC_SPEND_BELOW_NO_SALE_MIN = "SPEND_BELOW_NO_SALE_MINIMUM"
RC_CLICKS_AT_OR_ABOVE_JUDGMENT_MIN = "CLICKS_AT_OR_ABOVE_CONVERSION_JUDGMENT_MINIMUM"
RC_CLICKS_BELOW_JUDGMENT_MIN = "CLICKS_BELOW_CONVERSION_JUDGMENT_MINIMUM"
RC_ORDERS_AT_OR_ABOVE_PROVEN_MIN = "ORDERS_AT_OR_ABOVE_PROVEN_CONVERTER_MINIMUM"
RC_IMPRESSIONS_BELOW_CTR_MIN = "IMPRESSIONS_BELOW_CTR_JUDGMENT_MINIMUM"
RC_ACOS_ABOVE_HIGH = "ACOS_ABOVE_HIGH_THRESHOLD"
RC_ACOS_AT_OR_BELOW_LOW = "ACOS_AT_OR_BELOW_LOW_THRESHOLD"
RC_ACOS_WITHIN_TARGET_BAND = "ACOS_WITHIN_TARGET_BAND"
RC_CTR_UNDEFINED = "CTR_UNDEFINED_ZERO_IMPRESSIONS"
RC_CPC_UNDEFINED = "CPC_UNDEFINED_ZERO_CLICKS"
RC_CVR_UNDEFINED = "CONVERSION_RATE_UNDEFINED_ZERO_CLICKS"
RC_ACOS_UNDEFINED = "ACOS_UNDEFINED_ZERO_SALES"
RC_ROAS_UNDEFINED = "ROAS_UNDEFINED_ZERO_SPEND"
RC_SALES_NOT_REPORTED = "SALES_NOT_REPORTED"
RC_ORDERS_NOT_REPORTED = "ORDERS_NOT_REPORTED"
RC_REQUIRED_METRIC_UNUSABLE = "REQUIRED_METRIC_UNUSABLE"
RC_INVALID_NUMERIC = "INVALID_NUMERIC_VALUE"
RC_MATCH_TYPE_NOT_APPLICABLE = "MATCH_TYPE_NOT_APPLICABLE"
RC_OUTSIDE_LOOKBACK_WINDOW = "OUTSIDE_LOOKBACK_WINDOW"

# ---------------------------------------------------------------- artifact filenames
D_CONFIG, D_STAGING, D_PROMOTED, D_LOGS, D_QUARANTINE = (
    "config", "staging", "promoted", "logs", "quarantine")
_WORKSPACE_SUBDIRS = (D_CONFIG, D_STAGING, D_PROMOTED, D_LOGS, D_QUARANTINE)

F_THRESHOLDS = "analysis-thresholds.json"
F_MANIFEST = "analysis-manifest.json"
F_CAMPAIGN_CSV = "campaign-summary.csv"
F_AD_GROUP_CSV = "ad-group-summary.csv"
F_SEARCH_TERM_CSV = "search-term-analysis.csv"
F_DECISION_QUEUE_CSV = "owner-decision-queue.csv"
F_OWNER_REPORT_MD = "owner-report.md"
F_ANALYSIS_JSON = "analysis.json"

PROMOTED_ARTIFACTS = (F_CAMPAIGN_CSV, F_AD_GROUP_CSV, F_SEARCH_TERM_CSV, F_DECISION_QUEUE_CSV,
                      F_OWNER_REPORT_MD, F_ANALYSIS_JSON, F_MANIFEST)

# ---------------------------------------------------------------- zero-action boundary counters
_ZERO_COUNTERS = {
    "external_amazon_account_attempts": 0, "amazon_account_actions": 0, "campaign_write_actions": 0,
    "target_write_actions": 0, "negative_write_actions": 0, "bid_write_actions": 0,
    "budget_write_actions": 0, "report_download_attempts": 0, "external_network_attempts": 0,
    "browser_automation_attempts": 0, "credential_store_count": 0, "api_payload_count": 0,
    "automated_optimization_actions": 0, "upload_actions": 0, "raw_inbox_reads": 0,
}
_THIS_SESSION_NEVER = {
    "connects_to_amazon": True, "downloads_or_retrieves_reports": True, "changes_bids": True,
    "changes_budgets": True, "changes_campaigns": True, "changes_keywords": True,
    "creates_negatives": True, "uploads_anything": True, "reads_raw_inbox": True,
    "emits_api_payload": True, "automates_a_browser": True, "sends_rows_to_external_service": True,
}


class AdsAnalysisError(Exception):
    pass


# ================================================================ small helpers
def _sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _recursive_strip(obj, names):
    if isinstance(obj, dict):
        return {k: _recursive_strip(v, names) for k, v in obj.items() if k not in names}
    if isinstance(obj, list):
        return [_recursive_strip(v, names) for v in obj]
    return obj


def _finalize(doc, extra_volatile=()):
    volatile = set(_VOLATILE) | set(extra_volatile)
    doc["deterministic_content_sha256"] = content_sha256(_recursive_strip(doc, volatile))
    return doc


def _atomic_write_bytes(path, data):
    """temp sibling -> flush -> fsync -> os.replace. A failed write never overwrites a promoted file."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-7-3-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def workspace_dirs(base_dir):
    d = {"base": os.path.abspath(base_dir)}
    for s in _WORKSPACE_SUBDIRS:
        d[s] = os.path.join(d["base"], s)
    return d


def ensure_workspace(base_dir):
    d = workspace_dirs(base_dir)
    for k, v in d.items():
        if k != "base":
            os.makedirs(v, exist_ok=True)
    return d


def source_final_dir(phase7_2_dir):
    """The ONE legal Phase 7.2 source directory: its promoted ``final/``."""
    return os.path.join(os.path.abspath(phase7_2_dir), "final")


def assert_source_path_allowed(path, phase7_2_dir):
    """Refuse any read outside the promoted Phase 7.2 directory. The raw inbox and every
    intermediate directory are permanently out of bounds for Phase 7.3."""
    final = os.path.abspath(source_final_dir(phase7_2_dir))
    target = os.path.abspath(path)
    if os.path.commonpath([final, target]) != final:
        raise AdsAnalysisError(f"Phase 7.3 may read only promoted Phase 7.2 output: {target!r}")
    return target


# ================================================================ THRESHOLD CONFIGURATION
# One versioned threshold document. Nothing below is hardcoded at a call site.
DEFAULT_THRESHOLDS = {
    "schema_version": SCHEMA_THRESHOLDS,
    "policy_id": "phase7-3-analysis-thresholds-v1",
    "minimum_clicks_for_conversion_judgment": 10,
    "minimum_spend_for_no_sale_risk": "5.00",
    "minimum_orders_for_proven_converter": 1,
    "target_acos": "0.30",
    # There is no owner-declared target ACoS yet. This is a NEUTRAL DEFAULT, not a recommendation,
    # and the owner is expected to replace it in config/analysis-thresholds.json.
    "target_acos_source": "NEUTRAL_DEFAULT_OWNER_CONFIGURABLE",
    "high_acos_multiplier": "1.25",
    "low_acos_multiplier": "0.75",
    "minimum_impressions_for_ctr_judgment": 100,
    "lookback_days": 30,
    "date_rule": "SOURCE_REPORTED_RANGE_ONLY_NEVER_INFERRED",
}


class Thresholds:
    """Parsed, Decimal-safe view over a threshold document."""

    def __init__(self, doc):
        self.doc = dict(doc)
        self.schema_version = doc.get("schema_version")
        self.policy_id = doc.get("policy_id")
        self.min_clicks = MONEY.parse_nonnegative_int(
            doc["minimum_clicks_for_conversion_judgment"], field="minimum_clicks_for_conversion_judgment",
            allow_missing=False)
        self.min_spend_no_sale = MONEY.parse_nonnegative_decimal(
            doc["minimum_spend_for_no_sale_risk"], field="minimum_spend_for_no_sale_risk",
            allow_missing=False)
        self.min_orders_proven = MONEY.parse_nonnegative_int(
            doc["minimum_orders_for_proven_converter"], field="minimum_orders_for_proven_converter",
            allow_missing=False)
        self.target_acos = MONEY.parse_nonnegative_decimal(
            doc["target_acos"], field="target_acos", allow_missing=False)
        self.target_acos_source = doc.get("target_acos_source")
        self.high_multiplier = MONEY.parse_nonnegative_decimal(
            doc["high_acos_multiplier"], field="high_acos_multiplier", allow_missing=False)
        self.low_multiplier = MONEY.parse_nonnegative_decimal(
            doc["low_acos_multiplier"], field="low_acos_multiplier", allow_missing=False)
        self.min_impressions_ctr = MONEY.parse_nonnegative_int(
            doc["minimum_impressions_for_ctr_judgment"],
            field="minimum_impressions_for_ctr_judgment", allow_missing=False)
        self.lookback_days = MONEY.parse_nonnegative_int(
            doc["lookback_days"], field="lookback_days", allow_missing=False)
        self.date_rule = doc.get("date_rule")

    @property
    def high_acos(self):
        return self.target_acos * self.high_multiplier

    @property
    def low_acos(self):
        return self.target_acos * self.low_multiplier

    def owner_configured_target(self):
        return self.target_acos_source != DEFAULT_THRESHOLDS["target_acos_source"]


def load_thresholds(config_dir, *, write_default=True):
    """Load config/analysis-thresholds.json, materializing the versioned default when absent.
    An owner edit is honored as-is; nothing is silently merged or overwritten."""
    path = os.path.join(config_dir, F_THRESHOLDS)
    if os.path.isfile(path):
        with open(path, "rb") as f:
            doc = json.loads(f.read().decode("utf-8"))
        if not isinstance(doc, dict):
            raise AdsAnalysisError("threshold config is not an object")
        merged = dict(DEFAULT_THRESHOLDS)
        merged.update(doc)      # owner keys win; a newly added default key still resolves
        return Thresholds(merged), path
    if write_default:
        _atomic_write_bytes(path, canonical_json(DEFAULT_THRESHOLDS).encode("utf-8"))
    return Thresholds(DEFAULT_THRESHOLDS), path


# ================================================================ SOURCE VERIFICATION
def load_source(phase7_2_dir):
    """Verify and load the promoted Phase 7.2 state. Returns a source dict; ``state`` is None when
    the source is usable, otherwise the Phase 7.3 block state."""
    final = source_final_dir(phase7_2_dir)
    out = {"phase7_2_dir": os.path.abspath(phase7_2_dir), "final_dir": final, "state": None,
           "manifest": None, "readiness": None, "verification": None, "report_types": [],
           "rows": [], "source_files": [], "detail": {}}
    if not os.path.isdir(final):
        out["state"] = SOURCE_NOT_READY
        out["detail"] = {"reason": "PROMOTED_DIRECTORY_MISSING", "path": final}
        return out

    manifest = RI.read_promoted_manifest(final)
    if manifest is None:
        out["state"] = SOURCE_MANIFEST_INVALID
        out["detail"] = {"reason": "MANIFEST_MISSING_OR_UNREADABLE"}
        return out
    out["manifest"] = manifest

    # readiness gate: only a fully ready Phase 7.2 state may be analysed.
    readiness = manifest.get("analysis_readiness")
    out["readiness"] = readiness
    if readiness != RI.REPORTS_READY:
        out["state"] = SOURCE_NOT_READY
        out["detail"] = {"reason": "READINESS_NOT_READY", "analysis_readiness": readiness,
                         "required": RI.REPORTS_READY}
        return out

    # promotion integrity: re-verify the promoted bytes with Phase 7.2's own authority.
    verification = RI.verify_promoted_state(final, manifest)
    out["verification"] = verification
    if verification["result"] != "PASS":
        out["state"] = SOURCE_MANIFEST_INVALID
        out["detail"] = {"reason": "PROMOTED_STATE_VERIFICATION_FAILED", "verification": verification}
        return out

    # supported report type present?
    available = RI.promoted_normalized_artifacts(manifest)
    by_name = {RI.NORMALIZED_FILE[t]: t for t in RI.REPORT_TYPES}
    present_types = sorted({by_name[n] for n in available if n in by_name})
    out["report_types"] = present_types
    supported = [t for t in present_types if t in SUPPORTED_REPORT_TYPES]
    if not supported:
        out["state"] = UNSUPPORTED_REPORT_TYPE
        out["detail"] = {"reason": "NO_SUPPORTED_REPORT_TYPE", "present": present_types,
                         "supported": list(SUPPORTED_REPORT_TYPES)}
        return out

    rows, files = [], []
    for report_type in supported:
        name = RI.NORMALIZED_FILE[report_type]
        path = assert_source_path_allowed(os.path.join(final, name), phase7_2_dir)
        if not os.path.isfile(path):
            out["state"] = SOURCE_NOT_READY
            out["detail"] = {"reason": "NORMALIZED_ARTIFACT_MISSING", "artifact": name}
            return out
        with open(path, "rb") as f:
            data = f.read()
        files.append({"artifact": name, "report_type": report_type,
                      "sha256": _sha_bytes(data), "byte_length": len(data)})
        for line_no, line in enumerate(io.StringIO(data.decode("utf-8")), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                out["state"] = SOURCE_MANIFEST_INVALID
                out["detail"] = {"reason": "NORMALIZED_ROW_NOT_JSON", "artifact": name,
                                 "line": line_no}
                return out
            row["_source_artifact"] = name
            row["_source_line"] = line_no
            rows.append(row)
    out["rows"] = rows
    out["source_files"] = files

    # required columns must exist on every analysed row (identity groups + metric fields).
    missing = required_columns_missing(rows)
    if missing:
        out["state"] = REQUIRED_COLUMNS_MISSING
        out["detail"] = {"reason": "REQUIRED_COLUMNS_MISSING", "missing_fields": missing}
        return out

    # currency: preserved per row, never combined across currencies.
    currencies = sorted({r.get("currency") for r in rows if r.get("currency")})
    if len(currencies) > 1:
        out["state"] = CURRENCY_MIX_BLOCKED
        out["detail"] = {"reason": "MULTIPLE_CURRENCIES_IN_SOURCE", "currencies": currencies}
        return out
    out["currencies"] = currencies
    return out


def required_columns_missing(rows):
    """Identity groups / metric fields absent from the normalized rows, deterministically ordered."""
    missing = set()
    for row in rows:
        identity = row.get("identity") or {}
        key_basis = row.get("key_basis") or {}
        metrics = row.get("metrics") or {}
        states = row.get("metric_states") or {}
        for group in REQUIRED_IDENTITY_GROUPS:
            if group not in key_basis and not any(
                    f in identity for f in RI._GROUP_SOURCES.get(group, ())):
                missing.add(f"identity:{group}")
        for field in REQUIRED_METRIC_FIELDS:
            if field not in metrics and field not in states:
                missing.add(f"metric:{field}")
    return sorted(missing)


# ================================================================ METRIC EXTRACTION (Decimal-safe)
def read_int_metric(metrics, states, field):
    """Return (value_or_None, state). A malformed value becomes INVALID — never a silent zero."""
    if field not in metrics:
        return None, states.get(field, "MISSING")
    raw = metrics[field]
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None, "INVALID"
    if raw < 0:
        return None, "INVALID"
    return raw, states.get(field, "PRESENT")


def read_money_metric(metrics, states, field):
    """Return (Decimal_or_None, state). Floats and unparseable text are INVALID, never zero."""
    if field not in metrics:
        return None, states.get(field, "MISSING")
    raw = metrics[field]
    if isinstance(raw, (float, bool)):
        return None, "INVALID"
    try:
        value = MONEY.parse_decimal_string(raw, field=field, allow_missing=False)
    except MONEY.MoneyError:
        return None, "INVALID"
    if value < 0:
        return None, "INVALID"
    return value, states.get(field, "PRESENT")


def derive_rates(impressions, clicks, spend, sales, orders):
    """All derived rates. A zero/absent denominator yields None (null) — never zero."""
    dec = lambda n: None if n is None else Decimal(n)     # noqa: E731
    return {
        "ctr": MONEY.safe_divide(dec(clicks), dec(impressions)),
        "cpc": MONEY.safe_divide(spend, dec(clicks)),
        "conversion_rate": MONEY.safe_divide(dec(orders), dec(clicks)),
        "acos": MONEY.safe_divide(spend, sales),
        "roas": MONEY.safe_divide(sales, spend),
    }


# ================================================================ CLASSIFICATION
# Ordered precedence: the FIRST matching rule sets the single primary classification. A row may
# carry many reason codes, but exactly one primary classification. Order is the contract.
def _rule_required_unusable(ev, th):
    return ev["impressions"] is None or ev["clicks"] is None or ev["spend"] is None


def _rule_no_impressions(ev, th):
    return ev["impressions"] == 0


def _rule_no_clicks(ev, th):
    return ev["clicks"] == 0


def _rule_outcome_not_reported(ev, th):
    return ev["sales"] is None or ev["orders"] is None


def _rule_high_spend_no_sales(ev, th):
    return ev["sales"] == 0 and ev["spend"] >= th.min_spend_no_sale


def _rule_promising_low_data(ev, th):
    # converted, but on too little click data to support an ACoS or conversion judgment.
    return ev["orders"] >= th.min_orders_proven and ev["clicks"] < th.min_clicks


def _rule_high_acos(ev, th):
    # an ACoS judgment requires enough clicks to mean anything.
    return (ev["clicks"] >= th.min_clicks and ev["acos"] is not None
            and ev["acos"] > th.high_acos)


def _rule_low_acos(ev, th):
    return (ev["clicks"] >= th.min_clicks and ev["acos"] is not None
            and ev["acos"] <= th.low_acos)


def _rule_proven_converter(ev, th):
    return ev["orders"] >= th.min_orders_proven and ev["clicks"] >= th.min_clicks


def _rule_low_conversion(ev, th):
    return ev["clicks"] >= th.min_clicks and ev["orders"] == 0


def _rule_low_click_volume(ev, th):
    return ev["clicks"] < th.min_clicks


CLASSIFICATION_RULES = (
    ("REQUIRED_METRIC_UNUSABLE", CL_INSUFFICIENT_DATA, _rule_required_unusable),
    ("NO_IMPRESSIONS", CL_INSUFFICIENT_DATA, _rule_no_impressions),
    ("NO_CLICKS", CL_NO_CLICKS, _rule_no_clicks),
    ("OUTCOME_NOT_REPORTED", CL_NEEDS_OWNER_REVIEW, _rule_outcome_not_reported),
    ("HIGH_SPEND_NO_SALES", CL_HIGH_SPEND_NO_SALES, _rule_high_spend_no_sales),
    ("PROMISING_LOW_DATA", CL_PROMISING_LOW_DATA, _rule_promising_low_data),
    ("HIGH_ACOS", CL_HIGH_ACOS, _rule_high_acos),
    ("LOW_ACOS", CL_LOW_ACOS, _rule_low_acos),
    ("PROVEN_CONVERTER", CL_PROVEN_CONVERTER, _rule_proven_converter),
    ("LOW_CONVERSION", CL_LOW_CONVERSION, _rule_low_conversion),
    ("LOW_CLICK_VOLUME", CL_INSUFFICIENT_DATA, _rule_low_click_volume),
)
CLASSIFICATION_PRECEDENCE = tuple(r[0] for r in CLASSIFICATION_RULES)


def classify(ev, thresholds):
    """Return (primary_classification, matched_rule_id). Deterministic and threshold-driven."""
    for rule_id, label, predicate in CLASSIFICATION_RULES:
        if predicate(ev, thresholds):
            return label, rule_id
    return CL_NEEDS_OWNER_REVIEW, "FALLBACK_UNCLASSIFIED"


def reason_codes(ev, thresholds):
    """Every applicable evidence code, deterministically ordered. Independent of the primary label."""
    th, codes = thresholds, set()
    if ev["impressions"] is None or ev["clicks"] is None or ev["spend"] is None:
        codes.add(RC_REQUIRED_METRIC_UNUSABLE)
    for field, value, state in ev["metric_states"]:
        if state == "INVALID":
            codes.add(RC_INVALID_NUMERIC)
    if ev["impressions"] == 0:
        codes.add(RC_ZERO_IMPRESSIONS)
    if ev["clicks"] == 0:
        codes.add(RC_ZERO_CLICKS)
    if ev["spend"] is not None and ev["spend"] == 0:
        codes.add(RC_ZERO_SPEND)
    if ev["sales"] is None:
        codes.add(RC_SALES_NOT_REPORTED)
    elif ev["sales"] == 0:
        codes.add(RC_ZERO_SALES)
    if ev["orders"] is None:
        codes.add(RC_ORDERS_NOT_REPORTED)
    elif ev["orders"] == 0:
        codes.add(RC_ZERO_ORDERS)
    if ev["spend"] is not None:
        codes.add(RC_SPEND_AT_OR_ABOVE_NO_SALE_MIN if ev["spend"] >= th.min_spend_no_sale
                  else RC_SPEND_BELOW_NO_SALE_MIN)
    if ev["clicks"] is not None:
        codes.add(RC_CLICKS_AT_OR_ABOVE_JUDGMENT_MIN if ev["clicks"] >= th.min_clicks
                  else RC_CLICKS_BELOW_JUDGMENT_MIN)
    if ev["orders"] is not None and ev["orders"] >= th.min_orders_proven:
        codes.add(RC_ORDERS_AT_OR_ABOVE_PROVEN_MIN)
    if ev["impressions"] is not None and ev["impressions"] < th.min_impressions_ctr:
        codes.add(RC_IMPRESSIONS_BELOW_CTR_MIN)
    if ev["acos"] is not None:
        if ev["acos"] > th.high_acos:
            codes.add(RC_ACOS_ABOVE_HIGH)
        elif ev["acos"] <= th.low_acos:
            codes.add(RC_ACOS_AT_OR_BELOW_LOW)
        else:
            codes.add(RC_ACOS_WITHIN_TARGET_BAND)
    if ev["ctr"] is None:
        codes.add(RC_CTR_UNDEFINED)
    if ev["cpc"] is None:
        codes.add(RC_CPC_UNDEFINED)
    if ev["conversion_rate"] is None:
        codes.add(RC_CVR_UNDEFINED)
    if ev["acos"] is None:
        codes.add(RC_ACOS_UNDEFINED)
    if ev["roas"] is None:
        codes.add(RC_ROAS_UNDEFINED)
    if ev["match_type"] is None:
        codes.add(RC_MATCH_TYPE_NOT_APPLICABLE)
    if ev["outside_lookback"]:
        codes.add(RC_OUTSIDE_LOOKBACK_WINDOW)
    return sorted(codes)


# ================================================================ ROW ANALYSIS
def _lookback_start(reference_date, lookback_days):
    if not reference_date:
        return None
    try:
        ref = _dt.date.fromisoformat(reference_date)
    except ValueError:
        return None
    return (ref - _dt.timedelta(days=int(lookback_days))).isoformat()


def analyze_row(row, thresholds, *, lookback_start=None):
    """Analyze one normalized Phase 7.2 row into an owner-review record. Never mutates the source."""
    identity = row.get("identity") or {}
    metrics = row.get("metrics") or {}
    states = row.get("metric_states") or {}
    lineage = row.get("lineage") or {}

    impressions, s_impr = read_int_metric(metrics, states, "impressions")
    clicks, s_clicks = read_int_metric(metrics, states, "clicks")
    orders, s_orders = read_int_metric(metrics, states, "orders_7d")
    units, s_units = read_int_metric(metrics, states, "units_7d")
    spend, s_spend = read_money_metric(metrics, states, "spend")
    sales, s_sales = read_money_metric(metrics, states, "sales_7d")

    rates = derive_rates(impressions, clicks, spend, sales, orders)
    end_date = row.get("end_date")
    outside = bool(lookback_start and end_date and end_date < lookback_start)

    ev = {
        "impressions": impressions, "clicks": clicks, "spend": spend, "sales": sales,
        "orders": orders, "units": units, "match_type": identity.get("match_type"),
        "outside_lookback": outside,
        "metric_states": (("impressions", impressions, s_impr), ("clicks", clicks, s_clicks),
                          ("spend", spend, s_spend), ("sales_7d", sales, s_sales),
                          ("orders_7d", orders, s_orders), ("units_7d", units, s_units)),
    }
    ev.update(rates)

    classification, rule_id = classify(ev, thresholds)
    codes = reason_codes(ev, thresholds)
    label = REVIEW_LABEL_BY_CLASSIFICATION[classification]

    return {
        "schema_version": SCHEMA_ROW,
        "campaign": identity.get("campaign_name") or identity.get("campaign_id"),
        "ad_group": identity.get("ad_group_name") or identity.get("ad_group_id"),
        "targeting": (identity.get("targeting_expression") or identity.get("targeting_text")
                      or identity.get("targeting_id")),
        "match_type": identity.get("match_type"),
        "customer_search_term": identity.get("search_term"),
        "currency": row.get("currency"),
        "start_date": row.get("start_date"),
        "end_date": end_date,
        "impressions": impressions,
        "clicks": clicks,
        "spend": MONEY.serialize_currency(spend) if spend is not None else None,
        "orders": orders,
        "sales": MONEY.serialize_currency(sales) if sales is not None else None,
        "units": units,
        "ctr": MONEY.serialize_rate(rates["ctr"]),
        "cpc": MONEY.serialize_currency(rates["cpc"]) if rates["cpc"] is not None else None,
        "conversion_rate": MONEY.serialize_rate(rates["conversion_rate"]),
        "acos": MONEY.serialize_rate(rates["acos"]),
        "roas": MONEY.serialize_rate(rates["roas"]),
        "primary_classification": classification,
        "classification_rule": rule_id,
        "reason_codes": codes,
        "owner_review_label": label,
        "evidence": {
            "metric_states": {f: s for f, _v, s in ev["metric_states"]},
            "thresholds_policy_id": thresholds.policy_id,
            "target_acos": MONEY.serialize_rate(thresholds.target_acos),
            "target_acos_source": thresholds.target_acos_source,
            "high_acos_threshold": MONEY.serialize_rate(thresholds.high_acos),
            "low_acos_threshold": MONEY.serialize_rate(thresholds.low_acos),
            "minimum_clicks_for_conversion_judgment": thresholds.min_clicks,
            "minimum_spend_for_no_sale_risk": MONEY.serialize_currency(thresholds.min_spend_no_sale),
            "outside_lookback_window": outside,
        },
        "source_file": row.get("_source_artifact"),
        "source_file_sha256": lineage.get("source_file_sha256"),
        "source_row_number": lineage.get("source_row_number"),
        "source_line_number": row.get("_source_line"),
        "canonical_row_key": row.get("canonical_row_key"),
        "lineage_hash": lineage.get("lineage_hash"),
    }


# ================================================================ AGGREGATION
def _agg_seed(currency):
    return {"currency": currency, "impressions": 0, "clicks": 0, "orders": 0, "units": 0,
            "spend": Decimal("0"), "sales": Decimal("0"), "rows": 0, "excluded_rows": 0}


def _agg_add(acc, analyzed):
    """Accumulate one analysed row. A row missing a required metric is counted, never summed as 0."""
    if analyzed["impressions"] is None or analyzed["clicks"] is None or analyzed["spend"] is None:
        acc["excluded_rows"] += 1
        return
    acc["rows"] += 1
    acc["impressions"] += analyzed["impressions"]
    acc["clicks"] += analyzed["clicks"]
    acc["spend"] += MONEY.parse_decimal_string(analyzed["spend"], allow_missing=False)
    for field in ("orders", "units"):
        if analyzed[field] is not None:
            acc[field] += analyzed[field]
    if analyzed["sales"] is not None:
        acc["sales"] += MONEY.parse_decimal_string(analyzed["sales"], allow_missing=False)


def _agg_finish(keys, acc):
    rates = derive_rates(acc["impressions"], acc["clicks"], acc["spend"], acc["sales"], acc["orders"])
    out = dict(keys)
    out.update({
        "currency": acc["currency"],
        "impressions": acc["impressions"], "clicks": acc["clicks"],
        "spend": MONEY.serialize_currency(acc["spend"]),
        "orders": acc["orders"], "sales": MONEY.serialize_currency(acc["sales"]),
        "units": acc["units"],
        "ctr": MONEY.serialize_rate(rates["ctr"]),
        "cpc": MONEY.serialize_currency(rates["cpc"]) if rates["cpc"] is not None else None,
        "conversion_rate": MONEY.serialize_rate(rates["conversion_rate"]),
        "acos": MONEY.serialize_rate(rates["acos"]),
        "roas": MONEY.serialize_rate(rates["roas"]),
        "analyzed_rows": acc["rows"], "excluded_rows": acc["excluded_rows"],
    })
    return out


def aggregate(analyzed_rows, group_fields):
    """Group by currency + the given identity fields. Currencies are NEVER combined."""
    buckets = {}
    for row in analyzed_rows:
        key = tuple(row.get(f) for f in group_fields)
        bucket_key = (row.get("currency"),) + key
        acc = buckets.setdefault(bucket_key, _agg_seed(row.get("currency")))
        _agg_add(acc, row)
    out = []
    for bucket_key in sorted(buckets, key=lambda k: tuple("" if v is None else str(v) for v in k)):
        keys = dict(zip(group_fields, bucket_key[1:]))
        out.append(_agg_finish(keys, buckets[bucket_key]))
    return out


# ================================================================ DETERMINISTIC ORDERING
def _text(value):
    return "" if value is None else str(value)


def sort_search_terms(rows):
    """Stable, total ordering. canonical_row_key is unique, so the order is fully determined."""
    return sorted(rows, key=lambda r: (
        _text(r["campaign"]), _text(r["ad_group"]), _text(r["targeting"]), _text(r["match_type"]),
        _text(r["customer_search_term"]), _text(r["start_date"]), _text(r["end_date"]),
        _text(r["canonical_row_key"])))


def sort_decision_queue(rows):
    """Queue order: label priority, then spend (desc), then the unique canonical key."""
    order = {label: i for i, label in enumerate(DECISION_QUEUE_LABELS)}
    def key(r):
        spend = MONEY.parse_decimal_string(r["spend"], allow_missing=True) if r["spend"] else Decimal("0")
        return (order.get(r["owner_review_label"], len(order)), -spend, _text(r["canonical_row_key"]))
    return sorted(rows, key=key)


# ================================================================ DATA QUALITY
def build_data_quality(source, analyzed, thresholds, *, reference_date, lookback_start):
    manifest = source["manifest"] or {}
    rows = source["rows"]
    missing_fields, zero_denoms = {}, {"ctr": 0, "cpc": 0, "conversion_rate": 0, "acos": 0, "roas": 0}
    invalid_numeric = 0
    for a in analyzed:
        for field, state in a["evidence"]["metric_states"].items():
            if state in ("MISSING", "BLANK"):
                missing_fields[field] = missing_fields.get(field, 0) + 1
            elif state == "INVALID":
                invalid_numeric += 1
        for rate in zero_denoms:
            if a[rate] is None:
                zero_denoms[rate] += 1
    keys = [r.get("canonical_row_key") for r in rows]
    dates = sorted({r.get("start_date") for r in rows if r.get("start_date")} |
                   {r.get("end_date") for r in rows if r.get("end_date")})
    outside = sum(1 for a in analyzed if a["evidence"]["outside_lookback_window"])
    doc = {
        "schema_version": SCHEMA_DATA_QUALITY,
        "source_manifest_identity": {
            "stage_id": manifest.get("stage_id"),
            "schema_version": manifest.get("schema_version"),
            "analysis_readiness": manifest.get("analysis_readiness"),
            "deterministic_content_sha256": manifest.get("deterministic_content_sha256"),
        },
        "source_file_count": len(source["source_files"]),
        "source_files": source["source_files"],
        "source_row_count": len(rows),
        "analyzed_row_count": len(analyzed),
        "skipped_row_count": len(rows) - len(analyzed),
        "blocked_row_count": 0,
        "missing_field_counts": dict(sorted(missing_fields.items())),
        "duplicate_handling": {
            "policy": "PHASE7_2_RECONCILED_CANONICAL_ROW_KEY__NO_PHASE7_3_DEDUP",
            "distinct_canonical_row_keys": len(set(keys)),
            "duplicate_canonical_row_key_count": len(keys) - len(set(keys)),
        },
        "zero_denominator_counts": zero_denoms,
        "invalid_numeric_count": invalid_numeric,
        "date_coverage": {
            "earliest": dates[0] if dates else None,
            "latest": dates[-1] if dates else None,
            "distinct_dates": len(dates),
            "reference_date": reference_date,
            "lookback_days": thresholds.lookback_days,
            "lookback_start": lookback_start,
            "rows_outside_lookback_window": outside,
        },
        "currencies_encountered": sorted({r.get("currency") for r in rows if r.get("currency")}),
    }
    return doc


# ================================================================ DOCUMENT BUILDERS
_SEARCH_TERM_COLUMNS = (
    "campaign", "ad_group", "targeting", "match_type", "customer_search_term", "currency",
    "start_date", "end_date", "impressions", "clicks", "spend", "orders", "sales", "units",
    "ctr", "cpc", "conversion_rate", "acos", "roas", "primary_classification",
    "classification_rule", "reason_codes", "owner_review_label", "evidence_metric_states",
    "evidence_target_acos", "evidence_target_acos_source", "evidence_outside_lookback_window",
    "source_file", "source_file_sha256", "source_row_number", "source_line_number",
    "canonical_row_key", "lineage_hash",
)

_SUMMARY_COLUMNS_TAIL = ("currency", "impressions", "clicks", "spend", "orders", "sales", "units",
                         "ctr", "cpc", "conversion_rate", "acos", "roas", "analyzed_rows",
                         "excluded_rows")


def _csv_bytes(columns, records):
    """Deterministic CSV: LF endings, fixed column order, formula-inert cells."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(list(columns))
    for rec in records:
        writer.writerow([RI.csv_safe_cell("" if rec.get(c) is None else rec.get(c)) for c in columns])
    return buf.getvalue().encode("utf-8")


def _flatten_search_term(a):
    out = dict(a)
    out["reason_codes"] = ";".join(a["reason_codes"])
    ev = a["evidence"]
    out["evidence_metric_states"] = ";".join(f"{k}={v}" for k, v in sorted(ev["metric_states"].items()))
    out["evidence_target_acos"] = ev["target_acos"]
    out["evidence_target_acos_source"] = ev["target_acos_source"]
    out["evidence_outside_lookback_window"] = "true" if ev["outside_lookback_window"] else "false"
    return out


def _md_cell(value):
    return RI.csv_safe_cell("" if value is None else value).replace("|", "\\|").replace("\n", " ")


def build_owner_report_md(result):
    """Owner-facing review report. Contains REVIEW LABELS ONLY — never an instruction to change
    a bid, budget, campaign, keyword or negative, and never an upload step."""
    a = result["analysis"]
    dq = a["data_quality"]
    th = result["thresholds"]
    lines = []
    add = lines.append
    add("# Phase 7.3 — Sponsored Products Owner Review")
    add("")
    add(f"- Analysis readiness: `{a['analysis_readiness']}`")
    add(f"- Source Phase 7.2 state: `{dq['source_manifest_identity']['analysis_readiness']}`")
    add(f"- Source rows analysed: {dq['analyzed_row_count']} of {dq['source_row_count']}")
    add(f"- Currencies: {', '.join(dq['currencies_encountered']) or 'none'} "
        f"(totals are never combined across currencies)")
    add(f"- Date coverage: {dq['date_coverage']['earliest']} to {dq['date_coverage']['latest']} "
        f"({dq['date_coverage']['distinct_dates']} distinct dates)")
    add("")
    add("Everything below is for OWNER REVIEW ONLY. Phase 7.3 connects to nothing, changes nothing, "
        "and uploads nothing. The owner remains the only manual bridge to Amazon Seller Central.")
    add("")
    add("## Threshold configuration")
    add("")
    add(f"- Policy: `{th.policy_id}`")
    add(f"- Target ACoS: `{MONEY.serialize_rate(th.target_acos)}` "
        f"(source: `{th.target_acos_source}`)")
    if not th.owner_configured_target():
        add("  - This is a NEUTRAL DEFAULT, not a recommendation. It is owner-configurable in "
            "`config/analysis-thresholds.json`.")
    add(f"- High ACoS threshold: `{MONEY.serialize_rate(th.high_acos)}` "
        f"(target x {th.doc['high_acos_multiplier']})")
    add(f"- Low ACoS threshold: `{MONEY.serialize_rate(th.low_acos)}` "
        f"(target x {th.doc['low_acos_multiplier']})")
    add(f"- Minimum clicks for a conversion judgment: {th.min_clicks}")
    add(f"- Minimum spend for no-sale risk: {MONEY.serialize_currency(th.min_spend_no_sale)}")
    add(f"- Minimum orders for a proven converter: {th.min_orders_proven}")
    add(f"- Minimum impressions for a CTR judgment: {th.min_impressions_ctr}")
    add(f"- Lookback: {th.lookback_days} days (rule `{th.date_rule}`)")
    add("")
    add("## Classification counts")
    add("")
    add("| Classification | Rows |")
    add("| --- | ---: |")
    for name in CLASSIFICATIONS:
        add(f"| {name} | {a['classification_counts'].get(name, 0)} |")
    add("")
    add("## Owner review labels")
    add("")
    add("| Review label | Rows |")
    add("| --- | ---: |")
    for name in REVIEW_LABELS:
        add(f"| {name} | {a['review_label_counts'].get(name, 0)} |")
    add("")
    add("## Owner decision queue")
    add("")
    add(f"{len(a['owner_decision_queue'])} row(s) carry a review label that asks for owner attention. "
        "A review label is a prompt to look, never an instruction to act.")
    add("")
    if not a["owner_decision_queue"]:
        add(f"No row cleared a review threshold at this configuration — most commonly because "
            f"click volume stays under the {th.min_clicks}-click minimum required before any "
            f"conversion or ACoS judgment is made. The thresholds are owner-configurable in "
            f"`config/analysis-thresholds.json`.")
        add("")
    if a["owner_decision_queue"]:
        add("| Review label | Campaign | Ad group | Search term | Clicks | Spend | Sales | ACoS |")
        add("| --- | --- | --- | --- | ---: | ---: | ---: | ---: |")
        for r in a["owner_decision_queue"]:
            add(f"| {_md_cell(r['owner_review_label'])} | {_md_cell(r['campaign'])} "
                f"| {_md_cell(r['ad_group'])} | {_md_cell(r['customer_search_term'])} "
                f"| {_md_cell(r['clicks'])} | {_md_cell(r['spend'])} | {_md_cell(r['sales'])} "
                f"| {_md_cell(r['acos'])} |")
        add("")
    add("## Campaign summary")
    add("")
    add("| Campaign | Currency | Impressions | Clicks | Spend | Orders | Sales | ACoS |")
    add("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in a["campaign_summary"]:
        add(f"| {_md_cell(r['campaign'])} | {_md_cell(r['currency'])} | {_md_cell(r['impressions'])} "
            f"| {_md_cell(r['clicks'])} | {_md_cell(r['spend'])} | {_md_cell(r['orders'])} "
            f"| {_md_cell(r['sales'])} | {_md_cell(r['acos'])} |")
    add("")
    add("## Data quality")
    add("")
    add(f"- Source files: {dq['source_file_count']}")
    add(f"- Source rows: {dq['source_row_count']}")
    add(f"- Analysed rows: {dq['analyzed_row_count']}")
    add(f"- Skipped rows: {dq['skipped_row_count']}")
    add(f"- Invalid numeric values: {dq['invalid_numeric_count']}")
    add(f"- Duplicate canonical row keys: "
        f"{dq['duplicate_handling']['duplicate_canonical_row_key_count']}")
    add("- Undefined (null) rates from a zero denominator: "
        + ", ".join(f"{k}={v}" for k, v in sorted(dq["zero_denominator_counts"].items())))
    if dq["missing_field_counts"]:
        add("- Missing/blank field counts: "
            + ", ".join(f"{k}={v}" for k, v in dq["missing_field_counts"].items()))
    add(f"- Rows outside the {th.lookback_days}-day lookback window: "
        f"{dq['date_coverage']['rows_outside_lookback_window']}")
    add("")
    return "\n".join(lines) + "\n"


# ================================================================ ANALYSIS ORCHESTRATION
class AnalysisResult:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def run_analysis(base_dir, phase7_2_dir, *, reference_date=None, thresholds_path=None,
                 write=True, ensure_dirs=True):
    """Analyze promoted Phase 7.2 output into owner-review reports. Connects to NOTHING."""
    dirs = ensure_workspace(base_dir) if ensure_dirs else workspace_dirs(base_dir)
    if thresholds_path:
        with open(thresholds_path, "rb") as f:
            doc = json.loads(f.read().decode("utf-8"))
        merged = dict(DEFAULT_THRESHOLDS)
        merged.update(doc)
        thresholds, tpath = Thresholds(merged), os.path.abspath(thresholds_path)
    else:
        thresholds, tpath = load_thresholds(dirs[D_CONFIG], write_default=write)

    source = load_source(phase7_2_dir)
    lookback_start = _lookback_start(reference_date, thresholds.lookback_days)

    if source["state"] is not None:
        analysis = _blocked_analysis(source, thresholds, reference_date=reference_date,
                                     lookback_start=lookback_start)
        result = AnalysisResult(dirs=dirs, base_dir=dirs["base"], phase7_2_dir=source["phase7_2_dir"],
                                state=source["state"], thresholds=thresholds,
                                thresholds_path=tpath, source=source, analyzed=[],
                                analysis=analysis, promote_report=None, output_hashes={})
        if write:
            _write_blocked(result)
        return result

    analyzed = [analyze_row(r, thresholds, lookback_start=lookback_start) for r in source["rows"]]
    analyzed = sort_search_terms(analyzed)

    campaign_summary = aggregate(analyzed, ("campaign",))
    ad_group_summary = aggregate(analyzed, ("campaign", "ad_group"))
    queue = sort_decision_queue([a for a in analyzed
                                 if a["owner_review_label"] in DECISION_QUEUE_LABELS])

    classification_counts, review_counts = {}, {}
    for a in analyzed:
        classification_counts[a["primary_classification"]] = \
            classification_counts.get(a["primary_classification"], 0) + 1
        review_counts[a["owner_review_label"]] = review_counts.get(a["owner_review_label"], 0) + 1

    data_quality = build_data_quality(source, analyzed, thresholds,
                                      reference_date=reference_date, lookback_start=lookback_start)
    analysis = {
        "schema_version": SCHEMA_ANALYSIS,
        "stage_id": STAGE_ID,
        "stage_name": STAGE_NAME,
        "analysis_readiness": ANALYSIS_READY,
        "supported_report_types": list(SUPPORTED_REPORT_TYPES),
        "analysed_report_types": source["report_types"],
        "thresholds": thresholds.doc,
        "classification_precedence": list(CLASSIFICATION_PRECEDENCE),
        "classification_counts": dict(sorted(classification_counts.items())),
        "review_label_counts": dict(sorted(review_counts.items())),
        "campaign_summary": campaign_summary,
        "ad_group_summary": ad_group_summary,
        "search_term_analysis": analyzed,
        "owner_decision_queue": queue,
        "data_quality": data_quality,
        "owner_review_labels_only": True,
        "amazon_boundary": dict(_ZERO_COUNTERS),
        "this_session_never": dict(_THIS_SESSION_NEVER),
    }
    _finalize(analysis)

    result = AnalysisResult(dirs=dirs, base_dir=dirs["base"], phase7_2_dir=source["phase7_2_dir"],
                            state=ANALYSIS_READY, thresholds=thresholds, thresholds_path=tpath,
                            source=source, analyzed=analyzed, analysis=analysis,
                            promote_report=None, output_hashes={})
    if write:
        docs = build_documents(result)
        manifest, output_hashes = write_staging(docs, dirs[D_STAGING], result)
        report = promote_staging(dirs[D_STAGING], dirs[D_PROMOTED], output_hashes)
        result.promote_report = report
        result.output_hashes = output_hashes
        result.manifest = manifest
        if report["result"] != "PASS":
            result.state = ANALYSIS_BLOCKED
    return result


def _blocked_analysis(source, thresholds, *, reference_date, lookback_start):
    return {
        "schema_version": SCHEMA_ANALYSIS,
        "stage_id": STAGE_ID,
        "stage_name": STAGE_NAME,
        "analysis_readiness": source["state"],
        "block_detail": source["detail"],
        "source_readiness": source["readiness"],
        "source_report_types": source["report_types"],
        "thresholds": thresholds.doc,
        "campaign_summary": [], "ad_group_summary": [], "search_term_analysis": [],
        "owner_decision_queue": [], "classification_counts": {}, "review_label_counts": {},
        "data_quality": {
            "schema_version": SCHEMA_DATA_QUALITY,
            "source_manifest_identity": {
                "analysis_readiness": source["readiness"],
                "deterministic_content_sha256": (source["manifest"] or {}).get(
                    "deterministic_content_sha256"),
            },
            "source_file_count": len(source["source_files"]),
            "source_row_count": len(source["rows"]),
            "analyzed_row_count": 0, "skipped_row_count": len(source["rows"]),
            "blocked_row_count": len(source["rows"]),
            "missing_field_counts": {}, "invalid_numeric_count": 0,
            "duplicate_handling": {"policy": "NOT_ANALYSED"},
            "zero_denominator_counts": {},
            "date_coverage": {"reference_date": reference_date, "lookback_start": lookback_start},
            "currencies_encountered": [],
        },
        "owner_review_labels_only": True,
        "amazon_boundary": dict(_ZERO_COUNTERS),
        "this_session_never": dict(_THIS_SESSION_NEVER),
    }


def _write_blocked(result):
    """A blocked run records why, in logs/, and never touches promoted/."""
    doc = _finalize(dict(result.analysis))
    path = os.path.join(result.dirs[D_LOGS], "analysis-blocked.json")
    _atomic_write_bytes(path, canonical_json(doc).encode("utf-8"))
    return path


# ================================================================ ARTIFACTS + ATOMIC PROMOTION
def build_documents(result):
    a = result.analysis
    docs = {
        F_SEARCH_TERM_CSV: _csv_bytes(_SEARCH_TERM_COLUMNS,
                                      [_flatten_search_term(r) for r in a["search_term_analysis"]]),
        F_DECISION_QUEUE_CSV: _csv_bytes(_SEARCH_TERM_COLUMNS,
                                         [_flatten_search_term(r) for r in a["owner_decision_queue"]]),
        F_CAMPAIGN_CSV: _csv_bytes(("campaign",) + _SUMMARY_COLUMNS_TAIL, a["campaign_summary"]),
        F_AD_GROUP_CSV: _csv_bytes(("campaign", "ad_group") + _SUMMARY_COLUMNS_TAIL,
                                   a["ad_group_summary"]),
        F_ANALYSIS_JSON: canonical_json(a).encode("utf-8"),
        F_OWNER_REPORT_MD: build_owner_report_md(
            {"analysis": a, "thresholds": result.thresholds}).encode("utf-8"),
    }
    return docs


def build_manifest(result, docs, output_hashes):
    a = result.analysis
    dq = a["data_quality"]
    body = {
        "schema_version": SCHEMA_MANIFEST,
        "stage_id": STAGE_ID,
        "stage_name": STAGE_NAME,
        "analysis_readiness": a["analysis_readiness"],
        "source": {
            "phase7_2_final_dir": os.path.relpath(result.source["final_dir"],
                                                  result.source["phase7_2_dir"]).replace(os.sep, "/"),
            "phase7_2_readiness": result.source["readiness"],
            "phase7_2_manifest_sha256": (result.source["manifest"] or {}).get(
                "deterministic_content_sha256"),
            "phase7_2_verification": result.source["verification"],
            "source_files": result.source["source_files"],
            "raw_inbox_read": False,
        },
        "counts": {
            "source_row_count": dq["source_row_count"],
            "analyzed_row_count": dq["analyzed_row_count"],
            "skipped_row_count": dq["skipped_row_count"],
            "blocked_row_count": dq["blocked_row_count"],
            "decision_queue_row_count": len(a["owner_decision_queue"]),
            "campaign_count": len(a["campaign_summary"]),
            "ad_group_count": len(a["ad_group_summary"]),
        },
        "classification_counts": a["classification_counts"],
        "review_label_counts": a["review_label_counts"],
        "thresholds": a["thresholds"],
        "required_artifacts": sorted(PROMOTED_ARTIFACTS),
        "generated_outputs": sorted(docs),
        "stable_content_hashes": {name: _sha_bytes(data) for name, data in sorted(docs.items())},
        "amazon_boundary": dict(_ZERO_COUNTERS),
        "this_session_never": dict(_THIS_SESSION_NEVER),
        "output_hashes": output_hashes,
    }
    return _finalize(body, extra_volatile=("output_hashes",))


def write_staging(docs, staging_dir, result):
    """Write every artifact atomically into staging/, manifest last."""
    os.makedirs(staging_dir, exist_ok=True)
    for name in list(os.listdir(staging_dir)):
        p = os.path.join(staging_dir, name)
        if os.path.isfile(p):
            os.remove(p)
    output_hashes = {}
    for name in sorted(docs):
        _atomic_write_bytes(os.path.join(staging_dir, name), docs[name])
        output_hashes[name] = _sha_bytes(docs[name])
    manifest = build_manifest(result, docs, output_hashes)
    data = canonical_json(manifest).encode("utf-8")
    _atomic_write_bytes(os.path.join(staging_dir, F_MANIFEST), data)
    output_hashes[F_MANIFEST] = _sha_bytes(data)
    return manifest, output_hashes


def verify_staging(staging_dir, output_hashes, required_artifacts=PROMOTED_ARTIFACTS):
    """Reopen every staged file and verify its bytes. PASS/BLOCKED."""
    missing, mismatched = [], []
    for name in sorted(output_hashes):
        path = os.path.join(staging_dir, name)
        if not os.path.isfile(path):
            missing.append(name)
            continue
        with open(path, "rb") as f:
            if _sha_bytes(f.read()) != output_hashes[name]:
                mismatched.append(name)
    for name in required_artifacts:
        if not os.path.isfile(os.path.join(staging_dir, name)) and name not in missing:
            missing.append(name)
    ok = not missing and not mismatched
    return {"result": "PASS" if ok else "BLOCKED", "missing": sorted(missing),
            "mismatched": sorted(mismatched), "staged_file_count": len(output_hashes)}


def promote_staging(staging_dir, promoted_dir, output_hashes, required_artifacts=PROMOTED_ARTIFACTS):
    """Verify staging, then promote atomically. On ANY failure the prior promoted bytes stand."""
    report = verify_staging(staging_dir, output_hashes, required_artifacts)
    if report["result"] != "PASS":
        report["promoted"] = False
        return report
    os.makedirs(promoted_dir, exist_ok=True)
    for name in list(os.listdir(promoted_dir)):
        p = os.path.join(promoted_dir, name)
        if os.path.isfile(p):
            os.remove(p)
    ordered = [n for n in sorted(output_hashes) if n != F_MANIFEST] + [F_MANIFEST]
    for name in ordered:
        src = os.path.join(staging_dir, name)
        if os.path.isfile(src):
            os.replace(src, os.path.join(promoted_dir, name))
    report["promoted"] = True
    return report


# ================================================================ CLI
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        description="Phase 7.3 offline Sponsored Products analysis over promoted Phase 7.2 output "
                    "(deterministic, offline, owner-review labels only).")
    ap.add_argument("--base-dir", required=True, help="Phase 7.3 workspace (e.g. runs/T2/phase7/7.3)")
    ap.add_argument("--phase7-2-dir", required=True,
                    help="Phase 7.2 workspace whose promoted final/ is the ONLY source")
    ap.add_argument("--reference-date", default=None, help="owner-declared reference date (YYYY-MM-DD)")
    ap.add_argument("--thresholds", default=None, help="optional external threshold config path")
    a = ap.parse_args(argv)
    result = run_analysis(a.base_dir, a.phase7_2_dir, reference_date=a.reference_date,
                          thresholds_path=a.thresholds)
    dq = result.analysis["data_quality"]
    promote = result.promote_report.get("result") if result.promote_report else "n/a"
    print(f"analysis_readiness={result.state}")
    print(f"promote={promote}")
    print(f"source_rows={dq['source_row_count']}")
    print(f"analyzed_rows={dq['analyzed_row_count']}")
    print(f"decision_queue_rows={len(result.analysis['owner_decision_queue'])}")
    print(f"blocked_rows={dq['blocked_row_count']}")
    return 0 if result.state == ANALYSIS_READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
