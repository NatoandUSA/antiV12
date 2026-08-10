#!/usr/bin/env python3
"""production.phase7_workflow_stage_model -- Dashboard V1 Workflow stage-state derivation engine.

All six states over synthetic workspaces, per DASHBOARD-V1-SPEC.md Section 5 and the Step-1 exit
gate in Section 11: "unit tests over synthetic workspaces covering all 6 states." No real workspace,
no real git tags -- everything here is built and torn down per test.

Extended for the real 13-stage mapping's own findings (not assumed): stages 2/4 have no fixed
filename (existence_globs), and stages 9/10/11/13 are produced by more than one script sharing one
conceptual stage (StageComponent + composite rollup).
"""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "production")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import phase7_workflow_stage_model as WSM   # noqa: E402


def _write(path, content=b"{}"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)


def _touch_older(path, seconds_ago):
    t = time.time() - seconds_ago
    os.utime(path, (t, t))


def _state(results, stage_id):
    """resolve_all returns {stage_id: {"state": ..., "components": {...}}} -- this pulls just the
    top-level state, which is what most tests care about."""
    return results[stage_id]["state"]


class SingleStageStates(unittest.TestCase):
    """Each of the six states, in isolation, on a one-stage table."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="wsm-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def test_not_started_when_output_absent(self):
        spec = WSM.StageSpec(1, "Seed keyword", WSM.GROUP_RESEARCH, ("OUT.json",))
        results = WSM.resolve_all([spec], self.d)
        self.assertEqual(_state(results, 1), WSM.NOT_STARTED)

    def test_not_started_when_only_some_outputs_present(self):
        spec = WSM.StageSpec(1, "Two-file stage", WSM.GROUP_RESEARCH, ("A.json", "B.json"))
        _write(os.path.join(self.d, "A.json"))
        results = WSM.resolve_all([spec], self.d)
        self.assertEqual(_state(results, 1), WSM.NOT_STARTED)

    def test_ready_when_output_present_no_inputs_no_acceptance_gate(self):
        spec = WSM.StageSpec(1, "Seed keyword", WSM.GROUP_RESEARCH, ("OUT.json",))
        _write(os.path.join(self.d, "OUT.json"))
        results = WSM.resolve_all([spec], self.d)
        self.assertEqual(_state(results, 1), WSM.READY)

    def test_unknown_when_json_output_exists_but_is_unparseable(self):
        # Windows does not reliably enforce chmod-based read denial for a file's own owner, so a
        # raw OS-level permission failure is not portably simulable here. A truncated/corrupt JSON
        # artifact is both fully portable AND the more realistic real-world failure mode (a crash
        # mid-write, a partial disk flush) -- and it is explicitly in scope: the spec says
        # "unreadable / unparseable", not "unreadable" alone.
        spec = WSM.StageSpec(1, "Seed keyword", WSM.GROUP_RESEARCH, ("OUT.json",))
        _write(os.path.join(self.d, "OUT.json"), b'{"truncated": tr')
        results = WSM.resolve_all([spec], self.d)
        self.assertEqual(_state(results, 1), WSM.UNKNOWN)

    def test_unknown_is_not_collapsed_into_not_started(self):
        """The distinction DASHBOARD-V1-SPEC Section 5 explicitly protects: 'I could not read it'
        must never present the same as 'it is not there'."""
        spec = WSM.StageSpec(1, "Seed keyword", WSM.GROUP_RESEARCH, ("OUT.json",))
        _write(os.path.join(self.d, "OUT.json"), b'not valid json at all')
        state = _state(WSM.resolve_all([spec], self.d), 1)
        self.assertNotEqual(state, WSM.NOT_STARTED)
        self.assertEqual(state, WSM.UNKNOWN)

    def test_non_json_artifact_only_checked_for_raw_readability(self):
        """A .md brief (e.g. the creative or Seller Central handoff) is prose, not JSON -- it must
        not be flagged UNKNOWN just because it is not valid JSON."""
        spec = WSM.StageSpec(1, "Creative brief", WSM.GROUP_BUILD, ("BRIEF.md",))
        _write(os.path.join(self.d, "BRIEF.md"), b"# Not JSON at all, and that is fine here.")
        results = WSM.resolve_all([spec], self.d)
        self.assertEqual(_state(results, 1), WSM.READY)

    def test_not_accepted_when_gate_set_and_no_matching_tag(self):
        spec = WSM.StageSpec(1, "PPC export", WSM.GROUP_LAUNCH, ("PLAN.json",),
                             accepted_tag_prefix="phase7-1e")
        _write(os.path.join(self.d, "PLAN.json"))
        results = WSM.resolve_all([spec], self.d, tags=["phase7-1e-checkpoint-abc123"])
        self.assertEqual(_state(results, 1), WSM.NOT_ACCEPTED)

    def test_ready_when_gate_set_and_matching_accepted_tag_present(self):
        spec = WSM.StageSpec(1, "PPC export", WSM.GROUP_LAUNCH, ("PLAN.json",),
                             accepted_tag_prefix="phase7-1e")
        _write(os.path.join(self.d, "PLAN.json"))
        results = WSM.resolve_all([spec], self.d,
                                  tags=["phase7-1e-accepted-deadbeef", "unrelated-tag"])
        self.assertEqual(_state(results, 1), WSM.READY)

    def test_not_accepted_tag_prefix_does_not_match_a_different_stages_tag(self):
        """'phase7-1' must not accidentally match a 'phase7-1e' or 'phase7-1m' tag string -- the
        needle includes '-accepted-' so a bare prefix collision cannot silently pass."""
        spec = WSM.StageSpec(1, "Economics", WSM.GROUP_LAUNCH, ("ECON.json",),
                             accepted_tag_prefix="phase7-1")
        _write(os.path.join(self.d, "ECON.json"))
        results = WSM.resolve_all([spec], self.d, tags=["phase7-1e-accepted-deadbeef"])
        self.assertEqual(_state(results, 1), WSM.NOT_ACCEPTED)


class TwoStageDependency(unittest.TestCase):
    """BLOCKED and STALE, which only exist once a stage has an upstream."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="wsm-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def _table(self):
        s1 = WSM.StageSpec(1, "Upstream", WSM.GROUP_RESEARCH, ("UP.json",))
        s2 = WSM.StageSpec(2, "Downstream", WSM.GROUP_DECIDE, ("DOWN.json",),
                           blocking_stage_ids=(1,))
        return [s1, s2]

    def test_blocked_when_upstream_not_started(self):
        # downstream output exists (e.g. a stale leftover from a prior run), upstream does not
        _write(os.path.join(self.d, "DOWN.json"))
        results = WSM.resolve_all(self._table(), self.d)
        self.assertEqual(_state(results, 1), WSM.NOT_STARTED)
        self.assertEqual(_state(results, 2), WSM.BLOCKED)

    def test_blocked_when_upstream_not_accepted(self):
        s1 = WSM.StageSpec(1, "Upstream", WSM.GROUP_RESEARCH, ("UP.json",),
                           accepted_tag_prefix="phase7-1e")
        s2 = WSM.StageSpec(2, "Downstream", WSM.GROUP_DECIDE, ("DOWN.json",),
                           blocking_stage_ids=(1,))
        _write(os.path.join(self.d, "UP.json"))
        _write(os.path.join(self.d, "DOWN.json"))
        results = WSM.resolve_all([s1, s2], self.d, tags=[])
        self.assertEqual(_state(results, 1), WSM.NOT_ACCEPTED)
        self.assertEqual(_state(results, 2), WSM.BLOCKED,
                         "a NOT_ACCEPTED upstream must not silently unblock its downstream")

    def test_ready_when_upstream_ready_and_downstream_newer(self):
        up = os.path.join(self.d, "UP.json")
        down = os.path.join(self.d, "DOWN.json")
        _write(up)
        _touch_older(up, seconds_ago=100)
        _write(down)   # written "now" -- newer than upstream
        results = WSM.resolve_all(self._table(), self.d)
        self.assertEqual(_state(results, 1), WSM.READY)
        self.assertEqual(_state(results, 2), WSM.READY)

    def test_stale_when_upstream_output_newer_than_downstream(self):
        up = os.path.join(self.d, "UP.json")
        down = os.path.join(self.d, "DOWN.json")
        _write(down)
        _touch_older(down, seconds_ago=100)
        _write(up)   # re-imported / re-generated AFTER downstream was last built
        results = WSM.resolve_all(self._table(), self.d)
        self.assertEqual(_state(results, 1), WSM.READY)
        self.assertEqual(_state(results, 2), WSM.STALE)

    def test_stale_from_a_raw_freshness_glob_not_modelled_as_its_own_stage(self):
        """The real motivating case (DASHBOARD-V1-SPEC Section 8's own worked example): a stage's
        true freshness input is a raw import file, not necessarily the immediately upstream
        stage's own artifact."""
        spec = WSM.StageSpec(1, "Clean / merge / analyze", WSM.GROUP_RESEARCH,
                             ("MATRIX.json",), freshness_globs=("raw_*.xlsx",))
        out = os.path.join(self.d, "MATRIX.json")
        _write(out)
        _touch_older(out, seconds_ago=100)
        _write(os.path.join(self.d, "raw_import.xlsx"))   # imported after MATRIX.json was built
        results = WSM.resolve_all([spec], self.d)
        self.assertEqual(_state(results, 1), WSM.STALE)

    def test_not_stale_when_freshness_glob_matches_nothing(self):
        spec = WSM.StageSpec(1, "Clean / merge / analyze", WSM.GROUP_RESEARCH,
                             ("MATRIX.json",), freshness_globs=("raw_*.xlsx",))
        _write(os.path.join(self.d, "MATRIX.json"))
        results = WSM.resolve_all([spec], self.d)
        self.assertEqual(_state(results, 1), WSM.READY)


class Precedence(unittest.TestCase):
    """Each state check must short-circuit the ones below it, exactly in the documented order."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="wsm-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def test_blocked_outranks_not_accepted(self):
        """A stage that is both blocked AND would otherwise be not-accepted must report BLOCKED --
        its own acceptance status is moot until its prerequisite clears."""
        s1 = WSM.StageSpec(1, "Upstream", WSM.GROUP_RESEARCH, ("UP.json",))
        s2 = WSM.StageSpec(2, "Downstream", WSM.GROUP_LAUNCH, ("DOWN.json",),
                           blocking_stage_ids=(1,), accepted_tag_prefix="phase7-1e")
        _write(os.path.join(self.d, "DOWN.json"))   # upstream absent -> upstream NOT_STARTED
        results = WSM.resolve_all([s1, s2], self.d, tags=[])
        self.assertEqual(_state(results, 2), WSM.BLOCKED)

    def test_stale_outranks_not_accepted(self):
        s1 = WSM.StageSpec(1, "Upstream", WSM.GROUP_RESEARCH, ("UP.json",))
        s2 = WSM.StageSpec(2, "Downstream", WSM.GROUP_LAUNCH, ("DOWN.json",),
                           blocking_stage_ids=(1,), accepted_tag_prefix="phase7-1e")
        down = os.path.join(self.d, "DOWN.json")
        up = os.path.join(self.d, "UP.json")
        _write(down)
        _touch_older(down, seconds_ago=100)
        _write(up)
        results = WSM.resolve_all([s1, s2], self.d, tags=[])
        self.assertEqual(_state(results, 1), WSM.READY)
        self.assertEqual(_state(results, 2), WSM.STALE,
                         "staleness must be reported even though the stage would also be "
                         "NOT_ACCEPTED -- STALE is the more actionable, more specific defect")

    def test_all_six_states_are_reachable_and_distinct(self):
        """A single coherent table that exercises all six states at once, so the state SET itself
        is asserted, not just each state in isolation."""
        s1 = WSM.StageSpec(1, "Seed keyword", WSM.GROUP_RESEARCH, ("SEED.json",))          # READY
        s2 = WSM.StageSpec(2, "Import", WSM.GROUP_RESEARCH, ("IMPORT.json",))               # NOT_STARTED
        s3 = WSM.StageSpec(3, "Batches", WSM.GROUP_RESEARCH, ("BATCH.json",),
                           blocking_stage_ids=(2,))                                         # BLOCKED
        s4 = WSM.StageSpec(4, "Unreadable", WSM.GROUP_RESEARCH, ("BAD.json",))              # UNKNOWN
        s5 = WSM.StageSpec(5, "Clean/merge", WSM.GROUP_RESEARCH, ("MATRIX.json",),
                           freshness_globs=("raw_*.xlsx",))                                 # STALE
        s6 = WSM.StageSpec(6, "PPC export", WSM.GROUP_LAUNCH, ("PLAN.json",),
                           accepted_tag_prefix="phase7-1e")                                 # NOT_ACCEPTED

        _write(os.path.join(self.d, "SEED.json"))
        # stage 3's own output must exist for its BLOCKED check to even run -- otherwise it would
        # report NOT_STARTED first, same as any other stage nobody has attempted yet.
        _write(os.path.join(self.d, "BATCH.json"))
        _write(os.path.join(self.d, "BAD.json"), b'{"truncated": tr')
        matrix = os.path.join(self.d, "MATRIX.json")
        _write(matrix)
        _touch_older(matrix, seconds_ago=100)
        _write(os.path.join(self.d, "raw_import.xlsx"))
        _write(os.path.join(self.d, "PLAN.json"))

        results = WSM.resolve_all([s1, s2, s3, s4, s5, s6], self.d, tags=[])
        states = {sid: r["state"] for sid, r in results.items()}

        self.assertEqual(states, {
            1: WSM.READY, 2: WSM.NOT_STARTED, 3: WSM.BLOCKED, 4: WSM.UNKNOWN,
            5: WSM.STALE, 6: WSM.NOT_ACCEPTED,
        })
        self.assertEqual(set(states.values()), set(WSM.STAGE_STATES),
                         "every one of the six declared states must actually be reachable")


class HasAcceptedTag(unittest.TestCase):
    def test_empty_tags_never_matches(self):
        self.assertFalse(WSM.has_accepted_tag("phase7-1e", tags=[]))
        self.assertFalse(WSM.has_accepted_tag("phase7-1e", tags=None))

    def test_checkpoint_tag_does_not_count_as_accepted(self):
        self.assertFalse(WSM.has_accepted_tag("phase7-1e", tags=["phase7-1e-checkpoint-abc"]))

    def test_accepted_tag_matches(self):
        self.assertTrue(WSM.has_accepted_tag("phase7-1e", tags=["phase7-1e-accepted-abc"]))


class ExistenceGlobs(unittest.TestCase):
    """Stages 2 and 4's real shape: a raw H10/Xray/Cerebro export with a filename Amazon/H10
    chose, not the toolkit -- so there is no fixed path to check, only "does at least one file
    matching this shape exist"."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="wsm-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def test_not_started_when_nothing_matches(self):
        spec = WSM.StageSpec(1, "Import Amazon + Xray", WSM.GROUP_RESEARCH,
                             existence_globs=("*Xray*.xlsx", "*Xray*.csv"))
        results = WSM.resolve_all([spec], self.d)
        self.assertEqual(_state(results, 1), WSM.NOT_STARTED)

    def test_ready_when_one_unpredictably_named_file_matches(self):
        spec = WSM.StageSpec(1, "Import Amazon + Xray", WSM.GROUP_RESEARCH,
                             existence_globs=("*Xray*.xlsx", "*Xray*.csv"))
        _write(os.path.join(self.d, "US_nurse-sweatshirt.Xray.10.08.2026.xlsx"), b"binary-ish")
        results = WSM.resolve_all([spec], self.d)
        self.assertEqual(_state(results, 1), WSM.READY)

    def test_unreadable_import_is_unknown_not_not_started(self):
        spec = WSM.StageSpec(1, "Cerebro re-import", WSM.GROUP_RESEARCH,
                             existence_globs=("*cerebro*.json",))
        _write(os.path.join(self.d, "US_cerebro_export.json"), b'{"truncated": tr')
        results = WSM.resolve_all([spec], self.d)
        self.assertEqual(_state(results, 1), WSM.UNKNOWN)

    def test_only_one_of_several_patterns_needs_to_match(self):
        spec = WSM.StageSpec(1, "Import Amazon + Xray", WSM.GROUP_RESEARCH,
                             existence_globs=("*.xlsx", "*.csv"))
        _write(os.path.join(self.d, "export.csv"))   # only the second pattern hits
        results = WSM.resolve_all([spec], self.d)
        self.assertEqual(_state(results, 1), WSM.READY)

    def test_downstream_can_block_and_go_stale_against_an_existence_glob_stage(self):
        """An existence_globs stage participates in the blocking graph exactly like a
        fixed-artifact one -- confirms the shared _evidence_state core, not a parallel one."""
        s1 = WSM.StageSpec(1, "Import Amazon + Xray", WSM.GROUP_RESEARCH,
                           existence_globs=("*.xlsx",))
        s2 = WSM.StageSpec(2, "ASIN batches", WSM.GROUP_RESEARCH, ("ASIN-BATCHES.json",),
                           blocking_stage_ids=(1,))
        # upstream absent -> downstream BLOCKED
        _write(os.path.join(self.d, "ASIN-BATCHES.json"))
        results = WSM.resolve_all([s1, s2], self.d)
        self.assertEqual(_state(results, 2), WSM.BLOCKED)


class CompositeRollup(unittest.TestCase):
    """Stages 9/10/11/13's real shape: more than one script's output under one conceptual stage.
    "worst wins" via the SAME precedence order the single-stage engine already uses and tests --
    not a second, hand-invented ranking."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="wsm-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def _composite(self, **kw):
        comps = (
            WSM.StageComponent("listing", ("PRODUCT-DETAIL-PAGE.json",)),
            WSM.StageComponent("aplus", ("BASIC-APLUS-CONTENT.json",)),
        )
        return WSM.StageSpec(1, "Listing + A+", WSM.GROUP_BUILD, components=comps, **kw)

    def test_ready_only_when_every_component_is_ready(self):
        _write(os.path.join(self.d, "PRODUCT-DETAIL-PAGE.json"))
        _write(os.path.join(self.d, "BASIC-APLUS-CONTENT.json"))
        results = WSM.resolve_all([self._composite()], self.d)
        self.assertEqual(_state(results, 1), WSM.READY)
        self.assertEqual(results[1]["components"], {"listing": WSM.READY, "aplus": WSM.READY})

    def test_the_worked_example_listing_ready_aplus_not_started(self):
        """The exact case the product decision named: partial progress must not be lost, and the
        rolled-up state must not silently read READY just because one required part is."""
        _write(os.path.join(self.d, "PRODUCT-DETAIL-PAGE.json"))
        results = WSM.resolve_all([self._composite()], self.d)
        self.assertEqual(_state(results, 1), WSM.NOT_STARTED)
        self.assertEqual(results[1]["components"],
                         {"listing": WSM.READY, "aplus": WSM.NOT_STARTED})

    def test_one_component_blocked_rolls_the_whole_stage_up_to_blocked(self):
        s0 = WSM.StageSpec(0, "Keyword allocation", WSM.GROUP_BUILD, ("KEYWORD-ALLOCATION-MAP.json",))
        composite = self._composite(blocking_stage_ids=(0,))
        _write(os.path.join(self.d, "PRODUCT-DETAIL-PAGE.json"))
        _write(os.path.join(self.d, "BASIC-APLUS-CONTENT.json"))
        # stage 0 (keyword allocation) never ran -> both components BLOCKED -> rollup BLOCKED
        results = WSM.resolve_all([s0, composite], self.d)
        self.assertEqual(_state(results, 1), WSM.BLOCKED)

    def test_one_component_unknown_outranks_a_ready_sibling(self):
        comps = (
            WSM.StageComponent("listing", ("PRODUCT-DETAIL-PAGE.json",)),
            WSM.StageComponent("aplus", ("BASIC-APLUS-CONTENT.json",)),
        )
        spec = WSM.StageSpec(1, "Listing + A+", WSM.GROUP_BUILD, components=comps)
        _write(os.path.join(self.d, "PRODUCT-DETAIL-PAGE.json"))
        _write(os.path.join(self.d, "BASIC-APLUS-CONTENT.json"), b'{"truncated": tr')
        results = WSM.resolve_all([spec], self.d)
        self.assertEqual(_state(results, 1), WSM.UNKNOWN)
        self.assertEqual(results[1]["components"]["aplus"], WSM.UNKNOWN)
        self.assertEqual(results[1]["components"]["listing"], WSM.READY)

    def test_one_component_stale_outranks_a_ready_sibling(self):
        comps = (
            WSM.StageComponent("listing", ("PRODUCT-DETAIL-PAGE.json",),
                               freshness_globs=("raw_*.txt",)),
            WSM.StageComponent("aplus", ("BASIC-APLUS-CONTENT.json",)),
        )
        spec = WSM.StageSpec(1, "Listing + A+", WSM.GROUP_BUILD, components=comps)
        listing = os.path.join(self.d, "PRODUCT-DETAIL-PAGE.json")
        _write(listing)
        _touch_older(listing, seconds_ago=100)
        _write(os.path.join(self.d, "raw_input.txt"))   # newer than the listing component
        _write(os.path.join(self.d, "BASIC-APLUS-CONTENT.json"))
        results = WSM.resolve_all([spec], self.d)
        self.assertEqual(_state(results, 1), WSM.STALE)
        self.assertEqual(results[1]["components"]["listing"], WSM.STALE)
        self.assertEqual(results[1]["components"]["aplus"], WSM.READY)

    def test_one_component_not_accepted_outranks_a_ready_sibling(self):
        comps = (
            WSM.StageComponent("readiness", ("PHASE7-1E-READINESS.json",),
                               accepted_tag_prefix="phase7-1e"),
            WSM.StageComponent("guide", ("MANUAL-ENTRY-GUIDE.md",)),
        )
        spec = WSM.StageSpec(1, "PPC export", WSM.GROUP_LAUNCH, components=comps)
        _write(os.path.join(self.d, "PHASE7-1E-READINESS.json"))
        _write(os.path.join(self.d, "MANUAL-ENTRY-GUIDE.md"), b"guide text")
        results = WSM.resolve_all([spec], self.d, tags=[])
        self.assertEqual(_state(results, 1), WSM.NOT_ACCEPTED)
        self.assertEqual(results[1]["components"],
                         {"readiness": WSM.NOT_ACCEPTED, "guide": WSM.READY})

    def test_empty_components_is_not_started(self):
        spec = WSM.StageSpec(1, "Empty composite", WSM.GROUP_BUILD, components=())
        results = WSM.resolve_all([spec], self.d)
        self.assertEqual(_state(results, 1), WSM.NOT_STARTED)
        self.assertEqual(results[1]["components"], {})


class AuthoritativeStageTable(unittest.TestCase):
    """The real 13-stage table (11 resolvable + 2 not-tracked), exercised against a
    representative real-LAYOUT fixture workspace -- not a real production run, but real paths
    (phase6/6B/..., phase7/7.1E/final/..., phase7/7.3/promoted/...) so a directory-structure
    mistake fails here instead of silently in the console/frontend."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="wsm-table-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def test_table_covers_the_11_resolvable_stage_ids_in_dependency_order(self):
        table = WSM.workflow_stage_table()
        self.assertEqual([s.stage_id for s in table], [2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13])

    def test_not_tracked_stages_are_1_and_7_only(self):
        ids = [s["stage_id"] for s in WSM.NOT_TRACKED_STAGES]
        self.assertEqual(ids, [1, 7])
        all_ids = sorted(ids + [s.stage_id for s in WSM.workflow_stage_table()])
        self.assertEqual(all_ids, list(range(1, 14)), "all 13 stage numbers must be accounted "
                                                       "for exactly once, tracked or not")

    def test_every_resolvable_stage_id_is_unique(self):
        ids = [s.stage_id for s in WSM.workflow_stage_table()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_blocking_stage_id_refers_to_a_stage_actually_in_the_table(self):
        """A typo'd blocking_stage_ids entry pointing at stage 1 or 7 (not tracked) would silently
        BLOCKED-lock its downstream forever, since stage_states_so_far.get(1) is always None !=
        READY. Assert the whole table's dependency graph only points at resolvable stages."""
        table = WSM.workflow_stage_table()
        tracked_ids = {s.stage_id for s in table}
        for spec in table:
            for up_id in spec.blocking_stage_ids:
                self.assertIn(up_id, tracked_ids,
                             f"stage {spec.stage_id} blocks on {up_id}, which is not resolvable")

    def test_full_fresh_workspace_is_all_not_started_or_blocked(self):
        """An empty workspace: every stage with no upstream is NOT_STARTED; every stage WITH an
        upstream is BLOCKED (its own output is also absent, but NOT_STARTED already covers that --
        this specifically checks the chain doesn't crash or mis-derive on a totally fresh tree)."""
        results = WSM.resolve_all(WSM.workflow_stage_table(), self.d, tags=[])
        for spec in WSM.workflow_stage_table():
            state = results[spec.stage_id]["state"]
            self.assertIn(state, (WSM.NOT_STARTED, WSM.BLOCKED),
                         f"stage {spec.stage_id} ({spec.name}) = {state} on an empty workspace")

    def test_full_pipeline_run_resolves_every_resolvable_stage_ready(self):
        """Populate every real path the table expects, in dependency order, each write newer than
        the last -- the full chain must resolve READY end to end. 7.1E's readiness component needs
        its accepted tag too, or the whole stage caps at NOT_ACCEPTED."""
        paths = [
            "US_nurse_sweatshirt.Xray.10.08.2026.xlsx",                     # 2 (glob)
            "ASIN-BATCHES.json",                                           # 3
            "US_nurse_sweatshirt.Cerebro.10.08.2026.xlsx",                 # 4 (glob)
            "CEREBRO-EVIDENCE-MATRIX.json",                                # 5
            "MASTER-KEYWORDS-LEAN.json",                                   # 6
            "phase6/6B/KEYWORD-ALLOCATION-MAP.json",                       # 8
            "phase6/6C/PRODUCT-DETAIL-PAGE.json",                          # 9 listing
            "phase6/6D/BASIC-APLUS-CONTENT.json",                          # 9 aplus
            "phase6/6E/LISTING-IMAGE-PROMPTS.md",                          # 10
            "phase6/6E/APLUS-IMAGE-PROMPTS.md",                            # 10
            "phase6/6E/CREATIVE-BRIEF.md",                                 # 10
            "phase7/7.1E/final/PHASE7-1E-READINESS.json",                  # 11 readiness
            "phase7/7.1E/final/MANUAL-ENTRY-GUIDE.md",                     # 11 guide
            "phase7/7.2/final/PHASE7-REPORT-ANALYSIS-READINESS.json",      # 12
            "phase7/7.3/promoted/analysis.json",                          # 13
            "phase7/7.3/promoted/owner-decision-queue.csv",               # 13
        ]
        for i, rel in enumerate(paths):
            p = os.path.join(self.d, rel)
            _write(p)
            _touch_older(p, seconds_ago=len(paths) - i)   # each write strictly newer than the last

        results = WSM.resolve_all(WSM.workflow_stage_table(), self.d,
                                  tags=["phase7-1e-accepted-deadbeef"])
        for spec in WSM.workflow_stage_table():
            self.assertEqual(results[spec.stage_id]["state"], WSM.READY,
                             f"stage {spec.stage_id} ({spec.name}): {results[spec.stage_id]}")


if __name__ == "__main__":
    unittest.main()
