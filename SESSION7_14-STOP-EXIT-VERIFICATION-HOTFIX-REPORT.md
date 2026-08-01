# Phase 7.14 — Stop Exit Verification Hotfix

**Branch:** `hotfix-phase7-14-stop-exit-verification`
**Baseline:** `a68c1473ab904ff7b3fb26be0f7eaee73a7c7cf6`
**Checkpoint tag:** `phase7-14-stop-exit-verification-checkpoint-a68c147`
**Scope:** defect hotfix only. No Owner Home, no Demo, no Slice 1/2/3, no Phase 8, no PPC, no new
Amazon authority, no change to an accepted business authority, no Seller Central boundary change,
no `CAPABILITIES.json` change, `test_199e` not retired, `target_ids` not touched, no launcher rewrite.

---

## 1. Root cause verdict — **PROVEN**

> `process_alive()` is not an exit test on Windows. It answers "is this PID still addressable?",
> which stays **true for a process that has already exited** for as long as *any* handle to that
> process object remains open. `_await_exit()` polled exactly that, so a console that the kernel had
> already reported as terminated was reported to the owner as *still running*.

Windows frees a process object only when the process has exited **and** every handle to it has been
closed. Until then `OpenProcess` succeeds and `GetProcessTimes` returns the original creation time —
so both `process_alive()` and `process_start_token()` keep answering as though the process were live.
`_await_exit()` therefore ran to the full 15-second budget and `stop()` returned
`CONSOLE_DID_NOT_STOP`.

### 1.1 The reproduction is exact

One real Windows child, terminated, then measured **both ways at the same instant**
(`STOP_TIMEOUT_SECONDS = 15.0`, `STOP_POLL_INTERVAL = 0.25`):

| | mechanism | result | waited |
|---|---|---|---|
| **BEFORE** (accepted baseline) | `OpenProcess` + `GetProcessTimes` (`process_alive`) | `reported_stopped=false` → `SESSION7_14_LAUNCHER_FAILED / CONSOLE_DID_NOT_STOP` | **15.03 s** (first run) / **15.05 s** (re-run) |
| **AFTER** (this hotfix) | `WaitForSingleObject` + `GetExitCodeProcess` | `EXITED`, `WAIT_OBJECT_0`, `exit_code=1`, `still_active=false` | **0.0 s** |

`process_really_gone: true`, `terminate_process_result: {"ok": true, "error": null,
"api": "TerminateProcess"}`, and the start token still resolving throughout.

**The owner's exact failure signature is reproduced:** the whole 15-second budget consumed, then
`CONSOLE_DID_NOT_STOP`, on a process proven gone. The first recorded run measured **15.03 s** — the
owner's reported `stop_seconds` to the hundredth; the re-run captured in the proof measured 15.05 s.
Both are the full bounded budget, which is the signature that matters; the hundredths differ only by
scheduling jitter across the 60 polls.

### 1.2 The direct A/B, on one process object

`tests …TestWindowsRealProcessExitProof.test_x02` asserts both halves against one real terminated
child while a handle is referenced:

* kernel: `exit_state=EXITED`, `wait_name=WAIT_OBJECT_0`, `still_active=false`;
* accepted baseline check: `process_alive(pid) is True`, and `process_start_token(pid)` still
  resolves — held true across every sample for as long as the handle was held.

`test_x03` completes the proof from the other side: the *same* PID flips to
`process_alive() == False` only once the last handle is closed. Same process, same instant, two
different answers, decided solely by whether a handle is referenced.

### 1.3 What is **not** proven — stated plainly

**Which component held the handle on the owner's machine is UNPROVEN, and this hotfix does not
depend on it.** Measured on this machine, the launcher's own topology releases cleanly: with the
spawning process exited and no handle retained, `process_alive()` goes false **0.25 s** after
termination (`probe_real_topology`, case 1). So the 15-second stall required *some other* handle
holder — antimalware, a shell extension, an indexer, WMI, `conhost`, or a diagnostic tool. The
launcher log corroborates a long-lived reference: `pid=14360` was terminated at `14:48:16` and was
still reported alive at `14:49:08`, ~52 s later, with an unchanged start token, and a later
read-only diagnostic reported `reported_pid_exists=false`.

Two candidate explanations survive that evidence — a third-party handle holder, or a genuinely
slow kernel-side teardown. **The accepted baseline cannot tell those apart, and that inability is
the defect.** The fix removes the dependence entirely: exit is now proven by the kernel through a
handle the launcher holds itself, so neither explanation can produce a false `CONSOLE_DID_NOT_STOP`.

A second, self-inflicted instance of the same trap was measured and designed around: when the
*stopper itself* holds a handle in order to wait, `process_alive()` stays true for the whole window
(`probe_real_topology`, case 2 — `wait_is_signaled=true`, `still_active=false`,
`process_alive_true_for_whole_window=true`). Any fix that kept polling `process_alive()` while
holding a wait handle would have made the defect **permanent**. The exit answer therefore comes from
the handle, never from `process_alive()`, whenever a handle is available.

---

## 2. Implementation

**One file changed in production:** `production/phase7_owner_launcher.py`.

### 2.1 `WindowsExitVerifier` — the authoritative exit state

Opens **one** handle with `SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION` — never
`PROCESS_TERMINATE` — **before** anything is signalled, and holds it for the whole stop.

| kernel answer | verdict |
|---|---|
| `WAIT_OBJECT_0` **and** `GetExitCodeProcess != STILL_ACTIVE` | `EXITED` |
| `WAIT_TIMEOUT` **and** `STILL_ACTIVE` | `RUNNING` |
| `WAIT_FAILED`, `OpenProcess` failure, `GetExitCodeProcess` failure, or the two sources disagreeing | `UNPROVEN` — fail closed |

Both sources must agree before an exit is reported.

### 2.1.1 The PID identity race — found by independent audit, and what was wrong here

**An earlier revision of this report claimed that "Windows cannot recycle that PID while the stop is
in flight, so the escalation is guaranteed to reach the same process object whose identity was
proven." That claim was false as written, and it is withdrawn.** A handle does pin a PID — but only
from the moment the handle is open. The first revision opened it too late for that guarantee to
cover the interval the sentence claimed, and an independent audit demonstrated an unrelated process
being terminated.

The pre-remediation order was:

| # | step | pinned? |
|---|---|---|
| 1 | `process_start_token(pid)` — identity read through a **transient** handle, immediately closed | no |
| 2 | `probe_health(...)` — up to `HEALTH_REQUEST_TIMEOUT` = **3.0 s** | **no — the exposure window** |
| 3 | `open_exit_verifier(pid)` — the verifier handle finally opened | from here on |
| 4 | `os.kill(pid, CTRL_BREAK_EVENT)` — graceful signal, by **raw PID** | |
| 5 | `OpenProcess(PROCESS_TERMINATE, pid)` → `TerminateProcess` — a **second handle, by raw PID, with no identity re-check** | |

Between 1 and 3 the process object was free to be released and the number reassigned. Steps 4 and 5
then acted on whatever process held that number, and step 5 in particular opened its own handle and
killed through it without ever asking who it belonged to.

**Measured exposure window.** The independent audit measured **≈3.031 s**. Reproduced here on the
same machine at **3.063 s** (§2.1.2) — the same phenomenon, dominated by `HEALTH_REQUEST_TIMEOUT`
and differing only by scheduling jitter. It is *widest* exactly when the console is unhealthy or
unresponsive, i.e. when the recorded process is most likely to be exiting: a fast-failing probe
returns in milliseconds, but a console that accepts the connection and never answers holds the
launcher for the full timeout.

### 2.1.2 The remediation, and the exact conditions under which the race is closed

The pinned handle is now opened **first** — before the health probe, before any other delay and
before anything is signalled — and identity is read back **through that same handle**:

1. `open_exit_verifier(pid)` opens `SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION` and immediately
   calls `GetProcessTimes(handle)`, storing the result as `start_token`. A raw-PID read re-resolves
   the number and can therefore answer about a *different* process; a handle-derived read can only
   ever describe the process object the handle already refers to.
2. `_pinned_identity()` compares the recorded token, the handle-derived token and the raw-PID token.
   Any missing token, any API failure and any mismatch refuses: **no `CTRL_BREAK_EVENT`, no
   `TerminateProcess`, no runtime state cleared** (`terminate_requests` is recorded as `[]`, so
   "nothing was asked of the OS" is provable from the artifact rather than inferred).
3. Only then is health probed.
4. The hard path passes that same token to `terminate_process_result(..., expect_token=…)`, which
   opens `PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION` and **re-reads the creation token
   through that exact termination handle**. A mismatch returns
   `TERMINATION_IDENTITY_MISMATCH` and issues no kill at all.
5. The verifier handle is held until `stop()` returns.

So three tokens must agree before anything is signalled: **recorded**, **verifier-handle**, and
**termination-handle**. The graceful `CTRL_BREAK_EVENT` has no handle form — it signals a console
process *group* by number — and is therefore issued only after the pinned handle is open and its
token has matched, which is what makes the number safe to use.

The pinning guarantee is now stated only for what it actually covers: **from the moment the verifier
handle is open and its handle-derived token has matched the recorded token, until `stop()` returns,
that PID cannot become a different process.** It says nothing about any earlier instant, because
nothing holds before the handle exists.

Proven by: `test_x50` (identity read through the exact pinned handle), `test_x51` (termination handle
refuses a mismatched token and kills nothing — real process survives), `test_x52` (matching token
still terminates), **`test_x53` (the auditor's scenario end to end: a real unrelated replacement
process survives a full stop, untouched)**, `test_x54` (all three tokens agree on the normal path),
`test_x55` / `test_x56` (no handle, or an unreadable handle token, fails closed), `test_x57`
(the handle is opened before the probe and released last), `test_x58` (a refusal never even reaches
the probe and clears no state), `test_x46` (source contract: pin before probe, token re-read before
`TerminateProcess`). `test_x06` asserts the pinning on a real process and `test_x32` asserts a real
bystander survives an end-to-end stop — both true, but neither ever covered the pre-handle interval,
which is why they did not catch this.

### 2.2 `terminate_process_result()` — the request outcome is captured

The baseline discarded `TerminateProcess`'s return value, so a refused request was indistinguishable
from an accepted one. Every request now returns `{ok, hard, api, error}` with the Windows error code
folded into `error` (e.g. `OPEN_PROCESS_FAILED_87`, `TERMINATE_PROCESS_FAILED_5`), and every request
is recorded in `terminate_requests`. `terminate_process()` is retained as a boolean wrapper, so the
accepted call signature and the injected test seams are unchanged.

A failed request can never reach a success path: success requires `exit_state == EXITED`, proven by
the kernel. Only the **hard** request counts toward `termination_request_failed` — the graceful
console-break is expected to be refused for a detached child, and escalation is the designed answer.

### 2.3 Six machine states, four owner sentences

| `stop_state` | `exit_state` | readiness / `error_code` | owner sentence |
|---|---|---|---|
| `PROCESS_EXITED` | `EXITED` | `…LAUNCHER_STOPPED` | The toolkit stopped safely. |
| `PROCESS_EXITED_RUNTIME_STATE_STALE` | `EXITED` | `…LAUNCHER_STOPPED` | The toolkit stopped, but its local runtime record could not be cleaned up. … |
| `PROCESS_STILL_ALIVE` | `RUNNING` | `…FAILED` / `CONSOLE_DID_NOT_STOP` | The toolkit is still running. … |
| `PORT_CLOSED_PROCESS_ALIVE` | `RUNNING` | `…FAILED` / `CONSOLE_DID_NOT_STOP` | The toolkit is still running. … |
| `TERMINATION_REQUEST_FAILED` | `RUNNING` or `UNPROVEN` | `…FAILED` | per `exit_state` |
| `PROCESS_STATE_UNPROVEN` | `UNPROVEN` | `…FAILED` / `CONSOLE_EXIT_NOT_PROVEN` | The toolkit could not confirm … |

Six machine states, **four** owner sentences: a proven exit whose runtime record could not be cleaned
up is still a success, but it is not the same success, so it does not borrow the unqualified
sentence (`test_x22`, `test_x59`).

### 2.4 Fallback semantics where no handle exists

`process_alive()` is still used where a handle is unavailable, but only in the direction that is
sound: **`False` is conclusive on every platform** (the object is freed only after exit *and* full
handle release). A `True` answer is believed only on POSIX or through an injected seam; on real
Windows with no usable handle it yields `UNPROVEN`, never `RUNNING`.

### 2.5 `command_identity_verified` corrected

The baseline computed `bool(health.ok) or not health.http_status`. A transport error carries **no**
`http_status`, so an **unreachable** console was recorded as an identity proof. The owner's record
shows exactly that: health unreachable, `"command_identity_verified": true`. It is now true only for
the accepted health contract, with `command_identity_evidence` naming the basis
(`ACCEPTED_HEALTH_CONTRACT` / `FOREIGN_HTTP_RESPONDER` / `HEALTH_UNREACHABLE`). It remains
**diagnostic only** and authorizes nothing (`test_x23c`).

### 2.6 Preserved, verified by test

Exact PID validation · process-start-token validation · executable/command identity validation · no
broad Python termination · no unrelated process termination · fail-safe default · bounded timeout
(`STOP_TIMEOUT_SECONDS` still `15.0`, never extended) · launcher audit/log behaviour · existing
Start/Open contracts. Port and health are recorded as supporting diagnostics and authorize neither
termination nor success. Runtime state is cleared only after a proven exit; on any unproven outcome
the PID record is deliberately **left in place** so the next Stop still knows exactly which process
it may signal. No `taskkill`, no image-name matching, no `psutil`, no process enumeration, no
`shell=True` — asserted by `test_x43`, which also pins the source to exactly **one**
`TerminateProcess(` call site.

Strengthened by the identity-race remediation: identity now comes from the pinned handle rather than
a raw-PID read, the single `TerminateProcess` call site re-validates identity through its own handle,
and an identity refusal clears **no** runtime state — a Stop that could not prove what it was looking
at does not get to delete the record the next Stop needs. Reclaiming a genuinely stale record remains
Start's stale-PID sweep, which is unchanged.

### 2.7 Live end-to-end verification, on the owner's port

Real accepted Phase 7.13 console, real port 8780, real launcher CLI — the exact path that failed.
Started with `--no-browser` so no window was opened unprompted.

Re-run after the identity-race remediation (2026-07-31), on the same real console and port:

```
start  readiness=SESSION7_14_LAUNCHER_READY   pid=18760  startup_seconds=0.55  exit 0
stop   readiness=SESSION7_14_LAUNCHER_STOPPED pid=18760  stop_seconds=3.27     exit 0
       exit_state=EXITED   stop_state=PROCESS_EXITED
       "The toolkit stopped safely."
stop   readiness=SESSION7_14_LAUNCHER_ALREADY_STOPPED  exit 0
       "The toolkit was not running, so there was nothing to stop."   (baseline wording, unchanged)
```

```json
"process_identity": {"recorded_token_present": true, "handle_pinned": true,
                     "handle_token_read": true, "handle_token_matches_recorded": true,
                     "process_token_matches_recorded": true, "handle_identity_required": true,
                     "api_error": null, "authorized_by": "PINNED_HANDLE_TOKEN"}
"exit_verification": {"source": "windows_process_handle", "handle_held": true,
                      "wait_result": 0, "wait_name": "WAIT_OBJECT_0",
                      "get_exit_code_ok": true, "exit_code": 1, "still_active": false}
"terminate_requests": [{"api": "GenerateConsoleCtrlEvent", "hard": false, "ok": false,
                        "error": "OSError", "identity_checked": false, "identity_verified": null},
                       {"api": "TerminateProcess", "hard": true, "ok": true, "error": null,
                        "identity_checked": true,  "identity_verified": true}]
"termination_request_failed": false,  "runtime_state_cleared": true,
"command_identity_verified": true,    "command_identity_evidence": "ACCEPTED_HEALTH_CONTRACT",
"port_open_after_stop": false,        "health_reachable_after_stop": false
```

The hard termination records `identity_checked: true, identity_verified: true` — the creation token
was re-read through the termination handle itself and matched before the kill was issued. The
graceful request records `identity_checked: false` honestly: `CTRL_BREAK_EVENT` has no handle form,
and it is safe because it is issued only after the pinned handle matched (§2.1.2).

`stop_seconds` is **3.27 s**, identical to the pre-remediation run — the extra handle and two
`GetProcessTimes` calls cost nothing measurable.

This run confirms three design decisions against reality rather than against a seam:

1. **The kernel proof works on the real console** — `WAIT_OBJECT_0` with `exit_code=1`,
   `still_active=false`, from a handle the launcher held across the stop.
2. **The graceful console-break genuinely is refused** for a detached child
   (`GenerateConsoleCtrlEvent … ok:false`), and the hard `TerminateProcess` genuinely succeeds. This
   is exactly why a failed *graceful* request must not count toward `termination_request_failed` —
   which correctly reports `false` on a completely healthy stop.
3. **Port and health were both unreachable afterwards and authorized nothing** — the stop succeeded
   on the kernel proof alone.

`stop_seconds=3.27` against the historical healthy `3.26`, and against the owner's failing `15.03`.
Afterwards port 8780 held only `TIME_WAIT` sockets — no listener, console gone, nothing else stopped.

The launcher log now carries the captured request results and the proven state:

```
stop.signalled hard=False pid=18760 requested=False
stop.escalated hard=True  pid=18760 requested=True
stop.stopped   escalated=True exit_state=EXITED pid=18760 stop_state=PROCESS_EXITED waited=3.27
stop.already_stopped
```

---

## 3. Owner messages

| situation | text |
|---|---|
| Success | `The toolkit stopped safely.` |
| Success, runtime record stale | `The toolkit stopped, but its local runtime record could not be cleaned up. Nothing else on this computer was stopped. Open technical details before starting it again.` |
| Still running | `The toolkit is still running. Nothing else on this computer was stopped. Open technical details for the recorded reason.` |
| Unproven | `The toolkit could not confirm that the local server stopped safely. Nothing else on this computer was stopped. Open technical details for the recorded reason.` |

**Capitalization is one form: `The toolkit`.** An earlier revision of this hotfix introduced
`The Toolkit` for the new sentences while every accepted sentence — start, open, already-stopped,
refusals, port-blocked — used `The toolkit`. A single Stop session can print both (stop, then stop
again), so the owner saw the product named two ways in consecutive lines. The new sentences were
standardized *onto the existing accepted form* rather than the reverse, so no unrelated accepted copy
was touched. Asserted by `test_x48`, which fails if `The Toolkit` reappears anywhere in the launcher.

**`PROCESS_EXITED_RUNTIME_STATE_STALE` no longer reads as an unqualified success.** The process did
stop, so it is still `LAUNCHER_STOPPED` — but the launcher could not remove its own runtime record,
and the owner needs to know that before starting again. It now has its own sentence, selected by
`stop_state` through `_STOP_STATE_OWNER_MESSAGES`. `runtime_state_cleared: false` and
`stop_state: PROCESS_EXITED_RUNTIME_STATE_STALE` are preserved exactly (`test_x59`).

**No unreachable owner copy.** `_OWNER_MESSAGES[LAUNCHER_STOPPED]` was `"The toolkit has stopped."`,
a sentence the stop path could never print, because the stop-phase selector always answered first.
Rather than delete the entry, it is now routed to the same `STOP_SUCCESS_MESSAGE` constant the stop
path prints, so there is exactly one success sentence in the module and no second copy that can drift
out of date. `test_x47` asserts the entry and the selector agree, and that the dead string is gone.

No raw error code appears in any owner sentence (`test_x21`). Bounded codes remain in the technical
record: `exit_state`, `stop_state`, `exit_verification`, `terminate_requests`,
`termination_request_failed`, `command_identity_evidence`, `port_open_after_stop`,
`health_reachable_after_stop`, `runtime_state_cleared`. The CLI prints `exit_state` and `stop_state`
alongside the existing keys.

**Two previously-accepted assertions were updated**, because this hotfix's owner-message contract
supersedes them. Both are wording-only; `readiness` and `error_code` are unchanged.

* `test_h03` — was `"The toolkit did not stop within the allowed time."`. That sentence describes the
  launcher's clock, not the process, and read identically whether the process was alive or merely
  unproven — which is what the owner saw.
* `test_h07` (renamed `test_h07_successful_stop_wording_states_a_safe_stop`) — was
  `"The toolkit has stopped."`. Success is now reported only against a proven exit, so the sentence
  says so.

Every other owner sentence — start, open, port-blocked, refusal, already-stopped — is byte-identical
to the accepted baseline and still asserted by `test_h06`, `test_h08` and `test_x40`.

---

## 4. Tests — **+51** against the accepted baseline, focused suite 465 → **516**

`+38` for the exit-verification fix, then `+13` for the identity-race remediation.

Real Windows child processes; seam tests retained but no longer the only evidence. Every child is
spawned by the test run itself (`python -c "import time; time.sleep(...)"`), signalled only by a PID
the test spawned, and reaped on cleanup.

Focused-suite differential, both legs measured — not carried forward from an earlier report:

| | accepted baseline `a68c147` | HEAD |
|---|---|---|
| ran | **465** | **516** |
| failures | 1 | 1 |
| failing test | `test_199e_no_acceptance_tag_yet` | `test_199e_no_acceptance_tag_yet` |

The failure set is byte-identical, which is what makes the `test_199e` classification a measurement
rather than an argument: the accepted baseline fails exactly the same single test, for the same
repo-global tag reason (§5.2).

| # | requirement | test |
|---|---|---|
| 1 | real child exits, detected as exited | `test_x01`, `test_x04` |
| 2 | terminated child, handle referenced | `test_x02`, `test_x03` |
| 3 | handle is signalled | `test_x01`, `test_x32` |
| 4 | `GetExitCodeProcess` non-`STILL_ACTIVE` | `test_x01`, `test_x04`, `test_x32` |
| 5 | `TerminateProcess` returns failure | `test_x05`, `test_x16`, `test_x16c` |
| 6 | alive through timeout | `test_x10` |
| 7 | exits immediately before timeout | `test_x11` |
| 8 | exits just after a polling boundary | `test_x12` |
| 9 | port closed but process alive | `test_x13`, `test_x13b` |
| 10 | exited but stale runtime state | `test_x14` |
| 11 | state cleaned only after proven exit | `test_x15`, `test_x10` |
| 12 | PID/start-token mismatch refuses | `test_x30`, `test_x31` (real processes) |
| 13 | unrelated process never stopped | `test_x30`, `test_x32` (real bystander) |
| 14 | owner success message truthful | `test_x19`, `test_h07` |
| 15 | owner failure message truthful | `test_x20`, `test_x21`, `test_x22`, `test_h03` |
| 16 | Start/Open equivalent | `test_x40`, `test_x41` |
| 17 | Seller Central counters 0 | `test_x42`, `test_x44` |

Additional load-bearing tests: `test_x06` (PID pinned against reuse), `test_x07` (verifier never
requests terminate rights), `test_x08` (unopenable PID fails closed), `test_x17` / `test_x17b` (the
handle beats a lying alive seam — the owner's defect, inverted), `test_x18` (timeout not extended),
`test_x23` / `test_x23b` / `test_x23c` (`command_identity_verified`), `test_x33` (real end-to-end
stop is fast), `test_x43` (no image-name kill), `test_x45` (record bounded and secret-free).

**Platform:** real-lifecycle tests are gated by `WINDOWS_ONLY`; on this Windows host **none were
skipped**. `test_x30` / `test_x31` are cross-platform. `test_x07` asserts the source contract on
every platform.

**The +13 identity-race tests** (§2.1.2), all against real Windows process objects except where noted:

| test | proves |
|---|---|
| `test_x50` | identity is read through the **exact** pinned handle; handle token == raw token for a process that has not been replaced |
| `test_x51` | a `PROCESS_TERMINATE` handle whose own token does not match is **never terminated through** — the real process survives |
| `test_x52` | the same gate still permits a legitimate stop |
| **`test_x53`** | **the auditor's scenario end to end: a real unrelated replacement process survives a full stop — no signal, no kill, no state cleared** |
| `test_x54` | recorded, pinned-handle and termination-handle tokens all agree on the normal path; a bystander is untouched |
| `test_x55` | no pinned handle on real Windows → fail closed, never fall back to a raw-PID read |
| `test_x56` | a pinned handle whose `GetProcessTimes` failed authorizes nothing |
| `test_x57` | the handle is opened **before** the probe and released **last** (seam order) |
| `test_x58` | a refusal never even reaches the health probe, signals nothing, clears nothing |
| `test_x46` | source contract: pin before probe, token re-read before `TerminateProcess` |
| `test_x47` | no unreachable owner copy (D6) |
| `test_x48` | one owner capitalization across a Stop session (D5) |
| `test_x59` | the stale-runtime-record state gets its own qualified sentence (D6) |

`test_x53` is the one that would have caught the shipped defect: it runs the **real** `Launcher.stop()`
with no `alive`, `terminate` or `exit_verifier` seams, against a real live process that holds the
recorded PID but is not the recorded process.

**These tests are defect-specific**, not tautological: `test_x02` and `test_x03` characterize the
*broken* primitive directly, so they fail if the Windows semantics are ever misdescribed, and
`test_x17b` asserts that a handle-proven exit overrides a `process_alive()` that still claims life —
which is the defect inverted. The load-bearing differentials are §4.1 (the unpinned
window, measured in matched worktrees) and §4.2 (the real baseline `Launcher.stop()` measured
against the real HEAD `Launcher.stop()`).

### 4.1 Matched-worktree differential — the unpinned window

Both legs run the same instrumented script on the same machine, timestamping the identity read
(`start_token` seam) and the handle acquisition (`exit_verifier` seam), against a health endpoint
that accepts the TCP connection and never answers — so the probe runs to the full
`HEALTH_REQUEST_TIMEOUT`, which is the unhealthy/unresponsive-console case the audit called out.

| | **PRE-REMEDIATION `0a71cd9`** | **REMEDIATED** |
|---|---|---|
| seam order | `identity_read` → `health` → `handle_pinned` | `handle_pinned` → `identity_read` → `health` |
| health probe | 3.063 s | 3.016 s |
| **unpinned window** | **3.063 s** | **0.0 s** |
| handle pinned before identity read | **false** | **true** |
| handle pinned before health probe | **false** | **true** |
| stop result | `LAUNCHER_STOPPED` / `EXITED`, 3.25 s | `LAUNCHER_STOPPED` / `EXITED`, 3.25 s |

The independent audit measured the window at **≈3.031 s**; this reproduction measured **3.063 s** —
the same phenomenon, dominated by the 3.0 s probe timeout, differing only by scheduling jitter. The
stop result and cost are identical in both legs: the remediation changes the **order**, not the
outcome.

### 4.2 End-to-end differential — baseline vs HEAD, identical conditions

Both legs run the **real** stop path with the **real** process layer (no `alive` seam, no `terminate`
seam), each against its own freshly spawned real child, under the condition the probes proved
triggers the defect: a third party holds a handle to the process object. Baseline runs from a
detached worktree at `a68c147`.

| | **BASELINE `a68c147`** | **HEAD `569a72d`** |
|---|---|---|
| `readiness` | `SESSION7_14_LAUNCHER_FAILED` | `SESSION7_14_LAUNCHER_STOPPED` |
| `error_code` | `CONSOLE_DID_NOT_STOP` | *(none)* |
| `stop_seconds` | **15.01** | **3.25** |
| `exit_state` | *(field did not exist)* | `EXITED` |
| `exit_verification` | *(none)* | `WAIT_OBJECT_0`, `exit_code=1`, `still_active=false` |
| `command_identity_verified` | **`true`** — while health was unreachable | **`false`**, `HEALTH_UNREACHABLE` |
| `terminate_requests` | *(discarded)* | both recorded, both `ok:true` |
| `pid_record_remaining` | `true` | `false` |
| owner sentence | "The toolkit did not stop within the allowed time…" | "The toolkit stopped safely." |
| **ground truth** | **process exited, code 1** | **process exited, code 1** |

Ground truth was measured independently of whatever the launcher concluded, by releasing the handle
and reaping the child. **In both legs the process had genuinely exited with code 1.** The baseline
reported a running console and burned the full budget; HEAD reported the truth in 3.25 s. The same
run also demonstrates the `command_identity_verified` correction live: the baseline recorded
`true` on an unreachable console, HEAD records `false` with the reason named.

---

## 5. Regression

| gate | result |
|---|---|
| Focused Phase 7.14 suite | **516 ran, 1 failure** — `test_199e_no_acceptance_tag_yet` (stale, see below; the accepted baseline fails the same one) |
| New stop-exit classes only | **51 ran, OK**, 0 failures, 0 errors, 0 skipped |
| Identity-race classes only | `TestStopProcessIdentityRace` **7 ran, OK**, 0 skipped |
| Amazon boundary + network + connectivity policy | **47 ran, OK**, exit 0 |
| `scripts/connectivity_scan.py` | 96 files, **active amazon-account paths: 0**, exit 0 |
| `compileall production/ tests/` | exit **0** |
| `py_compile` on both changed files | OK |
| Full in-place suite | *see §5.1* |

### 5.1 Full in-place suite

`python -m unittest discover -s tests` → **Ran 4681 tests in 931.408 s — FAILED (failures=1,
errors=0, skipped=4), exit 1.** The single failure is `test_199e_no_acceptance_tag_yet`.

**Accepted baseline: `a68c147`** (`phase7-14-next-action-navigation-hotfix-accepted-a68c147`).
Counts of **4630 ran, 1 failure, 0 errors, 4 skipped**, 879.9 s, exit 1 were recorded at `5fcbf6f`.
`5fcbf6f` is **not** the accepted baseline and is not labelled as one here; the counts transfer
because its `production/` and `tests/` trees are byte-identical to `a68c147`'s — verified by tree
hash, not assumed:

```
production/  1cb44ddf80589ad0926f52d5f06070f656fe7463   (5fcbf6f == a68c147)
tests/       2c5995ddd3d961288ee3982b0b131bc5f3c993be   (5fcbf6f == a68c147)
git diff --name-only 5fcbf6f a68c147  ->  two .md files only
```

| | baseline | HEAD | delta |
|---|---|---|---|
| ran | 4630 | **4681** | **+51** |
| failures | 1 | **1** | **0** |
| errors | 0 | **0** | **0** |
| skipped | 4 | **4** | **0** |
| exit | 1 | 1 | — |

**Verdict: `BASELINE_EQUIVALENT_NONZERO`.** The `+51` is exactly the 51 tests this hotfix adds —
38 for the exit-verification fix, 13 for the identity-race remediation, and `4630 + 51 = 4681`
exactly. The failure set, the error count and the skip count are all unchanged.

**This suite is not green, and is not reported as green.**

#### Known Windows loopback flakes — disclosure preserved

`BASELINE_EQUIVALENT_NONZERO` means the differential found **zero PERSISTENT target-only
non-passing nodes**. It does not mean no node was ever observed once. A later independent
acceptance audit of the composite hotfix at `56f4339` did observe one, and it is recorded here
rather than dropped.

| | |
|---|---|
| Node observed | `test_phase7_13_unified_owner_console.TestBody.test_52_request_size_bounded` |
| Documented sibling, same failure class | `test_phase7_4_owner_dashboard.HttpSecurity.test_post_to_unknown_endpoint_rejected` |
| Signature | `ConnectionAbortedError: [WinError 10053]` |
| Observation | appeared **once**, on the target, under full-suite load |
| Classification | **`PRE_EXISTING_ACCEPTED_WINDOWS_LOOPBACK_FLAKE`** |

Evidence that this is not a launcher regression:

* `production/phase7_unified_owner_console.py` and `tests/test_phase7_13_unified_owner_console.py`
  are **identical git blobs** at baseline and target — same bytes for both the code under test and
  the test itself, so a regression there is impossible by construction;
* **12/12 isolated runs passed on both trees**;
* a **second full-suite sample on the target did not reproduce it**, and returned zero target-only
  non-passing nodes;
* prior **accepted** reports already record this node and this `WinError 10053` class as a
  load-dependent loopback flake, one of them measuring it in isolation on *both* sides.

Mechanism: the test POSTs an oversized body expecting HTTP 413. The server correctly rejects and
closes; if it closes before the client finishes writing, Windows aborts the connection and the
client never reads the response — timing, hence load-dependent and invisible in isolation.

The suite remains non-green because the known non-passing tests recorded in this report remain
non-passing. Read a single appearance of either node as this flake class, and confirm it cheaply —
identical blobs, then isolation, then a second sample — before calling it a regression.

### 5.2 The known stale failure, reported honestly

`test_199e_no_acceptance_tag_yet` **fails, and is expected to fail.** Classification:
**`STALE_BASELINE_EQUIVALENT`** — proven, not asserted:

```
AssertionError: 'checkpoint' not found in
  'phase7-14-next-action-navigation-hotfix-accepted-a68c147'
```

1. The test asserts that every `phase7-14-*` git tag contains `checkpoint` — i.e. that **no** Phase
   7.14 acceptance tag exists. Three do.
2. `git merge-base --is-ancestor` proves **all three are ancestors of the baseline `a68c147`**:
   `…owner-usability-pilot-readiness-accepted-b3e357e` → YES,
   `…stop-owner-message-hotfix-accepted-b5324f8` → YES,
   `…next-action-navigation-hotfix-accepted-a68c147` → YES. They existed before this hotfix began.
3. The test reads **repo-global git tag state**, not working-tree content, and this hotfix created
   no tag.
4. `git diff a68c147..HEAD -- tests/` contains **zero** references to `199e` — the test is
   byte-identical to baseline.

Therefore the failure reproduces identically on the untouched baseline. It was **not** retired,
deleted, skipped, weakened or concealed, as instructed.

**The suite is not green. It is baseline-equivalent nonzero**, and is reported as such.

---

## 6. Amazon boundary

Unchanged and re-verified. This hotfix adds **no** network code of any kind — the additions are
`ctypes`/process-lifecycle only.

* `seller_central_counters` all **0** on every stop path, including the new unproven and
  terminate-failed paths (`test_x42`).
* `launcher_never` flags intact, including `connects_to_seller_central`,
  `uses_a_seller_api_or_advertising_api`, `downloads_a_seller_report`, `drives_a_seller_browser`,
  `kills_a_process_it_did_not_start`, `kills_every_python_process`.
* No Seller Central / SP-API / advertising-API / `amazon.com` string reachable from the stop path
  (`test_x44`).
* `scripts/connectivity_scan.py` → **active amazon-account paths: 0**. The only launcher findings are
  the pre-existing loopback health-probe lines (`urllib`, `socket`), shifted in line number only; no
  finding is `PROHIBITED_AMAZON_PATH`. The regenerated scan artifact was **restored**, not committed —
  it is a stale whole-repo artifact from an earlier session and rewriting it is outside this hotfix.
* `test_amazon_boundary`, `test_network_policy`, `test_connectivity_policy` → 47/47 OK.

---

## 7. Known limitations

1. **The specific handle holder on the owner's machine is unproven** (§1.3). The fix is designed to
   be correct under either surviving explanation, but the report does not claim to know which
   occurred.
2. **`process_alive()` remains true-but-not-conclusive on Windows.** It is retained because `False`
   is conclusive and it is still correct for lock reclamation and stale-PID clearing, where a false
   "alive" is fail-safe (it declines to reclaim). Its docstring now records the limitation.
3. **A stop whose exit cannot be proven leaves the PID record in place.** This is deliberate and
   fail-safe, but it means a genuinely-exited console whose exit was unprovable will be re-signalled
   by the next Stop. That signal is still gated by the full identity check, so it can never reach an
   unrelated process.
4. **`PROCESS_EXITED_RUNTIME_STATE_STALE` still reports success** to the owner, because the process
   did stop. The stale record is carried in the machine record (`runtime_state_cleared: false`) and
   is cleared by the next Start's stale-PID sweep.
5. **The graceful console-break is still expected to fail for a detached child on Windows**, so most
   real stops escalate after the 3 s grace window. This is unchanged baseline behaviour and is why a
   healthy stop costs ~3.3 s rather than ~0 s.
6. **The PID identity race is closed, and the window it left is stated as measured — not as
   "microsecond-scale".** An earlier revision of this report described the residual window that way.
   That was wrong by three orders of magnitude: the gap was the health probe, and an independent
   audit measured it at **≈3.031 s** (reproduced here at **3.063 s**), widest precisely when the
   console was unhealthy or unresponsive. Moving the pinned handle ahead of the probe removes that
   exposure — measured **0.0 s**, with the handle opened before the identity read and before the
   probe (§2.1.2, §4.1).

   What remains: between the kernel freeing the process object and `OpenProcess` being called there
   is still an instant in which the recorded PID could already belong to something else. That is
   unavoidable — no API opens a handle to a PID without naming the PID — and it is now **harmless**,
   because nothing is signalled on the strength of the number. The handle is opened first, and the
   identity that authorizes every signal is read back through that handle: if the PID had already
   been reassigned, the handle refers to the replacement, its creation token does not match the
   recorded one, and the stop refuses without signalling, terminating or clearing state
   (`test_x53`). A stop can therefore fail closed on a recycled PID; it can no longer act on one.
7. **No live end-to-end console stop is included in the committed suite.** The real-process tests use
   stand-in children rather than the console itself, to keep the suite offline, port-free and
   parallel-safe. A live launcher run is recorded in the proof document instead.

---

## 8. Files changed

| file | change |
|---|---|
| `production/phase7_owner_launcher.py` | exit verification, terminate-result capture, six stop states, owner messages, `command_identity_verified` correction; **identity-race remediation**: handle-derived start token, `_pinned_identity` three-token gate, health probe moved after identity, handle held for the whole stop, termination-handle revalidation, qualified stale-record sentence, capitalization, `LAUNCHER_STOPPED` copy routed |
| `tests/test_phase7_14_owner_usability_pilot_readiness.py` | +51 tests total; `test_h03` / `test_h07` updated to the owner-message contract; verifier doubles now model the identity half of the contract |
| `SESSION7_14-STOP-EXIT-VERIFICATION-HOTFIX-REPORT.md` | this report (D1, D2, D4, D5, D6) |
| `SESSION7_14-STOP-EXIT-VERIFICATION-HOTFIX-PROOF.json` | machine-readable proof (D3 + D1–D6 correction evidence) |

No other production module, no accepted business authority, no `CAPABILITIES.json`, no launcher
script (`.bat` / `.ps1`), no console static asset, and no document under `docs/` was modified.

---

## 9. Status

Branch pushed. **Not merged. No acceptance tag. `main` untouched.** Independent **re-audit**
requested.

This revision answers the independent audit of `0a71cd9`, which returned **REMEDIATION_REQUIRED** on
one acceptance blocker — the PID identity race (§2.1.1) — and six documentation defects.

| finding | severity | where corrected |
|---|---|---|
| **PID identity race** | acceptance blocker | §2.1.2 — code, +13 tests, `test_x53` |
| D1 false whole-operation pinning guarantee | material | §2.1.1 — withdrawn, race and window described |
| D2 "microsecond-scale" residual window | material | §7.6 — measured ≈3.031 s / 3.063 s recorded |
| D3 race absent from proof JSON, over-broad pinning key | material | proof `process_identity_race`, `known_limitations[0]`, `preserved_contracts.pid_pinned_against_reuse` |
| D4 `5fcbf6f` mislabelled "accepted baseline" | low | §5.1 — `a68c147`, tree identity verified |
| D5 mixed owner capitalization | low | §3 — one form, `test_x48` |
| D6 unreachable owner copy / unqualified stale message | cosmetic | §3 — routed, `test_x47`, `test_x59` |

No additional audit findings were invented, and none were reinterpreted or weakened.
