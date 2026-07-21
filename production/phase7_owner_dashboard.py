#!/usr/bin/env python3
"""Phase 7.4 — offline, read-only Owner Review Dashboard over promoted Phase 7.3 output.

This module is the ONE Phase 7.4 authority. It is a REVIEW and LOCAL-EXPORT interface only.

It connects to NOTHING. There is no Amazon integration, no SP-API, no Ads API, no browser
automation, no network client of any kind. It never creates, pauses, or changes a campaign, bid,
budget, keyword, negative, or target, and it never uploads anything or emits an API payload. The
owner remains the only manual bridge to Amazon Seller Central.

What it does:
  * reads the promoted Phase 7.3 artifacts (never the raw inbox, never a staging/quarantine copy);
  * re-verifies the Phase 7.3 manifest and every recorded artifact hash from bytes;
  * refuses unsafe or inconsistent source data (malformed JSON, NaN/Infinity, null bytes, oversize,
    duplicate stable IDs, count inconsistency, path traversal);
  * exposes a local read-only HTTP API bound to 127.0.0.1 by default;
  * persists LOCAL owner-review labels (never Amazon actions) keyed by stable Phase 7.3 IDs;
  * exports the owner's review locally (TSV / JSON / Markdown), every export carrying a manual-action
    disclaimer and formula-injection-safe cells.

Financial rules: Decimal only (never float) for any analytical value. Values that arrive from
Phase 7.3 as canonical strings are preserved and never re-computed; the browser formats but never
mutates them. Missing values stay missing (rendered as an em dash), never silently zero.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ("", "core", "production"):
    _p = _ROOT if not _sub else os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import product_workspace as PW          # noqa: E402  canonical json / content hash
from production import phase7_ads_analysis as AA        # noqa: E402  the Phase 7.3 authority
from core import money as MONEY                          # noqa: E402  Decimal / integer authority

canonical_json = PW.canonical_json
content_sha256 = PW.content_sha256

# ================================================================ constants / versions
STAGE_ID = "7.4"
STAGE_NAME = "Offline Owner Review Dashboard"

DASHBOARD_SCHEMA = "phase7-4-owner-dashboard-v1"
REVIEW_STATE_SCHEMA = "phase7-4-review-state-v1"
EXPORT_SCHEMA = "phase7-4-export-v1"

# ---------------------------------------------------------------- readiness states
DASH_READY = "SESSION7_4_DASHBOARD_READY"
SOURCE_REQUIRED = "SESSION7_4_SOURCE_REQUIRED"
SOURCE_BLOCKED = "SESSION7_4_SOURCE_BLOCKED"
SOURCE_CHANGED = "SESSION7_4_SOURCE_CHANGED_REVIEW_REQUIRED"
REVIEW_STATE_READY = "SESSION7_4_REVIEW_STATE_READY"
EXPORT_READY = "SESSION7_4_EXPORT_READY"
EXPORT_BLOCKED = "SESSION7_4_EXPORT_BLOCKED"

# ---------------------------------------------------------------- local owner-review statuses
# These are LOCAL REVIEW LABELS. None of them performs, queues, or authorizes any Amazon action.
REVIEW_STATUSES = (
    "UNREVIEWED", "APPROVED_FOR_MANUAL_ACTION", "REJECTED", "DEFERRED",
    "NEEDS_MORE_DATA", "NEEDS_POLICY", "ALREADY_HANDLED", "NOT_APPLICABLE",
)
DEFAULT_STATUS = "UNREVIEWED"
# statuses whose material commitment must NOT silently carry to a changed source row.
MATERIAL_COMMITMENT_STATUSES = ("APPROVED_FOR_MANUAL_ACTION", "ALREADY_HANDLED")

# ---------------------------------------------------------------- source contract (Phase 7.3)
SOURCE_SUBDIR_CANDIDATES = ("promoted", "final")
F_MANIFEST = "analysis-manifest.json"
F_ANALYSIS = "analysis.json"
EXPECTED_MANIFEST_SCHEMA = "phase7-3-analysis-manifest-v1"
EXPECTED_ANALYSIS_SCHEMA = "phase7-3-analysis-v1"

# reason codes that mean a required metric could not be used / a value was invalid.
BLOCKING_REASON_CODES = ("REQUIRED_METRIC_UNUSABLE", "INVALID_NUMERIC_VALUE")
CL_NEEDS_OWNER_REVIEW = "NEEDS_OWNER_REVIEW"

# ---------------------------------------------------------------- workspace
_WORKSPACE_SUBDIRS = ("review_state", "exports", "manifests", "logs", "runtime")
F_REVIEW_STATE = "review-state.json"

# ---------------------------------------------------------------- limits
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024      # a promoted artifact larger than this is refused
MAX_BODY_BYTES = 1 * 1024 * 1024            # request-body ceiling
MAX_NOTE_CHARS = 4000
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")

# ---------------------------------------------------------------- static assets
STATIC_DIR = os.path.join(_HERE, "phase7_dashboard_static")
STATIC_FILES = {
    "index.html": "text/html; charset=utf-8",
    "dashboard.css": "text/css; charset=utf-8",
    "dashboard.js": "text/javascript; charset=utf-8",
}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'")
SECURITY_HEADERS = (
    ("Content-Security-Policy", CSP),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Cache-Control", "no-store"),
)

# The dashboard's own permanent Amazon boundary. Every counter is a constant zero — the process
# has no code path that could increment them.
AMAZON_COUNTERS = {
    "amazon_connections": 0, "amazon_api_calls": 0, "amazon_mutations": 0,
    "browser_automation_attempts": 0, "credential_store_count": 0, "network_calls": 0,
    "report_download_attempts": 0, "upload_actions": 0,
}
THIS_SESSION_NEVER = {
    "connects_to_amazon": True, "uses_sp_api": True, "uses_ads_api": True,
    "downloads_reports": True, "automates_a_browser": True, "changes_bids": True,
    "changes_budgets": True, "creates_or_pauses_campaigns": True, "adds_keywords": True,
    "adds_or_removes_targets": True, "creates_negatives": True, "uploads_anything": True,
    "stores_amazon_credentials": True, "makes_network_calls": True, "modifies_phase7_3_source": True,
}

DISCLAIMER_LINES = (
    "This file is for manual owner review only.",
    "No Amazon action has been performed.",
    "The owner must independently verify all data before making changes in Seller Central.",
)


class DashboardError(Exception):
    pass


class SourceError(DashboardError):
    """Raised when promoted Phase 7.3 source is missing, unsafe, or inconsistent."""

    def __init__(self, code, detail=""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


# ================================================================ small helpers
def _now_utc():
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt):
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stable_id(prefix, *parts):
    raw = "\x1f".join("" if p is None else str(p) for p in parts)
    return f"{prefix}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def _strict_json_loads(data, *, what):
    """Parse JSON bytes, rejecting oversize, null bytes, and NaN/Infinity tokens."""
    if len(data) > MAX_ARTIFACT_BYTES:
        raise SourceError("ARTIFACT_OVERSIZE", f"{what} exceeds {MAX_ARTIFACT_BYTES} bytes")
    if b"\x00" in data:
        raise SourceError("NULL_BYTE_REJECTED", what)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise SourceError("MALFORMED_ENCODING", f"{what}: {e}") from e

    def _reject_const(token):
        raise SourceError("NON_FINITE_NUMBER_REJECTED", f"{what}: {token}")

    try:
        return json.loads(text, parse_constant=_reject_const)
    except ValueError as e:
        raise SourceError("MALFORMED_JSON", f"{what}: {e}") from e


def _dash(v):
    """Pass a value straight through; None stays None (the frontend renders it as an em dash)."""
    return v


# ================================================================ source resolution & validation
def resolve_source_dir(phase7_3_dir):
    """The directory that actually holds the promoted Phase 7.3 artifacts.

    Accepts either the Phase 7.3 workspace root (``runs/T2/phase7/7.3`` -> its ``promoted/``),
    or a directory that already contains the manifest (used by fixtures)."""
    base = os.path.abspath(phase7_3_dir)
    if os.path.isfile(os.path.join(base, F_MANIFEST)):
        return base
    for sub in SOURCE_SUBDIR_CANDIDATES:
        cand = os.path.join(base, sub)
        if os.path.isfile(os.path.join(cand, F_MANIFEST)):
            return cand
    # Default to promoted/ so the "missing" reason names the canonical path.
    return os.path.join(base, SOURCE_SUBDIR_CANDIDATES[0])


def _assert_inside(source_dir, name):
    """Read only an allowlisted flat filename inside source_dir. Rejects traversal/escape."""
    if name != os.path.basename(name) or name in ("", ".", ".."):
        raise SourceError("UNSAFE_ARTIFACT_NAME", name)
    target = os.path.abspath(os.path.join(source_dir, name))
    if os.path.commonpath([os.path.abspath(source_dir), target]) != os.path.abspath(source_dir):
        raise SourceError("PATH_TRAVERSAL_REJECTED", name)
    return target


def _read_bytes(path, what):
    if not os.path.isfile(path):
        raise SourceError("ARTIFACT_MISSING", what)
    size = os.path.getsize(path)
    if size > MAX_ARTIFACT_BYTES:
        raise SourceError("ARTIFACT_OVERSIZE", f"{what} is {size} bytes")
    with open(path, "rb") as f:
        return f.read()


def verify_source_integrity(source_dir, manifest):
    """Re-verify a promoted Phase 7.3 state from its own bytes, using the producer's hash math.

    Mirrors ``phase7_ads_analysis`` exactly: the manifest's deterministic hash is recomputed after
    stripping the volatile keys, then every artifact recorded in ``output_hashes`` is re-hashed.
    Reads only — never writes, moves, or removes a promoted file."""
    volatile = set(AA._VOLATILE) | {"output_hashes"}
    stored = manifest.get("deterministic_content_sha256")
    recomputed = content_sha256(AA._recursive_strip(manifest, volatile))
    manifest_ok = bool(stored) and stored == recomputed
    missing, mismatched = [], []
    hashes = manifest.get("output_hashes") or {}
    for name in sorted(hashes):
        try:
            path = _assert_inside(source_dir, name)
        except SourceError:
            mismatched.append(name)
            continue
        if not os.path.isfile(path):
            missing.append(name)
            continue
        with open(path, "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() != hashes[name]:
                mismatched.append(name)
    for name in manifest.get("required_artifacts", []) or []:
        if not os.path.isfile(os.path.join(source_dir, name)) and name not in missing:
            missing.append(name)
    ok = manifest_ok and not missing and not mismatched
    return {"result": "PASS" if ok else "BLOCKED", "manifest_hash_verified": manifest_ok,
            "missing": sorted(missing), "mismatched": sorted(mismatched),
            "verified_file_count": len(hashes)}


class Source:
    """A loaded, validated (or blocked) promoted Phase 7.3 state."""

    def __init__(self, *, phase7_3_dir, source_dir, ok, readiness, reasons,
                 manifest=None, analysis=None, verification=None):
        self.phase7_3_dir = phase7_3_dir
        self.source_dir = source_dir
        self.ok = ok
        self.readiness = readiness
        self.reasons = reasons
        self.manifest = manifest or {}
        self.analysis = analysis or {}
        self.verification = verification or {}

    @property
    def manifest_hash(self):
        return self.manifest.get("deterministic_content_sha256")


def load_source(phase7_3_dir):
    """Load and validate promoted Phase 7.3 output. Never raises for a bad source — returns a
    blocked ``Source`` carrying exact reasons so the dashboard can start in a safe blocked mode."""
    source_dir = resolve_source_dir(phase7_3_dir)

    def blocked(readiness, reasons, verification=None):
        return Source(phase7_3_dir=os.path.abspath(phase7_3_dir), source_dir=source_dir,
                      ok=False, readiness=readiness, reasons=list(reasons), verification=verification)

    if not os.path.isdir(source_dir):
        return blocked(SOURCE_REQUIRED, [{"code": "SOURCE_DIRECTORY_MISSING", "detail": source_dir}])
    if not os.path.isfile(os.path.join(source_dir, F_MANIFEST)):
        return blocked(SOURCE_REQUIRED, [{"code": "MANIFEST_MISSING", "detail": F_MANIFEST}])

    try:
        manifest = _strict_json_loads(_read_bytes(os.path.join(source_dir, F_MANIFEST),
                                                  F_MANIFEST), what=F_MANIFEST)
        if not isinstance(manifest, dict):
            raise SourceError("MANIFEST_ROOT_NOT_OBJECT", type(manifest).__name__)
        if manifest.get("schema_version") != EXPECTED_MANIFEST_SCHEMA:
            raise SourceError("MANIFEST_SCHEMA_UNEXPECTED", str(manifest.get("schema_version")))

        verification = verify_source_integrity(source_dir, manifest)
        if verification["result"] != "PASS":
            return blocked(SOURCE_BLOCKED, [{"code": "SOURCE_INTEGRITY_FAILED",
                                             "detail": verification}], verification=verification)

        analysis = _strict_json_loads(_read_bytes(_assert_inside(source_dir, F_ANALYSIS),
                                                  F_ANALYSIS), what=F_ANALYSIS)
        if not isinstance(analysis, dict):
            raise SourceError("ANALYSIS_ROOT_NOT_OBJECT", type(analysis).__name__)
        if analysis.get("schema_version") != EXPECTED_ANALYSIS_SCHEMA:
            raise SourceError("ANALYSIS_SCHEMA_UNEXPECTED", str(analysis.get("schema_version")))

        _validate_analysis_consistency(analysis, manifest)
    except SourceError as e:
        return blocked(SOURCE_BLOCKED, [{"code": e.code, "detail": e.detail}])

    return Source(phase7_3_dir=os.path.abspath(phase7_3_dir), source_dir=source_dir, ok=True,
                  readiness=DASH_READY, reasons=[], manifest=manifest, analysis=analysis,
                  verification=verification)


def _validate_analysis_consistency(analysis, manifest):
    dq = analysis.get("data_quality")
    if not isinstance(dq, dict):
        raise SourceError("DATA_QUALITY_MISSING", "analysis.data_quality")
    rows = analysis.get("search_term_analysis")
    if not isinstance(rows, list):
        raise SourceError("SEARCH_TERM_ROOT_NOT_LIST", type(rows).__name__)

    # stable-ID uniqueness across every analyzed row.
    ids = [r.get("lineage_hash") for r in rows]
    if any(i is None for i in ids):
        raise SourceError("STABLE_ID_MISSING", "lineage_hash absent on a search-term row")
    if len(set(ids)) != len(ids):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise SourceError("DUPLICATE_STABLE_ID", dup[:5])

    # count identity: source == analyzed + skipped + blocked, cross-checked against the manifest.
    src = int(dq.get("source_row_count", -1))
    ana = int(dq.get("analyzed_row_count", -1))
    skip = int(dq.get("skipped_row_count", 0))
    blk = int(dq.get("blocked_row_count", 0))
    if src != ana + skip + blk:
        raise SourceError("COUNT_INCONSISTENT",
                          f"source={src} != analyzed={ana}+skipped={skip}+blocked={blk}")
    if ana != len(rows):
        raise SourceError("ANALYZED_COUNT_MISMATCH", f"data_quality={ana} rows={len(rows)}")
    mc = manifest.get("counts") or {}
    for key in ("source_row_count", "analyzed_row_count", "blocked_row_count"):
        if key in mc and int(mc[key]) != int(dq.get(key, mc[key])):
            raise SourceError("MANIFEST_ANALYSIS_COUNT_MISMATCH",
                              f"{key}: manifest={mc[key]} analysis={dq.get(key)}")


# ================================================================ entity / view assembly
_ROW_METRIC_FIELDS = ("impressions", "clicks", "spend", "orders", "sales", "units",
                      "ctr", "cpc", "conversion_rate", "acos", "roas")


def _row_material(r):
    m = {k: r.get(k) for k in _ROW_METRIC_FIELDS}
    m.update({
        "primary_classification": r.get("primary_classification"),
        "classification_rule": r.get("classification_rule"),
        "owner_review_label": r.get("owner_review_label"),
        "reason_codes": r.get("reason_codes"),
        "evidence": r.get("evidence"),
        "currency": r.get("currency"),
    })
    return content_sha256(m)


def _agg_material(g):
    return content_sha256({k: g.get(k) for k in
                           _ROW_METRIC_FIELDS + ("currency", "analyzed_rows", "excluded_rows")})


def _evidence_summary(evidence):
    if not isinstance(evidence, dict):
        return None
    parts = []
    states = evidence.get("metric_states") or {}
    for k in sorted(states):
        parts.append(f"{k}={states[k]}")
    if evidence.get("target_acos") is not None:
        parts.append(f"target_acos={evidence['target_acos']}")
    if evidence.get("outside_lookback_window"):
        parts.append("outside_lookback_window")
    return "; ".join(parts) if parts else None


def _reason_text(reason_codes):
    return "; ".join(reason_codes) if reason_codes else None


def _attribution_windows(rows):
    """Attribution windows are DERIVED from the metric-state field names (e.g. orders_7d -> 7 day),
    never inferred. Deterministic and sorted."""
    windows = set()
    for r in rows[:200]:
        for field in (r.get("evidence") or {}).get("metric_states", {}):
            m = re.search(r"_(\d+)d$", field)
            if m:
                windows.add(int(m.group(1)))
    return [f"{d} day" for d in sorted(windows)]


def _is_blocked_row(r):
    if r.get("primary_classification") == CL_NEEDS_OWNER_REVIEW:
        return True
    codes = set(r.get("reason_codes") or [])
    return bool(codes & set(BLOCKING_REASON_CODES))


def _build_campaigns(analysis, rows_by_campaign, queue_by_campaign):
    out = []
    for c in analysis.get("campaign_summary", []):
        camp = c.get("campaign")
        member = rows_by_campaign.get(camp, [])
        reason_codes = set()
        for r in member:
            reason_codes.update(r.get("reason_codes") or [])
        row = dict(c)
        row["campaign_id"] = _dash(c.get("campaign_id"))
        row["classification"] = _dash(c.get("classification"))
        row["observation_count"] = len(reason_codes)
        row["recommendation_count"] = len(queue_by_campaign.get(camp, []))
        row["entity_type"] = "campaign"
        row["entity_id"] = _stable_id("cmp", camp, c.get("currency"))
        row["content_sha256"] = _agg_material(c)
        out.append(row)
    return out


def _build_ad_groups(analysis):
    out = []
    for g in analysis.get("ad_group_summary", []):
        row = dict(g)
        row["classification"] = _dash(g.get("classification"))
        row["entity_type"] = "ad_group"
        row["entity_id"] = _stable_id("adg", g.get("campaign"), g.get("ad_group"), g.get("currency"))
        row["content_sha256"] = _agg_material(g)
        out.append(row)
    return out


def _build_targets(rows):
    """A navigational target-level grouping produced by Phase 7.3's OWN aggregation authority
    (``phase7_ads_analysis.aggregate``) — the same Decimal-safe, currency-isolating arithmetic used
    for its campaign/ad-group summaries. No classification is invented: the set of row-level
    classifications present is surfaced verbatim."""
    groups = AA.aggregate(rows, ["campaign", "ad_group", "targeting", "match_type"])
    facets = {}
    for r in rows:
        key = (r.get("currency"), r.get("campaign"), r.get("ad_group"),
               r.get("targeting"), r.get("match_type"))
        f = facets.setdefault(key, {"classes": set(), "reasons": set()})
        if r.get("primary_classification"):
            f["classes"].add(r["primary_classification"])
        f["reasons"].update(r.get("reason_codes") or [])
    out = []
    for g in groups:
        key = (g.get("currency"), g.get("campaign"), g.get("ad_group"),
               g.get("targeting"), g.get("match_type"))
        f = facets.get(key, {"classes": set(), "reasons": set()})
        row = dict(g)
        row["targeting_expression"] = g.get("targeting")
        row["classification"] = "; ".join(sorted(f["classes"])) if f["classes"] else None
        row["reason"] = "; ".join(sorted(f["reasons"])) if f["reasons"] else None
        row["entity_type"] = "target"
        row["entity_id"] = _stable_id("tgt", g.get("campaign"), g.get("ad_group"),
                                      g.get("targeting"), g.get("match_type"), g.get("currency"))
        row["content_sha256"] = _agg_material(g)
        out.append(row)
    return out


def _search_term_view(r, *, prefix, entity_type):
    return {
        "entity_type": entity_type,
        "entity_id": f"{prefix}:{r['lineage_hash']}",
        "content_sha256": _row_material(r),
        "recommendation_id": r.get("lineage_hash"),
        "campaign": _dash(r.get("campaign")),
        "ad_group": _dash(r.get("ad_group")),
        "targeting": _dash(r.get("targeting")),
        "target": _dash(r.get("targeting")),
        "match_type": _dash(r.get("match_type")),
        "customer_search_term": _dash(r.get("customer_search_term")),
        "search_term": _dash(r.get("customer_search_term")),
        "currency": _dash(r.get("currency")),
        "start_date": _dash(r.get("start_date")),
        "end_date": _dash(r.get("end_date")),
        "impressions": _dash(r.get("impressions")),
        "clicks": _dash(r.get("clicks")),
        "spend": _dash(r.get("spend")),
        "orders": _dash(r.get("orders")),
        "sales": _dash(r.get("sales")),
        "units": _dash(r.get("units")),
        "ctr": _dash(r.get("ctr")),
        "cpc": _dash(r.get("cpc")),
        "conversion_rate": _dash(r.get("conversion_rate")),
        "acos": _dash(r.get("acos")),
        "roas": _dash(r.get("roas")),
        "primary_classification": _dash(r.get("primary_classification")),
        "classification": _dash(r.get("primary_classification")),
        "classification_rule": _dash(r.get("classification_rule")),
        "owner_review_label": _dash(r.get("owner_review_label")),
        "reason_codes": r.get("reason_codes") or [],
        "reason": _reason_text(r.get("reason_codes")),
        "evidence": _evidence_summary(r.get("evidence")),
        "evidence_detail": r.get("evidence"),
        "confidence": _dash(r.get("confidence")),
        "lineage_hash": r.get("lineage_hash"),
        "canonical_row_key": r.get("canonical_row_key"),
    }


def _policy_dependency(r):
    src = ((r.get("evidence") or {}).get("target_acos_source"))
    if src and src != "OWNER_DECLARED":
        return f"target_acos_source={src}"
    return None


def build_views(source):
    """Assemble every read-only view from the validated analysis. Deterministic ordering throughout."""
    a = source.analysis
    rows = a.get("search_term_analysis", [])
    queue = a.get("owner_decision_queue", [])

    rows_by_campaign, queue_by_campaign = {}, {}
    for r in rows:
        rows_by_campaign.setdefault(r.get("campaign"), []).append(r)
    for r in queue:
        queue_by_campaign.setdefault(r.get("campaign"), []).append(r)

    campaigns = _build_campaigns(a, rows_by_campaign, queue_by_campaign)
    ad_groups = _build_ad_groups(a)
    targets = _build_targets(rows)
    search_terms = [_search_term_view(r, prefix="st", entity_type="search_term")
                    for r in AA.sort_search_terms(list(rows))]
    recommendations = [_search_term_view(r, prefix="rec", entity_type="recommendation")
                       for r in queue]
    for r in recommendations:
        r["recommendation_type"] = r["owner_review_label"]
        r["policy_requirement"] = _policy_dependency({"evidence": r["evidence_detail"]})

    blocked_rows = [r for r in AA.sort_search_terms(list(rows)) if _is_blocked_row(r)]
    blocked = []
    for r in blocked_rows:
        v = _search_term_view(r, prefix="blk", entity_type="blocked")
        states = (r.get("evidence") or {}).get("metric_states", {})
        v["blocked_item"] = v["customer_search_term"]
        v["blocking_reason"] = _reason_text(r.get("reason_codes"))
        v["missing_owner_policy"] = _policy_dependency(r)
        v["missing_data"] = "; ".join(sorted(k for k, s in states.items()
                                             if s in ("MISSING", "BLANK", "INVALID"))) or None
        v["conflicting_evidence"] = None
        v["required_owner_decision"] = "Review the evidence and decide manually in Seller Central."
        blocked.append(v)

    # manual review queue = actionable recommendations first (by queue priority), then blocked rows.
    manual = []
    for i, r in enumerate(queue):
        v = _search_term_view(r, prefix="mrq", entity_type="manual_review")
        v["queue_item_id"] = v["entity_id"]
        v["priority"] = i + 1
        v["entity"] = f"{r.get('campaign')} / {r.get('ad_group')} / {r.get('customer_search_term')}"
        v["suggested_review"] = r.get("owner_review_label")
        v["policy_dependency"] = _policy_dependency(r)
        manual.append(v)
    base = len(queue)
    for j, r in enumerate(blocked_rows):
        v = _search_term_view(r, prefix="mrq", entity_type="manual_review")
        v["queue_item_id"] = v["entity_id"]
        v["priority"] = base + j + 1
        v["entity"] = f"{r.get('campaign')} / {r.get('ad_group')} / {r.get('customer_search_term')}"
        v["suggested_review"] = "NEEDS_OWNER_REVIEW"
        v["policy_dependency"] = _policy_dependency(r)
        manual.append(v)

    observations = _build_observations(rows)
    policy = _build_policy(a)

    return {
        "campaigns": campaigns, "ad_groups": ad_groups, "targets": targets,
        "search_terms": search_terms, "recommendations": recommendations,
        "blocked": blocked, "manual_review": manual, "observations": observations,
        "policy_requirements": policy,
    }


def _build_observations(rows):
    counts, examples = {}, {}
    for r in rows:
        for code in r.get("reason_codes") or []:
            counts[code] = counts.get(code, 0) + 1
            examples.setdefault(code, r.get("lineage_hash"))
    out = []
    for code in sorted(counts):
        out.append({
            "entity_type": "observation",
            "entity_id": _stable_id("obs", code),
            "reason_code": code,
            "description": code.replace("_", " ").title(),
            "row_count": counts[code],
            "example_id": examples[code],
        })
    return out


def _build_policy(analysis):
    th = analysis.get("thresholds") or {}
    out = []
    src = th.get("target_acos_source")
    if src and src != "OWNER_DECLARED":
        out.append({
            "entity_type": "policy", "entity_id": _stable_id("pol", "target_acos"),
            "requirement": "Confirm the target ACoS for this account",
            "detail": f"target_acos={th.get('target_acos')} ({src})",
            "status": "OWNER_CONFIGURABLE",
        })
    out.append({
        "entity_type": "policy", "entity_id": _stable_id("pol", "date_rule"),
        "requirement": "Date range is taken from the source report only",
        "detail": th.get("date_rule"),
        "status": "SOURCE_REPORTED",
    })
    return out


# ================================================================ overview & data health
def build_overview(source, views, review_records):
    a = source.analysis
    dq = a.get("data_quality", {})
    mc = source.manifest.get("counts", {})
    dates = dq.get("date_coverage", {})
    currencies = dq.get("currencies_encountered") or []
    changed = sum(1 for r in review_records.values() if r.get("source_change_state") != "CURRENT")
    return {
        "phase7_3_readiness": a.get("analysis_readiness"),
        "source_row_count": dq.get("source_row_count"),
        "analyzed_row_count": dq.get("analyzed_row_count"),
        "skipped_row_count": dq.get("skipped_row_count"),
        "decision_queue_row_count": mc.get("decision_queue_row_count", len(views["recommendations"])),
        "blocked_row_count": dq.get("blocked_row_count"),
        "campaign_count": len(views["campaigns"]),
        "ad_group_count": len(views["ad_groups"]),
        "targeting_count": len(views["targets"]),
        "search_term_count": len(views["search_terms"]),
        "observation_count": len(views["observations"]),
        "recommendation_count": len(views["recommendations"]),
        "manual_review_count": len(views["manual_review"]),
        "policy_requirement_count": len(views["policy_requirements"]),
        "currency": ", ".join(currencies) if currencies else None,
        "currencies": currencies,
        "attribution_windows": _attribution_windows(a.get("search_term_analysis", [])),
        "report_date_range": {"earliest": dates.get("earliest"), "latest": dates.get("latest"),
                              "distinct_dates": dates.get("distinct_dates")},
        "manifest_validation": source.verification.get("result"),
        "source_integrity_status": "VERIFIED" if source.verification.get("result") == "PASS"
        else "UNVERIFIED",
        "review_item_count": len(review_records),
        "source_changed_item_count": changed,
        "dashboard_load_status": "READY" if source.ok else "BLOCKED",
    }


def build_data_health(source):
    a = source.analysis
    dq = a.get("data_quality", {})
    manifest = source.manifest
    ver = source.verification
    src = manifest.get("source", {})
    return {
        "source_directory": source.source_dir,
        "manifest_status": ver.get("result"),
        "manifest_hash_verified": ver.get("manifest_hash_verified"),
        "artifact_list": manifest.get("required_artifacts", []),
        "artifact_hashes": manifest.get("output_hashes", {}),
        "missing_files": ver.get("missing", []),
        "invalid_files": ver.get("mismatched", []),
        "schema_versions": {
            "manifest": manifest.get("schema_version"),
            "analysis": a.get("schema_version"),
            "data_quality": dq.get("schema_version"),
            "thresholds": (a.get("thresholds") or {}).get("schema_version"),
        },
        "currencies": dq.get("currencies_encountered", []),
        "attribution_windows": _attribution_windows(a.get("search_term_analysis", [])),
        "cumulative_phase7_2_row_count": dq.get("source_row_count"),
        "phase7_2_readiness": src.get("phase7_2_readiness"),
        "phase7_2_manifest_sha256": src.get("phase7_2_manifest_sha256"),
        "source_analyzed_consistency": {
            "source_row_count": dq.get("source_row_count"),
            "analyzed_row_count": dq.get("analyzed_row_count"),
            "skipped_row_count": dq.get("skipped_row_count"),
            "blocked_row_count": dq.get("blocked_row_count"),
            "consistent": (dq.get("source_row_count") ==
                           (dq.get("analyzed_row_count", 0) + dq.get("skipped_row_count", 0)
                            + dq.get("blocked_row_count", 0))),
        },
        "last_successful_analysis_state": a.get("analysis_readiness"),
        "duplicate_handling": dq.get("duplicate_handling", {}),
        "invalid_numeric_count": dq.get("invalid_numeric_count"),
    }


# ================================================================ review state (local only)
def review_state_path(base_dir):
    return os.path.join(os.path.abspath(base_dir), "review_state", F_REVIEW_STATE)


def _atomic_write(path, data):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_review_state(base_dir):
    path = review_state_path(base_dir)
    if not os.path.isfile(path):
        return {"schema_version": REVIEW_STATE_SCHEMA, "records": {}}
    with open(path, "rb") as f:
        doc = _strict_json_loads(f.read(), what=F_REVIEW_STATE)
    if not isinstance(doc, dict) or not isinstance(doc.get("records"), dict):
        raise DashboardError("REVIEW_STATE_CORRUPT")
    return doc


def _entity_index(views):
    """entity_id -> {entity_type, content_sha256} for every reviewable entity."""
    index = {}
    for key in ("campaigns", "ad_groups", "targets", "search_terms",
                "recommendations", "blocked", "manual_review"):
        for row in views[key]:
            index[row["entity_id"]] = {"entity_type": row["entity_type"],
                                       "content_sha256": row["content_sha256"]}
    return index


def merge_review_state(base_dir, source, views):
    """Merge stored review records against the current source. Preserves status where the entity
    still exists and its material content is unchanged; flags SOURCE_CHANGED / ENTITY_ABSENT
    otherwise so a material approval is never silently carried onto changed evidence."""
    doc = load_review_state(base_dir)
    index = _entity_index(views)
    merged = {}
    for entity_id, rec in doc.get("records", {}).items():
        rec = dict(rec)
        current = index.get(entity_id)
        if current is None:
            rec["source_change_state"] = "ENTITY_ABSENT"
        elif rec.get("content_sha256") != current["content_sha256"]:
            rec["source_change_state"] = "SOURCE_CHANGED"
        elif rec.get("source_manifest_sha256") not in (None, source.manifest_hash):
            rec["source_change_state"] = "CURRENT"
        else:
            rec["source_change_state"] = "CURRENT"
        rec["requires_re_review"] = (
            rec["source_change_state"] != "CURRENT"
            and rec.get("review_status") in MATERIAL_COMMITMENT_STATUSES)
        rec["effective_status"] = ("NEEDS_MORE_DATA" if rec["requires_re_review"]
                                   else rec.get("review_status", DEFAULT_STATUS))
        merged[entity_id] = rec
    return merged


def save_review(base_dir, source, views, updates, *, now=None):
    """Apply local review updates (single or bulk). Validates status and entity identity, stamps a
    runtime revision/timestamps, and persists atomically. Never touches Phase 7.3 source."""
    now = now or _now_utc
    if not isinstance(updates, list) or not updates:
        raise DashboardError("NO_UPDATES")
    index = _entity_index(views)
    doc = load_review_state(base_dir)
    records = doc.setdefault("records", {})
    stamp = _iso(now())
    applied = []
    for upd in updates:
        if not isinstance(upd, dict):
            raise DashboardError("UPDATE_NOT_OBJECT")
        entity_id = upd.get("entity_id")
        status = upd.get("review_status", DEFAULT_STATUS)
        note = upd.get("owner_note", "")
        if entity_id not in index:
            raise DashboardError(f"UNKNOWN_ENTITY:{entity_id}")
        if status not in REVIEW_STATUSES:
            raise DashboardError(f"INVALID_REVIEW_STATUS:{status}")
        if not isinstance(note, str) or len(note) > MAX_NOTE_CHARS:
            raise DashboardError("INVALID_OWNER_NOTE")
        prev = records.get(entity_id, {})
        records[entity_id] = {
            "entity_id": entity_id,
            "entity_type": index[entity_id]["entity_type"],
            "review_status": status,
            "owner_note": note,
            "source_manifest_sha256": source.manifest_hash,
            "content_sha256": index[entity_id]["content_sha256"],
            "revision": int(prev.get("revision", 0)) + 1,
            "created_at": prev.get("created_at", stamp),
            "updated_at": stamp,
        }
        applied.append(records[entity_id])
    doc["schema_version"] = REVIEW_STATE_SCHEMA
    _atomic_write(review_state_path(base_dir), canonical_json(doc).encode("utf-8"))
    return applied


def reset_review(base_dir, entity_ids=None):
    """Delete local review records (revert to UNREVIEWED). Never an Amazon action."""
    doc = load_review_state(base_dir)
    records = doc.get("records", {})
    if entity_ids is None:
        removed = list(records)
        records.clear()
    else:
        removed = [e for e in entity_ids if records.pop(e, None) is not None]
    doc["schema_version"] = REVIEW_STATE_SCHEMA
    _atomic_write(review_state_path(base_dir), canonical_json(doc).encode("utf-8"))
    return removed


# ================================================================ export (local only)
_TSV_DANGEROUS = ("=", "+", "-", "@", "\t", "\r", "|")
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _tsv_cell(value):
    """Neutralize spreadsheet formula injection and strip TSV-breaking control characters.
    A plain number keeps its leading sign; anything else with a dangerous leading char is prefixed
    with a single quote so a spreadsheet treats it as text."""
    if value is None:
        return ""
    s = str(value)
    s = s.replace("\t", " ").replace("\r", " ").replace("\n", " ").replace("\x00", "")
    if s and s[0] in _TSV_DANGEROUS and not _NUMERIC_RE.match(s):
        s = "'" + s
    return s


EXPORT_COLUMNS = ("entity_type", "recommendation_id", "campaign", "ad_group", "target",
                  "search_term", "classification", "reason", "evidence",
                  "owner_review_status", "owner_note", "source_change_state")


def _reviewable_index(views):
    index = {}
    for key in ("recommendations", "manual_review", "blocked", "search_terms",
                "campaigns", "ad_groups", "targets"):
        for row in views[key]:
            index.setdefault(row["entity_id"], row)
    return index


def _export_record(row, review):
    rec = review.get(row["entity_id"], {})
    return {
        "entity_id": row["entity_id"],
        "entity_type": row.get("entity_type"),
        "recommendation_id": row.get("recommendation_id") or row.get("entity_id"),
        "campaign": row.get("campaign"),
        "ad_group": row.get("ad_group"),
        "target": row.get("target") or row.get("targeting_expression"),
        "search_term": row.get("search_term"),
        "classification": row.get("classification"),
        "reason": row.get("reason"),
        "evidence": row.get("evidence"),
        "owner_review_status": rec.get("review_status", DEFAULT_STATUS),
        "owner_note": rec.get("owner_note", ""),
        "source_change_state": rec.get("source_change_state", "CURRENT"),
    }


def _next_export_name(exports_dir, ext):
    os.makedirs(exports_dir, exist_ok=True)
    n = 0
    for name in os.listdir(exports_dir):
        m = re.match(r"owner-review-export-(\d+)\.", name)
        if m:
            n = max(n, int(m.group(1)))
    return f"owner-review-export-{n + 1:04d}.{ext}"


def build_export(base_dir, source, views, review_records, *, fmt, scope="all",
                 entity_ids=None, filter_summary="", now=None):
    """Build and write a local review export. TSV / JSON / Markdown only. Every export carries the
    manual-action disclaimer. Never writes an Amazon upload template or API payload."""
    now = now or _now_utc
    if fmt not in ("tsv", "json", "markdown"):
        raise DashboardError(f"UNSUPPORTED_EXPORT_FORMAT:{fmt}")
    if scope not in ("all", "filtered", "approved_only"):
        raise DashboardError(f"UNSUPPORTED_EXPORT_SCOPE:{scope}")

    index = _reviewable_index(views)
    if entity_ids is None:
        selected = list(index)
    else:
        selected = [e for e in entity_ids if e in index]
    records = [_export_record(index[e], review_records) for e in selected]
    if scope == "approved_only":
        records = [r for r in records if r["owner_review_status"] == "APPROVED_FOR_MANUAL_ACTION"
                   and r["source_change_state"] == "CURRENT"]
    records.sort(key=lambda r: (r["entity_type"], r["entity_id"]))

    meta = {
        "export_schema_version": EXPORT_SCHEMA,
        "stage_id": STAGE_ID,
        "source_phase7_3_manifest_sha256": source.manifest_hash,
        "export_generated_at": _iso(now()),
        "filter_summary": filter_summary or f"scope={scope}",
        "scope": scope,
        "item_count": len(records),
        "disclaimer": list(DISCLAIMER_LINES),
        "amazon_action_performed": False,
    }
    if fmt == "tsv":
        content, ext = _export_tsv(meta, records), "tsv"
    elif fmt == "json":
        content, ext = _export_json(meta, records), "json"
    else:
        content, ext = _export_markdown(meta, records), "md"

    exports_dir = os.path.join(os.path.abspath(base_dir), "exports")
    name = _next_export_name(exports_dir, ext)
    path = os.path.join(exports_dir, name)
    _atomic_write(path, content.encode("utf-8"))
    return {"name": name, "path": path, "item_count": len(records), "format": fmt,
            "scope": scope, "content": content, "meta": meta}


def _export_tsv(meta, records):
    lines = [f"# {line}" for line in DISCLAIMER_LINES]
    lines.append(f"# export_schema_version\t{meta['export_schema_version']}")
    lines.append(f"# source_phase7_3_manifest_sha256\t{meta['source_phase7_3_manifest_sha256']}")
    lines.append(f"# export_generated_at\t{meta['export_generated_at']}")
    lines.append(f"# filter_summary\t{_tsv_cell(meta['filter_summary'])}")
    lines.append(f"# item_count\t{meta['item_count']}")
    lines.append("\t".join(EXPORT_COLUMNS))
    for r in records:
        lines.append("\t".join(_tsv_cell(r.get(c)) for c in EXPORT_COLUMNS))
    return "\n".join(lines) + "\n"


def _export_json(meta, records):
    return canonical_json({"meta": meta, "records": records}) + "\n"


def _export_markdown(meta, records):
    out = ["# Owner Review Export", "", "> **OFFLINE REVIEW MODE**"]
    for line in DISCLAIMER_LINES:
        out.append("> " + line)
    out += ["", f"- Export schema: `{meta['export_schema_version']}`",
            f"- Source Phase 7.3 manifest: `{meta['source_phase7_3_manifest_sha256']}`",
            f"- Generated: `{meta['export_generated_at']}`",
            f"- Filter: {meta['filter_summary']}",
            f"- Items: {meta['item_count']}", "",
            "| " + " | ".join(EXPORT_COLUMNS) + " |",
            "| " + " | ".join("---" for _ in EXPORT_COLUMNS) + " |"]
    for r in records:
        cells = []
        for c in EXPORT_COLUMNS:
            v = r.get(c)
            cells.append("—" if v is None or v == "" else str(v).replace("|", "\\|").replace("\n", " "))
        out.append("| " + " | ".join(cells) + " |")
    out.append("")
    return "\n".join(out)


def latest_export(base_dir):
    exports_dir = os.path.join(os.path.abspath(base_dir), "exports")
    if not os.path.isdir(exports_dir):
        return None
    names = sorted(n for n in os.listdir(exports_dir) if n.startswith("owner-review-export-"))
    if not names:
        return None
    name = names[-1]
    path = os.path.join(exports_dir, name)
    return {"name": name, "path": path, "size": os.path.getsize(path)}


# ================================================================ filters
def build_filters(views):
    def uniq(rows, field):
        return sorted({r.get(field) for r in rows if r.get(field) not in (None, "")})
    return {
        "campaigns": uniq(views["search_terms"], "campaign"),
        "ad_groups": uniq(views["search_terms"], "ad_group"),
        "targets": uniq(views["search_terms"], "targeting"),
        "classifications": uniq(views["search_terms"], "primary_classification"),
        "recommendation_types": sorted({r.get("recommendation_type")
                                        for r in views["recommendations"] if r.get("recommendation_type")}),
        "review_statuses": list(REVIEW_STATUSES),
        "currencies": uniq(views["search_terms"], "currency"),
    }


# ================================================================ model orchestration
def build_model(base_dir, source, *, now=None):
    """The full dashboard model: overview, health, every view, filters, review state, boundary."""
    if not source.ok:
        return {
            "schema_version": DASHBOARD_SCHEMA, "stage_id": STAGE_ID,
            "readiness": source.readiness, "ok": False, "reasons": source.reasons,
            "amazon_counters": dict(AMAZON_COUNTERS), "this_session_never": dict(THIS_SESSION_NEVER),
            "overview": {"dashboard_load_status": "BLOCKED",
                         "phase7_3_readiness": None, "manifest_validation": source.verification.get("result")},
            "data_health": {"source_directory": source.source_dir,
                            "manifest_status": source.verification.get("result"),
                            "reasons": source.reasons},
        }
    views = build_views(source)
    review_records = merge_review_state(base_dir, source, views)
    overview = build_overview(source, views, review_records)
    changed = overview["source_changed_item_count"]
    readiness = SOURCE_CHANGED if changed else DASH_READY
    return {
        "schema_version": DASHBOARD_SCHEMA, "stage_id": STAGE_ID, "stage_name": STAGE_NAME,
        "readiness": readiness, "ok": True, "reasons": [],
        "source_manifest_sha256": source.manifest_hash,
        "overview": overview, "data_health": build_data_health(source),
        "campaigns": views["campaigns"], "ad_groups": views["ad_groups"],
        "targets": views["targets"], "search_terms": views["search_terms"],
        "observations": views["observations"], "recommendations": views["recommendations"],
        "blocked_recommendations": views["blocked"], "manual_review_queue": views["manual_review"],
        "policy_requirements": views["policy_requirements"],
        "filters": build_filters(views), "review_state": review_records,
        "review_statuses": list(REVIEW_STATUSES),
        "amazon_counters": dict(AMAZON_COUNTERS), "this_session_never": dict(THIS_SESSION_NEVER),
        "disclaimer": list(DISCLAIMER_LINES),
    }


def ensure_workspace(base_dir):
    base = os.path.abspath(base_dir)
    for sub in _WORKSPACE_SUBDIRS:
        os.makedirs(os.path.join(base, sub), exist_ok=True)
    return base


# ================================================================ HTTP server
_API_VIEW_KEYS = {
    "/api/campaigns": "campaigns",
    "/api/ad-groups": "ad_groups",
    "/api/targets": "targets",
    "/api/search-terms": "search_terms",
    "/api/observations": "observations",
    "/api/recommendations": "recommendations",
    "/api/blocked-recommendations": "blocked_recommendations",
    "/api/manual-review-queue": "manual_review_queue",
    "/api/policy-requirements": "policy_requirements",
}


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, *, base_dir, phase7_3_dir, now=None):
        self.base_dir = os.path.abspath(base_dir)
        self.phase7_3_dir = os.path.abspath(phase7_3_dir)
        self.now = now or _now_utc
        super().__init__(address, handler)

    # every request re-reads and re-validates source + review state from disk, so the server never
    # holds a stale approval and "reload from disk" is automatic.
    def model(self):
        source = load_source(self.phase7_3_dir)
        return source, build_model(self.base_dir, source, now=self.now)


def _make_handler():
    class Handler(BaseHTTPRequestHandler):
        server_version = "Phase74Dashboard/1.0"
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # no telemetry, no stdout noise
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

        # -------------------------------------------------- routing
        def do_GET(self):
            try:
                path = urlsplit(self.path).path
                if path in ("/", "/index.html"):
                    return self._static("index.html")
                if path.lstrip("/") in STATIC_FILES:
                    return self._static(path.lstrip("/"))
                if path.startswith("/api/"):
                    return self._api_get(path)
                return self._error(404, "NOT_FOUND", path)
            except Exception as e:  # noqa: BLE001  never leak a stack trace to the client
                return self._error(500, "INTERNAL_ERROR", type(e).__name__)

        def do_HEAD(self):
            return self.do_GET()

        def do_POST(self):
            try:
                path = urlsplit(self.path).path
                if path not in ("/api/review/save", "/api/review/export", "/api/review/reset"):
                    return self._error(404, "NOT_FOUND", path)
                body = self._read_body()
                if body is None:
                    return
                return self._api_post(path, body)
            except DashboardError as e:
                return self._error(400, "REVIEW_ERROR", str(e))
            except Exception as e:  # noqa: BLE001
                return self._error(500, "INTERNAL_ERROR", type(e).__name__)

        def _reject_method(self):
            self._error(405, "METHOD_NOT_ALLOWED", self.command)

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _reject_method

        # -------------------------------------------------- static
        def _static(self, name):
            ctype = STATIC_FILES[name]
            path = os.path.join(STATIC_DIR, name)
            if not os.path.isfile(path):
                return self._error(404, "STATIC_NOT_FOUND", name)
            with open(path, "rb") as f:
                return self._send_bytes(200, ctype, f.read())

        # -------------------------------------------------- GET api
        def _api_get(self, path):
            source, model = self.server.model()
            if path == "/api/health":
                return self._json({
                    "dashboard_readiness": model["readiness"],
                    "source_readiness": source.analysis.get("analysis_readiness") if source.ok else None,
                    "ok": model["ok"], "reasons": model.get("reasons", []),
                    "amazon_counters": dict(AMAZON_COUNTERS),
                    "this_session_never": dict(THIS_SESSION_NEVER),
                    "schema_version": DASHBOARD_SCHEMA, "stage_id": STAGE_ID})
            if path == "/api/model":
                return self._json(model)
            if not model["ok"] and path not in ("/api/manifest", "/api/session"):
                return self._json({"blocked": True, "readiness": model["readiness"],
                                   "reasons": model["reasons"]})
            if path == "/api/session":
                return self._json({"overview": model.get("overview"),
                                   "data_health": model.get("data_health"),
                                   "readiness": model["readiness"], "ok": model["ok"]})
            if path == "/api/manifest":
                return self._json({"manifest": source.manifest, "verification": source.verification})
            if path == "/api/filters":
                return self._json(model.get("filters", {}))
            if path in _API_VIEW_KEYS:
                key = _API_VIEW_KEYS[path]
                return self._json({"data": model.get(key, []), "review_state": model.get("review_state", {})})
            if path == "/api/export/latest":
                return self._json({"latest": latest_export(self.server.base_dir)})
            return self._error(404, "UNKNOWN_ENDPOINT", path)

        # -------------------------------------------------- body
        def _drain(self, remaining):
            """Discard up to a bounded amount of an unwanted request body so a client blocked on
            send() unblocks. A body claiming more than the cap has the connection closed instead."""
            cap = MAX_BODY_BYTES * 4
            to_read = min(remaining, cap)
            while to_read > 0:
                chunk = self.rfile.read(min(65536, to_read))
                if not chunk:
                    break
                to_read -= len(chunk)
            if remaining > cap:
                self.close_connection = True

        def _read_body(self):
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                self._error(415, "JSON_CONTENT_TYPE_REQUIRED", ctype)
                return None
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._error(411, "LENGTH_REQUIRED")
                return None
            if length < 0 or length > MAX_BODY_BYTES:
                self._drain(max(length, 0))
                self._error(413, "REQUEST_BODY_TOO_LARGE", str(length))
                return None
            raw = self.rfile.read(length)
            if b"\x00" in raw:
                self._error(400, "NULL_BYTE_REJECTED")
                return None
            try:
                obj = json.loads(raw.decode("utf-8"),
                                 parse_constant=lambda t: (_ for _ in ()).throw(ValueError(t)))
            except ValueError as e:
                self._error(400, "MALFORMED_JSON", str(e))
                return None
            if not isinstance(obj, dict):
                self._error(400, "BODY_NOT_OBJECT")
                return None
            return obj

        # -------------------------------------------------- POST api
        def _api_post(self, path, body):
            source, model = self.server.model()
            if path == "/api/review/reset":
                removed = reset_review(self.server.base_dir, body.get("entity_ids"))
                return self._json({"reset": removed, "count": len(removed)})
            if not model["ok"]:
                return self._error(409, EXPORT_BLOCKED if path.endswith("export") else "SOURCE_BLOCKED",
                                   json.dumps(model["reasons"]))
            views = build_views(source)
            if path == "/api/review/save":
                applied = save_review(self.server.base_dir, source, views,
                                      body.get("updates", []), now=self.server.now)
                return self._json({"saved": applied, "count": len(applied),
                                   "readiness": REVIEW_STATE_READY})
            # /api/review/export
            review_records = merge_review_state(self.server.base_dir, source, views)
            result = build_export(self.server.base_dir, source, views, review_records,
                                  fmt=body.get("format", "tsv"), scope=body.get("scope", "all"),
                                  entity_ids=body.get("entity_ids"),
                                  filter_summary=body.get("filter_summary", ""),
                                  now=self.server.now)
            return self._json({"export": {k: v for k, v in result.items() if k != "path"},
                               "readiness": EXPORT_READY})

    return Handler


def validate_host(host, *, allow_nonlocal=False):
    if host in LOOPBACK_HOSTS or allow_nonlocal:
        return host
    raise DashboardError(
        f"NON_LOOPBACK_BIND_REFUSED: {host!r}. The dashboard binds to a loopback address only. "
        "Pass --allow-nonlocal-bind to override (not recommended).")


def build_server(base_dir, phase7_3_dir, *, host="127.0.0.1", port=8740,
                 allow_nonlocal=False, now=None):
    validate_host(host, allow_nonlocal=allow_nonlocal)
    ensure_workspace(base_dir)
    handler = _make_handler()
    return DashboardServer((host, port), handler, base_dir=base_dir,
                           phase7_3_dir=phase7_3_dir, now=now)


# ================================================================ CLI
def main(argv=None, *, serve=True):
    ap = argparse.ArgumentParser(
        description="Phase 7.4 offline, read-only Owner Review Dashboard over promoted Phase 7.3 "
                    "output. Binds to 127.0.0.1, connects to nothing, exports locally only.")
    ap.add_argument("--base-dir", required=True, help="Phase 7.4 workspace (e.g. runs/T2/phase7/7.4)")
    ap.add_argument("--phase7-3-dir", required=True,
                    help="Phase 7.3 workspace whose promoted/ output is the ONLY source")
    ap.add_argument("--host", default="127.0.0.1", help="bind host (loopback only by default)")
    ap.add_argument("--port", type=int, default=8740, help="bind port")
    ap.add_argument("--allow-nonlocal-bind", action="store_true",
                    help="explicitly allow a non-loopback bind (not recommended)")
    a = ap.parse_args(argv)

    try:
        validate_host(a.host, allow_nonlocal=a.allow_nonlocal_bind)
    except DashboardError as e:
        print(str(e), file=sys.stderr)
        return 2

    source = load_source(a.phase7_3_dir)
    model = build_model(os.path.abspath(a.base_dir), source)
    ensure_workspace(a.base_dir)
    dq = source.analysis.get("data_quality", {}) if source.ok else {}
    review = model.get("review_state", {}) if source.ok else {}
    blocked_items = (model.get("overview", {}) or {}).get("blocked_row_count", 0) or 0

    print(f"dashboard_readiness={model['readiness']}")
    print(f"source_readiness={source.analysis.get('analysis_readiness') if source.ok else source.readiness}")
    print(f"source_rows={dq.get('source_row_count', 0)}")
    print(f"analyzed_rows={dq.get('analyzed_row_count', 0)}")
    print(f"review_items={len(review)}")
    print(f"blocked_items={blocked_items}")
    print(f"amazon_connections={AMAZON_COUNTERS['amazon_connections']}")
    print(f"amazon_api_calls={AMAZON_COUNTERS['amazon_api_calls']}")
    print(f"amazon_mutations={AMAZON_COUNTERS['amazon_mutations']}")
    print(f"dashboard_url=http://{a.host}:{a.port}")
    if not source.ok:
        for reason in source.reasons:
            print(f"source_reason={reason.get('code')}", file=sys.stderr)

    if not serve:
        return 0 if source.ok else 1

    server = build_server(a.base_dir, a.phase7_3_dir, host=a.host, port=a.port,
                          allow_nonlocal=a.allow_nonlocal_bind)
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
