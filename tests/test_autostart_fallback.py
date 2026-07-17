#!/usr/bin/env python3
"""Session 5D.1 — no-admin autostart method model + Startup-folder fallback (Part A/B, G).

schtasks is mocked (creating a real task is destructive and, on this machine, denied)
so the Task-Scheduler branch is driven deterministically; the Startup-folder fallback
runs for real against an ISOLATED temp directory. Together these prove: auto prefers a
verified task, access-denied falls back to the current-user Startup folder (never
elevation), explicit modes do not silently switch, disable removes only toolkit-owned
methods, and no method ever uses SYSTEM / highest privilege / a browser / a secret.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core", "listing", "dashboard"):
    sys.path.insert(0, os.path.join(ROOT, d))
import windows_integration as WI      # noqa: E402

_SID = "S-1-5-21-100-200-300-1001"

CURRENT_USER_XML = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>{_SID}</UserId>
    <LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Actions><Exec><Command>pythonw.exe</Command><Arguments>-m amz_fbm start</Arguments></Exec></Actions>
</Task>"""


class _R:
    def __init__(self, success, stdout="", stderr="", exit_code=0):
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.diagnostic_summary = "stub"


class FakeSchtasks:
    """Deterministic schtasks/whoami stub; records commands and controls /Create."""

    def __init__(self, create_ok=True, create_stderr="", query_xml=CURRENT_USER_XML):
        self.create_ok = create_ok
        self.create_stderr = create_stderr
        self.query_xml = query_xml
        self.calls = []

    def run_subprocess(self, command, *, timeout_seconds, stage_name=None,
                       allowed_exit_codes=(0,), **kw):
        c = list(command)
        self.calls.append(c)
        if c and c[0] == "whoami":
            return _R(True, f'"HOST\\User","{_SID}"', "")
        if c and c[0] == "schtasks":
            if "/Query" in c:
                return _R(bool(self.query_xml), self.query_xml, "",
                          0 if self.query_xml else 1)
            if "/Create" in c:
                return _R(self.create_ok, "", self.create_stderr,
                          0 if self.create_ok else 1)
            if "/Delete" in c:
                return _R(True, "", "", 0)
        return _R(True, "", "")

    def create_cmd(self):
        for c in self.calls:
            if c[:2] == ["schtasks", "/Create"]:
                return c
        return None

    def created_task(self):
        return self.create_cmd() is not None


class AutostartFallback(unittest.TestCase):
    def setUp(self):
        WI._IDENTITY_CACHE.clear()
        self.startup = tempfile.mkdtemp(prefix="amz-autostart-")
        self.spaced = tempfile.mkdtemp(prefix="amz auto start ")   # path with spaces

    def tearDown(self):
        import shutil
        for d in (self.startup, self.spaced):
            shutil.rmtree(d, ignore_errors=True)

    def _patch(self, fake):
        p = mock.patch.object(WI.SR, "run_subprocess", fake.run_subprocess)
        p.start()
        self.addCleanup(p.stop)

    def _launcher(self, startup_dir=None):
        return WI._startup_launcher_path(startup_dir or self.startup)

    # 1 — auto prefers a verified Task Scheduler task; no fallback file written
    def test_01_auto_prefers_verified_task_scheduler(self):
        fake = FakeSchtasks(create_ok=True)
        self._patch(fake)
        res = WI.enable_autostart(method="auto", startup_dir=self.startup)
        self.assertTrue(res["ok"])
        self.assertEqual(res["method"], WI.AUTOSTART_TASK_SCHEDULER)
        self.assertFalse(os.path.exists(self._launcher()))     # no fallback created

    # 2 — access denied triggers the Startup-folder fallback (never elevation)
    def test_02_access_denied_falls_back_to_startup_folder(self):
        fake = FakeSchtasks(create_ok=False, create_stderr="ERROR: Access is denied.")
        self._patch(fake)
        res = WI.enable_autostart(method="auto", startup_dir=self.startup)
        self.assertTrue(res["ok"])
        self.assertEqual(res["method"], WI.AUTOSTART_STARTUP_FOLDER)
        self.assertEqual(res["task_scheduler"]["failure"], WI.TASK_SCHEDULER_ACCESS_DENIED)
        self.assertTrue(os.path.exists(self._launcher()))
        self.assertIn("warning", res)          # failure surfaced, not hidden

    # 3 — explicit task-scheduler does not silently fall back
    def test_03_explicit_task_scheduler_no_silent_fallback(self):
        fake = FakeSchtasks(create_ok=False, create_stderr="ERROR: Access is denied.")
        self._patch(fake)
        res = WI.enable_autostart(method="task-scheduler", startup_dir=self.startup)
        self.assertFalse(res["ok"])
        self.assertEqual(res["method"], WI.AUTOSTART_ENABLE_FAILED)
        self.assertFalse(os.path.exists(self._launcher()))

    # 4 — explicit startup-folder does not call Task Scheduler /Create
    def test_04_explicit_startup_folder_skips_task_scheduler(self):
        fake = FakeSchtasks(create_ok=True)
        self._patch(fake)
        res = WI.enable_autostart(method="startup-folder", startup_dir=self.startup)
        self.assertTrue(res["ok"])
        self.assertEqual(res["method"], WI.AUTOSTART_STARTUP_FOLDER)
        self.assertFalse(fake.created_task())

    # 5 / 6 — status identifies the actual active method
    def test_05_status_identifies_task_scheduler(self):
        self._patch(FakeSchtasks(query_xml=CURRENT_USER_XML))
        state = WI.autostart_state(startup_dir=self.startup)
        self.assertTrue(state["enabled"])
        self.assertEqual(state["method"], WI.AUTOSTART_TASK_SCHEDULER)

    def test_06_status_identifies_startup_folder(self):
        self._patch(FakeSchtasks(query_xml=""))          # no task installed
        WI.enable_startup_folder(startup_dir=self.startup)
        state = WI.autostart_state(startup_dir=self.startup)
        self.assertTrue(state["enabled"])
        self.assertEqual(state["method"], WI.AUTOSTART_STARTUP_FOLDER)
        self.assertFalse(state["requires_admin"])

    # 7 — disable removes both toolkit-owned methods
    def test_07_disable_removes_both_methods(self):
        self._patch(FakeSchtasks(create_ok=True, query_xml=""))   # /Delete ok; task gone after
        WI.enable_startup_folder(startup_dir=self.startup)
        res = WI.disable_autostart(startup_dir=self.startup)
        self.assertTrue(res["ok"])
        self.assertFalse(os.path.exists(self._launcher()))

    # 8 — disable preserves an unrelated Startup-folder entry
    def test_08_disable_preserves_unrelated_startup_entry(self):
        self._patch(FakeSchtasks(query_xml=""))
        WI.enable_startup_folder(startup_dir=self.startup)
        foreign = os.path.join(self.startup, "SomeOtherApp.cmd")
        with open(foreign, "w", encoding="utf-8") as f:
            f.write("echo not ours")
        WI.disable_autostart(startup_dir=self.startup)
        self.assertFalse(os.path.exists(self._launcher()))
        self.assertTrue(os.path.exists(foreign))          # unrelated entry kept

    # 9 — disable only deletes the toolkit's own task name (never unrelated tasks)
    def test_09_disable_only_toolkit_task(self):
        fake = FakeSchtasks(query_xml="")
        self._patch(fake)
        WI.disable_autostart(startup_dir=self.startup)
        for c in fake.calls:
            if c[:2] == ["schtasks", "/Delete"]:
                self.assertEqual(c[c.index("/TN") + 1], WI.TASK_NAME)

    # 10 / 11 — enable + disable idempotent (startup-folder branch)
    def test_10_enable_idempotent(self):
        self._patch(FakeSchtasks(create_ok=False, create_stderr="Access is denied."))
        a = WI.enable_autostart(method="auto", startup_dir=self.startup)
        b = WI.enable_autostart(method="auto", startup_dir=self.startup)
        self.assertTrue(a["ok"] and b["ok"])
        self.assertEqual(b["method"], WI.AUTOSTART_STARTUP_FOLDER)

    def test_11_disable_idempotent(self):
        self._patch(FakeSchtasks(query_xml=""))
        WI.enable_startup_folder(startup_dir=self.startup)
        self.assertTrue(WI.disable_autostart(startup_dir=self.startup)["ok"])
        self.assertTrue(WI.disable_autostart(startup_dir=self.startup)["ok"])

    # 12 / 13 / 14 — no elevation, no SYSTEM, no highest privilege anywhere
    def test_12_13_14_no_elevation_system_or_highest(self):
        fake = FakeSchtasks(create_ok=True)
        self._patch(fake)
        res = WI.enable_autostart(method="auto", startup_dir=self.startup)
        self.assertFalse(res["requires_admin"])
        joined = " ".join(fake.create_cmd()).upper()
        self.assertIn("LIMITED", joined)
        self.assertNotIn("HIGHEST", joined)
        self.assertNotIn("SYSTEM", joined)
        self.assertNotIn("S-1-5-18", joined)
        self.assertNotIn("/RU", fake.create_cmd())       # no run-as principal
        self.assertNotIn("RUNAS", joined)

    # 15-20 — Startup launcher safety (browser / dashboard / offline / quoting / secret)
    def test_15_to_20_startup_launcher_is_safe(self):
        WI.enable_startup_folder(startup_dir=self.startup)
        with open(self._launcher(), encoding="utf-8") as f:
            body = f.read()
        low = body.lower()
        self.assertNotIn("--open", body)                 # 15 no browser
        self.assertNotIn("http://", low)
        self.assertNotIn("https://", low)
        self.assertIn("-m amz_fbm start", body)          # 16 only the dashboard
        for bad in ("build-listing", "cloudflared", "ngrok", "claude", "git ", "pip "):
            self.assertNotIn(bad, low)                   # 17 offline / no external
        self.assertGreaterEqual(body.count('"'), 2)      # 18 exe path is quoted
        for tok in ("api_key", "sk-ant", "password=", "secret="):
            self.assertNotIn(tok, low)                   # 20 no secret

    def test_19_paths_with_spaces_work(self):
        en = WI.enable_startup_folder(startup_dir=self.spaced)
        self.assertTrue(en["ok"])
        st = WI.startup_folder_status(startup_dir=self.spaced)
        self.assertTrue(st["verified"])
        self.assertIn(" ", self.spaced)

    # created-but-unverified task must NOT fall back (surface the misconfiguration)
    def test_created_but_invalid_task_does_not_fall_back(self):
        system_xml = CURRENT_USER_XML.replace(_SID, "S-1-5-18")
        self._patch(FakeSchtasks(create_ok=True, query_xml=system_xml))
        res = WI.enable_autostart(method="auto", startup_dir=self.startup)
        self.assertFalse(res["ok"])
        self.assertEqual(res["method"], WI.AUTOSTART_CONFIGURATION_INVALID)
        self.assertFalse(os.path.exists(self._launcher()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
