#!/usr/bin/env python3
"""
core.paths — put every toolkit subfolder on sys.path so modules that were built
as flat scripts can cross-import (e.g. research/asin_picker importing the IP engine
in compliance/). Import this FIRST from any entry point (pipeline, tests).
"""
from __future__ import annotations
import sys, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUBDIRS = ["core", "research", "compliance", "feasibility", "economics",
           "positioning", "listing", "creative", "analytics", "reports"]

def setup() -> None:
    for d in [ROOT] + [os.path.join(ROOT, s) for s in SUBDIRS]:
        if d not in sys.path:
            sys.path.insert(0, d)

setup()
