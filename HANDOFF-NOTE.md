# HANDOFF NOTE

**Updated:** 2026-07-31 · **Type:** full-tool review, **NO CODE CHANGED**
**Branch:** `hotfix-phase7-14-stop-exit-verification` · **HEAD:** `a493e22`
**main:** `a68c147` (untouched) · **Merged:** NO · **Acceptance tag:** NONE

This file is overwritten after each major change. It exists so another AI can verify
this work instead of trusting it. Every claim below has a command to check it.

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

## 2. Verified workflow map — 12 of 13 steps already built

Verified by reading module docstrings and source, not inferred from filenames.

| Step | Status | Module |
|---|---|---|
| 1. H10 seed | BUILT | `research/phaseA_master.py` |
| 2. Import Amazon + Xray | BUILT | `research/phaseA_master.py`, `research/export_detector.py` |
| 3. 10-ASIN batches, 2-3 | BUILT | `research/batch_optimizer.py` — *"two (rarely three) strategically distinct batches of ten"* |
| 4. Re-import after Cerebro | BUILT | `research/cerebro_export_detector.py` |
| 5. Clean / merge / analyze | BUILT | `research/keyword_evidence_matrix.py`, `keyword_relevance_lean.py`, `sqp_crosscheck.py` |
| 6. ONE Master Keyword List | BUILT | `research/master_keyword_builder.py` → `MASTER-KEYWORDS-LEAN.json/.csv` |
| 7. Score / select | BUILT | `research/demand_score.py` (GO/TEST/SKIP), `research/asin_scoring.py` |
| 8. Keywords → listing | BUILT | `listing/keyword_allocation_planner.py` |
| 9. Listing + A+ | BUILT | `listing/*`, `production/aplus_assembly.py` |
| 10. Photo / A+ prompts | BUILT | `creative/creative_production_package.py` |
| 11. PPC export | **PARTIAL** | `production/phase7_extended_launch_planning.py` — plans + `MANUAL-ENTRY-WORKSHEET.csv`; **no accepted tag**; gated behind owner-confirmed economics |
| 12. Import PPC reports | BUILT | `production/phase7_report_ingestion.py` |
| 13. Analyze + suggest | BUILT | `production/phase7_ads_analysis.py`, `phase7_owner_decision_package.py` |

**Conclusion: no missing engines. The gap is one UI.**

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

This explains the empty pilot and why ~50% of production code serves 0 decisions.

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
identity, exit verification and stale sweeps all exist to answer. A foreground console
(server runs in the window the `.bat` opens; close window = stop) removes the entire
problem class in ~60 lines.

Verify: `grep -rln "instance_manager" --include=*.py . | grep -v test`

---

## 5. Open defect — `NULL_RECORDED_START_TOKEN` (audit finding, unfixed)

`production/phase7_owner_launcher.py:1397` in `_pinned_identity`:

```python
if not recorded:
    ev["authorized_by"] = "NO_RECORDED_TOKEN"
    return None, handle_token, ev          # None = AUTHORIZED
```

A null recorded start token **authorizes** termination, bypassing the entire identity gate
added by `4c5d362`. It is the first check in the function, so it short-circuits
`_handle_identity_required`, the pinned-handle check and both token comparisons.

`4c5d362`'s commit message claims *"any missing token … refuses"*. That is false for the
recorded token — the one the other two are compared against.

**The same falsy-token-skips-verification pattern exists at three sites, not one:**

| # | Site | Effect of a null token |
|---|---|---|
| 1 | `_pinned_identity` :1397 | **authorizes termination** (the audit's finding) |
| 2 | `_clear_stale_pid` :1226 | PID-reuse branch never runs |
| 3 | `status` :1498 | reports `launcher_owned: true`, unverified |

Site 3 matters: after fixing only site 1, Stop refuses while `status` still reports the
record as owned — a self-contradiction in one session.

Mitigating fact (verified): `_clear_stale_pid` clears any record where `not _alive(pid)`
regardless of token, so an exited console **is** reclaimed on next Start. The owner is
only stuck when the PID is alive but unverifiable — exactly when refusing is correct.

Recommended remediation scope: shared token validator applied at all 3 sites; failing test
first; honest owner copy delivered **in the Stop console output**, not the web panel (which
is unreachable when the console is wedged). Skip the proposed Start-side retry subsystem —
if the token can't be read, `proc.poll()` says the child is dead in one line. Better still,
read the token through the `Popen` handle Start already holds instead of by raw PID.

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

## 8. Recommended plan (ranked, not yet started)

**#1 — One console, pipeline-shaped.** Add steps 1-11 to the 7.13 console as new pages over
the existing engines. **No new business logic** — thin API layer over `research/`,
`listing/`, `creative/`. Since `dashboard/app.py` was never used, nothing needs porting.
Nav = the workflow, in order, one primary action per stage, data warnings on stale inputs.

**#2 — Acrylic, done properly** (see §6). Owner is selling it now.

**#3 — Minimal launcher fix + finish the 14-day pilot.** Fix the 3 null-token sites, ship,
then stop building and actually use it. The pilot has never reached Day 1 in three attempts.

**#4 — Later, NOT during the pilot** — retire `dashboard/app.py` and collapse the duplicate
supervisor (§4). Blast radius: `build_manifest.py`, `capabilities.py`,
`core/instance_manager.py`, `production/phase7_preflight.py`, `scripts/connectivity_scan.py`,
2 tests.

**Do not build:** Phase 7.15+, Phase 8, OS-v3 web app, PPC bulk-upload export (owner runs
campaigns manually — step 11 needs a readable worksheet, not an uploader).

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
