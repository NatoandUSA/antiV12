#!/usr/bin/env python3
"""Acrylic listing safety — the claim vocabulary and the A+ gate.

The owner sells acrylic today and the toolkit had no acrylic support at all: `grep -rli acrylic`
returned nothing repo-wide. Measured on 2026-08-02, before this file existed, a draft reading

    "Shatterproof acrylic keychain, unbreakable and will not yellow, dishwasher safe"

tripped ZERO unsafe-claim checks and audited PUBLISHABLE.

Every phrase guarded here is a DURABILITY OR SAFETY claim about a brittle plastic. Acrylic cracks
on impact, yellows under UV, scratches more easily than glass, and deforms below dishwasher
temperatures. "Food safe" and "BPA free" are regulatory claims requiring documentation a print
supplier does not provide. These are the claims that get a listing pulled, and the ones with a
plausible injury path.

Nothing here forbids the language outright: a VERIFIED claim still publishes it. What is fixed is
acrylic wording passing UNCHECKED because nobody had written it down.

Offline. No network, no Amazon call, no Seller Central path.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "listing")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import page_auditor as PA                                              # noqa: E402
import a_plus_templates as APT                                         # noqa: E402
from listing import category_policy_registry as CPR                    # noqa: E402


def _listing(category, title="", bullets=(), description=""):
    return {"category": category, "title": title,
            "bullets": list(bullets), "description": description}


def _unsupported(result):
    return [h["message"] for h in result["hard_failures"]
            if "unsupported claim" in h["message"]]


class AcrylicCategoryPolicy(unittest.TestCase):
    def test_acrylic_resolves_to_a_real_policy_not_the_fallback(self):
        """Before this, acrylic resolved through the fallback with POLICY_VERIFICATION_REQUIRED.
        The limits were never the danger -- they are the same Amazon rule -- but a category that
        silently falls back is one nobody has looked at."""
        policy = CPR.load_policy_registry().resolve("acrylic")
        self.assertEqual(policy.product_category, "acrylic")
        self.assertFalse(policy.fallback_used)

    def test_the_policy_record_says_why_the_limits_match_the_others(self):
        """The limits being identical to apparel is deliberate and could otherwise read as a
        copy-paste. The record has to say so, or the next reader 'fixes' it."""
        policy = CPR.load_policy_registry().resolve("acrylic")
        self.assertIn("CLAIM", policy.policy_source)


class AcrylicClaimVocabulary(unittest.TestCase):
    # Each is a real failure mode of acrylic, not a marketing adjective.
    DURABILITY = ("shatterproof", "unbreakable", "will not crack", "impact resistant")
    OPTICAL = ("will not yellow", "uv proof", "non yellowing")
    SURFACE = ("scratch proof", "scratch resistant")
    HEAT = ("dishwasher safe", "microwave safe", "heat resistant")
    REGULATORY = ("food safe", "bpa free", "non toxic", "child safe")
    SUBSTITUTION = ("real glass", "tempered")

    def _blocked(self, phrase, category="acrylic"):
        r = PA.audit_listing(_listing(category, title=f"Acrylic item {phrase}"))
        return any(phrase in m for m in _unsupported(r))

    def test_every_guarded_acrylic_claim_class_is_blocked(self):
        for group, phrases in (("durability", self.DURABILITY), ("optical", self.OPTICAL),
                               ("surface", self.SURFACE), ("heat", self.HEAT),
                               ("regulatory", self.REGULATORY),
                               ("substitution", self.SUBSTITUTION)):
            for phrase in phrases:
                with self.subTest(group=group, phrase=phrase):
                    self.assertTrue(self._blocked(phrase),
                                    f"{phrase!r} published unchecked on an acrylic listing")

    def test_the_original_reported_draft_is_now_blocked_unsafe(self):
        """The exact wording measured as PUBLISHABLE before this change."""
        r = PA.audit_listing(_listing(
            "acrylic", title="Shatterproof Acrylic Keychain",
            bullets=["Unbreakable and will not yellow", "Dishwasher safe and food safe"],
            description="Crystal clear acrylic."))
        self.assertEqual(r["publishability"], "BLOCKED_UNSAFE")
        self.assertGreaterEqual(len(_unsupported(r)), 5)

    def test_the_shared_vocabulary_still_applies_to_acrylic(self):
        """Additive, not a swap. An acrylic listing must still not claim 'made to last'. A category
        that REPLACED the shared list would quietly drop every protection the others rely on."""
        r = PA.audit_listing(_listing("acrylic", title="Made to last acrylic sign",
                                      bullets=["Soft and comfortable"]))
        joined = " ".join(_unsupported(r))
        self.assertIn("made to last", joined)
        self.assertIn("soft and comfortable", joined)
        self.assertLessEqual(set(PA.UNSAFE_CLAIM_PHRASES),
                             set(PA.unsafe_claim_phrases_for("acrylic")))

    def test_other_categories_are_not_affected(self):
        """The change must be surgical. Acrylic wording on an apparel listing is not an acrylic
        risk, and widening the shared list would have broken three live categories at once."""
        for cat in ("apparel", "pod", "jewelry"):
            with self.subTest(category=cat):
                r = PA.audit_listing(_listing(
                    cat, title="Shatterproof item", bullets=["Dishwasher safe"]))
                self.assertEqual(_unsupported(r), [])

    def test_an_unknown_category_gets_the_shared_vocabulary_and_no_crash(self):
        r = PA.audit_listing(_listing("tumbler", title="Made to last tumbler"))
        self.assertTrue(any("made to last" in m for m in _unsupported(r)))

    def _dishwasher_failures(self, evidence_block=None):
        listing = _listing("acrylic", title="Acrylic tray dishwasher safe")
        if evidence_block is not None:
            listing["claim_evidence"] = evidence_block
        return [m for m in _unsupported(PA.audit_listing(listing)) if "dishwasher" in m]

    def test_a_verified_claim_publishes_the_phrase_and_an_unrelated_one_does_not(self):
        """This is a check on EVIDENCE, not a banned-words list. If the owner can verify it, it
        publishes -- otherwise the gate would only teach people to route around it.

        The important half is the third assertion. A reviewer asked whether VERIFIED is a
        category-wide bypass: does holding *any* verified claim unlock *every* guarded phrase?
        Measured, it does not -- the match is against the verified claim TEXT, so evidence for
        "made in vietnam" leaves "dishwasher safe" blocked.

        An earlier version of this test set `verified_claim_texts`, which the auditor does not
        read, and asserted only that the result was a list. It passed while proving nothing.
        """
        self.assertIn("dishwasher safe", PA.unsafe_claim_phrases_for("acrylic"))
        self.assertTrue(self._dishwasher_failures(),
                        "with no evidence the phrase must be blocked")
        self.assertEqual(self._dishwasher_failures({"verified": [{"text": "dishwasher safe"}]}), [],
                         "a VERIFIED claim naming the phrase must publish it")
        self.assertTrue(self._dishwasher_failures({"verified": [{"text": "made in vietnam"}]}),
                        "an UNRELATED verified claim must not unlock a guarded acrylic phrase")


class AcrylicAPlusIsWithheld(unittest.TestCase):
    """The 6 A+ modules are embroidered-apparel only. Rendered for acrylic they would assert
    embroidery, thread, fabric and fit the product does not have -- a fabricated claim in
    publishable A+ copy, which is worse than having no A+ at all."""

    def test_acrylic_gets_no_embroidery_modules(self):
        self.assertEqual(APT.select_modules([], category="acrylic"), [])

    def test_the_refusal_states_a_reason_the_owner_can_act_on(self):
        supported, reason = APT.templates_supported_for("acrylic")
        self.assertFalse(supported)
        self.assertIn("acrylic", reason)
        self.assertIn("embroider", reason.lower())

    def test_an_unknown_category_fails_closed(self):
        """A new category must not inherit the embroidery set by default."""
        self.assertEqual(APT.select_modules([], category="ceramic"), [])
        self.assertFalse(APT.templates_supported_for("ceramic")[0])

    def test_apparel_and_pod_still_get_their_modules(self):
        for cat in ("apparel", "pod"):
            with self.subTest(category=cat):
                self.assertTrue(APT.select_modules([], category=cat))

    def test_existing_callers_are_unchanged(self):
        """`category` is optional. Callers that never passed one keep the historical behaviour,
        so this cannot silently empty A+ for the live apparel workflow."""
        self.assertTrue(APT.select_modules([]))
        self.assertEqual(APT.select_modules([]), APT.select_modules([], category=None))


if __name__ == "__main__":
    unittest.main()
