#!/usr/bin/env python3
"""
tests/test_ci_test_gate.py — Unit tests for the Deterministic Exact-Baseline CI Regression Gate.

Proves the exact-baseline contract:
  - Exact match -> PASS
  - New failure -> FAIL
  - New error -> FAIL
  - Removed baseline failure -> FAIL
  - Removed baseline error -> FAIL
  - Failure <-> Error category swap -> FAIL
  - Real baseline file loading & structure validation
"""
import unittest
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts.ci_test_gate import evaluate_gate_results, load_baseline, BASELINE_PATH


class TestExactBaselineGateContract(unittest.TestCase):
    def setUp(self):
        self.acc_failures = {"test_mod.ClassA.test_fail_1", "test_mod.ClassA.test_fail_2"}
        self.acc_errors = {"test_mod.ClassB.test_err_1"}

    def test_01_exact_match_passes(self):
        actual_failures = {"test_mod.ClassA.test_fail_1", "test_mod.ClassA.test_fail_2"}
        actual_errors = {"test_mod.ClassB.test_err_1"}
        is_exact_match, diff = evaluate_gate_results(
            actual_failures, actual_errors, self.acc_failures, self.acc_errors
        )
        self.assertTrue(is_exact_match)
        self.assertEqual(diff["unexpected_failures"], [])
        self.assertEqual(diff["unexpected_errors"], [])
        self.assertEqual(diff["fixed_failures"], [])
        self.assertEqual(diff["fixed_errors"], [])

    def test_02_new_failure_fails(self):
        actual_failures = {"test_mod.ClassA.test_fail_1", "test_mod.ClassA.test_fail_2", "test_mod.ClassC.test_new_fail"}
        actual_errors = {"test_mod.ClassB.test_err_1"}
        is_exact_match, diff = evaluate_gate_results(
            actual_failures, actual_errors, self.acc_failures, self.acc_errors
        )
        self.assertFalse(is_exact_match)
        self.assertIn("test_mod.ClassC.test_new_fail", diff["unexpected_failures"])

    def test_03_new_error_fails(self):
        actual_failures = {"test_mod.ClassA.test_fail_1", "test_mod.ClassA.test_fail_2"}
        actual_errors = {"test_mod.ClassB.test_err_1", "test_mod.ClassC.test_new_err"}
        is_exact_match, diff = evaluate_gate_results(
            actual_failures, actual_errors, self.acc_failures, self.acc_errors
        )
        self.assertFalse(is_exact_match)
        self.assertIn("test_mod.ClassC.test_new_err", diff["unexpected_errors"])

    def test_04_removed_baseline_failure_fails(self):
        # One baseline failure did not reproduce / passed
        actual_failures = {"test_mod.ClassA.test_fail_1"}
        actual_errors = {"test_mod.ClassB.test_err_1"}
        is_exact_match, diff = evaluate_gate_results(
            actual_failures, actual_errors, self.acc_failures, self.acc_errors
        )
        self.assertFalse(is_exact_match)
        self.assertIn("test_mod.ClassA.test_fail_2", diff["fixed_failures"])

    def test_05_removed_baseline_error_fails(self):
        # Baseline error did not reproduce / passed
        actual_failures = {"test_mod.ClassA.test_fail_1", "test_mod.ClassA.test_fail_2"}
        actual_errors = set()
        is_exact_match, diff = evaluate_gate_results(
            actual_failures, actual_errors, self.acc_failures, self.acc_errors
        )
        self.assertFalse(is_exact_match)
        self.assertIn("test_mod.ClassB.test_err_1", diff["fixed_errors"])

    def test_06_failure_to_error_swap_fails(self):
        # fail_1 became an error instead of a failure
        actual_failures = {"test_mod.ClassA.test_fail_2"}
        actual_errors = {"test_mod.ClassB.test_err_1", "test_mod.ClassA.test_fail_1"}
        is_exact_match, diff = evaluate_gate_results(
            actual_failures, actual_errors, self.acc_failures, self.acc_errors
        )
        self.assertFalse(is_exact_match)
        self.assertIn("test_mod.ClassA.test_fail_1", diff["swapped_fail_to_err"])

    def test_07_error_to_failure_swap_fails(self):
        # err_1 became a failure instead of an error
        actual_failures = {"test_mod.ClassA.test_fail_1", "test_mod.ClassA.test_fail_2", "test_mod.ClassB.test_err_1"}
        actual_errors = set()
        is_exact_match, diff = evaluate_gate_results(
            actual_failures, actual_errors, self.acc_failures, self.acc_errors
        )
        self.assertFalse(is_exact_match)
        self.assertIn("test_mod.ClassB.test_err_1", diff["swapped_err_to_fail"])

    def test_08_load_real_baseline_file(self):
        self.assertTrue(os.path.exists(BASELINE_PATH), f"Baseline file must exist at {BASELINE_PATH}")
        acc_failures, acc_errors, reasons = load_baseline()
        self.assertEqual(len(acc_failures), 6, "Expected exactly 6 accepted failures in baseline")
        self.assertEqual(len(acc_errors), 14, "Expected exactly 14 accepted errors in baseline")
        for tid in acc_failures | acc_errors:
            self.assertIn(tid, reasons, f"Reason must be provided for {tid}")
            self.assertTrue(len(reasons[tid]) > 0, f"Reason must not be empty for {tid}")


if __name__ == "__main__":
    unittest.main()
