#!/usr/bin/env python3
"""ACT-006 — typed product-fact loading. Unknown facts stay null and raise OWNER_FACT_REQUIRED;
missing/malformed/invalid files are distinguished; the normalized output is deterministic."""
import json, os, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "listing") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "listing"))

import product_fact_loader as PFL


def write_facts(folder, product, extra=None, name="product-facts.json"):
    doc = {"schema_version": "1.0.0", "source": "owner", "product": product}
    if extra:
        doc.update(extra)
    p = os.path.join(folder, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    return p


class LoadingCases(unittest.TestCase):
    def test_valid_file_loads(self):
        d = tempfile.mkdtemp()
        write_facts(d, {"brand": {"value": "Acme", "status": "VERIFIED"}})
        f = PFL.load_product_facts(folder=d)
        self.assertEqual(f.state("brand"), PFL.VERIFIED)
        self.assertEqual(f.publishable_value("brand"), "Acme")
        self.assertIsNotNone(f.source_sha256)

    def test_missing_optional_file_produces_warning(self):
        d = tempfile.mkdtemp()
        f = PFL.load_product_facts(folder=d)                     # no file at all
        self.assertIsNone(f.source_sha256)
        self.assertTrue(any("absent" in w for w in f.warnings))
        self.assertEqual(f.state("material"), PFL.UNKNOWN)

    def test_missing_required_file_fails_clearly(self):
        d = tempfile.mkdtemp()
        with self.assertRaises(PFL.MissingRequiredProductFactsError):
            PFL.load_product_facts(folder=d, required=True)

    def test_malformed_file_reports_path_line_column(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "product-facts.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{\n  "product": { "brand": }\n}')          # invalid value
        with self.assertRaises(PFL.MalformedProductFactsError) as cm:
            PFL.load_product_facts(path=p)
        e = cm.exception
        self.assertEqual(e.path, p)
        self.assertIsInstance(e.line, int)
        self.assertIsInstance(e.column, int)
        self.assertIn("product-facts", str(e))

    def test_malformed_is_not_silently_empty(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "product-facts.json")
        open(p, "w").write("{ broken")
        with self.assertRaises(PFL.MalformedProductFactsError):
            PFL.load_product_facts(path=p)                        # must raise, never return empty facts

    def test_invalid_schema_fails_clearly(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "product-facts.json")
        json.dump(["not", "an", "object"], open(p, "w"))
        with self.assertRaises(PFL.InvalidProductFactsSchemaError):
            PFL.load_product_facts(path=p)

    def test_invalid_product_block_type_fails_clearly(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "product-facts.json")
        json.dump({"product": "should be an object"}, open(p, "w"))
        with self.assertRaises(PFL.InvalidProductFactsSchemaError):
            PFL.load_product_facts(path=p)


class FieldStates(unittest.TestCase):
    def test_absent_material_remains_null(self):
        d = tempfile.mkdtemp()
        write_facts(d, {"brand": {"value": "Acme", "status": "VERIFIED"}})   # no material key
        f = PFL.load_product_facts(folder=d)
        self.assertEqual(f.state("material"), PFL.UNKNOWN)
        self.assertIsNone(f.get("material")["value"])

    def test_explicit_null_material_remains_null(self):
        d = tempfile.mkdtemp()
        write_facts(d, {"material": None})
        f = PFL.load_product_facts(folder=d)
        self.assertEqual(f.state("material"), PFL.UNKNOWN)
        self.assertIsNone(f.publishable_value("material"))

    def test_comma_and_list_fields_normalize_safely(self):
        d = tempfile.mkdtemp()
        write_facts(d, {"color_options": "black, navy , maroon",
                        "size_range": ["S", "M", "L"]})
        f = PFL.load_product_facts(folder=d)
        self.assertEqual(f.get("color_options")["value"], ["black", "navy", "maroon"])
        self.assertEqual(f.get("size_range")["value"], ["S", "M", "L"])

    def test_unknown_facts_produce_owner_fact_required(self):
        d = tempfile.mkdtemp()
        write_facts(d, {"brand": {"value": "Acme", "status": "VERIFIED"}})
        f = PFL.load_product_facts(folder=d)
        self.assertIn("material", f.owner_fact_required)
        self.assertIn("production_time_range", f.owner_fact_required)
        self.assertNotIn("brand", f.owner_fact_required)
        self.assertEqual(f.unknown_fact_count, len(f.owner_fact_required))

    def test_verified_facts_remain_usable(self):
        d = tempfile.mkdtemp()
        write_facts(d, {"material": {"value": "80% cotton 20% polyester", "status": "OWNER_CONFIRMED"}})
        f = PFL.load_product_facts(folder=d)
        self.assertEqual(f.state("material"), PFL.OWNER_CONFIRMED)
        self.assertEqual(f.publishable_value("material"), "80% cotton 20% polyester")

    def test_owner_review_facts_remain_non_publishable(self):
        d = tempfile.mkdtemp()
        write_facts(d, {"material": {"value": "cotton", "status": "OWNER_REVIEW_REQUIRED"},
                        "fit": "relaxed"})                        # bare value -> owner-review
        f = PFL.load_product_facts(folder=d)
        self.assertEqual(f.state("material"), PFL.OWNER_REVIEW_REQUIRED)
        self.assertIsNone(f.publishable_value("material"))       # present but not publishable
        self.assertEqual(f.state("fit"), PFL.OWNER_REVIEW_REQUIRED)
        self.assertIsNone(f.publishable_value("fit"))

    def test_blocked_fact_is_counted_and_non_publishable(self):
        d = tempfile.mkdtemp()
        write_facts(d, {"occasion": {"value": "halloween", "status": "BLOCKED"}})
        f = PFL.load_product_facts(folder=d)
        self.assertEqual(f.state("occasion"), PFL.BLOCKED)
        self.assertEqual(f.blocked_fact_count, 1)
        self.assertIsNone(f.publishable_value("occasion"))


class Determinism(unittest.TestCase):
    def _facts(self, folder):
        write_facts(folder, {"brand": {"value": "Acme", "status": "VERIFIED"},
                             "material": None, "color_options": "black, navy",
                             "fit": {"value": "relaxed", "status": "OWNER_REVIEW_REQUIRED"}})
        return PFL.load_product_facts(folder=folder)

    def test_normalized_output_is_deterministic(self):
        d = tempfile.mkdtemp()
        a = self._facts(d).content_sha256()
        b = PFL.load_product_facts(folder=d).content_sha256()
        self.assertEqual(a, b)

    def test_content_hash_excludes_volatile_generated_at(self):
        d = tempfile.mkdtemp()
        f = self._facts(d)
        d1 = f.to_dict(generated_at="2026-01-01T00:00:00Z")
        d2 = f.to_dict(generated_at="2026-12-31T23:59:59Z")
        self.assertNotEqual(d1["generated_at"], d2["generated_at"])
        self.assertEqual(d1["content_sha256"], d2["content_sha256"])

    def test_source_sha256_is_stable(self):
        d = tempfile.mkdtemp()
        f1 = self._facts(d)
        f2 = PFL.load_product_facts(folder=d)
        self.assertEqual(f1.source_sha256, f2.source_sha256)

    def test_write_normalized_artifact_roundtrips(self):
        d = tempfile.mkdtemp()
        f = self._facts(d)
        path, _ = PFL.write_normalized_product_facts(d, f, generated_at="2026-07-16T00:00:00Z")
        doc = json.load(open(path, encoding="utf-8"))
        self.assertEqual(doc["content_sha256"], f.content_sha256())
        self.assertEqual(doc["source_sha256"], f.source_sha256)
        self.assertEqual(doc["verified_fact_count"], f.verified_fact_count)
        self.assertIn("material", doc["owner_fact_required"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
