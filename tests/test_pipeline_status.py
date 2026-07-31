#!/usr/bin/env python3
"""Tests for core.pipeline_status - the read-only "what do I run next?" map.

The risk this module carries is not that it crashes; it is that it says READY about a stage that
is not, or invents a status it cannot derive from the filesystem. These tests pin the four states
against real files with real mtimes, and assert the two things it must never do: write anything,
or emit a character the owner's console cannot render.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import pipeline_status as P                                # noqa: E402


def touch(path, when=None):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("x")
    if when is not None:
        os.utime(path, (when, when))
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="pipe-")
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        self.t0 = time.time() - 10000

    def stage(self, **kw):
        kw.setdefault("n", 1)
        kw.setdefault("title", "t")
        kw.setdefault("produces", ["OUT.json"])
        return P.Stage(**kw)

    def one(self, stage):
        return P.evaluate(self.ws, [stage])[0]


class TestStates(Base):
    def test_missing_when_no_output(self):
        r = self.one(self.stage(command="c"))
        self.assertEqual(r["state"], P.MISSING)
        self.assertEqual(r["missing_outputs"], ["OUT.json"])

    def test_ready_when_output_newer_than_input(self):
        touch(os.path.join(self.ws, "IN.json"), self.t0)
        touch(os.path.join(self.ws, "OUT.json"), self.t0 + 100)
        r = self.one(self.stage(needs=["IN.json"], command="c"))
        self.assertEqual(r["state"], P.READY)
        self.assertEqual(r["artifact"], "OUT.json")

    def test_stale_when_an_input_is_newer(self):
        touch(os.path.join(self.ws, "OUT.json"), self.t0)
        touch(os.path.join(self.ws, "IN.json"), self.t0 + 100)
        r = self.one(self.stage(needs=["IN.json"], command="c"))
        self.assertEqual(r["state"], P.STALE)
        self.assertEqual(r["newer_input"], "IN.json")

    def test_blocked_when_a_required_input_is_absent(self):
        r = self.one(self.stage(needs=["IN.json"], command="c"))
        self.assertEqual(r["state"], P.BLOCKED)
        self.assertEqual(r["missing_inputs"], ["IN.json"])

    def test_owner_input_is_missing_not_blocked(self):
        """An owner-supplied file the owner has not dropped in yet is a thing they can act on;
        calling it BLOCKED would hide the one step that unblocks everything downstream."""
        r = self.one(self.stage(needs=["IN.json"], command=None, note="drop it in"))
        self.assertEqual(r["state"], P.MISSING)
        self.assertTrue(r["owner_input"])

    def test_a_stage_with_several_outputs_needs_them_all(self):
        touch(os.path.join(self.ws, "OUT.json"), self.t0)
        r = self.one(self.stage(produces=["OUT.json", "OTHER.json"], command="c"))
        self.assertEqual(r["state"], P.MISSING)
        self.assertEqual(r["missing_outputs"], ["OTHER.json"])

    def test_wildcard_artifact_resolves(self):
        touch(os.path.join(self.ws, "US_AMAZON_cerebro_B0X_2026-07-16.xlsx"), self.t0)
        r = self.one(self.stage(produces=["US_AMAZON_cerebro_*.xlsx"], command="c"))
        self.assertEqual(r["state"], P.READY)
        self.assertTrue(r["artifact"].startswith("US_AMAZON_cerebro_"))

    def test_newest_of_several_inputs_decides_staleness(self):
        touch(os.path.join(self.ws, "A.json"), self.t0)
        touch(os.path.join(self.ws, "OUT.json"), self.t0 + 50)
        touch(os.path.join(self.ws, "B.json"), self.t0 + 100)
        r = self.one(self.stage(needs=["A.json", "B.json"], command="c"))
        self.assertEqual(r["state"], P.STALE)
        self.assertEqual(r["newer_input"], "B.json")


class TestNextAction(Base):
    def rows(self, *stages):
        return P.evaluate(self.ws, list(stages))

    def test_first_actionable_stage_is_chosen(self):
        touch(os.path.join(self.ws, "A.json"), self.t0)
        rows = self.rows(self.stage(n=1, produces=["A.json"], command="a"),
                         self.stage(n=2, produces=["B.json"], command="b"))
        self.assertEqual(P.next_action(rows)["n"], 2)

    def test_blocked_stages_are_never_offered(self):
        """A BLOCKED stage cannot run: offering it would send the owner to a command that fails."""
        rows = self.rows(self.stage(n=1, produces=["B.json"], needs=["MISSING.json"], command="b"))
        self.assertEqual(rows[0]["state"], P.BLOCKED)
        self.assertIsNone(P.next_action(rows))

    def test_none_when_everything_is_ready(self):
        touch(os.path.join(self.ws, "A.json"), self.t0)
        self.assertIsNone(P.next_action(self.rows(self.stage(produces=["A.json"], command="a"))))


class TestRendering(Base):
    def test_multi_word_seed_is_quoted(self):
        """Unquoted, `--seed nurse sweatshirt` silently becomes a different argument."""
        cmd = P._fmt_command("python -m x --seed {seed}", "runs/T2", "nurse sweatshirt")
        self.assertIn('--seed "nurse sweatshirt"', cmd)

    def test_single_word_seed_is_not_quoted(self):
        self.assertTrue(P._fmt_command("x {seed}", "w", "mug").endswith("mug"))

    def test_placeholder_when_no_seed_given(self):
        self.assertIn("<seed-keyword>", P._fmt_command("x {seed}", "w", None))

    def test_output_is_ascii_only(self):
        """It prints into the Windows console the owner uses, whose code page mangles dashes."""
        touch(os.path.join(self.ws, "OUT.json"), self.t0)
        text = P.render(P.evaluate(self.ws, [self.stage(command="c")]), self.ws, "seed")
        self.assertTrue(all(ord(c) < 128 for c in text),
                        [c for c in text if ord(c) >= 128])

    def test_next_command_appears_in_the_output(self):
        rows = P.evaluate(self.ws, [self.stage(command="python -m research.x --seed {seed}")])
        out = P.render(rows, self.ws, "nurse sweatshirt")
        self.assertIn('python -m research.x --seed "nurse sweatshirt"', out)

    def test_owner_input_prints_the_note_not_a_command(self):
        rows = P.evaluate(self.ws, [self.stage(command=None, note="drop the export in")])
        out = P.render(rows, self.ws)
        self.assertIn("you supply this one", out)
        self.assertIn("drop the export in", out)


class TestSafety(Base):
    def test_evaluating_writes_nothing(self):
        touch(os.path.join(self.ws, "OUT.json"), self.t0)
        before = sorted(os.listdir(self.ws))
        P.render(P.evaluate(self.ws, P.STAGES), self.ws, "seed")
        self.assertEqual(sorted(os.listdir(self.ws)), before)

    def test_module_makes_no_network_or_amazon_call(self):
        src = open(os.path.join(ROOT, "core", "pipeline_status.py"), encoding="utf-8").read()
        for bad in ("requests", "urllib.request", "http.client", "socket",
                    "sellercentral", "amazon.com", "subprocess"):
            self.assertNotIn(bad, src, bad)

    def test_missing_workspace_is_an_error_not_a_crash(self):
        out = subprocess.run([sys.executable, "-m", "core.pipeline_status",
                              "--workspace", os.path.join(self.ws, "nope")],
                             capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(out.returncode, 2)
        self.assertIn("workspace not found", out.stderr)

    def test_real_stage_table_is_coherent(self):
        """Every non-first stage's inputs must be produced by an earlier stage, or the map would
        send the owner to a command whose input nothing ever creates."""
        produced = set()
        for st in P.STAGES:
            for need in st.needs:
                self.assertIn(need, produced, f"stage {st.n} needs {need}, nothing produces it")
            produced.update(st.produces)
        self.assertEqual([s.n for s in P.STAGES], list(range(1, len(P.STAGES) + 1)))

    def test_json_mode_is_valid_json(self):
        out = subprocess.run([sys.executable, "-m", "core.pipeline_status",
                              "--workspace", self.ws, "--json"],
                             capture_output=True, text=True, cwd=ROOT)
        import json
        doc = json.loads(out.stdout)
        self.assertEqual(doc["workspace"], self.ws)
        self.assertEqual(len(doc["stages"]), len(P.STAGES))


if __name__ == "__main__":
    unittest.main()
