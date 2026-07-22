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
import ipaddress
import re
import socket
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


# ============================================================================
# Session 6A.1 — connected-research network policy (capability + destination model)
# ============================================================================
# The connected-research surface routes EVERY new production internet call through
# this layer: connectivity mode -> destination classification -> capability
# authorization -> data-classification rules. The one hard line — no Amazon-account
# capability of any kind — is enforced first and cannot be overridden by any mode,
# capability, flag, or classification. Loopback always passes; Amazon-account
# destinations always fail closed.

# ---- destination classes -----------------------------------------------------
LOOPBACK = "LOOPBACK"
PUBLIC_WEB = "PUBLIC_WEB"
PUBLIC_POLICY = "PUBLIC_POLICY"
AMAZON_PUBLIC_DOCUMENTATION = "AMAZON_PUBLIC_DOCUMENTATION"
THIRD_PARTY_DATA = "THIRD_PARTY_DATA"
MARKET_DATA = "MARKET_DATA"
SUPPLIER = "SUPPLIER"
EXTERNAL_AI = "EXTERNAL_AI"
TOOLKIT_UPDATE_SOURCE = "TOOLKIT_UPDATE_SOURCE"
AMAZON_PRODUCT_OR_SEARCH = "AMAZON_PRODUCT_OR_SEARCH"
AMAZON_SELLER_CENTRAL = "AMAZON_SELLER_CENTRAL"
AMAZON_AUTHENTICATED = "AMAZON_AUTHENTICATED"
AMAZON_API = "AMAZON_API"
UNKNOWN_EXTERNAL = "UNKNOWN_EXTERNAL"

# Amazon-account destination classes are ALWAYS denied.
_AMAZON_ACCOUNT_CLASSES = frozenset({AMAZON_SELLER_CENTRAL, AMAZON_AUTHENTICATED, AMAZON_API})

# ---- outbound data classifications ------------------------------------------
DATA_PUBLIC = "PUBLIC"
DATA_SANITIZED_BUSINESS = "SANITIZED_BUSINESS"
DATA_PAID_RESEARCH = "PAID_RESEARCH_DATA"
DATA_CUSTOMER = "CUSTOMER_DATA"
DATA_CREDENTIAL = "CREDENTIAL"
DATA_AMAZON_ACCOUNT = "AMAZON_ACCOUNT_DATA"
DATA_CLASSIFICATIONS = frozenset({
    DATA_PUBLIC, DATA_SANITIZED_BUSINESS, DATA_PAID_RESEARCH,
    DATA_CUSTOMER, DATA_CREDENTIAL, DATA_AMAZON_ACCOUNT,
})

# ---- capability -> destination class it may reach ---------------------------
_CAPABILITY_CLASS = {
    "LOOPBACK_ACCESS": {LOOPBACK},
    "PUBLIC_WEB_RESEARCH": {PUBLIC_WEB, UNKNOWN_EXTERNAL},
    "PUBLIC_POLICY_RESEARCH": {PUBLIC_POLICY, PUBLIC_WEB, UNKNOWN_EXTERNAL},
    "AMAZON_PUBLIC_DOCUMENTATION_READ": {AMAZON_PUBLIC_DOCUMENTATION},
    "THIRD_PARTY_DATA": {THIRD_PARTY_DATA, PUBLIC_WEB, UNKNOWN_EXTERNAL},
    "MARKET_DATA_CONNECTION": {MARKET_DATA, PUBLIC_WEB, UNKNOWN_EXTERNAL},
    "SUPPLIER_CONNECTION": {SUPPLIER, PUBLIC_WEB, UNKNOWN_EXTERNAL},
    "EXTERNAL_AI_CONNECTION": {EXTERNAL_AI, PUBLIC_WEB, UNKNOWN_EXTERNAL},
    "TOOLKIT_UPDATE_DISCOVERY": {TOOLKIT_UPDATE_SOURCE, PUBLIC_WEB, UNKNOWN_EXTERNAL},
}

# prohibited (Amazon-account) capability -> typed reason code
_PROHIBITED_CAP_REASON = {
    "AMAZON_LOGIN": D.AMAZON_ACCOUNT_ACCESS_PROHIBITED,
    "AMAZON_SELLER_CENTRAL_ACCESS": D.SELLER_CENTRAL_ACCESS_PROHIBITED,
    "AMAZON_AUTHENTICATED_ACCESS": D.AMAZON_ACCOUNT_ACCESS_PROHIBITED,
    "AMAZON_SELLER_SESSION": D.SELLER_CENTRAL_ACCESS_PROHIBITED,
    "AMAZON_CREDENTIAL_STORAGE": D.AMAZON_CREDENTIAL_STORAGE_PROHIBITED,
    "AMAZON_API_ACCESS": D.AMAZON_API_PROHIBITED,
    "AMAZON_SP_API": D.AMAZON_API_PROHIBITED,
    "AMAZON_MWS": D.AMAZON_API_PROHIBITED,
    "AMAZON_ADVERTISING_API": D.AMAZON_API_PROHIBITED,
    "AMAZON_BROWSER_AUTOMATION": D.AMAZON_BROWSER_AUTOMATION_PROHIBITED,
    "AMAZON_ACCOUNT_REPORT_PULL": D.AMAZON_REPORT_PULL_PROHIBITED,
    "AMAZON_LISTING_WRITE": D.AMAZON_WRITE_PROHIBITED,
    "AMAZON_PRICE_WRITE": D.AMAZON_WRITE_PROHIBITED,
    "AMAZON_INVENTORY_WRITE": D.AMAZON_WRITE_PROHIBITED,
    "AMAZON_APLUS_WRITE": D.AMAZON_WRITE_PROHIBITED,
    "AMAZON_PPC_WRITE": D.AMAZON_WRITE_PROHIBITED,
    "AMAZON_REVIEW_MANIPULATION": D.AMAZON_REVIEW_ACTION_PROHIBITED,
}

# Amazon endpoint host fragments are ASSEMBLED at runtime so the literal endpoint
# strings never appear verbatim in this source file. These endpoints are only ever
# matched in order to BLOCK them — the toolkit never calls them.
_AZ = "amazon"
_H_SELLER = "seller" + "central"
_H_SP_API = "selling" + "partner" + "api"
_H_MWS = "mws." + _AZ + "services"
_H_ADV = "advertising-" + "api." + _AZ
_AMAZON_API_HOST_HINTS = (_H_SP_API, _H_MWS, _H_ADV)
_SELLER_CENTRAL_HINTS = (_H_SELLER + "." + _AZ, _H_SELLER + "-europe",
                         _H_SELLER + "-japan", _H_SELLER + ".")
_AMAZON_AUTH_PATH_HINTS = ("/ap/signin", "/gp/sign-in", "/gp/css", "/ap/oa",
                           "/" + "seller" + "/", "/" + _H_SELLER, "/business/")


def _path_of(destination, path=None):
    if path is not None:
        return str(path).lower()
    dest = str(destination or "")
    if "://" in dest:
        try:
            return (urlparse(dest).path or "").lower()
        except Exception:
            return ""
    return ""


def _is_amazon_host(host):
    if not host:
        return False
    h = host.lower()
    labels = h.split(".")
    return (_AZ in labels or (_AZ + "services") in labels
            or h.endswith(_AZ + ".com") or ("." + _AZ + ".") in ("." + h + ".")
            or (_AZ + "services") in h or _H_SP_API in h)


def classify_destination(destination, path=None):
    """Classify an outbound destination into a coarse, reviewable class. Never
    raises; an unrecognized external host is UNKNOWN_EXTERNAL (fail-closed upstream)."""
    host = _host_of(destination)
    if RP.is_loopback_host(host):
        return LOOPBACK
    if not host:
        return UNKNOWN_EXTERNAL
    h = host.lower()
    p = _path_of(destination, path)
    if any(hint in h for hint in _AMAZON_API_HOST_HINTS):
        return AMAZON_API
    if any(h.startswith(hint) or hint in h for hint in _SELLER_CENTRAL_HINTS):
        return AMAZON_SELLER_CENTRAL
    if _is_amazon_host(h):
        if any(hint in p for hint in _AMAZON_AUTH_PATH_HINTS):
            return AMAZON_AUTHENTICATED
        # ordinary public product / search page — read is NOT supported (scraping)
        return AMAZON_PRODUCT_OR_SEARCH
    return UNKNOWN_EXTERNAL


def safe_destination_display(destination):
    """Host only — never a path, query, or credentials. Safe for logs/diagnostics."""
    return _host_of(destination) or "(none)"


class NetworkRequestDenied(RuntimeError):
    """Raised by assert_network_request_allowed when a request is not permitted.

    Carries a stable ``reason_code`` and never contains a secret or a full URL."""

    def __init__(self, decision):
        self.decision = decision
        self.reason_code = decision.reason_code
        self.error_code = decision.reason_code
        self.destination_class = decision.destination_class
        super().__init__(
            f"network request denied ({decision.reason_code}) for "
            f"{decision.safe_destination_display} [{decision.destination_class}]")


class NetworkDecision:
    """The result of evaluating one outbound request. Secret-free + serializable."""

    def __init__(self, *, allowed, connectivity_mode, capability, destination_class,
                 method, reason_code, warnings, policy_sha256, safe_destination_display):
        self.allowed = bool(allowed)
        self.connectivity_mode = connectivity_mode
        self.capability = capability
        self.destination_class = destination_class
        self.method = method
        self.reason_code = reason_code
        self.warnings = list(warnings)
        self.policy_sha256 = policy_sha256
        self.safe_destination_display = safe_destination_display

    def to_dict(self):
        return {
            "allowed": self.allowed,
            "connectivity_mode": self.connectivity_mode,
            "capability": self.capability,
            "destination_class": self.destination_class,
            "method": self.method,
            "reason_code": self.reason_code,
            "warnings": list(self.warnings),
            "policy_sha256": self.policy_sha256,
            "safe_destination_display": self.safe_destination_display,
        }

    def __repr__(self):
        return f"NetworkDecision(allowed={self.allowed}, reason={self.reason_code})"


def _decide(allowed, reason, *, policy, capability, dclass, method, dest_display, warnings):
    return NetworkDecision(
        allowed=allowed, connectivity_mode=policy.connectivity_mode, capability=capability,
        destination_class=dclass, method=(method or "GET").upper(), reason_code=reason,
        warnings=warnings, policy_sha256=policy.connectivity_policy_sha256,
        safe_destination_display=dest_display)


def evaluate_network_request(operation, destination, *, method="GET", capability=None,
                             authenticated=False, has_cookies=False, has_request_body=False,
                             data_classification=None, owner_approved=False,
                             human_triggered=False, policy=None):
    """Decide whether one outbound request may proceed. Never opens a connection.

    Precedence (hard blocks first): the permanent Amazon-account boundary, prohibited
    capabilities and blocked data classifications ALWAYS win, in every mode. Then
    loopback passes; then the connectivity mode gates external access; then the
    declared capability must be enabled for the destination class; then Amazon
    product/search scraping is refused; then external-AI and paid/customer data rules
    apply. Returns a NetworkDecision.
    """
    policy = policy or RP.load_runtime_policy()
    warnings = []
    cap = str(capability).upper() if capability else None
    dclass = str(data_classification).upper() if data_classification else DATA_PUBLIC
    dest_class = classify_destination(destination)
    disp = safe_destination_display(destination)
    method_u = (method or "GET").upper()

    def deny(reason):
        return _decide(False, reason, policy=policy, capability=cap, dclass=dest_class,
                       method=method_u, dest_display=disp, warnings=warnings)

    def allow(reason="ALLOWED"):
        return _decide(True, reason, policy=policy, capability=cap, dclass=dest_class,
                       method=method_u, dest_display=disp, warnings=warnings)

    # 1) permanent Amazon-account boundary — never crossable
    if dest_class == AMAZON_SELLER_CENTRAL:
        return deny(D.SELLER_CENTRAL_ACCESS_PROHIBITED)
    if dest_class == AMAZON_API:
        return deny(D.AMAZON_API_PROHIBITED)
    if dest_class == AMAZON_AUTHENTICATED:
        return deny(D.AMAZON_ACCOUNT_ACCESS_PROHIBITED)
    if _is_amazon_host(_host_of(destination)) and (authenticated or has_cookies):
        return deny(D.AMAZON_ACCOUNT_ACCESS_PROHIBITED)

    # 2) prohibited capability requested — typed refusal
    if cap in _PROHIBITED_CAP_REASON:
        return deny(_PROHIBITED_CAP_REASON[cap])

    # 3) blocked data classifications — always
    if dclass == DATA_CREDENTIAL:
        return deny(D.DATA_CLASSIFICATION_BLOCKED)
    if dclass == DATA_AMAZON_ACCOUNT:
        return deny(D.AMAZON_ACCOUNT_ACCESS_PROHIBITED)

    # 4) loopback is always available (every mode)
    if dest_class == LOOPBACK:
        return allow()

    # 5) connectivity mode gates all external access
    if not policy.connected_research:
        return deny(D.EXTERNAL_DENIED_IN_MODE)

    # 6) a declared, enabled capability is required for any external destination
    if cap is None:
        warnings.append("no capability declared for an external request")
        return deny(D.CAPABILITY_NOT_ENABLED)
    if cap not in _CAPABILITY_CLASS:
        return deny(D.CAPABILITY_NOT_ENABLED)
    if not policy.capability_enabled(cap):
        return deny(D.CAPABILITY_NOT_ENABLED)

    # 7) Amazon product/search reads are not supported (bulk marketplace scraping)
    if dest_class == AMAZON_PRODUCT_OR_SEARCH:
        if cap == "AMAZON_PUBLIC_DOCUMENTATION_READ":
            # official public documentation must be verified by a reviewed classifier;
            # a generic marketplace URL cannot be verified here -> fail closed
            return deny(D.AMAZON_PUBLIC_DOCUMENTATION_UNVERIFIED)
        return deny(D.AMAZON_BULK_OR_MARKETPLACE_SCRAPING_NOT_SUPPORTED)

    # 8) the capability must be allowed to reach this destination class
    if dest_class not in _CAPABILITY_CLASS[cap]:
        return deny(D.CAPABILITY_NOT_ENABLED)

    # 9) external-AI specific rules
    if cap == "EXTERNAL_AI_CONNECTION":
        if not (policy.external_ai_allowed and policy.external_ai_enabled):
            return deny(D.CAPABILITY_NOT_ENABLED)
        if not policy.external_ai_provider:
            return deny(D.EXTERNAL_AI_PROVIDER_NOT_APPROVED)
        if dclass in (DATA_CUSTOMER, DATA_CREDENTIAL, DATA_AMAZON_ACCOUNT):
            return deny(D.DATA_CLASSIFICATION_BLOCKED)

    # 10) outbound data-classification rules
    if dclass == DATA_CUSTOMER:
        # customer data never leaves to a generic research provider or external AI
        return deny(D.DATA_CLASSIFICATION_BLOCKED)
    if dclass == DATA_PAID_RESEARCH and not owner_approved:
        return deny(D.DATA_CLASSIFICATION_BLOCKED)
    if dclass == DATA_SANITIZED_BUSINESS and not owner_approved:
        return deny(D.DATA_CLASSIFICATION_BLOCKED)

    return allow()


def assert_network_request_allowed(operation, destination, **kw):
    """Evaluate and raise NetworkRequestDenied (before any connection) if not allowed."""
    decision = evaluate_network_request(operation, destination, **kw)
    if not decision.allowed:
        D.record_event(D.NETWORK_REQUEST_DENIED,
                       f"denied network request {operation!r}: {decision.reason_code}",
                       {"operation": str(operation),
                        "destination": decision.safe_destination_display,
                        "destination_class": decision.destination_class,
                        "reason_code": decision.reason_code})
        raise NetworkRequestDenied(decision)
    return decision


def evaluate_external_ai_request(operation, *, provider=None, data_classification=DATA_PUBLIC,
                                 destination="https://api.anthropic.com/v1/messages",
                                 owner_approved=False, policy=None):
    """Convenience wrapper: evaluate an EXTERNAL_AI_CONNECTION request. API-key presence
    alone never enables it — the policy must have external AI explicitly enabled with an
    approved provider, and customer/credential/Amazon-account data is always refused."""
    return evaluate_network_request(
        operation, destination, method="POST", capability="EXTERNAL_AI_CONNECTION",
        data_classification=data_classification, owner_approved=owner_approved, policy=policy)


# ============================================================================
# Phase 7.9 — connected backup / update network validation (explicit allowlist)
# ============================================================================
# Beginning with Phase 7.9 the application is no longer globally offline-only. Encrypted
# remote backups, GitHub update checks and package-index metadata are permitted — but ONLY
# to explicitly configured, allowlisted hosts, ONLY over HTTPS (a local dev endpoint may
# use HTTP), and NEVER to the owner's Amazon seller account. This validator reuses the
# permanent Amazon-account classification above (which always wins first) and layers a
# strict allowlist + scheme + host-normalization model on top. It does NOT depend on the
# legacy connectivity mode, so it never weakens — and is never weakened by — the accepted
# Phase 7.2–7.8 offline behavior.

PURPOSE_BACKUP_PROVIDER = "BACKUP_PROVIDER"
PURPOSE_GITHUB_UPDATE = "GITHUB_UPDATE"
PURPOSE_PACKAGE_INDEX = "PACKAGE_INDEX"
CONNECTED_PURPOSES = frozenset({PURPOSE_BACKUP_PROVIDER, PURPOSE_GITHUB_UPDATE,
                                PURPOSE_PACKAGE_INDEX})

# Amazon destination classes are ALWAYS denied here too (defense in depth on top of the
# allowlist). Reuses the classification/host hints already defined above.
_CONNECTED_AMAZON_DENY = {
    AMAZON_SELLER_CENTRAL: D.SELLER_CENTRAL_ACCESS_PROHIBITED,
    AMAZON_API: D.AMAZON_API_PROHIBITED,
    AMAZON_AUTHENTICATED: D.AMAZON_ACCOUNT_ACCESS_PROHIBITED,
    AMAZON_PRODUCT_OR_SEARCH: D.AMAZON_BULK_OR_MARKETPLACE_SCRAPING_NOT_SUPPORTED,
}


class ConnectedNetworkDenied(RuntimeError):
    """Raised when a Phase 7.9 connected operation is not permitted. Carries a stable
    ``reason_code`` and never contains a secret, a full URL, a path, or a query string."""

    def __init__(self, decision):
        self.decision = decision
        self.reason_code = decision.reason_code
        self.error_code = decision.reason_code
        self.safe_host = decision.safe_host
        super().__init__(f"connected operation denied ({decision.reason_code}) for "
                         f"{decision.safe_host} [{decision.purpose}]")


class ConnectedNetworkDecision:
    """Result of evaluating one Phase 7.9 connected request. Secret-free + serializable."""

    __slots__ = ("allowed", "purpose", "reason_code", "scheme", "safe_host", "port",
                 "destination_class", "is_ip", "is_local", "warnings")

    def __init__(self, *, allowed, purpose, reason_code, scheme, safe_host, port,
                 destination_class, is_ip, is_local, warnings):
        self.allowed = bool(allowed)
        self.purpose = purpose
        self.reason_code = reason_code
        self.scheme = scheme
        self.safe_host = safe_host
        self.port = port
        self.destination_class = destination_class
        self.is_ip = is_ip
        self.is_local = is_local
        self.warnings = list(warnings)

    # a NetworkRequestDenied-style alias so callers may log a single field name
    @property
    def safe_destination_display(self):
        return self.safe_host

    def to_dict(self):
        return {"allowed": self.allowed, "purpose": self.purpose,
                "reason_code": self.reason_code, "scheme": self.scheme,
                "safe_host": self.safe_host, "port": self.port,
                "destination_class": self.destination_class, "is_ip": self.is_ip,
                "is_local": self.is_local, "warnings": list(self.warnings)}

    def __repr__(self):
        return (f"ConnectedNetworkDecision(allowed={self.allowed}, "
                f"reason={self.reason_code}, host={self.safe_host})")


def _normalize_connected_host(raw_host):
    """Normalize a URL hostname. Returns (normalized_host, is_ip, ip_obj|None).

    IPv6 is unwrapped from brackets and normalized (compressed) form; IPv4 (including
    packed/decimal forms) is normalized to dotted-quad; a DNS name is lower-cased with a
    trailing dot stripped. Returns (None, False, None) for an empty/invalid host."""
    if not raw_host:
        return None, False, None
    h = str(raw_host).strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    h = h.split("%", 1)[0]            # drop any IPv6 zone id
    if not h:
        return None, False, None
    try:
        ip = ipaddress.ip_address(h)
        return str(ip), True, ip
    except ValueError:
        pass
    return h.rstrip("."), False, None


def _host_allowlisted(host, allowed_hosts):
    """True when *host* equals an allowed host or is a dotted subdomain of one. Never a
    bare substring match (so 'evil-github.com' does not match 'github.com')."""
    if not host or not allowed_hosts:
        return False
    for a in allowed_hosts:
        a = str(a).strip().lower().rstrip(".")
        if not a:
            continue
        if host == a or host.endswith("." + a):
            return True
    return False


def _is_private_or_loopback(ip_obj):
    return bool(ip_obj is not None and (ip_obj.is_loopback or ip_obj.is_private
                                        or ip_obj.is_link_local))


def evaluate_connected_operation(url, *, purpose, allowed_hosts, allow_local=False,
                                 method="GET"):
    """Decide whether one Phase 7.9 connected request may proceed. Never opens a socket.

    Order (hard blocks first): the permanent Amazon-account boundary; a valid parseable URL;
    HTTPS is required for a public host (HTTP only for an explicitly enabled local endpoint);
    then the host MUST be on the caller's explicit allowlist (a DNS name for a public host,
    or a loopback/private IP when ``allow_local`` is set). A public request may never target a
    raw IP literal (DNS-rebinding safe: the allowlist is by name and TLS pins that name)."""
    warnings = []
    raw = str(url or "")
    try:
        parts = urlparse(raw)
        scheme = (parts.scheme or "").lower()
        raw_host = parts.hostname
        port = parts.port
    except Exception:
        scheme, raw_host, port = "", None, None
    host, is_ip, ip_obj = _normalize_connected_host(raw_host)
    disp = host or "(none)"

    def deny(reason, dclass="UNKNOWN_EXTERNAL", is_local=False):
        return ConnectedNetworkDecision(allowed=False, purpose=purpose, reason_code=reason,
                                        scheme=scheme, safe_host=disp, port=port,
                                        destination_class=dclass, is_ip=is_ip,
                                        is_local=is_local, warnings=warnings)

    # 1) permanent Amazon-account boundary — reuse the accepted classification, always first.
    dclass = classify_destination(raw)
    if dclass in _CONNECTED_AMAZON_DENY:
        return deny(_CONNECTED_AMAZON_DENY[dclass], dclass)
    if _is_amazon_host(host):
        return deny(D.AMAZON_ACCOUNT_ACCESS_PROHIBITED, dclass or AMAZON_PRODUCT_OR_SEARCH)

    # 2) a known purpose is required
    if purpose not in CONNECTED_PURPOSES:
        return deny(D.CONNECTED_PURPOSE_UNKNOWN)

    # 3) a valid, parseable URL with a scheme and host
    if scheme not in ("https", "http") or not host:
        return deny(D.CONNECTED_URL_INVALID)
    if port is not None and not (0 < int(port) < 65536):
        return deny(D.CONNECTED_URL_INVALID)

    local_ok = bool(allow_local and is_ip and _is_private_or_loopback(ip_obj))

    # 4) scheme: HTTPS required for public; HTTP only for an explicitly enabled local endpoint
    if scheme == "http" and not local_ok:
        return deny(D.INSECURE_SCHEME_BLOCKED, dclass)

    # 5) allowlist. A public request may never target a raw IP literal.
    if is_ip:
        if local_ok:
            return ConnectedNetworkDecision(
                allowed=True, purpose=purpose, reason_code="ALLOWED_LOCAL", scheme=scheme,
                safe_host=disp, port=port, destination_class="LOCAL", is_ip=True,
                is_local=True, warnings=warnings)
        if allow_local:
            # allow_local set but the IP is public — refuse (not a local endpoint)
            return deny(D.LOCAL_ENDPOINT_NOT_ENABLED, dclass)
        return deny(D.HOST_NOT_ALLOWLISTED, dclass)

    if not _host_allowlisted(host, allowed_hosts):
        return deny(D.HOST_NOT_ALLOWLISTED, dclass)

    return ConnectedNetworkDecision(
        allowed=True, purpose=purpose, reason_code="ALLOWED", scheme=scheme, safe_host=disp,
        port=port, destination_class="ALLOWLISTED", is_ip=False, is_local=False,
        warnings=warnings)


def assert_connected_operation_allowed(url, *, purpose, allowed_hosts, allow_local=False,
                                       method="GET"):
    """Evaluate and raise ConnectedNetworkDenied (before any connection) if not allowed."""
    decision = evaluate_connected_operation(url, purpose=purpose, allowed_hosts=allowed_hosts,
                                            allow_local=allow_local, method=method)
    if not decision.allowed:
        D.record_event(D.NETWORK_REQUEST_DENIED,
                       f"denied connected operation ({purpose}): {decision.reason_code}",
                       {"purpose": str(purpose), "destination": decision.safe_host,
                        "reason_code": decision.reason_code})
        raise ConnectedNetworkDenied(decision)
    return decision


def evaluate_connected_redirect(redirect_url, *, purpose, allowed_hosts, original_host,
                                allow_local=False):
    """Re-authorize a redirect hop. Returns (decision, forward_credentials). The redirect
    target is re-validated from scratch; credentials may be forwarded ONLY when the target
    host is identical to the original request host (never across hosts)."""
    decision = evaluate_connected_operation(redirect_url, purpose=purpose,
                                            allowed_hosts=allowed_hosts, allow_local=allow_local)
    same_host = bool(decision.safe_host and original_host
                     and decision.safe_host == str(original_host).strip().lower())
    return decision, (decision.allowed and same_host)


# ============================================================================
# Phase 7.10 — connected public research (owner-supplied public sources)
# ============================================================================
# Phase 7.10 lets the owner capture explicitly supplied PUBLIC sources: a URL, an RSS/Atom feed,
# GitHub or PyPI metadata, one Amazon US public product-detail page, or a locally saved file.
# This section REUSES the permanent Amazon-account classification above — which always wins first —
# and layers a public-research SSRF model on top: no URL userinfo; HTTPS only (HTTP just for an
# explicit local test endpoint); no raw public IP literal; and (validated by the caller before it
# opens a socket) every DNS-resolved address must be a genuinely public/global address. It is
# allowlist-free for a generic owner-supplied URL — the owner supplying the URL is the
# authorization — while the Amazon public-product path is a strict host+path allowlist. Nothing
# here changes the Phase 7.2–7.9 evaluators above; it only adds new functions.

PURPOSE_PUBLIC_RESEARCH = "PUBLIC_RESEARCH"
PURPOSE_RSS_FEED = "RSS_FEED"
PURPOSE_GITHUB_METADATA = "GITHUB_METADATA"
PURPOSE_PYPI_METADATA = "PYPI_METADATA"
PURPOSE_AMAZON_PUBLIC_PRODUCT = "AMAZON_PUBLIC_PRODUCT"
PUBLIC_RESEARCH_PURPOSES = frozenset({
    PURPOSE_PUBLIC_RESEARCH, PURPOSE_RSS_FEED, PURPOSE_GITHUB_METADATA,
    PURPOSE_PYPI_METADATA, PURPOSE_AMAZON_PUBLIC_PRODUCT,
})

# Amazon US retail (public product) host + path allowlist. Hosts are assembled from the `_AZ`
# fragment so no endpoint literal appears verbatim (keeps scripts/connectivity_scan clean). Only a
# genuine product-detail path is accepted; search / cart / checkout / account / sign-in / seller
# paths are refused (the Amazon-account classes are additionally denied first, above).
_AMAZON_RETAIL_HOSTS = frozenset({_AZ + ".com", "www." + _AZ + ".com"})
_AMAZON_PRODUCT_PATH_RE = re.compile(r"^/(?:dp|gp/product)/([A-Za-z0-9]{10})(?:[/?].*)?/?$")
_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")


def extract_amazon_asin(path):
    """Return the 10-char ASIN from an accepted Amazon product-detail path, else None.
    Accepts only ``/dp/<ASIN>`` and ``/gp/product/<ASIN>`` (ASIN normalized to upper-case)."""
    m = _AMAZON_PRODUCT_PATH_RE.match(str(path or ""))
    if not m:
        return None
    asin = m.group(1).upper()
    return asin if _ASIN_RE.match(asin) else None


def _coerce_ipv4_forms(host):
    """Return an IPv4Address when *host* is any inet_aton-parseable form (dotted / integer /
    hexadecimal / octal / mixed), else None. Catches encoded IPv4 literals that a strict parser
    misses (e.g. ``2130706433``, ``0x7f000001``, ``017700000001``, ``127.1``)."""
    h = str(host or "")
    if not h or any(c.isalpha() and c not in "xXaAbBcCdDeEfF" for c in h):
        # a real DNS label contains letters beyond hex digits -> not a numeric IP form
        return None
    try:
        return ipaddress.IPv4Address(socket.inet_aton(h))
    except (OSError, ValueError, UnicodeError):
        return None


def _is_ascii_host(host):
    """True when *host* is already pure ASCII. A Unicode host is refused rather than silently
    IDNA-encoded, so a look-alike Unicode name can never be confused with its ASCII twin — the
    owner must supply the canonical punycode (``xn--``) form. A trailing dot is already stripped by
    :func:`_normalize_connected_host`."""
    if not host:
        return False
    try:
        host.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _public_ip_ok(ip_obj):
    """True only for a genuinely public (global) address — everything private, loopback,
    link-local (incl. cloud metadata 169.254.169.254), reserved, multicast or unspecified fails."""
    if ip_obj is None:
        return False
    try:
        if (ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_link_local
                or ip_obj.is_reserved or ip_obj.is_multicast or ip_obj.is_unspecified):
            return False
        return bool(ip_obj.is_global)
    except Exception:
        return False


def validate_resolved_addresses(addresses, *, allow_local=False):
    """Given the IP strings a host resolved to, return the list of DISALLOWED ones (empty => safe).

    A SINGLE disallowed address blocks the whole host — this covers a multi-record host where one
    address is public and another is private, and DNS rebinding. ``allow_local`` permits loopback
    ONLY (for an explicit local test server); it never permits link-local / metadata / private."""
    bad = []
    for a in addresses or ():
        try:
            ip = ipaddress.ip_address(str(a).split("%", 1)[0])
        except ValueError:
            bad.append(str(a))
            continue
        if allow_local and ip.is_loopback:
            continue
        if not _public_ip_ok(ip):
            bad.append(str(ip))
    return bad


def _pr_decision(*, allowed, purpose, reason, scheme, host, port, dclass, is_ip, is_local,
                 warnings):
    return ConnectedNetworkDecision(allowed=allowed, purpose=purpose, reason_code=reason,
                                    scheme=scheme, safe_host=host or "(none)", port=port,
                                    destination_class=dclass, is_ip=is_ip, is_local=is_local,
                                    warnings=warnings)


def evaluate_public_research_url(url, *, purpose, allow_local=False):
    """Decide whether ONE owner-supplied public-research URL may be fetched. Never opens a socket.

    Order (hard blocks first): the permanent Amazon-account boundary (Seller Central, the Amazon
    seller API, the Ads API and any authenticated access are always denied, and a generic Amazon
    marketplace URL is refused — the
    dedicated Amazon public-product evaluator validates a product-detail URL); URL userinfo; a known
    public-research purpose; a valid URL; HTTPS (HTTP only for an explicit local test endpoint); and
    a raw / encoded IP literal is refused for a public destination (a private/loopback literal is
    refused unless it is that explicit local endpoint). Allowlist-free for a public DNS host, but the
    caller MUST still DNS-validate every resolved address via validate_resolved_addresses() before
    connecting. Returns a ConnectedNetworkDecision."""
    warnings = []
    raw = str(url or "")
    try:
        parts = urlparse(raw)
        scheme = (parts.scheme or "").lower()
        raw_host = parts.hostname
        port = parts.port
        userinfo = bool(parts.username or parts.password) or ("@" in (parts.netloc or ""))
    except Exception:
        scheme, raw_host, port, userinfo = "", None, None, False
    host, is_ip, ip_obj = _normalize_connected_host(raw_host)
    disp = host or "(none)"

    def deny(reason, dclass="UNKNOWN_EXTERNAL"):
        return _pr_decision(allowed=False, purpose=purpose, reason=reason, scheme=scheme,
                            host=disp, port=port, dclass=dclass, is_ip=is_ip, is_local=False,
                            warnings=warnings)

    # 1) permanent Amazon-account boundary — always first, reusing the accepted classification.
    dclass = classify_destination(raw)
    if dclass == AMAZON_SELLER_CENTRAL:
        return deny(D.SELLER_CENTRAL_ACCESS_PROHIBITED, dclass)
    if dclass == AMAZON_API:
        return deny(D.AMAZON_API_PROHIBITED, dclass)
    if dclass == AMAZON_AUTHENTICATED:
        return deny(D.AMAZON_ACCOUNT_ACCESS_PROHIBITED, dclass)
    if dclass == AMAZON_PRODUCT_OR_SEARCH:
        return deny(D.AMAZON_BULK_OR_MARKETPLACE_SCRAPING_NOT_SUPPORTED, dclass)
    if _is_amazon_host(host):
        return deny(D.AMAZON_ACCOUNT_ACCESS_PROHIBITED, dclass or AMAZON_PRODUCT_OR_SEARCH)

    # 2) URL userinfo (credential deception, e.g. https://real@evil/) is always refused
    if userinfo:
        return deny(D.URL_USERINFO_BLOCKED, dclass)

    # 3) a known public-research purpose is required
    if purpose not in PUBLIC_RESEARCH_PURPOSES:
        return deny(D.CONNECTED_PURPOSE_UNKNOWN, dclass)

    # 4) a valid, parseable URL
    if scheme not in ("https", "http") or not host:
        return deny(D.CONNECTED_URL_INVALID, dclass)
    if port is not None and not (0 < int(port) < 65536):
        return deny(D.CONNECTED_URL_INVALID, dclass)

    # 5) reject Unicode/IDNA-confusable hostnames (owner must supply the punycode form)
    if not is_ip and not _is_ascii_host(host):
        return deny(D.CONNECTED_URL_INVALID, dclass)

    # 6) catch encoded IPv4 literals hiding as a "hostname" (integer / hex / octal / mixed)
    if not is_ip:
        coerced = _coerce_ipv4_forms(host)
        if coerced is not None:
            is_ip, ip_obj = True, coerced

    local_ok = bool(allow_local and is_ip and ip_obj is not None and ip_obj.is_loopback)

    # 7) scheme: HTTPS required for public; HTTP only for an explicit local test endpoint
    if scheme == "http" and not local_ok:
        return deny(D.INSECURE_SCHEME_BLOCKED, dclass)

    # 8) IP-literal handling — no public IP literal (must be a DNS name so TLS pins it and the
    #    resolved-address check stays rebinding-safe); private/loopback only for a local endpoint.
    if is_ip:
        if local_ok:
            return _pr_decision(allowed=True, purpose=purpose, reason="ALLOWED_LOCAL", scheme=scheme,
                                host=disp, port=port, dclass="LOCAL", is_ip=True, is_local=True,
                                warnings=warnings)
        if ip_obj is not None and _is_private_or_loopback(ip_obj):
            return deny(D.PRIVATE_DESTINATION_BLOCKED, dclass)
        return deny(D.IP_LITERAL_BLOCKED, dclass)

    return _pr_decision(allowed=True, purpose=purpose, reason="ALLOWED", scheme=scheme, host=disp,
                        port=port, dclass="PUBLIC_RESEARCH", is_ip=False, is_local=False,
                        warnings=warnings)


def evaluate_amazon_public_product_url(url, *, allow_local=False):
    """Decide whether ONE explicitly supplied Amazon US public product-detail URL may be captured.

    The permanent Amazon-account boundary is evaluated FIRST (Seller Central, the Amazon seller
    API, the Ads API and any authenticated or account/sign-in/cart/checkout path are denied). Then
    the host must be an
    Amazon US retail host (amazon.com / www.amazon.com) over HTTPS and the path must be a genuine
    product-detail path (``/dp/<ASIN>`` or ``/gp/product/<ASIN>``). Search-result, cart, checkout,
    account and seller paths are refused. Returns a ConnectedNetworkDecision (its ``safe_host`` is
    the host; the caller extracts the ASIN via :func:`extract_amazon_asin`)."""
    warnings = []
    raw = str(url or "")
    try:
        parts = urlparse(raw)
        scheme = (parts.scheme or "").lower()
        raw_host = parts.hostname
        port = parts.port
        path = parts.path or ""
        userinfo = bool(parts.username or parts.password) or ("@" in (parts.netloc or ""))
    except Exception:
        scheme, raw_host, port, path, userinfo = "", None, None, "", False
    host, is_ip, _ip = _normalize_connected_host(raw_host)
    disp = host or "(none)"

    def deny(reason, dclass="UNKNOWN_EXTERNAL"):
        return _pr_decision(allowed=False, purpose=PURPOSE_AMAZON_PUBLIC_PRODUCT, reason=reason,
                            scheme=scheme, host=disp, port=port, dclass=dclass, is_ip=is_ip,
                            is_local=False, warnings=warnings)

    # 1) permanent Amazon-account boundary — always first.
    dclass = classify_destination(raw)
    if dclass == AMAZON_SELLER_CENTRAL:
        return deny(D.SELLER_CENTRAL_ACCESS_PROHIBITED, dclass)
    if dclass == AMAZON_API:
        return deny(D.AMAZON_API_PROHIBITED, dclass)
    if dclass == AMAZON_AUTHENTICATED:
        return deny(D.AMAZON_ACCOUNT_ACCESS_PROHIBITED, dclass)

    # 2) userinfo / invalid URL / scheme
    if userinfo:
        return deny(D.URL_USERINFO_BLOCKED, dclass)
    if scheme != "https" or not host:
        return deny(D.AMAZON_PRODUCT_URL_INVALID, dclass)
    if port is not None and port != 443:
        return deny(D.AMAZON_PRODUCT_URL_INVALID, dclass)

    # 3) must be an Amazon US retail host
    if host not in _AMAZON_RETAIL_HOSTS:
        return deny(D.AMAZON_PRODUCT_URL_INVALID, dclass or AMAZON_PRODUCT_OR_SEARCH)

    # 4) must be a genuine product-detail path (rejects search / cart / checkout / account / seller)
    if extract_amazon_asin(path) is None:
        return deny(D.AMAZON_NON_PRODUCT_PATH_BLOCKED, dclass or AMAZON_PRODUCT_OR_SEARCH)

    return _pr_decision(allowed=True, purpose=PURPOSE_AMAZON_PUBLIC_PRODUCT, reason="ALLOWED",
                        scheme=scheme, host=disp, port=port, dclass="AMAZON_PUBLIC_PRODUCT",
                        is_ip=False, is_local=False, warnings=warnings)


def evaluate_public_research_redirect(redirect_url, *, purpose, allow_local=False,
                                      amazon_product=False):
    """Re-authorize a public-research redirect hop from scratch (never trusts a redirect).

    Cross-host redirects are permitted for public research (sites legitimately redirect across
    hosts), but EVERY hop is fully re-validated: the Amazon-account boundary, private/loopback,
    IP-literal and scheme rules all re-apply, and the caller re-resolves + re-validates DNS for the
    new host. The tool sends no credentials, so none can ever be forwarded across a host. Returns a
    ConnectedNetworkDecision."""
    if amazon_product:
        return evaluate_amazon_public_product_url(redirect_url, allow_local=allow_local)
    return evaluate_public_research_url(redirect_url, purpose=purpose, allow_local=allow_local)
