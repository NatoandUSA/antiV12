#!/usr/bin/env python3
"""Session 5C — current-user Task Scheduler autostart (Part G).

schtasks is mocked (creating a real task is destructive and, in a locked-down
environment, denied): we assert the exact command shape (ONLOGON, LIMITED, current
user, quoted, force, timeouts) and that success is confirmed by parsing/querying the
task XML — never by a bare exit code. A tiny live /Query for a random non-existent
task confirms the real query path reports "not installed" cleanly.
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core", "listing", "dashboard"):
    sys.path.insert(0, os.path.join(ROOT, d))
import subprocess_runner as SR
import windows_integration as WI

_SID = "S-1-5-21-100-200-300-1001"

CURRENT_USER_XML = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>{_SID}</UserId>
    <LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Actions><Exec><Command>pythonw.exe</Command><Arguments>-m amz_fbm start</Arguments></Exec></Actions>
</Task>"""

SYSTEM_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>S-1-5-18</UserId>
    <RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Actions><Exec><Command>pythonw.exe</Command></Exec></Actions>
</Task>"""

HIGHEST_XML = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>{_SID}</UserId>
    <LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Actions><Exec><Command>pythonw.exe</Command></Exec></Actions>
</Task>"""


class _R:
    def __init__(self, success, stdout="", stderr="", exit_code=0):
        self.success = success
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.diagnostic_summary = "stub"


class FakeSR:
    def __init__(self, query_xml="", query_ok=True, create_ok=True, delete_ok=True):
        self.query_xml = query_xml
        self.query_ok = query_ok
        self.create_ok = create_ok
        self.delete_ok = delete_ok
        self.calls = []

    def run_subprocess(self, command, *, timeout_seconds, stage_name=None,
                       allowed_exit_codes=(0,), **kw):
        self.calls.append({"command": list(command), "timeout": timeout_seconds,
                           "stage": stage_name})
        c = list(command)
        if c and c[0] == "whoami":
            return _R(True, f'"HOST\\\\User","{_SID}"', "")
        if c and c[0] == "schtasks":
            if "/Query" in c:
                return _R(self.query_ok and bool(self.query_xml), self.query_xml,
                          "" if self.query_ok else "ERROR: cannot find", 0 if self.query_ok else 1)
            if "/Create" in c:
                return _R(self.create_ok, "", "" if self.create_ok else "Access denied",
                          0 if self.create_ok else 1)
            if "/Delete" in c:
                return _R(self.delete_ok, "", "", 0)
        return _R(True, "", "")

    def create_args(self):
        for c in self.calls:
            if c["command"][:2] == ["schtasks", "/Create"]:
                return c["command"]
        return None


class TaskScheduler(unittest.TestCase):
    def setUp(self):
        WI._IDENTITY_CACHE.clear()          # avoid a cached real SID leaking in

    def _patch(self, fake):
        p = mock.patch.object(WI.SR, "run_subprocess", fake.run_subprocess)
        p.start()
        self.addCleanup(p.stop)

    # ---- command construction (48, 47, 46, 49, 50, 54, 55, 56) ----
    def test_54_55_56_task_run_string_quoted_and_launches_dashboard(self):
        tr = WI._task_run_string("start")
        self.assertTrue(tr.startswith('"'))                 # exe path quoted (spaces safe)
        self.assertGreaterEqual(tr.count('"'), 2)
        self.assertIn("-m amz_fbm start", tr)               # starts ONLY the dashboard
        self.assertNotIn("build", tr.lower())               # not a generation job

    def test_46_48_50_create_command_shape(self):
        fake = FakeSR(query_xml=CURRENT_USER_XML)
        self._patch(fake)
        WI.enable_autostart()
        args = fake.create_args()
        self.assertIsNotNone(args)
        self.assertIn("/SC", args)
        self.assertIn("ONLOGON", args)                      # 48
        self.assertIn("/RL", args)
        self.assertIn("LIMITED", args)                      # 47 / 50 (not HIGHEST)
        self.assertNotIn("HIGHEST", args)
        self.assertIn("/F", args)                           # 51 idempotent overwrite
        self.assertEqual(args[args.index("/TN") + 1], WI.TASK_NAME)

    def test_49_never_system(self):
        fake = FakeSR(query_xml=CURRENT_USER_XML)
        self._patch(fake)
        WI.enable_autostart()
        args = fake.create_args()
        joined = " ".join(args).upper()
        self.assertNotIn("SYSTEM", joined)
        self.assertNotIn("S-1-5-18", joined)
        # and a SYSTEM task, if it existed, is flagged and NOT valid
        fake2 = FakeSR(query_xml=SYSTEM_XML)
        self._patch(fake2)
        st = WI.autostart_status()
        self.assertTrue(st["system"])
        self.assertFalse(st["valid"])

    # ---- verification (46, 47, 53) ----
    def test_46_47_53_status_verifies_current_user_limited(self):
        self._patch(FakeSR(query_xml=CURRENT_USER_XML))
        st = WI.autostart_status()
        self.assertTrue(st["installed"])
        self.assertTrue(st["onlogon"])
        self.assertTrue(st["limited"])
        self.assertTrue(st["current_user"])
        self.assertFalse(st["system"])
        self.assertFalse(st["highest_privilege"])
        self.assertTrue(st["valid"])

    def test_50_highest_privilege_is_invalid(self):
        self._patch(FakeSR(query_xml=HIGHEST_XML))
        st = WI.autostart_status()
        self.assertTrue(st["highest_privilege"])
        self.assertFalse(st["limited"])
        self.assertFalse(st["valid"])

    # ---- idempotency (51, 52) ----
    def test_51_enable_idempotent(self):
        self._patch(FakeSR(query_xml=CURRENT_USER_XML))
        self.assertTrue(WI.enable_autostart()["ok"])
        self.assertTrue(WI.enable_autostart()["ok"])

    def test_52_disable_idempotent(self):
        self._patch(FakeSR(query_xml="", query_ok=False, delete_ok=True))
        self.assertTrue(WI.disable_autostart()["ok"])
        self.assertTrue(WI.disable_autostart()["ok"])       # already gone -> still ok

    # ---- timeouts (57) ----
    def test_57_all_task_operations_have_timeouts(self):
        fake = FakeSR(query_xml=CURRENT_USER_XML)
        self._patch(fake)
        WI.enable_autostart()
        WI.disable_autostart()
        self.assertTrue(fake.calls)
        for c in fake.calls:
            self.assertIsInstance(c["timeout"], (int, float))
            self.assertGreater(c["timeout"], 0)

    # ---- failed verification (58) ----
    def test_58_failed_verification_reports_failure(self):
        # schtasks /Create "succeeds" but the resulting task is HIGHEST -> not valid
        self._patch(FakeSR(query_xml=HIGHEST_XML, create_ok=True))
        res = WI.enable_autostart()
        self.assertTrue(res["created"])
        self.assertFalse(res["verified"])
        self.assertFalse(res["ok"])         # NOT reported success on a bare exit code

    # ---- real query path (bounded, safe) ----
    def test_live_query_nonexistent_task_reports_not_installed(self):
        saved = WI.TASK_NAME
        WI.TASK_NAME = "AMZ-FBM-Toolkit-doesnotexist-" + os.urandom(3).hex()
        try:
            st = WI.autostart_status()
        finally:
            WI.TASK_NAME = saved
        self.assertFalse(st["installed"])
        self.assertFalse(st["valid"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
