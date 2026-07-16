#!/usr/bin/env python3
"""
listing.copy_package — build the owner-facing publishable package from an AUDITED listing (Session 3.1).

Two artifacts:
  LISTING-COPY-READY.txt              the copy the owner may paste, then a clearly separated
                                      "NOT READY TO PUBLISH" section for everything held back
  SELLER-CENTRAL-MANUAL-ENTRY-PLAN.json  a structured paste plan with `safe_to_paste` and `do_not_paste`

The exclusion rules are the whole point:
  * only PUBLISHABLE bullet jobs (with text) reach the paste-ready copy; BLOCKED_INCOMPLETE bullets are
    listed under "not ready" with the owner facts they still need;
  * legacy A+ is BLOCKED_LEGACY_UNVERIFIED — its state is reported but its copy is NEVER emitted into the
    paste-ready section, and the manual-entry plan explicitly instructs the owner NOT to paste it;
  * item_highlights is DRAFT_ONLY / owner-review — excluded from the paste-ready copy;
  * the description is included only when its state is publishable.

Nothing here re-audits — it consumes the PageAuditor verdict already on the listing (`publishability`) and
the per-section publishability the engines recorded, so a SAFE_DRAFT never ships a complete page.

Public API:
  build_copy_ready_text(listing) -> str
  build_manual_entry_plan(listing) -> dict
  write_publishable_package(folder, listing, generated_at=None) -> {path: ...}
"""
from __future__ import annotations

import json
import os

APLUS_BLOCKED_LEGACY_UNVERIFIED = "BLOCKED_LEGACY_UNVERIFIED"
APLUS_EVIDENCE_REBUILD_REQUIRED = "APLUS_EVIDENCE_REBUILD_REQUIRED"


def _publishable_bullets(listing):
    """(publishable, blocked) bullet lists from bullet_objects; falls back to plain string bullets."""
    objs = listing.get("bullet_objects")
    if isinstance(objs, list) and objs:
        pub = [b for b in objs if b.get("publishability") == "PUBLISHABLE" and (b.get("text") or "").strip()]
        blocked = [b for b in objs if b.get("publishability") != "PUBLISHABLE"]
        return pub, blocked
    bullets = listing.get("bullets") or listing.get("bullet_points") or []
    pub = [{"text": b, "job": None} for b in bullets if str(b).strip()]
    return pub, []


def _description_publishable(listing):
    dm = listing.get("description_meta") or {}
    state = dm.get("publishability")
    desc = listing.get("description") or ""
    if state == "BLOCKED_INCOMPLETE":
        return None
    return desc.strip() or None


def build_copy_ready_text(listing):
    """The paste-ready copy plus a separated 'not ready' section. Blocked A+ copy is never included."""
    title = listing.get("title") or ""
    mobile = (listing.get("mobile_preview") or {}).get("text") or title
    pub_bullets, blocked_bullets = _publishable_bullets(listing)
    description = _description_publishable(listing)
    backend = listing.get("backend") or ""
    publishability = listing.get("publishability") or (listing.get("audit") or {}).get("publishability")

    L = [f"PUBLISHABILITY: {publishability or 'UNKNOWN'}", "",
         f"TITLE ({len(title)} chars)", title, "",
         "MOBILE PREVIEW", mobile, "",
         "BULLETS (publishable only)"]
    if pub_bullets:
        L += [f"* {b['text']}" for b in pub_bullets]
    else:
        L += ["(none publishable yet)"]
    L += ["", "DESCRIPTION"]
    L += [description if description else "(held back — no verified section)"]
    L += ["", f"BACKEND SEARCH TERMS ({len(backend.encode('utf-8'))} bytes)", backend, ""]

    # --- everything held back from publication -------------------------------------------------
    L += ["--- NOT READY TO PUBLISH ---"]
    if blocked_bullets:
        jobs = ", ".join(b.get("job") or f"bullet {b.get('bullet_number')}" for b in blocked_bullets)
        L += [f"Blocked bullet jobs (missing verified facts): {jobs}"]
    # A+ — report the STATE only; never emit the legacy draft copy here.
    aplus_state = listing.get("aplus_state")
    if aplus_state == APLUS_BLOCKED_LEGACY_UNVERIFIED or (listing.get("aplus_draft") or {}):
        L += [f"A+ CONTENT: {APLUS_BLOCKED_LEGACY_UNVERIFIED} — legacy templates are draft-only and NOT "
              f"evidence-clean. Do NOT paste A+ ({APLUS_EVIDENCE_REBUILD_REQUIRED})."]
    # item highlights — draft-only, owner review.
    ih = listing.get("item_highlights") or ""
    if ih:
        L += [f"ITEM HIGHLIGHTS ({listing.get('item_highlights_state', 'DRAFT_ONLY')} — owner review "
              f"before use, excluded from paste-ready copy): {ih}"]
    ofr = listing.get("owner_fact_required") or []
    if ofr:
        L += [f"OWNER_FACT_REQUIRED: {', '.join(ofr)}"]
    mr = listing.get("missing_requirements") or []
    if mr:
        L += [f"MISSING REQUIREMENTS: {', '.join(mr)}"]
    return "\n".join(L) + "\n"


def build_manual_entry_plan(listing):
    """A Seller Central manual-entry plan: what is safe to paste, and what must NOT be pasted."""
    title = listing.get("title") or ""
    pub_bullets, blocked_bullets = _publishable_bullets(listing)
    description = _description_publishable(listing)
    backend = listing.get("backend") or ""
    publishability = listing.get("publishability") or (listing.get("audit") or {}).get("publishability")

    plan = {
        "publishability": publishability,
        "safe_to_paste": {
            "title": title,
            "bullets": [b["text"] for b in pub_bullets],
            "description": description,
            "backend_search_terms": backend,
        },
        "do_not_paste": {
            "aplus": {
                "state": listing.get("aplus_state") or APLUS_BLOCKED_LEGACY_UNVERIFIED,
                "instruction": "Do NOT paste any A+ content. Legacy A+ is unverified and quarantined; "
                               "wait for the evidence-aware rebuild.",
                "missing_requirement": APLUS_EVIDENCE_REBUILD_REQUIRED,
            },
            "item_highlights": {
                "state": listing.get("item_highlights_state", "DRAFT_ONLY_OWNER_REVIEW_REQUIRED"),
                "value": listing.get("item_highlights") or "",
                "instruction": "Owner review required before use; excluded from the publishable payload.",
            },
            "blocked_bullets": [{"job": b.get("job"), "bullet_number": b.get("bullet_number"),
                                 "missing_requirements": b.get("missing_requirements", [])}
                                for b in blocked_bullets],
        },
        "owner_fact_required": listing.get("owner_fact_required") or [],
        "missing_requirements": listing.get("missing_requirements") or [],
    }
    if publishability == "PUBLISHABLE":
        plan["instructions"] = [
            "Paste the title, five bullets, description and backend search terms into Seller Central.",
            "Do NOT paste any A+ content — A+ is quarantined until the evidence-aware rebuild.",
        ]
    else:
        plan["instructions"] = [
            "This is a SAFE DRAFT, not a complete listing — do NOT publish it as-is.",
            "Paste only the fields under safe_to_paste.",
            "Supply the OWNER_FACT_REQUIRED facts, regenerate, and re-audit before publishing.",
            "Do NOT paste any A+ content or item highlights.",
        ]
    return plan


def write_publishable_package(folder, listing, generated_at=None):
    """Write LISTING-COPY-READY.txt and SELLER-CENTRAL-MANUAL-ENTRY-PLAN.json for a listing."""
    txt_path = os.path.join(folder, "LISTING-COPY-READY.txt")
    plan_path = os.path.join(folder, "SELLER-CENTRAL-MANUAL-ENTRY-PLAN.json")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(build_copy_ready_text(listing))
    plan = build_manual_entry_plan(listing)
    if generated_at:
        plan = {"generated_at": generated_at, **plan}
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)
    return {"copy_ready": txt_path, "manual_entry_plan": plan_path}
