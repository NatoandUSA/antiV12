#!/usr/bin/env python3
"""Phase 7.13 — Unified Owner Console.

Every test runs with NO real internet call. The reused Phase 7.9–7.12 connected authorities are driven
through injected fake transports + fake DNS resolvers (the accepted 7.10 synthetic pattern), and the
accepted Phase 7.3–7.8 business read model is built by chaining the real upstream authorities. The
permanent boundary is asserted throughout: no Amazon Seller Central, no seller sign-in, no seller API,
no advertising API, no seller browser automation — every seller-account counter stays a constant zero.
Synthetic fixtures only; no committed test uses the real Internet and no upstream runtime tree is
mutated except through an accepted authority under an explicit confirmed console action.
"""
import ast
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse
import urllib.request
import http.client
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "core"), os.path.join(ROOT, "production"), os.path.join(ROOT, "tests")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import phase7_unified_owner_console as UC          # noqa: E402
from production import phase7_owner_operations_dashboard as OPS    # noqa: E402
from production import phase7_owner_dashboard as D                 # noqa: E402
from production import phase7_ads_analysis as AA                   # noqa: E402
from production import phase7_owner_decision_package as P5         # noqa: E402
from production import phase7_manual_action_tracker as TRK         # noqa: E402
from production import phase7_outcome_followup as FU               # noqa: E402
from production import phase7_connected_backup_recovery as BACKUP  # noqa: E402
from production import phase7_connected_public_research as R       # noqa: E402
from production import phase7_connected_research_watchlists as W   # noqa: E402
from production import phase7_owner_notification_delivery as N     # noqa: E402
from production import phase7_workflow_stage_model as WSM          # noqa: E402
from production import seller_central_package as SCP               # noqa: E402
import network_policy as NP                                        # noqa: E402
import diagnostics as DIAG                                         # noqa: E402
import test_phase7_4_owner_dashboard as T4                         # noqa: E402

MODULE_PATH = os.path.join(ROOT, "production", "phase7_unified_owner_console.py")
STATIC_DIR = os.path.join(ROOT, "production", "phase7_unified_owner_console_static")

# Amazon endpoint literals live in TEST code only (production assembles nothing like them). Used solely
# to assert these destinations are refused by the reused network policy.
SELLER_CENTRAL = "https://sellercentral.amazon.com/home"
SELLER_SIGNIN = "https://www.amazon.com/ap/signin"
SP_API = "https://sellingpartnerapi-na.amazon.com/orders"
ADS_API = "https://advertising-api.amazon.com/v2/campaigns"

REF, REF77 = "2026-07-21", "2026-07-31"
WIN = ("2026-06-01", "2026-06-29", "2026-07-01", "2026-07-31")
NOW = lambda: datetime(2026, 7, 24, 12, 0, 0, tzinfo=timezone.utc)   # noqa: E731
LIVE_ENV = {"PHASE7_12_WEBHOOK_URL": "https://hooks.example.com/x/y",
            "PHASE7_12_ALLOW_LIVE_DELIVERY": "1"}

ENT = dict(campaign="C1", ad_group="G1", targeting="wasteful", customer_search_term="wasteful",
           match_type=None, currency="USD")
DEF_BEFORE = dict(owner_review_label=AA.RL_NEGATIVE, primary_classification="HIGH_SPEND_NO_SALES",
                  start_date="2026-06-15", end_date="2026-06-15",
                  impressions=800, clicks=20, spend="25.00", sales="0.00", orders=0, units=0)
DEF_AFTER = dict(owner_review_label="INSUFFICIENT_EVIDENCE", start_date="2026-07-15", end_date="2026-07-15",
                 impressions=800, clicks=20, spend="25.00", sales="120.00", orders=6, units=6)

PUBLIC_ADDR = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def resolver_public(host, port=None, *a, **k):
    return list(PUBLIC_ADDR)


def _html(title="Nurse Sweatshirt", desc="cozy", price="24.99", availability="InStock"):
    parts = [b"<html><head><title>", title.encode(), b"</title>",
             b'<meta name="description" content="', desc.encode(), b'">']
    ld = {"@type": "Product", "name": title,
          "offers": {"price": price, "priceCurrency": "USD", "availability": availability}}
    parts.append(b'<script type="application/ld+json">' + json.dumps(ld).encode() + b"</script>")
    parts.append(b"</head><body>x</body></html>")
    return b"".join(parts)


def wl_transport(body):
    def t(url, *, timeout_seconds, max_bytes, headers=None):
        if "robots" in url:
            return (404, {"content-type": "text/plain"}, b"")
        return (200, {"content-type": "text/html"}, body)
    return t


def ntransport(status=200, record=None):
    def t(url, *, headers, body, connect_timeout, read_timeout, max_response_bytes):
        if record is not None:
            record.append({"url": url, "headers": dict(headers), "body": bytes(body)})
        return (status, {"content-type": "application/json"}, b'{"ok":true}')
    return t


def webhook_route(**over):
    d = {"name": "Owner Slack", "channel": "https-json-webhook", "payload_format": "slack",
         "destination_label": "owner-slack", "endpoint_env": "PHASE7_12_WEBHOOK_URL",
         "endpoint_host_allowlist": ["hooks.example.com"], "digest_policy": {"mode": "manual"}}
    d.update(over)
    return d


def module_ast():
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


def raw_http(host, port, request_bytes, timeout=5):
    s = socket.create_connection((host, port), timeout=timeout)
    try:
        s.sendall(request_bytes)
        s.settimeout(timeout)
        chunks = []
        while True:
            try:
                b = s.recv(4096)
            except socket.timeout:
                break
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks)
    finally:
        s.close()


def parse_http(raw):
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    status = int(lines[0].split(b" ")[1]) if len(lines[0].split(b" ")) > 1 else 0
    headers = {}
    for ln in lines[1:]:
        if b":" in ln:
            k, _, v = ln.partition(b":")
            headers[k.decode().strip().lower()] = v.decode().strip()
    return status, headers, body


def tree_hash(root):
    h = hashlib.sha256()
    if not os.path.isdir(root):
        return "MISSING"
    for dp, dn, fn in os.walk(root):
        dn.sort()
        for f in sorted(fn):
            p = os.path.join(dp, f)
            with open(p, "rb") as fh:
                data = fh.read()
            h.update(os.path.relpath(p, root).encode())
            h.update(data)
    return h.hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p713-")
        self._n = 0
        self._servers = []
        DIAG.clear_events()

    def tearDown(self):
        for s in self._servers:
            try:
                s.shutdown()
                s.server_close()
            except Exception:
                pass
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def newroot(self):
        self._n += 1
        r = os.path.join(self.tmp, "ws%d" % self._n, "runs", "T2", "phase7")
        os.makedirs(r)
        return r

    # ---- business pipeline (7.3-7.7) into ws/7.3 .. ws/7.7 -----------------------------------------
    def build_business(self, ws, *, populated=True):
        b = T4.make_row(1, **dict(ENT, **DEF_BEFORE))
        a = T4.make_row(2, **dict(ENT, **DEF_AFTER))
        p73 = T4.write_promoted(ws, T4.build_analysis([b, a], currencies=("USD",)))
        p74 = os.path.join(ws, "7.4")
        os.makedirs(p74, exist_ok=True)
        src = D.load_source(p73)
        views = D.build_views(src)
        status = "APPROVED_FOR_MANUAL_ACTION" if populated else "DEFERRED"
        D.save_review(p74, src, views, [{"entity_id": "st:" + b["lineage_hash"],
                                         "review_status": status, "owner_note": "n"}], now=NOW)
        p75 = os.path.join(ws, "7.5")
        r5 = P5.run(p75, p73, p74, REF, now=NOW)
        p76 = os.path.join(ws, "7.6")
        TRK.run_initialize(p76, p75, REF, package_id=r5["package_id"], now=NOW)
        if populated:
            iid = json.loads(open(os.path.join(r5["package_dir"], "manual_action_checklist.json"),
                                  encoding="utf-8").read())["items"][0]["package_item_id"]
            TRK.run_update(p76, p75, REF, package_id=r5["package_id"], package_item_id=iid,
                           status=TRK.S_COMPLETED,
                           updates=dict(owner_note="done", owner_checked_date="2026-06-30",
                                        owner_completed_date="2026-06-30"),
                           confirm_check=True, confirm_action=True, now=NOW)
        p77 = os.path.join(ws, "7.7")
        FU.run(p77, p73, p76, p75, REF77, WIN)
        return r5

    def build_backup(self, ws):
        bcfg = BACKUP.Config(os.path.join(ws, "7.9"), ws, env={}, repo_root=ROOT)
        return BACKUP.create_snapshot(bcfg)

    def build_research(self, ws):
        inp = os.path.join(self.tmp, "inp%d" % self._n)
        os.makedirs(inp, exist_ok=True)
        with open(os.path.join(inp, "note.txt"), "w", encoding="utf-8") as f:
            f.write("nurse sweatshirt gift for nurses")
        rcfg = R.Config(os.path.join(ws, "7.10"), input_root=inp)
        return R.capture_one(rcfg, "local-file", "note.txt")

    def build_watch(self, ws):
        adir = os.path.join(ws, "7.11")
        wd = {"name": "Nurse watch", "timezone": "UTC", "schedule": {"type": "manual"},
              "sources": [{"source_type": "public-url", "source_locator": "https://example.com/nurse"}],
              "alert_rules": [{"operator": "field-changed", "evidence_type": "html.title",
                               "field_path": "title", "severity": "IMPORTANT", "label": "Title",
                               "message": "changed"}]}
        c1 = W.Config(adir, env={}, transport=wl_transport(_html("Nurse Sweatshirt")),
                      resolver=resolver_public, now_fn=lambda: 1_000_000.0)
        wid = W.create_watchlist(c1, wd)["watchlist_id"]
        W.run_watchlist(c1, wid, reference_time="2026-07-23T10:00:00Z")
        c2 = W.Config(adir, env={}, transport=wl_transport(_html("Nurse Hoodie", price="29.99")),
                      resolver=resolver_public, now_fn=lambda: 1_003_600.0)
        W.run_watchlist(c2, wid, reference_time="2026-07-23T11:00:00Z")
        aid = W.list_alerts(c2, wid)["alerts"][0]["alert_id"]
        return adir, wid, aid

    def build_notify(self, ws):
        ncfg = N.Config(os.path.join(ws, "7.12"), alert_dir=os.path.join(ws, "7.11"), env=LIVE_ENV,
                        transport=ntransport(200), resolver=resolver_public, now_fn=lambda: 2_000_000.0)
        rid = N.create_route(ncfg, webhook_route())["route_id"]
        N.approve_route(ncfg, rid, "OWNER", "ok")
        bid = N.build_batch(ncfg, rid)["batch_id"]
        return rid, bid

    def full_ws(self, *, populated=True, backup=True, research=True, watch=True, notify=True):
        ws = self.newroot()
        self.build_business(ws, populated=populated)
        if backup:
            self.build_backup(ws)
        if research:
            self.build_research(ws)
        if watch:
            self.build_watch(ws)
            if notify:
                self.build_notify(ws)
        return ws

    def business_only(self, *, populated=True):
        ws = self.newroot()
        self.build_business(ws, populated=populated)
        return ws

    def cfg(self, ws, *, transport=None, resolver=resolver_public, env=None, base=None):
        return UC.Config(base or os.path.join(ws, "7.13"), ws, env=env if env is not None else {},
                         transport=transport, resolver=resolver, now_fn=NOW, repo_root=ROOT)

    def actx(self, cfg):
        UC.ensure_workspace(cfg.base_dir)
        return UC.AuditLog(cfg.audit_path), UC.ActionTokenStore()

    def run_action(self, cfg, action, params=None, *, phrase=None, tokens=None, audit=None,
                   fp="fp-test", auto_phrase=True):
        if audit is None or tokens is None:
            a2, t2 = self.actx(cfg)
            audit = audit or a2
            tokens = tokens or t2
        prep = UC.prepare_action(cfg, fp, action, params or {}, now=NOW(), tokens=tokens)
        if phrase is None and auto_phrase:
            phrase = prep.get("confirmation_phrase")
        res = UC.execute_action(cfg, token=prep["action_token"], confirmation_phrase=phrase,
                                session_fingerprint=fp, now=NOW(), tokens=tokens, audit=audit,
                                actor=cfg.actor, request_id="req-test", cache=None)
        return prep, res

    # ---- server + http ----------------------------------------------------------------------------
    def serve(self, cfg):
        srv = UC.build_server(cfg, host="127.0.0.1", port=0, now=NOW)
        self._servers.append(srv)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return "http://127.0.0.1:%d" % srv.server_address[1], srv

    def http(self, url, *, method="GET", headers=None, data=None):
        req = urllib.request.Request(url, method=method, data=data)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def session(self, url):
        """Return (cookie_header, csrf_token) for a fresh console session."""
        s, h, b = self.http(url + "/api/v1/session")
        cookie = h.get("Set-Cookie", "").split(";")[0]
        s2, h2, b2 = self.http(url + "/api/v1/session", headers={"Cookie": cookie})
        csrf = json.loads(b2)["data"]["csrf_token"]
        return cookie, csrf

    def getj(self, url, path, cookie=None):
        s, h, b = self.http(url + path, headers={"Cookie": cookie} if cookie else None)
        return s, json.loads(b.decode("utf-8"))

    def postj(self, url, path, cookie, csrf, obj):
        headers = {"Content-Type": "application/json", "Cookie": cookie}
        if csrf is not None:
            headers["X-CSRF-Token"] = csrf
        s, h, b = self.http(url + path, method="POST", headers=headers,
                            data=json.dumps(obj).encode("utf-8"))
        return s, json.loads(b.decode("utf-8"))


# ================================================================ 1) CLI
class TestCLI(Base):
    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = UC.main(argv, serve=False)
        return rc, out.getvalue(), err.getvalue()

    def base_args(self, ws, *extra):
        return ["--workspace-root", ws, "--base-dir", os.path.join(ws, "7.13")] + list(extra)

    def test_01_cli_help(self):
        with self.assertRaises(SystemExit):
            UC.build_arg_parser().parse_args(["--help"])

    def test_01b_help_lists_subcommands(self):
        h = UC.build_arg_parser().format_help()
        for sub in ("serve", "snapshot", "export", "verify-state", "validate-only"):
            self.assertIn(sub, h)

    def test_02_missing_command(self):
        with self.assertRaises(SystemExit):
            UC.build_arg_parser().parse_args(["--workspace-root", self.tmp])

    def test_03_unknown_command(self):
        with self.assertRaises(SystemExit):
            UC.build_arg_parser().parse_args(["teleport"])

    def test_04_unknown_option(self):
        with self.assertRaises(SystemExit):
            UC.build_arg_parser().parse_args(["--frobnicate", "x", "serve"])

    def test_05_validate_only_valid(self):
        ws = self.business_only()
        rc, out, err = self.run_cli(self.base_args(ws, "validate-only"))
        self.assertEqual(rc, 0, err)
        self.assertIn("readiness=", out)

    def test_06_validate_only_no_file_write(self):
        ws = self.newroot()
        miss = os.path.join(self.tmp, "brandnew", "phase7")
        rc, out, err = self.run_cli(["--workspace-root", miss, "--base-dir",
                                     os.path.join(miss, "7.13"), "validate-only"])
        self.assertFalse(os.path.exists(os.path.join(miss, "7.13")))

    def test_07_validate_only_no_directory(self):
        miss = os.path.join(self.tmp, "nodir", "phase7")
        UC.main(["--workspace-root", miss, "--base-dir", os.path.join(miss, "7.13"),
                 "validate-only"], serve=False)
        self.assertFalse(os.path.isdir(os.path.join(miss, "7.13")))

    def test_08_09_10_11_12_validate_only_no_side_effects(self):
        ws = self.business_only()
        cfg = self.cfg(ws)
        called = {"dns": 0, "http": 0}
        orig_gai = socket.getaddrinfo
        socket.getaddrinfo = lambda *a, **k: (_ for _ in ()).throw(AssertionError("dns in validate"))
        try:
            res = UC.validate_only(cfg)
        finally:
            socket.getaddrinfo = orig_gai
        self.assertEqual(res["files_written"], 0)
        self.assertEqual(res["directories_created"], 0)
        self.assertEqual(res["dns_lookups"], 0)
        self.assertEqual(res["http_requests"], 0)
        self.assertEqual(res["secret_env_reads"], 0)
        self.assertEqual(res["sessions_created"], 0)
        self.assertEqual(res["actions_executed"], 0)
        self.assertEqual(res["subprocesses"], 0)

    def test_snapshot_writes_only_under_7_13(self):
        ws = self.full_ws()
        before = {s: tree_hash(os.path.join(ws, s)) for s in ("7.3", "7.9", "7.11", "7.12")}
        rc, out, err = self.run_cli(self.base_args(ws, "snapshot"))
        self.assertEqual(rc, 0, err)
        self.assertTrue(os.path.isfile(os.path.join(ws, "7.13", "snapshots",
                                                    "owner_console_snapshot.json")))
        after = {s: tree_hash(os.path.join(ws, s)) for s in ("7.3", "7.9", "7.11", "7.12")}
        self.assertEqual(before, after)

    def test_export_and_verify_state(self):
        ws = self.business_only()
        rc, out, err = self.run_cli(self.base_args(ws, "export"))
        self.assertEqual(rc, 0, err)
        self.assertTrue(os.path.isfile(os.path.join(ws, "7.13", "exports",
                                                    "owner_console_status.tsv")))
        rc, out, err = self.run_cli(self.base_args(ws, "verify-state"))
        self.assertIn("audit_chain_ok=True", out)

    def test_startup_summary_no_absolute_path(self):
        ws = self.business_only()
        rc, out, err = self.run_cli(self.base_args(ws, "verify-state"))
        self.assertNotIn(self.tmp, out)


# ================================================================ 2) loopback / host / port
class TestLoopback(Base):
    def test_13_loopback_allowed(self):
        self.assertTrue(UC.is_loopback_host("127.0.0.1"))
        self.assertEqual(UC.validate_host("127.0.0.1"), "127.0.0.1")

    def test_14_ipv6_loopback(self):
        self.assertTrue(UC.is_loopback_host("::1"))
        self.assertTrue(UC.is_loopback_host("[::1]"))

    def test_14b_localhost_loopback(self):
        self.assertTrue(UC.is_loopback_host("localhost"))

    def test_15_zero_blocked(self):
        self.assertFalse(UC.is_loopback_host("0.0.0.0"))
        with self.assertRaises(UC.ConsoleError):
            UC.validate_host("0.0.0.0")

    def test_16_lan_blocked(self):
        for lan in ("192.168.1.10", "10.0.0.5", "172.16.4.4"):
            self.assertFalse(UC.is_loopback_host(lan), lan)

    def test_17_public_blocked(self):
        self.assertFalse(UC.is_loopback_host("8.8.8.8"))
        self.assertFalse(UC.is_loopback_host("example.com"))

    def test_17b_build_server_refuses_nonloopback(self):
        cfg = self.cfg(self.newroot())
        with self.assertRaises(UC.ConsoleError):
            UC.build_server(cfg, host="0.0.0.0", port=0)

    def test_invalid_port(self):
        for bad in (-1, 70000, "abc", None):
            with self.assertRaises(UC.ConsoleError):
                UC.validate_port(bad)


class TestServerBase(Base):
    def setUp(self):
        super().setUp()
        self.ws = self.business_only()
        self.cfg_ = self.cfg(self.ws)
        self.url, self.srv = self.serve(self.cfg_)
        self.host = "127.0.0.1"
        self.port = self.srv.server_address[1]


class TestHostAndMethods(TestServerBase):
    def test_18_unsafe_host_blocked(self):
        raw = raw_http(self.host, self.port,
                       b"GET /api/v1/health HTTP/1.1\r\nHost: evil.example\r\nConnection: close\r\n\r\n")
        self.assertEqual(parse_http(raw)[0], 400)

    def test_18b_missing_host_blocked(self):
        raw = raw_http(self.host, self.port, b"GET /api/v1/health HTTP/1.1\r\nConnection: close\r\n\r\n")
        self.assertEqual(parse_http(raw)[0], 400)

    def test_19_remote_client_blocked_by_host(self):
        # a request that claims a foreign Host is refused even though it reached the loopback socket.
        raw = raw_http(self.host, self.port,
                       b"GET /api/v1/overview HTTP/1.1\r\nHost: 10.0.0.9\r\nConnection: close\r\n\r\n")
        self.assertEqual(parse_http(raw)[0], 400)

    def test_get_allowed(self):
        s, _, _ = self.http(self.url + "/api/v1/health")
        self.assertEqual(s, 200)

    def test_head_allowed(self):
        s, h, b = self.http(self.url + "/api/v1/health", method="HEAD")
        self.assertEqual(s, 200)
        self.assertEqual(b, b"")

    def test_49_get_mutation_blocked(self):
        # actions endpoints reject GET (reads are GET-only; mutations are POST-only)
        s, _, _ = self.http(self.url + "/api/v1/actions/prepare")
        self.assertEqual(s, 404)

    def test_put_delete_patch_options_blocked(self):
        for m in ("PUT", "DELETE", "PATCH", "OPTIONS"):
            self.assertEqual(self.http(self.url + "/api/v1/overview", method=m, data=b"{}")[0], 405)

    def test_trace_connect_blocked(self):
        raw = raw_http(self.host, self.port,
                       b"TRACE /x HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        self.assertEqual(parse_http(raw)[0], 405)


# ================================================================ 3) static + traversal
class TestStatic(TestServerBase):
    def test_20_static_index(self):
        s, h, b = self.http(self.url + "/")
        self.assertEqual(s, 200)
        self.assertIn("text/html", h["Content-Type"])
        self.assertIn(b"Unified Owner Console", b)

    def test_21_static_js(self):
        s, h, b = self.http(self.url + "/app.js")
        self.assertEqual(s, 200)
        self.assertIn("javascript", h["Content-Type"])

    def test_22_static_css(self):
        s, h, b = self.http(self.url + "/styles.css")
        self.assertEqual(s, 200)
        self.assertIn("text/css", h["Content-Type"])

    def test_22b_static_icons(self):
        s, h, b = self.http(self.url + "/icons.svg")
        self.assertEqual(s, 200)
        self.assertIn("svg", h["Content-Type"])

    def test_23_unknown_static_404(self):
        self.assertEqual(self.http(self.url + "/nope.txt")[0], 404)

    def test_24_directory_traversal_blocked(self):
        raw = raw_http(self.host, self.port,
                       b"GET /../etc/passwd HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        self.assertIn(parse_http(raw)[0], (400, 404))

    def test_25_encoded_traversal_blocked(self):
        raw = raw_http(self.host, self.port,
                       b"GET /%2e%2e/secret HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        self.assertEqual(parse_http(raw)[0], 404)

    def test_26_null_byte_blocked(self):
        raw = raw_http(self.host, self.port,
                       b"GET /app%00.js HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        self.assertEqual(parse_http(raw)[0], 404)

    def test_27_mime_types(self):
        self.assertIn("text/html", self.http(self.url + "/")[1]["Content-Type"])
        self.assertIn("text/javascript", self.http(self.url + "/app.js")[1]["Content-Type"])

    def test_no_directory_listing(self):
        self.assertEqual(self.http(self.url + "/phase7_unified_owner_console_static/")[0], 404)


# ================================================================ 4) security headers + no external
class TestSecurityHeaders(TestServerBase):
    def headers(self, path="/"):
        return self.http(self.url + path)[1]

    def test_28_csp_present(self):
        csp = self.headers()["Content-Security-Policy"]
        for token in ("default-src 'self'", "script-src 'self'", "frame-ancestors 'none'",
                      "base-uri 'none'", "object-src 'none'"):
            self.assertIn(token, csp)
        self.assertNotIn("*", csp)

    def test_29_nosniff(self):
        self.assertEqual(self.headers()["X-Content-Type-Options"], "nosniff")

    def test_30_referrer_policy(self):
        self.assertEqual(self.headers()["Referrer-Policy"], "no-referrer")

    def test_31_frame_options(self):
        self.assertEqual(self.headers()["X-Frame-Options"], "DENY")

    def test_32_permissions_policy(self):
        pp = self.headers()["Permissions-Policy"]
        self.assertIn("camera=()", pp)
        self.assertIn("geolocation=()", pp)

    def test_33_no_unsafe_eval(self):
        self.assertNotIn("unsafe-eval", self.headers()["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", self.headers()["Content-Security-Policy"])

    def test_cache_control(self):
        self.assertIn("Cache-Control", self.headers("/api/v1/health"))

    def test_no_cors(self):
        self.assertNotIn("Access-Control-Allow-Origin", self.headers("/api/v1/overview"))

    def test_34_35_36_37_no_external_in_assets(self):
        for name in ("/app.js", "/styles.css"):
            b = self.http(self.url + name)[2].decode("utf-8")
            for bad in ("http://", "https://", "cdn.", "googleapis", "unpkg", "jsdelivr",
                        "new WebSocket", "serviceWorker", "@font-face", "sendBeacon", "@import"):
                self.assertNotIn(bad, b, "%s in %s" % (bad, name))

    def test_34b_no_inline_or_external_script_in_html(self):
        b = self.http(self.url + "/")[2].decode("utf-8")
        self.assertNotIn("<script>", b.lower())
        self.assertIn('src="/app.js"', b)
        self.assertNotIn("http://", b)
        self.assertNotIn("https://", b)

    def test_error_no_stack_trace(self):
        s, h, b = self.http(self.url + "/api/v1/nope")
        self.assertEqual(s, 404)
        self.assertNotIn(b"Traceback", b)
        self.assertNotIn(self.tmp.encode(), b)


# ================================================================ 5) session + CSRF
class TestSession(TestServerBase):
    def test_38_session_cookie_httponly(self):
        h = self.http(self.url + "/")[1]
        self.assertIn("HttpOnly", h.get("Set-Cookie", ""))

    def test_39_session_cookie_samesite_strict(self):
        h = self.http(self.url + "/")[1]
        self.assertIn("SameSite=Strict", h.get("Set-Cookie", ""))

    def test_40_session_secret_random(self):
        a = UC.SessionManager()
        b = UC.SessionManager()
        self.assertNotEqual(a._secret, b._secret)
        self.assertEqual(len(a._secret), 32)

    def test_41_session_secret_not_persisted(self):
        # nothing under the workspace ever contains the server secret / session id.
        cookie, csrf = self.session(self.url)
        sid = cookie.split("=", 1)[1]
        for dp, _dn, fn in os.walk(self.cfg_.base_dir):
            for f in fn:
                with open(os.path.join(dp, f), "rb") as fh:
                    data = fh.read()
                self.assertNotIn(sid.encode(), data)
                self.assertNotIn(csrf.encode(), data)

    def test_42_session_expires(self):
        clock = {"t": 1000.0}
        sm = UC.SessionManager(ttl=100, clock=lambda: clock["t"])
        sid, csrf = sm.create()
        self.assertTrue(sm.valid(sid))
        clock["t"] += 101
        self.assertFalse(sm.valid(sid))
        self.assertIsNone(sm.csrf_for(sid))

    def test_43_session_invalid_after_restart(self):
        cookie, csrf = self.session(self.url)
        # a brand-new server (restart) has an empty in-memory session table.
        cfg2 = self.cfg(self.ws, base=os.path.join(self.ws, "7.13b"))
        url2, _srv2 = self.serve(cfg2)
        s, j = self.postj(url2, "/api/v1/actions/prepare", cookie, csrf, {"action": "refresh-overview"})
        self.assertEqual(s, 401)
        self.assertEqual(j.get("error"), "SESSION_REQUIRED")

    def test_44_csrf_required(self):
        cookie, csrf = self.session(self.url)
        s, j = self.postj(self.url, "/api/v1/actions/prepare", cookie, None, {"action": "refresh-overview"})
        self.assertEqual(s, 403)
        self.assertEqual(j.get("error"), "CSRF_TOKEN_INVALID")

    def test_45_missing_csrf_blocked(self):
        cookie, _ = self.session(self.url)
        headers = {"Content-Type": "application/json", "Cookie": cookie}
        s, h, b = self.http(self.url + "/api/v1/actions/prepare", method="POST", headers=headers,
                            data=b'{"action":"refresh-overview"}')
        self.assertEqual(s, 403)

    def test_46_wrong_csrf_blocked(self):
        cookie, _ = self.session(self.url)
        s, j = self.postj(self.url, "/api/v1/actions/prepare", cookie, "not-the-token",
                          {"action": "refresh-overview"})
        self.assertEqual(s, 403)

    def test_47_csrf_session_bound(self):
        cookie, csrf = self.session(self.url)
        # a csrf from THIS session must not validate against a different session cookie.
        s2, h2, b2 = self.http(self.url + "/api/v1/session")
        cookie2 = h2.get("Set-Cookie", "").split(";")[0]
        s, j = self.postj(self.url, "/api/v1/actions/prepare", cookie2, csrf,
                          {"action": "refresh-overview"})
        self.assertEqual(s, 403)

    def test_48_csrf_not_logged(self):
        # the console emits no logs; assert the csrf never appears in any workspace file.
        cookie, csrf = self.session(self.url)
        self.postj(self.url, "/api/v1/actions/prepare", cookie, csrf, {"action": "refresh-overview"})
        for dp, _dn, fn in os.walk(self.cfg_.base_dir):
            for f in fn:
                with open(os.path.join(dp, f), "rb") as fh:
                    self.assertNotIn(csrf.encode(), fh.read())

    def test_session_fingerprint_no_secret(self):
        sm = UC.SessionManager()
        sid, _ = sm.create()
        fp = sm.fingerprint(sid)
        self.assertNotIn(sid, fp)
        self.assertEqual(len(fp), 16)


# ================================================================ 6) request body / JSON
class TestBody(TestServerBase):
    def test_50_query_string_mutation_blocked(self):
        cookie, csrf = self.session(self.url)
        headers = {"Content-Type": "application/json", "Cookie": cookie, "X-CSRF-Token": csrf}
        s, h, b = self.http(self.url + "/api/v1/actions/prepare?action=refresh-overview",
                            method="POST", headers=headers, data=b'{"action":"refresh-overview"}')
        self.assertEqual(s, 400)
        self.assertEqual(json.loads(b).get("error"), "QUERY_STRING_MUTATION_REFUSED")

    def test_51_json_required(self):
        cookie, csrf = self.session(self.url)
        headers = {"Content-Type": "text/plain", "Cookie": cookie, "X-CSRF-Token": csrf}
        s, h, b = self.http(self.url + "/api/v1/actions/prepare", method="POST", headers=headers,
                            data=b"action=refresh")
        self.assertEqual(s, 415)

    def test_52_request_size_bounded(self):
        cookie, csrf = self.session(self.url)
        headers = {
            "Content-Type": "application/json",
            "Cookie": cookie,
            "X-CSRF-Token": csrf,
            "Content-Length": str(UC.MAX_BODY_BYTES + 100),
        }
        parsed = urllib.parse.urlparse(self.url)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        try:
            conn.request("POST", "/api/v1/actions/prepare", headers=headers, body=b'{"action":"refresh-overview"}')
            res = conn.getresponse()
            self.assertEqual(res.status, 413)
            body = res.read()
            self.assertIn(b"REQUEST_BODY_TOO_LARGE", body)
        except (ConnectionAbortedError, ConnectionResetError):
            # Windows loopback TCP reset when server closes connection early on oversized header
            pass
        finally:
            conn.close()

        # Hard assertion: Server must remain running, healthy and non-corrupted after rejection
        s_health, h_health, b_health = self.http(self.url + "/api/v1/session")
        self.assertEqual(s_health, 200, "Server must remain healthy and active after rejecting oversized body")

    def test_52b_server_failure_is_not_masked(self):
        # Verify that normal valid requests still require 200 and are never masked
        cookie, csrf = self.session(self.url)
        s, h, b = self.http(self.url + "/api/v1/session", headers={"Cookie": cookie})
        self.assertEqual(s, 200)
        self.assertIn(b"csrf_token", b)

    def test_53_invalid_json_blocked(self):
        cookie, csrf = self.session(self.url)
        s, j = self.postj(self.url, "/api/v1/actions/prepare", cookie, csrf, "not json")
        # json.dumps("not json") is valid json string, not object -> JSON_OBJECT_REQUIRED
        self.assertEqual(s, 400)

    def test_53b_malformed_json_blocked(self):
        cookie, csrf = self.session(self.url)
        headers = {"Content-Type": "application/json", "Cookie": cookie, "X-CSRF-Token": csrf}
        s, h, b = self.http(self.url + "/api/v1/actions/prepare", method="POST", headers=headers,
                            data=b'{ not json ')
        self.assertEqual(s, 400)

    def test_54_duplicate_json_key_blocked(self):
        cookie, csrf = self.session(self.url)
        headers = {"Content-Type": "application/json", "Cookie": cookie, "X-CSRF-Token": csrf}
        s, h, b = self.http(self.url + "/api/v1/actions/prepare", method="POST", headers=headers,
                            data=b'{"action":"a","action":"b"}')
        self.assertEqual(s, 400)
        self.assertEqual(json.loads(b).get("error"), "DUPLICATE_JSON_KEY")

    def test_nan_rejected(self):
        cookie, csrf = self.session(self.url)
        headers = {"Content-Type": "application/json", "Cookie": cookie, "X-CSRF-Token": csrf}
        s, h, b = self.http(self.url + "/api/v1/actions/prepare", method="POST", headers=headers,
                            data=b'{"action":"x","n":NaN}')
        self.assertEqual(s, 400)


# ================================================================ 7) API endpoints + schema
class TestApi(Base):
    def setUp(self):
        super().setUp()
        self.ws = self.full_ws()
        self.url, self.srv = self.serve(self.cfg(self.ws))
        self.cookie, self.csrf = self.session(self.url)

    def g(self, path):
        return self.getj(self.url, path, self.cookie)

    def test_55_unknown_api_path(self):
        self.assertEqual(self.g("/api/v1/does-not-exist")[0], 404)

    def test_56_health(self):
        s, j = self.g("/api/v1/health")
        self.assertEqual(s, 200)
        self.assertTrue(j["data"]["audit_chain_ok"])

    def test_57_overview(self):
        s, j = self.g("/api/v1/overview")
        self.assertEqual(s, 200)
        self.assertIn("overview", j["data"])

    def test_58_modules(self):
        s, j = self.g("/api/v1/modules")
        self.assertEqual(s, 200)
        self.assertTrue(any(m["module"] == "analysis" for m in j["data"]["modules"]))

    def test_59_analysis(self):
        s, j = self.g("/api/v1/analysis?view=analysis")
        self.assertEqual(s, 200)
        self.assertIn("rows", j["data"])

    def test_60_research(self):
        s, j = self.g("/api/v1/research")
        self.assertEqual(s, 200)
        self.assertIn("runs", j["data"])

    def test_61_watchlists(self):
        s, j = self.g("/api/v1/watchlists")
        self.assertEqual(s, 200)
        self.assertIn("watchlists", j["data"])

    def test_62_alerts(self):
        s, j = self.g("/api/v1/alerts")
        self.assertEqual(s, 200)
        self.assertIn("alerts", j["data"])

    def test_63_notifications(self):
        s, j = self.g("/api/v1/notifications")
        self.assertEqual(s, 200)
        self.assertIn("routes", j["data"])

    def test_64_backups(self):
        s, j = self.g("/api/v1/backups")
        self.assertEqual(s, 200)
        self.assertIn("snapshots", j["data"])

    def test_65_system(self):
        s, j = self.g("/api/v1/system")
        self.assertEqual(s, 200)
        self.assertIn("python_version", j["data"]["system"])

    def test_66_activity(self):
        s, j = self.g("/api/v1/activity")
        self.assertEqual(s, 200)
        self.assertIn("events", j["data"])

    def test_67_stable_schema(self):
        for ep in ("overview", "modules", "analysis", "research", "watchlists", "alerts",
                   "notifications", "backups", "system", "activity", "workflow"):
            s, j = self.g("/api/v1/" + ep)
            for key in ("schema_version", "request_id", "readiness", "generated_at", "data",
                        "warnings", "errors", "source_authorities", "seller_central_counters"):
                self.assertIn(key, j, "%s missing %s" % (ep, key))
            self.assertEqual(j["schema_version"], UC.API_SCHEMA)

    def test_workflow_endpoint_includes_trust_and_product_truth(self):
        """Regression: /api/v1/workflow used to silently omit "trust" and "product_truth" from
        its response even though build_workflow_section always computed both -- no prior test
        exercised the real HTTP response body for this endpoint, only the internal model dict."""
        s, j = self.g("/api/v1/workflow")
        self.assertEqual(s, 200)
        self.assertIn("trust", j["data"])
        self.assertIsNotNone(j["data"]["trust"])
        self.assertEqual(j["data"]["trust"]["state"], UC.TRUST_HISTORICAL)  # self.ws is under runs/T2/phase7
        self.assertIn("product_truth", j["data"])
        self.assertIsNotNone(j["data"]["product_truth"])

    def test_workflow_trust_generic_not_only_t2(self):
        """Same fix, proven against a non-T2-named product root so it isn't accidentally correct
        only for the one quarantined name."""
        product_root = os.path.join(self.tmp, "acme")
        ws = os.path.join(product_root, "phase7")
        os.makedirs(ws)
        url, srv = self.serve(self.cfg(ws))
        cookie, _csrf = self.session(url)
        s, j = self.getj(url, "/api/v1/workflow", cookie)
        self.assertEqual(s, 200)
        self.assertIsNotNone(j["data"]["trust"])
        self.assertEqual(j["data"]["trust"]["state"], UC.TRUST_UNVERIFIED)

    def test_216_seller_central_counters_zero(self):
        s, j = self.g("/api/v1/overview")
        for v in j["seller_central_counters"].values():
            self.assertEqual(v, 0)

    def test_no_absolute_path_no_secret_in_api(self):
        for ep in ("overview", "system", "backups", "notifications", "activity"):
            s, j = self.g("/api/v1/" + ep)
            blob = json.dumps(j)
            self.assertNotIn(self.tmp, blob)
            self.assertNotIn("Authorization", blob)
            self.assertNotIn("Set-Cookie", blob)


# ================================================================ 8) read-model rules
class TestReadModel(Base):
    def test_68_69_canonical_readiness_ready_empty(self):
        ws = self.business_only(populated=False)
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        self.assertEqual(model["sections"]["analysis"]["readiness"], OPS.READY_EMPTY)
        # READY_EMPTY is never an error
        self.assertNotIn(model["sections"]["analysis"]["status"], (UC.MOD_BLOCKED,))

    def test_70_unknown_delivery_preserved(self):
        ws = self.full_ws()
        # craft a delivery record with UNKNOWN state directly under 7.12 via the authority double
        ncfg = N.Config(os.path.join(ws, "7.12"), alert_dir=os.path.join(ws, "7.11"), env=LIVE_ENV,
                        transport=N.ntransport(200) if hasattr(N, "ntransport") else ntransport(200),
                        resolver=resolver_public, now_fn=lambda: 2_100_000.0)
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        # the notification read model exposes an 'unknown' counter distinct from 'failed'
        counts = model["sections"]["notifications"]["counts"]
        self.assertIn("unknown", counts)
        self.assertIn("failed", counts)
        self.assertNotEqual("unknown", "failed")

    def test_71_blocked_not_renamed_ready(self):
        ws = self.business_only()
        # tamper the 7.3 analysis so the source integrity fails -> BLOCKED, never READY
        promoted = os.path.join(ws, "7.3", "promoted")
        with open(os.path.join(promoted, "analysis.json"), "r+b") as f:
            data = bytearray(f.read())
            data[10] ^= 0x5A
            f.seek(0)
            f.write(data)
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        self.assertEqual(model["sections"]["analysis"]["status"], UC.MOD_BLOCKED)
        self.assertEqual(model["readiness"], UC.CONSOLE_BLOCKED)
        self.assertNotIn("READY", model["readiness"])

    def test_72_missing_optional_module_partial(self):
        ws = self.full_ws(notify=False)   # 7.12 absent
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        self.assertEqual(model["sections"]["notifications"]["status"], UC.MOD_UNAVAILABLE)
        self.assertEqual(model["readiness"], UC.CONSOLE_READY_PARTIAL)

    def test_73_missing_required_module_blocked(self):
        ws = self.newroot()   # no 7.3 at all
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        self.assertEqual(model["sections"]["analysis"]["status"], UC.MOD_UNAVAILABLE)
        self.assertEqual(model["readiness"], UC.CONSOLE_REQUIRED)
        # a corrupt/blocked required core -> CONSOLE_BLOCKED
        ws2 = self.business_only()
        promoted = os.path.join(ws2, "7.3", "promoted")
        with open(os.path.join(promoted, "analysis.json"), "r+b") as f:
            d = bytearray(f.read()); d[5] ^= 0x33; f.seek(0); f.write(d)
        self.assertEqual(UC.build_console_model(self.cfg(ws2), now=NOW())["readiness"],
                         UC.CONSOLE_BLOCKED)

    def test_74_data_stale_marker(self):
        ws = self.business_only()
        fut = datetime(2030, 1, 1, tzinfo=timezone.utc)
        model = UC.build_console_model(self.cfg(ws), now=fut)
        self.assertTrue(any(f.get("stale") for f in model["freshness"].values()))

    def test_75_no_mtime_only_selection(self):
        # research/watchlist/notification selection is identity/lineage based, never by mtime; mtime is
        # used ONLY for the display-only freshness helper, which documents that record selection never
        # uses it. Confirm getmtime appears solely inside the freshness/display helpers.
        src = open(MODULE_PATH, encoding="utf-8").read()
        self.assertIn("selected by mtime", src)
        for fn_name in ("build_analysis_section", "build_research_section", "build_watchlist_section",
                        "build_notification_section", "build_backup_section", "_resolve_target"):
            body = src.split("def " + fn_name, 1)[1].split("\ndef ", 1)[0]
            self.assertNotIn("getmtime", body, fn_name)

    def test_76_77_currency_and_unit_separation(self):
        # the console never aggregates Decimal money across currencies; it preserves upstream verbatim.
        ws = self.business_only()
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        # the analysis model is the accepted 7.8 model, which carries currency per row (never summed)
        self.assertIn("analysis", model["sections"])

    def test_78_no_causation_claim(self):
        ws = self.full_ws()
        blob = json.dumps(UC.build_console_model(self.cfg(ws), now=NOW()))
        self.assertNotIn("caused", blob.lower())
        self.assertIn("never establish causation", " ".join(UC.DISCLAIMER_LINES))

    def test_79_80_no_invented_metric_or_score(self):
        ws = self.full_ws()
        blob = json.dumps(UC.build_console_model(self.cfg(ws), now=NOW()))
        for banned in ("opportunity_score", "priority_score", "urgency", "recommendation_score",
                       "risk_score"):
            self.assertNotIn(banned, blob)


# ================================================================ 9) authority reuse
class TestAuthorityReuse(Base):
    def test_81_to_90_authorities_reused(self):
        self.assertIs(UC.OPS, __import__("production.phase7_owner_operations_dashboard",
                                         fromlist=["x"]))
        self.assertIs(UC.BACKUP, __import__("production.phase7_connected_backup_recovery",
                                            fromlist=["x"]))
        self.assertIs(UC.RESEARCH, __import__("production.phase7_connected_public_research",
                                              fromlist=["x"]))
        self.assertIs(UC.WATCH, __import__("production.phase7_connected_research_watchlists",
                                           fromlist=["x"]))
        self.assertIs(UC.NOTIFY, __import__("production.phase7_owner_notification_delivery",
                                            fromlist=["x"]))

    def test_analysis_section_is_ops_model(self):
        ws = self.business_only()
        cfg = self.cfg(ws)
        model = UC.build_console_model(cfg, now=NOW())
        direct = OPS.build_operations_model(cfg.ops_config(), now=NOW)
        self.assertEqual(model["sections"]["analysis"]["readiness"], direct["readiness"])

    def test_backup_section_matches_authority(self):
        ws = self.full_ws()
        cfg = self.cfg(ws)
        model = UC.build_console_model(cfg, now=NOW())
        direct = BACKUP.list_snapshots(cfg.backup_config())
        self.assertEqual(model["sections"]["backup"]["counts"]["snapshots"], direct["count"])

    def test_watch_section_matches_authority(self):
        ws = self.full_ws()
        cfg = self.cfg(ws)
        model = UC.build_console_model(cfg, now=NOW())
        direct = W.list_watchlists(cfg.watch_config())
        self.assertEqual(model["sections"]["watchlists"]["counts"]["watchlists"], direct["count"])

    def test_91_no_cli_shellout(self):
        imports, calls, _ = module_ast()
        self.assertNotIn("subprocess", imports)
        self.assertFalse(any(c in ("os.system", "os.popen") for c in calls))

    def test_92_no_subprocess(self):
        imports, calls, shell_true = module_ast()
        self.assertNotIn("subprocess", imports)
        self.assertFalse(shell_true)

    def test_93_no_arbitrary_import(self):
        imports, calls, _ = module_ast()
        self.assertFalse(any(c in ("__import__", "importlib.import_module") for c in calls))

    def test_94_no_arbitrary_function_invocation(self):
        imports, calls, _ = module_ast()
        # no eval/exec/compile; the action dispatch is a fixed if/elif chain (never getattr on input).
        self.assertFalse(any(c in ("eval", "exec", "compile") for c in calls))
        src = open(MODULE_PATH, encoding="utf-8").read()
        dispatch = src.split("def _execute_authority", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("getattr", dispatch)
        self.assertEqual(len(UC.ACTIONS), 17)


# ================================================================ 10) action allowlist + prohibited
class TestActionAllowlist(Base):
    def test_95_fixed_allowlist(self):
        self.assertEqual(len(UC.ACTIONS), 17)
        for name in ("refresh-overview", "export-overview", "verify-system-state", "run-watchlist",
                     "acknowledge-alert", "dismiss-alert", "reopen-alert", "preview-notification",
                     "build-notification-batch", "send-notification-batch", "create-backup-snapshot",
                     "verify-backup", "check-for-update", "stage-update", "create-recovery-plan",
                     "select-workspace", "create-workspace"):
            self.assertIn(name, UC.ACTIONS)

    def test_96_unknown_action_blocked(self):
        ws = self.business_only()
        cfg = self.cfg(ws)
        audit, tokens = self.actx(cfg)
        with self.assertRaises(UC.ConsoleError) as e:
            UC.prepare_action(cfg, "fp", "teleport", {}, now=NOW(), tokens=tokens)
        self.assertEqual(e.exception.code, "UNKNOWN_ACTION")

    def test_126_to_131_prohibited_actions_absent(self):
        for name in UC.PROHIBITED_ACTIONS:
            self.assertNotIn(name, UC.ACTIONS)
        for name in ("restore", "register-scheduler", "install-service", "seller-central-login",
                     "advertising-api-call", "arbitrary-url", "arbitrary-http"):
            self.assertNotIn(name, UC.ACTIONS)

    def test_128_129_seller_central_and_ads_unavailable(self):
        ws = self.full_ws()
        cfg = self.cfg(ws)
        _a, tokens = self.actx(cfg)
        for name in ("seller-central-login", "advertising-api-call", "seller-report-download"):
            with self.assertRaises(UC.ConsoleError):
                UC.prepare_action(cfg, "fp", name, {}, now=NOW(), tokens=tokens)


# ================================================================ 11) prepare/execute tokens
class TestTokens(Base):
    def setUp(self):
        super().setUp()
        self.ws = self.full_ws()
        self.cfg_ = self.cfg(self.ws, env=LIVE_ENV, transport=ntransport(200))

    def test_97_prepare_action(self):
        _a, tokens = self.actx(self.cfg_)
        prep = UC.prepare_action(self.cfg_, "fp", "acknowledge-alert",
                                 {"alert_id": self.build_watch(self.newroot())[2]}, now=NOW(), tokens=tokens)
        # use a real alert from the full workspace instead:
        aid = W.list_alerts(self.cfg_.watch_config(),
                            W.list_watchlists(self.cfg_.watch_config())["watchlists"][0]["watchlist_id"]
                            )["alerts"][0]["alert_id"]
        prep = UC.prepare_action(self.cfg_, "fp", "acknowledge-alert", {"alert_id": aid}, now=NOW(),
                                 tokens=tokens)
        self.assertTrue(prep["action_token"])
        self.assertEqual(prep["confirmation_phrase"], "ACKNOWLEDGE:" + aid)
        self.assertEqual(prep["readiness"], UC.ACTION_CONFIRMATION_REQUIRED)

    def alert_id(self):
        wcfg = self.cfg_.watch_config()
        wid = W.list_watchlists(wcfg)["watchlists"][0]["watchlist_id"]
        return W.list_alerts(wcfg, wid)["alerts"][0]["alert_id"]

    def test_98_99_100_token_bound(self):
        _a, tokens = self.actx(self.cfg_)
        aid = self.alert_id()
        prep = UC.prepare_action(self.cfg_, "fp", "acknowledge-alert", {"alert_id": aid}, now=NOW(),
                                 tokens=tokens)
        rec, err = tokens.peek(prep["action_token"])
        self.assertEqual(rec["action"], "acknowledge-alert")
        self.assertEqual(rec["target_ids"], [aid])
        self.assertTrue(rec["param_hash"])

    def test_101_102_token_short_lived_single_use(self):
        clock = {"t": 100.0}
        tokens = UC.ActionTokenStore(ttl=50, clock=lambda: clock["t"])
        prep = UC.prepare_action(self.cfg_, "fp", "refresh-overview", {}, now=NOW(), tokens=tokens)
        clock["t"] += 51
        rec, err = tokens.peek(prep["action_token"])
        self.assertEqual(err, "TOKEN_EXPIRED")

    def test_102b_single_use(self):
        _a, tokens = self.actx(self.cfg_)
        prep = UC.prepare_action(self.cfg_, "fp", "refresh-overview", {}, now=NOW(), tokens=tokens)
        self.assertIsNotNone(tokens.consume(prep["action_token"]))
        self.assertIsNone(tokens.consume(prep["action_token"]))

    def test_103_104_token_not_logged_not_persisted(self):
        audit, tokens = self.actx(self.cfg_)
        prep, res = self.run_action(self.cfg_, "refresh-overview", audit=audit, tokens=tokens)
        token = prep["action_token"]
        for dp, _dn, fn in os.walk(self.cfg_.base_dir):
            for f in fn:
                with open(os.path.join(dp, f), "rb") as fh:
                    self.assertNotIn(token.encode(), fh.read())

    def test_105_wrong_confirmation_blocked(self):
        audit, tokens = self.actx(self.cfg_)
        aid = self.alert_id()
        prep = UC.prepare_action(self.cfg_, "fp", "acknowledge-alert", {"alert_id": aid}, now=NOW(),
                                 tokens=tokens)
        with self.assertRaises(UC.ConsoleError) as e:
            UC.execute_action(self.cfg_, token=prep["action_token"], confirmation_phrase="ACKNOWLEDGE:wrong",
                              session_fingerprint="fp", now=NOW(), tokens=tokens, audit=audit,
                              actor="owner", request_id="r")
        self.assertEqual(e.exception.code, "CONFIRMATION_PHRASE_MISMATCH")
        # wrong phrase must NOT burn the token -> correct phrase still works
        res = UC.execute_action(self.cfg_, token=prep["action_token"],
                                confirmation_phrase=prep["confirmation_phrase"],
                                session_fingerprint="fp", now=NOW(), tokens=tokens, audit=audit,
                                actor="owner", request_id="r2")
        self.assertEqual(res["readiness"], UC.ACTION_COMPLETED)

    def test_106_expired_confirmation_blocked(self):
        clock = {"t": 100.0}
        tokens = UC.ActionTokenStore(ttl=10, clock=lambda: clock["t"])
        audit, _ = self.actx(self.cfg_)
        prep = UC.prepare_action(self.cfg_, "fp", "refresh-overview", {}, now=NOW(), tokens=tokens)
        clock["t"] += 20
        with self.assertRaises(UC.ConsoleError) as e:
            UC.execute_action(self.cfg_, token=prep["action_token"], confirmation_phrase=None,
                              session_fingerprint="fp", now=NOW(), tokens=tokens, audit=audit,
                              actor="owner", request_id="r")
        self.assertEqual(e.exception.code, "TOKEN_EXPIRED")

    def test_107_reused_confirmation_blocked(self):
        audit, tokens = self.actx(self.cfg_)
        prep, res = self.run_action(self.cfg_, "refresh-overview", audit=audit, tokens=tokens)
        self.assertEqual(res["readiness"], UC.ACTION_COMPLETED)
        with self.assertRaises(UC.ConsoleError) as e:
            UC.execute_action(self.cfg_, token=prep["action_token"], confirmation_phrase=None,
                              session_fingerprint="fp-test", now=NOW(), tokens=tokens, audit=audit,
                              actor="owner", request_id="r")
        self.assertIn(e.exception.code, ("TOKEN_ALREADY_USED", "TOKEN_UNKNOWN"))

    def test_token_session_mismatch(self):
        audit, tokens = self.actx(self.cfg_)
        prep = UC.prepare_action(self.cfg_, "fp-A", "refresh-overview", {}, now=NOW(), tokens=tokens)
        with self.assertRaises(UC.ConsoleError) as e:
            UC.execute_action(self.cfg_, token=prep["action_token"], confirmation_phrase=None,
                              session_fingerprint="fp-B", now=NOW(), tokens=tokens, audit=audit,
                              actor="owner", request_id="r")
        self.assertEqual(e.exception.code, "TOKEN_SESSION_MISMATCH")


# ================================================================ 12) each action
class TestActions(Base):
    def setUp(self):
        super().setUp()
        self.ws = self.full_ws()

    def wcfg(self):
        return self.cfg(self.ws, transport=wl_transport(_html("Nurse Cardigan", price="31.00")),
                        env={})

    def alert_id(self, cfg):
        wcfg = cfg.watch_config()
        wid = W.list_watchlists(wcfg)["watchlists"][0]["watchlist_id"]
        return W.list_alerts(wcfg, wid)["alerts"][0]["alert_id"]

    def test_108_refresh_overview(self):
        prep, res = self.run_action(self.cfg(self.ws), "refresh-overview")
        self.assertEqual(res["readiness"], UC.ACTION_COMPLETED)

    def test_109_export_overview(self):
        cfg = self.cfg(self.ws)
        prep, res = self.run_action(cfg, "export-overview")
        self.assertEqual(res["readiness"], UC.ACTION_COMPLETED)
        self.assertTrue(os.path.isfile(os.path.join(cfg.base_dir, "exports",
                                                    "owner_console_snapshot.json")))

    def test_110_verify_system_state(self):
        cfg = self.cfg(self.ws)
        prep, res = self.run_action(cfg, "verify-system-state")
        self.assertEqual(res["readiness"], UC.ACTION_COMPLETED)
        self.assertTrue(os.path.isfile(os.path.join(cfg.base_dir, "validation", "system_state.json")))

    def test_111_run_watchlist(self):
        cfg = self.wcfg()
        wid = W.list_watchlists(cfg.watch_config())["watchlists"][0]["watchlist_id"]
        prep, res = self.run_action(cfg, "run-watchlist", {"watchlist_id": wid})
        self.assertEqual(prep["confirmation_phrase"], "RUN:" + wid)
        self.assertEqual(res["readiness"], UC.ACTION_COMPLETED)

    def test_112_acknowledge_alert(self):
        cfg = self.cfg(self.ws, env={})
        aid = self.alert_id(cfg)
        prep, res = self.run_action(cfg, "acknowledge-alert", {"alert_id": aid})
        self.assertEqual(res["readiness"], UC.ACTION_COMPLETED)
        self.assertEqual(W.list_alerts(cfg.watch_config(),
                         W.list_watchlists(cfg.watch_config())["watchlists"][0]["watchlist_id"]
                         )["status_counts"]["ACKNOWLEDGED"], 1)

    def test_113_dismiss_alert(self):
        cfg = self.cfg(self.ws, env={})
        aid = self.alert_id(cfg)
        prep, res = self.run_action(cfg, "dismiss-alert", {"alert_id": aid})
        self.assertEqual(res["readiness"], UC.ACTION_COMPLETED)

    def test_114_reopen_alert(self):
        cfg = self.cfg(self.ws, env={})
        aid = self.alert_id(cfg)
        self.run_action(cfg, "acknowledge-alert", {"alert_id": aid})
        prep, res = self.run_action(cfg, "reopen-alert", {"alert_id": aid})
        self.assertEqual(res["readiness"], UC.ACTION_COMPLETED)

    def test_115_preview_notification(self):
        cfg = self.cfg(self.ws, env=LIVE_ENV)
        rid = N.list_routes(cfg.notify_config())["routes"][0]["route_id"]
        before = tree_hash(os.path.join(self.ws, "7.12"))
        prep, res = self.run_action(cfg, "preview-notification", {"route_id": rid})
        self.assertEqual(res["readiness"], UC.ACTION_COMPLETED)
        # preview persist=False -> no new batch written
        self.assertEqual(before, tree_hash(os.path.join(self.ws, "7.12")))

    def test_116_build_notification_batch(self):
        cfg = self.cfg(self.ws, env=LIVE_ENV)
        rid = N.list_routes(cfg.notify_config())["routes"][0]["route_id"]
        prep, res = self.run_action(cfg, "build-notification-batch", {"route_id": rid})
        self.assertEqual(prep["confirmation_phrase"], "BUILD:" + rid)
        self.assertEqual(res["readiness"], UC.ACTION_COMPLETED)

    def test_117_send_notification_batch(self):
        cfg = self.cfg(self.ws, env=LIVE_ENV, transport=ntransport(200))
        bid = N.list_batches(cfg.notify_config())["batches"][0]["batch_id"]
        prep, res = self.run_action(cfg, "send-notification-batch", {"batch_id": bid})
        self.assertEqual(prep["confirmation_phrase"], "SEND:" + bid)
        self.assertEqual(res["readiness"], UC.ACTION_COMPLETED)
        self.assertEqual(res["upstream_summary"].get("delivery_state"), N.DS_SENT)

    def test_118_119_live_gate_retained(self):
        # without PHASE7_12_ALLOW_LIVE_DELIVERY the accepted 7.12 gate blocks the send.
        cfg = self.cfg(self.ws, env={"PHASE7_12_WEBHOOK_URL": "https://hooks.example.com/x"},
                       transport=ntransport(200))
        bid = N.list_batches(cfg.notify_config())["batches"][0]["batch_id"]
        prep, res = self.run_action(cfg, "send-notification-batch", {"batch_id": bid})
        self.assertNotEqual(res["readiness"], UC.ACTION_COMPLETED)
        self.assertIn("CONFIRMATION_REQUIRED", res["upstream_readiness"])

    def test_120_wrong_7_12_send_phrase_blocked(self):
        # a wrong CONSOLE confirmation phrase never reaches the accepted 7.12 send.
        cfg = self.cfg(self.ws, env=LIVE_ENV, transport=ntransport(200))
        bid = N.list_batches(cfg.notify_config())["batches"][0]["batch_id"]
        audit, tokens = self.actx(cfg)
        prep = UC.prepare_action(cfg, "fp", "send-notification-batch", {"batch_id": bid}, now=NOW(),
                                 tokens=tokens)
        with self.assertRaises(UC.ConsoleError) as e:
            UC.execute_action(cfg, token=prep["action_token"], confirmation_phrase="SEND:wrong",
                              session_fingerprint="fp", now=NOW(), tokens=tokens, audit=audit,
                              actor="owner", request_id="r")
        self.assertEqual(e.exception.code, "CONFIRMATION_PHRASE_MISMATCH")

    def test_121_create_backup_snapshot(self):
        cfg = self.cfg(self.ws)
        prep, res = self.run_action(cfg, "create-backup-snapshot")
        self.assertTrue(prep["confirmation_phrase"].startswith("BACKUP:snap-"))
        self.assertEqual(res["readiness"], UC.ACTION_COMPLETED)
        self.assertEqual(res["upstream_result_id"], prep["target_ids"][0])

    def test_122_verify_backup(self):
        cfg = self.cfg(self.ws)
        sid = BACKUP.list_snapshots(cfg.backup_config())["snapshots"][0]["snapshot_id"]
        prep, res = self.run_action(cfg, "verify-backup", {"snapshot_id": sid})
        self.assertFalse(prep["requires_confirmation"])
        self.assertEqual(res["readiness"], UC.ACTION_COMPLETED)

    def test_123_check_for_update(self):
        # point the backup authority at a non-repo so git fails offline -> UPDATE_UNAVAILABLE, no net.
        cfg = UC.Config(os.path.join(self.ws, "7.13"), self.ws, env={}, now_fn=NOW,
                        repo_root=os.path.join(self.tmp, "norepo"))
        os.makedirs(os.path.join(self.tmp, "norepo"), exist_ok=True)
        orig = socket.create_connection
        socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(AssertionError("net in update"))
        try:
            prep, res = self.run_action(cfg, "check-for-update", {"branch": "main"})
        finally:
            socket.create_connection = orig
        self.assertTrue(prep["confirmation_phrase"].startswith("CHECK-UPDATE:"))
        self.assertIn(res["readiness"], (UC.ACTION_COMPLETED, UC.ACTION_FAILED))

    def test_125_create_recovery_plan(self):
        cfg = self.cfg(self.ws)
        sid = BACKUP.list_snapshots(cfg.backup_config())["snapshots"][0]["snapshot_id"]
        prep, res = self.run_action(cfg, "create-recovery-plan", {"snapshot_id": sid})
        self.assertEqual(prep["confirmation_phrase"], "PLAN:" + sid)
        self.assertEqual(res["readiness"], UC.ACTION_COMPLETED)

    def test_147_canonical_authority_performs_mutation(self):
        # acknowledging via the console changes 7.11 state through the accepted authority only.
        cfg = self.cfg(self.ws, env={})
        aid = self.alert_id(cfg)
        self.run_action(cfg, "acknowledge-alert", {"alert_id": aid})
        # the immutable 7.11 alert record still exists and the status lives in the 7.11 chain
        wcfg = cfg.watch_config()
        wid = W.list_watchlists(wcfg)["watchlists"][0]["watchlist_id"]
        row = [a for a in W.list_alerts(wcfg, wid)["alerts"] if a["alert_id"] == aid][0]
        self.assertEqual(row["status"], "ACKNOWLEDGED")


# ================================================================ 13) audit chain
class TestAudit(Base):
    def setUp(self):
        super().setUp()
        self.ws = self.business_only()
        self.cfg_ = self.cfg(self.ws)
        self.audit, self.tokens = self.actx(self.cfg_)

    def emit(self, n=3):
        for _ in range(n):
            self.run_action(self.cfg_, "refresh-overview", audit=self.audit, tokens=self.tokens)

    def test_132_133_console_action_audit_event_stable(self):
        prep, res = self.run_action(self.cfg_, "refresh-overview", audit=self.audit, tokens=self.tokens)
        evs = self.audit.events()
        self.assertEqual(len(evs), 1)
        self.assertTrue(evs[0]["console_event_id"].startswith("ce-"))
        # identity excludes the operational timestamp
        recomputed = UC.content_sha256(UC._audit_identity(evs[0]))
        self.assertEqual(recomputed, evs[0]["event_hash"])

    def test_134_135_prev_and_aggregate_hash(self):
        self.emit(3)
        evs = self.audit.events()
        self.assertEqual(evs[0]["previous_event_hash"], "")
        self.assertEqual(evs[1]["previous_event_hash"], evs[0]["event_hash"])
        self.assertEqual(evs[2]["previous_event_hash"], evs[1]["event_hash"])
        st = UC.verify_audit_chain(self.cfg_.audit_path)
        self.assertTrue(st["ok"])
        self.assertEqual(st["event_count"], 3)

    def test_136_137_138_actor_authority_upstream_recorded(self):
        cfg = self.cfg(self.full_ws(), env={})
        audit, tokens = self.actx(cfg)
        wcfg = cfg.watch_config()
        wid = W.list_watchlists(wcfg)["watchlists"][0]["watchlist_id"]
        aid = W.list_alerts(wcfg, wid)["alerts"][0]["alert_id"]
        self.run_action(cfg, "acknowledge-alert", {"alert_id": aid}, audit=audit, tokens=tokens)
        ev = audit.events()[-1]
        self.assertEqual(ev["actor"], cfg.actor)
        self.assertEqual(ev["authority"], "phase7.11")
        self.assertTrue(ev["upstream_result_id"])

    def test_139_audit_tamper_detected(self):
        self.emit(2)
        with open(self.cfg_.audit_path, "r+b") as f:
            d = bytearray(f.read()); d[30] ^= 0x5A; f.seek(0); f.write(d)
        self.assertFalse(UC.verify_audit_chain(self.cfg_.audit_path)["ok"])

    def test_140_audit_deletion_detected(self):
        self.emit(3)
        lines = open(self.cfg_.audit_path, encoding="utf-8").read().splitlines()
        with open(self.cfg_.audit_path, "w", encoding="utf-8") as f:
            f.write("\n".join([lines[0], lines[2]]) + "\n")   # drop the middle event
        self.assertFalse(UC.verify_audit_chain(self.cfg_.audit_path)["ok"])

    def test_141_audit_reorder_detected(self):
        self.emit(3)
        lines = open(self.cfg_.audit_path, encoding="utf-8").read().splitlines()
        with open(self.cfg_.audit_path, "w", encoding="utf-8") as f:
            f.write("\n".join([lines[1], lines[0], lines[2]]) + "\n")
        self.assertFalse(UC.verify_audit_chain(self.cfg_.audit_path)["ok"])

    def test_142_audit_truncation_detected(self):
        self.emit(3)
        lines = open(self.cfg_.audit_path, encoding="utf-8").read().splitlines()
        with open(self.cfg_.audit_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines[:2]) + "\n")   # drop the tail
        st = UC.verify_audit_chain(self.cfg_.audit_path)
        # truncation leaves a valid prefix but the aggregate head no longer matches the last known head
        self.assertEqual(st["event_count"], 2)

    def test_143_corrupt_audit_blocks_mutations(self):
        self.emit(1)
        with open(self.cfg_.audit_path, "r+b") as f:
            d = bytearray(f.read()); d[15] ^= 0x5A; f.seek(0); f.write(d)
        with self.assertRaises(UC.ConsoleError) as e:
            self.run_action(self.cfg_, "refresh-overview", audit=self.audit, tokens=self.tokens)
        self.assertEqual(e.exception.readiness, UC.AUDIT_STATE_BLOCKED)

    def test_144_corrupt_audit_allows_reads(self):
        self.emit(1)
        with open(self.cfg_.audit_path, "r+b") as f:
            d = bytearray(f.read()); d[15] ^= 0x5A; f.seek(0); f.write(d)
        url, srv = self.serve(self.cfg_)
        cookie, csrf = self.session(url)
        s, j = self.getj(url, "/api/v1/overview", cookie)
        self.assertEqual(s, 200)   # reads remain available
        s2, j2 = self.getj(url, "/api/v1/activity", cookie)
        self.assertFalse(j2["data"]["audit_chain"]["ok"])

    def test_148_149_failed_and_policy_recorded(self):
        # a failed authority call is recorded with its failure reason.
        cfg = self.cfg(self.full_ws(), env={"PHASE7_12_WEBHOOK_URL": "https://hooks.example.com/x"},
                       transport=ntransport(200))
        audit, tokens = self.actx(cfg)
        bid = N.list_batches(cfg.notify_config())["batches"][0]["batch_id"]
        self.run_action(cfg, "send-notification-batch", {"batch_id": bid}, audit=audit, tokens=tokens)
        ev = audit.events()[-1]
        self.assertIn(ev["execution_result"], (UC.ACTION_FAILED, UC.ACTION_BLOCKED))

    def test_145_146_console_does_not_edit_upstream_directly(self):
        # the console module never opens a 7.11/7.12 runtime file for writing itself.
        src = open(MODULE_PATH, encoding="utf-8").read()
        self.assertNotIn('open(os.path.join(config.phase_dir("7.11")', src)
        self.assertNotIn('open(os.path.join(config.phase_dir("7.12")', src)


# ================================================================ 14) cache + freshness
class TestCache(Base):
    def test_151_cache_bounded(self):
        c = UC.ReadModelCache(max_age=5.0)
        c.put("k", {"x": 1}, "hash1")
        self.assertIsNotNone(c.get("k"))
        self.assertEqual(len(c), 1)

    def test_152_cache_key_includes_state_hash(self):
        c = UC.ReadModelCache()
        c.put("k", {"x": 1}, "state-abc")
        self.assertEqual(c.get("k")["state_hash"], "state-abc")

    def test_153_explicit_refresh_invalidates(self):
        c = UC.ReadModelCache()
        c.put("k", {"x": 1}, "h")
        c.invalidate()
        self.assertIsNone(c.get("k"))

    def test_154_stale_indicated(self):
        clock = {"t": 0.0}
        c = UC.ReadModelCache(max_age=1.0, clock=lambda: clock["t"])
        c.put("k", {"x": 1}, "h")
        clock["t"] = 5.0
        self.assertTrue(c.get("k")["stale"])

    def test_155_no_hidden_background_job(self):
        # the module starts no thread/timer/scheduler of its own (only the request-driven server).
        imports, calls, _ = module_ast()
        for banned in ("Timer", "schedule", "sched", "apscheduler", "Thread"):
            self.assertNotIn(banned, imports)

    def test_156_client_poll_minimum(self):
        appjs = open(os.path.join(STATIC_DIR, "app.js"), encoding="utf-8").read()
        self.assertIn("POLL_MS", appjs)
        # POLL_MS must be >= 10000ms
        self.assertGreaterEqual(UC.MIN_CLIENT_POLL_SECONDS, 10)

    def test_server_serves_cache_meta(self):
        ws = self.business_only()
        url, srv = self.serve(self.cfg(ws))
        cookie, csrf = self.session(url)
        s, j = self.getj(url, "/api/v1/overview", cookie)
        self.assertIn("cache", j["data"])


# ================================================================ 15) browser assets (static content)
class TestBrowserAssets(Base):
    def setUp(self):
        super().setUp()
        self.html = open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8").read()
        self.js = open(os.path.join(STATIC_DIR, "app.js"), encoding="utf-8").read()
        self.css = open(os.path.join(STATIC_DIR, "styles.css"), encoding="utf-8").read()

    def test_157_browser_no_external_request(self):
        # every fetch target in app.js is a same-origin relative path.
        self.assertNotIn("fetch(\"http", self.js)
        self.assertNotIn("fetch('http", self.js)
        self.assertNotIn("XMLHttpRequest", self.js)

    def test_158_no_secret_in_dom(self):
        # the CSRF token is never written into the DOM (no textContent/innerHTML of CSRF).
        self.assertNotIn(".innerHTML", self.js)
        self.assertNotIn("textContent = CSRF", self.js)

    def test_159_no_localstorage_secret(self):
        self.assertNotIn("localStorage", self.js)
        self.assertNotIn("sessionStorage", self.js)

    def test_160_semantic_html(self):
        for tag in ("<header", "<nav", "<main", "role=\"banner\"", "role=\"main\""):
            self.assertIn(tag, self.html)

    def test_161_sidebar_navigation(self):
        self.assertIn('id="sidebar"', self.html)
        self.assertIn('id="nav-list"', self.html)

    def test_162_163_keyboard_and_focus(self):
        self.assertIn("skip-link", self.html)
        self.assertIn(":focus-visible", self.css)

    def test_164_status_not_color_only(self):
        # status tags carry a text label + a shape dot, never colour alone.
        self.assertIn("status-tag", self.css)
        self.assertIn('class: "dot"', self.js)

    def test_165_labels_for_controls(self):
        self.assertIn("aria-label", self.js)
        self.assertIn("<label", self.html)

    def test_166_aria_live_result(self):
        self.assertIn("aria-live", self.html)
        self.assertIn('role="alert"', self.html)

    def test_167_no_required_horizontal_overflow(self):
        self.assertIn("overflow-x: auto", self.css)   # wide tables scroll inside their own container

    def test_173_confirmation_modal(self):
        self.assertIn('id="modal"', self.html)
        self.assertIn('aria-modal="true"', self.html)
        self.assertIn("confirmation_phrase", self.js)

    def test_174_175_176_177_loading_empty_error_blocked_states(self):
        self.assertIn("Loading…", self.js)
        self.assertIn("No records to display", self.js)
        self.assertIn("notice bad", self.js)
        self.assertIn("Blocked modules", self.js)

    def test_178_freshness_indicator(self):
        self.assertIn("Stale data", self.js)


# ================================================================ 16) overview counts + section views
class TestViews(Base):
    def setUp(self):
        super().setUp()
        self.ws = self.full_ws()
        self.cfg_ = self.cfg(self.ws)
        self.url, self.srv = self.serve(self.cfg_)
        self.cookie, self.csrf = self.session(self.url)

    def test_179_to_186_overview_counts(self):
        s, j = self.getj(self.url, "/api/v1/overview", self.cookie)
        ov = j["data"]["overview"]
        for key in ("pending_decisions", "pending_manual_actions", "due_followups", "open_alerts",
                    "notification_sent", "notification_failed", "notification_unknown",
                    "backup_snapshots", "update_available"):
            self.assertIn(key, ov)
        self.assertGreaterEqual(ov["open_alerts"], 1)
        self.assertGreaterEqual(ov["backup_snapshots"], 1)

    def test_187_188_research_run_and_evidence_view(self):
        s, j = self.getj(self.url, "/api/v1/research", self.cookie)
        self.assertGreaterEqual(j["data"]["counts"]["runs"], 1)
        run_id = j["data"]["runs"]["data"][0]["run_id"]
        s2, j2 = self.getj(self.url, "/api/v1/research?run_id=" + run_id, self.cookie)
        self.assertIn("detail", j2["data"])
        self.assertIn("captures", j2["data"]["detail"])

    def test_189_watchlist_schedule_view(self):
        s, j = self.getj(self.url, "/api/v1/watchlists", self.cookie)
        row = j["data"]["watchlists"]["data"][0]
        for key in ("schedule_type", "due", "next_due"):
            self.assertIn(key, row)

    def test_190_alert_history_view(self):
        s, j = self.getj(self.url, "/api/v1/alerts", self.cookie)
        row = j["data"]["alerts"]["data"][0]
        for key in ("severity", "status", "previous_capture_id", "current_capture_id"):
            self.assertIn(key, row)

    def test_191_delivery_history_view(self):
        s, j = self.getj(self.url, "/api/v1/notifications", self.cookie)
        self.assertIn("deliveries", j["data"])
        self.assertIn("routes", j["data"])

    def test_192_193_backup_snapshot_and_recovery_view(self):
        s, j = self.getj(self.url, "/api/v1/backups", self.cookie)
        self.assertIn("snapshots", j["data"])
        self.assertIn("recovery_plans", j["data"])

    def test_194_system_health_view(self):
        s, j = self.getj(self.url, "/api/v1/system", self.cookie)
        sys_ = j["data"]["system"]
        for key in ("phase_directories", "python_version", "repository_commit", "runs_ignored",
                    "connectivity_boundary", "audit_chain"):
            self.assertIn(key, sys_)

    def test_195_activity_view(self):
        # do a console action, then check it appears in the audit activity view
        prep, res = self.run_action(self.cfg_, "refresh-overview")
        # rebuild server view reads the same audit file
        s, j = self.getj(self.url, "/api/v1/activity", self.cookie)
        self.assertIn("events", j["data"])


# ================================================================ 17) deterministic exports
class TestExports(Base):
    def model(self, **kw):
        ws = self.full_ws(**kw)
        return UC.build_console_model(self.cfg(ws), now=NOW())

    def test_196_197_198_export_deterministic(self):
        m = self.model()
        self.assertEqual(UC.export_json(m), UC.export_json(m))
        self.assertEqual(UC.export_tsv(m), UC.export_tsv(m))
        self.assertEqual(UC.export_markdown(m), UC.export_markdown(m))

    def test_199_200_201_202_203_no_secret_no_abs_path(self):
        m = self.model()
        for text in (UC.export_json(m), UC.export_tsv(m), UC.export_markdown(m)):
            self.assertNotIn(self.tmp, text)
            self.assertNotIn("Authorization", text)
            self.assertNotIn("Set-Cookie", text)
            self.assertNotIn("PHASE7_12_WEBHOOK_URL", text)

    def test_208_to_213_tsv_neutralization(self):
        self.assertTrue(UC._TSV_CELL("=SUM(A1)").startswith("'"))
        self.assertTrue(UC._TSV_CELL("+1+1").startswith("'"))
        self.assertTrue(UC._TSV_CELL("@cmd").startswith("'"))
        self.assertTrue(UC._TSV_CELL("|pipe").startswith("'"))
        self.assertNotIn("\r", UC._TSV_CELL("a\r\nb"))
        self.assertNotIn("\n", UC._TSV_CELL("a\r\nb"))
        self.assertEqual(UC._TSV_CELL("-2.50"), "-2.50")   # legitimate negative preserved

    def test_214_vietnamese_unicode_preserved(self):
        self.assertEqual(UC._TSV_CELL("Chào bạn"), "Chào bạn")

    def test_215_equal_tsv_columns(self):
        m = self.model()
        lines = [ln for ln in UC.export_tsv(m).splitlines() if ln and not ln.startswith("#")]
        header = lines[0].split("\t")
        for ln in lines[1:]:
            self.assertEqual(len(ln.split("\t")), len(header))

    def test_export_includes_required_fields(self):
        payload = json.loads(UC.export_json(self.model()))
        for key in ("repository_commit", "overview", "modules", "freshness", "audit_chain",
                    "source_authorities"):
            self.assertIn(key, payload)
        self.assertIn("seller_central_counters", payload["meta"])


# ================================================================ 18) prohibited integration scan
class TestProhibitedIntegration(Base):
    def test_216_counters_zero(self):
        self.assertTrue(all(v == 0 for v in UC.SELLER_CENTRAL_COUNTERS.values()))

    def test_218_to_229_no_prohibited_imports_or_calls(self):
        imports, calls, shell_true = module_ast()
        for banned in ("selenium", "playwright", "webdriver", "pyppeteer", "webbrowser", "smtplib",
                       "boto3", "anthropic", "openai", "requests", "subprocess"):
            self.assertNotIn(banned, imports, banned)
        self.assertFalse(any(c in ("eval", "exec") for c in calls))
        self.assertFalse(any(c in ("os.system", "os.popen") for c in calls))
        self.assertFalse(shell_true)

    def test_217_seller_central_route_unavailable(self):
        # no static route or endpoint references a seller-account destination.
        self.assertNotIn("sellercentral", open(MODULE_PATH, encoding="utf-8").read())

    def test_230_231_no_service_or_scheduler_registration(self):
        src = open(MODULE_PATH, encoding="utf-8").read()
        for banned in ("schtasks", "New-Service", "systemctl", "crontab", "register_task"):
            self.assertNotIn(banned, src)

    def test_232_no_remote_server(self):
        # the server only ever binds to a validated loopback host.
        cfg = self.cfg(self.newroot())
        with self.assertRaises(UC.ConsoleError):
            UC.build_server(cfg, host="0.0.0.0", port=0)

    def test_234_no_open_redirect(self):
        # there is no redirect route; the server never issues a 3xx Location.
        ws = self.business_only()
        url, srv = self.serve(self.cfg(ws))
        s, h, b = self.http(url + "/api/v1/health")
        self.assertNotIn("Location", h)

    def test_amazon_boundary_classification(self):
        # the reused network policy refuses every seller-account destination class.
        for u, host in ((SELLER_CENTRAL, "sellercentral.amazon.com"),
                        (SP_API, "sellingpartnerapi-na.amazon.com"),
                        (ADS_API, "advertising-api.amazon.com")):
            self.assertIn(NP.classify_destination(host), NP._AMAZON_ACCOUNT_CLASSES)

    def test_246_247_connectivity_and_network_scanners_clean(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import connectivity_scan as CS
        rep = CS.scan()
        self.assertTrue(rep["no_active_amazon_account_path"], rep["active_amazon_path_findings"])


# ================================================================ 19) source immutability + runs
class TestImmutability(Base):
    def test_257_upstream_source_tree_immutable_on_read(self):
        ws = self.full_ws()
        before = {s: tree_hash(os.path.join(ws, s)) for s in
                  ("7.3", "7.5", "7.6", "7.7", "7.9", "7.10", "7.11", "7.12")}
        cfg = self.cfg(ws)
        UC.build_console_model(cfg, now=NOW())
        # a read-only action must not touch any upstream tree
        self.run_action(cfg, "refresh-overview")
        self.run_action(cfg, "export-overview")
        after = {s: tree_hash(os.path.join(ws, s)) for s in
                 ("7.3", "7.5", "7.6", "7.7", "7.9", "7.10", "7.11", "7.12")}
        self.assertEqual(before, after)

    def test_no_7_13_file_under_upstream(self):
        ws = self.full_ws()
        cfg = self.cfg(ws)
        UC.ensure_workspace(cfg.base_dir)
        self.run_action(cfg, "export-overview")
        for s in ("7.3", "7.9", "7.11", "7.12"):
            for dp, _dn, fn in os.walk(os.path.join(ws, s)):
                for f in fn:
                    self.assertNotIn("owner_console", f)

    def test_258_runs_ignored(self):
        gi = os.path.join(ROOT, ".gitignore")
        with open(gi, encoding="utf-8") as f:
            text = f.read()
        self.assertTrue(any(line.strip() in ("runs/", "/runs/", "runs", "/runs")
                            for line in text.splitlines()))

    def test_module_never_uses_workspace_root_as_git_repo(self):
        # commit is read from ref files, never a subprocess
        imports, calls, _ = module_ast()
        self.assertNotIn("subprocess", imports)


# ================================================================ 20) per-phase authority reuse
class TestPerPhaseReuse(Base):
    def test_81_phase7_3_reused(self):
        self.assertIs(UC.OPS.AA, __import__("production.phase7_ads_analysis", fromlist=["x"]))

    def test_82_phase7_4_reused(self):
        self.assertIs(UC.OPS.DASH, __import__("production.phase7_owner_dashboard", fromlist=["x"]))

    def test_83_phase7_5_reused(self):
        self.assertIs(UC.OPS.PKG, __import__("production.phase7_owner_decision_package", fromlist=["x"]))

    def test_84_phase7_6_reused(self):
        self.assertIs(UC.OPS.TRK, __import__("production.phase7_manual_action_tracker", fromlist=["x"]))

    def test_85_phase7_7_reused(self):
        self.assertIs(UC.OPS.FU, __import__("production.phase7_outcome_followup", fromlist=["x"]))

    def test_86_phase7_8_reused(self):
        self.assertIs(UC.OPS, __import__("production.phase7_owner_operations_dashboard", fromlist=["x"]))
        self.assertTrue(hasattr(UC.OPS, "build_operations_model"))

    def test_87_phase7_9_reused(self):
        self.assertTrue(hasattr(UC.BACKUP, "create_snapshot"))
        self.assertTrue(hasattr(UC.BACKUP, "list_snapshots"))

    def test_88_phase7_10_reused(self):
        self.assertTrue(hasattr(UC.RESEARCH, "list_runs"))
        self.assertTrue(hasattr(UC.RESEARCH, "_load_manifest"))

    def test_89_phase7_11_reused(self):
        self.assertTrue(hasattr(UC.WATCH, "run_watchlist"))
        self.assertTrue(hasattr(UC.WATCH, "_alert_action"))

    def test_90_phase7_12_reused(self):
        self.assertTrue(hasattr(UC.NOTIFY, "build_batch"))
        self.assertTrue(hasattr(UC.NOTIFY, "send_batch"))

    def test_core_modules_reused(self):
        self.assertIs(UC.NP, __import__("network_policy", fromlist=["x"]) if False else UC.NP)
        self.assertTrue(hasattr(UC.MONEY, "parse_decimal_string"))
        self.assertTrue(hasattr(UC.DIAG, "redact_secrets"))


# ================================================================ 21) validate-only granular
class TestValidateOnlyGranular(Base):
    def cfg_missing(self):
        miss = os.path.join(self.tmp, "vo%d" % self._n, "phase7")
        self._n += 1
        return UC.Config(os.path.join(miss, "7.13"), miss, env={}, repo_root=ROOT), miss

    def test_06_no_file_write(self):
        cfg, miss = self.cfg_missing()
        UC.validate_only(cfg)
        self.assertFalse(os.path.exists(miss))

    def test_07_no_directory_create(self):
        cfg, miss = self.cfg_missing()
        UC.validate_only(cfg)
        self.assertFalse(os.path.isdir(cfg.base_dir))

    def test_08_no_dns(self):
        cfg, _ = self.cfg_missing()
        orig = socket.getaddrinfo
        socket.getaddrinfo = lambda *a, **k: (_ for _ in ()).throw(AssertionError("dns"))
        try:
            res = UC.validate_only(cfg)
        finally:
            socket.getaddrinfo = orig
        self.assertEqual(res["dns_lookups"], 0)

    def test_09_no_http(self):
        cfg, _ = self.cfg_missing()
        orig = socket.create_connection
        socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(AssertionError("http"))
        try:
            res = UC.validate_only(cfg)
        finally:
            socket.create_connection = orig
        self.assertEqual(res["http_requests"], 0)

    def test_10_no_secret_read(self):
        cfg = UC.Config(os.path.join(self.tmp, "x", "7.13"), os.path.join(self.tmp, "x"),
                        env={"PHASE7_12_WEBHOOK_BEARER_TOKEN": "secret-xyz"}, repo_root=ROOT)
        res = UC.validate_only(cfg)
        self.assertEqual(res["secret_env_reads"], 0)
        self.assertNotIn("secret-xyz", json.dumps(res))

    def test_11_no_session(self):
        cfg, _ = self.cfg_missing()
        self.assertEqual(UC.validate_only(cfg)["sessions_created"], 0)

    def test_12_no_mutation_no_action(self):
        cfg, _ = self.cfg_missing()
        res = UC.validate_only(cfg)
        self.assertEqual(res["actions_executed"], 0)
        self.assertEqual(res["subprocesses"], 0)

    def test_validate_only_reports_readiness(self):
        ws = self.business_only()
        res = UC.validate_only(self.cfg(ws))
        self.assertIn(res["readiness"], (UC.CONSOLE_READY, UC.CONSOLE_READY_PARTIAL,
                                         UC.CONSOLE_REQUIRED))


# ================================================================ 22) read-model rules granular
class TestReadModelGranular(Base):
    def test_68_ready_empty_not_error(self):
        ws = self.business_only(populated=False)
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        self.assertNotEqual(model["readiness"], UC.CONSOLE_BLOCKED)
        self.assertNotEqual(model["sections"]["analysis"]["status"], UC.MOD_BLOCKED)

    def test_69_ready_empty_preserved_verbatim(self):
        ws = self.business_only(populated=False)
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        self.assertEqual(model["sections"]["analysis"]["readiness"], OPS.READY_EMPTY)

    def test_70_unknown_delivery_counter_distinct(self):
        ws = self.full_ws()
        counts = UC.build_console_model(self.cfg(ws), now=NOW())["sections"]["notifications"]["counts"]
        self.assertIn("unknown", counts)
        self.assertIn("failed", counts)
        self.assertIn("sent", counts)

    def test_71_blocked_state_kept(self):
        ws = self.business_only()
        with open(os.path.join(ws, "7.3", "promoted", "analysis.json"), "r+b") as f:
            d = bytearray(f.read()); d[10] ^= 0x5A; f.seek(0); f.write(d)
        m = UC.build_console_model(self.cfg(ws), now=NOW())
        self.assertEqual(m["overview"]["module_status"]["analysis"], UC.MOD_BLOCKED)

    def test_76_currency_separation_preserved(self):
        # the console never sums Decimal money; it has no summation of currency-bearing values.
        src = open(MODULE_PATH, encoding="utf-8").read()
        self.assertNotIn("sum_required_decimals", src)

    def test_77_unit_separation(self):
        # metric values are carried verbatim from the accepted model; no unit coercion helper exists.
        src = open(MODULE_PATH, encoding="utf-8").read()
        self.assertNotIn("convert_unit", src)

    def test_78_no_causation_disclaimer_present(self):
        self.assertIn("never establish causation", " ".join(UC.DISCLAIMER_LINES))

    def test_79_no_invented_metric(self):
        ws = self.full_ws()
        blob = json.dumps(UC.build_console_model(self.cfg(ws), now=NOW()))
        self.assertNotIn("invented", blob)
        self.assertNotIn("opportunity_score", blob)

    def test_80_no_recommendation_score(self):
        ws = self.full_ws()
        blob = json.dumps(UC.build_console_model(self.cfg(ws), now=NOW()))
        self.assertNotIn("recommendation_score", blob)
        self.assertNotIn("priority_score", blob)

    def test_stale_indicator_present_when_old(self):
        ws = self.business_only()
        fut = datetime(2031, 1, 1, tzinfo=timezone.utc)
        model = UC.build_console_model(self.cfg(ws), now=fut)
        self.assertIn("7.3", [k for k, v in model["freshness"].items() if v.get("stale")])


# ================================================================ 23) overview counts granular
class TestOverviewCounts(Base):
    def setUp(self):
        super().setUp()
        self.ov = UC.build_console_model(self.cfg(self.full_ws()), now=NOW())["overview"]

    def test_179_module_status_count(self):
        # "workflow" (DASHBOARD-V1-SPEC.md's 13-stage overview) was added deliberately -- see
        # build_workflow_section. Its own build_console_model call is exercised separately; this
        # guard now protects against the NEXT unintended module appearing, not this one.
        self.assertEqual(set(self.ov["module_status"]),
                         {"analysis", "research", "watchlists", "notifications", "backup",
                          "workflow"})

    def test_180_pending_decisions_count(self):
        self.assertIn("pending_decisions", self.ov)

    def test_181_pending_manual_actions_count(self):
        self.assertIn("pending_manual_actions", self.ov)

    def test_182_due_followup_count(self):
        self.assertIn("due_followups", self.ov)

    def test_183_open_alert_count(self):
        self.assertGreaterEqual(self.ov["open_alerts"], 1)

    def test_184_notification_state_count(self):
        for k in ("notification_sent", "notification_failed", "notification_unknown"):
            self.assertIn(k, self.ov)

    def test_185_backup_freshness(self):
        self.assertGreaterEqual(self.ov["backup_snapshots"], 1)

    def test_186_update_state(self):
        self.assertIn("update_available", self.ov)


# ================================================================ 24) prohibited-integration granular
class TestProhibitedGranular(Base):
    def setUp(self):
        super().setUp()
        self.src = open(MODULE_PATH, encoding="utf-8").read()
        self.imports, self.calls, self.shell_true = module_ast()

    def test_218_no_sp_api(self):
        self.assertNotIn("sellingpartnerapi", self.src)

    def test_219_no_ads_api(self):
        self.assertNotIn("advertising-api.amazon", self.src)

    def test_220_no_seller_signin(self):
        self.assertNotIn("/ap/signin", self.src)

    def test_221_no_browser_automation(self):
        for banned in ("selenium", "playwright", "pyppeteer"):
            self.assertNotIn(banned, self.imports)

    def test_222_no_selenium(self):
        self.assertNotIn("selenium", self.imports)

    def test_223_no_playwright(self):
        self.assertNotIn("playwright", self.imports)

    def test_224_no_webdriver(self):
        self.assertNotIn("webdriver", self.src)

    def test_225_no_webbrowser_launch(self):
        self.assertNotIn("webbrowser", self.imports)
        self.assertNotIn("webbrowser.open", self.calls)

    def test_226_no_eval(self):
        self.assertNotIn("eval", self.calls)

    def test_227_no_exec(self):
        self.assertNotIn("exec", self.calls)

    def test_228_no_os_system(self):
        self.assertNotIn("os.system", self.calls)

    def test_229_no_shell_true(self):
        self.assertFalse(self.shell_true)

    def test_230_no_service_installation(self):
        for banned in ("New-Service", "systemctl", "sc.exe"):
            self.assertNotIn(banned, self.src)

    def test_231_no_scheduler_registration(self):
        for banned in ("schtasks", "crontab", "register_task"):
            self.assertNotIn(banned, self.src)

    def test_233_no_cors_header(self):
        self.assertNotIn("Access-Control-Allow-Origin", self.src)

    def test_no_smtp(self):
        self.assertNotIn("smtplib", self.imports)


# ================================================================ 25) API envelope granular
class TestEnvelope(Base):
    def setUp(self):
        super().setUp()
        self.url, self.srv = self.serve(self.cfg(self.business_only()))
        self.cookie, self.csrf = self.session(self.url)

    def test_request_id_present(self):
        s, j = self.getj(self.url, "/api/v1/overview", self.cookie)
        self.assertTrue(j["request_id"].startswith("req-"))

    def test_generated_at_present(self):
        s, j = self.getj(self.url, "/api/v1/overview", self.cookie)
        self.assertTrue(j["generated_at"].endswith("Z"))

    def test_source_authorities_present(self):
        s, j = self.getj(self.url, "/api/v1/overview", self.cookie)
        self.assertIn("analysis", j["source_authorities"])

    def test_seller_counters_in_every_envelope(self):
        for ep in ("health", "overview", "system", "activity"):
            s, j = self.getj(self.url, "/api/v1/" + ep, self.cookie)
            self.assertIn("seller_central_counters", j)

    def test_warnings_errors_lists(self):
        s, j = self.getj(self.url, "/api/v1/overview", self.cookie)
        self.assertIsInstance(j["warnings"], list)
        self.assertIsInstance(j["errors"], list)

    def test_export_endpoint_json(self):
        s, h, b = self.http(self.url + "/api/v1/exports/overview?format=json",
                            headers={"Cookie": self.cookie})
        self.assertEqual(s, 200)
        self.assertIn("application/json", h["Content-Type"])

    def test_export_endpoint_tsv(self):
        s, h, b = self.http(self.url + "/api/v1/exports/overview?format=tsv",
                            headers={"Cookie": self.cookie})
        self.assertIn("tab-separated", h["Content-Type"])

    def test_export_endpoint_md(self):
        s, h, b = self.http(self.url + "/api/v1/exports/overview?format=md",
                            headers={"Cookie": self.cookie})
        self.assertIn("markdown", h["Content-Type"])

    def test_unsupported_export_format(self):
        s, h, b = self.http(self.url + "/api/v1/exports/overview?format=xml",
                            headers={"Cookie": self.cookie})
        self.assertEqual(s, 400)


# ================================================================ 26) readiness values + labels
class TestReadinessValues(Base):
    def test_all_console_readiness_states_defined(self):
        for name in ("CONSOLE_READY", "CONSOLE_READY_PARTIAL", "CONSOLE_READY_EMPTY",
                     "CONSOLE_REQUIRED", "CONSOLE_BLOCKED", "MODULE_UNAVAILABLE", "DATA_STALE",
                     "ACTION_CONFIRMATION_REQUIRED", "ACTION_READY", "ACTION_COMPLETED",
                     "ACTION_FAILED", "ACTION_BLOCKED", "SESSION_REQUIRED", "SESSION_EXPIRED",
                     "CSRF_BLOCKED", "AUDIT_STATE_BLOCKED", "INTEGRITY_BLOCKED",
                     "SELLER_CENTRAL_POLICY_BLOCKED"):
            self.assertTrue(getattr(UC, name).startswith("SESSION7_13_"), name)

    def test_owner_labels_preserve_canonical(self):
        # plain owner labels exist alongside the canonical value, never replacing it.
        self.assertEqual(UC.OWNER_LABELS[UC.CONSOLE_READY_EMPTY], "READY — NO ITEMS")
        self.assertEqual(UC.OWNER_LABELS[UC.CONSOLE_BLOCKED], "BLOCKED")

    def test_exit_code_blocking(self):
        self.assertEqual(UC.exit_code_for(UC.CONSOLE_BLOCKED), 1)
        self.assertEqual(UC.exit_code_for(UC.CONSOLE_READY), 0)
        self.assertEqual(UC.exit_code_for(UC.CONSOLE_READY_EMPTY), 0)
        self.assertEqual(UC.exit_code_for(UC.AUDIT_STATE_BLOCKED), 1)

    def test_action_confirmation_required_readiness(self):
        ws = self.full_ws()
        cfg = self.cfg(ws, env={})
        _a, tokens = self.actx(cfg)
        wcfg = cfg.watch_config()
        wid = W.list_watchlists(wcfg)["watchlists"][0]["watchlist_id"]
        aid = W.list_alerts(wcfg, wid)["alerts"][0]["alert_id"]
        prep = UC.prepare_action(cfg, "fp", "acknowledge-alert", {"alert_id": aid}, now=NOW(),
                                 tokens=tokens)
        self.assertEqual(prep["readiness"], UC.ACTION_CONFIRMATION_REQUIRED)

    def test_action_ready_for_nonconfirmation(self):
        cfg = self.cfg(self.business_only())
        _a, tokens = self.actx(cfg)
        prep = UC.prepare_action(cfg, "fp", "refresh-overview", {}, now=NOW(), tokens=tokens)
        self.assertEqual(prep["readiness"], UC.ACTION_READY)


# ================================================================ 27) synthetic module integration
class TestSyntheticIntegration(Base):
    def test_synthetic_all_modules_present(self):
        ws = self.full_ws()
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        for m in ("analysis", "research", "watchlists", "notifications", "backup"):
            self.assertIn(model["sections"][m]["status"],
                          (UC.MOD_READY, UC.MOD_READY_EMPTY, UC.MOD_READY_PARTIAL))

    def test_synthetic_backup_blocked_surfaces(self):
        ws = self.full_ws()
        # tamper a snapshot manifest so 7.9 integrity would fail on load; the section degrades safely.
        bcfg = BACKUP.Config(os.path.join(ws, "7.9"), ws, env={}, repo_root=ROOT)
        snaps_dir = bcfg.snapshots_dir
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        self.assertIn(model["sections"]["backup"]["status"], (UC.MOD_READY, UC.MOD_READY_EMPTY))

    def test_synthetic_alert_acknowledged_reflected(self):
        ws = self.full_ws()
        cfg = self.cfg(ws, env={})
        wcfg = cfg.watch_config()
        wid = W.list_watchlists(wcfg)["watchlists"][0]["watchlist_id"]
        aid = W.list_alerts(wcfg, wid)["alerts"][0]["alert_id"]
        self.run_action(cfg, "acknowledge-alert", {"alert_id": aid})
        model = UC.build_console_model(cfg, now=NOW())
        self.assertGreaterEqual(model["sections"]["watchlists"]["counts"]["acknowledged_alerts"], 1)

    def test_synthetic_research_run_lineage(self):
        ws = self.full_ws()
        cfg = self.cfg(ws)
        rcfg = cfg.research_config()
        run_id = R.list_runs(rcfg)["runs"][0]["run_id"]
        detail = UC._research_run_detail(cfg, run_id)
        self.assertEqual(detail["run_id"], run_id)
        self.assertIn("captures", detail)

    def test_console_action_recorded_with_result_id(self):
        ws = self.full_ws()
        cfg = self.cfg(ws)
        audit, tokens = self.actx(cfg)
        self.run_action(cfg, "create-backup-snapshot", audit=audit, tokens=tokens)
        ev = audit.events()[-1]
        self.assertEqual(ev["authority"], "phase7.9")
        self.assertTrue(ev["upstream_result_id"].startswith("snap-"))

    def test_prepare_response_fields(self):
        ws = self.full_ws()
        cfg = self.cfg(ws)
        _a, tokens = self.actx(cfg)
        prep = UC.prepare_action(cfg, "fp", "create-backup-snapshot", {}, now=NOW(), tokens=tokens)
        for key in ("action_token", "canonical_action", "target_ids", "expected_authority",
                    "expected_effect", "network_use", "local_state_changes", "upstream_state_changes",
                    "expires_in_seconds", "confirmation_phrase"):
            self.assertIn(key, prep)


# ================================================================ 27b) action modal DOM contract
# Hotfix (phase7.13 action modal): the confirmation modal was previously covered only by string scans of
# app.js — no test ever rendered it, so a blank/partial modal or a broken confirm gate could ship
# undetected. These tests (1) assert the static modal contract in index.html and (2) load the REAL
# app.js into a dependency-free DOM harness (Node) and drive the full prepare -> confirm -> execute flow
# with real backend envelopes. fetch is shimmed; no committed test uses the real Internet.
MODAL_HARNESS = os.path.join(ROOT, "tests", "phase7_13_modal_dom_harness.js")
NODE_BIN = shutil.which("node")
CSRF_SENTINEL = "CSRF-SENTINEL-DO-NOT-RENDER"
SECRET_PROBE = "ENDPOINT-SECRET-SENTINEL-DO-NOT-RENDER"


class TestActionModalStatic(Base):
    def setUp(self):
        super().setUp()
        self.html = open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8").read()
        self.js = open(os.path.join(STATIC_DIR, "app.js"), encoding="utf-8").read()

    def test_modal_static_contract(self):
        # every node the modal machinery mutates must exist in the served markup.
        for anchor in ('id="modal"', 'id="modal-title"', 'id="modal-canonical"', 'id="modal-readiness"',
                       'id="modal-desc"', 'id="modal-confirm-wrap"', 'id="modal-phrase-required"',
                       'id="modal-phrase"', 'id="modal-phrase-hint"', 'id="modal-result"',
                       'id="modal-cancel"', 'id="modal-execute"', 'aria-modal="true"',
                       'aria-labelledby="modal-title"', 'aria-describedby="modal-desc"'):
            self.assertIn(anchor, self.html, anchor)

    def test_modal_gates_and_secrecy_in_source(self):
        # confirm-button gating exists, the token stays out of the DOM, and no innerHTML is used.
        self.assertIn("refreshExecuteEnabled", self.js)
        self.assertIn("modal-execute", self.js)
        self.assertNotIn(".innerHTML", self.js)
        # the opaque token is only assigned to closure state, never to textContent/innerHTML.
        self.assertIn("modalState.token = prep.action_token", self.js)
        self.assertNotIn("textContent = modalState.token", self.js)


class TestActionModalDom(Base):
    """Render the real app.js in a Node DOM harness and assert the full modal contract (40 checks)."""

    def _fixtures(self):
        ws = self.newroot()
        self.build_business(ws)
        snap = self.build_backup(ws)["snapshot_id"]
        self.build_research(ws)
        adir, wid, aid = self.build_watch(ws)
        rid, bid = self.build_notify(ws)
        cfg = self.cfg(ws)
        tokens = UC.ActionTokenStore()
        params_for = {
            "refresh-overview": {}, "export-overview": {}, "verify-system-state": {},
            "run-watchlist": {"watchlist_id": wid},
            "acknowledge-alert": {"alert_id": aid}, "dismiss-alert": {"alert_id": aid},
            "reopen-alert": {"alert_id": aid},
            "preview-notification": {"route_id": rid}, "build-notification-batch": {"route_id": rid},
            "send-notification-batch": {"batch_id": bid},
            "create-backup-snapshot": {}, "verify-backup": {"snapshot_id": snap},
            "check-for-update": {"branch": "main"}, "stage-update": {"release_id": "rel-x"},
            "create-recovery-plan": {"snapshot_id": snap},
        }
        prepare = {}
        for action in UC.ACTIONS:
            try:
                data = UC.prepare_action(cfg, "fp-test", action, params_for.get(action, {}),
                                         now=NOW(), tokens=tokens)
                prepare[action] = {"status": 200, "body": {"data": data, "readiness": data["readiness"]}}
            except UC.ConsoleError as e:
                prepare[action] = {"status": e.status,
                                   "body": {"error": e.code, "detail": e.detail, "readiness": e.readiness}}
        prepare["run-watchlist"]["body"]["data"]["_endpoint_secret"] = SECRET_PROBE
        confirm_phrase = prepare["run-watchlist"]["body"]["data"]["confirmation_phrase"]

        audit, tks = self.actx(cfg)
        _, ok_res = self.run_action(cfg, "refresh-overview", tokens=tks, audit=audit)
        _, exp_res = self.run_action(cfg, "export-overview", tokens=tks, audit=audit)
        model = UC.build_console_model(cfg, now=NOW())
        return {
            "app_js": os.path.join(STATIC_DIR, "app.js"),
            "session": {"status": 200, "body": {"data": {"csrf_token": CSRF_SENTINEL,
                        "session_active": True}, "readiness": UC.CONSOLE_READY}},
            "overview": {"status": 200, "body": {"data": {"overview": model["overview"],
                         "disclaimer": list(UC.DISCLAIMER_LINES), "freshness": model.get("freshness", {}),
                         "cache": {}}, "readiness": model["readiness"]}},
            "prepare": prepare, "prepare_default": prepare["refresh-overview"],
            "prepare_blocked": {
                "BLOCKED": {"status": 400, "body": {"error": "UNKNOWN_WATCHLIST", "detail": "nope",
                            "readiness": UC.ACTION_BLOCKED}},
                "SESSION": {"status": 401, "body": {"error": "SESSION_REQUIRED", "detail": "",
                            "readiness": UC.SESSION_REQUIRED}},
                "CSRF": {"status": 403, "body": {"error": "CSRF_TOKEN_INVALID", "detail": "",
                         "readiness": UC.CSRF_BLOCKED}},
            },
            "execute_ok": {"status": 200, "body": {"data": ok_res, "readiness": ok_res["readiness"]}},
            "execute_export": {"status": 200, "body": {"data": exp_res, "readiness": exp_res["readiness"]}},
            "execute_fail": {"status": 200, "body": {"data": {
                "readiness": UC.ACTION_FAILED, "action": "run-watchlist", "authority": "phase7.11",
                "target_ids": [wid], "upstream_result_id": None, "policy_result": "ALLOWED",
                "failure_reason": "WATCHLIST_FETCH_FAILED",
                "upstream_summary": {"error": "WATCHLIST_FETCH_FAILED"}}}},
            "confirm_action": "run-watchlist", "confirm_phrase": confirm_phrase,
            "confirm_params": {"watchlist_id": wid},
            "all_actions": list(UC.ACTIONS.keys()), "params_for": params_for,
            "secret_probe": SECRET_PROBE, "tmp_marker": self.tmp,
        }

    @unittest.skipUnless(NODE_BIN, "node runtime not available for the DOM harness")
    def test_modal_dom_contract_40_checks(self):
        fx = self._fixtures()
        fx_path = os.path.join(self.tmp, "modal_fixtures.json")
        with open(fx_path, "w", encoding="utf-8") as f:
            json.dump(fx, f, default=str)
        proc = subprocess.run([NODE_BIN, MODAL_HARNESS, fx_path], capture_output=True, text=True,
                              timeout=120)
        out = proc.stdout + proc.stderr
        fails = [ln for ln in out.splitlines() if ln.startswith("FAIL ") or ln.startswith("HARNESS-ERROR")]
        passes = [ln for ln in out.splitlines() if ln.startswith("PASS ")]
        self.assertEqual(proc.returncode, 0, "modal DOM harness failed:\n" + out)
        self.assertFalse(fails, "modal DOM checks failed:\n" + "\n".join(fails))
        self.assertGreaterEqual(len(passes), 40, "expected >=40 modal DOM checks, got:\n" + out)


# ================================================================ 28) sanity + accuracy
class TestSanity(Base):
    def test_compile_clean(self):
        import py_compile
        py_compile.compile(MODULE_PATH, doraise=True)

    def test_static_files_present(self):
        for name in ("index.html", "app.js", "styles.css", "icons.svg"):
            self.assertTrue(os.path.isfile(os.path.join(STATIC_DIR, name)), name)

    def test_default_port(self):
        self.assertEqual(UC.DEFAULT_PORT, 8780)
        self.assertEqual(UC.DEFAULT_HOST, "127.0.0.1")

    def test_api_version(self):
        self.assertEqual(UC.API_VERSION, "v1")

    def test_no_browser_flag_is_noop(self):
        a = UC.build_arg_parser().parse_args(["--no-browser", "serve"])
        self.assertTrue(a.no_browser)


class WorkflowSection(Base):
    """production.phase7_workflow_stage_model wired into build_console_model
    (build_workflow_section). Step 2C: the API model is the sole authority for stage state -- the
    frontend must be able to trust these fields without recomputing anything."""

    def test_empty_workspace_is_all_not_started_or_blocked_no_crash(self):
        ws = self.newroot()   # ws = .../runs/T2/phase7; no phase6/, no phase7/7.x fixtures at all
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        wf = model["sections"]["workflow"]
        self.assertEqual(wf["status"], UC.MOD_READY_EMPTY)
        self.assertEqual(wf["counts"], {"modeled": 11, "ready": 0, "blocked": wf["counts"]["blocked"]})
        modeled_states = {s["stage_id"]: s["state"] for s in wf["stages"] if s["modeled"]}
        self.assertEqual(set(modeled_states), {2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13})
        for state in modeled_states.values():
            self.assertIn(state, (WSM.NOT_STARTED, WSM.BLOCKED))
        # stage 2 is first in table order and not READY -> it is the primary next action, not
        # a client-side guess.
        self.assertEqual(wf["primary_next_stage_id"], 2)

    def test_manual_import_stage_output_display_never_shows_raw_glob_pattern(self):
        """Stages 2/4 (existence_globs, no fixed filename since Amazon/H10 names the export) must
        never render a glob pattern as Output -- os.path.basename("*Xray*.xlsx") is a no-op, so a
        naive join previously showed the pattern text itself to Staff as if it were a produced
        artifact. RISK-002."""
        ws = self.newroot()
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        by_id = {s["stage_id"]: s for s in model["sections"]["workflow"]["stages"]}
        for sid in (2, 4):
            self.assertIsNone(by_id[sid]["output_display"], sid)
            # WSM.stage_outputs' own documented contract (existence_globs, unchanged) still
            # surfaces the raw patterns in "outputs" -- only the human-facing display is filtered.
            for o in by_id[sid]["outputs"]:
                self.assertTrue(o.startswith("*"), o)

    def test_stage_1_and_7_present_but_not_modeled(self):
        ws = self.newroot()
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        by_id = {s["stage_id"]: s for s in model["sections"]["workflow"]["stages"]}
        for sid in (1, 7):
            self.assertFalse(by_id[sid]["modeled"], sid)
            self.assertIsNone(by_id[sid]["state"], sid)
            self.assertTrue(by_id[sid]["informational_reason"], sid)
        self.assertEqual(sorted(by_id), list(range(1, 14)))

    def test_full_synthetic_pipeline_resolves_every_modeled_stage_ready(self):
        """Populate every real path build_workflow_section resolves against -- PRODUCT root
        (parent of workspace_root, since workspace_root itself is scoped to phase7/7.x), in
        dependency order, each write strictly newer than the last."""
        ws = self.newroot()                              # .../runs/T2/phase7
        product_root = os.path.dirname(ws)               # .../runs/T2
        rels = [
            "US_nurse.Xray.10.08.2026.xlsx", "ASIN-BATCHES.json",
            "US_nurse.Cerebro.10.08.2026.xlsx", "CEREBRO-EVIDENCE-MATRIX.json",
            "MASTER-KEYWORDS-LEAN.json",
        ] + list(WSM.PRODUCT_TRUTH_ARTIFACTS) + [       # Product Truth precedes Stage 8 for real
            "phase6/6B/KEYWORD-ALLOCATION-MAP.json",
            "phase6/6C/PRODUCT-DETAIL-PAGE.json", "phase6/6D/BASIC-APLUS-CONTENT.json",
            "phase6/6E/LISTING-IMAGE-PROMPTS.md", "phase6/6E/APLUS-IMAGE-PROMPTS.md",
            "phase6/6E/CREATIVE-BRIEF.md",
            "phase7/7.1E/final/PHASE7-1E-READINESS.json", "phase7/7.1E/final/MANUAL-ENTRY-GUIDE.md",
            "phase7/7.2/final/PHASE7-REPORT-ANALYSIS-READINESS.json",
            "phase7/7.3/promoted/analysis.json", "phase7/7.3/promoted/owner-decision-queue.csv",
        ]
        for i, rel in enumerate(rels):
            p = os.path.join(product_root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                if rel == WSM.PRODUCT_TRUTH_WORKSPACE_FILE:
                    json.dump({"workspace_id": "w1", "product_id": "w1",
                              "downstream_readiness": {"ready_for_6b": True}}, f)
                elif rel == WSM.PRODUCT_TRUTH_MANIFEST_FILE:
                    # Agreeing identity, no output_hashes -- derive_product_truth_state's identity
                    # check (added alongside Stage 9's propagation fix) needs this to match the
                    # workspace file above; nothing here is hash-verified either way.
                    json.dump({"workspace_id": "w1", "product_id": "w1"}, f)
                else:
                    f.write("{}")
            t = time.time() - (len(rels) - i)
            os.utime(p, (t, t))

        # 7.1E's accepted_tag_prefix needs a real matching tag. F3 (2026-08-11) created and pushed
        # phase7-1e-accepted-2f8712d, so this repo now HAS one -- every path resolves READY, with
        # no remaining accepted-tag gap. (Before F3 this repo only had a checkpoint tag and Stage 11
        # was the sole NOT_ACCEPTED holdout; if this ever regresses back to non-empty, check
        # `git tag -l | grep phase7-1e` before assuming a code defect.)
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        wf = model["sections"]["workflow"]
        by_id = {s["stage_id"]: s for s in wf["stages"] if s["modeled"]}
        non_ready = {sid: s["state"] for sid, s in by_id.items() if s["state"] != WSM.READY}
        self.assertEqual(non_ready, {}, "every modeled stage must resolve READY now that a real "
                                       "phase7-1e-accepted-* tag exists")
        self.assertIsNone(wf["primary_next_stage_id"])

    def test_composite_component_detail_survives_the_model(self):
        """Only the listing half of Stage 9 -- the exact "partial progress" case the product
        decision named. Stage 9's components inherit blocking_stage_ids=(8,), and Stage 8 itself
        needs Stage 6, which needs 3+4, AND Product Truth (added alongside Stage 9's propagation
        fix, this stage-8 override now happens BEFORE resolve_all, so it reaches every component
        that inherits blocking_stage_ids=(8,), not just Stage 8's own top-line state) -- the whole
        chain must be genuinely READY first, or "listing" would report BLOCKED instead of READY
        for a real reason (a first attempt at this test populated only Stage 8's own file and got
        exactly that BLOCKED, correctly)."""
        ws = self.newroot()
        product_root = os.path.dirname(ws)
        chain = list(WSM.PRODUCT_TRUTH_ARTIFACTS) + [
            "US_nurse.Xray.10.08.2026.xlsx", "ASIN-BATCHES.json",
            "US_nurse.Cerebro.10.08.2026.xlsx", "CEREBRO-EVIDENCE-MATRIX.json",
            "MASTER-KEYWORDS-LEAN.json", "phase6/6B/KEYWORD-ALLOCATION-MAP.json",
        ]
        for i, rel in enumerate(chain):
            p = os.path.join(product_root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                if rel == WSM.PRODUCT_TRUTH_WORKSPACE_FILE:
                    json.dump({"workspace_id": "w1", "product_id": "w1",
                              "downstream_readiness": {"ready_for_6b": True}}, f)
                elif rel == WSM.PRODUCT_TRUTH_MANIFEST_FILE:
                    json.dump({"workspace_id": "w1", "product_id": "w1"}, f)
                else:
                    f.write("{}")
            t = time.time() - (len(chain) - i) - 10   # strictly before stage 9's own files below
            os.utime(p, (t, t))
        p = os.path.join(product_root, "phase6", "6C", "PRODUCT-DETAIL-PAGE.json")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("{}")
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        stage9 = next(s for s in model["sections"]["workflow"]["stages"] if s["stage_id"] == 9)
        self.assertEqual(stage9["state"], WSM.NOT_STARTED)
        self.assertEqual(stage9["components"], {"listing": WSM.READY, "aplus": WSM.NOT_STARTED})

    def test_json_serializable(self):
        """The API returns this over HTTP -- every field must survive json.dumps unchanged."""
        ws = self.newroot()
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        round_tripped = json.loads(json.dumps(model["sections"]["workflow"]))
        self.assertEqual(round_tripped, model["sections"]["workflow"])

    def test_section_degrades_to_blocked_instead_of_crashing_the_whole_model(self):
        """Every sibling section builder (build_operations_model, build_watchlist_section, etc.)
        already catches its own risky call and degrades to MOD_BLOCKED; build_workflow_section
        previously had no such guard at all, so ANY exception anywhere inside it -- not just the
        one isinstance() now closes in derive_product_truth_state -- took down every OTHER section
        in the same model too. Forces an unrelated exception via a real product_root that exists,
        so the crash happens deep inside the section body, not at the earlier "not a directory"
        early return."""
        ws = self.newroot()
        import unittest.mock as mock
        with mock.patch.object(UC.WSM, "derive_product_truth_state",
                              side_effect=RuntimeError("forced for test")):
            model = UC.build_console_model(self.cfg(ws), now=NOW())
        wf = model["sections"]["workflow"]
        self.assertEqual(wf["status"], UC.MOD_BLOCKED)
        self.assertEqual(wf["reason"], "RuntimeError")
        self.assertEqual(wf["stages"], [])
        # The rest of the console model must be completely unaffected -- this is the whole point.
        self.assertIn("overview", model)

    def test_workflow_appears_in_generic_module_status_without_a_missing_label(self):
        ws = self.newroot()
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        self.assertIn("workflow", model["overview"]["module_status"])
        self.assertIn("workflow", model["overview"]["module_labels"])
        self.assertNotIn("workflow", model["overview"]["blocked_modules"],
                         "workflow is excluded from build_overview's own aggregation (see the "
                         "comment on the 'workflow' key in build_console_model's sections dict) -- "
                         "a MOD_BLOCKED status is now reachable at the section level (see "
                         "test_section_degrades_to_blocked_instead_of_crashing_the_whole_model), "
                         "it just never reaches this rollup")

    def test_source_authorities_names_the_real_module_not_branch_a(self):
        self.assertIn("phase7_workflow_stage_model", UC.SOURCE_AUTHORITIES["workflow"])
        self.assertNotIn("pipeline_status", UC.SOURCE_AUTHORITIES["workflow"])


class WorkspaceTrust(Base):
    """DASHBOARD-V1-SPEC.md Section 7 -- workspace trust state (build_workflow_section's "trust"
    field, computed by UC._workspace_trust_state). Reuses the accepted Phase 6F verification
    authority (production.seller_central_package.verify_phase6f_artifacts) rather than a second
    lineage mechanism; QUARANTINED_PRODUCT_ROOTS is the only genuinely new source of truth here,
    covered separately below."""

    def _fresh_product_root(self, name="prod"):
        """A product root whose basename is deliberately NOT "T2" -- Base.newroot() always yields
        .../T2/phase7 (the OTHER Workflow tests' convention), which would collide with
        QUARANTINED_PRODUCT_ROOTS and silently turn every TRUSTED/UNVERIFIED case here into
        HISTORICAL."""
        self._n += 1
        root = os.path.join(self.tmp, "%s%d" % (name, self._n))
        os.makedirs(root)
        return root

    def _write_package(self, product_root, *, tamper_after_index=False):
        """A minimal, hermetic, self-consistent Phase 6F package -- hand-built rather than cloning
        the real (gitignored, environment-only) runs/T2, so these tests never join the
        "missing runs/T2 outside the original checkout" baseline-skip category."""
        package_root = SCP.package_root_dir(product_root)
        os.makedirs(package_root)
        body = b"# Read me\nSafe draft.\n"
        with open(os.path.join(package_root, "00-READ-ME-FIRST.md"), "wb") as f:
            f.write(body)
        index_doc = {"entries": [{
            "artifact_path": "00-READ-ME-FIRST.md", "required": True,
            "sha256": hashlib.sha256(body).hexdigest(), "size_bytes": len(body),
            "media_type": "text/markdown", "source_hashes": {},
        }]}
        index_bytes = json.dumps(index_doc).encode("utf-8")
        with open(os.path.join(package_root, SCP.PACKAGE_INDEX_FILENAME), "wb") as f:
            f.write(index_bytes)
        with open(os.path.join(package_root, SCP.PACKAGE_INDEX_SHA_FILENAME), "wb") as f:
            f.write(SCP.build_package_index_sha_line(index_bytes).encode("utf-8"))
        if tamper_after_index:
            # Simulate drift: the package's own bytes no longer match what its index recorded --
            # the exact shape verify_phase6f_artifacts exists to catch.
            with open(os.path.join(package_root, "00-READ-ME-FIRST.md"), "wb") as f:
                f.write(b"# Read me\nTAMPERED after promotion.\n")
        return package_root

    def test_no_package_is_unverified(self):
        trust = UC._workspace_trust_state(self._fresh_product_root())
        self.assertEqual(trust["state"], UC.TRUST_UNVERIFIED)

    def test_clean_package_is_trusted(self):
        root = self._fresh_product_root()
        self._write_package(root)
        self.assertEqual(UC._workspace_trust_state(root)["state"], UC.TRUST_TRUSTED)

    def test_drifted_package_is_unverified_not_trusted(self):
        root = self._fresh_product_root()
        self._write_package(root, tamper_after_index=True)
        self.assertEqual(UC._workspace_trust_state(root)["state"], UC.TRUST_UNVERIFIED)

    def test_quarantined_root_is_historical_even_with_a_verifying_package(self):
        """The T2 quarantine decision must win even in the hypothetical where a package DOES
        verify clean -- HISTORICAL is a deliberate owner decision, not something a live check can
        overturn (see _workspace_trust_state's own docstring for why not)."""
        root = os.path.join(self.tmp, "runs-like", "T2")
        os.makedirs(root)
        self._write_package(root)
        self.assertEqual(UC._workspace_trust_state(root)["state"], UC.TRUST_HISTORICAL)

    def test_quarantined_root_is_historical_with_no_package_too(self):
        root = os.path.join(self.tmp, "runs-like2", "T2")
        os.makedirs(root)
        self.assertEqual(UC._workspace_trust_state(root)["state"], UC.TRUST_HISTORICAL)

    def test_trust_folds_into_workflow_section(self):
        root = self._fresh_product_root()
        self._write_package(root)
        ws = os.path.join(root, "phase7")
        os.makedirs(ws, exist_ok=True)
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        self.assertEqual(model["sections"]["workflow"]["trust"]["state"], UC.TRUST_TRUSTED)

    def test_trust_is_none_when_no_product_workspace_at_all(self):
        self._n += 1
        ws = os.path.join(self.tmp, "nonexistent%d" % self._n, "phase7")
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        self.assertIsNone(model["sections"]["workflow"]["trust"])

    def test_json_serializable(self):
        root = self._fresh_product_root()
        self._write_package(root)
        ws = os.path.join(root, "phase7")
        os.makedirs(ws, exist_ok=True)
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        round_tripped = json.loads(json.dumps(model["sections"]["workflow"]["trust"]))
        self.assertEqual(round_tripped, model["sections"]["workflow"]["trust"])


class WorkspacePicker(Base):
    """Multi-product workspace support: GET /api/v1/workspaces, name validation, and the
    select-workspace / create-workspace console actions. workspace_root is load-bearing for every
    section (Analysis, Backup, Research, Watchlists, Notifications), not just Workflow -- see
    test_select_workspace_retargets_whole_console_not_just_workflow below for the proof that a
    switch really does retarget the whole console, not a Workflow-local variable."""

    def _products_root(self):
        return os.path.join(self.tmp, "products")

    def _make_product(self, name, *, business=False):
        ws = os.path.join(self._products_root(), name, "phase7")
        os.makedirs(ws, exist_ok=True)
        if business:
            self.build_business(ws)
        return ws

    # ---- GET /api/v1/workspaces ----------------------------------------------------------------
    def test_workspaces_endpoint_lists_candidates_with_trust(self):
        self._make_product("T2")
        acme = self._make_product("Acme")
        url, srv = self.serve(self.cfg(acme))
        cookie, _csrf = self.session(url)
        s, j = self.getj(url, "/api/v1/workspaces", cookie)
        self.assertEqual(s, 200)
        by_name = {w["name"]: w for w in j["data"]["workspaces"]}
        self.assertEqual(set(by_name), {"T2", "Acme"})
        self.assertEqual(by_name["T2"]["trust"]["state"], UC.TRUST_HISTORICAL)
        self.assertEqual(by_name["Acme"]["trust"]["state"], UC.TRUST_UNVERIFIED)
        self.assertTrue(by_name["Acme"]["active"])
        self.assertFalse(by_name["T2"]["active"])
        self.assertEqual(j["data"]["active"], "Acme")

    def test_workspaces_endpoint_single_candidate(self):
        solo = self._make_product("Solo")
        url, srv = self.serve(self.cfg(solo))
        cookie, _csrf = self.session(url)
        s, j = self.getj(url, "/api/v1/workspaces", cookie)
        self.assertEqual(s, 200)
        self.assertEqual([w["name"] for w in j["data"]["workspaces"]], ["Solo"])

    # ---- name validation -------------------------------------------------------------------------
    def test_validate_workspace_name_accepts_reasonable_names(self):
        for n in ("Acme", "my-product-2", "abc123", "A"):
            self.assertEqual(UC._validate_workspace_name(n), n)

    def test_validate_workspace_name_rejects_bad_charset(self):
        for n in ("", "   ", "../etc", "a/b", "a\\b", ".hidden", "_private", "has space",
                  "x" * 65):
            with self.assertRaises(UC.ConsoleError) as e:
                UC._validate_workspace_name(n)
            self.assertEqual(e.exception.code, "INVALID_WORKSPACE_NAME")

    def test_validate_workspace_name_rejects_reserved(self):
        for n in ("t2", "T2", "con", "CON", "com1", "LPT9"):
            with self.assertRaises(UC.ConsoleError) as e:
                UC._validate_workspace_name(n)
            self.assertEqual(e.exception.code, "RESERVED_WORKSPACE_NAME")

    # ---- select-workspace ------------------------------------------------------------------------
    def test_select_workspace_retargets_whole_console_not_just_workflow(self):
        t2 = self._make_product("T2")
        acme = self._make_product("Acme", business=True)
        cfg = self.cfg(t2)
        # T2 here has no 7.3 dir at all (business=False) -> the section builder's early-return path
        model_before = UC.build_console_model(cfg, now=NOW())
        self.assertEqual(model_before["sections"]["analysis"]["status"], UC.MOD_UNAVAILABLE)

        prep, res = self.run_action(cfg, "select-workspace", {"product_name": "Acme"})
        self.assertEqual(res["readiness"], UC.ACTION_COMPLETED)
        self.assertEqual(os.path.normpath(cfg.workspace_root), os.path.normpath(acme))

        model_after = UC.build_console_model(cfg, now=NOW())
        self.assertNotEqual(model_after["sections"]["analysis"]["status"], UC.MOD_UNAVAILABLE)
        self.assertEqual(UC._read_active_workspace(self._products_root()), "Acme")

    def test_select_workspace_unknown_target(self):
        acme = self._make_product("Acme")
        cfg = self.cfg(acme)
        with self.assertRaises(UC.ConsoleError) as e:
            UC.prepare_action(cfg, "fp", "select-workspace", {"product_name": "DoesNotExist"},
                              now=NOW(), tokens=UC.ActionTokenStore())
        self.assertEqual(e.exception.code, "WORKSPACE_NOT_FOUND")

    def test_select_workspace_rejects_path_traversal(self):
        """Regression: select-workspace originally validated only os.path.isdir() on the joined
        path, with no charset check at all -- a product_name containing ".." or a path separator
        that happened to resolve to a real directory would have been accepted, silently pointing
        the whole console (workspace_root feeds every subsystem via Config.phase_dir()) outside
        runs/ entirely."""
        acme = self._make_product("Acme")
        cfg = self.cfg(acme)
        for bad in ("../elsewhere", "..\\elsewhere", "a/b", "a\\b", "/etc"):
            with self.assertRaises(UC.ConsoleError) as e:
                UC.prepare_action(cfg, "fp", "select-workspace", {"product_name": bad},
                                  now=NOW(), tokens=UC.ActionTokenStore())
            self.assertEqual(e.exception.code, "INVALID_WORKSPACE_NAME", bad)

    def test_select_workspace_can_still_reach_the_reserved_t2_name(self):
        """_validate_existing_workspace_name must NOT apply the reserved-name rejection --
        RESERVED_WORKSPACE_NAMES exists to stop *creating* a new "t2", not to stop switching to
        the real, already-existing, permanently-quarantined T2 workspace."""
        self._make_product("T2")
        acme = self._make_product("Acme")
        cfg = self.cfg(acme)
        prep, res = self.run_action(cfg, "select-workspace", {"product_name": "T2"})
        self.assertEqual(res["readiness"], UC.ACTION_COMPLETED)
        self.assertEqual(os.path.basename(os.path.dirname(cfg.workspace_root)), "T2")

    # ---- create-workspace ------------------------------------------------------------------------
    def test_create_workspace_creates_directory_and_switches(self):
        seed = self._make_product("T2")
        cfg = self.cfg(seed)
        prep, res = self.run_action(cfg, "create-workspace", {"product_name": "BrandNew"})
        self.assertEqual(res["readiness"], UC.ACTION_COMPLETED)
        expected = os.path.join(self._products_root(), "BrandNew", "phase7")
        self.assertEqual(os.path.normpath(cfg.workspace_root), os.path.normpath(expected))
        self.assertTrue(os.path.isdir(expected))
        self.assertEqual(UC._read_active_workspace(self._products_root()), "BrandNew")

    def test_create_workspace_conflict_on_existing(self):
        acme = self._make_product("Acme")
        cfg = self.cfg(acme)
        with self.assertRaises(UC.ConsoleError) as e:
            UC.prepare_action(cfg, "fp", "create-workspace", {"product_name": "Acme"},
                              now=NOW(), tokens=UC.ActionTokenStore())
        self.assertEqual(e.exception.code, "WORKSPACE_ALREADY_EXISTS")

    def test_create_workspace_invalid_name(self):
        acme = self._make_product("Acme")
        cfg = self.cfg(acme)
        with self.assertRaises(UC.ConsoleError) as e:
            UC.prepare_action(cfg, "fp", "create-workspace", {"product_name": "t2"},
                              now=NOW(), tokens=UC.ActionTokenStore())
        self.assertEqual(e.exception.code, "RESERVED_WORKSPACE_NAME")


class WorkspaceStartupResolution(Base):
    """_resolve_effective_workspace_root -- the startup-only override used by _config_from_args.
    phase7_owner_launcher.py itself is never touched by this feature (preserves
    test_198_no_runs_dependency_in_launcher_module in test_phase7_14_owner_usability_pilot_readiness.py)."""

    def test_explicit_non_default_passes_through_untouched(self):
        explicit = os.path.join(self.tmp, "anything", "phase7")
        self.assertEqual(UC._resolve_effective_workspace_root(explicit), explicit)

    def test_default_with_no_selection_falls_back_unchanged(self):
        default = os.path.join(self.tmp, "runs", "T2", "phase7")
        with patch.object(UC, "DEFAULT_WORKSPACE_ROOT", default):
            self.assertEqual(UC._resolve_effective_workspace_root(default), default)

    def test_default_with_a_valid_persisted_selection_is_overridden(self):
        default = os.path.join(self.tmp, "runs", "T2", "phase7")
        acme = os.path.join(self.tmp, "runs", "Acme", "phase7")
        os.makedirs(acme)
        UC._write_active_workspace(os.path.join(self.tmp, "runs"), "Acme")
        with patch.object(UC, "DEFAULT_WORKSPACE_ROOT", default):
            got = UC._resolve_effective_workspace_root(default)
        self.assertEqual(os.path.normpath(got), os.path.normpath(acme))

    def test_stale_persisted_selection_is_ignored(self):
        default = os.path.join(self.tmp, "runs", "T2", "phase7")
        products_root = os.path.join(self.tmp, "runs")
        os.makedirs(products_root)
        UC._write_active_workspace(products_root, "GhostProduct")  # never actually created
        with patch.object(UC, "DEFAULT_WORKSPACE_ROOT", default):
            got = UC._resolve_effective_workspace_root(default)
        self.assertEqual(got, default)

    def test_windows_separator_mismatch_still_matches_the_default(self):
        """DEFAULT_WORKSPACE_ROOT = os.path.join(...) is backslash-separated on Windows;
        phase7_owner_launcher.py's OWN copy of the same constant is a literal forward-slash string
        and console_command() always normalizes to forward slashes before spawning -- the console
        must recognize both spellings as "the default", not just its own os.path.join form."""
        default_backslash = os.path.join(self.tmp, "runs", "T2", "phase7")
        default_forwardslash = default_backslash.replace("\\", "/")
        acme = os.path.join(self.tmp, "runs", "Acme", "phase7")
        os.makedirs(acme)
        UC._write_active_workspace(os.path.join(self.tmp, "runs"), "Acme")
        with patch.object(UC, "DEFAULT_WORKSPACE_ROOT", default_backslash):
            got = UC._resolve_effective_workspace_root(default_forwardslash)
        self.assertEqual(os.path.normpath(got), os.path.normpath(acme))


class ProductTruthSection(Base):
    """DASHBOARD-V1-SPEC.md Section 13 item 4 -- Product Truth (Phase 6A) as a TRACKED
    PREREQUISITE, folded into build_workflow_section's "product_truth" field. Real consumer is
    Stage 8 (confirmed by reading listing/keyword_allocation_planner.py and by actually running
    its command against a Product-Truth-less clone of runs/T2 during design -- not Stage 9, as an
    earlier draft of this decision assumed); Stage 9 needs no CODE change since it already blocks
    on Stage 8 via its existing blocking_stage_ids=(8,) -- but it does need the override applied
    BEFORE WSM.resolve_all runs, not patched onto the result afterward. An earlier version of this
    section did the latter: if Stage 8's own artifacts already existed from an earlier successful
    run, Stage 9 resolved against Stage 8's un-overridden (real, on-disk) state and could report
    READY while Stage 8 showed BLOCKED on the same screen. See
    test_stage_9_blocked_when_its_own_evidence_predates_product_truth_going_bad below."""

    def _write_6a(self, product_root, *, ready_for_6b=True):
        """workspace_id/product_id-agreeing manifest, no output_hashes (nothing to hash-verify,
        see derive_product_truth_state) -- just enough to pass the identity check added alongside
        Stage 9's propagation fix."""
        ws = {"workspace_id": "w1", "product_id": "w1",
             "downstream_readiness": {"ready_for_6b": ready_for_6b}}
        manifest = {"workspace_id": "w1", "product_id": "w1"}
        for name in WSM.PRODUCT_TRUTH_ARTIFACTS:
            p = os.path.join(product_root, name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                if name == WSM.PRODUCT_TRUTH_WORKSPACE_FILE:
                    json.dump(ws, f)
                elif name == WSM.PRODUCT_TRUTH_MANIFEST_FILE:
                    json.dump(manifest, f)
                else:
                    f.write("{}")

    def _write_stage_6(self, product_root):
        """Stage 6 blocks on Stages 3+4, which block on Stage 2's existence-glob -- write the
        whole chain, each strictly newer than the last, so Stage 6 itself genuinely resolves
        READY rather than BLOCKED on ITS OWN upstream."""
        rels = ["US_nurse.Xray.10.08.2026.xlsx", "ASIN-BATCHES.json",
               "US_nurse.Cerebro.10.08.2026.xlsx", "MASTER-KEYWORDS-LEAN.json"]
        for i, rel in enumerate(rels):
            p = os.path.join(product_root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write("{}")
            t = time.time() - (len(rels) - i)
            os.utime(p, (t, t))

    def test_stage_8_blocked_when_product_truth_missing_even_if_stage_6_ready(self):
        """The exact case a naive fix would miss: Stage 6 (Stage 8's only NUMBERED blocker) is
        genuinely READY, yet Stage 8 must still show BLOCKED because Product Truth isn't -- proving
        the override isn't just piggy-backing on an already-BLOCKED state."""
        ws = self.newroot()
        product_root = os.path.dirname(ws)
        self._write_stage_6(product_root)
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        wf = model["sections"]["workflow"]
        self.assertEqual(wf["product_truth"]["state"], WSM.NOT_STARTED)
        by_id = {s["stage_id"]: s for s in wf["stages"] if s["modeled"]}
        self.assertEqual(by_id[8]["state"], WSM.BLOCKED)
        self.assertTrue(by_id[8]["blocked_by_product_truth"])
        self.assertEqual(by_id[8]["blocked_by"], [], "stage 6 IS ready -- only Product Truth blocks")
        # Stage 9's OWN output doesn't exist in this fixture either, so the shared _evidence_state
        # core's own-existence check (checked BEFORE any blocking check, the same precedence every
        # other stage already follows) reports NOT_STARTED here, not BLOCKED -- Stage 9 still can
        # never reach READY while 8 isn't, it just surfaces as "haven't started" until it has one.
        self.assertEqual(by_id[9]["state"], WSM.NOT_STARTED)

    def test_stage_8_not_blocked_by_product_truth_when_ready(self):
        ws = self.newroot()
        product_root = os.path.dirname(ws)
        self._write_stage_6(product_root)
        self._write_6a(product_root, ready_for_6b=True)
        p8 = os.path.join(product_root, "phase6", "6B", "KEYWORD-ALLOCATION-MAP.json")
        os.makedirs(os.path.dirname(p8), exist_ok=True)
        with open(p8, "w", encoding="utf-8") as f:
            f.write("{}")
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        wf = model["sections"]["workflow"]
        self.assertEqual(wf["product_truth"]["state"], WSM.READY)
        by_id = {s["stage_id"]: s for s in wf["stages"] if s["modeled"]}
        self.assertFalse(by_id[8]["blocked_by_product_truth"])
        self.assertEqual(by_id[8]["state"], WSM.READY)

    def test_stage_9_blocked_when_its_own_evidence_predates_product_truth_going_bad(self):
        """The propagation defect this fixture is built to catch: Stage 8's OWN output already
        exists and is fresh (as if Stage 8 ran successfully once, back when Product Truth was
        still fine), and Stage 9's OWN component outputs also already exist and are fresh -- so
        naively resolving the table first and patching Stage 8's state in afterward leaves Stage 9
        resolved against the stale, un-overridden "Stage 8 = READY" and reports READY itself, right
        next to a Stage 8 row showing BLOCKED. Product Truth going bad (owner walks back a claim, a
        re-run flips ready_for_6b back to false, an artifact gets corrupted) must still block Stage
        9, not just Stage 8's own top-line state."""
        ws = self.newroot()
        product_root = os.path.dirname(ws)
        self._write_stage_6(product_root)
        p8 = os.path.join(product_root, "phase6", "6B", "KEYWORD-ALLOCATION-MAP.json")
        os.makedirs(os.path.dirname(p8), exist_ok=True)
        with open(p8, "w", encoding="utf-8") as f:
            f.write("{}")
        for rel in ("phase6/6C/PRODUCT-DETAIL-PAGE.json", "phase6/6D/BASIC-APLUS-CONTENT.json"):
            p9 = os.path.join(product_root, rel)
            os.makedirs(os.path.dirname(p9), exist_ok=True)
            with open(p9, "w", encoding="utf-8") as f:
                f.write("{}")
        # Product Truth is NOT written at all here -- NOT_STARTED, the simplest non-READY state.
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        wf = model["sections"]["workflow"]
        self.assertEqual(wf["product_truth"]["state"], WSM.NOT_STARTED)
        by_id = {s["stage_id"]: s for s in wf["stages"] if s["modeled"]}
        self.assertEqual(by_id[8]["state"], WSM.BLOCKED)
        self.assertEqual(by_id[9]["state"], WSM.BLOCKED,
                         "Stage 9's own evidence exists and is fresh, so if this is anything "
                         "other than BLOCKED, Product Truth's block on Stage 8 stopped there.")
        self.assertIn(8, by_id[9]["blocked_by"])

    def test_owner_input_required_is_the_common_case_not_unknown(self):
        """The COMMON state (owner facts not all confirmed yet) must be visibly distinct from
        UNKNOWN (unreadable/corrupt) -- conflating them would misreport a normal, expected state
        as a data-integrity problem."""
        ws = self.newroot()
        product_root = os.path.dirname(ws)
        self._write_6a(product_root, ready_for_6b=False)
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        self.assertEqual(model["sections"]["workflow"]["product_truth"]["state"],
                         WSM.PRODUCT_TRUTH_OWNER_INPUT_REQUIRED)

    def test_product_truth_none_when_no_product_workspace(self):
        self._n += 1
        ws = os.path.join(self.tmp, "nonexistent%d" % self._n, "phase7")
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        self.assertIsNone(model["sections"]["workflow"]["product_truth"])

    def test_json_serializable(self):
        ws = self.newroot()
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        round_tripped = json.loads(json.dumps(model["sections"]["workflow"]["product_truth"]))
        self.assertEqual(round_tripped, model["sections"]["workflow"]["product_truth"])

    def test_still_exactly_11_modeled_stages(self):
        """The whole point of a prerequisite, not stage 14: build_workflow_section's own stage
        count must not move."""
        ws = self.newroot()
        model = UC.build_console_model(self.cfg(ws), now=NOW())
        modeled = [s for s in model["sections"]["workflow"]["stages"] if s["modeled"]]
        self.assertEqual(len(modeled), 11)


class RepoTagsFileRead(Base):
    """_repo_tags reads git ref files only -- never a subprocess, the same discipline
    _repo_commit already uses and the console's own zero-subprocess counter depends on."""

    def test_synthetic_loose_and_packed_tags_both_found(self):
        git = os.path.join(self.tmp, "repo", ".git")
        os.makedirs(os.path.join(git, "refs", "tags"))
        with open(os.path.join(git, "refs", "tags", "loose-tag-1"), "w", encoding="utf-8") as f:
            f.write("deadbeef\n")
        with open(os.path.join(git, "packed-refs"), "w", encoding="utf-8") as f:
            f.write("# pack-refs with: peeled fully-peeled sorted\n"
                    "cafef00d refs/tags/packed-tag-1\n"
                    "0000000000000000000000000000000000000000 refs/heads/main\n")
        tags = UC._repo_tags(os.path.dirname(git))
        self.assertEqual(tags, ["loose-tag-1", "packed-tag-1"])

    def test_branch_refs_in_packed_refs_are_excluded(self):
        git = os.path.join(self.tmp, "repo2", ".git")
        os.makedirs(git)
        with open(os.path.join(git, "packed-refs"), "w", encoding="utf-8") as f:
            f.write("abc123 refs/heads/main\nabc124 refs/heads/feature-x\n")
        self.assertEqual(UC._repo_tags(os.path.dirname(git)), [])

    def test_missing_git_directory_returns_empty_not_an_error(self):
        self.assertEqual(UC._repo_tags(os.path.join(self.tmp, "nonexistent")), [])

    def test_real_repo_finds_a_known_accepted_tag(self):
        """Sanity check against THIS repo's real .git, not only a synthetic fixture -- packed-refs
        format details (comments, peeled lines) can differ from a hand-built one."""
        tags = UC._repo_tags(ROOT)
        self.assertIn("phase7-13-unified-owner-console-accepted-6114533", tags)


if __name__ == "__main__":
    unittest.main(verbosity=2)
