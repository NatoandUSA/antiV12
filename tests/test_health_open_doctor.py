#!/usr/bin/env python3
"""Session 5C — health / open / doctor (Part F).

Loopback-only health, a bounded timeout, open that refuses an unhealthy service and
never leaves loopback, and a read-only doctor that makes no network request, reports
policy/port/autostart/shortcut state, and never exposes secrets.
"""
import json
import os
import socket
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core", "listing", "dashboard"):
    sys.path.insert(0, os.path.join(ROOT, d))
import instance_manager as IM
import local_install as LI
import runtime_policy as RP
import windows_integration as WI
import amz_fbm.cli as CLI


class FakePolicy:
    """Minimal policy stand-in for open() loopback checks."""
    def __init__(self, host, port=5000):
        self.bind_host = host
        self.bind_port = port


def free_closed_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()          # closed -> connection refused
    return p


class Health(unittest.TestCase):
    def test_37_health_refuses_non_loopback(self):
        with self.assertRaises(ValueError):
            IM.http_get_json("http://example.com/healthz")
        with self.assertRaises(ValueError):
            IM.http_get_json("http://8.8.8.8:80/healthz")

    def test_38_health_is_bounded(self):
        url = f"http://127.0.0.1:{free_closed_port()}/healthz"
        t0 = time.time()
        self.assertIsNone(IM.http_get_json(url, timeout=1.0))
        self.assertLess(time.time() - t0, 3.0)

    def test_health_url_is_loopback(self):
        pol = RP.load_runtime_policy()
        self.assertTrue(IM.health_url_for(pol).startswith("http://127.0.0.1"))


class Open(unittest.TestCase):
    def test_39_open_refuses_unhealthy_service(self):
        opened = []
        with mock.patch.object(IM, "http_get_json", lambda u, timeout=2.0: None), \
             mock.patch.object(IM, "_open_browser", lambda p: opened.append(1) or True):
            rc = CLI.cmd_open(types.SimpleNamespace(), json_out=False)
        self.assertEqual(rc, 1)
        self.assertEqual(opened, [])          # browser never opened when unhealthy

    def test_open_opens_when_healthy(self):
        with mock.patch.object(IM, "http_get_json",
                               lambda u, timeout=2.0: {"ok": True, "_http_status": 200}), \
             mock.patch.object(IM, "_open_browser", lambda p: True):
            rc = CLI.cmd_open(types.SimpleNamespace(), json_out=False)
        self.assertEqual(rc, 0)

    def test_40_open_never_leaves_loopback(self):
        self.assertTrue(IM.dashboard_url_for(FakePolicy("127.0.0.1")).startswith(
            "http://127.0.0.1"))
        # a non-loopback host is refused without ever invoking the browser
        called = []
        with mock.patch("webbrowser.open", lambda *a, **k: called.append(1) or True):
            self.assertFalse(IM._open_browser(FakePolicy("8.8.8.8")))
        self.assertEqual(called, [])


class Doctor(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="amzdoc_")
        self.env = {"AMZ_FBM_HOME": self.home}
        IM.AP.ensure_dirs(self.env)
        for target, repl in (
                ("autostart_status", lambda env=None: {"installed": True, "valid": True}),
                ("shortcuts_status", lambda env=None: {"installed": True})):
            p = mock.patch.object(WI, target, repl)
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.home, ignore_errors=True)

    def test_41_doctor_makes_no_network_request(self):
        saved = (socket.create_connection, socket.getaddrinfo)

        def boom(*a, **k):
            raise OSError("network disabled for test")

        socket.create_connection = boom
        socket.getaddrinfo = boom
        try:
            rep = LI.doctor(env=self.env)     # must complete with no outbound
        finally:
            socket.create_connection, socket.getaddrinfo = saved
        self.assertIn("checks", rep)

    def test_42_doctor_reports_runtime_policy_failure(self):
        bad = RP.load_runtime_policy(env={"BIND_HOST": "0.0.0.0"})
        rep = LI.doctor(env=self.env, policy=bad)
        self.assertFalse(rep["checks"]["runtime_policy"]["valid"])
        self.assertFalse(rep["ok"])

    def test_43_doctor_reports_port_conflict(self):
        with mock.patch.object(IM, "get_instance_status",
                               lambda env=None, policy=None: {
                                   "status": IM.PORT_IN_USE_BY_UNKNOWN_PROCESS,
                                   "detail": "x", "identity_verified": False}):
            rep = LI.doctor(env=self.env)
        self.assertTrue(rep["checks"]["port_conflict"]["conflict"])
        self.assertFalse(rep["ok"])

    def test_44_doctor_reports_autostart_and_shortcuts(self):
        rep = LI.doctor(env=self.env)
        self.assertIn("autostart", rep["checks"])
        self.assertIn("shortcuts", rep["checks"])
        self.assertTrue(rep["checks"]["autostart"]["installed"])

    def test_45_doctor_reveals_no_secrets(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-DOCTORNOLEAK99999999"
        try:
            blob = json.dumps(LI.doctor(env=self.env))
        finally:
            del os.environ["ANTHROPIC_API_KEY"]
        self.assertNotIn("DOCTORNOLEAK", blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
