#!/usr/bin/env python3
"""Phase 7.8 — offline, read-only Owner Operations Dashboard.

Every test runs OFFLINE and asserts the permanent Amazon / external-network boundary throughout: the
dashboard reads only the accepted Phase 7.3–7.7 outputs, connects to nothing, performs no Amazon
action, mutates no upstream artifact, and exports locally. Synthetic fixtures only — no real T2
runtime data is committed. Live HTTP servers used by the security tests bind to 127.0.0.1 on an
ephemeral port and are torn down in tearDown.

Synthetic pipelines are built by chaining the accepted authorities themselves (Phase 7.3 promoted
output -> 7.4 review -> 7.5 package -> 7.6 tracker -> 7.7 follow-up), so the dashboard is validated
against real upstream bytes, not hand-authored approximations.
"""
import ast
import hashlib
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout, redirect_stderr
import io
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from production import phase7_owner_operations_dashboard as OD   # noqa: E402
from production import phase7_owner_dashboard as D               # noqa: E402
from production import phase7_ads_analysis as AA                 # noqa: E402
from production import phase7_owner_decision_package as P5       # noqa: E402
from production import phase7_manual_action_tracker as TRK       # noqa: E402
from production import phase7_outcome_followup as FU             # noqa: E402
from tests import test_phase7_4_owner_dashboard as T4           # noqa: E402  make_row/build_analysis/write_promoted/reseal

REF = "2026-07-21"
REF77 = "2026-07-31"
WIN = ("2026-06-01", "2026-06-29", "2026-07-01", "2026-07-31")
COMPLETED = "2026-06-30"
NOW = lambda: datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)   # noqa: E731
APPROVED = "APPROVED_FOR_MANUAL_ACTION"

ENT = dict(campaign="C1", ad_group="G1", targeting="wasteful", customer_search_term="wasteful",
           match_type=None, currency="USD")
DEF_BEFORE = dict(owner_review_label=AA.RL_NEGATIVE, primary_classification="HIGH_SPEND_NO_SALES",
                  start_date="2026-06-15", end_date="2026-06-15",
                  impressions=800, clicks=20, spend="25.00", sales="0.00", orders=0, units=0)
DEF_AFTER = dict(owner_review_label="INSUFFICIENT_EVIDENCE",
                 start_date="2026-07-15", end_date="2026-07-15",
                 impressions=800, clicks=20, spend="25.00", sales="120.00", orders=6, units=6)


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def tree_hash(root):
    """A deterministic hash + inventory of a directory tree (path + bytes)."""
    h = hashlib.sha256()
    inv = []
    if not os.path.isdir(root):
        return "MISSING", []
    for dp, dn, fn in os.walk(root):
        dn.sort()
        for f in sorted(fn):
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, root).replace("\\", "/")
            with open(p, "rb") as fh:
                data = fh.read()
            h.update(rel.encode()); h.update(data)
            inv.append(rel)
    return h.hexdigest(), sorted(inv)


# ---------------------------------------------------------------- raw HTTP (for method/host/traversal)
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


def http(url, *, method="GET", headers=None, data=None):
    req = urllib.request.Request(url, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if data is not None:
        req.data = data
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p78-")
        self._n = 0
        self._servers = []

    def tearDown(self):
        for s in self._servers:
            try:
                s.shutdown(); s.server_close()
            except Exception:
                pass
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def newdir(self, tag):
        self._n += 1
        return os.path.join(self.tmp, f"{tag}-{self._n}")

    # ---- pipeline builders (chain the accepted authorities) ------------------------------------
    def promote(self, rows, *, mutate=None, currencies=("USD",)):
        analysis = T4.build_analysis(rows, currencies=currencies)
        if mutate:
            mutate(analysis)
        return T4.write_promoted(self.newdir("p73"), analysis)

    def review(self, p73, approvals, *, status=APPROVED):
        p74 = self.newdir("p74")
        src = D.load_source(p73)
        views = D.build_views(src)
        D.save_review(p74, src, views,
                      [{"entity_id": "st:" + lh, "review_status": status, "owner_note": "n"}
                       for lh in approvals], now=NOW)
        return p74

    def package(self, p73, p74, *, now=NOW):
        p75 = self.newdir("p75")
        r5 = P5.run(p75, p73, p74, REF, now=now)
        return p75, r5

    def item_ids(self, r5):
        items = json.loads(open(os.path.join(r5["package_dir"], "manual_action_checklist.json"),
                                encoding="utf-8").read())["items"]
        return [it["package_item_id"] for it in items]

    def tracker_init(self, p75, pkg_id, *, now=NOW):
        base76 = self.newdir("p76")
        TRK.run_initialize(base76, p75, REF, package_id=pkg_id, now=now)
        return base76

    def complete(self, base76, p75, pkg_id, item_id, *, now=NOW):
        return TRK.run_update(base76, p75, REF, package_id=pkg_id, package_item_id=item_id,
                              status=TRK.S_COMPLETED,
                              updates=dict(owner_note="done manually", owner_checked_date=COMPLETED,
                                           owner_completed_date=COMPLETED),
                              confirm_check=True, confirm_action=True, now=now)

    def followup(self, p73, base76, p75, *, windows=WIN, ref=REF77, **kw):
        base77 = self.newdir("p77")
        FU.run(base77, p73, base76, p75, ref, windows, **kw)
        return base77

    def config(self, *, p73, p74, p75, p76, p77, base=None, **kw):
        return OD.Config(base or self.newdir("p78"), p73, p74, p75, p76, p77, **kw)

    # ---- three canonical pipelines -------------------------------------------------------------
    def empty_pipeline(self, **cfgkw):
        """Mirror of real T2: source present, one DEFERRED review, empty package, empty tracker,
        empty follow-up -> dashboard READY_EMPTY."""
        b = T4.make_row(1, **dict(ENT, **DEF_BEFORE))
        a = T4.make_row(2, **dict(ENT, **DEF_AFTER))
        p73 = self.promote([b, a])
        p74 = self.review(p73, [b["lineage_hash"]], status="DEFERRED")
        p75, r5 = self.package(p73, p74)
        base76 = self.tracker_init(p75, r5["package_id"])
        base77 = self.followup(p73, base76, p75)
        return self.config(p73=p73, p74=p74, p75=p75, p76=base76, p77=base77, **cfgkw), r5

    def full_pipeline(self, **cfgkw):
        """A fully populated pipeline: one approved -> eligible action -> completed -> eligible
        follow-up -> dashboard READY."""
        b = T4.make_row(1, **dict(ENT, **DEF_BEFORE))
        a = T4.make_row(2, **dict(ENT, **DEF_AFTER))
        p73 = self.promote([b, a])
        p74 = self.review(p73, [b["lineage_hash"]])
        p75, r5 = self.package(p73, p74)
        self.assertEqual(r5["readiness"], P5.PKG_READY, r5)
        iid = self.item_ids(r5)[0]
        base76 = self.tracker_init(p75, r5["package_id"])
        self.complete(base76, p75, r5["package_id"], iid)
        base77 = self.followup(p73, base76, p75)
        return self.config(p73=p73, p74=p74, p75=p75, p76=base76, p77=base77, **cfgkw), r5

    def analysis_source(self, n=12):
        rows = [T4.make_row(i, campaign="C%d" % (i % 3), customer_search_term="term%02d" % i)
                for i in range(n)]
        return self.promote(rows)

    def serve(self, config):
        srv = OD.build_server(config, host="127.0.0.1", port=0, now=NOW)
        self._servers.append(srv)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return "http://127.0.0.1:%d" % srv.server_address[1], srv

    def getj(self, url):
        s, h, b = http(url)
        return s, json.loads(b.decode("utf-8"))


# ================================================================ loopback / host / port
class LoopbackHost(Base):
    def test_ipv4_loopback_accepted(self):
        self.assertTrue(OD.is_loopback_host("127.0.0.1"))
        self.assertEqual(OD.validate_host("127.0.0.1"), "127.0.0.1")

    def test_ipv4_loopback_range_accepted(self):
        self.assertTrue(OD.is_loopback_host("127.5.6.7"))

    def test_ipv6_loopback_accepted(self):
        self.assertTrue(OD.is_loopback_host("::1"))
        self.assertTrue(OD.is_loopback_host("[::1]"))

    def test_localhost_accepted_as_loopback(self):
        self.assertTrue(OD.is_loopback_host("localhost"))

    def test_zero_ipv4_rejected(self):
        self.assertFalse(OD.is_loopback_host("0.0.0.0"))
        with self.assertRaises(OD.OperationsError):
            OD.validate_host("0.0.0.0")

    def test_unspecified_ipv6_rejected(self):
        self.assertFalse(OD.is_loopback_host("::"))
        with self.assertRaises(OD.OperationsError):
            OD.validate_host("::")

    def test_lan_address_rejected(self):
        for lan in ("192.168.1.10", "10.0.0.5", "172.16.4.4"):
            self.assertFalse(OD.is_loopback_host(lan), lan)

    def test_public_address_rejected(self):
        self.assertFalse(OD.is_loopback_host("8.8.8.8"))
        self.assertFalse(OD.is_loopback_host("93.184.216.34"))

    def test_invalid_host_rejected_without_network(self):
        # a non-loopback DNS name is refused WITHOUT a resolution attempt.
        self.assertFalse(OD.is_loopback_host("example.com"))
        self.assertFalse(OD.is_loopback_host("not a host!!"))
        self.assertFalse(OD.is_loopback_host(""))
        self.assertFalse(OD.is_loopback_host(None))

    def test_invalid_port_rejected(self):
        for bad in (-1, 70000, 999999, "abc", None):
            with self.assertRaises(OD.OperationsError):
                OD.validate_port(bad)

    def test_valid_port_accepted(self):
        self.assertEqual(OD.validate_port(8780), 8780)
        self.assertEqual(OD.validate_port("0"), 0)

    def test_build_server_refuses_nonloopback(self):
        cfg, _ = self.empty_pipeline()
        with self.assertRaises(OD.OperationsError):
            OD.build_server(cfg, host="0.0.0.0", port=0)


# ================================================================ CLI
class Cli(Base):
    def run_cli(self, argv, serve=False):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = OD.main(argv, serve=serve)
        return rc, out.getvalue(), err.getvalue()

    def base_args(self, cfg, *extra):
        return ["--base-dir", cfg.base_dir, "--phase7-3-dir", cfg.phase7_3_dir,
                "--phase7-4-dir", cfg.phase7_4_dir, "--phase7-5-dir", cfg.phase7_5_dir,
                "--phase7-6-dir", cfg.phase7_6_dir, "--phase7-7-dir", cfg.phase7_7_dir] + list(extra)

    def test_help(self):
        with self.assertRaises(SystemExit):
            OD.build_arg_parser().parse_args(["--help"])

    def test_required_arguments_have_defaults(self):
        a = OD.build_arg_parser().parse_args([])
        self.assertTrue(a.phase7_3_dir and a.phase7_5_dir and a.phase7_7_dir)
        self.assertEqual(a.port, OD.DEFAULT_PORT)

    def test_validate_only_empty_pipeline_zero(self):
        cfg, _ = self.empty_pipeline()
        rc, out, err = self.run_cli(self.base_args(cfg, "--validate-only"))
        self.assertEqual(rc, 0, err)
        self.assertIn("dashboard_readiness=" + OD.READY_EMPTY, out)
        self.assertIn("amazon_connections=0", out)
        self.assertIn("external_network_calls=0", out)

    def test_validate_only_full_pipeline_ready(self):
        cfg, _ = self.full_pipeline()
        rc, out, err = self.run_cli(self.base_args(cfg, "--validate-only"))
        self.assertEqual(rc, 0, err)
        self.assertIn("dashboard_readiness=" + OD.READY, out)

    def test_validate_only_creates_no_files(self):
        cfg, _ = self.empty_pipeline()
        before = os.path.exists(cfg.base_dir)
        rc, out, err = self.run_cli(self.base_args(cfg, "--validate-only"))
        self.assertEqual(rc, 0)
        self.assertEqual(os.path.exists(cfg.base_dir), before)  # unchanged (not created)
        self.assertFalse(os.path.isdir(cfg.base_dir))

    def test_validate_only_blocked_source_nonzero(self):
        cfg = self.config(p73=self.newdir("nope"), p74=self.newdir("nope4"),
                          p75=self.newdir("nope5"), p76=self.newdir("nope6"), p77=self.newdir("nope7"))
        rc, out, err = self.run_cli(self.base_args(cfg, "--validate-only"))
        self.assertNotEqual(rc, 0)
        self.assertIn("SESSION7_8_SOURCE", out)

    def test_startup_summary_no_absolute_paths(self):
        cfg, _ = self.empty_pipeline()
        rc, out, err = self.run_cli(self.base_args(cfg, "--validate-only"))
        self.assertNotIn(self.tmp, out)
        self.assertNotIn(os.sep + "p73", out)

    def test_snapshot_writes_only_under_7_8(self):
        cfg, _ = self.empty_pipeline()
        before = {r: tree_hash(d) for r, d in
                  [("3", cfg.phase7_3_dir), ("5", cfg.phase7_5_dir), ("7", cfg.phase7_7_dir)]}
        rc, out, err = self.run_cli(self.base_args(cfg, "--snapshot-format", "json"))
        self.assertEqual(rc, 0, err)
        snap = os.path.join(cfg.base_dir, "snapshots", "operations_snapshot.json")
        self.assertTrue(os.path.isfile(snap))
        after = {r: tree_hash(d) for r, d in
                 [("3", cfg.phase7_3_dir), ("5", cfg.phase7_5_dir), ("7", cfg.phase7_7_dir)]}
        self.assertEqual(before, after)


# ================================================================ HTTP methods
class HttpMethods(Base):
    def setUp(self):
        super().setUp()
        cfg, _ = self.empty_pipeline()
        self.url, self.srv = self.serve(cfg)
        self.host = "127.0.0.1"
        self.port = self.srv.server_address[1]

    def test_get_allowed(self):
        s, _, _ = http(self.url + "/api/health")
        self.assertEqual(s, 200)

    def test_head_allowed(self):
        s, h, b = http(self.url + "/api/health", method="HEAD")
        self.assertEqual(s, 200)
        self.assertEqual(b, b"")

    def test_post_rejected(self):
        s, _, _ = http(self.url + "/api/overview", method="POST", data=b"{}")
        self.assertEqual(s, 405)

    def test_put_rejected(self):
        self.assertEqual(http(self.url + "/api/overview", method="PUT", data=b"{}")[0], 405)

    def test_patch_rejected(self):
        self.assertEqual(http(self.url + "/api/overview", method="PATCH", data=b"{}")[0], 405)

    def test_delete_rejected(self):
        self.assertEqual(http(self.url + "/api/overview", method="DELETE")[0], 405)

    def test_options_rejected(self):
        self.assertEqual(http(self.url + "/api/overview", method="OPTIONS")[0], 405)

    def test_trace_rejected(self):
        raw = raw_http(self.host, self.port,
                       b"TRACE /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        self.assertEqual(parse_http(raw)[0], 405)

    def test_connect_rejected(self):
        raw = raw_http(self.host, self.port,
                       b"CONNECT 127.0.0.1:80 HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        self.assertEqual(parse_http(raw)[0], 405)

    def test_unexpected_body_drained_keepalive(self):
        # a GET carrying a body, followed by a second request on the same connection, must not desync.
        req = (b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 5\r\n\r\nhello"
               b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        raw = raw_http(self.host, self.port, req)
        self.assertGreaterEqual(raw.count(b"HTTP/1.1 200"), 2)

    def test_post_body_rejected_not_interpreted(self):
        s, _, b = http(self.url + "/api/overview", method="POST", data=b'{"mutate":true}')
        self.assertEqual(s, 405)
        self.assertIn(b"METHOD_NOT_ALLOWED", b)


# ================================================================ HTTP security
class HttpSecurity(Base):
    def setUp(self):
        super().setUp()
        cfg, _ = self.empty_pipeline()
        self.url, self.srv = self.serve(cfg)
        self.host = "127.0.0.1"
        self.port = self.srv.server_address[1]

    def headers(self, path="/"):
        return http(self.url + path)[1]

    def test_static_index_served(self):
        s, h, b = http(self.url + "/")
        self.assertEqual(s, 200)
        self.assertIn("text/html", h["Content-Type"])
        self.assertIn(b"Owner Operations Dashboard", b)

    def test_css_served(self):
        s, h, b = http(self.url + "/dashboard.css")
        self.assertEqual(s, 200)
        self.assertIn("text/css", h["Content-Type"])

    def test_js_served(self):
        s, h, b = http(self.url + "/dashboard.js")
        self.assertEqual(s, 200)
        self.assertIn("javascript", h["Content-Type"])

    def test_unknown_static_404(self):
        self.assertEqual(http(self.url + "/nope.txt")[0], 404)

    def test_path_traversal_rejected(self):
        raw = raw_http(self.host, self.port,
                       b"GET /../etc/passwd HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        self.assertIn(parse_http(raw)[0], (400, 404))

    def test_encoded_traversal_rejected(self):
        raw = raw_http(self.host, self.port,
                       b"GET /%2e%2e/secret HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        self.assertEqual(parse_http(raw)[0], 404)

    def test_absolute_path_rejected(self):
        raw = raw_http(self.host, self.port,
                       b"GET /etc/passwd HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        self.assertEqual(parse_http(raw)[0], 404)

    def test_no_directory_listing(self):
        # the static subdir name is not a listable route.
        self.assertEqual(http(self.url + "/phase7_operations_dashboard_static/")[0], 404)

    def test_csp_header(self):
        csp = self.headers()["Content-Security-Policy"]
        self.assertIn("default-src 'self'", csp)
        self.assertIn("script-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("form-action 'none'", csp)
        self.assertIn("font-src 'none'", csp)
        self.assertNotIn("*", csp)

    def test_nosniff_header(self):
        self.assertEqual(self.headers()["X-Content-Type-Options"], "nosniff")

    def test_referrer_policy_header(self):
        self.assertEqual(self.headers()["Referrer-Policy"], "no-referrer")

    def test_frame_denied(self):
        self.assertEqual(self.headers()["X-Frame-Options"], "DENY")

    def test_permissions_policy_header(self):
        pp = self.headers()["Permissions-Policy"]
        self.assertIn("camera=()", pp)
        self.assertIn("geolocation=()", pp)

    def test_cache_control_header(self):
        self.assertEqual(self.headers()["Cache-Control"], "no-store")

    def test_no_cors_wildcard(self):
        h = self.headers("/api/overview")
        self.assertNotIn("Access-Control-Allow-Origin", h)

    def test_host_header_validation_ok(self):
        raw = raw_http(self.host, self.port,
                       ("GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nConnection: close\r\n\r\n"
                        % self.port).encode())
        self.assertEqual(parse_http(raw)[0], 200)

    def test_foreign_host_rejected(self):
        raw = raw_http(self.host, self.port,
                       b"GET /api/health HTTP/1.1\r\nHost: evil.example\r\nConnection: close\r\n\r\n")
        self.assertEqual(parse_http(raw)[0], 400)

    def test_missing_host_rejected(self):
        raw = raw_http(self.host, self.port, b"GET /api/health HTTP/1.1\r\nConnection: close\r\n\r\n")
        self.assertEqual(parse_http(raw)[0], 400)

    def test_no_inline_or_external_script_in_html(self):
        b = http(self.url + "/")[2].decode("utf-8")
        self.assertNotIn("<script>", b.lower())
        self.assertIn('src="/dashboard.js"', b)
        self.assertNotIn("http://", b)
        self.assertNotIn("https://", b)

    def test_no_external_refs_in_assets(self):
        for name in ("/dashboard.js", "/dashboard.css"):
            b = http(self.url + name)[2].decode("utf-8")
            for bad in ("http://", "https://", "cdn.", "googleapis", "unpkg", "jsdelivr",
                        "new WebSocket", "serviceWorker", "@font-face", "navigator.sendBeacon"):
                self.assertNotIn(bad, b, f"{bad} in {name}")

    def test_error_has_no_stack_trace_or_abs_path(self):
        s, h, b = http(self.url + "/api/unknownroute")
        self.assertEqual(s, 404)
        text = b.decode("utf-8")
        self.assertNotIn("Traceback", text)
        self.assertNotIn(self.tmp, text)


# ================================================================ source selection & blocking
class SourceSelection(Base):
    def model(self, cfg):
        return OD.build_operations_model(cfg, now=NOW)

    def test_valid_source_ready_empty(self):
        cfg, _ = self.empty_pipeline()
        m = self.model(cfg)
        self.assertEqual(m["readiness"], OD.READY_EMPTY)
        self.assertTrue(m["ok"])

    def test_missing_source_blocks(self):
        cfg = self.config(p73=self.newdir("x3"), p74=self.newdir("x4"), p75=self.newdir("x5"),
                          p76=self.newdir("x6"), p77=self.newdir("x7"))
        m = self.model(cfg)
        self.assertEqual(m["readiness"], OD.SOURCE_REQUIRED)
        self.assertFalse(m["ok"])

    def test_tampered_source_blocks(self):
        b = T4.make_row(1, **dict(ENT, **DEF_BEFORE))
        p73 = self.promote([b])
        # corrupt analysis.json bytes so the manifest hash no longer verifies.
        promoted = os.path.join(p73, "promoted")
        with open(os.path.join(promoted, "analysis.json"), "r+b") as f:
            data = bytearray(f.read()); data[10] ^= 0x5A; f.seek(0); f.write(data)
        cfg = self.config(p73=p73, p74=self.newdir("t4"), p75=self.newdir("t5"),
                          p76=self.newdir("t6"), p77=self.newdir("t7"))
        m = self.model(cfg)
        self.assertIn(m["readiness"], (OD.INTEGRITY_BLOCKED, OD.SOURCE_BLOCKED))
        self.assertFalse(m["ok"])

    def test_promoted_precedes_final(self):
        # write_promoted creates promoted/; DASH.resolve_source_dir prefers it. Confirm source dir.
        b = T4.make_row(1, **dict(ENT, **DEF_BEFORE))
        p73 = self.promote([b])
        src = D.load_source(p73)
        self.assertTrue(src.source_dir.replace("\\", "/").endswith("/promoted"))

    def test_malformed_review_state_blocks(self):
        b = T4.make_row(1, **dict(ENT, **DEF_BEFORE))
        p73 = self.promote([b])
        p74 = self.newdir("rv")
        os.makedirs(os.path.join(p74, "review_state"))
        with open(os.path.join(p74, "review_state", "review-state.json"), "w", encoding="utf-8") as f:
            f.write("{ not json ")
        cfg = self.config(p73=p73, p74=p74, p75=self.newdir("m5"), p76=self.newdir("m6"),
                          p77=self.newdir("m7"))
        m = self.model(cfg)
        self.assertEqual(m["readiness"], OD.REVIEW_STATE_BLOCKED)

    def test_duplicate_review_record_blocks(self):
        b = T4.make_row(1, **dict(ENT, **DEF_BEFORE))
        p73 = self.promote([b])
        p74 = self.newdir("dup")
        os.makedirs(os.path.join(p74, "review_state"))
        # duplicate keys in the JSON object -> strict loader must block.
        with open(os.path.join(p74, "review_state", "review-state.json"), "w", encoding="utf-8") as f:
            f.write('{"schema_version":"%s","records":{"st:a":{},"st:a":{}}}' % D.REVIEW_STATE_SCHEMA)
        cfg = self.config(p73=p73, p74=p74, p75=self.newdir("d5"), p76=self.newdir("d6"),
                          p77=self.newdir("d7"))
        m = self.model(cfg)
        self.assertEqual(m["readiness"], OD.REVIEW_STATE_BLOCKED)

    def test_valid_empty_package_displayed(self):
        cfg, r5 = self.empty_pipeline()
        m = self.model(cfg)
        dp = m["decision_packages"]
        self.assertEqual(dp["status"], OD.PKG_SELECTED)
        self.assertEqual(dp["package"]["package_id"], r5["package_id"])
        self.assertEqual(dp["counts"]["eligible_actions"], 0)

    def test_valid_populated_package(self):
        cfg, r5 = self.full_pipeline()
        m = self.model(cfg)
        self.assertEqual(m["decision_packages"]["status"], OD.PKG_SELECTED)
        self.assertGreaterEqual(m["decision_packages"]["counts"]["eligible_actions"], 1)

    def test_no_current_package_partial(self):
        # source with no matching package -> READY_PARTIAL (package NOT_AVAILABLE), not blocking.
        b = T4.make_row(1, **dict(ENT, **DEF_BEFORE))
        p73 = self.promote([b])
        cfg = self.config(p73=p73, p74=self.newdir("np4"), p75=self.newdir("np5"),
                          p76=self.newdir("np6"), p77=self.newdir("np7"))
        m = self.model(cfg)
        self.assertEqual(m["readiness"], OD.READY_PARTIAL)
        self.assertEqual(m["decision_packages"]["status"], OD.PKG_NOT_AVAILABLE)

    def test_tampered_package_blocks(self):
        cfg, r5 = self.full_pipeline()
        # corrupt a package artifact so integrity fails on the CURRENT package.
        pkgdir = os.path.join(cfg.phase7_5_dir, "packages", r5["package_id"])
        with open(os.path.join(pkgdir, "manual_action_checklist.json"), "ab") as f:
            f.write(b"\n// tamper\n")
        m = self.model(cfg)
        self.assertEqual(m["readiness"], OD.DECISION_PACKAGE_BLOCKED)
        self.assertEqual(m["decision_packages"]["status"], OD.PKG_BLOCKED)

    def test_duplicate_identical_packages_deduplicate(self):
        cfg, r5 = self.full_pipeline()
        # copy the package to a second directory (byte-identical) -> collapses, still SELECTED.
        import shutil
        src = os.path.join(cfg.phase7_5_dir, "packages", r5["package_id"])
        shutil.copytree(src, os.path.join(cfg.phase7_5_dir, "packages", "copy-" + r5["package_id"]))
        m = self.model(cfg)
        self.assertEqual(m["decision_packages"]["status"], OD.PKG_SELECTED)
        self.assertGreaterEqual(m["decision_packages"]["candidate_count"], 2)

    def test_conflicting_packages_block(self):
        cfg, r5 = self.full_pipeline()
        # forge a second current package (same lineage, different content hash) -> conflict.
        import shutil
        src = os.path.join(cfg.phase7_5_dir, "packages", r5["package_id"])
        dst = os.path.join(cfg.phase7_5_dir, "packages", "conflict")
        shutil.copytree(src, dst)
        mpath = os.path.join(dst, "package_manifest.json")
        man = json.loads(open(mpath, encoding="utf-8").read())
        man["package_content_sha256"] = "f" * 64   # same lineage keys, different content hash
        with open(mpath, "w", encoding="utf-8") as f:
            f.write(D.canonical_json(man))
        # re-hash the manifest so load_package integrity of OTHER artifacts still passes; the
        # manifest itself is not in artifact_sha256, so integrity_ok stays True.
        m = self.model(cfg)
        self.assertEqual(m["readiness"], OD.DECISION_PACKAGE_BLOCKED)
        self.assertEqual(m["decision_packages"]["status"], OD.PKG_CONFLICT)

    def test_valid_empty_tracker(self):
        cfg, _ = self.empty_pipeline()
        m = self.model(cfg)
        self.assertEqual(m["manual_actions"]["status"], OD.TRACKER_OK)
        self.assertEqual(m["manual_actions"]["counts"]["tracker_records"], 0)

    def test_valid_populated_tracker(self):
        cfg, _ = self.full_pipeline()
        m = self.model(cfg)
        self.assertEqual(m["manual_actions"]["counts"]["tracker_records"], 1)
        self.assertEqual(m["manual_actions"]["counts"]["manually_completed"], 1)

    def test_broken_tracker_history_blocks(self):
        cfg, _ = self.full_pipeline()
        hist = os.path.join(cfg.phase7_6_dir, "action_state", "history.jsonl")
        with open(hist, "a", encoding="utf-8") as f:
            f.write('{"event_seq":999,"tracker_record_id":"x","local_revision":1,'
                    '"previous_record_hash":null,"record_content_sha256":"z","record":{}}\n')
        m = self.model(cfg)
        self.assertEqual(m["readiness"], OD.TRACKER_BLOCKED)

    def test_current_history_mismatch_blocks(self):
        cfg, _ = self.full_pipeline()
        cur = os.path.join(cfg.phase7_6_dir, "action_state", "current.json")
        doc = json.loads(open(cur, encoding="utf-8").read())
        doc["records"] = {}   # disagree with history
        with open(cur, "w", encoding="utf-8") as f:
            f.write(D.canonical_json(doc))
        m = self.model(cfg)
        self.assertEqual(m["readiness"], OD.TRACKER_BLOCKED)

    def test_valid_empty_followup(self):
        cfg, _ = self.empty_pipeline()
        m = self.model(cfg)
        self.assertEqual(m["outcomes"]["status"], OD.FU_SELECTED)
        self.assertEqual(m["outcomes"]["counts"]["eligible_followups"], 0)

    def test_valid_populated_followup(self):
        cfg, _ = self.full_pipeline()
        m = self.model(cfg)
        self.assertEqual(m["outcomes"]["status"], OD.FU_SELECTED)
        self.assertGreaterEqual(m["outcomes"]["counts"]["eligible_followups"], 1)

    def test_tampered_followup_blocks(self):
        cfg, _ = self.full_pipeline()
        fdir = os.path.join(cfg.phase7_7_dir, "followups")
        name = os.listdir(fdir)[0]
        with open(os.path.join(fdir, name, "outcome_status.json"), "ab") as f:
            f.write(b"\n tamper \n")
        m = self.model(cfg)
        self.assertEqual(m["readiness"], OD.FOLLOWUP_BLOCKED)
        self.assertEqual(m["outcomes"]["status"], OD.FU_BLOCKED)

    def test_multiple_followups_require_selection(self):
        cfg, _ = self.full_pipeline()
        # forge a second current follow-up with the same lineage but a different id.
        import shutil
        fdir = os.path.join(cfg.phase7_7_dir, "followups")
        name = os.listdir(fdir)[0]
        dst = os.path.join(fdir, "followup-second00000000")
        shutil.copytree(os.path.join(fdir, name), dst)
        mpath = os.path.join(dst, "followup_manifest.json")
        man = json.loads(open(mpath, encoding="utf-8").read())
        man["followup_package_id"] = "followup-second00000000"
        newbytes = (D.canonical_json(man) + "\n").encode("utf-8")
        with open(mpath, "wb") as f:
            f.write(newbytes)
        # keep manifest self-consistent: artifact_sha256 excludes the manifest itself.
        m = self.model(cfg)
        self.assertEqual(m["readiness"], OD.SELECTION_REQUIRED)
        self.assertEqual(m["outcomes"]["status"], OD.FU_SELECTION_REQUIRED)

    def test_explicit_followup_id_selection(self):
        cfg, _ = self.full_pipeline()
        fid = os.listdir(os.path.join(cfg.phase7_7_dir, "followups"))[0]
        cfg.followup_id = fid
        m = self.model(cfg)
        self.assertEqual(m["outcomes"]["status"], OD.FU_SELECTED)
        self.assertEqual(m["outcomes"]["followup"]["followup_id"], fid)

    def test_missing_explicit_followup_id_blocks_safely(self):
        cfg, _ = self.full_pipeline()
        cfg.followup_id = "followup-doesnotexist00"
        m = self.model(cfg)
        self.assertEqual(m["outcomes"]["status"], OD.FU_NOT_AVAILABLE)

    def test_explicit_package_id_selection(self):
        cfg, r5 = self.full_pipeline()
        cfg.package_id = r5["package_id"]
        m = self.model(cfg)
        self.assertEqual(m["decision_packages"]["status"], OD.PKG_SELECTED)


# ================================================================ API schemas
class ApiSchemas(Base):
    def setUp(self):
        super().setUp()
        cfg, self.r5 = self.empty_pipeline()
        self.url, self.srv = self.serve(cfg)

    def test_health_schema(self):
        s, j = self.getj(self.url + "/api/health")
        self.assertEqual(s, 200)
        for k in ("stage_id", "dashboard_readiness", "amazon_counters", "this_session_never"):
            self.assertIn(k, j)
        self.assertTrue(all(v == 0 for v in j["amazon_counters"].values()))

    def test_overview_schema(self):
        s, j = self.getj(self.url + "/api/overview")
        ov = j["overview"]
        for k in ("source_rows", "analyzed_rows", "review_state_records", "eligible_decisions",
                  "tracker_records", "eligible_followups", "attention_item_count",
                  "amazon_connections", "external_network_calls"):
            self.assertIn(k, ov)

    def test_analysis_schema(self):
        s, j = self.getj(self.url + "/api/analysis")
        for k in ("data", "page", "page_size", "total", "total_pages", "filters"):
            self.assertIn(k, j)

    def test_reviews_schema(self):
        s, j = self.getj(self.url + "/api/reviews")
        self.assertIn("data", j)
        self.assertIn("Phase 7.4", j["notice"])

    def test_decision_schema(self):
        s, j = self.getj(self.url + "/api/decision-packages")
        for k in ("data", "package", "status", "excluded_items", "counts"):
            self.assertIn(k, j)

    def test_manual_actions_schema(self):
        s, j = self.getj(self.url + "/api/manual-actions")
        self.assertIn("data", j)
        self.assertIn("owner-entered", j["notice"])

    def test_outcomes_schema(self):
        s, j = self.getj(self.url + "/api/outcomes")
        for k in ("data", "followup", "status", "excluded", "causation_disclaimer"):
            self.assertIn(k, j)
        self.assertIn("causation", j["causation_disclaimer"].lower())

    def test_attention_schema(self):
        s, j = self.getj(self.url + "/api/attention")
        self.assertIn("data", j)

    def test_lineage_schema(self):
        s, j = self.getj(self.url + "/api/lineage")
        ln = j["lineage"]
        for k in ("phase7_3", "phase7_4", "phase7_5", "phase7_6", "phase7_7", "input_directories"):
            self.assertIn(k, ln)

    def test_unknown_api_route_404(self):
        self.assertEqual(http(self.url + "/api/does-not-exist")[0], 404)


# ================================================================ query / pagination
class QueryPagination(Base):
    def setUp(self):
        super().setUp()
        p73 = self.analysis_source(12)
        cfg = self.config(p73=p73, p74=self.newdir("q4"), p75=self.newdir("q5"),
                          p76=self.newdir("q6"), p77=self.newdir("q7"))
        self.url, self.srv = self.serve(cfg)

    def test_pagination(self):
        s, j = self.getj(self.url + "/api/analysis?page=1&page_size=5")
        self.assertEqual(len(j["data"]), 5)
        self.assertEqual(j["total"], 12)
        s2, j2 = self.getj(self.url + "/api/analysis?page=3&page_size=5")
        self.assertEqual(len(j2["data"]), 2)

    def test_deterministic_pagination(self):
        a = self.getj(self.url + "/api/analysis?page=2&page_size=4")[1]["data"]
        b = self.getj(self.url + "/api/analysis?page=2&page_size=4")[1]["data"]
        self.assertEqual([r["row_id"] for r in a], [r["row_id"] for r in b])

    def test_deterministic_sorting(self):
        a = self.getj(self.url + "/api/analysis?sort=customer_search_term&direction=asc&page_size=100")[1]["data"]
        b = self.getj(self.url + "/api/analysis?sort=customer_search_term&direction=asc&page_size=100")[1]["data"]
        self.assertEqual([r["row_id"] for r in a], [r["row_id"] for r in b])
        terms = [r["customer_search_term"] for r in a]
        self.assertEqual(terms, sorted(terms))

    def test_deterministic_filtering(self):
        a = self.getj(self.url + "/api/analysis?campaign=C1&page_size=100")[1]["data"]
        b = self.getj(self.url + "/api/analysis?campaign=C1&page_size=100")[1]["data"]
        self.assertEqual([r["row_id"] for r in a], [r["row_id"] for r in b])
        self.assertTrue(all(r["campaign"] == "C1" for r in a))

    def test_unknown_query_param_rejected(self):
        self.assertEqual(http(self.url + "/api/analysis?bogus=1")[0], 400)

    def test_invalid_sort_rejected(self):
        self.assertEqual(http(self.url + "/api/analysis?sort=not_a_column")[0], 400)

    def test_invalid_direction_rejected(self):
        self.assertEqual(http(self.url + "/api/analysis?sort=campaign&direction=sideways")[0], 400)

    def test_invalid_page_rejected(self):
        self.assertEqual(http(self.url + "/api/analysis?page=0")[0], 400)
        self.assertEqual(http(self.url + "/api/analysis?page=-3")[0], 400)
        self.assertEqual(http(self.url + "/api/analysis?page=abc")[0], 400)

    def test_invalid_page_size_rejected(self):
        self.assertEqual(http(self.url + "/api/analysis?page_size=0")[0], 400)
        self.assertEqual(http(self.url + "/api/analysis?page_size=xyz")[0], 400)

    def test_page_size_maximum_enforced(self):
        s, j = self.getj(self.url + "/api/analysis?page_size=99999")
        self.assertEqual(s, 200)
        self.assertLessEqual(j["page_size"], OD.MAX_PAGE_SIZE)

    def test_query_value_too_long(self):
        # a per-value length cap (well under the URI length limit) returns 400, not 414.
        self.assertEqual(http(self.url + "/api/analysis?filter=" + "x" * 300)[0], 400)

    def test_uri_too_long(self):
        self.assertEqual(http(self.url + "/api/analysis?filter=" + "x" * 5000)[0], 414)


# ================================================================ stable identity
class StableIdentity(Base):
    def setUp(self):
        super().setUp()
        self.p73 = self.analysis_source(10)
        self.cfg = self.config(p73=self.p73, p74=self.newdir("s4"), p75=self.newdir("s5"),
                               p76=self.newdir("s6"), p77=self.newdir("s7"))

    def test_stable_across_two_model_builds(self):
        m1 = OD.build_operations_model(self.cfg, now=NOW)
        m2 = OD.build_operations_model(self.cfg, now=NOW)
        self.assertEqual([r["row_id"] for r in m1["analysis"]["rows"]],
                         [r["row_id"] for r in m2["analysis"]["rows"]])

    def test_sorting_does_not_alter_ids(self):
        url, _ = self.serve(self.cfg)
        asc = self.getj(url + "/api/analysis?sort=campaign&direction=asc&page_size=100")[1]["data"]
        desc = self.getj(url + "/api/analysis?sort=campaign&direction=desc&page_size=100")[1]["data"]
        by_entity_asc = {r["entity_id"]: r["row_id"] for r in asc}
        by_entity_desc = {r["entity_id"]: r["row_id"] for r in desc}
        self.assertEqual(by_entity_asc, by_entity_desc)

    def test_filtering_does_not_alter_ids(self):
        url, _ = self.serve(self.cfg)
        all_rows = self.getj(url + "/api/analysis?page_size=100")[1]["data"]
        one = self.getj(url + "/api/analysis?campaign=C1&page_size=100")[1]["data"]
        m = {r["entity_id"]: r["row_id"] for r in all_rows}
        for r in one:
            self.assertEqual(r["row_id"], m[r["entity_id"]])

    def test_row_ids_are_content_derived(self):
        m = OD.build_operations_model(self.cfg, now=NOW)
        r = m["analysis"]["rows"][0]
        self.assertTrue(r["row_id"].startswith("op-"))
        self.assertEqual(len(r["row_id"]), 3 + 24)
        # deterministic pure function of (source identity, view, entity id, content hash)
        self.assertEqual(OD._op_id(m["source_identity_sha256"], "analysis", r["entity_id"], "x"),
                         OD._op_id(m["source_identity_sha256"], "analysis", r["entity_id"], "x"))

    def test_full_pipeline_row_ids_present(self):
        cfg, _ = self.full_pipeline()
        m = OD.build_operations_model(cfg, now=NOW)
        self.assertTrue(m["reviews"]["records"][0]["row_id"].startswith("op-"))
        self.assertTrue(m["decision_packages"]["eligible_items"][0]["row_id"].startswith("op-"))
        self.assertTrue(m["manual_actions"]["records"][0]["row_id"].startswith("op-"))
        self.assertTrue(m["outcomes"]["followups"][0]["row_id"].startswith("op-"))


# ================================================================ no invented analytics
class NoInventedAnalytics(Base):
    def model_json(self, cfg):
        return OD.export_json(OD.build_operations_model(cfg, now=NOW))

    def test_no_invented_metric_keys(self):
        cfg, _ = self.full_pipeline()
        blob = self.model_json(cfg)
        for bad in ("recommended_bid", "new_bid", "suggested_bid", "new_budget", "target_acos_new",
                    "urgency_score", "priority_score", "risk_score", "health_score",
                    "effectiveness_score", "recommended_budget"):
            self.assertNotIn(bad, blob, bad)

    def test_no_causal_language_outside_disclaimer(self):
        cfg, _ = self.full_pipeline()
        m = OD.build_operations_model(cfg, now=NOW)
        self.assertFalse(m["outcomes"]["followup"]["causation_asserted"])
        # outcome rows never carry a "caused" verdict
        for r in m["outcomes"]["followups"]:
            self.assertNotIn("caused_by", r)
            self.assertNotIn("causal", json.dumps(r).lower())

    def test_no_cross_currency_aggregate_field(self):
        cfg, _ = self.full_pipeline()
        blob = self.model_json(cfg)
        for bad in ("total_spend", "total_sales", "sum_spend", "combined_spend", "aggregate_acos",
                    "blended_acos"):
            self.assertNotIn(bad, blob, bad)

    def test_currency_separation_two_currencies(self):
        r_usd = T4.make_row(1, **dict(ENT, **DEF_BEFORE))
        r_eur = T4.make_row(2, **dict(ENT, currency="EUR", campaign="C2", customer_search_term="euro",
                                      **{k: v for k, v in DEF_BEFORE.items()}))
        p73 = self.promote([r_usd, r_eur], currencies=("USD", "EUR"))
        cfg = self.config(p73=p73, p74=self.newdir("c4"), p75=self.newdir("c5"),
                          p76=self.newdir("c6"), p77=self.newdir("c7"))
        m = OD.build_operations_model(cfg, now=NOW)
        self.assertEqual(sorted(m["overview"]["currencies"]), ["EUR", "USD"])

    def test_missing_values_remain_missing(self):
        r = T4.make_row(1, **dict(ENT, **DEF_BEFORE))
        r["sales"] = None
        p73 = self.promote([r])
        cfg = self.config(p73=p73, p74=self.newdir("mm4"), p75=self.newdir("mm5"),
                          p76=self.newdir("mm6"), p77=self.newdir("mm7"))
        m = OD.build_operations_model(cfg, now=NOW)
        row = m["analysis"]["rows"][0]
        self.assertIsNone(row["sales"])   # missing, never coerced to 0

    def test_no_authoritative_float(self):
        cfg, _ = self.full_pipeline()
        m = OD.build_operations_model(cfg, now=NOW)

        def has_float(o):
            if isinstance(o, bool):
                return False
            if isinstance(o, float):
                return True
            if isinstance(o, dict):
                return any(has_float(v) for v in o.values())
            if isinstance(o, (list, tuple)):
                return any(has_float(v) for v in o)
            return False
        self.assertFalse(has_float(m))

    def test_no_source_internal_fields_leaked(self):
        cfg, _ = self.full_pipeline()
        blob = OD.export_json(OD.build_operations_model(cfg, now=NOW))
        for internal in ("source_file_sha256", "source_line_number", "source_row_number"):
            self.assertNotIn(internal, blob, internal)


# ================================================================ exports
class Exports(Base):
    def model(self, cfg):
        return OD.build_operations_model(cfg, now=NOW)

    def test_json_export(self):
        cfg, _ = self.full_pipeline()
        blob = OD.export_json(self.model(cfg))
        doc = json.loads(blob)
        self.assertIn("meta", doc)
        self.assertIn("OFFLINE OWNER OPERATIONS SNAPSHOT", doc["meta"]["disclaimer"][0])
        self.assertFalse(doc["meta"]["amazon_action_performed"])
        self.assertFalse(doc["meta"]["is_amazon_upload_file"])

    def test_tsv_export(self):
        cfg, _ = self.full_pipeline()
        tsv = OD.export_tsv(self.model(cfg))
        self.assertIn("OFFLINE OWNER OPERATIONS SNAPSHOT", tsv)

    def test_markdown_export(self):
        cfg, _ = self.full_pipeline()
        md = OD.export_markdown(self.model(cfg))
        self.assertIn("Offline Owner Operations Snapshot", md)
        self.assertIn("not an amazon upload", md.lower())

    def test_export_disclaimer_all_formats(self):
        cfg, _ = self.empty_pipeline()
        m = self.model(cfg)
        for fn in (OD.export_json, OD.export_tsv, OD.export_markdown):
            text = fn(m).lower()
            self.assertIn("no amazon action was performed", text)
            self.assertIn("causation", text)

    def test_export_not_amazon_upload_format(self):
        cfg, _ = self.full_pipeline()
        # a genuine Amazon bulk-upload template carries these signature column headers; assert their
        # absence (multi-word markers that cannot collide with our own descriptive text).
        blob = OD.export_json(self.model(cfg)) + OD.export_tsv(self.model(cfg))
        for header in ("Sponsored Products Campaigns", "Campaign Id", "Ad Group Id", "Keyword Id",
                       "Product Targeting Expression", "Campaign Daily Budget", "Max Bid"):
            self.assertNotIn(header, blob, header)

    def test_equal_tsv_columns(self):
        cfg, _ = self.full_pipeline()
        tsv = OD.export_tsv(self.model(cfg))
        widths = set()
        for ln in tsv.splitlines():
            if ln.startswith("#") or not ln.strip():
                continue
            widths.add(len(ln.split("\t")))
        self.assertEqual(len(widths), 1, widths)
        self.assertEqual(widths.pop(), len(OD._OPS_COLUMNS))

    def test_deterministic_json_export(self):
        cfg, _ = self.full_pipeline()
        self.assertEqual(OD.export_json(self.model(cfg)), OD.export_json(self.model(cfg)))

    def test_deterministic_tsv_export(self):
        cfg, _ = self.full_pipeline()
        self.assertEqual(OD.export_tsv(self.model(cfg)), OD.export_tsv(self.model(cfg)))

    def test_deterministic_md_export(self):
        cfg, _ = self.full_pipeline()
        self.assertEqual(OD.export_markdown(self.model(cfg)), OD.export_markdown(self.model(cfg)))

    # ---- formula-injection / TSV safety (reuses the accepted Phase 7.4 rule) -------------------
    def test_formula_injection_equals(self):
        self.assertTrue(OD._TSV_CELL("=SUM(A1)").startswith("'"))

    def test_formula_injection_plus(self):
        self.assertTrue(OD._TSV_CELL("+1+2").startswith("'"))

    def test_formula_injection_minus_non_numeric(self):
        self.assertTrue(OD._TSV_CELL("-cmd|calc").startswith("'"))

    def test_formula_injection_at(self):
        self.assertTrue(OD._TSV_CELL("@x").startswith("'"))

    def test_formula_injection_tab_cr_lf_stripped(self):
        cell = OD._TSV_CELL("a\tb\rc\nd")
        self.assertNotIn("\t", cell)
        self.assertNotIn("\r", cell)
        self.assertNotIn("\n", cell)

    def test_negative_decimal_preserved(self):
        self.assertEqual(OD._TSV_CELL("-2.50"), "-2.50")

    def test_vietnamese_unicode_preserved(self):
        s = "Bảng điều khiển tiếng Việt"
        self.assertEqual(OD._TSV_CELL(s), s)

    def test_injection_through_export_campaign_name(self):
        r = T4.make_row(1, **dict(ENT, campaign="=cmd()", **DEF_BEFORE))
        p73 = self.promote([r])
        cfg = self.config(p73=p73, p74=self.newdir("i4"), p75=self.newdir("i5"),
                          p76=self.newdir("i6"), p77=self.newdir("i7"))
        tsv = OD.export_tsv(self.model(cfg))
        self.assertIn("'=cmd()", tsv)   # neutralized, not raw =cmd()

    def test_vietnamese_through_export(self):
        r = T4.make_row(1, **dict(ENT, campaign="Chiến dịch", **DEF_BEFORE))
        p73 = self.promote([r])
        cfg = self.config(p73=p73, p74=self.newdir("v4"), p75=self.newdir("v5"),
                          p76=self.newdir("v6"), p77=self.newdir("v7"))
        self.assertIn("Chiến dịch", OD.export_tsv(self.model(cfg)))
        self.assertIn("Chiến dịch", OD.export_json(self.model(cfg)))


# ================================================================ path / privacy
class Privacy(Base):
    def test_no_absolute_paths_in_model(self):
        cfg, _ = self.full_pipeline()
        blob = OD.export_json(OD.build_operations_model(cfg, now=NOW))
        self.assertNotIn(self.tmp, blob)
        self.assertNotIn(cfg.phase7_3_dir, blob)
        # no Windows drive-letter absolute path either
        self.assertNotIn(":\\", blob)

    def test_lineage_uses_relative_display_paths(self):
        cfg, _ = self.full_pipeline()
        m = OD.build_operations_model(cfg, now=NOW)
        disp = m["lineage"]["input_directories"]["phase7_3"]
        self.assertNotIn(self.tmp, disp)


# ================================================================ immutability
class Immutability(Base):
    UP = ("phase7_3_dir", "phase7_4_dir", "phase7_5_dir", "phase7_6_dir", "phase7_7_dir")

    def snapshot(self, cfg):
        return {a: tree_hash(getattr(cfg, a)) for a in self.UP}

    def test_model_build_is_read_only(self):
        cfg, _ = self.full_pipeline()
        before = self.snapshot(cfg)
        for _ in range(3):
            OD.build_operations_model(cfg, now=NOW)
        self.assertEqual(before, self.snapshot(cfg))

    def test_server_lifecycle_read_only(self):
        cfg, _ = self.full_pipeline()
        before = self.snapshot(cfg)
        url, srv = self.serve(cfg)
        for ep in ("/api/health", "/api/overview", "/api/analysis", "/api/reviews",
                   "/api/decision-packages", "/api/manual-actions", "/api/outcomes",
                   "/api/attention", "/api/lineage", "/api/export/operations.json",
                   "/api/export/operations.tsv", "/api/export/operations.md"):
            http(url + ep)
        http(url + "/api/overview", method="POST", data=b"{}")   # rejected, still read-only
        self.assertEqual(before, self.snapshot(cfg))

    def test_snapshot_does_not_touch_upstream(self):
        cfg, _ = self.full_pipeline()
        before = self.snapshot(cfg)
        OD.write_snapshot(cfg.base_dir, OD.build_operations_model(cfg, now=NOW), "tsv")
        self.assertEqual(before, self.snapshot(cfg))

    def test_no_new_files_in_upstream_dirs(self):
        cfg, _ = self.full_pipeline()
        before = {a: tree_hash(getattr(cfg, a))[1] for a in self.UP}
        OD.build_operations_model(cfg, now=NOW)
        after = {a: tree_hash(getattr(cfg, a))[1] for a in self.UP}
        self.assertEqual(before, after)


# ================================================================ server lifecycle
class ServerLifecycle(Base):
    def test_clean_shutdown(self):
        cfg, _ = self.empty_pipeline()
        srv = OD.build_server(cfg, host="127.0.0.1", port=0, now=NOW)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        http("http://127.0.0.1:%d/api/health" % srv.server_address[1])
        srv.shutdown()
        srv.server_close()

    def test_port_in_use_is_safe(self):
        # A bind failure (e.g. port already in use) must be handled gracefully: main() returns 2
        # and never raises. We simulate the bind failure deterministically (SO_REUSEADDR behaviour
        # differs across platforms, so we exercise the error-handling path directly).
        cfg, _ = self.empty_pipeline()
        args = ["--base-dir", cfg.base_dir, "--phase7-3-dir", cfg.phase7_3_dir,
                "--phase7-4-dir", cfg.phase7_4_dir, "--phase7-5-dir", cfg.phase7_5_dir,
                "--phase7-6-dir", cfg.phase7_6_dir, "--phase7-7-dir", cfg.phase7_7_dir,
                "--host", "127.0.0.1", "--port", "0"]
        orig = OD.build_server
        OD.build_server = lambda *a, **k: (_ for _ in ()).throw(OSError("address in use"))
        try:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = OD.main(args, serve=True)
            self.assertEqual(rc, 2)
            self.assertIn("BIND_FAILED", err.getvalue())
        finally:
            OD.build_server = orig


# ================================================================ attention
class Attention(Base):
    def test_attention_only_upstream_statuses(self):
        cfg, _ = self.full_pipeline()
        m = OD.build_operations_model(cfg, now=NOW)
        cats = {a["category"] for a in m["attention"]}
        allowed = {"review_required", "policy_required", "source_changed", "entity_absent",
                   "review_state_blocked", "package_changed", "decision_conflict",
                   "package_integrity", "pending_manual_check", "tracker_stale", "tracker_blocked",
                   "followup_blocked", "followup_selection_required", "insufficient_followup_data",
                   "ambiguous_entity_match", "attribution_mismatch", "currency_mismatch"}
        self.assertTrue(cats <= allowed, cats - allowed)

    def test_attention_no_priority_score(self):
        cfg, _ = self.full_pipeline()
        m = OD.build_operations_model(cfg, now=NOW)
        for a in m["attention"]:
            self.assertNotIn("priority", a)
            self.assertNotIn("score", a)


# ================================================================ prohibited integrations / accessibility
class ProhibitedIntegrations(Base):
    def src(self):
        return open(OD.__file__, encoding="utf-8").read()

    def test_no_prohibited_imports(self):
        tree = ast.parse(self.src())
        banned = {"requests", "httpx", "aiohttp", "boto3", "botocore", "selenium", "playwright",
                  "webdriver", "pickle", "marshal", "shelve", "urllib.request", "subprocess"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    self.assertNotIn(n.name, banned, n.name)
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn(node.module, banned, node.module)

    def test_no_eval_exec_ossystem(self):
        src = self.src()
        for bad in ("os.system(", "eval(", "exec(", "compile(", "__import__("):
            self.assertNotIn(bad, src, bad)

    def test_socket_only_used_for_loopback_resolution(self):
        src = self.src()
        # socket is imported for getaddrinfo; there must be no outbound connection.
        for bad in ("socket.connect", "socket.create_connection", ".connect(", "sendall", "sendto"):
            self.assertNotIn(bad, src, bad)
        self.assertIn("getaddrinfo", src)

    def test_no_browser_automation_or_webbrowser(self):
        src = self.src()
        for bad in ("webbrowser", "selenium", "playwright", "webdriver"):
            self.assertNotIn(bad, src, bad)

    def test_no_functional_credential_or_token_storage(self):
        # There is no FUNCTIONAL credential/token/cookie/session handling. (The only mentions of
        # "credential" are the constant-zero safety counter and the never-do boundary flag.)
        src = self.src()
        for bad in ("keyring", "getpass", "http.cookiejar", "CookieJar", "Set-Cookie", "Bearer ",
                    "oauth", "api_key", "access_token", "refresh_token", "secret_key"):
            self.assertNotIn(bad, src, bad)
        self.assertNotIn('"Authorization"', src)   # never sets an auth header
        # the safety-boundary declarations are exactly that — a zero counter and a never-flag.
        self.assertEqual(OD.AMAZON_COUNTERS["credential_store_count"], 0)
        self.assertTrue(OD.THIS_SESSION_NEVER["stores_amazon_credentials"])

    def test_amazon_counters_all_zero(self):
        self.assertTrue(all(v == 0 for v in OD.AMAZON_COUNTERS.values()))

    def test_module_compiles(self):
        import py_compile
        py_compile.compile(OD.__file__, doraise=True)


class Accessibility(Base):
    def html(self):
        return open(os.path.join(OD.STATIC_DIR, "index.html"), encoding="utf-8").read()

    def css(self):
        return open(os.path.join(OD.STATIC_DIR, "dashboard.css"), encoding="utf-8").read()

    def test_semantic_structure(self):
        h = self.html()
        self.assertIn('lang="en"', h)
        self.assertIn("<main", h)
        self.assertIn("<nav", h)
        self.assertIn("skip-link", h)

    def test_keyboard_and_aria(self):
        h = self.html()
        self.assertIn("aria-label", h)
        self.assertIn('tabindex="-1"', h)

    def test_focus_visible_and_reduced_motion(self):
        c = self.css()
        self.assertIn(":focus-visible", c)
        self.assertIn("prefers-reduced-motion", c)

    def test_light_and_dark_theme(self):
        self.assertIn("prefers-color-scheme: dark", self.css())

    def test_javascript_disabled_fallback(self):
        h = self.html()
        self.assertIn("<noscript>", h.lower())
        self.assertIn("/api/overview", h)

    def test_no_inline_style_or_script_body(self):
        h = self.html()
        self.assertNotIn("<script>", h.lower())
        self.assertNotIn("onclick", h.lower())
        self.assertNotIn("javascript:", h.lower())


# ================================================================ real T2 (skipped when absent)
_REAL = os.path.join(ROOT, "runs", "T2", "phase7")


@unittest.skipUnless(os.path.isdir(os.path.join(_REAL, "7.3", "promoted")),
                     "real T2 runtime data not present (expected on a fresh worktree)")
class RealT2(Base):
    def cfg(self):
        return OD.Config(os.path.join(_REAL, "7.8"), os.path.join(_REAL, "7.3"),
                         os.path.join(_REAL, "7.4"), os.path.join(_REAL, "7.5"),
                         os.path.join(_REAL, "7.6"), os.path.join(_REAL, "7.7"))

    def test_real_t2_counts(self):
        m = OD.build_operations_model(self.cfg(), now=NOW)
        ov = m["overview"]
        self.assertEqual(m["readiness"], OD.READY_EMPTY)
        self.assertEqual(ov["source_rows"], 114)
        self.assertEqual(ov["analyzed_rows"], 114)
        self.assertEqual(ov["review_state_records"], 1)
        self.assertEqual(ov["deferred_reviews"], 1)
        self.assertEqual(ov["decision_package_id"], "pkg-3cf372628abc6082")
        self.assertEqual(ov["eligible_decisions"], 0)
        self.assertEqual(ov["tracker_records"], 0)
        self.assertEqual(ov["followup_id"], "followup-ae48aea7a80654ca")
        self.assertEqual(ov["eligible_followups"], 0)

    def test_real_t2_counters_zero(self):
        m = OD.build_operations_model(self.cfg(), now=NOW)
        self.assertTrue(all(v == 0 for v in m["amazon_counters"].values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
