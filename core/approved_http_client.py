#!/usr/bin/env python3
"""The single approved outbound HTTP boundary (Session 6A.1).

EVERY new production internet call goes through :func:`request`. Before a socket is
ever opened it must pass, in order: the connectivity policy (mode), destination
classification, capability authorization, and the data-classification rules — all in
``core.network_policy``. A denied request raises ``NetworkRequestDenied`` *before*
connecting. Loopback stays available in every mode; Amazon-account destinations are
always blocked.

Transport guarantees (stdlib only — no new dependency, no shell, no ``requests``):
    - an explicit finite, positive timeout is required;
    - an explicit maximum response size is required and enforced (the read is bounded);
    - TLS certificate verification is always on for https;
    - no cookie jar — cookies are never stored or sent;
    - no credential forwarding and no browser-session reuse;
    - redirects are DISABLED by default; when enabled, each hop is reclassified and
      re-authorized, so a redirect can never smuggle a request to Seller Central;
    - secret request headers are redacted in the returned/diagnostic record, and a
      request body never enters diagnostics.

This module deliberately does not know about any specific provider. Adapters pass the
capability + data classification; the policy decides.
"""
import ssl
import urllib.request
import urllib.error

import diagnostics as D
import network_policy as NP
import runtime_policy as RP

# request headers whose value is a secret and must never be echoed back in a record
_SECRET_HEADER = ("authorization", "x-api-key", "api-key", "cookie", "set-cookie",
                  "proxy-authorization", "anthropic-api-key")

DEFAULT_MAX_RESPONSE_BYTES = 5 * 1024 * 1024   # a sane bound; callers should set their own


class ApprovedHttpError(RuntimeError):
    """A transport-level failure AFTER the request was authorized (e.g. timeout)."""


class ResponseTooLargeError(ApprovedHttpError):
    """The response exceeded the caller's max_response_bytes and was aborted."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never auto-follow a redirect — the caller re-evaluates each hop explicitly."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ApprovedResponse:
    """A bounded, secret-free view of an approved HTTP response."""

    def __init__(self, *, status, headers, body, url_display, decision, truncated):
        self.status = status
        self.headers = dict(headers)
        self.body = body
        self.url_display = url_display
        self.decision = decision
        self.truncated = truncated

    def to_dict(self):
        return {"status": self.status, "url_display": self.url_display,
                "bytes": len(self.body or b""), "truncated": self.truncated,
                "decision": self.decision.to_dict()}


def _redact_headers(headers):
    out = {}
    for k, v in (headers or {}).items():
        out[k] = D.REDACTED if str(k).lower() in _SECRET_HEADER else v
    return out


def request(method, url, *, capability, timeout_seconds, max_response_bytes,
            headers=None, body=None, data_classification=NP.DATA_PUBLIC,
            owner_approved=False, human_triggered=False, follow_redirects=False,
            policy=None):
    """Perform one policy-checked outbound request. Returns an ApprovedResponse.

    Raises ``NetworkRequestDenied`` (before connecting) when the policy refuses the
    request, ``ValueError`` for an invalid timeout / size bound, and
    ``ApprovedHttpError`` for a transport failure after authorization.
    """
    if not (isinstance(timeout_seconds, (int, float)) and timeout_seconds > 0
            and timeout_seconds != float("inf")):
        raise ValueError("timeout_seconds must be a finite positive number")
    if not (isinstance(max_response_bytes, int) and max_response_bytes > 0):
        raise ValueError("max_response_bytes must be a positive integer")

    policy = policy or RP.load_runtime_policy()
    method_u = str(method or "GET").upper()
    has_cookies = any(str(k).lower() == "cookie" for k in (headers or {}))

    # authorize BEFORE opening any connection (raises if denied)
    decision = NP.assert_network_request_allowed(
        f"http:{method_u}", url, method=method_u, capability=capability,
        has_cookies=has_cookies, has_request_body=body is not None,
        data_classification=data_classification, owner_approved=owner_approved,
        human_triggered=human_triggered, policy=policy)

    disp = NP.safe_destination_display(url)
    ctx = ssl.create_default_context()          # TLS verification ON
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED

    req = urllib.request.Request(url, method=method_u,
                                 data=(body if isinstance(body, (bytes, bytearray)) else
                                       (body.encode("utf-8") if isinstance(body, str) else None)))
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    # never carry cookies
    opener = urllib.request.build_opener(_NoRedirect,
                                         urllib.request.HTTPSHandler(context=ctx))

    try:
        with opener.open(req, timeout=float(timeout_seconds)) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read(max_response_bytes + 1)
            truncated = len(raw) > max_response_bytes
            data = raw[:max_response_bytes]
            resp_headers = _redact_headers(dict(resp.headers.items()) if resp.headers else {})
    except urllib.error.HTTPError as e:
        status = e.code
        # a 3xx surfaced here (redirects disabled) — reclassify + re-authorize if asked
        if follow_redirects and status in (301, 302, 303, 307, 308):
            loc = e.headers.get("Location") if e.headers else None
            if loc:
                return request(method, loc, capability=capability,
                               timeout_seconds=timeout_seconds,
                               max_response_bytes=max_response_bytes, headers=headers,
                               body=body, data_classification=data_classification,
                               owner_approved=owner_approved, human_triggered=human_triggered,
                               follow_redirects=follow_redirects, policy=policy)
        raw = e.read(max_response_bytes + 1) if hasattr(e, "read") else b""
        truncated = len(raw) > max_response_bytes
        data = raw[:max_response_bytes]
        resp_headers = _redact_headers(dict(e.headers.items()) if e.headers else {})
    except (urllib.error.URLError, ssl.SSLError, OSError, ValueError) as e:
        raise ApprovedHttpError(f"transport failure to {disp}: {type(e).__name__}") from None

    if truncated:
        D.record_event(D.NETWORK_REQUEST_DENIED,
                       f"response from {disp} exceeded max_response_bytes; truncated",
                       {"destination": disp, "max_response_bytes": max_response_bytes})

    return ApprovedResponse(status=status, headers=resp_headers, body=data,
                            url_display=disp, decision=decision, truncated=truncated)


def redacted_request_record(method, url, *, capability, headers=None):
    """A secret-free record of a request contract (no body, redacted headers)."""
    return {"method": str(method or "GET").upper(),
            "destination": NP.safe_destination_display(url),
            "capability": capability,
            "headers": _redact_headers(headers or {})}
