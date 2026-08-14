#!/usr/bin/env python3
"""
scripts/ci_test_gate.py — Deterministic CI Regression Gate for AMZ Launch OS.

Runs the canonical test suite, provides 100% transparent test execution output,
and enforces a strict regression gate:
  - Any unexpected failure or error (target-only regression) causes CI to FAIL (exit 1).
  - Pre-existing, accepted environment baseline issues (e.g. uncommitted paid fixtures,
    headless Windows subprocess timings) are reported transparently and allowed (exit 0).
  - Never hides failures: every failure and error is printed in full.
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


def load_baseline():
    if not os.path.exists(BASELINE_PATH):
        return set(), set(), {}
    try:
        with open(BASELINE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        acc_failures = {item["test_id"]: item.get("reason", "") for item in data.get("accepted_failures", [])}
        acc_errors = {item["test_id"]: item.get("reason", "") for item in data.get("accepted_errors", [])}
        reasons = {**acc_failures, **acc_errors}
        return set(acc_failures.keys()), set(acc_errors.keys()), reasons
    except Exception as e:
        print(f"[CI-GATE] Warning: Could not load accepted baseline: {e}")
        return set(), set(), {}


def main():
    print("=" * 80)
    print("AMZ Launch OS — Canonical Test Suite & Regression Gate")
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

    unexpected_failures = actual_failed_ids - acc_failures
    unexpected_errors = actual_error_ids - acc_errors

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
        print("\n[ACCEPTED BASELINE NON-PASSING TESTS]")
        for tid in sorted(actual_failed_ids | actual_error_ids):
            status = "FAIL" if tid in actual_failed_ids else "ERROR"
            reason = reasons.get(tid, "Unspecified baseline reason")
            print(f"  - [{status}] {tid}")
            print(f"          Reason: {reason}")

    # Check for fixed baseline tests
    fixed_failures = acc_failures - actual_failed_ids
    fixed_errors = acc_errors - actual_error_ids
    if fixed_failures or fixed_errors:
        print("\n[NOTICE: Tests in accepted baseline that now PASS]")
        for tid in sorted(fixed_failures | fixed_errors):
            print(f"  - [NOW PASSING] {tid}")
        print("  -> Baseline can be pruned in the next release certification pass.")

    # Enforcement
    if unexpected_failures or unexpected_errors:
        print("\n" + "!" * 80)
        print("GATE FAILURE: TARGET-ONLY REGRESSIONS DETECTED")
        print("!" * 80)
        if unexpected_failures:
            print("\nUnexpected Failures:")
            for tid in sorted(unexpected_failures):
                print(f"  - {tid}")
        if unexpected_errors:
            print("\nUnexpected Errors:")
            for tid in sorted(unexpected_errors):
                print(f"  - {tid}")
        print("\nCI Gate Verdict: REJECTED (New defects must be fixed).")
        sys.exit(1)

    print("\n" + "*" * 80)
    print("GATE SUCCESS: ZERO TARGET-ONLY REGRESSIONS")
    print("All tests passed or matched the accepted baseline exactly.")
    print("CI Gate Verdict: ACCEPTED FOR RELEASE PIPELINE.")
    print("*" * 80)
    sys.exit(0)


if __name__ == "__main__":
    main()
