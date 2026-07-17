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
import os, sys, re, glob, json, subprocess, traceback, shutil, time, functools
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
sys.path.insert(0, os.path.join(ROOT, "listing"))
import keyword_source_adapter as KSA
import category_policy_registry as CPR
import product_fact_loader as PFL
import page_auditor as PA
# Session 5B: shared runtime-safety authorities (offline-first, secret-safe).
import runtime_policy as RP
import network_policy as NP
import subprocess_runner as SR
import diagnostics as DIAG
# Session 5C: local launcher/install authorities for the compact runtime panel.
import instance_manager as IM
import local_install as LI
import windows_integration as WI

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024   # 64 MB uploads

# One shared runtime policy for every route + startup (Session 5B / ACT-016).
# Offline-only by default: no external client is created, no route can reach the
# Internet, and the app binds only to loopback. Loaded once at import.
POLICY = RP.load_runtime_policy()
START_TIME = time.time()
SERVICE_NAME = "amz-fbm-toolkit"
# Per-process identity so a launcher can verify THIS instance owns the port
# (Session 5C). Set by the launcher via env; otherwise generated for a manual run.
# It is an opaque local nonce — never a secret and safe to echo in /healthz.
INSTANCE_ID = os.environ.get("AMZ_FBM_INSTANCE_ID") or os.urandom(8).hex()
STARTUP_MODE = "launcher" if os.environ.get("AMZ_FBM_INSTANCE_ID") else "manual"

# API key + model live ONLY in memory for this local session — never written to disk, never in code.
# The user types their own key into their own local dashboard; Claude never sees or stores it.
SETTINGS = {"api_key": "", "model": ""}
PRODUCTS_FILE = os.path.join(RUNS, "_products.json")
ANTHROPIC_URL = "https://api.anthropic.com/v1"


def tool_version():
    try:
        txt = open(os.path.join(ROOT, "config.yaml"), encoding="utf-8").read()
        m = re.search(r'tool_version:\s*"([^"]+)"', txt)
        return m.group(1) if m else "unknown"
    except Exception:
        return "unknown"


def git_commit():
    """Best-effort local commit id from .git (no subprocess, no network)."""
    try:
        head = open(os.path.join(ROOT, ".git", "HEAD"), encoding="utf-8").read().strip()
        if head.startswith("ref:"):
            ref = head.split(" ", 1)[1].strip()
            return open(os.path.join(ROOT, ".git", ref), encoding="utf-8").read().strip()[:12]
        return head[:12]
    except Exception:
        return "unknown"


def requires_external_ai(fn):
    """Gate a route that would use external AI / the Internet. In offline mode
    (the default) it never runs the handler — no client is created, no request is
    made — and returns a clear, secret-free EXTERNAL_AI_DISABLED response."""
    @functools.wraps(fn)
    def _wrap(*a, **kw):
        if not POLICY.external_ai_enabled:
            DIAG.record_event(DIAG.EXTERNAL_AI_DISABLED,
                              f"blocked external-AI route {fn.__name__!r} (offline mode)")
            return jsonify({
                "ok": False,
                "status": "OFFLINE_ONLY",
                "error_code": "EXTERNAL_AI_DISABLED",
                "message": "External AI is disabled. The toolkit uses local "
                           "deterministic engines.",
            }), 403
        return fn(*a, **kw)
    return _wrap

# ---------------------------------------------------------------- helpers
def proj_dir(name):
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", (name or "project").strip()) or "project"
    d = os.path.join(RUNS, safe)
    os.makedirs(d, exist_ok=True)
    return d, safe

def run_cli(args, cwd=ROOT, timeout=240):
    # Route every CLI shell-out through the shared bounded runner (Session 5B /
    # ACT-020): explicit timeout, tree-kill on hang, secret-safe, UTF-8 safe.
    if args and args[0] in ("python", "python3"):
        args = [PY] + args[1:]
    stage = os.path.basename(str(args[1])) if (len(args) > 1 and str(args[1]).endswith(".py")) \
        else (os.path.basename(str(args[0])) if args else "cli")
    r = SR.run_subprocess(list(args), cwd=cwd, timeout_seconds=timeout, stage_name=stage,
                          allowed_exit_codes=tuple(range(0, 256)))
    if r.error_code == DIAG.SUBPROCESS_START_FAILED:
        return {"rc": -1, "out": r.stderr or r.diagnostic_summary}
    if r.timed_out:
        return {"rc": -1, "out": r.diagnostic_summary}
    return {"rc": r.exit_code, "out": (r.stdout or "") + (r.stderr or "")}

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

def load_keyword_source(folder, allow_legacy_unsafe=False):
    """The SAME authoritative source the listing generator uses -> (source|None, error|None).

    The dashboard used to read master-keywords.xlsx "Top Picks" on its own, so it could show a
    different keyword truth than the listing (ACT-002). Both now go through the shared adapter, which
    is why they report the same source SHA-256. Legacy stays opt-in here too, and a blocked legacy
    project reports an actionable error instead of an empty keyword list.
    """
    try:
        return KSA.load_keyword_source(folder, allow_legacy_unsafe=allow_legacy_unsafe), None
    except KSA.NoKeywordSourceError as e:
        if e.legacy_blocked:
            return None, str(e)                # legacy exists but is not enabled — say so
        return None, None                      # nothing researched yet — not an error to show
    except KSA.KeywordSourceError as e:
        return None, str(e)                    # malformed/incompatible — surface it, never fall back


def parse_keywords(folder, source=None, allow_legacy_unsafe=False):
    """Ranked keyword rows for the cockpit, from the authoritative source only."""
    src = source or load_keyword_source(folder, allow_legacy_unsafe)[0]
    if not src:
        return []
    rows = []
    for r in src.eligible_keywords()[:20]:
        sv = r["search_volume"]
        rows.append({
            "keyword": r["keyword_exact"],
            "sv": str(int(sv)) if sv is not None else "",
            "opportunity": "",                 # not a field of the authoritative source — never invented
            "intent": r["normalized_tier"],
            "placement": "",
        })
    return rows


def keyword_source_summary(folder, source=None, error=None, allow_legacy_unsafe=False):
    """Source identity + tier/eligibility counts for the cockpit header."""
    if source is None and error is None:
        source, error = load_keyword_source(folder, allow_legacy_unsafe)
    if error:
        return {"error": error}
    if not source:
        return {}
    tiers = source.tier_counts
    return {
        "file": source.source_file, "schema": source.source_schema,
        "run_id": source.source_run_id, "mode": source.source_mode,
        "sha256": source.source_sha256, "legacy_unsafe": source.legacy_unsafe,
        "tier_a": tiers.get(KSA.TIER_A, 0), "tier_b": tiers.get(KSA.TIER_B, 0),
        "review": tiers.get(KSA.REVIEW, 0), "rejected": tiers.get(KSA.REJECTED, 0),
        "excluded": source.eligibility_counts["ineligible"],
        "eligible": source.eligibility_counts["eligible"],
        "warnings": source.warnings,
    }

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

def category_policy_summary(folder, listing=None):
    """The resolved category policy for the cockpit — the SAME limits the generator and validator use
    (ACT-004). The frontend reads its title/highlights counters from this payload, never hardcoded."""
    L = listing if listing is not None else read_listing(folder)
    cat = ((L or {}).get("category")) or "apparel"
    p = CPR.resolve_category_policy(cat, marketplace="US")
    return {
        "category": p.product_category, "category_identifier": p.category_identifier,
        "title_hard_limit": p.title_hard_limit, "title_recommended_target": p.title_recommended_target,
        "backend_byte_ceiling": p.backend_byte_ceiling, "item_highlights_max": ITEM_HIGHLIGHTS_MAX,
        "item_highlights_max_count": p.item_highlights_max_count,
        "item_highlights_supported": p.supports_item_highlights(),
        "fallback_used": p.fallback_used, "warning_state": p.warning_state,
        "policy_source": p.policy_source,
    }

def product_fact_summary(folder):
    """Product-fact source + counts + OWNER_FACT_REQUIRED for the cockpit (ACT-006)."""
    try:
        pf = PFL.load_product_facts(folder=folder)
    except PFL.ProductFactError as e:
        return {"error": str(e)}
    return {
        "source_file": pf.source_file, "source_sha256": pf.source_sha256,
        "present": pf.source_sha256 is not None,
        "verified": pf.verified_fact_count, "owner_review": pf.owner_review_count,
        "unknown": pf.unknown_fact_count, "blocked": pf.blocked_fact_count,
        "owner_fact_required": pf.owner_fact_required, "warnings": pf.warnings[:6],
    }

def _aplus_evidence_summary(L):
    """Session 4 cockpit view of the evidence-aware A+ payload: capability, Basic module count + READY
    count, per-module status, the missing facts / real assets / claim & placeholder blockers, Premium
    eligibility + selected dynamic modules, the Basic-fallback status and copy-ready inclusion state."""
    ac = L.get("aplus_content") if isinstance(L.get("aplus_content"), dict) else None
    if not ac:
        return None
    comp = ac.get("compliance") or {}
    prem = ac.get("premium") or {}
    pub = ac.get("publishable_modules") or []
    return {
        "capability": ac.get("capability") or L.get("aplus_capability"),
        "basic_module_count": ac.get("basic_module_count"),
        "basic_ready_count": ac.get("basic_ready_count"),
        "basic_ready": ac.get("basic_ready"),
        "module_states": {m.get("module_key"): m.get("status") for m in (ac.get("basic_modules") or [])},
        "missing_facts": comp.get("missing_facts", []),
        "missing_real_assets": comp.get("missing_real_assets", []),
        "claim_blockers": comp.get("claim_blockers", []),
        "placeholder_blockers": comp.get("placeholder_blockers", []),
        "premium_eligible": prem.get("premium_eligible"),
        "premium_status": prem.get("premium_status"),
        "premium_module_count": len(prem.get("modules") or []) if prem.get("modules") else 0,
        "selected_dynamic_modules": prem.get("selected_dynamic_modules", []),
        "eligibility_unverified": prem.get("eligibility_unverified", False),
        "basic_fallback_positions": comp.get("fallback_positions", []),
        "publishable_module_count": len(pub),
        "copy_ready_included": bool(pub) and str(L.get("aplus_state") or "").startswith("READY_"),
    }


def _backend_optimizer_summary(L):
    """Session 5A cockpit view of the audited backend optimizer (ACT-009): the string, byte usage,
    included/excluded counts, visible overlap, incremental coverage, per-risk exclusion counts, source hash."""
    audit = L.get("backend_audit") if isinstance(L.get("backend_audit"), dict) else None
    backend = L.get("backend") or ""
    if not audit:
        return {"backend": backend, "bytes_used": len(backend.encode("utf-8")), "audit_present": False}
    return {
        "backend": backend,
        "bytes_used": audit.get("bytes_used"),
        "byte_ceiling": audit.get("byte_ceiling"),
        "bytes_remaining": audit.get("bytes_remaining"),
        "included_count": audit.get("included_count"),
        "excluded_count": audit.get("excluded_count"),
        "visible_field_overlap": (audit.get("visible_field_overlap") or [])[:20],
        "incremental_coverage_count": len(audit.get("incremental_coverage") or []),
        "excluded_summary": audit.get("excluded_summary", {}),
        "risk_results": {k: (v.get("count") if isinstance(v, dict) else v)
                         for k, v in (audit.get("risk_results") or {}).items()},
        "source_sha256": (audit.get("source_hashes") or {}).get("keyword_source_sha256"),
        "audit_present": True,
    }


def _item_highlights_capability_summary(L):
    """Session 5A cockpit view of the capability/evidence-aware item highlights (ACT-010): category support,
    generated/publishable/blocked counts, per-highlight claim lineage, missing facts, exclusion reasons."""
    content = L.get("item_highlights_content") if isinstance(L.get("item_highlights_content"), dict) else None
    if not content:
        return None
    pub = content.get("publishable_highlights") or []
    return {
        "category_support_state": content.get("category_support_state"),
        "max_count": content.get("max_count"),
        "generated_count": len(content.get("highlights") or []),
        "publishable_count": content.get("publishable_count"),
        "blocked_count": content.get("blocked_count"),
        "publishable": [{"concept": h.get("concept"), "text": h.get("text"),
                         "claim_ids": h.get("claim_ids", [])} for h in pub],
        "missing_facts": content.get("owner_fact_required", []),
        "manual_review": [{"concept": r.get("concept"),
                           "reason": r.get("exclusion_reason") or r.get("publishability_status")}
                          for r in (content.get("manual_review_report") or [])],
    }


def listing_copy_summary(folder, listing=None):
    """Session 3 cockpit view (ACT-005/007/008/015): title candidates + scores, recommended title and
    mobile preview, the five bullet jobs and their publishability, verified/owner-review/blocked claim
    counts, OWNER_FACT_REQUIRED, missing evidence, the shared PageAuditor result, and the last-valid-
    listing state. Everything is read from the generator's listing.json + the audit artifacts on disk."""
    L = listing if listing is not None else read_listing(folder)
    if not isinstance(L, dict):
        return {}
    audit = L.get("audit") or {}
    out = {
        "title_candidates": L.get("title_candidates"),
        "title_recommended": L.get("title_recommended") or L.get("title"),
        "title_recommended_candidate": L.get("title_recommended_candidate"),
        "title_scores": {k: {"length": v.get("length"), "score": v.get("score")}
                         for k, v in (L.get("title_scores") or {}).items()},
        "mobile_preview": L.get("mobile_preview"),
        "bullet_jobs": [{"bullet_number": b.get("bullet_number"), "job": b.get("job"),
                         "publishability": b.get("publishability"),
                         "claim_ids": b.get("claim_ids", [])}
                        for b in (L.get("bullet_objects") or [])],
        "claim_counts": (L.get("claim_evidence") or {}).get("counts"),
        "claim_evidence_sha256": (L.get("claim_evidence") or {}).get("source_content_sha256"),
        "owner_fact_required": L.get("owner_fact_required", []),
        "missing_claim_evidence": L.get("missing_claim_evidence", []),
        "missing_requirements": L.get("missing_requirements", []),
        # PATCH 2: publishability is the classification the owner reads — a safe draft is NOT publishable.
        "publishability": L.get("publishability") or audit.get("publishability"),
        "publishability_status": L.get("publishability_status") or audit.get("publishability")
        or audit.get("status"),
        # PATCH 1: legacy A+ is quarantined — the cockpit shows it is blocked and draft-only.
        "aplus_state": L.get("aplus_state"),
        "aplus_blocked_draft_only": L.get("aplus_state") == PA.APLUS_BLOCKED_LEGACY_UNVERIFIED,
        "aplus_draft_module_count": len((L.get("aplus_draft") or {}).get("modules") or []),
        # Session 4 — the evidence-aware, capability-based A+ state.
        "aplus_evidence": _aplus_evidence_summary(L),
        # Session 5A — the audited backend optimizer + capability/evidence-aware item highlights.
        "backend_optimizer": _backend_optimizer_summary(L),
        "item_highlights_capability": _item_highlights_capability_summary(L),
        "item_highlights_state": L.get("item_highlights_state"),
        "audited_text_fields": audit.get("audited_text_fields", {}),
        "page_audit": {"status": audit.get("status"),
                       "publishability": audit.get("publishability"),
                       "hard_failures": audit.get("hard_failures", []),
                       "warnings": audit.get("warnings", [])},
    }
    # last-valid-listing state from the audit artifacts on disk — the last SAFE DRAFT and the last
    # PUBLISHABLE listing are surfaced separately so the cockpit never conflates a draft with a listing.
    def _read(name):
        p = os.path.join(folder, name)
        if os.path.exists(p):
            try:
                return json.load(open(p, encoding="utf-8"))
            except Exception:
                return None
        return None
    out["last_valid_listing"] = _read("LAST-VALID-LISTING-METADATA.json")
    out["last_safe_draft"] = _read("LAST-SAFE-DRAFT-METADATA.json")
    out["last_publishable_listing"] = _read("LAST-PUBLISHABLE-LISTING-METADATA.json")
    fc = _read("FAILED-LISTING-CANDIDATE.json")
    out["failed_candidate"] = {"status": fc.get("status"), "publishability": fc.get("publishability"),
                               "hard_failures": fc.get("hard_failures", [])} if fc else None
    return out

def snapshot(folder, seed=""):
    src, src_err = load_keyword_source(folder)      # loaded once, shared by both views below
    listing = read_listing(folder)
    return {
        "asins": parse_asins(folder),
        "batch_meta": parse_batches_meta(folder),
        "keywords": parse_keywords(folder, source=src),
        "keyword_source": keyword_source_summary(folder, source=src, error=src_err),
        "seeds": parse_seeds(folder),
        "intel": parse_intel(folder),
        "gaps": parse_gaps(folder),
        "gates": parse_gates(folder),
        "listing": listing,
        "category_policy": category_policy_summary(folder, listing=listing),
        "product_facts": product_fact_summary(folder),
        "listing_copy": listing_copy_summary(folder, listing=listing),
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
# These routes are the ONLY external-AI surface. They are gated by @requires_external_ai,
# so in offline mode (the default) they never create a client or make a request — they
# return EXTERNAL_AI_DISABLED. The normal deterministic workflow uses none of them.
@app.route("/api/settings", methods=["POST"])
@requires_external_ai
def api_settings():
    data = request.get_json(force=True)
    if "api_key" in data:
        SETTINGS["api_key"] = (data.get("api_key") or "").strip()
    if "model" in data:
        SETTINGS["model"] = (data.get("model") or "").strip()
    return jsonify({"ok": True, "has_key": bool(SETTINGS["api_key"]), "model": SETTINGS["model"]})

@app.route("/api/models")
@requires_external_ai
def api_models():
    if not requests:
        return jsonify({"ok": False, "error": "the 'requests' package is not installed"}), 500
    if not SETTINGS["api_key"]:
        return jsonify({"ok": False, "error": "enter your Anthropic API key first"}), 400
    try:
        NP.assert_outbound_allowed("anthropic-models", ANTHROPIC_URL, policy=POLICY)
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
ITEM_HIGHLIGHTS_MAX = 125

def validate_listing(L, registry=None):
    """Field-level + content-safety validation via the shared PageAuditor (ACT-015). Returns (ok, errors).

    The dashboard, the CLI validator (compliance/listing_validate.py) and the proof gate all reach the
    same block/allow decision through listing.page_auditor — one authority, not a contradictory
    independent safety path. Title/backend limits resolve through the shared category-policy registry
    (ACT-004); unsupported claims, rejected/wrong-audience keyword leakage, backend overflow and A+
    placeholder errors all block here now, so they can no longer slip past safe_write_listing.
    """
    if not isinstance(L, dict):
        return False, ["listing is not a JSON object"]
    policy = CPR.resolve_category_policy(L.get("category") or "apparel", marketplace="US",
                                         registry=registry)
    return PA.audit_verdict(L, policy=policy, allow_warnings=True)

def safe_write_listing(folder, listing):
    """Audit, then promote to the last-valid listing ONLY if safe — a blocked candidate never overwrites
    listing.json (ACT-015). Backs up the prior valid file, writes atomically, and records the audit,
    the failed candidate (if any) and last-valid metadata. Returns (ok, errors)."""
    listing = dict(listing)
    listing.setdefault("schema_version", LISTING_SCHEMA_VERSION)
    policy = CPR.resolve_category_policy(listing.get("category") or "apparel", marketplace="US")
    result = PA.promote_if_safe(folder, listing, allow_warnings=True, policy=policy)
    if result["promoted"]:
        return True, []
    return False, [f"{h['category']}: {h['message']}" for h in result["audit"]["hard_failures"]]

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
@requires_external_ai
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
        NP.assert_outbound_allowed("anthropic-messages", ANTHROPIC_URL, policy=POLICY)
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
@requires_external_ai
def api_build_cc():
    """Build the listing using the local Claude Code CLI (included in the user's Pro/Max plan).
    No API key, no per-token charge — it uses the subscription the user is already logged into.
    We only shell out to a tool the user installed themselves; we never automate claude.ai in a browser.
    Gated by @requires_external_ai: unavailable while OFFLINE_ONLY (the default)."""
    claude = shutil.which("claude")
    if not claude:
        return jsonify({"ok": False, "error": "Claude Code CLI not found. Install it and sign in with your Max plan "
                                               "(https://support.claude.com/en/articles/11145838), then reload."}), 400
    data = request.get_json(force=True)
    name = data.get("project", "project")
    seed = (data.get("seed") or "").strip()
    folder, _ = proj_dir(name)
    prompt = listing_json_prompt(folder, seed) + "\n\nOutput the JSON object only. Do not use any tools."
    # -p / --print = non-interactive: prints the answer and exits. Bounded shared runner.
    r = SR.run_subprocess([claude, "-p", prompt], cwd=folder, timeout_seconds=300,
                          stage_name="claude-code-build", allowed_exit_codes=tuple(range(0, 256)))
    if r.timed_out:
        return jsonify({"ok": False, "error": "Claude Code timed out (300s)."}), 400
    if r.error_code == DIAG.SUBPROCESS_START_FAILED:
        return jsonify({"ok": False, "error": r.stderr or r.diagnostic_summary}), 500
    text = (r.stdout or "").strip() or (r.stderr or "")
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

# ---------------------------------------------------------------- health + runtime safety
def _health_payload():
    """Deterministic, secret-free, network-free local health snapshot (ACT-017)."""
    checks = {
        "runtime_policy": "PASS" if POLICY.is_valid else "FAIL",
        "loopback_binding": "PASS" if POLICY.loopback_only_effective else "FAIL",
        "local_engines": "PASS" if all(m in sys.modules for m in
                          ("keyword_source_adapter", "category_policy_registry",
                           "product_fact_loader", "page_auditor")) else "FAIL",
        "workspace_access": "PASS" if os.access(ROOT, os.R_OK) else "FAIL",
    }
    healthy = all(v == "PASS" for v in checks.values())
    last = DIAG.last_event()
    return healthy, {
        "ok": healthy,
        "service": SERVICE_NAME,
        "status": "healthy" if healthy else "unhealthy",
        "offline_only": POLICY.offline_only,
        "external_ai_enabled": POLICY.external_ai_enabled,
        "outbound_network_enabled": POLICY.outbound_network_enabled,
        "loopback_only": POLICY.loopback_only_effective,
        "bind_host": POLICY.bind_host,
        "bind_port": POLICY.bind_port,
        "version": tool_version(),
        "commit": git_commit(),
        "workspace": os.path.basename(ROOT),
        "uptime_seconds": int(max(0, time.time() - START_TIME)),
        "runtime_policy_sha256": POLICY.policy_sha256,
        # identity corroborators for a launcher (Session 5C) — never secrets
        "pid": os.getpid(),
        "instance_id": INSTANCE_ID,
        "debug": False,
        "last_diagnostic": (last.code if last else None),
        "checks": checks,
    }


@app.route("/healthz")
def healthz():
    healthy, payload = _health_payload()
    return jsonify(payload), (200 if healthy else 503)


@app.route("/api/runtime")
def api_runtime():
    """Compact runtime-safety panel data for the dashboard. Secret-free."""
    _, health = _health_payload()
    last = DIAG.last_event()
    return jsonify({
        "policy": POLICY.to_dict(),
        "health": {"ok": health["ok"], "status": health["status"], "checks": health["checks"]},
        "last_diagnostic": (last.to_dict() if last else None),
        "version": tool_version(),
    })


@app.route("/api/local")
def api_local():
    """Installation + instance status for the runtime panel (Session 5C / Part M).

    Secret-free and loopback-only. Because THIS process is the instance serving the
    request, instance state is derived from our own health snapshot — we never issue
    a re-entrant HTTP call to ourselves. Autostart/shortcut probes are bounded.
    """
    healthy, _health = _health_payload()
    manifest = LI.read_manifest()
    try:
        autostart = WI.autostart_status()
    except Exception:
        autostart = {"installed": False, "valid": False}
    try:
        shortcuts = WI.shortcuts_status()
    except Exception:
        shortcuts = {"installed": False}
    last = DIAG.last_event()
    return jsonify({
        "installed": manifest is not None,
        "install_version": (manifest or {}).get("toolkit_version"),
        "instance": {
            "status": IM.RUNNING_HEALTHY if healthy else IM.RUNNING_UNHEALTHY,
            "pid": os.getpid(),
            "instance_id": INSTANCE_ID,
            "startup_mode": STARTUP_MODE,
        },
        "autostart": {"installed": autostart.get("installed", False),
                      "valid": autostart.get("valid", False)},
        "shortcuts": {"installed": shortcuts.get("installed", False)},
        "last_diagnostic": (last.code if last else None),
    })


if __name__ == "__main__":
    os.makedirs(RUNS, exist_ok=True)
    # Fail fast on an unsafe/invalid runtime policy — never silently rewrite the
    # bind address to loopback (Session 5B / ACT-016, Part D).
    if not POLICY.is_valid:
        print("REFUSING TO START — invalid runtime policy:", file=sys.stderr)
        for err in POLICY.validation_errors:
            print("  - " + err, file=sys.stderr)
        sys.exit(2)
    for w in POLICY.warnings:
        print("[runtime-policy] " + w, file=sys.stderr)
    print(f"AMZ FBM cockpit -> http://{POLICY.bind_host}:{POLICY.bind_port}   "
          f"(offline-only={POLICY.offline_only}; external-AI={POLICY.external_ai_enabled}; "
          f"never touches Seller Central)")
    app.run(host=POLICY.bind_host, port=POLICY.bind_port, debug=False)
