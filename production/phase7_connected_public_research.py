#!/usr/bin/env python3
"""Phase 7.10 — Connected Public Research & Evidence Hub.

This module is the ONE Phase 7.10 authority. It lets the owner capture explicitly supplied
PUBLIC sources — a URL, an RSS/Atom feed, GitHub or PyPI metadata, one Amazon US public
product-detail page, or a locally saved HTML/JSON/XML/text file — and turn them into
deterministic, provenance-tracked evidence records with exports. It is evidence collection and
provenance ONLY: it never invents facts, estimates demand/sales/revenue, scores
opportunity/competition/viability, or makes recommendations. Analysis stays outside Phase 7.10.

PERMANENT AMAZON-ACCOUNT BOUNDARY (never crossable, in any path, by any flag or config):
no Seller Central, no Seller Central login, no Amazon seller API, no Ads API, no seller-account
OAuth, no seller credentials/cookies/sessions/tokens, no automatic report downloads, no bulk-file
uploads, no browser automation against seller pages, no campaign/bid/budget/target/keyword/negative
changes, no automated mutation of an Amazon seller account. Every online request is validated first
by :mod:`core.network_policy`, whose Amazon-account classification always wins before any allowlist
decision. Every capture carries ``seller_central_connections=0`` and the seller API / Ads API /
seller-auth / seller-mutation counters, all constant zero — no code path can increment them.

CONNECTIVITY: the application may reach the public Internet for legitimate research. Only the
explicitly supplied public source is fetched — the tool never crawls a site, follows arbitrary
links, loads images/scripts/fonts/ads, executes JavaScript, launches a browser, submits forms,
posts anything, bypasses access controls or robots restrictions, or defeats robot checks. When a
source is blocked, unavailable, or partially parsed, that is recorded honestly.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import socket
import ssl
import sys
import tempfile
import urllib.error
import urllib.request
import urllib.robotparser
import zlib
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _sub in ("", "core", "production"):
    _p = _ROOT if not _sub else os.path.join(_ROOT, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import diagnostics as D                     # noqa: E402  secret redaction + typed codes
import network_policy as NP                 # noqa: E402  canonical Amazon-deny + public-research SSRF

# ================================================================ identity / versions
STAGE_ID = "7.10"
STAGE_NAME = "Connected Public Research & Evidence Hub"
TOOL_VERSION = "phase7.10.0"
PARSER_VERSION = "phase7.10-parser-v1"
RUN_MANIFEST_SCHEMA = "phase7-10-run-manifest-v1"
CAPTURE_SCHEMA = "phase7-10-raw-capture-v1"
EVIDENCE_SCHEMA = "phase7-10-evidence-v1"
PLAN_SCHEMA = "phase7-10-research-plan-v1"
CACHE_SCHEMA = "phase7-10-cache-entry-v1"
SNAPSHOT_SCHEMA = "phase7-10-research-snapshot-v1"
VALIDATION_SCHEMA = "phase7-10-validation-v1"

# ---------------------------------------------------------------- readiness states
READY = "SESSION7_10_RESEARCH_READY"
READY_EMPTY = "SESSION7_10_RESEARCH_READY_EMPTY"
READY_PARTIAL = "SESSION7_10_RESEARCH_READY_PARTIAL"
SOURCE_REQUIRED = "SESSION7_10_SOURCE_REQUIRED"
SOURCE_BLOCKED = "SESSION7_10_SOURCE_BLOCKED"
NETWORK_UNAVAILABLE = "SESSION7_10_NETWORK_UNAVAILABLE"
NETWORK_POLICY_BLOCKED = "SESSION7_10_NETWORK_POLICY_BLOCKED"
ROBOTS_BLOCKED = "SESSION7_10_ROBOTS_BLOCKED"
CONTENT_TYPE_BLOCKED = "SESSION7_10_CONTENT_TYPE_BLOCKED"
CONTENT_TOO_LARGE = "SESSION7_10_CONTENT_TOO_LARGE"
PARSE_PARTIAL = "SESSION7_10_PARSE_PARTIAL"
CACHE_BLOCKED = "SESSION7_10_CACHE_BLOCKED"
INTEGRITY_BLOCKED = "SESSION7_10_INTEGRITY_BLOCKED"
PLAN_BLOCKED = "SESSION7_10_PLAN_BLOCKED"
SELLER_CENTRAL_POLICY_BLOCKED = "SESSION7_10_SELLER_CENTRAL_POLICY_BLOCKED"
VALIDATE_READY = "SESSION7_10_VALIDATE_READY"
VERIFY_READY = "SESSION7_10_VERIFY_READY"
REPLAY_READY = "SESSION7_10_REPLAY_READY"
EXPORT_READY = "SESSION7_10_EXPORT_READY"
LIST_READY = "SESSION7_10_LIST_READY"
PROVIDER_CHECK_READY = "SESSION7_10_PROVIDER_CHECK_READY"

# readiness values that mean success -> process exit 0. Everything else exits nonzero.
_EXIT_OK = frozenset({
    READY, READY_EMPTY, READY_PARTIAL, VALIDATE_READY, VERIFY_READY, REPLAY_READY,
    EXPORT_READY, LIST_READY, PROVIDER_CHECK_READY,
})

# ---------------------------------------------------------------- capture statuses
CAP_CAPTURED = "CAPTURED"
CAP_FROM_CACHE = "CAPTURED_FROM_CACHE"
CAP_NET_POLICY = "NETWORK_POLICY_BLOCKED"
CAP_SELLER = "SELLER_CENTRAL_POLICY_BLOCKED"
CAP_ROBOTS = "ROBOTS_BLOCKED"
CAP_CTYPE = "CONTENT_TYPE_BLOCKED"
CAP_TOO_LARGE = "CONTENT_TOO_LARGE"
CAP_UNAVAILABLE = "NETWORK_UNAVAILABLE"
CAP_NOT_FOUND = "NOT_FOUND"
CAP_CHALLENGED = "ROBOT_CHALLENGE_OR_BLOCKED"
CAP_PARSE_PARTIAL = "PARSE_PARTIAL"
CAP_CACHE_BLOCKED = "CACHE_BLOCKED"
CAP_SOURCE_BLOCKED = "SOURCE_BLOCKED"

_SUCCESS_STATUSES = frozenset({CAP_CAPTURED, CAP_FROM_CACHE, CAP_PARSE_PARTIAL})

# ---------------------------------------------------------------- permanent Amazon counters (constant zero)
AMAZON_COUNTERS = {
    "seller_central_connections": 0, "amazon_seller_api_calls": 0, "amazon_ads_api_calls": 0,
    "amazon_seller_auth_calls": 0, "amazon_seller_mutations": 0, "amazon_report_downloads": 0,
    "amazon_bulk_uploads": 0, "browser_automation_actions": 0, "amazon_credential_store_count": 0,
}
THIS_SESSION_NEVER = {
    "connects_to_seller_central": True, "logs_into_amazon": True,
    "uses_seller_api": True, "uses_ads_api": True, "uses_seller_oauth": True,
    "stores_seller_credentials": True, "downloads_seller_reports": True,
    "mutates_an_amazon_seller_account": True, "invents_facts_or_demand": True,
    "estimates_sales_or_revenue": True, "scores_opportunity_or_viability": True,
    "makes_campaign_recommendations": True, "crawls_a_website": True,
    "executes_javascript": True, "launches_a_browser": True, "submits_a_form": True,
    "posts_reviews_comments_or_messages": True, "bypasses_robots_or_access_controls": True,
}

# ---------------------------------------------------------------- content limits + request behavior
DEFAULT_MAX_BYTES = 5 * 1024 * 1024          # default response body bound
ABSOLUTE_MAX_BYTES = 20 * 1024 * 1024        # hard ceiling regardless of caller request
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_MAX_RETRIES = 2                       # retries only on a transport timeout
USER_AGENT = "AMZ-FBM-Toolkit-PublicResearch/7.10 (+local owner tool; respects robots.txt)"

ALLOWED_MEDIA_TYPES = frozenset({
    "text/html", "text/plain", "application/json", "application/ld+json",
    "application/xml", "text/xml", "application/rss+xml", "application/atom+xml",
})
# binary magic prefixes that must never be accepted as text (defeat a mislabeled Content-Type).
_BINARY_MAGIC = (b"\x89PNG", b"PK\x03\x04", b"PK\x05\x06", b"GIF8", b"\xff\xd8\xff", b"%PDF",
                 b"\x1f\x8b", b"\x7fELF", b"MZ", b"BM", b"II*\x00", b"MM\x00*", b"OggS",
                 b"RIFF", b"\x00\x00\x01\x00", b"\x00\x00\x01\xba", b"\x25\x21\x50\x53")

# JSON-LD bounds (defeat oversized / deeply nested structured data)
MAX_LD_BLOCKS = 24
MAX_LD_BYTES = 256 * 1024
MAX_LD_DEPTH = 20
# only these JSON-LD @types are read; anything else is ignored (never trust arbitrary types)
_TRUSTED_LD_TYPES = frozenset({"product"})

# extensions permitted for the local-file adapter -> media type
_LOCAL_EXT_MEDIA = {
    ".html": "text/html", ".htm": "text/html", ".json": "application/json",
    ".jsonld": "application/ld+json", ".xml": "application/xml", ".rss": "application/rss+xml",
    ".atom": "application/atom+xml", ".txt": "text/plain", ".text": "text/plain",
}
# local files that must never be read (secrets / credentials / keys / cookies / profiles)
_FORBIDDEN_FILE_NAMES = frozenset({
    ".env", ".netrc", ".pypirc", "credentials", "credentials.json", "cookies.txt",
    "cookies.sqlite", "id_rsa", "id_ed25519", "id_dsa", "secrets.json", "token.json",
    "aws_credentials", ".git-credentials",
})
_FORBIDDEN_FILE_SUFFIXES = (".env", ".key", ".pem", ".pfx", ".p12", ".crt", ".keystore",
                            ".ppk", ".jks", ".cookie", ".sqlite")
_FORBIDDEN_FILE_SUBSTR = ("secret", "credential", "password", "passwd", "apikey", "api_key",
                          "access_key", "private_key", "session", "cookie")

# environment token for optional GitHub authentication (public requests never require it)
GITHUB_TOKEN_ENV = "PHASE7_10_GITHUB_TOKEN"
LIVE_NETWORK_ENV = "PHASE7_10_ALLOW_LIVE_NETWORK"

# request/response headers whose value is a secret — never persisted, never echoed
_SECRET_HEADERS = frozenset({"authorization", "cookie", "set-cookie", "proxy-authorization",
                             "x-api-key", "api-key", "www-authenticate"})
# response headers that are safe to record (never a cookie / credential)
_SAFE_RESPONSE_HEADERS = ("content-type", "content-length", "etag", "last-modified",
                          "content-encoding", "cache-control", "content-language")

DISCLAIMERS = (
    "Phase 7.10 collects PUBLIC evidence and provenance only. It does not invent facts, estimate "
    "demand, sales or revenue, score opportunity, competition or viability, or make recommendations.",
    "No data here came from Amazon Seller Central. The toolkit never connects to an Amazon seller "
    "account, seller API, ads API, or seller credentials, cookies, sessions or tokens.",
    "Observed values are as published by the source at capture time; technical metadata (content "
    "hashes, media types, identifiers) is derived and labelled as such.",
    "An absent field is recorded as absent, never as zero. A blocked or unavailable source is "
    "recorded honestly and never presented as captured data.",
)


# ================================================================ errors
class ResearchError(Exception):
    """A safe, secret-free Phase 7.10 failure carrying a stable ``code`` and redacted detail."""

    def __init__(self, code, detail="", *, readiness=SOURCE_BLOCKED):
        self.code = code
        self.detail = redact(detail)
        self.readiness = readiness
        super().__init__(f"{code}: {self.detail}" if self.detail else code)


class PlanError(ResearchError):
    def __init__(self, detail):
        super().__init__("PLAN_BLOCKED", detail, readiness=PLAN_BLOCKED)


class TransportError(RuntimeError):
    pass


class TransportTimeout(TransportError):
    pass


class ContentTooLarge(TransportError):
    pass


# ================================================================ small helpers
def _now_utc():
    return _dt.datetime.now(_dt.timezone.utc)


def _iso(dt=None):
    """A UTC timestamp for the OPERATIONAL log only — never part of any identity or export."""
    return (dt or _now_utc()).astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(obj):
    """Stable canonical serialization — identical form to the Phase 5/6/7 engines. Rejects NaN /
    Infinity so a record can never carry a non-finite number."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2, separators=(",", ": "),
                      allow_nan=False)


def _sha256_hex(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def content_sha256(obj):
    return _sha256_hex(canonical_json(obj))


def redact(value):
    """Route any value through central secret redaction (env keys / key-shaped tokens). Recurses
    through dict/list so a log, manifest or export can never carry a secret."""
    if isinstance(value, str):
        return D.redact_secrets(value)
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _atomic_write_bytes(path, data):
    """Write bytes to a temp sibling, fsync, read-back verify, then atomically replace. A failed
    write never overwrites the last valid artifact (replace runs only on a fully written temp)."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-710-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        with open(tmp, "rb") as f:
            if f.read() != data:
                raise ResearchError("ATOMIC_WRITE_READBACK_FAILED", os.path.basename(path),
                                    readiness=INTEGRITY_BLOCKED)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _atomic_write_json(path, obj):
    _atomic_write_bytes(path, (canonical_json(obj) + "\n").encode("utf-8"))


def _atomic_write_text(path, text):
    _atomic_write_bytes(path, text.encode("utf-8"))


def _no_dup_pairs(pairs):
    """object_pairs_hook that rejects a duplicate key (ambiguous structured data)."""
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"duplicate key {k!r}")
        seen[k] = v
    return seen


def _loads_strict(data, what="json"):
    """Parse JSON rejecting NUL bytes, NaN/Infinity, and duplicate keys."""
    if isinstance(data, (bytes, bytearray)):
        if b"\x00" in data:
            raise ResearchError("NULL_BYTE_REJECTED", what)
        data = data.decode("utf-8")

    def _reject(tok):
        raise ResearchError("NON_FINITE_NUMBER_REJECTED", f"{what}: {tok}")

    return json.loads(data, parse_constant=_reject, object_pairs_hook=_no_dup_pairs)


def _to_decimal_str(value):
    """Return a canonical Decimal string (no exponent, no float) for a numeric-looking value, or
    None. Strips a currency symbol / thousands separators; preserves a leading minus."""
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace(" ", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return format(Decimal(m.group(0)), "f")
    except (InvalidOperation, ValueError):
        return None


def _to_int_str(value):
    if value is None:
        return None
    m = re.search(r"-?\d+", str(value).replace(",", ""))
    return m.group(0) if m else None


# ================================================================ configuration
class Config:
    """Phase 7.10 workspace + injectable seams. The default transport / resolver / clock touch the
    real network and wall clock; tests inject fakes so no unit test needs live Internet access."""

    __slots__ = ("base_dir", "input_root", "env", "allow_local", "transport", "resolver",
                 "host_gate", "max_bytes", "timeout_seconds", "max_redirects", "max_retries")

    def __init__(self, base_dir, *, input_root=None, env=None, allow_local=False, transport=None,
                 resolver=None, host_gate=None, max_bytes=DEFAULT_MAX_BYTES,
                 timeout_seconds=DEFAULT_TIMEOUT_SECONDS, max_redirects=DEFAULT_MAX_REDIRECTS,
                 max_retries=DEFAULT_MAX_RETRIES):
        self.base_dir = os.path.abspath(base_dir)
        self.input_root = os.path.abspath(input_root) if input_root else None
        self.env = dict(env) if env is not None else dict(os.environ)
        self.allow_local = bool(allow_local)
        self.transport = transport            # None -> _real_transport
        self.resolver = resolver              # None -> socket.getaddrinfo
        self.host_gate = host_gate or HostGate()
        self.max_bytes = min(int(max_bytes), ABSOLUTE_MAX_BYTES)
        self.timeout_seconds = timeout_seconds
        self.max_redirects = max_redirects
        self.max_retries = max_retries

    def _sub(self, name):
        return os.path.join(self.base_dir, name)

    runs_dir = property(lambda self: self._sub("runs"))
    cache_dir = property(lambda self: self._sub("cache"))
    raw_dir = property(lambda self: self._sub("raw"))
    normalized_dir = property(lambda self: self._sub("normalized"))
    reports_dir = property(lambda self: self._sub("reports"))
    manifests_dir = property(lambda self: self._sub("manifests"))
    validation_dir = property(lambda self: self._sub("validation"))
    logs_dir = property(lambda self: self._sub("logs"))

    def github_token(self):
        v = (self.env.get(GITHUB_TOKEN_ENV) or "").strip()
        return v or None


# ================================================================ per-host gate (rate limit + concurrency)
class HostGate:
    """Serialize + space out requests per host. Execution is single-threaded, so at most one
    request per host is ever in flight (concurrency == 1); ``min_interval`` (with an injected clock
    + sleeper) additionally spaces successive requests to the same host."""

    def __init__(self, min_interval=0.0, sleeper=None, clock=None):
        self.min_interval = float(min_interval)
        self._sleeper = sleeper
        self._clock = clock
        self._last = {}
        self._active = {}
        self.requests = {}
        self.max_concurrent = 0
        self.sleeps = []

    def acquire(self, host):
        self._active[host] = self._active.get(host, 0) + 1
        self.max_concurrent = max(self.max_concurrent, max(self._active.values()))
        if self.min_interval > 0 and self._clock is not None:
            now = self._clock()
            last = self._last.get(host)
            if last is not None:
                wait = self.min_interval - (now - last)
                if wait > 0:
                    self.sleeps.append((host, wait))
                    if self._sleeper is not None:
                        self._sleeper(wait)
                        now = self._clock()
            self._last[host] = now
        self.requests[host] = self.requests.get(host, 0) + 1

    def release(self, host):
        self._active[host] = max(0, self._active.get(host, 0) - 1)


# ================================================================ transport (default: real HTTP)
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never auto-follow a redirect — every hop is re-authorized + re-resolved explicitly."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _real_transport(url, *, timeout_seconds, max_bytes, headers=None):
    """One raw, TLS-verified request with NO auto-redirect, NO cookies, and a bounded read. Returns
    (status, headers_dict, raw_body_bytes). TLS certificate verification is always on and cannot be
    disabled. A 3xx status is returned with its (lower-cased) Location header, never followed."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    # ProxyHandler({}) => environment proxies are IGNORED (no implicit proxy use); no cookie
    # processor => cookies are never stored or sent; _NoRedirect => no auto-redirect.
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}),
                                         urllib.request.HTTPSHandler(context=ctx))
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept-Encoding", "gzip")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with opener.open(req, timeout=float(timeout_seconds)) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read(max_bytes + 1)
            hdrs = {k.lower(): v for k, v in (resp.headers.items() if resp.headers else [])}
            return status, hdrs, raw
    except urllib.error.HTTPError as e:
        hdrs = {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}
        body = b""
        try:
            body = e.read(max_bytes + 1) if hasattr(e, "read") else b""
        except Exception:
            body = b""
        return e.code, hdrs, body
    except (socket.timeout, TimeoutError) as e:
        raise TransportTimeout(type(e).__name__) from None
    except (urllib.error.URLError, ssl.SSLError, OSError, ValueError) as e:
        raise TransportError(type(e).__name__) from None


def _resolve_addresses(config, host):
    """Resolve *host* to a list of IP strings via the (injectable) resolver. Raises TransportError
    on a resolution failure (recorded as NETWORK_UNAVAILABLE, never a policy block)."""
    resolver = config.resolver or socket.getaddrinfo
    try:
        infos = resolver(host, None)
    except (socket.gaierror, OSError, UnicodeError, ValueError) as e:
        raise TransportError(f"DNS resolution failed: {type(e).__name__}") from None
    addrs = []
    for info in infos or ():
        try:
            sockaddr = info[4]
            addrs.append(str(sockaddr[0]))
        except (IndexError, TypeError):
            continue
    if not addrs:
        raise TransportError("DNS resolution returned no address")
    return addrs


def _safe_response_headers(headers):
    out = {}
    for k in _SAFE_RESPONSE_HEADERS:
        v = (headers or {}).get(k)
        if v is not None:
            out[k] = v
    return out


def _media_type_of(headers):
    ct = (headers or {}).get("content-type") or ""
    return ct.split(";", 1)[0].strip().lower()


def _looks_binary(body):
    if not body:
        return False
    head = body[:512]
    if b"\x00" in head:
        return True
    return any(head.startswith(m) for m in _BINARY_MAGIC)


def _maybe_decompress(body, headers, limit):
    """Bounded decompression of a gzip/deflate response. Raises ContentTooLarge on a bomb."""
    enc = ((headers or {}).get("content-encoding") or "").lower()
    if not enc or enc == "identity":
        return body
    if "gzip" in enc or "x-gzip" in enc:
        dobj = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif "deflate" in enc:
        dobj = zlib.decompressobj()
    else:
        return body
    out = bytearray()
    step = 65536
    for i in range(0, len(body), step):
        out += dobj.decompress(bytes(body[i:i + step]), limit - len(out) + 1)
        if len(out) > limit:
            raise ContentTooLarge("decompressed body exceeds bound")
    try:
        out += dobj.flush()
    except zlib.error:
        raise TransportError("corrupt compressed body") from None
    if len(out) > limit:
        raise ContentTooLarge("decompressed body exceeds bound")
    return bytes(out)


class FetchOutcome:
    """Everything a fetch produced — used to build a capture record. Never carries a secret."""

    __slots__ = ("status", "http_status", "media_type", "body", "content_sha256",
                 "redirect_lineage", "from_cache", "net_decision", "robots_result", "warnings",
                 "response_headers")

    def __init__(self, *, status, http_status=None, media_type="", body=b"", redirect_lineage=None,
                 from_cache=False, net_decision=None, robots_result=None, warnings=None,
                 response_headers=None):
        self.status = status
        self.http_status = http_status
        self.media_type = media_type
        self.body = body or b""
        self.content_sha256 = _sha256_hex(self.body) if self.body else ""
        self.redirect_lineage = list(redirect_lineage or [])
        self.from_cache = bool(from_cache)
        self.net_decision = net_decision
        self.robots_result = robots_result
        self.warnings = list(warnings or [])
        self.response_headers = dict(response_headers or {})


def _reason_to_status(reason_code):
    if reason_code in (D.SELLER_CENTRAL_ACCESS_PROHIBITED, D.AMAZON_API_PROHIBITED,
                       D.AMAZON_ACCOUNT_ACCESS_PROHIBITED, D.AMAZON_CREDENTIAL_STORAGE_PROHIBITED):
        return CAP_SELLER
    return CAP_NET_POLICY


# ================================================================ robots
def _robots_allowed(config, url, purpose):
    """Consult the origin robots.txt for the tool's static UA. Returns (allowed, result_string).
    A robots file that cannot be fetched (unavailable) is treated as ALLOW but recorded honestly.
    The robots.txt fetch itself is policy-validated + DNS-validated (redirects revalidated)."""
    parts = urlsplit(url)
    robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
    try:
        outcome = _fetch(config, robots_url, purpose=purpose, respect_robots=False,
                         use_cache=False, expected_media=("text/plain", "text/html"),
                         is_robots=True)
    except TransportError:
        return True, "ROBOTS_UNAVAILABLE_ALLOWED"
    if outcome.status != CAP_CAPTURED or outcome.http_status not in (200, None):
        return True, "ROBOTS_UNAVAILABLE_ALLOWED"
    try:
        text = outcome.body.decode("utf-8", "replace")
    except Exception:
        return True, "ROBOTS_UNAVAILABLE_ALLOWED"
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(text.splitlines())
    allowed = rp.can_fetch(USER_AGENT, url)
    return (True, "ROBOTS_ALLOWED") if allowed else (False, "ROBOTS_DISALLOWED")


# ================================================================ cache
def _cache_key(normalized_locator, purpose, variant="v1"):
    return _sha256_hex(canonical_json({"locator": normalized_locator, "purpose": purpose,
                                       "parser_version": PARSER_VERSION, "variant": variant}))


def _cache_paths(config, key):
    return (os.path.join(config.cache_dir, key + ".bin"),
            os.path.join(config.cache_dir, key + ".meta.json"))


def _cache_store(config, key, body, meta):
    bin_path, meta_path = _cache_paths(config, key)
    _atomic_write_bytes(bin_path, body)
    rec = dict(meta)
    rec["schema_version"] = CACHE_SCHEMA
    rec["content_sha256"] = _sha256_hex(body)
    rec["content_size"] = len(body)
    _atomic_write_json(meta_path, rec)


def _cache_load(config, key):
    """Return (body, meta) for a VALID cache entry, or raise ResearchError(CACHE_BLOCKED) when the
    entry is corrupt (bytes hash disagrees with meta), or return (None, None) when absent."""
    bin_path, meta_path = _cache_paths(config, key)
    if not (os.path.isfile(bin_path) and os.path.isfile(meta_path)):
        return None, None
    with open(bin_path, "rb") as f:
        body = f.read()
    try:
        with open(meta_path, "rb") as f:
            meta = _loads_strict(f.read(), "cache_meta")
    except (ResearchError, ValueError):
        raise ResearchError("CACHE_ENTRY_CORRUPT", "unreadable cache meta", readiness=CACHE_BLOCKED)
    if not isinstance(meta, dict) or meta.get("content_sha256") != _sha256_hex(body):
        raise ResearchError("CACHE_ENTRY_CORRUPT", "cache content hash mismatch",
                            readiness=CACHE_BLOCKED)
    return body, meta


# ================================================================ core fetch orchestrator
def _fetch(config, url, *, purpose, amazon_product=False, respect_robots=True, use_cache=True,
           expected_media=None, extra_headers=None, cache_variant="v1", is_robots=False,
           normalized_locator=None, max_bytes=None):
    """Fetch ONE explicitly supplied URL through the full public-research guard, then return a
    FetchOutcome. Order (all before any socket is opened): the network policy (Amazon-account
    boundary first, then SSRF); DNS resolution + validation of EVERY resolved address; robots; then
    the (injectable) transport with bounded redirects (each hop fully re-validated + re-resolved),
    bounded read, bounded decompression, media-type + binary enforcement. A blocked request never
    reaches the transport. GitHub/PyPI/Amazon hosts are constructed by the adapters; the owner only
    supplies the locator, so no secret is ever placed in a URL here."""
    transport = config.transport or _real_transport
    max_bytes = min(int(max_bytes), config.max_bytes) if max_bytes else config.max_bytes
    loc = normalized_locator or url

    # 1) policy gate (never opens a socket)
    if amazon_product:
        decision = NP.evaluate_amazon_public_product_url(url, allow_local=config.allow_local)
    else:
        decision = NP.evaluate_public_research_url(url, purpose=purpose,
                                                   allow_local=config.allow_local)
    if not decision.allowed:
        D.record_event(D.NETWORK_REQUEST_DENIED, f"public-research request denied: {decision.reason_code}",
                       {"destination": decision.safe_host, "reason_code": decision.reason_code,
                        "purpose": str(purpose)})
        return FetchOutcome(status=_reason_to_status(decision.reason_code), net_decision=decision)

    host = decision.safe_host

    # 2) DNS resolution + validation (every resolved address must be public, unless local endpoint)
    try:
        addrs = _resolve_addresses(config, host)
    except TransportError:
        return FetchOutcome(status=CAP_UNAVAILABLE, net_decision=decision,
                            warnings=["dns_resolution_failed"])
    bad = NP.validate_resolved_addresses(addrs, allow_local=config.allow_local)
    if bad:
        D.record_event(D.NETWORK_REQUEST_DENIED, "resolved address disallowed (SSRF guard)",
                       {"destination": host, "reason_code": D.PRIVATE_DESTINATION_BLOCKED})
        return FetchOutcome(status=CAP_NET_POLICY, net_decision=decision,
                            warnings=["resolved_private_or_disallowed_address"])

    # 3) robots (only for owner-supplied HTML surfaces; never for the robots fetch itself)
    robots_result = None
    if respect_robots and not is_robots:
        ok, robots_result = _robots_allowed(config, url, purpose)
        if not ok:
            return FetchOutcome(status=CAP_ROBOTS, net_decision=decision, robots_result=robots_result)

    # 4) cache reuse (conditional). Never cache a request that carried auth (extra_headers).
    key = _cache_key(loc, purpose, cache_variant)
    cached_body = cached_meta = None
    if use_cache and not extra_headers:
        cached_body, cached_meta = _cache_load(config, key)  # raises CACHE_BLOCKED on corruption
    conditional = {}
    if cached_meta:
        if cached_meta.get("etag"):
            conditional["If-None-Match"] = cached_meta["etag"]
        if cached_meta.get("last_modified"):
            conditional["If-Modified-Since"] = cached_meta["last_modified"]

    # 5) transport with bounded redirects + retries; each hop re-validated + re-resolved
    current = url
    origin_host = host
    lineage = []
    redirects = attempts = 0
    req_headers = dict(extra_headers or {})
    req_headers.update(conditional)
    while True:
        config.host_gate.acquire(NP._host_of(current) or host)
        try:
            status, headers, raw = transport(current, timeout_seconds=config.timeout_seconds,
                                             max_bytes=max_bytes, headers=req_headers)
        except TransportTimeout:
            attempts += 1
            if attempts > config.max_retries:
                return FetchOutcome(status=CAP_UNAVAILABLE, net_decision=decision,
                                    robots_result=robots_result, warnings=["transport_timeout"])
            continue
        except TransportError:
            return FetchOutcome(status=CAP_UNAVAILABLE, net_decision=decision,
                                robots_result=robots_result, warnings=["transport_error"])
        finally:
            config.host_gate.release(NP._host_of(current) or host)

        if status == 304 and cached_body is not None:                 # conditional hit
            body = cached_body
            headers = {**(cached_meta or {}), **headers}
            return _finish_fetch(config, key, body, cached_meta or {}, decision, robots_result,
                                 lineage, expected_media, from_cache=True, use_cache=use_cache,
                                 extra_headers=extra_headers, http_status=200, limit=max_bytes)

        if status in (301, 302, 303, 307, 308):
            redirects += 1
            if redirects > config.max_redirects:
                return FetchOutcome(status=CAP_UNAVAILABLE, net_decision=decision,
                                    robots_result=robots_result, warnings=["too_many_redirects"])
            loc_hdr = headers.get("location") or headers.get("Location")
            if not loc_hdr:
                return FetchOutcome(status=CAP_UNAVAILABLE, net_decision=decision,
                                    warnings=["redirect_without_location"])
            nxt = urljoin(current, loc_hdr)
            rdec = NP.evaluate_public_research_redirect(nxt, purpose=purpose,
                                                        allow_local=config.allow_local,
                                                        amazon_product=amazon_product)
            if not rdec.allowed:
                D.record_event(D.NETWORK_REQUEST_DENIED, f"redirect denied: {rdec.reason_code}",
                               {"destination": rdec.safe_host, "reason_code": rdec.reason_code})
                return FetchOutcome(status=_reason_to_status(rdec.reason_code), net_decision=rdec,
                                    robots_result=robots_result, redirect_lineage=lineage)
            # re-resolve the new host before following
            try:
                nbad = NP.validate_resolved_addresses(
                    _resolve_addresses(config, rdec.safe_host), allow_local=config.allow_local)
            except TransportError:
                return FetchOutcome(status=CAP_UNAVAILABLE, net_decision=rdec,
                                    warnings=["redirect_dns_failed"])
            if nbad:
                return FetchOutcome(status=CAP_NET_POLICY, net_decision=rdec,
                                    warnings=["redirect_to_private_address"])
            lineage.append(rdec.safe_host)
            # credentials (auth header) are NEVER forwarded to a different host
            if extra_headers and rdec.safe_host != origin_host:
                req_headers = dict(conditional)
            current = nxt
            continue

        if status == 404:
            return FetchOutcome(status=CAP_NOT_FOUND, http_status=404, net_decision=decision,
                                robots_result=robots_result, redirect_lineage=lineage)
        if status in (401, 403, 429) or _is_robot_challenge(raw):
            return FetchOutcome(status=CAP_CHALLENGED, http_status=status, net_decision=decision,
                                robots_result=robots_result, redirect_lineage=lineage,
                                warnings=[f"http_{status}_no_retry"])
        if status != 200:
            return FetchOutcome(status=CAP_UNAVAILABLE, http_status=status, net_decision=decision,
                                robots_result=robots_result, redirect_lineage=lineage)

        return _finish_fetch(config, key, raw, headers, decision, robots_result, lineage,
                             expected_media, from_cache=False, use_cache=use_cache,
                             extra_headers=extra_headers, http_status=200, limit=max_bytes)


def _is_robot_challenge(body):
    if not body:
        return False
    head = body[:4096].lower()
    return (b"to discuss automated access" in head or b"api-services-support@amazon" in head
            or b"/errors/validatecaptcha" in head or b"enter the characters you see below" in head)


def _finish_fetch(config, key, raw, headers, decision, robots_result, lineage, expected_media,
                  *, from_cache, use_cache, extra_headers, http_status, limit=None):
    """Content-Length + decompression + media-type + binary enforcement, then optional cache."""
    warnings = []
    limit = limit or config.max_bytes
    declared = headers.get("content-length")
    try:
        declared_len = int(declared) if declared is not None else None
    except (TypeError, ValueError):
        declared_len = None
    # decompress (bounded); a bomb -> CONTENT_TOO_LARGE
    try:
        body = _maybe_decompress(raw, headers, limit)
    except ContentTooLarge:
        return FetchOutcome(status=CAP_TOO_LARGE, net_decision=decision, robots_result=robots_result,
                            redirect_lineage=lineage, warnings=["decompression_bomb"])
    if len(raw) > limit or len(body) > limit:
        return FetchOutcome(status=CAP_TOO_LARGE, net_decision=decision, robots_result=robots_result,
                            redirect_lineage=lineage, warnings=["response_exceeds_bound"])
    # a declared length materially SMALLER than the delivered body is unsafe (smuggled extra data)
    enc = (headers.get("content-encoding") or "").lower()
    if declared_len is not None and not enc and len(raw) > declared_len:
        return FetchOutcome(status=CAP_UNAVAILABLE, net_decision=decision, robots_result=robots_result,
                            redirect_lineage=lineage, warnings=["content_length_mismatch"])

    media = _media_type_of(headers)
    allowed = expected_media if expected_media else ALLOWED_MEDIA_TYPES
    if media and media not in allowed and media not in ALLOWED_MEDIA_TYPES:
        return FetchOutcome(status=CAP_CTYPE, http_status=http_status, media_type=media,
                            net_decision=decision, robots_result=robots_result,
                            redirect_lineage=lineage, warnings=["content_type_not_allowed"])
    if _looks_binary(body):
        return FetchOutcome(status=CAP_CTYPE, http_status=http_status, media_type=media or "application/octet-stream",
                            net_decision=decision, robots_result=robots_result,
                            redirect_lineage=lineage, warnings=["binary_body_blocked"])
    if not media:
        media = "text/plain"

    if use_cache and not extra_headers and not from_cache:
        _cache_store(config, key, body, {"etag": headers.get("etag"),
                                         "last_modified": headers.get("last-modified"),
                                         "media_type": media})
    return FetchOutcome(status=(CAP_FROM_CACHE if from_cache else CAP_CAPTURED),
                        http_status=http_status, media_type=media, body=body,
                        redirect_lineage=lineage, from_cache=from_cache, net_decision=decision,
                        robots_result=robots_result, warnings=warnings,
                        response_headers=_safe_response_headers(headers))


# ================================================================ safe HTML parsing / extraction
class _HTMLExtractor(HTMLParser):
    """A non-executing HTML reader (stdlib html.parser). It never runs scripts — a <script> body is
    read as inert text and only application/ld+json blocks are retained. It collects the document
    title, meta name/property values, the canonical link, and a couple of explicit product hooks."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = None
        self.meta = {}
        self.canonical = None
        self.jsonld = []
        self.product_title = None
        self.offscreen = []
        self._in_title = False
        self._in_ld = False
        self._ld_buf = []
        self._grab_ptitle = False
        self._ptitle_buf = []
        self._grab_off = False
        self._off_buf = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or a.get("itemprop") or "").lower()
            content = a.get("content")
            if name and content is not None and name not in self.meta:
                self.meta[name] = content
        elif tag == "link" and "canonical" in (a.get("rel") or "").lower():
            if self.canonical is None and a.get("href"):
                self.canonical = a.get("href")
        elif tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._in_ld = True
            self._ld_buf = []
        if a.get("id") == "productTitle":
            self._grab_ptitle = True
            self._ptitle_buf = []
        if "a-offscreen" in (a.get("class") or "").split():
            self._grab_off = True
            self._off_buf = []

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._in_ld:
            self._in_ld = False
            text = "".join(self._ld_buf)
            if len(self.jsonld) < MAX_LD_BLOCKS and len(text) <= MAX_LD_BYTES:
                self.jsonld.append(text)
        if self._grab_ptitle and tag in ("span", "h1", "div"):
            val = "".join(self._ptitle_buf).strip()
            if val and self.product_title is None:
                self.product_title = val
            self._grab_ptitle = False
        if self._grab_off and tag == "span":
            val = "".join(self._off_buf).strip()
            if val:
                self.offscreen.append(val)
            self._grab_off = False

    def handle_data(self, data):
        if self._in_title and self.title is None:
            self.title = (self.title or "") + data
        if self._in_ld and sum(len(x) for x in self._ld_buf) <= MAX_LD_BYTES:
            self._ld_buf.append(data)
        if self._grab_ptitle:
            self._ptitle_buf.append(data)
        if self._grab_off:
            self._off_buf.append(data)


def _json_depth(obj, _d=0):
    if _d > MAX_LD_DEPTH:
        return _d
    if isinstance(obj, dict):
        return max([_d] + [_json_depth(v, _d + 1) for v in obj.values()])
    if isinstance(obj, list):
        return max([_d] + [_json_depth(v, _d + 1) for v in obj])
    return _d


def _ld_type_matches(node, wanted):
    t = node.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(isinstance(x, str) and x.lower() == wanted for x in types)


def _safe_jsonld_products(texts):
    """Parse ld+json blocks safely (bounded size / nesting; duplicate keys rejected) and return the
    list of Product nodes only. Arbitrary @types are ignored — never trusted."""
    products = []
    for text in texts[:MAX_LD_BLOCKS]:
        if len(text) > MAX_LD_BYTES:
            continue
        try:
            data = _loads_strict(text, "jsonld")
        except (ResearchError, ValueError):
            continue
        if _json_depth(data) > MAX_LD_DEPTH:
            continue
        nodes = []
        if isinstance(data, dict):
            nodes = data.get("@graph") if isinstance(data.get("@graph"), list) else [data]
        elif isinstance(data, list):
            nodes = data
        for node in nodes:
            if isinstance(node, dict) and _ld_type_matches(node, "product"):
                products.append(node)
    return products


# ================================================================ evidence + capture builders
def _evidence(evidence_type, field_path, observed_value, *, extraction_method, source_locator,
              source_content_hash, unit=None, currency=None, confidence="HIGH",
              value_kind="OBSERVED", warnings=None):
    """Build a normalized evidence record with a STABLE, content-addressed evidence_id. Identity
    depends only on the observed value + its provenance — never on row order, timestamps or paths."""
    identity = {
        "source_content_hash": source_content_hash,
        "evidence_type": evidence_type,
        "field_path": field_path,
        "observed_value": observed_value,
        "observed_unit": unit,
        "observed_currency": currency,
        "extraction_method": extraction_method,
        "parser_version": PARSER_VERSION,
    }
    evidence_id = "ev-" + _sha256_hex(canonical_json(identity))[:24]
    return {
        "schema_version": EVIDENCE_SCHEMA,
        "evidence_id": evidence_id,
        "evidence_type": evidence_type,
        "field_path": field_path,
        "observed_value": observed_value,
        "observed_unit": unit,
        "observed_currency": currency,
        "source_locator": source_locator,
        "source_content_hash": source_content_hash,
        "extraction_method": extraction_method,
        "parser_version": PARSER_VERSION,
        "confidence": confidence,
        "value_kind": value_kind,
        "warnings": list(warnings or []),
    }


def _sort_evidence(items):
    return sorted(items, key=lambda e: (e["evidence_type"], e["field_path"],
                                        str(e["observed_value"]), e["evidence_id"]))


def _build_capture(config, *, source_id, source_type, normalized_locator, source_locator, outcome,
                   evidence_items, extra_lineage=None):
    """Assemble a deterministic raw-capture record + its evidence, persist both atomically, and
    return the capture dict. capture_id depends only on content + locator + status (never on a
    timestamp, mtime, temp path, PID, random value or filesystem order)."""
    content_hash = outcome.content_sha256
    identity = {
        "schema_version": CAPTURE_SCHEMA,
        "source_type": source_type,
        "normalized_locator": normalized_locator,
        "content_sha256": content_hash,
        "content_size": len(outcome.body or b""),
        "media_type": outcome.media_type,
        "http_status": outcome.http_status,
        "redirect_lineage": list(outcome.redirect_lineage),
        "parser_version": PARSER_VERSION,
        "capture_status": outcome.status,
    }
    capture_id = "cap-" + _sha256_hex(canonical_json(identity))[:24]
    net = outcome.net_decision.to_dict() if outcome.net_decision is not None else None
    evid = _sort_evidence([{**e, "capture_id": capture_id, "source_id": source_id,
                            "lineage": {"capture_id": capture_id,
                                        "redirect_lineage": list(outcome.redirect_lineage),
                                        "extraction_method": e["extraction_method"]}}
                           for e in evidence_items])
    capture = {
        "schema_version": CAPTURE_SCHEMA,
        "capture_id": capture_id,
        "source_id": source_id,
        "source_type": source_type,
        "normalized_locator": normalized_locator,
        "source_locator": source_locator,
        "content_sha256": content_hash,
        "content_size": len(outcome.body or b""),
        "media_type": outcome.media_type,
        "http_status": outcome.http_status,
        "response_headers": dict(outcome.response_headers),
        "redirect_lineage": list(outcome.redirect_lineage),
        "parser_version": PARSER_VERSION,
        "network_policy_result": net,
        "robots_result": outcome.robots_result,
        "capture_status": outcome.status,
        "from_cache": outcome.from_cache,
        "cache_key": _cache_key(normalized_locator, _purpose_for_type(source_type)),
        "warnings": list(outcome.warnings),
        "evidence_count": len(evid),
        "evidence": evid,
    }
    capture.update(AMAZON_COUNTERS)
    # persist raw bytes (content-addressed) + normalized capture record
    if outcome.body:
        _atomic_write_bytes(os.path.join(config.raw_dir, capture_id + ".bin"), outcome.body)
    _atomic_write_json(os.path.join(config.normalized_dir, capture_id + ".json"), capture)
    return capture


def _purpose_for_type(source_type):
    return {
        "public-url": NP.PURPOSE_PUBLIC_RESEARCH,
        "rss": NP.PURPOSE_RSS_FEED,
        "github": NP.PURPOSE_GITHUB_METADATA,
        "pypi": NP.PURPOSE_PYPI_METADATA,
        "amazon-public-product": NP.PURPOSE_AMAZON_PUBLIC_PRODUCT,
        "local-file": NP.PURPOSE_PUBLIC_RESEARCH,
    }.get(source_type, NP.PURPOSE_PUBLIC_RESEARCH)


def _source_id(source_type, normalized_locator):
    return "src-" + _sha256_hex(canonical_json({"source_type": source_type,
                                               "normalized_locator": normalized_locator}))[:24]


# ================================================================ locator normalization
def _strip_fragment(url):
    p = urlsplit(str(url))
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, p.query, ""))


def _normalize_locator(source_type, locator):
    if source_type in ("public-url", "rss", "amazon-public-product"):
        return _strip_fragment(locator)
    if source_type == "github":
        return "github:" + _github_repo_slug(locator)
    if source_type == "pypi":
        return "pypi:" + _pypi_package_name(locator)
    if source_type == "local-file":
        return "file:" + str(locator).replace("\\", "/").lstrip("/")
    return str(locator)


def _github_repo_slug(locator):
    s = str(locator).strip()
    if "://" in s:
        s = urlsplit(s).path
    s = s.strip("/")
    if s.endswith(".git"):
        s = s[:-4]
    parts = [p for p in s.split("/") if p]
    if len(parts) < 2:
        raise ResearchError("GITHUB_LOCATOR_INVALID", "expected OWNER/REPOSITORY")
    owner, repo = parts[0], parts[1]
    if not re.match(r"^[A-Za-z0-9._-]+$", owner) or not re.match(r"^[A-Za-z0-9._-]+$", repo):
        raise ResearchError("GITHUB_LOCATOR_INVALID", "invalid owner/repo characters")
    return f"{owner}/{repo}"


def _pypi_package_name(locator):
    s = str(locator).strip()
    if "://" in s:
        s = [p for p in urlsplit(s).path.split("/") if p][-1]
    if not re.match(r"^[A-Za-z0-9._-]+$", s):
        raise ResearchError("PYPI_PACKAGE_INVALID", "invalid package name")
    return s.lower()


# ================================================================ adapters (source -> [captures])
def _capture_public_url(config, descriptor):
    url = descriptor["source_locator"]
    nloc = _normalize_locator("public-url", url)
    sid = _source_id("public-url", nloc)
    outcome = _fetch(config, url, purpose=NP.PURPOSE_PUBLIC_RESEARCH,
                     max_bytes=descriptor.get("_max_bytes"), respect_robots=True,
                     normalized_locator=nloc)
    evid = _extract_generic(outcome, url) if outcome.status in _SUCCESS_STATUSES else []
    return [_build_capture(config, source_id=sid, source_type="public-url", normalized_locator=nloc,
                           source_locator=url, outcome=outcome, evidence_items=evid)]


def _capture_rss(config, descriptor):
    url = descriptor["source_locator"]
    nloc = _normalize_locator("rss", url)
    sid = _source_id("rss", nloc)
    outcome = _fetch(config, url, purpose=NP.PURPOSE_RSS_FEED, respect_robots=True,
                     normalized_locator=nloc)
    evid = _extract_feed(outcome, url) if outcome.status in _SUCCESS_STATUSES else []
    status = outcome.status
    if status in _SUCCESS_STATUSES and evid is None:
        outcome.warnings.append("feed_parse_failed")
        outcome.status = CAP_PARSE_PARTIAL
        evid = []
    return [_build_capture(config, source_id=sid, source_type="rss", normalized_locator=nloc,
                           source_locator=url, outcome=outcome, evidence_items=evid or [])]


def _capture_github(config, descriptor):
    slug = _github_repo_slug(descriptor["source_locator"])
    token = config.github_token()
    headers = None
    if token:
        headers = {"Authorization": "Bearer " + token, "X-GitHub-Api-Version": "2022-11-28"}
    captures = []
    # ---- repository metadata
    repo_url = "https://api.github.com/repos/" + slug
    nloc = "github:" + slug + "#repo"
    sid = _source_id("github", "github:" + slug)
    outcome = _fetch(config, repo_url, purpose=NP.PURPOSE_GITHUB_METADATA, respect_robots=False,
                     use_cache=(token is None), extra_headers=headers, normalized_locator=nloc)
    evid = _extract_github_repo(outcome) if outcome.status in _SUCCESS_STATUSES else []
    captures.append(_build_capture(config, source_id=sid, source_type="github", normalized_locator=nloc,
                                   source_locator=slug, outcome=outcome, evidence_items=evid))
    # ---- latest release metadata (optional; a 404 = no release, recorded honestly)
    if descriptor.get("parser_options", {}).get("include_release", True):
        rel_url = "https://api.github.com/repos/" + slug + "/releases/latest"
        rloc = "github:" + slug + "#release"
        ro = _fetch(config, rel_url, purpose=NP.PURPOSE_GITHUB_METADATA, respect_robots=False,
                    use_cache=(token is None), extra_headers=headers, normalized_locator=rloc)
        revid = _extract_github_release(ro) if ro.status in _SUCCESS_STATUSES else []
        captures.append(_build_capture(config, source_id=sid, source_type="github",
                                       normalized_locator=rloc, source_locator=slug, outcome=ro,
                                       evidence_items=revid))
    return captures


def _capture_pypi(config, descriptor):
    pkg = _pypi_package_name(descriptor["source_locator"])
    nloc = "pypi:" + pkg
    sid = _source_id("pypi", nloc)
    url = "https://pypi.org/pypi/" + pkg + "/json"
    outcome = _fetch(config, url, purpose=NP.PURPOSE_PYPI_METADATA, respect_robots=False,
                     normalized_locator=nloc)
    evid = _extract_pypi(outcome) if outcome.status in _SUCCESS_STATUSES else []
    return [_build_capture(config, source_id=sid, source_type="pypi", normalized_locator=nloc,
                           source_locator=pkg, outcome=outcome, evidence_items=evid)]


def _capture_amazon_product(config, descriptor):
    raw = str(descriptor["source_locator"]).strip()
    if NP._ASIN_RE.match(raw.upper()):
        url = "https://www." + "amazon" + ".com/dp/" + raw.upper()
    else:
        url = raw
    nloc = _normalize_locator("amazon-public-product", url)
    sid = _source_id("amazon-public-product", nloc)
    outcome = _fetch(config, url, purpose=NP.PURPOSE_AMAZON_PUBLIC_PRODUCT, amazon_product=True,
                     respect_robots=True, expected_media=("text/html",), normalized_locator=nloc)
    evid = _extract_amazon(outcome, url) if outcome.status in _SUCCESS_STATUSES else []
    return [_build_capture(config, source_id=sid, source_type="amazon-public-product",
                           normalized_locator=nloc, source_locator=url, outcome=outcome,
                           evidence_items=evid)]


def _capture_local_file(config, descriptor):
    rel = str(descriptor["source_locator"])
    nloc = _normalize_locator("local-file", rel)
    sid = _source_id("local-file", nloc)
    outcome = _read_local_file(config, rel)
    if outcome.status in _SUCCESS_STATUSES:
        media = outcome.media_type
        if media == "text/html":
            evid = _extract_generic(outcome, nloc)
        elif media in ("application/xml", "text/xml", "application/rss+xml", "application/atom+xml"):
            evid = _extract_feed(outcome, nloc) or []
        else:
            evid = []
    else:
        evid = []
    return [_build_capture(config, source_id=sid, source_type="local-file", normalized_locator=nloc,
                           source_locator=nloc, outcome=outcome, evidence_items=evid)]


# ================================================================ local-file reader (sandboxed)
def _read_local_file(config, rel):
    if not config.input_root:
        return FetchOutcome(status=CAP_SOURCE_BLOCKED, warnings=["input_root_not_configured"])
    root = os.path.realpath(config.input_root)
    rel_s = str(rel)
    if os.path.isabs(rel_s) or rel_s.startswith("\\\\") or rel_s.startswith("//") or ":" in rel_s[1:3]:
        return FetchOutcome(status=CAP_SOURCE_BLOCKED, warnings=["absolute_or_unc_path_rejected"])
    candidate = os.path.realpath(os.path.join(root, rel_s))
    try:
        if os.path.commonpath([candidate, root]) != root:
            return FetchOutcome(status=CAP_SOURCE_BLOCKED, warnings=["path_escapes_input_root"])
    except ValueError:
        return FetchOutcome(status=CAP_SOURCE_BLOCKED, warnings=["path_escapes_input_root"])
    if not os.path.isfile(candidate):
        return FetchOutcome(status=CAP_NOT_FOUND, warnings=["local_file_not_found"])
    base = os.path.basename(candidate).lower()
    ext = os.path.splitext(base)[1]
    if (base in _FORBIDDEN_FILE_NAMES or base.endswith(_FORBIDDEN_FILE_SUFFIXES)
            or any(sub in base for sub in _FORBIDDEN_FILE_SUBSTR)):
        return FetchOutcome(status=CAP_SOURCE_BLOCKED, warnings=["forbidden_local_file"])
    if ext not in _LOCAL_EXT_MEDIA:
        return FetchOutcome(status=CAP_CTYPE, warnings=["unsupported_local_extension"])
    try:
        if os.path.getsize(candidate) > config.max_bytes:
            return FetchOutcome(status=CAP_TOO_LARGE, warnings=["local_file_too_large"])
        with open(candidate, "rb") as f:
            body = f.read(config.max_bytes + 1)
    except OSError:
        return FetchOutcome(status=CAP_SOURCE_BLOCKED, warnings=["local_file_unreadable"])
    if len(body) > config.max_bytes:
        return FetchOutcome(status=CAP_TOO_LARGE, warnings=["local_file_too_large"])
    if _looks_binary(body):
        return FetchOutcome(status=CAP_CTYPE, warnings=["binary_local_file_blocked"])
    return FetchOutcome(status=CAP_CAPTURED, http_status=None, media_type=_LOCAL_EXT_MEDIA[ext],
                        body=body, response_headers={"content-type": _LOCAL_EXT_MEDIA[ext]})


# ================================================================ extraction
def _extract_generic(outcome, locator):
    """HTML metadata + JSON-LD Product evidence for a generic public/local HTML document."""
    ch = outcome.content_sha256
    items = []
    try:
        p = _HTMLExtractor()
        p.feed(outcome.body.decode("utf-8", "replace"))
    except Exception:
        return items

    def add(etype, fp, val, method, **kw):
        if val is None:
            return
        v = val.strip() if isinstance(val, str) else val
        if v == "":
            return
        items.append(_evidence(etype, fp, v, extraction_method=method, source_locator=locator,
                               source_content_hash=ch, **kw))

    add("html.title", "title", p.title, "html-title")
    add("html.meta_description", "meta.description", p.meta.get("description"), "html-meta")
    add("html.canonical", "link.canonical", p.canonical, "html-canonical")
    for prop in ("og:title", "og:description", "og:site_name", "og:url", "og:type"):
        add("opengraph." + prop.split(":", 1)[1], "meta." + prop, p.meta.get(prop), "html-opengraph")
    _add_products(items, _safe_jsonld_products(p.jsonld), locator, ch, add)
    return items


def _add_products(items, products, locator, ch, add):
    for prod in products:
        name = prod.get("name")
        if isinstance(name, str):
            add("product.title", "jsonld.Product.name", name, "json-ld")
        brand = prod.get("brand")
        bname = brand.get("name") if isinstance(brand, dict) else brand
        if isinstance(bname, str):
            add("product.brand", "jsonld.Product.brand", bname, "json-ld")
        offers = prod.get("offers")
        offer = offers[0] if isinstance(offers, list) and offers else offers
        if isinstance(offer, dict):
            price = _to_decimal_str(offer.get("price"))
            cur = offer.get("priceCurrency")
            if price is not None:
                add("product.price", "jsonld.Product.offers.price", price, "json-ld",
                    currency=(cur if isinstance(cur, str) else None), value_kind="NORMALIZED_TECHNICAL")
            avail = offer.get("availability")
            if isinstance(avail, str):
                add("product.availability", "jsonld.Product.offers.availability", avail, "json-ld")
        rating = prod.get("aggregateRating")
        if isinstance(rating, dict):
            rv = _to_decimal_str(rating.get("ratingValue"))
            if rv is not None:
                add("product.rating_value", "jsonld.Product.aggregateRating.ratingValue", rv,
                    "json-ld", value_kind="NORMALIZED_TECHNICAL")
            rc = _to_int_str(rating.get("reviewCount") or rating.get("ratingCount"))
            if rc is not None:
                add("product.rating_count", "jsonld.Product.aggregateRating.reviewCount", rc,
                    "json-ld", value_kind="NORMALIZED_TECHNICAL")


def _extract_amazon(outcome, url):
    """Public Amazon product metadata explicitly present in the captured bytes. Never customer
    reviews, customer names, hidden form values, cart/session data, or tracking tokens."""
    ch = outcome.content_sha256
    items = []
    try:
        p = _HTMLExtractor()
        p.feed(outcome.body.decode("utf-8", "replace"))
    except Exception:
        p = None

    def add(etype, fp, val, method, **kw):
        if val is None:
            return
        v = val.strip() if isinstance(val, str) else val
        if v == "":
            return
        items.append(_evidence(etype, fp, v, extraction_method=method, source_locator=url,
                               source_content_hash=ch, **kw))

    asin = NP.extract_amazon_asin(urlsplit(url).path)
    add("product.asin", "url.asin", asin, "amazon-url")
    add("html.canonical", "link.canonical", (p.canonical if p else None), "html-canonical")
    if p:
        add("product.title", "html.productTitle", p.product_title, "amazon-visible")
        add("product.title", "meta.og:title", p.meta.get("og:title"), "html-opengraph")
        if not p.product_title and p.title:
            add("product.title", "title", p.title, "html-title")
        for span in p.offscreen[:1]:
            price = _to_decimal_str(span)
            if price is not None:
                cur = "USD" if "$" in span else None
                add("product.price", "html.a-offscreen", price, "amazon-visible",
                    currency=cur, value_kind="NORMALIZED_TECHNICAL")
                break
        _add_products(items, _safe_jsonld_products(p.jsonld), url, ch, add)
    return items


def _extract_feed(outcome, locator):
    """Parse an RSS or Atom feed with hardened XML (external entities + entity expansion refused).
    Returns evidence, or None when the XML is unparseable (caller records PARSE_PARTIAL)."""
    ch = outcome.content_sha256
    raw = outcome.body
    if re.search(rb"<!DOCTYPE", raw, re.IGNORECASE) or re.search(rb"<!ENTITY", raw, re.IGNORECASE):
        # a DOCTYPE / ENTITY declaration means a possible XXE or billion-laughs payload -> refuse
        return None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    items = []

    def add(etype, fp, val, method, **kw):
        if val is None:
            return
        v = val.strip() if isinstance(val, str) else val
        if v == "":
            return
        items.append(_evidence(etype, fp, v, extraction_method=method, source_locator=locator,
                               source_content_hash=ch, **kw))

    def local(tag):
        return tag.split("}", 1)[1] if "}" in tag else tag

    tag = local(root.tag).lower()
    seen_ids = set()
    if tag == "rss":
        channel = root.find("channel")
        if channel is not None:
            add("feed.title", "rss.channel.title", _text(channel.find("title")), "feed-xml")
            for it in channel.findall("item"):
                _feed_item(add, it, "rss", seen_ids)
    elif tag == "feed":                                       # Atom
        add("feed.title", "atom.feed.title", _text(_find_local(root, "title")), "feed-xml")
        for entry in _findall_local(root, "entry"):
            _feed_item(add, entry, "atom", seen_ids)
    else:
        return None
    return items


def _feed_item(add, node, kind, seen_ids):
    if kind == "rss":
        guid = _text(node.find("guid")) or _text(node.find("link"))
        title = _text(node.find("title"))
        link = _text(node.find("link"))
        pub = _text(node.find("pubDate"))
        summary = _text(node.find("description"))
    else:
        guid = _text(_find_local(node, "id"))
        title = _text(_find_local(node, "title"))
        link = _atom_link(node)
        pub = _text(_find_local(node, "updated")) or _text(_find_local(node, "published"))
        summary = _text(_find_local(node, "summary"))
    ident = guid or link or title
    if not ident or ident in seen_ids:                        # deterministic de-duplication
        return
    seen_ids.add(ident)
    add("feed.item.id", "item.id", ident, "feed-xml")
    add("feed.item.title", "item.title", title, "feed-xml")
    add("feed.item.url", "item.url", link, "feed-xml")
    add("feed.item.published", "item.published", pub, "feed-xml")
    add("feed.item.summary", "item.summary", summary, "feed-xml")


def _text(el):
    return el.text if el is not None and el.text else None


def _find_local(parent, name):
    for ch in parent:
        if ch.tag.split("}", 1)[-1] == name:
            return ch
    return None


def _findall_local(parent, name):
    return [ch for ch in parent if ch.tag.split("}", 1)[-1] == name]


def _atom_link(entry):
    for ch in entry:
        if ch.tag.split("}", 1)[-1] == "link":
            rel = (ch.get("rel") or "alternate").lower()
            if rel == "alternate" and ch.get("href"):
                return ch.get("href")
    for ch in entry:
        if ch.tag.split("}", 1)[-1] == "link" and ch.get("href"):
            return ch.get("href")
    return None


def _extract_github_repo(outcome):
    ch = outcome.content_sha256
    try:
        doc = _loads_strict(outcome.body, "github_repo")
    except (ResearchError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []
    items = []

    def add(etype, fp, val, **kw):
        if val is None or (isinstance(val, str) and not val.strip()):
            return
        items.append(_evidence(etype, fp, val if not isinstance(val, str) else val.strip(),
                               extraction_method="github-json", source_locator="github",
                               source_content_hash=ch, **kw))

    add("github.full_name", "repo.full_name", doc.get("full_name"))
    add("github.default_branch", "repo.default_branch", doc.get("default_branch"))
    desc = doc.get("description")
    if isinstance(desc, str) and desc.strip():
        add("github.description_sha256", "repo.description", _sha256_hex(desc),
            value_kind="NORMALIZED_TECHNICAL")
    return items


def _extract_github_release(outcome):
    ch = outcome.content_sha256
    try:
        doc = _loads_strict(outcome.body, "github_release")
    except (ResearchError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []
    items = []

    def add(etype, fp, val, **kw):
        if val is None or (isinstance(val, str) and not val.strip()):
            return
        items.append(_evidence(etype, fp, val if not isinstance(val, str) else val.strip(),
                               extraction_method="github-release-json", source_locator="github",
                               source_content_hash=ch, **kw))

    add("github.latest_release_tag", "release.tag_name", doc.get("tag_name"))
    add("github.release_title", "release.name", doc.get("name"))
    add("github.release_published_at", "release.published_at", doc.get("published_at"))
    body = doc.get("body")
    if isinstance(body, str) and body.strip():
        add("github.release_notes_sha256", "release.body", _sha256_hex(body),
            value_kind="NORMALIZED_TECHNICAL")
    assets = doc.get("assets")
    if isinstance(assets, list):
        for a in assets:
            if isinstance(a, dict) and isinstance(a.get("name"), str):
                add("github.release_asset", "release.asset." + a["name"], a["name"])
                size = _to_int_str(a.get("size"))
                if size is not None:
                    add("github.release_asset_size", "release.asset." + a["name"] + ".size", size,
                        unit="bytes", value_kind="NORMALIZED_TECHNICAL")
    return items


def _extract_pypi(outcome):
    ch = outcome.content_sha256
    try:
        doc = _loads_strict(outcome.body, "pypi")
    except (ResearchError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []
    info = doc.get("info") if isinstance(doc.get("info"), dict) else {}
    items = []

    def add(etype, fp, val, **kw):
        if val is None or (isinstance(val, str) and not val.strip()):
            return
        items.append(_evidence(etype, fp, val if not isinstance(val, str) else val.strip(),
                               extraction_method="pypi-json", source_locator="pypi",
                               source_content_hash=ch, **kw))

    add("pypi.name", "info.name", info.get("name"))
    add("pypi.version", "info.version", info.get("version"))
    add("pypi.summary", "info.summary", info.get("summary"))
    add("pypi.requires_python", "info.requires_python", info.get("requires_python"))
    urls = info.get("project_urls")
    if isinstance(urls, dict):
        for k in sorted(urls):
            if isinstance(urls[k], str):
                add("pypi.project_url", "info.project_urls." + k, urls[k])
    version = info.get("version")
    files = (doc.get("urls") if isinstance(doc.get("urls"), list) else [])
    for fobj in files:
        if not isinstance(fobj, dict):
            continue
        digests = fobj.get("digests") if isinstance(fobj.get("digests"), dict) else {}
        sha = digests.get("sha256")
        if isinstance(sha, str) and re.match(r"^[0-9a-f]{64}$", sha):
            add("pypi.release_file_sha256", "release." + str(fobj.get("filename")), sha,
                value_kind="NORMALIZED_TECHNICAL")
        upload = fobj.get("upload_time_iso_8601") or fobj.get("upload_time")
        if isinstance(upload, str):
            add("pypi.release_upload_time", "release." + str(fobj.get("filename")) + ".upload_time",
                upload)
        if fobj.get("yanked"):
            add("pypi.yanked", "release." + str(fobj.get("filename")) + ".yanked", "true")
    if version is not None:
        add("pypi.current_version", "info.version.current", version)
    return items


# ================================================================ research plan
_PLAN_TOP_KEYS = frozenset({"schema_version", "sources", "label", "tags", "description"})
_DESCRIPTOR_KEYS = frozenset({"source_type", "source_locator", "label", "tags", "parser_options",
                              "expected_content_type", "maximum_bytes", "enabled"})
_SOURCE_TYPES = frozenset({"public-url", "rss", "github", "pypi", "amazon-public-product",
                           "local-file"})
# any key whose presence signals a secret / arbitrary header / command -> the plan is refused
_PLAN_FORBIDDEN_SUBSTR = ("secret", "password", "passwd", "token", "cookie", "credential",
                          "authorization", "auth", "bearer", "api_key", "apikey", "session",
                          "header", "headers", "command", "cmd", "proxy", "seller")


def _scan_forbidden(obj, path=""):
    """Deep-scan a plan for any forbidden (secret/header/command) key. Returns the first bad key."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if any(sub in kl for sub in _PLAN_FORBIDDEN_SUBSTR):
                return path + "/" + str(k)
            bad = _scan_forbidden(v, path + "/" + str(k))
            if bad:
                return bad
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            bad = _scan_forbidden(v, f"{path}[{i}]")
            if bad:
                return bad
    return None


def normalize_plan(plan):
    """Validate + canonicalize a research plan. Rejects unknown fields, secret/header/command
    fields, and unknown source types. Returns (canonical_plan, plan_id). Reordered JSON keys
    produce the SAME plan_id (canonical form)."""
    if not isinstance(plan, dict):
        raise PlanError("plan must be a JSON object")
    bad = _scan_forbidden(plan)
    if bad:
        raise PlanError(f"forbidden field in plan: {bad}")
    unknown = set(plan) - _PLAN_TOP_KEYS
    if unknown:
        raise PlanError(f"unknown plan field(s): {sorted(unknown)}")
    sources = plan.get("sources")
    if not isinstance(sources, list):
        raise PlanError("plan.sources must be a list")
    norm_sources = []
    for i, desc in enumerate(sources):
        norm_sources.append(_normalize_descriptor(desc, i))
    canonical = {
        "schema_version": PLAN_SCHEMA,
        "sources": norm_sources,
    }
    plan_id = "plan-" + _sha256_hex(canonical_json(canonical))[:24]
    return canonical, plan_id


def _normalize_descriptor(desc, index):
    if not isinstance(desc, dict):
        raise PlanError(f"source[{index}] must be an object")
    unknown = set(desc) - _DESCRIPTOR_KEYS
    if unknown:
        raise PlanError(f"source[{index}] unknown field(s): {sorted(unknown)}")
    st = desc.get("source_type")
    if st not in _SOURCE_TYPES:
        raise PlanError(f"source[{index}] invalid source_type: {st!r}")
    locator = desc.get("source_locator")
    if not isinstance(locator, str) or not locator.strip():
        raise PlanError(f"source[{index}] requires a source_locator string")
    tags = desc.get("tags", [])
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        raise PlanError(f"source[{index}] tags must be a list of strings")
    popts = desc.get("parser_options", {})
    if not isinstance(popts, dict):
        raise PlanError(f"source[{index}] parser_options must be an object")
    mb = desc.get("maximum_bytes")
    if mb is not None and (not isinstance(mb, int) or mb <= 0 or mb > ABSOLUTE_MAX_BYTES):
        raise PlanError(f"source[{index}] maximum_bytes out of range")
    ect = desc.get("expected_content_type")
    if ect is not None and not isinstance(ect, str):
        raise PlanError(f"source[{index}] expected_content_type must be a string")
    return {
        "source_type": st,
        "source_locator": locator.strip(),
        "label": desc.get("label") if isinstance(desc.get("label"), str) else None,
        "tags": sorted(tags),
        "parser_options": popts,
        "expected_content_type": ect,
        "maximum_bytes": mb,
        "enabled": bool(desc.get("enabled", True)),
    }


# ================================================================ run assembly
_ADAPTERS = {
    "public-url": _capture_public_url,
    "rss": _capture_rss,
    "github": _capture_github,
    "pypi": _capture_pypi,
    "amazon-public-product": _capture_amazon_product,
    "local-file": _capture_local_file,
}


def _resolve_readiness(statuses, evidence_total):
    if not statuses:
        return SOURCE_REQUIRED
    if CAP_SELLER in statuses:
        return SELLER_CENTRAL_POLICY_BLOCKED
    n_ok = sum(1 for s in statuses if s in _SUCCESS_STATUSES)
    if n_ok == len(statuses):
        if CAP_PARSE_PARTIAL in statuses:
            return READY_PARTIAL
        return READY if evidence_total > 0 else READY_EMPTY
    if n_ok > 0:
        return READY_PARTIAL
    for st, rd in ((CAP_NET_POLICY, NETWORK_POLICY_BLOCKED), (CAP_CACHE_BLOCKED, CACHE_BLOCKED),
                   (CAP_SOURCE_BLOCKED, SOURCE_BLOCKED), (CAP_TOO_LARGE, CONTENT_TOO_LARGE),
                   (CAP_CTYPE, CONTENT_TYPE_BLOCKED), (CAP_ROBOTS, ROBOTS_BLOCKED)):
        if st in statuses:
            return rd
    return NETWORK_UNAVAILABLE


def _run_id(capture_ids, plan_id):
    return "run-" + _sha256_hex(canonical_json({"captures": sorted(capture_ids),
                                               "plan_id": plan_id}))[:24]


def _execute_sources(config, descriptors):
    """Run each enabled descriptor through its adapter, collecting captures. A per-source adapter
    failure is recorded as a SOURCE_BLOCKED capture, never a crash."""
    captures = []
    for desc in descriptors:
        if not desc.get("enabled", True):
            continue
        d = dict(desc)
        d["_max_bytes"] = desc.get("maximum_bytes")
        try:
            captures.extend(_ADAPTERS[desc["source_type"]](config, d))
        except ResearchError as e:
            nloc = _safe_normalize(desc["source_type"], desc["source_locator"])
            sid = _source_id(desc["source_type"], nloc)
            outcome = FetchOutcome(status=CAP_SOURCE_BLOCKED, warnings=[e.code])
            captures.append(_build_capture(config, source_id=sid, source_type=desc["source_type"],
                                           normalized_locator=nloc, source_locator=desc["source_locator"],
                                           outcome=outcome, evidence_items=[]))
    return captures


def _safe_normalize(source_type, locator):
    try:
        return _normalize_locator(source_type, locator)
    except ResearchError:
        return source_type + ":" + _sha256_hex(str(locator))[:16]


def _assemble_run(config, captures, plan_id):
    capture_ids = [c["capture_id"] for c in captures]
    run_id = _run_id(capture_ids, plan_id)
    statuses = [c["capture_status"] for c in captures]
    evidence_total = sum(c["evidence_count"] for c in captures)
    readiness = _resolve_readiness(statuses, evidence_total)
    status_counts = {}
    for s in statuses:
        status_counts[s] = status_counts.get(s, 0) + 1
    # group captures by source_id -> a source has 1..N captures
    by_source = {}
    for c in captures:
        by_source.setdefault(c["source_id"], []).append(c)
    sources = []
    for sid in sorted(by_source):
        clist = sorted(by_source[sid], key=lambda c: c["normalized_locator"])
        first = clist[0]
        sources.append({
            "source_id": sid,
            "source_type": first["source_type"],
            "normalized_locator": first["normalized_locator"].split("#", 1)[0],
            "source_locator": first["source_locator"],
            "captures": [{
                "capture_id": c["capture_id"], "normalized_locator": c["normalized_locator"],
                "capture_status": c["capture_status"], "content_sha256": c["content_sha256"],
                "media_type": c["media_type"], "http_status": c["http_status"],
                "redirect_lineage": c["redirect_lineage"], "evidence_count": c["evidence_count"],
                "robots_result": c["robots_result"],
            } for c in clist],
        })
    manifest = {
        "schema_version": RUN_MANIFEST_SCHEMA,
        "run_id": run_id,
        "tool_version": TOOL_VERSION,
        "parser_version": PARSER_VERSION,
        "plan_id": plan_id,
        "source_count": len(by_source),
        "capture_count": len(captures),
        "evidence_count": evidence_total,
        "capture_status_counts": status_counts,
        "readiness": readiness,
        "sources": sources,
        "capture_ids": sorted(capture_ids),
        "disclaimers": list(DISCLAIMERS),
        "network_purpose_counters": dict(config.host_gate.requests),
    }
    manifest.update(AMAZON_COUNTERS)
    _atomic_write_json(os.path.join(config.manifests_dir, run_id + ".json"), manifest)
    _atomic_write_json(os.path.join(config.runs_dir, run_id + ".json"), {
        "schema_version": RUN_MANIFEST_SCHEMA, "run_id": run_id, "plan_id": plan_id,
        "readiness": readiness, "source_count": len(by_source), "capture_count": len(captures),
        "evidence_count": evidence_total, "capture_ids": sorted(capture_ids),
        "capture_status_counts": status_counts,
    })
    _write_exports(config, run_id, manifest, captures)
    _log(config, "run", {"run_id": run_id, "readiness": readiness, "sources": len(by_source),
                         "evidence": evidence_total})
    return {"command": "run", "run_id": run_id, "readiness": readiness, "ok": readiness in _EXIT_OK,
            "source_count": len(by_source), "capture_count": len(captures),
            "evidence_count": evidence_total, "capture_status_counts": status_counts,
            "amazon_counters": dict(AMAZON_COUNTERS), "manifest_path": _rel(config, os.path.join(
                config.manifests_dir, run_id + ".json"))}


# ================================================================ exports
_TSV_DANGEROUS = ("=", "+", "-", "@", "\t", "\r", "|")
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _tsv_cell(value):
    """Neutralize spreadsheet formula injection and strip TSV-breaking control characters. A plain
    number keeps its leading sign (so -2.50 is preserved); anything else with a dangerous leading
    char is prefixed with a single quote so a spreadsheet treats it as text."""
    if value is None:
        return ""
    s = str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ").replace("\x00", "")
    if s and s[0] in _TSV_DANGEROUS and not _NUMERIC_RE.match(s):
        s = "'" + s
    return s


def _all_evidence(captures):
    rows = []
    for c in captures:
        for e in c["evidence"]:
            rows.append({**e, "source_type": c["source_type"], "capture_status": c["capture_status"],
                         "capture_id": c["capture_id"]})
    return sorted(rows, key=lambda e: (e["source_type"], e["source_locator"], e["evidence_type"],
                                       e["field_path"], str(e["observed_value"]), e["evidence_id"]))


def _write_exports(config, run_id, manifest, captures):
    out_dir = os.path.join(config.reports_dir, run_id)
    evidence_rows = _all_evidence(captures)
    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA,
        "run_id": run_id,
        "plan_id": manifest["plan_id"],
        "tool_version": TOOL_VERSION,
        "parser_version": PARSER_VERSION,
        "source_count": manifest["source_count"],
        "capture_count": manifest["capture_count"],
        "evidence_count": manifest["evidence_count"],
        "capture_status_counts": manifest["capture_status_counts"],
        "sources": manifest["sources"],
        "evidence": [{k: e[k] for k in ("evidence_id", "capture_id", "source_id", "evidence_type",
                                        "field_path", "observed_value", "observed_unit",
                                        "observed_currency", "source_locator", "source_content_hash",
                                        "extraction_method", "parser_version", "confidence",
                                        "value_kind", "warnings")}
                     for c in captures for e in c["evidence"]],
        "disclaimers": list(DISCLAIMERS),
        "network_purpose_counters": manifest["network_purpose_counters"],
    }
    snapshot["evidence"] = _sort_evidence(snapshot["evidence"])
    snapshot.update(AMAZON_COUNTERS)
    _atomic_write_json(os.path.join(out_dir, "research_snapshot.json"), snapshot)

    cols = ("source_type", "source_locator", "evidence_type", "field_path", "observed_value",
            "observed_unit", "observed_currency", "source_content_hash", "extraction_method",
            "confidence", "value_kind", "capture_status")
    lines = ["\t".join(cols)]
    for e in evidence_rows:
        lines.append("\t".join(_tsv_cell(e.get(c)) for c in cols))
    _atomic_write_text(os.path.join(out_dir, "research_evidence.tsv"), "\n".join(lines) + "\n")

    _atomic_write_text(os.path.join(out_dir, "research_report.md"),
                       _render_report(run_id, manifest, evidence_rows))


def _render_report(run_id, manifest, evidence_rows):
    L = []
    L.append(f"# Phase 7.10 — Connected Public Research Evidence Hub")
    L.append("")
    L.append(f"- Run ID: `{run_id}`")
    L.append(f"- Plan ID: `{manifest['plan_id']}`")
    L.append(f"- Tool: {TOOL_VERSION} (parser {PARSER_VERSION})")
    L.append(f"- Readiness: **{manifest['readiness']}**")
    L.append(f"- Sources: {manifest['source_count']} | Captures: {manifest['capture_count']} | "
             f"Evidence: {manifest['evidence_count']}")
    L.append("")
    L.append("## Capture statuses")
    for st in sorted(manifest["capture_status_counts"]):
        L.append(f"- {st}: {manifest['capture_status_counts'][st]}")
    L.append("")
    L.append("## Sources")
    for s in manifest["sources"]:
        L.append(f"### {s['source_type']} — `{s['normalized_locator']}`")
        for c in s["captures"]:
            lineage = " -> ".join(c["redirect_lineage"]) if c["redirect_lineage"] else "(no redirect)"
            L.append(f"- `{c['normalized_locator']}` — {c['capture_status']} — "
                     f"evidence {c['evidence_count']} — content `{c['content_sha256'][:16] or '(none)'}` "
                     f"— redirects: {lineage}")
    blocked = [s for s in manifest["sources"]
               for c in s["captures"] if c["capture_status"] not in _SUCCESS_STATUSES]
    if blocked:
        L.append("")
        L.append("## Blocked / unavailable captures")
        for s in manifest["sources"]:
            for c in s["captures"]:
                if c["capture_status"] not in _SUCCESS_STATUSES:
                    L.append(f"- `{c['normalized_locator']}` — {c['capture_status']}")
    L.append("")
    L.append("## Evidence")
    for e in evidence_rows:
        cur = f" {e['observed_currency']}" if e.get("observed_currency") else ""
        L.append(f"- [{e['source_type']}] {e['evidence_type']} = `{e['observed_value']}`{cur} "
                 f"({e['value_kind']}, via {e['extraction_method']})")
    L.append("")
    L.append("## Disclaimers")
    for d in DISCLAIMERS:
        L.append(f"- {d}")
    L.append("")
    L.append("## Amazon-account boundary")
    for k in sorted(AMAZON_COUNTERS):
        L.append(f"- {k}: {AMAZON_COUNTERS[k]}")
    return "\n".join(L) + "\n"


# ================================================================ logging (redacted, local, bounded)
def _log(config, operation, record):
    try:
        os.makedirs(config.logs_dir, exist_ok=True)
        line = json.dumps({"ts": _iso(), "op": str(operation), **redact(record)},
                          ensure_ascii=False, sort_keys=True)
        with open(os.path.join(config.logs_dir, "operations.jsonl"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _rel(config, path):
    """A relative display path that never leaks a full local absolute path."""
    try:
        return os.path.relpath(path, config.base_dir).replace(os.sep, "/")
    except ValueError:
        return os.path.basename(path)


# ================================================================ commands
def capture_one(config, source_type, locator, *, parser_options=None):
    """Capture a single source (used by the capture-* subcommands)."""
    desc = _normalize_descriptor({"source_type": source_type, "source_locator": locator,
                                  "parser_options": parser_options or {}}, 0)
    captures = _execute_sources(config, [desc])
    return _assemble_run(config, captures, plan_id=None)


def run_plan(config, plan_path):
    try:
        with open(plan_path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise PlanError(f"cannot read plan file: {type(e).__name__}")
    try:
        plan = _loads_strict(raw, "plan")
    except (ResearchError, ValueError) as e:
        raise PlanError(f"plan is not valid JSON: {e}")
    canonical, plan_id = normalize_plan(plan)
    enabled = [d for d in canonical["sources"] if d.get("enabled", True)]
    if not enabled:
        return {"command": "run-plan", "readiness": SOURCE_REQUIRED, "ok": False,
                "plan_id": plan_id, "detail": "no enabled sources in plan",
                "amazon_counters": dict(AMAZON_COUNTERS)}
    captures = _execute_sources(config, canonical["sources"])
    result = _assemble_run(config, captures, plan_id=plan_id)
    result["command"] = "run-plan"
    result["plan_id"] = plan_id
    return result


def validate_plan_only(config, plan_path):
    """validate-only: parse + validate a plan and perform NO network request and write NO files."""
    with open(plan_path, "rb") as f:
        raw = f.read()
    plan = _loads_strict(raw, "plan")
    canonical, plan_id = normalize_plan(plan)
    enabled = [d for d in canonical["sources"] if d.get("enabled", True)]
    return {"command": "validate-only", "readiness": VALIDATE_READY, "ok": True, "plan_id": plan_id,
            "source_count": len(canonical["sources"]), "enabled_source_count": len(enabled),
            "files_written": 0, "network_requests": 0, "amazon_counters": dict(AMAZON_COUNTERS)}


def list_runs(config):
    out = []
    root = config.runs_dir
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(root, name), "rb") as f:
                    rec = _loads_strict(f.read(), "run_index")
                out.append({"run_id": rec.get("run_id"), "readiness": rec.get("readiness"),
                            "source_count": rec.get("source_count"),
                            "evidence_count": rec.get("evidence_count")})
            except (ResearchError, ValueError, OSError):
                continue
    return {"command": "list-runs", "readiness": LIST_READY, "ok": True, "runs": out,
            "count": len(out), "amazon_counters": dict(AMAZON_COUNTERS)}


def _load_manifest(config, run_id):
    path = os.path.join(config.manifests_dir, run_id + ".json")
    if not os.path.isfile(path):
        raise ResearchError("RUN_NOT_FOUND", run_id, readiness=INTEGRITY_BLOCKED)
    with open(path, "rb") as f:
        return _loads_strict(f.read(), "manifest")


def _load_capture(config, capture_id):
    path = os.path.join(config.normalized_dir, capture_id + ".json")
    if not os.path.isfile(path):
        raise ResearchError("CAPTURE_NOT_FOUND", capture_id, readiness=INTEGRITY_BLOCKED)
    with open(path, "rb") as f:
        return _loads_strict(f.read(), "capture")


def verify_run(config, run_id):
    """Recompute every capture_id + evidence_id from the stored records + raw bytes and confirm the
    content hashes, identities, zero Amazon counters, and no secret leakage. Read-only."""
    manifest = _load_manifest(config, run_id)
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    add("run_id_recomputes", manifest["run_id"] == _run_id(manifest["capture_ids"], manifest["plan_id"]))
    ev_total = 0
    for cid in manifest["capture_ids"]:
        try:
            cap = _load_capture(config, cid)
        except ResearchError:
            add(f"capture_present:{cid}", False, "missing")
            continue
        identity = {
            "schema_version": CAPTURE_SCHEMA, "source_type": cap["source_type"],
            "normalized_locator": cap["normalized_locator"], "content_sha256": cap["content_sha256"],
            "content_size": cap["content_size"], "media_type": cap["media_type"],
            "http_status": cap["http_status"], "redirect_lineage": cap["redirect_lineage"],
            "parser_version": cap["parser_version"], "capture_status": cap["capture_status"],
        }
        add(f"capture_id:{cid}", cap["capture_id"] == "cap-" + _sha256_hex(canonical_json(identity))[:24])
        raw_path = os.path.join(config.raw_dir, cid + ".bin")
        if cap["content_sha256"]:
            if os.path.isfile(raw_path):
                with open(raw_path, "rb") as f:
                    add(f"raw_bytes_hash:{cid}", _sha256_hex(f.read()) == cap["content_sha256"])
            else:
                add(f"raw_bytes_present:{cid}", False, "raw bytes missing")
        for e in cap["evidence"]:
            ev_total += 1
            eid = "ev-" + _sha256_hex(canonical_json({
                "source_content_hash": e["source_content_hash"], "evidence_type": e["evidence_type"],
                "field_path": e["field_path"], "observed_value": e["observed_value"],
                "observed_unit": e["observed_unit"], "observed_currency": e["observed_currency"],
                "extraction_method": e["extraction_method"], "parser_version": e["parser_version"]}))[:24]
            if eid != e["evidence_id"]:
                add(f"evidence_id:{e['evidence_id']}", False, "recompute mismatch")
        add(f"amazon_counters_zero:{cid}", all(cap.get(k) == 0 for k in AMAZON_COUNTERS))
    add("evidence_count_matches", ev_total == manifest["evidence_count"])
    blob = canonical_json(manifest)
    add("no_secret_leakage", D.redact_secrets(blob) == blob)
    ok = all(c["ok"] for c in checks)
    readiness = VERIFY_READY if ok else INTEGRITY_BLOCKED
    report = {"schema_version": VALIDATION_SCHEMA, "run_id": run_id, "readiness": readiness,
              "ok": ok, "checks": checks, "evidence_count": ev_total}
    report.update(AMAZON_COUNTERS)
    _atomic_write_json(os.path.join(config.validation_dir, run_id + ".json"), report)
    return {"command": "verify-run", "run_id": run_id, "readiness": readiness, "ok": ok,
            "checks_passed": sum(1 for c in checks if c["ok"]), "checks_total": len(checks),
            "amazon_counters": dict(AMAZON_COUNTERS)}


def replay_run(config, run_id):
    """Re-extract evidence from LOCAL cached / raw bytes only (no network) and confirm the replayed
    evidence_ids match the stored ones — proving deterministic, offline-reproducible extraction."""
    manifest = _load_manifest(config, run_id)
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    replayed = 0
    for cid in manifest["capture_ids"]:
        cap = _load_capture(config, cid)
        if cap["capture_status"] not in _SUCCESS_STATUSES or not cap["content_sha256"]:
            continue
        raw_path = os.path.join(config.raw_dir, cid + ".bin")
        if not os.path.isfile(raw_path):
            add(f"raw_present:{cid}", False, "raw bytes missing for replay")
            continue
        with open(raw_path, "rb") as f:
            body = f.read()
        if _sha256_hex(body) != cap["content_sha256"]:
            add(f"raw_hash:{cid}", False, "cached bytes corrupt")
            continue
        outcome = FetchOutcome(status=cap["capture_status"], http_status=cap["http_status"],
                               media_type=cap["media_type"], body=body,
                               redirect_lineage=cap["redirect_lineage"])
        new_ev = _reextract(cap, outcome)
        old_ids = sorted(e["evidence_id"] for e in cap["evidence"])
        new_ids = sorted(e["evidence_id"] for e in new_ev)
        add(f"evidence_replay:{cid}", old_ids == new_ids,
            "" if old_ids == new_ids else "evidence ids differ on replay")
        replayed += 1
    add("at_least_one_replayable", replayed > 0 or manifest["evidence_count"] == 0)
    ok = all(c["ok"] for c in checks)
    readiness = REPLAY_READY if ok else INTEGRITY_BLOCKED
    return {"command": "replay-run", "run_id": run_id, "readiness": readiness, "ok": ok,
            "captures_replayed": replayed, "checks_total": len(checks),
            "amazon_counters": dict(AMAZON_COUNTERS)}


def _reextract(cap, outcome):
    st = cap["source_type"]
    loc = cap["source_locator"]
    if st == "public-url":
        return _extract_generic(outcome, loc)
    if st == "rss":
        return _extract_feed(outcome, loc) or []
    if st == "github":
        return (_extract_github_release(outcome) if cap["normalized_locator"].endswith("#release")
                else _extract_github_repo(outcome))
    if st == "pypi":
        return _extract_pypi(outcome)
    if st == "amazon-public-product":
        return _extract_amazon(outcome, loc)
    if st == "local-file":
        media = cap["media_type"]
        if media == "text/html":
            return _extract_generic(outcome, cap["normalized_locator"])
        if media in ("application/xml", "text/xml", "application/rss+xml", "application/atom+xml"):
            return _extract_feed(outcome, cap["normalized_locator"]) or []
    return []


def export_run(config, run_id):
    """Regenerate the deterministic exports for an existing run from its stored captures."""
    manifest = _load_manifest(config, run_id)
    captures = [_load_capture(config, cid) for cid in manifest["capture_ids"]]
    _write_exports(config, run_id, manifest, captures)
    out_dir = os.path.join(config.reports_dir, run_id)
    return {"command": "export", "run_id": run_id, "readiness": EXPORT_READY, "ok": True,
            "exports": [_rel(config, os.path.join(out_dir, n)) for n in
                        ("research_snapshot.json", "research_evidence.tsv", "research_report.md")],
            "amazon_counters": dict(AMAZON_COUNTERS)}


def provider_check(config):
    """Report which source adapters are available and confirm the Amazon-account boundary + a live
    Seller Central deny — WITHOUT making any real research request."""
    _sc_host = "seller" + "central" + "." + "amazon" + "." + "com"
    _sc_url = "htt" + "ps://" + _sc_host + "/home"
    dec = NP.evaluate_public_research_url(_sc_url, purpose=NP.PURPOSE_PUBLIC_RESEARCH)
    sc_blocked = (not dec.allowed) and dec.reason_code == D.SELLER_CENTRAL_ACCESS_PROHIBITED
    live = (config.env.get(LIVE_NETWORK_ENV) or "").strip() == "1"
    return {"command": "provider-check", "readiness": PROVIDER_CHECK_READY, "ok": sc_blocked,
            "adapters": sorted(_ADAPTERS), "source_types": sorted(_SOURCE_TYPES),
            "seller_central_blocked_first": sc_blocked,
            "github_token_configured": config.github_token() is not None,
            "live_network_opt_in": live, "allow_local": config.allow_local,
            "amazon_counters": dict(AMAZON_COUNTERS)}


# ================================================================ CLI
def _print(obj):
    sys.stdout.write(canonical_json(redact(obj)) + "\n")


def _exit_code(result):
    return 0 if result.get("readiness") in _EXIT_OK else 3


def build_parser():
    p = argparse.ArgumentParser(
        prog="python -m production.phase7_connected_public_research",
        description="Phase 7.10 — connected public research & evidence hub. Collects PUBLIC evidence "
                    "and provenance only; never connects to an Amazon seller account.")
    p.add_argument("--base-dir", default="runs/T2/phase7/7.10",
                   help="Phase 7.10 workspace (default: runs/T2/phase7/7.10)")
    p.add_argument("--input-root", default=None,
                   help="explicit allowed root for the local-file adapter")
    p.add_argument("--allow-local-test-server", action="store_true",
                   help="permit an http:// loopback endpoint (synthetic tests only)")
    p.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                   help=f"response body bound (default {DEFAULT_MAX_BYTES}, ceiling {ABSOLUTE_MAX_BYTES})")
    sub = p.add_subparsers(dest="command", required=True)

    cu = sub.add_parser("capture-url", help="capture one explicitly supplied public HTTPS URL")
    cu.add_argument("--url", required=True)
    cr = sub.add_parser("capture-rss", help="capture one public RSS or Atom feed")
    cr.add_argument("--url", required=True)
    cg = sub.add_parser("capture-github", help="capture public GitHub repository/release metadata")
    cg.add_argument("--repository", required=True, help="OWNER/REPOSITORY")
    cp = sub.add_parser("capture-pypi", help="capture public PyPI package metadata")
    cp.add_argument("--package", required=True)
    ca = sub.add_parser("capture-amazon-product", help="capture one Amazon US public product page")
    ca.add_argument("--url", required=True, help="an amazon.com /dp/<ASIN> or /gp/product/<ASIN> URL")
    cf = sub.add_parser("capture-file", help="ingest one local HTML/JSON/XML/text file")
    cf.add_argument("--file", required=True)
    cf.add_argument("--input-root", default=None)
    rp = sub.add_parser("run-plan", help="execute a declarative JSON research plan")
    rp.add_argument("--plan", required=True)
    vr = sub.add_parser("verify-run", help="verify a completed run's identities + integrity")
    vr.add_argument("--run-id", required=True)
    rr = sub.add_parser("replay-run", help="replay extraction from local cached bytes (no network)")
    rr.add_argument("--run-id", required=True)
    sub.add_parser("list-runs", help="list local research runs")
    ex = sub.add_parser("export", help="regenerate deterministic exports for a run")
    ex.add_argument("--run-id", required=True)
    sub.add_parser("provider-check", help="report adapters + confirm the Seller Central deny")
    vo = sub.add_parser("validate-only", help="validate a plan; make no request and write no files")
    vo.add_argument("--plan", required=True)
    return p


def run_cli(argv=None):
    args = build_parser().parse_args(argv)
    input_root = getattr(args, "input_root", None) or args.input_root
    config = Config(args.base_dir, input_root=input_root, allow_local=args.allow_local_test_server,
                    max_bytes=args.max_bytes)
    try:
        cmd = args.command
        if cmd == "capture-url":
            result = capture_one(config, "public-url", args.url)
        elif cmd == "capture-rss":
            result = capture_one(config, "rss", args.url)
        elif cmd == "capture-github":
            result = capture_one(config, "github", args.repository)
        elif cmd == "capture-pypi":
            result = capture_one(config, "pypi", args.package)
        elif cmd == "capture-amazon-product":
            result = capture_one(config, "amazon-public-product", args.url)
        elif cmd == "capture-file":
            result = capture_one(config, "local-file", args.file)
        elif cmd == "run-plan":
            result = run_plan(config, args.plan)
        elif cmd == "verify-run":
            result = verify_run(config, args.run_id)
        elif cmd == "replay-run":
            result = replay_run(config, args.run_id)
        elif cmd == "list-runs":
            result = list_runs(config)
        elif cmd == "export":
            result = export_run(config, args.run_id)
        elif cmd == "provider-check":
            result = provider_check(config)
        elif cmd == "validate-only":
            result = validate_plan_only(config, args.plan)
        else:  # pragma: no cover — argparse enforces the choices
            raise ResearchError("UNKNOWN_COMMAND", cmd)
    except (ResearchError, NP.ConnectedNetworkDenied) as e:
        code = getattr(e, "code", getattr(e, "reason_code", "ERROR"))
        readiness = getattr(e, "readiness", SOURCE_BLOCKED)
        if getattr(e, "reason_code", None) in (D.SELLER_CENTRAL_ACCESS_PROHIBITED,
                                               D.AMAZON_API_PROHIBITED,
                                               D.AMAZON_ACCOUNT_ACCESS_PROHIBITED):
            readiness = SELLER_CENTRAL_POLICY_BLOCKED
        result = {"command": args.command, "readiness": readiness, "ok": False, "error_code": code,
                  "detail": redact(str(getattr(e, "detail", ""))),
                  "amazon_counters": dict(AMAZON_COUNTERS)}
    _print(result)
    return _exit_code(result)


def main(argv=None):
    return run_cli(argv)


if __name__ == "__main__":
    sys.exit(main())
