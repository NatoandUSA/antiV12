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

**Holding the handle also closes a latent safety hole in the baseline.** The baseline verified the
start token **once**, then polled `process_alive()` for up to 15 s without ever re-verifying it, and
escalated to `terminate_process(pid, hard=True)` at the end. If the console had exited early in that
window and Windows had recycled its PID, the escalation would have hard-terminated **an unrelated
process** — the one outcome the launcher promises never to do. Because the verifier handle is opened
before anything is signalled and held for the whole stop, **Windows cannot recycle that PID while
the stop is in flight**, so the escalation is guaranteed to reach the same process object whose
identity was proven. `test_x06` asserts the pinning on a real process; `test_x32` asserts a real
bystander survives an end-to-end stop.

### 2.2 `terminate_process_result()` — the request outcome is captured

The baseline discarded `TerminateProcess`'s return value, so a refused request was indistinguishable
from an accepted one. Every request now returns `{ok, hard, api, error}` with the Windows error code
folded into `error` (e.g. `OPEN_PROCESS_FAILED_87`, `TERMINATE_PROCESS_FAILED_5`), and every request
is recorded in `terminate_requests`. `terminate_process()` is retained as a boolean wrapper, so the
accepted call signature and the injected test seams are unchanged.

A failed request can never reach a success path: success requires `exit_state == EXITED`, proven by
the kernel. Only the **hard** request counts toward `termination_request_failed` — the graceful
console-break is expected to be refused for a detached child, and escalation is the designed answer.

### 2.3 Six machine states, three owner sentences

| `stop_state` | `exit_state` | readiness / `error_code` | owner sentence |
|---|---|---|---|
| `PROCESS_EXITED` | `EXITED` | `…LAUNCHER_STOPPED` | The Toolkit stopped safely. |
| `PROCESS_EXITED_RUNTIME_STATE_STALE` | `EXITED` | `…LAUNCHER_STOPPED` | The Toolkit stopped safely. |
| `PROCESS_STILL_ALIVE` | `RUNNING` | `…FAILED` / `CONSOLE_DID_NOT_STOP` | The Toolkit is still running. … |
| `PORT_CLOSED_PROCESS_ALIVE` | `RUNNING` | `…FAILED` / `CONSOLE_DID_NOT_STOP` | The Toolkit is still running. … |
| `TERMINATION_REQUEST_FAILED` | `RUNNING` or `UNPROVEN` | `…FAILED` | per `exit_state` |
| `PROCESS_STATE_UNPROVEN` | `UNPROVEN` | `…FAILED` / `CONSOLE_EXIT_NOT_PROVEN` | The Toolkit could not confirm … |

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

### 2.7 Live end-to-end verification, on the owner's port

Real accepted Phase 7.13 console, real port 8780, real launcher CLI — the exact path that failed.
Started with `--no-browser` so no window was opened unprompted.

```
start  readiness=SESSION7_14_LAUNCHER_READY   pid=17696  startup_seconds=0.55  exit 0
stop   readiness=SESSION7_14_LAUNCHER_STOPPED pid=17696  stop_seconds=3.27     exit 0
       exit_state=EXITED   stop_state=PROCESS_EXITED
       "The Toolkit stopped safely."
stop   readiness=SESSION7_14_LAUNCHER_ALREADY_STOPPED  exit 0
       "The toolkit was not running, so there was nothing to stop."   (baseline wording, unchanged)
```

```json
"exit_verification": {"source": "windows_process_handle", "handle_held": true,
                      "wait_result": 0, "wait_name": "WAIT_OBJECT_0",
                      "get_exit_code_ok": true, "exit_code": 1, "still_active": false}
"terminate_requests": [{"api": "GenerateConsoleCtrlEvent", "hard": false, "ok": false, "error": "OSError"},
                       {"api": "TerminateProcess",         "hard": true,  "ok": true,  "error": null}]
"termination_request_failed": false,  "runtime_state_cleared": true,
"command_identity_verified": true,    "command_identity_evidence": "ACCEPTED_HEALTH_CONTRACT",
"port_open_after_stop": false,        "health_reachable_after_stop": false
```

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
stop.signalled hard=False pid=17696 requested=False
stop.escalated hard=True  pid=17696 requested=True
stop.stopped   escalated=True exit_state=EXITED pid=17696 stop_state=PROCESS_EXITED waited=3.27
```

---

## 3. Owner messages

| situation | text |
|---|---|
| Success | `The Toolkit stopped safely.` |
| Still running | `The Toolkit is still running. Nothing else on this computer was stopped. Open technical details for the recorded reason.` |
| Unproven | `The Toolkit could not confirm that the local server stopped safely. Nothing else on this computer was stopped. Open technical details for the recorded reason.` |

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

## 4. Tests — **+38**, focused suite 465 → **503**

Real Windows child processes; seam tests retained but no longer the only evidence. Every child is
spawned by the test run itself (`python -c "import time; time.sleep(...)"`), signalled only by a PID
the test spawned, and reaped on cleanup.

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

**These tests are defect-specific**, not tautological: `test_x02` and `test_x03` characterize the
*broken* primitive directly, so they fail if the Windows semantics are ever misdescribed, and
`test_x17b` asserts that a handle-proven exit overrides a `process_alive()` that still claims life —
which is the defect inverted. The load-bearing differential, however, is §4.1: the real baseline
`Launcher.stop()` measured against the real HEAD `Launcher.stop()`.

### 4.1 End-to-end differential — baseline vs HEAD, identical conditions

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
| owner sentence | "The toolkit did not stop within the allowed time…" | "The Toolkit stopped safely." |
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
| Focused Phase 7.14 suite | **503 ran, 1 failure** — `test_199e_no_acceptance_tag_yet` (stale, see below) |
| New stop-exit classes only | **38 ran, OK**, 0 skipped |
| Amazon boundary + network + connectivity policy | **47 ran, OK**, exit 0 |
| `scripts/connectivity_scan.py` | 96 files, **active amazon-account paths: 0**, exit 0 |
| `compileall production/ tests/` | exit **0** |
| `py_compile` on both changed files | OK |
| Full in-place suite | *see §5.1* |

### 5.1 Full in-place suite

`python -m unittest discover -s tests` → **Ran 4668 tests in 1129.770 s — FAILED (failures=1,
errors=0, skipped=4), exit 1.** The single failure is `test_199e_no_acceptance_tag_yet`.

Accepted baseline for comparison (recorded in the accepted navigation-hotfix acceptance report at
`5fcbf6f`): **4630 ran, 1 failure, 0 errors, 4 skipped**, 879.9 s, exit 1.

| | baseline | HEAD | delta |
|---|---|---|---|
| ran | 4630 | **4668** | **+38** |
| failures | 1 | **1** | **0** |
| errors | 0 | **0** | **0** |
| skipped | 4 | **4** | **0** |
| exit | 1 | 1 | — |

**Verdict: `BASELINE_EQUIVALENT_NONZERO`.** The `+38` is exactly the 38 tests this hotfix adds. The
failure set, the error count and the skip count are all unchanged.

**This suite is not green, and is not reported as green.**

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
6. **A microsecond-scale PID-recycle window remains between the start-token check and the handle
   being opened.** The handle is opened after identity is proven, so in principle the console could
   exit and Windows recycle its PID in that gap. The window is orders of magnitude smaller than the
   baseline's — which spanned the entire 15 s wait, unguarded (§2.1) — and closing it completely
   would mean pinning before verifying, a larger restructure than this bounded hotfix warrants. It
   is recorded here rather than silently accepted.
7. **No live end-to-end console stop is included in the committed suite.** The real-process tests use
   stand-in children rather than the console itself, to keep the suite offline, port-free and
   parallel-safe. A live launcher run is recorded in the proof document instead.

---

## 8. Files changed

| file | change |
|---|---|
| `production/phase7_owner_launcher.py` | exit verification, terminate-result capture, six stop states, owner messages, `command_identity_verified` correction |
| `tests/test_phase7_14_owner_usability_pilot_readiness.py` | +38 tests; `test_h03` / `test_h07` updated to the new owner-message contract |
| `SESSION7_14-STOP-EXIT-VERIFICATION-HOTFIX-REPORT.md` | this report |
| `SESSION7_14-STOP-EXIT-VERIFICATION-HOTFIX-PROOF.json` | machine-readable proof |

No other production module, no accepted business authority, no `CAPABILITIES.json`, no launcher
script (`.bat` / `.ps1`), no console static asset, and no document under `docs/` was modified.

---

## 9. Status

Branch pushed. **Not merged. No acceptance tag. `main` untouched.** Independent audit requested.
