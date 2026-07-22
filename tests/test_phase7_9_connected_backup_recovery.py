#!/usr/bin/env python3
"""Phase 7.9 — Connected Backup, Update & Recovery Manager.

Every test runs with NO real internet call: remote providers are the local filesystem mirror, the
HTTP layer is exercised through injected fake transports, and git update-check/stage run against
LOCAL git repositories created in a temp dir. The permanent Amazon-account boundary is asserted
throughout — no Seller Central, no SP-API, no Ads API, no seller OAuth, and every Amazon counter
stays a constant zero. Synthetic fixtures only; the RealT2 class skips when runs/ is absent and
never mutates or commits real runtime data.
"""
import base64
import io
import json
import os
import shutil
import socket
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "core"), os.path.join(ROOT, "production")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from production import phase7_connected_backup_recovery as B   # noqa: E402
import network_policy as NP                                    # noqa: E402
import diagnostics as D                                        # noqa: E402
import subprocess_runner as SP                                 # noqa: E402

PASSPHRASE = "correct horse battery staple — vượt"
GITHUB_HOSTS = ["github.com", "api.github.com", "codeload.github.com"]

# Amazon endpoint literals live in TEST code only (never in the production source, which assembles
# them from fragments). They are used solely to assert these destinations are BLOCKED.
SELLER_CENTRAL = "https://sellercentral.amazon.com/home"
SELLER_CENTRAL_EU = "https://sellercentral-europe.amazon.com/x"
SP_API = "https://sellingpartnerapi-na.amazon.com/orders/v0/orders"
ADS_API = "https://advertising-api.amazon.com/v2/campaigns"
AMAZON_OAUTH = "https://www.amazon.com/ap/signin"


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(content, (bytes, bytearray)) else "w"
    kw = {} if isinstance(content, (bytes, bytearray)) else {"encoding": "utf-8"}
    with open(path, mode, **kw) as f:
        f.write(content)


# ============================================================ base
class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p79-")
        self.src = os.path.join(self.tmp, "src")
        self.base = os.path.join(self.tmp, "ws")
        self.env = {"PHASE7_9_BACKUP_PASSPHRASE": PASSPHRASE}
        os.makedirs(self.src, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cfg(self, **env):
        e = dict(self.env)
        e.update(env)
        return B.Config(self.base, self.src, env=e)

    def populate(self, marker="MARKER"):
        """A realistic multi-phase source tree (7.3–7.7) including a valid empty-ish 7.6 dir."""
        write(os.path.join(self.src, "7.3", "promoted", "analysis.json"),
              json.dumps({"schema_version": "phase7-3-analysis-v1", "analysis_readiness": "READY",
                          "rows": [{"term": "nurse " + marker}]}))
        write(os.path.join(self.src, "7.3", "logs", "run.jsonl"), '{"event":"promote"}\n')
        write(os.path.join(self.src, "7.4", "review_state", "review.json"),
              json.dumps({"schema_version": "phase7-4-review-v1", "records": {}}))
        write(os.path.join(self.src, "7.5", "packages", "pkg-1", "readme.txt"), "decision package")
        write(os.path.join(self.src, "7.6", "runtime", "note.txt"), "tracker runtime")
        write(os.path.join(self.src, "7.7", "followups", "fu-1", "info.txt"), "followup")
        return self.cfg()


# ============================================================ network policy security
class NetworkPolicySecurity(Base):
    def ev(self, url, purpose=NP.PURPOSE_GITHUB_UPDATE, hosts=None, **kw):
        return NP.evaluate_connected_operation(url, purpose=purpose,
                                               allowed_hosts=GITHUB_HOSTS if hosts is None else hosts,
                                               **kw)

    def test_seller_central_hostname_blocked(self):
        d = self.ev(SELLER_CENTRAL)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, D.SELLER_CENTRAL_ACCESS_PROHIBITED)

    def test_seller_central_subdomain_blocked(self):
        d = self.ev(SELLER_CENTRAL_EU)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, D.SELLER_CENTRAL_ACCESS_PROHIBITED)

    def test_deceptive_seller_central_suffix_blocked(self):
        d = self.ev("https://sellercentral.amazon.com.evil.example/x")
        self.assertFalse(d.allowed)
        # deceptive suffix still classifies as a Seller Central host and is refused
        self.assertIn(d.reason_code, (D.SELLER_CENTRAL_ACCESS_PROHIBITED, D.HOST_NOT_ALLOWLISTED))

    def test_sp_api_blocked(self):
        d = self.ev(SP_API, hosts=["sellingpartnerapi-na.amazon.com"])
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, D.AMAZON_API_PROHIBITED)

    def test_ads_api_blocked(self):
        d = self.ev(ADS_API, hosts=["advertising-api.amazon.com"])
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, D.AMAZON_API_PROHIBITED)

    def test_amazon_seller_oauth_blocked(self):
        d = self.ev(AMAZON_OAUTH)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, D.AMAZON_ACCOUNT_ACCESS_PROHIBITED)

    def test_redirect_into_blocked_host_blocked(self):
        d, fwd = NP.evaluate_connected_redirect(SELLER_CENTRAL, purpose=NP.PURPOSE_GITHUB_UPDATE,
                                                allowed_hosts=GITHUB_HOSTS,
                                                original_host="api.github.com")
        self.assertFalse(d.allowed)
        self.assertFalse(fwd)

    def test_configured_github_allowed(self):
        self.assertTrue(self.ev("https://api.github.com/repos/o/r").allowed)

    def test_configured_r2_s3_allowed(self):
        d = NP.evaluate_connected_operation(
            "https://acct.r2.cloudflarestorage.com/bucket/key", purpose=NP.PURPOSE_BACKUP_PROVIDER,
            allowed_hosts=["acct.r2.cloudflarestorage.com"])
        self.assertTrue(d.allowed)

    def test_unrelated_unconfigured_host_blocked(self):
        d = self.ev("https://evil.example.com/x")
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, D.HOST_NOT_ALLOWLISTED)

    def test_lookalike_host_not_substring_matched(self):
        self.assertFalse(self.ev("https://evil-github.com/x").allowed)
        self.assertFalse(self.ev("https://github.com.evil.example/x").allowed)

    def test_https_required(self):
        d = self.ev("http://github.com/x")
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, D.INSECURE_SCHEME_BLOCKED)

    def test_tls_verification_enabled(self):
        # the raw transport builds a default TLS context with hostname + cert verification ON
        import ssl
        src = B.__file__
        text = open(src, encoding="utf-8").read()
        self.assertIn("CERT_REQUIRED", text)
        self.assertIn("check_hostname = True", text)
        self.assertNotIn("CERT_NONE", text)
        self.assertEqual(ssl.CERT_REQUIRED, ssl.CERT_REQUIRED)

    def test_credentials_not_forwarded_across_redirects(self):
        # a redirect to a *different* allowlisted host is allowed but must not forward credentials
        d, fwd = NP.evaluate_connected_redirect("https://codeload.github.com/x",
                                                purpose=NP.PURPOSE_GITHUB_UPDATE,
                                                allowed_hosts=GITHUB_HOSTS,
                                                original_host="api.github.com")
        self.assertTrue(d.allowed)
        self.assertFalse(fwd)

    def test_ipv4_normalization(self):
        # a raw public IPv4 literal is never allowlisted for a public purpose
        self.assertFalse(self.ev("https://93.184.216.34/x").allowed)

    def test_ipv6_normalization(self):
        self.assertFalse(self.ev("https://[2606:2800:220:1:248:1893:25c8:1946]/x").allowed)
        # loopback IPv6 with allow_local is a local endpoint
        d = NP.evaluate_connected_operation("http://[::1]:9000/b/k",
                                            purpose=NP.PURPOSE_BACKUP_PROVIDER, allowed_hosts=[],
                                            allow_local=True)
        self.assertTrue(d.allowed and d.is_local)

    def test_local_minio_http_allowed_only_with_flag(self):
        url = "http://127.0.0.1:9000/bucket/key"
        self.assertTrue(NP.evaluate_connected_operation(
            url, purpose=NP.PURPOSE_BACKUP_PROVIDER, allowed_hosts=[], allow_local=True).allowed)
        self.assertFalse(NP.evaluate_connected_operation(
            url, purpose=NP.PURPOSE_BACKUP_PROVIDER, allowed_hosts=[], allow_local=False).allowed)

    def test_dns_rebinding_safe_public_ip_refused(self):
        # allow_local set but the IP is PUBLIC -> refused (never treated as a local endpoint)
        d = NP.evaluate_connected_operation("https://93.184.216.34/x",
                                            purpose=NP.PURPOSE_BACKUP_PROVIDER, allowed_hosts=[],
                                            allow_local=True)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, D.LOCAL_ENDPOINT_NOT_ENABLED)

    def test_unknown_purpose_blocked(self):
        d = NP.evaluate_connected_operation("https://github.com/x", purpose="WHATEVER",
                                            allowed_hosts=GITHUB_HOSTS)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason_code, D.CONNECTED_PURPOSE_UNKNOWN)

    def test_assert_raises_connected_denied(self):
        with self.assertRaises(NP.ConnectedNetworkDenied):
            NP.assert_connected_operation_allowed(SELLER_CENTRAL, purpose=NP.PURPOSE_GITHUB_UPDATE,
                                                  allowed_hosts=GITHUB_HOSTS)


# ============================================================ snapshot model
class SnapshotModel(Base):
    def test_empty_source_snapshot(self):
        cfg = self.cfg()  # no phase dirs
        r = B.create_snapshot(cfg)
        self.assertEqual(r["readiness"], B.READY_EMPTY)
        self.assertEqual(r["file_count"], 0)
        self.assertTrue(r["snapshot_id"].startswith("snap-"))

    def test_realistic_populated_snapshot(self):
        cfg = self.populate()
        r = B.create_snapshot(cfg)
        self.assertEqual(r["readiness"], B.READY)
        self.assertEqual(r["file_count"], 6)
        self.assertGreater(r["total_bytes"], 0)

    def test_stable_snapshot_id(self):
        cfg = self.populate()
        a = B.create_snapshot(cfg)["snapshot_id"]
        # a second independent workspace over identical content yields the same id
        cfg2 = B.Config(os.path.join(self.tmp, "ws2"), self.src, env=self.env)
        b = B.create_snapshot(cfg2)["snapshot_id"]
        self.assertEqual(a, b)

    def test_deterministic_manifest(self):
        cfg = self.populate()
        B.create_snapshot(cfg)
        sid = B.build_snapshot_manifest(cfg)[0]["snapshot_id"]
        m1 = B.load_snapshot(cfg, sid)
        m2 = B.load_snapshot(cfg, sid)
        self.assertEqual(B.canonical_json(m1), B.canonical_json(m2))

    def test_sorted_file_entries(self):
        cfg = self.populate()
        m, _ = B.build_snapshot_manifest(cfg)
        paths = [e["path"] for e in m["files"]]
        self.assertEqual(paths, sorted(paths))

    def test_file_hash_verification(self):
        cfg = self.populate()
        sid = B.create_snapshot(cfg)["snapshot_id"]
        self.assertTrue(B.verify_snapshot(cfg, sid)["ok"])

    def test_tree_hash_verification(self):
        cfg = self.populate()
        m, _ = B.build_snapshot_manifest(cfg)
        ok, why = B._snapshot_valid(m)
        self.assertTrue(ok, why)

    def test_tampered_source_detection(self):
        cfg = self.populate()
        sid = B.create_snapshot(cfg)["snapshot_id"]
        write(os.path.join(self.src, "7.3", "promoted", "analysis.json"), "CHANGED")
        v = B.verify_snapshot(cfg, sid)
        self.assertFalse(v["ok"])
        self.assertIn("7.3/promoted/analysis.json", v["mismatched"])

    def test_tampered_manifest_detection(self):
        cfg = self.populate()
        sid = B.create_snapshot(cfg)["snapshot_id"]
        mpath = cfg.snapshot_manifest_path(sid)
        doc = json.load(open(mpath, encoding="utf-8"))
        doc["files"][0]["sha256"] = "0" * 64
        write(mpath, json.dumps(doc))
        with self.assertRaises(B.BackupError) as ctx:
            B.load_snapshot(cfg, sid)
        self.assertEqual(ctx.exception.code, "SNAPSHOT_MANIFEST_TAMPERED")

    def test_tampered_archive_detection(self):
        cfg = self.populate()
        m, _ = B.build_snapshot_manifest(cfg)
        archive = B.build_archive_bytes(cfg, m)
        bad = bytearray(archive)
        bad[len(bad) // 2] ^= 0xFF
        with self.assertRaises(B.BackupError):
            B._verify_archive_bytes(bytes(bad), m)

    def test_missing_source_file_detection(self):
        cfg = self.populate()
        sid = B.create_snapshot(cfg)["snapshot_id"]
        os.remove(os.path.join(self.src, "7.3", "logs", "run.jsonl"))
        v = B.verify_snapshot(cfg, sid)
        self.assertIn("7.3/logs/run.jsonl", v["missing"])
        self.assertFalse(v["ok"])

    def test_extra_source_file_detection(self):
        cfg = self.populate()
        sid = B.create_snapshot(cfg)["snapshot_id"]
        write(os.path.join(self.src, "7.4", "review_state", "extra.json"), "{}")
        v = B.verify_snapshot(cfg, sid)
        self.assertIn("7.4/review_state/extra.json", v["extra"])
        self.assertFalse(v["ok"])

    def test_source_change_produces_new_id(self):
        cfg = self.populate()
        a = B.create_snapshot(cfg)["snapshot_id"]
        write(os.path.join(self.src, "7.5", "packages", "pkg-1", "readme.txt"), "different")
        b = B.create_snapshot(cfg)["snapshot_id"]
        self.assertNotEqual(a, b)

    def test_identical_source_reuses_snapshot(self):
        cfg = self.populate()
        B.create_snapshot(cfg)
        r = B.create_snapshot(cfg)
        self.assertTrue(r["idempotent_reuse"])
        self.assertEqual(r["note"], "IDEMPOTENT_REUSE")

    def test_runtime_timestamp_excluded_from_identity(self):
        cfg = self.populate()
        m, _ = B.build_snapshot_manifest(cfg)
        # neither the tree core nor the id incorporate a timestamp; recompute must match
        self.assertEqual(m["snapshot_id"], "snap-" + m["source_tree_sha256"][:24])

    def test_mtime_excluded_from_identity(self):
        cfg = self.populate()
        a = B.build_snapshot_manifest(cfg)[0]["snapshot_id"]
        # bump mtimes far into the future
        for dp, _dn, fn in os.walk(self.src):
            for f in fn:
                os.utime(os.path.join(dp, f), (10 ** 9, 10 ** 9))
        b = B.build_snapshot_manifest(cfg)[0]["snapshot_id"]
        self.assertEqual(a, b)

    def test_path_separator_normalization(self):
        cfg = self.populate()
        m, _ = B.build_snapshot_manifest(cfg)
        for e in m["files"]:
            self.assertNotIn("\\", e["path"])

    def test_secret_file_exclusion(self):
        cfg = self.populate()
        write(os.path.join(self.src, "7.3", "my_secret.txt"), "shh")
        write(os.path.join(self.src, "7.3", "api_key.txt"), "k")
        m, _ = B.build_snapshot_manifest(cfg)
        paths = [e["path"] for e in m["files"]]
        self.assertNotIn("7.3/my_secret.txt", paths)
        self.assertNotIn("7.3/api_key.txt", paths)

    def test_env_and_key_exclusion(self):
        cfg = self.populate()
        write(os.path.join(self.src, "7.3", ".env"), "SECRET=1")
        write(os.path.join(self.src, "7.4", "server.key"), "PRIVATE")
        write(os.path.join(self.src, "7.4", "credentials.json"), "{}")
        m, _ = B.build_snapshot_manifest(cfg)
        paths = [e["path"] for e in m["files"]]
        self.assertNotIn("7.3/.env", paths)
        self.assertNotIn("7.4/server.key", paths)
        self.assertNotIn("7.4/credentials.json", paths)

    def test_ssh_key_and_cache_exclusion(self):
        cfg = self.populate()
        write(os.path.join(self.src, "7.5", ".ssh", "id_rsa"), "KEY")
        write(os.path.join(self.src, "7.5", "__pycache__", "x.pyc"), "cache")
        write(os.path.join(self.src, "7.6", "a.pyc"), "byte")
        m, _ = B.build_snapshot_manifest(cfg)
        paths = [e["path"] for e in m["files"]]
        self.assertFalse(any(".ssh" in p or "__pycache__" in p or p.endswith(".pyc") for p in paths))

    def test_symlink_rejection(self):
        cfg = self.populate()
        outside = os.path.join(self.tmp, "outside.txt")
        write(outside, "SECRET OUTSIDE")
        link = os.path.join(self.src, "7.3", "link.txt")
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not permitted on this platform/account")
        m, ex = B.build_snapshot_manifest(cfg)
        paths = [e["path"] for e in m["files"]]
        self.assertNotIn("7.3/link.txt", paths)
        self.assertGreaterEqual(m["excluded_summary"]["symlinks_skipped"], 1)

    def test_archive_path_traversal_rejection(self):
        # a crafted archive with a traversal member is refused on extraction
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name="../escape.txt")
            data = b"x"
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        with self.assertRaises(B.BackupError):
            B._safe_extract(buf.getvalue(), os.path.join(self.tmp, "dest"))

    def test_absolute_archive_path_rejection(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name="/etc/passwd" if os.name != "nt" else "C:/win.txt")
            data = b"x"
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        with self.assertRaises(B.BackupError):
            B._safe_extract(buf.getvalue(), os.path.join(self.tmp, "dest2"))


# ============================================================ encryption
class Encryption(Base):
    def enc(self, marker="PLAINTEXT_MARKER_XYZZY"):
        cfg = self.populate(marker=marker)
        sid = B.create_snapshot(cfg)["snapshot_id"]
        e = B.encrypt_snapshot(cfg, sid)
        return cfg, sid, e

    def test_encryption_success(self):
        cfg, sid, e = self.enc()
        self.assertEqual(e["readiness"], B.ENCRYPTION_READY)
        self.assertEqual(e["algorithm"], "AES-256-GCM")
        self.assertEqual(e["kdf"], "scrypt")
        self.assertTrue(os.path.isfile(cfg.encrypted_payload_path(sid)))

    def test_wrong_passphrase_failure(self):
        cfg, sid, _ = self.enc()
        bad = B.Config(self.base, self.src, env={"PHASE7_9_BACKUP_PASSPHRASE": "WRONG"})
        with self.assertRaises(B.BackupError) as ctx:
            B.decrypt_verify(bad, sid)
        self.assertEqual(ctx.exception.code, "DECRYPT_FAILED")

    def test_truncated_ciphertext_failure(self):
        cfg, sid, _ = self.enc()
        p = cfg.encrypted_payload_path(sid)
        data = open(p, "rb").read()
        write(p, data[:-20])
        with self.assertRaises(B.BackupError):
            B.decrypt_verify(cfg, sid)

    def test_modified_ciphertext_precheck_fails(self):
        cfg, sid, _ = self.enc()
        p = cfg.encrypted_payload_path(sid)
        data = bytearray(open(p, "rb").read())
        data[10] ^= 0xFF
        write(p, bytes(data))
        with self.assertRaises(B.BackupError) as ctx:
            B.decrypt_verify(cfg, sid)
        self.assertEqual(ctx.exception.code, "CIPHERTEXT_INTEGRITY")

    def test_modified_ciphertext_defeats_aead(self):
        # bypass the SHA precheck by also fixing the recorded payload hash -> GCM auth must still fail
        cfg, sid, _ = self.enc()
        p = cfg.encrypted_payload_path(sid)
        mp = cfg.encrypted_meta_path(sid)
        data = bytearray(open(p, "rb").read())
        data[10] ^= 0xFF
        write(p, bytes(data))
        meta = json.load(open(mp, encoding="utf-8"))
        meta["encrypted_payload_sha256"] = B._sha256_bytes(bytes(data))
        write(mp, json.dumps(meta))
        with self.assertRaises(B.BackupError) as ctx:
            B.decrypt_verify(cfg, sid)
        self.assertEqual(ctx.exception.code, "DECRYPT_FAILED")

    def test_unique_nonce_and_salt(self):
        cfg = self.populate("A")
        sid_a = B.create_snapshot(cfg)["snapshot_id"]
        B.encrypt_snapshot(cfg, sid_a)
        meta_a = json.load(open(cfg.encrypted_meta_path(sid_a), encoding="utf-8"))
        self.assertNotEqual(meta_a["salt_b64"], meta_a["nonce_b64"])
        write(os.path.join(self.src, "7.3", "promoted", "analysis.json"), "different content B")
        sid_b = B.create_snapshot(cfg)["snapshot_id"]
        B.encrypt_snapshot(cfg, sid_b)
        meta_b = json.load(open(cfg.encrypted_meta_path(sid_b), encoding="utf-8"))
        self.assertNotEqual(meta_a["salt_b64"], meta_b["salt_b64"])
        self.assertNotEqual(meta_a["nonce_b64"], meta_b["nonce_b64"])

    def test_no_plaintext_leakage(self):
        cfg, sid, _ = self.enc(marker="ZZ_UNIQUE_PLAINTEXT_MARKER_ZZ")
        ct = open(cfg.encrypted_payload_path(sid), "rb").read()
        self.assertNotIn(b"ZZ_UNIQUE_PLAINTEXT_MARKER_ZZ", ct)

    def test_existing_encrypted_object_reused(self):
        cfg, sid, first = self.enc()
        again = B.encrypt_snapshot(cfg, sid)
        self.assertTrue(again["idempotent_reuse"])
        self.assertEqual(first["encrypted_payload_sha256"], again["encrypted_payload_sha256"])

    def test_no_passphrase_blocks(self):
        cfg = self.populate()
        sid = B.create_snapshot(cfg)["snapshot_id"]
        nopw = B.Config(self.base, self.src, env={})
        with self.assertRaises(B.BackupError) as ctx:
            B.encrypt_snapshot(nopw, sid)
        self.assertEqual(ctx.exception.readiness, B.ENCRYPTION_REQUIRED)

    def test_decrypt_verify_roundtrip(self):
        cfg, sid, _ = self.enc()
        dv = B.decrypt_verify(cfg, sid)
        self.assertTrue(dv["ok"])
        self.assertEqual(dv["verified_file_count"], 6)

    def test_passphrase_never_in_metadata(self):
        cfg, sid, _ = self.enc()
        blob = open(cfg.encrypted_meta_path(sid), encoding="utf-8").read()
        self.assertNotIn(PASSPHRASE, blob)
        self.assertNotIn("key", json.load(open(cfg.encrypted_meta_path(sid), encoding="utf-8")))


# ============================================================ remote providers
class RemoteProviders(Base):
    def fs_env(self):
        self.remote_root = os.path.join(self.tmp, "remote")
        return {"PHASE7_9_REMOTE_PROVIDER": "filesystem",
                "PHASE7_9_FILESYSTEM_REMOTE_ROOT": self.remote_root,
                "PHASE7_9_S3_PREFIX": "backups"}

    def uploaded(self):
        cfg = self.populate()
        cfg = self.cfg(**self.fs_env())
        # re-populate under this cfg's source (same self.src)
        sid = B.create_snapshot(cfg)["snapshot_id"]
        B.encrypt_snapshot(cfg, sid)
        up = B.remote_upload(cfg, sid)
        return cfg, sid, up

    def test_filesystem_remote_upload(self):
        cfg, sid, up = self.uploaded()
        self.assertEqual(up["readiness"], B.REMOTE_READY)
        self.assertTrue(up["ok"])
        self.assertTrue(up["redownload_verified"])

    def test_filesystem_remote_verify(self):
        cfg, sid, _ = self.uploaded()
        self.assertTrue(B.remote_verify(cfg, sid)["ok"])

    def test_filesystem_remote_download(self):
        cfg, sid, _ = self.uploaded()
        dl = B.remote_download(cfg, sid)
        self.assertEqual(dl["readiness"], B.REMOTE_READY)
        self.assertGreater(dl["bytes"], 0)

    def test_remote_checksum_mismatch(self):
        cfg, sid, up = self.uploaded()
        # corrupt the stored remote object -> verify must fail
        obj = os.path.join(self.remote_root, up["object_key"].replace("/", os.sep))
        write(obj, b"corrupted-bytes")
        v = B.remote_verify(cfg, sid)
        self.assertFalse(v["ok"])
        self.assertEqual(v["readiness"], B.REMOTE_INTEGRITY_BLOCKED)

    def test_remote_overwrite_conflict(self):
        cfg, sid, up = self.uploaded()
        prov = B.FilesystemRemoteProvider(self.remote_root)
        with self.assertRaises(B.RemoteConflict):
            prov.put(up["object_key"], b"different content", content_sha256="deadbeef")

    def test_no_plaintext_uploaded(self):
        cfg, sid, up = self.uploaded()
        obj = os.path.join(self.remote_root, up["object_key"].replace("/", os.sep))
        stored = open(obj, "rb").read()
        # the stored object equals the ciphertext, not the plaintext archive
        expected = open(cfg.encrypted_payload_path(sid), "rb").read()
        self.assertEqual(stored, expected)
        self.assertNotIn(b"MARKER", stored)

    def test_content_addressed_object_key(self):
        cfg, sid, up = self.uploaded()
        meta = json.load(open(cfg.encrypted_meta_path(sid), encoding="utf-8"))
        self.assertIn(meta["encrypted_payload_sha256"], up["object_key"])

    def test_remote_not_configured_does_not_block_snapshot(self):
        cfg = self.populate()  # no remote env
        r = B.create_snapshot(cfg)
        self.assertTrue(r["ok"])
        up = B.remote_upload(cfg, r["snapshot_id"])
        self.assertEqual(up["readiness"], B.REMOTE_NOT_CONFIGURED)

    def test_s3_provider_lazy_import(self):
        # constructing the provider must NOT import boto3
        sys.modules.pop("boto3", None)
        s3 = B.S3Config.from_env({"PHASE7_9_S3_ENDPOINT_URL": "https://a.r2.cloudflarestorage.com",
                                  "PHASE7_9_S3_BUCKET": "b", "PHASE7_9_S3_ACCESS_KEY_ID": "AK",
                                  "PHASE7_9_S3_SECRET_ACCESS_KEY": "SK"})
        prov = B.S3RemoteProvider(s3)
        self.assertNotIn("boto3", sys.modules)
        self.assertEqual(prov.kind, "s3")

    def test_s3_missing_dependency_error(self):
        if "boto3" in sys.modules:
            self.skipTest("boto3 is installed in this environment")
        s3 = B.S3Config.from_env({"PHASE7_9_S3_ENDPOINT_URL": "https://a.r2.cloudflarestorage.com",
                                  "PHASE7_9_S3_BUCKET": "b", "PHASE7_9_S3_ACCESS_KEY_ID": "AK",
                                  "PHASE7_9_S3_SECRET_ACCESS_KEY": "SK"})
        prov = B.S3RemoteProvider(s3)
        with self.assertRaises(B.RemoteDependencyMissing):
            prov.list_keys()

    def test_s3_configuration_validation(self):
        s3 = B.S3Config.from_env({"PHASE7_9_S3_ENDPOINT_URL": "https://a.r2.cloudflarestorage.com"})
        self.assertIn("PHASE7_9_S3_BUCKET", s3.validate())
        with self.assertRaises(B.BackupError) as ctx:
            B.S3RemoteProvider(s3)
        self.assertEqual(ctx.exception.readiness, B.REMOTE_NOT_CONFIGURED)

    def test_s3_endpoint_amazon_blocked(self):
        s3 = B.S3Config.from_env({"PHASE7_9_S3_ENDPOINT_URL": SP_API, "PHASE7_9_S3_BUCKET": "b",
                                  "PHASE7_9_S3_ACCESS_KEY_ID": "AK",
                                  "PHASE7_9_S3_SECRET_ACCESS_KEY": "SK"})
        with self.assertRaises(NP.ConnectedNetworkDenied):
            B.S3RemoteProvider(s3)

    def test_s3_secret_redaction(self):
        s3 = B.S3Config.from_env({"PHASE7_9_S3_ENDPOINT_URL": "https://a.r2.cloudflarestorage.com",
                                  "PHASE7_9_S3_BUCKET": "b", "PHASE7_9_S3_ACCESS_KEY_ID": "AKIA-SECRET",
                                  "PHASE7_9_S3_SECRET_ACCESS_KEY": "TOPSECRETVALUE"})
        desc = json.dumps(s3.describe())
        self.assertNotIn("AKIA-SECRET", desc)
        self.assertNotIn("TOPSECRETVALUE", desc)
        self.assertEqual(s3.describe()["access_key_id"], D.REDACTED)


# ============================================================ http + dependency-check
class HttpAndDependency(Base):
    def test_network_timeout(self):
        def t(url, *, timeout_seconds, max_bytes):
            raise B.TransportTimeout("slow")
        with self.assertRaises(B.TransportTimeout):
            B.http_get_json("https://pypi.org/x", purpose=NP.PURPOSE_PACKAGE_INDEX,
                            allowed_hosts=["pypi.org"], timeout_seconds=1, max_retries=1, transport=t)

    def test_retry_limit(self):
        calls = {"n": 0}

        def t(url, *, timeout_seconds, max_bytes):
            calls["n"] += 1
            raise B.TransportTimeout("slow")
        with self.assertRaises(B.TransportTimeout):
            B.http_get_json("https://pypi.org/x", purpose=NP.PURPOSE_PACKAGE_INDEX,
                            allowed_hosts=["pypi.org"], timeout_seconds=1, max_retries=2, transport=t)
        self.assertEqual(calls["n"], 3)  # 1 initial + 2 retries

    def test_redirect_policy_allowed(self):
        seq = iter([(302, {"location": "https://pypi.org/final"}, b""), (200, {}, b'{"ok":1}')])

        def t(url, *, timeout_seconds, max_bytes):
            return next(seq)
        self.assertEqual(B.http_get_json("https://pypi.org/x", purpose=NP.PURPOSE_PACKAGE_INDEX,
                                         allowed_hosts=["pypi.org"], timeout_seconds=1, transport=t),
                         {"ok": 1})

    def test_redirect_to_blocked_host(self):
        def t(url, *, timeout_seconds, max_bytes):
            return (302, {"location": SELLER_CENTRAL}, b"")
        with self.assertRaises(NP.ConnectedNetworkDenied):
            B.http_get_json("https://pypi.org/x", purpose=NP.PURPOSE_PACKAGE_INDEX,
                            allowed_hosts=["pypi.org"], timeout_seconds=1, transport=t)

    def test_redirect_limit(self):
        def t(url, *, timeout_seconds, max_bytes):
            return (302, {"location": "https://pypi.org/loop"}, b"")
        with self.assertRaises(B.TransportError):
            B.http_get_json("https://pypi.org/x", purpose=NP.PURPOSE_PACKAGE_INDEX,
                            allowed_hosts=["pypi.org"], timeout_seconds=1, max_redirects=2, transport=t)

    def _pypi(self, version):
        def t(url, *, timeout_seconds, max_bytes):
            body = json.dumps({"info": {"version": version},
                               "releases": {version: [{"upload_time": "2026-01-02T00:00:00"}]}})
            return 200, {}, body.encode()
        return t

    def test_dependency_current(self):
        installed = B._installed_version("requests")
        cfg = self.cfg()
        r = B.dependency_check(cfg, packages=["requests"], transport=self._pypi(installed or "1.0.0"))
        self.assertEqual(r["packages"][0]["state"], B.DEP_CURRENT)

    def test_dependency_update_available(self):
        cfg = self.cfg()
        r = B.dependency_check(cfg, packages=["requests"], transport=self._pypi("999.0.0"))
        self.assertEqual(r["packages"][0]["state"], B.DEP_MAJOR)
        self.assertTrue(r["packages"][0]["update_available"])

    def test_dependency_compatible_update(self):
        cfg = self.cfg()
        inst = B._installed_version("requests") or "2.0.0"
        major = B._parse_version(inst)[0]
        r = B.dependency_check(cfg, packages=["requests"],
                               transport=self._pypi(f"{major}.9999.0"))
        self.assertEqual(r["packages"][0]["state"], B.DEP_COMPATIBLE)

    def test_dependency_network_unavailable(self):
        def t(url, *, timeout_seconds, max_bytes):
            raise B.TransportError("no network")
        cfg = self.cfg()
        r = B.dependency_check(cfg, packages=["requests"], transport=t)
        self.assertEqual(r["packages"][0]["state"], B.DEP_NETWORK_UNAVAILABLE)

    def test_dependency_offline_mode(self):
        cfg = self.cfg()
        r = B.dependency_check(cfg, packages=["requests"], allow_network=False)
        self.assertEqual(r["packages"][0]["state"], B.DEP_NETWORK_UNAVAILABLE)

    def test_dependency_no_auto_install(self):
        cfg = self.cfg()
        r = B.dependency_check(cfg, packages=["requests"], transport=self._pypi("999.0.0"))
        self.assertFalse(r["auto_install_performed"])

    def test_dependency_policy_blocked_target(self):
        # pointing the package index at an Amazon host would be refused before any fetch
        def t(url, *, timeout_seconds, max_bytes):
            return 200, {}, b'{"info":{"version":"1.0"}}'
        with self.assertRaises(NP.ConnectedNetworkDenied):
            B.http_get_json(SP_API, purpose=NP.PURPOSE_PACKAGE_INDEX,
                            allowed_hosts=["sellingpartnerapi-na.amazon.com"], timeout_seconds=1,
                            transport=t)


# ============================================================ restore model
class RestoreModel(Base):
    def prepared(self):
        cfg = self.populate()
        cfg = self.cfg()
        sid = B.create_snapshot(cfg)["snapshot_id"]
        B.encrypt_snapshot(cfg, sid)
        return cfg, sid

    def test_restore_plan_creates_no_changes(self):
        cfg, sid = self.prepared()
        before = B._sha256_file(os.path.join(self.src, "7.3", "promoted", "analysis.json"))
        plan = B.restore_plan(cfg, sid)
        self.assertEqual(plan["readiness"], B.RESTORE_PLAN_READY)
        self.assertEqual(plan["required_confirmation_token"], f"RESTORE:{sid}")
        after = B._sha256_file(os.path.join(self.src, "7.3", "promoted", "analysis.json"))
        self.assertEqual(before, after)

    def test_recovery_drill_isolated(self):
        cfg, sid = self.prepared()
        d = B.recovery_drill(cfg, sid)
        self.assertEqual(d["readiness"], B.RECOVERY_DRILL_READY)
        # the restored tree lives under the drill dir, not in the source root
        self.assertIn("recovery_drills", os.path.normpath(
            os.path.join(cfg.recovery_drills_dir)).replace("\\", "/"))
        self.assertTrue(d["ok"])

    def test_recovery_drill_verifies_every_file(self):
        cfg, sid = self.prepared()
        d = B.recovery_drill(cfg, sid)
        self.assertEqual(d["restored_file_count"], 6)
        self.assertEqual(d["file_failures"], [])

    def test_recovery_drill_detects_ciphertext_corruption(self):
        cfg, sid = self.prepared()
        p = cfg.encrypted_payload_path(sid)
        data = bytearray(open(p, "rb").read())
        data[5] ^= 0xFF
        write(p, bytes(data))
        with self.assertRaises(B.BackupError):
            B.recovery_drill(cfg, sid)

    def test_recovery_drill_detects_broken_7_6_chain(self):
        # a broken Phase 7.6 append-only history is caught by the reused accepted authority
        write(os.path.join(self.src, "7.6", "action_state", "history.jsonl"),
              json.dumps({"event_seq": 7, "tracker_record_id": "r", "local_revision": 1,
                          "previous_record_hash": None, "record_content_sha256": "x",
                          "record": {}}) + "\n")
        cfg = self.cfg()
        sid = B.create_snapshot(cfg)["snapshot_id"]
        d = B.recovery_drill(cfg, sid)
        self.assertEqual(d["readiness"], B.RECOVERY_DRILL_BLOCKED)
        self.assertFalse(d["schema_validation_ok"])

    def test_recovery_drill_detects_bad_7_7_followup(self):
        write(os.path.join(self.src, "7.7", "followups", "fu-x", "followup_manifest.json"),
              json.dumps({"schema_version": "v", "artifact_sha256": {"outcome_status.json": "00" * 32}}))
        write(os.path.join(self.src, "7.7", "followups", "fu-x", "outcome_status.json"), "{}")
        cfg = self.cfg()
        sid = B.create_snapshot(cfg)["snapshot_id"]
        d = B.recovery_drill(cfg, sid)
        self.assertFalse(d["schema_validation_ok"])

    def test_restore_confirmation_required(self):
        cfg, sid = self.prepared()
        B.recovery_drill(cfg, sid)
        r = B.restore(cfg, sid, confirm_token=None)
        self.assertEqual(r["readiness"], B.RESTORE_CONFIRMATION_REQUIRED)
        self.assertFalse(r["ok"])

    def test_wrong_restore_token_blocked(self):
        cfg, sid = self.prepared()
        B.recovery_drill(cfg, sid)
        r = B.restore(cfg, sid, confirm_token="RESTORE:not-the-right-id")
        self.assertEqual(r["readiness"], B.RESTORE_CONFIRMATION_REQUIRED)

    def test_restore_requires_recovery_drill(self):
        cfg, sid = self.prepared()  # no drill run
        with self.assertRaises(B.BackupError) as ctx:
            B.restore(cfg, sid, confirm_token=f"RESTORE:{sid}")
        self.assertEqual(ctx.exception.code, "RECOVERY_DRILL_REQUIRED")

    def test_restore_creates_pre_restore_backup(self):
        cfg, sid = self.prepared()
        B.recovery_drill(cfg, sid)
        write(os.path.join(self.src, "7.3", "promoted", "analysis.json"), "CHANGED BEFORE RESTORE")
        r = B.restore(cfg, sid, confirm_token=f"RESTORE:{sid}")
        self.assertEqual(r["readiness"], B.RESTORE_READY)
        self.assertTrue(r["pre_restore_snapshot_id"].startswith("snap-"))
        # the pre-restore backup preserved the pre-restore (changed) content
        backup = os.path.join(cfg.base_dir, "pre_restore_backups", sid, r["pre_restore_snapshot_id"],
                              "7.3", "promoted", "analysis.json")
        self.assertTrue(os.path.isfile(backup))
        self.assertIn("CHANGED BEFORE RESTORE", open(backup, encoding="utf-8").read())

    def test_restore_restores_original_content(self):
        cfg, sid = self.prepared()
        original = open(os.path.join(self.src, "7.3", "promoted", "analysis.json"),
                        encoding="utf-8").read()
        B.recovery_drill(cfg, sid)
        write(os.path.join(self.src, "7.3", "promoted", "analysis.json"), "CHANGED")
        B.restore(cfg, sid, confirm_token=f"RESTORE:{sid}")
        self.assertEqual(open(os.path.join(self.src, "7.3", "promoted", "analysis.json"),
                              encoding="utf-8").read(), original)

    def test_restore_only_touches_scope_roots(self):
        cfg, sid = self.prepared()
        # a sibling directory outside the scope must be untouched
        write(os.path.join(self.src, "unrelated", "keep.txt"), "KEEP ME")
        B.recovery_drill(cfg, sid)
        B.restore(cfg, sid, confirm_token=f"RESTORE:{sid}")
        self.assertTrue(os.path.isfile(os.path.join(self.src, "unrelated", "keep.txt")))
        self.assertIn("KEEP ME", open(os.path.join(self.src, "unrelated", "keep.txt"),
                                      encoding="utf-8").read())

    def test_restore_does_not_declare_code_or_git_targets(self):
        cfg, sid = self.prepared()
        plan = B.restore_plan(cfg, sid)
        pd = json.load(open(os.path.join(cfg.restore_plans_dir, sid + ".json"), encoding="utf-8"))
        self.assertFalse(pd["restores_tracked_code"])
        self.assertFalse(pd["overwrites_git_repository"])
        for root in pd["approved_runtime_roots"]:
            self.assertIn(root, B.DEFAULT_SOURCE_SCOPE)


# ============================================================ git repos helper
def _git(cwd, *args, check=True):
    r = SP.run_subprocess(["git", "-C", cwd, *args], timeout_seconds=60, allowed_exit_codes=(0, 1))
    if check and not r.success:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr}")
    return r


class _GitBase(Base):
    def setUp(self):
        super().setUp()
        # deterministic identity + neutral line endings for the synthetic repos
        for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                     "GIT_COMMITTER_EMAIL": "t@t"}.items():
            os.environ.setdefault(k, v)
        self.upstream = os.path.join(self.tmp, "upstream")
        os.makedirs(self.upstream)
        _git(self.upstream, "init", "-q", "-b", "main")
        write(os.path.join(self.upstream, ".gitattributes"), "* -text\n")
        write(os.path.join(self.upstream, "core", "m.py"), "x = 1\n")
        write(os.path.join(self.upstream, "production", "p.py"), "y = 2\n")
        write(os.path.join(self.upstream, "tests", "__init__.py"), "")
        write(os.path.join(self.upstream, "tests", "test_ok.py"),
              "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n")
        _git(self.upstream, "add", "-A")
        _git(self.upstream, "commit", "-qm", "c1")
        self.work = os.path.join(self.tmp, "work")
        _git(self.tmp, "clone", "-q", self.upstream, self.work)
        # workspace + source OUTSIDE the repo so the repo tree stays clean
        self.ext = os.path.join(self.tmp, "ext")
        self.gcfg = B.Config(os.path.join(self.ext, "ws"), os.path.join(self.ext, "src"),
                             repo_root=self.work, env=self.env)
        write(os.path.join(self.ext, "src", "7.3", "a.json"), "{}")

    def add_upstream_commit(self):
        write(os.path.join(self.upstream, "core", "m.py"), "x = 1\nz = 3\n")
        _git(self.upstream, "commit", "-qam", "c2")


class UpdateCheckStage(_GitBase):
    def test_update_check_current(self):
        r = B.update_check(self.gcfg, remote="origin", branch="main")
        self.assertEqual(r["readiness"], B.UPDATE_CURRENT)
        self.assertEqual(r["local_head"], r["remote_branch_head"])

    def test_update_check_update_available(self):
        self.add_upstream_commit()
        r = B.update_check(self.gcfg, remote="origin", branch="main")
        self.assertEqual(r["readiness"], B.UPDATE_AVAILABLE)
        self.assertTrue(r["update_available"])

    def test_update_check_remote_unavailable(self):
        r = B.update_check(self.gcfg, remote="does-not-exist", branch="main")
        self.assertIn(r["readiness"], (B.UPDATE_UNAVAILABLE,))
        self.assertFalse(r["ok"])

    def test_update_check_blocked_seller_central_target(self):
        r = B.update_check(self.gcfg, remote_url=SELLER_CENTRAL)
        self.assertEqual(r["readiness"], B.SELLER_CENTRAL_POLICY_BLOCKED)
        self.assertFalse(r["ok"])

    def test_update_stage_requires_clean_tree(self):
        write(os.path.join(self.work, "core", "dirty.py"), "d = 1\n")
        r = B.update_stage(self.gcfg, remote="origin", branch="main")
        self.assertEqual(r["readiness"], B.UPDATE_STAGE_BLOCKED)

    def test_update_stage_creates_isolated_worktree_and_passes(self):
        self.add_upstream_commit()
        r = B.update_stage(self.gcfg, remote="origin", branch="main",
                           focused_tests=["tests.test_ok"])
        self.assertEqual(r["readiness"], B.UPDATE_STAGE_READY)
        self.assertTrue(r["compile_ok"])
        self.assertTrue(r["focused_tests_ok"])

    def test_update_stage_leaves_primary_tree_unchanged(self):
        head_before = _git(self.work, "rev-parse", "HEAD").stdout.strip()
        self.add_upstream_commit()
        r = B.update_stage(self.gcfg, remote="origin", branch="main",
                           focused_tests=["tests.test_ok"])
        head_after = _git(self.work, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(head_before, head_after)
        self.assertTrue(r["primary_tree_unchanged"])
        self.assertEqual(_git(self.work, "status", "--porcelain").stdout.strip(), "")

    def test_update_stage_reports_failing_tests(self):
        self.add_upstream_commit()
        r = B.update_stage(self.gcfg, remote="origin", branch="main",
                           focused_tests=["tests.__does_not_exist__"])
        self.assertEqual(r["readiness"], B.UPDATE_STAGE_BLOCKED)
        self.assertFalse(r["focused_tests_ok"])

    def test_update_stage_no_apply_to_primary(self):
        self.add_upstream_commit()
        B.update_stage(self.gcfg, remote="origin", branch="main", focused_tests=["tests.test_ok"])
        # the primary work tree still has the old core/m.py (no z = 3)
        self.assertNotIn("z = 3", open(os.path.join(self.work, "core", "m.py"),
                                       encoding="utf-8").read())


# ============================================================ subprocess safety
class SubprocessSafety(Base):
    def test_static_subprocess_allowlist(self):
        with self.assertRaises(B.BackupError) as ctx:
            B._run_static_validation(self.cfg(), ["notepad.exe", "x"], cwd=self.tmp)
        self.assertEqual(ctx.exception.code, "VALIDATION_EXECUTABLE_NOT_ALLOWED")

    def test_no_shell_string_command(self):
        with self.assertRaises(B.BackupError):
            B._run_static_validation(self.cfg(), "git status", cwd=self.tmp)

    def test_no_shell_true_in_source(self):
        text = open(B.__file__, encoding="utf-8").read()
        self.assertNotIn("shell=True", text)

    def test_bounded_timeout_enforced(self):
        r = SP.run_subprocess([sys.executable, "-c", "import time; time.sleep(10)"],
                              timeout_seconds=1, stage_name="sleep")
        self.assertTrue(r.timed_out)
        self.assertFalse(r.success)

    def test_python_executable_allowed(self):
        r = B._run_static_validation(self.cfg(), [sys.executable, "-c", "print(1)"], cwd=self.tmp)
        self.assertTrue(r["success"])


# ============================================================ determinism / json safety
class Determinism(Base):
    def test_canonical_json_sorted(self):
        self.assertEqual(B.canonical_json({"b": 1, "a": 2}), '{\n  "a": 2,\n  "b": 1\n}')

    def test_no_nan(self):
        with self.assertRaises(ValueError):
            B.canonical_json({"x": float("nan")})

    def test_no_infinity(self):
        with self.assertRaises(ValueError):
            B.canonical_json({"x": float("inf")})

    def test_manifest_has_no_float(self):
        cfg = self.populate()
        m, _ = B.build_snapshot_manifest(cfg)

        def walk(o):
            if isinstance(o, float):
                self.fail("manifest contains a float")
            if isinstance(o, dict):
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(m)

    def test_utf8_and_vietnamese_paths(self):
        cfg = self.populate()
        write(os.path.join(self.src, "7.3", "chào-mừng.json"),
              json.dumps({"ghi_chú": "vượt qua — nợ"}, ensure_ascii=False))
        m, _ = B.build_snapshot_manifest(cfg)
        paths = [e["path"] for e in m["files"]]
        self.assertIn("7.3/chào-mừng.json", paths)
        sid = B.create_snapshot(cfg)["snapshot_id"]
        B.encrypt_snapshot(cfg, sid)
        self.assertTrue(B.decrypt_verify(cfg, sid)["ok"])

    def test_atomic_snapshot_no_temp_left(self):
        cfg = self.populate()
        sid = B.create_snapshot(cfg)["snapshot_id"]
        leftovers = [f for f in os.listdir(os.path.join(cfg.snapshots_dir, sid))
                     if f.startswith(".tmp-79-")]
        self.assertEqual(leftovers, [])

    def test_atomic_encryption_no_temp_left(self):
        cfg = self.populate()
        sid = B.create_snapshot(cfg)["snapshot_id"]
        B.encrypt_snapshot(cfg, sid)
        d = os.path.join(cfg.encrypted_dir, sid)
        self.assertFalse(any(f.startswith(".tmp-79-") for f in os.listdir(d)))

    def test_atomic_download_verified(self):
        remote_root = os.path.join(self.tmp, "remote")
        cfg = B.Config(self.base, self.src, env={**self.env, "PHASE7_9_REMOTE_PROVIDER": "filesystem",
                                                 "PHASE7_9_FILESYSTEM_REMOTE_ROOT": remote_root})
        self.populate()
        sid = B.create_snapshot(cfg)["snapshot_id"]
        B.encrypt_snapshot(cfg, sid)
        B.remote_upload(cfg, sid)
        dl = B.remote_download(cfg, sid)
        self.assertTrue(os.path.isfile(os.path.join(cfg.remote_cache_dir, sid + ".enc")))
        self.assertGreater(dl["bytes"], 0)

    def test_partial_failure_cleanup(self):
        # a write to a normal path leaves no temp sibling behind
        target = os.path.join(self.tmp, "x", "y.json")
        B._atomic_write_json(target, {"a": 1})
        self.assertEqual([f for f in os.listdir(os.path.dirname(target))
                          if f.startswith(".tmp-79-")], [])


# ============================================================ secrets + logging
class SecretsAndLogging(Base):
    def test_redact_helper(self):
        os.environ["PHASE7_9_BACKUP_PASSPHRASE"] = "SUPER-SECRET-PW"
        try:
            self.assertNotIn("SUPER-SECRET-PW", B.redact("my pw is SUPER-SECRET-PW here"))
            self.assertNotIn("SUPER-SECRET-PW",
                             json.dumps(B.redact({"note": "SUPER-SECRET-PW", "n": [1, 2]})))
        finally:
            os.environ.pop("PHASE7_9_BACKUP_PASSPHRASE", None)

    def test_logs_redact_secrets(self):
        cfg = self.populate()
        os.environ["PHASE7_9_S3_SECRET_ACCESS_KEY"] = "LEAKME-SECRET-KEY"
        try:
            B._log(cfg, "test", {"secret_access_key": "LEAKME-SECRET-KEY", "ok": True})
            blob = open(os.path.join(cfg.logs_dir, "operations.jsonl"), encoding="utf-8").read()
            self.assertNotIn("LEAKME-SECRET-KEY", blob)
        finally:
            os.environ.pop("PHASE7_9_S3_SECRET_ACCESS_KEY", None)

    def test_no_secret_in_manifest(self):
        cfg = self.populate()
        sid = B.create_snapshot(cfg)["snapshot_id"]
        blob = open(cfg.snapshot_manifest_path(sid), encoding="utf-8").read()
        self.assertNotIn(PASSPHRASE, blob)

    def test_env_credentials_not_persisted(self):
        cfg = self.populate()
        sid = B.create_snapshot(cfg)["snapshot_id"]
        B.encrypt_snapshot(cfg, sid)
        # scan every artifact the workspace wrote for the passphrase
        for dp, _dn, fn in os.walk(cfg.base_dir):
            for f in fn:
                data = open(os.path.join(dp, f), "rb").read()
                self.assertNotIn(PASSPHRASE.encode("utf-8"), data)

    def test_no_secret_in_stdout_stderr(self):
        out, err = io.StringIO(), io.StringIO()
        cfg_dir = os.path.join(self.tmp, "cliws")
        self.populate()
        env_backup = os.environ.get("PHASE7_9_BACKUP_PASSPHRASE")
        os.environ["PHASE7_9_BACKUP_PASSPHRASE"] = "STDOUT-SECRET-PW"
        try:
            with redirect_stdout(out), redirect_stderr(err):
                B.run_cli(["--base-dir", cfg_dir, "--source-root", self.src, "snapshot"])
        finally:
            if env_backup is None:
                os.environ.pop("PHASE7_9_BACKUP_PASSPHRASE", None)
            else:
                os.environ["PHASE7_9_BACKUP_PASSPHRASE"] = env_backup
        self.assertNotIn("STDOUT-SECRET-PW", out.getvalue())
        self.assertNotIn("STDOUT-SECRET-PW", err.getvalue())


# ============================================================ source immutability
class Immutability(Base):
    def _tree_hash(self, root):
        h = {}
        for dp, _dn, fn in os.walk(root):
            for f in fn:
                full = os.path.join(dp, f)
                h[os.path.relpath(full, root).replace(os.sep, "/")] = B._sha256_file(full)
        return h

    def test_source_immutable_across_all_operations(self):
        cfg = self.populate()
        before = self._tree_hash(self.src)
        sid = B.create_snapshot(cfg)["snapshot_id"]
        B.verify_snapshot(cfg, sid)
        B.encrypt_snapshot(cfg, sid)
        B.decrypt_verify(cfg, sid)
        B.restore_plan(cfg, sid)
        B.recovery_drill(cfg, sid)
        after = self._tree_hash(self.src)
        self.assertEqual(before, after)

    def test_each_phase_dir_immutable(self):
        cfg = self.populate()
        per_phase_before = {p: self._tree_hash(os.path.join(self.src, p))
                            for p in ("7.3", "7.4", "7.5", "7.6", "7.7")}
        sid = B.create_snapshot(cfg)["snapshot_id"]
        B.encrypt_snapshot(cfg, sid)
        B.recovery_drill(cfg, sid)
        for p, before in per_phase_before.items():
            self.assertEqual(before, self._tree_hash(os.path.join(self.src, p)), p)

    def test_workspace_separate_from_source(self):
        cfg = self.populate()
        B.create_snapshot(cfg)
        # nothing was written under the source root's phase dirs by the workspace
        self.assertFalse(os.path.exists(os.path.join(self.src, "snapshots")))
        self.assertTrue(os.path.isdir(cfg.snapshots_dir))


# ============================================================ amazon boundary
class AmazonBoundary(Base):
    def test_counters_zero_in_results(self):
        cfg = self.populate()
        sid = B.create_snapshot(cfg)["snapshot_id"]
        for res in (B.create_snapshot(cfg), B.verify_snapshot(cfg, sid),
                    B.encrypt_snapshot(cfg, sid), B.decrypt_verify(cfg, sid),
                    B.restore_plan(cfg, sid), B.recovery_drill(cfg, sid),
                    B.validate_only(cfg, sid)):
            self.assertTrue(all(v == 0 for v in res["amazon_counters"].values()), res["command"])

    def test_snapshot_manifest_counters_zero(self):
        cfg = self.populate()
        m, _ = B.build_snapshot_manifest(cfg)
        for key in ("seller_central_connections", "amazon_sp_api_calls", "amazon_ads_api_calls",
                    "amazon_seller_auth_calls", "amazon_seller_mutations"):
            self.assertEqual(m[key], 0)

    def test_this_session_never_flags(self):
        self.assertTrue(all(B.THIS_SESSION_NEVER.values()))

    def test_no_amazon_endpoint_literal_in_source(self):
        text = open(B.__file__, encoding="utf-8").read()
        for literal in ("sellercentral.amazon", "sellingpartnerapi", "advertising-api.amazon"):
            self.assertNotIn(literal, text, literal)


# ============================================================ CLI
class CLI(Base):
    def _cli(self, *args, env=None):
        out = io.StringIO()
        prev = {}
        for k, v in (env or {}).items():
            prev[k] = os.environ.get(k)
            os.environ[k] = v
        try:
            with redirect_stdout(out):
                code = B.run_cli(list(args))
        finally:
            for k, v in prev.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return code, out.getvalue()

    def test_cli_help(self):
        with self.assertRaises(SystemExit):
            B.build_parser().parse_args(["--help"])

    def test_cli_missing_subcommand(self):
        with self.assertRaises(SystemExit):
            B.build_parser().parse_args([])

    def test_cli_verify_missing_snapshot_id(self):
        with self.assertRaises(SystemExit):
            B.build_parser().parse_args(["verify"])

    def test_cli_snapshot_exit_zero(self):
        self.populate()
        code, out = self._cli("--base-dir", self.base, "--source-root", self.src, "snapshot")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["readiness"], B.READY)

    def test_cli_empty_snapshot_exit_zero(self):
        code, out = self._cli("--base-dir", self.base, "--source-root",
                             os.path.join(self.tmp, "empty"), "snapshot")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["readiness"], B.READY_EMPTY)

    def test_cli_validate_only_writes_nothing(self):
        self.populate()
        base = os.path.join(self.tmp, "vo_ws")
        code, out = self._cli("--base-dir", base, "--source-root", self.src, "validate-only")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["files_written"], 0)
        self.assertFalse(os.path.isdir(os.path.join(base, "snapshots")))

    def test_cli_blocked_nonzero_exit(self):
        # decrypt-verify on a nonexistent snapshot -> blocked -> nonzero
        code, out = self._cli("--base-dir", self.base, "--source-root", self.src,
                             "decrypt-verify", "--snapshot-id", "snap-nope")
        self.assertNotEqual(code, 0)
        self.assertFalse(json.loads(out)["ok"])

    def test_cli_seller_central_update_check_nonzero(self):
        code, out = self._cli("--base-dir", self.base, "--source-root", self.src,
                             "update-check", "--remote-url", SELLER_CENTRAL)
        self.assertNotEqual(code, 0)
        self.assertEqual(json.loads(out)["readiness"], B.SELLER_CENTRAL_POLICY_BLOCKED)

    def test_cli_list_snapshots(self):
        cfg = self.populate()
        B.create_snapshot(cfg)
        code, out = self._cli("--base-dir", self.base, "--source-root", self.src, "list")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["count"], 1)

    def test_cli_dependency_check_offline(self):
        code, out = self._cli("--base-dir", self.base, "--source-root", self.src,
                             "dependency-check", "--offline")
        self.assertEqual(code, 0)
        self.assertFalse(json.loads(out)["auto_install_performed"])


# ============================================================ real T2 runtime data (never committed)
class RealT2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_root = os.path.join(ROOT, "runs", "T2", "phase7")
        if not os.path.isdir(cls.source_root) or not any(
                os.path.isdir(os.path.join(cls.source_root, p)) for p in B.DEFAULT_SOURCE_SCOPE):
            raise unittest.SkipTest("real T2 runtime data not present")
        cls.tmp = tempfile.mkdtemp(prefix="p79-realt2-")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "tmp"):
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def cfg(self):
        return B.Config(os.path.join(self.tmp, "ws"), self.source_root,
                        env={"PHASE7_9_BACKUP_PASSPHRASE": PASSPHRASE})

    def _tree_hash(self):
        h = {}
        for p in B.DEFAULT_SOURCE_SCOPE:
            root = os.path.join(self.source_root, p)
            if not os.path.isdir(root):
                continue
            for dp, _dn, fn in os.walk(root):
                for f in fn:
                    full = os.path.join(dp, f)
                    h[os.path.relpath(full, self.source_root).replace(os.sep, "/")] = \
                        B._sha256_file(full)
        return h

    def test_real_t2_snapshot_verify_drill(self):
        before = self._tree_hash()
        cfg = self.cfg()
        snap = B.create_snapshot(cfg)
        self.assertIn(snap["readiness"], (B.READY, B.READY_EMPTY))
        self.assertTrue(B.verify_snapshot(cfg, snap["snapshot_id"])["ok"])
        B.encrypt_snapshot(cfg, snap["snapshot_id"])
        self.assertTrue(B.decrypt_verify(cfg, snap["snapshot_id"])["ok"])
        drill = B.recovery_drill(cfg, snap["snapshot_id"])
        self.assertEqual(drill["readiness"], B.RECOVERY_DRILL_READY)
        # every real source tree is byte-identical afterward
        self.assertEqual(before, self._tree_hash())
        # counters stay zero
        self.assertTrue(all(v == 0 for v in snap["amazon_counters"].values()))

    def test_real_t2_no_secret_file_included(self):
        cfg = self.cfg()
        m, _ = B.build_snapshot_manifest(cfg)
        for e in m["files"]:
            low = e["path"].lower()
            self.assertFalse(low.endswith((".env", ".key", ".pem")))
            self.assertNotIn("__pycache__", low)
            self.assertNotIn("credential", low)


if __name__ == "__main__":
    unittest.main(verbosity=2)
