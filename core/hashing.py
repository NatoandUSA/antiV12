#!/usr/bin/env python3
"""core.hashing — content hashes for source files and approved versions."""
from __future__ import annotations
import hashlib, os

def file_sha256(path: str) -> str | None:
    if not os.path.exists(path) or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()

def text_sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

def hash_many(paths: list[str]) -> dict[str, str]:
    out = {}
    for p in paths:
        hv = file_sha256(p)
        if hv:
            out[os.path.basename(p)] = hv
    return out
