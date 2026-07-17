#!/usr/bin/env python3
"""Session 6A.1 — connectivity policy + modes + config migration (proof tests 1-13, 60).

The toolkit is CONNECTED TO THE OPEN WORLD but ISOLATED FROM THE OWNER'S AMAZON ACCOUNT.
These tests prove the connectivity-mode model, the legacy mapping, deterministic
serialization, and the secret-free / Amazon-credential-free configuration file.
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
import runtime_policy as RP
import network_policy as NP
import connectivity_config as CC


class Modes(unittest.TestCase):
    def test_01_new_install_default_is_connected_research(self):
        self.assertEqual(CC.NEW_INSTALL_DEFAULTS["connectivity_mode"], RP.CONNECTED_RESEARCH)
        home = tempfile.mkdtemp(prefix="conn_")
        env = {"AMZ_FBM_HOME": home}
        CC.ensure_new_install_defaults(env)
        p = CC.load_policy(env=env)
        self.assertEqual(p.connectivity_mode, RP.CONNECTED_RESEARCH)
        self.assertTrue(p.connected_research)
        self.assertTrue(p.public_web_research_enabled)
        self.assertTrue(p.external_ai_allowed)
        self.assertFalse(p.external_ai_enabled)          # allowed, not enabled

    def test_02_local_safe_denies_external(self):
        p = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": "LOCAL_SAFE"})
        d = NP.evaluate_network_request("r", "https://trends.google.com/x",
                                        capability="PUBLIC_WEB_RESEARCH", policy=p)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, "EXTERNAL_DENIED_IN_MODE")

    def test_03_test_deny_external_denies_external(self):
        p = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": "TEST_DENY_EXTERNAL"})
        d = NP.evaluate_network_request("r", "https://example.org/x",
                                        capability="PUBLIC_WEB_RESEARCH", policy=p)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, "EXTERNAL_DENIED_IN_MODE")

    def test_04_loopback_allowed_in_every_mode(self):
        for m in RP.CONNECTIVITY_MODES:
            p = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": m})
            d = NP.evaluate_network_request("dash", "http://127.0.0.1:5000/healthz", policy=p)
            self.assertTrue(d.allowed, m)
            self.assertEqual(d.destination_class, NP.LOOPBACK)

    def test_05_dashboard_binding_is_loopback_only_every_mode(self):
        for m in RP.CONNECTIVITY_MODES:
            p = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": m})
            self.assertTrue(p.dashboard_loopback_only, m)
            self.assertTrue(RP.is_loopback_host(p.bind_host), m)

    def test_06_invalid_mode_fails_clearly(self):
        p = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": "TURBO"})
        self.assertFalse(p.is_valid)
        self.assertTrue(any("CONNECTIVITY_MODE" in e for e in p.validation_errors))

    def test_07_legacy_offline_true_maps_local_safe(self):
        p = RP.load_runtime_policy(env={"OFFLINE_ONLY": "true"})
        self.assertEqual(p.connectivity_mode, RP.LOCAL_SAFE)
        self.assertTrue(p.local_safe)

    def test_08_legacy_offline_false_maps_connected_research(self):
        p = RP.load_runtime_policy(env={"OFFLINE_ONLY": "false"})
        self.assertEqual(p.connectivity_mode, RP.CONNECTED_RESEARCH)

    def test_09_explicit_mode_overrides_legacy_flag(self):
        # legacy says offline, explicit connectivity mode says connected -> connected wins
        p = RP.load_runtime_policy(env={"OFFLINE_ONLY": "true",
                                        "CONNECTIVITY_MODE": "CONNECTED_RESEARCH"})
        self.assertEqual(p.connectivity_mode, RP.CONNECTED_RESEARCH)
        self.assertFalse(p.offline_only)                 # reconciled
        # and the reverse
        q = RP.load_runtime_policy(env={"OFFLINE_ONLY": "false",
                                        "CONNECTIVITY_MODE": "LOCAL_SAFE"})
        self.assertEqual(q.connectivity_mode, RP.LOCAL_SAFE)
        self.assertTrue(q.offline_only)

    def test_bare_default_is_local_safe_offline(self):
        # a bare environment stays safe (offline_only True) — CONNECTED_RESEARCH is a
        # deliberate opt-in written by an install / the CLI, not the empty default
        p = RP.load_runtime_policy(env={})
        self.assertEqual(p.connectivity_mode, RP.LOCAL_SAFE)
        self.assertTrue(p.offline_only)


class Serialization(unittest.TestCase):
    def test_10_serialization_and_hash_deterministic(self):
        a = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": "CONNECTED_RESEARCH"})
        b = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": "CONNECTED_RESEARCH"})
        self.assertEqual(a.connectivity_policy_sha256, b.connectivity_policy_sha256)
        c = RP.load_runtime_policy(env={"CONNECTIVITY_MODE": "LOCAL_SAFE"})
        self.assertNotEqual(a.connectivity_policy_sha256, c.connectivity_policy_sha256)
        blob = json.dumps(a.connectivity_to_dict(), sort_keys=True)
        self.assertEqual(json.loads(blob)["connectivity_policy_sha256"],
                         a.connectivity_policy_sha256)
        # legacy policy hash is unchanged by the connectivity layer (still present)
        self.assertTrue(a.policy_sha256)

    def test_legacy_policy_hash_stable_for_default(self):
        # the LEGACY canonical is untouched: the default env hash must not shift
        import hashlib
        p = RP.load_runtime_policy(env={})
        expect = hashlib.sha256(json.dumps({
            "offline_only": True, "external_ai_enabled": False,
            "outbound_network_enabled": False, "bind_host": "127.0.0.1",
            "bind_port": 5000, "loopback_only": True},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(p.policy_sha256, expect)


class ConfigFile(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="conncfg_")
        self.env = {"AMZ_FBM_HOME": self.home}

    def test_11_config_migration_creates_backup(self):
        CC.write_connectivity_config({"connectivity_mode": RP.CONNECTED_RESEARCH}, self.env)
        res = CC.write_connectivity_config({"connectivity_mode": RP.LOCAL_SAFE}, self.env)
        self.assertIsNotNone(res["backed_up"])
        self.assertTrue(os.path.exists(res["backed_up"]))

    def test_12_config_contains_no_secrets(self):
        res = CC.write_connectivity_config(
            {"connectivity_mode": RP.CONNECTED_RESEARCH,
             "api_key": "sk-ant-SECRET", "password": "p", "session_cookie": "c"}, self.env)
        blob = json.dumps(res["config"])
        self.assertNotIn("SECRET", blob)
        self.assertNotIn("api_key", blob)
        self.assertNotIn("password", blob)
        for k in res["config"]:
            self.assertIn(k, CC.ALLOWED_FIELDS)

    def test_13_config_has_no_amazon_credential_fields(self):
        res = CC.write_connectivity_config(
            {"amazon_username": "u", "seller_central_cookie": "c",
             "amazon_refresh_token": "t", "sp_api_key": "k",
             "amazon_browser_profile_path": "p"}, self.env)
        blob = json.dumps(res["config"]).lower()
        for bad in ("username", "cookie", "refresh", "token", "browser_profile",
                    "seller", "password"):
            self.assertNotIn(bad, blob)

    def test_legacy_migration_maps_offline(self):
        import app_paths as AP
        # seed a legacy local-config with offline_only True
        AP.ensure_dirs(self.env)
        AP.write_json_atomic(AP.local_config_path(self.env), {"offline_only": True})
        rec = CC.migrate_from_legacy(self.env)
        self.assertTrue(rec["migrated"])
        self.assertEqual(rec["mode"], RP.LOCAL_SAFE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
