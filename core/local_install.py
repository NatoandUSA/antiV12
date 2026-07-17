#!/usr/bin/env python3
"""Local install / uninstall / manifest / doctor (Session 5C / Part I, J, F).

Owner-operated, current-user, no-admin, no-network. ``install_local`` prepares the
per-user directory tree, writes an install manifest and offline local config,
generates launcher wrappers, and (only when explicitly asked) creates shortcuts /
enables autostart. ``uninstall`` removes the toolkit's own launch artifacts while
PRESERVING every piece of business data. ``doctor`` is a read-only local health
report that never touches the Internet and never reveals secrets.

Business data (the repository, runs/, product facts, keyword exports, listing
packages, reports, backups, logs, owner config) is never moved or deleted here.
"""
import importlib.util
import os
import shutil
import sys

import app_paths as AP
import connectivity_config as CC
import diagnostics as D
import instance_manager as IM
import offline_bootstrap as OB
import runtime_policy as RP
import windows_integration as WI

MANIFEST_SCHEMA_VERSION = "1.0"

# Fields that legitimately change every install/health-check; excluded from the
# determinism comparison (test 12).
MANIFEST_TIMESTAMP_FIELDS = ("installed_at", "updated_at", "last_health_check")

# What uninstall may remove vs. what it must always preserve (business data).
DATA_PRESERVATION_POLICY = {
    "preserve": ["repository", "runs", "product_facts", "keyword_exports",
                 "listing_packages", "reports", "business_outputs", "owner_config",
                 "backups", "logs", "data"],
    "uninstall_removes": ["runtime_metadata", "launcher_wrappers",
                          "scheduled_task", "shortcuts", "package_temp_files"],
    "data_purge_supported": False,
}

DEFAULT_LOCAL_CONFIG = {
    "offline_only": True,
    "external_ai_enabled": False,
    "outbound_network_enabled": False,
    "bind_host": "127.0.0.1",
    "bind_port": 5000,
    "loopback_only": True,
}

# Required local modules doctor confirms are importable (no network).
_REQUIRED_MODULES = ("runtime_policy", "network_policy", "subprocess_runner",
                     "diagnostics", "app_paths", "instance_manager",
                     "windows_integration", "local_install", "page_auditor",
                     "keyword_source_adapter", "category_policy_registry",
                     "product_fact_loader")


def is_windows_supported():
    return os.name == "nt"


# ---- local config (owner-facing; backed up before replacement) ---------------
def write_local_config(env=None, config=None):
    """Write the offline local config, backing up any existing file first (Part I)."""
    path = AP.local_config_path(env)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    merged = dict(DEFAULT_LOCAL_CONFIG)
    backed_up = None
    existing = None
    try:
        existing = AP.read_json(path)
    except AP.CorruptJsonError:
        existing = None
    if os.path.exists(path):
        backed_up = _backup_file(path, env)
    if isinstance(existing, dict):
        merged.update(existing)          # owner-set values (e.g. chosen port) survive
    if isinstance(config, dict):
        merged.update(config)
    AP.write_json_atomic(path, merged)
    return {"path": path, "backed_up": backed_up, "config": merged}


def _backup_file(path, env=None):
    if not os.path.exists(path):
        return None
    dest_dir = AP.backups_dir(env)
    os.makedirs(dest_dir, exist_ok=True)
    stamp = IM._now_iso().replace(":", "").replace("-", "")
    dest = os.path.join(dest_dir, f"{os.path.basename(path)}.{stamp}.bak")
    try:
        shutil.copy2(path, dest)
        return dest
    except OSError:
        return None


# ---- installation mode -------------------------------------------------------
def detect_installation_mode(python_executable=None):
    """Best-effort, read-only classification of how amz_fbm is installed here.

    OFFLINE_SOURCE_BOOTSTRAP when the toolkit-owned ``.pth`` is present;
    PEP517_EDITABLE when a pip/PEP 517 distribution is registered; SOURCE_ON_PATH
    otherwise. No network, no subprocess for the current interpreter.
    """
    try:
        if OB.is_bootstrap_installed(python_executable):
            return OB.MODE_OFFLINE_SOURCE_BOOTSTRAP
    except Exception:
        pass
    try:
        import importlib.metadata as ilm
        ilm.distribution("amz-fbm-toolkit")
        return OB.MODE_PEP517_EDITABLE
    except Exception:
        return "SOURCE_ON_PATH"


# ---- manifest ----------------------------------------------------------------
def build_manifest(env=None, shortcuts=None, autostart_enabled=False,
                   last_health_check=None, installation_mode=None):
    now = IM._now_iso()
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "toolkit_version": IM.dashboard_version(),
        "commit": IM.git_commit(),
        "installed_at": now,
        "updated_at": now,
        "package_source": "editable-local (pip install --no-deps -e .)",
        "installation_mode": installation_mode or detect_installation_mode(),
        "executable_path": IM.preferred_python(),
        "python_path": sys.executable or "",
        "runtime_root": AP.base_dir(env),
        "project_root": IM.project_root(),
        "workspace_id": IM._workspace_id(),
        "data_preservation_policy": DATA_PRESERVATION_POLICY,
        "shortcut_paths": list(shortcuts or []),
        "scheduled_task_name": WI.TASK_NAME,
        "autostart_enabled": bool(autostart_enabled),
        "last_health_check": last_health_check,
    }


def read_manifest(env=None):
    try:
        return AP.read_json(AP.manifest_path(env))
    except AP.CorruptJsonError:
        return None


def write_manifest(manifest, env=None):
    AP.write_json_atomic(AP.manifest_path(env), manifest)


def _oplog(env, action, event, data=None):
    rec = {"ts": IM._now_iso(), "component": "install", "action": action, "event": event}
    for k, v in (data or {}).items():
        rec[k] = D.redact_secrets(v) if isinstance(v, str) else v
    AP.append_log("launcher.log", rec, env=env)


# ---- install -----------------------------------------------------------------
def install_local(env=None, shortcuts=False, autostart=False, policy=None):
    """Prepare the local install. NEVER enables autostart unless autostart=True."""
    if not is_windows_supported():
        return {"ok": False, "status": "UNSUPPORTED_PLATFORM",
                "detail": "local install targets Windows (os.name != 'nt')"}

    policy = policy if policy is not None else RP.load_runtime_policy()
    created_dirs = AP.ensure_dirs(env)
    cfg = write_local_config(env)
    # Session 6A.1 — a new installation defaults to CONNECTED_RESEARCH (open web +
    # approved research), while the permanent Amazon-account boundary stays immutable.
    # A previous connectivity config is backed up before any replacement.
    connectivity = CC.ensure_new_install_defaults(env)
    wrappers = WI.write_wrappers(env)

    shortcut_result = None
    shortcut_paths = []
    if shortcuts:
        shortcut_result = WI.create_shortcuts(env)
        shortcut_paths = shortcut_result.get("created", [])

    autostart_result = None
    if autostart:
        autostart_result = WI.enable_autostart(env)

    manifest = build_manifest(env, shortcuts=shortcut_paths,
                              autostart_enabled=bool(autostart and
                                                     (autostart_result or {}).get("ok")))
    write_manifest(manifest, env)

    report = doctor(env=env, policy=policy)
    # record the last health check timestamp if the app happened to be healthy
    if report["checks"].get("healthz", {}).get("reachable"):
        manifest["last_health_check"] = IM._now_iso()
        write_manifest(manifest, env)

    ok = policy.is_valid and report["checks"]["runtime_directories"]["all_writable"]
    _oplog(env, "install", "install_local",
           {"shortcuts": bool(shortcuts), "autostart": bool(autostart), "ok": ok})
    return {
        "ok": bool(ok),
        "status": "INSTALLED" if ok else "INSTALLED_WITH_WARNINGS",
        "runtime_root": AP.base_dir(env),
        "created_dirs": created_dirs,
        "local_config": cfg["path"],
        "config_backed_up": cfg["backed_up"],
        "connectivity_config": connectivity.get("path"),
        "wrappers": wrappers,
        "shortcuts": shortcut_result,
        "autostart": autostart_result,
        "manifest": manifest,
        "doctor": report,
    }


# ---- uninstall (data-preserving) ---------------------------------------------
def uninstall(env=None, policy=None):
    """Remove the toolkit's launch artifacts; preserve ALL business data. Idempotent."""
    policy = policy if policy is not None else RP.load_runtime_policy()
    removed, preserved, failed, manual = [], [], [], []

    # 1) stop a verified instance (never kills an unknown process)
    stop = IM.stop_instance(env=env, policy=policy)
    if stop["status"] in (IM.STOPPED_OK,):
        removed.append("running_instance (stopped)")
    elif stop["status"] in (IM.PORT_IN_USE_BY_UNKNOWN_PROCESS,
                            IM.INSTANCE_IDENTITY_UNVERIFIED):
        manual.append("an unverified process holds the port; it was NOT terminated — "
                      "review it manually before reusing the port")
    else:
        preserved.append("no running instance to stop")

    # 2) autostart — scheduled task AND current-user Startup-folder launcher
    task = WI.disable_autostart(env)
    (removed if task["ok"] else failed).append("scheduled_task:" + WI.TASK_NAME)
    if task.get("removed_startup_folder"):
        removed.append("startup_folder_autostart")
    else:
        preserved.append("no startup-folder autostart present")

    # 3) shortcuts
    sc = WI.remove_shortcuts(env)
    if sc.get("removed"):
        removed.append(f"shortcuts ({len(sc['removed'])})")
    else:
        preserved.append("no shortcuts present")

    # 4) generated wrappers
    wr = WI.remove_wrappers(env)
    removed.append(f"launcher_wrappers ({len(wr)})") if wr else preserved.append(
        "no launcher wrappers present")

    # 4b) offline-bootstrap .pth + amz-fbm.cmd (only when toolkit-owned)
    try:
        boot = OB.remove_offline_source()
        if boot.files_removed:
            removed.append(f"offline_bootstrap ({len(boot.files_removed)})")
        else:
            preserved.append("no offline-bootstrap files present")
    except Exception:
        preserved.append("offline-bootstrap removal skipped")

    # 5) runtime metadata (active + archived stale) and package temp residue
    if IM.remove_metadata(env):
        removed.append("runtime_metadata")
    for extra in ("instance.stale.json",):
        p = os.path.join(AP.runtime_dir(env), extra)
        if os.path.exists(p):
            try:
                os.remove(p)
                removed.append("runtime_metadata (stale archive)")
            except OSError:
                failed.append(extra)
    _remove_temp_residue(AP.runtime_dir(env), removed, failed)

    # 6) PRESERVE business data + owner artifacts explicitly
    for keep in ("logs", "config", "backups", "data"):
        d = AP.subdir(keep, env)
        if os.path.exists(d):
            preserved.append(f"{keep}/ (kept)")
    preserved += ["repository (kept)", "runs/ (kept)", "product facts (kept)",
                  "keyword exports (kept)", "listing packages (kept)",
                  "reports/business outputs (kept)", "owner config (kept)"]

    # 7) record the uninstall in the manifest (kept as a local record)
    manifest = read_manifest(env)
    if manifest:
        manifest["uninstalled_at"] = IM._now_iso()
        manifest["autostart_enabled"] = False
        manifest["shortcut_paths"] = []
        write_manifest(manifest, env)

    ok = not failed
    _oplog(env, "uninstall", "uninstall", {"removed": len(removed), "failed": len(failed)})
    return {"ok": ok, "status": "UNINSTALLED" if ok else "UNINSTALLED_WITH_ERRORS",
            "report": {"removed": removed, "preserved": preserved,
                       "failed": failed, "manual_cleanup": manual},
            "data_preservation_policy": DATA_PRESERVATION_POLICY}


def _remove_temp_residue(directory, removed, failed):
    if not os.path.isdir(directory):
        return
    for name in os.listdir(directory):
        if name.startswith(".tmp-"):
            p = os.path.join(directory, name)
            try:
                os.remove(p)
                removed.append("package_temp_file")
            except OSError:
                failed.append(name)


# ---- doctor (read-only, no network, no secrets) ------------------------------
def _module_present(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def doctor(env=None, policy=None):
    """Local, read-only diagnostics. No Internet, no upgrade, no system changes."""
    policy = policy if policy is not None else RP.load_runtime_policy()
    checks = {}

    checks["python"] = {"version": "%d.%d.%d" % sys.version_info[:3],
                        "executable": os.path.basename(sys.executable or "")}

    checks["package"] = {"amz_fbm_importable": _module_present("amz_fbm"),
                         "version": IM.dashboard_version()}

    checks["runtime_policy"] = {
        "valid": policy.is_valid,
        "offline_only": policy.offline_only,
        "external_ai_enabled": policy.external_ai_enabled,
        "outbound_network_enabled": policy.outbound_network_enabled,
        "loopback_host": policy.bind_host,
        "loopback_only_effective": policy.loopback_only_effective,
        "bind_port": policy.bind_port,
        "errors": list(policy.validation_errors),
    }

    # -- connectivity model + permanent Amazon-account boundary (Session 6A.1) --
    # Read-only, no network, no provider contact — reflects the persisted choice.
    try:
        cpol = CC.load_policy(env=env)
    except Exception:
        cpol = policy
    checks["connectivity"] = {
        "connectivity_mode": cpol.connectivity_mode,
        "policy_valid": cpol.is_valid,
        "public_web_research_enabled": cpol.public_web_research_enabled,
        "public_policy_research_enabled": cpol.public_policy_research_enabled,
        "amazon_public_documentation_enabled": cpol.amazon_public_documentation_enabled,
        "third_party_data_enabled": cpol.third_party_data_enabled,
        "market_data_enabled": cpol.market_data_enabled,
        "supplier_connections_enabled": cpol.supplier_connections_enabled,
        "external_ai_allowed": cpol.external_ai_allowed,
        "external_ai_enabled": cpol.external_ai_enabled,
        "external_ai_provider": cpol.external_ai_provider,
        "toolkit_update_discovery_enabled": cpol.toolkit_update_discovery_enabled,
        "deterministic_local_fallback_enabled": cpol.deterministic_local_fallback_enabled,
        "dashboard_loopback_only": cpol.dashboard_loopback_only,
        "migration_warnings": list(cpol.migration_warnings),
        "connectivity_policy_sha256": cpol.connectivity_policy_sha256,
    }
    checks["amazon_boundary"] = {
        "amazon_account_isolation": cpol.amazon_account_isolation,
        "amazon_credential_store_available": cpol.amazon_credential_store_available,
        "amazon_seller_central_enabled": cpol.amazon_seller_central_enabled,
        "amazon_authenticated_access_enabled": cpol.amazon_authenticated_access_enabled,
        "amazon_api_enabled": cpol.amazon_api_enabled,
        "amazon_browser_automation_enabled": cpol.amazon_browser_automation_enabled,
        "amazon_account_report_pull_enabled": cpol.amazon_account_report_pull_enabled,
        "amazon_network_writes_enabled": cpol.amazon_network_writes_enabled,
        "all_blocked": (cpol.amazon_account_isolation
                        and not cpol.amazon_credential_store_available
                        and not cpol.amazon_seller_central_enabled
                        and not cpol.amazon_authenticated_access_enabled
                        and not cpol.amazon_api_enabled
                        and not cpol.amazon_browser_automation_enabled
                        and not cpol.amazon_account_report_pull_enabled
                        and not cpol.amazon_network_writes_enabled),
    }

    dirs = {}
    all_writable = True
    for name in AP.SUBDIRS:
        d = AP.subdir(name, env)
        exists = os.path.isdir(d)
        writable = exists and os.access(d, os.W_OK)
        if not writable:
            all_writable = False
        dirs[name] = {"exists": exists, "writable": bool(writable)}
    checks["runtime_directories"] = {"root": AP.base_dir(env), "by_dir": dirs,
                                     "all_writable": bool(all_writable)}

    inst = IM.get_instance_status(env=env, policy=policy)
    checks["instance"] = {"status": inst["status"], "detail": inst.get("detail", ""),
                          "identity_verified": inst.get("identity_verified", False)}
    checks["port_conflict"] = {
        "conflict": inst["status"] == IM.PORT_IN_USE_BY_UNKNOWN_PROCESS,
        "bind_port": policy.bind_port}

    # loopback health probe only (never the Internet)
    health = None
    if inst["status"] in (IM.RUNNING_HEALTHY, IM.RUNNING_UNHEALTHY):
        health = IM.http_get_json(IM.health_url_for(policy), timeout=2.0)
    checks["healthz"] = {"reachable": bool(health),
                         "ok": bool(health and health.get("ok") is True)}

    checks["autostart"] = WI.autostart_status(env)
    checks["autostart_state"] = WI.autostart_state(env)
    checks["shortcuts"] = WI.shortcuts_status(env)

    # installation mode + offline-bootstrap presence (read-only, no network)
    boot = {"installed": False}
    try:
        s = OB.bootstrap_status()
        boot = {"installed": bool(s.get("installed")),
                "pth_present": bool(s.get("pth_present")),
                "pth_owned": bool(s.get("pth_owned")),
                "wrapper_present": bool(s.get("wrapper_present")),
                "wrapper_owned": bool(s.get("wrapper_owned"))}
    except Exception:
        pass
    checks["installation"] = {
        "mode": detect_installation_mode(),
        "editable_available": _module_present("amz_fbm"),
        "offline_bootstrap": boot,
        "requires_admin": False,
    }

    mods = {m: _module_present(m) for m in _REQUIRED_MODULES}
    checks["required_modules"] = {"all_present": all(mods.values()), "modules": mods}

    checks["manifest"] = {"present": read_manifest(env) is not None,
                          "path": AP.manifest_path(env)}

    ok = (policy.is_valid and all_writable and checks["required_modules"]["all_present"]
          and not checks["port_conflict"]["conflict"])
    return {"ok": bool(ok), "checks": checks,
            "summary": "healthy local install" if ok else "review flagged checks"}
