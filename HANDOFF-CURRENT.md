# HANDOFF — CURRENT

**Updated:** 2026-08-01 · **Branch:** `hotfix-phase7-14-stop-exit-verification`
**main:** `a68c147` (untouched) · **Merged:** NO · **Acceptance tag:** NONE

This is the LIVING handoff. Each superseded version is frozen as
`HANDOFF-<date>-<commit>.md` rather than overwritten, so review history survives —
see `HANDOFF-2026-07-31-a493e22.md`. It exists so another AI can verify this work
instead of trusting it. Every claim below has a command to check it.

## 0. Since the last version

An independent review (`AMZ_FBM_TOOLKIT_HANDOFF_REVIEW_FEEDBACK.json`, 2026-07-31)
returned **STRONG_BUT_REQUIRES_REVISION_BEFORE_IMPLEMENTATION_AUTHORITY**. Its
corrections C1, C2, C4, C5, C8 and C9 are applied below; §3, §4 and §8 are the
sections that changed. Its immediate next action —
`FINISH_AND_INDEPENDENTLY_ACCEPT_NULL_TOKEN_LAUNCHER_REMEDIATION` — is **done and
awaiting independent audit**: see `SESSION7_14-NULL-START-TOKEN-HOTFIX-REPORT.md`.

Two of its findings I amended rather than accepted, with evidence:

* `proc.poll()` **is** conclusive when it returns non-`None` — `Popen` owns the child,
  so a reaped exit code cannot describe a different process. The invalid inference is
  the other direction. The strong half of C7 — read through the owned handle — is what
  was implemented.
* The Pipeline Observer is **not** a thin read adapter. A 10-state status model with
  block reasons, prerequisites and freshness is a derivation layer that does not exist
  today, and must be scoped and tested as new logic or it will invent status.

---

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

## 5. `NULL_RECORDED_START_TOKEN` — FIXED, awaiting independent audit

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

One shared `valid_identity_token()` at all four. 30 new tests (26 failed against the unfixed
module), real-Windows bystander proof, real end-to-end console cycle recording
`identity_source: popen_handle`.

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

**#1 — Launcher identity defects. DONE, awaiting independent audit** (§5). The review made
this the immediate next action and it is complete: all four sites, one shared validator,
Start-side handle read, real-process bystander proof, truthful owner recovery copy.

**#2 — Merge and run the real 14-day pilot.** Three attempts, never reached Day 1. Measure:
time to identify the current stage, incorrect or stale status, missing artifacts, how often
the CLI is still needed, which stages most want a Run button, dead navigation, confidence.

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

Full suite: `python -m unittest discover -s tests` → 4668 ran, 1 failure, 4 skipped, ~19 min.
The single failure is `test_199e_no_acceptance_tag_yet`, **permanently stale** (it asserts no
`phase7-14-*` acceptance tag exists; three do, all predating this branch). Two prior audits
recommended retiring it. Not a regression.

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
