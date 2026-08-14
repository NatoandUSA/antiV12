#!/usr/bin/env python3
"""
tests/test_ci_test_gate.py — Unit tests for Deterministic Regression Gate V2.

Proves the Gate V2 contract:
  - conditional test PASS -> gate PASS
  - conditional test FAIL with valid ID -> gate PASS
  - conditional test ERROR -> gate FAIL
  - unexpected failure/error -> gate FAIL
  - deterministic baseline test unexpectedly PASSing -> gate FAIL
  - deterministic baseline error unexpectedly PASSing -> gate FAIL
  - failure <-> error category swap -> gate FAIL
  - malformed conditional entry (missing fields) -> gate FAIL
  - expired conditional entry (past review_by date) -> gate FAIL
  - real baseline file structure & metadata integrity validation
"""
import unittest
import os
import sys
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts.ci_test_gate import evaluate_gate_results, load_baseline, BASELINE_PATH


class TestGateV2Contract(unittest.TestCase):
    def setUp(self):
        self.baseline_data = {
            "version": "2.0",
            "deterministic_failures": {
                "test_mod.DetClass.test_det_fail_1": "Deterministic reason 1",
                "test_mod.DetClass.test_det_fail_2": "Deterministic reason 2",
            },
            "deterministic_errors": {
                "test_mod.DetClass.test_det_err_1": "Deterministic error reason 1",
            },
            "environment_conditional_failures": {
                "test_mod.CondClass.test_cond_1": {
                    "test_id": "test_mod.CondClass.test_cond_1",
                    "reason_code": "TEST_REASON",
                    "reason": "Test reason description",
                    "owner": "test-team",
                    "introduced_at": "2026-08-01",
                    "review_by": "2026-12-31",
                    "remediation": "Test remediation plan",
                }
            },
            "schema_errors": [],
        }
        self.as_of_date = datetime.date(2026, 8, 15)

    def test_01_conditional_test_pass_passes_gate(self):
        # Deterministic match exactly; conditional test passes (not in actual_failures)
        actual_failures = {"test_mod.DetClass.test_det_fail_1", "test_mod.DetClass.test_det_fail_2"}
        actual_errors = {"test_mod.DetClass.test_det_err_1"}
        is_passed, diff = evaluate_gate_results(
            actual_failures, actual_errors, self.baseline_data, as_of_date=self.as_of_date
        )
        self.assertTrue(is_passed)
        self.assertIn("test_mod.CondClass.test_cond_1", diff["conditional_passed"])

    def test_02_conditional_test_fail_passes_gate(self):
        # Deterministic match exactly; conditional test fails (in actual_failures)
        actual_failures = {
            "test_mod.DetClass.test_det_fail_1",
            "test_mod.DetClass.test_det_fail_2",
            "test_mod.CondClass.test_cond_1",
        }
        actual_errors = {"test_mod.DetClass.test_det_err_1"}
        is_passed, diff = evaluate_gate_results(
            actual_failures, actual_errors, self.baseline_data, as_of_date=self.as_of_date
        )
        self.assertTrue(is_passed)
        self.assertIn("test_mod.CondClass.test_cond_1", diff["conditional_reproduced_failures"])

    def test_03_conditional_test_error_fails_gate(self):
        # Conditional test produces an ERROR instead of PASS/FAIL -> FORBIDDEN
        actual_failures = {"test_mod.DetClass.test_det_fail_1", "test_mod.DetClass.test_det_fail_2"}
        actual_errors = {"test_mod.DetClass.test_det_err_1", "test_mod.CondClass.test_cond_1"}
        is_passed, diff = evaluate_gate_results(
            actual_failures, actual_errors, self.baseline_data, as_of_date=self.as_of_date
        )
        self.assertFalse(is_passed)
        self.assertIn("test_mod.CondClass.test_cond_1", diff["conditional_produced_errors"])

    def test_04_unexpected_failure_fails_gate(self):
        # An unaccepted test fails
        actual_failures = {
            "test_mod.DetClass.test_det_fail_1",
            "test_mod.DetClass.test_det_fail_2",
            "test_mod.OtherClass.test_unaccepted_failure",
        }
        actual_errors = {"test_mod.DetClass.test_det_err_1"}
        is_passed, diff = evaluate_gate_results(
            actual_failures, actual_errors, self.baseline_data, as_of_date=self.as_of_date
        )
        self.assertFalse(is_passed)
        self.assertIn("test_mod.OtherClass.test_unaccepted_failure", diff["unexpected_failures"])

    def test_05_unexpected_error_fails_gate(self):
        # An unaccepted test errors
        actual_failures = {"test_mod.DetClass.test_det_fail_1", "test_mod.DetClass.test_det_fail_2"}
        actual_errors = {"test_mod.DetClass.test_det_err_1", "test_mod.OtherClass.test_unaccepted_error"}
        is_passed, diff = evaluate_gate_results(
            actual_failures, actual_errors, self.baseline_data, as_of_date=self.as_of_date
        )
        self.assertFalse(is_passed)
        self.assertIn("test_mod.OtherClass.test_unaccepted_error", diff["unexpected_errors"])

    def test_06_deterministic_failure_removed_fails_gate(self):
        # A deterministic baseline failure did not reproduce / unexpectedly passed
        actual_failures = {"test_mod.DetClass.test_det_fail_1"}
        actual_errors = {"test_mod.DetClass.test_det_err_1"}
        is_passed, diff = evaluate_gate_results(
            actual_failures, actual_errors, self.baseline_data, as_of_date=self.as_of_date
        )
        self.assertFalse(is_passed)
        self.assertIn("test_mod.DetClass.test_det_fail_2", diff["fixed_deterministic_failures"])

    def test_07_deterministic_error_removed_fails_gate(self):
        # A deterministic baseline error did not reproduce / unexpectedly passed
        actual_failures = {"test_mod.DetClass.test_det_fail_1", "test_mod.DetClass.test_det_fail_2"}
        actual_errors = set()
        is_passed, diff = evaluate_gate_results(
            actual_failures, actual_errors, self.baseline_data, as_of_date=self.as_of_date
        )
        self.assertFalse(is_passed)
        self.assertIn("test_mod.DetClass.test_det_err_1", diff["fixed_deterministic_errors"])

    def test_08_category_swap_fails_gate(self):
        # Deterministic failure became an error
        actual_failures = {"test_mod.DetClass.test_det_fail_2"}
        actual_errors = {"test_mod.DetClass.test_det_err_1", "test_mod.DetClass.test_det_fail_1"}
        is_passed, diff = evaluate_gate_results(
            actual_failures, actual_errors, self.baseline_data, as_of_date=self.as_of_date
        )
        self.assertFalse(is_passed)
        self.assertIn("test_mod.DetClass.test_det_fail_1", diff["swapped_fail_to_err"])

    def test_09_malformed_conditional_entry_fails_gate(self):
        # Conditional entry missing required fields (e.g. remediation)
        bad_baseline = dict(self.baseline_data)
        bad_baseline["schema_errors"] = ["Conditional test missing remediation"]
        actual_failures = {"test_mod.DetClass.test_det_fail_1", "test_mod.DetClass.test_det_fail_2"}
        actual_errors = {"test_mod.DetClass.test_det_err_1"}
        is_passed, diff = evaluate_gate_results(
            actual_failures, actual_errors, bad_baseline, as_of_date=self.as_of_date
        )
        self.assertFalse(is_passed)
        self.assertIn("Conditional test missing remediation", diff["schema_errors"])

    def test_10_expired_conditional_entry_fails_gate(self):
        # Conditional entry review_by is in the past
        expired_baseline = {
            "version": "2.0",
            "deterministic_failures": self.baseline_data["deterministic_failures"],
            "deterministic_errors": self.baseline_data["deterministic_errors"],
            "environment_conditional_failures": {
                "test_mod.CondClass.test_cond_1": {
                    "test_id": "test_mod.CondClass.test_cond_1",
                    "reason_code": "TEST_REASON",
                    "reason": "Test reason description",
                    "owner": "test-team",
                    "introduced_at": "2026-08-01",
                    "review_by": "2026-08-10",  # Expired relative to 2026-08-15
                    "remediation": "Test remediation plan",
                }
            },
            "schema_errors": [],
        }
        actual_failures = {"test_mod.DetClass.test_det_fail_1", "test_mod.DetClass.test_det_fail_2"}
        actual_errors = {"test_mod.DetClass.test_det_err_1"}
        is_passed, diff = evaluate_gate_results(
            actual_failures, actual_errors, expired_baseline, as_of_date=datetime.date(2026, 8, 15)
        )
        self.assertFalse(is_passed)
        self.assertEqual(len(diff["expired_conditional_entries"]), 1)
        self.assertEqual(diff["expired_conditional_entries"][0][0], "test_mod.CondClass.test_cond_1")

    def test_11_real_baseline_file_validation(self):
        self.assertTrue(os.path.exists(BASELINE_PATH), f"Baseline file must exist at {BASELINE_PATH}")
        baseline_data = load_baseline()
        self.assertEqual(baseline_data.get("schema_errors"), [])
        
        det_failures = baseline_data["deterministic_failures"]
        det_errors = baseline_data["deterministic_errors"]
        cond_failures = baseline_data["environment_conditional_failures"]

        self.assertEqual(len(det_failures), 3, f"Expected exactly 3 deterministic failures, got {len(det_failures)}")
        self.assertEqual(len(det_errors), 14, f"Expected exactly 14 deterministic errors, got {len(det_errors)}")
        self.assertEqual(len(cond_failures), 3, f"Expected exactly 3 conditional failures, got {len(cond_failures)}")

        for tid, entry in cond_failures.items():
            self.assertTrue(entry.get("reason_code"), f"Missing reason_code on {tid}")
            self.assertTrue(entry.get("reason"), f"Missing reason on {tid}")
            self.assertTrue(entry.get("owner"), f"Missing owner on {tid}")
            self.assertTrue(entry.get("introduced_at"), f"Missing introduced_at on {tid}")
            self.assertTrue(entry.get("review_by"), f"Missing review_by on {tid}")
            self.assertTrue(entry.get("remediation"), f"Missing remediation on {tid}")


if __name__ == "__main__":
    unittest.main()
