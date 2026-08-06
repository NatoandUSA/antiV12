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
  A+           publishable `aplus` prose is claim/leak/placeholder screened like other copy; legacy A+ is
               quarantined (BLOCKED_LEGACY_UNVERIFIED) and its draft must not reach the publishable field
  COVERAGE     every generated text field is audited / draft-only / excluded — none may silently bypass

A claim phrase in visible copy is "supported" only if it also appears in a VERIFIED claim's text — so
copy that quotes a verified fact passes, while a hardcoded/fabricated promise with no verified backing is
blocked. Verified backing is read from an explicit claim_evidence argument, else from the listing's own
embedded `claim_evidence` block, else treated as absent (conservative: unverifiable claims are blocked).

Statuses (low-level verdict): PASS · PASS_WITH_WARNINGS · BLOCKED.
Publishability (Session 3.1): PUBLISHABLE · SAFE_DRAFT_OWNER_FACTS_REQUIRED · BLOCKED_UNSAFE. A safe draft
that is only incomplete (blocked bullet jobs / description) because owner facts are missing is NOT a
publishable listing — it can update the last-safe-draft artifact but never the last-publishable one.

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
import aplus_module_registry as AREG
import backend_optimizer as BO
import item_highlights_builder as IHB
import unsafe_claim_policy as UCP

# statuses (low-level audit verdict — kept stable for existing callers)
PASS = "PASS"
PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
BLOCKED = "BLOCKED"

# publishability classification (Session 3.1) — a safe-but-incomplete draft is NOT a publishable listing.
PUBLISHABLE = "PUBLISHABLE"
SAFE_DRAFT_OWNER_FACTS_REQUIRED = "SAFE_DRAFT_OWNER_FACTS_REQUIRED"
BLOCKED_UNSAFE = "BLOCKED_UNSAFE"

# A+ content states — legacy A+ is quarantined until the evidence-aware rebuild lands (Session 4).
APLUS_BLOCKED_LEGACY_UNVERIFIED = "BLOCKED_LEGACY_UNVERIFIED"
APLUS_EVIDENCE_REBUILD_REQUIRED = "APLUS_EVIDENCE_REBUILD_REQUIRED"

DEFAULT_CATEGORY = "apparel"
ITEM_HIGHLIGHTS_MAX = 125

# Every generated customer-facing text field the auditor is responsible for, and how it is handled:
#   audited    -> screened for unsafe claims / leakage / placeholders; may enter publishable copy
#   draft_only -> screened for safety but EXCLUDED from the publishable package (owner review first)
#   excluded   -> quarantined; never part of publishable copy (e.g. legacy A+ draft)
# No generated text field may silently bypass the auditor (Session 3.1 PATCH 3).
AUDITED_COPY_FIELDS = (
    ("title", "audited"),
    ("bullets", "audited"),
    ("bullet_points", "audited"),
    ("description", "audited"),
    ("backend", "audited"),
    ("aplus", "audited"),
    ("aplus_modules", "audited"),
    ("a_plus", "audited"),
    ("personalization", "audited"),
    ("personalization_instructions", "audited"),
    ("item_highlights", "draft_only"),
    ("product_highlights", "draft_only"),
    # Session 5A — the capability/evidence-aware item highlights. `item_highlights_publishable` is audited
    # like other publishable copy; `item_highlights_content` is the structured payload (validated by
    # _audit_item_highlights) and never reaches publishable copy directly.
    ("item_highlights_publishable", "audited"),
    ("item_highlights_content", "excluded"),
    ("aplus_draft", "excluded"),
    # Session 4 — the evidence-aware A+ structure is validated by _audit_aplus_evidence; its READY module
    # copy only reaches publishable copy through the already-audited `aplus` field.
    ("aplus_content", "excluded"),
)
_COPY_FIELD_DISPOSITION = dict(AUDITED_COPY_FIELDS)
# copy-ish field names that MUST be routed through the registry; present-but-unregistered is a hard fail.
_SUSPECT_COPY_FIELDS = ("tagline", "subtitle", "subtitles", "highlights", "catalog_description",
                        "catalog_copy", "a_plus_content", "aplus_copy", "marketing_copy", "promo_copy")

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
# ---------------------------------------------------------------- category-specific claim risk
#
# UNSAFE_CLAIM_PHRASES below is the shared vocabulary, and it grew up around embroidered apparel:
# comfort, fading, stitching, fit. Acrylic fails in completely different ways, and none of its
# failure language was listed anywhere -- measured on 2026-08-02, a draft reading
# "Shatterproof acrylic keychain, unbreakable and will not yellow, dishwasher safe" tripped ZERO
# unsafe-claim checks.
#
# These are not marketing adjectives. Each one is a DURABILITY OR SAFETY claim about a brittle
# plastic, and the owner sells acrylic today:
#
#   * shatterproof / unbreakable  -- acrylic cracks and shatters on hard impact. A safety claim
#     about a product that can produce sharp edges is the kind that gets a listing pulled.
#   * won't yellow / UV proof     -- acrylic yellows under prolonged UV. This is a lifetime claim
#     nobody can support from a supplier listing.
#   * scratch proof               -- acrylic scratches more easily than glass, not less.
#   * dishwasher / microwave safe -- a heat claim. Acrylic deforms well below dishwasher
#     temperatures, and this one has a plausible injury path.
#   * food safe / BPA free        -- a regulatory claim requiring documentation the owner does
#     not have from a print supplier.
#
# Every phrase remains publishable when a VERIFIED claim backs it -- that is the existing
# mechanism and it is unchanged. What this fixes is acrylic language passing UNCHECKED because
# nobody had written it down.
# The vocabulary AND every phrase's policy now live in listing.unsafe_claim_policy, the ONE
# authority. These names are re-exported so existing callers and tests keep working, and so the
# tree never carries a second copy of the list that can drift out of sync with the policy.
UNSAFE_CLAIM_PHRASES = UCP.UNSAFE_CLAIM_PHRASES
UNSAFE_CLAIM_PHRASES_BY_CATEGORY = UCP.UNSAFE_CLAIM_PHRASES_BY_CATEGORY
unsafe_claim_phrases_for = UCP.phrases_for

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
def _claim_evidence_for_policy(listing, claim_evidence):
    """The evidence the unsafe-claim policy should consult.

    Callers that re-audit a persisted listing (promote_if_safe / safe_write_listing) pass no
    ClaimEvidence object; the listing carries an embedded claim_evidence block instead. That block is
    read by its declared concepts, never by its text.
    """
    if claim_evidence is not None:
        return claim_evidence
    block = listing.get("claim_evidence") if isinstance(listing, dict) else None
    return block if isinstance(block, dict) else None


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


def _audit_claims(a, listing, copy_text, copy_tokens, claim_evidence, results, category=None):
    cr = {"unsupported": [], "owner_review_in_copy": [], "prohibited": [], "unresolved_placeholders": [],
          "missing_lineage": [], "policy_blocks": []}
    # Unsupported factual claims. Authorisation is decided PER PHRASE by the unsafe-claim policy
    # authority against STRUCTURED claim evidence -- never against concatenated claim text, which let
    # an owner authorise any phrase by typing it into a free-text fact and verifying it.
    evidence = _claim_evidence_for_policy(listing, claim_evidence)
    for blk in UCP.findings_for_text(copy_text, evidence, UCP.SURFACE_CLAIMS, category=category):
        cr["unsupported"].append(blk["phrase"])
        cr["policy_blocks"].append(blk)
        a.fail("unsupported_claim",
               f"unsupported claim '{blk['phrase']}' in publishable copy "
               f"[{blk['policy_class']} / {blk['reason_code']}] {blk['next_action']}")

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
        if k != "policy_blocks":            # structured blocks are dicts; keep them as emitted
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


def _allowed_stopword_positions(tokens):
    """Indices of stopword tokens that sit INTERIOR to an approved connector phrase (the 'and' of
    'labor and delivery'). Every other stopword is an orphan. Deterministic string scan."""
    allowed = set()
    for phrase in BO._CONNECTOR_PHRASES:
        pt = phrase.split()
        m = len(pt)
        for i in range(len(tokens) - m + 1):
            if tokens[i:i + m] == pt:
                for j, t in enumerate(pt):
                    if t in BO._STOPWORDS:
                        allowed.add(i + j)
    return allowed


def _backend_semantic_findings(backend):
    """Session 5A.1/5A.2 — detect semantic token soup in a backend STRING, independent of any audit
    metadata: ORPHAN stopwords (a phrase-interior connector like 'and' in 'labor and delivery' is allowed),
    standalone unexplained numbers, single-character low-information tokens, suspicious concatenations, and
    unrecognized (no-vowel, non-credential) abbreviations. Each finding is a (category, token) pair. A
    broken specialty phrase ('labor delivery' with no connector) is caught separately in _audit_backend."""
    tokens = backend.split()
    allowed = _allowed_stopword_positions(tokens)
    findings = []
    for i, tok in enumerate(tokens):
        if tok in BO._STOPWORDS:
            if i not in allowed:
                findings.append(("backend_orphan_stopword", tok))
        elif tok.isdigit():
            findings.append(("backend_unexplained_number", tok))
        elif len(tok) < BO.MIN_TOKEN_LEN:
            findings.append(("backend_low_information", tok))
        elif BO._is_suspicious_concatenation(tok):
            findings.append(("backend_suspicious_concatenation", tok))
        elif BO._is_unrecognized_abbreviation(tok):
            findings.append(("backend_unrecognized_abbreviation", tok))
    return findings


# broken specialty phrases: an approved atomic unit that lost its connector/anchor and reduced to a broken
# form. The canonical case is "labor delivery" (approved unit is "labor and delivery"). String-level, so a
# broken backend is blocked even with no audit metadata.
_BROKEN_PHRASE_PAIRS = (("labor", "delivery", "labor and delivery"),)


def _broken_phrase_findings(backend):
    toks = backend.split()
    out = []
    for a_tok, b_tok, approved in _BROKEN_PHRASE_PAIRS:
        for i in range(len(toks) - 1):
            if toks[i] == a_tok and toks[i + 1] == b_tok:
                out.append((f"{a_tok} {b_tok}", approved))
    return out


def _audit_backend(a, listing, policy, results):
    br = {"bytes_used": 0, "byte_ceiling": policy.backend_byte_ceiling, "type_ok": True,
          "audit_present": False, "included_count": 0, "excluded_count": 0, "no_cut_tokens": True,
          "all_included_have_provenance": True, "all_included_have_reason": True,
          "deterministic_order": True, "no_risky_terms": True, "source_hash": "not_checked",
          # Session 5A.1/5A.2 — separate, explicit sub-results.
          "byte_safety": "PASS", "risk_safety": "not_checked", "semantic_quality": "PASS",
          "phrase_integrity": "PASS", "product_compatibility": "not_checked",
          "audience_occasion_compatibility": "not_checked", "attribute_evidence": "not_checked",
          "unit_reconstruction": "not_checked", "concept_dedup": "not_checked",
          "quality_thresholds": "not_checked", "semantic_findings": []}
    backend = listing.get("backend")
    if backend is None:
        results["backend_results"] = br
        return
    if not isinstance(backend, str):
        br["type_ok"] = False
        br["byte_safety"] = "FAIL"
        a.fail("backend_type", f"backend must be a string, got {type(backend).__name__}")
        results["backend_results"] = br
        return
    nb = len(backend.encode("utf-8"))
    br["bytes_used"] = nb
    if nb > policy.backend_byte_ceiling:
        br["byte_safety"] = "FAIL"
        a.fail("backend_overflow",
               f"backend {nb} bytes > {policy.backend_byte_ceiling} "
               f"({policy.category_identifier} backend byte ceiling)")

    # semantic-quality screen on the STRING — a backend carrying token soup is never publishable.
    findings = _backend_semantic_findings(backend)
    br["semantic_findings"] = [{"category": c, "token": t} for c, t in findings]
    if findings:
        br["semantic_quality"] = "FAIL"
        seen = set()
        for cat, tok in findings:
            key = (cat, tok)
            if key in seen:
                continue
            seen.add(key)
            a.fail(cat, f"backend term '{tok}' is a {cat.replace('backend_', '').replace('_', ' ')} "
                        f"with no independent search value")

    # broken specialty phrase (a phrase that lost its connector, e.g. "labor delivery") — never publishable.
    for broken, approved in _broken_phrase_findings(backend):
        br["phrase_integrity"] = "FAIL"
        a.fail("backend_broken_phrase",
               f"backend contains the broken specialty phrase '{broken}' — the approved atomic unit is "
               f"'{approved}' (its connector must be preserved)")

    audit = listing.get("backend_audit")
    if isinstance(audit, dict):
        _audit_backend_provenance(a, listing, backend, policy, audit, br)
    results["backend_results"] = br


def _audit_backend_provenance(a, listing, backend, policy, audit, br):
    """Validate the ACT-009 backend audit: byte accounting, no cut/hidden/reordered tokens, provenance +
    a reason for every included token, no risky included term, and source-hash consistency."""
    br["audit_present"] = True
    included = audit.get("included_terms") or []
    br["included_count"] = len(included)
    br["excluded_count"] = audit.get("excluded_count", len(audit.get("excluded_terms") or []))
    tokens = backend.split()
    included_terms = [str(t.get("term")) for t in included]

    if audit.get("bytes_used") != len(backend.encode("utf-8")):
        a.fail("backend_byte_mismatch",
               f"backend_audit bytes_used {audit.get('bytes_used')} != actual "
               f"{len(backend.encode('utf-8'))} bytes")
    if audit.get("byte_ceiling") != policy.backend_byte_ceiling:
        a.fail("backend_ceiling_source",
               f"backend_audit ceiling {audit.get('byte_ceiling')} != category policy "
               f"{policy.backend_byte_ceiling} (backend ceiling must come from the shared registry)")
    # the backend string tokens must equal the included_terms in order — this is what proves no token was
    # cut, no hidden unaudited token slipped in, and the deterministic ordering was preserved.
    if tokens != included_terms:
        # every backend token still traces to an included term? then it is unaudited/reordered, not cut.
        br["no_cut_tokens"] = all(t in set(included_terms) for t in tokens)
        br["deterministic_order"] = False
        a.fail("backend_provenance_order",
               "backend string tokens do not match backend_audit included_terms in order — a backend token "
               "is cut, unaudited, or reordered")
    # A repeated STRING token is allowed only when it is a phrase-interior shared token (the "nurse" of
    # distinct specialty phrases). A repeated STANDALONE single-token unit is a real duplicate. Concept-
    # level dedup below is the authoritative check; here we only flag a duplicate that is NOT phrase-shared.
    phrase_shared = {t.get("term") for t in included
                     if t.get("unit_type") in ("specialty_phrase", "product_type", "gift")}
    standalone = [t.get("term") for t in included if t.get("term") not in phrase_shared]
    if len(standalone) != len(set(standalone)):
        dups = sorted({x for x in standalone if standalone.count(x) > 1})
        a.fail("backend_duplicate_token", f"backend repeats standalone token(s): {dups}")
    for t in included:
        if not t.get("inclusion_reason"):
            br["all_included_have_reason"] = False
            a.fail("backend_missing_reason",
                   f"backend included term '{t.get('term')}' carries no inclusion reason")
        if not (t.get("source_keyword") and t.get("source_sha256")):
            br["all_included_have_provenance"] = False
            a.fail("backend_missing_provenance",
                   f"backend included term '{t.get('term')}' carries no source provenance")
        if t.get("blocking_risks"):
            br["no_risky_terms"] = False
            a.fail("backend_risky_term",
                   f"backend included term '{t.get('term')}' carries blocking risks {t.get('blocking_risks')}")

    br["risk_safety"] = "PASS" if br["no_risky_terms"] else "FAIL"

    conflict_states = (BO.PT_CONFLICT, BO.PT_UNVERIFIED)
    leaked_types = [t.get("term") for t in included if t.get("product_type_result") in conflict_states]
    if leaked_types:
        br["product_compatibility"] = "FAIL"
        a.fail("backend_product_type_conflict",
               f"backend published unverified/conflicting garment type(s): {sorted(set(leaked_types))}")
    else:
        br["product_compatibility"] = "PASS"

    ao = audit.get("audience_occasion_results") or {}
    supported = set(ao.get("supported_occasions") or [])
    leaked_occ = [tok for tok in tokens if tok in BO._OCCASION_TERMS and tok not in supported]
    if leaked_occ:
        br["audience_occasion_compatibility"] = "FAIL"
        a.fail("backend_unsupported_occasion",
               f"backend published unsupported occasion term(s): {sorted(set(leaked_occ))}")
    else:
        br["audience_occasion_compatibility"] = "PASS"

    # Session 5A.2 — atomic-unit audit. The included_units are the production authority; validate that the
    # emitted string is exactly their ordered concatenation (contiguity, no partial emission, no hidden
    # token), that no normalized concept repeats, that every factual attribute carries verified evidence,
    # and that every unit clears the production quality floors.
    units = audit.get("included_units")
    if isinstance(units, list) and units:
        recon = " ".join(str(u.get("text", "")) for u in units)
        if recon != backend:
            br["unit_reconstruction"] = "FAIL"
            a.fail("backend_unit_reconstruction",
                   "backend string is not the exact ordered concatenation of included_units — a unit is "
                   "partially emitted, reordered, or a hidden token was added")
        else:
            br["unit_reconstruction"] = "PASS"

        concepts = [u.get("normalized_concept") for u in units if u.get("normalized_concept")]
        dup_concepts = sorted({c for c in concepts if concepts.count(c) > 1})
        if dup_concepts:
            br["concept_dedup"] = "FAIL"
            a.fail("backend_duplicate_concept",
                   f"backend repeats normalized semantic concept(s): {dup_concepts}")
        else:
            br["concept_dedup"] = "PASS"

        _APPROVED_ATTR = ("VERIFIED_FACT", "APPROVED_IDENTITY")
        unverified_attrs = []
        for u in units:
            ut = u.get("unit_type")
            vs = u.get("verification_state")
            if ut == "attribute" and (vs not in _APPROVED_ATTR
                                      or not (u.get("product_fact_fields") or u.get("claim_ids"))):
                unverified_attrs.append(u.get("text"))
            elif ut == "gender" and vs not in _APPROVED_ATTR:
                unverified_attrs.append(u.get("text"))
        if unverified_attrs:
            br["attribute_evidence"] = "FAIL"
            a.fail("backend_unverified_attribute",
                   f"backend published product attribute(s) without verified product-fact / identity "
                   f"evidence: {sorted(set(unverified_attrs))}")
        else:
            br["attribute_evidence"] = "PASS"

        low_quality = [u.get("text") for u in units
                       if (u.get("semantic_score", 0) < BO.PRODUCTION_MIN_SEMANTIC_SCORE
                           or u.get("incremental_coverage_score", 0)
                           < BO.PRODUCTION_MIN_INCREMENTAL_COVERAGE)]
        if low_quality:
            br["quality_thresholds"] = "FAIL"
            a.fail("backend_low_quality",
                   f"backend published unit(s) below the production semantic/incremental floor "
                   f"(byte padding): {sorted(set(low_quality))}")
        else:
            br["quality_thresholds"] = "PASS"

    # phrase integrity: every specialty-phrase token keeps its unit provenance, the string reconstructs
    # from its units, and no orphan stopword / broken phrase survived (string scans set FAIL already).
    phrase_ok = all(t.get("unit") for t in included if t.get("unit_type") == "specialty_phrase")
    if not phrase_ok:
        a.fail("backend_phrase_integrity",
               "a backend specialty-phrase token lost its phrase-unit provenance")
    if (not phrase_ok or br["unit_reconstruction"] == "FAIL" or br["semantic_quality"] == "FAIL"
            or not br["deterministic_order"]):
        br["phrase_integrity"] = "FAIL"

    sh = (audit.get("source_hashes") or {}).get("keyword_source_sha256")
    lh = listing.get("keyword_source_sha256")
    if sh and lh and sh != lh:
        br["source_hash"] = "MISMATCH"
        a.fail("backend_source_hash",
               f"backend_audit keyword_source_sha256 {sh[:16]}… != listing {lh[:16]}…")
    elif sh:
        br["source_hash"] = "OK"


def _audit_item_highlights(a, listing, policy, claim_evidence, results):
    """Validate the ACT-010 item highlights: category-field support, VERIFIED facts + claim lineage, no
    unsupported claims / placeholders, unique concepts, and the policy count/character limits. An empty
    (optional) publishable set is fine and never demotes an otherwise-safe draft; an UNSAFE publishable
    highlight is a hard failure and prevents PUBLISHABLE."""
    ih = {"supported": None, "capability_state": None, "publishable_count": 0, "blocked_count": None,
          "unique_concepts": True, "within_limits": True, "policy_blocks": []}
    content = listing.get("item_highlights_content")
    pub = listing.get("item_highlights_publishable")
    if pub is None and not isinstance(content, dict):
        results["item_highlights_results"] = ih
        return
    supported = policy.supports_item_highlights()
    ih["supported"] = supported
    max_count = policy.item_highlights_max_count or IHB.DEFAULT_MAX_COUNT
    char_limit = policy.item_highlights_character_limit or IHB.DEFAULT_CHARACTER_LIMIT
    pub = pub if isinstance(pub, list) else []
    ih["publishable_count"] = len(pub)
    if isinstance(content, dict):
        ih["capability_state"] = content.get("category_support_state")
        ih["blocked_count"] = content.get("blocked_count")

    # a publishable item highlight requires a category that supports the field.
    if pub and not supported:
        a.fail("item_highlights_unsupported",
               "publishable item highlights are present but the category does not support item_highlights")
    exp_state = IHB.SUPPORTED if supported else IHB.UNSUPPORTED_BY_CATEGORY
    if isinstance(content, dict) and content.get("category_support_state") not in (None, exp_state):
        a.fail("item_highlights_capability",
               f"item_highlights capability {content.get('category_support_state')} != policy {exp_state}")

    concepts = []
    for h in pub:
        text = (h.get("text") or "") if isinstance(h, dict) else str(h)
        concepts.append(h.get("concept") if isinstance(h, dict) else None)
        if isinstance(h, dict) and h.get("publishability_status") not in (None, IHB.PUBLISHABLE):
            a.fail("item_highlights_state",
                   f"item highlight {h.get('highlight_id')} is in the publishable payload but its status is "
                   f"{h.get('publishability_status')}")
        if isinstance(h, dict) and h.get("verification_state") not in (None, "VERIFIED"):
            a.fail("item_highlights_unverified",
                   f"publishable item highlight {h.get('highlight_id')} is not VERIFIED "
                   f"({h.get('verification_state')})")
        if text and not (isinstance(h, dict) and h.get("claim_ids")):
            a.fail("item_highlights_missing_lineage",
                   f"publishable item highlight {(h.get('highlight_id') if isinstance(h, dict) else '')} "
                   f"carries text but no claim lineage")
        if _PLACEHOLDER_RE_PA.findall(text):
            a.fail("item_highlights_placeholder",
                   f"publishable item highlight contains an unresolved placeholder: {text}")
        for blk in UCP.findings_for_text(text, _claim_evidence_for_policy(listing, claim_evidence),
                                         UCP.SURFACE_ITEM_HIGHLIGHTS,
                                         category=policy.product_category):
            ih["policy_blocks"].append(blk)
            a.fail("item_highlights_unsupported_claim",
                   f"publishable item highlight states unsupported claim '{blk['phrase']}' "
                   f"[{blk['policy_class']} / {blk['reason_code']}] {blk['next_action']}")
        if len(text) > char_limit:
            ih["within_limits"] = False
            a.fail("item_highlights_limit",
                   f"publishable item highlight {len(text)} chars > {char_limit} "
                   f"({policy.category_identifier} item-highlights character limit)")
    real_concepts = [c for c in concepts if c]
    if len(set(real_concepts)) != len(real_concepts):
        ih["unique_concepts"] = False
        a.fail("item_highlights_duplicate_concept",
               f"publishable item highlights repeat a concept: {sorted(real_concepts)}")
    if len(pub) > max_count:
        a.fail("item_highlights_count",
               f"{len(pub)} publishable item highlights > policy maximum {max_count}")
    results["item_highlights_results"] = ih


def _publishable_aplus(listing):
    """The publishable A+ module list (aplus / aplus_modules / a_plus), or [] — NOT the legacy draft."""
    ap = listing.get("aplus")
    if ap is None:
        ap = listing.get("aplus_modules") or listing.get("a_plus")
    return ap if isinstance(ap, list) else ([] if ap is None else ap)


def _audit_aplus(a, listing, results):
    """Report the A+ state and enforce the legacy quarantine. Publishable A+ prose is claim/leak/
    placeholder screened via copy_text (folded in at audit_listing); here we only guard the quarantine
    and record the A+ state so PageAuditor callers can surface it."""
    ap = _publishable_aplus(listing)
    ap_is_list = isinstance(ap, list)
    state = listing.get("aplus_state")
    draft = listing.get("aplus_draft") if isinstance(listing.get("aplus_draft"), dict) else {}
    ar = {"state": state or ("PRESENT" if ap else "NONE"),
          "publishable_module_count": len(ap) if ap_is_list else 0,
          "draft_state": draft.get("state"),
          "draft_module_count": len(draft.get("modules") or []) if draft else 0}
    if not ap_is_list:
        a.fail("aplus_shape", "aplus must be a list of modules")
    elif state == APLUS_BLOCKED_LEGACY_UNVERIFIED:
        # legacy A+ is quarantined: it may exist only as a draft, never in the publishable aplus field.
        if any(_mod_text(m).strip() for m in ap):
            a.fail("aplus_quarantine",
                   "A+ is BLOCKED_LEGACY_UNVERIFIED but the publishable 'aplus' field still carries "
                   "module text — legacy A+ must stay draft-only")
        a.warning("aplus_state",
                  "A+ content is BLOCKED_LEGACY_UNVERIFIED (legacy templates, structurally audited only) "
                  "— draft-only and excluded from publishable copy; APLUS_EVIDENCE_REBUILD_REQUIRED")
    results["aplus_results"] = ar


def _audit_aplus_evidence(a, listing, claim_evidence, results, category=None):
    """Validate the Session 4 evidence-aware A+ structure (listing['aplus_content']).

    Reports capability, module counts, per-module states and the Basic-fallback/Premium invariants. It
    HARD-FAILS only on genuine safety violations — an invalid capability, a READY module that still has a
    placeholder / no claim lineage / an unsupported claim / an over-limit body, a non-READY module leaking
    into the publishable payload, a broken Basic fallback (positions must be 1..5), duplicate module keys
    or positions, or a Premium seven-module set that is not eligible+confirmed reaching publishable copy.
    An incomplete/blocked A+ (the T2 state) produces informational counts, never a hard failure — A+ is
    optional enhanced content and must not, by being incomplete, demote a safe Product Detail Page."""
    ac = listing.get("aplus_content")
    if not isinstance(ac, dict):
        results["aplus_evidence_results"] = {"present": False}
        return
    capability = listing.get("aplus_capability") or ac.get("capability")
    er = {"present": True, "capability": capability,
          "basic_module_count": ac.get("basic_module_count"),
          "basic_ready_count": ac.get("basic_ready_count"),
          "basic_ready": ac.get("basic_ready"),
          "publishable_module_count": len(ac.get("publishable_modules") or []),
          "module_states": {}, "premium_eligible": None, "selected_dynamic": [],
          "fallback_positions": [], "policy_blocks": []}

    # 1) capability must be one of the four modes (never silently defaulted).
    if capability not in AREG.CAPABILITY_MODES:
        a.fail("aplus_capability", f"invalid A+ capability {capability!r}; expected one of "
                                   f"{AREG.CAPABILITY_MODES}")

    basic = ac.get("basic_modules") or []
    er["module_states"] = {m.get("module_key"): m.get("status") for m in basic}

    # 2) expected Basic module count (0 for NO_A_PLUS, else exactly five) + unique keys and positions.
    expected = 0 if capability == AREG.NO_A_PLUS else AREG.BASIC_MODULE_COUNT
    if len(basic) != expected:
        a.fail("aplus_module_count",
               f"{capability} expects {expected} Basic modules, found {len(basic)}")
    keys = [m.get("module_key") for m in basic]
    positions = [m.get("position_slot") for m in basic]
    if len(set(keys)) != len(keys):
        a.fail("aplus_duplicate_module_key", f"duplicate A+ module keys: {keys}")
    if len(set(positions)) != len(positions):
        a.fail("aplus_duplicate_position", f"duplicate A+ position slots: {positions}")

    # 3) per-module evidence gates for READY modules (module-specific evidence, lineage, placeholders,
    #    unsupported claims, copy limits).
    for m in basic:
        status = m.get("status")
        if status not in AREG.MODULE_STATES:
            a.fail("aplus_module_state", f"module {m.get('module_key')} has unknown state {status!r}")
        if status != AREG.READY:
            continue
        mk, body = m.get("module_key"), m.get("body") or ""
        if _PLACEHOLDER_RE_PA.findall(body):
            a.fail("aplus_placeholder",
                   f"READY A+ module {mk} still contains a placeholder: {body}")
        if body and not m.get("claim_ids"):
            a.fail("aplus_missing_lineage",
                   f"READY A+ module {mk} carries factual copy but no claim lineage")
        for blk in UCP.findings_for_text(body, _claim_evidence_for_policy(listing, claim_evidence),
                                         UCP.SURFACE_APLUS,
                                         category=category):
            er["policy_blocks"].append(dict(blk, module_key=mk))
            a.fail("aplus_unsupported_claim",
                   f"READY A+ module {mk} states unsupported claim '{blk['phrase']}' "
                   f"[{blk['policy_class']} / {blk['reason_code']}] {blk['next_action']}")
        if len(body) > AREG.BODY_MAX:
            a.fail("aplus_copy_limit", f"READY A+ module {mk} body {len(body)} > {AREG.BODY_MAX} chars")
        if len(m.get("headline") or "") > AREG.HEADLINE_MAX:
            a.fail("aplus_copy_limit", f"READY A+ module {mk} headline exceeds {AREG.HEADLINE_MAX} chars")

    # 4) real-photo / asset requirements: a READY module may not still owe a required real asset.
    for m in basic:
        if m.get("status") == AREG.READY:
            es = m.get("evidence_summary") or {}
            if es.get("missing_real_assets"):
                a.fail("aplus_missing_asset",
                       f"READY A+ module {m.get('module_key')} still needs real asset(s): "
                       f"{es['missing_real_assets']}")

    # 5) alt-text limits on any asset serving a READY module.
    ready_keys = {m.get("module_key") for m in basic if m.get("status") == AREG.READY}
    for asset in ac.get("asset_manifest") or []:
        n = asset.get("alt_text_character_count") or 0
        if asset.get("module_key") in ready_keys and n > AREG.ALT_TEXT_MAX:
            a.fail("aplus_alt_text_limit",
                   f"A+ asset {asset.get('asset_id')} alt text {n} > {AREG.ALT_TEXT_MAX} chars")

    # 6) Basic fallback must always be reindexed to positions 1..5 (never a Premium slot number).
    prem = ac.get("premium") or {}
    fb = prem.get("basic_fallback") or []
    er["fallback_positions"] = [m.get("position_slot") for m in fb]
    if fb and er["fallback_positions"] != list(range(1, AREG.BASIC_MODULE_COUNT + 1)):
        a.fail("aplus_fallback_positions",
               f"Basic fallback positions must be 1..{AREG.BASIC_MODULE_COUNT}, got "
               f"{er['fallback_positions']}")

    # 7) Premium eligibility + draft separation.
    er["premium_eligible"] = prem.get("premium_eligible")
    er["selected_dynamic"] = prem.get("selected_dynamic_modules") or []
    prem_modules = prem.get("modules") or []
    if prem.get("premium_status") == "READY":
        # a truly eligible seven-module Premium: must have seven modules, all READY, two distinct dynamic.
        if len(prem_modules) != AREG.PREMIUM_MODULE_COUNT:
            a.fail("aplus_premium_count",
                   f"eligible Premium must have {AREG.PREMIUM_MODULE_COUNT} modules, found {len(prem_modules)}")
        if len(set(er["selected_dynamic"])) != AREG.PREMIUM_DYNAMIC_COUNT:
            a.fail("aplus_premium_dynamic",
                   f"Premium needs {AREG.PREMIUM_DYNAMIC_COUNT} distinct eligible dynamic modules, got "
                   f"{er['selected_dynamic']}")

    # 8) the publishable payload may contain ONLY READY modules; a draft/blocked/eligibility-unverified
    #    module must never leak into it (this is the core draft-vs-publishable separation).
    ready_module_keys = ready_keys | {m.get("module_key") for m in prem_modules
                                      if m.get("status") == AREG.READY}
    for pm in ac.get("publishable_modules") or []:
        mk = pm.get("module_key")
        if mk and mk not in ready_module_keys:
            a.fail("aplus_draft_leak",
                   f"A+ module {mk} is in the publishable payload but is not READY")
    # the publishable payload and the publishable `aplus` field must agree on count.
    pub_field = _publishable_aplus(listing)
    if isinstance(pub_field, list) and len(pub_field) != len(ac.get("publishable_modules") or []):
        a.warning("aplus_publishable_sync",
                  f"publishable aplus field has {len(pub_field)} modules but aplus_content lists "
                  f"{len(ac.get('publishable_modules') or [])}")

    # 9) an UNKNOWN-capability Premium draft must be marked never-publishable and stay out of publishable.
    if capability == AREG.UNKNOWN_A_PLUS_CAPABILITY and prem:
        if not prem.get("never_publishable"):
            a.fail("aplus_unknown_premium",
                   "UNKNOWN-capability Premium draft must be marked never_publishable "
                   "(ELIGIBILITY_UNVERIFIED)")
    results["aplus_evidence_results"] = er


_PLACEHOLDER_RE_PA = re.compile(r"\{[a-zA-Z0-9_]+\}")


def _audit_text_field_coverage(a, listing, results):
    """Enumerate every generated text field and its disposition, and hard-fail if any copy-bearing field
    is not routed through the registry (Session 3.1 PATCH 3: no silent bypass)."""
    def _text_of(val):
        if isinstance(val, (list, tuple)):
            return " ".join(x if isinstance(x, str) else _mod_text(x) for x in val)
        if isinstance(val, dict):
            return _mod_text(val)
        return str(val or "")

    registry = []
    for field, disp in AUDITED_COPY_FIELDS:
        present = bool(_text_of(listing.get(field)).strip())
        registry.append({"field": field, "disposition": disp, "present": present})
    unregistered = []
    for field in _SUSPECT_COPY_FIELDS:
        if _text_of(listing.get(field)).strip():
            unregistered.append(field)
            a.fail("unaudited_text_field",
                   f"generated text field '{field}' bypasses the audited-field registry — every text "
                   f"field must be audited, draft-only, or explicitly excluded")
    results["audited_text_fields"] = {"registry": registry, "unregistered": sorted(unregistered),
                                      "missing_requirements": list(listing.get("missing_requirements") or [])}


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
        return {"status": BLOCKED, "publishability": BLOCKED_UNSAFE, "hard_failures": [{"category": "shape",
                "message": "listing is not a JSON object"}], "warnings": [], "field_results": {},
                "keyword_results": {}, "claim_results": {}, "title_results": {}, "bullet_results": {},
                "description_results": {}, "backend_results": {}, "aplus_results": {},
                "audited_text_fields": {}, "publishability_reasons": ["not a JSON object"],
                "source_hashes": {}}

    if policy is None:
        policy = CPR.resolve_category_policy(listing.get("category") or DEFAULT_CATEGORY,
                                             marketplace="US", registry=registry)

    title = listing.get("title") or ""
    bullets = listing.get("bullets") or listing.get("bullet_points") or []
    description = listing.get("description") or ""
    item_highlights = listing.get("item_highlights") or ""
    if isinstance(item_highlights, list):
        item_highlights = ", ".join(str(x) for x in item_highlights)

    # The claim + keyword screens cover every field that can reach publishable copy: title, bullets,
    # description, item highlights, and the PUBLISHABLE A+ field. Legacy A+ is quarantined into
    # `aplus_draft` (excluded, not folded in here); a listing that keeps legacy text in the publishable
    # `aplus` field is caught by _audit_aplus. Screening publishable A+ prose enforces PATCH 3 — no A+
    # claim reaches a copy-ready package without verified backing.
    aplus_pub_text = " ".join(_mod_text(m) for m in _publishable_aplus(listing))
    ih_pub = listing.get("item_highlights_publishable")
    ih_pub_text = " ".join(_mod_text(m) for m in ih_pub) if isinstance(ih_pub, list) else ""
    copy_text = " ".join([title, description, item_highlights, ih_pub_text, aplus_pub_text]
                         + [str(b) for b in bullets])
    copy_tokens = _tokens(copy_text)

    if item_highlights and len(item_highlights) > ITEM_HIGHLIGHTS_MAX:
        a.fail("item_highlights_length",
               f"item_highlights {len(item_highlights)} chars > {ITEM_HIGHLIGHTS_MAX}")

    _audit_keywords(a, listing, copy_tokens, listing.get("backend") or "", keyword_source, results)
    _audit_claims(a, listing, copy_text, copy_tokens, claim_evidence, results,
                  category=policy.product_category)
    _audit_title(a, listing, policy, results)
    _audit_bullets(a, listing, claim_evidence, results)
    _audit_description(a, listing, results)
    _audit_backend(a, listing, policy, results)
    _audit_item_highlights(a, listing, policy, claim_evidence, results)
    _audit_aplus(a, listing, results)
    _audit_aplus_evidence(a, listing, claim_evidence, results,
                          category=policy.product_category)
    _audit_text_field_coverage(a, listing, results)
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
    publishability, publishability_reasons = _classify_publishability(a, listing, results)
    results["publishability"] = publishability
    results["publishability_reasons"] = publishability_reasons
    return {"status": status, "publishability": publishability, "hard_failures": a.hard,
            "warnings": a.warn, **results}


def _classify_publishability(a, listing, results):
    """Map the audit into one of PUBLISHABLE / SAFE_DRAFT_OWNER_FACTS_REQUIRED / BLOCKED_UNSAFE.

    - BLOCKED_UNSAFE: any hard failure (unsupported/prohibited/owner-review claims, keyword leakage,
      source-hash conflict, title/backend/structure gate, unresolved placeholder, unaudited field, …).
    - SAFE_DRAFT_OWNER_FACTS_REQUIRED: no unsafe finding, but an essential section is incomplete because
      verified owner facts are missing (a blocked bullet job or an unpublishable description). Legacy A+
      being quarantined is surfaced as APLUS_EVIDENCE_REBUILD_REQUIRED but does NOT by itself demote the
      Product Detail Page — A+ is separate, optional enhanced content.
    - PUBLISHABLE: no unsafe finding and every essential section is complete.
    """
    if a.hard:
        return BLOCKED_UNSAFE, ["unsafe: " + h["category"] for h in a.hard]
    reasons = []
    if results.get("bullet_results", {}).get("blocked_jobs"):
        reasons.append("bullet_jobs_incomplete:" + ",".join(results["bullet_results"]["blocked_jobs"]))
    dm = listing.get("description_meta") or {}
    if dm.get("publishability") == "BLOCKED_INCOMPLETE":
        reasons.append("description_incomplete")
    if reasons:
        return SAFE_DRAFT_OWNER_FACTS_REQUIRED, reasons
    return PUBLISHABLE, []


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
    """Audit a candidate listing and store it according to its publishability (Session 3.1).

    - BLOCKED_UNSAFE               : nothing is stored — neither the last safe draft nor the last
                                     publishable listing is touched; FAILED-LISTING-CANDIDATE.json is written.
    - SAFE_DRAFT_OWNER_FACTS_REQ.  : the candidate is a safe but incomplete draft. It updates the last
                                     SAFE DRAFT artifact and listing.json, but must NOT overwrite the last
                                     PUBLISHABLE listing (that older, complete listing is preserved).
    - PUBLISHABLE                  : updates both the last safe draft AND the last publishable listing.

    Always writes PAGE-AUDIT-REPORT.json and AUDITED-TEXT-FIELDS.json. Keeps the backward-compatible
    LAST-VALID-LISTING-METADATA.json, now annotated with `publishability`/`artifact_kind` so there is no
    ambiguity about whether it points at a draft or a publishable listing. Returns a result dict.
    """
    audit = audit_listing(listing, **ctx)
    publishability = audit["publishability"]
    stamp = generated_at or datetime.now(timezone.utc).isoformat()
    dest = os.path.join(folder, dest_name)
    candidate_text = json.dumps(listing, indent=2, ensure_ascii=False, sort_keys=True)
    input_hash = _sha256_text(candidate_text)

    report = {"generated_at": stamp, "status": audit["status"], "publishability": publishability,
              "dest": dest_name, "input_sha256": input_hash, "audit": audit}
    _atomic_write(os.path.join(folder, "PAGE-AUDIT-REPORT.json"),
                  json.dumps(report, indent=2, ensure_ascii=False))
    _atomic_write(os.path.join(folder, "AUDITED-TEXT-FIELDS.json"),
                  json.dumps({"generated_at": stamp, "publishability": publishability,
                              **audit.get("audited_text_fields", {})}, indent=2, ensure_ascii=False))

    promotable = publishability != BLOCKED_UNSAFE and (
        audit["status"] == PASS or (allow_warnings and audit["status"] == PASS_WITH_WARNINGS))
    if not promotable:
        failed = {"generated_at": stamp, "status": audit["status"], "publishability": publishability,
                  "input_sha256": input_hash, "hard_failures": audit["hard_failures"],
                  "warnings": audit["warnings"], "candidate": listing,
                  "note": "This candidate was BLOCKED_UNSAFE and updated NEITHER the last safe draft nor "
                          "the last publishable listing."}
        _atomic_write(os.path.join(folder, "FAILED-LISTING-CANDIDATE.json"),
                      json.dumps(failed, indent=2, ensure_ascii=False))
        return {"promoted": False, "status": audit["status"], "publishability": publishability,
                "audit": audit, "input_sha256": input_hash, "dest": dest,
                "last_valid_preserved": os.path.exists(dest),
                "last_publishable_preserved": os.path.exists(
                    os.path.join(folder, "LAST-PUBLISHABLE-LISTING-METADATA.json"))}

    # promotable (publishable OR safe draft): back up the prior working listing, then atomically replace.
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

    artifact_kind = "publishable_listing" if publishability == PUBLISHABLE else "safe_draft"
    meta = {"generated_at": stamp, "status": audit["status"], "publishability": publishability,
            "artifact_kind": artifact_kind, "dest": dest_name,
            "input_sha256": input_hash, "output_sha256": output_hash,
            "keyword_source_sha256": listing.get("keyword_source_sha256"),
            "product_fact_source_sha256": listing.get("product_fact_source_sha256"),
            "warnings": audit["warnings"]}
    # a safe write always updates the last SAFE DRAFT (a publishable listing is also a valid safe draft).
    _atomic_write(os.path.join(folder, "LAST-SAFE-DRAFT-METADATA.json"),
                  json.dumps(meta, indent=2, ensure_ascii=False))
    # backward-compatible last-valid artifact, now unambiguous about draft vs publishable.
    _atomic_write(os.path.join(folder, "LAST-VALID-LISTING-METADATA.json"),
                  json.dumps(meta, indent=2, ensure_ascii=False))
    # only a fully PUBLISHABLE listing updates the last publishable artifact + snapshot.
    if publishability == PUBLISHABLE:
        _atomic_write(os.path.join(folder, "listing.publishable.json"), output_text)
        _atomic_write(os.path.join(folder, "LAST-PUBLISHABLE-LISTING-METADATA.json"),
                      json.dumps({**meta, "snapshot": "listing.publishable.json"}, indent=2,
                                 ensure_ascii=False))
    # a promoted candidate clears any stale failed-candidate marker.
    fc = os.path.join(folder, "FAILED-LISTING-CANDIDATE.json")
    if os.path.exists(fc):
        try:
            os.remove(fc)
        except OSError:
            pass
    return {"promoted": True, "status": audit["status"], "publishability": publishability,
            "artifact_kind": artifact_kind, "audit": audit, "input_sha256": input_hash,
            "output_sha256": output_hash, "dest": dest}
