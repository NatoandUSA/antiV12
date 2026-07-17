#!/usr/bin/env python3
"""Session 5B — central runtime policy (ACT-016). Tests 1-14 of the proof gate.

Safe offline-first defaults hold with no env/config; invalid values fail clearly and
never silently flip to an unsafe true; a credential alone never enables external AI;
secrets never appear in policy output; unsafe/non-loopback binds fail; serialization
and the policy hash are deterministic.
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
import runtime_policy as RP


class SafeDefaults(unittest.TestCase):
    def setUp(self):
        self.p = RP.load_runtime_policy(env={})   # no env, no config

    def test_01_defaults_valid(self):
        self.assertTrue(self.p.is_valid, self.p.validation_errors)
        self.assertEqual(self.p.environment_source, "defaults")

    def test_02_offline_only_defaults_true(self):
        self.assertTrue(self.p.offline_only)

    def test_03_external_ai_defaults_false(self):
        self.assertFalse(self.p.external_ai_enabled)

    def test_04_outbound_network_defaults_false(self):
        self.assertFalse(self.p.outbound_network_enabled)

    def test_05_bind_host_defaults_loopback(self):
        self.assertEqual(self.p.bind_host, "127.0.0.1")

    def test_06_loopback_only_defaults_true(self):
        self.assertTrue(self.p.loopback_only)
        self.assertTrue(self.p.loopback_only_effective)


class BooleanNormalization(unittest.TestCase):
    def test_07_invalid_boolean_fails(self):
        p = RP.load_runtime_policy(env={"OFFLINE_ONLY": "maybe"})
        self.assertFalse(p.is_valid)
        self.assertTrue(any("OFFLINE_ONLY" in e for e in p.validation_errors))
        # never silently becomes an unsafe true-ish value: falls back to the SAFE default
        self.assertTrue(p.offline_only)

    def test_07b_strict_raises_on_invalid(self):
        with self.assertRaises(RP.RuntimePolicyError):
            RP.load_runtime_policy_strict(env={"OFFLINE_ONLY": "maybe"})

    def test_accepted_boolean_forms(self):
        for val in ("false", "0", "no", "off", "n"):
            p = RP.load_runtime_policy(env={"OFFLINE_ONLY": val, "LOOPBACK_ONLY": "true"})
            self.assertFalse(p.offline_only, val)
        for val in ("true", "1", "yes", "on", "y"):
            p = RP.load_runtime_policy(env={"OFFLINE_ONLY": val})
            self.assertTrue(p.offline_only, val)


class ExternalAiIsolation(unittest.TestCase):
    def test_08_offline_overrides_attempted_external_ai(self):
        p = RP.load_runtime_policy(env={"EXTERNAL_AI_ENABLED": "true",
                                        "OUTBOUND_NETWORK_ENABLED": "true"})
        # offline_only defaults true -> both forced off with a warning
        self.assertFalse(p.external_ai_enabled)
        self.assertFalse(p.outbound_network_enabled)
        self.assertTrue(p.warnings)

    def test_09_api_key_presence_does_not_enable_external_ai(self):
        p = RP.load_runtime_policy(env={"ANTHROPIC_API_KEY": "sk-ant-EXAMPLE1234567890",
                                        "OPENAI_API_KEY": "sk-EXAMPLEabcdefghijklmnop"})
        self.assertFalse(p.external_ai_enabled)
        self.assertFalse(p.outbound_network_enabled)
        self.assertTrue(p.credentials_present["ANTHROPIC_API_KEY"])

    def test_09b_explicit_opt_in_allows_external_ai(self):
        # the only way to enable it: explicit OFFLINE_ONLY=false + EXTERNAL_AI_ENABLED=true
        p = RP.load_runtime_policy(env={"OFFLINE_ONLY": "false", "EXTERNAL_AI_ENABLED": "true"})
        self.assertTrue(p.external_ai_enabled)
        self.assertTrue(p.is_valid, p.validation_errors)


class SecretRedaction(unittest.TestCase):
    def test_10_secrets_absent_from_policy_output(self):
        secret = "sk-ant-TOPSECRETVALUE0987654321"
        p = RP.load_runtime_policy(env={"ANTHROPIC_API_KEY": secret})
        blob = json.dumps(p.to_dict())
        self.assertNotIn(secret, blob)
        self.assertNotIn("TOPSECRET", blob)
        # only a boolean presence flag is exposed
        self.assertIs(p.to_dict()["credentials_present"]["ANTHROPIC_API_KEY"], True)


class BindHostSafety(unittest.TestCase):
    def test_11_non_loopback_bind_fails(self):
        p = RP.load_runtime_policy(env={"BIND_HOST": "192.168.1.50"})
        self.assertFalse(p.is_valid)
        self.assertTrue(any("BIND_HOST" in e for e in p.validation_errors))

    def test_12_zero_host_fails(self):
        p = RP.load_runtime_policy(env={"BIND_HOST": "0.0.0.0"})
        self.assertFalse(p.is_valid)
        # 0.0.0.0 fails even if someone tries to also disable loopback_only
        p2 = RP.load_runtime_policy(env={"BIND_HOST": "0.0.0.0", "LOOPBACK_ONLY": "false"})
        self.assertFalse(p2.is_valid)

    def test_13_localhost_and_ipv6_loopback_recognized(self):
        for host in ("localhost", "::1", "127.0.0.5"):
            p = RP.load_runtime_policy(env={"BIND_HOST": host})
            self.assertTrue(p.is_valid, (host, p.validation_errors))
            self.assertTrue(RP.is_loopback_host(host), host)
        self.assertFalse(RP.is_loopback_host("0.0.0.0"))
        self.assertFalse(RP.is_loopback_host("10.0.0.1"))


class Determinism(unittest.TestCase):
    def test_14_serialization_and_hash_deterministic(self):
        a = RP.load_runtime_policy(env={})
        b = RP.load_runtime_policy(env={})
        self.assertEqual(a.policy_sha256, b.policy_sha256)
        self.assertEqual(a.to_dict()["policy_sha256"], b.policy_sha256)
        # a different configuration yields a different hash
        c = RP.load_runtime_policy(env={"BIND_PORT": "5050"})
        self.assertNotEqual(a.policy_sha256, c.policy_sha256)
        # round-trips through json unchanged
        self.assertEqual(json.loads(json.dumps(a.to_dict()))["policy_sha256"], a.policy_sha256)


if __name__ == "__main__":
    unittest.main(verbosity=2)
