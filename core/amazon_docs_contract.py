#!/usr/bin/env python3
"""Policy contract for a FUTURE isolated public-Amazon-documentation reader (Session 6A.1).

This module is the *contract only* — it does not fetch anything. It defines the strict
rules a later, separate, human-triggered reader must obey to read official PUBLIC Amazon
materials (policy pages, help docs, public announcements that require no login). It is
deliberately kept apart from the generic research adapters and from any account path
(there is none). The capability ``AMAZON_PUBLIC_DOCUMENTATION_READ`` is off by default and
only a later dedicated adapter may turn it on after explicit owner approval.

Hard rules the future reader must honor (encoded in :func:`evaluate_documentation_fetch`):
    - GET or HEAD only; no request body; no form submission; no clicking;
    - human-triggered only; no scheduled crawler; no bulk URL queue;
    - no login, no cookies, no authentication;
    - bounded timeout + bounded response size + low frequency;
    - every fetch logged (sanitized URL, timestamp, status, content hash, policy hash);
    - every result is advisory and cannot auto-update production rules — owner review
      is required before any policy/configuration change.

When it cannot verify that an Amazon URL is official public documentation, it DENIES and
returns ``AMAZON_PUBLIC_DOCUMENTATION_UNVERIFIED`` — it never guesses. Product-detail,
search-result, review, price, inventory, seller-profile and bulk crawling are refused.
"""
import hashlib

import diagnostics as D
import network_policy as NP
import runtime_policy as RP

CAPABILITY = "AMAZON_PUBLIC_DOCUMENTATION_READ"
ALLOWED_METHODS = ("GET", "HEAD")

# There is intentionally no verified-official-docs allowlist shipped in this session, so
# every concrete Amazon URL is UNVERIFIED and denied. A later reviewed adapter supplies
# the allowlist under explicit owner approval.
VERIFIED_OFFICIAL_PREFIXES = ()   # e.g. future: ("sell.amazon.com/learn", ...)


class DocumentationFetchDecision:
    def __init__(self, *, allowed, reason_code, method, capability, policy_sha256,
                 safe_url_display, advisory_only=True, warnings=None):
        self.allowed = bool(allowed)
        self.reason_code = reason_code
        self.method = method
        self.capability = capability
        self.policy_sha256 = policy_sha256
        self.safe_url_display = safe_url_display
        self.advisory_only = advisory_only
        self.verified_as_product_fact = False   # documentation is NEVER a product fact
        self.warnings = list(warnings or [])

    def to_dict(self):
        return {"allowed": self.allowed, "reason_code": self.reason_code,
                "method": self.method, "capability": self.capability,
                "policy_sha256": self.policy_sha256, "safe_url_display": self.safe_url_display,
                "advisory_only": self.advisory_only,
                "verified_as_product_fact": self.verified_as_product_fact,
                "warnings": list(self.warnings)}


def _is_verified_official(url):
    disp = NP._host_of(url) or ""
    full = str(url or "").lower()
    return any(pref in full for pref in VERIFIED_OFFICIAL_PREFIXES) if disp else False


def evaluate_documentation_fetch(url, *, method="GET", human_triggered=False,
                                 authenticated=False, has_cookies=False,
                                 has_request_body=False, owner_approved=False, policy=None):
    """Decide whether a future reader may fetch *url* as official public documentation."""
    policy = policy or RP.load_runtime_policy()
    disp = NP.safe_destination_display(url)
    method_u = str(method or "GET").upper()
    sha = policy.connectivity_policy_sha256

    def deny(reason, **kw):
        return DocumentationFetchDecision(allowed=False, reason_code=reason, method=method_u,
                                          capability=CAPABILITY, policy_sha256=sha,
                                          safe_url_display=disp, **kw)

    # never touch an authenticated/cookie/body request — that is the account boundary
    if authenticated or has_cookies:
        return deny(D.AMAZON_ACCOUNT_ACCESS_PROHIBITED)
    if has_request_body or method_u not in ALLOWED_METHODS:
        return deny(D.AMAZON_DESTINATION_UNVERIFIED,
                    warnings=["only GET/HEAD with no body is permitted"])
    if not human_triggered:
        return deny(D.AMAZON_PUBLIC_DOCUMENTATION_UNVERIFIED,
                    warnings=["reads must be human-triggered, never scheduled/bulk"])
    if not policy.capability_enabled(CAPABILITY):
        return deny(D.CAPABILITY_NOT_ENABLED,
                    warnings=["AMAZON_PUBLIC_DOCUMENTATION_READ is disabled by default"])

    dclass = NP.classify_destination(url)
    if dclass in NP._AMAZON_ACCOUNT_CLASSES:
        return deny(D.AMAZON_ACCOUNT_ACCESS_PROHIBITED)
    if dclass == NP.AMAZON_PRODUCT_OR_SEARCH and not _is_verified_official(url):
        # product / search / review / price / inventory / seller-profile / bulk -> refused
        return deny(D.AMAZON_PUBLIC_DOCUMENTATION_UNVERIFIED)
    if not _is_verified_official(url):
        return deny(D.AMAZON_PUBLIC_DOCUMENTATION_UNVERIFIED)

    return DocumentationFetchDecision(allowed=True, reason_code="ALLOWED", method=method_u,
                                      capability=CAPABILITY, policy_sha256=sha,
                                      safe_url_display=disp)


def fetch_log_record(url, *, status=None, content=b"", timestamp="<unset>", policy=None):
    """The mandatory, secret-free per-fetch log record shape (sanitized URL, timestamp,
    status, content hash, policy hash). Advisory-only; never a verified product fact."""
    policy = policy or RP.load_runtime_policy()
    body = content if isinstance(content, (bytes, bytearray)) else str(content).encode("utf-8")
    return {
        "capability": CAPABILITY,
        "safe_url_display": NP.safe_destination_display(url),
        "timestamp": timestamp,
        "status": status,
        "content_sha256": hashlib.sha256(body).hexdigest(),
        "policy_sha256": policy.connectivity_policy_sha256,
        "advisory_only": True,
        "verified_as_product_fact": False,
        "review_status": "OWNER_REVIEW_REQUIRED",
    }
