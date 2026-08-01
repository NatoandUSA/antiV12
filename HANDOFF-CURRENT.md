# HANDOFF — CURRENT

**Updated:** 2026-08-01, after the first Windows execution of the pipeline-status gate.
Supersedes the version at `e99de03` (branch `docs-handoff-post-acceptance-2026-08-01`), which is
**superseded, not disposable** — see §13 for the diff that establishes it and for the three things
carried forward late. Do not delete that branch; closing it is a separate owner-approved step.

| | |
|---|---|
| Composite 7.14 launcher hotfix `56f4339` | **ACCEPTED** — independent audit, 0 code defects |
| `main` | `211f2f8`, **pushed**; `origin/main` matches |
| Acceptance tag | `phase7-14-composite-launcher-safety-hotfix-accepted-211f2f8`, **pushed** |
| `d163ff0` (pipeline-status) | audited → REMEDIATION_REQUIRED → **remediated on a branch**, still OUT of `main` (§11, §12) |
| Active branch | `hotfix-pipeline-status-multi-output-staleness` — pushed, **not merged, not tagged**. No hash pinned here on purpose: it would self-invalidate on the next commit, including this one. Verify local == origin instead |
| Acceptance of that branch | **HOLD** — was five blockers, now **three** (§12, §13) |

Verify:
```
git rev-parse --short main origin/main                       # both 211f2f8
git tag --points-at main
git merge-base --is-ancestor d163ff0 main || echo d163ff0-correctly-out
git rev-parse --short hotfix-pipeline-status-multi-output-staleness \
               origin/hotfix-pipeline-status-multi-output-staleness   # two lines, must be EQUAL
git tag --points-at hotfix-pipeline-status-multi-output-staleness     # empty
```

That last pair prints two hashes and says nothing about what they should be, on purpose. An
earlier version of this block ended `# both c6cbdb9` two lines under a header promising no pinned
hash, and it was already wrong when it was committed — the commit that wrote it moved the branch.
**A handoff cannot name its own HEAD.** Check equality, never a value.

This is the LIVING handoff. Each superseded version is frozen as `HANDOFF-<date>-<commit>.md`
rather than overwritten, so review history survives. It exists so another AI can verify this work
instead of trusting it. Every claim below has a command to check it.

**What the frozen files are, because the filename alone is ambiguous.**
`HANDOFF-2026-08-01-211f2f8.md` is byte-identical to `HANDOFF-CURRENT.md` **as it stands at
accepted `main` `211f2f8`** — it holds no candidate-branch state, and its own content is
deliberately stale (it still says "Acceptance tag: NONE yet", which was true when it was written
and is not now). That is what a freeze is for. The identity is checkable, which is the whole
value of not editing it:

```
git rev-parse HEAD:HANDOFF-2026-08-01-211f2f8.md   # 3918615e…
git rev-parse main:HANDOFF-CURRENT.md              # same blob
```

`HANDOFF-2026-07-31-a493e22.md` does **not** follow that rule: no handoff file existed at
`a493e22`, so it is named for the HEAD current when the content was handed over, not for a file
you can diff against that commit. The two conventions differ; do not assume either from the name.
For anything frozen from here on, name it for the date and the scope and state the scope inside
the file — a filename is not metadata.

---

## START HERE

**Nothing is half-finished. The tree is clean, everything is committed and pushed, `main` is
untouched.** There is exactly ONE next action and it needs the Windows machine.

### The one thing to do

```powershell
git checkout hotfix-pipeline-status-multi-output-staleness
.\Capture-PipelineStatusEvidence.ps1 -FullSuite -ConnectivityScan
```

**It will ask for the seed keyword and wait** (`Read-Host`, line 236). Run it from a real console
you are sitting at — piped or non-interactive invocation gets EOF and the seed arrives empty. The
recorded T2 seed is `personalized nurse sweatshirt`; confirm it rather than assuming it, because
a silently changed seed measures a workspace nobody asked about.

That single run produces every remaining piece of acceptance evidence for
`core/pipeline_status.py`: a real `runs/T2` execution proved read-only by SHA256 tree
snapshot, the three Windows-only tests, the full suite, and the connectivity scan. It **throws**
rather than reporting a partial pass.

**A throw is evidence of a real failure condition — it is not a pass.** If it fires: preserve the
evidence directory, fix only the demonstrated defect with a focused test, commit to the branch,
push branch-only, rerun the **complete** gate from a clean state, and keep acceptance on **HOLD**.
A gate that has been made to stop throwing is not a gate that has run. Do not weaken a gate,
convert a throw to a warning, merge, tag, or bypass a Windows test.

**The three Windows-only tests have now run** (§13) and pass, so the gate will get further than
it ever has. It has still never completed. The interpreter check is already satisfied in this
repo's shell — bare `python` resolves to `.venv\Scripts\python.exe`, not the Store alias — but the
script re-checks it at run time rather than trusting that record, because a different shell has a
different PATH.

### What NOT to do

Do not merge or tag anything. Do not start Phase 7.15, Phase 8, or the full Pipeline Observer
(§8). Do not do further static hardening on the branch — the policy is to stop once no known
blocker remains. §13 opened two and closed both; if that stays true, stop.

### If you cannot get to Windows

Nothing else on this branch can progress. The useful alternatives are §6 (acrylic — the one open
gap that could produce a listing you have to pull) or the deferred items in §8.

## 0. Since the last version

Two things closed, one opened.

**The composite Phase 7.14 launcher hotfix is ACCEPTED, merged, tagged, and now PUSHED.**
`origin/main` is `211f2f8` and the annotated tag resolves there. The previous handoff's warning
about local-only acceptance history is resolved; nothing is unpublished.

**`d163ff0`'s two audit defects are remediated** on `hotfix-pipeline-status-multi-output-staleness`,
which then went through four more independent reviews. It is pushed, unmerged, untagged, and on
**HOLD** pending Windows evidence. Full state in §12.

**What that sequence cost, and why it was worth it.** Six review rounds found defects that static
review on a macOS clone could not: a PowerShell parsing rule that would have failed the gate for
the wrong reason, an encoding trap that would have aborted the capture, a gate that could pass on
a *skipped* test, and a `python` PATH resolution that can silently open the Microsoft Store. Four
of those were in code I had already called finished. The lesson worth carrying: **a test that
only runs on a machine you do not have is a test you have not run.**

**Then the tests ran, and proved that lesson twice more** (§13). Six static rounds had left an
execution proof that could not pass on any machine, and behind it a real defect that dropped a
character out of the owner's seed. Neither was findable by reading.

**On the reviews themselves.** Several are external artifacts and are **not repository-verifiable**
— the JSON files they arrived in are not in this repo and never were
(`git log --all -- '*REVIEW*FEEDBACK*'` and `'*CONSOLIDATION_REVIEW*'` are both empty). What is
verifiable is what each one saw and what was done about it, so every correction is stated in full
where it is applied and the text stands without the source file. Treat a quoted review verdict in
this document as a claim about an unversioned artifact.

**Two review findings were amended rather than accepted, with evidence, and neither should be
quietly re-accepted.** `proc.poll()` **is** conclusive when it returns non-`None`: `Popen` owns
the child, so a reaped exit code cannot describe a different process — the invalid inference runs
the other way. And the Pipeline Observer is **not** a thin read adapter (§8 #3).

## 1. Owner's stated goal (recorded 2026-07-31)

Practical, simple **Amazon Market Intelligence Dashboard**, Amazon US only, this workflow:

```
H10 seed keyword → import Amazon + H10 Xray → pick 10-ASIN batches (2-3 of them)
→ Cerebro those batches on H10 → re-import keywords → clean/merge/analyze
→ ONE Master Keyword List → score + select opportunity → match keywords to listing
→ generate listing + A+ → generate photo/A+ prompts → export keywords for PPC
→ (owner runs PPC manually on Amazon) → import PPC reports → analyze + suggest
```

Hard constraints: never invent metrics; show source + date of every metric; keep raw
imports separate from calculated data and AI suggestions; no Seller Central connection;
owner imports and uploads by hand; AI recommends, owner approves; ≥$8 profit/unit;
POD, embroidery, personalized, jewelry, **acrylic**; modern uncluttered UI, one primary
action per screen; preserve existing features, no unnecessary rewrite.

### Owner decisions recorded this session

| Question | Answer |
|---|---|
| Is `dashboard/app.py` live? | **No.** Built as a backup-plan API, never used. |
| Acrylic products? | **Selling now.** Live gap, not future. |
| PPC export shape? | Export from Amazon → analyze in tool → **owner runs campaigns manually**. No bulk-upload export needed. |

---

## 2. Core workflow engine inventory — code paths found for 12 of 13 stages

**C1 correction.** The previous version said "12 of 13 steps already BUILT". Finding code
and docstrings proves **code presence** — not acceptance, not end-to-end execution, not
artifact compatibility, not UI readiness, not pilot readiness. Read the table below as
`CODE_PRESENT` unless a stronger status is stated; several stages are also `ARTIFACT_PROVEN`
(they have produced real CLI artifacts in `runs/T2/`), and only some carry an acceptance tag.

Verified by reading module docstrings and source, not inferred from filenames.

`ARTIFACT_PROVEN` below means a real output file for that stage exists in `runs/T2/` — named
in the last column, so it can be listed rather than believed. **No stage is `UI_READABLE`,
`UI_EXECUTABLE` or `PILOT_PROVEN`** from the launched console; that is the §3 finding.

| Step | Status | Module | Artifact in `runs/T2/` |
|---|---|---|---|
| 1. H10 seed | CODE_PRESENT | `research/phaseA_master.py` | (input stage) |
| 2. Import Amazon + Xray | ARTIFACT_PROVEN | `research/phaseA_master.py`, `research/export_detector.py` | `Helium_10_Xray_.xlsx`, `ASIN-CANDIDATES.json` |
| 3. 10-ASIN batches, 2-3 | ARTIFACT_PROVEN | `research/batch_optimizer.py` — *"two (rarely three) strategically distinct batches of ten"* | `ASIN-BATCHES.json`, `ASIN-BATCH-REPORT.md` |
| 4. Re-import after Cerebro | ARTIFACT_PROVEN | `research/cerebro_export_detector.py` | `US_AMAZON_cerebro_*.xlsx` (2 files) |
| 5. Clean / merge / analyze | ARTIFACT_PROVEN | `research/keyword_evidence_matrix.py`, `keyword_relevance_lean.py`, `sqp_crosscheck.py` | `CEREBRO-EVIDENCE-MATRIX.json/.csv` |
| 6. ONE Master Keyword List | ARTIFACT_PROVEN | `research/master_keyword_builder.py` | `MASTER-KEYWORDS-LEAN.json/.md/.xlsx` |
| 7. Score / select | ARTIFACT_PROVEN | `research/demand_score.py` (GO/TEST/SKIP), `research/asin_scoring.py` | `OPPORTUNITY-REPORT.md`, `KEYWORD-INTELLIGENCE.json` |
| 8. Keywords → listing | ARTIFACT_PROVEN | `listing/keyword_allocation_planner.py` | `LISTING-BRIEF.json`, `BACKEND-SEARCH-TERMS.json` |
| 9. Listing + A+ | ARTIFACT_PROVEN | `listing/*`, `production/aplus_assembly.py` | `PRODUCT-PAGE.json`, `BASIC-APLUS-CONTENT.json` |
| 10. Photo / A+ prompts | ARTIFACT_PROVEN | `creative/creative_production_package.py` | `CREATIVE-BRIEF.md`, `CREATIVE-ASSET-CHECKLIST.json` |
| 11. PPC export | CODE_PRESENT, **NOT ACCEPTED** | `production/phase7_extended_launch_planning.py` | plans + `MANUAL-ENTRY-WORKSHEET.csv`; **no accepted tag**; gated behind owner-confirmed economics |
| 12. Import PPC reports | ACCEPTED | `production/phase7_report_ingestion.py` | `phase7/7.2/` (tag `phase7-2-cumulative-accepted-d5ad841`) |
| 13. Analyze + suggest | ACCEPTED | `production/phase7_ads_analysis.py`, `phase7_owner_decision_package.py` | `phase7/7.3/`, `7.5/` (both tagged accepted) |

**Conclusion (C2 correction).** No obvious missing core analysis engine was found for the
existing non-acrylic workflow. The remaining gaps are acceptance, artifact contracts, UI
reachability, orchestration and pilot proof — which is more than "one UI", and the
distinction matters: read-only presentation is mostly UI work, but **run-from-UI** needs
new orchestration authority, locking, progress, cancellation, timeout, recovery, audit and
write-authority design, each with its own security review and acceptance (C3).

Verify: `python -c "import ast;print(ast.get_docstring(ast.parse(open('research/batch_optimizer.py',encoding='utf-8').read()))[:300])"`

---

## 3. PRIMARY FINDING — the front door opens into the wrong room

```
production/phase7_owner_launcher.py:118
    CONSOLE_MODULE = "production.phase7_unified_owner_console"
```

That console's pages: `overview · analysis · decisions · actions · alerts · followups ·
notifications · research · system · watchlists` — **steps 12-13 only** (post-launch ops).

Steps 1-11 live in `dashboard/app.py`, which **no `.bat` or `.ps1` launches**, and which
the owner has never used.

So double-clicking Start lands on the half of the toolkit whose decision queue has
**0 rows**, with no route to the half that does the work.

**C4 correction.** This likely explains a major part of the empty pilot — the owner lands
in a post-analysis console while the upstream workflow artifacts and actions are
unreachable — but it is not proven to be the only cause.

**C5 correction.** The previous version said "~50% of production code serves 0 decisions".
That conflated Phase 7's line count with decision-queue output. What is actually measured:
the current promoted decision queue contains only its header, so the default console has
little business content to show **in the present workspace**. That code still serves
analysis, system state, alerts and notification regardless.

Verify:
```
grep -n "^CONSOLE_MODULE" production/phase7_owner_launcher.py
grep -rn "dashboard/app" --include=*.bat --include=*.ps1 .        # returns nothing
ls production/phase7_unified_owner_console_static/
wc -l runs/T2/phase7/7.3/promoted/owner-decision-queue.csv        # 1 = header only
```

---

## 4. Secondary finding — duplicate process-supervisor subsystems

Two independent modules start/stop/inspect a local web app on one machine:

| Module | Lines | Supervises | Used by |
|---|---|---|---|
| `core/instance_manager.py` | 748 | old `dashboard/app.py` | `amz_fbm/cli.py`, `core/local_install.py`, `core/windows_integration.py`, `scripts/*` |
| `production/phase7_owner_launcher.py` | 1712 | 7.13 console | `Start/Stop-AMZ-Toolkit.ps1` |

**2,460 lines of process management**, and the launcher half has produced three
consecutive Day-0 pilot defects. The launcher does not import instance_manager — they
are fully independent implementations of the same job.

Root cause of the launcher's complexity: the console runs **detached**, which creates the
"is this the process I started?" problem that PID records, start tokens, handle-pinned
identity, exit verification and stale sweeps all exist to answer.

**C8 correction.** The previous version claimed a foreground console "removes the entire
problem class in ~60 lines". That was speculation. A foreground-owned process model *may*
eliminate much of the detached-process identity complexity, but its impact on desktop
shortcuts, browser launching, CLI installation, window closure, recovery and the existing
scripts must be traced before it is proposed as a replacement. Deferred either way (§8 #4).

Verify: `grep -rln "instance_manager" --include=*.py . | grep -v test`

---

## 5. `NULL_RECORDED_START_TOKEN` — FIXED, AUDITED, ACCEPTED, MERGED, TAGGED

Full evidence: `SESSION7_14-NULL-START-TOKEN-HOTFIX-REPORT.md` + `-PROOF.json`.

The pattern existed at **four** sites, not three: the previous version missed the source.
Start held a `Popen` that owns the child, read that child's identity by **raw PID** anyway,
persisted whatever came back — including `None` — and returned `SESSION7_14_LAUNCHER_READY`.

**Measured on the baseline, not theorised:** a real unrelated live Windows process whose PID
was recorded with a null token was **terminated**, and the stop reported
`SESSION7_14_LAUNCHER_STOPPED` with `identity_verified: true`. Its own record carried
`handle_token_matches_recorded: false` and `process_token_matches_recorded: false` — it had
the evidence that identity did not match, wrote it down, and killed anyway. The hard-path
check passed because `expect_token` was derived from the live handle, so the termination
handle was validated **against itself**.

| # | Site | Was | Now |
|---|---|---|---|
| 0 | `_start_locked` | manufactures the null token, reports READY | fails closed, no record, child killed via the owned `Popen` |
| 1 | `_pinned_identity` | **authorizes termination** | `PROCESS_IDENTITY_UNPROVEN`, `terminate_requests: []` |
| 2 | `_clear_stale_pid` | reuse branch skipped; unreadable live token wrongly cleared the record | three distinct answers, record kept when nothing is proven |
| 3 | `status` | `launcher_owned: true`, unverified | verified or not claimed |

**Why a truthiness test was the wrong gate**, carried forward because it is the transferable part:
only **falsy** tokens (`None`, `""`) slipped the old check. `"   "` and `12345` were refused
incidentally — non-empty values are truthy, so the `!=` comparison still ran and still rejected
them. The gate appeared to work because the cases that reached it were the ones truthiness happens
to handle. That is why one shared `valid_identity_token()` now governs all four sites rather than
four local `if token:` tests.

One shared `valid_identity_token()` at all four. 30 new tests (26 failed against the unfixed
module), real-Windows bystander proof, real end-to-end console cycle recording
`identity_source: popen_handle`. The audit's own measurement, on three trees against a bystander
process it spawned itself: two baselines reported success or failure while **killing** a real
unrelated process with `identity_verified: true`; only `56f4339` reported `STOP_REFUSED` with
`identity_verified: false` and left it alive.

Verify:
```
python -m unittest tests.test_phase7_14_owner_usability_pilot_readiness 2>&1 | tail -5
grep -n "def valid_identity_token\|def process_start_token_from_popen" production/phase7_owner_launcher.py
```

Verify: `sed -n '1395,1400p' production/phase7_owner_launcher.py`

---

## 6. Acrylic gap — owner sells acrylic now, zero support exists

`grep -rli acrylic` returns **nothing** repo-wide. Categories present: `apparel`, `pod`,
`jewelry` (+ default fallback).

Honest scoping — the JSON record is trivial, the real work is not:

* All three existing category policies have **identical** limits (title 75, 5 bullets @500,
  desc 2000, backend 249 bytes, 5 highlights @125). Only `product_category`,
  `category_identifier` and `policy_source` differ. So the policy record is ~15 lines.
* The **real** acrylic work is claim rules. "Shatterproof", "unbreakable", "won't yellow",
  "crystal clear" are durability/safety claims, and `config.yaml` already sets
  `unsupported_high_risk_claim_is_hard_gate: true`. Copying the apparel rules would let
  unsupported acrylic durability claims through.
* `listing/a_plus_templates.py` ships **6 A+ templates for embroidered apparel**, and
  `config.yaml` sets `creative_benchmarks.category: apparel` globally. Acrylic needs
  template and benchmark review, not just a category key.

Verify:
```
grep -rli acrylic --include=*.py --include=*.json .
python -c "import json;print([p['product_category'] for p in json.load(open('listing/category_policies.json',encoding='utf-8'))['policies']])"
```

---

## 7. What is already correct — do not touch

The three hardest owner requirements are enforced in code today:

* **Never invent data** — `sqp_crosscheck.py`: *"Where H10 and Amazon disagree, Amazon wins."*
  `keyword_evidence_matrix.py` keeps per-ASIN rank columns instead of collapsing to max/min.
  Real T2 ads data: 114 rows → 113 `INSUFFICIENT_DATA`, 1 `PROMISING_LOW_DATA`, decision
  queue **0 rows** — it declined to recommend rather than fabricate.
* **Source + date on every metric** — 7.3 rows carry `source_file`, `source_file_sha256`,
  `source_row_number`, `lineage_hash`, `date_coverage`.
* **Raw vs calculated separation** — 7.2 uses `inbox/ → staging/ → quarantine/ → promoted/
  → final/`; `core/provenance.py` and `listing/claim_evidence.py` separate evidence from
  inference.
* `config.yaml`: `minimum_contribution_profit: 8.00`, `marketplace: US`,
  `seller_central_connection_allowed: false`.
* Amazon boundary: all Seller Central counters 0, 47/47 policy tests pass, connectivity
  scan reports 0 active Amazon-account paths.

---

## 8. Plan — reordered by the 2026-07-31 review, and by what is now done

**#1 — Launcher identity defects. DONE, AUDITED, ACCEPTED, MERGED, TAGGED, PUSHED** (§5, §0).
All four sites, one shared validator, Start-side handle read, real-process bystander proof,
truthful owner recovery copy. Tag `phase7-14-composite-launcher-safety-hotfix-accepted-211f2f8`,
on `origin`. Nothing left to do here.

**#1b — `d163ff0` defects. REMEDIATED, ON HOLD** (§11, §12). Both closed on
`hotfix-pipeline-status-multi-output-staleness`, plus a third the first Windows run found (§13).
**The only remaining work is the Windows evidence run** — see START HERE. Do not lean on the CLI
status output from `main`; `main` has no `core/pipeline_status.py` at all.

**#2 — Use the CLI on a real product, then run the pilot.** The console covers steps 12–13 and its
decision queue is legitimately empty: 114 real ads rows produced 113 `INSUFFICIENT_DATA` and 1
`PROMISING_LOW_DATA`, so the engine declined to recommend rather than fabricate. **The pilot could
never have reached Day 1** — not only because of launcher defects, but because the screen it opens
on has nothing to review, and will not until more PPC data exists. More data means more weeks of
live campaigns, not more software. Get products live, feed PPC reports in weekly, let the queue
fill. Then measure: time to identify the current stage, stale status, missing artifacts, how often
the CLI is still needed, dead navigation, confidence.

**#3 — Read-only Pipeline Observer** ("Pipeline Observer V1"). Pipeline navigation and
canonical artifact state only; **no engine execution from the browser**; `Run` / `Re-run` /
`Import` / `Generate` / `Approve` / `Execute` must not appear without their own accepted
authority. Scope honestly: the 10-state status model, block reasons, prerequisites and
freshness are a **derivation layer that does not exist today**, so this is new logic with its
own tests — not a thin read adapter (see §0). Its honest positioning is "information
architecture and real-artifact pilot"; it does **not** yet deliver the no-PowerShell owner
experience, because every stage is still run from the CLI.

**#4 — Acrylic listing safety baseline** (see §6): category policy, evidence rules, claim
hard gates, listing tests, acrylic creative checks. **A+ disabled at first, with a clear
stated reason** — the 6 existing templates assume embroidery, garments, fit, stitching, care
and model-based lifestyle imagery.

**#5 — Later, NOT during the pilot** — retire `dashboard/app.py` and collapse the duplicate
supervisor (§4), only after tracing the `amz_fbm` CLI install path, `core/local_install.py`,
`core/windows_integration.py`, browser launch, desktop shortcuts, recovery, and
scripts/preflight dependencies. Blast radius: `build_manifest.py`, `capabilities.py`,
`core/instance_manager.py`, `production/phase7_preflight.py`, `scripts/connectivity_scan.py`,
2 tests.

**Do not build:** Phase 7.15+, Phase 8, OS-v3 web app, PPC bulk-upload export. The
`MANUAL-ENTRY-WORKSHEET.csv` path may be used as internal evidence but must **not** be
exposed as accepted production output while 7.1E carries no acceptance tag, and its economics
gate stays — the UI should say exactly which economics fields are missing, not bypass it.

---

## 9. Scale context for reviewers

| | |
|---|---|
| Production code | 60,539 lines / 3.0 MB |
| Test code | 41,658 lines / 2.0 MB (84 files) |
| Root docs + proofs | 14,092 lines / 1.5 MB (118 files) |
| Phase 7 alone | 30,112 lines = 50% of production code |
| Commits | 36 docs / 29 feat / 14 fix / 12 test |
| Empty packages | `analytics/`, `feasibility/`, `positioning/`, `reports/` — 0 lines |

**Historical, pre-environment-repair, in place:** `python -m unittest discover -s tests` →
4668 ran, 1 failure, 4 skipped, ~19 min. **DO NOT QUOTE AS THE CURRENT OR CANONICAL SUITE
RESULT.** It was produced before the environment repair, under a different interpreter and
dependency state, so it is not comparable with anything measured since. It is kept, not deleted,
because earlier documents cite it.
The single failure is `test_199e_no_acceptance_tag_yet`, **permanently stale** (it asserts no
`phase7-14-*` acceptance tag exists; three do, all predating this branch). Two prior audits
recommended retiring it. Not a regression.

No single absolute pass count replaces the historical figure above, deliberately — a bare count
with no interpreter attached is what made it misleading in the first place. For authoritative
figures use the repaired-environment, matched-worktree, **exact-ID differentials** in
`SESSION7_14-NULL-START-TOKEN-HOTFIX-REPORT.md` and
`SESSION7_14-STOP-EXIT-VERIFICATION-HOTFIX-REPORT.md`, and record the interpreter alongside any
number you quote. Note that a fresh detached worktree does not contain the gitignored `runs/T2/`
fixtures, so its collection and skip totals legitimately differ from an in-place run; worktree
figures are only ever compared worktree-to-worktree.

**Known Windows loopback flakes — do not read these as regressions.** Two nodes fail
intermittently under full-suite load and pass in isolation:
`test_phase7_13_unified_owner_console.TestBody.test_52_request_size_bounded` and
`test_phase7_4_owner_dashboard.HttpSecurity.test_post_to_unknown_endpoint_rejected`, both with
`ConnectionAbortedError: [WinError 10053]`. Classification:
**`PRE_EXISTING_ACCEPTED_WINDOWS_LOOPBACK_FLAKE`**. An independent audit of the composite 7.14
hotfix observed `test_52` **once** on the target; the console source and its test are identical
git blobs across baseline and target, 12/12 isolated runs passed on both trees, a second target
full-suite sample did not reproduce it, and prior accepted reports already record the same
signature. Confirm cheaply in that order — identical blobs, isolation, second sample — before
treating either as a regression. Full detail in
`SESSION7_14-NULL-START-TOKEN-HOTFIX-REPORT.md` and
`SESSION7_14-STOP-EXIT-VERIFICATION-HOTFIX-REPORT.md`.

The suite remains **non-green**: the known non-passing tests above remain non-passing.

Known verification weakness: an accepted browser gate scored 44/44 on a console where 226 of
259 controls were unclickable. Judge this codebase by real use, not by its proof volume.

---

## 10. Open questions for the next reviewer

1. Should acrylic A+ get its own templates, or is "no A+ for acrylic yet" acceptable at launch?
2. Is `MANUAL-ENTRY-WORKSHEET.csv` from the unaccepted 7.1E good enough for manual campaign
   entry, or should the keyword→campaign suggestion move somewhere reachable without 7.1E's
   economics gate?
3. Does retiring `core/instance_manager.py` break the `amz_fbm` CLI install path
   (`core/local_install.py`, `core/windows_integration.py`)? Not yet traced.
4. Did the re-run of `ASIN-CANDIDATES.json` actually change the candidate set? If not, steps
   3/4/6/14 can be left alone and no Cerebro credits need spending. Answerable in minutes.

---

## 11. `d163ff0` — remediated, no longer the blocker it was

`core/pipeline_status.py` — the read-only "where am I in the pipeline, what do I run next" map.

```
python -m core.pipeline_status --seed "<seed keyword>"      # add --json for machine use
```

**Both audited defects are closed** on the branch in §12, and are NOT in `main`:

* **DEFECT A, blocking — `MULTI_OUTPUT_STALENESS_MASKED`.** `evaluate()` compared the *newest*
  output against the newest input, so on a multi-output stage a fresher sibling hid an artifact
  genuinely older than its own input. Measured on an identical workspace through the real CLI:
  the baseline reported stage 5 `ok` and sent the owner to **step 6**; fixed, it reports `STALE`
  and sends them back to **step 5**. It was walking the owner past a Master Keyword List that had
  never seen its Cerebro data. Affected stages 5 and 11.
* **DEFECT B — command rendering.** Now targets ONE named shell (Windows PowerShell), refuses
  values it cannot pass through unchanged, and prints no command at all when the seed is unknown.

**The old workaround is obsolete.** The previous handoff said to treat `READY` on stages 5 and 11
as unproven and check them by hand. On the branch that is fixed. On `main` it is **not
applicable** — `main` has no `core/pipeline_status.py` at all, so there is no status output there
to distrust. An earlier wording said the workaround "still applies" on `main` and then explained
that nothing there produces one; a workaround for a file that does not exist is not a live caveat.


---

## 12. The active branch — `hotfix-pipeline-status-multi-output-staleness`

**Pushed. Not merged. No tag. `main` untouched at `211f2f8`.**

The head hash is deliberately not written here — a document that names its own commit is wrong
the moment it is committed. What matters is checkable:

```
git rev-parse --short HEAD origin/hotfix-pipeline-status-multi-output-staleness   # must match
git tag --points-at HEAD                                                         # must be empty
git merge-base --is-ancestor 518b516 HEAD && echo descends-from-audited-baseline
```

### Commit stack

| Commit | What |
|---|---|
| `518b516` | `d163ff0` cherry-picked **verbatim** — the audited baseline, replayed on accepted `main`. Byte-identity proved by patch-id `7c7594d2…` and matching blob hashes, not inferred from a clean cherry-pick |
| `95f9b67` | DEFECT A — blocking |
| `fcd6d31` | DEFECT B — separable, `git revert` removes it alone |
| `631a491` | report + proof, revision 1 |
| `2894269` | review corrections C1–C5, C7 |
| `5104904` | revision 2 |
| `018af92` | Windows PowerShell execution test + capture script |
| `cabab15` | revision 3 |
| `c3c4af2` | bug hunt — eight defects |
| `89e28e0` | revision 4 |
| `855f4f4` | refuse unrenderable values; prove the call operator |
| `f36a2ca` | revision 5 |
| `ea2190b` | audit against `required_checks` — six gaps |
| `c6cbdb9` | revision 6 |
| `06d42cc` | handoff |
| *this commit* | revision 7 — first Windows execution, two defects (§13) |

`66 tests — OK (skipped=0)` **on Windows**. Every earlier count in the report reads
`64 — OK (skipped=3)` and is not comparable: it was measured where the Windows-only tests cannot
run, so it describes a different set of executed tests rather than a worse result. Read
`SESSION7_14-PIPELINE-STATUS-REMEDIATION-REPORT.md` (§§10–13 are the review history) and
`-PROOF.json` (machine-readable; `first_windows_execution` holds §13, `response_export` holds
decisions and next steps).

### The blockers — was five, now three

1. `Capture-PipelineStatusEvidence.ps1` has **never completed a run**.
2. No real `runs/T2` execution evidence.
3. No fresh independent re-audit of the final Windows-evidenced commit.

Closed on 2026-08-01, recorded rather than deleted so the change is auditable:

* ~~The three Windows-only tests must pass on Windows.~~ **They ran and pass** — and two of them
  failed first. §13.
* ~~Bare `python` interpreter resolution unevidenced.~~ **Resolved in this repo's shell** to
  `.venv\Scripts\python.exe` 3.12.10, not the Store alias, identically under `-NoProfile`. Scope
  it that way: it is not evidence for a shell with a different PATH, which is why the capture
  script still checks at run time.
5. No fresh independent re-audit of the final Windows-evidenced commit.

### Decisions a new session must not silently reverse

These are two different kinds of thing and were previously one list, which is how a permanent
safety contract gets traded away in a refactor that only meant to change a policy.

**PERMANENT INVARIANTS — not changeable by a design decision.** Reversing any of these is a
safety regression regardless of what it buys:

* **No silently changed owner seed.** The seed is measured, echoed and refused — never
  normalised, trimmed or substituted. §13's defect 2 is what breaks this in practice.
* **No command emitted without its real required values.** A placeholder that looks pasteable is
  worse than no command.
* **One explicitly named shell per rendered command.** A renderer naming no shell is correct
  for none.
* **No Amazon Seller Central or Amazon API connection.** Not from this module, not from anything
  it prints.
* **Read-only pipeline status stays read-only.** `core/pipeline_status.py` executes nothing;
  proved at runtime, not by name scan (report §4).

**CURRENT POLICIES — changeable, but only through an explicit issue, a failing test, a proof
update and a fresh review.** They are decisions with reasons, not contracts:

* **Refuse rather than render inexactly.** A seed that both needs quoting and ends in a
  backslash is rejected with exit 2 and a structured `UNSUPPORTED_VALUE` in `--json`, because
  PowerShell cannot pass it to a child unchanged. A **bare** `nurse\` is unaffected and stays
  supported. Extended on 2026-08-01 to the **double quote** (§13) — that one is refused by
  *choice*, since it can be rendered exactly; the two cases are not the same and the docstrings
  say so.
* **`&` belongs to the test fixture, not production.** No production command starts with `&`,
  because none starts with a quoted value. `test_every_command_starts_with_a_bare_literal_token`
  is the tripwire if that changes.
* **Normal production commands begin with bare `python`.** Which makes PATH resolution a
  contract, not an assumption — hence the capture script's interpreter check.
* **`produces` is required-all.** Optional or any-of outputs would produce a false `STALE`.
* **Equal mtimes are `READY`.** Strict `<`. A permanent false `STALE` is worse.

### After a successful Windows run

Follow-up docs/proof commit with the real evidence — **do not rewrite history**, revisions 1–7
are preserved at the commits above. Push branch-only. Then a fresh independent re-audit from new
worktrees. Merge and tag only after that, as a separate owner-approved step.

---

## 13. The Windows-only tests finally ran — and found two defects

Full detail in the report §13 and in `-PROOF.json` → `first_windows_execution`.

`Ran 66 tests — OK (skipped=0)`, Windows 11, bare `python` = `.venv\Scripts\python.exe` 3.12.10.
First result on the same command: `Ran 64 tests — FAILED (failures=2)`.

**Defect 1 — the execution proof could not pass on any machine.** Both Windows execution tests
asserted `argv == ["--seed", seed]` *and* `assertNotIn("PWNED", run.stdout)` in the same loop
body. Four corpus seeds contain `PWNED` literally, so a **correct** renderer is precisely what
puts the marker in the probe's argv echo. Unsatisfiable by construction, and invisible for as
long as it was skipped — which was everywhere. Now it counts output lines instead, which is
strictly stronger. The regression guard runs on **every** platform on purpose.

**Defect 2 — real, and hidden behind defect 1.** With the loop able to reach the fourth seed,
`nurse"quote"` rendered as `'nurse"quote"'` arrived at the child as **`nursequote`**. PowerShell
5.1 rebuilds the native command line without escaping an embedded quote and the C runtime eats
it. The owner would have searched a keyword they never typed with nothing on screen to show it —
the same failure class as DEFECT A, a wrong value presented as a right one.

**Refused, by owner decision, and the reason is a choice not an impossibility.** Measured through
real `powershell.exe`: `'nurse\"quote\"'` arrives byte-exact, so this value *is* renderable —
unlike the trailing backslash. Emitting it would mean carrying the C runtime's backslash-doubling
rules inside a renderer whose job is a two-word keyword. A double quote is not part of any real
Amazon search term, so `unsupported_value()` gained a third refusal instead. A single quote —
`nurse's gift` — is unaffected and still renders `'nurse''s gift'`.

Blast radius: `core/pipeline_status.py` and its test, the only two files in the repo that
reference either.

### The `docs-handoff-post-acceptance-2026-08-01` branch — superseded, not disposable

Checked before saying so, because "logically superseded" and "safe to delete" are different
claims:

```
git log --left-right --cherry-pick --oneline \
  hotfix-pipeline-status-multi-output-staleness...docs-handoff-post-acceptance-2026-08-01
git diff --name-status docs-handoff-post-acceptance-2026-08-01..hotfix-pipeline-status-multi-output-staleness
```

It holds **one** unique commit, `e99de03`, touching **one** file, `HANDOFF-CURRENT.md`. No unique
proof artifact, no command transcript, nothing else to lose. But its handoff was not merged into
this one — it was re-authored from `main`'s copy — so three still-true things had been dropped and
are restored above: the falsy-vs-truthy explanation of why the old identity gate looked like it
worked (§5), the note that the review artifacts are external and not repository-verifiable (§0),
and the `proc.poll()` amendment (§0). **Deletion remains a separate owner-approved step.**

### What is still not proven

The gate script has still never completed a run. Nothing in §13 is `runs/T2` evidence, and a
suite that has been made to pass is not a gate that has run. **Acceptance stays HOLD.**
