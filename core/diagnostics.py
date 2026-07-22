#!/usr/bin/env python3
"""Local diagnostic events + secret redaction (Session 5B / ACT-016, ACT-020).

One tiny local structure — no telemetry service, no network. It gives the runtime
policy, the outbound-network guard and the subprocess runner a shared, secret-safe
way to record *why* something was blocked or truncated, so tests and the dashboard
can read a stable error code and a redacted message. Everything stays in memory on
the owner's machine and is bounded so it never grows without limit.
"""
import os
import re
from collections import deque

# ---- stable error codes (Part H) ---------------------------------------------
OFFLINE_ONLY = "OFFLINE_ONLY"
EXTERNAL_AI_DISABLED = "EXTERNAL_AI_DISABLED"
OUTBOUND_NETWORK_DISABLED = "OUTBOUND_NETWORK_DISABLED"
UNSAFE_BIND_HOST = "UNSAFE_BIND_HOST"
INVALID_RUNTIME_POLICY = "INVALID_RUNTIME_POLICY"
SUBPROCESS_TIMEOUT = "SUBPROCESS_TIMEOUT"
SUBPROCESS_EXIT_ERROR = "SUBPROCESS_EXIT_ERROR"
SUBPROCESS_OUTPUT_TRUNCATED = "SUBPROCESS_OUTPUT_TRUNCATED"
SUBPROCESS_START_FAILED = "SUBPROCESS_START_FAILED"

# ---- Session 6A.1 connectivity + permanent Amazon-account boundary codes -----
INVALID_CONNECTIVITY_MODE = "INVALID_CONNECTIVITY_MODE"
NETWORK_REQUEST_DENIED = "NETWORK_REQUEST_DENIED"
EXTERNAL_DENIED_IN_MODE = "EXTERNAL_DENIED_IN_MODE"
CAPABILITY_NOT_ENABLED = "CAPABILITY_NOT_ENABLED"
DATA_CLASSIFICATION_BLOCKED = "DATA_CLASSIFICATION_BLOCKED"
EXTERNAL_AI_PROVIDER_NOT_APPROVED = "EXTERNAL_AI_PROVIDER_NOT_APPROVED"
TOOLKIT_UPDATE_INSTALL_PROHIBITED = "TOOLKIT_UPDATE_INSTALL_PROHIBITED"
# Amazon marketplace read (public product/search) — never bulk-scraped by this tool
AMAZON_BULK_OR_MARKETPLACE_SCRAPING_NOT_SUPPORTED = \
    "AMAZON_BULK_OR_MARKETPLACE_SCRAPING_NOT_SUPPORTED"
# ---- Phase 7.9 connected backup/update network policy (allowlist model) -------
# The application is no longer globally offline-only: encrypted backups, GitHub update
# checks and package-index metadata are permitted for the configured, allowlisted hosts.
# The permanent Amazon-account boundary above still wins first, in every path.
CONNECTED_URL_INVALID = "CONNECTED_URL_INVALID"
INSECURE_SCHEME_BLOCKED = "INSECURE_SCHEME_BLOCKED"
HOST_NOT_ALLOWLISTED = "HOST_NOT_ALLOWLISTED"
REDIRECT_TARGET_BLOCKED = "REDIRECT_TARGET_BLOCKED"
CREDENTIAL_FORWARDING_BLOCKED = "CREDENTIAL_FORWARDING_BLOCKED"
LOCAL_ENDPOINT_NOT_ENABLED = "LOCAL_ENDPOINT_NOT_ENABLED"
CONNECTED_PURPOSE_UNKNOWN = "CONNECTED_PURPOSE_UNKNOWN"
AMAZON_DESTINATION_UNVERIFIED = "AMAZON_DESTINATION_UNVERIFIED"
AMAZON_PUBLIC_DOCUMENTATION_UNVERIFIED = "AMAZON_PUBLIC_DOCUMENTATION_UNVERIFIED"
# Permanent Amazon-account boundary (the one hard line — never configurable)
AMAZON_ACCOUNT_ACCESS_PROHIBITED = "AMAZON_ACCOUNT_ACCESS_PROHIBITED"
AMAZON_CREDENTIAL_STORAGE_PROHIBITED = "AMAZON_CREDENTIAL_STORAGE_PROHIBITED"
SELLER_CENTRAL_ACCESS_PROHIBITED = "SELLER_CENTRAL_ACCESS_PROHIBITED"
AMAZON_API_PROHIBITED = "AMAZON_API_PROHIBITED"
AMAZON_BROWSER_AUTOMATION_PROHIBITED = "AMAZON_BROWSER_AUTOMATION_PROHIBITED"
AMAZON_WRITE_PROHIBITED = "AMAZON_WRITE_PROHIBITED"
AMAZON_REPORT_PULL_PROHIBITED = "AMAZON_REPORT_PULL_PROHIBITED"
AMAZON_REVIEW_ACTION_PROHIBITED = "AMAZON_REVIEW_ACTION_PROHIBITED"

ERROR_CODES = frozenset({
    OFFLINE_ONLY, EXTERNAL_AI_DISABLED, OUTBOUND_NETWORK_DISABLED, UNSAFE_BIND_HOST,
    INVALID_RUNTIME_POLICY, SUBPROCESS_TIMEOUT, SUBPROCESS_EXIT_ERROR,
    SUBPROCESS_OUTPUT_TRUNCATED, SUBPROCESS_START_FAILED,
    INVALID_CONNECTIVITY_MODE, NETWORK_REQUEST_DENIED, EXTERNAL_DENIED_IN_MODE,
    CAPABILITY_NOT_ENABLED, DATA_CLASSIFICATION_BLOCKED,
    EXTERNAL_AI_PROVIDER_NOT_APPROVED, TOOLKIT_UPDATE_INSTALL_PROHIBITED,
    AMAZON_BULK_OR_MARKETPLACE_SCRAPING_NOT_SUPPORTED, AMAZON_DESTINATION_UNVERIFIED,
    AMAZON_PUBLIC_DOCUMENTATION_UNVERIFIED,
    AMAZON_ACCOUNT_ACCESS_PROHIBITED, AMAZON_CREDENTIAL_STORAGE_PROHIBITED,
    SELLER_CENTRAL_ACCESS_PROHIBITED, AMAZON_API_PROHIBITED,
    AMAZON_BROWSER_AUTOMATION_PROHIBITED, AMAZON_WRITE_PROHIBITED,
    AMAZON_REPORT_PULL_PROHIBITED, AMAZON_REVIEW_ACTION_PROHIBITED,
    CONNECTED_URL_INVALID, INSECURE_SCHEME_BLOCKED, HOST_NOT_ALLOWLISTED,
    REDIRECT_TARGET_BLOCKED, CREDENTIAL_FORWARDING_BLOCKED, LOCAL_ENDPOINT_NOT_ENABLED,
    CONNECTED_PURPOSE_UNKNOWN,
})

REDACTED = "***REDACTED***"

# Environment variables whose values are secrets and must never leak into a log,
# a diagnostic, an exception, a health payload or a command display.
SECRET_ENV_VARS = (
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "OPENAI_ORG", "AWS_SECRET_ACCESS_KEY", "AZURE_OPENAI_API_KEY",
    # Phase 7.9 connected-backup secrets — a passphrase or cloud credential must never
    # leak into a log, diagnostic, exception, manifest or command display.
    "PHASE7_9_BACKUP_PASSPHRASE", "PHASE7_9_S3_ACCESS_KEY_ID",
    "PHASE7_9_S3_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID", "AWS_SESSION_TOKEN",
)

# Known credential token shapes. Deliberately conservative — provider prefixes and
# clearly key-shaped bearer tokens only, so we never redact ordinary listing copy.
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"AIza[A-Za-z0-9_\-]{16,}"),          # Google API keys
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{12,}", re.IGNORECASE),
]

# Argument names that mean "the next token is a secret".
_SECRET_FLAG = re.compile(
    r"(?i)(api[_-]?key|apikey|x-api-key|authorization|auth[_-]?token|access[_-]?token"
    r"|secret|password|passwd|bearer|token)$"
)


def _known_secret_values(extra=()):
    vals = []
    for name in SECRET_ENV_VARS:
        v = os.environ.get(name)
        if v and v.strip():
            vals.append(v.strip())
    for v in extra or ():
        if v and str(v).strip():
            vals.append(str(v).strip())
    # Redact the longest values first so a shorter secret that is a substring of a
    # longer one does not leave a tail behind.
    return sorted(set(vals), key=len, reverse=True)


def redact_secrets(text, extra_secrets=()):
    """Return *text* with any known secret value or key-shaped token replaced.

    Safe on non-strings (returns them unchanged) so callers can pass it anything.
    """
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)
    for secret in _known_secret_values(extra_secrets):
        if secret and secret in text:
            text = text.replace(secret, REDACTED)
    for pat in _SECRET_PATTERNS:
        text = pat.sub(REDACTED, text)
    return text


def redact_command(command, extra_secrets=()):
    """Render an argv list (or string) as a single secret-safe display string.

    A token that follows a secret-looking flag is redacted, `--flag=SECRET` is
    redacted after the `=`, and any key-shaped token is redacted on its own.
    """
    if isinstance(command, str):
        return redact_secrets(command, extra_secrets)
    parts = []
    redact_next = False
    for raw in (command or []):
        tok = str(raw)
        if redact_next:
            parts.append(REDACTED)
            redact_next = False
            continue
        if _SECRET_FLAG.search(tok):
            # bare secret flag -> redact the following token
            parts.append(tok)
            redact_next = True
            continue
        if "=" in tok:
            name, _, _val = tok.partition("=")
            if _SECRET_FLAG.search(name):
                parts.append(name + "=" + REDACTED)
                continue
        parts.append(redact_secrets(tok, extra_secrets))
    return " ".join(parts)


class DiagnosticEvent:
    """A single local, secret-free diagnostic record."""

    __slots__ = ("code", "message", "details")

    def __init__(self, code, message, details=None):
        self.code = code
        self.message = redact_secrets(message)
        self.details = {k: redact_secrets(v) if isinstance(v, str) else v
                        for k, v in (details or {}).items()}

    def to_dict(self):
        return {"code": self.code, "message": self.message, "details": dict(self.details)}

    def __repr__(self):
        return f"DiagnosticEvent({self.code!r}, {self.message!r})"


# Bounded, in-memory ring buffer. Local only; never persisted, never sent anywhere.
_EVENTS = deque(maxlen=100)


def record_event(code, message, details=None):
    """Record a redacted local diagnostic event and return it."""
    ev = DiagnosticEvent(code, message, details)
    _EVENTS.append(ev)
    return ev


def last_event():
    return _EVENTS[-1] if _EVENTS else None


def recent_events(n=10):
    if n <= 0:
        return []
    return list(_EVENTS)[-n:]


def clear_events():
    """Test/support helper — reset the local event buffer."""
    _EVENTS.clear()
