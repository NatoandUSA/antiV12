#!/usr/bin/env python3
"""Session 5D — Release 1 certification machinery + mandatory-claim tests.

These executable tests cover the NEW Session 5D certification apparatus and the
5D-specific mandatory claims that are cheap and deterministic to assert in-process:
isolation/sanitization, the gate-status model (a BLOCKED gate is never promoted to
PASS), the offline network-denial harness, T2 twice-determinism, identity/port
safety (PID alone is never sufficient), no-admin, the security source scan, the
single authoritative output package, and artifact sanitization.

The heavy *live* proofs (detached start/health/stop, live Task Scheduler, live
shortcuts) are executed by ``scripts/release1_certify.py`` against the real machine
— a mocked task/shortcut check is explicitly NOT sufficient for those gates and so
is not faked here. The broad Session 1-5C regression (Part U tests 99-107) is the
existing suite, which this file extends without weakening.
"""
import json
import os
import socket
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("scripts", "core", "listing", "dashboard"):
    _p = os.path.join(ROOT, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import release1_certify as R          # noqa: E402
import amz_fbm                        # noqa: E402
import app_paths as AP                # noqa: E402
import instance_manager as IM         # noqa: E402
import runtime_policy as RP           # noqa: E402
import copy_package as CPKG           # noqa: E402


def _noop(*a, **k):
    pass


# ============================================================================
# Sanitization + isolation (Part U 1-4)
# ============================================================================
class TestSanitization(unittest.TestCase):
    def test_scrub_removes_home_user_temp(self):
        home = os.path.expanduser("~")
        raw = os.path.join(home, "secret", "path.json")
        out = R.SCRUB(raw)
        self.assertNotIn(home, out)
        self.assertIn("<USER_HOME>", out)

    def test_scrub_handles_nested_structures(self):
        home = os.path.expanduser("~")
        data = {"a": [home, {"b": home}], "n": 5, "ok": True}
        out = R.SCRUB(data)
        self.assertNotIn(home, json.dumps(out))
        self.assertEqual(out["n"], 5)
        self.assertIs(out["ok"], True)

    def test_scrub_passes_non_strings_through(self):
        self.assertEqual(R.SCRUB(42), 42)
        self.assertIsNone(R.SCRUB(None))

    def test_workspace_is_isolated_under_temp_not_repo(self):
        cert = R.Certifier(log=_noop)
        try:
            g = cert.gate_workspace()
            self.assertEqual(g.status, R.PASS)
            self.assertTrue(os.path.abspath(cert.workspace).startswith(
                os.path.abspath(tempfile.gettempdir())))
            self.assertFalse(os.path.abspath(cert.workspace).startswith(
                os.path.abspath(R.PROJECT_ROOT)))
            # AMZ_FBM_HOME redirects mutable app state into the isolated home
            self.assertEqual(os.path.abspath(AP.base_dir()),
                             os.path.abspath(cert.app_home))
        finally:
            cert._teardown()

    def test_certification_does_not_mutate_repository_runs(self):
        """Staging T2 inputs copies FROM runs/T2 and writes only into a temp dir."""
        before = _dir_snapshot(os.path.join(R.PROJECT_ROOT, "runs", "T2"))
        cert = R.Certifier(log=_noop)
        with tempfile.TemporaryDirectory() as tmp:
            copied = cert._stage_t2_inputs(os.path.join(tmp, "run"))
            self.assertIn("MASTER-KEYWORDS-LEAN.json", copied)
            cert._generate(os.path.join(tmp, "run"))
        after = _dir_snapshot(os.path.join(R.PROJECT_ROOT, "runs", "T2"))
        self.assertEqual(before, after, "runs/T2 must be untouched by certification")


def _dir_snapshot(path):
    out = {}
    for name in sorted(os.listdir(path)):
        p = os.path.join(path, name)
        if os.path.isfile(p):
            out[name] = os.path.getmtime(p)
    return out


# ============================================================================
# Gate model — a BLOCKED mandatory gate is never RELEASE1_CERTIFIED
# ============================================================================
class TestGateModel(unittest.TestCase):
    def _cert_with(self, *statuses):
        cert = R.Certifier(log=_noop)
        for i, st in enumerate(statuses):
            g = R.Gate(str(i), f"gate{i}")
            g.finish(st, "x")
            cert.gates.append(g)
        return cert

    def test_all_pass_is_certified(self):
        cert = self._cert_with(R.PASS, R.PASS, R.PASS)
        self.assertEqual(cert._report("t")["overall_status"], R.CERTIFIED)

    def test_one_blocked_is_not_certified(self):
        cert = self._cert_with(R.PASS, R.BLOCKED, R.PASS)
        rep = cert._report("t")
        self.assertEqual(rep["overall_status"], R.NOT_CERTIFIED)
        self.assertIn("1", rep["mandatory_blocked"])

    def test_one_fail_is_not_certified(self):
        rep = self._cert_with(R.PASS, R.FAIL)._report("t")
        self.assertEqual(rep["overall_status"], R.NOT_CERTIFIED)

    def test_not_applicable_does_not_block(self):
        rep = self._cert_with(R.PASS, R.NA)._report("t")
        self.assertEqual(rep["overall_status"], R.CERTIFIED)

    def test_blocked_status_distinct_from_pass(self):
        self.assertNotEqual(R.BLOCKED, R.PASS)
        self.assertNotEqual(R.FAIL, R.PASS)

    def test_gate_todict_is_sanitized(self):
        g = R.Gate("Z", "z")
        g.finish(R.PASS, "ok", path=os.path.expanduser("~"))
        self.assertNotIn(os.path.expanduser("~"), json.dumps(g.to_dict()))


# ============================================================================
# Hash helpers + free port
# ============================================================================
class TestHelpers(unittest.TestCase):
    def test_canon_hash_is_order_independent(self):
        self.assertEqual(R._canon_hash({"a": 1, "b": 2}),
                         R._canon_hash({"b": 2, "a": 1}))

    def test_canon_hash_distinguishes_content(self):
        self.assertNotEqual(R._canon_hash({"a": 1}), R._canon_hash({"a": 2}))

    def test_free_port_is_actually_free(self):
        p = R._free_port()
        s = socket.socket()
        try:
            self.assertNotEqual(s.connect_ex(("127.0.0.1", p)), 0)
        finally:
            s.close()


# ============================================================================
# Offline network-denial harness (Part U 42-44, 48)
# ============================================================================
class TestOfflineGuard(unittest.TestCase):
    def test_external_dns_is_denied_and_counted(self):
        with R.network_denial_guard() as attempts:
            with self.assertRaises(socket.gaierror):
                socket.getaddrinfo("example.com", 443)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["kind"], "dns")

    def test_external_socket_is_denied_and_counted(self):
        with R.network_denial_guard() as attempts:
            s = socket.socket()
            with self.assertRaises(OSError):
                s.connect(("8.8.8.8", 53))
            s.close()
        self.assertTrue(any(a["kind"] == "connect" for a in attempts))

    def test_loopback_is_permitted(self):
        with R.network_denial_guard() as attempts:
            # loopback name resolution must still succeed and NOT be counted
            socket.getaddrinfo("127.0.0.1", 80)
        self.assertEqual(attempts, [])

    def test_guard_restores_socket_functions(self):
        original = socket.getaddrinfo
        with R.network_denial_guard():
            pass
        self.assertIs(socket.getaddrinfo, original)


# ============================================================================
# T2 twice-determinism + zero leakage + deprecated absence (Part U 51-72)
# ============================================================================
class TestT2Determinism(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cert = R.Certifier(log=_noop)
        cls.tmp = tempfile.mkdtemp(prefix="amzfbm-5dtest-")
        d1 = os.path.join(cls.tmp, "run1")
        d2 = os.path.join(cls.tmp, "run2")
        cls.cert._stage_t2_inputs(d1)
        cls.cert._stage_t2_inputs(d2)
        cls.r1 = cls.cert._generate(d1)
        cls.r2 = cls.cert._generate(d2)
        cls.d1, cls.d2 = d1, d2

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_section_hashes_match_across_two_generations(self):
        h1 = self.cert._section_hashes(self.r1)
        h2 = self.cert._section_hashes(self.r2)
        self.assertEqual(h1, h2)

    def test_keyword_source_is_lean_with_expected_tiers(self):
        h = self.cert._section_hashes(self.r1)
        self.assertEqual(h["keyword_source_file"], "MASTER-KEYWORDS-LEAN.json")
        self.assertEqual(h["keyword_tier_counts"].get("TIER_A"), 74)
        self.assertEqual(h["keyword_tier_counts"].get("TIER_B"), 378)

    def test_t2_stays_safe_draft(self):
        self.assertEqual(self.r1["listing"]["publishability"],
                         "SAFE_DRAFT_OWNER_FACTS_REQUIRED")

    def test_five_bullets_generated(self):
        self.assertEqual(len(self.r1["listing"]["bullets"]), 5)

    def test_no_wrong_audience_or_occasion_leakage(self):
        text = CPKG.build_copy_ready_text(self.r1["listing"]).lower()
        for term in ("teacher", "bride", "mom", "halloween", "nursing bra"):
            self.assertNotIn(term, text)

    def test_copy_ready_and_manual_plan_are_deterministic(self):
        self.assertEqual(CPKG.build_copy_ready_text(self.r1["listing"]),
                         CPKG.build_copy_ready_text(self.r2["listing"]))
        self.assertEqual(
            R._canon_hash(CPKG.build_manual_entry_plan(self.r1["listing"])),
            R._canon_hash(CPKG.build_manual_entry_plan(self.r2["listing"])))

    def test_deprecated_outputs_are_not_written(self):
        for name in R.DEPRECATED_OUTPUTS:
            self.assertFalse(os.path.exists(os.path.join(self.d1, name)),
                             f"{name} must not be produced as a current authority")

    def test_authoritative_copy_paste_pair_is_written(self):
        for name in ("LISTING-COPY-READY.txt", "SELLER-CENTRAL-MANUAL-ENTRY-PLAN.json"):
            self.assertTrue(os.path.exists(os.path.join(self.d1, name)))


# ============================================================================
# Identity + port safety (Part U 20-28) — PID alone is never sufficient
# ============================================================================
class TestIdentityAndPortSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amzfbm-5dident-")
        self.env = {"AMZ_FBM_HOME": self.tmp}
        self.policy = RP.load_runtime_policy(
            env={"BIND_HOST": "127.0.0.1", "BIND_PORT": "5993"})
        AP.ensure_dirs(self.env)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_meta(self, pid, start_time):
        meta = IM.build_metadata(self.policy, pid, "iid-test", "python.exe",
                                 ["python"], start_time, "2026-01-01T00:00:00")
        IM.write_metadata(meta, self.env)

    def test_pid_alone_cannot_establish_identity_reused_pid(self):
        self._write_meta(4242, "100")           # recorded creation token 100
        with mock.patch.object(IM, "probe_pid_alive", return_value=True), \
             mock.patch.object(IM, "probe_pid_start_time", return_value="999"):
            ev = IM.evaluate(self.policy, self.env)
        self.assertEqual(ev["status"], IM.STALE_RUNTIME_STATE)
        self.assertTrue(ev["reused_pid"])
        self.assertIsNone(ev["pid"])             # a reused PID is never surfaced

    def test_health_pid_mismatch_prevents_identity(self):
        v = IM.verify_identity({"pid": 10, "instance_id": "a"},
                               {"service": IM.SERVICE_NAME, "pid": 11, "instance_id": "a"})
        self.assertFalse(v["ok"])

    def test_service_name_mismatch_prevents_identity(self):
        v = IM.verify_identity({"pid": 10}, {"service": "something-else", "pid": 10})
        self.assertFalse(v["ok"])

    def test_no_corroborator_prevents_identity(self):
        v = IM.verify_identity({"pid": 10}, {"service": IM.SERVICE_NAME})
        self.assertFalse(v["ok"])

    def test_dead_pid_metadata_is_stale(self):
        self._write_meta(999999, "100")
        with mock.patch.object(IM, "probe_pid_alive", return_value=False):
            ev = IM.evaluate(self.policy, self.env)
        self.assertEqual(ev["status"], IM.STALE_RUNTIME_STATE)

    def test_corrupt_metadata_is_stale_not_crash(self):
        with open(AP.instance_metadata_path(self.env), "w", encoding="utf-8") as f:
            f.write("{ not valid json ")
        ev = IM.get_instance_status(env=self.env, policy=self.policy)
        self.assertEqual(ev["status"], IM.STALE_RUNTIME_STATE)
        self.assertTrue(ev["metadata_corrupt"])

    def test_unknown_port_owner_is_not_ours(self):
        with mock.patch.object(IM, "port_listening", return_value=True), \
             mock.patch.object(IM, "http_get_json", return_value=None):
            ev = IM.evaluate(self.policy, self.env)
        self.assertEqual(ev["status"], IM.PORT_IN_USE_BY_UNKNOWN_PROCESS)

    def test_terminate_refuses_reused_pid(self):
        import subprocess
        victim = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(20)"])
        try:
            res = IM._terminate_tree(victim.pid, expected_start_time="not-the-real-token")
            self.assertFalse(res["terminated"])
            self.assertIsNone(victim.poll())     # victim still alive
        finally:
            victim.terminate()
            try:
                victim.wait(timeout=5)
            except Exception:
                victim.kill()


# ============================================================================
# No-admin + security source scan (Part U 88-98)
# ============================================================================
class TestNoAdminAndSecurity(unittest.TestCase):
    def test_process_is_not_elevated(self):
        cert = R.Certifier(log=_noop)
        g = cert.gate_no_admin()
        if g.evidence.get("elevated") is True:
            self.skipTest("shell is elevated; rerun non-elevated to prove no-admin")
        self.assertEqual(g.status, R.PASS)
        self.assertIs(g.evidence.get("elevated"), False)

    def test_security_scan_finds_no_prohibited_runtime_behavior(self):
        cert = R.Certifier(log=_noop)
        g = cert.gate_security_scan()
        self.assertEqual(g.status, R.PASS, g.evidence.get("reachable_findings"))
        self.assertEqual(g.evidence["reachable_findings"], [])
        self.assertGreater(g.evidence["files_scanned"], 0)


# ============================================================================
# Version authority + artifacts (Part U 10, S)
# ============================================================================
class TestVersionAndArtifacts(unittest.TestCase):
    def test_cli_version_matches_config_authority(self):
        self.assertEqual(amz_fbm.get_version(), IM.dashboard_version())

    def test_emit_artifacts_writes_all_and_is_sanitized(self):
        home = os.path.expanduser("~")
        report = {
            "session": "5D", "generated_at_start": "t0", "generated_at_end": "t1",
            "project_basename": "repo", "overall_status": R.NOT_CERTIFIED,
            "mandatory_total": 2, "mandatory_pass": 1,
            "mandatory_failed": [], "mandatory_blocked": ["H"], "test_port": 5057,
            "authority_map": {"current_authority": {"listing.json": "abc"}},
            "gates": [
                {"id": "H", "name": "task", "mandatory": True, "status": R.BLOCKED,
                 "summary": "blocked", "evidence": {"path": os.path.join(home, "x")}},
                {"id": "M", "name": "uninstall", "mandatory": True, "status": R.PASS,
                 "summary": "ok", "evidence": {}},
            ],
        }
        with tempfile.TemporaryDirectory() as out:
            written = R.emit_artifacts(report, out)
            for name in ("SESSION5D-RELEASE1-CERTIFICATION.json",
                         "RELEASE1-AUTHORITATIVE-OUTPUT-MAP.json",
                         "RELEASE1-CERTIFICATION-REPORT.md",
                         "RELEASE1-OWNER-CHECKLIST.md"):
                self.assertIn(name, written)
                self.assertTrue(os.path.exists(os.path.join(out, name)))
            with open(os.path.join(out, "SESSION5D-RELEASE1-CERTIFICATION.json"),
                      encoding="utf-8") as f:
                blob = f.read()
            self.assertNotIn(home, blob)          # sanitized


# ============================================================================
# Session 5D.1 — no-admin autostart + offline bootstrap certification surface
# ============================================================================
import windows_integration as WI      # noqa: E402
import offline_bootstrap as OB        # noqa: E402
import local_install as LI            # noqa: E402


class _FakeResult:
    def __init__(self, stderr="", stdout=""):
        self.stderr = stderr
        self.stdout = stdout


class TestAutostartMethodModel(unittest.TestCase):
    def test_classify_access_denied_vs_unavailable(self):
        self.assertEqual(WI._classify_task_failure(_FakeResult("ERROR: Access is denied.")),
                         WI.TASK_SCHEDULER_ACCESS_DENIED)
        self.assertEqual(WI._classify_task_failure(_FakeResult("cannot find the file")),
                         WI.TASK_SCHEDULER_UNAVAILABLE)

    def test_autostart_state_never_requires_admin(self):
        d = tempfile.mkdtemp(prefix="amz5d1-state-")
        try:
            with mock.patch.object(WI, "autostart_status",
                                   return_value={"installed": False, "valid": False}):
                state = WI.autostart_state(startup_dir=d)
            self.assertFalse(state["requires_admin"])
            self.assertEqual(state["method"], WI.AUTOSTART_DISABLED)
        finally:
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_generated_launchers_have_no_prohibited_patterns(self):
        bodies = [WI._startup_launcher_body(),
                  OB._wrapper_content(r"C:\Program Files\Py\python.exe"),
                  OB._pth_content(R.PROJECT_ROOT)]
        prohibited = ("0.0.0.0", "netsh advfirewall", "new-netfirewallrule", "ngrok",
                      "cloudflared", "trycloudflare", "localtunnel", "taskkill /im python",
                      "highestavailable", "s-1-5-18", "/rl highest", "/ru system",
                      "--open", "http://", "https://", "runas")
        for body in bodies:
            low = body.lower()
            for bad in prohibited:
                self.assertNotIn(bad, low, "%r in generated launcher" % bad)

    def test_no_new_elevation_command_in_core(self):
        import re
        rx = re.compile(r"verb['\"]?\s*[:=]\s*['\"]?runas|shellexecute[^\n]*runas|"
                        r"/RL[\"',\s]+HIGHEST|/RU[\"',\s]+(SYSTEM|S-1-5-18)|"
                        r"start-process[^\n]*-verb\s+runas", re.I)
        offenders = []
        for root, _dirs, files in os.walk(os.path.join(R.PROJECT_ROOT, "core")):
            if "__pycache__" in root:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                with open(os.path.join(root, fn), encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        s = line.strip()
                        if s.startswith("#") or not s:
                            continue
                        if rx.search(line):
                            offenders.append("%s:%d %s" % (fn, i, s[:80]))
        self.assertEqual(offenders, [])


class TestInstallationModeAndArtifacts(unittest.TestCase):
    def test_detect_installation_mode_is_known(self):
        self.assertIn(LI.detect_installation_mode(),
                      (OB.MODE_PEP517_EDITABLE, OB.MODE_OFFLINE_SOURCE_BOOTSTRAP,
                       "SOURCE_ON_PATH"))

    def test_installation_mode_and_startup_method_appear_sanitized(self):
        home = os.path.expanduser("~")
        report = {
            "session": "5D", "generated_at_start": "t0", "generated_at_end": "t1",
            "project_basename": "repo", "overall_status": R.CERTIFIED,
            "mandatory_total": 2, "mandatory_pass": 2,
            "mandatory_failed": [], "mandatory_blocked": [], "test_port": 5057,
            "authority_map": {"current_authority": {}},
            "gates": [
                {"id": "B", "name": "install", "mandatory": True, "status": R.PASS,
                 "summary": "ok", "evidence": {
                     "installation_mode": "OFFLINE_SOURCE_BOOTSTRAP",
                     "site_packages_display": os.path.join(home, "venv", "site")}},
                {"id": "H", "name": "autostart", "mandatory": True, "status": R.PASS,
                 "summary": "ok", "evidence": {
                     "method": "STARTUP_FOLDER_CURRENT_USER",
                     "startup_removed": True}},
            ],
        }
        with tempfile.TemporaryDirectory() as out:
            R.emit_artifacts(report, out)
            with open(os.path.join(out, "SESSION5D-RELEASE1-CERTIFICATION.json"),
                      encoding="utf-8") as f:
                cert_blob = f.read()
            with open(os.path.join(out, "RELEASE1-WINDOWS-INTEGRATION-PROOF.json"),
                      encoding="utf-8") as f:
                win_blob = f.read()
        self.assertIn("OFFLINE_SOURCE_BOOTSTRAP", cert_blob)
        self.assertIn("STARTUP_FOLDER_CURRENT_USER", win_blob)
        self.assertNotIn(home, cert_blob)          # still sanitized


class TestSecurityRegression5D1(unittest.TestCase):
    def test_security_scan_still_clean_with_new_modules(self):
        cert = R.Certifier(log=_noop)
        g = cert.gate_security_scan()
        self.assertEqual(g.status, R.PASS, g.evidence.get("reachable_findings"))

    def test_no_machine_wide_or_system_autostart_introduced(self):
        # the only /Create the toolkit issues is current-user ONLOGON/LIMITED
        tr = WI._task_run_string("start")
        self.assertIn("-m amz_fbm start", tr)
        self.assertNotIn("ONSTART", tr.upper())
        with open(WI.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"/RL", "LIMITED"', src)         # create uses limited privilege
        self.assertNotIn('"HIGHEST"', src)             # never a highest-privilege create arg
        self.assertNotIn('"/RU"', src)                 # never a run-as principal (no SYSTEM)

    def test_bootstrap_removal_only_touches_owned_files(self):
        # a bootstrap removal in this pip-editable env removes nothing (no owned files)
        res = OB.remove_offline_source()
        self.assertTrue(res.ok)
        self.assertEqual(res.files_removed, [])


if __name__ == "__main__":
    unittest.main()
