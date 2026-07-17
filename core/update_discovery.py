#!/usr/bin/env python3
"""Toolkit update-discovery boundary (Session 6A.1) — discovery, NOT installation.

The toolkit may, under the ``TOOLKIT_UPDATE_DISCOVERY`` capability, *discover* whether a
newer toolkit version or reference-data update exists and download metadata AFTER owner
approval. It must never silently replace code, silently change active policy rules,
install packages without approval, change production copy without review, bypass tests,
or remove rollback data.

This module encodes only that governance boundary. There is no installer here:
:func:`can_install` is always False and :func:`install` refuses. A later dedicated update
service must follow :data:`UPDATE_GOVERNANCE_STEPS`:
    discover -> display changes -> owner approves -> validate hashes -> install
    -> run regression checks -> preserve rollback.
"""
import diagnostics as D
import network_policy as NP
import runtime_policy as RP

CAPABILITY = "TOOLKIT_UPDATE_DISCOVERY"

UPDATE_GOVERNANCE_STEPS = (
    "discover", "display_changes", "owner_approves", "validate_hashes",
    "install", "run_regression_checks", "preserve_rollback",
)


def discovery_available(policy=None):
    """True only when the connectivity policy enables update discovery (read-only)."""
    policy = policy or RP.load_runtime_policy()
    return bool(policy.capability_enabled(CAPABILITY))


def discover(policy=None, source_url="https://example.invalid/updates"):
    """Read-only discovery contract. This session ships no live update source, so it
    reports 'unknown' without fetching. It NEVER installs and NEVER mutates anything."""
    policy = policy or RP.load_runtime_policy()
    if not discovery_available(policy):
        return {"available": False, "reason_code": D.CAPABILITY_NOT_ENABLED,
                "install_capable": False, "governance": list(UPDATE_GOVERNANCE_STEPS)}
    # A real adapter would evaluate + fetch metadata here via the approved client.
    return {"available": True, "update_found": None, "reason_code": "DISCOVERY_ONLY",
            "install_capable": False, "requires_owner_approval": True,
            "safe_source_display": NP.safe_destination_display(source_url),
            "governance": list(UPDATE_GOVERNANCE_STEPS)}


def can_install():
    """Update discovery can never install by itself — always False in this boundary."""
    return False


def install(*_a, **_kw):
    """Refuse: silent/auto installation is prohibited. A reviewed installer with owner
    approval, hash validation, regression checks and rollback is required."""
    raise PermissionError(D.TOOLKIT_UPDATE_INSTALL_PROHIBITED)
