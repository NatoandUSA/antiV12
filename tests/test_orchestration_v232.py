import os, sys, tempfile, json, unittest
from PIL import Image
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core","creative"): sys.path.insert(0, os.path.join(ROOT, d))
import stages as ST, manifest as M, gate_engine as GE, status as S, creative_edge as CE, edge_lib as L

class Orchestration(unittest.TestCase):
    def test_next_action_is_single_and_clear(self):
        d = tempfile.mkdtemp(); M.save(d, M.new("p","P"))
        info = ST.compute(M.load(d))
        self.assertIsNotNone(info["current_stage"])
        self.assertTrue(info["current_stage"]["action"])   # exactly one recommended action
    def test_embroidery_gate_required_for_embroidery_project(self):
        d = tempfile.mkdtemp(); m = M.new("p","P"); m["product_family"]="embroidered_apparel"; M.save(d,m)
        info = ST.compute(M.load(d))
        self.assertIn("EMBROIDERY_PROOF", info["required_gates"])
    def test_boolean_macro_not_proven(self):
        d = tempfile.mkdtemp(); CE.example_brief(d)
        b = json.load(open(os.path.join(d,"creative-brief.json"))); b["__folder"]=d
        b["our_images"]["macro_real"]=True
        proof,_ = CE.build_embroidery_proof(b)
        self.assertEqual(proof["status"], L.DRAFT_UNVERIFIED)
    def test_real_macro_asset_is_proven(self):
        import hashing
        d = tempfile.mkdtemp(); CE.example_brief(d)
        b = json.load(open(os.path.join(d,"creative-brief.json"))); b["__folder"]=d
        p = os.path.join(d,"macro.png"); Image.new("RGB",(2000,2000),(20,90,70)).save(p)
        b["our_images"]["macro"]="macro.png"
        b["embroidery_proof_review"] = {"reviewer":"owner","reviewed_at":"t","asset_hash":hashing.file_sha256(p),
            "individual_threads_visible":True,"stitch_edges_visible":True,"fabric_weave_visible":True,
            "image_is_not_ai":True,"image_is_not_blank":True,"supplier_sku_matches":True,
            "design_version_matches":True,"thread_colors_match":True,"placement_matches":True}
        proof,_ = CE.build_embroidery_proof(b)
        self.assertEqual(proof["status"], L.PROVEN)
        self.assertTrue(proof["evidence"]["asset_hash"].startswith("sha256:"))
    def test_blank_or_unreviewed_macro_not_proven(self):
        d = tempfile.mkdtemp(); CE.example_brief(d)
        b = json.load(open(os.path.join(d,"creative-brief.json"))); b["__folder"]=d
        Image.new("RGB",(2000,2000),(255,255,255)).save(os.path.join(d,"macro.png"))  # blank white
        b["our_images"]["macro"]="macro.png"     # no content review
        proof,_ = CE.build_embroidery_proof(b)
        self.assertEqual(proof["status"], L.DRAFT_UNVERIFIED)

if __name__=="__main__": unittest.main(verbosity=2)
