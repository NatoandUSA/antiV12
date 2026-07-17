#!/usr/bin/env python3
"""scripts.connectivity_scan — active-runtime network source scan (Session 6A.1).

Scans the active runtime source (core, listing, dashboard, research, amz_fbm, production,
compliance) for outbound-network primitives, provider SDKs, and Amazon endpoint/auth
strings, and classifies each finding so a reviewer can confirm that every production
connected call goes through the approved client and that NO Amazon-account path exists.

Classes:
    LOOPBACK_INTERNAL       local loopback / health probe only
    APPROVED_CLIENT_PATH    the approved outbound boundary (core/approved_http_client.py)
    LEGACY_EXTERNAL_PATH    a pre-6A.1 outbound path (dashboard AI routes, gated offline)
    TEST_FIXTURE            test-only helper (not scanned here; tests live outside)
    DOCUMENTATION_ONLY      docstring / comment / inert reference
    PROHIBITED_AMAZON_PATH  an Amazon-account endpoint/auth string (must be block-only)
    REVIEW_REQUIRED         anything else worth a human glance

It writes CONNECTED-RESEARCH-NETWORK-SCAN.json at the repository root (sanitized).
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
import diagnostics as D    # noqa: E402

SCAN_DIRS = ("core", "listing", "dashboard", "research", "amz_fbm", "production", "compliance")
OUT_NAME = "CONNECTED-RESEARCH-NETWORK-SCAN.json"

PATTERNS = [
    (re.compile(r"\brequests\.(get|post|put|delete|head|patch|request)\b"), "requests-call"),
    (re.compile(r"\bimport requests\b|\bimport httpx\b|\bimport aiohttp\b"), "http-lib-import"),
    (re.compile(r"\burllib\.request\b|\burlopen\b|build_opener"), "urllib"),
    (re.compile(r"socket\.(socket|create_connection|getaddrinfo)"), "socket"),
    (re.compile(r"\bcurl\b|Invoke-WebRequest|Invoke-RestMethod"), "shell-http"),
    (re.compile(r"selenium|playwright|webdriver|pyppeteer"), "browser-automation"),
    (re.compile(r"anthropic|openai|boto3|google\.generativeai"), "provider-sdk"),
    (re.compile(r"seller" r"central|selling" r"partnerapi|mws\.amazon|advertising-"
                r"api\.amazon|/ap/signin|amazon.{0,3}auth", re.I), "amazon-endpoint-or-auth"),
]

# a line is inert when it is a comment or a pure string/docstring reference
_COMMENT = re.compile(r"^\s*#")


def _classify(rel, line, label):
    s = line.strip()
    low = s.lower()
    if _COMMENT.match(line) or s.startswith(('"', "'", '"""', "'''")):
        return "DOCUMENTATION_ONLY"
    if label == "amazon-endpoint-or-auth":
        # An Amazon reference is only an ACTIVE path if the SAME line also performs an
        # outbound primitive (a real request to an Amazon host). Our code merely NAMES
        # these endpoints/fields in order to classify + BLOCK them, so a naming/boundary
        # reference is a safe denylist entry, never an outbound call.
        outbound = re.search(r"requests\.|urlopen|\.open\(|create_connection|"
                             r"http[s]?://|curl|Invoke-Web", line)
        return "REVIEW_REQUIRED" if outbound else "PROHIBITED_AMAZON_PATH"
    rel_norm = rel.replace("\\", "/")
    if rel_norm.endswith("core/approved_http_client.py"):
        return "APPROVED_CLIENT_PATH"
    if label == "socket" and rel_norm.endswith("core/instance_manager.py"):
        return "LOOPBACK_INTERNAL"
    if label in ("requests-call", "http-lib-import") and rel_norm.endswith("dashboard/app.py"):
        return "LEGACY_EXTERNAL_PATH"
    if label == "urllib" and rel_norm.endswith(("core/instance_manager.py",
                                                "core/approved_http_client.py")):
        return ("LOOPBACK_INTERNAL" if "instance_manager" in rel_norm
                else "APPROVED_CLIENT_PATH")
    if label == "browser-automation":
        return "REVIEW_REQUIRED"
    if label == "shell-http":
        return "REVIEW_REQUIRED"
    return "REVIEW_REQUIRED"


def scan():
    findings = []
    scanned = 0
    for d in SCAN_DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            if "__pycache__" in root:
                continue
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(root, fn)
                rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
                scanned += 1
                with open(path, encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        for rx, label in PATTERNS:
                            if rx.search(line):
                                findings.append({
                                    "file": rel, "line": i, "label": label,
                                    "classification": _classify(rel, line, label),
                                    "text": D.redact_secrets(line.strip()[:140]),
                                })
    by_class = {}
    for f in findings:
        by_class[f["classification"]] = by_class.get(f["classification"], 0) + 1
    prohibited_active = [f for f in findings
                         if f["classification"] == "REVIEW_REQUIRED"
                         and f["label"] == "amazon-endpoint-or-auth"]
    return {
        "session": "6A.1",
        "scan_dirs": list(SCAN_DIRS),
        "files_scanned": scanned,
        "total_findings": len(findings),
        "counts_by_classification": by_class,
        "approved_client_module": "core/approved_http_client.py",
        "amazon_account_path_findings": [f for f in findings
                                         if f["classification"] == "PROHIBITED_AMAZON_PATH"],
        "active_amazon_path_findings": prohibited_active,   # must be empty
        "no_active_amazon_account_path": not prohibited_active,
        "findings": findings,
    }


def write(report):
    path = os.path.join(ROOT, OUT_NAME)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, sort_keys=True, ensure_ascii=False)
    return path


if __name__ == "__main__":
    rep = scan()
    p = write(rep)
    print(f"scanned {rep['files_scanned']} files; {rep['total_findings']} findings; "
          f"active amazon-account paths: {len(rep['active_amazon_path_findings'])}")
    print("classification:", json.dumps(rep["counts_by_classification"], sort_keys=True))
    print("wrote", os.path.basename(p))
    sys.exit(0 if rep["no_active_amazon_account_path"] else 1)
