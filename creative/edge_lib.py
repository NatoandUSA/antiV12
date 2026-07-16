#!/usr/bin/env python3
"""
creative.edge_lib — shared helpers for the Conversion & Creative Edge module:
HTML rendering (green theme), status badges, scoring, safe file writes.
No computer vision — works on structured human observations + product facts.
"""
from __future__ import annotations
import os, json, html

# ---- classification vocab (v2.2 Amazon-aware) ----
OVERUSED, EXPECTED, EFFECTIVE, WEAK, MISSING, OPPORTUNITY, UNSAFE = (
    "OVERUSED_STYLE","TABLE_STAKES","EFFECTIVE_PATTERN","WEAK","MISSING","DIFFERENTIATION_OPPORTUNITY","UNSAFE_TO_COPY")
AMAZON_BASELINE, CATEGORY_REQUIREMENT, INSUFFICIENT_EVIDENCE = "AMAZON_BASELINE","CATEGORY_REQUIREMENT","INSUFFICIENT_EVIDENCE"
# embroidery proof (v2.2 adds NOT_APPLICABLE + DRAFT_UNVERIFIED)
NOT_APPLICABLE, DRAFT_UNVERIFIED, PARTIALLY_PROVEN, PROVEN, MISLEADING = (
    "NOT_APPLICABLE","DRAFT_UNVERIFIED","PARTIALLY_PROVEN","PROVEN","MISLEADING")
UNPROVEN = "UNPROVEN"
CONSISTENT, MINOR_REVIEW, CONVERSION_RISK, BLOCKED = "CONSISTENT","MINOR_REVIEW","CONVERSION_RISK","BLOCKED"
CONSISTENCY_INCOMPLETE, CONSISTENCY_ONE_SPEC = "INCOMPLETE","INCOMPLETE_FOR_CROSS_IMAGE_CONSISTENCY"
PLAN_CONSISTENT = "PLAN_CONSISTENT"   # specs agree but are not asset-backed/reviewed → not actual consistency
# main-image compliance
MI_INCOMPLETE, MI_READY_REVIEW, MI_COMPLIANT_DRAFT, MI_REVISE, MI_BLOCKED = (
    "INCOMPLETE","READY_FOR_MANUAL_CATEGORY_REVIEW","COMPLIANT_DRAFT","REVISE","BLOCKED")
INCOMPLETE = "INCOMPLETE"

_BADGE = {
    OVERUSED:("#8a6d00","#fff8e6"), EXPECTED:("#39463f","#eef2f0"), EFFECTIVE:("#1f7a4d","#eaf7ef"),
    WEAK:("#b4531a","#fff4e2"), MISSING:("#1451a8","#e6f0ff"), OPPORTUNITY:("#1f7a4d","#eaf7ef"),
    UNSAFE:("#c0392b","#fdECEC"), AMAZON_BASELINE:("#1451a8","#e6f0ff"),
    CATEGORY_REQUIREMENT:("#1451a8","#e6f0ff"), INSUFFICIENT_EVIDENCE:("#8a6d00","#fff8e6"),
    NOT_APPLICABLE:("#39463f","#eef2f0"), DRAFT_UNVERIFIED:("#8a6d00","#fff8e6"),
    PROVEN:("#1f7a4d","#eaf7ef"), PARTIALLY_PROVEN:("#b4531a","#fff4e2"),
    UNPROVEN:("#8a3d3d","#fdECEC"), MISLEADING:("#c0392b","#fdECEC"),
    CONSISTENT:("#1f7a4d","#eaf7ef"), MINOR_REVIEW:("#b4531a","#fff4e2"),
    CONVERSION_RISK:("#b4531a","#fff4e2"), BLOCKED:("#c0392b","#fdECEC"),
    MI_READY_REVIEW:("#1451a8","#e6f0ff"), MI_COMPLIANT_DRAFT:("#1f7a4d","#eaf7ef"), MI_REVISE:("#b4531a","#fff4e2"),
    "GO":("#1f7a4d","#eaf7ef"),"TEST":("#b4531a","#fff4e2"),"HIGH":("#c0392b","#fdECEC"),
    "LOW":("#c0392b","#fdECEC"),"MEDIUM":("#b4531a","#fff4e2"),
    "INCOMPLETE":("#8a6d00","#fff8e6"),
}

def badge(status: str) -> str:
    fg, bg = _BADGE.get(status, ("#39463f","#eef2f0"))
    return f'<span style="background:{bg};color:{fg};border-radius:20px;padding:2px 10px;font-size:12px;font-weight:700">{esc(status)}</span>'

def esc(s) -> str:
    return html.escape(str(s), quote=True)

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#eef1f5;color:#1c2431;font-family:-apple-system,"Segoe UI",Roboto,Arial,sans-serif;line-height:1.55;font-size:14.5px}
.wrap{max-width:1000px;margin:0 auto;padding:22px 18px 60px}
.hero{background:linear-gradient(135deg,#12332a,#1f5a45);color:#fff;border-radius:16px;padding:24px 28px;margin-bottom:16px}
.hero .kick{font-size:12px;letter-spacing:.2em;text-transform:uppercase;color:#8fe0b4;font-weight:700}
.hero h1{font-size:23px;font-weight:800;margin:6px 0 6px}
.hero p{opacity:.92;font-size:14px}
h2{font-size:13px;letter-spacing:.05em;text-transform:uppercase;color:#1f5a45;margin:24px 0 10px;font-weight:800}
.card{background:#fff;border:1px solid #dde4ea;border-radius:14px;padding:16px 18px;margin-bottom:12px;box-shadow:0 1px 2px rgba(20,30,50,.04)}
table{width:100%;border-collapse:collapse;font-size:13px;margin:4px 0}
th,td{border:1px solid #e2e8ee;padding:7px 9px;text-align:left;vertical-align:top}
th{background:#eaf4ef;color:#12332a;font-weight:700}
tr:nth-child(even) td{background:#f7faf8}
.big{font-size:30px;font-weight:800;color:#1f5a45}
.muted{color:#5a6570;font-size:12.5px}
.thumb{background:#fff;border:1px solid #dde4ea;display:inline-flex;align-items:center;justify-content:center;text-align:center;color:#8a97a3;overflow:hidden;vertical-align:top;margin:6px}
.thumb img{width:100%;height:100%;object-fit:cover}
.why{background:#fff8e6;border-left:5px solid #e0a800;border-radius:10px;padding:10px 14px;margin:10px 0;font-size:13px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:720px){.grid{grid-template-columns:1fr}}
.concept{background:#fff;border:1px solid #dde4ea;border-radius:12px;padding:14px 16px}
.concept h3{font-size:15px;color:#12332a;margin-bottom:4px}
code{background:#0f2a22;color:#8fe0b4;border-radius:5px;padding:1px 6px;font-size:12px}
"""

def page(title: str, kicker: str, subtitle: str, body: str) -> str:
    return (f"<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{esc(title)}</title><style>{CSS}</style></head><body><div class='wrap'>"
            f"<div class='hero'><div class='kick'>{esc(kicker)}</div><h1>{esc(title)}</h1>"
            f"<p>{esc(subtitle)}</p></div>{body}</div></body></html>")

def save(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def save_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
