# SESSION 7.14 — NULL START TOKEN HOTFIX — PROOF REPORT

**Date:** 2026-08-01 · **Branch:** `hotfix-phase7-14-stop-exit-verification`
**Baseline:** `a70bdb0` · **Scope:** launcher process identity only · **Merged:** NO · **Acceptance tag:** NONE

Closes the open `NULL_RECORDED_START_TOKEN` audit finding and the Start-side defect that produces it.
Every claim below has a command that reproduces it.

---

## 1. What was actually wrong

The accepted stop-exit-verification hotfix (`4c5d362`) added a three-token identity gate. Every
consumer of the recorded token then tested it for **truthiness inline**, and each read a falsy token
as *"skip the check"* rather than *"cannot verify"*.

| # | Site | Baseline code | Effect of a null token |
|---|---|---|---|
| 0 | `_start_locked` | `token = self._start_token(pid)` → persisted, `READY` returned | **manufactures** the null token |
| 1 | `_pinned_identity` | `if not recorded: return None, handle_token, ev` | **authorizes termination** |
| 2 | `_clear_stale_pid` | `if rec.get(...) and token != rec.get(...)` | PID-reuse branch never runs |
| 3 | `status` | `if owned and rec.get(...)` | `launcher_owned: true`, unverified |

Site 0 is the root. Start held a `Popen` that **owns** the child and read that child's identity by
**raw PID** anyway — the same unpinned read the stop path had just been rewritten to eliminate. It
then persisted whatever came back, including `None`, and returned `SESSION7_14_LAUNCHER_READY`.

`4c5d362`'s commit message claimed *"any missing token … refuses"*. That was false for the recorded
token — the one the other two are compared against.

### 1.1 Measured harm on the baseline (not theoretical)

A real, unrelated, live Windows process whose PID was recorded with a null token:

```
readiness          : SESSION7_14_LAUNCHER_STOPPED          <-- reported SUCCESS
identity_verified  : True                                   <-- claimed VERIFIED
signalled          : True
terminate_requests : [GenerateConsoleCtrlEvent ok, TerminateProcess ok]
process_identity   : {"recorded_token_present": false,
                      "handle_token_matches_recorded": false,
                      "process_token_matches_recorded": false,
                      "authorized_by": "NO_RECORDED_TOKEN"}
alive AFTER stop   : False
exit code          : 1
VERDICT            : BYSTANDER KILLED
```

Two details the original audit did not record:

1. The launcher **had** the evidence that identity did not match — `handle_token_matches_recorded:
   false` and `process_token_matches_recorded: false` are in its own output — wrote it down, and
   terminated anyway.
2. The hard-path termination check reported `identity_verified: true`. It passes trivially when the
   recorded token is absent, because `expect_token` is derived from the **live handle**, so the
   termination handle was validated **against itself**. The third token check was circular in exactly
   the case where the first two had failed.

---

## 2. The fix

### 2.1 One validator, four sites

```python
def valid_identity_token(token):
    return isinstance(token, str) and bool(token.strip())
```

Presence and shape, deliberately **not** format. A `win-create-…`/`posix-start-…` format gate would
reject the accepted test seams (which legitimately emit `tok-4242`) and silently convert every
seam-driven stop into a refusal — a different bug wearing this one's clothes.

### 2.2 Start reads identity through the handle it already owns

New `process_start_token_from_popen(proc)` reads the creation token through `proc._handle` —
the only read that cannot describe a different process. Order: handle → raw-PID fallback → fail.

The fallback is correct where it applies: on **POSIX** the child stays unreaped for as long as the
`Popen` lives, so the kernel cannot recycle its PID and the `/proc` read is *already* pinned by the
same object. There is nothing to close there. On Windows the handle is now used.

### 2.3 Start fails closed

`START_TOKEN_READ_ATTEMPTS = 3` bounded retries absorb a transient API failure. On exhaustion:

* **no** PID record is written (nothing downstream can act on an unverifiable record);
* the child is stopped through `proc.kill()` — the object this launcher owns, so **no PID is
  re-resolved** and no identity gate is needed to reach it;
* readiness is `LAUNCHER_FAILED` / `CONSOLE_IDENTITY_UNREADABLE`, never `READY`;
* no browser is opened.

### 2.4 `_clear_stale_pid` separates three answers the baseline collapsed into one

| Condition | Baseline | Now |
|---|---|---|
| process gone | clear | clear (unchanged) |
| **recorded** token unusable | keep (unverifiable record stranded the owner) | clear, `reason=unverifiable_record` |
| **live** token unreadable | **cleared as `pid_reused`** (`None != recorded` is true) | **keep** — unreadable is not reused |
| live ≠ recorded | clear | clear (unchanged) |

The third row destroyed the record on precisely the reading that establishes nothing.

### 2.5 `status` verifies before it claims

Ownership is now a verified claim or it is not made, and `identity_verified` is exposed. This is what
prevents the one-session self-contradiction: Stop refusing while `status` still reports the same
record owned.

### 2.6 Owner copy — in the Stop console output, not the web panel

The panel is unreachable in exactly the situation the sentence describes.

> The toolkit was not stopped because the launcher could not confirm which process it is. Nothing on
> this computer was stopped. Close the toolkit window yourself, or end its task in Task Manager, then
> run Start-AMZ-Toolkit again.

> The toolkit could not be started safely: the launcher could not confirm which process it had just
> created, so it closed that process again. Nothing was left behind. Run Start-AMZ-Toolkit once more.

---

## 3. Verification

### 3.1 Failing-test-first

30 new tests were written against the **unfixed** module: **26 of 30 failed**. After the fix: 30/30 pass.

```
python -m unittest tests.test_phase7_14_owner_usability_pilot_readiness.TestIdentityTokenValidator \
  tests.test_phase7_14_owner_usability_pilot_readiness.TestNullTokenNeverAuthorizes \
  tests.test_phase7_14_owner_usability_pilot_readiness.TestUnverifiableRecordSweep \
  tests.test_phase7_14_owner_usability_pilot_readiness.TestStatusOwnershipIsVerified \
  tests.test_phase7_14_owner_usability_pilot_readiness.TestStartProducesAVerifiedToken \
  tests.test_phase7_14_owner_usability_pilot_readiness.TestStartTokenComesFromTheOwnedHandle \
  tests.test_phase7_14_owner_usability_pilot_readiness.TestNullTokenNeverReachesARealProcess
```

### 3.2 Real-process bystander proof, same probe as §1.1, fixed code

```
readiness          : SESSION7_14_LAUNCHER_STOP_REFUSED
error_code         : PROCESS_IDENTITY_UNPROVEN
signalled          : False
identity_verified  : False
terminate_requests : []                                    <-- nothing was asked of the OS
process_identity   : {"recorded_token_valid": false, "authorized_by": null}
alive AFTER stop   : True
VERDICT            : survived
```

`terminate_requests: []` makes "nothing was asked of the OS" **provable from the artifact**, not
inferred from the absence of a field.

### 3.3 Real end-to-end console cycle (not a seam)

Real 7.13 console, real spawn, port 8791:

```
start readiness    : SESSION7_14_LAUNCHER_READY      startup_seconds: 0.56
recorded token     : win-create-134299933208600311
identity_source    : popen_handle                   <-- handle-derived, not raw PID
status owned       : True | identity_verified: True
stop readiness     : SESSION7_14_LAUNCHER_STOPPED    exit_state: EXITED   stop_seconds: 3.25
record cleared     : True
```

### 3.4 Suites

| Suite | Result |
|---|---|
| Phase 7.14 file | **546 ran, 1 failure** |
| Full `unittest discover -s tests` | see §3.5 |

The single 7.14 failure is `test_199e_no_acceptance_tag_yet`, **permanently stale** — it asserts no
`phase7-14-*` acceptance tag exists, and three do, all predating this branch. Verified pre-existing
by stashing this change and re-running it: identical failure. Two prior audits recommended retiring
it; it is **not** retired here, because retiring a test in the same commit that changes the code it
guards is exactly the move an auditor should distrust.

### 3.5 In-place differential — `INCOMPLETE_ENVIRONMENT`, **NOT a canonical suite count**

> **Read this before quoting any number below.** Both runs were executed in an interpreter that does
> **not** satisfy `requirements.txt`: `cryptography>=42.0` is declared and was absent. 37 of the 41
> recorded problems are that one missing package. These figures are therefore valid **only** as a
> before/after comparison of this change, and must not be cited as "the full suite result".
> The canonical comparison is §3.6, taken after the environment was repaired.

Method: identical interpreter, identical working tree, identical `runs/T2/` workspace, **only the
two changed files swapped** via `git checkout <commit> -- <paths>`. Compared by exact
`(kind, module, test_name)` — totals alone are not evidence, since two runs can share a count and
share none of its members.

| | Baseline `a70bdb0` | Target `5a9b495` |
|---|---|---|
| Ran | 4681 | 4711 (+30 — exactly the new tests) |
| Problems | 41 | 41 |
| Outside the 7.14 module | 40 | 40 — **identical set** |
| …present at baseline only | — | **0** |
| …present at target only (would be regressions) | — | **0** |
| Inside the 7.14 module | `test_199e` | `test_199e` |

**Verdict: `BASELINE_EQUIVALENT_OUTSIDE_CHANGED_MODULE`. 7.14 regressions: none.**
+30 tests, +0 problems.

The `0` above counts **persistent** target-only nodes. For the one transient node a later
independent audit observed once under full-suite load, and why it is not a regression, see
*Known Windows loopback flakes — disclosure preserved* below.

Structural corroboration, independent of the run: an exhaustive search of every `.py`, `.ps1`,
`.bat`, `.json`, `.md` and `.js` in the repository finds `phase7_owner_launcher` referenced only by
the module itself, `tests/test_phase7_14_owner_usability_pilot_readiness.py`, and the three
`.ps1` launcher scripts (which the suite reads and hashes but never executes). No other test module
imports it or invokes it by subprocess, so no other module *can* be affected by this change.

#### The 41 pre-existing problems, and why none of them is this change

| Module | Count | Root cause, from the traceback |
|---|---|---|
| `test_phase7_9_connected_backup_recovery` | 36 errors + 1 fail | `EncryptionUnavailable: the 'cryptography' package is required for encryption and is not installed` |
| `test_instance_manager` | 2 fails | `dashboard did not become healthy within 30s` — the **other** supervisor + `dashboard/app.py` |
| `test_autostart_fallback` | 1 fail | same old install path |
| `test_phase7_14_…` | 1 fail | `test_199e`, the known-stale tag assertion |

`dashboard/app.py` failing to start is recorded here as **pre-existing retirement evidence only**.
It is not modified, not retired and not otherwise touched by this hotfix.

#### Environment defect this exposed (separate from the hotfix)

The project virtualenv did not satisfy the project's own requirements file:

```
python  ->  .venv\Scripts\python.exe   3.12.10    cryptography: MISSING
            system Python 3.12                    cryptography: 49.0.0
```

`cryptography>=42.0` is declared in both `pyproject.toml` and `requirements.txt`. Phase 7.9 was
accepted on 2026-07-23 recording "crypto 91/91", which is only reachable with the package present —
so that figure came from a different interpreter than the one `python` resolves to in this checkout.
**The regression gate quoted by prior acceptances therefore returns different answers depending on
which interpreter is invoked.** That is an environment/process defect, not a code defect, and it is
why §3.6 exists.

### 3.6 CANONICAL differential — repaired environment, equivalent fresh worktrees

The environment was repaired first (`pip install -r requirements.txt`), then the accepted baseline
and the target were run **sequentially** in two fresh worktrees that differ **only by commit**,
driven by one absolute interpreter path — never a bare `python`, which resolves per-shell and is
what produced two contradictory suite numbers in the first place.

**Environment of record**

| | |
|---|---|
| Python executable | `…\.venv\Scripts\python.exe` |
| Python version | 3.12.10 |
| `pip check` | exit 0 — "No broken requirements found." |
| `cryptography` | **50.0.0** (previously absent) |
| `requirements.txt` SHA-256 | `f9a83989c2b91710bb0d6b4b48266949dc88cdaed2b455bd4814337c47367397` |
| Collected tests | 4711, **0 collection errors** |
| Packages installed | 25 |

`cryptography` was the **only** missing requirement; `flask` and everything else were already
satisfied. Repairing it removed all 37 Phase 7.9 problems (`EncryptionUnavailable` count: 0).

**Comparison**

| | Baseline worktree `a70bdb0` | Target worktree `5a9b495` |
|---|---|---|
| Ran | 4679 | 4709 (+30 — exactly the new tests) |
| Problems | 20 | 20 |
| Outside the 7.14 module | 19 | 19 — **identical membership** |
| …baseline only / target only | — | **0 / 0** |
| Inside the 7.14 module | `test_199e` | `test_199e` |

**Verdict: `BASELINE_EQUIVALENT_OUTSIDE_CHANGED_MODULE`. 7.14 regressions: none.**

Remaining 20, identical on both sides: `test_session5d_certification` (7+1),
`test_backend_semantic_quality` (4), `test_backend_phrase_integrity` (3), `test_instance_manager`
(2), `test_autostart_fallback` (1), `test_offline_bootstrap` (1), `test_199e` (1).

> **This is a canonical COMPARISON, not a canonical pass count.** A worktree does not contain
> `runs/T2/` (gitignored) or the local install state, so 329 tests skip here against 4 in place, and
> the modules above fail for want of that workspace rather than for want of correct code. Worktree
> figures are only ever compared worktree-to-worktree. An absolute "the suite is green" number would
> need a separate in-place run on the repaired environment and must be reported as its own figure.

#### Known Windows loopback flakes — disclosure preserved

The `0` cells above, and both `BASELINE_EQUIVALENT_OUTSIDE_CHANGED_MODULE` verdicts, mean **zero
PERSISTENT target-only non-passing nodes** on the canonical exact-ID differential. They do **not**
mean that no node was ever observed once. One was, and it is recorded here rather than dropped.

| | |
|---|---|
| Node observed | `test_phase7_13_unified_owner_console.TestBody.test_52_request_size_bounded` |
| Documented sibling, same failure class | `test_phase7_4_owner_dashboard.HttpSecurity.test_post_to_unknown_endpoint_rejected` |
| Signature | `ConnectionAbortedError: [WinError 10053]` |
| Observation | appeared **once**, on the target, under full-suite load |
| Classification | **`PRE_EXISTING_ACCEPTED_WINDOWS_LOOPBACK_FLAKE`** |

Evidence that this is not a launcher regression:

* `production/phase7_unified_owner_console.py` and `tests/test_phase7_13_unified_owner_console.py`
  are **identical git blobs** at baseline and target — the code under test and the test itself are
  the same bytes, so a regression there is impossible by construction. The whole merge scope
  changes exactly two code files, and neither is the console;
* **12/12 isolated runs passed on both trees**;
* a **second full-suite sample on the target did not reproduce it**, and returned zero target-only
  non-passing nodes;
* prior **accepted** reports already record this same node and this same
  `ConnectionAbortedError` / `WinError 10053` class as a load-dependent loopback flake — one of
  them measuring it failing in isolation on *both* sides, not just the feature side.

Mechanism: the test POSTs an oversized body and expects HTTP 413. The server correctly rejects the
body and closes; when it closes before the client has finished writing, Windows aborts the
connection and the client never reads the response. That is pure timing, which is why it surfaces
under full-suite load and vanishes in isolation.

**This suite is not green and is not reported as green** — the known non-passing tests listed above
remain non-passing. A single appearance of either node should be read as this flake class and
confirmed the cheap way first: identical blobs, then isolation, then a second full-suite sample.

### 3.7 `dashboard/app.py` — pre-existing retirement evidence ONLY

`test_instance_manager` and `test_autostart_fallback` fail with *"dashboard did not become healthy
within 30s"* on an environment where `flask` **is** installed and `pip check` is clean. So this is
not a dependency gap: the app does not come up. It fails identically at baseline and target.

Recorded as evidence for the retirement decision already deferred to after the pilot.
**`dashboard/app.py`, `core/instance_manager.py` and the autostart path were not modified, not
retired and not otherwise touched by this hotfix.**

---

## 4. Accepted assertions changed (disclosed)

One accepted-baseline test was modified, deliberately:

`test_h04_identity_refusal_wording_is_accurate` froze the exact sentence *"could not safely verify
the process identity."* — true and completely unactionable, printed in the one situation where the
web console cannot be used to act on it. The remediation contract required truthful recovery copy, so
the sentence changed and the assertion follows it. **The machine contract is untouched**: `readiness`
and `error_code` keep their accepted values, asserted in the same test.

No other accepted assertion was weakened, deleted or skipped.

---

## 5. Invariant this hotfix is accountable to

> A missing, null, blank, malformed or unreadable token never authorizes a signal, a termination, an
> ownership claim, or a successful Start.

## 6. What this hotfix does NOT do

* It does not touch `dashboard/app.py`, `core/instance_manager.py`, or the duplicate-supervisor
  question — deferred, correctly, until after the pilot.
* It does not change what Start spawns, what Stop signals on the verified path, or any Amazon
  boundary. Seller Central counters remain 0 on every path.
* It does not retire `test_199e_no_acceptance_tag_yet`.
* It adds no new readiness state: `CONSOLE_IDENTITY_UNREADABLE` is an `error_code` under the existing
  `LAUNCHER_FAILED`.
