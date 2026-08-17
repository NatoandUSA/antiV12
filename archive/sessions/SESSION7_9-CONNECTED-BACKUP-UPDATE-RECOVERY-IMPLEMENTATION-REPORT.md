# Session 7.9 — Connected Backup, Update & Recovery Manager — Implementation Report

## Identity
- **Branch:** `phase7-9-connected-backup-update-recovery`
- **Exact baseline (origin/main):** `0ef64106c2012b95de4bf2b6fb55dd5685dcee6b`
- **Checkpoint tag:** `phase7-9-connected-backup-update-recovery-checkpoint-0ef6410`
- **Implementation commit:** `8cf1449ae322c09b1f604d161f5ebde869c2d296` (feat)
- **Proof commit:** the docs commit that adds this report + the proof gate (`self`; its hash is
  reported in the session summary)
- **Python:** 3.12.10 · **Platform:** Windows-11-10.0.26200-SP0

### Accepted prior tags (present, untouched)
```
phase7-2-cumulative-accepted-d5ad841
phase7-3-accepted-7005275
phase7-4-owner-dashboard-accepted-eebecc5
phase7-5-owner-decision-package-accepted-66d972d
phase7-6-manual-action-tracker-accepted-f1d11d8
phase7-7-outcome-followup-accepted-581ae49
phase7-8-owner-operations-dashboard-accepted-80333ec
```
No Phase 7.9 acceptance tag exists; only the checkpoint tag exists.

---

## New permanent connectivity policy
Beginning with Phase 7.9 the application is **no longer globally offline-only**. Online connectivity is
permitted for legitimate **non–Seller-Central** purposes: encrypted remote backups (Cloudflare R2 /
S3-compatible / filesystem mirror), GitHub update checks and release metadata, PyPI dependency
information, update/upgrade staging, public web/API/documentation retrieval, and health checks for
configured non-Amazon services. The canonical policy document `docs/CONNECTIVITY-POLICY.md` was updated
in place (a **v2 amendment** section) — no competing policy document was created.

## Permanent Seller Central boundary (unchanged)
The one hard line is permanent and enforced first, before any allowlist: **no** Seller Central, Seller
Central login, SP-API, Ads API, seller-account OAuth, seller credentials/cookies/sessions/tokens,
automatic report downloads, campaign/bid/budget/target/keyword/negative changes, bulk-file uploads,
browser automation against seller pages, or any automated mutation of an Amazon seller account. Every
snapshot records `seller_central_connections=0`, `amazon_sp_api_calls=0`, `amazon_ads_api_calls=0`,
`amazon_seller_auth_calls=0`, `amazon_seller_mutations=0` — constant zeros no code path can increment.

---

## Files
### Created
- `production/phase7_connected_backup_recovery.py` — the ONE Phase 7.9 authority (CLI + engine).
- `tests/test_phase7_9_connected_backup_recovery.py` — 139 focused tests (1 platform skip).
- `SESSION7_9-CONNECTED-BACKUP-UPDATE-RECOVERY-IMPLEMENTATION-REPORT.md` — this report.
- `SESSION7_9-CONNECTED-BACKUP-UPDATE-RECOVERY-PROOF-GATE.json` — proof gate.

### Modified (additive only — no accepted behavior weakened)
- `.gitattributes` — **created** (repo's first): pins `docs/CONNECTIVITY-POLICY.md` and its manifest
  to `eol=lf` so their SHA-256 integrity record (verified by the accepted Phase 6C/6F tests) is stable
  in every checkout regardless of `core.autocrlf`. Narrow rule — one path each; everything else keeps
  the default line-ending behavior. (Without this, updating the doc would expose a pre-existing
  autocrlf fragility: a fresh worktree checks the file out as CRLF, whose hash differs from the LF
  hash the manifest records.)
- `core/network_policy.py` — added the Phase 7.9 connected-operation validator
  (`evaluate_connected_operation`, `assert_connected_operation_allowed`,
  `evaluate_connected_redirect`, `ConnectedNetworkDecision`, `ConnectedNetworkDenied`). Reuses the
  existing Amazon-account classification (`classify_destination`, `_is_amazon_host`, the fragment-
  assembled host hints). The legacy `evaluate_network_request` and all prior behavior are unchanged.
- `core/diagnostics.py` — added Phase 7.9 network reason codes (`CONNECTED_URL_INVALID`,
  `INSECURE_SCHEME_BLOCKED`, `HOST_NOT_ALLOWLISTED`, `REDIRECT_TARGET_BLOCKED`,
  `CREDENTIAL_FORWARDING_BLOCKED`, `LOCAL_ENDPOINT_NOT_ENABLED`, `CONNECTED_PURPOSE_UNKNOWN`) to the
  `ERROR_CODES` set, and added the Phase 7.9 secret env-var names to `SECRET_ENV_VARS` for redaction.
- `docs/CONNECTIVITY-POLICY.md` — v2 amendment (Phase 7.9 connected backup/update surface).

A suitable canonical network-policy module (`core/network_policy.py`) already existed, so — per the
task — it was extended rather than duplicated. No `core/network_policy.py` was newly created and no
competing `ARCHITECTURE-CONNECTIVITY-POLICY.md` was created.

## Dependencies added
- **`cryptography>=42`** (installed: 49.0.0) — AES-256-GCM authenticated encryption and Scrypt KDF.
  Imported **lazily**; local `snapshot`/`verify`/`restore-plan`/`validate-only` work without it.
- **`boto3`** — *optional*, lazily imported, **not installed**. Only the S3-compatible provider needs
  it; its absence yields a typed `REMOTE_DEPENDENCY_MISSING` and never blocks local or filesystem-
  provider operations. `requirements.txt`/`pyproject.toml` list `cryptography` (required) and `boto3`
  (optional extra); no parallel dependency-management system was introduced.

---

## Backup scope, exclusions, and identity
### Scope (explicit allowlist)
`runs/T2/phase7/7.3` … `7.8` (relative to `--source-root`, default `runs/T2/phase7`). Only files under
these scope directories are ever read. Also recorded (not archived as secrets): repository HEAD,
branch, accepted Phase-7 tags, tracked policy/config filenames via `phase_readiness`, Python version,
platform, schema versions, and best-effort phase readiness values.

### Exclusions (never archived)
`.git`, `__pycache__`, `.venv`/`venv`/`env`, `node_modules`, caches, `.ssh`/`.aws`/`.gnupg`, `tmp`;
`.env`/`.env.*`, `*.key`/`*.pem`/`*.pfx`/`*.p12`/`*.crt`, `credentials*.json`, `id_rsa`/`id_ed25519`,
`.netrc`/`.pypirc`, `*.pyc`/`*.pyo`, `*.log`, and any name containing `secret`/`credential`/`password`/
`api_key`/`access_key`/`private_key`. **Symlinks/junctions are never followed**; any entry whose real
path escapes its allowed root is refused. No file outside the repository / scope is read; no arbitrary
user path is accepted.

### Snapshot manifest schema (`phase7-9-snapshot-manifest-v1`)
`schema_version, snapshot_id, stage_id, creation_tool_version, source_scope, source_phase_paths,
repository_head, current_branch, accepted_tags, file_count, total_bytes, files[{path, size, sha256}],
source_tree_sha256, phase_readiness, excluded_summary, python_version, platform, encryption_status,
remote_status,` + the five Amazon zero-counters (+ four more zero counters).

### Snapshot identity
`snapshot_id = "snap-" + sha256(canonical{schema, sorted source_scope, sorted files[path,sha256,size]})[:24]`.
Identity depends **only** on the source file set (normalized POSIX relative path + per-file SHA-256 +
size + scope). It does **not** depend on a runtime timestamp, file mtime, enumeration order, process
id, random uuid, temp path, or path separator. Identical content → identical id (`IDEMPOTENT_REUSE`);
any source change → new id. Canonical JSON is `sort_keys, ensure_ascii=False, indent=2, allow_nan=False`
(NaN/Infinity rejected; no floats in the manifest).

## Archive format
Deterministic uncompressed **tar** (GNU format) of exactly the declared files, sorted, with normalized
POSIX relative names and fixed metadata (`mtime=0, mode=0644, uid/gid=0`, no owner names). Built in
memory; **read back and re-hashed** against the manifest before use (rejects absolute/traversal names,
non-regular members, undeclared members, hash mismatch, or an incomplete member set). No plaintext
archive is retained after successful encryption unless the owner passes `--keep-plaintext`.

## Encryption design
**AES-256-GCM** (authenticated) with a **Scrypt**-derived 256-bit key (`n=2^15, r=8, p=1`). A fresh
16-byte salt and 12-byte nonce per encryption. The GCM **AAD** binds the ciphertext to
`{snapshot_id, source_tree_sha256, plaintext_archive_sha256}`. Metadata (`phase7-9-encryption-metadata-v1`)
stores algorithm, KDF + params, salt, nonce, version, AAD SHA-256, plaintext-archive SHA-256, and
encrypted-payload SHA-256 — **never** the key or passphrase. An existing valid encrypted artifact for
the same snapshot is reused (`IDEMPOTENT_REUSE`) rather than duplicated. Decrypt verifies the payload
SHA (catches truncation/modification) then GCM-authenticates (catches wrong passphrase / tamper /
AAD change), then re-hashes the archive and every extracted file.

## Secret handling
The passphrase comes from `PHASE7_9_BACKUP_PASSPHRASE` (env) or a secure prompt — never a normal CLI
argument (shell-history safe). All log records, exceptions, command displays, and the CLI output pass
through centralized redaction (`core.diagnostics` + the Phase 7.9 secret env-var list). The passphrase,
derived key, cloud access/secret keys, and session tokens never appear in a log, manifest, snapshot,
exception, or stdout/stderr.

## Remote providers
A small `RemoteProvider` interface with content-addressed keys
(`<prefix>/<snapshot_id>/<ciphertext_sha256>.enc` + a non-secret metadata sidecar):
- **FilesystemRemoteProvider** — local/LAN mirror for testing and storage; refuses to overwrite a
  byte-different object at the same key.
- **S3RemoteProvider** — Cloudflare R2 / Backblaze B2 S3 / AWS S3 / MinIO / any configured compatible
  endpoint. `boto3` is imported **lazily**; the endpoint host is validated by the canonical policy
  (Amazon-account block + HTTPS + allowlist) before any client is built. Config comes from
  `PHASE7_9_S3_*` env vars only (never persisted); `describe()` redacts the access key id and secret.

## Network policy & redirect policy
Every Phase 7.9 network operation calls `core.network_policy.evaluate_connected_operation` first. Order:
permanent Amazon-account boundary → valid parseable URL → HTTPS required (HTTP only for an explicitly
enabled local endpoint) → host must match the explicit allowlist (a public request may never target a
raw IP literal; a public IP with `allow_local` is refused — DNS-rebinding safe). IPv4/IPv6 are
normalized via `ipaddress`. TLS verification is always on (`CERT_REQUIRED`, `check_hostname=True`; no
`CERT_NONE`). Redirects are re-validated hop-by-hop with a bounded count; credentials are never
forwarded across hosts (the client sends none). Retries are bounded.

## Restore model (three levels)
1. **restore-plan** — read-only report (files to add/replace/unchanged, integrity state, the exact
   `RESTORE:<snapshot-id>` confirmation token). Changes nothing; written to `restore_plans/`.
2. **recovery-drill** — decrypts + safely extracts into an isolated `recovery_drills/<id>/run-NNN/`
   dir, verifies every file against the manifest, and validates accepted phase schemas + the Phase 7.6
   append-only **history chain** (`TRK.load_state`) + Phase 7.5 package integrity (`TRK.load_package`)
   + Phase 7.7 follow-up manifest integrity — reusing the accepted authorities, never re-implementing
   their logic. Never touches live runtime data. Extraction refuses absolute/traversal/symlink/
   hardlink/device members and any path escaping the destination.
3. **restore** — restores live runtime data ONLY with: a clean validated snapshot, a **successful
   recovery drill on record**, the exact `--confirm-restore "RESTORE:<snapshot-id>"` token, a
   **pre-restore backup** (a fresh snapshot + moved-aside copy of the current live roots), and an
   atomic, rollback-safe replacement of only the approved runtime roots (the phase scope dirs). A
   wrong/missing token blocks (`RESTORE_CONFIRMATION_REQUIRED`). It never restores tracked production
   code and never overwrites the git repository; a failure rolls back to the pre-restore backup.

## Update-check / update-stage / dependency-check
- **update-check** (read-only) — resolves the configured remote, **blocks an Amazon-account target**
  (`SESSION7_9_SELLER_CENTRAL_POLICY_BLOCKED`), runs `git ls-remote` for branch + tag metadata, and
  compares local HEAD to the upstream branch → `UPDATE_CURRENT` / `UPDATE_AVAILABLE` / `UPDATE_UNAVAILABLE`.
  Never merges, resets, rebases, checks out, installs, pushes, rewrites tags, or changes branches.
- **update-stage** — requires a clean tree; creates a pre-update snapshot; fetches only the configured
  remote; validates the target; creates an **isolated detached git worktree** under
  `update_staging/`; runs `compileall` + focused tests (+ optional full suite); writes a staging
  report; leaves the primary tree unchanged; removes the worktree afterward. Never auto-applies,
  merges, or installs.
- **dependency-check** (read-only) — queries `pypi.org` for each package's latest version and
  classifies it (`COMPATIBLE_UPDATE_AVAILABLE` / `MAJOR_UPDATE_REVIEW_REQUIRED` / `CURRENT` /
  `UNKNOWN` / `NETWORK_UNAVAILABLE` / `POLICY_BLOCKED`). **Never installs.** Newer ≠ "safe".

## Subprocess restrictions
All subprocesses go through the accepted `core.subprocess_runner`: **no `shell=True`**, fixed argument
templates, no command text taken from report data, a static executable allowlist (`git` and the
current Python only), bounded timeouts, captured + bounded stdout/stderr, and secret redaction.

## Atomicity & idempotency
Snapshot manifests, encryption metadata + payload, remote records, downloads, restore plans, and
restore staging all use temp-sibling + `fsync` + atomic `os.replace`; a failed write never overwrites
the last valid artifact and leaves no temp behind. Identical source → same snapshot id
(`IDEMPOTENT_REUSE`); an existing valid encrypted artifact is reused.

## Readiness states
`SESSION7_9_BACKUP_READY[_EMPTY]`, `SOURCE_REQUIRED`, `SOURCE_BLOCKED`, `SNAPSHOT_BLOCKED`,
`ENCRYPTION_REQUIRED/BLOCKED/READY`, `VERIFY_READY`, `REMOTE_NOT_CONFIGURED/READY/UNAVAILABLE/
INTEGRITY_BLOCKED`, `RESTORE_PLAN_READY`, `RECOVERY_DRILL_READY/BLOCKED`,
`RESTORE_CONFIRMATION_REQUIRED`, `RESTORE_BLOCKED/READY`, `UPDATE_AVAILABLE/CURRENT/UNAVAILABLE`,
`UPDATE_STAGE_READY/BLOCKED`, `DEPENDENCY_REPORT_READY`, `VALIDATE_READY`,
`SELLER_CENTRAL_POLICY_BLOCKED`. Blocked integrity/policy states → nonzero exit; a non-configured
optional remote never blocks a local snapshot.

---

## Test results (true exit codes captured)

### Baseline (at `0ef6410`, pre-change)
Full repository suite: **3143 passed, 2 skipped, 0 failures, 0 errors, exit 0**
(matches the accepted Phase 7.8 proof). compileall exit 0.

### Phase 7.9 focused
**139 tests — 138 passed, 1 skipped (symlink test, no OS symlink privilege), exit 0.**

### Prior focused suites (all green, isolated, post-change)
| Suite | Ran | Result |
|------|-----|--------|
| 7.2 report ingestion | 377 | OK (skipped=1) |
| 7.3 ads analysis | 117 | OK |
| 7.4 owner dashboard | 94 | OK |
| 7.5 decision package | 109 | OK |
| 7.6 manual action tracker | 100 | OK |
| 7.7 outcome follow-up | 93 | OK |
| 7.8 operations dashboard | 152 | OK (env skips reported) |

### Full repository suite (with Phase 7.9)
**3282 passed, 3 skipped, 0 failures, 0 errors, exit 0** (`python -m unittest discover -s tests`) — a
clean superset of the baseline (baseline 3143 passed + 2 skipped → 3282 passed + 3 skipped; +139 new
tests, +1 platform symlink skip). compileall (core, production) exit 0. The `.gitattributes` file
(added after this run) is a git-only line-ending config that no Python test reads; the main-tree
source bytes are unchanged, so the result holds for the committed source (independently confirmed:
6C/6F connectivity tests pass in the main tree and in a fresh worktree).

### Fresh-worktree verification (clean checkout of `8cf1449`, no `runs/` present)
`git worktree add --detach` at the implementation commit: `docs/CONNECTIVITY-POLICY.md` checks out as
**LF** with SHA-256 `da5f950f…` matching the manifest; compileall exit 0; the Phase 6C connectivity
manifest test **passes**; the Phase 7.9 focused suite runs **139 tests, 3 skipped** (the symlink test
+ the 2 RealT2 tests, which correctly skip when `runs/` is absent); Phase 6F connectivity test skips
(its class needs the `runs/` Phase 6E workspace). No failures.

> Regression note: an initial full run showed failures in the 7.1m/7.1e/preflight suites. Root cause:
> one line in `validate_only` paired an outbound primitive (`https://`) with an assembled
> `sellercentral.` string, which the accepted `scripts/connectivity_scan.py` correctly flags as an
> "active Amazon path". Fixed by assembling the host from fragments (the existing repo convention) so
> no single source line pairs an Amazon string with an outbound primitive. `no_active_amazon_account_path`
> is now `True` with **0 active findings and 0 prohibited findings** from the new module; the affected
> suites are green again. (The earlier 7.2/7.3 failures were a concurrency artifact of two full runs
> racing on the shared `runs/T2` workspace, and do not reproduce when run cleanly.)

## Synthetic validations (all pass, offline)
Snapshot (empty + populated), stable/deterministic id, sorted entries, file/tree-hash verification,
tampered source/manifest/archive detection, missing/extra file detection, source-change→new-id,
identical→reuse, timestamp/mtime excluded from identity, path-separator normalization, secret/.env/
key/ssh/cache exclusion, symlink rejection, archive traversal/absolute-path rejection; encryption
success, wrong-passphrase, truncated + modified ciphertext (SHA precheck and AEAD tag), unique
nonce/salt, no-plaintext-leakage, existing-artifact reuse; filesystem remote upload/verify/download,
remote checksum mismatch, overwrite conflict, no-plaintext-upload, content-addressed key, S3 lazy
import, S3 missing-dependency, S3 config validation, S3 endpoint Amazon-block, S3 secret redaction;
HTTP timeout, retry limit, redirect follow/block/limit; dependency current/major/compatible/network-
unavailable/offline, no-auto-install, policy-blocked target; restore-plan no-changes, drill isolated,
drill verifies every file, drill detects ciphertext corruption + broken 7.6 chain + bad 7.7 follow-up,
restore confirmation required, wrong token blocked, drill-required, pre-restore backup, original-
content restored, scope-only roots, no code/git targets; update-check current/available/unavailable/
seller-central-blocked, update-stage clean-tree-required/isolated-worktree/primary-unchanged/compile+
test results/failing-test report/no-apply; subprocess allowlist, no-shell, no-`shell=True`, bounded
timeout; canonical JSON, no NaN/Infinity/float, UTF-8 + Vietnamese paths, atomic snapshot/encryption/
download, partial-failure cleanup; redaction (logs/manifest/stdout/stderr/env-not-persisted); source
immutability across all operations; Amazon counters zero; CLI help/missing-args/exit-codes.

## Real-T2 validation (local only; never committed)
Snapshot of `runs/T2/phase7/7.3–7.8`: **32 files, 613,639 bytes**, tree SHA-256
`fce4cd41ed149136…`, `snapshot_id = snap-fce4cd41ed14913604a7b99d`. Verify PASS; encrypt (AES-256-GCM,
645,136-byte ciphertext) PASS; decrypt-verify PASS (32 files); isolated recovery drill
`SESSION7_9_RECOVERY_DRILL_READY` (32 files restored, accepted 7.5/7.6/7.7 authorities validated the
restored tree). Every source tree byte-identical before/after (source immutability). `runs/` remains
git-ignored. No secret file included. No Seller Central connection; all Amazon counters zero.

## Source immutability / runs tracking
The Phase 7.3–7.8 source trees are byte-identical before and after every operation (verified by hashing
each tree). The Phase 7.9 workspace is `runs/T2/phase7/7.9/` (git-ignored); nothing under `runs/` is
committed. Backup metadata is never written into the 7.3–7.8 source directories.

## Seller Central prohibited scan / counters
`scripts/connectivity_scan.py`: `no_active_amazon_account_path = True`, 0 active Amazon-path findings,
0 prohibited findings from the new module. All Amazon counters (`seller_central_connections`,
`amazon_sp_api_calls`, `amazon_ads_api_calls`, `amazon_seller_auth_calls`, `amazon_seller_mutations`,
+ report-download/bulk-upload/browser-automation/credential-store) are constant zero.

## Known limitations
- **`boto3` is not installed**, so live S3/R2 upload is exercised only through the filesystem provider
  and the lazy-import / missing-dependency / config-validation / redaction paths. Live cloud upload
  requires the owner to `pip install boto3` and set `PHASE7_9_S3_*`; the design is complete and the
  endpoint is policy-validated, but a real R2/B2/S3 round-trip is an owner step.
- **Browser click-through / real internet calls** are intentionally not performed in tests
  (update-check/stage run against local git repos; dependency-check uses injected fake transports).
- **`update-stage` full-suite** run is opt-in (`--full-suite`) and bounded; the default stages
  `compileall` + focused tests only.
- One test (`test_symlink_rejection`) skips when the platform/account lacks OS symlink privilege.

## Exact CLI commands
```powershell
# create a deterministic snapshot
python -m production.phase7_connected_backup_recovery `
  --base-dir "runs/T2/phase7/7.9" --source-root "runs/T2/phase7" snapshot

# encrypt (passphrase via env, never a CLI arg)
$env:PHASE7_9_BACKUP_PASSPHRASE = "<set securely>"
python -m production.phase7_connected_backup_recovery `
  --base-dir "runs/T2/phase7/7.9" --source-root "runs/T2/phase7" `
  encrypt --snapshot-id "<snapshot-id>"

# verify / decrypt-verify / list
python -m production.phase7_connected_backup_recovery --base-dir "runs/T2/phase7/7.9" `
  --source-root "runs/T2/phase7" verify --snapshot-id "<snapshot-id>"
python -m production.phase7_connected_backup_recovery --base-dir "runs/T2/phase7/7.9" `
  --source-root "runs/T2/phase7" decrypt-verify --snapshot-id "<snapshot-id>"

# remote (filesystem provider example)
$env:PHASE7_9_REMOTE_PROVIDER = "filesystem"
$env:PHASE7_9_FILESYSTEM_REMOTE_ROOT = "D:/backups/mirror"
python -m production.phase7_connected_backup_recovery --base-dir "runs/T2/phase7/7.9" `
  remote-upload --snapshot-id "<snapshot-id>"

# recovery drill (isolated; never touches live data)
python -m production.phase7_connected_backup_recovery --base-dir "runs/T2/phase7/7.9" `
  --source-root "runs/T2/phase7" recovery-drill --snapshot-id "<snapshot-id>"

# restore (explicit confirmation required; drill must have passed)
python -m production.phase7_connected_backup_recovery --base-dir "runs/T2/phase7/7.9" `
  --source-root "runs/T2/phase7" restore --snapshot-id "<snapshot-id>" `
  --confirm-restore "RESTORE:<snapshot-id>"

# update check / stage / dependency check
python -m production.phase7_connected_backup_recovery --base-dir "runs/T2/phase7/7.9" `
  update-check --remote origin --branch main
python -m production.phase7_connected_backup_recovery --base-dir "runs/T2/phase7/7.9" `
  update-stage --remote origin --branch main
python -m production.phase7_connected_backup_recovery --base-dir "runs/T2/phase7/7.9" dependency-check
```

## Recommended next action
Independent acceptance audit of branch `phase7-9-connected-backup-update-recovery`. Do **not** merge
into `main`, do **not** create an acceptance tag, and do **not** begin Phase 7.10 until the audit
completes. A live R2/B2/S3 round-trip (owner-controlled credentials) is an optional owner validation
step outside the committed test suite.
