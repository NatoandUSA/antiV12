#!/usr/bin/env python3
"""
Phase 6B — the CLI must write into the workspace it was given, and nowhere else.

WHY THIS FILE EXISTS. `write_phase6b_artifacts` resolves its destination as

    workspace_dir = workspace_dir or os.path.join(_ROOT, "runs", result.workspace_id)

which is correct for a caller that supplies `workspace_dir` and silently wrong for one that does
not: the fallback is derived from the workspace's own recorded identity, not from the path the
owner selected. `main()` did not supply it, so `python -m listing.keyword_allocation_planner
<some-workspace>` wrote into `<repo>/runs/<workspace_id>` while verifying `<some-workspace>`. On
2026-08-04 that rewrote four artifacts in the owner's real, quarantined `runs/T2` from a clone
that merely carried T2's identity.

WHY THE TEST DRIVES `main()`. The defect is not in the writer, which honours `workspace_dir`
whenever it is passed. It is in the one call site that omits it. A test that calls
`write_phase6b_artifacts` directly passes both before and after the fix and proves nothing, so
these tests go through the CLI entry point and assert on the filesystem afterwards.

WHY THE SELECTED WORKSPACE ALREADY HOLDS A VALID 6B PACKAGE. Without one, the misdirected run
fails verification for the accidental reason that the verified directory is empty, and the test
would pass for a reason unrelated to the defect. The workspace that triggered the real incident
was a CLONE, so it carried a complete prior package; verification of that stale-but-internally-
consistent package succeeded and the run printed `verify_ok=True` while its output had gone
somewhere else entirely. That false pass is the half of the incident that would have hidden it
indefinitely, so the fixture reproduces it.

WHY A WHOLE-TREE MANIFEST. Asserting only "the decoy is unchanged" is a name-keyed check: it holds
for the one wrong destination we thought of. The assertion here is that NOTHING outside the
selected workspace changed, so any other wrong destination fails it too. Identity is compared on
content AND `mtime_ns`, because an atomic rewrite with identical bytes still moves the timestamp.

`_ROOT` is redirected inside the test process only. Nothing here reads, writes, or looks at the
real `runs/`.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "listing"))
sys.path.insert(0, os.path.join(ROOT, "core"))

from production import product_workspace as PW      # noqa: E402
import keyword_allocation_planner as KAP            # noqa: E402

# The smallest keyword source Phase 6A accepts as an authoritative lean source.
KEYWORDS = [
    {"keyword": "nurse sweatshirt", "tier": "A_CORE"},
    {"keyword": "nurses sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "sweatshirt for nurses", "tier": "B_SUPPORTING"},
    {"keyword": "gifts for nurses", "tier": "B_SUPPORTING"},
    {"keyword": "nurse gifts", "tier": "B_SUPPORTING"},
    {"keyword": "rn sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "cna sweatshirt", "tier": "B_SUPPORTING"},
    {"keyword": "icu nurse sweatshirt", "tier": "B_SUPPORTING"},
]

DECOY_SENTINEL = "this file stands in for the owner's real, quarantined workspace\n"


def _tree_manifest(root):
    """{relative path: (sha256, mtime_ns)} for every file under root.

    Content alone is not enough: the incident this file guards rewrote ten files while only two
    changed bytes, and only the timestamps showed the rest.
    """
    out = {}
    for base, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            p = os.path.join(base, name)
            rel = os.path.relpath(p, root).replace("\\", "/")
            with open(p, "rb") as f:
                out[rel] = (hashlib.sha256(f.read()).hexdigest(), os.stat(p).st_mtime_ns)
    return out


class CliWorkspaceIsolation(unittest.TestCase):
    """The workspace on the command line is the only workspace the CLI may write to."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="6b-isolation-")
        self.addCleanup(shutil.rmtree, self.base, True)

        # A fake repository root, redirected for this process only. The buggy fallback resolves
        # against _ROOT, so this is what keeps the real repository out of the test.
        self.fake_root = os.path.join(self.base, "fake-repo")
        self.decoy = os.path.join(self.fake_root, "runs", "T2")
        os.makedirs(self.decoy)
        with open(os.path.join(self.decoy, "SENTINEL.txt"), "w", encoding="utf-8") as f:
            f.write(DECOY_SENTINEL)

        # The workspace the owner actually selects. It lives OUTSIDE the fake repository and it
        # carries T2's identity, which is the collision that made the fallback destructive.
        self.selected = os.path.join(self.base, "selected-workspace")
        os.makedirs(self.selected)
        lean = {"run_metadata": {"run_id": "kwrun_isolation",
                                 "source_schema": "master_keywords_lean"},
                "target_profile": {"seed": "nurse sweatshirt"}, "keywords": KEYWORDS}
        with open(os.path.join(self.selected, "MASTER-KEYWORDS-LEAN.json"), "w",
                  encoding="utf-8") as f:
            json.dump(lean, f)
        with open(os.path.join(self.selected, "PROJECT-MANIFEST.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"project_id": "T2"}, f)
        result = PW.build_product_workspace(self.selected, workspace_id="T2")
        PW.write_phase6a_artifacts(result, dest_dir=os.path.join(self.selected, "phase6", "6A"),
                                   workspace_dir=self.selected, started_at="T0", completed_at="T1")

        # A complete, valid, prior 6B package -- written through the API the correct way, which is
        # what a cloned workspace carries. See the module docstring for why this matters.
        plan = KAP.plan_keyword_allocation(self.selected)
        KAP.write_phase6b_artifacts(plan, workspace_dir=self.selected,
                                    started_at="prior", completed_at="prior")

        self._real_root = KAP._ROOT
        KAP._ROOT = self.fake_root
        self.addCleanup(setattr, KAP, "_ROOT", self._real_root)

    def _run_cli(self):
        """Run the CLI on the selected workspace, recording where verification looked.

        The directory actually WRITTEN is never taken from the module's own arithmetic -- it is
        read back off the filesystem in the assertions, so a wrong destination cannot agree with
        a wrong expectation.
        """
        seen = {"verify_dirs": [], "writer_calls": 0}
        real_verify, real_write = KAP.verify_phase6b_artifacts, KAP.write_phase6b_artifacts

        def spy_verify(dest_dir, workspace_dir=None):
            seen["verify_dirs"].append(os.path.abspath(dest_dir))
            return real_verify(dest_dir, workspace_dir=workspace_dir)

        def spy_write(result, *a, **kw):
            seen["writer_calls"] += 1
            return real_write(result, *a, **kw)

        KAP.verify_phase6b_artifacts, KAP.write_phase6b_artifacts = spy_verify, spy_write
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                seen["rc"] = KAP.main([self.selected])
        finally:
            KAP.verify_phase6b_artifacts, KAP.write_phase6b_artifacts = real_verify, real_write
        seen["stdout"] = buf.getvalue()
        return seen

    def _written_6b_dir(self):
        """Where the run's artifacts actually landed, observed rather than recomputed."""
        found = set()
        for base, _dirnames, filenames in os.walk(self.base):
            if os.path.basename(base) == "6B" and KAP.ALLOCATION_MAP_FILENAME in filenames:
                found.add(os.path.abspath(base))
        return found

    def test_cli_writes_only_inside_the_selected_workspace(self):
        before = _tree_manifest(self.base)
        seen = self._run_cli()

        # Non-vacuity: a test that never reached the writer would satisfy every assertion below
        # by doing nothing at all.
        self.assertEqual(seen["writer_calls"], 1, "the CLI never invoked the Phase 6B writer")

        after = _tree_manifest(self.base)
        changed = sorted(set(before) ^ set(after)) + \
            sorted(p for p in (set(before) & set(after)) if before[p] != after[p])
        selected_prefix = os.path.relpath(self.selected, self.base).replace("\\", "/") + "/"
        stray = sorted(p for p in changed if not p.startswith(selected_prefix))
        self.assertEqual(stray, [], "the CLI wrote outside the workspace it was given: %s" % stray)

        # And THIS run must be what produced them. "No stray writes" is satisfied by a run that
        # wrote somewhere this test cannot see -- the real repository, for instance, which is
        # exactly what a fallback recomputed inline from __file__ instead of _ROOT would do.
        # setUp seeded the prior package with started_at="prior"; the CLI passes None, which the
        # manifest omits, so the key's disappearance proves the CLI wrote HERE.
        manifest_path = os.path.join(self.selected, "phase6", "6B", KAP.STAGE_MANIFEST_FILENAME)
        self.assertTrue(os.path.isfile(manifest_path))
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertNotEqual(manifest.get("started_at"), "prior",
                            "the selected workspace still holds the package setUp seeded, so the "
                            "CLI wrote its output somewhere this test cannot observe")
        self.assertTrue(os.path.isfile(os.path.join(self.selected, "phase6", "6B",
                                                    KAP.ALLOCATION_MAP_FILENAME)))
        self.assertEqual(seen["rc"], 0, "the CLI reported failure")

    def test_decoy_workspace_is_untouched_on_content_and_mtime(self):
        decoy_before = _tree_manifest(self.decoy)
        seen = self._run_cli()
        # Non-vacuity, repeated per test on purpose: "nothing was modified" is also what a run
        # that never happened looks like.
        self.assertEqual(seen["writer_calls"], 1, "the CLI never invoked the Phase 6B writer")
        decoy_after = _tree_manifest(self.decoy)
        self.assertEqual(decoy_before, decoy_after,
                         "a workspace that merely shares an identity was modified")

    def test_verified_directory_is_the_directory_written(self):
        """A pass must never be reported for a directory other than the one written.

        This is the half of the incident that would have hidden it indefinitely: the run printed
        `verify_ok=True` because it verified a different, internally consistent workspace.
        """
        seen = self._run_cli()
        self.assertEqual(len(seen["verify_dirs"]), 1)
        verified = seen["verify_dirs"][0]

        written = self._written_6b_dir()
        self.assertEqual(len(written), 1,
                         "6B artifacts exist in more than one workspace: %s" % sorted(written))
        self.assertEqual(verified, written.pop(),
                         "verification ran against a different directory than the one written")

        # The owner-visible symptom, asserted on the printed line itself.
        if "verify_ok=True" in seen["stdout"]:
            self.assertEqual(seen["rc"], 0)
            self.assertIn(os.path.abspath(self.selected), verified,
                          "reported success for a directory outside the selected workspace")


if __name__ == "__main__":
    unittest.main()
