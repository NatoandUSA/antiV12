#!/usr/bin/env python3
"""Regression tests for the 3 real bugs the end-to-end pilot found (v2.4.0)."""
import os, sys, json, tempfile, subprocess, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("compliance", "listing", "research"):
    sys.path.insert(0, os.path.join(ROOT, d))
import ip_guard


class IPGuardNotCryWolf(unittest.TestCase):
    def test_normal_listing_is_ok(self):
        txt = ("Personalized Nurse Sweatshirt Embroidered Crewneck with real machine embroidery, "
               "cotton-blend fabric, made to order, satin stitches you can actually see")
        self.assertEqual(ip_guard.screen(txt)["verdict"], "OK")

    def test_unknown_tokens_still_listed(self):
        r = ip_guard.screen("Zorptastic nurse sweatshirt")
        self.assertEqual(r["verdict"], "OK")            # unknown word doesn't fail the verdict
        self.assertIn("zorptastic", [u.lower() for u in r["unknown"]])  # but is surfaced

    def test_real_brand_still_blocks(self):
        self.assertEqual(ip_guard.screen("Disney Mickey Mouse Nurse Sweatshirt")["verdict"], "BLOCK")

    def test_curated_review_phrase_still_reviews(self):
        # a named enforced phrase should still drive REVIEW if present in the library
        import re
        phrases = getattr(ip_guard, "REVIEW_PHRASES", {})
        if phrases:
            ph = next(iter(phrases))
            self.assertEqual(ip_guard.screen(f"nurse {ph} sweatshirt")["verdict"], "REVIEW")


class ValidatorHandlesAplusDicts(unittest.TestCase):
    def test_aplus_as_dicts_does_not_crash(self):
        d = tempfile.mkdtemp()
        json.dump({"category": "apparel",
                   "title": "Personalized Nurse Sweatshirt Embroidered Crewneck Name",
                   "bullets": ["Custom embroidered name", "Soft crewneck", "Made to order", "Nurse gift", "Real embroidery"],
                   "backend": "nurse embroidered personalized custom crewneck name women rn",
                   "aplus": [{"headline": "See the Real Stitching", "copy": "Raised satin stitch, not a print."}]},
                  open(os.path.join(d, "listing.json"), "w"))
        p = subprocess.run([sys.executable, os.path.join(ROOT, "compliance", "listing_validate.py"),
                            os.path.join(d, "listing.json")], capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.assertNotIn("Traceback", p.stdout + p.stderr)
        self.assertIn("LISTING VALIDATION", p.stdout + p.stderr)


class BackendDedup(unittest.TestCase):
    def test_backend_excludes_title_words(self):
        import listing_generator as LG
        title = "Custom Nurse Gift Crewneck Sweatshirt Embroidered"
        bk = LG.build_backend(["custom nurse gift", "embroidered crewneck", "rn graduation women"], {}, title=title)
        for w in ("custom", "nurse", "gift", "crewneck", "sweatshirt", "embroidered"):
            self.assertNotIn(w, bk.split())
        self.assertIn("graduation", bk.split())          # non-title keyword survives


if __name__ == "__main__":
    unittest.main(verbosity=2)
