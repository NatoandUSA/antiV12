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
    """True when a process with this PID currently exists. Never matches by name."""
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


def terminate_process(pid, *, hard=False):
    """Signal ONE already-identity-verified process. Callers must verify identity first; this
    function never searches for a process, never matches a name and never touches a process tree."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if not hard:
            # Graceful: the console is spawned as its own process-group leader, so a console
            # break reaches exactly that process and it shuts the server down cleanly.
            try:
                os.kill(pid, signal.CTRL_BREAK_EVENT)
                return True
            except (OSError, AttributeError, ValueError):
                return False
        PROCESS_TERMINATE = 0x0001
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        handle = k32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if not handle:
            return False
        try:
            return bool(k32.TerminateProcess(handle, 1))
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, signal.SIGKILL if hard else signal.SIGTERM)
        return True
    except OSError:
        return False


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
                 alive=None, start_token=None, terminate=None, python_exe=None):
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
        self._terminate = terminate or terminate_process
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
        token = self._start_token(pid)
        record = {
            "schema_version": PID_SCHEMA, "pid": pid, "process_start_token": token,
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

    def _clear_stale_pid(self):
        """Drop a PID record whose process is gone or whose PID has been reused. Never signals."""
        rec = self.ws.read_pid()
        if not rec:
            return False
        pid = rec.get("pid")
        if not self._alive(pid):
            self.ws.log("pid.stale_cleared", pid=_s(pid), reason="not_alive")
            self.ws.clear_pid()
            return True
        token = self._start_token(pid)
        if rec.get("process_start_token") and token != rec.get("process_start_token"):
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
        current = self._start_token(pid)
        # "cannot prove it" is checked BEFORE "does not match", so an unreadable identity is never
        # misreported as PID reuse — and either way nothing is signalled.
        if recorded and current is None:
            self.ws.log("stop.refused_identity_unproven", pid=_s(pid))
            return self._finish(self._result(
                LAUNCHER_STOP_REFUSED, phase="stop", pid=pid, signalled=False,
                identity_verified=False, error_code="PROCESS_IDENTITY_UNPROVEN",
                owner_message=_owner_message(LAUNCHER_STOP_REFUSED, "PROCESS_IDENTITY_UNPROVEN", "",
                                             phase="stop")))
        if recorded and current != recorded:
            # The PID is alive but it is a DIFFERENT process now (PID reuse). Never signal it.
            self.ws.clear_pid()
            self.ws.log("stop.refused_pid_reused", pid=_s(pid))
            return self._finish(self._result(
                LAUNCHER_STOP_REFUSED, phase="stop", pid=pid, signalled=False,
                identity_verified=False, error_code="PID_REUSED_BY_ANOTHER_PROCESS",
                stale_pid_cleared=True,
                owner_message=_owner_message(LAUNCHER_STOP_REFUSED, "PID_REUSED_BY_ANOTHER_PROCESS",
                                             "", phase="stop")))

        # Command identity, where practical: the console still answering on the recorded port with
        # the accepted health contract is the strongest evidence available without a dependency.
        health = self._health(rec.get("host") or self.host, rec.get("port") or self.port)
        command_verified = bool(health.get("ok")) or not health.get("http_status")

        self._terminate(pid, hard=False)
        self.ws.log("stop.signalled", pid=_s(pid), hard=False)
        waited, stopped = self._await_exit(pid, min(STOP_GRACE_SECONDS, self.stop_timeout))
        escalated = False
        if not stopped:
            escalated = True
            self._terminate(pid, hard=True)
            self.ws.log("stop.escalated", pid=_s(pid), hard=True)
            more, stopped = self._await_exit(pid, max(0.0, self.stop_timeout - waited))
            waited += more

        if stopped:
            self.ws.clear_pid()
            self.ws.log("stop.stopped", pid=_s(pid), waited=round(waited, 2), escalated=escalated)
            return self._finish(self._result(
                LAUNCHER_STOPPED, phase="stop", pid=pid, signalled=True, identity_verified=True,
                command_identity_verified=command_verified, escalated=escalated,
                stop_seconds=round(waited, 2),
                owner_message=_owner_message(LAUNCHER_STOPPED, "", "", phase="stop")))
        self.ws.log("stop.still_running", pid=_s(pid), waited=round(waited, 2))
        return self._finish(self._result(
            LAUNCHER_FAILED, phase="stop", pid=pid, signalled=True, identity_verified=True,
            command_identity_verified=command_verified, escalated=escalated,
            stop_seconds=round(waited, 2), error_code="CONSOLE_DID_NOT_STOP",
            owner_message=_owner_message(LAUNCHER_FAILED, "CONSOLE_DID_NOT_STOP", "", phase="stop")))

    def _await_exit(self, pid, budget):
        started = self.clock()
        while True:
            if not self._alive(pid):
                return self.clock() - started, True
            if (self.clock() - started) >= budget:
                return self.clock() - started, False
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
        owned = bool(rec) and self._alive(rec.get("pid"))
        if owned and rec.get("process_start_token"):
            owned = self._start_token(rec.get("pid")) == rec.get("process_start_token")
        readiness = LAUNCHER_ALREADY_RUNNING if health.get("ok") else LAUNCHER_NOT_RUNNING
        return self._finish(self._result(
            readiness, phase="status", health=health, launcher_owned=owned,
            pid=(rec or {}).get("pid"),
            owner_message=_owner_message(readiness, "", "")), write=False)

    def _finish(self, result, *, write=True):
        result["generated_at"] = _iso(_now_utc())
        if write:
            self.ws.write_status(result)
        return result


# ================================================================ owner-facing messages
_OWNER_MESSAGES = {
    LAUNCHER_STARTING: "The toolkit is starting. Waiting until it is ready before opening a browser.",
    LAUNCHER_READY: "The toolkit is running. Your browser should now be open on the console.",
    LAUNCHER_ALREADY_RUNNING: "The toolkit was already running, so a second copy was not started.",
    LAUNCHER_STOPPED: "The toolkit has stopped.",
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

# Owner-facing text for the stop path, chosen by the canonical error_code. A stop that fails must
# never describe itself as a start: LAUNCHER_FAILED is shared by both phases, so the readiness state
# alone cannot name the operation. The machine-readable contract is untouched — readiness and
# error_code keep the exact values the accepted Phase 7.14 baseline records, and only the sentence
# the owner reads is phase-accurate. Nothing here changes what stop does.
_STOP_OWNER_MESSAGES = {
    "CONSOLE_DID_NOT_STOP": ("The toolkit did not stop within the allowed time. Nothing else on this "
                             "computer was stopped. See the launcher log for the recorded reason."),
    "PROCESS_IDENTITY_UNPROVEN": ("The toolkit was not stopped because the launcher could not safely "
                                  "verify the process identity. Nothing on this computer was stopped."),
    "PID_REUSED_BY_ANOTHER_PROCESS": ("The process was not stopped because it was not started by this "
                                      "launcher. The recorded process number now belongs to a "
                                      "different program, so nothing was stopped."),
    "NOT_LAUNCHER_OWNED": ("The process was not stopped because it was not started by this launcher. "
                           "A console is answering on this port, but this launcher did not start it, "
                           "so nothing was stopped."),
}
STOP_FAILED_MESSAGE = "The toolkit could not be stopped. See the launcher log for the recorded reason."


def _owner_message(readiness, code, detail, phase=None):
    # Stop-specific text is scoped to the stop phase, so start and open wording cannot change.
    if phase == "stop":
        specific = _STOP_OWNER_MESSAGES.get(code)
        if specific:
            return specific
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
               "browser_opened", "already_running", "error_code")


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
