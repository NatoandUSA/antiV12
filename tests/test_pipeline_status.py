#!/usr/bin/env python3
"""Tests for scripts.pipeline_status - the read-only "what do I run next?" map.

The risk this module carries is not that it crashes; it is that it says READY about a stage that
is not, or invents a status it cannot derive from the filesystem. These tests pin the four states
against real files with real mtimes, and assert the two things it must never do: write anything,
or emit a character the owner's console cannot render.
"""
import ast
import hashlib
import inspect
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

from scripts import pipeline_status as P                                # noqa: E402


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

    def test_a_stale_stage_is_offered_and_never_skipped(self):
        """`evaluate` returning STALE is pinned by five tests. The layer that SURFACES it was
        pinned by NONE -- every case above uses only MISSING, BLOCKED and READY -- so
        `next_action` could be changed to ignore STALE with the entire suite still green, and the
        owner would be told everything is up to date over a stale stage. That is precisely the
        failure the multi-output remediation exists to prevent, reintroduced one layer higher.
        Found by mutation testing, not by reading.
        """
        touch(os.path.join(self.ws, "A.json"), self.t0)
        touch(os.path.join(self.ws, "OUT.json"), self.t0)
        touch(os.path.join(self.ws, "IN.json"), self.t0 + 100)          # newer than the output
        rows = self.rows(self.stage(n=1, produces=["A.json"], command="a"),
                         self.stage(n=2, produces=["OUT.json"], needs=["IN.json"], command="b"))
        self.assertEqual(rows[1]["state"], P.STALE)
        nxt = P.next_action(rows)
        self.assertIsNotNone(nxt, "a STALE stage must be offered: STALE is the only signal this "
                                  "module derives, and skipping it silently discards it")
        self.assertEqual(nxt["n"], 2)

    def test_render_never_claims_up_to_date_while_a_stage_is_stale(self):
        """The same defect at the surface the owner actually reads."""
        touch(os.path.join(self.ws, "OUT.json"), self.t0)
        touch(os.path.join(self.ws, "IN.json"), self.t0 + 100)
        rows = self.rows(self.stage(n=1, produces=["OUT.json"], needs=["IN.json"], command="c"))
        out = P.render(rows, self.ws, seed="nurse sweatshirt")
        text = out if isinstance(out, str) else "\n".join(out)
        self.assertNotIn("Every stage is up to date", text)
        self.assertIn("stale", text)


# Seeds chosen to break a renderer in a DIFFERENT way each. Shared by the platform-independent
# shape tests and by the Windows execution proof, so the corpus cannot drift apart between them.
ADVERSARIAL_SEEDS = (
    "nurse sweatshirt",                 # spaces: the ordinary case that breaks unquoted
    "nurse $5 gift",                    # $ expands inside a PowerShell DOUBLE-quoted string
    "nurse'; Write-Host PWNED; #",      # closes a single-quoted literal, then injects
    "nurse > sentinel.txt",             # redirection: creates a file if quoting fails
    "nurse | Write-Host PWNED",         # pipeline injection
    "nurse; Write-Host PWNED",          # statement separator
    "nurse & Write-Host PWNED",         # call operator / cmd-style separator
    "nurse `n tab",                     # backtick: PowerShell's escape character
    "nurse (sub) [idx] {blk}",          # grouping, index and script-block syntax
    "nurse @splat %var !bang ^caret",   # splat, cmd var, delayed expansion, cmd escape
    "-shirt",                           # leading dash: read as a parameter name if bare
    "café naïve müg",    # non-ASCII: a real keyword, and a .ps1 encoding trap
    "nurse\\",           # the trailing backslash form the tool CLAIMS to support -- see below
)

# `nurse\` is in the corpus above on purpose. `unsupported_value()` refuses the QUOTE-REQUIRING
# trailing backslash and declares this bare form supported; a claim of support that is never
# round-tripped through the real shell is exactly the kind of untested assertion this work keeps
# finding. If PowerShell mangles it after all, the claim is wrong and the refusal must widen.

# Deliberately OUTSIDE the execution corpus. Note the SPACE: a bare `nurse\` is rendered
# unquoted and is therefore safe, so the hazard needs a value that both requires quoting and
# ends in a backslash. PowerShell then hands `'nurse gift\'` to a native child as
# `"nurse gift\"`, and the Microsoft C runtime reads `\"` as an escaped quote -- the argument is
# mangled BELOW the renderer. The literal `_ps_quote` emits is correct and the child still gets
# the wrong value, so putting this in the gate corpus would fail the acceptance test for a
# PowerShell limitation the renderer cannot fix. Shape asserted; behaviour documented.
TRAILING_BACKSLASH_SEED = "nurse gift\\"

# Also outside the execution corpus, and it was INSIDE it until the first Windows run of this
# file. `'nurse"quote"'` arrives at the child as `nursequote` -- PowerShell 5.1 rebuilds the
# native command line without escaping an embedded quote and the C runtime eats it. The seed sat
# in the corpus unexercised from the day it was written, because the only test that would have
# caught it is skipped everywhere except the machine it had never run on.
#
# It differs from TRAILING_BACKSLASH_SEED in one way worth keeping straight: that value cannot be
# rendered exactly, this one can (`'nurse\\"quote\\"'` was measured byte-exact). It is refused by
# choice -- see `unsupported_value()` -- so the shape test below asserts the REFUSAL, not a
# round-trip.
DOUBLE_QUOTE_SEED = 'nurse"quote"'


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
        self.assertIn("python -m scripts.pipeline_status", out)

    def _json(self, *extra):
        """Stage 1 is owner-input and carries no command; satisfy it so the next actionable
        stage is stage 2, whose command does take a seed."""
        touch(os.path.join(self.ws, "Helium_10_Xray_a.xlsx"), self.t0)
        out = subprocess.run([sys.executable, "-m", "scripts.pipeline_status",
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

    PS_EXE = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                          "System32", "WindowsPowerShell", "v1.0", "powershell.exe")

    def _probe(self):
        """Prints its argv as UTF-8 JSON and nothing else. No logging, no banner, no prefix --
        the assertion reads the LAST stdout line, and anything chatty would break that contract.

        It writes BYTES rather than using print(). Its stdout here is a pipe, not a console, and
        a redirected Python stdout falls back to the ANSI code page on Windows -- so `print()`
        would raise UnicodeEncodeError on the non-ASCII seed and the failure would look like a
        renderer defect. Encoding the payload itself makes the probe independent of the
        environment it is measured in.
        """
        path = os.path.join(self.ws, "argv_probe.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("import json, sys\n"
                     "sys.stdout.buffer.write("
                     "json.dumps(sys.argv[1:], ensure_ascii=False).encode('utf-8'))\n")
        return path

    def _write_ps1(self, name, line):
        """One executable command line, written the way Windows PowerShell 5.1 must read it.

        utf-8-SIG, not utf-8. Windows PowerShell 5.1 reads a BOM-less .ps1 as the system ANSI
        code page, so a non-ASCII seed would be silently mojibaked before PowerShell ever
        parsed it -- and the test would then be measuring the file encoding, not the renderer.
        """
        path = os.path.join(self.ws, name)
        with open(path, "w", encoding="utf-8-sig") as fh:
            fh.write(line + "\n")
        return path

    def _run_ps1(self, path):
        return subprocess.run(
            [self.PS_EXE, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", path],
            capture_output=True, text=True, encoding="utf-8", cwd=self.ws)

    def _tree(self):
        """Relative path -> content hash, for every file under the workspace.

        Paths and hashes, not bare names. Names alone miss a file that was MODIFIED rather than
        created, and collapse two same-named files in different directories -- so a redirect that
        overwrote the probe would have read as no side effect at all.
        """
        out = {}
        for root, _dirs, files in os.walk(self.ws):
            for f in files:
                p = os.path.join(root, f)
                with open(p, "rb") as fh:
                    out[os.path.relpath(p, self.ws)] = hashlib.sha256(fh.read()).hexdigest()
        return out

    def _require_powershell(self):
        # On Windows this must EXIST. Skipping here would let a missing shell read as proof.
        self.assertTrue(os.path.isfile(self.PS_EXE), f"powershell.exe not found at {self.PS_EXE}")

    @staticmethod
    def _unquote(rendered):
        """Invert `_ps_quote` by PowerShell's single-quoted-literal rule: strip the wrapper and
        collapse `''` back to `'`. Bare values invert to themselves."""
        if rendered.startswith("'") and rendered.endswith("'") and len(rendered) >= 2:
            return rendered[1:-1].replace("''", "'")
        return rendered

    def test_the_seed_corpus_is_covered_by_the_shape_tests_on_every_platform(self):
        """Runs everywhere, so a non-Windows CI still exercises the whole corpus.

        Asserts ROUND-TRIP, not "is quoted". The corpus deliberately contains `nurse\\`, which
        renders BARE -- an earlier version of this test asserted every seed came back
        single-quoted and would have blocked adding the one supported-but-bare case the tool
        makes a claim about. What matters is that the rendered form decodes to the original,
        whichever branch produced it.

        This proves the renderer's own inverse. It does NOT prove PowerShell parses it back the
        same way; that is what the Windows test below is for.
        """
        for seed in ADVERSARIAL_SEEDS:
            rendered = P._ps_quote(seed)
            self.assertEqual(self._unquote(rendered), seed, f"{seed!r} -> {rendered!r}")
            if P.needs_quoting(seed):
                self.assertTrue(rendered.startswith("'") and rendered.endswith("'"), seed)
            else:
                self.assertEqual(rendered, seed, seed)

    def test_the_execution_corpus_and_the_shape_corpus_cannot_diverge(self):
        """They are one constant today. This asserts it by AST -- not by counting occurrences in
        the source, which would count its own assertion -- so splitting them later is a test
        failure rather than a silent hole in whichever one stops being exercised."""
        cls = next(n for n in ast.parse(inspect.getsource(sys.modules[__name__])).body
                   if isinstance(n, ast.ClassDef) and n.name == "TestWindowsPowerShellExecution")
        # Only module-level CONSTANTS, by ALL_CAPS naming. An earlier version collected every
        # `for x in name` loop and so counted ordinary locals -- `for f in files` in the tree
        # snapshot -- as if they were rival corpora. The invariant is about which CORPUS is
        # iterated, not about every loop in the class.
        looped = {n.iter.id for n in ast.walk(cls)
                  if isinstance(n, ast.For) and isinstance(n.iter, ast.Name)
                  and n.iter.id.isupper()}
        self.assertEqual(looped, {"ADVERSARIAL_SEEDS"},
                         f"every seed loop must iterate the one corpus; found {looped}")
        self.assertNotIn(TRAILING_BACKSLASH_SEED, ADVERSARIAL_SEEDS)
        # A refused value in the execution corpus would fail the gate for a limitation the
        # renderer is not allowed to fix. Both refused seeds are pinned out of it by name.
        self.assertNotIn(DOUBLE_QUOTE_SEED, ADVERSARIAL_SEEDS)

    def test_the_execution_proof_cannot_assert_the_marker_is_absent_from_stdout(self):
        """Regression for the defect the FIRST Windows run of this file found, and it runs on
        EVERY platform on purpose.

        The two execution proofs asserted `PWNED not in run.stdout` while also requiring the seed
        echoed back verbatim. Four corpus seeds carry that marker literally, so the two could
        never hold at once: a correct renderer is what makes the marker appear. The proof was
        unsatisfiable by construction, and it stayed invisible for as long as it was skipped --
        which was everywhere, because it had never run on Windows. The property that actually
        matters is that no EXTRA OUTPUT appeared, which is what those tests count now.

        Asserted by AST rather than by substring, so this test cannot match its own source.
        """
        marked = [s for s in ADVERSARIAL_SEEDS if "PWNED" in s]
        self.assertTrue(marked, "the corpus must keep at least one injection-marker seed")
        cls = next(n for n in ast.parse(inspect.getsource(sys.modules[__name__])).body
                   if isinstance(n, ast.ClassDef) and n.name == "TestWindowsPowerShellExecution")
        unsatisfiable = [n for n in ast.walk(cls)
                         if isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Attribute) and n.func.attr == "assertNotIn"
                         and len(n.args) >= 2
                         and isinstance(n.args[0], ast.Constant) and n.args[0].value == "PWNED"
                         and isinstance(n.args[1], ast.Attribute) and n.args[1].attr == "stdout"]
        self.assertEqual(unsatisfiable, [],
                         "an unsatisfiable stdout assertion came back; count the lines instead")

    def test_a_trailing_backslash_renders_correctly_even_though_powershell_mangles_it(self):
        """Known limitation, pinned so it cannot be mistaken for an untested case. The literal is
        right; PowerShell's native argument marshalling is what breaks, below the renderer.

        A bare `nurse\\` is NOT affected -- unquoted, there is no quote for the C runtime to
        mis-parse. The hazard needs a value that both requires quoting and ends in a backslash."""
        self.assertEqual(P._ps_quote("nurse\\"), "nurse\\")                    # bare: safe
        self.assertEqual(P._ps_quote(TRAILING_BACKSLASH_SEED), "'nurse gift\\'")  # quoted: hazard

    @unittest.skipUnless(sys.platform == "win32", "Windows PowerShell call-operator proof")
    def test_a_quoted_executable_needs_the_call_operator_to_run_at_all(self):
        """Isolates B1 with a CONTROLLED fixture, because `sys.executable` may or may not contain
        a space on any given machine -- so the test below could pass without ever exercising the
        `&`. Here the path is guaranteed to need quoting.

        Both directions are asserted: without `&` PowerShell echoes the path instead of running
        it, which is the defect; with `&` the command runs. A test that only checked the fix
        would not show that the hazard is real.
        """
        self._require_powershell()
        d = os.path.join(self.ws, "dir with space")
        os.makedirs(d, exist_ok=True)
        cmd = os.path.join(d, "marker.cmd")
        with open(cmd, "w", encoding="ascii") as fh:
            fh.write("@echo off\r\n@echo MARKER-OK\r\n")
        self.assertIn(" ", cmd, "the fixture path must actually contain a space")
        quoted = P._ps_quote(cmd)
        self.assertTrue(quoted.startswith("'"), "fixture must require quoting")

        without = self._run_ps1(self._write_ps1("no-amp.ps1", quoted))
        self.assertNotIn("MARKER-OK", without.stdout,
                         "a quoted first token ran as a command; B1 no longer reproduces")
        self.assertIn(cmd, without.stdout, "expected PowerShell to echo the path as a string")

        with_amp = self._run_ps1(self._write_ps1("amp.ps1", "& " + quoted))
        self.assertIn("MARKER-OK", with_amp.stdout, with_amp.stderr)

    @unittest.skipUnless(sys.platform == "win32",
                         "Windows PowerShell execution proof; the renderer targets that shell")
    def test_windows_powershell_renderer_preserves_exact_seed(self):
        self._require_powershell()
        probe = self._probe()

        for seed in ADVERSARIAL_SEEDS:
            # `&` is required, not stylistic: a line whose FIRST token is a quoted string is a
            # string EXPRESSION in PowerShell, not a command. Proved separately above.
            line = "& {py} {probe} --seed {seed}".format(
                py=P._ps_quote(sys.executable), probe=P._ps_quote(probe), seed=P._ps_quote(seed))
            self.assertTrue(line.startswith("& "), line)
            ps1 = self._write_ps1("paste.ps1", line)
            with open(ps1, "rb") as fh:
                self.assertEqual(fh.read(3), b"\xef\xbb\xbf", "the .ps1 must carry a UTF-8 BOM")
            # Closed explicitly. Leaked, this handle raised a ResourceWarning on stderr in the
            # MIDDLE of unittest's "... ok" line, which split the gate's result line in two and
            # made the capture script unable to see that the test had passed. A warning that
            # corrupts the evidence is not cosmetic.
            with open(ps1, encoding="utf-8-sig") as fh:
                self.assertEqual(len([l for l in fh if l.strip()]), 1)

            # Snapshot per iteration, AFTER the .ps1 is written: the script file changes on
            # purpose every time, so a single up-front baseline could only be compared by
            # ignoring it -- and ignoring it is how a redirect that overwrote it would hide.
            before = self._tree()
            run = self._run_ps1(ps1)

            self.assertEqual(run.returncode, 0, f"{seed!r}: {run.stderr}")
            self.assertEqual(run.stderr.strip(), "", f"{seed!r} wrote to stderr: {run.stderr!r}")
            argv = json.loads(run.stdout.strip().splitlines()[-1])
            # 1. one argument, 2. exact value -- no normalising, no trimming
            self.assertEqual(argv, ["--seed", seed], f"{seed!r} was mangled: {argv!r}")
            self.assertEqual(len(argv), 2, argv)
            # 3. no extra command executed. The probe prints exactly ONE line -- its argv echo --
            #    so a second line is output PowerShell produced on its own. Substring-matching
            #    "PWNED" in stdout cannot work here and never could: four corpus seeds carry that
            #    marker literally and assertion 1 requires it echoed back verbatim. Counting lines
            #    is also stricter -- it catches an injected command that prints anything at all,
            #    and assertion 1 reads only the LAST line, so extra output above it would hide.
            printed = [ln for ln in run.stdout.splitlines() if ln.strip()]
            self.assertEqual(len(printed), 1, f"{seed!r} produced extra stdout: {printed!r}")
            self.assertNotIn("PWNED", run.stderr, seed)
            # 4. no filesystem side effect ANYWHERE: added, removed, or modified. A single
            #    sentinel check would miss a redirect whose target name came from the seed.
            self.assertEqual(self._tree(), before, f"{seed!r} changed the workspace")

    @unittest.skipUnless(sys.platform == "win32", "Windows PowerShell execution proof")
    def test_windows_powershell_runs_the_real_next_command_shape(self):
        """The same proof through `_fmt_command` rather than `_ps_quote` directly, so the
        template-substitution path is covered too, workspace argument included."""
        self._require_powershell()
        probe = self._probe()
        seed = "nurse'; Write-Host PWNED; # $5"
        line = P._fmt_command(
            "& {py} {probe} --workspace {{workspace}} --seed {{seed}}".format(
                py=P._ps_quote(sys.executable), probe=P._ps_quote(probe)),
            self.ws, seed)
        run = self._run_ps1(self._write_ps1("paste2.ps1", line))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout.strip().splitlines()[-1]),
                         ["--workspace", self.ws, "--seed", seed])
        # No extra command executed -- by line count, not by marker substring. This seed carries
        # the marker too, so its absence from stdout would contradict the exact echo above.
        printed = [ln for ln in run.stdout.splitlines() if ln.strip()]
        self.assertEqual(len(printed), 1, f"extra stdout: {printed!r}")


class TestCaptureScriptContract(unittest.TestCase):
    """Source-level guards on `Capture-PipelineStatusEvidence.ps1`.

    The script had NO test coverage of any kind, which is why two defects survived six review
    rounds and surfaced only when it was finally executed. Both were of a shape a reader will
    reproduce: an assertion that looks strict and checks the wrong thing.

    These run on every platform, because reading a file does not need PowerShell -- and because a
    guard that only ran on Windows would have the same blind spot as the defects it guards.
    """

    SCRIPT = os.path.join(ROOT, "Capture-PipelineStatusEvidence.ps1")

    def setUp(self):
        if not os.path.isfile(self.SCRIPT):
            self.skipTest("capture script absent from this tree")
        with open(self.SCRIPT, encoding="utf-8-sig") as fh:
            self.src = fh.read()

    def _body(self):
        """Everything after the comment-based help, so the .NOTES prose describing a defect is
        not mistaken for the defect itself."""
        marker = "\n#>"
        return self.src.split(marker, 1)[1] if marker in self.src else self.src

    def _code(self):
        """The body with comments removed -- BOTH line comments and `<# ... #>` blocks.

        Needed because these guards forbid literals that the script's own comments must be free
        to QUOTE while explaining why they are wrong. Two versions of this helper were wrong
        before this one: the first ignored comments entirely and failed on the line comment
        documenting the defect it guards, the second stripped only whole-line comments and failed
        on a block comment's interior, whose lines do not begin with `#`. Same trap as the corpus
        check that would otherwise count its own assertion.
        """
        out, in_block = [], False
        for ln in self._body().splitlines():
            s = ln.strip()
            if in_block:
                if "#>" in s:
                    in_block = False
                continue
            if s.startswith("<#"):
                if "#>" not in s[2:]:
                    in_block = True
                continue
            if s.startswith("#"):
                continue
            out.append(ln)
        return "\n".join(out)

    def _cli_invocations(self):
        """Only the lines that actually hand a value to the CLI.

        The marshalling characterization table deliberately CONTAINS the trap values -- measuring
        what the shell does to them is its entire job -- so a guard scanning the whole file would
        forbid exactly the evidence it wants. Scope the claim to where the claim applies.
        """
        return [ln for ln in self._code().splitlines()
                if "scripts.pipeline_status" in ln and "Invoke-Capture" in ln]

    def test_the_trailing_backslash_seed_is_doubled(self):
        """D13. `--seed "nurse gift\\"` cannot test the trailing-backslash refusal: PowerShell's
        native marshalling -- the very defect under test -- turns it into `nurse gift"` before the
        tool sees it, so the tool refuses it for containing a DOUBLE QUOTE and the script records
        the result as proof about backslashes. Measured, in this shell:

            PS value `nurse gift\\`   -> child receives  nurse gift"
            PS value `nurse gift\\\\`  -> child receives  nurse gift\\

        Only the doubled form delivers the value the step is about."""
        lines = [ln for ln in self._cli_invocations() if "nurse gift" in ln]
        self.assertTrue(lines, "the trailing-backslash refusal step disappeared")
        for ln in lines:
            self.assertIn('"nurse gift\\\\"', ln,
                          "the trailing-backslash seed must be doubled to survive marshalling")
            self.assertNotIn('"nurse gift\\"', ln.replace('"nurse gift\\\\"', ""),
                             "a single-backslash seed never reaches the tool intact")

    def test_the_double_quote_seed_is_escaped(self):
        """D14 -- D13 one step lower, and made by the same hand in the same sitting.

        The general rule: a value the tool refuses BECAUSE this shell cannot deliver it intact
        cannot be delivered to the tool by this shell. A plain `nurse"quote"` arrives as
        `nursequote`, an ordinary valid seed, and the tool correctly returns 0 -- so the step
        proves nothing and the gate throws. Only the backslash-escaped form carries the quote:

            PS value `nurse\\"quote\\"`  -> child receives  nurse"quote"
        """
        lines = [ln for ln in self._cli_invocations() if "quote" in ln]
        self.assertTrue(lines, "the double-quote refusal step disappeared")
        for ln in lines:
            self.assertIn(r'nurse\"quote\"', ln,
                          "the double-quote seed must be escaped to survive marshalling")
            self.assertNotIn("'nurse\"quote\"'", ln,
                             "a bare double-quote seed never reaches the tool intact")

    def test_every_refusal_assertion_names_its_reason(self):
        """Both refusals exit 2. An exit code alone cannot tell them apart, so a step asserting
        only the code passes whenever anything at all goes wrong -- which is exactly what happened:
        the backslash step passed on a double-quote refusal. Each refusal must be pinned by cause."""
        body = self._code()
        self.assertIn("ends in a backslash", body,
                      "the backslash refusal must be asserted by reason, not by exit code alone")
        self.assertIn("contains a double quote", body,
                      "the double-quote refusal must be asserted by reason too")

    def test_json_is_parsed_from_stdout_never_from_a_merged_stream(self):
        """D12. Merging stdout and stderr and then parsing the result cannot work: every refusal
        path writes a sentence to stderr and the document to stdout, so the combined text is not
        JSON. It threw `Invalid JSON primitive: seed` on the first run that reached a refusal.

        The first fix extracted the object back out of the merged text. This asserts the second
        and better one -- do not merge what you intend to parse. stdout is captured separately and
        is the only thing parsed."""
        body = self._code()
        self.assertIn("function ConvertFrom-CapturedStdout", self.src)
        self.assertIn("2>$errFile", body,
                      "Invoke-Capture must send stderr to its own file, not into stdout")
        # Merging is not banned outright -- it is banned wherever the result is PARSED. The one
        # remaining merge is `python --version`, whose output is recorded as a string and never
        # decoded; on Python 2 it went to stderr, so merging is what makes it robust. Pinning the
        # exception by name keeps the rule narrow and keeps a new merge from arriving unnoticed.
        for line in body.splitlines():
            if "2>&1" in line:
                self.assertIn("--version", line,
                              f"a parsed capture merges the streams: {line.strip()}")
            if "ConvertFrom-Json" in line and "Get-Content" in line:
                self.fail(f"a captured file is parsed as whole JSON: {line.strip()}")

    def test_the_read_only_claim_is_measured_across_the_tool_alone(self):
        """D16. The snapshot pair used to span the whole run -- focused tests, boundary tests,
        connectivity scan and the 4781-test suite included -- while the throw named
        `scripts.pipeline_status` as the thing that changed the workspace.

        Run 3 fired it on a workspace the module had not touched: zero content differences, zero
        size differences, ten files under phase6/6E rewritten byte-identically with new mtimes by
        the suite. The assertion was true and its stated cause was false, which is the defect this
        branch exists to remove. A third snapshot now brackets the tool's own commands."""
        body = self._code()
        self.assertIn("$MiddleSnapshot", body,
                      "a snapshot must bracket the pipeline-status commands alone")
        self.assertIn("runs-T2-diff-after-tests.txt", body,
                      "the suite's own delta must be recorded as a separate artifact")
        self.assertIn("runs_T2_changed_by_pipeline_status", body)
        self.assertIn("runs_T2_changed_by_test_suite", body)

    def test_the_connectivity_scan_side_effect_is_declared_not_ignored(self):
        """D15. `scripts/connectivity_scan.py` rewrites the tracked
        CONNECTED-RESEARCH-NETWORK-SCAN.json, so -ConnectivityScan made the clean-tree assertion
        fail for doing what the flag asks. The fix must SCOPE the check, not remove it: anything
        undeclared still fails."""
        body = self._code()
        self.assertIn("CONNECTED-RESEARCH-NETWORK-SCAN.json", body)
        self.assertIn("$Undeclared", body,
                      "undeclared working-tree changes must still fail the capture")


class TestInputHygiene(Base):
    def test_a_wildcard_does_not_match_on_overlapping_head_and_tail(self):
        """`startswith(head) and endswith(tail)` alone accepts `abcd` for `abc*bcd`, because the
        head and the tail overlap. A phantom match makes a stage look READY on a file that is
        not its artifact."""
        touch(os.path.join(self.ws, "abcd"), self.t0)
        self.assertEqual(P._resolve(self.ws, "abc*bcd"), [])
        touch(os.path.join(self.ws, "abcXbcd"), self.t0)
        self.assertEqual([os.path.basename(p) for p in P._resolve(self.ws, "abc*bcd")], ["abcXbcd"])

    def test_every_c0_control_character_and_del_is_named(self):
        """Rejected before any subprocess is built -- an embedded NUL cannot even be passed as an
        argument on most platforms, so catching it at the boundary is the only place it works."""
        self.assertEqual(P.unsupported_characters("nurse shirt"), "")
        for ch, shown in (("\n", "\\n"), ("\r", "\\r"), ("\t", "\\t"),
                          ("\x00", "\\x00"), ("\x1b", "\\x1b"), ("\x7f", "\\x7f")):
            self.assertIn(shown, P.unsupported_characters(f"a{ch}b"), repr(ch))

    def test_a_quote_requiring_trailing_backslash_is_refused_not_emitted(self):
        """Decision taken under the 2026-08-01 pre-Windows review: option B, reject.

        Calling it a PowerShell limitation and documenting it still left a value that looks
        supported and arrives at the engine changed. The tool either preserves a value exactly or
        emits nothing. The refusal is exactly as wide as the defect -- a BARE trailing backslash
        is unaffected and stays supported."""
        self.assertEqual(P.unsupported_value("nurse\\"), "")           # bare: still supported
        self.assertIn("backslash", P.unsupported_value("nurse gift\\"))
        self.assertIn("backslash", P.unsupported_value("nurse'gift\\"))
        self.assertEqual(P.unsupported_value("nurse gift"), "")

    def test_a_double_quote_is_refused_not_silently_dropped(self):
        """Found by the FIRST Windows execution of the gate, 2026-08-01 -- a real defect that had
        been masked by a second one in the test that should have caught it.

        `'nurse"quote"'` reaches the child as `nursequote`. The owner would have searched a
        keyword they never typed and had no way to see it, which is worse than an error.

        Refused by choice rather than by impossibility: `'nurse\\"quote\\"'` was measured to arrive
        byte-exact, so the assertion here is that the tool declines to render a value it COULD
        render, and says why. The refusal is exactly as wide as the character -- a single quote,
        the far more likely one in a real keyword, is still escaped and supported."""
        self.assertIn("double quote", P.unsupported_value(DOUBLE_QUOTE_SEED))
        self.assertIn("double quote", P.unsupported_value('say "hi"', "workspace"))
        self.assertEqual(P.unsupported_value("nurse's gift"), "")      # single quote: supported
        self.assertEqual(P._ps_quote("nurse's gift"), "'nurse''s gift'")

    def test_the_workspace_is_validated_on_the_same_terms_as_the_seed(self):
        """It is substituted into the same printed line and carries the same hazards."""
        self.assertIn("backslash", P.unsupported_value(r"C:\My Runs\T2" + "\\", "workspace"))
        self.assertEqual(P.unsupported_value("runs/T2", "workspace"), "")

    def _refused(self, *args):
        touch(os.path.join(self.ws, "Helium_10_Xray_a.xlsx"), self.t0)
        return subprocess.run([sys.executable, "-m", "scripts.pipeline_status",
                               "--workspace", self.ws, *args],
                              capture_output=True, text=True, cwd=ROOT)

    def test_a_seed_with_a_newline_is_refused_and_no_command_is_emitted(self):
        out = self._refused("--seed", "nurse\nshirt")
        self.assertEqual(out.returncode, 2)
        self.assertIn("cannot be printed in a command", out.stderr)
        self.assertIn("\\n", out.stderr)
        self.assertEqual(out.stdout, "", "a refused seed must emit no command at all")

    def test_a_refused_seed_reports_a_structured_error_in_json_mode(self):
        """A machine consumer must not have to scrape stderr, and next_command must stay null."""
        out = self._refused("--json", "--seed", "nurse gift\\")
        self.assertEqual(out.returncode, 2)
        doc = json.loads(out.stdout)
        self.assertEqual(doc["error"], "UNSUPPORTED_VALUE")
        self.assertEqual(doc["field"], "seed")
        self.assertIsNone(doc["next_command"])
        self.assertFalse(doc["next_command_needs_seed"])
        self.assertIn("backslash", doc["detail"])

    def test_an_ordinary_seed_still_runs(self):
        for seed in ("nurse sweatshirt", "nurse\\", "nurse $5 gift"):
            out = self._refused("--seed", seed)
            self.assertEqual(out.returncode, 0, f"{seed!r}: {out.stderr}")


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
        with open(os.path.join(ROOT, "scripts", "pipeline_status.py"), encoding="utf-8") as fh:
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
        run = subprocess.run([sys.executable, "-m", "scripts.pipeline_status",
                              "--workspace", self.ws, "--seed", "nurse sweatshirt"],
                             capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(snapshot(), before, "the read-only CLI changed the workspace")

    def test_no_qualified_write_or_exec_call_exists(self):
        """Classifies the RECEIVER, not the bare attribute name. The earlier scan flagged
        `text.replace` as if it were `os.replace`; this one distinguishes them, so a real
        `os.replace` cannot hide behind that false positive being waved through."""
        with open(os.path.join(ROOT, "scripts", "pipeline_status.py"), encoding="utf-8") as fh:
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
        with open(os.path.join(ROOT, "scripts", "pipeline_status.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        used = {n.attr for n in ast.walk(tree)
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                and n.value.id == "os"}
        self.assertTrue(used <= {"listdir", "path"}, sorted(used))

    def test_missing_workspace_is_an_error_not_a_crash(self):
        out = subprocess.run([sys.executable, "-m", "scripts.pipeline_status",
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

    def test_no_two_declared_outputs_of_a_stage_can_match_the_same_file(self):
        """Required-all is defeated if one file satisfies two output slots: the stage looks
        complete on a single artifact. The overlap guard in `_resolve` stops one PATTERN matching
        a phantom name; this stops two patterns collapsing onto one real name."""
        def can_share(a, b):
            # Case-INSENSITIVE. On Windows `PRODUCT-PAGE.json` and `product-page.json` are one
            # file, so a case-only difference between two output slots is not a difference.
            a, b = a.lower(), b.lower()
            if "*" not in a and "*" not in b:
                return a == b
            if "*" in a and "*" in b:
                self.fail(f"two wildcards in one stage ({a}, {b}); extend this check")
            lit, pat = (a, b) if "*" not in a else (b, a)
            head, tail = pat.split("*", 1)
            return (len(lit) >= len(head) + len(tail)
                    and lit.startswith(head) and lit.endswith(tail))

        for st in P.STAGES:
            for i, a in enumerate(st.produces):
                for b in st.produces[i + 1:]:
                    self.assertFalse(can_share(a, b),
                                     f"stage {st.n}: {a} and {b} can be satisfied by one file")

    def test_every_command_starts_with_a_bare_literal_token(self):
        """Why no printed command needs PowerShell's `&`. A line whose FIRST token is a quoted
        string is a string expression, not a command -- so a template leading with a substituted
        value would silently stop executing. None does: every one starts with a literal `python`.
        If that ever changes, the renderer must emit `& ` and this test is the tripwire."""
        for st in P.STAGES:
            if st.command:
                head = st.command.split()[0]
                self.assertEqual(head, "python", f"stage {st.n} leads with {head!r}")
                self.assertNotIn("{", head, st.command)

    def test_every_printed_command_satisfies_its_target_module_cli(self):
        """This module's ENTIRE job is printing the command to run next, and nothing checked that
        those commands run. `test_every_command_starts_with_a_bare_literal_token` above inspects
        the FIRST token only, which a command missing its required positional also passes.

        Measured when this test was written: 7 of 12 printed commands failed with argparse exit 2
        because the template omitted the workspace positional the target module declares -- among
        them the first command a fresh workspace emits, and the command the multi-output
        staleness remediation sends the owner to after correctly detecting STALE.

        The workspace is deliberately a path that does NOT exist, so a module whose CLI contract
        IS satisfied fails on the missing directory rather than doing real work. Only an argparse
        contract failure is reported here.
        """
        missing_ws = os.path.join(self.ws, "no-such-workspace")
        env = {**os.environ, "PYTHONPATH": ROOT, "PYTHONDONTWRITEBYTECODE": "1"}
        broken = []
        for st in P.STAGES:
            if not st.command:
                continue
            argv = ["nurse sweatshirt" if t == "{seed}" else
                    missing_ws if t == "{workspace}" else t
                    for t in st.command.split()]
            run = subprocess.run([sys.executable] + argv[1:], cwd=self.ws, env=env,
                                 capture_output=True, text=True, timeout=120)
            blob = (run.stderr or "") + (run.stdout or "")
            # Every way a printed command can be malformed, not just the one that was shipped.
            # A missing positional was the live defect; a command naming a module that does not
            # exist is exactly as broken, and the first version of this test did not catch it --
            # measured by mutating stage 11 to a nonexistent module and watching this pass.
            for symptom in ("the following arguments are required", "unrecognized arguments",
                            "No module named", "invalid choice", "expected one argument"):
                if symptom in blob:
                    last = [ln for ln in blob.strip().splitlines() if ln.strip()][-1]
                    broken.append(f"  stage {st.n:>2} | {st.command}\n       -> {last.strip()}")
                    break
        self.assertEqual(broken, [], "printed commands that cannot run as printed:\n"
                                     + "\n".join(broken))

    def test_json_mode_is_valid_json(self):
        out = subprocess.run([sys.executable, "-m", "scripts.pipeline_status",
                              "--workspace", self.ws, "--json"],
                             capture_output=True, text=True, cwd=ROOT)
        import json
        doc = json.loads(out.stdout)
        self.assertEqual(doc["workspace"], self.ws)
        self.assertEqual(len(doc["stages"]), len(P.STAGES))


if __name__ == "__main__":
    unittest.main()
