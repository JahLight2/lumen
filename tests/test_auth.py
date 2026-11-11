"""Slice A: per-collaborator tokens, roles and scopes.

These run against the real HTTP server with a real catalogue, because the thing
being tested is the *authorisation decision*, not a Python function signature.

What is proven here:
  * a token is stored as a hash and never returned twice
  * revoked and expired tokens are rejected, and are indistinguishable from
    unknown tokens to the caller (all 401)
  * a role gates what the token may do (403, not 401)
  * a collaborator cannot post a comment under another person's name

What is NOT proven here: that this is a hardened auth system. There is no
password reset, no MFA, no rate limiting and no TLS. Tokens are bearer secrets
over cleartext HTTP on a LAN. See docs/WEBDAV_MOUNT.md.
"""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lfmc.core import auth, db, scanner
from lfmc.net.server import Config, make_server
from lfmc.spikes.mp4_remote_open import write_synthetic_mp4

HOST_TOKEN = "host-token-auth-test"


class AuthFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.drive = cls.tmp / "drive"
        (cls.drive / "01_DAY1").mkdir(parents=True)
        write_synthetic_mp4(str(cls.drive / "01_DAY1" / "interview.mp4"), media_bytes=1 << 20)
        cls.db_path = cls.tmp / "catalogue.db"
        conn = db.connect(cls.db_path)
        cls.root_id = scanner.register_root(conn, str(cls.drive), "Camera 01")
        scanner.scan_root(conn, cls.root_id)
        cls.asset_id = conn.execute(
            "SELECT id FROM assets WHERE name='interview.mp4'").fetchone()["id"]
        conn.close()

        cls.config = Config(db_path=str(cls.db_path), token=HOST_TOKEN,
                            host="127.0.0.1", port=0)
        cls.httpd = make_server(cls.config)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls._tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def conn(self):
        return db.connect(self.db_path)

    def issue(self, name, role, **kw):
        conn = self.conn()
        try:
            return auth.add_collaborator(conn, name=name, role=role, **kw)
        finally:
            conn.close()

    def req(self, path, method="GET", body=None, token=HOST_TOKEN):
        url = f"http://127.0.0.1:{self.port}{path}"
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        r = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                raw = resp.read()
                try:
                    return resp.status, json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    return resp.status, {"raw": raw.decode("utf-8", "replace")}
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return exc.code, {"raw": raw.decode("utf-8", "replace")}


class TestTokenStorage(AuthFixture):
    def test_raw_token_is_never_stored(self):
        c = self.issue("Abebe", "editor")
        raw = c["token"]
        self.assertGreaterEqual(len(raw), 24)
        conn = self.conn()
        try:
            row = conn.execute("SELECT * FROM collaborators WHERE id=?", (c["id"],)).fetchone()
            stored = row["token_hash"]
            self.assertNotEqual(stored, raw)
            self.assertEqual(stored, auth.hash_token(raw))
            # the raw token must appear nowhere in the row
            for key in row.keys():
                self.assertNotEqual(row[key], raw, f"raw token leaked into column {key}")
        finally:
            conn.close()

    def test_listing_never_contains_the_hash(self):
        self.issue("Tigist", "producer")
        conn = self.conn()
        try:
            for row in auth.list_collaborators(conn):
                self.assertNotIn("token_hash", row)
        finally:
            conn.close()

    def test_unknown_role_is_rejected(self):
        conn = self.conn()
        try:
            with self.assertRaises(auth.AuthError):
                auth.add_collaborator(conn, name="X", role="admin")
        finally:
            conn.close()

    def test_empty_name_is_rejected(self):
        conn = self.conn()
        try:
            with self.assertRaises(auth.AuthError):
                auth.add_collaborator(conn, name="   ", role="reviewer")
        finally:
            conn.close()


class TestTokenResolution(AuthFixture):
    def test_valid_token_resolves(self):
        c = self.issue("Marta", "reviewer")
        conn = self.conn()
        try:
            who = auth.resolve_token(conn, c["token"])
            self.assertIsNotNone(who)
            self.assertEqual(who["name"], "Marta")
            self.assertEqual(who["role"], "reviewer")
        finally:
            conn.close()

    def test_revoked_token_stops_working(self):
        c = self.issue("Yonas", "editor")
        conn = self.conn()
        try:
            self.assertIsNotNone(auth.resolve_token(conn, c["token"]))
            auth.revoke_collaborator(conn, c["id"])
            self.assertIsNone(auth.resolve_token(conn, c["token"]))
        finally:
            conn.close()

    def test_expired_token_stops_working(self):
        c = self.issue("Expired", "reviewer", expires_in_hours=-1)
        conn = self.conn()
        try:
            self.assertIsNone(auth.resolve_token(conn, c["token"]))
        finally:
            conn.close()

    def test_expiry_is_enforced_at_the_http_layer(self):
        c = self.issue("Short", "reviewer", expires_in_hours=-0.01)
        status, _ = self.req("/api/roots", token=c["token"])
        self.assertEqual(status, 401)

    def test_unparseable_expiry_fails_closed(self):
        c = self.issue("Bad", "reviewer")
        conn = self.conn()
        try:
            conn.execute("UPDATE collaborators SET expires_at='not-a-date' WHERE id=?", (c["id"],))
            conn.commit()
            self.assertIsNone(auth.resolve_token(conn, c["token"]))
        finally:
            conn.close()


class TestScopesOverHTTP(AuthFixture):
    def test_reviewer_can_read(self):
        c = self.issue("Reader", "reviewer")
        status, body = self.req("/api/roots", token=c["token"])
        self.assertEqual(status, 200, body)
        self.assertIn("roots", body)

    def test_reviewer_cannot_share(self):
        c = self.issue("Reader2", "reviewer")
        status, body = self.req("/api/review-links", method="POST",
                                body={"asset_id": self.asset_id}, token=c["token"])
        self.assertEqual(status, 403, body)
        self.assertEqual(body["error"], "insufficient_scope")
        self.assertIn("share", body["message"])

    def test_reviewer_cannot_export_markers(self):
        c = self.issue("Reader3", "reviewer")
        status, body = self.req(f"/api/assets/{self.asset_id}/markers.csv", token=c["token"])
        self.assertEqual(status, 403, body)

    def test_reviewer_cannot_administer_roots(self):
        c = self.issue("Reader4", "reviewer")
        status, body = self.req("/api/roots", method="POST",
                                body={"path": str(self.drive)}, token=c["token"])
        self.assertEqual(status, 403, body)

    def test_editor_can_comment_and_export_but_not_share(self):
        c = self.issue("Editor", "editor")
        status, body = self.req(f"/api/assets/{self.asset_id}/comments", method="POST",
                                body={"body": "check the focus here", "timecode_ms": 4000},
                                token=c["token"])
        self.assertEqual(status, 201, body)
        self.assertEqual(body["author"], "Editor")

        status, _ = self.req(f"/api/assets/{self.asset_id}/markers.csv", token=c["token"])
        self.assertEqual(status, 200)

        status, _ = self.req("/api/review-links", method="POST",
                             body={"asset_id": self.asset_id}, token=c["token"])
        self.assertEqual(status, 403)

    def test_producer_can_share(self):
        c = self.issue("Producer", "producer")
        status, body = self.req("/api/review-links", method="POST",
                                body={"asset_id": self.asset_id}, token=c["token"])
        self.assertEqual(status, 201, body)
        self.assertIn("url", body)

    def test_host_token_keeps_full_rights(self):
        status, _ = self.req("/api/review-links", method="POST", body={"asset_id": self.asset_id})
        self.assertEqual(status, 201)
        status, body = self.req("/api/whoami")
        self.assertEqual(status, 200)
        self.assertEqual(body["kind"], "host")
        self.assertIn("admin", body["scopes"])

    def test_no_token_is_401(self):
        status, body = self.req("/api/roots", token=None)
        self.assertEqual(status, 401)

    def test_unknown_token_is_401(self):
        status, _ = self.req("/api/roots", token="not-a-real-token")
        self.assertEqual(status, 401)

    def test_collaborator_cannot_impersonate_in_a_comment(self):
        c = self.issue("Real Name", "editor")
        status, body = self.req(f"/api/assets/{self.asset_id}/comments", method="POST",
                                body={"body": "note", "author": "Someone Else"},
                                token=c["token"])
        self.assertEqual(status, 201, body)
        self.assertEqual(body["author"], "Real Name")

    def test_collaborator_can_be_listed_by_admin_only(self):
        c = self.issue("Temp", "reviewer")
        status, _ = self.req("/api/collaborators", token=c["token"])
        self.assertEqual(status, 403)
        status, body = self.req("/api/collaborators", token=HOST_TOKEN)
        self.assertEqual(status, 200)
        self.assertTrue(any(x["name"] == "Temp" for x in body["collaborators"]))

    def test_new_token_appears_once_in_the_create_response(self):
        status, body = self.req("/api/collaborators", method="POST",
                                body={"name": "Oneshot", "role": "reviewer"})
        self.assertEqual(status, 201, body)
        self.assertIn("token", body)
        self.assertIn("token_warning", body)
        raw = body["token"]
        # the same collaborator fetched again must not carry the token
        status, again = self.req(f"/api/collaborators/{body['id']}")
        self.assertEqual(status, 200)
        self.assertNotIn("token", again)
        self.assertNotEqual(raw, "")


if __name__ == "__main__":
    unittest.main()
