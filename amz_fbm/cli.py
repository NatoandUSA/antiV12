#!/usr/bin/env python3
"""``amz-fbm`` command-line interface (Session 5C / ACT-018, ACT-019).

A thin, offline-first launcher over the existing toolkit. Every command is local,
loopback-only, and never contacts Amazon, Seller Central, or the Internet. The heavy
lifting lives in the shared core authorities (runtime_policy, instance_manager,
windows_integration, local_install); this module only parses arguments, dispatches,
and formats output. Pass ``--json`` (anywhere) for machine-readable output.

Exit codes: 0 on success, non-zero on failure or an unhealthy/invalid state, so the
CLI composes cleanly in scripts. Unknown commands/arguments exit non-zero with help.
"""
import argparse
import json
import os
import sys

from amz_fbm import PROJECT_ROOT, get_version

# Resolve the existing toolkit modules the same way the dashboard does.
for _d in ("core", "listing", "dashboard"):
    _p = os.path.join(PROJECT_ROOT, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

import app_paths as AP          # noqa: E402
import connectivity_config as CC  # noqa: E402
import instance_manager as IM   # noqa: E402
import local_install as LI      # noqa: E402
import runtime_policy as RP     # noqa: E402
import windows_integration as WI  # noqa: E402

PROG = "amz-fbm"


# ---- helpers -----------------------------------------------------------------
def _policy():
    """Runtime policy resolved from the offline defaults + persisted local config."""
    return RP.load_runtime_policy(config_path=AP.local_config_path())


def _emit(result, json_out, lines):
    """Print JSON or human lines and return the result unchanged."""
    if json_out:
        print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        for ln in lines:
            print(ln)
    return result


def _ok_code(ok):
    return 0 if ok else 1


# ---- command handlers --------------------------------------------------------
def cmd_start(args, json_out):
    res = IM.start_instance(open_browser=args.open, policy=_policy())
    ok = res["status"] in (IM.STARTED, IM.ALREADY_RUNNING)
    lines = [f"start: {res['status']} — {res['message']}"]
    if res.get("bind_host"):
        lines.append(f"  dashboard: http://{res['bind_host']}:{res['bind_port']}/")
    _emit(res, json_out, lines)
    return _ok_code(ok)


def cmd_stop(args, json_out):
    res = IM.stop_instance(policy=_policy())
    ok = res["status"] in (IM.STOPPED_OK, IM.NOT_RUNNING)
    _emit(res, json_out, [f"stop: {res['status']} — {res['message']}"])
    return _ok_code(ok)


def cmd_restart(args, json_out):
    res = IM.restart_instance(open_browser=args.open, policy=_policy())
    ok = res["status"] in (IM.STARTED, IM.ALREADY_RUNNING)
    _emit(res, json_out, [f"restart: {res['status']} — {res['message']}"])
    return _ok_code(ok)


def cmd_status(args, json_out):
    st = IM.get_instance_status(policy=_policy())
    autos = WI.autostart_status()
    scut = WI.shortcuts_status()
    manifest = LI.read_manifest()
    policy = _policy()
    last = None
    try:
        import diagnostics as D
        ev = D.last_event()
        last = ev.code if ev else None
    except Exception:
        last = None
    payload = {
        "status": st["status"], "detail": st.get("detail", ""),
        "pid": st.get("pid"), "bind_host": st["bind_host"], "bind_port": st["bind_port"],
        "version": st.get("version") or IM.dashboard_version(),
        "uptime_seconds": st.get("uptime_seconds"),
        "health": st.get("health"),
        "offline_only": policy.offline_only,
        "external_ai_enabled": policy.external_ai_enabled,
        "outbound_network_enabled": policy.outbound_network_enabled,
        "autostart": {"installed": autos["installed"], "valid": autos["valid"]},
        "shortcuts": {"installed": scut["installed"]},
        "install_manifest_present": manifest is not None,
        "last_diagnostic": last,
    }
    lines = [
        f"status: {payload['status']}  ({payload['detail']})",
        f"  bind: http://{payload['bind_host']}:{payload['bind_port']}/  "
        f"offline={payload['offline_only']} external_ai={payload['external_ai_enabled']}",
        f"  pid: {payload['pid']}  health: {payload['health']}  "
        f"uptime: {payload['uptime_seconds']}",
        f"  autostart: {'on' if autos['valid'] else 'off'}  "
        f"shortcuts: {'yes' if scut['installed'] else 'no'}  "
        f"installed: {'yes' if manifest else 'no'}",
    ]
    _emit(payload, json_out, lines)
    return 0


def cmd_health(args, json_out):
    policy = _policy()
    health = IM.http_get_json(IM.health_url_for(policy), timeout=2.0)
    ok = bool(health and health.get("ok") is True)
    payload = {"reachable": bool(health), "ok": ok,
               "health": {k: v for k, v in (health or {}).items()
                          if k not in ("_http_status",)}}
    _emit(payload, json_out,
          [f"health: {'HEALTHY' if ok else ('UNHEALTHY' if health else 'UNREACHABLE')}"])
    return _ok_code(ok)


def cmd_open(args, json_out):
    policy = _policy()
    health = IM.http_get_json(IM.health_url_for(policy), timeout=2.0)
    if not (health and health.get("ok") is True):
        _emit({"ok": False, "status": "NOT_HEALTHY",
               "message": "dashboard is not healthy; not opening the browser"},
              json_out, ["open: refused — dashboard is not healthy (run `amz-fbm start`)"])
        return 1
    opened = IM._open_browser(policy)
    _emit({"ok": bool(opened), "url": IM.dashboard_url_for(policy)}, json_out,
          [f"open: {IM.dashboard_url_for(policy)}"])
    return _ok_code(opened)


def cmd_doctor(args, json_out):
    rep = LI.doctor(policy=_policy())
    lines = [f"doctor: {rep['summary']} (ok={rep['ok']})"]
    c = rep["checks"]
    conn = c.get("connectivity", {})
    amz = c.get("amazon_boundary", {})
    lines += [
        f"  policy valid: {c['runtime_policy']['valid']}  "
        f"offline: {c['runtime_policy']['offline_only']}  "
        f"loopback: {c['runtime_policy']['loopback_only_effective']}",
        f"  connectivity: {conn.get('connectivity_mode')}  "
        f"public web: {conn.get('public_web_research_enabled')}  "
        f"external AI: allowed={conn.get('external_ai_allowed')} "
        f"enabled={conn.get('external_ai_enabled')}",
        f"  amazon boundary: isolation={amz.get('amazon_account_isolation')}  "
        f"all account/API/writes blocked={amz.get('all_blocked')}  "
        f"credential store={amz.get('amazon_credential_store_available')}",
        f"  dirs writable: {c['runtime_directories']['all_writable']}  "
        f"modules present: {c['required_modules']['all_present']}",
        f"  instance: {c['instance']['status']}  "
        f"port conflict: {c['port_conflict']['conflict']}",
        f"  autostart: {c['autostart']['installed']}  "
        f"shortcuts: {c['shortcuts']['installed']}",
    ]
    _emit(rep, json_out, lines)
    return _ok_code(rep["ok"])


# ---- connectivity (Session 6A.1) --------------------------------------------
def _connectivity_policy():
    """Runtime policy that honors the persisted connectivity choice + local config."""
    return CC.load_policy(config_path=AP.local_config_path())


_MODE_ALIASES = {
    "connected-research": RP.CONNECTED_RESEARCH, "connected_research": RP.CONNECTED_RESEARCH,
    "local-safe": RP.LOCAL_SAFE, "local_safe": RP.LOCAL_SAFE,
    "test-deny-external": RP.TEST_DENY_EXTERNAL, "test_deny_external": RP.TEST_DENY_EXTERNAL,
}


def _connectivity_status_payload(policy):
    c = policy.connectivity_to_dict()
    return {
        "connectivity_mode": policy.connectivity_mode,
        "public_web_research_enabled": policy.public_web_research_enabled,
        "public_policy_research_enabled": policy.public_policy_research_enabled,
        "amazon_public_documentation_enabled": policy.amazon_public_documentation_enabled,
        "third_party_data_enabled": policy.third_party_data_enabled,
        "market_data_enabled": policy.market_data_enabled,
        "supplier_connections_enabled": policy.supplier_connections_enabled,
        "external_ai_allowed": policy.external_ai_allowed,
        "external_ai_enabled": policy.external_ai_enabled,
        "external_ai_provider": policy.external_ai_provider,
        "toolkit_update_discovery_enabled": policy.toolkit_update_discovery_enabled,
        "deterministic_local_fallback_enabled": policy.deterministic_local_fallback_enabled,
        "dashboard_loopback_only": policy.dashboard_loopback_only,
        "amazon_account_isolation": policy.amazon_account_isolation,
        "amazon_credential_store_available": policy.amazon_credential_store_available,
        "amazon_seller_central_enabled": policy.amazon_seller_central_enabled,
        "amazon_authenticated_access_enabled": policy.amazon_authenticated_access_enabled,
        "amazon_api_enabled": policy.amazon_api_enabled,
        "amazon_browser_automation_enabled": policy.amazon_browser_automation_enabled,
        "amazon_account_report_pull_enabled": policy.amazon_account_report_pull_enabled,
        "amazon_network_writes_enabled": policy.amazon_network_writes_enabled,
        "policy_sha256": c["connectivity_policy_sha256"],
        "migration_warnings": c["migration_warnings"],
        "is_valid": policy.is_valid,
    }


def cmd_connectivity(args, json_out):
    action = args.conn_action
    if action == "status":
        p = _connectivity_policy()
        payload = _connectivity_status_payload(p)
        lines = [
            f"connectivity: {payload['connectivity_mode']}  "
            f"(valid={payload['is_valid']})",
            f"  public web research : {payload['public_web_research_enabled']}   "
            f"public policy: {payload['public_policy_research_enabled']}",
            f"  third-party data    : {payload['third_party_data_enabled']}   "
            f"market: {payload['market_data_enabled']}   "
            f"supplier: {payload['supplier_connections_enabled']}",
            f"  official amazon docs : {payload['amazon_public_documentation_enabled']} "
            f"(separate, human-triggered, read-only)",
            f"  external AI          : allowed={payload['external_ai_allowed']} "
            f"enabled={payload['external_ai_enabled']} "
            f"provider={payload['external_ai_provider']}",
            f"  update discovery     : {payload['toolkit_update_discovery_enabled']}   "
            f"local fallback: {payload['deterministic_local_fallback_enabled']}",
            f"  dashboard exposure   : localhost only ({payload['dashboard_loopback_only']})",
            "  AMAZON ACCOUNT       : isolation=PASS  credentials=NONE  seller-central=OFF  "
            "api=OFF  browser-automation=OFF  reports=OFF  writes=OFF",
            f"  policy_sha256        : {payload['policy_sha256'][:16]}…",
        ]
        _emit(payload, json_out, lines)
        return _ok_code(payload["is_valid"])

    if action == "mode":
        target = _MODE_ALIASES.get(str(args.value).lower().replace("_", "-"))
        if not target:
            _emit({"ok": False, "error": f"invalid mode {args.value!r}"}, json_out,
                  [f"connectivity mode: invalid mode {args.value!r} "
                   f"(use connected-research | local-safe)"])
            return 2
        existing = CC.read_connectivity_config() or dict(CC.NEW_INSTALL_DEFAULTS)
        existing["connectivity_mode"] = target
        res = CC.write_connectivity_config(existing)
        p = _connectivity_policy()
        payload = {"ok": p.is_valid, "connectivity_mode": p.connectivity_mode,
                   "config": res["path"], "backed_up": res["backed_up"],
                   "amazon_account_isolation": p.amazon_account_isolation,
                   "amazon_credential_store_available": p.amazon_credential_store_available}
        _emit(payload, json_out,
              [f"connectivity mode -> {p.connectivity_mode} "
               f"(Amazon account access still NOT AVAILABLE)"])
        return _ok_code(p.is_valid)

    if action == "capabilities":
        p = _connectivity_policy()
        allowed = {name: p.capability_enabled(name) for name in RP.ALLOWED_CAPABILITIES}
        prohibited = {name: False for name in RP.PROHIBITED_CAPABILITIES}
        payload = {"connectivity_mode": p.connectivity_mode, "allowed": allowed,
                   "prohibited_permanently_blocked": prohibited}
        lines = [f"capabilities ({p.connectivity_mode}):"]
        lines += [f"  [{'x' if v else ' '}] {k}" for k, v in allowed.items()]
        lines.append("  permanently blocked (Amazon account): " +
                     ", ".join(prohibited) if prohibited else "")
        _emit(payload, json_out, lines)
        return 0

    if action == "verify":
        p = _connectivity_policy()
        checks = {
            "policy_valid": p.is_valid,
            "dashboard_loopback_only": p.dashboard_loopback_only,
            "amazon_account_isolation": p.amazon_account_isolation,
            "amazon_credential_store_absent": not p.amazon_credential_store_available,
            "seller_central_blocked": not p.amazon_seller_central_enabled,
            "amazon_api_blocked": not p.amazon_api_enabled,
            "amazon_browser_automation_blocked": not p.amazon_browser_automation_enabled,
            "amazon_report_pull_blocked": not p.amazon_account_report_pull_enabled,
            "amazon_writes_blocked": not p.amazon_network_writes_enabled,
            "deterministic_local_fallback": p.deterministic_local_fallback_enabled,
        }
        ok = all(checks.values())
        payload = {"ok": ok, "connectivity_mode": p.connectivity_mode, "checks": checks}
        _emit(payload, json_out,
              [f"connectivity verify: {'PASS' if ok else 'FAIL'} ({p.connectivity_mode})"] +
              [f"  {'ok ' if v else 'X  '}{k}" for k, v in checks.items()])
        return _ok_code(ok)

    if action == "amazon-boundary":
        p = _connectivity_policy()
        blocks = {
            "amazon_account_isolation": p.amazon_account_isolation,
            "amazon_credential_store_available": p.amazon_credential_store_available,
            "amazon_seller_central_enabled": p.amazon_seller_central_enabled,
            "amazon_authenticated_access_enabled": p.amazon_authenticated_access_enabled,
            "amazon_api_enabled": p.amazon_api_enabled,
            "amazon_browser_automation_enabled": p.amazon_browser_automation_enabled,
            "amazon_account_report_pull_enabled": p.amazon_account_report_pull_enabled,
            "amazon_network_writes_enabled": p.amazon_network_writes_enabled,
        }
        payload = {"amazon_account_isolation": True, "boundary": blocks,
                   "prohibited_capabilities": list(RP.PROHIBITED_CAPABILITIES)}
        lines = ["Amazon account boundary (permanent, not configurable):",
                 "  Amazon account access : NOT AVAILABLE",
                 "  Seller Central access : NOT AVAILABLE",
                 "  Amazon credentials    : NOT SUPPORTED (no credential store)",
                 "  Amazon selling / marketplace / advertising APIs: NOT AVAILABLE",
                 "  Amazon browser automation : NOT AVAILABLE",
                 "  Amazon account reports    : NOT AVAILABLE",
                 "  Amazon writes/actions     : NOT AVAILABLE",
                 f"  permanently blocked capabilities: {len(RP.PROHIBITED_CAPABILITIES)}"]
        _emit(payload, json_out, lines)
        return 0

    _emit({"ok": False, "error": "unknown connectivity action"}, json_out,
          ["connectivity: unknown action"])
    return 2


def cmd_install_local(args, json_out):
    res = LI.install_local(shortcuts=args.shortcuts, autostart=args.autostart)
    lines = [f"install-local: {res['status']} (ok={res['ok']})",
             f"  runtime root: {res.get('runtime_root')}",
             f"  shortcuts: {'created' if args.shortcuts else 'skipped'}  "
             f"autostart: {'enabled' if args.autostart else 'not enabled (default)'}"]
    _emit(res, json_out, lines)
    return _ok_code(res["ok"])


def cmd_autostart(args, json_out):
    if args.action == "enable":
        res = WI.enable_autostart(method=getattr(args, "method", "auto"))
        note = res.get("warning") or res.get("detail") or ""
        lines = [f"autostart enable: ok={res['ok']} method={res.get('method')} "
                 f"verified={res.get('verified')}"]
        if note:
            lines.append("  " + note)
        _emit(res, json_out, lines)
        return _ok_code(res["ok"])
    if args.action == "disable":
        res = WI.disable_autostart()
        _emit(res, json_out, [f"autostart disable: ok={res['ok']} "
                              f"method={res.get('method')}"])
        return _ok_code(res["ok"])
    state = WI.autostart_state()
    _emit(state, json_out, [f"autostart status: enabled={state['enabled']} "
                            f"method={state['method']} "
                            f"requires_admin={state['requires_admin']}"])
    return 0


def cmd_bootstrap_offline(args, json_out):
    import offline_bootstrap as OB    # local import: usable before the wrapper exists
    if args.remove:
        res = OB.remove_offline_source(python_executable=args.python)
        _emit(res.to_dict(), json_out,
              [f"bootstrap-offline remove: ok={res.ok} removed={len(res.files_removed)}"])
        return _ok_code(res.ok)
    source = args.source or PROJECT_ROOT
    res = OB.bootstrap_offline_source(source, python_executable=args.python,
                                      verify=args.verify)
    lines = [f"bootstrap-offline: ok={res.ok} mode={res.mode}",
             f"  created={len(res.files_created)} updated={len(res.files_updated)}"]
    if res.errors:
        lines.append("  errors: " + "; ".join(res.errors))
    _emit(res.to_dict(), json_out, lines)
    return _ok_code(res.ok)


def cmd_shortcuts(args, json_out):
    if args.action == "create":
        res = WI.create_shortcuts()
        _emit(res, json_out, [f"shortcuts create: ok={res['ok']} "
                              f"count={len(res.get('created', []))}"])
        return _ok_code(res["ok"])
    if args.action == "remove":
        res = WI.remove_shortcuts()
        _emit(res, json_out, [f"shortcuts remove: ok={res['ok']} "
                              f"count={len(res.get('removed', []))}"])
        return _ok_code(res["ok"])
    res = WI.shortcuts_status()
    _emit(res, json_out, [f"shortcuts status: installed={res['installed']} "
                          f"desktop={res['desktop']} start_menu={len(res['start_menu'])}"])
    return 0


def cmd_uninstall(args, json_out):
    res = LI.uninstall(policy=_policy())
    r = res["report"]
    lines = [f"uninstall: {res['status']} (ok={res['ok']})",
             f"  removed: {len(r['removed'])}  preserved: {len(r['preserved'])}  "
             f"failed: {len(r['failed'])}",
             "  business data preserved (repository, runs/, outputs, config, backups)"]
    if r["manual_cleanup"]:
        lines.append("  manual: " + "; ".join(r["manual_cleanup"]))
    _emit(res, json_out, lines)
    return _ok_code(res["ok"])


def cmd_version(args, json_out):
    payload = {"version": get_version(), "dashboard_version": IM.dashboard_version(),
               "service": IM.SERVICE_NAME}
    _emit(payload, json_out, [f"{PROG} {payload['version']}"])
    return 0


# ---- parser ------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(
        prog=PROG,
        description="Private, offline, localhost-only AMZ FBM toolkit launcher. "
                    "Add --json to any command for machine-readable output. Never "
                    "connects to Amazon or Seller Central.")
    p.add_argument("--version", action="version", version=f"{PROG} {get_version()}")
    sub = p.add_subparsers(dest="command", required=True, metavar="<command>")

    sp = sub.add_parser("start", help="start the local dashboard (detached)")
    sp.add_argument("--open", action="store_true", help="open the browser once healthy")
    sp.set_defaults(func=cmd_start)

    sub.add_parser("stop", help="stop the verified instance").set_defaults(func=cmd_stop)

    rp = sub.add_parser("restart", help="verified stop then start")
    rp.add_argument("--open", action="store_true", help="open the browser once healthy")
    rp.set_defaults(func=cmd_restart)

    sub.add_parser("status", help="report instance + install status").set_defaults(func=cmd_status)
    sub.add_parser("health", help="query local /healthz").set_defaults(func=cmd_health)
    sub.add_parser("open", help="open the local dashboard when healthy").set_defaults(func=cmd_open)
    sub.add_parser("doctor", help="local read-only diagnostics").set_defaults(func=cmd_doctor)

    cp = sub.add_parser("connectivity",
                        help="connectivity mode + permanent Amazon-account boundary")
    csub = cp.add_subparsers(dest="conn_action", required=True, metavar="<action>")
    csub.add_parser("status", help="report the connectivity mode + capabilities")
    csub.add_parser("capabilities", help="list connected-research + permanently blocked capabilities")
    csub.add_parser("verify", help="verify the connectivity policy + Amazon boundary")
    csub.add_parser("amazon-boundary", help="report the permanent Amazon-account boundary")
    mp = csub.add_parser("mode", help="set the connectivity mode (connected-research | local-safe)")
    # TEST_DENY_EXTERNAL stays internal: accepted but not advertised in help.
    mp.add_argument("value", metavar="connected-research|local-safe",
                    help="connected-research (open web + research) or local-safe (loopback only)")
    cp.set_defaults(func=cmd_connectivity)

    ip = sub.add_parser("install-local", help="prepare the local install (no autostart by default)")
    ip.add_argument("--shortcuts", action="store_true", help="also create shortcuts")
    ip.add_argument("--autostart", action="store_true", help="also enable current-user autostart")
    ip.set_defaults(func=cmd_install_local)

    ap = sub.add_parser("autostart", help="current-user login autostart (task or startup folder)")
    ap.add_argument("action", choices=["enable", "disable", "status"])
    ap.add_argument("--method", choices=["auto", "task-scheduler", "startup-folder"],
                    default="auto",
                    help="enable via auto (default), task-scheduler, or startup-folder")
    ap.set_defaults(func=cmd_autostart)

    bp = sub.add_parser("bootstrap-offline",
                        help="setuptools-free offline source install (.pth + amz-fbm.cmd)")
    bp.add_argument("--source", default=None, help="toolkit source root (default: this repo)")
    bp.add_argument("--python", default=None, help="target interpreter (default: this python)")
    bp.add_argument("--verify", dest="verify", action="store_true", default=True,
                    help="verify both command forms after install (default)")
    bp.add_argument("--no-verify", dest="verify", action="store_false",
                    help="skip post-install verification")
    bp.add_argument("--remove", action="store_true",
                    help="remove only the toolkit-owned bootstrap files")
    bp.set_defaults(func=cmd_bootstrap_offline)

    scp = sub.add_parser("shortcuts", help="Desktop + Start Menu shortcuts")
    scp.add_argument("action", choices=["create", "remove", "status"])
    scp.set_defaults(func=cmd_shortcuts)

    sub.add_parser("uninstall", help="remove launch artifacts; preserve all data").set_defaults(func=cmd_uninstall)
    sub.add_parser("version", help="print the toolkit version").set_defaults(func=cmd_version)
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    json_out = False
    if "--json" in argv:                      # global, position-independent flag
        json_out = True
        argv = [a for a in argv if a != "--json"]
    parser = build_parser()
    if not argv:
        parser.print_help(sys.stderr)
        return 2
    args = parser.parse_args(argv)
    return args.func(args, json_out)


if __name__ == "__main__":
    sys.exit(main())
