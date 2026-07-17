#!/usr/bin/env python3
"""Windows current-user integration (Session 5C / Part G, H, K).

Three things, all current-user and no-admin, all routed through the shared bounded
subprocess runner (no shell, explicit timeouts):

    autostart  — a Task Scheduler ONLOGON task with LIMITED privileges that starts
                 ONLY the local dashboard. Never SYSTEM, never HighestAvailable,
                 never machine-wide. Success is confirmed by querying and parsing
                 the task XML, not by trusting schtasks' exit code.
    shortcuts  — Desktop + Start Menu .lnk files created via WScript.Shell inside a
                 bounded PowerShell command. Idempotent and reversible.
    wrappers   — small generated PowerShell launchers under runtime\\bin.

Everything launches the toolkit the same way instance_manager does: the preferred
interpreter (pythonw when present) running ``-m amz_fbm <verb>``. No firewall
change, no service, no download, no icon fetch.
"""
import os
import xml.etree.ElementTree as ET

import app_paths as AP
import diagnostics as D
import instance_manager as IM
import subprocess_runner as SR

TASK_NAME = "AMZ-FBM-Toolkit"
START_MENU_FOLDER = "AMZ FBM Toolkit"

# Current-user Startup-folder autostart fallback (Session 5D.1 / Part A, B).
STARTUP_LAUNCHER_NAME = "AMZ-FBM-Toolkit-Startup.cmd"
_STARTUP_OWNER_MARKER = "AMZ-FBM-TOOLKIT-AUTOSTART"

# Explicit autostart methods reported to the owner / certification.
AUTOSTART_DISABLED = "DISABLED"
AUTOSTART_TASK_SCHEDULER = "TASK_SCHEDULER_CURRENT_USER"
AUTOSTART_STARTUP_FOLDER = "STARTUP_FOLDER_CURRENT_USER"
AUTOSTART_ENABLE_FAILED = "AUTOSTART_ENABLE_FAILED"
# Internal / diagnostic states.
TASK_SCHEDULER_UNAVAILABLE = "TASK_SCHEDULER_UNAVAILABLE"
TASK_SCHEDULER_ACCESS_DENIED = "TASK_SCHEDULER_ACCESS_DENIED"
AUTOSTART_CONFIGURATION_INVALID = "AUTOSTART_CONFIGURATION_INVALID"
AUTOSTART_METHODS = ("auto", "task-scheduler", "startup-folder")

# Shortcut label -> CLI verb. Desktop gets "Open" only; Start Menu gets all three.
_SHORTCUTS = [("Open AMZ FBM Toolkit", "open"),
              ("Start AMZ FBM Toolkit", "start"),
              ("Stop AMZ FBM Toolkit", "stop")]
_DESKTOP_SHORTCUT = "Open AMZ FBM Toolkit"

_SYSTEM_SIDS = {"S-1-5-18", "S-1-5-19", "S-1-5-20", "SYSTEM",
                "LOCALSYSTEM", "NT AUTHORITY\\SYSTEM"}


# ---- small shared runners -----------------------------------------------------
def _ps(script, stage="powershell", timeout=25):
    """Run a bounded, non-interactive PowerShell -Command. No shell, no profile."""
    return SR.run_subprocess(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout_seconds=timeout, stage_name=stage,
        allowed_exit_codes=tuple(range(0, 256)))


def _schtasks(args, stage, timeout=25, allowed=(0,)):
    return SR.run_subprocess(["schtasks"] + list(args), timeout_seconds=timeout,
                             stage_name=stage, allowed_exit_codes=allowed)


def _ps_quote(s):
    """Embed a path/string as a PowerShell single-quoted literal (space + quote safe)."""
    return "'" + str(s).replace("'", "''") + "'"


def _oplog(env, action, event, data=None):
    rec = {"ts": IM._now_iso(), "component": "windows", "action": action, "event": event}
    for k, v in (data or {}).items():
        rec[k] = D.redact_secrets(v) if isinstance(v, str) else v
    AP.append_log("launcher.log", rec, env=env)


def launcher_target():
    """(executable, args) that starts the dashboard with no console window."""
    return IM.preferred_python(), "-m amz_fbm start"


# ==============================================================================
# Autostart — current-user Task Scheduler (ONLOGON, LIMITED)
# ==============================================================================
def _task_run_string(verb="start"):
    exe = IM.preferred_python()
    return f'"{exe}" -m amz_fbm {verb}'


def _classify_task_failure(result):
    """Map a failed schtasks /Create to a stable diagnostic code (never elevate)."""
    text = ((getattr(result, "stderr", "") or "") + " "
            + (getattr(result, "stdout", "") or "")).lower()
    if "access is denied" in text or "access denied" in text:
        return TASK_SCHEDULER_ACCESS_DENIED
    return TASK_SCHEDULER_UNAVAILABLE


def _enable_task_scheduler(env=None):
    """Attempt the current-user ONLOGON/LIMITED task and verify it by query.

    Never elevates, never retries as administrator. Returns a structured result;
    ``failure`` is a stable code when creation is denied/unavailable.
    """
    tr = _task_run_string("start")
    r = _schtasks(["/Create", "/TN", TASK_NAME, "/TR", tr, "/SC", "ONLOGON",
                   "/RL", "LIMITED", "/F"], stage="autostart-enable",
                  allowed=tuple(range(0, 256)))
    if not r.success or r.exit_code != 0:
        failure = _classify_task_failure(r)
        return {"ok": False, "created": False, "verified": False, "failure": failure,
                "last_error_code": failure, "detail": r.diagnostic_summary,
                "stderr": (r.stderr or r.stdout or "").strip()[:300]}
    status = autostart_status(env)
    ok = status["valid"]
    return {"ok": ok, "created": True, "verified": status["valid"],
            "failure": None if ok else AUTOSTART_CONFIGURATION_INVALID,
            "last_error_code": None if ok else AUTOSTART_CONFIGURATION_INVALID,
            "status": status,
            "detail": "task created and verified" if ok else
            "task created but failed current-user/limited verification"}


def enable_autostart(env=None, method="auto", startup_dir=None):
    """Enable current-user login autostart. Never requires elevation.

    method="auto" (default) attempts Task Scheduler and, on a verified access-denied /
    unavailable result, falls back to the current-user Startup folder — reported
    honestly as STARTUP_FOLDER_CURRENT_USER, never as a hidden Task Scheduler success.
    Explicit "task-scheduler" fails honestly when unavailable; explicit
    "startup-folder" creates the fallback directly.
    """
    method = (method or "auto").lower()
    if method not in AUTOSTART_METHODS:
        return {"ok": False, "method": AUTOSTART_CONFIGURATION_INVALID,
                "requested_method": method, "verified": False, "requires_admin": False,
                "detail": "unknown autostart method: %s" % method}

    if method == "startup-folder":
        sf = enable_startup_folder(env, startup_dir=startup_dir)
        _oplog(env, "autostart", "enable", {"method": "startup-folder", "ok": sf["ok"]})
        return {"ok": sf["ok"],
                "method": AUTOSTART_STARTUP_FOLDER if sf["ok"] else AUTOSTART_ENABLE_FAILED,
                "requested_method": method, "created": sf["ok"], "verified": sf["ok"],
                "task_scheduler": None, "startup_folder": sf, "requires_admin": False,
                "detail": sf["detail"]}

    if method == "task-scheduler":
        ts = _enable_task_scheduler(env)
        _oplog(env, "autostart", "enable", {"method": "task-scheduler", "ok": ts["ok"]})
        return {"ok": ts["ok"],
                "method": AUTOSTART_TASK_SCHEDULER if ts["ok"] else AUTOSTART_ENABLE_FAILED,
                "requested_method": method, "created": ts["created"],
                "verified": ts["verified"], "task_scheduler": ts, "startup_folder": None,
                "task_name": TASK_NAME, "requires_admin": False, "detail": ts["detail"]}

    # method == "auto"
    ts = _enable_task_scheduler(env)
    if ts["ok"]:
        _oplog(env, "autostart", "enable",
               {"method": "task-scheduler", "ok": True, "fallback": False})
        return {"ok": True, "method": AUTOSTART_TASK_SCHEDULER, "requested_method": "auto",
                "created": True, "verified": True, "task_scheduler": ts,
                "startup_folder": None, "task_name": TASK_NAME, "requires_admin": False,
                "detail": "task created and verified"}
    if ts["created"]:
        # created but did not verify current-user/limited — surface, do NOT fall back.
        _oplog(env, "autostart", "enable",
               {"method": "task-scheduler", "ok": False, "verified": False})
        return {"ok": False, "method": AUTOSTART_CONFIGURATION_INVALID,
                "requested_method": "auto", "created": True, "verified": False,
                "task_scheduler": ts, "startup_folder": None, "requires_admin": False,
                "detail": ts["detail"]}
    # creation failed (access denied / unavailable) — fall back to the Startup folder.
    sf = enable_startup_folder(env, startup_dir=startup_dir)
    if sf["ok"]:
        warning = ("Task Scheduler unavailable (%s); using the current-user Startup "
                   "folder fallback — no elevation." % ts.get("failure"))
        _oplog(env, "autostart", "enable",
               {"method": "startup-folder", "ok": True, "fallback": True,
                "task_failure": ts.get("failure")})
        return {"ok": True, "method": AUTOSTART_STARTUP_FOLDER, "requested_method": "auto",
                "created": True, "verified": True, "task_scheduler": ts,
                "startup_folder": sf, "requires_admin": False, "warning": warning,
                "detail": "startup-folder fallback active"}
    _oplog(env, "autostart", "enable",
           {"method": "none", "ok": False, "task_failure": ts.get("failure")})
    return {"ok": False, "method": AUTOSTART_ENABLE_FAILED, "requested_method": "auto",
            "created": False, "verified": False, "task_scheduler": ts,
            "startup_folder": sf, "requires_admin": False,
            "detail": "both Task Scheduler and Startup-folder autostart failed"}


def disable_autostart(env=None, startup_dir=None):
    """Remove EVERY toolkit-owned autostart method (task + Startup folder). Idempotent.

    Never removes unrelated scheduled tasks or unrelated Startup-folder entries.
    """
    _schtasks(["/Delete", "/TN", TASK_NAME, "/F"], stage="autostart-disable",
              allowed=tuple(range(0, 256)))
    sf = disable_startup_folder(env, startup_dir=startup_dir)
    state = autostart_state(env, startup_dir=startup_dir)
    ok = not state["enabled"]
    _oplog(env, "autostart", "disable",
           {"removed": ok, "removed_startup_folder": sf["removed"]})
    return {"ok": ok, "task_name": TASK_NAME, "installed": state["enabled"],
            "method": state["method"], "removed_startup_folder": sf["removed"],
            "detail": "autostart removed" if ok else "an autostart method is still present"}


def _strip_ns(tag):
    return tag.rsplit("}", 1)[-1]


def _parse_task_xml(xml_text):
    """Extract the safety-relevant fields from schtasks /XML output."""
    out = {"onlogon": False, "run_level": None, "user_id": None, "logon_type": None}
    try:
        # schtasks may prefix a BOM; ElementTree wants a clean string
        root = ET.fromstring(xml_text.lstrip("﻿"))
    except ET.ParseError:
        return out
    for el in root.iter():
        tag = _strip_ns(el.tag)
        if tag == "LogonTrigger":
            out["onlogon"] = True
        elif tag == "RunLevel" and el.text:
            out["run_level"] = el.text.strip()
        elif tag == "UserId" and el.text and not out["user_id"]:
            out["user_id"] = el.text.strip()
        elif tag == "LogonType" and el.text:
            out["logon_type"] = el.text.strip()
    return out


def autostart_status(env=None):
    """Query the task and VERIFY current-user + limited + onlogon (never SYSTEM/highest)."""
    r = _schtasks(["/Query", "/TN", TASK_NAME, "/XML"], stage="autostart-status",
                  timeout=20, allowed=tuple(range(0, 256)))
    if not r.success or not (r.stdout or "").strip():
        return {"installed": False, "onlogon": False, "limited": False,
                "current_user": False, "system": False, "highest_privilege": False,
                "valid": False, "task_name": TASK_NAME}
    fields = _parse_task_xml(r.stdout)
    user_id = (fields["user_id"] or "").upper()
    system = user_id in {s.upper() for s in _SYSTEM_SIDS}
    highest = (fields["run_level"] or "").lower() == "highestavailable"
    limited = not highest                         # default/LeastPrivilege == limited
    ident = _current_identity()
    current_user = (not system) and (
        user_id in {v.upper() for v in ident.values() if v} or
        (fields["logon_type"] or "").lower() in ("interactivetoken", "s4u", "password",
                                                 "interactive"))
    valid = fields["onlogon"] and limited and current_user and not system and not highest
    return {"installed": True, "onlogon": fields["onlogon"], "limited": limited,
            "current_user": current_user, "system": system,
            "highest_privilege": highest, "run_level": fields["run_level"],
            "valid": valid, "task_name": TASK_NAME}


_IDENTITY_CACHE = {}


def _current_identity():
    if _IDENTITY_CACHE:
        return _IDENTITY_CACHE
    ident = {"user": os.environ.get("USERNAME", ""),
             "domain_user": (os.environ.get("USERDOMAIN", "") + "\\" +
                             os.environ.get("USERNAME", "")).strip("\\"),
             "sid": ""}
    r = SR.run_subprocess(["whoami", "/user", "/fo", "csv", "/nh"],
                          timeout_seconds=10, stage_name="whoami",
                          allowed_exit_codes=tuple(range(0, 256)))
    if r.success and r.stdout:
        parts = [p.strip().strip('"') for p in r.stdout.strip().splitlines()[-1].split(",")]
        if len(parts) >= 2:
            ident["sid"] = parts[-1]
    _IDENTITY_CACHE.update(ident)
    return _IDENTITY_CACHE


# ==============================================================================
# Autostart fallback — current-user Startup folder (no admin, no elevation)
# ==============================================================================
def startup_folder():
    """The current-user Startup folder (a real OS path, resolved from os.environ).

        %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup
    """
    appdata = os.environ.get("APPDATA")
    if not appdata or not appdata.strip():
        appdata = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs",
                        "Startup")


def _startup_launcher_body():
    """A current-user .cmd that starts ONLY the dashboard. No browser, no secrets.

    Uses the same interpreter (pythonw when present) and ``-m amz_fbm start`` as every
    other launcher, so single-instance handling prevents a duplicate at login.
    """
    exe = IM.preferred_python()
    return (
        "@echo off\r\n"
        "REM %s do-not-edit\r\n" % _STARTUP_OWNER_MARKER +
        "REM AMZ FBM Toolkit login autostart (current-user, offline, loopback-only).\r\n"
        "REM Starts only the local dashboard. No browser is opened. Contains no secrets.\r\n"
        '"%s" -m amz_fbm start\r\n' % exe
    )


def _startup_launcher_path(startup_dir=None):
    return os.path.join(startup_dir or startup_folder(), STARTUP_LAUNCHER_NAME)


def enable_startup_folder(env=None, startup_dir=None):
    """Create + verify the current-user Startup-folder launcher (idempotent)."""
    path = _startup_launcher_path(startup_dir)
    body = _startup_launcher_body()
    d = os.path.dirname(path)
    try:
        os.makedirs(d, exist_ok=True)
        # never overwrite a same-named file the toolkit does not own
        if os.path.exists(path) and _STARTUP_OWNER_MARKER not in (_read_text(path) or ""):
            return {"ok": False, "created": False, "path": path,
                    "detail": "refusing to overwrite a non-toolkit-owned Startup file"}
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(body)
        os.replace(tmp, path)
    except OSError as e:
        return {"ok": False, "created": False, "path": path,
                "detail": "could not write Startup launcher: %s" % type(e).__name__}
    status = startup_folder_status(env, startup_dir=startup_dir)
    ok = status["present"] and status["verified"]
    _oplog(env, "autostart", "startup_folder_enable", {"ok": ok})
    return {"ok": ok, "created": True, "verified": status["verified"], "path": path,
            "status": status, "detail": "startup launcher created and verified" if ok
            else "startup launcher created but failed verification"}


def _read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            return f.read()
    except OSError:
        return None


def startup_folder_status(env=None, startup_dir=None):
    """Verify the Startup launcher: owned, invokes the dashboard, no browser/URL."""
    path = _startup_launcher_path(startup_dir)
    if not os.path.exists(path):
        return {"present": False, "verified": False, "owned": False, "path": path}
    content = _read_text(path) or ""
    low = content.lower()
    owned = _STARTUP_OWNER_MARKER in content
    invokes_dashboard = "-m amz_fbm start" in content
    no_browser = "--open" not in content
    no_external_url = "http://" not in low and "https://" not in low
    verified = owned and invokes_dashboard and no_browser and no_external_url
    return {"present": True, "verified": verified, "owned": owned,
            "invokes_dashboard": invokes_dashboard, "no_browser": no_browser,
            "no_external_url": no_external_url, "path": path}


def disable_startup_folder(env=None, startup_dir=None):
    """Remove ONLY the toolkit-owned Startup launcher. Idempotent. Never unrelated files."""
    path = _startup_launcher_path(startup_dir)
    removed = False
    if os.path.exists(path):
        if _STARTUP_OWNER_MARKER in (_read_text(path) or ""):
            try:
                os.remove(path)
                removed = True
            except OSError:
                pass
    _oplog(env, "autostart", "startup_folder_disable", {"removed": removed})
    return {"ok": True, "removed": removed, "path": path}


def autostart_state(env=None, startup_dir=None):
    """Combined autostart state: the actual active method across both mechanisms."""
    task = autostart_status(env)
    sf = startup_folder_status(env, startup_dir=startup_dir)
    task_valid = bool(task.get("valid"))
    if task.get("installed") and task_valid:
        method, enabled = AUTOSTART_TASK_SCHEDULER, True
    elif sf["present"] and sf["verified"]:
        method, enabled = AUTOSTART_STARTUP_FOLDER, True
    elif task.get("installed") or sf["present"]:
        method, enabled = AUTOSTART_CONFIGURATION_INVALID, False
    else:
        method, enabled = AUTOSTART_DISABLED, False
    return {
        "ok": True,
        "enabled": enabled,
        "method": method,
        "task_scheduler": {"present": bool(task.get("installed")), "available": task_valid,
                           "valid": task_valid, "system": bool(task.get("system")),
                           "highest_privilege": bool(task.get("highest_privilege"))},
        "startup_folder": {"present": sf["present"], "verified": sf["verified"]},
        "requires_admin": False,
    }


# ==============================================================================
# Shortcuts — Desktop + Start Menu (WScript.Shell via PowerShell)
# ==============================================================================
def _location_lines(desktop_dir, start_menu_dir):
    """PowerShell assignments for $desk and $folder (current-user by default).

    Overrides (used by bounded live tests) point at a temp directory so a test can
    exercise real .lnk creation without touching the owner's Desktop/Start Menu.
    """
    desk = _ps_quote(desktop_dir) if desktop_dir else "[Environment]::GetFolderPath('Desktop')"
    if start_menu_dir:
        folder = _ps_quote(start_menu_dir)
    else:
        folder = (f"(Join-Path [Environment]::GetFolderPath('Programs') "
                  f"{_ps_quote(START_MENU_FOLDER)})")
    return [f"$desk = {desk}", f"$folder = {folder}"]


def create_shortcuts(env=None, desktop=True, start_menu=True,
                     desktop_dir=None, start_menu_dir=None):
    """Create current-user Desktop + Start Menu shortcuts. Idempotent (Save overwrites)."""
    exe, root = IM.preferred_python(), IM.project_root()
    lines = ["$ws = New-Object -ComObject WScript.Shell"]
    lines += _location_lines(desktop_dir, start_menu_dir)
    lines += ["New-Item -ItemType Directory -Force -Path $folder | Out-Null",
              "$made = @()"]

    def _mk(dir_expr, label, verb):
        target = f"Join-Path {dir_expr} {_ps_quote(label + '.lnk')}"
        return [
            f"$lnk = $ws.CreateShortcut(({target}))",
            f"$lnk.TargetPath = {_ps_quote(exe)}",
            f"$lnk.Arguments = {_ps_quote('-m amz_fbm ' + verb)}",
            f"$lnk.WorkingDirectory = {_ps_quote(root)}",
            f"$lnk.Description = {_ps_quote(label + ' (localhost, offline)')}",
            "$lnk.Save()",
            f"$made += ({target})",
        ]

    if start_menu:
        for label, verb in _SHORTCUTS:
            lines += _mk("$folder", label, verb)
    if desktop:
        lines += _mk("$desk", _DESKTOP_SHORTCUT, "open")
    lines.append("$made -join [Environment]::NewLine")
    r = _ps("\n".join(lines), stage="shortcuts-create", timeout=30)
    created = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip().endswith(".lnk")]
    _oplog(env, "shortcuts", "create", {"count": len(created), "ok": r.success})
    return {"ok": r.success and bool(created), "created": created,
            "detail": r.diagnostic_summary}


def remove_shortcuts(env=None, desktop_dir=None, start_menu_dir=None):
    """Remove the shortcuts + (empty) Start Menu folder. Idempotent."""
    labels = [lbl for lbl, _ in _SHORTCUTS]
    quoted = ", ".join(_ps_quote(l + ".lnk") for l in labels)
    lines = _location_lines(desktop_dir, start_menu_dir) + [
        "$removed = @()",
        f"foreach ($n in @({quoted})) {{",
        "  $p = Join-Path $folder $n",
        "  if (Test-Path $p) { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue; $removed += $p }",
        "}",
        f"$dp = Join-Path $desk {_ps_quote(_DESKTOP_SHORTCUT + '.lnk')}",
        "if (Test-Path $dp) { Remove-Item -LiteralPath $dp -Force -ErrorAction SilentlyContinue; $removed += $dp }",
        "if ((Test-Path $folder) -and -not (Get-ChildItem -LiteralPath $folder -Force)) { Remove-Item -LiteralPath $folder -Force -ErrorAction SilentlyContinue }",
        "$removed -join [Environment]::NewLine",
    ]
    r = _ps("\n".join(lines), stage="shortcuts-remove", timeout=25)
    removed = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip().endswith(".lnk")]
    _oplog(env, "shortcuts", "remove", {"count": len(removed), "ok": r.success})
    return {"ok": r.success, "removed": removed, "detail": r.diagnostic_summary}


def shortcuts_status(env=None, desktop_dir=None, start_menu_dir=None):
    """Report which shortcuts currently exist (current-user paths only)."""
    labels = [lbl for lbl, _ in _SHORTCUTS]
    quoted = ", ".join(_ps_quote(l + ".lnk") for l in labels)
    lines = _location_lines(desktop_dir, start_menu_dir) + [
        "$present = @()",
        f"foreach ($n in @({quoted})) {{ if (Test-Path (Join-Path $folder $n)) {{ $present += $n }} }}",
        f"$dp = Join-Path $desk {_ps_quote(_DESKTOP_SHORTCUT + '.lnk')}",
        "if (Test-Path $dp) { $present += 'DESKTOP' }",
        "$present -join ','",
    ]
    r = _ps("\n".join(lines), stage="shortcuts-status", timeout=20)
    present = [p for p in (r.stdout or "").strip().split(",") if p]
    return {"start_menu": [p for p in present if p != "DESKTOP"],
            "desktop": "DESKTOP" in present,
            "installed": bool(present), "folder": START_MENU_FOLDER}


# ==============================================================================
# Generated PowerShell launcher wrappers (Part K)
# ==============================================================================
def write_wrappers(env=None):
    """Write stable Start/Stop/Open wrappers under runtime\\bin. No secrets, no policy."""
    exe = IM.preferred_python()
    bind = AP.bin_dir(env)
    os.makedirs(bind, exist_ok=True)
    written = []
    for name, verb in (("Start-AMZ-FBM.ps1", "start"),
                       ("Stop-AMZ-FBM.ps1", "stop"),
                       ("Open-AMZ-FBM.ps1", "open")):
        body = (
            "# Generated by AMZ FBM Toolkit install-local. Do not edit.\n"
            "# Local, offline, loopback-only launcher. Contains no secrets.\n"
            f"& {_ps_quote(exe)} -m amz_fbm {verb} @args\n"
            "exit $LASTEXITCODE\n"
        )
        path = os.path.join(bind, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        written.append(path)
    _oplog(env, "wrappers", "write", {"count": len(written)})
    return written


def remove_wrappers(env=None):
    bind = AP.bin_dir(env)
    removed = []
    for name in ("Start-AMZ-FBM.ps1", "Stop-AMZ-FBM.ps1", "Open-AMZ-FBM.ps1"):
        path = os.path.join(bind, name)
        try:
            if os.path.exists(path):
                os.remove(path)
                removed.append(path)
        except OSError:
            pass
    return removed
