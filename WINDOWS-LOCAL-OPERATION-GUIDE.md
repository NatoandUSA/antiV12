# AMZ FBM Toolkit — Windows Local Operation Guide

A private, **offline, localhost-only** listing toolkit for one owner on one Windows
PC. It never connects to Amazon or Seller Central, never opens a firewall port,
never needs administrator rights, and never downloads anything during normal use.
You bridge results to Seller Central **manually**.

Everything below runs as your normal Windows user.

---

## 1. Install locally (one time)

From the project folder, in a normal (non-admin) terminal:

```
python -m pip install --no-deps -e .
amz-fbm install-local --shortcuts
```

- `pip install --no-deps -e .` registers the `amz-fbm` command using packages you
  already have. `--no-deps` means **no downloads**.
- `install-local` creates your per-user folders, writes an install manifest and an
  offline config, generates launcher wrappers, and (with `--shortcuts`) adds
  Desktop and Start Menu shortcuts. It runs `doctor` at the end.

Autostart is **off** unless you ask for it (see §7). To do everything at once:

```
amz-fbm install-local --shortcuts --autostart
```

---

## 2. Start

```
amz-fbm start --open
```

Starts the dashboard as a detached background process bound to `127.0.0.1`, waits
until `/healthz` reports healthy, verifies it is really this toolkit, then opens the
browser. Without `--open` it starts silently.

Normal address: **http://127.0.0.1:5000**

If it is already running, `start` reports `ALREADY_RUNNING` and does **not** launch a
second copy.

## 3. Status

```
amz-fbm status
```

Shows one of: `RUNNING_HEALTHY`, `RUNNING_UNHEALTHY`, `STOPPED`,
`STALE_RUNTIME_STATE`, `PORT_IN_USE_BY_UNKNOWN_PROCESS`,
`INSTANCE_IDENTITY_UNVERIFIED`, `INVALID_RUNTIME_POLICY` — plus bind address,
PID (only when verified), version, uptime, offline/external-AI state, and whether
autostart and shortcuts are installed.

## 4. Health

```
amz-fbm health
```

Queries only the local `/healthz` with a short timeout. Exit code `0` = healthy.

## 5. Stop

```
amz-fbm stop
```

Verifies the process is really this toolkit, then stops **only that process tree**.
It is idempotent (safe to run when nothing is running) and will refuse to touch an
unknown process. `amz-fbm restart` does a verified stop followed by a verified start.

## 6. Open the dashboard

```
amz-fbm open
```

Opens `http://127.0.0.1:5000` in your browser — but only after confirming the app is
healthy. It never opens an external URL.

---

## 7. Autostart at login (optional)

```
amz-fbm autostart enable
amz-fbm autostart disable
amz-fbm autostart status
```

`enable` creates a **current-user** Windows Task Scheduler task named
`AMZ-FBM-Toolkit` that runs at logon with **limited** privileges and starts only the
dashboard (no browser, offline). It never runs as SYSTEM, never requests highest
privileges, and is never machine-wide. Success is confirmed by re-reading the task,
not just the exit code.

> Note: creating the task requires an ordinary interactive logon session. Some
> locked-down or automation environments block task creation entirely; that is an
> environment restriction, not an admin requirement.

## 8. Doctor (self-check)

```
amz-fbm doctor
```

A read-only local check of Python, the package, runtime policy, directories,
instance state, port conflicts, `/healthz`, autostart, shortcuts, and required
modules. It performs **no** network request, changes nothing, and never prints
secrets.

## 9. Uninstall (keeps your data)

```
amz-fbm uninstall
```

Stops a verified instance, removes the scheduled task, shortcuts, generated
wrappers, and stale runtime metadata — and **preserves all business data**:

- the Git repository and `runs/`
- product facts, keyword exports, listing packages, reports
- your owner configuration, backups, and logs

There is no data-purge option. Uninstall prints a report of what was removed vs.
preserved and is safe to run twice.

---

## Where files live

Everything the launcher writes is under your user profile:

```
%LOCALAPPDATA%\AMZ-FBM-Toolkit\
  runtime\      instance metadata + bin\ launcher wrappers
  logs\         bounded launcher + dashboard logs (rotates at ~1 MB)
  config\       local-config.json (offline defaults, chosen port)
  backups\      timestamped backups of replaced local config
  data\         reserved for launcher-created data
  install-manifest.json
```

Your **business data stays in the project repository** (runs, product facts,
keyword exports, listings, reports) and is never moved or deleted by the launcher.

## Offline & safety

- Offline by default: no external AI, no outbound internet.
- Reachable only through `127.0.0.1` (loopback). Never `0.0.0.0`, no LAN, no tunnel.
- No Amazon API, no Seller Central automation — you copy results in by hand.
- No administrator rights, no Windows service, no SYSTEM account.

## Troubleshooting

**Port already in use** — run `amz-fbm status`.
- `ALREADY_RUNNING` / `RUNNING_HEALTHY`: it's already up; just `amz-fbm open`.
- `PORT_IN_USE_BY_UNKNOWN_PROCESS`: something else holds port 5000. The toolkit will
  **not** kill it. Close that program, or set a different port in
  `%LOCALAPPDATA%\AMZ-FBM-Toolkit\config\local-config.json` (`"bind_port"`) and
  start again.

**Stale state after a crash or reboot** — `amz-fbm status` may show
`STALE_RUNTIME_STATE`. Just run `amz-fbm start` (or `amz-fbm stop` then `start`); the
launcher archives the stale metadata and starts cleanly. It never assumes an
unrelated process is stale.

**Inspect logs** — open `%LOCALAPPDATA%\AMZ-FBM-Toolkit\logs\launcher.log`
(one JSON record per line) and `dashboard.log`. Logs contain no secrets or business
copy.

**Reinstall** — safe to re-run any time:

```
python -m pip install --no-deps -e .
amz-fbm install-local --shortcuts
```

Existing config is backed up before it is replaced; your data is untouched.
