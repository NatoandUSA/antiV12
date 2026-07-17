#!/usr/bin/env python3
"""Session 5B — outbound-network guard (ACT-016, Part C).

The guard blocks the app's own outbound internet calls before any connection is
opened, raises a typed error with a stable code and no secret, records a local
diagnostic, and still allows loopback communication.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
import network_policy as NP
import runtime_policy as RP
import diagnostics as D


OFFLINE = RP.load_runtime_policy(env={})                                   # offline default
ONLINE = RP.load_runtime_policy(env={"OFFLINE_ONLY": "false",
                                     "OUTBOUND_NETWORK_ENABLED": "true"})   # explicit opt-in


class LoopbackAllowed(unittest.TestCase):
    def test_20_loopback_access_allowed_even_offline(self):
        # returns True and never raises for loopback destinations
        self.assertTrue(NP.outbound_allowed("dash", "http://127.0.0.1:5000/healthz", policy=OFFLINE))
        NP.assert_outbound_allowed("dash", "http://localhost:5000/x", policy=OFFLINE)
        NP.assert_outbound_allowed("dash", "http://[::1]:5000/x", policy=OFFLINE)


class ExternalBlocked(unittest.TestCase):
    def setUp(self):
        D.clear_events()

    def test_21_external_http_blocked_before_connection(self):
        with self.assertRaises(NP.OutboundNetworkDisabledError) as cm:
            NP.assert_outbound_allowed("anthropic", "https://api.anthropic.com/v1/models",
                                       policy=OFFLINE)
        err = cm.exception
        self.assertEqual(err.error_code, D.OUTBOUND_NETWORK_DISABLED)
        self.assertEqual(err.operation, "anthropic")
        self.assertEqual(err.destination_host, "api.anthropic.com")
        # a local diagnostic was recorded
        self.assertEqual(D.last_event().code, D.OUTBOUND_NETWORK_DISABLED)

    def test_22_external_socket_blocked_before_connection(self):
        # a host:port socket destination is blocked just like a URL
        with self.assertRaises(NP.OutboundNetworkDisabledError):
            NP.assert_outbound_allowed("socket-connect", "suggestqueries.google.com:443",
                                       policy=OFFLINE)
        self.assertFalse(NP.outbound_allowed("socket-connect", "8.8.8.8:53", policy=OFFLINE))

    def test_no_secret_in_error_message(self):
        # even if a caller foolishly passes credentials in the URL, they don't leak
        try:
            NP.assert_outbound_allowed("x", "https://user:sk-ant-SECRET@api.anthropic.com/v1")
        except NP.OutboundNetworkDisabledError as e:
            self.assertNotIn("SECRET", str(e))
            self.assertNotIn("sk-ant", str(e))

    def test_online_opt_in_allows_outbound(self):
        self.assertTrue(NP.outbound_allowed("anthropic", "https://api.anthropic.com/v1", policy=ONLINE))
        NP.assert_outbound_allowed("anthropic", "https://api.anthropic.com/v1", policy=ONLINE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
