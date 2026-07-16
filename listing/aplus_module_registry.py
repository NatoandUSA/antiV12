#!/usr/bin/env python3
"""
listing.aplus_module_registry — the evidence contract for capability-aware A+ content (ACT-011/012/013).

The legacy a_plus_templates library carried six free-text templates with unsupported comfort, durability,
production, shipping and measurement claims, chose modules by competitor-gap score, and left unresolved
{placeholder} text in the output. This registry replaces that with a DECLARATIVE contract: it says, per
module, exactly which verified product facts, verified claim concepts and REAL assets a module needs
before any of its copy may be published. It carries no free text of its own beyond claim-safe headlines
and asset briefs — every factual sentence a module emits is assembled downstream from VERIFIED claims.

This module is pure data + small validators. The builder (aplus_builder) consumes it; the auditor
(page_auditor) re-checks the built output against the same counts and rules.

Capability modes (ACT-012):
  NO_A_PLUS                    zero modules; description/image fallback plan only
  BASIC_A_PLUS                 exactly the five Basic modules
  PREMIUM_A_PLUS               seven modules, only when 5 Basic valid + 2 distinct dynamic eligible + confirmed
  UNKNOWN_A_PLUS_CAPABILITY    complete Basic payload + a separate Premium draft marked ELIGIBILITY_UNVERIFIED

An invalid capability value fails loudly — it is NEVER silently defaulted to Basic.
"""
from __future__ import annotations

# ---------------------------------------------------------------- capability modes
NO_A_PLUS = "NO_A_PLUS"
BASIC_A_PLUS = "BASIC_A_PLUS"
PREMIUM_A_PLUS = "PREMIUM_A_PLUS"
UNKNOWN_A_PLUS_CAPABILITY = "UNKNOWN_A_PLUS_CAPABILITY"
CAPABILITY_MODES = (NO_A_PLUS, BASIC_A_PLUS, PREMIUM_A_PLUS, UNKNOWN_A_PLUS_CAPABILITY)
# When the owner has not stated their A+ eligibility we do NOT know if they have Basic or Premium access,
# so the honest default is UNKNOWN — it produces a validated Basic payload plus an unverified Premium draft.
DEFAULT_CAPABILITY = UNKNOWN_A_PLUS_CAPABILITY

BASIC_MODULE_COUNT = 5
PREMIUM_MODULE_COUNT = 7
PREMIUM_DYNAMIC_COUNT = 2   # 7 = 5 Basic + 2 distinct dynamic


class InvalidCapabilityError(ValueError):
    """A capability value that is not one of the four supported modes. Never defaulted to Basic."""

    def __init__(self, value):
        self.value = value
        super().__init__(f"invalid A+ capability {value!r}; expected one of {CAPABILITY_MODES}. "
                         f"An unknown capability must be stated as {UNKNOWN_A_PLUS_CAPABILITY}, never "
                         f"silently treated as {BASIC_A_PLUS}.")


def validate_capability(value):
    """Return the canonical capability string, or raise InvalidCapabilityError.

    None / empty -> DEFAULT_CAPABILITY (UNKNOWN). Any non-empty value that is not a known mode raises.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_CAPABILITY
    v = str(value).strip().upper()
    if v not in CAPABILITY_MODES:
        raise InvalidCapabilityError(value)
    return v


# ---------------------------------------------------------------- module states
READY = "READY"
OWNER_FACT_REQUIRED = "OWNER_FACT_REQUIRED"
REAL_PHOTO_REQUIRED = "REAL_PHOTO_REQUIRED"
ELIGIBILITY_UNVERIFIED = "ELIGIBILITY_UNVERIFIED"
BLOCKED_UNVERIFIED = "BLOCKED_UNVERIFIED"
BLOCKED_UNSUPPORTED_CLAIM = "BLOCKED_UNSUPPORTED_CLAIM"
BLOCKED_MISSING_ASSET = "BLOCKED_MISSING_ASSET"
BLOCKED_PLACEHOLDER = "BLOCKED_PLACEHOLDER"
MODULE_STATES = (READY, OWNER_FACT_REQUIRED, REAL_PHOTO_REQUIRED, ELIGIBILITY_UNVERIFIED,
                 BLOCKED_UNVERIFIED, BLOCKED_UNSUPPORTED_CLAIM, BLOCKED_MISSING_ASSET, BLOCKED_PLACEHOLDER)
# a module in any of these states must never enter a publishable payload.
NON_PUBLISHABLE_STATES = tuple(s for s in MODULE_STATES if s != READY)

# ---------------------------------------------------------------- asset states
ASSET_READY = "READY"
ASSET_REAL_PHOTO_REQUIRED = "REAL_PHOTO_REQUIRED"      # a real photo is required and none was supplied
ASSET_GENERATED_NOT_PROOF = "GENERATED_CANNOT_PROVE"   # an AI/generated asset cannot prove a real claim
ASSET_ALT_TEXT_REQUIRED = "ALT_TEXT_REQUIRED"

REAL = "REAL"
GENERATED = "GENERATED"

# ---------------------------------------------------------------- copy / alt-text limits
HEADLINE_MAX = 160
BODY_MAX = 500
ALT_TEXT_MAX = 100


class ModuleSpec:
    """A declarative A+ module contract. No free copy beyond a claim-safe headline and an asset brief.

    required_facts        product-fact fields the owner must verify (human-readable blocker names)
    required_concepts     claim concepts that MUST be VERIFIED before the module can be READY
    conditional_concepts  claim concepts included only if they happen to be VERIFIED (never required)
    required_real_assets  proof purposes that need a REAL photo (AI mockups cannot satisfy these)
    optional_assets       proof purposes that improve the module but do not block it
    any_of_concepts       if set, at least ONE must be VERIFIED (used by the FAQ / lifestyle modules)
    """

    def __init__(self, module_key, position_slot, purpose, headline, asset_brief,
                 required_facts=(), required_concepts=(), conditional_concepts=(),
                 required_real_assets=(), optional_assets=(), any_of_concepts=(),
                 required_facts_any=(), copy_intro=""):
        self.module_key = module_key
        self.position_slot = position_slot
        self.purpose = purpose
        self.headline = headline
        self.asset_brief = asset_brief
        self.required_facts = tuple(required_facts)
        self.required_concepts = tuple(required_concepts)
        self.conditional_concepts = tuple(conditional_concepts)
        self.required_real_assets = tuple(required_real_assets)
        self.optional_assets = tuple(optional_assets)
        self.any_of_concepts = tuple(any_of_concepts)
        self.required_facts_any = tuple(required_facts_any)
        self.copy_intro = copy_intro

    def limits(self):
        return {"headline_max": HEADLINE_MAX, "body_max": BODY_MAX, "alt_text_max": ALT_TEXT_MAX}


# ---------------------------------------------------------------- Basic journey (exactly five, ordered)
BASIC_MODULES = [
    ModuleSpec(
        module_key="HERO_EMBROIDERY_PROOF", position_slot=1,
        purpose="Prove the decoration is real, not a printed graphic, with a macro stitch photo.",
        headline="See the Real Decoration",
        asset_brief="Macro close-up of the actual embroidered/decorated area showing individual threads "
                    "and satin edges. MUST be a real photo of the finished product — an AI mockup cannot "
                    "prove stitch quality.",
        required_facts=["decoration_method"],
        required_concepts=["decoration_method"],
        conditional_concepts=["real_machine_embroidery", "satin_stitch", "tatami_fill"],
        required_real_assets=["MACRO_DECORATION_STITCH"],
        copy_intro="",
    ),
    ModuleSpec(
        module_key="PERSONALIZATION_GALLERY", position_slot=2,
        purpose="Show the supported personalization fields with real examples.",
        headline="Personalized For Them",
        asset_brief="A real photo of the finished product carrying an actual example personalization "
                    "(name/initials), plus an inset of the checkout personalization fields. Real photo — "
                    "no invented text and no 'exactly as entered' claim unless explicitly verified.",
        required_facts=["personalization_fields"],
        required_concepts=["personalization_fields"],
        # exact_personalization_promise has no evidence field, so it can never be verified here -> the
        # module can never claim 'embroidered exactly as you enter it' unless the owner supplies that fact.
        conditional_concepts=["exact_personalization_promise"],
        required_real_assets=["PERSONALIZATION_EXAMPLE"],
        copy_intro="",
    ),
    ModuleSpec(
        module_key="HOW_TO_CUSTOMIZE", position_slot=3,
        purpose="Explain the ordering steps and the input fields — no invented production process.",
        headline="How to Customize",
        asset_brief="Simple numbered graphic of the ordering steps and the personalization input fields. "
                    "Illustrative graphic is acceptable; do NOT depict an invented production process.",
        required_facts=["personalization_fields"],
        required_concepts=["personalization_fields"],
        conditional_concepts=[],
        required_real_assets=[],
        optional_assets=["ORDER_STEPS_GRAPHIC"],
        copy_intro="",
    ),
    ModuleSpec(
        module_key="FIT_SIZE_COLOR_DETAILS", position_slot=4,
        purpose="Show the real size range and colour options; measurements only when stated.",
        headline="Fit, Size & Colors",
        asset_brief="A real garment/colour photo or an accurate size chart built from REAL measurements. "
                    "Colours must be shown from real product photos, not AI-tinted mockups.",
        required_facts=["size_range", "color_options"],
        required_concepts=["size_range", "color_options"],
        conditional_concepts=["measurements", "fit"],
        required_real_assets=["GARMENT_OR_COLOR"],
        copy_intro="",
    ),
    ModuleSpec(
        module_key="CARE_PRODUCTION_FAQ", position_slot=5,
        purpose="Answer only the FAQ topics that are independently verified; omit the rest.",
        headline="Care & Production FAQ",
        asset_brief="Clean text/FAQ layout. Only include topics that are verified facts; leave unanswered "
                    "topics out rather than inventing a generic answer.",
        required_facts=[],
        required_facts_any=["care_instructions", "production_time_range", "handling_time",
                            "production_location", "packaging", "shipping_method", "tracking"],
        any_of_concepts=["care", "production_time", "handling_time", "production_location",
                         "packaging", "shipping_method", "tracking"],
        required_real_assets=[],
        copy_intro="",
    ),
]
BASIC_MODULE_KEYS = tuple(m.module_key for m in BASIC_MODULES)
BASIC_BY_KEY = {m.module_key: m for m in BASIC_MODULES}


# ---------------------------------------------------------------- Premium dynamic pool
class DynamicSpec:
    """Eligibility contract for a Premium dynamic module. A module is eligible ONLY when every required
    condition is met — there is no 'fill to seven' path. Conditions reference verified claim concepts,
    verified product-fact fields (including fields not yet in the schema, which therefore keep a module
    ineligible until the owner supplies explicit evidence), and REAL proof assets."""

    def __init__(self, module_key, purpose, headline, asset_brief,
                 req_concepts_all=(), req_concepts_any=(), req_facts_all=(), req_facts_any=(),
                 req_real_assets=(), eligibility_rule=""):
        self.module_key = module_key
        self.purpose = purpose
        self.headline = headline
        self.asset_brief = asset_brief
        self.req_concepts_all = tuple(req_concepts_all)
        self.req_concepts_any = tuple(req_concepts_any)
        self.req_facts_all = tuple(req_facts_all)
        self.req_facts_any = tuple(req_facts_any)
        self.req_real_assets = tuple(req_real_assets)
        self.eligibility_rule = eligibility_rule


PREMIUM_DYNAMIC_POOL = [
    DynamicSpec(
        "LIFESTYLE_RECIPIENT_OCCASION",
        purpose="A lifestyle scene tied to a verified recipient or occasion.",
        headline="Made For The Moment",
        asset_brief="A real lifestyle photo of the product with the intended recipient/occasion. No "
                    "unverified use-case claims.",
        req_concepts_any=["recipient", "occasion"], req_real_assets=["LIFESTYLE"],
        eligibility_rule="verified recipient OR occasion + a real lifestyle asset"),
    DynamicSpec(
        "PRODUCT_COMPARISON",
        purpose="Compare against real, named products on verified attributes.",
        headline="How It Compares",
        asset_brief="A comparison table of REAL products and verified attributes. No fabricated "
                    "superiority claims.",
        req_facts_all=["comparison_products", "comparison_attributes"],
        eligibility_rule="verified real comparison products + verified comparison attributes"),
    DynamicSpec(
        "GARMENT_OPTIONS",
        purpose="Show the actual alternative garment options available.",
        headline="Garment Options",
        asset_brief="Real photos of each available garment option. Only options that actually exist.",
        req_facts_all=["garment_options"], req_real_assets=["GARMENT_OPTIONS"],
        eligibility_rule="verified available garment options + matching real assets"),
    DynamicSpec(
        "DESIGN_VARIATIONS",
        purpose="Show the real design/thread/placement/colour variations available.",
        headline="Design Variations",
        asset_brief="Real photos of the available thread colours / placements / design variations.",
        req_facts_any=["thread_colors", "design_dimensions", "placement", "color_options"],
        req_real_assets=["DESIGN_VARIATIONS"],
        eligibility_rule="verified available design/thread/placement/colour variations + matching assets"),
    DynamicSpec(
        "PRODUCTION_PROCESS",
        purpose="Show the verified production process with real evidence.",
        headline="How It's Made",
        asset_brief="REAL production/process photos. AI-generated process imagery may NOT be used as "
                    "proof.",
        req_concepts_any=["production_location", "production_time"], req_real_assets=["PRODUCTION_PROCESS"],
        eligibility_rule="verified process facts + real (non-AI) process evidence"),
    DynamicSpec(
        "GIFT_PRESENTATION",
        purpose="Show verified gift packaging or presentation.",
        headline="Ready to Gift",
        asset_brief="A real photo of the actual packaging/gift presentation. Do NOT imply a gift box or "
                    "packaging that is not supplied.",
        req_concepts_all=["packaging"], req_real_assets=["GIFT_PACKAGING"],
        eligibility_rule="verified packaging/gift presentation + a real packaging asset"),
    DynamicSpec(
        "BRAND_STORY",
        purpose="Tell a verified brand story — no generic filler.",
        headline="Our Story",
        asset_brief="Brand-authentic imagery. Only verified brand facts; no generic filler brand story.",
        req_facts_all=["brand"],
        eligibility_rule="verified brand facts"),
    DynamicSpec(
        "COMFORT_AND_DAILY_USE",
        purpose="Describe comfort/daily use ONLY from explicit comfort evidence.",
        headline="Comfort & Daily Use",
        asset_brief="Real wear imagery. Measurements alone do not prove comfort; no long-shift, all-day or "
                    "durability claims without explicit evidence.",
        req_concepts_all=["softness_comfort"],
        eligibility_rule="explicit verified softness/comfort evidence (never inferred from material or "
                          "measurements)"),
]
PREMIUM_POOL_KEYS = tuple(m.module_key for m in PREMIUM_DYNAMIC_POOL)
PREMIUM_BY_KEY = {m.module_key: m for m in PREMIUM_DYNAMIC_POOL}


def expected_module_count(capability):
    """The number of modules a capability's PUBLISHABLE payload targets (Basic count for UNKNOWN)."""
    capability = validate_capability(capability)
    if capability == NO_A_PLUS:
        return 0
    if capability == PREMIUM_A_PLUS:
        return PREMIUM_MODULE_COUNT
    return BASIC_MODULE_COUNT
