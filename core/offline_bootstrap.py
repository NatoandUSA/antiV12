#!/usr/bin/env python3
"""Setuptools-free, no-network offline source bootstrap (Session 5D.1 / Part C).

A minimal, standard-library-only way to make ``amz_fbm`` importable and the
``amz-fbm`` command resolvable inside a fresh Python environment when the PEP 517
build backend (setuptools) is absent and nothing may be downloaded. It does NOT
duplicate the local-install authority (``local_install.py``) — that prepares the
per-user runtime tree; this only *registers the source* into a target interpreter.

It requires none of: setuptools, wheel, build, hatch, poetry, pip network access,
or administrator rights. It works by:

    1. locating the target interpreter's writable ``site-packages`` (purelib);
    2. writing a toolkit-owned ``.pth`` that adds the repository source root to
       ``sys.path`` (so ``import amz_fbm`` resolves with no build step); and
    3. writing a toolkit-owned ``amz-fbm.cmd`` wrapper in the target ``Scripts``
       directory that runs ``"<target-python>" -m amz_fbm %*`` (spaces-safe).

Every write is atomic, every file carries an ownership marker (so removal never
touches a file the toolkit did not create), and nothing here opens a socket.
"""
import hashlib
import json
import os
import subprocess
import sys
import sysconfig
import tempfile

MODE_OFFLINE_SOURCE_BOOTSTRAP = "OFFLINE_SOURCE_BOOTSTRAP"
MODE_PEP517_EDITABLE = "PEP517_EDITABLE"

PTH_NAME = "amz_fbm_toolkit_source.pth"
WRAPPER_NAME = "amz-fbm.cmd"

# Ownership marker written into both files. Removal/overwrite is refused unless the
# existing file carries this marker, so an unrelated .pth/.cmd is never deleted.
OWNER_MARKER = "AMZ-FBM-TOOLKIT-OFFLINE-BOOTSTRAP"

# Files that must exist under the source root for it to be the real toolkit source.
_SOURCE_SENTINELS = (os.path.join("amz_fbm", "__init__.py"),
                     os.path.join("amz_fbm", "cli.py"),
                     "pyproject.toml")

_IS_WINDOWS = os.name == "nt"
_SUBPROCESS_TIMEOUT = 30


class OfflineBootstrapResult:
    """Structured, path-sanitizable outcome of a bootstrap / removal call."""

    def __init__(self, ok, mode=None, **kw):
        self.ok = bool(ok)
        self.mode = mode
        self.source_root_display = kw.get("source_root_display")
        self.python_display = kw.get("python_display")
        self.site_packages_display = kw.get("site_packages_display")
        self.pth_path_display = kw.get("pth_path_display")
        self.wrapper_path_display = kw.get("wrapper_path_display")
        self.files_created = kw.get("files_created", [])
        self.files_updated = kw.get("files_updated", [])
        self.files_removed = kw.get("files_removed", [])
        self.verification_results = kw.get("verification_results", [])
        self.warnings = kw.get("warnings", [])
        self.errors = kw.get("errors", [])
        self.content_sha256 = kw.get("content_sha256")

    def to_dict(self):
        return {
            "ok": self.ok,
            "mode": self.mode,
            "source_root_display": self.source_root_display,
            "python_display": self.python_display,
            "site_packages_display": self.site_packages_display,
            "pth_path_display": self.pth_path_display,
            "wrapper_path_display": self.wrapper_path_display,
            "files_created": list(self.files_created),
            "files_updated": list(self.files_updated),
            "files_removed": list(self.files_removed),
            "verification_results": list(self.verification_results),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "content_sha256": self.content_sha256,
        }


# ---- validation helpers ------------------------------------------------------
def _has_control_chars(text):
    """True if *text* contains a newline, NUL, or other C0 control character.

    A source/executable path with an embedded control character is rejected — it is
    never legitimate and could inject extra lines into a .pth or wrapper.
    """
    return any(ord(ch) < 0x20 for ch in text)


def _is_toolkit_source(source_root):
    return all(os.path.exists(os.path.join(source_root, rel)) for rel in _SOURCE_SENTINELS)


def _validate_source(source_root):
    """Return a list of blocking errors for *source_root* (empty when valid)."""
    errors = []
    if not source_root or not str(source_root).strip():
        return ["source root is empty"]
    if _has_control_chars(str(source_root)):
        return ["source root contains a control/newline character"]
    if not os.path.isdir(source_root):
        errors.append("source root does not exist: not a directory")
        return errors
    if not _is_toolkit_source(source_root):
        errors.append("source root is not the AMZ FBM toolkit "
                      "(missing amz_fbm package / pyproject.toml)")
    return errors


def _target_paths(python_executable):
    """Resolve (purelib, scripts, executable) for the target interpreter.

    Uses this interpreter's ``sysconfig`` directly when no distinct executable is
    requested; otherwise queries the target interpreter in a bounded subprocess
    (stdlib only, no network). Returns a dict or raises RuntimeError.
    """
    same = (python_executable is None
            or os.path.abspath(python_executable) == os.path.abspath(sys.executable))
    if same:
        paths = sysconfig.get_paths()
        return {"purelib": paths["purelib"], "scripts": paths["scripts"],
                "executable": os.path.abspath(sys.executable)}
    exe = os.path.abspath(python_executable)
    if not os.path.exists(exe):
        raise RuntimeError("target python executable does not exist")
    code = ("import json, sys, sysconfig; p = sysconfig.get_paths(); "
            "print(json.dumps({'purelib': p['purelib'], 'scripts': p['scripts'], "
            "'executable': sys.executable}))")
    try:
        out = subprocess.run([exe, "-c", code], capture_output=True, text=True,
                             timeout=_SUBPROCESS_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError("could not query target interpreter: %s" % type(e).__name__)
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError("target interpreter did not report its paths")
    data = json.loads(out.stdout.strip())
    return {"purelib": data["purelib"], "scripts": data["scripts"],
            "executable": os.path.abspath(data["executable"])}


def _atomic_write(path, text):
    """Write *text* to *path* via a temp file + os.replace (atomic on Windows)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-bootstrap-",
                              dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
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


def _read(path):
    # newline="" preserves CRLF exactly so the idempotency comparison against the
    # intended (CRLF) wrapper content is byte-accurate — not translated to LF.
    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            return f.read()
    except OSError:
        return None


def _is_owned(path):
    """True if the file exists and carries the toolkit ownership marker."""
    text = _read(path)
    return bool(text) and OWNER_MARKER in text


# ---- content builders --------------------------------------------------------
def _pth_content(source_root):
    # site.py skips lines starting with '#', so the marker is an inert comment and
    # the source root is added to sys.path. No build backend is involved.
    return "# %s do-not-edit\n%s\n" % (OWNER_MARKER, source_root)


def _wrapper_content(exe):
    return (
        "@echo off\r\n"
        "REM %s do-not-edit\r\n" % OWNER_MARKER +
        "REM Local, offline, loopback-only launcher. Contains no secrets.\r\n"
        '"%s" -m amz_fbm %%*\r\n' % exe
    )


# ---- verification (bounded, no network) --------------------------------------
def _clean_child_env():
    env = dict(os.environ)
    # prove the .pth/wrapper resolve the package — not an inherited PYTHONPATH.
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run(cmd, cwd):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=_SUBPROCESS_TIMEOUT, env=_clean_child_env())
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return None, "", "%s: %s" % (type(e).__name__, e)


def _verify(exe, scripts):
    """Exercise both command forms from a neutral cwd (so cwd never supplies the
    package). Returns a list of {name, ok, detail}."""
    neutral = tempfile.gettempdir()
    checks = []

    def _check(name, cmd, want_json=False, want_version=False):
        code, out, err = _run(cmd, neutral)
        ok = code == 0
        detail = ""
        if ok and want_json:
            try:
                data = json.loads(out)
                ok = "version" in data
            except ValueError:
                ok = False
                detail = "output was not valid JSON"
        if ok and want_version and not out.strip():
            ok = False
            detail = "no version reported"
        if not ok and not detail:
            detail = (err or out).strip()[:160]
        checks.append({"name": name, "ok": ok, "detail": detail})
        return ok

    _check("import_amz_fbm",
           [exe, "-c", "import amz_fbm, sys; sys.stdout.write(amz_fbm.__version__)"],
           want_version=True)
    _check("python_m_version", [exe, "-m", "amz_fbm", "--version"])
    _check("cli_help", [exe, "-m", "amz_fbm", "--help"])
    _check("json_output", [exe, "-m", "amz_fbm", "version", "--json"], want_json=True)
    wrapper = os.path.join(scripts, WRAPPER_NAME)
    if _IS_WINDOWS and os.path.exists(wrapper):
        _check("wrapper_version", ["cmd", "/c", wrapper, "version", "--json"],
               want_json=True)
    return checks


# ---- public API --------------------------------------------------------------
def bootstrap_offline_source(source_root, python_executable=None, verify=True):
    """Register the toolkit *source_root* into a target interpreter, offline.

    Writes a toolkit-owned ``.pth`` (source on sys.path) and an ``amz-fbm.cmd``
    wrapper (``python -m amz_fbm``). No network, no setuptools, no admin. Returns an
    OfflineBootstrapResult; inspect ``.ok`` / ``.errors``.
    """
    src = os.path.abspath(source_root) if source_root else source_root
    errors = _validate_source(src)
    if errors:
        return OfflineBootstrapResult(False, MODE_OFFLINE_SOURCE_BOOTSTRAP,
                                      source_root_display=src, errors=errors)
    try:
        paths = _target_paths(python_executable)
    except RuntimeError as e:
        return OfflineBootstrapResult(False, MODE_OFFLINE_SOURCE_BOOTSTRAP,
                                      source_root_display=src, errors=[str(e)])
    purelib, scripts, exe = paths["purelib"], paths["scripts"], paths["executable"]
    if _has_control_chars(exe):
        return OfflineBootstrapResult(False, MODE_OFFLINE_SOURCE_BOOTSTRAP,
                                      source_root_display=src,
                                      errors=["target python path contains a control character"])

    warnings, created, updated = [], [], []
    for d in (purelib, scripts):
        os.makedirs(d, exist_ok=True)
    if not os.access(purelib, os.W_OK):
        return OfflineBootstrapResult(False, MODE_OFFLINE_SOURCE_BOOTSTRAP,
                                      source_root_display=src, python_display=exe,
                                      site_packages_display=purelib,
                                      errors=["target site-packages is not writable"])

    pth_path = os.path.join(purelib, PTH_NAME)
    wrapper_path = os.path.join(scripts, WRAPPER_NAME)
    pth_content = _pth_content(src)
    wrapper_content = _wrapper_content(exe)

    # never overwrite a file we do not own
    for path, content in ((pth_path, pth_content), (wrapper_path, wrapper_content)):
        existing = _read(path)
        if existing is not None and OWNER_MARKER not in existing:
            return OfflineBootstrapResult(
                False, MODE_OFFLINE_SOURCE_BOOTSTRAP, source_root_display=src,
                python_display=exe, site_packages_display=purelib,
                pth_path_display=pth_path, wrapper_path_display=wrapper_path,
                errors=["refusing to overwrite a non-toolkit-owned file: "
                        + os.path.basename(path)])

    for path, content, label in ((pth_path, pth_content, "pth"),
                                 (wrapper_path, wrapper_content, "wrapper")):
        existing = _read(path)
        if existing is None:
            _atomic_write(path, content)
            created.append(path)
        elif existing != content:
            _atomic_write(path, content)
            updated.append(path)

    content_sha = hashlib.sha256(
        (pth_content + "\x00" + wrapper_content).encode("utf-8")).hexdigest()

    verifications = []
    ok = True
    if verify:
        verifications = _verify(exe, scripts)
        ok = all(v["ok"] for v in verifications)
        if not ok:
            errors.append("post-bootstrap verification failed")

    return OfflineBootstrapResult(
        ok, MODE_OFFLINE_SOURCE_BOOTSTRAP, source_root_display=src,
        python_display=exe, site_packages_display=purelib,
        pth_path_display=pth_path, wrapper_path_display=wrapper_path,
        files_created=created, files_updated=updated,
        verification_results=verifications, warnings=warnings, errors=errors,
        content_sha256=content_sha)


def remove_offline_source(python_executable=None):
    """Remove ONLY the toolkit-owned bootstrap ``.pth`` / ``amz-fbm.cmd``. Idempotent.

    Never deletes a file whose ownership marker is absent. Returns an
    OfflineBootstrapResult with ``files_removed``.
    """
    try:
        paths = _target_paths(python_executable)
    except RuntimeError as e:
        return OfflineBootstrapResult(True, MODE_OFFLINE_SOURCE_BOOTSTRAP,
                                      warnings=["could not resolve target: %s" % e])
    purelib, scripts, exe = paths["purelib"], paths["scripts"], paths["executable"]
    removed, warnings = [], []
    for path in (os.path.join(purelib, PTH_NAME), os.path.join(scripts, WRAPPER_NAME)):
        if not os.path.exists(path):
            continue
        if not _is_owned(path):
            warnings.append("preserved (ownership unverified): " + os.path.basename(path))
            continue
        try:
            os.remove(path)
            removed.append(path)
        except OSError as e:
            warnings.append("could not remove %s: %s" % (os.path.basename(path), e))
    return OfflineBootstrapResult(True, MODE_OFFLINE_SOURCE_BOOTSTRAP,
                                  python_display=exe, site_packages_display=purelib,
                                  files_removed=removed, warnings=warnings)


def bootstrap_status(python_executable=None):
    """Read-only status of the offline bootstrap in a target interpreter. No network."""
    try:
        paths = _target_paths(python_executable)
    except RuntimeError as e:
        return {"installed": False, "error": str(e)}
    purelib, scripts = paths["purelib"], paths["scripts"]
    pth_path = os.path.join(purelib, PTH_NAME)
    wrapper_path = os.path.join(scripts, WRAPPER_NAME)
    pth_owned = _is_owned(pth_path)
    wrapper_owned = _is_owned(wrapper_path)
    return {
        "installed": bool(pth_owned),
        "pth_present": os.path.exists(pth_path),
        "pth_owned": pth_owned,
        "pth_path": pth_path,
        "wrapper_present": os.path.exists(wrapper_path),
        "wrapper_owned": wrapper_owned,
        "wrapper_path": wrapper_path,
    }


def is_bootstrap_installed(python_executable=None):
    """True when this interpreter has the toolkit-owned bootstrap ``.pth`` present."""
    try:
        return bool(bootstrap_status(python_executable).get("pth_owned"))
    except Exception:
        return False
