# Session 7.9 — Connected Backup, Update & Recovery Manager — Independent Acceptance Audit

**Decision: `PHASE7_9_CONNECTED_BACKUP_UPDATE_RECOVERY_ACCEPTED`**

- **Auditor role:** independent Phase 7.9 acceptance auditor (evidence reproduced from repository bytes; the implementation report, proof gate, and all claimed hashes/counts were treated as untrusted and re-derived).
- **Repository:** `D:\Claude\Amazon\AMZ-FBM-Toolkit-v2_4_0-RC2\AMZ-FBM-Toolkit-v2_3_4-RC1`
- **Branch:** `phase7-9-connected-backup-update-recovery`
- **Baseline:** `0ef64106c2012b95de4bf2b6fb55dd5685dcee6b`
- **Implementation commit:** `8cf1449ae322c09b1f604d161f5ebde869c2d296`
- **Proof commit / feature HEAD:** `5ebb9339c243d3edfd6caab2ae9dd6be689f7dc0`
- **Python 3.12.10 · Windows 11 · cryptography 49.0.0 present · boto3 absent · runs/7.3–7.8 present**

---

## Summary of evidence

| Gate | Result |
|------|--------|
| compileall (core/production/tests) | exit 0 |
| Phase 7.9 focused | Ran 139, OK (skipped=1), exit 0 |
| Prior focused 7.2–7.8 | 377(1skip)/117/94/109/100/93/152, all exit 0 |
| Full repository suite | **Ran 3282, OK (skipped=3), 0 fail, 0 error, exit 0** |
| Independent network / Seller-Central harness | **61/61 pass**, exit 0 |
| Independent crypto/snapshot/archive/remote/restore/redaction harness | **91/91 pass**, exit 0 |
| Real-T2 snapshot/verify/encrypt/decrypt/remote/drill | exit 0, snapshot reproduced exactly |
| Fresh detached worktree @5ebb9339 | compileall 0; 7.9 OK (137/2skip); 7.8 OK (152/2skip) |
| Connectivity scanner (`scripts/connectivity_scan.py`) | 90 files, **0 active amazon-account paths** |
| Seller Central counters (every result) | constant zero |

No blocking defect found. Four minor, non-blocking documentation/cosmetic observations are listed in §45.

---

## Numbered findings

### 1. Git provenance — PASS
- Current branch `phase7-9-connected-backup-update-recovery`; working tree clean before and after the audit (`git status --porcelain` empty).
- Local HEAD = remote HEAD = `5ebb9339`. `main` = `origin/main` = `0ef64106`.
- Checkpoint tag `phase7-9-connected-backup-update-recovery-checkpoint-0ef6410` → `0ef64106`.
- Implementation commit `8cf1449` parent = `0ef64106` (baseline). Proof commit `5ebb933` parent = `8cf1449`.
- All seven prior accepted tags resolve to their named commits (`d5ad841`, `7005275`, `eebecc5`, `66d972d`, `f1d11d8`, `581ae49`, `80333ec`) — unchanged, unmoved.
- No Phase 7.9 acceptance tag exists (only the checkpoint tag).
- Reflog shows the in-progress implementation commit was amended (`cf6ede7`→`8cf1449`) **before** the proof commit was created; no published, baseline, or accepted commit/tag was rewritten, and remote HEAD equals local HEAD.

### 2. Implementation diff (`0ef6410..8cf1449`) — PASS
9 files, +3646/−2. Exactly: `production/phase7_connected_backup_recovery.py` (new, 2048 lines), `tests/test_phase7_9_connected_backup_recovery.py` (new), `core/network_policy.py` (+216, additive), `core/diagnostics.py` (+18, additive), `docs/CONNECTIVITY-POLICY.md` (+48 v2 amendment), `docs/CONNECTIVITY-POLICY-MANIFEST.json` (version/hash update in place), `requirements.txt` (+4), `pyproject.toml` (+7), `.gitattributes` (new, 5 lines). `core/network_policy.py` and `core/diagnostics.py` changes are **purely additive** (0 deletions). No accepted Phase 7.2–7.8 production or test authority appears in the diff.

### 3. Proof diff (`8cf1449..5ebb933`) — PASS
2 files only: the implementation report (+346) and the proof gate JSON (+266). No code, test, or policy change. All six committed blob SHA-1s claimed in the proof gate match `git rev-parse HEAD:<file>` exactly.

### 4. Connectivity policy — PASS
Single canonical document `docs/CONNECTIVITY-POLICY.md` updated in place with a labelled "v2 amendment" section; no competing policy file created. Manifest `policy_sha256 = da5f950f…600fb9` matches the SHA-256 of both the git blob (LF) and the working-tree bytes; the prior v1 hash is preserved as `policy_sha256_v1_history`. The permanent Amazon boundary is unchanged and stated first. Accepted Phase 7.2–7.8 offline behavior is not weakened (the new validator is a separate additive code path that does not touch `evaluate_network_request` or the connectivity mode).

### 5. Seller Central deny policy — PASS
`evaluate_connected_operation` evaluates the permanent Amazon-account boundary (`classify_destination` + `_is_amazon_host`) as **step 1, before** the allowlist (step 5). Independently blocked (harness), even when the apparent host is explicitly allowlisted: `sellercentral.amazon.com`, `/ap/signin`, `sellercentral-europe.amazon.com`, `sellercentral.amazon.co.uk`, mixed-case, trailing-dot, `sellingpartnerapi-na.amazon.com` (SP-API), `advertising-api.amazon.com` (Ads API), `mws.amazonservices.com`, `amazon.com/ap/signin` (seller OAuth), alt port `:8443`, deceptive suffix `sellercentral.amazon.com.example.org`, and the **reverse-userinfo** case `https://github.com@sellercentral.amazon.com/` (real host is Amazon → blocked despite `github.com` allowlist). Typed reason codes returned (`SELLER_CENTRAL_ACCESS_PROHIBITED` / `AMAZON_API_PROHIBITED` / `AMAZON_ACCOUNT_ACCESS_PROHIBITED`). `assert_connected_operation_allowed` raises `ConnectedNetworkDenied` before any socket. An S3 endpoint configured as an Amazon host is refused at provider construction.

### 6. General network policy — PASS
Allowlist is exact-or-dotted-subdomain, never substring: `github.com`/`api.github.com` allowed; `evil-github.com` and `github.com.evil.org` refused (`HOST_NOT_ALLOWLISTED`). HTTPS required for public hosts (`INSECURE_SCHEME_BLOCKED` for `http://github.com`); plain HTTP only for an explicitly enabled loopback/private endpoint. Public IPv4/IPv6 literals refused; a public IP with `allow_local` set is still refused (`LOCAL_ENDPOINT_NOT_ENABLED`). IPv4/IPv6 normalization verified (`[::1]`→`::1`, expanded→compressed, DNS lower-cased + trailing dot stripped). Unknown purpose refused. `_real_transport` pins `ssl.create_default_context()` with `check_hostname=True`, `verify_mode=CERT_REQUIRED` — no certificate-bypass path exists. Redirects bounded (`max_redirects=3`), retries bounded (`max_retries=2`), response bounded (`max_bytes=2 MiB`). No production test contacts a real internet service (git uses local repos, dependency-check uses injected fake transports).

### 7. Backup scope — PASS
Only the allowlisted `runs/T2/phase7/7.3…7.8` roots are walked. Symlinks/junctions never followed; any entry whose realpath escapes its root is refused; per-file 256 MiB guard; POSIX-normalized relative paths; deterministic sort. Arbitrary absolute paths cannot enter the archive (traversal/absolute names raise `SOURCE_PATH_ESCAPE`/`ARCHIVE_UNSAFE_NAME`).

### 8. Secret exclusions — PASS
Excluded dirs (`.git`, `__pycache__`, `.venv`, `.ssh`, `.aws`, `.gnupg`, `node_modules`, …), file names (`.env`, `id_rsa`, `id_ed25519`, `credentials.json`, `cookies.*`, `.netrc`, `.pypirc`, `token.json`, `secrets.json`), suffixes (`.key`, `.pem`, `.pfx`, `.p12`, `.crt`, `.pyc`, `.log`, `.env`), and substrings (`secret`, `credential`, `password`, `api_key`, `private_key`, …). Real-T2 test `test_real_t2_no_secret_file_included` confirms none reach the manifest.

### 9. Symlink and junction handling — PASS
`_within` (realpath + `commonpath`) rejects escaping links at scope-root, sub-dir, and file level; `os.walk(followlinks=False)`. Archive build and `_safe_extract` both refuse symlink/hardlink/device/non-regular members. The dedicated symlink test skips only where the OS denies symlink creation (Windows without privilege).

### 10. Snapshot determinism — PASS (harness)
`snapshot_id = "snap-" + sha256(canonical{scope, sorted files[path,sha256,size]})[:24]`. Identical content under different mtimes, creation order, and temp directories → identical id and tree hash. Changed / added / removed / renamed file → new id. Files sorted; sizes exact; per-file SHA-256 correct. Identity independent of timestamp/mtime/order/pid/tmp/separator.

### 11. Manifest integrity — PASS
`canonical_json` uses `sort_keys`, `allow_nan=False` (NaN/Infinity rejected — verified). `_snapshot_valid` recomputes the tree hash and id from the manifest's own file list and re-checks the constant Amazon counters; a tampered id/tree/counter is rejected on load (`SNAPSHOT_MANIFEST_TAMPERED`). `_read_json` rejects NUL bytes and non-finite JSON constants.

### 12. Archive format — PASS
Deterministic uncompressed GNU tar: sorted members, normalized POSIX relative names, fixed metadata (`mtime=0, mode=0644, uid/gid=0`, no owner names), no absolute/`../`/backslash names, no symlink/device members. Byte-identical across two independent builds of the same snapshot. Read-back verification re-hashes every member against the manifest before returning; undeclared/incomplete members raise.

### 13. Archive extraction safety — PASS
`_safe_extract` independently refuses absolute, `../`, backslash, symlink, hardlink, device, and non-regular members, and refuses any member whose realpath escapes the destination (`commonpath` check). Crafted malicious tars (traversal, absolute, symlink) were all rejected.

### 14. Cryptography — PASS (harness)
AES-256-GCM with a scrypt-derived 32-byte key (`n=32768, r=8, p=1`). Fresh 16-byte salt and 12-byte nonce per encryption (`os.urandom`). AAD binds `{schema, snapshot_id, source_tree_sha256, plaintext_archive_sha256}`; the derived key is `del`-ed after use; no key or passphrase is ever stored or serialized. Valid encrypt→decrypt round-trip. Rejected: wrong passphrase (`DECRYPT_FAILED`), empty/missing passphrase (`BACKUP_PASSPHRASE_REQUIRED`), truncated ciphertext, single-byte-modified ciphertext, modified nonce, modified salt, modified AAD-bound field, and a cross-snapshot ciphertext substitution. Unique salt+nonce across distinct snapshots; a valid existing artifact is verified and reused (not re-encrypted) for the same snapshot. Encryption is authenticated (GCM) with a pre-decrypt ciphertext-hash gate; no unauthenticated path exists.

### 15. Passphrase handling — PASS
Source is `PHASE7_9_BACKUP_PASSPHRASE` env var or an explicit argument (secure-prompt result); there is **no** CLI passphrase flag (argparse exposes none). Missing/empty → typed `BACKUP_PASSPHRASE_REQUIRED`.

### 16. Secret redaction — PASS
`diagnostics.redact_secrets` redacts (a) the actual **values** of `SECRET_ENV_VARS` — which now include `PHASE7_9_BACKUP_PASSPHRASE`, `PHASE7_9_S3_ACCESS_KEY_ID`, `PHASE7_9_S3_SECRET_ACCESS_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SESSION_TOKEN` — wherever they appear, and (b) key-shaped token patterns (`sk-*`, `Bearer …`). Verified: a passphrase and S3 secret set in the environment are redacted by value from a synthetic error line and from a `BackupError` detail. `S3Config.describe()` emits `***REDACTED***` for both keys and never the endpoint's userinfo. No 7.9 sink emits a full URL — only `endpoint_host()` (hostname) and `urlsplit(url).hostname` are logged/reported, so a `user:pass@host` git remote cannot leak (see §45 note).

### 17. Dependency declarations — PASS
`cryptography>=42.0` declared in both `requirements.txt` and `pyproject.toml [project.dependencies]` (consistent). `boto3` is an optional extra: `[project.optional-dependencies] s3 = ["boto3>=1.28"]` in pyproject and a commented optional line in requirements. No parallel/auto dependency system introduced. See §45 note on the broad lower bound.

### 18. Filesystem provider — PASS (harness)
Content-addressed keys (payload key embeds the ciphertext SHA-256). Atomic write; refuses to overwrite a byte-different object at the same key (`REMOTE_OVERWRITE_CONFLICT`); idempotent reuse of identical bytes; key traversal refused (`REMOTE_KEY_UNSAFE`); stat/get/download verify size + checksum; `remote-verify` detects post-upload tamper. Only ciphertext + non-secret metadata are stored — no plaintext on the remote.

### 19. S3-compatible provider — PASS (harness)
`boto3` imported lazily (module import and provider construction succeed without it); a missing `boto3` yields typed `REMOTE_DEPENDENCY_MISSING`, not a crash. Endpoint validated by the canonical network policy at construction (Amazon host refused). Config read from env only, never persisted; `describe()` redacts both keys; TLS on via boto3 default; content-addressed, no-silent-overwrite semantics identical to the filesystem provider. Live S3 credentials not required; verified with the absent-boto3 path and synthetic config.

### 20. Remote integrity — PASS
Upload verifies remote size + checksum via `stat`, then optionally re-downloads and re-hashes; a mismatch raises `REMOTE_CHECKSUM_MISMATCH`/`REMOTE_REDOWNLOAD_MISMATCH`. `remote-verify` and `remote-download` re-check against the recorded content hash; a truncated/tampered object is rejected. No object is reported verified on upload success alone.

### 21. Restore plan — PASS (harness)
Read-only: lists add/replace/unchanged/extra, shows source hashes, the exact confirmation token `RESTORE:<id>`, and the required-drill flag; writes only to `restore_plans/`; changes nothing (source hash identical before/after); never restores tracked code or the git repo.

### 22. Recovery drill — PASS (harness + Real-T2)
Restores into an isolated `recovery_drills/<id>/run-NNN/restored/` directory, decrypts, safely extracts, verifies every member against the manifest, and validates the restored tree by reusing the **accepted** Phase 7.5 (`load_package`), 7.6 (`load_state` history chain) and 7.7 (follow-up manifest re-hash) authorities. A synthetic corrupted 7.7 follow-up (manifest hash ≠ file) is detected (`schema_validation_ok=false`). Never touches live data (source hash unchanged). Real-T2 drill on the live 32-file tree: `RECOVERY_DRILL_READY`, 0 file failures, schema valid.

### 23. Live-restore confirmation — PASS
`restore` requires the exact token `RESTORE:<id>` (wrong/missing → `RESTORE_CONFIRMATION_REQUIRED`, no change) **and** a successful recovery-drill on record (`RECOVERY_DRILL_REQUIRED` otherwise) **and** creates a pre-restore snapshot + backup of the current live roots before swapping. Only the approved runtime roots are replaced; tracked code and the git repo are never restore targets.

### 24. Live-restore failure safety — PASS (code review)
Staged tree is extracted to a sibling `.restore_staging/` and every file re-verified before any live root is moved. Per-phase swap moves the live root into `pre_restore_backups/…` then moves in the staged root; on any exception each already-restored phase is rolled back from the pre-restore backup and a `RESTORE_FAILED_ROLLED_BACK` is raised. An interrupted multi-root restore is recoverable because the original roots survive under `pre_restore_backups/`. A post-restore `verify_snapshot` gates the success readiness.

### 25. Update check — PASS
Read-only: resolves the configured remote, blocks an Amazon-account target first (`SESSION7_9_SELLER_CENTRAL_POLICY_BLOCKED`), reads `git ls-remote` metadata, and compares local HEAD to the upstream branch (`UPDATE_CURRENT`/`UPDATE_AVAILABLE`/`UPDATE_UNAVAILABLE`). Never merges/resets/rebases/checks-out/installs/pushes/moves-tags/changes-branch. Focused tests for current/available/unavailable/seller-central-blocked pass.

### 26. Update staging — PASS
Requires a clean primary tree; creates a pre-update snapshot; validates the remote target (Amazon-block); fetches only the configured remote; creates an **isolated detached worktree** under Phase 7.9 at a concrete resolved commit; runs `compileall` + focused tests (+ optional full suite); writes a staging report; removes the worktree in a `finally`; confirms the primary tree is unchanged; never applies/merges/installs. Focused tests confirm the primary tree and HEAD are unchanged and no apply occurs; malicious ref/command-like names are passed as argv (no shell).

### 27. Subprocess restrictions — PASS
No `shell=True`, `os.system`, `os.popen`, or direct `Popen` in the module; it delegates to `core.subprocess_runner.run_subprocess`, which rejects string commands, requires an explicit positive timeout, uses list args, tree-kills on timeout, and bounds+redacts output. Static executable allowlist = `git` + the current Python interpreter; `_run_static_validation` rejects any other executable or a shell string; fixed argument templates; no command text derived from report data.

### 28. Dependency check — PASS
Read-only PyPI metadata via the policy-checked `http_get_json` (allowlist `pypi.org`, TLS, bounded); classifies `COMPATIBLE_UPDATE_AVAILABLE` / `MAJOR_UPDATE_REVIEW_REQUIRED` / `CURRENT` / `UNKNOWN` / `NETWORK_UNAVAILABLE` / `POLICY_BLOCKED`; never installs, never rewrites requirements, never invokes pip; a network/policy failure is reported per-package, never raised; package/version data cannot cause code execution (deterministic integer version parsing). `--offline` forces `NETWORK_UNAVAILABLE`.

### 29. Atomicity — PASS
`_atomic_write_bytes` writes a temp sibling, `fsync`s, then `os.replace`; a failed write never replaces the last valid artifact, and the temp is cleaned in `finally`. Content-addressed IDs mean different bytes never overwrite the same id.

### 30. Idempotency — PASS
Identical source → identical snapshot id + `IDEMPOTENT_REUSE`; an existing valid encrypted artifact is verified and reused; remote put of identical content reports `reused`.

### 31. Validate-only — PASS (harness)
`validate-only` runs source-enumeration, Amazon-counter, and Seller-Central-block checks and (optionally) a named-snapshot recompute; writes **no** files (`files_written=0`, no workspace directory created); no network or subprocess call; returns `VALIDATE_READY`/exit 0 for valid input and a blocked readiness/nonzero otherwise.

### 32. Source immutability — PASS (harness + Real-T2)
The full `runs/T2/phase7/7.3…7.8` tree fingerprint (every file, including excluded) is byte-identical before and after validate/snapshot/verify/encrypt/decrypt/remote-upload-verify-download/restore-plan/recovery-drill. No Phase 7.9 manifest, metadata, lock, temp, or log file appears in any source phase directory; the workspace lived entirely in a scratch directory during the audit and nothing was written under `runs/`.

### 33. Real-T2 snapshot — PASS (reproduced exactly)
`snapshot_id = snap-fce4cd41ed14913604a7b99d`, `file_count = 32`, `total_bytes = 613639`, `source_tree_sha256 = fce4cd41ed14913604a7b99da7661decf1f238dd1e9c4b84c5b45f6884c845f0` — **identical** to the proof-gate claim (no drift). Deterministic reuse on a second run; verify OK (0 missing/mismatched/extra).

### 34. Real-T2 encryption — PASS
AES-256-GCM + scrypt; `ciphertext_bytes = 645136` (matches proof gate); `encrypted_payload_sha256 = 7038816f9474f672d1c8d76d3183574c20ae2bd4337903bb9a76535aca4d2c0a`; decrypt-verify OK (32 files). Filesystem remote upload (redownload-verified, content-addressed key), verify, and download all OK; remote holds only ciphertext.

### 35. Real-T2 recovery drill — PASS
`SESSION7_9_RECOVERY_DRILL_READY`, 32 files restored, 0 file failures, schema validation OK (exercises the real accepted 7.5/7.6/7.7 validators against live data). Live data untouched.

### 36. Seller Central counters — PASS
Every result dict (snapshot/verify/encrypt/decrypt/upload/verify/download/restore-plan/drill) carries the nine Amazon counters constant zero; `_snapshot_valid` rejects any manifest whose counters are non-zero; no code path increments them.

### 37. Compile result — PASS
`python -m compileall -q core production tests` → exit 0 (primary and fresh worktree).

### 38. Phase 7.9 focused tests — PASS
`Ran 139, OK (skipped=1), exit 0` (138 passed + 1 symlink-privilege skip). Matches the claimed 139/138/1.

### 39. Prior focused tests — PASS
7.2 = 377 (1 skip), 7.3 = 117, 7.4 = 94, 7.5 = 109, 7.6 = 100, 7.7 = 93, 7.8 = 152 — all `OK`, exit 0. Matches claims.

### 40. Full suite — PASS
`Ran 3282 tests, OK (skipped=3), 0 failures, 0 errors, exit 0` (true Python exit code captured, no pipeline masking). Matches the claimed 3282/3/0.

### 41. Independent harnesses — PASS
Network / Seller-Central deny: **61/61**, exit 0. Crypto/snapshot/archive/remote/restore/redaction: **91/91**, exit 0. Real-T2 audit: exit 0. Connectivity scanner: 0 active amazon-account paths.

### 42. Fresh worktree — PASS
Detached worktree @`5ebb9339`: `runs/` absent; `docs/CONNECTIVITY-POLICY.md` checks out as **LF** (no CRLF) with SHA-256 `da5f950f…` matching the manifest (the `.gitattributes eol=lf` pin holds across a fresh checkout); `cryptography>=42.0` and `boto3>=1.28` present; compileall exit 0; 7.9 focused `OK (137 ran, 2 skipped)` — only environment skips (`RealT2` no runs/, symlink no privilege); 7.8 focused `OK (152 ran, 2 skipped)`. No dependency on untracked runtime data. Worktree removed afterward; primary workspace untouched (no `git clean`).

### 43. runs/ tracking — PASS
`git ls-files runs/` → 0 files; `runs/…` is git-ignored. No runtime data is tracked; the Real-T2 snapshot/ciphertext are not committed.

### 44. Documentation accuracy — PASS
Implementation report and proof gate accurately describe the connectivity change, permanent Seller Central boundary, changed files (verified blob SHA-1s), added dependency + optional boto3, snapshot identity (reproduced), archive format, encryption design + KDF params, provider model, allowlist, redirect policy, secret handling, restore model, update-check/stage, dependency-check, subprocess restrictions, atomicity, idempotency, primary test counts (3282/3 and 139/1 reproduced exactly), Real-T2 snapshot, source immutability, and known limitations. Four minor cosmetic items (§45) do not misrepresent any material or security property.

### 45. Known limitations & non-blocking observations
Accurately disclosed by the implementer: boto3 not installed (live S3/R2 round-trip is an owner step; lazy-import/missing-dep/config/redaction paths exercised); no real internet calls in tests; update-stage full-suite is opt-in; symlink test needs OS privilege. **Auditor observations (all non-blocking, none security-relevant):**
1. `docs/CONNECTIVITY-POLICY.md` H1 still reads "(v1)" while the manifest declares `connectivity-policy-v2`; the doc contains a clearly-labelled "v2 amendment" section, so this is cosmetic.
2. Proof gate `fresh_worktree_result` states "ran 139, skipped 3"; the actual fresh run is "Ran 137, skipped 2" — a Python-3.12 unittest counting nuance (a `setUpClass`-level skip of the 2 RealT2 methods reports as one skip and is not counted in "Ran"). Same substance, 0 failures.
3. `cryptography>=42.0` is a broad lower bound; a pinned/capped range would aid reproducible deployment. Runtime behavior is safe (audit permits a documentation recommendation).
4. `diagnostics` defines `REDIRECT_TARGET_BLOCKED` / `CREDENTIAL_FORWARDING_BLOCKED` constants that are not emitted (blocked redirects carry the underlying reason such as `HOST_NOT_ALLOWLISTED` or the Amazon reason); unused-constant only.

### 46. Final decision
**`PHASE7_9_CONNECTED_BACKUP_UPDATE_RECOVERY_ACCEPTED`.** No cryptographic, restore, network-policy, or Seller Central boundary defect. Seller Central is unreachable and denied before any allowlist (including reverse-userinfo, deceptive-suffix, subdomain, SP-API/Ads-API, alt-port, mixed-case, trailing-dot); redirects cannot bypass policy and credentials never cross hosts; only AES-256-GCM ciphertext is uploaded; secrets are redacted; archive extraction cannot escape; encryption authentication cannot be bypassed; restore cannot silently leave a partial live state; update-stage cannot mutate the primary tree; no arbitrary command execution; Phase 7.3–7.8 source is byte-identical; the full suite and fresh worktree pass. The four observations in §45 are documentation/cosmetic only and are recorded, not fixed, for a clean acceptance (no production code modified).

### 47. Exact next action
Owner may proceed to review the accepted branch. Do **not** merge into `main`, do **not** create any further Phase 7.9 tag, and do **not** begin Phase 7.10 until explicitly authorized. Optional owner step: a real cloud round-trip (`pip install boto3` + `PHASE7_9_S3_*`) and a browser-free live restore rehearsal on non-critical data.
