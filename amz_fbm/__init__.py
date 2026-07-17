#!/usr/bin/env python3
"""AMZ FBM Toolkit — installable local package (Session 5C / ACT-018, ACT-019).

A thin, offline-first launcher package that wraps the *existing* toolkit: the local
Flask dashboard, the shared runtime policy, health endpoint, and subprocess safety
built in Sessions 1-5B. This package never adds a second server, never contacts
Amazon or Seller Central, never opens a firewall port, and never requires admin.

The single version authority is ``config.yaml`` (``tool_version``) at the project
root; ``__version__`` is derived from it so packaging, the dashboard, and the CLI
all report the same value. ``PROJECT_ROOT`` is resolved from this file's own
location so it is correct under an editable install regardless of ``sys.path``.
"""
import os
import re

# <root>/amz_fbm/__init__.py  ->  <root>
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_FALLBACK_VERSION = "0.0.0"


def get_version():
    """Read the one authoritative version from config.yaml (no yaml dependency).

    Returns a fallback only when config.yaml is missing/unreadable — the tests
    assert that, when present, this equals the dashboard's ``tool_version()``.
    """
    try:
        with open(os.path.join(PROJECT_ROOT, "config.yaml"), encoding="utf-8") as f:
            m = re.search(r'tool_version:\s*"([^"]+)"', f.read())
            return m.group(1) if m else _FALLBACK_VERSION
    except Exception:
        return _FALLBACK_VERSION


__version__ = get_version()

__all__ = ["PROJECT_ROOT", "get_version", "__version__"]
