#!/usr/bin/env python3
"""v2.3.4-RC evidence & approval integrity tests.

Covers the independent audit's reproduced findings:
  P0.1 supplier-photo proof needs a hash-bound review (not a boolean)
  P0.2 a tiny main image cannot be COMPLIANT
  P0.3 a tiny main image cannot be scored
  P0.4 image-spec hashes must match real current files
  P0.5 Creative Edge reports package status, never 'publication clear'
  P0.6 --approve-main-image / --approve-creative supported CLIs
  P0.7 final approval requires creative approval first
  P0.8 changing a bound evidence file invalidates final approval
  P0.9 VISUAL-CONSISTENCY.json is written
"""
import os, sys, json, tempfile, subprocess, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core", "creative"):
    sys.path.insert(0, os.path.join(ROOT, d))
import status as S, gate_engine as GE, manifest as M, asset_validator as AV, hashing
import creative_edge as CE, main_image_validator as MIV, edge_lib as L
from PIL import Image

def sh(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, encoding="utf-8", errors="replace",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return p.returncode, p.stdout + p.stderr

def img(path, size=(1200, 1200), color=(20, 90, 70)):
    Image.new("RGB", size, color).save(path); return path

def brief_at(d):
    CE.example_brief(d)
    b = json.load(open(os.path.join(d, "creative-brief.json")))
    b["__folder"] = d
    return b


class SupplierProof(unittest.TestCase):
    def test_blank_supplier_photo_not_partially_proven(self):
        d = tempfile.mkdtemp(); b = brief_at(d)
        Image.new("RGB", (2000, 2000), (255, 255, 255)).save(os.path.join(d, "supplier.png"))
        b["our_images"]["supplier_photo"] = "supplier.png"
        b["embroidery_proof_review"] = {"reviewer": "owner", "supplier_evidence_reviewed": True}
        self.assertEqual(CE.build_embroidery_proof(b)[0]["status"], L.DRAFT_UNVERIFIED)

    def test_supplier_review_requires_current_asset_hash(self):
        d = tempfile.mkdtemp(); b = brief_at(d)
        p = img(os.path.join(d, "supplier.png"), (2000, 2000))
        b["our_images"]["supplier_photo"] = "supplier.png"
        conf = {k: True for k in ("supplier_identity_verified", "supplier_sku_matches",
                "design_version_matches", "thread_colors_match", "placement_matches",
                "image_is_not_ai", "image_is_not_blank", "visible_embroidery_confirmed")}
        # correct hash -> PARTIALLY_PROVEN
        b["supplier_reference_review"] = {"reviewer": "o", "reviewed_at": "t",
                                          "asset_hash": hashing.file_sha256(p), **conf}
        self.assertEqual(CE.build_embroidery_proof(b)[0]["status"], L.PARTIALLY_PROVEN)
        # stale hash -> DRAFT_UNVERIFIED
        b["supplier_reference_review"]["asset_hash"] = "sha256:WRONG"
        self.assertEqual(CE.build_embroidery_proof(b)[0]["status"], L.DRAFT_UNVERIFIED)

    def test_supplier_review_missing_flag_not_proven(self):
        d = tempfile.mkdtemp(); b = brief_at(d)
        p = img(os.path.join(d, "supplier.png"), (2000, 2000))
        b["our_images"]["supplier_photo"] = "supplier.png"
        b["supplier_reference_review"] = {"reviewer": "o", "reviewed_at": "t",
            "asset_hash": hashing.file_sha256(p), "supplier_identity_verified": True}  # rest missing
        self.assertEqual(CE.build_embroidery_proof(b)[0]["status"], L.DRAFT_UNVERIFIED)


class TinyImage(unittest.TestCase):
    def test_tiny_main_image_not_compliant(self):
        d = tempfile.mkdtemp(); p = img(os.path.join(d, "m.png"), (1, 1))
        r = MIV.validate({"slot_type": "AMAZON_MAIN_IMAGE", "asset_path": p,
            "garment_type_accuracy": "VERIFIED", "color_accuracy": "VERIFIED",
            "placement_accuracy": "VERIFIED", "background": "white",
            "category_rules_verified": True, "category_review": {"reviewer": "owner"}})
        self.assertNotEqual(r["status"], "COMPLIANT")
        self.assertEqual(r["status"], L.MI_INCOMPLETE)

    def test_quality_review_required_asset_not_scored(self):
        d = tempfile.mkdtemp(); b = brief_at(d)
        p = img(os.path.join(d, "main.png"), (1, 1))
        b["our_images"]["main"] = "main.png"
        b["thumbnail_review"] = {"reviewer": "o", "reviewed_at": "t", "asset_hash": hashing.file_sha256(p),
            "main_image_clarity": 2, "thumbnail_clarity": 2, "product_fill": 2,
            "personalization_readable": 2, "mobile_readable": 2, "accuracy_verified": 2}
        proof, _ = CE.build_embroidery_proof(b); cons, _ = CE.build_consistency_audit(b)
        a, _ = CE.build_actual_asset_score(b, proof, cons)
        self.assertEqual(a["status"], "INCOMPLETE")


class ConsistencyHashes(unittest.TestCase):
    def test_consistency_rejects_nonexistent_asset_hash(self):
        d = tempfile.mkdtemp(); b = brief_at(d)
        b["image_specs"] = [
            {"image": 1, "garment_type": "crewneck sweatshirt", "asset_hash": "sha256:nope", "reviewed_by": "o", "reviewed_at": "t"},
            {"image": 2, "garment_type": "crewneck sweatshirt", "asset_hash": "sha256:nada", "reviewed_by": "o", "reviewed_at": "t"}]
        self.assertEqual(CE.build_consistency_audit(b)[0]["status"], L.PLAN_CONSISTENT)

    def test_consistency_accepts_real_matching_hash(self):
        d = tempfile.mkdtemp(); b = brief_at(d)
        p1 = img(os.path.join(d, "i1.png"), color=(20, 90, 71))
        p2 = img(os.path.join(d, "i2.png"), color=(20, 90, 72))
        b["image_specs"] = [
            {"image": 1, "garment_type": "crewneck sweatshirt", "asset_path": "i1.png", "asset_hash": hashing.file_sha256(p1), "reviewed_by": "o", "reviewed_at": "t", "personalization_example": "Sarah, RN"},
            {"image": 2, "garment_type": "crewneck sweatshirt", "asset_path": "i2.png", "asset_hash": hashing.file_sha256(p2), "reviewed_by": "o", "reviewed_at": "t", "personalization_example": "Sarah, RN"}]
        self.assertEqual(CE.build_consistency_audit(b)[0]["status"], L.CONSISTENT)

    def test_consistency_rejects_stale_hash_on_real_file(self):
        d = tempfile.mkdtemp(); b = brief_at(d)
        img(os.path.join(d, "i1.png")); img(os.path.join(d, "i2.png"))
        b["image_specs"] = [
            {"image": 1, "garment_type": "crewneck sweatshirt", "asset_path": "i1.png", "asset_hash": "sha256:STALE", "reviewed_by": "o", "reviewed_at": "t"},
            {"image": 2, "garment_type": "crewneck sweatshirt", "asset_path": "i2.png", "asset_hash": "sha256:STALE2", "reviewed_by": "o", "reviewed_at": "t"}]
        self.assertNotEqual(CE.build_consistency_audit(b)[0]["status"], L.CONSISTENT)

    def test_visual_consistency_json_written(self):
        d = tempfile.mkdtemp(); CE.example_brief(d)
        sh([sys.executable, "creative/creative_edge.py", d])
        self.assertTrue(os.path.exists(os.path.join(d, "VISUAL-CONSISTENCY.json")))


class CreativeStatus(unittest.TestCase):
    def test_creative_edge_never_says_publication_clear(self):
        d = tempfile.mkdtemp(); CE.example_brief(d)
        rc, out = sh([sys.executable, "creative/creative_edge.py", d])
        self.assertNotIn("Publication: clear", out)
        self.assertIn("CREATIVE PACKAGE", out)


class ApprovalWorkflow(unittest.TestCase):
    def _ev(self, d, fname, payload="{}"):
        """write a stub evidence file and return [{path, sha256}] for it (RC1: gates need evidence)."""
        with open(os.path.join(d, fname), "w") as f: f.write(payload)
        return [{"path": fname, "sha256": hashing.file_sha256(os.path.join(d, fname))}]

    def _ready_creative_project(self):
        """A project whose four creative gates pass except owner approvals — RC1-realistic:
        every passing gate carries an evidence file, and the compliance record binds a real hash."""
        d = tempfile.mkdtemp()
        m = M.load(d); m["requires_listing_images"] = True; m["requires_embroidery_proof"] = False
        m["product_family"] = "personalized_apparel"; m["decoration_method"] = "print"
        M.save(d, m)
        # base gates passing, each with an evidence file
        base = {"DEMAND": S.GO, "RELEVANCE": S.GO, "IP_SAFETY": "OK", "PRODUCT_FEASIBILITY": S.APPROVED,
                "ECONOMICS": S.GO, "FULFILLMENT": S.APPROVED, "CATALOG_STRUCTURE": S.APPROVED,
                "CLAIMS_EVIDENCE": "VERIFIED", "PERSONALIZATION": S.APPROVED, "LISTING_ACCURACY": S.GO}
        for g, st in base.items():
            M.set_gate(d, g, GE.gate(g, st, evidence=self._ev(d, f"{g}.evi.json")).to_dict())
        # main image: real file; compliance record binds its true hash
        p = img(os.path.join(d, "main.png"))
        json.dump({"status": "COMPLIANT",
                   "asset": {"relative_path": "main.png", "sha256": hashing.file_sha256(p)}},
                  open(os.path.join(d, "MAIN-IMAGE-COMPLIANCE.json"), "w"))
        mic_evi = [{"path": "MAIN-IMAGE-COMPLIANCE.json",
                    "sha256": hashing.file_sha256(os.path.join(d, "MAIN-IMAGE-COMPLIANCE.json"))}]
        M.set_gate(d, "MAIN_IMAGE_COMPLIANCE", GE.gate("MAIN_IMAGE_COMPLIANCE", "COMPLIANT", evidence=mic_evi).to_dict())
        M.set_gate(d, "VISUAL_CONSISTENCY", GE.gate("VISUAL_CONSISTENCY", "CONSISTENT",
                   evidence=self._ev(d, "VISUAL-CONSISTENCY.json")).to_dict())
        M.set_gate(d, "ACTUAL_ASSET_EDGE", GE.gate("ACTUAL_ASSET_EDGE", S.APPROVED,
                   evidence=self._ev(d, "ACTUAL-ASSET-EDGE-SCORE.json")).to_dict())
        # creative bundle files required by --approve-creative (print project: no embroidery proof)
        json.dump({"our_images": {"main": "main.png"}}, open(os.path.join(d, "creative-brief.json"), "w"))
        for f in ("THUMBNAIL-REVIEW.json", "IMAGE-STORYBOARD.json"):
            open(os.path.join(d, f), "w").write("{}")
        open(os.path.join(d, "listing.json"), "w").write('{"title":"v1"}')
        open(os.path.join(d, "economics-input.csv"), "w").write("product,price\nx,10\n")
        return d

    def test_approve_main_image_sets_gate_and_binds_hash(self):
        d = self._ready_creative_project()
        rc, out = sh([sys.executable, "pipeline.py", d, "--approve-main-image", "--asset", "main.png", "--by", "owner"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(M.load(d)["gates"]["MAIN_IMAGE_COMPLIANCE"]["status"], S.APPROVED)

    def test_changed_main_image_invalidates_main_approval(self):
        d = self._ready_creative_project()
        sh([sys.executable, "pipeline.py", d, "--approve-main-image", "--asset", "main.png", "--by", "owner"])
        img(os.path.join(d, "main.png"), color=(1, 2, 3))   # change the asset
        M.verify_approvals(d)
        self.assertNotEqual(M.load(d)["gates"]["MAIN_IMAGE_COMPLIANCE"]["status"], S.APPROVED)

    def test_approve_creative_requires_main_image_approved_first(self):
        d = self._ready_creative_project()
        rc, out = sh([sys.executable, "pipeline.py", d, "--approve-creative", "--by", "owner"])
        self.assertNotEqual(rc, 0)
        self.assertIn("MAIN_IMAGE_COMPLIANCE", out)

    def test_approve_creative_sets_gate_after_main_image(self):
        d = self._ready_creative_project()
        sh([sys.executable, "pipeline.py", d, "--approve-main-image", "--asset", "main.png", "--by", "owner"])
        rc, out = sh([sys.executable, "pipeline.py", d, "--approve-creative", "--by", "owner"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(M.load(d)["gates"]["CREATIVE_OWNER_APPROVAL"]["status"], S.APPROVED)

    def test_final_approval_requires_creative_approval(self):
        d = self._ready_creative_project()
        sh([sys.executable, "pipeline.py", d, "--approve-main-image", "--asset", "main.png", "--by", "owner"])
        # creative owner approval NOT yet granted -> final must refuse
        rc, out = sh([sys.executable, "pipeline.py", d, "--approve-final", "--by", "owner"])
        self.assertNotEqual(rc, 0)
        self.assertIn("CREATIVE_OWNER_APPROVAL", out)

    def test_full_approval_chain_unlocks(self):
        d = self._ready_creative_project()
        sh([sys.executable, "pipeline.py", d, "--approve-main-image", "--asset", "main.png", "--by", "owner"])
        sh([sys.executable, "pipeline.py", d, "--approve-creative", "--by", "owner"])
        rc, out = sh([sys.executable, "pipeline.py", d, "--approve-final", "--by", "owner"])
        self.assertEqual(rc, 0, out)
        self.assertFalse(M.load(d)["publication_locked"])

    def test_changed_evidence_invalidates_final_approval(self):
        d = self._ready_creative_project()
        sh([sys.executable, "pipeline.py", d, "--approve-main-image", "--asset", "main.png", "--by", "owner"])
        sh([sys.executable, "pipeline.py", d, "--approve-creative", "--by", "owner"])
        sh([sys.executable, "pipeline.py", d, "--approve-final", "--by", "owner"])
        # change a bound evidence file after final approval
        open(os.path.join(d, "listing.json"), "w").write('{"title":"CHANGED"}')
        M.verify_approvals(d)
        finals = [a for a in M.load(d)["owner_approvals"] if a["approval_type"] == "FINAL_LISTING"]
        self.assertEqual(finals[-1]["decision"], "INVALIDATED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
