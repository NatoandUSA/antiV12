# Session 7.14 — Owner Usability & Pilot Readiness — Implementation Report

| Field | Value |
|-------|-------|
| Branch | `phase7-14-owner-usability-pilot-readiness` |
| Baseline / origin/main / main | `3f758debc31bcf0b4e50d9693798e99910c64110` |
| Checkpoint tag | `phase7-14-owner-usability-pilot-readiness-checkpoint-3f758de` |
| Implementation commit | `49c045c3e80ecd204fac13eaadda46f1b28d12a2` |
| Proof commit | _(commit 2 — this report + the proof gate)_ |
| Acceptance tag | **none created** |
| Merged | **no** |
| Phase 8 | **not started** |

Phase 7.14 turns the accepted Phase 7.13 console into an application the owner can run by
double-clicking one file. It adds **no business authority**, **no analysis**, **no recommendation
algorithm**, and **no PPC**.

---

## 1. Permanent Amazon boundary

Unchanged and enforced. Nothing in this phase connects to Amazon Seller Central, uses a seller
sign-in or seller credentials, calls a seller or advertising API, downloads a seller report, mutates
a campaign / bid / budget / keyword / target / negative / listing / inventory, performs a bulk
upload, drives a seller browser, messages a buyer or requests a review.

Every seller-account counter is a constant zero in both the console and the launcher, and no code
path increments one. The owner remains the only manual bridge to Amazon; the UI never creates or
implies an automatic Amazon-side action.

## 2. Files created

| File | Purpose |
|------|---------|
| `production/phase7_owner_launcher.py` | Launcher Lite: start / stop / open / status / validate-only |
| `production/phase7_owner_next_action.py` | Smart next-action guidance (presentation read model only) |
| `Start-AMZ-Toolkit.bat` / `.ps1` | double-click start |
| `Stop-AMZ-Toolkit.bat` / `.ps1` | double-click stop |
| `Open-AMZ-Toolkit.bat` / `.ps1` | double-click open |
| `production/phase7_unified_owner_console_static/favicon.svg` | tab icon; removes the per-load 404 |
| `docs/PHASE7_14-OWNER-USABILITY-POLICY.md` | the usability + launcher policy |
| `docs/PHASE7_14-OWNER-PILOT-GUIDE.md` | the 14-day pilot |
| `docs/PHASE7_14-OWNER-PILOT-CHECKLIST.md` | Day 0 + daily + once-per-pilot checklist |
| `docs/PHASE7_14-PILOT-ISSUE-TEMPLATE.md` | issue record |
| `docs/PHASE7_14-PILOT-DAILY-LOG-TEMPLATE.md` | daily metrics |
| `docs/PHASE7_14-PILOT-EXIT-CRITERIA.md` | 14 exit criteria + hard stops |
| `tests/test_phase7_14_owner_usability_pilot_readiness.py` | the focused suite |
| `tests/phase7_14_console_dom_harness.js` | committed offline DOM render contract |
| `tests/phase7_14_browser_qa.js` | real-browser CDP QA driver (manually invoked) |

## 3. Files modified

| File | Change | Size of change |
|------|--------|----------------|
| `production/phase7_unified_owner_console.py` | import the guidance read model; attach `next_action` to the model; add `/api/v1/next-action`; publish `module_labels`; serve `favicon.svg` and alias `/favicon.ico`; print the next action in the CLI summary | 41 lines |
| `.../static/index.html` | inline icon sprite, grouped nav shell, favicon link, global feedback region, sidebar toggle; **removed the inline `style=` attribute** | rebuilt |
| `.../static/app.js` | owner-facing surface rebuilt; the accepted prepare→confirm→execute machinery preserved verbatim | rebuilt |
| `.../static/styles.css` | calm high-contrast visual system, type scale, disclosure, empty states | rebuilt |
| `.../static/icons.svg` | nav + status icon set | extended |
| `.gitattributes` | LF pins for the new hashed artifacts; **CRLF pins for the three `.bat` files** | +25 lines |

**No accepted Phase 7.3–7.12 authority was modified.** Verified by `git diff` against the baseline
over every 7.3–7.12 production module and all of `core/` — empty.

## 4. Dependencies

**None added.** Python standard library only (`argparse`, `ctypes`, `datetime`, `json`, `os`,
`platform`, `re`, `signal`, `socket`, `subprocess`, `sys`, `time`, `urllib`, `webbrowser`). The
front-end is vanilla JS with no framework, CDN, font, image, analytics, socket, worker or browser
storage. The two Node harnesses use only built-in `fs` / `vm` / `child_process` / `http` and Node's
global `WebSocket`.

---

## 5. Launcher architecture

```
Start-AMZ-Toolkit.bat  ->  Start-AMZ-Toolkit.ps1  ->  python -m production.phase7_owner_launcher start
                                (finds Python 3.9+)          (all safety lives here)
                                                                      |
                                                    preflight -> lock -> health-check -> spawn
                                                                      |
                                    python -m production.phase7_unified_owner_console
                                      --workspace-root "runs/T2/phase7" --host "127.0.0.1"
                                      --port 8780 serve
```

The console command exists in exactly **one** place (`console_command()`), so Start, Stop and Open
can never disagree. The `.ps1` scripts only locate a supported Python and delegate.

Launcher runtime: `runs/T2/phase7/7.14/launcher/` — `console.pid.json`, `launcher.lock`,
`launcher.log` (bounded, rotated), `launcher_status.json`. `runs/` is git-ignored.

### Start behaviour

1. resolve the repository root from the module file (never the current directory);
2. verify the working copy contains the accepted console **and** all five static assets;
3. verify Python ≥ 3.9 and that the required stdlib + console module import;
4. verify the runtime directory is writable;
5. take an **exclusive lock** (`O_CREAT|O_EXCL`), reclaiming a lock whose owner is gone or which is
   older than 180 s;
6. probe port 8780; if occupied, probe `/api/v1/health`:
   * accepted console → `SESSION7_14_LAUNCHER_ALREADY_RUNNING`, **no duplicate is started**;
   * anything else → `SESSION7_14_LAUNCHER_PORT_BLOCKED` + `PORT 8780 IS ALREADY IN USE`. The other
     program is never stopped and the port is never silently changed;
7. clear a stale PID record (process gone, or PID reused);
8. spawn the ONE fixed command (list argv, no shell, own process group, no window);
9. record PID + process-start token + command fingerprint + port;
10. poll `/api/v1/health` under a bounded timeout, watching for early child exit;
11. **only after health reports the accepted console ready**, open the validated loopback URL;
12. write a bounded, secret-free log line and a status document.

Handled: clean start · already running · stale PID · PID belonging to another process · port taken
by another program · health never ready · Python missing · unsupported Python · repository moved ·
paths with spaces · browser failure · repeated double-click · second launcher during startup ·
console crash during startup · stale lock · unwritable runtime directory.

### Stop behaviour

Reads only the launcher-owned PID record. Refuses unless the PID is alive **and** its process-start
token still matches what was recorded (Windows: process creation time via `GetProcessTimes`; POSIX:
`/proc/<pid>/stat` field 22). Command identity is corroborated by the accepted health contract still
answering on the recorded port. Sends a polite signal, waits a short grace window, then terminates
the one verified process directly, all inside a bounded budget. Removes the PID record and reports.

Refusals (nothing is signalled): PID reused · identity unprovable · a healthy console this launcher
did not start. There is **no** process-tree kill utility, no process-name matching, and no
"stop every interpreter" path anywhere in the module or the scripts.

### Open behaviour

Checks health; opens the browser only when the accepted console answers; otherwise prints exactly
what to run. Open never spawns anything (`started_a_server: false` on both paths).

### Launcher safety

Loopback-only bind (validated), strict fixed command, no shell, no arbitrary module, no arbitrary
URL (`is_allowed_url` accepts only the console URL), no service/scheduler/startup registration, and
bounded secret-free logs — a secret-looking key loses its **entire** value and any value containing
a URL scheme is redacted outright.

---

## 6. Next-action guidance

A presentation read model over the model the accepted console already assembled. It reads no file,
opens no socket, has no write path, and never mutates its input. Fourteen rules in a fixed total
order; the first match wins; `generated_at` is excluded from `next_action_identity`.

Published on the accepted overview (`data.next_action`, additive — every existing field is
unchanged) and on a dedicated `/api/v1/next-action` endpoint.

**Destination validation.** Every recommendation resolves to one of exactly five things: an existing
console page, an existing subsection, an allow-listed local command, owner instructions, or an
explicitly unavailable item with a stated reason. Unknown pages and unlisted commands raise. All
four destination kinds are genuinely produced by real rules — there is no fake route and no action
invented to make a recommendation clickable.

**Refused wording.** PPC vocabulary, seller-account vocabulary and causal vocabulary are matched on
word boundaries and refused; the engine raises rather than emit them. The absent-analysis wording is
exactly *"No current report analysis found"*.

---

## 7. Dashboard refinements

Overview hierarchy: heading + overall readiness → **next action** → needs attention → your work →
how current is this → parts of the toolkit → recent activity → technical details (disclosure).

* **Empty states** answer what is missing, whether it is an error, why it matters and the next step.
* **Button contract**: every control declares `data-act`, navigates, copies, or is disabled with a
  visible, `aria-describedby`-linked reason.
* **Navigation**: three labelled groups (OPERATIONS / INTELLIGENCE / SYSTEM) plus Overview, icons +
  labels, collapsible sidebar, single active page, breadcrumbs including the group, hash retained on
  refresh, subsection routes for Manual Actions and Follow-ups.
* **Tables**: bounded search, page size, sort, pagination, sticky headers, total count, reset
  filters, copy ID, and a useful empty state. Record IDs moved to the last column.
* **Feedback**: eight visibly distinct states. A serious failure stays in a persistent panel or the
  modal — never only in a disappearing toast.
* **Status**: word + glyph + shape + colour. An unmapped upstream value reads `UNKNOWN`.
* **Accessibility**: landmarks, skip link, keyboard reachability, visible focus, `aria-live`, modal
  focus trap + focus return, labelled controls, screen-reader labels on icon controls, correct
  heading hierarchy, reduced-motion support, no colour-only meaning.

### Baseline defects this fixes

Recorded in real Edge against the baseline **before** any redesign (44 checks, 31 pass, 13 fail):
no guidance at all; a raw `SESSION7_13_*` token above every owner action; colour-only status; no
progressive disclosure; a flat ungrouped sidebar; no reset-filters; bare `No records to display.`;
**a CSP violation logged on every page load** (inline `style=` on the icon sprite); **a 404 logged on
every page load** (`/favicon.ico`); no launcher; raw module keys as visible labels.

---

## 8. Pilot kit

A 14-day owner pilot under one rule: **FIX DEFECTS ONLY. DO NOT ADD NEW INFRASTRUCTURE.** Day 0
setup, a daily routine targeted at under 15 minutes with **zero PowerShell**, nine once-per-pilot
exercises including an isolated recovery drill and a Seller-Central counter verification, 17 tracked
metrics, and 14 pass/fail exit criteria plus five hard stops. Pilot runtime records live under
`runs/T2/phase7/7.14/pilot/` and are **not committed**.

---

## 9. Verification

Machine-readable evidence: `SESSION7_14-OWNER-USABILITY-PILOT-READINESS-PROOF.json`.

### Tests

| Suite | Result |
|-------|--------|
| Phase 7.14 focused (`tests/test_phase7_14_owner_usability_pilot_readiness.py`) | **418 ran, OK**, 0 failures, 0 errors |
| Phase 7.14 DOM render contract (Node, offline, committed) | **104 checks, 0 failed** |
| Phase 7.13 regression | **269 ran, OK** (includes its 40-check modal harness) |
| Phase 7.12 / 7.11 / 7.10 / prior suites | covered by the full run below, all green |
| **Full in-place suite** | **4583 ran, OK, 4 skipped**, 997 s |

The full in-place skip count is **4**, unchanged from the Phase 7.13 acceptance baseline (4162 ran /
4 skipped). Collection grew by exactly the 418 new tests plus the tests that only collect when
`runs/T2` data is present.

### Fresh-worktree differential

Two detached worktrees, no `runs/` tree in either, same interpreter (3.12.10), same discovery
command, run **concurrently with each other and nothing else**.

| | Baseline `3f758de` | Feature `49c045c` |
|---|---|---|
| Collected | 4163 | **4581** (+418, exactly the new suite) |
| Failures | 1 | 1 |
| Errors | 14 | 14 |
| Skipped | 329 | 329 |
| Seconds | 488.7 | 480.1 |

* new failures: **0**
* lost baseline passes: **0**
* broadened skips: **0**
* failure set: **identical**

**`FRESH_WORKTREE_FULL_SUITE_BASELINE_EQUIVALENT_NONZERO`** — not green, and not claimed to be.
The 15 pre-existing failures/errors are the historical data-dependent ones that need `runs/T2`,
which a fresh worktree does not have. They are identical on both sides.

> One earlier feature-worktree run additionally errored on
> `test_phase7_4_owner_dashboard.HttpSecurity.test_json_content_type_required` — a loopback HTTP
> disconnect inside an accepted Phase 7.4 test this phase does not touch, while a second full suite
> shared the machine. Re-run in isolation it passed 3/3 in the feature tree and 2/2 in the baseline,
> and it did not recur in the authoritative paired run. Recorded as transient, not a regression.

### Browser QA (real browsers, Chrome DevTools Protocol)

| | Microsoft Edge `150.0.4078.99` | Google Chrome `150.0.7871.182` |
|---|---|---|
| Checks | **44 / 44** | **44 / 44** |
| Browser console errors | 0 | 0 |
| Uncaught exceptions | 0 | 0 |
| Off-origin requests | 0 (33 requests, all `http://127.0.0.1:8780`) | 0 (33 requests, same) |

Covered: overview hierarchy, next-action panel above the metrics and answering all five questions,
next-action CTA resolving to a real rendered page, every nav destination rendering and marking
active, breadcrumbs, hash retained across reload, progressive disclosure closed by default, empty
states, table search/filter/reset/sticky headers/total count, keyboard tab order with visible focus,
modal open-not-blank + confirm gated + Escape closes, export download, 1920x1080 and 1366x768 with
no horizontal overflow, status never colour-only, all assets local, no inline script.

A separate probe confirmed all **11** nav icons resolve to the **16**-symbol inline sprite and paint
at 14x14 (browsers do not resolve `<use href>` across documents).

### Launcher QA (real double-click)

| Flow | Result |
|------|--------|
| `Start-AMZ-Toolkit.bat` double-click | `SESSION7_14_LAUNCHER_READY`, health ready in 0.55 s, browser opened **after** health, interim `…_STARTING` state observed |
| `Open-AMZ-Toolkit.bat` (running) | `…_ALREADY_RUNNING`, browser opened, `started_a_server: false` |
| `Open-AMZ-Toolkit.bat` (stopped) | `…_NOT_RUNNING`, no server started, tells the owner to run Start |
| `Stop-AMZ-Toolkit.bat` | `…_STOPPED` in 3.26 s, identity verified, health down afterwards |
| Second start while running | `…_ALREADY_RUNNING`, duplicate refused, nothing spawned |
| Unrelated listener on 8780 | `…_PORT_BLOCKED` + `PORT 8780 IS ALREADY IN USE`, nothing spawned, nothing terminated |
| Stop a console the launcher did not start | `…_STOP_REFUSED` / `NOT_LAUNCHER_OWNED`, nothing signalled |
| `validate-only` | `…_LAUNCHER_READY`, 0 files written, 0 directories created, 0 processes spawned, 0 network requests, 0 browsers opened |
| `validate-only` in a fresh worktree with no `runs/` | `…_LAUNCHER_READY` |

**Two launcher defects were found by this QA and fixed** (both would have broken the owner's very
first double-click):

1. Windows PowerShell 5.1 strips embedded double quotes when building a native command line, which
   corrupted the inline Python version probe — Start, Stop and Open all reported *"A supported Python
   was not found"*. Fixed with a quote-free probe; guarded by `test_042d2`.
2. The `.ps1` files contained a non-ASCII em-dash, and PowerShell 5.1 reads a BOM-less `.ps1` as
   ANSI, so it rendered as mojibake in the owner's window. Fixed by making the scripts ASCII-only;
   guarded by `test_042d1`.

### Other gates

| Gate | Result |
|------|--------|
| `compileall` (production, core, tests) | exit 0 |
| Next-action priority cases | 14 / 14 fire at the expected priority, identity stable across repeats |
| Destination validation | every rule's destination valid; all four destination kinds reachable |
| Source immutability | `git diff` vs baseline over all 7.3–7.12 authorities and `core/` — **empty** |
| `runs/` tracking | git-ignored; `git ls-files runs/` — **empty**; no pilot or launcher runtime committed |
| Prohibited-integration scan | 0 hits across the launcher, guidance, static assets, 6 scripts, 6 docs |
| Seller Central counters | every counter **0** in both the console and the launcher; no code path increments one |
| Acceptance tag | **none** |

## 10. Known limitations

1. **Stop is not a graceful shutdown on Windows.** A console-break signal only reaches a process
   sharing the caller's console, which a detached child never does. Stop therefore sends the polite
   signal (which *is* what stops the console cleanly on POSIX), waits a 3-second grace window, then
   terminates the one identity-verified process directly. This is safe because every accepted
   authority flushes and `fsync`s its write before returning, so no recorded state can be lost — but
   it does mean a Windows stop is a termination, not a graceful drain. Owner-visible cost: ~3.3 s.
2. **Command identity is corroborated, not proved.** Reading another process's full command line on
   Windows needs `NtQueryInformationProcess` or WMI. Instead, identity is established by PID + a
   process-start token (creation time) + the accepted health contract still answering on the recorded
   port. That is strong, but it is not a literal command-line comparison.
3. **The launcher log rotates rather than being cryptographically sealed.** It is operational
   telemetry for the owner, deliberately not part of any integrity chain.
4. **The real-browser QA driver is not part of the committed unittest suite.** It needs a real
   browser binary, so it is invoked manually and its structured result is recorded in the proof gate.
   The committed DOM render harness covers the same contract offline.
5. **The audit chain still does not auto-detect a clean tail truncation.** Inherited from Phase 7.13
   and disclosed there; Phase 7.14 changes nothing about it.
6. **`webbrowser.open` reports "opened" optimistically.** If the OS hands the request to a browser
   that then fails to render, the launcher cannot tell. The health check before it means the console
   itself is known-good.
7. **Icon symbols are duplicated** between `index.html` (for same-document `<use>`, which is the only
   form browsers resolve) and the served `/icons.svg`. A test asserts the two sets never drift.

## 11. Exact owner start command

**Double-click `Start-AMZ-Toolkit.bat`.** That is the whole daily start procedure.

Underneath, the launcher runs exactly one fixed command:

```
python -m production.phase7_unified_owner_console --workspace-root "runs/T2/phase7" --host "127.0.0.1" --port 8780 serve
```

Console address: `http://127.0.0.1:8780`. Stop with `Stop-AMZ-Toolkit.bat`; reopen a browser on an
already-running toolkit with `Open-AMZ-Toolkit.bat`.

## 12. Acceptance status and exact next action

**Not accepted.** This session implemented and self-verified Phase 7.14. No acceptance tag was
created, nothing was merged, and Phase 8 was not started.

**Exact next action:** run an independent acceptance audit of `49c045c` on branch
`phase7-14-owner-usability-pilot-readiness`, covering at minimum: the launcher's process-identity
and refusal paths, the next-action priority determinism and destination validity, the button
contract and empty states in a real browser, the permanent Amazon boundary, and the differential
fresh-worktree result. If the audit accepts, it — not this session — creates the acceptance tag.
