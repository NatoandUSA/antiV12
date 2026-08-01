#!/usr/bin/env python3
"""Where am I in the pipeline, and what do I run next?

READ-ONLY. This module runs nothing, writes nothing and decides nothing. It looks at which
artifacts exist in a workspace, compares their modification times, and prints the command the
owner would type next. Every stage's status is derived from files on disk, never inferred from a
stored "progress" record that could drift out of step with reality.

Four states, deliberately. A richer model would need per-stage semantics this module does not have
and must not invent:

    MISSING   no output artifact exists yet
    STALE     an output exists but an INPUT is newer, so the output no longer reflects its input
    READY     every declared output exists and is newer than every input
    BLOCKED   a required input does not exist, so the stage cannot run at all

STALE is the only derived signal here, and it is mechanical: OLDEST output mtime < newest input
mtime. Oldest, not newest: a stage that declares several outputs is only as current as its least
current one, and comparing the newest would let a fresh sibling hide a stale artifact. It is not
a quality judgement. This module never reads a metric out of an artifact, never scores anything and
never says a listing is good -- the engines own all of that.

The two owner-supplied inputs (the Helium 10 Xray export and the Cerebro exports) are listed as
stages because a pipeline that silently waits on a missing file is exactly how a session stalls.

Post-launch PPC stages (report ingestion onward) are deliberately NOT modelled here: they have
their own accepted Phase 7 authorities and their own workspace under phase7/. This module points at
them and stops.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

WORKSPACE_DEFAULT = os.path.join("runs", "T2")
PHASE7_SUBDIR = "phase7"

OWNER_INPUT = "owner-input"


class Stage:
    """One pipeline step: what proves it ran, what it needs, and how to run it.

    OUTPUT SEMANTICS ARE REQUIRED-ALL, and are stated here because the staleness rule is only
    correct under that reading. Every pattern in `produces` is a required current artifact:

      * required-all      -- what this class means. All patterns must match, and the stage is
                             only as current as its least current one.
      * required-any      -- NOT supported. A flat list cannot express "either of these".
      * optional outputs  -- NOT supported. An optional pattern in this list would produce a
                             false STALE, because the staleness minimum would include an
                             artifact nothing promised to keep current.

    If a stage ever needs any-of or optional outputs, model them explicitly -- a nested group,
    or a separate `optional_produces` -- rather than adding them to this flat list and hoping
    the comparison still holds. `test_every_declared_output_is_required` fails if the shipped
    table stops honouring required-all.

    `needs` is required-all too: a stage with any unmatched input is BLOCKED.
    """

    def __init__(self, n, title, produces, needs=(), command=None, note=""):
        self.n = n
        self.title = title
        self.produces = list(produces)           # required-all; see the class docstring
        self.needs = list(needs)
        self.command = command                   # None => the owner supplies this file by hand
        self.note = note

    @property
    def is_owner_input(self):
        return self.command is None


# The pipeline, in the owner's order. Artifact names and command shapes were both read from the
# modules themselves rather than assumed; a module that changes its output name will surface here
# as MISSING, which is the correct and visible failure.
STAGES = [
    Stage(1, "Helium 10 Xray export", ["Helium_10_Xray_*.xlsx"],
          note="drop the H10 Xray export into the workspace"),
    Stage(2, "ASIN candidates", ["ASIN-CANDIDATES.json"], ["Helium_10_Xray_*.xlsx"],
          "python -m research.asin_candidates --seed {seed}"),
    Stage(3, "10-ASIN batches", ["ASIN-BATCHES.json"], ["ASIN-CANDIDATES.json"],
          "python -m research.asin_batches --seed {seed}"),
    Stage(4, "Cerebro exports", ["US_AMAZON_cerebro_*.xlsx"], ["ASIN-BATCHES.json"],
          note="run Cerebro on those batches in H10, drop the exports in"),
    Stage(5, "Master keyword list", ["MASTER-KEYWORDS-LEAN.json", "CEREBRO-EVIDENCE-MATRIX.json"],
          ["US_AMAZON_cerebro_*.xlsx"],
          "python -m research.master_keyword_builder --seed {seed}"),
    Stage(6, "Keyword intelligence", ["KEYWORD-INTELLIGENCE.json"], ["MASTER-KEYWORDS-LEAN.json"],
          "python -m research.keyword_intelligence {workspace}"),
    Stage(7, "Competitor gap", ["COMPETITOR-GAP.json"], ["Helium_10_Xray_*.xlsx"],
          "python -m research.competitor_gap_analyzer {workspace}"),
    Stage(8, "Normalized keyword source", ["NORMALIZED-KEYWORD-SOURCE.json"],
          ["MASTER-KEYWORDS-LEAN.json"],
          "python -m listing.keyword_source_adapter {workspace} --write"),
    Stage(9, "Claim evidence", ["CLAIM-EVIDENCE.json"], ["NORMALIZED-KEYWORD-SOURCE.json"],
          "python -m listing.claim_evidence {workspace} --write"),
    Stage(10, "Listing brief", ["LISTING-BRIEF.json"],
          ["NORMALIZED-KEYWORD-SOURCE.json", "CLAIM-EVIDENCE.json"],
          "python -m listing.listing_generator {workspace} --write-listing"),
    Stage(11, "Product detail page", ["PRODUCT-PAGE.json", "BACKEND-SEARCH-TERMS.json"],
          ["LISTING-BRIEF.json", "CLAIM-EVIDENCE.json"],
          "python -m production.product_detail_page --write"),
    Stage(12, "A+ content", ["APLUS-ASSET-MANIFEST.json"], ["PRODUCT-PAGE.json"],
          "python -m production.aplus_assembly --write"),
    Stage(13, "Creative package", ["CREATIVE-ASSET-CHECKLIST.json"], ["PRODUCT-PAGE.json"],
          "python -m creative.creative_production_package --write"),
    Stage(14, "Seller Central package", ["SELLER-CENTRAL-MANUAL-ENTRY-PLAN.json"],
          ["PRODUCT-PAGE.json", "APLUS-ASSET-MANIFEST.json"],
          "python -m production.seller_central_package"),
]

MISSING, STALE, READY, BLOCKED = "MISSING", "STALE", "READY", "BLOCKED"
_MARK = {READY: "ok", STALE: "STALE", MISSING: "--", BLOCKED: "blocked"}


def _resolve(workspace, pattern):
    """Every existing file matching one artifact name. A trailing '*' is the only wildcard used.

    The length guard is not decoration: `startswith(head) and endswith(tail)` alone accepts a
    name where head and tail OVERLAP, so `abc*bcd` would match `abcd` even though a real match
    needs six characters. No shipped pattern can hit it today; a future one could, and it would
    surface as a phantom artifact that makes a stage look READY.
    """
    if "*" not in pattern:
        p = os.path.join(workspace, pattern)
        return [p] if os.path.isfile(p) else []
    head, tail = pattern.split("*", 1)
    try:
        names = os.listdir(workspace)
    except OSError:
        return []
    return sorted(os.path.join(workspace, n) for n in names
                  if len(n) >= len(head) + len(tail)
                  and n.startswith(head) and n.endswith(tail)
                  and os.path.isfile(os.path.join(workspace, n)))


def _newest(paths):
    """(mtime, path) of the most recently written file, or (None, None)."""
    best = (None, None)
    for p in paths:
        try:
            m = os.path.getmtime(p)
        except OSError:
            continue
        if best[0] is None or m > best[0]:
            best = (m, p)
    return best


def _oldest_current_output(workspace, patterns):
    """(mtime, path) of the least current CURRENT output, or (None, None).

    Precisely, and the name says only this because the earlier `_oldest_output` did not:
    this is NOT the oldest file matching the stage's patterns. Each pattern contributes its
    own newest match -- that pattern's CURRENT artifact -- and the minimum is taken over
    those per-pattern heads. Two different things, and conflating them breaks the module in
    opposite directions:

      * minimum over per-pattern heads (this function) -- a stage is only as current as its
        least current artifact, so a freshly rewritten sibling cannot mask an output that is
        genuinely older than its own input. That masking is the one failure this module
        exists to catch: the owner skips a needed re-run and every downstream stage inherits
        an artifact that never reflected its data.
      * minimum over every matching file (NOT this function) -- a superseded Cerebro export
        the owner never deleted would hold its stage stale for ever.

    A pattern with no match contributes nothing here. That is safe only because every
    declared output is REQUIRED (see `Stage.produces`) and `evaluate()` resolves
    `missing_outputs` to MISSING or BLOCKED *before* any timestamp is compared, so a stage
    with an unmatched pattern never reaches a staleness verdict at all.
    """
    worst = (None, None)
    for pat in patterns:
        m, p = _newest(_resolve(workspace, pat))
        if m is None:
            continue                             # unreachable from a READY/STALE verdict
        if worst[0] is None or m < worst[0]:
            worst = (m, p)
    return worst


def evaluate(workspace, stages=STAGES):
    """Status for every stage, from the filesystem alone."""
    out = []
    for st in stages:
        missing_outputs = [pat for pat in st.produces if not _resolve(workspace, pat)]
        needed = {pat: _resolve(workspace, pat) for pat in st.needs}
        missing_inputs = [pat for pat, fs in needed.items() if not fs]

        out_mtime, out_path = _oldest_current_output(workspace, st.produces)
        in_mtime, in_path = _newest([f for fs in needed.values() for f in fs])

        if missing_outputs:
            state = BLOCKED if (missing_inputs and not st.is_owner_input) else MISSING
        elif in_mtime is not None and out_mtime is not None and out_mtime < in_mtime:
            state = STALE
        else:
            state = READY

        out.append({
            "n": st.n, "title": st.title, "state": state,
            "owner_input": st.is_owner_input,
            "produces": st.produces, "missing_outputs": missing_outputs,
            "missing_inputs": missing_inputs,
            "artifact": os.path.basename(out_path) if out_path else None,
            "artifact_mtime": out_mtime,
            "newer_input": os.path.basename(in_path) if state == STALE else None,
            "command": st.command, "note": st.note,
        })
    return out


def next_action(rows):
    """The first stage that is actionable. BLOCKED stages are skipped: their input comes first."""
    for r in rows:
        if r["state"] in (MISSING, STALE):
            return r
    return None


def _age(mtime, now=None):
    if not mtime:
        return ""
    days = int(((now or time.time()) - mtime) // 86400)
    return "today" if days <= 0 else ("1 day ago" if days == 1 else f"{days} days ago")


# ONE target shell, named. Quoting rules are not portable: `"$x"` is a literal in cmd.exe and a
# variable expansion in PowerShell, and `'x'` quotes in PowerShell and does nothing in cmd.exe. A
# renderer that names no shell cannot be correct for any, so this one commits to PowerShell -- the
# shell Start-AMZ-Toolkit.ps1 already uses -- and labels its output.
TARGET_SHELL = "Windows PowerShell"

# Safe to hand PowerShell bare. Deliberately a whitelist: a blacklist of metacharacters is one
# PowerShell release away from being wrong. Note `$` and backtick are absent -- inside a
# double-quoted PowerShell string both still expand, which is why quoting below is single-quoted.
_BARE_SAFE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-+=/:\\")


def _ps_quote(value):
    """Render one argument as a PowerShell literal.

    Scope, stated because the baseline docstring overstated it: this makes a printed command safe
    for the owner to PASTE into """ + TARGET_SHELL + """. It is NOT a shell-injection boundary and
    does not need to be -- the seed is the owner's own text going into the owner's own shell, and
    this module executes nothing. A seed keyword is normally two or three words, and an unquoted
    one silently becomes the wrong argument.

    Single quotes, not double. A PowerShell single-quoted string is a true literal: no variable
    expansion, no backtick escapes, and `''` is the only escape it has. A double-quoted string
    would expand `$` and process backticks, so a seed of `nurse $5 gift` would lose the `$5`
    silently -- the exact class of failure this function exists to prevent.

    A leading `-` also forces quoting: bare, PowerShell reads it as a parameter name.

    TWO LIMITS THIS FUNCTION CANNOT FIX, because they are not about quoting. Both are REFUSED
    by `unsupported_value()` at the CLI boundary rather than emitted and hoped over -- a value
    the tool cannot render exactly must not be rendered at all:

      * a control character corrupts the printed line itself, whatever the quoting;
      * a quote-requiring value that ENDS in a backslash is mangled by PowerShell's native
        argument marshalling, downstream of this string.
    """
    text = str(value)
    if not needs_quoting(text):
        return text
    return "'" + text.replace("'", "''") + "'"


def needs_quoting(value):
    """Whether `_ps_quote` will wrap this value.

    ONE predicate, used by both the renderer and the refusal in `unsupported_value()`. It was
    briefly duplicated -- the same condition written out in both places -- which is an
    approximation waiting to drift: change what renders bare and the refusal silently stops
    matching the behaviour it is supposed to describe.
    """
    text = str(value)
    return not (text and not text.startswith("-") and all(c in _BARE_SAFE for c in text))


def unsupported_value(value, label="seed"):
    """Why `value` cannot be rendered as a pasteable argument, or '' if it can.

    The tool promises the owner an exact command. Where it cannot keep that promise it says so
    and emits nothing -- silently shipping a value that arrives at the engine changed is the one
    outcome worse than refusing.

    Two refusals, both demonstrated rather than assumed:

    1. CONTROL CHARACTERS. A seed pasted out of a spreadsheet can carry a newline or tab. Printed,
       it splits the command across lines and the second line reads as a separate command. No
       quoting reaches this; it is the printed text that is broken.

    2. A QUOTE-REQUIRING VALUE ENDING IN A BACKSLASH. Windows PowerShell 5.1 wraps such an
       argument for a native child without escaping the trailing backslash, so `nurse gift\\`
       goes out as `"nurse gift\\"`; the Microsoft C runtime then reads `\\"` as an escaped quote
       and the argument runs on into the next one. A BARE value ending in a backslash -- `nurse\\`
       with no space -- is NOT affected: unquoted, there is no quote for the runtime to mis-parse,
       and it is therefore still supported. The refusal is exactly as wide as the defect.
    """
    text = str(value)
    bad = unsupported_characters(text)
    if bad:
        return (f"{label} contains characters that cannot be printed in a command: {bad}\n"
                f"retype the {label} without them")
    if text.endswith("\\") and needs_quoting(text):
        return (f"{label} ends in a backslash and also needs quoting, which Windows PowerShell "
                f"cannot pass to a program unchanged\nremove the trailing backslash "
                f"(a {label} ending in a backslash with no spaces or symbols is fine)")
    return ""


def unsupported_characters(value):
    """The characters in `value` that cannot survive being printed as a command, or ''.

    Control characters only. A newline in a seed -- easily arrived at by pasting from a
    spreadsheet -- would split the printed command across lines, and the second line would look
    like a separate command. Rejecting at the boundary is honest; quoting cannot help.
    """
    bad = sorted({c for c in str(value) if ord(c) < 0x20 or ord(c) == 0x7F})
    return "".join(repr(c)[1:-1] for c in bad)


def _needs_seed(cmd):
    return bool(cmd) and "{seed}" in cmd


def _fmt_command(cmd, workspace, seed):
    """The exact line to paste, or None when a required value is not real yet.

    None rather than a placeholder: a command containing `<seed-keyword>` looks ready to paste
    and is not. Callers must present the None case as an instruction, never as a command.
    """
    if not cmd or (_needs_seed(cmd) and not seed):
        return None
    return cmd.format(workspace=_ps_quote(workspace), seed=_ps_quote(seed or ""))


def render(rows, workspace, seed=None, now=None):
    # ASCII only. This prints into the Windows console the owner actually uses, where the default
    # code page mangles en/em dashes into replacement characters.
    lines = [f"AMZ pipeline - workspace {workspace}", ""]
    for r in rows:
        when = "-"
        if r["artifact_mtime"]:
            stamp = time.strftime("%Y-%m-%d", time.localtime(r["artifact_mtime"]))
            when = f"{stamp} {_age(r['artifact_mtime'], now)}"
        if r["state"] == BLOCKED:
            detail = "needs " + ", ".join(r["missing_inputs"])
        elif r["state"] == STALE:
            detail = f"{r['artifact']} is older than {r['newer_input']}"
        elif r["state"] == MISSING:
            detail = r["note"] if r["owner_input"] else ", ".join(r["missing_outputs"])
        else:
            detail = r["artifact"] or ""
        # Fixed columns first, free text last, so a long filename can never shift the alignment.
        lines.append(f"{r['n']:>3}. {r['title']:<26} {_MARK[r['state']]:<8} "
                     f"{when:<22} {detail}")

    nxt = next_action(rows)
    lines.append("")
    if not nxt:
        lines.append("Every stage is up to date. PPC stages live under "
                     f"{os.path.join(workspace, PHASE7_SUBDIR)} and have their own commands.")
    elif nxt["owner_input"]:
        lines.append(f"NEXT - you supply this one: {nxt['note']}")
        lines.append(f"       expected in {workspace}: {', '.join(nxt['produces'])}")
    else:
        why = "not run yet" if nxt["state"] == MISSING else \
              f"stale, {nxt['newer_input']} is newer than {nxt['artifact']}"
        lines.append(f"NEXT - step {nxt['n']}, {nxt['title']} ({why}):")
        ready = _fmt_command(nxt["command"], workspace, seed)
        if ready:
            lines.append(f"       [{TARGET_SHELL}] {ready}")
        else:
            # No seed, so there is no real command yet. Printing one with a placeholder in it
            # would look pasteable and would run with the placeholder as the seed.
            lines.append("       This step needs your seed keyword, so there is no command to")
            lines.append("       paste yet. Re-run with it and the exact line appears here:")
            lines.append(f"         [{TARGET_SHELL}] python -m core.pipeline_status "
                         f"--workspace {_ps_quote(workspace)} --seed 'your seed keyword'")
        later = [r for r in rows if r is not nxt and r["state"] in (MISSING, STALE)]
        if later:
            lines.append(f"       then {len(later)} more: "
                         + ", ".join(str(r["n"]) for r in later))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="python -m core.pipeline_status",
        description="Read-only: which pipeline stage is done, which is stale, what to run next. "
                    "Runs no stage, writes no file and never contacts Amazon.")
    ap.add_argument("--workspace", default=WORKSPACE_DEFAULT,
                    help=f"workspace holding the artifacts (default {WORKSPACE_DEFAULT})")
    ap.add_argument("--seed", help="seed keyword, used to fill in the printed command")
    ap.add_argument("--json", action="store_true", help="print the status rows as JSON")
    a = ap.parse_args(argv)

    # Refuse rather than print a command whose values will not arrive intact. The workspace is
    # checked too: it is substituted into the same printed line and carries the same hazards.
    #
    # BEFORE the workspace is looked up, deliberately. A path carrying a control character would
    # otherwise fail `isdir` first and be reported as "workspace not found", which is true but
    # useless -- it sends the owner looking for a missing directory instead of at the character
    # they pasted. Validation never touches the filesystem, so nothing is lost by going first.
    for label, value in (("seed", a.seed), ("workspace", a.workspace)):
        reason = unsupported_value(value, label) if value else ""
        if reason:
            if a.json:
                # A machine consumer must get a structured refusal, not a stderr string it would
                # have to scrape -- and next_command stays null so nothing can be run from it.
                print(json.dumps({"error": "UNSUPPORTED_VALUE", "field": label,
                                  "detail": reason.replace("\n", " "),
                                  "workspace": a.workspace, "target_shell": TARGET_SHELL,
                                  "next": None, "next_command": None,
                                  "next_command_needs_seed": False},
                                 indent=2, sort_keys=True))
            print(reason, file=sys.stderr)
            return 2

    if not os.path.isdir(a.workspace):
        print(f"workspace not found: {a.workspace}", file=sys.stderr)
        return 2

    rows = evaluate(a.workspace)
    if a.json:
        nxt = next_action(rows)
        # next_command is null, never a placeholder, when the seed is not known. A machine
        # consumer that saw a `<seed-keyword>` string could run it; next_command_needs_seed
        # says why it is null instead.
        print(json.dumps({"workspace": a.workspace, "stages": rows,
                          "target_shell": TARGET_SHELL,
                          "next": nxt["n"] if nxt else None,
                          "next_command": _fmt_command(nxt["command"], a.workspace, a.seed)
                          if nxt else None,
                          "next_command_needs_seed": bool(
                              nxt and _needs_seed(nxt["command"]) and not a.seed)},
                         indent=2, sort_keys=True))
    else:
        print(render(rows, a.workspace, a.seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
