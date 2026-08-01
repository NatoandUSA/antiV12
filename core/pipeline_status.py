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
    """One pipeline step: what proves it ran, what it needs, and how to run it."""

    def __init__(self, n, title, produces, needs=(), command=None, note=""):
        self.n = n
        self.title = title
        self.produces = list(produces)
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
    """Every existing file matching one artifact name. A trailing '*' is the only wildcard used."""
    if "*" not in pattern:
        p = os.path.join(workspace, pattern)
        return [p] if os.path.isfile(p) else []
    head, tail = pattern.split("*", 1)
    try:
        names = os.listdir(workspace)
    except OSError:
        return []
    return sorted(os.path.join(workspace, n) for n in names
                  if n.startswith(head) and n.endswith(tail)
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


def _oldest_output(workspace, patterns):
    """(mtime, path) of the LEAST current declared output, or (None, None).

    A stage is only as current as its oldest artifact. Taking the newest instead lets a
    freshly rewritten sibling mask an output that is genuinely older than its own input --
    the one failure this module exists to catch -- so the owner skips a needed re-run and
    every downstream stage inherits an artifact that never reflected its data.

    One PATTERN contributes one artifact: its newest match. The minimum is taken ACROSS
    patterns, never across every file on disk, because a wildcard names a set the owner
    keeps adding to, and a superseded export they never deleted must not mark a stage stale
    for ever.
    """
    worst = (None, None)
    for pat in patterns:
        m, p = _newest(_resolve(workspace, pat))
        if m is None:
            continue
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

        out_mtime, out_path = _oldest_output(workspace, st.produces)
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


def _quote(value):
    """Quote an argument that the owner will paste into a shell. A seed keyword is normally two or
    three words, and an unquoted one silently becomes the wrong argument."""
    return f'"{value}"' if (" " in value or "\t" in value) else value


def _fmt_command(cmd, workspace, seed):
    if not cmd:
        return None
    return cmd.format(workspace=_quote(workspace), seed=_quote(seed or "<seed-keyword>"))


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
        lines.append(f"       {_fmt_command(nxt['command'], workspace, seed)}")
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

    if not os.path.isdir(a.workspace):
        print(f"workspace not found: {a.workspace}", file=sys.stderr)
        return 2

    rows = evaluate(a.workspace)
    if a.json:
        nxt = next_action(rows)
        print(json.dumps({"workspace": a.workspace, "stages": rows,
                          "next": nxt["n"] if nxt else None,
                          "next_command": _fmt_command(nxt["command"], a.workspace, a.seed)
                          if nxt and nxt["command"] else None},
                         indent=2, sort_keys=True))
    else:
        print(render(rows, a.workspace, a.seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
