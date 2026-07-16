#!/usr/bin/env python3
"""v2.3.1 adversarial tests — the bypasses the independent audit found must stay closed."""
import os, sys, json, tempfile, subprocess, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core","creative","economics","compliance"):
    sys.path.insert(0, os.path.join(ROOT, d))
import status as S, gate_engine as GE, manifest as M, asset_validator as AV, hashing
import creative_edge as CE, main_image_validator as MIV
from PIL import Image

def sh(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}); return p.returncode, p.stdout+p.stderr
def tool(n, sub): return os.path.join(ROOT, sub, n)
def real_png(path, size=(1200,1200)): Image.new("RGB", size, (20,90,70)).save(path); return path

class Adversarial(unittest.TestCase):
    # P0.1
    def test_fake_png_header_is_rejected(self):
        d = tempfile.mkdtemp(); p = os.path.join(d,"fake.png")
        open(p,"wb").write(bytes.fromhex("89504e470d0a1a0a"+"00"*40))   # signature + junk
        self.assertEqual(AV.validate_image(p)["status"], "INVALID_FILE")
    def test_one_pixel_not_publication_ready(self):
        d = tempfile.mkdtemp(); p = real_png(os.path.join(d,"tiny.png"),(1,1))
        self.assertEqual(AV.validate_image(p,source_type="r",sku="s",design_version="v",reviewer="o")["status"],
                         "QUALITY_REVIEW_REQUIRED")
    # P0.7
    def test_path_traversal_rejected(self):
        d = tempfile.mkdtemp()
        self.assertEqual(AV.validate_image("../../etc/passwd", project_dir=d)["status"], "INVALID_FILE")
    # P0.2
    def test_boolean_cannot_replace_review(self):
        d = tempfile.mkdtemp(); CE.example_brief(d)
        b = json.load(open(os.path.join(d,"creative-brief.json"))); b["__folder"]=d
        b["our_images"]["main"] = real_png(os.path.join(d,"m.png"))
        b["thumbnail_review_complete"] = True     # boolean must NOT unlock scoring
        proof,_ = CE.build_embroidery_proof(b); cons,_ = CE.build_consistency_audit(b)
        a,_ = CE.build_actual_asset_score(b, proof, cons)
        self.assertEqual(a["status"], "INCOMPLETE")
    def test_stale_review_hash_rejected(self):
        d = tempfile.mkdtemp(); CE.example_brief(d)
        b = json.load(open(os.path.join(d,"creative-brief.json"))); b["__folder"]=d
        b["our_images"]["main"] = real_png(os.path.join(d,"m.png"))
        b["thumbnail_review"] = {"reviewer":"o","reviewed_at":"t","asset_hash":"sha256:WRONG",
                                 "main_image_clarity":2,"thumbnail_clarity":2,"product_fill":2,
                                 "personalization_readable":2,"mobile_readable":2,"accuracy_verified":2}
        proof,_ = CE.build_embroidery_proof(b); cons,_ = CE.build_consistency_audit(b)
        a,_ = CE.build_actual_asset_score(b, proof, cons)
        self.assertEqual(a["status"], "INCOMPLETE")
    # P0.3
    def test_missing_review_component_is_incomplete(self):
        d = tempfile.mkdtemp(); CE.example_brief(d)
        b = json.load(open(os.path.join(d,"creative-brief.json"))); b["__folder"]=d
        p = real_png(os.path.join(d,"m.png")); b["our_images"]["main"]=p
        b["thumbnail_review"] = {"reviewer":"o","reviewed_at":"t","asset_hash":hashing.file_sha256(p),
                                 "main_image_clarity":2}    # missing other components
        b["image_specs"]=[{"image":1,"garment_type":"crewneck sweatshirt"},{"image":2,"garment_type":"crewneck sweatshirt"}]
        proof,_ = CE.build_embroidery_proof(b); cons,_ = CE.build_consistency_audit(b)
        a,_ = CE.build_actual_asset_score(b, proof, cons)
        self.assertEqual(a["status"], "INCOMPLETE")
    # P0.4
    def test_main_validator_rejects_missing_file(self):
        r = MIV.validate({"slot_type":"AMAZON_MAIN_IMAGE","asset_path":"ghost.jpg","category_rules_verified":True})
        self.assertNotEqual(r["status"], "COMPLIANT_DRAFT")
        self.assertIn(r["status"], (S.INCOMPLETE, "BLOCKED"))
    def test_main_validator_accuracy_unknown_is_incomplete(self):
        d = tempfile.mkdtemp(); p = real_png(os.path.join(d,"m.png"))
        r = MIV.validate({"slot_type":"AMAZON_MAIN_IMAGE","asset_path":p,"category_rules_verified":True})
        self.assertEqual(r["status"], S.INCOMPLETE)   # accuracy defaults UNKNOWN
    # P0.10 / P0.11
    def test_compliant_draft_does_not_pass(self):
        self.assertFalse(GE._passes("MAIN_IMAGE_COMPLIANCE","COMPLIANT_DRAFT"))
        self.assertFalse(GE._passes("MAIN_IMAGE_COMPLIANCE","COMPLIANT"))   # v2.3.3: technical-only, not a pass
        self.assertTrue(GE._passes("MAIN_IMAGE_COMPLIANCE","APPROVED"))
    def test_ready_for_review_exit_is_three(self):
        self.assertEqual(S.exit_code_for(S.READY_FOR_REVIEW), 3)
    # P0.5
    def test_creative_gates_required_for_apparel(self):
        req = GE.required_gates({"requires_listing_images":True,"decoration_method":"machine embroidery"})
        for g in ["MAIN_IMAGE_COMPLIANCE","VISUAL_CONSISTENCY","ACTUAL_ASSET_EDGE","EMBROIDERY_PROOF"]:
            self.assertIn(g, req)
    def test_research_only_skips_creative_gates(self):
        req = GE.required_gates({"requires_listing_images":False})
        self.assertNotIn("MAIN_IMAGE_COMPLIANCE", req)
    # P0.6
    def test_approval_without_files_rejected(self):
        d = tempfile.mkdtemp()
        rc, out = sh([sys.executable,"pipeline.py", d, "--approve-final","--by","owner"])
        self.assertEqual(rc, 2); self.assertIn("required files missing", out)
    def test_cli_approval_binds_and_invalidates(self):
        d = tempfile.mkdtemp()
        # research-only project so only BASE gates are required; set them all passing first (v2.3.3 P0.14)
        m = M.load(d); m["requires_listing_images"] = False; M.save(d, m)
        passing = {"DEMAND":S.GO,"RELEVANCE":S.GO,"IP_SAFETY":"OK","PRODUCT_FEASIBILITY":S.APPROVED,
                   "ECONOMICS":S.GO,"FULFILLMENT":S.APPROVED,"CATALOG_STRUCTURE":S.APPROVED,
                   "CLAIMS_EVIDENCE":"VERIFIED","PERSONALIZATION":S.APPROVED,"LISTING_ACCURACY":S.GO}
        # v2.3.4-RC1 (P0.5): every passing gate must carry an evidence file for final approval
        def _evi(g):
            fn = f"{g}.evi.json"; open(os.path.join(d,fn),"w").write("{}")
            return [{"path":fn,"sha256":hashing.file_sha256(os.path.join(d,fn))}]
        for g,st in passing.items(): M.set_gate(d, g, GE.gate(g, st, evidence=_evi(g)).to_dict())
        open(os.path.join(d,"listing.json"),"w").write('{"title":"v1"}')
        open(os.path.join(d,"economics-input.csv"),"w").write("product,price\nx,10\n")
        rc, out = sh([sys.executable,"pipeline.py", d, "--approve-final","--by","owner"])
        self.assertEqual(rc, 0, out)
        appr = M.load(d)["owner_approvals"][-1]
        self.assertIn("approved_bundle_hash", appr)
        open(os.path.join(d,"listing.json"),"w").write('{"title":"CHANGED"}')
        M.verify_approvals(d)
        self.assertEqual(M.load(d)["owner_approvals"][-1]["decision"], "INVALIDATED")
    def test_final_approval_rejected_before_gates_pass(self):
        d = tempfile.mkdtemp()
        open(os.path.join(d,"listing.json"),"w").write('{"title":"v1"}')
        open(os.path.join(d,"economics-input.csv"),"w").write("product,price\nx,10\n")
        rc, out = sh([sys.executable,"pipeline.py", d, "--approve-final","--by","owner"])
        self.assertNotEqual(rc, 0)   # gates not passing -> refused
        self.assertIn("do not pass", out)
    # P0.8
    def test_mixed_economics_not_project_go(self):
        d = tempfile.mkdtemp(); c = os.path.join(d,"economics-input.csv")
        open(c,"w").write("product,price,blank_cost,embroidery_cost,packaging,ship_out,amazon_fee_pct,refund_reserve_pct,ppc_per_order\n"
                          "good,44.99,8,3,1.5,6,17,4,6\n"
                          "bad,19.99,5,3,1.5,,17,4,\n")   # bad = missing ship+ppc
        rc, out = sh([sys.executable, tool("economics_gate.py","economics"), c, "--out", d])
        self.assertEqual(rc, 2); self.assertIn("PROJECT: INCOMPLETE", out)
    # P0.9
    def test_hand_stitched_blocked_for_machine_embroidery(self):
        d = tempfile.mkdtemp()
        json.dump({"category":"apparel","product":{"decoration_method":"machine embroidery"},
                   "title":"Custom Nurse Sweatshirt Personalized Embroidered",
                   "bullets":["Hand stitched by hand with care.","x","x","x","x"],
                   "backend":"nurse rn embroidered personalized custom gift crewneck np icu"},
                  open(os.path.join(d,"listing.json"),"w"))
        rc, out = sh([sys.executable, tool("listing_validate.py","compliance"), os.path.join(d,"listing.json")])
        self.assertEqual(rc, 1); self.assertIn("hand stitched", out.lower())

if __name__ == "__main__":
    unittest.main(verbosity=2)
