# Phase 7.14 — Stop-failure owner message hotfix

**Branch:** `hotfix-phase7-14-stop-owner-message`
**Accepted baseline:** `b3e357e27e60ff306d861f13a803a8a1f009817b`
(tag `phase7-14-owner-usability-pilot-readiness-accepted-b3e357e`, not modified, not moved)
**Checkpoint:** `phase7-14-stop-owner-message-hotfix-checkpoint-b3e357e`
**Fix commit:** `fa203bf`
**Scope:** owner-facing text on the launcher stop path. Nothing else.

---

## 1. The defect

`Stop-AMZ-Toolkit` told the owner the wrong thing when a stop failed:

```
readiness=SESSION7_14_LAUNCHER_FAILED
error_code=CONSOLE_DID_NOT_STOP

The toolkit could not be started. See the launcher log for the recorded reason.
```

The accepted baseline mapped owner text from the **readiness state alone**:

```python
def _owner_message(readiness, code, detail):
    base = _OWNER_MESSAGES.get(readiness, "")
```

`SESSION7_14_LAUNCHER_FAILED` is shared by the start path (`CONSOLE_SPAWN_FAILED`,
`CONSOLE_EXITED_DURING_STARTUP`) and the stop path (`CONSOLE_DID_NOT_STOP`). One state, one
sentence — so a stop timeout described a start failure. The `error_code` was always correct; only
the sentence named the wrong operation.

The independent Phase 7.14 acceptance audit recorded this as non-blocking observation 1 in §84.

A second, related inaccuracy in the same family is fixed here: all three
`SESSION7_14_LAUNCHER_STOP_REFUSED` outcomes shared one sentence — *"Stop refused: the recorded
process is not the console this launcher started"* — which is wrong for `PROCESS_IDENTITY_UNPROVEN`.
In that case the launcher **cannot prove** the identity; it does not know the process is different.

---

## 2. The fix

Owner text on the stop path is now selected by the canonical `error_code`, then by readiness.

| `error_code` | readiness (unchanged) | owner-facing sentence |
|---|---|---|
| `CONSOLE_DID_NOT_STOP` | `SESSION7_14_LAUNCHER_FAILED` | The toolkit did not stop within the allowed time. |
| `PROCESS_IDENTITY_UNPROVEN` | `SESSION7_14_LAUNCHER_STOP_REFUSED` | The toolkit was not stopped because the launcher could not safely verify the process identity. |
| `PID_REUSED_BY_ANOTHER_PROCESS` | `SESSION7_14_LAUNCHER_STOP_REFUSED` | The process was not stopped because it was not started by this launcher. |
| `NOT_LAUNCHER_OWNED` | `SESSION7_14_LAUNCHER_STOP_REFUSED` | The process was not stopped because it was not started by this launcher. |
| any other stop failure | `SESSION7_14_LAUNCHER_FAILED` | The toolkit could not be stopped. |

Each sentence is followed by a short, factual second clause (what else was affected, or where the
reason is recorded). The full strings are in `_STOP_OWNER_MESSAGES` / `STOP_FAILED_MESSAGE`.

### Canonical codes are preserved separately

`readiness` and `error_code` keep the exact values the accepted baseline records. They live in their
own result fields; the owner sentence lives in `owner_message`. A regression test asserts the code
string never appears inside the owner sentence, so the two layers cannot merge by accident.

### Start and Open cannot change, by construction

The stop table is consulted **only** when `phase="stop"`:

```python
def _owner_message(readiness, code, detail, phase=None):
    if phase == "stop":
        specific = _STOP_OWNER_MESSAGES.get(code)
        if specific:
            return specific
        if readiness == LAUNCHER_FAILED:
            return STOP_FAILED_MESSAGE
    base = _OWNER_MESSAGES.get(readiness, "")
    ...
```

`_OWNER_MESSAGES` — the accepted baseline table — is not edited at all. `LAUNCHER_FAILED` still maps
to *"The toolkit could not be started."* for the start path.

### Diff

```
production/phase7_owner_launcher.py                |  37 +   8 -
tests/test_phase7_14_owner_usability_pilot_readiness.py | 224 +   0 -
2 files changed, 261 insertions(+), 8 deletions(-)
```

The 8 deleted production lines are the 7 stop-path `_owner_message(...)` call sites gaining
`phase="stop"`, plus the old function signature. No other production file is touched.

---

## 3. What was deliberately NOT changed

Stop behaviour is byte-for-byte the same. It still:

- signals **one** recorded PID, only when the recorded `process_start_token` still matches;
- checks *unprovable* identity **before** *mismatched* identity, so an unreadable identity is never
  misreported as PID reuse;
- refuses PID reuse, refuses an unprovable identity, and refuses a console this launcher did not
  start — signalling nothing in all three cases;
- escalates only from a polite signal to terminating that same one verified PID, after a bounded
  grace window.

No process-name matching, no `taskkill`, no `Stop-Process`, no `Get-Process`, no `psutil`, no
process-tree kill, no "kill all Python". Stop was not made more aggressive in any way.

Also untouched: Start behaviour, Open behaviour, the fixed launch command, port handling (fixed
8780, no automatic reselection), browser-health ordering (browser only after `/api/v1/health`
reports ready), the dashboard, next-action guidance, and the permanent Amazon boundary — no Seller
Central, no seller sign-in, no seller or advertising API, every seller-account counter still a
constant zero.

### `process_alive()` is out of scope

The audit's second observation — `OpenProcess` succeeds for a terminated process while any handle to
it stays open in the *calling* process — is **not** addressed here. The shipped launcher runs one
command per process, where the condition does not arise (proved again in §5.3: a real
separate-process Start→Stop stops cleanly and releases the PID). The auditor classified it
non-blocking. It belongs in the **pilot / v1.0 backlog**, not in a text hotfix.

---

## 4. Tests

21 focused regression tests added as `TestStopOwnerMessage` in the existing Phase 7.14 file, in the
repository's unittest style. Every test is offline and drives the real stop paths through the
launcher's injected seams (health probe, port probe, spawn, browser, process identity, clock).

**The tests were verified against the unfixed baseline.** A regression test that passes on the buggy
code proves nothing, so the new file was copied into a fresh `b3e357e` worktree and run there:

| | accepted baseline `b3e357e` | hotfix `fa203bf` |
|---|---|---|
| tests that catch the defect | **6 FAIL + 2 ERROR** | 21 PASS |
| control tests | 13 PASS | 13 PASS |

The 8 failures are the defect. The 13 controls passing on **both** sides is what proves the
already-stopped, successful-stop, Start, Open, identity-protection and no-broad-termination
behaviours are genuinely unchanged rather than merely re-asserted.

Baseline failure evidence, verbatim:

```
AssertionError: 'stopped' not found in 'The toolkit could not be started. See the launcher
log for the recorded reason.' : SESSION7_14_LAUNCHER_FAILED

AssertionError: 'The toolkit was not stopped because the launcher could not safely verify the
process identity.' not found in 'Stop refused: the recorded process is not the console this
launcher started, so nothing was stopped.'
```

| # | Required proof | Test | Baseline |
|---|---|---|---|
| 1 | Stop timeout never says "started" | `test_h01`, `test_h01b` | FAIL |
| 2 | Stop failure uses the verb "stopped" | `test_h02`, `test_h02b` | FAIL / ERROR |
| 3 | Timeout wording accurate | `test_h03` | FAIL |
| 4 | Identity-refusal wording accurate | `test_h04` | FAIL |
| 5 | Unrelated-process wording accurate | `test_h05` | FAIL |
| 6 | Already-stopped still accurate | `test_h06` | pass (control) |
| 7 | Successful Stop unchanged | `test_h07` | pass (control) |
| 8 | Start messages unchanged | `test_h08`, `h08b`, `h08c`, `h08d` | pass (control) |
| 9 | Open messages unchanged | `test_h09`, `h09b` | pass (control) |
| 10 | Identity protections unchanged | `test_h10`, `h10b`, `h10c` | pass (control) |
| 11 | No broad process termination | `test_h11`, `h11b` | pass (control) |
| 12 | PowerShell wrapper text + ASCII | `test_h12` | pass (control) |

`test_h08` pins all eleven non-stop owner sentences byte-for-byte against the accepted baseline text.
`test_h08d` asserts the stop codes cannot produce stop wording outside the stop phase.
`test_h11b` asserts that across all seven stop outcomes the only PIDs ever signalled are the two
identity-verified recorded ones.

---

## 5. Verification

### 5.1 Focused Phase 7.14 suite (in place)

`python -m unittest tests.test_phase7_14_owner_usability_pilot_readiness`

| | Tests | Result |
|---|---|---|
| accepted baseline `b3e357e` (fresh worktree, untouched) | 418 | 1 failure — `test_199e`, §6 |
| hotfix `fa203bf` (in place) | **439** | 1 failure — `test_199e`, §6 |

Exactly +21, matching the 21 tests added. The single failure is identical on both sides and
pre-existing. `TestStopOwnerMessage` alone: 21/21 pass.

### 5.2 PowerShell 5.1 wrappers

Run as the owner runs them, `powershell.exe -NoProfile -ExecutionPolicy Bypass -File`:

| Wrapper | State | readiness | Exit | Owner text |
|---|---|---|---|---|
| `Stop-AMZ-Toolkit.ps1` | nothing running | `ALREADY_STOPPED` | 0 | "The toolkit was not running, so there was nothing to stop." |
| `Open-AMZ-Toolkit.ps1` | nothing running | `NOT_RUNNING` | 1 | "The toolkit is not running yet. Run Start-AMZ-Toolkit first." |
| `Stop-AMZ-Toolkit.ps1` | foreign console | `STOP_REFUSED` | 1 | new unrelated-process sentence (below) |

All three `.ps1` files remain pure ASCII (PowerShell 5.1 reads a BOM-less `.ps1` as ANSI). The
wrapper's own line, *"The toolkit was not stopped. The reason is printed above."*, was already
stop-accurate and is unchanged.

### 5.3 Real separate-process Start → Stop

Per the audit's Windows finding, stop can only be judged honestly across separate processes:

```
$ python -m production.phase7_owner_launcher --no-browser start
readiness=SESSION7_14_LAUNCHER_READY   pid=17552   startup_seconds=0.56

$ python -m production.phase7_owner_launcher stop
readiness=SESSION7_14_LAUNCHER_STOPPED pid=17552   stop_seconds=3.26
The toolkit has stopped.
```

Successful-stop wording is unchanged and the PID is released.

### 5.4 End-to-end proof of the new refusal text

A console was started **outside** the launcher (PID 8452), so no PID record exists but the port is
healthy — the real `NOT_LAUNCHER_OWNED` path:

```
$ python -m production.phase7_owner_launcher stop
readiness=SESSION7_14_LAUNCHER_STOP_REFUSED
error_code=NOT_LAUNCHER_OWNED

The process was not stopped because it was not started by this launcher. A console is
answering on this port, but this launcher did not start it, so nothing was stopped.
```

Exit code 1, canonical readiness and error code intact, and **PID 8452 was still alive afterwards** —
the launcher refused to touch a process it did not start. The test console was then cleaned up by
this session, not by the launcher.

### 5.5 Full in-place regression suite

`python -m unittest discover -s tests`, full discovery, primary working copy:

```
Ran 4604 tests in 1001.846s
FAILED (failures=1, errors=1, skipped=4)
```

| | Ran | Failures | Errors | Skipped |
|---|---|---|---|---|
| Phase 7.14 acceptance record (`b3e357e`) | 4583 | 0 | 0 | 4 |
| this hotfix (`fa203bf`) | **4604** | 1 | 1 | 4 |

+21 tests exactly, and the skip count is unchanged. Both non-passes are accounted for and neither is
caused by this hotfix:

**1. `test_199e_no_acceptance_tag_yet` — pre-existing, see §6.** It passed when Phase 7.14 was
implemented (hence 0 failures in the acceptance record) and fails now because the independent audit
subsequently created the very acceptance tag the test asserts does not exist. It fails identically
on the untouched baseline worktree.

**2. `test_phase7_13_unified_owner_console.TestBody.test_52_request_size_bounded` — environment
flake in untouched code.**

```
ConnectionAbortedError: [WinError 10053] An established connection was aborted by
the software in your host machine
```

A Windows loopback race: the 7.13 console closes the connection after rejecting an oversized
request body, and the client sometimes observes the abort before it reads the response. Evidence
that it is unrelated to this hotfix, rather than an assertion that it is:

- `git diff b3e357e HEAD -- tests/test_phase7_13_unified_owner_console.py
  production/phase7_unified_owner_console.py` is **empty** — both files are byte-identical to the
  accepted baseline.
- Re-run 10× in the primary working copy: **9 pass, 1 fail.**
- Re-run 10× in the untouched accepted-baseline worktree: **9 pass, 1 fail** — the same rate.
- It passes in both fresh worktrees at the same commit.

Same `WinError 10053` signature the Phase 7.14 audit recorded as a known environment flake.

### 5.6 Fresh-worktree differential

Two fresh worktrees, run **concurrently with each other and nothing else**, full discovery on both
sides, full output captured on both sides (truncating one side silently distorts the skip list):

| | Ran | Failures | Errors | Skipped | Seconds |
|---|---|---|---|---|---|
| accepted baseline `b3e357e` | 4581 | 2 | 14 | 329 | 513.7 |
| hotfix `fa203bf` | **4602** | 2 | 14 | 329 | 512.9 |

**+21 tests exactly. Identical failure, error and skip counts.**

Stronger than the counts: the **set** of 16 non-passing test nodes is byte-identical between the two
worktrees — verified by sorting and diffing the `ERROR:` / `FAIL:` lines, which produced no
difference. And `TestStopOwnerMessage` contributes **zero** non-passing nodes on the feature side, so
all 21 new tests pass in a clean checkout of the delivered form.

A fresh worktree here is never absolutely green: `runs/T2` is gitignored, so the 14 errors are the
known T2-data-dependent tests that cannot find their inputs in a bare checkout. This is why the
differential is judged relatively, not absolutely.

**`FRESH_WORKTREE_FULL_SUITE_BASELINE_EQUIVALENT_NONZERO`**

(The `WinError 10053` flake from §5.5 did not occur in either worktree run, consistent with its
measured ~1-in-10 rate.)

---

## 6. Known pre-existing failure (not caused by this hotfix)

`test_199e_no_acceptance_tag_yet` fails **identically on the untouched accepted baseline**:

```
AssertionError: 'checkpoint' not found in
'phase7-14-owner-usability-pilot-readiness-accepted-b3e357e' : unexpected 7.14 tag
```

It is a pre-acceptance self-guard asserting that no Phase 7.14 acceptance tag exists yet. The
independent audit then created exactly that tag, which invalidated the guard. It is unrelated to
Stop wording and out of scope for this hotfix, so it is reported rather than changed. It should be
retired or re-scoped in the next Phase 7.14-touching change.

Non-blocking observation, also not changed here: `start --no-browser` still prints *"Your browser
should now be open on the console."* The `--no-browser` flag is a diagnostic switch that the owner
wrappers never pass, so no owner sees it. Backlog.

---

## 7. Integrity

| File | SHA-256 (fresh checkout, LF-pinned) |
|---|---|
| `production/phase7_owner_launcher.py` | `e770e6dabec1f0daf899bbc749887370209f08e5b3cef90e9260d0a4d04e6f63` |
| `tests/test_phase7_14_owner_usability_pilot_readiness.py` | `73de681e4fc9599bb8a87a85c11fc45a249e1d4ea1df3b178b0ae1e447970805` |

Both files are pinned `text eol=lf` in `.gitattributes`, so these values reproduce in any checkout
regardless of `core.autocrlf`. Verified: the fresh worktree and the working copy agree exactly.

Accepted baseline for comparison:
`7296a59bb13d19414d5bc626fcb8106017637a4fa33fd2d8fd506188d920ac8b` (launcher) and
`e1600dd15254074a483fe10b9de418c87c4295bf69ce4d434f5a068aa885d43c` (tests).

Tag state after this hotfix:

```
phase7-14-owner-usability-pilot-readiness-accepted-b3e357e -> a629cd7 -> b3e357e   (untouched)
phase7-14-stop-owner-message-hotfix-checkpoint-b3e357e     -> b3e357e             (untouched)
```

No acceptance tag was created for this hotfix.

---

## 8. Status

`PHASE7_14_STOP_OWNER_MESSAGE_HOTFIX_READY_FOR_INDEPENDENT_ACCEPTANCE_AUDIT`

Branch pushed. **Not merged.** No acceptance tag. The pilot has not been started and no Phase 8 work
has begun.

**An independent hotfix acceptance audit is recommended** before this is merged or the pilot begins.
Suggested focus: that the canonical readiness/error-code contract is genuinely unchanged; that stop
gained no aggression; that Start and Open wording is provably untouched; and that the 13 control
tests really do pass on both sides.
