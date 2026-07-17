#!/usr/bin/env python3
"""Current-user application directories + atomic JSON IO (Session 5C / Part B).

One small shared authority for *where* local runtime state lives and *how* it is
written. It is deliberately not a policy/health/process authority (those already
exist in Session 5B) — it only resolves per-user paths and writes JSON atomically.

Root (Windows, the owner's platform):
    %LOCALAPPDATA%\\AMZ-FBM-Toolkit
Override for tests / portable use:
    set AMZ_FBM_HOME=<dir>

Subdirectories (never inside the installed package):
    runtime\\   runtime metadata + generated launcher wrappers (bin\\)
    logs\\      bounded operational logs
    config\\    local runtime configuration
    backups\\   timestamped backups of replaced local config
    data\\      reserved for owner data the launcher itself creates

Business data (runs/, product facts, keyword exports, listing packages, reports)
lives in the repository and is NEVER placed or deleted here.
"""
import json
import os
import tempfile

APP_DIR_NAME = "AMZ-FBM-Toolkit"
SUBDIRS = ("runtime", "logs", "config", "backups", "data")
HOME_ENV = "AMZ_FBM_HOME"


def base_dir(env=None):
    """Resolve the current-user application root. Never requires admin."""
    env = os.environ if env is None else env
    override = env.get(HOME_ENV)
    if override and override.strip():
        return os.path.abspath(override.strip())
    local = env.get("LOCALAPPDATA") or env.get("APPDATA")
    if not local:
        # Non-Windows / unusual environments: fall back under the user profile.
        local = os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(local, APP_DIR_NAME)


def subdir(name, env=None):
    return os.path.join(base_dir(env), name)


def runtime_dir(env=None):
    return subdir("runtime", env)


def logs_dir(env=None):
    return subdir("logs", env)


def config_dir(env=None):
    return subdir("config", env)


def backups_dir(env=None):
    return subdir("backups", env)


def data_dir(env=None):
    return subdir("data", env)


def bin_dir(env=None):
    """Generated launcher wrappers live under runtime\\bin (never the package)."""
    return os.path.join(runtime_dir(env), "bin")


def instance_metadata_path(env=None):
    return os.path.join(runtime_dir(env), "instance.json")


def manifest_path(env=None):
    return os.path.join(base_dir(env), "install-manifest.json")


def local_config_path(env=None):
    return os.path.join(config_dir(env), "local-config.json")


def all_dirs(env=None):
    root = base_dir(env)
    return [root] + [os.path.join(root, s) for s in SUBDIRS] + [bin_dir(env)]


def ensure_dirs(env=None):
    """Create the current-user directory tree (idempotent). Returns the dir list."""
    dirs = all_dirs(env)
    for d in dirs:
        os.makedirs(d, exist_ok=True)
    return dirs


# ---- atomic, deterministic JSON IO -------------------------------------------
def write_json_atomic(path, obj):
    """Write *obj* as UTF-8 JSON via a temp file + os.replace (atomic on Windows).

    Deterministic key order and indentation so manifests/metadata diff cleanly.
    A crash mid-write leaves either the old file or the new one, never a partial.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    text = json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return path


# ---- bounded, local, secret-free operational log (Part L) --------------------
_LOG_MAX_BYTES = 1_000_000   # 1 MB, then a single-generation rotation to name.1


def append_log(name, record, env=None, max_bytes=_LOG_MAX_BYTES):
    """Append one JSON record to logs\\<name> with simple size rotation.

    Local only, UTF-8, one object per line. The caller is responsible for passing
    a secret-free ``record`` (values are redacted upstream via diagnostics). Never
    raises to the caller — a logging failure must not break a lifecycle action.
    """
    try:
        d = logs_dir(env)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        if os.path.exists(path) and os.path.getsize(path) >= max_bytes:
            os.replace(path, path + ".1")
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


class CorruptJsonError(ValueError):
    """A file that exists but does not parse as a JSON object."""


def read_json(path):
    """Read a JSON object.

    Returns None if the file is missing (an expected/optional state). Raises
    CorruptJsonError if the file exists but is malformed — the caller decides
    whether that is stale residue to recover or a hard error.
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, OSError) as e:
        raise CorruptJsonError(f"{path}: {e}") from e
    if not isinstance(data, dict):
        raise CorruptJsonError(f"{path}: expected a JSON object")
    return data
