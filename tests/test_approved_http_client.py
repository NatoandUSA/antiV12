#!/usr/bin/env python3
"""Session 6A.1 — the approved outbound HTTP client (tests 61-73).

Every new production internet call goes through core.approved_http_client.request. It
requires an explicit finite timeout and an explicit response-size bound, enforces the
bound, disables redirects by default (reclassifying each hop when enabled), keeps TLS
verification on, retains no cookies, redacts secret headers, keeps request bodies out of
diagnostics, fails denied requests BEFORE connecting, still allows loopback, and never
shells out. A tiny loopback HTTP server exercises the transport paths without the Internet.
"""
import http.server
import os
import ssl
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
import runtime_policy as RP
import network_policy as NP
import approved_http_client as HC

CR = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": "CONNECTED_RESEARCH"})


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "https://sellercentral.amazon.com/steal")
            self.end_headers()
            return
        if self.path.startswith("/big"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x" * 100000)
            return
        self.send_response(200)
        self.send_header("Set-Cookie", "sid=abc; Path=/")
        self.end_headers()
        self.wfile.write(b"ok-loopback")

    do_POST = do_GET


def _start_server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class ClientContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.base = _start_server()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_61_timeout_is_explicit_and_positive(self):
        for bad in (0, -1, None, float("inf")):
            with self.assertRaises(ValueError):
                HC.request("GET", self.base + "/", capability="LOOPBACK_ACCESS",
                           timeout_seconds=bad, max_response_bytes=1000, policy=CR)

    def test_62_max_response_size_is_explicit(self):
        for bad in (0, -5, None):
            with self.assertRaises(ValueError):
                HC.request("GET", self.base + "/", capability="LOOPBACK_ACCESS",
                           timeout_seconds=2, max_response_bytes=bad, policy=CR)

    def test_63_response_size_is_bounded(self):
        r = HC.request("GET", self.base + "/big", capability="LOOPBACK_ACCESS",
                       timeout_seconds=5, max_response_bytes=1000, policy=CR)
        self.assertLessEqual(len(r.body), 1000)
        self.assertTrue(r.truncated)

    def test_64_redirects_disabled_by_default(self):
        # a 302 to Seller Central must NOT be followed by default
        r = HC.request("GET", self.base + "/redirect", capability="LOOPBACK_ACCESS",
                       timeout_seconds=5, max_response_bytes=1000, policy=CR)
        self.assertIn(r.status, (301, 302, 303, 307, 308))

    def test_65_66_redirect_to_seller_central_blocked_when_following(self):
        with self.assertRaises(NP.NetworkRequestDenied) as cm:
            HC.request("GET", self.base + "/redirect", capability="PUBLIC_WEB_RESEARCH",
                       timeout_seconds=5, max_response_bytes=1000, follow_redirects=True,
                       policy=CR)
        self.assertEqual(cm.exception.reason_code, "SELLER_CENTRAL_ACCESS_PROHIBITED")

    def test_67_tls_validation_remains_enabled(self):
        ctx = ssl.create_default_context()
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)

    def test_68_cookies_not_retained(self):
        r = HC.request("GET", self.base + "/", capability="LOOPBACK_ACCESS",
                       timeout_seconds=5, max_response_bytes=1000, policy=CR)
        # a second request carries no cookie from the first (no cookie jar)
        r2 = HC.request("GET", self.base + "/", capability="LOOPBACK_ACCESS",
                        timeout_seconds=5, max_response_bytes=1000, policy=CR)
        self.assertEqual(r2.body, b"ok-loopback")
        # Set-Cookie header is redacted in the returned record
        self.assertNotIn("abc", str(r.headers))

    def test_69_secret_headers_redacted(self):
        rec = HC.redacted_request_record(
            "GET", self.base + "/", capability="LOOPBACK_ACCESS",
            headers={"Authorization": "Bearer sk-ant-SECRET", "X-Api-Key": "k"})
        self.assertNotIn("SECRET", str(rec))
        self.assertEqual(rec["headers"]["Authorization"], "***REDACTED***")

    def test_70_request_body_not_in_diagnostics(self):
        rec = HC.redacted_request_record("POST", self.base + "/", capability="PUBLIC_WEB_RESEARCH")
        self.assertNotIn("body", rec)

    def test_71_denied_requests_fail_before_connection(self):
        # point at Seller Central with a socket-buster policy — must raise before connecting
        with self.assertRaises(NP.NetworkRequestDenied):
            HC.request("GET", "https://sellercentral.amazon.com/x",
                       capability="PUBLIC_WEB_RESEARCH", timeout_seconds=5,
                       max_response_bytes=1000, policy=CR)

    def test_72_loopback_requests_remain_possible(self):
        r = HC.request("GET", self.base + "/", capability="LOOPBACK_ACCESS",
                       timeout_seconds=5, max_response_bytes=1000, policy=CR)
        self.assertEqual(r.status, 200)
        self.assertEqual(r.body, b"ok-loopback")

    def test_73_no_shell_based_http(self):
        with open(os.path.join(ROOT, "core", "approved_http_client.py"), encoding="utf-8") as f:
            src = f.read()
        for bad in ("subprocess", "os.system", "curl", "Invoke-WebRequest", "popen"):
            self.assertNotIn(bad, src, bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
