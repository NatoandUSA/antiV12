#!/usr/bin/env python3
"""Phase 7.12 — Connected Owner Notification Delivery, Digest Queue & Audit Trail.

This module is the ONE Phase 7.12 authority. It lets the owner create + approve *notification
routes*, build deterministic *notification batches* from accepted Phase 7.11 alerts, preview them
locally, write them to a local outbox, and — only for an explicitly owner-approved HTTPS webhook and
only behind a live-delivery gate — POST a fixed-schema JSON notification to that endpoint, with a
bounded retry policy, rate limiting, quiet hours, digest windows, an append-only hash-chained
delivery audit trail, and deterministic JSON/TSV/Markdown reports.

AUTHORITY REUSE — Phase 7.12 performs NO alert work of its own. Watchlist identity, alert identity,
alert status, alert history, owner severity, source lineage, change lineage and state integrity all
come from the accepted Phase 7.11 authority
(:mod:`production.phase7_connected_research_watchlists`, imported as ``W``); its reused Phase 7.10
helpers (imported through ``W`` as ``R``) provide canonical JSON, content hashing, atomic writes,
secret redaction and TSV neutralization. Every webhook endpoint is validated by the ONE network
authority (:mod:`core.network_policy`). Phase 7.12 never modifies any Phase 7.11 alert state and
never acknowledges/dismisses/reopens an alert.

PERMANENT AMAZON-ACCOUNT BOUNDARY (never crossable, in any path, by any flag or config): no Seller
Central, no Seller Central login, no seller API, no Ads API, no seller-account OAuth/credentials/
cookies/sessions/tokens, no automatic report downloads, no bulk uploads, no browser automation, no
campaign/bid/budget/target/keyword/negative/listing/inventory mutation. The Amazon-account denial
lives in :mod:`core.network_policy` and always wins before any notification-endpoint allowlist.
Every Phase 7.12 record carries constant-zero Amazon counters — no code path can increment them.

OWNER-ONLY NOTIFICATION SCOPE — Phase 7.12 delivers LOCAL owner alerts to an owner-approved
destination ONLY. It never sends a customer message, review request, marketing campaign, buyer
communication, Amazon message, purchase/checkout request or Seller-Central notification. It runs no
JavaScript, launches no browser, installs no service, and registers no OS scheduler; the scheduler
plan is read-only and the owner registers it. Only HTTP POST to an approved HTTPS webhook is ever
performed; redirects are never followed; arbitrary methods/headers are impossible.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import hmac
import os
import re
import socket
import ssl
import sys
import urllib.error
import urllib.request

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

from production import phase7_connected_research_watchlists as W   # noqa: E402  the ONE 7.11 authority
from production import phase7_connected_public_research as R       # noqa: E402  7.11's reused 7.10 helpers
import network_policy as NP                                        # noqa: E402  canonical endpoint policy
import diagnostics as D                                            # noqa: E402  secret redaction + codes
import money as M                                                  # noqa: E402  Decimal-only numeric safety

# ================================================================ identity / versions
STAGE_ID = "7.12"
STAGE_NAME = "Connected Owner Notification Delivery, Digest Queue & Audit Trail"
TOOL_VERSION = "phase7.12.0"
TEMPLATE_VERSION = "phase7-12-template-v1"
CONTENT_POLICY_VERSION = "phase7-12-content-policy-v1"

ROUTE_SCHEMA = "phase7-12-route-v1"
APPROVAL_STATE_SCHEMA = "phase7-12-approval-state-v1"
BATCH_SCHEMA = "phase7-12-batch-v1"
PAYLOAD_SCHEMA = "phase7-12-notification-v1"
DELIVERY_SCHEMA = "phase7-12-delivery-v1"
DELIVERY_HISTORY_SCHEMA = "phase7-12-delivery-history-v1"
DIGEST_SCHEMA = "phase7-12-digest-v1"
OUTBOX_SCHEMA = "phase7-12-outbox-v1"
SCHEDULER_PLAN_SCHEMA = "phase7-12-scheduler-plan-v1"
VALIDATION_SCHEMA = "phase7-12-validation-v1"
REPORT_SCHEMA = "phase7-12-delivery-report-v1"

# environment gates + reference env-variable names (the route stores env NAMES, never secret values)
LIVE_DELIVERY_ENV = "PHASE7_12_ALLOW_LIVE_DELIVERY"
AUTO_SEND_TOKEN_ENV = "PHASE7_12_AUTO_SEND_TOKEN"
DEFAULT_URL_ENV = "PHASE7_12_WEBHOOK_URL"
DEFAULT_BEARER_ENV = "PHASE7_12_WEBHOOK_BEARER_TOKEN"
DEFAULT_HMAC_ENV = "PHASE7_12_WEBHOOK_HMAC_SECRET"

# ---------------------------------------------------------------- readiness states
READY = "SESSION7_12_NOTIFICATION_READY"
READY_EMPTY = "SESSION7_12_NOTIFICATION_READY_EMPTY"
READY_PARTIAL = "SESSION7_12_NOTIFICATION_READY_PARTIAL"
ROUTE_READY = "SESSION7_12_ROUTE_READY"
ROUTE_REQUIRED = "SESSION7_12_ROUTE_REQUIRED"
ROUTE_BLOCKED = "SESSION7_12_ROUTE_BLOCKED"
APPROVAL_READY = "SESSION7_12_APPROVAL_READY"
APPROVAL_REQUIRED = "SESSION7_12_APPROVAL_REQUIRED"
APPROVAL_BLOCKED = "SESSION7_12_APPROVAL_BLOCKED"
BATCH_READY = "SESSION7_12_BATCH_READY"
BATCH_READY_EMPTY = "SESSION7_12_BATCH_READY_EMPTY"
PREVIEW_READY = "SESSION7_12_PREVIEW_READY"
DELIVERY_CONFIRMATION_REQUIRED = "SESSION7_12_DELIVERY_CONFIRMATION_REQUIRED"
DELIVERY_SENT = "SESSION7_12_DELIVERY_SENT"
DELIVERY_FAILED = "SESSION7_12_DELIVERY_FAILED"
DELIVERY_UNKNOWN = "SESSION7_12_DELIVERY_UNKNOWN"
DELIVERY_BLOCKED = "SESSION7_12_DELIVERY_BLOCKED"
PROVIDER_UNAVAILABLE = "SESSION7_12_PROVIDER_UNAVAILABLE"
RATE_LIMITED = "SESSION7_12_RATE_LIMITED"
QUIET_HOURS = "SESSION7_12_QUIET_HOURS"
NOT_DUE = "SESSION7_12_NOT_DUE"
ALERT_SOURCE_BLOCKED = "SESSION7_12_ALERT_SOURCE_BLOCKED"
DELIVERY_STATE_BLOCKED = "SESSION7_12_DELIVERY_STATE_BLOCKED"
INTEGRITY_BLOCKED = "SESSION7_12_INTEGRITY_BLOCKED"
SELLER_CENTRAL_POLICY_BLOCKED = "SESSION7_12_SELLER_CENTRAL_POLICY_BLOCKED"
# operational-success readiness for read/utility commands
LIST_READY = "SESSION7_12_LIST_READY"
SHOW_READY = "SESSION7_12_SHOW_READY"
VERIFY_READY = "SESSION7_12_VERIFY_READY"
EXPORT_READY = "SESSION7_12_EXPORT_READY"
SCHEDULER_PLAN_READY = "SESSION7_12_SCHEDULER_PLAN_READY"
VALIDATE_READY = "SESSION7_12_VALIDATE_READY"
PROVIDER_CHECK_READY = "SESSION7_12_PROVIDER_CHECK_READY"
LOCK_READY = "SESSION7_12_LOCK_READY"

# readiness values that mean success/neutral -> process exit 0. Everything else exits nonzero.
_EXIT_OK = frozenset({
    READY, READY_EMPTY, READY_PARTIAL, ROUTE_READY, APPROVAL_READY, BATCH_READY, BATCH_READY_EMPTY,
    PREVIEW_READY, DELIVERY_SENT, QUIET_HOURS, NOT_DUE, RATE_LIMITED, LIST_READY, SHOW_READY,
    VERIFY_READY, EXPORT_READY, SCHEDULER_PLAN_READY, VALIDATE_READY, PROVIDER_CHECK_READY,
    LOCK_READY,
})

# ---------------------------------------------------------------- channels / payload formats
CH_LOCAL_OUTBOX = "local-outbox"
CH_HTTPS_WEBHOOK = "https-json-webhook"
_CHANNELS = frozenset({CH_LOCAL_OUTBOX, CH_HTTPS_WEBHOOK})

PF_GENERIC = "generic-json"
PF_SLACK = "slack"
PF_DISCORD = "discord"
PF_TEAMS = "teams"
_PAYLOAD_FORMATS = frozenset({PF_GENERIC, PF_SLACK, PF_DISCORD, PF_TEAMS})

# ---------------------------------------------------------------- authentication modes
AUTH_NONE = "none"
AUTH_BEARER = "bearer-env"
AUTH_HMAC = "hmac-sha256-env"
_AUTH_MODES = frozenset({AUTH_NONE, AUTH_BEARER, AUTH_HMAC})

# ---------------------------------------------------------------- digest modes
DIGEST_IMMEDIATE = "immediate"
DIGEST_HOURLY = "hourly"
DIGEST_DAILY = "daily"
DIGEST_WEEKLY = "weekly"
DIGEST_MANUAL = "manual"
_DIGEST_MODES = frozenset({DIGEST_IMMEDIATE, DIGEST_HOURLY, DIGEST_DAILY, DIGEST_WEEKLY,
                           DIGEST_MANUAL})
_AUTOMATIC_DIGEST = frozenset({DIGEST_HOURLY, DIGEST_DAILY, DIGEST_WEEKLY})
MIN_DIGEST_INTERVAL_HOURS = 1

# ---------------------------------------------------------------- delivery states
DS_QUEUED = "QUEUED"
DS_PREVIEWED = "PREVIEWED"
DS_SENT = "SENT"
DS_FAILED = "FAILED"
DS_UNKNOWN = "UNKNOWN"
DS_RATE_LIMITED = "RATE_LIMITED"
DS_QUIET_HOURS = "QUIET_HOURS"
DS_SKIPPED = "SKIPPED"
DS_BLOCKED = "BLOCKED"
DS_REVOKED = "REVOKED"
DS_CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
DS_PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
_DELIVERY_STATES = frozenset({
    DS_QUEUED, DS_PREVIEWED, DS_SENT, DS_FAILED, DS_UNKNOWN, DS_RATE_LIMITED, DS_QUIET_HOURS,
    DS_SKIPPED, DS_BLOCKED, DS_REVOKED, DS_CONFIRMATION_REQUIRED, DS_PROVIDER_UNAVAILABLE,
})

# ---------------------------------------------------------------- delivery-history event types
EV_QUEUED = "QUEUED"
EV_PREVIEWED = "PREVIEWED"
EV_SEND_STARTED = "SEND_STARTED"
EV_SENT = "SENT"
EV_FAILED = "FAILED"
EV_UNKNOWN = "UNKNOWN"
EV_RETRY_SCHEDULED = "RETRY_SCHEDULED"
EV_RETRY_STARTED = "RETRY_STARTED"
EV_RATE_LIMITED = "RATE_LIMITED"
EV_BLOCKED = "BLOCKED"
EV_REVOKED = "REVOKED"
EV_OWNER_RETRY_APPROVED = "OWNER_RETRY_APPROVED"
_EVENT_TYPES = frozenset({
    EV_QUEUED, EV_PREVIEWED, EV_SEND_STARTED, EV_SENT, EV_FAILED, EV_UNKNOWN, EV_RETRY_SCHEDULED,
    EV_RETRY_STARTED, EV_RATE_LIMITED, EV_BLOCKED, EV_REVOKED, EV_OWNER_RETRY_APPROVED,
})

# ---------------------------------------------------------------- approval model
APR_APPROVED = "APPROVED"
APR_REVOKED = "REVOKED"
_APPROVAL_STATUSES = frozenset({APR_APPROVED, APR_REVOKED})
MODE_LOCAL = "local"
MODE_LIVE = "live"
_DELIVERY_MODES = frozenset({MODE_LOCAL, MODE_LIVE})

# owner severities are owner-defined by Phase 7.11 — never computed or scored here
_SEVERITIES = W._SEVERITIES
_SEV_ORDER = {W.SEV_INFO: 0, W.SEV_REVIEW: 1, W.SEV_IMPORTANT: 2, W.SEV_CRITICAL: 3}
# alert statuses that are eligible by default (OPEN only; ACK/DISMISSED excluded unless configured)
DEFAULT_ALERT_STATUS = W.ALERT_OPEN

# ---------------------------------------------------------------- schema key sets
_ROUTE_TOP_KEYS = frozenset({
    "schema_version", "route_id", "name", "description", "enabled", "channel", "payload_format",
    "destination_label", "endpoint_env", "endpoint_host_allowlist", "authentication", "filter",
    "digest_policy", "quiet_hours", "retry_policy", "rate_limit", "content_policy", "tags",
    "owner_notes", "auto_send_approved",
})
_AUTH_KEYS = frozenset({"mode", "token_env", "secret_env"})
_FILTER_KEYS = frozenset({
    "watchlist_ids", "rule_ids", "severities", "statuses", "source_types", "field_paths",
    "owner_labels", "include_acknowledged", "include_dismissed",
})
_DIGEST_KEYS = frozenset({"mode", "timezone"})
_QUIET_KEYS = frozenset({"enabled", "timezone", "start", "end", "weekdays", "severity_bypass"})
_RETRY_KEYS = frozenset({"enabled", "max_attempts", "initial_delay_seconds",
                         "maximum_delay_seconds", "retryable_statuses"})
_RATE_KEYS = frozenset({"max_per_route_per_hour", "max_per_destination_per_hour",
                        "minimum_interval_seconds", "max_alerts_per_batch", "max_payload_bytes"})
_CONTENT_KEYS = frozenset({"fields", "max_alerts", "truncate"})

# a route field/key whose presence signals a secret / arbitrary header / command / template / path
_FORBIDDEN_KEY_SUBSTR = ("secret", "password", "passwd", "credential", "cookie", "session",
                         "bearer", "header", "command", "cmd", "shell", "subprocess", "eval",
                         "exec", "proxy", "template", "jinja", "script", "webhook_url", "url_literal")
# route STRING VALUES may never contain a literal URL / userinfo / bearer token / secret token.
_URL_LITERAL_RE = re.compile(r"://")
_BEARER_LITERAL_RE = re.compile(r"(?i)\bbearer\s+\S")
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")

# the fixed alert-field allowlist a route may include (never raw HTML / headers / endpoint / path /
# private notes / secret env values / customer info / full source body)
_ALERT_FIELD_ALLOWLIST = ("alert_id", "watchlist_label", "owner_severity", "owner_label",
                          "owner_message", "rule_id", "field_path", "previous_value",
                          "current_value", "source_label", "source_type", "capture_id", "change_id")
_DEFAULT_ALERT_FIELDS = ("alert_id", "watchlist_label", "owner_severity", "owner_label",
                         "owner_message", "rule_id", "field_path", "previous_value",
                         "current_value", "source_label", "source_type")

# ---------------------------------------------------------------- webhook request/response bounds
DEFAULT_PAYLOAD_BYTES = 64 * 1024
ABSOLUTE_MAX_PAYLOAD_BYTES = 256 * 1024
RESPONSE_READ_LIMIT = 64 * 1024
DEFAULT_CONNECT_TIMEOUT = 10
DEFAULT_READ_TIMEOUT = 20
FIXED_CONTENT_TYPE = "application/json"
USER_AGENT = "AMZ-FBM-Toolkit-OwnerNotify/7.12 (+local owner tool; owner-approved destinations only)"
HMAC_SIG_HEADER = "X-Phase7-Signature"
HMAC_TSV_HEADER = "X-Phase7-Timestamp-Version"
_SAFE_RESPONSE_HEADERS = ("content-type", "content-length", "retry-after", "date")

# retry bounds
RETRY_MAX_ATTEMPTS_CEILING = 5
RETRY_MIN_INITIAL_DELAY = 1
RETRY_MAX_DELAY_CEILING = 3600
_DEFAULT_RETRYABLE = (408, 429, 500, 502, 503, 504)
_RETRYABLE_ALLOWED = frozenset({408, 429, 500, 502, 503, 504, 522, 524})
# 4xx that are terminal (never retried) even if a route lists them
_TERMINAL_4XX = frozenset({400, 401, 403, 404, 405, 409, 410, 422})

MAX_LOCK_AGE_SECONDS = 6 * 3600
_TRUNCATE_VALUE_CHARS = 500

DISCLAIMERS = (
    "Phase 7.12 delivers LOCAL owner alerts from the accepted Phase 7.11 authority to an "
    "owner-approved destination only. It never sends a customer message, review request, marketing "
    "campaign, buyer communication, Amazon message or Seller-Central notification.",
    "No data here came from Amazon Seller Central. The toolkit never connects to an Amazon seller "
    "account, seller API, ads API, or seller credentials, cookies, sessions or tokens.",
    "Owner severity is owner-defined by Phase 7.11; Phase 7.12 never calculates a severity, urgency, "
    "score, demand estimate or recommendation. It uses fixed built-in templates only.",
    "Live HTTPS delivery is gated: it requires an explicit owner approval bound to the exact route "
    "content, an environment permission, an approved endpoint host, and an exact confirmation token.",
)


# ================================================================ errors
class NotificationError(Exception):
    """A safe, secret-free Phase 7.12 failure carrying a stable ``code`` and redacted detail."""

    def __init__(self, code, detail="", *, readiness=ROUTE_BLOCKED):
        self.code = code
        self.detail = R.redact(detail)
        self.readiness = readiness
        super().__init__(f"{code}: {self.detail}" if self.detail else code)


class _PreSendError(RuntimeError):
    """The transport failed to connect / send bytes (retryable, never a confirmed delivery)."""


class _PostSendTimeout(RuntimeError):
    """The request bytes were transmitted but no response arrived (UNKNOWN, never auto-retried)."""


class _ProviderUnavailable(RuntimeError):
    """The transport could not reach the provider at all (retryable)."""


# ================================================================ small helpers (identity is content-only)
def canonical_json(obj):
    return R.canonical_json(obj)


def _sha(obj):
    return R._sha256_hex(canonical_json(obj))


def _cid(prefix, obj):
    """A stable, content-addressed identifier. Never depends on time, mtime, path, pid or order."""
    return prefix + "-" + _sha(obj)[:24]


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
        raise NotificationError("STATE_INTEGRITY_MISSING", what, readiness=readiness)
    body = {k: v for k, v in rec.items() if k != "integrity_hash"}
    if R.content_sha256(body) != rec["integrity_hash"]:
        raise NotificationError("STATE_INTEGRITY_MISMATCH", what, readiness=readiness)
    return body


def _rel(cfg, path):
    try:
        return os.path.relpath(path, cfg.base_dir).replace(os.sep, "/")
    except ValueError:
        return os.path.basename(path)


def _now_epoch(cfg):
    return float(cfg.now_fn())


# ================================================================ configuration
class Config:
    """Phase 7.12 workspace + injectable seams. ``alert_dir`` is the accepted, READ-ONLY Phase 7.11
    workspace (its alerts are never modified). The transport / resolver / clock seams let every unit
    test run with no live Internet access."""

    __slots__ = ("base_dir", "alert_dir", "env", "transport", "resolver", "allow_local", "now_fn",
                 "pid", "connect_timeout", "read_timeout")

    def __init__(self, base_dir, *, alert_dir=None, env=None, transport=None, resolver=None,
                 allow_local=False, now_fn=None, pid=None, connect_timeout=DEFAULT_CONNECT_TIMEOUT,
                 read_timeout=DEFAULT_READ_TIMEOUT):
        self.base_dir = os.path.abspath(base_dir)
        self.alert_dir = os.path.abspath(alert_dir) if alert_dir else None
        self.env = dict(env) if env is not None else dict(os.environ)
        self.transport = transport
        self.resolver = resolver
        self.allow_local = bool(allow_local)
        self.now_fn = now_fn or __import__("time").time
        self.pid = int(pid) if pid is not None else os.getpid()
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout

    def _sub(self, name):
        return os.path.join(self.base_dir, name)

    routes_dir = property(lambda self: self._sub("routes"))
    approvals_dir = property(lambda self: self._sub("approvals"))
    batches_dir = property(lambda self: self._sub("batches"))
    outbox_dir = property(lambda self: self._sub("outbox"))
    deliveries_dir = property(lambda self: self._sub("deliveries"))
    delivery_history_dir = property(lambda self: self._sub("delivery_history"))
    retries_dir = property(lambda self: self._sub("retries"))
    digests_dir = property(lambda self: self._sub("digests"))
    reports_dir = property(lambda self: self._sub("reports"))
    scheduler_plans_dir = property(lambda self: self._sub("scheduler_plans"))
    validation_dir = property(lambda self: self._sub("validation"))
    locks_dir = property(lambda self: self._sub("locks"))
    logs_dir = property(lambda self: self._sub("logs"))

    def watchlist_config(self):
        """A READ-ONLY reused Phase 7.11 Config bound to the accepted alert workspace. No transport
        is wired (Phase 7.12 never runs a watchlist); it only reads accepted alert artefacts."""
        if not self.alert_dir:
            raise NotificationError("ALERT_DIR_REQUIRED", "an --alert-dir is required",
                                    readiness=ALERT_SOURCE_BLOCKED)
        return W.Config(self.alert_dir, env=self.env)


# ================================================================ validation: forbidden scan
def _scan_route_values(obj, path=""):
    """Reject any STRING VALUE that is a literal URL, URL userinfo, bearer token or key-shaped
    secret. Env-variable NAMES are allowed; secret VALUES are not. Returns the first bad path."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            bad = _scan_route_values(v, f"{path}/{k}")
            if bad:
                return bad
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            bad = _scan_route_values(v, f"{path}[{i}]")
            if bad:
                return bad
    elif isinstance(obj, str):
        if _URL_LITERAL_RE.search(obj):
            return f"{path}: literal URL"
        if _BEARER_LITERAL_RE.search(obj):
            return f"{path}: literal bearer token"
        if D.redact_secrets(obj) != obj:
            return f"{path}: secret-shaped value"
    return None


def _reject_forbidden_keys(obj, path="route"):
    """Deep-scan a route for a forbidden KEY (secret/header/cookie/command/template shape). Any such
    key is rejected regardless of nesting depth. Returns the first bad key path, else None."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if any(sub in kl for sub in _FORBIDDEN_KEY_SUBSTR):
                return f"{path}/{k}"
            bad = _reject_forbidden_keys(v, f"{path}/{k}")
            if bad:
                return bad
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            bad = _reject_forbidden_keys(v, f"{path}[{i}]")
            if bad:
                return bad
    return None


def _env_name(value, where):
    if not isinstance(value, str) or not _ENV_NAME_RE.match(value):
        raise NotificationError("ENV_NAME_INVALID",
                                f"{where}: must be an UPPER_SNAKE environment-variable NAME")
    if D.redact_secrets(value) != value or value in D.SECRET_ENV_VARS:
        raise NotificationError("ENV_NAME_INVALID", f"{where}: must be a NAME, never a secret value")
    return value


def _str_list(value, where, *, lower=False):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise NotificationError("ROUTE_INVALID", f"{where}: must be a list of strings")
    out = [x.strip() for x in value if x.strip()]
    if lower:
        out = [x.lower() for x in out]
    return sorted(set(out))


# ================================================================ validation: sub-objects
def _normalize_authentication(auth, channel, where="authentication"):
    if auth is None:
        auth = {"mode": AUTH_NONE}
    if not isinstance(auth, dict):
        raise NotificationError("ROUTE_INVALID", f"{where}: must be an object")
    unknown = set(auth) - _AUTH_KEYS
    if unknown:
        raise NotificationError("ROUTE_UNKNOWN_FIELD", f"{where}: {sorted(unknown)}")
    mode = auth.get("mode", AUTH_NONE)
    if mode not in _AUTH_MODES:
        raise NotificationError("AUTH_MODE_INVALID", f"{where}: {mode!r}")
    out = {"mode": mode, "token_env": None, "secret_env": None}
    if mode == AUTH_BEARER:
        out["token_env"] = _env_name(auth.get("token_env", DEFAULT_BEARER_ENV), f"{where}.token_env")
    elif mode == AUTH_HMAC:
        out["secret_env"] = _env_name(auth.get("secret_env", DEFAULT_HMAC_ENV), f"{where}.secret_env")
    if mode != AUTH_NONE and channel == CH_LOCAL_OUTBOX:
        raise NotificationError("AUTH_MODE_INVALID",
                                f"{where}: local-outbox never authenticates (mode must be none)")
    return out


def _normalize_filter(flt, where="filter"):
    if flt is None:
        flt = {}
    if not isinstance(flt, dict):
        raise NotificationError("ROUTE_INVALID", f"{where}: must be an object")
    unknown = set(flt) - _FILTER_KEYS
    if unknown:
        raise NotificationError("ROUTE_UNKNOWN_FIELD", f"{where}: {sorted(unknown)}")
    sev = _str_list(flt.get("severities"), f"{where}.severities")
    for s in sev:
        if s not in _SEVERITIES:
            raise NotificationError("ROUTE_INVALID", f"{where}.severities: unknown {s!r}")
    statuses = _str_list(flt.get("statuses"), f"{where}.statuses")
    for s in statuses:
        if s not in W._ALERT_STATUSES:
            raise NotificationError("ROUTE_INVALID", f"{where}.statuses: unknown {s!r}")
    for key in ("include_acknowledged", "include_dismissed"):
        if key in flt and not isinstance(flt[key], bool):
            raise NotificationError("ROUTE_INVALID", f"{where}.{key}: must be a boolean")
    return {
        "watchlist_ids": _str_list(flt.get("watchlist_ids"), f"{where}.watchlist_ids"),
        "rule_ids": _str_list(flt.get("rule_ids"), f"{where}.rule_ids"),
        "severities": sev,
        "statuses": statuses,
        "source_types": _str_list(flt.get("source_types"), f"{where}.source_types"),
        "field_paths": _str_list(flt.get("field_paths"), f"{where}.field_paths"),
        "owner_labels": _str_list(flt.get("owner_labels"), f"{where}.owner_labels"),
        "include_acknowledged": bool(flt.get("include_acknowledged", False)),
        "include_dismissed": bool(flt.get("include_dismissed", False)),
    }


def _validate_timezone(tzname, where):
    if not isinstance(tzname, str) or not tzname.strip():
        raise NotificationError("TIMEZONE_INVALID", f"{where}: must be a non-empty string")
    if ZoneInfo is None:  # pragma: no cover
        raise NotificationError("TIMEZONE_UNSUPPORTED", "zoneinfo unavailable")
    try:
        return ZoneInfo(tzname)
    except (ZoneInfoNotFoundError, ValueError, OSError, KeyError):
        raise NotificationError("TIMEZONE_INVALID", f"{where}: unknown timezone {tzname!r}")


def _normalize_digest_policy(dp, where="digest_policy"):
    if dp is None:
        dp = {"mode": DIGEST_IMMEDIATE}
    if not isinstance(dp, dict):
        raise NotificationError("ROUTE_INVALID", f"{where}: must be an object")
    unknown = set(dp) - _DIGEST_KEYS
    if unknown:
        raise NotificationError("ROUTE_UNKNOWN_FIELD", f"{where}: {sorted(unknown)}")
    mode = dp.get("mode", DIGEST_IMMEDIATE)
    if mode not in _DIGEST_MODES:
        raise NotificationError("DIGEST_MODE_INVALID", f"{where}: {mode!r}")
    tzname = dp.get("timezone", "UTC")
    _validate_timezone(tzname, f"{where}.timezone")
    return {"mode": mode, "timezone": tzname}


def _parse_local_time(value, where):
    if not isinstance(value, str) or not re.match(r"^\d{1,2}:\d{2}$", value.strip()):
        raise NotificationError("QUIET_HOURS_INVALID", f"{where}: must be 'HH:MM'")
    hh, mm = (int(x) for x in value.strip().split(":"))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise NotificationError("QUIET_HOURS_INVALID", f"{where}: out of range")
    return "%02d:%02d" % (hh, mm)


def _normalize_weekdays(value, where):
    if value is None:
        return list(range(7))
    if not isinstance(value, list):
        raise NotificationError("QUIET_HOURS_INVALID", f"{where}: weekdays must be a list")
    names = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    out = set()
    for w in value:
        if isinstance(w, bool):
            raise NotificationError("QUIET_HOURS_INVALID", f"{where}: invalid weekday")
        if isinstance(w, int):
            if not 0 <= w <= 6:
                raise NotificationError("QUIET_HOURS_INVALID", f"{where}: weekday out of range")
            out.add(w)
        elif isinstance(w, str) and w.strip().lower()[:3] in names:
            out.add(names.index(w.strip().lower()[:3]))
        else:
            raise NotificationError("QUIET_HOURS_INVALID", f"{where}: invalid weekday {w!r}")
    return sorted(out)


def _normalize_quiet_hours(qh, where="quiet_hours"):
    if qh is None:
        return {"enabled": False, "timezone": "UTC", "start": "00:00", "end": "00:00",
                "weekdays": list(range(7)), "severity_bypass": []}
    if not isinstance(qh, dict):
        raise NotificationError("ROUTE_INVALID", f"{where}: must be an object")
    unknown = set(qh) - _QUIET_KEYS
    if unknown:
        raise NotificationError("ROUTE_UNKNOWN_FIELD", f"{where}: {sorted(unknown)}")
    tzname = qh.get("timezone", "UTC")
    _validate_timezone(tzname, f"{where}.timezone")
    bypass = _str_list(qh.get("severity_bypass"), f"{where}.severity_bypass")
    for s in bypass:
        if s not in _SEVERITIES:
            raise NotificationError("QUIET_HOURS_INVALID", f"{where}.severity_bypass: unknown {s!r}")
    return {
        "enabled": bool(qh.get("enabled", False)),
        "timezone": tzname,
        "start": _parse_local_time(qh.get("start", "00:00"), f"{where}.start"),
        "end": _parse_local_time(qh.get("end", "00:00"), f"{where}.end"),
        "weekdays": _normalize_weekdays(qh.get("weekdays"), f"{where}.weekdays"),
        "severity_bypass": bypass,
    }


def _normalize_retry_policy(rp, where="retry_policy"):
    if rp is None:
        rp = {}
    if not isinstance(rp, dict):
        raise NotificationError("ROUTE_INVALID", f"{where}: must be an object")
    unknown = set(rp) - _RETRY_KEYS
    if unknown:
        raise NotificationError("ROUTE_UNKNOWN_FIELD", f"{where}: {sorted(unknown)}")

    def _int(name, default, lo, hi):
        v = rp.get(name, default)
        if isinstance(v, bool) or not isinstance(v, int):
            raise NotificationError("RETRY_POLICY_INVALID", f"{where}.{name}: must be an int")
        if not (lo <= v <= hi):
            raise NotificationError("RETRY_POLICY_INVALID",
                                    f"{where}.{name}: must be in [{lo}, {hi}]")
        return v

    max_attempts = _int("max_attempts", 3, 1, RETRY_MAX_ATTEMPTS_CEILING)
    initial = _int("initial_delay_seconds", 60, RETRY_MIN_INITIAL_DELAY, RETRY_MAX_DELAY_CEILING)
    maximum = _int("maximum_delay_seconds", 900, RETRY_MIN_INITIAL_DELAY, RETRY_MAX_DELAY_CEILING)
    if maximum < initial:
        raise NotificationError("RETRY_POLICY_INVALID",
                                f"{where}: maximum_delay_seconds < initial_delay_seconds")
    statuses = rp.get("retryable_statuses", list(_DEFAULT_RETRYABLE))
    if not isinstance(statuses, list) or any(isinstance(s, bool) or not isinstance(s, int)
                                             for s in statuses):
        raise NotificationError("RETRY_POLICY_INVALID", f"{where}.retryable_statuses: list of ints")
    for s in statuses:
        if s not in _RETRYABLE_ALLOWED:
            raise NotificationError("RETRY_STATUS_INVALID",
                                    f"{where}.retryable_statuses: {s} is not retryable")
    return {"enabled": bool(rp.get("enabled", True)), "max_attempts": max_attempts,
            "initial_delay_seconds": initial, "maximum_delay_seconds": maximum,
            "retryable_statuses": sorted(set(statuses))}


def _normalize_rate_limit(rl, where="rate_limit"):
    if rl is None:
        rl = {}
    if not isinstance(rl, dict):
        raise NotificationError("ROUTE_INVALID", f"{where}: must be an object")
    unknown = set(rl) - _RATE_KEYS
    if unknown:
        raise NotificationError("ROUTE_UNKNOWN_FIELD", f"{where}: {sorted(unknown)}")

    def _int(name, default, lo, hi):
        v = rl.get(name, default)
        if isinstance(v, bool) or not isinstance(v, int):
            raise NotificationError("RATE_LIMIT_INVALID", f"{where}.{name}: must be an int")
        if not (lo <= v <= hi):
            raise NotificationError("RATE_LIMIT_INVALID", f"{where}.{name}: must be in [{lo}, {hi}]")
        return v

    return {
        "max_per_route_per_hour": _int("max_per_route_per_hour", 12, 1, 10000),
        "max_per_destination_per_hour": _int("max_per_destination_per_hour", 20, 1, 10000),
        "minimum_interval_seconds": _int("minimum_interval_seconds", 0, 0, 86400),
        "max_alerts_per_batch": _int("max_alerts_per_batch", 100, 1, 5000),
        "max_payload_bytes": _int("max_payload_bytes", DEFAULT_PAYLOAD_BYTES, 1024,
                                  ABSOLUTE_MAX_PAYLOAD_BYTES),
    }


def _normalize_content_policy(cp, where="content_policy"):
    if cp is None:
        cp = {}
    if not isinstance(cp, dict):
        raise NotificationError("ROUTE_INVALID", f"{where}: must be an object")
    unknown = set(cp) - _CONTENT_KEYS
    if unknown:
        raise NotificationError("ROUTE_UNKNOWN_FIELD", f"{where}: {sorted(unknown)}")
    fields = cp.get("fields")
    if fields is None:
        chosen = list(_DEFAULT_ALERT_FIELDS)
    else:
        if not isinstance(fields, list) or any(not isinstance(x, str) for x in fields):
            raise NotificationError("CONTENT_POLICY_INVALID", f"{where}.fields: list of strings")
        for f in fields:
            if f not in _ALERT_FIELD_ALLOWLIST:
                raise NotificationError("CONTENT_FIELD_NOT_ALLOWED", f"{where}.fields: {f!r}")
        # keep a stable, allowlist-ordered field set; alert_id + severity always retained
        chosen = [f for f in _ALERT_FIELD_ALLOWLIST if f in set(fields) | {"alert_id",
                                                                           "owner_severity"}]
    max_alerts = cp.get("max_alerts", 100)
    if isinstance(max_alerts, bool) or not isinstance(max_alerts, int) or not (1 <= max_alerts <= 5000):
        raise NotificationError("CONTENT_POLICY_INVALID", f"{where}.max_alerts: int in [1, 5000]")
    return {"version": CONTENT_POLICY_VERSION, "fields": chosen, "max_alerts": max_alerts,
            "truncate": bool(cp.get("truncate", True))}


# ================================================================ validation: route
def validate_route(defn):
    """Validate + canonicalize a notification-route definition. Returns (canonical_route, route_id).

    Rejects unknown fields, secret/header/cookie/command/template fields, literal URLs/tokens, unknown
    channels/payload formats/auth modes, invalid timezones and invalid policies. Reordered JSON keys
    produce the SAME route_id (canonical form); the id never depends on a timestamp, mtime, temporary
    path, pid, random uuid, mutable approval state or delivery history."""
    if not isinstance(defn, dict):
        raise NotificationError("ROUTE_INVALID", "route must be a JSON object")
    unknown = set(defn) - _ROUTE_TOP_KEYS
    if unknown:
        # an unknown field is rejected; a forbidden-shaped unknown field (a secret/header/cookie/
        # command/template injection attempt) gets the sharper FORBIDDEN_FIELD code. Known route
        # fields are strictly allowlisted at every nesting level below, so this scan runs ONLY over
        # the unknown keys (it never false-flags a legitimate field such as ``description`` or
        # ``authentication.secret_env``).
        bad_key = _reject_forbidden_keys({k: defn[k] for k in unknown})
        if bad_key:
            raise NotificationError("FORBIDDEN_FIELD", f"route: {bad_key}")
        raise NotificationError("ROUTE_UNKNOWN_FIELD", f"unknown field(s): {sorted(unknown)}")
    bad_val = _scan_route_values(defn)
    if bad_val:
        raise NotificationError("FORBIDDEN_VALUE", f"route: {bad_val}")

    name = defn.get("name")
    if not isinstance(name, str) or not name.strip():
        raise NotificationError("ROUTE_INVALID", "route requires a non-empty name")
    channel = defn.get("channel")
    if channel not in _CHANNELS:
        raise NotificationError("CHANNEL_INVALID", f"channel must be one of {sorted(_CHANNELS)}")
    payload_format = defn.get("payload_format", PF_GENERIC)
    if payload_format not in _PAYLOAD_FORMATS:
        raise NotificationError("PAYLOAD_FORMAT_INVALID",
                                f"payload_format must be one of {sorted(_PAYLOAD_FORMATS)}")
    destination_label = str(defn.get("destination_label", "")).strip()

    endpoint_env = defn.get("endpoint_env")
    allowlist = _str_list(defn.get("endpoint_host_allowlist"), "endpoint_host_allowlist", lower=True)
    auth = _normalize_authentication(defn.get("authentication"), channel)
    if channel == CH_HTTPS_WEBHOOK:
        endpoint_env = _env_name(endpoint_env or DEFAULT_URL_ENV, "endpoint_env")
        if not allowlist:
            raise NotificationError("ENDPOINT_ALLOWLIST_REQUIRED",
                                    "an https-json-webhook route requires endpoint_host_allowlist")
        for h in allowlist:
            _reject_amazon_allowlist_host(h)
    else:
        endpoint_env = None
        allowlist = []

    flt = _normalize_filter(defn.get("filter"))
    digest_policy = _normalize_digest_policy(defn.get("digest_policy"))
    quiet_hours = _normalize_quiet_hours(defn.get("quiet_hours"))
    retry_policy = _normalize_retry_policy(defn.get("retry_policy"))
    rate_limit = _normalize_rate_limit(defn.get("rate_limit"))
    content_policy = _normalize_content_policy(defn.get("content_policy"))
    tags = _str_list(defn.get("tags"), "tags")
    auto_send_approved = bool(defn.get("auto_send_approved", False))

    identity = {
        "channel": channel, "payload_format": payload_format, "destination_label": destination_label,
        "endpoint_env": endpoint_env, "endpoint_host_allowlist": allowlist, "authentication": auth,
        "filter": flt, "digest_policy": digest_policy, "quiet_hours": quiet_hours,
        "retry_policy": retry_policy, "rate_limit": rate_limit, "content_policy": content_policy,
        "auto_send_approved": auto_send_approved,
    }
    route_id = _cid("route", identity)
    canonical = {
        "schema_version": ROUTE_SCHEMA,
        "route_id": route_id,
        "name": name.strip(),
        "description": str(defn.get("description", "")),
        "enabled": bool(defn.get("enabled", True)),
        "channel": channel,
        "payload_format": payload_format,
        "destination_label": destination_label,
        "endpoint_env": endpoint_env,
        "endpoint_host_allowlist": allowlist,
        "authentication": auth,
        "filter": flt,
        "digest_policy": digest_policy,
        "quiet_hours": quiet_hours,
        "retry_policy": retry_policy,
        "rate_limit": rate_limit,
        "content_policy": content_policy,
        "tags": tags,
        "owner_notes": str(defn.get("owner_notes", "")),
        "auto_send_approved": auto_send_approved,
    }
    return canonical, route_id


_ROUTE_IDENTITY_KEYS = ("channel", "payload_format", "destination_label", "endpoint_env",
                        "endpoint_host_allowlist", "authentication", "filter", "digest_policy",
                        "quiet_hours", "retry_policy", "rate_limit", "content_policy",
                        "auto_send_approved")


def _reject_amazon_allowlist_host(host):
    """A route may never allowlist an Amazon-account / marketplace host. Reuses the ONE network
    authority's classification (assembles nothing itself)."""
    probe = "https://" + host + "/"
    dclass = NP.classify_destination(probe)
    if dclass in (NP.AMAZON_SELLER_CENTRAL, NP.AMAZON_API, NP.AMAZON_AUTHENTICATED,
                  NP.AMAZON_PRODUCT_OR_SEARCH) or NP._is_amazon_host(host):
        raise NotificationError("ENDPOINT_ALLOWLIST_REJECTED",
                                f"endpoint_host_allowlist: {host} is an Amazon destination",
                                readiness=SELLER_CENTRAL_POLICY_BLOCKED)


def _recompute_route_id(route):
    identity = {k: route[k] for k in _ROUTE_IDENTITY_KEYS}
    return _cid("route", identity)


def _route_content_hash(route):
    """The exact route content hash the approval binds to (excludes the derived integrity hash)."""
    return R.content_sha256({k: v for k, v in route.items() if k != "integrity_hash"})


# ================================================================ route persistence
def _route_path(cfg, route_id):
    return os.path.join(cfg.routes_dir, route_id + ".json")


def load_route(cfg, route_id):
    path = _route_path(cfg, route_id)
    if not os.path.isfile(path):
        raise NotificationError("ROUTE_NOT_FOUND", route_id, readiness=ROUTE_REQUIRED)
    rec = _load_json(path, "route")
    if not isinstance(rec, dict) or "channel" not in rec:
        raise NotificationError("ROUTE_INVALID", route_id, readiness=INTEGRITY_BLOCKED)
    try:
        recomputed = _recompute_route_id(rec)
    except (KeyError, TypeError):
        raise NotificationError("ROUTE_INVALID", route_id, readiness=INTEGRITY_BLOCKED)
    if recomputed != rec.get("route_id"):
        raise NotificationError("ROUTE_IDENTITY_MISMATCH", route_id, readiness=INTEGRITY_BLOCKED)
    return rec


def create_route(cfg, defn):
    canonical, route_id = validate_route(defn)
    path = _route_path(cfg, route_id)
    if os.path.isfile(path):
        existing = _load_json(path, "route")
        same = canonical_json({k: v for k, v in existing.items() if k != "schema_version"}) == \
            canonical_json({k: v for k, v in canonical.items() if k != "schema_version"})
        return {"command": "create-route", "readiness": ROUTE_READY if same else ROUTE_BLOCKED,
                "ok": same, "route_id": route_id,
                "detail": "IDEMPOTENT_REUSE" if same else "DUPLICATE_ROUTE_ID_DIFFERENT_CONTENT",
                "amazon_counters": dict(R.AMAZON_COUNTERS)}
    _write_json(path, canonical)
    _log(cfg, "create-route", {"route_id": route_id, "channel": canonical["channel"]})
    return {"command": "create-route", "readiness": ROUTE_READY, "ok": True, "route_id": route_id,
            "name": canonical["name"], "channel": canonical["channel"],
            "payload_format": canonical["payload_format"],
            "route_content_hash": _route_content_hash(canonical),
            "amazon_counters": dict(R.AMAZON_COUNTERS)}


def list_routes(cfg):
    out = []
    root = cfg.routes_dir
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if not name.endswith(".json"):
                continue
            try:
                rec = _load_json(os.path.join(root, name), "route")
            except (NotificationError, ValueError, OSError):
                continue
            out.append({"route_id": rec.get("route_id"), "name": rec.get("name"),
                        "enabled": rec.get("enabled"), "channel": rec.get("channel"),
                        "payload_format": rec.get("payload_format"),
                        "digest_mode": rec.get("digest_policy", {}).get("mode"),
                        "approval_status": _current_approval_status(cfg, rec.get("route_id"))})
    return {"command": "list-routes", "readiness": LIST_READY, "ok": True, "count": len(out),
            "routes": out, "amazon_counters": dict(R.AMAZON_COUNTERS)}


def show_route(cfg, route_id):
    route = load_route(cfg, route_id)
    return {"command": "show-route", "readiness": SHOW_READY, "ok": True, "route": route,
            "route_content_hash": _route_content_hash(route),
            "amazon_counters": dict(R.AMAZON_COUNTERS)}


def validate_route_cmd(cfg, *, definition_path=None, route_id=None):
    if definition_path is not None:
        canonical, rid = validate_route(_read_definition(definition_path))
    elif route_id is not None:
        canonical = load_route(cfg, route_id)
        rid = canonical["route_id"]
    else:
        raise NotificationError("VALIDATE_INPUT_REQUIRED", "provide --definition or --route-id",
                                readiness=ROUTE_REQUIRED)
    return {"command": "validate-route", "readiness": VALIDATE_READY, "ok": True, "route_id": rid,
            "name": canonical["name"], "channel": canonical["channel"],
            "payload_format": canonical["payload_format"],
            "route_content_hash": _route_content_hash(canonical),
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


# ================================================================ approval: hash-chained audit trail
def _approval_path(cfg, route_id):
    return os.path.join(cfg.approvals_dir, route_id + ".json")


def _approval_event_hash(seq, route_id, route_hash, actor, note, mode, status, allow_auto,
                         auto_token_hash, prev):
    return _sha({"seq": seq, "route_id": route_id, "route_content_hash": route_hash, "actor": actor,
                 "note": note, "delivery_mode": mode, "status": status,
                 "allow_auto_send": bool(allow_auto), "automation_token_hash": auto_token_hash,
                 "prev_state_hash": prev})


def _verify_approval_chain(state):
    prev = ""
    for i, ev in enumerate(state.get("history", [])):
        if ev.get("seq") != i:
            raise NotificationError("APPROVAL_HISTORY_REORDERED", f"seq {ev.get('seq')} at {i}",
                                    readiness=APPROVAL_BLOCKED)
        if ev.get("prev_state_hash") != prev:
            raise NotificationError("APPROVAL_HISTORY_BROKEN", f"prev mismatch at {i}",
                                    readiness=APPROVAL_BLOCKED)
        recomputed = _approval_event_hash(i, ev.get("route_id"), ev.get("route_content_hash"),
                                          ev.get("actor"), ev.get("note"), ev.get("delivery_mode"),
                                          ev.get("status"), ev.get("allow_auto_send"),
                                          ev.get("automation_token_hash"), prev)
        if recomputed != ev.get("event_hash"):
            raise NotificationError("APPROVAL_HISTORY_TAMPERED", f"event hash mismatch at {i}",
                                    readiness=APPROVAL_BLOCKED)
        prev = recomputed
    if state.get("head_hash", "") != prev:
        raise NotificationError("APPROVAL_HISTORY_HEAD_MISMATCH", "aggregate head mismatch",
                                readiness=APPROVAL_BLOCKED)
    return True


def _load_approval_state(cfg, route_id):
    path = _approval_path(cfg, route_id)
    if not os.path.isfile(path):
        return {"schema_version": APPROVAL_STATE_SCHEMA, "route_id": route_id, "status": None,
                "current_route_content_hash": None, "allow_auto_send": False,
                "automation_token_hash": None, "history": [], "head_hash": ""}
    state = _load_hashed_json(path, "approval_state", readiness=APPROVAL_BLOCKED)
    _verify_approval_chain(state)
    return state


def _save_approval_state(cfg, state):
    _write_hashed_json(_approval_path(cfg, state["route_id"]), state)


def _automation_token(route_id, route_hash):
    """A deterministic, NON-SECRET automation token bound to the exact route content hash. It carries
    no secret value; possession alone never sends — the live gate and confirmation still apply."""
    return "AUTOSEND:" + route_id + ":" + route_hash[:16]


def _current_approval_status(cfg, route_id):
    if not route_id:
        return None
    try:
        state = _load_approval_state(cfg, route_id)
    except NotificationError:
        return "CORRUPT"
    return state.get("status")


def approve_route(cfg, route_id, actor, note, *, delivery_mode=MODE_LIVE, allow_auto_send=False):
    route = load_route(cfg, route_id)
    if not isinstance(actor, str) or not actor.strip():
        raise NotificationError("ACTOR_REQUIRED", "an explicit owner actor label is required",
                                readiness=APPROVAL_BLOCKED)
    if delivery_mode not in _DELIVERY_MODES:
        raise NotificationError("APPROVAL_MODE_INVALID", str(delivery_mode),
                                readiness=APPROVAL_BLOCKED)
    route_hash = _route_content_hash(route)
    state = _load_approval_state(cfg, route_id)
    note = "" if note is None else str(note)
    auto_token_hash = None
    if allow_auto_send:
        auto_token_hash = _sha(_automation_token(route_id, route_hash))
    seq = len(state["history"])
    prev = state.get("head_hash", "")
    eh = _approval_event_hash(seq, route_id, route_hash, actor.strip(), note, delivery_mode,
                              APR_APPROVED, allow_auto_send, auto_token_hash, prev)
    approval_id = _cid("apr", {"route_id": route_id, "route_content_hash": route_hash, "seq": seq,
                               "actor": actor.strip(), "status": APR_APPROVED})
    state["history"].append({
        "seq": seq, "approval_id": approval_id, "route_id": route_id,
        "route_content_hash": route_hash, "actor": actor.strip(), "note": note,
        "delivery_mode": delivery_mode, "status": APR_APPROVED,
        "allow_auto_send": bool(allow_auto_send), "automation_token_hash": auto_token_hash,
        "prev_state_hash": prev, "event_hash": eh, "recorded_at": _iso_utc()})
    state.update({"status": APR_APPROVED, "current_route_content_hash": route_hash,
                  "allow_auto_send": bool(allow_auto_send), "automation_token_hash": auto_token_hash,
                  "head_hash": eh})
    _save_approval_state(cfg, state)
    _log(cfg, "approve-route", {"route_id": route_id, "actor": actor.strip()})
    return {"command": "approve-route", "readiness": APPROVAL_READY, "ok": True, "route_id": route_id,
            "approval_id": approval_id, "status": APR_APPROVED, "delivery_mode": delivery_mode,
            "route_content_hash": route_hash, "aggregate_hash": eh,
            "allow_auto_send": bool(allow_auto_send), "history_length": len(state["history"]),
            "amazon_counters": dict(R.AMAZON_COUNTERS)}


def revoke_route(cfg, route_id, actor, note):
    route = load_route(cfg, route_id)
    if not isinstance(actor, str) or not actor.strip():
        raise NotificationError("ACTOR_REQUIRED", "an explicit owner actor label is required",
                                readiness=APPROVAL_BLOCKED)
    state = _load_approval_state(cfg, route_id)
    route_hash = _route_content_hash(route)
    note = "" if note is None else str(note)
    seq = len(state["history"])
    prev = state.get("head_hash", "")
    eh = _approval_event_hash(seq, route_id, route_hash, actor.strip(), note, MODE_LIVE,
                              APR_REVOKED, False, None, prev)
    approval_id = _cid("apr", {"route_id": route_id, "route_content_hash": route_hash, "seq": seq,
                               "actor": actor.strip(), "status": APR_REVOKED})
    state["history"].append({
        "seq": seq, "approval_id": approval_id, "route_id": route_id,
        "route_content_hash": route_hash, "actor": actor.strip(), "note": note,
        "delivery_mode": MODE_LIVE, "status": APR_REVOKED, "allow_auto_send": False,
        "automation_token_hash": None, "prev_state_hash": prev, "event_hash": eh,
        "recorded_at": _iso_utc()})
    state.update({"status": APR_REVOKED, "current_route_content_hash": route_hash,
                  "allow_auto_send": False, "automation_token_hash": None, "head_hash": eh})
    _save_approval_state(cfg, state)
    _log(cfg, "revoke-route", {"route_id": route_id, "actor": actor.strip()})
    return {"command": "revoke-route", "readiness": APPROVAL_READY, "ok": True, "route_id": route_id,
            "approval_id": approval_id, "status": APR_REVOKED, "aggregate_hash": eh,
            "history_length": len(state["history"]), "amazon_counters": dict(R.AMAZON_COUNTERS)}


def show_approval(cfg, route_id):
    load_route(cfg, route_id)
    state = _load_approval_state(cfg, route_id)
    return {"command": "show-approval", "readiness": SHOW_READY, "ok": True, "route_id": route_id,
            "status": state.get("status"),
            "current_route_content_hash": state.get("current_route_content_hash"),
            "allow_auto_send": state.get("allow_auto_send", False),
            "aggregate_hash": state.get("head_hash", ""), "history": state.get("history", []),
            "amazon_counters": dict(R.AMAZON_COUNTERS)}


def list_approvals(cfg):
    out = []
    root = cfg.approvals_dir
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if not name.endswith(".json"):
                continue
            route_id = name[:-5]
            try:
                state = _load_approval_state(cfg, route_id)
                status = state.get("status")
            except NotificationError as e:
                status = "CORRUPT:" + e.code
            out.append({"route_id": route_id, "status": status})
    return {"command": "list-approvals", "readiness": LIST_READY, "ok": True, "count": len(out),
            "approvals": out, "amazon_counters": dict(R.AMAZON_COUNTERS)}


def _approval_gate(cfg, route):
    """Return (approved_bool, reason, state). Live delivery requires an APPROVED current status whose
    bound route content hash equals the current route file's content hash."""
    route_id = route["route_id"]
    state = _load_approval_state(cfg, route_id)   # raises on a corrupt chain -> APPROVAL_BLOCKED
    if state.get("status") != APR_APPROVED:
        return False, "APPROVAL_REQUIRED", state
    if state.get("current_route_content_hash") != _route_content_hash(route):
        return False, "APPROVAL_STALE_ROUTE_CHANGED", state
    return True, "APPROVED", state


# ================================================================ alert source selection (from 7.11)
def _severity_of(alert):
    return alert.get("severity")


def _select_alerts(cfg, route, *, extra_filters=None):
    """Read verified Phase 7.11 alerts (the ONE alert authority) and return (selected, blocked_sources,
    counts). Selection is by verified alert identity + state, never filesystem mtime. Any corrupt
    watchlist/alert-state/history blocks that source (ALERT_SOURCE_BLOCKED)."""
    wcfg = cfg.watchlist_config()
    flt = dict(route.get("filter", {}))
    if extra_filters:
        flt.update(extra_filters)
    listing = W.list_watchlists(wcfg)
    selected, blocked = [], []
    for meta in listing["watchlists"]:
        wl_id = meta["watchlist_id"]
        wl_filter = flt.get("watchlist_ids") or []
        if wl_filter and wl_id not in wl_filter:
            continue
        try:
            wl = W.load_watchlist(wcfg, wl_id)
            vs = W.verify_state(wcfg, wl_id)
            if not vs.get("ok"):
                blocked.append({"watchlist_id": wl_id, "reason": vs.get("readiness")})
                continue
            W._load_alert_state(wcfg, wl_id)       # raises on a corrupt/tampered chain
            rows = W.list_alerts(wcfg, wl_id)["alerts"]
        except W.WatchlistError as e:
            blocked.append({"watchlist_id": wl_id, "reason": e.code})
            continue
        source_index = {s["item_id"]: s for s in wl.get("sources", [])}
        for alert in rows:
            enriched = _enrich_alert(alert, wl, source_index)
            if _alert_passes_filter(enriched, flt):
                selected.append(enriched)
    selected.sort(key=lambda a: a["alert_id"])
    counts = {}
    for a in selected:
        counts[a["severity"]] = counts.get(a["severity"], 0) + 1
    return selected, blocked, counts


def _enrich_alert(alert, wl, source_index):
    src = source_index.get(alert.get("item_id"), {})
    desc = src.get("descriptor", {}) if isinstance(src, dict) else {}
    lineage = alert.get("evidence_lineage", {}) or {}
    return {
        "alert_id": alert.get("alert_id"),
        "watchlist_id": alert.get("watchlist_id"),
        "watchlist_label": wl.get("name"),
        "item_id": alert.get("item_id"),
        "rule_id": alert.get("rule_id"),
        "change_id": alert.get("change_id"),
        "severity": alert.get("severity"),
        "owner_label": alert.get("label"),
        "owner_message": alert.get("message"),
        "status": alert.get("status"),
        "field_path": alert.get("field_path"),
        "evidence_type": alert.get("evidence_type"),
        "change_type": alert.get("change_type"),
        "previous_value": alert.get("previous_value"),
        "current_value": alert.get("current_value"),
        "observed_unit": alert.get("observed_unit"),
        "observed_currency": alert.get("observed_currency"),
        "source_label": desc.get("label"),
        "source_type": desc.get("source_type"),
        "capture_id": lineage.get("current_capture_id"),
    }


def _alert_passes_filter(alert, flt):
    status = alert.get("status")
    statuses = flt.get("statuses") or []
    if statuses:
        # an explicit status filter is authoritative — only those statuses are eligible
        if status not in statuses:
            return False
    else:
        # default eligibility is OPEN only; ACKNOWLEDGED / DISMISSED are excluded unless the route
        # explicitly opts them in (they are never sent by default)
        if status == W.ALERT_OPEN:
            pass
        elif status == W.ALERT_ACKNOWLEDGED and flt.get("include_acknowledged"):
            pass
        elif status == W.ALERT_DISMISSED and flt.get("include_dismissed"):
            pass
        else:
            return False
    if flt.get("severities") and alert.get("severity") not in flt["severities"]:
        return False
    if flt.get("rule_ids") and alert.get("rule_id") not in flt["rule_ids"]:
        return False
    if flt.get("source_types") and (alert.get("source_type") or "") not in flt["source_types"]:
        return False
    if flt.get("field_paths") and (alert.get("field_path") or "") not in flt["field_paths"]:
        return False
    if flt.get("owner_labels") and (alert.get("owner_label") or "") not in flt["owner_labels"]:
        return False
    return True


# ================================================================ digest windows + quiet hours
def _parse_reference(cfg, reference_time):
    if reference_time is None:
        return _dt.datetime.now(_dt.timezone.utc)
    s = str(reference_time).strip().replace("Z", "+00:00")
    try:
        dt = _dt.datetime.fromisoformat(s)
    except ValueError:
        raise NotificationError("REFERENCE_TIME_INVALID", f"not ISO-8601: {reference_time!r}",
                                readiness=ROUTE_BLOCKED)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)


def _digest_period_identity(digest_policy, reference_dt):
    """A deterministic digest-window label. immediate/manual collapse to a constant so the same alert
    set is one idempotent batch; hourly/daily/weekly bucket the reference into an explicit local
    window. The label is the identity input — a raw runtime timestamp never enters a batch id."""
    mode = digest_policy["mode"]
    if mode in (DIGEST_IMMEDIATE, DIGEST_MANUAL):
        return mode
    tz = _validate_timezone(digest_policy["timezone"], "digest_policy.timezone")
    local = reference_dt.astimezone(tz)
    if mode == DIGEST_HOURLY:
        return "hourly:" + local.strftime("%Y-%m-%dT%H")
    if mode == DIGEST_DAILY:
        return "daily:" + local.strftime("%Y-%m-%d")
    if mode == DIGEST_WEEKLY:
        iso = local.isocalendar()
        return "weekly:%04d-W%02d" % (iso[0], iso[1])
    raise NotificationError("DIGEST_MODE_INVALID", mode)   # pragma: no cover


def _digest_due(cfg, route, reference_dt):
    """For an automatic digest, due iff the current window differs from the last delivered window.
    immediate/manual are always due when explicitly invoked. Returns (due, reason, period_id)."""
    dp = route["digest_policy"]
    period_id = _digest_period_identity(dp, reference_dt)
    if dp["mode"] in (DIGEST_IMMEDIATE, DIGEST_MANUAL):
        return True, "IMMEDIATE_OR_MANUAL", period_id
    last = _load_route_send_index(cfg, route["route_id"]).get("last_period_id")
    if last == period_id:
        return False, "PERIOD_ALREADY_DELIVERED", period_id
    return True, "PERIOD_DUE", period_id


def _in_quiet_hours(quiet, reference_dt):
    """True when *reference_dt* falls inside the owner-configured quiet window (tz-aware; handles
    same-day, overnight, DST via zoneinfo). Never computes urgency; only the explicit config."""
    if not quiet.get("enabled"):
        return False
    tz = _validate_timezone(quiet["timezone"], "quiet_hours.timezone")
    local = reference_dt.astimezone(tz)
    if local.weekday() not in set(quiet.get("weekdays", range(7))):
        return False
    minutes = local.hour * 60 + local.minute
    sh, sm = (int(x) for x in quiet["start"].split(":"))
    eh, em = (int(x) for x in quiet["end"].split(":"))
    start, end = sh * 60 + sm, eh * 60 + em
    if start == end:
        return False   # empty window
    if start < end:
        return start <= minutes < end
    return minutes >= start or minutes < end     # overnight window wraps midnight


def _quiet_bypassed(quiet, severities):
    bypass = set(quiet.get("severity_bypass", []))
    return bool(bypass & set(severities))


# ================================================================ payload builders (fixed templates)
def _esc(value):
    """Escape a user-controlled string for a fixed template: neutralize HTML/script, strip control
    chars, bound the length. Never executes anything; there is no template engine."""
    if value is None:
        return None
    s = str(value)
    s = s.replace("\x00", "").replace("\r", " ").replace("\n", " ")
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if len(s) > _TRUNCATE_VALUE_CHARS:
        s = s[:_TRUNCATE_VALUE_CHARS] + "…"
    return s


def _alert_projection(alert, fields):
    """Project an alert to the route's content-policy fields, escaping every user-controlled value.
    Never includes raw HTML, headers, endpoint, filesystem path, private notes or secrets."""
    src = {
        "alert_id": alert.get("alert_id"),
        "watchlist_label": _esc(alert.get("watchlist_label")),
        "owner_severity": alert.get("severity"),
        "owner_label": _esc(alert.get("owner_label")),
        "owner_message": _esc(alert.get("owner_message")),
        "rule_id": alert.get("rule_id"),
        "field_path": _esc(alert.get("field_path")),
        "previous_value": _esc(alert.get("previous_value")),
        "current_value": _esc(alert.get("current_value")),
        "source_label": _esc(alert.get("source_label")),
        "source_type": alert.get("source_type"),
        "capture_id": alert.get("capture_id"),
        "change_id": alert.get("change_id"),
    }
    return {f: src.get(f) for f in fields}


def _severity_counts(alerts):
    counts = {}
    for a in alerts:
        counts[a["severity"]] = counts.get(a["severity"], 0) + 1
    return counts


def _summary_line(alert):
    sev = alert.get("severity")
    label = _esc(alert.get("owner_label")) or "(no label)"
    msg = _esc(alert.get("owner_message")) or ""
    fp = _esc(alert.get("field_path")) or ""
    return f"[{sev}] {label} — {msg} ({fp})".strip()


def build_payload(route, batch, projected_alerts, *, omitted):
    """Build the fixed-schema JSON payload for the route's provider format. No AI, no LLM, no invented
    recommendation/demand/sales — fixed built-in templates only, with every value escaped."""
    fmt = route["payload_format"]
    counts = {"by_severity": batch["severity_counts"], "total": batch["alert_count"],
              "omitted": omitted}
    common = {
        "schema_version": PAYLOAD_SCHEMA,
        "event_type": "phase7.12.owner_notification",
        "template_version": TEMPLATE_VERSION,
        "batch_id": batch["batch_id"],
        "route_id": route["route_id"],
        "readiness": batch["readiness"],
        "digest_mode": route["digest_policy"]["mode"],
        "digest_period": batch["digest_period_id"],
        "generated_from_alert_ids": batch["alert_ids"],
        "alert_count": batch["alert_count"],
        "severity_counts": batch["severity_counts"],
        "omitted_alert_count": omitted,
        "amazon_counters": dict(R.AMAZON_COUNTERS),
    }
    title = (f"Owner alerts: {batch['alert_count']} change(s) — "
             + ", ".join(f"{k}:{v}" for k, v in sorted(batch["severity_counts"].items())))
    lines = [_summary_line(a) for a in projected_alerts]
    if omitted:
        lines.append(f"…and {omitted} more omitted by content policy")
    body_text = _esc(title) + ("\n" + "\n".join(lines) if lines else "")

    if fmt == PF_GENERIC:
        return {**common, "alerts": projected_alerts}
    if fmt == PF_SLACK:
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": _esc(l)}} for l in lines]
        return {"text": _esc(title), "blocks": [{"type": "header",
                "text": {"type": "plain_text", "text": _esc(title)[:150]}}] + blocks[:48],
                "phase7_12": common}
    if fmt == PF_DISCORD:
        return {"content": body_text[:1900],
                "embeds": [{"title": _esc(title)[:256],
                            "description": ("\n".join(lines))[:4000] if lines else "(no changes)"}],
                "phase7_12": common}
    if fmt == PF_TEAMS:
        return {"@type": "MessageCard", "@context": "http://schema.org/extensions",
                "summary": _esc(title)[:256], "title": _esc(title)[:256],
                "text": body_text[:16000], "phase7_12": common}
    raise NotificationError("PAYLOAD_FORMAT_INVALID", fmt)   # pragma: no cover


# ================================================================ batch build + identity
def _batch_identity(route, digest_period_id, alert_ids):
    return _cid("batch", {
        "route_content_hash": _route_content_hash(route),
        "route_identity": {k: route[k] for k in _ROUTE_IDENTITY_KEYS},
        "digest_period_id": digest_period_id,
        "alert_ids": sorted(alert_ids),
        "template_version": TEMPLATE_VERSION,
        "payload_format": route["payload_format"],
        "content_policy_version": route["content_policy"]["version"],
    })


def _apply_content_policy(route, alerts):
    """Deterministic truncation to the content policy's max_alerts / route max_alerts_per_batch.
    Returns (kept_projection, kept_alerts, omitted_count). Nothing is silently discarded — the omitted
    count is recorded honestly."""
    cp = route["content_policy"]
    cap = min(cp["max_alerts"], route["rate_limit"]["max_alerts_per_batch"])
    ordered = sorted(alerts, key=lambda a: (-_SEV_ORDER.get(a["severity"], 0), a["alert_id"]))
    kept = ordered[:cap]
    omitted = len(ordered) - len(kept)
    projection = [_alert_projection(a, cp["fields"]) for a in
                  sorted(kept, key=lambda a: a["alert_id"])]
    return projection, sorted(kept, key=lambda a: a["alert_id"]), omitted


def build_batch(cfg, route_id, *, reference_time=None, extra_filters=None, persist=True):
    route = load_route(cfg, route_id)
    reference_dt = _parse_reference(cfg, reference_time)
    selected, blocked, _counts = _select_alerts(cfg, route, extra_filters=extra_filters)
    if blocked:
        return {"command": "build-batch", "readiness": ALERT_SOURCE_BLOCKED, "ok": False,
                "route_id": route_id, "blocked_sources": blocked,
                "amazon_counters": dict(R.AMAZON_COUNTERS)}
    period_id = _digest_period_identity(route["digest_policy"], reference_dt)
    projection, kept, omitted = _apply_content_policy(route, selected)
    alert_ids = [a["alert_id"] for a in kept]
    batch_id = _batch_identity(route, period_id, alert_ids)
    readiness = BATCH_READY if kept else BATCH_READY_EMPTY
    batch = {
        "schema_version": BATCH_SCHEMA,
        "batch_id": batch_id,
        "route_id": route_id,
        "route_content_hash": _route_content_hash(route),
        "payload_format": route["payload_format"],
        "channel": route["channel"],
        "digest_mode": route["digest_policy"]["mode"],
        "digest_period_id": period_id,
        "template_version": TEMPLATE_VERSION,
        "content_policy_version": route["content_policy"]["version"],
        "readiness": readiness,
        "alert_ids": alert_ids,
        "alert_count": len(kept),
        "watchlist_ids": sorted({a["watchlist_id"] for a in kept}),
        "change_ids": sorted({a["change_id"] for a in kept if a.get("change_id")}),
        "source_labels": sorted({a["source_label"] for a in kept if a.get("source_label")}),
        "severities": sorted({a["severity"] for a in kept}),
        "severity_counts": _severity_counts(kept),
        "omitted_alert_count": omitted,
        "truncation_summary": {"selected": len(selected), "kept": len(kept), "omitted": omitted,
                               "max_alerts": route["content_policy"]["max_alerts"],
                               "max_alerts_per_batch": route["rate_limit"]["max_alerts_per_batch"]},
        "alerts": kept,
        "disclaimers": list(DISCLAIMERS),
    }
    batch.update(R.AMAZON_COUNTERS)
    payload = build_payload(route, batch, projection, omitted=omitted)
    if persist:
        _write_json(os.path.join(cfg.batches_dir, batch_id + ".json"), batch)
        _write_json(os.path.join(cfg.batches_dir, batch_id + ".payload.json"), payload)
        _log(cfg, "build-batch", {"route_id": route_id, "batch_id": batch_id,
                                  "alert_count": len(kept)})
    return {"command": "build-batch", "readiness": readiness, "ok": True, "route_id": route_id,
            "batch_id": batch_id, "alert_count": len(kept), "omitted_alert_count": omitted,
            "digest_period_id": period_id, "severity_counts": batch["severity_counts"],
            "batch": batch, "payload": payload, "amazon_counters": dict(R.AMAZON_COUNTERS)}


def load_batch(cfg, batch_id):
    path = os.path.join(cfg.batches_dir, batch_id + ".json")
    if not os.path.isfile(path):
        raise NotificationError("BATCH_NOT_FOUND", batch_id, readiness=BATCH_READY_EMPTY)
    return _load_json(path, "batch")


def load_batch_payload(cfg, batch_id):
    path = os.path.join(cfg.batches_dir, batch_id + ".payload.json")
    if not os.path.isfile(path):
        raise NotificationError("BATCH_PAYLOAD_NOT_FOUND", batch_id, readiness=BATCH_READY_EMPTY)
    return _load_json(path, "payload")


# ================================================================ preview + local outbox
def preview(cfg, route_id, *, reference_time=None, extra_filters=None):
    """Local preview — build the batch + payload without any approval or network. No socket, no DNS."""
    res = build_batch(cfg, route_id, reference_time=reference_time, extra_filters=extra_filters,
                      persist=True)
    if res["readiness"] == ALERT_SOURCE_BLOCKED:
        return res
    return {"command": "preview", "readiness": PREVIEW_READY, "ok": True, "route_id": route_id,
            "batch_id": res["batch_id"], "alert_count": res["alert_count"],
            "omitted_alert_count": res["omitted_alert_count"],
            "severity_counts": res["severity_counts"], "payload": res["payload"],
            "amazon_counters": dict(R.AMAZON_COUNTERS)}


def _outbox_path(cfg, route_id, batch_id):
    return os.path.join(cfg.outbox_dir, route_id, batch_id + ".json")


def write_outbox(cfg, route_id, *, batch_id=None, reference_time=None, extra_filters=None):
    """Write a deterministic local notification preview to the outbox. No network request; works
    without any approval. Idempotent: the same batch never produces a duplicate outbox file."""
    route = load_route(cfg, route_id)
    if batch_id is None:
        res = build_batch(cfg, route_id, reference_time=reference_time, extra_filters=extra_filters)
        if res["readiness"] == ALERT_SOURCE_BLOCKED:
            return res
        batch_id = res["batch_id"]
        payload = res["payload"]
    else:
        payload = load_batch_payload(cfg, batch_id)
    path = _outbox_path(cfg, route_id, batch_id)
    record = {"schema_version": OUTBOX_SCHEMA, "route_id": route_id, "batch_id": batch_id,
              "channel": route["channel"], "payload_format": route["payload_format"],
              "destination_label": route["destination_label"], "payload": payload,
              "payload_sha256": R.content_sha256(payload)}
    record.update(R.AMAZON_COUNTERS)
    duplicate = os.path.isfile(path)
    _write_json(path, record)
    return {"command": "write-outbox", "readiness": PREVIEW_READY, "ok": True, "route_id": route_id,
            "batch_id": batch_id, "outbox_path": _rel(cfg, path),
            "payload_sha256": record["payload_sha256"],
            "idempotent_reuse": duplicate, "detail": "IDEMPOTENT_REUSE" if duplicate else "WRITTEN",
            "amazon_counters": dict(R.AMAZON_COUNTERS)}


# ================================================================ delivery identity + history chain
def _delivery_identity(route, batch, endpoint_host, payload_sha256):
    return _cid("dlv", {
        "batch_id": batch["batch_id"],
        "route_id": route["route_id"],
        "provider_type": route["payload_format"],
        "channel": route["channel"],
        "destination_label": route["destination_label"],
        "approved_endpoint_host": endpoint_host or "",
        "payload_sha256": payload_sha256,
    })


def _delivery_history_path(cfg, delivery_id):
    return os.path.join(cfg.delivery_history_dir, delivery_id + ".json")


def _delivery_event_hash(seq, delivery_id, event_type, state, provider_status, payload_sha256,
                         route_hash, approval_hash, actor, reason, prev):
    return _sha({"seq": seq, "delivery_id": delivery_id, "event_type": event_type,
                 "delivery_state": state, "provider_status": provider_status,
                 "payload_sha256": payload_sha256, "route_hash": route_hash,
                 "approval_hash": approval_hash, "actor": actor, "sanitized_reason": reason,
                 "prev_state_hash": prev})


def _verify_delivery_chain(state):
    prev = ""
    for i, ev in enumerate(state.get("history", [])):
        if ev.get("seq") != i:
            raise NotificationError("DELIVERY_HISTORY_REORDERED", f"seq {ev.get('seq')} at {i}",
                                    readiness=DELIVERY_STATE_BLOCKED)
        if ev.get("previous_event_hash") != prev:
            raise NotificationError("DELIVERY_HISTORY_BROKEN", f"prev mismatch at {i}",
                                    readiness=DELIVERY_STATE_BLOCKED)
        recomputed = _delivery_event_hash(i, ev.get("delivery_id"), ev.get("event_type"),
                                          ev.get("delivery_state"), ev.get("provider_status"),
                                          ev.get("payload_sha256"), ev.get("route_hash"),
                                          ev.get("approval_hash"), ev.get("actor"),
                                          ev.get("sanitized_reason"), prev)
        if recomputed != ev.get("event_hash"):
            raise NotificationError("DELIVERY_HISTORY_TAMPERED", f"event hash mismatch at {i}",
                                    readiness=DELIVERY_STATE_BLOCKED)
        prev = recomputed
    if state.get("head_hash", "") != prev:
        raise NotificationError("DELIVERY_HISTORY_HEAD_MISMATCH", "aggregate head mismatch",
                                readiness=DELIVERY_STATE_BLOCKED)
    return True


def _load_delivery_history(cfg, delivery_id):
    path = _delivery_history_path(cfg, delivery_id)
    if not os.path.isfile(path):
        return {"schema_version": DELIVERY_HISTORY_SCHEMA, "delivery_id": delivery_id,
                "current_state": None, "attempt_sequence": 0, "history": [], "head_hash": ""}
    state = _load_hashed_json(path, "delivery_history", readiness=DELIVERY_STATE_BLOCKED)
    _verify_delivery_chain(state)
    return state


def _append_delivery_event(cfg, hist, *, event_type, state, provider_status=None, payload_sha256,
                           route_hash, approval_hash, actor, reason, attempt_sequence=None):
    if event_type not in _EVENT_TYPES:
        raise NotificationError("DELIVERY_EVENT_INVALID", event_type,
                                readiness=DELIVERY_STATE_BLOCKED)
    seq = len(hist["history"])
    prev = hist.get("head_hash", "")
    eh = _delivery_event_hash(seq, hist["delivery_id"], event_type, state, provider_status,
                              payload_sha256, route_hash, approval_hash, actor, reason, prev)
    hist["history"].append({
        "schema_version": DELIVERY_HISTORY_SCHEMA, "seq": seq, "delivery_id": hist["delivery_id"],
        "attempt_sequence": attempt_sequence if attempt_sequence is not None
        else hist.get("attempt_sequence", 0),
        "previous_event_hash": prev, "event_type": event_type, "delivery_state": state,
        "provider_status": provider_status, "payload_sha256": payload_sha256,
        "route_hash": route_hash, "approval_hash": approval_hash, "actor": actor,
        "sanitized_reason": reason, "event_hash": eh, "recorded_at": _iso_utc()})
    hist["head_hash"] = eh
    hist["current_state"] = state
    if attempt_sequence is not None:
        hist["attempt_sequence"] = attempt_sequence
    _write_hashed_json(_delivery_history_path(cfg, hist["delivery_id"]), hist)
    return hist


# ================================================================ delivery record persistence
def _delivery_path(cfg, delivery_id):
    return os.path.join(cfg.deliveries_dir, delivery_id + ".json")


def _write_delivery_record(cfg, record):
    _write_json(_delivery_path(cfg, record["delivery_id"]), record)


def _load_delivery_record(cfg, delivery_id):
    path = _delivery_path(cfg, delivery_id)
    if not os.path.isfile(path):
        return None
    return _load_json(path, "delivery")


# ================================================================ per-route send index (idempotency + rate)
def _route_send_index_path(cfg, route_id):
    return os.path.join(cfg.deliveries_dir, "index-" + route_id + ".json")


def _load_route_send_index(cfg, route_id):
    path = _route_send_index_path(cfg, route_id)
    if not os.path.isfile(path):
        return {"schema_version": "phase7-12-send-index-v1", "route_id": route_id,
                "sent_alert_ids": [], "sent_deliveries": [], "sent_epochs": [],
                "last_period_id": None}
    return _load_hashed_json(path, "send_index", readiness=INTEGRITY_BLOCKED)


def _save_route_send_index(cfg, idx):
    _write_hashed_json(_route_send_index_path(cfg, idx["route_id"]), idx)


def _record_sent(cfg, route_id, delivery_id, alert_ids, epoch, period_id, destination_host):
    idx = _load_route_send_index(cfg, route_id)
    idx["sent_alert_ids"] = sorted(set(idx["sent_alert_ids"]) | set(alert_ids))
    if delivery_id not in idx["sent_deliveries"]:
        idx["sent_deliveries"].append(delivery_id)
    idx.setdefault("sent_epochs", []).append({"delivery_id": delivery_id, "epoch": epoch,
                                              "host": destination_host})
    idx["last_period_id"] = period_id
    _save_route_send_index(cfg, idx)


def _rate_check(cfg, route, destination_host, now):
    """Return (ok, reason). Enforces per-route + per-destination hourly caps and a minimum interval
    between sends. A rate-limited batch remains pending; nothing is discarded."""
    rl = route["rate_limit"]
    idx = _load_route_send_index(cfg, route["route_id"])
    epochs = idx.get("sent_epochs", [])
    window = [e for e in epochs if now - float(e.get("epoch", 0)) < 3600]
    if len(window) >= rl["max_per_route_per_hour"]:
        return False, "ROUTE_HOURLY_LIMIT"
    if destination_host:
        dest = [e for e in window if e.get("host") == destination_host]
        if len(dest) >= rl["max_per_destination_per_hour"]:
            return False, "DESTINATION_HOURLY_LIMIT"
    if rl["minimum_interval_seconds"] > 0 and epochs:
        last = max(float(e.get("epoch", 0)) for e in epochs)
        if now - last < rl["minimum_interval_seconds"]:
            return False, "MINIMUM_INTERVAL"
    return True, "OK"


# ================================================================ endpoint resolution + webhook transport
def _endpoint_url(cfg, route):
    env_name = route.get("endpoint_env")
    if not env_name:
        raise NotificationError("ENDPOINT_ENV_MISSING", "route has no endpoint_env",
                                readiness=ROUTE_BLOCKED)
    url = (cfg.env.get(env_name) or "").strip()
    if not url:
        raise NotificationError("ENDPOINT_ENV_UNSET",
                                f"environment variable {env_name} is not set",
                                readiness=DELIVERY_FAILED)
    return url


def _bearer_token(cfg, route):
    env_name = route["authentication"].get("token_env")
    return (cfg.env.get(env_name) or "").strip() if env_name else ""


def _hmac_secret(cfg, route):
    env_name = route["authentication"].get("secret_env")
    return (cfg.env.get(env_name) or "").strip() if env_name else ""


def _build_headers(cfg, route, delivery_id, body_bytes):
    """Build the FIXED header set. No route-provided header names/values are ever possible."""
    headers = {"Content-Type": FIXED_CONTENT_TYPE, "User-Agent": USER_AGENT,
               "Idempotency-Key": delivery_id, "Accept": FIXED_CONTENT_TYPE}
    mode = route["authentication"]["mode"]
    if mode == AUTH_BEARER:
        token = _bearer_token(cfg, route)
        if not token:
            raise NotificationError("AUTH_SECRET_UNSET", "bearer token env is not set",
                                    readiness=DELIVERY_FAILED)
        headers["Authorization"] = "Bearer " + token
    elif mode == AUTH_HMAC:
        secret = _hmac_secret(cfg, route)
        if not secret:
            raise NotificationError("AUTH_SECRET_UNSET", "hmac secret env is not set",
                                    readiness=DELIVERY_FAILED)
        sig = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
        headers[HMAC_SIG_HEADER] = "sha256=" + sig
        headers[HMAC_TSV_HEADER] = "v1"
    return headers


def compute_hmac_signature(secret, body_bytes):
    """Deterministic HMAC-SHA256 hex of *body_bytes* under *secret* (test/verification helper)."""
    return hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never auto-follow a redirect. A 3xx is surfaced to the caller and recorded BLOCKED."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _real_webhook_transport(url, *, headers, body, connect_timeout, read_timeout, max_response_bytes):
    """One bounded, TLS-verified HTTPS POST with NO auto-redirect, NO cookies, NO proxy, NO
    credential persistence. Returns (status, safe_headers, bounded_body_bytes). A 3xx status is
    returned (its Location is never followed). Raises _PostSendTimeout on a read timeout (UNKNOWN),
    _PreSendError on a connection failure before any byte is sent (retryable)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}),
                                         urllib.request.HTTPSHandler(context=ctx))
    req = urllib.request.Request(url, data=body, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with opener.open(req, timeout=float(read_timeout)) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read(max_response_bytes + 1)
            hdrs = {k.lower(): v for k, v in (resp.headers.items() if resp.headers else [])}
            return status, hdrs, raw
    except urllib.error.HTTPError as e:
        hdrs = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
        try:
            body_bytes = e.read(max_response_bytes + 1) if hasattr(e, "read") else b""
        except Exception:
            body_bytes = b""
        return e.code, hdrs, body_bytes
    except (socket.timeout, TimeoutError):
        raise _PostSendTimeout("read timeout after request transmission") from None
    except (urllib.error.URLError, ssl.SSLError, ConnectionError, OSError) as e:
        raise _PreSendError(type(e).__name__) from None


def _safe_headers(headers):
    return {k: headers[k] for k in _SAFE_RESPONSE_HEADERS if k in (headers or {})}


def _response_summary(status, headers, body):
    """Store ONLY status, a small safe-header subset, a bounded sanitized summary + body sha — never
    the full provider response body, never a Set-Cookie / credential."""
    text = ""
    try:
        text = body.decode("utf-8", errors="replace") if body else ""
    except Exception:
        text = ""
    text = D.redact_secrets(text.replace("\x00", ""))
    return {"provider_status": status, "safe_headers": _safe_headers(headers),
            "response_summary": text[:280], "response_sha256": R._sha256_hex(body or b""),
            "response_bytes": len(body or b"")}


# ================================================================ live-delivery gate
def _live_gate(cfg, route, batch, *, confirm_send, auto=False):
    """Validate the full live-delivery gate. Returns (ok, readiness, reason, ctx). Local preview /
    outbox never call this; live HTTPS delivery requires EVERY condition."""
    ctx = {}
    if route["channel"] != CH_HTTPS_WEBHOOK:
        return False, ROUTE_BLOCKED, "CHANNEL_NOT_WEBHOOK", ctx
    if not route.get("enabled", True):
        return False, ROUTE_BLOCKED, "ROUTE_DISABLED", ctx
    approved, reason, appr_state = _approval_gate(cfg, route)
    ctx["approval_state"] = appr_state
    if not approved:
        readiness = APPROVAL_REQUIRED if reason == "APPROVAL_REQUIRED" else APPROVAL_BLOCKED
        return False, readiness, reason, ctx
    if str(cfg.env.get(LIVE_DELIVERY_ENV, "")).strip() != "1":
        return False, DELIVERY_CONFIRMATION_REQUIRED, "LIVE_DELIVERY_ENV_NOT_SET", ctx
    if auto:
        if not route.get("auto_send_approved"):
            return False, APPROVAL_REQUIRED, "AUTO_SEND_NOT_APPROVED_ON_ROUTE", ctx
        if not appr_state.get("allow_auto_send"):
            return False, APPROVAL_REQUIRED, "APPROVAL_DOES_NOT_ALLOW_AUTO_SEND", ctx
        expected = _sha(_automation_token(route["route_id"], _route_content_hash(route)))
        provided = str(cfg.env.get(AUTO_SEND_TOKEN_ENV, "")).strip()
        if not provided or _sha(provided) != expected:
            return False, DELIVERY_CONFIRMATION_REQUIRED, "AUTO_SEND_TOKEN_INVALID", ctx
    else:
        expected_token = "SEND:" + batch["batch_id"]
        if str(confirm_send or "").strip() != expected_token:
            return False, DELIVERY_CONFIRMATION_REQUIRED, "CONFIRMATION_TOKEN_MISMATCH", ctx
    return True, None, "OK", ctx


# ================================================================ send a batch
def send_batch(cfg, batch_id, *, confirm_send=None, reference_time=None, break_stale=False,
               _auto=False, _route=None, _batch=None):
    try:
        batch = _batch or load_batch(cfg, batch_id)
        route = _route or load_route(cfg, batch["route_id"])
        route_hash = _route_content_hash(route)
        if batch.get("route_content_hash") != route_hash:
            return _result("send-batch", ROUTE_BLOCKED, False, batch_id=batch_id,
                           detail="BATCH_ROUTE_HASH_MISMATCH")
        lock = _acquire_lock(cfg, "batch-" + batch_id, break_stale=break_stale)
    except NotificationError as e:
        return _err_result("send-batch", e, batch_id=batch_id)
    rlock = None
    try:
        rlock = _acquire_lock(cfg, "route-" + route["route_id"], break_stale=break_stale)
        ok, readiness, reason, ctx = _live_gate(cfg, route, batch, confirm_send=confirm_send,
                                                auto=_auto)
        approval_hash = (ctx.get("approval_state") or {}).get("head_hash", "")
        if not ok:
            return _result("send-batch", readiness, False, batch_id=batch_id, route_id=route["route_id"],
                           reason=reason)

        endpoint_url = _endpoint_url(cfg, route)
        # endpoint policy — the ONE network authority (Amazon boundary first, then SSRF + allowlist)
        decision = NP.evaluate_notification_delivery_url(
            endpoint_url, allowed_hosts=route["endpoint_host_allowlist"], allow_local=cfg.allow_local)
        if not decision.allowed:
            seller = decision.reason_code in (D.SELLER_CENTRAL_ACCESS_PROHIBITED,
                                              D.AMAZON_API_PROHIBITED,
                                              D.AMAZON_ACCOUNT_ACCESS_PROHIBITED)
            block_readiness = SELLER_CENTRAL_POLICY_BLOCKED if seller else DELIVERY_BLOCKED
            D.record_event(D.NETWORK_REQUEST_DENIED, "notification endpoint denied",
                           {"destination": decision.safe_host, "reason_code": decision.reason_code})
            return _record_terminal(cfg, route, batch, decision.safe_host, block_readiness,
                                    DS_BLOCKED, EV_BLOCKED, decision.reason_code, route_hash,
                                    approval_hash)

        endpoint_host = decision.safe_host
        payload = load_batch_payload(cfg, batch_id)
        body_bytes = (canonical_json(payload) + "\n").encode("utf-8")
        max_payload = route["rate_limit"]["max_payload_bytes"]
        if len(body_bytes) > min(max_payload, ABSOLUTE_MAX_PAYLOAD_BYTES):
            return _record_terminal(cfg, route, batch, endpoint_host, DELIVERY_FAILED, DS_FAILED,
                                    EV_FAILED, "PAYLOAD_TOO_LARGE", route_hash, approval_hash,
                                    payload_sha256=R.content_sha256(payload))

        payload_sha = R.content_sha256(payload)
        delivery_id = _delivery_identity(route, batch, endpoint_host, payload_sha)
        hist = _load_delivery_history(cfg, delivery_id)

        # idempotency — never resend a delivery already committed SENT
        if hist.get("current_state") == DS_SENT:
            return _result("send-batch", DELIVERY_SENT, True, batch_id=batch_id,
                           route_id=route["route_id"], delivery_id=delivery_id,
                           detail="IDEMPOTENT_REUSE", delivery_state=DS_SENT)

        # quiet hours (owner-defined; CRITICAL bypass only when explicitly configured)
        reference_dt = _parse_reference(cfg, reference_time)
        if _in_quiet_hours(route["quiet_hours"], reference_dt) and \
                not _quiet_bypassed(route["quiet_hours"], batch["severities"]):
            _append_delivery_event(cfg, hist, event_type=EV_RATE_LIMITED, state=DS_QUIET_HOURS,
                                   payload_sha256=payload_sha, route_hash=route_hash,
                                   approval_hash=approval_hash, actor="SYSTEM", reason="QUIET_HOURS")
            return _result("send-batch", QUIET_HOURS, True, batch_id=batch_id, delivery_id=delivery_id,
                           route_id=route["route_id"], delivery_state=DS_QUIET_HOURS)

        now = _now_epoch(cfg)
        rate_ok, rate_reason = _rate_check(cfg, route, endpoint_host, now)
        if not rate_ok:
            _append_delivery_event(cfg, hist, event_type=EV_RATE_LIMITED, state=DS_RATE_LIMITED,
                                   payload_sha256=payload_sha, route_hash=route_hash,
                                   approval_hash=approval_hash, actor="SYSTEM", reason=rate_reason)
            return _result("send-batch", RATE_LIMITED, True, batch_id=batch_id, delivery_id=delivery_id,
                           route_id=route["route_id"], delivery_state=DS_RATE_LIMITED,
                           reason=rate_reason)

        attempt = hist.get("attempt_sequence", 0) + 1
        return _attempt_send(cfg, route, batch, hist, endpoint_url, endpoint_host, payload, body_bytes,
                             payload_sha, route_hash, approval_hash, attempt, now,
                             batch["digest_period_id"])
    except NotificationError as e:
        return _err_result("send-batch", e, batch_id=batch_id, route_id=route["route_id"])
    finally:
        if rlock is not None:
            _release_lock(cfg, "route-" + route["route_id"], rlock)
        _release_lock(cfg, "batch-" + batch_id, lock)


def _attempt_send(cfg, route, batch, hist, endpoint_url, endpoint_host, payload, body_bytes,
                  payload_sha, route_hash, approval_hash, attempt, now, period_id):
    delivery_id = hist["delivery_id"]
    headers = _build_headers(cfg, route, delivery_id, body_bytes)
    # DNS resolution + validation of EVERY resolved address BEFORE any socket is opened
    host = endpoint_host
    if not cfg.allow_local or not _is_ip_literal(host):
        try:
            addrs = _resolve(cfg, host)
        except _PreSendError:
            return _record_attempt(cfg, route, batch, hist, PROVIDER_UNAVAILABLE, DS_PROVIDER_UNAVAILABLE,
                                   EV_FAILED, "DNS_RESOLUTION_FAILED", payload_sha, route_hash,
                                   approval_hash, attempt, None, retryable=True)
        bad = NP.validate_resolved_addresses(addrs, allow_local=cfg.allow_local)
        if bad:
            D.record_event(D.NETWORK_REQUEST_DENIED, "resolved address disallowed (SSRF guard)",
                           {"destination": host, "reason_code": D.PRIVATE_DESTINATION_BLOCKED})
            return _record_terminal(cfg, route, batch, host, DELIVERY_BLOCKED, DS_BLOCKED, EV_BLOCKED,
                                    "RESOLVED_PRIVATE_ADDRESS", route_hash, approval_hash,
                                    payload_sha256=payload_sha, hist=hist)

    _append_delivery_event(cfg, hist, event_type=EV_SEND_STARTED, state=DS_QUEUED,
                           payload_sha256=payload_sha, route_hash=route_hash,
                           approval_hash=approval_hash, actor="OWNER", reason="SEND_STARTED",
                           attempt_sequence=attempt)
    transport = cfg.transport or _real_webhook_transport
    try:
        status, resp_headers, resp_body = transport(
            endpoint_url, headers=headers, body=body_bytes, connect_timeout=cfg.connect_timeout,
            read_timeout=cfg.read_timeout, max_response_bytes=RESPONSE_READ_LIMIT)
    except _PostSendTimeout:
        return _record_attempt(cfg, route, batch, hist, DELIVERY_UNKNOWN, DS_UNKNOWN, EV_UNKNOWN,
                               "POST_SEND_TIMEOUT", payload_sha, route_hash, approval_hash, attempt,
                               None, retryable=False)
    except (_PreSendError, _ProviderUnavailable):
        return _record_attempt(cfg, route, batch, hist, PROVIDER_UNAVAILABLE, DS_PROVIDER_UNAVAILABLE,
                               EV_FAILED, "CONNECTION_FAILED_PRE_SEND", payload_sha, route_hash,
                               approval_hash, attempt, None, retryable=True)

    summary = _response_summary(status, resp_headers, resp_body)
    return _classify_response(cfg, route, batch, hist, endpoint_host, status, summary, payload,
                              payload_sha, route_hash, approval_hash, attempt, now, period_id)


def _classify_response(cfg, route, batch, hist, endpoint_host, status, summary, payload, payload_sha,
                       route_hash, approval_hash, attempt, now, period_id):
    delivery_id = hist["delivery_id"]

    def terminal(readiness, dstate, event, reason):
        return _record_terminal(cfg, route, batch, endpoint_host, readiness, dstate, event, reason,
                                route_hash, approval_hash, payload_sha256=payload_sha, hist=hist,
                                provider_status=status, summary=summary)

    if 300 <= status < 400:
        return terminal(DELIVERY_BLOCKED, DS_BLOCKED, EV_BLOCKED, "DELIVERY_BLOCKED_REDIRECT")
    if 200 <= status < 300:
        # 2xx means SENT only AFTER the local delivery record is committed
        record = _delivery_record(route, batch, endpoint_host, delivery_id, DS_SENT, status, summary,
                                  payload_sha, attempt, now)
        _write_delivery_record(cfg, record)
        _append_delivery_event(cfg, hist, event_type=EV_SENT, state=DS_SENT, provider_status=status,
                               payload_sha256=payload_sha, route_hash=route_hash,
                               approval_hash=approval_hash, actor="OWNER", reason="HTTP_2XX",
                               attempt_sequence=attempt)
        _record_sent(cfg, route["route_id"], delivery_id, batch["alert_ids"], now, period_id,
                     endpoint_host)
        _log(cfg, "send-batch", {"route_id": route["route_id"], "batch_id": batch["batch_id"],
                                 "delivery_id": delivery_id, "state": DS_SENT})
        return _result("send-batch", DELIVERY_SENT, True, batch_id=batch["batch_id"],
                       route_id=route["route_id"], delivery_id=delivery_id, delivery_state=DS_SENT,
                       provider_status=status)
    retryable = _is_retryable_status(route, status)
    return _record_attempt(cfg, route, batch, hist, DELIVERY_FAILED, DS_FAILED, EV_FAILED,
                           f"HTTP_{status}", payload_sha, route_hash, approval_hash, attempt,
                           status, retryable=retryable, summary=summary, endpoint_host=endpoint_host)


def _is_retryable_status(route, status):
    if status in _TERMINAL_4XX:
        return False
    return status in set(route["retry_policy"]["retryable_statuses"])


def _delivery_record(route, batch, endpoint_host, delivery_id, state, provider_status, summary,
                     payload_sha, attempt, epoch):
    rec = {
        "schema_version": DELIVERY_SCHEMA,
        "delivery_id": delivery_id,
        "batch_id": batch["batch_id"],
        "route_id": route["route_id"],
        "provider_type": route["payload_format"],
        "channel": route["channel"],
        "destination_label": route["destination_label"],
        "approved_endpoint_host": endpoint_host,
        "payload_sha256": payload_sha,
        "delivery_state": state,
        "provider_status": provider_status,
        "response_summary": summary.get("response_summary") if summary else None,
        "safe_response_headers": summary.get("safe_headers") if summary else {},
        "response_sha256": summary.get("response_sha256") if summary else None,
        "attempt_sequence": attempt,
        "alert_count": batch["alert_count"],
        "idempotency_key": delivery_id,
        "sent_epoch": epoch,          # OPERATIONAL only — never part of delivery identity
    }
    rec.update(R.AMAZON_COUNTERS)
    return rec


def _record_terminal(cfg, route, batch, endpoint_host, readiness, dstate, event, reason, route_hash,
                     approval_hash, *, payload_sha256=None, hist=None, provider_status=None,
                     summary=None):
    if payload_sha256 is None:
        payload_sha256 = batch.get("route_content_hash", "")
    delivery_id = (hist or {}).get("delivery_id") or _delivery_identity(
        route, batch, endpoint_host, payload_sha256)
    if hist is None:
        hist = _load_delivery_history(cfg, delivery_id)
    record = _delivery_record(route, batch, endpoint_host, delivery_id, dstate, provider_status,
                              summary, payload_sha256, hist.get("attempt_sequence", 0), _now_epoch(cfg))
    _write_delivery_record(cfg, record)
    _append_delivery_event(cfg, hist, event_type=event, state=dstate, provider_status=provider_status,
                           payload_sha256=payload_sha256, route_hash=route_hash,
                           approval_hash=approval_hash, actor="OWNER", reason=reason)
    return _result("send-batch", readiness, readiness in _EXIT_OK, batch_id=batch["batch_id"],
                   route_id=route["route_id"], delivery_id=delivery_id, delivery_state=dstate,
                   reason=reason, provider_status=provider_status)


def _record_attempt(cfg, route, batch, hist, readiness, dstate, event, reason, payload_sha,
                    route_hash, approval_hash, attempt, provider_status, *, retryable, summary=None,
                    endpoint_host=None):
    delivery_id = hist["delivery_id"]
    record = _delivery_record(route, batch, endpoint_host or "", delivery_id, dstate, provider_status,
                              summary, payload_sha, attempt, _now_epoch(cfg))
    record["retryable"] = bool(retryable)
    _write_delivery_record(cfg, record)
    _append_delivery_event(cfg, hist, event_type=event, state=dstate, provider_status=provider_status,
                           payload_sha256=payload_sha, route_hash=route_hash,
                           approval_hash=approval_hash, actor="OWNER", reason=reason,
                           attempt_sequence=attempt)
    if retryable and route["retry_policy"]["enabled"] and attempt < route["retry_policy"]["max_attempts"]:
        delay = _retry_delay(route, attempt)
        _append_delivery_event(cfg, hist, event_type=EV_RETRY_SCHEDULED, state=dstate,
                               provider_status=provider_status, payload_sha256=payload_sha,
                               route_hash=route_hash, approval_hash=approval_hash, actor="SYSTEM",
                               reason=f"RETRY_IN_{delay}s", attempt_sequence=attempt)
        _write_retry_state(cfg, delivery_id, route["route_id"], batch["batch_id"], attempt, delay,
                           reason)
    return _result("send-batch", readiness, readiness in _EXIT_OK, batch_id=batch["batch_id"],
                   route_id=route["route_id"], delivery_id=delivery_id, delivery_state=dstate,
                   reason=reason, provider_status=provider_status, retryable=bool(retryable))


def _retry_delay(route, attempt):
    rp = route["retry_policy"]
    delay = rp["initial_delay_seconds"] * (2 ** (attempt - 1))
    return min(delay, rp["maximum_delay_seconds"])


def _write_retry_state(cfg, delivery_id, route_id, batch_id, attempt, delay, reason):
    rec = {"schema_version": "phase7-12-retry-v1", "delivery_id": delivery_id, "route_id": route_id,
           "batch_id": batch_id, "attempt_sequence": attempt, "next_delay_seconds": delay,
           "reason": reason, "scheduled_at": _iso_utc()}
    rec.update(R.AMAZON_COUNTERS)
    _write_json(os.path.join(cfg.retries_dir, delivery_id + ".json"), rec)


def _resolve(cfg, host):
    resolver = cfg.resolver or socket.getaddrinfo
    try:
        infos = resolver(host, None)
    except (socket.gaierror, OSError, UnicodeError, ValueError):
        raise _PreSendError("DNS resolution failed") from None
    addrs = []
    for info in infos or ():
        try:
            addrs.append(str(info[4][0]))
        except (IndexError, TypeError):
            continue
    if not addrs:
        raise _PreSendError("DNS resolution returned no address")
    return addrs


def _is_ip_literal(host):
    import ipaddress
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


# ================================================================ retry a delivery
def retry_delivery(cfg, delivery_id, *, confirm_unknown=None, break_stale=False):
    try:
        return _retry_delivery(cfg, delivery_id, confirm_unknown=confirm_unknown,
                               break_stale=break_stale)
    except NotificationError as e:
        return _err_result("retry-delivery", e, delivery_id=delivery_id)


def _retry_delivery(cfg, delivery_id, *, confirm_unknown=None, break_stale=False):
    hist = _load_delivery_history(cfg, delivery_id)
    if not hist["history"]:
        raise NotificationError("DELIVERY_NOT_FOUND", delivery_id, readiness=DELIVERY_STATE_BLOCKED)
    record = _load_delivery_record(cfg, delivery_id)
    if record is None:
        raise NotificationError("DELIVERY_NOT_FOUND", delivery_id, readiness=DELIVERY_STATE_BLOCKED)
    state = hist.get("current_state")
    if state == DS_SENT:
        return _result("retry-delivery", DELIVERY_SENT, True, delivery_id=delivery_id,
                       detail="ALREADY_SENT", delivery_state=DS_SENT)
    route = load_route(cfg, record["route_id"])
    batch = load_batch(cfg, record["batch_id"])
    route_hash = _route_content_hash(route)
    approval_hash = _load_approval_state(cfg, route["route_id"]).get("head_hash", "")

    # a revoked route can never be retried
    approved, reason, _ = _approval_gate(cfg, route)
    if not approved:
        readiness = APPROVAL_REQUIRED if reason == "APPROVAL_REQUIRED" else APPROVAL_BLOCKED
        return _result("retry-delivery", readiness, False, delivery_id=delivery_id, reason=reason)

    if state == DS_UNKNOWN:
        expected = "RETRY-UNKNOWN:" + delivery_id
        if str(confirm_unknown or "").strip() != expected:
            return _result("retry-delivery", DELIVERY_CONFIRMATION_REQUIRED, False,
                           delivery_id=delivery_id, reason="UNKNOWN_REQUIRES_OWNER_CONFIRMATION",
                           expected_confirmation=expected, delivery_state=DS_UNKNOWN)
        _append_delivery_event(cfg, hist, event_type=EV_OWNER_RETRY_APPROVED, state=DS_UNKNOWN,
                               payload_sha256=record["payload_sha256"], route_hash=route_hash,
                               approval_hash=approval_hash, actor="OWNER",
                               reason="OWNER_CONFIRMED_UNKNOWN_RETRY")
    elif state not in (DS_FAILED, DS_PROVIDER_UNAVAILABLE, DS_RATE_LIMITED, DS_QUIET_HOURS):
        return _result("retry-delivery", DELIVERY_STATE_BLOCKED, False, delivery_id=delivery_id,
                       reason="STATE_NOT_RETRYABLE", delivery_state=state)

    lock = _acquire_lock(cfg, "batch-" + batch["batch_id"], break_stale=break_stale)
    rlock = None
    try:
        rlock = _acquire_lock(cfg, "route-" + route["route_id"], break_stale=break_stale)
        if str(cfg.env.get(LIVE_DELIVERY_ENV, "")).strip() != "1":
            return _result("retry-delivery", DELIVERY_CONFIRMATION_REQUIRED, False,
                           delivery_id=delivery_id, reason="LIVE_DELIVERY_ENV_NOT_SET")
        attempts = hist.get("attempt_sequence", 0)
        if attempts >= route["retry_policy"]["max_attempts"]:
            return _result("retry-delivery", DELIVERY_FAILED, False, delivery_id=delivery_id,
                           reason="MAX_ATTEMPTS_REACHED", delivery_state=DS_FAILED)
        endpoint_url = _endpoint_url(cfg, route)
        decision = NP.evaluate_notification_delivery_url(
            endpoint_url, allowed_hosts=route["endpoint_host_allowlist"], allow_local=cfg.allow_local)
        if not decision.allowed:
            return _result("retry-delivery", DELIVERY_BLOCKED, False, delivery_id=delivery_id,
                           reason=decision.reason_code)
        payload = load_batch_payload(cfg, batch["batch_id"])
        body_bytes = (canonical_json(payload) + "\n").encode("utf-8")
        _append_delivery_event(cfg, hist, event_type=EV_RETRY_STARTED, state=hist["current_state"],
                               payload_sha256=record["payload_sha256"], route_hash=route_hash,
                               approval_hash=approval_hash, actor="OWNER", reason="RETRY_STARTED",
                               attempt_sequence=attempts)
        return _attempt_send(cfg, route, batch, hist, endpoint_url, decision.safe_host, payload,
                             body_bytes, record["payload_sha256"], route_hash, approval_hash,
                             attempts + 1, _now_epoch(cfg), batch["digest_period_id"])
    finally:
        if rlock is not None:
            _release_lock(cfg, "route-" + route["route_id"], rlock)
        _release_lock(cfg, "batch-" + batch["batch_id"], lock)


# ================================================================ send-due (owner-gated automation)
def send_due(cfg, *, reference_time=None, break_stale=False):
    reference_dt = _parse_reference(cfg, reference_time)
    listing = list_routes(cfg)
    executed, skipped = [], []
    for meta in listing["routes"]:
        route_id = meta["route_id"]
        route = load_route(cfg, route_id)
        if route["channel"] != CH_HTTPS_WEBHOOK or not route.get("enabled", True):
            skipped.append({"route_id": route_id, "reason": "NOT_ELIGIBLE"})
            continue
        due, reason, _period = _digest_due(cfg, route, reference_dt)
        if not due:
            skipped.append({"route_id": route_id, "reason": reason})
            continue
        built = build_batch(cfg, route_id, reference_time=reference_time)
        if built["readiness"] == ALERT_SOURCE_BLOCKED:
            skipped.append({"route_id": route_id, "reason": ALERT_SOURCE_BLOCKED})
            continue
        if built["readiness"] == BATCH_READY_EMPTY:
            skipped.append({"route_id": route_id, "reason": "NO_ELIGIBLE_ALERTS"})
            continue
        res = send_batch(cfg, built["batch_id"], reference_time=reference_time, _auto=True,
                         break_stale=break_stale)
        executed.append({"route_id": route_id, "batch_id": built["batch_id"],
                         "readiness": res["readiness"], "delivery_state": res.get("delivery_state")})
    readiness = READY if executed else NOT_DUE
    return {"command": "send-due", "readiness": readiness, "ok": True,
            "reference_time": _iso_utc(reference_dt), "executed": executed, "skipped": skipped,
            "executed_count": len(executed), "amazon_counters": dict(R.AMAZON_COUNTERS)}


# ================================================================ listings + verify
def list_batches(cfg, route_id=None):
    out = []
    root = cfg.batches_dir
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if not name.endswith(".json") or name.endswith(".payload.json"):
                continue
            try:
                rec = _load_json(os.path.join(root, name), "batch")
            except (ValueError, OSError):
                continue
            if route_id and rec.get("route_id") != route_id:
                continue
            out.append({"batch_id": rec.get("batch_id"), "route_id": rec.get("route_id"),
                        "alert_count": rec.get("alert_count"), "readiness": rec.get("readiness"),
                        "digest_period_id": rec.get("digest_period_id")})
    return {"command": "list-batches", "readiness": LIST_READY, "ok": True, "count": len(out),
            "batches": out, "amazon_counters": dict(R.AMAZON_COUNTERS)}


def list_deliveries(cfg, route_id=None):
    out = []
    root = cfg.deliveries_dir
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if not name.startswith("dlv-") or not name.endswith(".json"):
                continue
            try:
                rec = _load_json(os.path.join(root, name), "delivery")
            except (ValueError, OSError):
                continue
            if route_id and rec.get("route_id") != route_id:
                continue
            out.append({"delivery_id": rec.get("delivery_id"), "route_id": rec.get("route_id"),
                        "batch_id": rec.get("batch_id"), "delivery_state": rec.get("delivery_state"),
                        "provider_status": rec.get("provider_status"),
                        "attempt_sequence": rec.get("attempt_sequence")})
    return {"command": "list-deliveries", "readiness": LIST_READY, "ok": True, "count": len(out),
            "deliveries": out, "amazon_counters": dict(R.AMAZON_COUNTERS)}


def show_delivery(cfg, delivery_id):
    record = _load_delivery_record(cfg, delivery_id)
    if record is None:
        raise NotificationError("DELIVERY_NOT_FOUND", delivery_id, readiness=DELIVERY_STATE_BLOCKED)
    hist = _load_delivery_history(cfg, delivery_id)
    return {"command": "show-delivery", "readiness": SHOW_READY, "ok": True,
            "delivery": record, "history": hist.get("history", []),
            "current_state": hist.get("current_state"), "aggregate_hash": hist.get("head_hash", ""),
            "amazon_counters": dict(R.AMAZON_COUNTERS)}


def verify_state(cfg, *, route_id=None):
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    readiness = VERIFY_READY
    route_ids = [route_id] if route_id else _all_route_ids(cfg)
    for rid in route_ids:
        try:
            route = load_route(cfg, rid)
            add(f"route_identity:{rid}", True)
        except NotificationError as e:
            add(f"route_identity:{rid}", False, e.code)
            readiness = e.readiness
            continue
        try:
            _load_approval_state(cfg, rid)
            add(f"approval_chain:{rid}", True)
        except NotificationError as e:
            add(f"approval_chain:{rid}", False, e.code)
            readiness = APPROVAL_BLOCKED
    # delivery histories
    root = cfg.delivery_history_dir
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if not name.endswith(".json"):
                continue
            did = name[:-5]
            try:
                _load_delivery_history(cfg, did)
                add(f"delivery_chain:{did}", True)
            except NotificationError as e:
                add(f"delivery_chain:{did}", False, e.code)
                readiness = DELIVERY_STATE_BLOCKED
    ok = all(c["ok"] for c in checks)
    return {"command": "verify-state", "readiness": readiness if ok else readiness, "ok": ok,
            "checks": checks, "checks_passed": sum(1 for c in checks if c["ok"]),
            "checks_total": len(checks), "amazon_counters": dict(R.AMAZON_COUNTERS)}


def _all_route_ids(cfg):
    out = []
    if os.path.isdir(cfg.routes_dir):
        for name in sorted(os.listdir(cfg.routes_dir)):
            if name.endswith(".json"):
                out.append(name[:-5])
    return out


# ================================================================ deterministic exports
_TSV_COLS = ("delivery_id", "route_id", "batch_id", "provider_type", "channel", "destination_label",
             "approved_endpoint_host", "delivery_state", "provider_status", "attempt_sequence",
             "alert_count", "payload_sha256", "response_sha256")


def _tsv(value):
    if isinstance(value, (list, tuple)):
        value = ";".join(str(v) for v in value)
    return R._tsv_cell(value)


def export(cfg, *, route_id=None):
    """Write deterministic JSON, TSV and Markdown delivery reports. No secret is ever included."""
    deliveries = list_deliveries(cfg, route_id)["deliveries"]
    records = []
    for d in deliveries:
        rec = _load_delivery_record(cfg, d["delivery_id"])
        if rec:
            records.append(rec)
    records.sort(key=lambda r: r["delivery_id"])
    out_dir = cfg.reports_dir

    report = {
        "schema_version": REPORT_SCHEMA,
        "route_id": route_id,
        "delivery_count": len(records),
        "state_counts": _count_by(records, "delivery_state"),
        "deliveries": [{k: r.get(k) for k in ("delivery_id", "route_id", "batch_id", "provider_type",
                                              "channel", "destination_label", "approved_endpoint_host",
                                              "delivery_state", "provider_status", "attempt_sequence",
                                              "alert_count", "payload_sha256", "response_sha256")}
                       for r in records],
        "disclaimers": list(DISCLAIMERS),
    }
    report.update(R.AMAZON_COUNTERS)
    _write_json(os.path.join(out_dir, "delivery_report.json"), report)

    lines = ["\t".join(_TSV_COLS)]
    for r in records:
        lines.append("\t".join(_tsv(r.get(c)) for c in _TSV_COLS))
    _write_text(os.path.join(out_dir, "delivery_report.tsv"), "\n".join(lines) + "\n")

    _write_text(os.path.join(out_dir, "delivery_report.md"), _render_report(report, records))
    return {"command": "export", "readiness": EXPORT_READY, "ok": True, "route_id": route_id,
            "delivery_count": len(records),
            "exports": [_rel(cfg, os.path.join(out_dir, n)) for n in
                        ("delivery_report.json", "delivery_report.tsv", "delivery_report.md")],
            "amazon_counters": dict(R.AMAZON_COUNTERS)}


def _count_by(records, key):
    counts = {}
    for r in records:
        counts[r.get(key)] = counts.get(r.get(key), 0) + 1
    return counts


def _render_report(report, records):
    L = ["# Phase 7.12 — Owner Notification Delivery Report", "",
         f"- Deliveries: {report['delivery_count']}", ""]
    L.append("## Delivery states")
    for st in sorted(report["state_counts"], key=lambda x: str(x)):
        L.append(f"- {st}: {report['state_counts'][st]}")
    L.append("")
    L.append("## Deliveries")
    for r in records:
        L.append(f"- `{r['delivery_id']}` — {r['delivery_state']} (HTTP {r.get('provider_status')}) "
                 f"route `{r['route_id']}` batch `{r['batch_id']}` host "
                 f"`{r.get('approved_endpoint_host')}`")
    L.append("")
    L.append("## Disclaimers")
    for d in DISCLAIMERS:
        L.append(f"- {d}")
    L.append("")
    L.append("## Amazon-account boundary (constant zero)")
    for k in sorted(R.AMAZON_COUNTERS):
        L.append(f"- {k}: {R.AMAZON_COUNTERS[k]}")
    return "\n".join(L) + "\n"


# ================================================================ provider-check (no network)
def provider_check(cfg, route_id):
    """Validate a route's endpoint policy WITHOUT any network: classify the (env-referenced) endpoint,
    confirm allowlist coverage, report whether the env var is set (never its value)."""
    route = load_route(cfg, route_id)
    result = {"command": "provider-check", "readiness": PROVIDER_CHECK_READY, "ok": True,
              "route_id": route_id, "channel": route["channel"],
              "payload_format": route["payload_format"],
              "endpoint_env": route.get("endpoint_env"),
              "endpoint_env_set": bool((cfg.env.get(route.get("endpoint_env") or "") or "").strip()),
              "endpoint_host_allowlist": route["endpoint_host_allowlist"],
              "amazon_counters": dict(R.AMAZON_COUNTERS)}
    if route["channel"] == CH_HTTPS_WEBHOOK and result["endpoint_env_set"]:
        url = (cfg.env.get(route["endpoint_env"]) or "").strip()
        decision = NP.evaluate_notification_delivery_url(
            url, allowed_hosts=route["endpoint_host_allowlist"], allow_local=cfg.allow_local)
        result["endpoint_allowed"] = decision.allowed
        result["endpoint_host"] = decision.safe_host
        result["reason_code"] = decision.reason_code
        if not decision.allowed:
            result["ok"] = False
            seller = decision.reason_code in (D.SELLER_CENTRAL_ACCESS_PROHIBITED,
                                              D.AMAZON_API_PROHIBITED,
                                              D.AMAZON_ACCOUNT_ACCESS_PROHIBITED)
            result["readiness"] = SELLER_CENTRAL_POLICY_BLOCKED if seller else DELIVERY_BLOCKED
    return result


# ================================================================ scheduler plan (read-only)
def _pwsq(s):
    return "'" + str(s).replace("'", "''") + "'"


def _shq(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def scheduler_plan(cfg, flavor="all"):
    """Emit a READ-ONLY OS-scheduler plan for `send-due` the OWNER may register. This never runs or
    registers schtasks / cron / crontab / systemctl / launchctl / Task Scheduler COM. It contains no
    secret and no Seller-Central target; it references environment-variable NAMES only."""
    python_exe = sys.executable or "python"
    base = cfg.base_dir
    alert = cfg.alert_dir or "runs/T2/phase7/7.11"
    module = "production.phase7_owner_notification_delivery"

    win = ("powershell.exe -NoProfile -Command \"$env:" + LIVE_DELIVERY_ENV + "='1'; & " +
           _pwsq(python_exe) + " -m " + module + " --base-dir " + _pwsq(base) + " --alert-dir " +
           _pwsq(alert) + " send-due\"")
    cron = ("0 * * * * " + LIVE_DELIVERY_ENV + "=1 " + _shq(python_exe) + " -m " + module +
            " --base-dir " + _shq(base) + " --alert-dir " + _shq(alert) + " send-due")
    manual = (_shq(python_exe) + " -m " + module + " --base-dir " + _shq(base) + " --alert-dir " +
              _shq(alert) + " send-due")

    template = {"module": module, "subcommand": "send-due",
                "minimum_interval_hours": MIN_DIGEST_INTERVAL_HOURS,
                "flavors": ["windows", "cron", "manual"], "env_names_only": [LIVE_DELIVERY_ENV,
                DEFAULT_URL_ENV, AUTO_SEND_TOKEN_ENV]}
    plan = {
        "schema_version": SCHEDULER_PLAN_SCHEMA,
        "owner_action_required": "OWNER_ACTION_REQUIRED",
        "registration_is_owner_action": True,
        "note": ("Phase 7.12 never registers or executes an OS scheduler task. Review the example "
                 "below and register it yourself; the minimum automatic interval is 1 hour. Live "
                 "delivery still requires an approved route, the environment permission, and a "
                 "route-bound auto-send token."),
        "windows_task_scheduler_example": win,
        "cron_example": cron,
        "manual_invocation": manual,
        "environment_variable_names": [LIVE_DELIVERY_ENV, DEFAULT_URL_ENV, AUTO_SEND_TOKEN_ENV],
        "template_hash": _sha(template),
    }
    plan.update(R.AMAZON_COUNTERS)
    if flavor in ("windows", "cron", "manual"):
        plan["selected_flavor"] = flavor
    _write_json(os.path.join(cfg.scheduler_plans_dir, "send-due.json"), plan)
    return {"command": "scheduler-plan", "readiness": SCHEDULER_PLAN_READY, "ok": True, "plan": plan,
            "amazon_counters": dict(R.AMAZON_COUNTERS)}


# ================================================================ locking / concurrency
def _lock_path(cfg, name):
    return os.path.join(cfg.locks_dir, name + ".lock")


def _acquire_lock(cfg, name, *, break_stale=False):
    os.makedirs(cfg.locks_dir, exist_ok=True)
    path = _lock_path(cfg, name)
    created = _now_epoch(cfg)
    token = {"name": name, "pid": cfg.pid, "created_epoch": created, "tool_version": TOOL_VERSION}
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
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    continue
                raise NotificationError("LOCK_STALE",
                                        f"stale lock (age {int(age)}s); rerun with break-stale-lock",
                                        readiness=ROUTE_BLOCKED)
            raise NotificationError("LOCK_HELD", "another run holds this lock", readiness=ROUTE_BLOCKED)
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(canonical_json(token))
            return token
    raise NotificationError("LOCK_HELD", "could not acquire lock", readiness=ROUTE_BLOCKED)


def _release_lock(cfg, name, token):
    path = _lock_path(cfg, name)
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


def break_stale_lock(cfg, name):
    path = _lock_path(cfg, name)
    if not os.path.isfile(path):
        return {"command": "break-stale-lock", "readiness": LOCK_READY, "ok": True, "lock": name,
                "detail": "NO_LOCK", "amazon_counters": dict(R.AMAZON_COUNTERS)}
    try:
        existing = _load_json(path, "lock")
    except (ValueError, OSError):
        existing = {}
    age = _now_epoch(cfg) - float(existing.get("created_epoch", _now_epoch(cfg)))
    if age <= MAX_LOCK_AGE_SECONDS:
        raise NotificationError("LOCK_NOT_STALE",
                                f"lock age {int(age)}s <= {MAX_LOCK_AGE_SECONDS}s; not removed",
                                readiness=ROUTE_BLOCKED)
    os.remove(path)
    return {"command": "break-stale-lock", "readiness": LOCK_READY, "ok": True, "lock": name,
            "detail": "REMOVED_STALE", "lock_age_seconds": int(age),
            "amazon_counters": dict(R.AMAZON_COUNTERS)}


# ================================================================ validate-only (no side effects)
def validate_only(definition_path):
    """validate-only — parse + validate a route definition and perform NO DNS lookup, NO HTTP request,
    write NO file, create NO base directory, acquire NO lock, mutate NO state, read NO secret env
    value, launch NO browser, and execute NO subprocess."""
    with open(definition_path, "rb") as f:
        defn = R._loads_strict(f.read(), "route")
    canonical, route_id = validate_route(defn)
    return {"command": "validate-only", "readiness": VALIDATE_READY, "ok": True, "route_id": route_id,
            "name": canonical["name"], "channel": canonical["channel"],
            "payload_format": canonical["payload_format"],
            "route_content_hash": _route_content_hash(canonical),
            "files_written": 0, "network_requests": 0, "dns_lookups": 0, "locks_acquired": 0,
            "secret_env_reads": 0, "amazon_counters": dict(R.AMAZON_COUNTERS)}


# ================================================================ result helper
def _result(command, readiness, ok, **extra):
    out = {"command": command, "readiness": readiness, "ok": bool(ok)}
    out.update(extra)
    out["amazon_counters"] = dict(R.AMAZON_COUNTERS)
    return out


def _err_result(command, e, **extra):
    """Convert a NotificationError raised inside an operational command into a stable result dict
    (never leaks a traceback or secret to a direct caller)."""
    out = {"command": command, "readiness": getattr(e, "readiness", ROUTE_BLOCKED), "ok": False,
           "error_code": getattr(e, "code", "ERROR"), "detail": _redact(getattr(e, "detail", ""))}
    out.update(extra)
    out["amazon_counters"] = dict(R.AMAZON_COUNTERS)
    return out


# ================================================================ CLI
def _print(obj):
    sys.stdout.write(canonical_json(_redact(obj)) + "\n")


def _exit_code(result):
    return 0 if result.get("readiness") in _EXIT_OK else 3


def _read_definition(path):
    with open(path, "rb") as f:
        return R._loads_strict(f.read(), "route")


def _extra_filters(args):
    flt = {}
    if getattr(args, "watchlist_id", None):
        flt["watchlist_ids"] = [args.watchlist_id]
    if getattr(args, "severity", None):
        flt["severities"] = [args.severity]
    if getattr(args, "status", None):
        flt["statuses"] = [args.status]
    if getattr(args, "rule_id", None):
        flt["rule_ids"] = [args.rule_id]
    if getattr(args, "source_type", None):
        flt["source_types"] = [args.source_type]
    if getattr(args, "field_path", None):
        flt["field_paths"] = [args.field_path]
    return flt or None


def build_parser():
    p = argparse.ArgumentParser(
        prog="python -m production.phase7_owner_notification_delivery",
        description="Phase 7.12 — connected OWNER notification delivery, digest queue & audit trail. "
                    "Delivers LOCAL Phase 7.11 owner alerts to an owner-approved HTTPS webhook only; "
                    "never connects to an Amazon seller account or sends customer/marketing messages.")
    p.add_argument("--base-dir", default="runs/T2/phase7/7.12",
                   help="Phase 7.12 workspace (default: runs/T2/phase7/7.12)")
    p.add_argument("--alert-dir", default="runs/T2/phase7/7.11",
                   help="accepted, READ-ONLY Phase 7.11 alert workspace")
    p.add_argument("--allow-local-test-server", action="store_true",
                   help="permit an https:// loopback endpoint (tests / owner-local webhook)")
    sub = p.add_subparsers(dest="command", required=True)

    cr = sub.add_parser("create-route", help="validate + persist a notification-route definition")
    cr.add_argument("--definition", required=True)
    vr = sub.add_parser("validate-route", help="validate a route definition or stored route")
    vr.add_argument("--definition")
    vr.add_argument("--route-id")
    sr = sub.add_parser("show-route", help="print a stored route")
    sr.add_argument("--route-id", required=True)
    sub.add_parser("list-routes", help="list stored routes")
    ar = sub.add_parser("approve-route", help="explicitly approve a route for delivery")
    ar.add_argument("--route-id", required=True)
    ar.add_argument("--actor", required=True)
    ar.add_argument("--note", default=None)
    ar.add_argument("--mode", default=MODE_LIVE, choices=sorted(_DELIVERY_MODES))
    ar.add_argument("--allow-auto-send", action="store_true")
    rv = sub.add_parser("revoke-route", help="revoke an approved route")
    rv.add_argument("--route-id", required=True)
    rv.add_argument("--actor", required=True)
    rv.add_argument("--note", default=None)
    sa = sub.add_parser("show-approval", help="print a route's approval chain")
    sa.add_argument("--route-id", required=True)
    sub.add_parser("list-approvals", help="list route approvals")

    for name, help_text in (("preview", "build a batch + payload locally (no approval, no network)"),
                            ("build-batch", "build + persist a deterministic notification batch")):
        bp = sub.add_parser(name, help=help_text)
        bp.add_argument("--route-id", required=True)
        bp.add_argument("--reference-time", default=None)
        bp.add_argument("--watchlist-id", default=None)
        bp.add_argument("--severity", default=None, choices=sorted(_SEVERITIES))
        bp.add_argument("--status", default=None, choices=sorted(W._ALERT_STATUSES))
        bp.add_argument("--rule-id", default=None)
        bp.add_argument("--source-type", default=None)
        bp.add_argument("--field-path", default=None)

    wo = sub.add_parser("write-outbox", help="write a deterministic local outbox preview (no network)")
    wo.add_argument("--route-id", required=True)
    wo.add_argument("--batch-id", default=None)
    wo.add_argument("--reference-time", default=None)

    sb = sub.add_parser("send-batch", help="send a batch to an approved HTTPS webhook (gated)")
    sb.add_argument("--batch-id", required=True)
    sb.add_argument("--confirm-send", default=None)
    sb.add_argument("--reference-time", default=None)
    sb.add_argument("--break-stale-lock", action="store_true")
    sd = sub.add_parser("send-due", help="send due digest batches for approved auto-send routes")
    sd.add_argument("--reference-time", default=None)
    sd.add_argument("--break-stale-lock", action="store_true")
    rd = sub.add_parser("retry-delivery", help="retry an eligible failed delivery")
    rd.add_argument("--delivery-id", required=True)
    rd.add_argument("--confirm-unknown", default=None)
    rd.add_argument("--break-stale-lock", action="store_true")

    lb = sub.add_parser("list-batches", help="list notification batches")
    lb.add_argument("--route-id", default=None)
    ld = sub.add_parser("list-deliveries", help="list deliveries")
    ld.add_argument("--route-id", default=None)
    sdl = sub.add_parser("show-delivery", help="show a delivery + its history chain")
    sdl.add_argument("--delivery-id", required=True)
    vs = sub.add_parser("verify-state", help="verify route/approval/delivery integrity")
    vs.add_argument("--route-id", default=None)
    ex = sub.add_parser("export", help="write deterministic JSON/TSV/Markdown delivery reports")
    ex.add_argument("--route-id", default=None)
    pc = sub.add_parser("provider-check", help="validate a route's endpoint policy (no network)")
    pc.add_argument("--route-id", required=True)
    spn = sub.add_parser("scheduler-plan", help="emit a read-only OS-scheduler plan (owner registers)")
    spn.add_argument("--flavor", default="all", choices=("all", "windows", "cron", "manual"))
    bl = sub.add_parser("break-stale-lock", help="explicitly break a single stale lock")
    bl.add_argument("--lock", required=True)
    vo = sub.add_parser("validate-only", help="validate a route; no network, no files, no lock")
    vo.add_argument("--definition", required=True)
    return p


def _dispatch(cfg, args):
    cmd = args.command
    if cmd == "create-route":
        return create_route(cfg, _read_definition(args.definition))
    if cmd == "validate-route":
        return validate_route_cmd(cfg, definition_path=args.definition, route_id=args.route_id)
    if cmd == "show-route":
        return show_route(cfg, args.route_id)
    if cmd == "list-routes":
        return list_routes(cfg)
    if cmd == "approve-route":
        return approve_route(cfg, args.route_id, args.actor, args.note, delivery_mode=args.mode,
                             allow_auto_send=args.allow_auto_send)
    if cmd == "revoke-route":
        return revoke_route(cfg, args.route_id, args.actor, args.note)
    if cmd == "show-approval":
        return show_approval(cfg, args.route_id)
    if cmd == "list-approvals":
        return list_approvals(cfg)
    if cmd == "preview":
        return preview(cfg, args.route_id, reference_time=args.reference_time,
                       extra_filters=_extra_filters(args))
    if cmd == "build-batch":
        return build_batch(cfg, args.route_id, reference_time=args.reference_time,
                           extra_filters=_extra_filters(args))
    if cmd == "write-outbox":
        return write_outbox(cfg, args.route_id, batch_id=args.batch_id,
                            reference_time=args.reference_time)
    if cmd == "send-batch":
        return send_batch(cfg, args.batch_id, confirm_send=args.confirm_send,
                          reference_time=args.reference_time, break_stale=args.break_stale_lock)
    if cmd == "send-due":
        return send_due(cfg, reference_time=args.reference_time, break_stale=args.break_stale_lock)
    if cmd == "retry-delivery":
        return retry_delivery(cfg, args.delivery_id, confirm_unknown=args.confirm_unknown,
                              break_stale=args.break_stale_lock)
    if cmd == "list-batches":
        return list_batches(cfg, args.route_id)
    if cmd == "list-deliveries":
        return list_deliveries(cfg, args.route_id)
    if cmd == "show-delivery":
        return show_delivery(cfg, args.delivery_id)
    if cmd == "verify-state":
        return verify_state(cfg, route_id=args.route_id)
    if cmd == "export":
        return export(cfg, route_id=args.route_id)
    if cmd == "provider-check":
        return provider_check(cfg, args.route_id)
    if cmd == "scheduler-plan":
        return scheduler_plan(cfg, args.flavor)
    if cmd == "break-stale-lock":
        return break_stale_lock(cfg, args.lock)
    if cmd == "validate-only":
        return validate_only(args.definition)
    raise NotificationError("UNKNOWN_COMMAND", cmd)   # pragma: no cover - argparse enforces choices


def run_cli(argv=None, *, cfg_factory=None):
    args = build_parser().parse_args(argv)
    if cfg_factory is not None:
        cfg = cfg_factory(args)
    else:
        cfg = Config(args.base_dir, alert_dir=args.alert_dir,
                     allow_local=args.allow_local_test_server)
    try:
        result = _dispatch(cfg, args)
    except (NotificationError, W.WatchlistError, R.ResearchError, R.PlanError) as e:
        readiness = getattr(e, "readiness", ROUTE_BLOCKED)
        result = {"command": args.command, "readiness": readiness, "ok": False,
                  "error_code": getattr(e, "code", "ERROR"),
                  "detail": _redact(str(getattr(e, "detail", ""))),
                  "amazon_counters": dict(R.AMAZON_COUNTERS)}
    except NP.ConnectedNetworkDenied as e:
        result = {"command": args.command, "readiness": SELLER_CENTRAL_POLICY_BLOCKED, "ok": False,
                  "error_code": getattr(e, "reason_code", "ERROR"),
                  "amazon_counters": dict(R.AMAZON_COUNTERS)}
    except M.MoneyError as e:
        result = {"command": args.command, "readiness": ROUTE_BLOCKED, "ok": False,
                  "error_code": "NUMERIC_INVALID", "detail": _redact(str(e)),
                  "amazon_counters": dict(R.AMAZON_COUNTERS)}
    _print(result)
    return _exit_code(result)


def main(argv=None):
    return run_cli(argv)


if __name__ == "__main__":
    sys.exit(main())
