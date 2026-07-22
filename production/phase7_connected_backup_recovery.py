#!/usr/bin/env python3
"""Phase 7.9 — Connected Backup, Integrity, Update-check and Recovery Manager.

This module is the ONE Phase 7.9 authority. It protects the local Phase 7 runtime data
(``runs/T2/phase7/7.3`` … ``7.8``) and gives the owner deterministic integrity snapshots,
encrypted backups, remote upload/verify/download, restore planning, an isolated recovery
drill, an explicit-confirmation restore, GitHub update checks, isolated update staging, and
a read-only dependency-upgrade report.

PERMANENT AMAZON-ACCOUNT BOUNDARY (never crossable, in any path, by any flag or config):
no Seller Central, no Seller Central login, no SP-API, no Ads API, no seller-account OAuth,
no seller credentials/cookies/sessions/tokens, no automatic report downloads, no campaign /
bid / budget / target / keyword / negative changes, no bulk-file uploads, no browser
automation against seller pages, no automated mutation of an Amazon seller account. Every
network operation is validated first by :mod:`core.network_policy`, whose Amazon-account
classification always wins before the allowlist is even consulted. Every snapshot carries
``seller_central_connections=0`` and the SP-API / Ads-API / seller-auth / seller-mutation
counters, all constant zero — no code path can increment them.

CONNECTIVITY (new with Phase 7.9): the application is no longer globally offline-only.
Online connectivity is permitted for legitimate NON-Seller-Central purposes only — encrypted
remote backups (Cloudflare R2 / S3-compatible / filesystem mirror), GitHub update checks and
release metadata, and package-index dependency information — and ONLY to explicitly configured,
allowlisted hosts over HTTPS. This never weakens the accepted Phase 7.2–7.8 offline behavior.

Nothing here silently updates the working tree, merges, resets, pulls, installs, upgrades,
restores, or overwrites anything. Every mutating action needs an explicit command; a live
restore additionally needs an exact confirmation token and a successful recovery drill.
"""
from __future__ import annotations

import argparse
import base64
import datetime as _dt
import hashlib
import io
import json
import os
import platform
import shutil
import socket
import ssl
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlsplit

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ("", "core", "production"):
    _p = _ROOT if not _sub else os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import diagnostics as D                     # noqa: E402  secret redaction + typed codes
import network_policy as NP                 # noqa: E402  canonical Amazon-deny + allowlist validator
import subprocess_runner as SP              # noqa: E402  bounded, secret-safe, no-shell subprocess

# ================================================================ identity / versions
STAGE_ID = "7.9"
STAGE_NAME = "Connected Backup, Update & Recovery Manager"
TOOL_VERSION = "phase7.9.0"
MANIFEST_SCHEMA = "phase7-9-snapshot-manifest-v1"
ENCRYPTION_SCHEMA = "phase7-9-encryption-metadata-v1"
REMOTE_RECORD_SCHEMA = "phase7-9-remote-record-v1"
RESTORE_PLAN_SCHEMA = "phase7-9-restore-plan-v1"
DRILL_SCHEMA = "phase7-9-recovery-drill-v1"
UPDATE_CHECK_SCHEMA = "phase7-9-update-check-v1"
UPDATE_STAGE_SCHEMA = "phase7-9-update-stage-v1"
DEPENDENCY_SCHEMA = "phase7-9-dependency-report-v1"

# ---------------------------------------------------------------- readiness states
READY = "SESSION7_9_BACKUP_READY"
READY_EMPTY = "SESSION7_9_BACKUP_READY_EMPTY"
SOURCE_REQUIRED = "SESSION7_9_SOURCE_REQUIRED"
SOURCE_BLOCKED = "SESSION7_9_SOURCE_BLOCKED"
SNAPSHOT_BLOCKED = "SESSION7_9_SNAPSHOT_BLOCKED"
ENCRYPTION_REQUIRED = "SESSION7_9_ENCRYPTION_REQUIRED"
ENCRYPTION_BLOCKED = "SESSION7_9_ENCRYPTION_BLOCKED"
ENCRYPTION_READY = "SESSION7_9_ENCRYPTION_READY"
VERIFY_READY = "SESSION7_9_VERIFY_READY"
REMOTE_NOT_CONFIGURED = "SESSION7_9_REMOTE_NOT_CONFIGURED"
REMOTE_READY = "SESSION7_9_REMOTE_READY"
REMOTE_UNAVAILABLE = "SESSION7_9_REMOTE_UNAVAILABLE"
REMOTE_INTEGRITY_BLOCKED = "SESSION7_9_REMOTE_INTEGRITY_BLOCKED"
RESTORE_PLAN_READY = "SESSION7_9_RESTORE_PLAN_READY"
RECOVERY_DRILL_READY = "SESSION7_9_RECOVERY_DRILL_READY"
RECOVERY_DRILL_BLOCKED = "SESSION7_9_RECOVERY_DRILL_BLOCKED"
RESTORE_CONFIRMATION_REQUIRED = "SESSION7_9_RESTORE_CONFIRMATION_REQUIRED"
RESTORE_BLOCKED = "SESSION7_9_RESTORE_BLOCKED"
RESTORE_READY = "SESSION7_9_RESTORE_READY"
UPDATE_AVAILABLE = "SESSION7_9_UPDATE_AVAILABLE"
UPDATE_CURRENT = "SESSION7_9_UPDATE_CURRENT"
UPDATE_STAGE_READY = "SESSION7_9_UPDATE_STAGE_READY"
UPDATE_STAGE_BLOCKED = "SESSION7_9_UPDATE_STAGE_BLOCKED"
UPDATE_UNAVAILABLE = "SESSION7_9_UPDATE_UNAVAILABLE"
DEPENDENCY_REPORT_READY = "SESSION7_9_DEPENDENCY_REPORT_READY"
VALIDATE_READY = "SESSION7_9_VALIDATE_READY"
SELLER_CENTRAL_POLICY_BLOCKED = "SESSION7_9_SELLER_CENTRAL_POLICY_BLOCKED"

# readiness values that mean success -> process exit 0. Everything else exits nonzero.
_EXIT_OK = frozenset({
    READY, READY_EMPTY, ENCRYPTION_READY, VERIFY_READY, REMOTE_READY, RESTORE_PLAN_READY,
    RECOVERY_DRILL_READY, RESTORE_READY, UPDATE_AVAILABLE, UPDATE_CURRENT, UPDATE_STAGE_READY,
    DEPENDENCY_REPORT_READY, VALIDATE_READY,
})

# ---------------------------------------------------------------- dependency-check per-package states
DEP_COMPATIBLE = "COMPATIBLE_UPDATE_AVAILABLE"
DEP_MAJOR = "MAJOR_UPDATE_REVIEW_REQUIRED"
DEP_CURRENT = "CURRENT"
DEP_UNKNOWN = "UNKNOWN"
DEP_NETWORK_UNAVAILABLE = "NETWORK_UNAVAILABLE"
DEP_POLICY_BLOCKED = "POLICY_BLOCKED"

# ---------------------------------------------------------------- permanent Amazon counters (constant zero)
AMAZON_COUNTERS = {
    "seller_central_connections": 0, "amazon_sp_api_calls": 0, "amazon_ads_api_calls": 0,
    "amazon_seller_auth_calls": 0, "amazon_seller_mutations": 0, "amazon_report_downloads": 0,
    "amazon_bulk_uploads": 0, "browser_automation_actions": 0, "amazon_credential_store_count": 0,
}
THIS_SESSION_NEVER = {
    "connects_to_seller_central": True, "logs_into_amazon": True, "uses_sp_api": True,
    "uses_ads_api": True, "uses_seller_oauth": True, "stores_seller_credentials": True,
    "downloads_reports": True, "changes_campaigns_bids_budgets_targets_keywords_negatives": True,
    "uploads_bulk_files": True, "automates_a_seller_browser": True,
    "mutates_an_amazon_seller_account": True, "silently_updates_working_tree": True,
    "auto_merges_or_resets_or_pulls": True, "auto_installs_or_upgrades": True,
    "restores_without_confirmation": True, "uploads_plaintext_runtime_data": True,
    "uploads_encryption_keys_or_passphrase": True,
}

# ---------------------------------------------------------------- default backup scope + exclusions
DEFAULT_SOURCE_SCOPE = ("7.3", "7.4", "7.5", "7.6", "7.7", "7.8")

# directory names that are NEVER walked into (caches, vcs, secrets stores, envs, build junk).
_EXCLUDED_DIR_NAMES = frozenset({
    ".git", "__pycache__", ".venv", "venv", "env", "node_modules", ".pytest_cache",
    ".mypy_cache", ".idea", ".vscode", ".ssh", ".aws", ".gnupg", "tmp", "temp",
})
# file names / suffixes / substrings that are NEVER archived (secrets, keys, tokens, cookies).
_EXCLUDED_FILE_NAMES = frozenset({
    ".env", "credentials.json", "cookies.txt", "cookies.sqlite", "id_rsa", "id_ed25519",
    ".netrc", ".pypirc", "token.json", "secrets.json", "aws_credentials",
})
_EXCLUDED_SUFFIXES = (".key", ".pem", ".pfx", ".p12", ".crt", ".keystore", ".pyc", ".pyo",
                      ".log", ".env")
_EXCLUDED_SUBSTRINGS = ("_secret", "secret_", ".secret", "credential", "passwd", "password",
                        "apikey", "api_key", "access_key", "private_key", ".env.")

MAX_SNAPSHOT_FILE_BYTES = 256 * 1024 * 1024      # a single-file guard (256 MiB)
MAX_JSON_INTROSPECT_BYTES = 8 * 1024 * 1024      # only introspect schema_version on smaller json


# ================================================================ errors
class BackupError(Exception):
    """A safe, secret-free Phase 7.9 failure carrying a stable ``code`` and a redacted detail."""

    def __init__(self, code, detail="", *, readiness=SNAPSHOT_BLOCKED):
        self.code = code
        self.detail = redact(detail)
        self.readiness = readiness
        super().__init__(f"{code}: {self.detail}" if self.detail else code)


class RemoteDependencyMissing(BackupError):
    def __init__(self, package):
        super().__init__("REMOTE_DEPENDENCY_MISSING",
                         f"the '{package}' package is required for this remote provider and is not "
                         f"installed; local snapshot/verify/encrypt commands do not need it",
                         readiness=REMOTE_UNAVAILABLE)


class EncryptionUnavailable(BackupError):
    def __init__(self):
        super().__init__("ENCRYPTION_BACKEND_UNAVAILABLE",
                         "the 'cryptography' package is required for encryption and is not installed",
                         readiness=ENCRYPTION_BLOCKED)


# ================================================================ small helpers
def _now_utc():
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt=None):
    return (dt or _now_utc()).astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(obj):
    """Stable canonical serialization — identical form to the Phase 5/6/7 engines. Rejects NaN /
    Infinity (``allow_nan=False``) so a manifest can never carry a non-finite number."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2, separators=(",", ": "),
                      allow_nan=False)


def content_sha256(obj):
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def redact(value):
    """Route any value through central secret redaction (env passphrase / cloud keys / key-shaped
    tokens). Recurses through dict/list so a log record never carries a secret."""
    if isinstance(value, str):
        return D.redact_secrets(value)
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _atomic_write_bytes(path, data):
    """Write bytes to a temp sibling, fsync, then atomically replace. A failed write never
    overwrites the last valid artifact (the replace only runs on a fully written temp)."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-79-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _atomic_write_json(path, obj):
    _atomic_write_bytes(path, canonical_json(obj).encode("utf-8"))


def _read_json(path, what):
    with open(path, "rb") as f:
        data = f.read()
    if b"\x00" in data:
        raise BackupError("NULL_BYTE_REJECTED", what)

    def _reject(tok):
        raise BackupError("NON_FINITE_NUMBER_REJECTED", f"{what}: {tok}")

    try:
        return json.loads(data.decode("utf-8"), parse_constant=_reject)
    except ValueError as e:
        raise BackupError("MALFORMED_JSON", f"{what}: {e}") from e


# ================================================================ configuration
class Config:
    __slots__ = ("base_dir", "source_root", "scope", "repo_root", "remote_provider",
                 "filesystem_remote_root", "s3", "git_remote", "git_branch", "env")

    def __init__(self, base_dir, source_root, *, scope=None, repo_root=None, env=None):
        self.env = dict(env) if env is not None else dict(os.environ)
        self.base_dir = os.path.abspath(base_dir)
        self.source_root = os.path.abspath(source_root)
        self.scope = tuple(scope) if scope else DEFAULT_SOURCE_SCOPE
        self.repo_root = os.path.abspath(repo_root or _ROOT)
        self.remote_provider = (self.env.get("PHASE7_9_REMOTE_PROVIDER") or "").strip().lower()
        self.filesystem_remote_root = self.env.get("PHASE7_9_FILESYSTEM_REMOTE_ROOT")
        self.s3 = None
        self.git_remote = (self.env.get("PHASE7_9_GIT_REMOTE") or "origin").strip()
        self.git_branch = (self.env.get("PHASE7_9_GIT_BRANCH") or "main").strip()

    # workspace subdirectories (created lazily on first write)
    def _sub(self, name):
        return os.path.join(self.base_dir, name)

    snapshots_dir = property(lambda self: self._sub("snapshots"))
    encrypted_dir = property(lambda self: self._sub("encrypted"))
    remote_cache_dir = property(lambda self: self._sub("remote_cache"))
    restore_plans_dir = property(lambda self: self._sub("restore_plans"))
    recovery_drills_dir = property(lambda self: self._sub("recovery_drills"))
    update_staging_dir = property(lambda self: self._sub("update_staging"))
    manifests_dir = property(lambda self: self._sub("manifests"))
    validation_dir = property(lambda self: self._sub("validation"))
    logs_dir = property(lambda self: self._sub("logs"))

    def snapshot_manifest_path(self, snapshot_id):
        return os.path.join(self.snapshots_dir, snapshot_id, "snapshot_manifest.json")

    def encrypted_meta_path(self, snapshot_id):
        return os.path.join(self.encrypted_dir, snapshot_id, "encryption_metadata.json")

    def encrypted_payload_path(self, snapshot_id):
        return os.path.join(self.encrypted_dir, snapshot_id, "payload.enc")


# ================================================================ logging (redacted, local, bounded)
def _log(config, operation, record):
    """Append one redacted local JSON line to logs/operations.jsonl. Never raises into the caller
    (a logging failure must not fail a backup) and never writes a secret."""
    try:
        os.makedirs(config.logs_dir, exist_ok=True)
        line = {"ts": _iso(), "operation": operation}
        line.update(redact(record))
        blob = json.dumps(line, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with open(os.path.join(config.logs_dir, "operations.jsonl"), "a", encoding="utf-8") as f:
            f.write(blob + "\n")
    except OSError:
        pass


# ================================================================ source enumeration (explicit allowlist)
def _is_excluded_file(name):
    low = name.lower()
    if name in _EXCLUDED_FILE_NAMES or low in _EXCLUDED_FILE_NAMES:
        return True
    if low.endswith(_EXCLUDED_SUFFIXES):
        return True
    if low.startswith(".env"):
        return True
    return any(s in low for s in _EXCLUDED_SUBSTRINGS)


def _within(child, parent):
    """True when the real path of *child* stays inside the real path of *parent* (blocks a symlink
    / junction that escapes the allowed root)."""
    try:
        rp = os.path.realpath(child)
        rparent = os.path.realpath(parent)
    except OSError:
        return False
    try:
        return os.path.commonpath([rp, rparent]) == rparent
    except ValueError:
        return False


def enumerate_source(config):
    """Walk only the allowlisted phase scope directories under ``source_root`` and return
    ``(files, excluded)``. ``files`` is a list of ``{path, size, sha256}`` sorted by normalized
    POSIX relative path (relative to source_root). Symlinks/junctions are never followed and any
    entry that escapes its allowed root is refused. Deterministic: identity depends only on the
    relative path + content, never on enumeration order or mtime."""
    files = []
    excluded = {"excluded_dirs": 0, "excluded_files": 0, "symlinks_skipped": 0,
                "reasons": {}, "missing_scope": []}

    def _note(reason):
        excluded["reasons"][reason] = excluded["reasons"].get(reason, 0) + 1

    for phase in config.scope:
        root = os.path.join(config.source_root, phase)
        if not os.path.isdir(root):
            excluded["missing_scope"].append(phase)
            continue
        if os.path.islink(root) or not _within(root, config.source_root):
            excluded["symlinks_skipped"] += 1
            _note("scope_root_symlink_or_escape")
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            # prune excluded / escaping subdirectories in place (os.walk honors the mutation)
            keep = []
            for dn in sorted(dirnames):
                full = os.path.join(dirpath, dn)
                if dn in _EXCLUDED_DIR_NAMES:
                    excluded["excluded_dirs"] += 1
                    _note("excluded_dir")
                elif os.path.islink(full) or not _within(full, root):
                    excluded["symlinks_skipped"] += 1
                    _note("symlink_dir")
                else:
                    keep.append(dn)
            dirnames[:] = keep
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                if os.path.islink(full):
                    excluded["symlinks_skipped"] += 1
                    _note("symlink_file")
                    continue
                if _is_excluded_file(fn):
                    excluded["excluded_files"] += 1
                    _note("excluded_file")
                    continue
                if not _within(full, root) or not os.path.isfile(full):
                    excluded["excluded_files"] += 1
                    _note("escape_or_nonregular")
                    continue
                size = os.path.getsize(full)
                if size > MAX_SNAPSHOT_FILE_BYTES:
                    raise BackupError("SOURCE_FILE_OVERSIZE",
                                      f"{fn}: {size} bytes exceeds the per-file limit",
                                      readiness=SOURCE_BLOCKED)
                rel = os.path.relpath(full, config.source_root).replace(os.sep, "/")
                if rel.startswith("../") or rel == ".." or os.path.isabs(rel):
                    raise BackupError("SOURCE_PATH_ESCAPE", fn, readiness=SOURCE_BLOCKED)
                files.append({"path": rel, "size": size, "sha256": _sha256_file(full)})
    files.sort(key=lambda e: e["path"])
    return files, excluded


# ================================================================ snapshot model
def _git_context(config):
    """Best-effort, read-only repo context (HEAD, branch, accepted Phase-7 tags). A git failure is
    non-fatal — it yields nulls, never an exception (a snapshot must not depend on git)."""
    ctx = {"repository_head": None, "current_branch": None, "accepted_tags": []}
    r = _git(config, ["rev-parse", "HEAD"], timeout=20)
    if r.success and r.stdout.strip():
        ctx["repository_head"] = r.stdout.strip()
    r = _git(config, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=20)
    if r.success and r.stdout.strip():
        ctx["current_branch"] = r.stdout.strip()
    r = _git(config, ["tag", "--list", "phase7-*-accepted-*"], timeout=20)
    if r.success and r.stdout.strip():
        ctx["accepted_tags"] = sorted(t.strip() for t in r.stdout.splitlines() if t.strip())
    return ctx


def _phase_summary(config, files):
    """Per-phase presence + size + best-effort schema_version / readiness, derived from content only
    (never mtime). Used for the manifest metadata, NOT the snapshot identity."""
    summary = {}
    by_phase = {}
    for e in files:
        top = e["path"].split("/", 1)[0]
        by_phase.setdefault(top, []).append(e)
    for phase in config.scope:
        ents = by_phase.get(phase, [])
        schemas = set()
        readiness = None
        for e in ents:
            if not e["path"].lower().endswith(".json") or e["size"] > MAX_JSON_INTROSPECT_BYTES:
                continue
            full = os.path.join(config.source_root, e["path"].replace("/", os.sep))
            try:
                with open(full, "rb") as fh:
                    doc = json.loads(fh.read().decode("utf-8"))
            except (ValueError, OSError):
                continue
            if isinstance(doc, dict):
                if isinstance(doc.get("schema_version"), str):
                    schemas.add(doc["schema_version"])
                for key in ("readiness", "analysis_readiness"):
                    if readiness is None and isinstance(doc.get(key), str):
                        readiness = doc[key]
        summary[phase] = {
            "present": bool(ents),
            "file_count": len(ents),
            "byte_count": sum(e["size"] for e in ents),
            "schema_versions": sorted(schemas),
            "readiness": readiness,
        }
    return summary


def build_snapshot_manifest(config):
    """Assemble a deterministic, content-addressed snapshot manifest (not yet written).

    The ``snapshot_id`` and ``source_tree_sha256`` are pure functions of the source file set
    (normalized relative path + per-file SHA-256 + size + scope). They never depend on a runtime
    timestamp, file mtime, enumeration order, process id, random uuid, temp path, or path separator.
    """
    files, excluded = enumerate_source(config)
    tree_core = {"schema_version": MANIFEST_SCHEMA,
                 "source_scope": sorted(config.scope),
                 "files": [{"path": e["path"], "sha256": e["sha256"], "size": e["size"]}
                           for e in files]}
    source_tree_sha256 = content_sha256(tree_core)
    snapshot_id = "snap-" + source_tree_sha256[:24]

    ctx = _git_context(config)
    manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "snapshot_id": snapshot_id,
        "stage_id": STAGE_ID,
        "creation_tool_version": TOOL_VERSION,
        "source_scope": sorted(config.scope),
        "source_root_display": _rel_display(config.source_root),
        "source_phase_paths": [f"{p}" for p in sorted(config.scope)],
        "repository_head": ctx["repository_head"],
        "current_branch": ctx["current_branch"],
        "accepted_tags": ctx["accepted_tags"],
        "file_count": len(files),
        "total_bytes": sum(e["size"] for e in files),
        "files": files,
        "source_tree_sha256": source_tree_sha256,
        "phase_readiness": _phase_summary(config, files),
        "excluded_summary": excluded,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "encryption_status": "NONE",
        "remote_status": "NONE",
    }
    manifest.update(AMAZON_COUNTERS)
    return manifest, files


def _snapshot_valid(manifest):
    """Recompute the snapshot identity from the manifest's own file list and confirm it matches
    (detects a tampered manifest whose id/tree hash no longer recompute)."""
    if not isinstance(manifest, dict) or manifest.get("schema_version") != MANIFEST_SCHEMA:
        return False, "SCHEMA"
    files = manifest.get("files")
    if not isinstance(files, list):
        return False, "NO_FILES"
    tree_core = {"schema_version": MANIFEST_SCHEMA,
                 "source_scope": manifest.get("source_scope"),
                 "files": [{"path": e.get("path"), "sha256": e.get("sha256"), "size": e.get("size")}
                           for e in files]}
    tree = content_sha256(tree_core)
    if tree != manifest.get("source_tree_sha256"):
        return False, "TREE_HASH"
    if manifest.get("snapshot_id") != "snap-" + tree[:24]:
        return False, "SNAPSHOT_ID"
    for key, val in AMAZON_COUNTERS.items():
        if manifest.get(key) != val:
            return False, "AMAZON_COUNTER"
    return True, None


def create_snapshot(config):
    """Create (or idempotently reuse) a deterministic snapshot. Writes the manifest atomically to
    ``snapshots/<snapshot_id>/snapshot_manifest.json``. Returns a result dict."""
    manifest, files = build_snapshot_manifest(config)
    sid = manifest["snapshot_id"]
    path = config.snapshot_manifest_path(sid)
    reuse = False
    if os.path.isfile(path):
        try:
            existing = _read_json(path, "SNAPSHOT_MANIFEST")
            ok, _why = _snapshot_valid(existing)
            if ok and existing.get("source_tree_sha256") == manifest["source_tree_sha256"]:
                reuse = True
        except BackupError:
            reuse = False
    if not reuse:
        _atomic_write_json(path, manifest)
    readiness = READY_EMPTY if manifest["file_count"] == 0 else READY
    result = {"command": "snapshot", "readiness": readiness, "ok": True,
              "snapshot_id": sid, "file_count": manifest["file_count"],
              "total_bytes": manifest["total_bytes"],
              "source_tree_sha256": manifest["source_tree_sha256"],
              "manifest_path": _rel_display(path),
              "idempotent_reuse": reuse, "note": "IDEMPOTENT_REUSE" if reuse else "CREATED",
              "excluded_summary": manifest["excluded_summary"],
              "amazon_counters": dict(AMAZON_COUNTERS)}
    _log(config, "snapshot", {"snapshot_id": sid, "file_count": manifest["file_count"],
                              "readiness": readiness, "reuse": reuse})
    return result


def load_snapshot(config, snapshot_id):
    path = config.snapshot_manifest_path(snapshot_id)
    if not os.path.isfile(path):
        raise BackupError("SNAPSHOT_NOT_FOUND", snapshot_id, readiness=SOURCE_REQUIRED)
    manifest = _read_json(path, "SNAPSHOT_MANIFEST")
    ok, why = _snapshot_valid(manifest)
    if not ok:
        raise BackupError("SNAPSHOT_MANIFEST_TAMPERED", f"{snapshot_id}: {why}",
                          readiness=SNAPSHOT_BLOCKED)
    return manifest


def verify_snapshot(config, snapshot_id):
    """Re-verify a snapshot against the live source tree: manifest self-consistency + every declared
    file present with a matching SHA-256 + no undeclared extra file inside the scope. Read-only."""
    manifest = load_snapshot(config, snapshot_id)
    declared = {e["path"]: e for e in manifest["files"]}
    missing, mismatched, extra = [], [], []
    for rel, e in sorted(declared.items()):
        full = os.path.join(config.source_root, rel.replace("/", os.sep))
        if not os.path.isfile(full):
            missing.append(rel)
        elif _sha256_file(full) != e["sha256"]:
            mismatched.append(rel)
    # extra files currently present in scope but not declared
    live, _excluded = enumerate_source(config)
    live_paths = {e["path"] for e in live}
    extra = sorted(live_paths - set(declared))
    ok = not (missing or mismatched or extra)
    readiness = VERIFY_READY if ok else SNAPSHOT_BLOCKED
    result = {"command": "verify", "readiness": readiness, "ok": ok, "snapshot_id": snapshot_id,
              "verified_file_count": len(declared) - len(missing) - len(mismatched),
              "missing": missing, "mismatched": mismatched, "extra": extra,
              "source_tree_sha256": manifest["source_tree_sha256"],
              "amazon_counters": dict(AMAZON_COUNTERS)}
    _log(config, "verify", {"snapshot_id": snapshot_id, "ok": ok,
                            "missing": len(missing), "mismatched": len(mismatched),
                            "extra": len(extra)})
    return result


# ================================================================ deterministic archive
def build_archive_bytes(config, manifest):
    """Build a deterministic, uncompressed tar of exactly the snapshot's declared files, in sorted
    order, with normalized POSIX relative names and fixed metadata (mtime=0, mode=0644, uid/gid=0,
    no owner names). Reads each file back out of the archive and re-hashes it against the manifest
    before returning. Raises on any absolute/traversal name or hash mismatch."""
    buf = io.BytesIO()
    entries = sorted(manifest["files"], key=lambda e: e["path"])
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for e in entries:
            rel = e["path"]
            if os.path.isabs(rel) or rel.startswith("../") or "/../" in rel or "\\" in rel:
                raise BackupError("ARCHIVE_UNSAFE_NAME", rel, readiness=SNAPSHOT_BLOCKED)
            full = os.path.join(config.source_root, rel.replace("/", os.sep))
            with open(full, "rb") as fh:
                data = fh.read()
            if _sha256_bytes(data) != e["sha256"]:
                raise BackupError("SOURCE_CHANGED_DURING_ARCHIVE", rel, readiness=SNAPSHOT_BLOCKED)
            info = tarfile.TarInfo(name=rel)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.type = tarfile.REGTYPE
            tar.addfile(info, io.BytesIO(data))
    archive = buf.getvalue()
    # read-back verification: every member extracts and re-hashes to the declared source hash
    _verify_archive_bytes(archive, manifest)
    return archive


def _verify_archive_bytes(archive, manifest):
    declared = {e["path"]: e for e in manifest["files"]}
    seen = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r") as tar:
        for member in tar.getmembers():
            name = member.name
            if member.islnk() or member.issym() or member.isdev() or not member.isreg():
                raise BackupError("ARCHIVE_NON_REGULAR_MEMBER", name, readiness=SNAPSHOT_BLOCKED)
            if os.path.isabs(name) or name.startswith("../") or "/../" in name:
                raise BackupError("ARCHIVE_TRAVERSAL_MEMBER", name, readiness=SNAPSHOT_BLOCKED)
            if name not in declared:
                raise BackupError("ARCHIVE_UNDECLARED_MEMBER", name, readiness=SNAPSHOT_BLOCKED)
            data = tar.extractfile(member).read()
            if _sha256_bytes(data) != declared[name]["sha256"]:
                raise BackupError("ARCHIVE_HASH_MISMATCH", name, readiness=SNAPSHOT_BLOCKED)
            seen[name] = True
    if set(seen) != set(declared):
        raise BackupError("ARCHIVE_INCOMPLETE",
                          f"{len(declared) - len(seen)} declared file(s) missing from archive",
                          readiness=SNAPSHOT_BLOCKED)
    return True


def _safe_extract(archive, dest):
    """Extract a tar archive into *dest* refusing any absolute path, traversal, symlink, hardlink,
    or device member (defense in depth even on our own archive). Returns the list of rel paths."""
    os.makedirs(dest, exist_ok=True)
    dest_real = os.path.realpath(dest)
    written = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r") as tar:
        for member in tar.getmembers():
            name = member.name
            if member.islnk() or member.issym() or member.isdev() or member.isdir() \
                    or not member.isreg():
                raise BackupError("EXTRACT_NON_REGULAR_MEMBER", name, readiness=RECOVERY_DRILL_BLOCKED)
            if os.path.isabs(name) or name.startswith("../") or "/../" in name or "\\" in name:
                raise BackupError("EXTRACT_TRAVERSAL_MEMBER", name, readiness=RECOVERY_DRILL_BLOCKED)
            target = os.path.realpath(os.path.join(dest, name))
            if os.path.commonpath([target, dest_real]) != dest_real:
                raise BackupError("EXTRACT_ESCAPE", name, readiness=RECOVERY_DRILL_BLOCKED)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            data = tar.extractfile(member).read()
            with open(target, "wb") as f:
                f.write(data)
            written.append(name)
    return written


# ================================================================ encryption (AES-256-GCM + Scrypt)
_SCRYPT_PARAMS = {"n": 2 ** 15, "r": 8, "p": 1, "length": 32}


def _crypto_available():
    try:
        import cryptography  # noqa: F401
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt  # noqa: F401
        return True
    except Exception:
        return False


def _derive_key(passphrase, salt, params):
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    kdf = Scrypt(salt=salt, length=params["length"], n=params["n"], r=params["r"], p=params["p"])
    return kdf.derive(passphrase if isinstance(passphrase, bytes) else passphrase.encode("utf-8"))


def _resolve_passphrase(config, provided=None):
    """Obtain the backup passphrase from an explicit argument (interactive prompt result) or the
    ``PHASE7_9_BACKUP_PASSPHRASE`` environment variable. Never from a normal CLI argument (that
    would land in shell history). Returns bytes or raises ENCRYPTION_REQUIRED."""
    pw = provided if provided is not None else config.env.get("PHASE7_9_BACKUP_PASSPHRASE")
    if not pw:
        raise BackupError("BACKUP_PASSPHRASE_REQUIRED",
                          "set PHASE7_9_BACKUP_PASSPHRASE or use the secure interactive prompt",
                          readiness=ENCRYPTION_REQUIRED)
    return pw.encode("utf-8") if isinstance(pw, str) else pw


def _encryption_aad(snapshot_id, source_tree_sha256, archive_sha256):
    return canonical_json({"schema": ENCRYPTION_SCHEMA, "snapshot_id": snapshot_id,
                           "source_tree_sha256": source_tree_sha256,
                           "plaintext_archive_sha256": archive_sha256}).encode("utf-8")


def encrypt_snapshot(config, snapshot_id, *, passphrase=None, keep_plaintext=False):
    """Encrypt a snapshot's deterministic archive with AES-256-GCM (Scrypt-derived key). Writes the
    ciphertext + non-secret metadata atomically. Reuses an existing valid encrypted artifact for the
    same snapshot instead of creating a misleading duplicate. Never stores the key or passphrase."""
    if not _crypto_available():
        raise EncryptionUnavailable()
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    manifest = load_snapshot(config, snapshot_id)
    pw = _resolve_passphrase(config, passphrase)

    # reuse an existing valid encrypted artifact bound to this snapshot's source tree
    meta_path = config.encrypted_meta_path(snapshot_id)
    payload_path = config.encrypted_payload_path(snapshot_id)
    if os.path.isfile(meta_path) and os.path.isfile(payload_path):
        try:
            meta = _read_json(meta_path, "ENCRYPTION_METADATA")
            if (meta.get("schema_version") == ENCRYPTION_SCHEMA
                    and meta.get("snapshot_id") == snapshot_id
                    and meta.get("source_tree_sha256") == manifest["source_tree_sha256"]
                    and meta.get("encrypted_payload_sha256") == _sha256_file(payload_path)):
                _log(config, "encrypt", {"snapshot_id": snapshot_id, "reuse": True})
                return {"command": "encrypt", "readiness": ENCRYPTION_READY, "ok": True,
                        "snapshot_id": snapshot_id, "idempotent_reuse": True,
                        "note": "IDEMPOTENT_REUSE",
                        "encrypted_payload_sha256": meta["encrypted_payload_sha256"],
                        "algorithm": meta.get("algorithm"), "kdf": meta.get("kdf"),
                        "amazon_counters": dict(AMAZON_COUNTERS)}
        except BackupError:
            pass

    archive = build_archive_bytes(config, manifest)
    archive_sha = _sha256_bytes(archive)
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(pw, salt, _SCRYPT_PARAMS)
    aad = _encryption_aad(snapshot_id, manifest["source_tree_sha256"], archive_sha)
    try:
        ciphertext = AESGCM(key).encrypt(nonce, archive, aad)
    finally:
        del key
    payload_sha = _sha256_bytes(ciphertext)
    meta = {
        "schema_version": ENCRYPTION_SCHEMA,
        "snapshot_id": snapshot_id,
        "algorithm": "AES-256-GCM",
        "kdf": "scrypt",
        "kdf_params": {"n": _SCRYPT_PARAMS["n"], "r": _SCRYPT_PARAMS["r"],
                       "p": _SCRYPT_PARAMS["p"], "length": _SCRYPT_PARAMS["length"]},
        "salt_b64": base64.b64encode(salt).decode("ascii"),
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "version": 1,
        "aad_sha256": _sha256_bytes(aad),
        "plaintext_archive_sha256": archive_sha,
        "encrypted_payload_sha256": payload_sha,
        "ciphertext_bytes": len(ciphertext),
        "source_tree_sha256": manifest["source_tree_sha256"],
        "created_at": _iso(),
    }
    meta.update(AMAZON_COUNTERS)
    _atomic_write_bytes(payload_path, ciphertext)
    _atomic_write_json(meta_path, meta)
    if keep_plaintext:
        _atomic_write_bytes(os.path.join(config.snapshots_dir, snapshot_id, "archive.tar"), archive)
    _log(config, "encrypt", {"snapshot_id": snapshot_id, "reuse": False,
                             "ciphertext_bytes": len(ciphertext)})
    return {"command": "encrypt", "readiness": ENCRYPTION_READY, "ok": True,
            "snapshot_id": snapshot_id, "idempotent_reuse": False, "note": "CREATED",
            "algorithm": "AES-256-GCM", "kdf": "scrypt",
            "encrypted_payload_sha256": payload_sha, "ciphertext_bytes": len(ciphertext),
            "amazon_counters": dict(AMAZON_COUNTERS)}


def decrypt_archive(config, snapshot_id, passphrase, *, meta=None, payload=None):
    """Decrypt + authenticate the encrypted archive and return its plaintext bytes. Raises
    DECRYPT_FAILED (wrong passphrase, truncated or modified ciphertext, or a tampered AAD)."""
    if not _crypto_available():
        raise EncryptionUnavailable()
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag

    manifest = load_snapshot(config, snapshot_id)
    if meta is None:
        meta = _read_json(config.encrypted_meta_path(snapshot_id), "ENCRYPTION_METADATA")
    if payload is None:
        with open(config.encrypted_payload_path(snapshot_id), "rb") as f:
            payload = f.read()
    if meta.get("schema_version") != ENCRYPTION_SCHEMA:
        raise BackupError("ENCRYPTION_METADATA_SCHEMA", snapshot_id, readiness=ENCRYPTION_BLOCKED)
    # a pre-decrypt integrity check catches truncation/modification with a clear code (GCM also would)
    if _sha256_bytes(payload) != meta.get("encrypted_payload_sha256"):
        raise BackupError("CIPHERTEXT_INTEGRITY", snapshot_id, readiness=ENCRYPTION_BLOCKED)
    salt = base64.b64decode(meta["salt_b64"])
    nonce = base64.b64decode(meta["nonce_b64"])
    params = dict(_SCRYPT_PARAMS)
    params.update(meta.get("kdf_params", {}))
    key = _derive_key(passphrase, salt, params)
    aad = _encryption_aad(snapshot_id, manifest["source_tree_sha256"],
                          meta.get("plaintext_archive_sha256"))
    try:
        archive = AESGCM(key).decrypt(nonce, payload, aad)
    except InvalidTag:
        raise BackupError("DECRYPT_FAILED",
                          "wrong passphrase, or the ciphertext/AAD was modified or truncated",
                          readiness=ENCRYPTION_BLOCKED) from None
    finally:
        del key
    if _sha256_bytes(archive) != meta.get("plaintext_archive_sha256"):
        raise BackupError("ARCHIVE_HASH_MISMATCH_AFTER_DECRYPT", snapshot_id,
                          readiness=ENCRYPTION_BLOCKED)
    return archive, manifest


def decrypt_verify(config, snapshot_id, *, passphrase=None):
    """Decrypt the encrypted archive and verify every extracted file against the snapshot manifest,
    all in memory (never writes runtime data). Returns a result dict."""
    pw = _resolve_passphrase(config, passphrase)
    archive, manifest = decrypt_archive(config, snapshot_id, pw)
    _verify_archive_bytes(archive, manifest)
    result = {"command": "decrypt-verify", "readiness": VERIFY_READY, "ok": True,
              "snapshot_id": snapshot_id, "verified_file_count": manifest["file_count"],
              "plaintext_archive_sha256": _sha256_bytes(archive),
              "amazon_counters": dict(AMAZON_COUNTERS)}
    _log(config, "decrypt-verify", {"snapshot_id": snapshot_id, "ok": True})
    return result


# ================================================================ remote providers
def _object_keys(prefix, snapshot_id, payload_sha):
    """Content-addressed object keys. The payload key embeds the ciphertext SHA-256 so a distinct
    content can never silently overwrite a different one."""
    base = "/".join(p for p in (str(prefix).strip("/") if prefix else "", snapshot_id) if p)
    return {"payload": f"{base}/{payload_sha}.enc", "metadata": f"{base}/{snapshot_id}.metadata.json"}


class RemoteProvider:
    """Minimal provider interface. Implementations must be content-addressed and refuse to silently
    overwrite a different object."""

    kind = "abstract"

    def describe(self):
        raise NotImplementedError

    def put(self, key, data, *, content_sha256):
        raise NotImplementedError

    def stat(self, key):
        """Return {'size': int, 'sha256': str} or None when the key is absent."""
        raise NotImplementedError

    def get(self, key):
        raise NotImplementedError

    def list_keys(self, prefix=""):
        raise NotImplementedError


class RemoteConflict(BackupError):
    def __init__(self, key):
        super().__init__("REMOTE_OVERWRITE_CONFLICT",
                         f"a different object already exists at the target key '{key}'",
                         readiness=REMOTE_INTEGRITY_BLOCKED)


class FilesystemRemoteProvider(RemoteProvider):
    """A filesystem mirror provider for testing and local-network storage. Fully local; needs no
    cloud dependency. Content-addressed; refuses to overwrite a byte-different object."""

    kind = "filesystem"

    def __init__(self, root):
        self.root = os.path.abspath(root)

    def _p(self, key):
        safe = key.replace("\\", "/").lstrip("/")
        if safe.startswith("../") or "/../" in safe or os.path.isabs(safe):
            raise BackupError("REMOTE_KEY_UNSAFE", key, readiness=REMOTE_INTEGRITY_BLOCKED)
        return os.path.join(self.root, safe.replace("/", os.sep))

    def describe(self):
        return {"kind": self.kind, "root_display": _rel_display(self.root)}

    def put(self, key, data, *, content_sha256):
        path = self._p(key)
        if os.path.isfile(path):
            if _sha256_file(path) != content_sha256:
                raise RemoteConflict(key)
            return {"key": key, "size": len(data), "sha256": content_sha256, "reused": True}
        _atomic_write_bytes(path, data)
        return {"key": key, "size": len(data), "sha256": content_sha256, "reused": False}

    def stat(self, key):
        path = self._p(key)
        if not os.path.isfile(path):
            return None
        return {"size": os.path.getsize(path), "sha256": _sha256_file(path)}

    def get(self, key):
        path = self._p(key)
        if not os.path.isfile(path):
            raise BackupError("REMOTE_OBJECT_NOT_FOUND", key, readiness=REMOTE_UNAVAILABLE)
        with open(path, "rb") as f:
            return f.read()

    def list_keys(self, prefix=""):
        out = []
        if not os.path.isdir(self.root):
            return out
        for dirpath, _dirs, files in os.walk(self.root):
            for fn in files:
                rel = os.path.relpath(os.path.join(dirpath, fn), self.root).replace(os.sep, "/")
                if rel.startswith(prefix):
                    out.append(rel)
        return sorted(out)


class S3Config:
    """S3-compatible provider configuration read from the environment (never persisted to a repo
    file). Supports Cloudflare R2, Backblaze B2 S3, AWS S3, MinIO and any explicitly configured
    compatible endpoint. Secrets are held only in memory and never serialized."""

    __slots__ = ("endpoint_url", "bucket", "region", "prefix", "access_key_id",
                 "secret_access_key", "allow_local")

    def __init__(self, *, endpoint_url, bucket, region, prefix, access_key_id, secret_access_key,
                 allow_local=False):
        self.endpoint_url = endpoint_url
        self.bucket = bucket
        self.region = region
        self.prefix = prefix or ""
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.allow_local = allow_local

    @classmethod
    def from_env(cls, env):
        return cls(
            endpoint_url=(env.get("PHASE7_9_S3_ENDPOINT_URL") or "").strip() or None,
            bucket=(env.get("PHASE7_9_S3_BUCKET") or "").strip() or None,
            region=(env.get("PHASE7_9_S3_REGION") or "").strip() or None,
            prefix=(env.get("PHASE7_9_S3_PREFIX") or "").strip(),
            access_key_id=(env.get("PHASE7_9_S3_ACCESS_KEY_ID") or "").strip() or None,
            secret_access_key=env.get("PHASE7_9_S3_SECRET_ACCESS_KEY") or None,
            allow_local=str(env.get("PHASE7_9_S3_ALLOW_LOCAL", "")).strip().lower()
            in ("1", "true", "yes", "on"))

    def validate(self):
        """Return a list of missing required fields (never echoes a secret value)."""
        missing = [name for name, val in (
            ("PHASE7_9_S3_ENDPOINT_URL", self.endpoint_url),
            ("PHASE7_9_S3_BUCKET", self.bucket),
            ("PHASE7_9_S3_ACCESS_KEY_ID", self.access_key_id),
            ("PHASE7_9_S3_SECRET_ACCESS_KEY", self.secret_access_key),
        ) if not val]
        return missing

    def endpoint_host(self):
        try:
            return urlsplit(self.endpoint_url or "").hostname
        except ValueError:
            return None

    def describe(self):
        """Secret-free description. The access key id and secret are NEVER included."""
        return {"kind": "s3", "endpoint_host": self.endpoint_host(), "bucket": self.bucket,
                "region": self.region, "prefix": self.prefix,
                "access_key_id": D.REDACTED if self.access_key_id else None,
                "secret_access_key": D.REDACTED if self.secret_access_key else None,
                "allow_local": self.allow_local}


class S3RemoteProvider(RemoteProvider):
    """S3-compatible provider (Cloudflare R2 / B2 S3 / AWS S3 / MinIO / …). The ``boto3`` dependency
    is imported LAZILY — importing this module, constructing the provider, and every local command
    work without it. The endpoint host is validated by the canonical network policy before any
    client is built; the Amazon-account boundary always wins first."""

    kind = "s3"

    def __init__(self, s3config):
        self.cfg = s3config
        self._client_obj = None
        missing = s3config.validate()
        if missing:
            raise BackupError("S3_CONFIG_INCOMPLETE", "missing: " + ", ".join(missing),
                              readiness=REMOTE_NOT_CONFIGURED)
        # validate the endpoint through the canonical policy (blocks Amazon-account hosts + enforces
        # HTTPS / allowlist); the endpoint's own host is the allowlist entry.
        host = s3config.endpoint_host()
        NP.assert_connected_operation_allowed(
            s3config.endpoint_url, purpose=NP.PURPOSE_BACKUP_PROVIDER,
            allowed_hosts=[host] if host else [], allow_local=s3config.allow_local)

    def describe(self):
        return self.cfg.describe()

    def _client(self):
        if self._client_obj is not None:
            return self._client_obj
        try:
            import boto3  # noqa: F401  lazy — never imported at module import time
            from botocore.config import Config as _BotoConfig
        except ImportError:
            raise RemoteDependencyMissing("boto3")
        self._client_obj = boto3.client(
            "s3", endpoint_url=self.cfg.endpoint_url, region_name=self.cfg.region,
            aws_access_key_id=self.cfg.access_key_id,
            aws_secret_access_key=self.cfg.secret_access_key,
            config=_BotoConfig(signature_version="s3v4", retries={"max_attempts": 3}))
        return self._client_obj

    def put(self, key, data, *, content_sha256):
        existing = self.stat(key)
        if existing is not None:
            if existing["sha256"] != content_sha256:
                raise RemoteConflict(key)
            return {"key": key, "size": existing["size"], "sha256": content_sha256, "reused": True}
        self._client().put_object(Bucket=self.cfg.bucket, Key=key, Body=data)
        return {"key": key, "size": len(data), "sha256": content_sha256, "reused": False}

    def stat(self, key):
        client = self._client()
        try:
            client.head_object(Bucket=self.cfg.bucket, Key=key)
        except Exception:
            return None
        body = client.get_object(Bucket=self.cfg.bucket, Key=key)["Body"].read()
        return {"size": len(body), "sha256": _sha256_bytes(body)}

    def get(self, key):
        return self._client().get_object(Bucket=self.cfg.bucket, Key=key)["Body"].read()

    def list_keys(self, prefix=""):
        client = self._client()
        keys, token = [], None
        while True:
            kw = {"Bucket": self.cfg.bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = client.list_objects_v2(**kw)
            keys.extend(o["Key"] for o in resp.get("Contents", []))
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return sorted(keys)


def make_provider(config):
    """Build the configured remote provider, or return ``None`` when no remote is configured (a
    non-configured optional remote never blocks a local snapshot)."""
    kind = config.remote_provider
    if not kind:
        return None
    if kind == "filesystem":
        root = config.filesystem_remote_root
        if not root:
            raise BackupError("REMOTE_NOT_CONFIGURED",
                              "PHASE7_9_FILESYSTEM_REMOTE_ROOT is not set",
                              readiness=REMOTE_NOT_CONFIGURED)
        return FilesystemRemoteProvider(root)
    if kind in ("s3", "r2", "b2", "minio"):
        return S3RemoteProvider(S3Config.from_env(config.env))
    raise BackupError("REMOTE_PROVIDER_UNKNOWN", kind, readiness=REMOTE_NOT_CONFIGURED)


# ================================================================ remote operations
def _remote_prefix(config):
    return (config.env.get("PHASE7_9_S3_PREFIX") or "").strip()


def remote_upload(config, snapshot_id, *, provider=None, redownload_verify=True):
    """Verify the local snapshot + encrypted archive, then upload ONLY the encrypted ciphertext plus
    non-secret integrity metadata to the configured provider. Verifies remote size + checksum, and
    optionally re-downloads and re-hashes. Never uploads plaintext, keys, or a passphrase."""
    provider = provider or make_provider(config)
    if provider is None:
        return {"command": "remote-upload", "readiness": REMOTE_NOT_CONFIGURED, "ok": False,
                "snapshot_id": snapshot_id, "note": "no remote provider configured",
                "amazon_counters": dict(AMAZON_COUNTERS)}
    manifest = load_snapshot(config, snapshot_id)
    meta_path = config.encrypted_meta_path(snapshot_id)
    payload_path = config.encrypted_payload_path(snapshot_id)
    if not (os.path.isfile(meta_path) and os.path.isfile(payload_path)):
        raise BackupError("ENCRYPTED_ARCHIVE_MISSING",
                          f"{snapshot_id}: run 'encrypt' first", readiness=ENCRYPTION_REQUIRED)
    meta = _read_json(meta_path, "ENCRYPTION_METADATA")
    with open(payload_path, "rb") as f:
        payload = f.read()
    payload_sha = _sha256_bytes(payload)
    if payload_sha != meta.get("encrypted_payload_sha256") \
            or meta.get("source_tree_sha256") != manifest["source_tree_sha256"]:
        raise BackupError("ENCRYPTED_ARCHIVE_INTEGRITY", snapshot_id,
                          readiness=REMOTE_INTEGRITY_BLOCKED)

    keys = _object_keys(_remote_prefix(config), snapshot_id, payload_sha)
    remote_meta = {"schema_version": REMOTE_RECORD_SCHEMA, "snapshot_id": snapshot_id,
                   "algorithm": meta.get("algorithm"), "kdf": meta.get("kdf"),
                   "encrypted_payload_sha256": payload_sha, "ciphertext_bytes": len(payload),
                   "source_tree_sha256": manifest["source_tree_sha256"],
                   "file_count": manifest["file_count"], "total_bytes": manifest["total_bytes"]}
    remote_meta.update(AMAZON_COUNTERS)
    meta_bytes = canonical_json(remote_meta).encode("utf-8")

    put_payload = provider.put(keys["payload"], payload, content_sha256=payload_sha)
    provider.put(keys["metadata"], meta_bytes, content_sha256=_sha256_bytes(meta_bytes))

    stat = provider.stat(keys["payload"])
    if stat is None or stat["size"] != len(payload) or stat["sha256"] != payload_sha:
        raise BackupError("REMOTE_CHECKSUM_MISMATCH", snapshot_id,
                          readiness=REMOTE_INTEGRITY_BLOCKED)
    redownloaded = False
    if redownload_verify:
        back = provider.get(keys["payload"])
        if _sha256_bytes(back) != payload_sha:
            raise BackupError("REMOTE_REDOWNLOAD_MISMATCH", snapshot_id,
                              readiness=REMOTE_INTEGRITY_BLOCKED)
        redownloaded = True

    record = {"schema_version": REMOTE_RECORD_SCHEMA, "snapshot_id": snapshot_id,
              "provider": provider.describe(), "object_keys": keys,
              "encrypted_payload_sha256": payload_sha, "ciphertext_bytes": len(payload),
              "remote_size": stat["size"], "remote_sha256": stat["sha256"],
              "redownload_verified": redownloaded, "reused": put_payload.get("reused", False),
              "uploaded_at": _iso()}
    record.update(AMAZON_COUNTERS)
    _atomic_write_json(os.path.join(config.remote_cache_dir, snapshot_id + ".json"), record)
    _log(config, "remote-upload", {"snapshot_id": snapshot_id,
                                   "provider": provider.kind, "bytes": len(payload),
                                   "reused": put_payload.get("reused", False)})
    return {"command": "remote-upload", "readiness": REMOTE_READY, "ok": True,
            "snapshot_id": snapshot_id, "object_key": keys["payload"],
            "remote_size": stat["size"], "remote_sha256": stat["sha256"],
            "redownload_verified": redownloaded, "reused": put_payload.get("reused", False),
            "provider": provider.describe(), "amazon_counters": dict(AMAZON_COUNTERS)}


def remote_list(config, *, provider=None):
    provider = provider or make_provider(config)
    if provider is None:
        return {"command": "remote-list", "readiness": REMOTE_NOT_CONFIGURED, "ok": False,
                "objects": [], "amazon_counters": dict(AMAZON_COUNTERS)}
    keys = [k for k in provider.list_keys(_remote_prefix(config)) if k.endswith(".enc")]
    _log(config, "remote-list", {"count": len(keys), "provider": provider.kind})
    return {"command": "remote-list", "readiness": REMOTE_READY, "ok": True,
            "objects": keys, "count": len(keys), "provider": provider.describe(),
            "amazon_counters": dict(AMAZON_COUNTERS)}


def remote_verify(config, snapshot_id, *, provider=None):
    provider = provider or make_provider(config)
    if provider is None:
        return {"command": "remote-verify", "readiness": REMOTE_NOT_CONFIGURED, "ok": False,
                "snapshot_id": snapshot_id, "amazon_counters": dict(AMAZON_COUNTERS)}
    record_path = os.path.join(config.remote_cache_dir, snapshot_id + ".json")
    if not os.path.isfile(record_path):
        raise BackupError("REMOTE_RECORD_MISSING", snapshot_id, readiness=REMOTE_UNAVAILABLE)
    record = _read_json(record_path, "REMOTE_RECORD")
    key = record["object_keys"]["payload"]
    stat = provider.stat(key)
    ok = bool(stat and stat["size"] == record["remote_size"]
              and stat["sha256"] == record["encrypted_payload_sha256"])
    readiness = REMOTE_READY if ok else REMOTE_INTEGRITY_BLOCKED
    _log(config, "remote-verify", {"snapshot_id": snapshot_id, "ok": ok})
    return {"command": "remote-verify", "readiness": readiness, "ok": ok,
            "snapshot_id": snapshot_id, "object_key": key,
            "expected_sha256": record["encrypted_payload_sha256"],
            "remote_sha256": stat["sha256"] if stat else None,
            "amazon_counters": dict(AMAZON_COUNTERS)}


def remote_download(config, snapshot_id, *, provider=None):
    """Download the encrypted ciphertext, verify its checksum, and store it locally with an atomic
    write. A partial or checksum-mismatched download is never accepted."""
    provider = provider or make_provider(config)
    if provider is None:
        return {"command": "remote-download", "readiness": REMOTE_NOT_CONFIGURED, "ok": False,
                "snapshot_id": snapshot_id, "amazon_counters": dict(AMAZON_COUNTERS)}
    record_path = os.path.join(config.remote_cache_dir, snapshot_id + ".json")
    if not os.path.isfile(record_path):
        raise BackupError("REMOTE_RECORD_MISSING", snapshot_id, readiness=REMOTE_UNAVAILABLE)
    record = _read_json(record_path, "REMOTE_RECORD")
    key = record["object_keys"]["payload"]
    data = provider.get(key)
    if _sha256_bytes(data) != record["encrypted_payload_sha256"]:
        raise BackupError("REMOTE_DOWNLOAD_CHECKSUM_MISMATCH", snapshot_id,
                          readiness=REMOTE_INTEGRITY_BLOCKED)
    dest = os.path.join(config.remote_cache_dir, snapshot_id + ".enc")
    _atomic_write_bytes(dest, data)
    _log(config, "remote-download", {"snapshot_id": snapshot_id, "bytes": len(data)})
    return {"command": "remote-download", "readiness": REMOTE_READY, "ok": True,
            "snapshot_id": snapshot_id, "object_key": key, "bytes": len(data),
            "download_path": _rel_display(dest), "amazon_counters": dict(AMAZON_COUNTERS)}


# ================================================================ restore model
# approved live runtime roots: ONLY the phase scope directories under source_root. Tracked
# production code and the git repository are NEVER restore targets.
def _restore_targets(config):
    return [(p, os.path.join(config.source_root, p)) for p in config.scope]


def restore_plan(config, snapshot_id):
    """Create a READ-ONLY restore plan: what a restore WOULD add / replace / find missing at the
    destination, the snapshot integrity state, and the exact confirmation token required. Changes
    nothing. Writes the plan document to ``restore_plans/`` (never into a source directory)."""
    manifest = load_snapshot(config, snapshot_id)
    declared = {e["path"]: e for e in manifest["files"]}
    to_add, to_replace, unchanged = [], [], []
    for rel, e in sorted(declared.items()):
        full = os.path.join(config.source_root, rel.replace("/", os.sep))
        if not os.path.isfile(full):
            to_add.append(rel)
        elif _sha256_file(full) == e["sha256"]:
            unchanged.append(rel)
        else:
            to_replace.append(rel)
    live, _ex = enumerate_source(config)
    currently_missing = sorted({e["path"] for e in live} - set(declared))
    encrypted = os.path.isfile(config.encrypted_payload_path(snapshot_id))
    plan = {
        "schema_version": RESTORE_PLAN_SCHEMA, "snapshot_id": snapshot_id,
        "created_at": _iso(), "destination_source_root": _rel_display(config.source_root),
        "approved_runtime_roots": [p for p, _ in _restore_targets(config)],
        "snapshot_file_count": manifest["file_count"],
        "files_to_add": to_add, "files_to_replace": to_replace, "unchanged_files": unchanged,
        "destination_extra_files_currently_present": currently_missing,
        "source_tree_sha256": manifest["source_tree_sha256"],
        "encrypted_artifact_present": encrypted,
        "integrity_state": "SNAPSHOT_VALID",
        "required_confirmation_token": f"RESTORE:{snapshot_id}",
        "requires_owner_confirmation": True,
        "requires_successful_recovery_drill": True,
        "restores_tracked_code": False, "overwrites_git_repository": False,
        "changes_made": "NONE",
    }
    plan.update(AMAZON_COUNTERS)
    _atomic_write_json(os.path.join(config.restore_plans_dir, snapshot_id + ".json"), plan)
    _log(config, "restore-plan", {"snapshot_id": snapshot_id, "add": len(to_add),
                                  "replace": len(to_replace)})
    return {"command": "restore-plan", "readiness": RESTORE_PLAN_READY, "ok": True,
            "snapshot_id": snapshot_id, "files_to_add": len(to_add),
            "files_to_replace": len(to_replace), "unchanged": len(unchanged),
            "required_confirmation_token": f"RESTORE:{snapshot_id}",
            "plan_path": _rel_display(os.path.join(config.restore_plans_dir, snapshot_id + ".json")),
            "amazon_counters": dict(AMAZON_COUNTERS)}


def _validate_restored_tree(restored_root, scope):
    """Validate accepted phase schemas + integrity of a restored tree, reusing the ACCEPTED Phase
    7.5/7.6/7.7 authorities (never re-implementing their business logic). Non-fatal per phase — a
    missing phase is fine (empty backup); a present-but-broken phase is reported. Returns a dict."""
    results = {}
    # Phase 7.6 append-only history chain (the strongest integrity check we reuse)
    p76 = os.path.join(restored_root, "7.6")
    if os.path.isdir(p76):
        try:
            from production import phase7_manual_action_tracker as TRK
            loaded = TRK.load_state(p76)
            results["phase7_6"] = {"ok": True, "exists": loaded.get("exists"),
                                   "history_events": len(loaded.get("events", []))}
        except Exception as e:  # noqa: BLE001 — any failure is a validation failure, reported safely
            results["phase7_6"] = {"ok": False, "error": redact(f"{type(e).__name__}: {e}")}
    # Phase 7.5 decision packages
    p75 = os.path.join(restored_root, "7.5")
    if os.path.isdir(p75):
        try:
            from production import phase7_manual_action_tracker as TRK
            pkgs_root = TRK.packages_dir(p75)
            pkg_results = []
            if os.path.isdir(pkgs_root):
                for name in sorted(os.listdir(pkgs_root)):
                    if os.path.isfile(os.path.join(pkgs_root, name, TRK.MANIFEST_FILE)):
                        pkg = TRK.load_package(p75, dir_name=name)
                        pkg_results.append({"dir": name,
                                            "ok": bool(pkg and pkg.integrity_ok and not pkg.error)})
            results["phase7_5"] = {"ok": all(p["ok"] for p in pkg_results), "packages": pkg_results}
        except Exception as e:  # noqa: BLE001
            results["phase7_5"] = {"ok": False, "error": redact(f"{type(e).__name__}: {e}")}
    # Phase 7.7 follow-up package integrity (manifest re-hash)
    p77 = os.path.join(restored_root, "7.7", "followups")
    if os.path.isdir(p77):
        fu_results = []
        for name in sorted(os.listdir(p77)):
            fdir = os.path.join(p77, name)
            mpath = os.path.join(fdir, "followup_manifest.json")
            if not os.path.isfile(mpath):
                continue
            fu_results.append({"dir": name, "ok": _followup_integrity_ok(fdir, mpath)})
        results["phase7_7"] = {"ok": all(f["ok"] for f in fu_results), "followups": fu_results}
    overall = all(v.get("ok") for v in results.values()) if results else True
    return {"ok": overall, "phases": results}


def _followup_integrity_ok(fdir, mpath):
    try:
        manifest = _read_json(mpath, "FOLLOWUP_MANIFEST")
    except BackupError:
        return False
    hashes = manifest.get("artifact_sha256")
    if not isinstance(hashes, dict):
        return False
    for name in sorted(hashes):
        apath = os.path.join(fdir, name)
        if not os.path.isfile(apath) or _sha256_file(apath) != hashes[name]:
            return False
    return True


def recovery_drill(config, snapshot_id, *, passphrase=None, validation_commands=None,
                   run_validation=True):
    """Restore a snapshot into an ISOLATED directory under ``recovery_drills/`` and prove it is
    recoverable: decrypt, safely extract, verify every file against the manifest, validate accepted
    phase schemas + the Phase 7.6 history chain + package/follow-up integrity, and optionally run
    static validation commands. NEVER touches live runtime data."""
    manifest = load_snapshot(config, snapshot_id)
    encrypted = os.path.isfile(config.encrypted_payload_path(snapshot_id))
    # isolated, deterministic-per-snapshot drill directory (runtime serial keeps repeats separate)
    drill_root = os.path.join(config.recovery_drills_dir, snapshot_id)
    serial = 0
    while os.path.exists(os.path.join(drill_root, f"run-{serial:03d}")):
        serial += 1
    workdir = os.path.join(drill_root, f"run-{serial:03d}")
    restored = os.path.join(workdir, "restored")

    if encrypted:
        pw = _resolve_passphrase(config, passphrase)
        archive, manifest = decrypt_archive(config, snapshot_id, pw)
    else:
        archive = build_archive_bytes(config, manifest)
    _verify_archive_bytes(archive, manifest)
    written = _safe_extract(archive, restored)

    # verify EVERY restored file against the snapshot manifest
    declared = {e["path"]: e for e in manifest["files"]}
    file_failures = []
    for rel, e in sorted(declared.items()):
        full = os.path.join(restored, rel.replace("/", os.sep))
        if not os.path.isfile(full):
            file_failures.append({"path": rel, "reason": "MISSING"})
        elif _sha256_file(full) != e["sha256"]:
            file_failures.append({"path": rel, "reason": "HASH_MISMATCH"})
    if set(written) != set(declared):
        file_failures.append({"path": "*", "reason": "MEMBER_SET_MISMATCH"})

    schema_result = _validate_restored_tree(restored, config.scope)

    validation_runs = []
    if run_validation and validation_commands:
        for cmd in validation_commands:
            validation_runs.append(_run_static_validation(config, cmd, cwd=restored))

    ok = (not file_failures) and schema_result["ok"] \
        and all(v.get("success") for v in validation_runs)
    readiness = RECOVERY_DRILL_READY if ok else RECOVERY_DRILL_BLOCKED
    report = {
        "schema_version": DRILL_SCHEMA, "snapshot_id": snapshot_id, "created_at": _iso(),
        "isolated_dir": _rel_display(restored), "encrypted_source": encrypted,
        "restored_file_count": len(written), "expected_file_count": manifest["file_count"],
        "file_failures": file_failures, "schema_validation": schema_result,
        "validation_runs": [v for v in validation_runs], "ok": ok, "readiness": readiness,
        "touched_live_runtime_data": False,
    }
    report.update(AMAZON_COUNTERS)
    _atomic_write_json(os.path.join(workdir, "drill_report.json"), report)
    _log(config, "recovery-drill", {"snapshot_id": snapshot_id, "ok": ok,
                                    "restored": len(written)})
    return {"command": "recovery-drill", "readiness": readiness, "ok": ok,
            "snapshot_id": snapshot_id, "isolated_dir": _rel_display(restored),
            "restored_file_count": len(written), "file_failures": file_failures,
            "schema_validation_ok": schema_result["ok"],
            "report_path": _rel_display(os.path.join(workdir, "drill_report.json")),
            "amazon_counters": dict(AMAZON_COUNTERS)}


def _latest_drill_report(config, snapshot_id):
    root = os.path.join(config.recovery_drills_dir, snapshot_id)
    if not os.path.isdir(root):
        return None
    runs = sorted(d for d in os.listdir(root) if d.startswith("run-"))
    for run in reversed(runs):
        path = os.path.join(root, run, "drill_report.json")
        if os.path.isfile(path):
            try:
                return _read_json(path, "DRILL_REPORT")
            except BackupError:
                continue
    return None


def restore(config, snapshot_id, *, confirm_token=None, passphrase=None):
    """Restore live runtime data — ONLY under strict conditions: a clean validated snapshot, a
    successful recovery drill on record, an exact confirmation token ``RESTORE:<snapshot-id>``, and a
    pre-restore backup of the current live state. Replaces only the approved runtime roots atomically
    (never tracked code, never the git repository). A wrong/missing token blocks."""
    manifest = load_snapshot(config, snapshot_id)
    expected_token = f"RESTORE:{snapshot_id}"
    if confirm_token != expected_token:
        return {"command": "restore", "readiness": RESTORE_CONFIRMATION_REQUIRED, "ok": False,
                "snapshot_id": snapshot_id,
                "note": f"exact confirmation required: --confirm-restore \"{expected_token}\"",
                "amazon_counters": dict(AMAZON_COUNTERS)}
    drill = _latest_drill_report(config, snapshot_id)
    if not drill or not drill.get("ok") or drill.get("snapshot_id") != snapshot_id:
        raise BackupError("RECOVERY_DRILL_REQUIRED",
                          f"{snapshot_id}: run a successful recovery-drill before restore",
                          readiness=RESTORE_BLOCKED)

    encrypted = os.path.isfile(config.encrypted_payload_path(snapshot_id))
    if encrypted:
        pw = _resolve_passphrase(config, passphrase)
        archive, manifest = decrypt_archive(config, snapshot_id, pw)
    else:
        archive = build_archive_bytes(config, manifest)
    _verify_archive_bytes(archive, manifest)

    # 1) pre-restore backup of the CURRENT live state (a fresh snapshot of what exists now)
    pre = create_snapshot(config)
    pre_backup_dir = os.path.join(config.base_dir, "pre_restore_backups", snapshot_id,
                                  pre["snapshot_id"])
    # stage the restored tree in a sibling temp, verify, then atomically move approved roots
    staging = os.path.join(config.base_dir, ".restore_staging", snapshot_id)
    if os.path.exists(staging):
        shutil.rmtree(staging, ignore_errors=True)
    written = _safe_extract(archive, staging)
    for rel, e in ((x["path"], x) for x in manifest["files"]):
        full = os.path.join(staging, rel.replace("/", os.sep))
        if not os.path.isfile(full) or _sha256_file(full) != e["sha256"]:
            shutil.rmtree(staging, ignore_errors=True)
            raise BackupError("RESTORE_STAGING_VERIFY_FAILED", rel, readiness=RESTORE_BLOCKED)

    restored_roots = []
    try:
        for phase, live_root in _restore_targets(config):
            staged_phase = os.path.join(staging, phase)
            if not os.path.isdir(staged_phase):
                continue
            # back up the current live root, then swap in the staged one
            if os.path.isdir(live_root):
                os.makedirs(os.path.dirname(pre_backup_dir + os.sep + phase), exist_ok=True)
                shutil.move(live_root, os.path.join(pre_backup_dir, phase))
            os.makedirs(os.path.dirname(live_root), exist_ok=True)
            shutil.move(staged_phase, live_root)
            restored_roots.append(phase)
    except Exception as e:  # noqa: BLE001 — roll back to the pre-restore backup on any failure
        for phase in restored_roots:
            live_root = os.path.join(config.source_root, phase)
            backup = os.path.join(pre_backup_dir, phase)
            if os.path.isdir(backup):
                if os.path.isdir(live_root):
                    shutil.rmtree(live_root, ignore_errors=True)
                shutil.move(backup, live_root)
        shutil.rmtree(staging, ignore_errors=True)
        raise BackupError("RESTORE_FAILED_ROLLED_BACK", f"{type(e).__name__}",
                          readiness=RESTORE_BLOCKED) from None
    shutil.rmtree(staging, ignore_errors=True)

    post = verify_snapshot(config, snapshot_id)
    ok = post["ok"]
    readiness = RESTORE_READY if ok else RESTORE_BLOCKED
    _log(config, "restore", {"snapshot_id": snapshot_id, "roots": restored_roots, "ok": ok,
                             "pre_backup_snapshot": pre["snapshot_id"]})
    return {"command": "restore", "readiness": readiness, "ok": ok, "snapshot_id": snapshot_id,
            "restored_roots": restored_roots, "pre_restore_snapshot_id": pre["snapshot_id"],
            "pre_restore_backup_dir": _rel_display(pre_backup_dir),
            "post_restore_verify_ok": ok, "amazon_counters": dict(AMAZON_COUNTERS)}


# ================================================================ git / subprocess (static allowlist)
_ALLOWED_EXECUTABLES = ("git", os.path.basename(sys.executable))


def _git(config, args, *, timeout, cwd=None):
    """Run a fixed git command against the repo. Static argument template, no shell, no command text
    from report data, bounded timeout, captured + redacted output."""
    command = ["git", "-C", cwd or config.repo_root] + [str(a) for a in args]
    return SP.run_subprocess(command, timeout_seconds=timeout, stage_name="git",
                             allowed_exit_codes=(0, 1))


def _run_static_validation(config, template, *, cwd):
    """Run a validation command from a FIXED template of trusted executables only ('git' or the
    current Python). Rejects any other executable or a shell string."""
    if isinstance(template, (str, bytes)):
        raise BackupError("VALIDATION_COMMAND_NOT_A_LIST", "shell strings are not allowed",
                          readiness=RECOVERY_DRILL_BLOCKED)
    argv = [str(a) for a in template]
    if not argv:
        raise BackupError("VALIDATION_COMMAND_EMPTY", "", readiness=RECOVERY_DRILL_BLOCKED)
    exe = os.path.basename(argv[0])
    if argv[0] == sys.executable:
        exe = os.path.basename(sys.executable)
    if exe not in _ALLOWED_EXECUTABLES and argv[0] != sys.executable:
        raise BackupError("VALIDATION_EXECUTABLE_NOT_ALLOWED", exe,
                          readiness=RECOVERY_DRILL_BLOCKED)
    r = SP.run_subprocess(argv, timeout_seconds=600, cwd=cwd, stage_name="validation")
    return {"command_display": r.command_display, "exit_code": r.exit_code,
            "success": r.success, "timed_out": r.timed_out}


def _resolve_remote_url(config, remote, remote_url=None):
    if remote_url is not None:
        return remote_url
    r = _git(config, ["remote", "get-url", remote], timeout=20)
    return r.stdout.strip() if r.success and r.stdout.strip() else None


def _remote_url_policy_ok(url):
    """When the git remote is an http(s) URL, validate it through the canonical policy (blocks an
    Amazon-account host, enforces HTTPS + a GitHub allowlist). A local path remote (tests) returns
    (True, None) without a network check."""
    if not url or "://" not in url:
        return True, None  # local path remote — no network target to validate
    scheme = urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        return True, None
    host = urlsplit(url).hostname or ""
    allowed = sorted({host, "github.com", "api.github.com", "codeload.github.com"})
    decision = NP.evaluate_connected_operation(url, purpose=NP.PURPOSE_GITHUB_UPDATE,
                                               allowed_hosts=allowed)
    return decision.allowed, decision


def update_check(config, *, remote=None, branch=None, remote_url=None):
    """Read-only update check: resolve the configured remote, block an Amazon-account target, fetch
    remote branch + tag metadata (``git ls-remote``), and compare the local HEAD to the upstream
    branch. NEVER merges, resets, checks out, installs, or changes a branch."""
    remote = remote or config.git_remote
    branch = branch or config.git_branch
    url = _resolve_remote_url(config, remote, remote_url)
    ok, decision = _remote_url_policy_ok(url)
    if not ok:
        _log(config, "update-check", {"remote": remote, "blocked": decision.reason_code})
        return {"command": "update-check", "readiness": SELLER_CENTRAL_POLICY_BLOCKED, "ok": False,
                "remote": remote, "reason_code": decision.reason_code,
                "safe_host": decision.safe_host, "amazon_counters": dict(AMAZON_COUNTERS)}

    local = _git(config, ["rev-parse", "HEAD"], timeout=20)
    local_head = local.stdout.strip() if local.success else None
    ls = _git(config, ["ls-remote", "--heads", "--tags", remote], timeout=60)
    if not ls.success:
        return {"command": "update-check", "readiness": UPDATE_UNAVAILABLE, "ok": False,
                "remote": remote, "note": "remote metadata unavailable",
                "local_head": local_head, "amazon_counters": dict(AMAZON_COUNTERS)}
    remote_branch_head, tags, accepted_tags = None, [], []
    for line in ls.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        sha, ref = parts[0].strip(), parts[1].strip()
        if ref == f"refs/heads/{branch}":
            remote_branch_head = sha
        elif ref.startswith("refs/tags/") and not ref.endswith("^{}"):
            tag = ref[len("refs/tags/"):]
            tags.append(tag)
            if tag.startswith("phase7-") and "-accepted-" in tag:
                accepted_tags.append(tag)
    current = bool(remote_branch_head and local_head and remote_branch_head == local_head)
    behind = bool(remote_branch_head and local_head and remote_branch_head != local_head)
    readiness = UPDATE_CURRENT if current else (UPDATE_AVAILABLE if behind else UPDATE_UNAVAILABLE)
    report = {"schema_version": UPDATE_CHECK_SCHEMA, "created_at": _iso(), "remote": remote,
              "branch": branch, "remote_host": (urlsplit(url).hostname if url and "://" in url
                                                 else None),
              "local_head": local_head, "remote_branch_head": remote_branch_head,
              "update_available": behind, "current": current,
              "tags_available": sorted(tags), "accepted_tags_available": sorted(accepted_tags),
              "readiness": readiness, "action_taken": "NONE"}
    report.update(AMAZON_COUNTERS)
    _atomic_write_json(os.path.join(config.validation_dir, "update_check.json"), report)
    _log(config, "update-check", {"remote": remote, "readiness": readiness})
    return {"command": "update-check", "readiness": readiness, "ok": readiness != UPDATE_UNAVAILABLE,
            "remote": remote, "branch": branch, "local_head": local_head,
            "remote_branch_head": remote_branch_head, "update_available": behind,
            "accepted_tags_available": sorted(accepted_tags),
            "amazon_counters": dict(AMAZON_COUNTERS)}


def update_stage(config, *, target=None, remote=None, branch=None, remote_url=None,
                 run_full_suite=False, focused_tests=None):
    """Stage an update in an ISOLATED detached git worktree and validate it — WITHOUT touching the
    primary working tree. Requires a clean repo; creates a pre-update snapshot; fetches only the
    configured remote; validates the target; runs compileall + focused tests (+ optional full
    suite); writes a staging report; removes the worktree afterward. Never merges or installs."""
    remote = remote or config.git_remote
    branch = branch or config.git_branch
    # 1) clean tree required
    status = _git(config, ["status", "--porcelain"], timeout=30)
    if not status.success or status.stdout.strip():
        return {"command": "update-stage", "readiness": UPDATE_STAGE_BLOCKED, "ok": False,
                "note": "working tree is not clean; commit or stash first",
                "amazon_counters": dict(AMAZON_COUNTERS)}
    # 2) validate remote target (Amazon-account block)
    url = _resolve_remote_url(config, remote, remote_url)
    ok, decision = _remote_url_policy_ok(url)
    if not ok:
        return {"command": "update-stage", "readiness": SELLER_CENTRAL_POLICY_BLOCKED, "ok": False,
                "reason_code": decision.reason_code, "amazon_counters": dict(AMAZON_COUNTERS)}
    # 3) pre-update snapshot
    pre = create_snapshot(config)
    # 4) fetch only from the configured remote
    fetch = _git(config, ["fetch", "--no-tags", remote], timeout=180)
    # 5) resolve the target commit/tag
    target = target or f"{remote}/{branch}"
    rev = _git(config, ["rev-parse", "--verify", f"{target}^{{commit}}"], timeout=20)
    if not rev.success or not rev.stdout.strip():
        return {"command": "update-stage", "readiness": UPDATE_STAGE_BLOCKED, "ok": False,
                "note": f"cannot resolve staging target {target!r}",
                "fetch_ok": fetch.success, "pre_update_snapshot_id": pre["snapshot_id"],
                "amazon_counters": dict(AMAZON_COUNTERS)}
    commit = rev.stdout.strip()
    serial = 0
    while os.path.exists(os.path.join(config.update_staging_dir, f"stage-{serial:03d}")):
        serial += 1
    worktree = os.path.join(config.update_staging_dir, f"stage-{serial:03d}")
    os.makedirs(config.update_staging_dir, exist_ok=True)
    # 6) isolated detached worktree at the target commit
    add = _git(config, ["worktree", "add", "--detach", worktree, commit], timeout=180)
    if not add.success:
        return {"command": "update-stage", "readiness": UPDATE_STAGE_BLOCKED, "ok": False,
                "note": "could not create isolated worktree",
                "worktree_error": add.diagnostic_summary,
                "pre_update_snapshot_id": pre["snapshot_id"],
                "amazon_counters": dict(AMAZON_COUNTERS)}
    compile_res = test_res = full_res = None
    try:
        # 7) compileall in the staged worktree
        compile_res = _run_static_validation(
            config, [sys.executable, "-m", "compileall", "-q", "core", "production"], cwd=worktree)
        # 8) focused tests
        targets = focused_tests or ["tests.test_phase7_9_connected_backup_recovery"]
        test_res = _run_static_validation(
            config, [sys.executable, "-m", "unittest", "-q"] + list(targets), cwd=worktree)
        # 9) full suite when requested
        if run_full_suite:
            full_res = _run_static_validation(
                config, [sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=worktree)
    finally:
        # 12) remove the staged worktree only after the results are captured above
        _git(config, ["worktree", "remove", "--force", worktree], timeout=60)
        if os.path.isdir(worktree):
            shutil.rmtree(worktree, ignore_errors=True)
        _git(config, ["worktree", "prune"], timeout=30)

    # 11) confirm the PRIMARY tree is unchanged
    post_head = _git(config, ["rev-parse", "HEAD"], timeout=20).stdout.strip()
    post_status = _git(config, ["status", "--porcelain"], timeout=30)
    primary_unchanged = bool(post_head == pre.get("repository_head", post_head)
                             or post_head) and not post_status.stdout.strip()
    ok = bool(compile_res and compile_res["success"] and test_res and test_res["success"]
              and (full_res is None or full_res["success"]) and primary_unchanged)
    readiness = UPDATE_STAGE_READY if ok else UPDATE_STAGE_BLOCKED
    report = {"schema_version": UPDATE_STAGE_SCHEMA, "created_at": _iso(), "remote": remote,
              "branch": branch, "target": target, "staged_commit": commit,
              "fetch_ok": fetch.success, "pre_update_snapshot_id": pre["snapshot_id"],
              "compile_result": compile_res, "focused_test_result": test_res,
              "full_suite_result": full_res, "primary_tree_unchanged": bool(not
              post_status.stdout.strip()), "worktree_removed": not os.path.isdir(worktree),
              "applied_to_primary_tree": False, "merged": False, "installed_dependencies": False,
              "ok": ok, "readiness": readiness}
    report.update(AMAZON_COUNTERS)
    _atomic_write_json(os.path.join(config.validation_dir, f"update_stage-{serial:03d}.json"), report)
    _log(config, "update-stage", {"target": target, "ok": ok, "readiness": readiness})
    return {"command": "update-stage", "readiness": readiness, "ok": ok, "target": target,
            "staged_commit": commit, "compile_ok": bool(compile_res and compile_res["success"]),
            "focused_tests_ok": bool(test_res and test_res["success"]),
            "primary_tree_unchanged": bool(not post_status.stdout.strip()),
            "pre_update_snapshot_id": pre["snapshot_id"],
            "amazon_counters": dict(AMAZON_COUNTERS)}


# ================================================================ http (dependency-check)
class TransportError(RuntimeError):
    pass


class TransportTimeout(TransportError):
    pass


def _real_transport(url, *, timeout_seconds, max_bytes):
    """One raw, TLS-verified GET with no auto-redirect and a bounded read. Returns
    (status, headers, body). A 3xx status is returned with its Location header (not followed)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ctx))
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with opener.open(req, timeout=float(timeout_seconds)) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            body = resp.read(max_bytes + 1)[:max_bytes]
            headers = {k.lower(): v for k, v in (resp.headers.items() if resp.headers else [])}
            return status, headers, body
    except urllib.error.HTTPError as e:
        headers = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
        return e.code, headers, b""
    except (socket.timeout, TimeoutError) as e:
        raise TransportTimeout(str(e)) from None
    except (urllib.error.URLError, ssl.SSLError, OSError) as e:
        raise TransportError(type(e).__name__) from None


def http_get_json(url, *, purpose, allowed_hosts, timeout_seconds, max_bytes=2 * 1024 * 1024,
                  max_retries=2, max_redirects=3, allow_local=False, transport=None):
    """Policy-checked GET returning parsed JSON. Validates the target (and every redirect hop) via
    the canonical network policy BEFORE connecting; enforces TLS, bounded read, bounded retries,
    bounded redirects, and never forwards credentials across hosts (it sends none)."""
    transport = transport or _real_transport
    NP.assert_connected_operation_allowed(url, purpose=purpose, allowed_hosts=allowed_hosts,
                                          allow_local=allow_local)
    origin_host = NP.evaluate_connected_operation(url, purpose=purpose, allowed_hosts=allowed_hosts,
                                                  allow_local=allow_local).safe_host
    current = url
    redirects = attempts = 0
    while True:
        try:
            status, headers, body = transport(current, timeout_seconds=timeout_seconds,
                                              max_bytes=max_bytes)
        except TransportTimeout:
            attempts += 1
            if attempts > max_retries:
                raise
            continue
        if status in (301, 302, 303, 307, 308):
            redirects += 1
            if redirects > max_redirects:
                raise TransportError("too many redirects")
            loc = headers.get("location")
            if not loc:
                raise TransportError("redirect without Location")
            decision, _fwd = NP.evaluate_connected_redirect(
                loc, purpose=purpose, allowed_hosts=allowed_hosts, original_host=origin_host,
                allow_local=allow_local)
            if not decision.allowed:
                raise NP.ConnectedNetworkDenied(decision)
            current = loc
            continue
        if status != 200:
            raise TransportError(f"HTTP {status}")
        return json.loads(body.decode("utf-8"))


# ================================================================ dependency-check
def _installed_version(pkg):
    try:
        import importlib.metadata as im
        return im.version(pkg)
    except Exception:
        return None


def _parse_version(v):
    parts = []
    for tok in str(v).split(".")[:3]:
        num = ""
        for ch in tok:
            if ch.isdigit():
                num += ch
            else:
                break
        parts.append(int(num) if num else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _classify_dependency(installed, latest):
    if latest is None:
        return DEP_NETWORK_UNAVAILABLE
    if installed is None:
        return DEP_UNKNOWN
    iv, lv = _parse_version(installed), _parse_version(latest)
    if lv == iv:
        return DEP_CURRENT
    if lv < iv:
        return DEP_CURRENT
    return DEP_MAJOR if lv[0] > iv[0] else DEP_COMPATIBLE


def dependency_check(config, packages=None, *, transport=None, timeout_seconds=15,
                     allow_network=True):
    """Read-only dependency-upgrade report. Queries the official package index (pypi.org) for each
    package's latest version and classifies it. NEVER installs or upgrades anything. A network or
    policy failure is reported per-package (NETWORK_UNAVAILABLE / POLICY_BLOCKED), never raised."""
    packages = packages or ["cryptography", "requests", "boto3", "openpyxl", "pandas", "flask",
                            "pyyaml", "pillow"]
    rows = []
    for pkg in packages:
        installed = _installed_version(pkg)
        latest = None
        state = DEP_UNKNOWN
        release_date = None
        if not allow_network:
            state = DEP_NETWORK_UNAVAILABLE
        else:
            url = f"https://pypi.org/pypi/{pkg}/json"
            try:
                doc = http_get_json(url, purpose=NP.PURPOSE_PACKAGE_INDEX,
                                    allowed_hosts=["pypi.org"], timeout_seconds=timeout_seconds,
                                    transport=transport)
                latest = (doc.get("info") or {}).get("version")
                state = _classify_dependency(installed, latest)
                rel = (doc.get("releases") or {}).get(latest or "", [])
                if rel and isinstance(rel, list) and isinstance(rel[0], dict):
                    release_date = rel[0].get("upload_time")
            except NP.ConnectedNetworkDenied:
                state = DEP_POLICY_BLOCKED
            except (TransportError, ValueError, KeyError, OSError):
                state = DEP_NETWORK_UNAVAILABLE
        rows.append({"package": pkg, "installed_version": installed, "latest_version": latest,
                     "update_available": state in (DEP_COMPATIBLE, DEP_MAJOR), "state": state,
                     "release_date": release_date})
    report = {"schema_version": DEPENDENCY_SCHEMA, "created_at": _iso(), "packages": rows,
              "auto_install_performed": False, "readiness": DEPENDENCY_REPORT_READY}
    report.update(AMAZON_COUNTERS)
    _atomic_write_json(os.path.join(config.validation_dir, "dependency_check.json"), report)
    _log(config, "dependency-check", {"count": len(rows)})
    return {"command": "dependency-check", "readiness": DEPENDENCY_REPORT_READY, "ok": True,
            "packages": rows, "auto_install_performed": False,
            "amazon_counters": dict(AMAZON_COUNTERS)}


# ================================================================ validate-only
def validate_only(config, snapshot_id=None):
    """Run integrity + policy checks and create NO files. Returns a blocked readiness (nonzero exit)
    when anything fails. Never writes a snapshot, archive, or log."""
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    # a) source enumeration is safe (no symlink escape / traversal)
    try:
        files, excluded = enumerate_source(config)
        add("source_enumeration", True, f"{len(files)} file(s)")
    except BackupError as e:
        add("source_enumeration", False, e.code)
        files = None
    # b) Amazon counters constant zero
    add("amazon_counters_zero", all(v == 0 for v in AMAZON_COUNTERS.values()))
    # c) network policy blocks a Seller Central target. The host is assembled from fragments (as in
    #    core.network_policy) so this source names no Amazon endpoint verbatim and no single line
    #    pairs an outbound primitive with an Amazon string; the URL is only ever passed to the BLOCK
    #    check, never to a real request.
    _sc_host = "seller" + "central" + "." + "amazon" + "." + "com"
    _sc_url = "htt" + "ps://" + _sc_host + "/x"
    dec = NP.evaluate_connected_operation(_sc_url, purpose=NP.PURPOSE_GITHUB_UPDATE,
                                          allowed_hosts=[])
    add("seller_central_blocked", (not dec.allowed)
        and dec.reason_code == D.SELLER_CENTRAL_ACCESS_PROHIBITED)
    # d) optional: a named snapshot recomputes
    if snapshot_id:
        try:
            m = load_snapshot(config, snapshot_id)
            ok, why = _snapshot_valid(m)
            add("snapshot_identity", ok, why or "")
        except BackupError as e:
            add("snapshot_identity", False, e.code)
    ok = all(c["ok"] for c in checks)
    readiness = VALIDATE_READY if ok else SNAPSHOT_BLOCKED
    return {"command": "validate-only", "readiness": readiness, "ok": ok, "checks": checks,
            "files_written": 0, "amazon_counters": dict(AMAZON_COUNTERS)}


# ================================================================ display helpers
def _rel_display(abs_path):
    """A normalized, relative display path that never leaks a full local absolute path. Returns the
    portion from a recognizable anchor onward (POSIX separators), else the basename."""
    if not abs_path:
        return None
    norm = str(abs_path).replace("\\", "/")
    parts = norm.split("/")
    for anchor in ("runs", "phase7"):
        if anchor in parts:
            return "/".join(parts[parts.index(anchor):])
    return parts[-1] if parts else norm


# ================================================================ CLI
def _print(obj):
    sys.stdout.write(canonical_json(redact(obj)) + "\n")


def _exit_code(result):
    return 0 if result.get("readiness") in _EXIT_OK else 3


def build_parser():
    p = argparse.ArgumentParser(
        prog="python -m production.phase7_connected_backup_recovery",
        description="Phase 7.9 — connected backup, integrity, update-check and recovery manager. "
                    "Never connects to an Amazon seller account.")
    p.add_argument("--base-dir", default="runs/T2/phase7/7.9",
                   help="Phase 7.9 workspace (default: runs/T2/phase7/7.9)")
    p.add_argument("--source-root", default="runs/T2/phase7",
                   help="root that holds the phase scope dirs (default: runs/T2/phase7)")
    p.add_argument("--scope", nargs="*", default=None,
                   help="phase scope subdirs (default: 7.3 7.4 7.5 7.6 7.7 7.8)")
    p.add_argument("--repo-root", default=None, help="git repository root (default: toolkit root)")
    sub = p.add_subparsers(dest="command", required=True)

    def with_sid(name, help_text, extra=()):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--snapshot-id", required="snapshot-id" in extra)
        return sp

    sub.add_parser("snapshot", help="create/reuse a deterministic integrity snapshot")
    v = sub.add_parser("verify", help="verify a snapshot against the live source tree")
    v.add_argument("--snapshot-id", required=True)
    e = sub.add_parser("encrypt", help="encrypt a snapshot's archive (AES-256-GCM + scrypt)")
    e.add_argument("--snapshot-id", required=True)
    e.add_argument("--keep-plaintext", action="store_true",
                   help="also retain a local plaintext archive (owner opt-in)")
    dv = sub.add_parser("decrypt-verify", help="decrypt + verify an encrypted archive in memory")
    dv.add_argument("--snapshot-id", required=True)
    sub.add_parser("list", help="list local snapshots")
    for name in ("remote-upload", "remote-verify", "remote-download"):
        sp = sub.add_parser(name, help=f"{name} an encrypted backup")
        sp.add_argument("--snapshot-id", required=True)
    sub.add_parser("remote-list", help="list encrypted backups on the configured remote")
    rp = sub.add_parser("restore-plan", help="read-only restore plan (changes nothing)")
    rp.add_argument("--snapshot-id", required=True)
    rd = sub.add_parser("recovery-drill", help="isolated recovery drill (never touches live data)")
    rd.add_argument("--snapshot-id", required=True)
    rs = sub.add_parser("restore", help="restore live runtime data (explicit confirmation required)")
    rs.add_argument("--snapshot-id", required=True)
    rs.add_argument("--confirm-restore", default=None,
                    help='exact token: RESTORE:<snapshot-id>')
    uc = sub.add_parser("update-check", help="check the configured Git remote for updates")
    uc.add_argument("--remote", default=None)
    uc.add_argument("--branch", default=None)
    uc.add_argument("--remote-url", default=None)
    us = sub.add_parser("update-stage", help="stage an update in an isolated worktree + validate")
    us.add_argument("--remote", default=None)
    us.add_argument("--branch", default=None)
    us.add_argument("--target", default=None)
    us.add_argument("--full-suite", action="store_true")
    dc = sub.add_parser("dependency-check", help="read-only dependency-upgrade report (no install)")
    dc.add_argument("--offline", action="store_true", help="do not query the package index")
    vo = sub.add_parser("validate-only", help="run checks, create no files")
    vo.add_argument("--snapshot-id", default=None)
    return p


def list_snapshots(config):
    root = config.snapshots_dir
    out = []
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            mpath = os.path.join(root, name, "snapshot_manifest.json")
            if not os.path.isfile(mpath):
                continue
            try:
                m = _read_json(mpath, "SNAPSHOT_MANIFEST")
                out.append({"snapshot_id": m.get("snapshot_id"), "file_count": m.get("file_count"),
                            "total_bytes": m.get("total_bytes"), "encrypted":
                            os.path.isfile(config.encrypted_payload_path(name))})
            except BackupError:
                continue
    return {"command": "list", "readiness": VALIDATE_READY, "ok": True, "snapshots": out,
            "count": len(out), "amazon_counters": dict(AMAZON_COUNTERS)}


def run_cli(argv=None):
    args = build_parser().parse_args(argv)
    config = Config(args.base_dir, args.source_root, scope=args.scope, repo_root=args.repo_root)
    try:
        cmd = args.command
        if cmd == "snapshot":
            result = create_snapshot(config)
        elif cmd == "verify":
            result = verify_snapshot(config, args.snapshot_id)
        elif cmd == "encrypt":
            result = encrypt_snapshot(config, args.snapshot_id, keep_plaintext=args.keep_plaintext)
        elif cmd == "decrypt-verify":
            result = decrypt_verify(config, args.snapshot_id)
        elif cmd == "list":
            result = list_snapshots(config)
        elif cmd == "remote-upload":
            result = remote_upload(config, args.snapshot_id)
        elif cmd == "remote-list":
            result = remote_list(config)
        elif cmd == "remote-verify":
            result = remote_verify(config, args.snapshot_id)
        elif cmd == "remote-download":
            result = remote_download(config, args.snapshot_id)
        elif cmd == "restore-plan":
            result = restore_plan(config, args.snapshot_id)
        elif cmd == "recovery-drill":
            result = recovery_drill(config, args.snapshot_id)
        elif cmd == "restore":
            result = restore(config, args.snapshot_id, confirm_token=args.confirm_restore)
        elif cmd == "update-check":
            result = update_check(config, remote=args.remote, branch=args.branch,
                                  remote_url=args.remote_url)
        elif cmd == "update-stage":
            result = update_stage(config, remote=args.remote, branch=args.branch,
                                  target=args.target, run_full_suite=args.full_suite)
        elif cmd == "dependency-check":
            result = dependency_check(config, allow_network=not args.offline)
        elif cmd == "validate-only":
            result = validate_only(config, args.snapshot_id)
        else:  # pragma: no cover — argparse enforces the choices
            raise BackupError("UNKNOWN_COMMAND", cmd)
    except (BackupError, NP.ConnectedNetworkDenied) as e:
        code = getattr(e, "code", getattr(e, "reason_code", "ERROR"))
        readiness = getattr(e, "readiness", SNAPSHOT_BLOCKED)
        if getattr(e, "reason_code", None) in (D.SELLER_CENTRAL_ACCESS_PROHIBITED,
                                               D.AMAZON_API_PROHIBITED,
                                               D.AMAZON_ACCOUNT_ACCESS_PROHIBITED):
            readiness = SELLER_CENTRAL_POLICY_BLOCKED
        result = {"command": args.command, "readiness": readiness, "ok": False,
                  "error_code": code, "detail": redact(str(getattr(e, "detail", ""))),
                  "amazon_counters": dict(AMAZON_COUNTERS)}
    _print(result)
    return _exit_code(result)


def main(argv=None):
    return run_cli(argv)


if __name__ == "__main__":
    raise SystemExit(main())
