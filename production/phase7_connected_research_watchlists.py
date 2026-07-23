#!/usr/bin/env python3
"""Phase 7.11 — Connected Research Watchlists, Change Detection & Owner Alerts.

This module is the ONE Phase 7.11 authority. It lets the owner keep a *watchlist* of
Phase 7.10-compatible public sources, run those watchlists on an explicit schedule (or
manually), compare the newest ACCEPTED Phase 7.10 evidence with the previous accepted
evidence, record exact field-level changes, and raise LOCAL owner-rule-based alerts that
the owner can acknowledge, dismiss or reopen with an append-only audit trail.

AUTHORITY REUSE — Phase 7.11 performs NO network work of its own. Every fetch, parse,
network-policy decision, Seller-Central denial, SSRF guard, robots decision, capture and
evidence record comes from the accepted Phase 7.10 authority
(:mod:`production.phase7_connected_public_research`). Phase 7.11 only ORCHESTRATES 7.10
through its documented Python functions, then reads the accepted, verified artefacts.

PERMANENT AMAZON-ACCOUNT BOUNDARY (never crossable, in any path, by any flag or config):
no Seller Central, no Seller Central login, no seller API, no Ads API, no seller-account
OAuth, no seller credentials/cookies/sessions/tokens, no automatic report downloads, no
bulk uploads, no browser automation, no campaign/bid/budget/target/keyword/negative
changes, no listing/inventory mutation, no automated mutation of an Amazon seller account.
The Amazon-account denial lives in Phase 7.10 / :mod:`core.network_policy` and always wins
before any allowlist decision. Every Phase 7.11 record carries constant-zero Amazon
counters — no code path can increment them.

Phase 7.11 NEVER: invents demand; estimates sales/revenue/conversion/search-volume/
inventory; calculates opportunity/competitor/viability scores; makes campaign/bid/budget
recommendations; launches a browser; executes JavaScript; crawls linked pages; opens a
second HTTP transport; scrapes a URL directly; registers an OS scheduled task; installs a
background service; or sends any email/webhook/chat notification. Alerts are LOCAL only.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import sys
import time
import unicodedata
from decimal import Decimal, ROUND_HALF_UP

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except Exception:  # pragma: no cover - zoneinfo is stdlib on 3.9+
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ("", "core", "production"):
    _p = _ROOT if not _sub else os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import phase7_connected_public_research as R   # noqa: E402  the ONE 7.10 authority
import network_policy as NP                                    # noqa: E402  canonical Amazon-deny
import diagnostics as D                                        # noqa: E402  secret redaction + codes
import money as M                                              # noqa: E402  Decimal-only numeric safety

# ================================================================ identity / versions
STAGE_ID = "7.11"
STAGE_NAME = "Connected Research Watchlists, Change Detection & Owner Alerts"
TOOL_VERSION = "phase7.11.0"
COMPARISON_ENGINE_VERSION = "phase7.11-compare-v1"

WATCHLIST_SCHEMA = "phase7-11-watchlist-v1"
STATE_SCHEMA = "phase7-11-watchlist-state-v1"
EXECUTION_SCHEMA = "phase7-11-execution-v1"
COMPARISON_SCHEMA = "phase7-11-comparison-v1"
CHANGE_SCHEMA = "phase7-11-change-event-v1"
ALERT_SCHEMA = "phase7-11-alert-v1"
ALERT_STATE_SCHEMA = "phase7-11-alert-state-v1"
SNAPSHOT_SCHEMA = "phase7-11-snapshot-v1"
SCHEDULER_PLAN_SCHEMA = "phase7-11-scheduler-plan-v1"
VALIDATION_SCHEMA = "phase7-11-validation-v1"

# ---------------------------------------------------------------- readiness states
READY = "SESSION7_11_WATCHLIST_READY"
READY_EMPTY = "SESSION7_11_WATCHLIST_READY_EMPTY"
READY_PARTIAL = "SESSION7_11_WATCHLIST_READY_PARTIAL"
WATCHLIST_REQUIRED = "SESSION7_11_WATCHLIST_REQUIRED"
WATCHLIST_BLOCKED = "SESSION7_11_WATCHLIST_BLOCKED"
NOT_DUE = "SESSION7_11_NOT_DUE"
SOURCE_REQUIRED = "SESSION7_11_SOURCE_REQUIRED"
SOURCE_BLOCKED = "SESSION7_11_SOURCE_BLOCKED"
NETWORK_UNAVAILABLE = "SESSION7_11_NETWORK_UNAVAILABLE"
RESEARCH_RUN_BLOCKED = "SESSION7_11_RESEARCH_RUN_BLOCKED"
BASELINE_REQUIRED = "SESSION7_11_BASELINE_REQUIRED"
COMPARISON_READY = "SESSION7_11_COMPARISON_READY"
COMPARISON_READY_EMPTY = "SESSION7_11_COMPARISON_READY_EMPTY"
COMPARISON_BLOCKED = "SESSION7_11_COMPARISON_BLOCKED"
ALERTS_READY = "SESSION7_11_ALERTS_READY"
ALERTS_READY_EMPTY = "SESSION7_11_ALERTS_READY_EMPTY"
ALERT_STATE_BLOCKED = "SESSION7_11_ALERT_STATE_BLOCKED"
INTEGRITY_BLOCKED = "SESSION7_11_INTEGRITY_BLOCKED"
SELLER_CENTRAL_POLICY_BLOCKED = "SESSION7_11_SELLER_CENTRAL_POLICY_BLOCKED"
# operational-success readiness for read/utility commands
LIST_READY = "SESSION7_11_LIST_READY"
SHOW_READY = "SESSION7_11_SHOW_READY"
VERIFY_READY = "SESSION7_11_VERIFY_READY"
EXPORT_READY = "SESSION7_11_EXPORT_READY"
SCHEDULER_PLAN_READY = "SESSION7_11_SCHEDULER_PLAN_READY"
VALIDATE_READY = "SESSION7_11_VALIDATE_READY"
ACK_READY = "SESSION7_11_ALERT_ACTION_READY"

# readiness values that mean success -> process exit 0. Everything else exits nonzero.
_EXIT_OK = frozenset({
    READY, READY_EMPTY, READY_PARTIAL, NOT_DUE, COMPARISON_READY, COMPARISON_READY_EMPTY,
    ALERTS_READY, ALERTS_READY_EMPTY, LIST_READY, SHOW_READY, VERIFY_READY, EXPORT_READY,
    SCHEDULER_PLAN_READY, VALIDATE_READY, ACK_READY,
})

# ---------------------------------------------------------------- change-event statuses
CHG_INITIAL_BASELINE = "INITIAL_BASELINE"
CHG_ADDED = "ADDED"
CHG_REMOVED = "REMOVED"
CHG_CHANGED = "CHANGED"
CHG_UNCHANGED = "UNCHANGED"
CHG_SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
CHG_SOURCE_BLOCKED = "SOURCE_BLOCKED"
CHG_PARSE_PARTIAL = "PARSE_PARTIAL"
CHG_INTEGRITY_BLOCKED = "INTEGRITY_BLOCKED"
CHG_COMPARISON_NOT_AVAILABLE = "COMPARISON_NOT_AVAILABLE"
_CHANGE_STATUSES = frozenset({
    CHG_INITIAL_BASELINE, CHG_ADDED, CHG_REMOVED, CHG_CHANGED, CHG_UNCHANGED,
    CHG_SOURCE_UNAVAILABLE, CHG_SOURCE_BLOCKED, CHG_PARSE_PARTIAL, CHG_INTEGRITY_BLOCKED,
    CHG_COMPARISON_NOT_AVAILABLE,
})
# statuses that represent an actionable evidence delta (drive READY vs READY_EMPTY)
_ACTIONABLE_CHANGES = frozenset({CHG_ADDED, CHG_REMOVED, CHG_CHANGED})
_CAPTURE_FIELD = "__capture__"

# ---------------------------------------------------------------- alert model
ALERT_OPEN = "OPEN"
ALERT_ACKNOWLEDGED = "ACKNOWLEDGED"
ALERT_DISMISSED = "DISMISSED"
_ALERT_STATUSES = frozenset({ALERT_OPEN, ALERT_ACKNOWLEDGED, ALERT_DISMISSED})

ACT_ACKNOWLEDGE = "ACKNOWLEDGE"
ACT_DISMISS = "DISMISS"
ACT_REOPEN = "REOPEN"
_ACTION_TO_STATUS = {ACT_ACKNOWLEDGE: ALERT_ACKNOWLEDGED, ACT_DISMISS: ALERT_DISMISSED,
                     ACT_REOPEN: ALERT_OPEN}

# owner-defined severities — never computed / scored by the system
SEV_INFO = "INFO"
SEV_REVIEW = "REVIEW"
SEV_IMPORTANT = "IMPORTANT"
SEV_CRITICAL = "CRITICAL"
_SEVERITIES = frozenset({SEV_INFO, SEV_REVIEW, SEV_IMPORTANT, SEV_CRITICAL})

# owner alert-rule operators — bounded, no arbitrary regular expressions
OP_FIELD_CHANGED = "field-changed"
OP_FIELD_ADDED = "field-added"
OP_FIELD_REMOVED = "field-removed"
OP_VALUE_EQUALS = "value-equals"
OP_VALUE_CONTAINS = "value-contains"
OP_NUMERIC_ABOVE = "numeric-above"
OP_NUMERIC_BELOW = "numeric-below"
OP_ABS_DELTA = "absolute-delta-at-least"
OP_PCT_CHANGE = "percent-change-at-least"
OP_SOURCE_UNAVAILABLE = "source-unavailable"
OP_SOURCE_BLOCKED = "source-blocked"
OP_PARSE_PARTIAL = "parse-partial"
OP_INTEGRITY_BLOCKED = "integrity-blocked"
_RULE_OPERATORS = frozenset({
    OP_FIELD_CHANGED, OP_FIELD_ADDED, OP_FIELD_REMOVED, OP_VALUE_EQUALS, OP_VALUE_CONTAINS,
    OP_NUMERIC_ABOVE, OP_NUMERIC_BELOW, OP_ABS_DELTA, OP_PCT_CHANGE, OP_SOURCE_UNAVAILABLE,
    OP_SOURCE_BLOCKED, OP_PARSE_PARTIAL, OP_INTEGRITY_BLOCKED,
})
_NUMERIC_OPERATORS = frozenset({OP_NUMERIC_ABOVE, OP_NUMERIC_BELOW, OP_ABS_DELTA, OP_PCT_CHANGE})
_STATUS_OPERATORS = {
    OP_SOURCE_UNAVAILABLE: CHG_SOURCE_UNAVAILABLE, OP_SOURCE_BLOCKED: CHG_SOURCE_BLOCKED,
    OP_PARSE_PARTIAL: CHG_PARSE_PARTIAL, OP_INTEGRITY_BLOCKED: CHG_INTEGRITY_BLOCKED,
}

# ---------------------------------------------------------------- schema key sets
_WATCHLIST_TOP_KEYS = frozenset({
    "schema_version", "watchlist_id", "name", "description", "enabled", "timezone", "schedule",
    "sources", "comparison_policy", "alert_rules", "tags", "created_by", "owner_notes",
})
# fields that form the STABLE watchlist identity (annotations/mutable metadata excluded)
_WATCHLIST_IDENTITY_KEYS = ("name", "timezone", "schedule", "sources", "comparison_policy",
                            "alert_rules", "tags")
_SOURCE_ITEM_KEYS = frozenset({
    "source_type", "source_locator", "label", "tags", "parser_options", "expected_content_type",
    "maximum_bytes", "enabled", "comparison_policy",
})
_SCHEDULE_KEYS = frozenset({"type", "interval_hours", "local_time", "weekdays", "timezone",
                            "catch_up_policy", "enabled"})
_COMPARISON_POLICY_KEYS = frozenset({"case_insensitive_field_paths", "ignored_field_paths"})
_RULE_KEYS = frozenset({
    "rule_id", "enabled", "source_selector", "evidence_type", "field_path", "operator",
    "comparison_value", "unit", "currency", "severity", "label", "message",
})

_SCHEDULE_TYPES = frozenset({"manual", "hourly", "daily", "weekly", "interval-hours"})
_CATCH_UP_POLICIES = frozenset({"skip", "single"})
MIN_INTERVAL_HOURS = 1
_WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# forbidden substrings for the watchlist deep-scan (secrets / headers / commands / cookies).
# Reuses the accepted Phase 7.10 plan-forbidden set and adds command/shell/exec/path shapes.
_FORBIDDEN_SUBSTR = tuple(sorted(set(R._PLAN_FORBIDDEN_SUBSTR) | {
    "shell", "subprocess", "eval", "exec", "import", "argv", "__", "dynamic",
}))

MAX_LOCK_AGE_SECONDS = 6 * 3600           # a lock older than this is *reported* stale (never auto-removed)
MAX_CATCH_UP = 1                          # bounded catch-up: at most one run per due invocation

DISCLAIMERS = (
    "Phase 7.11 watches PUBLIC evidence via the accepted Phase 7.10 authority and records exact "
    "field-level changes only. It never invents demand, estimates sales/revenue/conversion/search "
    "volume/inventory, scores opportunity/competition, or makes campaign/bid/budget recommendations.",
    "No data here came from Amazon Seller Central. The toolkit never connects to an Amazon seller "
    "account, seller API, ads API, or seller credentials, cookies, sessions or tokens.",
    "Alerts are LOCAL owner alerts driven only by explicit owner-configured rules. Owner severity is "
    "owner-defined; the system never calculates a severity, urgency or score.",
    "A first successful observation is an INITIAL_BASELINE, not a change. A source-content hash change "
    "with no accepted evidence change is recorded as source-content changed but evidence unchanged.",
)


# ================================================================ errors
class WatchlistError(Exception):
    """A safe, secret-free Phase 7.11 failure carrying a stable ``code`` and redacted detail."""

    def __init__(self, code, detail="", *, readiness=WATCHLIST_BLOCKED):
        self.code = code
        self.detail = R.redact(detail)
        self.readiness = readiness
        super().__init__(f"{code}: {self.detail}" if self.detail else code)


# ================================================================ small helpers (identity is content-only)
def canonical_json(obj):
    return R.canonical_json(obj)


def _sha(obj):
    return R._sha256_hex(canonical_json(obj))


def _cid(prefix, obj):
    """A stable, content-addressed identifier. Never depends on time, mtime, path, pid or order."""
    return prefix + "-" + _sha(obj)[:24]


def _now_epoch(cfg):
    return float(cfg.now_fn())


def _iso_utc(dt=None):
    """A UTC timestamp for OPERATIONAL metadata only — never part of any identity or export hash."""
    dt = dt or _dt.datetime.now(_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _redact(obj):
    return R.redact(obj)


def _write_json(path, obj):
    R._atomic_write_json(path, obj)


def _write_text(path, text):
    R._atomic_write_text(path, text)


def _load_json(path, what="json"):
    with open(path, "rb") as f:
        return R._loads_strict(f.read(), what)


def _hashed(obj):
    """Return a copy of *obj* with an ``integrity_hash`` over its content (hash field excluded)."""
    body = {k: v for k, v in obj.items() if k != "integrity_hash"}
    out = dict(body)
    out["integrity_hash"] = R.content_sha256(body)
    return out


def _write_hashed_json(path, obj):
    _write_json(path, _hashed(obj))


def _load_hashed_json(path, what="json", *, readiness=INTEGRITY_BLOCKED):
    rec = _load_json(path, what)
    if not isinstance(rec, dict) or "integrity_hash" not in rec:
        raise WatchlistError("STATE_INTEGRITY_MISSING", what, readiness=readiness)
    body = {k: v for k, v in rec.items() if k != "integrity_hash"}
    if R.content_sha256(body) != rec["integrity_hash"]:
        raise WatchlistError("STATE_INTEGRITY_MISMATCH", what, readiness=readiness)
    return body


def _rel(cfg, path):
    try:
        return os.path.relpath(path, cfg.base_dir).replace(os.sep, "/")
    except ValueError:
        return os.path.basename(path)


# ================================================================ configuration
class Config:
    """Phase 7.11 workspace + injectable seams. ``research_dir`` is the accepted, READ-ONLY Phase
    7.10 workspace (existing runs). New watchlist fetches are written to a 7.11-OWNED Phase 7.10
    workspace under ``base_dir/research`` so the accepted 7.10 tree is never modified. The transport
    / resolver seams are handed to the reused Phase 7.10 authority so no unit test needs live net."""

    __slots__ = ("base_dir", "research_dir", "env", "transport", "resolver", "allow_local",
                 "input_root", "now_fn", "pid")

    def __init__(self, base_dir, *, research_dir=None, env=None, transport=None, resolver=None,
                 allow_local=False, input_root=None, now_fn=None, pid=None):
        self.base_dir = os.path.abspath(base_dir)
        self.research_dir = os.path.abspath(research_dir) if research_dir else None
        self.env = dict(env) if env is not None else dict(os.environ)
        self.transport = transport
        self.resolver = resolver
        self.allow_local = bool(allow_local)
        self.input_root = os.path.abspath(input_root) if input_root else None
        self.now_fn = now_fn or time.time
        self.pid = int(pid) if pid is not None else os.getpid()

    def _sub(self, name):
        return os.path.join(self.base_dir, name)

    watchlists_dir = property(lambda self: self._sub("watchlists"))
    state_dir = property(lambda self: self._sub("state"))
    executions_dir = property(lambda self: self._sub("executions"))
    comparisons_dir = property(lambda self: self._sub("comparisons"))
    changes_dir = property(lambda self: self._sub("changes"))
    alerts_dir = property(lambda self: self._sub("alerts"))
    alert_history_dir = property(lambda self: self._sub("alert_history"))
    reports_dir = property(lambda self: self._sub("reports"))
    scheduler_plans_dir = property(lambda self: self._sub("scheduler_plans"))
    validation_dir = property(lambda self: self._sub("validation"))
    locks_dir = property(lambda self: self._sub("locks"))
    logs_dir = property(lambda self: self._sub("logs"))
    research_runs_dir = property(lambda self: self._sub("research"))

    def research_config(self, base):
        """Build a reused Phase 7.10 Config bound to *base*, wiring the injected transport seams."""
        return R.Config(base, env=self.env, transport=self.transport, resolver=self.resolver,
                        allow_local=self.allow_local, input_root=self.input_root,
                        host_gate=R.HostGate())


# ================================================================ validation: forbidden-field scan
def _reject_forbidden(obj, what):
    bad = R._scan_forbidden(obj)
    if bad:
        raise WatchlistError("FORBIDDEN_FIELD", f"{what}: {bad}", readiness=WATCHLIST_BLOCKED)


# ================================================================ validation: comparison policy
def _normalize_comparison_policy(policy, where):
    if policy is None:
        return {"case_insensitive_field_paths": [], "ignored_field_paths": []}
    if not isinstance(policy, dict):
        raise WatchlistError("COMPARISON_POLICY_INVALID", f"{where}: must be an object")
    unknown = set(policy) - _COMPARISON_POLICY_KEYS
    if unknown:
        raise WatchlistError("COMPARISON_POLICY_UNKNOWN_FIELD", f"{where}: {sorted(unknown)}")
    out = {}
    for k in ("case_insensitive_field_paths", "ignored_field_paths"):
        v = policy.get(k, [])
        if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
            raise WatchlistError("COMPARISON_POLICY_INVALID", f"{where}.{k}: must be a list of strings")
        out[k] = sorted(set(v))
    return out


# ================================================================ validation: schedule
def _validate_timezone(tzname, where):
    if not isinstance(tzname, str) or not tzname.strip():
        raise WatchlistError("TIMEZONE_INVALID", f"{where}: timezone must be a non-empty string")
    if ZoneInfo is None:  # pragma: no cover
        raise WatchlistError("TIMEZONE_UNSUPPORTED", "zoneinfo unavailable")
    try:
        return ZoneInfo(tzname)
    except (ZoneInfoNotFoundError, ValueError, OSError, KeyError):
        raise WatchlistError("TIMEZONE_INVALID", f"{where}: unknown timezone {tzname!r}")


def _normalize_weekdays(value, where):
    if not isinstance(value, list) or not value:
        raise WatchlistError("SCHEDULE_INVALID", f"{where}: weekly schedule requires a weekdays list")
    out = set()
    for w in value:
        if isinstance(w, bool):
            raise WatchlistError("SCHEDULE_INVALID", f"{where}: weekday must be int 0-6 or name")
        if isinstance(w, int):
            if not 0 <= w <= 6:
                raise WatchlistError("SCHEDULE_INVALID", f"{where}: weekday out of range: {w}")
            out.add(w)
        elif isinstance(w, str) and w.strip().lower()[:3] in _WEEKDAY_NAMES:
            out.add(_WEEKDAY_NAMES.index(w.strip().lower()[:3]))
        else:
            raise WatchlistError("SCHEDULE_INVALID", f"{where}: invalid weekday {w!r}")
    return sorted(out)


def _parse_local_time(value, where):
    if not isinstance(value, str) or not re.match(r"^\d{1,2}:\d{2}$", value.strip()):
        raise WatchlistError("SCHEDULE_INVALID", f"{where}: local_time must be 'HH:MM'")
    hh, mm = (int(x) for x in value.strip().split(":"))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise WatchlistError("SCHEDULE_INVALID", f"{where}: local_time out of range")
    return hh, mm


def _normalize_schedule(schedule, default_tz, where="schedule"):
    """Validate + canonicalize a schedule. Returns a canonical dict. Enforces the 1-hour minimum
    automatic interval and rejects unknown fields / invalid timezones."""
    if not isinstance(schedule, dict):
        raise WatchlistError("SCHEDULE_INVALID", f"{where}: must be an object")
    unknown = set(schedule) - _SCHEDULE_KEYS
    if unknown:
        raise WatchlistError("SCHEDULE_UNKNOWN_FIELD", f"{where}: {sorted(unknown)}")
    st = schedule.get("type")
    if st not in _SCHEDULE_TYPES:
        raise WatchlistError("SCHEDULE_INVALID", f"{where}: invalid schedule type {st!r}")
    tzname = schedule.get("timezone", default_tz)
    _validate_timezone(tzname, where)
    catch_up = schedule.get("catch_up_policy", "single")
    if catch_up not in _CATCH_UP_POLICIES:
        raise WatchlistError("SCHEDULE_INVALID", f"{where}: invalid catch_up_policy {catch_up!r}")
    out = {"type": st, "timezone": tzname, "catch_up_policy": catch_up,
           "enabled": bool(schedule.get("enabled", True))}
    if st == "interval-hours":
        ih = schedule.get("interval_hours")
        if not isinstance(ih, int) or isinstance(ih, bool) or ih < MIN_INTERVAL_HOURS:
            raise WatchlistError("SCHEDULE_MIN_INTERVAL",
                                 f"{where}: interval_hours must be an int >= {MIN_INTERVAL_HOURS}")
        out["interval_hours"] = ih
    elif st == "hourly":
        out["interval_hours"] = 1
    elif st in ("daily", "weekly"):
        out["local_time"] = "%02d:%02d" % _parse_local_time(schedule.get("local_time", "09:00"), where)
        if st == "weekly":
            out["weekdays"] = _normalize_weekdays(schedule.get("weekdays"), where)
    return out


# ================================================================ validation: alert rules
def _validate_rule(rule, index):
    if not isinstance(rule, dict):
        raise WatchlistError("ALERT_RULE_INVALID", f"rule[{index}] must be an object")
    unknown = set(rule) - _RULE_KEYS
    if unknown:
        raise WatchlistError("ALERT_RULE_UNKNOWN_FIELD", f"rule[{index}]: {sorted(unknown)}")
    op = rule.get("operator")
    if op not in _RULE_OPERATORS:
        raise WatchlistError("ALERT_RULE_OPERATOR_UNKNOWN", f"rule[{index}]: {op!r}")
    sev = rule.get("severity", SEV_REVIEW)
    if sev not in _SEVERITIES:
        raise WatchlistError("ALERT_RULE_SEVERITY_INVALID", f"rule[{index}]: {sev!r}")
    sel = rule.get("source_selector", "*")
    if not isinstance(sel, str) or not sel.strip():
        raise WatchlistError("ALERT_RULE_INVALID", f"rule[{index}]: source_selector must be a string")
    etype = rule.get("evidence_type", "*")
    fpath = rule.get("field_path", "*")
    for key, val in (("evidence_type", etype), ("field_path", fpath)):
        if not isinstance(val, str) or not val.strip():
            raise WatchlistError("ALERT_RULE_INVALID", f"rule[{index}]: {key} must be a non-empty string")
    unit = rule.get("unit")
    currency = rule.get("currency")
    for key, val in (("unit", unit), ("currency", currency)):
        if val is not None and (not isinstance(val, str) or not val.strip()):
            raise WatchlistError("ALERT_RULE_INVALID", f"rule[{index}]: {key} must be a string")
    cmp_value = rule.get("comparison_value")
    if op in (OP_VALUE_EQUALS, OP_VALUE_CONTAINS):
        if not isinstance(cmp_value, str) or cmp_value == "":
            raise WatchlistError("ALERT_RULE_INVALID",
                                 f"rule[{index}]: {op} requires a non-empty string comparison_value")
    elif op in _NUMERIC_OPERATORS:
        try:
            dv = M.parse_decimal_string(cmp_value, field=f"rule[{index}].comparison_value",
                                        allow_missing=False)
        except M.MoneyError as e:
            raise WatchlistError("ALERT_RULE_INVALID", f"rule[{index}]: {e}")
        if op == OP_PCT_CHANGE and dv < 0:
            raise WatchlistError("ALERT_RULE_INVALID", f"rule[{index}]: percent threshold must be >= 0")
        if op == OP_ABS_DELTA and dv < 0:
            raise WatchlistError("ALERT_RULE_INVALID", f"rule[{index}]: absolute-delta threshold must be >= 0")
        cmp_value = M.decimal_to_canonical_string(dv)
    else:
        cmp_value = None if cmp_value is None else str(cmp_value)
    rid_basis = {"source_selector": sel.strip(), "evidence_type": etype.strip(),
                 "field_path": fpath.strip(), "operator": op, "comparison_value": cmp_value,
                 "unit": unit, "currency": currency, "severity": sev}
    rule_id = _cid("rule", rid_basis)
    return {
        "rule_id": rule_id,
        "enabled": bool(rule.get("enabled", True)),
        "source_selector": sel.strip(),
        "evidence_type": etype.strip(),
        "field_path": fpath.strip(),
        "operator": op,
        "comparison_value": cmp_value,
        "unit": unit,
        "currency": currency,
        "severity": sev,
        "label": str(rule.get("label", "")),
        "message": str(rule.get("message", "")),
    }


# ================================================================ validation: sources -> 7.10 descriptors + items
def _descriptor_710(src, index):
    """Extract the accepted Phase 7.10 descriptor from a watchlist source and validate it through
    the reused 7.10 normalizer (the ONE source-descriptor authority). Raises on anything 7.10 would
    reject — arbitrary URL, seller-central source, bad locator, unknown source type."""
    d710 = {k: src[k] for k in R._DESCRIPTOR_KEYS if k in src}
    try:
        return R._normalize_descriptor(d710, index)
    except R.PlanError as e:
        raise WatchlistError("SOURCE_DESCRIPTOR_REJECTED", f"source[{index}]: {e.code}: {e.detail}",
                             readiness=SOURCE_BLOCKED)


def _normalize_source(src, index, default_policy):
    if not isinstance(src, dict):
        raise WatchlistError("SOURCE_INVALID", f"source[{index}] must be an object")
    unknown = set(src) - _SOURCE_ITEM_KEYS
    if unknown:
        raise WatchlistError("SOURCE_UNKNOWN_FIELD", f"source[{index}]: {sorted(unknown)}")
    d710 = _descriptor_710(src, index)
    policy = _normalize_comparison_policy(src.get("comparison_policy", default_policy),
                                          f"source[{index}].comparison_policy")
    # a stable source identity (matches the reused 7.10 run manifest's source_id)
    normalized_locator = R._normalize_locator(d710["source_type"], d710["source_locator"])
    source_id = R._source_id(d710["source_type"], normalized_locator)
    return {"descriptor": d710, "comparison_policy": policy,
            "normalized_locator": normalized_locator, "source_id": source_id}


def validate_watchlist(defn):
    """Validate + canonicalize a watchlist definition. Returns (canonical_watchlist, watchlist_id).

    Rejects unknown fields, secret/header/cookie/command fields, unknown source types, invalid
    Phase 7.10 sources, invalid schedules/timezones and invalid alert rules. Reordered JSON keys
    produce the SAME watchlist_id (canonical form); the id never depends on a timestamp, mtime,
    temporary path, pid, random uuid or filesystem order."""
    if not isinstance(defn, dict):
        raise WatchlistError("WATCHLIST_INVALID", "watchlist must be a JSON object")
    _reject_forbidden(defn, "watchlist")
    unknown = set(defn) - _WATCHLIST_TOP_KEYS
    if unknown:
        raise WatchlistError("WATCHLIST_UNKNOWN_FIELD", f"unknown field(s): {sorted(unknown)}")
    name = defn.get("name")
    if not isinstance(name, str) or not name.strip():
        raise WatchlistError("WATCHLIST_INVALID", "watchlist requires a non-empty name")
    tzname = defn.get("timezone", "UTC")
    _validate_timezone(tzname, "watchlist")
    default_policy = _normalize_comparison_policy(defn.get("comparison_policy"), "comparison_policy")
    schedule = _normalize_schedule(defn.get("schedule", {"type": "manual"}), tzname)
    raw_sources = defn.get("sources", [])
    if not isinstance(raw_sources, list):
        raise WatchlistError("SOURCE_INVALID", "sources must be a list")
    norm_sources = [_normalize_source(s, i, default_policy) for i, s in enumerate(raw_sources)]
    raw_rules = defn.get("alert_rules", [])
    if not isinstance(raw_rules, list):
        raise WatchlistError("ALERT_RULE_INVALID", "alert_rules must be a list")
    rules = [_validate_rule(r, i) for i, r in enumerate(raw_rules)]
    tags = defn.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        raise WatchlistError("WATCHLIST_INVALID", "tags must be a list of strings")

    identity_sources = [{"descriptor": s["descriptor"], "comparison_policy": s["comparison_policy"]}
                        for s in norm_sources]
    watchlist_id = _watchlist_identity(name.strip(), tzname, schedule, identity_sources,
                                       default_policy, rules, sorted(set(tags)))

    items = []
    for i, s in enumerate(norm_sources):
        item_id = _cid("item", {"watchlist_id": watchlist_id, "descriptor": s["descriptor"],
                                "label": s["descriptor"].get("label"),
                                "comparison_policy": s["comparison_policy"]})
        items.append({
            "item_id": item_id,
            "source_id": s["source_id"],
            "normalized_locator": s["normalized_locator"],
            "descriptor": s["descriptor"],
            "comparison_policy": s["comparison_policy"],
        })

    canonical = {
        "schema_version": WATCHLIST_SCHEMA,
        "watchlist_id": watchlist_id,
        "name": name.strip(),
        "description": str(defn.get("description", "")),
        "enabled": bool(defn.get("enabled", True)),
        "timezone": tzname,
        "schedule": schedule,
        "comparison_policy": default_policy,
        "sources": items,
        "alert_rules": rules,
        "tags": sorted(set(tags)),
        "created_by": str(defn.get("created_by", "")),
        "owner_notes": str(defn.get("owner_notes", "")),
    }
    return canonical, watchlist_id


_RULE_IDENTITY_KEYS = ("source_selector", "evidence_type", "field_path", "operator",
                       "comparison_value", "unit", "currency", "severity")


def _watchlist_identity(name, tzname, schedule, identity_sources, comparison_policy, rules, tags):
    """The ONE stable watchlist-identity function. Annotations (description, notes, created_by,
    enabled) are excluded so toggling them keeps the id; reordered JSON keys give the SAME id."""
    identity = {
        "name": name, "timezone": tzname, "schedule": schedule, "sources": identity_sources,
        "comparison_policy": comparison_policy,
        "alert_rules": [{k: r[k] for k in _RULE_IDENTITY_KEYS} for r in rules],
        "tags": sorted(set(tags)),
    }
    return _cid("wl", identity)


def _recompute_watchlist_id(wl):
    identity_sources = [{"descriptor": s["descriptor"], "comparison_policy": s["comparison_policy"]}
                        for s in wl["sources"]]
    return _watchlist_identity(wl["name"], wl["timezone"], wl["schedule"], identity_sources,
                               wl["comparison_policy"], wl["alert_rules"], wl.get("tags", []))


# ================================================================ watchlist persistence
def _watchlist_path(cfg, watchlist_id):
    return os.path.join(cfg.watchlists_dir, watchlist_id + ".json")


def load_watchlist(cfg, watchlist_id):
    path = _watchlist_path(cfg, watchlist_id)
    if not os.path.isfile(path):
        raise WatchlistError("WATCHLIST_NOT_FOUND", watchlist_id, readiness=WATCHLIST_REQUIRED)
    rec = _load_json(path, "watchlist")
    # recompute identity from the stored (already-normalized) content — a tampered watchlist file
    # cannot be trusted; a mismatch is an integrity block, never a silent bad run.
    if not isinstance(rec, dict) or "sources" not in rec:
        raise WatchlistError("WATCHLIST_INVALID", watchlist_id, readiness=INTEGRITY_BLOCKED)
    try:
        recomputed = _recompute_watchlist_id(rec)
    except (KeyError, TypeError):
        raise WatchlistError("WATCHLIST_INVALID", watchlist_id, readiness=INTEGRITY_BLOCKED)
    if recomputed != rec.get("watchlist_id"):
        raise WatchlistError("WATCHLIST_IDENTITY_MISMATCH", watchlist_id, readiness=INTEGRITY_BLOCKED)
    return rec


def create_watchlist(cfg, defn):
    canonical, watchlist_id = validate_watchlist(defn)
    path = _watchlist_path(cfg, watchlist_id)
    if os.path.isfile(path):
        existing = _load_json(path, "watchlist")
        same = canonical_json({k: v for k, v in existing.items() if k != "schema_version"}) == \
            canonical_json({k: v for k, v in canonical.items() if k != "schema_version"})
        return {"command": "create-watchlist", "readiness": READY if same else WATCHLIST_BLOCKED,
                "ok": same, "watchlist_id": watchlist_id,
                "detail": "IDEMPOTENT_REUSE" if same else "DUPLICATE_WATCHLIST_ID_DIFFERENT_CONTENT",
                "amazon_counters": dict(R.AMAZON_COUNTERS)}
    _write_json(path, canonical)
    _log(cfg, "create-watchlist", {"watchlist_id": watchlist_id, "sources": len(canonical["sources"])})
    return {"command": "create-watchlist", "readiness": READY, "ok": True,
            "watchlist_id": watchlist_id, "name": canonical["name"],
            "source_count": len(canonical["sources"]), "rule_count": len(canonical["alert_rules"]),
            "amazon_counters": dict(R.AMAZON_COUNTERS)}


def list_watchlists(cfg):
    out = []
    root = cfg.watchlists_dir
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if not name.endswith(".json"):
                continue
            try:
                rec = _load_json(os.path.join(root, name), "watchlist")
                out.append({"watchlist_id": rec.get("watchlist_id"), "name": rec.get("name"),
                            "enabled": rec.get("enabled"), "schedule_type": rec.get("schedule", {}).get("type"),
                            "source_count": len(rec.get("sources", [])),
                            "rule_count": len(rec.get("alert_rules", []))})
            except (WatchlistError, ValueError, OSError):
                continue
    return {"command": "list-watchlists", "readiness": LIST_READY, "ok": True, "count": len(out),
            "watchlists": out, "amazon_counters": dict(R.AMAZON_COUNTERS)}


def show_watchlist(cfg, watchlist_id):
    wl = load_watchlist(cfg, watchlist_id)
    return {"command": "show-watchlist", "readiness": SHOW_READY, "ok": True, "watchlist": wl,
            "amazon_counters": dict(R.AMAZON_COUNTERS)}


def _log(cfg, operation, record):
    try:
        os.makedirs(cfg.logs_dir, exist_ok=True)
        import json as _json
        line = _json.dumps({"ts": _iso_utc(), "op": str(operation), **_redact(record)},
                           ensure_ascii=False, sort_keys=True)
        with open(os.path.join(cfg.logs_dir, "operations.jsonl"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ================================================================ schedule: due / next-due
def _parse_iso(value, where):
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        dt = value
    else:
        s = str(value).strip().replace("Z", "+00:00")
        try:
            dt = _dt.datetime.fromisoformat(s)
        except ValueError:
            raise WatchlistError("REFERENCE_TIME_INVALID", f"{where}: not ISO-8601: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)


def _daily_instants(reference_local, hh, mm, tz, back_days=2, fwd_days=2):
    base = reference_local.date()
    out = []
    for delta in range(-back_days, fwd_days + 1):
        day = base + _dt.timedelta(days=delta)
        out.append(_dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz))
    return out


def compute_due(schedule, tzname, last_run_iso, reference_iso, *, forced=False):
    """Return a due decision for a schedule. Deterministic given (schedule, tz, last_run, reference).

    Schedule timestamps are OPERATIONAL: they influence due/next-due only and never any watchlist,
    source, evidence, change or alert identity. Handles clock-backward (never negative intervals),
    DST forward/backward (aware local arithmetic), bounded catch-up (at most one run per invocation),
    and manual/hourly/daily/weekly/interval-hours schedules."""
    reference = _parse_iso(reference_iso, "reference_time") or _parse_iso(_iso_utc(), "reference_time")
    last_run = _parse_iso(last_run_iso, "last_run")
    tz = _validate_timezone(schedule.get("timezone", tzname), "schedule")

    if forced:
        return {"due": True, "reason": "MANUAL_FORCE", "next_due": None, "missed": 0,
                "reference_time": _iso_utc(reference)}
    if not schedule.get("enabled", True):
        return {"due": False, "reason": "SCHEDULE_DISABLED", "next_due": None, "missed": 0,
                "reference_time": _iso_utc(reference)}
    stype = schedule["type"]
    if stype == "manual":
        return {"due": False, "reason": "MANUAL_ONLY", "next_due": None, "missed": 0,
                "reference_time": _iso_utc(reference)}

    if stype in ("hourly", "interval-hours"):
        interval = _dt.timedelta(hours=schedule.get("interval_hours", 1))
        if last_run is None:
            return {"due": True, "reason": "FIRST_RUN", "next_due": _iso_utc(reference), "missed": 0,
                    "reference_time": _iso_utc(reference)}
        if reference < last_run:
            return {"due": False, "reason": "CLOCK_MOVED_BACKWARD", "next_due": _iso_utc(last_run + interval),
                    "missed": 0, "reference_time": _iso_utc(reference)}
        next_due = last_run + interval
        elapsed = reference - last_run
        missed = int(elapsed.total_seconds() // interval.total_seconds())
        due = reference >= next_due
        return {"due": due, "reason": "DUE" if due else "NOT_DUE", "next_due": _iso_utc(next_due),
                "missed": min(missed, MAX_CATCH_UP) if due else 0, "reference_time": _iso_utc(reference)}

    # daily / weekly — compute the most-recent scheduled instant <= reference in local tz
    hh, mm = (int(x) for x in schedule["local_time"].split(":"))
    reference_local = reference.astimezone(tz)
    candidates = _daily_instants(reference_local, hh, mm, tz)
    if stype == "weekly":
        weekdays = set(schedule.get("weekdays", []))
        candidates = [c for c in candidates if c.weekday() in weekdays]
    past = sorted([c.astimezone(_dt.timezone.utc) for c in candidates if c <= reference])
    future = sorted([c.astimezone(_dt.timezone.utc) for c in candidates if c > reference])
    next_due = _iso_utc(future[0]) if future else None
    if not past:
        return {"due": False, "reason": "NOT_DUE", "next_due": next_due, "missed": 0,
                "reference_time": _iso_utc(reference)}
    most_recent = past[-1]
    if last_run is not None and reference < last_run:
        return {"due": False, "reason": "CLOCK_MOVED_BACKWARD", "next_due": next_due, "missed": 0,
                "reference_time": _iso_utc(reference)}
    due = last_run is None or most_recent > last_run
    return {"due": due, "reason": ("FIRST_RUN" if last_run is None else "DUE") if due else "NOT_DUE",
            "next_due": next_due, "missed": 1 if (due and last_run is not None) else 0,
            "reference_time": _iso_utc(reference)}


# ================================================================ comparison: snapshots + normalization
def _capture_snapshot(cap):
    """Distil a reused Phase 7.10 capture record into a comparison snapshot keyed by (evidence_type,
    field_path). Carries only accepted, provenance-tracked fields — never a timestamp or path."""
    seen, evidence = set(), []
    for e in cap.get("evidence", []):
        key = (e["evidence_type"], e["field_path"])
        if key in seen:          # 7.10 field paths are unique per capture; keep first on any dup
            continue
        seen.add(key)
        evidence.append({
            "evidence_type": e["evidence_type"], "field_path": e["field_path"],
            "observed_value": e["observed_value"], "observed_unit": e.get("observed_unit"),
            "observed_currency": e.get("observed_currency"),
            "extraction_method": e.get("extraction_method"),
            "parser_version": e.get("parser_version"), "evidence_id": e.get("evidence_id"),
        })
    evidence.sort(key=lambda x: (x["evidence_type"], x["field_path"]))
    return {
        "capture_id": cap.get("capture_id"),
        "content_sha256": cap.get("content_sha256"),
        "capture_status": cap.get("capture_status"),
        "parser_version": cap.get("parser_version"),
        "source_locator": cap.get("source_locator"),
        "normalized_locator": cap.get("normalized_locator"),
        "media_type": cap.get("media_type"),
        "evidence": evidence,      # JSON-safe list; keyed on demand via _evidence_map
    }


def _evidence_map(snap):
    """A (evidence_type, field_path) -> evidence dict view of a snapshot's JSON-safe evidence list."""
    return {(e["evidence_type"], e["field_path"]): e for e in (snap or {}).get("evidence", [])}


def _norm_text(value, case_insensitive):
    if value is None:
        return None
    s = unicodedata.normalize("NFC", str(value))
    s = re.sub(r"\s+", " ", s).strip()
    return s.casefold() if case_insensitive else s


def _num(value):
    """Return a Decimal for a clean explicit decimal string, else None (never raises, never float)."""
    if value is None:
        return None
    try:
        return M.parse_decimal_string(str(value), allow_missing=False)
    except M.MoneyError:
        return None


def _quantize_pct(d):
    return d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _numeric_annotations(prev_ev, curr_ev):
    """Compute a safe numeric delta / percent change, or (None, None, warnings). Only when BOTH
    values are explicit numeric evidence with identical unit and identical currency."""
    warnings = []
    pu, cu = prev_ev.get("observed_unit"), curr_ev.get("observed_unit")
    pc, cc = prev_ev.get("observed_currency"), curr_ev.get("observed_currency")
    if pu != cu:
        warnings.append("unit_mismatch")
    if pc != cc:
        warnings.append("currency_mismatch")
    pnum, cnum = _num(prev_ev.get("observed_value")), _num(curr_ev.get("observed_value"))
    if pnum is None or cnum is None or warnings:
        return None, None, warnings
    delta = cnum - pnum
    delta_s = M.decimal_to_canonical_string(delta)
    if pnum == 0:
        warnings.append("percent_change_undefined_previous_zero")
        return delta_s, None, warnings
    pct = _quantize_pct((delta / pnum) * Decimal(100))
    return delta_s, M.decimal_to_canonical_string(pct), warnings


def _make_change(cfg_wl_id, item_id, source_id, normalized_locator, source_locator, *,
                 evidence_type, field_path, previous_value, current_value, observed_unit,
                 observed_currency, change_type, previous_capture_id, current_capture_id,
                 previous_content_hash, current_content_hash, parser_previous, parser_current,
                 comparison_method, numeric_delta=None, numeric_percent_change=None, warnings=None):
    identity = {
        "watchlist_id": cfg_wl_id, "item_id": item_id, "source_id": source_id,
        "normalized_locator": normalized_locator, "evidence_type": evidence_type,
        "field_path": field_path, "previous_capture_id": previous_capture_id,
        "current_capture_id": current_capture_id, "previous_content_hash": previous_content_hash,
        "current_content_hash": current_content_hash, "previous_value": previous_value,
        "current_value": current_value, "observed_unit": observed_unit,
        "observed_currency": observed_currency, "change_type": change_type,
        "comparison_method": comparison_method, "parser_version_previous": parser_previous,
        "parser_version_current": parser_current,
    }
    return {
        "schema_version": CHANGE_SCHEMA,
        "change_id": _cid("chg", identity),
        "watchlist_id": cfg_wl_id, "item_id": item_id, "source_id": source_id,
        "normalized_locator": normalized_locator, "source_locator": source_locator,
        "previous_capture_id": previous_capture_id, "current_capture_id": current_capture_id,
        "previous_content_hash": previous_content_hash, "current_content_hash": current_content_hash,
        "evidence_type": evidence_type, "field_path": field_path,
        "previous_value": previous_value, "current_value": current_value,
        "observed_unit": observed_unit, "observed_currency": observed_currency,
        "change_type": change_type, "numeric_delta": numeric_delta,
        "numeric_percent_change": numeric_percent_change, "warnings": list(warnings or []),
        "comparison_method": comparison_method,
        "parser_version_previous": parser_previous, "parser_version_current": parser_current,
        "owner_rule_matches": [],
    }


def _values_equal(prev_ev, curr_ev, policy):
    fp = curr_ev.get("field_path")
    ci = fp in set(policy.get("case_insensitive_field_paths", []))
    # numeric-aware equality first (so "24.99" == "24.990"); else normalized-text equality
    pnum, cnum = _num(prev_ev.get("observed_value")), _num(curr_ev.get("observed_value"))
    if pnum is not None and cnum is not None and \
            prev_ev.get("observed_unit") == curr_ev.get("observed_unit") and \
            prev_ev.get("observed_currency") == curr_ev.get("observed_currency"):
        return pnum == cnum
    return (_norm_text(prev_ev.get("observed_value"), ci) == _norm_text(curr_ev.get("observed_value"), ci)
            and prev_ev.get("observed_unit") == curr_ev.get("observed_unit")
            and prev_ev.get("observed_currency") == curr_ev.get("observed_currency"))


# ================================================================ comparison: capture status mapping
_STATUS_TO_CHANGE = {
    R.CAP_SELLER: (CHG_SOURCE_BLOCKED, "seller_central_policy_blocked"),
    R.CAP_NET_POLICY: (CHG_SOURCE_BLOCKED, "network_policy_blocked"),
    R.CAP_ROBOTS: (CHG_SOURCE_BLOCKED, "robots_blocked"),
    R.CAP_CTYPE: (CHG_SOURCE_BLOCKED, "content_type_blocked"),
    R.CAP_TOO_LARGE: (CHG_SOURCE_BLOCKED, "content_too_large"),
    R.CAP_SOURCE_BLOCKED: (CHG_SOURCE_BLOCKED, "source_blocked"),
    R.CAP_UNAVAILABLE: (CHG_SOURCE_UNAVAILABLE, "network_unavailable"),
    R.CAP_NOT_FOUND: (CHG_SOURCE_UNAVAILABLE, "not_found"),
    R.CAP_CHALLENGED: (CHG_SOURCE_UNAVAILABLE, "robot_challenge_or_blocked"),
    R.CAP_CACHE_BLOCKED: (CHG_INTEGRITY_BLOCKED, "cache_blocked"),
}


def _compare_unit(wl_id, item, normalized_locator, prev_snap, curr_snap):
    """Compare one prior + one current snapshot for a single normalized source locator. Returns
    (changes, summary). A blocked/unavailable current snapshot never overwrites a good baseline."""
    item_id, source_id = item["item_id"], item["source_id"]
    policy = item["comparison_policy"]
    ignored = set(policy.get("ignored_field_paths", []))
    src_loc = (curr_snap or prev_snap or {}).get("source_locator")

    def mk(**kw):
        kw.setdefault("evidence_type", _CAPTURE_FIELD)
        kw.setdefault("field_path", _CAPTURE_FIELD)
        kw.setdefault("previous_value", None)
        kw.setdefault("current_value", None)
        kw.setdefault("observed_unit", None)
        kw.setdefault("observed_currency", None)
        kw.setdefault("previous_capture_id", prev_snap.get("capture_id") if prev_snap else None)
        kw.setdefault("current_capture_id", curr_snap.get("capture_id") if curr_snap else None)
        kw.setdefault("previous_content_hash", prev_snap.get("content_sha256") if prev_snap else None)
        kw.setdefault("current_content_hash", curr_snap.get("content_sha256") if curr_snap else None)
        kw.setdefault("parser_previous", prev_snap.get("parser_version") if prev_snap else None)
        kw.setdefault("parser_current", curr_snap.get("parser_version") if curr_snap else None)
        return _make_change(wl_id, item_id, source_id, normalized_locator, src_loc, **kw)

    summary = {"normalized_locator": normalized_locator,
               "previous_capture_id": prev_snap.get("capture_id") if prev_snap else None,
               "current_capture_id": curr_snap.get("capture_id") if curr_snap else None,
               "previous_content_hash": prev_snap.get("content_sha256") if prev_snap else None,
               "current_content_hash": curr_snap.get("content_sha256") if curr_snap else None,
               "content_hash_changed": False, "evidence_changed": False,
               "parser_version_changed": False, "status": None, "change_ids": []}
    changes = []

    curr_status = curr_snap.get("capture_status") if curr_snap else None
    curr_ok = curr_snap is not None and curr_status in R._SUCCESS_STATUSES

    # ---- current unavailable / blocked / missing -> record honestly, preserve baseline
    if curr_snap is None:
        summary["status"] = CHG_COMPARISON_NOT_AVAILABLE
        changes.append(mk(change_type=CHG_COMPARISON_NOT_AVAILABLE, comparison_method="capture-status",
                          warnings=["current_capture_absent"]))
        return changes, summary
    if not curr_ok:
        ctype, warn = _STATUS_TO_CHANGE.get(curr_status, (CHG_SOURCE_UNAVAILABLE, "unknown_status"))
        summary["status"] = ctype
        changes.append(mk(change_type=ctype, comparison_method="capture-status", warnings=[warn]))
        return changes, summary

    parser_changed = bool(prev_snap) and prev_snap.get("parser_version") != curr_snap.get("parser_version")
    summary["parser_version_changed"] = parser_changed
    base_warn = ["parser_version_changed"] if parser_changed else []

    if curr_status == R.CAP_PARSE_PARTIAL:
        changes.append(mk(change_type=CHG_PARSE_PARTIAL, comparison_method="capture-status",
                          warnings=["parse_partial"] + base_warn))

    # ---- no prior accepted capture -> initial baseline (never a false business change)
    if prev_snap is None:
        summary["status"] = CHG_INITIAL_BASELINE
        changes.append(mk(change_type=CHG_INITIAL_BASELINE, comparison_method="baseline",
                          warnings=base_warn))
        return changes, summary

    summary["content_hash_changed"] = prev_snap.get("content_sha256") != curr_snap.get("content_sha256")
    prev_ev, curr_ev = _evidence_map(prev_snap), _evidence_map(curr_snap)
    all_keys = sorted(set(prev_ev) | set(curr_ev))
    evidence_changed = False
    for key in all_keys:
        etype, fpath = key
        if fpath in ignored:
            continue
        pe, ce = prev_ev.get(key), curr_ev.get(key)
        if pe is None and ce is not None:
            evidence_changed = True
            changes.append(mk(evidence_type=etype, field_path=fpath, current_value=ce["observed_value"],
                              observed_unit=ce.get("observed_unit"), observed_currency=ce.get("observed_currency"),
                              change_type=CHG_ADDED, comparison_method="exact-field", warnings=base_warn))
        elif pe is not None and ce is None:
            evidence_changed = True
            changes.append(mk(evidence_type=etype, field_path=fpath, previous_value=pe["observed_value"],
                              observed_unit=pe.get("observed_unit"), observed_currency=pe.get("observed_currency"),
                              change_type=CHG_REMOVED, comparison_method="exact-field", warnings=base_warn))
        else:
            equal = _values_equal(pe, ce, policy)
            delta, pct, num_warn = _numeric_annotations(pe, ce) if not equal else (None, None, [])
            method = "numeric-decimal" if (delta is not None or pct is not None) else "exact-field"
            ctype = CHG_UNCHANGED if equal else CHG_CHANGED
            if not equal:
                evidence_changed = True
            changes.append(mk(evidence_type=etype, field_path=fpath,
                              previous_value=pe["observed_value"], current_value=ce["observed_value"],
                              observed_unit=ce.get("observed_unit"), observed_currency=ce.get("observed_currency"),
                              change_type=ctype, comparison_method=method, numeric_delta=delta,
                              numeric_percent_change=pct, warnings=(num_warn + base_warn)))
    summary["evidence_changed"] = evidence_changed
    # source-content changed but no accepted evidence change -> recorded explicitly
    if summary["content_hash_changed"] and not evidence_changed:
        changes.append(mk(change_type=CHG_UNCHANGED, comparison_method="content-hash",
                          field_path="__source_content__", evidence_type=_CAPTURE_FIELD,
                          warnings=["source_content_changed_evidence_unchanged"] + base_warn))
    summary["status"] = (CHG_CHANGED if evidence_changed else CHG_UNCHANGED)
    return changes, summary


def _compare_item(wl_id, item, prev_snaps, curr_snaps):
    """Compare all normalized locators for one watchlist item. prev_snaps / curr_snaps map
    normalized_locator -> snapshot. Produces a deterministic, content-addressed comparison record."""
    units, changes = [], []
    for nloc in sorted(set(prev_snaps) | set(curr_snaps)):
        u_changes, summary = _compare_unit(wl_id, item, nloc, prev_snaps.get(nloc), curr_snaps.get(nloc))
        summary["change_ids"] = sorted(c["change_id"] for c in u_changes)
        units.append(summary)
        changes.extend(u_changes)
    comparison_id = _cid("cmp", {"watchlist_id": wl_id, "item_id": item["item_id"],
                                 "previous": sorted(s.get("capture_id") for s in prev_snaps.values()
                                                    if s.get("capture_id")),
                                 "current": sorted(s.get("capture_id") for s in curr_snaps.values()
                                                   if s.get("capture_id")),
                                 "changes": sorted(c["change_id"] for c in changes)})
    counts = {}
    for c in changes:
        counts[c["change_type"]] = counts.get(c["change_type"], 0) + 1
    record = {
        "schema_version": COMPARISON_SCHEMA,
        "comparison_id": comparison_id,
        "watchlist_id": wl_id, "item_id": item["item_id"], "source_id": item["source_id"],
        "comparison_engine_version": COMPARISON_ENGINE_VERSION,
        "units": units, "change_count": len(changes), "change_counts": counts,
        "changes": sorted(changes, key=lambda c: (c["normalized_locator"], c["evidence_type"],
                                                   c["field_path"], c["change_id"])),
    }
    record.update(R.AMAZON_COUNTERS)
    return record


def _item_readiness(record):
    counts = record["change_counts"]
    if counts.get(CHG_INTEGRITY_BLOCKED):
        return INTEGRITY_BLOCKED
    if counts.get(CHG_SOURCE_BLOCKED):
        return SOURCE_BLOCKED
    if counts.get(CHG_SOURCE_UNAVAILABLE):
        return NETWORK_UNAVAILABLE
    if any(counts.get(s) for s in _ACTIONABLE_CHANGES):
        return COMPARISON_READY
    return COMPARISON_READY_EMPTY


# ================================================================ owner alert rules -> alerts
def _selector_matches(sel, change):
    return sel == "*" or sel in (change["source_id"], change["normalized_locator"],
                                 change.get("source_locator"))


def _unit_currency_ok(rule, change):
    if rule.get("unit") is not None and rule["unit"] != change.get("observed_unit"):
        return False
    if rule.get("currency") is not None and rule["currency"] != change.get("observed_currency"):
        return False
    return True


def _rule_matches(rule, change):
    """Return (matched, reason). Bounded operators only — no arbitrary regex, no fuzzy match, no
    cross-currency/cross-unit numeric comparison, no calculated severity."""
    if not rule.get("enabled", True):
        return False, "rule_disabled"
    if not _selector_matches(rule["source_selector"], change):
        return False, "selector_mismatch"
    if rule["evidence_type"] != "*" and rule["evidence_type"] != change["evidence_type"]:
        return False, "evidence_type_mismatch"
    if rule["field_path"] != "*" and rule["field_path"] != change["field_path"]:
        return False, "field_path_mismatch"
    op, ct = rule["operator"], change["change_type"]

    if op == OP_FIELD_CHANGED:
        return (ct == CHG_CHANGED), "field_changed"
    if op == OP_FIELD_ADDED:
        return (ct == CHG_ADDED), "field_added"
    if op == OP_FIELD_REMOVED:
        return (ct == CHG_REMOVED), "field_removed"
    if op in _STATUS_OPERATORS:
        return (ct == _STATUS_OPERATORS[op]), op

    if op in (OP_VALUE_EQUALS, OP_VALUE_CONTAINS):
        if ct not in (CHG_ADDED, CHG_CHANGED) or change.get("current_value") is None:
            return False, "no_current_value"
        cur = _norm_text(change["current_value"], False)
        target = _norm_text(rule["comparison_value"], False)
        if op == OP_VALUE_EQUALS:
            return (cur == target), "value_equals"
        return (target in cur), "value_contains"

    if op in _NUMERIC_OPERATORS:
        if not _unit_currency_ok(rule, change):
            return False, "unit_or_currency_incompatible"
        threshold = _num(rule["comparison_value"])
        if op in (OP_NUMERIC_ABOVE, OP_NUMERIC_BELOW):
            if ct not in (CHG_ADDED, CHG_CHANGED):
                return False, "not_a_value_change"
            cur = _num(change.get("current_value"))
            if cur is None or threshold is None:
                return False, "non_numeric_value"
            if op == OP_NUMERIC_ABOVE:
                return (cur > threshold), "numeric_above"
            return (cur < threshold), "numeric_below"
        if op == OP_ABS_DELTA:
            if change.get("numeric_delta") is None or threshold is None:
                return False, "no_numeric_delta"
            return (abs(_num(change["numeric_delta"])) >= threshold), "absolute_delta"
        if op == OP_PCT_CHANGE:
            if change.get("numeric_percent_change") is None or threshold is None:
                return False, "no_numeric_percent"
            return (abs(_num(change["numeric_percent_change"])) >= threshold), "percent_change"
    return False, "unhandled_operator"


def _build_alert(wl_id, rule, change, reason):
    alert_id = _cid("alt", {"watchlist_id": wl_id, "item_id": change["item_id"],
                            "rule_id": rule["rule_id"], "change_id": change["change_id"]})
    return {
        "schema_version": ALERT_SCHEMA,
        "alert_id": alert_id,
        "watchlist_id": wl_id, "item_id": change["item_id"], "rule_id": rule["rule_id"],
        "change_id": change["change_id"], "source_id": change["source_id"],
        "normalized_locator": change["normalized_locator"], "source_locator": change.get("source_locator"),
        "evidence_type": change["evidence_type"], "field_path": change["field_path"],
        "change_type": change["change_type"],
        "severity": rule["severity"], "label": rule["label"], "message": rule["message"],
        "reason": reason,
        "previous_value": change.get("previous_value"), "current_value": change.get("current_value"),
        "observed_unit": change.get("observed_unit"), "observed_currency": change.get("observed_currency"),
        "numeric_delta": change.get("numeric_delta"),
        "numeric_percent_change": change.get("numeric_percent_change"),
        "evidence_lineage": {
            "previous_capture_id": change["previous_capture_id"],
            "current_capture_id": change["current_capture_id"],
            "previous_content_hash": change["previous_content_hash"],
            "current_content_hash": change["current_content_hash"],
            "parser_version_previous": change["parser_version_previous"],
            "parser_version_current": change["parser_version_current"],
        },
    }
    # (identity is complete; status lives only in the alert-state chain, never here)


def evaluate_rules(wl, changes):
    """Evaluate explicit owner rules against change events. Returns (alerts, changes). Each alert is
    deterministic + content-addressed; owner_rule_matches is recorded on the matched change."""
    wl_id = wl["watchlist_id"]
    rules = wl.get("alert_rules", [])
    alerts = {}
    for change in changes:
        for rule in rules:
            matched, reason = _rule_matches(rule, change)
            if not matched:
                continue
            change.setdefault("owner_rule_matches", []).append(rule["rule_id"])
            alert = _build_alert(wl_id, rule, change, reason)
            alerts.setdefault(alert["alert_id"], alert)   # dedup: same rule+change -> one alert
    return list(alerts.values()), changes


# ================================================================ alert state: hash-chained audit trail
def _alert_state_path(cfg, watchlist_id):
    return os.path.join(cfg.state_dir, "alerts-" + watchlist_id + ".json")


def _event_hash(seq, alert_id, action, actor, note, prev_state_hash):
    return _sha({"seq": seq, "alert_id": alert_id, "action": action, "actor": actor,
                 "note": note, "prev_state_hash": prev_state_hash})


def _verify_chain(state):
    """Recompute the append-only hash chain; a corrupted / deleted / reordered history raises
    ALERT_STATE_BLOCKED so no further state update can proceed on a broken chain."""
    prev = ""
    for i, ev in enumerate(state.get("history", [])):
        if ev.get("seq") != i:
            raise WatchlistError("ALERT_HISTORY_REORDERED", f"seq {ev.get('seq')} at {i}",
                                 readiness=ALERT_STATE_BLOCKED)
        if ev.get("prev_state_hash") != prev:
            raise WatchlistError("ALERT_HISTORY_BROKEN", f"prev mismatch at {i}",
                                 readiness=ALERT_STATE_BLOCKED)
        recomputed = _event_hash(i, ev.get("alert_id"), ev.get("action"), ev.get("actor"),
                                 ev.get("note"), prev)
        if recomputed != ev.get("event_hash"):
            raise WatchlistError("ALERT_HISTORY_TAMPERED", f"event hash mismatch at {i}",
                                 readiness=ALERT_STATE_BLOCKED)
        prev = recomputed
    if state.get("head_hash", "") != prev:
        raise WatchlistError("ALERT_HISTORY_HEAD_MISMATCH", "aggregate head mismatch",
                             readiness=ALERT_STATE_BLOCKED)
    return True


def _load_alert_state(cfg, watchlist_id):
    path = _alert_state_path(cfg, watchlist_id)
    if not os.path.isfile(path):
        return {"schema_version": ALERT_STATE_SCHEMA, "watchlist_id": watchlist_id,
                "alerts": {}, "history": [], "head_hash": ""}
    state = _load_hashed_json(path, "alert_state", readiness=ALERT_STATE_BLOCKED)
    _verify_chain(state)
    return state


def _save_alert_state(cfg, state):
    _write_hashed_json(_alert_state_path(cfg, state["watchlist_id"]), state)


def _register_alerts(cfg, wl_id, alerts):
    """Persist new alert identities + register them OPEN in the state (idempotent, dedup-safe)."""
    state = _load_alert_state(cfg, wl_id)
    created, reused = [], []
    for alert in alerts:
        aid = alert["alert_id"]
        apath = os.path.join(cfg.alerts_dir, aid + ".json")
        if not os.path.isfile(apath):
            rec = dict(alert)
            rec.update(R.AMAZON_COUNTERS)
            _write_json(apath, rec)
        if aid in state["alerts"]:
            reused.append(aid)
        else:
            state["alerts"][aid] = {"status": ALERT_OPEN}
            created.append(aid)
    _save_alert_state(cfg, state)
    return created, reused


def _alert_action(cfg, alert_id, action, actor, note):
    if action == ACT_REOPEN and action not in _ACTION_TO_STATUS:      # defensive
        raise WatchlistError("UNKNOWN_ACTION", action)
    if not isinstance(actor, str) or not actor.strip():
        raise WatchlistError("ACTOR_REQUIRED", "an explicit owner actor label is required",
                             readiness=ALERT_STATE_BLOCKED)
    apath = os.path.join(cfg.alerts_dir, alert_id + ".json")
    if not os.path.isfile(apath):
        raise WatchlistError("ALERT_NOT_FOUND", alert_id, readiness=ALERT_STATE_BLOCKED)
    alert = _load_json(apath, "alert")
    wl_id = alert["watchlist_id"]
    state = _load_alert_state(cfg, wl_id)
    if alert_id not in state["alerts"]:
        raise WatchlistError("ALERT_NOT_REGISTERED", alert_id, readiness=ALERT_STATE_BLOCKED)
    current = state["alerts"][alert_id]["status"]
    if action == ACT_REOPEN and current == ALERT_OPEN:
        raise WatchlistError("ALERT_ALREADY_OPEN", alert_id, readiness=ALERT_STATE_BLOCKED)
    note = "" if note is None else str(note)
    seq = len(state["history"])
    prev = state.get("head_hash", "")
    eh = _event_hash(seq, alert_id, action, actor.strip(), note, prev)
    state["history"].append({"seq": seq, "alert_id": alert_id, "action": action,
                             "actor": actor.strip(), "note": note, "prev_state_hash": prev,
                             "event_hash": eh, "recorded_at": _iso_utc()})
    state["head_hash"] = eh
    state["alerts"][alert_id] = {"status": _ACTION_TO_STATUS[action]}
    _save_alert_state(cfg, state)
    return {"command": action.lower() + "-alert", "readiness": ACK_READY, "ok": True,
            "alert_id": alert_id, "status": _ACTION_TO_STATUS[action], "actor": actor.strip(),
            "aggregate_hash": eh, "history_length": len(state["history"]),
            "amazon_counters": dict(R.AMAZON_COUNTERS)}


# ================================================================ watchlist run-state (baselines)
def _state_path(cfg, watchlist_id):
    return os.path.join(cfg.state_dir, watchlist_id + ".json")


def _load_state(cfg, watchlist_id):
    path = _state_path(cfg, watchlist_id)
    if not os.path.isfile(path):
        return {"schema_version": STATE_SCHEMA, "watchlist_id": watchlist_id, "items": {},
                "last_run_utc": None, "last_execution_id": None, "executions": []}
    return _load_hashed_json(path, "watchlist_state", readiness=INTEGRITY_BLOCKED)


def _save_state(cfg, state):
    _write_hashed_json(_state_path(cfg, state["watchlist_id"]), state)


# ================================================================ locking / concurrency
def _lock_path(cfg, watchlist_id):
    return os.path.join(cfg.locks_dir, watchlist_id + ".lock")


def _acquire_lock(cfg, watchlist_id, *, break_stale=False):
    os.makedirs(cfg.locks_dir, exist_ok=True)
    path = _lock_path(cfg, watchlist_id)
    created = _now_epoch(cfg)
    token = {"watchlist_id": watchlist_id, "pid": cfg.pid, "created_epoch": created,
             "tool_version": TOOL_VERSION}
    for attempt in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                existing = _load_json(path, "lock")
            except (ValueError, OSError):
                existing = {}
            age = created - float(existing.get("created_epoch", created))
            if age > MAX_LOCK_AGE_SECONDS:
                if break_stale and attempt == 0:
                    # explicit owner recovery only — never auto-remove a possibly-live lock
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    continue
                raise WatchlistError("LOCK_STALE",
                                     f"stale lock (age {int(age)}s); rerun with --break-stale-lock",
                                     readiness=WATCHLIST_BLOCKED)
            raise WatchlistError("LOCK_HELD", "another run holds this watchlist lock",
                                 readiness=WATCHLIST_BLOCKED)
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(canonical_json(token))
            return token
    raise WatchlistError("LOCK_HELD", "could not acquire lock", readiness=WATCHLIST_BLOCKED)


def _release_lock(cfg, watchlist_id, token):
    """Release ONLY a lock this process owns (pid + created_epoch match). Never remove a foreign
    or possibly-live lock. Safe to call after a failed run."""
    path = _lock_path(cfg, watchlist_id)
    try:
        existing = _load_json(path, "lock")
    except (ValueError, OSError):
        return False
    if existing.get("pid") == token.get("pid") and \
            float(existing.get("created_epoch", -1)) == float(token.get("created_epoch", -2)):
        try:
            os.remove(path)
            return True
        except OSError:
            return False
    return False


# ================================================================ reused Phase 7.10 research run
def _load_verified_capture(rcfg, capture_id):
    """Load a reused Phase 7.10 capture and confirm its stored content hash matches the raw bytes.
    A corrupt capture raises INTEGRITY_BLOCKED (never a silent bad comparison)."""
    cap = R._load_capture(rcfg, capture_id)
    ch = cap.get("content_sha256")
    if ch:
        raw = os.path.join(rcfg.raw_dir, capture_id + ".bin")
        if not os.path.isfile(raw):
            raise WatchlistError("CAPTURE_RAW_MISSING", capture_id, readiness=INTEGRITY_BLOCKED)
        with open(raw, "rb") as f:
            if R._sha256_hex(f.read()) != ch:
                raise WatchlistError("CAPTURE_CORRUPT", capture_id, readiness=INTEGRITY_BLOCKED)
    return cap


def _snapshots_from_run(rcfg, run_id):
    """Return {source_id: {normalized_locator: snapshot}} for a reused Phase 7.10 run, each capture
    integrity-verified. Also returns the run readiness + whether any Seller-Central block occurred."""
    manifest = R._load_manifest(rcfg, run_id)
    by_source = {}
    seller_blocked = False
    for cid in manifest["capture_ids"]:
        cap = _load_verified_capture(rcfg, cid)
        if cap.get("capture_status") == R.CAP_SELLER:
            seller_blocked = True
        snap = _capture_snapshot(cap)
        by_source.setdefault(cap["source_id"], {})[cap["normalized_locator"]] = snap
    return by_source, manifest["readiness"], seller_blocked, manifest


def _run_research(cfg, items):
    """Build a Phase 7.10 plan from the given watchlist items (deduped by source identity), run it
    through the reused 7.10 authority into a 7.11-OWNED 7.10 workspace, verify the run, and return
    ({source_id: {nloc: snapshot}}, run_id, research_readiness, rcfg, extras)."""
    rcfg = cfg.research_config(cfg.research_runs_dir)
    seen, descriptors = set(), []
    for it in items:
        if it["source_id"] in seen:
            continue
        seen.add(it["source_id"])
        descriptors.append(it["descriptor"])
    if not descriptors:
        return {}, None, SOURCE_REQUIRED, rcfg, {}
    plan = {"schema_version": R.PLAN_SCHEMA, "sources": descriptors}
    plan_path = os.path.join(cfg.executions_dir, "plan-" + _sha(plan)[:16] + ".json")
    _write_json(plan_path, plan)
    res = R.run_plan(rcfg, plan_path)
    run_id = res.get("run_id")
    if run_id is None:
        return {}, None, RESEARCH_RUN_BLOCKED, rcfg, {"research_result": res}
    verify = R.verify_run(rcfg, run_id)
    if not verify.get("ok"):
        return {}, run_id, INTEGRITY_BLOCKED, rcfg, {"verify": verify}
    try:
        by_source, readiness, seller_blocked, manifest = _snapshots_from_run(rcfg, run_id)
    except WatchlistError as e:
        if e.readiness == INTEGRITY_BLOCKED:
            return {}, run_id, INTEGRITY_BLOCKED, rcfg, {"error": e.code}
        raise
    r711 = SELLER_CENTRAL_POLICY_BLOCKED if seller_blocked else RESEARCH_RUN_BLOCKED
    ok_map = {R.READY: READY, R.READY_EMPTY: READY_EMPTY, R.READY_PARTIAL: READY_PARTIAL}
    research_readiness = ok_map.get(readiness, r711 if seller_blocked else _map_research_block(readiness))
    return by_source, run_id, research_readiness, rcfg, {
        "manifest": manifest, "network_purpose_counters": manifest.get("network_purpose_counters", {})}


def _map_research_block(readiness):
    if readiness == R.SELLER_CENTRAL_POLICY_BLOCKED:
        return SELLER_CENTRAL_POLICY_BLOCKED
    if readiness in (R.NETWORK_UNAVAILABLE,):
        return NETWORK_UNAVAILABLE
    if readiness in (R.SOURCE_REQUIRED,):
        return SOURCE_REQUIRED
    if readiness in (R.INTEGRITY_BLOCKED, R.CACHE_BLOCKED):
        return INTEGRITY_BLOCKED
    return SOURCE_BLOCKED


# ================================================================ execution readiness combine
def _combine_readiness(item_readinesses, alert_state_blocked):
    if alert_state_blocked:
        return ALERT_STATE_BLOCKED
    if not item_readinesses:
        return READY_EMPTY
    ok = [r for r in item_readinesses if r in (COMPARISON_READY, COMPARISON_READY_EMPTY)]
    blocked = [r for r in item_readinesses if r in (INTEGRITY_BLOCKED, SOURCE_BLOCKED,
                                                    NETWORK_UNAVAILABLE, SELLER_CENTRAL_POLICY_BLOCKED)]
    has_actionable = COMPARISON_READY in item_readinesses
    if ok and blocked:
        return READY_PARTIAL
    if ok:
        return READY if has_actionable else READY_EMPTY
    for r in (SELLER_CENTRAL_POLICY_BLOCKED, INTEGRITY_BLOCKED, SOURCE_BLOCKED, NETWORK_UNAVAILABLE):
        if r in blocked:
            return r
    return READY_EMPTY


# ================================================================ execution
def _execute_watchlist(cfg, wl, reference_iso, *, break_stale=False, forced=True):
    wl_id = wl["watchlist_id"]
    items = [it for it in wl["sources"] if it["descriptor"].get("enabled", True)]
    token = _acquire_lock(cfg, wl_id, break_stale=break_stale)
    try:
        by_source, run_id, research_readiness, rcfg, extras = _run_research(cfg, items)
        state = _load_state(cfg, wl_id)
        state_items = state.setdefault("items", {})

        if research_readiness in (INTEGRITY_BLOCKED, RESEARCH_RUN_BLOCKED, SELLER_CENTRAL_POLICY_BLOCKED) \
                and not by_source:
            execution = _write_execution(cfg, wl_id, run_id, [], reference_iso, research_readiness,
                                         [], extras)
            return execution

        comparisons, all_changes, item_readinesses = [], [], []
        for it in items:
            iid = it["item_id"]
            prev_snaps = (state_items.get(iid, {}).get("baselines") or {})
            curr_snaps = by_source.get(it["source_id"], {})
            record = _compare_item(wl_id, it, prev_snaps, curr_snaps)
            _write_json(os.path.join(cfg.comparisons_dir, record["comparison_id"] + ".json"), record)
            comparisons.append(record)
            all_changes.extend(record["changes"])
            item_readinesses.append(_item_readiness(record))
            # update baseline ONLY from accepted (successful) current captures; preserve last valid
            si = state_items.setdefault(iid, {"source_id": it["source_id"], "baselines": {}})
            for nloc, snap in curr_snaps.items():
                if snap.get("capture_status") in R._SUCCESS_STATUSES:
                    si["baselines"][nloc] = snap
            si["last_run_id"] = run_id

        alerts, all_changes = evaluate_rules(wl, all_changes)
        alert_state_blocked = False
        created, reused = [], []
        try:
            created, reused = _register_alerts(cfg, wl_id, alerts)
        except WatchlistError as e:
            if e.readiness == ALERT_STATE_BLOCKED:
                alert_state_blocked = True
            else:
                raise

        readiness = _combine_readiness(item_readinesses, alert_state_blocked)
        if research_readiness == SELLER_CENTRAL_POLICY_BLOCKED:   # policy always wins, first
            readiness = SELLER_CENTRAL_POLICY_BLOCKED
        execution = _write_execution(cfg, wl_id, run_id, comparisons, reference_iso, readiness,
                                     all_changes, extras, alerts_created=created, alerts_reused=reused)
        # persist run-state (operational last_run_utc drives future due calc)
        state["last_run_utc"] = _parse_iso(reference_iso, "reference_time") and \
            _iso_utc(_parse_iso(reference_iso, "reference_time")) or _iso_utc()
        state["last_execution_id"] = execution["execution_id"]
        state.setdefault("executions", []).append(execution["execution_id"])
        _save_state(cfg, state)
        _write_reports(cfg, wl, execution, comparisons, all_changes, alerts)
        return execution
    finally:
        _release_lock(cfg, wl_id, token)


def _write_execution(cfg, wl_id, run_id, comparisons, reference_iso, readiness, changes, extras,
                     *, alerts_created=None, alerts_reused=None):
    comparison_ids = sorted(c["comparison_id"] for c in comparisons)
    execution_id = _cid("exec", {"watchlist_id": wl_id, "research_run_id": run_id,
                                 "comparison_ids": comparison_ids})
    change_counts = {}
    for c in changes:
        change_counts[c["change_type"]] = change_counts.get(c["change_type"], 0) + 1
    record = {
        "schema_version": EXECUTION_SCHEMA,
        "execution_id": execution_id,
        "watchlist_id": wl_id,
        "research_run_id": run_id,
        "readiness": readiness,
        "comparison_ids": comparison_ids,
        "comparison_count": len(comparisons),
        "change_count": len(changes),
        "change_counts": change_counts,
        "alerts_created": sorted(alerts_created or []),
        "alerts_reused": sorted(alerts_reused or []),
        "items": [{"item_id": c["item_id"], "source_id": c["source_id"],
                   "comparison_id": c["comparison_id"], "change_count": c["change_count"],
                   "readiness": _item_readiness(c)} for c in comparisons],
        "network_purpose_counters": extras.get("network_purpose_counters", {}),
        "reference_time": _parse_iso(reference_iso, "reference_time") and
        _iso_utc(_parse_iso(reference_iso, "reference_time")) or _iso_utc(),   # OPERATIONAL only
        "disclaimers": list(DISCLAIMERS),
    }
    record.update(R.AMAZON_COUNTERS)
    _write_json(os.path.join(cfg.executions_dir, execution_id + ".json"), record)
    if changes:
        _write_json(os.path.join(cfg.changes_dir, execution_id + ".json"),
                    {"schema_version": CHANGE_SCHEMA, "execution_id": execution_id,
                     "watchlist_id": wl_id, "changes": changes})
    _log(cfg, "execute", {"watchlist_id": wl_id, "execution_id": execution_id,
                          "readiness": readiness, "changes": len(changes)})
    return record


def run_watchlist(cfg, watchlist_id, *, reference_time=None, break_stale=False):
    """Objective 4 — run one watchlist manually (a forced run, ignoring schedule due state)."""
    wl = load_watchlist(cfg, watchlist_id)
    ref = reference_time or _iso_utc()
    execution = _execute_watchlist(cfg, wl, ref, break_stale=break_stale, forced=True)
    out = {"command": "run-watchlist", "watchlist_id": watchlist_id,
           "execution_id": execution["execution_id"], "readiness": execution["readiness"],
           "ok": execution["readiness"] in _EXIT_OK, "research_run_id": execution["research_run_id"],
           "change_count": execution["change_count"], "change_counts": execution["change_counts"],
           "alerts_created": execution["alerts_created"], "alerts_reused": execution["alerts_reused"],
           "amazon_counters": dict(R.AMAZON_COUNTERS)}
    return out


def run_due(cfg, *, reference_time=None):
    """Objective 5 — run every watchlist whose explicit schedule is due at the reference time."""
    ref = reference_time or _iso_utc()
    listing = list_watchlists(cfg)
    executed, skipped = [], []
    for meta in listing["watchlists"]:
        wl_id = meta["watchlist_id"]
        wl = load_watchlist(cfg, wl_id)
        if not wl.get("enabled", True):
            skipped.append({"watchlist_id": wl_id, "reason": "WATCHLIST_DISABLED"})
            continue
        state = _load_state(cfg, wl_id)
        decision = compute_due(wl["schedule"], wl["timezone"], state.get("last_run_utc"), ref)
        if not decision["due"]:
            skipped.append({"watchlist_id": wl_id, "reason": decision["reason"],
                            "next_due": decision["next_due"]})
            continue
        execution = _execute_watchlist(cfg, wl, ref, forced=False)
        executed.append({"watchlist_id": wl_id, "execution_id": execution["execution_id"],
                         "readiness": execution["readiness"], "change_count": execution["change_count"]})
    readiness = READY if executed else NOT_DUE
    return {"command": "run-due", "readiness": readiness, "ok": True, "reference_time": ref,
            "executed": executed, "skipped": skipped, "executed_count": len(executed),
            "amazon_counters": dict(R.AMAZON_COUNTERS)}


# ================================================================ compare (replay from existing 7.10 runs)
def compare(cfg, watchlist_id, previous_run_id, current_run_id):
    """Objective 13 — replay change detection between two EXISTING accepted Phase 7.10 runs (read
    from --research-dir). No network; no baseline mutation; corrupt captures raise INTEGRITY_BLOCKED."""
    wl = load_watchlist(cfg, watchlist_id)
    if not cfg.research_dir:
        raise WatchlistError("RESEARCH_DIR_REQUIRED", "compare requires --research-dir",
                             readiness=BASELINE_REQUIRED)
    rcfg = cfg.research_config(cfg.research_dir)
    try:
        prev_by_source, _, _, _ = _snapshots_from_run(rcfg, previous_run_id)
        curr_by_source, _, _, _ = _snapshots_from_run(rcfg, current_run_id)
    except R.ResearchError as e:
        raise WatchlistError("RESEARCH_RUN_NOT_FOUND", str(getattr(e, "code", e)),
                             readiness=COMPARISON_BLOCKED)
    except WatchlistError as e:
        return {"command": "compare", "watchlist_id": watchlist_id, "readiness": COMPARISON_BLOCKED,
                "ok": False, "previous_run_id": previous_run_id, "current_run_id": current_run_id,
                "error_code": e.code, "amazon_counters": dict(R.AMAZON_COUNTERS)}
    comparisons, all_changes, item_readinesses = [], [], []
    for it in wl["sources"]:
        record = _compare_item(watchlist_id, it, prev_by_source.get(it["source_id"], {}),
                               curr_by_source.get(it["source_id"], {}))
        _write_json(os.path.join(cfg.comparisons_dir, record["comparison_id"] + ".json"), record)
        comparisons.append(record)
        all_changes.extend(record["changes"])
        item_readinesses.append(_item_readiness(record))
    readiness = _combine_comparison_readiness(item_readinesses)
    return {"command": "compare", "watchlist_id": watchlist_id, "readiness": readiness,
            "ok": readiness in _EXIT_OK, "previous_run_id": previous_run_id,
            "current_run_id": current_run_id, "comparison_ids": sorted(c["comparison_id"] for c in comparisons),
            "change_count": len(all_changes),
            "comparison_ids_count": len(comparisons), "amazon_counters": dict(R.AMAZON_COUNTERS)}


def _combine_comparison_readiness(item_readinesses):
    if not item_readinesses:
        return COMPARISON_READY_EMPTY
    if INTEGRITY_BLOCKED in item_readinesses:
        return COMPARISON_BLOCKED
    if COMPARISON_READY in item_readinesses:
        return COMPARISON_READY
    if SOURCE_BLOCKED in item_readinesses or NETWORK_UNAVAILABLE in item_readinesses:
        return COMPARISON_READY   # documented blocked/unavailable events are a valid comparison result
    return COMPARISON_READY_EMPTY


# ================================================================ alert views + listings
def _alert_status_counts(cfg, wl_id):
    counts = {ALERT_OPEN: 0, ALERT_ACKNOWLEDGED: 0, ALERT_DISMISSED: 0}
    try:
        state = _load_alert_state(cfg, wl_id)
    except WatchlistError:
        return counts, None
    for meta in state["alerts"].values():
        counts[meta["status"]] = counts.get(meta["status"], 0) + 1
    return counts, state


def _alert_view(cfg, wl_id, status_filter=None):
    _, state = _alert_status_counts(cfg, wl_id)
    rows = []
    if state is None:
        return rows
    for aid in sorted(state["alerts"]):
        status = state["alerts"][aid]["status"]
        if status_filter and status != status_filter:
            continue
        apath = os.path.join(cfg.alerts_dir, aid + ".json")
        if not os.path.isfile(apath):
            continue
        alert = _load_json(apath, "alert")
        rows.append({**{k: alert[k] for k in ("alert_id", "watchlist_id", "item_id", "rule_id",
                                              "change_id", "severity", "label", "message", "reason",
                                              "previous_value", "current_value", "observed_unit",
                                              "observed_currency", "evidence_type", "field_path",
                                              "change_type", "evidence_lineage")},
                     "status": status})
    return rows


def list_executions(cfg, watchlist_id):
    load_watchlist(cfg, watchlist_id)
    out = []
    root = cfg.executions_dir
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if not name.startswith("exec-") or not name.endswith(".json"):
                continue
            try:
                rec = _load_json(os.path.join(root, name), "execution")
            except (ValueError, OSError):
                continue
            if rec.get("watchlist_id") != watchlist_id:
                continue
            out.append({"execution_id": rec.get("execution_id"), "readiness": rec.get("readiness"),
                        "research_run_id": rec.get("research_run_id"),
                        "change_count": rec.get("change_count"),
                        "change_counts": rec.get("change_counts")})
    return {"command": "list-executions", "readiness": LIST_READY, "ok": True, "count": len(out),
            "watchlist_id": watchlist_id, "executions": out, "amazon_counters": dict(R.AMAZON_COUNTERS)}


def list_changes(cfg, watchlist_id, execution_id=None):
    load_watchlist(cfg, watchlist_id)
    rows = []
    root = cfg.changes_dir
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if not name.endswith(".json"):
                continue
            if execution_id and name != execution_id + ".json":
                continue
            try:
                rec = _load_json(os.path.join(root, name), "changes")
            except (ValueError, OSError):
                continue
            if rec.get("watchlist_id") != watchlist_id:
                continue
            rows.extend(rec.get("changes", []))
    rows = sorted(rows, key=lambda c: (c["item_id"], c["normalized_locator"], c["evidence_type"],
                                       c["field_path"], c["change_id"]))
    return {"command": "list-changes", "readiness": LIST_READY, "ok": True, "count": len(rows),
            "watchlist_id": watchlist_id, "changes": rows, "amazon_counters": dict(R.AMAZON_COUNTERS)}


def list_alerts(cfg, watchlist_id, status_filter=None):
    load_watchlist(cfg, watchlist_id)
    rows = _alert_view(cfg, watchlist_id, status_filter)
    counts, _ = _alert_status_counts(cfg, watchlist_id)
    readiness = ALERTS_READY if rows else ALERTS_READY_EMPTY
    return {"command": "list-alerts", "readiness": readiness, "ok": True, "count": len(rows),
            "watchlist_id": watchlist_id, "alerts": rows, "status_counts": counts,
            "amazon_counters": dict(R.AMAZON_COUNTERS)}


# ================================================================ verify-state
def verify_state(cfg, watchlist_id):
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    readiness = VERIFY_READY
    try:
        wl = load_watchlist(cfg, watchlist_id)
        add("watchlist_identity", True)
    except WatchlistError as e:
        add("watchlist_identity", False, e.code)
        return {"command": "verify-state", "readiness": e.readiness, "ok": False, "checks": checks,
                "watchlist_id": watchlist_id, "amazon_counters": dict(R.AMAZON_COUNTERS)}
    try:
        _load_state(cfg, watchlist_id)
        add("run_state_integrity", True)
    except WatchlistError as e:
        add("run_state_integrity", False, e.code)
        readiness = INTEGRITY_BLOCKED
    try:
        state = _load_alert_state(cfg, watchlist_id)
        add("alert_history_chain", True)
        for aid, meta in state["alerts"].items():
            add(f"alert_present:{aid}", os.path.isfile(os.path.join(cfg.alerts_dir, aid + ".json")))
            add(f"alert_status_valid:{aid}", meta["status"] in _ALERT_STATUSES)
    except WatchlistError as e:
        add("alert_history_chain", False, e.code)
        readiness = ALERT_STATE_BLOCKED
    ok = all(c["ok"] for c in checks)
    if not ok and readiness == VERIFY_READY:
        readiness = INTEGRITY_BLOCKED
    return {"command": "verify-state", "readiness": readiness if ok else readiness, "ok": ok,
            "checks": checks, "checks_passed": sum(1 for c in checks if c["ok"]),
            "checks_total": len(checks), "watchlist_id": watchlist_id,
            "amazon_counters": dict(R.AMAZON_COUNTERS)}


# ================================================================ deterministic exports
_TSV_COLS = ("watchlist_id", "execution_id", "item_id", "source_id", "normalized_locator",
             "evidence_type", "field_path", "change_type", "previous_value", "current_value",
             "observed_unit", "observed_currency", "numeric_delta", "numeric_percent_change",
             "comparison_method", "previous_capture_id", "current_capture_id",
             "previous_content_hash", "current_content_hash", "owner_rule_matches", "warnings")


def _tsv(value):
    if isinstance(value, (list, tuple)):
        value = ";".join(str(v) for v in value)
    return R._tsv_cell(value)


def _write_reports(cfg, wl, execution, comparisons, changes, alerts):
    wl_id = wl["watchlist_id"]
    exec_id = execution["execution_id"]
    out_dir = os.path.join(cfg.reports_dir, exec_id)
    counts, _ = _alert_status_counts(cfg, wl_id)
    alert_rows = _alert_view(cfg, wl_id)

    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA,
        "watchlist_id": wl_id, "name": wl["name"], "execution_id": exec_id,
        "research_run_id": execution["research_run_id"], "readiness": execution["readiness"],
        "change_count": execution["change_count"], "change_counts": execution["change_counts"],
        "source_status": [{"item_id": c["item_id"], "source_id": c["source_id"],
                           "readiness": _item_readiness(c), "units": c["units"]} for c in comparisons],
        "owner_rule_ids": sorted({r["rule_id"] for r in wl.get("alert_rules", [])}),
        "alert_counts": {**counts, "total": sum(counts.values())},
        "network_purpose_counters": execution.get("network_purpose_counters", {}),
        "disclaimers": list(DISCLAIMERS),
    }
    snapshot.update(R.AMAZON_COUNTERS)
    _write_json(os.path.join(out_dir, "watchlist_snapshot.json"), snapshot)

    lines = ["\t".join(_TSV_COLS)]
    for c in sorted(changes, key=lambda x: (x["item_id"], x["normalized_locator"], x["evidence_type"],
                                            x["field_path"], x["change_id"])):
        row = {**c, "watchlist_id": wl_id, "execution_id": exec_id,
               "owner_rule_matches": c.get("owner_rule_matches", [])}
        lines.append("\t".join(_tsv(row.get(col)) for col in _TSV_COLS))
    _write_text(os.path.join(out_dir, "watchlist_changes.tsv"), "\n".join(lines) + "\n")

    _write_json(os.path.join(out_dir, "watchlist_alerts.json"), {
        "schema_version": ALERT_SCHEMA, "watchlist_id": wl_id, "execution_id": exec_id,
        "alerts": alert_rows, "counts": {**counts, "total": sum(counts.values())},
        **R.AMAZON_COUNTERS})

    _write_text(os.path.join(out_dir, "watchlist_report.md"),
                _render_report(wl, execution, comparisons, changes, alert_rows, counts))
    return out_dir


def _render_report(wl, execution, comparisons, changes, alert_rows, counts):
    L = ["# Phase 7.11 — Connected Research Watchlist", "",
         f"- Watchlist: **{wl['name']}** (`{wl['watchlist_id']}`)",
         f"- Execution: `{execution['execution_id']}`",
         f"- Research run: `{execution['research_run_id']}`",
         f"- Readiness: **{execution['readiness']}**",
         f"- Changes: {execution['change_count']}", ""]
    L.append("## Change counts")
    for st in sorted(execution["change_counts"]):
        L.append(f"- {st}: {execution['change_counts'][st]}")
    L.append("")
    L.append("## Sources")
    for c in comparisons:
        L.append(f"### item `{c['item_id']}` — {_item_readiness(c)}")
        for u in c["units"]:
            L.append(f"- `{u['normalized_locator']}` — {u['status']} — content_changed="
                     f"{u['content_hash_changed']} evidence_changed={u['evidence_changed']}")
    L.append("")
    L.append("## Changes")
    for c in sorted(changes, key=lambda x: (x["item_id"], x["field_path"], x["change_id"])):
        cur = f" {c['observed_currency']}" if c.get("observed_currency") else ""
        L.append(f"- [{c['change_type']}] {c['evidence_type']}/{c['field_path']}: "
                 f"`{c.get('previous_value')}` -> `{c.get('current_value')}`{cur}")
    L.append("")
    L.append(f"## Alerts ({counts.get(ALERT_OPEN,0)} open / {counts.get(ALERT_ACKNOWLEDGED,0)} "
             f"acknowledged / {counts.get(ALERT_DISMISSED,0)} dismissed)")
    for a in alert_rows:
        L.append(f"- [{a['severity']}] {a['label']} — {a['message']} ({a['status']}, rule `{a['rule_id']}`)")
    L.append("")
    L.append("## Disclaimers")
    for d in DISCLAIMERS:
        L.append(f"- {d}")
    L.append("")
    L.append("## Amazon-account boundary (constant zero)")
    for k in sorted(R.AMAZON_COUNTERS):
        L.append(f"- {k}: {R.AMAZON_COUNTERS[k]}")
    return "\n".join(L) + "\n"


def export(cfg, watchlist_id, execution_id=None):
    wl = load_watchlist(cfg, watchlist_id)
    if execution_id is None:
        state = _load_state(cfg, watchlist_id)
        execution_id = state.get("last_execution_id")
        if execution_id is None:
            return {"command": "export", "readiness": BASELINE_REQUIRED, "ok": False,
                    "watchlist_id": watchlist_id, "detail": "no execution to export",
                    "amazon_counters": dict(R.AMAZON_COUNTERS)}
    epath = os.path.join(cfg.executions_dir, execution_id + ".json")
    if not os.path.isfile(epath):
        raise WatchlistError("EXECUTION_NOT_FOUND", execution_id, readiness=BASELINE_REQUIRED)
    execution = _load_json(epath, "execution")
    comparisons = [_load_json(os.path.join(cfg.comparisons_dir, cid + ".json"), "comparison")
                   for cid in execution["comparison_ids"]]
    changes = []
    cpath = os.path.join(cfg.changes_dir, execution_id + ".json")
    if os.path.isfile(cpath):
        changes = _load_json(cpath, "changes").get("changes", [])
    out_dir = _write_reports(cfg, wl, execution, comparisons, changes, [])
    return {"command": "export", "readiness": EXPORT_READY, "ok": True, "watchlist_id": watchlist_id,
            "execution_id": execution_id,
            "exports": [_rel(cfg, os.path.join(out_dir, n)) for n in
                        ("watchlist_snapshot.json", "watchlist_changes.tsv", "watchlist_report.md",
                         "watchlist_alerts.json")],
            "amazon_counters": dict(R.AMAZON_COUNTERS)}


# ================================================================ scheduler plan (read-only; owner registers)
def _pwsq(s):
    """PowerShell single-quote a path (doubles embedded single quotes). Never executes anything."""
    return "'" + str(s).replace("'", "''") + "'"


def _shq(s):
    """POSIX shell single-quote."""
    return "'" + str(s).replace("'", "'\\''") + "'"


def scheduler_plan(cfg, watchlist_id, flavor="all"):
    """Objective 15 — emit a READ-ONLY OS-scheduler plan the OWNER may register. This never runs or
    registers schtasks / cron / crontab / systemctl / launchctl / Task Scheduler COM. It contains no
    secret and no Seller-Central target, and is deterministic apart from operational path metadata."""
    wl = load_watchlist(cfg, watchlist_id)
    python_exe = sys.executable or "python"
    base = cfg.base_dir
    research = cfg.research_dir or "runs/T2/phase7/7.10"
    module = "production.phase7_connected_research_watchlists"

    win = ("powershell.exe -NoProfile -Command \"& " + _pwsq(python_exe) + " -m " + module +
           " --base-dir " + _pwsq(base) + " --research-dir " + _pwsq(research) + " run-due\"")
    cron = ("0 * * * * " + _shq(python_exe) + " -m " + module + " --base-dir " + _shq(base) +
            " --research-dir " + _shq(research) + " run-due")
    manual = (_shq(python_exe) + " -m " + module + " --base-dir " + _shq(base) + " --research-dir " +
              _shq(research) + " run-watchlist --watchlist-id " + _shq(watchlist_id))

    template = {"module": module, "subcommand": "run-due", "schedule_type": wl["schedule"]["type"],
                "minimum_interval_hours": MIN_INTERVAL_HOURS, "flavors": ["windows", "cron", "manual"]}
    plan = {
        "schema_version": SCHEDULER_PLAN_SCHEMA,
        "watchlist_id": watchlist_id,
        "owner_action_required": "OWNER_ACTION_REQUIRED",
        "registration_is_owner_action": True,
        "note": ("Phase 7.11 never registers or executes an OS scheduler task. Review the example "
                 "below and register it yourself; the minimum automatic interval is 1 hour."),
        "windows_task_scheduler_example": win,
        "cron_example": cron,
        "manual_invocation": manual,
        "template_hash": _sha(template),
    }
    plan.update(R.AMAZON_COUNTERS)
    if flavor in ("windows", "cron", "manual"):
        plan["selected_flavor"] = flavor
    _write_json(os.path.join(cfg.scheduler_plans_dir, watchlist_id + ".json"), plan)
    return {"command": "scheduler-plan", "readiness": SCHEDULER_PLAN_READY, "ok": True,
            "watchlist_id": watchlist_id, "plan": plan, "amazon_counters": dict(R.AMAZON_COUNTERS)}


# ================================================================ validate-only / validate-watchlist
def validate_only(definition_path):
    """validate-only — parse + validate a watchlist definition and perform NO network request, NO
    DNS resolution, write NO file, create NO base directory, acquire NO lock, mutate NO alert state,
    launch NO browser, and execute NO subprocess."""
    with open(definition_path, "rb") as f:
        defn = R._loads_strict(f.read(), "watchlist")
    canonical, watchlist_id = validate_watchlist(defn)
    enabled_sources = sum(1 for s in canonical["sources"] if s["descriptor"].get("enabled", True))
    return {"command": "validate-only", "readiness": VALIDATE_READY, "ok": True,
            "watchlist_id": watchlist_id, "name": canonical["name"],
            "source_count": len(canonical["sources"]), "enabled_source_count": enabled_sources,
            "rule_count": len(canonical["alert_rules"]), "schedule_type": canonical["schedule"]["type"],
            "files_written": 0, "network_requests": 0, "locks_acquired": 0,
            "amazon_counters": dict(R.AMAZON_COUNTERS)}


def validate_watchlist_cmd(cfg, *, definition_path=None, watchlist_id=None):
    if definition_path is not None:
        with open(definition_path, "rb") as f:
            defn = R._loads_strict(f.read(), "watchlist")
        canonical, wid = validate_watchlist(defn)
    elif watchlist_id is not None:
        canonical = load_watchlist(cfg, watchlist_id)
        wid = canonical["watchlist_id"]
    else:
        raise WatchlistError("VALIDATE_INPUT_REQUIRED", "provide --definition or --watchlist-id",
                             readiness=WATCHLIST_REQUIRED)
    return {"command": "validate-watchlist", "readiness": VALIDATE_READY, "ok": True,
            "watchlist_id": wid, "name": canonical["name"],
            "source_count": len(canonical["sources"]), "rule_count": len(canonical["alert_rules"]),
            "amazon_counters": dict(R.AMAZON_COUNTERS)}


# ================================================================ CLI
def _print(obj):
    sys.stdout.write(canonical_json(_redact(obj)) + "\n")


def _exit_code(result):
    return 0 if result.get("readiness") in _EXIT_OK else 3


def _read_definition(path):
    with open(path, "rb") as f:
        return R._loads_strict(f.read(), "watchlist")


def build_parser():
    p = argparse.ArgumentParser(
        prog="python -m production.phase7_connected_research_watchlists",
        description="Phase 7.11 — connected research watchlists, change detection & LOCAL owner "
                    "alerts. Reuses the accepted Phase 7.10 authority for all network work; never "
                    "connects to an Amazon seller account.")
    p.add_argument("--base-dir", default="runs/T2/phase7/7.11",
                   help="Phase 7.11 workspace (default: runs/T2/phase7/7.11)")
    p.add_argument("--research-dir", default="runs/T2/phase7/7.10",
                   help="accepted, READ-ONLY Phase 7.10 workspace for the compare command")
    p.add_argument("--allow-local-test-server", action="store_true",
                   help="permit an http:// loopback endpoint via the reused 7.10 authority (tests)")
    sub = p.add_subparsers(dest="command", required=True)

    cw = sub.add_parser("create-watchlist", help="validate + persist a watchlist definition")
    cw.add_argument("--definition", required=True)
    vw = sub.add_parser("validate-watchlist", help="validate a watchlist definition or stored watchlist")
    vw.add_argument("--definition")
    vw.add_argument("--watchlist-id")
    sw = sub.add_parser("show-watchlist", help="print a stored watchlist")
    sw.add_argument("--watchlist-id", required=True)
    sub.add_parser("list-watchlists", help="list stored watchlists")
    rw = sub.add_parser("run-watchlist", help="run one watchlist manually (a forced run)")
    rw.add_argument("--watchlist-id", required=True)
    rw.add_argument("--reference-time", default=None)
    rw.add_argument("--break-stale-lock", action="store_true")
    rd = sub.add_parser("run-due", help="run every watchlist whose schedule is due")
    rd.add_argument("--reference-time", default=None)
    cm = sub.add_parser("compare", help="replay change detection between two existing 7.10 runs")
    cm.add_argument("--watchlist-id", required=True)
    cm.add_argument("--previous-run-id", required=True)
    cm.add_argument("--current-run-id", required=True)
    le = sub.add_parser("list-executions", help="list a watchlist's executions")
    le.add_argument("--watchlist-id", required=True)
    lc = sub.add_parser("list-changes", help="list a watchlist's change events")
    lc.add_argument("--watchlist-id", required=True)
    lc.add_argument("--execution-id", default=None)
    la = sub.add_parser("list-alerts", help="list a watchlist's alerts")
    la.add_argument("--watchlist-id", required=True)
    la.add_argument("--status", default=None, choices=sorted(_ALERT_STATUSES))
    for name, help_text in (("acknowledge-alert", "acknowledge an alert"),
                            ("dismiss-alert", "dismiss an alert"),
                            ("reopen-alert", "reopen an alert")):
        ap = sub.add_parser(name, help=help_text)
        ap.add_argument("--alert-id", required=True)
        ap.add_argument("--actor", required=True)
        ap.add_argument("--note", default=None)
    vs = sub.add_parser("verify-state", help="verify watchlist + alert-history integrity")
    vs.add_argument("--watchlist-id", required=True)
    ex = sub.add_parser("export", help="write deterministic exports for an execution")
    ex.add_argument("--watchlist-id", required=True)
    ex.add_argument("--execution-id", default=None)
    sp = sub.add_parser("scheduler-plan", help="emit a read-only OS-scheduler plan (owner registers)")
    sp.add_argument("--watchlist-id", required=True)
    sp.add_argument("--flavor", default="all", choices=("all", "windows", "cron", "manual"))
    vo = sub.add_parser("validate-only", help="validate a definition; no network, no files, no lock")
    vo.add_argument("--definition", required=True)
    return p


def _dispatch(cfg, args):
    cmd = args.command
    if cmd == "create-watchlist":
        return create_watchlist(cfg, _read_definition(args.definition))
    if cmd == "validate-watchlist":
        return validate_watchlist_cmd(cfg, definition_path=args.definition,
                                      watchlist_id=args.watchlist_id)
    if cmd == "show-watchlist":
        return show_watchlist(cfg, args.watchlist_id)
    if cmd == "list-watchlists":
        return list_watchlists(cfg)
    if cmd == "run-watchlist":
        return run_watchlist(cfg, args.watchlist_id, reference_time=args.reference_time,
                             break_stale=args.break_stale_lock)
    if cmd == "run-due":
        return run_due(cfg, reference_time=args.reference_time)
    if cmd == "compare":
        return compare(cfg, args.watchlist_id, args.previous_run_id, args.current_run_id)
    if cmd == "list-executions":
        return list_executions(cfg, args.watchlist_id)
    if cmd == "list-changes":
        return list_changes(cfg, args.watchlist_id, args.execution_id)
    if cmd == "list-alerts":
        return list_alerts(cfg, args.watchlist_id, args.status)
    if cmd == "acknowledge-alert":
        return _alert_action(cfg, args.alert_id, ACT_ACKNOWLEDGE, args.actor, args.note)
    if cmd == "dismiss-alert":
        return _alert_action(cfg, args.alert_id, ACT_DISMISS, args.actor, args.note)
    if cmd == "reopen-alert":
        return _alert_action(cfg, args.alert_id, ACT_REOPEN, args.actor, args.note)
    if cmd == "verify-state":
        return verify_state(cfg, args.watchlist_id)
    if cmd == "export":
        return export(cfg, args.watchlist_id, args.execution_id)
    if cmd == "scheduler-plan":
        return scheduler_plan(cfg, args.watchlist_id, args.flavor)
    if cmd == "validate-only":
        return validate_only(args.definition)
    raise WatchlistError("UNKNOWN_COMMAND", cmd)   # pragma: no cover - argparse enforces choices


def run_cli(argv=None, *, cfg_factory=None):
    args = build_parser().parse_args(argv)
    if cfg_factory is not None:
        cfg = cfg_factory(args)
    else:
        cfg = Config(args.base_dir, research_dir=args.research_dir,
                     allow_local=args.allow_local_test_server)
    try:
        result = _dispatch(cfg, args)
    except (WatchlistError, R.ResearchError, R.PlanError) as e:
        readiness = getattr(e, "readiness", WATCHLIST_BLOCKED)
        result = {"command": args.command, "readiness": readiness, "ok": False,
                  "error_code": getattr(e, "code", "ERROR"),
                  "detail": _redact(str(getattr(e, "detail", ""))),
                  "amazon_counters": dict(R.AMAZON_COUNTERS)}
    except NP.ConnectedNetworkDenied as e:
        result = {"command": args.command, "readiness": SELLER_CENTRAL_POLICY_BLOCKED, "ok": False,
                  "error_code": getattr(e, "reason_code", "ERROR"),
                  "amazon_counters": dict(R.AMAZON_COUNTERS)}
    except M.MoneyError as e:
        result = {"command": args.command, "readiness": WATCHLIST_BLOCKED, "ok": False,
                  "error_code": "NUMERIC_INVALID", "detail": _redact(str(e)),
                  "amazon_counters": dict(R.AMAZON_COUNTERS)}
    _print(result)
    return _exit_code(result)


def main(argv=None):
    return run_cli(argv)


if __name__ == "__main__":
    sys.exit(main())











