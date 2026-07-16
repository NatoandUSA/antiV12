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
APLUS_OWNER_FACTS_OR_ASSETS_REQUIRED = "APLUS_OWNER_FACTS_OR_ASSETS_REQUIRED"


def _aplus_publishable(listing):
    """The READY A+ modules that may be pasted — ONLY when the evidence-aware builder's gates all passed.

    Session 4: Basic A+ is included only when its five modules are READY (or the confirmed seven-module
    Premium). Until then the publishable `aplus` field is empty and nothing A+ is pasted. Returns
    (modules, capability, ready)."""
    ap = listing.get("aplus")
    state = listing.get("aplus_state") or ""
    ac = listing.get("aplus_content") if isinstance(listing.get("aplus_content"), dict) else {}
    capability = listing.get("aplus_capability") or ac.get("capability")
    ready = isinstance(ap, list) and bool(ap) and state.startswith("READY_")
    return (ap if ready else []), capability, ready


def _aplus_premium_draft_note(listing):
    """For UNKNOWN capability, the Premium draft is eligibility-unverified and must never be pasted."""
    ac = listing.get("aplus_content") if isinstance(listing.get("aplus_content"), dict) else {}
    prem = ac.get("premium") if isinstance(ac.get("premium"), dict) else {}
    if prem.get("eligibility_unverified"):
        return {"premium_status": prem.get("premium_status"),
                "instruction": "Premium A+ eligibility is UNVERIFIED — this is a DRAFT. Do NOT paste any "
                               "Premium module until Premium A+ access is confirmed.",
                "never_publishable": True}
    return None


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


def _backend_audit_summary(listing):
    """A compact backend inclusion/exclusion summary for the manual-entry plan (ACT-009)."""
    audit = listing.get("backend_audit") if isinstance(listing.get("backend_audit"), dict) else {}
    backend = listing.get("backend") or ""
    return {
        "backend_search_terms": backend,
        "bytes_used": audit.get("bytes_used", len(backend.encode("utf-8"))),
        "byte_ceiling": audit.get("byte_ceiling"),
        "bytes_remaining": audit.get("bytes_remaining"),
        "included_count": audit.get("included_count"),
        "excluded_count": audit.get("excluded_count"),
        "excluded_summary": audit.get("excluded_summary", {}),
        "risk_results": {k: v.get("count") if isinstance(v, dict) else None
                         for k, v in (audit.get("risk_results") or {}).items()},
    }


def _publishable_item_highlights(listing):
    """(publishable_highlights, supported, ready). Verified item highlights are pasted only when the category
    supports the field, at least one VERIFIED highlight exists, and the whole page is PUBLISHABLE — mirroring
    A+ and the description, so a SAFE_DRAFT or BLOCKED_UNSAFE page never pastes them. Returns [] when not
    ready. (The highlights are still individually verified and stay visible in the structured payload.)"""
    content = listing.get("item_highlights_content") if isinstance(listing.get("item_highlights_content"),
                                                                    dict) else {}
    supported = (listing.get("item_highlights_capability_state") == "SUPPORTED"
                 or content.get("category_support_state") == "SUPPORTED")
    pub = listing.get("item_highlights_publishable")
    pub = [h for h in pub if isinstance(h, dict) and (h.get("text") or "").strip()] \
        if isinstance(pub, list) else []
    publishability = listing.get("publishability") or (listing.get("audit") or {}).get("publishability")
    ready = supported and bool(pub) and publishability == "PUBLISHABLE"
    return (pub if ready else []), supported, ready


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

    # Item highlights are pasted ONLY when the category supports the field AND every included highlight is a
    # VERIFIED, traceable attribute (Session 5A). Blocked/unsupported highlights never reach this section.
    ih_pub, ih_supported, ih_ready = _publishable_item_highlights(listing)
    if ih_ready:
        L += ["ITEM HIGHLIGHTS (verified, paste-ready)"]
        L += [f"* {h.get('text')}" for h in ih_pub]
        L += [""]

    # A+ content is pasted ONLY when the evidence-aware builder's gates all passed (Session 4).
    aplus_modules, aplus_capability, aplus_ready = _aplus_publishable(listing)
    if aplus_ready:
        L += [f"A+ CONTENT ({aplus_capability}, paste-ready)"]
        for i, m in enumerate(aplus_modules, 1):
            head = m.get("headline") if isinstance(m, dict) else ""
            copy = m.get("copy") if isinstance(m, dict) else str(m)
            L += [f"[Module {i}] {head}", copy]
        L += [""]

    # --- everything held back from publication -------------------------------------------------
    L += ["--- NOT READY TO PUBLISH ---"]
    if blocked_bullets:
        jobs = ", ".join(b.get("job") or f"bullet {b.get('bullet_number')}" for b in blocked_bullets)
        L += [f"Blocked bullet jobs (missing verified facts): {jobs}"]
    # A+ — report the STATE only; never emit blocked/legacy draft copy here.
    if not aplus_ready:
        aplus_state = listing.get("aplus_state")
        ac = listing.get("aplus_content") if isinstance(listing.get("aplus_content"), dict) else {}
        ready_count = ac.get("basic_ready_count")
        detail = f" (Basic modules READY {ready_count}/5)" if ready_count is not None else ""
        L += [f"A+ CONTENT: {APLUS_OWNER_FACTS_OR_ASSETS_REQUIRED}{detail} — supply the missing owner "
              f"facts and REAL photos, regenerate, and re-audit before any A+ module can be pasted.",
              f"A+ CONTENT: {APLUS_BLOCKED_LEGACY_UNVERIFIED} — legacy templates remain draft-only and NOT "
              f"evidence-clean. Do NOT paste A+ ({APLUS_EVIDENCE_REBUILD_REQUIRED})."]
    # item highlights — capability/evidence state (Session 5A) + the legacy draft-only keyword string.
    content = listing.get("item_highlights_content") if isinstance(listing.get("item_highlights_content"),
                                                                   dict) else {}
    if content and not ih_ready:
        if not ih_supported:
            L += ["ITEM HIGHLIGHTS: UNSUPPORTED_BY_CATEGORY — this category does not support the "
                  "item_highlights field; nothing is published (see the manual-review report)."]
        else:
            blocked = content.get("blocked_count")
            L += [f"ITEM HIGHLIGHTS: no publishable highlights yet ({blocked} concept(s) held back for "
                  f"missing verified facts) — supply the owner facts and regenerate."]
    ih = listing.get("item_highlights") or ""
    if ih:
        L += [f"ITEM HIGHLIGHTS ({listing.get('item_highlights_state', 'DRAFT_ONLY')} — legacy keyword "
              f"draft, owner review before use, excluded from paste-ready copy): {ih}"]
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

    aplus_modules, aplus_capability, aplus_ready = _aplus_publishable(listing)
    ih_pub, ih_supported, ih_ready = _publishable_item_highlights(listing)
    ih_content = listing.get("item_highlights_content") if isinstance(listing.get("item_highlights_content"),
                                                                      dict) else {}

    plan = {
        "publishability": publishability,
        "safe_to_paste": {
            "title": title,
            "bullets": [b["text"] for b in pub_bullets],
            "description": description,
            "backend_search_terms": backend,
        },
        # ACT-009 — the backend byte count + inclusion/exclusion audit summary the owner reviews.
        "backend_audit": _backend_audit_summary(listing),
        # ACT-010 — the item-highlights capability + missing-facts + blocked-reason state.
        "item_highlights_state": {
            "capability_state": listing.get("item_highlights_capability_state")
            or ih_content.get("category_support_state"),
            "supported": ih_supported,
            "publishable_count": len(ih_pub),
            "blocked_count": ih_content.get("blocked_count"),
            "owner_fact_required": ih_content.get("owner_fact_required", []),
            "manual_review_report": ih_content.get("manual_review_report", []),
        },
        "do_not_paste": {
            "item_highlights_legacy_draft": {
                "state": listing.get("item_highlights_state", "DRAFT_ONLY_OWNER_REVIEW_REQUIRED"),
                "value": listing.get("item_highlights") or "",
                "instruction": "Legacy keyword draft — owner review required before use; excluded from the "
                               "publishable payload.",
            },
            "blocked_bullets": [{"job": b.get("job"), "bullet_number": b.get("bullet_number"),
                                 "missing_requirements": b.get("missing_requirements", [])}
                                for b in blocked_bullets],
        },
        "owner_fact_required": listing.get("owner_fact_required") or [],
        "missing_requirements": listing.get("missing_requirements") or [],
    }
    # publishable item highlights (verified, traceable) may be pasted; otherwise they are held back with the
    # capability/blocked state above and never enter safe_to_paste.
    if ih_ready:
        plan["safe_to_paste"]["item_highlights"] = [{"highlight_id": h.get("highlight_id"),
                                                      "concept": h.get("concept"), "text": h.get("text"),
                                                      "claim_ids": h.get("claim_ids", [])} for h in ih_pub]
    else:
        plan["do_not_paste"]["item_highlights"] = {
            "capability_state": listing.get("item_highlights_capability_state")
            or ih_content.get("category_support_state"),
            "instruction": ("This category does not support item_highlights — do NOT paste."
                            if not ih_supported else
                            "No verified item highlights yet — supply the owner facts and regenerate."),
        }
    # A+ inclusion: only READY A+ enters safe_to_paste. When A+ is not READY the plan explicitly forbids
    # pasting it — the manual-entry plan never instructs the owner to paste blocked or draft A+ content.
    premium_draft = _aplus_premium_draft_note(listing)
    if aplus_ready:
        plan["safe_to_paste"]["aplus"] = [{"headline": m.get("headline"), "copy": m.get("copy")}
                                          for m in aplus_modules if isinstance(m, dict)]
        plan["do_not_paste"]["aplus_capability"] = aplus_capability
        if premium_draft:
            plan["do_not_paste"]["aplus_premium_draft"] = premium_draft
    else:
        plan["do_not_paste"]["aplus"] = {
            "state": listing.get("aplus_state") or APLUS_BLOCKED_LEGACY_UNVERIFIED,
            "capability": aplus_capability,
            "instruction": "Do NOT paste any A+ content. The evidence-aware A+ is incomplete (owner facts "
                           "and/or real photos missing) and legacy A+ is quarantined.",
            "missing_requirement": APLUS_OWNER_FACTS_OR_ASSETS_REQUIRED,
        }
        if premium_draft:
            plan["do_not_paste"]["aplus_premium_draft"] = premium_draft
    if publishability == "PUBLISHABLE":
        plan["instructions"] = [
            "Paste the title, five bullets, description and backend search terms into Seller Central.",
            ("Paste the verified item highlights from safe_to_paste.item_highlights." if ih_ready
             else "Do NOT paste any item highlights — none are verified/supported yet."),
            ("Paste the READY A+ modules from safe_to_paste.aplus." if aplus_ready
             else "Do NOT paste any A+ content — the evidence-aware A+ is not READY yet."),
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
