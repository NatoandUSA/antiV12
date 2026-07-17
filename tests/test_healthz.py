#!/usr/bin/env python3
"""Session 5B — /healthz local health endpoint (ACT-017, Part E). Tests 26-33.

Fast, deterministic, network-free, secret-free. 200 when the local runtime is
healthy; 503 when the runtime policy is invalid.
"""
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core", "listing", "dashboard"):
    sys.path.insert(0, os.path.join(ROOT, d))
import runtime_policy as RP
import app as DASH


class Healthz(unittest.TestCase):
    def setUp(self):
        self.c = DASH.app.test_client()

    def test_26_healthz_returns_200_when_healthy(self):
        r = self.c.get("/healthz")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertTrue(j["ok"])
        self.assertEqual(j["service"], "amz-fbm-toolkit")
        self.assertEqual(j["status"], "healthy")
        self.assertTrue(all(v == "PASS" for v in j["checks"].values()), j["checks"])

    def test_27_reports_offline_only_true(self):
        self.assertTrue(self.c.get("/healthz").get_json()["offline_only"])

    def test_28_reports_external_ai_false(self):
        self.assertFalse(self.c.get("/healthz").get_json()["external_ai_enabled"])

    def test_29_reports_loopback_only_binding(self):
        j = self.c.get("/healthz").get_json()
        self.assertTrue(j["loopback_only"])
        self.assertEqual(j["bind_host"], "127.0.0.1")
        self.assertFalse(j["outbound_network_enabled"])

    def test_30_performs_no_network_request(self):
        # swap the requests client for one that explodes if touched — healthz must not use it
        class Boom:
            def __getattr__(self, _):
                raise AssertionError("healthz attempted a network request")
        saved = DASH.requests
        DASH.requests = Boom()
        try:
            self.assertEqual(self.c.get("/healthz").status_code, 200)
        finally:
            DASH.requests = saved

    def test_31_no_secret_in_health_response(self):
        secret = "sk-ant-HEALTHENDPOINTSECRET99999"
        saved = DASH.POLICY
        DASH.POLICY = RP.load_runtime_policy(env={"ANTHROPIC_API_KEY": secret})
        try:
            body = self.c.get("/healthz").get_data(as_text=True)
            self.assertNotIn(secret, body)
            self.assertNotIn("HEALTHENDPOINT", body)
            # /api/runtime also exposes only a boolean presence flag, never the value
            rt = self.c.get("/api/runtime").get_data(as_text=True)
            self.assertNotIn(secret, rt)
        finally:
            DASH.POLICY = saved

    def test_32_invalid_runtime_state_returns_503(self):
        saved = DASH.POLICY
        DASH.POLICY = RP.load_runtime_policy(env={"BIND_HOST": "0.0.0.0"})   # invalid
        try:
            r = self.c.get("/healthz")
            self.assertEqual(r.status_code, 503)
            j = r.get_json()
            self.assertFalse(j["ok"])
            self.assertEqual(j["checks"]["runtime_policy"], "FAIL")
        finally:
            DASH.POLICY = saved

    def test_33_health_response_is_fast_and_bounded(self):
        t0 = time.time()
        r = self.c.get("/healthz")
        self.assertLess(time.time() - t0, 2.0)
        self.assertLess(len(r.get_data()), 4096)   # small, bounded payload


if __name__ == "__main__":
    unittest.main(verbosity=2)
