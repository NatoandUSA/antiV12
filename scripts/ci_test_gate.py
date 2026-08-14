#!/usr/bin/env python3
"""
scripts/ci_test_gate.py — Deterministic Exact-Baseline CI Regression Gate for AMZ Launch OS.

Runs the canonical test suite, provides 100% transparent test execution output,
and enforces a strict deterministic exact-baseline contract:
  - actual_failures MUST equal accepted_failures exactly.
  - actual_errors MUST equal accepted_errors exactly.

Any divergence fails CI (exit 1):
  1. New failure or error (target-only regression).
  2. Disappeared failure or error (baseline drift / requires explicit reviewed baseline prune).
  3. Failure <-> Error category swap.
"""
import sys
import os
import json
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for d in ("core", "listing", "dashboard", "production", "compliance", "research", "creative", "economics"):
    p = os.path.join(ROOT, d)
    if p not in sys.path:
        sys.path.insert(0, p)

BASELINE_PATH = os.path.join(ROOT, "tests", "accepted_baseline_failures.json")


def _normalize_id(test_id):
    """Normalize test ID to 'module.Class.method'."""
    parts = test_id.split(".")
    if len(parts) >= 4 and parts[0] == "tests":
        return ".".join(parts[1:])
    return test_id


class BaselineAwareTestResult(unittest.TextTestResult):
    def __init__(self, stream, descriptions, verbosity):
        super().__init__(stream, descriptions, verbosity)
        self.recorded_failures = []
        self.recorded_errors = []
        self.recorded_skips = []
        self.recorded_successes = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.recorded_successes.append(_normalize_id(test.id()))

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.recorded_failures.append((_normalize_id(test.id()), err))

    def addError(self, test, err):
        super().addError(test, err)
        self.recorded_errors.append((_normalize_id(test.id()), err))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.recorded_skips.append((_normalize_id(test.id()), reason))


def load_baseline(baseline_path=None):
    path = baseline_path or BASELINE_PATH
    if not os.path.exists(path):
        return set(), set(), {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        acc_failures = {item["test_id"]: item.get("reason", "") for item in data.get("accepted_failures", [])}
        acc_errors = {item["test_id"]: item.get("reason", "") for item in data.get("accepted_errors", [])}
        reasons = {**acc_failures, **acc_errors}
        return set(acc_failures.keys()), set(acc_errors.keys()), reasons
    except Exception as e:
        print(f"[CI-GATE] Warning: Could not load accepted baseline: {e}")
        return set(), set(), {}


def evaluate_gate_results(actual_failures, actual_errors, accepted_failures, accepted_errors):
    """
    Deterministic evaluation:
    Returns (is_exact_match: bool, diff: dict)
    """
    actual_fail_set = set(actual_failures)
    actual_err_set = set(actual_errors)
    acc_fail_set = set(accepted_failures)
    acc_err_set = set(accepted_errors)

    unexpected_failures = actual_fail_set - acc_fail_set
    unexpected_errors = actual_err_set - acc_err_set

    fixed_failures = acc_fail_set - actual_fail_set
    fixed_errors = acc_err_set - actual_err_set

    swapped_fail_to_err = acc_fail_set & actual_err_set
    swapped_err_to_fail = acc_err_set & actual_fail_set

    is_exact_match = (actual_fail_set == acc_fail_set) and (actual_err_set == acc_err_set)

    diff = {
        "is_exact_match": is_exact_match,
        "unexpected_failures": sorted(unexpected_failures),
        "unexpected_errors": sorted(unexpected_errors),
        "fixed_failures": sorted(fixed_failures),
        "fixed_errors": sorted(fixed_errors),
        "swapped_fail_to_err": sorted(swapped_fail_to_err),
        "swapped_err_to_fail": sorted(swapped_err_to_fail),
    }
    return is_exact_match, diff


def main():
    print("=" * 80)
    print("AMZ Launch OS — Canonical Test Suite & Exact-Baseline Regression Gate")
    print(f"Repository Root: {ROOT}")
    print(f"Python Version : {sys.version}")
    print("=" * 80)

    acc_failures, acc_errors, reasons = load_baseline()
    print(f"[CI-GATE] Loaded accepted baseline: {len(acc_failures)} failures, {len(acc_errors)} errors.")

    loader = unittest.TestLoader()
    suite = loader.discover(os.path.join(ROOT, "tests"), pattern="test_*.py")
    total_discovered = suite.countTestCases()
    print(f"[CI-GATE] Discovered {total_discovered} formal test cases across test suite.\n")

    runner = unittest.TextTestRunner(resultclass=BaselineAwareTestResult, verbosity=2)
    start_time = time.perf_counter()
    result = runner.run(suite)
    duration = time.perf_counter() - start_time

    actual_failed_ids = {t_id for t_id, _ in result.recorded_failures}
    actual_error_ids = {t_id for t_id, _ in result.recorded_errors}

    is_exact_match, diff = evaluate_gate_results(
        actual_failed_ids, actual_error_ids, acc_failures, acc_errors
    )

    print("\n" + "=" * 80)
    print("CI TEST GATE REPORT")
    print("=" * 80)
    print(f"Total Discovered : {total_discovered}")
    print(f"Total Run        : {result.testsRun}")
    print(f"Passed           : {len(result.recorded_successes)}")
    print(f"Skipped          : {len(result.recorded_skips)}")
    print(f"Failures         : {len(actual_failed_ids)} (Accepted baseline: {len(acc_failures)})")
    print(f"Errors           : {len(actual_error_ids)} (Accepted baseline: {len(acc_errors)})")
    print(f"Duration         : {duration:.2f}s")
    print("-" * 80)

    if actual_failed_ids or actual_error_ids:
        print("\n[ACTUAL NON-PASSING TESTS]")
        for tid in sorted(actual_failed_ids | actual_error_ids):
            status = "FAIL" if tid in actual_failed_ids else "ERROR"
            reason = reasons.get(tid, "Unspecified reason")
            print(f"  - [{status}] {tid}")
            print(f"          Baseline Reason: {reason}")

    if not is_exact_match:
        print("\n" + "!" * 80)
        print("GATE FAILURE: EXACT-BASELINE CONTRACT VIOLATION")
        print("!" * 80)

        if diff["unexpected_failures"]:
            print("\n[REGRESSION] Unexpected Failures:")
            for tid in diff["unexpected_failures"]:
                print(f"  + {tid}")

        if diff["unexpected_errors"]:
            print("\n[REGRESSION] Unexpected Errors:")
            for tid in diff["unexpected_errors"]:
                print(f"  + {tid}")

        if diff["fixed_failures"] or diff["fixed_errors"]:
            print("\n[BASELINE DRIFT / REMOVAL] Baseline tests that did not reproduce:")
            for tid in diff["fixed_failures"]:
                print(f"  - [FAILURE REMOVED/PASSING] {tid}")
            for tid in diff["fixed_errors"]:
                print(f"  - [ERROR REMOVED/PASSING] {tid}")
            print("  Note: If a baseline failure was intentionally fixed or pruned,")
            print("        update tests/accepted_baseline_failures.json in a reviewed successor commit.")

        if diff["swapped_fail_to_err"] or diff["swapped_err_to_fail"]:
            print("\n[CATEGORY SWAP] Failure <-> Error Class Mismatches:")
            for tid in diff["swapped_fail_to_err"]:
                print(f"  ~ {tid} (expected FAIL, became ERROR)")
            for tid in diff["swapped_err_to_fail"]:
                print(f"  ~ {tid} (expected ERROR, became FAIL)")

        print("\nCI Gate Verdict: REJECTED (Exact baseline match required).")
        sys.exit(1)

    print("\n" + "*" * 80)
    print("GATE SUCCESS: EXACT BASELINE MATCH VERIFIED")
    print("actual_failures == accepted_failures AND actual_errors == accepted_errors.")
    print("Zero target-only regressions and zero silent baseline drift.")
    print("CI Gate Verdict: ACCEPTED FOR RELEASE PIPELINE.")
    print("*" * 80)
    sys.exit(0)


if __name__ == "__main__":
    main()
