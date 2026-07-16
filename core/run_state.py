#!/usr/bin/env python3
"""
RUN STATE + OWNER APPROVAL  —  machine-readable state per run folder.

Replaces "infer state from filenames" with an explicit run-state.json that records
each stage, each gate verdict (demand / economics / ip), and owner approvals.
The pipeline writes stage/gate results here; the owner records approval here; and
"ready to build/publish" is computed, not assumed.

CLI:
  python run_state.py <folder> show
  python run_state.py <folder> approve --keyword "personalized nurse sweatshirt" --by owner [--note "..."]
  python run_state.py <folder> ready
Importable: set_stage(), set_gate(), add_approval(), ready_to_build().
Not connected to Seller Central — this is a local decision log.
"""
import json, os, sys, argparse
from datetime import datetime

def _now():
    try: return datetime.now().astimezone().isoformat(timespec="seconds")
    except Exception: return datetime.now().isoformat(timespec="seconds")

def _path(folder): return os.path.join(folder, "run-state.json")

def load(folder):
    p = _path(folder)
    if os.path.exists(p):
        try: return json.load(open(p, encoding="utf-8"))
        except Exception: pass
    return {"niche": None, "seed": None, "created_at": _now(), "updated_at": _now(),
            "stages": {}, "gates": {}, "approvals": []}

def save(folder, st):
    st["updated_at"] = _now()
    try: json.dump(st, open(_path(folder), "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    except Exception as e: print("run_state: could not save:", e)
    return st

def meta(folder, niche=None, seed=None):
    st = load(folder)
    if niche: st["niche"] = niche
    if seed:  st["seed"] = seed
    return save(folder, st)

def set_stage(folder, stage, status, detail=""):
    st = load(folder); st["stages"][stage] = {"status": status, "detail": detail, "at": _now()}
    return save(folder, st)

def set_gate(folder, name, verdict, detail=""):
    st = load(folder); st["gates"][name] = {"verdict": verdict, "detail": detail, "at": _now()}
    return save(folder, st)

def add_approval(folder, keyword, by, note=""):
    st = load(folder)
    st["approvals"].append({"keyword": keyword, "by": by, "note": note, "at": _now()})
    return save(folder, st)

def ready_to_build(folder):
    """Return (ok: bool, blockers: list[str]). ALL must pass to build."""
    st = load(folder); g = st.get("gates", {})
    b = []
    if g.get("demand", {}).get("verdict") != "GO":       b.append(f"demand={g.get('demand',{}).get('verdict') or 'unchecked'}")
    if g.get("economics", {}).get("verdict") != "GO":    b.append(f"economics={g.get('economics',{}).get('verdict') or 'unchecked'}")
    if g.get("ip", {}).get("verdict") == "BLOCK":        b.append("ip=BLOCK")
    if not st.get("approvals"):                          b.append("no owner approval")
    return (len(b) == 0, b)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder"); ap.add_argument("cmd", choices=["show","approve","ready"])
    ap.add_argument("--keyword", default=""); ap.add_argument("--by", default="owner")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    if not os.path.isdir(a.folder): sys.exit("Folder not found: "+a.folder)

    if a.cmd == "approve":
        if not a.keyword: sys.exit("--keyword required to approve")
        add_approval(a.folder, a.keyword, a.by, a.note)
        print(f"✅ recorded approval: '{a.keyword}' by {a.by}")
        ok, b = ready_to_build(a.folder)
        print("READY TO BUILD" if ok else "still blocked: " + ", ".join(b)); return

    if a.cmd == "ready":
        ok, b = ready_to_build(a.folder)
        print("✅ READY TO BUILD" if ok else "⛔ NOT READY — " + ", ".join(b))
        sys.exit(0 if ok else 6); return

    st = load(a.folder)
    print(f"# run-state · {st.get('niche') or a.folder}")
    print(f"  seed: {st.get('seed')}   updated: {st.get('updated_at')}")
    print("  gates:")
    for k,v in st.get("gates",{}).items(): print(f"    - {k}: {v.get('verdict')}  ({v.get('detail','')})")
    print("  approvals:", len(st.get("approvals",[])))
    for ap_ in st.get("approvals",[]): print(f"    - '{ap_['keyword']}' by {ap_['by']} @ {ap_['at']}")
    ok, b = ready_to_build(a.folder)
    print("  READY TO BUILD:", "✅ yes" if ok else "⛔ no — " + ", ".join(b))

if __name__ == "__main__":
    main()
