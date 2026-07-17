#!/usr/bin/env python3
"""Session 6A.1 — CLI / healthz / doctor / dashboard connectivity surface (tests 74-88).

The owner-facing surfaces report the connectivity mode + capabilities and always prove
the permanent Amazon-account boundary. They make no external request, expose no secret,
and keep the dashboard localhost-only. Changing the connectivity mode never alters an
Amazon hard-boundary value.
"""
import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core", "listing", "dashboard"):
    sys.path.insert(0, os.path.join(ROOT, d))
import runtime_policy as RP
import local_install as LI
import connectivity_config as CC
import amz_fbm.cli as CLI
import app as DASH


def _run(*args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = CLI.main(list(args))
    return rc, buf.getvalue()


class CliConnectivity(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="cli_conn_")
        os.environ["AMZ_FBM_HOME"] = self.home
        self.addCleanup(lambda: os.environ.pop("AMZ_FBM_HOME", None))

    def test_74_connectivity_status_works(self):
        rc, out = _run("connectivity", "status")
        self.assertEqual(rc, 0)
        self.assertIn("connectivity", out.lower())

    def test_75_connectivity_json_is_valid(self):
        rc, out = _run("connectivity", "status", "--json")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertIn("connectivity_mode", payload)
        self.assertTrue(payload["amazon_account_isolation"])
        self.assertFalse(payload["amazon_api_enabled"])

    def test_76_mode_connected_research(self):
        rc, _ = _run("connectivity", "mode", "connected-research")
        self.assertEqual(rc, 0)
        rc, out = _run("connectivity", "status", "--json")
        self.assertEqual(json.loads(out)["connectivity_mode"], "CONNECTED_RESEARCH")

    def test_77_mode_local_safe(self):
        _run("connectivity", "mode", "connected-research")
        rc, _ = _run("connectivity", "mode", "local-safe")
        self.assertEqual(rc, 0)
        rc, out = _run("connectivity", "status", "--json")
        self.assertEqual(json.loads(out)["connectivity_mode"], "LOCAL_SAFE")

    def test_78_invalid_mode_exits_nonzero(self):
        rc, _ = _run("connectivity", "mode", "turbo-boost")
        self.assertNotEqual(rc, 0)

    def test_79_capabilities_report_amazon_hard_blocks(self):
        rc, out = _run("connectivity", "capabilities", "--json")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        for cap in RP.PROHIBITED_CAPABILITIES:
            self.assertIn(cap, payload["prohibited_permanently_blocked"])
            self.assertFalse(payload["prohibited_permanently_blocked"][cap])

    def test_80_amazon_boundary_reports_all_permanent_blocks(self):
        rc, out = _run("connectivity", "amazon-boundary", "--json")
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        b = payload["boundary"]
        self.assertTrue(payload["amazon_account_isolation"])
        for k in ("amazon_seller_central_enabled", "amazon_api_enabled",
                  "amazon_browser_automation_enabled", "amazon_network_writes_enabled",
                  "amazon_account_report_pull_enabled"):
            self.assertFalse(b[k], k)

    def test_mode_change_preserves_amazon_boundary(self):
        _run("connectivity", "mode", "connected-research")
        cfg = CC.read_connectivity_config({"AMZ_FBM_HOME": self.home})
        blob = json.dumps(cfg).lower()
        for bad in ("amazon_api", "seller", "credential", "password", "cookie"):
            self.assertNotIn(bad, blob)

    def test_verify_passes(self):
        rc, out = _run("connectivity", "verify", "--json")
        self.assertEqual(rc, 0)
        self.assertTrue(json.loads(out)["ok"])

    def test_commands_expose_no_secret(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-CONNSECRET111"
        try:
            _, out = _run("connectivity", "status", "--json")
            _, out2 = _run("connectivity", "capabilities", "--json")
        finally:
            del os.environ["ANTHROPIC_API_KEY"]
        self.assertNotIn("CONNSECRET", out + out2)


class HealthzConnectivity(unittest.TestCase):
    def setUp(self):
        self.c = DASH.app.test_client()

    def test_81_healthz_reports_connectivity_state(self):
        j = self.c.get("/healthz").get_json()
        self.assertIn("connectivity_mode", j)
        self.assertIn(j["connectivity_mode"], RP.CONNECTIVITY_MODES)
        self.assertEqual(j["amazon_account_isolation"], "PASS")
        self.assertFalse(j["amazon_credential_store_available"])
        self.assertFalse(j["amazon_api_enabled"])
        self.assertFalse(j["amazon_network_writes_enabled"])
        self.assertTrue(j["dashboard_loopback_only"])
        self.assertIn("connectivity_policy_sha256", j)

    def test_82_healthz_makes_no_external_call(self):
        class Boom:
            def __getattr__(self, _):
                raise AssertionError("healthz attempted a network request")
        saved = DASH.requests
        DASH.requests = Boom()
        try:
            self.assertEqual(self.c.get("/healthz").status_code, 200)
        finally:
            DASH.requests = saved

    def test_83_healthz_contains_no_secret(self):
        secret = "sk-ant-HEALTHCONN9999"
        saved = DASH.POLICY
        DASH.POLICY = RP.load_runtime_policy(env={"ANTHROPIC_API_KEY": secret,
                                                  "CONNECTIVITY_MODE": "CONNECTED_RESEARCH"})
        try:
            body = self.c.get("/healthz").get_data(as_text=True)
            self.assertNotIn(secret, body)
            self.assertNotIn("HEALTHCONN", body)
        finally:
            DASH.POLICY = saved

    def test_healthz_reports_connected_when_set(self):
        saved = DASH.POLICY
        DASH.POLICY = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": "CONNECTED_RESEARCH"})
        try:
            j = self.c.get("/healthz").get_json()
            self.assertEqual(j["connectivity_mode"], "CONNECTED_RESEARCH")
            self.assertTrue(j["connected_research"])
            self.assertTrue(j["public_web_research_enabled"])
        finally:
            DASH.POLICY = saved


class DoctorConnectivity(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="doc_conn_")
        self.env = {"AMZ_FBM_HOME": self.home}

    def test_84_doctor_is_read_only_and_reports_connectivity(self):
        rep = LI.doctor(env=self.env)
        self.assertIn("connectivity", rep["checks"])
        self.assertIn("amazon_boundary", rep["checks"])
        self.assertTrue(rep["checks"]["amazon_boundary"]["all_blocked"])

    def test_85_doctor_makes_no_provider_call(self):
        saved = (socket.create_connection, socket.getaddrinfo)

        def boom(*a, **k):
            raise OSError("network disabled for test")
        socket.create_connection = boom
        socket.getaddrinfo = boom
        try:
            rep = LI.doctor(env=self.env)
        finally:
            socket.create_connection, socket.getaddrinfo = saved
        self.assertIn("connectivity", rep["checks"])


class DashboardConnectivity(unittest.TestCase):
    def setUp(self):
        self.c = DASH.app.test_client()

    def test_86_dashboard_reports_connected_research_state(self):
        saved = DASH.POLICY
        DASH.POLICY = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": "CONNECTED_RESEARCH"})
        try:
            j = self.c.get("/api/runtime").get_json()
            self.assertEqual(j["connectivity"]["connectivity_mode"], "CONNECTED_RESEARCH")
            self.assertTrue(j["connectivity"]["public_web_research_enabled"])
        finally:
            DASH.POLICY = saved

    def test_87_dashboard_reports_amazon_access_unavailable(self):
        j = self.c.get("/api/runtime").get_json()
        b = j["amazon_boundary"]
        self.assertTrue(b["amazon_account_isolation"])
        self.assertFalse(b["amazon_seller_central_enabled"])
        self.assertFalse(b["amazon_api_enabled"])
        self.assertFalse(b["amazon_network_writes_enabled"])

    def test_88_dashboard_remains_localhost_only(self):
        j = self.c.get("/api/runtime").get_json()
        self.assertTrue(j["connectivity"]["dashboard_loopback_only"])
        self.assertTrue(RP.is_loopback_host(j["policy"]["bind_host"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
