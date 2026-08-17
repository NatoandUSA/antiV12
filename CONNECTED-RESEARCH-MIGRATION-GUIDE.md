# Migration guide — from "offline-only" to Connected Research

**Session 6A.1.** This release replaces the old *offline-only* identity with the
owner-approved connectivity policy. Nothing about your Amazon-account safety changes: the
tool never operated inside your Amazon account before, and it never will. What changes is
the honest description and the ability to use approved open-web and research services.

## What changed

| Before (wording) | After (accurate) |
|---|---|
| "fully offline / never connects to the internet" | "never operates inside your Amazon account; free to use the open web and public research" |
| single `OFFLINE_ONLY` switch | three connectivity **modes** (below) + a capability model |
| all outbound blocked | approved research allowed via the approved client; Amazon-account access permanently blocked |

## Connectivity modes

- **CONNECTED_RESEARCH** *(default for new installs)* — approved public web / policy /
  third-party / market / supplier research, optional external AI, and update discovery.
  Deterministic local engines still run. Amazon-account access remains permanently absent.
- **LOCAL_SAFE** — loopback only; all external destinations denied. Useful for private work,
  troubleshooting, or a disconnected machine. Deterministic local engines still run.
- **TEST_DENY_EXTERNAL** — every external connection denied; used for tests and
  certification. Not the normal owner mode.

## Legacy mapping (automatic, safe)

| Old setting | New mode |
|---|---|
| `OFFLINE_ONLY=true` | `LOCAL_SAFE` |
| `OFFLINE_ONLY=false` (no explicit mode) | `CONNECTED_RESEARCH` |
| explicit `CONNECTIVITY_MODE=…` | that mode (takes priority) |

A bare environment with nothing configured stays **LOCAL_SAFE** (safe by default). A real
installation writes `connectivity-policy.json` with `CONNECTED_RESEARCH`. Your previous
configuration is **backed up** before any migration; an invalid mode value fails clearly.

## How to switch

```
amz-fbm connectivity status                      # see the current mode + capabilities
amz-fbm connectivity mode connected-research     # open web + approved research
amz-fbm connectivity mode local-safe             # loopback only
amz-fbm connectivity capabilities                # enabled + permanently-blocked capabilities
amz-fbm connectivity amazon-boundary             # the permanent Amazon boundary
amz-fbm connectivity verify                       # verify isolation + no credential store
```

Changing the mode never alters an Amazon hard-boundary value.

## Configuration

`connectivity-policy.json` (in your per-user config directory) holds **only** the mode and
capability toggles — never a secret, API key, password, cookie, token, or any Amazon
seller/credential field. Example:

```json
{
  "schema_version": "connectivity-policy-v1",
  "connectivity_mode": "CONNECTED_RESEARCH",
  "public_web_research_enabled": true,
  "public_policy_research_enabled": true,
  "amazon_public_documentation_enabled": false,
  "third_party_data_enabled": true,
  "market_data_enabled": true,
  "supplier_connections_enabled": true,
  "external_ai_allowed": true,
  "external_ai_enabled": false,
  "external_ai_provider": null,
  "toolkit_update_discovery_enabled": true,
  "deterministic_local_fallback_enabled": true
}
```

## What is *not* built yet (future, owner-approved)

Session 6A.1 ships the **policy boundary and capability model** only. There is no live
research adapter, no external-AI generation workflow, no update installer, and no
public-Amazon-documentation reader yet. Those are future work, each gated behind explicit
owner approval, and each must obey the contracts in `core/amazon_docs_contract.py`,
`core/update_discovery.py`, and `core/provenance.py`.
