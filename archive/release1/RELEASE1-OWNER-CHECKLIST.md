# AMZ FBM Toolkit — Owner Operating Checklist (Release 1)

Private and localhost-only. The one hard line: **it never operates inside your Amazon
account** — no Seller Central login, no Amazon credentials, no Amazon API, no browser
automation, no account reports, no writes. It may use approved open-web / research services
(`CONNECTED_RESEARCH`) or run fully local (`LOCAL_SAFE`); either way it binds only to
`127.0.0.1`. Check or change the mode with `amz-fbm connectivity status` /
`amz-fbm connectivity mode connected-research|local-safe`, and prove the boundary with
`amz-fbm connectivity amazon-boundary`.

## One-time setup (two supported local-install modes)
```
# Mode 1 — standard editable install (needs setuptools already present):
python -m pip install --no-deps --no-index --no-build-isolation -e .

# Mode 2 — no-setuptools offline source bootstrap (stdlib only, no network):
python -m amz_fbm bootstrap-offline --source . --verify

amz-fbm install-local --shortcuts
```
> Both install modes need no network, no setuptools requirement for Mode 2, and no admin.
> Mode 2 registers the source via a toolkit-owned `.pth` + `amz-fbm.cmd`. Neither install
> mode grants any Amazon-account access — that boundary is permanent in every connectivity
> mode.

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
- **Autostart at login (no admin ever):** `amz-fbm autostart enable` uses the
  current-user Task Scheduler when permitted, and otherwise automatically falls back
  to a current-user Startup-folder launcher (`AMZ-FBM-Toolkit-Startup.cmd`). It never
  elevates, never runs as SYSTEM, never opens a browser. Inspect with
  `amz-fbm autostart status`; remove with `amz-fbm autostart disable`.
- **Logs:** under `%LOCALAPPDATA%\AMZ-FBM-Toolkit\logs` (bounded, no secrets).
- **Reinstall:** rerun the one-time setup; your data is untouched.
- **Confirm health:** `amz-fbm health --json` should show `"ok": true`.
- **Report a blocked state:** run `amz-fbm doctor --json` and share the output.

_Certification status at last run: `RELEASE1_CERTIFIED` (17/17 mandatory gates PASS)._
