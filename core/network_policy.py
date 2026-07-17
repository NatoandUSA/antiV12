#!/usr/bin/env python3
"""Outbound-network guard (Session 5B / ACT-016, Part C).

A single choke-point every repository-owned outbound HTTP/socket call must pass
through *before* it opens a connection. When the runtime policy is offline (the
default), the guard raises a typed error and records a secret-free diagnostic —
the connection is never attempted. Local loopback communication (the dashboard's
own Flask test client, 127.0.0.1 calls) is explicitly allowed, so this does not
break localhost operation.

This is deliberately NOT a global monkeypatch of Python networking: it guards the
app's own outbound calls without disturbing Flask's in-process test client or
localhost dashboard traffic.
"""
from urllib.parse import urlparse

import diagnostics as D
import runtime_policy as RP


class OutboundNetworkDisabledError(RuntimeError):
    """Raised when an outbound internet operation is attempted while offline."""

    def __init__(self, operation, destination=None):
        self.operation = operation
        self.destination_host = _host_of(destination) if destination else None
        self.error_code = D.OUTBOUND_NETWORK_DISABLED
        msg = f"outbound network disabled for operation {operation!r}"
        if self.destination_host:
            msg += f" (destination host {self.destination_host})"
        super().__init__(msg)


def _host_of(destination):
    """Extract a bare host from a URL, 'host:port', or plain host. Never a secret
    (query strings / credentials in a URL are dropped)."""
    if not destination:
        return None
    dest = str(destination).strip()
    if "://" in dest:
        try:
            return (urlparse(dest).hostname or "").lower() or None
        except Exception:
            return None
    # strip a trailing :port but keep IPv6 in brackets intact
    if dest.startswith("["):
        return dest.split("]")[0].lstrip("[").lower() or None
    return dest.rsplit(":", 1)[0].lower() if ":" in dest else dest.lower()


def is_loopback_destination(destination):
    """True when *destination* resolves to a loopback host name/address."""
    return RP.is_loopback_host(_host_of(destination))


def outbound_allowed(operation, destination=None, policy=None):
    """Non-raising check. True when this outbound call is permitted."""
    if destination is not None and is_loopback_destination(destination):
        return True   # local loopback is always allowed
    policy = policy or RP.load_runtime_policy()
    return bool(policy.outbound_network_enabled) and not policy.offline_only


def assert_outbound_allowed(operation, destination=None, policy=None):
    """Gate an outbound internet operation.

    Loopback destinations always pass. Otherwise, unless the policy explicitly
    enables outbound network (and is not offline), record a diagnostic and raise
    OutboundNetworkDisabledError *before* any connection is opened.
    """
    if destination is not None and is_loopback_destination(destination):
        return True
    policy = policy or RP.load_runtime_policy()
    if policy.outbound_network_enabled and not policy.offline_only:
        return True
    host = _host_of(destination)
    D.record_event(
        D.OUTBOUND_NETWORK_DISABLED,
        f"blocked outbound operation {operation!r}",
        {"operation": str(operation), "destination_host": host or "",
         "offline_only": policy.offline_only},
    )
    raise OutboundNetworkDisabledError(operation, destination)
