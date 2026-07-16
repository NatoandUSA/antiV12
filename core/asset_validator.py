#!/usr/bin/env python3
"""
core.asset_validator (v2.3.1) — REAL image decoding, not header sniffing (P0.1).

Uses Pillow to actually decode: verify() catches truncation, then reopen + load()
loads pixels, confirms format/mode/dimensions, records size + SHA-256. A valid file
is NOT automatically publication-ready — tiny images require review. Paths are
resolved safely against the project folder; traversal is rejected (P0.7).

Statuses:
  MISSING · INVALID_FILE · VALID_FILE · QUALITY_REVIEW_REQUIRED · PUBLICATION_READY
"""
from __future__ import annotations
import os
try:
    from . import hashing
except ImportError:
    import hashing

ALLOWED_FORMATS = {"PNG":"image/png","JPEG":"image/jpeg","WEBP":"image/webp","GIF":"image/gif"}
MIN_SIDE_PUBLICATION = 1000   # Amazon guidance ~1000px+ for zoom; configurable by caller

def _safe_path(path: str, project_dir: str | None, allow_external: bool = False):
    if os.path.isabs(path):
        # P0.5: an absolute path that escapes the project is rejected by default when a
        # project_dir is given (keeps evidence inside the portable project bundle).
        if project_dir and not allow_external:
            base = os.path.realpath(project_dir); full = os.path.realpath(path)
            if not (full == base or full.startswith(base + os.sep)):
                return None
        return path
    if project_dir:
        base = os.path.realpath(project_dir)
        full = os.path.realpath(os.path.join(base, path))
        if not (full == base or full.startswith(base + os.sep)):
            return None   # traversal outside project
        return full
    return path

def validate_image(path: str | None, *, project_dir: str | None = None, source_type: str = "",
                   sku: str = "", design_version: str = "", reviewer: str = "",
                   publication_intent: bool = False, min_side: int = MIN_SIDE_PUBLICATION) -> dict:
    if not path:
        return {"status": "MISSING", "reason": "no image supplied"}
    resolved = _safe_path(path, project_dir)
    if resolved is None:
        return {"status": "INVALID_FILE", "reason": "path resolves outside the project (traversal rejected)"}
    if not os.path.exists(resolved) or not os.path.isfile(resolved):
        return {"status": "MISSING", "reason": f"file does not exist: {path}"}
    try:
        from PIL import Image
    except Exception:
        return {"status": "INVALID_FILE", "reason": "Pillow not available — cannot decode image safely"}
    try:
        with Image.open(resolved) as im:
            im.verify()                       # catches truncation/corruption
        with Image.open(resolved) as im2:
            im2.load()                        # actually decode pixels
            fmt, mode, (w, h) = im2.format, im2.mode, im2.size
            try: exif = im2.getexif().get(274)
            except Exception: exif = None
    except Exception as e:
        return {"status": "INVALID_FILE", "reason": f"file did not decode as a valid image: {e}"}
    if fmt not in ALLOWED_FORMATS:
        return {"status": "INVALID_FILE", "reason": f"decoded format {fmt} not allowed"}
    size = os.path.getsize(resolved)
    rec = {"path": resolved, "relative_path": path, "sha256": hashing.file_sha256(resolved),
           "mime_type": ALLOWED_FORMATS[fmt], "decoded_format": fmt, "width": w, "height": h,
           "file_size": size, "color_mode": mode, "exif_orientation": exif,
           "source_type": source_type, "supplier_sku": sku, "design_version": design_version,
           "reviewer": reviewer, "publication_intent": bool(publication_intent)}
    missing_meta = [k for k, v in (("source_type",source_type),("sku",sku),
                    ("design_version",design_version),("reviewer",reviewer)) if not v]
    if min(w, h) < min_side:
        rec["status"] = "QUALITY_REVIEW_REQUIRED"
        rec["reason"] = f"decoded {w}x{h} — below {min_side}px minimum for a publication main image"
    elif missing_meta:
        rec["status"] = "VALID_FILE"
        rec["reason"] = f"decoded {w}x{h}; missing metadata: {', '.join(missing_meta)}"
        rec["missing_metadata"] = missing_meta
    else:
        rec["status"] = "PUBLICATION_READY"
        rec["reason"] = f"decoded {w}x{h} with full metadata"
    return rec

def is_real_image(res: dict) -> bool:
    return res.get("status") in ("VALID_FILE", "QUALITY_REVIEW_REQUIRED", "PUBLICATION_READY")

def is_quality_acceptable(res: dict) -> bool:
    """v2.3.4 (P0.2/P0.3): a real file that also meets the configured minimum size.
    QUALITY_REVIEW_REQUIRED (too small) is a real file but NOT quality-acceptable — it must
    not qualify for main-image compliance, embroidery proof, or actual-asset scoring."""
    return res.get("status") in ("VALID_FILE", "PUBLICATION_READY")

def main():
    import argparse, json, sys
    ap = argparse.ArgumentParser(description="Validate a real image asset (decodes with Pillow).")
    ap.add_argument("path"); ap.add_argument("--project-dir", default=None)
    ap.add_argument("--source-type", default=""); ap.add_argument("--sku", default="")
    ap.add_argument("--design-version", default=""); ap.add_argument("--reviewer", default="")
    ap.add_argument("--publication-intent", action="store_true"); ap.add_argument("--min-side", type=int, default=MIN_SIDE_PUBLICATION)
    a = ap.parse_args()
    res = validate_image(a.path, project_dir=a.project_dir, source_type=a.source_type, sku=a.sku,
                         design_version=a.design_version, reviewer=a.reviewer,
                         publication_intent=a.publication_intent, min_side=a.min_side)
    print(json.dumps(res, indent=2))
    # exit codes: 0 publication-ready · 2 valid-but-incomplete/quality · 6 missing/invalid
    code = {"PUBLICATION_READY":0, "VALID_FILE":2, "QUALITY_REVIEW_REQUIRED":2,
            "MISSING":6, "INVALID_FILE":6}.get(res["status"], 6)
    sys.exit(code)

if __name__ == "__main__":
    main()
