#!/usr/bin/env python3
"""Unit tests for listing.keyword_source_adapter — the shared authoritative keyword source.

Covers ACT-001 (one source for every consumer), ACT-002 (dashboard uses it too) and ACT-003
(malformed authoritative JSON hard fails instead of silently becoming "no data").
"""
import json, os, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "listing") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "listing"))

import keyword_source_adapter as KSA


def lean_kw(keyword, tier="A_CORE", **extra):
    r = {"keyword_exact": keyword, "keyword_normalized": keyword.lower(), "tier": tier,
         "owner_status": "auto", "reason_codes": ["AUDIENCE_MATCH"], "risk_flags": [],
         "search_volume": 100, "simple_competitor_coverage_pct": 50.0, "top_20_coverage_pct": 10.0,
         "best_rank": 10, "median_rank": 20, "batch_support": {"B1_CORE_MONEY": 5},
         "observation_count": 1}
    r.update(extra)
    return r


def lean_doc(records, run_id="kwrun_test"):
    counts = {}
    for r in records:
        counts[r["tier"]] = counts.get(r["tier"], 0) + 1
    return {"run_metadata": {"run_id": run_id, "tool_version": "test", "schema_version": "1.0.0",
                             "phase": "Phase 1 - lean relevance engine"},
            "target_profile": {"seed": "personalized nurse sweatshirt"},
            "quality_summary": {"counts": counts, "total_keywords": len(records)},
            "downstream_policy": {"mode": "transition", "lean_master_status": "available"},
            "keywords": records}


def prod_kw(keyword, tier="TIER_A_EXACT_MONEY", **extra):
    r = lean_kw(keyword, tier)
    r.update(extra)
    return r


def prod_doc(records, run_id="prodrun_test"):
    counts = {}
    for r in records:
        counts[r["tier"]] = counts.get(r["tier"], 0) + 1
    return {"run_metadata": {"run_id": run_id, "schema_version": "1.0.0"},
            "quality_summary": {"counts": counts},
            "keywords": records}


def legacy_doc(records=None):
    records = records or [{"keyword": "nurse sweatshirt", "score": 99, "sv": 4663,
                           "confidence": "MEDIUM", "reason_codes": ["high_search_volume"],
                           "why": "high search volume"}]
    return {"schema_version": "2.4", "count": len(records), "top": records, "all": records}


def write(folder, name, doc):
    p = os.path.join(folder, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)


class SourcePriority(Base):
    def test_production_source_beats_lean(self):
        write(self.d, "MASTER-KEYWORDS.json", prod_doc([prod_kw("personalized nurse sweatshirt")]))
        write(self.d, "MASTER-KEYWORDS-LEAN.json", lean_doc([lean_kw("nurse sweater")]))
        src = KSA.load_keyword_source(self.d)
        self.assertEqual(src.source_file, "MASTER-KEYWORDS.json")
        self.assertEqual(src.source_schema, KSA.SCHEMA_PRODUCTION)
        self.assertEqual(src.source_mode, KSA.MODE_PRODUCTION)

    def test_lean_source_beats_legacy(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json", lean_doc([lean_kw("personalized nurse sweatshirt")]))
        write(self.d, "KEYWORD-INTELLIGENCE.json", legacy_doc())
        src = KSA.load_keyword_source(self.d, allow_legacy_unsafe=True)
        self.assertEqual(src.source_file, "MASTER-KEYWORDS-LEAN.json")
        self.assertEqual(src.source_mode, KSA.MODE_LEAN)
        self.assertFalse(src.legacy_unsafe)

    def test_legacy_is_blocked_unless_enabled(self):
        write(self.d, "KEYWORD-INTELLIGENCE.json", legacy_doc())
        with self.assertRaises(KSA.NoKeywordSourceError):
            KSA.load_keyword_source(self.d)
        src = KSA.load_keyword_source(self.d, allow_legacy_unsafe=True)
        self.assertEqual(src.source_mode, KSA.MODE_LEGACY_UNSAFE)
        self.assertTrue(src.legacy_unsafe)

    def test_legacy_records_keep_unverified_source_warning(self):
        write(self.d, "KEYWORD-INTELLIGENCE.json", legacy_doc())
        src = KSA.load_keyword_source(self.d, allow_legacy_unsafe=True)
        self.assertIn(KSA.WARN_LEGACY_UNVERIFIED, src.warnings)
        self.assertIn(KSA.WARN_LEGACY_UNVERIFIED, src.keywords[0]["warnings"])

    def test_no_source_at_all_raises(self):
        with self.assertRaises(KSA.NoKeywordSourceError):
            KSA.load_keyword_source(self.d)


class MalformedSource(Base):
    def test_malformed_authoritative_source_never_falls_back_to_legacy(self):
        p = os.path.join(self.d, "MASTER-KEYWORDS-LEAN.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"run_metadata": {"run_id": "x"}, "keywords": [ {"keyword_exact": "a",, } ]}')
        write(self.d, "KEYWORD-INTELLIGENCE.json", legacy_doc())
        with self.assertRaises(KSA.MalformedKeywordSourceError):
            KSA.load_keyword_source(self.d, allow_legacy_unsafe=True)   # legacy must NOT be selected

    def test_malformed_error_reports_path_line_and_column(self):
        p = os.path.join(self.d, "MASTER-KEYWORDS-LEAN.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{\n  "keywords": [],\n  "bad": ,\n}')
        with self.assertRaises(KSA.MalformedKeywordSourceError) as cm:
            KSA.load_keyword_source(self.d)
        e = cm.exception
        self.assertEqual(e.path, p)
        self.assertEqual(e.line, 3)
        self.assertIsInstance(e.column, int)
        self.assertTrue(e.parser_message)
        for part in (p, "line 3", "column"):
            self.assertIn(str(part), str(e))

    def test_malformed_production_does_not_fall_back_to_valid_lean(self):
        with open(os.path.join(self.d, "MASTER-KEYWORDS.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        write(self.d, "MASTER-KEYWORDS-LEAN.json", lean_doc([lean_kw("nurse sweatshirt")]))
        with self.assertRaises(KSA.MalformedKeywordSourceError):
            KSA.load_keyword_source(self.d)


class TierMapping(Base):
    def _tier_of(self, keyword, records):
        write(self.d, "MASTER-KEYWORDS-LEAN.json", lean_doc(records))
        src = KSA.load_keyword_source(self.d)
        return {r["keyword_normalized"]: r for r in src.keywords}[keyword]["normalized_tier"]

    def test_A_CORE_maps_to_TIER_A(self):
        self.assertEqual(self._tier_of("a", [lean_kw("a", "A_CORE")]), KSA.TIER_A)

    def test_B_SUPPORTING_maps_to_TIER_B(self):
        self.assertEqual(self._tier_of("b", [lean_kw("b", "B_SUPPORTING")]), KSA.TIER_B)

    def test_REVIEW_stays_REVIEW(self):
        self.assertEqual(self._tier_of("c", [lean_kw("c", "REVIEW")]), KSA.REVIEW)

    def test_REJECTED_stays_REJECTED(self):
        self.assertEqual(self._tier_of("d", [lean_kw("d", "REJECTED")]), KSA.REJECTED)

    def test_production_tier_vocabulary_maps(self):
        records = [prod_kw("a", "TIER_A_EXACT_MONEY"), prod_kw("b", "TIER_B_CORE_NICHE"),
                   prod_kw("c", "TIER_C_SUPPORTING_LONG_TAIL"), prod_kw("d", "OUTLIER_OPPORTUNITY"),
                   prod_kw("e", "RESIDUE_RELEVANT"), prod_kw("f", "REJECTED_NOISE")]
        write(self.d, "MASTER-KEYWORDS.json", prod_doc(records))
        src = KSA.load_keyword_source(self.d)
        got = {r["keyword_normalized"]: r["normalized_tier"] for r in src.keywords}
        self.assertEqual(got, {"a": KSA.TIER_A, "b": KSA.TIER_B, "c": KSA.TIER_C,
                               "d": KSA.OUTLIER, "e": KSA.RESIDUE, "f": KSA.REJECTED})

    def test_legacy_C_maps_to_TIER_C(self):
        write(self.d, "KEYWORD-INTELLIGENCE.json",
              legacy_doc([{"keyword": "nurse gift", "tier": "C", "sv": 100}]))
        src = KSA.load_keyword_source(self.d, allow_legacy_unsafe=True)
        self.assertEqual(src.keywords[0]["normalized_tier"], KSA.TIER_C)

    def test_unknown_legacy_tier_maps_to_REVIEW(self):
        write(self.d, "KEYWORD-INTELLIGENCE.json",
              legacy_doc([{"keyword": "nurse gift", "tier": "ZZZ", "sv": 100}]))
        src = KSA.load_keyword_source(self.d, allow_legacy_unsafe=True)
        self.assertEqual(src.keywords[0]["normalized_tier"], KSA.REVIEW)

    def test_legacy_record_without_tier_maps_to_REVIEW(self):
        write(self.d, "KEYWORD-INTELLIGENCE.json", legacy_doc())
        src = KSA.load_keyword_source(self.d, allow_legacy_unsafe=True)
        self.assertEqual(src.keywords[0]["normalized_tier"], KSA.REVIEW)


class SchemaDetection(Base):
    def test_normal_lean_file_detected(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json",
              lean_doc([lean_kw("a", "A_CORE"), lean_kw("b", "B_SUPPORTING"),
                        lean_kw("c", "REVIEW"), lean_kw("d", "REJECTED")]))
        self.assertEqual(KSA.load_keyword_source(self.d).source_schema, KSA.SCHEMA_LEAN)

    def test_all_B_SUPPORTING_lean_file_detected_as_lean(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json",
              lean_doc([lean_kw("a", "B_SUPPORTING"), lean_kw("b", "B_SUPPORTING")]))
        src = KSA.load_keyword_source(self.d)
        self.assertEqual(src.source_schema, KSA.SCHEMA_LEAN)
        self.assertEqual(src.tier_counts[KSA.TIER_B], 2)

    def test_all_REVIEW_lean_file_detected_as_lean(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json",
              lean_doc([lean_kw("a", "REVIEW"), lean_kw("b", "REVIEW")]))
        src = KSA.load_keyword_source(self.d)
        self.assertEqual(src.source_schema, KSA.SCHEMA_LEAN)
        self.assertEqual(src.tier_counts[KSA.REVIEW], 2)

    def test_all_REJECTED_lean_file_detected_as_lean(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json", lean_doc([lean_kw("a", "REJECTED")]))
        self.assertEqual(KSA.load_keyword_source(self.d).source_schema, KSA.SCHEMA_LEAN)

    def test_empty_valid_lean_file_detected_as_lean(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json", lean_doc([]))
        src = KSA.load_keyword_source(self.d)
        self.assertEqual(src.source_schema, KSA.SCHEMA_LEAN)
        self.assertEqual(src.keywords, [])
        self.assertEqual(src.eligibility_counts["total"], 0)

    def test_lean_is_not_detected_from_a_single_A_CORE_record(self):
        """A file mixing one lean token with production tokens must be rejected, not called lean."""
        doc = lean_doc([lean_kw("a", "A_CORE"), lean_kw("b", "TIER_A_EXACT_MONEY")])
        write(self.d, "MASTER-KEYWORDS-LEAN.json", doc)
        with self.assertRaises(KSA.SchemaConflictError):
            KSA.load_keyword_source(self.d)

    def test_mixed_incompatible_tier_vocabulary_fails(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json",
              lean_doc([lean_kw("a", "A_CORE"), lean_kw("b", "RESIDUE_RELEVANT")]))
        with self.assertRaises(KSA.SchemaConflictError) as cm:
            KSA.load_keyword_source(self.d)
        self.assertIn("tier vocabulary", str(cm.exception))

    def test_unknown_tier_token_in_lean_file_fails(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json", lean_doc([lean_kw("a", "MYSTERY_TIER")]))
        with self.assertRaises(KSA.SchemaConflictError):
            KSA.load_keyword_source(self.d)

    def test_declared_schema_conflicting_with_content_fails(self):
        doc = lean_doc([lean_kw("a", "A_CORE")])
        doc["source_schema"] = "production"
        write(self.d, "MASTER-KEYWORDS-LEAN.json", doc)
        with self.assertRaises(KSA.SchemaConflictError):
            KSA.load_keyword_source(self.d)

    def test_production_content_in_lean_slot_fails(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json", prod_doc([prod_kw("a")]))
        with self.assertRaises(KSA.SchemaConflictError):
            KSA.load_keyword_source(self.d)

    def test_observed_tier_vocabulary_is_complete(self):
        doc = lean_doc([lean_kw("a", "A_CORE"), lean_kw("b", "REJECTED"), lean_kw("c", "REVIEW")])
        self.assertEqual(KSA.observed_tier_vocabulary(doc), {"A_CORE", "REJECTED", "REVIEW"})


class Normalization(Base):
    def test_comma_formatted_numbers_parse(self):
        self.assertEqual(KSA.parse_number("1,200")[0], 1200.0)
        self.assertEqual(KSA.parse_number("1,234,567")[0], 1234567.0)

    def test_comma_formatted_search_volume_parses_in_a_record(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json",
              lean_doc([lean_kw("a", "A_CORE", search_volume="1,200")]))
        src = KSA.load_keyword_source(self.d)
        self.assertEqual(src.keywords[0]["search_volume"], 1200.0)

    def test_number_parsing_edge_cases(self):
        self.assertEqual(KSA.parse_number(42)[0], 42.0)
        self.assertEqual(KSA.parse_number(3.5)[0], 3.5)
        self.assertEqual(KSA.parse_number("45%")[0], 45.0)
        self.assertEqual(KSA.parse_number("")[0], None)
        self.assertEqual(KSA.parse_number(None)[0], None)
        self.assertEqual(KSA.parse_number("nan")[0], None)
        self.assertEqual(KSA.parse_number(float("nan"))[0], None)
        self.assertEqual(KSA.parse_number(float("inf"))[0], None)

    def test_invalid_number_warns_instead_of_crashing(self):
        value, warning = KSA.parse_number("not a number", "search_volume")
        self.assertIsNone(value)
        self.assertIn("search_volume_unparsable", warning)

    def test_invalid_number_in_record_warns_and_keeps_record(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json",
              lean_doc([lean_kw("a", "A_CORE", search_volume="banana")]))
        src = KSA.load_keyword_source(self.d)
        self.assertIsNone(src.keywords[0]["search_volume"])
        self.assertTrue(any("unparsable" in w for w in src.warnings))

    def test_phrase_normalization_nfkc_and_whitespace(self):
        # NFKC folds the full-width characters; internal whitespace collapses; display case survives.
        self.assertEqual(KSA.normalize_phrase("  Nurse　　Ｓweatshirt  "), "Nurse Sweatshirt")
        self.assertEqual(KSA.comparison_phrase("  Nurse   SWEATSHIRT "), "nurse sweatshirt")

    def test_display_phrase_is_preserved_and_comparison_phrase_is_lowercase(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json",
              lean_doc([lean_kw("Personalized  Nurse Sweatshirt", "A_CORE",
                                keyword_normalized="personalized nurse sweatshirt")]))
        r = KSA.load_keyword_source(self.d).keywords[0]
        self.assertEqual(r["keyword_exact"], "Personalized Nurse Sweatshirt")
        self.assertEqual(r["keyword_normalized"], "personalized nurse sweatshirt")


class RiskAndEligibility(Base):
    def test_each_blocking_risk_flag_makes_a_record_ineligible(self):
        for risk in KSA.BLOCKING_RISKS:
            with self.subTest(risk=risk):
                d = tempfile.mkdtemp()
                try:
                    write(d, "MASTER-KEYWORDS-LEAN.json",
                          lean_doc([lean_kw("a", "A_CORE", risk_flags=[risk])]))
                    r = KSA.load_keyword_source(d).keywords[0]
                    self.assertFalse(r["eligible"])
                    self.assertIn(f"BLOCKING_RISK:{risk}", r["exclusion_reasons"])
                finally:
                    shutil.rmtree(d, ignore_errors=True)

    def test_namespaced_lean_risk_flags_block(self):
        """The lean engine writes 'wrong_audience:teacher', not 'WRONG_AUDIENCE'."""
        write(self.d, "MASTER-KEYWORDS-LEAN.json",
              lean_doc([lean_kw("teacher sweatshirt", "A_CORE",
                                risk_flags=["wrong_audience:teacher", "wrong_product_family:shirt"])]))
        r = KSA.load_keyword_source(self.d).keywords[0]
        self.assertFalse(r["eligible"])
        self.assertEqual(r["blocking_risks"], ["WRONG_AUDIENCE", "WRONG_PRODUCT"])
        self.assertEqual(r["risk_flags"], ["wrong_audience:teacher", "wrong_product_family:shirt"])

    def test_rejected_never_becomes_eligible(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json",
              lean_doc([lean_kw("a", "REJECTED", owner_status="approved")]))
        r = KSA.load_keyword_source(self.d).keywords[0]
        self.assertFalse(r["eligible"])
        self.assertIn("REJECTED_TIER", r["exclusion_reasons"])

    def test_review_needs_approved_owner_status(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json",
              lean_doc([lean_kw("a", "REVIEW"), lean_kw("b", "REVIEW", owner_status="approved")]))
        by = {r["keyword_normalized"]: r for r in KSA.load_keyword_source(self.d).keywords}
        self.assertFalse(by["a"]["eligible"])
        self.assertIn("OWNER_APPROVAL_REQUIRED", by["a"]["exclusion_reasons"])
        self.assertTrue(by["b"]["eligible"])

    def test_review_outlier_residue_stay_out_of_automatic_allocation(self):
        records = [prod_kw("a", "TIER_A_EXACT_MONEY"), prod_kw("r", "RESIDUE_RELEVANT"),
                   prod_kw("o", "OUTLIER_OPPORTUNITY")]
        write(self.d, "MASTER-KEYWORDS.json", prod_doc(records))
        src = KSA.load_keyword_source(self.d)
        self.assertEqual(src.eligible_phrases(), ["a"])

    def test_approved_outlier_is_eligible_but_not_auto_allocated(self):
        write(self.d, "MASTER-KEYWORDS.json",
              prod_doc([prod_kw("o", "OUTLIER_OPPORTUNITY", owner_status="approved")]))
        src = KSA.load_keyword_source(self.d)
        self.assertTrue(src.keywords[0]["eligible"])
        self.assertEqual(src.eligible_phrases(), [])            # OUTLIER is not an allocatable tier
        self.assertEqual(src.eligible_phrases(tiers=(KSA.OUTLIER,)), ["o"])

    def test_empty_keyword_phrase_is_malformed_and_ineligible(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json", lean_doc([lean_kw("   ", "A_CORE")]))
        r = KSA.load_keyword_source(self.d).keywords[0]
        self.assertIn("MALFORMED", r["blocking_risks"])
        self.assertFalse(r["eligible"])

    def test_exclusions_are_grouped_by_reason(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json",
              lean_doc([lean_kw("a", "A_CORE"), lean_kw("b", "REJECTED"),
                        lean_kw("c", "A_CORE", risk_flags=["TRADEMARK"])]))
        ex = KSA.load_keyword_source(self.d).exclusions
        self.assertEqual(ex["REJECTED_TIER"]["keywords"], ["b"])
        self.assertEqual(ex["BLOCKING_RISK:TRADEMARK"]["keywords"], ["c"])


class DuplicateResolution(Base):
    def test_strongest_safe_duplicate_wins(self):
        weak = lean_kw("Nurse Sweatshirt", "REVIEW", keyword_normalized="nurse sweatshirt",
                       search_volume=None, simple_competitor_coverage_pct=None, best_rank=None,
                       batch_support={}, observation_count=0)
        strong = lean_kw("nurse sweatshirt", "A_CORE", keyword_normalized="nurse sweatshirt",
                         search_volume=4663, simple_competitor_coverage_pct=85.0, best_rank=3,
                         batch_support={"B1_CORE_MONEY": 9, "B2_CORE_OPPORTUNITY": 8})
        write(self.d, "MASTER-KEYWORDS-LEAN.json", lean_doc([weak, strong]))
        src = KSA.load_keyword_source(self.d)
        self.assertEqual(len(src.keywords), 1)
        win = src.keywords[0]
        # the whole strong record wins — a weak record is never patched with a better tier/volume
        self.assertEqual(win["normalized_tier"], KSA.TIER_A)
        self.assertEqual(win["keyword_exact"], "nurse sweatshirt")
        self.assertEqual(win["search_volume"], 4663)
        self.assertTrue(win["eligible"])

    def test_eligible_record_beats_ineligible_duplicate(self):
        blocked = lean_kw("x", "A_CORE", risk_flags=["TRADEMARK"], simple_competitor_coverage_pct=99.0)
        clean = lean_kw("x", "B_SUPPORTING", simple_competitor_coverage_pct=10.0)
        write(self.d, "MASTER-KEYWORDS-LEAN.json", lean_doc([blocked, clean]))
        win = KSA.load_keyword_source(self.d).keywords[0]
        self.assertTrue(win["eligible"])
        self.assertEqual(win["normalized_tier"], KSA.TIER_B)

    def test_rejected_duplicate_cannot_be_promoted(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json",
              lean_doc([lean_kw("x", "REJECTED"), lean_kw("x", "REJECTED", search_volume=9999)]))
        src = KSA.load_keyword_source(self.d)
        self.assertEqual(len(src.keywords), 1)
        self.assertFalse(src.keywords[0]["eligible"])

    def test_owner_approved_beats_auto_at_equal_tier(self):
        a = lean_kw("x", "REVIEW", owner_status="auto")
        b = lean_kw("x", "REVIEW", owner_status="approved")
        write(self.d, "MASTER-KEYWORDS-LEAN.json", lean_doc([a, b]))
        win = KSA.load_keyword_source(self.d).keywords[0]
        self.assertEqual(win["owner_status"], "approved")

    def test_duplicate_provenance_is_preserved(self):
        a = lean_kw("x", "REVIEW", search_volume=10)
        b = lean_kw("x", "A_CORE", search_volume=20)
        c = lean_kw("x", "B_SUPPORTING", search_volume=30)
        write(self.d, "MASTER-KEYWORDS-LEAN.json", lean_doc([a, b, c]))
        win = KSA.load_keyword_source(self.d).keywords[0]
        prov = win["duplicate_provenance"]
        self.assertEqual(len(prov), 3)                      # every duplicate source record kept
        self.assertEqual([p["source_tier"] for p in prov], ["REVIEW", "A_CORE", "B_SUPPORTING"])
        self.assertEqual([p["selected"] for p in prov], [False, True, False])
        self.assertEqual(sorted(p["search_volume"] for p in prov), [10.0, 20.0, 30.0])

    def test_non_duplicates_have_empty_provenance(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json", lean_doc([lean_kw("a"), lean_kw("b")]))
        for r in KSA.load_keyword_source(self.d).keywords:
            self.assertEqual(r["duplicate_provenance"], [])


class CanonicalOutput(Base):
    def _folder(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json",
              lean_doc([lean_kw("a", "A_CORE"), lean_kw("b", "B_SUPPORTING"),
                        lean_kw("c", "REVIEW"), lean_kw("d", "REJECTED")]))
        return self.d

    def test_normalized_artifact_contains_the_required_fields(self):
        path, src = KSA.write_normalized_source(self._folder())
        self.assertTrue(path.endswith(KSA.NORMALIZED_FILENAME))
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        for field in ("source_file", "source_schema", "source_run_id", "source_mode", "source_sha256",
                      "normalized_schema_version", "warnings", "keywords", "tier_counts",
                      "eligibility_counts", "exclusions", "content_sha256", "generated_at"):
            self.assertIn(field, doc)
        self.assertEqual(doc["source_schema"], KSA.SCHEMA_LEAN)
        self.assertEqual(doc["tier_counts"][KSA.TIER_A], 1)
        self.assertEqual(doc["eligibility_counts"], {"eligible": 2, "ineligible": 2, "total": 4})

    def test_identical_input_produces_identical_normalized_output(self):
        folder = self._folder()
        a = KSA.load_keyword_source(folder)
        b = KSA.load_keyword_source(folder)
        self.assertEqual(a.content_sha256(), b.content_sha256())
        self.assertEqual(KSA.canonical_json(a.canonical_content()),
                         KSA.canonical_json(b.canonical_content()))

    def test_content_hash_ignores_only_the_volatile_timestamp(self):
        folder = self._folder()
        src = KSA.load_keyword_source(folder)
        one = src.to_dict(generated_at="2020-01-01T00:00:00+00:00")
        two = src.to_dict(generated_at="2031-12-31T23:59:59+00:00")
        self.assertEqual(one["content_sha256"], two["content_sha256"])
        one.pop("generated_at"), two.pop("generated_at")
        self.assertEqual(one, two)

    def test_written_artifact_is_byte_identical_for_identical_input(self):
        folder = self._folder()
        stamp = "2026-07-16T00:00:00+00:00"
        p1, _ = KSA.write_normalized_source(folder, generated_at=stamp)
        with open(p1, "rb") as f:
            first = f.read()
        p2, _ = KSA.write_normalized_source(folder, generated_at=stamp)
        with open(p2, "rb") as f:
            self.assertEqual(first, f.read())

    def test_source_sha256_is_the_hash_of_the_source_file(self):
        folder = self._folder()
        src = KSA.load_keyword_source(folder)
        self.assertEqual(src.source_sha256,
                         KSA.sha256_file(os.path.join(folder, "MASTER-KEYWORDS-LEAN.json")))

    def test_listing_metadata_block(self):
        src = KSA.load_keyword_source(self._folder())
        meta = src.listing_metadata()
        self.assertEqual(meta["keyword_source_file"], "MASTER-KEYWORDS-LEAN.json")
        self.assertEqual(meta["keyword_source_schema"], KSA.SCHEMA_LEAN)
        self.assertEqual(meta["keyword_source_run_id"], "kwrun_test")
        self.assertEqual(meta["keyword_source_mode"], KSA.MODE_LEAN)
        self.assertEqual(meta["keyword_source_sha256"], src.source_sha256)
        self.assertFalse(meta["legacy_unsafe"])


class PreservedEvidence(Base):
    def test_evidence_fields_are_preserved(self):
        rec = lean_kw("a", "A_CORE", search_volume=330, simple_competitor_coverage_pct=90.0,
                      top_20_coverage_pct=30.0, top_50_coverage_pct=40.0, top_100_coverage_pct=50.0,
                      best_rank=2, median_rank=38,
                      batch_support={"B1_CORE_MONEY": 9, "B2_CORE_OPPORTUNITY": 9},
                      observation_count=2, reason_codes=["EXACT_INTENT"], owner_status="auto",
                      source_files=["US_AMAZON_cerebro_B0F1TMKKDL_2026-07-16.xlsx"],
                      source_batches=["B1_CORE_MONEY"])
        write(self.d, "MASTER-KEYWORDS-LEAN.json", lean_doc([rec]))
        r = KSA.load_keyword_source(self.d).keywords[0]
        self.assertEqual(r["search_volume"], 330.0)
        self.assertEqual(r["competitor_coverage"], 90.0)
        self.assertEqual(r["top_20_coverage"], 30.0)
        self.assertEqual(r["top_50_coverage"], 40.0)
        self.assertEqual(r["top_100_coverage"], 50.0)
        self.assertEqual(r["best_rank"], 2.0)
        self.assertEqual(r["median_rank"], 38.0)
        self.assertEqual(r["batch_support"], {"B1_CORE_MONEY": 9, "B2_CORE_OPPORTUNITY": 9})
        self.assertEqual(r["observation_count"], 2)
        self.assertEqual(r["reason_codes"], ["EXACT_INTENT"])
        self.assertEqual(r["owner_status"], "auto")
        self.assertEqual(r["source_batches"], ["B1_CORE_MONEY"])
        self.assertEqual(r["source_tier"], "A_CORE")
        self.assertEqual(r["source_schema"], KSA.SCHEMA_LEAN)
        self.assertEqual(r["source_file"], "MASTER-KEYWORDS-LEAN.json")
        self.assertEqual(r["source_run_id"], "kwrun_test")

    def test_stronger_tier_sorts_first(self):
        write(self.d, "MASTER-KEYWORDS-LEAN.json",
              lean_doc([lean_kw("weak", "B_SUPPORTING", simple_competitor_coverage_pct=5.0),
                        lean_kw("strong", "A_CORE", simple_competitor_coverage_pct=90.0)]))
        self.assertEqual(KSA.load_keyword_source(self.d).eligible_phrases(), ["strong", "weak"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
