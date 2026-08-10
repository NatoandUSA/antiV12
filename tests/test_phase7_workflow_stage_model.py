#!/usr/bin/env python3
"""production.phase7_workflow_stage_model -- Dashboard V1 Workflow stage-state derivation engine.

All six states over synthetic workspaces, per DASHBOARD-V1-SPEC.md Section 5 and the Step-1 exit
gate in Section 11: "unit tests over synthetic workspaces covering all 6 states." No real workspace,
no real git tags -- everything here is built and torn down per test.
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


class SingleStageStates(unittest.TestCase):
    """Each of the six states, in isolation, on a one-stage table."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="wsm-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def test_not_started_when_output_absent(self):
        spec = WSM.StageSpec(1, "Seed keyword", WSM.GROUP_RESEARCH, ("OUT.json",))
        states = WSM.resolve_all([spec], self.d)
        self.assertEqual(states[1], WSM.NOT_STARTED)

    def test_not_started_when_only_some_outputs_present(self):
        spec = WSM.StageSpec(1, "Two-file stage", WSM.GROUP_RESEARCH, ("A.json", "B.json"))
        _write(os.path.join(self.d, "A.json"))
        states = WSM.resolve_all([spec], self.d)
        self.assertEqual(states[1], WSM.NOT_STARTED)

    def test_ready_when_output_present_no_inputs_no_acceptance_gate(self):
        spec = WSM.StageSpec(1, "Seed keyword", WSM.GROUP_RESEARCH, ("OUT.json",))
        _write(os.path.join(self.d, "OUT.json"))
        states = WSM.resolve_all([spec], self.d)
        self.assertEqual(states[1], WSM.READY)

    def test_unknown_when_json_output_exists_but_is_unparseable(self):
        # Windows does not reliably enforce chmod-based read denial for a file's own owner, so a
        # raw OS-level permission failure is not portably simulable here. A truncated/corrupt JSON
        # artifact is both fully portable AND the more realistic real-world failure mode (a crash
        # mid-write, a partial disk flush) -- and it is explicitly in scope: the spec says
        # "unreadable / unparseable", not "unreadable" alone.
        spec = WSM.StageSpec(1, "Seed keyword", WSM.GROUP_RESEARCH, ("OUT.json",))
        _write(os.path.join(self.d, "OUT.json"), b'{"truncated": tr')
        states = WSM.resolve_all([spec], self.d)
        self.assertEqual(states[1], WSM.UNKNOWN)

    def test_unknown_is_not_collapsed_into_not_started(self):
        """The distinction DASHBOARD-V1-SPEC Section 5 explicitly protects: 'I could not read it'
        must never present the same as 'it is not there'."""
        spec = WSM.StageSpec(1, "Seed keyword", WSM.GROUP_RESEARCH, ("OUT.json",))
        _write(os.path.join(self.d, "OUT.json"), b'not valid json at all')
        state = WSM.resolve_all([spec], self.d)[1]
        self.assertNotEqual(state, WSM.NOT_STARTED)
        self.assertEqual(state, WSM.UNKNOWN)

    def test_non_json_artifact_only_checked_for_raw_readability(self):
        """A .md brief (e.g. the creative or Seller Central handoff) is prose, not JSON -- it must
        not be flagged UNKNOWN just because it is not valid JSON."""
        spec = WSM.StageSpec(1, "Creative brief", WSM.GROUP_BUILD, ("BRIEF.md",))
        _write(os.path.join(self.d, "BRIEF.md"), b"# Not JSON at all, and that is fine here.")
        states = WSM.resolve_all([spec], self.d)
        self.assertEqual(states[1], WSM.READY)

    def test_not_accepted_when_gate_set_and_no_matching_tag(self):
        spec = WSM.StageSpec(1, "PPC export", WSM.GROUP_LAUNCH, ("PLAN.json",),
                             accepted_tag_prefix="phase7-1e")
        _write(os.path.join(self.d, "PLAN.json"))
        states = WSM.resolve_all([spec], self.d, tags=["phase7-1e-checkpoint-abc123"])
        self.assertEqual(states[1], WSM.NOT_ACCEPTED)

    def test_ready_when_gate_set_and_matching_accepted_tag_present(self):
        spec = WSM.StageSpec(1, "PPC export", WSM.GROUP_LAUNCH, ("PLAN.json",),
                             accepted_tag_prefix="phase7-1e")
        _write(os.path.join(self.d, "PLAN.json"))
        states = WSM.resolve_all([spec], self.d,
                                 tags=["phase7-1e-accepted-deadbeef", "unrelated-tag"])
        self.assertEqual(states[1], WSM.READY)

    def test_not_accepted_tag_prefix_does_not_match_a_different_stages_tag(self):
        """'phase7-1' must not accidentally match a 'phase7-1e' or 'phase7-1m' tag string -- the
        needle includes '-accepted-' so a bare prefix collision cannot silently pass."""
        spec = WSM.StageSpec(1, "Economics", WSM.GROUP_LAUNCH, ("ECON.json",),
                             accepted_tag_prefix="phase7-1")
        _write(os.path.join(self.d, "ECON.json"))
        states = WSM.resolve_all([spec], self.d, tags=["phase7-1e-accepted-deadbeef"])
        self.assertEqual(states[1], WSM.NOT_ACCEPTED)


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
        states = WSM.resolve_all(self._table(), self.d)
        self.assertEqual(states[1], WSM.NOT_STARTED)
        self.assertEqual(states[2], WSM.BLOCKED)

    def test_blocked_when_upstream_not_accepted(self):
        s1 = WSM.StageSpec(1, "Upstream", WSM.GROUP_RESEARCH, ("UP.json",),
                           accepted_tag_prefix="phase7-1e")
        s2 = WSM.StageSpec(2, "Downstream", WSM.GROUP_DECIDE, ("DOWN.json",),
                           blocking_stage_ids=(1,))
        _write(os.path.join(self.d, "UP.json"))
        _write(os.path.join(self.d, "DOWN.json"))
        states = WSM.resolve_all([s1, s2], self.d, tags=[])
        self.assertEqual(states[1], WSM.NOT_ACCEPTED)
        self.assertEqual(states[2], WSM.BLOCKED,
                         "a NOT_ACCEPTED upstream must not silently unblock its downstream")

    def test_ready_when_upstream_ready_and_downstream_newer(self):
        up = os.path.join(self.d, "UP.json")
        down = os.path.join(self.d, "DOWN.json")
        _write(up)
        _touch_older(up, seconds_ago=100)
        _write(down)   # written "now" -- newer than upstream
        states = WSM.resolve_all(self._table(), self.d)
        self.assertEqual(states[1], WSM.READY)
        self.assertEqual(states[2], WSM.READY)

    def test_stale_when_upstream_output_newer_than_downstream(self):
        up = os.path.join(self.d, "UP.json")
        down = os.path.join(self.d, "DOWN.json")
        _write(down)
        _touch_older(down, seconds_ago=100)
        _write(up)   # re-imported / re-generated AFTER downstream was last built
        states = WSM.resolve_all(self._table(), self.d)
        self.assertEqual(states[1], WSM.READY)
        self.assertEqual(states[2], WSM.STALE)

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
        states = WSM.resolve_all([spec], self.d)
        self.assertEqual(states[1], WSM.STALE)

    def test_not_stale_when_freshness_glob_matches_nothing(self):
        spec = WSM.StageSpec(1, "Clean / merge / analyze", WSM.GROUP_RESEARCH,
                             ("MATRIX.json",), freshness_globs=("raw_*.xlsx",))
        _write(os.path.join(self.d, "MATRIX.json"))
        states = WSM.resolve_all([spec], self.d)
        self.assertEqual(states[1], WSM.READY)


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
        states = WSM.resolve_all([s1, s2], self.d, tags=[])
        self.assertEqual(states[2], WSM.BLOCKED)

    def test_stale_outranks_not_accepted(self):
        s1 = WSM.StageSpec(1, "Upstream", WSM.GROUP_RESEARCH, ("UP.json",))
        s2 = WSM.StageSpec(2, "Downstream", WSM.GROUP_LAUNCH, ("DOWN.json",),
                           blocking_stage_ids=(1,), accepted_tag_prefix="phase7-1e")
        down = os.path.join(self.d, "DOWN.json")
        up = os.path.join(self.d, "UP.json")
        _write(down)
        _touch_older(down, seconds_ago=100)
        _write(up)
        states = WSM.resolve_all([s1, s2], self.d, tags=[])
        self.assertEqual(states[1], WSM.READY)
        self.assertEqual(states[2], WSM.STALE,
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

        states = WSM.resolve_all([s1, s2, s3, s4, s5, s6], self.d, tags=[])

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


if __name__ == "__main__":
    unittest.main()
