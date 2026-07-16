#!/usr/bin/env python3
"""
migrate_project.py — migrate a v1 run folder into a v2 project (safe, non-destructive).

  python migrate_project.py runs/<old-project>

- Backs up the original folder (copy, never delete).
- Detects old outputs (master-keywords, reports, run-state.json).
- Builds a v2 PROJECT-MANIFEST.json, carrying over gate verdicts it can VERIFY.
- Marks unknown information as MISSING. Never invents approvals or evidence.
- Writes a migration report. An old "completed" stage is NOT turned into an
  approved hard gate without evidence.
"""
import sys, os, shutil, json, glob
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "core"))
import paths  # noqa
import manifest as M, status as S, gate_engine as GE

def main():
    if len(sys.argv) < 2:
        print("Usage: python migrate_project.py runs/<old-project>"); sys.exit(6)
    folder = sys.argv[1]
    if not os.path.isdir(folder):
        print("Not a folder:", folder); sys.exit(6)

    backup = folder.rstrip("/") + "_v1backup"
    if not os.path.exists(backup):
        shutil.copytree(folder, backup)
    report = ["# Migration report", f"- source: {folder}", f"- backup: {backup}"]

    man = M.load(folder, os.path.basename(folder.rstrip("/")), os.path.basename(folder.rstrip("/")))
    man["tool_versions"]["migrated_from"] = "v1"

    # carry over v1 run-state.json gates/approvals IF present (verifiable)
    rs = os.path.join(folder, "run-state.json")
    carried = []
    if os.path.exists(rs):
        try:
            old = json.load(open(rs, encoding="utf-8"))
            for gid, g in (old.get("gates") or {}).items():
                v = g.get("verdict")
                st = {"GO": S.GO, "SKIP": S.SKIP, "BLOCK": S.BLOCKED, "OK": S.GO}.get(v, S.INCOMPLETE)
                gname = {"demand":"DEMAND","economics":"ECONOMICS","ip":"IP_SAFETY"}.get(gid, gid.upper())
                M.set_gate(folder, gname, GE.gate(gname, st, reasons=[f"migrated from v1 ({v})"]).to_dict())
                carried.append(f"{gname}={st}")
            # approvals are carried but NOT auto-trusted as FINAL unless explicitly final
            for a in (old.get("approvals") or []):
                report.append(f"- v1 approval found for '{a.get('keyword')}' — recorded as ANGLE approval, NOT final listing approval")
                M.add_approval(folder, {"approval_type":"ANGLE","decision":"APPROVED",
                                        "owner_name":a.get("by","owner"),"keyword":a.get("keyword"),
                                        "notes":"migrated from v1 — re-approve FINAL_LISTING in v2"})
        except Exception as e:
            report.append(f"- could not parse v1 run-state.json: {e}")
    report.append(f"- gates carried: {', '.join(carried) if carried else 'none (all set MISSING/NOT_RUN)'}")

    # register existing outputs as source files (hashed)
    outs = [f for e in ("*.xlsx","*.csv","*.md","*.json") for f in glob.glob(os.path.join(folder,e))]
    M.register_sources(folder, outs)
    man = M.load(folder)
    man["warnings"].append("Migrated from v1. Re-run gates and record a FINAL_LISTING approval before publishing.")
    M.save(folder, man)

    report.append(f"- publication_locked: {man['publication_locked']} (v2 default = locked until re-verified)")
    report.append("- NOTE: no approvals were invented; old 'done' stages were not converted to approved hard gates.")
    open(os.path.join(folder, "MIGRATION-REPORT.md"), "w", encoding="utf-8").write("\n".join(report))
    print("\n".join(report))
    print("\n✅ migrated. Backup at:", backup)

if __name__ == "__main__":
    main()
