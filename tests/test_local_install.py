#!/usr/bin/env python3
"""Session 5C — local install / uninstall / manifest / doctor (Part I, J).

Windows integrations that would mutate the system (schtasks, shortcut COM calls)
are mocked; file/manifest/directory behavior is exercised for real under a temp
current-user home so the owner's real %LOCALAPPDATA% and repository are untouched.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core", "listing", "dashboard"):
    sys.path.insert(0, os.path.join(ROOT, d))
import app_paths as AP
import instance_manager as IM
import local_install as LI
import windows_integration as WI


def _stub_autostart(env=None):
    return {"installed": False, "valid": False, "onlogon": False, "limited": True,
            "current_user": True, "system": False, "highest_privilege": False,
            "task_name": WI.TASK_NAME}


def _stub_shortcuts(env=None):
    return {"installed": False, "start_menu": [], "desktop": False,
            "folder": WI.START_MENU_FOLDER}


class InstallBase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="amzinst_")
        self.env = {"AMZ_FBM_HOME": self.home}
        # never spawn schtasks/powershell during install/doctor in these tests
        for target, repl in (("autostart_status", _stub_autostart),
                             ("shortcuts_status", _stub_shortcuts)):
            p = mock.patch.object(WI, target, repl)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.home, ignore_errors=True)


class Install(InstallBase):
    def test_09_creates_current_user_directories(self):
        LI.install_local(env=self.env)
        for name in AP.SUBDIRS:
            self.assertTrue(os.path.isdir(AP.subdir(name, self.env)), name)

    def test_12_manifest_deterministic_except_timestamps(self):
        m1 = LI.install_local(env=self.env)["manifest"]
        m2 = LI.install_local(env=self.env)["manifest"]
        for f in LI.MANIFEST_TIMESTAMP_FIELDS:
            m1.pop(f, None)
            m2.pop(f, None)
        self.assertEqual(json.dumps(m1, sort_keys=True), json.dumps(m2, sort_keys=True))

    def test_11_existing_config_backed_up_before_replacement(self):
        first = LI.install_local(env=self.env)
        self.assertIsNone(first["config_backed_up"])          # nothing to back up yet
        second = LI.install_local(env=self.env)
        self.assertTrue(second["config_backed_up"])           # existing config backed up
        self.assertTrue(os.path.exists(second["config_backed_up"]))

    def test_13_install_local_is_idempotent(self):
        a = LI.install_local(env=self.env)
        b = LI.install_local(env=self.env)
        self.assertTrue(a["ok"] and b["ok"])
        self.assertTrue(os.path.exists(AP.manifest_path(self.env)))

    def test_14_autostart_not_enabled_by_default(self):
        with mock.patch.object(WI, "enable_autostart",
                               lambda env=None: self.fail("autostart must not run by default")):
            res = LI.install_local(env=self.env)               # no --autostart
        self.assertFalse(res["manifest"]["autostart_enabled"])
        self.assertIsNone(res["autostart"])

    def test_14b_autostart_only_when_requested(self):
        called = {}

        def fake_enable(env=None):
            called["x"] = 1
            return {"ok": True, "verified": True}

        with mock.patch.object(WI, "enable_autostart", fake_enable):
            LI.install_local(env=self.env, autostart=True)
        self.assertEqual(called.get("x"), 1)

    def test_15_no_admin_and_offline(self):
        res = LI.install_local(env=self.env)
        # editable local install, offline defaults, no elevation anywhere in the flow
        self.assertIn("editable-local", res["manifest"]["package_source"])
        pol = res["doctor"]["checks"]["runtime_policy"]
        self.assertTrue(pol["offline_only"])
        self.assertFalse(pol["external_ai_enabled"])
        self.assertFalse(pol["outbound_network_enabled"])

    def test_manifest_has_no_secrets(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-INSTALLSECRET000000"
        try:
            blob = json.dumps(LI.install_local(env=self.env)["manifest"])
        finally:
            del os.environ["ANTHROPIC_API_KEY"]
        self.assertNotIn("INSTALLSECRET", blob)


class Uninstall(InstallBase):
    def setUp(self):
        super().setUp()
        LI.install_local(env=self.env)
        # seed business/owner data that must survive uninstall
        for name, fn in (("data", "product-facts.json"), ("config", "owner.json"),
                         ("logs", "keep.log"), ("backups", "b.bak")):
            d = AP.subdir(name, self.env)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, fn), "w", encoding="utf-8") as f:
                f.write("preserve me")
        # patch destructive windows calls to safe stubs
        for target, repl in (
                ("disable_autostart", lambda env=None: {"ok": True, "task_name": WI.TASK_NAME,
                                                        "installed": False, "detail": "removed"}),
                ("remove_shortcuts", lambda env=None: {"ok": True,
                                                       "removed": ["a.lnk", "b.lnk"]})):
            p = mock.patch.object(WI, target, repl)
            p.start()
            self.addCleanup(p.stop)

    def _uninstall(self, stop_status=IM.NOT_RUNNING):
        with mock.patch.object(IM, "stop_instance",
                               lambda **k: {"status": stop_status, "ok": True, "message": ""}):
            return LI.uninstall(env=self.env)

    def test_66_69_10_preserves_business_and_owner_data(self):
        self._uninstall()
        self.assertTrue(os.path.exists(os.path.join(AP.data_dir(self.env), "product-facts.json")))
        self.assertTrue(os.path.exists(os.path.join(AP.config_dir(self.env), "owner.json")))
        self.assertTrue(os.path.exists(os.path.join(AP.logs_dir(self.env), "keep.log")))
        self.assertTrue(os.path.exists(os.path.join(AP.backups_dir(self.env), "b.bak")))

    def test_67_68_70_repository_and_runs_never_deleted(self):
        res = self._uninstall()
        self.assertTrue(os.path.isdir(IM.project_root()))
        self.assertTrue(os.path.isdir(os.path.join(ROOT, "runs")))
        preserved = " ".join(res["report"]["preserved"]).lower()
        self.assertIn("repository", preserved)
        self.assertIn("runs", preserved)
        self.assertIn("listing packages", preserved)

    def test_71_72_task_and_shortcuts_removed(self):
        removed = " ".join(self._uninstall()["report"]["removed"]).lower()
        self.assertIn("scheduled_task", removed)
        self.assertIn("shortcut", removed)

    def test_73_verified_running_instance_stopped(self):
        res = self._uninstall(stop_status=IM.STOPPED_OK)
        self.assertIn("running_instance (stopped)",
                      " ".join(res["report"]["removed"]).lower())

    def test_74_unknown_process_not_killed(self):
        res = self._uninstall(stop_status=IM.PORT_IN_USE_BY_UNKNOWN_PROCESS)
        self.assertTrue(any("not terminated" in m.lower()
                            for m in res["report"]["manual_cleanup"]))

    def test_75_uninstall_idempotent(self):
        self.assertTrue(self._uninstall()["ok"])
        self.assertTrue(self._uninstall()["ok"])

    def test_76_report_distinguishes_removed_and_preserved(self):
        r = self._uninstall()["report"]
        self.assertIsInstance(r["removed"], list)
        self.assertIsInstance(r["preserved"], list)
        self.assertTrue(r["preserved"])
        self.assertFalse(LI.DATA_PRESERVATION_POLICY["data_purge_supported"])


class Doctor(InstallBase):
    def test_doctor_reports_no_secrets_and_is_offline(self):
        LI.install_local(env=self.env)
        os.environ["OPENAI_API_KEY"] = "sk-DOCTORSECRET1234567890"
        try:
            rep = LI.doctor(env=self.env)
            blob = json.dumps(rep)
        finally:
            del os.environ["OPENAI_API_KEY"]
        self.assertNotIn("DOCTORSECRET", blob)
        self.assertTrue(rep["checks"]["required_modules"]["all_present"])
        self.assertTrue(rep["checks"]["runtime_directories"]["all_writable"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
