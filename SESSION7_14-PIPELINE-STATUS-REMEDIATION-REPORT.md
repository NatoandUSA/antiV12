# Pipeline Status — remediation of the `d163ff0` audit findings

**Date:** 2026-08-01 · **Branch:** `hotfix-pipeline-status-multi-output-staleness`
**Base:** `main` = `211f2f8` (accepted, tagged `phase7-14-composite-launcher-safety-hotfix-accepted-211f2f8`)
**Merged:** NO · **Acceptance tag:** NONE · **Independent re-audit:** REQUIRED

Nothing here is offered as accepted. This is the remediation the 2026-08-01 independent
audit of `d163ff0` required before `core/pipeline_status.py` could be considered for its
own acceptance. Every claim below has a command next to it.

---

## 0. Branch shape — why the diff is readable

| Commit | What |
|---|---|
| `211f2f8` | accepted `main`, untouched |
| `518b516` | `d163ff0` cherry-picked verbatim — the audited baseline, replayed on accepted `main` |
| `95f9b67` | **DEFECT A** — blocking |
| `fcd6d31` | **DEFECT B** — low, non-blocking, separable |

`d163ff0`'s parent was `56f4339`, already an ancestor of `main`, so the cherry-pick applied
with no conflict and `518b516` is byte-identical to the audited code. The re-auditor can
therefore diff remediation against baseline directly:

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

[`core/pipeline_status.py:131`](core/pipeline_status.py#L131) — `_oldest_output()` takes each
declared output **pattern**'s newest match, then the **minimum across patterns**. A stage is
only as current as its least current artifact.

Two behaviours are deliberately preserved and now pinned by tests rather than left to trust:

* **One pattern contributes one artifact.** The minimum is taken across patterns, never
  across every file on disk. A superseded Cerebro export the owner never deleted must not
  mark stage 4 stale for ever.
* **`artifact` now names the oldest output** — the file the owner actually has to look at.
  Naming the fresh sibling sent them to a file that was fine.

The module docstring's staleness rule was rewritten to state the oldest-vs-newest contract;
the baseline text said `output mtime < input mtime`, which is exactly the ambiguity that
allowed the defect.

---

## 2. DEFECT B — `_quote` (low, non-blocking) — CLOSED

The baseline quoted only on space or tab and never escaped an embedded quote.

The audit's finding — a seed of `nurse"; calc; #` printed as `--seed "nurse"; calc; #"`,
which a shell splits at the second quote — is fixed and pinned.

**A second instance the audit did not name, found while fixing it:** `<seed-keyword>`, the
placeholder printed whenever no `--seed` is supplied, went out with `<` and `>` bare. Those
are cmd.exe redirection operators. Pasted exactly as printed, that line does not run the
command — it truncates a file called `seed-keyword` and reads from it. It was also the one
line in the whole output guaranteed to be pasted by an owner running the tool for the first
time.

[`core/pipeline_status.py:207`](core/pipeline_status.py#L207) — anything outside
`[A-Za-z0-9._+=/:\-\\]` now forces quoting, and an embedded quote is doubled, the form both
PowerShell and the Microsoft C runtime argument parser accept. `runs/T2` and single-word
seeds still print bare.

**The docstring claimed more than the function delivered, and now does not.** It states the
scope plainly: this makes a printed command safe to *paste* into the PowerShell and cmd.exe
consoles this tool targets. It is **not** a shell-injection boundary and does not need to
be — the seed is the owner's own text going into the owner's own shell, and this module
executes nothing. The audit reached the same conclusion; the code no longer implies
otherwise.

---

## 3. Tests

```
python -m unittest tests.test_pipeline_status
```

| | Baseline `518b516` | This branch |
|---|---|---|
| Tests | 22 | **33** |
| Result | `Ran 28 tests — FAILED (failures=4)` * | `Ran 33 tests — OK` |

\* the 28 is the baseline module run against **this branch's** test file: the six new
DEFECT A tests added to the 22 baseline tests. Four fail on the baseline —
`test_a_stale_output_is_not_masked_by_a_fresher_sibling`,
`test_real_stage_5_master_keyword_list_is_not_masked`,
`test_real_stage_11_product_page_is_not_masked` (all `'READY' != 'STALE'`) and
`test_the_stale_output_is_the_one_named_to_the_owner` (`'OTHER.json' != 'OUT.json'`).
**Failing test first**, then the fix.

The other two new DEFECT A tests — `test_ready_still_requires_every_output_to_beat_the_input`
and `test_a_wildcard_pattern_is_satisfied_by_its_newest_match` — pass on **both** trees by
design. They are scope guards: they fail if the fix over-reaches into wildcard semantics.

No accepted assertion was changed, weakened or skipped. All 22 baseline tests still pass
unmodified.

---

## 4. The audit's soundness findings, re-verified after the change

The audit accepted `d163ff0` as read-only by AST rather than by docstring. Re-run against
the remediated file:

```
imports                     __future__, argparse, json, os, sys, time    (unchanged)
os.* attributes used        listdir, path                                (unchanged)
write/exec-capable calls    1 apparent  -- see below
ascii-only source           True
printed -m modules          12 referenced, 0 missing on disk
two files added             yes; zero existing files modified
```

**One disclosure the re-auditor needs.** A naive AST scan for write/exec-capable call names
now reports one hit: `text.replace('"', '""')` at
[`core/pipeline_status.py:222`](core/pipeline_status.py#L222). That is `str.replace` on a
local string, not `os.replace`. It is disambiguated by the line above it: the only `os.*`
attributes in the file are `listdir` and `path`, so no `os.replace` call exists. The audit's
original scan returned zero hits, so this would otherwise read as a regression in
auditability. It is not a filesystem call.

---

## 5. Known limitations — stated, not worked around

1. **No real-artifact run.** `runs/T2` does not exist on this macOS clone; the workspace
   lives on the Windows machine. The before/after in §1 is a synthetic workspace driven
   through the real CLI, which proves the logic and the owner-facing output but **not**
   behaviour against the owner's actual files. Someone should run
   `python -m core.pipeline_status --seed "<seed>"` against the real `runs/T2` on Windows
   before this is accepted.
2. **Pre-existing `ResourceWarning`** in the baseline's
   `test_module_makes_no_network_or_amazon_call` — it calls `open(...).read()` without
   closing. One line to fix, deliberately left alone to keep the remediation diff inside
   the audited scope. Worth cleaning up in a separate commit.
3. **Stage 3's finding stands unchanged.** The audit's separate observation that
   `ASIN-CANDIDATES` can be newer than `ASIN-BATCHES` concerns a single-output stage and is
   not touched by this fix.
4. **Windows mtime granularity was not exercised.** All tests set mtimes explicitly via
   `os.utime`. Sub-second-resolution filesystems are not a factor at the day-scale gaps this
   module compares, but this was not measured on NTFS.
5. **Nothing here is accepted.** Not merged, no tag, `main` unchanged at `211f2f8`.

---

## 6. What this does and does not unlock

This closes the only thing the audit named as standing between
`core/pipeline_status.py` and an acceptance tag of its own. It does **not** make the module
the Pipeline Observer from the 2026-07-31 review: that is a 10-state model with block
reasons, prerequisites, freshness and source dates, and — as the current handoff correctly
argues against the review's "thin read adapter" framing — it is a derivation layer that does
not exist yet and must be scoped and tested as new logic or it will invent status. This
module has four states and is honest about it.

It also does not change the standing constraint that read-only-first still requires CLI
execution, and so does not yet deliver the no-PowerShell owner experience.

---

## 7. Verify this report

```
git log --oneline 211f2f8..HEAD
git diff 518b516 HEAD --stat
python -m unittest tests.test_pipeline_status
git merge-base --is-ancestor d163ff0 main || echo "baseline still out of main: correct"
git rev-parse main origin/main                 # both 211f2f8
```
