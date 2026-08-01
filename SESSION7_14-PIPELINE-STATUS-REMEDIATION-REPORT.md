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
2. **The printed commands have not been executed in the shell they name.** `pwsh` is not
   available on this clone. `_ps_quote` implements PowerShell's documented single-quoted
   literal rule and is unit-tested against it, but "the seed arrives as one argument with its
   exact value" has **not** been demonstrated by running the line. Windows-side evidence,
   same bucket as limitation 1. **Blocking on its own.**
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
