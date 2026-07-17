#!/usr/bin/env python3
"""Session 5C — security + regression guards (Part N tests 77-90).

The launcher/install features must not weaken any Session 1-5B guarantee: loopback
only, external AI off, outbound blocked, no Amazon/Seller-Central write path, no
firewall opening, no public tunnel, no global python kill, no machine-wide task.
Deterministic T2 generation and the shared auditor must remain unchanged.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core", "listing", "dashboard"):
    sys.path.insert(0, os.path.join(ROOT, d))
import runtime_policy as RP
import network_policy as NP
import instance_manager as IM
import windows_integration as WI
import listing_generator as LG
import page_auditor as PA
import category_policy_registry as CPR


def _scan_files():
    files = []
    for sub in ("core", "amz_fbm"):
        folder = os.path.join(ROOT, sub)
        for fn in os.listdir(folder):
            if fn.endswith(".py"):
                files.append(os.path.join(folder, fn))
    files.append(os.path.join(ROOT, "dashboard", "app.py"))
    out = {}
    for p in files:
        with open(p, encoding="utf-8") as f:
            out[os.path.relpath(p, ROOT).replace(os.sep, "/")] = f.read().lower()
    return out


def _make_project():
    d = tempfile.mkdtemp(prefix="amzreg_")
    records = [{"keyword_exact": p, "keyword_normalized": p.lower(), "tier": "A_CORE",
                "owner_status": "auto", "risk_flags": [], "search_volume": sv,
                "simple_competitor_coverage_pct": cov, "best_rank": 5, "median_rank": 20,
                "batch_support": {"B1": 4}, "observation_count": 1}
               for p, sv, cov in [("personalized nurse sweatshirt", 1900, 90.0),
                                  ("embroidered nurse crewneck", 820, 45.0)]]
    doc = {"run_metadata": {"run_id": "kwrun_s5c", "schema_version": "1.0.0",
                            "source_schema": "MASTER_KEYWORDS_LEAN"},
           "quality_summary": {"counts": {"A_CORE": len(records)}}, "keywords": records}
    with open(os.path.join(d, "MASTER-KEYWORDS-LEAN.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f)
    return d


class RuntimeSafety(unittest.TestCase):
    def test_77_bind_host_loopback_only(self):
        pol = RP.load_runtime_policy()
        self.assertEqual(pol.bind_host, "127.0.0.1")
        self.assertTrue(pol.loopback_only_effective)

    def test_78_zero_host_rejected(self):
        self.assertFalse(RP.load_runtime_policy(env={"BIND_HOST": "0.0.0.0"}).is_valid)

    def test_79_external_ai_disabled_by_default(self):
        self.assertFalse(RP.load_runtime_policy().external_ai_enabled)

    def test_80_outbound_blocked_by_default(self):
        pol = RP.load_runtime_policy()
        self.assertFalse(pol.outbound_network_enabled)
        with self.assertRaises(NP.OutboundNetworkDisabledError):
            NP.assert_outbound_allowed("https", "api.anthropic.com:443", policy=pol)


class NoForbiddenPaths(unittest.TestCase):
    def setUp(self):
        self.sources = _scan_files()

    def _assert_absent(self, tokens):
        for rel, src in self.sources.items():
            for t in tokens:
                self.assertNotIn(t, src, f"{t!r} found in {rel}")

    def test_81_no_amazon_or_seller_central_path(self):
        self._assert_absent(("sellingpartnerapi", "sp-api", "mws.amazonaws",
                             "advertising-api.amazon", "sellercentral.amazon",
                             "amazon.com/ap/", "boto3", "spapi"))

    def test_82_no_firewall_command(self):
        self._assert_absent(("netsh advfirewall", "new-netfirewallrule",
                             "set-netfirewallrule", "enablefirewallrule"))

    def test_83_no_public_tunnel(self):
        self._assert_absent(("cloudflared", "ngrok", "trycloudflare",
                             "localtunnel", "devtunnel", "--tunnel"))

    def test_84_no_kill_all_python(self):
        self._assert_absent(("/im python", "taskkill /im", "get-process python",
                             "killall", "pkill"))
        # our termination targets a single verified PID tree only
        self.assertIn("/pid", self.sources["core/instance_manager.py"])

    def test_85_no_machine_wide_or_privileged_task(self):
        killer = []
        with mock.patch.object(WI.SR, "run_subprocess",
                               lambda command, **k: killer.append(list(command)) or
                               type("R", (), {"success": True, "stdout": "", "stderr": "",
                                              "exit_code": 0, "diagnostic_summary": ""})()):
            WI.enable_autostart()
        create = next((c for c in killer if c[:2] == ["schtasks", "/Create"]), None)
        self.assertIsNotNone(create)
        up = " ".join(create).upper()
        self.assertIn("ONLOGON", up)
        self.assertIn("LIMITED", up)
        self.assertNotIn("HIGHEST", up)
        self.assertNotIn("SYSTEM", up)
        self.assertNotIn("/RU", up)          # runs as the current interactive user


class DeterminismAndAuditor(unittest.TestCase):
    def test_86_session5b_authorities_intact(self):
        for mod in (RP, NP, IM):
            self.assertTrue(mod)
        pol = RP.load_runtime_policy()
        self.assertTrue(pol.offline_only and pol.is_valid)

    def test_87_t2_generation_deterministic(self):
        d = _make_project()
        a = LG.generate(d, "personalized nurse sweatshirt")["listing"]
        b = LG.generate(d, "personalized nurse sweatshirt")["listing"]
        for field in ("title_recommended", "title", "backend", "bullets", "description"):
            self.assertEqual(json.dumps(a.get(field), sort_keys=True),
                             json.dumps(b.get(field), sort_keys=True), field)

    def test_88_89_page_auditor_unchanged(self):
        self.assertTrue(hasattr(PA, "audit_verdict"))
        d = _make_project()
        L = LG.generate(d, "personalized nurse sweatshirt")["listing"]
        policy = CPR.resolve_category_policy(L.get("category") or "apparel", marketplace="US")
        ok, errors = PA.audit_verdict(L, policy=policy, allow_warnings=True)
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(errors, list)

    def test_90_no_import_regression(self):
        # the launcher additions must not break the dashboard / listing imports
        import app as DASH
        self.assertTrue(DASH.app)
        self.assertTrue(hasattr(DASH, "healthz"))
        self.assertTrue(hasattr(DASH, "api_local"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
