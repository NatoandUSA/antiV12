#!/usr/bin/env python3
"""Tests for core.pipeline_status - the read-only "what do I run next?" map.

The risk this module carries is not that it crashes; it is that it says READY about a stage that
is not, or invents a status it cannot derive from the filesystem. These tests pin the four states
against real files with real mtimes, and assert the two things it must never do: write anything,
or emit a character the owner's console cannot render.
"""
import ast
import json
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


class TestMultiOutputStaleness(Base):
    """A stage that declares several outputs is only as current as its OLDEST one.

    Comparing the NEWEST output against the input -- as the audited d163ff0 baseline did --
    lets a freshly rewritten sibling hide an artifact that is genuinely older than its own
    input. STALE is this module's only derived signal, so a masked STALE is not a cosmetic
    defect: the owner skips the re-run and every downstream stage is then built on an input
    that never reflected the data it claims to summarise.
    """

    def test_a_stale_output_is_not_masked_by_a_fresher_sibling(self):
        # The exact reproduction from the 2026-08-01 independent audit of d163ff0.
        touch(os.path.join(self.ws, "IN.xlsx"), self.t0 + 1000)
        touch(os.path.join(self.ws, "OUT.json"), self.t0 + 500)        # older than its input
        touch(os.path.join(self.ws, "OTHER.json"), self.t0 + 2000)     # fresher sibling
        r = self.one(self.stage(produces=["OUT.json", "OTHER.json"], needs=["IN.xlsx"],
                                command="c"))
        self.assertEqual(r["state"], P.STALE)

    def test_the_stale_output_is_the_one_named_to_the_owner(self):
        """Naming the fresh sibling would send the owner to look at a file that is fine."""
        touch(os.path.join(self.ws, "IN.xlsx"), self.t0 + 1000)
        touch(os.path.join(self.ws, "OUT.json"), self.t0 + 500)
        touch(os.path.join(self.ws, "OTHER.json"), self.t0 + 2000)
        r = self.one(self.stage(produces=["OUT.json", "OTHER.json"], needs=["IN.xlsx"],
                                command="c"))
        self.assertEqual(r["artifact"], "OUT.json")
        self.assertEqual(r["newer_input"], "IN.xlsx")
        self.assertIn("OUT.json is older than IN.xlsx",
                      P.render(P.evaluate(self.ws, [self.stage(
                          produces=["OUT.json", "OTHER.json"], needs=["IN.xlsx"],
                          command="c")]), self.ws))

    def test_ready_still_requires_every_output_to_beat_the_input(self):
        touch(os.path.join(self.ws, "IN.xlsx"), self.t0)
        touch(os.path.join(self.ws, "OUT.json"), self.t0 + 500)
        touch(os.path.join(self.ws, "OTHER.json"), self.t0 + 2000)
        r = self.one(self.stage(produces=["OUT.json", "OTHER.json"], needs=["IN.xlsx"],
                                command="c"))
        self.assertEqual(r["state"], P.READY)

    def test_real_stage_5_master_keyword_list_is_not_masked(self):
        """Stage 5 in the shipped table: a stale MASTER-KEYWORDS-LEAN.json hidden behind a
        fresh CEREBRO-EVIDENCE-MATRIX.json is the case the owner actually hits."""
        touch(os.path.join(self.ws, "US_AMAZON_cerebro_B0X.xlsx"), self.t0 + 1000)
        touch(os.path.join(self.ws, "MASTER-KEYWORDS-LEAN.json"), self.t0 + 500)
        touch(os.path.join(self.ws, "CEREBRO-EVIDENCE-MATRIX.json"), self.t0 + 2000)
        stage5 = [s for s in P.STAGES if s.n == 5][0]
        self.assertEqual(P.evaluate(self.ws, [stage5])[0]["state"], P.STALE)

    def test_real_stage_11_product_page_is_not_masked(self):
        touch(os.path.join(self.ws, "LISTING-BRIEF.json"), self.t0 + 1000)
        touch(os.path.join(self.ws, "CLAIM-EVIDENCE.json"), self.t0 + 1000)
        touch(os.path.join(self.ws, "PRODUCT-PAGE.json"), self.t0 + 500)
        touch(os.path.join(self.ws, "BACKEND-SEARCH-TERMS.json"), self.t0 + 2000)
        stage11 = [s for s in P.STAGES if s.n == 11][0]
        self.assertEqual(P.evaluate(self.ws, [stage11])[0]["state"], P.STALE)

    def test_a_wildcard_pattern_is_satisfied_by_its_newest_match(self):
        """Deliberate scope limit. One PATTERN contributes one artifact -- its newest match --
        and the oldest is taken ACROSS patterns, not across every file on disk. Otherwise a
        superseded Cerebro export the owner never deleted would mark the stage stale forever."""
        touch(os.path.join(self.ws, "IN.json"), self.t0 + 1000)
        touch(os.path.join(self.ws, "US_AMAZON_cerebro_old.xlsx"), self.t0 + 500)
        touch(os.path.join(self.ws, "US_AMAZON_cerebro_new.xlsx"), self.t0 + 2000)
        r = self.one(self.stage(produces=["US_AMAZON_cerebro_*.xlsx"], needs=["IN.json"],
                                command="c"))
        self.assertEqual(r["state"], P.READY)
        self.assertEqual(r["artifact"], "US_AMAZON_cerebro_new.xlsx")


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


# Seeds chosen to break a renderer in a DIFFERENT way each. Shared by the platform-independent
# shape tests and by the Windows execution proof, so the corpus cannot drift apart between them.
ADVERSARIAL_SEEDS = (
    "nurse sweatshirt",                 # spaces: the ordinary case that breaks unquoted
    "nurse $5 gift",                    # $ expands inside a PowerShell DOUBLE-quoted string
    "nurse'; Write-Host PWNED; #",      # closes a single-quoted literal, then injects
    'nurse"quote"',                     # double quotes inside a single-quoted literal
    "nurse > sentinel.txt",             # redirection: creates a file if quoting fails
    "nurse | Write-Host PWNED",         # pipeline injection
    "nurse; Write-Host PWNED",          # statement separator
    "nurse & Write-Host PWNED",         # call operator / cmd-style separator
    "nurse `n tab",                     # backtick: PowerShell's escape character
    "nurse (sub) [idx] {blk}",          # grouping, index and script-block syntax
    "nurse @splat %var !bang ^caret",   # splat, cmd var, delayed expansion, cmd escape
    "-shirt",                           # leading dash: read as a parameter name if bare
)


class TestRendering(Base):
    def test_multi_word_seed_is_quoted(self):
        """Unquoted, `--seed nurse sweatshirt` silently becomes a different argument."""
        cmd = P._fmt_command("python -m x --seed {seed}", "runs/T2", "nurse sweatshirt")
        self.assertIn("--seed 'nurse sweatshirt'", cmd)

    def test_single_word_seed_is_not_quoted(self):
        self.assertTrue(P._fmt_command("x {seed}", "w", "mug").endswith("mug"))

    def test_an_embedded_quote_is_escaped_not_passed_through(self):
        """Baseline printed `--seed "nurse"; calc; #"`, which a shell splits at the second quote.
        PowerShell's only escape inside a literal is a doubled single quote."""
        out = P._fmt_command("x --seed {seed}", "w", "nurse'; calc; #")
        self.assertEqual(out, "x --seed 'nurse''; calc; #'")

    def test_shell_metacharacters_force_quoting(self):
        for seed in ("a;b", "a|b", "a&b", "a>b", "a<b", "a$b", "a`b", "a(b)", "a{b}",
                     "a,b", "a@b", "a%b", "a!b", "a^b", "a#b", "a'b", 'a"b', "a b"):
            self.assertTrue(P._ps_quote(seed).startswith("'"), seed)

    def test_a_leading_dash_forces_quoting(self):
        """Bare, PowerShell reads a leading `-` as the start of a parameter name, not a value."""
        self.assertEqual(P._ps_quote("-shirt"), "'-shirt'")

    def test_a_dollar_seed_is_not_expanded_away(self):
        """A double-quoted PowerShell string expands `$5`; the seed would arrive mangled. This is
        why the renderer commits to single-quoted literals and to ONE named shell."""
        out = P._fmt_command("x --seed {seed}", "w", "nurse $5 gift")
        self.assertEqual(out, "x --seed 'nurse $5 gift'")
        self.assertNotIn('"', out)

    def test_a_backtick_seed_survives(self):
        self.assertEqual(P._ps_quote("a`b"), "'a`b'")

    def test_a_workspace_path_with_a_space_is_quoted(self):
        cmd = P._fmt_command("x {workspace}", r"C:\Users\Long\My Runs\T2", None)
        self.assertIn(r"'C:\Users\Long\My Runs\T2'", cmd)

    def test_an_ordinary_path_stays_bare(self):
        self.assertEqual(P._ps_quote("runs/T2"), "runs/T2")

    def test_output_is_ascii_only(self):
        """It prints into the Windows console the owner uses, whose code page mangles dashes."""
        touch(os.path.join(self.ws, "OUT.json"), self.t0)
        text = P.render(P.evaluate(self.ws, [self.stage(command="c")]), self.ws, "seed")
        self.assertTrue(all(ord(c) < 128 for c in text),
                        [c for c in text if ord(c) >= 128])

    def test_next_command_appears_in_the_output(self):
        rows = P.evaluate(self.ws, [self.stage(command="python -m research.x --seed {seed}")])
        out = P.render(rows, self.ws, "nurse sweatshirt")
        self.assertIn("python -m research.x --seed 'nurse sweatshirt'", out)

    def test_the_printed_command_names_its_shell(self):
        """Quoting is not portable. A command with no named shell cannot be correct for any."""
        rows = P.evaluate(self.ws, [self.stage(command="python -m research.x --seed {seed}")])
        self.assertIn(f"[{P.TARGET_SHELL}]", P.render(rows, self.ws, "mug"))

    def test_owner_input_prints_the_note_not_a_command(self):
        rows = P.evaluate(self.ws, [self.stage(command=None, note="drop the export in")])
        out = P.render(rows, self.ws)
        self.assertIn("you supply this one", out)
        self.assertIn("drop the export in", out)


class TestNoCommandWithoutARealSeed(Base):
    """A command containing `<seed-keyword>` looks ready to paste and is not. Pasted unchanged it
    runs the engine with the literal placeholder as the seed. It was also the line most likely to
    be pasted, because it is what a first run with no arguments prints."""

    def test_fmt_command_returns_none_rather_than_a_placeholder(self):
        self.assertIsNone(P._fmt_command("x --seed {seed}", "w", None))

    def test_a_command_not_needing_a_seed_still_renders(self):
        self.assertEqual(P._fmt_command("x {workspace}", "runs/T2", None), "x runs/T2")

    def test_no_placeholder_string_is_ever_printed(self):
        rows = P.evaluate(self.ws, [self.stage(command="python -m research.x --seed {seed}")])
        out = P.render(rows, self.ws, None)
        self.assertNotIn("<seed-keyword>", out)
        self.assertNotIn("python -m research.x", out)   # the engine command is not offered

    def test_the_owner_is_told_how_to_get_the_real_command(self):
        rows = P.evaluate(self.ws, [self.stage(command="python -m research.x --seed {seed}")])
        out = P.render(rows, self.ws, None)
        self.assertIn("needs your seed keyword", out)
        self.assertIn("python -m core.pipeline_status", out)

    def _json(self, *extra):
        """Stage 1 is owner-input and carries no command; satisfy it so the next actionable
        stage is stage 2, whose command does take a seed."""
        touch(os.path.join(self.ws, "Helium_10_Xray_a.xlsx"), self.t0)
        out = subprocess.run([sys.executable, "-m", "core.pipeline_status",
                              "--workspace", self.ws, "--json", *extra],
                             capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_json_next_command_is_null_and_says_why(self):
        """A machine consumer that saw a placeholder command string could run it."""
        doc = self._json()
        self.assertEqual(doc["next"], 2)
        self.assertIsNone(doc["next_command"])
        self.assertTrue(doc["next_command_needs_seed"])
        self.assertEqual(doc["target_shell"], P.TARGET_SHELL)

    def test_json_next_command_is_real_when_the_seed_is_real(self):
        doc = self._json("--seed", "nurse sweatshirt")
        self.assertIn("'nurse sweatshirt'", doc["next_command"])
        self.assertFalse(doc["next_command_needs_seed"])


class TestWindowsPowerShellExecution(Base):
    """EXECUTION proof for the rendered command, not a shape assertion.

    Everything else about `_ps_quote` checks the string it produces. That is an argument from
    PowerShell's documented single-quoted-literal rule, not evidence. This class pastes the
    production-rendered line into a real `powershell.exe` and reads back what the process
    actually received in argv.

    The probe is harmless: a Python script that prints its own argv as JSON and does nothing
    else. Nothing here runs an engine, touches the workspace, or contacts anything.
    """

    def _probe(self):
        path = os.path.join(self.ws, "argv_probe.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("import json, sys\nprint(json.dumps(sys.argv[1:]))\n")
        return path

    def test_the_seed_corpus_is_covered_by_the_shape_tests_on_every_platform(self):
        """Runs everywhere, so a non-Windows CI still exercises the whole corpus. It proves the
        renderer emits a single-quoted literal with the right escaping -- not that PowerShell
        parses it back, which is what the Windows test below is for."""
        for seed in ADVERSARIAL_SEEDS:
            rendered = P._ps_quote(seed)
            self.assertTrue(rendered.startswith("'") and rendered.endswith("'"), seed)
            self.assertEqual(rendered[1:-1].replace("''", "\x00"), seed.replace("'", "\x00"), seed)

    @unittest.skipUnless(sys.platform == "win32",
                         "Windows PowerShell execution proof; the renderer targets that shell")
    def test_windows_powershell_renderer_preserves_exact_seed(self):
        exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                           "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        # On Windows this must EXIST. Skipping here would let a missing shell read as proof.
        self.assertTrue(os.path.isfile(exe), f"powershell.exe not found at {exe}")

        probe = self._probe()
        ps1 = os.path.join(self.ws, "paste.ps1")
        sentinel = os.path.join(self.ws, "sentinel.txt")

        for seed in ADVERSARIAL_SEEDS:
            # Rendered by the PRODUCTION helper. If _ps_quote is wrong, this line is wrong.
            line = "{py} {probe} --seed {seed}".format(
                py=P._ps_quote(sys.executable), probe=P._ps_quote(probe), seed=P._ps_quote(seed))
            with open(ps1, "w", encoding="utf-8") as fh:
                fh.write(line + "\n")

            run = subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", ps1],
                capture_output=True, text=True, cwd=self.ws)

            self.assertEqual(run.returncode, 0, f"{seed!r}: {run.stderr}")
            argv = json.loads(run.stdout.strip().splitlines()[-1])
            # 1. the seed arrives as ONE argument, 2. with its exact value
            self.assertEqual(argv, ["--seed", seed], f"{seed!r} was mangled: {argv!r}")
            # 3. no extra command executed
            self.assertNotIn("PWNED", run.stdout, seed)
            self.assertNotIn("PWNED", run.stderr, seed)
            # 4. no redirection-created file
            self.assertFalse(os.path.exists(sentinel), f"{seed!r} created {sentinel}")

    @unittest.skipUnless(sys.platform == "win32", "Windows PowerShell execution proof")
    def test_windows_powershell_runs_the_real_next_command_shape(self):
        """The same proof through `_fmt_command` rather than `_ps_quote` directly, so the
        template-substitution path is covered too, workspace argument included."""
        exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                           "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        self.assertTrue(os.path.isfile(exe), exe)
        probe = self._probe()
        seed = "nurse'; Write-Host PWNED; # $5"
        line = P._fmt_command(
            "{py} {probe} --workspace {{workspace}} --seed {{seed}}".format(
                py=P._ps_quote(sys.executable), probe=P._ps_quote(probe)),
            self.ws, seed)
        ps1 = os.path.join(self.ws, "paste2.ps1")
        with open(ps1, "w", encoding="utf-8") as fh:
            fh.write(line + "\n")
        run = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", ps1],
            capture_output=True, text=True, cwd=self.ws)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout.strip().splitlines()[-1]),
                         ["--workspace", self.ws, "--seed", seed])
        self.assertNotIn("PWNED", run.stdout)


class TestOutputPatternSemantics(Base):
    """`Stage.produces` is required-all. The staleness minimum is only correct under that reading:
    an optional or any-of pattern in the same flat list would produce a false STALE."""

    def test_every_declared_output_is_required(self):
        """Behavioural proof against the SHIPPED table, not a comment. Materialise every output
        of a multi-output stage fresh, then remove them one at a time: each removal alone must
        stop the stage being READY. If an optional output is ever added to `produces`, this
        fails."""
        multi = [s for s in P.STAGES if len(s.produces) > 1]
        self.assertTrue(multi, "table has no multi-output stage; this guard would be vacuous")
        for st in multi:
            for pat in st.needs:
                touch(os.path.join(self.ws, pat.replace("*", "X")), self.t0)
            outs = [os.path.join(self.ws, p.replace("*", "X")) for p in st.produces]
            for o in outs:
                touch(o, self.t0 + 1000)
            self.assertEqual(P.evaluate(self.ws, [st])[0]["state"], P.READY, st.title)
            for o in outs:
                os.remove(o)
                self.assertNotEqual(P.evaluate(self.ws, [st])[0]["state"], P.READY,
                                    f"{st.title}: {os.path.basename(o)} is not required")
                touch(o, self.t0 + 1000)

    def test_a_missing_required_output_never_reaches_a_staleness_verdict(self):
        """It must report MISSING or BLOCKED. Not READY, and not STALE either -- a stage that has
        not produced an artifact cannot be described as having produced a stale one."""
        touch(os.path.join(self.ws, "IN.xlsx"), self.t0 + 1000)
        touch(os.path.join(self.ws, "OUT.json"), self.t0 + 500)      # present and older
        r = self.one(self.stage(produces=["OUT.json", "ABSENT.json"], needs=["IN.xlsx"],
                                command="c"))
        self.assertIn(r["state"], (P.MISSING, P.BLOCKED))
        self.assertEqual(r["missing_outputs"], ["ABSENT.json"])

    def test_equal_mtimes_are_ready_not_stale(self):
        """Boundary, pinned deliberately. An engine that writes its output in the same clock tick
        as its input is the normal fast case; calling that STALE would be a permanent false alarm
        on coarse-granularity filesystems. The comparison is strict `<`."""
        touch(os.path.join(self.ws, "IN.xlsx"), self.t0)
        touch(os.path.join(self.ws, "OUT.json"), self.t0)
        r = self.one(self.stage(needs=["IN.xlsx"], command="c"))
        self.assertEqual(r["state"], P.READY)

    def test_one_second_older_is_stale(self):
        touch(os.path.join(self.ws, "OUT.json"), self.t0)
        touch(os.path.join(self.ws, "IN.xlsx"), self.t0 + 1)
        self.assertEqual(self.one(self.stage(needs=["IN.xlsx"], command="c"))["state"], P.STALE)

    def test_a_superseded_match_inside_one_pattern_does_not_hold_a_stage_stale(self):
        """The other direction of the same rule: minimum ACROSS patterns, never across every
        matching file. Otherwise an old export the owner never deleted is a permanent STALE."""
        touch(os.path.join(self.ws, "IN.json"), self.t0 + 1000)
        for name, when in (("US_AMAZON_cerebro_a.xlsx", self.t0 + 100),
                           ("US_AMAZON_cerebro_b.xlsx", self.t0 + 500),
                           ("US_AMAZON_cerebro_c.xlsx", self.t0 + 2000)):
            touch(os.path.join(self.ws, name), when)
        r = self.one(self.stage(produces=["US_AMAZON_cerebro_*.xlsx"], needs=["IN.json"],
                                command="c"))
        self.assertEqual(r["state"], P.READY)
        self.assertEqual(r["artifact"], "US_AMAZON_cerebro_c.xlsx")


class TestSafety(Base):
    def test_evaluating_writes_nothing(self):
        touch(os.path.join(self.ws, "OUT.json"), self.t0)
        before = sorted(os.listdir(self.ws))
        P.render(P.evaluate(self.ws, P.STAGES), self.ws, "seed")
        self.assertEqual(sorted(os.listdir(self.ws)), before)

    def test_module_makes_no_network_or_amazon_call(self):
        with open(os.path.join(ROOT, "core", "pipeline_status.py"), encoding="utf-8") as fh:
            src = fh.read()
        for bad in ("requests", "urllib.request", "http.client", "socket",
                    "sellercentral", "amazon.com", "subprocess"):
            self.assertNotIn(bad, src, bad)

    def test_the_cli_touches_no_file_in_the_workspace(self):
        """Runtime evidence, not a source scan. A recursive size+mtime snapshot either side of a
        real subprocess run of the CLI. An AST scan can be fooled by a dynamically resolved
        write; a filesystem diff around the actual process cannot."""
        for name, when in (("Helium_10_Xray_a.xlsx", self.t0), ("ASIN-CANDIDATES.json", self.t0),
                           ("US_AMAZON_cerebro_a.xlsx", self.t0 + 900),
                           ("MASTER-KEYWORDS-LEAN.json", self.t0),
                           ("CEREBRO-EVIDENCE-MATRIX.json", self.t0 + 999)):
            touch(os.path.join(self.ws, name), when)

        def snapshot():
            out = {}
            for root, _dirs, files in os.walk(self.ws):
                for f in files:
                    p = os.path.join(root, f)
                    st = os.stat(p)
                    out[p] = (st.st_size, st.st_mtime_ns)
            return out

        before = snapshot()
        run = subprocess.run([sys.executable, "-m", "core.pipeline_status",
                              "--workspace", self.ws, "--seed", "nurse sweatshirt"],
                             capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(snapshot(), before, "the read-only CLI changed the workspace")

    def test_no_qualified_write_or_exec_call_exists(self):
        """Classifies the RECEIVER, not the bare attribute name. The earlier scan flagged
        `text.replace` as if it were `os.replace`; this one distinguishes them, so a real
        `os.replace` cannot hide behind that false positive being waved through."""
        with open(os.path.join(ROOT, "core", "pipeline_status.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        banned_os = {"remove", "unlink", "mkdir", "makedirs", "rmdir", "rename", "replace",
                     "utime", "chmod", "system", "popen", "walk"}
        banned_bare = {"open", "exec", "eval", "compile", "input", "__import__"}
        offenders = []
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                if f.value.id == "os" and f.attr in banned_os:
                    offenders.append((n.lineno, f"os.{f.attr}"))
            elif isinstance(f, ast.Name) and f.id in banned_bare:
                offenders.append((n.lineno, f.id))
        self.assertEqual(offenders, [], offenders)

    def test_only_read_only_os_functions_are_used(self):
        with open(os.path.join(ROOT, "core", "pipeline_status.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        used = {n.attr for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                and n.value.id == "os"}
        self.assertTrue(used <= {"listdir", "path"}, sorted(used))

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
