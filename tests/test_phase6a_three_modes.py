#!/usr/bin/env python3
"""Session 6A.1 — Phase 6A determinism across connectivity modes + security regression
(proof tests 89-117).

Phase 6A product readiness is a deterministic local computation: it does not consult the
connectivity policy, request enrichment, or touch the network. So its stable outputs must
be byte-identical whether the toolkit is in CONNECTED_RESEARCH, LOCAL_SAFE, or
TEST_DENY_EXTERNAL. The certified T2 tiers, product-fact hash, claim-evidence hash,
READY_FOR_6B state and zero leakage are unchanged, and no Amazon-account integration,
browser automation, credential store, scraper, crawler, tunnel or public bind was added.

runs/T2 holds paid Helium 10 data (gitignored), so the determinism block SKIPS on a clean
checkout with no local T2 workspace.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core", "listing"):
    sys.path.insert(0, os.path.join(ROOT, d))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import runtime_policy as RP
import network_policy as NP
import provenance as PV
from production import product_workspace as PW

T2 = os.path.join(ROOT, "runs", "T2")
T2_PRESENT = os.path.exists(os.path.join(T2, "MASTER-KEYWORDS-LEAN.json"))
CERTIFIED_CLAIM_SHA = "840e62168f46e133444ca42a5cd8130c2041eaaae63a9249ec5cf557c3c16edf"
CERTIFIED_KEYWORD_SHA = "52b02f162e75f70806a8e2893a5327210037135cb8b4b3834fc192b318f8e8e7"
CERTIFIED_FACT_SHA = "0199b4de0ebce165af359a117ab437983d4ff1d6d98d94f4bf9999b6cfbca274"
MODES = ("CONNECTED_RESEARCH", "LOCAL_SAFE", "TEST_DENY_EXTERNAL")


def _artifacts(result):
    root = tempfile.mkdtemp(prefix="6a-3mode-")
    dest = os.path.join(root, "phase6", "6A")
    man = PW.write_phase6a_artifacts(result, dest_dir=dest, workspace_dir=root,
                                     started_at="T0", completed_at="T1")
    files = {}
    for name in ("PRODUCT-WORKSPACE.json", "OWNER-INPUT-REQUIRED.json",
                 "NORMALIZED-PRODUCT-FACTS.json", "CLAIM-EVIDENCE.json",
                 "PRODUCT-READINESS-REPORT.md"):
        with open(os.path.join(dest, name), "rb") as f:
            files[name] = f.read()
    return files, man["deterministic_content_sha256"]


@unittest.skipUnless(T2_PRESENT, "runs/T2 (paid, gitignored) not present on this machine")
class ThreeModeDeterminism(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.by_mode = {}
        for m in MODES:
            os.environ["CONNECTIVITY_MODE"] = m
            try:
                r = PW.build_product_workspace(T2)
                files, csha = _artifacts(r)
                snap = PW.runtime_safety_snapshot(env={"CONNECTIVITY_MODE": m})
                cls.by_mode[m] = {"r": r, "files": files, "csha": csha, "snap": snap}
            finally:
                os.environ.pop("CONNECTIVITY_MODE", None)

    def test_89_connected_research_builds(self):
        r = self.by_mode["CONNECTED_RESEARCH"]["r"]
        self.assertTrue(r.downstream_readiness["ready_for_6b"])

    def test_90_local_safe_builds(self):
        self.assertTrue(self.by_mode["LOCAL_SAFE"]["r"].downstream_readiness["ready_for_6b"])

    def test_91_test_deny_external_builds(self):
        self.assertTrue(self.by_mode["TEST_DENY_EXTERNAL"]["r"].downstream_readiness["ready_for_6b"])

    def test_92_stable_hashes_match_across_modes(self):
        shas = {m: self.by_mode[m]["csha"] for m in MODES}
        self.assertEqual(len(set(shas.values())), 1, shas)
        # and the written artifact bytes are identical across all three modes
        ref = self.by_mode["CONNECTED_RESEARCH"]["files"]
        for m in ("LOCAL_SAFE", "TEST_DENY_EXTERNAL"):
            for name, blob in self.by_mode[m]["files"].items():
                self.assertEqual(blob, ref[name], f"{name} differs in {m}")

    def test_92b_snapshot_reflects_mode_but_not_artifacts(self):
        self.assertFalse(self.by_mode["CONNECTED_RESEARCH"]["snap"]["offline_only"])
        self.assertTrue(self.by_mode["LOCAL_SAFE"]["snap"]["offline_only"])
        self.assertTrue(self.by_mode["TEST_DENY_EXTERNAL"]["snap"]["offline_only"])

    def test_93_product_fact_hash_unchanged(self):
        self.assertEqual(self.by_mode["CONNECTED_RESEARCH"]["r"].product_facts_sha256,
                         CERTIFIED_FACT_SHA)

    def test_94_claim_evidence_hash_unchanged(self):
        for m in MODES:
            self.assertEqual(self.by_mode[m]["r"].claim_evidence_sha256, CERTIFIED_CLAIM_SHA, m)

    def test_95_ready_for_6b_true(self):
        for m in MODES:
            self.assertTrue(self.by_mode[m]["r"].downstream_readiness["ready_for_6b"], m)

    def test_96_publishability_safe_draft(self):
        for m in MODES:
            self.assertEqual(self.by_mode[m]["r"].current_listing_publishability_state,
                             PW.LISTING_SAFE_DRAFT, m)

    def test_97_tier_a_74(self):
        self.assertEqual(self.by_mode["CONNECTED_RESEARCH"]["r"]._src.tier_counts["TIER_A"], 74)

    def test_98_tier_b_378(self):
        self.assertEqual(self.by_mode["CONNECTED_RESEARCH"]["r"]._src.tier_counts["TIER_B"], 378)

    def test_99_100_101_102_leakage_zero(self):
        r = self.by_mode["CONNECTED_RESEARCH"]["r"]
        for token in ("WRONG_AUDIENCE", "WRONG_PRODUCT", "WRONG_OCCASION"):
            n = 0
            for rec in r._src.eligible_keywords():
                flags = {str(f).upper() for f in rec.get("risk_flags", [])} | \
                        {str(f).upper() for f in rec.get("exclusion_reasons", [])}
                if any(token in f for f in flags):
                    n += 1
            self.assertEqual(n, 0, token)
        self.assertEqual(len(PW._eligible_ip_contamination(r._src)), 0)

    def test_keyword_source_unchanged(self):
        self.assertEqual(self.by_mode["CONNECTED_RESEARCH"]["r"].keyword_source_sha256,
                         CERTIFIED_KEYWORD_SHA)


class SecurityRegression(unittest.TestCase):
    NEW_MODULES = ("core/runtime_policy.py", "core/network_policy.py",
                   "core/approved_http_client.py", "core/connectivity_config.py",
                   "core/amazon_docs_contract.py", "core/update_discovery.py",
                   "amz_fbm/cli.py", "dashboard/app.py")

    def _src(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            return f.read().lower()

    def test_103_no_amazon_account_integration_literals(self):
        forbidden = ("sellingpartnerapi", "sp-api", "mws.amazonaws", "advertising-api.amazon",
                     "sellercentral.amazon", "amazon.com/ap/", "boto3", "spapi")
        for rel in self.NEW_MODULES:
            s = self._src(rel)
            for bad in forbidden:
                self.assertNotIn(bad, s, f"{bad!r} in {rel}")

    def test_104_no_browser_automation(self):
        for rel in self.NEW_MODULES:
            s = self._src(rel)
            for bad in ("selenium", "playwright", "webdriver", "pyppeteer",
                        "undetected_chromedriver"):
                self.assertNotIn(bad, s, f"{bad!r} in {rel}")

    def test_105_no_amazon_credential_store(self):
        import connectivity_config as CC
        home = tempfile.mkdtemp(prefix="sec_")
        res = CC.write_connectivity_config(
            {"amazon_password": "x", "sp_api_key": "y", "seller_central_cookie": "c",
             "amazon_refresh_token": "t"}, {"AMZ_FBM_HOME": home})
        blob = repr(res["config"]).lower()
        # the injected credential keys must be dropped (only capability toggles survive)
        for bad in ("password", "api_key", "credential", "cookie", "refresh", "token",
                    "seller", "_key"):
            self.assertNotIn(bad, blob)
        for k in res["config"]:
            self.assertIn(k, CC.ALLOWED_FIELDS)

    def test_106_no_amazon_scraper(self):
        p = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": "CONNECTED_RESEARCH"})
        d = NP.evaluate_network_request("op", "https://www.amazon.com/s?k=x",
                                        capability="PUBLIC_WEB_RESEARCH", policy=p)
        self.assertFalse(d.allowed)
        for rel in ("core/network_policy.py", "core/approved_http_client.py"):
            s = self._src(rel)
            for bad in ("beautifulsoup", "scrapy", "bs4"):
                self.assertNotIn(bad, s)

    def test_107_no_bulk_crawler(self):
        import update_discovery as UD
        self.assertFalse(UD.can_install())
        with self.assertRaises(PermissionError):
            UD.install()

    def test_108_no_public_dashboard_binding(self):
        for m in MODES:
            p = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": m})
            self.assertTrue(p.dashboard_loopback_only, m)
            self.assertTrue(p.loopback_only)

    def test_109_no_firewall_change(self):
        for rel in self.NEW_MODULES:
            s = self._src(rel)
            self.assertNotIn("netsh", s)
            self.assertNotIn("new-netfirewallrule", s)

    def test_110_no_public_tunnel(self):
        for rel in self.NEW_MODULES:
            s = self._src(rel)
            for bad in ("ngrok", "cloudflared", "trycloudflare", "localtunnel"):
                self.assertNotIn(bad, s)

    def test_111_deterministic_local_fallback_available(self):
        for m in MODES:
            p = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": m})
            self.assertTrue(p.deterministic_local_fallback_enabled, m)

    def test_112_113_facts_and_evidence_authority_unchanged(self):
        # provenance still marks AI inference / assumption / missing as non-evidence
        self.assertFalse(PV.is_evidence(PV.AI_INFERENCE))
        self.assertFalse(PV.is_evidence(PV.ASSUMPTION))
        self.assertTrue(PV.is_evidence(PV.VERIFIED_FACT))

    def test_114_115_116_external_research_never_auto_verified(self):
        rec = PV.research_provenance_record(source_type="ai", provider="anthropic")
        self.assertTrue(rec["advisory_only"])
        self.assertFalse(rec["verified_as_product_fact"])
        self.assertEqual(rec["review_status"], "OWNER_REVIEW_REQUIRED")
        # there is no field that lets connected research self-mark as publishable/verified
        self.assertNotIn("publishable", rec)
        self.assertNotIn("verified_fact", rec)


if __name__ == "__main__":
    unittest.main(verbosity=2)
