#!/usr/bin/env python3
"""
AMZ FBM Toolkit — local dashboard (cockpit).

A thin LOCAL web front-end over the existing toolkit CLIs. It does NOT rewrite the
backend and NEVER connects to Seller Central. You upload the H10 exports you made
yourself, click Run, and it drives:
    research/asin_picker.py   -> ASIN-BATCH-REPORT.md   (10 ASIN)
    research/phaseA_master.py -> master-keywords.xlsx   (ranked keywords)
    research/seed_expand.py   -> NEW-SEEDS.md           (expansion seeds)
    pipeline.py               -> PROJECT-MANIFEST.json  (gate board)
then renders the results in a modern Amazon-style layout. The "Generate listing
brief" button produces a ready-to-paste prompt bundle for Claude to build the
listing + A+ + 10-photo brief (no API key needed).

Run:
    pip install -r ../requirements.txt
    python app.py
    # open http://127.0.0.1:5000
"""
import os, sys, re, glob, json, subprocess, traceback, shutil
from datetime import datetime
from flask import Flask, request, jsonify, render_template
try:
    import requests
except Exception:
    requests = None

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # toolkit root
RUNS = os.path.join(ROOT, "runs")
PY = sys.executable or "python3"
sys.path.insert(0, os.path.join(ROOT, "core"))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024   # 64 MB uploads

# API key + model live ONLY in memory for this local session — never written to disk, never in code.
# The user types their own key into their own local dashboard; Claude never sees or stores it.
SETTINGS = {"api_key": "", "model": ""}
PRODUCTS_FILE = os.path.join(RUNS, "_products.json")
ANTHROPIC_URL = "https://api.anthropic.com/v1"

# ---------------------------------------------------------------- helpers
def proj_dir(name):
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", (name or "project").strip()) or "project"
    d = os.path.join(RUNS, safe)
    os.makedirs(d, exist_ok=True)
    return d, safe

def run_cli(args, cwd=ROOT, timeout=240):
    if args and args[0] in ("python", "python3"):
        args = [PY] + args[1:]
    try:
        # Windows: children default to cp1252 on a pipe and crash printing emoji/arrows.
        p = subprocess.run(args, capture_output=True, text=True, cwd=cwd, timeout=timeout,
                           encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        return {"rc": p.returncode, "out": (p.stdout or "") + (p.stderr or "")}
    except subprocess.TimeoutExpired:
        return {"rc": -1, "out": "timed out"}
    except Exception as e:
        return {"rc": -1, "out": f"{e}\n{traceback.format_exc()}"}

def parse_batches_meta(folder):
    """Phase 2 batch metadata from ASIN-BATCHES.json (authoritative). -> [] if absent."""
    p = os.path.join(folder, "ASIN-BATCHES.json")
    if not os.path.exists(p):
        return {}
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}
    return {
        "engine": "batch_optimizer_v2",
        "batches": [{"batch_id": b["batch_id"], "purpose": b["purpose"],
                     "quality_score": b["batch_quality_score"], "quality_label": b["quality_label"],
                     "penalties": b.get("batch_penalties", []),
                     "constraints": b.get("constraint_results", {}),
                     "paste": b["paste_ready_asins"]} for b in d.get("batches", [])],
        "warnings": d.get("warnings", []),
        "constraint_validation": d.get("constraint_validation", {}),
        "summary": d.get("summary", {}),
        "replacements": d.get("unselected_top_candidates", [])[:10],
    }


def parse_asins(folder):
    """ASIN-BATCHES.json is authoritative (Phase 2). The Markdown table is a
    backward-compatible fallback for runs produced before the optimizer."""
    jp = os.path.join(folder, "ASIN-BATCHES.json")
    if os.path.exists(jp):
        try:
            d = json.load(open(jp, encoding="utf-8"))
            out = []
            for b in d.get("batches", []):
                for m in b.get("asins", []):
                    km = m.get("key_metrics", {})
                    out.append({
                        "batch": b["batch_id"], "batch_label": b["quality_label"],
                        "batch_score": b["batch_quality_score"],
                        "rank": m["rank_within_batch"], "asin": m["asin"],
                        "klass": m["activity_state"], "brand": m.get("brand") or "?",
                        "reviews": km.get("reviews") if km.get("reviews") is not None else "—",
                        "rev": f"${int(km['asin_revenue']):,}" if km.get("asin_revenue") else "$0",
                        "note": m.get("why_selected", ""),
                        "title": m.get("title", ""),
                        "scores": m.get("component_scores", {}),
                        "risk_flags": m.get("risk_flags", []),
                    })
            if out:
                return out
        except Exception:
            pass   # fall through to the legacy parser

    p = os.path.join(folder, "ASIN-BATCH-REPORT.md")
    out = []
    if not os.path.exists(p):
        return out
    for line in open(p, encoding="utf-8", errors="ignore"):
        if not line.startswith("|"):
            continue
        # | # | Batch | ASIN | Class | Reviews | Est $/mo | Ful | Brand | Why |
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) < 9 or not c[0].isdigit() or not re.fullmatch(r"B0[A-Z0-9]{8}", c[2]):
            continue
        out.append({"rank": int(c[0]), "batch": int(c[1]), "asin": c[2], "klass": c[3],
                    "reviews": c[4], "rev": c[5], "ful": c[6], "brand": c[7], "note": c[8][:160]})
    return out

def parse_keywords(folder):
    """Read master-keywords.xlsx 'Top Picks' -> ranked keyword rows."""
    p = os.path.join(folder, "master-keywords.xlsx")
    if not os.path.exists(p):
        return []
    try:
        import pandas as pd
        xl = pd.ExcelFile(p)
        sheet = "Top Picks" if "Top Picks" in xl.sheet_names else xl.sheet_names[0]
        df = xl.parse(sheet).fillna("")
    except Exception:
        return []
    cols = {c.lower().strip(): c for c in df.columns}
    def pick(*names):
        for n in names:
            if n in cols:
                return cols[n]
        return None
    kw_c = pick("keyword", "keyword phrase", "phrase", "search terms")
    sv_c = pick("search volume", "search vol", "sv", "amazon_sv")
    op_c = pick("opportunity", "opportunity score", "score")
    it_c = pick("intent", "bucket", "type")
    pl_c = pick("placement", "use in", "where")
    rows = []
    for _, r in df.iterrows():
        kw = str(r[kw_c]).strip() if kw_c else ""
        if not kw:
            continue
        rows.append({
            "keyword": kw,
            "sv": str(r[sv_c]).strip() if sv_c else "",
            "opportunity": str(r[op_c]).strip() if op_c else "",
            "intent": str(r[it_c]).strip() if it_c else "",
            "placement": str(r[pl_c]).strip() if pl_c else "",
        })
    return rows[:20]

def parse_seeds(folder):
    """Read NEW-SEEDS.md -> [{seed, note}]."""
    p = os.path.join(folder, "NEW-SEEDS.md")
    out = []
    if not os.path.exists(p):
        return out
    for line in open(p, encoding="utf-8", errors="ignore"):
        s = line.strip()
        m = re.match(r"^[-*\d.)\s]+(.+)", s)
        if not m:
            continue
        txt = m.group(1).strip()
        if len(txt) < 3 or txt.lower().startswith(("seed", "same niche", "avg", "score", "column", "how")):
            continue
        # first cell of a markdown table row, or a bullet
        cell = txt.split("|")[0].strip() if "|" in txt else txt
        cell = re.sub(r"[`*]", "", cell).strip()
        if cell and len(cell) < 60 and re.search(r"[a-zA-Z]", cell):
            out.append({"seed": cell, "note": re.sub(r"\s+", " ", txt)[:140]})
    # dedupe
    seen, uniq = set(), []
    for x in out:
        k = x["seed"].lower()
        if k not in seen:
            seen.add(k); uniq.append(x)
    return uniq[:5]

def parse_intel(folder):
    p = os.path.join(folder, "KEYWORD-INTELLIGENCE.json")
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p, encoding="utf-8"))
        return {"has_ytrends": d.get("has_ytrends", False), "note": d.get("note", ""),
                "top": d.get("top", [])[:10]}
    except Exception:
        return None

def parse_gaps(folder):
    p = os.path.join(folder, "COMPETITOR-GAP.json")
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p, encoding="utf-8"))
        if not d.get("ok"):
            return None
        return {"n": d.get("n_competitors", 0), "gaps": d.get("gaps", [])}
    except Exception:
        return None

def parse_gates(folder):
    """Read PROJECT-MANIFEST.json -> gate board."""
    p = os.path.join(folder, "PROJECT-MANIFEST.json")
    if not os.path.exists(p):
        return {"locked": True, "gates": []}
    try:
        man = json.load(open(p, encoding="utf-8"))
    except Exception:
        return {"locked": True, "gates": []}
    PASS = {"GO", "APPROVED", "VERIFIED", "CONSISTENT", "PROVEN", "PARTIALLY_PROVEN", "OK"}
    gates = []
    for gid, g in (man.get("gates") or {}).items():
        st = g.get("status", "NOT_RUN")
        state = "pass" if st in PASS else ("block" if st in ("BLOCKED", "REVISE") else "wait")
        gates.append({"id": gid, "status": st, "state": state})
    gates.sort(key=lambda x: {"block": 0, "wait": 1, "pass": 2}[x["state"]])
    return {"locked": bool(man.get("publication_locked", True)),
            "overall": man.get("overall_status", "—"), "gates": gates}

def read_md(folder, name):
    p = os.path.join(folder, name)
    return open(p, encoding="utf-8", errors="ignore").read() if os.path.exists(p) else ""

def read_listing(folder):
    p = os.path.join(folder, "listing.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return None
    return None

def snapshot(folder, seed=""):
    return {
        "asins": parse_asins(folder),
        "batch_meta": parse_batches_meta(folder),
        "keywords": parse_keywords(folder),
        "seeds": parse_seeds(folder),
        "intel": parse_intel(folder),
        "gaps": parse_gaps(folder),
        "gates": parse_gates(folder),
        "listing": read_listing(folder),
        "seed": seed,
        "files": sorted(os.path.basename(f) for f in glob.glob(os.path.join(folder, "*"))
                        if os.path.isfile(f)),
    }

# ---------------------------------------------------------------- routes
@app.route("/")
def index():
    projects = sorted(os.path.basename(d) for d in glob.glob(os.path.join(RUNS, "*"))
                      if os.path.isdir(d))
    return render_template("index.html", projects=projects)

@app.route("/api/project/<name>")
def api_project(name):
    folder, _ = proj_dir(name)
    return jsonify(snapshot(folder))

@app.route("/api/upload", methods=["POST"])
def api_upload():
    name = request.form.get("project", "project")
    kind = request.form.get("kind", "file")   # xray | cerebro | listing | file
    folder, safe = proj_dir(name)
    saved = []
    for f in request.files.getlist("files"):
        if not f.filename:
            continue
        base = re.sub(r"[^a-zA-Z0-9_.-]+", "-", os.path.basename(f.filename))
        if kind == "xray" and "xray" not in base.lower():
            base = "xray_" + base
        if kind == "cerebro" and "cerebro" not in base.lower():
            base = "cerebro_" + base
        if kind == "ytrends" and "ytrend" not in base.lower():
            base = "ytrends_" + base
        if kind == "listing":
            base = "listing.json"
        f.save(os.path.join(folder, base))
        saved.append(base)
    return jsonify({"ok": True, "saved": saved, "files": snapshot(folder)["files"]})

@app.route("/api/run", methods=["POST"])
def api_run():
    data = request.get_json(force=True)
    name = data.get("project", "project")
    seed = (data.get("seed") or "").strip()
    # blank unless the user set one: seed.split()[0] is usually a stop word ("personalized"),
    # and each tool derives a better anchor from the seed itself.
    anchor = (data.get("anchor") or "").strip()
    exclude = (data.get("exclude") or "").strip()
    folder, safe = proj_dir(name)
    log = []

    # 1) ASIN batches — Phase 2 optimizer (file-type detection means any spreadsheet
    #    in the folder is classified, not just ones named *xray*).
    has_sheet = bool(glob.glob(os.path.join(folder, "*.xls*")) + glob.glob(os.path.join(folder, "*.csv")))
    if has_sheet and seed:
        args = [PY, os.path.join(ROOT, "research", "asin_batches.py"), folder, "--seed", seed, "--out", folder]
        if data.get("decoration_method"): args += ["--decoration-method", data["decoration_method"]]
        if exclude: args += ["--exclude", exclude]
        r = run_cli(args); log.append(("ASIN batches", r["rc"], r["out"][-700:]))
    else:
        log.append(("ASIN batches", "skip", "no export uploaded, or no seed phrase entered"))

    # 2) keyword master (needs a cerebro file in the folder)
    has_cerebro = any("cerebro" in os.path.basename(f).lower() for f in glob.glob(os.path.join(folder, "*.xls*"))) \
                  or any(f.lower().endswith((".csv",)) for f in glob.glob(os.path.join(folder, "*.csv")))
    if has_cerebro:
        # The niche argument decides keyword relevance, so it must be the seed phrase.
        # Passing the project name made a project called "T1" mark every keyword irrelevant.
        r = run_cli([PY, os.path.join(ROOT, "research", "phaseA_master.py"), folder, (seed or safe)])
        log.append(("Keyword master", r["rc"], r["out"][-500:]))
    else:
        log.append(("Keyword master", "skip", "no cerebro_*.xlsx uploaded"))

    # 3) seed expansion (needs master-keywords.xlsx)
    if os.path.exists(os.path.join(folder, "master-keywords.xlsx")):
        args = [PY, os.path.join(ROOT, "research", "seed_expand.py"), folder]
        if anchor: args += ["--anchor", anchor]
        r = run_cli(args); log.append(("Seed expander", r["rc"], r["out"][-500:]))
    else:
        log.append(("Seed expander", "skip", "no master-keywords.xlsx yet"))

    # 4) competitor gap analyzer (needs an xray file)
    if has_sheet:
        r = run_cli([PY, os.path.join(ROOT, "research", "competitor_gap_analyzer.py"), folder]); log.append(("Competitor gaps", r["rc"], r["out"][-400:]))
    else:
        log.append(("Competitor gaps", "skip", "no xray file"))

    # 5) keyword intelligence (H10 + optional YTrends) — the starting-decision ranker
    r = run_cli([PY, os.path.join(ROOT, "research", "keyword_intelligence.py"), folder]); log.append(("Keyword intelligence", r["rc"], r["out"][-400:]))

    # 6) gate board (safe — never touches Amazon)
    r = run_cli([PY, os.path.join(ROOT, "pipeline.py"), folder, safe]); log.append(("Gate pipeline", r["rc"], r["out"][-500:]))

    def _st(rc):
        if rc == "skip": return "SKIP"
        if rc == 0: return "SUCCESS"
        if rc == 3: return "REVIEW"          # gate pipeline: locked/needs review is expected, not a crash
        return "FAILED"
    snap = snapshot(folder, seed=seed)
    snap["log"] = [{"step": s, "rc": rc, "status": _st(rc), "out": o} for s, rc, o in log]
    snap["any_failed"] = any(l["status"] == "FAILED" for l in snap["log"])
    return jsonify(snap)

@app.route("/api/brief", methods=["POST"])
def api_brief():
    """Build a ready-to-paste prompt bundle for Claude to write the listing + A+ + photo brief."""
    data = request.get_json(force=True)
    name = data.get("project", "project")
    seed = (data.get("seed") or "").strip()
    folder, _ = proj_dir(name)
    kws = parse_keywords(folder)
    seeds = parse_seeds(folder)
    top5 = [k["keyword"] for k in kws[:5]]
    seed5 = [s["seed"] for s in seeds[:5]]
    opp = read_md(folder, "OPPORTUNITY-REPORT.md")[:2500]
    brief = build_brief(seed, top5, seed5, opp)
    return jsonify({"brief": brief, "top5": top5, "seed5": seed5})

def listing_json_prompt(folder, seed):
    """The full listing brief + a strict JSON-only instruction (shared by API and Claude Code)."""
    kws = [k["keyword"] for k in parse_keywords(folder)][:5]
    seeds = [s["seed"] for s in parse_seeds(folder)][:5]
    opp = read_md(folder, "OPPORTUNITY-REPORT.md")[:2000]
    return build_brief(seed, kws, seeds, opp) + """

IMPORTANT: Return ONLY a single JSON object (no prose, no markdown fences) with EXACTLY these keys:
{"title": "...", "bullets": ["..x5.."], "description": "...", "backend": "...",
 "aplus": [{"headline":"...","copy":"..."} x7], "photo_brief": ["..x10.."],
 "size_chart": "...", "ppc": {"exact":["..."],"phrase":["..."],"broad":["..."]}}"""

def build_brief(seed, top5, seed5, opp):
    kws = "\n".join(f"  {i+1}. {k}" for i, k in enumerate(top5)) or "  (run analysis first)"
    seeds = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(seed5)) or "  (run analysis first)"
    return f"""You are a senior Amazon US FBM listing + A+ specialist. Build a COMPLETE, policy-compliant
product page for this personalized-apparel product. Data-driven, honest, no invented claims.

PRODUCT SEED: {seed or '(fill in)'}

TOP 5 KEYWORDS (from my Helium 10 data — use these to rank/index):
{kws}

5 EXPANSION SEEDS (next products to build later):
{seeds}

FOLLOW THESE CURRENT AMAZON RULES (2026):
- TITLE: category-specific character cap is now ENFORCED (auto-truncation) as of Jul 27 2026.
  Verify my apparel category's exact cap in Seller Central; front-load the #1 keyword in the first
  ~60 characters (mobile). NO promo words (best/cheap/sale/free shipping/guarantee/bestseller),
  NO ALL-CAPS, NO repeated keywords, NO special symbols.
- IMAGES: main = real product, pure white (RGB 255,255,255), product >=85% of frame, no text/logo/
  inset, >=2000px. Up to 9 images; only 7 show on desktop — put the strongest 7 first.
- A10: organic sales + external traffic + engagement + A+ content now outrank pure PPC. Treat A+ as
  a ranking lever, not decoration.

DELIVER:
1. TITLE (to the category cap, front-loaded).
2. 5 BULLETS (benefit-led, keyword-woven, claim-safe).
3. DESCRIPTION (or note if brand-registered -> A+ replaces it).
4. BACKEND SEARCH TERMS (<=249 bytes, no repeats of the title).
5. A+ CONTENT — 7 modules (headline + copy + image direction each).
6. 10-PHOTO BRIEF: main (white bg), 6 secondary (lifestyle, macro-stitch, size chart, how-to-
   personalize, color grid, gift/occasion), + note which 7 lead on desktop.
7. SIZE CHART (real measurements placeholder) + HOW-TO-USE/PERSONALIZE graphic direction.
8. A PPC starter plan (exact/phrase/broad seeds from the keywords above).

Return it as clean sections I can paste into my toolkit's listing.json + creative-brief.json.

--- OPPORTUNITY REPORT (context) ---
{opp or '(no OPPORTUNITY-REPORT.md yet — run analysis)'}"""

# ---------------------------------------------------------------- products board
def load_products():
    if os.path.exists(PRODUCTS_FILE):
        try:
            return json.load(open(PRODUCTS_FILE, encoding="utf-8"))
        except Exception:
            return []
    return []

def write_products(items):
    os.makedirs(RUNS, exist_ok=True)
    json.dump(items, open(PRODUCTS_FILE, "w", encoding="utf-8"), indent=2, ensure_ascii=False)

@app.route("/api/products")
def api_products():
    return jsonify(load_products())

@app.route("/api/products/save", methods=["POST"])
def api_products_save():
    data = request.get_json(force=True)
    name = data.get("project", "project")
    folder, safe = proj_dir(name)
    L = read_listing(folder) or {}
    kws = parse_keywords(folder)
    gates = parse_gates(folder)
    entry = {
        "project": safe,
        "seed": (data.get("seed") or "").strip(),
        "title": (L.get("title") if isinstance(L, dict) else "") or "(no listing yet)",
        "top_keyword": (kws[0]["keyword"] if kws else ""),
        "unlocked": gates.get("locked") is False,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    items = [x for x in load_products() if x.get("project") != safe]   # upsert by project
    items.insert(0, entry)
    write_products(items)
    return jsonify({"ok": True, "products": items})

@app.route("/api/products/delete", methods=["POST"])
def api_products_delete():
    name = request.get_json(force=True).get("project")
    items = [x for x in load_products() if x.get("project") != name]
    write_products(items)
    return jsonify({"ok": True, "products": items})

# ---------------------------------------------------------------- settings + AI auto-build
@app.route("/api/settings", methods=["POST"])
def api_settings():
    data = request.get_json(force=True)
    if "api_key" in data:
        SETTINGS["api_key"] = (data.get("api_key") or "").strip()
    if "model" in data:
        SETTINGS["model"] = (data.get("model") or "").strip()
    return jsonify({"ok": True, "has_key": bool(SETTINGS["api_key"]), "model": SETTINGS["model"]})

@app.route("/api/models")
def api_models():
    if not requests:
        return jsonify({"ok": False, "error": "the 'requests' package is not installed"}), 500
    if not SETTINGS["api_key"]:
        return jsonify({"ok": False, "error": "enter your Anthropic API key first"}), 400
    try:
        r = requests.get(ANTHROPIC_URL + "/models",
                         headers={"x-api-key": SETTINGS["api_key"], "anthropic-version": "2023-06-01"},
                         timeout=30)
        if r.status_code != 200:
            return jsonify({"ok": False, "error": f"Anthropic API {r.status_code}: {r.text[:200]}"}), 400
        ids = [m.get("id") for m in r.json().get("data", []) if m.get("id")]
        return jsonify({"ok": True, "models": ids})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

LISTING_SCHEMA_VERSION = "2.4"
TITLE_MAX = 75            # P0-02: Amazon non-media title cap (effective 2026-07-27)
ITEM_HIGHLIGHTS_MAX = 125

def validate_listing(L):
    """P0-06: field-level validation against the minimum listing contract. Returns (ok, errors)."""
    errs = []
    if not isinstance(L, dict):
        return False, ["listing is not a JSON object"]
    title = L.get("title")
    if not isinstance(title, str) or not title.strip():
        errs.append("title: missing or empty")
    elif len(title) > TITLE_MAX:
        errs.append(f"title: {len(title)} chars > {TITLE_MAX} (Amazon non-media limit, effective 2026-07-27)")
    bullets = L.get("bullets") or L.get("bullet_points")
    if not isinstance(bullets, list) or len(bullets) < 1:
        errs.append("bullets: expected a non-empty list")
    ih = L.get("item_highlights") or L.get("product_highlights")
    if ih:
        t = ih if isinstance(ih, str) else ", ".join(str(x) for x in ih)
        if len(t) > ITEM_HIGHLIGHTS_MAX:
            errs.append(f"item_highlights: {len(t)} chars > {ITEM_HIGHLIGHTS_MAX}")
    ap = L.get("aplus") or L.get("aplus_modules") or L.get("a_plus")
    if ap is not None and not isinstance(ap, list):
        errs.append("aplus: expected a list of modules")
    return (len(errs) == 0), errs

def safe_write_listing(folder, listing):
    """Validate, then write only if valid — a bad build never overwrites the last good listing.json.
    Backs up the prior valid file. Returns (ok, errors)."""
    ok, errs = validate_listing(listing)
    if not ok:
        return False, errs
    listing = dict(listing); listing.setdefault("schema_version", LISTING_SCHEMA_VERSION)
    dest = os.path.join(folder, "listing.json")
    if os.path.exists(dest):
        try:
            import shutil as _sh
            _sh.copyfile(dest, os.path.join(folder, "listing.prev.json"))
        except Exception:
            pass
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(listing, f, indent=2, ensure_ascii=False)
    return True, []

def _extract_json(text):
    """Pull the first {...} JSON object out of a model reply."""
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j == -1 or j < i:
        return None
    try:
        return json.loads(text[i:j + 1])
    except Exception:
        return None

@app.route("/api/build", methods=["POST"])
def api_build():
    """Auto-build the listing via the user's Anthropic key, write listing.json, return preview."""
    if not requests:
        return jsonify({"ok": False, "error": "'requests' not installed"}), 500
    if not SETTINGS["api_key"]:
        return jsonify({"ok": False, "error": "enter your Anthropic API key in Settings first"}), 400
    if not SETTINGS["model"]:
        return jsonify({"ok": False, "error": "pick a model in Settings first (Load models)"}), 400
    data = request.get_json(force=True)
    name = data.get("project", "project")
    seed = (data.get("seed") or "").strip()
    folder, _ = proj_dir(name)
    prompt = listing_json_prompt(folder, seed)
    try:
        r = requests.post(ANTHROPIC_URL + "/messages",
            headers={"x-api-key": SETTINGS["api_key"], "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": SETTINGS["model"], "max_tokens": 4096,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=180)
        if r.status_code != 200:
            return jsonify({"ok": False, "error": f"Anthropic API {r.status_code}: {r.text[:300]}"}), 400
        text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    listing = _extract_json(text)
    if not listing:
        return jsonify({"ok": False, "error": "model did not return valid JSON", "raw": text[:600]}), 400
    ok, errs = safe_write_listing(folder, listing)
    with open(os.path.join(folder, "AI-LISTING-RAW.md"), "w", encoding="utf-8") as f:
        f.write(text)
    if not ok:
        return jsonify({"ok": False, "error": "built listing failed validation (kept your previous listing.json)",
                        "field_errors": errs, "listing": listing}), 400
    return jsonify({"ok": True, "listing": listing})

@app.route("/api/generate-listing", methods=["POST"])
def api_generate_listing():
    """Deterministic structured listing from research data (no AI, no cost). Runs listing_generator,
    validates, safe-writes listing.json, and returns the brief + preview."""
    data = request.get_json(force=True)
    name = data.get("project", "project")
    seed = (data.get("seed") or "").strip()
    folder, _ = proj_dir(name)
    try:
        sys.path.insert(0, os.path.join(ROOT, "listing"))
        import importlib, listing_generator as LG
        importlib.reload(LG)
        r = LG.generate(folder, seed)
    except Exception as e:
        return jsonify({"ok": False, "error": f"generator error: {e}"}), 500
    if not r.get("ok"):
        return jsonify({"ok": False, "error": r.get("reason", "generation failed")}), 400
    ok, errs = safe_write_listing(folder, r["listing"])
    if not ok:
        return jsonify({"ok": False, "error": "generated listing failed validation (kept previous)",
                        "field_errors": errs, "listing": r["listing"]}), 400
    return jsonify({"ok": True, "listing": r["listing"], "brief": r["brief"],
                    "proof_required": r.get("proof_required", []), "engine": "listing_generator_v2"})

@app.route("/api/version")
def api_version():
    """P0-01: report toolkit version + whether each key module actually imports (VERIFIED/FAILED)."""
    ver = "unknown"
    try:
        m = re.search(r'tool_version:\s*"([^"]+)"', open(os.path.join(ROOT, "config.yaml"), encoding="utf-8").read())
        ver = m.group(1) if m else "unknown"
    except Exception:
        pass
    mods = {}
    for name, rel in (("keyword_intelligence", "research/keyword_intelligence.py"),
                      ("competitor_gap_analyzer", "research/competitor_gap_analyzer.py"),
                      ("phaseA_master", "research/phaseA_master.py"),
                      ("asin_picker", "research/asin_picker.py")):
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            mods[name] = "UNAVAILABLE"; continue
        try:
            # compile (not exec) — verifies the file is valid Python without running CLI side-effects
            compile(open(path, encoding="utf-8").read(), path, "exec")
            mods[name] = "VERIFIED"
        except Exception:
            mods[name] = "FAILED"
    bm = os.path.join(ROOT, "BUILD-MANIFEST.json")
    has_bm = os.path.exists(bm)
    return jsonify({"version": ver, "modules": mods, "build_manifest": has_bm})

@app.route("/api/claude_code_status")
def api_cc_status():
    """Is the Claude Code CLI installed on this machine?"""
    path = shutil.which("claude")
    return jsonify({"available": bool(path), "path": path or ""})

@app.route("/api/build_cc", methods=["POST"])
def api_build_cc():
    """Build the listing using the local Claude Code CLI (included in the user's Pro/Max plan).
    No API key, no per-token charge — it uses the subscription the user is already logged into.
    We only shell out to a tool the user installed themselves; we never automate claude.ai in a browser."""
    claude = shutil.which("claude")
    if not claude:
        return jsonify({"ok": False, "error": "Claude Code CLI not found. Install it and sign in with your Max plan "
                                               "(https://support.claude.com/en/articles/11145838), then reload."}), 400
    data = request.get_json(force=True)
    name = data.get("project", "project")
    seed = (data.get("seed") or "").strip()
    folder, _ = proj_dir(name)
    prompt = listing_json_prompt(folder, seed) + "\n\nOutput the JSON object only. Do not use any tools."
    try:
        # -p / --print = non-interactive: prints the answer and exits. Uses the signed-in subscription.
        p = subprocess.run([claude, "-p", prompt], capture_output=True, text=True, cwd=folder, timeout=300,
                           encoding="utf-8", errors="replace")
        text = (p.stdout or "").strip() or (p.stderr or "")
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Claude Code timed out (300s)."}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    listing = _extract_json(text)
    if not listing:
        return jsonify({"ok": False, "error": "Claude Code did not return valid JSON", "raw": text[:600]}), 400
    ok, errs = safe_write_listing(folder, listing)
    with open(os.path.join(folder, "AI-LISTING-RAW.md"), "w", encoding="utf-8") as f:
        f.write(text)
    if not ok:
        return jsonify({"ok": False, "error": "built listing failed validation (kept your previous listing.json)",
                        "field_errors": errs, "listing": listing}), 400
    return jsonify({"ok": True, "listing": listing, "engine": "claude-code"})

if __name__ == "__main__":
    os.makedirs(RUNS, exist_ok=True)
    print("AMZ FBM cockpit -> http://127.0.0.1:5000   (local only; never touches Seller Central)")
    app.run(host="127.0.0.1", port=5000, debug=False)
