# Phase 7.14 Stop Owner-Message Hotfix — Independent Acceptance Audit

**Decision: `PHASE7_14_STOP_OWNER_MESSAGE_HOTFIX_ACCEPTED`**

Audited commit: `6c5c2490825b8271895def95cc7b94406a22ce78`
Accepted Phase 7.14 baseline: `b3e357e27e60ff306d861f13a803a8a1f009817b`
Auditor: independent acceptance audit. No production code was modified.

Every material claim below was reproduced from repository bytes and from independent fixtures
written by this audit. The implementation report, the proof JSON, its test counts, its root-cause
description, its process-safety claims and its fresh-worktree claims were treated as unverified
input, not as evidence.

---

## A. Git provenance

**1. Branch.** `git rev-parse --abbrev-ref HEAD` = `hotfix-phase7-14-stop-owner-message`. Expected.

**2. Working tree clean before audit.** `git status --porcelain` returned empty at audit start, and
again after all live process tests and all suite runs. `runs/` is gitignored and holds no tracked
file (`git ls-tree -r --name-only HEAD | grep ^runs/` = 0).

**3. HEAD.** `6c5c2490825b8271895def95cc7b94406a22ce78`. Expected.

**4. Remote hotfix HEAD.** `git rev-parse origin/hotfix-phase7-14-stop-owner-message` =
`6c5c2490825b8271895def95cc7b94406a22ce78` — identical to local.

**5. Implementation commit.** `fa203bf918a3fb08612d6e327280269a19ea2640`, parent
`b3e357e27e60ff306d861f13a803a8a1f009817b`. Expected.

**6. Proof commit.** `6c5c2490825b8271895def95cc7b94406a22ce78`, parent `fa203bf9…`. Expected.

**7. Descent from baseline.** `git merge-base --is-ancestor b3e357e HEAD` succeeded. The hotfix
descends from the accepted Phase 7.14 baseline.

**8. Checkpoint peels to baseline.** `phase7-14-stop-owner-message-hotfix-checkpoint-b3e357e^{}` =
`b3e357e27e60ff306d861f13a803a8a1f009817b`. Exact.

**9. Accepted Phase 7.14 tag intact.**
`phase7-14-owner-usability-pilot-readiness-accepted-b3e357e^{}` =
`b3e357e27e60ff306d861f13a803a8a1f009817b`; it is an annotated `tag` object whose id is
`a629cd701975926b8223482c005c338eddff6bf2`, matching the value the proof records. Not moved, not
rewritten.

**10. main / origin/main.** Both `3f758debc31bcf0b4e50d9693798e99910c64110`. Unchanged. Not merged.

**11. No Stop-message acceptance tag existed.** `git tag -l "*stop-owner-message*"` returned only the
checkpoint tag before this audit.

**12. No accepted tag was moved.** All 12 pre-existing `*-accepted-*` tags still peel to the commits
named in their own tag names; the full tag list is unchanged apart from the hotfix checkpoint.

**13. No pilot runtime records committed.** The complete file set touched across `b3e357e..HEAD` is
four paths (finding 14). No `runs/` path, no pilot artifact, no launcher log or status document.

**14. No Phase 8 work.** `git tag -l "*phase8*" "*phase-8*"` and the matching branch listing are
empty; the suite's own `test_199f_no_phase_8_work` passes.

---

## B. Diff scope

**15. Changed files — exactly the expected four.**

```
A  SESSION7_14-STOP-OWNER-MESSAGE-HOTFIX-PROOF.json
A  SESSION7_14-STOP-OWNER-MESSAGE-HOTFIX-REPORT.md
M  production/phase7_owner_launcher.py
M  tests/test_phase7_14_owner_usability_pilot_readiness.py
```

Exactly **one** production file. No undocumented production file changed.

**16. Production insertion/deletion counts reconciled.** `git diff --numstat` reports **37
insertions, 8 deletions** for `production/phase7_owner_launcher.py`. `git diff --stat` renders this
as `45 ++-`, because `--stat` shows insertions **plus** deletions (37 + 8 = 45).

The report's diff block states `production/phase7_owner_launcher.py | 37 + 8 -` and
`2 files changed, 261 insertions(+), 8 deletions(-)`. Both are exactly right: 37 is the insertion
count, explicitly paired with the 8 deletions, and 37 + 224 = 261 for the fix commit. Independently
counted added lines: 36 non-blank + 1 blank = 37. **There is no line-count inaccuracy to reconcile**
— the documented figure and Git agree, and the 45 is simply the combined `--stat` total.

**17. The 8 deletions are exactly what the report says.** They are the 7 stop-path
`_owner_message(...)` call-site lines that gained `phase="stop"`, plus the old
`def _owner_message(readiness, code, detail):` signature line. Verified line by line against the
diff.

**18. Launcher scripts unchanged.** `Start-AMZ-Toolkit.bat`, `Start-AMZ-Toolkit.ps1`,
`Stop-AMZ-Toolkit.bat`, `Stop-AMZ-Toolkit.ps1`, `Open-AMZ-Toolkit.bat`, `Open-AMZ-Toolkit.ps1` — all
report an empty diff against `b3e357e`. All three `.ps1` files are pure ASCII (0 bytes > 127).

**19. Console backend, frontend and next-action authority unchanged.**
`production/phase7_unified_owner_console.py`, `production/phase7_unified_owner_console_static/`
and `production/phase7_owner_next_action.py` all diff empty against `b3e357e`.

**20. Amazon boundary files unchanged.** `production/phase7_owner_notification_delivery.py`,
`production/phase7_connected_backup_recovery.py`,
`production/phase7_connected_research_watchlists.py`, `core/network_policy.py` — all diff empty.

---

## C. Root cause

**21. Baseline defect reproduced from bytes, not from the report.** An independent harness loaded the
**baseline** `production/phase7_owner_launcher.py` from a detached `b3e357e` worktree and drove the
real `Launcher.stop()` through injected seams. A stop that times out produced:

```
readiness    : SESSION7_14_LAUNCHER_FAILED
error_code   : CONSOLE_DID_NOT_STOP
owner_message: "The toolkit could not be started. See the launcher log for the recorded reason."
```

The defect is real and is exactly as described.

**22. Shared readiness state is the mechanism.** `LAUNCHER_FAILED` is emitted by the start path
(`CONSOLE_SPAWN_FAILED`, `CONSOLE_EXITED_DURING_STARTUP`) **and** by the stop path
(`CONSOLE_DID_NOT_STOP`). Baseline `_owner_message(readiness, code, detail)` ignored `code` entirely
and keyed only on `readiness`, so the stop timeout inherited the start sentence. Confirmed by reading
the baseline function body.

**23. Secondary wording defect reproduced.** On baseline, all three `LAUNCHER_STOP_REFUSED` outcomes —
including `PROCESS_IDENTITY_UNPROVEN` — returned *"Stop refused: the recorded process is not the
console this launcher started, so nothing was stopped."* That asserts a **proven** mismatch. For
`PROCESS_IDENTITY_UNPROVEN` the launcher explicitly could not prove identity either way, so the
baseline sentence was factually wrong. Confirmed.

**24. The fix mechanism is error-code dispatch, not a global replacement.** The feature adds a
separate `_STOP_OWNER_MESSAGES` dict keyed by canonical `error_code`, a `STOP_FAILED_MESSAGE`
fallback, and a `phase=None` parameter. The lookup runs only inside `if phase == "stop":`. This is
not a broad substitution and cannot reach Start or Open (findings 30–34).

**25. Exact phase scoping — literal equality.** The guard is `phase == "stop"`, an exact string
comparison with no normalization, no `.lower()`, no `.strip()`, no membership test. All seven stop
call sites pass the literal `"stop"`.

**26. `_owner_message` has no other callers.** A repo-wide search (`*.py`, `*.js`, `*.ps1`, `*.bat`)
found zero references outside `production/phase7_owner_launcher.py` and its own test file. It is a
private module function, so there is no external surface that could pass an unexpected `phase`.

**27. Call-site census: 20 before, 20 after.** Baseline and feature each have exactly 20
`_owner_message(` call sites. Exactly 7 changed — all inside `Launcher.stop()` (lines 984, 989, 998,
1009, 1019, 1045, 1051). The 10 start sites and 3 open/status sites are **byte-identical** and pass
no `phase` argument at all.

---

## D. Stop owner-message matrix

Every row below was produced by driving the real `Launcher.stop()`. Canonical fields were captured
alongside the sentence.

**28. `CONSOLE_DID_NOT_STOP` (timeout).** readiness `SESSION7_14_LAUNCHER_FAILED`, error_code
`CONSOLE_DID_NOT_STOP` → *"The toolkit did not stop within the allowed time. Nothing else on this
computer was stopped. See the launcher log for the recorded reason."* Required semantics met.

**29. `PROCESS_IDENTITY_UNPROVEN`.** readiness `SESSION7_14_LAUNCHER_STOP_REFUSED` → *"The toolkit
was not stopped because the launcher could not safely verify the process identity. Nothing on this
computer was stopped."* It claims inability to verify, **not** a proven mismatch. Required semantics
met.

**30. `PID_REUSED_BY_ANOTHER_PROCESS` (PID reuse).** readiness `SESSION7_14_LAUNCHER_STOP_REFUSED`,
`stale_pid_cleared=True` → *"The process was not stopped because it was not started by this launcher.
The recorded process number now belongs to a different program, so nothing was stopped."* Required
semantics met.

**31. `NOT_LAUNCHER_OWNED` (unrelated process).** readiness `SESSION7_14_LAUNCHER_STOP_REFUSED` →
*"The process was not stopped because it was not started by this launcher. A console is answering on
this port, but this launcher did not start it, so nothing was stopped."* Required semantics met.

**32. Stale PID.** readiness `SESSION7_14_LAUNCHER_ALREADY_STOPPED`, `stale_pid_cleared=True` → *"The
toolkit was not running, so there was nothing to stop."* Byte-identical to baseline.

**33. Already stopped.** readiness `SESSION7_14_LAUNCHER_ALREADY_STOPPED` → same sentence.
Byte-identical to baseline.

**34. Successful stop.** readiness `SESSION7_14_LAUNCHER_STOPPED`, `identity_verified=True` → *"The
toolkit has stopped."* Byte-identical to baseline. A successful stop after hard escalation gives the
same sentence.

**35. No recorded start token.** Identity checks are skipped exactly as on baseline; the stop
proceeds and reports `SESSION7_14_LAUNCHER_STOPPED`. Byte-identical to baseline.

**36. Generic Stop failure (unknown / malformed / missing code).** With `phase="stop"` and
`readiness=LAUNCHER_FAILED`, an unknown code, an empty code and a `None` code all return *"The
toolkit could not be stopped. See the launcher log for the recorded reason."* Required semantics met.

**37. No Stop failure or refusal claims a failed start.** Across all nine stop outcomes, the string
`"could not be started"` appears **zero** times. The two unrelated/mismatch sentences do contain the
words *"not started by this launcher"* — that is the required wording in the audit brief itself and
is a correct statement of provenance, not a claim that starting failed.

**38. Owner text is bounded and clean.** Every sentence is a single short paragraph of plain prose.
No stack trace, no exception text, no file path and no raw code string appears in any owner sentence.

---

## E. Canonical field preservation

**39. Only `owner_message` differs — nothing else.** The harness diffed the full result envelope
field by field for all nine stop scenarios. The only differing key in any scenario is
`owner_message`. `readiness`, `phase`, `error_code`, `error_detail`, `pid`, `signalled`,
`identity_verified`, `command_identity_verified`, `stale_pid_cleared`, `escalated`, `stop_seconds`,
`host`, `port`, `console_url`, `automatic_port_selection`, `browser_opened`, `browser_attempted`,
`launcher_never` and `seller_central_counters` are all identical between baseline and feature.

**40. Readiness values are exactly the accepted values.** `SESSION7_14_LAUNCHER_FAILED`,
`…_STOP_REFUSED`, `…_STOPPED`, `…_ALREADY_STOPPED` — unchanged in every scenario.

**41. Error codes remain in their canonical field.** `error_code` still carries
`CONSOLE_DID_NOT_STOP`, `PROCESS_IDENTITY_UNPROVEN`, `PID_REUSED_BY_ANOTHER_PROCESS`,
`NOT_LAUNCHER_OWNED`. The owner text is a **separate** field; it never replaces the code.

**42. No raw error-code leak.** For every stop outcome carrying a code, the code string does not
appear anywhere inside `owner_message`. Independently asserted, and pinned by the suite's own
`test_h10c`.

**43. The accepted `_OWNER_MESSAGES` table is provably unedited.** The harness dumped the live dict
from both modules and compared: **identical**. The diff also shows the table only as unchanged
context. `LAUNCHER_FAILED` still maps to *"The toolkit could not be started."* for the start path.

**44. Timing and policy constants unchanged.** `STOP_TIMEOUT_SECONDS=15.0`, `STOP_GRACE_SECONDS=3.0`,
`STOP_POLL_INTERVAL=0.25`, `START_TIMEOUT_SECONDS=45.0`, `HEALTH_POLL_INTERVAL=0.5`,
`HEALTH_REQUEST_TIMEOUT=3.0`, `AUTOMATIC_PORT_SELECTION=False`, `DEFAULT_HOST=127.0.0.1`,
`DEFAULT_PORT=8780` — all identical between baseline and feature.

---

## F. Phase-scoping attacks

**45. Seventeen phase variants tested against ten readiness/code combinations.** Variants:
`"stop"`, `"start"`, `"open"`, `"STOP"`, `"Stop"`, `" stop"`, `"stop "`, `"  stop  "`, `""`, `None`,
`"shutdown"`, `"preflight"`, `"health"`, `"ready"`, `"lock"`, `0`, `True`.

**Result: only the literal `"stop"` changes any output.** All 16 non-canonical variants return
byte-identical text to the baseline module, including the case variants and the whitespace-padded
variants. Exactly 7 changed rows exist in the whole matrix, all under `phase="stop"`.

**46. No Stop wording leaks into Start or Open.** With `phase="start"` and `phase="open"`, a
`CONSOLE_DID_NOT_STOP` code still returns the baseline start sentence. Start-only readiness states
(`READY`, `PORT_BLOCKED`, `NOT_RUNNING`, `BROWSER_UNAVAILABLE`) are unchanged **even when
`phase="stop"` is forced**, because those readiness values are not `LAUNCHER_FAILED` and their codes
are not in the stop table.

**47. Case variants fall back safely, and are unreachable.** `"STOP"` / `"Stop"` do not trigger stop
wording; they return baseline text. This is conservative rather than wrong, and it is unreachable in
production: all seven stop call sites pass the literal lowercase `"stop"`, and the function has no
external callers (finding 26).

**48. Malformed inputs do not raise.** `phase=0`, `phase=True`, `phase=None`, `code=None`,
`code=""`, and an unknown readiness string all return a string without raising.

**49. Unknown readiness under `phase="stop"`.** Returns the code-matched stop sentence rather than the
baseline empty string. Benign and unreachable — `readiness` is always one of the canonical constants
at every call site.

---

## G. Start immutability

**50. Fourteen Start scenarios, all byte-identical to baseline.** Driven through the real
`Launcher.start()`: successful start; browser-unavailable; already-running; port-blocked by an
unrelated listener; console spawn failure; console crash during startup; health timeout; launcher
locked; locked-but-already-running; workspace not writable; plus the preflight failures for
unsupported Python, missing console module and missing imports.

Every one returns the identical `readiness`, `phase`, `error_code` and — critically — the **identical
`owner_message`** on both sides. Specific wording confirmed unchanged for start success
(`LAUNCHER_READY`), start failure (`CONSOLE_SPAWN_FAILED` → *"The toolkit could not be started…"*),
port conflict (`PORT 8780 IS ALREADY IN USE …`) and start timeout (`LAUNCHER_TIMEOUT`).

**51. Start control flow is byte-identical.** The diff touches no line inside `start()`,
`_start_locked()`, `_already_running()`, `_await_health()`, `_clear_stale_pid()` or
`console_command()`. The two diff hunks are confined to lines 969–1066 (`stop()`) and 1105–1163 (the
message tables and helper).

---

## H. Open immutability

**52. Five Open scenarios, all byte-identical to baseline.** Healthy console; unhealthy console;
absent console; malformed (empty) health response; browser-open failure. Identical `readiness`,
`error_code` and `owner_message` on both sides.

**53. Open never starts a server.** Every Open result carries `started_a_server=False`, and no spawn
seam was invoked in any Open scenario.

**54. Open uses only the fixed loopback URL.** The browser-unavailable sentence appends
`console_url(DEFAULT_HOST, DEFAULT_PORT)` = `http://127.0.0.1:8780`, a compile-time constant. Open
accepts no URL argument, and the CLI exposes no host/port override that can reach a non-loopback
address — `validate_host` rejects anything but loopback. Unchanged by this hotfix.

**55. Open is unaffected by stop dispatch.** Its two call sites pass no `phase`; the attack matrix
confirms Open readiness states are unchanged even under a forced `phase="stop"`.

---

## I. Stop control-flow immutability

**56. Nothing in the stop algorithm changed.** Reading the full `stop()` body against baseline, the
only textual differences are the seven `owner_message=` argument lists. Unchanged: PID lookup
(`ws.read_pid()`); the launcher-owned-PID requirement; `process_start_token` verification;
PID-reuse refusal; the ordering that checks *unprovable* identity **before** *mismatched* identity;
the health-based command-identity probe; signal selection (`_terminate(pid, hard=False)` then
`hard=True`); the bounded `_await_exit` waits; `min(STOP_GRACE_SECONDS, self.stop_timeout)` and
`max(0.0, self.stop_timeout - waited)`; `clear_pid()` state cleanup; lock handling; and
`exit_code_for(readiness)`.

**57. Identity-unproven ordering verified behaviourally.** With a recorded token and an unreadable
current token, the result is `PROCESS_IDENTITY_UNPROVEN`, never `PID_REUSED_BY_ANOTHER_PROCESS`.

**58. Termination-path scan of the production diff: clean.** Grepping the added lines for
`terminate`, `kill`, `taskkill`, `signal`, process-name matching, `python.exe`, `subprocess`,
`shell=True`, `os.system`, `eval`, `exec` returned **zero** matches. The hotfix adds only two dict
literals, one string constant and a four-line guard.

**59. No new termination path exists.** `terminate_process()` is untouched: it signals **one**
integer PID, never searches for a process, never matches by name and never walks a process tree. The
only `subprocess` use is the single fixed `console_command()` spawn — a list argv with no
`shell=True`. `LAUNCHER_NEVER` still asserts all 15 prohibitions, identical on both sides.

**60. Termination calls are identical between baseline and feature.** In every one of the nine stop
scenarios the recorded `_terminate` call list matched exactly. Refusals record **zero** calls on both
sides; the timeout records exactly two (soft then hard) on the **same** PID on both sides.

---

## J. Live safety tests

All five ran against real OS processes. In tests C and D the **real** `terminate_process` was left in
place, so an incorrect refusal decision would have visibly killed a real process.

**61. A — unrelated listener on 127.0.0.1:8780.** Start refused with readiness
`SESSION7_14_LAUNCHER_PORT_BLOCKED`, `error_code=PORT_IN_USE_BY_ANOTHER_PROCESS`,
`automatic_port_selection=false`, and **no `pid` key at all** — nothing was spawned. The unrelated
listener (PID 18124) was confirmed still running afterwards via `Get-Process`. No random port was
selected. Exit code 1.

**62. B — console started outside the launcher.** A real console was started directly (PID 20164) and
confirmed healthy. `stop` refused: readiness `SESSION7_14_LAUNCHER_STOP_REFUSED`,
`error_code=NOT_LAUNCHER_OWNED`, `signalled=false`, exit 1, with the correct owner sentence. The
outside console **remained alive**. The set of running `python` PIDs was byte-identical before and
after. No launcher-owned stopped state was recorded — the PID record remained `null`.

**63. C — identity unproven, against a real live process.** `signalled=false`,
`identity_verified=false`, `error_code=PROCESS_IDENTITY_UNPROVEN`, the real process **still alive**.
The wording claims only that identity could not be verified; it does not claim a proven mismatch.

**64. D — PID reuse, against a real live process.** A real process was recorded with a deliberately
wrong start token. Result: `PID_REUSED_BY_ANOTHER_PROCESS`, `signalled=false`,
`stale_pid_cleared=true`, and the real process **still alive**. The owner text accurately describes a
safe refusal.

**65. E — launcher-owned console.** `start` (separate process) → `SESSION7_14_LAUNCHER_READY`, exit 0,
PID 20772. `stop` (separate process) → `SESSION7_14_LAUNCHER_STOPPED`, `identity_verified=true`,
`signalled=true`, exit 0, *"The toolkit has stopped."* The owned console was gone afterwards, and an
unrelated bystander Python process (PID 20824) spawned beforehand was **still alive**. Exactly one
launcher-owned console was stopped.

---

## K. Timeout

**66. Bounded timeout behaviour verified live.** A launcher-owned real process that does not exit
produced: `readiness=SESSION7_14_LAUNCHER_FAILED` (the accepted canonical value),
`error_code=CONSOLE_DID_NOT_STOP`, `escalated=true`, `stop_seconds=15.03` against the configured
`STOP_TIMEOUT_SECONDS=15.0`, wall clock 15.1 s. The accepted bound is unchanged.

**67. Owner message names a stop, never a start.** *"The toolkit did not stop within the allowed
time. Nothing else on this computer was stopped…"* The string `"did not start"` does not appear.

**68. No extra aggression, and cleanup stays safe.** Exactly two terminate calls were recorded —
`hard=False` then `hard=True` — both against the **same** PID. No third escalation, no name-based
sweep, no tree kill. The PID record is deliberately **not** cleared on a failed stop, so a later stop
still knows what it owns.

---

## L. Test evidence

**69. Baseline defect-detection reproduced exactly.** The feature test file was copied into a clean
detached `b3e357e` worktree and run against the **unfixed** launcher:

```
Ran 21 tests — FAILED (failures=6, errors=2)   unmasked exit code 1
```

6 failures + 2 errors + **13 controls passing** — precisely the reported figures. The 8 non-passing
node IDs match the proof's list exactly:

`test_h01_stop_timeout_never_says_started`, `test_h01b_no_stop_outcome_reports_a_start_failure`,
`test_h02_every_stop_failure_uses_the_verb_stopped`, `test_h02b_generic_stop_failure_wording`,
`test_h03_timeout_wording_is_accurate`, `test_h04_identity_refusal_wording_is_accurate`,
`test_h05_unrelated_process_wording_is_accurate`,
`test_h08d_stop_codes_never_leak_into_another_phase`.

The 2 errors are `AttributeError` on `L.STOP_FAILED_MESSAGE` and `L._STOP_OWNER_MESSAGES`, which do
not exist on baseline — a legitimate detection, not a broken test.

**70. Controls pass on both sides.** The same 13 control tests pass against baseline **and** feature.
Against the unmutated feature code the class is 21/21 `OK`, exit 0.

**71. Tests exercise real behaviour, not source strings.** Each outcome builder writes a real PID
record into a real `Workspace` and calls the real `Launcher.stop()` through injected seams, then
asserts on the returned envelope. Only `test_h11` is a source scan, and it is an additional
prohibition check rather than the mechanism under test.

**72. Mutation testing — 5 mutations planted, 5 killed.**

| Mutation | Verdict | Killed by |
|---|---|---|
| `M1` drop the `phase == "stop"` guard (dispatch globally) | KILLED | `h08`, `h08b`, `h08d` |
| `M2` widen the guard to `("stop","start",None)` | KILLED | `h08`, `h08b`, `h08d` |
| `M3` identity-unproven text falsely claims a proven mismatch | KILLED | `h04` |
| `M4` remove the generic stop fallback | KILLED | `h02b` |
| `M5` revert the timeout sentence to the start wording | KILLED | `h01`, `h01b`, `h02`, `h03` |

**Start controls and phase-scoping tests are load-bearing**: M1 and M2 are precisely the
"Stop dispatch leaks into Start/Open" regressions, and the Start control tests catch both. Process-
safety controls (`h10`, `h10b`, `h11b`) and Open controls (`h09`, `h09b`) remained green throughout,
confirming they gate the properties they claim. The production file was restored byte-identically
afterwards (verified by `diff`).

**73. Stale pre-acceptance guard — confirmed stale, not a regression.**
`test_199e_no_acceptance_tag_yet` asserts every tag containing `7-14`/`7.14` also contains
`checkpoint`. It fails **identically** on the untouched accepted baseline and on the hotfix, with the
same assertion text:

```
AssertionError: 'checkpoint' not found in
'phase7-14-owner-usability-pilot-readiness-accepted-b3e357e' : unexpected 7.14 tag
```

Both runs exit 1. This hotfix did not create the problem — the **prior independent Phase 7.14 audit**
legitimately created that tag, which invalidated a guard written before acceptance existed. It hides
no other regression: it inspects only the tag list. No tag was moved or deleted to make it pass
(finding 12). The proof identifies it correctly as a stale pre-acceptance guard.

It was **not** skipped, deleted or weakened during this audit. **Recorded as maintenance backlog**:
retire or re-scope it in the next Phase 7.14-touching change. Note that the acceptance tag created by
*this* audit will be a second tag it flags — expected, and part of the same backlog item.

**74. Request-size transient (WinError 10053) — investigated, not assumed.**
`tests/test_phase7_13_unified_owner_console.py` and `production/phase7_unified_owner_console.py` are
**byte-identical** between baseline and HEAD (empty diff), and neither imports nor references the
launcher module — so the hotfix code cannot participate in the failing path.

Isolated reruns of `TestBody.test_52_request_size_bounded`, nothing else running:

| Side | Runs | Pass | Fail |
|---|---|---|---|
| Feature (primary working copy) | 30 | 24 | 6 |
| Accepted baseline `b3e357e` (fresh worktree) | 30 | 27 | 3 |

Both sides flake, in the same family (`WinError 10053` connection-aborted and `WinError 10054`
connection-reset). The 6-vs-3 difference over n=30 is not statistically distinguishable and cannot
be causal, since the executing bytes are identical. The test also passed in **both** fresh-worktree
full runs and in the full in-place run. Classification as an environment transient is **supported by
evidence**, not asserted. No new error rate is introduced by the hotfix.

**75. PowerShell 5.1 launcher QA — reproduced live.** Edition confirmed `5.1.26100.8875`, `Desktop`.

| Script | State | readiness | Exit | Owner sentence |
|---|---|---|---|---|
| `Stop-AMZ-Toolkit.ps1` | nothing running | `…_ALREADY_STOPPED` | 0 | *"The toolkit was not running, so there was nothing to stop."* |
| `Open-AMZ-Toolkit.ps1` | nothing running | `…_NOT_RUNNING` | 1 | *"The toolkit is not running yet. Run Start-AMZ-Toolkit first."* |
| `Stop-AMZ-Toolkit.ps1` | console started outside the launcher | `…_STOP_REFUSED` / `NOT_LAUNCHER_OWNED` | 1 | *"The process was not stopped because it was not started by this launcher. A console is answering on this port…"* |

In the third run the outside console (PID 17632) was confirmed **still alive** afterwards. The
wrapper's own lines — *"The toolkit was not stopped. The reason is printed above. / Nothing else on
this computer was stopped."* — are stop-accurate and were not modified.

---

## M. Suite reproduction (unmasked exit codes)

**76. Focused and regression suites.**

| # | Command | Ran | Fail | Err | Skip | Exit | Non-passing |
|---|---|---|---|---|---|---|---|
| 1 | `unittest …TestStopOwnerMessage` (feature) | 21 | 0 | 0 | 0 | 0 | — |
| 2 | `unittest …TestStopOwnerMessage` (baseline `b3e357e`) | 21 | 6 | 2 | 0 | 1 | the 8 in finding 69 |
| 3 | `unittest tests.test_phase7_14_owner_usability_pilot_readiness` | 439 | 1 | 0 | 0 | 1 | `test_199e` only |
| 4 | `unittest tests.test_phase7_13_unified_owner_console` | 269 | 0 | 0 | 0 | 0 | — |
| 5 | `unittest tests.test_phase7_12_owner_notification_delivery` | 234 | 0 | 0 | 0 | 0 | — |
| 6 | `unittest …TestActionModalDom` (7.13 modal harness) | 1 | 0 | 0 | 0 | 0 | — |
| 7 | `unittest …TestDomRenderContract` (7.14 DOM contract) | 1 | 0 | 0 | 0 | 0 | — |
| 8 | `unittest tests.test_amazon_boundary` | 26 | 0 | 0 | 0 | 0 | — |
| 9 | `unittest tests.test_connectivity_policy` | 16 | 0 | 0 | 0 | 0 | — |
| 10 | `unittest tests.test_connectivity_surface` | 19 | 0 | 0 | 0 | 0 | — |
| 11 | `unittest tests.test_network_policy` | 5 | 0 | 0 | 0 | 0 | — |

The 7.14 focused suite is 439 against the baseline's 418 — **+21 exactly**.

**77. Full in-place suite.** `python -m unittest discover -s tests`, primary working copy:

```
Ran 4604 tests in 916.593s
FAILED (failures=1, skipped=4)          unmasked exit code 1
```

The **only** non-passing node is `test_199e_no_acceptance_tag_yet` (finding 73). `TestStopOwnerMessage`
contributes zero non-passing nodes. This is one better than the implementation's own record
(which also hit the 10053 transient); the difference is that transient, not code.

Against the Phase 7.14 acceptance record of 4583 tests, this is **+21 exactly**.

**78. compileall.** `python -m compileall -q production core tests` — exit 0 in the primary working
copy and in both fresh worktrees.

---

## N. Mandatory fresh-worktree differential

**79. Method.** Two detached worktrees at `b3e357e` and `fa203bf`, both verified clean with `runs/`
absent, run **sequentially** (not concurrently, to remove loopback-port contention as a variable)
with the identical Python `3.12.10`, identical environment, identical command
`python -m unittest discover -s tests`, and full output captured on both sides.

**80. Baseline worktree `b3e357e`.**

```
Ran 4581 tests in 466.164s
FAILED (failures=2, errors=14, skipped=329)     exit 1
```

**81. Feature worktree `fa203bf`.**

```
Ran 4602 tests in 465.428s
FAILED (failures=2, errors=14, skipped=329)     exit 1
```

**82. Differential.** **+21 tests exactly. 0 new failures. 0 new errors. 0 lost baseline passes. 0
broadened skips.** Both sides have **16** non-passing nodes and the sorted node sets are
**byte-identical** (`diff` produced no output). `TestStopOwnerMessage` contributes **zero**
non-passing nodes on the feature side — all 21 new tests pass in a clean checkout.

**83. Why nonzero — verified, not accepted on assertion.** The 14 errors are all T2-data-dependent
(`test_backend_semantic_quality`, `test_backend_phrase_integrity`, `test_session5d_certification`)
and cannot find their inputs because `runs/T2` is gitignored. The 2 failures are `test_199e` and one
further T2-dependent assertion. A fresh worktree here is never absolutely green; the differential is
therefore judged **relatively**.

**84. Classification: `FRESH_WORKTREE_FULL_SUITE_BASELINE_EQUIVALENT_NONZERO`.** Proven, not assumed.
Neither fresh-worktree full suite is called green.

**85. Worktrees removed.** All three detached worktrees (baseline, feature, mutation) were removed
with `git worktree remove --force` and pruned. `git clean` was never run in the primary workspace.
The primary tree is clean and still at `6c5c249`.

---

## O. Source immutability

**86. Byte identity for everything outside the four changed paths.** `git diff --name-only b3e357e
HEAD` returns exactly the four paths in finding 15. Every other accepted file is byte-identical by
construction. Explicitly confirmed empty for: `production/phase7_owner_next_action.py`,
`production/phase7_unified_owner_console.py`, the console frontend static files, all six
Start/Stop/Open wrappers, the Phase 7.12 notification authority, the backup authority, the
research/watchlist authority, and `core/network_policy.py`. Session/CSRF protections, action
confirmation, the audit chain and the Seller Central counters live in those untouched files.

**87. Claimed SHA-256 values verified.** All four match exactly:

| File | SHA-256 | Matches proof |
|---|---|---|
| `production/phase7_owner_launcher.py` (hotfix) | `e770e6da…4e6f63` | yes |
| `tests/…pilot_readiness.py` (hotfix) | `73de681e…970805` | yes |
| `production/phase7_owner_launcher.py` (baseline) | `7296a59b…20ac8b` | yes |
| `tests/…pilot_readiness.py` (baseline) | `e1600dd1…a885d43c` | yes |

Working-copy hashes equal the Git blob hashes, confirming the `.gitattributes eol=lf` pin holds.

---

## P. Permanent Amazon boundary

**88. All Seller Central counters zero, and identical on both sides.** Captured live from a real
result envelope: `advertising_api_calls`, `buyer_messages_sent`, `review_requests_sent`,
`seller_account_mutations`, `seller_api_calls`, `seller_browser_automation_actions`,
`seller_bulk_uploads`, `seller_central_connections`, `seller_credential_store_count`,
`seller_report_downloads` — **all 0**.

**89. No prohibited integration introduced.** The hotfix adds no network call of any kind. No Seller
Central integration, login, OAuth, SP-API, Ads API, report download, campaign/bid/budget/keyword/
target/negative change, listing or inventory mutation, bulk upload, browser automation, buyer
messaging, review request or CAPTCHA handling appears in the diff. `LAUNCHER_NEVER` still asserts all
15 prohibitions, identical on both sides. The repository's own scanners
(`test_amazon_boundary` 26, `test_connectivity_policy` 16, `test_connectivity_surface` 19,
`test_network_policy` 5) all pass. **The owner remains the only manual bridge to Amazon.**

**90. General prohibited-path scan of active code: clean.** No arbitrary command execution, no
arbitrary subprocess, no `shell=True`, no `os.system`, no `eval`, no `exec`, no arbitrary URL opening,
no non-loopback binding, no broad `taskkill`, no service installation, no scheduler registration and
no remote-control path. The only `subprocess` use is the single fixed `console_command()` argv list.
Denial strings in `LAUNCHER_NEVER` and prohibition names inside test fixtures were distinguished from
active behaviour and are not executable paths.

---

## Q. Documentation accuracy

**91. The report and proof JSON are accurate.** Independently confirmed: root cause and secondary
wording defect; accepted baseline and its tag object id `a629cd70…`; all commits; the changed-file
set; production insertion/deletion counts (finding 16 — no inaccuracy); the phase-scoping mechanism;
error-code dispatch; `_OWNER_MESSAGES` immutability; Start/Open immutability; Stop control-flow
immutability; process-safety preservation; the live refusal result; the timeout result; the baseline
defect-detection run (6/2/13 and all 8 node IDs); the control tests; the stale acceptance-tag guard;
the WinError 10053 investigation; the full suite; the fresh-worktree nonzero result and its
classification; source immutability; prohibited integrations; and the `process_alive` backlog item.

**92. Two figures differ from the proof, both explained and neither an inaccuracy.** (a) The proof's
in-place run recorded `errors=1` from the 10053 transient; my run recorded `errors=0`, consistent
with the measured flake rate. (b) Wall-clock seconds differ (466 s vs the proof's 514 s per fresh
worktree) because I ran them sequentially rather than concurrently. Neither figure is a claim about
code behaviour.

**93. The `stop_never` block uses an inverted convention** — `"weakens_pid_verification": true` means
the launcher *never* does this. It is explicitly documented by the adjacent `note` field. Confusing
to read but not inaccurate; noted for future proof documents, not a defect.

**94. No production defect is being classified as documentation-only.** No production defect was
found.

---

## R. Known limitations (non-blocking)

**95. `process_alive()` Windows handle sensitivity — independently confirmed and characterised.** I
reproduced it: a terminated process still reports `process_alive() == True` while **any** open handle
to it remains (a spawning parent's un-released `Popen` handle keeps the kernel process object alive,
so the PID stays resolvable — and it stays resolvable to *other* processes too, not only the holder).
Releasing the handle makes `process_alive()` report `False` immediately.

- **Predates this hotfix and is unchanged**: the diff touches no line of `process_alive` or
  `_win_process_times`.
- **Not visible in the shipped flow**: the launcher runs one command per process, so the process that
  spawned the console has exited (closing its handle) before a separate Stop process queries the PID.
  Live test E confirms Stop correctly observed the owned console terminate.
- **Creates no unrelated-process termination risk.** The failure direction is fail-safe: a dead
  process looking alive can only make Stop wait longer or report `CONSOLE_DID_NOT_STOP`. It can never
  cause an unrelated process to be signalled — and PID reuse is independently guarded by the
  `process_start_token` check, which refuses before any signal.
- Accurately recorded for pilot / v1.0 hardening. **Not modified during this audit.**

**96. `start --no-browser` still prints the browser sentence.** Pre-existing, unchanged, and the
owner wrappers never pass that diagnostic flag. Backlog.

**97. Stale acceptance-tag guard.** Finding 73. Maintenance backlog.

---

## S. Decision

**98. No rejection trigger is present.**

- No Stop failure or refusal claims the toolkit could not be **started** — verified across all nine
  stop outcomes.
- Identity-unproven wording claims only an unverifiable identity, never a proven mismatch.
- Stop mapping cannot affect Start or Open — 16 of 17 phase variants are byte-identical to baseline,
  and mutation testing proves the Start controls catch any widening.
- Start and Open wording did not regress — 19 scenarios byte-identical.
- Canonical readiness, error codes, PID, token result, identity result and audit metadata are
  unchanged; `owner_message` is the only differing field.
- No raw error code leaks into owner text.
- Stop control flow is unchanged; process safety is preserved and demonstrated against real live
  processes; no unrelated process can be signalled; no new termination path exists.
- The full in-place suite does not regress; the fresh feature worktree is not worse than baseline.
- Changed files stay inside the narrow scope; the permanent Amazon boundary is intact.

**99. Decision: `PHASE7_14_STOP_OWNER_MESSAGE_HOTFIX_ACCEPTED`** — clean. No documentation correction
was required, so none was made. No production code was modified by this audit.

**100. Exact next action.** Merge is **not** authorised by this audit and was not performed. The
recommended next action is a single decision by the owner:

> Merge `hotfix-phase7-14-stop-owner-message` into `main` (fast-forward from
> `3f758debc31bcf0b4e50d9693798e99910c64110`), then begin the pilot.

Before that merge, the one piece of housekeeping worth folding in is retiring or re-scoping
`test_199e_no_acceptance_tag_yet`, which is now stale on every branch and will flag both the Phase
7.14 acceptance tag and this hotfix's acceptance tag. It is the only non-passing node in the full
in-place suite.

The pilot was not started. Phase 8 was not begun.
