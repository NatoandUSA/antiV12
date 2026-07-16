#!/usr/bin/env python3
"""
listing.bullet_engine — five evidence-led bullet jobs (ACT-007).

Before this engine build_bullets() emitted fixed customer promises ("raised satin stitching", "shipped
from the US with tracking", "comfortable fit", "made to last") whether or not any fact backed them, and
barely used the real research inputs. This engine builds exactly five bullets, each owning a distinct
buyer job, and every factual phrase is drawn from a VERIFIED claim carrying its claim id, source fact
field, and verification state. When the evidence for a job is missing, the unsupported language is
omitted — never replaced with generic marketing filler — the gap is recorded, and if the job's essential
message cannot be stated safely the bullet is marked BLOCKED_INCOMPLETE.

The five jobs are always present and always distinct:
  1. PRODUCT_AND_PERSONALIZATION
  2. EMBROIDERY_OR_DECORATION_PROOF
  3. FIT_SIZE_COLOR_ORDER_ACCURACY
  4. CARE_PRODUCTION_PACKAGING_DELIVERY
  5. RECIPIENT_OCCASION_USE

Keyword allocations are recorded as metadata (for the placement map); they are NOT stuffed into the
visible text, and no keyword phrase is used to assert an unverified product attribute.

Public API:
  build_bullets(claim_evidence, primary_keyword, eligible_keywords=(), garment=None) -> BulletResult
"""
from __future__ import annotations

# publishability
PUBLISHABLE = "PUBLISHABLE"
BLOCKED_INCOMPLETE = "BLOCKED_INCOMPLETE"

JOBS = ("PRODUCT_AND_PERSONALIZATION", "EMBROIDERY_OR_DECORATION_PROOF",
        "FIT_SIZE_COLOR_ORDER_ACCURACY", "CARE_PRODUCTION_PACKAGING_DELIVERY",
        "RECIPIENT_OCCASION_USE")

# which claim concepts each job may draw its factual phrases from (VERIFIED only publish).
JOB_CONCEPTS = {
    "PRODUCT_AND_PERSONALIZATION": ("personalization_fields", "exact_personalization_promise"),
    "EMBROIDERY_OR_DECORATION_PROOF": ("real_machine_embroidery", "satin_stitch", "tatami_fill",
                                       "decoration_method"),
    "FIT_SIZE_COLOR_ORDER_ACCURACY": ("fit", "size_range", "color_options", "measurements"),
    "CARE_PRODUCTION_PACKAGING_DELIVERY": ("care", "production_location", "production_time",
                                           "handling_time", "shipping_method", "tracking",
                                           "packaging", "made_to_order"),
    "RECIPIENT_OCCASION_USE": ("recipient", "occasion", "differentiator"),
}

# short, non-factual topic labels. A label is only shown when the bullet has verified content.
JOB_LABELS = {
    "PRODUCT_AND_PERSONALIZATION": "PERSONALIZED FOR YOU",
    "EMBROIDERY_OR_DECORATION_PROOF": "REAL DECORATION",
    "FIT_SIZE_COLOR_ORDER_ACCURACY": "FIT, SIZE AND COLOR",
    "CARE_PRODUCTION_PACKAGING_DELIVERY": "CARE, PRODUCTION AND DELIVERY",
    "RECIPIENT_OCCASION_USE": "A THOUGHTFUL GIFT",
}


def _dedupe_decoration(verified):
    """Prefer the specific decoration claims; drop the generic decoration_method phrase when a more
    specific verified claim (machine embroidery / satin / tatami) already covers it."""
    concepts = {c["normalized_concept"] for c in verified}
    specific = concepts & {"real_machine_embroidery", "satin_stitch", "tatami_fill"}
    if specific:
        return [c for c in verified if c["normalized_concept"] != "decoration_method"]
    return verified


def _collect(claim_evidence, concepts):
    """-> (verified_records, non_publishable_records) for the given concepts, order preserved."""
    verified, other = [], []
    for concept in concepts:
        c = claim_evidence.claim(concept) if claim_evidence else None
        if not c:
            continue
        (verified if c["publishable"] else other).append(c)
    return verified, other


def _verification_summary(verified, other):
    return {
        "verified": len(verified),
        "owner_review": sum(1 for c in other if c["verification_state"] == "SUPPORTED_OWNER_REVIEW"),
        "blocked": sum(1 for c in other if c["verification_state"] == "UNVERIFIED_BLOCKED"),
        "prohibited": sum(1 for c in other if c["verification_state"] == "PROHIBITED"),
    }


def _missing(other):
    """Fields whose absence blocked a claim, for the OWNER_FACT_REQUIRED trail."""
    fields = []
    for c in other:
        for f in c.get("source_fact_fields", []):
            if not f.startswith("keyword:"):
                fields.append(f)
        if not c.get("source_fact_fields"):
            fields.append(c["normalized_concept"])
    return sorted(set(fields))


def _bullet(number, job, text, claim_ids, verified, other, kw_alloc, status):
    return {
        "bullet_number": number,
        "job": job,
        "text": text,
        "keyword_allocations": list(kw_alloc),
        "claim_ids": list(claim_ids),
        "verification_summary": _verification_summary(verified, other),
        "publishability": status,
        "missing_requirements": _missing(other) if status == BLOCKED_INCOMPLETE or other else [],
    }


def build_bullets(claim_evidence, primary_keyword, eligible_keywords=(), garment=None):
    """Build exactly five distinct-job bullets from evidence-classed claims.

    Only VERIFIED claims contribute publishable factual phrases. Bullet 1 may also state the approved
    product identity (from the primary keyword) as its lead — no other bullet repeats product identity.
    """
    product_identity = _titlecase_phrase(primary_keyword)
    # round-robin eligible keywords across bullets for the placement map (metadata only, not stuffed).
    kw_by_bullet = {i: [] for i in range(1, 6)}
    for idx, kw in enumerate(list(eligible_keywords)[:15]):
        kw_by_bullet[(idx % 5) + 1].append(kw)

    bullets = []
    for i, job in enumerate(JOBS, 1):
        verified, other = _collect(claim_evidence, JOB_CONCEPTS[job])
        if job == "EMBROIDERY_OR_DECORATION_PROOF":
            verified = _dedupe_decoration(verified)
        claim_ids = [c["claim_id"] for c in verified]
        phrases = [c["proposed_text"] for c in verified if c.get("proposed_text")]
        label = JOB_LABELS[job]

        if job == "PRODUCT_AND_PERSONALIZATION":
            # product identity is always safe (approved keyword); personalization specifics need a claim.
            lead = f"A {product_identity.lower()}" if product_identity else "This item"
            if phrases:
                text = f"{label}: {lead}. " + " ".join(phrases)
                status = PUBLISHABLE
            else:
                text = f"{label}: {lead}. Add your personalization when you order."
                status = PUBLISHABLE       # identity carries the job; personalization detail is a gap
        elif job == "RECIPIENT_OCCASION_USE":
            if phrases:
                text = f"{label}: " + " ".join(phrases)
                status = PUBLISHABLE
            else:
                text = ""
                status = BLOCKED_INCOMPLETE
        else:
            # EMBROIDERY / FIT / CARE jobs are purely factual — no verified claim means no safe text.
            if phrases:
                text = f"{label}: " + " ".join(phrases)
                status = PUBLISHABLE
            else:
                text = ""
                status = BLOCKED_INCOMPLETE

        bullets.append(_bullet(i, job, text, claim_ids, verified, other, kw_by_bullet[i], status))

    return BulletResult(bullets)


def _titlecase_phrase(s):
    import re
    small = {"for", "and", "with", "the", "a", "an", "of", "to", "in"}
    words = re.sub(r"[^A-Za-z0-9 ]", " ", str(s or "")).split()
    return " ".join(w if (w.islower() and w in small and i != 0) else w[:1].upper() + w[1:]
                    for i, w in enumerate(words))


class BulletResult:
    def __init__(self, bullets):
        self.bullets = bullets

    @property
    def texts(self):
        """The five bullet strings (backward-compatible listing['bullets'])."""
        return [b["text"] for b in self.bullets]

    @property
    def jobs(self):
        return [b["job"] for b in self.bullets]

    @property
    def publishable_count(self):
        return sum(1 for b in self.bullets if b["publishability"] == PUBLISHABLE)

    @property
    def blocked_jobs(self):
        return [b["job"] for b in self.bullets if b["publishability"] == BLOCKED_INCOMPLETE]

    @property
    def all_missing_requirements(self):
        out = []
        for b in self.bullets:
            out.extend(b["missing_requirements"])
        return sorted(set(out))

    def to_list(self):
        return list(self.bullets)
