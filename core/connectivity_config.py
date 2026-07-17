#!/usr/bin/env python3
"""Owner-facing connectivity configuration file (Session 6A.1).

A small, current-user JSON file — ``config/connectivity-policy.json`` — that persists
the owner's connectivity choice. It carries ONLY the connectivity mode and the
connected-research capability toggles. It never contains a secret, an API key, a
password, a cookie, a token, or any Amazon seller/credential field — those fields do
not exist in the toolkit at all, and any that appear in an inbound dict are dropped.

Writes are atomic (temp file + os.replace) and the previous valid file is backed up
before replacement, so an owner's configuration is never lost on migration.
"""
import os

import app_paths as AP
import instance_manager as IM   # for a timestamp helper only
import runtime_policy as RP

SCHEMA_VERSION = "connectivity-policy-v1"
CONFIG_FILENAME = "connectivity-policy.json"

# The exact fields this file may hold. Anything else (especially credential/Amazon
# fields) is refused: it is not written and it is not read back.
ALLOWED_FIELDS = (
    "schema_version",
    "connectivity_mode",
    "public_web_research_enabled",
    "public_policy_research_enabled",
    "amazon_public_documentation_enabled",
    "third_party_data_enabled",
    "market_data_enabled",
    "supplier_connections_enabled",
    "external_ai_allowed",
    "external_ai_enabled",
    "external_ai_provider",
    "toolkit_update_discovery_enabled",
    "deterministic_local_fallback_enabled",
)

# Fields that must NEVER appear in this file (defensive; they have no home in the tool).
# The ALLOWED_FIELDS whitelist is the primary guard; this is belt-and-suspenders.
FORBIDDEN_SUBSTRINGS = ("password", "cookie", "token", "secret", "api_key", "apikey",
                        "credential", "seller", "refresh", "session", "browser_profile",
                        "advertising")

NEW_INSTALL_DEFAULTS = {
    "schema_version": SCHEMA_VERSION,
    "connectivity_mode": RP.CONNECTED_RESEARCH,
    "public_web_research_enabled": True,
    "public_policy_research_enabled": True,
    "amazon_public_documentation_enabled": False,
    "third_party_data_enabled": True,
    "market_data_enabled": True,
    "supplier_connections_enabled": True,
    "external_ai_allowed": True,
    "external_ai_enabled": False,
    "external_ai_provider": None,
    "toolkit_update_discovery_enabled": True,
    "deterministic_local_fallback_enabled": True,
}


def connectivity_config_path(env=None):
    return os.path.join(AP.config_dir(env), CONFIG_FILENAME)


def _is_forbidden(key):
    k = str(key).lower()
    return any(sub in k for sub in FORBIDDEN_SUBSTRINGS)


def sanitize(config):
    """Return a copy with only ALLOWED_FIELDS, dropping any forbidden/secret field."""
    clean = {}
    for k, v in (config or {}).items():
        if k in ALLOWED_FIELDS and not _is_forbidden(k):
            clean[k] = v
    clean["schema_version"] = SCHEMA_VERSION
    return clean


def read_connectivity_config(env=None):
    """Return the sanitized persisted connectivity config, or None when absent."""
    path = connectivity_config_path(env)
    try:
        data = AP.read_json(path)
    except AP.CorruptJsonError:
        return None
    if not isinstance(data, dict):
        return None
    return sanitize(data)


def _backup_existing(path, env=None):
    if not os.path.exists(path):
        return None
    dest_dir = AP.backups_dir(env)
    os.makedirs(dest_dir, exist_ok=True)
    stamp = IM._now_iso().replace(":", "").replace("-", "")
    dest = os.path.join(dest_dir, f"{CONFIG_FILENAME}.{stamp}.bak")
    try:
        import shutil
        shutil.copy2(path, dest)
        return dest
    except OSError:
        return None


def write_connectivity_config(config, env=None):
    """Atomically write a sanitized connectivity config, backing up any prior file."""
    path = connectivity_config_path(env)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    backed_up = _backup_existing(path, env)
    merged = dict(NEW_INSTALL_DEFAULTS)
    merged.update(sanitize(config or {}))
    merged["schema_version"] = SCHEMA_VERSION
    AP.write_json_atomic(path, merged)
    return {"path": path, "backed_up": backed_up, "config": merged}


def ensure_new_install_defaults(env=None):
    """Create connectivity-policy.json with CONNECTED_RESEARCH defaults if absent."""
    if read_connectivity_config(env) is not None:
        return {"created": False, "path": connectivity_config_path(env)}
    res = write_connectivity_config(dict(NEW_INSTALL_DEFAULTS), env)
    return {"created": True, **res}


def migrate_from_legacy(env=None, legacy_config_path=None):
    """One-time migration from the old offline-only local config.

    If no connectivity-policy.json exists yet, derive the mode from a legacy
    ``local-config.json`` (OFFLINE_ONLY -> LOCAL_SAFE / CONNECTED_RESEARCH), back up the
    previous connectivity file (none yet on first run), and write the new file. Returns
    a record describing what happened. Never touches business data.
    """
    if read_connectivity_config(env) is not None:
        return {"migrated": False, "reason": "connectivity config already present",
                "path": connectivity_config_path(env)}
    legacy = None
    lpath = legacy_config_path or AP.local_config_path(env)
    try:
        legacy = AP.read_json(lpath)
    except AP.CorruptJsonError:
        legacy = None
    mode = RP.NEW_INSTALL_CONNECTIVITY_MODE
    warnings = []
    if isinstance(legacy, dict) and "offline_only" in legacy:
        offline = bool(legacy.get("offline_only"))
        mode = RP.LOCAL_SAFE if offline else RP.CONNECTED_RESEARCH
        warnings.append(f"migrated legacy offline_only={offline} -> connectivity_mode={mode}")
    cfg = dict(NEW_INSTALL_DEFAULTS)
    cfg["connectivity_mode"] = mode
    if mode != RP.CONNECTED_RESEARCH:
        for k in ("public_web_research_enabled", "public_policy_research_enabled",
                  "third_party_data_enabled", "market_data_enabled",
                  "supplier_connections_enabled", "toolkit_update_discovery_enabled",
                  "external_ai_allowed"):
            cfg[k] = False
    res = write_connectivity_config(cfg, env)
    return {"migrated": True, "mode": mode, "warnings": warnings, **res}


def load_policy(env=None, config_path=None):
    """Load a RuntimePolicy honoring the persisted connectivity config (if any)."""
    conn = read_connectivity_config(env)
    return RP.load_runtime_policy(env=env, config_path=config_path,
                                  connectivity_config=conn)
