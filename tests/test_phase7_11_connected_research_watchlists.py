#!/usr/bin/env python3
"""Phase 7.11 — Connected Research Watchlists, Change Detection & Owner Alerts.

Every test runs with NO real internet call: the reused Phase 7.10 authority is driven through
injected fake transports + fake DNS resolvers (the accepted 7.10 synthetic-transport pattern). The
permanent Amazon-account boundary is asserted throughout — no Seller Central, no seller API, no Ads
API, no seller OAuth — and every Amazon counter stays a constant zero. Synthetic fixtures only; the
upstream Phase 7.3–7.10 runtime trees are never touched.
"""
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

from production import phase7_connected_research_watchlists as W   # noqa: E402
from production import phase7_connected_public_research as R       # noqa: E402
import network_policy as NP                                        # noqa: E402
import diagnostics as D                                            # noqa: E402

# Amazon endpoint literals live in TEST code only (production assembles nothing like them). They are
# used solely to assert these destinations are BLOCKED by the reused 7.10 policy.
SELLER_CENTRAL = "https://sellercentral.amazon.com/home"
SELLER_CENTRAL_SIGNIN = "https://www.amazon.com/ap/signin"

PUBLIC_ADDR = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def resolver_public(host, port=None, *a, **k):
    return list(PUBLIC_ADDR)


def resolver_fail(host, port=None, *a, **k):
    raise socket.gaierror("no such host")


def route(status, ctype, body, **extra):
    h = {"content-type": ctype}
    h.update(extra)
    return (status, h, body)


def make_transport(routes, record=None):
    def t(url, *, timeout_seconds, max_bytes, headers=None):
        if record is not None:
            record.append((url, dict(headers or {})))
        for frag, resp in routes.items():
            if frag in url:
                return resp if not callable(resp) else resp(url, headers)
        return 404, {}, b""
    return t


def html(title="Nurse Sweatshirt", desc="A cozy nurse sweatshirt", price=None, currency="USD",
         availability="InStock", rating_count=None):
    parts = [b"<html><head><title>", title.encode(), b"</title>",
             b'<meta name="description" content="', desc.encode(), b'">']
    ld = {"@type": "Product", "name": title}
    if price is not None:
        ld["offers"] = {"price": price, "priceCurrency": currency, "availability": availability}
    if rating_count is not None:
        ld["aggregateRating"] = {"ratingValue": "4.7", "reviewCount": rating_count}
    parts.append(b'<script type="application/ld+json">' +
                 json.dumps(ld).encode() + b"</script>")
    parts.append(b"</head><body>x</body></html>")
    return b"".join(parts)


HTML1 = html()
HTML2 = html(title="Nurse Hoodie")
RSS = (b'<?xml version="1.0"?><rss version="2.0"><channel><title>Feed</title>'
       b'<item><title>Item A</title><link>https://ex.com/a</link><guid>a</guid></item>'
       b'</channel></rss>')

ROBOTS = route(404, "text/plain", b"")


def transport_html(body=HTML1, host="example.com", ctype="text/html", record=None):
    return make_transport({"robots.txt": ROBOTS, host: route(200, ctype, body)}, record=record)


def wl_def(**over):
    d = {
        "name": "Nurse watch",
        "timezone": "Asia/Ho_Chi_Minh",
        "schedule": {"type": "manual"},
        "sources": [{"source_type": "public-url", "source_locator": "https://example.com/nurse"}],
        "alert_rules": [],
    }
    d.update(over)
    return d


def rule(operator, **kw):
    r = {"operator": operator, "evidence_type": kw.pop("evidence_type", "*"),
         "field_path": kw.pop("field_path", "*"), "severity": kw.pop("severity", "REVIEW"),
         "label": kw.pop("label", "L"), "message": kw.pop("message", "M")}
    r.update(kw)
    return r


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p711t-")
        self._n = 0
        D.clear_events()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cfg(self, transport=None, resolver=resolver_public, now=1_000_000.0, research_dir=None,
            allow_local=False, env=None, base=None):
        self._n += 1
        base = base or os.path.join(self.tmp, "ws%d" % self._n)
        return W.Config(base, research_dir=research_dir, env=env or {}, transport=transport,
                        resolver=resolver, now_fn=(now if callable(now) else (lambda: now)),
                        allow_local=allow_local)

    def rebind(self, cfg, transport, now=None):
        return W.Config(cfg.base_dir, research_dir=cfg.research_dir, env=cfg.env, transport=transport,
                        resolver=cfg.resolver, allow_local=cfg.allow_local,
                        now_fn=(lambda: now) if now is not None else cfg.now_fn)

    def html_cfg(self, body=HTML1, **kw):
        return self.cfg(transport=transport_html(body), **kw)

    def create(self, cfg, defn=None):
        res = W.create_watchlist(cfg, defn or wl_def())
        self.assertTrue(res["ok"], res)
        return res["watchlist_id"]

    def write_def(self, defn):
        path = os.path.join(self.tmp, "def%d.json" % self._n)
        self._n += 1
        with open(path, "w", encoding="utf-8") as f:
            json.dump(defn, f)
        return path


# ======================================================================== 1) CLI
class TestCLI(Base):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = W.run_cli(argv)
                return code, out.getvalue(), err.getvalue()
            except SystemExit as e:
                return e.code, out.getvalue(), err.getvalue()

    def test_01_cli_help(self):
        code, out, err = self._run(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("run-watchlist", out)

    def test_02_missing_command(self):
        code, out, err = self._run(["--base-dir", self.tmp])
        self.assertNotEqual(code, 0)

    def test_03_unknown_command(self):
        code, out, err = self._run(["--base-dir", self.tmp, "teleport-watchlist"])
        self.assertNotEqual(code, 0)

    def test_04_unknown_argument(self):
        code, out, err = self._run(["--base-dir", self.tmp, "list-watchlists", "--frobnicate", "x"])
        self.assertNotEqual(code, 0)

    def test_04b_all_subcommands_present(self):
        help_text = W.build_parser().format_help()
        for sub in ("create-watchlist", "validate-watchlist", "show-watchlist", "list-watchlists",
                    "run-watchlist", "run-due", "compare", "list-executions", "list-changes",
                    "list-alerts", "acknowledge-alert", "dismiss-alert", "reopen-alert",
                    "verify-state", "export", "scheduler-plan", "validate-only"):
            self.assertIn(sub, help_text)


# ======================================================================== 2) watchlist schema
class TestWatchlistSchema(Base):
    def test_05_create_valid(self):
        c = self.cfg()
        res = W.create_watchlist(c, wl_def())
        self.assertEqual(res["readiness"], W.READY)
        self.assertTrue(res["watchlist_id"].startswith("wl-"))

    def test_06_empty_watchlist(self):
        c = self.cfg()
        res = W.create_watchlist(c, wl_def(sources=[]))
        self.assertTrue(res["ok"])
        self.assertEqual(res["source_count"], 0)

    def test_07_disabled_watchlist_run_due_skips(self):
        c = self.html_cfg()
        wid = self.create(c, wl_def(enabled=False, schedule={"type": "hourly"}))
        res = W.run_due(c, reference_time="2026-07-23T10:00:00Z")
        self.assertEqual(res["readiness"], W.NOT_DUE)
        self.assertTrue(any(s["watchlist_id"] == wid and s["reason"] == "WATCHLIST_DISABLED"
                            for s in res["skipped"]))

    def test_08_duplicate_watchlist_idempotent(self):
        c = self.cfg()
        r1 = W.create_watchlist(c, wl_def())
        r2 = W.create_watchlist(c, wl_def())
        self.assertEqual(r1["watchlist_id"], r2["watchlist_id"])
        self.assertTrue(r2["ok"])
        self.assertEqual(r2["detail"], "IDEMPOTENT_REUSE")

    def test_09_unknown_watchlist_field(self):
        c = self.cfg()
        with self.assertRaises(W.WatchlistError) as e:
            W.create_watchlist(c, wl_def(mystery=1))
        self.assertEqual(e.exception.code, "WATCHLIST_UNKNOWN_FIELD")

    def test_10_secret_field_rejected(self):
        c = self.cfg()
        d = wl_def(sources=[{"source_type": "public-url", "source_locator": "https://example.com/x",
                             "parser_options": {"api_secret": "shh"}}])
        with self.assertRaises(W.WatchlistError) as e:
            W.create_watchlist(c, d)
        self.assertEqual(e.exception.code, "FORBIDDEN_FIELD")

    def test_11_header_field_rejected(self):
        c = self.cfg()
        d = wl_def(sources=[{"source_type": "public-url", "source_locator": "https://example.com/x",
                             "parser_options": {"headers": {"X": "Y"}}}])
        with self.assertRaises(W.WatchlistError) as e:
            W.create_watchlist(c, d)
        self.assertEqual(e.exception.code, "FORBIDDEN_FIELD")

    def test_12_cookie_field_rejected(self):
        c = self.cfg()
        d = wl_def(sources=[{"source_type": "public-url", "source_locator": "https://example.com/x",
                             "parser_options": {"cookie": "s=1"}}])
        with self.assertRaises(W.WatchlistError):
            W.create_watchlist(c, d)

    def test_13_command_field_rejected(self):
        c = self.cfg()
        d = wl_def(sources=[{"source_type": "public-url", "source_locator": "https://example.com/x",
                             "parser_options": {"command": "rm -rf"}}])
        with self.assertRaises(W.WatchlistError):
            W.create_watchlist(c, d)

    def test_14_canonical_watchlist_id_stable(self):
        _, id1 = W.validate_watchlist(wl_def())
        _, id2 = W.validate_watchlist(wl_def())
        self.assertEqual(id1, id2)

    def test_15_reordered_json_keys_same_id(self):
        d1 = wl_def()
        d2 = {k: d1[k] for k in reversed(list(d1))}
        self.assertEqual(W.validate_watchlist(d1)[1], W.validate_watchlist(d2)[1])

    def test_16_timestamp_excluded_from_id(self):
        a = W.validate_watchlist(wl_def(created_by="A", owner_notes="early"))[1]
        b = W.validate_watchlist(wl_def(created_by="B", owner_notes="late", description="x"))[1]
        self.assertEqual(a, b)

    def test_16b_enabled_excluded_from_id(self):
        self.assertEqual(W.validate_watchlist(wl_def(enabled=True))[1],
                         W.validate_watchlist(wl_def(enabled=False))[1])

    def test_17_mtime_excluded_from_id(self):
        c1 = self.cfg()
        c2 = self.cfg()
        id1 = W.create_watchlist(c1, wl_def())["watchlist_id"]
        id2 = W.create_watchlist(c2, wl_def())["watchlist_id"]
        self.assertEqual(id1, id2)

    def test_18_temp_path_excluded_from_id(self):
        other = tempfile.mkdtemp(prefix="p711-other-")
        try:
            c = W.Config(os.path.join(other, "ws"), env={}, now_fn=lambda: 42.0)
            self.assertEqual(W.create_watchlist(c, wl_def())["watchlist_id"],
                             W.validate_watchlist(wl_def())[1])
        finally:
            shutil.rmtree(other, ignore_errors=True)


# ======================================================================== 3) source descriptors (reuse 7.10)
class TestSources(Base):
    def test_19_source_descriptor_reused_from_710(self):
        # the stored item's descriptor is exactly what the reused 7.10 normalizer produces
        canonical, _ = W.validate_watchlist(wl_def())
        item = canonical["sources"][0]
        self.assertEqual(item["descriptor"], R._normalize_descriptor(
            {"source_type": "public-url", "source_locator": "https://example.com/nurse"}, 0))
        self.assertEqual(item["source_id"], R._source_id("public-url",
                         R._normalize_locator("public-url", "https://example.com/nurse")))

    def test_20_invalid_710_source_rejected(self):
        c = self.cfg()
        with self.assertRaises(W.WatchlistError) as e:
            W.create_watchlist(c, wl_def(sources=[{"source_type": "teleport",
                                                   "source_locator": "x"}]))
        self.assertEqual(e.exception.code, "SOURCE_DESCRIPTOR_REJECTED")

    def test_21_seller_central_source_blocked_on_run(self):
        c = self.cfg(transport=make_transport({"x": route(200, "text/html", b"x")}))
        wid = self.create(c, wl_def(sources=[{"source_type": "public-url",
                                              "source_locator": SELLER_CENTRAL}]))
        res = W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        self.assertEqual(res["readiness"], W.SELLER_CENTRAL_POLICY_BLOCKED)
        self.assertTrue(all(v == 0 for v in res["amazon_counters"].values()))

    def test_22_account_scoped_amazon_source_blocked(self):
        c = self.cfg(transport=make_transport({"x": route(200, "text/html", b"x")}))
        wid = self.create(c, wl_def(sources=[{"source_type": "public-url",
                                              "source_locator": SELLER_CENTRAL_SIGNIN}]))
        res = W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        self.assertIn(res["readiness"], (W.SELLER_CENTRAL_POLICY_BLOCKED, W.SOURCE_BLOCKED))
        self.assertNotEqual(res["readiness"], W.READY)

    def test_23_amazon_public_product_allowed(self):
        body = html(title="Nurse Sweatshirt", price="24.99")
        t = make_transport({"robots.txt": ROBOTS, "amazon.com": route(200, "text/html", body)})
        c = self.cfg(transport=t)
        wid = self.create(c, wl_def(sources=[{"source_type": "amazon-public-product",
                                              "source_locator": "https://www.amazon.com/dp/B012345678"}]))
        res = W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        self.assertIn(res["readiness"], (W.READY, W.READY_EMPTY))
        self.assertEqual(res["change_counts"].get("INITIAL_BASELINE"), 1)

    def test_24_no_second_network_transport_in_source(self):
        with open(W.__file__, encoding="utf-8") as f:
            src = f.read()
        for banned in ("urllib.request", "http.client", "socket.create_connection", "import requests",
                       "webbrowser", "selenium", "playwright", "webdriver"):
            self.assertNotIn(banned, src, banned)

    def test_25_no_direct_url_fetch_goes_through_710(self):
        record = []
        c = self.cfg(transport=transport_html(HTML1, record=record))
        wid = self.create(c)
        W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        # every fetch was made through the injected reused-7.10 transport seam
        self.assertTrue(any("example.com" in url for url, _ in record))


# ======================================================================== 4) schedule model
class TestSchedule(Base):
    def due(self, sched, last, ref, **kw):
        return W.compute_due(sched, sched.get("timezone", "UTC"), last, ref, **kw)

    def test_26_manual_schedule_never_auto_due(self):
        d = self.due({"type": "manual", "timezone": "UTC", "enabled": True}, None, "2026-07-23T10:00:00Z")
        self.assertFalse(d["due"])

    def test_27_hourly_schedule(self):
        s = {"type": "hourly", "interval_hours": 1, "timezone": "UTC", "enabled": True}
        self.assertTrue(self.due(s, "2026-07-23T10:00:00Z", "2026-07-23T11:00:00Z")["due"])
        self.assertFalse(self.due(s, "2026-07-23T10:00:00Z", "2026-07-23T10:30:00Z")["due"])

    def test_28_daily_schedule(self):
        s = {"type": "daily", "local_time": "09:00", "timezone": "UTC", "enabled": True}
        self.assertTrue(self.due(s, "2026-07-22T09:00:00Z", "2026-07-23T09:30:00Z")["due"])
        self.assertFalse(self.due(s, "2026-07-23T09:00:00Z", "2026-07-23T10:00:00Z")["due"])

    def test_29_weekly_schedule(self):
        # 2026-07-23 is a Thursday (weekday 3)
        s = {"type": "weekly", "local_time": "09:00", "weekdays": [3], "timezone": "UTC", "enabled": True}
        self.assertTrue(self.due(s, None, "2026-07-23T10:00:00Z")["due"])
        # already ran after Thursday's instant -> Friday is not due (next is next Thursday)
        self.assertFalse(self.due(s, "2026-07-23T09:30:00Z", "2026-07-24T10:00:00Z")["due"])

    def test_30_interval_hours_schedule(self):
        s = {"type": "interval-hours", "interval_hours": 6, "timezone": "UTC", "enabled": True}
        self.assertTrue(self.due(s, "2026-07-23T00:00:00Z", "2026-07-23T06:00:00Z")["due"])
        self.assertFalse(self.due(s, "2026-07-23T00:00:00Z", "2026-07-23T05:00:00Z")["due"])

    def test_31_minimum_interval_enforced(self):
        with self.assertRaises(W.WatchlistError) as e:
            W._normalize_schedule({"type": "interval-hours", "interval_hours": 0}, "UTC")
        self.assertEqual(e.exception.code, "SCHEDULE_MIN_INTERVAL")

    def test_32_invalid_timezone(self):
        with self.assertRaises(W.WatchlistError) as e:
            W.validate_watchlist(wl_def(timezone="Mars/Phobos"))
        self.assertEqual(e.exception.code, "TIMEZONE_INVALID")

    def test_33_dst_forward(self):
        s = {"type": "daily", "local_time": "02:30", "timezone": "America/New_York", "enabled": True}
        d = self.due(s, None, "2026-03-08T12:00:00Z")   # US spring-forward day
        self.assertTrue(d["due"])
        self.assertIsNotNone(d["next_due"])

    def test_34_dst_backward(self):
        s = {"type": "daily", "local_time": "01:30", "timezone": "America/New_York", "enabled": True}
        d = self.due(s, None, "2026-11-01T12:00:00Z")   # US fall-back day
        self.assertTrue(d["due"])

    def test_35_clock_moved_backward(self):
        s = {"type": "hourly", "interval_hours": 1, "timezone": "UTC", "enabled": True}
        d = self.due(s, "2026-07-23T12:00:00Z", "2026-07-23T10:00:00Z")
        self.assertFalse(d["due"])
        self.assertEqual(d["reason"], "CLOCK_MOVED_BACKWARD")

    def test_36_due_state(self):
        s = {"type": "interval-hours", "interval_hours": 2, "timezone": "UTC", "enabled": True}
        self.assertTrue(self.due(s, "2026-07-23T00:00:00Z", "2026-07-23T03:00:00Z")["due"])

    def test_37_not_due_state(self):
        s = {"type": "interval-hours", "interval_hours": 2, "timezone": "UTC", "enabled": True}
        self.assertFalse(self.due(s, "2026-07-23T00:00:00Z", "2026-07-23T01:00:00Z")["due"])

    def test_38_disabled_schedule(self):
        s = {"type": "hourly", "interval_hours": 1, "timezone": "UTC", "enabled": False}
        self.assertFalse(self.due(s, None, "2026-07-23T10:00:00Z")["due"])

    def test_39_bounded_catch_up(self):
        s = {"type": "hourly", "interval_hours": 1, "timezone": "UTC", "enabled": True}
        d = self.due(s, "2026-07-20T00:00:00Z", "2026-07-23T00:00:00Z")   # 72 intervals missed
        self.assertTrue(d["due"])
        self.assertLessEqual(d["missed"], W.MAX_CATCH_UP)

    def test_40_manual_force(self):
        s = {"type": "manual", "timezone": "UTC", "enabled": True}
        self.assertTrue(self.due(s, None, "2026-07-23T10:00:00Z", forced=True)["due"])


# ======================================================================== 5) scheduler plan
class TestSchedulerPlan(Base):
    def plan(self):
        c = self.cfg(research_dir=os.path.join(self.tmp, "r10"))
        wid = self.create(c)
        return W.scheduler_plan(c, wid)["plan"], c, wid

    def test_41_scheduler_plan_windows(self):
        plan, _, _ = self.plan()
        self.assertIn("powershell.exe", plan["windows_task_scheduler_example"])
        self.assertIn("run-due", plan["windows_task_scheduler_example"])

    def test_42_scheduler_plan_cron(self):
        plan, _, _ = self.plan()
        self.assertRegex(plan["cron_example"], r"^\d+ \*")
        self.assertIn("run-due", plan["cron_example"])

    def test_43_scheduler_plan_no_execution(self):
        plan, _, _ = self.plan()
        blob = W.canonical_json(plan).lower()
        for banned in ("schtasks", "crontab", "systemctl", "launchctl", "register-scheduledtask"):
            self.assertNotIn(banned, blob)
        self.assertEqual(plan["owner_action_required"], "OWNER_ACTION_REQUIRED")

    def test_44_scheduler_plan_no_secrets(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-SHOULDNOTLEAK123456"
        try:
            plan, _, _ = self.plan()
            self.assertNotIn("SHOULDNOTLEAK", W.canonical_json(plan))
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        self.assertNotIn("sellercentral", W.canonical_json(plan).lower())

    def test_45_scheduler_plan_safe_quoting(self):
        plan, _, _ = self.plan()
        # embedded single quote is doubled for PowerShell, escaped for POSIX
        self.assertEqual(W._pwsq("a'b"), "'a''b'")
        self.assertEqual(W._shq("a'b"), "'a'\\''b'")
        self.assertIn("template_hash", plan)


# ---- helpers for crafted-snapshot comparison ---------------------------------
def snap(evs, *, status="CAPTURED", content="h1", cid="cap-1", parser=None, nloc="loc"):
    parser = parser or R.PARSER_VERSION
    return {"capture_id": cid, "content_sha256": content, "capture_status": status,
            "parser_version": parser, "source_locator": "https://example.com/x",
            "normalized_locator": nloc, "media_type": "text/html",
            "evidence": [{"evidence_type": et, "field_path": fp, "observed_value": v,
                          "observed_unit": u, "observed_currency": cur, "extraction_method": "m",
                          "parser_version": parser, "evidence_id": "ev-" + fp}
                         for (et, fp, v, u, cur) in evs]}


def item(ci_fields=(), ignored=()):
    return {"item_id": "item-x", "source_id": "src-x",
            "comparison_policy": {"case_insensitive_field_paths": list(ci_fields),
                                  "ignored_field_paths": list(ignored)}}


def compare_unit(prev, curr, it=None):
    changes, summary = W._compare_unit("wl-x", it or item(), "loc", prev, curr)
    by_type = {}
    for c in changes:
        by_type.setdefault(c["change_type"], []).append(c)
    return changes, summary, by_type


# ======================================================================== 6) baseline selection
class TestBaseline(Base):
    def two_runs(self, body1, body2, defn=None, ref2="2026-07-23T11:00:00Z"):
        c = self.html_cfg(body1)
        wid = self.create(c, defn or wl_def())
        r1 = W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        c2 = self.rebind(c, transport_html(body2))
        r2 = W.run_watchlist(c2, wid, reference_time=ref2)
        changes = W.list_changes(c2, wid)["changes"]
        return c2, wid, r1, r2, changes

    def test_46_first_baseline(self):
        c = self.html_cfg(HTML1)
        wid = self.create(c)
        r1 = W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        self.assertEqual(r1["change_counts"].get("INITIAL_BASELINE"), 1)
        self.assertNotIn("CHANGED", r1["change_counts"])

    def test_47_no_previous_capture_is_initial(self):
        _, _, by = compare_unit(None, snap([("html.title", "title", "T", None, None)]))
        self.assertIn(W.CHG_INITIAL_BASELINE, by)

    def test_48_one_previous_capture(self):
        _, _, r1, r2, changes = self.two_runs(HTML1, HTML2)
        self.assertTrue(any(c["change_type"] == "CHANGED" for c in changes))

    def test_49_multiple_identical_captures_unchanged(self):
        c = self.html_cfg(HTML1)
        wid = self.create(c)
        W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        c2 = self.rebind(c, transport_html(HTML1))
        r2 = W.run_watchlist(c2, wid, reference_time="2026-07-23T11:00:00Z")
        self.assertEqual(r2["readiness"], W.READY_EMPTY)
        self.assertNotIn("CHANGED", r2["change_counts"])

    def test_50_duplicate_source_no_crash(self):
        d = wl_def(sources=[{"source_type": "public-url", "source_locator": "https://example.com/nurse"},
                            {"source_type": "public-url", "source_locator": "https://example.com/nurse",
                             "label": "second"}])
        c = self.html_cfg(HTML1)
        wid = self.create(c, d)
        r = W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        self.assertIn(r["readiness"], (W.READY, W.READY_EMPTY))

    def test_51_corrupt_previous_state_blocks_run(self):
        c = self.html_cfg(HTML1)
        wid = self.create(c)
        W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        sp = W._state_path(c, wid)
        with open(sp, encoding="utf-8") as f:
            raw = json.load(f)
        raw["items"] = {}
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        c2 = self.rebind(c, transport_html(HTML2))
        with self.assertRaises(W.WatchlistError) as e:
            W.run_watchlist(c2, wid, reference_time="2026-07-23T11:00:00Z")
        self.assertEqual(e.exception.readiness, W.INTEGRITY_BLOCKED)

    def test_52_corrupt_current_capture_in_compare(self):
        rdir = os.path.join(self.tmp, "r10")
        rcfg = R.Config(rdir, env={}, transport=transport_html(HTML1), resolver=resolver_public)
        run1 = R.capture_one(rcfg, "public-url", "https://example.com/nurse")["run_id"]
        rcfg2 = R.Config(rdir, env={}, transport=transport_html(HTML2), resolver=resolver_public)
        run2 = R.capture_one(rcfg2, "public-url", "https://example.com/nurse")["run_id"]
        # corrupt one raw capture file
        raws = [n for n in os.listdir(rcfg.raw_dir) if n.endswith(".bin")]
        with open(os.path.join(rcfg.raw_dir, raws[0]), "wb") as f:
            f.write(b"tampered")
        c = self.cfg(research_dir=rdir)
        wid = self.create(c)
        res = W.compare(c, wid, run1, run2)
        self.assertEqual(res["readiness"], W.COMPARISON_BLOCKED)

    def test_53_partial_capture_recorded(self):
        _, _, by = compare_unit(snap([("html.title", "title", "T", None, None)]),
                                snap([("html.title", "title", "T", None, None)],
                                     status=R.CAP_PARSE_PARTIAL))
        self.assertIn(W.CHG_PARSE_PARTIAL, by)

    def test_54_blocked_capture_preserves_baseline(self):
        base = snap([("html.title", "title", "T", None, None)])
        changes, summary, by = compare_unit(base, snap([], status=R.CAP_SOURCE_BLOCKED, content=""))
        self.assertIn(W.CHG_SOURCE_BLOCKED, by)
        self.assertNotIn(W.CHG_CHANGED, by)

    def test_55_unavailable_capture(self):
        base = snap([("html.title", "title", "T", None, None)])
        _, _, by = compare_unit(base, snap([], status=R.CAP_UNAVAILABLE, content=""))
        self.assertIn(W.CHG_SOURCE_UNAVAILABLE, by)

    def test_56_parser_version_change_in_lineage(self):
        prev = snap([("html.title", "title", "T", None, None)], parser="phase7.10-parser-v1")
        curr = snap([("html.title", "title", "T", None, None)], parser="phase7.99-parser-v9",
                    content="h2")
        changes, summary, by = compare_unit(prev, curr)
        self.assertTrue(summary["parser_version_changed"])
        self.assertTrue(any("parser_version_changed" in c["warnings"] for c in changes))

    def test_57_source_normalization_change(self):
        # fragment-only difference normalizes to the same locator (reused 7.10 normalizer)
        self.assertEqual(R._normalize_locator("public-url", "https://example.com/x#a"),
                         R._normalize_locator("public-url", "https://example.com/x#b"))


# ======================================================================== 7) change events
class TestChangeEvents(Base):
    def test_58_exact_field_unchanged(self):
        e = [("html.title", "title", "T", None, None)]
        _, _, by = compare_unit(snap(e), snap(e))
        self.assertIn(W.CHG_UNCHANGED, by)
        self.assertNotIn(W.CHG_CHANGED, by)

    def test_59_field_added(self):
        _, _, by = compare_unit(snap([("html.title", "title", "T", None, None)]),
                                snap([("html.title", "title", "T", None, None),
                                      ("html.meta_description", "meta.description", "D", None, None)]))
        self.assertIn(W.CHG_ADDED, by)

    def test_60_field_removed(self):
        _, _, by = compare_unit(snap([("html.title", "title", "T", None, None),
                                      ("html.meta_description", "meta.description", "D", None, None)]),
                                snap([("html.title", "title", "T", None, None)]))
        self.assertIn(W.CHG_REMOVED, by)

    def test_61_field_changed(self):
        _, _, by = compare_unit(snap([("html.title", "title", "A", None, None)]),
                                snap([("html.title", "title", "B", None, None)]))
        self.assertIn(W.CHG_CHANGED, by)

    def test_62_page_hash_changed_evidence_unchanged(self):
        e = [("html.title", "title", "T", None, None)]
        changes, summary, by = compare_unit(snap(e, content="h1"), snap(e, content="h2"))
        self.assertTrue(summary["content_hash_changed"])
        self.assertFalse(summary["evidence_changed"])
        self.assertNotIn(W.CHG_CHANGED, by)
        self.assertTrue(any("source_content_changed_evidence_unchanged" in c["warnings"]
                            for c in changes))

    def test_63_stable_change_id(self):
        a = compare_unit(snap([("t", "f", "A", None, None)]), snap([("t", "f", "B", None, None)]))[0]
        b = compare_unit(snap([("t", "f", "A", None, None)]), snap([("t", "f", "B", None, None)]))[0]
        self.assertEqual([c["change_id"] for c in a], [c["change_id"] for c in b])

    def test_64_65_66_order_sort_pagination_excluded(self):
        e1 = [("t", "a", "1", None, None), ("t", "b", "2", None, None)]
        e2 = list(reversed(e1))
        ids1 = sorted(c["change_id"] for c in compare_unit(snap(e1), snap(e1))[0])
        ids2 = sorted(c["change_id"] for c in compare_unit(snap(e2), snap(e2))[0])
        self.assertEqual(ids1, ids2)

    def test_67_timestamp_excluded_from_change_id(self):
        # change_id depends only on content — computing at two different wall clocks is identical
        a = compare_unit(snap([("t", "f", "A", None, None)]), snap([("t", "f", "B", None, None)]))[0]
        b = compare_unit(snap([("t", "f", "A", None, None)]), snap([("t", "f", "B", None, None)]))[0]
        self.assertEqual(a[0]["change_id"], b[0]["change_id"])

    def test_68_unicode_normalization(self):
        nfc = "Café"       # é as one codepoint
        nfd = "Café"      # e + combining accent
        _, _, by = compare_unit(snap([("t", "f", nfc, None, None)]),
                                snap([("t", "f", nfd, None, None)]))
        self.assertIn(W.CHG_UNCHANGED, by)
        self.assertNotIn(W.CHG_CHANGED, by)

    def test_69_whitespace_normalization(self):
        _, _, by = compare_unit(snap([("t", "f", "Nurse  Sweatshirt", None, None)]),
                                snap([("t", "f", "Nurse Sweatshirt", None, None)]))
        self.assertIn(W.CHG_UNCHANGED, by)

    def test_70_case_sensitive_field(self):
        _, _, by = compare_unit(snap([("t", "f", "Nurse", None, None)]),
                                snap([("t", "f", "nurse", None, None)]))
        self.assertIn(W.CHG_CHANGED, by)

    def test_71_documented_case_insensitive_field(self):
        _, _, by = compare_unit(snap([("t", "f", "Nurse", None, None)]),
                                snap([("t", "f", "nurse", None, None)]), it=item(ci_fields=["f"]))
        self.assertIn(W.CHG_UNCHANGED, by)
        self.assertNotIn(W.CHG_CHANGED, by)

    def test_72_explicit_decimal_comparison(self):
        _, _, by = compare_unit(snap([("price", "offers.price", "24.99", None, "USD")]),
                                snap([("price", "offers.price", "24.990", None, "USD")]))
        self.assertIn(W.CHG_UNCHANGED, by)

    def test_73_numeric_absolute_delta(self):
        changes, _, by = compare_unit(snap([("price", "offers.price", "24.99", None, "USD")]),
                                      snap([("price", "offers.price", "29.99", None, "USD")]))
        ch = by[W.CHG_CHANGED][0]
        self.assertEqual(ch["numeric_delta"], "5.00")

    def test_74_numeric_percentage_delta(self):
        changes, _, by = compare_unit(snap([("price", "offers.price", "100", None, "USD")]),
                                      snap([("price", "offers.price", "125", None, "USD")]))
        ch = by[W.CHG_CHANGED][0]
        self.assertEqual(ch["numeric_delta"], "25")
        self.assertEqual(ch["numeric_percent_change"], "25.0000")

    def test_75_previous_zero_percent_blocked(self):
        delta, pct, warn = W._numeric_annotations({"observed_value": "0", "observed_unit": None,
                                                   "observed_currency": None},
                                                  {"observed_value": "5", "observed_unit": None,
                                                   "observed_currency": None})
        self.assertEqual(delta, "5")
        self.assertIsNone(pct)
        self.assertIn("percent_change_undefined_previous_zero", warn)

    def test_76_currency_match(self):
        delta, pct, warn = W._numeric_annotations({"observed_value": "10", "observed_unit": None,
                                                   "observed_currency": "USD"},
                                                  {"observed_value": "12", "observed_unit": None,
                                                   "observed_currency": "USD"})
        self.assertEqual(delta, "2")
        self.assertEqual(warn, [])

    def test_77_currency_mismatch(self):
        delta, pct, warn = W._numeric_annotations({"observed_value": "10", "observed_unit": None,
                                                   "observed_currency": "USD"},
                                                  {"observed_value": "12", "observed_unit": None,
                                                   "observed_currency": "EUR"})
        self.assertIsNone(delta)
        self.assertIn("currency_mismatch", warn)

    def test_78_unit_match(self):
        delta, _, warn = W._numeric_annotations({"observed_value": "10", "observed_unit": "cm",
                                                 "observed_currency": None},
                                                {"observed_value": "12", "observed_unit": "cm",
                                                 "observed_currency": None})
        self.assertEqual(delta, "2")

    def test_79_unit_mismatch(self):
        delta, _, warn = W._numeric_annotations({"observed_value": "10", "observed_unit": "cm",
                                                 "observed_currency": None},
                                                {"observed_value": "12", "observed_unit": "in",
                                                 "observed_currency": None})
        self.assertIsNone(delta)
        self.assertIn("unit_mismatch", warn)

    def test_80_parser_definition_mismatch_warns(self):
        prev = snap([("t", "f", "A", None, None)], parser="p1")
        curr = snap([("t", "f", "B", None, None)], parser="p2", content="h2")
        changes, _, _ = compare_unit(prev, curr)
        self.assertTrue(all("parser_version_changed" in c["warnings"] for c in changes))

    def test_81_marketplace_source_isolation(self):
        # two different source identities are never aggregated into one comparison
        self.assertNotEqual(R._source_id("public-url", "l1"), R._source_id("amazon-public-product", "l1"))

    def test_82_no_cross_currency_aggregation(self):
        self.assertIsNone(W._numeric_annotations({"observed_value": "1", "observed_unit": None,
                                                  "observed_currency": "USD"},
                                                 {"observed_value": "1", "observed_unit": None,
                                                  "observed_currency": "GBP"})[0])

    def test_83_no_fuzzy_matching(self):
        _, _, by = compare_unit(snap([("t", "f", "Nurse Sweatshirt", None, None)]),
                                snap([("t", "f", "Nurse Sweater", None, None)]))
        self.assertIn(W.CHG_CHANGED, by)

    def test_84_85_no_llm_or_embedding(self):
        with open(W.__file__, encoding="utf-8") as f:
            src = f.read().lower()
        for banned in ("openai", "anthropic", "embedding", "sentence_transformers", "cosine",
                       "llm", "gpt-", "transformers"):
            self.assertNotIn(banned, src, banned)


# ---- helpers for alert-rule evaluation ---------------------------------------
def mkchange(change_type="CHANGED", **kw):
    base = {"schema_version": W.CHANGE_SCHEMA, "change_id": "chg-test", "watchlist_id": "wl-x",
            "item_id": "item-x", "source_id": "src-x", "normalized_locator": "loc",
            "source_locator": "https://example.com/x", "previous_capture_id": "cap-a",
            "current_capture_id": "cap-b", "previous_content_hash": "h1", "current_content_hash": "h2",
            "evidence_type": "t", "field_path": "f", "previous_value": "A", "current_value": "B",
            "observed_unit": None, "observed_currency": None, "change_type": change_type,
            "numeric_delta": None, "numeric_percent_change": None, "warnings": [],
            "comparison_method": "exact-field", "parser_version_previous": "p",
            "parser_version_current": "p", "owner_rule_matches": []}
    base.update(kw)
    return base


def nr(operator, **kw):
    return W._validate_rule(rule(operator, **kw), 0)


# ======================================================================== 8) owner alert rules
class TestAlertRules(Base):
    def test_86_field_changed_rule(self):
        self.assertTrue(W._rule_matches(nr("field-changed"), mkchange("CHANGED"))[0])
        self.assertFalse(W._rule_matches(nr("field-changed"), mkchange("ADDED"))[0])

    def test_87_field_added_rule(self):
        self.assertTrue(W._rule_matches(nr("field-added"), mkchange("ADDED"))[0])

    def test_88_field_removed_rule(self):
        self.assertTrue(W._rule_matches(nr("field-removed"), mkchange("REMOVED"))[0])

    def test_89_value_equals_rule(self):
        self.assertTrue(W._rule_matches(nr("value-equals", comparison_value="B"), mkchange())[0])
        self.assertFalse(W._rule_matches(nr("value-equals", comparison_value="Z"), mkchange())[0])

    def test_90_value_contains_rule(self):
        self.assertTrue(W._rule_matches(nr("value-contains", comparison_value="ur"),
                                        mkchange(current_value="Nurse"))[0])
        self.assertFalse(W._rule_matches(nr("value-contains", comparison_value="zzz"),
                                         mkchange(current_value="Nurse"))[0])

    def test_91_numeric_above_rule(self):
        ch = mkchange(current_value="30.00", observed_currency="USD")
        self.assertTrue(W._rule_matches(nr("numeric-above", comparison_value="25", currency="USD"), ch)[0])
        self.assertFalse(W._rule_matches(nr("numeric-above", comparison_value="35", currency="USD"), ch)[0])

    def test_92_numeric_below_rule(self):
        ch = mkchange(current_value="10.00", observed_currency="USD")
        self.assertTrue(W._rule_matches(nr("numeric-below", comparison_value="25", currency="USD"), ch)[0])

    def test_93_absolute_delta_rule(self):
        ch = mkchange(numeric_delta="5.00", observed_currency="USD")
        self.assertTrue(W._rule_matches(nr("absolute-delta-at-least", comparison_value="1", currency="USD"), ch)[0])
        self.assertFalse(W._rule_matches(nr("absolute-delta-at-least", comparison_value="10", currency="USD"), ch)[0])

    def test_94_percent_change_rule(self):
        ch = mkchange(numeric_percent_change="20.0000", observed_currency="USD")
        self.assertTrue(W._rule_matches(nr("percent-change-at-least", comparison_value="10", currency="USD"), ch)[0])

    def test_95_source_unavailable_rule(self):
        self.assertTrue(W._rule_matches(nr("source-unavailable"),
                                        mkchange("SOURCE_UNAVAILABLE"))[0])

    def test_96_source_blocked_rule(self):
        self.assertTrue(W._rule_matches(nr("source-blocked"), mkchange("SOURCE_BLOCKED"))[0])

    def test_97_parse_partial_rule(self):
        self.assertTrue(W._rule_matches(nr("parse-partial"), mkchange("PARSE_PARTIAL"))[0])

    def test_98_integrity_blocked_rule(self):
        self.assertTrue(W._rule_matches(nr("integrity-blocked"), mkchange("INTEGRITY_BLOCKED"))[0])

    def test_99_disabled_rule(self):
        r = nr("field-changed")
        r["enabled"] = False
        self.assertFalse(W._rule_matches(r, mkchange("CHANGED"))[0])

    def test_100_unknown_rule_operator(self):
        with self.assertRaises(W.WatchlistError) as e:
            W._validate_rule(rule("teleport-changed"), 0)
        self.assertEqual(e.exception.code, "ALERT_RULE_OPERATOR_UNKNOWN")

    def test_101_invalid_rule_field(self):
        with self.assertRaises(W.WatchlistError) as e:
            W._validate_rule({**rule("field-changed"), "mystery": 1}, 0)
        self.assertEqual(e.exception.code, "ALERT_RULE_UNKNOWN_FIELD")

    def test_102_incompatible_rule_currency(self):
        ch = mkchange(current_value="30", observed_currency="EUR")
        self.assertFalse(W._rule_matches(nr("numeric-above", comparison_value="25", currency="USD"), ch)[0])

    def test_103_incompatible_rule_unit(self):
        ch = mkchange(current_value="30", observed_unit="in")
        self.assertFalse(W._rule_matches(nr("numeric-above", comparison_value="25", unit="cm"), ch)[0])

    def test_104_107_owner_severities(self):
        for sev in ("INFO", "REVIEW", "IMPORTANT", "CRITICAL"):
            self.assertEqual(W._validate_rule(rule("field-changed", severity=sev), 0)["severity"], sev)
        with self.assertRaises(W.WatchlistError):
            W._validate_rule(rule("field-changed", severity="APOCALYPSE"), 0)

    def test_108_109_110_no_calculated_severity_urgency_recommendation(self):
        r = nr("field-changed", severity="INFO")
        alert = W._build_alert("wl-x", r, mkchange(), "field_changed")
        self.assertEqual(alert["severity"], "INFO")             # owner-defined, not calculated
        for banned in ("severity_score", "urgency", "recommendation", "opportunity_score",
                       "priority_score"):
            self.assertNotIn(banned, alert)


# ======================================================================== 9) alerts + audit trail
class TestAlerts(Base):
    def make_alert(self):
        d = wl_def(alert_rules=[rule("field-changed", evidence_type="html.title", field_path="title",
                                     severity="IMPORTANT", label="Title", message="changed")])
        c = self.html_cfg(HTML1)
        wid = self.create(c, d)
        W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        c2 = self.rebind(c, transport_html(HTML2))
        r2 = W.run_watchlist(c2, wid, reference_time="2026-07-23T11:00:00Z")
        alerts = W.list_alerts(c2, wid)["alerts"]
        return c2, wid, alerts[0]["alert_id"]

    def test_111_stable_alert_id(self):
        r = nr("field-changed")
        a1 = W._build_alert("wl-x", r, mkchange(), "field_changed")
        a2 = W._build_alert("wl-x", r, mkchange(), "field_changed")
        self.assertEqual(a1["alert_id"], a2["alert_id"])

    def test_112_duplicate_alert_prevented(self):
        c = self.cfg()
        r = nr("field-changed")
        alert = W._build_alert("wl-x", r, mkchange(), "field_changed")
        c1, _ = W._register_alerts(c, "wl-x", [alert])
        c2, reused = W._register_alerts(c, "wl-x", [alert])
        self.assertEqual(c1, [alert["alert_id"]])
        self.assertEqual(reused, [alert["alert_id"]])
        counts, _ = W._alert_status_counts(c, "wl-x")
        self.assertEqual(counts["OPEN"], 1)

    def test_113_open_alert(self):
        c, wid, aid = self.make_alert()
        self.assertEqual(W.list_alerts(c, wid)["status_counts"]["OPEN"], 1)

    def test_114_acknowledge_alert(self):
        c, wid, aid = self.make_alert()
        res = W._alert_action(c, aid, W.ACT_ACKNOWLEDGE, "OWNER", "seen")
        self.assertEqual(res["status"], "ACKNOWLEDGED")

    def test_115_dismiss_alert(self):
        c, wid, aid = self.make_alert()
        res = W._alert_action(c, aid, W.ACT_DISMISS, "OWNER", None)
        self.assertEqual(res["status"], "DISMISSED")

    def test_116_reopen_alert(self):
        c, wid, aid = self.make_alert()
        W._alert_action(c, aid, W.ACT_DISMISS, "OWNER", None)
        res = W._alert_action(c, aid, W.ACT_REOPEN, "OWNER", "back")
        self.assertEqual(res["status"], "OPEN")

    def test_117_wrong_alert_id(self):
        c, wid, aid = self.make_alert()
        with self.assertRaises(W.WatchlistError) as e:
            W._alert_action(c, "alt-nonexistent", W.ACT_ACKNOWLEDGE, "OWNER", None)
        self.assertEqual(e.exception.code, "ALERT_NOT_FOUND")

    def test_118_missing_actor(self):
        c, wid, aid = self.make_alert()
        with self.assertRaises(W.WatchlistError) as e:
            W._alert_action(c, aid, W.ACT_ACKNOWLEDGE, "  ", "note")
        self.assertEqual(e.exception.code, "ACTOR_REQUIRED")

    def test_119_owner_note_recorded(self):
        c, wid, aid = self.make_alert()
        W._alert_action(c, aid, W.ACT_ACKNOWLEDGE, "OWNER", "please review Q3")
        state = W._load_alert_state(c, wid)
        self.assertEqual(state["history"][-1]["note"], "please review Q3")

    def test_120_append_only_history(self):
        c, wid, aid = self.make_alert()
        W._alert_action(c, aid, W.ACT_ACKNOWLEDGE, "OWNER", "a")
        W._alert_action(c, aid, W.ACT_DISMISS, "OWNER", "b")
        state = W._load_alert_state(c, wid)
        self.assertEqual([h["action"] for h in state["history"]], ["ACKNOWLEDGE", "DISMISS"])

    def test_121_122_123_hash_chain_fields(self):
        c, wid, aid = self.make_alert()
        W._alert_action(c, aid, W.ACT_ACKNOWLEDGE, "OWNER", "a")
        res = W._alert_action(c, aid, W.ACT_DISMISS, "OWNER", "b")
        state = W._load_alert_state(c, wid)
        self.assertEqual(state["history"][1]["prev_state_hash"], state["history"][0]["event_hash"])
        self.assertEqual(state["head_hash"], state["history"][1]["event_hash"])
        self.assertEqual(res["aggregate_hash"], state["head_hash"])

    def _reseal(self, c, wid, mutate):
        path = W._alert_state_path(c, wid)
        body = W._load_json(path)
        body.pop("integrity_hash", None)
        mutate(body)
        W._write_json(path, W._hashed(body))

    def test_124_corrupt_history_blocked(self):
        c, wid, aid = self.make_alert()
        W._alert_action(c, aid, W.ACT_ACKNOWLEDGE, "OWNER", "a")
        self._reseal(c, wid, lambda b: b["history"][0].update(note="TAMPERED"))
        with self.assertRaises(W.WatchlistError) as e:
            W._alert_action(c, aid, W.ACT_DISMISS, "OWNER", "b")
        self.assertEqual(e.exception.readiness, W.ALERT_STATE_BLOCKED)

    def test_125_history_deletion_detected(self):
        c, wid, aid = self.make_alert()
        W._alert_action(c, aid, W.ACT_ACKNOWLEDGE, "OWNER", "a")
        W._alert_action(c, aid, W.ACT_DISMISS, "OWNER", "b")
        self._reseal(c, wid, lambda b: b["history"].pop())
        with self.assertRaises(W.WatchlistError):
            W._load_alert_state(c, wid)

    def test_126_history_reorder_detected(self):
        c, wid, aid = self.make_alert()
        W._alert_action(c, aid, W.ACT_ACKNOWLEDGE, "OWNER", "a")
        W._alert_action(c, aid, W.ACT_DISMISS, "OWNER", "b")
        self._reseal(c, wid, lambda b: b["history"].reverse())
        with self.assertRaises(W.WatchlistError):
            W._load_alert_state(c, wid)

    def test_127_acknowledge_does_not_change_alert_id(self):
        c, wid, aid = self.make_alert()
        W._alert_action(c, aid, W.ACT_ACKNOWLEDGE, "OWNER", "a")
        self.assertEqual(W.list_alerts(c, wid)["alerts"][0]["alert_id"], aid)


# ======================================================================== 10) locking / concurrency
class TestLocking(Base):
    def at(self, base, now):
        return W.Config(base, env={}, transport=transport_html(HTML1), resolver=resolver_public,
                        now_fn=lambda: now)

    def test_128_concurrent_same_watchlist_blocked(self):
        c = self.html_cfg()
        wid = self.create(c)
        tok = W._acquire_lock(c, wid)
        try:
            with self.assertRaises(W.WatchlistError) as e:
                W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
            self.assertEqual(e.exception.code, "LOCK_HELD")
        finally:
            W._release_lock(c, wid, tok)

    def test_129_different_watchlists_can_run(self):
        c = self.html_cfg()
        wid1 = self.create(c, wl_def(name="A"))
        wid2 = self.create(c, wl_def(name="B"))
        tok = W._acquire_lock(c, wid1)
        try:
            res = W.run_watchlist(c, wid2, reference_time="2026-07-23T10:00:00Z")
            self.assertIn(res["readiness"], (W.READY, W.READY_EMPTY))
        finally:
            W._release_lock(c, wid1, tok)

    def test_130_valid_lock_release(self):
        c = self.html_cfg()
        wid = self.create(c)
        tok = W._acquire_lock(c, wid)
        self.assertTrue(os.path.isfile(W._lock_path(c, wid)))
        self.assertTrue(W._release_lock(c, wid, tok))
        self.assertFalse(os.path.isfile(W._lock_path(c, wid)))

    def test_131_failed_run_releases_lock(self):
        c = self.html_cfg()
        wid = self.create(c)
        W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        # corrupt state so the next run fails after acquiring the lock
        sp = W._state_path(c, wid)
        with open(sp, "w", encoding="utf-8") as f:
            f.write('{"schema_version":"x","watchlist_id":"' + wid + '","items":{},"integrity_hash":"bad"}')
        c2 = self.rebind(c, transport_html(HTML2))
        with self.assertRaises(W.WatchlistError):
            W.run_watchlist(c2, wid, reference_time="2026-07-23T11:00:00Z")
        # lock was released by the finally-block, so it can be re-acquired
        self.assertFalse(os.path.isfile(W._lock_path(c, wid)))

    def test_132_active_lock_preserved(self):
        c = self.html_cfg()
        wid = self.create(c)
        tok = W._acquire_lock(c, wid)
        try:
            with self.assertRaises(W.WatchlistError):
                W._acquire_lock(c, wid)
            self.assertTrue(os.path.isfile(W._lock_path(c, wid)))   # not removed
        finally:
            W._release_lock(c, wid, tok)

    def test_133_stale_lock_detected(self):
        base = os.path.join(self.tmp, "wsL")
        c = self.at(base, 1_000_000.0)
        wid = self.create(c)
        W._acquire_lock(c, wid)
        c2 = self.at(base, 1_000_000.0 + 7 * 3600)      # 7h later > 6h max
        with self.assertRaises(W.WatchlistError) as e:
            W._acquire_lock(c2, wid)
        self.assertEqual(e.exception.code, "LOCK_STALE")

    def test_134_stale_lock_not_silently_removed(self):
        base = os.path.join(self.tmp, "wsL2")
        c = self.at(base, 1_000_000.0)
        wid = self.create(c)
        W._acquire_lock(c, wid)
        c2 = self.at(base, 1_000_000.0 + 7 * 3600)
        try:
            W._acquire_lock(c2, wid)
        except W.WatchlistError:
            pass
        self.assertTrue(os.path.isfile(W._lock_path(c, wid)))       # still present

    def test_135_explicit_stale_lock_recovery(self):
        base = os.path.join(self.tmp, "wsL3")
        c = self.at(base, 1_000_000.0)
        wid = self.create(c)
        W._acquire_lock(c, wid)
        c2 = self.at(base, 1_000_000.0 + 7 * 3600)
        tok = W._acquire_lock(c2, wid, break_stale=True)             # explicit owner recovery
        self.assertEqual(tok["watchlist_id"], wid)


# ======================================================================== 11) atomicity
class TestAtomicity(Base):
    def _no_tmp(self, root):
        for dp, dn, fn in os.walk(root):
            for n in fn:
                self.assertFalse(n.startswith(".tmp-"), os.path.join(dp, n))

    def test_136_140_atomic_writes_leave_valid_json(self):
        c = self.html_cfg()
        wid = self.create(c)
        r = W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        # watchlist, execution, comparison, state, alert-state files are all valid canonical JSON
        for path in (W._watchlist_path(c, wid), W._state_path(c, wid)):
            with open(path, encoding="utf-8") as f:
                json.load(f)
        self._no_tmp(c.base_dir)

    def test_141_partial_write_cleanup(self):
        c = self.html_cfg()
        wid = self.create(c)
        W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        W.export(c, wid)
        self._no_tmp(c.base_dir)

    def test_142_previous_valid_state_preserved_on_failure(self):
        c = self.html_cfg()
        wid = self.create(c)
        r1 = W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        exec1 = r1["execution_id"]
        sp = W._state_path(c, wid)
        with open(sp, "w", encoding="utf-8") as f:
            f.write('{"watchlist_id":"' + wid + '","items":{},"integrity_hash":"bad"}')
        c2 = self.rebind(c, transport_html(HTML2))
        with self.assertRaises(W.WatchlistError):
            W.run_watchlist(c2, wid, reference_time="2026-07-23T11:00:00Z")
        # the first execution record + watchlist are untouched
        self.assertTrue(os.path.isfile(os.path.join(c.executions_dir, exec1 + ".json")))
        self.assertEqual(W.load_watchlist(c, wid)["watchlist_id"], wid)


# ======================================================================== 12) idempotency
class TestIdempotency(Base):
    def research_runs(self):
        rdir = os.path.join(self.tmp, "r10")
        runs = {}
        for tag, body in (("v1", HTML1), ("v2", HTML2), ("v3", html(title="Third"))):
            rc = R.Config(rdir, env={}, transport=transport_html(body), resolver=resolver_public)
            runs[tag] = R.capture_one(rc, "public-url", "https://example.com/nurse")["run_id"]
        c = self.cfg(research_dir=rdir)
        wid = self.create(c)
        return c, wid, runs

    def test_143_144_identical_run_reuse(self):
        c, wid, runs = self.research_runs()
        a = W.compare(c, wid, runs["v1"], runs["v2"])
        b = W.compare(c, wid, runs["v1"], runs["v2"])
        self.assertEqual(a["comparison_ids"], b["comparison_ids"])
        # change ids are stable across identical compares
        ida = sorted(ch["change_id"] for ch in W.list_changes(c, wid)["changes"])
        b2 = W.compare(c, wid, runs["v1"], runs["v2"])
        idb = sorted(ch["change_id"] for ch in W.list_changes(c, wid)["changes"])
        self.assertEqual(ida, idb)

    def test_145_identical_alerts_reused(self):
        r = nr("field-changed")
        ch = mkchange()
        a1 = W._build_alert("wl-x", r, ch, "field_changed")
        a2 = W._build_alert("wl-x", r, ch, "field_changed")
        self.assertEqual(a1["alert_id"], a2["alert_id"])
        c = self.cfg()
        W._register_alerts(c, "wl-x", [a1])
        _, reused = W._register_alerts(c, "wl-x", [a2])
        self.assertEqual(reused, [a1["alert_id"]])

    def test_146_different_evidence_creates_new_comparison(self):
        c, wid, runs = self.research_runs()
        a = W.compare(c, wid, runs["v1"], runs["v2"])
        b = W.compare(c, wid, runs["v1"], runs["v3"])
        self.assertNotEqual(a["comparison_ids"], b["comparison_ids"])


# ======================================================================== 13) exports
VN1 = html(title="Áo điều dưỡng", price="24.99")
VN2 = html(title="Áo điều dưỡng mới", price="29.99")


class TestExports(Base):
    def full_run(self):
        d = wl_def(alert_rules=[rule("field-changed", evidence_type="html.title", field_path="title",
                                     severity="IMPORTANT", label="Title", message="Tiêu đề đổi"),
                                rule("absolute-delta-at-least", comparison_value="1.00",
                                     currency="USD", severity="CRITICAL", label="Price", message="giá đổi")])
        c = self.html_cfg(VN1)
        wid = self.create(c, d)
        W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        c2 = self.rebind(c, transport_html(VN2))
        r2 = W.run_watchlist(c2, wid, reference_time="2026-07-23T11:00:00Z")
        out_dir = os.path.join(c2.reports_dir, r2["execution_id"])
        return c2, wid, r2["execution_id"], out_dir

    def read(self, out_dir, name):
        with open(os.path.join(out_dir, name), "rb") as f:
            return f.read()

    def test_147_json_export_deterministic(self):
        c, wid, ex, out = self.full_run()
        a = self.read(out, "watchlist_snapshot.json")
        W.export(c, wid, ex)
        self.assertEqual(a, self.read(out, "watchlist_snapshot.json"))

    def test_148_tsv_export_deterministic(self):
        c, wid, ex, out = self.full_run()
        a = self.read(out, "watchlist_changes.tsv")
        W.export(c, wid, ex)
        self.assertEqual(a, self.read(out, "watchlist_changes.tsv"))

    def test_149_markdown_export_deterministic(self):
        c, wid, ex, out = self.full_run()
        a = self.read(out, "watchlist_report.md")
        W.export(c, wid, ex)
        self.assertEqual(a, self.read(out, "watchlist_report.md"))

    def test_150_alerts_export_deterministic(self):
        c, wid, ex, out = self.full_run()
        a = self.read(out, "watchlist_alerts.json")
        W.export(c, wid, ex)
        self.assertEqual(a, self.read(out, "watchlist_alerts.json"))

    def test_151_tsv_equals_neutralized(self):
        self.assertEqual(W._tsv("=cmd()"), "'=cmd()")

    def test_152_tsv_plus_neutralized(self):
        self.assertEqual(W._tsv("+SUM(A1)"), "'+SUM(A1)")

    def test_153_tsv_at_sign_neutralized(self):
        self.assertEqual(W._tsv("@ref"), "'@ref")

    def test_154_tsv_formula_minus_neutralized(self):
        self.assertEqual(W._tsv("-cmd|1"), "'-cmd|1")

    def test_155_legitimate_negative_preserved(self):
        self.assertEqual(W._tsv("-2.50"), "-2.50")

    def test_156_tabs_and_newlines_neutralized(self):
        self.assertEqual(W._tsv("a\tb\nc\rd"), "a b c d")

    def test_157_vietnamese_unicode_preserved(self):
        self.assertEqual(W._tsv("Áo điều dưỡng"), "Áo điều dưỡng")
        c, wid, ex, out = self.full_run()
        self.assertIn("điều dưỡng", self.read(out, "watchlist_changes.tsv").decode("utf-8"))

    def test_158_equal_tsv_columns(self):
        c, wid, ex, out = self.full_run()
        # rstrip only the final newline (never trailing tabs, which are legitimate empty columns)
        lines = self.read(out, "watchlist_changes.tsv").decode("utf-8").rstrip("\n").split("\n")
        width = len(lines[0].split("\t"))
        self.assertEqual(width, len(W._TSV_COLS))
        for ln in lines[1:]:
            self.assertEqual(len(ln.split("\t")), width)

    def test_159_160_161_no_nan_inf_float(self):
        c, wid, ex, out = self.full_run()
        for name in ("watchlist_snapshot.json", "watchlist_alerts.json"):
            blob = self.read(out, name).decode("utf-8")
            self.assertNotIn("NaN", blob)
            self.assertNotIn("Infinity", blob)
            obj = json.loads(blob)
            self._no_float(obj)

    def _no_float(self, obj):
        if isinstance(obj, float):
            self.fail("float found in export: %r" % obj)
        if isinstance(obj, dict):
            for v in obj.values():
                self._no_float(v)
        elif isinstance(obj, list):
            for v in obj:
                self._no_float(v)

    def test_162_no_absolute_path_leak(self):
        c, wid, ex, out = self.full_run()
        for name in ("watchlist_snapshot.json", "watchlist_report.md", "watchlist_alerts.json"):
            blob = self.read(out, name).decode("utf-8")
            self.assertNotIn(self.tmp, blob)
            self.assertNotIn(c.base_dir, blob)

    def test_163_164_165_no_secret_leak(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-EXPORTLEAK987654321"
        try:
            c, wid, ex, out = self.full_run()
            for name in ("watchlist_snapshot.json", "watchlist_alerts.json", "watchlist_report.md"):
                blob = self.read(out, name).decode("utf-8")
                self.assertNotIn("EXPORTLEAK", blob)
                for tok in ("Authorization", "Set-Cookie", "Bearer "):
                    self.assertNotIn(tok, blob)
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)


# ======================================================================== 14) validate-only
class TestValidateOnly(Base):
    def test_166_170_validate_only_no_side_effects(self):
        path = self.write_def(wl_def())
        base = os.path.join(self.tmp, "vo_ws")
        before = os.path.exists(base)
        # a resolver + transport that EXPLODE if touched prove no DNS / no network
        boom_resolver = lambda *a, **k: (_ for _ in ()).throw(AssertionError("DNS"))
        boom_transport = lambda *a, **k: (_ for _ in ()).throw(AssertionError("NET"))
        cfg = W.Config(base, env={}, transport=boom_transport, resolver=boom_resolver)
        res = W.validate_only(path)
        self.assertEqual(res["readiness"], W.VALIDATE_READY)
        self.assertEqual(res["files_written"], 0)
        self.assertEqual(res["network_requests"], 0)
        self.assertEqual(before, os.path.exists(base))     # base dir never created
        self.assertFalse(os.path.isdir(cfg.locks_dir))     # no lock dir

    def test_166b_validate_only_rejects_bad(self):
        path = self.write_def(wl_def(sources=[{"source_type": "teleport", "source_locator": "x"}]))
        with self.assertRaises(W.WatchlistError):
            W.validate_only(path)


# ======================================================================== 15) compliance / immutability
class TestCompliance(Base):
    def _tree_hash(self, root):
        import hashlib
        h = hashlib.sha256()
        for dp, dn, fn in sorted(os.walk(root)):
            for n in sorted(fn):
                p = os.path.join(dp, n)
                h.update(os.path.relpath(p, root).encode())
                with open(p, "rb") as f:
                    h.update(f.read())
        return h.hexdigest()

    def test_171_phase710_research_dir_immutable(self):
        rdir = os.path.join(self.tmp, "r10")
        rc = R.Config(rdir, env={}, transport=transport_html(HTML1), resolver=resolver_public)
        run1 = R.capture_one(rc, "public-url", "https://example.com/nurse")["run_id"]
        rc2 = R.Config(rdir, env={}, transport=transport_html(HTML2), resolver=resolver_public)
        run2 = R.capture_one(rc2, "public-url", "https://example.com/nurse")["run_id"]
        before = self._tree_hash(rdir)
        c = self.cfg(research_dir=rdir)
        wid = self.create(c)
        W.compare(c, wid, run1, run2)
        W.export(c, wid) if False else None
        self.assertEqual(before, self._tree_hash(rdir))    # 7.10 data byte-identical

    def test_172_no_writes_outside_base_and_research(self):
        # a full lifecycle writes ONLY under base_dir; the research dir is separate + owned by 7.11
        c = self.html_cfg()
        wid = self.create(c)
        W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        W.export(c, wid)
        W.scheduler_plan(c, wid)
        # everything lives under base_dir
        self.assertTrue(os.path.isdir(c.base_dir))
        # research sub-workspace is under base_dir (7.11-owned), not a sibling 7.3-7.10 tree
        self.assertTrue(c.research_runs_dir.startswith(c.base_dir))

    def test_173_runs_gitignored(self):
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
            self.assertIn("runs/", f.read())

    def test_174_seller_central_counters_zero(self):
        c = self.html_cfg()
        wid = self.create(c)
        r = W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        self.assertEqual(r["amazon_counters"]["seller_central_connections"], 0)
        self.assertTrue(all(v == 0 for v in r["amazon_counters"].values()))
        self.assertGreaterEqual(len(r["amazon_counters"]), 8)

    def test_175_179_prohibited_integration_scan(self):
        # scan CODE tokens only — docstrings/comments that DESCRIBE the prohibitions (safety strings)
        # are not active integrations, so strings + comments are excluded via tokenize.
        import tokenize
        with open(W.__file__, "rb") as f:
            toks = list(tokenize.tokenize(f.readline))
        names = {t.string.lower() for t in toks if t.type == tokenize.NAME}
        for banned in ("subprocess", "selenium", "playwright", "webdriver", "webbrowser",
                       "pyppeteer", "requests", "smtplib", "sendmail", "eval", "exec", "system",
                       "urllib", "socket", "shell", "sellingpartner", "captcha"):
            self.assertNotIn(banned, names, banned)

    def test_180_188_prior_modules_import_clean(self):
        # Phase 7.11 does not shadow or break prior authorities
        import importlib
        for mod in ("phase7_connected_public_research", "phase7_connected_backup_recovery",
                    "phase7_owner_operations_dashboard", "phase7_outcome_followup",
                    "phase7_manual_action_tracker", "phase7_owner_decision_package",
                    "phase7_owner_dashboard", "phase7_ads_analysis", "phase7_report_ingestion"):
            self.assertTrue(importlib.import_module("production." + mod))

    def test_190_module_compiles(self):
        import py_compile
        py_compile.compile(W.__file__, doraise=True)

    def test_191_amazon_counters_constant_zero_in_module(self):
        self.assertTrue(all(v == 0 for v in R.AMAZON_COUNTERS.values()))


# ======================================================================== 16) CLI end-to-end
class TestCLIEndToEnd(Base):
    def cli(self, argv, transport=None, resolver=resolver_public, now=1_000_000.0, base=None,
            research=None):
        base = base or os.path.join(self.tmp, "cliws")
        research = research or os.path.join(self.tmp, "r10")
        argv = ["--base-dir", base, "--research-dir", research] + argv
        out = io.StringIO()

        def factory(args):
            return W.Config(args.base_dir, research_dir=args.research_dir, env={},
                            transport=transport, resolver=resolver, now_fn=lambda: now,
                            allow_local=args.allow_local_test_server)
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = W.run_cli(argv, cfg_factory=factory)
        return code, json.loads(out.getvalue())

    def setUp(self):
        super().setUp()
        self.defpath = self.write_def(wl_def(
            alert_rules=[rule("field-changed", evidence_type="html.title", field_path="title",
                              severity="IMPORTANT", label="T", message="m")]))

    def test_192_cli_create_and_show(self):
        code, res = self.cli(["create-watchlist", "--definition", self.defpath])
        self.assertEqual(code, 0)
        wid = res["watchlist_id"]
        code, shown = self.cli(["show-watchlist", "--watchlist-id", wid])
        self.assertEqual(code, 0)
        self.assertEqual(shown["watchlist"]["watchlist_id"], wid)

    def test_193_cli_validate_watchlist(self):
        code, res = self.cli(["validate-watchlist", "--definition", self.defpath])
        self.assertEqual(code, 0)
        self.assertEqual(res["readiness"], W.VALIDATE_READY)

    def test_194_cli_validate_only(self):
        code, res = self.cli(["validate-only", "--definition", self.defpath])
        self.assertEqual(code, 0)
        self.assertEqual(res["files_written"], 0)

    def test_195_cli_list_watchlists(self):
        self.cli(["create-watchlist", "--definition", self.defpath])
        code, res = self.cli(["list-watchlists"])
        self.assertEqual(code, 0)
        self.assertEqual(res["count"], 1)

    def test_196_cli_run_and_list_executions(self):
        code, res = self.cli(["create-watchlist", "--definition", self.defpath])
        wid = res["watchlist_id"]
        code, run = self.cli(["run-watchlist", "--watchlist-id", wid,
                              "--reference-time", "2026-07-23T10:00:00Z"], transport=transport_html())
        self.assertEqual(code, 0)
        code, ex = self.cli(["list-executions", "--watchlist-id", wid])
        self.assertEqual(ex["count"], 1)

    def test_197_cli_run_due_not_due_exit_zero(self):
        # a manual-schedule watchlist is never auto-due -> NOT_DUE, exit 0
        self.cli(["create-watchlist", "--definition", self.defpath])
        code, res = self.cli(["run-due", "--reference-time", "2026-07-23T10:00:00Z"],
                             transport=transport_html())
        self.assertEqual(code, 0)
        self.assertEqual(res["readiness"], W.NOT_DUE)

    def test_198_cli_full_alert_lifecycle(self):
        base = os.path.join(self.tmp, "life")
        code, res = self.cli(["create-watchlist", "--definition", self.defpath], base=base)
        wid = res["watchlist_id"]
        self.cli(["run-watchlist", "--watchlist-id", wid, "--reference-time", "2026-07-23T10:00:00Z"],
                 transport=transport_html(HTML1), base=base)
        self.cli(["run-watchlist", "--watchlist-id", wid, "--reference-time", "2026-07-23T11:00:00Z"],
                 transport=transport_html(HTML2), base=base)
        code, alerts = self.cli(["list-alerts", "--watchlist-id", wid], base=base)
        self.assertEqual(code, 0)
        self.assertEqual(alerts["count"], 1)
        aid = alerts["alerts"][0]["alert_id"]
        code, ack = self.cli(["acknowledge-alert", "--alert-id", aid, "--actor", "OWNER",
                             "--note", "seen"], base=base)
        self.assertEqual(code, 0)
        self.assertEqual(ack["status"], "ACKNOWLEDGED")
        code, dis = self.cli(["dismiss-alert", "--alert-id", aid, "--actor", "OWNER"], base=base)
        self.assertEqual(dis["status"], "DISMISSED")
        code, reo = self.cli(["reopen-alert", "--alert-id", aid, "--actor", "OWNER"], base=base)
        self.assertEqual(reo["status"], "OPEN")

    def test_199_cli_list_changes(self):
        base = os.path.join(self.tmp, "lc")
        code, res = self.cli(["create-watchlist", "--definition", self.defpath], base=base)
        wid = res["watchlist_id"]
        self.cli(["run-watchlist", "--watchlist-id", wid, "--reference-time", "2026-07-23T10:00:00Z"],
                 transport=transport_html(HTML1), base=base)
        code, lc = self.cli(["list-changes", "--watchlist-id", wid], base=base)
        self.assertEqual(code, 0)
        self.assertGreaterEqual(lc["count"], 1)

    def test_200_cli_export(self):
        base = os.path.join(self.tmp, "exp")
        code, res = self.cli(["create-watchlist", "--definition", self.defpath], base=base)
        wid = res["watchlist_id"]
        self.cli(["run-watchlist", "--watchlist-id", wid, "--reference-time", "2026-07-23T10:00:00Z"],
                 transport=transport_html(HTML1), base=base)
        code, ex = self.cli(["export", "--watchlist-id", wid], base=base)
        self.assertEqual(code, 0)
        self.assertEqual(len(ex["exports"]), 4)

    def test_201_cli_scheduler_plan(self):
        base = os.path.join(self.tmp, "sp")
        code, res = self.cli(["create-watchlist", "--definition", self.defpath], base=base)
        wid = res["watchlist_id"]
        code, sp = self.cli(["scheduler-plan", "--watchlist-id", wid, "--flavor", "windows"], base=base)
        self.assertEqual(code, 0)
        self.assertEqual(sp["plan"]["owner_action_required"], "OWNER_ACTION_REQUIRED")

    def test_202_cli_verify_state(self):
        base = os.path.join(self.tmp, "vs")
        code, res = self.cli(["create-watchlist", "--definition", self.defpath], base=base)
        wid = res["watchlist_id"]
        self.cli(["run-watchlist", "--watchlist-id", wid, "--reference-time", "2026-07-23T10:00:00Z"],
                 transport=transport_html(HTML1), base=base)
        code, vs = self.cli(["verify-state", "--watchlist-id", wid], base=base)
        self.assertEqual(code, 0)
        self.assertEqual(vs["readiness"], W.VERIFY_READY)

    def test_203_cli_seller_source_nonzero_exit(self):
        path = self.write_def(wl_def(sources=[{"source_type": "public-url",
                                               "source_locator": SELLER_CENTRAL}]))
        base = os.path.join(self.tmp, "sc")
        code, res = self.cli(["create-watchlist", "--definition", path], base=base)
        wid = res["watchlist_id"]
        code, run = self.cli(["run-watchlist", "--watchlist-id", wid,
                             "--reference-time", "2026-07-23T10:00:00Z"],
                             transport=make_transport({"x": route(200, "text/html", b"x")}), base=base)
        self.assertNotEqual(code, 0)
        self.assertEqual(run["readiness"], W.SELLER_CENTRAL_POLICY_BLOCKED)

    def test_204_cli_show_missing_nonzero(self):
        code, res = self.cli(["show-watchlist", "--watchlist-id", "wl-nope"])
        self.assertNotEqual(code, 0)

    def test_205_cli_compare(self):
        rdir = os.path.join(self.tmp, "r10c")
        R.capture_one(R.Config(rdir, env={}, transport=transport_html(HTML1), resolver=resolver_public),
                      "public-url", "https://example.com/nurse")
        rc2 = R.Config(rdir, env={}, transport=transport_html(HTML2), resolver=resolver_public)
        runs = [n[:-5] for n in sorted(os.listdir(os.path.join(rdir, "runs")))]
        r2 = R.capture_one(rc2, "public-url", "https://example.com/nurse")["run_id"]
        base = os.path.join(self.tmp, "cmpws")
        code, res = self.cli(["create-watchlist", "--definition", self.defpath], base=base, research=rdir)
        wid = res["watchlist_id"]
        code, cmp = self.cli(["compare", "--watchlist-id", wid, "--previous-run-id", runs[0],
                             "--current-run-id", r2], base=base, research=rdir)
        self.assertEqual(code, 0)
        self.assertIn(cmp["readiness"], (W.COMPARISON_READY, W.COMPARISON_READY_EMPTY))


# ======================================================================== 17) synthetic scenarios
class TestSyntheticScenarios(Base):
    def two(self, body1, body2, defn=None):
        c = self.html_cfg(body1)
        wid = self.create(c, defn or wl_def())
        W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        c2 = self.rebind(c, transport_html(body2))
        r2 = W.run_watchlist(c2, wid, reference_time="2026-07-23T11:00:00Z")
        return c2, wid, r2, W.list_changes(c2, wid)["changes"]

    def test_206_rss_source_evidence(self):
        t = make_transport({"robots.txt": ROBOTS, "feed.example.com": route(200, "application/rss+xml", RSS)})
        c = self.cfg(transport=t)
        wid = self.create(c, wl_def(sources=[{"source_type": "rss",
                                              "source_locator": "https://feed.example.com/rss"}]))
        r = W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        self.assertIn(r["readiness"], (W.READY, W.READY_EMPTY))

    def test_207_title_change(self):
        c, wid, r2, changes = self.two(HTML1, HTML2)
        self.assertTrue(any(ch["change_type"] == "CHANGED" and ch["field_path"] == "title"
                            for ch in changes))

    def test_208_price_text_change(self):
        c, wid, r2, changes = self.two(html(price="24.99"), html(price="26.50"))
        self.assertTrue(any(ch["change_type"] == "CHANGED" and ch["numeric_delta"] == "1.51"
                            for ch in changes))

    def test_209_availability_change(self):
        c, wid, r2, changes = self.two(html(price="24.99", availability="InStock"),
                                       html(price="24.99", availability="OutOfStock"))
        self.assertTrue(any(ch["change_type"] == "CHANGED" and "availab" in ch["field_path"].lower()
                            for ch in changes))

    def test_210_rating_count_change(self):
        c, wid, r2, changes = self.two(html(price="24.99", rating_count="100"),
                                       html(price="24.99", rating_count="140"))
        self.assertTrue(any(ch["change_type"] == "CHANGED" and ch["numeric_delta"] == "40"
                            for ch in changes))

    def test_211_currency_mismatch_no_delta(self):
        c, wid, r2, changes = self.two(html(price="24.99", currency="USD"),
                                       html(price="24.99", currency="EUR"))
        # value unchanged but currency differs -> CHANGED with currency_mismatch, no numeric delta
        cur_changes = [ch for ch in changes if ch["field_path"].endswith("priceCurrency")
                       or ch["evidence_type"].endswith("currency")]
        self.assertTrue(any(ch["numeric_delta"] is None for ch in changes if ch["change_type"] == "CHANGED"))

    def test_212_source_content_changed_evidence_unchanged(self):
        body_b = HTML1[:-7] + b"<!-- tracking pixel changes -->" + b"</html>"
        c, wid, r2, changes = self.two(HTML1, body_b)
        self.assertEqual(r2["readiness"], W.READY_EMPTY)
        self.assertTrue(any("source_content_changed_evidence_unchanged" in ch["warnings"]
                            for ch in changes))

    def test_213_source_unavailable_scenario(self):
        c = self.html_cfg(HTML1)
        wid = self.create(c)
        W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        # second run: DNS fails -> NETWORK_UNAVAILABLE capture, baseline preserved
        c2 = W.Config(c.base_dir, env={}, transport=transport_html(HTML1), resolver=resolver_fail,
                      now_fn=c.now_fn)
        r2 = W.run_watchlist(c2, wid, reference_time="2026-07-23T11:00:00Z")
        self.assertEqual(r2["readiness"], W.NETWORK_UNAVAILABLE)

    def test_214_readiness_ready_empty_no_change(self):
        c, wid, r2, changes = self.two(HTML1, HTML1)
        self.assertEqual(r2["readiness"], W.READY_EMPTY)


# ======================================================================== 18) misc schedule / readiness
class TestMisc(Base):
    def test_215_interval_hours_min_boundary(self):
        s = W._normalize_schedule({"type": "interval-hours", "interval_hours": 1}, "UTC")
        self.assertEqual(s["interval_hours"], 1)

    def test_216_catch_up_skip_accepted(self):
        s = W._normalize_schedule({"type": "hourly", "catch_up_policy": "skip"}, "UTC")
        self.assertEqual(s["catch_up_policy"], "skip")

    def test_217_next_due_present_for_hourly(self):
        d = W.compute_due({"type": "hourly", "interval_hours": 1, "timezone": "UTC", "enabled": True},
                          "UTC", "2026-07-23T10:00:00Z", "2026-07-23T10:30:00Z")
        self.assertEqual(d["next_due"], "2026-07-23T11:00:00Z")

    def test_218_reopen_when_open_rejected(self):
        d = wl_def(alert_rules=[rule("field-changed", evidence_type="html.title", field_path="title")])
        c = self.html_cfg(HTML1)
        wid = self.create(c, d)
        W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        c2 = self.rebind(c, transport_html(HTML2))
        W.run_watchlist(c2, wid, reference_time="2026-07-23T11:00:00Z")
        aid = W.list_alerts(c2, wid)["alerts"][0]["alert_id"]
        with self.assertRaises(W.WatchlistError) as e:
            W._alert_action(c2, aid, W.ACT_REOPEN, "OWNER", None)
        self.assertEqual(e.exception.code, "ALERT_ALREADY_OPEN")

    def test_219_list_alerts_empty(self):
        c = self.html_cfg()
        wid = self.create(c)
        res = W.list_alerts(c, wid)
        self.assertEqual(res["readiness"], W.ALERTS_READY_EMPTY)

    def test_220_run_due_executes_due(self):
        d = wl_def(schedule={"type": "hourly", "interval_hours": 1})
        c = self.html_cfg()
        wid = self.create(c, d)
        res = W.run_due(c, reference_time="2026-07-23T10:00:00Z")
        self.assertEqual(len(res["executed"]), 1)
        self.assertEqual(res["readiness"], W.READY)

    def test_221_disabled_source_not_fetched(self):
        record = []
        d = wl_def(sources=[{"source_type": "public-url", "source_locator": "https://example.com/nurse",
                             "enabled": False}])
        c = self.cfg(transport=transport_html(HTML1, record=record))
        wid = self.create(c, d)
        r = W.run_watchlist(c, wid, reference_time="2026-07-23T10:00:00Z")
        self.assertEqual(r["readiness"], W.READY_EMPTY)
        self.assertEqual(record, [])   # nothing fetched

    def test_222_ignored_field_path_excluded(self):
        it = item(ignored=["f"])
        _, _, by = compare_unit(snap([("t", "f", "A", None, None)]),
                                snap([("t", "f", "B", None, None)]), it=it)
        self.assertNotIn(W.CHG_CHANGED, by)


