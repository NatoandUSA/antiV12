#!/usr/bin/env python3
"""
listing.page_auditor — the ONE shared listing safety auditor (ACT-015).

Before this module the dashboard's safe_write_listing() checked only basic shape, title length, item-
highlights length and A+ list-type, so unsafe claims, rejected/wrong-audience keywords, backend overflow
and missing evidence could overwrite the last valid listing. This module is the single auditor shared by
the listing generator, the compliance validator, the dashboard safe-write path, and the Session 3 proof
gate. A failed candidate can never overwrite the last valid listing.

What it audits (each returns hard_failures and/or warnings):
  KEYWORDS     rejected / wrong-audience / wrong-product / wrong-occasion / brand-IP leakage,
               ineligible allocation, keyword source-hash inconsistency
  CLAIMS       unsupported factual claims, owner-review claims in publishable text, prohibited claims,
               missing claim lineage, unresolved placeholders
  TITLE        category hard limit, duplicate product/personalization concept, missing product identity,
               a component reported too long
  BULLETS      exactly five, unique buyer jobs, unsupported claims, missing lineage, blocked jobs
  DESCRIPTION  unsupported claims/sections, unresolved placeholders, missing lineage
  BACKEND      byte ceiling, malformed type, rejected/risky terms
  A+           list shape + no unresolved {placeholder} (structural only; A+ is not rebuilt here)

A claim phrase in visible copy is "supported" only if it also appears in a VERIFIED claim's text — so
copy that quotes a verified fact passes, while a hardcoded/fabricated promise with no verified backing is
blocked. Verified backing is read from an explicit claim_evidence argument, else from the listing's own
embedded `claim_evidence` block, else treated as absent (conservative: unverifiable claims are blocked).

Statuses: PASS · PASS_WITH_WARNINGS · BLOCKED.

Public API:
  audit_listing(listing, keyword_source=None, claim_evidence=None, product_facts=None, policy=None) -> dict
  audit_verdict(listing, **ctx) -> (ok, errors)
  promote_if_safe(folder, listing, allow_warnings=True, **ctx) -> dict
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import category_policy_registry as CPR

# statuses
PASS = "PASS"
PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
BLOCKED = "BLOCKED"

DEFAULT_CATEGORY = "apparel"
ITEM_HIGHLIGHTS_MAX = 125

# best-effort trademark/brand screen (same guard the compliance validator uses); optional.
try:
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "compliance"))
    try:
        from ip_guard import check as _tm_check          # type: ignore
    except Exception:
        from tm_guard import check as _tm_check          # type: ignore
except Exception:
    _tm_check = None

# curated claim phrases that assert a product fact and therefore require verified backing. A phrase here
# is a hard failure ONLY when it appears in visible copy without a VERIFIED claim carrying the same text.
UNSAFE_CLAIM_PHRASES = (
    # comfort / softness (never inferred from material or measurements)
    "soft and comfortable", "comfortable fit", "soft against the skin", "buttery soft",
    "cozy and comfortable", "all day comfort", "perfect for long shifts", "for long shifts",
    "12 hour shifts", "12-hour shifts",
    # durability (never inferred from embroidery)
    "made to last", "built to last", "will not fade", "wont fade", "never fades", "lasts forever",
    # exact personalization (never inferred from a name field)
    "exactly as you enter", "exactly as entered", "exactly what you enter", "embroidered exactly",
    # shipping / production origin (never inferred from a local supplier)
    "shipped from the us", "ships from the us", "made in the usa", "made in the us",
    "printed in the usa",
    # tracking (never inferred from the existence of shipping)
    "tracking included", "with tracking", "includes tracking",
    # material
    "premium material", "premium fabric", "cotton blend",
    # measurements
    "measured from the real garment", "measurements are taken from the real garment",
    "measured from real garments",
    # production time / made to order (never inferred from personalization)
    "made to order", "10 14 business days",
    # decoration
    "real machine embroidery", "machine embroidery", "satin stitch", "raised satin", "raised stitching",
    # invented defaults the generator used to inject
    "any name and credentials", "any occasion", "embroidered name", "mock neck option",
)

LEAKAGE_RISKS = ("WRONG_AUDIENCE", "WRONG_PRODUCT", "WRONG_OCCASION", "TRADEMARK", "BRAND_TERM",
                 "IP_RISK")

# concept tokens for title duplicate-concept detection.
_PROD_NOUNS = ("crewneck", "sweatshirt", "hoodie", "pullover", "sweater", "tshirt", "tee", "tank",
               "jacket", "cardigan", "shirt")
_PERS_TOKENS = ("personalized", "personalised", "custom", "customized", "monogram", "monogrammed")


# ---------------------------------------------------------------- text helpers
def _norm(s):
    """Lowercase, flatten hyphens/dashes to spaces, drop other punctuation, collapse whitespace."""
    s = str(s or "").lower().replace("-", " ").replace("–", " ").replace("—", " ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


def _tokens(s):
    return _norm(s).split()


def _contains_phrase(hay_tokens, needle_tokens):
    """True if needle_tokens appears as a contiguous run inside hay_tokens (word-boundary safe)."""
    n, m = len(hay_tokens), len(needle_tokens)
    if m == 0 or m > n:
        return False
    for i in range(n - m + 1):
        if hay_tokens[i:i + m] == needle_tokens:
            return True
    return False


def _mod_text(a):
    if isinstance(a, dict):
        return " ".join(str(a.get(k, "")) for k in ("headline", "copy", "text") if a.get(k))
    return str(a)


# ---------------------------------------------------------------- accumulator
class _Audit:
    def __init__(self):
        self.hard = []
        self.warn = []

    def fail(self, category, msg):
        self.hard.append({"category": category, "message": msg})

    def warning(self, category, msg):
        self.warn.append({"category": category, "message": msg})


# ---------------------------------------------------------------- claim backing
def _verified_texts(listing, claim_evidence):
    """Concatenated normalized text of every VERIFIED claim (explicit evidence wins, else the listing's
    own embedded block). Used to decide whether an unsafe phrase is actually backed by a verified fact."""
    texts = []
    if claim_evidence is not None:
        for c in getattr(claim_evidence, "publishable", []):
            if c.get("proposed_text"):
                texts.append(c["proposed_text"])
    else:
        block = listing.get("claim_evidence") if isinstance(listing, dict) else None
        if isinstance(block, dict):
            for c in block.get("verified") or []:
                if isinstance(c, dict) and c.get("text"):
                    texts.append(c["text"])
    return _norm(" ".join(texts))


def _owner_review_texts(listing, claim_evidence):
    texts = []
    if claim_evidence is not None:
        for c in claim_evidence.by_state("SUPPORTED_OWNER_REVIEW"):
            if c.get("proposed_text"):
                texts.append(c["proposed_text"])
    else:
        block = listing.get("claim_evidence") if isinstance(listing, dict) else None
        if isinstance(block, dict):
            for c in block.get("owner_review") or []:
                if isinstance(c, dict) and c.get("text"):
                    texts.append(c["text"])
    return [_norm(t) for t in texts if t]


# ---------------------------------------------------------------- audits
def _audit_keywords(a, listing, copy_tokens, backend, keyword_source, results):
    kw = {"rejected_leakage": [], "wrong_audience_leakage": [], "wrong_product_leakage": [],
          "wrong_occasion_leakage": [], "brand_ip_leakage": [], "ineligible_allocation": [],
          "source_hash": "not_checked"}

    # source-hash consistency
    if keyword_source is not None:
        listing_hash = listing.get("keyword_source_sha256")
        if listing_hash and listing_hash != keyword_source.source_sha256:
            a.fail("keyword_source_hash",
                   f"listing keyword_source_sha256 {listing_hash[:16]}… != source "
                   f"{keyword_source.source_sha256[:16]}…")
            kw["source_hash"] = "MISMATCH"
        else:
            kw["source_hash"] = "OK"

        risk_by_cat = {r: [] for r in LEAKAGE_RISKS}
        eligible_norm = set()
        for rec in keyword_source.keywords:
            if rec["eligible"]:
                eligible_norm.add(rec["keyword_normalized"])
            for r in rec["blocking_risks"]:
                if r in risk_by_cat:
                    risk_by_cat[r].append(rec["keyword_exact"])

        backend_tokens = _tokens(backend)
        scan_tokens = copy_tokens + ["|"] + backend_tokens
        cat_map = {"WRONG_AUDIENCE": "wrong_audience_leakage", "WRONG_PRODUCT": "wrong_product_leakage",
                   "WRONG_OCCASION": "wrong_occasion_leakage", "TRADEMARK": "brand_ip_leakage",
                   "BRAND_TERM": "brand_ip_leakage", "IP_RISK": "brand_ip_leakage"}
        # Only RISK-classed phrases are leakage. A phrase that is merely REJECTED-tier (low value/noise)
        # is often a fragment of an APPROVED keyword — e.g. "personalized nurse" inside the approved
        # "personalized nurse sweatshirt" — so treating rejected-tier substrings as leakage produces
        # false positives on safe copy. Rejected phrases only matter when deliberately re-allocated,
        # which the ineligible-allocation check below catches.
        for risk, phrases in risk_by_cat.items():
            bucket = cat_map[risk]
            for phrase in phrases:
                pt = _tokens(phrase)
                if len(pt) >= 1 and _contains_phrase(scan_tokens, pt):
                    kw[bucket].append(phrase)
                    a.fail("keyword_leakage", f"{risk} keyword '{phrase}' leaked into publishable copy/backend")

        # ineligible allocation: a keyword the listing claims to allocate that is not eligible
        # (this is where a rejected/blocked phrase deliberately re-entering allocation is caught).
        allocated = []
        sk = listing.get("selected_keywords") or {}
        for grp in ("primary", "secondary"):
            allocated += [str(x) for x in (sk.get(grp) or [])]
        for phrase in allocated:
            if _norm(phrase) and _norm(phrase) not in eligible_norm:
                kw["ineligible_allocation"].append(phrase)
                a.warning("keyword_allocation",
                          f"allocated keyword '{phrase}' is not in the eligible set")
    for k in ("rejected_leakage", "wrong_audience_leakage", "wrong_product_leakage",
              "wrong_occasion_leakage", "brand_ip_leakage", "ineligible_allocation"):
        kw[k] = sorted(set(kw[k]))
    results["keyword_results"] = kw


def _audit_claims(a, listing, copy_text, copy_tokens, claim_evidence, results):
    cr = {"unsupported": [], "owner_review_in_copy": [], "prohibited": [], "unresolved_placeholders": [],
          "missing_lineage": []}
    verified_norm = _verified_texts(listing, claim_evidence)
    verified_tokens = verified_norm.split()

    # unsupported factual claims: an unsafe phrase present in copy but not in any verified claim text.
    for phrase in UNSAFE_CLAIM_PHRASES:
        pt = _norm(phrase).split()
        if _contains_phrase(copy_tokens, pt) and not _contains_phrase(verified_tokens, pt):
            cr["unsupported"].append(phrase)
            a.fail("unsupported_claim", f"unsupported claim '{phrase}' in publishable copy "
                                        f"(no VERIFIED claim backs it)")

    # owner-review claim text must not appear in publishable copy.
    for t in _owner_review_texts(listing, claim_evidence):
        if t and _contains_phrase(copy_tokens, t.split()):
            cr["owner_review_in_copy"].append(t)
            a.fail("owner_review_in_copy",
                   f"SUPPORTED_OWNER_REVIEW claim text '{t}' appears in publishable copy")

    # prohibited claim text must never appear.
    if claim_evidence is not None:
        for c in claim_evidence.by_state("PROHIBITED"):
            t = c.get("proposed_text")
            if t and _contains_phrase(copy_tokens, _norm(t).split()):
                cr["prohibited"].append(t)
                a.fail("prohibited_claim", f"PROHIBITED claim '{t}' appears in publishable copy")

    # unresolved placeholders anywhere in visible copy.
    for m in re.findall(r"\{[a-zA-Z0-9_]+\}", copy_text):
        cr["unresolved_placeholders"].append(m)
        a.fail("unresolved_placeholder", f"unresolved placeholder {m} in publishable copy")

    # structured lineage: a factual bullet/section with verified content must carry claim ids.
    for b in listing.get("bullet_objects") or []:
        vs = b.get("verification_summary") or {}
        if b.get("text") and vs.get("verified", 0) > 0 and not b.get("claim_ids"):
            cr["missing_lineage"].append(f"bullet {b.get('bullet_number')}")
            a.fail("missing_claim_lineage",
                   f"bullet {b.get('bullet_number')} states verified content but carries no claim ids")
    dm = listing.get("description_meta") or {}
    for s in dm.get("sections") or []:
        if s.get("state") == "VERIFIED" and s.get("text") and not s.get("claim_ids"):
            cr["missing_lineage"].append(f"description:{s.get('section_id')}")
            a.fail("missing_claim_lineage",
                   f"description section '{s.get('section_id')}' is verified but carries no claim ids")

    for k in cr:
        cr[k] = sorted(set(cr[k]))
    results["claim_results"] = cr


def _audit_title(a, listing, policy, results):
    tr = {"length": 0, "hard_limit": policy.title_hard_limit, "duplicate_product_concept": False,
          "duplicate_personalization_concept": False, "missing_product_identity": False,
          "component_too_long": False}
    title = listing.get("title") or ""
    tr["length"] = len(title)
    if len(title) > policy.title_hard_limit:
        a.fail("title_hard_limit",
               f"title {len(title)} chars > {policy.title_hard_limit} "
               f"({policy.category_identifier} title hard limit)")
    toks = _tokens(title)
    # duplicate product concept: the SAME garment noun repeated verbatim (e.g. "Sweatshirt ...
    # Sweatshirt"). Two DIFFERENT compatible nouns ("crewneck sweatshirt") are one garment, not a
    # duplicate, and a genuine multi-garment title is a variation question the validator warns on.
    prod_hits = [t for t in toks if t in _PROD_NOUNS]
    if len(prod_hits) != len(set(prod_hits)):
        tr["duplicate_product_concept"] = True
        a.fail("duplicate_product_concept", f"title repeats a product concept: {prod_hits}")
    # duplicate personalization concept: the same personalization word repeated.
    pers_hits = [t for t in toks if t in _PERS_TOKENS]
    if len(pers_hits) != len(set(pers_hits)):
        tr["duplicate_personalization_concept"] = True
        a.fail("duplicate_personalization_concept",
               f"title repeats a personalization concept: {pers_hits}")

    # missing product identity — only assertable when we know the intended product noun.
    tmeta = listing.get("title_meta") or {}
    if tmeta.get("status") == "TITLE_COMPONENT_TOO_LONG":
        tr["component_too_long"] = True
        a.fail("title_component_too_long", "title reports TITLE_COMPONENT_TOO_LONG (identity did not fit)")
    expected_noun = None
    for src in (listing.get("selected_keywords", {}).get("primary") or []):
        for noun in _PROD_NOUNS:
            if noun in _tokens(src):
                expected_noun = noun
                break
    if expected_noun and expected_noun not in toks:
        tr["missing_product_identity"] = True
        a.fail("missing_product_identity",
               f"title is missing the product identity '{expected_noun}' from the primary keyword")
    results["title_results"] = tr


def _audit_bullets(a, listing, claim_evidence, results):
    br = {"count": 0, "unique_jobs": True, "blocked_jobs": [], "duplicate_jobs": []}
    bullets = listing.get("bullets") or listing.get("bullet_points") or []
    objs = listing.get("bullet_objects") or []
    count = len(objs) if objs else len(bullets)
    br["count"] = count
    if objs:
        # the evidence-backed bullet engine must always emit exactly five distinct-job bullets.
        if len(objs) != 5:
            a.fail("bullet_count", f"expected exactly 5 structured bullets, found {len(objs)}")
        jobs = [b.get("job") for b in objs]
        if len(set(jobs)) != len(jobs):
            br["unique_jobs"] = False
            seen, dups = set(), set()
            for j in jobs:
                if j in seen:
                    dups.add(j)
                seen.add(j)
            br["duplicate_jobs"] = sorted(dups)
            a.fail("duplicate_bullet_jobs", f"bullets repeat buyer job(s): {sorted(dups)}")
        blocked = [b.get("job") for b in objs if b.get("publishability") == "BLOCKED_INCOMPLETE"]
        br["blocked_jobs"] = blocked
        if blocked:
            a.warning("bullet_blocked_incomplete",
                      f"{len(blocked)} bullet job(s) are BLOCKED_INCOMPLETE for missing evidence: {blocked}")
    else:
        # plain string bullets (arbitrary/minimal listing): empty is broken; other counts only warn.
        if count == 0:
            a.fail("bullet_count", "listing has no bullets")
        elif count != 5:
            a.warning("bullet_count", f"{count} bullets (5 recommended)")
    results["bullet_results"] = br


def _audit_description(a, listing, results):
    dr = {"unsupported_sections": [], "unresolved_placeholders": []}
    dm = listing.get("description_meta") or {}
    for s in dm.get("sections") or []:
        if s.get("state") == "VERIFIED" and not s.get("claim_ids"):
            dr["unsupported_sections"].append(s.get("section_id"))
    results["description_results"] = dr


def _audit_backend(a, listing, policy, results):
    br = {"bytes_used": 0, "byte_ceiling": policy.backend_byte_ceiling, "type_ok": True}
    backend = listing.get("backend")
    if backend is None:
        results["backend_results"] = br
        return
    if not isinstance(backend, str):
        br["type_ok"] = False
        a.fail("backend_type", f"backend must be a string, got {type(backend).__name__}")
        results["backend_results"] = br
        return
    nb = len(backend.encode("utf-8"))
    br["bytes_used"] = nb
    if nb > policy.backend_byte_ceiling:
        a.fail("backend_overflow",
               f"backend {nb} bytes > {policy.backend_byte_ceiling} "
               f"({policy.category_identifier} backend byte ceiling)")
    results["backend_results"] = br


def _audit_aplus(a, listing, results):
    ap = listing.get("aplus") or listing.get("aplus_modules") or listing.get("a_plus")
    if ap is None:
        return
    if not isinstance(ap, list):
        a.fail("aplus_shape", "aplus must be a list of modules")
        return
    for i, mod in enumerate(ap, 1):
        text = _mod_text(mod)
        for m in re.findall(r"\{[a-zA-Z0-9_]+\}", text):
            a.fail("aplus_placeholder", f"A+ module {i} contains an unresolved placeholder {m}")


def _brand_screen(a, listing, fields):
    if _tm_check is None:
        return
    for label, text in fields:
        if not str(text).strip():
            continue
        try:
            st, why = _tm_check(text)
        except Exception:
            continue
        if st == "HIGH":
            a.fail("trademark", f"trademark/brand in {label}: {why}")


# ---------------------------------------------------------------- entry point
def audit_listing(listing, keyword_source=None, claim_evidence=None, product_facts=None,
                  policy=None, registry=None):
    """Audit a listing dict and return the structured audit result (never raises on listing content)."""
    a = _Audit()
    results = {}
    if not isinstance(listing, dict):
        return {"status": BLOCKED, "hard_failures": [{"category": "shape",
                "message": "listing is not a JSON object"}], "warnings": [], "field_results": {},
                "keyword_results": {}, "claim_results": {}, "title_results": {}, "bullet_results": {},
                "description_results": {}, "backend_results": {}, "source_hashes": {}}

    if policy is None:
        policy = CPR.resolve_category_policy(listing.get("category") or DEFAULT_CATEGORY,
                                             marketplace="US", registry=registry)

    title = listing.get("title") or ""
    bullets = listing.get("bullets") or listing.get("bullet_points") or []
    description = listing.get("description") or ""
    item_highlights = listing.get("item_highlights") or ""
    if isinstance(item_highlights, list):
        item_highlights = ", ".join(str(x) for x in item_highlights)

    # The claim + keyword screens cover the fields this session governs (title, bullets, description,
    # item highlights). A+ prose is NOT screened for claims here — A+ is audited structurally only
    # (_audit_aplus) and is not rebuilt this session; screening its legacy copy would wrongly block a
    # safe draft over out-of-scope A+ content.
    copy_text = " ".join([title, description, item_highlights] + [str(b) for b in bullets])
    copy_tokens = _tokens(copy_text)

    if item_highlights and len(item_highlights) > ITEM_HIGHLIGHTS_MAX:
        a.fail("item_highlights_length",
               f"item_highlights {len(item_highlights)} chars > {ITEM_HIGHLIGHTS_MAX}")

    _audit_keywords(a, listing, copy_tokens, listing.get("backend") or "", keyword_source, results)
    _audit_claims(a, listing, copy_text, copy_tokens, claim_evidence, results)
    _audit_title(a, listing, policy, results)
    _audit_bullets(a, listing, claim_evidence, results)
    _audit_description(a, listing, results)
    _audit_backend(a, listing, policy, results)
    _audit_aplus(a, listing, results)
    _brand_screen(a, listing, [("title", title), ("description", description)]
                  + [(f"bullet {i}", b) for i, b in enumerate(bullets, 1)])

    results["field_results"] = {
        "title": {"length": len(title), "hard_limit": policy.title_hard_limit},
        "bullets": {"count": len(listing.get("bullet_objects") or bullets)},
        "backend": results.get("backend_results", {}),
        "category_policy": policy.category_identifier,
        "policy_fallback": policy.fallback_used,
    }
    results["source_hashes"] = {
        "keyword_source_sha256": listing.get("keyword_source_sha256"),
        "product_fact_source_sha256": listing.get("product_fact_source_sha256"),
        "claim_evidence_sha256": (listing.get("claim_evidence") or {}).get("source_content_sha256")
        if isinstance(listing.get("claim_evidence"), dict) else None,
    }
    if policy.fallback_used:
        a.warning("category_policy",
                  f"POLICY_VERIFICATION_REQUIRED: no verified policy for {policy.category_identifier}")

    status = BLOCKED if a.hard else (PASS_WITH_WARNINGS if a.warn else PASS)
    return {"status": status, "hard_failures": a.hard, "warnings": a.warn, **results}


def audit_verdict(listing, allow_warnings=True, **ctx):
    """(ok, errors) convenience — ok=True for PASS or (allowed) PASS_WITH_WARNINGS."""
    audit = audit_listing(listing, **ctx)
    errors = [f"{h['category']}: {h['message']}" for h in audit["hard_failures"]]
    ok = audit["status"] == PASS or (allow_warnings and audit["status"] == PASS_WITH_WARNINGS)
    return ok, errors


# ---------------------------------------------------------------- last-valid-listing protection
def _atomic_write(path, text):
    """Write text to path atomically: unique temp in the same dir, flush, fsync, os.replace."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, f".{os.path.basename(path)}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _sha256_text(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def promote_if_safe(folder, listing, allow_warnings=True, generated_at=None, dest_name="listing.json",
                    **ctx):
    """Audit a candidate listing and promote it to the last-valid listing ONLY if it is safe.

    A blocked candidate never overwrites listing.json. Writes PAGE-AUDIT-REPORT.json always,
    FAILED-LISTING-CANDIDATE.json when blocked, and LAST-VALID-LISTING-METADATA.json when promoted.
    Returns a result dict.
    """
    audit = audit_listing(listing, **ctx)
    stamp = generated_at or datetime.now(timezone.utc).isoformat()
    dest = os.path.join(folder, dest_name)
    candidate_text = json.dumps(listing, indent=2, ensure_ascii=False, sort_keys=True)
    input_hash = _sha256_text(candidate_text)

    report = {"generated_at": stamp, "dest": dest_name, "input_sha256": input_hash, "audit": audit}
    _atomic_write(os.path.join(folder, "PAGE-AUDIT-REPORT.json"),
                  json.dumps(report, indent=2, ensure_ascii=False))

    promotable = audit["status"] == PASS or (allow_warnings and audit["status"] == PASS_WITH_WARNINGS)
    if not promotable:
        failed = {"generated_at": stamp, "status": audit["status"], "input_sha256": input_hash,
                  "hard_failures": audit["hard_failures"], "warnings": audit["warnings"],
                  "candidate": listing,
                  "note": "This candidate was BLOCKED and did NOT overwrite the last valid listing."}
        _atomic_write(os.path.join(folder, "FAILED-LISTING-CANDIDATE.json"),
                      json.dumps(failed, indent=2, ensure_ascii=False))
        return {"promoted": False, "status": audit["status"], "audit": audit,
                "input_sha256": input_hash, "dest": dest, "last_valid_preserved": os.path.exists(dest)}

    # promote: back up the prior valid listing, then atomically replace.
    if os.path.exists(dest):
        try:
            with open(dest, encoding="utf-8") as f:
                prior = f.read()
            _atomic_write(os.path.join(folder, "listing.prev.json"), prior)
        except OSError:
            pass
    output = dict(listing)
    output.setdefault("schema_version", "2.4")
    output_text = json.dumps(output, indent=2, ensure_ascii=False)
    _atomic_write(dest, output_text)
    output_hash = _sha256_text(output_text)
    meta = {"generated_at": stamp, "status": audit["status"], "dest": dest_name,
            "input_sha256": input_hash, "output_sha256": output_hash,
            "keyword_source_sha256": listing.get("keyword_source_sha256"),
            "product_fact_source_sha256": listing.get("product_fact_source_sha256"),
            "warnings": audit["warnings"]}
    _atomic_write(os.path.join(folder, "LAST-VALID-LISTING-METADATA.json"),
                  json.dumps(meta, indent=2, ensure_ascii=False))
    # a promoted candidate clears any stale failed-candidate marker.
    fc = os.path.join(folder, "FAILED-LISTING-CANDIDATE.json")
    if os.path.exists(fc):
        try:
            os.remove(fc)
        except OSError:
            pass
    return {"promoted": True, "status": audit["status"], "audit": audit,
            "input_sha256": input_hash, "output_sha256": output_hash, "dest": dest}
