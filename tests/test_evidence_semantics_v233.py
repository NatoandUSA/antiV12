import os, sys, json, tempfile, subprocess, unittest
from PIL import Image
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core","creative"): sys.path.insert(0, os.path.join(ROOT, d))
import manifest as M, stages as ST, gate_engine as GE, status as S, creative_edge as CE, edge_lib as L
def sh(c): p=subprocess.run(c,capture_output=True,text=True,cwd=ROOT,encoding="utf-8",errors="replace",
                            env={**os.environ,"PYTHONIOENCODING":"utf-8"}); return p.returncode,p.stdout+p.stderr

class Semantics(unittest.TestCase):
    def test_blank_image_not_proven(self):
        d=tempfile.mkdtemp(); CE.example_brief(d)
        b=json.load(open(os.path.join(d,"creative-brief.json"))); b["__folder"]=d
        Image.new("RGB",(2000,2000),(255,255,255)).save(os.path.join(d,"macro.png"))
        b["our_images"]["macro"]="macro.png"
        self.assertEqual(CE.build_embroidery_proof(b)[0]["status"], L.DRAFT_UNVERIFIED)
    def test_tiny_macro_not_proven(self):
        d=tempfile.mkdtemp(); CE.example_brief(d)
        b=json.load(open(os.path.join(d,"creative-brief.json"))); b["__folder"]=d
        Image.new("RGB",(1,1),(0,0,0)).save(os.path.join(d,"macro.png"))
        b["our_images"]["macro"]="macro.png"
        self.assertEqual(CE.build_embroidery_proof(b)[0]["status"], L.DRAFT_UNVERIFIED)
    def test_creative_edge_updates_manifest(self):
        d=tempfile.mkdtemp()
        m=M.new("p","P"); m["product_family"]="embroidered_apparel"; M.save(d,m)
        sh([sys.executable,"creative/creative_edge.py",d,"--example"])
        sh([sys.executable,"creative/creative_edge.py",d])
        man=M.load(d)
        self.assertIn("EMBROIDERY_PROOF", man["gates"])       # manifest now reflects creative work
        self.assertIn("MAIN_IMAGE_COMPLIANCE", man["gates"])
    def test_orchestration_error_not_all_pass(self):
        d=tempfile.mkdtemp(); m=M.new("p","P"); m["requires_listing_images"]=False; M.save(d,m)
        # only set some base gates; leave RELEVANCE etc. missing
        M.set_gate(d,"DEMAND",GE.gate("DEMAND",S.GO).to_dict())
        info=ST.compute(M.load(d))
        self.assertTrue(info["missing"])
        self.assertIsNotNone(info["current_stage"])   # never None while gates missing
    def test_init_project_requires_embroidery_proof(self):
        d=tempfile.mkdtemp()
        sh([sys.executable,"pipeline.py",d,"--init-project","--product-family","embroidered_apparel","--decoration-method","machine embroidery"])
        req=GE.required_gates(ST.project_traits(M.load(d)))
        self.assertIn("EMBROIDERY_PROOF", req)
    def test_plan_specs_are_not_actual_consistent(self):
        d=tempfile.mkdtemp(); CE.example_brief(d)
        b=json.load(open(os.path.join(d,"creative-brief.json"))); b["__folder"]=d
        b["image_specs"]=[{"image":1,"garment_type":"crewneck sweatshirt"},{"image":2,"garment_type":"crewneck sweatshirt"}]
        self.assertEqual(CE.build_consistency_audit(b)[0]["status"], L.PLAN_CONSISTENT)

if __name__=="__main__": unittest.main(verbosity=2)
