#!/usr/bin/env python3
"""Session 6A.1 — connected-research services + external-AI boundary (tests 47-60).

CONNECTED_RESEARCH allows approved public web / third-party / market / supplier research
and update discovery, each behind its own capability. External AI is optional: an API key
alone never enables it — it needs explicit enablement + an approved provider — and it never
receives customer, credential, or Amazon-account data. Connected data is advisory by
default and never becomes a verified product fact automatically.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
import runtime_policy as RP
import network_policy as NP
import update_discovery as UD
import provenance as PV

CR = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": "CONNECTED_RESEARCH"})


def _dec(dest="https://data.example.com/x", **kw):
    kw.setdefault("policy", CR)
    return NP.evaluate_network_request("op", dest, **kw)


class ConnectedServices(unittest.TestCase):
    def test_47_generic_public_get_allowed_in_connected(self):
        d = _dec("https://duckduckgo.com/html?q=nurse+sweatshirt",
                 capability="PUBLIC_WEB_RESEARCH")
        self.assertTrue(d.allowed)
        self.assertEqual(d.reason_code, "ALLOWED")

    def test_48_third_party_data_distinct_capability(self):
        # disable third-party data only; public web must still work
        p = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": "CONNECTED_RESEARCH",
                                        "THIRD_PARTY_DATA_ENABLED": "false"})
        self.assertFalse(_dec(capability="THIRD_PARTY_DATA", policy=p).allowed)
        self.assertTrue(_dec(capability="PUBLIC_WEB_RESEARCH", policy=p).allowed)

    def test_49_market_data_distinct_capability(self):
        p = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": "CONNECTED_RESEARCH",
                                        "MARKET_DATA_ENABLED": "false"})
        self.assertFalse(_dec(capability="MARKET_DATA_CONNECTION", policy=p).allowed)
        self.assertTrue(_dec(capability="PUBLIC_WEB_RESEARCH", policy=p).allowed)

    def test_50_supplier_distinct_capability(self):
        p = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": "CONNECTED_RESEARCH",
                                        "SUPPLIER_CONNECTIONS_ENABLED": "false"})
        self.assertFalse(_dec(capability="SUPPLIER_CONNECTION", policy=p).allowed)
        self.assertTrue(CR.capability_enabled("SUPPLIER_CONNECTION"))

    def test_51_update_discovery_is_read_only(self):
        rep = UD.discover(policy=CR)
        self.assertTrue(rep["available"])
        self.assertFalse(rep["install_capable"])
        self.assertFalse(UD.can_install())

    def test_52_update_discovery_cannot_install_silently(self):
        with self.assertRaises(PermissionError):
            UD.install()

    def test_60_connected_research_defaults_advisory_only(self):
        rec = PV.research_provenance_record(source_type="web", provider="duckduckgo",
                                            connectivity_mode=CR.connectivity_mode)
        self.assertTrue(rec["advisory_only"])
        self.assertFalse(rec["verified_as_product_fact"])
        self.assertEqual(rec["review_status"], "OWNER_REVIEW_REQUIRED")


class ExternalAiBoundary(unittest.TestCase):
    def setUp(self):
        self.enabled = RP.load_runtime_policy(env={
            "CONNECTIVITY_MODE": "CONNECTED_RESEARCH", "EXTERNAL_AI_ENABLED": "true",
            "EXTERNAL_AI_PROVIDER": "anthropic"})

    def test_53_external_ai_requires_explicit_enablement(self):
        self.assertFalse(CR.external_ai_enabled)              # allowed, not enabled
        d = NP.evaluate_external_ai_request("ai", policy=CR)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, "CAPABILITY_NOT_ENABLED")

    def test_54_api_key_presence_alone_does_not_enable_ai(self):
        p = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": "CONNECTED_RESEARCH",
                                        "ANTHROPIC_API_KEY": "sk-ant-XYZ",
                                        "OPENAI_API_KEY": "sk-XYZ"})
        self.assertFalse(p.external_ai_enabled)
        self.assertFalse(NP.evaluate_external_ai_request("ai", policy=p).allowed)

    def test_55_unapproved_provider_blocked(self):
        p = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": "CONNECTED_RESEARCH",
                                        "EXTERNAL_AI_ENABLED": "true"})   # no provider
        d = NP.evaluate_external_ai_request("ai", policy=p)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, "EXTERNAL_AI_PROVIDER_NOT_APPROVED")

    def test_enabled_with_provider_allows_public(self):
        d = NP.evaluate_external_ai_request("ai", data_classification="PUBLIC",
                                            policy=self.enabled)
        self.assertTrue(d.allowed)

    def test_56_ai_cannot_receive_customer_data(self):
        d = NP.evaluate_external_ai_request("ai", data_classification="CUSTOMER_DATA",
                                            policy=self.enabled)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, "DATA_CLASSIFICATION_BLOCKED")

    def test_57_ai_cannot_receive_credential(self):
        d = NP.evaluate_external_ai_request("ai", data_classification="CREDENTIAL",
                                            policy=self.enabled)
        self.assertFalse(d.allowed)

    def test_58_ai_cannot_receive_amazon_account_data(self):
        d = NP.evaluate_external_ai_request("ai", data_classification="AMAZON_ACCOUNT_DATA",
                                            policy=self.enabled)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, "AMAZON_ACCOUNT_ACCESS_PROHIBITED")

    def test_ai_disabled_in_local_safe(self):
        p = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": "LOCAL_SAFE",
                                        "EXTERNAL_AI_ENABLED": "true",
                                        "EXTERNAL_AI_PROVIDER": "anthropic"})
        self.assertFalse(p.external_ai_allowed)
        self.assertFalse(p.external_ai_enabled)


class DataClassification(unittest.TestCase):
    def test_59_paid_research_data_requires_owner_approval(self):
        blocked = _dec(capability="THIRD_PARTY_DATA", data_classification="PAID_RESEARCH_DATA")
        self.assertFalse(blocked.allowed)
        ok = _dec(capability="THIRD_PARTY_DATA", data_classification="PAID_RESEARCH_DATA",
                  owner_approved=True)
        self.assertTrue(ok.allowed)

    def test_public_data_flows_to_approved_service(self):
        self.assertTrue(_dec(capability="THIRD_PARTY_DATA",
                             data_classification="PUBLIC").allowed)

    def test_credential_always_blocked(self):
        self.assertFalse(_dec(capability="THIRD_PARTY_DATA",
                              data_classification="CREDENTIAL").allowed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
