#!/usr/bin/env python3
"""Phase 7.13 — Unified Owner Console.

ONE local console that unifies the already-accepted Phase 7 authorities behind a single loopback
web interface. It is an AGGREGATION + ORCHESTRATION layer only: it re-uses the accepted authorities
for every read model and for every state-changing action, and it never re-implements or re-interprets
their business logic, never invents a metric, and never weakens an integrity check.

Reused accepted authorities (never duplicated):
  * Phase 7.8 owner operations dashboard  -> the accepted 7.3-7.7 business read model;
  * Phase 7.9 connected backup/recovery   -> snapshots, verify, update-check/stage, recovery plans;
  * Phase 7.10 connected public research   -> research runs / evidence read model;
  * Phase 7.11 research watchlists         -> watchlists / schedules / change detection / owner alerts;
  * Phase 7.12 owner notification delivery -> routes / batches / outbox / delivery history;
  * core.network_policy / core.diagnostics / core.money.

Permanent boundary (never configurable, always first): this console never connects to Amazon Seller
Central, never uses a seller sign-in, seller login, seller credentials, seller cookies, seller
sessions, or seller tokens, never uses a seller API or advertising API, never downloads a seller
report, never mutates a campaign / bid / budget / keyword / target / negative / listing / inventory,
never performs a seller bulk upload, never drives a seller browser, and never messages a buyer or
requests a review. Every allowed public-Internet operation is performed only by an accepted backend
authority (Phase 7.9 / 7.10 / 7.12); the browser front-end contacts nothing but this local server.

The console binds to a loopback address only, serves a self-contained front-end (no CDN, no external
script / style / font / image / analytics), issues an ephemeral in-memory session with a CSRF token,
requires an explicit owner confirmation for every state-changing or network action through a fixed
server-side action allowlist, and records an append-only, hash-chained orchestration audit trail.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from threading import RLock
from urllib.parse import urlsplit, parse_qsl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ("", "core", "production"):
    _p = _ROOT if not _sub else os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import product_workspace as PW                      # noqa: E402  canonical json / content hash
from production import phase7_owner_operations_dashboard as OPS     # noqa: E402  accepted 7.3-7.7 read model
from production import phase7_owner_dashboard as DASH               # noqa: E402  accepted TSV rule reuse
from production import phase7_connected_backup_recovery as BACKUP   # noqa: E402  accepted 7.9 authority
from production import phase7_connected_public_research as RESEARCH # noqa: E402  accepted 7.10 authority
from production import phase7_connected_research_watchlists as WATCH# noqa: E402  accepted 7.11 authority
from production import phase7_owner_notification_delivery as NOTIFY # noqa: E402  accepted 7.12 authority
from production import phase7_owner_next_action as NEXT             # noqa: E402  7.14 guidance read model
from production import phase7_workflow_stage_model as WSM           # noqa: E402  Dashboard V1 stage engine
from production import seller_central_package as SCP                 # noqa: E402  accepted 6F package verify
from core import network_policy as NP                               # noqa: E402  the ONE network authority
from core import diagnostics as DIAG                                # noqa: E402  redacted local diagnostics
from core import money as MONEY                                     # noqa: E402  Decimal authority (parity import)

canonical_json = PW.canonical_json
content_sha256 = PW.content_sha256
_TSV_CELL = DASH._tsv_cell   # reuse the accepted formula-injection rule verbatim

# ================================================================ identity / versions
STAGE_ID = "7.13"
STAGE_NAME = "Unified Owner Console"
API_VERSION = "v1"
API_SCHEMA = "phase7-13-console-api-v1"
MODEL_SCHEMA = "phase7-13-console-model-v1"
EXPORT_SCHEMA = "phase7-13-console-snapshot-v1"
AUDIT_SCHEMA = "phase7-13-console-audit-event-v1"
SESSION_SCHEMA = "phase7-13-console-session-v1"

# ---------------------------------------------------------------- console readiness states
CONSOLE_READY = "SESSION7_13_CONSOLE_READY"
CONSOLE_READY_PARTIAL = "SESSION7_13_CONSOLE_READY_PARTIAL"
CONSOLE_READY_EMPTY = "SESSION7_13_CONSOLE_READY_EMPTY"
CONSOLE_REQUIRED = "SESSION7_13_CONSOLE_REQUIRED"
CONSOLE_BLOCKED = "SESSION7_13_CONSOLE_BLOCKED"
MODULE_UNAVAILABLE = "SESSION7_13_MODULE_UNAVAILABLE"
DATA_STALE = "SESSION7_13_DATA_STALE"
ACTION_CONFIRMATION_REQUIRED = "SESSION7_13_ACTION_CONFIRMATION_REQUIRED"
ACTION_READY = "SESSION7_13_ACTION_READY"
ACTION_COMPLETED = "SESSION7_13_ACTION_COMPLETED"
ACTION_FAILED = "SESSION7_13_ACTION_FAILED"
ACTION_BLOCKED = "SESSION7_13_ACTION_BLOCKED"
SESSION_REQUIRED = "SESSION7_13_SESSION_REQUIRED"
SESSION_EXPIRED = "SESSION7_13_SESSION_EXPIRED"
CSRF_BLOCKED = "SESSION7_13_CSRF_BLOCKED"
AUDIT_STATE_BLOCKED = "SESSION7_13_AUDIT_STATE_BLOCKED"
INTEGRITY_BLOCKED = "SESSION7_13_INTEGRITY_BLOCKED"
SELLER_CENTRAL_POLICY_BLOCKED = "SESSION7_13_SELLER_CENTRAL_POLICY_BLOCKED"

_OK_READINESS = (CONSOLE_READY, CONSOLE_READY_PARTIAL, CONSOLE_READY_EMPTY)
_BLOCKING_READINESS = (CONSOLE_BLOCKED, AUDIT_STATE_BLOCKED, INTEGRITY_BLOCKED,
                       SELLER_CENTRAL_POLICY_BLOCKED)

# ---------------------------------------------------------------- per-module status codes
MOD_READY = "READY"
MOD_READY_EMPTY = "READY_EMPTY"
MOD_READY_PARTIAL = "READY_PARTIAL"
MOD_UNAVAILABLE = "MODULE_UNAVAILABLE"
MOD_BLOCKED = "BLOCKED"

# plain owner-facing labels (canonical value is always preserved alongside)
OWNER_LABELS = {
    MOD_READY: "READY", MOD_READY_EMPTY: "READY — NO ITEMS", MOD_READY_PARTIAL: "READY — PARTIAL",
    MOD_UNAVAILABLE: "NEEDS SETUP", MOD_BLOCKED: "BLOCKED", DATA_STALE: "STALE",
    CONSOLE_READY: "READY", CONSOLE_READY_EMPTY: "READY — NO ITEMS",
    CONSOLE_READY_PARTIAL: "READY — PARTIAL", CONSOLE_REQUIRED: "NEEDS SETUP",
    CONSOLE_BLOCKED: "BLOCKED", AUDIT_STATE_BLOCKED: "INTEGRITY ERROR",
    INTEGRITY_BLOCKED: "INTEGRITY ERROR",
}

# Owner-facing names for the integrated modules — the internal key is never the visible label.
MODULE_OWNER_LABELS = {
    "analysis": "Analysis & Decisions", "research": "Research", "watchlists": "Watchlists & Alerts",
    "notifications": "Notifications", "backup": "Backup & Recovery", "workflow": "Workflow",
}

# ---------------------------------------------------------------- limits
MAX_ARTIFACT_BYTES = OPS.MAX_ARTIFACT_BYTES
MAX_URL_BYTES = 8192
MAX_QUERY_VALUE = 256
MAX_BODY_BYTES = 262144          # 256 KiB bounded JSON action body
MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 50
MAX_ACTIVITY_ROWS = 2000
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8780
DEFAULT_WORKSPACE_ROOT = os.path.join("runs", "T2", "phase7")
DEFAULT_BASE_DIR = os.path.join("runs", "T2", "phase7", "7.13")

SESSION_TTL_SECONDS = 8 * 3600
ACTION_TOKEN_TTL_SECONDS = 300
CACHE_MAX_AGE_SECONDS = 5.0
MIN_CLIENT_POLL_SECONDS = 10
DATA_STALE_AFTER_SECONDS = 24 * 3600

SESSION_COOKIE = "console_session"
CSRF_HEADER = "X-CSRF-Token"

_WORKSPACE_SUBDIRS = ("audit", "snapshots", "exports", "logs", "validation")
AUDIT_FILE = "console_audit.jsonl"

# ---------------------------------------------------------------- static assets
STATIC_DIR = os.path.join(_HERE, "phase7_unified_owner_console_static")
STATIC_FILES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "icons.svg": "image/svg+xml; charset=utf-8",
    "favicon.svg": "image/svg+xml; charset=utf-8",
}
# A browser requests /favicon.ico on its own. Serving the same local asset there removes the 404 the
# browser would otherwise log on every single page load.
FAVICON_ALIASES = {"favicon.ico": "favicon.svg"}

# Strict same-origin CSP: no inline script, no external anything, no framing, no eval.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'; form-action 'self'")
PERMISSIONS_POLICY = ("accelerometer=(), autoplay=(), camera=(), display-capture=(), geolocation=(), "
                      "gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=(), "
                      "interest-cohort=()")
_BASE_SECURITY_HEADERS = (
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Permissions-Policy", PERMISSIONS_POLICY),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Embedder-Policy", "require-corp"),
)

# Permanent boundary counters — every counter is a constant zero; no code path increments them.
SELLER_CENTRAL_COUNTERS = {
    "seller_central_connections": 0, "seller_api_calls": 0, "advertising_api_calls": 0,
    "seller_account_mutations": 0, "seller_report_downloads": 0, "seller_bulk_uploads": 0,
    "seller_browser_automation_actions": 0, "seller_credential_store_count": 0,
    "buyer_messages_sent": 0, "review_requests_sent": 0,
}
THIS_CONSOLE_NEVER = {
    "connects_to_seller_central": True, "uses_seller_sign_in": True, "stores_seller_credentials": True,
    "uses_seller_cookies_or_sessions_or_tokens": True, "uses_a_seller_api": True,
    "uses_an_advertising_api": True, "downloads_a_seller_report": True, "mutates_a_campaign": True,
    "mutates_a_bid_or_budget": True, "mutates_a_keyword_target_or_negative": True,
    "mutates_a_listing_or_inventory": True, "performs_a_seller_bulk_upload": True,
    "drives_a_seller_browser": True, "messages_a_buyer": True, "requests_a_review": True,
    "opens_a_browser_automatically": True, "registers_a_scheduler_or_service": True,
    "exposes_the_server_publicly": True, "executes_a_destructive_restore_from_the_console": True,
}
DISCLAIMER_LINES = (
    "UNIFIED OWNER CONSOLE — LOCAL, LOOPBACK-ONLY",
    "No connection to Amazon Seller Central is ever made by this console.",
    "No seller-account action of any kind is performed by this console.",
    "Every business status shown comes verbatim from its accepted upstream authority.",
    "Observational outcome classifications never establish causation.",
    "This export is not an Amazon upload file.",
)


class ConsoleError(Exception):
    """A safe, client-facing error carrying an HTTP status + short code (never a stack trace)."""

    def __init__(self, status, code, detail="", *, readiness=None):
        self.status = status
        self.code = code
        self.detail = detail
        self.readiness = readiness
        super().__init__(f"{code}: {detail}" if detail else code)


# ================================================================ small helpers
def _now_utc():
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt):
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _s(v):
    return "" if v is None else str(v)


def _rel_display(abs_path):
    """A normalized relative display path that never leaks an absolute local path — the portion from
    the 'phase7' anchor onward (POSIX separators), else the basename."""
    if not abs_path:
        return None
    norm = str(abs_path).replace("\\", "/")
    parts = norm.split("/")
    if "phase7" in parts:
        return "/".join(parts[parts.index("phase7"):])
    return parts[-1] if parts else norm


def _row_id(*, view, entity_id, content_hash):
    return "uc-" + content_sha256({
        "kind": "phase7-13-console-row-id", "view": view,
        "entity_id": _s(entity_id), "content_sha256": _s(content_hash)})[:24]


def _request_id():
    return "req-" + secrets.token_hex(8)


def _load_json_file(path, what, *, max_bytes=MAX_ARTIFACT_BYTES):
    """Bounded, strict JSON read (rejects oversize, null bytes, NaN/Infinity). Read-only."""
    if not os.path.isfile(path):
        raise ConsoleError(500, "ARTIFACT_MISSING", what)
    if os.path.getsize(path) > max_bytes:
        raise ConsoleError(500, "ARTIFACT_OVERSIZE", what)
    with open(path, "rb") as f:
        data = f.read()
    if b"\x00" in data:
        raise ConsoleError(500, "NULL_BYTE_REJECTED", what)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ConsoleError(500, "MALFORMED_ENCODING", f"{what}: {e}") from e

    def _reject(tok):
        raise ConsoleError(500, "NON_FINITE_NUMBER_REJECTED", f"{what}: {tok}")

    try:
        return json.loads(text, parse_constant=_reject)
    except ValueError as e:
        raise ConsoleError(500, "MALFORMED_JSON", f"{what}: {e}") from e


def _reject_duplicate_keys(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ConsoleError(400, "DUPLICATE_JSON_KEY", k)
        seen[k] = v
    return seen


def parse_json_body(data, *, what="body"):
    """Strict JSON parse for an action request: reject NaN/Infinity and duplicate object keys."""
    if not data:
        raise ConsoleError(400, "EMPTY_BODY", what)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ConsoleError(400, "MALFORMED_ENCODING", what) from e

    def _reject(tok):
        raise ConsoleError(400, "NON_FINITE_NUMBER_REJECTED", tok)

    try:
        obj = json.loads(text, parse_constant=_reject, object_pairs_hook=_reject_duplicate_keys)
    except ConsoleError:
        raise
    except ValueError as e:
        raise ConsoleError(400, "MALFORMED_JSON", str(e)) from e
    if not isinstance(obj, dict):
        raise ConsoleError(400, "JSON_OBJECT_REQUIRED", what)
    return obj


def _repo_commit(repo_root):
    """Resolve the current repo HEAD commit by reading git ref FILES only (never a subprocess).
    Returns (commit_sha_or_None, branch_or_None)."""
    git = os.path.join(repo_root, ".git")
    head = os.path.join(git, "HEAD")
    if not os.path.isfile(head):
        return None, None
    try:
        with open(head, "r", encoding="utf-8") as f:
            ref = f.read().strip()
    except OSError:
        return None, None
    if ref.startswith("ref:"):
        branch_ref = ref.split(" ", 1)[1].strip() if " " in ref else ref[4:].strip()
        branch = branch_ref.split("/")[-1] if branch_ref else None
        loose = os.path.join(git, *branch_ref.split("/"))
        if os.path.isfile(loose):
            try:
                with open(loose, "r", encoding="utf-8") as f:
                    return f.read().strip() or None, branch
            except OSError:
                return None, branch
        packed = os.path.join(git, "packed-refs")
        if os.path.isfile(packed):
            try:
                with open(packed, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("#") or line.startswith("^") or " " not in line:
                            continue
                        sha, name = line.split(" ", 1)
                        if name.strip() == branch_ref:
                            return sha.strip() or None, branch
            except OSError:
                pass
        return None, branch
    # detached HEAD: the file holds the sha directly.
    return (ref or None), None


def _runs_ignored(repo_root):
    """True when '.gitignore' at the repo root ignores the runs/ tree (read-only)."""
    gi = os.path.join(repo_root, ".gitignore")
    if not os.path.isfile(gi):
        return False
    try:
        with open(gi, "r", encoding="utf-8") as f:
            for line in f:
                t = line.strip()
                if t in ("runs/", "/runs/", "runs", "/runs"):
                    return True
    except OSError:
        return False
    return False


# ================================================================ loopback / host / port validation
def is_loopback_host(host):
    """True only for a provably loopback address. IP literals are checked directly; 'localhost' is
    resolved and every resolved address must be loopback; any other name is refused WITHOUT a DNS
    query (so no external lookup is ever performed here)."""
    if host is None:
        return False
    h = host.strip()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if not h:
        return False
    try:
        return ipaddress.ip_address(h.split("%", 1)[0]).is_loopback
    except ValueError:
        pass
    if h.lower() == "localhost":
        try:
            infos = socket.getaddrinfo(h, None)
        except socket.gaierror:
            return False
        if not infos:
            return False
        for info in infos:
            ip = info[4][0].split("%", 1)[0]
            try:
                if not ipaddress.ip_address(ip).is_loopback:
                    return False
            except ValueError:
                return False
        return True
    return False


def validate_host(host):
    if is_loopback_host(host):
        return host
    raise ConsoleError(400, "NON_LOOPBACK_BIND_REFUSED",
                       f"{host!r}: the console binds to a loopback address only "
                       "(127.0.0.1 / ::1 / localhost).")


def validate_port(port):
    try:
        p = int(port)
    except (TypeError, ValueError):
        raise ConsoleError(400, "INVALID_PORT", str(port))
    if p < 0 or p > 65535:
        raise ConsoleError(400, "INVALID_PORT", str(port))
    return p


# ================================================================ configuration
class Config:
    """Console configuration. `workspace_root` holds the phase 7.3-7.12 workspaces; `base_dir` is the
    console's own 7.13 workspace. Network seams (transport/resolver/allow_local) and env are threaded
    to the accepted 7.9/7.11/7.12 authorities so tests never touch the real Internet."""
    __slots__ = ("base_dir", "workspace_root", "env", "actor", "reference_date", "allow_local",
                 "transport", "resolver", "repo_root", "now_fn")

    def __init__(self, base_dir, workspace_root, *, env=None, actor="owner-console",
                 reference_date=None, allow_local=False, transport=None, resolver=None,
                 repo_root=None, now_fn=None):
        self.base_dir = os.path.abspath(base_dir)
        self.workspace_root = os.path.abspath(workspace_root)
        self.env = dict(env) if env is not None else dict(os.environ)
        self.actor = actor or "owner-console"
        self.reference_date = reference_date
        self.allow_local = bool(allow_local)
        self.transport = transport
        self.resolver = resolver
        self.repo_root = os.path.abspath(repo_root) if repo_root else _ROOT
        self.now_fn = now_fn

    def phase_dir(self, stage):
        return os.path.join(self.workspace_root, stage)

    @property
    def audit_dir(self):
        return os.path.join(self.base_dir, "audit")

    @property
    def audit_path(self):
        return os.path.join(self.audit_dir, AUDIT_FILE)

    # ---- accepted-authority config builders (read-only construction) ----------------------------
    def ops_config(self):
        return OPS.Config(self.phase_dir("7.8"), self.phase_dir("7.3"), self.phase_dir("7.4"),
                          self.phase_dir("7.5"), self.phase_dir("7.6"), self.phase_dir("7.7"),
                          reference_date=self.reference_date)

    def backup_config(self):
        return BACKUP.Config(self.phase_dir("7.9"), self.workspace_root, env=self.env,
                             repo_root=self.repo_root)

    def research_config(self):
        return RESEARCH.Config(self.phase_dir("7.10"), env=self.env, allow_local=self.allow_local)

    def watch_config(self):
        return WATCH.Config(self.phase_dir("7.11"), research_dir=self.phase_dir("7.10"),
                            env=self.env, transport=self.transport, resolver=self.resolver,
                            allow_local=self.allow_local)

    def notify_config(self):
        return NOTIFY.Config(self.phase_dir("7.12"), alert_dir=self.phase_dir("7.11"), env=self.env,
                             transport=self.transport, resolver=self.resolver,
                             allow_local=self.allow_local)


def ensure_workspace(base_dir):
    base = os.path.abspath(base_dir)
    for sub in _WORKSPACE_SUBDIRS:
        os.makedirs(os.path.join(base, sub), exist_ok=True)
    return base


# ================================================================ read models
def _dir_present(path):
    return os.path.isdir(path)


def build_analysis_section(config, *, now):
    """Analysis & Decisions — the accepted Phase 7.8 model over 7.3-7.7 (never recomputed here)."""
    if not _dir_present(config.phase_dir("7.3")):
        return {"status": MOD_UNAVAILABLE, "reason": "phase 7.3 workspace not present", "model": None}
    try:
        model = OPS.build_operations_model(config.ops_config(), now=config.now_fn or _now_utc)
    except Exception as e:   # noqa: BLE001 — degrade, never crash the whole console
        return {"status": MOD_BLOCKED, "reason": type(e).__name__, "model": None}
    readiness = model.get("readiness")
    if readiness == OPS.SOURCE_REQUIRED:
        status = MOD_UNAVAILABLE
    elif readiness in OPS._BLOCKING_READINESS:
        status = MOD_BLOCKED
    elif readiness == OPS.READY_PARTIAL:
        status = MOD_READY_PARTIAL
    elif readiness == OPS.READY_EMPTY:
        status = MOD_READY_EMPTY
    elif readiness == OPS.READY:
        status = MOD_READY
    else:
        status = MOD_UNAVAILABLE
    ov = model.get("overview") or {}
    counts = {
        "analyzed_rows": ov.get("analyzed_rows") or 0,
        "review_state_records": ov.get("review_state_records") or 0,
        "approved_reviews": ov.get("approved_reviews") or 0,
        "pending_decisions": ov.get("eligible_decisions") or 0,
        "pending_manual_actions": ov.get("pending_manual_check") or 0,
        "eligible_followups": ov.get("eligible_followups") or 0,
        "attention_items": ov.get("attention_item_count") or 0,
    }
    return {"status": status, "readiness": readiness, "model": model, "counts": counts,
            "reason": None}


def build_research_section(config, *, now):
    if not _dir_present(config.phase_dir("7.10")):
        return {"status": MOD_UNAVAILABLE, "runs": [], "counts": {"runs": 0}, "reason": None}
    try:
        rcfg = config.research_config()
        listing = RESEARCH.list_runs(rcfg)
    except Exception as e:   # noqa: BLE001
        return {"status": MOD_BLOCKED, "runs": [], "counts": {"runs": 0}, "reason": type(e).__name__}
    runs = []
    for r in listing.get("runs", []) or []:
        if not isinstance(r, dict):
            continue
        runs.append({
            "row_id": _row_id(view="research", entity_id=r.get("run_id"), content_hash=r.get("readiness")),
            "run_id": r.get("run_id"), "readiness": r.get("readiness"),
            "source_count": r.get("source_count"), "evidence_count": r.get("evidence_count"),
        })
    runs.sort(key=lambda r: _s(r.get("run_id")))
    status = MOD_READY if runs else MOD_READY_EMPTY
    return {"status": status, "readiness": listing.get("readiness"), "runs": runs,
            "counts": {"runs": len(runs)}, "reason": None}


def _research_run_detail(config, run_id):
    rcfg = config.research_config()
    manifest = RESEARCH._load_manifest(rcfg, run_id)
    sources = manifest.get("sources", []) if isinstance(manifest, dict) else []
    captures = []
    blocked = []
    for src in sources:
        for cap in (src.get("captures", []) if isinstance(src, dict) else []):
            row = {
                "row_id": _row_id(view="capture", entity_id=cap.get("capture_id"),
                                  content_hash=cap.get("content_sha256")),
                "capture_id": cap.get("capture_id"), "source_id": src.get("source_id"),
                "source_type": src.get("source_type"),
                "normalized_locator": cap.get("normalized_locator"),
                "capture_status": cap.get("capture_status"), "media_type": cap.get("media_type"),
                "http_status": cap.get("http_status"), "evidence_count": cap.get("evidence_count"),
                "redirect_lineage": cap.get("redirect_lineage", []),
                "robots_result": cap.get("robots_result"),
            }
            captures.append(row)
            if cap.get("capture_status") not in RESEARCH._SUCCESS_STATUSES:
                blocked.append(row)
    return {
        "run_id": manifest.get("run_id"), "readiness": manifest.get("readiness"),
        "plan_id": manifest.get("plan_id"),
        "capture_status_counts": manifest.get("capture_status_counts", {}),
        "source_count": manifest.get("source_count"), "capture_count": manifest.get("capture_count"),
        "evidence_count": manifest.get("evidence_count"),
        "captures": captures, "blocked_captures": blocked,
        "exports": _research_exports(rcfg, manifest.get("run_id")),
        "network_purpose_counters": manifest.get("network_purpose_counters", {}),
    }


def _research_exports(rcfg, run_id):
    out = []
    if not run_id:
        return out
    rep = os.path.join(rcfg.reports_dir, run_id)
    for name in ("research_snapshot.json", "research_evidence.tsv", "research_report.md"):
        if os.path.isfile(os.path.join(rep, name)):
            out.append(name)
    return out


def build_watchlist_section(config, *, now):
    if not _dir_present(config.phase_dir("7.11")):
        return {"status": MOD_UNAVAILABLE, "watchlists": [], "alerts": [],
                "counts": {"watchlists": 0, "due": 0, "open_alerts": 0}, "reason": None}
    try:
        wcfg = config.watch_config()
        wl_listing = WATCH.list_watchlists(wcfg)
    except Exception as e:   # noqa: BLE001
        return {"status": MOD_BLOCKED, "watchlists": [], "alerts": [],
                "counts": {"watchlists": 0, "due": 0, "open_alerts": 0}, "reason": type(e).__name__}
    ref_iso = _iso(now)
    rows = []
    due_count = 0
    alert_rows = []
    open_alerts = 0
    ack_alerts = 0
    dismissed_alerts = 0
    any_blocked = False
    for w in wl_listing.get("watchlists", []) or []:
        wid = w.get("watchlist_id")
        due_reason = None
        due = False
        next_due = None
        try:
            full = WATCH.load_watchlist(wcfg, wid)
            state = WATCH._load_state(wcfg, wid)
            dd = WATCH.compute_due(full["schedule"], full["timezone"],
                                   state.get("last_run_utc"), ref_iso)
            due = bool(dd.get("due"))
            due_reason = dd.get("reason")
            next_due = dd.get("next_due")
        except Exception:   # noqa: BLE001
            any_blocked = True
        if due:
            due_count += 1
        rows.append({
            "row_id": _row_id(view="watchlist", entity_id=wid, content_hash=w.get("schedule_type")),
            "watchlist_id": wid, "name": w.get("name"), "enabled": w.get("enabled"),
            "schedule_type": w.get("schedule_type"), "source_count": w.get("source_count"),
            "rule_count": w.get("rule_count"), "due": due, "due_reason": due_reason,
            "next_due": next_due,
        })
        try:
            al = WATCH.list_alerts(wcfg, wid)
            counts = al.get("status_counts", {}) or {}
            open_alerts += counts.get(WATCH.ALERT_OPEN, 0)
            ack_alerts += counts.get(WATCH.ALERT_ACKNOWLEDGED, 0)
            dismissed_alerts += counts.get(WATCH.ALERT_DISMISSED, 0)
            for a in al.get("alerts", []) or []:
                lineage = a.get("evidence_lineage", {}) or {}
                alert_rows.append({
                    "row_id": _row_id(view="alert", entity_id=a.get("alert_id"),
                                      content_hash=a.get("change_id")),
                    "alert_id": a.get("alert_id"), "watchlist_id": a.get("watchlist_id"),
                    "severity": a.get("severity"), "label": a.get("label"),
                    "status": a.get("status"), "reason": a.get("reason"),
                    "change_type": a.get("change_type"), "field_path": a.get("field_path"),
                    "previous_value": a.get("previous_value"), "current_value": a.get("current_value"),
                    "previous_capture_id": lineage.get("previous_capture_id"),
                    "current_capture_id": lineage.get("current_capture_id"),
                })
        except Exception:   # noqa: BLE001
            any_blocked = True
    rows.sort(key=lambda r: _s(r.get("watchlist_id")))
    alert_rows.sort(key=lambda r: _s(r.get("alert_id")))
    if any_blocked:
        status = MOD_BLOCKED
    elif rows:
        status = MOD_READY
    else:
        status = MOD_READY_EMPTY
    return {"status": status, "watchlists": rows, "alerts": alert_rows,
            "counts": {"watchlists": len(rows), "due": due_count, "open_alerts": open_alerts,
                       "acknowledged_alerts": ack_alerts, "dismissed_alerts": dismissed_alerts},
            "reason": None}


def build_notification_section(config, *, now):
    if not _dir_present(config.phase_dir("7.12")):
        return {"status": MOD_UNAVAILABLE, "routes": [], "batches": [], "deliveries": [],
                "counts": {"routes": 0, "batches": 0, "deliveries": 0, "sent": 0, "failed": 0,
                           "unknown": 0}, "reason": None}
    try:
        ncfg = config.notify_config()
        routes = NOTIFY.list_routes(ncfg)
        batches = NOTIFY.list_batches(ncfg)
        deliveries = NOTIFY.list_deliveries(ncfg)
    except Exception as e:   # noqa: BLE001
        return {"status": MOD_BLOCKED, "routes": [], "batches": [], "deliveries": [],
                "counts": {"routes": 0, "batches": 0, "deliveries": 0, "sent": 0, "failed": 0,
                           "unknown": 0}, "reason": type(e).__name__}
    route_rows = []
    for r in routes.get("routes", []) or []:
        route_rows.append({
            "row_id": _row_id(view="route", entity_id=r.get("route_id"),
                              content_hash=r.get("approval_status")),
            "route_id": r.get("route_id"), "name": r.get("name"), "enabled": r.get("enabled"),
            "channel": r.get("channel"), "payload_format": r.get("payload_format"),
            "digest_mode": r.get("digest_mode"), "approval_status": r.get("approval_status"),
        })
    batch_rows = []
    for b in batches.get("batches", []) or []:
        batch_rows.append({
            "row_id": _row_id(view="batch", entity_id=b.get("batch_id"),
                              content_hash=b.get("readiness")),
            "batch_id": b.get("batch_id"), "route_id": b.get("route_id"),
            "alert_count": b.get("alert_count"), "readiness": b.get("readiness"),
            "digest_period_id": b.get("digest_period_id"),
        })
    delivery_rows = []
    sent = failed = unknown = 0
    for d in deliveries.get("deliveries", []) or []:
        st = d.get("delivery_state")
        if st == NOTIFY.DS_SENT:
            sent += 1
        elif st == NOTIFY.DS_FAILED:
            failed += 1
        elif st == NOTIFY.DS_UNKNOWN:
            unknown += 1
        delivery_rows.append({
            "row_id": _row_id(view="delivery", entity_id=d.get("delivery_id"),
                              content_hash=d.get("delivery_state")),
            "delivery_id": d.get("delivery_id"), "route_id": d.get("route_id"),
            "batch_id": d.get("batch_id"), "delivery_state": st,
            "provider_status": d.get("provider_status"), "attempt_sequence": d.get("attempt_sequence"),
        })
    route_rows.sort(key=lambda r: _s(r.get("route_id")))
    batch_rows.sort(key=lambda r: _s(r.get("batch_id")))
    delivery_rows.sort(key=lambda r: _s(r.get("delivery_id")))
    status = MOD_READY if (route_rows or batch_rows or delivery_rows) else MOD_READY_EMPTY
    return {"status": status, "routes": route_rows, "batches": batch_rows, "deliveries": delivery_rows,
            "counts": {"routes": len(route_rows), "batches": len(batch_rows),
                       "deliveries": len(delivery_rows), "sent": sent, "failed": failed,
                       "unknown": unknown}, "reason": None}


def build_backup_section(config, *, now):
    if not _dir_present(config.phase_dir("7.9")):
        return {"status": MOD_UNAVAILABLE, "snapshots": [], "update_check": None,
                "recovery_plans": [], "counts": {"snapshots": 0}, "reason": None}
    try:
        bcfg = config.backup_config()
        listing = BACKUP.list_snapshots(bcfg)
    except Exception as e:   # noqa: BLE001
        return {"status": MOD_BLOCKED, "snapshots": [], "update_check": None,
                "recovery_plans": [], "counts": {"snapshots": 0}, "reason": type(e).__name__}
    snaps = []
    for snp in listing.get("snapshots", []) or []:
        snaps.append({
            "row_id": _row_id(view="snapshot", entity_id=snp.get("snapshot_id"),
                              content_hash=str(snp.get("total_bytes"))),
            "snapshot_id": snp.get("snapshot_id"), "file_count": snp.get("file_count"),
            "total_bytes": snp.get("total_bytes"), "encrypted": snp.get("encrypted"),
        })
    snaps.sort(key=lambda r: _s(r.get("snapshot_id")))
    update_check = None
    uc_path = os.path.join(bcfg.validation_dir, "update_check.json")
    if os.path.isfile(uc_path):
        try:
            doc = _load_json_file(uc_path, "update_check.json")
            update_check = {
                "remote": doc.get("remote"), "branch": doc.get("branch"),
                "local_head": doc.get("local_head"), "remote_branch_head": doc.get("remote_branch_head"),
                "update_available": doc.get("update_available"), "readiness": doc.get("readiness"),
                "accepted_tags_available": doc.get("accepted_tags_available", []),
            }
        except ConsoleError:
            update_check = None
    plans = []
    plan_dir = bcfg.restore_plans_dir
    if os.path.isdir(plan_dir):
        for name in sorted(os.listdir(plan_dir)):
            if name.endswith(".json"):
                plans.append({"snapshot_id": name[:-5], "plan_file": name})
    status = MOD_READY if snaps else MOD_READY_EMPTY
    return {"status": status, "snapshots": snaps, "update_check": update_check,
            "recovery_plans": plans,
            "counts": {"snapshots": len(snaps), "recovery_plans": len(plans),
                       "update_available": bool(update_check and update_check.get("update_available"))},
            "reason": None}


def _common_git_dir(repo_root):
    """The git directory that actually holds refs/tags and packed-refs.

    In an ordinary checkout, <repo_root>/.git is that directory directly. In a `git worktree`
    checkout (this project uses worktrees routinely for review/testing -- see this repo's own
    history), <repo_root>/.git is instead a FILE containing "gitdir: <path>" pointing at a
    per-worktree private directory that holds only THIS worktree's HEAD/index; the actual shared
    refs live in the directory named by that private directory's own "commondir" file (a path
    relative to it, typically "../.."). Getting this wrong doesn't just miss tags in a test
    fixture -- it makes accepted_tag_prefix checks silently see zero tags for any stage launched
    from a worktree, misreporting every accepted stage as NOT_ACCEPTED."""
    dotgit = os.path.join(repo_root, ".git")
    if os.path.isdir(dotgit):
        return dotgit
    if not os.path.isfile(dotgit):
        return None
    try:
        with open(dotgit, "r", encoding="utf-8") as f:
            content = f.read().strip()
    except OSError:
        return None
    if not content.startswith("gitdir:"):
        return None
    worktree_gitdir = content[len("gitdir:"):].strip()
    if not os.path.isabs(worktree_gitdir):
        worktree_gitdir = os.path.join(repo_root, worktree_gitdir)
    commondir_file = os.path.join(worktree_gitdir, "commondir")
    if os.path.isfile(commondir_file):
        try:
            with open(commondir_file, "r", encoding="utf-8") as f:
                rel = f.read().strip()
        except OSError:
            return worktree_gitdir
        return os.path.normpath(os.path.join(worktree_gitdir, rel))
    return worktree_gitdir


def _repo_tags(repo_root):
    """Every git tag name, by reading ref FILES only (never a subprocess) -- the same discipline
    _repo_commit already uses, and the console's own zero-subprocess counter depends on staying
    true. Loose tags live one-file-per-tag under <common-git-dir>/refs/tags/; this repo's 60+ tags
    mostly live packed in <common-git-dir>/packed-refs as "<sha> refs/tags/<name>" lines alongside
    branch entries, which must be excluded by their differing refs/heads/ prefix."""
    git = _common_git_dir(repo_root)
    if git is None:
        return []
    tags = []
    loose_dir = os.path.join(git, "refs", "tags")
    if os.path.isdir(loose_dir):
        for root, _dirs, files in os.walk(loose_dir):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), loose_dir)
                tags.append(rel.replace(os.sep, "/"))
    packed = os.path.join(git, "packed-refs")
    if os.path.isfile(packed):
        try:
            with open(packed, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("#") or line.startswith("^") or " " not in line:
                        continue
                    _sha, name = line.split(" ", 1)
                    name = name.strip()
                    if name.startswith("refs/tags/"):
                        tags.append(name[len("refs/tags/"):])
        except OSError:
            pass
    return sorted(set(tags))


TRUST_TRUSTED = "TRUSTED"
TRUST_UNVERIFIED = "UNVERIFIED"
TRUST_HISTORICAL = "HISTORICAL"

# Explicit, deliberate owner quarantine decisions (DASHBOARD-V1-SPEC.md Section 7 /
# HANDOFF-2026-08-04 Section 5) -- never auto-derived. runs/T2 drifted from its accepted Phase 6F
# package across stages 6A-6E; the accepted bytes are unrecoverable (never committed, runs/
# gitignored, no backup), so no LIVE check can ever detect this drift after the fact --
# verify_phase6f_artifacts would report today's (already-drifted) files as internally consistent,
# because the promoted package's own index was itself built from those same drifted files. Decided
# 2026-08-04. Matched by product-root basename, not full path, so this travels across machines.
QUARANTINED_PRODUCT_ROOTS = frozenset({"T2"})


def _workspace_trust_state(product_root):
    """DASHBOARD-V1-SPEC.md Section 7 -- exactly one of TRUSTED / UNVERIFIED / HISTORICAL for the
    PRODUCT workspace. Reuses production.seller_central_package.verify_phase6f_artifacts (the
    accepted Phase 6F authority) rather than inventing a second lineage-verification mechanism;
    the only new logic here is QUARANTINED_PRODUCT_ROOTS, which -- deliberately -- a live check
    cannot replace (see its own comment)."""
    basename = os.path.basename(os.path.normpath(product_root))
    if basename in QUARANTINED_PRODUCT_ROOTS:
        return {"state": TRUST_HISTORICAL,
                "reason": "Quarantined -- readable as history, never a basis for a new decision."}
    package_root = SCP.package_root_dir(product_root)
    if not os.path.isdir(package_root):
        return {"state": TRUST_UNVERIFIED, "reason": "No accepted package yet to compare against."}
    report = SCP.verify_phase6f_artifacts(package_root, workspace_dir=product_root)
    if report.get("blockers"):
        return {"state": TRUST_UNVERIFIED,
                "reason": "An accepted package exists but no longer verifies against this workspace."}
    return {"state": TRUST_TRUSTED, "reason": "Lineage verified against the accepted package."}


def build_workflow_section(config, *, now):
    """Dashboard V1 Workflow -- the 13-stage pipeline overview (DASHBOARD-V1-SPEC.md). Read-only
    and purely additive: no existing section, the console readiness banner, module list, or any
    action changes because this section exists -- it is a new top-level model field only.

    Stage state comes from production.phase7_workflow_stage_model, NOT production.pipeline_status
    (Branch A -- unmerged, HOLD pending independent re-audit); see that module's own docstring for
    why the duplication is deliberate and disclosed rather than an accident.

    The stage table's paths are relative to the PRODUCT workspace root (holds both phase6/ and
    phase7/ as siblings), which is workspace_root's own parent -- config.workspace_root is scoped
    to "the phase 7.3-7.12 workspaces" (DEFAULT_WORKSPACE_ROOT = runs/T2/phase7), one level below.
    """
    product_root = os.path.dirname(config.workspace_root.rstrip(os.sep))
    if not os.path.isdir(product_root):
        return {"status": MOD_UNAVAILABLE, "reason": "product workspace not present",
                "product_root": product_root, "stages": [], "trust": None, "product_truth": None,
                "counts": {"modeled": 0, "ready": 0, "blocked": 0}}
    trust = _workspace_trust_state(product_root)
    tags = _repo_tags(config.repo_root)
    table = WSM.workflow_stage_table()
    # Product Truth (Phase 6A) -- a TRACKED PREREQUISITE, deliberately not one of the 13 numbered
    # stages (owner decision, DASHBOARD-V1-SPEC.md Section 13 item 4). Its real consumer is Stage
    # 8 (confirmed by reading listing/keyword_allocation_planner.py and by actually running its
    # command against a Product-Truth-less workspace: it raises the same unhandled
    # Phase6ADependencyError Stage 9 was already known to raise). Forcing Stage 8's state via
    # WSM.resolve_all's state_overrides -- rather than adding a numeric blocking_stage_ids entry,
    # since Product Truth is not in WSM.workflow_stage_table() and has no numeric stage_id to
    # reference -- BEFORE resolve_all runs, not by patching its result afterward: Stage 9 blocks on
    # Stage 8 via its existing blocking_stage_ids=(8,), but that check only sees the forced BLOCKED
    # if Stage 8's OWN evidence is overridden while Stage 9 is still being resolved. A prior version
    # of this patched results[8] after the fact and left Stage 9+ resolved against Stage 8's
    # un-overridden state -- if Stage 8's own artifacts already existed from an earlier successful
    # run, Stage 9 (and anything chained beyond it) reported READY while Stage 8 showed BLOCKED on
    # the same screen. Reproduced directly before this fix; see the regression test added alongside.
    product_truth_state = WSM.derive_product_truth_state(product_root)
    product_truth_blocks_stage_8 = product_truth_state != WSM.READY
    overrides = {8: WSM.BLOCKED} if product_truth_blocks_stage_8 else None
    results = WSM.resolve_all(table, product_root, tags=tags, state_overrides=overrides)
    stages = []
    for spec in table:
        r = results[spec.stage_id]
        blocked_by = ([up for up in spec.blocking_stage_ids
                      if results.get(up, {}).get("state") != WSM.READY]
                     if r["state"] == WSM.BLOCKED else [])
        stages.append({
            "stage_id": spec.stage_id, "label": spec.name, "group": spec.group, "modeled": True,
            "state": r["state"], "components": r["components"], "blocked_by": blocked_by,
            "blocked_by_product_truth": spec.stage_id == 8 and product_truth_blocks_stage_8,
            "command": spec.command, "acceptance_required": bool(spec.accepted_tag_prefix),
            "informational_reason": None,
        })
    for nt in WSM.NOT_TRACKED_STAGES:
        stages.append({
            "stage_id": nt["stage_id"], "label": nt["name"], "group": nt["group"],
            "modeled": False, "state": None, "components": {}, "blocked_by": [],
            "command": None, "acceptance_required": False,
            "informational_reason": nt["note"],
        })
    stages.sort(key=lambda s: s["stage_id"])
    ready = sum(1 for s in stages if s["state"] == WSM.READY)
    blocked = sum(1 for s in stages if s["state"] == WSM.BLOCKED)
    status = (MOD_READY if ready == len(table) else
             MOD_READY_PARTIAL if ready else MOD_READY_EMPTY)
    # "One primary next action" is a backend fact, not a client-side inference: the first modeled
    # stage (in the table's own dependency order) that is not yet READY. The frontend must only
    # read this field, never walk `stages` itself to guess -- the same separation NEXT.build_
    # next_action already keeps for the console's overall next-action panel.
    primary_next_stage_id = next((spec.stage_id for spec in table
                                 if results[spec.stage_id]["state"] != WSM.READY), None)
    product_truth = {
        "label": WSM.PRODUCT_TRUTH_LABEL, "group": WSM.PRODUCT_TRUTH_GROUP,
        "state": product_truth_state, "command": WSM.PRODUCT_TRUTH_COMMAND,
        "staff_note": WSM.PRODUCT_TRUTH_STAFF_NOTE,
    }
    return {"status": status, "reason": None, "product_root": product_root, "stages": stages,
            "trust": trust, "product_truth": product_truth,
            "primary_next_stage_id": primary_next_stage_id,
            "counts": {"modeled": len(table), "ready": ready, "blocked": blocked}}


def build_system_section(config, *, now, audit_state):
    phase_dirs = {}
    for stage in ("7.3", "7.4", "7.5", "7.6", "7.7", "7.8", "7.9", "7.10", "7.11", "7.12"):
        phase_dirs[stage] = _dir_present(config.phase_dir(stage))
    commit, branch = _repo_commit(config.repo_root)
    # connectivity boundary — assemble the checked hosts from fragments so no endpoint literal appears
    # on any source line; the classification proves each seller-account class is refused.
    _az = "ama" + "zon"
    seller_host = "seller" + "central." + _az + ".com"
    api_host = "selling" + "partner" + "api." + _az + ".com"
    ads_host = "advertising-" + "api." + _az + ".com"
    boundary = {
        "seller_central_classification": NP.classify_destination(seller_host),
        "seller_api_classification": NP.classify_destination(api_host),
        "advertising_api_classification": NP.classify_destination(ads_host),
        "seller_central_blocked": NP.classify_destination(seller_host) in NP._AMAZON_ACCOUNT_CLASSES,
        "seller_api_blocked": NP.classify_destination(api_host) in NP._AMAZON_ACCOUNT_CLASSES,
        "advertising_api_blocked": NP.classify_destination(ads_host) in NP._AMAZON_ACCOUNT_CLASSES,
    }
    diag = [ev.to_dict() for ev in DIAG.recent_events(10)]
    return {
        "phase_directories": phase_dirs,
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "repository_commit": commit,
        "repository_branch": branch,
        "runs_ignored": _runs_ignored(config.repo_root),
        "network_policy_status": "LOOPBACK_UI_ONLY; connected operations only via accepted authorities",
        "connectivity_boundary": boundary,
        "audit_chain": audit_state,
        "recent_errors": diag,
        "seller_central_counters": dict(SELLER_CENTRAL_COUNTERS),
        "this_console_never": dict(THIS_CONSOLE_NEVER),
    }


# ================================================================ freshness
def _mtime_iso(path):
    try:
        return _iso(_dt.datetime.fromtimestamp(os.path.getmtime(path), _dt.timezone.utc))
    except OSError:
        return None


def build_freshness(config, *, now):
    """Data-freshness display only — reports the newest artifact timestamp per phase for the owner.
    It is a display signal; records are NEVER selected by mtime anywhere in the console."""
    out = {}
    now_ts = now.timestamp()
    for stage in ("7.3", "7.5", "7.6", "7.7", "7.9", "7.10", "7.11", "7.12"):
        d = config.phase_dir(stage)
        newest = None
        newest_ts = None
        if os.path.isdir(d):
            for root, _dn, files in os.walk(d):
                if "__pycache__" in root:
                    continue
                for f in files:
                    p = os.path.join(root, f)
                    try:
                        ts = os.path.getmtime(p)
                    except OSError:
                        continue
                    if newest_ts is None or ts > newest_ts:
                        newest_ts = ts
                        newest = p
        stale = bool(newest_ts is not None and (now_ts - newest_ts) > DATA_STALE_AFTER_SECONDS)
        out[stage] = {"present": os.path.isdir(d),
                      "latest": _iso(_dt.datetime.fromtimestamp(newest_ts, _dt.timezone.utc))
                      if newest_ts else None,
                      "stale": stale}
    return out


# ================================================================ overall readiness + overview
def resolve_console_readiness(sections, audit_state):
    if not audit_state.get("ok", True):
        return AUDIT_STATE_BLOCKED
    analysis = sections["analysis"]
    if analysis["status"] == MOD_BLOCKED:
        return CONSOLE_BLOCKED
    if analysis["status"] == MOD_UNAVAILABLE:
        return CONSOLE_REQUIRED
    optional = (sections["research"], sections["watchlists"], sections["notifications"],
                sections["backup"])
    partial = (analysis["status"] == MOD_READY_PARTIAL
               or any(s["status"] in (MOD_UNAVAILABLE, MOD_BLOCKED, MOD_READY_PARTIAL) for s in optional))
    populated = (
        (analysis.get("counts", {}).get("analyzed_rows", 0) or 0) > 0
        or sections["research"]["counts"].get("runs", 0)
        or sections["watchlists"]["counts"].get("watchlists", 0)
        or sections["notifications"]["counts"].get("routes", 0)
        or sections["backup"]["counts"].get("snapshots", 0)
    )
    if partial:
        return CONSOLE_READY_PARTIAL
    if populated:
        return CONSOLE_READY
    return CONSOLE_READY_EMPTY


def build_overview(sections, readiness, freshness):
    a = sections["analysis"].get("counts", {})
    w = sections["watchlists"]["counts"]
    n = sections["notifications"]["counts"]
    b = sections["backup"]["counts"]
    r = sections["research"]["counts"]
    blocked = [name for name, sec in sections.items() if sec["status"] == MOD_BLOCKED]
    unavailable = [name for name, sec in sections.items() if sec["status"] == MOD_UNAVAILABLE]
    stale = [stage for stage, f in freshness.items() if f.get("stale")]
    return {
        "console_readiness": readiness,
        "owner_label": OWNER_LABELS.get(readiness, readiness),
        "module_status": {name: sec["status"] for name, sec in sections.items()},
        # plain owner names for the same modules, so no internal key is ever the visible label.
        "module_labels": dict(MODULE_OWNER_LABELS),
        "blocked_modules": blocked,
        "unavailable_modules": unavailable,
        "pending_decisions": a.get("pending_decisions", 0),
        "pending_manual_actions": a.get("pending_manual_actions", 0),
        "due_followups": a.get("eligible_followups", 0),
        "attention_items": a.get("attention_items", 0),
        "open_alerts": w.get("open_alerts", 0),
        "acknowledged_alerts": w.get("acknowledged_alerts", 0),
        "due_watchlists": w.get("due", 0),
        "notification_sent": n.get("sent", 0),
        "notification_failed": n.get("failed", 0),
        "notification_unknown": n.get("unknown", 0),
        "research_runs": r.get("runs", 0),
        "backup_snapshots": b.get("snapshots", 0),
        "update_available": b.get("update_available", False),
        "stale_data_phases": stale,
        "seller_central_counters": dict(SELLER_CENTRAL_COUNTERS),
    }


def build_modules(sections):
    order = ["analysis", "research", "watchlists", "notifications", "backup"]
    titles = {"analysis": "Analysis & Decisions", "research": "Public Research",
              "watchlists": "Watchlists & Alerts", "notifications": "Notifications",
              "backup": "Backup & Recovery"}
    required = {"analysis"}
    out = []
    for name in order:
        sec = sections[name]
        out.append({
            "module": name, "title": titles[name], "status": sec["status"],
            "owner_label": OWNER_LABELS.get(sec["status"], sec["status"]),
            "required": name in required, "counts": sec.get("counts", {}),
            "reason": sec.get("reason"),
        })
    return out


def build_console_model(config, *, now=None, audit=None):
    """Assemble the complete, read-only console model. Never mutates any upstream artifact."""
    now = now or (config.now_fn() if config.now_fn else _now_utc())
    audit_state = (audit.state() if audit is not None
                   else verify_audit_chain(config.audit_path))
    sections = {
        "analysis": build_analysis_section(config, now=now),
        "research": build_research_section(config, now=now),
        "watchlists": build_watchlist_section(config, now=now),
        "notifications": build_notification_section(config, now=now),
        "backup": build_backup_section(config, now=now),
        # Deliberately NOT read by resolve_console_readiness / build_overview / build_modules --
        # those enumerate the five sections above by name and predate this one. Wiring "workflow"
        # into the console readiness banner or the module-card list is a UI-scope decision for
        # Step 2D/2C, not a silent side effect of this section existing. See build_workflow_section.
        "workflow": build_workflow_section(config, now=now),
    }
    freshness = build_freshness(config, now=now)
    readiness = resolve_console_readiness(sections, audit_state)
    system = build_system_section(config, now=now, audit_state=audit_state)
    overview = build_overview(sections, readiness, freshness)
    model = {
        "schema_version": MODEL_SCHEMA, "stage_id": STAGE_ID, "stage_name": STAGE_NAME,
        "readiness": readiness, "overview": overview, "modules": build_modules(sections),
        "sections": sections, "freshness": freshness, "system": system,
        "source_authorities": SOURCE_AUTHORITIES, "disclaimer": list(DISCLAIMER_LINES),
        "seller_central_counters": dict(SELLER_CENTRAL_COUNTERS),
    }
    # Phase 7.14 owner guidance: a presentation-only read model over the model just assembled. It
    # adds no authority, reads nothing further, and never changes a value above.
    model["next_action"] = NEXT.build_next_action(model, generated_at=_iso(now))
    # one owner-facing word for "can I use this right now?", derived only from that guidance.
    model["overview"]["usability_readiness"] = NEXT.usability_readiness(model["next_action"])
    model["overview"]["usability_label"] = NEXT.USABILITY_LABELS[
        model["overview"]["usability_readiness"]]
    return model


SOURCE_AUTHORITIES = {
    "analysis": "production.phase7_owner_operations_dashboard (Phase 7.3-7.8)",
    "research": "production.phase7_connected_public_research (Phase 7.10)",
    "watchlists": "production.phase7_connected_research_watchlists (Phase 7.11)",
    "notifications": "production.phase7_owner_notification_delivery (Phase 7.12)",
    "backup": "production.phase7_connected_backup_recovery (Phase 7.9)",
    "network_policy": "core.network_policy",
    "next_action": "production.phase7_owner_next_action (Phase 7.14, presentation only)",
    "workflow": "production.phase7_workflow_stage_model (DASHBOARD-V1-SPEC.md, not Branch A)",
}


# ================================================================ bounded in-memory read-model cache
class ReadModelCache:
    """A bounded, short-lived read-model cache. Each entry records the source-state hash so a caller
    can see whether the served data is fresh or stale. Never persisted; excluded from any identity."""

    def __init__(self, *, max_age=CACHE_MAX_AGE_SECONDS, clock=time.monotonic):
        self._entries = {}
        self._max_age = max_age
        self._clock = clock
        self._lock = RLock()

    def get(self, key):
        with self._lock:
            e = self._entries.get(key)
            if e is None:
                return None
            age = self._clock() - e["built_at"]
            return {"value": e["value"], "state_hash": e["state_hash"], "age": age,
                    "stale": age > self._max_age}

    def put(self, key, value, state_hash):
        with self._lock:
            self._entries[key] = {"value": value, "state_hash": state_hash,
                                  "built_at": self._clock()}

    def invalidate(self, key=None):
        with self._lock:
            if key is None:
                self._entries.clear()
            else:
                self._entries.pop(key, None)

    def __len__(self):
        with self._lock:
            return len(self._entries)


# ================================================================ append-only audit chain
def _audit_identity(fields):
    """The immutable identity of an audit event — operational timestamps are deliberately excluded."""
    return {
        "kind": "phase7-13-console-audit-event", "schema_version": AUDIT_SCHEMA,
        "request_id": fields["request_id"], "actor": fields["actor"],
        "session_fingerprint": fields["session_fingerprint"], "action": fields["action"],
        "target_ids": fields["target_ids"], "param_hash": fields["param_hash"],
        "authority": fields["authority"], "preparation_result": fields["preparation_result"],
        "execution_result": fields["execution_result"], "upstream_result_id": fields["upstream_result_id"],
        "readiness": fields["readiness"], "policy_result": fields["policy_result"],
        "previous_event_hash": fields["previous_event_hash"],
    }


def _read_audit_lines(path):
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(line)
    return out


def verify_audit_chain(path):
    """Re-derive every event hash + the running aggregate, and prove the prev-hash links are intact.
    Detects tampering, deletion, reordering, and truncation. Returns a state dict (never raises)."""
    lines = _read_audit_lines(path)
    prev_event = ""
    prev_agg = ""
    count = 0
    for idx, raw in enumerate(lines):
        try:
            ev = json.loads(raw)
        except ValueError:
            return {"ok": False, "state": AUDIT_STATE_BLOCKED, "reason": "MALFORMED_EVENT",
                    "index": idx, "event_count": count, "aggregate_hash": prev_agg}
        if not isinstance(ev, dict):
            return {"ok": False, "state": AUDIT_STATE_BLOCKED, "reason": "MALFORMED_EVENT",
                    "index": idx, "event_count": count, "aggregate_hash": prev_agg}
        if ev.get("previous_event_hash") != prev_event:
            return {"ok": False, "state": AUDIT_STATE_BLOCKED, "reason": "CHAIN_LINK_BROKEN",
                    "index": idx, "event_count": count, "aggregate_hash": prev_agg}
        try:
            recomputed = content_sha256(_audit_identity(ev))
        except KeyError:
            return {"ok": False, "state": AUDIT_STATE_BLOCKED, "reason": "MALFORMED_EVENT",
                    "index": idx, "event_count": count, "aggregate_hash": prev_agg}
        if recomputed != ev.get("event_hash"):
            return {"ok": False, "state": AUDIT_STATE_BLOCKED, "reason": "EVENT_HASH_MISMATCH",
                    "index": idx, "event_count": count, "aggregate_hash": prev_agg}
        agg = content_sha256({"previous_aggregate": prev_agg, "event_hash": recomputed})
        if agg != ev.get("aggregate_hash"):
            return {"ok": False, "state": AUDIT_STATE_BLOCKED, "reason": "AGGREGATE_MISMATCH",
                    "index": idx, "event_count": count, "aggregate_hash": prev_agg}
        prev_event = recomputed
        prev_agg = agg
        count += 1
    return {"ok": True, "state": "OK", "reason": None, "event_count": count,
            "aggregate_hash": prev_agg, "head_event_hash": prev_event}


class AuditLog:
    """Append-only, hash-chained orchestration audit trail under 7.13/audit/. Thread-safe. Never a
    duplicate business authority — it only records what the console orchestrated."""

    def __init__(self, path):
        self.path = path
        self._lock = RLock()

    def state(self):
        with self._lock:
            return verify_audit_chain(self.path)

    def append(self, fields, *, now):
        """Append one event. Callers MUST verify the chain first and refuse to mutate on corruption;
        this method assumes a good chain and links onto the current head."""
        with self._lock:
            st = verify_audit_chain(self.path)
            if not st["ok"]:
                raise ConsoleError(409, "AUDIT_STATE_BLOCKED", st["reason"],
                                   readiness=AUDIT_STATE_BLOCKED)
            prev_event = st.get("head_event_hash", "")
            prev_agg = st.get("aggregate_hash", "")
            record = dict(fields)
            record["previous_event_hash"] = prev_event
            event_hash = content_sha256(_audit_identity(record))
            aggregate = content_sha256({"previous_aggregate": prev_agg, "event_hash": event_hash})
            record["schema_version"] = AUDIT_SCHEMA
            record["event_hash"] = event_hash
            record["aggregate_hash"] = aggregate
            record["console_event_id"] = "ce-" + event_hash[:24]
            record["recorded_at"] = _iso(now)   # operational only, outside identity
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            line = json.dumps(record, ensure_ascii=False, sort_keys=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
            return record

    def events(self):
        rows = []
        for raw in _read_audit_lines(self.path):
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            if isinstance(ev, dict):
                rows.append(ev)
        return rows


# ================================================================ ephemeral session + CSRF
class SessionManager:
    """Ephemeral, in-memory local console session. A random server secret is generated at startup and
    never persisted. Sessions live only in memory and are invalidated when the server restarts."""

    def __init__(self, *, ttl=SESSION_TTL_SECONDS, clock=None):
        self._sessions = {}
        self._secret = secrets.token_bytes(32)
        self._ttl = ttl
        self._clock = clock or (lambda: time.time())
        self._lock = RLock()

    def create(self):
        with self._lock:
            sid = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(32)
            self._sessions[sid] = {"csrf": csrf, "created": self._clock()}
            return sid, csrf

    def _live(self, sid):
        s = self._sessions.get(sid)
        if s is None:
            return None
        if (self._clock() - s["created"]) > self._ttl:
            self._sessions.pop(sid, None)
            return None
        return s

    def csrf_for(self, sid):
        with self._lock:
            s = self._live(sid)
            return s["csrf"] if s else None

    def valid(self, sid):
        with self._lock:
            return self._live(sid) is not None

    def check_csrf(self, sid, token):
        with self._lock:
            s = self._live(sid)
            if s is None or not token:
                return False
            return hmac.compare_digest(s["csrf"], token)

    def fingerprint(self, sid):
        # A stable, secret-free session fingerprint (never contains the session id or the secret).
        return hashlib.sha256(("fp:" + _s(sid)).encode("utf-8") + self._secret).hexdigest()[:16]

    def invalidate_all(self):
        with self._lock:
            self._sessions.clear()


# ================================================================ action allowlist
class ActionSpec:
    __slots__ = ("name", "authority", "effect", "network_use", "local_state_change",
                 "upstream_state_change", "requires_confirmation", "phrase_prefix", "target_param")

    def __init__(self, name, *, authority, effect, network_use="NONE", local_state_change="NONE",
                 upstream_state_change="NONE", requires_confirmation=False, phrase_prefix=None,
                 target_param=None):
        self.name = name
        self.authority = authority
        self.effect = effect
        self.network_use = network_use
        self.local_state_change = local_state_change
        self.upstream_state_change = upstream_state_change
        self.requires_confirmation = requires_confirmation
        self.phrase_prefix = phrase_prefix
        self.target_param = target_param


ACTIONS = {s.name: s for s in [
    ActionSpec("refresh-overview", authority="console",
               effect="Invalidate the in-memory read-model cache and rebuild the overview.",
               local_state_change="cache_only"),
    ActionSpec("export-overview", authority="console",
               effect="Write the consolidated owner-console snapshot exports under 7.13/exports.",
               local_state_change="7.13_exports"),
    ActionSpec("verify-system-state", authority="console",
               effect="Verify the console audit chain and integrated-module integrity; write a 7.13 validation record.",
               local_state_change="7.13_validation"),
    ActionSpec("run-watchlist", authority="phase7.11", target_param="watchlist_id",
               effect="Run one accepted Phase 7.11 watchlist (fetches public sources through Phase 7.10).",
               network_use="PUBLIC_RESEARCH_VIA_7.10", upstream_state_change="phase7.11",
               requires_confirmation=True, phrase_prefix="RUN"),
    ActionSpec("acknowledge-alert", authority="phase7.11", target_param="alert_id",
               effect="Acknowledge one accepted Phase 7.11 owner alert.",
               upstream_state_change="phase7.11", requires_confirmation=True, phrase_prefix="ACKNOWLEDGE"),
    ActionSpec("dismiss-alert", authority="phase7.11", target_param="alert_id",
               effect="Dismiss one accepted Phase 7.11 owner alert.",
               upstream_state_change="phase7.11", requires_confirmation=True, phrase_prefix="DISMISS"),
    ActionSpec("reopen-alert", authority="phase7.11", target_param="alert_id",
               effect="Reopen one accepted Phase 7.11 owner alert.",
               upstream_state_change="phase7.11", requires_confirmation=True, phrase_prefix="REOPEN"),
    ActionSpec("preview-notification", authority="phase7.12", target_param="route_id",
               effect="Render a Phase 7.12 notification preview for one route (no network, no send)."),
    ActionSpec("build-notification-batch", authority="phase7.12", target_param="route_id",
               effect="Build a Phase 7.12 notification batch and write it to the local outbox (no network).",
               upstream_state_change="phase7.12", requires_confirmation=True, phrase_prefix="BUILD"),
    ActionSpec("send-notification-batch", authority="phase7.12", target_param="batch_id",
               effect="Send one Phase 7.12 notification batch (live HTTPS POST; all Phase 7.12 live gates preserved).",
               network_use="HTTPS_WEBHOOK", upstream_state_change="phase7.12",
               requires_confirmation=True, phrase_prefix="SEND"),
    ActionSpec("create-backup-snapshot", authority="phase7.9", target_param=None,
               effect="Create a Phase 7.9 deterministic local backup snapshot (no network).",
               upstream_state_change="phase7.9", requires_confirmation=True, phrase_prefix="BACKUP"),
    ActionSpec("verify-backup", authority="phase7.9", target_param="snapshot_id",
               effect="Verify one Phase 7.9 backup snapshot against the live tree (read-only, no network)."),
    ActionSpec("check-for-update", authority="phase7.9", target_param="branch",
               effect="Check the accepted git remote for a toolkit update (read-only; never installs).",
               network_use="GIT_REMOTE", local_state_change="phase7.9_validation",
               requires_confirmation=True, phrase_prefix="CHECK-UPDATE"),
    ActionSpec("stage-update", authority="phase7.9", target_param="release_id",
               effect="Stage a toolkit update in an isolated worktree (never merges/installs; primary tree unchanged).",
               network_use="GIT_REMOTE", upstream_state_change="phase7.9_staging",
               requires_confirmation=True, phrase_prefix="STAGE-UPDATE"),
    ActionSpec("create-recovery-plan", authority="phase7.9", target_param="snapshot_id",
               effect="Create a Phase 7.9 read-only recovery plan for one snapshot (changes nothing at source).",
               upstream_state_change="phase7.9", requires_confirmation=True, phrase_prefix="PLAN"),
]}

# Prohibited actions are simply absent from the allowlist. Naming them here documents the refusal.
PROHIBITED_ACTIONS = (
    "restore", "execute-restore", "destructive-restore", "register-scheduler", "install-service",
    "install-daemon", "seller-central-login", "seller-report-download", "campaign-mutation",
    "advertising-api-call", "arbitrary-url", "arbitrary-http", "arbitrary-import", "arbitrary-call",
    "shell-command", "buyer-message", "review-request",
)


class ActionTokenStore:
    """Short-lived, single-use, in-memory action tokens. Never persisted, never logged, never
    exported. Each token is bound to the exact action, canonical parameters, target ids, and session."""

    def __init__(self, *, ttl=ACTION_TOKEN_TTL_SECONDS, clock=None):
        self._tokens = {}
        self._ttl = ttl
        self._clock = clock or (lambda: time.time())
        self._lock = RLock()

    def issue(self, payload):
        with self._lock:
            token = secrets.token_urlsafe(32)
            rec = dict(payload)
            rec["issued_at"] = self._clock()
            rec["used"] = False
            self._tokens[token] = rec
            return token, rec["issued_at"] + self._ttl

    def peek(self, token):
        with self._lock:
            rec = self._tokens.get(token)
            if rec is None:
                return None, "TOKEN_UNKNOWN"
            if rec["used"]:
                return None, "TOKEN_ALREADY_USED"
            if (self._clock() - rec["issued_at"]) > self._ttl:
                self._tokens.pop(token, None)
                return None, "TOKEN_EXPIRED"
            return rec, None

    def consume(self, token):
        with self._lock:
            rec = self._tokens.get(token)
            if rec is None or rec["used"]:
                return None
            rec["used"] = True
            return rec

    def expiry_iso(self, issued_at_plus_ttl, now):
        return _iso(now)


# ---------------------------------------------------------------- per-action prepare/execute handlers
def _canonical_params(action, params):
    """Return the canonical params dict the token binds to (only the action's known parameters)."""
    spec = ACTIONS[action]
    canon = {}
    if spec.target_param:
        canon[spec.target_param] = _s(params.get(spec.target_param))
    for extra in ("remote", "branch", "target", "note"):
        if extra in params and params[extra] is not None:
            canon[extra] = _s(params[extra])
    return canon


def _resolve_target(config, action, params):
    """Validate the requested target against the accepted authority and return (target_ids, phrase_key,
    prepared_context). Raises ConsoleError on an unknown/invalid target."""
    spec = ACTIONS[action]
    ctx = {}
    if action in ("acknowledge-alert", "dismiss-alert", "reopen-alert"):
        alert_id = _s(params.get("alert_id")).strip()
        if not alert_id:
            raise ConsoleError(400, "MISSING_TARGET", "alert_id", readiness=ACTION_BLOCKED)
        wcfg = config.watch_config()
        if not os.path.isfile(os.path.join(wcfg.alerts_dir, alert_id + ".json")):
            raise ConsoleError(404, "ALERT_NOT_FOUND", alert_id, readiness=ACTION_BLOCKED)
        return [alert_id], alert_id, ctx
    if action == "run-watchlist":
        wid = _s(params.get("watchlist_id")).strip()
        if not wid:
            raise ConsoleError(400, "MISSING_TARGET", "watchlist_id", readiness=ACTION_BLOCKED)
        wcfg = config.watch_config()
        try:
            WATCH.load_watchlist(wcfg, wid)
        except WATCH.WatchlistError as e:
            raise ConsoleError(404, "WATCHLIST_NOT_FOUND", e.code, readiness=ACTION_BLOCKED)
        return [wid], wid, ctx
    if action in ("preview-notification", "build-notification-batch"):
        rid = _s(params.get("route_id")).strip()
        if not rid:
            raise ConsoleError(400, "MISSING_TARGET", "route_id", readiness=ACTION_BLOCKED)
        ncfg = config.notify_config()
        try:
            NOTIFY.load_route(ncfg, rid)
        except NOTIFY.NotificationError as e:
            raise ConsoleError(404, "ROUTE_NOT_FOUND", e.code, readiness=ACTION_BLOCKED)
        return [rid], rid, ctx
    if action == "send-notification-batch":
        bid = _s(params.get("batch_id")).strip()
        if not bid:
            raise ConsoleError(400, "MISSING_TARGET", "batch_id", readiness=ACTION_BLOCKED)
        ncfg = config.notify_config()
        try:
            NOTIFY.load_batch(ncfg, bid)
        except NOTIFY.NotificationError as e:
            raise ConsoleError(404, "BATCH_NOT_FOUND", e.code, readiness=ACTION_BLOCKED)
        return [bid], bid, ctx
    if action == "create-backup-snapshot":
        bcfg = config.backup_config()
        manifest, _files = BACKUP.build_snapshot_manifest(bcfg)
        snap_id = manifest.get("snapshot_id")
        ctx["prospective_snapshot_id"] = snap_id
        return [snap_id], snap_id, ctx
    if action == "verify-backup":
        sid = _s(params.get("snapshot_id")).strip()
        if not sid:
            raise ConsoleError(400, "MISSING_TARGET", "snapshot_id", readiness=ACTION_BLOCKED)
        bcfg = config.backup_config()
        try:
            BACKUP.load_snapshot(bcfg, sid)
        except BACKUP.BackupError as e:
            raise ConsoleError(404, "SNAPSHOT_NOT_FOUND", e.code, readiness=ACTION_BLOCKED)
        return [sid], sid, ctx
    if action == "create-recovery-plan":
        sid = _s(params.get("snapshot_id")).strip()
        if not sid:
            raise ConsoleError(400, "MISSING_TARGET", "snapshot_id", readiness=ACTION_BLOCKED)
        bcfg = config.backup_config()
        try:
            BACKUP.load_snapshot(bcfg, sid)
        except BACKUP.BackupError as e:
            raise ConsoleError(404, "SNAPSHOT_NOT_FOUND", e.code, readiness=ACTION_BLOCKED)
        return [sid], sid, ctx
    if action == "check-for-update":
        bcfg = config.backup_config()
        branch = _s(params.get("branch")).strip() or bcfg.git_branch
        return [branch], branch, ctx
    if action == "stage-update":
        rid = _s(params.get("release_id") or params.get("target")).strip()
        if not rid:
            bcfg = config.backup_config()
            uc = os.path.join(bcfg.validation_dir, "update_check.json")
            if os.path.isfile(uc):
                try:
                    rid = _s(_load_json_file(uc, "update_check.json").get("remote_branch_head")).strip()
                except ConsoleError:
                    rid = ""
        if not rid:
            raise ConsoleError(400, "MISSING_TARGET", "release_id", readiness=ACTION_BLOCKED)
        ctx["release_id"] = rid
        return [rid], rid, ctx
    # console-internal actions
    if action == "refresh-overview":
        return ["overview"], "overview", ctx
    if action == "export-overview":
        return ["overview"], "overview", ctx
    if action == "verify-system-state":
        return ["console"], "console", ctx
    raise ConsoleError(400, "UNKNOWN_ACTION", action, readiness=ACTION_BLOCKED)


def prepare_action(config, session_fingerprint, action, params, *, now, tokens):
    if action not in ACTIONS:
        raise ConsoleError(400, "UNKNOWN_ACTION", action, readiness=ACTION_BLOCKED)
    spec = ACTIONS[action]
    if not isinstance(params, dict):
        raise ConsoleError(400, "INVALID_PARAMS", action, readiness=ACTION_BLOCKED)
    target_ids, phrase_key, ctx = _resolve_target(config, action, params)
    canon = _canonical_params(action, params)
    param_hash = content_sha256({"action": action, "params": canon})
    phrase = f"{spec.phrase_prefix}:{phrase_key}" if spec.requires_confirmation else None
    token, expires_at = tokens.issue({
        "action": action, "params": canon, "param_hash": param_hash, "target_ids": target_ids,
        "confirmation_phrase": phrase, "requires_confirmation": spec.requires_confirmation,
        "session_fingerprint": session_fingerprint, "context": ctx,
    })
    return {
        "action_token": token, "canonical_action": action, "target_ids": target_ids,
        "expected_authority": spec.authority, "expected_effect": spec.effect,
        "network_use": spec.network_use, "local_state_changes": spec.local_state_change,
        "upstream_state_changes": spec.upstream_state_change,
        "requires_confirmation": spec.requires_confirmation,
        "confirmation_phrase": phrase, "param_hash": param_hash,
        "expires_in_seconds": ACTION_TOKEN_TTL_SECONDS,
        "readiness": ACTION_CONFIRMATION_REQUIRED if spec.requires_confirmation else ACTION_READY,
    }


def _map_result(action, result):
    """Map an accepted-authority result to (execution_result, upstream_result_id, policy_result)."""
    if not isinstance(result, dict):
        return ACTION_COMPLETED, None, "ALLOWED"
    readiness = result.get("readiness")
    ok = result.get("ok")
    policy = "ALLOWED"
    if readiness and "SELLER_CENTRAL_POLICY_BLOCKED" in str(readiness):
        policy = "SELLER_CENTRAL_POLICY_BLOCKED"
    # pick the most meaningful upstream id
    upstream_id = None
    for key in ("execution_id", "delivery_id", "batch_id", "snapshot_id", "run_id", "alert_id",
                "plan_path", "aggregate_hash", "staged_commit", "remote_branch_head"):
        if result.get(key):
            upstream_id = _s(result.get(key))
            break
    if policy == "SELLER_CENTRAL_POLICY_BLOCKED":
        return ACTION_BLOCKED, upstream_id, policy
    if ok:
        return ACTION_COMPLETED, upstream_id, policy
    return ACTION_FAILED, upstream_id, policy


def _execute_authority(config, action, params, ctx):
    """Invoke the ONE accepted authority for `action`. This is a fixed if/elif dispatch — never a
    dynamic attribute lookup on caller input, never an arbitrary import, never a subprocess, never an
    arbitrary URL."""
    if action == "run-watchlist":
        return WATCH.run_watchlist(config.watch_config(), _s(params.get("watchlist_id")))
    if action == "acknowledge-alert":
        return WATCH._alert_action(config.watch_config(), _s(params.get("alert_id")),
                                   WATCH.ACT_ACKNOWLEDGE, config.actor, _s(params.get("note")) or None)
    if action == "dismiss-alert":
        return WATCH._alert_action(config.watch_config(), _s(params.get("alert_id")),
                                   WATCH.ACT_DISMISS, config.actor, _s(params.get("note")) or None)
    if action == "reopen-alert":
        return WATCH._alert_action(config.watch_config(), _s(params.get("alert_id")),
                                   WATCH.ACT_REOPEN, config.actor, _s(params.get("note")) or None)
    if action == "preview-notification":
        return NOTIFY.build_batch(config.notify_config(), _s(params.get("route_id")), persist=False)
    if action == "build-notification-batch":
        ncfg = config.notify_config()
        rid = _s(params.get("route_id"))
        built = NOTIFY.build_batch(ncfg, rid, persist=True)
        bid = built.get("batch_id")
        if bid:
            NOTIFY.write_outbox(ncfg, rid, batch_id=bid)
        return built
    if action == "send-notification-batch":
        bid = _s(params.get("batch_id"))
        # the console's confirmation phrase is additional; the accepted Phase 7.12 SEND token +
        # PHASE7_12_ALLOW_LIVE_DELIVERY env gate are preserved, never replaced.
        return NOTIFY.send_batch(config.notify_config(), bid, confirm_send="SEND:" + bid)
    if action == "create-backup-snapshot":
        return BACKUP.create_snapshot(config.backup_config())
    if action == "verify-backup":
        return BACKUP.verify_snapshot(config.backup_config(), _s(params.get("snapshot_id")))
    if action == "check-for-update":
        bcfg = config.backup_config()
        branch = _s(params.get("branch")).strip() or None
        return BACKUP.update_check(bcfg, branch=branch)
    if action == "stage-update":
        return BACKUP.update_stage(config.backup_config(), target=ctx.get("release_id"))
    if action == "create-recovery-plan":
        return BACKUP.restore_plan(config.backup_config(), _s(params.get("snapshot_id")))
    raise ConsoleError(400, "UNKNOWN_ACTION", action, readiness=ACTION_BLOCKED)


def execute_action(config, *, token, confirmation_phrase, session_fingerprint, now, tokens, audit,
                   actor, request_id, cache=None):
    """Validate the single-use token + confirmation, verify the audit chain, perform the ONE accepted
    authority action, and record an immutable audit event. Returns the execute response."""
    rec, err = tokens.peek(token)
    if err is not None:
        raise ConsoleError(400 if err != "TOKEN_EXPIRED" else 410, err, readiness=ACTION_BLOCKED)
    if rec.get("session_fingerprint") != session_fingerprint:
        raise ConsoleError(403, "TOKEN_SESSION_MISMATCH", readiness=ACTION_BLOCKED)
    action = rec["action"]
    spec = ACTIONS[action]
    # confirmation is checked BEFORE the single-use token is consumed, so a wrong phrase does not burn
    # the token; a correct phrase (or a non-confirmation action) consumes it exactly once.
    if spec.requires_confirmation:
        if _s(confirmation_phrase).strip() != _s(rec.get("confirmation_phrase")):
            raise ConsoleError(400, "CONFIRMATION_PHRASE_MISMATCH",
                               readiness=ACTION_CONFIRMATION_REQUIRED)
    # audit chain integrity gate — a corrupt chain blocks every state-changing operation.
    st = audit.state()
    if not st["ok"]:
        raise ConsoleError(409, "AUDIT_STATE_BLOCKED", st["reason"], readiness=AUDIT_STATE_BLOCKED)

    consumed = tokens.consume(token)
    if consumed is None:
        raise ConsoleError(400, "TOKEN_ALREADY_USED", readiness=ACTION_BLOCKED)

    params = rec["params"]
    ctx = rec.get("context", {})
    failure_reason = None
    if spec.authority == "console":
        execution_result, upstream_id, policy_result, result = _execute_console_action(
            config, action, now=now, cache=cache)
    else:
        try:
            result = _execute_authority(config, action, params, ctx)
            execution_result, upstream_id, policy_result = _map_result(action, result)
            if execution_result != ACTION_COMPLETED:
                failure_reason = _s(result.get("readiness")) if isinstance(result, dict) else None
        except (WATCH.WatchlistError, NOTIFY.NotificationError, BACKUP.BackupError,
                RESEARCH.ResearchError, NP.ConnectedNetworkDenied, MONEY.MoneyError) as e:
            code = getattr(e, "code", type(e).__name__)
            readiness = getattr(e, "readiness", None)
            policy_result = ("SELLER_CENTRAL_POLICY_BLOCKED"
                             if readiness and "SELLER_CENTRAL_POLICY_BLOCKED" in str(readiness)
                             else "ALLOWED")
            execution_result = (ACTION_BLOCKED if policy_result == "SELLER_CENTRAL_POLICY_BLOCKED"
                                else ACTION_FAILED)
            upstream_id = None
            failure_reason = code
            result = {"error": code, "detail": DIAG.redact_secrets(getattr(e, "detail", "")),
                      "readiness": readiness}

    event = audit.append({
        "request_id": request_id, "actor": actor,
        "session_fingerprint": session_fingerprint, "action": action,
        "target_ids": rec["target_ids"], "param_hash": rec["param_hash"],
        "authority": spec.authority, "preparation_result": "PREPARED",
        "execution_result": execution_result, "upstream_result_id": upstream_id,
        "readiness": (result.get("readiness") if isinstance(result, dict) else execution_result),
        "policy_result": policy_result,
    }, now=now)

    if cache is not None and execution_result == ACTION_COMPLETED and action != "verify-system-state":
        cache.invalidate()

    return {
        "readiness": execution_result, "action": action, "target_ids": rec["target_ids"],
        "authority": spec.authority, "upstream_result_id": upstream_id,
        "policy_result": policy_result, "failure_reason": failure_reason,
        "console_event_id": event["console_event_id"], "aggregate_hash": event["aggregate_hash"],
        "upstream_readiness": (result.get("readiness") if isinstance(result, dict) else None),
        "upstream_summary": _sanitize_result(action, result),
    }


def _execute_console_action(config, action, *, now, cache):
    if action == "refresh-overview":
        if cache is not None:
            cache.invalidate()
        model = build_console_model(config, now=now)
        return (ACTION_COMPLETED, content_sha256(model["overview"])[:24], "ALLOWED",
                {"ok": True, "readiness": model["readiness"], "overview": model["overview"]})
    if action == "export-overview":
        model = build_console_model(config, now=now)
        paths = write_exports(config.base_dir, model, subdir="exports")
        bundle = content_sha256(sorted(_rel_display(p) for p in paths.values()))[:24]
        return (ACTION_COMPLETED, bundle, "ALLOWED",
                {"ok": True, "readiness": model["readiness"],
                 "exports": [_rel_display(p) for p in paths.values()]})
    if action == "verify-system-state":
        audit_state = verify_audit_chain(config.audit_path)
        model = build_console_model(config, now=now)
        record = {
            "schema_version": "phase7-13-console-validation-v1",
            "console_readiness": model["readiness"], "audit_chain": audit_state,
            "module_status": model["overview"]["module_status"],
            "seller_central_counters": dict(SELLER_CENTRAL_COUNTERS),
        }
        vdir = os.path.join(config.base_dir, "validation")
        os.makedirs(vdir, exist_ok=True)
        path = os.path.join(vdir, "system_state.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(canonical_json(record) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        ok = audit_state["ok"] and model["readiness"] not in _BLOCKING_READINESS
        return (ACTION_COMPLETED if ok else ACTION_FAILED,
                content_sha256(record)[:24], "ALLOWED",
                {"ok": ok, "readiness": model["readiness"], "audit_chain": audit_state})
    raise ConsoleError(400, "UNKNOWN_ACTION", action, readiness=ACTION_BLOCKED)


def _sanitize_result(action, result):
    """A bounded, secret-free summary of an authority result for the UI (never the full payload)."""
    if not isinstance(result, dict):
        return {}
    keep = ("readiness", "ok", "execution_id", "delivery_id", "delivery_state", "batch_id",
            "snapshot_id", "file_count", "run_id", "status", "alert_id", "update_available",
            "remote_branch_head", "staged_commit", "change_count", "alerts_created", "exports",
            "outbox_path", "provider_status", "detail", "error", "note", "plan_path",
            "required_confirmation_token", "compile_ok", "focused_tests_ok")
    out = {}
    for k in keep:
        if k in result:
            v = result[k]
            if isinstance(v, str):
                v = DIAG.redact_secrets(v)
            out[k] = v
    return out


# ================================================================ deterministic exports
_TSV_COLUMNS = ("section", "identity", "status", "detail")
_MD_TITLE = "# Unified Owner Console Snapshot"


def _export_rows(model):
    rows = []
    ov = model["overview"]
    for name, status in sorted(ov["module_status"].items()):
        rows.append({"section": "module", "identity": name, "status": status,
                     "detail": OWNER_LABELS.get(status, status)})
    for r in model["sections"]["watchlists"]["alerts"]:
        rows.append({"section": "alert", "identity": r.get("alert_id"), "status": r.get("status"),
                     "detail": r.get("label")})
    for r in model["sections"]["notifications"]["deliveries"]:
        rows.append({"section": "delivery", "identity": r.get("delivery_id"),
                     "status": r.get("delivery_state"), "detail": r.get("route_id")})
    for r in model["sections"]["backup"]["snapshots"]:
        rows.append({"section": "snapshot", "identity": r.get("snapshot_id"), "status": "PRESENT",
                     "detail": str(r.get("file_count"))})
    for r in model["sections"]["research"]["runs"]:
        rows.append({"section": "research_run", "identity": r.get("run_id"),
                     "status": r.get("readiness"), "detail": str(r.get("evidence_count"))})
    rows.sort(key=lambda r: (r["section"], _s(r["identity"])))
    return rows


def export_json(model):
    ov = model["overview"]
    payload = {
        "meta": {
            "schema_version": EXPORT_SCHEMA, "stage_id": STAGE_ID, "stage_name": STAGE_NAME,
            "console_readiness": model["readiness"], "disclaimer": list(DISCLAIMER_LINES),
            "seller_central_action_performed": False, "is_amazon_upload_file": False,
            "seller_central_counters": dict(SELLER_CENTRAL_COUNTERS),
        },
        "repository_commit": model["system"].get("repository_commit"),
        "overview": ov, "modules": model["modules"], "freshness": model["freshness"],
        "connectivity_boundary": model["system"].get("connectivity_boundary"),
        "audit_chain": {"ok": model["system"]["audit_chain"]["ok"],
                        "event_count": model["system"]["audit_chain"].get("event_count"),
                        "aggregate_hash": model["system"]["audit_chain"].get("aggregate_hash")},
        "rows": _export_rows(model),
        "source_authorities": model["source_authorities"],
    }
    return canonical_json(payload) + "\n"


def export_tsv(model):
    lines = [f"# {line}" for line in DISCLAIMER_LINES]
    lines.append(f"# console_readiness\t{model['readiness']}")
    lines.append("\t".join(_TSV_COLUMNS))
    for r in _export_rows(model):
        lines.append("\t".join(_TSV_CELL(r.get(c)) for c in _TSV_COLUMNS))
    return "\n".join(lines) + "\n"


def export_markdown(model):
    ov = model["overview"]
    out = [_MD_TITLE, "", "> **UNIFIED OWNER CONSOLE — LOCAL, LOOPBACK-ONLY**"]
    for line in DISCLAIMER_LINES[1:]:
        out.append("> " + line)
    out += ["", f"- Console readiness: `{model['readiness']}`",
            f"- Repository commit: `{model['system'].get('repository_commit')}`",
            f"- Open alerts: {ov.get('open_alerts')} · Pending decisions: {ov.get('pending_decisions')} "
            f"· Pending manual actions: {ov.get('pending_manual_actions')}",
            f"- Notifications sent/failed/unknown: {ov.get('notification_sent')}/"
            f"{ov.get('notification_failed')}/{ov.get('notification_unknown')}",
            f"- Backup snapshots: {ov.get('backup_snapshots')} · Update available: "
            f"{ov.get('update_available')}",
            "",
            "| " + " | ".join(_TSV_COLUMNS) + " |",
            "| " + " | ".join("---" for _ in _TSV_COLUMNS) + " |"]
    for r in _export_rows(model):
        cells = []
        for c in _TSV_COLUMNS:
            v = r.get(c)
            cells.append("—" if v in (None, "") else str(v).replace("|", "\\|").replace("\n", " "))
        out.append("| " + " | ".join(cells) + " |")
    out.append("")
    return "\n".join(out)


EXPORTERS = {
    "owner_console_snapshot.json": (export_json, "application/json; charset=utf-8"),
    "owner_console_status.tsv": (export_tsv, "text/tab-separated-values; charset=utf-8"),
    "owner_console_report.md": (export_markdown, "text/markdown; charset=utf-8"),
}


def write_exports(base_dir, model, *, subdir="exports"):
    base = ensure_workspace(base_dir)
    out_dir = os.path.join(base, subdir)
    os.makedirs(out_dir, exist_ok=True)
    written = {}
    for name, (fn, _ctype) in EXPORTERS.items():
        content = fn(model)
        path = os.path.join(out_dir, name)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(content.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        written[name] = path
    return written


# ================================================================ HTTP server
_READ_ENDPOINTS = ("health", "overview", "modules", "analysis", "research", "watchlists", "alerts",
                   "notifications", "backups", "system", "activity", "next-action", "workflow")


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, *, config, now=None):
        self.config = config
        self.now_fn = now or _now_utc
        self.sessions = SessionManager()
        self.tokens = ActionTokenStore()
        self.cache = ReadModelCache()
        self.audit = AuditLog(config.audit_path)
        super().__init__(address, handler)

    def now(self):
        return self.now_fn()

    def model(self, *, use_cache=True):
        key = "console-model"
        if use_cache:
            hit = self.cache.get(key)
            if hit is not None and not hit["stale"]:
                m = dict(hit["value"])
                m["_cache"] = {"cached": True, "stale": False, "age_seconds": round(hit["age"], 3),
                               "state_hash": hit["state_hash"]}
                return m
        model = build_console_model(self.config, now=self.now(), audit=self.audit)
        state_hash = content_sha256({"overview": model["overview"],
                                     "module_status": model["overview"]["module_status"]})
        self.cache.put(key, model, state_hash)
        m = dict(model)
        m["_cache"] = {"cached": False, "stale": False, "age_seconds": 0.0, "state_hash": state_hash}
        return m


def _make_handler():
    class Handler(BaseHTTPRequestHandler):
        server_version = "Phase713UnifiedOwnerConsole/1.0"
        protocol_version = "HTTP/1.1"
        timeout = 15

        def log_message(self, *a):   # no telemetry, no stdout noise
            return

        # -------------------------------------------------- response helpers
        def _headers(self, status, content_type, body_len, *, extra=()):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(body_len))
            for k, v in _BASE_SECURITY_HEADERS:
                self.send_header(k, v)
            self.send_header("Cache-Control", "no-store" if content_type.startswith("application/json")
                             else "no-cache")
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()

        def _send_bytes(self, status, content_type, body, *, extra=()):
            self._headers(status, content_type, len(body), extra=extra)
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, status=200, *, extra=()):
            body = (canonical_json(obj) + "\n").encode("utf-8")
            self._send_bytes(status, "application/json; charset=utf-8", body, extra=extra)

        def _envelope(self, data, *, readiness=None, warnings=None, errors=None, status=200, extra=()):
            self._json({
                "schema_version": API_SCHEMA, "api_version": API_VERSION,
                "request_id": _request_id(), "readiness": readiness,
                "generated_at": _iso(self.server.now()), "data": data,
                "warnings": warnings or [], "errors": errors or [],
                "source_authorities": SOURCE_AUTHORITIES,
                "seller_central_counters": dict(SELLER_CENTRAL_COUNTERS),
            }, status=status, extra=extra)

        def _error(self, status, code, detail="", *, readiness=None):
            self._json({
                "schema_version": API_SCHEMA, "request_id": _request_id(),
                "error": code, "detail": detail, "readiness": readiness,
                "seller_central_action_performed": False,
                "seller_central_counters": dict(SELLER_CENTRAL_COUNTERS),
            }, status=status)

        # -------------------------------------------------- host / body safety
        def _host_ok(self):
            hdr = self.headers.get("Host")
            if hdr is None:
                return False
            h = hdr.strip()
            if h.startswith("["):
                end = h.find("]")
                if end < 0:
                    return False
                name, rest = h[1:end], h[end + 1:]
                port = rest[1:] if rest.startswith(":") else ""
            elif ":" in h:
                name, port = h.rsplit(":", 1)
            else:
                name, port = h, ""
            if name.lower() not in LOOPBACK_HOSTS:
                return False
            if port and port != str(self.server.server_address[1]):
                return False
            return True

        def _read_body(self):
            te = (self.headers.get("Transfer-Encoding") or "").strip()
            if te:
                self.close_connection = True
                raise ConsoleError(400, "CHUNKED_BODY_REJECTED")
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ConsoleError(400, "INVALID_CONTENT_LENGTH")
            if length < 0:
                raise ConsoleError(400, "INVALID_CONTENT_LENGTH")
            if length > MAX_BODY_BYTES:
                self.close_connection = True
                raise ConsoleError(413, "REQUEST_BODY_TOO_LARGE")
            return self.rfile.read(length) if length else b""

        def _drain_body(self):
            if (self.headers.get("Transfer-Encoding") or "").strip():
                self.close_connection = True
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.close_connection = True
                return
            cap = MAX_BODY_BYTES
            to_read = min(max(length, 0), cap)
            while to_read > 0:
                chunk = self.rfile.read(min(65536, to_read))
                if not chunk:
                    break
                to_read -= len(chunk)
            if length > cap:
                self.close_connection = True

        # -------------------------------------------------- session helpers
        def _session_id(self):
            raw = self.headers.get("Cookie")
            if not raw:
                return None
            try:
                jar = SimpleCookie()
                jar.load(raw)
            except Exception:   # noqa: BLE001
                return None
            m = jar.get(SESSION_COOKIE)
            return m.value if m else None

        def _ensure_session(self):
            """Return (session_id, set_cookie_or_None). Issues a new session if none is valid."""
            sid = self._session_id()
            if sid and self.server.sessions.valid(sid):
                return sid, None
            sid, _csrf = self.server.sessions.create()
            cookie = (f"{SESSION_COOKIE}={sid}; HttpOnly; SameSite=Strict; Path=/")
            return sid, cookie

        # -------------------------------------------------- method dispatch
        def do_GET(self):
            self._handle_get(head=False)

        def do_HEAD(self):
            self._handle_get(head=True)

        def do_POST(self):
            self._handle_post()

        def _reject_method(self):
            self._drain_body()
            self._error(405, "METHOD_NOT_ALLOWED", self.command)

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _reject_method
        do_TRACE = do_CONNECT = _reject_method

        # -------------------------------------------------- GET (reads only)
        def _handle_get(self, *, head):
            try:
                self._drain_body()
                if len(self.requestline) > MAX_URL_BYTES:
                    return self._error(414, "URI_TOO_LONG")
                if not self._host_ok():
                    return self._error(400, "BAD_HOST")
                split = urlsplit(self.path)
                path = split.path
                qs = {}
                for k, v in parse_qsl(split.query, keep_blank_values=True):
                    if k in qs:
                        raise ConsoleError(400, "DUPLICATE_QUERY_PARAM", k)
                    if len(v) > MAX_QUERY_VALUE:
                        raise ConsoleError(400, "QUERY_VALUE_TOO_LONG", k)
                    qs[k] = v
                sid, set_cookie = self._ensure_session()
                extra = (("Set-Cookie", set_cookie),) if set_cookie else ()
                if path in ("/", "/index.html"):
                    return self._static("index.html", extra=extra)
                if path.lstrip("/") in STATIC_FILES:
                    return self._static(path.lstrip("/"), extra=extra)
                if path.lstrip("/") in FAVICON_ALIASES:
                    return self._static(FAVICON_ALIASES[path.lstrip("/")], extra=extra)
                if path.startswith(f"/api/{API_VERSION}/"):
                    return self._api_get(path, qs, sid, extra)
                return self._error(404, "NOT_FOUND", path)
            except ConsoleError as e:
                return self._error(e.status, e.code, e.detail, readiness=e.readiness)
            except Exception as e:   # noqa: BLE001 — never leak a stack trace
                return self._error(500, "INTERNAL_ERROR", type(e).__name__)

        def _static(self, name, *, extra=()):
            if name not in STATIC_FILES:
                return self._error(404, "STATIC_NOT_FOUND", name)
            path = os.path.join(STATIC_DIR, name)
            if not os.path.isfile(path):
                return self._error(404, "STATIC_NOT_FOUND", name)
            with open(path, "rb") as f:
                return self._send_bytes(200, STATIC_FILES[name], f.read(), extra=extra)

        def _api_get(self, path, qs, sid, extra):
            endpoint = path[len(f"/api/{API_VERSION}/"):]
            if endpoint == "session":
                csrf = self.server.sessions.csrf_for(sid)
                return self._envelope({"csrf_token": csrf, "poll_min_seconds": MIN_CLIENT_POLL_SECONDS,
                                       "session_active": csrf is not None},
                                      readiness=CONSOLE_READY if csrf else SESSION_REQUIRED, extra=extra)
            if endpoint == "health":
                return self._health(extra)
            if endpoint == "exports/overview":
                model = self.server.model()
                fmt = qs.get("format", "json")
                fname = {"json": "owner_console_snapshot.json", "tsv": "owner_console_status.tsv",
                         "md": "owner_console_report.md"}.get(fmt)
                if fname is None:
                    return self._error(400, "UNSUPPORTED_EXPORT_FORMAT", fmt)
                fn, ctype = EXPORTERS[fname]
                return self._send_bytes(200, ctype, fn(model).encode("utf-8"), extra=extra)
            if endpoint not in _READ_ENDPOINTS:
                return self._error(404, "UNKNOWN_ENDPOINT", endpoint)
            model = self.server.model()
            data, readiness = self._read_data(endpoint, qs, model)
            return self._envelope(data, readiness=readiness, extra=extra,
                                  warnings=(["stale-cache"] if model.get("_cache", {}).get("stale")
                                            else []))

        def _read_data(self, endpoint, qs, model):
            readiness = model["readiness"]
            cache_meta = model.get("_cache", {})
            if endpoint == "overview":
                # `next_action` is additive: every field the accepted Phase 7.13 contract published
                # is still present and unchanged.
                return {"overview": model["overview"], "disclaimer": list(DISCLAIMER_LINES),
                        "cache": cache_meta, "freshness": model["freshness"],
                        "next_action": model.get("next_action")}, readiness
            if endpoint == "next-action":
                return {"next_action": model.get("next_action")}, readiness
            if endpoint == "modules":
                return {"modules": model["modules"]}, readiness
            if endpoint == "system":
                return {"system": model["system"]}, readiness
            if endpoint == "analysis":
                sec = model["sections"]["analysis"]
                sub = qs.get("view", "overview")
                m = sec.get("model") or {}
                payload = {"status": sec["status"], "readiness": sec.get("readiness"),
                           "counts": sec.get("counts", {})}
                if m:
                    if sub == "analysis":
                        payload["rows"] = _page(m["analysis"]["rows"], qs)
                    elif sub == "reviews":
                        payload["rows"] = _page(m["reviews"].get("records", []), qs)
                    elif sub == "decisions":
                        payload["rows"] = _page(m["decision_packages"].get("eligible_items", []), qs)
                    elif sub == "manual-actions":
                        payload["rows"] = _page(m["manual_actions"].get("records", []), qs)
                    elif sub == "outcomes":
                        payload["rows"] = _page(m["outcomes"].get("followups", []), qs)
                    elif sub == "attention":
                        payload["rows"] = _page(m.get("attention", []), qs)
                    else:
                        payload["overview"] = m.get("overview")
                        payload["lineage"] = m.get("lineage")
                return payload, readiness
            if endpoint == "research":
                sec = model["sections"]["research"]
                data = {"status": sec["status"], "runs": _page(sec["runs"], qs),
                        "counts": sec["counts"]}
                run_id = qs.get("run_id")
                if run_id:
                    try:
                        data["detail"] = _research_run_detail(self.server.config, run_id)
                    except Exception as e:   # noqa: BLE001
                        data["detail_error"] = type(e).__name__
                return data, readiness
            if endpoint == "watchlists":
                sec = model["sections"]["watchlists"]
                return {"status": sec["status"], "watchlists": _page(sec["watchlists"], qs),
                        "counts": sec["counts"]}, readiness
            if endpoint == "alerts":
                sec = model["sections"]["watchlists"]
                rows = sec["alerts"]
                status_filter = qs.get("status")
                if status_filter:
                    rows = [r for r in rows if r.get("status") == status_filter]
                return {"status": sec["status"], "alerts": _page(rows, qs),
                        "counts": sec["counts"],
                        "notice": ("Alert state changes must be made through the accepted Phase 7.11 "
                                   "authority via a confirmed console action.")}, readiness
            if endpoint == "notifications":
                sec = model["sections"]["notifications"]
                return {"status": sec["status"], "routes": sec["routes"],
                        "batches": _page(sec["batches"], qs), "deliveries": sec["deliveries"],
                        "counts": sec["counts"],
                        "notice": ("UNKNOWN delivery is never treated as FAILED; live send preserves "
                                   "every accepted Phase 7.12 gate.")}, readiness
            if endpoint == "backups":
                sec = model["sections"]["backup"]
                return {"status": sec["status"], "snapshots": _page(sec["snapshots"], qs),
                        "update_check": sec["update_check"], "recovery_plans": sec["recovery_plans"],
                        "counts": sec["counts"]}, readiness
            if endpoint == "workflow":
                sec = model["sections"]["workflow"]
                return {"status": sec["status"], "stages": sec["stages"], "counts": sec["counts"],
                        "product_root": sec["product_root"],
                        "primary_next_stage_id": sec.get("primary_next_stage_id")}, readiness
            if endpoint == "activity":
                audit_state = self.server.audit.state()
                events = self.server.audit.events()
                if len(events) > MAX_ACTIVITY_ROWS:
                    events = events[-MAX_ACTIVITY_ROWS:]
                view = [{
                    "console_event_id": e.get("console_event_id"), "recorded_at": e.get("recorded_at"),
                    "actor": e.get("actor"), "action": e.get("action"),
                    "target_ids": e.get("target_ids"), "authority": e.get("authority"),
                    "preparation_result": e.get("preparation_result"),
                    "execution_result": e.get("execution_result"),
                    "upstream_result_id": e.get("upstream_result_id"), "readiness": e.get("readiness"),
                    "policy_result": e.get("policy_result"), "request_id": e.get("request_id"),
                } for e in events]
                return {"audit_chain": audit_state, "events": _page(view, qs)}, (
                    AUDIT_STATE_BLOCKED if not audit_state["ok"] else readiness)
            return {}, readiness

        def _health(self, extra):
            audit_state = self.server.audit.state()
            self._envelope({
                "stage_id": STAGE_ID, "stage_name": STAGE_NAME, "api_schema": API_SCHEMA,
                "audit_chain_ok": audit_state["ok"], "audit_event_count": audit_state.get("event_count"),
                "seller_central_action_performed": False,
                "this_console_never": dict(THIS_CONSOLE_NEVER),
                "server_time": _iso(self.server.now()),
            }, readiness=CONSOLE_READY, extra=extra)

        # -------------------------------------------------- POST (prepared actions only)
        def _handle_post(self):
            try:
                if not self._host_ok():
                    self._drain_body()
                    return self._error(400, "BAD_HOST")
                if len(self.requestline) > MAX_URL_BYTES:
                    self._drain_body()
                    return self._error(414, "URI_TOO_LONG")
                split = urlsplit(self.path)
                path = split.path
                if split.query:
                    self._drain_body()
                    return self._error(400, "QUERY_STRING_MUTATION_REFUSED", path)
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                body = self._read_body()
                if ctype != "application/json":
                    return self._error(415, "JSON_REQUIRED", ctype)
                sid = self._session_id()
                if not sid or not self.server.sessions.valid(sid):
                    return self._error(401, "SESSION_REQUIRED", readiness=SESSION_REQUIRED)
                if not self.server.sessions.check_csrf(sid, self.headers.get(CSRF_HEADER)):
                    return self._error(403, "CSRF_TOKEN_INVALID", readiness=CSRF_BLOCKED)
                payload = parse_json_body(body)
                fingerprint = self.server.sessions.fingerprint(sid)
                if path == f"/api/{API_VERSION}/actions/prepare":
                    return self._prepare(payload, fingerprint)
                if path == f"/api/{API_VERSION}/actions/execute":
                    return self._execute(payload, fingerprint, sid)
                return self._error(404, "UNKNOWN_ENDPOINT", path)
            except ConsoleError as e:
                return self._error(e.status, e.code, e.detail, readiness=e.readiness)
            except Exception as e:   # noqa: BLE001
                return self._error(500, "INTERNAL_ERROR", type(e).__name__)

        def _prepare(self, payload, fingerprint):
            action = _s(payload.get("action")).strip()
            params = payload.get("params") or {}
            resp = prepare_action(self.server.config, fingerprint, action, params,
                                  now=self.server.now(), tokens=self.server.tokens)
            return self._envelope(resp, readiness=resp["readiness"])

        def _execute(self, payload, fingerprint, sid):
            token = _s(payload.get("action_token")).strip()
            phrase = payload.get("confirmation_phrase")
            if not token:
                return self._error(400, "MISSING_ACTION_TOKEN", readiness=ACTION_BLOCKED)
            resp = execute_action(
                self.server.config, token=token, confirmation_phrase=phrase,
                session_fingerprint=fingerprint, now=self.server.now(), tokens=self.server.tokens,
                audit=self.server.audit, actor=self.server.config.actor, request_id=_request_id(),
                cache=self.server.cache)
            return self._envelope(resp, readiness=resp["readiness"])

    return Handler


def _page(rows, qs):
    """A bounded, deterministic filter -> paginate. Row identities never change."""
    out = list(rows)
    needle = qs.get("filter")
    if needle:
        low = needle.lower()
        out = [r for r in out if any(low in _s(v).lower() for v in r.values()
                                     if not isinstance(v, (dict, list)))]
    sort = qs.get("sort")
    if sort:
        direction = qs.get("direction", "asc")
        out.sort(key=lambda r: _s(r.get(sort)), reverse=(direction == "desc"))
    try:
        page = max(1, int(qs.get("page", "1")))
    except ValueError:
        page = 1
    try:
        page_size = int(qs.get("page_size", str(DEFAULT_PAGE_SIZE)))
    except ValueError:
        page_size = DEFAULT_PAGE_SIZE
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    total = len(out)
    start = (page - 1) * page_size
    window = out[start:start + page_size]
    return {"data": window, "page": page, "page_size": page_size, "total": total,
            "total_pages": (total + page_size - 1) // page_size if page_size else 0}


def build_server(config, *, host=DEFAULT_HOST, port=DEFAULT_PORT, now=None):
    validate_host(host)
    validate_port(port)
    ensure_workspace(config.base_dir)
    return ConsoleServer((host, port), _make_handler(), config=config, now=now)


# ================================================================ validate-only (no side effects)
def validate_only(config):
    """Structural validation with ZERO side effects: no DNS, no HTTP, no secret read, no directory
    creation, no file write, no lock, no session, no upstream mutation, no action, no subprocess."""
    checks = []
    for stage in ("7.3", "7.4", "7.5", "7.6", "7.7", "7.8", "7.9", "7.10", "7.11", "7.12"):
        present = os.path.isdir(config.phase_dir(stage))
        checks.append({"check": f"phase_{stage}_present", "ok": True, "present": present})
    checks.append({"check": "action_allowlist_fixed", "ok": len(ACTIONS) == 15, "count": len(ACTIONS)})
    checks.append({"check": "api_schema_defined", "ok": bool(API_SCHEMA)})
    checks.append({"check": "static_assets_declared", "ok": set(STATIC_FILES) == {
        "index.html", "app.js", "styles.css", "icons.svg", "favicon.svg"}})
    checks.append({"check": "next_action_schema_declared", "ok": NEXT.SCHEMA
                   == "phase7.14.next-action.v1", "schema": NEXT.SCHEMA})
    checks.append({"check": "seller_central_counters_zero",
                   "ok": all(v == 0 for v in SELLER_CENTRAL_COUNTERS.values())})
    core_present = os.path.isdir(config.phase_dir("7.3"))
    optional_present = all(os.path.isdir(config.phase_dir(s)) for s in ("7.9", "7.10", "7.11", "7.12"))
    if not core_present:
        readiness = CONSOLE_REQUIRED
    elif not optional_present:
        readiness = CONSOLE_READY_PARTIAL
    else:
        readiness = CONSOLE_READY
    return {
        "schema_version": "phase7-13-console-validate-v1", "stage_id": STAGE_ID,
        "readiness": readiness, "ok": all(c["ok"] for c in checks), "checks": checks,
        "files_written": 0, "directories_created": 0, "dns_lookups": 0, "http_requests": 0,
        "secret_env_reads": 0, "sessions_created": 0, "actions_executed": 0, "subprocesses": 0,
        "seller_central_counters": dict(SELLER_CENTRAL_COUNTERS),
    }


# ================================================================ CLI
def exit_code_for(readiness):
    return 1 if readiness in _BLOCKING_READINESS or readiness == CONSOLE_REQUIRED else 0


def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="python -m production.phase7_unified_owner_console",
        description="Unified Owner Console (Phase 7.13). One loopback web interface that unifies the "
                    "accepted Phase 7.3-7.12 authorities. Reuses every authority, connects to nothing "
                    "but through those authorities, binds to a loopback address only, and performs no "
                    "seller-account action.")
    p.add_argument("--workspace-root", default=DEFAULT_WORKSPACE_ROOT,
                   help="root holding the phase 7.3-7.12 workspaces (default runs/T2/phase7).")
    p.add_argument("--base-dir", default=DEFAULT_BASE_DIR,
                   help="the console's own 7.13 workspace (default runs/T2/phase7/7.13).")
    p.add_argument("--host", default=DEFAULT_HOST, help="loopback bind host (default 127.0.0.1).")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"bind port (default {DEFAULT_PORT}).")
    p.add_argument("--reference-date", default=None, help="optional reference date (YYYY-MM-DD).")
    p.add_argument("--actor", default="owner-console", help="actor label recorded in the audit trail.")
    p.add_argument("--allow-local-test-server", action="store_true",
                   help="test-only: permit a loopback endpoint in the reused connected authorities.")
    p.add_argument("--no-browser", action="store_true",
                   help="no-op: this console never opens a browser automatically.")
    p.add_argument("command", choices=("serve", "snapshot", "export", "verify-state", "validate-only"),
                   help="serve the console, write a snapshot/export, verify system state, or "
                        "validate-only (no side effects).")
    return p


def _config_from_args(a):
    return Config(a.base_dir, a.workspace_root, actor=a.actor, reference_date=a.reference_date,
                  allow_local=a.allow_local_test_server)


def _print_summary(model, *, host, port):
    ov = model["overview"]
    na = model.get("next_action") or {}
    pairs = [
        ("console_readiness", model["readiness"]),
        ("owner_label", ov.get("owner_label")),
        ("next_action_priority", na.get("priority")),
        ("next_action_title", na.get("owner_title")),
        ("next_action_step", na.get("recommended_step")),
        ("pending_decisions", ov.get("pending_decisions")),
        ("pending_manual_actions", ov.get("pending_manual_actions")),
        ("open_alerts", ov.get("open_alerts")),
        ("due_watchlists", ov.get("due_watchlists")),
        ("notification_sent", ov.get("notification_sent")),
        ("notification_failed", ov.get("notification_failed")),
        ("notification_unknown", ov.get("notification_unknown")),
        ("research_runs", ov.get("research_runs")),
        ("backup_snapshots", ov.get("backup_snapshots")),
        ("update_available", ov.get("update_available")),
        ("blocked_modules", ",".join(ov.get("blocked_modules", [])) or "-"),
        ("audit_chain_ok", model["system"]["audit_chain"]["ok"]),
        ("seller_central_connections", SELLER_CENTRAL_COUNTERS["seller_central_connections"]),
        ("seller_api_calls", SELLER_CENTRAL_COUNTERS["seller_api_calls"]),
        ("advertising_api_calls", SELLER_CENTRAL_COUNTERS["advertising_api_calls"]),
        ("console_url", f"http://{host}:{port}"),
    ]
    print("\n".join(f"{k}={'' if v is None else v}" for k, v in pairs))


def main(argv=None, *, serve=True):
    a = build_arg_parser().parse_args(argv)
    try:
        validate_host(a.host)
        validate_port(a.port)
    except ConsoleError as e:
        print(f"{e.code}: {e.detail}", file=sys.stderr)
        return 2

    config = _config_from_args(a)

    if a.command == "validate-only":
        res = validate_only(config)
        print("\n".join(f"{k}={res[k]}" for k in ("readiness", "ok", "files_written",
                                                  "directories_created", "dns_lookups",
                                                  "http_requests", "actions_executed")))
        return exit_code_for(res["readiness"])

    ensure_workspace(config.base_dir)
    audit = AuditLog(config.audit_path)
    model = build_console_model(config, audit=audit)
    _print_summary(model, host=a.host, port=a.port)

    if a.command in ("snapshot", "export"):
        subdir = "snapshots" if a.command == "snapshot" else "exports"
        written = write_exports(config.base_dir, model, subdir=subdir)
        for name, path in sorted(written.items()):
            print(f"{a.command}={_rel_display(path)}")
        return exit_code_for(model["readiness"])

    if a.command == "verify-state":
        st = verify_audit_chain(config.audit_path)
        print(f"audit_chain_ok={st['ok']}")
        print(f"audit_event_count={st.get('event_count')}")
        return exit_code_for(AUDIT_STATE_BLOCKED if not st["ok"] else model["readiness"])

    if not serve:
        return exit_code_for(model["readiness"])

    try:
        server = build_server(config, host=a.host, port=a.port)
    except ConsoleError as e:
        print(f"{e.code}: {e.detail}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"BIND_FAILED: {e}", file=sys.stderr)
        return 2
    print(f"Serving on http://{a.host}:{a.port} — press Ctrl+C to stop. (No browser is opened.)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
