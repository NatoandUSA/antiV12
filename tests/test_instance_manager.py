#!/usr/bin/env python3
"""Session 5C — single-instance lifecycle authority (Part C, D, E, F).

Fast mocked tests for identity/state/port/stale/stop safety, plus a small bounded
LIVE lifecycle that starts and stops the real dashboard on an ephemeral port under a
temporary current-user home. Nothing here touches the owner's real %LOCALAPPDATA%,
and no unknown process is ever killed.
"""
import os
import socket
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for d in ("core", "listing", "dashboard"):
    sys.path.insert(0, os.path.join(ROOT, d))
import instance_manager as IM
import runtime_policy as RP


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def make_policy(port=5000):
    return RP.load_runtime_policy(env={"BIND_PORT": str(port)})


def ours_health(pid, iid, ok=True, status=200):
    return {"service": IM.SERVICE_NAME, "pid": pid, "instance_id": iid,
            "ok": ok, "_http_status": status}


class TempHome(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="amzim_")
        self.env = {"AMZ_FBM_HOME": self.home}
        self.policy = make_policy(5000)
        IM.AP.ensure_dirs(self.env)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.home, ignore_errors=True)

    def write_meta(self, pid=4242, iid="deadbeefcafef00d", start="T1",
                   host="127.0.0.1", port=5000):
        meta = {"schema_version": "1.0", "service": IM.SERVICE_NAME, "pid": pid,
                "instance_id": iid, "process_start_time": start, "bind_host": host,
                "bind_port": port, "health_url": f"http://{host}:{port}/healthz"}
        IM.write_metadata(meta, self.env)
        return meta


class Identity(unittest.TestCase):
    def test_19_pid_alone_is_insufficient(self):
        meta = {"pid": 100, "instance_id": "abc"}
        # only a matching pid, no service corroboration -> rejected
        self.assertFalse(IM.verify_identity(meta, {"pid": 100})["ok"])
        # matching pid + our service, but instance_id mismatch -> rejected (test 22)
        self.assertFalse(IM.verify_identity(
            meta, ours_health(100, "WRONG"))["ok"])
        # matching pid + our service + matching instance_id -> verified
        self.assertTrue(IM.verify_identity(meta, ours_health(100, "abc"))["ok"])

    def test_21_foreign_service_is_not_us(self):
        self.assertFalse(IM.verify_identity(
            {"pid": 1, "instance_id": "a"},
            {"service": "some-other-service", "pid": 1, "instance_id": "a"})["ok"])


class StateMachine(TempHome):
    def test_20_start_time_mismatch_is_stale_reused(self):
        self.write_meta(pid=4242, start="OLD")
        with mock.patch.object(IM, "probe_pid_alive", lambda pid: True), \
             mock.patch.object(IM, "probe_pid_start_time", lambda pid: "NEW"), \
             mock.patch.object(IM, "port_listening", lambda h, p, timeout=0.5: False), \
             mock.patch.object(IM, "http_get_json", lambda u, timeout=2.0: None):
            ev = IM.evaluate(self.policy, self.env)
        self.assertEqual(ev["status"], IM.STALE_RUNTIME_STATE)
        self.assertTrue(ev["reused_pid"])
        self.assertIsNone(ev["pid"])           # never surface an unverified pid

    def test_21_wrong_executable_service_unverified(self):
        m = self.write_meta(pid=555, start="T", iid="iid")
        foreign = {"service": "not-us", "_http_status": 200, "ok": True}
        with mock.patch.object(IM, "probe_pid_alive", lambda pid: True), \
             mock.patch.object(IM, "probe_pid_start_time", lambda pid: "T"), \
             mock.patch.object(IM, "port_listening", lambda h, p, timeout=0.5: True), \
             mock.patch.object(IM, "http_get_json", lambda u, timeout=2.0: foreign):
            ev = IM.evaluate(self.policy, self.env)
        self.assertEqual(ev["status"], IM.INSTANCE_IDENTITY_UNVERIFIED)

    def test_22_wrong_health_identity_unverified(self):
        m = self.write_meta(pid=555, start="T", iid="rightid")
        with mock.patch.object(IM, "probe_pid_alive", lambda pid: True), \
             mock.patch.object(IM, "probe_pid_start_time", lambda pid: "T"), \
             mock.patch.object(IM, "port_listening", lambda h, p, timeout=0.5: True), \
             mock.patch.object(IM, "http_get_json",
                               lambda u, timeout=2.0: ours_health(555, "wrongid")):
            ev = IM.evaluate(self.policy, self.env)
        self.assertEqual(ev["status"], IM.INSTANCE_IDENTITY_UNVERIFIED)

    def test_23_verified_running_healthy(self):
        m = self.write_meta(pid=555, start="T", iid="iid")
        with mock.patch.object(IM, "probe_pid_alive", lambda pid: True), \
             mock.patch.object(IM, "probe_pid_start_time", lambda pid: "T"), \
             mock.patch.object(IM, "port_listening", lambda h, p, timeout=0.5: True), \
             mock.patch.object(IM, "http_get_json",
                               lambda u, timeout=2.0: ours_health(555, "iid")):
            ev = IM.evaluate(self.policy, self.env)
        self.assertEqual(ev["status"], IM.RUNNING_HEALTHY)
        self.assertTrue(ev["identity_verified"])
        self.assertEqual(ev["pid"], 555)

    def test_24_dead_pid_is_stale_and_recovers(self):
        self.write_meta(pid=999999)
        with mock.patch.object(IM, "probe_pid_alive", lambda pid: False), \
             mock.patch.object(IM, "port_listening", lambda h, p, timeout=0.5: False), \
             mock.patch.object(IM, "http_get_json", lambda u, timeout=2.0: None):
            ev = IM.evaluate(self.policy, self.env)
        self.assertEqual(ev["status"], IM.STALE_RUNTIME_STATE)
        IM.recover_stale_state(self.env)
        self.assertIsNone(IM.read_metadata(self.env)[0])
        self.assertTrue(os.path.exists(
            os.path.join(IM.AP.runtime_dir(self.env), "instance.stale.json")))

    def test_26_corrupt_metadata_is_stale(self):
        with open(IM.AP.instance_metadata_path(self.env), "w", encoding="utf-8") as f:
            f.write('{"pid": 5, ')          # truncated / partial write residue
        meta, corrupt = IM.read_metadata(self.env)
        self.assertIsNone(meta)
        self.assertTrue(corrupt)
        with mock.patch.object(IM, "port_listening", lambda h, p, timeout=0.5: False), \
             mock.patch.object(IM, "http_get_json", lambda u, timeout=2.0: None):
            ev = IM.evaluate(self.policy, self.env)
        self.assertEqual(ev["status"], IM.STALE_RUNTIME_STATE)

    def test_29_port_conflict_unknown_process(self):
        # no metadata, something listening, not our health -> unknown process
        with mock.patch.object(IM, "port_listening", lambda h, p, timeout=0.5: True), \
             mock.patch.object(IM, "http_get_json", lambda u, timeout=2.0: None):
            ev = IM.evaluate(self.policy, self.env)
        self.assertEqual(ev["status"], IM.PORT_IN_USE_BY_UNKNOWN_PROCESS)

    def test_18_metadata_atomic_and_hashed(self):
        meta = IM.build_metadata(self.policy, 111, "iid1", "pythonw.exe", "cmd",
                                 "T1", "2026-07-17T00:00:00")
        IM.write_metadata(meta, self.env)
        back, corrupt = IM.read_metadata(self.env)
        self.assertFalse(corrupt)
        self.assertEqual(back["pid"], 111)
        self.assertEqual(back["metadata_sha256"], IM._metadata_sha(back))
        residue = [n for n in os.listdir(IM.AP.runtime_dir(self.env))
                   if n.startswith(".tmp-")]
        self.assertEqual(residue, [])


class StopSafety(TempHome):
    def test_25_reused_pid_is_not_killed(self):
        self.write_meta(pid=4242, start="OLD")
        killed = []
        with mock.patch.object(IM, "probe_pid_alive", lambda pid: True), \
             mock.patch.object(IM, "probe_pid_start_time", lambda pid: "NEW"), \
             mock.patch.object(IM, "port_listening", lambda h, p, timeout=0.5: False), \
             mock.patch.object(IM, "http_get_json", lambda u, timeout=2.0: None), \
             mock.patch.object(IM, "_terminate_tree",
                               lambda *a, **k: killed.append(a) or {"terminated": True}):
            res = IM.stop_instance(env=self.env, policy=self.policy)
        self.assertEqual(res["status"], IM.NOT_RUNNING)
        self.assertEqual(killed, [])           # reused pid must NOT be terminated

    def test_28_unknown_process_on_port_never_killed(self):
        killed = []
        with mock.patch.object(IM, "port_listening", lambda h, p, timeout=0.5: True), \
             mock.patch.object(IM, "http_get_json", lambda u, timeout=2.0: None), \
             mock.patch.object(IM, "_terminate_tree",
                               lambda *a, **k: killed.append(a) or {"terminated": True}):
            res = IM.stop_instance(env=self.env, policy=self.policy)
        self.assertEqual(res["status"], IM.PORT_IN_USE_BY_UNKNOWN_PROCESS)
        self.assertFalse(res["ok"])
        self.assertEqual(killed, [])

    def test_33_only_verified_tree_is_stopped(self):
        self.write_meta(pid=555, start="T", iid="iid")
        killed = []
        with mock.patch.object(IM, "probe_pid_alive", lambda pid: True), \
             mock.patch.object(IM, "probe_pid_start_time", lambda pid: "T"), \
             mock.patch.object(IM, "port_listening", lambda h, p, timeout=0.5: True), \
             mock.patch.object(IM, "http_get_json",
                               lambda u, timeout=2.0: ours_health(555, "iid")), \
             mock.patch.object(IM, "_terminate_tree",
                               lambda pid, **k: killed.append(pid) or {"terminated": True}), \
             mock.patch.object(IM, "_wait_until_stopped", lambda *a, **k: True):
            res = IM.stop_instance(env=self.env, policy=self.policy)
        self.assertEqual(res["status"], IM.STOPPED_OK)
        self.assertEqual(killed, [555])         # exactly the verified pid, nothing else
        self.assertIsNone(IM.read_metadata(self.env)[0])

    def test_36_metadata_kept_when_stop_unconfirmed(self):
        self.write_meta(pid=555, start="T", iid="iid")
        with mock.patch.object(IM, "probe_pid_alive", lambda pid: True), \
             mock.patch.object(IM, "probe_pid_start_time", lambda pid: "T"), \
             mock.patch.object(IM, "port_listening", lambda h, p, timeout=0.5: True), \
             mock.patch.object(IM, "http_get_json",
                               lambda u, timeout=2.0: ours_health(555, "iid")), \
             mock.patch.object(IM, "_terminate_tree",
                               lambda pid, **k: {"terminated": False}), \
             mock.patch.object(IM, "_wait_until_stopped", lambda *a, **k: False):
            res = IM.stop_instance(env=self.env, policy=self.policy)
        self.assertEqual(res["status"], IM.STOP_FAILED)
        self.assertIsNotNone(IM.read_metadata(self.env)[0])   # NOT removed


class StartPaths(TempHome):
    def test_27_partial_metadata_does_not_break_startup(self):
        with open(IM.AP.instance_metadata_path(self.env), "w", encoding="utf-8") as f:
            f.write('{"pid": 5,')            # corrupt residue present before start
        spawned = {}

        def fake_spawn(policy, iid, env):
            spawned["iid"] = iid
            return (770001, "pythonw.exe", "cmd")

        with mock.patch.object(IM, "port_listening", lambda h, p, timeout=0.5: False), \
             mock.patch.object(IM, "probe_pid_start_time", lambda pid: "T"), \
             mock.patch.object(IM, "wait_for_health",
                               lambda url, t, **k: (True, ours_health(770001, spawned["iid"]))):
            res = IM.start_instance(env=self.env, policy=self.policy, spawn=fake_spawn,
                                    startup_timeout=2.0)
        self.assertEqual(res["status"], IM.STARTED)

    def test_30_31_startup_timeout_cleans_spawned_process(self):
        killed = []

        def fake_spawn(policy, iid, env):
            return (770777, "pythonw.exe", "cmd")

        with mock.patch.object(IM, "port_listening", lambda h, p, timeout=0.5: False), \
             mock.patch.object(IM, "http_get_json", lambda u, timeout=2.0: None), \
             mock.patch.object(IM, "probe_pid_start_time", lambda pid: "T"), \
             mock.patch.object(IM, "wait_for_health", lambda url, t, **k: (False, None)), \
             mock.patch.object(IM, "_terminate_tree",
                               lambda pid, **k: killed.append(pid) or {"terminated": True}):
            res = IM.start_instance(env=self.env, policy=self.policy, spawn=fake_spawn,
                                    startup_timeout=1.0)
        self.assertEqual(res["status"], IM.START_FAILED)   # failed health prevents success
        self.assertIn(770777, killed)                      # spawned process cleaned up
        self.assertIsNone(IM.read_metadata(self.env)[0])   # metadata cleared

    def test_35_restart_does_verified_stop_then_start(self):
        calls = []
        with mock.patch.object(IM, "stop_instance",
                               lambda **k: calls.append("stop") or
                               {"status": IM.NOT_RUNNING, "ok": True}), \
             mock.patch.object(IM, "start_instance",
                               lambda **k: calls.append("start") or
                               {"status": IM.STARTED, "ok": True, "message": ""}):
            res = IM.restart_instance(env=self.env, policy=self.policy)
        self.assertEqual(calls, ["stop", "start"])
        self.assertEqual(res["status"], IM.STARTED)
        self.assertIn("stop_phase", res)

    def test_35b_restart_aborts_on_unknown_process(self):
        with mock.patch.object(IM, "stop_instance",
                               lambda **k: {"status": IM.PORT_IN_USE_BY_UNKNOWN_PROCESS,
                                            "ok": False}), \
             mock.patch.object(IM, "start_instance",
                               lambda **k: self.fail("must not start over unknown process")):
            res = IM.restart_instance(env=self.env, policy=self.policy)
        self.assertEqual(res["status"], IM.RESTART_ABORTED)


class LiveLifecycle(unittest.TestCase):
    """Bounded real start/stop on an ephemeral port under a temp home."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="amzlive_")
        self.env = {"AMZ_FBM_HOME": self.home}
        self.port = free_port()
        self.policy = make_policy(self.port)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        try:
            IM.stop_instance(env=self.env, policy=self.policy, timeout=10)
        except Exception:
            pass
        import shutil
        shutil.rmtree(self.home, ignore_errors=True)

    def test_16_17_23_32_33_34_full_lifecycle(self):
        r = IM.start_instance(env=self.env, policy=self.policy, startup_timeout=30.0)
        self.assertEqual(r["status"], IM.STARTED, r.get("message"))       # 16
        inst = r["instance"]
        self.assertTrue(inst["identity_verified"])                        # 23
        self.assertNotEqual(inst["pid"], os.getpid())                     # 32 detached
        # second start does not create a duplicate
        r2 = IM.start_instance(env=self.env, policy=self.policy, startup_timeout=8.0)
        self.assertEqual(r2["status"], IM.ALREADY_RUNNING)                # 17
        st = IM.get_instance_status(env=self.env, policy=self.policy)
        self.assertEqual(st["status"], IM.RUNNING_HEALTHY)                # 23
        self.assertEqual(st["pid"], inst["pid"])
        # stop the verified instance, then confirm idempotency
        s = IM.stop_instance(env=self.env, policy=self.policy, timeout=15)
        self.assertEqual(s["status"], IM.STOPPED_OK)                      # 33
        self.assertIsNone(IM.read_metadata(self.env)[0])                  # 36 (removed)
        s2 = IM.stop_instance(env=self.env, policy=self.policy, timeout=5)
        self.assertEqual(s2["status"], IM.NOT_RUNNING)                    # 34 idempotent

    def test_35_live_restart(self):
        r = IM.restart_instance(env=self.env, policy=self.policy, startup_timeout=30.0)
        self.assertEqual(r["status"], IM.STARTED, r.get("message"))
        self.assertIn("stop_phase", r)
        st = IM.get_instance_status(env=self.env, policy=self.policy)
        self.assertEqual(st["status"], IM.RUNNING_HEALTHY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
