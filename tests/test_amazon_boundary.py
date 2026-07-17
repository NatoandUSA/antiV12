#!/usr/bin/env python3
"""Session 6A.1 — the ONE hard line: permanent Amazon-account isolation (tests 14-46).

There is no capability, in any mode, to log in to Amazon / Seller Central, use an
Amazon session or API, store Amazon credentials, automate an Amazon browser, pull
account reports, or write listings / prices / inventory / A+ / PPC / reviews. Every
prohibited capability stays false in every mode; every prohibited destination fails
closed; unknown Amazon destinations fail closed; and the public-documentation reader
contract is separate, disabled by default, and read-only.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
import runtime_policy as RP
import network_policy as NP
import amazon_docs_contract as ADC

MODES = RP.CONNECTIVITY_MODES
CR = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": "CONNECTED_RESEARCH"})


def _dec(dest, **kw):
    kw.setdefault("policy", CR)
    return NP.evaluate_network_request("op", dest, **kw)


class BoundaryImmutable(unittest.TestCase):
    def test_immutable_fields_false_in_every_mode(self):
        for m in MODES:
            p = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": m})
            self.assertTrue(p.amazon_account_isolation, m)
            self.assertFalse(p.amazon_credential_store_available, m)
            self.assertFalse(p.amazon_seller_central_enabled, m)
            self.assertFalse(p.amazon_authenticated_access_enabled, m)
            self.assertFalse(p.amazon_api_enabled, m)
            self.assertFalse(p.amazon_browser_automation_enabled, m)
            self.assertFalse(p.amazon_account_report_pull_enabled, m)
            self.assertFalse(p.amazon_network_writes_enabled, m)

    def test_14_amazon_login_capability_absent(self):
        self.assertNotIn("AMAZON_LOGIN", RP.ALLOWED_CAPABILITIES)
        self.assertIn("AMAZON_LOGIN", RP.PROHIBITED_CAPABILITIES)
        self.assertFalse(CR.capability_enabled("AMAZON_LOGIN"))

    def test_15_seller_central_blocked(self):
        d = _dec("https://sellercentral.amazon.com/home", capability="PUBLIC_WEB_RESEARCH")
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, "SELLER_CENTRAL_ACCESS_PROHIBITED")

    def test_16_authenticated_access_blocked(self):
        d = _dec("https://www.amazon.com/ap/signin", capability="PUBLIC_WEB_RESEARCH")
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, "AMAZON_ACCOUNT_ACCESS_PROHIBITED")

    def test_17_credential_storage_unavailable(self):
        self.assertFalse(CR.amazon_credential_store_available)
        d = _dec("https://example.com", capability="AMAZON_CREDENTIAL_STORAGE")
        self.assertEqual(d.reason_code, "AMAZON_CREDENTIAL_STORAGE_PROHIBITED")

    def test_18_19_20_sp_mws_ads_api_blocked(self):
        for host in ("selling" + "partnerapi-na.amazon.com",
                     "mws." + "amazonservices.com",
                     "advertising-" + "api.amazon.com"):
            d = _dec("https://" + host + "/x", capability="THIRD_PARTY_DATA")
            self.assertFalse(d.allowed, host)
            self.assertEqual(d.reason_code, "AMAZON_API_PROHIBITED", host)

    def test_18b_19b_20b_prohibited_api_capabilities(self):
        for cap in ("AMAZON_SP_API", "AMAZON_MWS", "AMAZON_ADVERTISING_API",
                    "AMAZON_API_ACCESS"):
            d = _dec("https://example.com", capability=cap)
            self.assertEqual(d.reason_code, "AMAZON_API_PROHIBITED", cap)

    def test_21_browser_automation_blocked(self):
        d = _dec("https://example.com", capability="AMAZON_BROWSER_AUTOMATION")
        self.assertEqual(d.reason_code, "AMAZON_BROWSER_AUTOMATION_PROHIBITED")

    def test_22_account_report_pull_blocked(self):
        d = _dec("https://example.com", capability="AMAZON_ACCOUNT_REPORT_PULL")
        self.assertEqual(d.reason_code, "AMAZON_REPORT_PULL_PROHIBITED")

    def test_23_24_25_26_27_amazon_writes_blocked(self):
        for cap in ("AMAZON_LISTING_WRITE", "AMAZON_PRICE_WRITE", "AMAZON_INVENTORY_WRITE",
                    "AMAZON_APLUS_WRITE", "AMAZON_PPC_WRITE"):
            d = _dec("https://example.com", capability=cap)
            self.assertEqual(d.reason_code, "AMAZON_WRITE_PROHIBITED", cap)

    def test_28_review_manipulation_blocked(self):
        d = _dec("https://example.com", capability="AMAZON_REVIEW_MANIPULATION")
        self.assertEqual(d.reason_code, "AMAZON_REVIEW_ACTION_PROHIBITED")

    def test_29_no_config_value_enables_a_prohibited_capability(self):
        # attempt to enable each prohibited capability via env/config -> still disabled
        env = {"CONNECTIVITY_MODE": "CONNECTED_RESEARCH"}
        for cap in RP.PROHIBITED_CAPABILITIES:
            env[cap + "_ENABLED"] = "true"
            env[cap] = "true"
        p = RP.load_runtime_policy(env=env)
        for cap in RP.PROHIBITED_CAPABILITIES:
            self.assertFalse(p.capability_enabled(cap), cap)
        self.assertFalse(p.amazon_api_enabled)
        self.assertFalse(p.amazon_seller_central_enabled)

    def test_31_api_key_or_cookie_cannot_enable_amazon_access(self):
        p = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": "CONNECTED_RESEARCH",
                                        "ANTHROPIC_API_KEY": "sk-ant-x"})
        # a cookie on an Amazon destination is refused outright
        d = NP.evaluate_network_request("op", "https://www.amazon.com/dp/B0",
                                        capability="PUBLIC_WEB_RESEARCH",
                                        has_cookies=True, authenticated=True, policy=p)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, "AMAZON_ACCOUNT_ACCESS_PROHIBITED")

    def test_32_unknown_amazon_destination_fails_closed(self):
        # an ordinary amazon marketplace host is product/search -> scraping not supported
        d = _dec("https://www.amazon.co.uk/s?k=nurse", capability="PUBLIC_WEB_RESEARCH")
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, "AMAZON_BULK_OR_MARKETPLACE_SCRAPING_NOT_SUPPORTED")

    def test_amazon_product_scraping_not_supported(self):
        d = _dec("https://www.amazon.com/dp/B00TEST", capability="PUBLIC_WEB_RESEARCH")
        self.assertEqual(d.reason_code, "AMAZON_BULK_OR_MARKETPLACE_SCRAPING_NOT_SUPPORTED")


class PublicDocumentationContract(unittest.TestCase):
    def setUp(self):
        self.docs_on = RP.load_runtime_policy(env={
            "CONNECTIVITY_MODE": "CONNECTED_RESEARCH",
            "AMAZON_PUBLIC_DOCUMENTATION_ENABLED": "true"})

    def test_33_capability_is_separately_controlled(self):
        self.assertIn("AMAZON_PUBLIC_DOCUMENTATION_READ", RP.ALLOWED_CAPABILITIES)
        self.assertTrue(self.docs_on.amazon_public_documentation_enabled)
        # default CONNECTED_RESEARCH has it OFF while other research is ON
        self.assertFalse(CR.amazon_public_documentation_enabled)
        self.assertTrue(CR.public_web_research_enabled)

    def test_34_defaults_disabled(self):
        self.assertFalse(CR.amazon_public_documentation_enabled)
        self.assertFalse(CR.capability_enabled("AMAZON_PUBLIC_DOCUMENTATION_READ"))

    def test_35_only_get_or_head(self):
        d = ADC.evaluate_documentation_fetch("https://sell.amazon.com/x", method="POST",
                                             human_triggered=True, policy=self.docs_on)
        self.assertFalse(d.allowed)

    def test_36_rejects_authentication(self):
        d = ADC.evaluate_documentation_fetch("https://sell.amazon.com/x", authenticated=True,
                                             human_triggered=True, policy=self.docs_on)
        self.assertEqual(d.reason_code, "AMAZON_ACCOUNT_ACCESS_PROHIBITED")

    def test_37_rejects_cookies(self):
        d = ADC.evaluate_documentation_fetch("https://sell.amazon.com/x", has_cookies=True,
                                             human_triggered=True, policy=self.docs_on)
        self.assertEqual(d.reason_code, "AMAZON_ACCOUNT_ACCESS_PROHIBITED")

    def test_38_rejects_request_body(self):
        d = ADC.evaluate_documentation_fetch("https://sell.amazon.com/x", method="GET",
                                             has_request_body=True, human_triggered=True,
                                             policy=self.docs_on)
        self.assertFalse(d.allowed)

    def test_39_requires_human_triggered(self):
        d = ADC.evaluate_documentation_fetch("https://sell.amazon.com/x", human_triggered=False,
                                             policy=self.docs_on)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, "AMAZON_PUBLIC_DOCUMENTATION_UNVERIFIED")

    def test_40_41_42_rejects_product_search_bulk(self):
        for url in ("https://www.amazon.com/dp/B0", "https://www.amazon.com/s?k=x",
                    "https://www.amazon.com/gp/bestsellers"):
            d = ADC.evaluate_documentation_fetch(url, human_triggered=True, policy=self.docs_on)
            self.assertFalse(d.allowed, url)

    def test_43_logs_each_permitted_fetch_contract(self):
        rec = ADC.fetch_log_record("https://sell.amazon.com/learn", status=200,
                                   content=b"hello", timestamp="T", policy=self.docs_on)
        for k in ("safe_url_display", "timestamp", "status", "content_sha256",
                  "policy_sha256"):
            self.assertIn(k, rec)
        self.assertNotIn("/", rec["safe_url_display"].replace("sell.amazon.com", ""))

    def test_44_45_results_advisory_and_not_auto_rules(self):
        rec = ADC.fetch_log_record("https://sell.amazon.com/x", policy=self.docs_on)
        self.assertTrue(rec["advisory_only"])
        self.assertFalse(rec["verified_as_product_fact"])
        self.assertEqual(rec["review_status"], "OWNER_REVIEW_REQUIRED")

    def test_46_unverified_amazon_docs_fail_closed(self):
        # even enabled + human-triggered + GET, a non-allowlisted amazon URL is unverified
        d = ADC.evaluate_documentation_fetch("https://www.amazon.com/help/policy",
                                             method="GET", human_triggered=True,
                                             policy=self.docs_on)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, "AMAZON_PUBLIC_DOCUMENTATION_UNVERIFIED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
