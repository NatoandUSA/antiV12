#!/usr/bin/env python3
"""Session 5C — current-user directories + atomic JSON IO (Part B)."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core",):
    sys.path.insert(0, os.path.join(ROOT, d))
import app_paths as AP


class AppPaths(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="amzpaths_")
        self.env = {"AMZ_FBM_HOME": self.home}

    def tearDown(self):
        import shutil
        shutil.rmtree(self.home, ignore_errors=True)

    def test_base_dir_honors_home_override(self):
        self.assertEqual(AP.base_dir(self.env), os.path.abspath(self.home))

    def test_ensure_dirs_creates_current_user_tree(self):
        AP.ensure_dirs(self.env)
        for name in AP.SUBDIRS:
            self.assertTrue(os.path.isdir(AP.subdir(name, self.env)), name)
        self.assertTrue(os.path.isdir(AP.bin_dir(self.env)))

    def test_default_base_uses_localappdata_not_package(self):
        base = AP.base_dir({"LOCALAPPDATA": r"C:\\Users\\x\\AppData\\Local"})
        self.assertIn("AMZ-FBM-Toolkit", base)
        self.assertNotIn(ROOT, base)          # never inside the installed package

    def test_write_json_atomic_roundtrip_and_no_residue(self):
        p = AP.instance_metadata_path(self.env)
        AP.write_json_atomic(p, {"b": 2, "a": 1})
        self.assertEqual(AP.read_json(p), {"a": 1, "b": 2})
        # deterministic key order on disk
        with open(p, encoding="utf-8") as f:
            self.assertLess(f.read().index('"a"'), open(p, encoding="utf-8").read().index('"b"'))
        residue = [n for n in os.listdir(os.path.dirname(p)) if n.startswith(".tmp-")]
        self.assertEqual(residue, [])

    def test_read_missing_returns_none(self):
        self.assertIsNone(AP.read_json(os.path.join(self.home, "nope.json")))

    def test_read_corrupt_raises(self):
        p = os.path.join(self.home, "bad.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{not json")
        with self.assertRaises(AP.CorruptJsonError):
            AP.read_json(p)

    def test_append_log_rotates_and_is_bounded(self):
        for i in range(3):
            AP.append_log("t.log", {"i": i}, env=self.env, max_bytes=20)
        logp = os.path.join(AP.logs_dir(self.env), "t.log")
        self.assertTrue(os.path.exists(logp))
        self.assertTrue(os.path.exists(logp + ".1"))   # rotated at least once
        # every line is valid JSON
        with open(logp, encoding="utf-8") as f:
            for line in f:
                json.loads(line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
