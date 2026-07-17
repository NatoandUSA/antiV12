# Connected Research — the permanent Amazon-account boundary

**Session 6A.1.** The toolkit is now **connected to the open world** and **isolated from your
Amazon account**. Connectivity improves *research*; it never lets the tool *act* on Amazon.
You remain the only manual bridge to Seller Central.

## The one hard line — NOT AVAILABLE, in every mode, forever

There is **no capability in the toolkit** to do any of the following, and none can be added
by a mode, flag, environment variable, config field, API key, plugin, or dashboard action:

| Capability | State |
|---|---|
| Log in to Amazon / Seller Central | **NOT AVAILABLE** |
| Use an Amazon seller session / authentication | **NOT AVAILABLE** |
| Store Amazon credentials / seller cookies | **NO CREDENTIAL STORE EXISTS** |
| Use a logged-in Amazon browser profile | **NOT AVAILABLE** |
| SP-API / MWS / Advertising API | **NOT AVAILABLE** |
| Access Seller Central account pages | **NOT AVAILABLE** |
| Pull seller-account reports automatically | **NOT AVAILABLE** |
| Create / edit / delete / publish listings | **NOT AVAILABLE** |
| Change prices / inventory / A+ / PPC | **NOT AVAILABLE** |
| Automate an Amazon browser session | **NOT AVAILABLE** |
| Manipulate reviews | **NOT AVAILABLE** |

These are encoded as immutable policy fields (`core/runtime_policy.py`,
`AMAZON_BOUNDARY`) that are the same value in `CONNECTED_RESEARCH`, `LOCAL_SAFE`, and
`TEST_DENY_EXTERNAL`:

```
amazon_account_isolation            = true   (PASS)
amazon_credential_store_available   = false
amazon_seller_central_enabled       = false
amazon_authenticated_access_enabled = false
amazon_api_enabled                  = false
amazon_browser_automation_enabled   = false
amazon_account_report_pull_enabled  = false
amazon_network_writes_enabled       = false
```

## How it is enforced

1. **No credential store.** There is no config field for an Amazon username, password,
   Seller Central cookie, refresh token, SP-API/MWS/Advertising key, or browser-profile
   path. `connectivity-policy.json` accepts only a whitelist of capability toggles; any
   Amazon/credential field in an inbound config is dropped.
2. **Prohibited capabilities.** Seventeen Amazon-account capability names
   (`AMAZON_LOGIN`, `AMAZON_SELLER_CENTRAL_ACCESS`, `AMAZON_SP_API`, `AMAZON_MWS`,
   `AMAZON_ADVERTISING_API`, `AMAZON_BROWSER_AUTOMATION`, `AMAZON_ACCOUNT_REPORT_PULL`,
   `AMAZON_LISTING_WRITE`, `AMAZON_PRICE_WRITE`, `AMAZON_INVENTORY_WRITE`,
   `AMAZON_APLUS_WRITE`, `AMAZON_PPC_WRITE`, `AMAZON_REVIEW_MANIPULATION`, …) always
   return a typed policy error and can never be enabled.
3. **Destination classification.** `core/network_policy.py` classifies every outbound
   destination. Seller Central, SP-API/MWS/Advertising, and Amazon authentication paths
   are always denied with a specific reason code. Ordinary Amazon product/search pages are
   classified `AMAZON_PRODUCT_OR_SEARCH` and denied with
   `AMAZON_BULK_OR_MARKETPLACE_SCRAPING_NOT_SUPPORTED` — the tool does not scrape Amazon.
4. **Approved client.** Every production internet call must pass through
   `core/approved_http_client.py`, which re-checks the policy (and reclassifies redirects),
   so a redirect can never smuggle a request to Seller Central.
5. **Fail closed.** When an Amazon destination cannot be verified, it is denied
   (`AMAZON_DESTINATION_UNVERIFIED` / `AMAZON_PUBLIC_DOCUMENTATION_UNVERIFIED`).

## What connectivity *does* allow (research only, advisory only)

Public web research, public policy research, third-party data, market data, supplier
services, external AI (optional, explicitly enabled, approved provider only), and toolkit
update *discovery*. Everything fetched is **advisory** and must be reviewed by you before it
changes any rule or configuration; it never becomes a verified product fact automatically,
and it never marks content publishable — product facts, claim evidence, and the PageAuditor
remain the authorities.

## Verify it yourself

```
amz-fbm connectivity amazon-boundary     # prints every permanent NOT AVAILABLE
amz-fbm connectivity verify              # checks isolation + no credential store
amz-fbm doctor                           # read-only; shows the boundary is all-blocked
python scripts/connectivity_scan.py      # scans runtime source; 0 active Amazon-account paths
```
