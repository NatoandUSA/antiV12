#!/usr/bin/env python3
"""Creative Edge v2.2 regression tests. Run: python -m unittest tests.test_creative"""
import os, sys, json, tempfile, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "creative"))
sys.path.insert(0, os.path.join(ROOT, "core"))
import creative_edge as CE
import main_image_validator as MIV
import edge_lib as L
import creative_diagnosis as CD

def build(folder, **overrides):
    CE.example_brief(folder)
    p = os.path.join(folder, "creative-brief.json")
    with open(p) as f: brief = json.load(f)
    for k, v in overrides.items():
        if "." in k:
            a, b = k.split("."); brief.setdefault(a, {})[b] = v
        else:
            brief[k] = v
    with open(p, "w") as f: json.dump(brief, f)
    brief["__folder"] = folder
    return brief

class Creative(unittest.TestCase):
    def test_01_main_image_no_headline(self):
        b = build(tempfile.mkdtemp())
        sb, _, prompts = CE.build_storyboard(b)
        self.assertIsNone(sb["images"][0]["headline"])
        self.assertFalse(sb["images"][0]["text_allowed"])
        self.assertIsNone(sb["images"][0]["image_generation_prompt"])

    def test_02_main_concepts_have_no_macro_or_gift(self):
        b = build(tempfile.mkdtemp())
        m, _ = CE.build_visual_matrix(b)
        mi, _ = CE.build_main_image_edge(b, m)
        names = " ".join(c["name"].lower() for c in mi["concepts"])
        self.assertNotIn("macro", names); self.assertNotIn("gift", names); self.assertNotIn("split", names)

    def test_03_white_bg_is_amazon_baseline(self):
        b = build(tempfile.mkdtemp())
        m, _ = CE.build_visual_matrix(b)
        wb = [r for r in m["rows"] if r["key"] == "white_bg"][0]
        self.assertEqual(wb["classification"], L.AMAZON_BASELINE)
        self.assertNotIn("White background (main)", m["gaps_to_own_secondary"])

    def test_04_small_sample_low_confidence(self):
        b = build(tempfile.mkdtemp())
        b["competitors"] = b["competitors"][:4]
        m, _ = CE.build_visual_matrix(b)
        self.assertEqual(m["confidence"]["confidence"], "LOW")

    def test_05_duplicate_brands_reduce_effective(self):
        comps = [{"asin": f"A{i}", "brand": "SAME", "parent_group": f"p{i}", "top_seller": False, "visual": {}} for i in range(6)]
        conf = CE.competitor_confidence(comps)
        self.assertLessEqual(conf["effective_unique_sample"], 2)

    def test_06_missing_image_actual_score_incomplete(self):
        b = build(tempfile.mkdtemp())
        proof, _ = CE.build_embroidery_proof(b); cons, _ = CE.build_consistency_audit(b)
        a, _ = CE.build_actual_asset_score(b, proof, cons)
        self.assertEqual(a["status"], "INCOMPLETE")

    def test_07_ai_draft_not_misleading(self):
        b = build(tempfile.mkdtemp(), publication_intent=False)
        proof, _ = CE.build_embroidery_proof(b)
        self.assertEqual(proof["status"], L.DRAFT_UNVERIFIED)

    def test_08_ai_as_proof_is_misleading(self):
        b = build(tempfile.mkdtemp(), publication_intent=True)
        proof, _ = CE.build_embroidery_proof(b)
        self.assertEqual(proof["status"], L.MISLEADING)
        self.assertTrue(proof["publication_blocked"])

    def test_09_real_macro_is_proven(self):
        from PIL import Image
        import sys as _s; _s.path.insert(0, os.path.join(ROOT,"core")); import hashing
        d = tempfile.mkdtemp(); b = build(d); b["__folder"] = d
        p = os.path.join(d,"macro.png"); Image.new("RGB",(2000,2000),(20,90,70)).save(p)
        b["our_images"]["macro"] = "macro.png"
        b["product"]["supplier_sku"]="NS-001"; b["product"]["design_version"]="v3"; b["asset_reviewer"]="owner"
        # v2.3.3: a real file needs a hash-bound content review confirming visible embroidery
        b["embroidery_proof_review"] = {"reviewer":"owner","reviewed_at":"2026-07-15","asset_hash":hashing.file_sha256(p),
            "individual_threads_visible":True,"stitch_edges_visible":True,"fabric_weave_visible":True,
            "image_is_not_ai":True,"image_is_not_blank":True,"supplier_sku_matches":True,
            "design_version_matches":True,"thread_colors_match":True,"placement_matches":True}
        proof, _ = CE.build_embroidery_proof(b)
        self.assertEqual(proof["status"], L.PROVEN)
    def test_09c_real_macro_without_review_is_not_proven(self):
        from PIL import Image
        d = tempfile.mkdtemp(); b = build(d); b["__folder"] = d
        Image.new("RGB",(2000,2000),(20,90,70)).save(os.path.join(d,"macro.png"))
        b["our_images"]["macro"] = "macro.png"        # real file, NO content review
        proof, _ = CE.build_embroidery_proof(b)
        self.assertEqual(proof["status"], L.DRAFT_UNVERIFIED)
    def test_09b_macro_boolean_alone_is_not_proven(self):
        d = tempfile.mkdtemp(); b = build(d); b["__folder"] = d
        b["our_images"]["macro_real"] = True       # boolean statement, no asset
        proof, _ = CE.build_embroidery_proof(b)
        self.assertEqual(proof["status"], L.DRAFT_UNVERIFIED)

    def test_10_handmade_headline_flagged(self):
        self.assertTrue(CE.claim_check_headline("Handmade to Order, Just for Her"))
        self.assertTrue(CE.claim_check_headline("Real Embroidery, Made to Last"))
        self.assertFalse(CE.claim_check_headline("Made to Order"))
        self.assertFalse(CE.claim_check_headline("See the Stitch Detail"))

    def test_11_main_image_validator_blocks_text_and_props(self):
        r = MIV.validate({"slot_type": "AMAZON_MAIN_IMAGE", "headline": "Best Nurse Gift",
                          "composition": "product with a gift box and inset macro", "asset_path": "x.jpg"})
        self.assertEqual(r["status"], L.MI_BLOCKED)

    def test_12_zero_clicks_is_ctr_zero(self):
        diag = CD.diagnose({"impressions": 1000, "clicks": 0})
        self.assertTrue(any(d[0] == "LOW_CTR" for d in diag), "0 clicks on 1000 imp must read as CTR 0%, not missing")

    def test_13_zero_orders_is_conversion_zero(self):
        diag = CD.diagnose({"impressions": 1000, "clicks": 40, "sessions": 150, "orders": 0})
        self.assertTrue(any(d[0] == "STRONG_CTR_LOW_CONVERSION" for d in diag))

    def test_14_actual_asset_blocks_on_bad_proof(self):
        from PIL import Image
        d = tempfile.mkdtemp(); b = build(d, publication_intent=True); b["__folder"] = d
        # a REAL, decodable, adequately-sized image
        p = os.path.join(d, "main.png"); Image.new("RGB",(1200,1200),(20,90,70)).save(p)
        b["our_images"]["main"] = p
        b["product"]["supplier_sku"]="SKU1"; b["product"]["design_version"]="v1"; b["asset_reviewer"]="owner"
        # a structured, hash-matched thumbnail review with all components
        import sys as _s; _s.path.insert(0, os.path.join(ROOT,"core")); import hashing
        b["thumbnail_review"] = {"reviewer":"owner","reviewed_at":"2026-07-15T10:00:00",
            "asset_hash":hashing.file_sha256(p),"main_image_clarity":2,"thumbnail_clarity":2,
            "product_fill":2,"personalization_readable":2,"mobile_readable":2,"accuracy_verified":2}
        # v2.3.4: image specs must resolve to REAL current files whose hash matches asset_hash.
        s1 = os.path.join(d,"s1.png"); Image.new("RGB",(1200,1200),(20,90,71)).save(s1)
        s2 = os.path.join(d,"s2.png"); Image.new("RGB",(1200,1200),(20,90,72)).save(s2)
        b["image_specs"] = [{"image":1,"garment_type":"crewneck sweatshirt","personalization_example":"Sarah, RN","asset_path":"s1.png","asset_hash":hashing.file_sha256(s1),"reviewed_by":"owner","reviewed_at":"2026-07-15"},
                            {"image":2,"garment_type":"crewneck sweatshirt","personalization_example":"Sarah, RN","asset_path":"s2.png","asset_hash":hashing.file_sha256(s2),"reviewed_by":"owner","reviewed_at":"2026-07-15"}]
        proof, _ = CE.build_embroidery_proof(b); cons, _ = CE.build_consistency_audit(b)
        a, _ = CE.build_actual_asset_score(b, proof, cons)
        # proof is MISLEADING (publication_intent + AI) -> SCORED but approval blocked
        self.assertEqual(a["status"], "SCORED")
        self.assertTrue(a["approval_blocked"])

    def test_15_garment_mismatch_blocks_consistency(self):
        d = tempfile.mkdtemp(); b = build(d)
        b["image_specs"] = [{"image": 1, "garment_type": "hoodie"}]
        cons, _ = CE.build_consistency_audit(b)
        self.assertEqual(cons["status"], L.BLOCKED)

if __name__ == "__main__":
    unittest.main(verbosity=2)
