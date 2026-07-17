#!/usr/bin/env python3
"""Session 5B — dashboard offline behavior + external-AI isolation + network denial.

Covers proof-gate tests 15-25: no external client at startup, external-AI routes
return EXTERNAL_AI_DISABLED, no model-list / key-validation request offline, loopback
stays allowed, outbound blocked before connection, and the complete local listing +
audit + copy package still runs with all outbound network denied. No secret leaks.
"""
import json
import os
import socket
import sys
import tempfile
import unittest
from contextlib import contextmanager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core", "listing", "research", "dashboard"):
    sys.path.insert(0, os.path.join(ROOT, d))
import runtime_policy as RP
import network_policy as NP
import listing_generator as LG
import page_auditor as PA
import copy_package as CPKG
import category_policy_registry as CPR
import app as DASH


@contextmanager
def deny_network():
    """Simulate 'Internet disabled' at the OS layer: any real outbound socket /
    DNS lookup raises. In-process Flask test clients and local file I/O are
    unaffected, so this proves the local workflow needs no network."""
    saved = (socket.socket.connect, socket.create_connection, socket.getaddrinfo)

    def _blocked(*a, **k):
        raise OSError("network disabled for test (offline proof)")

    socket.socket.connect = _blocked
    socket.create_connection = _blocked
    socket.getaddrinfo = _blocked
    try:
        yield
    finally:
        (socket.socket.connect, socket.create_connection, socket.getaddrinfo) = saved


def make_project(with_facts=None):
    d = tempfile.mkdtemp()
    records = [{"keyword_exact": p, "keyword_normalized": p.lower(), "tier": "A_CORE",
                "owner_status": "auto", "risk_flags": [], "search_volume": sv,
                "simple_competitor_coverage_pct": cov, "best_rank": 5, "median_rank": 20,
                "batch_support": {"B1": 4}, "observation_count": 1}
               for p, sv, cov in [("personalized nurse sweatshirt", 1900, 90.0),
                                  ("embroidered nurse crewneck", 820, 45.0),
                                  ("rn gift pullover", 400, 30.0)]]
    doc = {"run_metadata": {"run_id": "kwrun_s5b", "schema_version": "1.0.0",
                            "source_schema": "MASTER_KEYWORDS_LEAN"},
           "quality_summary": {"counts": {"A_CORE": len(records)}}, "keywords": records}
    with open(os.path.join(d, "MASTER-KEYWORDS-LEAN.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f)
    if with_facts:
        with open(os.path.join(d, "product-facts.json"), "w", encoding="utf-8") as f:
            json.dump({"schema_version": "1.0.0", "product": with_facts}, f)
    return d


class StartupIsolation(unittest.TestCase):
    def test_15_startup_initializes_no_external_client(self):
        # offline by default; no live client object is constructed at import
        self.assertFalse(DASH.POLICY.external_ai_enabled)
        self.assertTrue(DASH.POLICY.offline_only)
        self.assertEqual(DASH.SETTINGS["api_key"], "")     # nothing stored
        self.assertEqual(DASH.SETTINGS["model"], "")


class ExternalAiRoutesDisabled(unittest.TestCase):
    def setUp(self):
        self.c = DASH.app.test_client()

    def test_17_external_ai_routes_return_disabled(self):
        for route, method in [("/api/models", "GET"), ("/api/build", "POST"),
                              ("/api/build_cc", "POST"), ("/api/settings", "POST")]:
            r = self.c.open(route, method=method, json={})
            self.assertEqual(r.status_code, 403, route)
            j = r.get_json()
            self.assertEqual(j["error_code"], "EXTERNAL_AI_DISABLED", route)
            self.assertEqual(j["status"], "OFFLINE_ONLY", route)

    def test_18_19_no_model_list_or_key_validation_request_offline(self):
        # if the route touched `requests`, this Boom would raise; the gate blocks first
        class Boom:
            def __getattr__(self, _):
                raise AssertionError("offline route attempted a network request")
        saved = DASH.requests
        DASH.requests = Boom()
        try:
            self.assertEqual(self.c.get("/api/models").status_code, 403)
            self.assertEqual(self.c.post("/api/build", json={}).status_code, 403)
        finally:
            DASH.requests = saved

    def test_21_external_http_blocked_before_connection(self):
        # explicit opt-in to external AI but outbound still disabled -> the guard
        # must stop the call BEFORE `requests` is ever touched.
        class Boom:
            def __getattr__(self, _):
                raise AssertionError("request issued despite outbound disabled")
        saved_policy, saved_req = DASH.POLICY, DASH.requests
        DASH.POLICY = RP.load_runtime_policy(env={"OFFLINE_ONLY": "false",
                                                  "EXTERNAL_AI_ENABLED": "true"})
        DASH.requests = Boom()
        DASH.SETTINGS["api_key"] = "sk-ant-UNITTESTVALUE123456"
        DASH.SETTINGS["model"] = "claude-x"
        try:
            r = self.c.get("/api/models")
            self.assertNotEqual(r.status_code, 200)     # blocked, not fulfilled
        finally:
            DASH.POLICY, DASH.requests = saved_policy, saved_req
            DASH.SETTINGS["api_key"] = ""
            DASH.SETTINGS["model"] = ""

    def test_22_external_socket_blocked_before_connection(self):
        with self.assertRaises(NP.OutboundNetworkDisabledError):
            NP.assert_outbound_allowed("socket", "api.anthropic.com:443", policy=DASH.POLICY)

    def test_25_no_secret_in_response_or_diagnostics(self):
        secret = "sk-ant-DASHBOARDSECRET7777777"
        saved = DASH.POLICY
        DASH.POLICY = RP.load_runtime_policy(env={"ANTHROPIC_API_KEY": secret})
        try:
            body = self.c.get("/api/runtime").get_data(as_text=True)
            self.assertNotIn(secret, body)
            self.assertNotIn("DASHBOARDSECRET", body)
        finally:
            DASH.POLICY = saved


class LoopbackAllowed(unittest.TestCase):
    def test_20_local_loopback_access_remains_allowed(self):
        self.assertTrue(NP.outbound_allowed("dash", "http://127.0.0.1:5000/healthz",
                                            policy=DASH.POLICY))
        # the in-process Flask client works with the network denied
        with deny_network():
            self.assertEqual(DASH.app.test_client().get("/healthz").status_code, 200)


class NetworkDenialLocalWorkflow(unittest.TestCase):
    def test_16_23_offline_listing_generation_no_outbound(self):
        d = make_project()
        with deny_network():
            r = LG.generate(d, "personalized nurse sweatshirt")
        self.assertTrue(r["ok"], r.get("reason"))
        L = r["listing"]
        self.assertTrue(L.get("title_recommended") or L.get("title"))
        self.assertTrue(L.get("backend"))
        self.assertTrue(L.get("bullets"))

    def test_24_network_denial_permits_auditor_and_copy_package(self):
        d = make_project(with_facts={"garment_type": "crewneck sweatshirt",
                                     "colors": ["navy"], "personalization_fields": ["name"]})
        with deny_network():
            r = LG.generate(d, "personalized nurse sweatshirt")
            L = r["listing"]
            policy = CPR.resolve_category_policy(L.get("category") or "apparel", marketplace="US")
            ok, errors = PA.audit_verdict(L, policy=policy, allow_warnings=True)
            copy_text = CPKG.build_copy_ready_text(L)
        self.assertIsInstance(ok, bool)          # auditor ran to a verdict, no crash
        self.assertIsInstance(errors, list)
        self.assertTrue(copy_text.strip())       # copy package produced offline


if __name__ == "__main__":
    unittest.main(verbosity=2)
