#!/usr/bin/env python3
"""Session 5C — Desktop + Start Menu shortcuts (Part H).

Real .lnk creation/removal is exercised against temporary directories (never the
owner's real Desktop/Start Menu) via the same WScript.Shell path used in production.
Current-user only, idempotent, space-safe, no admin, no icon download.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core", "listing", "dashboard"):
    sys.path.insert(0, os.path.join(ROOT, d))
import instance_manager as IM
import windows_integration as WI


def read_lnk(path):
    script = "\n".join([
        "$ws = New-Object -ComObject WScript.Shell",
        f"$l = $ws.CreateShortcut({WI._ps_quote(path)})",
        "Write-Output $l.TargetPath",
        "Write-Output $l.Arguments",
        "Write-Output $l.WorkingDirectory",
    ])
    r = WI._ps(script, stage="readlnk")
    return [x.strip() for x in (r.stdout or "").splitlines() if x.strip()]


class Shortcuts(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="amzsc_")
        self.desk = os.path.join(self.base, "Desktop")
        self.sm = os.path.join(self.base, "Start Menu")   # space in path on purpose
        os.makedirs(self.desk)
        os.makedirs(self.sm)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.base, ignore_errors=True)

    def _create(self):
        return WI.create_shortcuts(desktop_dir=self.desk, start_menu_dir=self.sm)

    def test_59_default_locations_are_current_user(self):
        lines = " ".join(WI._location_lines(None, None))
        self.assertIn("GetFolderPath('Desktop')", lines)
        self.assertIn("GetFolderPath('Programs')", lines)
        # never the all-users / machine-wide special folders
        self.assertNotIn("CommonDesktop", lines)
        self.assertNotIn("CommonPrograms", lines)

    def test_63_65_create_handles_spaces_no_admin(self):
        res = self._create()
        self.assertTrue(res["ok"])
        # start menu path contains a space and still resolves
        self.assertTrue(os.path.exists(os.path.join(self.sm, "Open AMZ FBM Toolkit.lnk")))
        self.assertTrue(os.path.exists(os.path.join(self.desk, "Open AMZ FBM Toolkit.lnk")))

    def test_60_targets_are_stable(self):
        self._create()
        target, args, wd = read_lnk(os.path.join(self.sm, "Start AMZ FBM Toolkit.lnk"))[:3]
        self.assertEqual(os.path.normcase(target),
                         os.path.normcase(IM.preferred_python()))
        self.assertIn("-m amz_fbm start", args)
        self.assertEqual(os.path.normcase(wd), os.path.normcase(IM.project_root()))

    def test_61_create_idempotent(self):
        self._create()
        self._create()
        lnks = [f for f in os.listdir(self.sm) if f.endswith(".lnk")]
        self.assertEqual(len(lnks), 3)      # no duplicates on re-create

    def test_62_remove_idempotent(self):
        self._create()
        r1 = WI.remove_shortcuts(desktop_dir=self.desk, start_menu_dir=self.sm)
        r2 = WI.remove_shortcuts(desktop_dir=self.desk, start_menu_dir=self.sm)
        self.assertTrue(r1["ok"] and r2["ok"])
        self.assertEqual(
            [f for f in os.listdir(self.desk) if f.endswith(".lnk")], [])

    def test_64_missing_optional_icon_does_not_fail(self):
        # we never set an IconLocation (no download); creation must still succeed
        res = self._create()
        self.assertTrue(res["ok"])
        with open(os.path.join(ROOT, "core", "windows_integration.py"),
                  encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("IconLocation", src)
        self.assertNotIn("-Verb RunAs", src)      # never requests elevation

    def test_status_reports_current_user_shortcuts(self):
        self._create()
        st = WI.shortcuts_status(desktop_dir=self.desk, start_menu_dir=self.sm)
        self.assertTrue(st["installed"])
        self.assertTrue(st["desktop"])
        self.assertEqual(len(st["start_menu"]), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
