#!/usr/bin/env python3
"""Phase 7.14 — Owner Usability & Pilot Readiness.

Covers the four Phase 7.14 deliverables: Launcher Lite, the smart next-action guidance read model,
the owner-facing dashboard refinements, and the real-pilot preparation kit.

Every test runs offline. No committed test uses the real Internet: the launcher is driven entirely
through injected seams (health probe, port probe, spawn, browser, process identity, clock), and the
front-end is rendered in a dependency-free Node DOM harness against fixtures produced here. The
permanent boundary is asserted throughout — no Amazon Seller Central, no seller sign-in, no seller
API, no advertising API, no seller browser automation, and every seller-account counter is a
constant zero.
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout, redirect_stderr
import io

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "core"), os.path.join(ROOT, "production"),
           os.path.join(ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import phase7_owner_launcher as L                    # noqa: E402
from production import phase7_owner_next_action as NA                # noqa: E402
from production import phase7_unified_owner_console as UC            # noqa: E402

LAUNCHER_PATH = os.path.join(ROOT, "production", "phase7_owner_launcher.py")
NEXT_PATH = os.path.join(ROOT, "production", "phase7_owner_next_action.py")
CONSOLE_PATH = os.path.join(ROOT, "production", "phase7_unified_owner_console.py")
STATIC_DIR = os.path.join(ROOT, "production", "phase7_unified_owner_console_static")
DOCS = os.path.join(ROOT, "docs")
TESTS = os.path.join(ROOT, "tests")

SCRIPTS = ("Start-AMZ-Toolkit.bat", "Start-AMZ-Toolkit.ps1", "Stop-AMZ-Toolkit.bat",
           "Stop-AMZ-Toolkit.ps1", "Open-AMZ-Toolkit.bat", "Open-AMZ-Toolkit.ps1")
PILOT_DOCS = ("PHASE7_14-OWNER-USABILITY-POLICY.md", "PHASE7_14-OWNER-PILOT-GUIDE.md",
              "PHASE7_14-OWNER-PILOT-CHECKLIST.md", "PHASE7_14-PILOT-ISSUE-TEMPLATE.md",
              "PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md", "PHASE7_14-PILOT-EXIT-CRITERIA.md")

NODE = shutil.which("node")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def doc(name):
    return read(os.path.join(DOCS, name))


def script(name):
    return read(os.path.join(ROOT, name))


def static(name):
    return read(os.path.join(STATIC_DIR, name))


# ================================================================ next-action fixtures
def model(**over):
    """A complete, healthy console model. Each test perturbs exactly one thing."""
    ov = {"pending_decisions": 0, "pending_manual_actions": 0, "due_followups": 0,
          "open_alerts": 0, "due_watchlists": 0, "notification_unknown": 0, "notification_sent": 0,
          "notification_failed": 0, "backup_snapshots": 1, "update_available": False,
          "stale_data_phases": [], "blocked_modules": [], "unavailable_modules": [],
          "attention_items": 0, "research_runs": 1}
    ov.update(over.pop("overview", {}))
    m = {
        "readiness": UC.CONSOLE_READY,
        "overview": ov,
        "sections": {
            "analysis": {"status": "READY", "counts": {"analyzed_rows": 10},
                         "model": {"overview": {"decision_package_id": "pkg-1"}}},
            "research": {"status": "READY", "counts": {"runs": 1}},
            "watchlists": {"status": "READY", "alerts": [], "counts": {}},
            "notifications": {"status": "READY", "counts": {}},
            "backup": {"status": "READY", "counts": {}},
        },
        "freshness": {"7.3": {"present": True, "stale": False},
                      "7.9": {"present": True, "stale": False}},
        "system": {"audit_chain": {"ok": True},
                   "seller_central_counters": dict(UC.SELLER_CENTRAL_COUNTERS),
                   "connectivity_boundary": {"seller_central_blocked": True,
                                             "seller_api_blocked": True,
                                             "advertising_api_blocked": True}},
    }
    for k, v in over.items():
        if k in ("sections", "system", "freshness") and isinstance(v, dict):
            m[k].update(v)
        else:
            m[k] = v
    return m


def na_for(**over):
    return NA.build_next_action(model(**over))


CASES = {
    1: lambda: model(system={"audit_chain": {"ok": False, "reason": "HASH_MISMATCH"}}),
    2: lambda: model(system={"seller_central_counters": dict(UC.SELLER_CENTRAL_COUNTERS,
                                                            seller_api_calls=1)}),
    3: lambda: model(sections={"analysis": {"status": "MODULE_UNAVAILABLE", "counts": {}}},
                     overview={"unavailable_modules": ["analysis"]}),
    4: lambda: model(sections={"analysis": {"status": "READY_EMPTY",
                                            "counts": {"analyzed_rows": 0}}}),
    5: lambda: model(freshness={"7.3": {"present": True, "stale": True}}),
    6: lambda: model(overview={"pending_decisions": 3}),
    7: lambda: model(overview={"pending_manual_actions": 2}),
    8: lambda: model(overview={"due_followups": 4}),
    9: lambda: model(sections={"watchlists": {"status": "READY", "counts": {}, "alerts": [
        {"alert_id": "al-1", "status": "OPEN", "severity": "CRITICAL"}]}}),
    10: lambda: model(overview={"due_watchlists": 1}),
    11: lambda: model(overview={"notification_unknown": 2}),
    12: lambda: model(overview={"backup_snapshots": 0}),
    13: lambda: model(overview={"update_available": True}),
    14: lambda: model(),
}


# ================================================================ launcher seams
class FakeProc:
    """A spawned console stand-in. `exit_after` makes it 'crash' after N health polls."""

    def __init__(self, pid=424242, exit_after=None, exit_code=1):
        self.pid = pid
        self._exit_after = exit_after
        self._exit_code = exit_code
        self.polls = 0
        self.kills = 0
        self.waits = 0

    def poll(self):
        self.polls += 1
        if self._exit_after is not None and self.polls > self._exit_after:
            return self._exit_code
        return None

    def kill(self):
        """The owned-child cleanup path: no PID is ever re-resolved to reach this process."""
        self.kills += 1
        self._exit_after = 0

    def wait(self, timeout=None):
        self.waits += 1
        return self._exit_code


HEALTHY = {"ok": True, "reason": "OK", "stage_id": "7.13",
           "api_schema": "phase7-13-console-api-v1", "readiness": UC.CONSOLE_READY,
           "http_status": 200, "seller_central_action_performed": False}
UNHEALTHY = {"ok": False, "reason": "ConnectionRefusedError", "stage_id": None,
             "api_schema": None, "readiness": None, "http_status": None}
FOREIGN = {"ok": False, "reason": "NOT_THE_ACCEPTED_CONSOLE", "stage_id": None,
           "api_schema": "something-else", "readiness": None, "http_status": 200}


class LauncherBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p714-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = L.Workspace(self.tmp)
        self.ws.ensure()
        self.browser_calls = []
        self.terminated = []
        self.slept = []

    def launcher(self, **kw):
        kw.setdefault("root", ROOT)
        kw.setdefault("workspace", self.ws)
        kw.setdefault("clock", self._clock())
        kw.setdefault("sleep", self.slept.append)
        kw.setdefault("health", lambda h, p: dict(UNHEALTHY))
        kw.setdefault("port_probe", lambda h, p: False)
        kw.setdefault("spawn", lambda cmd, cwd: FakeProc())
        kw.setdefault("browser", self._browser)
        kw.setdefault("alive", lambda pid: True)
        kw.setdefault("start_token", lambda pid: "tok-" + str(pid))
        kw.setdefault("terminate", self._terminate)
        return L.Launcher(**kw)

    def _clock(self):
        box = {"t": 0.0}

        def clock():
            box["t"] += 0.25
            return box["t"]
        return clock

    def _browser(self, url):
        self.browser_calls.append(url)
        return True

    def _terminate(self, pid, *, hard=False):
        self.terminated.append((pid, hard))
        return True


# ================================================================ 1) launcher CLI
class TestLauncherCLI(unittest.TestCase):
    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                rc = L.main(argv)
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue(), err.getvalue()

    def test_001_cli_help(self):
        rc, out, _ = self.run_cli(["--help"])
        self.assertEqual(rc, 0)
        self.assertIn("start", out)
        self.assertIn("stop", out)
        self.assertIn("open", out)

    def test_002_unknown_command_rejected(self):
        rc, _, err = self.run_cli(["frobnicate"])
        self.assertEqual(rc, 2)

    def test_002b_help_states_no_seller_action(self):
        rc, out, _ = self.run_cli(["--help"])
        self.assertIn("Seller Central", out)

    def test_003_validate_only(self):
        rc, out, _ = self.run_cli(["validate-only"])
        self.assertEqual(rc, 0)
        self.assertIn("readiness=" + L.LAUNCHER_READY, out)
        self.assertIn("ok=True", out)

    def test_004_validate_only_no_side_effects(self):
        res = L.validate_only()
        for k in ("files_written", "directories_created", "processes_spawned", "processes_signalled",
                  "browsers_opened", "network_requests", "ports_probed", "locks_taken"):
            self.assertEqual(res[k], 0, k)

    def test_004b_validate_only_writes_nothing_to_disk(self):
        tmp = tempfile.mkdtemp(prefix="p714-vo-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        before = sorted(os.listdir(tmp))
        L.validate_only(root=tmp)
        self.assertEqual(sorted(os.listdir(tmp)), before)

    def test_004c_validate_only_json(self):
        rc, out, _ = self.run_cli(["--json", "validate-only"])
        self.assertEqual(json.loads(out)["schema_version"], "phase7-14-launcher-validate-v1")

    def test_004d_non_loopback_host_refused_by_cli(self):
        rc, _, err = self.run_cli(["--host", "0.0.0.0", "start"])
        self.assertEqual(rc, 2)
        self.assertIn("NON_LOOPBACK_BIND_REFUSED", err)

    def test_004e_invalid_port_refused_by_cli(self):
        rc, _, err = self.run_cli(["--port", "99999", "start"])
        self.assertEqual(rc, 2)


# ================================================================ 2) preflight
class TestLauncherPreflight(LauncherBase):
    def test_005_python_detected(self):
        c = L.check_python()
        self.assertTrue(c["ok"])
        self.assertEqual(c["python_version"], ".".join(str(x) for x in sys.version_info[:3]))

    def test_006_unsupported_python_refused(self):
        self.assertFalse(L.check_python((3, 6, 0))["ok"])
        self.assertFalse(L.check_python((2, 7, 18))["ok"])

    def test_006b_supported_python_accepted(self):
        for v in ((3, 9, 0), (3, 12, 10), (3, 13, 1)):
            self.assertTrue(L.check_python(v)["ok"], v)

    def test_006c_beyond_tested_python_still_runs(self):
        c = L.check_python((3, 99, 0))
        self.assertTrue(c["ok"])
        self.assertTrue(c["beyond_tested"])

    def test_007_repository_detected(self):
        c = L.check_repository(ROOT)
        self.assertTrue(c["ok"], c["missing"])
        self.assertEqual(c["missing"], [])

    def test_008_repository_moved_detected(self):
        c = L.check_repository(self.tmp)
        self.assertFalse(c["ok"])
        self.assertIn("production/phase7_unified_owner_console.py", c["missing"])

    def test_008b_repository_root_resolved_from_module_not_cwd(self):
        self.assertTrue(os.path.isfile(os.path.join(L.repo_root(), L.CONSOLE_SOURCE)))

    def test_008c_missing_static_asset_detected(self):
        fake = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(fake, "production", "phase7_unified_owner_console_static"))
        open(os.path.join(fake, L.CONSOLE_SOURCE), "w").close()
        c = L.check_repository(fake)
        self.assertFalse(c["ok"])
        self.assertTrue(any("index.html" in m for m in c["missing"]))

    def test_009_paths_with_spaces(self):
        spaced = os.path.join(self.tmp, "My Toolkit Folder")
        shutil.copytree(os.path.join(ROOT, "production", "phase7_unified_owner_console_static"),
                        os.path.join(spaced, "production", "phase7_unified_owner_console_static"))
        shutil.copy(CONSOLE_PATH, os.path.join(spaced, L.CONSOLE_SOURCE))
        self.assertTrue(L.check_repository(spaced)["ok"])

    def test_009b_workspace_in_spaced_path(self):
        spaced = os.path.join(self.tmp, "a b c")
        ws = L.Workspace(spaced)
        self.assertTrue(ws.ensure())
        ws.log("probe", note="ok")
        self.assertTrue(os.path.isfile(ws.path(L.LOG_FILE)))

    def test_009c_required_imports_present(self):
        c = L.check_imports()
        self.assertTrue(c["ok"], c)
        self.assertTrue(c["console_module_importable"])
        self.assertEqual(c["missing_stdlib"], [])

    def test_009d_preflight_aggregates(self):
        pre = self.launcher().preflight()
        self.assertTrue(pre["ok"])
        self.assertEqual({c["check"] for c in pre["checks"]},
                         {"python_supported", "repository_contains_console", "required_imports"})

    # ---- pilot readiness of the working copy ----------------------------------------------------
    def test_009e_pilot_kit_present_in_this_checkout(self):
        p = L.pilot_readiness(ROOT)
        self.assertTrue(p["ok"], p["missing"])
        self.assertEqual(p["readiness"], L.PILOT_READY)

    def test_009f_pilot_required_when_kit_missing(self):
        p = L.pilot_readiness(self.tmp)
        self.assertFalse(p["ok"])
        self.assertEqual(p["readiness"], L.PILOT_REQUIRED)
        self.assertEqual(len(p["missing"]), len(L.LAUNCHER_SCRIPTS) + len(L.PILOT_DOCUMENTS))

    def test_009g_pilot_kit_lists_all_six_scripts_and_docs(self):
        self.assertEqual(len(L.LAUNCHER_SCRIPTS), 6)
        self.assertEqual(len(L.PILOT_DOCUMENTS), 6)
        self.assertEqual(set(L.LAUNCHER_SCRIPTS), set(SCRIPTS))
        self.assertEqual({os.path.basename(d) for d in L.PILOT_DOCUMENTS}, set(PILOT_DOCS))

    def test_009h_validate_only_reports_pilot_readiness(self):
        self.assertEqual(L.validate_only()["pilot_readiness"], L.PILOT_READY)

    def test_009i_starting_state_declared_and_messaged(self):
        self.assertEqual(L.LAUNCHER_STARTING, "SESSION7_14_LAUNCHER_STARTING")
        self.assertIn(L.LAUNCHER_STARTING, L._OWNER_MESSAGES)

    def test_009j_starting_status_published_during_the_wait(self):
        seen = []
        ws = self.ws

        class Recording(L.Workspace):
            pass
        original = ws.write_status

        def record(result):
            seen.append(result["readiness"])
            return original(result)
        ws.write_status = record
        self.addCleanup(setattr, ws, "write_status", original)
        self.launcher(health=lambda h, p: dict(HEALTHY), workspace=ws).start()
        self.assertEqual(seen[0], L.LAUNCHER_STARTING, seen)
        self.assertEqual(seen[-1], L.LAUNCHER_READY, seen)


# ================================================================ 3) port handling
class TestLauncherPort(LauncherBase):
    def test_010_port_free_starts(self):
        seq = [dict(UNHEALTHY), dict(HEALTHY)]
        lc = self.launcher(port_probe=lambda h, p: False,
                           health=lambda h, p: seq.pop(0) if seq else dict(HEALTHY))
        res = lc.start()
        self.assertEqual(res["readiness"], L.LAUNCHER_READY)

    def test_011_port_occupied_by_stranger_blocks(self):
        lc = self.launcher(port_probe=lambda h, p: True, health=lambda h, p: dict(FOREIGN))
        res = lc.start()
        self.assertEqual(res["readiness"], L.LAUNCHER_PORT_BLOCKED)

    def test_011b_port_blocked_message_is_exact(self):
        lc = self.launcher(port_probe=lambda h, p: True, health=lambda h, p: dict(FOREIGN))
        self.assertEqual(lc.start()["port_message"], "PORT 8780 IS ALREADY IN USE")

    def test_011c_port_blocked_message_constant(self):
        self.assertEqual(L.PORT_IN_USE_MESSAGE, "PORT 8780 IS ALREADY IN USE")

    def test_012_port_occupied_by_accepted_console_is_already_running(self):
        lc = self.launcher(port_probe=lambda h, p: True, health=lambda h, p: dict(HEALTHY))
        res = lc.start()
        self.assertEqual(res["readiness"], L.LAUNCHER_ALREADY_RUNNING)
        self.assertTrue(res["duplicate_start_refused"])

    def test_013_port_occupied_by_unrelated_process_never_spawns(self):
        spawned = []
        lc = self.launcher(port_probe=lambda h, p: True, health=lambda h, p: dict(FOREIGN),
                           spawn=lambda cmd, cwd: spawned.append(cmd) or FakeProc())
        lc.start()
        self.assertEqual(spawned, [])

    def test_013b_port_occupied_by_unrelated_process_never_terminates_it(self):
        lc = self.launcher(port_probe=lambda h, p: True, health=lambda h, p: dict(FOREIGN))
        lc.start()
        self.assertEqual(self.terminated, [])

    def test_013c_no_automatic_random_port(self):
        self.assertIs(L.AUTOMATIC_PORT_SELECTION, False)
        lc = self.launcher(port_probe=lambda h, p: True, health=lambda h, p: dict(FOREIGN))
        self.assertEqual(lc.start()["port"], 8780)

    def test_013d_port_fixed_across_start_stop_open(self):
        self.assertEqual(L.DEFAULT_PORT, 8780)
        for verb in ("start", "stop", "open"):
            self.assertIn("--port", script("%s-AMZ-Toolkit.ps1" % verb.capitalize()))
            self.assertIn("8780", script("%s-AMZ-Toolkit.ps1" % verb.capitalize()))

    def test_013e_port_validation_bounds(self):
        for bad in (-1, 0, 65536, "x", None):
            with self.assertRaises(L.LauncherError):
                L.validate_port(bad)


# ================================================================ 4) already-running / duplicates
class TestLauncherRunning(LauncherBase):
    def test_014_already_running_healthy_console_not_duplicated(self):
        spawned = []
        lc = self.launcher(port_probe=lambda h, p: True, health=lambda h, p: dict(HEALTHY),
                           spawn=lambda cmd, cwd: spawned.append(cmd) or FakeProc())
        res = lc.start()
        self.assertEqual(spawned, [])
        self.assertTrue(res["already_running"])

    def test_015_already_running_unhealthy_is_blocked_not_killed(self):
        lc = self.launcher(port_probe=lambda h, p: True, health=lambda h, p: dict(UNHEALTHY))
        res = lc.start()
        self.assertEqual(res["readiness"], L.LAUNCHER_PORT_BLOCKED)
        self.assertEqual(self.terminated, [])

    def test_016_duplicate_start_opens_browser_but_starts_nothing(self):
        lc = self.launcher(port_probe=lambda h, p: True, health=lambda h, p: dict(HEALTHY))
        lc.start()
        self.assertEqual(self.browser_calls, ["http://127.0.0.1:8780"])

    def test_016b_repeated_double_click_is_idempotent(self):
        state = {"running": False}

        def probe(h, p):
            return state["running"]

        def health(h, p):
            return dict(HEALTHY) if state["running"] else dict(UNHEALTHY)

        def spawn(cmd, cwd):
            state["running"] = True
            return FakeProc()
        first = self.launcher(port_probe=probe, health=health, spawn=spawn).start()
        second = self.launcher(port_probe=probe, health=health, spawn=spawn).start()
        self.assertEqual(first["readiness"], L.LAUNCHER_READY)
        self.assertEqual(second["readiness"], L.LAUNCHER_ALREADY_RUNNING)

    def test_017_launcher_lock_is_exclusive(self):
        a = L.LauncherLock(self.ws)
        b = L.LauncherLock(self.ws)
        self.assertTrue(a.acquire())
        self.assertFalse(b.acquire())
        a.release()
        self.assertTrue(b.acquire())
        b.release()

    def test_017b_second_start_during_startup_refuses(self):
        held = L.LauncherLock(self.ws)
        held.acquire()
        self.addCleanup(held.release)
        res = self.launcher(health=lambda h, p: dict(UNHEALTHY)).start()
        self.assertEqual(res["readiness"], L.LAUNCHER_LOCKED)

    def test_017c_second_start_during_startup_spawns_nothing(self):
        held = L.LauncherLock(self.ws)
        held.acquire()
        self.addCleanup(held.release)
        spawned = []
        self.launcher(spawn=lambda cmd, cwd: spawned.append(c) or FakeProc()).start()
        self.assertEqual(spawned, [])

    def test_017d_lock_released_after_start(self):
        seq = [dict(HEALTHY)]
        self.launcher(health=lambda h, p: seq[0]).start()
        self.assertFalse(os.path.isfile(self.ws.path(L.LOCK_FILE)))

    def test_018_stale_lock_reclaimed_when_owner_gone(self):
        lock = L.LauncherLock(self.ws)
        with open(lock.path, "w", encoding="utf-8") as f:
            json.dump({"launcher_pid": 999999999, "acquired_at_epoch": 0.0}, f)
        fresh = L.LauncherLock(self.ws)
        self.assertTrue(fresh.acquire())
        self.assertTrue(fresh.reclaimed_stale)
        fresh.release()

    def test_018b_stale_lock_reclaimed_when_too_old(self):
        lock = L.LauncherLock(self.ws)
        with open(lock.path, "w", encoding="utf-8") as f:
            json.dump({"launcher_pid": os.getpid(), "acquired_at_epoch": 0.0}, f)
        fresh = L.LauncherLock(self.ws, stale_after=1.0, clock=lambda: 10_000.0)
        self.assertTrue(fresh.acquire())
        fresh.release()

    def test_018c_live_lock_not_reclaimed(self):
        held = L.LauncherLock(self.ws)
        held.acquire()
        self.addCleanup(held.release)
        other = L.LauncherLock(self.ws)
        self.assertFalse(other.acquire())
        self.assertFalse(other.reclaimed_stale)

    def test_018d_corrupt_lock_file_is_reclaimed(self):
        with open(L.LauncherLock(self.ws).path, "w", encoding="utf-8") as f:
            f.write("not json")
        fresh = L.LauncherLock(self.ws)
        self.assertTrue(fresh.acquire())
        fresh.release()


# ================================================================ 5) PID record + identity
class TestLauncherPid(LauncherBase):
    def start_ok(self, **kw):
        kw.setdefault("health", lambda h, p: dict(HEALTHY))
        return self.launcher(**kw).start()

    def test_019_pid_record_written(self):
        res = self.start_ok(spawn=lambda cmd, cwd: FakeProc(pid=777))
        self.assertEqual(res["pid"], 777)
        rec = self.ws.read_pid()
        self.assertEqual(rec["pid"], 777)
        self.assertEqual(rec["schema_version"], L.PID_SCHEMA)

    def test_019b_pid_record_holds_identity_token(self):
        self.start_ok(spawn=lambda cmd, cwd: FakeProc(pid=778))
        self.assertEqual(self.ws.read_pid()["process_start_token"], "tok-778")

    def test_019c_pid_record_holds_command_fingerprint(self):
        self.start_ok(spawn=lambda cmd, cwd: FakeProc(pid=779))
        fp = self.ws.read_pid()["command_fingerprint"]
        self.assertIn("production.phase7_unified_owner_console", fp)
        self.assertTrue(fp.endswith("serve"))

    def test_019d_pid_record_has_no_absolute_path(self):
        self.start_ok(spawn=lambda cmd, cwd: FakeProc(pid=780))
        blob = json.dumps(self.ws.read_pid())
        self.assertNotIn(":\\", blob)
        self.assertNotIn(self.tmp, blob)

    def test_020_stale_pid_cleared_when_process_gone(self):
        self.ws.write_pid({"pid": 4242, "process_start_token": "tok-4242", "host": "127.0.0.1",
                           "port": 8780})
        res = self.start_ok(alive=lambda pid: pid != 4242,
                            spawn=lambda cmd, cwd: FakeProc(pid=99))
        self.assertTrue(res["stale_pid_cleared"])
        self.assertEqual(self.ws.read_pid()["pid"], 99)

    def test_021_pid_reuse_detected_and_record_cleared(self):
        self.ws.write_pid({"pid": 4242, "process_start_token": "tok-OLD", "host": "127.0.0.1",
                           "port": 8780})
        res = self.start_ok(alive=lambda pid: True, start_token=lambda pid: "tok-NEW",
                            spawn=lambda cmd, cwd: FakeProc(pid=99))
        self.assertTrue(res["stale_pid_cleared"])

    def test_021b_pid_reuse_never_signals(self):
        self.ws.write_pid({"pid": 4242, "process_start_token": "tok-OLD", "host": "127.0.0.1",
                           "port": 8780})
        self.start_ok(alive=lambda pid: True, start_token=lambda pid: "tok-NEW",
                      spawn=lambda cmd, cwd: FakeProc(pid=99))
        self.assertEqual(self.terminated, [])

    def test_022_process_identity_helpers_real(self):
        self.assertTrue(L.process_alive(os.getpid()))
        self.assertIsNotNone(L.process_start_token(os.getpid()))

    def test_022b_process_identity_stable_for_same_process(self):
        self.assertEqual(L.process_start_token(os.getpid()), L.process_start_token(os.getpid()))

    def test_022c_process_alive_false_for_impossible_pid(self):
        self.assertFalse(L.process_alive(-1))
        self.assertFalse(L.process_alive(0))
        self.assertFalse(L.process_alive("x"))

    def test_022d_start_token_none_for_impossible_pid(self):
        self.assertIsNone(L.process_start_token(-1))
        self.assertIsNone(L.process_start_token(None))

    def test_022e_corrupt_pid_file_tolerated(self):
        with open(self.ws.path(L.PID_FILE), "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertIsNone(self.ws.read_pid())


# ================================================================ 6) start behaviour
class TestLauncherStart(LauncherBase):
    def test_023_start_bounded_timeout(self):
        lc = self.launcher(health=lambda h, p: dict(UNHEALTHY), timeout=2.0)
        res = lc.start()
        self.assertEqual(res["readiness"], L.LAUNCHER_TIMEOUT)
        self.assertLessEqual(res["startup_seconds"], 10.0)

    def test_023b_timeout_reports_budget(self):
        res = self.launcher(health=lambda h, p: dict(UNHEALTHY), timeout=2.0).start()
        self.assertEqual(res["timeout_seconds"], 2.0)

    def test_023c_default_timeout_is_bounded(self):
        self.assertLessEqual(L.START_TIMEOUT_SECONDS, 120.0)
        self.assertGreater(L.START_TIMEOUT_SECONDS, 0)

    def test_024_health_polling_until_ready(self):
        seq = [dict(UNHEALTHY), dict(UNHEALTHY), dict(HEALTHY)]
        calls = []

        def health(h, p):
            calls.append(1)
            return seq.pop(0) if seq else dict(HEALTHY)
        res = self.launcher(health=health, timeout=30.0).start()
        self.assertEqual(res["readiness"], L.LAUNCHER_READY)
        self.assertGreaterEqual(len(calls), 3)

    def test_024b_health_probe_targets_accepted_endpoint(self):
        self.assertEqual(L.HEALTH_PATH, "/api/v1/health")
        self.assertIn(L.HEALTH_PATH, UC._READ_ENDPOINTS[0].join(["/api/v1/", ""]) or "/api/v1/health")

    def test_024c_health_requires_the_accepted_console_identity(self):
        captured = {}

        def opener(url, timeout):
            captured["url"] = url
            return 200, json.dumps({"data": {"stage_id": "9.9", "api_schema": "other"}}).encode()
        res = L.probe_health("127.0.0.1", 8780, opener=opener)
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "NOT_THE_ACCEPTED_CONSOLE")

    def test_024d_health_accepts_the_accepted_console(self):
        def opener(url, timeout):
            return 200, json.dumps({"readiness": UC.CONSOLE_READY, "data": {
                "stage_id": "7.13", "api_schema": "phase7-13-console-api-v1"}}).encode()
        self.assertTrue(L.probe_health("127.0.0.1", 8780, opener=opener)["ok"])

    def test_024e_health_tolerates_malformed_body(self):
        res = L.probe_health("127.0.0.1", 8780, opener=lambda u, t: (200, b"<html>"))
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason"], "MALFORMED_HEALTH_BODY")

    def test_024f_health_tolerates_transport_failure(self):
        def opener(url, timeout):
            raise OSError("refused")
        self.assertFalse(L.probe_health("127.0.0.1", 8780, opener=opener)["ok"])

    def test_024g_health_probe_url_is_loopback(self):
        captured = {}

        def opener(url, timeout):
            captured["url"] = url
            return 200, b"{}"
        L.probe_health("127.0.0.1", 8780, opener=opener)
        self.assertTrue(captured["url"].startswith("http://127.0.0.1:8780/"))

    def test_025_browser_opens_only_after_health(self):
        order = []
        seq = [dict(UNHEALTHY), dict(UNHEALTHY), dict(HEALTHY)]

        def health(h, p):
            r = seq.pop(0) if seq else dict(HEALTHY)
            order.append("health:" + ("ok" if r["ok"] else "no"))
            return r

        def browser(url):
            order.append("browser")
            return True
        self.launcher(health=health, browser=browser, timeout=30.0).start()
        self.assertEqual(order[-1], "browser")
        self.assertEqual(order[-2], "health:ok")

    def test_026_browser_not_opened_before_health(self):
        self.launcher(health=lambda h, p: dict(UNHEALTHY), timeout=1.0).start()
        self.assertEqual(self.browser_calls, [])

    def test_026b_browser_not_opened_on_port_block(self):
        self.launcher(port_probe=lambda h, p: True, health=lambda h, p: dict(FOREIGN)).start()
        self.assertEqual(self.browser_calls, [])

    def test_026c_browser_not_opened_on_preflight_failure(self):
        self.launcher(root=self.tmp).start()
        self.assertEqual(self.browser_calls, [])

    def test_027_browser_failure_is_bounded_not_fatal(self):
        def bad_browser(url):
            raise RuntimeError("no browser")
        res = self.launcher(health=lambda h, p: dict(HEALTHY), browser=bad_browser).start()
        self.assertEqual(res["readiness"], L.LAUNCHER_READY)
        self.assertFalse(res["browser_opened"])
        self.assertTrue(res["browser_attempted"])

    def test_027b_browser_returning_false_is_reported(self):
        res = self.launcher(health=lambda h, p: dict(HEALTHY), browser=lambda u: False).start()
        self.assertEqual(res["readiness"], L.LAUNCHER_READY)
        self.assertFalse(res["browser_opened"])

    def test_027c_no_browser_flag_respected(self):
        self.launcher(health=lambda h, p: dict(HEALTHY), open_browser_on_start=False).start()
        self.assertEqual(self.browser_calls, [])

    def test_028_console_spawn_failure_reported(self):
        def spawn(cmd, cwd):
            raise OSError("cannot exec")
        res = self.launcher(spawn=spawn).start()
        self.assertEqual(res["readiness"], L.LAUNCHER_FAILED)
        self.assertEqual(res["error_code"], "CONSOLE_SPAWN_FAILED")

    def test_029_console_crash_during_startup_detected(self):
        res = self.launcher(health=lambda h, p: dict(UNHEALTHY), timeout=30.0,
                            spawn=lambda cmd, cwd: FakeProc(exit_after=1)).start()
        self.assertEqual(res["readiness"], L.LAUNCHER_FAILED)
        self.assertEqual(res["error_code"], "CONSOLE_EXITED_DURING_STARTUP")

    def test_029b_crash_during_startup_clears_pid(self):
        self.launcher(health=lambda h, p: dict(UNHEALTHY), timeout=30.0,
                      spawn=lambda cmd, cwd: FakeProc(exit_after=1)).start()
        self.assertIsNone(self.ws.read_pid())

    def test_029c_crash_during_startup_opens_no_browser(self):
        self.launcher(health=lambda h, p: dict(UNHEALTHY), timeout=30.0,
                      spawn=lambda cmd, cwd: FakeProc(exit_after=1)).start()
        self.assertEqual(self.browser_calls, [])

    def test_029d_unwritable_runtime_dir_reported(self):
        ws = L.Workspace(self.tmp)
        ws.ensure()
        ws.writable = False
        ws.write_error = "PermissionError"
        res = self.launcher(workspace=ws).start()
        self.assertEqual(res["readiness"], L.LAUNCHER_WORKSPACE_REQUIRED)

    def test_029e_missing_repository_reported(self):
        res = self.launcher(root=self.tmp).start()
        self.assertEqual(res["readiness"], L.LAUNCHER_MODULE_REQUIRED)

    def test_029f_owner_message_present_on_every_outcome(self):
        outcomes = [
            self.launcher(root=self.tmp).start(),
            self.launcher(health=lambda h, p: dict(HEALTHY)).start(),
            self.launcher(port_probe=lambda h, p: True, health=lambda h, p: dict(FOREIGN)).start(),
            self.launcher(health=lambda h, p: dict(UNHEALTHY), timeout=1.0).start(),
        ]
        for o in outcomes:
            self.assertTrue(o["owner_message"], o["readiness"])


# ================================================================ 7) stop behaviour
class TestLauncherStop(LauncherBase):
    def test_030_stop_accepted_console(self):
        self.ws.write_pid({"pid": 555, "process_start_token": "tok-555", "host": "127.0.0.1",
                           "port": 8780})
        alive = {"v": True}
        res = self.launcher(alive=lambda pid: alive["v"], health=lambda h, p: dict(HEALTHY),
                            terminate=lambda pid, hard=False: (self.terminated.append((pid, hard)),
                                                               alive.update(v=False), True)[-1]).stop()
        self.assertEqual(res["readiness"], L.LAUNCHER_STOPPED)
        self.assertTrue(res["identity_verified"])

    def test_030b_stop_clears_pid_record(self):
        self.ws.write_pid({"pid": 556, "process_start_token": "tok-556"})
        alive = {"v": True}
        self.launcher(alive=lambda pid: alive["v"], health=lambda h, p: dict(HEALTHY),
                      terminate=lambda pid, hard=False: (alive.update(v=False), True)[-1]).stop()
        self.assertIsNone(self.ws.read_pid())

    def test_030c_stop_signals_only_the_recorded_pid(self):
        self.ws.write_pid({"pid": 557, "process_start_token": "tok-557"})
        alive = {"v": True}
        self.launcher(alive=lambda pid: alive["v"], health=lambda h, p: dict(HEALTHY),
                      terminate=lambda pid, hard=False: (self.terminated.append((pid, hard)),
                                                         alive.update(v=False), True)[-1]).stop()
        self.assertEqual({pid for pid, _ in self.terminated}, {557})

    def test_030d_stop_tries_graceful_first(self):
        self.ws.write_pid({"pid": 558, "process_start_token": "tok-558"})
        alive = {"v": True}
        self.launcher(alive=lambda pid: alive["v"], health=lambda h, p: dict(HEALTHY),
                      terminate=lambda pid, hard=False: (self.terminated.append((pid, hard)),
                                                         alive.update(v=False), True)[-1]).stop()
        self.assertEqual(self.terminated[0][1], False)

    def test_031_stop_already_stopped(self):
        res = self.launcher(health=lambda h, p: dict(UNHEALTHY)).stop()
        self.assertEqual(res["readiness"], L.LAUNCHER_ALREADY_STOPPED)
        self.assertFalse(res["signalled"])

    def test_031b_stop_with_dead_pid_record(self):
        self.ws.write_pid({"pid": 559, "process_start_token": "tok-559"})
        res = self.launcher(alive=lambda pid: False).stop()
        self.assertEqual(res["readiness"], L.LAUNCHER_ALREADY_STOPPED)
        self.assertTrue(res["stale_pid_cleared"])
        self.assertEqual(self.terminated, [])

    def test_032_stop_refuses_reused_pid(self):
        self.ws.write_pid({"pid": 560, "process_start_token": "tok-OLD"})
        res = self.launcher(alive=lambda pid: True, start_token=lambda pid: "tok-NEW").stop()
        self.assertEqual(res["readiness"], L.LAUNCHER_STOP_REFUSED)
        self.assertEqual(res["error_code"], "PID_REUSED_BY_ANOTHER_PROCESS")
        self.assertEqual(self.terminated, [])

    def test_032b_stop_refuses_unproven_identity(self):
        self.ws.write_pid({"pid": 561, "process_start_token": "tok-561"})
        res = self.launcher(alive=lambda pid: True, start_token=lambda pid: None).stop()
        self.assertEqual(res["readiness"], L.LAUNCHER_STOP_REFUSED)
        self.assertEqual(res["error_code"], "PROCESS_IDENTITY_UNPROVEN")
        self.assertEqual(self.terminated, [])

    def test_032c_stop_refuses_console_it_did_not_start(self):
        res = self.launcher(health=lambda h, p: dict(HEALTHY)).stop()
        self.assertEqual(res["readiness"], L.LAUNCHER_STOP_REFUSED)
        self.assertEqual(res["error_code"], "NOT_LAUNCHER_OWNED")
        self.assertEqual(self.terminated, [])

    def test_033_stop_bounded_wait(self):
        self.ws.write_pid({"pid": 562, "process_start_token": "tok-562"})
        res = self.launcher(alive=lambda pid: True, health=lambda h, p: dict(HEALTHY),
                            stop_timeout=2.0).stop()
        self.assertEqual(res["readiness"], L.LAUNCHER_FAILED)
        self.assertEqual(res["error_code"], "CONSOLE_DID_NOT_STOP")
        self.assertLessEqual(res["stop_seconds"], 12.0)

    def test_033b_stop_escalates_after_grace(self):
        self.ws.write_pid({"pid": 563, "process_start_token": "tok-563"})
        res = self.launcher(alive=lambda pid: True, health=lambda h, p: dict(HEALTHY),
                            stop_timeout=3.0).stop()
        self.assertTrue(res["escalated"])
        self.assertIn((563, True), self.terminated)

    def test_033c_grace_window_shorter_than_budget(self):
        self.assertLess(L.STOP_GRACE_SECONDS, L.STOP_TIMEOUT_SECONDS)

    def test_033d_stop_records_command_identity_check(self):
        self.ws.write_pid({"pid": 564, "process_start_token": "tok-564"})
        alive = {"v": True}
        res = self.launcher(alive=lambda pid: alive["v"], health=lambda h, p: dict(HEALTHY),
                            terminate=lambda pid, hard=False: (alive.update(v=False), True)[-1]).stop()
        self.assertTrue(res["command_identity_verified"])


# ================================================================ 7b) stop owner-facing wording
# The owner text every non-stop outcome must keep, byte for byte, exactly as the accepted Phase 7.14
# baseline shipped it. The hotfix touches the stop path only; these are the control group.
ACCEPTED_NON_STOP_MESSAGES = {
    L.LAUNCHER_STARTING: "The toolkit is starting. Waiting until it is ready before opening a browser.",
    L.LAUNCHER_READY: "The toolkit is running. Your browser should now be open on the console.",
    L.LAUNCHER_ALREADY_RUNNING: "The toolkit was already running, so a second copy was not started.",
    L.LAUNCHER_TIMEOUT: ("The toolkit started but did not become ready in time. Run Stop-AMZ-Toolkit, "
                         "then try Start-AMZ-Toolkit once more."),
    L.LAUNCHER_PORT_BLOCKED: (L.PORT_IN_USE_MESSAGE + " — another program on this computer is "
                              "using it. Close that program, then run Start-AMZ-Toolkit again."),
    L.LAUNCHER_PYTHON_REQUIRED: ("A supported Python was not found. Install Python 3.9 or newer, then "
                                 "run Start-AMZ-Toolkit again."),
    L.LAUNCHER_MODULE_REQUIRED: ("This folder does not contain the toolkit console. Run "
                                 "Start-AMZ-Toolkit from inside the toolkit folder."),
    L.LAUNCHER_WORKSPACE_REQUIRED: ("The toolkit could not write to its own runtime folder. Check that "
                                    "the toolkit folder is not read-only."),
    L.LAUNCHER_LOCKED: "The toolkit is already starting. Wait a few seconds, then try again.",
    L.LAUNCHER_NOT_RUNNING: "The toolkit is not running yet. Run Start-AMZ-Toolkit first.",
    L.LAUNCHER_FAILED: "The toolkit could not be started. See the launcher log for the recorded reason.",
}


class TestStopOwnerMessage(LauncherBase):
    """Phase 7.14 hotfix - a Stop that fails must describe a stop.

    The accepted baseline mapped every SESSION7_14_LAUNCHER_FAILED result to one sentence, "The
    toolkit could not be started...", which the stop path also emitted on CONSOLE_DID_NOT_STOP. The
    canonical readiness state and error_code are unchanged; only the owner-facing sentence is now
    phase-accurate. What stop *does* - which process it signals, and the identity it proves first -
    is untouched.
    """

    # ---- outcome builders: each drives one real stop path through the injected seams -----------
    def stop_timeout(self):
        self.ws.clear_pid()
        self.ws.write_pid({"pid": 5620, "process_start_token": "tok-5620"})
        return self.launcher(alive=lambda pid: True, health=lambda h, p: dict(HEALTHY),
                             stop_timeout=2.0).stop()

    def stop_identity_unproven(self):
        self.ws.clear_pid()
        self.ws.write_pid({"pid": 5610, "process_start_token": "tok-5610"})
        return self.launcher(alive=lambda pid: True, start_token=lambda pid: None).stop()

    def stop_pid_reused(self):
        self.ws.clear_pid()
        self.ws.write_pid({"pid": 5600, "process_start_token": "tok-OLD"})
        return self.launcher(alive=lambda pid: True, start_token=lambda pid: "tok-NEW").stop()

    def stop_not_ours(self):
        self.ws.clear_pid()
        return self.launcher(health=lambda h, p: dict(HEALTHY)).stop()

    def stop_already_stopped(self):
        self.ws.clear_pid()
        return self.launcher(health=lambda h, p: dict(UNHEALTHY)).stop()

    def stop_stale_pid(self):
        self.ws.clear_pid()
        self.ws.write_pid({"pid": 5590, "process_start_token": "tok-5590"})
        return self.launcher(alive=lambda pid: False).stop()

    def stop_success(self):
        self.ws.clear_pid()
        self.ws.write_pid({"pid": 5550, "process_start_token": "tok-5550"})
        alive = {"v": True}
        return self.launcher(alive=lambda pid: alive["v"], health=lambda h, p: dict(HEALTHY),
                             terminate=lambda pid, hard=False: (self.terminated.append((pid, hard)),
                                                                alive.update(v=False), True)[-1]).stop()

    def all_stop_outcomes(self):
        return [self.stop_timeout(), self.stop_identity_unproven(), self.stop_pid_reused(),
                self.stop_not_ours(), self.stop_already_stopped(), self.stop_stale_pid(),
                self.stop_success()]

    # ---- 1) the defect itself: a failed stop never claims a failed start ----------------------
    def test_h01_stop_timeout_never_says_started(self):
        res = self.stop_timeout()
        self.assertEqual(res["error_code"], "CONSOLE_DID_NOT_STOP")
        self.assertNotIn("started", res["owner_message"])
        self.assertNotIn("could not be started", res["owner_message"])

    def test_h01b_no_stop_outcome_reports_a_start_failure(self):
        for res in self.all_stop_outcomes():
            self.assertNotIn("could not be started", res["owner_message"], res["readiness"])

    # ---- 2) a failed stop uses the verb "stopped" ---------------------------------------------
    def test_h02_every_stop_failure_uses_the_verb_stopped(self):
        for res in (self.stop_timeout(), self.stop_identity_unproven(), self.stop_pid_reused(),
                    self.stop_not_ours()):
            self.assertIn("stopped", res["owner_message"], res["readiness"])

    def test_h02b_generic_stop_failure_wording(self):
        self.assertTrue(L.STOP_FAILED_MESSAGE.startswith("The toolkit could not be stopped."))
        self.assertEqual(
            L._owner_message(L.LAUNCHER_FAILED, "A_FUTURE_STOP_CODE", "", phase="stop"),
            L.STOP_FAILED_MESSAGE)

    # ---- 3) timeout wording --------------------------------------------------------------------
    def test_h03_timeout_wording_is_accurate(self):
        """Stop-exit-verification hotfix: this outcome proves the process is STILL RUNNING (the
        injected alive seam is authoritative), so the owner is told exactly that. The accepted
        baseline said "did not stop within the allowed time", which describes the launcher's clock
        rather than the process, and read identically whether the process was alive or merely
        unproven. readiness and error_code are unchanged."""
        res = self.stop_timeout()
        self.assertEqual(res["readiness"], L.LAUNCHER_FAILED)
        self.assertEqual(res["error_code"], "CONSOLE_DID_NOT_STOP")
        self.assertEqual(res["exit_state"], L.EXIT_STATE_RUNNING)
        self.assertEqual(res["owner_message"], L.STOP_STILL_RUNNING_MESSAGE)
        self.assertIn("The toolkit is still running.", res["owner_message"])
        self.assertIn("Nothing else on this computer was stopped.", res["owner_message"])

    # ---- 4) identity refusal wording -----------------------------------------------------------
    def test_h04_identity_refusal_wording_is_accurate(self):
        """Null-start-token hotfix: this sentence gained the RECOVERY half it was missing.

        The accepted baseline said only "could not safely verify the process identity", which is
        true and completely unactionable — and it is printed in the one situation where the web
        console cannot be used to do anything about it. The machine contract (readiness, error_code)
        is unchanged; the owner is now told what state the computer is in and what to do next."""
        res = self.stop_identity_unproven()
        self.assertEqual(res["readiness"], L.LAUNCHER_STOP_REFUSED)
        self.assertEqual(res["error_code"], "PROCESS_IDENTITY_UNPROVEN")
        msg = res["owner_message"]
        self.assertIn("The toolkit was not stopped because the launcher could not confirm which "
                      "process it is.", msg)
        self.assertIn("Nothing on this computer was stopped.", msg)
        self.assertIn("Task Manager", msg)
        self.assertIn("Start-AMZ-Toolkit", msg)

    # ---- 5) unrelated-process refusal wording --------------------------------------------------
    def test_h05_unrelated_process_wording_is_accurate(self):
        want = "The process was not stopped because it was not started by this launcher."
        for res, code in ((self.stop_pid_reused(), "PID_REUSED_BY_ANOTHER_PROCESS"),
                          (self.stop_not_ours(), "NOT_LAUNCHER_OWNED")):
            self.assertEqual(res["readiness"], L.LAUNCHER_STOP_REFUSED)
            self.assertEqual(res["error_code"], code)
            self.assertIn(want, res["owner_message"])

    # ---- 6) already-stopped stays accurate -----------------------------------------------------
    def test_h06_already_stopped_wording_unchanged(self):
        for res in (self.stop_already_stopped(), self.stop_stale_pid()):
            self.assertEqual(res["readiness"], L.LAUNCHER_ALREADY_STOPPED)
            self.assertEqual(res["owner_message"],
                             "The toolkit was not running, so there was nothing to stop.")
            self.assertFalse(res["signalled"])
        self.assertEqual(self.terminated, [])

    # ---- 7) a successful stop states what was proven --------------------------------------------
    def test_h07_successful_stop_wording_states_a_safe_stop(self):
        """Stop-exit-verification hotfix: success is now reported only against a PROVEN exit, so the
        sentence says so. readiness (SESSION7_14_LAUNCHER_STOPPED) and identity_verified are
        unchanged; only the owner-facing sentence moved from "The toolkit has stopped." """
        res = self.stop_success()
        self.assertEqual(res["readiness"], L.LAUNCHER_STOPPED)
        self.assertEqual(res["owner_message"], L.STOP_SUCCESS_MESSAGE)
        self.assertEqual(res["owner_message"], "The toolkit stopped safely.")
        self.assertEqual(res["exit_state"], L.EXIT_STATE_EXITED)
        self.assertTrue(res["identity_verified"])

    # ---- 8) start wording is untouched ---------------------------------------------------------
    def test_h08_non_stop_owner_messages_unchanged(self):
        for state, want in ACCEPTED_NON_STOP_MESSAGES.items():
            self.assertEqual(L._OWNER_MESSAGES[state], want, state)
            self.assertEqual(L._owner_message(state, "", ""), want, state)

    def test_h08b_start_failure_still_reports_a_start(self):
        def spawn(cmd, cwd):
            raise OSError("cannot exec")
        res = self.launcher(spawn=spawn).start()
        self.assertEqual(res["readiness"], L.LAUNCHER_FAILED)
        self.assertEqual(res["error_code"], "CONSOLE_SPAWN_FAILED")
        self.assertEqual(res["owner_message"], ACCEPTED_NON_STOP_MESSAGES[L.LAUNCHER_FAILED])

    def test_h08c_start_timeout_message_unchanged(self):
        res = self.launcher(health=lambda h, p: dict(UNHEALTHY), timeout=1.0).start()
        self.assertEqual(res["readiness"], L.LAUNCHER_TIMEOUT)
        self.assertEqual(res["owner_message"], ACCEPTED_NON_STOP_MESSAGES[L.LAUNCHER_TIMEOUT])

    def test_h08d_stop_codes_never_leak_into_another_phase(self):
        for code in L._STOP_OWNER_MESSAGES:
            self.assertEqual(L._owner_message(L.LAUNCHER_FAILED, code, ""),
                             ACCEPTED_NON_STOP_MESSAGES[L.LAUNCHER_FAILED], code)

    # ---- 9) open wording is untouched -----------------------------------------------------------
    def test_h09_open_owner_messages_unchanged(self):
        res = self.launcher(health=lambda h, p: dict(UNHEALTHY)).open()
        self.assertEqual(res["readiness"], L.LAUNCHER_NOT_RUNNING)
        self.assertEqual(res["owner_message"], ACCEPTED_NON_STOP_MESSAGES[L.LAUNCHER_NOT_RUNNING])

    def test_h09b_open_success_and_browser_unavailable_unchanged(self):
        ok = self.launcher(health=lambda h, p: dict(HEALTHY)).open()
        self.assertEqual(ok["owner_message"],
                         ACCEPTED_NON_STOP_MESSAGES[L.LAUNCHER_ALREADY_RUNNING])
        nb = self.launcher(health=lambda h, p: dict(HEALTHY), browser=lambda u: False).open()
        self.assertEqual(nb["owner_message"],
                         "The toolkit is running but a browser could not be opened. Open this "
                         "address yourself: http://127.0.0.1:8780")

    # ---- 10) process identity protections are unchanged -----------------------------------------
    def test_h10_refusals_still_signal_nothing(self):
        for res in (self.stop_identity_unproven(), self.stop_pid_reused(), self.stop_not_ours()):
            self.assertFalse(res["signalled"], res["error_code"])
            self.assertFalse(res["identity_verified"], res["error_code"])
        self.assertEqual(self.terminated, [])

    def test_h10b_unprovable_identity_still_checked_before_mismatch(self):
        self.ws.clear_pid()
        self.ws.write_pid({"pid": 5611, "process_start_token": "tok-RECORDED"})
        res = self.launcher(alive=lambda pid: True, start_token=lambda pid: None).stop()
        self.assertEqual(res["error_code"], "PROCESS_IDENTITY_UNPROVEN")
        self.assertNotEqual(res["error_code"], "PID_REUSED_BY_ANOTHER_PROCESS")

    def test_h10c_canonical_codes_preserved_separately_from_owner_text(self):
        expected = {"CONSOLE_DID_NOT_STOP": L.LAUNCHER_FAILED,
                    "PROCESS_IDENTITY_UNPROVEN": L.LAUNCHER_STOP_REFUSED,
                    "PID_REUSED_BY_ANOTHER_PROCESS": L.LAUNCHER_STOP_REFUSED,
                    "NOT_LAUNCHER_OWNED": L.LAUNCHER_STOP_REFUSED}
        seen = {}
        for res in self.all_stop_outcomes():
            if res.get("error_code"):
                seen[res["error_code"]] = res["readiness"]
                # the machine code is carried in its own field, never printed at the owner
                self.assertNotIn(res["error_code"], res["owner_message"])
        self.assertEqual(seen, expected)

    # ---- 11) no broad process termination is introduced -----------------------------------------
    def test_h11_no_broad_process_termination_in_the_launcher(self):
        src = read(LAUNCHER_PATH)
        for bad in ("taskkill", "TASKKILL", "pkill", "killall", "Get-Process", "wmic", "psutil",
                    "process_iter", "python.exe", "pythonw", "os.system(", "os.popen("):
            self.assertNotIn(bad, src, bad)

    def test_h11b_only_identity_verified_recorded_pids_are_ever_signalled(self):
        self.all_stop_outcomes()
        self.assertEqual({pid for pid, _ in self.terminated}, {5620, 5550})

    # ---- 12) the PowerShell stop wrapper keeps stop-accurate, ASCII-only text --------------------
    def test_h12_stop_wrapper_text_names_a_stop_and_stays_ascii(self):
        body = script("Stop-AMZ-Toolkit.ps1")
        self.assertIn("The toolkit was not stopped.", body)
        self.assertNotIn("could not be started", body)
        self.assertEqual([c for c in body if ord(c) > 127], [])


# ================================================================ 8) open behaviour
class TestLauncherOpen(LauncherBase):
    def test_034_open_healthy_console(self):
        res = self.launcher(health=lambda h, p: dict(HEALTHY)).open()
        self.assertEqual(res["readiness"], L.LAUNCHER_ALREADY_RUNNING)
        self.assertEqual(self.browser_calls, ["http://127.0.0.1:8780"])

    def test_035_open_unhealthy_console_explains(self):
        res = self.launcher(health=lambda h, p: dict(UNHEALTHY)).open()
        self.assertEqual(res["readiness"], L.LAUNCHER_NOT_RUNNING)
        self.assertIn("Start-AMZ-Toolkit", res["owner_message"])

    def test_035b_open_unhealthy_opens_no_browser(self):
        self.launcher(health=lambda h, p: dict(UNHEALTHY)).open()
        self.assertEqual(self.browser_calls, [])

    def test_036_open_never_starts_a_server(self):
        spawned = []
        for health in (dict(HEALTHY), dict(UNHEALTHY)):
            self.launcher(health=lambda h, p, r=health: dict(r),
                          spawn=lambda cmd, cwd: spawned.append(c) or FakeProc()).open()
        self.assertEqual(spawned, [])

    def test_036b_open_reports_started_a_server_false(self):
        for health in (dict(HEALTHY), dict(UNHEALTHY)):
            res = self.launcher(health=lambda h, p, r=health: dict(r)).open()
            self.assertFalse(res["started_a_server"])

    def test_036c_open_browser_failure_bounded(self):
        res = self.launcher(health=lambda h, p: dict(HEALTHY), browser=lambda u: False).open()
        self.assertEqual(res["readiness"], L.LAUNCHER_BROWSER_UNAVAILABLE)
        self.assertIn("127.0.0.1:8780", res["owner_message"])

    def test_036d_status_reports_without_side_effects(self):
        spawned = []
        res = self.launcher(health=lambda h, p: dict(HEALTHY),
                            spawn=lambda cmd, cwd: spawned.append(c) or FakeProc()).status()
        self.assertEqual(res["readiness"], L.LAUNCHER_ALREADY_RUNNING)
        self.assertEqual(spawned, [])


# ================================================================ 9) launcher safety
class TestLauncherSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = read(LAUNCHER_PATH)
        cls.tree = ast.parse(cls.src)

    def test_037_no_taskkill_anywhere(self):
        for bad in ("taskkill", "TASKKILL", "pkill", "killall", "Get-Process", "wmic"):
            self.assertNotIn(bad, self.src)

    def test_037b_no_process_name_matching(self):
        for bad in ("python.exe", "pythonw", "process_iter", "psutil"):
            self.assertNotIn(bad, self.src)

    def test_037c_scripts_never_taskkill(self):
        for name in SCRIPTS:
            body = script(name)
            for bad in ("taskkill", "Stop-Process", "Get-Process", "kill "):
                self.assertNotIn(bad, body, "%s in %s" % (bad, name))

    def test_038_no_shell_true(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "shell":
                        self.assertNotEqual(getattr(kw.value, "value", None), True)
        self.assertNotIn("shell=True", self.src)

    def test_038b_no_os_system_or_popen_shell(self):
        for bad in ("os.system(", "os.popen(", "commands.getoutput"):
            self.assertNotIn(bad, self.src)

    def test_039_only_the_fixed_console_command(self):
        cmd = L.console_command(python_exe="python")
        self.assertEqual(cmd[1:], ["-m", "production.phase7_unified_owner_console",
                                   "--workspace-root", "runs/T2/phase7",
                                   "--host", "127.0.0.1", "--port", "8780", "serve"])

    def test_039b_command_module_is_a_constant(self):
        self.assertEqual(L.CONSOLE_MODULE, "production.phase7_unified_owner_console")
        self.assertEqual(L.CONSOLE_COMMAND_VERB, "serve")

    def test_039c_command_is_a_list_never_a_string(self):
        self.assertIsInstance(L.console_command(), list)

    def test_040_no_arbitrary_module(self):
        # the module name is never built from a variable
        self.assertEqual(self.src.count("CONSOLE_MODULE ="), 1)
        self.assertNotIn("importlib", self.src)
        self.assertNotIn("__import__(mod", self.src)

    def test_040b_workspace_root_escape_refused(self):
        for bad in ("../outside", "/etc", "C:\\Windows", "runs/../../x", "runs/T2/.."):
            with self.assertRaises(L.LauncherError, msg=bad):
                L.console_command(workspace_root=bad)

    def test_040c_workspace_root_normal_accepted(self):
        cmd = L.console_command(workspace_root="runs/T2/phase7")
        self.assertIn("runs/T2/phase7", cmd)

    def test_041_no_arbitrary_url(self):
        self.assertTrue(L.is_allowed_url("http://127.0.0.1:8780", "127.0.0.1", 8780))
        self.assertTrue(L.is_allowed_url("http://127.0.0.1:8780/", "127.0.0.1", 8780))
        for bad in ("http://example.com", "https://127.0.0.1:8780", "http://127.0.0.1:9999",
                    "file:///c:/", "http://127.0.0.1:8780/evil", "javascript:alert(1)"):
            self.assertFalse(L.is_allowed_url(bad, "127.0.0.1", 8780), bad)

    def test_041b_open_browser_refuses_foreign_url(self):
        calls = []
        with self.assertRaises(L.LauncherError):
            L.open_browser("http://example.com", "127.0.0.1", 8780, opener=calls.append)
        self.assertEqual(calls, [])

    def test_041c_console_url_is_loopback(self):
        self.assertEqual(L.console_url("127.0.0.1", 8780), "http://127.0.0.1:8780")
        self.assertEqual(L.console_url("::1", 8780), "http://[::1]:8780")

    def test_042_loopback_only(self):
        for good in ("127.0.0.1", "localhost", "::1"):
            self.assertTrue(L.is_loopback_host(good), good)
        for bad in ("0.0.0.0", "192.168.1.5", "10.0.0.1", "example.com", "", None,
                    "127.0.0.1.evil.com"):
            self.assertFalse(L.is_loopback_host(bad), bad)

    def test_042b_non_loopback_bind_refused(self):
        for bad in ("0.0.0.0", "192.168.1.5"):
            with self.assertRaises(L.LauncherError):
                L.validate_host(bad)

    def test_042c_non_loopback_probe_refused(self):
        with self.assertRaises(L.LauncherError):
            L._open_loopback("http://example.com/x", 1.0)

    # ---- regression guards for two defects the real double-click QA exposed -------------------
    def test_042d1_powershell_scripts_are_ascii_only(self):
        """Windows PowerShell 5.1 reads a BOM-less .ps1 as ANSI, so a non-ASCII character renders
        as mojibake in the owner's window. These scripts must stay pure ASCII."""
        for name in ("Start-AMZ-Toolkit.ps1", "Stop-AMZ-Toolkit.ps1", "Open-AMZ-Toolkit.ps1"):
            body = script(name)
            bad = [c for c in body if ord(c) > 127]
            self.assertEqual(bad, [], "%s contains non-ASCII: %r" % (name, bad[:5]))

    def test_042d2_version_probe_has_no_embedded_quote(self):
        """PowerShell 5.1 strips embedded double quotes when building a native command line, which
        silently corrupts an inline Python one-liner. The probe must contain no quote character."""
        for name in ("Start-AMZ-Toolkit.ps1", "Stop-AMZ-Toolkit.ps1", "Open-AMZ-Toolkit.ps1"):
            for line in script(name).splitlines():
                if "'-c'" not in line:
                    continue
                inner = line.split("'-c'", 1)[1]
                probe = inner[inner.index("'") + 1:inner.rindex("'")]
                self.assertNotIn('"', probe, "%s: quoted probe %r" % (name, probe))
                self.assertIn("sys.version", probe)

    def test_042d3_version_probe_is_not_stderr_redirected(self):
        """A redirected native stderr becomes a terminating ErrorRecord under -ErrorAction Stop."""
        for name in ("Start-AMZ-Toolkit.ps1", "Stop-AMZ-Toolkit.ps1", "Open-AMZ-Toolkit.ps1"):
            for line in script(name).splitlines():
                if "'-c'" in line:
                    self.assertNotIn("2>$null", line, name)
                    self.assertNotIn("2>&1", line, name)

    def test_042d4_probe_runs_with_continue_preference(self):
        for name in ("Start-AMZ-Toolkit.ps1", "Stop-AMZ-Toolkit.ps1", "Open-AMZ-Toolkit.ps1"):
            self.assertIn("$ErrorActionPreference = 'Continue'", script(name), name)

    def test_042d5_batch_files_use_crlf(self):
        """cmd.exe mis-parses an LF-only batch file, so the .bat launchers are pinned to CRLF."""
        ga = read(os.path.join(ROOT, ".gitattributes"))
        for name in ("Start-AMZ-Toolkit.bat", "Stop-AMZ-Toolkit.bat", "Open-AMZ-Toolkit.bat"):
            self.assertIn("%s text eol=crlf" % name, ga, name)

    def test_042d6_scripts_delegate_the_console_command(self):
        """The console command lives in ONE place; the scripts must never inline it themselves."""
        for name in ("Start-AMZ-Toolkit.ps1", "Stop-AMZ-Toolkit.ps1", "Open-AMZ-Toolkit.ps1"):
            body = script(name)
            self.assertNotIn("phase7_unified_owner_console", body, name)
            self.assertIn("production.phase7_owner_launcher", body, name)

    def test_042e_scripts_bind_loopback_only(self):
        for name in ("Start-AMZ-Toolkit.ps1", "Stop-AMZ-Toolkit.ps1", "Open-AMZ-Toolkit.ps1"):
            body = script(name)
            self.assertIn("127.0.0.1", body)
            for bad in ("0.0.0.0", "192.168.", "--host 0"):
                self.assertNotIn(bad, body, "%s in %s" % (bad, name))

    def test_043_logs_are_secret_free(self):
        for probe in ("PHASE7_12_WEBHOOK_URL=https://hooks.example.com/secret",
                      "csrf_token=abc123", "Authorization=Bearer xyz", "session=deadbeef",
                      "api_key=k-123", "password=hunter2", "cookie=console_session=zzz"):
            red = L.redact(probe)
            self.assertIn("[REDACTED]", red, probe)
            for leak in ("hooks.example.com/secret", "abc123", "xyz", "deadbeef", "k-123",
                         "hunter2", "zzz"):
                self.assertNotIn(leak, red, "%s leaked from %s" % (leak, probe))

    def test_043b_log_lines_are_bounded(self):
        self.assertLessEqual(len(L.redact("x" * 5000)), L.MAX_LOG_LINE)

    def test_043c_log_never_multiline(self):
        self.assertNotIn("\n", L.redact("a\nb\r\nc"))

    def test_043d_log_rotates(self):
        tmp = tempfile.mkdtemp(prefix="p714-log-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ws = L.Workspace(tmp)
        ws.ensure()
        with open(ws.path(L.LOG_FILE), "w", encoding="utf-8") as f:
            f.write("x" * (L.MAX_LOG_BYTES + 10))
        ws.log("event")
        self.assertTrue(os.path.isfile(ws.path(L.LOG_FILE) + ".1"))
        self.assertLess(os.path.getsize(ws.path(L.LOG_FILE)), 1000)

    def test_043e_no_environ_dump(self):
        self.assertNotIn("os.environ)", self.src)
        self.assertNotIn("dict(os.environ", self.src)

    def test_043f_relpath_never_leaks_absolute(self):
        self.assertNotIn(":", L._relpath(os.path.join(ROOT, "production", "x.py")))

    def test_044_launcher_runtime_is_ignored_by_git(self):
        self.assertTrue(L.LAUNCHER_SUBDIR.replace("\\", "/").startswith("runs/"))
        gi = read(os.path.join(ROOT, ".gitignore"))
        self.assertTrue(any(line.strip() in ("runs/", "/runs/", "runs", "/runs")
                            for line in gi.splitlines()))

    def test_044b_launcher_runtime_files_untracked(self):
        out = subprocess.run(["git", "ls-files", "runs/"], cwd=ROOT, capture_output=True,
                             text=True).stdout.strip()
        self.assertEqual(out, "")

    def test_044c_launcher_never_registers_service_or_scheduler(self):
        for bad in ("schtasks", "New-Service", "sc.exe", "Register-ScheduledTask", "systemd",
                    "Startup\\", "CurrentVersion\\Run"):
            self.assertNotIn(bad, self.src)
            for name in SCRIPTS:
                self.assertNotIn(bad, script(name), "%s in %s" % (bad, name))

    def test_044d_launcher_never_claims_a_seller_action(self):
        for k, v in L.SELLER_CENTRAL_COUNTERS.items():
            self.assertEqual(v, 0, k)
        self.assertTrue(all(L.LAUNCHER_NEVER.values()))

    def test_044e_no_network_beyond_loopback_probe(self):
        # the only outbound call site is the bounded loopback health probe
        self.assertEqual(self.src.count("urllib.request.build_opener"), 1)
        self.assertNotIn("socket.create_connection((h", self.src.replace(
            'socket.create_connection((h, int(port))', ''))


# ================================================================ 10) next-action priority
class TestNextActionPriority(unittest.TestCase):
    def test_045_overview_carries_next_action(self):
        m = model()
        m["next_action"] = NA.build_next_action(m)
        self.assertEqual(m["next_action"]["schema_version"], "phase7.14.next-action.v1")

    def test_046_priority_is_deterministic(self):
        for p, build in CASES.items():
            a = NA.build_next_action(build())
            b = NA.build_next_action(build())
            self.assertEqual(a["next_action_identity"], b["next_action_identity"], p)

    def test_046b_rule_table_is_total_and_ordered(self):
        rules = NA.describe_rules()
        self.assertEqual([r["priority"] for r in rules], list(range(1, 15)))
        self.assertEqual(len(NA.RULES), NA.MAX_PRIORITY)

    def test_046c_every_priority_reachable(self):
        for p, build in CASES.items():
            self.assertEqual(NA.build_next_action(build())["priority"], p)

    def test_047_integrity_block_is_highest(self):
        m = CASES[1]()
        m["overview"].update({"pending_decisions": 9, "due_followups": 9, "open_alerts": 9,
                              "update_available": True, "backup_snapshots": 0})
        na = NA.build_next_action(m)
        self.assertEqual(na["priority"], 1)
        self.assertEqual(na["readiness"], NA.NEXT_ACTION_BLOCKED)

    def test_047b_blocked_module_is_priority_one(self):
        m = model(overview={"blocked_modules": ["research"]})
        self.assertEqual(NA.build_next_action(m)["priority"], 1)

    def test_047c_integrity_messages_differ_by_cause(self):
        audit = NA.build_next_action(CASES[1]())
        mod = NA.build_next_action(model(overview={"blocked_modules": ["research"]}))
        self.assertNotEqual(audit["owner_message"], mod["owner_message"])
        self.assertIn("State-changing actions are blocked", audit["owner_message"])

    def test_048_security_block_second(self):
        m = CASES[2]()
        m["overview"].update({"pending_decisions": 9, "due_followups": 9})
        na = NA.build_next_action(m)
        self.assertEqual(na["priority"], 2)
        self.assertEqual(na["readiness"], NA.NEXT_ACTION_BLOCKED)

    def test_048b_unrefused_boundary_triggers_security(self):
        m = model(system={"connectivity_boundary": {"seller_central_blocked": False,
                                                    "seller_api_blocked": True,
                                                    "advertising_api_blocked": True}})
        self.assertEqual(NA.build_next_action(m)["priority"], 2)

    def test_048c_intact_boundary_does_not_trigger(self):
        self.assertNotEqual(NA.build_next_action(model())["priority"], 2)

    def test_049_required_module_unavailable(self):
        na = NA.build_next_action(CASES[3]())
        self.assertEqual(na["priority"], 3)
        self.assertEqual(na["canonical_status"], NA.USABILITY_REQUIRED)

    def test_050_missing_required_data(self):
        na = NA.build_next_action(CASES[4]())
        self.assertEqual(na["priority"], 4)
        self.assertEqual(na["owner_title"], "No current report analysis found")

    def test_050b_zero_analyzed_rows_counts_as_missing(self):
        m = model(sections={"analysis": {"status": "READY", "counts": {"analyzed_rows": 0}}})
        self.assertEqual(NA.build_next_action(m)["priority"], 4)

    def test_051_stale_critical_data(self):
        na = NA.build_next_action(CASES[5]())
        self.assertEqual(na["priority"], 5)
        self.assertIn("7.3", na["source_ids"])

    def test_051b_stale_non_critical_phase_ignored(self):
        m = model(freshness={"7.10": {"present": True, "stale": True}})
        self.assertNotEqual(NA.build_next_action(m)["priority"], 5)

    def test_052_pending_decision(self):
        na = NA.build_next_action(CASES[6]())
        self.assertEqual(na["priority"], 6)
        self.assertEqual(na["destination"]["section"], "decisions")

    def test_052b_pending_decision_counts_shown(self):
        self.assertIn("3", NA.build_next_action(CASES[6]())["owner_message"])

    def test_053_pending_manual_action(self):
        na = NA.build_next_action(CASES[7]())
        self.assertEqual(na["priority"], 7)
        self.assertEqual(na["destination"]["section"], "manual-actions")

    def test_053b_manual_action_states_owner_is_the_bridge(self):
        msg = NA.build_next_action(CASES[7]())["why_it_matters"]
        self.assertIn("never changes anything in your Amazon account", msg)

    def test_054_due_followup(self):
        na = NA.build_next_action(CASES[8]())
        self.assertEqual(na["priority"], 8)
        self.assertEqual(na["destination"]["section"], "outcomes")

    def test_055_open_critical_alert(self):
        na = NA.build_next_action(CASES[9]())
        self.assertEqual(na["priority"], 9)
        self.assertEqual(na["destination"]["page"], "alerts")
        self.assertIn("al-1", na["source_ids"])

    def test_055b_important_alert_also_escalates(self):
        m = model(sections={"watchlists": {"status": "READY", "counts": {}, "alerts": [
            {"alert_id": "al-2", "status": "OPEN", "severity": "IMPORTANT"}]}})
        self.assertEqual(NA.build_next_action(m)["priority"], 9)

    def test_055c_info_alert_does_not_escalate(self):
        m = model(sections={"watchlists": {"status": "READY", "counts": {}, "alerts": [
            {"alert_id": "al-3", "status": "OPEN", "severity": "INFO"}]}})
        self.assertNotEqual(NA.build_next_action(m)["priority"], 9)

    def test_055d_acknowledged_alert_does_not_escalate(self):
        m = model(sections={"watchlists": {"status": "READY", "counts": {}, "alerts": [
            {"alert_id": "al-4", "status": "ACKNOWLEDGED", "severity": "CRITICAL"}]}})
        self.assertNotEqual(NA.build_next_action(m)["priority"], 9)

    def test_056_due_watchlist(self):
        self.assertEqual(NA.build_next_action(CASES[10]())["priority"], 10)

    def test_057_notification_unknown(self):
        na = NA.build_next_action(CASES[11]())
        self.assertEqual(na["priority"], 11)
        self.assertIn("UNKNOWN", na["owner_message"])

    def test_057b_unknown_is_never_called_failed(self):
        na = NA.build_next_action(CASES[11]())
        self.assertNotIn("failed", NA.owner_text(na).lower().replace("never treated as failed", ""))

    def test_058_backup_missing_or_stale(self):
        self.assertEqual(NA.build_next_action(CASES[12]())["priority"], 12)

    def test_058b_stale_backup_triggers(self):
        m = model(freshness={"7.9": {"present": True, "stale": True}})
        self.assertEqual(NA.build_next_action(m)["priority"], 12)

    def test_058c_backup_module_unavailable_is_unavailable_destination(self):
        m = model(sections={"backup": {"status": "MODULE_UNAVAILABLE", "counts": {}}})
        na = NA.build_next_action(m)
        self.assertEqual(na["priority"], 12)
        self.assertEqual(na["destination"]["type"], NA.DEST_UNAVAILABLE)

    def test_059_update_available(self):
        na = NA.build_next_action(CASES[13]())
        self.assertEqual(na["priority"], 13)
        self.assertIn("never installed for you", na["why_it_matters"])

    def test_060_no_urgent_action(self):
        na = NA.build_next_action(CASES[14]())
        self.assertEqual(na["priority"], 14)
        self.assertEqual(na["readiness"], NA.NEXT_ACTION_NONE)
        self.assertEqual(na["owner_title"], "No urgent action")

    def test_060b_no_urgent_action_is_not_an_error(self):
        na = NA.build_next_action(CASES[14]())
        self.assertEqual(na["canonical_status"], NA.NO_URGENT_ACTION)
        self.assertNotIn("error", NA.owner_text(na).lower())

    def test_060c_higher_priority_always_wins(self):
        for high in range(1, 14):
            m = CASES[high]()
            m["overview"]["update_available"] = True         # priority 13 also true
            self.assertEqual(NA.build_next_action(m)["priority"], high, high)

    def test_060d_empty_model_degrades_safely(self):
        na = NA.build_next_action({})
        self.assertIn(na["priority"], (3, 4))
        self.assertTrue(NA.validate_destination(na["destination"]))


# ================================================================ 11) guidance language
class TestNextActionLanguage(unittest.TestCase):
    def all_actions(self):
        return [NA.build_next_action(build()) for build in CASES.values()]

    def test_061_no_invented_metric(self):
        # every number in the owner text traces to a count supplied in the model
        na = NA.build_next_action(CASES[6]())
        self.assertIn("3", na["owner_message"])
        self.assertNotIn("%", NA.owner_text(na))
        for a in self.all_actions():
            self.assertNotIn("score", NA.owner_text(a).lower())
            self.assertNotIn("estimated", NA.owner_text(a).lower())

    def test_062_no_causal_language(self):
        for a in self.all_actions():
            self.assertEqual(NA.forbidden_phrases_in(a), [], a["rule_id"])

    def test_062b_causal_words_are_actually_banned(self):
        fake = {"owner_title": "t", "owner_message": "this caused that", "why_it_matters": "",
                "recommended_step": "", "expected_result": "", "destination": {}}
        self.assertIn("caused", NA.forbidden_phrases_in(fake))

    def test_062c_word_boundary_avoids_false_positives(self):
        fake = {"owner_title": "enabled to proceed", "owner_message": "forbidden overbid handling",
                "why_it_matters": "", "recommended_step": "", "expected_result": "",
                "destination": {}}
        self.assertEqual(NA.forbidden_phrases_in(fake), [])

    def test_063_no_ppc_recommendation(self):
        for a in self.all_actions():
            low = NA.owner_text(a).lower()
            for banned in ("ppc", "campaign", "bid", "budget", "ad group"):
                self.assertNotIn(banned, low.split(), banned)

    def test_064_no_advertising_analysis_wording(self):
        for a in self.all_actions():
            self.assertNotIn("advertising analysis", NA.owner_text(a).lower())

    def test_064b_absent_analysis_uses_the_required_wording(self):
        self.assertEqual(NA.build_next_action(CASES[4]())["owner_title"],
                         "No current report analysis found")

    def test_065_no_fake_import_page(self):
        for a in self.all_actions():
            dest = a["destination"]
            self.assertNotEqual(dest.get("page"), "import")
            self.assertNotIn("import page", NA.owner_text(a).lower())

    def test_065b_import_is_only_ever_an_instruction(self):
        na = NA.build_next_action(CASES[3]())
        self.assertEqual(na["destination"]["type"], NA.DEST_INSTRUCTIONS)
        self.assertTrue(any("Import" in s for s in na["destination"]["steps"]))

    def test_065c_engine_refuses_refused_wording(self):
        original = NA._r14_none

        def bad(f):
            out = original(f)
            out["owner_message"] = "this proves the campaign caused it"
            return out
        NA.RULES[-1].__code__  # sanity: rules are plain functions
        try:
            NA.__dict__["_r14_none"] = bad
            rules = list(NA.RULES)
            rules[-1] = bad
            NA.RULES = tuple(rules)
            with self.assertRaises(ValueError):
                NA.build_next_action(model())
        finally:
            NA.__dict__["_r14_none"] = original
            rules = list(NA.RULES)
            rules[-1] = original
            NA.RULES = tuple(rules)

    def test_065d_no_seller_account_recommendation(self):
        for a in self.all_actions():
            low = NA.owner_text(a).lower()
            for banned in ("seller central", "sp-api", "ads api", "bulk upload", "sign in to amazon"):
                self.assertNotIn(banned, low)

    def test_065e_amazon_action_never_implied(self):
        for a in self.all_actions():
            self.assertFalse(a["amazon_action_implied"])


# ================================================================ 12) destinations
class TestNextActionDestinations(unittest.TestCase):
    def all_actions(self):
        return [NA.build_next_action(build()) for build in CASES.values()]

    def test_066_existing_destination_only(self):
        for a in self.all_actions():
            self.assertTrue(NA.validate_destination(a["destination"]), a["rule_id"])

    def test_066b_pages_are_real_console_pages(self):
        for a in self.all_actions():
            page = a["destination"].get("page")
            if page is not None:
                self.assertIn(page, NA.CONSOLE_PAGES)

    def test_066c_sections_are_real_analysis_views(self):
        for a in self.all_actions():
            sec = a["destination"].get("section")
            if sec is not None:
                self.assertIn(sec, NA.ANALYSIS_SECTIONS)

    def test_066d_console_pages_match_front_end_views(self):
        js = static("app.js")
        for page in NA.CONSOLE_PAGES:
            self.assertIn('id: "%s"' % page, js, page)

    def test_066e_analysis_sections_match_backend_views(self):
        py = read(CONSOLE_PATH)
        for sec in NA.ANALYSIS_SECTIONS:
            self.assertIn('sub == "%s"' % sec, py + 'sub == "analysis"', sec)

    def test_066f_unknown_page_refused(self):
        with self.assertRaises(ValueError):
            NA._destination_page("import")
        self.assertFalse(NA.validate_destination({"type": NA.DEST_PAGE, "page": "import"}))

    def test_066g_unknown_section_refused(self):
        with self.assertRaises(ValueError):
            NA._destination_page("analysis", section="nope")

    def test_067_cli_copy_destination_valid(self):
        d = NA._destination_command(NA.SUPPORTED_COMMANDS[0])
        self.assertTrue(NA.validate_destination(d))

    def test_067b_unsupported_command_refused(self):
        with self.assertRaises(ValueError):
            NA._destination_command("rm -rf /")
        self.assertFalse(NA.validate_destination({"type": NA.DEST_COMMAND, "command": "rm -rf /"}))

    def test_067c_supported_commands_are_real_modules(self):
        for cmd in NA.SUPPORTED_COMMANDS:
            m = re.search(r"-m\s+(production\.[a-z0-9_]+)", cmd)
            self.assertIsNotNone(m, cmd)
            self.assertTrue(os.path.isfile(os.path.join(
                ROOT, m.group(1).replace(".", os.sep) + ".py")), cmd)

    def test_067d_supported_commands_touch_no_amazon_account(self):
        for cmd in NA.SUPPORTED_COMMANDS:
            for bad in ("sellercentral", "sp-api", "advertising-api", "signin"):
                self.assertNotIn(bad, cmd.lower())

    def test_068_instruction_only_destination(self):
        na = NA.build_next_action(CASES[3]())
        d = na["destination"]
        self.assertEqual(d["type"], NA.DEST_INSTRUCTIONS)
        self.assertTrue(len(d["steps"]) >= 2)
        self.assertTrue(NA.validate_destination(d))

    def test_068b_instruction_command_is_allowlisted(self):
        d = NA.build_next_action(CASES[3]())["destination"]
        self.assertIn(d["command"], NA.SUPPORTED_COMMANDS)

    def test_068c_empty_instructions_refused(self):
        self.assertFalse(NA.validate_destination({"type": NA.DEST_INSTRUCTIONS, "steps": []}))

    def test_069_disabled_destination_reason(self):
        m = model(sections={"backup": {"status": "MODULE_UNAVAILABLE", "counts": {}}})
        d = NA.build_next_action(m)["destination"]
        self.assertEqual(d["type"], NA.DEST_UNAVAILABLE)
        self.assertTrue(d["reason"].strip())
        self.assertEqual(d["page"], "backups")

    def test_069b_unavailable_without_reason_refused(self):
        self.assertFalse(NA.validate_destination({"type": NA.DEST_UNAVAILABLE, "reason": ""}))

    def test_069c_all_four_destination_kinds_reachable(self):
        kinds = {NA.build_next_action(b())["destination"]["type"] for b in CASES.values()}
        kinds.add(NA.build_next_action(model(
            sections={"backup": {"status": "MODULE_UNAVAILABLE", "counts": {}}}))["destination"]["type"])
        self.assertIn(NA.DEST_PAGE, kinds)
        self.assertIn(NA.DEST_INSTRUCTIONS, kinds)
        self.assertIn(NA.DEST_UNAVAILABLE, kinds)
        # the copy-a-command kind is reachable through the instruction destination's command field
        self.assertIn("command", NA.build_next_action(CASES[3]())["destination"])
        self.assertTrue(NA.validate_destination(NA._destination_command(NA.SUPPORTED_COMMANDS[0])))

    def test_069d_unknown_destination_type_refused(self):
        self.assertFalse(NA.validate_destination({"type": "teleport"}))
        self.assertFalse(NA.validate_destination(None))

    def test_070_source_authorities_retained(self):
        for a in self.all_actions():
            self.assertTrue(a["source_authorities"], a["rule_id"])
            for auth in a["source_authorities"]:
                self.assertTrue(auth.startswith("production.") or auth.startswith("core."))

    def test_070b_authorities_are_accepted_modules(self):
        for a in self.all_actions():
            for auth in a["source_authorities"]:
                mod = auth.split(" ")[0].replace(".", os.sep) + ".py"
                self.assertTrue(os.path.isfile(os.path.join(ROOT, mod)), auth)

    def test_071_source_ids_retained_and_bounded(self):
        na = NA.build_next_action(CASES[9]())
        self.assertEqual(na["source_ids"], ["al-1"])
        many = model(sections={"watchlists": {"status": "READY", "counts": {}, "alerts": [
            {"alert_id": "al-%d" % i, "status": "OPEN", "severity": "CRITICAL"} for i in range(50)]}})
        self.assertLessEqual(len(NA.build_next_action(many)["source_ids"]), NA.MAX_SOURCE_IDS)

    def test_071b_source_ids_never_contain_a_path(self):
        for a in self.all_actions():
            for sid in a["source_ids"]:
                self.assertNotIn("/", sid)
                self.assertNotIn("\\", sid)

    def test_071c_decision_package_id_retained(self):
        self.assertEqual(NA.build_next_action(CASES[6]())["source_ids"], ["pkg-1"])


# ================================================================ 13) guidance shape
class TestNextActionFields(unittest.TestCase):
    def all_actions(self):
        return [NA.build_next_action(build()) for build in CASES.values()]

    def test_072_owner_title_present(self):
        for a in self.all_actions():
            self.assertTrue(a["owner_title"].strip())
            self.assertLess(len(a["owner_title"]), 70)

    def test_073_owner_message_present(self):
        for a in self.all_actions():
            self.assertTrue(a["owner_message"].strip())

    def test_074_why_it_matters_present(self):
        for a in self.all_actions():
            self.assertTrue(a["why_it_matters"].strip())

    def test_075_recommended_step_present(self):
        for a in self.all_actions():
            self.assertTrue(a["recommended_step"].strip())

    def test_076_expected_result_present(self):
        for a in self.all_actions():
            self.assertTrue(a["expected_result"].strip())

    def test_077_canonical_status_declared(self):
        for a in self.all_actions():
            self.assertIn(a["canonical_status"], NA.CANONICAL_STATUSES)
            self.assertIn(a["readiness"], NA.READINESS_STATES)

    def test_077b_canonical_statuses_are_phase_7_14(self):
        for s in NA.CANONICAL_STATUSES + NA.READINESS_STATES:
            self.assertTrue(s.startswith("SESSION7_14_"), s)

    def test_078_generated_at_excluded_from_identity(self):
        m = model()
        a = NA.build_next_action(m, generated_at="2026-01-01T00:00:00Z")
        b = NA.build_next_action(m, generated_at="2030-12-31T23:59:59Z")
        self.assertNotEqual(a["generated_at"], b["generated_at"])
        self.assertEqual(a["next_action_identity"], b["next_action_identity"])

    def test_078b_identity_changes_when_guidance_changes(self):
        self.assertNotEqual(NA.build_next_action(CASES[6]())["next_action_identity"],
                            NA.build_next_action(CASES[7]())["next_action_identity"])

    def test_078c_identity_field_excluded_from_itself(self):
        a = NA.build_next_action(model())
        self.assertEqual(a["next_action_identity"], NA.next_action_identity(a))

    def test_078d_schema_hash_stable(self):
        self.assertEqual(NA.schema_hash(), NA.schema_hash())
        self.assertEqual(len(NA.schema_hash()), 64)

    def test_078e_every_declared_field_present(self):
        for a in self.all_actions():
            self.assertEqual(set(a), set(NA.SCHEMA_FIELDS))

    def test_078f_json_serialisable_and_canonical(self):
        for a in self.all_actions():
            self.assertEqual(json.loads(UC.canonical_json(a))["schema_version"],
                             "phase7.14.next-action.v1")

    # ---- overall usability readiness ------------------------------------------------------------
    def test_078g_usability_states_declared(self):
        for s in NA.USABILITY_STATES:
            self.assertTrue(s.startswith("SESSION7_14_USABILITY_"), s)
        self.assertEqual(len(set(NA.USABILITY_STATES)), 4)

    def test_078h_usability_blocked_for_priority_1_2(self):
        for p in (1, 2):
            self.assertEqual(NA.usability_readiness(NA.build_next_action(CASES[p]())),
                             NA.USABILITY_BLOCKED, p)

    def test_078i_usability_required_for_setup_priorities(self):
        for p in (3, 4, 5):
            self.assertEqual(NA.usability_readiness(NA.build_next_action(CASES[p]())),
                             NA.USABILITY_REQUIRED, p)

    def test_078j_usability_partial_when_work_is_waiting(self):
        for p in range(6, 14):
            self.assertEqual(NA.usability_readiness(NA.build_next_action(CASES[p]())),
                             NA.USABILITY_READY_PARTIAL, p)

    def test_078k_usability_ready_when_nothing_waiting(self):
        self.assertEqual(NA.usability_readiness(NA.build_next_action(CASES[14]())),
                         NA.USABILITY_READY)

    def test_078l_usability_labels_are_owner_words(self):
        self.assertEqual(set(NA.USABILITY_LABELS), set(NA.USABILITY_STATES))
        for label in NA.USABILITY_LABELS.values():
            self.assertNotIn("SESSION7_14", label)

    def test_078m_usability_degrades_on_missing_guidance(self):
        self.assertEqual(NA.usability_readiness(None), NA.USABILITY_READY)
        self.assertEqual(NA.usability_readiness({}), NA.USABILITY_READY)


# ================================================================ 14) console API wiring
class TestConsoleWiring(unittest.TestCase):
    def test_045b_next_action_endpoint_declared(self):
        self.assertIn("next-action", UC._READ_ENDPOINTS)

    def test_045c_overview_includes_next_action(self):
        py = read(CONSOLE_PATH)
        self.assertIn('"next_action": model.get("next_action")', py)

    def test_045d_console_model_builds_next_action(self):
        py = read(CONSOLE_PATH)
        self.assertIn("NEXT.build_next_action(model", py)

    def test_045e_accepted_overview_fields_still_present(self):
        # the 7.13 contract is additive-only
        for key in ("console_readiness", "owner_label", "module_status", "pending_decisions",
                    "pending_manual_actions", "due_followups", "open_alerts", "notification_sent",
                    "notification_failed", "notification_unknown", "backup_snapshots",
                    "update_available", "stale_data_phases", "seller_central_counters"):
            self.assertIn('"%s"' % key, read(CONSOLE_PATH), key)

    def test_045f_module_owner_labels_declared(self):
        # "workflow" (DASHBOARD-V1-SPEC.md's 13-stage overview) was added deliberately -- see
        # production.phase7_workflow_stage_model / build_workflow_section. Same update already
        # made to test_phase7_13_unified_owner_console.test_179_module_status_count.
        self.assertEqual(set(UC.MODULE_OWNER_LABELS),
                         {"analysis", "research", "watchlists", "notifications", "backup",
                          "workflow"})

    def test_045g_favicon_served(self):
        self.assertIn("favicon.svg", UC.STATIC_FILES)
        self.assertEqual(UC.FAVICON_ALIASES["favicon.ico"], "favicon.svg")
        self.assertTrue(os.path.isfile(os.path.join(STATIC_DIR, "favicon.svg")))

    def test_045l_overview_publishes_usability_readiness(self):
        py = read(CONSOLE_PATH)
        self.assertIn('NEXT.usability_readiness(model["next_action"])', py)
        self.assertIn("usability_label", py)

    def test_045m_front_end_maps_every_usability_state(self):
        js = static("app.js")
        for s in NA.USABILITY_STATES:
            self.assertIn('"%s"' % s, js, s)

    def test_045n_front_end_prefers_the_usability_state(self):
        self.assertIn("ov.usability_readiness || readiness", static("app.js"))

    def test_045h_validate_only_still_clean(self):
        tmp = tempfile.mkdtemp(prefix="p714-uc-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        res = UC.validate_only(UC.Config(os.path.join(tmp, "7.13"), tmp))
        self.assertTrue(res["ok"], [c for c in res["checks"] if not c["ok"]])

    def test_045i_next_action_never_mutates_the_model(self):
        m = model()
        before = UC.canonical_json(m)
        NA.build_next_action(m)
        self.assertEqual(UC.canonical_json(m), before)

    def test_045j_next_action_module_has_no_write_path(self):
        src = read(NEXT_PATH)
        for bad in ("open(", "os.makedirs", "os.remove", "shutil.", "subprocess", "urllib",
                    "socket", "requests"):
            self.assertNotIn(bad, src, bad)

    def test_045k_next_action_module_reads_no_file(self):
        tree = ast.parse(read(NEXT_PATH))
        names = {n.func.id for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertNotIn("open", names)


# ================================================================ 15) front-end contracts (static)
class TestFrontEndStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = static("index.html")
        cls.js = static("app.js")
        cls.css = static("styles.css")
        cls.icons = static("icons.svg")

    def test_079_overview_hierarchy_declared(self):
        for block in ("heading", "next-action", "attention", "counts", "freshness", "modules",
                      "activity", "technical"):
            self.assertIn('"data-block": "%s"' % block, self.js, block)

    def test_080_next_action_declared_before_metrics(self):
        # source order of the RENDER CALLS inside renderOverview is what fixes the page order
        body = self.js[self.js.index("function renderOverview"):self.js.index("function nextActionPanel")]
        self.assertLess(body.index("nextActionPanel(na)"), body.index('"data-block": "counts"'))
        self.assertLess(body.index("nextActionPanel(na)"), body.index('"data-block": "modules"'))

    def test_081_progressive_disclosure_used(self):
        self.assertIn('el("details"', self.js)
        self.assertIn("View details", self.js)
        self.assertIn("details.tech", self.css)

    def test_081b_disclosure_not_open_by_default(self):
        self.assertNotIn('open: "open"', self.js)
        self.assertNotIn('"open": true', self.js)

    def test_082_empty_state_helper_exists(self):
        self.assertIn("function emptyState", self.js)
        self.assertIn("es-title", self.js)

    def test_083_empty_state_explains_missing(self):
        self.assertIn("es-title", self.js)
        self.assertIn("No current report analysis found", self.js)

    def test_084_empty_state_says_whether_error(self):
        self.assertIn("This is not an error.", self.js)
        self.assertIn("This is a problem that needs your attention.", self.js)

    def test_085_empty_state_explains_next_step(self):
        self.assertIn("es-next", self.js)
        self.assertIn("Next step: ", self.js)

    def test_085b_empty_state_never_bare_no_data(self):
        self.assertNotIn('text: "No data"', self.js)

    def test_086_no_dead_button_marker(self):
        self.assertIn('"data-act"', self.js)
        self.assertIn('data-act="modal:cancel"', self.html)
        self.assertIn('data-act="modal:execute"', self.html)

    def test_087_real_navigation(self):
        self.assertIn('"data-act": "nav:"', self.js.replace('"data-act": "nav:" + v.id',
                                                            '"data-act": "nav:"'))

    def test_088_real_action_wiring(self):
        self.assertIn('"data-act": "action:" + spec.action', self.js)
        self.assertIn('"data-act": "action:" + action', self.js)

    def test_089_copy_command_control(self):
        self.assertIn('"data-act": "copy:command"', self.js)
        self.assertIn('"data-act": "copy:id"', self.js)

    def test_090_disabled_explanation_helper(self):
        self.assertIn("function disabledBtn", self.js)
        self.assertIn("aria-describedby", self.js)
        self.assertIn("disabled-why", self.js)

    def test_091_loading_state(self):
        self.assertIn("Loading…", self.js)
        self.assertIn('"data-state": "loading"', self.js)

    def test_092_completed_state(self):
        self.assertIn("completed:", self.js)
        self.assertIn('setFeedback("completed"', self.js)

    def test_093_blocked_state(self):
        self.assertIn("blocked:", self.js)
        self.assertIn("BLOCKED", self.js)

    def test_094_failed_state(self):
        self.assertIn("failed:", self.js)
        self.assertIn('setFeedback("failed"', self.js)

    def test_095_no_change_state(self):
        self.assertIn('"no-change"', self.js)
        self.assertIn("Nothing has changed since your last check.", self.js)

    def test_096_session_expired_state(self):
        self.assertIn('"session-expired"', self.js)
        self.assertIn("Your local session expired.", self.js)

    def test_097_stale_state(self):
        self.assertIn("stale:", self.js)
        self.assertIn("Stale data", self.js)

    def test_098_sidebar_groups(self):
        for g in ("OPERATIONS", "INTELLIGENCE", "SYSTEM"):
            self.assertIn('"%s"' % g, self.js)
        self.assertIn("nav-group-title", self.js)
        self.assertIn("nav-group-title", self.css)

    def test_099_active_navigation(self):
        self.assertIn('aria-current", "page"', self.js)
        self.assertIn('a[aria-current="page"]', self.css)

    def test_100_keyboard_navigation(self):
        self.assertIn("skip-link", self.html)
        self.assertIn("trapTab", self.js)
        self.assertIn(":focus-visible", self.css)

    def test_101_breadcrumb(self):
        self.assertIn('id="breadcrumbs"', self.html)
        self.assertIn('aria-label="Breadcrumb"', self.html)
        self.assertIn("breadcrumbs", self.js)

    def test_102_hash_state_retained(self):
        self.assertIn("window.location.hash", self.js)
        self.assertIn('window.addEventListener("hashchange", route)', self.js)

    def test_103_no_horizontal_overflow(self):
        self.assertIn("overflow-x: auto", self.css)
        self.assertIn("max-width: 100%", self.css)

    def test_104_viewport_1366(self):
        self.assertIn("@media (max-width: 1400px)", self.css)
        self.assertIn("@media (max-width: 1100px)", self.css)

    def test_105_bounded_search(self):
        self.assertIn("slice(0, 200)", self.js)

    def test_106_bounded_filter(self):
        self.assertIn("MAX_QUERY_VALUE", read(CONSOLE_PATH))
        self.assertEqual(UC.MAX_QUERY_VALUE, 256)

    def test_107_bounded_sort(self):
        self.assertIn('"data-act": "table:sort"', self.js)

    def test_108_bounded_pagination(self):
        self.assertLessEqual(UC.DEFAULT_PAGE_SIZE, UC.MAX_PAGE_SIZE)
        self.assertIn("page_size=", self.js)

    def test_109_sticky_headers(self):
        self.assertIn("position: sticky", self.css)

    def test_110_reset_filters(self):
        self.assertIn("reset-filters", self.js)
        self.assertIn("Reset filters", self.js)

    def test_111_copy_id(self):
        self.assertIn("function copyBtn", self.js)
        self.assertIn("Copy identifier", self.js)

    def test_112_view_details(self):
        self.assertIn("View technical details", self.js)
        self.assertIn("View data timestamps", self.js)

    def test_113_record_count(self):
        self.assertIn("record(s) · read-only", self.js)

    def test_114_status_text(self):
        for label in ("READY", "READY — NO ITEMS", "ACTION REQUIRED", "BLOCKED", "STALE",
                      "UNKNOWN", "NEEDS SETUP"):
            self.assertIn('"%s"' % label, self.js, label)

    def test_115_status_icon(self):
        self.assertIn('class: "glyph"', self.js)
        self.assertIn("glyph:", self.js)

    def test_116_status_shape(self):
        self.assertIn('class: "dot"', self.js)
        self.assertIn(".status-tag .dot", self.css)

    def test_117_no_colour_only_state(self):
        self.assertIn('"data-state": key', self.js)
        self.assertIn(".status-tag .glyph", self.css)

    def test_118_visible_focus(self):
        self.assertIn("outline: 3px solid var(--focus)", self.css)

    def test_119_reduced_motion(self):
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.css)
        self.assertIn("@media (prefers-reduced-motion: no-preference)", self.css)

    def test_120_heading_hierarchy(self):
        self.assertIn('el("h1"', self.js)
        self.assertIn('el("h2"', self.js)
        self.assertIn(".panel h1", self.css)
        self.assertIn(".panel h2", self.css)

    def test_120b_type_scale_meets_policy(self):
        self.assertIn("font-size: 1.6rem", self.css)     # h1 ~25.6px (24-28)
        self.assertIn("font-size: 1.2rem", self.css)     # h2 ~19.2px (18-20)
        self.assertIn("font-size: .95rem", self.css)     # body ~15.2px (14-16)
        self.assertIn("font-size: .85rem", self.css)     # table ~13.6px (>=13)

    def test_121_control_labels(self):
        self.assertIn("<label", self.html)
        self.assertIn('"aria-label": "Search "', self.js)
        self.assertIn('"aria-label": "Rows per page"', self.js)

    def test_122_screen_reader_icon_labels(self):
        self.assertIn('aria-label="Hide or show the menu"', self.html)
        self.assertIn('"aria-label": "Copy the command "', self.js)
        self.assertIn('"aria-hidden", "true"', self.js)

    def test_123_no_external_font(self):
        for bad in ("@font-face", "fonts.googleapis", "@import"):
            self.assertNotIn(bad, self.css)

    def test_124_no_cdn(self):
        for asset in (self.js, self.css, self.html):
            for bad in ("cdn.", "unpkg", "jsdelivr", "googleapis", "http://", "https://"):
                self.assertNotIn(bad, asset, bad)

    def test_125_no_analytics(self):
        for bad in ("sendBeacon", "gtag", "analytics", "ga(", "_paq", "mixpanel"):
            self.assertNotIn(bad, self.js)

    def test_126_no_external_browser_request(self):
        self.assertNotIn('fetch("http', self.js)
        self.assertNotIn("fetch('http", self.js)
        self.assertNotIn("XMLHttpRequest", self.js)
        self.assertNotIn("WebSocket", self.js)
        self.assertNotIn("serviceWorker", self.js)

    def test_127_no_unsafe_html(self):
        for bad in ("insertAdjacentHTML", "outerHTML", "document.write", "createContextualFragment"):
            self.assertNotIn(bad, self.js)

    def test_128_no_innerhtml(self):
        self.assertNotIn(".innerHTML", self.js)

    def test_129_no_eval(self):
        for bad in ("eval(", "new Function(", "setTimeout(\"", "setInterval(\""):
            self.assertNotIn(bad, self.js)

    def test_130_no_browser_storage_secret(self):
        for bad in ("localStorage", "sessionStorage", "indexedDB", "document.cookie"):
            self.assertNotIn(bad, self.js)

    def test_130b_csrf_never_written_to_dom(self):
        self.assertNotIn("textContent = CSRF", self.js)
        self.assertNotIn("text: CSRF", self.js)

    def test_130c_no_inline_script_in_html(self):
        self.assertNotIn("<script>", self.html.lower())
        self.assertIn('src="/app.js"', self.html)

    def test_130d_no_inline_style_attribute(self):
        # an inline style= attribute violates the strict style-src 'self' CSP the console sends
        self.assertNotIn("style=", self.html)

    def test_130e_icon_sprite_in_sync(self):
        ids_html = set(re.findall(r'<symbol id="([a-z0-9\-]+)"', self.html))
        ids_svg = set(re.findall(r'<symbol id="([a-z0-9\-]+)"', self.icons))
        self.assertEqual(ids_html, ids_svg)
        self.assertGreaterEqual(len(ids_html), 10)

    def test_130f_every_icon_used_exists(self):
        ids = set(re.findall(r'<symbol id="([a-z0-9\-]+)"', self.html))
        for used in re.findall(r'icon: "([a-z0-9\-]+)"', self.js):
            self.assertIn(used, ids, used)
        for used in re.findall(r'svgIcon\("([a-z0-9\-]+)"\)', self.js):
            self.assertIn(used, ids, used)

    def test_130g_favicon_is_local_and_self_contained(self):
        fav = static("favicon.svg")
        self.assertNotIn("http", fav.replace("http://www.w3.org", ""))
        self.assertIn('rel="icon"', self.html)
        self.assertIn('href="/favicon.svg"', self.html)


# ================================================================ 16) Phase 7.13 / 7.12 regression
class TestPriorPhaseRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = static("index.html")
        cls.js = static("app.js")

    def test_131_modal_markup_intact(self):
        for marker in ('id="modal"', 'aria-modal="true"', 'id="modal-title"', 'id="modal-desc"',
                       'id="modal-confirm-wrap"', 'id="modal-phrase"', 'id="modal-result"',
                       'id="modal-cancel"', 'id="modal-execute"', 'id="modal-backdrop"'):
            self.assertIn(marker, self.html, marker)

    def test_131b_modal_functions_intact(self):
        for fn in ("startAction", "openModal", "openModalBlocked", "executeModal", "closeModal",
                   "wireModal", "refreshExecuteEnabled", "finishExecute", "buildDetails"):
            self.assertIn("function %s" % fn, self.js, fn)

    def test_131c_failed_prepare_still_opens_a_modal(self):
        self.assertIn("openModalBlocked", self.js)
        self.assertIn("This action cannot be prepared right now.", self.js)

    def test_132_exact_phrase_gating_intact(self):
        self.assertIn('id("modal-phrase").value !== modalState.phrase', self.js)
        self.assertIn("confirmation_phrase", self.js)

    def test_132b_no_phrase_normalisation(self):
        for bad in (".trim()", ".toLowerCase()"):
            snippet = self.js[self.js.index("function refreshExecuteEnabled"):
                              self.js.index("function closeModal")]
            self.assertNotIn(bad, snippet)

    def test_133_failure_modal_intact(self):
        self.assertIn('resEl.className = "bad"', self.js)
        self.assertIn("Not completed", self.js)

    def test_134_export_download_intact(self):
        self.assertIn("/exports/overview?format=", self.js)
        self.assertIn("appendExportResult", self.js)

    def test_135_focus_trap_intact(self):
        self.assertIn("function trapTab", self.js)
        self.assertIn("modalFocusables", self.js)
        self.assertIn("modalState.lastFocus", self.js)

    def test_135b_single_use_token_intact(self):
        self.assertIn("modalState.token = null", self.js)
        self.assertIn("modalState.done = true", self.js)

    def test_135c_backdrop_click_still_does_not_dismiss(self):
        self.assertIn("backdrop.addEventListener(\"click\"", self.js)
        self.assertIn("e.preventDefault()", self.js)

    def test_136_phase_712_double_gate_untouched(self):
        from production import phase7_owner_notification_delivery as N
        self.assertTrue(hasattr(N, "DS_UNKNOWN"))
        self.assertNotEqual(N.DS_UNKNOWN, N.DS_FAILED)

    def test_136b_accepted_authorities_unmodified(self):
        out = subprocess.run(
            ["git", "diff", "--name-only", "3f758debc31bcf0b4e50d9693798e99910c64110", "--",
             "production/phase7_ads_analysis.py", "production/phase7_report_ingestion.py",
             "production/phase7_owner_dashboard.py", "production/phase7_owner_decision_package.py",
             "production/phase7_manual_action_tracker.py", "production/phase7_outcome_followup.py",
             "production/phase7_owner_operations_dashboard.py",
             "production/phase7_connected_backup_recovery.py",
             "production/phase7_connected_public_research.py",
             "production/phase7_connected_research_watchlists.py",
             "production/phase7_owner_notification_delivery.py", "core/"],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "", "accepted 7.3-7.12 authority modified")

    def test_136c_action_allowlist_still_fifteen(self):
        self.assertEqual(len(UC.ACTIONS), 15)

    def test_136d_prohibited_actions_still_absent(self):
        for name in UC.PROHIBITED_ACTIONS:
            self.assertNotIn(name, UC.ACTIONS)


# ================================================================ 17) permanent boundary
class TestBoundary(unittest.TestCase):
    def sources(self):
        return {"launcher": read(LAUNCHER_PATH), "next_action": read(NEXT_PATH),
                "app.js": static("app.js"), "index.html": static("index.html"),
                "styles.css": static("styles.css")}

    def test_137_seller_central_counters_zero(self):
        for k, v in L.SELLER_CENTRAL_COUNTERS.items():
            self.assertEqual(v, 0, k)
        for k, v in UC.SELLER_CENTRAL_COUNTERS.items():
            self.assertEqual(v, 0, k)

    def test_137b_counters_never_incremented(self):
        for name, src in self.sources().items():
            self.assertNotIn("SELLER_CENTRAL_COUNTERS[", src.replace(
                'dict(SELLER_CENTRAL_COUNTERS)', ''), name)
            self.assertNotIn("+= 1", src.split("SELLER_CENTRAL_COUNTERS")[-1][:200], name)

    def test_138_no_sp_api(self):
        for name, src in self.sources().items():
            low = src.lower()
            for bad in ("sellingpartnerapi", "sp-api", "sellingpartner"):
                self.assertNotIn(bad, low, "%s in %s" % (bad, name))

    def test_139_no_ads_api(self):
        # NOTE: `advertising_api_calls` is an accepted constant-zero BOUNDARY COUNTER name shared with
        # the accepted Phase 7.13 console. What must be absent is an advertising API endpoint or client.
        for name, src in self.sources().items():
            low = src.lower()
            for bad in ("advertising-api.", "amazon-adsystem", "ads-api.", "/v2/campaigns"):
                self.assertNotIn(bad, low, "%s in %s" % (bad, name))

    def test_140_no_seller_oauth(self):
        for name, src in self.sources().items():
            low = src.lower()
            for bad in ("lwa_", "login_with_amazon", "refresh_token", "client_secret",
                        "oauth", "ap/signin", "sellercentral"):
                self.assertNotIn(bad, low, "%s in %s" % (bad, name))

    def test_141_no_seller_browser_automation(self):
        for name, src in self.sources().items():
            low = src.lower()
            for bad in ("selenium", "playwright", "puppeteer", "webdriver", "chromedriver"):
                self.assertNotIn(bad, low, "%s in %s" % (bad, name))

    def test_141b_launcher_scripts_free_of_amazon(self):
        for name in SCRIPTS:
            low = script(name).lower()
            for bad in ("sellercentral", "sp-api", "advertising-api", "amazon.com", "signin"):
                self.assertNotIn(bad, low, "%s in %s" % (bad, name))

    def test_141c_browser_only_ever_opens_loopback(self):
        src = read(LAUNCHER_PATH)
        self.assertIn("is_allowed_url(url, host, port)", src)
        self.assertIn("URL_NOT_ALLOWED", src)

    def test_141d_console_url_never_built_from_input(self):
        self.assertIn("validate_host(host)", read(LAUNCHER_PATH))
        with self.assertRaises(L.LauncherError):
            L.console_url("evil.example.com", 8780)

    def test_141e_guidance_never_recommends_an_amazon_mutation(self):
        for build in CASES.values():
            text = NA.owner_text(NA.build_next_action(build())).lower()
            for bad in ("change your listing", "update your campaign", "upload", "log in to amazon"):
                self.assertNotIn(bad, text, bad)


# ================================================================ 18) pilot kit
class TestPilotKit(unittest.TestCase):
    def test_142_pilot_guide_present(self):
        self.assertIn("14-day", doc("PHASE7_14-OWNER-PILOT-GUIDE.md"))

    def test_143_pilot_checklist_present(self):
        self.assertIn("Day 0", doc("PHASE7_14-OWNER-PILOT-CHECKLIST.md"))

    def test_144_issue_template_present(self):
        d = doc("PHASE7_14-PILOT-ISSUE-TEMPLATE.md")
        self.assertIn("ISSUE ID", d)
        self.assertIn("SEVERITY", d)

    def test_145_daily_log_template_present(self):
        self.assertIn("PILOT DAY", doc("PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md"))

    def test_146_exit_criteria_present(self):
        self.assertIn("Exit Criteria", doc("PHASE7_14-PILOT-EXIT-CRITERIA.md"))

    def test_146b_usability_policy_present(self):
        self.assertIn("Owner Usability Policy", doc("PHASE7_14-OWNER-USABILITY-POLICY.md"))

    def test_146c_all_pilot_docs_exist(self):
        for name in PILOT_DOCS:
            self.assertTrue(os.path.isfile(os.path.join(DOCS, name)), name)

    def test_147_day0_checklist(self):
        g = doc("PHASE7_14-OWNER-PILOT-GUIDE.md")
        c = doc("PHASE7_14-OWNER-PILOT-CHECKLIST.md")
        for item in ("Launcher verification", "Browser verification", "Data backup",
                     "Report availability", "Owner orientation", "Issue-recording method"):
            self.assertIn(item, g, item)
            self.assertIn(item, c, item)

    def test_148_daily_workflow(self):
        g = doc("PHASE7_14-OWNER-PILOT-GUIDE.md")
        for step in ("Start the toolkit", "Review the next action", "Inspect the current analysis",
                     "Record an owner decision", "record the manual action", "Review alerts",
                     "Review follow-ups", "Stop the toolkit", "Record time spent"):
            self.assertIn(step, g, step)

    def test_149_export_exercise(self):
        self.assertIn("Create an export", doc("PHASE7_14-OWNER-PILOT-GUIDE.md"))
        self.assertIn("Export exercise", doc("PHASE7_14-OWNER-PILOT-CHECKLIST.md"))

    def test_150_backup_exercise(self):
        self.assertIn("Create a backup snapshot", doc("PHASE7_14-OWNER-PILOT-GUIDE.md"))
        self.assertIn("Verify a backup", doc("PHASE7_14-OWNER-PILOT-GUIDE.md"))

    def test_151_research_exercise(self):
        self.assertIn("Create a research run", doc("PHASE7_14-OWNER-PILOT-GUIDE.md"))

    def test_152_watchlist_exercise(self):
        self.assertIn("Create a watchlist", doc("PHASE7_14-OWNER-PILOT-GUIDE.md"))

    def test_153_alert_exercise(self):
        self.assertIn("Review an alert", doc("PHASE7_14-OWNER-PILOT-GUIDE.md"))

    def test_154_notification_review(self):
        self.assertIn("Review the notification outbox", doc("PHASE7_14-OWNER-PILOT-GUIDE.md"))

    def test_155_recovery_drill(self):
        g = doc("PHASE7_14-OWNER-PILOT-GUIDE.md")
        self.assertIn("recovery drill", g.lower())
        self.assertIn("never on your live data", g)

    def test_155b_recovery_drill_is_isolated(self):
        self.assertIn("on a copy", doc("PHASE7_14-OWNER-PILOT-GUIDE.md"))

    def test_156_seller_counter_verification(self):
        self.assertIn("Seller Central counters", doc("PHASE7_14-OWNER-PILOT-GUIDE.md"))
        self.assertIn("Seller Central counter verification",
                      doc("PHASE7_14-OWNER-PILOT-CHECKLIST.md"))

    def test_157_startup_metric(self):
        self.assertIn("Startup success", doc("PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md"))

    def test_158_startup_time_metric(self):
        self.assertIn("Startup time", doc("PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md"))

    def test_159_overview_understanding_metric(self):
        self.assertIn("Time to understand Overview", doc("PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md"))

    def test_160_next_action_time_metric(self):
        self.assertIn("Time to identify the next action",
                      doc("PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md"))

    def test_161_powershell_use_metric(self):
        d = doc("PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md")
        self.assertIn("Number of PowerShell uses", d)
        self.assertIn("target: 0", d)

    def test_162_dead_end_metric(self):
        self.assertIn("Number of dead ends", doc("PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md"))

    def test_162b_unclear_status_metric(self):
        self.assertIn("Number of unclear statuses", doc("PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md"))

    def test_162c_ui_defect_metric(self):
        self.assertIn("Number of UI defects", doc("PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md"))

    def test_162d_blocked_workflow_metric(self):
        self.assertIn("Number of blocked workflows", doc("PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md"))

    def test_162e_review_time_metric(self):
        self.assertIn("Time to complete the owner review",
                      doc("PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md"))

    def test_163_confidence_metric(self):
        d = doc("PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md")
        self.assertIn("Owner confidence rating", d)
        self.assertIn("[ ]1 [ ]2 [ ]3 [ ]4 [ ]5", d)

    def test_164_effort_metric(self):
        self.assertIn("Owner effort rating", doc("PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md"))

    def test_165_decision_metric(self):
        self.assertIn("Completed decisions", doc("PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md"))

    def test_166_manual_action_metric(self):
        self.assertIn("Completed manual actions", doc("PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md"))

    def test_167_followup_metric(self):
        self.assertIn("Completed follow-ups", doc("PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md"))

    def test_167b_export_and_backup_metrics(self):
        d = doc("PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md")
        self.assertIn("Exports created", d)
        self.assertIn("Backups verified", d)

    def test_167c_metrics_disclaim_business_causation(self):
        d = doc("PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md")
        self.assertIn("never describe business results", d)
        self.assertIn("causation", d)

    def test_168_pilot_no_infrastructure_rule(self):
        for name in ("PHASE7_14-OWNER-PILOT-GUIDE.md", "PHASE7_14-OWNER-PILOT-CHECKLIST.md",
                     "PHASE7_14-PILOT-ISSUE-TEMPLATE.md", "PHASE7_14-PILOT-EXIT-CRITERIA.md"):
            self.assertIn("DO NOT ADD NEW INFRASTRUCTURE", doc(name), name)

    def test_169_pilot_fix_only_rule(self):
        for name in ("PHASE7_14-OWNER-PILOT-GUIDE.md", "PHASE7_14-PILOT-ISSUE-TEMPLATE.md",
                     "PHASE7_14-PILOT-EXIT-CRITERIA.md"):
            self.assertIn("FIX DEFECTS ONLY", doc(name), name)

    def test_169b_deferred_list_exists(self):
        self.assertIn("Deferred (post-pilot)", doc("PHASE7_14-PILOT-ISSUE-TEMPLATE.md"))

    def test_170_full_workflow_exit_criterion(self):
        self.assertIn("Owner completes at least one full workflow",
                      doc("PHASE7_14-PILOT-EXIT-CRITERIA.md"))

    def test_171_backup_exit_criterion(self):
        self.assertIn("Backup and recovery drill completed",
                      doc("PHASE7_14-PILOT-EXIT-CRITERIA.md"))

    def test_172_no_critical_usability_defect_criterion(self):
        self.assertIn("No unresolved critical usability defect",
                      doc("PHASE7_14-PILOT-EXIT-CRITERIA.md"))

    def test_173_confidence_threshold(self):
        d = doc("PHASE7_14-PILOT-EXIT-CRITERIA.md")
        self.assertIn("Owner confidence meets the threshold", d)
        self.assertIn(">= 4.0 / 5".replace(">=", "≥"), d)

    def test_173b_all_fourteen_exit_criteria(self):
        d = doc("PHASE7_14-PILOT-EXIT-CRITERIA.md")
        for phrase in ("Launcher works reliably", "No normal daily PowerShell requirement",
                       "No dead buttons", "No blank or unusable modal", "No fake route",
                       "Next-action guidance is accurate",
                       "Owner completes at least one full workflow",
                       "Owner records at least one decision",
                       "Owner records at least one manual action",
                       "A later report supports one observational follow-up",
                       "Backup and recovery drill completed",
                       "Seller Central counters remain zero",
                       "No unresolved critical usability defect",
                       "Owner confidence meets the threshold"):
            self.assertIn(phrase, d, phrase)

    def test_173c_hard_stops_declared(self):
        self.assertIn("Hard stops", doc("PHASE7_14-PILOT-EXIT-CRITERIA.md"))

    def test_173d_pilot_runtime_records_not_committed(self):
        self.assertIn("Do not commit", doc("PHASE7_14-OWNER-PILOT-CHECKLIST.md"))
        out = subprocess.run(["git", "ls-files", "runs/T2/phase7/7.14/"], cwd=ROOT,
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, "")

    def test_173e_policy_declares_priority_order(self):
        p = doc("PHASE7_14-OWNER-USABILITY-POLICY.md")
        for rule in NA.describe_rules():
            self.assertIn(rule["rule_id"], p, rule["rule_id"])

    def test_173f_policy_declares_button_contract(self):
        p = doc("PHASE7_14-OWNER-USABILITY-POLICY.md")
        for phrase in ("dead buttons", "blank modals", "silent failures", "infinite spinners",
                       "fake routes"):
            self.assertIn(phrase, p, phrase)

    def test_173g_policy_declares_boundary_first(self):
        p = doc("PHASE7_14-OWNER-USABILITY-POLICY.md")
        self.assertLess(p.index("Permanent Amazon boundary"), p.index("Next-action guidance"))


# ================================================================ 19) determinism + safety of output
class TestDeterminism(unittest.TestCase):
    def test_174_launcher_result_deterministic_where_applicable(self):
        tmp = tempfile.mkdtemp(prefix="p714-det-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        results = []
        for _ in range(2):
            ws = L.Workspace(tmp)
            ws.ensure()
            lc = L.Launcher(root=ROOT, workspace=ws, health=lambda h, p: dict(HEALTHY),
                            port_probe=lambda h, p: True, browser=lambda u: True)
            r = lc.start()
            r.pop("generated_at")
            r.pop("preflight", None)
            results.append(UC.canonical_json(r))
        self.assertEqual(results[0], results[1])

    def test_175_guidance_json_deterministic(self):
        for build in CASES.values():
            m = build()
            a = UC.canonical_json(NA.build_next_action(m, generated_at="Z"))
            b = UC.canonical_json(NA.build_next_action(m, generated_at="Z"))
            self.assertEqual(a, b)

    def test_176_no_secrets_in_guidance(self):
        for build in CASES.values():
            blob = UC.canonical_json(NA.build_next_action(build()))
            for bad in ("token", "secret", "password", "cookie", "csrf", "authorization"):
                self.assertNotIn(bad, blob.lower(), bad)

    def test_177_no_absolute_path_in_guidance(self):
        for build in CASES.values():
            blob = UC.canonical_json(NA.build_next_action(build()))
            self.assertNotIn(":\\", blob)
            self.assertNotIn(ROOT.replace("\\", "\\\\"), blob)

    def test_177b_no_absolute_path_in_launcher_result(self):
        tmp = tempfile.mkdtemp(prefix="p714-abs-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ws = L.Workspace(tmp)
        ws.ensure()
        res = L.Launcher(root=ROOT, workspace=ws, health=lambda h, p: dict(HEALTHY),
                         port_probe=lambda h, p: True, browser=lambda u: True).start()
        blob = json.dumps(res)
        self.assertNotIn(tmp.replace("\\", "\\\\"), blob)

    def test_178_vietnamese_unicode_preserved(self):
        m = model(sections={"watchlists": {"status": "READY", "counts": {}, "alerts": [
            {"alert_id": "cảnh-báo-1", "status": "OPEN", "severity": "CRITICAL"}]}})
        na = NA.build_next_action(m)
        self.assertIn("cảnh-báo-1", na["source_ids"])
        self.assertIn("cảnh-báo-1", UC.canonical_json(na))

    def test_178b_unicode_survives_identity(self):
        m = model(sections={"watchlists": {"status": "READY", "counts": {}, "alerts": [
            {"alert_id": "Huế-1", "status": "OPEN", "severity": "CRITICAL"}]}})
        self.assertEqual(NA.build_next_action(m)["next_action_identity"],
                         NA.build_next_action(m)["next_action_identity"])

    def test_179_legitimate_negative_values_preserved(self):
        m = model(overview={"pending_decisions": 0, "notification_unknown": 0})
        m["overview"]["some_negative_upstream_value"] = -12
        na = NA.build_next_action(m)
        # an unknown extra field changes nothing: the healthy model still resolves to "no urgent action"
        self.assertEqual(na["priority"], 14)

    def test_179b_counts_read_verbatim_never_clamped(self):
        m = model(overview={"pending_decisions": 1234})
        self.assertIn("1234", NA.build_next_action(m)["owner_message"])

    def test_179c_negative_upstream_count_is_not_invented_away(self):
        m = model(overview={"pending_decisions": -3})
        # a negative count is not "pending work"; the engine reads it verbatim and does not fire
        self.assertNotEqual(NA.build_next_action(m)["priority"], 6)

    def test_180_formula_safe_tsv_rule_unchanged(self):
        # 7.14 adds no exporter; the accepted formula-injection rule is still the one in use
        self.assertTrue(callable(UC._TSV_CELL))
        for danger in ("=cmd", "+1", "@x", "-abc"):
            self.assertTrue(UC._TSV_CELL(danger).startswith("'"), danger)

    def test_180b_legitimate_negative_number_not_quoted(self):
        # the accepted rule deliberately preserves a real negative number
        self.assertEqual(UC._TSV_CELL("-1"), "-1")
        self.assertEqual(UC._TSV_CELL("-12.50"), "-12.50")

    def test_181_source_immutability(self):
        """The guidance never writes to, or derives a new value from, its inputs."""
        m = model()
        snapshot = UC.canonical_json(m)
        for _ in range(3):
            NA.build_next_action(m)
        self.assertEqual(UC.canonical_json(m), snapshot)

    def test_181b_facts_are_read_only_copies(self):
        m = model()
        f = NA.Facts(m)
        f.stale_phases.append("tampered")
        self.assertEqual(m["overview"]["stale_data_phases"], [])

    def test_182_runs_ignored(self):
        gi = read(os.path.join(ROOT, ".gitignore"))
        self.assertIn("runs/", gi)
        out = subprocess.run(["git", "check-ignore", "-q", "runs/T2/phase7/7.14/launcher"],
                             cwd=ROOT)
        self.assertEqual(out.returncode, 0)

    def test_183_static_assets_hashable(self):
        import hashlib
        for name in ("index.html", "app.js", "styles.css", "icons.svg", "favicon.svg"):
            h = hashlib.sha256(open(os.path.join(STATIC_DIR, name), "rb").read()).hexdigest()
            self.assertEqual(len(h), 64)

    def test_184_launcher_files_hashable(self):
        import hashlib
        for name in SCRIPTS + ("production/phase7_owner_launcher.py",):
            p = os.path.join(ROOT, name.replace("/", os.sep))
            self.assertTrue(os.path.isfile(p), name)
            self.assertEqual(len(hashlib.sha256(open(p, "rb").read()).hexdigest()), 64)

    def test_184b_lf_pinned_for_hashed_artifacts(self):
        ga = read(os.path.join(ROOT, ".gitattributes"))
        for name in ("production/phase7_owner_launcher.py",
                     "production/phase7_owner_next_action.py",
                     "production/phase7_unified_owner_console_static/favicon.svg",
                     "Start-AMZ-Toolkit.ps1", "docs/PHASE7_14-OWNER-USABILITY-POLICY.md"):
            self.assertIn(name, ga, name)

    def test_185_compileall(self):
        out = subprocess.run([sys.executable, "-m", "compileall", "-q",
                              os.path.join(ROOT, "production", "phase7_owner_launcher.py"),
                              os.path.join(ROOT, "production", "phase7_owner_next_action.py"),
                              os.path.join(ROOT, "production", "phase7_unified_owner_console.py")],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


# ================================================================ 20) fresh-tree + evidence
class TestFreshTreeAndEvidence(unittest.TestCase):
    def test_186_focused_suite_present(self):
        self.assertTrue(os.path.isfile(os.path.join(
            TESTS, "test_phase7_14_owner_usability_pilot_readiness.py")))

    def test_187_phase_713_suite_present(self):
        self.assertTrue(os.path.isfile(os.path.join(
            TESTS, "test_phase7_13_unified_owner_console.py")))

    def test_188_phase_712_suite_present(self):
        self.assertTrue(os.path.isfile(os.path.join(
            TESTS, "test_phase7_12_owner_notification_delivery.py")))

    def test_189_prior_suites_present(self):
        for name in ("test_phase7_10_connected_public_research.py",
                     "test_phase7_11_connected_research_watchlists.py",
                     "test_phase7_1m_foundation.py", "test_phase7_1e_planning.py"):
            self.assertTrue(os.path.isfile(os.path.join(TESTS, name)), name)

    def test_190_full_suite_discoverable(self):
        out = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests",
                              "-p", "test_*.py", "--locals", "-v", "-k", "nothing_matches_this"],
                             cwd=ROOT, capture_output=True, text=True)
        self.assertIn("Ran 0 tests", out.stderr + out.stdout)

    def test_191_connectivity_scanner_clean(self):
        for name, src in (("launcher", read(LAUNCHER_PATH)), ("next_action", read(NEXT_PATH))):
            for bad in ("requests.get", "requests.post", "http.client.HTTPSConnection",
                        "ssl.wrap_socket"):
                self.assertNotIn(bad, src, "%s in %s" % (bad, name))

    def test_192_network_policy_scanner(self):
        src = read(LAUNCHER_PATH)
        self.assertIn("NON_LOOPBACK_PROBE_REFUSED", src)
        self.assertIn("_NoRedirect", src)

    def test_192b_health_probe_follows_no_redirect(self):
        self.assertIn("def redirect_request", read(LAUNCHER_PATH))

    def test_193_prohibited_integration_scan(self):
        # matched on token boundaries, so an innocent word can never be mistaken for an integration
        # ("always" contains "lwa"; "advertising_api_calls" is a constant-zero boundary counter).
        banned = ("sellercentral", "sellingpartnerapi", "amazon-adsystem", "mws.amazonservices",
                  "lwa", "seller_refresh_token", "selenium", "playwright", "puppeteer",
                  "webdriver", "chromedriver", "taskkill", "pkill", "killall")
        rx = [(b, re.compile(r"(?<![a-z0-9_.\-])" + re.escape(b) + r"(?![a-z0-9_])")) for b in banned]
        literal = ("advertising-api.", "shell=true", "sp-api.")
        targets = [LAUNCHER_PATH, NEXT_PATH,
                   os.path.join(STATIC_DIR, "app.js"), os.path.join(STATIC_DIR, "index.html"),
                   os.path.join(STATIC_DIR, "styles.css")] + \
                  [os.path.join(ROOT, s) for s in SCRIPTS] + \
                  [os.path.join(DOCS, d) for d in PILOT_DOCS]
        for path in targets:
            low = read(path).lower()
            name = os.path.basename(path)
            for bad, pat in rx:
                self.assertIsNone(pat.search(low), "%s in %s" % (bad, name))
            for bad in literal:
                self.assertNotIn(bad, low, "%s in %s" % (bad, name))

    def test_194_baseline_commit_reachable(self):
        out = subprocess.run(["git", "cat-file", "-t",
                              "3f758debc31bcf0b4e50d9693798e99910c64110"],
                             cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "commit")

    def test_195_checkpoint_tag_present(self):
        out = subprocess.run(["git", "tag", "--list",
                              "phase7-14-owner-usability-pilot-readiness-checkpoint-3f758de"],
                             cwd=ROOT, capture_output=True, text=True)
        self.assertIn("checkpoint", out.stdout)

    def test_196_launcher_works_without_runs_data(self):
        """A fresh worktree has no runs/ tree; the launcher must still preflight and validate."""
        tmp = tempfile.mkdtemp(prefix="p714-fresh-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        res = L.validate_only(root=ROOT)
        self.assertTrue(res["ok"])
        ws = L.Workspace(tmp)
        self.assertTrue(ws.ensure())

    def test_196b_launcher_creates_its_own_runtime_dir(self):
        tmp = tempfile.mkdtemp(prefix="p714-mk-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ws = L.Workspace(tmp)
        ws.ensure()
        self.assertTrue(os.path.isdir(ws.dir))

    def test_197_static_assets_work_without_runs(self):
        for name in ("index.html", "app.js", "styles.css", "icons.svg", "favicon.svg"):
            self.assertGreater(len(static(name)), 100, name)

    def test_198_no_runs_dependency_in_launcher_module(self):
        src = read(LAUNCHER_PATH)
        # runs/ is only ever the DEFAULT workspace + the launcher's own runtime dir
        self.assertEqual(src.count('"runs"'), 1)
        self.assertEqual(src.count('DEFAULT_WORKSPACE_ROOT = "runs/T2/phase7"'), 1)

    def test_199_browser_qa_harness_present(self):
        self.assertTrue(os.path.isfile(os.path.join(TESTS, "phase7_14_browser_qa.js")))

    def test_199b_browser_qa_is_loopback_only(self):
        src = read(os.path.join(TESTS, "phase7_14_browser_qa.js"))
        self.assertIn("offOrigin", src)
        self.assertIn("all_requests_same_origin_loopback", src)
        self.assertIn("127.0.0.1", src)

    def test_199c_browser_qa_records_more_than_screenshots(self):
        src = read(os.path.join(TESTS, "phase7_14_browser_qa.js"))
        for signal in ("consoleAPICalled", "exceptionThrown", "requestWillBeSent", "Runtime.evaluate"):
            self.assertIn(signal, src, signal)
        self.assertNotIn("captureScreenshot", src)

    def test_199d_dom_harness_present(self):
        self.assertTrue(os.path.isfile(os.path.join(TESTS, "phase7_14_console_dom_harness.js")))

    def test_199e_no_acceptance_tag_yet(self):
        out = subprocess.run(["git", "tag", "--list"], cwd=ROOT, capture_output=True, text=True)
        for line in out.stdout.splitlines():
            t = line.strip()
            if "7-14" in t or "7.14" in t:
                self.assertIn("checkpoint", t, "unexpected 7.14 tag: " + t)

    def test_199f_no_phase_8_work(self):
        for entry in os.listdir(os.path.join(ROOT, "production")):
            self.assertFalse(entry.startswith("phase8"), entry)
        for entry in os.listdir(TESTS):
            self.assertFalse(entry.startswith("test_phase8"), entry)

    def test_199g_no_ppc_module_added(self):
        for entry in os.listdir(os.path.join(ROOT, "production")):
            self.assertNotIn("ppc", entry.lower())


# ================================================================ hotfix: hidden must really hide
#
# Day-0 pilot defect (BLOCKING OWNER NAVIGATION): the Overview CTA "Go to Analysis & Decisions" did
# not navigate and an empty "Confirm action" dialog sat over the page with a live Confirm button.
#
# Root cause: app.js hides the confirmation dialog with the `hidden` attribute ONLY, and
# `#modal-backdrop { display: flex }` in styles.css is an author-origin declaration that outranks the
# user-agent `[hidden] { display: none }` rule. The hidden backdrop therefore stayed painted at
# `position: fixed; inset: 0; z-index: 80`, covered the whole viewport, and its own click handler
# preventDefault()ed the CTA anchor. `.btn { display: inline-flex }` broke `exec.hidden` the same way.
#
# Neither DOM harness could catch this: both run in a dependency-free DOM with no CSS engine, and the
# real-browser probes asserted the `hidden` property (correctly true) but never the computed style.
# These tests close that gap offline by computing the real cascade for the elements app.js hides.
_DECL_RE = re.compile(r"([a-zA-Z-]+)\s*:\s*([^;{}]+?)\s*(!important)?\s*(?:;|$)")


def _css_rules(css):
    """Parse the stylesheet into (selector, declarations) pairs, skipping @media/@print blocks.

    Bounded and deliberately simple: this console's stylesheet is flat, hand-written and has no
    nesting beyond at-rule blocks. At-rule bodies are skipped because none of them may be relied on
    to hide anything -- a print or width-conditional rule is not a hide mechanism.
    """
    rules = []
    i, n = 0, len(css)
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    n = len(css)
    while i < n:
        brace = css.find("{", i)
        if brace < 0:
            break
        selector = css[i:brace].strip()
        depth, j = 1, brace + 1
        while j < n and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        body = css[brace + 1:j - 1]
        if selector.startswith("@"):
            pass                                    # at-rule block: never a hide mechanism
        else:
            decls = [(p.group(1).lower(), p.group(2).strip(), bool(p.group(3)))
                     for p in _DECL_RE.finditer(body)]
            for sel in selector.split(","):
                sel = sel.strip()
                if sel:
                    rules.append((sel, decls))
        i = j
    return rules


def _matches(sel, el):
    """True when a simple selector matches the element dict {tag, id, classes, attrs}.

    Supports the compound forms this stylesheet actually uses: tag, #id, .class, [attr] and
    descendant combinations. A selector naming anything the element does not have simply misses.
    """
    parts = sel.split()
    last = parts[-1]                                # only the subject of the selector must match
    for tok in re.findall(r"[#.\[]?[^#.\[\]:]+\]?", last):
        if tok.startswith("#"):
            if tok[1:] != el["id"]:
                return False
        elif tok.startswith("."):
            if tok[1:] not in el["classes"]:
                return False
        elif tok.startswith("["):
            if tok.strip("[]").split("=")[0] not in el["attrs"]:
                return False
        elif tok in ("*",):
            continue
        else:
            if tok.lower() != el["tag"]:
                return False
    return True


def _specificity(sel):
    last = sel.split()[-1]
    return (last.count("#"), last.count(".") + last.count("["), len(re.findall(r"^[a-z]+", last)))


def _winning_display(rules, el, ua_hidden=True):
    """Compute the winning `display` for an element, honouring CSS origin precedence.

    Order (lowest to highest): user-agent normal, author normal, author !important. That is the whole
    point: an author `display` beats the user-agent `[hidden] { display: none }` however specific the
    UA rule is, which is exactly how the blank modal reached a pilot.
    """
    winner, best = None, None
    if ua_hidden and "hidden" in el["attrs"]:
        winner, best = "none", (0, (0, 1, 0), -1)   # user-agent origin == 0
    for idx, (sel, decls) in enumerate(rules):
        if not _matches(sel, el):
            continue
        for prop, value, important in decls:
            if prop != "display":
                continue
            key = (2 if important else 1, _specificity(sel), idx)
            if best is None or key > best:
                winner, best = value, key
    return winner


# Every element app.js hides by setting `.hidden`, with the classes it really carries.
_HIDEABLE = (
    ("modal backdrop", {"tag": "div", "id": "modal-backdrop", "classes": set(), "attrs": {"hidden"}}),
    ("confirm wrap", {"tag": "div", "id": "modal-confirm-wrap", "classes": set(), "attrs": {"hidden"}}),
    ("Confirm button", {"tag": "button", "id": "modal-execute",
                        "classes": {"btn", "primary"}, "attrs": {"hidden"}}),
    ("Cancel button", {"tag": "button", "id": "modal-cancel",
                       "classes": {"btn"}, "attrs": {"hidden"}}),
    ("toast", {"tag": "div", "id": "toast", "classes": {"toast"}, "attrs": {"hidden"}}),
    ("nav badge", {"tag": "span", "id": "badge-alerts", "classes": {"badge"}, "attrs": {"hidden"}}),
)


class TestHiddenActuallyHides(unittest.TestCase):
    """The `hidden` attribute is the console's only hide mechanism, so CSS must never outrank it."""

    def setUp(self):
        self.css = static("styles.css")
        self.rules = _css_rules(self.css)

    def test_300_stylesheet_parses(self):
        self.assertGreater(len(self.rules), 60, len(self.rules))
        self.assertTrue(any(sel == "#modal-backdrop" for sel, _ in self.rules))

    def test_301_hidden_guard_declared(self):
        winner = _winning_display(self.rules,
                                  {"tag": "span", "id": "", "classes": set(), "attrs": {"hidden"}})
        self.assertEqual(winner, "none", "a bare [hidden] element must compute to display:none")

    def test_302_every_hideable_element_computes_to_display_none(self):
        for name, el in _HIDEABLE:
            with self.subTest(element=name):
                self.assertEqual(_winning_display(self.rules, el), "none",
                                 f"{name} stays displayed while hidden -> it can still be seen "
                                 f"and can still swallow clicks")

    def test_303_guard_is_load_bearing_not_vacuous(self):
        """Without the guard the modal backdrop really would stay displayed.

        This proves the test above is not passing by accident: strip the `[hidden]` guard from the
        parsed rules and the backdrop must go back to `flex`. If this ever fails, the guard has
        stopped being the thing that protects the console and test_302 has become decorative.
        """
        stripped = [(sel, decls) for sel, decls in self.rules if sel != "[hidden]"]
        backdrop = dict(_HIDEABLE[0][1])
        self.assertEqual(_winning_display(stripped, backdrop), "flex",
                         "expected the historical defect to reappear without the guard")
        self.assertEqual(_winning_display(stripped, dict(_HIDEABLE[2][1])), "inline-flex",
                         "expected the Confirm button to reappear without the guard")

    def test_304_guard_cannot_be_outranked(self):
        """The guard is the only !important display in the stylesheet, so nothing can beat it."""
        important = [(sel, value) for sel, decls in self.rules
                     for prop, value, imp in decls if prop == "display" and imp]
        self.assertEqual(important, [("[hidden]", "none")], important)

    def test_305_guard_is_unconditional(self):
        """The guard must not live inside @media/@print, where it would apply only sometimes."""
        body = self.css
        idx = body.find("[hidden]")
        self.assertGreater(idx, 0)
        before = body[:idx]
        self.assertEqual(before.count("{"), before.count("}"),
                         "the [hidden] guard is nested inside an at-rule block")

    def test_306_hidden_is_the_declared_hide_mechanism(self):
        """app.js must keep hiding via `hidden` only: a class/inline-style hide would bypass the guard."""
        app = static("app.js")
        self.assertIn(".hidden = false", app)
        self.assertIn(".hidden = true", app)
        self.assertNotIn(".style.display", app)
        self.assertNotIn("classList", app)

    def test_307_backdrop_still_lays_out_when_shown(self):
        """The fix must not have removed the layout the dialog needs when it IS open."""
        shown = {"tag": "div", "id": "modal-backdrop", "classes": set(), "attrs": set()}
        self.assertEqual(_winning_display(self.rules, shown), "flex")

    def test_308_no_hidden_element_is_left_clickable(self):
        """A hidden element must be display:none, which removes it from hit-testing entirely."""
        for name, el in _HIDEABLE:
            with self.subTest(element=name):
                self.assertNotIn(_winning_display(self.rules, el),
                                 ("flex", "block", "inline-flex", "inline-block", "grid", "inline"),
                                 name)


class TestNextActionNavigationContract(unittest.TestCase):
    """The Overview next-action CTA is navigation, and the modal fails closed. Static guarantees."""

    def setUp(self):
        self.app = static("app.js")
        self.html = static("index.html")

    def test_310_control_kinds_declared(self):
        for kind in ('"nav": "navigation"', '"action": "action"', '"copy": "copy"',
                     '"disabled": "disabled"'):
            self.assertIn(kind, self.app, kind)
        self.assertIn("function controlKind(", self.app)
        self.assertIn("function isNavigationAct(", self.app)

    def test_311_cta_is_an_anchor_with_a_hash_route(self):
        """The CTA is built as an <a href="#route"> carrying a nav classification, never a button."""
        self.assertIn('"data-act": "nav:" + route', self.app)
        self.assertIn('href: "#" + route', self.app)

    def test_312_cta_route_resolves_to_a_real_view(self):
        """Every next-action destination page, and every section route, names a declared view."""
        views = set(re.findall(r'\{ id: "([a-z-]+)", label:', self.app))
        self.assertIn("analysis", views)
        for page in NA.PAGE_LABELS:
            self.assertIn(page, views, page)
        for section, route in re.findall(r'"([a-z-]+)": "([a-z-]+)"',
                                        self.app.split("SECTION_ROUTE = ")[1].split("};")[0]):
            self.assertIn(route, views, f"{section} -> {route}")

    def test_313_navigation_can_never_be_prepared(self):
        """startAction refuses a navigation act and a view id before it ever calls the endpoint."""
        body = self.app.split("function startAction(")[1].split("\n  }")[0]
        self.assertIn("isNavigationAct(action) || viewById(action)", body)
        guard = body.index("isNavigationAct(action) || viewById(action)")
        post = body.index("postJSON(API")
        self.assertLess(guard, post, "the refusal must precede the prepare call")

    def test_314_prepare_is_validated_before_the_modal_opens(self):
        body = self.app.split("function startAction(")[1].split("\n  }")[0]
        self.assertIn("missingPreparationFields(d)", body)
        self.assertIn("if (!missing.length) openModal(d, label);", body)
        self.assertIn("else openModalBlocked(action, label, res, missing);", body)

    def test_315_required_preparation_fields_are_complete(self):
        decl = self.app.split("REQUIRED_PREPARATION_FIELDS = [")[1].split("]")[0]
        for field in ("action_token", "canonical_action", "readiness", "expected_authority",
                      "expected_effect"):
            self.assertIn(field, decl, field)
        validator = self.app.split("function missingPreparationFields(")[1].split("\n  }")[0]
        for extra in ("target_ids", "requires_confirmation", "confirmation_phrase",
                      "expires_in_seconds"):
            self.assertIn(extra, validator, extra)

    def test_316_required_fields_match_the_accepted_server_contract(self):
        """Every field the front end demands is a field prepare_action() actually returns.

        refresh-overview resolves its target without reading the config, so no workspace is needed.
        """
        prepared = UC.prepare_action(
            None, "fingerprint", "refresh-overview", {},
            now=0.0, tokens=UC.ActionTokenStore())
        decl = self.app.split("REQUIRED_PREPARATION_FIELDS = [")[1].split("]")[0]
        for field in re.findall(r'"([a-z_]+)"', decl):
            self.assertIn(field, prepared, field)
        for field in ("target_ids", "requires_confirmation", "expires_in_seconds"):
            self.assertIn(field, prepared, field)
        self.assertTrue(prepared["target_ids"], "a real preparation always names its target")

    def test_317_confirm_starts_disabled_in_the_served_markup(self):
        exec_tag = self.html.split('id="modal-execute"')[1].split(">")[0]
        self.assertIn("disabled", exec_tag, exec_tag)

    def test_318_reset_fails_closed(self):
        body = self.app.split("function resetModal(")[1].split("\n  }")[0]
        self.assertIn("exec.disabled = true", body)

    def test_319_blank_modal_is_structurally_impossible(self):
        body = self.app.split("function openBackdrop(")[1].split("\n  }\n")[0]
        self.assertIn("desc.children.length", body)
        self.assertIn("modalState.token = null", body)
        self.assertIn("exec.hidden = true; exec.disabled = true", body)
        # the guard must run BEFORE the backdrop is revealed
        self.assertLess(body.index("desc.children.length"),
                        body.index('id("modal-backdrop").hidden = false'))

    def test_320_blocked_prepare_never_keeps_a_token_or_a_confirm(self):
        body = self.app.split("function openModalBlocked(")[1].split("\n  }")[0]
        self.assertIn("modalState.token = null", body)
        self.assertIn("modalState.phrase = null", body)
        self.assertIn("exec.hidden = true; exec.disabled = true", body)
        self.assertIn("did not receive", body)
        self.assertIn("Nothing was changed", body)

    def test_321_phrase_gating_is_still_exact(self):
        body = self.app.split("function refreshExecuteEnabled(")[1].split("\n  }")[0]
        self.assertIn('id("modal-phrase").value !== modalState.phrase', body)
        for loosener in (".trim()", ".toLowerCase()", ".toUpperCase()", ".normalize("):
            self.assertNotIn(loosener, body, loosener)

    def test_322_execute_still_single_use_and_double_run_locked(self):
        body = self.app.split("function executeModal(")[1].split("\n  }")[0]
        self.assertIn("modalState.executing || modalState.done || !modalState.token", body)
        self.assertIn("!== modalState.phrase", body)

    def test_323_no_new_unsafe_construct(self):
        for banned in ("innerHTML", "outerHTML", "eval(", "new Function", "insertAdjacentHTML",
                       "document.write", "localStorage", "sessionStorage", "indexedDB"):
            self.assertNotIn(banned, self.app, banned)
            self.assertNotIn(banned, self.html, banned)

    def test_324_no_external_destination_added(self):
        for asset in ("app.js", "index.html", "styles.css"):
            src = static(asset)
            self.assertNotIn("//cdn", src, asset)
            self.assertNotIn("https://", src, asset)
            self.assertNotIn("http://", src, asset)

    def test_325_amazon_boundary_untouched(self):
        for asset in ("app.js", "index.html"):
            src = static(asset).lower()
            for term in ("sellercentral", "seller-central", "sp-api", "advertising-api",
                         "amazon.com"):
                self.assertNotIn(term, src, f"{asset}:{term}")

    def test_326_navigation_never_touches_the_action_endpoint(self):
        """Every startAction() call site is an action:-classified control; no nav control binds one."""
        # comments explain the contract and legitimately name startAction; only real code counts
        code = re.sub(r"/\*.*?\*/", "", self.app, flags=re.S)
        code = "\n".join(re.sub(r"//.*$", "", ln) for ln in code.splitlines())
        lines = code.splitlines()
        sites = [(i + 1, ln) for i, ln in enumerate(lines)
                 if "startAction(" in ln and "function startAction" not in ln]
        self.assertGreaterEqual(len(sites), 2, sites)
        for line_no, line in sites:
            window = "\n".join(lines[max(0, line_no - 4):line_no])
            self.assertIn('"data-act": "action:', window, f"line {line_no}: {line.strip()}")
        # and no nav-classified control is ever built with a startAction binding next to it
        for match in re.finditer(r'"data-act": "nav:', code):
            window = code[match.start():match.start() + 400]
            self.assertNotIn("startAction", window, window[:200])


# ================================================================ 21) Node DOM render contract
@unittest.skipIf(NODE is None, "node is not available on this machine")
class TestDomRenderContract(unittest.TestCase):
    """Render the REAL app.js in a dependency-free DOM harness and assert the owner contract."""

    def test_200_to_220_dom_render_contract(self):
        tmp = tempfile.mkdtemp(prefix="p714-dom-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        def envelope(data, readiness=UC.CONSOLE_READY, status=200):
            return {"status": status, "body": {"readiness": readiness, "data": data}}

        def paged(rows):
            return {"data": rows, "page": 1, "page_size": 50, "total": len(rows),
                    "total_pages": 1 if rows else 0}

        na = NA.build_next_action(model(overview={"pending_decisions": 2}),
                                  generated_at="2026-07-28T00:00:00Z")
        fixtures = {
            "app_js": os.path.join(STATIC_DIR, "app.js"),
            "session": envelope({"csrf_token": "csrf-secret-value", "session_active": True}),
            "overview": envelope({
                "overview": {"module_status": {"analysis": "READY", "backup": "READY"},
                             "module_labels": dict(UC.MODULE_OWNER_LABELS),
                             "pending_decisions": 2, "pending_manual_actions": 0,
                             "due_followups": 0, "open_alerts": 0, "due_watchlists": 0,
                             "notification_unknown": 0, "notification_sent": 0,
                             "notification_failed": 0, "research_runs": 1, "backup_snapshots": 1,
                             "update_available": False, "attention_items": 0,
                             "blocked_modules": [], "stale_data_phases": []},
                "freshness": {"7.3": {"latest": "2026-07-27T10:00:00Z", "stale": False}},
                "cache": {"state_hash": "h1", "age_seconds": 0.0},
                "disclaimer": ["A", "B"], "next_action": na}),
            "activity": envelope({"audit_chain": {"ok": True, "event_count": 0},
                                  "events": paged([])}),
            "research": envelope({"status": "READY", "counts": {"runs": 1},
                                  "runs": paged([{"row_id": "uc-r1", "run_id": "run-1",
                                                  "readiness": "READY", "source_count": 2,
                                                  "evidence_count": 5}])}),
            "backups": envelope({"status": "READY", "counts": {"snapshots": 1},
                                 "update_check": None, "recovery_plans": [],
                                 "snapshots": paged([{"row_id": "uc-s1", "snapshot_id": "snap-1",
                                                      "file_count": 3, "total_bytes": 99,
                                                      "encrypted": True}])}),
            # Dashboard V1 Workflow (production.phase7_workflow_stage_model). Deliberately covers
            # all six modeled states at once (2=READY, 3=STALE, 4=BLOCKED, 5=UNKNOWN,
            # 6=NOT_STARTED, 8=NOT_ACCEPTED), both not-tracked informational stages (1, 7), and one
            # composite with real partial progress (9: listing READY, A+ NOT_STARTED) -- the exact
            # shape build_workflow_section actually returns, not a simplified stand-in.
            "workflow": envelope({
                "status": "READY_PARTIAL",
                "counts": {"modeled": 11, "ready": 6, "blocked": 1},
                "product_root": "/runs/T2", "primary_next_stage_id": 3,
                # DASHBOARD-V1-SPEC.md Section 7. This fixture's product_root ("/runs/T2") is the
                # real quarantined workspace, so HISTORICAL is the narratively-consistent choice --
                # the other two states are covered directly in WorkspaceTrust (Python), which
                # exercises the real _workspace_trust_state logic; this fixture only needs to prove
                # the DOM renders whatever state the backend sends.
                "trust": {"state": "HISTORICAL",
                         "reason": "Quarantined -- readable as history, never a basis for a new "
                                   "decision."},
                "stages": [
                    {"stage_id": 1, "label": "Seed keyword", "group": "Research", "modeled": False,
                     "state": None, "components": {}, "blocked_by": [], "command": None,
                     "acceptance_required": False,
                     "informational_reason": "Captured as part of Stage 3 (ASIN batches) -- no "
                                             "separate action needed here."},
                    {"stage_id": 2, "label": "Import Amazon + Xray", "group": "Research",
                     "modeled": True, "state": "READY", "components": {}, "blocked_by": [],
                     "command": None, "acceptance_required": False, "informational_reason": None},
                    {"stage_id": 3, "label": "ASIN batches", "group": "Research", "modeled": True,
                     "state": "STALE", "components": {}, "blocked_by": [],
                     "command": "python research/asin_batches.py <folder> --seed \"...\"",
                     "acceptance_required": False, "informational_reason": None},
                    {"stage_id": 4, "label": "Cerebro re-import", "group": "Research",
                     "modeled": True, "state": "BLOCKED", "components": {}, "blocked_by": [2],
                     "command": None, "acceptance_required": False, "informational_reason": None},
                    {"stage_id": 5, "label": "Clean / merge / analyze", "group": "Research",
                     "modeled": True, "state": "UNKNOWN", "components": {}, "blocked_by": [],
                     "command": "python research/master_keyword_builder.py <folder>",
                     "acceptance_required": False, "informational_reason": None},
                    {"stage_id": 6, "label": "Master Keyword List", "group": "Decide",
                     "modeled": True, "state": "NOT_STARTED", "components": {}, "blocked_by": [],
                     "command": "python research/master_keyword_builder.py <folder>",
                     "acceptance_required": False, "informational_reason": None},
                    {"stage_id": 7, "label": "Score + select opportunity", "group": "Decide",
                     "modeled": False, "state": None, "components": {}, "blocked_by": [],
                     "command": None, "acceptance_required": False,
                     "informational_reason": "Opportunity review -- not separately tracked."},
                    {"stage_id": 8, "label": "Keywords -> listing", "group": "Build",
                     "modeled": True, "state": "NOT_ACCEPTED", "components": {}, "blocked_by": [],
                     "command": "python -m listing.keyword_allocation_planner <run_dir>",
                     "acceptance_required": True, "informational_reason": None},
                    {"stage_id": 9, "label": "Listing + A+", "group": "Build", "modeled": True,
                     "state": "NOT_STARTED",
                     "components": {"listing": "READY", "aplus": "NOT_STARTED"},
                     "blocked_by": [], "command": "python -m production.product_detail_page <run_dir>",
                     "acceptance_required": False, "informational_reason": None},
                    {"stage_id": 10, "label": "Photo / A+ prompts", "group": "Build",
                     "modeled": True, "state": "READY", "components": {"listing_image_prompts": "READY"},
                     "blocked_by": [], "command": None, "acceptance_required": False,
                     "informational_reason": None},
                    {"stage_id": 11, "label": "PPC export", "group": "Launch", "modeled": True,
                     "state": "READY", "components": {"readiness": "READY"}, "blocked_by": [],
                     "command": None, "acceptance_required": False, "informational_reason": None},
                    {"stage_id": 12, "label": "Import PPC reports", "group": "Launch",
                     "modeled": True, "state": "READY", "components": {}, "blocked_by": [],
                     "command": None, "acceptance_required": False, "informational_reason": None},
                    {"stage_id": 13, "label": "Analyze + suggest", "group": "Launch",
                     "modeled": True, "state": "READY", "components": {"analysis": "READY"},
                     "blocked_by": [], "command": None, "acceptance_required": False,
                     "informational_reason": None},
                ],
            }),
            "system": envelope({"system": {
                "python_version": "3.12.10", "platform": "win32",
                "audit_chain": {"ok": True, "event_count": 0}, "recent_errors": [],
                "phase_directories": {"7.3": True},
                "seller_central_counters": dict(UC.SELLER_CENTRAL_COUNTERS),
                "connectivity_boundary": {"seller_central_blocked": True,
                                          "seller_api_blocked": True,
                                          "advertising_api_blocked": True},
                "runs_ignored": True, "network_policy_status": "LOOPBACK_UI_ONLY"}}),
            "alerts": envelope({"status": "READY_EMPTY", "counts": {}, "alerts": paged([]),
                                "notice": "n"}),
            "notifications": envelope({"status": "READY_EMPTY", "counts": {}, "routes": [],
                                       "deliveries": [], "batches": paged([]), "notice": "n"}),
            "analysis": envelope({"status": "READY", "counts": {}, "rows": paged([])}),
            "watchlists": envelope({"status": "READY_EMPTY", "counts": {},
                                    "watchlists": paged([])}),
            # mirrors the accepted server contract exactly: prepare_action() always names the
            # bounded target it resolved, so target_ids is never empty for a real preparation
            # (see _resolve_target: every branch returns exactly one id).
            "prepare_default": envelope({
                "action_token": "action-token-secret", "canonical_action": "refresh-overview",
                "requires_confirmation": False, "confirmation_phrase": None,
                "expected_authority": "console", "target_ids": ["overview"],
                "expected_effect": "Rebuild the overview.", "network_use": "NONE",
                "local_state_changes": "cache_only", "upstream_state_changes": "NONE",
                "expires_in_seconds": 300, "readiness": UC.ACTION_READY}),
            "execute_ok": envelope({"readiness": UC.ACTION_COMPLETED, "authority": "console",
                                    "upstream_result_id": "r-1", "upstream_summary": {}}),
            # keyed directly (not nested under "workflow") -- the harness reads these to verify
            # the rendered DOM against the SAME fixture without re-deriving expectations in JS.
            "workflow_state_by_stage": {"2": "READY", "3": "STALE", "4": "BLOCKED",
                                        "5": "UNKNOWN", "6": "NOT_STARTED", "8": "NOT_ACCEPTED"},
            "workflow_composite_stage_id": 9,
        }
        fx_path = os.path.join(tmp, "fixtures.json")
        with open(fx_path, "w", encoding="utf-8") as f:
            json.dump(fixtures, f)

        out = subprocess.run([NODE, os.path.join(TESTS, "phase7_14_console_dom_harness.js"),
                              fx_path], cwd=TESTS, capture_output=True, text=True)
        report = out.stdout + out.stderr
        failures = [ln for ln in report.splitlines() if ln.startswith("FAIL")]
        self.assertEqual(out.returncode, 0, "\n".join(failures) or report[-3000:])
        total = [ln for ln in report.splitlines() if ln.startswith("TOTAL")]
        self.assertTrue(total, report[-2000:])
        self.assertIn("FAILED 0", total[0])
        # the harness is substantial: it must actually be running its full contract
        self.assertGreaterEqual(int(total[0].split()[1]), 190, total[0])


# ================================================================ 7c) stop EXIT VERIFICATION
# Phase 7.14 stop-exit-verification hotfix.
#
# The accepted baseline proved "has it stopped?" with process_alive(), i.e. OpenProcess +
# GetProcessTimes. On Windows that is only conclusive when it is FALSE: a terminated process object
# stays addressable for as long as ANY handle to it is open, so the check keeps answering "alive"
# about a process the kernel has already reported as exited. Every stop test in this file injected
# the process-alive seam, so nothing ever exercised the real function against a real corpse — which
# is why the defect reached the owner as `stop_seconds=15.03 CONSOLE_DID_NOT_STOP` for a process
# that a read-only diagnostic then proved was gone.
#
# These tests use REAL Windows child processes of this test run. They spawn only
# `python -c "import time; time.sleep(...)"`, they signal only PIDs they spawned themselves, and
# every child is cleaned up. Nothing here touches the repository, the network or any other process.
WINDOWS_ONLY = unittest.skipUnless(os.name == "nt", "Windows process-lifecycle proof")


class RealProcessBase(unittest.TestCase):
    """Spawns and reaps real children, so a failing assertion can never leak a process."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p714-exit-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ws = L.Workspace(self.tmp)
        self.ws.ensure()
        self._children = []

    def spawn_child(self, seconds=300):
        """One real child, spawned exactly the way the launcher spawns the console."""
        kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                  "stderr": subprocess.DEVNULL, "close_fds": True}
        if os.name == "nt":
            kwargs["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                       | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({seconds})"],
                                **kwargs)
        self._children.append(proc)
        self.addCleanup(self._reap, proc)
        return proc

    def spawn_orphan_pid(self, seconds=300):
        """A real child whose PID is held by NOBODY: the parent handle is released immediately, so
        the process object is freed the moment the process itself goes away."""
        proc = self.spawn_child(seconds)
        pid = proc.pid
        handle = getattr(proc, "_handle", None)
        if handle is not None:
            handle.Close()                       # release the only reference this test holds
            proc.returncode = 0                  # keep Popen.__del__ quiet; we reap by PID below
        self.addCleanup(L.terminate_process_result, pid, hard=True)
        return pid

    def _reap(self, proc):
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
        except Exception:                        # noqa: BLE001 — a reaped child is not a failure
            pass


class TestWindowsRealProcessExitProof(RealProcessBase):
    """Requirements 1-5: what the OS actually reports for a real, genuinely terminated process."""

    @WINDOWS_ONLY
    def test_x01_real_child_exits_and_is_detected_as_exited(self):
        """1 + 3 + 4: a real child is terminated; the handle is signalled and carries a real code."""
        proc = self.spawn_child()
        v = L.open_exit_verifier(proc.pid)
        self.addCleanup(v.close)
        self.assertTrue(v.usable, v.open_error)
        before = v.state()
        self.assertEqual(before["exit_state"], L.EXIT_STATE_RUNNING)
        self.assertEqual(before["wait_name"], "WAIT_TIMEOUT")
        self.assertTrue(before["still_active"])
        self.assertEqual(before["exit_code"], 259)          # STILL_ACTIVE

        res = L.terminate_process_result(proc.pid, hard=True)
        self.assertTrue(res["ok"], res)
        proc.wait(timeout=15)

        after = v.state()
        self.assertEqual(after["exit_state"], L.EXIT_STATE_EXITED)
        self.assertEqual(after["wait_name"], "WAIT_OBJECT_0")     # 3) handle is signalled
        self.assertEqual(after["wait_result"], 0)
        self.assertFalse(after["still_active"])                   # 4) non-STILL_ACTIVE exit code
        self.assertNotEqual(after["exit_code"], 259)

    @WINDOWS_ONLY
    def test_x02_terminated_child_with_referenced_handle_is_still_openable(self):
        """2 + THE ROOT CAUSE: while a handle is referenced, the OLD check calls a corpse alive.

        This is the exact defect. The kernel says the process exited; process_alive() says it is
        still there, for as long as any handle to the process object remains open."""
        proc = self.spawn_child()
        pid = proc.pid
        v = L.open_exit_verifier(pid)                # this handle keeps the object addressable
        self.addCleanup(v.close)
        self.assertTrue(L.terminate_process_result(pid, hard=True)["ok"])
        proc.wait(timeout=15)

        # The kernel: definitively exited.
        st = v.state()
        self.assertEqual(st["exit_state"], L.EXIT_STATE_EXITED)
        self.assertFalse(st["still_active"])

        # The accepted baseline's check, on that same definitively-exited process:
        self.assertTrue(L.process_alive(pid),
                        "expected the documented Windows behaviour: a terminated process object "
                        "stays openable while a handle is referenced")
        self.assertIsNotNone(L.process_start_token(pid))   # identity still resolves, unchanged

        # ...and it stays wrong for as long as the handle is held, which is what produced the
        # owner's 15-second false CONSOLE_DID_NOT_STOP.
        for _ in range(8):
            time.sleep(0.05)
            self.assertTrue(L.process_alive(pid))
            self.assertEqual(v.state()["exit_state"], L.EXIT_STATE_EXITED)

    @WINDOWS_ONLY
    def test_x03_process_alive_goes_false_once_every_handle_is_closed(self):
        """2 (second half): the FALSE answer is the conclusive one, and it arrives on handle close.

        Same process, same instant, two different answers depending only on whether a handle is
        referenced — which is precisely why a handle-free liveness check cannot be trusted."""
        pid = self.spawn_orphan_pid()
        v = L.open_exit_verifier(pid)             # the ONLY remaining reference
        self.addCleanup(v.close)
        self.assertTrue(L.terminate_process_result(pid, hard=True)["ok"])
        deadline = time.monotonic() + 10.0
        while v.state()["exit_state"] != L.EXIT_STATE_EXITED and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(v.state()["exit_state"], L.EXIT_STATE_EXITED)

        self.assertTrue(L.process_alive(pid))     # handle referenced -> reported alive
        v.close()                                 # last reference released
        deadline = time.monotonic() + 10.0
        while L.process_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(L.process_alive(pid))    # only now is the old check correct
        self.assertIsNone(L.process_start_token(pid))

    @WINDOWS_ONLY
    def test_x04_natural_exit_is_proven_without_any_termination(self):
        """1: a child that exits on its own is proven exited — no signal is ever sent."""
        proc = self.spawn_child(seconds=1)
        v = L.open_exit_verifier(proc.pid)
        self.addCleanup(v.close)
        proc.wait(timeout=20)
        st = v.state()
        self.assertEqual(st["exit_state"], L.EXIT_STATE_EXITED)
        self.assertEqual(st["wait_name"], "WAIT_OBJECT_0")
        self.assertEqual(st["exit_code"], 0)
        self.assertFalse(st["still_active"])
        # and the old check still reports it alive, because this test holds a handle
        self.assertTrue(L.process_alive(proc.pid))

    @WINDOWS_ONLY
    def test_x05_terminate_result_is_captured_including_failure(self):
        """5: TerminateProcess's answer is recorded, and a refused request reports the reason."""
        proc = self.spawn_child()
        ok = L.terminate_process_result(proc.pid, hard=True)
        self.assertEqual(ok["api"], "TerminateProcess")
        self.assertTrue(ok["ok"])
        self.assertIsNone(ok["error"])
        proc.wait(timeout=15)

        # A PID that cannot be opened at all: the request FAILS and says so.
        bad = L.terminate_process_result(0x7FFFFFF0, hard=True)
        self.assertFalse(bad["ok"])
        self.assertTrue(str(bad["error"]).startswith("OPEN_PROCESS_FAILED_"), bad)
        for pid in (0, -1, "x", None):
            r = L.terminate_process_result(pid, hard=True)
            self.assertFalse(r["ok"])
            self.assertEqual(r["error"], "INVALID_PID")

    @WINDOWS_ONLY
    def test_x06_verifier_pins_the_pid_against_reuse(self):
        """The handle held across a stop is also what stops Windows recycling the PID mid-stop."""
        proc = self.spawn_child()
        pid = proc.pid
        token_before = L.process_start_token(pid)
        v = L.open_exit_verifier(pid)
        self.addCleanup(v.close)
        self.assertTrue(L.terminate_process_result(pid, hard=True)["ok"])
        proc.wait(timeout=15)
        self.assertEqual(L.process_start_token(pid), token_before)

    def test_x07_verifier_is_windows_only_and_never_asks_for_terminate_rights(self):
        src = read(LAUNCHER_PATH)
        cls = src.split("class WindowsExitVerifier", 1)[1].split("\ndef open_exit_verifier", 1)[0]
        self.assertIn("_WIN_SYNCHRONIZE | _WIN_PROCESS_QUERY_LIMITED_INFORMATION", cls)
        self.assertNotIn("_WIN_PROCESS_TERMINATE", cls)
        self.assertNotIn("TerminateProcess", cls)
        if os.name != "nt":
            self.assertIsNone(L.open_exit_verifier(os.getpid()))

    @WINDOWS_ONLY
    def test_x08_unopenable_pid_is_unproven_not_exited(self):
        """A handle that cannot be opened must fail CLOSED, never report a convenient exit."""
        v = L.WindowsExitVerifier(0x7FFFFFF0)
        self.addCleanup(v.close)
        self.assertFalse(v.usable)
        st = v.state()
        self.assertEqual(st["exit_state"], L.EXIT_STATE_UNPROVEN)
        self.assertTrue(str(st["api_error"]).startswith("OPEN_PROCESS_FAILED_"), st)


class TestStopExitVerificationContract(LauncherBase):
    """Requirements 6-15: the stop RESULT the owner and the record actually receive."""

    def stop_with(self, pid=7100, token=None, **kw):
        self.ws.clear_pid()
        self.ws.write_pid({"pid": pid, "process_start_token": token or f"tok-{pid}",
                           "host": "127.0.0.1", "port": 8780})
        kw.setdefault("health", lambda h, p: dict(HEALTHY))
        return self.launcher(**kw).stop()

    # ---- 6) alive through the whole timeout ----------------------------------------------------
    def test_x10_process_alive_through_timeout_reports_still_running(self):
        res = self.stop_with(pid=7101, alive=lambda pid: True, stop_timeout=2.0,
                             port_probe=lambda h, p: True)
        self.assertEqual(res["readiness"], L.LAUNCHER_FAILED)
        self.assertEqual(res["exit_state"], L.EXIT_STATE_RUNNING)
        self.assertEqual(res["error_code"], "CONSOLE_DID_NOT_STOP")
        self.assertEqual(res["stop_state"], L.STOP_STATE_ALIVE)
        self.assertFalse(res["runtime_state_cleared"])
        self.assertIsNotNone(self.ws.read_pid())      # 11) state kept: exit was NOT proven

    # ---- 7) exits immediately, before the timeout ----------------------------------------------
    def test_x11_process_exits_immediately_is_reported_as_exited(self):
        alive = {"v": True}
        res = self.stop_with(pid=7102, alive=lambda pid: alive["v"],
                             terminate=lambda pid, hard=False: (alive.update(v=False), True)[-1])
        self.assertEqual(res["readiness"], L.LAUNCHER_STOPPED)
        self.assertEqual(res["exit_state"], L.EXIT_STATE_EXITED)
        self.assertEqual(res["stop_state"], L.STOP_STATE_EXITED)
        self.assertFalse(res["escalated"])
        self.assertTrue(res["runtime_state_cleared"])

    # ---- 8) exits just after a polling boundary -------------------------------------------------
    def test_x12_exit_just_after_a_polling_boundary_is_still_an_exit(self):
        """The state is re-read at the top of the loop BEFORE the budget is re-checked, so a process
        that exits during the final sleep is reported as exited rather than as a timeout — even
        though the budget has elapsed by the time the answer arrives."""
        gone = {"v": False}

        def alive(pid):
            return not gone["v"]

        def sleep(_seconds):
            gone["v"] = True               # the process exits DURING the last sleep
        res = self.stop_with(pid=7103, alive=alive, sleep=sleep, stop_timeout=0.3)
        self.assertEqual(res["exit_state"], L.EXIT_STATE_EXITED)
        self.assertEqual(res["readiness"], L.LAUNCHER_STOPPED)
        self.assertEqual(res["owner_message"], L.STOP_SUCCESS_MESSAGE)
        self.assertFalse(res["escalated"])
        # the answer arrived after the budget had already elapsed, and was still honoured
        self.assertGreaterEqual(res["stop_seconds"], 0.3)

    # ---- 9) port closed but the process is alive ------------------------------------------------
    def test_x13_port_closed_but_process_alive_is_not_a_success(self):
        res = self.stop_with(pid=7104, alive=lambda pid: True, stop_timeout=2.0,
                             port_probe=lambda h, p: False)
        self.assertEqual(res["readiness"], L.LAUNCHER_FAILED)
        self.assertEqual(res["stop_state"], L.STOP_STATE_PORT_CLOSED_ALIVE)
        self.assertFalse(res["port_open_after_stop"])
        self.assertEqual(res["exit_state"], L.EXIT_STATE_RUNNING)
        self.assertNotEqual(res["readiness"], L.LAUNCHER_STOPPED)

    def test_x13b_a_closed_port_alone_never_authorizes_success(self):
        """The port probe is a diagnostic. It may not turn an unproven state into a stop."""
        res = self.stop_with(pid=7105, alive=lambda pid: True, stop_timeout=1.0,
                             port_probe=lambda h, p: False,
                             health=lambda h, p: dict(UNHEALTHY))
        self.assertNotEqual(res["readiness"], L.LAUNCHER_STOPPED)
        self.assertIn(res["exit_state"], (L.EXIT_STATE_RUNNING, L.EXIT_STATE_UNPROVEN))

    # ---- 10 + 11) runtime state ------------------------------------------------------------------
    def test_x14_stale_runtime_state_after_a_proven_exit_is_reported(self):
        alive = {"v": True}
        self.ws.clear_pid()
        self.ws.write_pid({"pid": 7106, "process_start_token": "tok-7106"})
        orig = self.ws.clear_pid
        self.ws.clear_pid = lambda: False           # the record cannot be removed
        self.addCleanup(setattr, self.ws, "clear_pid", orig)
        res = self.launcher(alive=lambda pid: alive["v"], health=lambda h, p: dict(HEALTHY),
                            terminate=lambda pid, hard=False: (alive.update(v=False), True)[-1]).stop()
        self.assertEqual(res["exit_state"], L.EXIT_STATE_EXITED)
        self.assertEqual(res["readiness"], L.LAUNCHER_STOPPED)
        self.assertEqual(res["stop_state"], L.STOP_STATE_EXITED_STALE_STATE)
        self.assertFalse(res["runtime_state_cleared"])

    def test_x15_runtime_state_is_cleared_only_after_a_proven_exit(self):
        for label, kw in (("running", {"alive": lambda pid: True, "stop_timeout": 1.0}),):
            with self.subTest(label):
                res = self.stop_with(pid=7107, **kw)
                self.assertNotEqual(res["exit_state"], L.EXIT_STATE_EXITED)
                self.assertFalse(res["runtime_state_cleared"])
                self.assertIsNotNone(self.ws.read_pid())
        alive = {"v": True}
        res = self.stop_with(pid=7108, alive=lambda pid: alive["v"],
                             terminate=lambda pid, hard=False: (alive.update(v=False), True)[-1])
        self.assertTrue(res["runtime_state_cleared"])
        self.assertIsNone(self.ws.read_pid())

    # ---- termination-request failure --------------------------------------------------------------
    def test_x16_failed_termination_request_never_reports_success(self):
        res = self.stop_with(pid=7109, alive=lambda pid: True, stop_timeout=2.0,
                             terminate=lambda pid, hard=False: {"ok": False, "api": "TerminateProcess",
                                                                "error": "TERMINATE_PROCESS_FAILED_5"})
        self.assertNotEqual(res["readiness"], L.LAUNCHER_STOPPED)
        self.assertTrue(res["termination_request_failed"])
        self.assertEqual(res["stop_state"], L.STOP_STATE_TERMINATE_FAILED)
        self.assertTrue(any(r["error"] == "TERMINATE_PROCESS_FAILED_5"
                            for r in res["terminate_requests"]), res["terminate_requests"])

    def test_x16b_terminate_results_are_captured_for_every_request(self):
        res = self.stop_with(pid=7110, alive=lambda pid: True, stop_timeout=2.0)
        self.assertGreaterEqual(len(res["terminate_requests"]), 2)
        self.assertEqual([r["hard"] for r in res["terminate_requests"]][:2], [False, True])
        for r in res["terminate_requests"]:
            self.assertIn("ok", r)

    def test_x16c_a_raising_terminate_seam_is_data_not_a_crash(self):
        def boom(pid, hard=False):
            raise OSError("refused")
        res = self.stop_with(pid=7111, alive=lambda pid: True, stop_timeout=1.0, terminate=boom)
        self.assertNotEqual(res["readiness"], L.LAUNCHER_STOPPED)
        self.assertTrue(res["termination_request_failed"])

    # ---- unproven state ---------------------------------------------------------------------------
    def test_x17_unproven_exit_state_is_reported_as_unproven(self):
        class Unprovable:
            usable = True
            start_token = "tok-7112"             # identity proven; only the EXIT state is unprovable
            token_error = None

            def state(self):
                return {"source": "windows_process_handle", "exit_state": L.EXIT_STATE_UNPROVEN,
                        "api_error": "WAIT_FAILED_5"}

            def close(self):
                pass
        res = self.stop_with(pid=7112, stop_timeout=1.0, alive=lambda pid: True,
                             exit_verifier=lambda pid: Unprovable())
        self.assertEqual(res["readiness"], L.LAUNCHER_FAILED)
        self.assertEqual(res["exit_state"], L.EXIT_STATE_UNPROVEN)
        self.assertEqual(res["error_code"], "CONSOLE_EXIT_NOT_PROVEN")
        self.assertEqual(res["stop_state"], L.STOP_STATE_UNPROVEN)
        self.assertEqual(res["owner_message"], L.STOP_EXIT_UNPROVEN_MESSAGE)
        self.assertFalse(res["runtime_state_cleared"])

    def test_x17b_handle_backed_exit_beats_a_lying_alive_seam(self):
        """The handle is authoritative: a process_alive() that still says "alive" cannot turn a
        kernel-proven exit into a false CONSOLE_DID_NOT_STOP. This is the owner's defect, inverted."""
        class Exited:
            usable = True
            start_token = "tok-7113"             # the pinned handle proves it is the recorded process
            token_error = None

            def state(self):
                return {"source": "windows_process_handle", "exit_state": L.EXIT_STATE_EXITED,
                        "wait_name": "WAIT_OBJECT_0", "exit_code": 1, "still_active": False}

            def close(self):
                pass
        res = self.stop_with(pid=7113, alive=lambda pid: True, stop_timeout=15.0,
                             exit_verifier=lambda pid: Exited())
        self.assertEqual(res["readiness"], L.LAUNCHER_STOPPED)
        self.assertEqual(res["exit_state"], L.EXIT_STATE_EXITED)
        self.assertEqual(res["owner_message"], L.STOP_SUCCESS_MESSAGE)
        self.assertLess(res["stop_seconds"], 15.0)
        self.assertEqual(res["exit_verification"]["wait_name"], "WAIT_OBJECT_0")

    def test_x18_bounded_timeout_is_not_extended(self):
        self.assertEqual(L.STOP_TIMEOUT_SECONDS, 15.0)
        res = self.stop_with(pid=7114, alive=lambda pid: True, stop_timeout=2.0)
        self.assertLessEqual(res["stop_seconds"], 12.0)

    # ---- 14 + 15) owner messages are truthful ----------------------------------------------------
    def test_x19_owner_success_message_is_truthful(self):
        alive = {"v": True}
        res = self.stop_with(pid=7115, alive=lambda pid: alive["v"],
                             terminate=lambda pid, hard=False: (alive.update(v=False), True)[-1])
        self.assertEqual(res["owner_message"], "The toolkit stopped safely.")
        self.assertEqual(res["exit_state"], L.EXIT_STATE_EXITED)

    def test_x20_owner_failure_messages_are_truthful_and_scoped(self):
        running = self.stop_with(pid=7116, alive=lambda pid: True, stop_timeout=1.0)
        self.assertEqual(running["owner_message"], L.STOP_STILL_RUNNING_MESSAGE)
        self.assertIn("Nothing else on this computer was stopped.", running["owner_message"])
        self.assertIn("Open technical details", running["owner_message"])

    def test_x21_owner_message_is_never_a_raw_error_code(self):
        results = [
            self.stop_with(pid=7117, alive=lambda pid: True, stop_timeout=1.0),
            self.stop_with(pid=7118, alive=lambda pid: True, start_token=lambda pid: None),
        ]
        for res in results:
            msg = res["owner_message"]
            self.assertTrue(msg.endswith("."), msg)
            for code in ("CONSOLE_DID_NOT_STOP", "CONSOLE_EXIT_NOT_PROVEN", "WAIT_FAILED",
                         "TERMINATE_PROCESS_FAILED", "SESSION7_14_"):
                self.assertNotIn(code, msg)

    def test_x22_four_owner_sentences_cover_the_six_machine_states(self):
        """A proven exit whose runtime record could NOT be cleaned up gets its own qualified
        sentence: it is still a success, but not the same success, and the owner has to know before
        starting again. One capitalization throughout, so a single Stop session never mixes forms."""
        states = {L.STOP_STATE_EXITED, L.STOP_STATE_EXITED_STALE_STATE, L.STOP_STATE_ALIVE,
                  L.STOP_STATE_PORT_CLOSED_ALIVE, L.STOP_STATE_TERMINATE_FAILED,
                  L.STOP_STATE_UNPROVEN}
        self.assertEqual(len(states), 6)
        msgs = {L.STOP_SUCCESS_MESSAGE, L.STOP_EXITED_STALE_STATE_MESSAGE,
                L.STOP_STILL_RUNNING_MESSAGE, L.STOP_EXIT_UNPROVEN_MESSAGE}
        self.assertEqual(len(msgs), 4)
        for m in msgs:
            self.assertTrue(m.startswith("The toolkit "), m)
            self.assertNotIn("The Toolkit", m)
        for m in (L.STOP_EXITED_STALE_STATE_MESSAGE, L.STOP_STILL_RUNNING_MESSAGE,
                  L.STOP_EXIT_UNPROVEN_MESSAGE):
            self.assertIn("Nothing else on this computer was stopped.", m)

    # ---- the pinned-handle ordering contract -----------------------------------------------------
    def test_x57_pinned_handle_is_opened_before_health_and_held_to_the_very_end(self):
        """Requirements 1, 5 and 8, as an observable ORDER.

        The accepted implementation probed health -- up to HEALTH_REQUEST_TIMEOUT, 3.0 s -- while the
        PID was unpinned, and only then opened the handle. The handle must now be the FIRST thing
        that happens and the LAST thing released."""
        order = []

        class Pinned:
            usable = True
            start_token = "tok-7150"
            token_error = None

            def state(self):
                return {"source": "windows_process_handle", "exit_state": L.EXIT_STATE_EXITED,
                        "wait_name": "WAIT_OBJECT_0", "exit_code": 0, "still_active": False}

            def close(self):
                order.append("verifier_closed")

        def verifier(pid):
            order.append("verifier_opened")
            return Pinned()

        def health(h, p):
            order.append("health")
            return dict(HEALTHY)

        res = self.stop_with(pid=7150, alive=lambda pid: True, exit_verifier=verifier,
                             health=health)
        self.assertEqual(res["readiness"], L.LAUNCHER_STOPPED)
        self.assertEqual(order[0], "verifier_opened")
        self.assertIn("health", order)
        self.assertLess(order.index("verifier_opened"), order.index("health"))
        self.assertEqual(order[-1], "verifier_closed")
        self.assertEqual(res["process_identity"]["authorized_by"], "PINNED_HANDLE_TOKEN")
        self.assertTrue(res["process_identity"]["handle_token_matches_recorded"])

    def test_x58_identity_refusal_probes_no_health_and_signals_nothing(self):
        """A refusal must not even reach the health probe: the decision is already made, and the
        probe is what used to create the unpinned window."""
        calls = []

        class Pinned:
            usable = True
            start_token = "tok-DIFFERENT"
            token_error = None

            def state(self):
                return {"source": "windows_process_handle", "exit_state": L.EXIT_STATE_RUNNING}

            def close(self):
                pass

        res = self.stop_with(pid=7151, token="tok-RECORDED", alive=lambda pid: True,
                             exit_verifier=lambda pid: Pinned(),
                             health=lambda h, p: (calls.append(1), dict(HEALTHY))[-1])
        self.assertEqual(res["readiness"], L.LAUNCHER_STOP_REFUSED)
        self.assertEqual(res["error_code"], "PID_REUSED_BY_ANOTHER_PROCESS")
        self.assertEqual(calls, [], "health was probed after identity had already failed")
        self.assertEqual(res["terminate_requests"], [])
        self.assertEqual(self.terminated, [])
        self.assertFalse(res["runtime_state_cleared"])
        self.assertIsNotNone(self.ws.read_pid(), "a refusal cleared runtime state")

    def test_x59_stale_runtime_record_gets_its_own_qualified_sentence(self):
        """A proven exit whose runtime record could not be cleaned up must NOT read as the plain
        success sentence: the owner has to know before starting again."""
        alive = {"v": True}
        self.ws.clear_pid()
        self.ws.write_pid({"pid": 7152, "process_start_token": "tok-7152"})
        orig = self.ws.clear_pid
        self.ws.clear_pid = lambda: False
        self.addCleanup(setattr, self.ws, "clear_pid", orig)
        res = self.launcher(alive=lambda pid: alive["v"], health=lambda h, p: dict(HEALTHY),
                            terminate=lambda pid, hard=False: (alive.update(v=False), True)[-1]).stop()
        self.assertEqual(res["readiness"], L.LAUNCHER_STOPPED)
        self.assertEqual(res["stop_state"], L.STOP_STATE_EXITED_STALE_STATE)
        self.assertFalse(res["runtime_state_cleared"])
        self.assertEqual(res["owner_message"], L.STOP_EXITED_STALE_STATE_MESSAGE)
        self.assertNotEqual(res["owner_message"], L.STOP_SUCCESS_MESSAGE)
        self.assertIn("could not be cleaned up", res["owner_message"])
        self.assertIn("Open technical details before starting it again.", res["owner_message"])

    # ---- command identity ------------------------------------------------------------------------
    def test_x23_command_identity_is_not_claimed_when_health_is_unreachable(self):
        """The accepted baseline recorded command_identity_verified=true whenever the health probe
        failed to connect (`not http_status` is true for a transport error). The owner's
        2026-07-30 record shows exactly that: health unreachable, identity reported "verified"."""
        res = self.stop_with(pid=7119, alive=lambda pid: True, stop_timeout=1.0,
                             health=lambda h, p: dict(UNHEALTHY))
        self.assertFalse(res["command_identity_verified"])
        self.assertEqual(res["command_identity_evidence"], "HEALTH_UNREACHABLE")

    def test_x23b_command_identity_is_claimed_only_on_the_accepted_contract(self):
        alive = {"v": True}
        ok = self.stop_with(pid=7120, alive=lambda pid: alive["v"],
                            terminate=lambda pid, hard=False: (alive.update(v=False), True)[-1])
        self.assertTrue(ok["command_identity_verified"])
        self.assertEqual(ok["command_identity_evidence"], "ACCEPTED_HEALTH_CONTRACT")
        foreign = self.stop_with(pid=7121, alive=lambda pid: True, stop_timeout=1.0,
                                 health=lambda h, p: dict(FOREIGN))
        self.assertFalse(foreign["command_identity_verified"])
        self.assertEqual(foreign["command_identity_evidence"], "FOREIGN_HTTP_RESPONDER")

    def test_x23c_command_identity_never_authorizes_anything(self):
        """It is diagnostic. An unreachable health probe must not change which process is signalled
        or whether the stop succeeds."""
        alive = {"v": True}
        res = self.stop_with(pid=7122, alive=lambda pid: alive["v"],
                             health=lambda h, p: dict(UNHEALTHY),
                             terminate=lambda pid, hard=False: (self.terminated.append((pid, hard)),
                                                                alive.update(v=False), True)[-1])
        self.assertEqual(res["readiness"], L.LAUNCHER_STOPPED)
        self.assertFalse(res["command_identity_verified"])
        self.assertEqual({pid for pid, _ in self.terminated}, {7122})


class TestStopNeverTouchesAnUnrelatedProcess(RealProcessBase):
    """Requirements 12 + 13, against REAL processes rather than seams."""

    def launcher_for(self, **kw):
        kw.setdefault("root", ROOT)
        kw.setdefault("workspace", self.ws)
        kw.setdefault("health", lambda h, p: dict(UNHEALTHY))
        kw.setdefault("port_probe", lambda h, p: False)
        kw.setdefault("browser", lambda url: True)
        kw.setdefault("stop_timeout", 2.0)
        return L.Launcher(**kw)

    def test_x30_start_token_mismatch_refuses_to_terminate_a_real_process(self):
        """12 + 13: a real, live, unrelated process whose PID is in the record is NEVER signalled."""
        proc = self.spawn_child()
        self.ws.write_pid({"pid": proc.pid, "process_start_token": "tok-FROM-A-DIFFERENT-BOOT",
                           "host": "127.0.0.1", "port": 8780})
        res = self.launcher_for().stop()
        self.assertEqual(res["readiness"], L.LAUNCHER_STOP_REFUSED)
        self.assertEqual(res["error_code"], "PID_REUSED_BY_ANOTHER_PROCESS")
        self.assertFalse(res["signalled"])
        # the real process is untouched
        self.assertIsNone(proc.poll(), "an unrelated process was terminated")
        self.assertTrue(L.process_alive(proc.pid))

    def test_x31_unproven_identity_refuses_to_terminate_a_real_process(self):
        proc = self.spawn_child()
        self.ws.write_pid({"pid": proc.pid, "process_start_token": "tok-x",
                           "host": "127.0.0.1", "port": 8780})
        res = self.launcher_for(start_token=lambda pid: None).stop()
        self.assertEqual(res["readiness"], L.LAUNCHER_STOP_REFUSED)
        self.assertEqual(res["error_code"], "PROCESS_IDENTITY_UNPROVEN")
        self.assertFalse(res["signalled"])
        self.assertIsNone(proc.poll())

    @WINDOWS_ONLY
    def test_x32_only_the_recorded_process_is_stopped_end_to_end(self):
        """The full real stop path: the recorded child stops, a bystander child does not."""
        target = self.spawn_child()
        bystander = self.spawn_child()
        self.ws.write_pid({"pid": target.pid,
                           "process_start_token": L.process_start_token(target.pid),
                           "host": "127.0.0.1", "port": 8780})
        res = self.launcher_for(stop_timeout=15.0).stop()

        self.assertEqual(res["readiness"], L.LAUNCHER_STOPPED, res.get("error_code"))
        self.assertEqual(res["exit_state"], L.EXIT_STATE_EXITED)
        self.assertEqual(res["owner_message"], "The toolkit stopped safely.")
        self.assertEqual(res["exit_verification"]["source"], "windows_process_handle")
        self.assertEqual(res["exit_verification"]["wait_name"], "WAIT_OBJECT_0")
        self.assertFalse(res["exit_verification"]["still_active"])
        self.assertTrue(res["runtime_state_cleared"])
        self.assertIsNone(self.ws.read_pid())
        target.wait(timeout=15)
        self.assertIsNotNone(target.poll())
        # 13) the bystander is untouched
        self.assertIsNone(bystander.poll(), "an unrelated process was stopped")

    @WINDOWS_ONLY
    def test_x33_real_stop_is_fast_and_does_not_burn_the_whole_budget(self):
        """The owner's defect, end to end: a real terminated console used to hold the launcher for
        the full 15-second budget and then report CONSOLE_DID_NOT_STOP."""
        target = self.spawn_child()
        self.ws.write_pid({"pid": target.pid,
                           "process_start_token": L.process_start_token(target.pid),
                           "host": "127.0.0.1", "port": 8780})
        res = self.launcher_for(stop_timeout=15.0).stop()
        self.assertEqual(res["readiness"], L.LAUNCHER_STOPPED)
        self.assertLess(res["stop_seconds"], 12.0, res)
        self.assertNotEqual(res.get("error_code"), "CONSOLE_DID_NOT_STOP")


class TestStopProcessIdentityRace(RealProcessBase):
    """The PID identity race, against REAL Windows processes.

    The accepted implementation checked the recorded start token through a TRANSIENT handle, then
    spent up to HEALTH_REQUEST_TIMEOUT (3.0 s) probing health with the PID unpinned, and only then
    opened the verifier handle. Hard escalation opened a further handle by raw PID and terminated
    through it without ever re-reading identity. An independent audit used that interval to have an
    unrelated process terminated.

    These tests prove the remediation on real process objects: the pinned handle is opened first,
    identity is read back through that exact handle, the termination handle is validated separately,
    and an unrelated replacement process survives untouched."""

    def launcher_for(self, **kw):
        kw.setdefault("root", ROOT)
        kw.setdefault("workspace", self.ws)
        kw.setdefault("health", lambda h, p: dict(UNHEALTHY))
        kw.setdefault("port_probe", lambda h, p: False)
        kw.setdefault("browser", lambda url: True)
        kw.setdefault("stop_timeout", 2.0)
        return L.Launcher(**kw)

    @WINDOWS_ONLY
    def test_x50_identity_is_read_through_the_exact_pinned_handle(self):
        """The verifier answers identity from the handle it holds, not from the PID number."""
        proc = self.spawn_child()
        v = L.open_exit_verifier(proc.pid)
        self.addCleanup(v.close)
        self.assertTrue(v.usable, v.open_error)
        self.assertIsNotNone(v.start_token)
        self.assertIsNone(v.token_error)
        # The handle-derived token and the raw-PID token agree for a process that has NOT been
        # replaced -- which is what makes them comparable at all.
        self.assertEqual(v.start_token, L.process_start_token(proc.pid))
        self.assertTrue(v.start_token.startswith("win-create-"), v.start_token)
        self.assertTrue(v.identity(v.start_token)["matches"])
        self.assertTrue(v.identity(v.start_token)["handle_token_read"])
        self.assertFalse(v.identity("win-create-1")["matches"])
        self.assertFalse(v.identity(None)["matches"])

    @WINDOWS_ONLY
    def test_x51_termination_handle_refuses_a_mismatched_token_and_kills_nothing(self):
        """Requirement 6, at the API boundary: a PROCESS_TERMINATE handle whose own creation token
        does not match is never terminated through. The real process must survive."""
        proc = self.spawn_child()
        res = L.terminate_process_result(proc.pid, hard=True, expect_token="win-create-1")
        self.assertFalse(res["ok"], res)
        self.assertEqual(res["error"], "TERMINATION_IDENTITY_MISMATCH")
        self.assertTrue(res["identity_checked"])
        self.assertFalse(res["identity_verified"])
        time.sleep(0.4)
        self.assertIsNone(proc.poll(), "a process with a mismatched token was terminated")
        self.assertTrue(L.process_alive(proc.pid))

    @WINDOWS_ONLY
    def test_x52_termination_handle_accepts_only_the_matching_token(self):
        """The same gate must not block a legitimate stop: the matching token terminates."""
        proc = self.spawn_child()
        token = L.process_start_token(proc.pid)
        res = L.terminate_process_result(proc.pid, hard=True, expect_token=token)
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["identity_checked"])
        self.assertTrue(res["identity_verified"])
        self.assertIsNone(res["error"])
        proc.wait(timeout=15)
        self.assertIsNotNone(proc.poll())

    @WINDOWS_ONLY
    def test_x53_an_unrelated_replacement_process_survives_the_whole_stop(self):
        """Requirement 9 -- the auditor's scenario, end to end on the REAL stop path.

        The recorded PID is live, but the process holding it is NOT the recorded one: its creation
        token differs. That is exactly what a stop sees after Windows has handed the number to
        something else. The replacement must be left completely alone."""
        donor = self.spawn_child()                    # the console that "exited"
        recorded_token = L.process_start_token(donor.pid)
        self.assertIsNotNone(recorded_token)
        L.terminate_process_result(donor.pid, hard=True, expect_token=recorded_token)
        donor.wait(timeout=15)

        replacement = self.spawn_child()              # the unrelated program now holding the PID
        self.assertNotEqual(L.process_start_token(replacement.pid), recorded_token)
        self.ws.write_pid({"pid": replacement.pid, "process_start_token": recorded_token,
                           "host": "127.0.0.1", "port": 8780})

        res = self.launcher_for(stop_timeout=15.0).stop()

        self.assertEqual(res["readiness"], L.LAUNCHER_STOP_REFUSED)
        self.assertEqual(res["error_code"], "PID_REUSED_BY_ANOTHER_PROCESS")
        self.assertFalse(res["signalled"])
        self.assertFalse(res["identity_verified"])
        # 4) no CTRL_BREAK_EVENT, no TerminateProcess -- nothing was asked of the OS at all.
        self.assertEqual(res["terminate_requests"], [])
        # 4) no runtime state cleared.
        self.assertFalse(res["runtime_state_cleared"])
        self.assertFalse(res["stale_pid_cleared"])
        self.assertIsNotNone(self.ws.read_pid())
        # the decision was made through the pinned handle, not through a raw-PID read
        ident = res["process_identity"]
        self.assertTrue(ident["handle_pinned"])
        self.assertTrue(ident["handle_token_read"])
        self.assertFalse(ident["handle_token_matches_recorded"])
        self.assertIsNone(ident["authorized_by"])
        # THE assertion: the unrelated process is still running.
        time.sleep(0.5)
        self.assertIsNone(replacement.poll(), "an unrelated process was terminated")
        self.assertTrue(L.process_alive(replacement.pid))

    @WINDOWS_ONLY
    def test_x54_a_matching_real_process_is_still_stopped_and_all_tokens_agree(self):
        """The gate must not break the normal path: recorded, pinned-handle and termination-handle
        tokens all agree, so the recorded console stops and a bystander is untouched."""
        target = self.spawn_child()
        bystander = self.spawn_child()
        recorded = L.process_start_token(target.pid)
        self.ws.write_pid({"pid": target.pid, "process_start_token": recorded,
                           "host": "127.0.0.1", "port": 8780})
        res = self.launcher_for(stop_timeout=15.0).stop()

        self.assertEqual(res["readiness"], L.LAUNCHER_STOPPED, res.get("error_code"))
        self.assertEqual(res["exit_state"], L.EXIT_STATE_EXITED)
        self.assertEqual(res["process_identity"]["authorized_by"], "PINNED_HANDLE_TOKEN")
        self.assertTrue(res["process_identity"]["handle_token_matches_recorded"])
        hard = [r for r in res["terminate_requests"] if r["hard"]]
        self.assertTrue(hard, res["terminate_requests"])
        for r in hard:                                # the termination handle validated its own token
            self.assertTrue(r["identity_checked"], r)
            self.assertTrue(r["identity_verified"], r)
        target.wait(timeout=15)
        self.assertIsNotNone(target.poll())
        self.assertIsNone(bystander.poll(), "an unrelated process was stopped")

    def test_x55_no_handle_on_real_windows_fails_closed(self):
        """Requirement 4: if the pinned handle cannot be obtained at all, there is nothing left but
        a raw-PID read -- the very read this hotfix removes. It must refuse, not fall back."""
        lau = self.launcher_for()
        lau._handle_identity_required = True
        code, token, ev = lau._pinned_identity(4321, "tok-recorded", None)
        self.assertEqual(code, "PROCESS_IDENTITY_UNPROVEN")
        self.assertIsNone(token)
        self.assertFalse(ev["handle_pinned"])
        self.assertIsNone(ev["authorized_by"])

    def test_x56_unreadable_handle_token_fails_closed(self):
        """A pinned handle whose GetProcessTimes call failed proves nothing and authorizes nothing."""
        class Unreadable:
            usable = True
            start_token = None
            token_error = "GET_PROCESS_TIMES_FAILED_5"

            def close(self):
                pass
        lau = self.launcher_for()
        code, token, ev = lau._pinned_identity(4322, "tok-recorded", Unreadable())
        self.assertEqual(code, "PROCESS_IDENTITY_UNPROVEN")
        self.assertIsNone(token)
        self.assertFalse(ev["handle_token_read"])
        self.assertEqual(ev["api_error"], "GET_PROCESS_TIMES_FAILED_5")
        self.assertIsNone(ev["authorized_by"])


class TestStopExitVerificationBoundary(LauncherBase):
    """Requirements 16 + 17: nothing else moved."""

    def test_x40_start_and_open_are_unchanged(self):
        started = self.launcher(health=self._health_after(2), port_probe=lambda h, p: False).start()
        self.assertEqual(started["readiness"], L.LAUNCHER_READY)
        self.assertTrue(started["browser_opened"])
        self.assertNotIn("exit_state", started)
        self.assertNotIn("stop_state", started)
        opened = self.launcher(health=lambda h, p: dict(HEALTHY)).open()
        self.assertEqual(opened["readiness"], L.LAUNCHER_ALREADY_RUNNING)
        self.assertFalse(opened["started_a_server"])
        self.assertEqual(opened["owner_message"],
                         "The toolkit was already running, so a second copy was not started.")
        not_running = self.launcher(health=lambda h, p: dict(UNHEALTHY)).open()
        self.assertEqual(not_running["readiness"], L.LAUNCHER_NOT_RUNNING)
        self.assertEqual(not_running["owner_message"],
                         "The toolkit is not running yet. Run Start-AMZ-Toolkit first.")

    def _health_after(self, n):
        box = {"n": 0}

        def health(h, p):
            box["n"] += 1
            return dict(HEALTHY) if box["n"] >= n else dict(UNHEALTHY)
        return health

    def test_x41_validate_only_still_has_zero_side_effects(self):
        res = L.validate_only(root=ROOT)
        for k in ("files_written", "directories_created", "processes_spawned", "processes_signalled",
                  "browsers_opened", "network_requests", "ports_probed", "locks_taken"):
            self.assertEqual(res[k], 0, k)

    def test_x42_seller_central_counters_remain_zero_on_every_stop_path(self):
        alive = {"v": True}
        outcomes = [
            self.stop_result(pid=7200, alive=lambda pid: alive["v"],
                             terminate=lambda pid, hard=False: (alive.update(v=False), True)[-1]),
            self.stop_result(pid=7201, alive=lambda pid: True, stop_timeout=1.0),
            self.stop_result(pid=7202, alive=lambda pid: True, start_token=lambda pid: None),
        ]
        for res in outcomes:
            self.assertEqual(res["seller_central_counters"], dict(L.SELLER_CENTRAL_COUNTERS))
            self.assertTrue(all(v == 0 for v in res["seller_central_counters"].values()))
            for flag in ("connects_to_seller_central", "uses_a_seller_api_or_advertising_api",
                         "downloads_a_seller_report", "drives_a_seller_browser",
                         "kills_a_process_it_did_not_start", "kills_every_python_process"):
                self.assertTrue(res["launcher_never"][flag], flag)

    def stop_result(self, pid, **kw):
        self.ws.clear_pid()
        self.ws.write_pid({"pid": pid, "process_start_token": f"tok-{pid}"})
        kw.setdefault("health", lambda h, p: dict(HEALTHY))
        return self.launcher(**kw).stop()

    def test_x43_no_image_name_kill_and_no_broad_python_termination(self):
        src = read(LAUNCHER_PATH)
        for banned in ("taskkill", "/IM ", "pkill", "killall", "psutil", "process_iter",
                       "CreateToolhelp32Snapshot", "Process32", "WMIC", "Get-Process",
                       "os.system", "shell=True"):
            self.assertNotIn(banned, src, banned)
        self.assertEqual(src.count("TerminateProcess("), 1)

    def test_x44_stop_path_makes_no_amazon_request(self):
        src = read(LAUNCHER_PATH)
        for banned in ("sellercentral", "seller-central", "amazon.com", "sp-api", "advertising-api",
                       "webservices.amazon"):
            self.assertNotIn(banned, src.lower(), banned)

    def test_x46_the_hard_termination_is_handed_the_pinned_token(self):
        """Requirement 7, as a source contract: the token the pinned handle proved is the same token
        the termination handle must re-prove. Three tokens, one value."""
        src = read(LAUNCHER_PATH)
        stop = src.split("    def stop(self):", 1)[1].split("\n    def _pinned_identity", 1)[0]
        self.assertIn("verifier = self._exit_verifier(pid)", stop)
        self.assertIn("self._pinned_identity(pid, recorded, verifier)", stop)
        self.assertIn("hard=True, expect_token=pinned_token", stop)
        # Scoped to the part of stop() that HAS a recorded PID. The earlier probe belongs to the
        # no-record branch, where there is no PID to pin and nothing can be signalled.
        owned = stop.split("recorded = rec.get(", 1)[1]
        self.assertLess(owned.index("verifier = self._exit_verifier(pid)"),
                        owned.index("health = self._health("))
        self.assertLess(owned.index("self._pinned_identity("), owned.index("health = self._health("))
        # and the termination path re-reads identity through its own handle before killing
        term = src.split("def terminate_process_result(", 1)[1].split("\ndef terminate_process(", 1)[0]
        self.assertIn("_win_handle_start_token(ctypes, wintypes, k32, handle)", term)
        self.assertLess(term.index("_win_handle_start_token"), term.index("k32.TerminateProcess("))
        self.assertIn("TERMINATION_IDENTITY_MISMATCH", term)

    def test_x47_no_unreachable_owner_copy_for_a_stopped_launcher(self):
        """The LAUNCHER_STOPPED mapping entry is the same sentence stop actually prints, so there is
        no second, unreachable success string to drift out of date."""
        self.assertEqual(L._OWNER_MESSAGES[L.LAUNCHER_STOPPED], L.STOP_SUCCESS_MESSAGE)
        self.assertNotIn("The toolkit has stopped.", read(LAUNCHER_PATH))
        self.assertEqual(L._owner_message(L.LAUNCHER_STOPPED, "", "", phase="stop",
                                          stop_state=L.STOP_STATE_EXITED), L.STOP_SUCCESS_MESSAGE)
        self.assertEqual(L._owner_message(L.LAUNCHER_STOPPED, "", "", phase="stop",
                                          stop_state=L.STOP_STATE_EXITED_STALE_STATE),
                         L.STOP_EXITED_STALE_STATE_MESSAGE)

    def test_x48_owner_capitalization_is_one_form_across_a_stop_session(self):
        """D5: a single Stop session can print a new sentence and a baseline one back to back."""
        src = read(LAUNCHER_PATH)
        self.assertNotIn("The Toolkit", src)
        session = [L.STOP_SUCCESS_MESSAGE, L.STOP_EXITED_STALE_STATE_MESSAGE,
                   L.STOP_STILL_RUNNING_MESSAGE, L.STOP_EXIT_UNPROVEN_MESSAGE,
                   L.STOP_FAILED_MESSAGE, L._OWNER_MESSAGES[L.LAUNCHER_ALREADY_STOPPED]]
        for msg in session:
            self.assertTrue(msg.startswith("The toolkit "), msg)

    def test_x45_exit_verification_record_is_bounded_and_secret_free(self):
        res = self.stop_result(pid=7203, alive=lambda pid: True, stop_timeout=1.0)
        blob = json.dumps(res)
        self.assertLess(len(json.dumps(res["exit_verification"])), 600)
        self.assertLess(len(json.dumps(res["terminate_requests"])), 600)
        for marker in ("C:\\\\", "Authorization", "csrf", "cookie"):
            self.assertNotIn(marker, blob)


# ================================================================ 7d) IDENTITY TOKEN VALIDITY
# Phase 7.14 null-start-token hotfix.
#
# The accepted stop-exit-verification hotfix added a three-token identity gate, but every consumer
# of the recorded token treated a FALSY token as "skip the check" rather than "cannot verify":
#
#   _pinned_identity : `if not recorded: return None, ...`      -> AUTHORIZED a termination
#   _clear_stale_pid : `if rec.get(...) and token != rec[...]`  -> the PID-reuse branch never ran
#   status           : `if owned and rec.get(...)`              -> launcher_owned: true, unverified
#
# And the record those three read from is written by START, which reads the token by RAW PID from a
# process it already owns a handle to, persists whatever comes back -- including None -- and then
# reports SESSION7_14_LAUNCHER_READY. So the null token is not an inherited condition: Start
# manufactures it, and the identity gate is bypassed from the moment the record is written.
#
# The invariant these tests encode: a missing, null, blank, malformed or unreadable token NEVER
# authorizes a signal, a termination, an ownership claim, or a successful Start.
class TestIdentityTokenValidator(unittest.TestCase):
    """One rule, four sites. Presence and shape only -- never a format gate, because the accepted
    test seams legitimately produce tokens like `tok-4242` that no real platform would emit."""

    def test_x60_falsy_and_malformed_tokens_are_never_valid(self):
        for bad in (None, "", "   ", "\t\n", 0, 1, 1.0, True, False, [], {}, (), b"win-create-1"):
            self.assertFalse(L.valid_identity_token(bad), repr(bad))

    def test_x60b_real_and_seam_tokens_are_valid(self):
        for good in ("win-create-133742", "posix-start-9981", "tok-4242", "x"):
            self.assertTrue(L.valid_identity_token(good), repr(good))

    def test_x60c_every_identity_site_uses_the_one_validator(self):
        """A second, hand-rolled truthiness test at any of the four sites is the defect returning."""
        src = read(LAUNCHER_PATH)
        for site in ("_pinned_identity", "_clear_stale_pid"):
            body = src.split(f"def {site}", 1)[1].split("\n    def ", 1)[0]
            self.assertIn("valid_identity_token", body, site)
        self.assertNotIn("if not recorded:", src)
        self.assertNotIn('if owned and rec.get("process_start_token")', src)


class TestNullTokenNeverAuthorizes(LauncherBase):
    """Site 1 -- `_pinned_identity`. The audit's finding: a null recorded token authorized a kill."""

    def stop_with_recorded(self, recorded, pid=7300, **kw):
        self.ws.clear_pid()
        self.ws.write_pid({"pid": pid, "process_start_token": recorded,
                           "host": "127.0.0.1", "port": 8780})
        kw.setdefault("health", lambda h, p: dict(HEALTHY))
        kw.setdefault("alive", lambda p: True)
        kw.setdefault("stop_timeout", 1.0)
        return self.launcher(**kw).stop()

    def test_x61_null_recorded_token_refuses_and_signals_nothing(self):
        res = self.stop_with_recorded(None, pid=7301)
        self.assertEqual(res["readiness"], L.LAUNCHER_STOP_REFUSED)
        self.assertEqual(res["error_code"], "PROCESS_IDENTITY_UNPROVEN")
        self.assertFalse(res["signalled"])
        self.assertFalse(res["identity_verified"])
        self.assertEqual(res["terminate_requests"], [])
        self.assertEqual(self.terminated, [], "a null token authorized a termination")

    def test_x61b_every_malformed_recorded_token_refuses(self):
        for i, bad in enumerate((None, "", "   ", 0, 1234, True, [], {})):
            res = self.stop_with_recorded(bad, pid=7310 + i)
            self.assertEqual(res["readiness"], L.LAUNCHER_STOP_REFUSED, repr(bad))
            self.assertEqual(res["error_code"], "PROCESS_IDENTITY_UNPROVEN", repr(bad))
            self.assertEqual(res["terminate_requests"], [], repr(bad))
        self.assertEqual(self.terminated, [])

    def test_x61c_a_refusal_clears_no_state_and_probes_no_health(self):
        """A refusal must not destroy the record the next attempt needs, and must not spend the
        3.0 s health timeout with an unverifiable PID in hand."""
        probes = []
        res = self.stop_with_recorded(None, pid=7320,
                                      health=lambda h, p: (probes.append((h, p)), dict(HEALTHY))[-1])
        self.assertFalse(res["runtime_state_cleared"])
        self.assertFalse(res["stale_pid_cleared"])
        self.assertIsNotNone(self.ws.read_pid())
        self.assertEqual(probes, [], "health was probed on an unverifiable identity")

    def test_x61d_evidence_records_why_it_refused(self):
        res = self.stop_with_recorded(None, pid=7330)
        ident = res["process_identity"]
        self.assertFalse(ident["recorded_token_valid"])
        self.assertIsNone(ident["authorized_by"])
        self.assertNotIn("NO_RECORDED_TOKEN", json.dumps(ident))

    def test_x61e_owner_copy_says_what_to_do_next(self):
        res = self.stop_with_recorded(None, pid=7340)
        msg = res["owner_message"]
        self.assertTrue(msg.startswith("The toolkit "), msg)
        self.assertIn("Nothing on this computer was stopped.", msg)
        self.assertIn("Task Manager", msg)
        for code in ("PROCESS_IDENTITY_UNPROVEN", "SESSION7_14_", "process_start_token"):
            self.assertNotIn(code, msg)

    def test_x61f_a_valid_matching_token_still_stops(self):
        """The gate must not block the legitimate path it exists to protect."""
        alive = {"v": True}
        res = self.stop_with_recorded("tok-7350", pid=7350, alive=lambda p: alive["v"],
                                      start_token=lambda p: "tok-7350",
                                      terminate=lambda pid, hard=False: (alive.update(v=False),
                                                                         True)[-1])
        self.assertEqual(res["readiness"], L.LAUNCHER_STOPPED)
        self.assertTrue(res["identity_verified"])


class TestUnverifiableRecordSweep(LauncherBase):
    """Site 2 -- `_clear_stale_pid`. Unreadable is not reused, and unverifiable is not owned."""

    def start_it(self, **kw):
        kw.setdefault("health", lambda h, p: dict(HEALTHY))
        kw.setdefault("spawn", lambda cmd, cwd: FakeProc(pid=99))
        return self.launcher(**kw).start()

    def test_x62_unverifiable_record_for_a_live_pid_is_cleared_not_kept(self):
        """A record whose own token is null can never authorize anything, so keeping it only leaves
        the owner with a PID record no operation will ever act on."""
        self.ws.write_pid({"pid": 4242, "process_start_token": None, "host": "127.0.0.1",
                           "port": 8780})
        res = self.start_it(alive=lambda pid: True)
        self.assertTrue(res["stale_pid_cleared"])
        self.assertEqual(self.ws.read_pid()["pid"], 99)
        self.assertEqual(self.terminated, [], "the stale sweep signalled a process")

    def test_x62b_unreadable_live_token_is_not_reported_as_pid_reuse(self):
        """The baseline cleared the record whenever the live read failed, because `None != recorded`
        is true. That destroys the record on exactly the reading that proves nothing."""
        self.ws.write_pid({"pid": 4242, "process_start_token": "tok-4242", "host": "127.0.0.1",
                           "port": 8780})
        res = self.start_it(alive=lambda pid: True, start_token=lambda pid: None)
        self.assertFalse(res["stale_pid_cleared"])
        self.assertEqual(self.terminated, [])

    def test_x62c_a_genuinely_reused_pid_is_still_cleared(self):
        self.ws.write_pid({"pid": 4242, "process_start_token": "tok-OLD", "host": "127.0.0.1",
                           "port": 8780})
        res = self.start_it(alive=lambda pid: True, start_token=lambda pid: "tok-NEW")
        self.assertTrue(res["stale_pid_cleared"])


class TestStatusOwnershipIsVerified(LauncherBase):
    """Site 3 -- `status`. After fixing only site 1, Stop refuses while status still calls the
    record owned: one session, two contradictory answers about the same PID."""

    def status_for(self, recorded, **kw):
        self.ws.clear_pid()
        self.ws.write_pid({"pid": 7400, "process_start_token": recorded,
                           "host": "127.0.0.1", "port": 8780})
        kw.setdefault("health", lambda h, p: dict(HEALTHY))
        kw.setdefault("alive", lambda p: True)
        return self.launcher(**kw).status()

    def test_x63_null_recorded_token_is_never_reported_as_owned(self):
        for bad in (None, "", "   ", 0, 1234, []):
            res = self.status_for(bad)
            self.assertFalse(res["launcher_owned"], repr(bad))
            self.assertFalse(res["identity_verified"], repr(bad))

    def test_x63b_unreadable_live_token_is_never_reported_as_owned(self):
        res = self.status_for("tok-7400", start_token=lambda pid: None)
        self.assertFalse(res["launcher_owned"])

    def test_x63c_a_matching_pair_is_reported_as_owned(self):
        res = self.status_for("tok-7400", start_token=lambda pid: "tok-7400")
        self.assertTrue(res["launcher_owned"])
        self.assertTrue(res["identity_verified"])

    def test_x63d_status_and_stop_agree_about_the_same_record(self):
        """The self-contradiction this site exists to prevent, asserted directly."""
        self.ws.clear_pid()
        self.ws.write_pid({"pid": 7401, "process_start_token": None,
                           "host": "127.0.0.1", "port": 8780})
        st = self.launcher(health=lambda h, p: dict(HEALTHY), alive=lambda p: True).status()
        sp = self.launcher(health=lambda h, p: dict(HEALTHY), alive=lambda p: True,
                           stop_timeout=1.0).stop()
        self.assertFalse(st["launcher_owned"])
        self.assertEqual(sp["readiness"], L.LAUNCHER_STOP_REFUSED)
        self.assertFalse(sp["identity_verified"])


class TestStartProducesAVerifiedToken(LauncherBase):
    """Site 0 -- the source. Start must not persist, nor report success on, an unverifiable child."""

    def start_it(self, **kw):
        kw.setdefault("health", lambda h, p: dict(HEALTHY))
        return self.launcher(**kw).start()

    def test_x64_unreadable_child_token_never_reports_ready(self):
        proc = FakeProc(pid=99)
        res = self.start_it(spawn=lambda cmd, cwd: proc, start_token=lambda pid: None)
        self.assertNotEqual(res["readiness"], L.LAUNCHER_READY)
        self.assertEqual(res["readiness"], L.LAUNCHER_FAILED)
        self.assertEqual(res["error_code"], "CONSOLE_IDENTITY_UNREADABLE")

    def test_x64b_unreadable_child_token_persists_no_runtime_record(self):
        res = self.start_it(spawn=lambda cmd, cwd: FakeProc(pid=99), start_token=lambda pid: None)
        self.assertIsNone(self.ws.read_pid(), "a null-token record was persisted")
        self.assertFalse(res.get("browser_opened"))
        self.assertEqual(self.browser_calls, [], "a browser was opened for an unmanageable console")

    def test_x64c_the_unverifiable_child_is_cleaned_up_through_the_owned_object(self):
        """Cleanup goes through the Popen object we already hold -- never by re-resolving the PID,
        which is the exact unpinned read this whole hotfix exists to remove."""
        proc = FakeProc(pid=99)
        self.start_it(spawn=lambda cmd, cwd: proc, start_token=lambda pid: None)
        self.assertEqual(proc.kills, 1)
        self.assertGreaterEqual(proc.waits, 1)
        self.assertEqual(self.terminated, [], "cleanup went through the PID-based terminate seam")

    def test_x64d_a_transient_read_failure_is_retried_before_failing(self):
        calls = {"n": 0}

        def flaky(pid):
            calls["n"] += 1
            return None if calls["n"] < 2 else "tok-99"

        proc = FakeProc(pid=99)
        res = self.start_it(spawn=lambda cmd, cwd: proc, start_token=flaky)
        self.assertEqual(res["readiness"], L.LAUNCHER_READY)
        self.assertEqual(proc.kills, 0)
        self.assertEqual(self.ws.read_pid()["process_start_token"], "tok-99")

    def test_x64e_retries_are_bounded(self):
        calls = {"n": 0}

        def never(pid):
            calls["n"] += 1
            return None

        self.start_it(spawn=lambda cmd, cwd: FakeProc(pid=99), start_token=never)
        self.assertEqual(calls["n"], L.START_TOKEN_READ_ATTEMPTS)
        self.assertLessEqual(L.START_TOKEN_READ_ATTEMPTS, 5)

    def test_x64f_owner_copy_for_an_unverifiable_start_is_truthful(self):
        res = self.start_it(spawn=lambda cmd, cwd: FakeProc(pid=99), start_token=lambda pid: None)
        msg = res["owner_message"]
        self.assertTrue(msg.startswith("The toolkit "), msg)
        self.assertIn("Start-AMZ-Toolkit", msg)
        self.assertNotIn("is running", msg)
        for code in ("CONSOLE_IDENTITY_UNREADABLE", "SESSION7_14_"):
            self.assertNotIn(code, msg)

    def test_x64g_a_readable_token_records_its_source(self):
        res = self.start_it(spawn=lambda cmd, cwd: FakeProc(pid=99))
        self.assertEqual(res["readiness"], L.LAUNCHER_READY)
        rec = self.ws.read_pid()
        self.assertTrue(L.valid_identity_token(rec["process_start_token"]))
        self.assertEqual(rec["identity_source"], "process_start_token")


class TestStartTokenComesFromTheOwnedHandle(RealProcessBase):
    """Start held a `Popen` that OWNS the child, and read the child's identity by raw PID anyway.

    On Windows the Popen handle is the only read that cannot describe a different process. On POSIX
    the child is unreaped for as long as the Popen lives, so its PID cannot be recycled and the
    /proc read is already pinned -- which is why the fallback is correct there and not here."""

    def test_x65_popen_token_matches_the_pid_token_for_a_real_child(self):
        proc = self.spawn_child()
        token, err = L.process_start_token_from_popen(proc)
        if os.name != "nt":
            self.assertIsNone(token)
            self.assertEqual(err, "NOT_WINDOWS")
            return
        self.assertIsNone(err, err)
        self.assertTrue(L.valid_identity_token(token), token)
        self.assertEqual(token, L.process_start_token(proc.pid))
        self.assertTrue(token.startswith("win-create-"), token)

    @WINDOWS_ONLY
    def test_x65b_popen_token_survives_the_child_exiting(self):
        """The handle keeps answering about the process it owns, which is the whole point."""
        proc = self.spawn_child(seconds=1)
        token_before, _ = L.process_start_token_from_popen(proc)
        proc.wait(timeout=20)
        token_after, err = L.process_start_token_from_popen(proc)
        self.assertIsNone(err, err)
        self.assertEqual(token_after, token_before)

    def test_x65c_a_handleless_process_object_falls_back_rather_than_crashing(self):
        token, err = L.process_start_token_from_popen(FakeProc(pid=1))
        self.assertIsNone(token)
        self.assertIn(err, ("NOT_WINDOWS", "NO_POPEN_HANDLE"))

    def test_x65d_no_process_object_is_never_a_token(self):
        for bad in (None, 0, "proc"):
            token, err = L.process_start_token_from_popen(bad)
            self.assertIsNone(token)


class TestNullTokenNeverReachesARealProcess(RealProcessBase):
    """The bystander proof, on REAL Windows processes rather than seams.

    A null recorded token must not reach the OS at all: no console break, no TerminateProcess, and a
    live unrelated process holding that PID must be running when the stop returns."""

    def launcher_for(self, **kw):
        kw.setdefault("root", ROOT)
        kw.setdefault("workspace", self.ws)
        kw.setdefault("health", lambda h, p: dict(UNHEALTHY))
        kw.setdefault("port_probe", lambda h, p: False)
        kw.setdefault("browser", lambda url: True)
        kw.setdefault("stop_timeout", 2.0)
        return L.Launcher(**kw)

    def test_x66_a_real_bystander_survives_a_null_token_stop(self):
        bystander = self.spawn_child()
        self.ws.write_pid({"pid": bystander.pid, "process_start_token": None,
                           "host": "127.0.0.1", "port": 8780})
        launcher = self.launcher_for()
        res = launcher.stop()

        self.assertEqual(res["readiness"], L.LAUNCHER_STOP_REFUSED)
        self.assertEqual(res["error_code"], "PROCESS_IDENTITY_UNPROVEN")
        self.assertFalse(res["signalled"])
        # Nothing was asked of the OS -- provable from the record, not inferred from an absence.
        self.assertEqual(res["terminate_requests"], [])
        self.assertEqual(launcher.terminate_requests, [])
        time.sleep(0.4)
        self.assertIsNone(bystander.poll(), "a null recorded token reached a real process")
        self.assertTrue(L.process_alive(bystander.pid))
        # The record survives the refusal.
        self.assertIsNotNone(self.ws.read_pid())

    def test_x66b_blank_and_malformed_tokens_reach_no_real_process_either(self):
        for bad in ("", "   ", 1234):
            bystander = self.spawn_child()
            self.ws.clear_pid()
            self.ws.write_pid({"pid": bystander.pid, "process_start_token": bad,
                               "host": "127.0.0.1", "port": 8780})
            res = self.launcher_for().stop()
            self.assertEqual(res["error_code"], "PROCESS_IDENTITY_UNPROVEN", repr(bad))
            self.assertEqual(res["terminate_requests"], [], repr(bad))
            time.sleep(0.2)
            self.assertIsNone(bystander.poll(), repr(bad))

    @WINDOWS_ONLY
    def test_x66c_the_matching_real_process_is_still_stopped(self):
        """The invariant is fail-closed, not fail-always: a real recorded child still stops."""
        target = self.spawn_child()
        self.ws.write_pid({"pid": target.pid,
                           "process_start_token": L.process_start_token(target.pid),
                           "host": "127.0.0.1", "port": 8780})
        res = self.launcher_for(stop_timeout=15.0).stop()
        self.assertEqual(res["readiness"], L.LAUNCHER_STOPPED, res.get("error_code"))
        self.assertEqual(res["exit_state"], L.EXIT_STATE_EXITED)
        target.wait(timeout=15)
        self.assertIsNotNone(target.poll())


if __name__ == "__main__":
    unittest.main()
