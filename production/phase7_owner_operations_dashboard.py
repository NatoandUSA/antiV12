#!/usr/bin/env python3
"""Phase 7.8 — offline, read-only Owner Operations Dashboard.

This module is the ONE Phase 7.8 authority. It is an AGGREGATION and NAVIGATION layer over the
already-accepted Phase 7.3–7.7 authorities. It re-uses their loaders, validators, and derived views;
it never re-implements or re-interprets their business logic and never weakens an integrity check.

It connects to NOTHING. There is no Amazon integration, no SP-API, no Ads API, no browser
automation, and no network client of any kind. It never creates, pauses, or changes a campaign, bid,
budget, keyword, negative, or target; it never uploads anything and never emits an API payload. It
never mutates any Phase 7.3–7.7 artifact (it is strictly read-only toward every upstream source).
The owner remains the only manual bridge to Amazon Seller Central.

What it presents (one coherent local view):
  * Phase 7.3 offline analysis (via the accepted Phase 7.4 read authority);
  * Phase 7.4 owner-review status (read-only);
  * the current, lineage-matched Phase 7.5 decision package (read-only);
  * Phase 7.6 manually recorded owner actions (read-only);
  * the current, lineage-matched Phase 7.7 observational outcome follow-up (read-only);
  * source lineage, integrity, readiness, exclusions, blockers, and a consolidated attention list.

Financial rules: Decimal-as-string values that arrive from an upstream authority are preserved
verbatim and never re-computed, never converted to float, and never aggregated across currencies,
attribution windows, or marketplaces. Missing stays missing (never silently zero).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import ipaddress
import json
import os
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qsl

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ("", "core", "production"):
    _p = _ROOT if not _sub else os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import product_workspace as PW                 # noqa: E402  canonical json / content hash
from production import phase7_ads_analysis as AA               # noqa: E402  Phase 7.3 constants / aggregate
from production import phase7_owner_dashboard as DASH          # noqa: E402  Phase 7.3 read + 7.4 review authority
from production import phase7_owner_decision_package as PKG    # noqa: E402  Phase 7.5 authority
from production import phase7_manual_action_tracker as TRK     # noqa: E402  Phase 7.6 authority (+ 7.5 pkg reader)
from production import phase7_outcome_followup as FU           # noqa: E402  Phase 7.7 authority
from core import money as MONEY                                # noqa: E402  Decimal authority (imported for parity)

canonical_json = PW.canonical_json
content_sha256 = PW.content_sha256

# ================================================================ identity / versions
STAGE_ID = "7.8"
STAGE_NAME = "Offline Owner Operations Dashboard"
MODEL_SCHEMA = "phase7-8-operations-model-v1"
EXPORT_SCHEMA = "phase7-8-operations-snapshot-v1"

# ---------------------------------------------------------------- dashboard readiness states
READY = "SESSION7_8_OPERATIONS_DASHBOARD_READY"
READY_EMPTY = "SESSION7_8_OPERATIONS_DASHBOARD_READY_EMPTY"
READY_PARTIAL = "SESSION7_8_OPERATIONS_DASHBOARD_READY_PARTIAL"
SOURCE_REQUIRED = "SESSION7_8_SOURCE_REQUIRED"
SOURCE_BLOCKED = "SESSION7_8_SOURCE_BLOCKED"
REVIEW_STATE_BLOCKED = "SESSION7_8_REVIEW_STATE_BLOCKED"
DECISION_PACKAGE_BLOCKED = "SESSION7_8_DECISION_PACKAGE_BLOCKED"
TRACKER_BLOCKED = "SESSION7_8_TRACKER_BLOCKED"
FOLLOWUP_BLOCKED = "SESSION7_8_FOLLOWUP_BLOCKED"
SELECTION_REQUIRED = "SESSION7_8_SELECTION_REQUIRED"
INTEGRITY_BLOCKED = "SESSION7_8_INTEGRITY_BLOCKED"

# states that mean the dashboard cannot present a trustworthy, complete operations view.
_BLOCKING_READINESS = (SOURCE_REQUIRED, SOURCE_BLOCKED, REVIEW_STATE_BLOCKED,
                       DECISION_PACKAGE_BLOCKED, TRACKER_BLOCKED, FOLLOWUP_BLOCKED,
                       SELECTION_REQUIRED, INTEGRITY_BLOCKED)
_OK_READINESS = (READY, READY_EMPTY, READY_PARTIAL)

# ---------------------------------------------------------------- section status codes
PKG_SELECTED = "SELECTED"
PKG_NOT_AVAILABLE = "PACKAGE_NOT_AVAILABLE"
PKG_STALE = "PACKAGE_STALE"
PKG_CONFLICT = "PACKAGE_CONFLICT"
PKG_BLOCKED = "PACKAGE_BLOCKED"

FU_SELECTED = "SELECTED"
FU_NOT_AVAILABLE = "FOLLOWUP_NOT_AVAILABLE"
FU_STALE = "FOLLOWUP_STALE"
FU_SELECTION_REQUIRED = "FOLLOWUP_SELECTION_REQUIRED"
FU_BLOCKED = "FOLLOWUP_BLOCKED"

REVIEW_OK = "OK"
REVIEW_MISSING = "MISSING"
REVIEW_BLOCKED = "BLOCKED"

TRACKER_OK = "OK"
TRACKER_NOT_INITIALIZED = "NOT_INITIALIZED"
TRK_BLOCKED = "BLOCKED"

# ---------------------------------------------------------------- workspace / limits
_WORKSPACE_SUBDIRS = ("runtime", "validation", "snapshots", "logs")
MAX_ARTIFACT_BYTES = DASH.MAX_ARTIFACT_BYTES
MAX_URL_BYTES = 4096
MAX_QUERY_VALUE = 256
MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 50
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
DEFAULT_PORT = 8780

# ---------------------------------------------------------------- static assets
STATIC_DIR = os.path.join(_HERE, "phase7_operations_dashboard_static")
STATIC_FILES = {
    "index.html": "text/html; charset=utf-8",
    "dashboard.css": "text/css; charset=utf-8",
    "dashboard.js": "text/javascript; charset=utf-8",
}

# Strict same-origin CSP: no inline script, no external anything, no fonts, no framing.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'none'; object-src 'none'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'none'")
PERMISSIONS_POLICY = ("accelerometer=(), autoplay=(), camera=(), display-capture=(), geolocation=(), "
                      "gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=(), "
                      "interest-cohort=()")
SECURITY_HEADERS = (
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Permissions-Policy", PERMISSIONS_POLICY),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cache-Control", "no-store"),
)

# Permanent Amazon / external-network boundary. Every counter is a constant zero — no code path
# can increment them. Aggregated across every upstream authority they must all remain zero.
AMAZON_COUNTERS = {
    "amazon_connections": 0, "amazon_sp_api_calls": 0, "amazon_ads_api_calls": 0,
    "amazon_mutations": 0, "amazon_report_downloads": 0, "amazon_bulk_uploads": 0,
    "browser_automation_actions": 0, "credential_store_count": 0, "external_network_calls": 0,
}
THIS_SESSION_NEVER = {
    "connects_to_amazon": True, "uses_sp_api": True, "uses_ads_api": True,
    "downloads_reports": True, "automates_a_browser": True, "changes_bids": True,
    "changes_budgets": True, "creates_or_pauses_campaigns": True, "adds_keywords": True,
    "adds_or_removes_targets": True, "creates_negatives": True, "uploads_anything": True,
    "executes_phase7_5_candidates": True, "marks_actions_completed": True,
    "modifies_phase7_4_review_state": True, "modifies_phase7_6_tracker_state": True,
    "modifies_phase7_7_outcome_packages": True, "asserts_causation": True,
    "stores_amazon_credentials": True, "makes_network_calls": True, "exposes_server_publicly": True,
}

DISCLAIMER_LINES = (
    "OFFLINE OWNER OPERATIONS SNAPSHOT",
    "No Amazon connection was made.",
    "No Amazon action was performed by this dashboard.",
    "Review statuses, manual-action statuses, and outcome classifications come from their accepted "
    "upstream authorities.",
    "Observational outcome classifications never establish causation.",
    "This export is not an Amazon upload file.",
)


class OperationsError(Exception):
    """A safe, client-facing error carrying an HTTP status and a short code (never a stack trace)."""

    def __init__(self, status, code, detail=""):
        self.status = status
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


# ================================================================ small helpers
def _now_utc():
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt):
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _s(v):
    return "" if v is None else str(v)


def _op_id(source_identity, view, entity_id, content_hash):
    """A deterministic dashboard row identity. Pure function of accepted source identity, the view,
    the accepted stable entity id, and the upstream content hash — never of row position, sort,
    filter, timestamp, mtime, pid, or a random uuid. Client-side sorting/filtering cannot change it."""
    return "op-" + content_sha256({
        "kind": "phase7-8-operations-row-id",
        "source_identity": _s(source_identity),
        "view": view,
        "entity_id": _s(entity_id),
        "content_sha256": _s(content_hash),
    })[:24]


def _rel_display(abs_path):
    """A normalized, relative display path that never leaks an absolute local path. Returns the
    portion of the path from the 'phase7' anchor onward (POSIX separators), else the basename."""
    if not abs_path:
        return None
    norm = str(abs_path).replace("\\", "/")
    parts = norm.split("/")
    if "phase7" in parts:
        return "/".join(parts[parts.index("phase7"):])
    return parts[-1] if parts else norm


def _load_json_file(path, what, *, max_bytes=MAX_ARTIFACT_BYTES):
    """Bounded, strict JSON read (rejects oversize, null bytes, NaN/Infinity). Read-only."""
    if not os.path.isfile(path):
        raise OperationsError(500, "ARTIFACT_MISSING", what)
    if os.path.getsize(path) > max_bytes:
        raise OperationsError(500, "ARTIFACT_OVERSIZE", what)
    with open(path, "rb") as f:
        data = f.read()
    if b"\x00" in data:
        raise OperationsError(500, "NULL_BYTE_REJECTED", what)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise OperationsError(500, "MALFORMED_ENCODING", f"{what}: {e}") from e

    def _reject(tok):
        raise OperationsError(500, "NON_FINITE_NUMBER_REJECTED", f"{what}: {tok}")

    try:
        return json.loads(text, parse_constant=_reject)
    except ValueError as e:
        raise OperationsError(500, "MALFORMED_JSON", f"{what}: {e}") from e


def _sha_file(path):
    import hashlib
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# ================================================================ loopback / host validation
def is_loopback_host(host):
    """True only for an address that is provably loopback. IP literals are checked directly; the
    special name 'localhost' is resolved and every resolved address must be loopback. Any other DNS
    name is refused WITHOUT a network query (so no external DNS lookup is ever performed)."""
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
    raise OperationsError(400, "NON_LOOPBACK_BIND_REFUSED",
                          f"{host!r}: the operations dashboard binds to a loopback address only "
                          "(127.0.0.1 / ::1 / localhost).")


def validate_port(port):
    try:
        p = int(port)
    except (TypeError, ValueError):
        raise OperationsError(400, "INVALID_PORT", str(port))
    if p < 0 or p > 65535:
        raise OperationsError(400, "INVALID_PORT", str(port))
    return p


# ================================================================ configuration
class Config:
    __slots__ = ("base_dir", "phase7_3_dir", "phase7_4_dir", "phase7_5_dir", "phase7_6_dir",
                 "phase7_7_dir", "reference_date", "package_id", "followup_id")

    def __init__(self, base_dir, phase7_3_dir, phase7_4_dir, phase7_5_dir, phase7_6_dir,
                 phase7_7_dir, *, reference_date=None, package_id=None, followup_id=None):
        self.base_dir = os.path.abspath(base_dir)
        self.phase7_3_dir = os.path.abspath(phase7_3_dir)
        self.phase7_4_dir = os.path.abspath(phase7_4_dir)
        self.phase7_5_dir = os.path.abspath(phase7_5_dir)
        self.phase7_6_dir = os.path.abspath(phase7_6_dir)
        self.phase7_7_dir = os.path.abspath(phase7_7_dir)
        self.reference_date = reference_date
        self.package_id = package_id
        self.followup_id = followup_id


# ================================================================ section: analysis (7.3) + review (7.4)
def _derive_row_attribution_windows(row):
    ev = row.get("evidence_detail")
    windows = set()
    if isinstance(ev, dict):
        states = ev.get("metric_states")
        if isinstance(states, dict):
            for field in states:
                m = FU._METRIC_STATE_WINDOW_RE.search(field)
                if m:
                    windows.add(int(m.group(1)))
    return [f"{d} day" for d in sorted(windows)]


def load_analysis_and_review(config):
    """Load Phase 7.3 (via the accepted Phase 7.4 read authority) and the local Phase 7.4 review
    state. Read-only. Returns a dict describing both sections without ever mutating a source file."""
    source = DASH.load_source(config.phase7_3_dir)
    section = {"source": source, "ok": source.ok, "readiness": source.readiness,
               "reasons": list(source.reasons)}

    if not source.ok:
        section.update({"views": None, "overview": None, "analysis_rows": [], "review": None,
                        "review_records": {}})
        return section

    # analysis readiness must be the accepted "ready for owner review" state.
    if source.analysis.get("analysis_readiness") != AA.ANALYSIS_READY:
        section["ok"] = False
        section["readiness"] = DASH.SOURCE_BLOCKED
        section["reasons"] = [{"code": "ANALYSIS_NOT_READY",
                               "detail": source.analysis.get("analysis_readiness")}]
        section.update({"views": None, "overview": None, "analysis_rows": [], "review": None,
                        "review_records": {}})
        return section

    views = DASH.build_views(source)

    # ---- Phase 7.4 review state: strict structural validation first (blocking), then the accepted
    #      merge for enriched per-record source-change state.
    review = {"status": REVIEW_MISSING, "records": [], "aggregate_sha256": content_sha256({}),
              "reasons": [], "counts": {}}
    review_records = {}
    try:
        rs_status, doc = PKG.load_review_state_strict(config.phase7_4_dir)
    except PKG.DecisionPackageError as e:
        review["status"] = REVIEW_BLOCKED
        review["reasons"] = [{"code": e.code, "detail": e.detail}]
        section.update({"views": views, "overview": None, "analysis_rows": [], "review": review,
                        "review_records": {}})
        section["review_blocked"] = True
        return section

    raw_records = doc.get("records", {})
    review["aggregate_sha256"] = PKG.review_state_aggregate_sha256(raw_records)
    if rs_status == "OK" or raw_records:
        review["status"] = REVIEW_OK if raw_records else REVIEW_MISSING
    merged = DASH.merge_review_state(config.phase7_4_dir, source, views)
    review_records = merged

    entity_index = {}
    for key in ("campaigns", "ad_groups", "targets", "search_terms",
                "recommendations", "blocked", "manual_review"):
        for r in views[key]:
            entity_index[r["entity_id"]] = r

    review_rows = []
    for entity_id in sorted(merged):
        rec = merged[entity_id]
        cur = entity_index.get(entity_id)
        content_match = bool(cur) and rec.get("content_sha256") == cur.get("content_sha256")
        manifest_match = rec.get("source_manifest_sha256") in (None, source.manifest_hash)
        review_rows.append({
            "row_id": _op_id(source.manifest_hash, "review", entity_id, rec.get("content_sha256")),
            "entity_id": entity_id,
            "entity_type": rec.get("entity_type"),
            "recommendation_id": (cur or {}).get("recommendation_id") or entity_id,
            "review_status": rec.get("review_status"),
            "effective_status": rec.get("effective_status"),
            "owner_note": rec.get("owner_note", ""),
            "source_change_state": rec.get("source_change_state"),
            "source_manifest_match": manifest_match,
            "content_hash_match": content_match,
            "requires_re_review": bool(rec.get("requires_re_review")),
            "revision": rec.get("revision"),
        })
    approved = sum(1 for r in review_rows if r["review_status"] == "APPROVED_FOR_MANUAL_ACTION")
    deferred = sum(1 for r in review_rows if r["review_status"] == "DEFERRED")
    status_counts = {}
    for r in review_rows:
        status_counts[r["review_status"]] = status_counts.get(r["review_status"], 0) + 1
    review["records"] = review_rows
    review["counts"] = {"review_state_records": len(review_rows), "approved_reviews": approved,
                        "deferred_reviews": deferred, "status_counts": status_counts,
                        "source_changed": sum(1 for r in review_rows
                                              if r["source_change_state"] not in (None, "CURRENT"))}

    # ---- analysis rows: reuse the accepted Phase 7.4 search-term view verbatim, annotated with the
    #      owner's review status and the derived (never inferred) attribution window(s).
    analysis_rows = []
    for r in views["search_terms"]:
        eid = r["entity_id"]
        rec = merged.get(eid)
        wins = _derive_row_attribution_windows(r)
        analysis_rows.append({
            "row_id": _op_id(source.manifest_hash, "analysis", eid, r["content_sha256"]),
            "entity_id": eid,
            "campaign": r.get("campaign"),
            "ad_group": r.get("ad_group"),
            "target": r.get("target"),
            "customer_search_term": r.get("customer_search_term"),
            "match_type": r.get("match_type"),
            "currency": r.get("currency"),
            "start_date": r.get("start_date"),
            "end_date": r.get("end_date"),
            "date_period": (f"{r.get('start_date')} to {r.get('end_date')}"
                            if r.get("start_date") and r.get("end_date") else None),
            "impressions": r.get("impressions"),
            "clicks": r.get("clicks"),
            "spend": r.get("spend"),
            "orders": r.get("orders"),
            "sales": r.get("sales"),
            "acos": r.get("acos"),
            "roas": r.get("roas"),
            "classification": r.get("primary_classification"),
            "attribution_window": wins[0] if len(wins) == 1 else None,
            "attribution_windows": wins,
            "reason": r.get("reason"),
            "review_status": rec.get("review_status") if rec else "UNREVIEWED",
        })

    overview = DASH.build_overview(source, views, merged)
    section.update({"views": views, "overview": overview, "analysis_rows": analysis_rows,
                    "review": review, "review_records": review_records})
    return section


# ================================================================ section: decision packages (7.5)
def discover_packages(phase7_5_dir):
    """Discover and integrity-validate every Phase 7.5 package via the ACCEPTED Phase 7.6 reader
    (``phase7_manual_action_tracker.load_package``). Never selects by mtime; returns them sorted by
    directory name for determinism. Read-only."""
    root = TRK.packages_dir(phase7_5_dir)
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        pkg_dir = os.path.join(root, name)
        if not os.path.isfile(os.path.join(pkg_dir, TRK.MANIFEST_FILE)):
            continue
        pkg = TRK.load_package(phase7_5_dir, dir_name=name)
        if pkg is None:
            continue
        out.append(pkg)
    return out


def _package_lineage_match(pkg, source_manifest_hash, review_aggregate):
    m = pkg.manifest if isinstance(pkg.manifest, dict) else {}
    return (m.get("phase7_3_manifest_sha256") == source_manifest_hash
            and m.get("phase7_4_review_state_aggregate_sha256") == review_aggregate)


def select_package(packages, source_manifest_hash, review_aggregate, *, package_id=None):
    """Select the single current package whose accepted lineage matches the currently validated
    Phase 7.3 + Phase 7.4 authorities. Deterministic; never uses mtime."""
    # explicit --package-id: validate and select exactly that one.
    if package_id is not None:
        chosen = [p for p in packages if p.package_id == package_id]
        if not chosen:
            return {"status": PKG_NOT_AVAILABLE, "package": None,
                    "reasons": [{"code": "PACKAGE_ID_NOT_FOUND", "detail": package_id}],
                    "candidate_count": 0}
        p = chosen[0]
        if p.error or not p.integrity_ok:
            return {"status": PKG_BLOCKED, "package": None,
                    "reasons": [{"code": "PACKAGE_INTEGRITY",
                                 "detail": p.error or p.integrity_detail}], "candidate_count": 1}
        if not _package_lineage_match(p, source_manifest_hash, review_aggregate):
            return {"status": PKG_STALE, "package": None,
                    "reasons": [{"code": "PACKAGE_LINEAGE_MISMATCH", "detail": package_id}],
                    "candidate_count": 1}
        return {"status": PKG_SELECTED, "package": p, "reasons": [], "candidate_count": 1}

    current = [p for p in packages if _package_lineage_match(p, source_manifest_hash, review_aggregate)]
    if not current:
        if packages:
            return {"status": PKG_STALE, "package": None,
                    "reasons": [{"code": "NO_CURRENT_PACKAGE",
                                 "detail": "no discovered package matches the current 7.3/7.4 lineage"}],
                    "candidate_count": len(packages)}
        return {"status": PKG_NOT_AVAILABLE, "package": None, "reasons": [], "candidate_count": 0}

    # a current package that fails integrity is a tampered current package -> blocking.
    corrupt = [p for p in current if p.error or not p.integrity_ok]
    if corrupt:
        return {"status": PKG_BLOCKED, "package": None,
                "reasons": [{"code": "PACKAGE_INTEGRITY",
                             "detail": corrupt[0].error or corrupt[0].integrity_detail}],
                "candidate_count": len(current)}

    distinct = sorted({p.content_sha256 for p in current})
    if len(distinct) > 1:
        return {"status": PKG_CONFLICT, "package": None,
                "reasons": [{"code": "PACKAGE_CONFLICT",
                             "detail": f"{len(distinct)} conflicting current packages"}],
                "candidate_count": len(current)}
    # one distinct content hash: byte-identical duplicates collapse to the first by dir name.
    chosen = sorted([p for p in current if p.content_sha256 == distinct[0]],
                    key=lambda p: p.dir_name)[0]
    return {"status": PKG_SELECTED, "package": chosen, "reasons": [],
            "candidate_count": len(current), "duplicate_identical": len(current) - 1}


def build_package_section(config, source_manifest_hash, review_aggregate):
    packages = discover_packages(config.phase7_5_dir)
    sel = select_package(packages, source_manifest_hash, review_aggregate,
                         package_id=config.package_id)
    section = {"status": sel["status"], "reasons": sel["reasons"],
               "discovered_count": len(packages), "candidate_count": sel.get("candidate_count", 0),
               "duplicate_identical": sel.get("duplicate_identical", 0),
               "package": None, "eligible_items": [], "excluded_items": [], "counts": {}}
    pkg = sel["package"]
    if pkg is None:
        return section

    m = pkg.manifest
    excluded = []
    try:
        exdoc = _load_json_file(os.path.join(pkg.pkg_dir, "excluded_items.json"), "excluded_items.json")
        excluded = exdoc.get("excluded_items", []) if isinstance(exdoc, dict) else []
    except OperationsError:
        excluded = []

    eligible_rows = []
    for it in pkg.items:
        if not isinstance(it, dict):
            continue
        eligible_rows.append({
            "row_id": _op_id(source_manifest_hash, "decision", it.get("package_item_id"),
                             it.get("content_sha256")),
            "package_item_id": it.get("package_item_id"),
            "entity_id": it.get("entity_id"),
            "entity_type": it.get("entity_type"),
            "recommendation_type": it.get("recommendation_type"),
            "action_description": it.get("action_description"),
            "campaign": it.get("campaign"),
            "ad_group": it.get("ad_group"),
            "target": it.get("target"),
            "customer_search_term": it.get("customer_search_term"),
            "match_type": it.get("match_type"),
            "currency": it.get("currency"),
            "attribution_window": it.get("attribution_window"),
            "spend": it.get("spend"),
            "sales": it.get("sales"),
            "acos": it.get("acos"),
            "review_status": it.get("review_status"),
            "content_sha256": it.get("content_sha256"),
        })
    excluded_rows = []
    for ex in excluded:
        if not isinstance(ex, dict):
            continue
        excluded_rows.append({
            "row_id": _op_id(source_manifest_hash, "decision_excluded", ex.get("entity_id"),
                             ex.get("reason_code")),
            "entity_id": ex.get("entity_id"),
            "entity_type": ex.get("entity_type"),
            "review_status": ex.get("review_status"),
            "reason_code": ex.get("reason_code"),
            "reason_detail": ex.get("reason_detail"),
            "source_change_state": ex.get("source_change_state"),
            "campaign": ex.get("campaign"),
            "ad_group": ex.get("ad_group"),
            "target": ex.get("target"),
            "customer_search_term": ex.get("customer_search_term"),
            "currency": ex.get("currency"),
        })

    section["package"] = {
        "package_id": pkg.package_id,
        "dir_display": _rel_display(pkg.pkg_dir),
        "readiness": m.get("readiness"),
        "package_content_sha256": pkg.content_sha256,
        "manifest_file_sha256": pkg.manifest_file_sha256,
        "integrity_ok": pkg.integrity_ok,
        "eligible_action_count": m.get("eligible_action_count"),
        "excluded_item_count": m.get("excluded_item_count"),
        "duplicate_identical_count": m.get("duplicate_identical_count"),
        "duplicate_conflict_count": m.get("duplicate_conflict_count"),
        "source_changed_count": m.get("source_changed_count"),
        "blocked_count": m.get("blocked_count"),
        "policy_required_count": m.get("policy_required_count"),
        "currencies": m.get("currencies", []),
        "attribution_windows": m.get("attribution_windows", []),
        "report_period": m.get("report_period"),
        "phase7_3_manifest_sha256": m.get("phase7_3_manifest_sha256"),
        "phase7_4_review_state_aggregate_sha256": m.get("phase7_4_review_state_aggregate_sha256"),
        "lineage_current": True,
    }
    section["eligible_items"] = eligible_rows
    section["excluded_items"] = excluded_rows
    section["counts"] = {
        "eligible_actions": m.get("eligible_action_count", 0) or 0,
        "excluded_items": m.get("excluded_item_count", 0) or 0,
        "duplicate_identical": m.get("duplicate_identical_count", 0) or 0,
        "duplicate_conflict": m.get("duplicate_conflict_count", 0) or 0,
        "source_changed": m.get("source_changed_count", 0) or 0,
        "policy_required": m.get("policy_required_count", 0) or 0,
    }
    return section


# ================================================================ section: manual actions (7.6)
def build_tracker_section(config, source_manifest_hash):
    """Load + validate Phase 7.6 current state and its full append-only history via the ACCEPTED
    authority. current/history disagreement or a broken chain is blocking. Read-only."""
    section = {"status": TRACKER_OK, "reasons": [], "records": [], "counts": {},
               "state_content_sha256": None, "history_chain_sha256": None, "history_event_count": 0,
               "exists": False}
    try:
        loaded = TRK.load_state(config.phase7_6_dir)
    except TRK.TrackerError as e:
        section["status"] = TRK_BLOCKED
        section["reasons"] = [{"code": e.code, "detail": e.detail}]
        return section

    if not loaded["exists"]:
        section["status"] = TRACKER_NOT_INITIALIZED
        section["counts"] = {"tracker_records": 0, "manually_completed": 0,
                             "pending_manual_check": 0, "stale": 0, "history_revision_count": 0}
        return section

    records = loaded["records"]
    events = loaded["events"]
    binds = TRK._binding_map(records, config.phase7_5_dir)
    rows = []
    for rid in sorted(records):
        rec = records[rid]
        b_state = binds.get(rid, (TRK.B_CURRENT, ""))[0]
        snap = rec.get("source_snapshot", {}) or {}
        rows.append({
            "row_id": _op_id(source_manifest_hash, "tracker", rid, rec.get("record_content_sha256")),
            "tracker_record_id": rid,
            "package_id": rec.get("package_id"),
            "package_item_id": rec.get("package_item_id"),
            "entity_type": rec.get("entity_type"),
            "recommendation_type": rec.get("recommendation_type"),
            "owner_status": rec.get("owner_status"),
            "owner_checked_date": rec.get("owner_checked_date"),
            "owner_completed_date": rec.get("owner_completed_date"),
            "before_value": rec.get("before_value"),
            "before_value_currency": rec.get("before_value_currency"),
            "after_value": rec.get("after_value"),
            "after_value_currency": rec.get("after_value_currency"),
            "owner_note": rec.get("owner_note", ""),
            "campaign": snap.get("campaign"),
            "ad_group": snap.get("ad_group"),
            "target": snap.get("target"),
            "customer_search_term": snap.get("customer_search_term"),
            "binding_state": b_state,
            "stale": b_state in TRK._STALE_BINDINGS,
            "local_revision": rec.get("local_revision"),
            "record_content_sha256": rec.get("record_content_sha256"),
        })
    completed = sum(1 for r in rows if r["owner_status"] == TRK.S_COMPLETED)
    pending = sum(1 for r in rows if r["owner_status"] == TRK.S_PENDING)
    stale = sum(1 for r in rows if r["stale"])
    status_counts = {}
    for r in rows:
        status_counts[r["owner_status"]] = status_counts.get(r["owner_status"], 0) + 1
    section.update({
        "exists": True,
        "records": rows,
        "state_content_sha256": loaded["state"]["state_content_sha256"],
        "history_chain_sha256": loaded["state"]["history_chain_sha256"],
        "history_event_count": len(events),
        "counts": {"tracker_records": len(rows), "manually_completed": completed,
                   "pending_manual_check": pending, "stale": stale,
                   "history_revision_count": len(events), "status_counts": status_counts},
    })
    return section


# ================================================================ section: outcome follow-up (7.7)
_FU_MANIFEST_FILE = "followup_manifest.json"
_FU_MANIFEST_SCHEMA = FU.MANIFEST_SCHEMA


class _Followup:
    __slots__ = ("dir_name", "final_dir", "manifest", "package_id", "integrity_ok",
                 "integrity_detail", "error")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def _load_followup(final_dir):
    """Integrity-validate a Phase 7.7 follow-up package from its own bytes (schema + every declared
    artifact re-hashed against the manifest). Thin infra only — no 7.7 business logic is reproduced."""
    fu = _Followup(dir_name=os.path.basename(final_dir), final_dir=final_dir, integrity_ok=False,
                   manifest=None, error=None, integrity_detail="")
    mpath = os.path.join(final_dir, _FU_MANIFEST_FILE)
    try:
        manifest = _load_json_file(mpath, "FOLLOWUP_MANIFEST")
    except OperationsError as e:
        fu.error = f"{e.code}: {e.detail}"
        return fu
    if not isinstance(manifest, dict) or manifest.get("schema_version") != _FU_MANIFEST_SCHEMA:
        fu.error = "FOLLOWUP_MANIFEST_SCHEMA"
        return fu
    fu.manifest = manifest
    fu.package_id = manifest.get("followup_package_id")
    artifact_hashes = manifest.get("artifact_sha256")
    if not isinstance(artifact_hashes, dict):
        fu.error = "FOLLOWUP_MANIFEST_NO_ARTIFACT_HASHES"
        return fu
    for name in sorted(artifact_hashes):
        apath = os.path.join(final_dir, name)
        if not os.path.isfile(apath):
            fu.integrity_detail = f"FOLLOWUP_ARTIFACT_MISSING: {name}"
            return fu
        if _sha_file(apath) != artifact_hashes[name]:
            fu.integrity_detail = f"FOLLOWUP_HASH_MISMATCH: {name}"
            return fu
    fu.integrity_ok = True
    return fu


def discover_followups(phase7_7_dir):
    root = os.path.join(os.path.abspath(phase7_7_dir), "followups")
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        final_dir = os.path.join(root, name)
        if not os.path.isfile(os.path.join(final_dir, _FU_MANIFEST_FILE)):
            continue
        out.append(_load_followup(final_dir))
    return out


def _followup_lineage_match(fu, source_identity, tracker_state_sha):
    m = fu.manifest if isinstance(fu.manifest, dict) else {}
    return (m.get("source_identity_sha256") == source_identity
            and m.get("tracker_state_sha256") == tracker_state_sha)


def select_followup(followups, source_identity, tracker_state_sha, *, followup_id=None):
    if followup_id is not None:
        chosen = [f for f in followups if f.package_id == followup_id]
        if not chosen:
            return {"status": FU_NOT_AVAILABLE, "followup": None,
                    "reasons": [{"code": "FOLLOWUP_ID_NOT_FOUND", "detail": followup_id}],
                    "candidate_count": 0}
        f = chosen[0]
        if f.error or not f.integrity_ok:
            return {"status": FU_BLOCKED, "followup": None,
                    "reasons": [{"code": "FOLLOWUP_INTEGRITY",
                                 "detail": f.error or f.integrity_detail}], "candidate_count": 1}
        if not _followup_lineage_match(f, source_identity, tracker_state_sha):
            return {"status": FU_STALE, "followup": None,
                    "reasons": [{"code": "FOLLOWUP_LINEAGE_MISMATCH", "detail": followup_id}],
                    "candidate_count": 1}
        return {"status": FU_SELECTED, "followup": f, "reasons": [], "candidate_count": 1}

    current = [f for f in followups if _followup_lineage_match(f, source_identity, tracker_state_sha)]
    if not current:
        if followups:
            return {"status": FU_STALE, "followup": None,
                    "reasons": [{"code": "NO_CURRENT_FOLLOWUP",
                                 "detail": "no discovered follow-up matches the current 7.3/7.6 lineage"}],
                    "candidate_count": len(followups)}
        return {"status": FU_NOT_AVAILABLE, "followup": None, "reasons": [], "candidate_count": 0}

    corrupt = [f for f in current if f.error or not f.integrity_ok]
    if corrupt:
        return {"status": FU_BLOCKED, "followup": None,
                "reasons": [{"code": "FOLLOWUP_INTEGRITY",
                             "detail": corrupt[0].error or corrupt[0].integrity_detail}],
                "candidate_count": len(current)}

    distinct = sorted({f.package_id for f in current})
    if len(distinct) > 1:
        return {"status": FU_SELECTION_REQUIRED, "followup": None,
                "reasons": [{"code": "MULTIPLE_CURRENT_FOLLOWUPS",
                             "detail": ", ".join(distinct)}], "candidate_count": len(current)}
    chosen = sorted([f for f in current if f.package_id == distinct[0]],
                    key=lambda f: f.dir_name)[0]
    return {"status": FU_SELECTED, "followup": chosen, "reasons": [],
            "candidate_count": len(current), "duplicate_identical": len(current) - 1}


def build_followup_section(config, source_identity, tracker_state_sha):
    followups = discover_followups(config.phase7_7_dir)
    sel = select_followup(followups, source_identity, tracker_state_sha,
                          followup_id=config.followup_id)
    section = {"status": sel["status"], "reasons": sel["reasons"],
               "discovered_count": len(followups), "candidate_count": sel.get("candidate_count", 0),
               "duplicate_identical": sel.get("duplicate_identical", 0),
               "followup": None, "followups": [], "excluded": [], "counts": {}}
    fu = sel["followup"]
    if fu is None:
        return section

    m = fu.manifest
    try:
        status_doc = _load_json_file(os.path.join(fu.final_dir, "outcome_status.json"),
                                     "outcome_status.json")
    except OperationsError:
        status_doc = {}
    try:
        excl_doc = _load_json_file(os.path.join(fu.final_dir, "excluded_followups.json"),
                                   "excluded_followups.json")
    except OperationsError:
        excl_doc = {}
    raw_followups = status_doc.get("followups", []) if isinstance(status_doc, dict) else []
    raw_excluded = excl_doc.get("excluded_followups", []) if isinstance(excl_doc, dict) else []

    fu_rows = []
    for f in raw_followups:
        if not isinstance(f, dict):
            continue
        ident = f.get("entity_identity", {}) or {}
        fu_rows.append({
            "row_id": _op_id(source_identity, "outcome", f.get("followup_record_id"),
                             f.get("followup_content_sha256")),
            "followup_record_id": f.get("followup_record_id"),
            "tracker_record_id": f.get("tracker_record_id"),
            "package_item_id": f.get("package_item_id"),
            "recommendation_type": f.get("recommendation_type"),
            "campaign": ident.get("campaign"),
            "ad_group": ident.get("ad_group"),
            "target": ident.get("targeting"),
            "customer_search_term": ident.get("customer_search_term"),
            "match_type": ident.get("match_type"),
            "currency": f.get("currency"),
            "attribution_window": f.get("attribution_window"),
            "marketplace": f.get("marketplace"),
            "before_period": f.get("before_period"),
            "after_period": f.get("after_period"),
            "before_metrics": f.get("before_metrics"),
            "after_metrics": f.get("after_metrics"),
            "absolute_deltas": f.get("absolute_deltas"),
            "percentage_deltas": f.get("percentage_deltas"),
            "outcome_classification": f.get("outcome_classification"),
            "outcome_statement": f.get("outcome_statement"),
            "confidence_status": f.get("confidence_status"),
        })
    ex_rows = []
    for e in raw_excluded:
        if not isinstance(e, dict):
            continue
        ex_rows.append({
            "row_id": _op_id(source_identity, "outcome_excluded", e.get("tracker_record_id"),
                             e.get("exclusion_reason")),
            "tracker_record_id": e.get("tracker_record_id"),
            "package_id": e.get("package_id"),
            "package_item_id": e.get("package_item_id"),
            "recommendation_type": e.get("recommendation_type"),
            "owner_status": e.get("owner_status"),
            "currency": e.get("currency"),
            "attribution_window": e.get("attribution_window"),
            "outcome_classification": e.get("outcome_classification"),
            "exclusion_reason": e.get("exclusion_reason"),
            "exclusion_detail": e.get("exclusion_detail"),
            "binding_state": e.get("binding_state"),
        })

    section["followup"] = {
        "followup_id": fu.package_id,
        "dir_display": _rel_display(fu.final_dir),
        "readiness": m.get("readiness"),
        "reference_date": m.get("reference_date"),
        "before_period": m.get("before_period"),
        "after_period": m.get("after_period"),
        "counts": m.get("counts", {}),
        "policy_id": m.get("policy_id"),
        "material_change_ratio": m.get("material_change_ratio"),
        "source_identity_sha256": m.get("source_identity_sha256"),
        "tracker_state_sha256": m.get("tracker_state_sha256"),
        "causation_asserted": bool(m.get("causation_asserted")),
        "integrity_ok": fu.integrity_ok,
        "lineage_current": True,
    }
    section["followups"] = fu_rows
    section["excluded"] = ex_rows
    counts = m.get("counts", {}) if isinstance(m.get("counts"), dict) else {}
    section["counts"] = {
        "eligible_followups": counts.get("eligible_followup_count", 0) or 0,
        "excluded_followups": counts.get("excluded_count", 0) or 0,
        "reverted": counts.get("reverted_count", 0) or 0,
        "classification_counts": {c: counts.get(f"classified_{c.lower()}", 0)
                                  for c in FU._OUTCOME_CLASSES},
    }
    return section


# ================================================================ attention (consolidated, no invention)
def _attention_item(source_identity, category, entity_id, detail, section, ref=None):
    return {
        "row_id": _op_id(source_identity, "attention:" + category, entity_id, detail),
        "category": category,
        "section": section,
        "reference_id": entity_id,
        "detail": detail,
        "upstream_ref": ref,
    }


def build_attention(source_identity, analysis_section, package_section, tracker_section,
                    followup_section):
    """One consolidated list of ONLY upstream-existing statuses. No new recommendation, priority,
    urgency, or risk score is invented; every entry restates an existing upstream condition."""
    items = []
    sid = source_identity

    # ---- Phase 7.3 / 7.4 -----------------------------------------------------------------------
    views = analysis_section.get("views") or {}
    for r in views.get("blocked_recommendations", []) or []:
        items.append(_attention_item(sid, "review_required", r["entity_id"],
                                     r.get("blocking_reason") or "needs owner review",
                                     "analysis", ref=r.get("entity_id")))
    for r in views.get("policy_requirements", []) or []:
        if r.get("status") == "OWNER_CONFIGURABLE":
            items.append(_attention_item(sid, "policy_required", r["entity_id"],
                                         r.get("requirement"), "analysis"))
    review = analysis_section.get("review") or {}
    if review.get("status") == REVIEW_BLOCKED:
        for rsn in review.get("reasons", []):
            items.append(_attention_item(sid, "review_state_blocked", rsn.get("code"),
                                         rsn.get("detail"), "reviews"))
    for rec in review.get("records", []):
        state = rec.get("source_change_state")
        if state == "SOURCE_CHANGED":
            items.append(_attention_item(sid, "source_changed", rec["entity_id"],
                                         "approved/reviewed evidence changed", "reviews"))
        elif state == "ENTITY_ABSENT":
            items.append(_attention_item(sid, "entity_absent", rec["entity_id"],
                                         "reviewed entity no longer present", "reviews"))
        if rec.get("requires_re_review"):
            items.append(_attention_item(sid, "review_required", rec["entity_id"],
                                         "material commitment requires re-review", "reviews"))

    # ---- Phase 7.5 -----------------------------------------------------------------------------
    ps = package_section
    if ps.get("status") == PKG_STALE:
        items.append(_attention_item(sid, "package_changed", "decision-package",
                                     "no current package matches the 7.3/7.4 lineage", "decision_packages"))
    elif ps.get("status") == PKG_CONFLICT:
        items.append(_attention_item(sid, "decision_conflict", "decision-package",
                                     "multiple conflicting current packages", "decision_packages"))
    elif ps.get("status") == PKG_BLOCKED:
        items.append(_attention_item(sid, "package_integrity", "decision-package",
                                     "; ".join(r.get("detail", "") for r in ps.get("reasons", [])),
                                     "decision_packages"))
    pc = ps.get("counts", {})
    if pc.get("duplicate_conflict"):
        items.append(_attention_item(sid, "decision_conflict", "decision-package",
                                     f"{pc['duplicate_conflict']} duplicate conflict(s)", "decision_packages"))
    if pc.get("policy_required"):
        items.append(_attention_item(sid, "policy_required", "decision-package",
                                     f"{pc['policy_required']} approved record(s) need owner policy",
                                     "decision_packages"))
    if pc.get("source_changed"):
        items.append(_attention_item(sid, "source_changed", "decision-package",
                                     f"{pc['source_changed']} approved record(s) changed at source",
                                     "decision_packages"))

    # ---- Phase 7.6 -----------------------------------------------------------------------------
    ts = tracker_section
    if ts.get("status") == TRK_BLOCKED:
        for rsn in ts.get("reasons", []):
            items.append(_attention_item(sid, "tracker_blocked", rsn.get("code"),
                                         rsn.get("detail"), "manual_actions"))
    for rec in ts.get("records", []):
        if rec.get("owner_status") == TRK.S_PENDING:
            items.append(_attention_item(sid, "pending_manual_check", rec["tracker_record_id"],
                                         "manual action still pending", "manual_actions"))
        if rec.get("stale"):
            cat = ("package_changed" if rec.get("binding_state") in
                   (TRK.B_PACKAGE_CHANGED, TRK.B_PACKAGE_ITEM_CHANGED) else "tracker_stale")
            items.append(_attention_item(sid, cat, rec["tracker_record_id"],
                                         f"binding is {rec.get('binding_state')}", "manual_actions"))

    # ---- Phase 7.7 -----------------------------------------------------------------------------
    fs = followup_section
    if fs.get("status") == FU_BLOCKED:
        for rsn in fs.get("reasons", []):
            items.append(_attention_item(sid, "followup_blocked", rsn.get("code"),
                                         rsn.get("detail"), "outcomes"))
    elif fs.get("status") == FU_SELECTION_REQUIRED:
        items.append(_attention_item(sid, "followup_selection_required", "outcome-followup",
                                     "; ".join(r.get("detail", "") for r in fs.get("reasons", [])),
                                     "outcomes"))
    _EX_CATEGORY = {
        FU.INSUFFICIENT_DATA: "insufficient_followup_data",
        FU.AMBIGUOUS_ENTITY_MATCH: "ambiguous_entity_match",
        FU.ATTRIBUTION_WINDOW_MISMATCH: "attribution_mismatch",
        FU.CURRENCY_MISMATCH: "currency_mismatch",
        FU.ENTITY_NOT_FOUND: "entity_absent",
        FU.WINDOW_NOT_READY_CLASS: "insufficient_followup_data",
    }
    for e in fs.get("excluded", []):
        cls = e.get("outcome_classification")
        cat = _EX_CATEGORY.get(cls)
        if cat:
            items.append(_attention_item(sid, cat, e.get("tracker_record_id"),
                                         e.get("exclusion_detail") or cls, "outcomes"))

    items.sort(key=lambda i: (i["category"], i["section"], _s(i["reference_id"]), i["row_id"]))
    return items


# ================================================================ lineage / integrity
def build_lineage(config, analysis_section, package_section, tracker_section, followup_section):
    source = analysis_section["source"]
    verification = source.verification or {}
    review = analysis_section.get("review") or {}
    pkg = package_section.get("package") or {}
    fu = followup_section.get("followup") or {}
    missing = list(verification.get("missing", []))
    invalid = list(verification.get("mismatched", []))
    return {
        "phase7_3": {
            "source_dir_type": os.path.basename(os.path.normpath(source.source_dir)),
            "source_dir_display": _rel_display(source.source_dir),
            "manifest_sha256": source.manifest_hash,
            "integrity_result": verification.get("result"),
            "manifest_hash_verified": verification.get("manifest_hash_verified"),
            "verified_file_count": verification.get("verified_file_count"),
            "missing_artifacts": missing,
            "invalid_artifacts": invalid,
            "analysis_readiness": source.analysis.get("analysis_readiness"),
        },
        "phase7_4": {
            "review_state_status": review.get("status"),
            "review_state_aggregate_sha256": review.get("aggregate_sha256"),
            "review_state_records": review.get("counts", {}).get("review_state_records", 0),
        },
        "phase7_5": {
            "status": package_section.get("status"),
            "package_id": pkg.get("package_id"),
            "package_content_sha256": pkg.get("package_content_sha256"),
            "manifest_file_sha256": pkg.get("manifest_file_sha256"),
            "discovered_count": package_section.get("discovered_count"),
            "lineage_current": pkg.get("lineage_current", False),
        },
        "phase7_6": {
            "status": tracker_section.get("status"),
            "state_content_sha256": tracker_section.get("state_content_sha256"),
            "history_chain_sha256": tracker_section.get("history_chain_sha256"),
            "history_event_count": tracker_section.get("history_event_count"),
        },
        "phase7_7": {
            "status": followup_section.get("status"),
            "followup_id": fu.get("followup_id"),
            "source_identity_sha256": fu.get("source_identity_sha256"),
            "tracker_state_sha256": fu.get("tracker_state_sha256"),
            "discovered_count": followup_section.get("discovered_count"),
            "lineage_current": fu.get("lineage_current", False),
        },
        "input_directories": {
            "phase7_3": _rel_display(config.phase7_3_dir),
            "phase7_4": _rel_display(config.phase7_4_dir),
            "phase7_5": _rel_display(config.phase7_5_dir),
            "phase7_6": _rel_display(config.phase7_6_dir),
            "phase7_7": _rel_display(config.phase7_7_dir),
        },
    }


# ================================================================ readiness resolution
def resolve_readiness(analysis_section, package_section, tracker_section, followup_section):
    source = analysis_section["source"]
    if not analysis_section["ok"]:
        if source.ok is False and analysis_section["readiness"] == DASH.SOURCE_REQUIRED:
            return SOURCE_REQUIRED
        ver = source.verification or {}
        if ver and ver.get("result") not in (None, "PASS"):
            return INTEGRITY_BLOCKED
        return SOURCE_BLOCKED

    if (analysis_section.get("review") or {}).get("status") == REVIEW_BLOCKED:
        return REVIEW_STATE_BLOCKED
    if package_section.get("status") in (PKG_BLOCKED, PKG_CONFLICT):
        return DECISION_PACKAGE_BLOCKED
    if tracker_section.get("status") == TRK_BLOCKED:
        return TRACKER_BLOCKED
    if followup_section.get("status") == FU_BLOCKED:
        return FOLLOWUP_BLOCKED
    if followup_section.get("status") == FU_SELECTION_REQUIRED:
        return SELECTION_REQUIRED

    partial = (
        package_section.get("status") in (PKG_NOT_AVAILABLE, PKG_STALE)
        or tracker_section.get("status") == TRACKER_NOT_INITIALIZED
        or followup_section.get("status") in (FU_NOT_AVAILABLE, FU_STALE)
    )
    populated = (
        (package_section.get("counts", {}).get("eligible_actions", 0) or 0) > 0
        or (tracker_section.get("counts", {}).get("tracker_records", 0) or 0) > 0
        or (followup_section.get("counts", {}).get("eligible_followups", 0) or 0) > 0
    )
    if partial:
        return READY_PARTIAL
    if populated:
        return READY
    return READY_EMPTY


# ================================================================ full model
def build_operations_model(config, *, now=None):
    """Assemble the complete, deterministic, read-only operations model. Never writes and never
    mutates an upstream artifact."""
    analysis = load_analysis_and_review(config)
    source = analysis["source"]
    source_identity = source.manifest_hash if source.ok else None
    review_aggregate = (analysis.get("review") or {}).get("aggregate_sha256")

    if not analysis["ok"]:
        readiness = resolve_readiness(analysis, {"status": PKG_NOT_AVAILABLE, "counts": {}},
                                      {"status": TRACKER_NOT_INITIALIZED, "counts": {}},
                                      {"status": FU_NOT_AVAILABLE, "counts": {}})
        return {
            "schema_version": MODEL_SCHEMA, "stage_id": STAGE_ID, "stage_name": STAGE_NAME,
            "readiness": readiness, "ok": False, "reasons": analysis["reasons"],
            "overview": _overview_blocked(readiness, analysis),
            "analysis": {"rows": [], "readiness": analysis["readiness"]},
            "reviews": (analysis.get("review") or {}),
            "decision_packages": {"status": PKG_NOT_AVAILABLE},
            "manual_actions": {"status": TRACKER_NOT_INITIALIZED},
            "outcomes": {"status": FU_NOT_AVAILABLE},
            "attention": [],
            "lineage": {"phase7_3": {"integrity_result": (source.verification or {}).get("result"),
                                     "source_dir_display": _rel_display(source.source_dir)}},
            "amazon_counters": dict(AMAZON_COUNTERS),
            "this_session_never": dict(THIS_SESSION_NEVER),
            "disclaimer": list(DISCLAIMER_LINES),
        }

    # tracker state hash needed to select the current 7.7 follow-up.
    tracker = build_tracker_section(config, source_identity)
    tracker_state_sha = (tracker.get("state_content_sha256") if tracker.get("exists")
                         else content_sha256({}))

    package = build_package_section(config, source_identity, review_aggregate)
    followup = build_followup_section(config, source_identity, tracker_state_sha)

    attention = build_attention(source_identity, analysis, package, tracker, followup)
    lineage = build_lineage(config, analysis, package, tracker, followup)
    readiness = resolve_readiness(analysis, package, tracker, followup)
    overview = build_overview(analysis, package, tracker, followup, attention, readiness)

    return {
        "schema_version": MODEL_SCHEMA, "stage_id": STAGE_ID, "stage_name": STAGE_NAME,
        "readiness": readiness, "ok": True, "reasons": [],
        "source_identity_sha256": source_identity,
        "overview": overview,
        "analysis": {"rows": analysis["analysis_rows"],
                     "readiness": source.analysis.get("analysis_readiness"),
                     "filters": _analysis_filters(analysis["analysis_rows"])},
        "reviews": analysis["review"],
        "decision_packages": package,
        "manual_actions": tracker,
        "outcomes": followup,
        "attention": attention,
        "lineage": lineage,
        "amazon_counters": dict(AMAZON_COUNTERS),
        "this_session_never": dict(THIS_SESSION_NEVER),
        "disclaimer": list(DISCLAIMER_LINES),
    }


def _overview_blocked(readiness, analysis):
    source = analysis["source"]
    return {
        "dashboard_readiness": readiness,
        "phase7_3_readiness": (source.analysis.get("analysis_readiness") if source.ok
                               else source.readiness),
        "dashboard_load_status": "BLOCKED",
        "reasons": analysis["reasons"],
        "amazon_counters": dict(AMAZON_COUNTERS),
    }


def build_overview(analysis, package, tracker, followup, attention, readiness):
    ov = analysis["overview"] or {}
    review = analysis["review"] or {}
    rc = review.get("counts", {})
    pc = package.get("counts", {})
    tc = tracker.get("counts", {})
    fc = followup.get("counts", {})
    pkg = package.get("package") or {}
    fu = followup.get("followup") or {}
    return {
        "dashboard_readiness": readiness,
        "dashboard_load_status": "READY",
        "phase7_3_readiness": ov.get("phase7_3_readiness"),
        "phase7_4_review_status": review.get("status"),
        "phase7_5_package_status": package.get("status"),
        "phase7_6_tracker_status": tracker.get("status"),
        "phase7_7_followup_status": followup.get("status"),
        "source_rows": ov.get("source_row_count"),
        "analyzed_rows": ov.get("analyzed_row_count"),
        "review_state_records": rc.get("review_state_records", 0),
        "approved_reviews": rc.get("approved_reviews", 0),
        "deferred_reviews": rc.get("deferred_reviews", 0),
        "decision_package_id": pkg.get("package_id"),
        "eligible_decisions": pc.get("eligible_actions", 0),
        "excluded_decisions": pc.get("excluded_items", 0),
        "tracker_records": tc.get("tracker_records", 0),
        "manually_completed": tc.get("manually_completed", 0),
        "pending_manual_check": tc.get("pending_manual_check", 0),
        "followup_id": fu.get("followup_id"),
        "eligible_followups": fc.get("eligible_followups", 0),
        "excluded_followups": fc.get("excluded_followups", 0),
        "outcome_classification_counts": fc.get("classification_counts", {}),
        "attention_item_count": len(attention),
        "integrity_blockers": _integrity_blockers(analysis, package, tracker, followup),
        "policy_blockers": pc.get("policy_required", 0),
        "source_change_warnings": rc.get("source_changed", 0) + (pc.get("source_changed", 0) or 0),
        "currencies": ov.get("currencies", []),
        "attribution_windows": ov.get("attribution_windows", []),
        "report_date_range": ov.get("report_date_range", {}),
        "amazon_connections": AMAZON_COUNTERS["amazon_connections"],
        "amazon_sp_api_calls": AMAZON_COUNTERS["amazon_sp_api_calls"],
        "amazon_ads_api_calls": AMAZON_COUNTERS["amazon_ads_api_calls"],
        "amazon_mutations": AMAZON_COUNTERS["amazon_mutations"],
        "browser_automation_actions": AMAZON_COUNTERS["browser_automation_actions"],
        "external_network_calls": AMAZON_COUNTERS["external_network_calls"],
    }


def _integrity_blockers(analysis, package, tracker, followup):
    n = 0
    ver = analysis["source"].verification or {}
    if analysis["source"].ok and ver.get("result") not in (None, "PASS"):
        n += 1
    if (analysis.get("review") or {}).get("status") == REVIEW_BLOCKED:
        n += 1
    if package.get("status") in (PKG_BLOCKED, PKG_CONFLICT):
        n += 1
    if tracker.get("status") == TRK_BLOCKED:
        n += 1
    if followup.get("status") == FU_BLOCKED:
        n += 1
    return n


def _analysis_filters(rows):
    def uniq(field):
        return sorted({r.get(field) for r in rows if r.get(field) not in (None, "")})
    return {
        "campaign": uniq("campaign"),
        "ad_group": uniq("ad_group"),
        "target": uniq("target"),
        "match_type": uniq("match_type"),
        "classification": uniq("classification"),
        "currency": uniq("currency"),
        "attribution_window": uniq("attribution_window"),
        "review_status": uniq("review_status"),
    }


# ================================================================ query / pagination (deterministic)
_ANALYSIS_SORT = ("campaign", "ad_group", "target", "customer_search_term", "match_type",
                  "currency", "classification", "review_status", "spend", "sales", "acos",
                  "clicks", "impressions", "orders", "row_id")
_ANALYSIS_FILTER = ("campaign", "ad_group", "target", "customer_search_term", "match_type",
                    "currency", "attribution_window", "classification", "review_status")
_GENERIC_SORT = ("row_id",)


def _paginate(rows, qs, *, sortable, filterable, text_fields):
    """Apply an allowlisted, deterministic filter -> sort -> paginate over `rows`. Raises
    OperationsError(400,...) for any malformed or unknown parameter. Row identities never change."""
    allowed = {"page", "page_size", "sort", "direction", "filter"} | set(filterable)
    for k in qs:
        if k not in allowed:
            raise OperationsError(400, "UNKNOWN_QUERY_PARAM", k)
        if len(qs[k]) > MAX_QUERY_VALUE:
            raise OperationsError(400, "QUERY_VALUE_TOO_LONG", k)

    out = list(rows)

    # exact-match filters
    for field in filterable:
        if field in qs:
            want = qs[field]
            out = [r for r in out if _s(r.get(field)) == want]

    # free-text substring filter across a small allowlist of text fields
    if "filter" in qs and qs["filter"]:
        needle = qs["filter"].lower()
        out = [r for r in out
               if any(needle in _s(r.get(f)).lower() for f in text_fields)]

    # sort (deterministic, row_id tie-break)
    sort = qs.get("sort")
    direction = qs.get("direction", "asc")
    if direction not in ("asc", "desc"):
        raise OperationsError(400, "INVALID_DIRECTION", direction)
    if sort is not None:
        if sort not in sortable:
            raise OperationsError(400, "INVALID_SORT", sort)
        out.sort(key=lambda r: (_s(r.get(sort)), r.get("row_id", "")),
                 reverse=(direction == "desc"))

    # pagination
    total = len(out)
    try:
        page = int(qs.get("page", "1"))
    except ValueError:
        raise OperationsError(400, "INVALID_PAGE", qs.get("page"))
    try:
        page_size = int(qs.get("page_size", str(DEFAULT_PAGE_SIZE)))
    except ValueError:
        raise OperationsError(400, "INVALID_PAGE_SIZE", qs.get("page_size"))
    if page < 1:
        raise OperationsError(400, "INVALID_PAGE", str(page))
    if page_size < 1:
        raise OperationsError(400, "INVALID_PAGE_SIZE", str(page_size))
    page_size = min(page_size, MAX_PAGE_SIZE)   # enforce the maximum (clamp)
    start = (page - 1) * page_size
    window = out[start:start + page_size]
    return {
        "data": window,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size if page_size else 0,
    }


# ================================================================ exports (deterministic, local)
_TSV_CELL = DASH._tsv_cell   # reuse the accepted Phase 7.4–7.7 formula-injection rule verbatim

_OPS_COLUMNS = ("section", "row_id", "entity_type", "identity", "currency", "status", "detail")
_SECTION_ORDER = {"analysis": 0, "reviews": 1, "decision_packages": 2, "manual_actions": 3,
                  "outcomes": 4, "attention": 5}


def _identity_text(*parts):
    vals = [p for p in parts if p not in (None, "")]
    return " / ".join(str(v) for v in vals) if vals else None


def operations_rows(model):
    """A flat, deterministic operations table used by the TSV / Markdown exports. Every row carries
    exactly the same columns; ordering is by (section, row_id)."""
    rows = []
    for r in model["analysis"]["rows"]:
        rows.append({"section": "analysis", "row_id": r["row_id"], "entity_type": "search_term",
                     "identity": _identity_text(r.get("campaign"), r.get("ad_group"),
                                                r.get("target") or r.get("customer_search_term")),
                     "currency": r.get("currency"), "status": r.get("classification"),
                     "detail": r.get("review_status")})
    for r in model["reviews"].get("records", []):
        rows.append({"section": "reviews", "row_id": r["row_id"], "entity_type": r.get("entity_type"),
                     "identity": r.get("recommendation_id"), "currency": None,
                     "status": r.get("review_status"), "detail": r.get("source_change_state")})
    dp = model["decision_packages"]
    for r in dp.get("eligible_items", []):
        rows.append({"section": "decision_packages", "row_id": r["row_id"],
                     "entity_type": r.get("recommendation_type"),
                     "identity": _identity_text(r.get("campaign"), r.get("ad_group"),
                                                r.get("customer_search_term")),
                     "currency": r.get("currency"), "status": "ELIGIBLE",
                     "detail": r.get("action_description")})
    for r in dp.get("excluded_items", []):
        rows.append({"section": "decision_packages", "row_id": r["row_id"],
                     "entity_type": r.get("entity_type"),
                     "identity": _identity_text(r.get("campaign"), r.get("ad_group"),
                                                r.get("customer_search_term")),
                     "currency": r.get("currency"), "status": "EXCLUDED",
                     "detail": r.get("reason_code")})
    for r in model["manual_actions"].get("records", []):
        rows.append({"section": "manual_actions", "row_id": r["row_id"],
                     "entity_type": r.get("recommendation_type"),
                     "identity": _identity_text(r.get("campaign"), r.get("ad_group"),
                                                r.get("customer_search_term")),
                     "currency": r.get("before_value_currency") or r.get("after_value_currency"),
                     "status": r.get("owner_status"), "detail": r.get("binding_state")})
    for r in model["outcomes"].get("followups", []):
        rows.append({"section": "outcomes", "row_id": r["row_id"],
                     "entity_type": r.get("recommendation_type"),
                     "identity": _identity_text(r.get("campaign"), r.get("ad_group"),
                                                r.get("customer_search_term")),
                     "currency": r.get("currency"), "status": r.get("outcome_classification"),
                     "detail": r.get("confidence_status")})
    for r in model["outcomes"].get("excluded", []):
        rows.append({"section": "outcomes", "row_id": r["row_id"],
                     "entity_type": r.get("recommendation_type"), "identity": r.get("tracker_record_id"),
                     "currency": r.get("currency"), "status": "EXCLUDED",
                     "detail": r.get("exclusion_reason")})
    for r in model["attention"]:
        rows.append({"section": "attention", "row_id": r["row_id"], "entity_type": r.get("category"),
                     "identity": r.get("reference_id"), "currency": None,
                     "status": r.get("category"), "detail": r.get("detail")})
    rows.sort(key=lambda r: (_SECTION_ORDER.get(r["section"], 99), r["row_id"]))
    return rows


def export_json(model):
    payload = {
        "meta": {
            "schema_version": EXPORT_SCHEMA,
            "stage_id": STAGE_ID,
            "stage_name": STAGE_NAME,
            "dashboard_readiness": model["readiness"],
            "disclaimer": list(DISCLAIMER_LINES),
            "amazon_action_performed": False,
            "causation_asserted": False,
            "is_amazon_upload_file": False,
            "amazon_counters": dict(AMAZON_COUNTERS),
        },
        "overview": model["overview"],
        "reviews": model["reviews"].get("records", []),
        "decision_packages": {"package": model["decision_packages"].get("package"),
                              "eligible_items": model["decision_packages"].get("eligible_items", []),
                              "excluded_items": model["decision_packages"].get("excluded_items", [])},
        "manual_actions": model["manual_actions"].get("records", []),
        "outcomes": {"followups": model["outcomes"].get("followups", []),
                     "excluded": model["outcomes"].get("excluded", [])},
        "attention": model["attention"],
        "lineage": model["lineage"],
        "analysis_row_count": len(model["analysis"]["rows"]),
        "operations_rows": operations_rows(model),
    }
    return canonical_json(payload) + "\n"


def export_tsv(model):
    lines = [f"# {line}" for line in DISCLAIMER_LINES]
    lines.append(f"# dashboard_readiness\t{model['readiness']}")
    lines.append(f"# analysis_rows\t{len(model['analysis']['rows'])}")
    lines.append("\t".join(_OPS_COLUMNS))
    for r in operations_rows(model):
        lines.append("\t".join(_TSV_CELL(r.get(c)) for c in _OPS_COLUMNS))
    return "\n".join(lines) + "\n"


def export_markdown(model):
    ov = model["overview"]
    out = ["# Offline Owner Operations Snapshot", "", "> **OFFLINE OWNER OPERATIONS SNAPSHOT**"]
    for line in DISCLAIMER_LINES[1:]:
        out.append("> " + line)
    out += ["", f"- Dashboard readiness: `{model['readiness']}`",
            f"- Source rows: {ov.get('source_rows')} · Analyzed rows: {ov.get('analyzed_rows')}",
            f"- Review records: {ov.get('review_state_records')} "
            f"(approved {ov.get('approved_reviews')}, deferred {ov.get('deferred_reviews')})",
            f"- Decision package: {ov.get('decision_package_id')} "
            f"(eligible {ov.get('eligible_decisions')}, excluded {ov.get('excluded_decisions')})",
            f"- Tracker records: {ov.get('tracker_records')} "
            f"(completed {ov.get('manually_completed')}, pending {ov.get('pending_manual_check')})",
            f"- Follow-up: {ov.get('followup_id')} "
            f"(eligible {ov.get('eligible_followups')}, excluded {ov.get('excluded_followups')})",
            f"- Attention items: {ov.get('attention_item_count')}", "",
            "| " + " | ".join(_OPS_COLUMNS) + " |",
            "| " + " | ".join("---" for _ in _OPS_COLUMNS) + " |"]
    for r in operations_rows(model):
        cells = []
        for c in _OPS_COLUMNS:
            v = r.get(c)
            cells.append("—" if v in (None, "") else str(v).replace("|", "\\|").replace("\n", " "))
        out.append("| " + " | ".join(cells) + " |")
    out.append("")
    return "\n".join(out)


EXPORTERS = {"json": (export_json, "application/json; charset=utf-8"),
             "tsv": (export_tsv, "text/tab-separated-values; charset=utf-8"),
             "md": (export_markdown, "text/markdown; charset=utf-8")}


# ================================================================ workspace / snapshot
def ensure_workspace(base_dir):
    base = os.path.abspath(base_dir)
    for sub in _WORKSPACE_SUBDIRS:
        os.makedirs(os.path.join(base, sub), exist_ok=True)
    return base


def write_snapshot(base_dir, model, fmt):
    """Write a deterministic operations snapshot ONLY under the Phase 7.8 snapshots/ directory.
    Never writes into any Phase 7.3–7.7 directory. Returns the written path."""
    if fmt not in EXPORTERS:
        raise OperationsError(400, "UNSUPPORTED_SNAPSHOT_FORMAT", fmt)
    ext = {"json": "json", "tsv": "tsv", "md": "md"}[fmt]
    content = EXPORTERS[fmt][0](model)
    base = ensure_workspace(base_dir)
    snap_dir = os.path.join(base, "snapshots")
    path = os.path.join(snap_dir, f"operations_snapshot.{ext}")
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(content.encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


# ================================================================ HTTP server
_LIST_ENDPOINTS = ("/api/analysis", "/api/reviews", "/api/decision-packages",
                   "/api/manual-actions", "/api/outcomes", "/api/attention")
_EXPORT_ENDPOINTS = {"/api/export/operations.json": "json",
                     "/api/export/operations.tsv": "tsv",
                     "/api/export/operations.md": "md"}


class OperationsServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, *, config, now=None):
        self.config = config
        self.now = now or _now_utc
        # loopback names + the bound port form the Host-header allowlist.
        super().__init__(address, handler)

    def model(self):
        return build_operations_model(self.config, now=self.now)


def _make_handler():
    class Handler(BaseHTTPRequestHandler):
        server_version = "Phase78OperationsDashboard/1.0"
        protocol_version = "HTTP/1.1"
        timeout = 15

        def log_message(self, *a):   # no telemetry, no stdout noise
            return

        # -------------------------------------------------- response helpers
        def _headers(self, status, content_type, body_len):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(body_len))
            for k, v in SECURITY_HEADERS:
                self.send_header(k, v)
            self.end_headers()

        def _send_bytes(self, status, content_type, body):
            self._headers(status, content_type, len(body))
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, status=200):
            body = (canonical_json(obj) + "\n").encode("utf-8")
            self._send_bytes(status, "application/json; charset=utf-8", body)

        def _error(self, status, code, detail=""):
            self._json({"error": code, "detail": detail, "amazon_action_performed": False}, status)

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

        def _drain_body(self):
            """Discard a bounded amount of any unexpected request body so a keep-alive connection
            cannot desynchronize; a chunked or over-large body closes the connection instead. The
            dashboard is read-only and never interprets a request body."""
            if (self.headers.get("Transfer-Encoding") or "").strip():
                self.close_connection = True   # we do not decode chunked bodies
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.close_connection = True
                return
            cap = 1 * 1024 * 1024
            to_read = min(max(length, 0), cap)
            while to_read > 0:
                chunk = self.rfile.read(min(65536, to_read))
                if not chunk:
                    break
                to_read -= len(chunk)
            if length > cap:
                self.close_connection = True

        # -------------------------------------------------- method dispatch
        def do_GET(self):
            self._handle(head=False)

        def do_HEAD(self):
            self._handle(head=True)

        def _reject_method(self):
            self._drain_body()
            self._error(405, "METHOD_NOT_ALLOWED", self.command)

        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _reject_method
        do_TRACE = do_CONNECT = _reject_method

        # -------------------------------------------------- request handling
        def _handle(self, *, head):
            try:
                self._drain_body()   # GET/HEAD carry no body; drain any erroneous one safely
                if len(self.requestline) > MAX_URL_BYTES:
                    return self._error(414, "URI_TOO_LONG")
                if not self._host_ok():
                    return self._error(400, "BAD_HOST")
                split = urlsplit(self.path)
                path = split.path
                qs = {}
                for k, v in parse_qsl(split.query, keep_blank_values=True):
                    if k in qs:
                        raise OperationsError(400, "DUPLICATE_QUERY_PARAM", k)
                    qs[k] = v
                if path in ("/", "/index.html"):
                    return self._static("index.html")
                if path.lstrip("/") in STATIC_FILES:
                    return self._static(path.lstrip("/"))
                if path.startswith("/api/"):
                    return self._api(path, qs)
                return self._error(404, "NOT_FOUND", path)
            except OperationsError as e:
                return self._error(e.status, e.code, e.detail)
            except Exception as e:   # noqa: BLE001 — never leak a stack trace to the client
                return self._error(500, "INTERNAL_ERROR", type(e).__name__)

        # -------------------------------------------------- static
        def _static(self, name):
            if name not in STATIC_FILES:
                return self._error(404, "STATIC_NOT_FOUND", name)
            path = os.path.join(STATIC_DIR, name)
            if not os.path.isfile(path):
                return self._error(404, "STATIC_NOT_FOUND", name)
            with open(path, "rb") as f:
                return self._send_bytes(200, STATIC_FILES[name], f.read())

        # -------------------------------------------------- api
        def _api(self, path, qs):
            if path == "/api/health":
                return self._json(self._health())
            model = self.server.model()
            if path in _EXPORT_ENDPOINTS:
                fmt = _EXPORT_ENDPOINTS[path]
                fn, ctype = EXPORTERS[fmt]
                body = fn(model).encode("utf-8")
                return self._send_bytes(200, ctype, body)
            if path == "/api/overview":
                return self._json({"overview": model["overview"], "readiness": model["readiness"],
                                   "ok": model["ok"], "reasons": model.get("reasons", []),
                                   "disclaimer": list(DISCLAIMER_LINES)})
            if path == "/api/lineage":
                return self._json({"lineage": model.get("lineage", {}), "readiness": model["readiness"],
                                   "amazon_counters": dict(AMAZON_COUNTERS)})
            if path in _LIST_ENDPOINTS:
                return self._list(path, qs, model)
            return self._error(404, "UNKNOWN_ENDPOINT", path)

        def _health(self):
            model = self.server.model()
            ov = model["overview"]
            return {
                "stage_id": STAGE_ID, "schema_version": MODEL_SCHEMA,
                "dashboard_readiness": model["readiness"], "ok": model["ok"],
                "phase7_3_readiness": ov.get("phase7_3_readiness"),
                "amazon_counters": dict(AMAZON_COUNTERS),
                "this_session_never": dict(THIS_SESSION_NEVER),
                "amazon_action_performed": False, "causation_asserted": False,
                "server_time": _iso(self.server.now()),   # non-authoritative only
            }

        def _list(self, path, qs, model):
            if path == "/api/analysis":
                res = _paginate(model["analysis"]["rows"], qs, sortable=_ANALYSIS_SORT,
                                filterable=_ANALYSIS_FILTER,
                                text_fields=("campaign", "ad_group", "target",
                                             "customer_search_term", "classification"))
                res["filters"] = model["analysis"]["filters"]
                res["readiness"] = model["analysis"]["readiness"]
                return self._json(res)
            if path == "/api/reviews":
                res = _paginate(model["reviews"].get("records", []), qs, sortable=_GENERIC_SORT,
                                filterable=("review_status", "source_change_state"),
                                text_fields=("entity_id", "recommendation_id", "owner_note"))
                res["counts"] = model["reviews"].get("counts", {})
                res["status"] = model["reviews"].get("status")
                res["notice"] = ("Review updates must be made through the accepted Phase 7.4 "
                                 "owner-review authority.")
                return self._json(res)
            if path == "/api/decision-packages":
                dp = model["decision_packages"]
                res = _paginate(dp.get("eligible_items", []), qs, sortable=_GENERIC_SORT,
                                filterable=("currency", "recommendation_type"),
                                text_fields=("campaign", "ad_group", "customer_search_term"))
                res["package"] = dp.get("package")
                res["status"] = dp.get("status")
                res["reasons"] = dp.get("reasons", [])
                res["excluded_items"] = dp.get("excluded_items", [])
                res["counts"] = dp.get("counts", {})
                return self._json(res)
            if path == "/api/manual-actions":
                ma = model["manual_actions"]
                res = _paginate(ma.get("records", []), qs, sortable=_GENERIC_SORT,
                                filterable=("owner_status", "binding_state"),
                                text_fields=("campaign", "ad_group", "customer_search_term",
                                             "owner_note"))
                res["status"] = ma.get("status")
                res["counts"] = ma.get("counts", {})
                res["reasons"] = ma.get("reasons", [])
                res["notice"] = ("Status values are owner-entered records. No Amazon action was "
                                 "performed by this software.")
                return self._json(res)
            if path == "/api/outcomes":
                oc = model["outcomes"]
                res = _paginate(oc.get("followups", []), qs, sortable=_GENERIC_SORT,
                                filterable=("currency", "outcome_classification"),
                                text_fields=("campaign", "ad_group", "customer_search_term"))
                res["followup"] = oc.get("followup")
                res["status"] = oc.get("status")
                res["excluded"] = oc.get("excluded", [])
                res["counts"] = oc.get("counts", {})
                res["reasons"] = oc.get("reasons", [])
                res["causation_disclaimer"] = ("Observational classifications describe an observed "
                                               "before/after change only and never establish causation.")
                return self._json(res)
            if path == "/api/attention":
                res = _paginate(model["attention"], qs, sortable=("category", "section", "row_id"),
                                filterable=("category", "section"),
                                text_fields=("reference_id", "detail", "category"))
                return self._json(res)
            return self._error(404, "UNKNOWN_ENDPOINT", path)

    return Handler


def build_server(config, *, host="127.0.0.1", port=DEFAULT_PORT, now=None):
    validate_host(host)
    validate_port(port)
    ensure_workspace(config.base_dir)
    return OperationsServer((host, port), _make_handler(), config=config, now=now)


# ================================================================ CLI
def _startup_pairs(model, *, host, port):
    ov = model["overview"]
    ac = AMAZON_COUNTERS
    return [
        ("dashboard_readiness", model["readiness"]),
        ("phase7_3_readiness", ov.get("phase7_3_readiness")),
        ("source_rows", ov.get("source_rows")),
        ("analyzed_rows", ov.get("analyzed_rows")),
        ("review_state_records", ov.get("review_state_records")),
        ("approved_reviews", ov.get("approved_reviews")),
        ("deferred_reviews", ov.get("deferred_reviews")),
        ("decision_package_id", ov.get("decision_package_id")),
        ("eligible_decisions", ov.get("eligible_decisions")),
        ("excluded_decisions", ov.get("excluded_decisions")),
        ("tracker_records", ov.get("tracker_records")),
        ("manually_completed", ov.get("manually_completed")),
        ("pending_manual_check", ov.get("pending_manual_check")),
        ("followup_id", ov.get("followup_id")),
        ("eligible_followups", ov.get("eligible_followups")),
        ("excluded_followups", ov.get("excluded_followups")),
        ("attention_items", ov.get("attention_item_count")),
        ("amazon_connections", ac["amazon_connections"]),
        ("amazon_sp_api_calls", ac["amazon_sp_api_calls"]),
        ("amazon_ads_api_calls", ac["amazon_ads_api_calls"]),
        ("amazon_mutations", ac["amazon_mutations"]),
        ("browser_automation_actions", ac["browser_automation_actions"]),
        ("external_network_calls", ac["external_network_calls"]),
        ("dashboard_url", f"http://{host}:{port}"),
    ]


def format_startup_summary(model, *, host, port):
    return "\n".join(f"{k}={'' if v is None else v}" for k, v in _startup_pairs(model, host=host, port=port))


def exit_code_for(readiness):
    return 1 if readiness in _BLOCKING_READINESS else 0


def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="python -m production.phase7_owner_operations_dashboard",
        description="Offline Owner Operations Dashboard (Phase 7.8). A read-only aggregation and "
                    "navigation layer over the accepted Phase 7.3–7.7 authorities. Binds to a "
                    "loopback address only, connects to nothing, and performs no Amazon action.")
    p.add_argument("--base-dir", default=os.path.join("runs", "T2", "phase7", "7.8"),
                   help="Phase 7.8 workspace (runtime/ validation/ snapshots/ logs/).")
    p.add_argument("--phase7-3-dir", default=os.path.join("runs", "T2", "phase7", "7.3"))
    p.add_argument("--phase7-4-dir", default=os.path.join("runs", "T2", "phase7", "7.4"))
    p.add_argument("--phase7-5-dir", default=os.path.join("runs", "T2", "phase7", "7.5"))
    p.add_argument("--phase7-6-dir", default=os.path.join("runs", "T2", "phase7", "7.6"))
    p.add_argument("--phase7-7-dir", default=os.path.join("runs", "T2", "phase7", "7.7"))
    p.add_argument("--host", default="127.0.0.1", help="loopback bind host (default 127.0.0.1).")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"bind port (default {DEFAULT_PORT}).")
    p.add_argument("--reference-date", default=None, help="Optional reference date (YYYY-MM-DD).")
    p.add_argument("--package-id", default=None, help="Select an explicit Phase 7.5 package id.")
    p.add_argument("--followup-id", default=None, help="Select an explicit Phase 7.7 follow-up id.")
    p.add_argument("--validate-only", action="store_true",
                   help="Validate every source and print the readiness/counts without a server.")
    p.add_argument("--no-browser", action="store_true",
                   help="No-op: this dashboard never opens a browser automatically.")
    p.add_argument("--snapshot-format", choices=("json", "tsv", "md"), default=None,
                   help="Write one operations snapshot under 7.8/snapshots/ and exit (no server).")
    return p


def _config_from_args(a):
    return Config(a.base_dir, a.phase7_3_dir, a.phase7_4_dir, a.phase7_5_dir, a.phase7_6_dir,
                  a.phase7_7_dir, reference_date=a.reference_date, package_id=a.package_id,
                  followup_id=a.followup_id)


def main(argv=None, *, serve=True):
    a = build_arg_parser().parse_args(argv)
    try:
        validate_host(a.host)
        validate_port(a.port)
    except OperationsError as e:
        print(f"{e.code}: {e.detail}", file=sys.stderr)
        return 2

    config = _config_from_args(a)
    model = build_operations_model(config)
    print(format_startup_summary(model, host=a.host, port=a.port))
    if not model["ok"]:
        for reason in model.get("reasons", []):
            print(f"reason={reason.get('code')}: {reason.get('detail')}", file=sys.stderr)

    if a.validate_only:
        return exit_code_for(model["readiness"])

    if a.snapshot_format:
        path = write_snapshot(config.base_dir, model, a.snapshot_format)
        print(f"snapshot={_rel_display(path)}")
        return exit_code_for(model["readiness"])

    if not serve:
        return exit_code_for(model["readiness"])

    try:
        server = build_server(config, host=a.host, port=a.port)
    except OperationsError as e:
        print(f"{e.code}: {e.detail}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"BIND_FAILED: {e}", file=sys.stderr)
        return 2
    print(f"Serving on http://{a.host}:{a.port} — press Ctrl+C to stop.")
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
