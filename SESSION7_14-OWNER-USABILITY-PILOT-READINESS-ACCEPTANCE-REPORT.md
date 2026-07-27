# Session 7.14 — Owner Usability & Pilot Readiness — Independent Acceptance Audit

| Field | Value |
|-------|-------|
| Branch | `phase7-14-owner-usability-pilot-readiness` |
| Baseline / main / origin-main | `3f758debc31bcf0b4e50d9693798e99910c64110` |
| Checkpoint tag | `phase7-14-owner-usability-pilot-readiness-checkpoint-3f758de` |
| Implementation commit | `49c045c3e80ecd204fac13eaadda46f1b28d12a2` |
| Proof commit / feature HEAD | `74f442e9ff21dbb3f374044ecbaf8491eef31d48` |
| Audit posture | independent; no implementation claim, proof value or test total trusted without reproduction |
| Production code modified by this audit | **none** |
| Merged | **no** |
| Phase 8 | **not started** |

**DECISION: `PHASE7_14_OWNER_USABILITY_PILOT_READINESS_ACCEPTED_WITH_DOCUMENTATION_FIX`**

Every blocking gate passes. The launcher, the guidance read model, the console surface, the pilot kit
and the permanent Amazon boundary were reproduced from repository bytes and from auditor-written
fixtures and harnesses. Four inaccuracies were found in the implementation report and proof gate —
all descriptive, none of them a production defect, all corrected in this commit.

---

## 1. Git provenance

Reproduced from the repository, not from the report.

| Check | Result |
|-------|--------|
| Branch | `phase7-14-owner-usability-pilot-readiness` ✔ |
| Working tree before audit | clean (`git status --porcelain` empty) ✔ |
| Local HEAD | `74f442e9ff21dbb3f374044ecbaf8491eef31d48` ✔ |
| `origin/phase7-14-owner-usability-pilot-readiness` | `74f442e…` — matches local ✔ |
| `main` | `3f758debc31bcf0b4e50d9693798e99910c64110` ✔ |
| `origin/main` | `3f758debc31bcf0b4e50d9693798e99910c64110` ✔ |
| Checkpoint tag target | `git rev-parse …checkpoint-3f758de^{}` → `3f758de…` exactly ✔ |
| Phase 7.14 acceptance tag before this audit | none (`git tag -l "*7-14*"` → checkpoint only) ✔ |
| Accepted 7.13 tags intact | `phase7-13-unified-owner-console-accepted-6114533`, `phase7-13-action-modal-hotfix-accepted-3f758de` ✔ |
| Tag count | 50, all prior accepted tags present ✔ |
| `runs/` ignored | `.gitignore:5:runs/`; `git ls-files runs/` → 0 files ✔ |
| Phase 8 work | none — no `phase8` path, tag, branch or commit anywhere ✔ |

## 2. Baseline

`3f758de` is simultaneously `main`, `origin/main`, the checkpoint target and the accepted Phase 7.13
action-modal-hotfix tag target. The audit's fresh baseline worktree was cut from this commit.

## 3. Implementation commit

`49c045c` — 24 files, +7850/−386. Contents verified file by file; nothing outside Phase 7.14 scope.

## 4. Proof commit

`74f442e` — exactly two files added (the implementation report and the proof gate), no code.

## 5. Diff scope

```
A  production/phase7_owner_launcher.py            (1234)
A  production/phase7_owner_next_action.py          (718)
M  production/phase7_unified_owner_console.py      (+40 / −5)
M  .../static/{index.html, app.js, styles.css, icons.svg}
A  .../static/favicon.svg
A  Start/Stop/Open-AMZ-Toolkit.{bat,ps1}           (6 scripts)
A  docs/PHASE7_14-*.md                             (6 pilot documents)
A  tests/test_phase7_14_owner_usability_pilot_readiness.py
A  tests/phase7_14_console_dom_harness.js, tests/phase7_14_browser_qa.js
M  .gitattributes                                  (+25)
```

No accepted Phase 7.3–7.12 module, no `core/` file and no prior test file appears in the diff.

## 6. Dependencies

**Zero added.** `phase7_owner_launcher.py` imports only `argparse, datetime, json, os, platform, re,
signal, socket, subprocess, sys, time, urllib, webbrowser` (+ `ctypes` locally on Windows).
`phase7_owner_next_action.py` imports only `os, re, sys` plus the accepted `product_workspace`
canonical-JSON helpers. The front end is vanilla JS: no framework, CDN, font, image, analytics,
socket, worker or browser storage. `requirements.txt` unchanged.

## 7. Launcher command fixedness

`console_command()` is the single definition site (`CONSOLE_MODULE` appears once as a constant).
Auditor harness produced exactly:

```
[python, -m, production.phase7_unified_owner_console,
 --workspace-root, runs/T2/phase7, --host, 127.0.0.1, --port, 8780, serve]
```

The six wrapper scripts pass **no** user input: the `.bat` files do not forward `%*`, the `.ps1`
files declare no `param()` block and hard-code `--host 127.0.0.1 --port 8780 --workspace-root
runs/T2/phase7`. The module CLI exposes no `--url`, `--command`, `--module`, `--exec`, `--pid` or
`--token` flag, and its positional command is a fixed 5-value choice list. Source scan: no
`shell=True`, no `os.system`, no `taskkill`/`pkill`/`killall`, no `psutil`/`tasklist`/`process_iter`,
no scheduler/service/startup registration, no random-port selection.

Workspace-root escape refused for `/etc`, `C:\Windows`, `../..`, `runs/../../etc`, `runs/T2/..`.
Non-loopback bind refused for `0.0.0.0`, `192.168.1.5`, `10.0.0.1`, `example.com`, `""` and an
injection attempt. Browser URL allowlist accepts only `http://127.0.0.1:8780` and that plus `/`;
it rejected `http://evil.com`, `…:8780/x`, `…:8781`, `file:///c:/`, `javascript:alert(1)`,
`…:8780/@evil.com` and `…:8780#@evil`.

## 8. Repository detection

`repo_root()` resolves from the module file, never the process CWD — verified by `chdir`-ing to a
temporary directory and confirming the resolved root is unchanged. A directory without the console
yields `SESSION7_14_LAUNCHER_MODULE_REQUIRED` with the owner sentence "This folder does not contain
the toolkit console…", and **no process is spawned**.

## 9. Python detection

`check_python` rejects 3.8, accepts 3.9, and flags versions beyond the tested ceiling — exercised
directly. In the live PowerShell 5.1 run the `.ps1` probe (which tries `py -3`, then `python`, then
five well-known install paths) selected **Python 3.14.6** and the console started and served under
it, so the probe is confirmed working against a real second interpreter.

The *missing-Python* and *console-import-failure* branches were verified by inspection plus their
module-level checks rather than by uninstalling Python or corrupting the console module: a null probe
result prints "A supported Python was not found… install Python 3.9 or newer" and exits 1, and
`check_imports()` reports `console_module_importable: false` which `_preflight_failure` maps to
`REQUIRED_IMPORT_MISSING`. The audit confirmed the surrounding guarantee empirically — **any**
preflight failure returns before the spawn step, with zero processes started (§8).

## 10. Paths with spaces

The feature tree was copied to
`…\A Folder With Spaces\AMZ FBM Toolkit\` and every flow was driven there under Windows PowerShell
5.1. Start, Open and Stop all resolved `$PSScriptRoot`, `cd /d "%~dp0"` and the Python path correctly.
No quoting corruption.

## 11. Start lock

`O_CREAT|O_EXCL`. Second acquisition refused. With the lock held and no healthy console, Start
returns `SESSION7_14_LAUNCHER_LOCKED` ("The toolkit is already starting. Wait a few seconds…") and
spawns nothing. With the lock held **and** a healthy console, Start returns `…_ALREADY_RUNNING`
instead of an error the owner cannot act on — still spawning nothing. A lock whose owner PID is dead,
one older than 180 s, and a corrupt lock file are each reclaimed exactly once.

## 12. Port handling

Port is fixed (`AUTOMATIC_PORT_SELECTION is False`); no random-port code path exists. Port free →
normal start. Port occupied → health probe decides, and the port is never silently changed.

## 13. Healthy existing console

Live and simulated: `SESSION7_14_LAUNCHER_ALREADY_RUNNING`, `duplicate_start_refused: true`, zero
spawns. Confirmed twice against a real running console (in-process and via double-click).

## 14. Unrelated port owner

Live test: an unrelated `python -m http.server` was bound to 8780, then `Start-AMZ-Toolkit.bat` was
double-clicked.

```
readiness=SESSION7_14_LAUNCHER_PORT_BLOCKED
error_code=PORT_IN_USE_BY_ANOTHER_PROCESS
PORT 8780 IS ALREADY IN USE
```

`python.exe` process count before **and** after: 3. The decoy was still alive afterwards, and still
alive after a subsequent `Stop-AMZ-Toolkit.bat`. Nothing was spawned and nothing was terminated.
A non-HTTP occupant (no HTTP status at all) is treated the same way.

## 15. Health polling

`_await_health` polls `/api/v1/health` on a bounded budget, checking `proc.poll()` first so an early
child exit is detected rather than waited out. Health is only accepted when the body reports
`stage_id == "7.13"` **and** `api_schema == "phase7-13-console-api-v1"`; the probe follows no
redirect, reads a bounded body, and refuses any non-loopback URL.

## 16. Browser health order

Proven by call ordering, not by reading the code: an instrumented run recorded the interleaving of
health probes and browser opens. The browser was opened **once**, and the event immediately before it
was a health probe that returned ready. On the timeout path and the crash path
`browser_attempted` is `false` — the browser is never opened. A deliberate assertion-throwing browser
stub was never invoked on either failure path.

## 17. Startup timeout

Bounded. `SESSION7_14_LAUNCHER_TIMEOUT` / `HEALTH_NOT_READY_IN_TIME`, no browser, and the owner
message names the recovery: "Run Stop-AMZ-Toolkit, then try Start-AMZ-Toolkit once more."

## 18. Startup crash

Child exiting before health → `SESSION7_14_LAUNCHER_FAILED` / `CONSOLE_EXITED_DURING_STARTUP`, the
PID record is cleared, and no browser opens.

## 19. PID record

`runs/T2/phase7/7.14/launcher/console.pid.json` holds pid, `process_start_token`,
`command_fingerprint`, console module, host, port, launcher pid, Python version and
`repository_root_relative: "."`. Live record inspected: no absolute path anywhere in it.

## 20. Process-start token

Real processes, not fixtures. On Windows the token is the `GetProcessTimes` creation time via stdlib
`ctypes` (`win-create-<100ns>`); on POSIX it is field 22 of `/proc/<pid>/stat`. Verified: stable
across repeated reads for one process, different for two processes started back to back, never
containing the PID, and `None` for pid 0, −1 and a non-numeric pid. It is written only into the
launcher's own runtime file and there is **no CLI flag by which a user could supply one**.

## 21. PID reuse

A recorded PID that is alive but whose token no longer matches is refused:
`SESSION7_14_LAUNCHER_STOP_REFUSED` / `PID_REUSED_BY_ANOTHER_PROCESS`, `signalled: false`,
`identity_verified: false`. On the Start path the same condition merely clears the stale record —
it never signals. Confirmed with an injected terminate hook that recorded every call: empty.

## 22. Stop identity

PID alone is insufficient by construction. Stop refuses unless the PID is alive **and** the recorded
process-start token still matches. "Cannot prove it" is checked *before* "does not match", so an
unreadable identity reports `PROCESS_IDENTITY_UNPROVEN` rather than being misreported as reuse — and
neither path signals anything.

On the report's wording — *"command identity is corroborated, not a literal command-line compare"* —
this is conservative and safe: `command_identity_verified` is a **reported field only**. The decision
to signal is gated exclusively on PID + start-token, which together already prove the process is the
one this launcher spawned. Note that the field reads `true` when the recorded port answers nothing at
all (no HTTP status); since it gates nothing, this is cosmetic.

## 23. Stop unrelated-process refusal

* Healthy console the launcher did not start → `NOT_LAUNCHER_OWNED`, nothing signalled.
* Recorded PID dead → `ALREADY_STOPPED`, record removed, nothing signalled.
* Unrelated decoy holding the port → untouched (verified alive afterwards).

There is no process-name matching, no process-tree kill and no "stop every interpreter" path in the
module or in any of the six scripts.

## 24. Stop bounded behaviour

Live double-click Stop: `SESSION7_14_LAUNCHER_STOPPED` in **3.26 s**, identity verified, PID record
removed, port free, and `python.exe` count went **3 → 2** — exactly one process, the console, was
stopped. Escalation is bounded and stays on the same PID (`[(pid, False), (pid, True)]`).

The documented Windows limitation is **accurate**: a console-break cannot reach a detached child, so
Stop is a bounded terminate after a 3 s grace window, not a graceful drain. The report states this
plainly and does not claim graceful shutdown. Observed cost matches the documented ~3.3 s.

## 25. Open behaviour

Healthy → opens exactly `http://127.0.0.1:8780`, `started_a_server: false`, zero spawns.
Not healthy → `SESSION7_14_LAUNCHER_NOT_RUNNING`, `started_a_server: false`, and the owner is told
"Run Start-AMZ-Toolkit first, then use Open-AMZ-Toolkit." A browser that fails to open is not fatal:
`…_BROWSER_UNAVAILABLE` with exit code 0 and the address printed for manual use.

## 26. Open arbitrary-URL refusal

`open_browser` raises `URL_NOT_ALLOWED` for a foreign URL. There is no CLI or wrapper surface that
accepts a URL at all, so an externally supplied URL cannot reach the launcher.

## 27. PowerShell 5.1

Executed on **Windows PowerShell 5.1.26100.8875 (Desktop)** — not PowerShell 7 — from a path with
spaces, using the CRLF `.bat` files exactly as a fresh checkout delivers them.

| Double-click | Result |
|---|---|
| `Start-AMZ-Toolkit.bat` | `…_READY`, 0.52 s, browser opened after health, exit 0 |
| `Start-AMZ-Toolkit.bat` again | `…_ALREADY_RUNNING`, duplicate refused, exit 0 |
| `Open-AMZ-Toolkit.bat` (running) | `…_ALREADY_RUNNING`, browser opened, exit 0 |
| `Stop-AMZ-Toolkit.bat` | `…_STOPPED` 3.26 s, one process stopped, exit 0 |
| `Open-AMZ-Toolkit.bat` (stopped) | `…_NOT_RUNNING`, clear instruction, exit 1 (window pauses) |
| `Stop-AMZ-Toolkit.bat` again | `…_ALREADY_STOPPED`, exit 0 |
| Unrelated listener on 8780 | `…_PORT_BLOCKED`, nothing spawned or killed, exit 1 |

Both claimed PS 5.1 defects are verified fixed: the version probe contains **no** double-quote
character, and all six scripts are **pure ASCII with no BOM** (byte-level check: 0 bytes > 0x7F in
every file), so no mojibake is possible. Output rendered correctly throughout.

## 28. Secret-free logs

The auditor logged deliberately hostile fields — `csrf_token`, `authorization=Bearer …`,
`cookie=sid=…`, `action_token`, `password`, `session`, `api_key`, an absolute path and a URL carrying
credentials. None of the nine secret values appeared in the log. Every line is bounded to 400
characters, and rotation to a single `.1` file at 256 KiB works. The live launcher log after a real
start/stop round-trip contained no `Bearer`, no CSRF value and no absolute repository path.

## 29. Launcher runtime tracking

Runtime state lives under `runs/T2/phase7/7.14/launcher/` — git-ignored. `git ls-files runs/` is
empty; no launcher runtime and no pilot record is committed.

## 30. Next-action authority

`phase7_owner_next_action` is a presentation read model. Source scan confirms it contains **no**
`open(`, `os.makedirs`, `shutil`, `subprocess`, `socket`, `urllib`, `requests`, `http.client`,
`os.remove`, `os.rename`, `json.dump(` or `write(` — no write path, no file read, no network. It
never mutates its input (byte-compared before/after). Every fact is copied verbatim from the model
the accepted Phase 7.13 console already assembled; every recommendation records
`source_authorities`, and every named authority resolves to a real accepted module file.
`amazon_action_implied` is a constant `false`.

## 31. Priority order

All **14** rules were exercised individually from auditor-built models and each fired at exactly its
documented priority with its documented `rule_id`. The published id and priority are taken from the
tuple **position**, not from whatever a rule body writes, so a rule cannot publish an id the policy
table does not list. `RULES` and `RULE_IDS` are tuples of equal length (asserted at import).

## 32. Deterministic identity

`generated_at` is excluded: the same model with timestamps 2020 and 2099 produced identical
`next_action_identity` and identical documents apart from that one field. Reversing the insertion
order of `overview`, `sections` and `system` produced an identical identity — the outcome cannot vary
by dictionary order. A changed state produced a different identity.

## 33. Conflicting conditions

With **every** condition true simultaneously the result is priority 1. Removing integrity yields 2;
removing security yields the data rules; and stepping the owner-task conditions down one at a time
walks the sequence 6 → 7 → 8 → 9 → 10 → 11 → 12 → 13 → 14 exactly. First match wins, always.

Honest handling verified: an `INFO` alert and an `ACKNOWLEDGED` critical alert do **not** escalate; a
stale *derived* phase (7.5) does not trigger the critical-staleness rule, only 7.3 does; and eight
malformed/absent models (including `{}` and `None`) each produced a valid bounded recommendation
rather than an exception. "No urgent action" is `SESSION7_14_NEXT_ACTION_NONE` — never an error.

## 34. Real-T2 recommendation

Reproduced from current accepted state without trusting the report:

```
priority            4 of 14
rule_id             required-data-missing
owner_title         "No current report analysis found"
destination         existing_console_page → analysis  ("Analysis & Decisions")
canonical_status    SESSION7_14_USABILITY_REQUIRED  →  overview label "NEEDS SETUP"
source_authorities  production.phase7_owner_operations_dashboard (Phase 7.3-7.8)
```

**Why 4, from lineage:** audit chain `ok: true` and no blocked module → rule 1 skipped; the boundary
reports `seller_central_blocked / seller_api_blocked / advertising_api_blocked` all true with every
seller counter 0 → rule 2 skipped; the analysis module is `READY_EMPTY`, not `MODULE_UNAVAILABLE` →
rule 3 skipped; rule 4 matches on `READY_EMPTY`. Note that 7.3 freshness *is* stale
(`latest 2026-07-21`), so rule 5 would also have matched — rule 4 correctly wins on priority. This
case also exercises the `READY_EMPTY`-with-`analyzed_rows: 114` branch.

The guidance identity is derived from the guidance content only; untracked T2 state is not part of
the module's source identity (`schema_hash()` covers schema, rule order and destination vocabulary).

## 35. Destination validation

Every emitted destination validated. Invalid shapes are refused: a fabricated `import` or
`advertising` page, an unknown analysis section, `rm -rf /`, an `os.system` one-liner, an empty
instruction list, `curl http://evil`, an empty unavailable-reason, an unknown page on an unavailable
destination, an invented `open_url` type, a bare string and `None` — all rejected.
`_destination_page("import")` and `_destination_command("python -m evil")` both raise.
The two allow-listed commands are local `python -m production.…` invocations with no shell
metacharacter.

**Finding (documentation).** Only **three** of the four declared destination kinds are produced by a
rule: `existing_console_page`, `instructions` and `unavailable`. `_destination_command()` — the
`copy_command` kind — is never called by any rule, and the corresponding branch in `app.js` is
unreachable from server guidance. The owner does still get a copyable command, via rule 3's
`instructions` destination carrying a `command` field, so the *capability* is delivered. The
implementation's own test is honest about this in a code comment; the report prose ("all four
destination kinds are genuinely produced by real rules"), its verification table ("all four
destination kinds reachable") and the test's name overstate it. Corrected in this commit.

## 36. No fake Import page

`CONSOLE_PAGES` contains no `import` entry and no page containing the substring. Rule 3 — the only
rule about a missing analysis workspace — is honestly **instruction-only**, precisely because no
console page can create that workspace. That is the correct answer, not a link to nowhere.

## 37. No PPC language

The refusal guard matched every one of 17 probes on word boundaries — `ppc`, `advertising`,
`campaign`, `bid`, `budget`, `sponsored products`, `ad group`, `seller central`, `sp-api`, `ads api`,
`bulk upload`, `caused`, `resulted in`, `roi`, `guaranteed`, `will increase`, `uplift`, `proves` —
while innocent substrings (`forbidden`, `enabled to`, `abidance`, `roid`, `adding`, `readiness`,
`candidate`) did not trip it. Every one of the 22 real rule outputs is clean. The engine raises
rather than emitting refused wording. The absent-analysis sentence is exactly
**"No current report analysis found"**.

## 38. Overview hierarchy

Verified in real Edge and Chrome. `#view-root` section order is exactly:

```
heading → next-action → attention → counts → freshness → modules → activity → technical
```

The next-action panel is within the first viewport at 1366×768 and answers all five questions
(what / why / what to do / where / what to expect) with a real CTA resolving to `#analysis`.
Everything above the technical block was scraped as text and contains **no** `SESSION7_1x_` token,
**no** 32+ character hash and **no** schema name.

## 39. Progressive disclosure

Every `details.tech` element is closed on load and opens with real content. Canonical readiness,
next-action rule id, guidance identity, read-model state hash and source ids all live inside it.

## 40. Empty states

All **10** reachable empty states were enumerated across the 11 pages in a real browser. Every one
answers what is missing, whether it is an error, why it matters and the next step; not one is a bare
"No data." The Backup page correctly flags `isError: true` ("This is a problem that needs your
attention") while the other nine correctly say "This is not an error." Three carry a real CTA
(`#analysis`, `#manual-actions`). Filtered-to-empty tables get their own distinct empty state that
names the row count being hidden and how to clear it.

## 41. Button inventory

Full inventory across all 11 pages. Every clickable control declares `data-act`, and every value
falls into exactly one supported class: `nav:` · `action:` · `copy:id` / `copy:command` ·
`download:` · `disabled` · `disclose` · `table:*` · `modal:*` · `toggle:sidebar`.

## 42. Dead-button scan

**Zero** controls without `data-act` across all 11 pages. Every `nav:` href resolves to one of the 11
real routes. Disabled controls are the pagination boundaries (which carry a `title` reason) and
`disabledBtn` controls (which carry a visible `aria-describedby` reason). No dead button, no fake
route, no silent failure, no infinite spinner, no false success.

## 43. Navigation

Overview plus three labelled groups — OPERATIONS (Analysis & Decisions, Manual Actions, Follow-ups),
INTELLIGENCE (Research, Watchlists, Alerts), SYSTEM (Notifications, Backup & Recovery, System Health,
Activity). Icons resolve from the 16-symbol inline sprite; `/icons.svg` serves the same set —
both files were parsed and compared, giving an identical 16-id set and identical symbol bodies
(after whitespace normalisation), so the two cannot silently drift. Active page is marked with
`aria-current="page"`,
breadcrumbs include the group, and the sidebar is collapsible with a labelled toggle. All 11 routes
render without error. No route introduces a second business authority — every page reads an accepted
7.3–7.12 endpoint through the accepted 7.13 API.

## 44. Hash retention

Every route retains its hash. After a **full page reload** on `#alerts` the page re-rendered Alerts
with `aria-current` still on the Alerts link. The accepted legacy route `#alerts-view` resolves to
the real Alerts page.

## 45. Tables

Bounded search (clamped to 200 chars, server-side filter), page-size select (25/50/100/200), sortable
headers with direction indicator, prev/next pagination disabled at the boundaries, sticky headers
(`position: sticky`), a `record(s)` caption total, reset-filters, copy-ID, and a useful empty row.
Rendering is bounded server-side: `_page()` clamps `page_size` to `MAX_PAGE_SIZE`, so an oversized
request cannot force an unbounded render. Record IDs are in the last column, not the first.

## 46. Feedback states

Eight states are defined and the renderer produces a visibly distinct, `data-state`-tagged panel for
each; the committed DOM harness exercises all eight directly. In the shipped front-end, six are
reached by a code path (`no-change`, `stale`, `completed`, `failed`, `session-expired`, plus
`loading` via `loadingBlock`); `ready` and `blocked` are defined but never passed to `setFeedback`.
Non-blocking — no owner-visible failure results, and the policy statement is a vocabulary
statement — but it is recorded here as an observation.

A serious failure never disappears into a toast: request rejections render a persistent
`role="alert"` panel, and an execution failure stays in the modal until the owner closes it.

## 47. Status communication

Every status tag carries a **word**, a **glyph** (shape) and a colour class plus a `data-state`
attribute — verified on live rendered pages, not just in source. Colour is never the only carrier.
An unmapped upstream value resolves to `UNKNOWN`, an honest answer rather than a guess.

## 48. Accessibility

Verified in-browser: `role="banner"`, `<main role="main">`, ≥2 `<nav>` landmarks, a skip link,
≥3 `aria-live` regions, **no heading-level jumps** on any page, zero unlabelled icon buttons, zero
unlabelled inputs/selects, a 3px `:focus-visible` outline, `prefers-reduced-motion` support present in
the stylesheet, and `#modal[role=dialog][aria-modal=true]`. Modal focus trap, phrase gating, Escape
close and focus return all verified live (§51–52).

Two minor observations, non-blocking: pagination boundary buttons state their reason via `title`
rather than the visible `aria-describedby` used elsewhere; and `.btn.copy` has a 1.4 rem (~22 px)
minimum height, just under the 24 px WCAG 2.2 target-size minimum.

## 49. 1366×768

No horizontal page overflow on any of the 11 routes. Wide tables scroll inside their own
`overflow-x: auto` container.

## 50. 1920×1080

No horizontal page overflow on Overview, Analysis, Activity or System Health.

## 51. Modal regression (accepted Phase 7.13 hotfix re-run)

The accepted 7.13 modal DOM harness (40 checks) was re-run inside the 7.13 suite — **269 tests, OK**.
Live in Edge and Chrome, a real action prepare-and-open produced a modal that is **not blank**: it
carries the canonical action, a readiness tag, a populated details list, and focus lands inside the
dialog. Escape closes it and focus returns to the triggering control.

## 52. Phrase gating

Where the accepted server requires confirmation, `Confirm & run` starts disabled, stays disabled for
a wrong phrase, and enables only on an exact match (no trim, no case-fold) — never looser than the
server check. Backdrop clicks are deliberately inert so an accidental click cannot discard a
single-use token. Execution is single-shot: the token is cleared on submit and the Confirm button is
hidden afterwards, so double submission is impossible.

## 53. Failure modal

A simulated failing prepare (HTTP 409) still opened a modal — the exact Phase 7.13 hotfix
requirement. It was not blank, it showed the readiness and a readable reason, the Confirm button was
**hidden** (no ungated confirm on a blocked prepare), the dismiss button read "Close", and focus was
placed on it.

## 54. Export result

Export links are same-origin `/api/v1/exports/overview?format=…` anchors with `download` attributes;
an execute result that returns a file list renders it and offers the same three local downloads. All
browser responses observed were HTTP 200.

## 55. CSP baseline defect

Reproduced against a real baseline console (`3f758de` served from the baseline worktree) in real Edge:

```
INLINE_STYLE_ATTRS = ["svg style=\"position:absolute\""]
CSP_VIOLATIONS     = ["Applying inline style violates the following Content Security Policy
                      directive 'style-src 'self''…"]
```

A genuine, per-page-load CSP violation. The claim is accurate.

## 56. CSP feature result

Feature console, same probe: `INLINE_STYLE_ATTRS = []`, `CSP_VIOLATIONS = []`, `ERROR_LOGS = []`.

The CSP header is **byte-identical** to baseline:

```
default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self';
font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'
```

No `unsafe-inline`, no `unsafe-eval`, no wildcard. The defect was fixed by removing the inline style
attribute, **not** by relaxing the policy.

## 57. Favicon baseline defect

Baseline: `GET /favicon.ico` → **404**, `GET /favicon.svg` → **404**, and the browser logged
"Failed to load resource: … 404" on every page load. Accurate.

## 58. Favicon feature result

Feature: `GET /favicon.ico` → **200 `image/svg+xml; charset=utf-8`** via a fixed one-entry alias
(`FAVICON_ALIASES = {"favicon.ico": "favicon.svg"}`) into the fixed five-entry `STATIC_FILES`
allowlist. No arbitrary path is servable. `favicon.svg` is self-contained — no raster, no external
reference. Across a full browser session: **zero** 4xx/5xx responses.

## 59. Edge QA

Real Microsoft Edge over CDP, auditor-written harness: **130 / 132 checks pass**. The two
non-passes are defects in the auditor's own harness, resolved independently: a malformed CSP regex
(`\\*` matches the empty string — the header itself was printed and inspected, §56), and an
empty-state probe pointed at a page that legitimately had rows (empty states fully verified in §40).
Zero browser console errors, zero uncaught exceptions.

## 60. Chrome QA

Real Google Chrome, same harness, same result: **130 / 132**, same two harness artefacts, zero
console errors, zero uncaught exceptions. Behaviour identical to Edge across all checks.

## 61. Browser network boundaries

Across a full session in each browser: **47 requests, 100 % to `http://127.0.0.1:<port>`, zero
off-origin**, zero WebSocket. `localStorage` and `sessionStorage` both empty. `app.js` contains no
`innerHTML`, no `eval(`, no `new Function`, and no external URL. Both `fetch` call sites are
relative, `same-origin`, `no-store`. The CSRF token is held only in a closure. No CDN, font,
analytics, service worker or cookie manipulation.

## 62. Pilot guide

Real 14-day structure. Day 0 (~30 min, 7 numbered steps including launcher, both browsers, stop
verification, a `runs/` backup and confirming the owner can obtain their own report). A 9-step daily
routine targeted under 15 minutes with an explicit rule that **any** PowerShell use in the daily
routine is a defect. Nine once-per-pilot exercises including the isolated recovery drill (performed
on a copy, never live data) and the Seller Central zero-counter verification. A troubleshooting table
that ends every row with "nothing on your Amazon account has been touched."

## 63. Pilot checklist

Day 0, a 14×-repeated daily block, the once-per-pilot exercises, a per-day PowerShell counter, an
explicit quality-gate block (no dead button, no blank dialog, no dead link, no spinner that never
finishes, no failure that vanished, every empty screen explains itself) and an end-of-pilot block.

## 64. Pilot issue template

Sequential IDs, four severities, 13 categories that map directly onto the exit criteria (dead button,
blank dialog, unexplained empty screen, unreadable failure, PowerShell needed, accessibility, …),
expected/actual/repro, and an explicit *Deferred (post-pilot)* section enforcing the no-new-
infrastructure rule.

## 65. Pilot daily log

Seventeen tracked metrics across launcher, understanding, friction, ratings and completed work —
including startup success and time, time to identify the next action, PowerShell uses (target 0),
dead ends, and confidence/effort ratings 1–5.

## 66. Pilot exit criteria

Fourteen pass/fail criteria covering every item required by this audit: launcher reliability
(13/14 days), zero daily PowerShell, no dead buttons, no unusable modal, no fake route, accurate next
action (12/14 days), one complete owner workflow, ≥1 decision, ≥1 manual Amazon-side action recorded
manually, ≥1 observational follow-up, backup + verify + isolated recovery drill, zero Seller Central
counters on all 14 days, no unresolved critical defect, and confidence ≥ 4.0/5 with no day below 3.
Nine supporting targets and a structured sign-off block.

## 67. Pilot metrics

Metrics describe the owner's experience of the toolkit only. Both the guide and the daily log state
explicitly that they "never describe your business results, and nothing here establishes business
causation." The follow-up wording throughout is observational. **No business causation is claimed.**

## 68. Pilot hard stops

Five, each an immediate failure regardless of other results: any Seller Central connection or
sign-in prompt; any non-zero boundary counter on any day; any change inside the Amazon account;
recorded history failing to verify with the cause unidentified; business data lost and unrecoverable.

**The pilot has not been run or marked complete by this audit.**

## 69. Source immutability

`git diff 3f758de 74f442e` restricted to `core/` → **empty**. Restricted to each accepted authority
(`phase7_ads_analysis`, `phase7_owner_dashboard`, `phase7_owner_decision_package`,
`phase7_manual_action_tracker`, `phase7_offline_outcome_followup`,
`phase7_owner_operations_dashboard`, `phase7_connected_backup_recovery`,
`phase7_connected_public_research`, `phase7_connected_research_watchlists`,
`phase7_owner_notification_delivery`, `phase7_report_ingestion`, `product_workspace`) → **empty**.
No prior test file changed. The only production changes are the two new 7.14 modules, the 7.13
console integration, and the 7.13 static assets.

## 70. Phase 7.13 additive integration

Measured: **+40 / −5** lines in `phase7_unified_owner_console.py` (the report's "41 lines" is a close
approximation; corrected in this commit). Reviewed line by line — the change adds one import, a
`DATA_STALE` owner label, an owner-label map, `favicon.svg` in `STATIC_FILES`, a one-entry
`favicon.ico` alias, `module_labels` and `next_action` on the model, a `/api/v1/next-action` read
endpoint, one `SOURCE_AUTHORITIES` entry, two `validate_only` checks and three CLI summary lines.

Nothing weakened. Verified unchanged: session handling, CSRF, the **15**-action allowlist,
confirmation tokens, the audit chain, loopback bind, Host validation, the CSP string and the Seller
Central boundary. `STAGE_ID` is still `7.13` and `API_SCHEMA` still `phase7-13-console-api-v1`. The
`overview` payload is a strict superset of the accepted contract — the only added key is
`module_labels` (plus the sibling `next_action`), with every pre-existing field unchanged. No
authority is duplicated: the guidance reads the assembled model and decides nothing.

## 71. Phase 7.12 double gate

Untouched. `phase7_owner_notification_delivery.py` is byte-identical to baseline, the console's
notification action paths are outside the diff, and the Phase 7.12 suite passes **234 tests, OK**.
The live-send double gate is preserved.

## 72. Seller Central boundary

No route around the permanent restrictions was introduced. Scans over the launcher, the guidance, all
static assets, the six scripts and the six documents found **zero** active hits for Seller Central,
seller authentication/OAuth, SP-API, Amazon Ads API, seller mutations, buyer messaging, review
requests, Selenium, Playwright, webdriver, browser automation, `shell=True`, `os.system`, `eval`,
`exec`, arbitrary command/URL, remote binding, service installation, scheduler registration or
taskkill-all-Python. Every Seller/Amazon occurrence is a denial string, a zero-valued counter, a
refusal-vocabulary fragment or owner-facing reassurance text — never an active path.

All 10 seller counters are constant `0` in both the console and the launcher, no code path increments
one, and the live console reports `seller_central_action_performed: false`. The guidance's
`boundary_intact()` requires every seller class refused **and** every counter zero, and rule 2 fires
at priority 2 — above every ordinary task — if it is not.

## 73. Focused tests

`python -m unittest tests.test_phase7_14_owner_usability_pilot_readiness`
→ **`Ran 418 tests`, `OK`, exit 0.** Claim reproduced exactly.

## 74. DOM contract

The committed harness was driven with **auditor-written fixtures**, not the implementation's:
→ **`TOTAL 104 FAILED 0`**, returncode 0, 104 PASS lines counted. Claim reproduced exactly.

## 75. Phase 7.13 tests

`python -m unittest tests.test_phase7_13_unified_owner_console`
→ **`Ran 269 tests`, `OK`, exit 0.** Claim reproduced exactly. Includes the accepted 40-check modal
DOM harness (40 `assert(` sites confirmed in `tests/phase7_13_modal_dom_harness.js`).

## 76. Prior suites

Phase 7.12: **`Ran 234 tests`, `OK`.** All other prior suites are covered by the full in-place run
below and by the fresh-worktree differential.

**Connectivity and network-policy scanners**, run explicitly:

| Suite | Result |
|---|---|
| `tests.test_amazon_boundary` | `Ran 26 tests`, **OK** |
| `tests.test_connectivity_policy` | `Ran 16 tests`, **OK** |
| `tests.test_connectivity_surface` | `Ran 19 tests`, **OK** |
| `tests.test_network_policy` | `Ran 5 tests`, **OK** |
| `tests.test_connected_services` | `Ran 18 tests`, **OK** |

`python -m scripts.connectivity_scan` was also run over the live tree *including* the two new 7.14
modules: 96 files, 83 findings, **`active_amazon_account_paths: 0`**,
`no_active_amazon_account_path: true`. `phase7_owner_next_action.py` produced **zero** findings of any
class. `phase7_owner_launcher.py` produced five `REVIEW_REQUIRED` and one `DOCUMENTATION_ONLY` — every
one of them the launcher's own health probe (`socket.create_connection` and `urllib.request`), which
is guarded: `port_in_use()` normalizes to `127.0.0.1`, and `_open_loopback()` raises
`NON_LOOPBACK_PROBE_REFUSED` for anything that is not `http://127.0.0.1:`, `http://localhost:` or
`http://[::1]:`. No new approved-client path, no legacy external path, no prohibited Amazon path.

This scanner rewrites `CONNECTED-RESEARCH-NETWORK-SCAN.json` as a side effect. The audit restored
that tracked artifact to its committed bytes rather than commit a regenerated one; the committed copy
predates several phases (73 files vs 96 today), which is pre-existing and outside Phase 7.14's scope.

## 77. Full in-place suite

`python -m unittest discover -s tests -p "test_*.py"`
→ **`Ran 4583 tests`, `OK (skipped=4)`, exit 0.**

Collection and skip count both match the claim exactly. The skip count is unchanged from the Phase
7.13 acceptance baseline (4162 ran / 4 skipped); collection grew by the 418 new tests plus the
data-dependent tests that only collect when `runs/T2` is present. **No regression.**

## 78. Compileall

`python -m compileall -q production core tests` → **exit 0**, in the primary tree and in both fresh
worktrees.

## 79. Fresh baseline

Detached worktree at `3f758de`, confirmed clean and confirmed to contain **no** `runs/` tree, same
interpreter (3.12.10), same discovery command:

```
compileall            exit 0
Ran 4163 tests in 447.042s
FAILED (failures=1, errors=14, skipped=329)     exit 1
```

15 failing nodes, all historical and data-dependent (`test_backend_semantic_quality`,
`test_backend_phrase_integrity`, `test_session5d_certification` — they need the `runs/T2` Helium 10
exports a fresh worktree does not have).

## 80. Fresh feature

Detached worktree at `49c045c`, identical conditions:

```
compileall            exit 0
Ran 4581 tests in 460.570s
FAILED (failures=1, errors=14, skipped=329)     exit 1
```

An earlier feature run recorded one additional error —
`test_phase7_4_owner_dashboard.HttpSecurity.test_json_content_type_required` — which the audit
investigated rather than accepted:

* the exception is `ConnectionAbortedError: [WinError 10053]`, a transient loopback disconnect;
* `production/phase7_owner_dashboard.py` **and** `tests/test_phase7_4_owner_dashboard.py` are
  **byte-identical** between `3f758de` and `49c045c` (SHA-256 compared), so a regression is not
  structurally possible;
* isolated repeats: **0 flakes in 40 baseline runs**, 1 in 11 feature runs, all under load;
* a quiet, unloaded re-run of the full feature suite reproduced **14 errors** and a failure set
  **identical to baseline**.

The implementation disclosed this same node, with the same diagnosis, in both the report and the
proof gate. Independently corroborated: **transient, not a regression.**

## 81. Differential result

| | Baseline `3f758de` | Feature `49c045c` |
|---|---|---|
| Collected / ran | 4163 | **4581** |
| Failures | 1 | 1 |
| Errors | 14 | 14 |
| Skipped | 329 | 329 |
| Exit code | 1 | 1 |

* collection delta: **+418**, exactly the new suite;
* new failures: **0**;
* lost baseline passes: **0**;
* broadened skips: **0**;
* narrowed skips: **0**;
* shared-node verdicts: **identical** (node-id sets diffed — no difference);
* Phase 7.14 failures: **0**;
* the launcher does **not** require `runs/T2`: `validate-only` in the fresh feature worktree with no
  `runs/` returned `SESSION7_14_LAUNCHER_READY` and `SESSION7_14_PILOT_READY`, all 8 checks OK, with
  0 files written, 0 directories created, 0 processes spawned, 0 network requests, 0 ports probed.

**`FRESH_WORKTREE_FULL_SUITE_BASELINE_EQUIVALENT_NONZERO`**

This is **not green** and is not claimed to be. Both sides fail identically for pre-existing,
data-dependent reasons.

Both worktrees were removed after the audit. `git clean` was never run in the primary workspace.

## 82. Prohibited integration

Scanned the two new modules, all five static assets, the six launcher scripts and the six pilot
documents for: Seller Central, seller authentication, seller OAuth, SP-API, Amazon Ads API, Amazon
mutations, customer/buyer messaging, review requests, Selenium, Playwright, webdriver, arbitrary
browser automation, arbitrary subprocess, `shell=True`, `os.system`, `eval`, `exec`, arbitrary
command, arbitrary URL, remote binding, service installation, scheduler registration and
taskkill-all-Python.

**Zero active hits.** Denial strings, zero-valued counters, refusal-vocabulary fragments and test
fixtures were distinguished from active paths by inspecting every occurrence in context. The one
`subprocess` use in the launcher spawns the single fixed console command with a list argv and no
shell keyword at all. The guidance module contains no `subprocess`, `socket` or `urllib` reference.

## 83. Documentation accuracy

Commits, files, dependency claim (none added), launcher behaviour, process identity, the Windows stop
limitation, the PowerShell 5.1 fixes, the 14 next-action rules, the real-T2 result, browser QA, the
baseline defects found, test totals, the fresh-worktree nonzero result, source immutability and
prohibited integrations were each reproduced and are accurate.

Four descriptive inaccuracies were found. **None is a production defect** — behaviour is correct and
safe in every case; only the prose or the recorded value is wrong. All four are corrected in this
commit.

1. **Destination kinds.** "All four destination kinds are genuinely produced by real rules" and the
   verification row "all four destination kinds reachable" are false: three are produced, and
   `copy_command` is not emitted by any rule (§35).
2. **`.bat` proof hashes.** The three `.bat` SHA-256 values in the proof gate were taken from
   LF-normalized bytes, but `.gitattributes` pins those files to `eol=crlf`, so **every fresh
   checkout produces different bytes and a different hash**. Verified: 21 of the 24 recorded hashes
   reproduce exactly in a fresh checkout; the three `.bat` entries do not. This contradicts the
   `.gitattributes` rationale ("reproduce identically in every checkout"). The delivered `.bat` files
   are correct — CRLF is what `cmd.exe` requires, and the CRLF form is exactly what was
   double-click-tested in §27 — so only the recorded values are wrong.
3. **"All five static assets."** The launcher's repository check covers **four**
   (`CONSOLE_STATIC_FILES = index.html, app.js, styles.css, icons.svg`); `favicon.svg` is not among
   them. The console's `STATIC_FILES` serving allowlist is the one with five entries.
4. **"41 lines."** The console change measures **+40 / −5**.

## 84. Known limitations

The report's seven declared limitations are accurate, and the two most load-bearing were verified
directly: the Windows stop really is a bounded terminate rather than a graceful drain (§24), and
command identity really is corroborated rather than compared (§22). Three further observations from
this audit, all non-blocking:

1. **Stop-failure wording.** `SESSION7_14_LAUNCHER_FAILED` maps to one owner message — "The toolkit
   could not be started…" — which is also emitted when a *Stop* fails with `CONSOLE_DID_NOT_STOP`.
   The error code is correct; the sentence names the wrong verb on a rare path.
2. **`process_alive()` is handle-sensitive on Windows.** A terminated process still reports alive
   while any handle to it remains open in the *calling* process. This does not affect the shipped
   launcher, which runs one command per process (verified: a real separate-process Start→Stop
   round-trip stops cleanly in 3.26 s and the PID is released). It does mean an in-process
   Start→Stop sequence reports `CONSOLE_DID_NOT_STOP`; the auditor reproduced this.
3. **Two unreachable feedback states** (`ready`, `blocked`) and one **unreachable `copy_command`
   branch** in `app.js` — dead vocabulary, no owner-visible effect (§46, §35).

Additionally, the next-action destination for rule 6 declares `section: "decisions"`, but the
front-end `SECTION_ROUTE` maps it to the `#analysis` route whose sub-view defaults to "Analysis", so
the declared subsection is not applied. The owner lands on the correct, real page with a "View"
selector one interaction away, and rules 7 and 8 (`manual-actions`, `outcomes`) do land on their
dedicated pages precisely. Cosmetic, non-blocking.

## 85. Final decision

**`PHASE7_14_OWNER_USABILITY_PILOT_READINESS_ACCEPTED_WITH_DOCUMENTATION_FIX`**

No rejection condition is met:

| Rejection condition | Finding |
|---|---|
| Launcher runs arbitrary commands or URLs | **No** — one fixed argv; no URL/command surface anywhere |
| Launcher binds beyond loopback | **No** — refused for every non-loopback host tested |
| Browser opens before health | **No** — ordering instrumented; never opened on timeout or crash |
| Unrelated process can be terminated | **No** — decoy survived Start and Stop; count 3→3 |
| PID reuse can cause termination | **No** — refused, nothing signalled |
| Stop relies only on PID | **No** — PID **and** process-start token both required |
| PowerShell 5.1 double-click broken | **No** — all six flows verified on PS 5.1 from a spaced path |
| Next-action guidance invents facts | **No** — verbatim restatement; no read, no write, no network |
| Destination is fake or dead | **No** — every emitted destination exists and renders |
| PPC or advertising analysis implied | **No** — refused on word boundaries; engine raises |
| Normal navigation produces a dead button | **No** — 0 unclassified controls across 11 pages |
| Blank or ungated modal regresses | **No** — populated on success *and* on blocked prepare |
| CSP weakened | **No** — header byte-identical to baseline |
| Browser contacts external services | **No** — 47/47 requests same-origin, both browsers |
| Seller Central boundary weakens | **No** — zero active paths; all counters 0 |
| Accepted source changes unexpectedly | **No** — 7.3–7.12 and `core/` diffs empty |
| Phase 7.14 tests fail | **No** — 418 OK, 104 DOM checks OK |
| Full in-place suite regresses | **No** — 4583 OK, 4 skipped, unchanged |
| Fresh feature worse than baseline | **No** — identical failure set, +418 collection |
| Blocking owner-usability defect remains | **No** |

## 86. Exact next action

Phase 7.14 is accepted and tagged. It is **not merged**, and Phase 8 is **not started**.

The exact next action is the one the phase was built for: **run the 14-day owner pilot** from
`docs/PHASE7_14-OWNER-PILOT-GUIDE.md`, starting with Day 0 — double-click `Start-AMZ-Toolkit.bat`,
confirm the browser opens by itself on `http://127.0.0.1:8780`, copy `runs/` somewhere safe, and set
up the issue and daily-log records outside the repository.

Throughout the pilot: **FIX DEFECTS ONLY. DO NOT ADD NEW INFRASTRUCTURE.** At the end, work through
`docs/PHASE7_14-PILOT-EXIT-CRITERIA.md`. Merging this branch to `main`, and any decision about Phase
8, should follow the pilot result — not precede it.

---

### Audit method

Every material claim was reproduced from repository bytes or from auditor-written fixtures and
harnesses. The auditor wrote and ran four independent harnesses that do not reuse the
implementation's tests:

| Harness | Coverage | Result |
|---|---|---|
| `audit_launcher.py` | 149 checks: fixed command, host/port/URL/workspace refusals, lock, stale lock, stale PID, PID reuse, identity refusal, stop escalation, open safety, preflight, secret-free logging, real process identity, real end-to-end start/stop | 144 pass; 5 non-passes traced to the Windows handle-lifetime artefact of §84.2, not to product behaviour |
| `audit_next_action.py` | 264 checks: all 14 rules, conflict resolution, determinism, dictionary-order independence, hostile input, destination validity, refused wording, read-only enforcement, additive console integration | 262 pass; 2 non-passes were auditor expectations (one surfaced finding §35) |
| `audit_browser.js` | 132 checks per browser in real Edge and Chrome: network boundary, CSP, favicon, hierarchy, disclosure, button inventory, navigation, hash retention, tables, accessibility, both viewports, modal, blocked-prepare modal, storage boundary | 130/132 in each; 2 auditor-harness artefacts |
| `audit_empty.js` / `audit_dom.py` / `audit_baseline_defects.js` | empty-state enumeration, DOM contract with auditor fixtures, baseline-defect reproduction | all clean |

Reproduction commands, exact exit codes and node-id sets are recorded in §73–§81.

**No production code was modified by this audit.** The only changes in the acceptance commit are this
report and the four documentation corrections listed in §83.
