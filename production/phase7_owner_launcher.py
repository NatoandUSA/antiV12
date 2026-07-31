#!/usr/bin/env python3
"""Phase 7.14 — Launcher Lite for the accepted Phase 7.13 Unified Owner Console.

The owner double-clicks one file, waits, and the browser opens on a console that is already healthy.
Nothing else. This module is the safety layer behind Start / Stop / Open:

  start  — preflight, take an exclusive lock, refuse to duplicate a healthy console, spawn ONE fixed
           command, record the PID with a process-identity token, poll the accepted /api/v1/health
           endpoint under a bounded timeout, and open the browser only once health reports ready.
  stop   — read only the launcher-owned PID record, prove the recorded process is still that exact
           process, ask it to stop gracefully, wait a bounded time, and report the outcome.
  open   — check health; open the browser only when healthy; otherwise say exactly what to run.

Hard safety rules, all enforced in code and asserted by the Phase 7.14 tests:

  * the ONLY command this module may ever spawn is the fixed accepted console command below;
  * a shell is never invoked, and no argument is ever assembled from owner input;
  * no process-tree kill utility, no process-name matching, no "stop every interpreter on this
    machine" — a process is signalled only after its recorded PID *and* its recorded process-start
    token both still match;
  * the bind host is validated as loopback and the browser URL is validated against a strict pattern,
    so no remote server, no LAN address and no arbitrary URL can ever be launched or opened;
  * the launcher log is bounded and secret-free: no environment value, cookie, CSRF token,
    confirmation token, Authorization header or absolute path is ever written to it;
  * nothing here registers a service, a scheduler or a Windows startup entry.

Permanent boundary: this launcher starts a LOCAL read-first console. It never connects to Amazon
Seller Central, never uses a seller sign-in or seller credentials, never calls a seller or advertising
API, never downloads a seller report and never drives a seller browser. The owner remains the only
manual bridge to Amazon.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ("", "core", "production"):
    _p = _ROOT if not _sub else os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ================================================================ identity
STAGE_ID = "7.14"
STAGE_NAME = "Owner Launcher Lite"
LAUNCHER_SCHEMA = "phase7-14-launcher-result-v1"
PID_SCHEMA = "phase7-14-launcher-pid-v1"

# ---------------------------------------------------------------- readiness states
LAUNCHER_READY = "SESSION7_14_LAUNCHER_READY"
LAUNCHER_ALREADY_RUNNING = "SESSION7_14_LAUNCHER_ALREADY_RUNNING"
LAUNCHER_STARTING = "SESSION7_14_LAUNCHER_STARTING"
LAUNCHER_TIMEOUT = "SESSION7_14_LAUNCHER_TIMEOUT"
LAUNCHER_PORT_BLOCKED = "SESSION7_14_LAUNCHER_PORT_BLOCKED"
LAUNCHER_PYTHON_REQUIRED = "SESSION7_14_LAUNCHER_PYTHON_REQUIRED"
LAUNCHER_STOPPED = "SESSION7_14_LAUNCHER_STOPPED"
LAUNCHER_ALREADY_STOPPED = "SESSION7_14_LAUNCHER_ALREADY_STOPPED"
LAUNCHER_STOP_REFUSED = "SESSION7_14_LAUNCHER_STOP_REFUSED"
LAUNCHER_LOCKED = "SESSION7_14_LAUNCHER_LOCKED"
LAUNCHER_REPOSITORY_REQUIRED = "SESSION7_14_LAUNCHER_REPOSITORY_REQUIRED"
LAUNCHER_MODULE_REQUIRED = "SESSION7_14_LAUNCHER_MODULE_REQUIRED"
LAUNCHER_WORKSPACE_REQUIRED = "SESSION7_14_LAUNCHER_WORKSPACE_REQUIRED"
LAUNCHER_NOT_RUNNING = "SESSION7_14_LAUNCHER_NOT_RUNNING"
LAUNCHER_BROWSER_UNAVAILABLE = "SESSION7_14_LAUNCHER_BROWSER_UNAVAILABLE"
LAUNCHER_FAILED = "SESSION7_14_LAUNCHER_FAILED"

# ---------------------------------------------------------------- proven process exit states
# What the launcher can actually PROVE about the process it asked to stop. "Not exited" and "still
# running" are different claims, and only one of them may be reported as a running console.
EXIT_STATE_EXITED = "EXITED"          # proven gone: the kernel reported a real exit
EXIT_STATE_RUNNING = "RUNNING"        # proven alive: still executing
EXIT_STATE_UNPROVEN = "UNPROVEN"      # neither could be proven — fail closed, never claim success

# The six distinct stop situations the owner's record must be able to tell apart. They map onto the
# three owner-facing sentences below; the machine record keeps the full resolution.
STOP_STATE_EXITED = "PROCESS_EXITED"
STOP_STATE_EXITED_STALE_STATE = "PROCESS_EXITED_RUNTIME_STATE_STALE"
STOP_STATE_ALIVE = "PROCESS_STILL_ALIVE"
STOP_STATE_PORT_CLOSED_ALIVE = "PORT_CLOSED_PROCESS_ALIVE"
STOP_STATE_TERMINATE_FAILED = "TERMINATION_REQUEST_FAILED"
STOP_STATE_UNPROVEN = "PROCESS_STATE_UNPROVEN"

# ---------------------------------------------------------------- pilot readiness of this checkout
PILOT_READY = "SESSION7_14_PILOT_READY"
PILOT_REQUIRED = "SESSION7_14_PILOT_REQUIRED"

# The documents an owner needs in hand before a real 14-day pilot can start.
PILOT_DOCUMENTS = (
    "docs/PHASE7_14-OWNER-USABILITY-POLICY.md",
    "docs/PHASE7_14-OWNER-PILOT-GUIDE.md",
    "docs/PHASE7_14-OWNER-PILOT-CHECKLIST.md",
    "docs/PHASE7_14-PILOT-ISSUE-TEMPLATE.md",
    "docs/PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md",
    "docs/PHASE7_14-PILOT-EXIT-CRITERIA.md",
)
LAUNCHER_SCRIPTS = (
    "Start-AMZ-Toolkit.bat", "Start-AMZ-Toolkit.ps1", "Stop-AMZ-Toolkit.bat",
    "Stop-AMZ-Toolkit.ps1", "Open-AMZ-Toolkit.bat", "Open-AMZ-Toolkit.ps1",
)

_OK_STATES = (LAUNCHER_READY, LAUNCHER_ALREADY_RUNNING, LAUNCHER_STOPPED, LAUNCHER_ALREADY_STOPPED,
              LAUNCHER_BROWSER_UNAVAILABLE)

# ---------------------------------------------------------------- fixed configuration
CONSOLE_MODULE = "production.phase7_unified_owner_console"
CONSOLE_SOURCE = os.path.join("production", "phase7_unified_owner_console.py")
CONSOLE_STATIC = os.path.join("production", "phase7_unified_owner_console_static")
CONSOLE_STATIC_FILES = ("index.html", "app.js", "styles.css", "icons.svg")
CONSOLE_COMMAND_VERB = "serve"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8780
DEFAULT_WORKSPACE_ROOT = "runs/T2/phase7"
LAUNCHER_SUBDIR = os.path.join("runs", "T2", "phase7", "7.14", "launcher")

HEALTH_PATH = "/api/v1/health"
EXPECTED_STAGE_ID = "7.13"
EXPECTED_API_SCHEMA = "phase7-13-console-api-v1"

MIN_PYTHON = (3, 9)
MAX_TESTED_PYTHON = (3, 14)

START_TIMEOUT_SECONDS = 45.0        # bounded: the owner is never left waiting indefinitely
HEALTH_POLL_INTERVAL = 0.5
HEALTH_REQUEST_TIMEOUT = 3.0
STOP_TIMEOUT_SECONDS = 15.0
STOP_POLL_INTERVAL = 0.25
# A child's identity is read immediately after spawn, through the handle this launcher already owns.
# That read does not fail in normal operation, so the retry exists only to absorb a transient API
# failure — it is bounded, and exhausting it fails CLOSED rather than recording an unverifiable child.
START_TOKEN_READ_ATTEMPTS = 3
START_TOKEN_RETRY_SECONDS = 0.1
# A graceful console-break only reaches a process that shares the caller's console, which a detached
# child never does on Windows. So the polite signal is still sent (it is what stops the console
# cleanly on POSIX), but the owner waits a SHORT grace window, not the whole stop budget, before the
# launcher terminates the one identity-verified process directly. Every accepted-authority write is
# already flushed and fsync'd before its response returns, so no recorded state can be lost.
STOP_GRACE_SECONDS = 3.0
LOCK_STALE_SECONDS = 180.0          # a lock older than this is treated as abandoned
MAX_LOG_BYTES = 262144              # 256 KiB, then rotated to a single .1 file
MAX_LOG_LINE = 400

PID_FILE = "console.pid.json"
LOCK_FILE = "launcher.lock"
LOG_FILE = "launcher.log"
STATUS_FILE = "launcher_status.json"

# Loopback bind addresses this launcher will accept. Nothing else can be bound or opened.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

PORT_IN_USE_MESSAGE = "PORT 8780 IS ALREADY IN USE"

# This launcher never selects a port automatically. The port is fixed so that Start, Stop and Open
# always agree about where the console is; a random port would silently break Stop and Open.
AUTOMATIC_PORT_SELECTION = False

# Every key whose VALUE must never reach the launcher log or the exported status record.
SECRET_KEY_MARKERS = ("token", "secret", "password", "passwd", "cookie", "csrf", "authorization",
                      "api_key", "apikey", "credential", "session")

LAUNCHER_NEVER = {
    "uses_shell_true": True,
    "runs_an_arbitrary_command": True,
    "imports_an_arbitrary_module": True,
    "opens_an_arbitrary_url": True,
    "binds_a_non_loopback_address": True,
    "kills_a_process_it_did_not_start": True,
    "kills_every_python_process": True,
    "selects_a_random_port": True,
    "registers_a_service_or_scheduler": True,
    "starts_with_windows": True,
    "logs_a_secret_value": True,
    "connects_to_seller_central": True,
    "uses_a_seller_api_or_advertising_api": True,
    "downloads_a_seller_report": True,
    "drives_a_seller_browser": True,
}

SELLER_CENTRAL_COUNTERS = {
    "seller_central_connections": 0, "seller_api_calls": 0, "advertising_api_calls": 0,
    "seller_account_mutations": 0, "seller_report_downloads": 0, "seller_bulk_uploads": 0,
    "seller_browser_automation_actions": 0, "seller_credential_store_count": 0,
    "buyer_messages_sent": 0, "review_requests_sent": 0,
}


class LauncherError(Exception):
    """A safe, owner-facing launcher failure carrying a bounded code (never a stack trace)."""

    def __init__(self, readiness, code, detail=""):
        self.readiness = readiness
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


def repo_root():
    """The repository root, resolved from THIS file only — never from the current directory, so the
    launcher keeps working when the owner double-clicks it from anywhere or moves the folder."""
    return _ROOT


def is_loopback_host(host):
    return _s(host).strip().strip("[]").lower() in LOOPBACK_HOSTS


def validate_host(host):
    if not is_loopback_host(host):
        raise LauncherError(LAUNCHER_REPOSITORY_REQUIRED, "NON_LOOPBACK_BIND_REFUSED", _s(host))
    return _s(host).strip()


def validate_port(port):
    try:
        p = int(port)
    except (TypeError, ValueError):
        raise LauncherError(LAUNCHER_PORT_BLOCKED, "INVALID_PORT", _s(port))
    if not (1 <= p <= 65535):
        raise LauncherError(LAUNCHER_PORT_BLOCKED, "INVALID_PORT", _s(port))
    return p


def console_url(host, port):
    """The one URL this launcher may ever open. Built only from a validated loopback host and a
    validated port — never from owner input, a config file, an environment value or a redirect."""
    h = validate_host(host)
    p = validate_port(port)
    if h == "::1":
        h = "[::1]"
    return f"http://{h}:{p}"


def is_allowed_url(url, host, port):
    """A launcher may open exactly the console URL or the console URL + '/'. Nothing else."""
    base = console_url(host, port)
    return url in (base, base + "/")


def _relpath(path):
    """A repository-relative POSIX display path. Absolute local paths never reach a launcher record."""
    try:
        rel = os.path.relpath(os.path.abspath(path), repo_root())
    except ValueError:
        return os.path.basename(_s(path))
    if rel.startswith(".."):
        return os.path.basename(_s(path))
    return rel.replace("\\", "/")


_URL_VALUE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://\S*")
# A secret-looking key takes its WHOLE value, not just the first token: `Authorization=Bearer xyz`
# must not leave `xyz` behind. The value therefore runs to the next `key=` field or end of line.
_SECRET_FIELD = re.compile(
    r"(?i)([A-Za-z0-9_.\-]*(?:" + "|".join(SECRET_KEY_MARKERS) + r")[A-Za-z0-9_.\-]*)"
    r"\s*=\s*.*?(?=\s+[A-Za-z_][A-Za-z0-9_.\-]*\s*=|$)")


def redact(text):
    """Bounded, secret-free log text.

    Two rules, applied to every line before it is written:
      1. a `key=value` whose KEY looks like a secret loses its ENTIRE value;
      2. any value containing a URL scheme loses the whole value — an endpoint URL can itself carry
         a credential, so it is never written even when its key looks innocent.
    """
    out = _s(text).replace("\r", " ").replace("\n", " ")
    out = _SECRET_FIELD.sub(lambda m: m.group(1) + "=[REDACTED]", out)
    out = _URL_VALUE.sub("[REDACTED]", out)
    return out[:MAX_LOG_LINE]


# ================================================================ process identity (stdlib + ctypes)
def process_alive(pid):
    """True when a process object with this PID still exists. Never matches by name.

    WINDOWS LIMITATION, measured and proven by the Phase 7.14 real-process tests: this answer is
    conclusive only when it is FALSE. Windows keeps a terminated process's object — and therefore its
    PID — addressable for as long as ANY handle to it is open, so OpenProcess + GetProcessTimes keep
    succeeding after the process has demonstrably exited (WaitForSingleObject signalled,
    GetExitCodeProcess reporting a real exit code). A True answer therefore means "not yet freed",
    which is NOT the same as "still running".

    Stop must never infer a running console from this function alone; it proves exit through a handle
    it holds itself (see WindowsExitVerifier). False remains conclusive: the object is only freed once
    the process has exited and every handle to it is closed."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        return _win_process_times(pid) is not None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _win_process_times(pid):
    """(creation_time_100ns) for a live PID on Windows, else None. Stdlib ctypes only."""
    import ctypes
    from ctypes import wintypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_t = wintypes.FILETIME()
        kernel_t = wintypes.FILETIME()
        user_t = wintypes.FILETIME()
        ok = k32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_t),
                                 ctypes.byref(kernel_t), ctypes.byref(user_t))
        if not ok:
            return None
        return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    finally:
        k32.CloseHandle(handle)


def process_start_token(pid):
    """A stable per-process-instance token used to prove a recorded PID has NOT been reused.

    Windows: the process creation time from GetProcessTimes.
    POSIX:   field 22 (starttime) of /proc/<pid>/stat.
    Returns None when the process does not exist or the platform cannot answer; a None token is
    treated as 'identity unproven', which makes Stop refuse rather than guess."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    if os.name == "nt":
        t = _win_process_times(pid)
        return None if t is None else f"win-create-{t}"
    stat = f"/proc/{pid}/stat"
    try:
        with open(stat, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError:
        return None
    try:
        after = raw[raw.rindex(")") + 2:].split()
        return f"posix-start-{after[19]}"
    except (ValueError, IndexError):
        return None


def valid_identity_token(token):
    """Whether this value may be used as identity evidence AT ALL. ONE rule, every site.

    Every consumer of the recorded token previously tested it for truthiness inline, and each of
    them read a falsy token as "skip the check" rather than "cannot verify". A null token therefore
    authorized a termination in `_pinned_identity`, disabled the PID-reuse branch in
    `_clear_stale_pid`, and produced an unverified `launcher_owned: true` in `status`.

    This deliberately checks PRESENCE and SHAPE, not format. The accepted seams legitimately produce
    tokens like `tok-4242`; a format gate here would reject the test process layer and silently
    convert every seam-driven stop into a refusal, which is a different bug wearing this one's
    clothes."""
    return isinstance(token, str) and bool(token.strip())


def process_start_token_from_popen(proc):
    """The identity token of the child THIS `Popen` object owns, read through its own handle.

    Returns (token, error). Start already holds a handle to the process it just created, and reading
    identity by raw PID re-resolves that number against whatever the OS currently has there — the
    same unpinned read the stop path was rewritten to eliminate. This closes it at the source.

    Windows only, by necessity and not by omission: on POSIX the child stays unreaped for as long as
    the `Popen` lives, so the kernel cannot recycle its PID and the /proc read is ALREADY pinned by
    the same object. There is nothing to close there."""
    if os.name != "nt":
        return None, "NOT_WINDOWS"
    handle = getattr(proc, "_handle", None)
    if handle is None:
        return None, "NO_POPEN_HANDLE"
    try:
        ctypes, wintypes, k32 = _win_kernel32()
    except Exception:                            # noqa: BLE001 — never crash a start
        return None, "KERNEL32_UNAVAILABLE"
    try:
        return _win_handle_start_token(ctypes, wintypes, k32, int(handle))
    except (TypeError, ValueError):
        return None, "NO_POPEN_HANDLE"


# ---------------------------------------------------------------- Windows exit proof
_WIN_SYNCHRONIZE = 0x00100000
_WIN_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WIN_PROCESS_TERMINATE = 0x0001
_WIN_STILL_ACTIVE = 259
_WIN_WAIT_OBJECT_0 = 0x00000000
_WIN_WAIT_TIMEOUT = 0x00000102
_WIN_WAIT_FAILED = 0xFFFFFFFF
_WIN_WAIT_NAMES = {_WIN_WAIT_OBJECT_0: "WAIT_OBJECT_0", _WIN_WAIT_TIMEOUT: "WAIT_TIMEOUT",
                   _WIN_WAIT_FAILED: "WAIT_FAILED", 0x00000080: "WAIT_ABANDONED"}


def _win_kernel32():
    """kernel32 with the exact signatures this module uses. Stdlib ctypes only, no dependency."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.TerminateProcess.restype = wintypes.BOOL
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.GetExitCodeProcess.restype = wintypes.BOOL
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.GetProcessTimes.restype = wintypes.BOOL
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    return ctypes, wintypes, k32


def _win_handle_start_token(ctypes, wintypes, k32, handle):
    """The creation-time identity token read through EXACTLY this handle. Returns (token, error).

    This is the only identity read that cannot be answered by a DIFFERENT process. A raw-PID read
    (`process_start_token`) re-resolves the number first, so it describes whatever process holds that
    PID at the moment of the call; this call can only ever describe the one process object the given
    handle already refers to. Every identity decision that authorizes a signal must come from here.

    The token format is byte-identical to `process_start_token`'s, so a handle-derived token and a
    recorded token compare directly."""
    creation = wintypes.FILETIME()
    exit_t = wintypes.FILETIME()
    kernel_t = wintypes.FILETIME()
    user_t = wintypes.FILETIME()
    try:
        ctypes.set_last_error(0)
        ok = k32.GetProcessTimes(wintypes.HANDLE(handle), ctypes.byref(creation),
                                 ctypes.byref(exit_t), ctypes.byref(kernel_t), ctypes.byref(user_t))
    except Exception:                            # noqa: BLE001 — an API failure is data, not a crash
        return None, "GET_PROCESS_TIMES_RAISED"
    if not ok:
        return None, f"GET_PROCESS_TIMES_FAILED_{ctypes.get_last_error()}"
    return f"win-create-{(creation.dwHighDateTime << 32) | creation.dwLowDateTime}", None


class WindowsExitVerifier:
    """Authoritative exit proof for ONE already-identity-verified process on Windows.

    It opens a single read/synchronise handle (SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION —
    never PROCESS_TERMINATE) BEFORE the process is asked to stop, and answers from the kernel:

        WaitForSingleObject(handle, 0) == WAIT_OBJECT_0   -> the process object is signalled
        GetExitCodeProcess(handle) != STILL_ACTIVE        -> and it carries a real exit code

    Both must agree before an exit is reported. WAIT_TIMEOUT means "not yet proven exited", and a
    failed API call means UNPROVEN — never success.

    It is ALSO the identity authority. The handle is opened before any delay and before anything is
    signalled, and the process creation token is read back through that same handle immediately, so
    the caller can prove that this handle refers to the recorded process before it authorizes
    anything. Windows cannot recycle a PID while a handle to it is open, so from the moment this
    handle is open and its token has matched, the pinned PID cannot become a different process for
    the remainder of the stop. The guarantee starts at handle acquisition — never earlier.
    """

    def __init__(self, pid):
        self.pid = pid
        self.handle = None
        self.usable = False
        self.open_error = None
        self.start_token = None
        self.token_error = None
        try:
            pid_i = int(pid)
        except (TypeError, ValueError):
            self.open_error = "INVALID_PID"
            return
        if pid_i <= 0:
            self.open_error = "INVALID_PID"
            return
        try:
            ctypes, wintypes, k32 = _win_kernel32()
        except Exception:                        # noqa: BLE001 — never crash the launcher
            self.open_error = "KERNEL32_UNAVAILABLE"
            return
        self._ctypes, self._wintypes, self._k32 = ctypes, wintypes, k32
        handle = k32.OpenProcess(_WIN_SYNCHRONIZE | _WIN_PROCESS_QUERY_LIMITED_INFORMATION,
                                 False, pid_i)
        if not handle:
            self.open_error = f"OPEN_PROCESS_FAILED_{ctypes.get_last_error()}"
            self.token_error = self.open_error
            return
        self.handle = handle
        self.usable = True
        # Read identity through the handle we just pinned, before returning to the caller and
        # therefore before any probe, delay or signal can intervene.
        self.start_token, self.token_error = _win_handle_start_token(ctypes, wintypes, k32, handle)

    def identity(self, recorded):
        """Whether the process THIS handle refers to is the recorded one, as a bounded record.

        `matches` is True only when a token was actually read through this handle and it equals the
        recorded token. An unreadable token, an absent handle and a mismatch are all reported as
        not-matching, so every failure direction is fail-closed."""
        return {"source": "windows_process_handle", "handle_held": bool(self.usable),
                "handle_token_read": self.start_token is not None,
                "matches": bool(self.usable and self.start_token is not None and recorded
                                and self.start_token == recorded),
                "api_error": self.token_error}

    def state(self):
        """The kernel's answer, as a bounded, secret-free diagnostic record."""
        base = {"source": "windows_process_handle", "handle_held": bool(self.usable)}
        if not self.usable:
            base.update({"exit_state": EXIT_STATE_UNPROVEN, "api_error": self.open_error})
            return base
        ctypes, wintypes, k32 = self._ctypes, self._wintypes, self._k32
        wait = int(k32.WaitForSingleObject(wintypes.HANDLE(self.handle), 0))
        base["wait_result"] = wait
        base["wait_name"] = _WIN_WAIT_NAMES.get(wait, f"WAIT_OTHER_{wait}")
        if wait == _WIN_WAIT_FAILED:
            base.update({"exit_state": EXIT_STATE_UNPROVEN,
                         "api_error": f"WAIT_FAILED_{ctypes.get_last_error()}"})
            return base
        code = wintypes.DWORD(0)
        got = bool(k32.GetExitCodeProcess(wintypes.HANDLE(self.handle), ctypes.byref(code)))
        base["get_exit_code_ok"] = got
        if not got:
            base.update({"exit_state": EXIT_STATE_UNPROVEN,
                         "api_error": f"GET_EXIT_CODE_FAILED_{ctypes.get_last_error()}"})
            return base
        base["exit_code"] = int(code.value)
        base["still_active"] = int(code.value) == _WIN_STILL_ACTIVE
        if wait == _WIN_WAIT_OBJECT_0 and not base["still_active"]:
            base["exit_state"] = EXIT_STATE_EXITED
        elif wait == _WIN_WAIT_TIMEOUT and base["still_active"]:
            base["exit_state"] = EXIT_STATE_RUNNING
        else:
            # The two sources disagree (a signalled object still reporting STILL_ACTIVE, or the
            # reverse). Nothing is proven, so nothing is claimed.
            base["exit_state"] = EXIT_STATE_UNPROVEN
            base["api_error"] = "WAIT_AND_EXIT_CODE_DISAGREE"
        return base

    def close(self):
        if self.handle:
            try:
                self._k32.CloseHandle(self.handle)
            except Exception:                    # noqa: BLE001
                pass
        self.handle = None
        self.usable = False


def open_exit_verifier(pid):
    """A handle-backed exit verifier on Windows; None elsewhere (POSIX needs no such proof)."""
    if os.name != "nt":
        return None
    return WindowsExitVerifier(pid)


def terminate_process_result(pid, *, hard=False, expect_token=None):
    """Signal ONE already-identity-verified process and REPORT what the OS actually said.

    Callers must verify identity first; this function never searches for a process, never matches a
    name and never touches a process tree. The return value is a record, not a guess: a refused
    request is visible to the caller instead of being discarded.

    `expect_token` closes the last raw-PID gap on the hard path. Terminating needs its own handle,
    and opening one by PID re-resolves that number — so when a token is supplied, the creation token
    is read back through THAT EXACT handle and must equal it before the kill is issued. A missing or
    mismatched token means no kill is issued at all: the request is refused and says why."""
    out = {"ok": False, "hard": bool(hard), "api": None, "error": None,
           "identity_checked": expect_token is not None, "identity_verified": None}
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        out["error"] = "INVALID_PID"
        return out
    if pid <= 0:
        out["error"] = "INVALID_PID"
        return out
    if os.name == "nt":
        if not hard:
            # Graceful: the console is spawned as its own process-group leader, so a console
            # break reaches exactly that process and it shuts the server down cleanly.
            out["api"] = "GenerateConsoleCtrlEvent"
            try:
                os.kill(pid, signal.CTRL_BREAK_EVENT)
                out["ok"] = True
            except (OSError, AttributeError, ValueError) as e:
                out["error"] = type(e).__name__
            return out
        out["api"] = "TerminateProcess"
        try:
            ctypes, wintypes, k32 = _win_kernel32()
        except Exception:                        # noqa: BLE001
            out["error"] = "KERNEL32_UNAVAILABLE"
            return out
        access = _WIN_PROCESS_TERMINATE
        if expect_token is not None:
            access |= _WIN_PROCESS_QUERY_LIMITED_INFORMATION      # to re-read identity through it
        handle = k32.OpenProcess(access, False, pid)
        if not handle:
            out["error"] = f"OPEN_PROCESS_FAILED_{ctypes.get_last_error()}"
            return out
        try:
            if expect_token is not None:
                token, token_error = _win_handle_start_token(ctypes, wintypes, k32, handle)
                if token is None:
                    out["identity_verified"] = False
                    out["error"] = token_error or "TERMINATION_IDENTITY_UNREADABLE"
                    return out                   # nothing is killed on an unreadable identity
                if token != expect_token:
                    out["identity_verified"] = False
                    out["error"] = "TERMINATION_IDENTITY_MISMATCH"
                    return out                   # this handle is a DIFFERENT process: refuse
                out["identity_verified"] = True
            ctypes.set_last_error(0)
            ok = bool(k32.TerminateProcess(handle, 1))
            out["ok"] = ok
            if not ok:
                out["error"] = f"TERMINATE_PROCESS_FAILED_{ctypes.get_last_error()}"
        finally:
            k32.CloseHandle(handle)
        return out
    out["api"] = "SIGKILL" if hard else "SIGTERM"
    try:
        os.kill(pid, signal.SIGKILL if hard else signal.SIGTERM)
        out["ok"] = True
    except OSError as e:
        out["error"] = type(e).__name__
    return out


def terminate_process(pid, *, hard=False):
    """Boolean form of terminate_process_result, kept for the accepted call signature."""
    return terminate_process_result(pid, hard=hard)["ok"]


# ================================================================ preflight checks
def check_python(version=None):
    """Verify the running interpreter is a supported Python."""
    v = tuple(version or sys.version_info[:3])
    ok = v[:2] >= MIN_PYTHON
    return {
        "check": "python_supported", "ok": bool(ok),
        "python_version": ".".join(str(x) for x in v),
        "minimum": ".".join(str(x) for x in MIN_PYTHON),
        "beyond_tested": bool(v[:2] > MAX_TESTED_PYTHON),
        "implementation": platform.python_implementation(),
    }


def check_repository(root=None):
    """Verify the working copy really contains the accepted console this launcher may start."""
    root = os.path.abspath(root or repo_root())
    source = os.path.join(root, CONSOLE_SOURCE)
    static = os.path.join(root, CONSOLE_STATIC)
    missing = []
    if not os.path.isfile(source):
        missing.append(CONSOLE_SOURCE.replace("\\", "/"))
    for name in CONSOLE_STATIC_FILES:
        if not os.path.isfile(os.path.join(static, name)):
            missing.append((os.path.join(CONSOLE_STATIC, name)).replace("\\", "/"))
    return {"check": "repository_contains_console", "ok": not missing,
            "repository_root_present": os.path.isdir(root), "missing": missing}


def pilot_readiness(root=None):
    """Is THIS working copy ready for a real owner pilot? Read-only file presence, nothing more.

    Ready means: the owner can start it by double-click (all six launcher scripts present) and has
    the pilot kit in hand (all six documents present)."""
    root = os.path.abspath(root or repo_root())
    missing = [rel for rel in (LAUNCHER_SCRIPTS + PILOT_DOCUMENTS)
               if not os.path.isfile(os.path.join(root, rel.replace("/", os.sep)))]
    return {
        "check": "pilot_kit_present",
        "ok": not missing,
        "readiness": PILOT_READY if not missing else PILOT_REQUIRED,
        "missing": missing,
        "launcher_scripts": len(LAUNCHER_SCRIPTS),
        "pilot_documents": len(PILOT_DOCUMENTS),
    }


def check_imports():
    """Verify the console module and the stdlib pieces it needs actually import here."""
    missing = []
    for name in ("http.server", "json", "socket", "ssl", "hashlib", "hmac", "secrets",
                 "urllib.request", "webbrowser"):
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    console_ok = True
    console_detail = None
    try:
        __import__(CONSOLE_MODULE)
    except Exception as e:                       # noqa: BLE001 — report, never crash the launcher
        console_ok = False
        console_detail = type(e).__name__
    return {"check": "required_imports", "ok": not missing and console_ok,
            "missing_stdlib": missing, "console_module_importable": console_ok,
            "console_import_error": console_detail}


def port_in_use(host, port, *, timeout=0.6):
    """True when something is already listening on the loopback port."""
    h = "127.0.0.1" if _s(host).strip("[]") == "::1" else _s(host)
    try:
        with socket.create_connection((h, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def probe_health(host, port, *, timeout=HEALTH_REQUEST_TIMEOUT, opener=None):
    """Probe the ACCEPTED console health endpoint on loopback.

    Returns {"ok": bool, "reason": str, "stage_id":..., "api_schema":..., "readiness":...}. The
    request is issued to a validated loopback URL only, follows no redirect and reads a bounded body.
    """
    url = console_url(host, port) + HEALTH_PATH
    try:
        raw = (opener or _open_loopback)(url, timeout)
    except Exception as e:                       # noqa: BLE001
        return {"ok": False, "reason": type(e).__name__, "stage_id": None, "api_schema": None,
                "readiness": None, "http_status": None}
    if raw is None:
        return {"ok": False, "reason": "NO_RESPONSE", "stage_id": None, "api_schema": None,
                "readiness": None, "http_status": None}
    status, body = raw
    if status != 200:
        return {"ok": False, "reason": f"HTTP_{status}", "stage_id": None, "api_schema": None,
                "readiness": None, "http_status": status}
    try:
        doc = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"ok": False, "reason": "MALFORMED_HEALTH_BODY", "stage_id": None,
                "api_schema": None, "readiness": None, "http_status": status}
    data = doc.get("data") or {}
    stage = data.get("stage_id")
    schema = data.get("api_schema") or doc.get("schema_version")
    accepted = (stage == EXPECTED_STAGE_ID and schema == EXPECTED_API_SCHEMA)
    return {"ok": bool(accepted), "reason": "OK" if accepted else "NOT_THE_ACCEPTED_CONSOLE",
            "stage_id": stage, "api_schema": schema, "readiness": doc.get("readiness"),
            "http_status": status,
            "seller_central_action_performed": bool(data.get("seller_central_action_performed"))}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):        # a health probe never follows a redirect
        return None


def _open_loopback(url, timeout):
    """Fetch a validated loopback URL with a bounded body. Never used for any other destination."""
    if not url.startswith("http://127.0.0.1:") and not url.startswith("http://localhost:") \
            and not url.startswith("http://[::1]:"):
        raise LauncherError(LAUNCHER_REPOSITORY_REQUIRED, "NON_LOOPBACK_PROBE_REFUSED", "")
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "Host": url.split("//", 1)[1].split("/", 1)[0]})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read(262144)
    except urllib.error.HTTPError as e:
        return e.code, b""


# ================================================================ launcher workspace
class Workspace:
    """The launcher's own runtime directory. It holds only launcher state: a PID record, a lock, a
    bounded log and a status document. It is under runs/, which git ignores."""

    def __init__(self, root=None, *, subdir=LAUNCHER_SUBDIR):
        self.root = os.path.abspath(root or repo_root())
        self.dir = os.path.join(self.root, subdir)
        self.writable = True
        self.write_error = None

    def ensure(self):
        try:
            os.makedirs(self.dir, exist_ok=True)
            probe = os.path.join(self.dir, ".write-probe")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
        except OSError as e:
            self.writable = False
            self.write_error = type(e).__name__
        return self.writable

    def path(self, name):
        return os.path.join(self.dir, name)

    # ---- bounded, secret-free log ---------------------------------------------------------------
    def log(self, event, **fields):
        if not self.writable:
            return
        parts = [_iso(_now_utc()), _s(event)]
        for k in sorted(fields):
            parts.append(f"{k}={redact(fields[k])}")
        line = redact(" ".join(parts))
        path = self.path(LOG_FILE)
        try:
            if os.path.isfile(path) and os.path.getsize(path) > MAX_LOG_BYTES:
                os.replace(path, path + ".1")
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            self.writable = False

    def write_status(self, result):
        if not self.writable:
            return
        try:
            tmp = self.path(STATUS_FILE + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path(STATUS_FILE))
        except OSError:
            self.writable = False

    # ---- PID record -----------------------------------------------------------------------------
    def read_pid(self):
        path = self.path(PID_FILE)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            return None
        return doc if isinstance(doc, dict) else None

    def write_pid(self, record):
        if not self.writable:
            return False
        try:
            tmp = self.path(PID_FILE + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path(PID_FILE))
            return True
        except OSError:
            return False

    def clear_pid(self):
        try:
            os.remove(self.path(PID_FILE))
            return True
        except OSError:
            return False


class LauncherLock:
    """An exclusive, self-healing launcher lock.

    Created with O_CREAT|O_EXCL so two double-clicks can never start two consoles. A lock whose owner
    process is gone, or which is older than LOCK_STALE_SECONDS, is treated as abandoned and reclaimed
    — a crashed launcher can never wedge the owner out of their own toolkit."""

    def __init__(self, workspace, *, stale_after=LOCK_STALE_SECONDS, clock=time.time):
        self.ws = workspace
        self.path = workspace.path(LOCK_FILE)
        self.stale_after = stale_after
        self.clock = clock
        self.held = False
        self.reclaimed_stale = False

    def _read(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                doc = json.load(f)
            return doc if isinstance(doc, dict) else None
        except (OSError, ValueError):
            return None

    def _is_stale(self, doc):
        if doc is None:
            return True
        age = self.clock() - float(doc.get("acquired_at_epoch") or 0)
        if age > self.stale_after:
            return True
        pid = doc.get("launcher_pid")
        return not process_alive(pid)

    def acquire(self):
        for attempt in (1, 2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                doc = self._read()
                if attempt == 1 and self._is_stale(doc):
                    self.reclaimed_stale = True
                    try:
                        os.remove(self.path)
                    except OSError:
                        return False
                    continue
                return False
            except OSError:
                return False
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"schema_version": "phase7-14-launcher-lock-v1",
                           "launcher_pid": os.getpid(),
                           "acquired_at_epoch": self.clock(),
                           "acquired_at": _iso(_now_utc())}, f, sort_keys=True)
            self.held = True
            return True
        return False

    def release(self):
        if not self.held:
            return
        try:
            os.remove(self.path)
        except OSError:
            pass
        self.held = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False


# ================================================================ the fixed console command
def console_command(*, python_exe=None, workspace_root=DEFAULT_WORKSPACE_ROOT, host=DEFAULT_HOST,
                    port=DEFAULT_PORT):
    """The ONE command this launcher may spawn. Fixed module, fixed verb, validated host/port.

    The workspace root is normalized to a repository-relative POSIX path and refused if it escapes
    the repository, so no argument can ever point the console somewhere unexpected."""
    h = validate_host(host)
    p = validate_port(port)
    ws = _s(workspace_root).replace("\\", "/").strip() or DEFAULT_WORKSPACE_ROOT
    if os.path.isabs(ws) or ws.startswith("..") or "/../" in ws or ws.endswith("/.."):
        raise LauncherError(LAUNCHER_REPOSITORY_REQUIRED, "WORKSPACE_ROOT_REFUSED", ws)
    return [python_exe or sys.executable, "-m", CONSOLE_MODULE,
            "--workspace-root", ws, "--host", h, "--port", str(p), CONSOLE_COMMAND_VERB]


def command_fingerprint(cmd):
    """A stable identity for the spawned command, minus the interpreter path (which is absolute)."""
    return " ".join(cmd[1:])


def _spawn(cmd, *, cwd):
    """Spawn the fixed console command. No shell is ever requested (the `shell` keyword is never
    passed at all); the argument vector is a list, so nothing is ever interpreted by a shell."""
    kwargs = {"cwd": cwd, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        # Its own process group, so a graceful console-break in Stop reaches exactly this process,
        # and no window is shown to the owner.
        kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                   | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def open_browser(url, host, port, *, opener=None):
    """Open the console URL — and only the console URL — in the owner's default browser."""
    if not is_allowed_url(url, host, port):
        raise LauncherError(LAUNCHER_BROWSER_UNAVAILABLE, "URL_NOT_ALLOWED", "")
    try:
        return bool((opener or webbrowser.open)(url))
    except Exception:                            # noqa: BLE001 — a browser failure is never fatal
        return False


# ================================================================ the launcher
class Launcher:
    """Start / Stop / Open with every hook injectable so the tests never need a real server,
    a real browser, a real port or a real child process."""

    def __init__(self, *, root=None, host=DEFAULT_HOST, port=DEFAULT_PORT,
                 workspace_root=DEFAULT_WORKSPACE_ROOT, timeout=START_TIMEOUT_SECONDS,
                 stop_timeout=STOP_TIMEOUT_SECONDS, open_browser_on_start=True,
                 workspace=None, clock=time.monotonic, sleep=time.sleep,
                 health=None, port_probe=None, spawn=None, browser=None,
                 alive=None, start_token=None, terminate=None, python_exe=None,
                 exit_verifier=None, child_token=None):
        self.root = os.path.abspath(root or repo_root())
        self.host = host
        self.port = port
        self.workspace_root = workspace_root
        self.timeout = float(timeout)
        self.stop_timeout = float(stop_timeout)
        self.open_browser_on_start = bool(open_browser_on_start)
        self.ws = workspace or Workspace(self.root)
        self.clock = clock
        self.sleep = sleep
        self.python_exe = python_exe
        self._health = health or (lambda h, p: probe_health(h, p))
        self._port_probe = port_probe or port_in_use
        self._spawn = spawn or _spawn
        self._browser = browser
        self._alive = alive or process_alive
        self._start_token = start_token or process_start_token
        # Identity of a child THIS launcher owns, read through the spawn object's own handle. The
        # raw-PID reader above stays as the fallback for platforms with no handle to read (POSIX,
        # where an unreaped child already pins its own PID) and for injected spawn stand-ins.
        self._child_token = child_token or process_start_token_from_popen
        self._terminate = terminate or terminate_process_result
        # Handle-validated termination exists only on the REAL process layer; an injected terminate
        # seam keeps the accepted two-argument signature and is never handed a token it cannot check.
        self._terminate_validates_identity = terminate is None
        # Injecting `alive` replaces the whole process layer, handle-based half included: a test that
        # declares process existence through a seam must not have a real Windows handle opened
        # against its stand-in PID. Without an injected seam the real verifier is used.
        self._exit_verifier = exit_verifier or ((lambda pid: None) if alive is not None
                                                else open_exit_verifier)
        # On real Windows the pinned handle is MANDATORY identity evidence. Without it the only
        # remaining answer is a raw-PID read, which is exactly the unpinned read that let a stop
        # reach a process it had never verified — so a missing handle fails closed instead.
        self._handle_identity_required = bool(os.name == "nt" and alive is None
                                              and exit_verifier is None)
        # Where an exit answer comes from, and whether a True process_alive() may be believed. On
        # POSIX it may (a PID that no longer resolves is gone, and one that does is running). On real
        # Windows it may NOT: see process_alive.__doc__.
        self._alive_authoritative = bool(alive is not None or os.name != "nt")
        self._exit_source = "process_alive_seam" if alive is not None else (
            "process_alive_posix" if os.name != "nt" else "process_alive_windows")
        self.terminate_requests = []
        self.browser_opened = False
        self.browser_attempted = False

    # ---------------------------------------------------------------- shared result envelope
    def _result(self, readiness, **extra):
        out = {
            "schema_version": LAUNCHER_SCHEMA, "stage_id": STAGE_ID, "stage_name": STAGE_NAME,
            "readiness": readiness,
            "console_url": console_url(self.host, self.port),
            "host": self.host, "port": validate_port(self.port),
            "automatic_port_selection": AUTOMATIC_PORT_SELECTION,
            "browser_opened": self.browser_opened,
            "browser_attempted": self.browser_attempted,
            "launcher_never": dict(LAUNCHER_NEVER),
            "seller_central_counters": dict(SELLER_CENTRAL_COUNTERS),
        }
        out.update(extra)
        return out

    # ---------------------------------------------------------------- preflight
    def preflight(self):
        checks = [check_python(), check_repository(self.root), check_imports()]
        return {"ok": all(c["ok"] for c in checks), "checks": checks}

    def _preflight_failure(self, pre):
        by = {c["check"]: c for c in pre["checks"]}
        if not by["python_supported"]["ok"]:
            return LAUNCHER_PYTHON_REQUIRED, "UNSUPPORTED_PYTHON", by["python_supported"]["python_version"]
        if not by["repository_contains_console"]["ok"]:
            return (LAUNCHER_MODULE_REQUIRED, "CONSOLE_MODULE_MISSING",
                    ",".join(by["repository_contains_console"]["missing"])[:200])
        return (LAUNCHER_MODULE_REQUIRED, "REQUIRED_IMPORT_MISSING",
                _s(by["required_imports"].get("console_import_error")))

    # ---------------------------------------------------------------- start
    def start(self):
        self.ws.ensure()
        pre = self.preflight()
        if not pre["ok"]:
            readiness, code, detail = self._preflight_failure(pre)
            self.ws.log("start.preflight_failed", code=code, readiness=readiness)
            return self._finish(self._result(readiness, phase="preflight", error_code=code,
                                             error_detail=detail, preflight=pre,
                                             owner_message=_owner_message(readiness, code, detail)))
        if not self.ws.writable:
            self.ws.log("start.workspace_not_writable")
            return self._finish(self._result(
                LAUNCHER_WORKSPACE_REQUIRED, phase="workspace", preflight=pre,
                error_code="RUNTIME_DIR_NOT_WRITABLE", error_detail=_s(self.ws.write_error),
                owner_message=_owner_message(LAUNCHER_WORKSPACE_REQUIRED, "RUNTIME_DIR_NOT_WRITABLE", "")))

        lock = LauncherLock(self.ws)
        if not lock.acquire():
            # A second double-click while the first start is still running must never start a
            # second console; the owner is told to wait, not shown an error they cannot act on.
            existing = self._health(self.host, self.port)
            if existing.get("ok"):
                return self._already_running(pre, existing, locked=True)
            self.ws.log("start.locked")
            return self._finish(self._result(
                LAUNCHER_LOCKED, phase="lock", preflight=pre, error_code="LAUNCHER_ALREADY_STARTING",
                owner_message=_owner_message(LAUNCHER_LOCKED, "LAUNCHER_ALREADY_STARTING", "")))
        try:
            return self._start_locked(pre, lock)
        finally:
            lock.release()

    def _start_locked(self, pre, lock):
        if lock.reclaimed_stale:
            self.ws.log("start.stale_lock_reclaimed")

        # ---- already running? -------------------------------------------------------------------
        occupied = self._port_probe(self.host, self.port)
        health = self._health(self.host, self.port) if occupied else {"ok": False, "reason": "PORT_FREE"}
        if occupied and health.get("ok"):
            return self._already_running(pre, health, stale_lock=lock.reclaimed_stale)
        if occupied and not health.get("ok"):
            # Something else owns the port. We NEVER kill it and we never silently move to another
            # port — Stop and Open would then look in the wrong place.
            self.ws.log("start.port_blocked", port=self.port, reason=_s(health.get("reason")))
            return self._finish(self._result(
                LAUNCHER_PORT_BLOCKED, phase="port", preflight=pre,
                error_code="PORT_IN_USE_BY_ANOTHER_PROCESS", error_detail=_s(health.get("reason")),
                port_message=PORT_IN_USE_MESSAGE,
                owner_message=_owner_message(LAUNCHER_PORT_BLOCKED, "PORT_IN_USE_BY_ANOTHER_PROCESS", "")))

        # ---- stale PID record from a previous run ------------------------------------------------
        stale_pid = self._clear_stale_pid()

        # ---- spawn the ONE fixed command --------------------------------------------------------
        cmd = console_command(python_exe=self.python_exe, workspace_root=self.workspace_root,
                              host=self.host, port=self.port)
        self.ws.log("start.spawn", command=command_fingerprint(cmd), port=self.port)
        try:
            proc = self._spawn(cmd, cwd=self.root)
        except OSError as e:
            self.ws.log("start.spawn_failed", code=type(e).__name__)
            return self._finish(self._result(
                LAUNCHER_FAILED, phase="spawn", preflight=pre, error_code="CONSOLE_SPAWN_FAILED",
                error_detail=type(e).__name__,
                owner_message=_owner_message(LAUNCHER_FAILED, "CONSOLE_SPAWN_FAILED", "")))

        pid = getattr(proc, "pid", None)

        # ---- identity of the child we just created, BEFORE anything is recorded ------------------
        # A start that cannot say WHICH process it created must not write a record claiming it owns
        # one. The accepted baseline read this token by raw PID, persisted whatever came back —
        # including None — and reported READY; every downstream identity check then read that null
        # as "nothing to verify" and waved itself through.
        token, identity_source, token_error = self._child_identity(proc, pid)
        if not valid_identity_token(token):
            # Fail closed. The child is cleaned up through the spawn object THIS launcher holds, so
            # no PID is ever re-resolved to reach it, and no runtime record is left behind.
            cleaned = self._discard_child(proc)
            self.ws.log("start.identity_unreadable", pid=_s(pid), reason=_s(token_error),
                        child_cleaned_up=cleaned)
            return self._finish(self._result(
                LAUNCHER_FAILED, phase="start", preflight=pre, pid=pid,
                error_code="CONSOLE_IDENTITY_UNREADABLE", error_detail=_s(token_error),
                identity_source=identity_source, child_cleaned_up=cleaned,
                stale_pid_cleared=stale_pid, runtime_state_written=False,
                owner_message=_owner_message(LAUNCHER_FAILED, "CONSOLE_IDENTITY_UNREADABLE", "",
                                             phase="start")))

        record = {
            "schema_version": PID_SCHEMA, "pid": pid, "process_start_token": token,
            "identity_source": identity_source,
            "host": self.host, "port": validate_port(self.port),
            "command_fingerprint": command_fingerprint(cmd),
            "console_module": CONSOLE_MODULE,
            "workspace_root": _s(self.workspace_root).replace("\\", "/"),
            "started_at": _iso(_now_utc()),
            "launcher_pid": os.getpid(),
            "python_version": ".".join(str(x) for x in sys.version_info[:3]),
            "repository_root_relative": ".",
        }
        self.ws.write_pid(record)
        # Publish the in-flight state before the wait begins, so a concurrent `status` or Open sees
        # "starting" rather than the previous run's result.
        self.ws.write_status(self._result(LAUNCHER_STARTING, phase="health", pid=pid,
                                          timeout_seconds=self.timeout,
                                          generated_at=_iso(_now_utc()),
                                          owner_message=_owner_message(LAUNCHER_STARTING, "", "")))

        # ---- bounded health wait ------------------------------------------------------------------
        waited, health, crashed = self._await_health(proc)
        if crashed:
            self.ws.log("start.console_exited", exit_code=_s(crashed))
            self.ws.clear_pid()
            return self._finish(self._result(
                LAUNCHER_FAILED, phase="health", preflight=pre, pid=pid,
                startup_seconds=round(waited, 2), error_code="CONSOLE_EXITED_DURING_STARTUP",
                error_detail=_s(crashed), stale_pid_cleared=stale_pid,
                owner_message=_owner_message(LAUNCHER_FAILED, "CONSOLE_EXITED_DURING_STARTUP", "")))
        if not health.get("ok"):
            self.ws.log("start.timeout", waited=round(waited, 2), reason=_s(health.get("reason")))
            return self._finish(self._result(
                LAUNCHER_TIMEOUT, phase="health", preflight=pre, pid=pid,
                startup_seconds=round(waited, 2), timeout_seconds=self.timeout,
                error_code="HEALTH_NOT_READY_IN_TIME", error_detail=_s(health.get("reason")),
                health=health, stale_pid_cleared=stale_pid,
                owner_message=_owner_message(LAUNCHER_TIMEOUT, "HEALTH_NOT_READY_IN_TIME", "")))

        # ---- healthy: only NOW may a browser be opened --------------------------------------------
        self._open_after_health(health)
        self.ws.log("start.ready", pid=pid, waited=round(waited, 2), port=self.port,
                    python=".".join(str(x) for x in sys.version_info[:3]),
                    browser_opened=self.browser_opened)
        return self._finish(self._result(
            LAUNCHER_READY, phase="ready", preflight=pre, pid=pid,
            startup_seconds=round(waited, 2), health=health, already_running=False,
            stale_pid_cleared=stale_pid, stale_lock_reclaimed=lock.reclaimed_stale,
            owner_message=_owner_message(LAUNCHER_READY, "", "")))

    def _already_running(self, pre, health, *, locked=False, stale_lock=False):
        self.ws.log("start.already_running", port=self.port, locked=locked)
        self._open_after_health(health)
        return self._finish(self._result(
            LAUNCHER_ALREADY_RUNNING, phase="already-running", preflight=pre, health=health,
            already_running=True, duplicate_start_refused=True, stale_lock_reclaimed=stale_lock,
            owner_message=_owner_message(LAUNCHER_ALREADY_RUNNING, "", "")))

    def _await_health(self, proc):
        """Poll the accepted health endpoint under a bounded timeout. Returns (waited, health,
        crashed_exit_code_or_None). The browser is NEVER opened from here."""
        started = self.clock()
        health = {"ok": False, "reason": "NOT_POLLED"}
        while True:
            poll = getattr(proc, "poll", None)
            code = poll() if callable(poll) else None
            if code is not None:
                return self.clock() - started, health, code
            health = self._health(self.host, self.port)
            if health.get("ok"):
                return self.clock() - started, health, None
            if (self.clock() - started) >= self.timeout:
                return self.clock() - started, health, None
            self.sleep(HEALTH_POLL_INTERVAL)

    def _open_after_health(self, health):
        """Open the browser only when the accepted health endpoint has already reported ready."""
        if not self.open_browser_on_start:
            return
        if not (health or {}).get("ok"):
            return                                # unreachable by construction; belt and braces
        url = console_url(self.host, self.port)
        self.browser_attempted = True
        self.browser_opened = open_browser(url, self.host, self.port, opener=self._browser)
        if not self.browser_opened:
            self.ws.log("browser.open_failed")

    def _child_identity(self, proc, pid):
        """(token, source, error) for the child just spawned. Bounded retry, then fail closed.

        The handle-backed read comes first because it is the only one that cannot describe a
        different process. The raw-PID reader is the fallback for platforms and seams with no handle
        to offer, and it is safe there: on POSIX the unreaped child pins its own PID."""
        error = None
        for attempt in range(START_TOKEN_READ_ATTEMPTS):
            token, error = self._child_token(proc)
            if valid_identity_token(token):
                return token, "popen_handle", None
            token = self._start_token(pid)
            if valid_identity_token(token):
                return token, "process_start_token", None
            error = error or "IDENTITY_TOKEN_UNREADABLE"
            if attempt + 1 < START_TOKEN_READ_ATTEMPTS:
                self.sleep(START_TOKEN_RETRY_SECONDS)
        return None, "unreadable", error

    def _discard_child(self, proc):
        """Stop a child whose identity could not be established, THROUGH the object that owns it.

        No PID is re-resolved and no identity gate is needed: this object refers to exactly the one
        process this launcher created, which is precisely the guarantee a PID lookup cannot give."""
        try:
            kill = getattr(proc, "kill", None)
            if not callable(kill):
                return False
            kill()
            wait = getattr(proc, "wait", None)
            if callable(wait):
                wait(timeout=STOP_GRACE_SECONDS)
            return True
        except Exception:                        # noqa: BLE001 — a failed cleanup is data, not a crash
            return False

    def _clear_stale_pid(self):
        """Drop a PID record that is gone, unverifiable, or has been reused. Never signals.

        Three distinct answers, where the baseline collapsed them into one truthiness test:
          * the process is gone            -> stale, clear it;
          * the RECORDED token is unusable -> no operation can ever verify this record, so keeping
                                              it only strands the owner; clear it;
          * the LIVE token cannot be read  -> proves nothing. The baseline treated this as PID reuse
                                              (`None != recorded` is true) and destroyed the record
                                              on exactly the reading that establishes nothing."""
        rec = self.ws.read_pid()
        if not rec:
            return False
        pid = rec.get("pid")
        if not self._alive(pid):
            self.ws.log("pid.stale_cleared", pid=_s(pid), reason="not_alive")
            self.ws.clear_pid()
            return True
        recorded = rec.get("process_start_token")
        if not valid_identity_token(recorded):
            self.ws.log("pid.stale_cleared", pid=_s(pid), reason="unverifiable_record")
            self.ws.clear_pid()
            return True
        token = self._start_token(pid)
        if not valid_identity_token(token):
            return False                         # unreadable is not reused: keep the record
        if token != recorded:
            self.ws.log("pid.stale_cleared", pid=_s(pid), reason="pid_reused")
            self.ws.clear_pid()
            return True
        return False

    # ---------------------------------------------------------------- stop
    def stop(self):
        self.ws.ensure()
        rec = self.ws.read_pid()
        if not rec:
            health = self._health(self.host, self.port)
            if health.get("ok"):
                # A healthy console exists but this launcher does not own it, so it is not ours to
                # stop. Refusing is the only safe answer.
                self.ws.log("stop.refused_not_launcher_owned")
                return self._finish(self._result(
                    LAUNCHER_STOP_REFUSED, phase="stop", error_code="NOT_LAUNCHER_OWNED",
                    identity_verified=False, signalled=False,
                    owner_message=_owner_message(LAUNCHER_STOP_REFUSED, "NOT_LAUNCHER_OWNED", "",
                                                 phase="stop")))
            self.ws.log("stop.already_stopped")
            return self._finish(self._result(
                LAUNCHER_ALREADY_STOPPED, phase="stop", signalled=False, identity_verified=False,
                owner_message=_owner_message(LAUNCHER_ALREADY_STOPPED, "", "", phase="stop")))

        pid = rec.get("pid")
        if not self._alive(pid):
            self.ws.clear_pid()
            self.ws.log("stop.stale_pid_removed", pid=_s(pid))
            return self._finish(self._result(
                LAUNCHER_ALREADY_STOPPED, phase="stop", pid=pid, signalled=False,
                identity_verified=False, stale_pid_cleared=True,
                owner_message=_owner_message(LAUNCHER_ALREADY_STOPPED, "", "", phase="stop")))

        recorded = rec.get("process_start_token")

        # ---- the pinned handle is opened FIRST -------------------------------------------------
        # Before the health probe, before any other delay and before anything at all is signalled.
        # Everything below reasons about the process object this handle refers to, never about
        # whatever process the raw PID happens to resolve to later. The accepted baseline opened it
        # only after the identity check AND after the health probe, leaving that whole interval
        # unpinned; the independent audit used exactly that interval to have an unrelated process
        # terminated. The handle is held until this method returns.
        self.terminate_requests = []
        verifier = self._exit_verifier(pid)
        try:
            code, pinned_token, identity = self._pinned_identity(pid, recorded, verifier)
            if code:
                # Fail closed. Nothing is signalled, nothing is terminated, and NO runtime state is
                # cleared: a refusal must not destroy the record the next Stop needs, and clearing
                # it here would act on an identity that was never proven. Start's stale-PID sweep
                # is what reclaims a genuinely stale record.
                self.ws.log("stop.refused_pid_reused"
                            if code == "PID_REUSED_BY_ANOTHER_PROCESS"
                            else "stop.refused_identity_unproven", pid=_s(pid))
                return self._finish(self._result(
                    LAUNCHER_STOP_REFUSED, phase="stop", pid=pid, signalled=False,
                    identity_verified=False, error_code=code, process_identity=identity,
                    stale_pid_cleared=False, runtime_state_cleared=False,
                    # Recorded empty, so "nothing was asked of the OS" is provable from the record
                    # itself rather than inferred from the absence of a field.
                    terminate_requests=list(self.terminate_requests),
                    owner_message=_owner_message(LAUNCHER_STOP_REFUSED, code, "", phase="stop")))

            # Command identity, where practical. DIAGNOSTIC ONLY: it authorizes nothing. The accepted
            # health contract answering on the recorded port is the strongest evidence available
            # without a new dependency — and it is the ONLY positive evidence. The accepted baseline
            # also reported "verified" whenever the probe failed to connect at all (`not http_status`
            # is true for a transport error), so an unreachable console was recorded as an identity
            # proof; the owner's 2026-07-30 record shows exactly that. Silence proves nothing and is
            # no longer counted as proof.
            #
            # This probe runs AFTER the pinned identity check, never before it. It can block for
            # HEALTH_REQUEST_TIMEOUT (3.0 s) — longest precisely when the console is unhealthy or
            # unresponsive, i.e. when the recorded process is most likely to be exiting — and in the
            # baseline order that delay sat between the identity check and the handle.
            health = self._health(rec.get("host") or self.host, rec.get("port") or self.port)
            command_verified = bool(health.get("ok"))
            command_evidence = ("ACCEPTED_HEALTH_CONTRACT" if command_verified else
                                ("FOREIGN_HTTP_RESPONDER" if health.get("http_status")
                                 else "HEALTH_UNREACHABLE"))

            self._request_terminate(pid, hard=False, expect_token=pinned_token)
            self.ws.log("stop.signalled", pid=_s(pid), hard=False,
                        requested=self.terminate_requests[-1]["ok"])
            waited, exit_state, evidence = self._await_exit(
                pid, min(STOP_GRACE_SECONDS, self.stop_timeout), verifier)
            escalated = False
            hard_ok = None
            if exit_state != EXIT_STATE_EXITED:
                escalated = True
                hard_ok = self._request_terminate(pid, hard=True, expect_token=pinned_token)
                self.ws.log("stop.escalated", pid=_s(pid), hard=True, requested=hard_ok)
                more, exit_state, evidence = self._await_exit(
                    pid, max(0.0, self.stop_timeout - waited), verifier)
                waited += more

            # Supporting diagnostics only. Neither may authorize a termination or a success: a
            # closed port does not prove a stopped process, and an open one does not prove a
            # running console.
            port_open = bool(self._port_probe(self.host, self.port))
            after_health = self._health(self.host, self.port)
            terminate_failed = bool(hard_ok is False)
            common = dict(
                pid=pid, signalled=True, identity_verified=True, process_identity=identity,
                command_identity_verified=command_verified,
                command_identity_evidence=command_evidence,
                escalated=escalated, stop_seconds=round(waited, 2), exit_state=exit_state,
                exit_verification=evidence, terminate_requests=list(self.terminate_requests),
                termination_request_failed=terminate_failed,
                port_open_after_stop=port_open,
                health_reachable_after_stop=bool(after_health.get("http_status") is not None),
            )

            if exit_state == EXIT_STATE_EXITED:
                # Runtime state is cleared ONLY here, on the proven-exit path.
                cleared = self.ws.clear_pid()
                stop_state = STOP_STATE_EXITED if cleared else STOP_STATE_EXITED_STALE_STATE
                self.ws.log("stop.stopped", pid=_s(pid), waited=round(waited, 2),
                            escalated=escalated, exit_state=exit_state, stop_state=stop_state)
                return self._finish(self._result(
                    LAUNCHER_STOPPED, phase="stop", stop_state=stop_state,
                    runtime_state_cleared=cleared, **common,
                    owner_message=_owner_message(LAUNCHER_STOPPED, "", "", phase="stop",
                                                 stop_state=stop_state)))

            # Not proven exited: the PID record is deliberately LEFT IN PLACE so the next Stop still
            # knows exactly which process it may signal, and nothing else is ever touched.
            if exit_state == EXIT_STATE_RUNNING:
                code = "CONSOLE_DID_NOT_STOP"
                stop_state = (STOP_STATE_TERMINATE_FAILED if terminate_failed else
                              (STOP_STATE_PORT_CLOSED_ALIVE if not port_open else STOP_STATE_ALIVE))
                self.ws.log("stop.still_running", pid=_s(pid), waited=round(waited, 2),
                            stop_state=stop_state)
            else:
                code = "CONSOLE_EXIT_NOT_PROVEN"
                stop_state = STOP_STATE_TERMINATE_FAILED if terminate_failed else STOP_STATE_UNPROVEN
                self.ws.log("stop.exit_unproven", pid=_s(pid), waited=round(waited, 2),
                            stop_state=stop_state, reason=_s(evidence.get("api_error")))
            return self._finish(self._result(
                LAUNCHER_FAILED, phase="stop", stop_state=stop_state, runtime_state_cleared=False,
                error_code=code, **common,
                owner_message=_owner_message(LAUNCHER_FAILED, code, "", phase="stop")))
        finally:
            # Held for the COMPLETE stop operation, so the pinned PID cannot be recycled onto an
            # unrelated program at any point between identity validation and the final answer.
            if verifier is not None:
                verifier.close()

    def _pinned_identity(self, pid, recorded, verifier):
        """Prove — through the handle held for the whole stop — that this PID is still the recorded
        process. Returns (error_code_or_None, token_to_authorize_with, evidence).

        Three tokens have to agree before anything may be signalled: the one RECORDED at start, the
        one read back through the PINNED HANDLE, and (on the hard path) the one read through the
        TERMINATION handle itself. This method settles the first two; `terminate_process_result`
        settles the third against the token returned here.

        Every failure direction is fail-closed, and "cannot prove it" is decided BEFORE "does not
        match", so an unreadable identity is never misreported as PID reuse."""
        pinned = bool(verifier is not None and getattr(verifier, "usable", False))
        handle_token = getattr(verifier, "start_token", None) if pinned else None
        handle_error = getattr(verifier, "token_error", None) if verifier is not None else None
        process_token = self._start_token(pid)
        recorded_ok = valid_identity_token(recorded)
        ev = {"recorded_token_present": bool(recorded), "recorded_token_valid": recorded_ok,
              "handle_pinned": pinned,
              "handle_token_read": handle_token is not None,
              "handle_token_matches_recorded": bool(recorded_ok and handle_token == recorded),
              "process_token_matches_recorded": bool(recorded_ok and process_token == recorded),
              "handle_identity_required": self._handle_identity_required,
              "api_error": handle_error}
        if not recorded_ok:
            # Nothing to compare against, so nothing can be proven. The accepted baseline returned
            # AUTHORIZED here, which short-circuited every check below it — including the pinned
            # handle — and let a stop terminate whatever the recorded PID currently pointed at. It
            # also handed the live handle's own token to the hard path as `expect_token`, so the
            # termination-handle check validated that handle against itself and passed.
            ev["authorized_by"] = None
            return "PROCESS_IDENTITY_UNPROVEN", None, ev
        if self._handle_identity_required and not pinned:
            ev["authorized_by"] = None
            return "PROCESS_IDENTITY_UNPROVEN", None, ev
        if pinned and not valid_identity_token(handle_token):
            ev["authorized_by"] = None
            return "PROCESS_IDENTITY_UNPROVEN", None, ev
        if not valid_identity_token(process_token):
            ev["authorized_by"] = None
            return "PROCESS_IDENTITY_UNPROVEN", None, ev
        if pinned and handle_token != recorded:
            # The handle we hold refers to a DIFFERENT process than the one recorded. Never signal.
            ev["authorized_by"] = None
            return "PID_REUSED_BY_ANOTHER_PROCESS", None, ev
        if process_token != recorded:
            ev["authorized_by"] = None
            return "PID_REUSED_BY_ANOTHER_PROCESS", None, ev
        ev["authorized_by"] = "PINNED_HANDLE_TOKEN" if pinned else "PROCESS_START_TOKEN"
        return None, handle_token, ev

    def _request_terminate(self, pid, hard, expect_token=None):
        """Ask the OS to stop ONE identity-verified process and RECORD what it answered.

        The accepted baseline discarded this result, so a refused request was indistinguishable from
        an accepted one. Both dict-returning and bool-returning seams are accepted.

        `expect_token` is handed to the real process layer so the HARD path re-verifies identity
        through its own termination handle before killing anything. The graceful path signals a
        console process GROUP by number and has no handle form; it is safe because it is issued only
        after the pinned handle is open and its token has matched, and Windows cannot recycle a PID
        while that handle is held."""
        kw = {"hard": hard}
        if expect_token is not None and hard and self._terminate_validates_identity:
            kw["expect_token"] = expect_token
        try:
            res = self._terminate(pid, **kw)
        except Exception as e:                   # noqa: BLE001 — a refusal is data, not a crash
            res = {"ok": False, "error": type(e).__name__}
        if isinstance(res, dict):
            record = {"hard": bool(hard), "ok": bool(res.get("ok")),
                      "api": res.get("api"), "error": res.get("error"),
                      "identity_checked": bool(res.get("identity_checked")),
                      "identity_verified": res.get("identity_verified")}
        else:
            record = {"hard": bool(hard), "ok": bool(res), "api": None, "error": None,
                      "identity_checked": False, "identity_verified": None}
        self.terminate_requests.append(record)
        return record["ok"]

    def _exit_state(self, pid, verifier):
        """What can be PROVEN about this process right now: EXITED, RUNNING or UNPROVEN."""
        if verifier is not None and getattr(verifier, "usable", False):
            ev = verifier.state()
            return ev.get("exit_state", EXIT_STATE_UNPROVEN), ev
        if not self._alive(pid):
            # Conclusive on every platform: the PID no longer resolves to a process object at all.
            return EXIT_STATE_EXITED, {"source": self._exit_source, "process_alive": False}
        if self._alive_authoritative:
            return EXIT_STATE_RUNNING, {"source": self._exit_source, "process_alive": True}
        # Real Windows with no usable handle: "still openable" is not proof of life. Fail closed.
        return EXIT_STATE_UNPROVEN, {"source": self._exit_source, "process_alive": True,
                                     "api_error": "OPEN_PROCESS_TRUE_IS_NOT_PROOF_OF_LIFE"}

    def _await_exit(self, pid, budget, verifier=None):
        """Poll for a PROVEN exit under a bounded budget. Returns (waited, exit_state, evidence).

        The loop is bounded by the owner's budget and never extended to hide an unproven state. An
        exit that lands during the final sleep is still reported as an exit, because the state is
        re-read at the top of the loop before the budget is re-checked."""
        started = self.clock()
        while True:
            state, evidence = self._exit_state(pid, verifier)
            if state == EXIT_STATE_EXITED:
                return self.clock() - started, EXIT_STATE_EXITED, evidence
            if (self.clock() - started) >= budget:
                return self.clock() - started, state, evidence
            self.sleep(STOP_POLL_INTERVAL)

    # ---------------------------------------------------------------- open
    def open(self):
        """Open never starts anything. It checks health and either opens the browser or explains."""
        health = self._health(self.host, self.port)
        if not health.get("ok"):
            return self._finish(self._result(
                LAUNCHER_NOT_RUNNING, phase="open", health=health, started_a_server=False,
                error_code="CONSOLE_NOT_HEALTHY", error_detail=_s(health.get("reason")),
                owner_message=_owner_message(LAUNCHER_NOT_RUNNING, "CONSOLE_NOT_HEALTHY", "")))
        self._open_after_health(health)
        readiness = LAUNCHER_ALREADY_RUNNING if self.browser_opened else LAUNCHER_BROWSER_UNAVAILABLE
        return self._finish(self._result(
            readiness, phase="open", health=health, started_a_server=False,
            owner_message=_owner_message(readiness, "", "")))

    # ---------------------------------------------------------------- status
    def status(self):
        health = self._health(self.host, self.port)
        rec = self.ws.read_pid()
        # Ownership is a VERIFIED claim or it is not made. The baseline only compared tokens when
        # the recorded one was truthy, so a null token reported `launcher_owned: true` — and a
        # single session could then report the record owned here and refuse to stop it there.
        recorded = (rec or {}).get("process_start_token")
        verified = False
        if rec and self._alive(rec.get("pid")) and valid_identity_token(recorded):
            live = self._start_token(rec.get("pid"))
            verified = valid_identity_token(live) and live == recorded
        readiness = LAUNCHER_ALREADY_RUNNING if health.get("ok") else LAUNCHER_NOT_RUNNING
        return self._finish(self._result(
            readiness, phase="status", health=health, launcher_owned=verified,
            identity_verified=verified, pid=(rec or {}).get("pid"),
            owner_message=_owner_message(readiness, "", "")), write=False)

    def _finish(self, result, *, write=True):
        result["generated_at"] = _iso(_now_utc())
        if write:
            self.ws.write_status(result)
        return result


# ================================================================ owner-facing messages
# Owner-facing text for the stop path, chosen by the canonical error_code. A stop that fails must
# never describe itself as a start: LAUNCHER_FAILED is shared by both phases, so the readiness state
# alone cannot name the operation. The machine-readable contract is untouched — readiness and
# error_code keep the exact values the accepted Phase 7.14 baseline records, and only the sentence
# the owner reads is phase-accurate. Nothing here changes what stop does.
#
# Phase 7.14 stop-exit-verification hotfix: the owner reads what was PROVEN, never a raw code. Four
# sentences cover the six machine states — stopped, stopped-but-record-stale, still running, and
# "could not confirm".
#
# Capitalization is deliberately ONE form, "The toolkit", matching every accepted owner sentence in
# this module. A single Stop session can print a new sentence and a baseline one back to back (stop,
# then stop again), so a second capitalization would be visible to the owner as an inconsistency.
STOP_SUCCESS_MESSAGE = "The toolkit stopped safely."
STOP_EXITED_STALE_STATE_MESSAGE = ("The toolkit stopped, but its local runtime record could not be "
                                   "cleaned up. Nothing else on this computer was stopped. Open "
                                   "technical details before starting it again.")
STOP_STILL_RUNNING_MESSAGE = ("The toolkit is still running. Nothing else on this computer was "
                              "stopped. Open technical details for the recorded reason.")
STOP_EXIT_UNPROVEN_MESSAGE = ("The toolkit could not confirm that the local server stopped safely. "
                              "Nothing else on this computer was stopped. Open technical details "
                              "for the recorded reason.")

_OWNER_MESSAGES = {
    LAUNCHER_STARTING: "The toolkit is starting. Waiting until it is ready before opening a browser.",
    LAUNCHER_READY: "The toolkit is running. Your browser should now be open on the console.",
    LAUNCHER_ALREADY_RUNNING: "The toolkit was already running, so a second copy was not started.",
    # Routed to the one canonical success sentence, so there is exactly one of them in the module
    # and no unreachable duplicate can drift away from what stop actually prints.
    LAUNCHER_STOPPED: STOP_SUCCESS_MESSAGE,
    LAUNCHER_ALREADY_STOPPED: "The toolkit was not running, so there was nothing to stop.",
    LAUNCHER_TIMEOUT: ("The toolkit started but did not become ready in time. Run Stop-AMZ-Toolkit, "
                       "then try Start-AMZ-Toolkit once more."),
    LAUNCHER_PORT_BLOCKED: (PORT_IN_USE_MESSAGE + " — another program on this computer is using it. "
                            "Close that program, then run Start-AMZ-Toolkit again."),
    LAUNCHER_PYTHON_REQUIRED: ("A supported Python was not found. Install Python 3.9 or newer, then "
                               "run Start-AMZ-Toolkit again."),
    LAUNCHER_MODULE_REQUIRED: ("This folder does not contain the toolkit console. Run "
                               "Start-AMZ-Toolkit from inside the toolkit folder."),
    LAUNCHER_WORKSPACE_REQUIRED: ("The toolkit could not write to its own runtime folder. Check that "
                                  "the toolkit folder is not read-only."),
    LAUNCHER_LOCKED: "The toolkit is already starting. Wait a few seconds, then try again.",
    LAUNCHER_NOT_RUNNING: "The toolkit is not running yet. Run Start-AMZ-Toolkit first.",
    LAUNCHER_STOP_REFUSED: ("Stop refused: the recorded process is not the console this launcher "
                            "started, so nothing was stopped."),
    LAUNCHER_BROWSER_UNAVAILABLE: ("The toolkit is running but a browser could not be opened. Open "
                                   "this address yourself: "),
    LAUNCHER_FAILED: "The toolkit could not be started. See the launcher log for the recorded reason.",
}

_STOP_OWNER_MESSAGES = {
    "CONSOLE_DID_NOT_STOP": STOP_STILL_RUNNING_MESSAGE,
    "CONSOLE_EXIT_NOT_PROVEN": STOP_EXIT_UNPROVEN_MESSAGE,
    # Recovery guidance lives HERE, in what Stop prints to the console window, and not in the web
    # panel: the panel is unreachable in exactly the situation this sentence describes.
    "PROCESS_IDENTITY_UNPROVEN": ("The toolkit was not stopped because the launcher could not confirm "
                                  "which process it is. Nothing on this computer was stopped. Close "
                                  "the toolkit window yourself, or end its task in Task Manager, then "
                                  "run Start-AMZ-Toolkit again."),
    "PID_REUSED_BY_ANOTHER_PROCESS": ("The process was not stopped because it was not started by this "
                                      "launcher. The recorded process number now belongs to a "
                                      "different program, so nothing was stopped."),
    "NOT_LAUNCHER_OWNED": ("The process was not stopped because it was not started by this launcher. "
                           "A console is answering on this port, but this launcher did not start it, "
                           "so nothing was stopped."),
}
STOP_FAILED_MESSAGE = "The toolkit could not be stopped. See the launcher log for the recorded reason."

# Start-phase text keyed by error code, mirroring the stop table. A start that cannot identify the
# process it just created stops that process again, so the owner is told the machine was left clean
# and given the one action that resolves it — never a code, and never a false "it is running".
_START_OWNER_MESSAGES = {
    "CONSOLE_IDENTITY_UNREADABLE": ("The toolkit could not be started safely: the launcher could not "
                                    "confirm which process it had just created, so it closed that "
                                    "process again. Nothing was left behind. Run Start-AMZ-Toolkit "
                                    "once more."),
}

# A proven exit is still a success, but it is not the SAME success when the launcher could not clean
# up its own runtime record: the owner has to know before starting again, so that state gets its own
# qualified sentence instead of the unqualified one.
_STOP_STATE_OWNER_MESSAGES = {
    STOP_STATE_EXITED: STOP_SUCCESS_MESSAGE,
    STOP_STATE_EXITED_STALE_STATE: STOP_EXITED_STALE_STATE_MESSAGE,
}


def _owner_message(readiness, code, detail, phase=None, stop_state=None):
    # Start-specific text is scoped the same way stop's is, so no existing start or open sentence
    # changes: only a call that passes phase="start" AND a mapped code can reach this table.
    if phase == "start":
        specific = _START_OWNER_MESSAGES.get(code)
        if specific:
            return specific
    # Stop-specific text is scoped to the stop phase, so start and open wording cannot change.
    if phase == "stop":
        specific = _STOP_OWNER_MESSAGES.get(code)
        if specific:
            return specific
        by_state = _STOP_STATE_OWNER_MESSAGES.get(stop_state)
        if by_state:
            return by_state
        if readiness == LAUNCHER_STOPPED:
            return STOP_SUCCESS_MESSAGE
        if readiness == LAUNCHER_FAILED:
            return STOP_FAILED_MESSAGE
    base = _OWNER_MESSAGES.get(readiness, "")
    if readiness == LAUNCHER_BROWSER_UNAVAILABLE:
        return base + console_url(DEFAULT_HOST, DEFAULT_PORT)
    return base


# ================================================================ validate-only (no side effects)
def validate_only(*, root=None, host=DEFAULT_HOST, port=DEFAULT_PORT,
                  workspace_root=DEFAULT_WORKSPACE_ROOT):
    """Structural validation with ZERO side effects: no directory is created, no file is written, no
    lock is taken, no port is probed, no process is spawned or signalled, no browser is opened and no
    network request of any kind is made."""
    checks = [check_python(), check_repository(root), pilot_readiness(root)]
    try:
        cmd = console_command(python_exe="python", workspace_root=workspace_root, host=host, port=port)
        cmd_ok, cmd_fp = True, command_fingerprint(cmd)
    except LauncherError as e:
        cmd_ok, cmd_fp = False, e.code
    checks.append({"check": "console_command_fixed", "ok": cmd_ok, "command": cmd_fp})
    checks.append({"check": "loopback_host_only", "ok": is_loopback_host(host), "host": host})
    checks.append({"check": "port_fixed_not_random", "ok": AUTOMATIC_PORT_SELECTION is False,
                   "port": validate_port(port)})
    url = console_url(host, port)
    checks.append({"check": "browser_url_allowlisted", "ok": is_allowed_url(url, host, port),
                   "url": url})
    checks.append({"check": "seller_central_counters_zero",
                   "ok": all(v == 0 for v in SELLER_CENTRAL_COUNTERS.values())})
    readiness = LAUNCHER_READY if all(c["ok"] for c in checks) else LAUNCHER_MODULE_REQUIRED
    if not checks[0]["ok"]:
        readiness = LAUNCHER_PYTHON_REQUIRED
    pilot = pilot_readiness(root)
    return {
        "schema_version": "phase7-14-launcher-validate-v1", "stage_id": STAGE_ID,
        "readiness": readiness, "ok": all(c["ok"] for c in checks), "checks": checks,
        "pilot_readiness": pilot["readiness"], "pilot_kit_missing": pilot["missing"],
        "files_written": 0, "directories_created": 0, "processes_spawned": 0,
        "processes_signalled": 0, "browsers_opened": 0, "network_requests": 0, "ports_probed": 0,
        "locks_taken": 0, "seller_central_counters": dict(SELLER_CENTRAL_COUNTERS),
    }


# ================================================================ CLI
def exit_code_for(readiness):
    return 0 if readiness in _OK_STATES else 1


def build_arg_parser():
    p = argparse.ArgumentParser(
        prog="python -m production.phase7_owner_launcher",
        description="Launcher Lite for the accepted Phase 7.13 Unified Owner Console. Starts, stops "
                    "or opens ONE local loopback console. It never registers a service or a "
                    "scheduler, never opens a non-loopback address, and never performs any Amazon "
                    "Seller Central action.")
    p.add_argument("--host", default=DEFAULT_HOST, help="loopback bind host (default 127.0.0.1).")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"fixed port (default {DEFAULT_PORT}).")
    p.add_argument("--workspace-root", default=DEFAULT_WORKSPACE_ROOT,
                   help="repository-relative root holding the phase 7.3-7.13 workspaces.")
    p.add_argument("--timeout", type=float, default=START_TIMEOUT_SECONDS,
                   help=f"bounded startup wait in seconds (default {START_TIMEOUT_SECONDS}).")
    p.add_argument("--stop-timeout", type=float, default=STOP_TIMEOUT_SECONDS,
                   help=f"bounded shutdown wait in seconds (default {STOP_TIMEOUT_SECONDS}).")
    p.add_argument("--no-browser", action="store_true",
                   help="do everything except open the browser.")
    p.add_argument("--json", action="store_true", help="print the full result document as JSON.")
    p.add_argument("command", choices=("start", "stop", "open", "status", "validate-only"),
                   help="start / stop / open the console, report status, or validate-only.")
    return p


_PRINT_KEYS = ("readiness", "console_url", "pid", "startup_seconds", "stop_seconds",
               "browser_opened", "already_running", "exit_state", "stop_state", "error_code")


def main(argv=None):
    a = build_arg_parser().parse_args(argv)
    try:
        validate_host(a.host)
        validate_port(a.port)
    except LauncherError as e:
        print(f"{e.code}: {e.detail}", file=sys.stderr)
        return 2

    if a.command == "validate-only":
        res = validate_only(host=a.host, port=a.port, workspace_root=a.workspace_root)
        if a.json:
            print(json.dumps(res, indent=2, sort_keys=True))
        else:
            print("\n".join(f"{k}={res[k]}" for k in ("readiness", "ok", "files_written",
                                                      "directories_created", "processes_spawned",
                                                      "network_requests", "browsers_opened")))
        return exit_code_for(res["readiness"])

    launcher = Launcher(host=a.host, port=a.port, workspace_root=a.workspace_root,
                        timeout=a.timeout, stop_timeout=a.stop_timeout,
                        open_browser_on_start=not a.no_browser)
    res = {"start": launcher.start, "stop": launcher.stop, "open": launcher.open,
           "status": launcher.status}[a.command]()
    if a.json:
        print(json.dumps(res, indent=2, sort_keys=True))
    else:
        for k in _PRINT_KEYS:
            if k in res and res[k] is not None:
                print(f"{k}={res[k]}")
        if res.get("port_message"):
            print(res["port_message"])
        if res.get("owner_message"):
            print("")
            print(res["owner_message"])
    return exit_code_for(res["readiness"])


if __name__ == "__main__":
    raise SystemExit(main())
