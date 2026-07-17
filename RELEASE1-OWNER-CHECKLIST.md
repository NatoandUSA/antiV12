# AMZ FBM Toolkit — Owner Operating Checklist (Release 1)

Private, offline, localhost-only. It never connects to Amazon or Seller Central,
never automates your account, and binds only to `127.0.0.1`.

## One-time setup
```
python -m pip install --no-deps -e .        # needs setuptools present (see note)
amz-fbm install-local --shortcuts
```
> Offline note: a fully offline install needs `setuptools` already installed
> (`pip install setuptools` once while online). Then reinstalls work offline with
> `python -m pip install --no-deps --no-index --no-build-isolation -e .`.

## Daily use
| Action | Command |
| --- | --- |
| Start (opens the dashboard) | `amz-fbm start --open` |
| Status | `amz-fbm status` |
| Health | `amz-fbm health` |
| Open dashboard | `amz-fbm open` |
| Stop | `amz-fbm stop` |
| Restart | `amz-fbm restart` |
| Diagnostics | `amz-fbm doctor` |
| Enable login autostart | `amz-fbm autostart enable` |
| Disable login autostart | `amz-fbm autostart disable` |
| Uninstall (keeps all data) | `amz-fbm uninstall` |

- **Local URL:** http://127.0.0.1:5000/  (health at `/healthz`).
- **Offline mode:** on by default; external AI and outbound network are disabled.
- **No Amazon connection / no Seller Central automation:** by design.
- **Data preservation:** uninstall removes only launch artifacts (task, shortcuts,
  wrappers, stale runtime metadata). Your repository, `runs/`, product facts,
  keyword exports, listing packages, reports, backups, logs, and config are kept.
- **Port conflict:** if another program holds the port, the toolkit refuses to start
  and never kills it; free the port or change `BIND_PORT`, then start again.
- **Stale state:** a dead/reused/corrupt runtime record is detected and recovered
  automatically; an unknown process is never killed.
- **Autostart on a single-admin PC:** if `amz-fbm autostart enable` reports Access
  denied, run it once from an elevated terminal (right-click → Run as administrator).
  A standard-user Windows profile needs no elevation.
- **Logs:** under `%LOCALAPPDATA%\AMZ-FBM-Toolkit\logs` (bounded, no secrets).
- **Reinstall:** rerun the one-time setup; your data is untouched.
- **Confirm health:** `amz-fbm health --json` should show `"ok": true`.
- **Report a blocked state:** run `amz-fbm doctor --json` and share the output.

_Certification status at last run: `RELEASE1_BLOCKED` (15/17 mandatory gates PASS)._
