#!/usr/bin/env python3
"""
AMZ FBM Toolkit v2 — pipeline orchestrator.

Runs the tool stages, translates each gate script's EXIT CODE into a structured
GateResult, records everything in PROJECT-MANIFEST.json, and computes whether the
project is PUBLICATION LOCKED. A BLOCKED gate can never be overridden by a score.
Nothing here connects to Seller Central.

Usage:
  python pipeline.py runs/<niche> --init-project --decoration-method "machine embroidery"
  python pipeline.py runs/<niche> --scaffold-gate-files
  python pipeline.py runs/<niche> "<Project Name>" [--seed "..."] [--anchor nurse]
  python pipeline.py runs/<niche> --approve-main-image --asset main.png --by owner
  python pipeline.py runs/<niche> --approve-creative --by owner
  python pipeline.py runs/<niche> --approve-final --by owner
"""
import sys, os, subprocess, glob, argparse, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "core"))
import paths  # noqa: sets sys.path for all subfolders
import status as S
import gate_engine as GE
import manifest as M
import hashing
try:                              # Session 5B: bounded, tree-killing subprocess runner
    import subprocess_runner as SR
except Exception:                 # pragma: no cover - keep pipeline usable if core is absent
    SR = None

HERE = os.path.dirname(os.path.abspath(__file__))
def tool(name, sub): return os.path.join(HERE, sub, name)

# v2.3.4 (P0.8): the evidence file each gate leaves behind. The final approval bundle is derived
# from whichever of these exist, so changing any one after approval invalidates the approval.
GATE_EVIDENCE_FILES = [
    "PRODUCT-FEASIBILITY.json", "FBM-FEASIBILITY.json", "ECONOMICS.json",
    "CATALOG-STRUCTURE.json", "PERSONALIZATION-RULES.json", "CLAIMS-EVIDENCE.json",
    "MAIN-IMAGE-COMPLIANCE.json", "EMBROIDERY-PROOF.json", "VISUAL-CONSISTENCY.json",
    "ACTUAL-ASSET-EDGE-SCORE.json", "THUMBNAIL-REVIEW.json", "IMAGE-STORYBOARD.json",
    "creative-brief.json",
]

# v2.3.4-RC1 (P0.1): deterministic file→gate readers. gate_id -> (filename, {pass decision values}).
# A pass is NEVER inferred from the filename — the file must carry an explicit decision/status.
FILE_GATES = {
    "RELEVANCE":           ("RELEVANCE-REVIEW.json",     {"GO"}),
    "PRODUCT_FEASIBILITY": ("PRODUCT-FEASIBILITY.json",  {"GO", "APPROVED"}),
    "FULFILLMENT":         ("FBM-FEASIBILITY.json",      {"GO", "APPROVED"}),
    "CATALOG_STRUCTURE":   ("CATALOG-STRUCTURE.json",    {"APPROVED"}),
    "PERSONALIZATION":     ("PERSONALIZATION-RULES.json",{"APPROVED"}),
    "CLAIMS_EVIDENCE":     ("CLAIMS-EVIDENCE.json",      {"VERIFIED", "APPROVED"}),
}
_BLOCK_DECISIONS = {"BLOCKED", "BLOCK", "REJECTED", "NO", "FAIL", "SKIP", "REVISE"}

# blank templates written by --scaffold-gate-files so staff have a supported way to fill each gate
GATE_TEMPLATES = {
    "RELEVANCE-REVIEW.json": {"decision": "INCOMPLETE", "reviewer": "", "reviewed_at": "",
        "seed": "", "confirms_exact_product": None,
        "notes": "Set decision to GO once the seed/keywords describe the exact product."},
    "PRODUCT-FEASIBILITY.json": {"decision": "INCOMPLETE", "reviewer": "", "reviewed_at": "",
        "supplier": "", "sizes_ok": None, "colors_ok": None, "method_ok": None,
        "notes": "Set decision to GO/APPROVED once supplier, sizes, colors, and method are confirmed."},
    "FBM-FEASIBILITY.json": {"decision": "INCOMPLETE", "reviewer": "", "reviewed_at": "",
        "production_days": None, "capacity_per_week": None, "tracking_provider": "",
        "notes": "Set decision to GO/APPROVED once production time, capacity, and tracking are real."},
    "CATALOG-STRUCTURE.json": {"decision": "INCOMPLETE", "reviewer": "", "reviewed_at": "",
        "parent_child": None, "variations": [],
        "notes": "Set decision to APPROVED once the catalog/variation plan is defined."},
    "PERSONALIZATION-RULES.json": {"decision": "INCOMPLETE", "reviewer": "", "reviewed_at": "",
        "fields": [], "char_limits": {}, "proof_workflow": "",
        "notes": "Set decision to APPROVED once personalization fields, limits, and proof flow are set."},
    "CLAIMS-EVIDENCE.json": {"decision": "INCOMPLETE", "reviewer": "", "reviewed_at": "",
        "claims": [], "notes": "List each factual claim with its evidence. Set decision to VERIFIED when all are evidenced."},
}

def _evidence(folder, *files):
    """[{path, sha256}] for each file that exists — attached to a gate so the final bundle can bind it."""
    out = []
    for f in files:
        p = os.path.join(folder, f)
        if f and os.path.exists(p):
            out.append({"path": f, "sha256": hashing.file_sha256(p)})
    return out

def read_file_gate(folder, gate_id, fname, pass_values):
    """Read one file→gate. Missing file → leave NOT_RUN (fail-safe). Present but no/invalid decision
    → INCOMPLETE. A recognised block decision → BLOCKED. A pass decision → that status."""
    p = os.path.join(folder, fname)
    if not os.path.exists(p):
        return None
    try:
        doc = json.load(open(p, encoding="utf-8"))
    except Exception:
        M.set_gate(folder, gate_id, GE.gate(gate_id, S.INCOMPLETE, reasons=[f"{fname} is not valid JSON"]).to_dict())
        return S.INCOMPLETE
    decision = str(doc.get("decision") or doc.get("status") or "").upper()
    evi = _evidence(folder, fname)
    if not decision or decision == "INCOMPLETE":
        M.set_gate(folder, gate_id, GE.gate(gate_id, S.INCOMPLETE,
                   reasons=[f"{fname} has no decision yet"], evidence=evi).to_dict())
        return S.INCOMPLETE
    if decision in pass_values:
        st = decision
    elif decision in _BLOCK_DECISIONS:
        st = S.BLOCKED
    else:
        st = S.INCOMPLETE
    M.set_gate(folder, gate_id, GE.gate(gate_id, st, evidence=evi,
               reasons=[f"{fname}: decision={decision}",
                        f"reviewer={doc.get('reviewer') or '—'} at {doc.get('reviewed_at') or '—'}"]).to_dict())
    return st

PY = sys.executable or "python3"   # v2.3.4-RC1 (P0.7): use the running interpreter (Windows-safe)

def run(cmd, timeout=600):
    # accept commands that start with a literal "python3"/"python" and normalize to sys.executable
    if cmd and cmd[0] in ("python3", "python"):
        cmd = [PY] + cmd[1:]
    if SR is not None:
        # Session 5B (ACT-020): explicit timeout, tree-kill on hang, secret-safe.
        stage = os.path.basename(str(cmd[1])) if (len(cmd) > 1 and str(cmd[1]).endswith(".py")) \
            else os.path.basename(str(cmd[0]))
        r = SR.run_subprocess(list(cmd), timeout_seconds=timeout, stage_name=stage,
                              allowed_exit_codes=tuple(range(0, 256)))
        if r.timed_out:
            return -1, r.diagnostic_summary
        if r.error_code == "SUBPROCESS_START_FAILED":
            return -1, r.stderr or r.diagnostic_summary
        return r.exit_code, (r.stdout or "") + (r.stderr or "")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")

_IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")
def _is_asset_ref(v):
    """A brief our_images value is an image REFERENCE only if it's a filename with an image
    extension — metadata strings like 'real_photo' or booleans are not assets."""
    return isinstance(v, str) and v.lower().endswith(_IMG_EXT)

# exit code -> gate status
def status_from_exit(rc, econ=False):
    return {0: S.GO, 2: S.INCOMPLETE, 3: S.TEST, 4: S.BLOCKED,
            5: (S.SKIP if econ else S.BLOCKED), 6: S.INCOMPLETE, 7: S.INCOMPLETE}.get(rc, S.INCOMPLETE)

def first(out, needle, default=""):
    return next((l.strip() for l in out.splitlines() if needle in l), default)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder"); ap.add_argument("project_name", nargs="?", default="")
    ap.add_argument("--seed"); ap.add_argument("--anchor")
    ap.add_argument("--approve-final", action="store_true", dest="approve_final")
    ap.add_argument("--approve-main-image", action="store_true", dest="approve_main_image")
    ap.add_argument("--approve-creative", action="store_true", dest="approve_creative")
    ap.add_argument("--asset", default=None)
    ap.add_argument("--status", action="store_true"); ap.add_argument("--next", action="store_true", dest="whatnext")
    ap.add_argument("--init-project", action="store_true", dest="init_project")
    ap.add_argument("--scaffold-gate-files", action="store_true", dest="scaffold_gate_files")
    ap.add_argument("--product-family", default="embroidered_apparel")
    ap.add_argument("--decoration-method", default="machine embroidery")
    ap.add_argument("--fulfillment", default="FBM")
    ap.add_argument("--by", default="owner"); ap.add_argument("--note", default="")
    a = ap.parse_args()

    # ---- P0.12: initialize project traits (so required gates derive from facts, not manual edits) ----
    if a.init_project:
        man = M.load(a.folder, os.path.basename(a.folder.rstrip("/")), a.project_name or a.folder)
        man["product_family"] = a.product_family
        man["decoration_method"] = a.decoration_method
        man["fulfillment"] = a.fulfillment
        man["requires_listing_images"] = True
        man["requires_embroidery_proof"] = "embroider" in a.decoration_method.lower()
        M.save(a.folder, man)
        print(f"✅ initialized project: family={a.product_family}, method={a.decoration_method}, fulfillment={a.fulfillment}")
        print("   required creative + embroidery gates now apply automatically."); return

    # ---- v2.3.4-RC1 (P0.1): scaffold blank gate files so staff have a supported way to fill each gate ----
    if a.scaffold_gate_files:
        os.makedirs(a.folder, exist_ok=True); wrote = []
        for fname, tmpl in GATE_TEMPLATES.items():
            p = os.path.join(a.folder, fname)
            if os.path.exists(p):
                print(f"   (kept existing {fname})"); continue
            with open(p, "w", encoding="utf-8") as f: json.dump(tmpl, f, indent=2, ensure_ascii=False)
            wrote.append(fname)
        print("✅ scaffolded gate files:", ", ".join(wrote) or "(none — all already present)")
        print("   Fill each file's \"decision\" (e.g. GO/APPROVED/VERIFIED) with reviewer + reviewed_at, then re-run the pipeline.")
        return

    # ---- orchestration: --status / --next (one clear next action) ----
    if a.status or a.whatnext:
        import stages as ST
        M.verify_approvals(a.folder)          # P0.15: detect approval hash drift on every status call
        man = M.load(a.folder)
        info = ST.compute(man)
        if a.status:
            print(f"Project: {man.get('project_name') or a.folder}")
            print(f"Overall status: {man.get('overall_status')}  ·  Publication locked: {'YES' if info['publication_locked'] else 'no'}")
            print("\nPassing gates:"); [print("  ✔", g) for g,_ in info["passing"]] or print("  (none yet)")
            print("Missing gates:"); [print(f"  - {g} = {st}") for g,st in info["missing"]] or print("  (none)")
        cur = info["current_stage"]
        if cur:
            miss_inputs = [f for f in cur["inputs"] if not os.path.exists(os.path.join(a.folder, f))]
            print(f"\nNext stage: {cur['name']}  ({cur['role']})")
            if miss_inputs: print("Missing inputs:", ", ".join(miss_inputs))
            print("Recommended action:", cur["action"])
        else:
            print("\n✅ All required gates pass — ready for the manual publication step.")
        return
    if not os.path.isdir(a.folder):
        os.makedirs(a.folder, exist_ok=True)

    # ---- owner MAIN-IMAGE approval (v2.3.4 P0.6): hash-bound; sets MAIN_IMAGE_COMPLIANCE=APPROVED ----
    if a.approve_main_image:
        import asset_validator as AV
        mic_path = os.path.join(a.folder, "MAIN-IMAGE-COMPLIANCE.json")
        if not os.path.exists(mic_path):
            print("⛔ MAIN-IMAGE-COMPLIANCE.json not found — run creative_edge first."); sys.exit(S.EXIT_INCOMPLETE)
        mic = json.load(open(mic_path))
        mic_asset = mic.get("asset") or {}
        mic_rel = mic_asset.get("relative_path"); mic_hash = mic_asset.get("sha256")
        if not mic_rel or not mic_hash:
            print("⛔ MAIN-IMAGE-COMPLIANCE.json has no reviewed asset (path+hash). Re-run creative_edge with a real main image.")
            sys.exit(S.EXIT_INCOMPLETE)
        asset = a.asset or mic_rel
        # v2.3.4-RC1 (P0.3): you may only approve the EXACT image the compliance record was built for.
        if a.asset and a.asset != mic_rel:
            print(f"⛔ --asset '{a.asset}' is not the reviewed image '{mic_rel}'. Re-run creative_edge for that asset, then approve.")
            sys.exit(S.EXIT_REVIEW)
        av = AV.validate_image(asset, project_dir=a.folder)
        if not AV.is_quality_acceptable(av):
            print(f"⛔ Main-image asset not quality-acceptable: {av.get('reason')}"); sys.exit(S.EXIT_INCOMPLETE)
        if av.get("sha256") != mic_hash:
            print("⛔ The current file's hash does not match the compliance record — the image changed. Re-run creative_edge, then approve.")
            sys.exit(S.EXIT_REVIEW)
        if mic.get("status") != "COMPLIANT":
            print(f"⛔ Technical compliance is '{mic.get('status')}', not COMPLIANT — complete category review + accuracy first.")
            for act in mic.get("required_actions", []): print("   -", act)
            sys.exit(S.EXIT_REVIEW)
        M.add_approval(a.folder, {"approval_type": "MAIN_IMAGE_APPROVAL", "decision": "APPROVED",
                                  "owner_name": a.by, "notes": a.note, "approved_files": [asset, "MAIN-IMAGE-COMPLIANCE.json"]})
        print(f"✅ hash-bound MAIN_IMAGE_APPROVAL by {a.by} on {asset} — MAIN_IMAGE_COMPLIANCE now APPROVED")
        print("   (auto-invalidates if the image or its compliance record changes)")
        print(M.summary(a.folder)); return

    # ---- owner CREATIVE-PACKAGE approval (v2.3.4 P0.6): sets CREATIVE_OWNER_APPROVAL=APPROVED ----
    if a.approve_creative:
        import stages as ST
        M.verify_approvals(a.folder)
        man0 = M.load(a.folder); g = man0.get("gates", {})
        need = {"MAIN_IMAGE_COMPLIANCE": "APPROVED", "EMBROIDERY_PROOF": ("PROVEN","PARTIALLY_PROVEN"),
                "VISUAL_CONSISTENCY": ("CONSISTENT",), "ACTUAL_ASSET_EDGE": ("APPROVED",)}
        # EMBROIDERY_PROOF only required for embroidery projects
        req = set(GE.required_gates({"product_family": man0.get("product_family",""),
                    "decoration_method": man0.get("decoration_method", man0.get("product_family","")),
                    "requires_listing_images": man0.get("requires_listing_images", True),
                    "requires_embroidery_proof": man0.get("requires_embroidery_proof", False)}))
        blocking = []
        for gid, ok in need.items():
            if gid not in req: continue
            st = (g.get(gid) or {}).get("status")
            ok_set = (ok,) if isinstance(ok, str) else ok
            if st not in ok_set: blocking.append(f"{gid}={st or 'NOT_RUN'}")
        if blocking:
            print("⛔ Cannot approve the creative package — these creative gates do not pass yet:")
            for b in blocking: print("   -", b)
            print("   Fix them (see --next), then approve the main image, then approve the creative package.")
            sys.exit(S.EXIT_REVIEW)
        # v2.3.4-RC1 (P0.4): a creative approval MUST bind a complete evidence bundle — never zero
        # hashes. Refuse if any required creative evidence file or asset is missing.
        is_emb = "embroider" in str(man0.get("decoration_method", man0.get("product_family",""))).lower() \
                 or man0.get("requires_embroidery_proof", False)
        required_creative = ["creative-brief.json", "MAIN-IMAGE-COMPLIANCE.json", "VISUAL-CONSISTENCY.json",
                             "ACTUAL-ASSET-EDGE-SCORE.json", "THUMBNAIL-REVIEW.json", "IMAGE-STORYBOARD.json"]
        if is_emb: required_creative.append("EMBROIDERY-PROOF.json")
        missing_c = [f for f in required_creative if not os.path.exists(os.path.join(a.folder, f))]
        # referenced image assets (main + macro/supplier + image-spec assets) must exist too
        cb = os.path.join(a.folder, "creative-brief.json"); assets = []
        if os.path.exists(cb):
            try:
                brief_doc = json.load(open(cb))
                imgs = (brief_doc.get("our_images") or {})
                assets = [v for k,v in imgs.items() if _is_asset_ref(v)]
                assets += [s.get("asset_path") for s in (brief_doc.get("image_specs") or []) if s.get("asset_path")]
            except Exception: pass
        missing_assets = [v for v in assets if not os.path.exists(os.path.join(a.folder, v))]
        if not os.path.exists(cb):
            missing_c.append("creative-brief.json")
        if missing_c or missing_assets:
            print("⛔ Cannot approve the creative package — the evidence bundle is incomplete:")
            for f in missing_c: print("   - missing evidence file:", f)
            for v in missing_assets: print("   - missing asset:", v)
            print("   Run creative_edge and shoot/validate the assets first. An approval with no bundle is not allowed.")
            sys.exit(S.EXIT_INCOMPLETE)
        creative_files = sorted(set(required_creative + [v for v in assets if os.path.exists(os.path.join(a.folder, v))]))
        M.add_approval(a.folder, {"approval_type": "CREATIVE_PACKAGE", "decision": "APPROVED",
                                  "owner_name": a.by, "notes": a.note, "approved_files": creative_files})
        print(f"✅ hash-bound CREATIVE_PACKAGE approval by {a.by} — CREATIVE_OWNER_APPROVAL now APPROVED")
        print("   bound files:"); [print("   -", f) for f in creative_files]
        print("   (auto-invalidates if any creative file or asset changes)")
        print(M.summary(a.folder)); return

    # ---- owner FINAL approval (P0.6): hash-bound bundle, refuses if incomplete ----
    if a.approve_final:
        # the required approval bundle — the exact files the approval is bound to
        required = ["listing.json", "economics-input.csv"]
        present_req = [f for f in required if os.path.exists(os.path.join(a.folder, f))]
        missing_req = [f for f in required if not os.path.exists(os.path.join(a.folder, f))]
        if missing_req:
            print("⛔ Cannot record final approval — required files missing:", ", ".join(missing_req))
            print("   A keyword string alone is not a valid final approval.")
            sys.exit(S.EXIT_INCOMPLETE)
        # v2.3.4 (P0.7): final approval is the LAST step — refuse unless EVERY non-final required
        # gate passes, INCLUDING CREATIVE_OWNER_APPROVAL. Only OWNER_APPROVAL (the gate this call
        # creates) is excluded.
        import stages as ST
        man0 = M.load(a.folder); info0 = ST.compute(man0)
        blocking = [f"{g}={st}" for g,st in info0["missing"] if g != "OWNER_APPROVAL"]
        if blocking:
            print("⛔ Cannot record FINAL approval — these required gates do not pass yet:")
            for b in blocking: print("   -", b)
            if any(x.startswith("CREATIVE_OWNER_APPROVAL") for x in blocking):
                print("   Run --approve-main-image then --approve-creative before --approve-final.")
            print("   Final approval must be the LAST step. Clear the gates (see --next) first.")
            sys.exit(S.EXIT_REVIEW)
        # v2.3.4-RC1 (P0.5): the bundle is DERIVED from the evidence each PASSING required gate
        # declares. A required gate that passes but declares no current evidence file is a hard
        # refusal — we never approve a green gate that can't prove what it was green on.
        assets = []
        cb = os.path.join(a.folder, "creative-brief.json")
        if os.path.exists(cb):
            try:
                brief_doc = json.load(open(cb))
                imgs = (brief_doc.get("our_images") or {})
                assets = [v for k,v in imgs.items() if _is_asset_ref(v) and os.path.exists(os.path.join(a.folder, v))]
                assets += [s.get("asset_path") for s in (brief_doc.get("image_specs") or [])
                           if s.get("asset_path") and os.path.exists(os.path.join(a.folder, s.get("asset_path")))]
            except Exception: pass
        gates_now = man0.get("gates", {})
        gate_evidence, no_evidence = [], []
        # gates that carry their proof in-manifest (approvals) or have no separate file are exempt
        EXEMPT = {"OWNER_APPROVAL", "CREATIVE_OWNER_APPROVAL", "MAIN_IMAGE_COMPLIANCE"}
        for gid in info0["required_gates"]:
            if gid in EXEMPT:
                continue
            gd = gates_now.get(gid, {})
            if not GE._passes(gid, gd.get("status")):
                continue
            files = [e.get("path") for e in (gd.get("evidence") or []) if e.get("path")]
            files = [f for f in files if os.path.exists(os.path.join(a.folder, f))]
            if files:
                gate_evidence += files
            else:
                no_evidence.append(gid)
        if no_evidence:
            print("⛔ Cannot record FINAL approval — these passing gates have no current evidence file:")
            for gid in no_evidence: print("   -", gid)
            print("   Re-run the stage that produces each gate's evidence, then approve.")
            sys.exit(S.EXIT_INCOMPLETE)
        # main-image evidence: the exact approved asset + its compliance record (from the approval)
        main_appr = [ap for ap in man0.get("owner_approvals", [])
                     if ap.get("approval_type") == "MAIN_IMAGE_APPROVAL" and ap.get("decision") == "APPROVED"]
        if main_appr:
            gate_evidence += list((main_appr[-1].get("approved_file_hashes") or {}).keys())
        # write a hashed snapshot of the passing gate state and bind it too
        gate_snap = {k: v.get("status") for k, v in gates_now.items()}
        with open(os.path.join(a.folder, "GATE-SNAPSHOT.json"), "w", encoding="utf-8") as _gs:
            json.dump(gate_snap, _gs, indent=2, sort_keys=True)
        bundle = sorted(set(present_req + gate_evidence + assets + ["GATE-SNAPSHOT.json"]))
        print("Files to approve (hash-bound, derived from passing-gate evidence):")
        [print("  -", f) for f in bundle]
        man = M.load(a.folder)
        M.add_approval(a.folder, {"approval_type": "FINAL_LISTING", "decision": "APPROVED",
                                  "owner_name": a.by, "notes": a.note, "approved_files": bundle,
                                  "gate_snapshot": {k: v.get("status") for k, v in man.get("gates", {}).items()}})
        print(f"✅ hash-bound FINAL_LISTING approval by {a.by} — will auto-invalidate if any approved file changes")
        print(M.summary(a.folder)); return

    man = M.load(a.folder, os.path.basename(a.folder.rstrip("/")), a.project_name or a.folder)
    M.save(a.folder, man)
    src = [f for e in ("*.csv","*.xlsx","*.xls","*.json") for f in glob.glob(os.path.join(a.folder,e))]
    if src: M.register_sources(a.folder, src)

    print("="*68); print(f"  AMZ FBM TOOLKIT v2 — {a.project_name or a.folder}"); print("="*68)

    # ---- DEMAND gate ----
    dinput = os.path.join(a.folder, "demand-input.csv")
    if os.path.exists(dinput):
        rc, out = run([PY, tool("demand_score.py","research"), dinput, "--out", a.folder])
        gos = [l for l in out.splitlines() if "🟢 GO" in l]
        st = S.GO if gos else status_from_exit(rc)
        M.set_gate(a.folder, "DEMAND", GE.gate("DEMAND", st, evidence=_evidence(a.folder,"demand-input.csv","DEMAND-SCORECARD.md"),
                   reasons=[f"{len(gos)} GO keyword(s)"], required_actions=[] if gos else ["gather more evidence"]).to_dict())
        print(f"  DEMAND        {st}  ({len(gos)} GO)")
    else:
        M.set_gate(a.folder, "DEMAND", GE.gate("DEMAND", S.INCOMPLETE, reasons=["no demand-input.csv"]).to_dict())
        print("  DEMAND        INCOMPLETE (fill demand-input.csv)")

    # ---- ECONOMICS gate ----
    einput = os.path.join(a.folder, "economics-input.csv")
    if os.path.exists(einput):
        rc, out = run([PY, tool("economics_gate.py","economics"), einput, "--out", a.folder])
        st = status_from_exit(rc, econ=True)
        M.set_gate(a.folder, "ECONOMICS", GE.gate("ECONOMICS", st, evidence=_evidence(a.folder,"economics-input.csv","ECONOMICS.json"),
                   reasons=[first(out,"GO ", "see ECONOMICS-GATE.md")]).to_dict())
        print(f"  ECONOMICS     {st}")
    else:
        M.set_gate(a.folder, "ECONOMICS", GE.gate("ECONOMICS", S.INCOMPLETE, reasons=["no economics-input.csv"]).to_dict())
        print("  ECONOMICS     INCOMPLETE (fill economics-input.csv)")

    # ---- file→gate ingestion (v2.3.4-RC1 P0.1): feasibility, fulfillment, catalog, personalization, claims, relevance ----
    for gid, (fname, passvals) in FILE_GATES.items():
        st = read_file_gate(a.folder, gid, fname, passvals)
        if st is not None:
            print(f"  {gid:20} {st}  ({fname})")

    # ---- IP_SAFETY + LISTING_ACCURACY gates (if a listing is present) ----
    listing_md = sorted(glob.glob(os.path.join(a.folder,"LISTING*")) + glob.glob(os.path.join(a.folder,"*listing*.md")))
    listing_json = os.path.join(a.folder,"listing.json")
    # v2.3.4-RC1 (P0.6): IP screening must run on listing.json content too, not only markdown listings.
    ip_set = False
    if os.path.exists(listing_json):
        try:
            import ip_guard as _IPG
            lj = json.load(open(listing_json, encoding="utf-8"))
            parts = [str(lj.get("title",""))]
            parts += [str(x) for x in (lj.get("bullets") or lj.get("bullet_points") or [])]
            parts += [str(lj.get("description",""))]
            bk = lj.get("backend") or lj.get("backend_keywords") or lj.get("search_terms") or ""
            parts += [bk if isinstance(bk,str) else " ".join(map(str, bk))]
            parts += [str(lj.get("personalization","")), str(lj.get("personalization_instructions",""))]
            for mod in (lj.get("aplus") or lj.get("a_plus") or []):
                parts.append(str(mod))
            content = " ".join(p for p in parts if p.strip())
            scr = _IPG.screen(content)
            st = {"OK":"OK","REVIEW":S.REVISE,"BLOCK":S.BLOCKED}.get(scr["verdict"], S.INCOMPLETE)
            blocks = [h["term"] for h in scr["hits"] if h["risk"]=="BLOCK"]
            M.set_gate(a.folder, "IP_SAFETY", GE.gate("IP_SAFETY", st, evidence=_evidence(a.folder,"listing.json"),
                       reasons=[f"listing.json IP screen: {scr['verdict']}" + (f" — BLOCK terms: {', '.join(blocks)}" if blocks else "")]).to_dict())
            print(f"  IP_SAFETY     {st}  (listing.json)")
            ip_set = True
        except Exception as _e:
            print("  (IP screen on listing.json skipped:", _e, ")")
    if listing_md and not ip_set:
        rc, out = run([PY, tool("ip_guard.py","compliance"), "--file", listing_md[0]])
        st = {0:"OK",2:S.REVISE,3:S.BLOCKED}.get(rc, "OK")
        M.set_gate(a.folder, "IP_SAFETY", GE.gate("IP_SAFETY", st, evidence=_evidence(a.folder, os.path.basename(listing_md[0])),
                   reasons=[first(out,"VERDICT")]).to_dict())
        print(f"  IP_SAFETY     {st}")
    if os.path.exists(listing_json):
        master = os.path.join(a.folder,"master-keywords.xlsx")
        rc, out = run([PY, tool("listing_validate.py","compliance"), listing_json] + ([master] if os.path.exists(master) else []))
        st = S.GO if rc == 0 else (S.INCOMPLETE if rc == 2 else S.BLOCKED)
        M.set_gate(a.folder, "LISTING_ACCURACY", GE.gate("LISTING_ACCURACY", st, evidence=_evidence(a.folder,"listing.json"),
                   reasons=[first(out,"FAIL") or first(out,"PASS") or "validated"]).to_dict())
        print(f"  LISTING_ACCURACY {st}")

    # ---- research reports (non-gating) ----
    if a.seed:
        xray = [f for f in glob.glob(os.path.join(a.folder,"*.xlsx")) if "xray" in os.path.basename(f).lower()]
        if xray:
            cmd = [PY, tool("asin_picker.py","research"), a.folder, "--seed", a.seed, "--out", a.folder]
            if a.anchor: cmd += ["--anchor", a.anchor]
            run(cmd)
            M.set_stage(a.folder, "Stage 3 ASIN selection", {"status": S.READY_FOR_REVIEW, "output": "ASIN-BATCH-REPORT.md"})

    # ---- final publication-lock verdict ----
    man = M.load(a.folder)
    print("-"*68)
    print(M.summary(a.folder))
    if man["publication_locked"]:
        print("\n  ⛔ PUBLICATION LOCKED — clear every gate + owner approvals before the manual publish step.")
        # v2.3.4-RC1 (P0.9): ONE next-action engine. The footer uses stages.compute(), the same
        # authoritative source as --status/--next, so the recommended action is always consistent.
        import stages as ST
        cur = ST.compute(man)["current_stage"]
        if cur:
            miss_inputs = [f for f in cur["inputs"] if not os.path.exists(os.path.join(a.folder, f))]
            print(f"  NEXT: {cur['name']} ({cur['role']})")
            if miss_inputs: print("        missing inputs:", ", ".join(miss_inputs))
            print("        action:", cur["action"])
        print(f'  (run: python pipeline.py {a.folder} --next  for the single next action)')
    else:
        print("\n  🔓 All hard gates pass + owner approved → owner may proceed to the MANUAL publication package.")
    print("  (v2 · nothing here touches Seller Central)\n")
    sys.exit(S.EXIT_OK if not man["publication_locked"] else S.EXIT_REVIEW)

if __name__ == "__main__":
    main()
