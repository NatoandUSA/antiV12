#!/usr/bin/env python3
"""production.phase7_workflow_stage_model -- stage-state derivation for the Dashboard V1 Workflow view.

Six states, derived from artifacts on disk, never invented, never defaulted to a happy value --
DASHBOARD-V1-SPEC.md Section 5. This module owns the derivation ENGINE only: given a StageSpec
(what a stage writes, what its freshness depends on, whether its code carries an acceptance tag)
and a workspace directory, compute one state. production.phase7_unified_owner_console wires this
into the console's read model (build_workflow_section) -- kept separate so the derivation logic is
unit-testable against synthetic workspaces without touching the console's Config/audit/cache
machinery, the same separation build_analysis_section already has from OPS.build_operations_model.

This is NOT production.pipeline_status (Branch A -- unmerged, HOLD pending independent re-audit).
DASHBOARD-V1-SPEC.md Section 12 is explicit: V1 must not wait for or depend on Branch A. If Branch A
is ever accepted, this derivation should be replaced by a call into it -- a disclosed, deliberate
duplication for now, not an accident.
"""
import glob
import hashlib
import json
import os

NOT_STARTED = "NOT_STARTED"
READY = "READY"
STALE = "STALE"
BLOCKED = "BLOCKED"
UNKNOWN = "UNKNOWN"
NOT_ACCEPTED = "NOT_ACCEPTED"

STAGE_STATES = (NOT_STARTED, READY, STALE, BLOCKED, UNKNOWN, NOT_ACCEPTED)

# The ONE precedence order, used two ways: as the check order for a single stage (each step
# short-circuits the next -- see derive_stage_state) AND as the severity ranking a composite
# rollup uses to pick its worst component (see _rollup). Two separate hand-maintained orderings
# would drift; this is the single source either mechanism reads.
_PRECEDENCE = (NOT_STARTED, UNKNOWN, BLOCKED, STALE, NOT_ACCEPTED, READY)
_SEVERITY = {state: rank for rank, state in enumerate(_PRECEDENCE)}

GROUP_RESEARCH = "Research"
GROUP_DECIDE = "Decide"
GROUP_BUILD = "Build"
GROUP_LAUNCH = "Launch"
STAGE_GROUPS = (GROUP_RESEARCH, GROUP_DECIDE, GROUP_BUILD, GROUP_LAUNCH)


class StageComponent:
    """One required piece of evidence inside a COMPOSITE stage (e.g. stage 9's listing text vs.
    its A+ content) -- real 13-stage investigation found stages whose "one artifact" is actually
    two independent scripts' outputs. A component is not a stage in its own right: it has no
    stage_id and does not sit in the blocking graph -- it inherits its parent StageSpec's
    blocking_stage_ids and tags wholesale. What it DOES get is its own output/freshness/acceptance
    evaluation, so partial progress ("listing ready, A+ not started") stays visible in the detail
    view instead of collapsing into one opaque flag on the parent.
    """
    __slots__ = ("name", "output_paths", "freshness_globs", "accepted_tag_prefix")

    def __init__(self, name, output_paths, *, freshness_globs=(), accepted_tag_prefix=None):
        self.name = name
        self.output_paths = tuple(output_paths)
        self.freshness_globs = tuple(freshness_globs)
        self.accepted_tag_prefix = accepted_tag_prefix


class StageSpec:
    """One pipeline stage's identity and dependency contract. Nothing here executes anything --
    it is a description the deriver reads. A stage is EITHER a plain stage (output_paths and/or
    existence_globs set, components empty) OR a composite (components set, output_paths and
    existence_globs empty) -- never both; callers should not mix the two shapes on one spec.

    output_paths: workspace-relative FIXED paths this stage writes. ALL must exist and be readable
    for the stage to be considered started; staleness compares against the OLDEST of their mtimes,
    so a partially-rewritten output cannot look fresher than its slowest file.

    existence_globs: workspace-relative glob patterns for a stage whose artifact has no fixed
    filename -- a raw H10/Xray/Cerebro export the owner drops in with whatever name Amazon/H10
    gave it. The stage is "started" once AT LEAST ONE file matches ANY pattern (unlike
    output_paths, which requires ALL fixed paths). Mutually exclusive with output_paths.

    components: for a COMPOSITE stage, the required StageComponents. When set, output_paths and
    existence_globs are ignored and the stage's own state is the rollup of its components' states
    (worst wins, via the shared _PRECEDENCE order) -- see resolve_all / build_stage_result.

    blocking_stage_ids: upstream stage_ids that must already be READY, or this stage is BLOCKED.
    Callers must resolve stages in dependency order (see resolve_all) so an upstream state is
    always available before a downstream stage asks for it.

    freshness_globs: workspace-relative glob patterns whose newest matching mtime is compared
    against this stage's own oldest output mtime. Deliberately separate from blocking_stage_ids:
    a stage's true freshness input is often a raw import file (e.g. "US_AMAZON_cerebro_*.xlsx"),
    not only the immediately upstream stage's own artifact.

    accepted_tag_prefix: if set, a git tag "<prefix>-accepted-*" must exist for this stage to ever
    report READY; otherwise, once its artifacts exist and would otherwise qualify, it reports
    NOT_ACCEPTED instead of a false READY.

    input_hint: optional human-friendly description of manual input files / owner-provided facts,
    used when inputs cannot be purely inferred from upstream stage output artifacts.
    """
    __slots__ = ("stage_id", "name", "group", "output_paths", "existence_globs", "components",
                "blocking_stage_ids", "freshness_globs", "accepted_tag_prefix", "command",
                "input_hint")

    def __init__(self, stage_id, name, group, output_paths=(), *, existence_globs=(),
                components=(), blocking_stage_ids=(), freshness_globs=(),
                accepted_tag_prefix=None, command=None, input_hint=None):
        self.stage_id = stage_id
        self.name = name
        self.group = group
        self.output_paths = tuple(output_paths)
        self.existence_globs = tuple(existence_globs)
        self.components = tuple(components)
        self.blocking_stage_ids = tuple(blocking_stage_ids)
        self.freshness_globs = tuple(freshness_globs)
        self.accepted_tag_prefix = accepted_tag_prefix
        self.command = command
        self.input_hint = input_hint


def stage_outputs(spec):
    """The authoritative output paths for a stage, derived directly from its existing definition.

    Plain stages return their output_paths (or existence_globs). Composite stages return the union
    of all their components' output_paths in declaration order. No duplicate label authority."""
    if spec.components:
        out = []
        for comp in spec.components:
            out.extend(comp.output_paths)
        return tuple(out)
    if spec.output_paths:
        return tuple(spec.output_paths)
    if spec.existence_globs:
        return tuple(spec.existence_globs)
    return ()


def stage_input_display(spec, spec_by_id=None):
    """Derive a human-friendly input summary for Staff from the spec's upstream dependencies or explicit input_hint."""
    if spec.input_hint:
        return spec.input_hint
    if spec.blocking_stage_ids:
        parts = []
        spec_map = spec_by_id or {}
        for up_id in spec.blocking_stage_ids:
            up = spec_map.get(up_id)
            if up:
                outs = stage_outputs(up)
                out_names = [os.path.basename(p) for p in outs if not p.startswith("*")]
                if out_names:
                    parts.append(", ".join(out_names))
                else:
                    parts.append(f"Stage {up_id} ({up.name})")
            else:
                parts.append(f"Stage {up_id}")
        if spec.stage_id == 8:
            parts.append("Product Truth")
        return " + ".join(parts)
    return None


def _existing_paths(workspace_dir, rel_paths):
    out = []
    for rel in rel_paths:
        p = os.path.join(workspace_dir, rel)
        if os.path.isfile(p):
            out.append(p)
    return out


def _matching_existence_paths(workspace_dir, patterns):
    """Every file matching ANY of `patterns` -- for a stage with no fixed filename (a raw H10
    export named however Amazon/H10 gave it). Unlike _existing_paths (which requires ALL of a
    fixed list), this is existence-by-shape: one match is enough for the stage to be "started",
    and every match's own readability/freshness is still checked exactly like a fixed artifact."""
    out = []
    for pattern in patterns:
        for p in glob.glob(os.path.join(workspace_dir, pattern)):
            if os.path.isfile(p):
                out.append(p)
    return out


def _oldest_mtime(paths):
    """The OLDEST mtime among paths -- a stage is only as fresh as its slowest-written output.
    None if any path's mtime cannot be read (caller must already know the path exists)."""
    ts = []
    for p in paths:
        try:
            ts.append(os.path.getmtime(p))
        except OSError:
            return None
    return min(ts) if ts else None


def _newest_glob_mtime(workspace_dir, patterns):
    newest = None
    for pattern in patterns:
        for p in glob.glob(os.path.join(workspace_dir, pattern)):
            if not os.path.isfile(p):
                continue
            try:
                ts = os.path.getmtime(p)
            except OSError:
                continue
            if newest is None or ts > newest:
                newest = ts
    return newest


def _is_readable(paths):
    """A file that exists but cannot be opened, or (for a .json artifact) cannot be parsed, is
    UNKNOWN -- never silently NOT_STARTED. 'I could not read it' and 'it is not there' must stay
    distinguishable (DASHBOARD-V1-SPEC Section 5). Non-JSON artifacts (e.g. the .md creative /
    Seller Central briefs) get a raw-readability check only -- this module has no way to validate
    prose content, and a workflow overview should not need to."""
    for p in paths:
        try:
            with open(p, "rb") as f:
                raw = f.read()
        except OSError:
            return False
        if p.lower().endswith(".json"):
            try:
                json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return False
    return True


def has_accepted_tag(prefix, *, tags):
    """Whether an '<prefix>-accepted-*' tag exists. `tags` is the caller's own `git tag --list`
    output (or a synthetic list in tests) -- this function never shells out itself, so it stays
    trivially testable and the console controls when (or whether) it pays for a git call."""
    needle = prefix + "-accepted-"
    return any(t.startswith(needle) for t in (tags or ()))


def _evidence_state(*, output_paths, existence_globs, freshness_globs, accepted_tag_prefix,
                    workspace_dir, blocking_stage_ids, stage_states_so_far, spec_by_id, tags):
    """The shared core precedence chain -- the ONE place it is written, so a single stage and a
    composite's own component both go through identical logic instead of two hand-maintained
    copies that could quietly drift apart.

    Precedence, each step short-circuiting the rest:
      1. NOT_STARTED  -- an expected output is simply absent (or, for existence_globs, nothing
                          matches). Nothing else is knowable yet.
      2. UNKNOWN       -- an output exists but cannot be read/parsed. Distinct from #1 on purpose.
      3. BLOCKED       -- a prerequisite stage is not READY. This evidence's own freshness is moot
                          until the prerequisite is.
      4. STALE         -- a freshness input (an upstream stage's artifact, or a raw import glob)
                          is newer than this evidence's own oldest output.
      5. NOT_ACCEPTED  -- everything above passed, but the owning code carries no acceptance tag.
      6. READY         -- everything checked out.

    output_paths and existence_globs are mutually exclusive per the StageSpec/StageComponent
    contract; if both are empty there is no evidence to check at all, which reports NOT_STARTED
    rather than falling through to the mtime checks below with an empty file list (that path
    returns UNKNOWN for a different reason -- "0 files have no oldest mtime" -- so it must never
    be reached with zero evidence defined in the first place).
    """
    if existence_globs:
        existing = _matching_existence_paths(workspace_dir, existence_globs)
        if not existing:
            return NOT_STARTED
    elif output_paths:
        existing = _existing_paths(workspace_dir, output_paths)
        if len(existing) < len(output_paths):
            return NOT_STARTED
    else:
        return NOT_STARTED

    if not _is_readable(existing):
        return UNKNOWN
    for up_id in blocking_stage_ids:
        if stage_states_so_far.get(up_id) != READY:
            return BLOCKED
    own_oldest = _oldest_mtime(existing)
    if own_oldest is None:
        return UNKNOWN

    freshness_newest = _newest_glob_mtime(workspace_dir, freshness_globs)
    for up_id in blocking_stage_ids:
        up_spec = spec_by_id.get(up_id)
        if up_spec is None:
            continue
        up_existing = (_matching_existence_paths(workspace_dir, up_spec.existence_globs)
                      if up_spec.existence_globs else _existing_paths(workspace_dir, up_spec.output_paths))
        for p in up_existing:
            try:
                ts = os.path.getmtime(p)
            except OSError:
                continue
            if freshness_newest is None or ts > freshness_newest:
                freshness_newest = ts
    if freshness_newest is not None and freshness_newest > own_oldest:
        return STALE

    if accepted_tag_prefix and not has_accepted_tag(accepted_tag_prefix, tags=tags):
        return NOT_ACCEPTED
    return READY


def derive_stage_state(spec, workspace_dir, stage_states_so_far, spec_by_id, *, tags=None):
    """One PLAIN stage's state (output_paths or existence_globs -- not a composite; see
    derive_composite_state for that). `stage_states_so_far` is {stage_id: state} for every stage
    already resolved this pass; `spec_by_id` is {stage_id: StageSpec} for the whole table. Both are
    supplied by resolve_all in dependency order -- this function does no ordering of its own."""
    return _evidence_state(
        output_paths=spec.output_paths, existence_globs=spec.existence_globs,
        freshness_globs=spec.freshness_globs, accepted_tag_prefix=spec.accepted_tag_prefix,
        workspace_dir=workspace_dir, blocking_stage_ids=spec.blocking_stage_ids,
        stage_states_so_far=stage_states_so_far, spec_by_id=spec_by_id, tags=tags)


def derive_composite_state(spec, workspace_dir, stage_states_so_far, spec_by_id, *, tags=None):
    """A COMPOSITE stage's rolled-up state plus its per-component detail.

    Each component is evaluated through the exact same _evidence_state core as a plain stage,
    inheriting the PARENT spec's blocking_stage_ids (a component does not have independent
    upstream dependencies from the stage it belongs to). The rollup picks the component with the
    WORST state by the shared _PRECEDENCE order -- READY only when every required component is
    READY; NOT_STARTED still wins over a sibling that IS ready, so "listing ready, A+ not started"
    correctly reports the stage as NOT_STARTED overall rather than silently looking done.

    Returns (overall_state, {component_name: component_state}).
    """
    component_states = {}
    for comp in spec.components:
        component_states[comp.name] = _evidence_state(
            output_paths=comp.output_paths, existence_globs=(),
            freshness_globs=comp.freshness_globs, accepted_tag_prefix=comp.accepted_tag_prefix,
            workspace_dir=workspace_dir, blocking_stage_ids=spec.blocking_stage_ids,
            stage_states_so_far=stage_states_so_far, spec_by_id=spec_by_id, tags=tags)
    overall = max(component_states.values(), key=lambda s: -_SEVERITY[s]) if component_states \
        else NOT_STARTED
    # "worst wins": lowest _SEVERITY rank is the least-ready state; the line above picks it via
    # a negated max rather than min() so a component_states with mixed types never surprises a
    # future editor reaching for the obvious min(..., key=_SEVERITY.get) -- both are equivalent
    # here, this spells out the intent (worst, not lowest-alphabetically) at the call site.
    return overall, component_states


def resolve_all(stage_table, workspace_dir, *, tags=None, state_overrides=None):
    """Resolve every stage in `stage_table`, in the order given, returning
    {stage_id: {"state": ..., "components": {name: state, ...}}} -- "components" is empty for a
    plain stage. Callers must supply stages in dependency order: every stage_id named in a later
    stage's blocking_stage_ids must already have appeared earlier in the list. The authoritative
    13-stage table satisfies this by construction (see workflow_stage_table).

    state_overrides: optional {stage_id: forced_state}, for a real prerequisite this table has no
    stage_id for (Product Truth blocking Stage 8 -- see build_workflow_section). The override is
    applied to that stage's OWN result BEFORE the next stage in the table is resolved, so every
    later stage's blocking_stage_ids check sees the forced value through the exact same
    states_only mechanism a genuinely-computed BLOCKED already uses -- the propagation to Stage 9
    and beyond is automatic, not something each caller has to hand-roll. Applying an override
    AFTER this loop returns would be too late: every later stage would already have resolved
    against the un-overridden value."""
    spec_by_id = {s.stage_id: s for s in stage_table}
    overrides = state_overrides or {}
    results = {}
    for spec in stage_table:
        states_only = {sid: r["state"] for sid, r in results.items()}
        if spec.components:
            state, components = derive_composite_state(spec, workspace_dir, states_only,
                                                        spec_by_id, tags=tags)
        else:
            state = derive_stage_state(spec, workspace_dir, states_only, spec_by_id, tags=tags)
            components = {}
        if spec.stage_id in overrides:
            state = overrides[spec.stage_id]
        results[spec.stage_id] = {"state": state, "components": components}
    return results


# ================================================================ the authoritative 13-stage table
#
# Every path, dependency and acceptance gate below was read directly from the producer script's own
# source (argument parsing, FILENAME/F_ constants, and -- for dependencies -- the actual verification
# call a downstream script makes, not the DASHBOARD-V1-SPEC.md group ordering alone). Two real traps
# this caught: research/seed_expand.py LOOKS like Stage 1 (it reads a "master-keywords.xlsx") but
# belongs to research/phaseA_master.py, an older, disconnected H10-xlsx pipeline -- mapping it in
# would have been exactly the invented state this whole design exists to prevent. And
# research/demand_score.py genuinely does GO/TEST/SKIP opportunity scoring, but it is called only
# from pipeline.py, a separate "v2" gate orchestrator that predates the Phase 6/7 system; the
# CURRENT wiring has Stage 8 read Stage 6's output directly, with no scoring gate in between.
#
# Stage 1 (Seed keyword) and Stage 7 (Score + select opportunity) are therefore NOT in this
# resolvable table:
#   Stage 1 has no artifact of its own -- research/target_profile.py turns the owner's --seed
#     string into an in-memory profile that research/asin_batches.py (Stage 3) embeds in its own
#     output. Forcing a 6-state read on "nothing persisted" would be exactly the invented state
#     the derivation engine exists to refuse.
#   Stage 7 has no CURRENT persisted producer at all (see the demand_score.py finding above) --
#     the current pipeline goes straight from Stage 6 to Stage 8. NOT_IMPLEMENTED_IN_CURRENT_PIPELINE
#     is a presentation-only fact for the console/frontend to show; it does not belong in the
#     6-state model.
#
# Two further disclosed gaps, found by checking the real code rather than assuming from group
# order: production/phase7_extended_launch_planning.py (Stage 11) has NO source-code dependency on
# the Stage 9/10 listing/creative artifacts -- its real gate is the accepted Phase 7.1M economics
# foundation plus a confirmed owner policy, and NEITHER is one of these 13 numbered stages. Stage 11
# is therefore modelled with no blocking_stage_ids here; a real prerequisite exists in the code, it
# is just outside what DASHBOARD-V1-SPEC.md's 13-stage numbering represents.
# "note" is what a STAFF MEMBER reads in the Workflow view -- it must never be the developer
# rationale above. An earlier draft put the raw finding (file names, module names, "NOT_
# IMPLEMENTED_IN_CURRENT_PIPELINE") directly in "note" and it would have shipped verbatim to the
# owner; caught in review by generating a real build_workflow_section() response and reading what
# it actually said. The technical WHY stays in the comment block above, where a maintainer reads
# it; "note" carries only what a non-technical reader needs.
STAGE_1_SEED_KEYWORD = {
    "stage_id": 1, "name": "Seed keyword", "group": GROUP_RESEARCH,
    "note": "Captured as part of Stage 3 (ASIN batches) -- no separate action needed here.",
}
STAGE_7_OPPORTUNITY = {
    "stage_id": 7, "name": "Score + select opportunity", "group": GROUP_DECIDE,
    "note": "Opportunity review -- not separately tracked.",
}
NOT_TRACKED_STAGES = (STAGE_1_SEED_KEYWORD, STAGE_7_OPPORTUNITY)


def workflow_stage_table():
    """The 11 resolvable stages (of 13 -- see NOT_TRACKED_STAGES for 1 and 7), in dependency order."""
    return [
        WSM_STAGE_2, WSM_STAGE_3, WSM_STAGE_4, WSM_STAGE_5, WSM_STAGE_6,
        WSM_STAGE_8, WSM_STAGE_9, WSM_STAGE_10, WSM_STAGE_11, WSM_STAGE_12, WSM_STAGE_13,
    ]


# research/asin_batches.py: "Xray folder -> ... -> ASIN-BATCHES.json". Reads the Xray folder
# directly -- no fixed filename, since Amazon/H10 names the export.
# LIMITATION, disclosed rather than hidden: the real classifier (research/export_detector.py) reads
# file HEADERS, not filenames -- "Header inspection only", by its own docstring. These
# existence_globs are a filename heuristic for the dashboard's presence signal ONLY; they do not
# replace, and are not as accurate as, the real header-based classification the producer scripts
# themselves run when actually invoked.
WSM_STAGE_2 = StageSpec(
    2, "Import Amazon + Xray", GROUP_RESEARCH,
    existence_globs=("*Xray*.xlsx", "*Xray*.xls", "*Xray*.csv", "*xray*.xlsx", "*xray*.csv"),
    command="(place the Helium 10 Xray export in the workspace folder)",
    input_hint="Helium 10 Xray export (*.xlsx / *.csv)")

# research/asin_batches.py main(): writes BATCHES = "ASIN-BATCHES.json" under the workspace.
WSM_STAGE_3 = StageSpec(
    3, "ASIN batches", GROUP_RESEARCH, ("ASIN-BATCHES.json",),
    blocking_stage_ids=(2,),
    command="python research/asin_batches.py <folder> --seed \"<seed keyword>\"",
    input_hint="Stage 2 (Xray export) + seed keyword")

# research/master_keyword_builder.py: "Cerebro exports (+ approved ASIN-BATCHES.json) -> evidence
# matrix". research/cerebro_export_detector.py (imported, not standalone) classifies these by
# header shape, not filename -- same disclosed heuristic-vs-real-classifier gap as Stage 2 above.
WSM_STAGE_4 = StageSpec(
    4, "Cerebro re-import", GROUP_RESEARCH,
    existence_globs=("*erebro*.xlsx", "*erebro*.csv", "*agnet*.xlsx", "*agnet*.csv"),
    command="(place fresh Cerebro/Magnet exports in the workspace folder)",
    input_hint="Cerebro / Magnet export (*.xlsx / *.csv)")

# research/master_keyword_builder.py main(): writes CEREBRO-EVIDENCE-MATRIX.json, THEN
# MASTER-KEYWORDS-LEAN.json, in the same invocation -- stages 5 and 6 share one producer.
WSM_STAGE_5 = StageSpec(
    5, "Clean / merge / analyze", GROUP_RESEARCH, ("CEREBRO-EVIDENCE-MATRIX.json",),
    blocking_stage_ids=(3, 4),
    command="python research/master_keyword_builder.py <folder>")

WSM_STAGE_6 = StageSpec(
    6, "Master Keyword List", GROUP_DECIDE, ("MASTER-KEYWORDS-LEAN.json",),
    blocking_stage_ids=(3, 4),   # same script/inputs as stage 5, not sequential on it
    command="python research/master_keyword_builder.py <folder>")

# listing/keyword_allocation_planner.py: reads MASTER-KEYWORDS-LEAN.json via
# keyword_source_adapter.load_keyword_source -- directly, no gate in between (confirmed: no
# reference to demand_score.py, DEMAND-SCORECARD.md, or any GO/TEST/SKIP concept in this file).
WSM_STAGE_8 = StageSpec(
    8, "Keywords -> listing", GROUP_BUILD, ("phase6/6B/KEYWORD-ALLOCATION-MAP.json",),
    blocking_stage_ids=(6,),
    command="python -m listing.keyword_allocation_planner <run_dir>",
    input_hint="MASTER-KEYWORDS-LEAN.json + Product Truth")

# production/product_detail_page.py (6C) reads phase6/6B/KEYWORD-ALLOCATION-MAP.json directly
# (KAP.phase6b_dir / "KEYWORD-ALLOCATION-MAP.json", confirmed by source). production/aplus_assembly.py
# (6D) is the sibling A+ producer for the same stage.
# F5 finding, confirmed by running the exact command that used to be here: both CLIs default to a
# dry-run print-only mode -- --write is REQUIRED or PRODUCT-DETAIL-PAGE.json / BASIC-APLUS-CONTENT.json
# are never written and the stage silently stays NOT_STARTED forever, with no error to explain why.
WSM_STAGE_9 = StageSpec(
    9, "Listing + A+", GROUP_BUILD,
    components=(
        StageComponent("listing", ("phase6/6C/PRODUCT-DETAIL-PAGE.json",)),
        StageComponent("aplus", ("phase6/6D/BASIC-APLUS-CONTENT.json",)),
    ),
    blocking_stage_ids=(8,),
    command="python -m production.product_detail_page <run_dir> --write"
           "  # then: python -m production.aplus_assembly <run_dir> --write",
    input_hint="KEYWORD-ALLOCATION-MAP.json + Product Facts")

# creative/creative_production_package.py's load_phase6e_dependencies() HARD-verifies both the 6C
# and 6D output directories exist and pass verify_phase6c_artifacts / verify_phase6d_artifacts,
# raising Phase6EDependencyError otherwise -- confirmed by reading the function body, not inferred.
# F5 finding: same --write requirement as Stage 9 (confirmed by reading main() -- artifacts are
# gated behind action="store_true" --write; the bare command silently never writes anything).
WSM_STAGE_10 = StageSpec(
    10, "Photo / A+ prompts", GROUP_BUILD,
    components=(
        StageComponent("listing_image_prompts", ("phase6/6E/LISTING-IMAGE-PROMPTS.md",)),
        StageComponent("aplus_image_prompts", ("phase6/6E/APLUS-IMAGE-PROMPTS.md",)),
        StageComponent("creative_brief", ("phase6/6E/CREATIVE-BRIEF.md",)),
    ),
    blocking_stage_ids=(9,),
    command="python -m creative.creative_production_package <run_dir> --write",
    input_hint="Verified 6C (PDP) + 6D (A+) artifacts")

# production/phase7_extended_launch_planning.py: NO source-code reference to phase6c_dir /
# phase6d_dir / phase6e_dir was found (checked directly) -- its real gate is the accepted Phase
# 7.1M economics foundation + a confirmed owner policy, neither of which is one of these 13
# numbered stages. Left with no blocking_stage_ids here on purpose: a real prerequisite exists in
# the code, it is just outside what this 13-stage table represents -- a disclosed gap, not an
# assumption that none exists. accepted_tag_prefix reflects that 7.1E currently has only a
# checkpoint tag (phase7-1e-checkpoint-*), never an -accepted- one, per git tag --list.
# Path: assemble_and_write_phase7_1e() writes to <run_dir>/phase7/7.1E/candidate/, verifies, then
# PROMOTES to .../final/ (confirmed by reading the function body) -- the candidate dir is a
# staging area, not evidence; only the promoted final/ counts.
# F5 finding, confirmed by actually running this exact string: --run-dir is a REQUIRED FLAG on this
# CLI, not a bare positional -- the command as it stood only printed argparse usage and did nothing.
WSM_STAGE_11 = StageSpec(
    11, "PPC export", GROUP_LAUNCH,
    components=(
        StageComponent("readiness", ("phase7/7.1E/final/PHASE7-1E-READINESS.json",),
                       accepted_tag_prefix="phase7-1e"),
        StageComponent("entry_guide", ("phase7/7.1E/final/MANUAL-ENTRY-GUIDE.md",)),
    ),
    command="python -m production.phase7_extended_launch_planning --run-dir <run_dir>",
    input_hint="Confirmed Phase 7.1M Economics + Owner Policy")

# production/phase7_report_ingestion.py: "owner-EXPORTED Amazon Ads report files ... placed in a
# local inbox" -- the raw import itself has no fixed name (owner-driven, like stages 2/4), but the
# F_READINESS artifact it writes after validating/normalizing is fixed and is the real "is this
# stage done" signal, matching this codebase's own MANIFEST/READINESS rollup convention elsewhere.
# Path: workspace_dirs()'s _WORKSPACE_SUBDIRS includes "final" (promote_candidate writes there,
# confirmed by reading run_ingestion's promotion call) -- same candidate/final/last_valid shape as
# 7.1E, just under <run_dir>/phase7/7.2/.
# F5 finding, confirmed by running the exact string: this CLI takes --base-dir (required), not a
# positional run_dir at all -- and it wants the phase7/7.2 workspace itself, one level below
# <run_dir>.
WSM_STAGE_12 = StageSpec(
    12, "Import PPC reports", GROUP_LAUNCH,
    ("phase7/7.2/final/PHASE7-REPORT-ANALYSIS-READINESS.json",),
    command="python -m production.phase7_report_ingestion --base-dir <run_dir>/phase7/7.2",
    input_hint="Amazon Ads Search Term report placed in phase7/7.2")

# production/phase7_ads_analysis.py: hard-coded to read ONLY the promoted Phase 7.2 "final/"
# directory, raising AdsAnalysisError on any other source (confirmed by source) -- the strongest-
# evidenced dependency in this whole table. Its OWN outputs are written under its OWN "promoted/"
# subdir (D_PROMOTED = "promoted", confirmed), NOT "final/" -- 7.2 and 7.3 use different names for
# the same concept, an inconsistency worth knowing rather than silently normalizing away.
# runs/T2/phase7/7.3/promoted/owner-decision-queue.csv is DASHBOARD-V1-SPEC.md's own cited path.
# F5 finding, confirmed by running the exact string: this CLI requires --base-dir AND
# --phase7-2-dir, both flags, neither a bare positional -- the command as it stood was missing both.
WSM_STAGE_13 = StageSpec(
    13, "Analyze + suggest", GROUP_LAUNCH,
    components=(
        StageComponent("analysis", ("phase7/7.3/promoted/analysis.json",)),
        StageComponent("decision_queue", ("phase7/7.3/promoted/owner-decision-queue.csv",)),
    ),
    blocking_stage_ids=(12,),
    command="python -m production.phase7_ads_analysis --base-dir <run_dir>/phase7/7.3"
           " --phase7-2-dir <run_dir>/phase7/7.2",
    input_hint="Promoted Phase 7.2 report dataset")


# ================================================================ Product Truth (Phase 6A) --
# a TRACKED PREREQUISITE, deliberately NOT one of the 13 numbered stages. Owner decision,
# DASHBOARD-V1-SPEC.md Section 13 item 4 (2026-08-11): Phase 6A has a real, stable persisted
# artifact set -- forcing it into the informational NOT_TRACKED_STAGES pattern (like Stage 1/7,
# which have no persisted artifact at all) would misrepresent it. Renumbering the established
# 13-stage sequence was explicitly rejected -- this is a prerequisite relationship, not stage 14.
#
# Real consumer, confirmed by reading source and by actually running the command: it is Stage 8
# (listing/keyword_allocation_planner.py), NOT Stage 9 -- plan_keyword_allocation() calls
# load_phase6a_dependency() as its very first step, and an F5 follow-up investigation reproduced
# the identical unhandled Phase6ADependencyError Stage 9 was known to raise. Stage 9 only inherits
# the problem transitively through its own existing blocking_stage_ids=(8,) -- blocking Stage 8
# here is therefore both the technically correct fix point AND sufficient for Stage 9 too.
#
# State model: load_phase6a_dependency's real failure modes (confirmed by reading it directly)
# mostly map cleanly onto three of the six existing states -- missing artifact -> NOT_STARTED
# (via the shared _evidence_state core every numbered stage goes through), hash/identity mismatch
# -> UNKNOWN (via an explicit re-check below, NOT a call into production.product_workspace.
# verify_phase6a_artifacts -- that function's own REQUIRED_ARTIFACTS also demands
# PRODUCT-READINESS-REPORT.md, a file PRODUCT_TRUTH_ARTIFACTS below has never tracked; calling it
# wholesale would silently add a new existence requirement this module never disclosed, not verify
# one it already has. The check below recomputes sha256 directly -- a generic, unambiguous
# operation, not domain logic -- scoped to exactly the artifacts PRODUCT_TRUTH_ARTIFACTS already
# lists, against whichever of them the manifest's own output_hashes records).
# Either way the frontend's existing tag()/ownerState() mapping needs no new entries for them.
# One real failure mode does NOT fit any of the six: workspace.downstream_
# readiness.ready_for_6b being false. This is not a rare edge case -- it is the DEFAULT, COMMON
# state of any product whose owner facts are not all confirmed yet, which is most products right
# after their first phase6a_build.py run. Forcing it into NOT_STARTED would be dishonest (the
# files DO exist); PRODUCT_TRUTH_OWNER_INPUT_REQUIRED is a new, disclosed, deliberately adapted
# state instead, named the way this codebase already names the identical shape of gap elsewhere
# (production.phase7_minimal_launch_foundation's PHASE7_OWNER_INPUT_REQUIRED).
#
# Deliberately NOT modelled: keyword-source hash drift (load_phase6a_dependency's own STALE-
# shaped check). That check is hash-based against the specific raw keyword-source file
# listing.keyword_source_adapter resolves, which this module has no existing coupling to and no
# established glob convention for (unlike the Xray/Cerebro raw-import globs Stages 2/4 already
# use) -- inventing one would be exactly the kind of fabricated signal this whole design exists to
# refuse. A disclosed gap, not an assumed absence: PRODUCT_TRUTH_STALE_UNMODELED records it below.
PRODUCT_TRUTH_ARTIFACTS = (
    "phase6/6A/PRODUCT-WORKSPACE.json", "phase6/6A/OWNER-INPUT-REQUIRED.json",
    "phase6/6A/NORMALIZED-PRODUCT-FACTS.json", "phase6/6A/CLAIM-EVIDENCE.json",
    "phase6/6A/STAGE-6A-MANIFEST.json",
)
PRODUCT_TRUTH_WORKSPACE_FILE = "phase6/6A/PRODUCT-WORKSPACE.json"
PRODUCT_TRUTH_MANIFEST_FILE = "phase6/6A/STAGE-6A-MANIFEST.json"
PRODUCT_TRUTH_OWNER_INPUT_REQUIRED = "OWNER_INPUT_REQUIRED"
# Named for the comment above, never returned: staleness genuinely isn't checked (disclosed gap).
PRODUCT_TRUTH_STALE_UNMODELED = STALE

PRODUCT_TRUTH_LABEL = "Product Truth"
PRODUCT_TRUTH_GROUP = GROUP_BUILD
PRODUCT_TRUTH_COMMAND = "python scripts/phase6a_build.py <run_dir>"
PRODUCT_TRUTH_STAFF_NOTE = "Product Truth required before Listing + A+."
PRODUCT_TRUTH_INPUT_HINT = "MASTER-KEYWORDS-LEAN.json + Owner-confirmed product facts"


def derive_product_truth_state(product_root):
    """One of NOT_STARTED / UNKNOWN / STALE / READY / PRODUCT_TRUTH_OWNER_INPUT_REQUIRED for the
    Phase 6A ('Product Truth') prerequisite. See the module comment above this section for the
    full evidence trail behind this shape."""
    state = _evidence_state(
        output_paths=PRODUCT_TRUTH_ARTIFACTS, existence_globs=(), freshness_globs=(),
        accepted_tag_prefix=None, workspace_dir=product_root, blocking_stage_ids=(),
        stage_states_so_far={}, spec_by_id={}, tags=None)
    if state != READY:
        return state
    # Existence, readability, and (the freshness_globs=() no-op aside) the shared core all say
    # READY. Two checks that core has no way to perform, both of which load_phase6a_dependency
    # itself raises Phase6ADependencyError on -- a workspace that looks READY here must not be
    # invented-happy for either of them.
    try:
        with open(os.path.join(product_root, PRODUCT_TRUTH_WORKSPACE_FILE), "rb") as f:
            workspace = json.loads(f.read().decode("utf-8"))
        with open(os.path.join(product_root, PRODUCT_TRUTH_MANIFEST_FILE), "rb") as f:
            manifest = json.loads(f.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return UNKNOWN
    # _is_readable (inside the _evidence_state call above) only proves json.loads doesn't raise --
    # "null", "42", and "[1]" all parse without error but are not objects, and every .get() below
    # assumes one. Both must be dicts before either is trusted, same as an unparseable file: the
    # evidence is unusable, not a green light.
    if not isinstance(workspace, dict) or not isinstance(manifest, dict):
        return UNKNOWN
    #   1. artifact hash agreement -- for whichever of PRODUCT_TRUTH_ARTIFACTS the manifest's own
    #      output_hashes records (keyed workspace-relative, exactly like PRODUCT_TRUTH_ARTIFACTS
    #      itself), the recomputed sha256 of what is actually on disk must match. An artifact this
    #      module doesn't track is never required just because it might exist.
    output_hashes = manifest.get("output_hashes") or {}
    for rel in PRODUCT_TRUTH_ARTIFACTS:
        expected = output_hashes.get(rel)
        if expected is None:
            continue
        h = hashlib.sha256()
        try:
            with open(os.path.join(product_root, rel), "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
        except OSError:
            return UNKNOWN
        if h.hexdigest() != expected:
            return UNKNOWN
    #   2. identity agreement -- workspace_id == product_id, and the manifest agrees with both.
    ws_id = workspace.get("workspace_id")
    prod_id = workspace.get("product_id")
    if (not ws_id or not prod_id or ws_id != prod_id
            or manifest.get("workspace_id") != ws_id or manifest.get("product_id") != prod_id):
        return UNKNOWN
    # Deliberately NOT re-checked here: keyword-source hash drift (see the disclosed-gap comment
    # above, PRODUCT_TRUTH_STALE_UNMODELED) -- unlike the two checks above, that one requires a
    # glob convention this module still has no coupling to.
    #
    # Does 6A's OWN readiness verdict say the owner facts are actually complete? The exact field
    # load_phase6a_dependency hard-requires before Stage 8 will even start -- confirmed by reading
    # it directly, not inferred.
    ready_for_6b = bool((workspace.get("downstream_readiness") or {}).get("ready_for_6b"))
    return READY if ready_for_6b else PRODUCT_TRUTH_OWNER_INPUT_REQUIRED
