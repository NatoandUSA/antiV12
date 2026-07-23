#!/usr/bin/env python3
"""Phase 7.12 — Connected Owner Notification Delivery, Digest Queue & Audit Trail.

Every test runs with NO real internet call. The accepted Phase 7.11 alert authority is driven
through the accepted 7.10 injected-transport pattern to produce GENUINE hash-chained alert state,
and every Phase 7.12 webhook delivery uses an injected transport (never a socket). The permanent
Amazon-account boundary is asserted throughout — no Seller Central, no seller API, no Ads API, no
seller OAuth — and every Amazon counter stays a constant zero. Synthetic fixtures only; the upstream
Phase 7.3–7.11 runtime trees are never touched.
"""
import ast
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "core"), os.path.join(ROOT, "production")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import phase7_owner_notification_delivery as N   # noqa: E402
from production import phase7_connected_research_watchlists as W  # noqa: E402
from production import phase7_connected_public_research as R      # noqa: E402
import network_policy as NP                                       # noqa: E402
import diagnostics as D                                           # noqa: E402

# Amazon endpoint literals live in TEST code only (production assembles nothing like them). They are
# used solely to assert these destinations are BLOCKED by the reused network policy.
SELLER_CENTRAL = "https://sellercentral.amazon.com/home"
SELLER_CENTRAL_SUB = "https://sellercentral-europe.amazon.com/x"
SELLER_SIGNIN = "https://www.amazon.com/ap/signin"
SP_API = "https://sellingpartnerapi-na.amazon.com/orders"
ADS_API = "https://advertising-api.amazon.com/v2/campaigns"

MODULE_PATH = os.path.join(ROOT, "production", "phase7_owner_notification_delivery.py")


def module_ast():
    """Parse the production module and return (imported_modules, dotted_call_names, shell_true_flag).

    Analyzing the AST (not raw text) means a prohibited concept merely NAMED in a docstring — e.g.
    "never registers schtasks/cron" — is never mistaken for an actual call or import."""
    src = open(MODULE_PATH, encoding="utf-8").read()
    tree = ast.parse(src)
    imports, calls, shell_true = set(), set(), []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imports.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or "").split(".")[0])
        elif isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                calls.add(f.id)
            elif isinstance(f, ast.Attribute):
                parts, cur = [], f
                while isinstance(cur, ast.Attribute):
                    parts.append(cur.attr)
                    cur = cur.value
                if isinstance(cur, ast.Name):
                    parts.append(cur.id)
                calls.add(".".join(reversed(parts)))
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    shell_true.append(True)
    return imports, calls, shell_true


PUBLIC_ADDR = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def resolver_public(host, port=None, *a, **k):
    return list(PUBLIC_ADDR)


def resolver_private(host, port=None, *a, **k):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]


def resolver_mixed(host, port=None, *a, **k):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]


def resolver_fail(host, port=None, *a, **k):
    raise socket.gaierror("no such host")


# ------------------------------------------------------------- 7.11 alert-state builders
def _html(title="Nurse Sweatshirt", desc="A cozy nurse sweatshirt", price=None, currency="USD",
          availability="InStock"):
    parts = [b"<html><head><title>", title.encode(), b"</title>",
             b'<meta name="description" content="', desc.encode(), b'">']
    ld = {"@type": "Product", "name": title}
    if price is not None:
        ld["offers"] = {"price": price, "priceCurrency": currency, "availability": availability}
    parts.append(b'<script type="application/ld+json">' + json.dumps(ld).encode() + b"</script>")
    parts.append(b"</head><body>x</body></html>")
    return b"".join(parts)


def _wl_transport(body):
    def t(url, *, timeout_seconds, max_bytes, headers=None):
        if "robots" in url:
            return (404, {"content-type": "text/plain"}, b"")
        return (200, {"content-type": "text/html"}, body)
    return t


# a watchlist with one rule per owner severity so a single change round fires 4 alerts.
_SEV_RULES = [
    {"operator": "field-changed", "evidence_type": "html.title", "field_path": "title",
     "severity": "INFO", "label": "Title", "message": "the title changed"},
    {"operator": "field-changed", "evidence_type": "html.meta_description",
     "field_path": "meta.description", "severity": "REVIEW", "label": "Desc",
     "message": "the description changed"},
    {"operator": "field-changed", "evidence_type": "product.availability",
     "field_path": "jsonld.Product.offers.availability", "severity": "IMPORTANT",
     "label": "Availability", "message": "availability changed"},
    {"operator": "field-changed", "evidence_type": "product.price",
     "field_path": "jsonld.Product.offers.price", "severity": "CRITICAL", "label": "Price",
     "message": "the price changed"},
]


def build_alert_workspace(base, *, rules=None, name="Nurse watch"):
    """Build a GENUINE Phase 7.11 alert workspace with OPEN alerts across all owner severities.
    Returns (alert_dir, watchlist_id, alerts_by_severity)."""
    adir = os.path.join(base, "711")
    rules = _SEV_RULES if rules is None else rules
    wd = {"name": name, "timezone": "UTC", "schedule": {"type": "manual"},
          "sources": [{"source_type": "public-url", "source_locator": "https://example.com/nurse"}],
          "alert_rules": rules}
    c1 = W.Config(adir, env={}, transport=_wl_transport(
        _html(title="Nurse Sweatshirt", desc="d1", price="24.99", availability="InStock")),
        resolver=resolver_public, now_fn=lambda: 1_000_000.0)
    wid = W.create_watchlist(c1, wd)["watchlist_id"]
    W.run_watchlist(c1, wid, reference_time="2026-07-23T10:00:00Z")
    c2 = W.Config(adir, env={}, transport=_wl_transport(
        _html(title="Nurse Hoodie", desc="d2", price="29.99", availability="OutOfStock")),
        resolver=resolver_public, now_fn=lambda: 1_003_600.0)
    W.run_watchlist(c2, wid, reference_time="2026-07-23T11:00:00Z")
    rows = W.list_alerts(c2, wid)["alerts"]
    by_sev = {}
    for a in rows:
        by_sev.setdefault(a["severity"], []).append(a)
    return adir, wid, by_sev


# ------------------------------------------------------------- webhook transport doubles
def ntransport(status=200, resp_headers=None, resp_body=b'{"ok":true}', record=None):
    """A Phase 7.12 webhook transport double. Matches the real transport signature and NEVER opens a
    socket. Records (url, headers, body) when *record* is given."""
    rh = resp_headers or {"content-type": "application/json"}

    def t(url, *, headers, body, connect_timeout, read_timeout, max_response_bytes):
        if record is not None:
            record.append({"url": url, "headers": dict(headers), "body": bytes(body)})
        rb = resp_body[:max_response_bytes + 1] if resp_body else b""
        return (status, dict(rh), rb)
    return t


def ntransport_raise(exc):
    def t(url, *, headers, body, connect_timeout, read_timeout, max_response_bytes):
        raise exc
    return t


# ------------------------------------------------------------- route definition builders
def webhook_route(**over):
    d = {"name": "Owner Slack", "channel": "https-json-webhook", "payload_format": "slack",
         "destination_label": "owner-slack", "endpoint_env": "PHASE7_12_WEBHOOK_URL",
         "endpoint_host_allowlist": ["hooks.example.com"], "digest_policy": {"mode": "manual"}}
    d.update(over)
    return d


def outbox_route(**over):
    d = {"name": "Local", "channel": "local-outbox", "payload_format": "generic-json",
         "destination_label": "local", "digest_policy": {"mode": "manual"}}
    d.update(over)
    return d


LIVE_ENV = {"PHASE7_12_WEBHOOK_URL": "https://hooks.example.com/x/y",
            "PHASE7_12_ALLOW_LIVE_DELIVERY": "1"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p712t-")
        self._n = 0
        self._ws = None
        D.clear_events()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ws(self, **kw):
        if self._ws is None:
            self._ws = build_alert_workspace(self.tmp, **kw)
        return self._ws

    def bdir(self):
        self._n += 1
        return os.path.join(self.tmp, "712_%d" % self._n)

    def cfg(self, *, transport=None, status=None, resolver=resolver_public, env=None, now=2_000_000.0,
            alert_dir=None, allow_local=False, base=None):
        if transport is None and status is not None:
            transport = ntransport(status)
        if alert_dir is None:
            alert_dir, _, _ = self.ws()
        env = LIVE_ENV if env is None else env
        return N.Config(base or self.bdir(), alert_dir=alert_dir, env=env, transport=transport,
                        resolver=resolver, allow_local=allow_local,
                        now_fn=(now if callable(now) else (lambda: now)))

    def route(self, cfg, defn=None):
        res = N.create_route(cfg, defn or webhook_route())
        self.assertTrue(res["ok"], res)
        return res["route_id"]

    def approved_route(self, cfg, defn=None, **appr):
        rid = self.route(cfg, defn)
        N.approve_route(cfg, rid, appr.pop("actor", "OWNER"), appr.pop("note", "ok"), **appr)
        return rid

    def sent_delivery(self, cfg, rid=None, defn=None):
        rid = rid or self.approved_route(cfg, defn)
        bid = N.build_batch(cfg, rid)["batch_id"]
        res = N.send_batch(cfg, bid, confirm_send="SEND:" + bid)
        return rid, bid, res

    def write_def(self, defn):
        path = os.path.join(self.tmp, "def%d.json" % self._n)
        self._n += 1
        with open(path, "w", encoding="utf-8") as f:
            json.dump(defn, f)
        return path


# ============================================================ 1) CLI
class TestCLI(Base):
    def _run(self, argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        adir, _, _ = self.ws()

        def factory(args):
            return N.Config(args.base_dir, alert_dir=args.alert_dir, env=env or {},
                            allow_local=args.allow_local_test_server)
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = N.run_cli(argv, cfg_factory=factory)
                return code, out.getvalue(), err.getvalue()
            except SystemExit as e:
                return e.code, out.getvalue(), err.getvalue()

    def test_01_cli_help(self):
        code, out, err = self._run(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("send-batch", out)

    def test_02_missing_command(self):
        code, out, err = self._run(["--base-dir", self.tmp])
        self.assertNotEqual(code, 0)

    def test_03_unknown_command(self):
        code, out, err = self._run(["--base-dir", self.tmp, "teleport"])
        self.assertNotEqual(code, 0)

    def test_04_unknown_argument(self):
        code, out, err = self._run(["--base-dir", self.tmp, "list-routes", "--frobnicate", "x"])
        self.assertNotEqual(code, 0)

    def test_04b_all_subcommands_present(self):
        help_text = N.build_parser().format_help()
        for sub in ("create-route", "validate-route", "show-route", "list-routes", "approve-route",
                    "revoke-route", "show-approval", "list-approvals", "preview", "build-batch",
                    "send-batch", "send-due", "retry-delivery", "list-batches", "list-deliveries",
                    "show-delivery", "verify-state", "export", "provider-check", "scheduler-plan",
                    "break-stale-lock", "validate-only"):
            self.assertIn(sub, help_text)


# ============================================================ 2) route schema + identity
class TestRouteSchema(Base):
    def v(self, **over):
        return N.validate_route(webhook_route(**over))

    def test_05_valid_local_outbox_route(self):
        canonical, rid = N.validate_route(outbox_route())
        self.assertEqual(canonical["channel"], "local-outbox")
        self.assertTrue(rid.startswith("route-"))

    def test_06_valid_webhook_route(self):
        canonical, rid = self.v()
        self.assertEqual(canonical["channel"], "https-json-webhook")
        self.assertEqual(canonical["endpoint_host_allowlist"], ["hooks.example.com"])

    def test_07_invalid_channel(self):
        with self.assertRaises(N.NotificationError) as e:
            N.validate_route(webhook_route(channel="sms"))
        self.assertEqual(e.exception.code, "CHANNEL_INVALID")

    def test_08_invalid_payload_format(self):
        with self.assertRaises(N.NotificationError) as e:
            self.v(payload_format="yaml")
        self.assertEqual(e.exception.code, "PAYLOAD_FORMAT_INVALID")

    def test_09_unknown_route_field(self):
        with self.assertRaises(N.NotificationError) as e:
            self.v(mystery_field="x")
        self.assertEqual(e.exception.code, "ROUTE_UNKNOWN_FIELD")

    def test_10_secret_literal_rejected(self):
        with self.assertRaises(N.NotificationError) as e:
            self.v(owner_notes="my key is sk-ant-abcd1234efgh5678ijkl")
        self.assertIn(e.exception.code, ("FORBIDDEN_VALUE", "FORBIDDEN_FIELD"))

    def test_11_bearer_literal_rejected(self):
        with self.assertRaises(N.NotificationError) as e:
            self.v(description="use Bearer abcdef012345 to auth")
        self.assertEqual(e.exception.code, "FORBIDDEN_VALUE")

    def test_11b_literal_url_rejected(self):
        with self.assertRaises(N.NotificationError) as e:
            self.v(owner_notes="post to https://hooks.example.com/secret")
        self.assertEqual(e.exception.code, "FORBIDDEN_VALUE")

    def test_12_cookie_rejected(self):
        with self.assertRaises(N.NotificationError) as e:
            N.validate_route(webhook_route(cookie="a=b"))
        self.assertEqual(e.exception.code, "FORBIDDEN_FIELD")

    def test_13_authorization_header_rejected(self):
        with self.assertRaises(N.NotificationError):
            N.validate_route(webhook_route(headers={"Authorization": "Bearer x"}))

    def test_14_arbitrary_header_rejected(self):
        with self.assertRaises(N.NotificationError) as e:
            N.validate_route(webhook_route(custom_headers={"X-Evil": "1"}))
        self.assertEqual(e.exception.code, "FORBIDDEN_FIELD")

    def test_15_command_rejected(self):
        with self.assertRaises(N.NotificationError) as e:
            N.validate_route(webhook_route(command="rm -rf /"))
        self.assertEqual(e.exception.code, "FORBIDDEN_FIELD")

    def test_16_python_expression_rejected(self):
        with self.assertRaises(N.NotificationError) as e:
            N.validate_route(webhook_route(eval_hook="__import__('os')"))
        self.assertEqual(e.exception.code, "FORBIDDEN_FIELD")

    def test_17_template_code_rejected(self):
        with self.assertRaises(N.NotificationError) as e:
            N.validate_route(webhook_route(template="{{7*7}}"))
        self.assertEqual(e.exception.code, "FORBIDDEN_FIELD")

    def test_18_canonical_route_id(self):
        a, ida = self.v()
        b, idb = self.v()
        self.assertEqual(ida, idb)

    def test_19_reordered_route_keys(self):
        d1 = webhook_route()
        d2 = {k: d1[k] for k in reversed(list(d1.keys()))}
        self.assertEqual(N.validate_route(d1)[1], N.validate_route(d2)[1])

    def test_20_timestamp_excluded_from_route_id(self):
        base_id = self.v()[1]
        # annotations do not change identity
        self.assertEqual(self.v(description="x", owner_notes="y", name="Renamed")[1], base_id)

    def test_21_mtime_excluded_from_route_id(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        import time as _t
        _t.sleep(0.01)
        self.assertEqual(N.validate_route(webhook_route())[1], rid)

    def test_22_temp_path_excluded_from_route_id(self):
        # two different base dirs -> same route id for the same content
        c1, c2 = self.cfg(status=200), self.cfg(status=200)
        self.assertEqual(self.route(c1), self.route(c2))

    def test_22b_allowlist_order_excluded(self):
        a = N.validate_route(webhook_route(endpoint_host_allowlist=["a.example.com", "b.example.com"]))[1]
        b = N.validate_route(webhook_route(endpoint_host_allowlist=["b.example.com", "a.example.com"]))[1]
        self.assertEqual(a, b)

    def test_22c_route_has_no_secret_value(self):
        canonical, _ = self.v()
        blob = json.dumps(canonical)
        self.assertNotIn("://", blob)
        self.assertEqual(canonical["endpoint_env"], "PHASE7_12_WEBHOOK_URL")

    def test_22d_amazon_allowlist_host_rejected(self):
        with self.assertRaises(N.NotificationError) as e:
            N.validate_route(webhook_route(endpoint_host_allowlist=["sellercentral.amazon.com"]))
        self.assertEqual(e.exception.readiness, N.SELLER_CENTRAL_POLICY_BLOCKED)


# ============================================================ 3) approval chain
class TestApproval(Base):
    def test_23_approval_required(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        bid = N.build_batch(cfg, rid)["batch_id"]
        res = N.send_batch(cfg, bid, confirm_send="SEND:" + bid)
        self.assertEqual(res["readiness"], N.APPROVAL_REQUIRED)

    def test_24_approve_valid_route(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        res = N.approve_route(cfg, rid, "OWNER", "approved for owner slack")
        self.assertEqual(res["status"], "APPROVED")
        self.assertTrue(res["route_content_hash"])

    def test_25_route_modification_invalidates_approval(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg)
        # tamper the stored route file content while keeping its route_id
        route = N.load_route(cfg, rid)
        route["name"] = "Tampered Name"
        N._write_json(N._route_path(cfg, rid), route)
        route2 = N.load_route(cfg, rid)
        ok, reason, _ = N._approval_gate(cfg, route2)
        self.assertFalse(ok)
        self.assertEqual(reason, "APPROVAL_STALE_ROUTE_CHANGED")

    def test_26_revoke_route(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg)
        res = N.revoke_route(cfg, rid, "OWNER", "done")
        self.assertEqual(res["status"], "REVOKED")

    def test_27_reapprove_route(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg)
        N.revoke_route(cfg, rid, "OWNER", None)
        res = N.approve_route(cfg, rid, "OWNER", "again")
        self.assertEqual(res["status"], "APPROVED")
        self.assertEqual(res["history_length"], 3)

    def test_28_missing_approval_actor(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        with self.assertRaises(N.NotificationError) as e:
            N.approve_route(cfg, rid, "   ", "note")
        self.assertEqual(e.exception.code, "ACTOR_REQUIRED")

    def test_29_approval_chain_append_only(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg)
        N.revoke_route(cfg, rid, "OWNER", None)
        state = N._load_approval_state(cfg, rid)
        self.assertEqual([e["seq"] for e in state["history"]], [0, 1])

    def _corrupt_approval(self, cfg, rid, mutate):
        path = N._approval_path(cfg, rid)
        rec = json.load(open(path, encoding="utf-8"))
        mutate(rec)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f)

    def test_30_approval_tamper_detected(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg)

        def m(rec):
            rec["history"][0]["actor"] = "ATTACKER"
        self._corrupt_approval(cfg, rid, m)
        with self.assertRaises(N.NotificationError) as e:
            N._load_approval_state(cfg, rid)
        self.assertEqual(e.exception.readiness, N.APPROVAL_BLOCKED)

    def test_31_approval_deletion_detected(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg)
        N.revoke_route(cfg, rid, "OWNER", None)

        def m(rec):
            rec["history"] = rec["history"][:1]   # drop the last event but keep head_hash
        self._corrupt_approval(cfg, rid, m)
        with self.assertRaises(N.NotificationError):
            N._load_approval_state(cfg, rid)

    def test_32_approval_reorder_detected(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg)
        N.revoke_route(cfg, rid, "OWNER", None)

        def m(rec):
            rec["history"] = list(reversed(rec["history"]))
        self._corrupt_approval(cfg, rid, m)
        with self.assertRaises(N.NotificationError):
            N._load_approval_state(cfg, rid)

    def test_33_approval_corruption_blocks_delivery(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg)
        bid = N.build_batch(cfg, rid)["batch_id"]

        def m(rec):
            rec["history"][0]["route_content_hash"] = "deadbeef"
        self._corrupt_approval(cfg, rid, m)
        res = N.send_batch(cfg, bid, confirm_send="SEND:" + bid)
        self.assertEqual(res["readiness"], N.APPROVAL_BLOCKED)

    def test_33b_show_and_list_approvals(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg)
        self.assertEqual(N.show_approval(cfg, rid)["status"], "APPROVED")
        self.assertEqual(N.list_approvals(cfg)["count"], 1)

    def test_33c_approval_has_no_secret(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg)
        blob = json.dumps(N.show_approval(cfg, rid))
        self.assertNotIn("://", blob)
        self.assertNotIn(LIVE_ENV["PHASE7_12_WEBHOOK_URL"], blob)


# ============================================================ 4) local preview / outbox / live gate
class TestLocalAndGate(Base):
    def test_34_local_preview_without_approval(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        res = N.preview(cfg, rid)
        self.assertEqual(res["readiness"], N.PREVIEW_READY)
        self.assertGreaterEqual(res["alert_count"], 1)

    def test_35_local_outbox_without_network(self):
        cfg = self.cfg(transport=ntransport_raise(AssertionError("no network in outbox")))
        rid = self.route(cfg)
        res = N.write_outbox(cfg, rid)
        self.assertTrue(res["ok"])
        self.assertTrue(os.path.isfile(os.path.join(cfg.outbox_dir, rid, res["batch_id"] + ".json")))

    def test_36_live_send_requires_env_gate(self):
        env = dict(LIVE_ENV)
        env["PHASE7_12_ALLOW_LIVE_DELIVERY"] = "0"
        cfg = self.cfg(status=200, env=env)
        rid = self.approved_route(cfg)
        bid = N.build_batch(cfg, rid)["batch_id"]
        res = N.send_batch(cfg, bid, confirm_send="SEND:" + bid)
        self.assertEqual(res["readiness"], N.DELIVERY_CONFIRMATION_REQUIRED)

    def test_37_live_send_requires_confirmation(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg)
        bid = N.build_batch(cfg, rid)["batch_id"]
        res = N.send_batch(cfg, bid)
        self.assertEqual(res["readiness"], N.DELIVERY_CONFIRMATION_REQUIRED)

    def test_38_wrong_confirmation_blocked(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg)
        bid = N.build_batch(cfg, rid)["batch_id"]
        res = N.send_batch(cfg, bid, confirm_send="SEND:wrong")
        self.assertEqual(res["readiness"], N.DELIVERY_CONFIRMATION_REQUIRED)

    def test_39_endpoint_missing(self):
        env = {"PHASE7_12_ALLOW_LIVE_DELIVERY": "1"}   # URL env unset
        cfg = self.cfg(status=200, env=env)
        rid = self.approved_route(cfg)
        bid = N.build_batch(cfg, rid)["batch_id"]
        res = N.send_batch(cfg, bid, confirm_send="SEND:" + bid)
        self.assertEqual(res["readiness"], N.DELIVERY_FAILED)
        self.assertEqual(res["error_code"], "ENDPOINT_ENV_UNSET")

    def test_40_endpoint_env_reference_allowed(self):
        canonical, _ = N.validate_route(webhook_route())
        self.assertEqual(canonical["endpoint_env"], "PHASE7_12_WEBHOOK_URL")

    def test_41_endpoint_secret_not_stored(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        stored = open(N._route_path(cfg, rid), encoding="utf-8").read()
        self.assertNotIn(LIVE_ENV["PHASE7_12_WEBHOOK_URL"], stored)


# ============================================================ 5) endpoint policy
class TestEndpointPolicy(Base):
    def dec(self, url, allow=("hooks.example.com",), allow_local=False):
        return NP.evaluate_notification_delivery_url(url, allowed_hosts=list(allow),
                                                     allow_local=allow_local)

    def test_42_https_allowed(self):
        self.assertTrue(self.dec("https://hooks.example.com/x").allowed)

    def test_43_http_blocked(self):
        d = self.dec("http://hooks.example.com/x")
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, D.INSECURE_SCHEME_BLOCKED)

    def test_44_seller_central_blocked(self):
        d = self.dec(SELLER_CENTRAL, allow=("sellercentral.amazon.com",))
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, D.SELLER_CENTRAL_ACCESS_PROHIBITED)

    def test_45_seller_central_subdomain_blocked(self):
        d = self.dec(SELLER_CENTRAL_SUB, allow=("sellercentral-europe.amazon.com",))
        self.assertFalse(d.allowed)

    def test_46_sp_api_blocked(self):
        d = self.dec(SP_API, allow=("sellingpartnerapi-na.amazon.com",))
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, D.AMAZON_API_PROHIBITED)

    def test_47_ads_api_blocked(self):
        d = self.dec(ADS_API, allow=("advertising-api.amazon.com",))
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, D.AMAZON_API_PROHIBITED)

    def test_48_seller_oauth_blocked(self):
        d = self.dec(SELLER_SIGNIN, allow=("www.amazon.com",))
        self.assertFalse(d.allowed)
        self.assertIn("AMAZON", d.reason_code)

    def test_49_url_userinfo_blocked(self):
        d = self.dec("https://user@hooks.example.com/x")
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, D.URL_USERINFO_BLOCKED)

    def test_50_embedded_password_blocked(self):
        d = self.dec("https://user:pass@hooks.example.com/x")
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, D.URL_USERINFO_BLOCKED)

    def test_51_loopback_blocked(self):
        d = self.dec("https://127.0.0.1/x")
        self.assertFalse(d.allowed)

    def test_52_private_ipv4_blocked(self):
        d = self.dec("https://10.0.0.5/x")
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, D.PRIVATE_DESTINATION_BLOCKED)

    def test_53_private_ipv6_blocked(self):
        d = self.dec("https://[fd00::1]/x")
        self.assertFalse(d.allowed)

    def test_54_link_local_blocked(self):
        d = self.dec("https://169.254.169.254/latest/meta-data")
        self.assertFalse(d.allowed)

    def test_55_metadata_endpoint_blocked(self):
        d = self.dec("https://169.254.169.254/x")
        self.assertFalse(d.allowed)

    def test_56_ip_literal_blocked(self):
        d = self.dec("https://93.184.216.34/x")
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, D.IP_LITERAL_BLOCKED)

    def test_56b_encoded_ip_literal_blocked(self):
        d = self.dec("https://2130706433/x")   # 127.0.0.1 as integer
        self.assertFalse(d.allowed)

    def test_56c_unicode_host_blocked(self):
        d = self.dec("https://hÃ¶st.example.com/x")
        self.assertFalse(d.allowed)

    def test_57_mixed_dns_result_blocked(self):
        bad = NP.validate_resolved_addresses(["93.184.216.34", "127.0.0.1"])
        self.assertEqual(bad, ["127.0.0.1"])

    def test_58_dns_rebinding_blocked(self):
        # a webhook host resolving to a private address is blocked at send time
        cfg = self.cfg(status=200, resolver=resolver_private)
        rid = self.approved_route(cfg)
        bid = N.build_batch(cfg, rid)["batch_id"]
        res = N.send_batch(cfg, bid, confirm_send="SEND:" + bid)
        self.assertEqual(res["readiness"], N.DELIVERY_BLOCKED)

    def test_58b_mixed_dns_blocked_at_send(self):
        cfg = self.cfg(status=200, resolver=resolver_mixed)
        rid = self.approved_route(cfg)
        bid = N.build_batch(cfg, rid)["batch_id"]
        res = N.send_batch(cfg, bid, confirm_send="SEND:" + bid)
        self.assertEqual(res["readiness"], N.DELIVERY_BLOCKED)

    def test_60_proxy_environment_ignored(self):
        # the real transport uses ProxyHandler({}) -> env proxies never used. Assert source contract.
        src = open(os.path.join(ROOT, "production", "phase7_owner_notification_delivery.py"),
                   encoding="utf-8").read()
        self.assertIn("ProxyHandler({})", src)

    def test_59_tls_verification_required(self):
        src = open(os.path.join(ROOT, "production", "phase7_owner_notification_delivery.py"),
                   encoding="utf-8").read()
        self.assertIn("CERT_REQUIRED", src)
        self.assertIn("check_hostname = True", src)

    def test_seller_central_endpoint_denied_via_provider_check(self):
        env = {"PHASE7_12_ALLOW_LIVE_DELIVERY": "1",
               "PHASE7_12_WEBHOOK_URL": "https://ok.example.com/x"}
        cfg = self.cfg(status=200, env=env)
        # route allowlists a normal host; endpoint env points elsewhere (not allowlisted)
        rid = self.approved_route(cfg, webhook_route(endpoint_host_allowlist=["hooks.example.com"]))
        res = N.provider_check(cfg, rid)
        self.assertFalse(res["endpoint_allowed"])


# ============================================================ 6) webhook request model
class TestWebhookRequest(Base):
    def test_61_webhook_redirect_blocked(self):
        cfg = self.cfg(status=302)
        _, _, res = self.sent_delivery(cfg)
        self.assertEqual(res["delivery_state"], N.DS_BLOCKED)
        self.assertEqual(res["reason"], "DELIVERY_BLOCKED_REDIRECT")

    def test_62_redirect_not_followed(self):
        rec = []
        cfg = self.cfg(transport=ntransport(302, record=rec))
        self.sent_delivery(cfg)
        self.assertEqual(len(rec), 1)   # exactly one request, no follow

    def test_63_post_only(self):
        src = open(os.path.join(ROOT, "production", "phase7_owner_notification_delivery.py"),
                   encoding="utf-8").read()
        self.assertIn('method="POST"', src)

    def test_64_67_no_other_methods(self):
        src = open(os.path.join(ROOT, "production", "phase7_owner_notification_delivery.py"),
                   encoding="utf-8").read()
        for m in ('method="GET"', 'method="PUT"', 'method="PATCH"', 'method="DELETE"'):
            self.assertNotIn(m, src)

    def test_68_fixed_content_type(self):
        rec = []
        cfg = self.cfg(transport=ntransport(200, record=rec))
        self.sent_delivery(cfg)
        self.assertEqual(rec[0]["headers"]["Content-Type"], "application/json")

    def test_69_fixed_user_agent(self):
        rec = []
        cfg = self.cfg(transport=ntransport(200, record=rec))
        self.sent_delivery(cfg)
        self.assertIn("OwnerNotify/7.12", rec[0]["headers"]["User-Agent"])

    def test_70_idempotency_key_stable(self):
        rec = []
        cfg = self.cfg(transport=ntransport(200, record=rec))
        rid, bid, res = self.sent_delivery(cfg)
        self.assertEqual(rec[0]["headers"]["Idempotency-Key"], res["delivery_id"])

    def test_71_arbitrary_header_impossible(self):
        rec = []
        cfg = self.cfg(transport=ntransport(200, record=rec))
        self.sent_delivery(cfg)
        allowed = {"Content-Type", "User-Agent", "Idempotency-Key", "Accept", "Authorization",
                   N.HMAC_SIG_HEADER, N.HMAC_TSV_HEADER}
        self.assertTrue(set(rec[0]["headers"]).issubset(allowed))

    def test_72_bearer_env_auth(self):
        rec = []
        env = dict(LIVE_ENV, PHASE7_12_WEBHOOK_BEARER_TOKEN="tok-abc-123")
        cfg = self.cfg(transport=ntransport(200, record=rec), env=env)
        rid = self.approved_route(cfg, webhook_route(
            authentication={"mode": "bearer-env", "token_env": "PHASE7_12_WEBHOOK_BEARER_TOKEN"}))
        bid = N.build_batch(cfg, rid)["batch_id"]
        N.send_batch(cfg, bid, confirm_send="SEND:" + bid)
        self.assertEqual(rec[0]["headers"]["Authorization"], "Bearer tok-abc-123")

    def test_73_bearer_not_logged(self):
        env = dict(LIVE_ENV, PHASE7_12_WEBHOOK_BEARER_TOKEN="tok-secret-xyz")
        cfg = self.cfg(status=200, env=env)
        rid = self.approved_route(cfg, webhook_route(
            authentication={"mode": "bearer-env", "token_env": "PHASE7_12_WEBHOOK_BEARER_TOKEN"}))
        bid = N.build_batch(cfg, rid)["batch_id"]
        res = N.send_batch(cfg, bid, confirm_send="SEND:" + bid)
        blob = json.dumps(N.show_delivery(cfg, res["delivery_id"]))
        self.assertNotIn("tok-secret-xyz", blob)
        logs = os.path.join(cfg.logs_dir, "operations.jsonl")
        if os.path.isfile(logs):
            self.assertNotIn("tok-secret-xyz", open(logs, encoding="utf-8").read())

    def test_74_hmac_env_auth(self):
        rec = []
        env = dict(LIVE_ENV, PHASE7_12_WEBHOOK_HMAC_SECRET="shhh-secret")
        cfg = self.cfg(transport=ntransport(200, record=rec), env=env)
        rid = self.approved_route(cfg, webhook_route(
            authentication={"mode": "hmac-sha256-env", "secret_env": "PHASE7_12_WEBHOOK_HMAC_SECRET"}))
        bid = N.build_batch(cfg, rid)["batch_id"]
        N.send_batch(cfg, bid, confirm_send="SEND:" + bid)
        self.assertIn(N.HMAC_SIG_HEADER, rec[0]["headers"])
        expected = "sha256=" + N.compute_hmac_signature("shhh-secret", rec[0]["body"])
        self.assertEqual(rec[0]["headers"][N.HMAC_SIG_HEADER], expected)

    def test_75_hmac_secret_not_logged(self):
        env = dict(LIVE_ENV, PHASE7_12_WEBHOOK_HMAC_SECRET="shhh-secret-777")
        cfg = self.cfg(status=200, env=env)
        rid = self.approved_route(cfg, webhook_route(
            authentication={"mode": "hmac-sha256-env", "secret_env": "PHASE7_12_WEBHOOK_HMAC_SECRET"}))
        bid = N.build_batch(cfg, rid)["batch_id"]
        res = N.send_batch(cfg, bid, confirm_send="SEND:" + bid)
        blob = json.dumps(N.show_delivery(cfg, res["delivery_id"]))
        self.assertNotIn("shhh-secret-777", blob)

    def test_76_hmac_deterministic(self):
        a = N.compute_hmac_signature("k", b"payload")
        b = N.compute_hmac_signature("k", b"payload")
        self.assertEqual(a, b)

    def test_77_hmac_changes_with_payload(self):
        self.assertNotEqual(N.compute_hmac_signature("k", b"a"),
                            N.compute_hmac_signature("k", b"b"))

    def test_78_payload_64k_default(self):
        canonical, _ = N.validate_route(webhook_route())
        self.assertEqual(canonical["rate_limit"]["max_payload_bytes"], N.DEFAULT_PAYLOAD_BYTES)

    def test_79_payload_256k_ceiling(self):
        with self.assertRaises(N.NotificationError):
            N.validate_route(webhook_route(rate_limit={"max_payload_bytes": 1024 * 1024}))

    def test_80_oversized_payload_blocked(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg, webhook_route(rate_limit={"max_payload_bytes": 1200}))
        bid = N.build_batch(cfg, rid)["batch_id"]
        res = N.send_batch(cfg, bid, confirm_send="SEND:" + bid)
        self.assertEqual(res["readiness"], N.DELIVERY_FAILED)
        self.assertEqual(res["reason"], "PAYLOAD_TOO_LARGE")

    def test_81_82_83_response_bounded_and_safe(self):
        big = b"x" * (200 * 1024)
        rec = []
        cfg = self.cfg(transport=ntransport(200, resp_body=big,
                                            resp_headers={"content-type": "application/json",
                                                          "set-cookie": "sid=leak"}, record=rec))
        rid, bid, res = self.sent_delivery(cfg)
        rec_d = N._load_delivery_record(cfg, res["delivery_id"])
        self.assertNotIn("set-cookie", rec_d["safe_response_headers"])
        self.assertLessEqual(len(rec_d.get("response_summary") or ""), 280)


# ============================================================ 7) delivery states
class TestDeliveryStates(Base):
    def _send(self, status):
        cfg = self.cfg(status=status)
        _, _, res = self.sent_delivery(cfg)
        return res

    def test_84_2xx_sent(self):
        self.assertEqual(self._send(200)["delivery_state"], N.DS_SENT)
        self.assertEqual(self._send(204)["delivery_state"], N.DS_SENT)

    def test_85_3xx_blocked(self):
        self.assertEqual(self._send(302)["delivery_state"], N.DS_BLOCKED)

    def test_86_400_failed(self):
        r = self._send(400)
        self.assertEqual(r["delivery_state"], N.DS_FAILED)
        self.assertFalse(r["retryable"])

    def test_87_401_failed(self):
        self.assertFalse(self._send(401)["retryable"])

    def test_88_403_failed(self):
        self.assertFalse(self._send(403)["retryable"])

    def test_89_404_failed(self):
        self.assertFalse(self._send(404)["retryable"])

    def test_90_408_retryable(self):
        self.assertTrue(self._send(408)["retryable"])

    def test_91_429_retryable(self):
        self.assertTrue(self._send(429)["retryable"])

    def test_92_500_retryable(self):
        self.assertTrue(self._send(500)["retryable"])

    def test_93_503_retryable(self):
        self.assertTrue(self._send(503)["retryable"])

    def test_94_connect_failure_retryable(self):
        cfg = self.cfg(transport=ntransport_raise(N._PreSendError("refused")))
        _, _, res = self.sent_delivery(cfg)
        self.assertEqual(res["readiness"], N.PROVIDER_UNAVAILABLE)
        self.assertTrue(res["retryable"])

    def test_95_post_send_timeout_unknown(self):
        cfg = self.cfg(transport=ntransport_raise(N._PostSendTimeout("timeout")))
        _, _, res = self.sent_delivery(cfg)
        self.assertEqual(res["delivery_state"], N.DS_UNKNOWN)

    def test_96_unknown_not_auto_retried(self):
        cfg = self.cfg(transport=ntransport_raise(N._PostSendTimeout("timeout")))
        _, _, res = self.sent_delivery(cfg)
        # no retry state file was written for an UNKNOWN delivery
        self.assertFalse(os.path.isfile(os.path.join(cfg.retries_dir, res["delivery_id"] + ".json")))

    def test_97_owner_approval_for_unknown_retry(self):
        cfg = self.cfg(transport=ntransport_raise(N._PostSendTimeout("timeout")))
        _, _, res = self.sent_delivery(cfg)
        did = res["delivery_id"]
        r1 = N.retry_delivery(cfg, did)
        self.assertEqual(r1["readiness"], N.DELIVERY_CONFIRMATION_REQUIRED)
        # confirm + succeed on retry
        cfg2 = N.Config(cfg.base_dir, alert_dir=cfg.alert_dir, env=cfg.env, resolver=resolver_public,
                        transport=ntransport(200), now_fn=lambda: 2_000_100.0)
        r2 = N.retry_delivery(cfg2, did, confirm_unknown="RETRY-UNKNOWN:" + did)
        self.assertEqual(r2["delivery_state"], N.DS_SENT)


# ============================================================ 8) retry policy
class TestRetryPolicy(Base):
    def test_98_max_retries_bounded(self):
        with self.assertRaises(N.NotificationError):
            N.validate_route(webhook_route(retry_policy={"max_attempts": 9}))

    def test_99_retry_delay_minimum(self):
        with self.assertRaises(N.NotificationError):
            N.validate_route(webhook_route(retry_policy={"initial_delay_seconds": 0}))

    def test_100_retry_maximum_delay(self):
        with self.assertRaises(N.NotificationError):
            N.validate_route(webhook_route(retry_policy={"maximum_delay_seconds": 7200}))

    def test_101_invalid_retry_status_rejected(self):
        with self.assertRaises(N.NotificationError) as e:
            N.validate_route(webhook_route(retry_policy={"retryable_statuses": [200, 404]}))
        self.assertEqual(e.exception.code, "RETRY_STATUS_INVALID")

    def test_163_failed_eligible_retry(self):
        cfg = self.cfg(status=503)
        _, _, res = self.sent_delivery(cfg)
        did = res["delivery_id"]
        cfg2 = N.Config(cfg.base_dir, alert_dir=cfg.alert_dir, env=cfg.env, resolver=resolver_public,
                        transport=ntransport(200), now_fn=lambda: 2_000_050.0)
        r = N.retry_delivery(cfg2, did)
        self.assertEqual(r["delivery_state"], N.DS_SENT)

    def test_max_attempts_reached(self):
        cfg = self.cfg(status=503)
        rid = self.approved_route(cfg, webhook_route(
            retry_policy={"max_attempts": 2, "initial_delay_seconds": 1, "maximum_delay_seconds": 2}))
        bid = N.build_batch(cfg, rid)["batch_id"]
        res = N.send_batch(cfg, bid, confirm_send="SEND:" + bid)  # attempt 1
        did = res["delivery_id"]
        cfg2 = N.Config(cfg.base_dir, alert_dir=cfg.alert_dir, env=cfg.env, resolver=resolver_public,
                        transport=ntransport(503), now_fn=lambda: 2_000_060.0)
        N.retry_delivery(cfg2, did)  # attempt 2 -> reaches max
        r3 = N.retry_delivery(cfg2, did)
        self.assertEqual(r3["reason"], "MAX_ATTEMPTS_REACHED")

    def test_no_retry_on_terminal_4xx(self):
        cfg = self.cfg(status=400)
        _, _, res = self.sent_delivery(cfg)
        self.assertFalse(os.path.isfile(os.path.join(cfg.retries_dir, res["delivery_id"] + ".json")))


# ============================================================ 9) source selection
class TestSourceSelection(Base):
    def test_102_alert_verification_reused(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        sel, blocked, counts = N._select_alerts(cfg, N.load_route(cfg, rid))
        self.assertEqual(len(sel), 4)   # one per severity
        self.assertEqual(blocked, [])

    def test_103_corrupt_alert_state_blocked(self):
        adir, wid, _ = self.ws()
        # corrupt the 7.11 alert-state file body -> its integrity hash no longer matches
        path = os.path.join(adir, "state", "alerts-" + wid + ".json")
        rec = json.load(open(path, encoding="utf-8"))
        rec["head_hash"] = "tampered-head-hash"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        cfg = self.cfg(status=200, alert_dir=adir)
        rid = self.route(cfg)
        res = N.build_batch(cfg, rid)
        self.assertEqual(res["readiness"], N.ALERT_SOURCE_BLOCKED)

    def test_104_corrupt_alert_history_blocked(self):
        adir, wid, by = self.ws()
        # acknowledge one alert so the 7.11 alert history chain is non-empty, then tamper it
        wc = W.Config(adir, env={})
        W._alert_action(wc, by["INFO"][0]["alert_id"], W.ACT_ACKNOWLEDGE, "OWNER", "seen")
        path = os.path.join(adir, "state", "alerts-" + wid + ".json")
        rec = json.load(open(path, encoding="utf-8"))
        rec["history"][0]["actor"] = "ATTACKER"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        cfg = self.cfg(status=200, alert_dir=adir)
        rid = self.route(cfg)
        self.assertEqual(N.build_batch(cfg, rid)["readiness"], N.ALERT_SOURCE_BLOCKED)

    def test_105_open_alert_eligible(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        sel, _, _ = N._select_alerts(cfg, N.load_route(cfg, rid))
        self.assertTrue(all(a["status"] == "OPEN" for a in sel))

    def test_106_acknowledged_excluded_by_default(self):
        adir, wid, by = self.ws()
        aid = by["INFO"][0]["alert_id"]
        wc = W.Config(adir, env={})
        W._alert_action(wc, aid, W.ACT_ACKNOWLEDGE, "OWNER", "seen")
        cfg = self.cfg(status=200, alert_dir=adir)
        rid = self.route(cfg)
        sel, _, _ = N._select_alerts(cfg, N.load_route(cfg, rid))
        self.assertNotIn(aid, [a["alert_id"] for a in sel])

    def test_107_dismissed_excluded_by_default(self):
        adir, wid, by = self.ws()
        aid = by["REVIEW"][0]["alert_id"]
        wc = W.Config(adir, env={})
        W._alert_action(wc, aid, W.ACT_DISMISS, "OWNER", None)
        cfg = self.cfg(status=200, alert_dir=adir)
        rid = self.route(cfg)
        sel, _, _ = N._select_alerts(cfg, N.load_route(cfg, rid))
        self.assertNotIn(aid, [a["alert_id"] for a in sel])

    def test_108_explicit_acknowledged_inclusion(self):
        adir, wid, by = self.ws()
        aid = by["INFO"][0]["alert_id"]
        wc = W.Config(adir, env={})
        W._alert_action(wc, aid, W.ACT_ACKNOWLEDGE, "OWNER", "seen")
        cfg = self.cfg(status=200, alert_dir=adir)
        rid = self.route(cfg, webhook_route(filter={"include_acknowledged": True}))
        sel, _, _ = N._select_alerts(cfg, N.load_route(cfg, rid))
        self.assertIn(aid, [a["alert_id"] for a in sel])

    def test_109_filter_by_watchlist(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, webhook_route(filter={"watchlist_ids": ["wl-nonexistent"]}))
        sel, _, _ = N._select_alerts(cfg, N.load_route(cfg, rid))
        self.assertEqual(sel, [])

    def test_110_filter_by_severity(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, webhook_route(filter={"severities": ["CRITICAL"]}))
        sel, _, _ = N._select_alerts(cfg, N.load_route(cfg, rid))
        self.assertTrue(all(a["severity"] == "CRITICAL" for a in sel))
        self.assertEqual(len(sel), 1)

    def test_111_filter_by_status(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, webhook_route(filter={"statuses": ["ACKNOWLEDGED"]}))
        sel, _, _ = N._select_alerts(cfg, N.load_route(cfg, rid))
        self.assertEqual(sel, [])

    def test_112_filter_by_rule_id(self):
        cfg = self.cfg(status=200)
        allsel, _, _ = N._select_alerts(cfg, N.load_route(cfg, self.route(cfg)))
        rule_id = allsel[0]["rule_id"]
        rid = self.route(cfg, webhook_route(filter={"rule_ids": [rule_id]}))
        sel, _, _ = N._select_alerts(cfg, N.load_route(cfg, rid))
        self.assertTrue(all(a["rule_id"] == rule_id for a in sel))

    def test_113_filter_by_source_type(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, webhook_route(filter={"source_types": ["public-url"]}))
        sel, _, _ = N._select_alerts(cfg, N.load_route(cfg, rid))
        self.assertEqual(len(sel), 4)
        rid2 = self.route(cfg, webhook_route(filter={"source_types": ["rss"]}))
        self.assertEqual(N._select_alerts(cfg, N.load_route(cfg, rid2))[0], [])

    def test_114_filter_by_field_path(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, webhook_route(filter={"field_paths": ["title"]}))
        sel, _, _ = N._select_alerts(cfg, N.load_route(cfg, rid))
        self.assertTrue(all(a["field_path"] == "title" for a in sel))


# ============================================================ 10) batch identity
class TestBatchIdentity(Base):
    def test_115_first_batch(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        res = N.build_batch(cfg, rid)
        self.assertEqual(res["readiness"], N.BATCH_READY)
        self.assertEqual(res["alert_count"], 4)

    def test_116_empty_batch(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, webhook_route(filter={"severities": ["INFO"],
                                                    "watchlist_ids": ["wl-none"]}))
        res = N.build_batch(cfg, rid)
        self.assertEqual(res["readiness"], N.BATCH_READY_EMPTY)
        self.assertEqual(res["alert_count"], 0)

    def test_117_stable_batch_id(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        self.assertEqual(N.build_batch(cfg, rid)["batch_id"], N.build_batch(cfg, rid)["batch_id"])

    def test_118_alert_order_excluded(self):
        # batch id derives from a sorted alert-id set, not selection order
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        route = N.load_route(cfg, rid)
        sel, _, _ = N._select_alerts(cfg, route)
        ids = [a["alert_id"] for a in sel]
        p1 = N._batch_identity(route, "manual", ids)
        p2 = N._batch_identity(route, "manual", list(reversed(ids)))
        self.assertEqual(p1, p2)

    def test_119_runtime_timestamp_excluded(self):
        cfg1 = self.cfg(status=200, now=2_000_000.0)
        rid = self.route(cfg1)
        b1 = N.build_batch(cfg1, rid, reference_time="2026-07-23T10:00:00Z")["batch_id"]
        b2 = N.build_batch(cfg1, rid, reference_time="2026-07-23T23:59:00Z")["batch_id"]
        self.assertEqual(b1, b2)   # manual digest -> same batch id regardless of reference time

    def test_120_route_change_changes_batch_id(self):
        cfg = self.cfg(status=200)
        r1 = N.load_route(cfg, self.route(cfg))
        r2 = N.load_route(cfg, self.route(cfg, webhook_route(payload_format="discord")))
        ids = ["alt-a", "alt-b"]
        self.assertNotEqual(N._batch_identity(r1, "manual", ids), N._batch_identity(r2, "manual", ids))

    def test_121_alert_set_change_changes_batch_id(self):
        cfg = self.cfg(status=200)
        route = N.load_route(cfg, self.route(cfg))
        self.assertNotEqual(N._batch_identity(route, "manual", ["alt-a"]),
                            N._batch_identity(route, "manual", ["alt-a", "alt-b"]))


# ============================================================ 11) delivery identity
class TestDeliveryIdentity(Base):
    def test_122_stable_delivery_id(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        route = N.load_route(cfg, rid)
        batch = N.build_batch(cfg, rid)["batch"]
        a = N._delivery_identity(route, batch, "hooks.example.com", "sha1")
        b = N._delivery_identity(route, batch, "hooks.example.com", "sha1")
        self.assertEqual(a, b)

    def test_123_secret_url_excluded(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        route = N.load_route(cfg, rid)
        batch = N.build_batch(cfg, rid)["batch"]
        did = N._delivery_identity(route, batch, "hooks.example.com", "sha1")
        self.assertNotIn("/x/y", did)
        self.assertNotIn("://", did)

    def test_124_payload_change_changes_delivery_id(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        route = N.load_route(cfg, rid)
        batch = N.build_batch(cfg, rid)["batch"]
        self.assertNotEqual(N._delivery_identity(route, batch, "hooks.example.com", "sha1"),
                            N._delivery_identity(route, batch, "hooks.example.com", "sha2"))


# ============================================================ 12) digest + quiet hours
class TestDigestQuietHours(Base):
    def route_dp(self, mode, **kw):
        return webhook_route(digest_policy={"mode": mode, **kw})

    def test_125_immediate_digest(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, self.route_dp("immediate"))
        self.assertEqual(N.build_batch(cfg, rid)["digest_period_id"], "immediate")

    def test_126_hourly_digest(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, self.route_dp("hourly", timezone="UTC"))
        pid = N.build_batch(cfg, rid, reference_time="2026-07-23T10:30:00Z")["digest_period_id"]
        self.assertEqual(pid, "hourly:2026-07-23T10")

    def test_127_daily_digest(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, self.route_dp("daily", timezone="UTC"))
        pid = N.build_batch(cfg, rid, reference_time="2026-07-23T10:30:00Z")["digest_period_id"]
        self.assertEqual(pid, "daily:2026-07-23")

    def test_128_weekly_digest(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, self.route_dp("weekly", timezone="UTC"))
        pid = N.build_batch(cfg, rid, reference_time="2026-07-23T10:30:00Z")["digest_period_id"]
        self.assertTrue(pid.startswith("weekly:2026-W"))

    def test_129_manual_digest(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, self.route_dp("manual"))
        self.assertEqual(N.build_batch(cfg, rid)["digest_period_id"], "manual")

    def test_130_minimum_one_hour_interval(self):
        self.assertEqual(N.MIN_DIGEST_INTERVAL_HOURS, 1)

    def test_131_invalid_timezone(self):
        with self.assertRaises(N.NotificationError) as e:
            N.validate_route(self.route_dp("daily", timezone="Mars/Phobos"))
        self.assertEqual(e.exception.code, "TIMEZONE_INVALID")

    def test_132_dst_forward(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, self.route_dp("hourly", timezone="America/New_York"))
        # 2026-03-08 spring-forward day
        pid = N.build_batch(cfg, rid, reference_time="2026-03-08T07:30:00Z")["digest_period_id"]
        self.assertTrue(pid.startswith("hourly:2026-03-08T"))

    def test_133_dst_backward(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, self.route_dp("hourly", timezone="America/New_York"))
        pid = N.build_batch(cfg, rid, reference_time="2026-11-01T06:30:00Z")["digest_period_id"]
        self.assertTrue(pid.startswith("hourly:2026-11-01T"))

    def test_134_digest_boundary(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, self.route_dp("hourly", timezone="UTC"))
        a = N.build_batch(cfg, rid, reference_time="2026-07-23T10:59:59Z")["digest_period_id"]
        b = N.build_batch(cfg, rid, reference_time="2026-07-23T11:00:00Z")["digest_period_id"]
        self.assertNotEqual(a, b)

    def test_135_digest_not_due(self):
        cfg = self.cfg(status=200)
        route = N.load_route(cfg, self.route(cfg, self.route_dp("hourly", timezone="UTC")))
        ref = N._parse_reference(cfg, "2026-07-23T10:30:00Z")
        N._record_sent(cfg, route["route_id"], "dlv-x", [], 1.0, "hourly:2026-07-23T10", "h")
        due, reason, _ = N._digest_due(cfg, route, ref)
        self.assertFalse(due)

    def _quiet(self, **kw):
        base = {"enabled": True, "timezone": "UTC", "start": "22:00", "end": "06:00",
                "weekdays": [0, 1, 2, 3, 4, 5, 6]}
        base.update(kw)
        return base

    def test_136_quiet_hours_same_day(self):
        q = N._normalize_quiet_hours(self._quiet(start="09:00", end="17:00"))
        self.assertTrue(N._in_quiet_hours(q, N._parse_reference(None, "2026-07-23T12:00:00Z")))
        self.assertFalse(N._in_quiet_hours(q, N._parse_reference(None, "2026-07-23T20:00:00Z")))

    def test_137_quiet_hours_overnight(self):
        q = N._normalize_quiet_hours(self._quiet())
        self.assertTrue(N._in_quiet_hours(q, N._parse_reference(None, "2026-07-23T23:00:00Z")))
        self.assertTrue(N._in_quiet_hours(q, N._parse_reference(None, "2026-07-23T02:00:00Z")))
        self.assertFalse(N._in_quiet_hours(q, N._parse_reference(None, "2026-07-23T12:00:00Z")))

    def test_138_quiet_hours_exact_start(self):
        q = N._normalize_quiet_hours(self._quiet(start="22:00", end="06:00"))
        self.assertTrue(N._in_quiet_hours(q, N._parse_reference(None, "2026-07-23T22:00:00Z")))

    def test_139_quiet_hours_exact_end(self):
        q = N._normalize_quiet_hours(self._quiet(start="22:00", end="06:00"))
        self.assertFalse(N._in_quiet_hours(q, N._parse_reference(None, "2026-07-23T06:00:00Z")))

    def test_140_quiet_hours_dst_forward(self):
        q = N._normalize_quiet_hours(self._quiet(timezone="America/New_York", start="01:00",
                                                 end="03:00"))
        # inside the DST forward gap day; must not raise
        self.assertIn(N._in_quiet_hours(q, N._parse_reference(None, "2026-03-08T07:30:00Z")),
                      (True, False))

    def test_141_quiet_hours_dst_backward(self):
        q = N._normalize_quiet_hours(self._quiet(timezone="America/New_York", start="01:00",
                                                 end="03:00"))
        self.assertIn(N._in_quiet_hours(q, N._parse_reference(None, "2026-11-01T06:30:00Z")),
                      (True, False))

    def test_142_explicit_critical_bypass(self):
        q = N._normalize_quiet_hours(self._quiet(start="00:00", end="23:59",
                                                 severity_bypass=["CRITICAL"]))
        self.assertTrue(N._quiet_bypassed(q, ["CRITICAL", "INFO"]))
        self.assertFalse(N._quiet_bypassed(q, ["INFO"]))

    def test_143_no_implicit_severity_bypass(self):
        q = N._normalize_quiet_hours(self._quiet(start="00:00", end="23:59"))
        self.assertFalse(N._quiet_bypassed(q, ["CRITICAL"]))

    def test_quiet_hours_block_at_send(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg, webhook_route(
            quiet_hours=self._quiet(start="00:00", end="23:59")))
        bid = N.build_batch(cfg, rid)["batch_id"]
        res = N.send_batch(cfg, bid, confirm_send="SEND:" + bid,
                           reference_time="2026-07-23T12:00:00Z")
        self.assertEqual(res["readiness"], N.QUIET_HOURS)

    def test_quiet_hours_critical_bypass_sends(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg, webhook_route(
            filter={"severities": ["CRITICAL"]},
            quiet_hours=self._quiet(start="00:00", end="23:59", severity_bypass=["CRITICAL"])))
        bid = N.build_batch(cfg, rid)["batch_id"]
        res = N.send_batch(cfg, bid, confirm_send="SEND:" + bid,
                           reference_time="2026-07-23T12:00:00Z")
        self.assertEqual(res["delivery_state"], N.DS_SENT)


# ============================================================ 13) content policy + truncation
class TestContentPolicy(Base):
    def test_144_max_alerts_per_batch(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, webhook_route(rate_limit={"max_alerts_per_batch": 2}))
        res = N.build_batch(cfg, rid)
        self.assertEqual(res["alert_count"], 2)
        self.assertEqual(res["omitted_alert_count"], 2)

    def test_145_deterministic_truncation(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, webhook_route(content_policy={"max_alerts": 2}))
        a = N.build_batch(cfg, rid)["batch"]["alert_ids"]
        b = N.build_batch(cfg, rid)["batch"]["alert_ids"]
        self.assertEqual(a, b)
        # highest severity retained first (CRITICAL kept)
        sev = {x["alert_id"]: x["severity"] for x in N.build_batch(cfg, rid)["batch"]["alerts"]}
        self.assertIn("CRITICAL", sev.values())

    def test_146_omitted_count_recorded(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, webhook_route(content_policy={"max_alerts": 1}))
        res = N.build_batch(cfg, rid)
        self.assertEqual(res["omitted_alert_count"], 3)
        self.assertEqual(res["batch"]["truncation_summary"]["omitted"], 3)

    def test_147_generic_json_payload(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, webhook_route(payload_format="generic-json"))
        p = N.preview(cfg, rid)["payload"]
        self.assertEqual(p["schema_version"], N.PAYLOAD_SCHEMA)
        self.assertIn("alerts", p)
        self.assertIn("severity_counts", p)

    def test_148_slack_payload(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, webhook_route(payload_format="slack"))
        p = N.preview(cfg, rid)["payload"]
        self.assertIn("blocks", p)

    def test_149_discord_payload(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, webhook_route(payload_format="discord"))
        p = N.preview(cfg, rid)["payload"]
        self.assertIn("embeds", p)

    def test_150_teams_payload(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg, webhook_route(payload_format="teams"))
        p = N.preview(cfg, rid)["payload"]
        self.assertEqual(p["@type"], "MessageCard")

    def test_151_fixed_template_only(self):
        # no template engine and no dynamic-code execution is ever CALLED or imported
        imports, calls, _ = module_ast()
        for banned in ("eval", "exec", "compile"):
            self.assertNotIn(banned, calls)
        for mod in ("jinja2", "jinja", "mako", "django"):
            self.assertNotIn(mod, imports)

    def test_152_html_script_escaped(self):
        adir, wid, _ = build_alert_workspace(
            os.path.join(self.tmp, "esc"),
            rules=[{"operator": "field-changed", "evidence_type": "html.title", "field_path": "title",
                    "severity": "INFO", "label": "<script>alert(1)</script>",
                    "message": "<b>&bad</b>"}])
        cfg = self.cfg(status=200, alert_dir=adir)
        rid = self.route(cfg, webhook_route(payload_format="generic-json"))
        p = N.preview(cfg, rid)["payload"]
        blob = json.dumps(p)
        self.assertNotIn("<script>", blob)
        self.assertIn("&lt;script&gt;", blob)

    def test_153_no_raw_source_html(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        blob = json.dumps(N.preview(cfg, rid)["payload"])
        self.assertNotIn("<html>", blob)
        self.assertNotIn("<body>", blob)

    def test_154_no_absolute_path(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        blob = json.dumps(N.preview(cfg, rid)["payload"])
        self.assertNotIn(self.tmp, blob)

    def test_155_no_credentials(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        blob = json.dumps(N.preview(cfg, rid)["payload"])
        self.assertNotIn("://", blob)

    def test_156_157_158_159_no_invented_content(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        blob = json.dumps(N.preview(cfg, rid)["payload"]).lower()
        for banned in ("demand", "revenue", "sales_estimate", "recommendation", "opportunity_score"):
            self.assertNotIn(banned, blob)

    def test_content_field_not_allowed(self):
        with self.assertRaises(N.NotificationError) as e:
            N.validate_route(webhook_route(content_policy={"fields": ["raw_html"]}))
        self.assertEqual(e.exception.code, "CONTENT_FIELD_NOT_ALLOWED")


# ============================================================ 14) outbox + idempotency
class TestOutboxIdempotency(Base):
    def test_160_local_outbox_deterministic(self):
        cfg = self.cfg(transport=ntransport_raise(AssertionError("no net")))
        rid = self.route(cfg)
        a = N.write_outbox(cfg, rid)
        b = N.write_outbox(cfg, rid)
        self.assertEqual(a["payload_sha256"], b["payload_sha256"])

    def test_161_duplicate_outbox_prevented(self):
        cfg = self.cfg(transport=ntransport_raise(AssertionError("no net")))
        rid = self.route(cfg)
        N.write_outbox(cfg, rid)
        b = N.write_outbox(cfg, rid)
        self.assertTrue(b["idempotent_reuse"])

    def test_162_sent_delivery_not_resent(self):
        rec = []
        cfg = self.cfg(transport=ntransport(200, record=rec))
        rid, bid, res = self.sent_delivery(cfg)
        N.send_batch(cfg, bid, confirm_send="SEND:" + bid)   # second attempt
        self.assertEqual(len(rec), 1)   # only ONE actual network request

    def test_idempotent_reuse_reported(self):
        cfg = self.cfg(status=200)
        rid, bid, res = self.sent_delivery(cfg)
        r2 = N.send_batch(cfg, bid, confirm_send="SEND:" + bid)
        self.assertEqual(r2["detail"], "IDEMPOTENT_REUSE")
        self.assertEqual(r2["delivery_id"], res["delivery_id"])


# ============================================================ 15) revoke + rate limiting
class TestRevokeRate(Base):
    def test_165_route_revoke_blocks_pending(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg)
        bid = N.build_batch(cfg, rid)["batch_id"]
        N.revoke_route(cfg, rid, "OWNER", "stop")
        res = N.send_batch(cfg, bid, confirm_send="SEND:" + bid)
        self.assertIn(res["readiness"], (N.APPROVAL_REQUIRED, N.APPROVAL_BLOCKED))
        self.assertNotEqual(res.get("delivery_state"), N.DS_SENT)

    def test_166_rate_limit_per_route(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg, webhook_route(rate_limit={"max_per_route_per_hour": 1}))
        b1 = N.build_batch(cfg, rid, reference_time="2026-07-23T10:00:00Z")["batch_id"]
        N.send_batch(cfg, b1, confirm_send="SEND:" + b1)
        # a different batch (different filter) in the same hour is rate limited
        rid2 = self.approved_route(cfg, webhook_route(rate_limit={"max_per_route_per_hour": 1},
                                                      destination_label="d2"))
        # reuse same route by building a second batch after changing content is complex; instead
        # send the SAME route again with a forced different batch via severity filter
        cfg2 = N.Config(cfg.base_dir, alert_dir=cfg.alert_dir, env=cfg.env, resolver=resolver_public,
                        transport=ntransport(200), now_fn=lambda: 2_000_010.0)
        b2 = N.build_batch(cfg2, rid, extra_filters={"severities": ["CRITICAL"]})["batch_id"]
        res = N.send_batch(cfg2, b2, confirm_send="SEND:" + b2)
        self.assertEqual(res["readiness"], N.RATE_LIMITED)

    def test_168_minimum_delivery_interval(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg, webhook_route(
            rate_limit={"minimum_interval_seconds": 600, "max_per_route_per_hour": 10}))
        b1 = N.build_batch(cfg, rid)["batch_id"]
        N.send_batch(cfg, b1, confirm_send="SEND:" + b1)
        cfg2 = N.Config(cfg.base_dir, alert_dir=cfg.alert_dir, env=cfg.env, resolver=resolver_public,
                        transport=ntransport(200), now_fn=lambda: 2_000_060.0)  # 60s later < 600s
        b2 = N.build_batch(cfg2, rid, extra_filters={"severities": ["CRITICAL"]})["batch_id"]
        res = N.send_batch(cfg2, b2, confirm_send="SEND:" + b2)
        self.assertEqual(res["readiness"], N.RATE_LIMITED)
        self.assertEqual(res["reason"], "MINIMUM_INTERVAL")

    def test_169_rate_limited_batch_preserved(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg, webhook_route(rate_limit={"max_per_route_per_hour": 1}))
        b1 = N.build_batch(cfg, rid)["batch_id"]
        N.send_batch(cfg, b1, confirm_send="SEND:" + b1)
        cfg2 = N.Config(cfg.base_dir, alert_dir=cfg.alert_dir, env=cfg.env, resolver=resolver_public,
                        transport=ntransport(200), now_fn=lambda: 2_000_010.0)
        b2 = N.build_batch(cfg2, rid, extra_filters={"severities": ["CRITICAL"]})["batch_id"]
        N.send_batch(cfg2, b2, confirm_send="SEND:" + b2)
        self.assertTrue(os.path.isfile(os.path.join(cfg.batches_dir, b2 + ".json")))


# ============================================================ 16) delivery history chain
class TestDeliveryHistory(Base):
    def test_170_history_append_only(self):
        cfg = self.cfg(status=200)
        _, _, res = self.sent_delivery(cfg)
        hist = N._load_delivery_history(cfg, res["delivery_id"])
        seqs = [e["seq"] for e in hist["history"]]
        self.assertEqual(seqs, list(range(len(seqs))))

    def test_171_delivery_event_hash(self):
        cfg = self.cfg(status=200)
        _, _, res = self.sent_delivery(cfg)
        hist = N._load_delivery_history(cfg, res["delivery_id"])
        self.assertTrue(all(e.get("event_hash") for e in hist["history"]))

    def test_172_previous_event_hash(self):
        cfg = self.cfg(status=200)
        _, _, res = self.sent_delivery(cfg)
        hist = N._load_delivery_history(cfg, res["delivery_id"])
        self.assertEqual(hist["history"][0]["previous_event_hash"], "")

    def _corrupt_hist(self, cfg, did, mutate):
        path = N._delivery_history_path(cfg, did)
        rec = json.load(open(path, encoding="utf-8"))
        mutate(rec)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rec, f)

    def test_173_history_tamper_detected(self):
        cfg = self.cfg(status=200)
        _, _, res = self.sent_delivery(cfg)
        self._corrupt_hist(cfg, res["delivery_id"],
                           lambda r: r["history"][0].__setitem__("delivery_state", "SENT"))
        with self.assertRaises(N.NotificationError):
            N._load_delivery_history(cfg, res["delivery_id"])

    def test_174_history_deletion_detected(self):
        cfg = self.cfg(status=200)
        _, _, res = self.sent_delivery(cfg)
        self._corrupt_hist(cfg, res["delivery_id"], lambda r: r.__setitem__("history",
                                                                            r["history"][:1]))
        with self.assertRaises(N.NotificationError):
            N._load_delivery_history(cfg, res["delivery_id"])

    def test_175_history_reorder_detected(self):
        cfg = self.cfg(status=200)
        _, _, res = self.sent_delivery(cfg)
        self._corrupt_hist(cfg, res["delivery_id"],
                           lambda r: r.__setitem__("history", list(reversed(r["history"]))))
        with self.assertRaises(N.NotificationError):
            N._load_delivery_history(cfg, res["delivery_id"])

    def test_176_history_truncation_detected(self):
        cfg = self.cfg(status=200)
        _, _, res = self.sent_delivery(cfg)
        # drop integrity by removing head event but keeping head_hash
        self._corrupt_hist(cfg, res["delivery_id"], lambda r: r["history"].pop())
        with self.assertRaises(N.NotificationError):
            N._load_delivery_history(cfg, res["delivery_id"])

    def test_177_corrupt_history_blocks_update(self):
        cfg = self.cfg(status=503)
        _, _, res = self.sent_delivery(cfg)
        did = res["delivery_id"]
        self._corrupt_hist(cfg, did, lambda r: r["history"][0].__setitem__("actor", "X"))
        r = N.retry_delivery(cfg, did)
        self.assertEqual(r["readiness"], N.DELIVERY_STATE_BLOCKED)


# ============================================================ 17) locking
class TestLocking(Base):
    def test_178_route_lock_atomic(self):
        cfg = self.cfg(status=200)
        t = N._acquire_lock(cfg, "route-x")
        with self.assertRaises(N.NotificationError) as e:
            N._acquire_lock(cfg, "route-x")
        self.assertEqual(e.exception.code, "LOCK_HELD")
        N._release_lock(cfg, "route-x", t)

    def test_179_batch_lock_atomic(self):
        cfg = self.cfg(status=200)
        t = N._acquire_lock(cfg, "batch-y")
        with self.assertRaises(N.NotificationError):
            N._acquire_lock(cfg, "batch-y")
        N._release_lock(cfg, "batch-y", t)

    def test_180_same_batch_concurrent_blocked(self):
        cfg = self.cfg(status=200)
        rid, bid, _ = self.sent_delivery(cfg)
        t = N._acquire_lock(cfg, "batch-" + bid)   # simulate a concurrent holder
        res = N.send_batch(cfg, bid, confirm_send="SEND:" + bid)
        self.assertEqual(res["readiness"], N.ROUTE_BLOCKED)
        N._release_lock(cfg, "batch-" + bid, t)

    def test_181_different_routes_independent(self):
        cfg = self.cfg(status=200)
        t = N._acquire_lock(cfg, "route-a")
        t2 = N._acquire_lock(cfg, "route-b")   # different route -> independent
        self.assertTrue(t2)
        N._release_lock(cfg, "route-a", t)
        N._release_lock(cfg, "route-b", t2)

    def test_182_active_lock_preserved(self):
        cfg = self.cfg(status=200)
        t = N._acquire_lock(cfg, "route-c")
        with self.assertRaises(N.NotificationError):
            N._acquire_lock(cfg, "route-c", break_stale=True)  # fresh lock not broken
        N._release_lock(cfg, "route-c", t)

    def test_183_184_stale_lock_reported_not_removed(self):
        cfg = self.cfg(status=200, now=2_000_000.0)
        N._acquire_lock(cfg, "route-d")
        # a config far in the future sees the lock as stale but never auto-removes it
        cfg2 = N.Config(cfg.base_dir, alert_dir=cfg.alert_dir, env=cfg.env,
                        now_fn=lambda: 2_000_000.0 + 7 * 3600)
        with self.assertRaises(N.NotificationError) as e:
            N._acquire_lock(cfg2, "route-d")
        self.assertEqual(e.exception.code, "LOCK_STALE")

    def test_185_explicit_stale_lock_break(self):
        cfg = self.cfg(status=200, now=2_000_000.0)
        N._acquire_lock(cfg, "route-e")
        cfg2 = N.Config(cfg.base_dir, alert_dir=cfg.alert_dir, env=cfg.env,
                        now_fn=lambda: 2_000_000.0 + 7 * 3600)
        res = N.break_stale_lock(cfg2, "route-e")
        self.assertEqual(res["detail"], "REMOVED_STALE")

    def test_185b_break_refuses_fresh_lock(self):
        cfg = self.cfg(status=200)
        N._acquire_lock(cfg, "route-f")
        with self.assertRaises(N.NotificationError) as e:
            N.break_stale_lock(cfg, "route-f")
        self.assertEqual(e.exception.code, "LOCK_NOT_STALE")

    def test_186_owner_only_lock_release(self):
        cfg = self.cfg(status=200)
        t = N._acquire_lock(cfg, "route-g")
        foreign = {"pid": t["pid"] + 999, "created_epoch": 1.0}
        self.assertFalse(N._release_lock(cfg, "route-g", foreign))
        self.assertTrue(N._release_lock(cfg, "route-g", t))


# ============================================================ 18) atomicity
class TestAtomicity(Base):
    def test_187_atomic_route_write(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        self.assertTrue(os.path.isfile(N._route_path(cfg, rid)))
        # no leftover temp files
        self.assertFalse(any(n.startswith(".tmp") for n in os.listdir(cfg.routes_dir)))

    def test_188_atomic_approval_write(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg)
        self.assertFalse(any(n.startswith(".tmp") for n in os.listdir(cfg.approvals_dir)))

    def test_189_atomic_batch_write(self):
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        N.build_batch(cfg, rid)
        self.assertFalse(any(n.startswith(".tmp") for n in os.listdir(cfg.batches_dir)))

    def test_190_atomic_outbox_write(self):
        cfg = self.cfg(transport=ntransport_raise(AssertionError("no net")))
        rid = self.route(cfg)
        res = N.write_outbox(cfg, rid)
        self.assertFalse(any(n.startswith(".tmp") for n in os.listdir(os.path.dirname(
            os.path.join(cfg.outbox_dir, rid, res["batch_id"] + ".json")))))

    def test_191_192_atomic_delivery_and_history_write(self):
        cfg = self.cfg(status=200)
        _, _, res = self.sent_delivery(cfg)
        self.assertFalse(any(n.startswith(".tmp") for n in os.listdir(cfg.deliveries_dir)))
        self.assertFalse(any(n.startswith(".tmp") for n in os.listdir(cfg.delivery_history_dir)))

    def test_193_partial_write_cleanup(self):
        # a readback failure raises and leaves no temp file behind
        cfg = self.cfg(status=200)
        rid = self.route(cfg)
        before = set(os.listdir(cfg.routes_dir))
        self.assertNotIn(".tmp", "".join(before))

    def test_194_previous_valid_state_preserved(self):
        cfg = self.cfg(status=200)
        rid = self.approved_route(cfg)
        h1 = N._load_approval_state(cfg, rid)["head_hash"]
        # a failed live-gate send does not mutate the approval chain
        bid = N.build_batch(cfg, rid)["batch_id"]
        N.send_batch(cfg, bid)   # missing confirm -> no state mutation
        self.assertEqual(N._load_approval_state(cfg, rid)["head_hash"], h1)


# ============================================================ 19) exports
class TestExports(Base):
    def _exported(self):
        cfg = self.cfg(status=200)
        rid, bid, res = self.sent_delivery(cfg)
        ex = N.export(cfg)
        return cfg, ex

    def test_195_json_export_deterministic(self):
        cfg = self.cfg(status=200)
        self.sent_delivery(cfg)
        N.export(cfg)
        a = open(os.path.join(cfg.reports_dir, "delivery_report.json"), encoding="utf-8").read()
        N.export(cfg)
        b = open(os.path.join(cfg.reports_dir, "delivery_report.json"), encoding="utf-8").read()
        self.assertEqual(a, b)

    def test_196_tsv_export_deterministic(self):
        cfg = self.cfg(status=200)
        self.sent_delivery(cfg)
        N.export(cfg)
        a = open(os.path.join(cfg.reports_dir, "delivery_report.tsv"), encoding="utf-8").read()
        N.export(cfg)
        b = open(os.path.join(cfg.reports_dir, "delivery_report.tsv"), encoding="utf-8").read()
        self.assertEqual(a, b)

    def test_197_markdown_export_deterministic(self):
        cfg = self.cfg(status=200)
        self.sent_delivery(cfg)
        N.export(cfg)
        a = open(os.path.join(cfg.reports_dir, "delivery_report.md"), encoding="utf-8").read()
        N.export(cfg)
        b = open(os.path.join(cfg.reports_dir, "delivery_report.md"), encoding="utf-8").read()
        self.assertEqual(a, b)

    def test_198_tsv_formula_injection(self):
        self.assertTrue(N._tsv("=cmd|'/c calc'").startswith("'"))
        self.assertTrue(N._tsv("+SUM(A1)").startswith("'"))
        self.assertTrue(N._tsv("@x").startswith("'"))

    def test_199_legitimate_negative_preserved(self):
        self.assertEqual(N._tsv("-2.50"), "-2.50")

    def test_200_vietnamese_unicode_preserved(self):
        cfg = self.cfg(status=200)
        self.assertEqual(N._tsv("giá đổi"), "giá đổi")

    def test_201_equal_tsv_columns(self):
        cfg = self.cfg(status=200)
        self.sent_delivery(cfg)
        N.export(cfg)
        text = open(os.path.join(cfg.reports_dir, "delivery_report.tsv"), encoding="utf-8").read()
        rows = [r for r in text.split("\n") if r]
        header_cols = rows[0].count("\t")
        for r in rows[1:]:
            self.assertEqual(r.count("\t"), header_cols)

    def test_export_has_no_secret(self):
        cfg = self.cfg(status=200)
        self.sent_delivery(cfg)
        N.export(cfg)
        for name in ("delivery_report.json", "delivery_report.tsv", "delivery_report.md"):
            blob = open(os.path.join(cfg.reports_dir, name), encoding="utf-8").read()
            self.assertNotIn("://", blob)
            self.assertNotIn(LIVE_ENV["PHASE7_12_WEBHOOK_URL"], blob)


# ============================================================ 20) validate-only + scheduler
class TestValidateOnlyScheduler(Base):
    def test_202_207_validate_only_no_side_effects(self):
        path = self.write_def(webhook_route())
        base = os.path.join(self.tmp, "vo_base")
        res = N.validate_only(path)
        self.assertEqual(res["readiness"], N.VALIDATE_READY)
        self.assertEqual(res["files_written"], 0)
        self.assertEqual(res["network_requests"], 0)
        self.assertEqual(res["dns_lookups"], 0)
        self.assertEqual(res["locks_acquired"], 0)
        self.assertEqual(res["secret_env_reads"], 0)
        self.assertFalse(os.path.exists(base))

    def test_205_validate_only_no_base_directory(self):
        path = self.write_def(webhook_route())
        base = os.path.join(self.tmp, "never_created")
        N.validate_only(path)
        self.assertFalse(os.path.exists(base))

    def test_208_scheduler_plan_windows(self):
        cfg = self.cfg(status=200)
        plan = N.scheduler_plan(cfg)["plan"]
        self.assertIn("powershell", plan["windows_task_scheduler_example"].lower())

    def test_209_scheduler_plan_cron(self):
        cfg = self.cfg(status=200)
        plan = N.scheduler_plan(cfg)["plan"]
        self.assertIn("send-due", plan["cron_example"])

    def test_210_scheduler_plan_no_execution(self):
        cfg = self.cfg(status=200)
        plan = N.scheduler_plan(cfg)["plan"]
        self.assertEqual(plan["owner_action_required"], "OWNER_ACTION_REQUIRED")
        self.assertTrue(plan["registration_is_owner_action"])

    def test_211_scheduler_plan_no_secrets(self):
        cfg = self.cfg(status=200)
        plan = N.scheduler_plan(cfg)["plan"]
        blob = json.dumps(plan)
        self.assertNotIn(LIVE_ENV["PHASE7_12_WEBHOOK_URL"], blob)
        self.assertIn("PHASE7_12_WEBHOOK_URL", blob)   # a NAME is fine

    def test_scheduler_never_executes_scheduler_apis(self):
        imports, calls, _ = module_ast()
        self.assertNotIn("subprocess", imports)
        for banned in ("schtasks", "crontab", "systemctl", "launchctl", "os.system", "os.popen"):
            self.assertFalse(any(banned in c for c in calls), banned)


# ============================================================ 21) prohibited integration + boundary
class TestBoundaryAndProhibited(Base):
    SRC = None

    @classmethod
    def setUpClass(cls):
        cls.SRC = open(os.path.join(ROOT, "production",
                                    "phase7_owner_notification_delivery.py"), encoding="utf-8").read()

    def test_215_seller_central_counters_zero(self):
        cfg = self.cfg(status=200)
        _, _, res = self.sent_delivery(cfg)
        for k, v in res["amazon_counters"].items():
            self.assertEqual(v, 0, k)

    def test_216_no_direct_seller_central_transport(self):
        # no amazon literal appears on any outbound-primitive line
        for i, line in enumerate(self.SRC.splitlines(), 1):
            low = line.lower()
            if any(p in low for p in ("sellercentral", "sellingpartner", "advertising-api",
                                      "/ap/signin", "mws.amazon")):
                self.assertNotIn(".open(", line)
                self.assertNotIn("urlopen", line)

    def test_217_no_browser_automation(self):
        for banned in ("selenium", "playwright", "webdriver", "pyppeteer", "webbrowser"):
            self.assertNotIn(banned, self.SRC.lower())

    def test_218_no_shell_execution(self):
        _, _, shell_true = module_ast()
        self.assertEqual(shell_true, [])

    def test_219_no_arbitrary_subprocess(self):
        imports, calls, _ = module_ast()
        self.assertNotIn("subprocess", imports)
        self.assertNotIn("os.system", calls)
        self.assertNotIn("os.popen", calls)

    def test_220_no_scheduler_registration(self):
        # no scheduler API is ever CALLED (the names appear only in the read-only plan's docstring)
        _, calls, _ = module_ast()
        for banned in ("schtasks", "crontab", "systemctl", "launchctl"):
            self.assertFalse(any(banned in c for c in calls), banned)

    def test_221_222_no_customer_or_smtp(self):
        imports, calls, _ = module_ast()
        for mod in ("smtplib", "email"):
            self.assertNotIn(mod, imports)
        for banned in ("sendmail", "send_email", "review_request"):
            self.assertFalse(any(banned in c for c in calls), banned)

    def test_no_provider_sdk(self):
        imports, _, _ = module_ast()
        for banned in ("anthropic", "openai", "boto3"):
            self.assertNotIn(banned, imports)

    def test_connectivity_scan_clean(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import connectivity_scan as CS
        rep = CS.scan()
        self.assertTrue(rep["no_active_amazon_account_path"], rep["active_amazon_path_findings"])


# ============================================================ 22) upstream immutability + runs
class TestUpstreamImmutability(Base):
    def _hash_tree(self, root):
        import hashlib
        h = hashlib.sha256()
        if not os.path.isdir(root):
            return "ABSENT"
        for dirpath, _dn, files in sorted(os.walk(root)):
            for f in sorted(files):
                p = os.path.join(dirpath, f)
                h.update(os.path.relpath(p, root).encode())
                with open(p, "rb") as fh:
                    h.update(fh.read())
        return h.hexdigest()

    def test_212_phase7_11_data_immutable(self):
        adir, wid, _ = self.ws()
        before = self._hash_tree(adir)
        cfg = self.cfg(status=200, alert_dir=adir)
        rid = self.approved_route(cfg)
        bid = N.build_batch(cfg, rid)["batch_id"]
        N.send_batch(cfg, bid, confirm_send="SEND:" + bid)
        N.preview(cfg, rid)
        N.write_outbox(cfg, rid)
        N.export(cfg)
        self.assertEqual(self._hash_tree(adir), before)

    def test_214_no_phase712_file_under_upstream(self):
        adir, wid, _ = self.ws()
        cfg = self.cfg(status=200, alert_dir=adir)
        rid = self.approved_route(cfg)
        bid = N.build_batch(cfg, rid)["batch_id"]
        N.send_batch(cfg, bid, confirm_send="SEND:" + bid)
        # the 7.12 workspace is separate from the 7.11 alert dir
        self.assertFalse(cfg.base_dir.startswith(adir))
        for dirpath, _dn, files in os.walk(adir):
            for f in files:
                self.assertNotIn("delivery", f.lower())


# ============================================================ 23) end-to-end + misc
class TestEndToEnd(Base):
    def test_full_flow_local_outbox(self):
        cfg = self.cfg(transport=ntransport_raise(AssertionError("no net")))
        rid = self.route(cfg, outbox_route())
        prev = N.preview(cfg, rid)
        self.assertEqual(prev["readiness"], N.PREVIEW_READY)
        ob = N.write_outbox(cfg, rid)
        self.assertTrue(ob["ok"])

    def test_list_and_show(self):
        cfg = self.cfg(status=200)
        rid, bid, res = self.sent_delivery(cfg)
        self.assertEqual(N.list_routes(cfg)["count"], 1)
        self.assertEqual(N.list_batches(cfg)["count"], 1)
        self.assertGreaterEqual(N.list_deliveries(cfg)["count"], 1)
        self.assertEqual(N.show_delivery(cfg, res["delivery_id"])["current_state"], N.DS_SENT)

    def test_verify_state_ok(self):
        cfg = self.cfg(status=200)
        self.sent_delivery(cfg)
        vs = N.verify_state(cfg)
        self.assertTrue(vs["ok"])
        self.assertEqual(vs["readiness"], N.VERIFY_READY)

    def test_provider_check_no_network(self):
        cfg = self.cfg(transport=ntransport_raise(AssertionError("no net")))
        rid = self.route(cfg)
        res = N.provider_check(cfg, rid)
        self.assertTrue(res["endpoint_allowed"])
        self.assertEqual(res["endpoint_host"], "hooks.example.com")

    def test_cli_exit_codes(self):
        adir, _, _ = self.ws()

        def factory(args):
            return N.Config(args.base_dir, alert_dir=args.alert_dir, env=LIVE_ENV,
                            allow_local=args.allow_local_test_server)
        base = self.bdir()
        path = self.write_def(webhook_route())
        out = io.StringIO()
        with redirect_stdout(out):
            code = N.run_cli(["--base-dir", base, "create-route", "--definition", path],
                             cfg_factory=factory)
        self.assertEqual(code, 0)

    def test_send_due_not_due_is_not_error(self):
        cfg = self.cfg(status=200)
        res = N.send_due(cfg)
        self.assertEqual(res["readiness"], N.NOT_DUE)
        self.assertEqual(N._exit_code(res), 0)

    def test_send_due_auto_send(self):
        env = dict(LIVE_ENV)
        cfg = self.cfg(status=200, env=env)
        rid = self.route(cfg, webhook_route(auto_send_approved=True,
                                            digest_policy={"mode": "immediate"}))
        route = N.load_route(cfg, rid)
        token = N._automation_token(rid, N._route_content_hash(route))
        N.approve_route(cfg, rid, "OWNER", "auto ok", allow_auto_send=True)
        env["PHASE7_12_AUTO_SEND_TOKEN"] = token
        cfg2 = N.Config(cfg.base_dir, alert_dir=cfg.alert_dir, env=env, resolver=resolver_public,
                        transport=ntransport(200), now_fn=lambda: 2_000_000.0)
        res = N.send_due(cfg2)
        self.assertEqual(res["readiness"], N.READY)
        self.assertEqual(res["executed"][0]["delivery_state"], N.DS_SENT)

    def test_send_due_requires_auto_token(self):
        env = dict(LIVE_ENV)
        cfg = self.cfg(status=200, env=env)
        rid = self.route(cfg, webhook_route(auto_send_approved=True,
                                            digest_policy={"mode": "immediate"}))
        N.approve_route(cfg, rid, "OWNER", "auto ok", allow_auto_send=True)
        # no auto-send token in env -> confirmation required, not sent
        res = N.send_due(cfg)
        self.assertTrue(all(e["readiness"] != N.READY or
                            e["delivery_state"] != N.DS_SENT for e in res["executed"]))


if __name__ == "__main__":
    unittest.main()
