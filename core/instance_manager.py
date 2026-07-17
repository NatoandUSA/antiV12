#!/usr/bin/env python3
"""Single-instance lifecycle authority (Session 5C / Part C, D, E, F).

One shared way to start, stop, inspect, and recover the *existing* local dashboard
so the toolkit runs as exactly one verified instance on loopback. It never adds a
second server, never binds beyond loopback, and — the central safety rule — never
terminates a process it has not positively identified as this toolkit.

Identity is never trusted from a PID file alone. Before a process is treated as
"ours" (and certainly before it is stopped) we corroborate, as available:
    - the recorded PID is alive;
    - its process start time matches (guards against a reused PID);
    - the configured loopback port is actually being served;
    - GET /healthz self-reports service == "amz-fbm-toolkit";
    - the health payload's pid and instance_id match our runtime metadata.

When identity cannot be verified the state is reported (PORT_IN_USE_BY_UNKNOWN_
PROCESS / INSTANCE_IDENTITY_UNVERIFIED) and the process is preserved untouched.

Reused Session 5B authorities: runtime_policy (bind/offline), subprocess_runner
(bounded taskkill of only the verified tree), diagnostics (secret-safe events).
The detached server spawn is intentionally NOT run through subprocess_runner —
that runner is for bounded, captured, run-to-completion calls; a long-lived
detached daemon is a different concern handled here with explicit detach flags.
"""
import hashlib
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from urllib.parse import urlparse

import app_paths as AP
import diagnostics as D
import runtime_policy as RP
import subprocess_runner as SR

SERVICE_NAME = "amz-fbm-toolkit"
INSTANCE_SCHEMA_VERSION = "1.0"

# ---- status vocabulary (Part F) ----------------------------------------------
RUNNING_HEALTHY = "RUNNING_HEALTHY"
RUNNING_UNHEALTHY = "RUNNING_UNHEALTHY"
STOPPED = "STOPPED"
STALE_RUNTIME_STATE = "STALE_RUNTIME_STATE"
PORT_IN_USE_BY_UNKNOWN_PROCESS = "PORT_IN_USE_BY_UNKNOWN_PROCESS"
INSTANCE_IDENTITY_UNVERIFIED = "INSTANCE_IDENTITY_UNVERIFIED"
INVALID_RUNTIME_POLICY = "INVALID_RUNTIME_POLICY"

# action outcomes (not persistent states)
ALREADY_RUNNING = "ALREADY_RUNNING"
STARTED = "STARTED"
START_FAILED = "START_FAILED"
STOPPED_OK = "STOPPED_OK"
STOP_FAILED = "STOP_FAILED"
NOT_RUNNING = "NOT_RUNNING"
RESTART_ABORTED = "RESTART_ABORTED"

_IS_WINDOWS = os.name == "nt"

# ==============================================================================
# Low-level process probes (module-level so tests can monkeypatch them).
# On Windows these use ctypes only — no psutil, no third-party dependency, and
# CRUCIALLY never os.kill(pid, 0), which on Windows would TERMINATE the process.
# ==============================================================================
if _IS_WINDOWS:
    try:
        import ctypes
        from ctypes import wintypes

        _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _PROC_QUERY_LIMITED = 0x1000
        _STILL_ACTIVE = 259
        _ERROR_ACCESS_DENIED = 5

        _k32.OpenProcess.restype = wintypes.HANDLE
        _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        _k32.CloseHandle.argtypes = [wintypes.HANDLE]
        _k32.GetExitCodeProcess.restype = wintypes.BOOL
        _k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                            ctypes.POINTER(wintypes.DWORD)]
        _k32.GetProcessTimes.restype = wintypes.BOOL
        _k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + \
            [ctypes.POINTER(wintypes.FILETIME)] * 4
        _k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        _k32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD)]
        _CTYPES_OK = True
    except Exception:      # pragma: no cover - defensive, keeps import safe
        _CTYPES_OK = False
else:
    _CTYPES_OK = False


def _win_open(pid):
    h = _k32.OpenProcess(_PROC_QUERY_LIMITED, False, int(pid))
    return h if h else None


def probe_pid_alive(pid):
    """True when *pid* is a currently-running process. Best effort, safe."""
    if pid is None:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if _IS_WINDOWS and _CTYPES_OK:
        h = _win_open(pid)
        if not h:
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
        try:
            code = wintypes.DWORD()
            if _k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == _STILL_ACTIVE
            return True
        finally:
            _k32.CloseHandle(h)
    try:
        os.kill(pid, 0)          # POSIX only — never reached on Windows
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def probe_pid_start_time(pid):
    """Return a stable process-creation token (str) or None if unavailable.

    Two runs of the *same* process return the same token; a reused PID (a new
    process with the same number) returns a different one, which is how we detect
    stale/reused PIDs without ever trusting the number alone.
    """
    if pid is None or not (_IS_WINDOWS and _CTYPES_OK):
        return None
    try:
        h = _win_open(pid)
        if not h:
            return None
        try:
            creation = wintypes.FILETIME()
            dummy = wintypes.FILETIME()
            if _k32.GetProcessTimes(h, ctypes.byref(creation), ctypes.byref(dummy),
                                    ctypes.byref(dummy), ctypes.byref(dummy)):
                return str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
            return None
        finally:
            _k32.CloseHandle(h)
    except Exception:
        return None


def probe_pid_executable(pid):
    """Best-effort full image path of *pid* (str) or None. Informational only."""
    if pid is None or not (_IS_WINDOWS and _CTYPES_OK):
        return None
    try:
        h = _win_open(pid)
        if not h:
            return None
        try:
            size = wintypes.DWORD(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            if _k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value
            return None
        finally:
            _k32.CloseHandle(h)
    except Exception:
        return None


def port_listening(host, port, timeout=0.5):
    """True when something accepts a TCP connection on host:port (bounded)."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def http_get_json(url, timeout=2.0):
    """GET a loopback JSON endpoint with a hard timeout. LOOPBACK ONLY.

    Returns the parsed object with an added ``_http_status`` (so a 503 /healthz is
    observed, not discarded), or None when nothing answered. Refuses a non-loopback
    URL so a health check can never reach the Internet.
    """
    host = urlparse(url).hostname
    if not RP.is_loopback_host(host):
        raise ValueError("health/status checks must target a loopback URL")
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (loopback-checked)
            status = resp.getcode()
            raw = resp.read(65536)
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            raw = e.read(65536)
        except Exception:
            raw = b""
    except (urllib.error.URLError, OSError, ValueError):
        return None
    try:
        import json as _json
        data = _json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        data = None
    if not isinstance(data, dict):
        data = {}
    data["_http_status"] = status
    return data


# ==============================================================================
# Paths, version, metadata
# ==============================================================================
def project_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def health_url_for(policy):
    return f"http://{policy.bind_host}:{policy.bind_port}/healthz"


def dashboard_url_for(policy):
    return f"http://{policy.bind_host}:{policy.bind_port}/"


def dashboard_version():
    """The one authoritative version (config.yaml tool_version)."""
    try:
        with open(os.path.join(project_root(), "config.yaml"), encoding="utf-8") as f:
            m = re.search(r'tool_version:\s*"([^"]+)"', f.read())
            return m.group(1) if m else "unknown"
    except Exception:
        return "unknown"


def git_commit():
    try:
        with open(os.path.join(project_root(), ".git", "HEAD"), encoding="utf-8") as f:
            head = f.read().strip()
        if head.startswith("ref:"):
            ref = head.split(" ", 1)[1].strip()
            with open(os.path.join(project_root(), ".git", ref), encoding="utf-8") as f:
                return f.read().strip()[:12]
        return head[:12]
    except Exception:
        return "unknown"


def _workspace_id():
    """Stable identifier for this checkout without leaking the full private path."""
    root = project_root()
    short = hashlib.sha256(root.encode("utf-8")).hexdigest()[:8]
    return f"{os.path.basename(root)}-{short}"


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _new_instance_id():
    return os.urandom(8).hex()


def _metadata_sha(meta):
    clean = {k: v for k, v in meta.items() if k != "metadata_sha256"}
    blob = __import__("json").dumps(clean, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_metadata(policy, pid, instance_id, executable, command, start_time, started_at):
    meta = {
        "schema_version": INSTANCE_SCHEMA_VERSION,
        "service": SERVICE_NAME,
        "pid": pid,
        "parent_pid": os.getpid(),
        "process_start_time": start_time,
        "instance_id": instance_id,
        "bind_host": policy.bind_host,
        "bind_port": policy.bind_port,
        "command": command,
        "executable": os.path.basename(str(executable)),
        "workspace": _workspace_id(),
        "started_at": started_at,
        "health_url": health_url_for(policy),
        "runtime_policy_sha256": policy.policy_sha256,
        "version": dashboard_version(),
        "commit": git_commit(),
    }
    meta["metadata_sha256"] = _metadata_sha(meta)
    return meta


def read_metadata(env=None):
    """Return (metadata_dict|None, corrupt_bool). A malformed file -> (None, True)."""
    try:
        meta = AP.read_json(AP.instance_metadata_path(env))
        return meta, False
    except AP.CorruptJsonError:
        return None, True


def write_metadata(meta, env=None):
    AP.write_json_atomic(AP.instance_metadata_path(env), meta)


def remove_metadata(env=None):
    path = AP.instance_metadata_path(env)
    try:
        if os.path.exists(path):
            os.remove(path)
            return True
    except OSError:
        pass
    return False


def _oplog(env, action, event, data=None):
    rec = {"ts": _now_iso(), "component": "instance", "action": action, "event": event}
    for k, v in (data or {}).items():
        rec[k] = D.redact_secrets(v) if isinstance(v, str) else v
    AP.append_log("launcher.log", rec, env=env)


# ==============================================================================
# Identity + state evaluation
# ==============================================================================
def verify_identity(meta, health):
    """Corroborate that the process answering /healthz IS this recorded instance.

    A matching PID is NEVER sufficient on its own: the service name must match and,
    when the health payload exposes them, the pid and instance_id must match too.
    """
    if not isinstance(health, dict):
        return {"ok": False, "reason": "no health payload"}
    if health.get("service") != SERVICE_NAME:
        return {"ok": False, "reason": "service name mismatch"}
    h_pid, m_pid = health.get("pid"), (meta or {}).get("pid")
    if h_pid is not None and m_pid is not None and int(h_pid) != int(m_pid):
        return {"ok": False, "reason": "pid mismatch between health and metadata"}
    h_iid, m_iid = health.get("instance_id"), (meta or {}).get("instance_id")
    if m_iid and h_iid and str(h_iid) != str(m_iid):
        return {"ok": False, "reason": "instance_id mismatch"}
    # require at least one strong corroborator beyond the PID number
    if h_pid is None and not (m_iid and h_iid):
        return {"ok": False, "reason": "no corroborating identity (pid/instance_id) in health"}
    return {"ok": True, "reason": "identity verified"}


def evaluate(policy=None, env=None):
    """Classify the current instance state without ever mutating or killing anything."""
    policy = policy if policy is not None else RP.load_runtime_policy()
    res = {
        "status": None, "pid": None, "instance_id": None,
        "bind_host": policy.bind_host, "bind_port": policy.bind_port,
        "health": None, "policy_valid": policy.is_valid,
        "identity_verified": False, "reused_pid": False, "detail": "",
    }
    if not policy.is_valid:
        res["status"] = INVALID_RUNTIME_POLICY
        res["detail"] = "; ".join(policy.validation_errors)
        return res

    host, port = policy.bind_host, policy.bind_port
    listening = port_listening(host, port)
    health = http_get_json(health_url_for(policy)) if listening else None
    meta, corrupt = read_metadata(env)

    h_service = (health or {}).get("service")
    h_ok = bool(health) and health.get("_http_status") == 200 and health.get("ok") is True
    is_ours = (h_service == SERVICE_NAME)

    if meta is None:
        if corrupt:
            res["status"] = STALE_RUNTIME_STATE
            res["detail"] = "corrupt runtime metadata (residue)"
            return res
        if not listening:
            res["status"] = STOPPED
            return res
        if is_ours:
            res["status"] = RUNNING_HEALTHY if h_ok else RUNNING_UNHEALTHY
            res["pid"] = (health or {}).get("pid")
            res["instance_id"] = (health or {}).get("instance_id")
            res["health"] = "healthy" if h_ok else "unhealthy"
            res["identity_verified"] = True
            res["detail"] = "running; service self-identified (no local metadata)"
            return res
        res["status"] = PORT_IN_USE_BY_UNKNOWN_PROCESS
        res["detail"] = "another process is listening on the configured port"
        return res

    # metadata present
    res["pid"] = meta.get("pid")
    res["instance_id"] = meta.get("instance_id")
    pid = meta.get("pid")
    if not (isinstance(pid, int) and probe_pid_alive(pid)):
        res["status"] = STALE_RUNTIME_STATE
        res["detail"] = "recorded process is not alive (dead pid)"
        res["pid"] = None
        return res

    recorded_start = meta.get("process_start_time")
    actual_start = probe_pid_start_time(pid)
    if recorded_start and actual_start and str(actual_start) != str(recorded_start):
        res["status"] = STALE_RUNTIME_STATE
        res["reused_pid"] = True
        res["pid"] = None
        res["detail"] = "pid alive but start time differs (reused pid) — not ours"
        return res

    if not listening or health is None:
        res["status"] = RUNNING_UNHEALTHY
        res["health"] = "unhealthy"
        res["detail"] = "process alive but health endpoint not responding"
        return res
    if not is_ours:
        res["status"] = INSTANCE_IDENTITY_UNVERIFIED
        res["pid"] = None
        res["detail"] = "port serves a different service than expected"
        return res

    identity = verify_identity(meta, health)
    if not identity["ok"]:
        res["status"] = INSTANCE_IDENTITY_UNVERIFIED
        res["pid"] = None
        res["detail"] = identity["reason"]
        return res

    res["identity_verified"] = True
    res["health"] = "healthy" if h_ok else "unhealthy"
    res["status"] = RUNNING_HEALTHY if h_ok else RUNNING_UNHEALTHY
    res["version"] = health.get("version")
    res["uptime_seconds"] = health.get("uptime_seconds")
    res["detail"] = "verified instance"
    return res


def get_instance_status(env=None, policy=None):
    """Rich status for the CLI/dashboard. PID is exposed only when it is verified ours."""
    policy = policy if policy is not None else RP.load_runtime_policy()
    ev = evaluate(policy, env)
    meta, corrupt = read_metadata(env)
    ev["metadata_present"] = meta is not None
    ev["metadata_corrupt"] = corrupt
    ev["service"] = SERVICE_NAME
    if ev["status"] in (RUNNING_HEALTHY, RUNNING_UNHEALTHY) and meta:
        ev.setdefault("version", meta.get("version"))
        ev["started_at"] = meta.get("started_at")
        ev["health_url"] = meta.get("health_url")
    if ev["status"] not in (RUNNING_HEALTHY, RUNNING_UNHEALTHY):
        ev["pid"] = None            # never surface a PID we have not verified as ours
    return ev


# ==============================================================================
# Health waiting + stale recovery
# ==============================================================================
def wait_for_health(url, timeout, interval=0.4):
    """Poll /healthz until a 200/ok payload or the bounded timeout. Returns (ok, payload)."""
    deadline = time.perf_counter() + max(0.1, float(timeout))
    health = None
    while time.perf_counter() < deadline:
        health = http_get_json(url, timeout=min(2.0, interval * 3 + 0.5))
        if health is not None and health.get("_http_status") == 200 \
                and health.get("ok") is True:
            return True, health
        time.sleep(interval)
    return False, health


def recover_stale_state(env=None):
    """Archive (or remove) ONLY stale runtime metadata. Logs/diagnostics are preserved."""
    path = AP.instance_metadata_path(env)
    archived = False
    if os.path.exists(path):
        archive = os.path.join(AP.runtime_dir(env), "instance.stale.json")
        try:
            os.replace(path, archive)
            archived = True
        except OSError:
            try:
                os.remove(path)
            except OSError:
                pass
    _oplog(env, "recover", "stale_recovered", {"archived": bool(archived)})
    return {"recovered": True, "archived": archived}


# ==============================================================================
# Spawn / terminate (detached, verified-tree-only)
# ==============================================================================
def preferred_python():
    """The interpreter used to launch the detached dashboard.

    pythonw.exe (no console window) when it sits beside the active interpreter,
    otherwise the active interpreter. Shared with windows_integration so autostart,
    shortcuts, and wrappers all launch the toolkit the same way.
    """
    base = sys.executable or "python"
    if _IS_WINDOWS:
        cand = os.path.join(os.path.dirname(base), "pythonw.exe")
        if os.path.exists(cand):
            return cand
    return base


def _spawn_dashboard(policy, instance_id, env=None):
    """Start the existing dashboard as a detached, offline, loopback-only process.

    Uses pythonw.exe when available (no console window) and forces the offline-safe
    runtime env. Returns (pid, executable, command_display). Output is redirected to
    a bounded local log so the detached process cannot flood or block on a pipe.
    """
    root = project_root()
    script = os.path.join(root, "dashboard", "app.py")
    exe = preferred_python()
    child_env = dict(os.environ)
    child_env.update({
        "BIND_HOST": policy.bind_host,
        "BIND_PORT": str(policy.bind_port),
        "OFFLINE_ONLY": "true",
        "EXTERNAL_AI_ENABLED": "false",
        "OUTBOUND_NETWORK_ENABLED": "false",
        "LOOPBACK_ONLY": "true",
        "AMZ_FBM_INSTANCE_ID": instance_id,
        "PYTHONIOENCODING": "utf-8",
    })
    log_path = os.path.join(AP.logs_dir(env), "dashboard.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    if os.path.exists(log_path) and os.path.getsize(log_path) >= 2_000_000:
        try:
            os.replace(log_path, log_path + ".1")
        except OSError:
            pass
    logf = open(log_path, "ab")
    try:
        kwargs = dict(cwd=root, env=child_env, stdout=logf, stderr=logf,
                      stdin=subprocess.DEVNULL, close_fds=True)
        if _IS_WINDOWS:
            detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            kwargs["creationflags"] = detached | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen([exe, script], **kwargs)          # noqa: S603 (no shell)
    finally:
        logf.close()
    command_display = D.redact_command([os.path.basename(exe), "dashboard/app.py"])
    return proc.pid, exe, command_display


def _terminate_tree(pid, env=None, expected_start_time=None, reason=""):
    """Terminate ONLY the given PID's process tree. Refuses a reused/mismatched PID.

    Never a global kill. On Windows this is taskkill /T /F on the exact PID, routed
    through the bounded shared subprocess runner.
    """
    if not isinstance(pid, int):
        return {"terminated": False, "reason": "no pid"}
    if not probe_pid_alive(pid):
        return {"terminated": False, "reason": "not alive"}
    if expected_start_time is not None:
        actual = probe_pid_start_time(pid)
        if actual is not None and str(actual) != str(expected_start_time):
            _oplog(env, "terminate", "refused_reused_pid", {"pid": pid})
            return {"terminated": False, "reason": "pid reused; not terminated"}
    if _IS_WINDOWS:
        r = SR.run_subprocess(["taskkill", "/T", "/F", "/PID", str(pid)],
                              timeout_seconds=15, stage_name="terminate-tree",
                              allowed_exit_codes=tuple(range(0, 256)))
        ok = not probe_pid_alive(pid)
        _oplog(env, "terminate", "taskkill", {"pid": pid, "reason": reason, "ok": ok})
        return {"terminated": ok, "reason": r.diagnostic_summary}
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        if probe_pid_alive(pid):
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    return {"terminated": not probe_pid_alive(pid), "reason": reason}


def _wait_until_stopped(policy, pid, timeout):
    deadline = time.perf_counter() + max(0.1, float(timeout))
    while time.perf_counter() < deadline:
        if not port_listening(policy.bind_host, policy.bind_port) \
                and not (isinstance(pid, int) and probe_pid_alive(pid)):
            return True
        time.sleep(0.25)
    return not port_listening(policy.bind_host, policy.bind_port)


def _open_browser(policy):
    if not RP.is_loopback_host(policy.bind_host):
        return False
    try:
        return bool(webbrowser.open(dashboard_url_for(policy)))
    except Exception:
        return False


def _result(status, ok, message, policy=None, instance=None, **extra):
    r = {"status": status, "ok": bool(ok), "message": message, "service": SERVICE_NAME}
    if policy is not None:
        r["bind_host"] = policy.bind_host
        r["bind_port"] = policy.bind_port
    if instance is not None:
        r["instance"] = instance
    r.update(extra)
    return r


# ==============================================================================
# Public lifecycle: start / stop / restart
# ==============================================================================
def start_instance(*, open_browser=False, env=None, policy=None,
                    startup_timeout=20.0, spawn=None):
    policy = policy if policy is not None else RP.load_runtime_policy()
    started_at = _now_iso()
    if not policy.is_valid:
        _oplog(env, "start", "refused_invalid_policy")
        return _result(INVALID_RUNTIME_POLICY, False,
                       "; ".join(policy.validation_errors), policy=policy)

    ev = evaluate(policy, env)
    st = ev["status"]
    if st == RUNNING_HEALTHY:
        _oplog(env, "start", "already_running", {"port": policy.bind_port})
        return _result(ALREADY_RUNNING, True, "already running (healthy)",
                       policy=policy, instance=ev)
    if st in (PORT_IN_USE_BY_UNKNOWN_PROCESS, INSTANCE_IDENTITY_UNVERIFIED,
              RUNNING_UNHEALTHY):
        _oplog(env, "start", "refused", {"reason": st})
        return _result(st, False,
                       "not starting a second instance; an existing listener/process "
                       "is present and was NOT terminated", policy=policy, instance=ev)
    if st == STALE_RUNTIME_STATE:
        recover_stale_state(env)

    instance_id = _new_instance_id()
    spawn = spawn or _spawn_dashboard
    try:
        pid, exe, cmd = spawn(policy, instance_id, env)
    except Exception as e:
        _oplog(env, "start", "spawn_failed", {"error": D.redact_secrets(str(e))})
        return _result(START_FAILED, False,
                       "failed to spawn dashboard: " + D.redact_secrets(str(e)),
                       policy=policy)

    start_time = probe_pid_start_time(pid)
    meta = build_metadata(policy, pid, instance_id, exe, cmd, start_time, started_at)
    write_metadata(meta, env)

    healthy, health = wait_for_health(health_url_for(policy), startup_timeout)
    if healthy and verify_identity(meta, health)["ok"]:
        _oplog(env, "start", "healthy", {"pid": pid, "port": policy.bind_port})
        res = _result(STARTED, True, "dashboard started and verified healthy",
                      policy=policy, instance=get_instance_status(env=env, policy=policy))
        if open_browser:
            res["opened"] = _open_browser(policy)
        return res

    # startup failed: clean up ONLY the process we just spawned, then clear metadata
    _terminate_tree(pid, env=env, expected_start_time=start_time, reason="startup-cleanup")
    remove_metadata(env)
    _oplog(env, "start", "startup_failed", {"pid": pid})
    return _result(START_FAILED, False,
                   f"dashboard did not become healthy within {startup_timeout:.0f}s; "
                   "the spawned process was cleaned up", policy=policy)


def stop_instance(*, env=None, policy=None, timeout=15.0):
    policy = policy if policy is not None else RP.load_runtime_policy()
    if not policy.is_valid:
        return _result(INVALID_RUNTIME_POLICY, False,
                       "; ".join(policy.validation_errors), policy=policy)
    meta, _corrupt = read_metadata(env)
    ev = evaluate(policy, env)
    st = ev["status"]

    if st == STOPPED:
        return _result(NOT_RUNNING, True, "not running", policy=policy)
    if st == STALE_RUNTIME_STATE:
        recover_stale_state(env)
        return _result(NOT_RUNNING, True,
                       "cleared stale runtime state; nothing was running", policy=policy)
    if st in (PORT_IN_USE_BY_UNKNOWN_PROCESS, INSTANCE_IDENTITY_UNVERIFIED):
        _oplog(env, "stop", "refused", {"reason": st})
        return _result(st, False,
                       "refusing to stop an unknown/unverified process on the port",
                       policy=policy, instance=ev)

    pid = ev.get("pid") or (meta or {}).get("pid")
    expected_start = (meta or {}).get("process_start_time")
    term = _terminate_tree(pid, env=env, expected_start_time=expected_start, reason="stop")
    if _wait_until_stopped(policy, pid, timeout):
        remove_metadata(env)
        _oplog(env, "stop", "stopped", {"pid": pid})
        return _result(STOPPED_OK, True, "stopped the verified instance", policy=policy)
    _oplog(env, "stop", "stop_timeout", {"pid": pid, "term": term.get("reason")})
    return _result(STOP_FAILED, False,
                   "the verified process did not stop within the timeout", policy=policy)


def restart_instance(*, env=None, policy=None, open_browser=False, startup_timeout=20.0):
    policy = policy if policy is not None else RP.load_runtime_policy()
    stop_res = stop_instance(env=env, policy=policy)
    if not stop_res["ok"] and stop_res["status"] in (
            PORT_IN_USE_BY_UNKNOWN_PROCESS, INSTANCE_IDENTITY_UNVERIFIED):
        return _result(RESTART_ABORTED, False,
                       "cannot restart: an unverified process holds the port",
                       policy=policy, stop_phase=stop_res)
    start_res = start_instance(env=env, policy=policy, open_browser=open_browser,
                               startup_timeout=startup_timeout)
    start_res["stop_phase"] = {"status": stop_res["status"], "ok": stop_res["ok"]}
    return start_res
