# DASHBOARD V1 — SPEC

**Status:** DRAFT, not implemented. **Written:** 2026-08-03.
**Scope:** read-only. No orchestration, no Amazon connection, no write authority.

Every claim in §1 has a command. Check it rather than trusting it.

---

## 1. The problem, measured

The owner double-clicks Start and lands on a console with nothing in it, while the work
sits finished on disk a few directories away.

```
grep -n "^CONSOLE_MODULE" production/phase7_owner_launcher.py
    118:CONSOLE_MODULE = "production.phase7_unified_owner_console"     # steps 12-13 only

grep -rn "dashboard/app" --include=*.bat --include=*.ps1 .           # nothing launches it
wc -l < runs/T2/phase7/7.3/promoted/owner-decision-queue.csv         # 1 == header only
ls runs/T2/*.json | wc -l                                            # 40+ finished artifacts
```

Steps 1–11 of the 13-stage workflow have produced real artifacts — `ASIN-BATCHES.json`,
`CEREBRO-EVIDENCE-MATRIX.json`, `MASTER-KEYWORDS-LEAN.json`, `BASIC-APLUS-CONTENT.json`,
`CREATIVE-BRIEF.md` and more. The front door renders none of them. It opens on
steps 12–13, whose decision queue is empty because the upstream promotion never ran.

**V1 is therefore not a new engine. It is the missing route to engines that already ran.**

---

## 2. Goal and non-goals

**Goal.** One front door that shows, for all 13 stages: what is done, what is stale, what
to run next, and what the last run produced — sourced only from artifacts already on disk.

**Non-goals for V1, each deferred for a stated reason.**

| Not in V1 | Why |
|---|---|
| Run stages from the UI | Needs new orchestration authority: locking, progress, cancellation, timeout, recovery, audit, write-authority design — each with its own security review (`HANDOFF-CURRENT` §2, C3). That is a larger project than V1 and must not ride in on V1's acceptance. |
| Any Amazon connection | Permanent boundary. Unchanged. |
| Editing artifacts | AI recommends, owner approves, owner edits at source. |
| Replacing `dashboard/app.py` | It is a never-used backup-plan API. V1 does not adopt it and does not delete it. |
| A second process supervisor | Two already exist (`core/instance_manager.py`, `production/phase7_owner_launcher.py`). V1 adds none. |

**Controls that must not exist anywhere in V1's UI**, as a checkable list rather than a principle:

```
Run    Re-run    Import    Generate    Approve    Execute
any Amazon connection            any browser-side pipeline mutation
```

If a screen needs one of these to be useful, the screen is out of scope for V1 — not a reason to
add the control. The one primary action a stage screen may offer is **copying a command** for the
owner to run in their own shell (§8).

---

## 3. Architecture decision

**Extend `production/phase7_unified_owner_console.py`. Do not build a new app.**

Considered and rejected:

- *Wire `dashboard/app.py` into the launcher.* It is unused, unaccepted, and would activate
  the duplicate-supervisor problem the handoff already flags as a secondary finding.
- *New standalone V1 app.* Would duplicate loopback binding, strict CSP, Host validation,
  session/CSRF, and the hash-chained audit trail — all of which the 7.13 console already has
  and which are already independently accepted.

The console has clean extension points, which is what makes this cheap:

```
production/phase7_unified_owner_console.py
    build_<name>_section(config, *, now)     <- add build_workflow_section
    build_console_model(...)                 <- assemble it in
    line 124                                 <- page-title map

production/phase7_unified_owner_console_static/app.js
    VIEWS[]      { id, label, icon, render } <- add the workflow views
    NAV_GROUPS[] { title, items }            <- add a WORKFLOW group above INTELLIGENCE
```

`production/phase7_unified_owner_console.py` is **not** one of the 11 protected authorities
frozen by `test_136b`, so extending it does not touch an accepted-authority baseline.
Verify: the protected list is `tests/test_phase7_14_owner_usability_pilot_readiness.py`
`test_136b`; the console is absent from it.

---

## 4. Information architecture — 13 stages, 4 groups

The workflow is linear, so the UI is linear. One nav group, stages in execution order,
each stage a row that expands to a detail view.

```
WORKFLOW
  Research      1. Seed keyword      2. Import Amazon + Xray   3. ASIN batches
                4. Cerebro re-import 5. Clean / merge / analyze
  Decide        6. Master Keyword List   7. Score + select opportunity
  Build         8. Keywords -> listing   9. Listing + A+   10. Photo / A+ prompts
  Launch        11. PPC export      12. Import PPC reports    13. Analyze + suggest

INTELLIGENCE    research · watchlists · alerts        (existing, unchanged)
OPERATIONS      analysis · decisions · actions · ...  (existing, unchanged)
```

Steps 12–13 already have views. V1 does not rebuild them; it places them at the end of the
workflow where they belong, so the owner sees that the empty decision queue is stage 13 of a
pipeline whose stage 11 has not run — rather than an empty app.

---

## 5. Stage state model

Six states. Derived from artifacts on disk, never invented, never defaulted to a happy value.

| State | Meaning | Derived from |
|---|---|---|
| `NOT_STARTED` | no artifact for this stage | expected outputs absent |
| `READY` | artifact present, newer than every input | mtime + lineage vs upstream |
| `STALE` | artifact present, older than an input | an input is newer than the output |
| `BLOCKED` | a prerequisite stage is not READY | upstream state |
| `UNKNOWN` | artifact unreadable / unparseable | read or parse failure |
| `NOT_ACCEPTED` | stage code exists but carries no acceptance tag | stage 11 today |

`UNKNOWN` is a first-class state and must never collapse into `NOT_STARTED`. "I could not
read it" and "it is not there" are different answers, and the project has been bitten
specifically by a check that reported clean when it could not measure.

`NOT_ACCEPTED` exists because stage 11 (`phase7_extended_launch_planning.py`) has code but
no acceptance tag and is gated behind owner-confirmed economics. Showing it as `READY`
would be a false statement about a launch-planning stage.

---

## 6. The three-layer rule, enforced visually

A hard constraint, so it gets a visual grammar rather than a paragraph in a doc:

| Layer | Meaning | Presentation |
|---|---|---|
| **Imported** | raw, exactly as exported from H10 / Amazon | neutral; source filename + import date |
| **Calculated** | deterministic derivation by this toolkit | marked; shows the input artifact it came from |
| **Suggested** | AI recommendation awaiting owner approval | visually distinct; never mixed into a calculated table |

No row ever mixes layers. A screen showing suggestions shows them in their own block with an
explicit "not applied" affordance.

**Every metric carries source + date.** A number with no provenance is not rendered — it is
shown as `—` with the reason. This is the "never invent metrics" constraint made structural
instead of aspirational.

---

## 7. Workspace trust state

`runs/T2` drifted from its accepted 6F package across stages 6A–6E. The 6E cause is known
and fixed; **6A–6D is still unexplained**. The accepted bytes are unrecoverable — never
committed, `runs/` gitignored, no backup.

**Decided 2026-08-04: `runs/T2` is quarantined as `HISTORICAL`.** Not restorable, not a basis
for live owner guidance, and re-derived artifacts must never be presented as a restoration.
New work uses a **new workspace per real product**.

Every screen carries a workspace banner with exactly one state:

```
TRUSTED     lineage verified against the accepted package
UNVERIFIED  no accepted package to compare against (a fresh workspace)
HISTORICAL  quarantined; readable as history, never a basis for a new decision
```

`runs/T2` renders **`HISTORICAL`**. A `HISTORICAL` workspace is browsable but every screen it
feeds is marked, and it can never be the active workspace for guidance.

A fresh workspace carries: explicit run ID, product + seed identity, stage input/output hashes,
and a visible trust state. **No silent fallback to T2** — if no workspace is selected, V1 says so
rather than defaulting to the quarantined one.

---

## 8. Primary action per screen — copy-command, not run

Each stage screen has exactly one primary action: **Copy the command that advances this stage.**

```
Stage 5 · Clean / merge / analyze          STALE
  inputs   US_AMAZON_cerebro_*.xlsx  (2 files, imported 2026-07-28)
  output   CEREBRO-EVIDENCE-MATRIX.json    (built 2026-07-26 -- older than its input)

  [ Copy command ]
  python -m research.keyword_evidence_matrix --workspace runs/T2
```

This delivers the "what do I run next?" value with **zero new write authority** — no locking,
no cancellation, no audit design, no security review of a new execution path. The owner keeps
a terminal open, which they already do.

Run-from-UI stays a V2 question, and V1 shipping is not an argument for it.

---

## 9. Screens

| Screen | Purpose | One primary action |
|---|---|---|
| **Workflow** (landing) | 13 stages, state each, next action highlighted | Copy next command |
| **Stage detail** ×13 | inputs, outputs, provenance, freshness, what changed | Copy this stage's command |
| **Master Keyword List** | the one list, filterable, layer-tagged | Copy selected rows |
| **Opportunity** | GO / TEST / SKIP with the evidence behind each | Open the winning candidate |
| **Listing + A+** | assembled copy with publishability + evidence state | Copy field |
| **Workspace** | trust state, lineage, drift detail | Switch workspace |

Landing changes from `overview` to `workflow`. The existing `overview` remains reachable.

---

## 10. What "done" means — acceptance criteria

V1 is done when, on a fresh machine with the real workspace:

1. Start opens on **Workflow**, showing 13 stages with a state for each.
2. Every state is traceable to a file on disk — no state is defaulted.
3. Every metric shows source + date, or renders `—` with a reason.
4. Imported / Calculated / Suggested are visually distinct and never mixed in a row.
5. The workspace banner shows `DRIFTED` for `runs/T2` and names the stages.
6. Copy-command yields a command that runs unmodified in the owner's shell.
7. `files_written = 0`, `dns_lookups = 0`, `http_requests = 0`, `amazon_connections = 0`
   for a full browse session — asserted by test, matching the existing console counters.
8. Full suite differential vs the then-current main is baseline-equivalent on shared nodes.

---

## 11. Build order

Each step ends in something checkable.

| # | Step | Verify |
|---|---|---|
| 1 | `build_workflow_section()` — stage discovery + state derivation, backend only | unit tests over synthetic workspaces covering all 6 states |
| 2 | Wire into `build_console_model`, expose on the API | `--validate-only` prints 13 stage states, writes nothing |
| 3 | Workflow view + nav group in `app.js` | DOM render harness — the project has one from the 7.13 modal hotfix |
| 4 | Stage detail + copy-command | render test asserts the command string matches the module's real CLI |
| 5 | Workspace trust banner | test with a drifted and a clean fixture |
| 6 | Landing switch to `workflow` | launcher test |
| 7 | Differential + counters | full suite, n=2 per side, node-set comparison |

Steps 1–2 are backend and independently testable. Nothing in 1–6 requires branch A, so V1
is not blocked by A's acceptance chain. Binding the stage-state derivation to A's
`pipeline_status` module is deliberately **not** in this list — see below.

---

## 12. Relationship to branch A

A's `pipeline_status.py` derives a stage/staleness model over the same artifacts. There is
real overlap with §5.

**V1 does not depend on it and must not wait for it.** A is unmerged, its Windows gate is
pending, and its independent re-audit is open. If A is accepted later, §5's derivation
should be replaced by a call into it rather than duplicated — one authority, not two. That
is a deliberate V2 consolidation, recorded here so the duplication is a known and temporary
cost rather than an accident.

If A is accepted **before** V1 step 1 begins, do it the other way round: build §5 on A from
the start.

---

## 13. Open questions for the owner

1. ~~**Workspace disposition.**~~ **ANSWERED 2026-08-04: quarantine.** `runs/T2` is `HISTORICAL`;
   fresh workspace per real product. Folded into §7.
2. **Landing screen.** Workflow for everyone, or Workflow only until the decision queue is
   non-empty, then Overview?
3. **Stage 11.** Show `NOT_ACCEPTED` and let the owner see the planning output anyway, or
   hide the stage until it is accepted? This spec assumes the former — hiding a stage of a
   13-stage pipeline is its own kind of lie.
