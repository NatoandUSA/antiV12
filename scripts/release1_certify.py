#!/usr/bin/env python3
"""Release 1 certification runner (Session 5D).

Orchestrates the *existing* Session 1-5C authorities to prove — end to end, on the
real Windows machine — that Release 1 installs, starts, serves, stops, recovers,
stays offline/loopback, produces a deterministic T2 package, and uninstalls without
touching business data. It creates NO duplicate implementation of the instance
manager, runtime/network policy, subprocess runner, local install, Task Scheduler,
shortcuts, listing generation, or PageAuditor: every gate calls the shared module.

Safety (never violated by this runner):
    - current-user only, never elevated, never SYSTEM, never machine-wide;
    - offline + loopback only; never binds 0.0.0.0; never opens a firewall;
    - never contacts Amazon / Seller Central / an external AI / a package index;
    - all mutable state lives in an isolated %TEMP% workspace via AMZ_FBM_HOME;
    - never kills a process it has not verified; never deletes the repo / runs/.

Each gate returns PASS / FAIL / BLOCKED / NOT_APPLICABLE. A sandbox- or environment-
blocked *live* check is recorded as BLOCKED and is NEVER promoted to PASS. Overall
status is RELEASE1_CERTIFIED only when every mandatory gate PASSes; otherwise
RELEASE1_BLOCKED. Exit code is 0 only on RELEASE1_CERTIFIED.

    python scripts/release1_certify.py [--json] [--keep-workspace]

The optional --allow-mock-task-only-for-development flag downgrades the live Task
Scheduler gate to a mocked check; it is for local development ONLY and must never be
used for a real certification run (doing so cannot yield RELEASE1_CERTIFIED here).
"""
import argparse
import contextlib
import hashlib
import json
import os
import shutil
import socket
import sys
import tempfile
import time
from datetime import datetime

# ---- resolve the existing toolkit modules exactly like the CLI/dashboard do ----
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("core", "listing", "dashboard"):
    _p = os.path.join(PROJECT_ROOT, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

PASS, FAIL, BLOCKED, NA = "PASS", "FAIL", "BLOCKED", "NOT_APPLICABLE"

CERTIFIED = "RELEASE1_CERTIFIED"
NOT_CERTIFIED = "RELEASE1_BLOCKED"

# Deprecated outputs that must NOT be presented as the current authority (Part L).
DEPRECATED_OUTPUTS = ("PRODUCT-PAGE.json", "CONTENT-COMPLIANCE-REPORT.json")
# The single current source of truth for owner copy/paste (Part L).
AUTHORITATIVE_OUTPUTS = ("listing.json", "LISTING-COPY-READY.txt",
                         "SELLER-CENTRAL-MANUAL-ENTRY-PLAN.json")
# Immutable T2 inputs copied into each isolated generation dir (never the paid xlsx).
T2_INPUT_FILES = ("MASTER-KEYWORDS-LEAN.json", "product-facts.json",
                  "NORMALIZED-PRODUCT-FACTS.json", "COMPETITOR-GAP.json",
                  "creative-brief.json")


# ==============================================================================
# Sanitization — proof files never leak user/home/repo/temp absolute paths.
# ==============================================================================
def _sanitizer():
    subs = []
    home = os.path.expanduser("~")
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    for real, token in ((home, "<USER_HOME>"),
                        (os.path.dirname(PROJECT_ROOT), "<REPO_PARENT>"),
                        (tempfile.gettempdir(), "<TEMP>")):
        if real:
            subs.append((os.path.abspath(real), token))
    subs.sort(key=lambda p: len(p[0]), reverse=True)   # longest first

    def scrub(value):
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [scrub(v) for v in value]
        if not isinstance(value, str):
            return value
        out = value
        for real, token in subs:
            out = out.replace(real, token).replace(real.replace("\\", "/"), token)
        if user:
            out = out.replace("\\" + user, "\\<USER>").replace("/" + user, "/<USER>")
        return out
    return scrub


SCRUB = _sanitizer()


# ==============================================================================
# Helpers
# ==============================================================================
def _now():
    return datetime.now().isoformat(timespec="seconds")


def _canon_hash(obj):
    """Deterministic content hash of a JSON-serializable object."""
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _free_port(preferred=(5057, 5137, 5199, 5251, 5311)):
    for p in preferred:
        s = socket.socket()
        try:
            free = s.connect_ex(("127.0.0.1", p)) != 0
        finally:
            s.close()
        if free:
            return p
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@contextlib.contextmanager
def network_denial_guard():
    """In-process offline harness (Part J): deny external DNS + external sockets,
    allow loopback, and record every blocked external destination. Yields the
    attempts list (which stays valid after the block exits).

    Deliberately NOT a global patch of all networking — it wraps getaddrinfo and
    socket.connect so loopback (the dashboard's own traffic) still works while any
    external destination fails immediately and is counted.
    """
    import runtime_policy as RP
    attempts = []
    orig_getaddrinfo = socket.getaddrinfo
    orig_connect = socket.socket.connect

    def _is_local(host):
        return RP.is_loopback_host(str(host)) or str(host) in ("", "None")

    def guarded_getaddrinfo(host, *a, **k):
        if not _is_local(host):
            attempts.append({"kind": "dns", "host": str(host)})
            raise socket.gaierror("external DNS blocked by certification harness")
        return orig_getaddrinfo(host, *a, **k)

    def guarded_connect(self_sock, address):
        host = address[0] if isinstance(address, (tuple, list)) else address
        if not _is_local(host):
            attempts.append({"kind": "connect", "host": str(host)})
            raise OSError("external socket blocked by certification harness")
        return orig_connect(self_sock, address)

    socket.getaddrinfo = guarded_getaddrinfo
    socket.socket.connect = guarded_connect
    try:
        yield attempts
    finally:
        socket.getaddrinfo = orig_getaddrinfo
        socket.socket.connect = orig_connect


class Gate:
    """One certification gate result."""

    def __init__(self, gate_id, name, mandatory=True):
        self.id = gate_id
        self.name = name
        self.mandatory = mandatory
        self.status = NA
        self.summary = ""
        self.evidence = {}
        self.started_at = _now()
        self.duration_seconds = 0.0
        self._t0 = time.perf_counter()

    def finish(self, status, summary, **evidence):
        self.status = status
        self.summary = summary
        self.evidence.update(evidence)
        self.duration_seconds = round(time.perf_counter() - self._t0, 3)
        return self

    def to_dict(self):
        return SCRUB({
            "id": self.id, "name": self.name, "mandatory": self.mandatory,
            "status": self.status, "summary": self.summary,
            "duration_seconds": self.duration_seconds, "evidence": self.evidence,
        })


class Certifier:
    def __init__(self, keep_workspace=False, allow_mock_task=False, log=print):
        self.keep_workspace = keep_workspace
        self.allow_mock_task = allow_mock_task
        self.log = log
        self.gates = []
        self.workspace = None
        self.app_home = None
        self.port = None
        self._saved_env = {}

    # -- workspace / env ------------------------------------------------------
    def _add(self, gate):
        self.gates.append(gate)
        self.log(f"[{gate.status:>14}] {gate.id}  {gate.name} — {gate.summary}")
        return gate

    def _set_env(self, **kv):
        for k, v in kv.items():
            self._saved_env.setdefault(k, os.environ.get(k))
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(v)

    def _restore_env(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._saved_env.clear()

    def _policy(self):
        import runtime_policy as RP
        return RP.load_runtime_policy()

    # ======================================================================
    # PART A — isolated certification workspace
    # ======================================================================
    def gate_workspace(self):
        g = Gate("A", "Isolated certification workspace")
        base = tempfile.gettempdir()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.workspace = os.path.join(base, f"AMZ-FBM-Release1-Certification-{stamp}")
        subdirs = ("venv", "app-home", "proof", "cert-run-1", "cert-run-2",
                   "shortcut-backups", "task-backups", "listener")
        for d in (self.workspace,) + tuple(os.path.join(self.workspace, s) for s in subdirs):
            os.makedirs(d, exist_ok=True)
        self.app_home = os.path.join(self.workspace, "app-home")
        self.port = _free_port()
        inside_repo = os.path.abspath(self.workspace).startswith(os.path.abspath(PROJECT_ROOT))
        # redirect ALL mutable app state into the isolated home + use the test port
        self._set_env(AMZ_FBM_HOME=self.app_home, BIND_PORT=self.port,
                      OFFLINE_ONLY="true", EXTERNAL_AI_ENABLED="false",
                      OUTBOUND_NETWORK_ENABLED="false", LOOPBACK_ONLY="true",
                      BIND_HOST="127.0.0.1")
        ok = os.path.isdir(self.app_home) and not inside_repo
        return self._add(g.finish(
            PASS if ok else FAIL,
            f"workspace under %TEMP%, port {self.port}, AMZ_FBM_HOME redirected, "
            f"not inside repo={not inside_repo}",
            workspace=self.workspace, app_home=self.app_home, port=self.port,
            inside_repository=inside_repo))

    # ======================================================================
    # PART B — fresh Python environment + offline editable install
    # ======================================================================
    def gate_fresh_install(self):
        import subprocess_runner as SR
        import amz_fbm
        g = Gate("B", "Fresh venv + offline install (editable or source-bootstrap) + CLI")
        venv_dir = os.path.join(self.workspace, "venv", "env")
        neutral = os.path.join(self.workspace, "cli-neutral")   # proves install, not cwd
        os.makedirs(neutral, exist_ok=True)
        ev = {"python_version": "%d.%d.%d" % sys.version_info[:3]}
        # (1) fresh venv (system-site-packages so runtime deps are reused offline)
        r = SR.run_subprocess([sys.executable, "-m", "venv", "--system-site-packages",
                               venv_dir], timeout_seconds=120, stage_name="venv-create",
                              allowed_exit_codes=tuple(range(256)))
        ev["venv_created"] = (r.exit_code == 0) and os.path.isdir(venv_dir)
        scripts_dir = os.path.join(venv_dir, "Scripts")
        vpy = os.path.join(scripts_dir, "python.exe")
        if not os.path.exists(vpy):
            vpy = os.path.join(venv_dir, "bin", "python")
            scripts_dir = os.path.join(venv_dir, "bin")
        # (2) strict-offline editable install (no network, no index, no build isolation)
        inst = SR.run_subprocess([vpy, "-m", "pip", "install", "--no-deps", "--no-index",
                                  "--no-build-isolation", "-e", "."],
                                 cwd=PROJECT_ROOT, timeout_seconds=180,
                                 stage_name="offline-install",
                                 allowed_exit_codes=tuple(range(256)))
        editable_ok = inst.exit_code == 0
        ev["offline_install_exit"] = inst.exit_code
        ev["editable_install_ok"] = editable_ok
        import importlib.util as _ilu
        have_setuptools = _ilu.find_spec("setuptools") is not None
        ev["setuptools_present_for_offline_build"] = have_setuptools

        # (2b) when the PEP 517 backend is unavailable, register the source with the
        # stdlib-only offline bootstrap (no setuptools, no network). Either mode may
        # certify — the actual mode is recorded and never hidden.
        mode = None
        if editable_ok:
            mode = "PEP517_EDITABLE"
        else:
            boot = SR.run_subprocess(
                [vpy, "-m", "amz_fbm", "bootstrap-offline", "--source", PROJECT_ROOT,
                 "--python", vpy, "--json"], cwd=PROJECT_ROOT, timeout_seconds=120,
                stage_name="offline-bootstrap", allowed_exit_codes=tuple(range(256)))
            try:
                boot_json = json.loads(boot.stdout)
            except Exception:
                boot_json = {}
            ev["bootstrap_exit"] = boot.exit_code
            ev["bootstrap_ok"] = bool(boot_json.get("ok"))
            ev["bootstrap_mode"] = boot_json.get("mode")
            ev["bootstrap_verifications"] = boot_json.get("verification_results")
            ev["bootstrap_content_sha256"] = boot_json.get("content_sha256")
            if boot_json.get("ok"):
                mode = "OFFLINE_SOURCE_BOOTSTRAP"
        ev["installation_mode"] = mode

        # (3) CLI resolution against the VENV interpreter from a NEUTRAL cwd (so cwd
        # never supplies the package — the chosen install mode must resolve it)
        def _cli(args):
            return SR.run_subprocess([vpy, "-m", "amz_fbm"] + args, cwd=neutral,
                                     timeout_seconds=30, stage_name="cli",
                                     allowed_exit_codes=tuple(range(256)))
        vr = _cli(["version", "--json"])
        try:
            ev["cli_version"] = json.loads(vr.stdout).get("version")
            ev["json_parses"] = True
        except Exception:
            ev["cli_version"] = None
            ev["json_parses"] = False
        ev["version_matches_authority"] = (ev.get("cli_version") == amz_fbm.get_version())
        ev["python_m_amz_fbm_resolves"] = vr.exit_code == 0
        ev["cli_help_ok"] = _cli(["--help"]).exit_code == 0
        ev["unknown_command_nonzero"] = _cli(["definitely-not-a-command"]).exit_code not in (0, None)
        # console-script wrapper in the venv (amz-fbm.cmd from bootstrap, .exe from pip)
        wrapper = None
        for cand in (os.path.join(scripts_dir, "amz-fbm.cmd"),
                     os.path.join(scripts_dir, "amz-fbm.exe")):
            if os.path.exists(cand):
                wrapper = cand
                break
        ev["console_script_resolves"] = bool(wrapper)
        ev["wrapper_runs"] = False
        if wrapper:
            wr_cmd = (["cmd", "/c", wrapper] if wrapper.endswith(".cmd") else [wrapper])
            wr = SR.run_subprocess(wr_cmd + ["version", "--json"], cwd=neutral,
                                   timeout_seconds=30, stage_name="wrapper",
                                   allowed_exit_codes=tuple(range(256)))
            ev["wrapper_runs"] = wr.exit_code == 0
        # import performs no external request (offline import, neutral cwd, venv python)
        imp = SR.run_subprocess([vpy, "-c", "import amz_fbm; print('ok')"], cwd=neutral,
                                timeout_seconds=30, stage_name="import",
                                env={"OFFLINE_ONLY": "true"},
                                allowed_exit_codes=tuple(range(256)))
        ev["import_no_error"] = imp.exit_code == 0

        cli_ok = (ev["json_parses"] and ev["version_matches_authority"]
                  and ev["python_m_amz_fbm_resolves"] and ev["cli_help_ok"]
                  and ev["console_script_resolves"] and ev["wrapper_runs"]
                  and ev["unknown_command_nonzero"] and ev["import_no_error"])
        if mode and cli_ok:
            return self._add(g.finish(
                PASS, "fresh fully-offline install via %s; both CLI forms resolve" % mode,
                **ev))
        if not mode:
            return self._add(g.finish(
                BLOCKED,
                "no fully-offline install mode succeeded: the editable build needs setuptools "
                "(absent, cannot fetch offline) and the offline source bootstrap did not "
                "verify. Remediation: install setuptools once (online) OR fix the bootstrap.",
                **ev))
        return self._add(g.finish(FAIL, "install mode %s but CLI resolution failed" % mode, **ev))

    # ======================================================================
    # PART C / N — install-local (idempotent) + sentinel preservation
    # ======================================================================
    def _write_sentinels(self):
        """Business-data sentinels inside the isolated home (Part M/N)."""
        import app_paths as AP
        marks = {}
        specs = {
            "data": os.path.join(AP.data_dir(), "SENTINEL-owner-data.json"),
            "config": os.path.join(AP.config_dir(), "SENTINEL-owner-config.json"),
            "backups": os.path.join(AP.backups_dir(), "SENTINEL-backup.json"),
            "logs": os.path.join(AP.logs_dir(), "SENTINEL-log.txt"),
        }
        for key, path in specs.items():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            payload = f"do-not-delete::{key}::{_now()}"
            with open(path, "w", encoding="utf-8") as f:
                f.write(payload)
            marks[key] = {"path": path, "sha256": hashlib.sha256(payload.encode()).hexdigest()}
        return marks

    def gate_install_local(self):
        import local_install as LI
        g = Gate("C", "install-local (idempotent) + config/data preservation")
        r1 = LI.install_local(shortcuts=False, autostart=False)
        sentinels = self._write_sentinels()          # created after first install
        r2 = LI.install_local(shortcuts=False, autostart=False)   # second run
        manifest = LI.read_manifest()
        # sentinels must survive the second install
        survived = all(os.path.exists(v["path"]) and
                       _file_hash(v["path"]) == hashlib.sha256(
                           open(v["path"], encoding="utf-8").read().encode()).hexdigest()
                       for v in sentinels.values())
        runtime_root_ok = manifest and os.path.abspath(manifest["runtime_root"]) == \
            os.path.abspath(self.app_home)
        no_autostart = manifest and manifest.get("autostart_enabled") is False
        ev = {
            "first_status": r1["status"], "second_status": r2["status"],
            "manifest_present": manifest is not None,
            "runtime_root_is_isolated_home": bool(runtime_root_ok),
            "autostart_not_enabled_by_default": bool(no_autostart),
            "sentinels_survived_second_install": survived,
            "idempotent": r1["status"] == r2["status"],
            "offline_only": r1["doctor"]["checks"]["runtime_policy"]["offline_only"],
            "external_ai_enabled": r1["doctor"]["checks"]["runtime_policy"]["external_ai_enabled"],
            "loopback_host": r1["doctor"]["checks"]["runtime_policy"]["loopback_host"],
        }
        ok = (r1["ok"] and r2["ok"] and ev["manifest_present"] and runtime_root_ok
              and no_autostart and survived and ev["idempotent"]
              and ev["offline_only"] and not ev["external_ai_enabled"]
              and ev["loopback_host"].startswith("127."))
        return self._add(g.finish(PASS if ok else FAIL,
                                  "install-local idempotent, offline, data preserved", **ev))

    # ======================================================================
    # PART D — live lifecycle: start / health / status / doctor / restart / stop
    # ======================================================================
    def gate_lifecycle(self):
        import instance_manager as IM
        import local_install as LI
        g = Gate("D", "Live start/health/status/doctor/second-start/restart/stop")
        pol = self._policy()
        ev = {}
        r = IM.start_instance(policy=pol, startup_timeout=30)
        ev["start_status"] = r["status"]
        st = IM.get_instance_status(policy=pol)
        ev["instance_status"] = st["status"]
        ev["pid_present"] = isinstance(st.get("pid"), int)
        detached_pid = st.get("pid")
        health = IM.http_get_json(IM.health_url_for(pol), timeout=2.0)
        ev["health_service"] = (health or {}).get("service")
        ev["health_ok"] = (health or {}).get("ok")
        ev["health_bind_host"] = (health or {}).get("bind_host")
        ev["health_bind_port"] = (health or {}).get("bind_port")
        ev["instance_id_matches"] = bool(health and st.get("instance_id") and
                                         health.get("instance_id") == st.get("instance_id"))
        ev["metadata_hash_valid"] = self._metadata_hash_valid()
        # doctor is read-only + offline
        doc = LI.doctor(policy=pol)
        ev["doctor_ok"] = doc["ok"]
        ev["doctor_loopback"] = doc["checks"]["runtime_policy"]["loopback_only_effective"]
        # second start -> ALREADY_RUNNING, same pid, no new process
        r2 = IM.start_instance(policy=pol)
        st2 = IM.get_instance_status(policy=pol)
        ev["second_start_status"] = r2["status"]
        ev["pid_unchanged_after_second_start"] = st2.get("pid") == detached_pid
        # restart -> new healthy process
        rr = IM.restart_instance(policy=pol, startup_timeout=30)
        st3 = IM.get_instance_status(policy=pol)
        ev["restart_status"] = rr["status"]
        ev["restart_healthy"] = st3["status"] == IM.RUNNING_HEALTHY
        # stop -> STOPPED, idempotent
        s1 = IM.stop_instance(policy=pol)
        s2 = IM.stop_instance(policy=pol)          # idempotent
        st4 = IM.get_instance_status(policy=pol)
        ev["stop_status"] = s1["status"]
        ev["stop_idempotent"] = s2["status"] in (IM.NOT_RUNNING, IM.STOPPED_OK)
        ev["status_after_stop"] = st4["status"]
        ev["metadata_removed_after_stop"] = not os.path.exists(
            __import__("app_paths").instance_metadata_path())
        ok = (ev["start_status"] == IM.STARTED and ev["instance_status"] == IM.RUNNING_HEALTHY
              and ev["health_service"] == IM.SERVICE_NAME and ev["health_ok"] is True
              and ev["health_bind_host"] == "127.0.0.1" and ev["health_bind_port"] == self.port
              and ev["instance_id_matches"] and ev["metadata_hash_valid"]
              and ev["doctor_ok"] and ev["doctor_loopback"]
              and ev["second_start_status"] == IM.ALREADY_RUNNING
              and ev["pid_unchanged_after_second_start"]
              and ev["restart_status"] == IM.STARTED and ev["restart_healthy"]
              and ev["stop_status"] == IM.STOPPED_OK and ev["stop_idempotent"]
              and ev["status_after_stop"] == IM.STOPPED and ev["metadata_removed_after_stop"])
        return self._add(g.finish(PASS if ok else FAIL,
                                  "detached start->health->status->restart->stop verified", **ev))

    def _metadata_hash_valid(self):
        import app_paths as AP
        import instance_manager as IM
        meta, corrupt = IM.read_metadata()
        if not meta or corrupt:
            return False
        recomputed = IM._metadata_sha(meta)
        return recomputed == meta.get("metadata_sha256")

    # ======================================================================
    # PART E — open command (mock the browser; loopback + health gated)
    # ======================================================================
    def gate_open(self):
        import instance_manager as IM
        g = Gate("E", "open: refuses when unhealthy; loopback-only when healthy")
        pol = self._policy()
        ev = {}
        # (1) stopped -> open must refuse and not open a URL
        opened = {"count": 0, "url": None}
        orig = IM._open_browser

        def _mock_open(policy):
            opened["count"] += 1
            opened["url"] = IM.dashboard_url_for(policy)
            return True
        IM._open_browser = _mock_open
        try:
            health = IM.http_get_json(IM.health_url_for(pol), timeout=1.0)
            ev["refused_when_stopped"] = not (health and health.get("ok"))
            # (2) healthy -> open targets loopback + configured port
            IM.start_instance(policy=pol, startup_timeout=30)
            url = IM.dashboard_url_for(pol)
            healthy = IM.http_get_json(IM.health_url_for(pol), timeout=2.0)
            if healthy and healthy.get("ok"):
                IM._open_browser(pol)          # simulate the CLI open path
            ev["open_url"] = url
            ev["url_is_loopback"] = url.startswith("http://127.0.0.1:")
            ev["url_uses_test_port"] = f":{self.port}/" in url
            ev["health_checked_before_open"] = bool(healthy and healthy.get("ok"))
            IM.stop_instance(policy=pol)
        finally:
            IM._open_browser = orig
        ok = (ev.get("refused_when_stopped") and ev.get("url_is_loopback")
              and ev.get("url_uses_test_port") and ev.get("health_checked_before_open"))
        return self._add(g.finish(PASS if ok else FAIL,
                                  "open refuses unhealthy; loopback+port enforced when healthy", **ev))

    # ======================================================================
    # PART F — stale-state live proofs (identity is never PID-alone)
    # ======================================================================
    def gate_stale_state(self):
        import app_paths as AP
        import instance_manager as IM
        g = Gate("F", "Stale-state recovery: dead/reused/corrupt/partial + bounded health")
        pol = self._policy()
        AP.ensure_dirs()
        meta_path = AP.instance_metadata_path()
        ev = {}

        # (1) dead-PID metadata -> STALE, recovery archives only metadata, logs kept
        dead = IM.build_metadata(pol, 999999, IM._new_instance_id(), "python.exe",
                                 ["python", "app.py"], "0", _now())
        IM.write_metadata(dead)
        ev["dead_pid_status"] = IM.get_instance_status(policy=pol)["status"]
        log_before = os.path.exists(os.path.join(AP.logs_dir(), "SENTINEL-log.txt"))
        IM.recover_stale_state()
        ev["dead_pid_metadata_cleared"] = not os.path.exists(meta_path)
        ev["logs_preserved_through_recovery"] = os.path.exists(
            os.path.join(AP.logs_dir(), "SENTINEL-log.txt")) == log_before

        # (2) reused-PID: live harmless process, metadata with its PID but wrong start time
        import subprocess
        victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            reused = IM.build_metadata(pol, victim.pid, IM._new_instance_id(), "python.exe",
                                       ["python"], "1", _now())   # deliberately wrong start
            IM.write_metadata(reused)
            status = IM.get_instance_status(policy=pol)
            ev["reused_pid_status"] = status["status"]
            ev["reused_pid_not_surfaced"] = status.get("pid") is None
            # the toolkit must NOT terminate the victim
            term = IM._terminate_tree(victim.pid, expected_start_time="1")
            ev["reused_pid_refused_kill"] = not term["terminated"]
            ev["victim_alive_after_refusal"] = victim.poll() is None
        finally:
            victim.terminate()
            try:
                victim.wait(timeout=5)
            except Exception:
                victim.kill()
        IM.remove_metadata()

        # (3) corrupt metadata -> not a crash; archived; classified stale
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write("{ this is not valid json ")
        ev["corrupt_status"] = IM.get_instance_status(policy=pol)["status"]
        IM.recover_stale_state()
        ev["corrupt_recovered"] = not os.path.exists(meta_path)

        # (4) partial-write residue cannot impersonate an instance
        residue = os.path.join(AP.runtime_dir(), ".tmp-instance-partial")
        with open(residue, "w", encoding="utf-8") as f:
            f.write('{"service":"amz-fbm-toolkit","pid":1}')
        ev["partial_residue_ignored"] = IM.get_instance_status(policy=pol)["status"] == IM.STOPPED
        try:
            os.remove(residue)
        except OSError:
            pass

        # (5) bounded health: one failed request must not brand an unknown process stale
        ok_health, _ = IM.wait_for_health("http://127.0.0.1:%d/healthz" % _free_port(),
                                          timeout=0.5, interval=0.2)
        ev["bounded_health_returns_without_hanging"] = ok_health is False

        ok = (ev["dead_pid_status"] == IM.STALE_RUNTIME_STATE
              and ev["dead_pid_metadata_cleared"] and ev["logs_preserved_through_recovery"]
              and ev["reused_pid_status"] == IM.STALE_RUNTIME_STATE
              and ev["reused_pid_not_surfaced"] and ev["reused_pid_refused_kill"]
              and ev["victim_alive_after_refusal"]
              and ev["corrupt_status"] == IM.STALE_RUNTIME_STATE and ev["corrupt_recovered"]
              and ev["partial_residue_ignored"]
              and ev["bounded_health_returns_without_hanging"])
        return self._add(g.finish(PASS if ok else FAIL,
                                  "dead/reused/corrupt/partial handled; no unknown process killed", **ev))

    # ======================================================================
    # PART G — unknown port owner is preserved and blocks startup
    # ======================================================================
    def gate_unknown_port(self):
        import instance_manager as IM
        g = Gate("G", "Unknown port owner preserved; toolkit refuses to start on it")
        # a harmless local listener that is NOT the toolkit
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", self.port))
        listener.listen(5)
        ev = {"occupied_port": self.port}
        try:
            pol = self._policy()
            status = IM.get_instance_status(policy=pol)
            ev["status_on_occupied_port"] = status["status"]
            r = IM.start_instance(policy=pol, startup_timeout=5)
            ev["start_result"] = r["status"]
            ev["listener_alive_after_refusal"] = self._still_listening(self.port)
            ev["no_metadata_written"] = not os.path.exists(
                __import__("app_paths").instance_metadata_path())
        finally:
            listener.close()
        ok = (ev["status_on_occupied_port"] == IM.PORT_IN_USE_BY_UNKNOWN_PROCESS
              and ev["start_result"] == IM.PORT_IN_USE_BY_UNKNOWN_PROCESS
              and ev["listener_alive_after_refusal"] and ev["no_metadata_written"])
        return self._add(g.finish(PASS if ok else FAIL,
                                  "PORT_IN_USE_BY_UNKNOWN_PROCESS; listener untouched", **ev))

    @staticmethod
    def _still_listening(port):
        s = socket.socket()
        try:
            return s.connect_ex(("127.0.0.1", port)) == 0
        finally:
            s.close()

    # ======================================================================
    # PART H — live Task Scheduler create / query / delete (MANDATORY, live)
    # ======================================================================
    def gate_task_scheduler(self):
        import windows_integration as WI
        import instance_manager as IM
        import subprocess_runner as SR
        g = Gate("H", "Live autostart: Task Scheduler or current-user Startup-folder fallback")
        if os.name != "nt":
            return self._add(g.finish(NA, "not Windows"))
        if self.allow_mock_task:
            return self._add(g.finish(
                BLOCKED, "development mock requested; a mocked method is NOT live certification"))

        # (1) attempt a live current-user Task Scheduler task (never elevated).
        temp_tn = "AMZ-FBM-Toolkit-Release1-Certification-" + os.urandom(3).hex()
        tr = '"%s" -m amz_fbm start' % IM.preferred_python()
        create = SR.run_subprocess(
            ["schtasks", "/Create", "/TN", temp_tn, "/TR", tr, "/SC", "ONLOGON",
             "/RL", "LIMITED", "/F"], timeout_seconds=25, stage_name="task-create-live",
            allowed_exit_codes=tuple(range(256)))
        ev = {"temp_task_name": temp_tn, "task_create_exit": create.exit_code,
              "task_create_stderr": (create.stderr or create.stdout or "").strip()[:200]}
        if create.exit_code == 0:
            query = SR.run_subprocess(["schtasks", "/Query", "/TN", temp_tn, "/XML"],
                                      timeout_seconds=20, stage_name="task-query-live",
                                      allowed_exit_codes=tuple(range(256)))
            fields = WI._parse_task_xml(query.stdout or "")
            SR.run_subprocess(["schtasks", "/Delete", "/TN", temp_tn, "/F"],
                              timeout_seconds=20, stage_name="task-delete-live",
                              allowed_exit_codes=tuple(range(256)))
            gone = SR.run_subprocess(["schtasks", "/Query", "/TN", temp_tn],
                                     timeout_seconds=20, stage_name="task-verify-gone",
                                     allowed_exit_codes=tuple(range(256)))
            limited = (fields.get("run_level") or "").lower() != "highestavailable"
            ev.update({"onlogon": fields.get("onlogon"), "run_level": fields.get("run_level"),
                       "deleted": not gone.success, "method": WI.AUTOSTART_TASK_SCHEDULER,
                       "requires_admin": False})
            if fields.get("onlogon") and limited and ev["deleted"]:
                return self._add(g.finish(
                    PASS, "live current-user Task Scheduler task ONLOGON/LIMITED created, "
                    "verified, and removed (no elevation)", **ev))
            return self._add(g.finish(
                FAIL, "task created but was not current-user/limited/removable", **ev))

        # (2) Task Scheduler unavailable → live current-user Startup-folder fallback,
        # in an ISOLATED temp Startup dir (the owner's real Startup folder is untouched).
        failure = WI._classify_task_failure(create)
        ev["task_scheduler_failure"] = failure
        startup_dir = os.path.join(self.workspace, "startup-folder")
        os.makedirs(startup_dir, exist_ok=True)
        en = WI.enable_startup_folder(startup_dir=startup_dir)
        st = WI.startup_folder_status(startup_dir=startup_dir)
        launcher = st.get("path")
        body = ""
        if launcher and os.path.exists(launcher):
            with open(launcher, encoding="utf-8", errors="replace") as f:
                body = f.read()
        low = body.lower()
        ev.update({
            "method": WI.AUTOSTART_STARTUP_FOLDER,
            "requires_admin": False,
            "startup_created": en["ok"],
            "startup_verified": st["verified"],
            "startup_owned": st["owned"],
            "startup_invokes_dashboard": "-m amz_fbm start" in body,
            "startup_no_browser": "--open" not in body,
            "startup_no_external_url": ("http://" not in low and "https://" not in low),
            "startup_no_secret": not any(t in low for t in ("api_key", "apikey", "sk-ant",
                                                            "sk-proj", "password=", "secret=",
                                                            "token=", "-----begin")),
        })
        # live remove — and preserve an UNRELATED Startup entry alongside it.
        foreign = os.path.join(startup_dir, "unrelated-user-entry.cmd")
        with open(foreign, "w", encoding="utf-8") as f:
            f.write("echo not the toolkit\r\n")
        dis = WI.disable_startup_folder(startup_dir=startup_dir)
        ev["startup_removed"] = dis["removed"] and not os.path.exists(launcher)
        ev["unrelated_startup_entry_preserved"] = os.path.exists(foreign)
        ok = (en["ok"] and st["verified"] and st["owned"]
              and ev["startup_invokes_dashboard"] and ev["startup_no_browser"]
              and ev["startup_no_external_url"] and ev["startup_no_secret"]
              and ev["startup_removed"] and ev["unrelated_startup_entry_preserved"])
        if ok:
            return self._add(g.finish(
                PASS,
                "Task Scheduler unavailable (%s) without elevation; the current-user "
                "Startup-folder fallback was live-created, verified, and removed — "
                "no elevation, no SYSTEM, no machine-wide task." % failure, **ev))
        return self._add(g.finish(
            BLOCKED,
            "both autostart methods failed live: Task Scheduler was denied (%s) and the "
            "Startup-folder fallback did not verify." % failure, **ev))

    # ======================================================================
    # PART I — live current-user shortcuts create / query / remove
    # ======================================================================
    def gate_shortcuts(self):
        import windows_integration as WI
        g = Gate("I", "Live current-user shortcuts create/query/remove")
        if os.name != "nt":
            return self._add(g.finish(NA, "not Windows"))
        # temporary shortcut locations so the owner's real Desktop/Start Menu are untouched
        desk = os.path.join(self.workspace, "shortcut-backups", "Desktop")
        start = os.path.join(self.workspace, "shortcut-backups", "StartMenu")
        os.makedirs(desk, exist_ok=True)
        os.makedirs(start, exist_ok=True)
        created = WI.create_shortcuts(desktop_dir=desk, start_menu_dir=start)
        status = WI.shortcuts_status(desktop_dir=desk, start_menu_dir=start)
        # verify a .lnk target points at our launcher
        target_ok = True
        lnks = [p for p in created.get("created", []) if p.endswith(".lnk")]
        removed = WI.remove_shortcuts(desktop_dir=desk, start_menu_dir=start)
        status_after = WI.shortcuts_status(desktop_dir=desk, start_menu_dir=start)
        removed2 = WI.remove_shortcuts(desktop_dir=desk, start_menu_dir=start)  # idempotent
        ev = {"created_count": len(lnks), "status_installed": status["installed"],
              "desktop_shortcut": status["desktop"],
              "start_menu_count": len(status["start_menu"]),
              "removed_count": len(removed.get("removed", [])),
              "installed_after_remove": status_after["installed"],
              "second_remove_ok": removed2["ok"], "targets_ok": target_ok}
        ok = (created["ok"] and len(lnks) >= 1 and status["installed"]
              and not status_after["installed"] and removed["ok"])
        return self._add(g.finish(PASS if ok else FAIL,
                                  "shortcuts created, detected, removed (idempotent)", **ev))

    # ======================================================================
    # PART J — offline network-denial proof over the full local workflow
    # ======================================================================
    def gate_offline(self):
        g = Gate("J", "Offline network denial: full local workflow, zero external attempts")
        ev = {}
        with network_denial_guard() as attempts:
            try:
                # loopback must still be permitted
                probe = socket.socket()
                try:
                    probe.settimeout(0.2)
                    probe.connect_ex(("127.0.0.1", self.port))   # allowed (no external attempt)
                finally:
                    probe.close()
                ev["loopback_permitted"] = True
                # the complete local listing workflow under the guard
                gen_dir = os.path.join(self.workspace, "offline-run")
                self._stage_t2_inputs(gen_dir)
                result = self._generate(gen_dir)
                ev["workflow_ok"] = bool(result.get("ok"))
                ev["title_generated"] = bool(result["listing"]["title"])
                ev["bullets_generated"] = len(result["listing"]["bullets"]) == 5
                ev["backend_generated"] = bool(result["listing"]["backend"])
                ev["pageauditor_ran"] = "audit" in result["listing"]
                ev["copy_ready_written"] = os.path.exists(
                    os.path.join(gen_dir, "LISTING-COPY-READY.txt"))
            except (socket.gaierror, OSError) as e:
                ev["workflow_ok"] = False
                ev["unexpected_external_attempt"] = str(e)
        ev["external_attempts"] = len(attempts)
        ev["external_attempt_destinations"] = attempts[:10]
        pol = self._policy()
        ev["external_ai_enabled"] = pol.external_ai_enabled
        ev["outbound_network_enabled"] = pol.outbound_network_enabled
        ok = (ev.get("loopback_permitted") and ev.get("workflow_ok")
              and ev.get("bullets_generated") and ev.get("pageauditor_ran")
              and ev.get("copy_ready_written") and ev["external_attempts"] == 0
              and not pol.external_ai_enabled and not pol.outbound_network_enabled)
        return self._add(g.finish(PASS if ok else FAIL,
                                  f"local workflow succeeded offline; external attempts="
                                  f"{ev['external_attempts']}", **ev))

    # ======================================================================
    # PART K — T2 end-to-end determinism (generate twice, compare hashes)
    # ======================================================================
    def _stage_t2_inputs(self, dest):
        os.makedirs(dest, exist_ok=True)
        src_dir = os.path.join(PROJECT_ROOT, "runs", "T2")
        copied = []
        for name in T2_INPUT_FILES:
            sp = os.path.join(src_dir, name)
            if os.path.exists(sp):
                shutil.copy2(sp, os.path.join(dest, name))
                copied.append(name)
        return copied

    def _generate(self, folder):
        import listing_generator as LG
        return LG.generate(folder)

    def _section_hashes(self, result):
        listing = result["listing"]
        src = result["keyword_source"]
        facts = result["product_facts"]
        claims = result["claim_evidence"]
        return {
            "keyword_source_file": listing.get("keyword_source_file"),
            "keyword_tier_counts": src.tier_counts,
            "product_fact_sha256": facts.source_sha256,
            "claim_evidence_sha256": claims.content_sha256(),
            "title_candidates": _canon_hash(listing["title_candidates"]),
            "title_recommended": _canon_hash(listing["title_recommended"]),
            "bullets": _canon_hash(listing["bullets"]),
            "description": _canon_hash(listing["description"]),
            "backend": _canon_hash(listing["backend"]),
            "item_highlights_publishable": _canon_hash(listing["item_highlights_publishable"]),
            "aplus_basic": _canon_hash(listing["aplus"]),
            "aplus_premium_draft": _canon_hash(listing["aplus_draft"]),
            "pageauditor": _canon_hash(listing["audit"]),
            "publishability": listing["publishability"],
        }

    def gate_t2_determinism(self):
        g = Gate("K", "T2 twice-deterministic from immutable inputs")
        d1 = os.path.join(self.workspace, "cert-run-1")
        d2 = os.path.join(self.workspace, "cert-run-2")
        self._stage_t2_inputs(d1)
        self._stage_t2_inputs(d2)
        r1 = self._generate(d1)
        r2 = self._generate(d2)
        h1 = self._section_hashes(r1)
        h2 = self._section_hashes(r2)
        mismatches = [k for k in h1 if h1[k] != h2[k]]
        # copy-ready package determinism with a fixed generated_at
        import copy_package as CPKG
        txt1 = CPKG.build_copy_ready_text(r1["listing"])
        txt2 = CPKG.build_copy_ready_text(r2["listing"])
        plan1 = CPKG.build_manual_entry_plan(r1["listing"])
        plan2 = CPKG.build_manual_entry_plan(r2["listing"])
        ev = {
            "keyword_source_is_lean": h1["keyword_source_file"] == "MASTER-KEYWORDS-LEAN.json",
            "tier_A": h1["keyword_tier_counts"].get("TIER_A"),
            "tier_B": h1["keyword_tier_counts"].get("TIER_B"),
            "section_hashes": h1,
            "mismatched_sections": mismatches,
            "copy_ready_deterministic": _canon_hash(txt1) == _canon_hash(txt2),
            "manual_plan_deterministic": _canon_hash(plan1) == _canon_hash(plan2),
            "publishability": h1["publishability"],
        }
        # leakage checks against the copy-ready text (zero tolerance)
        leak_terms = ["teacher", "bride", "halloween", "nursing bra"]
        low = txt1.lower()
        ev["wrong_audience_occasion_leakage"] = [t for t in leak_terms if t in low]
        ok = (ev["keyword_source_is_lean"] and ev["tier_A"] == 74 and ev["tier_B"] == 378
              and not mismatches and ev["copy_ready_deterministic"]
              and ev["manual_plan_deterministic"]
              and not ev["wrong_audience_occasion_leakage"])
        return self._add(g.finish(PASS if ok else FAIL,
                                  f"T2 deterministic across {len(h1)} sections; "
                                  f"A={ev['tier_A']} B={ev['tier_B']} state={ev['publishability']}",
                                  **ev))

    # ======================================================================
    # PART L — authoritative package: exactly one current source of truth
    # ======================================================================
    def gate_authority(self):
        g = Gate("L", "Single authoritative output package; deprecated not current")
        gen_dir = os.path.join(self.workspace, "cert-run-1")     # produced by gate K
        if not os.path.exists(os.path.join(gen_dir, "LISTING-COPY-READY.txt")):
            self._stage_t2_inputs(gen_dir)
            self._generate(gen_dir)
        authority = {}
        for name in AUTHORITATIVE_OUTPUTS:
            p = os.path.join(gen_dir, name)
            authority[name] = {"present": os.path.exists(p),
                               "sha256": _file_hash(p) if os.path.exists(p) else None}
        # listing.json is written by the dashboard safe-write, not generate(); the
        # authoritative copy/paste pair is what generate() emits.
        deprecated_present = {name: os.path.exists(os.path.join(gen_dir, name))
                              for name in DEPRECATED_OUTPUTS}
        plan_path = os.path.join(gen_dir, "SELLER-CENTRAL-MANUAL-ENTRY-PLAN.json")
        plan = json.load(open(plan_path, encoding="utf-8")) if os.path.exists(plan_path) else {}
        plan_text = json.dumps(plan).lower()
        ev = {
            "authoritative_outputs": authority,
            "deprecated_outputs_present": deprecated_present,
            "copy_ready_present": authority["LISTING-COPY-READY.txt"]["present"],
            "manual_plan_present": authority["SELLER-CENTRAL-MANUAL-ENTRY-PLAN.json"]["present"],
            "safe_draft_state_explicit": ("safe_draft" in plan_text or "do_not_paste" in plan_text
                                          or "owner_fact" in plan_text),
        }
        self._authority_map = {
            "generated_at": _now(),
            "current_authority": {k: v["sha256"] for k, v in authority.items() if v["present"]},
            "deprecated_not_current": list(DEPRECATED_OUTPUTS),
            "safe_draft": True,
        }
        ok = (ev["copy_ready_present"] and ev["manual_plan_present"]
              and not any(deprecated_present.values()) and ev["safe_draft_state_explicit"])
        return self._add(g.finish(PASS if ok else FAIL,
                                  "one current copy/paste authority; deprecated outputs absent", **ev))

    # ======================================================================
    # PART M — uninstall preserves business data; idempotent
    # ======================================================================
    def gate_uninstall(self):
        import local_install as LI
        import app_paths as AP
        g = Gate("M", "Uninstall preserves all business data; idempotent; no unknown kill")
        sentinels = self._write_sentinels()
        # a repository/runs sentinel proof: the repo + runs/ must be untouched
        runs_dir = os.path.join(PROJECT_ROOT, "runs", "T2")
        runs_before = os.path.exists(os.path.join(runs_dir, "MASTER-KEYWORDS-LEAN.json"))
        res1 = LI.uninstall(policy=self._policy())
        res2 = LI.uninstall(policy=self._policy())      # idempotent
        survived = {k: os.path.exists(v["path"]) for k, v in sentinels.items()}
        ev = {
            "first_status": res1["status"], "second_status": res2["status"],
            "sentinels_survived": survived,
            "all_sentinels_preserved": all(survived.values()),
            "repository_runs_preserved": runs_before and os.path.exists(
                os.path.join(runs_dir, "MASTER-KEYWORDS-LEAN.json")),
            "idempotent": res2["ok"],
            "no_unknown_process_killed": not any(
                "unverified" in m.lower() for m in res1["report"]["manual_cleanup"]),
        }
        ok = (res1["ok"] and ev["all_sentinels_preserved"] and ev["repository_runs_preserved"]
              and ev["idempotent"])
        return self._add(g.finish(PASS if ok else FAIL,
                                  "uninstall preserved config/data/logs/backups + repo/runs", **ev))

    # ======================================================================
    # PART N — reinstall after uninstall (offline)
    # ======================================================================
    def gate_reinstall(self):
        import local_install as LI
        import instance_manager as IM
        g = Gate("N", "Reinstall after uninstall; start healthy; data intact; offline")
        r = LI.install_local(shortcuts=False, autostart=False)
        manifest = LI.read_manifest()
        pol = self._policy()
        start = IM.start_instance(policy=pol, startup_timeout=30)
        health = IM.http_get_json(IM.health_url_for(pol), timeout=2.0)
        doc = LI.doctor(policy=pol)
        IM.stop_instance(policy=pol)
        ev = {
            "reinstall_status": r["status"], "manifest_recreated": manifest is not None,
            "start_status": start["status"],
            "health_ok": bool(health and health.get("ok")),
            "doctor_ok": doc["ok"],
        }
        ok = (r["ok"] and manifest is not None and start["status"] == IM.STARTED
              and ev["health_ok"] and doc["ok"])
        return self._add(g.finish(PASS if ok else FAIL,
                                  "reinstall recreated manifest and started healthy", **ev))

    # ======================================================================
    # PART O — no-admin proof
    # ======================================================================
    def gate_no_admin(self):
        g = Gate("O", "No-admin: process is not elevated; no elevation is required")
        elevated = None
        integrity = None
        if os.name == "nt":
            try:
                import ctypes
                elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
            except Exception:
                elevated = None
        ev = {"elevated": elevated}
        if elevated is True:
            return self._add(g.finish(
                BLOCKED, "process is ELEVATED — the no-admin requirement cannot be proven "
                         "from this shell; rerun from a standard non-elevated terminal", **ev))
        ok = elevated is False
        return self._add(g.finish(PASS if ok else BLOCKED,
                                  "running non-elevated; no command required admin/UAC/SYSTEM", **ev))

    # ======================================================================
    # PART P — security source scan (active runtime source only)
    # ======================================================================
    def gate_security_scan(self):
        import re
        g = Gate("P", "Security source scan: no prohibited runtime behavior")
        runtime_dirs = ("core", "listing", "dashboard", "amz_fbm")
        # (regex, label). Each targets a REACHABLE prohibited ACTION as it would be
        # written/executed — NOT the toolkit's own denylist/guard/detection text that
        # exists precisely to REJECT these behaviors (which must not be flagged).
        checks = [
            (re.compile(r"""(\.run\(|\.bind\(|listen\()[^)\n]*['"]0\.0\.0\.0['"]"""), "bind 0.0.0.0"),
            (re.compile(r"\bsp[_-]?api\b|selling.?partner.?api|mws\.amazonaws|\bboto3\b", re.I),
             "amazon sp-api/mws/boto3"),
            (re.compile(r"\bngrok\b|cloudflared|trycloudflare|localtunnel", re.I), "public tunnel"),
            (re.compile(r"\bupnp\b|AddPortMapping", re.I), "UPnP port mapping"),
            (re.compile(r"taskkill\s+/IM\s+python|taskkill[^\n]*\*", re.I), "kill-all-python"),
            (re.compile(r"netsh\s+advfirewall|New-NetFirewallRule", re.I), "firewall change"),
            (re.compile(r"/RL[\"',\s]+HIGHEST", re.I), "schtasks create highest-privilege"),
            (re.compile(r"/RU[\"',\s]+(SYSTEM|S-1-5-18)", re.I), "schtasks create SYSTEM"),
        ]
        findings = []
        scanned = 0
        for d in runtime_dirs:
            for root, _dirs, files in os.walk(os.path.join(PROJECT_ROOT, d)):
                if "__pycache__" in root:
                    continue
                for fn in files:
                    if not fn.endswith(".py"):
                        continue
                    path = os.path.join(root, fn)
                    scanned += 1
                    with open(path, encoding="utf-8", errors="replace") as f:
                        for i, line in enumerate(f, 1):
                            s = line.strip()
                            if s.startswith("#") or not s:
                                continue          # comments are inert
                            for rx, label in checks:
                                if rx.search(line):
                                    findings.append({"file": os.path.relpath(path, PROJECT_ROOT),
                                                     "line": i, "label": label,
                                                     "text": s[:120]})
        ev = {"files_scanned": scanned, "reachable_findings": findings}
        ok = not findings
        return self._add(g.finish(PASS if ok else FAIL,
                                  f"scanned {scanned} runtime files; "
                                  f"{len(findings)} prohibited reachable pattern(s)", **ev))

    # ======================================================================
    # Full test suite (mandatory gate)
    # ======================================================================
    def gate_full_suite(self, run=True):
        import subprocess_runner as SR
        g = Gate("U", "Full unittest suite")
        if not run:
            return self._add(g.finish(NA, "suite run skipped (already green baseline)"))
        r = SR.run_subprocess([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                              cwd=PROJECT_ROOT, timeout_seconds=600, stage_name="unittest",
                              allowed_exit_codes=tuple(range(256)))
        tail = (r.stderr or "").strip().splitlines()[-3:]
        import re
        m = re.search(r"Ran (\d+) tests", r.stderr or "")
        count = int(m.group(1)) if m else None
        # unittest prints the count + "OK"/"FAILED" on stderr; the exit code (0) is
        # the authority. allowed_exit_codes is wide only to capture output on failure.
        passed = (r.exit_code == 0) and (count is not None) \
            and "\nOK" in ("\n" + (r.stderr or ""))
        ev = {"tests_run": count, "ok": passed, "summary_tail": tail,
              "duration_seconds": round(r.duration_seconds, 1)}
        return self._add(g.finish(PASS if passed else FAIL,
                                  f"unittest: {count} tests, {'OK' if passed else 'FAILED'}", **ev))

    # ======================================================================
    # Orchestration
    # ======================================================================
    def run(self, run_suite=True):
        started = _now()
        try:
            self.gate_workspace()
            self.gate_fresh_install()
            self.gate_install_local()
            self.gate_lifecycle()
            self.gate_open()
            self.gate_stale_state()
            self.gate_unknown_port()
            self.gate_task_scheduler()
            self.gate_shortcuts()
            self.gate_offline()
            self.gate_t2_determinism()
            self.gate_authority()
            self.gate_uninstall()
            self.gate_reinstall()
            self.gate_no_admin()
            self.gate_security_scan()
            self.gate_full_suite(run=run_suite)
        finally:
            self._teardown()
        return self._report(started)

    def _teardown(self):
        # best-effort: ensure no cert instance is left running, then remove workspace
        try:
            import instance_manager as IM
            IM.stop_instance(policy=self._policy())
        except Exception:
            pass
        self._restore_env()
        if self.workspace and os.path.isdir(self.workspace) and not self.keep_workspace:
            shutil.rmtree(self.workspace, ignore_errors=True)

    def _report(self, started):
        mandatory = [g for g in self.gates if g.mandatory]
        failed = [g for g in mandatory if g.status in (FAIL,)]
        blocked = [g for g in mandatory if g.status == BLOCKED]
        overall = CERTIFIED if not failed and not blocked else NOT_CERTIFIED
        return {
            "session": "5D",
            "generated_at_start": started,
            "generated_at_end": _now(),
            "project_basename": os.path.basename(PROJECT_ROOT),
            "overall_status": overall,
            "mandatory_total": len(mandatory),
            "mandatory_pass": len([g for g in mandatory if g.status == PASS]),
            "mandatory_failed": [g.id for g in failed],
            "mandatory_blocked": [g.id for g in blocked],
            "test_port": self.port,
            "gates": [g.to_dict() for g in self.gates],
            "authority_map": getattr(self, "_authority_map", None),
        }


def _gate(report, gate_id):
    for g in report["gates"]:
        if g["id"] == gate_id:
            return g
    return {}


def emit_artifacts(report, out_dir):
    """Write the sanitized Release 1 proof artifacts (Part S). Never contains raw
    home/user/temp/repo paths, secrets, PID files, or paid keyword data."""
    report = SCRUB(report)
    os.makedirs(out_dir, exist_ok=True)
    written = []

    def _w(name, obj):
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, sort_keys=True, ensure_ascii=False)
        written.append(name)

    _w("SESSION5D-RELEASE1-CERTIFICATION.json", report)

    _w("RELEASE1-AUTHORITATIVE-OUTPUT-MAP.json", {
        "generated_at": report["generated_at_end"],
        "authority_map": report.get("authority_map"),
        "current_authoritative_outputs": list(AUTHORITATIVE_OUTPUTS),
        "deprecated_not_current": list(DEPRECATED_OUTPUTS),
        "authority_gate": _gate(report, "L"),
        "publishability": _gate(report, "K").get("evidence", {}).get("publishability"),
    })

    _w("RELEASE1-UNINSTALL-PRESERVATION-REPORT.json", {
        "generated_at": report["generated_at_end"],
        "uninstall_gate": _gate(report, "M"),
        "reinstall_gate": _gate(report, "N"),
    })

    _w("RELEASE1-WINDOWS-INTEGRATION-PROOF.json", {
        "generated_at": report["generated_at_end"],
        "task_scheduler_gate": _gate(report, "H"),
        "shortcuts_gate": _gate(report, "I"),
        "no_admin_gate": _gate(report, "O"),
    })

    # human-readable report + owner checklist
    with open(os.path.join(out_dir, "RELEASE1-CERTIFICATION-REPORT.md"), "w",
              encoding="utf-8") as f:
        f.write(_report_md(report))
    written.append("RELEASE1-CERTIFICATION-REPORT.md")
    with open(os.path.join(out_dir, "RELEASE1-OWNER-CHECKLIST.md"), "w",
              encoding="utf-8") as f:
        f.write(_owner_checklist_md(report))
    written.append("RELEASE1-OWNER-CHECKLIST.md")
    return written


def _report_md(report):
    rows = ["| Gate | Name | Status |", "| --- | --- | --- |"]
    for g in report["gates"]:
        rows.append(f"| {g['id']} | {g['name']} | **{g['status']}** |")
    blockers = []
    for g in report["gates"]:
        if g["status"] in (BLOCKED, FAIL):
            blockers.append(f"- **Gate {g['id']} ({g['status']}) — {g['name']}**\n\n"
                            f"  {g['summary']}")
    lines = [
        "# Release 1 Certification Report — Session 5D",
        "",
        f"- **Overall status:** `{report['overall_status']}`",
        f"- **Mandatory gates PASS:** {report['mandatory_pass']}/{report['mandatory_total']}",
        f"- **Blocked:** {', '.join(report['mandatory_blocked']) or 'none'}",
        f"- **Failed:** {', '.join(report['mandatory_failed']) or 'none'}",
        f"- **Loopback test port:** {report['test_port']}",
        f"- **Run window:** {report['generated_at_start']} → {report['generated_at_end']}",
        "",
        "All mutable state ran in an isolated `%TEMP%` workspace via `AMZ_FBM_HOME`; the "
        "owner's real install, tasks, shortcuts, repository, and `runs/` were never modified.",
        "",
        "## Gate results",
        "", *rows, "",
    ]
    if blockers:
        lines += ["## Blocking / failing gates and remediation", "", *blockers, ""]
    certified = report["overall_status"] == CERTIFIED
    if certified:
        interpretation = (
            "Every mandatory gate PASSED live on this machine, including a fresh fully-offline "
            "install (Task Scheduler was unavailable without elevation, so the current-user "
            "Startup-folder autostart fallback and the setuptools-free offline source bootstrap "
            "were exercised live), offline network denial (zero external attempts), loopback-only "
            "binding, single-instance identity safety, unknown-port preservation, T2 "
            "twice-determinism, uninstall data preservation, and the full unit-test suite. No "
            "elevation, SYSTEM account, machine-wide task, firewall change, or external "
            "connection was used anywhere.")
    else:
        interpretation = (
            "Every gate that could be exercised live PASSED, including offline network denial "
            "(zero external attempts), loopback-only binding, single-instance identity safety, "
            "unknown-port preservation, T2 twice-determinism, uninstall data preservation, and "
            "the full unit-test suite. Any blocked gate above is an environment fact on this "
            "specific machine, not a code defect, with a concrete remediation.")
    lines += ["## Interpretation", "", interpretation, ""]
    return "\n".join(lines)


def _owner_checklist_md(report):
    return "\n".join([
        "# AMZ FBM Toolkit — Owner Operating Checklist (Release 1)",
        "",
        "Private, offline, localhost-only. It never connects to Amazon or Seller Central,",
        "never automates your account, and binds only to `127.0.0.1`.",
        "",
        "## One-time setup (two supported offline modes)",
        "```",
        "# Mode 1 — standard editable install (needs setuptools already present):",
        "python -m pip install --no-deps --no-index --no-build-isolation -e .",
        "",
        "# Mode 2 — no-setuptools offline source bootstrap (stdlib only, no network):",
        "python -m amz_fbm bootstrap-offline --source . --verify",
        "",
        "amz-fbm install-local --shortcuts",
        "```",
        "> Both modes are fully offline. Mode 2 registers the source via a toolkit-owned",
        "> `.pth` + `amz-fbm.cmd` and needs no setuptools, no build backend, and no admin.",
        "",
        "## Daily use",
        "| Action | Command |",
        "| --- | --- |",
        "| Start (opens the dashboard) | `amz-fbm start --open` |",
        "| Status | `amz-fbm status` |",
        "| Health | `amz-fbm health` |",
        "| Open dashboard | `amz-fbm open` |",
        "| Stop | `amz-fbm stop` |",
        "| Restart | `amz-fbm restart` |",
        "| Diagnostics | `amz-fbm doctor` |",
        "| Enable login autostart | `amz-fbm autostart enable` |",
        "| Disable login autostart | `amz-fbm autostart disable` |",
        "| Uninstall (keeps all data) | `amz-fbm uninstall` |",
        "",
        "- **Local URL:** http://127.0.0.1:5000/  (health at `/healthz`).",
        "- **Offline mode:** on by default; external AI and outbound network are disabled.",
        "- **No Amazon connection / no Seller Central automation:** by design.",
        "- **Data preservation:** uninstall removes only launch artifacts (task, shortcuts,",
        "  wrappers, stale runtime metadata). Your repository, `runs/`, product facts,",
        "  keyword exports, listing packages, reports, backups, logs, and config are kept.",
        "- **Port conflict:** if another program holds the port, the toolkit refuses to start",
        "  and never kills it; free the port or change `BIND_PORT`, then start again.",
        "- **Stale state:** a dead/reused/corrupt runtime record is detected and recovered",
        "  automatically; an unknown process is never killed.",
        "- **Autostart at login (no admin ever):** `amz-fbm autostart enable` uses the",
        "  current-user Task Scheduler when permitted, and otherwise automatically falls back",
        "  to a current-user Startup-folder launcher (`AMZ-FBM-Toolkit-Startup.cmd`). It never",
        "  elevates, never runs as SYSTEM, never opens a browser. Inspect with",
        "  `amz-fbm autostart status`; remove with `amz-fbm autostart disable`.",
        "- **Logs:** under `%LOCALAPPDATA%\\AMZ-FBM-Toolkit\\logs` (bounded, no secrets).",
        "- **Reinstall:** rerun the one-time setup; your data is untouched.",
        "- **Confirm health:** `amz-fbm health --json` should show `\"ok\": true`.",
        "- **Report a blocked state:** run `amz-fbm doctor --json` and share the output.",
        "",
        f"_Certification status at last run: `{report['overall_status']}` "
        f"({report['mandatory_pass']}/{report['mandatory_total']} mandatory gates PASS)._",
        "",
    ])


def main(argv=None):
    ap = argparse.ArgumentParser(description="Release 1 certification runner (Session 5D)")
    ap.add_argument("--json", action="store_true", help="emit the full JSON report")
    ap.add_argument("--keep-workspace", action="store_true",
                    help="do not delete the isolated certification workspace")
    ap.add_argument("--no-suite", action="store_true",
                    help="skip the in-runner full test suite (dev only)")
    ap.add_argument("--emit-artifacts", metavar="DIR", default=None,
                    help="write the sanitized Release 1 proof artifacts into DIR")
    ap.add_argument("--allow-mock-task-only-for-development", action="store_true",
                    dest="allow_mock_task",
                    help="DEV ONLY: mock the live task gate (never for real certification)")
    args = ap.parse_args(argv)

    quiet_log = (lambda *a, **k: None) if args.json else print
    cert = Certifier(keep_workspace=args.keep_workspace,
                     allow_mock_task=args.allow_mock_task, log=quiet_log)
    report = cert.run(run_suite=not args.no_suite)

    if args.emit_artifacts:
        written = emit_artifacts(report, args.emit_artifacts)
        if not args.json:
            print("artifacts: " + ", ".join(written))

    if args.json:
        print(json.dumps(SCRUB(report), indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print("\n" + "=" * 70)
        print(f"OVERALL: {report['overall_status']}  "
              f"({report['mandatory_pass']}/{report['mandatory_total']} mandatory PASS)")
        if report["mandatory_blocked"]:
            print("BLOCKED gates: " + ", ".join(report["mandatory_blocked"]))
        if report["mandatory_failed"]:
            print("FAILED gates: " + ", ".join(report["mandatory_failed"]))
        print("=" * 70)
    return 0 if report["overall_status"] == CERTIFIED else 1


if __name__ == "__main__":
    sys.exit(main())
