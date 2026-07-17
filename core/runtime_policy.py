#!/usr/bin/env python3
"""Central runtime policy authority (Session 5B / ACT-016, ACT-017; Session 6A.1).

One shared object that answers, deterministically, every runtime-safety question:
    - what connectivity mode are we in?  (CONNECTED_RESEARCH | LOCAL_SAFE | TEST_DENY_EXTERNAL)
    - which connected-research capabilities are enabled?  (public web, third-party, AI, ...)
    - is external AI allowed / enabled, and for which provider?  (default allowed-not-enabled)
    - what host/port do we bind, and is it loopback-only?  (always loopback)
    - is the owner's Amazon account permanently isolated?  (always YES — not configurable)

Session 6A.1 replaces the old "offline-only" identity with the owner-approved
connectivity policy: the toolkit is CONNECTED TO THE OPEN WORLD but ISOLATED FROM THE
OWNER'S AMAZON ACCOUNT. The legacy ``offline_only`` field is preserved unchanged (it now
means "the legacy blanket outbound/external-AI path is closed") and the connectivity mode
is derived from it, so every prior consumer and test keeps its exact behavior. The one
hard line — no Amazon-account capability of any kind — is encoded as immutable fields that
no environment variable, config value, flag, or mode can flip.

Every consumer — dashboard startup, API routes, /healthz, the outbound-network guard,
the approved HTTP client, subprocess/stage execution, diagnostics and the tests — loads
the same policy so there is a single source of truth. Safe defaults hold even when no
environment variable and no config file exist. Invalid values fail clearly (recorded as
validation errors) and never silently flip a switch to an unsafe "true". Secrets are never
stored on, or emitted by, the policy.
"""
import hashlib
import json
import os

import diagnostics as D

# ---- legacy defaults (production-safe; never 0.0.0.0 / external-on) -----------
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

# ---- connectivity modes (Session 6A.1) ---------------------------------------
CONNECTED_RESEARCH = "CONNECTED_RESEARCH"
LOCAL_SAFE = "LOCAL_SAFE"
TEST_DENY_EXTERNAL = "TEST_DENY_EXTERNAL"
CONNECTIVITY_MODES = (CONNECTED_RESEARCH, LOCAL_SAFE, TEST_DENY_EXTERNAL)

# The connectivity mode written for a brand-new installation. A bare environment
# (no env var, no config file) resolves to the safe LOCAL_SAFE default; a real
# install explicitly opts into CONNECTED_RESEARCH via connectivity-policy.json.
NEW_INSTALL_CONNECTIVITY_MODE = CONNECTED_RESEARCH

# Capability names the toolkit MAY have (connected-research surface).
ALLOWED_CAPABILITIES = (
    "LOOPBACK_ACCESS",
    "PUBLIC_WEB_RESEARCH",
    "PUBLIC_POLICY_RESEARCH",
    "AMAZON_PUBLIC_DOCUMENTATION_READ",
    "THIRD_PARTY_DATA",
    "MARKET_DATA_CONNECTION",
    "SUPPLIER_CONNECTION",
    "EXTERNAL_AI_CONNECTION",
    "TOOLKIT_UPDATE_DISCOVERY",
)

# The one hard line — capability names that must NEVER exist, in any mode. Any
# attempt to configure one returns a typed policy error (see network_policy).
PROHIBITED_CAPABILITIES = (
    "AMAZON_LOGIN",
    "AMAZON_SELLER_CENTRAL_ACCESS",
    "AMAZON_AUTHENTICATED_ACCESS",
    "AMAZON_SELLER_SESSION",
    "AMAZON_CREDENTIAL_STORAGE",
    "AMAZON_API_ACCESS",
    "AMAZON_SP_API",
    "AMAZON_MWS",
    "AMAZON_ADVERTISING_API",
    "AMAZON_BROWSER_AUTOMATION",
    "AMAZON_ACCOUNT_REPORT_PULL",
    "AMAZON_LISTING_WRITE",
    "AMAZON_PRICE_WRITE",
    "AMAZON_INVENTORY_WRITE",
    "AMAZON_APLUS_WRITE",
    "AMAZON_PPC_WRITE",
    "AMAZON_REVIEW_MANIPULATION",
)

# Immutable Amazon-account boundary. These are the same in EVERY mode and can never
# be changed by env, config, flag, plugin, or dashboard action. There is no Amazon
# credential store; there is no seller-account path; there are no Amazon writes.
AMAZON_BOUNDARY = {
    "amazon_account_isolation": True,
    "amazon_credential_store_available": False,
    "amazon_seller_central_enabled": False,
    "amazon_authenticated_access_enabled": False,
    "amazon_api_enabled": False,
    "amazon_browser_automation_enabled": False,
    "amazon_account_report_pull_enabled": False,
    "amazon_network_writes_enabled": False,
}

# The connected-research capability flags, and their new-install default value in
# CONNECTED_RESEARCH mode. In LOCAL_SAFE / TEST_DENY_EXTERNAL every external flag is
# forced False (loopback stays available; deterministic local fallback stays on).
_CAPABILITY_FIELDS = (
    "public_web_research_enabled",
    "public_policy_research_enabled",
    "amazon_public_documentation_enabled",
    "third_party_data_enabled",
    "market_data_enabled",
    "supplier_connections_enabled",
    "toolkit_update_discovery_enabled",
)
_CONNECTED_RESEARCH_DEFAULTS = {
    "public_web_research_enabled": True,
    "public_policy_research_enabled": True,
    "amazon_public_documentation_enabled": False,   # separate, human-triggered, off by default
    "third_party_data_enabled": True,
    "market_data_enabled": True,
    "supplier_connections_enabled": True,
    "toolkit_update_discovery_enabled": True,
}
# env/config key -> capability field
_CAPABILITY_ENV = {
    "PUBLIC_WEB_RESEARCH_ENABLED": "public_web_research_enabled",
    "PUBLIC_POLICY_RESEARCH_ENABLED": "public_policy_research_enabled",
    "AMAZON_PUBLIC_DOCUMENTATION_ENABLED": "amazon_public_documentation_enabled",
    "THIRD_PARTY_DATA_ENABLED": "third_party_data_enabled",
    "MARKET_DATA_ENABLED": "market_data_enabled",
    "SUPPLIER_CONNECTIONS_ENABLED": "supplier_connections_enabled",
    "TOOLKIT_UPDATE_DISCOVERY_ENABLED": "toolkit_update_discovery_enabled",
}


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


def _coerce_opt_bool(value):
    """Boolean coercion for optional capability flags: None when unset/invalid."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return None


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
                 credentials_present, connectivity=None):
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
        self._apply_connectivity(connectivity or _default_connectivity(self.offline_only))

    # -- connectivity layer (Session 6A.1) ------------------------------------
    def _apply_connectivity(self, conn):
        self.connectivity_mode = conn["connectivity_mode"]
        self.connected_research = self.connectivity_mode == CONNECTED_RESEARCH
        self.local_safe = self.connectivity_mode == LOCAL_SAFE
        self.test_deny_external = self.connectivity_mode == TEST_DENY_EXTERNAL
        for field in _CAPABILITY_FIELDS:
            setattr(self, field, conn[field])
        self.external_ai_allowed = conn["external_ai_allowed"]
        self.external_ai_provider = conn["external_ai_provider"]
        self.deterministic_local_fallback_enabled = conn["deterministic_local_fallback_enabled"]
        self.migration_warnings = list(conn.get("migration_warnings", []))
        # the one hard line — always these values, never configurable
        for k, v in AMAZON_BOUNDARY.items():
            setattr(self, k, v)
        self.connectivity_policy_sha256 = self._compute_connectivity_sha256()

    # -- derived --------------------------------------------------------------
    @property
    def is_valid(self):
        return not self.validation_errors

    @property
    def loopback_only_effective(self):
        return self.loopback_only and is_loopback_host(self.bind_host)

    @property
    def dashboard_loopback_only(self):
        # the dashboard is loopback-only in EVERY mode, including CONNECTED_RESEARCH
        return self.loopback_only_effective

    def capability_enabled(self, capability):
        """True when a named connected-research capability is enabled in this mode.

        Prohibited (Amazon-account) capabilities are ALWAYS unavailable."""
        cap = str(capability or "").upper()
        if cap in PROHIBITED_CAPABILITIES:
            return False
        return {
            "LOOPBACK_ACCESS": True,
            "PUBLIC_WEB_RESEARCH": self.public_web_research_enabled,
            "PUBLIC_POLICY_RESEARCH": self.public_policy_research_enabled,
            "AMAZON_PUBLIC_DOCUMENTATION_READ": self.amazon_public_documentation_enabled,
            "THIRD_PARTY_DATA": self.third_party_data_enabled,
            "MARKET_DATA_CONNECTION": self.market_data_enabled,
            "SUPPLIER_CONNECTION": self.supplier_connections_enabled,
            "EXTERNAL_AI_CONNECTION": self.external_ai_allowed and self.external_ai_enabled,
            "TOOLKIT_UPDATE_DISCOVERY": self.toolkit_update_discovery_enabled,
        }.get(cap, False)

    def _canonical(self):
        """Deterministic, secret-free dict used for the LEGACY policy hash. Never
        changed by the connectivity layer, so every prior hash stays stable."""
        return {
            "offline_only": self.offline_only,
            "external_ai_enabled": self.external_ai_enabled,
            "outbound_network_enabled": self.outbound_network_enabled,
            "bind_host": self.bind_host,
            "bind_port": self.bind_port,
            "loopback_only": self.loopback_only,
        }

    def _connectivity_canonical(self):
        d = {"connectivity_mode": self.connectivity_mode,
             "external_ai_allowed": self.external_ai_allowed,
             "external_ai_enabled": self.external_ai_enabled,
             "external_ai_provider": self.external_ai_provider,
             "deterministic_local_fallback_enabled": self.deterministic_local_fallback_enabled}
        for field in _CAPABILITY_FIELDS:
            d[field] = getattr(self, field)
        d.update(AMAZON_BOUNDARY)
        return d

    def _compute_sha256(self):
        blob = json.dumps(self._canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _compute_connectivity_sha256(self):
        blob = json.dumps(self._connectivity_canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def connectivity_to_dict(self):
        """Secret-free connectivity view for /healthz, doctor, dashboard, CLI."""
        d = dict(self._connectivity_canonical())
        d.update({
            "connected_research": self.connected_research,
            "local_safe": self.local_safe,
            "test_deny_external": self.test_deny_external,
            "dashboard_loopback_only": self.dashboard_loopback_only,
            "migration_warnings": list(self.migration_warnings),
            "connectivity_policy_sha256": self.connectivity_policy_sha256,
        })
        return d

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
            "connectivity": self.connectivity_to_dict(),
        })
        return d

    def raise_if_invalid(self):
        if not self.is_valid:
            D.record_event(D.INVALID_RUNTIME_POLICY,
                           "runtime policy invalid: " + "; ".join(self.validation_errors))
            raise RuntimePolicyError("; ".join(self.validation_errors))
        return self

    def __repr__(self):
        return (f"RuntimePolicy(mode={self.connectivity_mode}, "
                f"offline_only={self.offline_only}, "
                f"external_ai={self.external_ai_enabled}, "
                f"bind={self.bind_host}:{self.bind_port}, valid={self.is_valid})")


def _normalize_mode(raw):
    """Return a canonical mode string, or None when *raw* is not a valid mode."""
    if raw is None:
        return None
    s = str(raw).strip().upper().replace("-", "_")
    return s if s in CONNECTIVITY_MODES else None


def _default_connectivity(offline_only):
    """Connectivity fields for a policy built without the 6A.1 resolver (legacy path)."""
    mode = LOCAL_SAFE if offline_only else CONNECTED_RESEARCH
    return _resolve_capabilities(mode, {}, external_ai_enabled=False,
                                 external_ai_provider=None, warnings=[])


def _resolve_capabilities(mode, overrides, *, external_ai_enabled, external_ai_provider,
                          warnings):
    """Compute the capability flags for *mode*.

    In CONNECTED_RESEARCH each capability starts at its new-install default and may be
    turned OFF by an explicit override. In LOCAL_SAFE / TEST_DENY_EXTERNAL every external
    capability is forced False (an attempt to enable one is refused with a warning);
    loopback and deterministic local fallback always remain available.
    """
    connected = mode == CONNECTED_RESEARCH
    conn = {"connectivity_mode": mode,
            "deterministic_local_fallback_enabled": True,
            "external_ai_provider": (str(external_ai_provider).strip()
                                     if external_ai_provider else None),
            "migration_warnings": list(warnings)}
    for field in _CAPABILITY_FIELDS:
        base = _CONNECTED_RESEARCH_DEFAULTS[field] if connected else False
        ov = overrides.get(field)
        if ov is None:
            value = base
        elif not connected and ov:
            value = False        # cannot enable an external capability outside CONNECTED_RESEARCH
            conn["migration_warnings"].append(
                f"{field} requested but forced OFF outside CONNECTED_RESEARCH")
        else:
            value = bool(ov)
        conn[field] = value
    conn["external_ai_allowed"] = connected
    conn["external_ai_enabled"] = bool(external_ai_enabled and connected)
    if not connected:
        conn["external_ai_provider"] = None
    return conn


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


def load_runtime_policy(env=None, config_path=None, connectivity_config=None):
    """Resolve the runtime policy from (defaults <- config file <- connectivity
    config <- environment).

    Always returns a RuntimePolicy. Configuration problems are captured in
    ``validation_errors`` (so /healthz can report 503) rather than raised — call
    ``raise_if_invalid()`` at startup when you want a hard failure. *env* defaults
    to ``os.environ``; pass an explicit dict to test deterministically.
    ``connectivity_config`` is an optional pre-read connectivity-policy.json dict
    (lowest precedence) so a caller can honor the persisted connectivity choice.
    """
    if env is None:
        env = os.environ
    warnings = []
    errors = []

    cfg, cfg_used = _read_config(config_path)
    conn_cfg = ({str(k).upper(): v for k, v in connectivity_config.items()}
                if isinstance(connectivity_config, dict) else {})

    def _raw(key):
        # precedence: environment > config file > connectivity config
        if key in env:
            return env[key]
        if key in cfg:
            return cfg[key]
        if key in conn_cfg:
            return conn_cfg[key]
        return None

    _all_keys = set(DEFAULTS) | {"CONNECTIVITY_MODE", "EXTERNAL_AI_PROVIDER"} | set(_CAPABILITY_ENV)
    env_used = any(k in env for k in _all_keys)

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

    # -- connectivity mode resolution (Session 6A.1) --------------------------
    migration_warnings = []
    raw_mode = _raw("CONNECTIVITY_MODE")
    mode = None
    if raw_mode is not None and str(raw_mode).strip():
        mode = _normalize_mode(raw_mode)
        if mode is None:
            errors.append(
                f"CONNECTIVITY_MODE: invalid mode {raw_mode!r} "
                f"(use {', '.join(CONNECTIVITY_MODES)})")
        else:
            # an explicit valid mode takes priority and reconciles the legacy flag
            values["OFFLINE_ONLY"] = (mode != CONNECTED_RESEARCH)
    if mode is None:
        # legacy mapping: OFFLINE_ONLY=true -> LOCAL_SAFE, false -> CONNECTED_RESEARCH
        mode = LOCAL_SAFE if values["OFFLINE_ONLY"] else CONNECTED_RESEARCH
        if raw_mode is None:
            migration_warnings.append(
                f"connectivity mode derived from OFFLINE_ONLY={values['OFFLINE_ONLY']} -> {mode}")

    # -- invariant 1: offline (non-CONNECTED) forces legacy AI + outbound OFF --
    if values["OFFLINE_ONLY"]:
        if values["EXTERNAL_AI_ENABLED"]:
            warnings.append("EXTERNAL_AI_ENABLED requested but forced OFF by OFFLINE_ONLY")
            values["EXTERNAL_AI_ENABLED"] = False
        if values["OUTBOUND_NETWORK_ENABLED"]:
            warnings.append("OUTBOUND_NETWORK_ENABLED requested but forced OFF by OFFLINE_ONLY")
            values["OUTBOUND_NETWORK_ENABLED"] = False

    # -- invariant 5/6: a credential alone must never enable external AI ------
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
        errors.append(f"BIND_HOST {host!r} is not loopback; external binding is not "
                      f"permitted in this loopback-only tool")

    # -- capability + external-AI resolution ----------------------------------
    overrides = {}
    for env_key, field in _CAPABILITY_ENV.items():
        overrides[field] = _coerce_opt_bool(_raw(env_key))
    provider_raw = _raw("EXTERNAL_AI_PROVIDER")
    provider = (str(provider_raw).strip() if provider_raw is not None
                and str(provider_raw).strip() and str(provider_raw).strip().lower() != "null"
                else None)
    connectivity = _resolve_capabilities(
        mode, overrides, external_ai_enabled=values["EXTERNAL_AI_ENABLED"],
        external_ai_provider=provider, warnings=migration_warnings)

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

    return RuntimePolicy(values, warnings, errors, source, credentials_present,
                         connectivity=connectivity)


def load_runtime_policy_strict(env=None, config_path=None, connectivity_config=None):
    """Load the policy and raise RuntimePolicyError if it is invalid (startup use)."""
    return load_runtime_policy(env=env, config_path=config_path,
                               connectivity_config=connectivity_config).raise_if_invalid()
