#!/usr/bin/env python3
"""
scripts/ci_test_gate.py — Deterministic CI Regression Gate V2 for AMZ Launch OS.

Gate V2 Architecture:
  1. deterministic_failures (Exact Match):
     - Must reproduce identically on all hosts.
     - Unexpected appearance, disappearance, or category swap -> CI FAIL (exit 1).
  2. deterministic_errors (Exact Match):
     - Must reproduce identically on all hosts (e.g. uncommitted paid T2 fixtures).
     - Unexpected appearance, disappearance, or category swap -> CI FAIL (exit 1).
  3. environment_conditional_failures (Host/Path/Timing Flake Guard):
     - Allowed outcomes: PASS or FAIL.
     - ERROR -> CI FAIL (exit 1).
     - Must have complete metadata: test_id, reason_code, reason, owner, introduced_at, review_by, remediation.
     - Expired review_by date -> CI FAIL (exit 1).
  4. Any failure/error outside baseline sets -> CI FAIL (exit 1).
"""
import sys
import os
import json
import time
import datetime
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for d in ("core", "listing", "dashboard", "production", "compliance", "research", "creative", "economics"):
    p = os.path.join(ROOT, d)
    if p not in sys.path:
        sys.path.insert(0, p)

BASELINE_PATH = os.path.join(ROOT, "tests", "accepted_baseline_failures.json")

MANDATORY_CONDITIONAL_FIELDS = (
    "test_id",
    "reason_code",
    "reason",
    "owner",
    "introduced_at",
    "review_by",
    "remediation",
)


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
        return {"deterministic_failures": {}, "deterministic_errors": {}, "environment_conditional_failures": {}, "schema_errors": [f"File not found: {path}"]}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        det_failures = {item["test_id"]: item.get("reason", "") for item in data.get("deterministic_failures", [])}
        det_errors = {item["test_id"]: item.get("reason", "") for item in data.get("deterministic_errors", [])}
        cond_failures = {}
        schema_errors = []

        for item in data.get("environment_conditional_failures", []):
            tid = item.get("test_id")
            if not tid:
                schema_errors.append("Conditional failure missing 'test_id'")
                continue
            # Validate mandatory fields
            missing = [k for k in MANDATORY_CONDITIONAL_FIELDS if not item.get(k)]
            if missing:
                schema_errors.append(f"Conditional test {tid} missing required fields: {missing}")
            cond_failures[tid] = item

        return {
            "version": data.get("version", "2.0"),
            "deterministic_failures": det_failures,
            "deterministic_errors": det_errors,
            "environment_conditional_failures": cond_failures,
            "schema_errors": schema_errors,
        }
    except Exception as e:
        return {"deterministic_failures": {}, "deterministic_errors": {}, "environment_conditional_failures": {}, "schema_errors": [f"Parse error: {e}"]}


def evaluate_gate_results(actual_failures, actual_errors, baseline_data, as_of_date=None):
    """
    Gate V2 Evaluation Logic:
    Returns (is_passed: bool, diff: dict)
    """
    now_date = as_of_date or datetime.date.today()
    if isinstance(now_date, str):
        now_date = datetime.date.fromisoformat(now_date)

    actual_fail_set = set(actual_failures)
    actual_err_set = set(actual_errors)

    det_fail_set = set(baseline_data.get("deterministic_failures", {}).keys())
    det_err_set = set(baseline_data.get("deterministic_errors", {}).keys())
    cond_fail_dict = baseline_data.get("environment_conditional_failures", {})
    cond_fail_set = set(cond_fail_dict.keys())

    schema_errors = list(baseline_data.get("schema_errors", []))
    expired_conditional_entries = []

    # Check conditional expiry
    for tid, entry in cond_fail_dict.items():
        rb = entry.get("review_by")
        if rb:
            try:
                rb_date = datetime.date.fromisoformat(rb)
                if now_date > rb_date:
                    expired_conditional_entries.append((tid, rb, f"Expired on {rb} (current: {now_date.isoformat()})"))
            except Exception:
                schema_errors.append(f"Conditional test {tid} has invalid review_by date: {rb}")

    # 1. Errors evaluation (MUST exactly match deterministic_errors)
    unexpected_errors = actual_err_set - det_err_set
    fixed_deterministic_errors = det_err_set - actual_err_set

    # 2. Check if any conditional test produced an ERROR (FORBIDDEN in Gate V2)
    conditional_produced_errors = cond_fail_set & actual_err_set

    # 3. Failures evaluation
    # Conditional failures that actually failed (ALLOWED)
    conditional_reproduced_failures = cond_fail_set & actual_fail_set
    conditional_passed = cond_fail_set - actual_fail_set

    # Residual actual failures (after removing allowable conditional failures)
    residual_actual_failures = actual_fail_set - cond_fail_set

    unexpected_failures = residual_actual_failures - det_fail_set
    fixed_deterministic_failures = det_fail_set - residual_actual_failures

    # Category swaps
    swapped_fail_to_err = det_fail_set & actual_err_set
    swapped_err_to_fail = det_err_set & actual_fail_set

    is_passed = (
        len(unexpected_failures) == 0
        and len(unexpected_errors) == 0
        and len(fixed_deterministic_failures) == 0
        and len(fixed_deterministic_errors) == 0
        and len(conditional_produced_errors) == 0
        and len(swapped_fail_to_err) == 0
        and len(swapped_err_to_fail) == 0
        and len(schema_errors) == 0
        and len(expired_conditional_entries) == 0
    )

    diff = {
        "is_passed": is_passed,
        "unexpected_failures": sorted(unexpected_failures),
        "unexpected_errors": sorted(unexpected_errors),
        "fixed_deterministic_failures": sorted(fixed_deterministic_failures),
        "fixed_deterministic_errors": sorted(fixed_deterministic_errors),
        "conditional_produced_errors": sorted(conditional_produced_errors),
        "conditional_reproduced_failures": sorted(conditional_reproduced_failures),
        "conditional_passed": sorted(conditional_passed),
        "swapped_fail_to_err": sorted(swapped_fail_to_err),
        "swapped_err_to_fail": sorted(swapped_err_to_fail),
        "schema_errors": sorted(schema_errors),
        "expired_conditional_entries": sorted(expired_conditional_entries),
    }
    return is_passed, diff


def main():
    print("=" * 80)
    print("AMZ Launch OS — Canonical Test Suite & Deterministic Regression Gate V2")
    print(f"Repository Root: {ROOT}")
    print(f"Python Version : {sys.version}")
    print("=" * 80)

    baseline_data = load_baseline()
    det_failures = baseline_data["deterministic_failures"]
    det_errors = baseline_data["deterministic_errors"]
    cond_failures = baseline_data["environment_conditional_failures"]

    print(f"[CI-GATE V2] Loaded baseline:")
    print(f"  - Deterministic Failures  : {len(det_failures)}")
    print(f"  - Deterministic Errors    : {len(det_errors)}")
    print(f"  - Environment Conditional : {len(cond_failures)}")

    loader = unittest.TestLoader()
    suite = loader.discover(os.path.join(ROOT, "tests"), pattern="test_*.py")
    total_discovered = suite.countTestCases()
    print(f"[CI-GATE V2] Discovered {total_discovered} formal test cases across test suite.\n")

    runner = unittest.TextTestRunner(resultclass=BaselineAwareTestResult, verbosity=2)
    start_time = time.perf_counter()
    result = runner.run(suite)
    duration = time.perf_counter() - start_time

    actual_failed_ids = {t_id for t_id, _ in result.recorded_failures}
    actual_error_ids = {t_id for t_id, _ in result.recorded_errors}

    is_passed, diff = evaluate_gate_results(
        actual_failed_ids, actual_error_ids, baseline_data
    )

    print("\n" + "=" * 80)
    print("CI TEST GATE V2 REPORT")
    print("=" * 80)
    print(f"Total Discovered : {total_discovered}")
    print(f"Total Run        : {result.testsRun}")
    print(f"Passed           : {len(result.recorded_successes)}")
    print(f"Skipped          : {len(result.recorded_skips)}")
    print(f"Actual Failures  : {len(actual_failed_ids)}")
    print(f"Actual Errors    : {len(actual_error_ids)}")
    print(f"Duration         : {duration:.2f}s")
    print("-" * 80)

    # Detailed listing of actual non-passing tests
    if actual_failed_ids or actual_error_ids:
        print("\n[ACTUAL NON-PASSING TESTS]")
        for tid in sorted(actual_failed_ids | actual_error_ids):
            status = "FAIL" if tid in actual_failed_ids else "ERROR"
            cat = "DETERMINISTIC" if tid in det_failures or tid in det_errors else ("CONDITIONAL" if tid in cond_failures else "UNACCEPTED")
            print(f"  - [{status}] [{cat}] {tid}")

    # Environment Conditional Summary
    if cond_failures:
        print("\n[ENVIRONMENT-CONDITIONAL STATUS]")
        for tid in sorted(cond_failures.keys()):
            entry = cond_failures[tid]
            outcome = "FAIL" if tid in actual_failed_ids else ("ERROR" if tid in actual_error_ids else "PASS")
            print(f"  - {tid}: {outcome}")
            print(f"      Reason Code: {entry.get('reason_code')} | Owner: {entry.get('owner')} | Review By: {entry.get('review_by')}")
            print(f"      Remediation: {entry.get('remediation')}")

    if not is_passed:
        print("\n" + "!" * 80)
        print("GATE FAILURE: GATE V2 CONTRACT VIOLATION")
        print("!" * 80)

        if diff["unexpected_failures"]:
            print("\n[REGRESSION] Unexpected Failures:")
            for tid in diff["unexpected_failures"]:
                print(f"  + {tid}")

        if diff["unexpected_errors"]:
            print("\n[REGRESSION] Unexpected Errors:")
            for tid in diff["unexpected_errors"]:
                print(f"  + {tid}")

        if diff["conditional_produced_errors"]:
            print("\n[FORBIDDEN] Conditional Tests that Produced ERROR:")
            for tid in diff["conditional_produced_errors"]:
                print(f"  ! {tid} (conditional tests are only permitted PASS or FAIL)")

        if diff["fixed_deterministic_failures"] or diff["fixed_deterministic_errors"]:
            print("\n[DETERMINISTIC BASELINE DRIFT] Deterministic baseline tests that did not reproduce:")
            for tid in diff["fixed_deterministic_failures"]:
                print(f"  - [DETERMINISTIC FAILURE MISSING] {tid}")
            for tid in diff["fixed_deterministic_errors"]:
                print(f"  - [DETERMINISTIC ERROR MISSING] {tid}")

        if diff["swapped_fail_to_err"] or diff["swapped_err_to_fail"]:
            print("\n[CATEGORY SWAP] Failure <-> Error Class Mismatches:")
            for tid in diff["swapped_fail_to_err"]:
                print(f"  ~ {tid} (expected FAIL, became ERROR)")
            for tid in diff["swapped_err_to_fail"]:
                print(f"  ~ {tid} (expected ERROR, became FAIL)")

        if diff["schema_errors"]:
            print("\n[SCHEMA ERROR] Baseline JSON Schema Violations:")
            for err in diff["schema_errors"]:
                print(f"  x {err}")

        if diff["expired_conditional_entries"]:
            print("\n[POLICY VIOLATION] Expired Conditional Entries:")
            for tid, exp, msg in diff["expired_conditional_entries"]:
                print(f"  x {tid}: {msg}")

        print("\nCI Gate Verdict: REJECTED (Gate V2 contract violation).")
        sys.exit(1)

    print("\n" + "*" * 80)
    print("GATE SUCCESS: GATE V2 EXACT-BASELINE CONTRACT VERIFIED")
    print("Deterministic failures/errors matched 100%; environment conditionals in valid state.")
    print("Zero target-only regressions and zero uncontrolled baseline drift.")
    print("CI Gate Verdict: ACCEPTED FOR RELEASE PIPELINE.")
    print("*" * 80)
    sys.exit(0)


if __name__ == "__main__":
    main()
