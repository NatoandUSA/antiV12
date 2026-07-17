#!/usr/bin/env python3
"""Session 5D.1 — setuptools-free, no-network offline source bootstrap (Part C, G).

Validation logic (missing/wrong/control-char source, non-writable target, ownership)
is exercised in-process. The live proofs — correct site-packages, atomic toolkit-owned
.pth pointing at the source, an exact-target-python amz-fbm.cmd, both command forms
resolving, idempotency, deterministic hash, and toolkit-only removal — run against a
real fresh venv created once for the class (no setuptools, no network).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core", "listing", "dashboard"):
    sys.path.insert(0, os.path.join(ROOT, d))
import offline_bootstrap as OB       # noqa: E402
import local_install as LI           # noqa: E402


# ============================================================================
# Validation + safety (no target interpreter required)
# ============================================================================
class Validation(unittest.TestCase):
    def test_28_module_needs_no_setuptools(self):
        with open(OB.__file__, encoding="utf-8") as f:
            src = f.read()
        for bad in ("import setuptools", "from setuptools", "import wheel",
                    "import pip", "import build", "import hatch", "import poetry"):
            self.assertNotIn(bad, src)

    def test_29_module_makes_no_network(self):
        with open(OB.__file__, encoding="utf-8") as f:
            src = f.read()
        for bad in ("import socket", "import urllib", "http.client", "import requests",
                    "urlopen"):
            self.assertNotIn(bad, src)

    def test_41_missing_source_rejected(self):
        res = OB.bootstrap_offline_source(os.path.join(ROOT, "no-such-dir-xyz"))
        self.assertFalse(res.ok)
        self.assertTrue(any("does not exist" in e for e in res.errors))

    def test_42_wrong_source_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:      # a dir without amz_fbm/
            res = OB.bootstrap_offline_source(tmp)
        self.assertFalse(res.ok)
        self.assertTrue(any("not the AMZ FBM toolkit" in e for e in res.errors))

    def test_46_control_chars_in_source_rejected(self):
        res = OB.bootstrap_offline_source(ROOT + "\nmalicious")
        self.assertFalse(res.ok)
        self.assertTrue(any("control" in e for e in res.errors))

    def test_43_nonwritable_target_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = {"purelib": os.path.join(tmp, "site"),
                    "scripts": os.path.join(tmp, "scripts"),
                    "executable": sys.executable}
            with mock.patch.object(OB, "_target_paths", return_value=fake), \
                 mock.patch.object(OB.os, "access", return_value=False):
                res = OB.bootstrap_offline_source(ROOT, verify=False)
        self.assertFalse(res.ok)
        self.assertTrue(any("not writable" in e for e in res.errors))

    def test_49_editable_env_records_pep517(self):
        # this dev environment is a pip editable install (console script resolves)
        self.assertEqual(LI.detect_installation_mode(), OB.MODE_PEP517_EDITABLE)

    def test_48_manifest_records_bootstrap_mode(self):
        m = LI.build_manifest(installation_mode=OB.MODE_OFFLINE_SOURCE_BOOTSTRAP)
        self.assertEqual(m["installation_mode"], OB.MODE_OFFLINE_SOURCE_BOOTSTRAP)
        with mock.patch.object(OB, "is_bootstrap_installed", return_value=True):
            self.assertEqual(LI.detect_installation_mode(),
                             OB.MODE_OFFLINE_SOURCE_BOOTSTRAP)


# ============================================================================
# Live bootstrap against a real fresh venv (created once for the class)
# ============================================================================
@unittest.skipUnless(os.name == "nt", "bootstrap wrapper is a Windows .cmd")
class LiveBootstrap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="amz-boot-")
        cls.venv = os.path.join(cls.tmp, "env")
        subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", cls.venv],
                       check=True, capture_output=True, timeout=120)
        cls.vpy = os.path.join(cls.venv, "Scripts", "python.exe")
        paths = OB._target_paths(cls.vpy)
        cls.purelib, cls.scripts = paths["purelib"], paths["scripts"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        OB.remove_offline_source(self.vpy)             # clean slate per test
        for extra in ("unrelated.pth", "SomeApp.cmd"):
            for d in (self.purelib, self.scripts):
                p = os.path.join(d, extra)
                if os.path.exists(p):
                    os.remove(p)

    def _boot(self, verify=False):
        return OB.bootstrap_offline_source(ROOT, python_executable=self.vpy, verify=verify)

    def test_30_31_32_pth_written_to_target_site_packages(self):
        res = self._boot()
        self.assertTrue(res.ok)
        pth = os.path.join(self.purelib, OB.PTH_NAME)
        self.assertTrue(os.path.exists(pth))
        with open(pth, encoding="utf-8") as f:
            content = f.read()
        self.assertIn(OB.OWNER_MARKER, content)                 # owned
        self.assertIn(os.path.abspath(ROOT), content)           # points to the source

    def test_33_34_wrapper_uses_exact_python_and_quotes_spaces(self):
        self._boot()
        wrapper = os.path.join(self.scripts, OB.WRAPPER_NAME)
        with open(wrapper, encoding="utf-8") as f:
            body = f.read()
        self.assertIn(os.path.abspath(self.vpy), body)          # exact target python
        self.assertIn('"%s"' % os.path.abspath(self.vpy), body)  # quoted (space-safe)
        self.assertIn("-m amz_fbm", body)
        # a spaced target path is still quoted correctly
        self.assertTrue(OB._wrapper_content(r"C:\Program Files\Py\python.exe")
                        .count('"') >= 2)

    def test_35_to_38_verify_exercises_both_command_forms(self):
        res = self._boot(verify=True)
        self.assertTrue(res.ok, res.verification_results)
        names = {v["name"]: v["ok"] for v in res.verification_results}
        for required in ("import_amz_fbm", "python_m_version", "cli_help",
                         "json_output", "wrapper_version"):
            self.assertTrue(names.get(required), "%s failed: %s" % (required, names))

    def test_39_40_idempotent_and_deterministic_hash(self):
        r1 = self._boot()
        r2 = self._boot()
        self.assertEqual(r2.files_created, [])          # nothing re-created
        self.assertEqual(r2.files_updated, [])          # nothing changed
        self.assertEqual(r1.content_sha256, r2.content_sha256)

    def test_44_unrelated_pth_preserved(self):
        other = os.path.join(self.purelib, "unrelated.pth")
        with open(other, "w", encoding="utf-8") as f:
            f.write("some/unrelated/path\n")
        self._boot()
        self.assertTrue(os.path.exists(other))
        with open(other, encoding="utf-8") as f:
            self.assertEqual(f.read(), "some/unrelated/path\n")

    def test_45_unrelated_wrapper_preserved(self):
        other = os.path.join(self.scripts, "SomeApp.cmd")
        with open(other, "w", encoding="utf-8") as f:
            f.write("echo not ours\r\n")
        self._boot()
        self.assertTrue(os.path.exists(other))

    def test_overwrite_non_owned_pth_refused(self):
        pth = os.path.join(self.purelib, OB.PTH_NAME)
        with open(pth, "w", encoding="utf-8") as f:
            f.write("C:/someone/elses/path\n")          # no ownership marker
        try:
            res = self._boot()
            self.assertFalse(res.ok)
            self.assertTrue(any("non-toolkit-owned" in e for e in res.errors))
            with open(pth, encoding="utf-8") as f:      # untouched
                self.assertEqual(f.read(), "C:/someone/elses/path\n")
        finally:
            os.remove(pth)

    def test_47_wrapper_contains_no_secret(self):
        self._boot()
        with open(os.path.join(self.scripts, OB.WRAPPER_NAME), encoding="utf-8") as f:
            low = f.read().lower()
        for tok in ("api_key", "sk-ant", "password=", "secret=", "token="):
            self.assertNotIn(tok, low)

    def test_50_removal_only_toolkit_owned(self):
        self._boot()
        other = os.path.join(self.purelib, "unrelated.pth")
        with open(other, "w", encoding="utf-8") as f:
            f.write("keep me\n")
        res = OB.remove_offline_source(self.vpy)
        self.assertTrue(res.ok)
        self.assertEqual(len(res.files_removed), 2)     # only our .pth + .cmd
        self.assertFalse(os.path.exists(os.path.join(self.purelib, OB.PTH_NAME)))
        self.assertFalse(os.path.exists(os.path.join(self.scripts, OB.WRAPPER_NAME)))
        self.assertTrue(os.path.exists(other))          # unrelated preserved


if __name__ == "__main__":
    unittest.main(verbosity=2)
