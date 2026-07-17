#!/usr/bin/env python3
"""Session 5B — bounded subprocess runner (ACT-020, Part F). Tests 34-50.

Structured results, non-zero exits, hard timeouts that actually stop the child and
do not hang the suite, process-tree cleanup, separated/UTF-8-safe/bounded output,
secret redaction, an explicit positive timeout, no shell, and only the spawned tree
is ever terminated.
"""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
import subprocess_runner as SR
import diagnostics as D

PY = sys.executable
ALL_EXITS = tuple(range(0, 256))


class BasicResults(unittest.TestCase):
    def test_34_success_returns_structured_result(self):
        r = SR.run_subprocess([PY, "-c", "print('hello')"], timeout_seconds=30, stage_name="ok")
        self.assertTrue(r.success)
        self.assertEqual(r.exit_code, 0)
        self.assertIn("hello", r.stdout)
        self.assertFalse(r.timed_out)
        self.assertIsNone(r.error_code)
        d = r.to_dict()
        self.assertEqual(d["stage_name"], "ok")
        self.assertIn("duration_seconds", d)

    def test_35_non_zero_exit_reported(self):
        r = SR.run_subprocess([PY, "-c", "import sys; sys.exit(7)"], timeout_seconds=30)
        self.assertFalse(r.success)
        self.assertEqual(r.exit_code, 7)
        self.assertEqual(r.error_code, D.SUBPROCESS_EXIT_ERROR)

    def test_35b_allowed_exit_codes_respected(self):
        r = SR.run_subprocess([PY, "-c", "import sys; sys.exit(2)"], timeout_seconds=30,
                              allowed_exit_codes=(0, 2))
        self.assertTrue(r.success)


class Timeout(unittest.TestCase):
    def test_36_37_timeout_stops_process_and_does_not_hang(self):
        t0 = time.time()
        r = SR.run_subprocess([PY, "-c", "import time; time.sleep(60)"],
                              timeout_seconds=2, stage_name="hang")
        elapsed = time.time() - t0
        self.assertTrue(r.timed_out)
        self.assertTrue(r.terminated)
        self.assertEqual(r.error_code, D.SUBPROCESS_TIMEOUT)
        self.assertFalse(r.success)
        # 37: the call returns quickly — the suite is never held for the child's full sleep
        self.assertLess(elapsed, 20, f"runner did not stop fast enough: {elapsed:.1f}s")

    def test_50_slow_stage_diagnostic_has_stage_and_elapsed(self):
        D.clear_events()
        r = SR.run_subprocess([PY, "-c", "import time; time.sleep(30)"],
                              timeout_seconds=1, stage_name="slow-stage")
        self.assertIn("slow-stage", r.diagnostic_summary)
        self.assertRegex(r.diagnostic_summary, r"\d+\.\d+s")
        ev = D.last_event()
        self.assertEqual(ev.code, D.SUBPROCESS_TIMEOUT)
        self.assertEqual(ev.details.get("stage"), "slow-stage")
        self.assertIn("elapsed_seconds", ev.details)


class ChildCleanup(unittest.TestCase):
    def test_38_child_process_tree_is_killed(self):
        # parent spawns a grandchild that writes a marker only AFTER a delay; if the
        # tree is killed on timeout the marker never appears.
        tmp = tempfile.mkdtemp()
        marker = os.path.join(tmp, "grandchild_alive.txt")
        gc = os.path.join(tmp, "grandchild.py")
        parent = os.path.join(tmp, "parent.py")
        with open(gc, "w", encoding="utf-8") as f:
            f.write("import time, sys\n"
                    "time.sleep(5)\n"
                    "open(sys.argv[1], 'w').write('alive')\n")
        with open(parent, "w", encoding="utf-8") as f:
            f.write("import subprocess, sys, time\n"
                    "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])\n"
                    "time.sleep(30)\n")
        r = SR.run_subprocess([PY, parent, gc, marker], timeout_seconds=2, stage_name="tree")
        self.assertTrue(r.timed_out)
        # give a real orphan enough time to reach its delayed write
        time.sleep(6)
        self.assertFalse(os.path.exists(marker),
                         "grandchild survived timeout -> process tree was not cleaned up")

    def test_48_only_spawned_tree_terminated(self):
        # an unrelated sentinel process must survive a sibling's timeout+kill.
        import subprocess
        sentinel = subprocess.Popen([PY, "-c", "import time; time.sleep(20)"])
        try:
            r = SR.run_subprocess([PY, "-c", "import time; time.sleep(30)"],
                                  timeout_seconds=2, stage_name="victim")
            self.assertTrue(r.timed_out)
            self.assertIsNone(sentinel.poll(), "unrelated process was killed — too broad")
        finally:
            sentinel.terminate()
            try:
                sentinel.wait(timeout=10)
            except Exception:
                sentinel.kill()


class OutputHandling(unittest.TestCase):
    def test_39_stdout_and_stderr_captured_separately(self):
        r = SR.run_subprocess(
            [PY, "-c", "import sys; sys.stdout.write('OUT'); sys.stderr.write('ERR')"],
            timeout_seconds=30)
        self.assertIn("OUT", r.stdout)
        self.assertNotIn("ERR", r.stdout)
        self.assertIn("ERR", r.stderr)

    def test_40_utf8_output_decodes(self):
        r = SR.run_subprocess(
            [PY, "-c", "print('café — 日本語 ✓')"], timeout_seconds=30)
        self.assertIn("café", r.stdout)
        self.assertIn("日本語", r.stdout)

    def test_41_invalid_bytes_do_not_crash(self):
        r = SR.run_subprocess(
            [PY, "-c", "import sys; sys.stdout.buffer.write(bytes([0xff, 0xfe, 0x41]))"],
            timeout_seconds=30)
        self.assertTrue(r.success)          # decoding did not raise
        self.assertIn("A", r.stdout)        # the valid 0x41 byte survived

    def test_42_43_output_bounded_and_flagged(self):
        r = SR.run_subprocess([PY, "-c", "print('Z' * 50000)"], timeout_seconds=30,
                              max_output_bytes=500)
        self.assertTrue(r.stdout_truncated)
        self.assertLess(len(r.stdout.encode("utf-8")), 5000)   # far below original
        self.assertGreaterEqual(r.stdout_bytes, 50000)         # observed size preserved
        self.assertEqual(D.last_event().code, D.SUBPROCESS_OUTPUT_TRUNCATED)


class SecretSafety(unittest.TestCase):
    def test_44_secret_args_redacted_from_command_display(self):
        r = SR.run_subprocess([PY, "-c", "print(1)", "--api-key", "sk-ant-SUPERSECRET123456"],
                              timeout_seconds=30)
        self.assertNotIn("SUPERSECRET", r.command_display)
        self.assertIn("***REDACTED***", r.command_display)

    def test_44b_secret_shaped_token_redacted(self):
        r = SR.run_subprocess([PY, "-c", "print(1)", "sk-ant-anotherLONGsecretVALUE99"],
                              timeout_seconds=30)
        self.assertNotIn("anotherLONGsecret", r.command_display)

    def test_45_env_secrets_not_copied_to_diagnostics(self):
        r = SR.run_subprocess([PY, "-c", "import os; print(os.environ.get('MY_SECRET',''))"],
                              timeout_seconds=30, env={"MY_SECRET": "sk-ant-ENVSECRETVALUE12345"})
        blob = str(r.to_dict())
        self.assertNotIn("ENVSECRETVALUE", blob)
        self.assertNotIn("ENVSECRETVALUE", r.command_display)
        # the child could read it (env passed through) but stdout is redacted on the way out
        self.assertNotIn("ENVSECRETVALUE", r.stdout)


class Contract(unittest.TestCase):
    def test_46_positive_timeout_required(self):
        for bad in (0, -5, None):
            with self.assertRaises(ValueError):
                SR.run_subprocess([PY, "-c", "pass"], timeout_seconds=bad)

    def test_47_shell_true_not_used_by_default(self):
        # a string command (which would imply shell interpretation) is rejected
        with self.assertRaises(ValueError):
            SR.run_subprocess("echo hi", timeout_seconds=5)

    def test_start_failure_is_typed(self):
        r = SR.run_subprocess(["this_binary_does_not_exist_amz_5b"], timeout_seconds=5)
        self.assertEqual(r.error_code, D.SUBPROCESS_START_FAILED)
        self.assertFalse(r.success)


class ProductionIntegration(unittest.TestCase):
    def test_49_pipeline_run_integration_still_works(self):
        # pipeline.run() now routes through the shared runner (bounded)
        sys.path.insert(0, ROOT)
        import pipeline
        rc, out = pipeline.run([PY, "-c", "print('pipeline-ok')"])
        self.assertEqual(rc, 0)
        self.assertIn("pipeline-ok", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
