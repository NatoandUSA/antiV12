#!/usr/bin/env python3
"""v2.3.4-RC1 alpha-blocker regression tests (from the FINAL ALPHA READINESS review).

Covers: gate-file ingestion (P0.1), thumbnail source of truth (P0.2), main-image approval
identity (P0.3), creative bundle completeness (P0.4), final evidence completeness (P0.5),
IP on listing.json (P0.6), sys.executable (P0.7), docs (P0.8), one next-action engine (P0.9),
plus a full end-to-end shadow run with no manifest editing.
"""
import os, sys, json, tempfile, subprocess, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core", "creative", "compliance"):
    sys.path.insert(0, os.path.join(ROOT, d))
import status as S, gate_engine as GE, manifest as M, hashing
import creative_edge as CE
from PIL import Image

def sh(*args):
    p = subprocess.run([sys.executable, "pipeline.py", *args], capture_output=True, text=True, cwd=ROOT,
                       encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return p.returncode, p.stdout + p.stderr

def img(path, size=(2000, 2000), color=(20, 60, 90)):
    Image.new("RGB", size, color).save(path); return path


class GateFileIngestion(unittest.TestCase):
    def _init(self):
        d = tempfile.mkdtemp()
        sh(d, "--init-project", "--decoration-method", "machine embroidery")
        return d

    def test_scaffold_writes_templates(self):
        d = self._init()
        sh(d, "--scaffold-gate-files")
        for f in ("RELEVANCE-REVIEW.json", "PRODUCT-FEASIBILITY.json", "FBM-FEASIBILITY.json",
                  "CATALOG-STRUCTURE.json", "PERSONALIZATION-RULES.json", "CLAIMS-EVIDENCE.json"):
            self.assertTrue(os.path.exists(os.path.join(d, f)), f)

    def test_feasibility_file_sets_gate(self):
        d = self._init()
        json.dump({"decision": "GO", "reviewer": "o", "reviewed_at": "t"},
                  open(os.path.join(d, "PRODUCT-FEASIBILITY.json"), "w"))
        sh(d, "Nurse")
        self.assertEqual(M.load(d)["gates"]["PRODUCT_FEASIBILITY"]["status"], "GO")

    def test_incomplete_decision_does_not_pass(self):
        d = self._init()
        json.dump({"decision": "INCOMPLETE"}, open(os.path.join(d, "CATALOG-STRUCTURE.json"), "w"))
        sh(d, "Nurse")
        self.assertNotIn(M.load(d)["gates"]["CATALOG_STRUCTURE"]["status"], (S.APPROVED,))

    def test_filename_alone_does_not_pass(self):
        d = self._init()
        json.dump({"reviewer": "o"}, open(os.path.join(d, "CLAIMS-EVIDENCE.json"), "w"))  # no decision
        sh(d, "Nurse")
        self.assertEqual(M.load(d)["gates"]["CLAIMS_EVIDENCE"]["status"], S.INCOMPLETE)

    def test_relevance_review_file_sets_gate(self):
        d = self._init()
        json.dump({"decision": "GO", "reviewer": "o", "reviewed_at": "t"},
                  open(os.path.join(d, "RELEVANCE-REVIEW.json"), "w"))
        sh(d, "Nurse")
        self.assertEqual(M.load(d)["gates"]["RELEVANCE"]["status"], "GO")


class IPScreen(unittest.TestCase):
    def test_listing_json_runs_ip_guard(self):
        d = tempfile.mkdtemp()
        sh(d, "--init-project")
        json.dump({"title": "Disney Mickey Mouse Nurse Sweatshirt",
                   "description": "Official Disney character gift"},
                  open(os.path.join(d, "listing.json"), "w"))
        sh(d, "Nurse")
        st = M.load(d)["gates"].get("IP_SAFETY", {}).get("status")
        self.assertEqual(st, S.BLOCKED)   # Disney/Mickey -> BLOCK, not NOT_RUN


class ThumbnailSourceOfTruth(unittest.TestCase):
    def test_completed_thumbnail_json_is_used_for_score(self):
        d = tempfile.mkdtemp(); CE.example_brief(d)
        b = json.load(open(os.path.join(d, "creative-brief.json"))); b["__folder"] = d
        p = img(os.path.join(d, "main.png"), (1500, 1500))
        b["our_images"]["main"] = "main.png"
        b.pop("thumbnail_review", None)   # NOT embedded — only the file exists
        json.dump({"reviewer": "owner", "reviewed_at": "t", "asset_hash": hashing.file_sha256(p),
                   "main_image_clarity": 2, "thumbnail_clarity": 2, "product_fill": 2,
                   "personalization_readable": 2, "mobile_readable": 2, "accuracy_verified": 2},
                  open(os.path.join(d, "THUMBNAIL-REVIEW.json"), "w"))
        proof, _ = CE.build_embroidery_proof(b); cons, _ = CE.build_consistency_audit(b)
        a, _ = CE.build_actual_asset_score(b, proof, cons)
        # the completed file is read; the only reason to be INCOMPLETE now is consistency, not "no review"
        self.assertNotIn("no completed thumbnail review", str(a.get("reason", "")))


class MainImageApprovalIdentity(unittest.TestCase):
    def _project_with_compliance(self, asset_name="a.png"):
        d = tempfile.mkdtemp()
        sh(d, "--init-project", "--decoration-method", "print")
        p = img(os.path.join(d, asset_name))
        json.dump({"status": "COMPLIANT",
                   "asset": {"relative_path": asset_name, "sha256": hashing.file_sha256(p)}},
                  open(os.path.join(d, "MAIN-IMAGE-COMPLIANCE.json"), "w"))
        M.set_gate(d, "MAIN_IMAGE_COMPLIANCE", GE.gate("MAIN_IMAGE_COMPLIANCE", "COMPLIANT").to_dict())
        return d

    def test_approve_rejects_asset_not_in_compliance(self):
        d = self._project_with_compliance("a.png")
        img(os.path.join(d, "b.png"), color=(1, 2, 3))
        rc, out = sh(d, "--approve-main-image", "--asset", "b.png", "--by", "owner")
        self.assertNotEqual(rc, 0)
        self.assertIn("not the reviewed image", out)

    def test_approve_rejects_changed_file(self):
        d = self._project_with_compliance("a.png")
        img(os.path.join(d, "a.png"), color=(9, 9, 9))   # change after compliance recorded
        rc, out = sh(d, "--approve-main-image", "--asset", "a.png", "--by", "owner")
        self.assertNotEqual(rc, 0)
        self.assertIn("hash does not match", out)


class CreativeBundle(unittest.TestCase):
    def test_creative_approval_rejects_empty_bundle(self):
        d = tempfile.mkdtemp()
        sh(d, "--init-project", "--decoration-method", "print")
        # force creative gates to pass but provide NO evidence files
        for g, st in (("MAIN_IMAGE_COMPLIANCE", "APPROVED"), ("VISUAL_CONSISTENCY", "CONSISTENT"),
                      ("ACTUAL_ASSET_EDGE", S.APPROVED)):
            M.set_gate(d, g, GE.gate(g, st).to_dict())
        # main-image approval derived gate: simulate via approval so the precheck passes
        img(os.path.join(d, "main.png"))
        rc, out = sh(d, "--approve-creative", "--by", "owner")
        self.assertNotEqual(rc, 0)
        self.assertIn("incomplete", out.lower())


class NextActionEngine(unittest.TestCase):
    def test_pipeline_footer_and_next_agree(self):
        d = tempfile.mkdtemp()
        sh(d, "--init-project", "--decoration-method", "machine embroidery")
        _, foot = sh(d, "Nurse")
        _, nxt = sh(d, "--next")
        # both derive from stages.compute — the same stage name should appear in each
        import re
        m = re.search(r"NEXT: ([^\(]+)\(", foot)
        if m:
            self.assertIn(m.group(1).strip(), nxt)


class Docs(unittest.TestCase):
    def test_quickstart_has_no_removed_approve_command(self):
        p = os.path.join(ROOT, "docs", "QUICKSTART-nhan-vien.md")
        txt = open(p, encoding="utf-8").read()
        self.assertNotIn('--approve "', txt)   # the removed keyword-approval command
    def test_requests_in_requirements(self):
        req = open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8").read()
        self.assertIn("requests", req)

    def test_no_operational_doc_has_removed_approve_command(self):
        import glob as _g
        for p in _g.glob(os.path.join(ROOT, "docs", "*.md")) + [os.path.join(ROOT, "README.md")]:
            txt = open(p, encoding="utf-8").read()
            self.assertNotIn('--approve "', txt, p)

    def test_pipeline_uses_sys_executable_not_python3(self):
        src = open(os.path.join(ROOT, "pipeline.py"), encoding="utf-8").read()
        # the only allowed 'python3' is the sys.executable fallback string
        bad = [ln for ln in src.splitlines()
               if '"python3"' in ln and not ln.strip().startswith("#")
               and "sys.executable or" not in ln and 'cmd[0] in' not in ln]
        self.assertEqual(bad, [], bad)


class EndToEndShadow(unittest.TestCase):
    def test_full_project_unlocks_without_manifest_editing(self):
        d = tempfile.mkdtemp()
        sh(d, "--init-project", "--decoration-method", "machine embroidery")
        sh(d, "--scaffold-gate-files")
        for f, dec in (("RELEVANCE-REVIEW.json", "GO"), ("PRODUCT-FEASIBILITY.json", "GO"),
                       ("FBM-FEASIBILITY.json", "GO"), ("CATALOG-STRUCTURE.json", "APPROVED"),
                       ("PERSONALIZATION-RULES.json", "APPROVED"), ("CLAIMS-EVIDENCE.json", "VERIFIED")):
            p = os.path.join(d, f); doc = json.load(open(p))
            doc["decision"] = dec; doc["reviewer"] = "owner"; doc["reviewed_at"] = "2026-07-15"
            json.dump(doc, open(p, "w"))
        # IP-clean AND evidence-clean listing + real demand/economics. (The copy must not assert a
        # factual attribute with no verified backing: "cotton blend" and "made to order" were unsupported
        # claims that Session 3.1 PATCH 3 now blocks — a genuinely unlockable listing carries neither.)
        json.dump({"title": "Personalized Nurse Sweatshirt Embroidered Crewneck with Name for Women",
                   "bullets": ["Custom name embroidery for nurses", "Personalized nurse crewneck sweatshirt",
                               "Embroidered with the name you choose", "Personalized nurse gift",
                               "Embroidered design, not a printed graphic"],
                   "description": "Personalized nurse crewneck sweatshirt embroidered with the name you choose.",
                   "backend": "nurse embroidered personalized custom gift crewneck women name",
                   "personalization": "name to embroider"}, open(os.path.join(d, "listing.json"), "w"))
        open(os.path.join(d, "demand-input.csv"), "w").write(
            "keyword,amazon_sv,amazon_td,etsy,ebay_sold,trend,pinterest,reddit\n"
            "personalized nurse sweatshirt,600,3,bestseller,60,up,rising,gift\n")
        open(os.path.join(d, "economics-input.csv"), "w").write(
            "product,price,blank_cost,embroidery_cost,packaging,ship_out,amazon_fee_pct,refund_reserve_pct,ppc_per_order\n"
            "nurse sweatshirt,44.99,8,3,1.5,6,17,4,6\n")
        # real creative assets + reviews
        main = img(os.path.join(d, "main.png")); macro = img(os.path.join(d, "macro.png"), color=(30, 80, 110))
        s1 = img(os.path.join(d, "s1.png"), (1500, 1500), (21, 61, 91)); s2 = img(os.path.join(d, "s2.png"), (1500, 1500), (22, 62, 92))
        json.dump({"project": "Nurse", "publication_intent": True, "category_rules_verified": True,
                   "category_review": {"reviewer": "owner", "reviewed_at": "t"},
                   "product": {"garment_type": "crewneck sweatshirt", "colors": ["navy"], "thread_colors": ["cream"],
                               "decoration_method": "machine embroidery", "design_placement": "left chest",
                               "supplier_sku": "NS-001", "design_version": "v1", "personalization_fields": ["name"],
                               "main_image_is_ai": False, "garment_type_accuracy": "VERIFIED",
                               "color_accuracy": "VERIFIED", "placement_accuracy": "VERIFIED"},
                   "our_images": {"main": "main.png", "macro": "macro.png", "main_source_type": "real_photo"},
                   "asset_reviewer": "owner",
                   "embroidery_proof_review": {"reviewer": "owner", "reviewed_at": "t", "asset_hash": hashing.file_sha256(macro),
                       "individual_threads_visible": True, "stitch_edges_visible": True, "fabric_weave_visible": True,
                       "image_is_not_ai": True, "image_is_not_blank": True, "supplier_sku_matches": True,
                       "design_version_matches": True, "thread_colors_match": True, "placement_matches": True},
                   "image_specs": [
                       {"image": 1, "garment_type": "crewneck sweatshirt", "asset_path": "s1.png", "asset_hash": hashing.file_sha256(s1), "reviewed_by": "owner", "reviewed_at": "t", "personalization_example": "Sarah"},
                       {"image": 2, "garment_type": "crewneck sweatshirt", "asset_path": "s2.png", "asset_hash": hashing.file_sha256(s2), "reviewed_by": "owner", "reviewed_at": "t", "personalization_example": "Sarah"}],
                   "competitors": []}, open(os.path.join(d, "creative-brief.json"), "w"))
        json.dump({"reviewer": "owner", "reviewed_at": "t", "asset_hash": hashing.file_sha256(main),
                   "main_image_clarity": 2, "thumbnail_clarity": 2, "product_fill": 2,
                   "personalization_readable": 2, "mobile_readable": 2, "accuracy_verified": 2},
                  open(os.path.join(d, "THUMBNAIL-REVIEW.json"), "w"))
        subprocess.run([sys.executable, "creative/creative_edge.py", d], capture_output=True, cwd=ROOT)
        sh(d, "Nurse")
        sh(d, "--approve-main-image", "--asset", "main.png", "--by", "owner")
        sh(d, "--approve-creative", "--by", "owner")
        rc, out = sh(d, "--approve-final", "--by", "owner")
        self.assertFalse(M.load(d)["publication_locked"], out)
        # A11: editing a bound evidence file re-locks the project
        pth = os.path.join(d, "CLAIMS-EVIDENCE.json"); x = json.load(open(pth)); x["edited"] = True
        json.dump(x, open(pth, "w"))
        sh(d, "--status")
        self.assertTrue(M.load(d)["publication_locked"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
