#!/usr/bin/env python3
"""
listing.aplus_builder — the capability-aware, evidence-gated A+ builder (ACT-011/012/013/014).

Replaces a_plus_templates' six free-text templates + gap-score selector with a builder that assembles
each module's body ONLY from VERIFIED claim texts, gates every module on its own required product facts,
verified claim concepts and REAL proof assets, refuses to leave any {placeholder} in READY copy, and
never adds a Premium dynamic module just to reach seven.

Capability behaviour (exactly four states — an invalid value fails, never defaults to Basic):
  NO_A_PLUS                  -> zero modules + a description/image fallback plan
  BASIC_A_PLUS               -> exactly the five Basic modules
  PREMIUM_A_PLUS             -> seven modules only when 5 Basic READY + 2 distinct dynamic eligible + confirmed
  UNKNOWN_A_PLUS_CAPABILITY  -> complete Basic payload + a separate Premium draft (ELIGIBILITY_UNVERIFIED)

Every Premium output also carries the complete Basic fallback, reindexed to slots 1..5 (never keeping a
Premium slot number such as 7). Output is deterministic canonical JSON; identical inputs -> identical hash.

Public API:
  build_aplus(capability, claims, facts, assets=None, dynamic_data=None, premium_confirmed=False,
              garment=None) -> APlusResult
  APlusResult.to_basic_content() / .to_premium_draft() / .to_asset_manifest() / .to_compliance_report()
  write_aplus_outputs(folder, result, generated_at=None) -> {name: path}
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import aplus_module_registry as REG
import claim_evidence as CE
import product_fact_loader as PFL

_PLACEHOLDER_RE = re.compile(r"\{[a-zA-Z0-9_]+\}")

# claim phrases that must never appear in READY A+ copy without a VERIFIED claim backing them. Kept in
# sync (intentionally a superset-safe subset) with page_auditor.UNSAFE_CLAIM_PHRASES so the builder's
# self-check and the shared auditor agree; the auditor remains the authority.
_UNSAFE_PHRASES = (
    "soft and comfortable", "comfortable fit", "soft against the skin", "all day comfort",
    "for long shifts", "12 hour shifts", "made to last", "built to last", "will not fade",
    "exactly as you enter", "exactly as entered", "embroidered exactly",
    "shipped from the us", "made in the usa", "with tracking", "tracking included",
    "cotton blend", "premium fabric", "measured from the real garment", "made to order",
)


def canonical_json(obj):
    """Stable canonical serialization — sorted keys, fixed separators, no ASCII escaping."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2, separators=(",", ": "))


def _norm(s):
    s = str(s or "").lower().replace("-", " ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


# ---------------------------------------------------------------- assets
def _normalize_asset(raw, index):
    """One provided asset -> normalized record. Missing fields are null, never guessed."""
    if not isinstance(raw, dict):
        raw = {}
    real_or_generated = str(raw.get("real_or_generated") or raw.get("source_kind") or "").upper() or None
    if real_or_generated in ("AI", "MOCKUP", "RENDER", "GENERATED"):
        real_or_generated = REG.GENERATED
    elif real_or_generated in ("REAL", "PHOTO", "PHOTOGRAPH"):
        real_or_generated = REG.REAL
    alt = raw.get("alt_text")
    return {
        "asset_id": raw.get("asset_id") or f"ASSET-{index}",
        "module_key": raw.get("module_key"),
        "asset_type": raw.get("asset_type") or "image",
        "source": raw.get("source"),
        "real_or_generated": real_or_generated,
        "proof_purpose": raw.get("proof_purpose"),
        "alt_text": alt,
    }


def _asset_index(assets):
    """proof_purpose -> list of normalized supplied assets."""
    idx = {}
    for i, raw in enumerate(assets or []):
        rec = _normalize_asset(raw, i)
        idx.setdefault(rec["proof_purpose"], []).append(rec)
    return idx


def _match_real_asset(purpose, asset_index):
    """Return (real_asset, generated_asset_present) for a proof purpose."""
    supplied = asset_index.get(purpose) or []
    real = next((a for a in supplied if a["real_or_generated"] == REG.REAL), None)
    generated_present = any(a["real_or_generated"] == REG.GENERATED for a in supplied)
    return real, generated_present


# ---------------------------------------------------------------- claim helpers
def _verified(claims, concept):
    """The claim record for a concept if it is publishable (VERIFIED), else None."""
    c = claims.claim(concept)
    return c if (c and c.get("publishable")) else None


def _claim_id(claims, concept):
    c = claims.claim(concept)
    return c["claim_id"] if c else "CLM-" + concept.upper()


# ---------------------------------------------------------------- module evaluation
def _eval_module(spec, claims, facts, asset_index):
    """Evaluate one Basic module against its evidence contract -> a full module dict."""
    missing_facts, missing_real_assets, claim_blockers, placeholder_blockers = [], [], [], []
    included = []          # (claim_id, text) actually placed in the body

    # 1) required verified concepts (each maps 1:1 to a required fact for this journey)
    for concept in spec.required_concepts:
        rec = _verified(claims, concept)
        if rec:
            included.append((rec["claim_id"], rec["proposed_text"]))
        else:
            claim_blockers.append(_claim_id(claims, concept))
    for fld in spec.required_facts:
        if facts.publishable_value(fld) is None:
            missing_facts.append(fld)

    # 2) any-of module (CARE_PRODUCTION_FAQ): include every verified topic, block if none verified
    any_verified = False
    for concept in spec.any_of_concepts:
        rec = _verified(claims, concept)
        if rec:
            included.append((rec["claim_id"], rec["proposed_text"]))
            any_verified = True
    if spec.any_of_concepts and not any_verified:
        missing_facts.extend(spec.required_facts_any)

    # 3) conditional concepts — included only when verified (never required, never blocking)
    for concept in spec.conditional_concepts:
        rec = _verified(claims, concept)
        if rec:
            included.append((rec["claim_id"], rec["proposed_text"]))

    # 4) required REAL assets
    generated_only_purposes = []
    for purpose in spec.required_real_assets:
        real, generated_present = _match_real_asset(purpose, asset_index)
        if real is None:
            if generated_present:
                generated_only_purposes.append(purpose)
            missing_real_assets.append(purpose)

    # 5) assemble body from verified claim texts only (deduped, order-stable)
    body = _assemble_body(spec, included)
    claim_ids = sorted({cid for cid, _ in included})
    for m in _PLACEHOLDER_RE.findall(body):
        placeholder_blockers.append(m)

    # 6) unsafe-claim self check (defence in depth — body is built from verified claims, so this only
    #    ever fires if a verified claim text itself contains an unbacked phrase, which the auditor also
    #    catches). Only fires when the phrase is NOT present in an included verified claim text.
    unsupported = []
    nbody = _norm(body)
    included_norm = _norm(" ".join(t for _, t in included))
    for phrase in _UNSAFE_PHRASES:
        if phrase in nbody and phrase not in included_norm:
            unsupported.append(phrase)

    # 7) copy / alt-text limits
    limit_issues = []
    if len(spec.headline) > REG.HEADLINE_MAX:
        limit_issues.append(f"headline_exceeds_{REG.HEADLINE_MAX}")
    if len(body) > REG.BODY_MAX:
        limit_issues.append(f"body_exceeds_{REG.BODY_MAX}")

    status = _module_status(missing_facts, missing_real_assets, generated_only_purposes,
                            placeholder_blockers, unsupported, limit_issues)

    missing_requirements = sorted(set(
        [f"fact:{f}" for f in missing_facts]
        + [f"real_photo:{p}" for p in missing_real_assets]
        + [f"placeholder:{p}" for p in placeholder_blockers]
        + [f"unsupported_claim:{p}" for p in unsupported]
        + list(limit_issues)))

    return {
        "module_key": spec.module_key,
        "position_slot": spec.position_slot,
        "purpose": spec.purpose,
        "headline": spec.headline,
        "body": body,
        "asset_brief": spec.asset_brief,
        "required_facts": sorted(set(list(spec.required_facts) + list(spec.required_facts_any))),
        "required_claim_ids": sorted({_claim_id(claims, c)
                                      for c in list(spec.required_concepts) + list(spec.any_of_concepts)}),
        "required_real_assets": list(spec.required_real_assets),
        "optional_assets": list(spec.optional_assets),
        "copy_limits": {"headline_max": REG.HEADLINE_MAX, "body_max": REG.BODY_MAX},
        "alt_text_limits": {"alt_text_max": REG.ALT_TEXT_MAX},
        "claim_ids": claim_ids,
        "status": status,
        "publishable": status == REG.READY,
        "missing_requirements": missing_requirements,
        "evidence_summary": {
            "verified_claim_ids": claim_ids,
            "missing_facts": sorted(set(missing_facts)),
            "missing_real_assets": sorted(set(missing_real_assets)),
            "generated_only_assets": sorted(set(generated_only_purposes)),
            "claim_blockers": sorted(set(claim_blockers)),
            "placeholder_blockers": sorted(set(placeholder_blockers)),
            "unsupported_claims": sorted(set(unsupported)),
            "limit_issues": sorted(set(limit_issues)),
        },
    }


def _assemble_body(spec, included):
    """Body copy = only verified claim texts, deduped, in the order they were added. HOW_TO_CUSTOMIZE
    additionally frames the personalization fields as an ordering instruction (no production process)."""
    seen, parts = set(), []
    for _, text in included:
        t = (text or "").strip()
        if t and t not in seen:
            seen.add(t)
            parts.append(t)
    body = " ".join(parts)
    if spec.module_key == "HOW_TO_CUSTOMIZE" and parts:
        body = body + " Enter your personalization in the fields at checkout, then add to cart to order."
    return body.strip()


def _module_status(missing_facts, missing_real_assets, generated_only, placeholders, unsupported, limits):
    """Blocking precedence: owner facts first, then real photos, then AI-can't-prove, then placeholders,
    then unsupported claims, then limits. READY only when nothing blocks."""
    if missing_facts:
        return REG.OWNER_FACT_REQUIRED
    if missing_real_assets and not generated_only:
        return REG.REAL_PHOTO_REQUIRED
    if generated_only:
        return REG.BLOCKED_MISSING_ASSET      # an AI/generated asset cannot prove a real claim
    if placeholders:
        return REG.BLOCKED_PLACEHOLDER
    if unsupported:
        return REG.BLOCKED_UNSUPPORTED_CLAIM
    if limits:
        return REG.BLOCKED_UNVERIFIED
    return REG.READY


# ---------------------------------------------------------------- dynamic (Premium) eligibility
def _eval_dynamic(spec, claims, facts, asset_index, dynamic_data):
    """Evaluate one Premium dynamic module -> (eligible, reasons, module_dict)."""
    reasons = []
    dyn = dynamic_data or {}

    def concept_ok(c):
        return _verified(claims, c) is not None

    def fact_ok(f):
        return facts.publishable_value(f) is not None or bool(dyn.get(f))

    for c in spec.req_concepts_all:
        if not concept_ok(c):
            reasons.append(f"missing_verified_concept:{c}")
    if spec.req_concepts_any and not any(concept_ok(c) for c in spec.req_concepts_any):
        reasons.append("missing_any_verified_concept:" + ",".join(spec.req_concepts_any))
    for f in spec.req_facts_all:
        if not fact_ok(f):
            reasons.append(f"missing_verified_fact:{f}")
    if spec.req_facts_any and not any(fact_ok(f) for f in spec.req_facts_any):
        reasons.append("missing_any_verified_fact:" + ",".join(spec.req_facts_any))
    missing_assets = []
    for purpose in spec.req_real_assets:
        real, _ = _match_real_asset(purpose, asset_index)
        if real is None:
            missing_assets.append(purpose)
            reasons.append(f"missing_real_asset:{purpose}")

    eligible = not reasons
    # body copy for an eligible dynamic module = its verified concept claim texts only (never filler).
    included = []
    for concept in list(spec.req_concepts_all) + list(spec.req_concepts_any):
        rec = _verified(claims, concept)
        if rec:
            included.append((rec["claim_id"], rec["proposed_text"]))
    body = _assemble_body(spec, included)
    claim_ids = sorted({cid for cid, _ in included})
    module = {
        "module_key": spec.module_key,
        "purpose": spec.purpose,
        "headline": spec.headline,
        "body": body,
        "asset_brief": spec.asset_brief,
        "eligibility_rule": spec.eligibility_rule,
        "eligible": eligible,
        "ineligible_reasons": sorted(set(reasons)),
        "required_real_assets": list(spec.req_real_assets),
        "missing_real_assets": sorted(set(missing_assets)),
        "claim_ids": claim_ids,
        "copy_limits": {"headline_max": REG.HEADLINE_MAX, "body_max": REG.BODY_MAX},
        "alt_text_limits": {"alt_text_max": REG.ALT_TEXT_MAX},
    }
    return eligible, reasons, module


# ---------------------------------------------------------------- result
class APlusResult:
    """The deterministic, capability-aware A+ build for one product."""

    def __init__(self, capability, basic_modules, dynamic_eval, selected_dynamic, premium_confirmed,
                 asset_manifest, source_claim_sha256, source_fact_sha256, fallback_plan=None):
        self.capability = capability
        self.basic_modules = basic_modules              # [] for NO_A_PLUS, else 5 module dicts
        self.dynamic_eval = dynamic_eval                # list of dynamic module dicts (with eligibility)
        self.selected_dynamic = selected_dynamic        # <=2 module_keys chosen for Premium
        self.premium_confirmed = premium_confirmed
        self.asset_manifest = asset_manifest
        self.source_claim_sha256 = source_claim_sha256
        self.source_fact_sha256 = source_fact_sha256
        self.fallback_plan = fallback_plan

    # -- Basic ------------------------------------------------------
    @property
    def basic_ready_count(self):
        return sum(1 for m in self.basic_modules if m["status"] == REG.READY)

    @property
    def basic_ready(self):
        return len(self.basic_modules) == REG.BASIC_MODULE_COUNT and \
            self.basic_ready_count == REG.BASIC_MODULE_COUNT

    def basic_fallback(self):
        """The five Basic modules, reindexed to slots 1..5 (never a Premium slot number)."""
        out = []
        for i, m in enumerate(sorted(self.basic_modules, key=lambda x: x["position_slot"]), start=1):
            out.append({**m, "position_slot": i})
        return out

    # -- Premium ----------------------------------------------------
    @property
    def eligible_dynamic(self):
        return [d for d in self.dynamic_eval if d["eligible"]]

    @property
    def premium_eligible(self):
        """Premium is publishable ONLY when Basic is fully READY, two distinct dynamic modules are
        eligible, AND Premium eligibility was explicitly confirmed."""
        return (self.capability == REG.PREMIUM_A_PLUS and self.basic_ready
                and len(self.selected_dynamic) == REG.PREMIUM_DYNAMIC_COUNT
                and len(set(self.selected_dynamic)) == REG.PREMIUM_DYNAMIC_COUNT
                and self.premium_confirmed)

    def premium_modules(self, draft=False):
        """The seven-module Premium journey: 5 Basic at slots 1-5 + 2 dynamic at slots 6-7.
        In an UNKNOWN-capability draft every module is marked ELIGIBILITY_UNVERIFIED (never publishable)."""
        base = self.basic_fallback()
        dyn = []
        for i, key in enumerate(self.selected_dynamic[:REG.PREMIUM_DYNAMIC_COUNT], start=6):
            d = next((x for x in self.dynamic_eval if x["module_key"] == key), None)
            if d:
                dyn.append({**d, "position_slot": i,
                            "status": REG.ELIGIBILITY_UNVERIFIED if draft
                            else (REG.READY if d["eligible"] else REG.BLOCKED_UNVERIFIED)})
        mods = base + dyn
        if draft:
            mods = [{**m, "status": REG.ELIGIBILITY_UNVERIFIED, "publishable": False} for m in mods]
        return mods

    # -- publishable payload ---------------------------------------
    def publishable_modules(self):
        """The A+ modules that may enter a publishable payload — [] unless the evidence gates all pass."""
        if self.capability == REG.NO_A_PLUS:
            return []
        if self.premium_eligible:
            mods = self.premium_modules(draft=False)
            if all(m["status"] == REG.READY for m in mods):
                return [{"headline": m["headline"], "copy": m.get("body", ""),
                         "module_key": m["module_key"], "position_slot": m["position_slot"]} for m in mods]
            return []
        if self.capability in (REG.BASIC_A_PLUS, REG.PREMIUM_A_PLUS, REG.UNKNOWN_A_PLUS_CAPABILITY) \
                and self.basic_ready:
            return [{"headline": m["headline"], "copy": m.get("body", ""),
                     "module_key": m["module_key"], "position_slot": m["position_slot"]}
                    for m in self.basic_fallback()]
        return []

    @property
    def publishable(self):
        return bool(self.publishable_modules())

    # -- output documents ------------------------------------------
    def to_basic_content(self):
        return {
            "schema_version": "1.0.0",
            "capability": self.capability,
            "basic_module_count": len(self.basic_modules),
            "basic_ready_count": self.basic_ready_count,
            "basic_ready": self.basic_ready,
            "modules": self.basic_modules,
            "publishable": self.basic_ready and self.capability != REG.NO_A_PLUS,
            "source_claim_evidence_sha256": self.source_claim_sha256,
            "source_product_fact_sha256": self.source_fact_sha256,
        }

    def to_premium_draft(self):
        # Premium content exists only for PREMIUM_A_PLUS and UNKNOWN (which yields an unverified draft).
        # NO_A_PLUS and BASIC_A_PLUS are explicit owner capabilities that carry no Premium payload.
        if self.capability in (REG.NO_A_PLUS, REG.BASIC_A_PLUS):
            return {"schema_version": "1.0.0", "capability": self.capability, "premium": None,
                    "reason": f"{self.capability} produces no Premium content."}
        is_draft = self.capability == REG.UNKNOWN_A_PLUS_CAPABILITY
        eligible = self.premium_eligible
        status = (REG.ELIGIBILITY_UNVERIFIED if is_draft
                  else ("READY" if eligible else "PREMIUM_INCOMPLETE"))
        modules = self.premium_modules(draft=is_draft) if (is_draft or self.capability == REG.PREMIUM_A_PLUS) \
            else None
        return {
            "schema_version": "1.0.0",
            "capability": self.capability,
            "premium_status": status,
            "premium_eligible": eligible,
            "eligibility_unverified": is_draft,
            "never_publishable": is_draft or not eligible,
            "premium_confirmed": self.premium_confirmed,
            "selected_dynamic_modules": list(self.selected_dynamic),
            "eligible_dynamic_modules": [d["module_key"] for d in self.eligible_dynamic],
            "dynamic_pool": self.dynamic_eval,
            "modules": modules,
            "basic_fallback": self.basic_fallback(),
            "source_claim_evidence_sha256": self.source_claim_sha256,
        }

    def to_asset_manifest(self):
        return {"schema_version": "1.0.0", "capability": self.capability,
                "assets": self.asset_manifest}

    def to_compliance_report(self):
        module_states = {m["module_key"]: m["status"] for m in self.basic_modules}
        agg_missing_facts, agg_missing_assets, agg_claim_blockers, agg_placeholders = set(), set(), set(), set()
        for m in self.basic_modules:
            es = m["evidence_summary"]
            agg_missing_facts.update(es["missing_facts"])
            agg_missing_assets.update(es["missing_real_assets"])
            agg_claim_blockers.update(es["claim_blockers"])
            agg_placeholders.update(es["placeholder_blockers"])
        return {
            "schema_version": "1.0.0",
            "capability": self.capability,
            "basic_module_count": len(self.basic_modules),
            "basic_ready_count": self.basic_ready_count,
            "basic_ready": self.basic_ready,
            "basic_module_states": module_states,
            "premium_eligible": self.premium_eligible,
            "premium_confirmed": self.premium_confirmed,
            "selected_dynamic_modules": list(self.selected_dynamic),
            "eligible_dynamic_count": len(self.eligible_dynamic),
            "publishable_module_count": len(self.publishable_modules()),
            "missing_facts": sorted(agg_missing_facts),
            "missing_real_assets": sorted(agg_missing_assets),
            "claim_blockers": sorted(agg_claim_blockers),
            "placeholder_blockers": sorted(agg_placeholders),
            "fallback_positions": [m["position_slot"] for m in self.basic_fallback()],
            "source_claim_evidence_sha256": self.source_claim_sha256,
            "source_product_fact_sha256": self.source_fact_sha256,
        }

    # -- deterministic canonical view + hash -----------------------
    def canonical_content(self):
        return {
            "capability": self.capability,
            "basic": self.to_basic_content(),
            "premium": self.to_premium_draft(),
            "assets": self.to_asset_manifest(),
            "compliance": self.to_compliance_report(),
            "publishable_modules": self.publishable_modules(),
        }

    def content_sha256(self):
        return hashlib.sha256(canonical_json(self.canonical_content()).encode("utf-8")).hexdigest()

    def listing_block(self):
        """The compact block embedded in listing.json so PageAuditor sees every A+ field."""
        return {
            "capability": self.capability,
            "basic_module_count": len(self.basic_modules),
            "basic_ready_count": self.basic_ready_count,
            "basic_ready": self.basic_ready,
            "basic_modules": self.basic_modules,
            "premium": self.to_premium_draft(),
            "asset_manifest": self.asset_manifest,
            "compliance": self.to_compliance_report(),
            "publishable_modules": self.publishable_modules(),
            "fallback_plan": self.fallback_plan,
            "content_sha256": self.content_sha256(),
        }


# ---------------------------------------------------------------- build
def build_aplus(capability, claims, facts, assets=None, dynamic_data=None, premium_confirmed=False,
                garment=None):
    """Build the capability-aware A+ payload. Raises InvalidCapabilityError on a bad capability value."""
    capability = REG.validate_capability(capability)      # None -> UNKNOWN; bad value -> raise
    if not isinstance(claims, CE.ClaimEvidence):
        raise TypeError("build_aplus expects a claim_evidence.ClaimEvidence")
    if not isinstance(facts, PFL.NormalizedProductFacts):
        raise TypeError("build_aplus expects a product_fact_loader.NormalizedProductFacts")

    asset_index = _asset_index(assets)
    claim_sha = claims.content_sha256()
    fact_sha = facts.source_sha256

    # NO_A_PLUS: zero modules, only a description/image fallback plan.
    if capability == REG.NO_A_PLUS:
        fallback_plan = {
            "kind": "DESCRIPTION_AND_IMAGE_FALLBACK",
            "note": "This catalog has NO A+ capability. Use the product description and the standard image "
                    "stack; do not generate hidden Basic or Premium modules.",
            "recommended_images": ["Main white-background product photo",
                                   "Real decoration macro (if decoration is verified)",
                                   "Personalization example (real photo)",
                                   "Size/colour reference (real photo)"],
        }
        return APlusResult(capability, [], [], [], premium_confirmed,
                           _build_asset_manifest([], asset_index), claim_sha, fact_sha,
                           fallback_plan=fallback_plan)

    # Basic modules (always built for BASIC / PREMIUM / UNKNOWN).
    basic_modules = [_eval_module(spec, claims, facts, asset_index) for spec in REG.BASIC_MODULES]

    # Dynamic pool evaluation (needed for PREMIUM and UNKNOWN draft).
    dynamic_eval, selected = [], []
    if capability in (REG.PREMIUM_A_PLUS, REG.UNKNOWN_A_PLUS_CAPABILITY):
        for spec in REG.PREMIUM_DYNAMIC_POOL:
            _, _, module = _eval_dynamic(spec, claims, facts, asset_index, dynamic_data)
            dynamic_eval.append(module)
        # deterministic selection: pool order, distinct, only genuinely eligible, capped at two.
        for d in dynamic_eval:
            if d["eligible"] and d["module_key"] not in selected:
                selected.append(d["module_key"])
            if len(selected) == REG.PREMIUM_DYNAMIC_COUNT:
                break

    asset_manifest = _build_asset_manifest(basic_modules, asset_index, dynamic_eval, selected)
    return APlusResult(capability, basic_modules, dynamic_eval, selected, premium_confirmed,
                       asset_manifest, claim_sha, fact_sha)


def _build_asset_manifest(basic_modules, asset_index, dynamic_eval=None, selected=None):
    """One record per required/optional asset across modules + any supplied asset, with a status."""
    manifest = []
    seen = set()

    def add_required(module_key, purpose, kind):
        key = (module_key, purpose)
        if key in seen:
            return
        seen.add(key)
        real, generated_present = _match_real_asset(purpose, asset_index)
        chosen = real or (asset_index.get(purpose) or [None])[0]
        if chosen is None:
            manifest.append({
                "asset_id": f"{module_key}:{purpose}",
                "module_key": module_key, "asset_type": "image", "source": None,
                "real_or_generated": None, "proof_purpose": purpose, "requirements": kind,
                "status": REG.ASSET_REAL_PHOTO_REQUIRED if kind == "REAL_PHOTO" else REG.ASSET_ALT_TEXT_REQUIRED,
                "missing_reason": "no real photo supplied for this proof purpose",
                "alt_text": None, "alt_text_character_count": 0})
            return
        alt = chosen.get("alt_text")
        alt_len = len(alt) if isinstance(alt, str) else 0
        if kind == "REAL_PHOTO" and chosen["real_or_generated"] != REG.REAL:
            status, reason = REG.ASSET_GENERATED_NOT_PROOF, "generated/AI asset cannot prove a real claim"
        elif alt and alt_len > REG.ALT_TEXT_MAX:
            status, reason = REG.ASSET_ALT_TEXT_REQUIRED, f"alt text {alt_len} > {REG.ALT_TEXT_MAX} chars"
        elif not alt:
            status, reason = REG.ASSET_ALT_TEXT_REQUIRED, "alt text missing"
        else:
            status, reason = REG.ASSET_READY, None
        manifest.append({
            "asset_id": chosen["asset_id"], "module_key": module_key,
            "asset_type": chosen["asset_type"], "source": chosen["source"],
            "real_or_generated": chosen["real_or_generated"], "proof_purpose": purpose,
            "requirements": kind, "status": status, "missing_reason": reason,
            "alt_text": alt, "alt_text_character_count": alt_len})

    for m in basic_modules:
        spec = REG.BASIC_BY_KEY[m["module_key"]]
        for purpose in spec.required_real_assets:
            add_required(m["module_key"], purpose, "REAL_PHOTO")
        for purpose in spec.optional_assets:
            add_required(m["module_key"], purpose, "OPTIONAL")
    for key in (selected or []):
        spec = REG.PREMIUM_BY_KEY.get(key)
        if spec:
            for purpose in spec.req_real_assets:
                add_required(key, purpose, "REAL_PHOTO")
    return manifest


# ---------------------------------------------------------------- output files
APLUS_OUTPUT_FILES = {
    "BASIC-APLUS-CONTENT.json": "to_basic_content",
    "PREMIUM-APLUS-DRAFT.json": "to_premium_draft",
    "APLUS-ASSET-MANIFEST.json": "to_asset_manifest",
    "APLUS-COMPLIANCE-REPORT.json": "to_compliance_report",
}


def write_aplus_outputs(folder, result, generated_at=None):
    """Write the four canonical A+ JSON artifacts. generated_at is the only volatile field (excluded from
    the content hash). Returns {filename: path}."""
    written = {}
    for name, method in APLUS_OUTPUT_FILES.items():
        doc = getattr(result, method)()
        if generated_at:
            doc = {**doc, "generated_at": generated_at}
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(canonical_json(doc))
        written[name] = path
    return written


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Build capability-aware A+ content for a project folder")
    ap.add_argument("folder")
    ap.add_argument("--capability", default=None)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    facts = PFL.load_product_facts(folder=a.folder)
    claims = CE.build_claim_evidence(facts)
    result = build_aplus(a.capability, claims, facts)
    print(f"capability {result.capability} · basic ready {result.basic_ready_count}/"
          f"{len(result.basic_modules)} · premium eligible {result.premium_eligible} · "
          f"publishable modules {len(result.publishable_modules())}")
    print(f"content sha256 {result.content_sha256()}")
    if a.write:
        for name, path in write_aplus_outputs(a.folder, result).items():
            print(f"wrote {path}")


if __name__ == "__main__":
    main()
