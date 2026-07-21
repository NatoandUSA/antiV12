#!/usr/bin/env python3
"""Phase 7.6 — offline Manual Action Tracker over accepted Phase 7.5 decision packages.

This module is the ONE Phase 7.6 authority. It is a local RECORD-KEEPING layer only.

It connects to NOTHING. There is no Amazon integration, no SP-API, no Ads API, no browser
automation, and no network client of any kind. It never changes a bid, budget, keyword, negative,
target, or campaign, never uploads anything, and never claims that an Amazon action happened. The
owner is the only manual bridge to Amazon Seller Central: this tracker records, after the owner has
manually checked Seller Central, exactly what the OWNER says they did — nothing is ever inferred
from a Phase 7.5 approval.

What it does:
  * reads (read-only) a Phase 7.5 decision package (its accepted manifest + checklist) and treats
    those package items as the ONLY source of trackable actions — no owner-invented Amazon action
    without Phase 7.5 lineage is allowed;
  * records owner-entered outcomes (status, dates, before/after observations, notes, local evidence
    reference) in a deterministic, append-only, hash-chained local history under its own workspace;
  * never marks an item MANUALLY_COMPLETED unless the owner explicitly confirms, for that update,
    that they manually checked the current Seller Central state AND performed the action manually;
  * detects and reports package drift (a completed record tied to a changed/absent package is kept
    in history and visibly marked stale) and any tampering of its own state / history chain;
  * writes only under its own Phase 7.6 workspace, atomically, with compare-and-swap concurrency
    safety. It is strictly read-only toward every Phase 7.5 artifact.

Financial rules: owner-entered before/after values are preserved verbatim (exact strings). A value
that looks monetary requires an explicit currency; NaN/Infinity is rejected via the Decimal money
authority. Nothing is ever coerced to float and no bid/budget is ever computed here.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ("", "core", "production"):
    _p = _ROOT if not _sub else os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import phase7_owner_decision_package as PKG   # noqa: E402  Phase 7.5 authority (schema)
from production import phase7_owner_dashboard as DASH          # noqa: E402  formula-safe TSV / limits
from production import product_workspace as PW                 # noqa: E402  canonical json / content hash
from core import money as MONEY                                # noqa: E402  Decimal authority (no float)

canonical_json = PW.canonical_json
content_sha256 = PW.content_sha256

# ================================================================ constants / versions
STAGE_ID = "7.6"
STAGE_NAME = "Offline Manual Action Tracker"
GENERATOR_VERSION = "phase7-6-manual-action-tracker-v1"
RECORD_SCHEMA = "phase7-6-tracker-record-v1"
STATE_SCHEMA = "phase7-6-action-state-v1"
HISTORY_SCHEMA = "phase7-6-action-history-v1"
STATUS_EXPORT_SCHEMA = "phase7-6-manual-action-status-v1"

# Phase 7.5 authority (never assumed — imported from the accepted module).
PACKAGE_MANIFEST_SCHEMA = PKG.MANIFEST_SCHEMA          # "phase7-5-package-manifest-v1"
PACKAGE_CHECKLIST_SCHEMA = PKG.CHECKLIST_SCHEMA        # "phase7-5-manual-action-checklist-v1"
CHECKLIST_FILE = "manual_action_checklist.json"
MANIFEST_FILE = "package_manifest.json"

# ---------------------------------------------------------------- readiness states
TRACKER_READY = "SESSION7_6_TRACKER_READY"
TRACKER_READY_EMPTY = "SESSION7_6_TRACKER_READY_EMPTY"
PACKAGE_REQUIRED = "SESSION7_6_PACKAGE_REQUIRED"
PACKAGE_BLOCKED = "SESSION7_6_PACKAGE_BLOCKED"
STATE_REQUIRED = "SESSION7_6_STATE_REQUIRED"
STATE_BLOCKED = "SESSION7_6_STATE_BLOCKED"
HISTORY_BLOCKED = "SESSION7_6_HISTORY_BLOCKED"
PACKAGE_CHANGED_REVIEW_REQUIRED = "SESSION7_6_PACKAGE_CHANGED_REVIEW_REQUIRED"
CONFIRMATION_REQUIRED = "SESSION7_6_CONFIRMATION_REQUIRED"
VALIDATION_READY = "SESSION7_6_VALIDATION_READY"
UPDATE_REJECTED = "SESSION7_6_UPDATE_REJECTED"
CONCURRENT_MODIFICATION = "SESSION7_6_CONCURRENT_MODIFICATION"

# Everything that is not one of these three means "no valid result / attention required" -> nonzero.
_OK_READINESS = (TRACKER_READY, TRACKER_READY_EMPTY, VALIDATION_READY)

# ---------------------------------------------------------------- owner statuses
S_PENDING = "PENDING_MANUAL_CHECK"
S_COMPLETED = "MANUALLY_COMPLETED"
S_SKIPPED = "MANUALLY_SKIPPED"
S_NOT_RELEVANT = "NO_LONGER_RELEVANT"
S_NEEDS_REVIEW = "NEEDS_REVIEW"
S_BLOCKED_STATE = "BLOCKED_BY_CURRENT_STATE"
S_UNABLE = "UNABLE_TO_VERIFY"
S_REVERTED = "REVERTED_MANUALLY"
OWNER_STATUSES = (S_PENDING, S_COMPLETED, S_SKIPPED, S_NOT_RELEVANT, S_NEEDS_REVIEW,
                  S_BLOCKED_STATE, S_UNABLE, S_REVERTED)
DEFAULT_STATUS = S_PENDING
# Only MANUALLY_COMPLETED asserts the owner changed Seller Central; it carries the hard confirmation
# gate. Nothing else may claim an action happened.
_REQUIRES_CONFIRMATION = (S_COMPLETED,)

# ---------------------------------------------------------------- package binding states
B_CURRENT = "CURRENT"
B_PACKAGE_CHANGED = "PACKAGE_CHANGED"
B_PACKAGE_ITEM_CHANGED = "PACKAGE_ITEM_CHANGED"
B_PACKAGE_ITEM_ABSENT = "PACKAGE_ITEM_ABSENT"
B_PACKAGE_MANIFEST_MISMATCH = "PACKAGE_MANIFEST_MISMATCH"
B_PACKAGE_INTEGRITY_BLOCKED = "PACKAGE_INTEGRITY_BLOCKED"
_STALE_BINDINGS = (B_PACKAGE_CHANGED, B_PACKAGE_ITEM_CHANGED, B_PACKAGE_ITEM_ABSENT,
                   B_PACKAGE_MANIFEST_MISMATCH, B_PACKAGE_INTEGRITY_BLOCKED)

# ---------------------------------------------------------------- workspace
_WORKSPACE_SUBDIRS = ("action_state", "exports", "validation", "runtime", "logs")
STATE_FILE = "current.json"
HISTORY_FILE = "history.jsonl"
MAX_ARTIFACT_BYTES = DASH.MAX_ARTIFACT_BYTES

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
    "performs_the_action": True, "infers_completion_from_approval": True,
    "claims_amazon_confirmed_completion": True, "modifies_phase7_5_package": True,
}

DISCLAIMER_LINES = (
    "LOCAL OWNER-ENTERED TRACKER.",
    "No Amazon connection was made.",
    "No action was performed by this software.",
    "Status values represent owner-entered records only.",
    "This file is not an Amazon upload template and contains no API payload.",
)

# Human-readable status text. Completion NEVER claims Amazon confirmed anything.
STATUS_PHRASING = {
    S_PENDING: "Pending owner manual check in Seller Central.",
    S_COMPLETED: "Owner recorded this action as manually completed.",
    S_SKIPPED: "Owner recorded that they manually skipped this action.",
    S_NOT_RELEVANT: "Owner recorded that this item is no longer relevant.",
    S_NEEDS_REVIEW: "Owner recorded that this item needs another review.",
    S_BLOCKED_STATE: "Owner recorded that the current Seller Central state blocks this action.",
    S_UNABLE: "Owner recorded that they were unable to verify this item.",
    S_REVERTED: "Owner recorded that they manually reverted this action.",
}

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")   # a monetary-looking value -> currency required
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9:._-]+$")

# runtime-only fields never enter a record's stable content hash or the tracker identity.
_RUNTIME_RECORD_FIELDS = ("created_at", "updated_at", "record_content_sha256")

_TSV_CELL = DASH._tsv_cell  # reuse the accepted Phase 7.4/7.5 formula-injection rule verbatim


class TrackerError(Exception):
    """A structural failure that blocks a tracker operation. Carries a machine-readable code."""

    def __init__(self, code, detail=""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


# ================================================================ small helpers
def _now_utc():
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt):
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_hex64(v):
    return isinstance(v, str) and bool(_HEX64_RE.match(v))


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def _valid_date(v):
    if not isinstance(v, str) or not _DATE_RE.match(v):
        return False
    try:
        _dt.date.fromisoformat(v)
        return True
    except ValueError:
        return False


def _jsonl(obj):
    """Canonical single-line JSON (sorted keys, UTF-8 safe) + newline — the history line form."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"


def _read_bytes_bounded(path, what):
    size = os.path.getsize(path)
    if size > MAX_ARTIFACT_BYTES:
        raise TrackerError(f"{what}_OVERSIZE", str(size))
    with open(path, "rb") as f:
        data = f.read()
    if b"\x00" in data:
        raise TrackerError(f"{what}_NULL_BYTE", os.path.basename(path))
    return data


# ================================================================ Phase 7.5 package (read-only)
class Package:
    """A resolved, read-only view of a Phase 7.5 decision package."""

    __slots__ = ("package_id", "dir_name", "pkg_dir", "manifest", "manifest_file_sha256",
                 "content_sha256", "items", "items_by_id", "integrity_ok", "integrity_detail",
                 "error")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def packages_dir(phase7_5_dir):
    return os.path.join(os.path.abspath(phase7_5_dir), "packages")


def _resolve_package_dir(phase7_5_dir, package_id=None, dir_name=None):
    """Locate a package directory by explicit dir name, by id-as-dirname, or by scanning manifests."""
    root = packages_dir(phase7_5_dir)
    if dir_name:
        cand = os.path.join(root, dir_name)
        return cand if os.path.isfile(os.path.join(cand, MANIFEST_FILE)) else None
    if package_id:
        cand = os.path.join(root, package_id)  # default naming: dir == package_id
        if os.path.isfile(os.path.join(cand, MANIFEST_FILE)):
            return cand
        if os.path.isdir(root):
            for name in sorted(os.listdir(root)):
                mpath = os.path.join(root, name, MANIFEST_FILE)
                if not os.path.isfile(mpath):
                    continue
                try:
                    with open(mpath, "rb") as f:
                        m = json.loads(f.read().decode("utf-8"))
                except (ValueError, OSError):
                    continue
                if isinstance(m, dict) and m.get("package_id") == package_id:
                    return os.path.join(root, name)
    return None


def load_package(phase7_5_dir, *, package_id=None, dir_name=None):
    """Load a package read-only. Returns a Package (never raises for data reasons — structural
    problems surface as .error / .integrity_ok=False so callers can gate deliberately). Returns None
    only when no package directory can be resolved at all."""
    pkg_dir = _resolve_package_dir(phase7_5_dir, package_id=package_id, dir_name=dir_name)
    if pkg_dir is None:
        return None
    p = Package(pkg_dir=pkg_dir, dir_name=os.path.basename(pkg_dir), integrity_ok=False,
                items=[], items_by_id={})
    mpath = os.path.join(pkg_dir, MANIFEST_FILE)
    try:
        mdata = _read_bytes_bounded(mpath, "PACKAGE_MANIFEST")
        manifest = json.loads(mdata.decode("utf-8"))
    except TrackerError as e:
        p.error = f"{e.code}: {e.detail}"
        return p
    except (ValueError, UnicodeDecodeError) as e:
        p.error = f"PACKAGE_MANIFEST_MALFORMED: {e}"
        return p
    if not isinstance(manifest, dict) or manifest.get("schema_version") != PACKAGE_MANIFEST_SCHEMA:
        p.error = f"PACKAGE_MANIFEST_SCHEMA: {manifest.get('schema_version') if isinstance(manifest, dict) else type(manifest).__name__}"
        return p
    p.manifest = manifest
    p.manifest_file_sha256 = _sha256_hex(mdata)
    p.package_id = manifest.get("package_id")
    p.content_sha256 = manifest.get("package_content_sha256")

    # ---- integrity: every declared artifact must hash to its recorded value --------------------
    artifact_hashes = manifest.get("artifact_sha256")
    if not isinstance(artifact_hashes, dict):
        p.error = "PACKAGE_MANIFEST_NO_ARTIFACT_HASHES"
        return p
    integrity_ok = True
    detail = ""
    for name in sorted(artifact_hashes):
        want = artifact_hashes[name]
        apath = os.path.join(pkg_dir, name)
        if not os.path.isfile(apath):
            integrity_ok, detail = False, f"PACKAGE_ARTIFACT_MISSING: {name}"
            break
        with open(apath, "rb") as f:
            got = _sha256_hex(f.read())
        if got != want:
            integrity_ok, detail = False, f"PACKAGE_HASH_MISMATCH: {name}"
            break
    p.integrity_ok = integrity_ok
    p.integrity_detail = detail

    # ---- checklist items (source of trackable actions) -----------------------------------------
    cpath = os.path.join(pkg_dir, CHECKLIST_FILE)
    if os.path.isfile(cpath):
        try:
            cdata = _read_bytes_bounded(cpath, "PACKAGE_CHECKLIST")
            checklist = json.loads(cdata.decode("utf-8"))
        except (TrackerError, ValueError, UnicodeDecodeError) as e:
            p.error = p.error or f"PACKAGE_CHECKLIST_MALFORMED: {e}"
            return p
        items = checklist.get("items") if isinstance(checklist, dict) else None
        if not isinstance(items, list):
            p.error = p.error or "PACKAGE_CHECKLIST_NO_ITEMS"
            return p
        p.items = items
        p.items_by_id = {it.get("package_item_id"): it for it in items
                         if isinstance(it, dict) and it.get("package_item_id")}
    else:
        p.error = p.error or "PACKAGE_CHECKLIST_MISSING"
    return p


# ================================================================ tracker record identity & content
def tracker_record_id(package_id, package_item_id):
    """Deterministic, stable identity — a pure function of (package id, package item id).
    Never depends on row number, timestamp, random UUID, display order, or filename."""
    ident = {"kind": "phase7-6-tracker-record-id",
             "package_id": _s(package_id), "package_item_id": _s(package_item_id)}
    return "trk-" + content_sha256(ident)[:32]


def _s(v):
    return "" if v is None else str(v)


def _record_content_hash(record):
    stable = {k: v for k, v in record.items() if k not in _RUNTIME_RECORD_FIELDS}
    return content_sha256(stable)


def _source_snapshot(item):
    """Immutable reference copy of the package item's identifying fields (for display only)."""
    return {
        "entity_id": item.get("entity_id"),
        "campaign": item.get("campaign"),
        "ad_group": item.get("ad_group"),
        "target": item.get("target"),
        "customer_search_term": item.get("customer_search_term"),
        "match_type": item.get("match_type"),
        "currency": item.get("currency"),
        "attribution_window": item.get("attribution_window"),
        "action_description": item.get("action_description"),
    }


def _new_record(package, item, reference_date, *, now):
    pid = package.package_id
    iid = item.get("package_item_id")
    rec = {
        "schema_version": RECORD_SCHEMA,
        "tracker_record_id": tracker_record_id(pid, iid),
        "package_id": pid,
        "package_dir_name": package.dir_name,
        "package_manifest_sha256": package.manifest_file_sha256,
        "package_content_sha256": package.content_sha256,
        "package_item_id": iid,
        "source_recommendation_id": item.get("source_recommendation_id"),
        "entity_type": item.get("entity_type"),
        "recommendation_type": item.get("recommendation_type"),
        "item_content_sha256": item.get("content_sha256"),
        "source_snapshot": _source_snapshot(item),
        "reference_date": reference_date,
        "owner_status": DEFAULT_STATUS,
        "owner_checked_date": None,
        "owner_completed_date": None,
        "owner_note": "",
        "before_value": None,
        "after_value": None,
        "before_value_currency": None,
        "after_value_currency": None,
        "manually_verified_campaign": None,
        "manually_verified_ad_group": None,
        "manually_verified_target_or_term": None,
        "confirmed_manual_seller_central_check": False,
        "confirmed_owner_performed_action": False,
        "evidence_reference": None,
        "evidence_sha256": None,
        "local_revision": 1,
        "previous_record_hash": None,
        "created_at": _iso(now()),
        "updated_at": _iso(now()),
        "record_content_sha256": None,
    }
    rec["record_content_sha256"] = _record_content_hash(rec)
    return rec


# ================================================================ update construction / gates
_MERGE_FIELDS = ("owner_checked_date", "owner_completed_date", "owner_note", "before_value",
                 "after_value", "before_value_currency", "after_value_currency",
                 "manually_verified_campaign", "manually_verified_ad_group",
                 "manually_verified_target_or_term", "evidence_reference")


def _validate_value_and_currency(value, currency, field):
    """Owner-entered observations are preserved verbatim. A monetary-looking value REQUIRES a
    currency and must be finite (never NaN/Infinity, never float). Returns nothing; raises on error."""
    if value is None:
        return
    if not isinstance(value, str):
        raise TrackerError("VALUE_NOT_STRING", f"{field} must be a string")
    if _NUMERIC_RE.match(value.strip()):
        # finite-Decimal check (rejects NaN/Infinity/exponent) — value itself is preserved as-is.
        MONEY.parse_decimal_string(value.strip(), field=field, allow_missing=False)
        if not currency:
            raise TrackerError("CURRENCY_REQUIRED",
                               f"{field} '{value}' is monetary; a currency is required")
    else:
        low = value.lower()
        if "nan" in low or "inf" in low:
            raise TrackerError("NON_FINITE_VALUE", f"{field} '{value}' is not a finite value")


def _completion_gate(record, confirm_check, confirm_action):
    """Return a list of reason dicts for why MANUALLY_COMPLETED is not permitted (empty == allowed)."""
    reasons = []
    if not confirm_check:
        reasons.append({"code": "MISSING_SELLER_CENTRAL_CHECK_CONFIRMATION",
                        "detail": "owner did not confirm the current Seller Central state was checked"})
    if not confirm_action:
        reasons.append({"code": "MISSING_OWNER_ACTION_CONFIRMATION",
                        "detail": "owner did not confirm they performed the action manually"})
    if not _valid_date(record.get("owner_checked_date")):
        reasons.append({"code": "MISSING_OWNER_CHECKED_DATE",
                        "detail": "owner_checked_date (YYYY-MM-DD) is required to record completion"})
    if not _valid_date(record.get("owner_completed_date")):
        reasons.append({"code": "MISSING_OWNER_COMPLETED_DATE",
                        "detail": "owner_completed_date (YYYY-MM-DD) is required to record completion"})
    has_outcome = bool((record.get("owner_note") or "").strip()) or record.get("after_value") is not None
    if not has_outcome:
        reasons.append({"code": "MISSING_NOTE_OR_OUTCOME",
                        "detail": "an owner note or a manually entered after_value is required"})
    return reasons


def build_next_record(prev, *, status, updates, confirm_check, confirm_action,
                      evidence_sha256, binding_state, now):
    """Construct the next revision of a record from its previous revision + this update.

    Merge policy: confirmation flags and status are per-revision (a completion assertion is made in
    the moment); observation fields merge over the previous revision unless explicitly supplied.
    Raises TrackerError on invalid input. Returns (record, reasons) — reasons non-empty means the
    update is not permitted (caller must not persist it)."""
    if status not in OWNER_STATUSES:
        raise TrackerError("INVALID_STATUS", f"{status!r} is not an owner status")

    rec = dict(prev)
    rec["schema_version"] = RECORD_SCHEMA
    rec["owner_status"] = status
    # per-revision confirmation (never merged forward — completion must be re-asserted each time).
    rec["confirmed_manual_seller_central_check"] = bool(confirm_check)
    rec["confirmed_owner_performed_action"] = bool(confirm_action)

    for f in _MERGE_FIELDS:
        if f in updates and updates[f] is not None:
            rec[f] = updates[f]
    if evidence_sha256 is not None:
        rec["evidence_sha256"] = evidence_sha256

    for dfield in ("owner_checked_date", "owner_completed_date"):
        v = rec.get(dfield)
        if v is not None and not _valid_date(v):
            raise TrackerError("INVALID_DATE", f"{dfield} must be YYYY-MM-DD, got {v!r}")

    _validate_value_and_currency(rec.get("before_value"), rec.get("before_value_currency"),
                                 "before_value")
    _validate_value_and_currency(rec.get("after_value"), rec.get("after_value_currency"),
                                 "after_value")

    reasons = []
    if status in _REQUIRES_CONFIRMATION:
        reasons = _completion_gate(rec, confirm_check, confirm_action)
        if not reasons and binding_state in _STALE_BINDINGS:
            reasons.append({"code": "PACKAGE_BINDING_STALE",
                            "detail": f"cannot record completion against a {binding_state} package"})

    rec["local_revision"] = int(prev["local_revision"]) + 1
    rec["previous_record_hash"] = prev["record_content_sha256"]
    rec["created_at"] = prev.get("created_at")
    rec["updated_at"] = _iso(now())
    rec["record_content_sha256"] = None
    rec["record_content_sha256"] = _record_content_hash(rec)
    return rec, reasons


# ================================================================ state / history (append-only)
def _state_content_sha256(records):
    """CAS anchor — a deterministic digest over each record's content hash."""
    return content_sha256({rid: records[rid]["record_content_sha256"] for rid in sorted(records)})


def _history_chain_sha256(events):
    chain = [[e["event_seq"], e["tracker_record_id"], e["local_revision"],
              e["record_content_sha256"]] for e in events]
    return content_sha256(chain)


def state_paths(base_dir):
    base = os.path.abspath(base_dir)
    d = os.path.join(base, "action_state")
    return os.path.join(d, STATE_FILE), os.path.join(d, HISTORY_FILE)


def ensure_workspace(base_dir):
    base = os.path.abspath(base_dir)
    for sub in _WORKSPACE_SUBDIRS:
        os.makedirs(os.path.join(base, sub), exist_ok=True)
    return base


def _parse_history(base_dir):
    """Parse and structurally validate history.jsonl. Returns the ordered event list.
    Raises TrackerError (HISTORY_*) on any corruption of the append-only chain."""
    _, hpath = state_paths(base_dir)
    if not os.path.isfile(hpath):
        return []
    data = _read_bytes_bounded(hpath, "HISTORY")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise TrackerError("HISTORY_ENCODING", str(e)) from e
    events = []
    for i, line in enumerate(text.splitlines(), 1):
        if line.strip() == "":
            continue
        try:
            ev = json.loads(line)
        except ValueError as e:
            raise TrackerError("HISTORY_MALFORMED", f"line {i}: {e}") from e
        if not isinstance(ev, dict):
            raise TrackerError("HISTORY_MALFORMED", f"line {i}: not an object")
        for key in ("event_seq", "tracker_record_id", "local_revision", "previous_record_hash",
                    "record_content_sha256", "record"):
            if key not in ev:
                raise TrackerError("HISTORY_MALFORMED", f"line {i}: missing {key}")
        events.append(ev)

    # global sequence: strictly 1..N in file order, no gaps, no duplicates, no rollback.
    for idx, ev in enumerate(events, 1):
        if ev["event_seq"] != idx:
            raise TrackerError("HISTORY_SEQ_GAP",
                               f"expected event_seq {idx}, found {ev['event_seq']}")

    # per-record chain: contiguous revisions from 1, correct previous-hash links, no rewrite.
    by_rec = {}
    for ev in events:
        by_rec.setdefault(ev["tracker_record_id"], []).append(ev)
    for rid, evs in by_rec.items():
        prev_hash = None
        for pos, ev in enumerate(evs, 1):
            if ev["local_revision"] != pos:
                if any(e["local_revision"] == ev["local_revision"] for e in evs[:pos - 1]):
                    raise TrackerError("HISTORY_DUP_REVISION",
                                       f"{rid}: revision {ev['local_revision']} repeats")
                if ev["local_revision"] < pos:
                    raise TrackerError("HISTORY_REVISION_ROLLBACK",
                                       f"{rid}: revision {ev['local_revision']} rolls back")
                raise TrackerError("HISTORY_MISSING_ENTRY",
                                   f"{rid}: expected revision {pos}, found {ev['local_revision']}")
            if ev["previous_record_hash"] != prev_hash:
                raise TrackerError("HISTORY_CHAIN_BROKEN",
                                   f"{rid} revision {pos}: previous_record_hash does not match prior")
            rec = ev["record"]
            if not isinstance(rec, dict):
                raise TrackerError("HISTORY_MALFORMED", f"{rid} revision {pos}: record not an object")
            recomputed = _record_content_hash(rec)
            if recomputed != ev["record_content_sha256"] or rec.get("record_content_sha256") != ev["record_content_sha256"]:
                raise TrackerError("HISTORY_REWRITTEN",
                                   f"{rid} revision {pos}: record content hash does not verify")
            if rec.get("local_revision") != ev["local_revision"] or rec.get("tracker_record_id") != rid:
                raise TrackerError("HISTORY_REWRITTEN",
                                   f"{rid} revision {pos}: embedded record identity mismatch")
            prev_hash = ev["record_content_sha256"]
    return events


def _latest_records_from_history(events):
    latest = {}
    for ev in events:
        latest[ev["tracker_record_id"]] = ev["record"]
    return latest


def load_state(base_dir):
    """Load + fully validate current state and its history. Returns a dict:
        {"exists": bool, "state": <doc or None>, "events": [...], "records": {rid: record}}
    Raises TrackerError (STATE_* / HISTORY_* / CURRENT_HISTORY_MISMATCH) on any corruption."""
    spath, _ = state_paths(base_dir)
    events = _parse_history(base_dir)
    if not os.path.isfile(spath):
        if events:
            raise TrackerError("CURRENT_HISTORY_MISMATCH", "history exists without a current state")
        return {"exists": False, "state": None, "events": [], "records": {}}

    data = _read_bytes_bounded(spath, "STATE")
    try:
        doc = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise TrackerError("STATE_MALFORMED", str(e)) from e
    if not isinstance(doc, dict):
        raise TrackerError("STATE_MALFORMED", "root is not an object")
    if doc.get("schema_version") != STATE_SCHEMA:
        raise TrackerError("STATE_SCHEMA", str(doc.get("schema_version")))
    records = doc.get("records")
    if not isinstance(records, dict):
        raise TrackerError("STATE_MALFORMED", "records is not an object")

    # each stored record must self-verify (detects a hand-edited current.json).
    for rid, rec in records.items():
        if not isinstance(rec, dict):
            raise TrackerError("STATE_MALFORMED", f"record {rid} is not an object")
        if rec.get("tracker_record_id") != rid:
            raise TrackerError("STATE_MALFORMED", f"record {rid} identity mismatch")
        if _record_content_hash(rec) != rec.get("record_content_sha256"):
            raise TrackerError("STATE_HASH_MISMATCH", f"record {rid} content hash does not verify")

    if doc.get("state_content_sha256") != _state_content_sha256(records):
        raise TrackerError("STATE_HASH_MISMATCH", "state_content_sha256 does not match records")
    if doc.get("history_chain_sha256") != _history_chain_sha256(events):
        raise TrackerError("STATE_HASH_MISMATCH", "history_chain_sha256 does not match history")

    # current must equal the latest history revision for every record, and cover exactly them.
    latest = _latest_records_from_history(events)
    if set(latest) != set(records):
        raise TrackerError("CURRENT_HISTORY_MISMATCH", "record set differs from history")
    for rid, rec in records.items():
        if rec.get("record_content_sha256") != latest[rid].get("record_content_sha256"):
            raise TrackerError("CURRENT_HISTORY_MISMATCH", f"record {rid} is not the latest revision")
        if int(rec.get("local_revision", 0)) != int(latest[rid].get("local_revision", -1)):
            raise TrackerError("CURRENT_HISTORY_MISMATCH", f"record {rid} revision differs from history")
    return {"exists": True, "state": doc, "events": events, "records": records}


def _build_state_doc(records, events, reference_date):
    return {
        "schema_version": STATE_SCHEMA,
        "stage_id": STAGE_ID,
        "generator_version": GENERATOR_VERSION,
        "reference_date": reference_date,
        "record_count": len(records),
        "history_event_count": len(events),
        "state_content_sha256": _state_content_sha256(records),
        "history_chain_sha256": _history_chain_sha256(events),
        "records": records,
        "amazon_boundary": dict(AMAZON_COUNTERS),
        "this_session_never": dict(THIS_SESSION_NEVER),
    }


# ================================================================ atomic commit (CAS)
def _write_bytes(path, data):
    with open(path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _commit(base_dir, records, events, reference_date, *, observed_state_sha):
    """Atomically publish new history + current state. Compare-and-swap on the observed state hash
    guards against a concurrent writer (no last-write-wins). Both temp files are written and fsynced
    before either replace, so a failure before the replaces preserves the previous files intact."""
    ensure_workspace(base_dir)
    spath, hpath = state_paths(base_dir)

    # ---- CAS re-read guard: current.json on disk must still match what we loaded ---------------
    disk_sha = None
    if os.path.isfile(spath):
        try:
            with open(spath, "rb") as f:
                disk_doc = json.loads(f.read().decode("utf-8"))
            disk_sha = disk_doc.get("state_content_sha256") if isinstance(disk_doc, dict) else None
        except (ValueError, OSError, UnicodeDecodeError):
            disk_sha = "<unreadable>"
    if disk_sha != observed_state_sha:
        raise TrackerError("CONCURRENT_MODIFICATION",
                           "current state changed on disk since it was loaded; reload and retry")

    doc = _build_state_doc(records, events, reference_date)
    hist_bytes = ("".join(_jsonl(ev) for ev in events)).encode("utf-8")
    state_bytes = (canonical_json(doc) + "\n").encode("utf-8")

    tmp_h = hpath + ".tmp"
    tmp_s = spath + ".tmp"
    _write_bytes(tmp_h, hist_bytes)
    _write_bytes(tmp_s, state_bytes)
    os.replace(tmp_h, hpath)     # history first ...
    os.replace(tmp_s, spath)     # ... then current (spec-ordered; any gap is later detectable)
    return doc


# ================================================================ package binding evaluation
def binding_state(record, phase7_5_dir):
    """Evaluate a record against its bound Phase 7.5 package on disk. Returns (state, detail)."""
    pkg = load_package(phase7_5_dir, package_id=record.get("package_id"),
                       dir_name=record.get("package_dir_name"))
    # Checks run strongest-signal first; the raw manifest-bytes check is last (weakest), so an
    # item- or content-level drift is never masked by a benign manifest re-emission.
    if pkg is None:
        return B_PACKAGE_ITEM_ABSENT, "bound package is absent"
    if pkg.error:
        return B_PACKAGE_INTEGRITY_BLOCKED, pkg.error
    if not pkg.integrity_ok:
        return B_PACKAGE_INTEGRITY_BLOCKED, pkg.integrity_detail
    if pkg.content_sha256 != record.get("package_content_sha256"):
        return B_PACKAGE_CHANGED, "package content hash differs from the recorded binding"
    item = pkg.items_by_id.get(record.get("package_item_id"))
    if item is None:
        return B_PACKAGE_ITEM_ABSENT, "package item is absent from the current package"
    if item.get("content_sha256") != record.get("item_content_sha256"):
        return B_PACKAGE_ITEM_CHANGED, "package item content hash differs from the recorded binding"
    if pkg.manifest_file_sha256 != record.get("package_manifest_sha256"):
        return B_PACKAGE_MANIFEST_MISMATCH, "package manifest bytes differ from the recorded binding"
    return B_CURRENT, ""


def _binding_map(records, phase7_5_dir):
    return {rid: binding_state(rec, phase7_5_dir) for rid, rec in records.items()}


# ================================================================ commands
def _result(command, readiness, **kw):
    res = {
        "command": command,
        "readiness": readiness,
        "reasons": kw.pop("reasons", []),
        "amazon_counters": dict(AMAZON_COUNTERS),
    }
    res.update(kw)
    return res


def _err_readiness(command, code):
    if code.startswith("HISTORY"):
        return HISTORY_BLOCKED
    if code.startswith("STATE") or code == "CURRENT_HISTORY_MISMATCH":
        return STATE_BLOCKED
    if code == "CONCURRENT_MODIFICATION":
        return CONCURRENT_MODIFICATION
    if code.startswith("PACKAGE"):
        return PACKAGE_BLOCKED
    if code.startswith("MISSING_") or code == "PACKAGE_BINDING_STALE":
        return CONFIRMATION_REQUIRED
    return UPDATE_REJECTED


def run_initialize(base_dir, phase7_5_dir, reference_date, *, package_id=None, dir_name=None,
                   now=None):
    now = now or _now_utc
    pkg = load_package(phase7_5_dir, package_id=package_id, dir_name=dir_name)
    if pkg is None:
        return _result("initialize", PACKAGE_REQUIRED,
                       reasons=[{"code": "PACKAGE_NOT_FOUND",
                                 "detail": f"no package for id={package_id!r} dir={dir_name!r}"}])
    if pkg.error or not pkg.integrity_ok:
        return _result("initialize", PACKAGE_BLOCKED,
                       reasons=[{"code": "PACKAGE_INTEGRITY",
                                 "detail": pkg.error or pkg.integrity_detail}],
                       package_id=pkg.package_id)
    try:
        loaded = load_state(base_dir)
    except TrackerError as e:
        return _result("initialize", _err_readiness("initialize", e.code),
                       reasons=[{"code": e.code, "detail": e.detail}])

    records = dict(loaded["records"])
    events = list(loaded["events"])
    observed = loaded["state"]["state_content_sha256"] if loaded["exists"] else None
    created = 0
    for item in pkg.items:
        if not isinstance(item, dict) or not item.get("package_item_id"):
            continue
        rid = tracker_record_id(pkg.package_id, item["package_item_id"])
        if rid in records:
            continue
        rec = _new_record(pkg, item, reference_date, now=now)
        records[rid] = rec
        events.append({
            "event_seq": len(events) + 1,
            "action": "INITIALIZE",
            "tracker_record_id": rid,
            "local_revision": rec["local_revision"],
            "previous_record_hash": rec["previous_record_hash"],
            "record_content_sha256": rec["record_content_sha256"],
            "reference_date": reference_date,
            "record": rec,
        })
        created += 1

    doc = _commit(base_dir, records, events, reference_date, observed_state_sha=observed)
    readiness = TRACKER_READY if records else TRACKER_READY_EMPTY
    return _result("initialize", readiness, package_id=pkg.package_id,
                   record_count=len(records), created=created,
                   state_content_sha256=doc["state_content_sha256"], records=records)


def run_update(base_dir, phase7_5_dir, reference_date, *, package_id, package_item_id, status,
               updates=None, confirm_check=False, confirm_action=False, evidence_file=None,
               expected_state_sha256=None, expected_record_hash=None, now=None):
    now = now or _now_utc
    updates = updates or {}
    try:
        loaded = load_state(base_dir)
    except TrackerError as e:
        return _result("update", _err_readiness("update", e.code),
                       reasons=[{"code": e.code, "detail": e.detail}])

    records = dict(loaded["records"])
    events = list(loaded["events"])
    observed = loaded["state"]["state_content_sha256"] if loaded["exists"] else None

    if expected_state_sha256 is not None and expected_state_sha256 != (observed or ""):
        return _result("update", CONCURRENT_MODIFICATION,
                       reasons=[{"code": "EXPECTED_STATE_SHA_MISMATCH",
                                 "detail": "expected state hash does not match current state"}])

    rid = tracker_record_id(package_id, package_item_id)
    prev = records.get(rid)

    # package binding — establishes lineage and drift; an owner may never invent an Amazon action.
    pkg = load_package(phase7_5_dir, package_id=package_id, dir_name=None)
    if prev is None:
        if pkg is None or pkg.error or not pkg.integrity_ok:
            return _result("update", PACKAGE_BLOCKED,
                           reasons=[{"code": "NO_PACKAGE_LINEAGE",
                                     "detail": "no tracked record and package is unavailable/invalid"}])
        item = pkg.items_by_id.get(package_item_id)
        if item is None:
            return _result("update", PACKAGE_BLOCKED,
                           reasons=[{"code": "NO_PACKAGE_LINEAGE",
                                     "detail": f"package item {package_item_id!r} not in package"}])
        prev = _new_record(pkg, item, reference_date, now=now)  # seed rev 1 implicitly
        records[rid] = prev
        events.append({
            "event_seq": len(events) + 1, "action": "INITIALIZE",
            "tracker_record_id": rid, "local_revision": prev["local_revision"],
            "previous_record_hash": prev["previous_record_hash"],
            "record_content_sha256": prev["record_content_sha256"],
            "reference_date": reference_date, "record": prev,
        })

    if expected_record_hash is not None and expected_record_hash != prev["record_content_sha256"]:
        return _result("update", CONCURRENT_MODIFICATION,
                       reasons=[{"code": "EXPECTED_RECORD_HASH_MISMATCH",
                                 "detail": "expected record hash does not match current record"}])

    b_state, b_detail = binding_state(prev, phase7_5_dir)

    evidence_sha = None
    if evidence_file is not None:
        if not os.path.isfile(evidence_file):
            return _result("update", UPDATE_REJECTED,
                           reasons=[{"code": "EVIDENCE_FILE_MISSING", "detail": evidence_file}])
        evidence_sha = _sha256_hex(_read_bytes_bounded(evidence_file, "EVIDENCE"))
        updates = dict(updates)
        updates.setdefault("evidence_reference", os.path.basename(evidence_file))

    try:
        new_rec, reasons = build_next_record(
            prev, status=status, updates=updates, confirm_check=confirm_check,
            confirm_action=confirm_action, evidence_sha256=evidence_sha,
            binding_state=b_state, now=now)
    except TrackerError as e:
        return _result("update", _err_readiness("update", e.code),
                       reasons=[{"code": e.code, "detail": e.detail}],
                       binding_state=b_state)

    if reasons:
        readiness = (PACKAGE_CHANGED_REVIEW_REQUIRED
                     if any(r["code"] == "PACKAGE_BINDING_STALE" for r in reasons)
                     else CONFIRMATION_REQUIRED)
        return _result("update", readiness, reasons=reasons, binding_state=b_state,
                       package_id=package_id, package_item_id=package_item_id)

    records[rid] = new_rec
    events.append({
        "event_seq": len(events) + 1, "action": "UPDATE",
        "tracker_record_id": rid, "local_revision": new_rec["local_revision"],
        "previous_record_hash": new_rec["previous_record_hash"],
        "record_content_sha256": new_rec["record_content_sha256"],
        "reference_date": reference_date, "record": new_rec,
    })
    doc = _commit(base_dir, records, events, reference_date, observed_state_sha=observed)
    return _result("update", TRACKER_READY, package_id=package_id, package_item_id=package_item_id,
                   binding_state=b_state, record=new_rec, local_revision=new_rec["local_revision"],
                   status_phrase=STATUS_PHRASING[status],
                   state_content_sha256=doc["state_content_sha256"],
                   record_count=len(records))


def _load_for_read(command, base_dir):
    """Shared loader for the read-only commands. Returns (loaded, error_result-or-None)."""
    try:
        loaded = load_state(base_dir)
    except TrackerError as e:
        return None, _result(command, _err_readiness(command, e.code),
                             reasons=[{"code": e.code, "detail": e.detail}])
    if not loaded["exists"]:
        return None, _result(command, STATE_REQUIRED,
                             reasons=[{"code": "STATE_NOT_INITIALIZED",
                                       "detail": "no tracker state; run initialize first"}])
    return loaded, None


def run_list(base_dir, phase7_5_dir, reference_date):
    loaded, err = _load_for_read("list", base_dir)
    if err:
        return err
    records = loaded["records"]
    binds = _binding_map(records, phase7_5_dir)
    rows = _status_rows(records, binds)
    readiness = TRACKER_READY if records else TRACKER_READY_EMPTY
    stale = sum(1 for _rid, (st, _d) in binds.items() if st in _STALE_BINDINGS)
    return _result("list", readiness, record_count=len(records), rows=rows,
                   stale_count=stale, records=records, bindings=binds)


def run_show(base_dir, phase7_5_dir, reference_date, *, package_id=None, package_item_id=None,
             tracker_record_id_=None):
    loaded, err = _load_for_read("show", base_dir)
    if err:
        return err
    rid = tracker_record_id_ or tracker_record_id(package_id, package_item_id)
    rec = loaded["records"].get(rid)
    if rec is None:
        return _result("show", UPDATE_REJECTED,
                       reasons=[{"code": "RECORD_NOT_FOUND", "detail": rid}])
    b_state, b_detail = binding_state(rec, phase7_5_dir)
    history = [ev for ev in loaded["events"] if ev["tracker_record_id"] == rid]
    return _result("show", TRACKER_READY, record=rec, binding_state=b_state,
                   binding_detail=b_detail, history=history,
                   status_phrase=STATUS_PHRASING.get(rec["owner_status"]))


def run_history(base_dir, phase7_5_dir, reference_date, *, package_id=None, package_item_id=None):
    loaded, err = _load_for_read("history", base_dir)
    if err:
        return err
    events = loaded["events"]
    if package_id and package_item_id:
        rid = tracker_record_id(package_id, package_item_id)
        events = [ev for ev in events if ev["tracker_record_id"] == rid]
    return _result("history", TRACKER_READY if loaded["records"] else TRACKER_READY_EMPTY,
                   events=events, event_count=len(events), records=loaded["records"])


def run_validate(base_dir, phase7_5_dir, reference_date):
    try:
        loaded = load_state(base_dir)
    except TrackerError as e:
        return _result("validate", _err_readiness("validate", e.code),
                       reasons=[{"code": e.code, "detail": e.detail}])
    if not loaded["exists"]:
        return _result("validate", STATE_REQUIRED,
                       reasons=[{"code": "STATE_NOT_INITIALIZED",
                                 "detail": "no tracker state; run initialize first"}])
    records = loaded["records"]
    binds = _binding_map(records, phase7_5_dir)
    stale = {rid: st for rid, (st, _d) in binds.items() if st in _STALE_BINDINGS}
    if stale:
        return _result("validate", PACKAGE_CHANGED_REVIEW_REQUIRED, record_count=len(records),
                       reasons=[{"code": st, "detail": rid} for rid, st in sorted(stale.items())],
                       stale_count=len(stale), bindings=binds)
    return _result("validate", VALIDATION_READY, record_count=len(records),
                   stale_count=0, bindings=binds,
                   state_content_sha256=loaded["state"]["state_content_sha256"],
                   history_chain_sha256=loaded["state"]["history_chain_sha256"])


# ================================================================ exports (deterministic, local)
STATUS_COLUMNS = ("package_id", "package_item_id", "tracker_record_id", "entity_type",
                  "recommendation_type", "owner_status", "owner_checked_date",
                  "owner_completed_date", "before_value", "before_value_currency",
                  "after_value", "after_value_currency", "owner_note",
                  "manually_verified_campaign", "manually_verified_ad_group",
                  "manually_verified_target_or_term", "evidence_reference", "evidence_sha256",
                  "binding_state", "local_revision", "record_content_sha256")


def _status_rows(records, binds):
    rows = []
    for rid in sorted(records):
        rec = records[rid]
        b_state = binds.get(rid, (B_CURRENT, ""))[0]
        row = {c: rec.get(c) for c in STATUS_COLUMNS if c in rec}
        row["tracker_record_id"] = rid
        row["binding_state"] = b_state
        rows.append(row)
    return rows


def _disclaimer_hash_comment():
    return "\n".join("# " + line for line in DISCLAIMER_LINES)


def _disclaimer_md(prefix="> "):
    return "\n".join(prefix + line for line in DISCLAIMER_LINES)


def _tsv(columns, rows):
    lines = ["\t".join(columns)]
    for r in rows:
        lines.append("\t".join(_TSV_CELL(r.get(c)) for c in columns))
    return "\n".join(lines) + "\n"


def _status_tsv(rows):
    return _disclaimer_hash_comment() + "\n" + _tsv(STATUS_COLUMNS, rows)


def _status_json(records, binds, reference_date):
    rows = _status_rows(records, binds)
    meta = {
        "schema_version": STATUS_EXPORT_SCHEMA,
        "stage_id": STAGE_ID,
        "generator_version": GENERATOR_VERSION,
        "reference_date": reference_date,
        "record_count": len(records),
        "stale_count": sum(1 for r in rows if r["binding_state"] in _STALE_BINDINGS),
        "owner_entered_only": True,
        "amazon_action_performed": False,
        "manual_action_disclaimer": list(DISCLAIMER_LINES),
    }
    return canonical_json({"meta": meta, "records": rows}) + "\n"


def _history_md(records, events):
    out = ["# Manual Action History — LOCAL OWNER-ENTERED TRACKER", "", _disclaimer_md(), "",
           f"- Records: {len(records)}", f"- History events: {len(events)}", ""]
    by_rec = {}
    for ev in events:
        by_rec.setdefault(ev["tracker_record_id"], []).append(ev)
    for i, rid in enumerate(sorted(by_rec), 1):
        evs = by_rec[rid]
        head = evs[-1]["record"]
        out += [f"## {i}. `{rid}`", "",
                f"- Package item: `{head.get('package_item_id')}`",
                f"- Package id: `{head.get('package_id')}`",
                f"- Recommendation type: {head.get('recommendation_type') or '—'}",
                f"- Current owner status: {head.get('owner_status')} "
                f"({STATUS_PHRASING.get(head.get('owner_status'), '')})", ""]
        for ev in evs:
            rec = ev["record"]
            out += [f"- Revision {ev['local_revision']} ({ev['action']}): "
                    f"status={rec.get('owner_status')}, checked={rec.get('owner_checked_date') or '—'}, "
                    f"completed={rec.get('owner_completed_date') or '—'}, "
                    f"before={rec.get('before_value') or '—'} {rec.get('before_value_currency') or ''}, "
                    f"after={rec.get('after_value') or '—'} {rec.get('after_value_currency') or ''}, "
                    f"note={rec.get('owner_note') or '—'}"]
        out.append("")
    return "\n".join(out)


def _write_atomic_file(path, data):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    _write_bytes(tmp, data)
    os.replace(tmp, path)


def run_export(base_dir, phase7_5_dir, reference_date):
    loaded, err = _load_for_read("export", base_dir)
    if err:
        return err
    base = ensure_workspace(base_dir)
    records = loaded["records"]
    events = loaded["events"]
    binds = _binding_map(records, phase7_5_dir)
    rows = _status_rows(records, binds)
    artifacts = {
        "manual_action_status.tsv": _status_tsv(rows),
        "manual_action_status.json": _status_json(records, binds, reference_date),
        "manual_action_history.md": _history_md(records, events),
    }
    written = []
    for name, text in sorted(artifacts.items()):
        path = os.path.join(base, "exports", name)
        _write_atomic_file(path, text.encode("utf-8"))
        written.append(path)
    readiness = TRACKER_READY if records else TRACKER_READY_EMPTY
    return _result("export", readiness, record_count=len(records), exports=written,
                   artifacts={k: content_sha256(v) for k, v in artifacts.items()})


# ================================================================ CLI
def exit_code_for(readiness):
    return 0 if readiness in _OK_READINESS else 2


def _summary_pairs(result):
    ac = result["amazon_counters"]
    pairs = [
        ("command", result.get("command")),
        ("tracker_readiness", result.get("readiness")),
        ("record_count", result.get("record_count")),
    ]
    for opt in ("created", "stale_count", "event_count", "binding_state", "local_revision",
                "package_id", "package_item_id", "status_phrase", "state_content_sha256"):
        if opt in result and result[opt] is not None:
            pairs.append((opt, result[opt]))
    for r in result.get("reasons", []):
        pairs.append(("reason", f"{r['code']}: {r['detail']}"))
    pairs += [
        ("amazon_connections", ac["amazon_connections"]),
        ("amazon_mutations", ac["amazon_mutations"]),
        ("amazon_bulk_uploads", ac["amazon_bulk_uploads"]),
        ("external_network_calls", ac["external_network_calls"]),
        ("amazon_action_performed", "false"),
    ]
    return pairs


def format_summary(result, fmt="text"):
    if fmt == "json":
        out = {k: v for k, v in _summary_pairs(result) if k != "reason"}
        out["reasons"] = result.get("reasons", [])
        if result.get("command") == "list":
            out["rows"] = result.get("rows", [])
        return canonical_json(out)
    lines = [f"{k}={'' if v is None else v}" for k, v in _summary_pairs(result)]
    if result.get("command") == "list":
        for row in result.get("rows", []):
            lines.append(f"item\t{row['package_item_id']}\t{row['owner_status']}\t{row['binding_state']}")
    return "\n".join(lines)


def _updates_from_args(args):
    return {
        "owner_checked_date": args.owner_checked_date,
        "owner_completed_date": args.owner_completed_date,
        "owner_note": args.owner_note,
        "before_value": args.before_value,
        "after_value": args.after_value,
        "before_value_currency": args.before_value_currency,
        "after_value_currency": args.after_value_currency,
        "manually_verified_campaign": args.manually_verified_campaign,
        "manually_verified_ad_group": args.manually_verified_ad_group,
        "manually_verified_target_or_term": args.manually_verified_target_or_term,
        "evidence_reference": args.evidence_reference,
    }


def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="python -m production.phase7_manual_action_tracker",
        description="Offline Manual Action Tracker (Phase 7.6). Records owner-entered outcomes for "
                    "accepted Phase 7.5 decision-package items. Connects to nothing; performs no "
                    "Amazon action; never infers completion from a Phase 7.5 approval; is not an "
                    "upload template.")
    p.add_argument("--base-dir", default=os.path.join("runs", "T2", "phase7", "7.6"),
                   help="Phase 7.6 workspace (action_state/ exports/ validation/ runtime/ logs/).")
    p.add_argument("--phase7-5-dir", default=os.path.join("runs", "T2", "phase7", "7.5"),
                   help="Phase 7.5 workspace root (its packages/ hold the decision packages).")
    p.add_argument("--reference-date", required=True, help="Reference date (YYYY-MM-DD).")
    p.add_argument("--format", choices=("text", "json"), default="text", help="Summary format.")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List tracked records with owner status and package binding state.")

    sp = sub.add_parser("show", help="Show one record and its revision history.")
    sp.add_argument("--package-id")
    sp.add_argument("--package-item-id")
    sp.add_argument("--tracker-record-id")

    si = sub.add_parser("initialize", help="Seed PENDING_MANUAL_CHECK records from a package.")
    si.add_argument("--package-id")
    si.add_argument("--package-dir-name")

    su = sub.add_parser("update", help="Record an owner-entered outcome for one package item.")
    su.add_argument("--package-id", required=True)
    su.add_argument("--package-item-id", required=True)
    su.add_argument("--status", required=True, choices=OWNER_STATUSES)
    su.add_argument("--owner-checked-date")
    su.add_argument("--owner-completed-date")
    su.add_argument("--owner-note")
    su.add_argument("--before-value")
    su.add_argument("--after-value")
    su.add_argument("--before-value-currency")
    su.add_argument("--after-value-currency")
    su.add_argument("--manually-verified-campaign")
    su.add_argument("--manually-verified-ad-group")
    su.add_argument("--manually-verified-target-or-term")
    su.add_argument("--evidence-reference")
    su.add_argument("--evidence-file", help="Local file whose SHA-256 is recorded as evidence_sha256.")
    su.add_argument("--confirmed-manual-seller-central-check", action="store_true",
                    help="Owner confirms they manually checked current Seller Central state.")
    su.add_argument("--confirmed-owner-performed-action", action="store_true",
                    help="Owner confirms they performed the action manually in Seller Central.")
    su.add_argument("--expected-state-sha256", help="Compare-and-swap guard on the current state.")
    su.add_argument("--expected-record-hash", help="Compare-and-swap guard on the target record.")

    sh = sub.add_parser("history", help="Print the append-only history (optionally for one item).")
    sh.add_argument("--package-id")
    sh.add_argument("--package-item-id")

    sub.add_parser("validate", help="Validate state, history chain, and package bindings.")
    sub.add_parser("export", help="Write local human-readable exports (TSV / JSON / Markdown).")
    return p


def run_command(args, *, now=None):
    base, p75, ref = args.base_dir, args.phase7_5_dir, args.reference_date
    if not _valid_date(ref):
        raise TrackerError("INVALID_REFERENCE_DATE", str(ref))
    cmd = args.command
    if cmd == "initialize":
        return run_initialize(base, p75, ref, package_id=args.package_id,
                              dir_name=args.package_dir_name, now=now)
    if cmd == "update":
        return run_update(base, p75, ref, package_id=args.package_id,
                          package_item_id=args.package_item_id, status=args.status,
                          updates=_updates_from_args(args),
                          confirm_check=args.confirmed_manual_seller_central_check,
                          confirm_action=args.confirmed_owner_performed_action,
                          evidence_file=args.evidence_file,
                          expected_state_sha256=args.expected_state_sha256,
                          expected_record_hash=args.expected_record_hash, now=now)
    if cmd == "list":
        return run_list(base, p75, ref)
    if cmd == "show":
        return run_show(base, p75, ref, package_id=args.package_id,
                        package_item_id=args.package_item_id,
                        tracker_record_id_=args.tracker_record_id)
    if cmd == "history":
        return run_history(base, p75, ref, package_id=args.package_id,
                           package_item_id=args.package_item_id)
    if cmd == "validate":
        return run_validate(base, p75, ref)
    if cmd == "export":
        return run_export(base, p75, ref)
    raise TrackerError("UNKNOWN_COMMAND", str(cmd))


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        result = run_command(args)
    except TrackerError as e:
        sys.stdout.write(f"tracker_readiness={_err_readiness(getattr(args, 'command', ''), e.code)}\n"
                         f"error_code={e.code}\nerror_detail={e.detail}\n")
        return 2
    sys.stdout.write(format_summary(result, fmt=args.format) + "\n")
    return exit_code_for(result["readiness"])


if __name__ == "__main__":
    raise SystemExit(main())
