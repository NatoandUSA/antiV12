#!/usr/bin/env python3
"""Phase 0 Evidence Patch regression tests (v2.3). Run: python -m unittest tests.test_evidence_patch"""
import os, sys, json, tempfile, subprocess, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core","creative","economics","research","compliance"):
    sys.path.insert(0, os.path.join(ROOT, d))
sys.path.insert(0, os.path.join(ROOT, "core"))
import paths  # noqa
import status as S, gate_engine as GE, manifest as M, asset_validator as AV
import creative_edge as CE, edge_lib as L

def sh(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}); return p.returncode, p.stdout+p.stderr
def tool(n, sub): return os.path.join(ROOT, sub, n)

class EvidencePatch(unittest.TestCase):
    # P0.1
    def test_ready_for_review_does_not_pass_hard_gate(self):
        self.assertFalse(GE._passes("MAIN_IMAGE_COMPLIANCE", S.READY_FOR_REVIEW))
        self.assertFalse(GE._passes("CLAIMS_EVIDENCE", S.READY_FOR_REVIEW))
        self.assertTrue(GE._passes("DEMAND", S.GO))
        self.assertTrue(GE._passes("EMBROIDERY_PROOF", "PARTIALLY_PROVEN"))
        self.assertFalse(GE._passes("EMBROIDERY_PROOF", "DRAFT_UNVERIFIED"))

    def test_review_status_locks_publication(self):
        d = tempfile.mkdtemp()
        for g in GE.HARD_GATES:
            M.set_gate(d, g, GE.gate(g, S.GO if g!="OWNER_APPROVAL" else S.APPROVED).to_dict())
        M.set_gate(d, "CLAIMS_EVIDENCE", GE.gate("CLAIMS_EVIDENCE", S.READY_FOR_REVIEW).to_dict())
        man = M.load(d)
        self.assertTrue(man["publication_locked"])
        self.assertTrue(any("CLAIMS_EVIDENCE" in r for r in man["publication_lock_reasons"]))

    # P0.3 / v2.3.1 real decoding
    def test_filename_string_is_not_an_image(self):
        r = AV.validate_image("does_not_exist.jpg")
        self.assertIn(r["status"], ("MISSING","INVALID_FILE"))
    def test_real_png_validates(self):
        from PIL import Image
        d = tempfile.mkdtemp(); p = os.path.join(d, "x.png")
        Image.new("RGB", (1200, 1200), (20, 90, 70)).save(p)   # a REAL, adequately-sized PNG
        r = AV.validate_image(p, source_type="real", sku="SKU1", design_version="v1", reviewer="owner")
        self.assertEqual(r["status"], "PUBLICATION_READY")
        self.assertEqual((r["width"], r["height"]), (1200,1200))
        self.assertTrue(r["sha256"].startswith("sha256:"))
    def test_tiny_image_not_publication_ready(self):
        from PIL import Image
        d = tempfile.mkdtemp(); p = os.path.join(d, "tiny.png")
        Image.new("RGB", (10, 10), (0,0,0)).save(p)
        r = AV.validate_image(p, source_type="real", sku="S", design_version="v", reviewer="o")
        self.assertEqual(r["status"], "QUALITY_REVIEW_REQUIRED")

    def test_actual_asset_incomplete_without_real_image(self):
        d = tempfile.mkdtemp(); CE.example_brief(d)
        with open(os.path.join(d,"creative-brief.json")) as f: b = json.load(f)
        b["our_images"]["main"] = "ghost.jpg"      # filename that doesn't exist
        b["__folder"] = os.path.dirname(os.path.join(d,"creative-brief.json"))
        proof, _ = CE.build_embroidery_proof(b); cons, _ = CE.build_consistency_audit(b)
        a, _ = CE.build_actual_asset_score(b, proof, cons)
        self.assertEqual(a["status"], "INCOMPLETE")

    # P0.4
    def test_empty_specs_is_incomplete_not_consistent(self):
        d = tempfile.mkdtemp(); CE.example_brief(d)
        with open(os.path.join(d,"creative-brief.json")) as f: b = json.load(f)
        b["image_specs"] = []
        cons, _ = CE.build_consistency_audit(b)
        self.assertEqual(cons["status"], L.CONSISTENCY_INCOMPLETE)
    def test_single_bad_spec_still_blocks(self):
        d = tempfile.mkdtemp(); CE.example_brief(d)
        with open(os.path.join(d,"creative-brief.json")) as f: b = json.load(f)
        b["image_specs"] = [{"image":1,"garment_type":"hoodie"}]
        cons, _ = CE.build_consistency_audit(b)
        self.assertEqual(cons["status"], L.BLOCKED)

    # P0.5
    def test_missing_cost_is_incomplete(self):
        d = tempfile.mkdtemp(); c = os.path.join(d,"economics-input.csv")
        # ship_out and ppc_per_order MISSING (blank)
        open(c,"w").write("product,price,blank_cost,embroidery_cost,packaging,ship_out,amazon_fee_pct,refund_reserve_pct,ppc_per_order\n"
                          "nurse,44.99,8,3,1.5,,17,4,\n")
        rc, out = sh([sys.executable, tool("economics_gate.py","economics"), c, "--out", d])
        self.assertIn("INCOMPLETE", out); self.assertEqual(rc, 2)

    # P0.6
    def test_approval_bundle_hash_auto_invalidates(self):
        d = tempfile.mkdtemp()
        f = os.path.join(d, "listing.json"); open(f,"w").write('{"title":"v1"}')
        M.add_approval(d, {"approval_type":"FINAL_LISTING","decision":"APPROVED","owner_name":"owner",
                           "approved_files":["listing.json"]})
        man = M.load(d)
        appr = man["owner_approvals"][0]
        self.assertIn("approved_bundle_hash", appr)
        # change the file -> next reevaluate must auto-invalidate
        open(f,"w").write('{"title":"v2 CHANGED"}')
        M.set_gate(d, "DEMAND", GE.gate("DEMAND", S.GO).to_dict())   # triggers reevaluate
        man = M.load(d)
        self.assertEqual(man["owner_approvals"][0]["decision"], "INVALIDATED")

    # P0.8
    def test_blocked_creative_exits_nonzero(self):
        d = tempfile.mkdtemp()
        sh([sys.executable, tool("creative_edge.py","creative"), d, "--example"])
        rc, out = sh([sys.executable, tool("creative_edge.py","creative"), d])
        self.assertNotEqual(rc, 0, "blocked creative run (AI draft, no real image) must not exit 0")
        self.assertEqual(rc, 3)

    # P0.7
    def test_capability_manifest_generates(self):
        rc, out = sh([sys.executable, os.path.join(ROOT,"capabilities.py")])
        self.assertEqual(rc, 0)
        cap = json.load(open(os.path.join(ROOT,"CAPABILITIES.json")))
        self.assertEqual(cap["capabilities"]["dashboard_ui"]["maturity"], "NOT_BUILT")
        self.assertIn(cap["capabilities"]["research"]["maturity"], ("BUILT","BUILT_WITH_LIMITATIONS"))

if __name__ == "__main__":
    unittest.main(verbosity=2)
