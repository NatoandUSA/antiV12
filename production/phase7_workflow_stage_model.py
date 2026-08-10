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
import json
import os

NOT_STARTED = "NOT_STARTED"
READY = "READY"
STALE = "STALE"
BLOCKED = "BLOCKED"
UNKNOWN = "UNKNOWN"
NOT_ACCEPTED = "NOT_ACCEPTED"

STAGE_STATES = (NOT_STARTED, READY, STALE, BLOCKED, UNKNOWN, NOT_ACCEPTED)

GROUP_RESEARCH = "Research"
GROUP_DECIDE = "Decide"
GROUP_BUILD = "Build"
GROUP_LAUNCH = "Launch"
STAGE_GROUPS = (GROUP_RESEARCH, GROUP_DECIDE, GROUP_BUILD, GROUP_LAUNCH)


class StageSpec:
    """One pipeline stage's identity and dependency contract. Nothing here executes anything --
    it is a description the deriver reads.

    output_paths: workspace-relative paths this stage writes. ALL must exist and be readable for
    the stage to be considered started; staleness compares against the OLDEST of their mtimes, so a
    partially-rewritten output cannot look fresher than its slowest file.

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
    """
    __slots__ = ("stage_id", "name", "group", "output_paths", "blocking_stage_ids",
                "freshness_globs", "accepted_tag_prefix", "command")

    def __init__(self, stage_id, name, group, output_paths, *, blocking_stage_ids=(),
                freshness_globs=(), accepted_tag_prefix=None, command=None):
        self.stage_id = stage_id
        self.name = name
        self.group = group
        self.output_paths = tuple(output_paths)
        self.blocking_stage_ids = tuple(blocking_stage_ids)
        self.freshness_globs = tuple(freshness_globs)
        self.accepted_tag_prefix = accepted_tag_prefix
        self.command = command


def _existing_paths(workspace_dir, rel_paths):
    out = []
    for rel in rel_paths:
        p = os.path.join(workspace_dir, rel)
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


def derive_stage_state(spec, workspace_dir, stage_states_so_far, spec_by_id, *, tags=None):
    """One stage's state.

    `stage_states_so_far` is {stage_id: state} for every stage already resolved this pass;
    `spec_by_id` is {stage_id: StageSpec} for the whole table, needed only to look up an upstream
    stage's own output_paths for the staleness check. Both are supplied by resolve_all in the
    correct order -- this function does no ordering or global lookup of its own.

    Precedence, each step short-circuiting the rest:
      1. NOT_STARTED  -- an expected output is simply absent. Nothing else is knowable yet.
      2. UNKNOWN       -- an output exists but cannot be read. Distinct from #1 on purpose.
      3. BLOCKED       -- a prerequisite stage is not READY. This stage's own freshness is moot
                          until the prerequisite is.
      4. STALE         -- a freshness input (an upstream stage's artifact, or a raw import glob)
                          is newer than this stage's own oldest output.
      5. NOT_ACCEPTED  -- everything above passed, but the stage's code carries no acceptance tag.
      6. READY         -- everything checked out.
    """
    existing = _existing_paths(workspace_dir, spec.output_paths)
    if len(existing) < len(spec.output_paths):
        return NOT_STARTED
    if not _is_readable(existing):
        return UNKNOWN
    for up_id in spec.blocking_stage_ids:
        if stage_states_so_far.get(up_id) != READY:
            return BLOCKED
    own_oldest = _oldest_mtime(existing)
    if own_oldest is None:
        return UNKNOWN

    freshness_newest = _newest_glob_mtime(workspace_dir, spec.freshness_globs)
    for up_id in spec.blocking_stage_ids:
        up_spec = spec_by_id.get(up_id)
        if up_spec is None:
            continue
        up_existing = _existing_paths(workspace_dir, up_spec.output_paths)
        for p in up_existing:
            try:
                ts = os.path.getmtime(p)
            except OSError:
                continue
            if freshness_newest is None or ts > freshness_newest:
                freshness_newest = ts
    if freshness_newest is not None and freshness_newest > own_oldest:
        return STALE

    if spec.accepted_tag_prefix and not has_accepted_tag(spec.accepted_tag_prefix, tags=tags):
        return NOT_ACCEPTED
    return READY


def resolve_all(stage_table, workspace_dir, *, tags=None):
    """Resolve every stage in `stage_table`, in the order given. Callers must supply stages in
    dependency order: every stage_id named in a later stage's blocking_stage_ids must already have
    appeared earlier in the list. workflow_stage_table() below satisfies this by construction."""
    spec_by_id = {s.stage_id: s for s in stage_table}
    states = {}
    for spec in stage_table:
        states[spec.stage_id] = derive_stage_state(spec, workspace_dir, states, spec_by_id,
                                                    tags=tags)
    return states
