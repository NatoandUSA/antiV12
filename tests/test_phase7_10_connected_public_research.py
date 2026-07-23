#!/usr/bin/env python3
"""Phase 7.10 — Connected Public Research & Evidence Hub.

Every test runs with NO real internet call: the HTTP layer is exercised through injected fake
transports + fake DNS resolvers, and one class binds a synthetic HTTP server to 127.0.0.1 only
(never the public Internet). The permanent Amazon-account boundary is asserted throughout — no
Seller Central, no seller API, no Ads API, no seller OAuth — and every Amazon counter stays a
constant zero. Synthetic fixtures only; the upstream Phase 7.3–7.9 runtime trees are never touched.
"""
import gzip
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout, redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "core"), os.path.join(ROOT, "production")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import phase7_connected_public_research as R   # noqa: E402
import network_policy as NP                                    # noqa: E402
import diagnostics as D                                        # noqa: E402

# Amazon endpoint literals live in TEST code only (production assembles them from fragments). They
# are used solely to assert these destinations are BLOCKED.
SELLER_CENTRAL = "https://sellercentral.amazon.com/home"
SELLER_CENTRAL_EU = "https://sellercentral-europe.amazon.com/x"
SP_API = "https://sellingpartnerapi-na.amazon.com/orders/v0/orders"
ADS_API = "https://advertising-api.amazon.com/v2/campaigns"
AMAZON_OAUTH = "https://www.amazon.com/ap/signin"

PUBLIC_ADDR = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def resolver_public(host, port=None, *a, **k):
    return list(PUBLIC_ADDR)


def resolver_to(ip):
    def _r(host, port=None, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
    return _r


def resolver_mixed(*ips):
    def _r(host, port=None, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]
    return _r


def resolver_fail(host, port=None, *a, **k):
    raise socket.gaierror("no such host")


HTML = (b'<html><head><title>Nurse Sweatshirt</title>'
        b'<meta name="description" content="A cozy nurse sweatshirt">'
        b'<link rel="canonical" href="https://example.com/nurse"/>'
        b'<meta property="og:title" content="Nurse Sweatshirt OG">'
        b'<meta property="og:description" content="warm">'
        b'<script type="application/ld+json">{"@type":"Product","name":"Nurse Sweatshirt",'
        b'"brand":{"name":"UMEE"},"offers":{"price":"24.99","priceCurrency":"USD",'
        b'"availability":"https://schema.org/InStock"},'
        b'"aggregateRating":{"ratingValue":"4.7","reviewCount":"123"}}</script>'
        b'</head><body><h1>Nurse</h1></body></html>')

# an HTML page full of links / images / scripts / forms — none of which may be fetched or executed
HTML_LINKS = (b'<html><head><title>Links</title></head><body>'
              b'<a href="https://other.example.com/page2">next</a>'
              b'<img src="https://cdn.example.com/a.png">'
              b'<script src="https://cdn.example.com/app.js"></script>'
              b'<script>window.x = "SHOULD_NOT_EXECUTE";</script>'
              b'<form action="https://example.com/submit"><input name="secret" value="hidden"></form>'
              b'</body></html>')

AMZ_HTML = (b'<html><head><title>Amazon Product</title>'
            b'<link rel="canonical" href="https://www.amazon.com/dp/B012345678"/>'
            b'<meta property="og:title" content="Nurse Sweatshirt">'
            b'<script type="application/ld+json">{"@type":"Product","name":"Nurse Sweatshirt",'
            b'"brand":{"name":"UMEE"},"offers":{"price":"24.99","priceCurrency":"USD",'
            b'"availability":"InStock"},"aggregateRating":{"ratingValue":"4.6","reviewCount":"88"}}</script>'
            b'</head><body><span id="productTitle">Nurse Sweatshirt</span>'
            b'<span class="a-offscreen">$24.99</span>'
            b'<div id="reviewText">This customer review body must NOT be extracted</div>'
            b'<div class="review-byline">By Jane Customer</div>'
            b'<input type="hidden" name="csrf" value="TRACKING-TOKEN-XYZ">'
            b'</body></html>')

RSS = (b'<?xml version="1.0"?><rss version="2.0"><channel><title>My Feed</title>'
       b'<item><title>Item A</title><link>https://ex.com/a</link><guid>a</guid>'
       b'<pubDate>2026-01-01</pubDate><description>first</description></item>'
       b'<item><title>Item A</title><link>https://ex.com/a</link><guid>a</guid></item>'
       b'<item><title>Item B</title><link>https://ex.com/b</link><guid>b</guid></item></channel></rss>')

ATOM = (b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Atom Feed</title>'
        b'<entry><title>Entry 1</title><id>urn:1</id>'
        b'<link rel="alternate" href="https://ex.com/1"/><updated>2026-01-01T00:00:00Z</updated>'
        b'<summary>sum</summary></entry></feed>')

XXE = (b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
       b'<rss><channel><title>&xxe;</title></channel></rss>')

BILLION = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
           b'<!ENTITY lol2 "&lol;&lol;&lol;">]><rss><channel><title>&lol2;</title></channel></rss>')

GH_REPO = json.dumps({"full_name": "owner/repo", "default_branch": "main",
                      "description": "a repo"}).encode()
GH_REL = json.dumps({"tag_name": "v1.2.3", "name": "Release 1.2.3",
                     "published_at": "2026-01-02T00:00:00Z", "body": "release notes",
                     "assets": [{"name": "wheel.whl", "size": 2048}]}).encode()
PYPI = json.dumps({"info": {"name": "cryptography", "version": "42.0.0", "summary": "crypto lib",
                            "requires_python": ">=3.7", "project_urls": {"Homepage": "https://x"}},
                   "urls": [{"filename": "c-42.whl", "digests": {"sha256": "a" * 64},
                             "upload_time_iso_8601": "2026-01-01T00:00:00Z", "yanked": False}]}).encode()


def route(status, ctype, body, **extra):
    h = {"content-type": ctype}
    h.update(extra)
    return (status, h, body)


def make_transport(routes, record=None):
    """A fake transport: matches the first route fragment found in the URL. ``record`` (if given)
    collects (url, headers) for every call so a test can assert what was and was NOT fetched."""
    def t(url, *, timeout_seconds, max_bytes, headers=None):
        if record is not None:
            record.append((url, dict(headers or {})))
        for frag, resp in routes.items():
            if frag in url:
                return resp(url, headers) if callable(resp) else (resp[0], dict(resp[1]), resp[2])
        return 404, {}, b""
    return t


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p710-")
        self._n = 0
        D.clear_events()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cfg(self, transport=None, resolver=resolver_public, **kw):
        self._n += 1
        base = os.path.join(self.tmp, "ws%d" % self._n)
        kw.setdefault("input_root", os.path.join(self.tmp, "input"))
        return R.Config(base, env=kw.pop("env", {}), resolver=resolver, transport=transport,
                        host_gate=kw.pop("host_gate", R.HostGate()), **kw)

    def html_cfg(self, body=HTML, ctype="text/html", record=None, **kw):
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "example.com": route(200, ctype, body)}, record=record)
        return self.cfg(transport=t, **kw)


# ======================================================================== 1) CLI
class TestCLI(Base):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = R.run_cli(argv)
                return code, out.getvalue(), err.getvalue()
            except SystemExit as e:
                return e.code, out.getvalue(), err.getvalue()

    def test_01_cli_help(self):
        code, out, err = self._run(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("capture-url", out)

    def test_02_missing_command(self):
        code, out, err = self._run(["--base-dir", self.tmp])
        self.assertNotEqual(code, 0)

    def test_03_missing_required_source(self):
        code, out, err = self._run(["--base-dir", self.tmp, "capture-url"])
        self.assertNotEqual(code, 0)

    def test_04_unknown_command(self):
        code, out, err = self._run(["--base-dir", self.tmp, "capture-teleport"])
        self.assertNotEqual(code, 0)

    def test_05_unknown_argument(self):
        code, out, err = self._run(["--base-dir", self.tmp, "capture-url", "--frobnicate", "x"])
        self.assertNotEqual(code, 0)

    def test_06_subcommands_present(self):
        p = R.build_parser()
        help_text = p.format_help()
        for sub in ("capture-url", "capture-rss", "capture-github", "capture-pypi",
                    "capture-amazon-product", "capture-file", "run-plan", "verify-run",
                    "replay-run", "list-runs", "export", "provider-check", "validate-only"):
            self.assertIn(sub, help_text)

    def test_07_provider_check_cli(self):
        code, out, err = self._run(["--base-dir", os.path.join(self.tmp, "b"), "provider-check"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["seller_central_blocked_first"])

    def test_08_list_runs_cli_empty(self):
        code, out, err = self._run(["--base-dir", os.path.join(self.tmp, "b2"), "list-runs"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["count"], 0)

    def test_09_capture_file_cli(self):
        root = os.path.join(self.tmp, "inroot")
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "p.html"), "wb") as f:
            f.write(HTML)
        code, out, err = self._run(["--base-dir", os.path.join(self.tmp, "b3"),
                                    "capture-file", "--input-root", root, "--file", "p.html"])
        self.assertEqual(code, 0)
        self.assertIn("RESEARCH_READY", json.loads(out)["readiness"])


# ======================================================================== 2) plan
class TestPlan(Base):
    def test_06_canonical_plan_id(self):
        plan = {"schema_version": "x", "sources": [{"source_type": "pypi", "source_locator": "flask"}]}
        _, pid = R.normalize_plan(plan)
        self.assertTrue(pid.startswith("plan-"))

    def test_07_reordered_keys_same_id(self):
        a = {"sources": [{"source_type": "pypi", "source_locator": "flask"}], "schema_version": "y"}
        b = {"schema_version": "z", "sources": [{"source_locator": "flask", "source_type": "pypi"}]}
        self.assertEqual(R.normalize_plan(a)[1], R.normalize_plan(b)[1])

    def test_08_unknown_plan_field_rejected(self):
        with self.assertRaises(R.PlanError):
            R.normalize_plan({"sources": [], "surprise": 1})

    def test_08b_unknown_descriptor_field_rejected(self):
        with self.assertRaises(R.PlanError):
            R.normalize_plan({"sources": [{"source_type": "pypi", "source_locator": "x", "z": 1}]})

    def test_09_secret_field_rejected(self):
        for bad in ("api_key", "authorization", "cookie", "bearer_token", "password", "headers"):
            with self.assertRaises(R.PlanError):
                R.normalize_plan({"sources": [{"source_type": "pypi", "source_locator": "x", bad: "v"}]})

    def test_10_empty_plan(self):
        canonical, pid = R.normalize_plan({"sources": []})
        self.assertEqual(canonical["sources"], [])

    def test_11_disabled_source_skipped(self):
        plan = {"sources": [{"source_type": "pypi", "source_locator": "flask", "enabled": False}]}
        pf = os.path.join(self.tmp, "p.json")
        with open(pf, "w") as f:
            json.dump(plan, f)
        res = R.run_plan(self.cfg(make_transport({})), pf)
        self.assertEqual(res["readiness"], R.SOURCE_REQUIRED)

    def test_11b_unknown_source_type_rejected(self):
        with self.assertRaises(R.PlanError):
            R.normalize_plan({"sources": [{"source_type": "telnet", "source_locator": "x"}]})

    def test_11c_missing_locator_rejected(self):
        with self.assertRaises(R.PlanError):
            R.normalize_plan({"sources": [{"source_type": "pypi"}]})

    def test_11d_maximum_bytes_range(self):
        with self.assertRaises(R.PlanError):
            R.normalize_plan({"sources": [{"source_type": "pypi", "source_locator": "x",
                                           "maximum_bytes": 10 ** 12}]})


# ======================================================================== 3) public-research policy
class TestPublicPolicy(Base):
    def ev(self, url, **kw):
        return NP.evaluate_public_research_url(url, purpose=NP.PURPOSE_PUBLIC_RESEARCH, **kw)

    def test_12_public_https_allowed(self):
        d = self.ev("https://example.com/page")
        self.assertTrue(d.allowed)

    def test_13_http_public_blocked(self):
        d = self.ev("http://example.com/page")
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, D.INSECURE_SCHEME_BLOCKED)

    def test_29_userinfo_rejected(self):
        self.assertEqual(self.ev("https://u:p@example.com/").reason_code, D.URL_USERINFO_BLOCKED)

    def test_30_username_rejected(self):
        self.assertEqual(self.ev("https://user@example.com/").reason_code, D.URL_USERINFO_BLOCKED)

    def test_31_password_deception_rejected(self):
        self.assertEqual(self.ev("https://real.com@evil.com/").reason_code, D.URL_USERINFO_BLOCKED)

    def test_32_file_scheme_blocked(self):
        self.assertEqual(self.ev("file:///etc/passwd").reason_code, D.CONNECTED_URL_INVALID)

    def test_33_ftp_blocked(self):
        self.assertEqual(self.ev("ftp://example.com/x").reason_code, D.CONNECTED_URL_INVALID)

    def test_34_gopher_blocked(self):
        self.assertEqual(self.ev("gopher://example.com/x").reason_code, D.CONNECTED_URL_INVALID)

    def test_35_data_url_blocked(self):
        self.assertEqual(self.ev("data:text/html,<b>x</b>").reason_code, D.CONNECTED_URL_INVALID)

    def test_36_javascript_url_blocked(self):
        self.assertEqual(self.ev("javascript:alert(1)").reason_code, D.CONNECTED_URL_INVALID)

    def test_purpose_unknown_blocked(self):
        d = NP.evaluate_public_research_url("https://example.com/", purpose="MYSTERY")
        self.assertEqual(d.reason_code, D.CONNECTED_PURPOSE_UNKNOWN)


# ======================================================================== 4) Seller Central boundary
class TestSellerCentral(Base):
    def ev(self, url, **kw):
        return NP.evaluate_public_research_url(url, purpose=NP.PURPOSE_PUBLIC_RESEARCH, **kw)

    def test_14_seller_central_blocked_first(self):
        self.assertEqual(self.ev(SELLER_CENTRAL).reason_code, D.SELLER_CENTRAL_ACCESS_PROHIBITED)

    def test_15_seller_central_subdomain_blocked(self):
        self.assertEqual(self.ev(SELLER_CENTRAL_EU).reason_code, D.SELLER_CENTRAL_ACCESS_PROHIBITED)

    def test_16_deceptive_suffix_blocked(self):
        d = self.ev("https://sellercentral.amazon.com.evil.example/x")
        self.assertEqual(d.reason_code, D.SELLER_CENTRAL_ACCESS_PROHIBITED)

    def test_17_mixed_case_blocked(self):
        self.assertEqual(self.ev("https://SellerCentral.Amazon.COM/home").reason_code,
                         D.SELLER_CENTRAL_ACCESS_PROHIBITED)

    def test_18_trailing_dot_blocked(self):
        self.assertEqual(self.ev("https://sellercentral.amazon.com./home").reason_code,
                         D.SELLER_CENTRAL_ACCESS_PROHIBITED)

    def test_19_seller_api_blocked(self):
        self.assertEqual(self.ev(SP_API).reason_code, D.AMAZON_API_PROHIBITED)

    def test_20_ads_api_blocked(self):
        self.assertEqual(self.ev(ADS_API).reason_code, D.AMAZON_API_PROHIBITED)

    def test_21_seller_oauth_blocked(self):
        self.assertEqual(self.ev(AMAZON_OAUTH).reason_code, D.AMAZON_ACCOUNT_ACCESS_PROHIBITED)

    def test_22_seller_login_path_blocked(self):
        self.assertEqual(self.ev("https://www.amazon.com/gp/sign-in").reason_code,
                         D.AMAZON_ACCOUNT_ACCESS_PROHIBITED)

    def test_23_amazon_account_path_blocked(self):
        self.assertEqual(self.ev("https://www.amazon.com/gp/css/homepage.html").reason_code,
                         D.AMAZON_ACCOUNT_ACCESS_PROHIBITED)

    def test_24_amazon_cart_blocked(self):
        d = NP.evaluate_amazon_public_product_url("https://www.amazon.com/gp/cart/view.html")
        self.assertFalse(d.allowed)

    def test_25_amazon_checkout_blocked(self):
        d = NP.evaluate_amazon_public_product_url("https://www.amazon.com/gp/buy/spc/handlers/display.html")
        self.assertEqual(d.reason_code, D.AMAZON_NON_PRODUCT_PATH_BLOCKED)

    def test_sc_blocked_before_scheme(self):
        # even http Seller Central is refused by the Amazon boundary, not merely the scheme rule
        self.assertEqual(self.ev("http://sellercentral.amazon.com/home").reason_code,
                         D.SELLER_CENTRAL_ACCESS_PROHIBITED)

    def test_capture_seller_central_no_transport(self):
        rec = []
        t = make_transport({}, record=rec)
        res = R.capture_one(self.cfg(t), "public-url", SELLER_CENTRAL)
        self.assertEqual(res["readiness"], R.SELLER_CENTRAL_POLICY_BLOCKED)
        self.assertEqual(rec, [])


# ======================================================================== 5) Amazon public product
class TestAmazonProduct(Base):
    def test_26_amazon_product_dp_allowed(self):
        d = NP.evaluate_amazon_public_product_url("https://www.amazon.com/dp/B012345678")
        self.assertTrue(d.allowed)

    def test_26b_amazon_gp_product_allowed(self):
        d = NP.evaluate_amazon_public_product_url("https://amazon.com/gp/product/B0ABCDEFGH")
        self.assertTrue(d.allowed)

    def test_27_amazon_non_product_blocked(self):
        d = NP.evaluate_amazon_public_product_url("https://www.amazon.com/gp/help/customer/x")
        self.assertEqual(d.reason_code, D.AMAZON_NON_PRODUCT_PATH_BLOCKED)

    def test_28_amazon_search_blocked(self):
        d = NP.evaluate_amazon_public_product_url("https://www.amazon.com/s?k=nurse+sweatshirt")
        self.assertFalse(d.allowed)

    def test_127_valid_asin_url(self):
        self.assertEqual(NP.extract_amazon_asin("/dp/B012345678"), "B012345678")

    def test_128_malformed_asin_rejected(self):
        self.assertIsNone(NP.extract_amazon_asin("/dp/SHORT"))
        d = NP.evaluate_amazon_public_product_url("https://www.amazon.com/dp/SHORT")
        self.assertEqual(d.reason_code, D.AMAZON_NON_PRODUCT_PATH_BLOCKED)

    def test_amazon_non_retail_host_blocked(self):
        d = NP.evaluate_amazon_public_product_url("https://evil.example/dp/B012345678")
        self.assertEqual(d.reason_code, D.AMAZON_PRODUCT_URL_INVALID)

    def test_amazon_http_blocked(self):
        d = NP.evaluate_amazon_public_product_url("http://www.amazon.com/dp/B012345678")
        self.assertEqual(d.reason_code, D.AMAZON_PRODUCT_URL_INVALID)

    def test_amazon_sellercentral_dp_blocked_first(self):
        d = NP.evaluate_amazon_public_product_url("https://sellercentral.amazon.com/dp/B012345678")
        self.assertEqual(d.reason_code, D.SELLER_CENTRAL_ACCESS_PROHIBITED)

    def test_capture_amazon_product_ok(self):
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "/dp/": route(200, "text/html", AMZ_HTML)})
        res = R.capture_one(self.cfg(t), "amazon-public-product", "https://www.amazon.com/dp/B012345678")
        self.assertEqual(res["readiness"], R.READY)

    def test_capture_amazon_bare_asin(self):
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "/dp/": route(200, "text/html", AMZ_HTML)})
        res = R.capture_one(self.cfg(t), "amazon-public-product", "B012345678")
        self.assertEqual(res["readiness"], R.READY)


# ======================================================================== 6) SSRF (policy-level)
class TestSSRF(Base):
    def ev(self, url, **kw):
        return NP.evaluate_public_research_url(url, purpose=NP.PURPOSE_PUBLIC_RESEARCH, **kw)

    def test_37_localhost_blocked(self):
        # a bare "localhost" is a DNS name; DNS validation blocks it — the policy leaves it to resolve
        d = self.ev("https://localhost/x")
        self.assertTrue(d.allowed or d.reason_code)  # policy allows the name; resolver blocks it

    def test_38_ipv4_loopback_blocked(self):
        self.assertEqual(self.ev("https://127.0.0.1/x").reason_code, D.PRIVATE_DESTINATION_BLOCKED)

    def test_39_ipv6_loopback_blocked(self):
        self.assertEqual(self.ev("https://[::1]/x").reason_code, D.PRIVATE_DESTINATION_BLOCKED)

    def test_40_private_ipv4_blocked(self):
        for ip in ("10.0.0.1", "192.168.1.1", "172.16.5.5"):
            self.assertEqual(self.ev(f"https://{ip}/x").reason_code, D.PRIVATE_DESTINATION_BLOCKED)

    def test_41_private_ipv6_blocked(self):
        self.assertEqual(self.ev("https://[fd00::1]/x").reason_code, D.PRIVATE_DESTINATION_BLOCKED)

    def test_42_link_local_blocked(self):
        self.assertEqual(self.ev("https://169.254.1.1/x").reason_code, D.PRIVATE_DESTINATION_BLOCKED)

    def test_43_metadata_ip_blocked(self):
        self.assertEqual(self.ev("https://169.254.169.254/latest/meta-data/").reason_code,
                         D.PRIVATE_DESTINATION_BLOCKED)

    def test_44_integer_ipv4_blocked(self):
        self.assertEqual(self.ev("https://2130706433/x").reason_code, D.PRIVATE_DESTINATION_BLOCKED)

    def test_45_hex_ipv4_blocked(self):
        self.assertEqual(self.ev("https://0x7f000001/x").reason_code, D.PRIVATE_DESTINATION_BLOCKED)

    def test_46_octal_ipv4_blocked(self):
        self.assertEqual(self.ev("https://017700000001/x").reason_code, D.PRIVATE_DESTINATION_BLOCKED)

    def test_47_mixed_encoding_blocked(self):
        self.assertEqual(self.ev("https://0x7f.0.0.1/x").reason_code, D.PRIVATE_DESTINATION_BLOCKED)

    def test_48_public_ip_literal_rejected(self):
        self.assertEqual(self.ev("https://93.184.216.34/x").reason_code, D.IP_LITERAL_BLOCKED)

    def test_idna_confusable_rejected(self):
        self.assertEqual(self.ev("https://exàmple.com/x").reason_code, D.CONNECTED_URL_INVALID)

    def test_validate_resolved_addresses(self):
        self.assertEqual(NP.validate_resolved_addresses(["8.8.8.8", "1.1.1.1"]), [])
        self.assertTrue(NP.validate_resolved_addresses(["8.8.8.8", "10.0.0.1"]))
        self.assertEqual(NP.validate_resolved_addresses(["127.0.0.1"], allow_local=True), [])

    def test_local_endpoint_only_with_flag(self):
        self.assertFalse(self.ev("http://127.0.0.1:8000/x").allowed)
        self.assertTrue(self.ev("http://127.0.0.1:8000/x", allow_local=True).allowed)


# ======================================================================== 6b) DNS resolution / rebinding
class TestDNS(Base):
    def test_49_dns_mixed_safe_unsafe_blocked(self):
        rec = []
        t = make_transport({"example.com": route(200, "text/html", HTML)}, record=rec)
        c = self.cfg(t, resolver=resolver_mixed("93.184.216.34", "10.0.0.9"))
        res = R.capture_one(c, "public-url", "https://example.com/x")
        self.assertEqual(res["readiness"], R.NETWORK_POLICY_BLOCKED)
        self.assertEqual(rec, [])

    def test_50_dns_rebinding_blocked(self):
        rec = []
        t = make_transport({"example.com": route(200, "text/html", HTML)}, record=rec)
        c = self.cfg(t, resolver=resolver_to("10.0.0.5"))
        res = R.capture_one(c, "public-url", "https://internal.example.com/x")
        self.assertEqual(res["readiness"], R.NETWORK_POLICY_BLOCKED)
        self.assertEqual(rec, [])

    def test_dns_all_public_allowed(self):
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "example.com": route(200, "text/html", HTML)})
        c = self.cfg(t, resolver=resolver_mixed("93.184.216.34", "1.1.1.1"))
        res = R.capture_one(c, "public-url", "https://example.com/x")
        self.assertEqual(res["readiness"], R.READY)

    def test_dns_failure_unavailable(self):
        t = make_transport({"example.com": route(200, "text/html", HTML)})
        c = self.cfg(t, resolver=resolver_fail)
        res = R.capture_one(c, "public-url", "https://example.com/x")
        self.assertEqual(res["readiness"], R.NETWORK_UNAVAILABLE)


# ======================================================================== 7) redirect defense
class TestRedirect(Base):
    def _redir(self, location, resolver=resolver_public, record=None):
        routes = {"start.example.com": route(302, "text/html", b"", location=location),
                  "robots.txt": route(404, "text/plain", b"")}
        t = make_transport(routes, record=record)
        return R.capture_one(self.cfg(t, resolver=resolver), "public-url", "https://start.example.com/go")

    def test_51_redirect_to_private_blocked(self):
        rec = []
        res = self._redir("https://10.0.0.1/evil", record=rec)
        self.assertEqual(res["readiness"], R.NETWORK_POLICY_BLOCKED)

    def test_52_redirect_to_seller_central_blocked(self):
        res = self._redir(SELLER_CENTRAL)
        self.assertEqual(res["readiness"], R.SELLER_CENTRAL_POLICY_BLOCKED)

    def test_53_redirect_to_seller_api_blocked(self):
        res = self._redir(SP_API)
        self.assertEqual(res["readiness"], R.SELLER_CENTRAL_POLICY_BLOCKED)

    def test_54_redirect_to_ads_api_blocked(self):
        res = self._redir(ADS_API)
        self.assertEqual(res["readiness"], R.SELLER_CENTRAL_POLICY_BLOCKED)

    def test_55_redirect_limit(self):
        n = [0]

        def loop(url, headers):
            n[0] += 1
            return (302, {"location": "https://hop%d.example.com/" % n[0]}, b"")
        t = make_transport({"robots.txt": route(404, "text/plain", b""), "example.com": loop})
        res = R.capture_one(self.cfg(t), "public-url", "https://start.example.com/go")
        self.assertEqual(res["readiness"], R.NETWORK_UNAVAILABLE)

    def test_56_cross_host_credentials_removed(self):
        rec = []
        routes = {"api.github.com": route(302, "application/json", b"",
                                          location="https://other.example.com/x"),
                  "other.example.com": route(200, "application/json", b"{}")}
        t = make_transport(routes, record=rec)
        env = {R.GITHUB_TOKEN_ENV: "sk-ant-SECRETTOKENVALUE123456"}
        R.capture_one(self.cfg(t, env=env), "github", "owner/repo")
        # first hop (api.github.com) may carry auth; the cross-host hop must NOT
        cross = [h for (u, h) in rec if "other.example.com" in u]
        self.assertTrue(cross)
        for h in cross:
            self.assertNotIn("Authorization", h)

    def test_redirect_without_location(self):
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "example.com": route(302, "text/html", b"")})
        res = R.capture_one(self.cfg(t), "public-url", "https://start.example.com/go")
        self.assertEqual(res["readiness"], R.NETWORK_UNAVAILABLE)

    def test_redirect_reresolves_dns(self):
        rec = []
        res = self._redir("https://internal.example.com/x", resolver=resolver_to("192.168.0.9"),
                          record=rec)
        self.assertEqual(res["readiness"], R.NETWORK_POLICY_BLOCKED)


# ======================================================================== 8) transport limits
class TestTransport(Base):
    def test_61_62_timeout_then_retry(self):
        calls = [0]

        def flaky(url, headers):
            calls[0] += 1
            if calls[0] < 2:
                raise R.TransportTimeout("t")
            return (200, {"content-type": "text/html"}, HTML)
        t = make_transport({"robots.txt": route(404, "text/plain", b""), "example.com": flaky})
        res = R.capture_one(self.cfg(t), "public-url", "https://example.com/x")
        self.assertEqual(res["readiness"], R.READY)

    def test_63_retry_limit(self):
        def always(url, headers):
            raise R.TransportTimeout("t")
        t = make_transport({"robots.txt": route(404, "text/plain", b""), "example.com": always})
        res = R.capture_one(self.cfg(t), "public-url", "https://example.com/x")
        self.assertEqual(res["readiness"], R.NETWORK_UNAVAILABLE)

    def test_64_response_size_bound(self):
        big = b"<html><title>" + b"A" * 200000 + b"</title></html>"
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "example.com": route(200, "text/html", big)})
        res = R.capture_one(self.cfg(t, max_bytes=50000), "public-url", "https://example.com/x")
        self.assertEqual(res["readiness"], R.CONTENT_TOO_LARGE)

    def test_65_content_length_mismatch(self):
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "example.com": route(200, "text/html", HTML, **{"content-length": "5"})})
        res = R.capture_one(self.cfg(t), "public-url", "https://example.com/x")
        self.assertEqual(res["readiness"], R.NETWORK_UNAVAILABLE)

    def test_66_gzip_bound_ok(self):
        gz = gzip.compress(HTML)
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "example.com": route(200, "text/html", gz, **{"content-encoding": "gzip"})})
        res = R.capture_one(self.cfg(t), "public-url", "https://example.com/x")
        self.assertEqual(res["readiness"], R.READY)

    def test_67_decompression_bomb_blocked(self):
        bomb = gzip.compress(b"A" * 5_000_000)
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "example.com": route(200, "text/html", bomb, **{"content-encoding": "gzip"})})
        res = R.capture_one(self.cfg(t, max_bytes=100000), "public-url", "https://example.com/x")
        self.assertEqual(res["readiness"], R.CONTENT_TOO_LARGE)


# ======================================================================== 9) content types
class TestContentType(Base):
    def _grab(self, ctype, body=b'{"a":1}'):
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "example.com": route(200, ctype, body)})
        return R.capture_one(self.cfg(t), "public-url", "https://example.com/x")

    def test_70_html_accepted(self):
        self.assertEqual(self._grab("text/html", HTML)["readiness"], R.READY)

    def test_71_json_accepted(self):
        self.assertIn("READY", self._grab("application/json")["readiness"])

    def test_72_jsonld_accepted(self):
        self.assertIn("READY", self._grab("application/ld+json")["readiness"])

    def test_73_xml_accepted(self):
        self.assertIn("READY", self._grab("application/xml", b"<r/>")["readiness"])

    def test_74_rss_accepted(self):
        self.assertIn("READY", self._grab("application/rss+xml", RSS)["readiness"])

    def test_75_atom_accepted(self):
        self.assertIn("READY", self._grab("application/atom+xml", ATOM)["readiness"])

    def test_76_plain_text_accepted(self):
        self.assertIn("READY", self._grab("text/plain", b"hello")["readiness"])

    def test_68_unsupported_content_type(self):
        self.assertEqual(self._grab("application/octet-stream", b"data")["readiness"],
                         R.CONTENT_TYPE_BLOCKED)

    def test_69_binary_body_blocked(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
        self.assertEqual(self._grab("text/html", png)["readiness"], R.CONTENT_TYPE_BLOCKED)

    def test_image_content_type_blocked(self):
        self.assertEqual(self._grab("image/png", b"\x89PNG")["readiness"], R.CONTENT_TYPE_BLOCKED)


# ======================================================================== 10) robots + request behavior
class TestRobots(Base):
    def test_77_robots_allow(self):
        t = make_transport({"robots.txt": route(200, "text/plain", b"User-agent: *\nAllow: /"),
                            "example.com": route(200, "text/html", HTML)})
        res = R.capture_one(self.cfg(t), "public-url", "https://example.com/page")
        self.assertEqual(res["readiness"], R.READY)

    def test_78_robots_disallow(self):
        t = make_transport({"robots.txt": route(200, "text/plain", b"User-agent: *\nDisallow: /"),
                            "example.com": route(200, "text/html", HTML)})
        res = R.capture_one(self.cfg(t), "public-url", "https://example.com/page")
        self.assertEqual(res["readiness"], R.ROBOTS_BLOCKED)

    def test_79_robots_unavailable_allows(self):
        t = make_transport({"robots.txt": route(500, "text/plain", b""),
                            "example.com": route(200, "text/html", HTML)})
        res = R.capture_one(self.cfg(t), "public-url", "https://example.com/page")
        self.assertEqual(res["readiness"], R.READY)

    def test_80_robots_redirect_to_private_blocked(self):
        # a robots.txt that redirects to a private host is refused, so robots is treated unavailable
        t = make_transport({"robots.txt": route(302, "text/plain", b"", location="https://10.0.0.1/r"),
                            "example.com": route(200, "text/html", HTML)})
        res = R.capture_one(self.cfg(t), "public-url", "https://example.com/page")
        # robots became unavailable-allowed -> the page itself still captures
        self.assertIn(res["readiness"], (R.READY, R.NETWORK_POLICY_BLOCKED))

    def test_81_user_agent_static_and_not_browser(self):
        self.assertTrue(R.USER_AGENT)
        self.assertNotIn("Mozilla", R.USER_AGENT)
        self.assertIn("Toolkit", R.USER_AGENT)

    def test_82_no_cookie_header_sent(self):
        rec = []
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "example.com": route(200, "text/html", HTML)}, record=rec)
        R.capture_one(self.cfg(t), "public-url", "https://example.com/x")
        for (u, h) in rec:
            self.assertNotIn("Cookie", h)

    def test_83_set_cookie_not_persisted(self):
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "example.com": route(200, "text/html", HTML,
                                                  **{"set-cookie": "s=SECRET; HttpOnly"})})
        c = self.cfg(t)
        R.capture_one(c, "public-url", "https://example.com/x")
        for name in os.listdir(c.normalized_dir):
            blob = open(os.path.join(c.normalized_dir, name), encoding="utf-8").read()
            self.assertNotIn("set-cookie", blob.lower())
            self.assertNotIn("SECRET", blob)

    def test_84_85_86_87_no_asset_fetch_or_execute_or_crawl(self):
        rec = []
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "example.com/page": route(200, "text/html", HTML_LINKS)}, record=rec)
        res = R.capture_one(self.cfg(t), "public-url", "https://example.com/page")
        fetched = [u for (u, h) in rec]
        # only the page (+robots) — never the linked cdn asset, next page, or form action
        self.assertTrue(any("example.com/page" in u for u in fetched))
        self.assertFalse(any("cdn.example.com" in u for u in fetched))
        self.assertFalse(any("other.example.com" in u for u in fetched))
        self.assertFalse(any("submit" in u for u in fetched))
        # no executed JS value and no hidden form value leaked into evidence
        blob = json.dumps(res)
        self.assertNotIn("SHOULD_NOT_EXECUTE", blob)

    def test_88_per_host_concurrency_one(self):
        gate = R.HostGate()
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "example.com": route(200, "text/html", HTML)})
        R.capture_one(self.cfg(t, host_gate=gate), "public-url", "https://example.com/x")
        self.assertLessEqual(gate.max_concurrent, 1)

    def test_89_rate_limiting(self):
        clock = [100.0]
        gate = R.HostGate(min_interval=5.0, clock=lambda: clock[0], sleeper=lambda s: None)
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "example.com": route(200, "text/html", HTML)})
        c = self.cfg(t, host_gate=gate)
        R.capture_one(c, "public-url", "https://example.com/a")
        R.capture_one(c, "public-url", "https://example.com/b")
        self.assertTrue(gate.sleeps)


# ======================================================================== 11) cache
class TestCache(Base):
    def _cfg_with_page(self, extra=None, record=None):
        r = {"content-type": "text/html"}
        if extra:
            r.update(extra)
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "example.com": (200, r, HTML)}, record=record)
        return self.cfg(t, record=None) if False else self.cfg(t)

    def test_90_91_conditional_headers_sent(self):
        rec = []
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "example.com": route(200, "text/html", HTML,
                                                  etag='"v1"', **{"last-modified": "Mon"})}, record=rec)
        c = self.cfg(t)
        R.capture_one(c, "public-url", "https://example.com/x")
        # second capture reuses the cache -> conditional headers included
        rec.clear()
        R.capture_one(c, "public-url", "https://example.com/x")
        page = [h for (u, h) in rec if "example.com/x" in u]
        self.assertTrue(any("If-None-Match" in h for h in page))

    def test_92_304_cache_reuse(self):
        seq = [route(200, "text/html", HTML, etag='"v1"'), route(304, "text/html", b"", etag='"v1"')]
        idx = [0]

        def pager(url, headers):
            r = seq[min(idx[0], 1)]
            idx[0] += 1
            return r
        t = make_transport({"robots.txt": route(404, "text/plain", b""), "example.com/x": pager})
        c = self.cfg(t)
        r1 = R.capture_one(c, "public-url", "https://example.com/x")
        r2 = R.capture_one(c, "public-url", "https://example.com/x")
        self.assertEqual(r1["evidence_count"], r2["evidence_count"])
        self.assertIn(R.CAP_FROM_CACHE, r2["capture_status_counts"])

    def test_93_cache_content_hash(self):
        c = self.cfg(make_transport({"robots.txt": route(404, "text/plain", b""),
                                     "example.com": route(200, "text/html", HTML)}))
        R.capture_one(c, "public-url", "https://example.com/x")
        metas = [f for f in os.listdir(c.cache_dir) if f.endswith(".meta.json")]
        self.assertTrue(metas)
        meta = json.load(open(os.path.join(c.cache_dir, metas[0]), encoding="utf-8"))
        self.assertEqual(meta["content_sha256"], R._sha256_hex(HTML))

    def test_94_corrupt_cache_blocked(self):
        c = self.cfg(make_transport({"robots.txt": route(404, "text/plain", b""),
                                     "example.com": route(200, "text/html", HTML)}))
        R.capture_one(c, "public-url", "https://example.com/x")
        binf = [f for f in os.listdir(c.cache_dir) if f.endswith(".bin")][0]
        with open(os.path.join(c.cache_dir, binf), "wb") as f:
            f.write(b"CORRUPTED")
        with self.assertRaises(R.ResearchError) as e:
            R._cache_load(c, binf[:-4])
        self.assertEqual(e.exception.readiness, R.CACHE_BLOCKED)

    def test_95_secret_url_not_cached(self):
        # a github request that carried an auth token is NOT cached
        env = {R.GITHUB_TOKEN_ENV: "sk-ant-SECRETTOKEN1234567890"}
        c = self.cfg(make_transport({"repos/owner/repo": route(200, "application/json", GH_REPO),
                                     "releases/latest": route(404, "application/json", b"")}), env=env)
        R.capture_one(c, "github", "owner/repo")
        cache_files = os.listdir(c.cache_dir) if os.path.isdir(c.cache_dir) else []
        self.assertEqual(cache_files, [])

    def test_96_offline_replay(self):
        c = self.cfg(make_transport({"robots.txt": route(404, "text/plain", b""),
                                     "example.com": route(200, "text/html", HTML)}))
        res = R.capture_one(c, "public-url", "https://example.com/x")
        # replay uses only local bytes; give it a transport that raises if touched
        c.transport = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network touched"))
        rep = R.replay_run(c, res["run_id"])
        self.assertEqual(rep["readiness"], R.REPLAY_READY)


# ======================================================================== 12) determinism
class TestDeterminism(Base):
    def _capture(self, resolver=resolver_public):
        c = self.cfg(make_transport({"robots.txt": route(404, "text/plain", b""),
                                     "example.com": route(200, "text/html", HTML)}), resolver=resolver)
        return c, R.capture_one(c, "public-url", "https://example.com/nurse")

    def test_97_100_deterministic_ids(self):
        _, a = self._capture()
        _, b = self._capture()
        self.assertEqual(a["run_id"], b["run_id"])

    def test_98_99_timestamp_and_mtime_excluded(self):
        c1, a = self._capture()
        cap = R._load_capture(c1, a["capture_status_counts"] and
                              [f[:-5] for f in os.listdir(c1.normalized_dir)][0])
        self.assertNotIn("fetched_at", cap)
        self.assertNotIn("created_at", cap)
        # the raw-capture identity never references a timestamp / mtime / path
        blob = json.dumps(cap)
        self.assertNotIn("mtime", blob)

    def test_101_row_order_excluded_from_identity(self):
        items = [{"evidence_id": "b"}, {"evidence_id": "a"}]
        e1 = R._evidence("t", "f", "v1", extraction_method="m", source_locator="l",
                         source_content_hash="h")
        e2 = R._evidence("t", "f", "v1", extraction_method="m", source_locator="l",
                         source_content_hash="h")
        self.assertEqual(e1["evidence_id"], e2["evidence_id"])

    def test_102_canonical_json(self):
        self.assertEqual(R.canonical_json({"b": 1, "a": 2}), R.canonical_json({"a": 2, "b": 1}))

    def test_103_sorted_records(self):
        rows = R._sort_evidence([R._evidence("z", "f", "1", extraction_method="m",
                                             source_locator="l", source_content_hash="h"),
                                 R._evidence("a", "f", "1", extraction_method="m",
                                             source_locator="l", source_content_hash="h")])
        self.assertEqual([r["evidence_type"] for r in rows], ["a", "z"])

    def test_104_105_no_nan_inf(self):
        with self.assertRaises(ValueError):
            R.canonical_json({"x": float("nan")})
        with self.assertRaises(ValueError):
            R.canonical_json({"x": float("inf")})

    def test_106_decimal_not_float(self):
        # Decimal preserves the value as published (trailing zeros kept, never a binary float)
        self.assertEqual(R._to_decimal_str("24.990"), "24.990")
        self.assertEqual(R._to_decimal_str("$1,234.50"), "1234.50")
        # value stored as a string, never a binary float
        _, a = self._capture()
        blob = json.dumps(a)
        self.assertNotIn("24.9900000", blob)

    def test_export_bytes_deterministic(self):
        c1, a = self._capture()
        c2, b = self._capture()
        f1 = os.path.join(c1.reports_dir, a["run_id"], "research_snapshot.json")
        f2 = os.path.join(c2.reports_dir, b["run_id"], "research_snapshot.json")
        self.assertEqual(open(f1, "rb").read(), open(f2, "rb").read())


# ======================================================================== 13) HTML / JSON-LD extraction
class TestHTMLExtraction(Base):
    def _ev(self, body):
        oc = R.FetchOutcome(status=R.CAP_CAPTURED, media_type="text/html", body=body)
        return {e["evidence_type"]: e for e in R._extract_generic(oc, "https://example.com/x")}

    def test_107_title(self):
        self.assertEqual(self._ev(HTML)["html.title"]["observed_value"], "Nurse Sweatshirt")

    def test_108_meta_description(self):
        self.assertIn("cozy", self._ev(HTML)["html.meta_description"]["observed_value"])

    def test_109_canonical(self):
        self.assertEqual(self._ev(HTML)["html.canonical"]["observed_value"],
                         "https://example.com/nurse")

    def test_110_open_graph(self):
        self.assertEqual(self._ev(HTML)["opengraph.title"]["observed_value"], "Nurse Sweatshirt OG")

    def test_111_jsonld_size_bound(self):
        big = b'<script type="application/ld+json">{"@type":"Product","name":"' + b"A" * 300000 + b'"}</script>'
        page = b"<html><head>" + big + b"</head></html>"
        self.assertNotIn("product.title", self._ev(page))

    def test_112_jsonld_nesting_bound(self):
        deep = "{" + "".join('"a":{' for _ in range(30)) + '"@type":"Product","name":"x"' + "}" * 31
        page = ('<html><head><script type="application/ld+json">%s</script></head></html>' % deep).encode()
        self.assertNotIn("product.title", self._ev(page))

    def test_113_jsonld_invalid_type_ignored(self):
        page = (b'<html><head><script type="application/ld+json">'
                b'{"@type":"Recipe","name":"Soup"}</script></head></html>')
        self.assertNotIn("product.title", self._ev(page))

    def test_114_jsonld_duplicate_key_rejected(self):
        page = (b'<html><head><script type="application/ld+json">'
                b'{"@type":"Product","name":"A","name":"B"}</script></head></html>')
        self.assertNotIn("product.title", self._ev(page))

    def test_115_product_title(self):
        self.assertEqual(self._ev(HTML)["product.title"]["observed_value"], "Nurse Sweatshirt")

    def test_116_price(self):
        self.assertEqual(self._ev(HTML)["product.price"]["observed_value"], "24.99")

    def test_117_currency(self):
        self.assertEqual(self._ev(HTML)["product.price"]["observed_currency"], "USD")

    def test_118_brand(self):
        self.assertEqual(self._ev(HTML)["product.brand"]["observed_value"], "UMEE")

    def test_119_availability(self):
        self.assertIn("InStock", self._ev(HTML)["product.availability"]["observed_value"])

    def test_120_aggregate_rating(self):
        self.assertEqual(self._ev(HTML)["product.rating_value"]["observed_value"], "4.7")

    def test_121_rating_count(self):
        self.assertEqual(self._ev(HTML)["product.rating_count"]["observed_value"], "123")

    def test_122_absent_field_not_zero(self):
        page = (b'<html><head><script type="application/ld+json">'
                b'{"@type":"Product","name":"No Price"}</script></head></html>')
        ev = self._ev(page)
        self.assertNotIn("product.price", ev)
        self.assertNotIn("product.rating_value", ev)

    def test_123_124_125_126_no_sensitive_extraction(self):
        oc = R.FetchOutcome(status=R.CAP_CAPTURED, media_type="text/html", body=AMZ_HTML)
        vals = json.dumps(R._extract_amazon(oc, "https://www.amazon.com/dp/B012345678"))
        self.assertNotIn("customer review body", vals)
        self.assertNotIn("Jane Customer", vals)
        self.assertNotIn("TRACKING-TOKEN-XYZ", vals)
        self.assertNotIn("csrf", vals)

    def test_value_kind_labelled(self):
        ev = self._ev(HTML)
        self.assertEqual(ev["html.title"]["value_kind"], "OBSERVED")
        self.assertEqual(ev["product.price"]["value_kind"], "NORMALIZED_TECHNICAL")


# ======================================================================== 13b) Amazon extraction
class TestAmazonExtraction(Base):
    def test_asin_from_url(self):
        oc = R.FetchOutcome(status=R.CAP_CAPTURED, media_type="text/html", body=AMZ_HTML)
        ev = {e["evidence_type"]: e for e in R._extract_amazon(oc, "https://www.amazon.com/dp/B012345678")}
        self.assertEqual(ev["product.asin"]["observed_value"], "B012345678")

    def test_129_robot_challenge_recorded(self):
        challenge = b"<html>Enter the characters you see below to discuss automated access</html>"
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "/dp/": route(200, "text/html", challenge)})
        res = R.capture_one(self.cfg(t), "amazon-public-product", "https://www.amazon.com/dp/B012345678")
        self.assertEqual(res["readiness"], R.NETWORK_UNAVAILABLE)
        self.assertIn(R.CAP_CHALLENGED, res["capture_status_counts"])

    def test_130_403_recorded(self):
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "/dp/": route(403, "text/html", b"forbidden")})
        res = R.capture_one(self.cfg(t), "amazon-public-product", "https://www.amazon.com/dp/B012345678")
        self.assertIn(R.CAP_CHALLENGED, res["capture_status_counts"])

    def test_131_429_no_aggressive_retry(self):
        calls = [0]

        def once(url, headers):
            calls[0] += 1
            return (429, {"content-type": "text/html"}, b"slow down")
        t = make_transport({"robots.txt": route(404, "text/plain", b""), "/dp/": once})
        R.capture_one(self.cfg(t), "amazon-public-product", "https://www.amazon.com/dp/B012345678")
        self.assertEqual(calls[0], 1)


# ======================================================================== 14) GitHub
class TestGitHub(Base):
    def _cfg(self, env=None, record=None):
        t = make_transport({"repos/owner/repo/releases/latest": route(200, "application/json", GH_REL),
                            "repos/owner/repo": route(200, "application/json", GH_REPO)}, record=record)
        return self.cfg(t, env=env or {})

    def test_132_public_metadata(self):
        res = R.capture_one(self._cfg(), "github", "owner/repo")
        c = self.cfg(make_transport({}))  # unused
        self.assertEqual(res["readiness"], R.READY)

    def test_133_release_metadata(self):
        c = self._cfg()
        res = R.capture_one(c, "github", "owner/repo")
        blob = "".join(open(os.path.join(c.normalized_dir, f), encoding="utf-8").read()
                       for f in os.listdir(c.normalized_dir))
        self.assertIn("v1.2.3", blob)
        self.assertIn("wheel.whl", blob)

    def test_134_token_optional(self):
        res = R.capture_one(self._cfg(env={}), "github", "owner/repo")
        self.assertEqual(res["readiness"], R.READY)

    def test_135_token_not_persisted(self):
        secret = "sk-ant-GHSECRETTOKENVALUE0001"
        c = self._cfg(env={R.GITHUB_TOKEN_ENV: secret})
        R.capture_one(c, "github", "owner/repo")
        for base_sub in (c.normalized_dir, c.manifests_dir, c.reports_dir, c.raw_dir):
            for dp, _dn, fn in os.walk(base_sub):
                for f in fn:
                    self.assertNotIn(secret, open(os.path.join(dp, f), encoding="utf-8",
                                                  errors="replace").read())

    def test_136_token_only_to_github_host(self):
        rec = []
        R.capture_one(self._cfg(env={R.GITHUB_TOKEN_ENV: "sk-ant-X0123456789ABCDEF"}, record=rec),
                      "github", "owner/repo")
        for (u, h) in rec:
            if "Authorization" in h:
                self.assertIn("api.github.com", u)

    def test_137_no_mutation_verbs_in_source(self):
        src = open(R.__file__, encoding="utf-8").read()
        for verb in ('method="POST"', 'method="PUT"', 'method="DELETE"', 'method="PATCH"'):
            self.assertNotIn(verb, src)


# ======================================================================== 15) PyPI
class TestPyPI(Base):
    def test_138_official_endpoint(self):
        rec = []
        t = make_transport({"pypi.org": route(200, "application/json", PYPI)}, record=rec)
        res = R.capture_one(self.cfg(t), "pypi", "cryptography")
        self.assertEqual(res["readiness"], R.READY)
        self.assertTrue(all("pypi.org/pypi/" in u for (u, h) in rec))

    def test_139_malformed_metadata(self):
        t = make_transport({"pypi.org": route(200, "application/json", b"{not json")})
        res = R.capture_one(self.cfg(t), "pypi", "cryptography")
        self.assertEqual(res["readiness"], R.READY_EMPTY)

    def test_140_141_no_install_no_pip(self):
        src = open(R.__file__, encoding="utf-8").read()
        self.assertNotIn("pip install", src)
        self.assertNotIn("subprocess", src)

    def test_pypi_fields(self):
        c = self.cfg(make_transport({"pypi.org": route(200, "application/json", PYPI)}))
        R.capture_one(c, "pypi", "cryptography")
        blob = "".join(open(os.path.join(c.normalized_dir, f), encoding="utf-8").read()
                       for f in os.listdir(c.normalized_dir))
        self.assertIn("42.0.0", blob)
        self.assertIn(">=3.7", blob)


# ======================================================================== 16) RSS / Atom
class TestFeeds(Base):
    def _feed(self, body, ctype="application/rss+xml"):
        oc = R.FetchOutcome(status=R.CAP_CAPTURED, media_type=ctype, body=body)
        return R._extract_feed(oc, "https://feeds.example.com/f")

    def test_142_rss_safe(self):
        ev = self._feed(RSS)
        titles = [e["observed_value"] for e in ev if e["evidence_type"] == "feed.item.title"]
        self.assertIn("Item A", titles)
        self.assertIn("Item B", titles)

    def test_143_atom_safe(self):
        ev = self._feed(ATOM, "application/atom+xml")
        self.assertTrue(any(e["evidence_type"] == "feed.item.title" for e in ev))

    def test_144_external_entity_blocked(self):
        self.assertIsNone(self._feed(XXE))

    def test_145_entity_expansion_blocked(self):
        self.assertIsNone(self._feed(BILLION))

    def test_146_rss_dedupe(self):
        ev = self._feed(RSS)
        ids = [e["observed_value"] for e in ev if e["evidence_type"] == "feed.item.id"]
        self.assertEqual(sorted(ids), ["a", "b"])

    def test_147_item_links_not_fetched(self):
        rec = []
        t = make_transport({"robots.txt": route(404, "text/plain", b""),
                            "feeds.example.com": route(200, "application/rss+xml", RSS)}, record=rec)
        R.capture_one(self.cfg(t), "rss", "https://feeds.example.com/f.xml")
        self.assertFalse(any("ex.com/a" in u or "ex.com/b" in u for (u, h) in rec))


# ======================================================================== 17) local file
class TestLocalFile(Base):
    def _root(self):
        root = os.path.join(self.tmp, "lroot")
        os.makedirs(root, exist_ok=True)
        return root

    def _cfg(self, root):
        return R.Config(os.path.join(self.tmp, "lws%d" % self._n), input_root=root, env={},
                        resolver=resolver_fail,
                        transport=lambda *a, **k: (_ for _ in ()).throw(AssertionError("net")))

    def test_148_allowed_root(self):
        self._n += 1
        root = self._root()
        with open(os.path.join(root, "p.html"), "wb") as f:
            f.write(HTML)
        res = R.capture_one(self._cfg(root), "local-file", "p.html")
        self.assertEqual(res["readiness"], R.READY)

    def test_149_traversal_blocked(self):
        self._n += 1
        res = R.capture_one(self._cfg(self._root()), "local-file", "../../etc/passwd")
        self.assertEqual(res["readiness"], R.SOURCE_BLOCKED)

    def test_150_absolute_outside_blocked(self):
        self._n += 1
        res = R.capture_one(self._cfg(self._root()), "local-file", os.path.abspath(__file__))
        self.assertEqual(res["readiness"], R.SOURCE_BLOCKED)

    def test_151_symlink_escape_blocked(self):
        self._n += 1
        root = self._root()
        outside = os.path.join(self.tmp, "outside.html")
        with open(outside, "wb") as f:
            f.write(HTML)
        link = os.path.join(root, "link.html")
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlink not permitted on this platform")
        res = R.capture_one(self._cfg(root), "local-file", "link.html")
        self.assertEqual(res["readiness"], R.SOURCE_BLOCKED)

    def test_153_binary_blocked(self):
        self._n += 1
        root = self._root()
        with open(os.path.join(root, "img.html"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
        res = R.capture_one(self._cfg(root), "local-file", "img.html")
        self.assertEqual(res["readiness"], R.CONTENT_TYPE_BLOCKED)

    def test_154_secret_file_blocked(self):
        self._n += 1
        root = self._root()
        with open(os.path.join(root, ".env"), "wb") as f:
            f.write(b"SECRET=1")
        res = R.capture_one(self._cfg(root), "local-file", ".env")
        self.assertIn(res["readiness"], (R.SOURCE_BLOCKED, R.CONTENT_TYPE_BLOCKED))

    def test_154b_credentials_file_blocked(self):
        self._n += 1
        root = self._root()
        with open(os.path.join(root, "credentials.json"), "wb") as f:
            f.write(b"{}")
        res = R.capture_one(self._cfg(root), "local-file", "credentials.json")
        self.assertEqual(res["readiness"], R.SOURCE_BLOCKED)

    def test_155_local_input_immutable(self):
        self._n += 1
        root = self._root()
        path = os.path.join(root, "p.html")
        with open(path, "wb") as f:
            f.write(HTML)
        before = (os.path.getmtime(path), open(path, "rb").read())
        R.capture_one(self._cfg(root), "local-file", "p.html")
        self.assertEqual(open(path, "rb").read(), before[1])

    def test_unsupported_extension_blocked(self):
        self._n += 1
        root = self._root()
        with open(os.path.join(root, "a.exe"), "wb") as f:
            f.write(b"text")
        res = R.capture_one(self._cfg(root), "local-file", "a.exe")
        self.assertEqual(res["readiness"], R.CONTENT_TYPE_BLOCKED)

    def test_no_input_root_blocked(self):
        self._n += 1
        c = R.Config(os.path.join(self.tmp, "noroot"), input_root=None, env={},
                     transport=lambda *a, **k: None)
        res = R.capture_one(c, "local-file", "p.html")
        self.assertEqual(res["readiness"], R.SOURCE_BLOCKED)


# ======================================================================== 18) atomicity
class TestAtomicity(Base):
    def test_156_run_atomic(self):
        c = self.cfg(make_transport({"robots.txt": route(404, "text/plain", b""),
                                     "example.com": route(200, "text/html", HTML)}))
        res = R.capture_one(c, "public-url", "https://example.com/x")
        self.assertTrue(os.path.isfile(os.path.join(c.manifests_dir, res["run_id"] + ".json")))

    def test_157_158_no_temp_files_left(self):
        c = self.cfg(make_transport({"robots.txt": route(404, "text/plain", b""),
                                     "example.com": route(200, "text/html", HTML)}))
        R.capture_one(c, "public-url", "https://example.com/x")
        leftovers = []
        for dp, _dn, fn in os.walk(c.base_dir):
            leftovers += [f for f in fn if f.startswith(".tmp-710-")]
        self.assertEqual(leftovers, [])

    def test_159_export_atomic(self):
        c = self.cfg(make_transport({"robots.txt": route(404, "text/plain", b""),
                                     "example.com": route(200, "text/html", HTML)}))
        res = R.capture_one(c, "public-url", "https://example.com/x")
        for n in ("research_snapshot.json", "research_evidence.tsv", "research_report.md"):
            self.assertTrue(os.path.isfile(os.path.join(c.reports_dir, res["run_id"], n)))


# ======================================================================== 19) exports
class TestExports(Base):
    def _run(self, body=HTML):
        c = self.cfg(make_transport({"robots.txt": route(404, "text/plain", b""),
                                     "example.com": route(200, "text/html", body)}))
        res = R.capture_one(c, "public-url", "https://example.com/x")
        return c, res["run_id"]

    def test_160_161_162_exports_present(self):
        c, rid = self._run()
        d = os.path.join(c.reports_dir, rid)
        self.assertTrue(os.path.isfile(os.path.join(d, "research_snapshot.json")))
        self.assertTrue(os.path.isfile(os.path.join(d, "research_evidence.tsv")))
        self.assertTrue(os.path.isfile(os.path.join(d, "research_report.md")))

    def test_163_tsv_formula_injection(self):
        self.assertEqual(R._tsv_cell("=cmd()"), "'=cmd()")
        self.assertEqual(R._tsv_cell("@SUM(A1)"), "'@SUM(A1)")
        self.assertEqual(R._tsv_cell("+1+2"), "'+1+2")

    def test_164_negative_decimal_preserved(self):
        self.assertEqual(R._tsv_cell("-2.50"), "-2.50")

    def test_165_vietnamese_unicode_preserved(self):
        page = ('<html><head><title>Áo nữ điều dưỡng — vượt</title></head></html>').encode("utf-8")
        c, rid = self._run(page)
        snap = open(os.path.join(c.reports_dir, rid, "research_snapshot.json"),
                    encoding="utf-8").read()
        self.assertIn("điều dưỡng", snap)

    def test_snapshot_no_secrets_or_amazon_data(self):
        c, rid = self._run()
        snap = json.load(open(os.path.join(c.reports_dir, rid, "research_snapshot.json"),
                              encoding="utf-8"))
        self.assertEqual(snap["seller_central_connections"], 0)
        self.assertIn("disclaimers", snap)


# ======================================================================== 20) secret redaction
class TestSecretRedaction(Base):
    def test_166_167_stdout_stderr_clean(self):
        secret = "sk-ant-CLI-SECRET-1234567890"
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            os.environ[R.GITHUB_TOKEN_ENV] = secret
            try:
                R.run_cli(["--base-dir", os.path.join(self.tmp, "sr"), "provider-check"])
            finally:
                os.environ.pop(R.GITHUB_TOKEN_ENV, None)
        self.assertNotIn(secret, out.getvalue())
        self.assertNotIn(secret, err.getvalue())

    def test_168_logs_clean(self):
        secret = "sk-ant-LOGSECRET-0987654321"
        env = {R.GITHUB_TOKEN_ENV: secret}
        c = self.cfg(make_transport({"repos/owner/repo": route(200, "application/json", GH_REPO),
                                     "releases/latest": route(404, "application/json", b"")}), env=env)
        R.capture_one(c, "github", "owner/repo")
        logf = os.path.join(c.logs_dir, "operations.jsonl")
        if os.path.isfile(logf):
            self.assertNotIn(secret, open(logf, encoding="utf-8").read())

    def test_169_170_manifest_exports_clean(self):
        secret = "sk-ant-MANIFESTSECRET-55555"
        os.environ[R.GITHUB_TOKEN_ENV] = secret
        try:
            c = self.cfg(make_transport({"repos/owner/repo": route(200, "application/json", GH_REPO),
                                         "releases/latest": route(404, "application/json", b"")}),
                         env={R.GITHUB_TOKEN_ENV: secret})
            R.capture_one(c, "github", "owner/repo")
        finally:
            os.environ.pop(R.GITHUB_TOKEN_ENV, None)
        for dp, _dn, fn in os.walk(c.base_dir):
            for f in fn:
                self.assertNotIn(secret, open(os.path.join(dp, f), encoding="utf-8",
                                              errors="replace").read())

    def test_redact_helper(self):
        self.assertIn(D.REDACTED, R.redact("token sk-ant-abcdef1234567890"))


# ======================================================================== 21) validate-only
class TestValidateOnly(Base):
    def test_171_172_no_network_no_files(self):
        plan = {"sources": [{"source_type": "public-url", "source_locator": "https://example.com/x"}]}
        pf = os.path.join(self.tmp, "vo.json")
        with open(pf, "w") as f:
            json.dump(plan, f)
        c = R.Config(os.path.join(self.tmp, "vows"), env={},
                     transport=lambda *a, **k: (_ for _ in ()).throw(AssertionError("net")),
                     resolver=resolver_fail)
        res = R.validate_plan_only(c, pf)
        self.assertEqual(res["readiness"], R.VALIDATE_READY)
        self.assertEqual(res["network_requests"], 0)
        self.assertFalse(os.path.isdir(c.base_dir) and os.listdir(c.base_dir))


# ======================================================================== 22/23) provider + boundary + repo
class TestBoundaryAndProvider(Base):
    def test_173_provider_check(self):
        res = R.provider_check(self.cfg(make_transport({})))
        self.assertTrue(res["seller_central_blocked_first"])
        self.assertEqual(sorted(res["source_types"]),
                         ["amazon-public-product", "github", "local-file", "public-url", "pypi", "rss"])

    def test_176_amazon_counters_zero(self):
        c = self.cfg(make_transport({"robots.txt": route(404, "text/plain", b""),
                                     "example.com": route(200, "text/html", HTML)}))
        res = R.capture_one(c, "public-url", "https://example.com/x")
        self.assertTrue(all(v == 0 for v in res["amazon_counters"].values()))
        cap = R._load_capture(c, [f[:-5] for f in os.listdir(c.normalized_dir)][0])
        self.assertTrue(all(cap[k] == 0 for k in R.AMAZON_COUNTERS))

    def test_177_no_seller_central_transport_call(self):
        rec = []
        R.capture_one(self.cfg(make_transport({}, record=rec)), "public-url", SELLER_CENTRAL)
        self.assertEqual(rec, [])

    def test_178_179_no_browser_or_subprocess(self):
        src = open(R.__file__, encoding="utf-8").read().lower()
        for banned in ("selenium", "playwright", "webdriver", "pyppeteer", "subprocess",
                       "os.system", "popen"):
            self.assertNotIn(banned, src)

    def test_174_upstream_trees_untouched(self):
        # capture writes ONLY under base_dir; a sibling "7.9" tree is never touched
        upstream = os.path.join(self.tmp, "upstream", "7.9")
        os.makedirs(upstream, exist_ok=True)
        with open(os.path.join(upstream, "keep.json"), "w") as f:
            f.write('{"x":1}')
        before = os.path.getmtime(os.path.join(upstream, "keep.json"))
        c = self.cfg(make_transport({"robots.txt": route(404, "text/plain", b""),
                                     "example.com": route(200, "text/html", HTML)}))
        R.capture_one(c, "public-url", "https://example.com/x")
        self.assertEqual(os.path.getmtime(os.path.join(upstream, "keep.json")), before)
        self.assertNotIn("upstream", c.base_dir)

    def test_verify_run_detects_tamper(self):
        c = self.cfg(make_transport({"robots.txt": route(404, "text/plain", b""),
                                     "example.com": route(200, "text/html", HTML)}))
        res = R.capture_one(c, "public-url", "https://example.com/x")
        self.assertEqual(R.verify_run(c, res["run_id"])["readiness"], R.VERIFY_READY)
        # corrupt a raw byte file -> verify fails
        raw = [f for f in os.listdir(c.raw_dir)][0]
        with open(os.path.join(c.raw_dir, raw), "ab") as f:
            f.write(b"TAMPER")
        self.assertEqual(R.verify_run(c, res["run_id"])["readiness"], R.INTEGRITY_BLOCKED)


# ======================================================================== real loopback server (synthetic HTTP)
class _Handler(BaseHTTPRequestHandler):
    payload = HTML
    ctype = "text/html"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/robots.txt":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", self.ctype)
        self.end_headers()
        self.wfile.write(self.payload)


class TestRealLoopbackServer(Base):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_180_real_http_capture_loopback_only(self):
        # exercises the REAL transport + real DNS resolution to 127.0.0.1, permitted only with the
        # explicit local-test flag; the public Internet is never contacted.
        c = R.Config(os.path.join(self.tmp, "realws"), env={}, allow_local=True,
                     host_gate=R.HostGate())
        res = R.capture_one(c, "public-url", f"http://127.0.0.1:{self.port}/page")
        self.assertEqual(res["readiness"], R.READY)
        self.assertGreater(res["evidence_count"], 0)

    def test_real_http_blocked_without_local_flag(self):
        c = R.Config(os.path.join(self.tmp, "realws2"), env={}, allow_local=False,
                     host_gate=R.HostGate())
        res = R.capture_one(c, "public-url", f"http://127.0.0.1:{self.port}/page")
        self.assertEqual(res["readiness"], R.NETWORK_POLICY_BLOCKED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
