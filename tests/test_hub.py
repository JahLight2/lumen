"""Slice C: project hub -- locks, version registry, conflict recovery.

The load-bearing test in this file is `TestNoProjectBytesAreRead`. Everything
else is behaviour; that one is the blueprint's integrity rule:

    never byte-merge or sync .prproj/.drp as documents, never keep a second copy

It works by making `open()` explode for the project path and then exercising
every hub operation. If any code path ever starts reading project files, that
test fails loudly instead of the failure surfacing as a corrupted timeline in
somebody's edit.
"""

import builtins
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from lfmc.core import db, hub
from lfmc.net.server import Config, make_server

TOKEN = "hub-test-token"


class HubFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.db_path = cls.tmp / "catalogue.db"
        # A project file that is never opened: 12 bytes of fake content.
        cls.project_file = cls.tmp / "Addis_Doc_v3.prproj"
        cls.project_file.write_bytes(b"PRPROJFAKE12")
        cls.config = Config(db_path=str(cls.db_path), token=TOKEN, host="127.0.0.1", port=0)
        cls.httpd = make_server(cls.config)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls._tmp.cleanup()

    def setUp(self):
        # each test gets a clean catalogue
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.db_path) + suffix)
            if p.exists():
                p.unlink()
        db.connect(self.db_path).close()

    def conn(self):
        return db.connect(self.db_path)

    def req(self, path, method="GET", body=None):
        headers = {"Authorization": f"Bearer {TOKEN}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                   data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")


class TestLifecycle(HubFixture):
    def test_create_and_list(self):
        conn = self.conn()
        try:
            p = hub.create_project(conn, "Addis Documentary")
            self.assertEqual(p["name"], "Addis Documentary")
            self.assertIsNone(p["lock"])
            self.assertEqual(p["versions"], 0)
            self.assertEqual(len(hub.list_projects(conn)), 1)
        finally:
            conn.close()

    def test_empty_name_rejected(self):
        conn = self.conn()
        try:
            with self.assertRaises(hub.HubError):
                hub.create_project(conn, "   ")
        finally:
            conn.close()

    def test_unknown_project_is_404_over_http(self):
        status, body = self.req("/api/projects/nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"], "not_found")


class TestLocks(HubFixture):
    def test_second_holder_is_refused_with_the_name_of_the_first(self):
        conn = self.conn()
        try:
            p = hub.create_project(conn, "P")
            hub.acquire_lock(conn, p["id"], holder="abebe")
            with self.assertRaises(hub.LockHeld) as ctx:
                hub.acquire_lock(conn, p["id"], holder="tigist")
            self.assertEqual(ctx.exception.lock["holder"], "abebe")
            self.assertIn("abebe", str(ctx.exception))
        finally:
            conn.close()

    def test_lock_conflict_is_409_over_http(self):
        status, project = self.req("/api/projects", "POST", {"name": "P"})
        self.assertEqual(status, 201)
        pid = project["id"]
        status, lock = self.req(f"/api/projects/{pid}/lock", "POST", {"holder": "abebe"})
        self.assertEqual(status, 201, lock)
        self.assertEqual(lock["holder"], "abebe")
        status, body = self.req(f"/api/projects/{pid}/lock", "POST", {"holder": "tigist"})
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "lock_held")
        self.assertEqual(body["holder"], "abebe")

    def test_same_holder_extends_instead_of_fighting(self):
        conn = self.conn()
        try:
            p = hub.create_project(conn, "P")
            hub.acquire_lock(conn, p["id"], holder="abebe", ttl_minutes=10)
            again = hub.acquire_lock(conn, p["id"], holder="abebe", ttl_minutes=60)
            self.assertTrue(again.get("extended"))
            self.assertGreater(again["expires_at"], again["acquired_at"])
        finally:
            conn.close()

    def test_expired_lock_is_reclaimed_and_names_the_previous_holder(self):
        conn = self.conn()
        try:
            p = hub.create_project(conn, "P")
            hub.acquire_lock(conn, p["id"], holder="abebe", ttl_minutes=1)
            # simulate a laptop that went home: push the expiry into the past
            conn.execute("UPDATE project_locks SET expires_at=? WHERE project_id=?",
                         ("2020-01-01T00:00:00+00:00", p["id"]))
            conn.commit()
            self.assertTrue(hub.lock_state(conn, p["id"])["expired"])
            lock = hub.acquire_lock(conn, p["id"], holder="tigist")
            self.assertEqual(lock["holder"], "tigist")
            self.assertEqual(lock["reclaimed_from"]["holder"], "abebe")
        finally:
            conn.close()

    def test_only_the_holder_releases(self):
        conn = self.conn()
        try:
            p = hub.create_project(conn, "P")
            hub.acquire_lock(conn, p["id"], holder="abebe")
            with self.assertRaises(hub.LockHeld):
                hub.release_lock(conn, p["id"], holder="tigist")
            res = hub.release_lock(conn, p["id"], holder="abebe")
            self.assertTrue(res["released"])
            self.assertIsNone(hub.lock_state(conn, p["id"]))
        finally:
            conn.close()

    def test_release_when_nothing_is_held_is_not_an_error(self):
        conn = self.conn()
        try:
            p = hub.create_project(conn, "P")
            res = hub.release_lock(conn, p["id"], holder="abebe")
            self.assertFalse(res["released"])
        finally:
            conn.close()

    def test_absurd_ttl_is_rejected(self):
        conn = self.conn()
        try:
            p = hub.create_project(conn, "P")
            with self.assertRaises(hub.HubError):
                hub.acquire_lock(conn, p["id"], holder="abebe", ttl_minutes=60 * 48)
        finally:
            conn.close()


class TestVersions(HubFixture):
    def test_register_records_path_and_stat_not_contents(self):
        conn = self.conn()
        try:
            p = hub.create_project(conn, "P")
            v = hub.register_version(conn, p["id"], label="v1", author="abebe",
                                     source_path=str(self.project_file))
            self.assertEqual(v["source_path"], str(self.project_file))
            self.assertEqual(v["size_bytes"], 12)
            self.assertTrue(v["fingerprint"].startswith("stat:v1:12:"))
            # the stored fingerprint must not be a content hash of the file
            import hashlib
            self.assertNotIn(hashlib.blake2b(b"PRPROJFAKE12").hexdigest()[:16],
                             v["fingerprint"])
        finally:
            conn.close()

    def test_duplicate_label_is_refused(self):
        conn = self.conn()
        try:
            p = hub.create_project(conn, "P")
            hub.register_version(conn, p["id"], label="v1", author="a",
                                 source_path=str(self.project_file))
            with self.assertRaises(hub.HubError):
                hub.register_version(conn, p["id"], label="v1", author="b",
                                     source_path=str(self.project_file))
        finally:
            conn.close()

    def test_missing_file_is_a_clear_error(self):
        conn = self.conn()
        try:
            p = hub.create_project(conn, "P")
            with self.assertRaises(hub.HubError):
                hub.register_version(conn, p["id"], label="v1", author="a",
                                     source_path=str(self.tmp / "gone.prproj"))
        finally:
            conn.close()

    def test_media_file_is_not_accepted_as_a_project_reference(self):
        conn = self.conn()
        try:
            p = hub.create_project(conn, "P")
            clip = self.tmp / "clip.mp4"
            clip.write_bytes(b"not really video")
            with self.assertRaises(hub.HubError):
                hub.register_version(conn, p["id"], label="v1", author="a",
                                     source_path=str(clip))
        finally:
            conn.close()

    def test_version_chain_is_ordered(self):
        conn = self.conn()
        try:
            p = hub.create_project(conn, "P")
            for label in ("v1", "v2", "v3"):
                hub.register_version(conn, p["id"], label=label, author="a",
                                     source_path=str(self.project_file))
                time.sleep(0.01)
            chain = [v["label"] for v in hub.list_versions(conn, p["id"])]
            self.assertEqual(chain, ["v1", "v2", "v3"])
        finally:
            conn.close()

    def test_missing_file_shows_as_not_present_but_still_listed(self):
        conn = self.conn()
        try:
            p = hub.create_project(conn, "P")
            copy = self.tmp / "move_me.drp"
            copy.write_bytes(b"DRPFAKE")
            hub.register_version(conn, p["id"], label="v1", author="a",
                                 source_path=str(copy))
            copy.unlink()
            versions = hub.list_versions(conn, p["id"])
            self.assertEqual(len(versions), 1)
            self.assertFalse(versions[0]["present_on_disk"])
        finally:
            conn.close()


class TestRecovery(HubFixture):
    def test_recover_returns_newest_and_says_it_cannot_restore(self):
        conn = self.conn()
        try:
            p = hub.create_project(conn, "P")
            hub.register_version(conn, p["id"], label="v1", author="a",
                                 source_path=str(self.project_file))
            time.sleep(0.01)
            hub.register_version(conn, p["id"], label="v2", author="b",
                                 source_path=str(self.project_file))
            res = hub.recover(conn, p["id"])
            self.assertEqual(res["newest"]["label"], "v2")
            self.assertFalse(res["byte_restore_available"])
            self.assertTrue(res["recoverable"])
            self.assertIn("Premiere", res["instructions"])
            self.assertEqual([c["label"] for c in res["version_chain"]], ["v1", "v2"])
        finally:
            conn.close()

    def test_recover_with_no_versions_refuses_to_pretend(self):
        conn = self.conn()
        try:
            p = hub.create_project(conn, "P")
            res = hub.recover(conn, p["id"])
            self.assertFalse(res["recoverable"])
            self.assertFalse(res["byte_restore_available"])
            self.assertIn("cannot reconstruct", res["instructions"])
        finally:
            conn.close()

    def test_recover_warns_when_the_drive_is_disconnected(self):
        conn = self.conn()
        try:
            p = hub.create_project(conn, "P")
            copy = self.tmp / "offline.prproj"
            copy.write_bytes(b"X")
            hub.register_version(conn, p["id"], label="v1", author="a",
                                 source_path=str(copy))
            copy.unlink()
            res = hub.recover(conn, p["id"])
            self.assertFalse(res["recoverable"])
            self.assertIn("NOT reachable", res["instructions"])
        finally:
            conn.close()

    def test_recover_reports_the_current_lock(self):
        conn = self.conn()
        try:
            p = hub.create_project(conn, "P")
            hub.register_version(conn, p["id"], label="v1", author="a",
                                 source_path=str(self.project_file))
            hub.acquire_lock(conn, p["id"], holder="abebe")
            res = hub.recover(conn, p["id"])
            self.assertEqual(res["lock"]["holder"], "abebe")
        finally:
            conn.close()


class TestNoProjectBytesAreRead(HubFixture):
    """The integrity rule, enforced by making reading impossible."""

    def test_every_hub_operation_survives_open_being_forbidden(self):
        real_open = builtins.open
        blocked = str(self.project_file)

        def guarded_open(file, *a, **kw):
            if str(file) == blocked:
                raise AssertionError(
                    "the project hub tried to open a project file; the blueprint "
                    "forbids reading or copying .prproj/.drp contents")
            return real_open(file, *a, **kw)

        conn = self.conn()
        try:
            with mock.patch("builtins.open", guarded_open):
                p = hub.create_project(conn, "P")
                hub.acquire_lock(conn, p["id"], holder="abebe")
                hub.register_version(conn, p["id"], label="v1", author="a",
                                     source_path=str(self.project_file))
                hub.list_versions(conn, p["id"])
                hub.recover(conn, p["id"])
                hub.release_lock(conn, p["id"], holder="abebe")
        finally:
            conn.close()

    def test_hub_module_source_has_no_file_reading_calls(self):
        """Static guard: strip strings and comments, then assert the *code* has
        no file-reading call at all. Catches a future edit that the runtime mock
        above might miss (a subprocess, a shutil.copy, a read_text)."""
        import inspect
        import io
        import tokenize

        src = inspect.getsource(hub)
        kept = []
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.STRING, tokenize.COMMENT):
                continue
            kept.append(tok.string)
        code = " ".join(kept)
        for forbidden in ("open", "read_text", "read_bytes", "shutil",
                          "subprocess", "copyfile", "urlopen"):
            self.assertNotIn(forbidden, code,
                             f"hub.py code contains {forbidden!r}; the project hub "
                             "must never read or copy project files")

    def test_http_layer_never_returns_file_contents(self):
        status, project = self.req("/api/projects", "POST", {"name": "P"})
        pid = project["id"]
        status, version = self.req(f"/api/projects/{pid}/versions", "POST",
                                   {"label": "v1", "author": "abebe",
                                    "source_path": str(self.project_file)})
        self.assertEqual(status, 201, version)
        blob = json.dumps(version)
        self.assertNotIn("PRPROJFAKE12", blob)
        status, rec = self.req(f"/api/projects/{pid}/recover", "POST", {})
        self.assertEqual(status, 200)
        self.assertNotIn("PRPROJFAKE12", json.dumps(rec))


if __name__ == "__main__":
    unittest.main()
