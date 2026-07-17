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


def enable_autostart(env=None):
    """Create/replace the current-user ONLOGON task, then verify it by query."""
    tr = _task_run_string("start")
    r = _schtasks(["/Create", "/TN", TASK_NAME, "/TR", tr, "/SC", "ONLOGON",
                   "/RL", "LIMITED", "/F"], stage="autostart-enable", allowed=(0,))
    status = autostart_status(env)
    ok = bool(r.success) and status["valid"]
    _oplog(env, "autostart", "enable", {"created": r.success, "valid": status["valid"]})
    return {"ok": ok, "created": r.success, "verified": status["valid"],
            "task_name": TASK_NAME, "status": status,
            "detail": r.diagnostic_summary if not r.success else "task created and verified"}


def disable_autostart(env=None):
    """Delete the task. Idempotent — a missing task is treated as success."""
    r = _schtasks(["/Delete", "/TN", TASK_NAME, "/F"], stage="autostart-disable",
                  allowed=tuple(range(0, 256)))
    status = autostart_status(env)
    ok = not status["installed"]
    _oplog(env, "autostart", "disable", {"removed": ok})
    return {"ok": ok, "task_name": TASK_NAME, "installed": status["installed"],
            "detail": "task removed" if ok else "task still present"}


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
