#!/usr/bin/env python3
"""Session 5C — packaging + CLI surface (Part A, Part N tests 1-8).

The real entry point is exercised via ``python -m amz_fbm`` (subprocess) for help,
unknown-command, and --json; single-authority version, offline-safety, and secret
hygiene are checked in-process.
"""
import contextlib
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    import tomllib
except ModuleNotFoundError:            # pragma: no cover
    tomllib = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core", "listing", "dashboard"):
    sys.path.insert(0, os.path.join(ROOT, d))
import instance_manager as IM
import windows_integration as WI
import amz_fbm
import amz_fbm.cli as CLI


def run_cli(*args, env_extra=None):
    env = dict(os.environ)
    env.setdefault("AMZ_FBM_HOME", tempfile.mkdtemp(prefix="amzcli_"))
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, "-m", "amz_fbm", *args],
                          cwd=ROOT, capture_output=True, text=True, timeout=60, env=env)


class Packaging(unittest.TestCase):
    def test_01_pyproject_is_valid(self):
        self.assertIsNotNone(tomllib, "tomllib unavailable")
        with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
            data = tomllib.load(f)
        self.assertEqual(data["project"]["name"], "amz-fbm-toolkit")
        self.assertEqual(data["project"]["scripts"]["amz-fbm"], "amz_fbm.cli:main")
        self.assertIn("version", data["project"]["dynamic"])   # not hardcoded twice

    def test_06_version_comes_from_one_authority(self):
        with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8") as f:
            m = re.search(r'tool_version:\s*"([^"]+)"', f.read())
        cfg_version = m.group(1)
        self.assertEqual(amz_fbm.get_version(), cfg_version)
        self.assertEqual(IM.dashboard_version(), cfg_version)


class CliSurface(unittest.TestCase):
    def test_02_entry_point_resolves(self):
        r = run_cli("version")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(amz_fbm.get_version(), r.stdout)

    def test_03_help_works(self):
        r = run_cli("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("amz-fbm", r.stdout)
        for cmd in ("start", "stop", "status", "doctor", "install-local", "uninstall"):
            self.assertIn(cmd, r.stdout)

    def test_04_unknown_command_exits_nonzero(self):
        r = run_cli("definitely-not-a-command")
        self.assertNotEqual(r.returncode, 0)

    def test_04b_no_args_exits_nonzero(self):
        r = run_cli()
        self.assertNotEqual(r.returncode, 0)

    def test_05_json_output_is_valid(self):
        r = run_cli("version", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["version"], amz_fbm.get_version())


class OfflineAndSecrets(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="amzcli2_")
        os.environ["AMZ_FBM_HOME"] = self.home
        self.addCleanup(lambda: os.environ.pop("AMZ_FBM_HOME", None))
        IM.AP.ensure_dirs()          # doctor needs the current-user tree to exist
        for target, repl in (
                ("autostart_status", lambda env=None: {"installed": False, "valid": False}),
                ("shortcuts_status", lambda env=None: {"installed": False})):
            p = mock.patch.object(WI, target, repl)
            p.start()
            self.addCleanup(p.stop)

    def test_07_commands_need_no_internet(self):
        saved = (socket.create_connection, socket.getaddrinfo)

        def boom(*a, **k):
            raise OSError("network disabled for test")

        socket.create_connection = boom
        socket.getaddrinfo = boom
        try:
            self.assertEqual(CLI.main(["version"]), 0)
            self.assertEqual(CLI.main(["status"]), 0)
            self.assertEqual(CLI.main(["doctor"]), 0)   # ok because dirs writable, policy valid
        finally:
            socket.create_connection, socket.getaddrinfo = saved

    def test_08_commands_expose_no_secrets(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-CLISECRETLEAK7777777"
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                CLI.main(["status", "--json"])
                CLI.main(["doctor", "--json"])
        finally:
            del os.environ["ANTHROPIC_API_KEY"]
        self.assertNotIn("CLISECRETLEAK", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
