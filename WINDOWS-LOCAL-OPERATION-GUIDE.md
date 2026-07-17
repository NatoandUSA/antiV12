# AMZ FBM Toolkit — Windows Local Operation Guide

A private, **localhost-only** listing toolkit for one owner on one Windows PC. The one
hard line is that **it never operates inside your Amazon account** — no Seller Central
login, no Amazon credentials, no Amazon API, no browser automation, no account reports,
no writes. It may use approved **open-web and third-party research** services
(`CONNECTED_RESEARCH`), or run **fully local** (`LOCAL_SAFE`) when you prefer. Either way
the dashboard binds only to `127.0.0.1`, never opens a firewall port, and never needs
administrator rights. You bridge results to Seller Central **manually**.

Everything below runs as your normal Windows user.

## Connectivity (Session 6A.1)

```
amz-fbm connectivity status                  # current mode + capabilities + Amazon boundary
amz-fbm connectivity mode connected-research # approved open web + research services
amz-fbm connectivity mode local-safe         # loopback only (fully local)
amz-fbm connectivity capabilities            # enabled + permanently-blocked capabilities
amz-fbm connectivity amazon-boundary         # the permanent "NOT AVAILABLE" Amazon boundary
amz-fbm doctor                               # read-only health incl. connectivity + boundary
```

- The toolkit may use the open web and approved research services; it **never** operates
  inside your Amazon account. Seller Central access does not exist and Amazon credentials
  cannot be configured.
- Reading **public** Amazon documentation is a separate, occasional, human-triggered,
  read-only feature (off by default). **Amazon product/search scraping is not supported.**
- **External AI is optional** and must be explicitly enabled with an approved provider — an
  API key alone activates nothing.
- Connected information stays **advisory** until you verify it. Product facts, claim
  evidence, and the PageAuditor remain authoritative. You remain the only bridge to Seller
  Central.

---

## 1. Install locally (one time)

From the project folder, in a normal (non-admin) terminal. There are **two supported
fully-offline install modes** — pick whichever your Python supports:

**Mode 1 — standard editable install** (when `setuptools` is already present):

```
python -m pip install --no-deps --no-index --no-build-isolation -e .
amz-fbm install-local --shortcuts
```

**Mode 2 — no-setuptools offline source bootstrap** (stdlib only, no build backend,
no network — use this when a fresh Python 3.12+ has no `setuptools`):

```
python -m amz_fbm bootstrap-offline --source . --verify
amz-fbm install-local --shortcuts
```

- Mode 1 registers the `amz-fbm` command using packages you already have.
  `--no-deps --no-index` means **no downloads**.
- Mode 2 registers the source with a toolkit-owned `.pth` and an `amz-fbm.cmd`
  wrapper (`python -m amz_fbm`). It needs **no setuptools, no build backend, and no
  admin**, and verifies both command forms before reporting success.
- `install-local` creates your per-user folders, writes an install manifest (recording
  the active installation mode), an offline config, generates launcher wrappers, and
  (with `--shortcuts`) adds Desktop and Start Menu shortcuts. It runs `doctor` at the
  end.

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
amz-fbm autostart enable                     # auto: task scheduler, else startup folder
amz-fbm autostart enable --method task-scheduler
amz-fbm autostart enable --method startup-folder
amz-fbm autostart disable
amz-fbm autostart status
```

`enable` (default `--method auto`) tries a **current-user** Windows Task Scheduler task
named `AMZ-FBM-Toolkit` that runs at logon with **limited** privileges and starts only
the dashboard (no browser, offline). Success is confirmed by re-reading the task, not
just the exit code.

If Task Scheduler is unavailable **without elevation** (for example a single-admin PC
whose UAC-filtered token is denied task creation), `enable` automatically falls back to
a **current-user Startup-folder launcher**
(`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AMZ-FBM-Toolkit-Startup.cmd`).
The fallback is reported honestly as `STARTUP_FOLDER_CURRENT_USER` — never as a hidden
task success.

Neither method **ever** requires elevation, runs as SYSTEM, requests highest
privileges, is machine-wide, or opens a browser. `status` reports the actual active
method; `disable` removes every toolkit-owned autostart method (task **and** Startup
launcher) and never touches unrelated tasks or Startup-folder entries.

> Do **not** run an elevated terminal to enable autostart — the Startup-folder fallback
> is the supported no-admin path when Task Scheduler is blocked.

## 8. Doctor (self-check)

```
amz-fbm doctor
```

A read-only local check of Python, the package, runtime policy, directories,
instance state, port conflicts, `/healthz`, **installation mode** (editable vs offline
source bootstrap), **autostart method** (task scheduler / Startup-folder fallback, and
that no elevation is required), shortcuts, and required modules. It performs **no**
network request, changes nothing, and never prints secrets.

## 9. Uninstall (keeps your data)

```
amz-fbm uninstall
```

Stops a verified instance, removes the scheduled task, the current-user Startup-folder
autostart launcher, shortcuts, generated wrappers, any toolkit-owned offline-bootstrap
`.pth` / `amz-fbm.cmd`, and stale runtime metadata — and **preserves all business
data** (and never removes a `.pth`/`.cmd`/Startup file it does not own):

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

**Reinstall** — safe to re-run any time (either install mode from §1):

```
python -m pip install --no-deps --no-index --no-build-isolation -e .   # or:
python -m amz_fbm bootstrap-offline --source . --verify
amz-fbm install-local --shortcuts
```

Existing config is backed up before it is replaced; your data is untouched.
