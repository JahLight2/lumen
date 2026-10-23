"""End-to-end HTTP tests for the read-only media server.

These run a real threaded HTTP server on an ephemeral port and make real
requests. They do NOT prove anything about FUSE/WinFsp mounting or NLE
behaviour -- only that our HTTP contract, auth, containment and byte
accounting are correct.
"""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from lfmc.core import catalogue, db, scanner
from lfmc.core.db import now_iso
from lfmc.net.server import Config, make_server
from lfmc.spikes.mp4_remote_open import write_synthetic_mp4

TOKEN = "test-token-0123456789"


class ServerFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)
        cls.drive = cls.tmp / "drive"
        cls.drive.mkdir()
        write_synthetic_mp4(str(cls.drive / "big_clip.mp4"), media_bytes=10 << 20)
        (cls.drive / "notes.txt").write_text("small sidecar file")
        cls.db_path = cls.tmp / "catalogue.db"
        conn = db.connect(cls.db_path)
        cls.root_id = scanner.register_root(conn, str(cls.drive), "HTTP Test Drive")
        scanner.scan_root(conn, cls.root_id)
        cls.big = conn.execute("SELECT * FROM assets WHERE name='big_clip.mp4'").fetchone()
        cls.small = conn.execute("SELECT * FROM assets WHERE name='notes.txt'").fetchone()
        conn.close()

        cls.config = Config(db_path=str(cls.db_path), token=TOKEN, host="127.0.0.1", port=0)
        cls.httpd = make_server(cls.config)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls._tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def request(self, path, headers=None, method="GET"):
        hdrs = {"Authorization": f"Bearer {TOKEN}"}
        hdrs.update(headers or {})
        req = urllib.request.Request(self.url(path), headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def conn(self):
        return db.connect(self.db_path)

    def wait_for_transfer(self, asset_id, timeout=3.0):
        """Transfer rows are written by the server thread *after* the body is
        streamed, so accounting is eventually consistent (documented in
        README). Poll briefly instead of racing the handler."""
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            conn = self.conn()
            row = conn.execute(
                "SELECT * FROM transfer_log WHERE asset_id=? ORDER BY id DESC LIMIT 1",
                (asset_id,)).fetchone()
            conn.close()
            if row is not None:
                return row
            time.sleep(0.02)
        self.fail(f"no transfer_log row for {asset_id} within {timeout}s")

    def ensure_online(self):
        conn = self.conn()
        scanner.resume_sharing(conn, self.root_id)
        conn.close()


class MediaEndpointTests(ServerFixture):
    def test_full_head_reports_accept_ranges(self):
        code, headers, _ = self.request(f"/media/{self.big['id']}", method="HEAD")
        self.assertEqual(code, 200)
        self.assertEqual(headers.get("Accept-Ranges"), "bytes")
        self.assertEqual(int(headers["Content-Length"]), self.big["size_bytes"])

    def test_partial_content_exact_bytes(self):
        code, headers, body = self.request(f"/media/{self.big['id']}",
                                           {"Range": "bytes=0-15"})
        self.assertEqual(code, 206)
        self.assertEqual(len(body), 16)
        self.assertEqual(headers["Content-Range"],
                         f"bytes 0-15/{self.big['size_bytes']}")

    def test_suffix_range_reads_tail(self):
        size = self.big["size_bytes"]
        code, headers, body = self.request(f"/media/{self.big['id']}", {"Range": "bytes=-64"})
        self.assertEqual(code, 206)
        self.assertEqual(len(body), 64)
        self.assertEqual(headers["Content-Range"], f"bytes {size-64}-{size-1}/{size}")

    def test_small_file_without_range_is_served_whole(self):
        code, headers, body = self.request(f"/media/{self.small['id']}")
        self.assertEqual(code, 200)
        self.assertEqual(len(body), self.small["size_bytes"])

    def test_large_original_without_range_is_refused(self):
        code, _, body = self.request(f"/media/{self.big['id']}")
        self.assertEqual(code, 400, "must not implicitly stream a whole original")
        self.assertEqual(json.loads(body)["error"], "range_required")

    def test_unsatisfiable_range(self):
        size = self.big["size_bytes"]
        code, headers, body = self.request(f"/media/{self.big['id']}",
                                           {"Range": f"bytes={size+10}-"})
        self.assertEqual(code, 416)
        self.assertEqual(headers["Content-Range"], f"bytes */{size}")

    def test_multi_range_is_rejected_not_silently_fulfilled(self):
        code, _, body = self.request(f"/media/{self.big['id']}",
                                     {"Range": "bytes=0-9,20-29"})
        self.assertEqual(code, 416)

    def test_bytes_are_logged_accurately(self):
        conn = self.conn()
        conn.execute("DELETE FROM transfer_log")
        conn.commit()
        conn.close()
        code, _, body = self.request(f"/media/{self.big['id']}", {"Range": "bytes=1000-1999"})
        self.assertEqual(code, 206)
        row = self.wait_for_transfer(self.big["id"])
        self.assertEqual(row["outcome"], "ok")
        self.assertEqual(row["bytes"], 1000)
        self.assertEqual(row["bytes"], len(body))
        self.assertEqual(row["start_byte"], 1000)
        self.assertEqual(row["end_byte"], 1999)


class AuthTests(ServerFixture):
    def test_missing_token_is_401(self):
        req = urllib.request.Request(self.url(f"/media/{self.big['id']}"),
                                     headers={"Range": "bytes=0-9"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 401)

    def test_wrong_token_is_401(self):
        req = urllib.request.Request(self.url(f"/media/{self.big['id']}"),
                                     headers={"Authorization": "Bearer nope",
                                              "Range": "bytes=0-9"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(ctx.exception.code, 401)

    def test_query_token_accepted(self):
        req = urllib.request.Request(
            self.url(f"/media/{self.big['id']}?token={TOKEN}"), headers={"Range": "bytes=0-9"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            self.assertEqual(resp.status, 206)


class AvailabilityTests(ServerFixture):
    def test_unknown_asset_404(self):
        code, _, body = self.request("/media/does-not-exist", {"Range": "bytes=0-9"})
        self.assertEqual(code, 404)

    def test_offline_drive_returns_explanatory_409(self):
        conn = self.conn()
        scanner.mark_root_offline(conn, self.root_id)
        conn.close()
        try:
            code, _, body = self.request(f"/media/{self.big['id']}", {"Range": "bytes=0-9"})
            self.assertEqual(code, 409)
            payload = json.loads(body)
            self.assertEqual(payload["error"], "original_unavailable")
            self.assertIn("host", payload["message"].lower())
            self.assertIn("catalogue", payload["hint"].lower())
        finally:
            self.ensure_online()

    def test_preview_served_while_original_offline(self):
        svg = self.drive / "thumb.svg"
        svg.write_text("<svg xmlns='http://www.w3.org/2000/svg' width='8' height='8'/>")
        conn = self.conn()
        conn.execute(
            """INSERT INTO cached_previews(asset_id, kind, location, path, bytes, created_at, source)
               VALUES(?,?,?,?,?,?,?)""",
            (self.big["id"], "thumbnail", "host", str(svg), svg.stat().st_size, now_iso(), "test"))
        conn.commit()
        scanner.mark_root_offline(conn, self.root_id)
        conn.close()
        try:
            code, headers, body = self.request(f"/preview/{self.big['id']}")
            self.assertEqual(code, 200)
            self.assertTrue(body.startswith(b"<svg"))
            # and the catalogue still reports the mixed state
            conn = self.conn()
            state = catalogue.asset_state(conn, catalogue.get_asset(conn, self.big["id"]))
            conn.close()
            self.assertEqual(state["state"], "preview_only")
        finally:
            self.ensure_online()

    def test_path_traversal_outside_root_is_forbidden(self):
        outside = self.tmp / "outside_secret.txt"
        outside.write_text("do not serve me")
        conn = self.conn()
        conn.execute(
            """INSERT INTO assets(id, root_id, rel_path, name, ext, size_bytes, mtime,
                                  fingerprint, fingerprint_algo, media_kind, probe_state,
                                  first_seen_at, last_seen_at, updated_at)
               VALUES('traversal-asset',?, '../outside_secret.txt','outside_secret.txt','.txt',16,
                      '0','deadbeef','test','other','unprobed',?,?,?)""",
            (self.root_id, now_iso(), now_iso(), now_iso()))
        conn.commit()
        conn.close()
        code, _, body = self.request("/media/traversal-asset", {"Range": "bytes=0-4"})
        self.assertEqual(code, 403)
        self.assertEqual(json.loads(body)["error"], "forbidden")


class ApiTests(ServerFixture):
    def test_health_and_capability_matrix(self):
        code, _, body = self.request("/api/health")
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["ok"])

        code, _, body = self.request("/api/nle/capabilities")
        payload = json.loads(body)
        self.assertEqual(code, 200)
        self.assertEqual(len(payload["adapters"]), 4)
        self.assertTrue(all(a["verification_status"].startswith("unverified")
                            for a in payload["adapters"]))
        self.assertTrue(payload["nle_access_is_user_side"])

    def test_search_endpoint_flags_offline_catalogue(self):
        code, _, body = self.request("/api/search?q=clip")
        payload = json.loads(body)
        self.assertEqual(code, 200)
        self.assertTrue(payload["searched_offline_catalogue"])
        self.assertEqual(payload["count"], 1)

    def test_spike_endpoint_measures_real_range_requests(self):
        code, _, body = self.request(f"/api/spike/open?asset_id={self.big['id']}&seeks=5")
        payload = json.loads(body)
        self.assertEqual(code, 200)
        self.assertGreater(payload["requests_for_open"], 0)
        self.assertLess(payload["bytes_fetched_for_open"], self.big["size_bytes"] / 4,
                        "structure read must not fetch a large share of the file")
        self.assertEqual(payload["transport"], "http-range (real requests, measured bytes)")


if __name__ == "__main__":
    unittest.main()
