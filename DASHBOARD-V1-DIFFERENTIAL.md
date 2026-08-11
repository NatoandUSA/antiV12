# Dashboard V1 — Formal Differential

DASHBOARD-V1-SPEC.md Step 7. Compares real local `main` against the final F1 feature head, both
read directly from git, not from any prior handoff's claims.

## Commit range

`main @ d5cee459792fdf0ac4badf7e13554fb40d874abf` (unchanged throughout F0/F1 — never touched)
vs `feature-dashboard-workflow-view @ 373cfff` (final F1 head), 6 commits ahead:

```
234c5a9  feat(workflow-view): stage-state derivation engine (step 1)
b90f4a1  feat(workflow-view): the authoritative 13-stage table (step 2A/2B)
70decf2  feat(workflow-view): wire the 13-stage table into the accepted console (step 2C)
9857474  feat(workflow-view): the Workflow view, a thin renderer of backend state (step 2D)
1d3702b  fix(workflow-view): UX review finding -- Stage 1/7 notes leaked developer prose to staff
373cfff  feat(workflow-view): workspace trust banner + Workflow as default landing (steps 5-6)
```

Not included in this branch (deliberately deferred): step 6's alternative ("Workflow only until
the decision queue is non-empty") was not built — Section 13 Q2 was answered "Workflow for
everyone." No PPC 7.1E work. No run-from-UI. `main` itself is unmodified.

## Changed files (full branch diff, main → F1 head)

```
DASHBOARD-V1-SPEC.md                                       | 281 ++++++++ (new file)
production/phase7_unified_owner_console.py                 | 178 ++++-
production/phase7_unified_owner_console_static/app.js      | 142 ++++-
production/phase7_unified_owner_console_static/icons.svg   |   3 +
production/phase7_unified_owner_console_static/index.html  |   3 +
production/phase7_workflow_stage_model.py                  | 483 ++++ (new file)
tests/phase7_14_console_dom_harness.js                     | 162 ++++-
tests/test_phase7_13_unified_owner_console.py               | 252 ++++-
tests/test_phase7_14_owner_usability_pilot_readiness.py     |  81 ++-
tests/test_phase7_workflow_stage_model.py                  | 532 ++++ (new file)
10 files changed, 2104 insertions(+), 13 deletions(-)
```

## Changed production contracts

- **New module** `production/phase7_workflow_stage_model.py` — pure derivation engine (6 states:
  NOT_STARTED/READY/STALE/BLOCKED/UNKNOWN/NOT_ACCEPTED) over the real 13-stage pipeline, read from
  each producer script's own source, not inferred from the spec's group ordering. Stages 1 and 7
  are deliberately not modeled (no persisted artifact / no current producer — see the module's own
  comment block for the direct-source evidence).
- **`production/phase7_unified_owner_console.py`**: additive only.
  - `build_workflow_section()` — new top-level `"workflow"` model section (stages, counts,
    `primary_next_stage_id`, `trust`).
  - `_workspace_trust_state()` — new (Step 5): TRUSTED / UNVERIFIED / HISTORICAL for the product
    workspace, reusing the accepted Phase 6F authority (`seller_central_package.
    verify_phase6f_artifacts`) rather than a second lineage mechanism. `QUARANTINED_PRODUCT_ROOTS`
    (currently `{"T2"}`) is the one new, deliberately-explicit source of truth — the real
    `runs/T2` 6A–6D drift is permanently undetectable by any live check (accepted bytes never
    committed, `runs/` gitignored, no backup), so a live re-verification can only prove internal
    self-consistency of files that may already have been wrong when the package was built.
  - `_repo_tags` / `_common_git_dir` — new, worktree-safe git tag reader (file-based, never a
    subprocess).
  - `MODULE_OWNER_LABELS`, `SOURCE_AUTHORITIES`, `_READ_ENDPOINTS` gain one `"workflow"` entry
    each. **`ACTIONS` is unchanged (still exactly 15 entries — verified directly, not assumed).**
  - `GET /api/v1/workflow` — new read-only endpoint, routed through the same session gate
    (`_ensure_session`) as every other read endpoint; no special-casing.
- **Frontend** (`app.js` / `icons.svg` / `index.html`): new `WORKFLOW` nav group (above
  `INTELLIGENCE`, as DASHBOARD-V1-SPEC.md §3 specifies), `renderWorkflow()` as a thin renderer
  (reads `stage.state` / `wf.primary_next_stage_id` / `wf.trust.state` verbatim, computes no
  readiness itself), copy-command reuses the pre-existing `copyValue()` helper, and (Step 6)
  `route()`'s no-hash fallback moved from `#overview` to `#workflow`.

## Test additions / updates

- `tests/test_phase7_workflow_stage_model.py` — 40 tests, the derivation engine in isolation.
- `tests/test_phase7_13_unified_owner_console.py` — `WorkflowSection` (11 tests, section wiring),
  `RepoTagsFileRead` (4 tests, incl. a real-`.git` sanity check), `WorkspaceTrust` (8 tests, Step
  5: hermetic synthetic Phase 6F package fixtures for TRUSTED / UNVERIFIED / drifted-UNVERIFIED /
  HISTORICAL-overrides-a-verifying-package / HISTORICAL-with-no-package, plus the console-model
  and JSON-serialization wiring). None of the trust fixtures touch the real, gitignored `runs/T2`.
- `tests/phase7_14_console_dom_harness.js` — checks 194–217: stage rendering, all six states,
  not-tracked-stage handling, composite component disclosure, empty state, nav reachability, the
  trust banner (renders backend state/reason, absent when the backend sends none), and (Step 6)
  default-landing-is-Workflow with Overview still reachable.

## Security / write-authority statement

- **No new write authority.** `ACTIONS` allowlist unchanged at 15 entries (`test_136c`, and
  independently reconfirmed by direct grep this session). No `subprocess` call added anywhere in
  the console module (grepped; only pre-existing docstring text asserting the zero-subprocess
  discipline).
- **No new execution path.** `command` fields on stage rows are inert strings, copy-to-clipboard
  only, via the pre-existing `copy_command` pattern — never executed client- or server-side.
- **No Seller Central integration.** Nothing in this branch touches a seller session, seller API,
  advertising API, or seller browser automation.
- `GET /api/v1/workflow` sits behind the same session/CSRF machinery as every other read endpoint
  — confirmed by reading `_handle_get` → `_ensure_session` → `_api_get`, no bypass.

## State-model / API / frontend changes

- New model field: `sections.workflow` (stages, counts, `primary_next_stage_id`, `trust`).
- New API endpoint: `GET /api/v1/workflow`.
- New frontend view: Workflow (nav id `workflow`), now the default landing view. Overview
  unchanged and fully reachable via nav and `#overview`.

## Known baseline failures / new failures

Full suite, run in a disposable worktree at the final F1 head (`373cfff`), never the primary
checkout:

```
Ran 4878 tests in 534.448s
FAILED (failures=6, errors=14, skipped=329)
```

4878 = the F0-verified baseline of 4870 plus this branch's 8 new `WorkspaceTrust` tests. The 6
failures + 14 errors are the **identical named set** verified at F0 (`test_199e_no_acceptance_
tag_yet` and the environment-only categories: missing `runs/T2` outside the original checkout,
a live dashboard process that can't stay healthy in a sandboxed shell, pip install-mode detection
tied to the original checkout) — confirmed by diffing the failing test names, not just the counts.

**New failures attributable to this branch: 0.**

## Trust-banner behavior

Every screen this branch touches (Workflow only — the one screen connected to the product
workspace concept) shows a banner with exactly one of TRUSTED / UNVERIFIED / HISTORICAL, backend-
computed, plus a plain-language reason. No banner renders when there is no product workspace
(`trust: null`) — no silent fallback to a default state. Verified directly against the real,
gitignored `runs/T2` this session: its promoted Phase 6F package currently fails live
verification for an unrelated, real, already-known 6E-manifest source-hash mismatch — independent
confirmation that the `HISTORICAL` override and the live `TRUSTED`/`UNVERIFIED` check are two
genuinely different signals, not a redundant pair.

## Landing-page behavior

DASHBOARD-V1-SPEC.md §13 open question 2, now answered: Workflow is the default landing view for
everyone. Overview is unchanged and remains fully reachable via nav and its own `#overview` route
— only the no-hash entry point moved. This was implemented on the strength of the F1 authorization
JSON's explicit `product_decision` field; it is flagged here because DASHBOARD-V1-SPEC.md itself
still marked this question open (no `ANSWERED` strikethrough, unlike question 1) before this
branch — now updated in this same commit.

## Rollback point

`main @ d5cee45` is untouched; this entire branch (`234c5a9`..`373cfff`) is unmerged and unpushed
beyond its already-known pushed state at `1d3702b`. Reverting is `git branch -f
feature-dashboard-workflow-view 1d3702b` (or simply not merging) — no main-branch or pushed-origin
state depends on this differential's two new commits.
