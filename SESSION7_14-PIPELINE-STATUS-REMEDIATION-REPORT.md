# Pipeline Status — remediation of the `d163ff0` audit findings

**Date:** 2026-08-01 · **Branch:** `hotfix-pipeline-status-multi-output-staleness`
**Base:** `main` = `211f2f8` (accepted, tagged `phase7-14-composite-launcher-safety-hotfix-accepted-211f2f8`)
**Merged:** NO · **Acceptance tag:** NONE · **Independent re-audit:** REQUIRED
**Pushed for review:** YES, branch only — authorized by the 2026-08-01T18:33 remediation review

Nothing here is offered as accepted. This is the remediation the 2026-08-01 independent
audit of `d163ff0` required before `core/pipeline_status.py` could be considered for its
own acceptance. Every claim below has a command next to it.

> **Revision 2, 2026-08-01.** Revised after the remediation review
> (`AMZ_FBM_PIPELINE_STATUS_REMEDIATION_REVIEW_AND_FIXED_FEEDBACK_2026-08-01.json`), which
> returned `STRONG_REMEDIATION_CANDIDATE_NOT_READY_FOR_ACCEPTANCE` with corrections C1–C8.
> Revision 1 is preserved in git at `631a491`; nothing was rewritten. **§8 records the
> disposition of every correction, including a real defect the review's C4 caused me to find
> in revision 1's own fix.** Sections 1–7 below are updated to describe the current code —
> leaving revision 1's now-superseded descriptions in place would have made this report false.

---

## 0. Branch shape — why the diff is readable

| Commit | What |
|---|---|
| `211f2f8` | accepted `main`, untouched |
| `518b516` | `d163ff0` cherry-picked verbatim — the audited baseline, replayed on accepted `main` |
| `95f9b67` | **DEFECT A** — blocking |
| `fcd6d31` | **DEFECT B** — low, non-blocking, separable |
| `631a491` | revision 1 of this report + proof — the reviewed candidate |
| `2894269` | **review corrections C1–C5, C7** |

### `518b516` really is byte-identical — proved, not asserted (C6)

Revision 1 inferred byte-identity from a conflict-free cherry-pick. The review correctly
rejected that inference. It happens to be true, and here is the proof rather than the claim:

```
d163ff0 patch-id (stable)   7c7594d2a81a339848fd122a80f4b1449442f2a9
518b516 patch-id (stable)   7c7594d2a81a339848fd122a80f4b1449442f2a9   identical
changed-path sets           identical (diff of both --name-status outputs is empty)
blob core/pipeline_status.py        e2e139acb44351acfb629bba59eddc9007773786  both
blob tests/test_pipeline_status.py  71091d792ca2f6eb8a77685d20d9d553176fd8a2  both
git diff --check 518b516^..631a491  clean
```

No unrelated `feature-pipeline-status` file entered this branch: the cherry-pick touched
exactly the two paths above, and `2f4c929` was excluded.

The re-auditor can diff remediation against baseline directly:

```
git diff 518b516 HEAD -- core/pipeline_status.py
git merge-base --is-ancestor d163ff0 main || echo "baseline correctly still out of main"
```

Defect B is a separate commit on purpose. It can be dropped with `git revert fcd6d31`
without touching the blocking fix.

---

## 1. DEFECT A — `MULTI_OUTPUT_STALENESS_MASKED` (blocking) — CLOSED

### The defect

`evaluate()` took `_newest()` over every file a stage produced and compared that single
maximum against the newest input. For a stage declaring more than one output, the fresher
sibling won the max and hid an artifact that was genuinely older than its own input.

### Measured harm, not asserted harm

Same synthetic workspace, same seed, run through the real CLI on both trees:

```
input   US_AMAZON_cerebro_B0X.xlsx    mtime t+1000
output  MASTER-KEYWORDS-LEAN.json     mtime t+ 500   <- stale in fact
output  CEREBRO-EVIDENCE-MATRIX.json  mtime t+2000   <- fresher sibling
```

| | Stage 5 reported | NEXT action offered to the owner |
|---|---|---|
| `d163ff0` baseline | `ok`, showing `CEREBRO-EVIDENCE-MATRIX.json` | **step 6, keyword intelligence** |
| this branch | `STALE`, naming `MASTER-KEYWORDS-LEAN.json` | **step 5, re-run master keyword builder** |

The baseline did not merely mislabel a row. It walked the owner *past* a Master Keyword
List that had never seen its Cerebro data and told them to build keyword intelligence on
top of it. `STALE` is this module's only derived signal and its whole reason to exist, so a
masked `STALE` is the module failing at the one job it has. Stages 6, 8, 9 and 10 all
consume that list.

Both shipped multi-output stages were affected — **5** (`MASTER-KEYWORDS-LEAN.json` +
`CEREBRO-EVIDENCE-MATRIX.json`) and **11** (`PRODUCT-PAGE.json` +
`BACKEND-SEARCH-TERMS.json`).

### The fix

[`core/pipeline_status.py:149`](core/pipeline_status.py#L149) — `_oldest_current_output()`
takes each declared output **pattern**'s newest match, then the **minimum across patterns**.
A stage is only as current as its least current artifact.

The name is precise as of `2894269` (review C2). It was `_oldest_output`, which describes
something the function never did: it does not return the oldest matching file, it returns
the oldest of the per-pattern *newest* matches. Those are different, and conflating them
breaks the module in opposite directions — see the docstring.

Three behaviours are deliberately preserved and pinned by tests rather than left to trust:

* **One pattern contributes one artifact.** The minimum is taken across patterns, never
  across every file on disk. A superseded Cerebro export the owner never deleted must not
  mark stage 4 stale for ever.
* **`artifact` now names the oldest output** — the file the owner actually has to look at.
  Naming the fresh sibling sent them to a file that was fine.
* **Equal mtimes are `READY`, not `STALE`.** Deliberate, and now pinned by
  `test_equal_mtimes_are_ready_not_stale`. An engine that writes its output inside the same
  clock tick as its input is the normal fast case; calling that `STALE` would be a permanent
  false alarm on coarse-granularity filesystems. The comparison is strict `<`.

The module docstring's staleness rule was rewritten to state the oldest-vs-newest contract;
the baseline text said `output mtime < input mtime`, which is exactly the ambiguity that
allowed the defect.

### Output semantics are now stated, not inferred (C1, C3)

The staleness minimum is only correct if every declared output is a **required current**
artifact. That was true but undocumented and untested — one refactor from being false.
[`Stage`](core/pipeline_status.py#L44) now states it: `produces` is **required-all**;
required-any and optional outputs are unsupported and would produce a false `STALE` if added
to the flat list.

`test_every_declared_output_is_required` proves it behaviourally against the **shipped**
table rather than by comment: for each multi-output stage it materialises every output fresh,
confirms `READY`, then removes each output in turn and requires the stage to stop being
`READY`. If an optional output is ever added to `produces`, that test fails.

A missing required output reports `MISSING` or `BLOCKED` and never reaches a staleness
verdict at all — `evaluate()` resolves `missing_outputs` *before* any timestamp is compared,
so a pattern with no match cannot silently drop out of the minimum.

---

## 2. DEFECT B — owner command rendering (low, non-blocking) — CLOSED

The baseline quoted only on space or tab and never escaped an embedded quote. The audit's
finding — a seed of `nurse"; calc; #` printed as `--seed "nurse"; calc; #"`, which a shell
splits at the second quote — is fixed and pinned.

**A second instance the audit did not name, found while fixing it:** `<seed-keyword>`, the
placeholder printed whenever no `--seed` is supplied, went out with `<` and `>` bare. Those
are cmd.exe redirection operators. Pasted exactly as printed, that line does not run the
command — it truncates a file called `seed-keyword` and reads from it. It was also the one
line in the whole output guaranteed to be pasted by an owner running the tool for the first
time.

### ONE named shell (C4)

The review is right that a generic quote helper cannot be correct for any shell: `"$x"` is a
literal in cmd.exe and an expansion in PowerShell; `'x'` quotes in PowerShell and does
nothing in cmd.exe. Revision 1 claimed paste-safety for *both* and could not have had it.

[`core/pipeline_status.py:231`](core/pipeline_status.py#L231) — `TARGET_SHELL` is
**Windows PowerShell**, the shell `Start-AMZ-Toolkit.ps1` already uses. Every printed command
is labelled `[Windows PowerShell]`.

**Fixing this exposed a real defect in revision 1's own fix.** `_quote` used **double**
quotes, and a PowerShell double-quoted string still expands `$`. A seed of `nurse $5 gift`
would have arrived at the engine as `nurse  gift` — silent value corruption, the exact
failure class the function exists to prevent. Revision 1's tests passed because none of them
contained a `$`. Pinned now by `test_a_dollar_seed_is_not_expanded_away`.

[`core/pipeline_status.py:239`](core/pipeline_status.py#L239) — `_ps_quote` emits a
single-quoted PowerShell **literal**: no expansion, no backtick processing, `''` the only
escape. A leading `-` also forces quoting, or PowerShell reads the value as a parameter name.
`runs/T2` and single-word seeds still print bare. 18 adversarial characters are covered,
`$` and backtick individually.

### No command at all without a real seed (C5)

Quoting the placeholder made it *syntactically* safe and left it *semantically* misleading:
`--seed "<seed-keyword>"` still looks pasteable and would run the engine with the literal
placeholder as the seed. [`_fmt_command`](core/pipeline_status.py#L265) now returns `None`
rather than a placeholder; `render` prints an instruction naming what is missing and the
command that produces the real line; `--json` emits `next_command: null` plus
`next_command_needs_seed: true`, so a machine consumer cannot run a placeholder either.

```
NEXT - step 5, Master keyword list (stale, US_AMAZON_cerebro_B0X.xlsx is newer than ...):
       This step needs your seed keyword, so there is no command to
       paste yet. Re-run with it and the exact line appears here:
         [Windows PowerShell] python -m core.pipeline_status --workspace runs/T2 --seed 'your seed keyword'
```

### Scope claim

The docstring claimed more than the function delivered, and now does not. It states: this
makes a printed command safe to *paste* into **Windows PowerShell**. It is **not** a
shell-injection boundary and does not need to be — the seed is the owner's own text going
into the owner's own shell, and this module executes nothing.

---

## 3. Tests

```
python -m unittest tests.test_pipeline_status
```

| | Baseline `518b516` | Revision 1 `631a491` | Now `2894269` |
|---|---|---|---|
| Tests | 22 | 33 | **49** |
| Result | `Ran 28 — FAILED (failures=4)` * | `Ran 33 — OK` | `Ran 49 — OK` |

\* the 28 is the baseline module run against revision 1's test file: the six new DEFECT A
tests added to the 22 baseline tests. Four fail on the baseline —
`test_a_stale_output_is_not_masked_by_a_fresher_sibling`,
`test_real_stage_5_master_keyword_list_is_not_masked`,
`test_real_stage_11_product_page_is_not_masked` (all `'READY' != 'STALE'`) and
`test_the_stale_output_is_the_one_named_to_the_owner` (`'OTHER.json' != 'OUT.json'`).
**Failing test first**, then the fix.

Two DEFECT A tests — `test_ready_still_requires_every_output_to_beat_the_input` and
`test_a_wildcard_pattern_is_satisfied_by_its_newest_match` — pass on **both** trees by
design. They are scope guards: they fail if the fix over-reaches into wildcard semantics.

**Baseline assertions changed: 3, all in `TestRendering`**, all direct consequences of the
double-to-single quote change (`--seed "x"` → `--seed 'x'`). Revision 1 stated "no accepted
assertion was changed"; that remains true — `d163ff0` is **not accepted**, so these are
unaccepted baseline assertions, not accepted ones. Recorded here explicitly so the
re-auditor does not have to derive it:

```
git diff 518b516 HEAD -- tests/test_pipeline_status.py | grep '^-' | grep self.assert
```

No test was weakened or skipped, and no test is target-only-passing: the 4 DEFECT A tests
that fail on the baseline are exactly the intended regression tests.

---

## 4. Read-only is now proved at runtime, not by a name scan (C7)

The review is right that revision 1's AST scan was weak: bare call names produce false
positives like `str.replace` and can miss a dynamically resolved write. Revision 1 disclosed
its false positive but still presented the scan as if it settled the question. It did not.

Three checks now, in the test suite rather than in a one-off script:

| Check | Test |
|---|---|
| **Runtime filesystem diff** — recursive `(size, mtime_ns)` snapshot either side of a real `subprocess` run of the CLI over a populated workspace | `test_the_cli_touches_no_file_in_the_workspace` |
| **Receiver-qualified AST scan** — classifies `os.replace` and `str.replace` differently instead of flagging both | `test_no_qualified_write_or_exec_call_exists` |
| **`os.*` surface** — usage must stay within `{listdir, path}` | `test_only_read_only_os_functions_are_used` |

The filesystem diff is the load-bearing one: an AST scan can be fooled, a diff around the
actual process cannot. The qualified scan matters because it means the `text.replace` false
positive can no longer be used to wave a real `os.replace` through.

Unchanged and re-verified: imports are `__future__, argparse, json, os, sys, time`; source is
ASCII-only; 12 printed `-m` modules all exist on disk; two files added, zero existing files
modified.

---

## 5. Known limitations — stated, not worked around

1. **No real-artifact run.** `runs/T2` does not exist on this macOS clone; the workspace
   lives on the Windows machine. The before/after in §1 is a synthetic workspace driven
   through the real CLI, which proves the logic and the owner-facing output but **not**
   behaviour against the owner's actual files. Run
   `python -m core.pipeline_status --seed '<seed>'` against the real `runs/T2` on Windows
   before this is accepted. **Blocking on its own.**
2. **The printed commands have not been executed in the shell they name — but the test that
   will do it now exists.** `TestWindowsPowerShellExecution.test_windows_powershell_renderer_preserves_exact_seed`
   writes the production-rendered line to a `.ps1`, runs it through real `powershell.exe`, and
   reads back argv. It **skips on this clone** and must **pass on Windows**. Until it has run
   there, "the seed arrives as one argument with its exact value" is an argument from
   PowerShell's documented literal-string rule, not evidence. **Blocking on its own.**
3. **Pre-existing `ResourceWarning`** in `test_module_makes_no_network_or_amazon_call` —
   fixed in `2894269` while that test was already being edited for C7 (`open(...).read()` →
   context manager). Noted because revision 1 said it was deliberately left alone.
4. **Stage 3's finding stands unchanged.** The audit's separate observation that
   `ASIN-CANDIDATES` can be newer than `ASIN-BATCHES` concerns a single-output stage and is
   not touched by this fix.
5. **Windows mtime granularity was not exercised.** All tests set mtimes explicitly via
   `os.utime`. The equal-mtime boundary is now pinned as `READY`
   (`test_equal_mtimes_are_ready_not_stale`), but this was not measured on NTFS.
6. **Nothing here is accepted.** Not merged, no tag, `main` unchanged at `211f2f8`. The
   branch is pushed for review only.

---

## 6. What this does and does not unlock

This closes the defects the audit and the review named. It does **not** make the module the
Pipeline Observer from the 2026-07-31 review: that is a 10-state model with block reasons,
prerequisites, freshness and source dates, and — as the current handoff correctly argues
against the review's "thin read adapter" framing — it is a derivation layer that does not
exist yet and must be scoped and tested as new logic or it will invent status. This module
has four states and is honest about it.

It also does not change the standing constraint that read-only-first still requires CLI
execution, and so does not yet deliver the no-PowerShell owner experience.

It is **not** ready for acceptance. Two limitations in §5 block it on their own, and both
need Windows.

---

## 7. Verify this report

```
git log --oneline 211f2f8..HEAD
git diff 518b516 HEAD --stat
python -m unittest tests.test_pipeline_status
git merge-base --is-ancestor d163ff0 main || echo "baseline still out of main: correct"
git rev-parse main origin/main                 # both 211f2f8
git show d163ff0 --pretty=format: | git patch-id --stable
git show 518b516 --pretty=format: | git patch-id --stable
```

---

## 8. Disposition of the 2026-08-01 remediation review

Verdict received: `STRONG_REMEDIATION_CANDIDATE_NOT_READY_FOR_ACCEPTANCE`. Branch push
authorized, merge and tag not. All eight corrections are addressed. Where I disagreed I said
so and acted anyway; where the review found something I had wrong, I have said that plainly.

| | Topic | Disposition |
|---|---|---|
| **C1** | Required vs optional output patterns | **ACCEPTED.** Was true in code, undocumented and untested. Now stated on `Stage` and proved behaviourally against the shipped table. §1 |
| **C2** | `_oldest_output` naming | **ACCEPTED.** The name described something the function never did. Renamed `_oldest_current_output`. §1 |
| **C3** | Missing-output semantics | **ACCEPTED.** Already correct via the `missing_outputs` short-circuit, now pinned — plus the equal-mtime boundary, decided deliberately and documented. §1 |
| **C4** | Shell-specific quoting | **ACCEPTED, and it found a defect in my own fix.** See below. §2 |
| **C5** | Placeholder UX | **ACCEPTED.** Quoting made it syntactically safe and left it semantically misleading. No command is printed without a real seed. §2 |
| **C6** | Byte-identical cherry-pick claim | **ACCEPTED.** The claim was true; the inference was not valid. Now proved by patch-id and blob hashes. §0 |
| **C7** | Read-only scan quality | **ACCEPTED.** Runtime filesystem diff added; the AST scan now classifies receivers. §4 |
| **C8** | Update artifacts after Windows evidence | **ACCEPTED as process.** This revision is a follow-up commit; revision 1 is preserved at `631a491`. No history rewritten. |

### C4 found a real defect in revision 1

This is the finding worth reading. Revision 1 replaced the baseline's broken quoting with
**double**-quoted strings and doubled embedded quotes — correct for the Microsoft C runtime
argument parser, and **wrong for PowerShell**, where a double-quoted string still expands
`$`. A seed of `nurse $5 gift` would have reached the engine as `nurse  gift`.

Revision 1's tests passed because not one of them contained a `$`. The review did not name
this defect; insisting the renderer commit to a single named shell is what surfaced it. That
is the argument for C4 in one example: a renderer targeting "PowerShell and cmd.exe" was
targeting neither, and could not be tested against either.

### Where I do not fully agree

The review asks (C4) for the printed command to be *executed* in the claimed shell with
adversarial seeds. That is right, and it is not possible here — `pwsh` is not installed on
this clone. Rather than approximate it, limitation §5.2 records it as unproven and blocking.
The quoting rule is implemented against PowerShell's documented literal-string semantics and
unit-tested; that is weaker than execution and is not described as equivalent.

### Still open, by the review's own gate

* Real Windows `runs/T2` execution evidence — §5.1
* Shell execution evidence for printed commands — §5.2
* Fresh independent re-audit from a new session
* Merge and acceptance tag remain unauthorized

---

## 9. Windows capture — the script, and five defects in the draft

`Capture-PipelineStatusEvidence.ps1` produces both blocking artifacts in one run. It is
derived from the reviewer's `WINDOWS_PIPELINE_STATUS_ACCEPTANCE_CAPTURE.ps1`, which is
careful work — SHA256 tree snapshot, `PYTHONDONTWRITEBYTECODE` so `__pycache__` cannot
pollute the git-status gate, preflight anchored on commits, and a refusal to conclude
anything without a passing PowerShell execution test.

That last point was the real instruction: **the test it demands did not exist.** It does now
(§2, `TestWindowsPowerShellExecution`). Reviewing the draft to write it surfaced five defects.

| | Defect | Why it mattered |
|---|---|---|
| **D1** | `native.exe 2>&1 \| Tee-Object` under `$ErrorActionPreference='Stop'` raises `NativeCommandError` on the first stderr line in Windows PowerShell 5.1 | **Blocking.** Verified that `python -m unittest` writes *all* output to stderr — stdout is empty, even `OK` is on stderr. Both test steps would have thrown before producing evidence. |
| **D2** | The gate looked for the test **name** in `-v` output. A skipped test prints its name too | **Blocking for correctness.** `powershell_execution_test_seen` would report `true` while nothing executed — a false pass on the one gate the script exists to enforce. Reproduced on this clone. |
| **D3** | Only the with-seed rendering was captured | C5 — no command printed without a real seed — had no Windows evidence at all |
| **D4** | No adversarial seed was ever run against the real workspace | C4 had no end-to-end Windows evidence outside the unit test |
| **D5** | `-ExpectedHead` hardcoded to `5104904` | Self-invalidates on the next commit — including the one adding the script. A stale default either blocks a valid run or gets edited out of the way |

D2 is the one worth dwelling on. Had the test been written and the check left alone, a
macOS or CI run would have reported the execution proof as seen while both Windows tests
skipped. The gate would have certified the absence of the thing it was checking for.

Two more, found while rewriting: `Tee-Object` output had to be routed to `Out-Host` or every
captured line becomes part of the function's return value and each exit-code variable an
array; and `Compare-Object` needed guarding for an empty workspace under `StrictMode`.

**What the script cannot do:** it is untested. `pwsh` is not on this clone, so it has been
read and reasoned about, not run. Balanced delimiters and ASCII-only are checked; that is
not the same as executing. Expect to fix something on first run.

```powershell
git checkout hotfix-pipeline-status-multi-output-staleness
.\Capture-PipelineStatusEvidence.ps1                       # or -ExpectedHead <sha> to pin
.\Capture-PipelineStatusEvidence.ps1 -FullSuite -ConnectivityScan   # also closes B5 and B6
```

---

## 10. Bug hunt — eight defects, one of which broke the gate

The 2026-08-01 gate review set `additional_code_change_authorized_before_windows_run: false`.
That is overruled here, deliberately: **B1 would have made the Windows capture throw for the
wrong reason.** There is no value in running a script with a known defect to obtain evidence.

| | Defect | Severity |
|---|---|---|
| **B1** | In PowerShell, a line whose **first token is a quoted string** is a string *expression*, not a command. `_ps_quote(sys.executable)` quotes any interpreter path containing a space — `C:\Program Files\Python311\python.exe` is the common case — so the generated `.ps1` would have **echoed the path, exited 0**, and `json.loads` would then have raised on the echoed text. The gate test would have failed on the environment, not on the renderer. Fixed with the call operator `&`. | **Blocking** |
| **B2** | Windows PowerShell 5.1 reads a **BOM-less `.ps1` as the system ANSI code page**. The Unicode seed this review asked for would have been mojibaked before PowerShell parsed it, and the test would have measured file encoding rather than the renderer. `.ps1` now written `utf-8-sig`. | **Blocking, latent** |
| **B3** | Python's stdout falls back to the ANSI code page when **redirected** rather than attached to a console — exactly what `Tee-Object` does. A non-ASCII seed would have raised `UnicodeEncodeError` and aborted the capture. `PYTHONIOENCODING=utf-8`. | High |
| **B4** | `_resolve` matched on **overlapping head and tail**: `startswith(head) and endswith(tail)` alone accepts `abcd` for `abc*bcd`. No shipped pattern hits it; a future one would, as a phantom artifact making a stage look `READY`. Length guard. | Latent correctness |
| **B5** | No **reject policy for control characters**. A seed pasted from a spreadsheet can carry a newline, which splits the printed command across lines and makes the second look like a separate command. Quoting cannot fix it. `unsupported_characters()` names them; the CLI exits 2. | Medium |
| **B6** | `Tee-Object` output became part of `Invoke-Capture`'s return value, so **every exit-code variable would have been an array** of output lines. | High |
| **B7** | `Compare-Object` refuses an empty collection, so an empty `runs/T2` threw an argument-binding error that reads like a script bug rather than the evidence result. | Medium |
| **B8** | `StrictMode Latest` throws on a missing JSON property; `Get-JsonProperty` reports which one. `origin/<branch>` verified to exist before dereferencing. | Low |

B1 is the one to note. The Windows-only gate test was written last revision and could not run
here, so nothing on this clone would ever have exercised it. It would have failed on the first
Windows machine whose Python lives under `C:\Program Files` — and the failure mode is a
`JSONDecodeError` on an echoed path, which reads like a broken test rather than a shell issue.

### One correction to my own last revision

I first characterised the trailing-backslash hazard as `nurse\`. That is wrong: `\` is
bare-safe, so it renders **unquoted** and there is no quote for the C runtime to mis-parse. The
hazard needs a value that **both** requires quoting **and** ends in a backslash —
`nurse gift\` → `'nurse gift\'` → `"nurse gift\"`. Both cases are now pinned by test.

> **Superseded by §11.** I then argued it should stay *out* of the execution corpus because it
> is a PowerShell limitation below the renderer. The pre-Windows review rejected that, correctly,
> and it is now **refused** at the CLI boundary instead.

### Review items also implemented

Exact-value comparison with no normalising · `stderr` asserted empty · a **whole-directory
snapshot** instead of one sentinel name, so a redirect to *any* filename is caught · exactly one
executable line per `.ps1` · `argv` length asserted · corpus divergence blocked **by AST**
rather than by counting source occurrences, which counted its own assertion · `-FullSuite` and
`-ConnectivityScan` for B5/B6 of the gate review · stages 5 and 11 recorded by name in
`summary.json`.

`Ran 58 tests — OK (skipped=2)`. Module invariants re-verified after the changes: imports
unchanged, `os.*` still `{listdir, path}`, source ASCII-only.

---

## 11. Pre-Windows review — one reversal, two holes in my own fixes

The 2026-08-01 pre-Windows review confirmed the overrule was justified and set a policy worth
keeping: *do not knowingly run a deterministic defective gate; fix a concrete reproducible
defect with a focused test; stop once no known blocker remains.* It also landed one decision
against me and two contract items that found holes in what I had already "fixed".

### The reversal: trailing backslash is now REFUSED

I argued it should be documented as a PowerShell limitation below the renderer and left out of
the corpus. **That was wrong, and the review said so plainly:** calling it a shell limitation
does not remove it from the paste-safety contract. A value that looks supported and arrives at
the engine changed is precisely the failure class this entire line of work exists to close.

Option B, implemented: [`unsupported_value()`](core/pipeline_status.py#L279) refuses a value
that **both** requires quoting **and** ends in a backslash. Exit 2, no command emitted, and in
`--json` a structured `UNSUPPORTED_VALUE` error with `next_command: null`.

The refusal is **exactly as wide as the defect** — a bare `nurse\` needs no quoting, so there is
no quote for the C runtime to mis-parse, and it stays supported:

```
--seed 'nurse\'        exit 0   supported, renders bare
--seed 'nurse gift\'   exit 2   "ends in a backslash and also needs quoting..."
```

The workspace is validated on the same terms. It is substituted into the same printed line and
carries the same hazard.

### B1's contract found a hole in my own B1 fix

The Windows test used `sys.executable` — which **may or may not contain a space on any given
machine**, so the `&` fix could have passed without ever being exercised. That is the same class
of mistake as the D2 skipped-test gate: a check that can silently not check.

`test_a_quoted_executable_needs_the_call_operator_to_run_at_all` now uses a controlled fixture
under `dir with space` and asserts **both directions** — without `&` PowerShell echoes the path
instead of running it; with `&` it runs. A test that only checked the fix would never show the
hazard is real.

### B3's contract found the same bug still live inside the test

I fixed the redirected-stdout encoding trap in the capture script and **left it in the test.**
The argv probe used `print()`, and its stdout there is a pipe, not a console — so on Windows the
`café naïve müg` seed would have raised `UnicodeEncodeError` *inside the probe*, and the failure
would have read as a renderer defect. The probe now encodes UTF-8 bytes itself and no longer
depends on the environment it is being measured in.

### Also implemented

C0 controls **and** DEL enumerated and tested individually, NUL included and caught before any
subprocess is built · refused seed emits no command, structured error in `--json` · `.ps1`
asserted to carry a UTF-8 BOM · **the Windows test fixture's** rendered line asserted to start
with `& ` (see the scope note below) · filesystem
side-effect check made absolute rather than an allow-list · `Invoke-Capture` returns exactly one
`[pscustomobject]` with an integer `ExitCode` rather than relying on position · an **empty
`runs/T2` is refused** instead of satisfying the read-only differential trivially · the
trailing-backslash refusal captured as Windows evidence in its own right.

**`PYTHONIOENCODING` is the single authoritative encoding mechanism**, per the review's caution
against stacking overrides. `PYTHONUTF8=1` and `-X utf8` were rejected deliberately: UTF-8 mode
also changes the *filesystem* encoding, and this tool's entire job is reading filenames —
changing how they decode would alter the measurement.

### Two table invariants replace assumptions with proof

* **No two declared outputs of a stage can be satisfied by one file.** Required-all is defeated
  if a single artifact fills two slots, and the `_resolve` overlap guard does not cover that.
* **Every shipped command starts with a bare literal `python`** — which is *why* no printed
  command needs `&`. The production contract is satisfied structurally rather than by adding
  noise to every printed line, and the test is the tripwire if a template ever leads with a
  substituted value.

> **Scope note — `&` belongs to the test fixture, not to production output.** The rev-5 review
> flagged an ambiguity in the previous wording, correctly. To be exact: **no production
> next-command starts with `&`**, because none starts with a quoted value. The `& ` assertion
> applies only to the controlled Windows fixture, which deliberately invokes a quoted path under
> `dir with space` to prove the hazard is real. If a template ever leads with a substituted
> value, `test_every_command_starts_with_a_bare_literal_token` fails and the renderer must then
> emit `&`.

`Ran 64 tests — OK (skipped=3)`. Module invariants unchanged: imports, `os.*` `{listdir, path}`,
ASCII-only source.

### Unchanged

Acceptance is **HOLD**. The script has still never executed and the three Windows-only tests
have still never run. Nothing here is evidence — it is a better-instrumented candidate.

---

## 12. Audit against the rev-5 review's `required_checks` — six gaps

The rev-5 review returned `stop_coding_now: true` and
`remaining_known_static_defects: NONE_IDENTIFIED_FROM_THE_SUMMARY`. That last phrase is doing
real work: it was identified *from the summary*. Auditing the **source** against each
`required_checks` list — rather than assuming they were met — found six that were not. Every one
maps to an explicit check. None is speculative.

| | Gap | Required by |
|---|---|---|
| **A1** | `needs_quoting()` **re-wrote** `_ps_quote`'s bare-safe condition instead of being the one the renderer calls. Change what renders bare and the refusal silently stops describing the behaviour it exists to describe. | *"the predicate is derived from the actual renderer behavior rather than from a separate approximation"* |
| **A2** | `nurse\` — the value the tool explicitly **claims to support** — was absent from the Windows corpus. A claim of support that is never executed is the same untested assertion this work keeps finding. | *"the accepted bare-safe trailing-backslash value round-trips exactly on Windows"* |
| **A3** | Validation ran **after** `isdir`, so a path with a control character was reported "workspace not found" — true but useless, sending the owner to look for a missing directory instead of at the character they pasted. | *"before command rendering, workspace lookup, and subprocess creation"* |
| **A4** | The side-effect snapshot compared **basenames**. That misses a file MODIFIED rather than created, and collapses same-named files in different directories — so a redirect that **overwrote the probe** read as no side effect at all. | *"canonical paths and hashes, not names alone"* |
| **A5** | Output-slot uniqueness was **case-sensitive**. `PRODUCT-PAGE.json` and `product-page.json` are one file on Windows. | *"Windows case-insensitive path equivalence is accounted for"* |
| **A6** | **Interpreter identity unverified** — the review's `important_unverified_contract`. | see below |

### A6 — bare `python` is a PATH lookup, and that is a contract

Every printed command begins with the bare literal `python`, which is *why* none needs a call
operator. But bare `python` resolves through PATH, and on Windows it can resolve to the
**Microsoft Store alias stub** under `WindowsApps` — which opens the Store rather than running
Python — or to an interpreter without this repository's dependencies.

The printed command is only as good as what that name resolves to, so the resolution is now
evidence rather than an assumption: the capture resolves it, **refuses the Store alias by path**,
proves it can `import core.pipeline_status` from the repo, and records the path and version.

### Also closed

The H1 fixture now asserts its path **actually contains a space**, rather than only that it needs
quoting · `Invoke-Capture` exposes `Output` and `LineCount`, with stdout and stderr merged **on
purpose** because their interleaving is what makes a failure readable · a non-empty but
*irrelevant* `runs/T2` is recorded as **not exercising stages 5 and 11** rather than passing as
evidence for them · `PYTHONIOENCODING`'s process scope is documented — it dies with the script
and cannot leak into a later shell.

### Two of my own tests failed on this change, and both were right to

* The corpus-divergence check collected **every** `for x in name` loop, so the new tree
  snapshot's `for f in files` counted as a rival corpus. It now considers ALL_CAPS module
  constants only.
* The shape test asserted every corpus seed comes back single-quoted — which the deliberately
  **bare** `nurse\` breaks. It now asserts **round-trip** through the renderer's own inverse,
  which is the property that actually matters and does not block adding a legitimately bare case.

A test that has to be relaxed to admit a true case was asserting the wrong thing. Both are worth
recording, because both were guards I wrote in the last two revisions.

`Ran 64 tests — OK (skipped=3)`. Module invariants unchanged: imports, `os.*` `{listdir, path}`,
ASCII-only.

**Acceptance is still HOLD.** Nothing here is evidence. The script has still never executed, and
the three Windows-only tests have still never run.

---

## 13. The three Windows-only tests ran — two defects, one of them real

`Ran 66 tests — OK (skipped=0)`, Windows 11, `python` = `.venv\Scripts\python.exe` (3.12.10),
repo root, `PYTHONDONTWRITEBYTECODE=1`. Every previous count in this document was
`64 — OK (skipped=3)` measured where the Windows tests **cannot run**, so it is not comparable
with this one and does not become wrong: it describes a different set of executed tests.

The prediction in §9 was that the capture script would need a first-run fix. What it actually
needed was two, and the second one was hiding behind the first.

### DEFECT 1 — the execution proof was unsatisfiable by construction

`test_windows_powershell_renderer_preserves_exact_seed` asserted, in the same loop body:

```
assertEqual(argv, ["--seed", seed])     # the seed arrives VERBATIM
assertNotIn("PWNED", run.stdout)        # the marker does not appear
```

Four corpus seeds contain `PWNED` literally. A correct renderer is exactly what makes the marker
appear in the probe's argv echo, so the two assertions could never both hold. The test could not
pass on any machine — and nothing said so for as long as it was skipped, which was everywhere,
because it had never run on the one platform it targets.

Fixed by asserting the property that was actually meant: the probe prints exactly ONE line, so a
second line is output PowerShell produced on its own. That is strictly stronger than the marker
check — it catches an injected command that prints anything at all, and assertion 1 reads only
the LAST line, so extra output above it would previously have hidden.

The regression guard is `test_the_execution_proof_cannot_assert_the_marker_is_absent_from_stdout`,
and it runs on **every** platform on purpose: a guard that only runs on Windows would have the
same blind spot as the defect it guards. Applied by AST to the pre-fix file it fires on both
sites, lines 524 and 545; on the fixed file it is silent.

### DEFECT 2 — a real one, masked by the first

With the loop able to proceed past the third seed, it reached `nurse"quote"` and found the
renderer's contract genuinely broken:

```
rendered  'nurse"quote"'        -> child receives  nursequote
```

The owner would have searched a keyword they never typed, with nothing on screen to show it. That
is worse than an error, and it is the same failure class as DEFECT A: a wrong value presented as
a right one.

Measured through real `powershell.exe`, all three renderings:

| Rendering | Child receives |
|---|---|
| `'nurse"quote"'` — what `_ps_quote` emits | `nursequote` — both quotes dropped |
| `"nurse` `` ` ``\`"quote…"` — double-quoted | three arguments |
| `'nurse\"quote\"'` — backslash-escaped in the literal | `nurse"quote"` — byte-exact |

**Refused, and the reason is a choice rather than an impossibility.** The third form works, so
unlike the trailing backslash this value *is* renderable. Emitting it means carrying the C
runtime's backslash-doubling rules inside a renderer whose job is a two-word keyword, and each of
those rules would need its own Windows proof. A double quote is not part of any real Amazon search
term. Owner decision, taken explicitly rather than assumed, recorded here so it can be reversed
the same way: refuse.

`unsupported_value()` now has **three** refusals. The docstrings state that this one is
renderable-in-principle — a refusal that claims impossibility it has not got would be the same
overstatement this document has corrected twice already.

`DOUBLE_QUOTE_SEED` moves out of the execution corpus and is pinned out of it by name, next to
`TRAILING_BACKSLASH_SEED`: a refused value inside the gate corpus fails the gate for a limitation
the renderer is not allowed to fix.

### What this changes about the five blockers

Blocker 2 is closed — the three Windows-only tests ran, and pass. Blocker 4 is closed **for this
shell**: bare `python` resolves to `.venv\Scripts\python.exe`, not the Microsoft Store alias, and
it resolves identically under `powershell.exe -NoProfile`. Blockers 1, 3 and 5 stand: the capture
script itself has still never completed, there is still no real `runs/T2` execution evidence, and
this commit has had no independent re-audit.

**Acceptance remains HOLD.** A test that has been made to pass is not a gate that has run.
