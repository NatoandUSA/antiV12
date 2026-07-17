#!/usr/bin/env python3
"""Central runtime policy authority (Session 5B / ACT-016, ACT-017).

One shared object that answers, deterministically and offline by default:
    - are we offline-only?
    - is external AI allowed?  (default NO)
    - is outbound internet allowed?  (default NO)
    - what host/port do we bind, and is it loopback-only?

Every consumer — dashboard startup, API routes, /healthz, the outbound-network
guard, subprocess/stage execution, diagnostics and the tests — loads the same
policy so there is a single source of truth. The safe defaults hold even when no
environment variable and no config file exist. Invalid values fail clearly
(recorded as validation errors) and never silently flip a switch to an unsafe
"true". Secrets are never stored on, or emitted by, the policy.
"""
import hashlib
import json
import os

import diagnostics as D

# ---- defaults (production-safe; never 0.0.0.0 / external-on) ------------------
DEFAULTS = {
    "OFFLINE_ONLY": True,
    "EXTERNAL_AI_ENABLED": False,
    "OUTBOUND_NETWORK_ENABLED": False,
    "BIND_HOST": "127.0.0.1",
    "BIND_PORT": 5000,
    "LOOPBACK_ONLY": True,
}

_TRUE = {"true", "1", "yes", "on", "y", "t"}
_FALSE = {"false", "0", "no", "off", "n", "f"}

# Loopback host names / literals we accept. Any 127.0.0.0/8 address counts.
_LOOPBACK_NAMES = {"localhost", "::1", "0:0:0:0:0:0:0:1", "[::1]"}


class RuntimePolicyError(Exception):
    """Raised at startup when the runtime policy is invalid (e.g. unsafe bind)."""


def is_loopback_host(host):
    """True only for verified loopback addresses / names."""
    if not host:
        return False
    h = str(host).strip().lower()
    if h in _LOOPBACK_NAMES:
        return True
    if h == "0.0.0.0":              # explicitly NOT loopback — binds every interface
        return False
    return h.startswith("127.")     # 127.0.0.0/8


def _coerce_bool(value, field, errors):
    """Normalize a boolean-ish value. On an invalid value, record a validation
    error and return the SAFE default for *field* (never a silent unsafe true)."""
    if isinstance(value, bool):
        return value
    if value is None:
        return DEFAULTS[field]
    s = str(value).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    errors.append(f"{field}: invalid boolean value {value!r} "
                  f"(use true/false, 1/0, yes/no, on/off)")
    return DEFAULTS[field]


def _coerce_int(value, field, errors):
    if value is None:
        return DEFAULTS[field]
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        errors.append(f"{field}: invalid integer value {value!r}")
        return DEFAULTS[field]
    if not (0 < n < 65536):
        errors.append(f"{field}: port {n} out of range 1-65535")
        return DEFAULTS[field]
    return n


class RuntimePolicy:
    """Immutable-ish snapshot of the resolved runtime policy. Secret-free."""

    def __init__(self, values, warnings, validation_errors, environment_source,
                 credentials_present):
        self.offline_only = values["OFFLINE_ONLY"]
        self.external_ai_enabled = values["EXTERNAL_AI_ENABLED"]
        self.outbound_network_enabled = values["OUTBOUND_NETWORK_ENABLED"]
        self.bind_host = values["BIND_HOST"]
        self.bind_port = values["BIND_PORT"]
        self.loopback_only = values["LOOPBACK_ONLY"]
        self.environment_source = environment_source
        self.warnings = list(warnings)
        self.validation_errors = list(validation_errors)
        # booleans only — which providers have a credential present, never the value
        self.credentials_present = dict(credentials_present)
        self.policy_sha256 = self._compute_sha256()

    # -- derived --------------------------------------------------------------
    @property
    def is_valid(self):
        return not self.validation_errors

    @property
    def loopback_only_effective(self):
        return self.loopback_only and is_loopback_host(self.bind_host)

    def _canonical(self):
        """Deterministic, secret-free dict used for the policy hash."""
        return {
            "offline_only": self.offline_only,
            "external_ai_enabled": self.external_ai_enabled,
            "outbound_network_enabled": self.outbound_network_enabled,
            "bind_host": self.bind_host,
            "bind_port": self.bind_port,
            "loopback_only": self.loopback_only,
        }

    def _compute_sha256(self):
        blob = json.dumps(self._canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_dict(self):
        """Full, secret-safe serialization (safe for /healthz, dashboard, tests)."""
        d = dict(self._canonical())
        d.update({
            "loopback_only_effective": self.loopback_only_effective,
            "environment_source": self.environment_source,
            "warnings": list(self.warnings),
            "validation_errors": list(self.validation_errors),
            "is_valid": self.is_valid,
            "credentials_present": dict(self.credentials_present),
            "policy_sha256": self.policy_sha256,
        })
        return d

    def raise_if_invalid(self):
        if not self.is_valid:
            D.record_event(D.INVALID_RUNTIME_POLICY,
                           "runtime policy invalid: " + "; ".join(self.validation_errors))
            raise RuntimePolicyError("; ".join(self.validation_errors))
        return self

    def __repr__(self):
        return (f"RuntimePolicy(offline_only={self.offline_only}, "
                f"external_ai={self.external_ai_enabled}, "
                f"outbound={self.outbound_network_enabled}, "
                f"bind={self.bind_host}:{self.bind_port}, valid={self.is_valid})")


def _read_config(config_path):
    if not config_path or not os.path.exists(config_path):
        return {}, False
    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}, False
        # accept either lower or upper case keys
        return {str(k).upper(): v for k, v in data.items()}, True
    except Exception:
        return {}, False


def load_runtime_policy(env=None, config_path=None):
    """Resolve the runtime policy from (defaults <- config file <- environment).

    Always returns a RuntimePolicy. Configuration problems are captured in
    ``validation_errors`` (so /healthz can report 503) rather than raised — call
    ``raise_if_invalid()`` at startup when you want a hard failure. *env* defaults
    to ``os.environ``; pass an explicit dict to test deterministically.
    """
    if env is None:
        env = os.environ
    warnings = []
    errors = []

    cfg, cfg_used = _read_config(config_path)

    def _raw(key):
        # environment wins over config file
        if key in env:
            return env[key]
        if key in cfg:
            return cfg[key]
        return None

    env_used = any(k in env for k in DEFAULTS)

    values = {}
    values["OFFLINE_ONLY"] = _coerce_bool(_raw("OFFLINE_ONLY"), "OFFLINE_ONLY", errors)
    values["EXTERNAL_AI_ENABLED"] = _coerce_bool(
        _raw("EXTERNAL_AI_ENABLED"), "EXTERNAL_AI_ENABLED", errors)
    values["OUTBOUND_NETWORK_ENABLED"] = _coerce_bool(
        _raw("OUTBOUND_NETWORK_ENABLED"), "OUTBOUND_NETWORK_ENABLED", errors)
    values["LOOPBACK_ONLY"] = _coerce_bool(_raw("LOOPBACK_ONLY"), "LOOPBACK_ONLY", errors)
    raw_host = _raw("BIND_HOST")
    values["BIND_HOST"] = (str(raw_host).strip() if raw_host is not None
                           else DEFAULTS["BIND_HOST"])
    values["BIND_PORT"] = _coerce_int(_raw("BIND_PORT"), "BIND_PORT", errors)

    # -- invariant 1: offline_only forces external AI + outbound OFF ----------
    if values["OFFLINE_ONLY"]:
        if values["EXTERNAL_AI_ENABLED"]:
            warnings.append("EXTERNAL_AI_ENABLED requested but forced OFF by OFFLINE_ONLY")
            values["EXTERNAL_AI_ENABLED"] = False
        if values["OUTBOUND_NETWORK_ENABLED"]:
            warnings.append("OUTBOUND_NETWORK_ENABLED requested but forced OFF by OFFLINE_ONLY")
            values["OUTBOUND_NETWORK_ENABLED"] = False

    # -- invariant 5/6: a credential alone must never enable external AI ------
    # We only *observe* which credentials exist; we never let their presence flip
    # external_ai_enabled. Recorded as booleans, never as values.
    credentials_present = {name: bool(env.get(name) or os.environ.get(name))
                           for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                                        "GEMINI_API_KEY", "GOOGLE_API_KEY")}
    if any(credentials_present.values()) and not values["EXTERNAL_AI_ENABLED"]:
        warnings.append("credential present but external AI stays disabled "
                        "(a key alone never enables network behavior)")

    # -- invariant 2/3/4: bind host must be loopback (0.0.0.0 always fails) ----
    host = values["BIND_HOST"]
    if host == "0.0.0.0":
        errors.append("BIND_HOST 0.0.0.0 is never allowed (binds all interfaces)")
    elif values["LOOPBACK_ONLY"] and not is_loopback_host(host):
        errors.append(f"BIND_HOST {host!r} is not a loopback address "
                      f"while LOOPBACK_ONLY=true")
    elif not values["LOOPBACK_ONLY"] and not is_loopback_host(host):
        # loopback_only disabled but host is external — still unsafe for this tool
        errors.append(f"BIND_HOST {host!r} is not loopback; external binding is not "
                      f"permitted in this offline-first tool")

    if cfg_used and env_used:
        source = "config+env"
    elif cfg_used:
        source = "config"
    elif env_used:
        source = "env"
    else:
        source = "defaults"

    if errors:
        D.record_event(D.INVALID_RUNTIME_POLICY,
                       "runtime policy validation error: " + "; ".join(errors))

    return RuntimePolicy(values, warnings, errors, source, credentials_present)


def load_runtime_policy_strict(env=None, config_path=None):
    """Load the policy and raise RuntimePolicyError if it is invalid (startup use)."""
    return load_runtime_policy(env=env, config_path=config_path).raise_if_invalid()
