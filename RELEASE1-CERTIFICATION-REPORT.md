# Release 1 Certification Report — Session 5D

- **Overall status:** `RELEASE1_BLOCKED`
- **Mandatory gates PASS:** 15/17
- **Blocked:** B, H
- **Failed:** none
- **Loopback test port:** 5057
- **Run window:** 2026-07-17T14:28:25 → 2026-07-17T14:30:44

All mutable state ran in an isolated `%TEMP%` workspace via `AMZ_FBM_HOME`; the owner's real install, tasks, shortcuts, repository, and `runs/` were never modified.

## Gate results

| Gate | Name | Status |
| --- | --- | --- |
| A | Isolated certification workspace | **PASS** |
| B | Fresh venv + offline editable install + CLI resolution | **BLOCKED** |
| C | install-local (idempotent) + config/data preservation | **PASS** |
| D | Live start/health/status/doctor/second-start/restart/stop | **PASS** |
| E | open: refuses when unhealthy; loopback-only when healthy | **PASS** |
| F | Stale-state recovery: dead/reused/corrupt/partial + bounded health | **PASS** |
| G | Unknown port owner preserved; toolkit refuses to start on it | **PASS** |
| H | Live Task Scheduler create/query/delete (current-user, no-admin) | **BLOCKED** |
| I | Live current-user shortcuts create/query/remove | **PASS** |
| J | Offline network denial: full local workflow, zero external attempts | **PASS** |
| K | T2 twice-deterministic from immutable inputs | **PASS** |
| L | Single authoritative output package; deprecated not current | **PASS** |
| M | Uninstall preserves all business data; idempotent; no unknown kill | **PASS** |
| N | Reinstall after uninstall; start healthy; data intact; offline | **PASS** |
| O | No-admin: process is not elevated; no elevation is required | **PASS** |
| P | Security source scan: no prohibited runtime behavior | **PASS** |
| U | Full unittest suite | **PASS** |

## Blocking / failing gates and remediation

- **Gate B (BLOCKED) — Fresh venv + offline editable install + CLI resolution**

  CLI/version/offline-import all PASS on the installed package, but a fresh fully-offline editable build is BLOCKED: setuptools (PEP 517 backend) is absent and cannot be fetched offline. Remediation: install setuptools once (online), then offline reinstall works with --no-build-isolation.
- **Gate H (BLOCKED) — Live Task Scheduler create/query/delete (current-user, no-admin)**

  live schtasks /Create denied by this environment — cannot certify live. Root cause: the machine's sole account runs a UAC-filtered token in which Administrators is deny-only, so creating a scheduled task needs elevation, which the no-admin safety rule forbids. Remediation: run `amz-fbm autostart enable` once from an elevated terminal, or use a standard-user Windows profile where a current-user ONLOGON/LIMITED task needs no elevation.

## Interpretation

Every gate that could be exercised live PASSED, including offline network denial (zero external attempts), loopback-only binding, single-instance identity safety, unknown-port preservation, T2 twice-determinism, uninstall data preservation, and the full unit-test suite. The blocked gates are environment/packaging facts on this specific machine, not code defects, and each has a concrete remediation above.
