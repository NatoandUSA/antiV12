#!/usr/bin/env python3
"""Bounded, secret-safe subprocess runner (Session 5B / ACT-020, Part F).

One shared way to run a child process so no call in the toolkit can hang the test
suite, leak a secret, or leave an orphan process behind:

    - the timeout is explicit and must be positive (no infinite wait);
    - shell=True is never used (arguments are passed as a list);
    - stdout and stderr are captured separately and decoded UTF-8 with replacement;
    - on timeout the whole spawned process *tree* is terminated (graceful first,
      force-kill after a short bounded grace) — only the tree we spawned;
    - output is bounded so a runaway child cannot exhaust memory, with truncation
      flags and observed size preserved;
    - a typed SubprocessResult always comes back with a stable error code and a
      secret-free diagnostic summary.

Windows-first (the owner's platform): a new process group is created so the child
tree can be terminated with taskkill /T /F as a bounded fallback. It never runs a
global "kill all python" — only the exact PID tree it started.
"""
import os
import subprocess
import sys
import time
from datetime import datetime

import diagnostics as D

_IS_WINDOWS = os.name == "nt"
_KILL_GRACE_SECONDS = 1.5          # bounded grace between graceful and force-kill
_DEFAULT_MAX_OUTPUT_BYTES = 1_000_000   # 1 MB per stream unless the caller overrides


class SubprocessResult:
    """Structured, secret-safe outcome of a run_subprocess call."""

    def __init__(self, **kw):
        self.stage_name = kw.get("stage_name")
        self.command_display = kw.get("command_display", "")
        self.cwd_display = kw.get("cwd_display")
        self.started_at = kw.get("started_at")
        self.finished_at = kw.get("finished_at")
        self.duration_seconds = kw.get("duration_seconds", 0.0)
        self.exit_code = kw.get("exit_code")
        self.timed_out = kw.get("timed_out", False)
        self.terminated = kw.get("terminated", False)
        self.stdout = kw.get("stdout", "")
        self.stderr = kw.get("stderr", "")
        self.stdout_truncated = kw.get("stdout_truncated", False)
        self.stderr_truncated = kw.get("stderr_truncated", False)
        self.stdout_bytes = kw.get("stdout_bytes", 0)
        self.stderr_bytes = kw.get("stderr_bytes", 0)
        self.success = kw.get("success", False)
        self.error_code = kw.get("error_code")
        self.diagnostic_summary = kw.get("diagnostic_summary", "")

    def to_dict(self):
        return {
            "stage_name": self.stage_name,
            "command_display": self.command_display,
            "cwd_display": self.cwd_display,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round(self.duration_seconds, 3),
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "terminated": self.terminated,
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
            "stdout_bytes": self.stdout_bytes,
            "stderr_bytes": self.stderr_bytes,
            "success": self.success,
            "error_code": self.error_code,
            "diagnostic_summary": self.diagnostic_summary,
        }

    def __repr__(self):
        return (f"SubprocessResult(stage={self.stage_name!r}, exit={self.exit_code}, "
                f"timed_out={self.timed_out}, success={self.success})")


def _bound_output(data, limit):
    """Return (text, truncated, observed_bytes) decoding bytes UTF-8 safely and
    keeping a head+tail slice when it exceeds *limit* bytes."""
    if data is None:
        return "", False, 0
    observed = len(data)
    if limit is None or observed <= limit:
        return data.decode("utf-8", "replace"), False, observed
    head = max(1, limit // 2)
    tail = max(1, limit - head)
    notice = f"\n...[truncated: {observed} bytes -> kept {limit}]...\n"
    text = (data[:head].decode("utf-8", "replace") + notice
            + data[observed - tail:].decode("utf-8", "replace"))
    return text, True, observed


def _kill_tree(proc):
    """Terminate the spawned process *tree* — graceful first, force after a short
    bounded grace. Only ever targets proc.pid's tree, never unrelated processes.

    On Windows a bare proc.terminate() kills only the parent and orphans its
    children, so the whole tree must be walked from the still-alive parent with
    taskkill /T. This function is only called on the timeout path, where the parent
    is guaranteed alive, so the parent-child links are still intact for the walk.
    """
    pid = proc.pid
    if _IS_WINDOWS:
        # graceful tree close first (best effort; a sleeping console child ignores it)
        try:
            subprocess.run(["taskkill", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=5)
        except Exception:
            pass
        deadline = time.perf_counter() + _KILL_GRACE_SECONDS
        while proc.poll() is None and time.perf_counter() < deadline:
            time.sleep(0.05)
        # force-kill the entire tree (parent + any descendants), bounded fallback
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=10)
        except Exception:
            pass
    else:
        import signal
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except Exception:
            pass
        deadline = time.perf_counter() + _KILL_GRACE_SECONDS
        while proc.poll() is None and time.perf_counter() < deadline:
            time.sleep(0.05)
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except Exception:
                pass
    try:
        proc.kill()
    except Exception:
        pass


def run_subprocess(command, *, cwd=None, timeout_seconds, env=None, stage_name=None,
                   allowed_exit_codes=(0,), max_output_bytes=_DEFAULT_MAX_OUTPUT_BYTES):
    """Run *command* (a list of args) with a hard timeout and bounded, secret-safe output.

    timeout_seconds must be an explicit positive number. shell=True is never used.
    Returns a SubprocessResult; never raises for a non-zero exit or a timeout —
    inspect ``result.success`` / ``result.error_code`` instead.
    """
    if isinstance(command, (str, bytes)):
        raise ValueError("command must be a list of arguments (shell=True is not allowed)")
    command = [str(a) for a in command]
    if timeout_seconds is None or not isinstance(timeout_seconds, (int, float)) \
            or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be an explicit positive number")

    stage = stage_name or (command[0] if command else "subprocess")
    command_display = D.redact_command(command)
    cwd_display = os.path.basename(cwd) if cwd else None

    # merge env safely; a caller env replaces os.environ only for the child
    child_env = dict(os.environ)
    if env:
        child_env.update({str(k): str(v) for k, v in env.items()})
    child_env.setdefault("PYTHONIOENCODING", "utf-8")

    popen_kwargs = dict(cwd=cwd, env=child_env, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE)
    if _IS_WINDOWS:
        # new process group so the whole child tree can be killed together, and
        # no console window pops up for pythonw/GUI parents.
        popen_kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                         | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        popen_kwargs["start_new_session"] = True   # own session -> killpg the tree

    started_at = datetime.now().isoformat(timespec="seconds")
    t0 = time.perf_counter()

    try:
        proc = subprocess.Popen(command, **popen_kwargs)
    except (OSError, ValueError) as e:
        duration = time.perf_counter() - t0
        summary = f"{stage}: failed to start ({type(e).__name__})"
        D.record_event(D.SUBPROCESS_START_FAILED, summary,
                       {"stage": stage, "command": command_display})
        return SubprocessResult(
            stage_name=stage, command_display=command_display, cwd_display=cwd_display,
            started_at=started_at, finished_at=datetime.now().isoformat(timespec="seconds"),
            duration_seconds=duration, exit_code=None, timed_out=False, terminated=False,
            stdout="", stderr=D.redact_secrets(str(e)), success=False,
            error_code=D.SUBPROCESS_START_FAILED, diagnostic_summary=summary)

    timed_out = False
    terminated = False
    try:
        out_b, err_b = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminated = True
        _kill_tree(proc)
        try:
            out_b, err_b = proc.communicate(timeout=10)
        except Exception:
            out_b, err_b = b"", b""

    duration = time.perf_counter() - t0
    finished_at = datetime.now().isoformat(timespec="seconds")

    stdout, out_trunc, out_size = _bound_output(out_b, max_output_bytes)
    stderr, err_trunc, err_size = _bound_output(err_b, max_output_bytes)
    stdout = D.redact_secrets(stdout)
    stderr = D.redact_secrets(stderr)

    exit_code = proc.returncode
    if timed_out:
        success = False
        error_code = D.SUBPROCESS_TIMEOUT
        summary = f"{stage}: TIMEOUT after {duration:.2f}s (limit {timeout_seconds}s)"
        D.record_event(D.SUBPROCESS_TIMEOUT, summary,
                       {"stage": stage, "elapsed_seconds": round(duration, 3),
                        "timeout_seconds": timeout_seconds, "command": command_display})
    elif exit_code in allowed_exit_codes:
        success = True
        error_code = None
        summary = f"{stage}: exit {exit_code} in {duration:.2f}s"
    else:
        success = False
        error_code = D.SUBPROCESS_EXIT_ERROR
        summary = f"{stage}: exit {exit_code} (not allowed) in {duration:.2f}s"
        D.record_event(D.SUBPROCESS_EXIT_ERROR, summary,
                       {"stage": stage, "exit_code": exit_code, "command": command_display})

    if out_trunc or err_trunc:
        D.record_event(D.SUBPROCESS_OUTPUT_TRUNCATED,
                       f"{stage}: output truncated (stdout={out_size}B, stderr={err_size}B)",
                       {"stage": stage, "stdout_bytes": out_size, "stderr_bytes": err_size})

    return SubprocessResult(
        stage_name=stage, command_display=command_display, cwd_display=cwd_display,
        started_at=started_at, finished_at=finished_at, duration_seconds=duration,
        exit_code=exit_code, timed_out=timed_out, terminated=terminated,
        stdout=stdout, stderr=stderr, stdout_truncated=out_trunc, stderr_truncated=err_trunc,
        stdout_bytes=out_size, stderr_bytes=err_size, success=success,
        error_code=error_code, diagnostic_summary=summary)
